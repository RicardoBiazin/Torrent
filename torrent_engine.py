"""
Engine de torrents: encapsula a sessao libtorrent e o gerenciamento de
multiplos downloads (adicionar, pausar, retomar, remover, persistir estado).

Usado tanto pela CLI (torrent_downloader.py) quanto pela GUI (torrent_gui.py).
O agendamento (scheduler.py) e o streaming (streaming.py) atuam por aqui.
"""

import json
import os
import sys
import threading
import time
from datetime import datetime

import libtorrent as lt

import scheduler

__version__ = "1.1.1"

# No .exe (PyInstaller --onefile) __file__ aponta para a pasta temporaria de
# extracao, apagada ao fechar: estado e downloads ficam ao lado do executavel.
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

STATE_DIR = os.path.join(BASE_DIR, ".state")
JOBS_FILE = os.path.join(STATE_DIR, "jobs.json")
SETTINGS_FILE = os.path.join(STATE_DIR, "settings.json")

DEFAULT_SETTINGS = {
    "save_path": os.path.join(BASE_DIR, "downloads"),
    "max_down_kbs": 0,      # 0 = ilimitado
    "max_up_kbs": 100,
    "port_min": 6881,
    "port_max": 6891,
    "max_active_downloads": 5,
    "sequential_default": False,
    "schedule_enabled": False,
    "schedule": [],         # regras de scheduler.py
    "player_path": "",      # vazio = procura VLC/mpv; sem player, abre no navegador
}

MEDIA_EXTENSIONS = {
    ".mp4", ".m4v", ".mkv", ".webm", ".avi", ".mov", ".wmv", ".mpg", ".mpeg", ".ts", ".flv",
    ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wav", ".wma",
}

STATE_NAMES = [
    "na fila", "verificando", "obtendo metadados", "baixando",
    "concluido", "compartilhando (seed)", "alocando espaco", "verificando dados salvos",
]


def format_speed(bytes_per_sec: float) -> str:
    kb = bytes_per_sec / 1024
    if kb < 1024:
        return f"{kb:.1f} KB/s"
    return f"{kb / 1024:.2f} MB/s"


def format_size(num_bytes: int) -> str:
    n = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def torrent_layout(ti):
    """Arquivos do torrent: layout() no libtorrent 2.1 (files() ficou obsoleto), files() no 2.0."""
    return ti.layout() if hasattr(ti, "layout") else ti.files()


def load_settings() -> dict:
    os.makedirs(STATE_DIR, exist_ok=True)
    settings = dict(DEFAULT_SETTINGS)
    if os.path.isfile(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                settings.update(json.load(f))
        except (json.JSONDecodeError, OSError):
            pass
    return settings


def save_settings(settings: dict) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)


class TorrentEngine:
    """Gerencia a sessao libtorrent e todos os downloads ativos."""

    def __init__(self, settings: dict | None = None):
        self.settings = settings or load_settings()
        os.makedirs(STATE_DIR, exist_ok=True)
        os.makedirs(self.settings["save_path"], exist_ok=True)
        self.session = self._build_session()
        self.handles: dict[str, lt.torrent_handle] = {}  # info_hash -> handle
        self.jobs: dict[str, dict] = self._load_jobs()
        # efeito atual do agendamento e o que ele mudou, para desfazer so' isso
        self._sched_download = True
        self._sched_upload = True
        self._sched_session_paused = False
        self._sched_upload_mode: set[str] = set()
        # alertas: so' um thread por vez tira da fila e distribui
        self._alert_lock = threading.Lock()
        self._piece_cond = threading.Condition()
        self._pieces: dict[tuple[str, int], bytes | None] = {}
        self._resume_pending = 0

    # ------------------------------------------------------------------ #
    # Sessao
    # ------------------------------------------------------------------ #

    def _build_session(self) -> lt.session:
        s = self.settings
        alert_mask = (
            lt.alert.category_t.error_notification
            | lt.alert.category_t.storage_notification
            | lt.alert.category_t.status_notification
        )
        session_settings = {
            "listen_interfaces": f"0.0.0.0:{s['port_min']},[::]:{s['port_min']}",
            "enable_dht": True,
            "enable_lsd": True,
            "enable_upnp": True,
            "enable_natpmp": True,
            "out_enc_policy": lt.enc_policy.enabled,
            "in_enc_policy": lt.enc_policy.enabled,
            "allowed_enc_level": lt.enc_level.both,
            "prefer_rc4": True,
            "connections_limit": 400,
            "active_downloads": s["max_active_downloads"],
            "active_seeds": 10,
            "active_limit": s["max_active_downloads"] + 10,
            "alert_mask": alert_mask,
        }
        session_settings.update(self._rate_limits(upload_allowed=True))
        return lt.session(session_settings)

    def _rate_limits(self, upload_allowed: bool | None = None) -> dict:
        s = self.settings
        if upload_allowed is None:
            upload_allowed = getattr(self, "_sched_upload", True)
        up = s["max_up_kbs"] * 1024 if s["max_up_kbs"] > 0 else 0
        if not upload_allowed:
            # 0 no libtorrent quer dizer ILIMITADO; 1 byte/s e' o mais perto de parado
            up = 1
        return {
            "download_rate_limit": s["max_down_kbs"] * 1024 if s["max_down_kbs"] > 0 else 0,
            "upload_rate_limit": up,
        }

    def apply_settings(self, new_settings: dict) -> None:
        """Atualiza limites de velocidade e portas em tempo real."""
        self.settings.update(new_settings)
        save_settings(self.settings)
        s = self.settings
        limits = self._rate_limits()
        limits["active_downloads"] = s["max_active_downloads"]
        limits["active_limit"] = s["max_active_downloads"] + 10
        self.session.apply_settings(limits)
        self.apply_schedule()

    # ------------------------------------------------------------------ #
    # Agendamento
    # ------------------------------------------------------------------ #

    def apply_schedule(self, now: datetime | None = None) -> str | None:
        """
        Aplica as regras de horario. Chamar periodicamente (a GUI chama a cada
        segundo). Retorna a descricao do efeito ativo, ou None se nada parado.

        Nao mexe no "pausado" de cada torrent: o que o usuario pausou continua
        pausado quando a regra termina, e o que estava rodando volta a rodar.
        - download e upload parados: pausa a sessao inteira
        - so' download parado: upload_mode nos torrents (continuam semeando)
        - so' upload parado: limite de upload de 1 byte/s
        """
        s = self.settings
        if s.get("schedule_enabled") and s.get("schedule"):
            down, up, _active = scheduler.current_effect(s["schedule"], now or datetime.now())
        else:
            down, up = True, True

        both_off = not down and not up
        if both_off and not self._sched_session_paused:
            self.session.pause()
            self._sched_session_paused = True
        elif not both_off and self._sched_session_paused:
            self.session.resume()
            self._sched_session_paused = False

        upload_only = not down and up
        for info_hash, handle in list(self.handles.items()):
            if not handle.is_valid():
                continue
            if upload_only and info_hash not in self._sched_upload_mode:
                # torrent que ja estava em upload_mode (ex.: erro de disco) nao e' nosso
                if not handle.flags() & lt.torrent_flags.upload_mode:
                    handle.set_flags(lt.torrent_flags.upload_mode)
                    self._sched_upload_mode.add(info_hash)
            elif not upload_only and info_hash in self._sched_upload_mode:
                handle.unset_flags(lt.torrent_flags.upload_mode)
                self._sched_upload_mode.discard(info_hash)

        if up != self._sched_upload:
            self._sched_upload = up
            self.session.apply_settings(self._rate_limits())
        self._sched_download = down
        return scheduler.describe_effect(down, up)

    def _clear_schedule_effects(self) -> None:
        """Desfaz o agendamento antes de salvar a retomada (o upload_mode iria junto)."""
        for info_hash in list(self._sched_upload_mode):
            handle = self.handles.get(info_hash)
            if handle and handle.is_valid():
                handle.unset_flags(lt.torrent_flags.upload_mode)
        self._sched_upload_mode.clear()
        if self._sched_session_paused:
            self.session.resume()
            self._sched_session_paused = False

    # ------------------------------------------------------------------ #
    # Persistencia de jobs (para retomar downloads entre sessoes)
    # ------------------------------------------------------------------ #

    def _load_jobs(self) -> dict:
        if os.path.isfile(JOBS_FILE):
            try:
                with open(JOBS_FILE, "r", encoding="utf-8") as f:
                    jobs = json.load(f)
            except (json.JSONDecodeError, OSError):
                jobs = {}
        else:
            jobs = {}

        for info_hash, job in list(jobs.items()):
            try:
                self._add_from_job(info_hash, job)
            except Exception as exc:  # torrent corrompido/inacessivel: ignora
                print(f"Aviso: nao foi possivel retomar {job.get('name', info_hash)}: {exc}")

        return jobs

    def _save_jobs(self) -> None:
        with open(JOBS_FILE, "w", encoding="utf-8") as f:
            json.dump(self.jobs, f, indent=2, ensure_ascii=False)

    def _add_from_job(self, info_hash: str, job: dict) -> None:
        save_path = job["save_path"]
        os.makedirs(save_path, exist_ok=True)
        resume_file = os.path.join(STATE_DIR, f"{info_hash}.fastresume")

        params = None
        if os.path.isfile(resume_file):
            # dados de retomada ja contem save_path/ti/flags: usa-los diretamente
            try:
                with open(resume_file, "rb") as f:
                    params = lt.read_resume_data(f.read())
                params.save_path = save_path
            except RuntimeError:
                # arquivo de retomada corrompido/incompleto: descarta e usa a fonte original
                os.remove(resume_file)
                params = None

        if params is None and job["source_type"] == "magnet":
            params = lt.parse_magnet_uri(job["source"])
            params.save_path = save_path
            params.storage_mode = lt.storage_mode_t.storage_mode_sparse
        elif params is None:
            if not os.path.isfile(job["source"]):
                raise FileNotFoundError(f"Arquivo .torrent nao encontrado: {job['source']}")
            info = lt.torrent_info(job["source"])
            params = lt.add_torrent_params()
            params.ti = info
            params.save_path = save_path
            params.storage_mode = lt.storage_mode_t.storage_mode_sparse

        if job.get("sequential"):
            params.flags |= lt.torrent_flags.sequential_download
        if job.get("paused"):
            params.flags |= lt.torrent_flags.paused

        handle = self.session.add_torrent(params)
        self.handles[info_hash] = handle

    # ------------------------------------------------------------------ #
    # Operacoes publicas
    # ------------------------------------------------------------------ #

    def add_torrent(self, source: str, save_path: str | None = None, sequential: bool = False) -> str:
        """Adiciona um magnet link ou caminho de arquivo .torrent. Retorna o info_hash."""
        save_path = save_path or self.settings["save_path"]
        os.makedirs(save_path, exist_ok=True)
        is_magnet = source.startswith("magnet:")

        if is_magnet:
            params = lt.parse_magnet_uri(source)
        else:
            if not os.path.isfile(source):
                raise FileNotFoundError(f"Arquivo .torrent nao encontrado: {source}")
            info = lt.torrent_info(source)
            params = lt.add_torrent_params()
            params.ti = info

        params.save_path = save_path
        params.storage_mode = lt.storage_mode_t.storage_mode_sparse
        if sequential:
            params.flags |= lt.torrent_flags.sequential_download

        handle = self.session.add_torrent(params)
        info_hash = str(handle.info_hash())

        self.handles[info_hash] = handle
        self.jobs[info_hash] = {
            "source": source,
            "source_type": "magnet" if is_magnet else "file",
            "save_path": save_path,
            "sequential": sequential,
            "paused": False,
            "added_at": job_timestamp(),
            "name": handle.status().name or "(obtendo nome...)",
        }
        self._save_jobs()
        return info_hash

    def remove_torrent(self, info_hash: str, delete_files: bool = False) -> None:
        handle = self.handles.get(info_hash)
        if handle:
            # lt.options_t(0) nao e' aceito pelo binding (ArgumentError): sem
            # apagar arquivos, chama sem flag
            if delete_files:
                self.session.remove_torrent(handle, lt.options_t.delete_files)
            else:
                self.session.remove_torrent(handle)
            del self.handles[info_hash]
        self.jobs.pop(info_hash, None)
        self._sched_upload_mode.discard(info_hash)
        resume_file = os.path.join(STATE_DIR, f"{info_hash}.fastresume")
        if os.path.isfile(resume_file):
            os.remove(resume_file)
        self._save_jobs()

    def pause_torrent(self, info_hash: str) -> None:
        handle = self.handles.get(info_hash)
        if handle:
            handle.pause()
            if info_hash in self.jobs:
                self.jobs[info_hash]["paused"] = True
                self._save_jobs()

    def resume_torrent(self, info_hash: str) -> None:
        handle = self.handles.get(info_hash)
        if handle:
            handle.resume()
            if info_hash in self.jobs:
                self.jobs[info_hash]["paused"] = False
                self._save_jobs()

    def set_sequential(self, info_hash: str, sequential: bool) -> None:
        handle = self.handles.get(info_hash)
        if handle:
            if sequential:
                handle.set_flags(lt.torrent_flags.sequential_download)
            else:
                handle.unset_flags(lt.torrent_flags.sequential_download)
            if info_hash in self.jobs:
                self.jobs[info_hash]["sequential"] = sequential
                self._save_jobs()

    def list_statuses(self) -> list[dict]:
        """Retorna um snapshot do estado de todos os torrents."""
        results = []
        for info_hash, handle in list(self.handles.items()):
            if not handle.is_valid():
                continue
            s = handle.status()
            job = self.jobs.get(info_hash, {})
            name = s.name or job.get("name") or info_hash[:12]
            if s.name and job.get("name") != s.name:
                job["name"] = s.name

            state = STATE_NAMES[s.state] if s.state < len(STATE_NAMES) else str(s.state)
            if s.paused:
                state = "pausado"

            results.append({
                "info_hash": info_hash,
                "name": name,
                "progress": s.progress,
                "download_rate": s.download_rate,
                "upload_rate": s.upload_rate,
                "peers": s.num_peers,
                "seeds": s.num_seeds,
                "state": state,
                "total_wanted": s.total_wanted,
                "total_wanted_done": s.total_wanted_done,
                "save_path": job.get("save_path", self.settings["save_path"]),
                "is_seeding": s.is_seeding,
                "paused": s.paused,
            })
        return results

    def get_peers(self, info_hash: str) -> list[dict]:
        """Detalhes de cada peer/seed conectado: IP, cliente, velocidades, progresso e peca atual."""
        handle = self.handles.get(info_hash)
        if not handle or not handle.is_valid():
            return []

        peers = []
        for p in handle.get_peer_info():
            ip, port = p.ip
            client = p.client
            if isinstance(client, bytes):
                client = client.decode("utf-8", errors="replace")

            encrypted = bool(
                p.flags & lt.peer_info.rc4_encrypted or p.flags & lt.peer_info.plaintext_encrypted
            )
            is_seed = bool(p.flags & lt.peer_info.seed) or p.progress >= 1.0

            peers.append({
                "ip": ip,
                "port": port,
                "client": client or "?",
                "down_speed": p.down_speed,
                "up_speed": p.up_speed,
                "progress": p.progress,
                "piece": p.downloading_piece_index if p.downloading_piece_index >= 0 else None,
                "encrypted": encrypted,
                "is_seed": is_seed,
            })
        return peers

    def get_piece_map(self, info_hash: str, max_blocks: int = 300) -> list[str]:
        """
        Estado de cada peca do arquivo: 'have' (baixada), 'downloading' (em andamento)
        ou 'missing' (faltando). Se houver mais pecas que max_blocks, agrega em buckets.
        """
        handle = self.handles.get(info_hash)
        if not handle or not handle.is_valid() or not handle.status().has_metadata:
            return []

        have = handle.status().pieces
        num_pieces = len(have)
        if num_pieces == 0:
            return []

        downloading = {q["piece_index"] for q in handle.get_download_queue()}

        states = ["have" if have[i] else ("downloading" if i in downloading else "missing")
                  for i in range(num_pieces)]

        if num_pieces <= max_blocks:
            return states

        bucket_size = num_pieces / max_blocks
        aggregated = []
        for b in range(max_blocks):
            start = int(b * bucket_size)
            end = max(int((b + 1) * bucket_size), start + 1)
            chunk = states[start:end]
            if "downloading" in chunk:
                aggregated.append("downloading")
            elif all(s == "have" for s in chunk):
                aggregated.append("have")
            elif "have" in chunk:
                aggregated.append("partial")
            else:
                aggregated.append("missing")
        return aggregated

    def save_all_resume_data(self, timeout: float = 5.0) -> None:
        """Salva dados de retomada de todos os torrents (chamar antes de fechar)."""
        pending = 0
        for handle in self.handles.values():
            if handle.is_valid() and handle.status().has_metadata:
                # save_info_dict: sem ele o .fastresume nao leva os metadados e o
                # torrent reabre em "obtendo metadados", mesmo vindo de .torrent
                handle.save_resume_data(lt.torrent_handle.save_info_dict)
                pending += 1
        self._resume_pending += pending

        deadline = time.time() + timeout
        while self._resume_pending > 0 and time.time() < deadline:
            self.process_alerts(wait_ms=100)
        self._resume_pending = 0

    # ------------------------------------------------------------------ #
    # Alertas e leitura de pecas
    # ------------------------------------------------------------------ #

    def process_alerts(self, wait_ms: int = 0) -> None:
        """Tira os alertas da fila e trata os que interessam. Seguro entre threads."""
        with self._alert_lock:
            if wait_ms:
                self.session.wait_for_alert(wait_ms)
            alerts = self.session.pop_alerts()
        for alert in alerts:
            if isinstance(alert, lt.read_piece_alert):
                key = (str(alert.handle.info_hash()), alert.piece)
                data = None if alert.error.value() else bytes(alert.buffer)
                with self._piece_cond:
                    self._pieces[key] = data
                    self._piece_cond.notify_all()
            elif isinstance(alert, lt.save_resume_data_alert):
                info_hash = str(alert.handle.info_hash())
                resume_file = os.path.join(STATE_DIR, f"{info_hash}.fastresume")
                # write_resume_data_buf, nao bencode: alert.params e' um
                # add_torrent_params, e lt.bencode() dele gera um dicionario
                # vazio -- o .fastresume saia com 2 bytes e era descartado
                # na abertura seguinte, forcando nova verificacao completa.
                with open(resume_file, "wb") as f:
                    f.write(lt.write_resume_data_buf(alert.params))
                self._resume_pending -= 1
            elif isinstance(alert, lt.save_resume_data_failed_alert):
                self._resume_pending -= 1

    def read_piece(self, info_hash: str, piece: int, timeout: float = 30.0) -> bytes:
        """
        Conteudo de uma peca ja baixada, lido pelo proprio libtorrent.

        Ler o arquivo direto do disco nao serve: no libtorrent 2.1 a peca fica
        "have" enquanto os dados ainda estao no cache de escrita, e o disco
        devolve zeros.
        """
        handle = self.handles.get(info_hash)
        if not handle or not handle.is_valid():
            raise ConnectionAbortedError("torrent removido")
        key = (info_hash, piece)
        with self._piece_cond:
            self._pieces.pop(key, None)
        handle.read_piece(piece)
        deadline = time.time() + timeout
        while True:
            with self._piece_cond:
                if key in self._pieces:
                    data = self._pieces.pop(key)
                    if data is None:
                        raise OSError(f"erro lendo a peca {piece}")
                    return data
            if time.time() > deadline:
                raise TimeoutError(f"leitura da peca {piece} nao terminou")
            self.process_alerts(wait_ms=50)

    def shutdown(self) -> None:
        self._clear_schedule_effects()
        self.save_all_resume_data()
        self._save_jobs()


    # ------------------------------------------------------------------ #
    # Arquivos (usado pelo streaming)
    # ------------------------------------------------------------------ #

    def get_files(self, info_hash: str) -> list[dict]:
        """Arquivos do torrent com tamanho e quanto ja foi baixado. Vazio sem metadados."""
        handle = self.handles.get(info_hash)
        if not handle or not handle.is_valid() or not handle.status().has_metadata:
            return []
        fs = torrent_layout(handle.torrent_file())
        progress = handle.file_progress()
        files = []
        for i in range(fs.num_files()):
            if fs.file_flags(i) & lt.file_storage.flag_pad_file:
                continue  # preenchimento de alinhamento (torrents v2/hibridos), nao e' arquivo real
            path = fs.file_path(i)
            files.append({
                "index": i,
                "path": path,
                "name": os.path.basename(path),
                "size": fs.file_size(i),
                "done": progress[i] if i < len(progress) else 0,
                "is_media": os.path.splitext(path)[1].lower() in MEDIA_EXTENSIONS,
            })
        return files


def job_timestamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
