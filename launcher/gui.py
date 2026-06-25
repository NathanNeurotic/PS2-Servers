"""Tkinter GUI -- the front-end any user sees.

One card per server: pick a folder/file, hit Start, and the card shows exactly
what to enter in OPL plus a live log. No terminal required. The GUI never blocks
on a server; each runs as a subprocess (see process.py) and its output is pumped
to the log via a thread-safe queue drained on the Tk main thread.
"""

import platform
import queue
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import config, netinfo
from .process import ServerProcess
from .servers import REGISTRY, REPO_ROOT

DOT_RUNNING = "●"  # filled circle
COLOR_RUNNING = "#2e9e44"
COLOR_STOPPED = "#b0b0b0"
COLOR_ERROR = "#d23c3c"


def opl_hint(key, ip, values):
    if key == "smbv1":
        port = str(values.get("port") or 1445)
        return ("In OPL → Network:  IP {}  ·  Port {}  ·  Share 'games'  "
                "·  NetBIOS off  ·  user/pass blank".format(ip, port))
    if key == "udpfs":
        return "In OPL → select UDPFS  ·  server IP {} (if prompted)".format(ip)
    if key == "udpbd":
        return "In OPL → select UDPBD  ·  auto-discovered (no IP or port needed)"
    return ""


class ServerCard(ttk.LabelFrame):
    """One server's controls, status and OPL hint."""

    def __init__(self, master, app, server):
        super().__init__(master, text="  " + server.label + "  ")
        self.app = app
        self.server = server
        self.vars = {}
        self._advanced_shown = False
        self._build()

    # -- widget construction ---------------------------------------------- #
    def _build(self):
        self.columnconfigure(1, weight=1)
        row = 0

        # header: blurb + status + start/stop
        ttk.Label(self, text=self.server.blurb, wraplength=560,
                  foreground="#555").grid(row=row, column=0, columnspan=3,
                                          sticky="w", padx=8, pady=(6, 2))
        row += 1

        self.status = ttk.Label(self, text=DOT_RUNNING + " Stopped",
                                foreground=COLOR_STOPPED)
        self.status.grid(row=row, column=0, sticky="w", padx=8)
        self.toggle_btn = ttk.Button(self, text="Start", width=10,
                                     command=self.on_toggle)
        self.toggle_btn.grid(row=row, column=2, sticky="e", padx=8, pady=2)
        if not self.server.is_available():
            self.status.config(text="n/a on this OS", foreground=COLOR_ERROR)
            self.toggle_btn.config(state="disabled")
        row += 1

        # primary fields, then advanced fields (hidden behind a toggle)
        primary = [f for f in self.server.fields if not f.advanced]
        advanced = [f for f in self.server.fields if f.advanced]
        for f in primary:
            row = self._add_field(self, f, row)

        if advanced:
            self.adv_btn = ttk.Button(self, text="Advanced ▸", width=14,
                                      command=self._toggle_advanced)
            self.adv_btn.grid(row=row, column=0, sticky="w", padx=8, pady=2)
            row += 1
            self.adv_frame = ttk.Frame(self)
            self.adv_frame.grid(row=row, column=0, columnspan=3, sticky="ew")
            self.adv_frame.columnconfigure(1, weight=1)
            self.adv_frame.grid_remove()
            arow = 0
            for f in advanced:
                arow = self._add_field(self.adv_frame, f, arow)
            row += 1

        self.hint = ttk.Label(self, text="", foreground="#1c6db5", wraplength=620)
        self.hint.grid(row=row, column=0, columnspan=3, sticky="w", padx=8, pady=(2, 6))

    def _add_field(self, parent, f, row):
        if f.kind == "bool":
            var = tk.BooleanVar(value=bool(f.default))
            ttk.Checkbutton(parent, text=f.label, variable=var).grid(
                row=row, column=0, columnspan=3, sticky="w", padx=8, pady=1)
            self.vars[f.key] = var
            return row + 1

        ttk.Label(parent, text=f.label + ":").grid(row=row, column=0, sticky="w",
                                                   padx=8, pady=1)
        if f.kind == "port":
            var = tk.StringVar(value=self.server.port_display())
            ttk.Entry(parent, textvariable=var, width=12).grid(
                row=row, column=1, sticky="w", padx=4, pady=1)
        elif f.kind in ("folder", "file"):
            var = tk.StringVar(value="")
            ttk.Entry(parent, textvariable=var).grid(
                row=row, column=1, sticky="ew", padx=4, pady=1)
            ttk.Button(parent, text="Browse…", width=10,
                       command=lambda v=var, k=f.kind: self._browse(v, k)).grid(
                row=row, column=2, sticky="e", padx=8, pady=1)
        else:  # text
            var = tk.StringVar(value=str(f.default or ""))
            ttk.Entry(parent, textvariable=var).grid(
                row=row, column=1, sticky="ew", padx=4, pady=1)
        self.vars[f.key] = var
        row += 1
        if f.help:  # on its own row so it never overlaps the entry/Browse button
            ttk.Label(parent, text=f.help, foreground="#888", font=("", 8)).grid(
                row=row, column=1, columnspan=2, sticky="w", padx=4, pady=(0, 2))
            row += 1
        return row

    def _toggle_advanced(self):
        self._advanced_shown = not self._advanced_shown
        if self._advanced_shown:
            self.adv_frame.grid()
            self.adv_btn.config(text="Advanced ▾")
        else:
            self.adv_frame.grid_remove()
            self.adv_btn.config(text="Advanced ▸")

    def _browse(self, var, kind):
        path = (filedialog.askdirectory() if kind == "folder"
                else filedialog.askopenfilename())
        if path:
            var.set(path)

    # -- values / config --------------------------------------------------- #
    def values(self):
        out = {}
        for key, var in self.vars.items():
            v = var.get()
            if isinstance(v, str):
                v = v.strip()
            if v not in ("", False, None):
                out[key] = v
        return out

    def set_values(self, saved):
        for key, var in self.vars.items():
            if key in saved:
                var.set(saved[key])

    # -- lifecycle --------------------------------------------------------- #
    def on_toggle(self):
        if self.app.is_running(self.server.key):
            self.app.stop_server(self.server.key)
        else:
            self.app.start_server(self.server.key)

    def refresh_status(self, running, error=False):
        if error:
            self.status.config(text=DOT_RUNNING + " Error", foreground=COLOR_ERROR)
        elif running:
            self.status.config(text=DOT_RUNNING + " Running", foreground=COLOR_RUNNING)
        else:
            self.status.config(text=DOT_RUNNING + " Stopped", foreground=COLOR_STOPPED)
        self.toggle_btn.config(text="Stop" if running else "Start")
        if running:
            self.hint.config(text=opl_hint(self.server.key, self.app.current_ip(),
                                           self.values()))
        else:
            self.hint.config(text="")


class LauncherApp:
    def __init__(self, root):
        self.root = root
        self.procs = {}
        self.cards = {}
        self.out_queue = queue.Queue()
        self.logs = {}
        self.saved = config.load()

        root.title("PS2 Servers")
        root.minsize(720, 600)
        self._build()
        self._restore()

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(150, self._drain_logs)
        self.root.after(600, self._poll_status)

    def _build(self):
        # header: LAN IP the user types into OPL
        header = ttk.Frame(self.root)
        header.pack(fill="x", padx=10, pady=(10, 4))
        ttk.Label(header, text="Your PC's LAN IP:", font=("", 10, "bold")).pack(side="left")
        self.ip_var = tk.StringVar(value=netinfo.best_lan_ip())
        self.ip_combo = ttk.Combobox(header, textvariable=self.ip_var, width=18,
                                     values=netinfo.all_ipv4(), state="readonly")
        self.ip_combo.pack(side="left", padx=6)
        ttk.Button(header, text="Refresh", command=self._refresh_ips).pack(side="left")
        ttk.Label(header, text="  (enter this in OPL where it asks for the PC/server IP)",
                  foreground="#888").pack(side="left")

        # server cards
        cards = ttk.Frame(self.root)
        cards.pack(fill="x", padx=10, pady=4)
        for server in REGISTRY.values():
            card = ServerCard(cards, self, server)
            card.pack(fill="x", pady=5)
            self.cards[server.key] = card

        # logs
        logframe = ttk.LabelFrame(self.root, text="  Logs  ")
        logframe.pack(fill="both", expand=True, padx=10, pady=(4, 6))
        self.nb = ttk.Notebook(logframe)
        self.nb.pack(fill="both", expand=True, padx=4, pady=4)
        for server in REGISTRY.values():
            txt = tk.Text(self.nb, height=8, wrap="none", state="disabled",
                          background="#101418", foreground="#d8dee9",
                          insertbackground="#d8dee9")
            self.nb.add(txt, text=server.label)
            self.logs[server.key] = txt

        # footer
        footer = ttk.Frame(self.root)
        footer.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(footer, text="Stop all", command=self.stop_all).pack(side="right")

    # -- IP --------------------------------------------------------------- #
    def current_ip(self):
        return self.ip_var.get()

    def _refresh_ips(self):
        self.ip_combo.config(values=netinfo.all_ipv4())
        self.ip_var.set(netinfo.best_lan_ip())
        for key in self.procs:
            self.cards[key].refresh_status(self.is_running(key))

    # -- run/stop --------------------------------------------------------- #
    def is_running(self, key):
        p = self.procs.get(key)
        return p is not None and p.is_running()

    def start_server(self, key):
        card = self.cards[key]
        server = REGISTRY[key]
        values = card.values()

        missing = [f.label for f in server.fields
                   if f.required and not values.get(f.key)]
        if missing:
            messagebox.showerror("Missing input",
                                 "Please set: " + ", ".join(missing))
            return
        try:
            command = server.launch_command(values)
        except Exception as e:
            messagebox.showerror("Cannot start", str(e))
            return

        self._append_log(key, "[launcher] starting: {}\n".format(" ".join(command)))
        proc = ServerProcess(key, command, cwd=REPO_ROOT, on_output=self._on_output)
        try:
            proc.start()
        except OSError as e:
            messagebox.showerror("Cannot start", str(e))
            card.refresh_status(False, error=True)
            return
        self.procs[key] = proc
        card.refresh_status(True)
        self.nb.select(list(REGISTRY).index(key))

    def stop_server(self, key):
        proc = self.procs.get(key)
        if proc:
            proc.stop()
        self.cards[key].refresh_status(False)
        self._append_log(key, "[launcher] stopped\n")

    def stop_all(self):
        for key in list(self.procs):
            self.stop_server(key)

    # -- logging (thread-safe) ------------------------------------------- #
    def _on_output(self, key, line):
        self.out_queue.put((key, line + "\n"))

    def _drain_logs(self):
        try:
            while True:
                key, line = self.out_queue.get_nowait()
                self._append_log(key, line)
        except queue.Empty:
            pass
        self.root.after(150, self._drain_logs)

    def _append_log(self, key, text):
        widget = self.logs[key]
        widget.config(state="normal")
        widget.insert("end", text)
        widget.see("end")
        widget.config(state="disabled")

    # -- status polling --------------------------------------------------- #
    def _poll_status(self):
        for key, proc in self.procs.items():
            running = proc.is_running()
            current = self.cards[key].toggle_btn.cget("text") == "Stop"
            if current and not running:  # server exited on its own
                self.cards[key].refresh_status(False)
                self._append_log(key, "[launcher] server exited (code {})\n".format(
                    proc.returncode))
        self.root.after(600, self._poll_status)

    # -- config ----------------------------------------------------------- #
    def _restore(self):
        servers = self.saved.get("servers", {})
        for key, card in self.cards.items():
            card.set_values(servers.get(key, {}))
        ip = self.saved.get("ip")
        if ip and ip in netinfo.all_ipv4():
            self.ip_var.set(ip)

    def _save(self):
        data = {"servers": {key: card.values() for key, card in self.cards.items()},
                "ip": self.ip_var.get()}
        try:
            config.save(data)
        except OSError:
            pass

    def on_close(self):
        self._save()
        self.stop_all()
        self.root.destroy()


def run_gui():
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista" if platform.system() == "Windows" else "clam")
    except tk.TclError:
        pass
    LauncherApp(root)
    root.mainloop()
    return 0
