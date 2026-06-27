"""Tkinter GUI -- the front-end any user sees.

One tab per server: pick a folder/file, hit Start, and the card shows exactly
what to enter in OPL. The Terminal tab shows live output from every server. No
terminal required. The GUI never blocks on a server; each runs as a subprocess
(see process.py) and its output is pumped to the log via a thread-safe queue
drained on the Tk main thread.
"""

import platform
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import config, elevate, netinfo, tray, windows_setup
from .process import ServerProcess
from .servers import REGISTRY, REPO_ROOT

DOT_RUNNING = "●"  # filled circle
COLOR_RUNNING = "#2e9e44"
COLOR_STOPPED = "#b0b0b0"
COLOR_ERROR = "#d23c3c"

TAB_TITLES = {
    "smbv1": "SMBv1",
    "udpfs": "UDPFS",
    "udpbd": "UDPBD",
    "setup": "SETUP",
}


def opl_hint(key, ip, values):
    if key == "smbv1":
        port = "445" if values.get("take_445") else str(values.get("port") or 1445)
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
        path = (filedialog.askdirectory(parent=self) if kind == "folder"
                else filedialog.askopenfilename(parent=self))
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
        root.minsize(720, 540)
        self._build()
        self._restore()

        # On Windows, run from the system tray: closing or minimizing hides the
        # window (servers keep running) and the tray menu restores or quits.
        self._tray = None
        self._tray_queue = queue.Queue()
        if tray.AVAILABLE:
            try:
                self._tray = tray.SystemTray(
                    "PS2 Servers — running",
                    on_open=lambda: self._tray_queue.put("open"),
                    on_quit=lambda: self._tray_queue.put("quit"))
                if not self._tray.start():
                    self._tray = None
            except Exception:
                self._tray = None

        if self._tray:
            root.protocol("WM_DELETE_WINDOW", self._hide_to_tray)
            root.bind("<Unmap>", self._on_unmap)
            self.root.after(150, self._drain_tray)
        else:
            root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.root.after(150, self._drain_logs)
        self.root.after(600, self._poll_status)
        if self.saved.get("pending_firewall_allow"):
            self.root.after(350, self._allow_pending)
        elif self.saved.get("pending_cleanup"):
            self.root.after(350, self._cleanup_pending)
        elif self.saved.get("pending_start"):
            self.root.after(350, self._start_pending)

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

        # main tabs: one server per tab, plus a shared terminal tab
        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill="both", expand=True, padx=10, pady=4)
        self.server_tabs = {}

        for server in REGISTRY.values():
            tab = ttk.Frame(self.nb)
            tab.columnconfigure(0, weight=1)
            card = ServerCard(tab, self, server)
            card.grid(row=0, column=0, sticky="new", padx=8, pady=8)
            self.nb.add(tab, text=TAB_TITLES.get(server.key, server.label))
            self.server_tabs[server.key] = tab
            self.cards[server.key] = card

        self.terminal_tab = ttk.Frame(self.nb)
        self.terminal_tab.rowconfigure(0, weight=1)
        self.terminal_tab.columnconfigure(0, weight=1)
        self.terminal = tk.Text(self.terminal_tab, height=16, wrap="none",
                                state="disabled", background="#101418",
                                foreground="#d8dee9", insertbackground="#d8dee9")
        scroll = ttk.Scrollbar(self.terminal_tab, orient="vertical",
                               command=self.terminal.yview)
        self.terminal.configure(yscrollcommand=scroll.set)
        self.terminal.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=8)
        scroll.grid(row=0, column=1, sticky="ns", padx=(0, 8), pady=8)
        self.nb.add(self.terminal_tab, text="TERMINAL")

        for server in REGISTRY.values():
            self.logs[server.key] = self.terminal
        self.logs["setup"] = self.terminal

        # footer
        footer = ttk.Frame(self.root)
        footer.pack(fill="x", padx=10, pady=(0, 10))
        allow = ttk.Button(footer, text="Allow through firewall",
                           command=self.allow_windows_setup)
        allow.pack(side="left")
        remove = ttk.Button(footer, text="Remove PS2 Servers firewall rules",
                            command=self.remove_windows_setup)
        remove.pack(side="left", padx=(6, 0))
        if not windows_setup.is_windows():
            allow.config(state="disabled")
            remove.config(state="disabled")
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

        if windows_setup.is_windows():
            self._begin_windows_setup_check(key, values)
            return
        self._launch_server(key, values)

    def _set_card_busy(self, key, busy, text=None):
        card = self.cards[key]
        card.toggle_btn.config(state="disabled" if busy else "normal")
        if text:
            card.toggle_btn.config(text=text)

    def _begin_windows_setup_check(self, key, values):
        self._set_card_busy(key, True, "Checking")
        self._append_log(key, "[setup] checking Windows Firewall setup\n")

        def worker():
            setup_needed = True
            error = None
            try:
                setup_needed = windows_setup.needs_setup(key, values)
            except Exception as e:
                error = str(e)
            self.root.after(0, lambda: self._handle_windows_setup_check(
                key, values, setup_needed, error))

        threading.Thread(target=worker, daemon=True).start()

    def _handle_windows_setup_check(self, key, values, setup_needed, error=None):
        if error:
            self._append_log(key, "[setup] Windows setup check failed; elevation will retry: {}\n".format(error))

        admin_required = setup_needed or (key == "smbv1" and values.get("take_445"))
        if admin_required and not elevate.is_admin():
            self._set_card_busy(key, False, "Start")
            if not elevate.can_elevate():
                messagebox.showerror(
                    "Administrator required",
                    "Windows network setup needs administrator rights.")
                return

            summary = windows_setup.setup_summary(key, values)
            if messagebox.askyesno(
                    "Administrator required",
                    "PS2 Servers needs administrator rights to {}.\n\n"
                    "This will not enable Windows SMB1. It only manages PS2 Servers "
                    "firewall rules, and advanced port 445 mode only pauses Windows "
                    "file sharing while that server is running.\n\n"
                    "Restart the launcher as administrator now? Your settings are "
                    "saved and the server will continue automatically.".format(summary)):
                self._save(pending_start=key)
                if elevate.relaunch_as_admin():
                    self.stop_all()  # free ports before the elevated instance starts
                    if self._tray:
                        self._tray.stop()
                    self.root.destroy()
                else:
                    messagebox.showerror(
                        "Elevation failed",
                        "Could not restart as administrator.")
            return

        if setup_needed and elevate.is_admin():
            if not self._confirm_windows_setup(key, values):
                self._set_card_busy(key, False, "Start")
                return
            self._apply_windows_setup_then_start(key, values)
            return

        self._launch_server(key, values)

    def _confirm_windows_setup(self, key, values):
        summary = windows_setup.setup_summary(key, values)
        if key == "smbv1":
            detail = (
                "The SMB server is PS2 Servers' built-in OPL-compatible SMB/CIFS "
                "server. This does not enable Windows SMB1 or expose Windows file "
                "sharing over SMB1."
            )
        else:
            detail = "This only creates or refreshes PS2 Servers firewall allow rules."
        return messagebox.askyesno(
            "Allow through Windows Firewall?",
            "PS2 Servers needs to {}.\n\n{}\n\nContinue?".format(summary, detail))

    def _apply_windows_setup_then_start(self, key, values):
        self._set_card_busy(key, True, "Setting up")

        def worker():
            try:
                result = windows_setup.apply_setup(key, values)
            except Exception as e:
                self.root.after(0, lambda error=e: self._finish_windows_setup_failure(key, error))
                return
            self.root.after(0, lambda: self._finish_windows_setup_success(key, values, result))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_windows_setup_success(self, key, values, result):
        output = result.get("output") or ""
        if output:
            self._append_log(key, "[setup] {}\n".format(output.replace("\n", "\n[setup] ")))
        if result.get("restart_needed"):
            messagebox.showwarning(
                "Windows restart may be needed",
                "Windows reported that a restart may be needed before the "
                "network setup change is fully active.\n\n"
                "The PS2 Servers app will still try to start now.")
        self._launch_server(key, values)

    def _finish_windows_setup_failure(self, key, error):
        self._set_card_busy(key, False, "Start")
        messagebox.showerror("Windows setup failed", str(error))
        self._append_log(key, "[setup] failed:\n{}\n".format(error))

    def _launch_server(self, key, values):
        card = self.cards[key]
        server = REGISTRY[key]
        self._set_card_busy(key, True, "Starting")
        try:
            command = server.launch_command(values)
        except Exception as e:
            self._set_card_busy(key, False, "Start")
            messagebox.showerror("Cannot start", str(e))
            return

        self._append_log(key, "[launcher] starting: {}\n".format(" ".join(command)))
        proc = ServerProcess(key, command, cwd=REPO_ROOT, on_output=self._on_output)
        try:
            proc.start()
        except OSError as e:
            self._set_card_busy(key, False, "Start")
            messagebox.showerror("Cannot start", str(e))
            card.refresh_status(False, error=True)
            return
        self.procs[key] = proc
        card.refresh_status(True)
        card.toggle_btn.config(state="normal")
        self.nb.select(self.terminal_tab)

    def _allow_pending(self):
        self.saved.pop("pending_firewall_allow", None)
        self._save()
        self.nb.select(self.terminal_tab)
        self._append_log("setup", "[setup] continuing firewall allow after administrator restart\n")
        self._allow_windows_setup(require_confirm=False)

    def _cleanup_pending(self):
        self.saved.pop("pending_cleanup", None)
        self._save()
        self.nb.select(self.terminal_tab)
        self._append_log("setup", "[setup] continuing firewall cleanup after administrator restart\n")
        self._remove_windows_setup(require_confirm=False)

    def _start_pending(self):
        key = self.saved.get("pending_start")
        if key not in self.cards:
            return
        self.saved.pop("pending_start", None)
        self._save()
        self.nb.select(self.server_tabs[key])
        self._append_log(key, "[launcher] continuing after administrator restart\n")
        self.start_server(key)

    def allow_windows_setup(self):
        self._allow_windows_setup(require_confirm=True)

    def _allow_windows_setup(self, require_confirm=True):
        if not windows_setup.is_windows():
            messagebox.showinfo("Windows only", "Firewall rules are only needed on Windows.")
            return

        if require_confirm:
            if not messagebox.askyesno(
                    "Allow PS2 Servers through Windows Firewall?",
                    "This creates or refreshes allow rules named:\n\n"
                    "PS2 Servers - ...\n\n"
                    "It does not enable Windows SMB1 and it does not create block rules.\n\n"
                    "Continue?"):
                return

        if not elevate.is_admin():
            if not require_confirm:
                self._append_log(
                    "setup",
                    "[setup] firewall allow aborted: administrator rights were not granted\n")
                messagebox.showerror(
                    "Administrator required",
                    "Failed to acquire administrator rights to allow PS2 Servers through the firewall.")
                return
            if not elevate.can_elevate():
                messagebox.showerror(
                    "Administrator required",
                    "Allowing PS2 Servers through Windows Firewall needs administrator rights.")
                return

            self._save(pending_firewall_allow=True)
            if elevate.relaunch_as_admin():
                self.stop_all()
                if self._tray:
                    self._tray.stop()
                self.root.destroy()
            else:
                self.saved.pop("pending_firewall_allow", None)
                self._save()
                messagebox.showerror(
                    "Elevation failed",
                    "Could not restart as administrator.")
            return

        self._allow_windows_setup_async()

    def _allow_windows_setup_async(self):
        self._append_log("setup", "[setup] allowing PS2 Servers through Windows Firewall\n")
        values = {key: card.values() for key, card in self.cards.items()}

        def worker():
            try:
                outputs = []
                for key, server_values in values.items():
                    result = windows_setup.apply_setup(key, server_values)
                    output = result.get("output") or ""
                    if output:
                        outputs.append(output)
                output = "\n".join(outputs) or "PS2 Servers firewall allow rules are present."
                self.root.after(0, lambda: self._finish_allow_success({"output": output}))
            except Exception as e:
                self.root.after(0, lambda error=e: self._finish_allow_failure(error))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_allow_success(self, result):
        output = result.get("output") or "PS2 Servers firewall allow rules are present."
        self._append_log("setup", "[setup] {}\n".format(output.replace("\n", "\n[setup] ")))
        messagebox.showinfo("Allowed through firewall", output)

    def _finish_allow_failure(self, error):
        messagebox.showerror("Firewall allow failed", str(error))
        self._append_log("setup", "[setup] firewall allow failed:\n{}\n".format(error))

    def remove_windows_setup(self):
        self._remove_windows_setup(require_confirm=True)

    def _remove_windows_setup(self, require_confirm=True):
        if not windows_setup.is_windows():
            messagebox.showinfo("Windows only", "Firewall rules are only needed on Windows.")
            return

        running = [key for key in self.procs if self.is_running(key)]
        if running:
            if require_confirm:
                if not messagebox.askyesno(
                        "Stop running servers?",
                        "Firewall cleanup should be done with PS2 Servers stopped.\n\n"
                        "Stop all running servers and continue?"):
                    return
            self.stop_all()

        if require_confirm:
            if not messagebox.askyesno(
                    "Remove PS2 Servers firewall rules?",
                    "This removes only Windows Firewall rules whose display names "
                    "start with:\n\nPS2 Servers -\n\n"
                    "It does not create block rules. After this, Windows returns to "
                    "having no PS2 Servers-specific firewall rules. Continue?"):
                return

        if not elevate.is_admin():
            if not require_confirm:
                self._append_log(
                    "setup",
                    "[setup] firewall cleanup aborted: administrator rights were not granted\n")
                messagebox.showerror(
                    "Administrator required",
                    "Failed to acquire administrator rights for firewall cleanup.")
                return
            if not elevate.can_elevate():
                messagebox.showerror(
                    "Administrator required",
                    "Removing Windows Firewall rules needs administrator rights.")
                return

            self._save(pending_cleanup=True)
            if elevate.relaunch_as_admin():
                self.stop_all()
                if self._tray:
                    self._tray.stop()
                self.root.destroy()
            else:
                self.saved.pop("pending_cleanup", None)
                self._save()
                messagebox.showerror(
                    "Elevation failed",
                    "Could not restart as administrator.")
            return

        self._cleanup_windows_setup_async()

    def _cleanup_windows_setup_async(self):
        self._append_log("setup", "[setup] removing PS2 Servers firewall rules\n")

        def worker():
            try:
                result = windows_setup.remove_setup()
            except Exception as e:
                self.root.after(0, lambda error=e: self._finish_cleanup_failure(error))
                return
            self.root.after(0, lambda: self._finish_cleanup_success(result))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_cleanup_success(self, result):
        output = result.get("output") or "No PS2 Servers firewall rules found."
        self._append_log("setup", "[setup] {}\n".format(output.replace("\n", "\n[setup] ")))
        messagebox.showinfo("PS2 Servers firewall rules removed", output)

    def _finish_cleanup_failure(self, error):
        messagebox.showerror("Firewall removal failed", str(error))
        self._append_log("setup", "[setup] firewall cleanup failed:\n{}\n".format(error))

    def stop_server(self, key):
        if not self.is_running(key):
            return
        proc = self.procs.get(key)
        if proc:
            proc.stop()
        self.cards[key].refresh_status(False)
        self.cards[key].toggle_btn.config(state="normal")
        self._append_log(key, "[launcher] stopped\n")

    def stop_all(self):
        for key in list(self.procs):
            self.stop_server(key)

    # -- logging (thread-safe) ------------------------------------------- #
    def _on_output(self, key, line):
        self.out_queue.put((key, line + "\n"))

    def _drain_logs(self):
        updates = {}
        try:
            for _ in range(500):  # cap per tick so a log flood can't freeze the GUI
                key, line = self.out_queue.get_nowait()
                updates.setdefault(key, []).append(line)
        except queue.Empty:
            pass
        for key, lines in updates.items():  # one widget update per server per tick
            self._append_log(key, "".join(lines))
        self.root.after(150, self._drain_logs)

    def _append_log(self, key, text):
        widget = self.logs[key]
        widget.config(state="normal")
        widget.insert("end", self._terminal_text(key, text))
        lines = int(widget.index("end-1c").split(".")[0])
        if lines > 2000:  # keep the log bounded so memory/redraw stay cheap
            widget.delete("1.0", "{}.0".format(lines - 2000))
        widget.see("end")
        widget.config(state="disabled")

    def _terminal_text(self, key, text):
        prefix = "[{}] ".format(TAB_TITLES.get(key, key.upper()))
        return "".join(
            prefix + line if line.strip("\r\n") else line
            for line in text.splitlines(True)
        )

    # -- status polling --------------------------------------------------- #
    def _poll_status(self):
        for key, proc in self.procs.items():
            running = proc.is_running()
            current = self.cards[key].toggle_btn.cget("text") == "Stop"
            if current and not running:  # server exited on its own
                self.cards[key].refresh_status(False)
                self.cards[key].toggle_btn.config(state="normal")
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

    def _save(self, pending_start=None, pending_cleanup=False,
              pending_firewall_allow=False):
        data = {"servers": {key: card.values() for key, card in self.cards.items()},
                "ip": self.ip_var.get()}
        if pending_start:
            data["pending_start"] = pending_start
        if pending_cleanup:
            data["pending_cleanup"] = True
        if pending_firewall_allow:
            data["pending_firewall_allow"] = True
        try:
            config.save(data)
        except OSError:
            pass

    def on_close(self):
        self._save()
        # hide first so the (up to a few seconds of) child termination doesn't
        # look like a frozen window
        self.root.withdraw()
        self.stop_all()
        self.root.destroy()

    # -- system tray (Windows) -------------------------------------------- #
    def _hide_to_tray(self):
        # closing the window just hides it; the servers keep running in the tray
        self._save()
        self.root.withdraw()

    def _on_unmap(self, event):
        # minimizing also hides to the tray (off the taskbar)
        if event.widget is self.root and self.root.state() == "iconic":
            self.root.withdraw()

    def _restore_from_tray(self):
        self.root.deiconify()
        self.root.state("normal")
        self.root.lift()
        self.root.focus_force()

    def _drain_tray(self):
        try:
            while True:
                action = self._tray_queue.get_nowait()
                if action == "open":
                    self._restore_from_tray()
                elif action == "quit":
                    self._quit_from_tray()
        except queue.Empty:
            pass
        self.root.after(150, self._drain_tray)

    def _quit_from_tray(self):
        self._save()
        self.stop_all()
        if self._tray:
            self._tray.stop()
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
