"""
Interface grafica do baixador de torrents (Tkinter - sem dependencias extras).

Executar:
    python torrent_gui.py
"""

import os
import subprocess
import sys
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

from torrent_engine import TorrentEngine, __version__, format_size, format_speed
import geoip
import scheduler
from streaming import StreamServer

REFRESH_MS = 1000

PIECE_COLORS = {
    "have": "#4CAF50",
    "downloading": "#FF9800",
    "partial": "#FFEB3B",
    "missing": "#555555",
}


class TorrentGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"Torrent Downloader {__version__} - rapido e seguro")
        self.geometry("1040x780")
        self.minsize(820, 560)

        self.engine = TorrentEngine()
        self.streamer = StreamServer(self.engine)
        self._row_by_hash: dict[str, str] = {}

        self._build_toolbar()
        self._build_table()
        self._build_detail_panel()
        self._build_statusbar()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(200, self._refresh)

    # ------------------------------------------------------------------ #
    # UI construction
    # ------------------------------------------------------------------ #

    def _build_toolbar(self):
        bar = ttk.Frame(self, padding=6)
        bar.pack(side=tk.TOP, fill=tk.X)

        ttk.Button(bar, text="+ Magnet", command=self._add_magnet_dialog).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="+ Arquivo .torrent", command=self._add_file_dialog).pack(side=tk.LEFT, padx=2)
        ttk.Separator(bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6)
        ttk.Button(bar, text="Pausar", command=self._pause_selected).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="Retomar", command=self._resume_selected).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="Remover", command=self._remove_selected).pack(side=tk.LEFT, padx=2)
        ttk.Separator(bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6)
        ttk.Button(bar, text="Abrir pasta", command=self._open_folder_selected).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="Reproduzir", command=self._play_selected).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="Configuracoes", command=self._open_settings).pack(side=tk.RIGHT, padx=2)
        ttk.Button(bar, text="Agendamento", command=self._open_schedule).pack(side=tk.RIGHT, padx=2)

    def _build_table(self):
        columns = ("name", "progress", "state", "down", "up", "peers", "size")
        self.tree = ttk.Treeview(self, columns=columns, show="headings", selectmode="browse", height=8)
        headings = {
            "name": "Nome", "progress": "%", "state": "Status",
            "down": "Download", "up": "Upload", "peers": "Peers/Seeds", "size": "Tamanho",
        }
        widths = {"name": 300, "progress": 60, "state": 130, "down": 100, "up": 100, "peers": 100, "size": 150}
        for col in columns:
            self.tree.heading(col, text=headings[col])
            self.tree.column(col, width=widths[col], anchor=tk.W if col in ("name", "state") else tk.CENTER)

        self.tree.pack(side=tk.TOP, fill=tk.X, padx=6, pady=(0, 4))
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

    def _build_detail_panel(self):
        frame = ttk.Frame(self, padding=(6, 0, 6, 6))
        frame.pack(side=tk.TOP, fill=tk.X)

        self.detail_label = ttk.Label(frame, text="Selecione um torrent para ver detalhes.")
        self.detail_label.pack(side=tk.TOP, anchor=tk.W)

        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(frame, variable=self.progress_var, maximum=100)
        self.progress_bar.pack(side=tk.TOP, fill=tk.X, pady=(4, 0))

        seq_frame = ttk.Frame(frame)
        seq_frame.pack(side=tk.TOP, fill=tk.X, pady=(4, 0))
        self.sequential_var = tk.BooleanVar(value=False)
        self.sequential_check = ttk.Checkbutton(
            seq_frame, text="Download sequencial (para assistir enquanto baixa)",
            variable=self.sequential_var, command=self._toggle_sequential, state=tk.DISABLED,
        )
        self.sequential_check.pack(side=tk.LEFT)

        ttk.Label(frame, text="Mapa de pecas do arquivo (verde=baixado, laranja=baixando, cinza=faltando):").pack(
            side=tk.TOP, anchor=tk.W, pady=(8, 2)
        )
        self.piece_canvas = tk.Canvas(frame, height=20, background="#1e1e1e", highlightthickness=0)
        self.piece_canvas.pack(side=tk.TOP, fill=tk.X)

        ttk.Label(frame, text="Peers / Seeds conectados:").pack(side=tk.TOP, anchor=tk.W, pady=(8, 2))
        peer_columns = ("country", "ip", "type", "client", "progress", "down", "up", "piece", "enc")
        self.peer_tree = ttk.Treeview(frame, columns=peer_columns, show="headings", height=10)
        peer_headings = {
            "country": "Pais", "ip": "IP:Porta", "type": "Tipo", "client": "Cliente",
            "progress": "%", "down": "Download", "up": "Upload", "piece": "Peca atual", "enc": "Cripto.",
        }
        peer_widths = {
            "country": 90, "ip": 150, "type": 55, "client": 130,
            "progress": 55, "down": 90, "up": 90, "piece": 90, "enc": 65,
        }
        for col in peer_columns:
            self.peer_tree.heading(col, text=peer_headings[col])
            self.peer_tree.column(col, width=peer_widths[col], anchor=tk.CENTER if col != "client" else tk.W)
        self.peer_tree.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=(0, 4))

    def _build_statusbar(self):
        self.status_var = tk.StringVar(value="Pronto.")
        bar = ttk.Label(self, textvariable=self.status_var, anchor=tk.W, relief=tk.SUNKEN, padding=(6, 2))
        bar.pack(side=tk.BOTTOM, fill=tk.X)

    # ------------------------------------------------------------------ #
    # Acoes de torrent
    # ------------------------------------------------------------------ #

    def _add_magnet_dialog(self):
        magnet = simpledialog.askstring("Adicionar magnet", "Cole o link magnet:", parent=self)
        if not magnet:
            return
        if not magnet.startswith("magnet:"):
            messagebox.showerror("Erro", "O link informado nao parece ser um magnet link valido.")
            return
        self._add_torrent_safe(magnet)

    def _add_file_dialog(self):
        path = filedialog.askopenfilename(title="Selecione um arquivo .torrent", filetypes=[("Torrent", "*.torrent")])
        if not path:
            return
        self._add_torrent_safe(path)

    def _add_torrent_safe(self, source: str):
        try:
            self.engine.add_torrent(source, sequential=self.engine.settings["sequential_default"])
            self.status_var.set("Torrent adicionado. Obtendo metadados...")
        except Exception as exc:
            messagebox.showerror("Erro ao adicionar torrent", str(exc))

    def _selected_hash(self) -> str | None:
        sel = self.tree.selection()
        if not sel:
            return None
        item_id = sel[0]
        for info_hash, row_id in self._row_by_hash.items():
            if row_id == item_id:
                return info_hash
        return None

    def _pause_selected(self):
        info_hash = self._selected_hash()
        if info_hash:
            self.engine.pause_torrent(info_hash)

    def _resume_selected(self):
        info_hash = self._selected_hash()
        if info_hash:
            self.engine.resume_torrent(info_hash)

    def _remove_selected(self):
        info_hash = self._selected_hash()
        if not info_hash:
            return
        job = self.engine.jobs.get(info_hash, {})
        name = job.get("name", info_hash[:12])
        delete_files = messagebox.askyesnocancel(
            "Remover torrent",
            f'Remover "{name}"?\n\nSim = remover e apagar arquivos baixados\n'
            f"Nao = remover apenas da lista (mantem arquivos)\nCancelar = nao fazer nada",
        )
        if delete_files is None:
            return
        self.engine.remove_torrent(info_hash, delete_files=delete_files)
        self.status_var.set(f'Removido: "{name}"')

    def _open_folder_selected(self):
        info_hash = self._selected_hash()
        if not info_hash:
            return
        job = self.engine.jobs.get(info_hash)
        if not job:
            return
        path = job["save_path"]
        if os.path.isdir(path):
            if sys.platform == "win32":
                os.startfile(path)
            else:
                subprocess.Popen(["xdg-open", path])
        else:
            messagebox.showwarning("Pasta nao encontrada", f"A pasta {path} nao existe.")

    def _toggle_sequential(self):
        info_hash = self._selected_hash()
        if info_hash:
            self.engine.set_sequential(info_hash, self.sequential_var.get())

    def _open_settings(self):
        SettingsDialog(self, self.engine)

    def _open_schedule(self):
        ScheduleDialog(self, self.engine)

    def _play_selected(self):
        info_hash = self._selected_hash()
        if not info_hash:
            messagebox.showinfo("Reproduzir", "Selecione um torrent na lista.")
            return
        files = self.engine.get_files(info_hash)
        if not files:
            messagebox.showinfo("Reproduzir", "O torrent ainda esta obtendo metadados. Tente em instantes.")
            return
        media = [f for f in files if f["is_media"]]
        if len(media) == 1:
            self._start_stream(info_hash, media[0])
            return
        FileChooser(self, media or files, lambda f: self._start_stream(info_hash, f),
                    warn_not_media=not media)

    def _start_stream(self, info_hash: str, file: dict):
        try:
            url = self.streamer.prepare(info_hash, file["index"])
            where = self.streamer.open_in_player(url, file["name"], self.engine.settings.get("player_path", ""))
        except Exception as exc:
            messagebox.showerror("Erro ao reproduzir", str(exc))
            return
        self._stream_msg = f'{where}: "{file["name"]}" ({url})'
        self.status_var.set(self._stream_msg)

    # ------------------------------------------------------------------ #
    # Loop de atualizacao
    # ------------------------------------------------------------------ #

    def _refresh(self):
        statuses = self.engine.list_statuses()
        seen_hashes = set()
        total_down = total_up = 0.0

        for st in statuses:
            info_hash = st["info_hash"]
            seen_hashes.add(info_hash)
            total_down += st["download_rate"]
            total_up += st["upload_rate"]

            values = (
                st["name"],
                f"{st['progress'] * 100:.1f}",
                st["state"],
                format_speed(st["download_rate"]),
                format_speed(st["upload_rate"]),
                f"{st['peers']}/{st['seeds']}",
                f"{format_size(st['total_wanted_done'])} / {format_size(st['total_wanted'])}",
            )

            if info_hash in self._row_by_hash:
                self.tree.item(self._row_by_hash[info_hash], values=values)
            else:
                row_id = self.tree.insert("", tk.END, values=values)
                self._row_by_hash[info_hash] = row_id

        # remove linhas de torrents que nao existem mais
        for info_hash in list(self._row_by_hash.keys()):
            if info_hash not in seen_hashes:
                self.tree.delete(self._row_by_hash[info_hash])
                del self._row_by_hash[info_hash]

        self._update_detail_panel(statuses)
        self.engine.process_alerts()  # sem isso a fila de alertas so' cresce
        sched = self.engine.apply_schedule()
        text = (
            f"{len(statuses)} torrent(s) | Total download: {format_speed(total_down)} | "
            f"Total upload: {format_speed(total_up)}"
        )
        if sched:
            text += f" | Agendamento: {sched}"
        if getattr(self, "_stream_msg", None):
            text += f" | {self._stream_msg}"
        else:
            text += f" | Pasta padrao: {self.engine.settings['save_path']}"
        self.status_var.set(text)

        self.after(REFRESH_MS, self._refresh)

    def _update_detail_panel(self, statuses: list[dict]):
        info_hash = self._selected_hash()
        if not info_hash:
            self.detail_label.config(text="Selecione um torrent para ver detalhes.")
            self.progress_var.set(0)
            self.sequential_check.config(state=tk.DISABLED)
            self.piece_canvas.delete("all")
            self.peer_tree.delete(*self.peer_tree.get_children())
            return

        st = next((s for s in statuses if s["info_hash"] == info_hash), None)
        if not st:
            return

        self.detail_label.config(
            text=f'{st["name"]}  —  {st["state"]}  —  {format_size(st["total_wanted"])}  —  {st["save_path"]}'
        )
        self.progress_var.set(st["progress"] * 100)
        self.sequential_check.config(state=tk.NORMAL)
        job = self.engine.jobs.get(info_hash, {})
        self.sequential_var.set(job.get("sequential", False))

        self._update_piece_map(info_hash)
        self._update_peer_list(info_hash)

    def _update_piece_map(self, info_hash: str):
        self.piece_canvas.delete("all")
        states = self.engine.get_piece_map(info_hash)
        if not states:
            return

        self.update_idletasks()
        width = max(self.piece_canvas.winfo_width(), 200)
        height = 20
        block_w = width / len(states)
        for i, state in enumerate(states):
            x0 = i * block_w
            x1 = x0 + block_w + 1
            self.piece_canvas.create_rectangle(x0, 0, x1, height, fill=PIECE_COLORS[state], width=0)

    def _update_peer_list(self, info_hash: str):
        peers = self.engine.get_peers(info_hash)

        self.peer_tree.delete(*self.peer_tree.get_children())

        ips = [p["ip"] for p in peers]
        geoip.request_lookup(ips)

        for p in sorted(peers, key=lambda x: x["down_speed"], reverse=True):
            country = geoip.get_country(p["ip"])
            code = country.get("code", "")
            country_text = f'{geoip.country_flag(code)} {code}' if code else "?"

            piece_text = str(p["piece"]) if p["piece"] is not None else "-"
            type_text = "Seed" if p["is_seed"] else "Peer"
            enc_text = "Sim" if p["encrypted"] else "Nao"

            self.peer_tree.insert("", tk.END, values=(
                country_text,
                f'{p["ip"]}:{p["port"]}',
                type_text,
                p["client"],
                f'{p["progress"] * 100:.0f}',
                format_speed(p["down_speed"]),
                format_speed(p["up_speed"]),
                piece_text,
                enc_text,
            ))

    def _on_select(self, _event):
        self._update_detail_panel(self.engine.list_statuses())

    def _on_close(self):
        self.status_var.set("Salvando estado, aguarde...")
        self.update()
        self.streamer.stop()
        self.engine.shutdown()
        self.destroy()


class SettingsDialog(tk.Toplevel):
    def __init__(self, parent: TorrentGUI, engine: TorrentEngine):
        super().__init__(parent)
        self.title("Configuracoes")
        self.resizable(False, False)
        self.engine = engine
        self.parent = parent

        s = engine.settings
        pad = {"padx": 8, "pady": 4}

        self.save_path_var = tk.StringVar(value=s["save_path"])
        self.max_down_var = tk.IntVar(value=s["max_down_kbs"])
        self.max_up_var = tk.IntVar(value=s["max_up_kbs"])
        self.port_min_var = tk.IntVar(value=s["port_min"])
        self.port_max_var = tk.IntVar(value=s["port_max"])
        self.max_active_var = tk.IntVar(value=s["max_active_downloads"])
        self.sequential_default_var = tk.BooleanVar(value=s["sequential_default"])
        self.player_var = tk.StringVar(value=s.get("player_path", ""))

        row = 0
        ttk.Label(self, text="Pasta de downloads:").grid(row=row, column=0, sticky=tk.W, **pad)
        ttk.Entry(self, textvariable=self.save_path_var, width=40).grid(row=row, column=1, **pad)
        ttk.Button(self, text="...", width=3, command=self._choose_folder).grid(row=row, column=2, **pad)
        row += 1

        ttk.Label(self, text="Limite de download (KB/s, 0 = ilimitado):").grid(row=row, column=0, sticky=tk.W, **pad)
        ttk.Entry(self, textvariable=self.max_down_var, width=10).grid(row=row, column=1, sticky=tk.W, **pad)
        row += 1

        ttk.Label(self, text="Limite de upload (KB/s):").grid(row=row, column=0, sticky=tk.W, **pad)
        ttk.Entry(self, textvariable=self.max_up_var, width=10).grid(row=row, column=1, sticky=tk.W, **pad)
        row += 1

        ttk.Label(self, text="Porta minima:").grid(row=row, column=0, sticky=tk.W, **pad)
        ttk.Entry(self, textvariable=self.port_min_var, width=10).grid(row=row, column=1, sticky=tk.W, **pad)
        row += 1

        ttk.Label(self, text="Porta maxima:").grid(row=row, column=0, sticky=tk.W, **pad)
        ttk.Entry(self, textvariable=self.port_max_var, width=10).grid(row=row, column=1, sticky=tk.W, **pad)
        row += 1

        ttk.Label(self, text="Max. downloads ativos simultaneos:").grid(row=row, column=0, sticky=tk.W, **pad)
        ttk.Entry(self, textvariable=self.max_active_var, width=10).grid(row=row, column=1, sticky=tk.W, **pad)
        row += 1

        ttk.Checkbutton(
            self, text="Ativar download sequencial por padrao em novos torrents",
            variable=self.sequential_default_var,
        ).grid(row=row, column=0, columnspan=2, sticky=tk.W, **pad)
        row += 1

        ttk.Label(self, text="Player para streaming (vazio = VLC/mpv):").grid(row=row, column=0, sticky=tk.W, **pad)
        ttk.Entry(self, textvariable=self.player_var, width=40).grid(row=row, column=1, **pad)
        ttk.Button(self, text="...", width=3, command=self._choose_player).grid(row=row, column=2, **pad)
        row += 1

        note = ttk.Label(
            self,
            text="Nota: alteracoes de porta exigem reiniciar o programa para ter efeito completo.",
            foreground="gray",
        )
        note.grid(row=row, column=0, columnspan=3, sticky=tk.W, padx=8, pady=(0, 4))
        row += 1

        btn_frame = ttk.Frame(self)
        btn_frame.grid(row=row, column=0, columnspan=3, pady=8)
        ttk.Button(btn_frame, text="Salvar", command=self._save).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_frame, text="Cancelar", command=self.destroy).pack(side=tk.LEFT, padx=4)

        self.transient(parent)
        self.grab_set()

    def _choose_folder(self):
        path = filedialog.askdirectory(initialdir=self.save_path_var.get())
        if path:
            self.save_path_var.set(path)

    def _choose_player(self):
        path = filedialog.askopenfilename(title="Selecione o player",
                                          filetypes=[("Programa", "*.exe"), ("Todos", "*.*")])
        if path:
            self.player_var.set(path)

    def _save(self):
        try:
            new_settings = {
                "save_path": self.save_path_var.get(),
                "max_down_kbs": int(self.max_down_var.get()),
                "max_up_kbs": int(self.max_up_var.get()),
                "port_min": int(self.port_min_var.get()),
                "port_max": int(self.port_max_var.get()),
                "max_active_downloads": int(self.max_active_var.get()),
                "sequential_default": bool(self.sequential_default_var.get()),
                "player_path": self.player_var.get().strip(),
            }
        except (ValueError, tk.TclError):
            messagebox.showerror("Valor invalido", "Verifique se os campos numericos foram preenchidos corretamente.")
            return

        os.makedirs(new_settings["save_path"], exist_ok=True)
        self.engine.apply_settings(new_settings)
        self.destroy()


class FileChooser(tk.Toplevel):
    """Escolhe qual arquivo do torrent reproduzir."""

    def __init__(self, parent, files: list[dict], on_choose, warn_not_media: bool = False):
        super().__init__(parent)
        self.title("Reproduzir arquivo")
        self.files = sorted(files, key=lambda f: f["size"], reverse=True)
        self.on_choose = on_choose

        text = "Escolha o arquivo para reproduzir enquanto baixa:"
        if warn_not_media:
            text = "Nenhum arquivo de video/audio reconhecido. Escolha um arquivo mesmo assim:"
        ttk.Label(self, text=text, padding=8).pack(anchor=tk.W)

        self.tree = ttk.Treeview(self, columns=("name", "size", "done"), show="headings", height=10)
        for col, title, width in (("name", "Arquivo", 380), ("size", "Tamanho", 90), ("done", "Baixado", 70)):
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, anchor=tk.W if col == "name" else tk.CENTER)
        for i, f in enumerate(self.files):
            pct = f["done"] / f["size"] * 100 if f["size"] else 100
            self.tree.insert("", tk.END, iid=str(i), values=(f["path"], format_size(f["size"]), f"{pct:.0f}%"))
        if self.files:
            self.tree.selection_set("0")
        self.tree.pack(fill=tk.BOTH, expand=True, padx=8)
        self.tree.bind("<Double-1>", lambda _e: self._ok())

        btns = ttk.Frame(self, padding=8)
        btns.pack()
        ttk.Button(btns, text="Reproduzir", command=self._ok).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="Cancelar", command=self.destroy).pack(side=tk.LEFT, padx=4)
        self.transient(parent)
        self.grab_set()

    def _ok(self):
        sel = self.tree.selection()
        if not sel:
            return
        f = self.files[int(sel[0])]
        self.destroy()
        self.on_choose(f)


class ScheduleDialog(tk.Toplevel):
    """Regras de horario para parar downloads e/ou uploads."""

    STATES = ("Liberado", "Parado")

    def __init__(self, parent: TorrentGUI, engine: TorrentEngine):
        super().__init__(parent)
        self.title("Agendamento de horarios")
        self.resizable(False, False)
        self.engine = engine
        self.rules = [dict(r) for r in engine.settings.get("schedule", [])]
        pad = {"padx": 8, "pady": 4}

        self.enabled_var = tk.BooleanVar(value=engine.settings.get("schedule_enabled", False))
        ttk.Checkbutton(self, text="Ativar agendamento", variable=self.enabled_var).grid(
            row=0, column=0, columnspan=2, sticky=tk.W, **pad)

        cols = ("days", "start", "end", "down", "up")
        self.tree = ttk.Treeview(self, columns=cols, show="headings", height=6, selectmode="browse")
        for col, title, width in (("days", "Dias", 180), ("start", "Inicio", 60), ("end", "Fim", 60),
                                  ("down", "Download", 80), ("up", "Upload", 80)):
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, anchor=tk.W if col == "days" else tk.CENTER)
        self.tree.grid(row=1, column=0, columnspan=2, sticky=tk.EW, **pad)
        ttk.Button(self, text="Remover regra selecionada", command=self._remove).grid(
            row=2, column=0, columnspan=2, sticky=tk.W, **pad)

        form = ttk.LabelFrame(self, text="Nova regra", padding=8)
        form.grid(row=3, column=0, columnspan=2, sticky=tk.EW, **pad)

        days_frame = ttk.Frame(form)
        days_frame.grid(row=0, column=0, columnspan=4, sticky=tk.W)
        self.day_vars = []
        for i, name in enumerate(scheduler.DAY_NAMES):
            var = tk.BooleanVar(value=i < 5)
            ttk.Checkbutton(days_frame, text=name, variable=var).pack(side=tk.LEFT, padx=2)
            self.day_vars.append(var)

        self.start_var = tk.StringVar(value="08:00")
        self.end_var = tk.StringVar(value="18:00")
        ttk.Label(form, text="Das").grid(row=1, column=0, sticky=tk.W, pady=(6, 0))
        ttk.Entry(form, textvariable=self.start_var, width=7).grid(row=1, column=1, sticky=tk.W, pady=(6, 0))
        ttk.Label(form, text="as").grid(row=1, column=2, sticky=tk.W, pady=(6, 0))
        ttk.Entry(form, textvariable=self.end_var, width=7).grid(row=1, column=3, sticky=tk.W, pady=(6, 0))

        self.down_var = tk.StringVar(value="Parado")
        self.up_var = tk.StringVar(value="Liberado")
        ttk.Label(form, text="Download:").grid(row=2, column=0, sticky=tk.W, pady=(6, 0))
        ttk.Combobox(form, textvariable=self.down_var, values=self.STATES, state="readonly", width=9).grid(
            row=2, column=1, sticky=tk.W, pady=(6, 0))
        ttk.Label(form, text="Upload:").grid(row=2, column=2, sticky=tk.W, pady=(6, 0))
        ttk.Combobox(form, textvariable=self.up_var, values=self.STATES, state="readonly", width=9).grid(
            row=2, column=3, sticky=tk.W, pady=(6, 0))
        ttk.Button(form, text="Adicionar regra", command=self._add).grid(row=3, column=0, columnspan=4,
                                                                          sticky=tk.W, pady=(8, 0))

        ttk.Label(
            self, foreground="gray", justify=tk.LEFT,
            text="Fim antes do inicio (ex.: 23:00 as 06:00) atravessa a meia-noite.\n"
                 "Inicio igual ao fim vale o dia inteiro. Upload parado deixa o download lento:\n"
                 "os outros peers dao prioridade a quem tambem envia.",
        ).grid(row=4, column=0, columnspan=2, sticky=tk.W, padx=8)

        btns = ttk.Frame(self)
        btns.grid(row=5, column=0, columnspan=2, pady=8)
        ttk.Button(btns, text="Salvar", command=self._save).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="Cancelar", command=self.destroy).pack(side=tk.LEFT, padx=4)

        self._fill()
        self.transient(parent)
        self.grab_set()

    def _fill(self):
        self.tree.delete(*self.tree.get_children())
        for i, r in enumerate(self.rules):
            self.tree.insert("", tk.END, iid=str(i), values=(
                scheduler.describe_days(r["days"]), r["start"], r["end"],
                "Liberado" if r["download"] else "Parado",
                "Liberado" if r["upload"] else "Parado",
            ))

    def _add(self):
        try:
            rule = scheduler.validate_rule({
                "days": [i for i, v in enumerate(self.day_vars) if v.get()],
                "start": self.start_var.get(),
                "end": self.end_var.get(),
                "download": self.down_var.get() == "Liberado",
                "upload": self.up_var.get() == "Liberado",
            })
        except ValueError as exc:
            messagebox.showerror("Regra invalida", str(exc), parent=self)
            return
        self.rules.append(rule)
        self._fill()

    def _remove(self):
        sel = self.tree.selection()
        if sel:
            del self.rules[int(sel[0])]
            self._fill()

    def _save(self):
        self.engine.apply_settings({"schedule_enabled": bool(self.enabled_var.get()), "schedule": self.rules})
        self.destroy()


if __name__ == "__main__":
    app = TorrentGUI()
    app.mainloop()
