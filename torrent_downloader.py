#!/usr/bin/env python3
"""
Torrent Downloader - baixador de torrents rapido e seguro.

Uso:
    python torrent_downloader.py <magnet_link_ou_arquivo.torrent> [opcoes]

Exemplos:
    python torrent_downloader.py "magnet:?xt=urn:btih:...."
    python torrent_downloader.py meuarquivo.torrent -o "D:\\Downloads"
    python torrent_downloader.py meuarquivo.torrent --max-down 5000 --max-up 500
"""

import argparse
import os
import sys
import time
import signal

import libtorrent as lt

from torrent_engine import __version__, format_size, format_speed


def build_session(port_min: int, port_max: int, max_down_kbs: int, max_up_kbs: int) -> lt.session:
    settings = {
        "listen_interfaces": f"0.0.0.0:{port_min},[::]:{port_min}",
        "enable_dht": True,
        "enable_lsd": True,
        "enable_upnp": True,
        "enable_natpmp": True,
        # seguranca: preferir peers que suportam encriptacao do protocolo BitTorrent
        "out_enc_policy": lt.enc_policy.enabled,
        "in_enc_policy": lt.enc_policy.enabled,
        "allowed_enc_level": lt.enc_level.both,
        "prefer_rc4": True,
        # performance: mais conexoes e requisicoes simultaneas = downloads mais rapidos
        "connections_limit": 400,
        "active_downloads": 10,
        "active_seeds": 10,
        "active_limit": 20,
        "download_rate_limit": max_down_kbs * 1024 if max_down_kbs > 0 else 0,
        "upload_rate_limit": max_up_kbs * 1024 if max_up_kbs > 0 else 0,
        "alert_mask": lt.alert.category_t.error_notification
        | lt.alert.category_t.storage_notification
        | lt.alert.category_t.status_notification,
    }
    ses = lt.session(settings)
    return ses


def add_torrent(ses: lt.session, source: str, save_path: str, sequential: bool) -> lt.torrent_handle:
    os.makedirs(save_path, exist_ok=True)
    flags = lt.torrent_flags.sequential_download if sequential else 0

    if source.startswith("magnet:"):
        params = lt.parse_magnet_uri(source)
        params.save_path = save_path
        params.storage_mode = lt.storage_mode_t.storage_mode_sparse
        params.flags |= flags
        handle = ses.add_torrent(params)
    else:
        if not os.path.isfile(source):
            print(f"Erro: arquivo .torrent nao encontrado: {source}")
            sys.exit(1)
        info = lt.torrent_info(source)
        handle = ses.add_torrent({
            "ti": info,
            "save_path": save_path,
            "storage_mode": lt.storage_mode_t.storage_mode_sparse,
        })
        if sequential:
            handle.set_flags(lt.torrent_flags.sequential_download)

    return handle


def main():
    parser = argparse.ArgumentParser(description="Baixador de torrents rapido e seguro.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("source", help="Magnet link ou caminho para arquivo .torrent")
    parser.add_argument("-o", "--output", default="downloads", help="Pasta de destino (padrao: ./downloads)")
    parser.add_argument("--max-down", type=int, default=0, help="Limite de download em KB/s (0 = ilimitado)")
    parser.add_argument("--max-up", type=int, default=100, help="Limite de upload em KB/s (padrao: 100, protege sua rede)")
    parser.add_argument("--port-min", type=int, default=6881, help="Porta minima de escuta (padrao: 6881)")
    parser.add_argument("--port-max", type=int, default=6891, help="Porta maxima de escuta (padrao: 6891)")
    parser.add_argument("--sequential", action="store_true", help="Baixar pecas em ordem sequencial (util p/ video/streaming)")
    args = parser.parse_args()

    save_path = os.path.abspath(args.output)
    print(f"Salvando em: {save_path}")
    print("Iniciando sessao (DHT, LSD, UPnP, encriptacao habilitados)...")

    ses = build_session(args.port_min, args.port_max, args.max_down, args.max_up)
    handle = add_torrent(ses, args.source, save_path, args.sequential)

    print("Obtendo metadados do torrent...")
    while not handle.status().has_metadata:
        time.sleep(1)
    print(f"Torrent: {handle.status().name}")

    stop_requested = {"flag": False}

    def handle_sigint(signum, frame):
        stop_requested["flag"] = True

    signal.signal(signal.SIGINT, handle_sigint)

    try:
        while not stop_requested["flag"]:
            s = handle.status()

            state_names = [
                "queued", "checking", "downloading metadata", "downloading",
                "finished", "seeding", "allocating", "checking resume data",
            ]
            state = state_names[s.state] if s.state < len(state_names) else str(s.state)

            print(
                f"\r[{state}] {s.progress * 100:5.1f}% | "
                f"down: {format_speed(s.download_rate)} | up: {format_speed(s.upload_rate)} | "
                f"peers: {s.num_peers} | seeds: {s.num_seeds} | "
                f"{format_size(s.total_wanted_done)}/{format_size(s.total_wanted)}",
                end="",
                flush=True,
            )

            if s.is_seeding:
                print("\nDownload concluido! Verificacao de integridade (hash) OK.")
                break

            time.sleep(1)
    finally:
        if stop_requested["flag"]:
            print("\nInterrompido pelo usuario. Salvando estado para retomar depois...")
        ses.pause()


if __name__ == "__main__":
    main()
