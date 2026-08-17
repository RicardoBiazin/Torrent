"""
Lookup de pais por IP (para mostrar bandeira/pais dos peers), com cache em
disco e requisicoes em background para nao travar a interface.

Usa a API publica e gratuita ip-api.com (sem chave, limite ~45 req/min,
ate 100 IPs por lote). Falhas de rede sao silenciosas: o pais simplesmente
fica desconhecido ("?").
"""

import ipaddress
import json
import os
import threading
import urllib.request

from torrent_engine import STATE_DIR

CACHE_FILE = os.path.join(STATE_DIR, "geoip_cache.json")

_cache: dict[str, dict] = {}
_loaded = False
_cache_lock = threading.Lock()

_pending: set[str] = set()
_pending_lock = threading.Lock()


def _load_cache() -> None:
    global _loaded
    if _loaded:
        return
    os.makedirs(STATE_DIR, exist_ok=True)
    if os.path.isfile(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                _cache.update(json.load(f))
        except (json.JSONDecodeError, OSError):
            pass
    _loaded = True


def _save_cache() -> None:
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_cache, f)
    except OSError:
        pass


def _is_public(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
        return not (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved)
    except ValueError:
        return False


def country_flag(code: str) -> str:
    """Converte codigo de pais (ex: 'BR') em emoji de bandeira."""
    if not code or len(code) != 2 or not code.isalpha():
        return "🏳"
    return "".join(chr(0x1F1E6 + ord(c.upper()) - ord("A")) for c in code)


def get_country(ip: str) -> dict:
    """Retorna {'code': 'BR', 'name': 'Brazil'} ou {} se ainda desconhecido."""
    _load_cache()
    return _cache.get(ip, {})


def request_lookup(ips: list[str]) -> None:
    """Dispara lookup em background para IPs publicos ainda nao cacheados."""
    _load_cache()
    with _pending_lock:
        todo = [ip for ip in set(ips) if ip not in _cache and ip not in _pending and _is_public(ip)]
        for ip in todo:
            _pending.add(ip)
    if not todo:
        return
    threading.Thread(target=_lookup_worker, args=(todo,), daemon=True).start()


def _lookup_worker(ips: list[str]) -> None:
    try:
        for i in range(0, len(ips), 100):
            batch = ips[i:i + 100]
            payload = [{"query": ip, "fields": "query,countryCode,country"} for ip in batch]
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                "http://ip-api.com/batch", data=data, headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                results = json.loads(resp.read().decode("utf-8"))
            with _cache_lock:
                for r in results:
                    ip = r.get("query")
                    if ip:
                        _cache[ip] = {"code": r.get("countryCode", ""), "name": r.get("country", "")}
                _save_cache()
    except Exception:
        pass
    finally:
        with _pending_lock:
            for ip in ips:
                _pending.discard(ip)
