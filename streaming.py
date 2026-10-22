"""
Streaming: reproduz um arquivo de video/audio enquanto ele ainda esta baixando.

Sobe um servidor HTTP local (so' 127.0.0.1, porta aleatoria) que entrega o
arquivo com suporte a Range. O player (VLC, mpv ou o navegador) pede um trecho;
o servidor descobre quais pecas cobrem esse trecho, pede ao libtorrent que as
baixe primeiro (set_piece_deadline) e espera cada uma chegar antes de ler do
disco. Quando o player pula para outro ponto do video, o pedido seguinte ja
prioriza as pecas do ponto novo.

Ao abrir um arquivo o torrent passa a baixar em ordem (sequencial) e o inicio e
o fim do arquivo ganham prioridade: varios formatos guardam o indice no final
(MP4 sem "faststart", por exemplo) e o player le essa parte antes de tocar.
"""

import mimetypes
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote, unquote


# o mimetypes do Windows nao conhece alguns formatos comuns em torrent
mimetypes.add_type("video/x-matroska", ".mkv")
mimetypes.add_type("audio/x-matroska", ".mka")
mimetypes.add_type("video/mp2t", ".ts")
mimetypes.add_type("audio/flac", ".flac")
mimetypes.add_type("audio/ogg", ".opus")

READAHEAD_BYTES = 8 * 1024 * 1024   # quanto baixar a frente da posicao do player
PIECE_TIMEOUT = 120.0               # segundos esperando uma peca antes de desistir
CHUNK = 256 * 1024

# formatos que o navegador costuma tocar sozinho; o resto precisa de VLC/mpv
BROWSER_FRIENDLY = {".mp4", ".m4v", ".webm", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".flac"}


def find_player(configured: str = "") -> str | None:
    """Caminho do player: o configurado, senao VLC ou mpv instalados."""
    if configured and os.path.isfile(configured):
        return configured
    candidates = [shutil.which("vlc"), shutil.which("mpv")]
    if sys.platform == "win32":
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
            candidates.append(os.path.join(base, "VideoLAN", "VLC", "vlc.exe"))
            candidates.append(os.path.join(base, "mpv", "mpv.exe"))
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


class StreamServer:
    """Servidor HTTP local que entrega arquivos de torrents em andamento."""

    def __init__(self, engine):
        self.engine = engine
        self._stop = threading.Event()
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ #

    @property
    def port(self) -> int:
        self.start()
        return self._httpd.server_address[1]

    def start(self) -> None:
        if self._httpd:
            return
        server = self

        class Handler(_StreamHandler):
            stream = server

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    # ------------------------------------------------------------------ #

    def prepare(self, info_hash: str, file_index: int) -> str:
        """Prioriza o arquivo para streaming e devolve a URL local dele."""
        handle = self.engine.handles.get(info_hash)
        if not handle or not handle.is_valid() or not handle.status().has_metadata:
            raise RuntimeError("Torrent ainda sem metadados: aguarde aparecer o tamanho.")
        ti = handle.torrent_file()
        fs = ti.files()
        if not 0 <= file_index < fs.num_files():
            raise ValueError("Arquivo invalido.")

        self.engine.set_sequential(info_hash, True)
        if handle.file_priority(file_index) == 0:
            handle.file_priority(file_index, 4)
        handle.clear_piece_deadlines()
        size = fs.file_size(file_index)
        if size > 0:
            first = ti.map_file(file_index, 0, 1).piece
            last = ti.map_file(file_index, size - 1, 1).piece
            for i, p in enumerate(range(first, min(first + 4, last + 1))):
                handle.set_piece_deadline(p, i * 100)
            handle.set_piece_deadline(last, 100)

        self.start()
        name = os.path.basename(fs.file_path(file_index))
        return f"http://127.0.0.1:{self.port}/{info_hash}/{file_index}/{quote(name)}"

    def open_in_player(self, url: str, file_name: str, configured_player: str = "") -> str:
        """Abre a URL no player. Retorna uma frase dizendo onde abriu."""
        player = find_player(configured_player)
        if player:
            subprocess.Popen([player, url])
            return f"Abrindo em {os.path.basename(player)}"
        webbrowser.open(url)
        ext = os.path.splitext(file_name)[1].lower()
        if ext in BROWSER_FRIENDLY:
            return "Abrindo no navegador (instale o VLC para mais formatos)"
        return f"Abrindo no navegador, que talvez nao toque {ext}: instale o VLC"


class _StreamHandler(BaseHTTPRequestHandler):
    stream: StreamServer  # preenchido pela subclasse criada em StreamServer.start
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):  # noqa: A002 - silencioso (GUI sem console)
        pass

    def do_HEAD(self):
        self._serve(send_body=False)

    def do_GET(self):
        self._serve(send_body=True)

    # ------------------------------------------------------------------ #

    def _error(self, code: int, text: str) -> None:
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve(self, send_body: bool) -> None:
        parts = unquote(self.path.split("?", 1)[0]).strip("/").split("/")
        if len(parts) < 2 or not parts[1].isdigit():
            return self._error(404, "Caminho invalido.")
        info_hash, file_index = parts[0], int(parts[1])

        handle = self.stream.engine.handles.get(info_hash)
        if not handle or not handle.is_valid() or not handle.status().has_metadata:
            return self._error(404, "Torrent nao encontrado ou sem metadados.")
        ti = handle.torrent_file()
        fs = ti.files()
        if not 0 <= file_index < fs.num_files():
            return self._error(404, "Arquivo nao encontrado.")

        size = fs.file_size(file_index)
        rng = _parse_range(self.headers.get("Range"), size)
        if rng is False:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        start, end = rng if rng else (0, size - 1)
        length = end - start + 1 if size else 0

        ctype = mimetypes.guess_type(fs.file_path(file_index))[0] or "application/octet-stream"
        self.send_response(206 if rng else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if rng:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if not send_body or length == 0:
            return

        disk_path = os.path.join(handle.status().save_path, fs.file_path(file_index))
        try:
            self._send_range(handle, ti, file_index, disk_path, start, end)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass  # player fechou ou pulou para outro ponto: normal
        except TimeoutError:
            pass  # peca nao chegou (sem peers): o player mostra erro/espera

    def _send_range(self, handle, ti, file_index, disk_path, start, end) -> None:
        piece_len = ti.piece_length()
        ahead = max(4, READAHEAD_BYTES // piece_len)
        f = None
        try:
            pos = start
            while pos <= end:
                req = ti.map_file(file_index, pos, 1)
                piece = req.piece
                self._prioritize(handle, ti, piece, ahead)
                self._wait_piece(handle, piece)

                # le ate o fim desta peca (ou do trecho pedido)
                n = min(ti.piece_size(piece) - req.start, end - pos + 1)
                if f is None:
                    f = open(disk_path, "rb")
                f.seek(pos)
                left = n
                while left > 0:
                    data = f.read(min(CHUNK, left))
                    if not data:
                        raise ConnectionAbortedError("arquivo menor que o esperado")
                    self.wfile.write(data)
                    left -= len(data)
                pos += n
        finally:
            if f:
                f.close()

    @staticmethod
    def _prioritize(handle, ti, piece: int, ahead: int) -> None:
        last = min(piece + ahead, ti.num_pieces() - 1)
        for i, p in enumerate(range(piece, last + 1)):
            if not handle.have_piece(p):
                handle.set_piece_deadline(p, i * 200)

    def _wait_piece(self, handle, piece: int) -> None:
        deadline = time.time() + PIECE_TIMEOUT
        while not handle.have_piece(piece):
            if self.stream.stopping or not handle.is_valid():
                raise ConnectionAbortedError("servidor encerrando")
            if time.time() > deadline:
                raise TimeoutError(f"peca {piece} nao chegou")
            time.sleep(0.05)


def _parse_range(header: str | None, size: int):
    """None = sem Range; False = Range invalido (416); (inicio, fim) inclusivo."""
    if not header or not header.startswith("bytes=") or size == 0:
        return None
    spec = header[6:].split(",")[0].strip()  # varios trechos: atende so' o primeiro
    try:
        a, b = spec.split("-", 1)
        if a == "":
            n = int(b)
            if n <= 0:
                return False
            return max(0, size - n), size - 1
        start = int(a)
        end = int(b) if b else size - 1
    except ValueError:
        return False
    if start >= size or end < start:
        return False
    return start, min(end, size - 1)
