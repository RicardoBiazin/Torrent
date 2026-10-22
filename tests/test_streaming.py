"""
Testes do streaming: um seed local limitado em velocidade e a engine baixando
dele, enquanto um cliente HTTP pede trechos do arquivo como um player faria.
"""

import os
import time
import urllib.error
import urllib.request

import libtorrent as lt
import pytest

import streaming
from test_engine import OfflineEngine, _espera, ambiente  # noqa: F401 - fixture


@pytest.fixture
def seed_e_leecher(ambiente, tmp_path):  # noqa: F811
    # seed separado, a ~2 MB/s, para o streaming ter que esperar pecas
    seed = lt.session({
        "listen_interfaces": "127.0.0.1:0",
        "enable_dht": False, "enable_lsd": False, "enable_upnp": False, "enable_natpmp": False,
        "ignore_limits_on_local_network": False,
        "upload_rate_limit": 2 * 1024 * 1024,
    })
    seed.add_torrent({"ti": lt.torrent_info(ambiente["torrent"]),
                      "save_path": ambiente["settings"]["save_path"]})
    time.sleep(0.5)

    vazio = tmp_path / "baixando"
    vazio.mkdir()
    eng = OfflineEngine(dict(ambiente["settings"], save_path=str(vazio)))
    h = eng.add_torrent(ambiente["torrent"])
    eng.handles[h].connect_peer(("127.0.0.1", seed.listen_port()))

    server = streaming.StreamServer(eng)
    original = open(os.path.join(ambiente["settings"]["save_path"], "conteudo", "a.bin"), "rb").read()
    yield eng, h, server, original
    server.stop()
    del seed


def _indice(eng, h, nome):
    return next(f["index"] for f in eng.get_files(h) if f["name"] == nome)


def test_get_files(seed_e_leecher):
    eng, h, _server, original = seed_e_leecher
    files = {f["name"]: f for f in eng.get_files(h)}
    assert set(files) == {"a.bin", "b.txt"}
    assert files["a.bin"]["size"] == len(original)
    assert not files["a.bin"]["is_media"]


def test_range_no_meio_enquanto_baixa(seed_e_leecher):
    eng, h, server, original = seed_e_leecher
    idx = _indice(eng, h, "a.bin")
    url = server.prepare(h, idx)
    assert url.startswith("http://127.0.0.1:") and url.endswith("/a.bin")
    assert eng.jobs[h]["sequential"] is True

    # o trecho do meio ainda nao existe no disco: o servidor tem que esperar
    assert eng.list_statuses()[0]["progress"] < 1.0
    req = urllib.request.Request(url, headers={"Range": "bytes=1000000-1099999"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        assert resp.status == 206
        assert resp.headers["Content-Range"] == f"bytes 1000000-1099999/{len(original)}"
        assert resp.read() == original[1000000:1100000]


def test_arquivo_inteiro_e_sufixo(seed_e_leecher):
    eng, h, server, original = seed_e_leecher
    url = server.prepare(h, _indice(eng, h, "a.bin"))
    with urllib.request.urlopen(url, timeout=60) as resp:
        assert resp.status == 200
        assert resp.headers["Accept-Ranges"] == "bytes"
        assert resp.read() == original

    req = urllib.request.Request(url, headers={"Range": "bytes=-500"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        assert resp.read() == original[-500:]

    req = urllib.request.Request(url, method="HEAD")
    with urllib.request.urlopen(req, timeout=30) as resp:
        assert int(resp.headers["Content-Length"]) == len(original)


def test_range_invalido_e_caminho_errado(seed_e_leecher):
    eng, h, server, original = seed_e_leecher
    url = server.prepare(h, _indice(eng, h, "a.bin"))
    req = urllib.request.Request(url, headers={"Range": f"bytes={len(original)}-"})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=10)
    assert e.value.code == 416

    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(f"http://127.0.0.1:{server.port}/naoexiste/0/x", timeout=10)
    assert e.value.code == 404


def test_parse_range():
    assert streaming._parse_range(None, 100) is None
    assert streaming._parse_range("bytes=10-19", 100) == (10, 19)
    assert streaming._parse_range("bytes=90-", 100) == (90, 99)
    assert streaming._parse_range("bytes=50-500", 100) == (50, 99)
    assert streaming._parse_range("bytes=-10", 100) == (90, 99)
    assert streaming._parse_range("bytes=100-", 100) is False
    assert streaming._parse_range("bytes=abc", 100) is False


def test_find_player_usa_o_configurado(tmp_path):
    falso = tmp_path / "player.exe"
    falso.write_bytes(b"")
    assert streaming.find_player(str(falso)) == str(falso)
