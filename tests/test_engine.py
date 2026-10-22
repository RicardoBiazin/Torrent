"""
Testes do TorrentEngine sem rede externa: cria um torrent local de verdade,
semeia a partir de uma pasta temporaria e confere o ciclo completo
(adicionar, pausar, retomar, salvar dados de retomada, reabrir, remover).
"""

import os
import sys
import time

import libtorrent as lt
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import geoip  # noqa: E402
import torrent_engine  # noqa: E402
from torrent_engine import TorrentEngine, format_size, format_speed  # noqa: E402


class OfflineEngine(TorrentEngine):
    """Mesma engine, mas a sessao so escuta em 127.0.0.1 e nao usa DHT/LSD/UPnP."""

    def _build_session(self) -> lt.session:
        return lt.session({
            "listen_interfaces": "127.0.0.1:0",
            "enable_dht": False,
            "enable_lsd": False,
            "enable_upnp": False,
            "enable_natpmp": False,
            "alert_mask": lt.alert.category_t.error_notification
            | lt.alert.category_t.storage_notification
            | lt.alert.category_t.status_notification,
        })


@pytest.fixture
def ambiente(tmp_path, monkeypatch):
    state = tmp_path / ".state"
    monkeypatch.setattr(torrent_engine, "STATE_DIR", str(state))
    monkeypatch.setattr(torrent_engine, "JOBS_FILE", str(state / "jobs.json"))
    monkeypatch.setattr(torrent_engine, "SETTINGS_FILE", str(state / "settings.json"))

    dados = tmp_path / "dados"
    (dados / "conteudo").mkdir(parents=True)
    (dados / "conteudo" / "a.bin").write_bytes(os.urandom(3_000_000))
    (dados / "conteudo" / "b.txt").write_text("ola torrent\n" * 100, encoding="utf-8")

    fs = lt.file_storage()
    lt.add_files(fs, str(dados / "conteudo"))
    ct = lt.create_torrent(fs, piece_size=16384)
    lt.set_piece_hashes(ct, str(dados))
    arquivo = tmp_path / "teste.torrent"
    arquivo.write_bytes(lt.bencode(ct.generate()))

    settings = dict(torrent_engine.DEFAULT_SETTINGS, save_path=str(dados))
    return {"settings": settings, "torrent": str(arquivo), "state": state}


def _espera(cond, timeout=10.0):
    fim = time.time() + timeout
    while time.time() < fim:
        if cond():
            return True
        time.sleep(0.1)
    return False


def test_formatadores():
    assert format_size(0) == "0.0 B"
    assert format_size(1536) == "1.5 KB"
    assert format_size(5 * 1024 ** 3) == "5.0 GB"
    assert format_speed(512) == "0.5 KB/s"
    assert format_speed(3 * 1024 * 1024) == "3.00 MB/s"


def test_ciclo_completo(ambiente):
    eng = OfflineEngine(dict(ambiente["settings"]))
    h = eng.add_torrent(ambiente["torrent"])
    assert h in eng.jobs and eng.jobs[h]["source_type"] == "file"

    assert _espera(lambda: eng.list_statuses()[0]["is_seeding"]), "dados locais deveriam semear"
    st = eng.list_statuses()[0]
    assert st["progress"] == 1.0 and st["name"] == "conteudo"
    assert set(eng.get_piece_map(h)) == {"have"}

    eng.pause_torrent(h)
    assert _espera(lambda: eng.list_statuses()[0]["paused"])
    assert eng.jobs[h]["paused"] is True
    eng.resume_torrent(h)
    assert _espera(lambda: not eng.list_statuses()[0]["paused"])

    eng.set_sequential(h, True)
    assert eng.jobs[h]["sequential"] is True

    eng.shutdown()
    resume = ambiente["state"] / f"{h}.fastresume"
    assert resume.is_file()
    # regressao: lt.bencode(add_torrent_params) gravava 2 bytes ("de")
    assert resume.stat().st_size > 100
    lt.read_resume_data(resume.read_bytes())

    # reabre: o job volta pela retomada, sem precisar reverificar
    eng2 = OfflineEngine(dict(ambiente["settings"]))
    assert h in eng2.handles
    assert _espera(lambda: eng2.list_statuses()[0]["is_seeding"])

    eng2.remove_torrent(h, delete_files=False)
    assert h not in eng2.jobs and not resume.exists()
    assert os.path.isfile(os.path.join(ambiente["settings"]["save_path"], "conteudo", "a.bin"))


def test_torrent_inexistente(ambiente):
    eng = OfflineEngine(dict(ambiente["settings"]))
    with pytest.raises(FileNotFoundError):
        eng.add_torrent(os.path.join(str(ambiente["state"]), "nao_existe.torrent"))


def test_settings_corrompido_usa_padrao(ambiente):
    ambiente["state"].mkdir(parents=True, exist_ok=True)
    (ambiente["state"] / "settings.json").write_text("{quebrado", encoding="utf-8")
    assert torrent_engine.load_settings()["port_min"] == 6881


def test_geoip_bandeira_e_ip_privado():
    assert geoip.country_flag("BR") == "\U0001F1E7\U0001F1F7"
    assert geoip.country_flag("") == "🏳"
    assert not geoip._is_public("192.168.0.10")
    assert not geoip._is_public("127.0.0.1")
    assert not geoip._is_public("lixo")
    assert geoip._is_public("8.8.8.8")


def test_remover_apagando_arquivos(ambiente):
    eng = OfflineEngine(dict(ambiente["settings"]))
    h = eng.add_torrent(ambiente["torrent"])
    assert _espera(lambda: eng.list_statuses()[0]["is_seeding"])
    alvo = os.path.join(ambiente["settings"]["save_path"], "conteudo", "a.bin")
    eng.remove_torrent(h, delete_files=True)
    assert h not in eng.handles and h not in eng.jobs
    assert _espera(lambda: not os.path.exists(alvo)), "delete_files=True deveria apagar os dados"
