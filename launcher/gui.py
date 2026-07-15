"""Tkinter GUI -- the front-end any user sees.

One tab per server: pick a folder/file, hit Start, and the card shows exactly
what to enter in OPL. The Terminal tab shows live output from every server. No
terminal required. The GUI never blocks on a server; each runs as a subprocess
(see process.py) and its output is pumped to the log via a thread-safe queue
drained on the Tk main thread.
"""

import platform
import queue
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, ttk

from . import config, elevate, netinfo, tray, windows_setup
from .process import ServerProcess
from .servers import REGISTRY, REPO_ROOT, frozen_self_exe, is_frozen

DOT_RUNNING = "●"  # filled circle
COLOR_RUNNING = "#2e9e44"
COLOR_STOPPED = "#b0b0b0"
COLOR_ERROR = "#d23c3c"

APP_CONTENT_WIDTH = 1000
APP_WINDOW_WIDTH = 1024
APP_INITIAL_HEIGHT = 760
APP_MIN_HEIGHT = 420

# Field help wraps at a fixed pixel width: the window is width-locked (resizable(False, True)
# plus _enforce_fixed_width) and the scroll canvas pins its content to APP_CONTENT_WIDTH, so
# there is no horizontal resize for a wraplength to track. The 220px reserve clears the widest
# field label (161px) plus card padding and grid padx at either placement below.
HELP_WRAPLENGTH = APP_CONTENT_WIDTH - 220
# Indent checkbox help past the indicator so it lines up under the label, not under the box.
CHECK_HELP_INDENT = 27

PROJECT_URL = "https://www.psx-place.com/resources/windows-linux-mac-ps2-servers-smbv1-udpbd-udpfs-for-everyone.1728/"
REPO_URL = "https://github.com/NathanNeurotic/PS2-Servers"
RELEASES_URL = "https://github.com/NathanNeurotic/PS2-Servers/releases"
SECURITY_URL = "https://github.com/NathanNeurotic/PS2-Servers/blob/main/SECURITY.md"

ABOUT_TEXT = r"""PS2 Servers

PS2 Servers is a no-terminal launcher for PlayStation 2 network-loading servers. It gives normal users a simple GUI for starting the server mode they need, choosing folders or files, seeing live logs, and copying the exact settings they need to enter in OPL.

What it runs

- SMBv1 / RiptOPL mode: runs PS2 Servers' own small OPL-compatible SMB/CIFS server. This is not Windows File Sharing and does not require Windows' built-in SMB1 optional feature tree.
- UDPFS mode: runs a UDPFS server for OPL's UDPFS device support.
- UDPBD mode: runs a UDPBD block-device server for compatible clients.

How SMB mode works

Normal SMB mode listens on a custom TCP port, by default 1111. OPL connects directly to PS2 Servers at that port and share name. PS2 Servers speaks the small SMB/CIFS subset that OPL expects. Avoid ports below 1033 -- Windows can reserve or block low ports.

That means normal SMB mode does not need Windows File Sharing, does not need Windows SMB1 enabled, and does not expose your normal Windows shares through SMB1.

Advanced port 445 mode

Port 445 is the standard Windows SMB/File Sharing port. If you choose the advanced port 445 option, PS2 Servers may need administrator rights because Windows normally owns that port.

In that mode, PS2 Servers temporarily pauses Windows File Sharing / LanmanServer while the PS2 Servers SMB server is running, then returns control when the server stops. This is only for the advanced 445 path. Normal custom-port mode does not need it.

Windows Firewall changes

PS2 Servers creates only Windows Firewall allow rules with display names starting with:

PS2 Servers -

Those rules allow the app and selected server ports to accept inbound LAN connections from your PS2/client. The rules are created so Windows does not silently block the server.

PS2 Servers does not create firewall block rules. It does not disable Windows Firewall. It does not broadly open unrelated ports. It does not enable, disable, install, or remove Windows SMB1 optional features.

Allowing through the firewall

Use "Allow through firewall" to create or refresh PS2 Servers allow rules. This is useful after moving the app, changing ports, reinstalling, or cleaning old rules.

The allow action uses the current GUI settings, including the SMB port, UDPFS port, UDPBD port, and the current executable/Python path.

Removing firewall rules

Use "Remove PS2 Servers firewall rules" to delete only rules whose display names start with "PS2 Servers -".

Removing those rules returns Windows to having no PS2 Servers-specific firewall rules. It does not add block rules. It does not change Windows SMB1. It does not remove unrelated firewall rules.

No terminal required

The buttons in the launcher footer are the normal way to manage PS2 Servers' Windows changes. Use "Allow through firewall" to add or refresh the rules. Use "Remove PS2 Servers firewall rules" to undo them. Use "Stop all" to shut down every running PS2 Servers process from the GUI.

Advanced manual fallback

The PowerShell cleanup command still exists for advanced users, scripts, or emergency repair, but normal users should not need it:

powershell -ExecutionPolicy Bypass -File .\tools\remove-windows-firewall-rules.ps1

Equivalent manual command:

Get-NetFirewallRule -DisplayName "PS2 Servers - *" -ErrorAction SilentlyContinue | Remove-NetFirewallRule

Release transparency

PS2 Servers is open source. Packaged releases are built from the public GitHub repository. Releases can include checksums, source archives, and GitHub build provenance so users can inspect what they are running.

Unsigned Windows network tools can still trigger antivirus heuristics. That does not prove the file is malicious, but users should not have to rely on trust alone. The source, release checksums, and security notes exist for verification.
"""

TAB_TITLES = {
    "smbv1": "SMBv1",
    "udpfs": "UDPFS",
    "udpbd": "UDPBD",
    "setup": "SETUP",
}


def opl_hint(key, ip, values):
    if key == "smbv1":
        port = "445" if values.get("take_445") else str(values.get("port") or 1111)
        return ("In OPL → Network:  IP {}  ·  Port {}  ·  Share 'games'  "
                "·  NetBIOS off  ·  User 'guest'  ·  Password blank".format(ip, port))
    if key == "udpfs":
        return "In OPL → select UDPFS  ·  server IP {} (if prompted)".format(ip)
    if key == "udpbd":
        return "In OPL → select UDPBD  ·  auto-discovered (no IP or port needed)"
    return ""


class ServerCard(ttk.LabelFrame):
    """One server's controls, status and OPL hint."""

    def __init__(self, master, app, server):
        super().__init__(master, text="  " + server.label + "  ",
                         style="Card.TLabelframe")
        self.app = app
        self.server = server
        self.vars = {}
        self._active_values = None
        self._advanced_shown = False
        self._build()

    # -- widget construction ---------------------------------------------- #
    def _build(self):
        self.configure(padding=(12, 10, 12, 12))
        self.columnconfigure(1, weight=1)
        row = 0

        # header: blurb + status + start/stop
        ttk.Label(self, text=self.server.blurb, wraplength=560,
                  style="CardMuted.TLabel").grid(row=row, column=0, columnspan=3,
                                                 sticky="w", padx=4, pady=(2, 6))
        row += 1

        self.status = ttk.Label(self, text=DOT_RUNNING + " Stopped",
                                foreground=COLOR_STOPPED,
                                style="CardStatus.TLabel")
        self.status.grid(row=row, column=0, sticky="w", padx=4, pady=(0, 4))
        self.toggle_btn = ttk.Button(self, text="Start", width=10,
                                     command=self.on_toggle)
        self.toggle_btn.grid(row=row, column=2, sticky="e", padx=4, pady=(0, 4))
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
            self.adv_btn.grid(row=row, column=0, sticky="w", padx=4, pady=(4, 2))
            row += 1
            self.adv_frame = ttk.Frame(self, style="Card.TFrame")
            self.adv_frame.grid(row=row, column=0, columnspan=3, sticky="ew")
            self.adv_frame.columnconfigure(1, weight=1)
            self.adv_frame.grid_remove()
            arow = 0
            for f in advanced:
                arow = self._add_field(self.adv_frame, f, arow)
            row += 1

        self.hint = ttk.Label(self, text="", style="CardHint.TLabel",
                              wraplength=720)
        self.hint.grid(row=row, column=0, columnspan=3, sticky="w",
                       padx=4, pady=(4, 0))

    def _add_help(self, parent, text, row, column, indent):
        # Own row, so help never overlaps the entry or Browse button. columnspan
        # reaches the card's last column (2) from wherever it starts.
        ttk.Label(parent, text=text, style="CardHelp.TLabel", font=("", 8),
                  wraplength=HELP_WRAPLENGTH).grid(
            row=row, column=column, columnspan=3 - column, sticky="w",
            padx=(indent, 4), pady=(0, 4))
        return row + 1

    def _add_field(self, parent, f, row):
        if f.kind == "bool":
            var = tk.BooleanVar(value=bool(f.default))
            ttk.Checkbutton(parent, text=f.label, variable=var,
                            style="Card.TCheckbutton").grid(
                row=row, column=0, columnspan=3, sticky="w", padx=4, pady=2)
            self.vars[f.key] = var
            row += 1
            if f.help:
                row = self._add_help(parent, f.help, row, 0, CHECK_HELP_INDENT)
            return row

        ttk.Label(parent, text=f.label + ":", style="Card.TLabel").grid(
            row=row, column=0, sticky="w", padx=4, pady=2)
        if f.kind == "port":
            # Format the FIELD's own default, never the server's listen port:
            # port_display() always returns ServerDef.default_port, so every port
            # field on a server would prefill with the main port. A falsy default
            # (0/None) means "auto" and renders blank -- prefilling a data port with
            # the discovery port would collide with the discovery socket.
            if f.default:
                default_val = (("0x%04X" % f.default) if self.server.port_is_hex
                               else str(f.default))
            else:
                default_val = ""
            var = tk.StringVar(value=default_val)
            ttk.Entry(parent, textvariable=var, width=12).grid(
                row=row, column=1, sticky="w", padx=6, pady=2)
        elif f.kind in ("folder", "file"):
            var = tk.StringVar(value="")
            ttk.Entry(parent, textvariable=var).grid(
                row=row, column=1, sticky="ew", padx=6, pady=2)
            ttk.Button(parent, text="Browse…", width=10,
                       command=lambda v=var, k=f.kind: self._browse(v, k)).grid(
                row=row, column=2, sticky="e", padx=4, pady=2)
        else:  # text
            var = tk.StringVar(value=str(f.default or ""))
            ttk.Entry(parent, textvariable=var).grid(
                row=row, column=1, sticky="ew", padx=6, pady=2)
        self.vars[f.key] = var
        row += 1
        if f.help:
            row = self._add_help(parent, f.help, row, 1, 6)
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
            if isinstance(v, bool):
                # Persist booleans explicitly, including False. Most fields
                # default off, but a field that defaults ON (enable_compression)
                # needs a stored False to remember the user unticking it --
                # otherwise the default would silently re-enable it next launch.
                out[key] = v
                continue
            if isinstance(v, str):
                v = v.strip()
            if v not in ("", None):
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
        if error or not running:
            self._active_values = None
        if error:
            self.status.config(text=DOT_RUNNING + " Error", foreground=COLOR_ERROR)
        elif running:
            self.status.config(text=DOT_RUNNING + " Running", foreground=COLOR_RUNNING)
        else:
            self.status.config(text=DOT_RUNNING + " Stopped", foreground=COLOR_STOPPED)
        self.toggle_btn.config(text="Stop" if running else "Start")
        if running:
            hint_values = self._active_values if self._active_values is not None else self.values()
            self.hint.config(text=opl_hint(self.server.key, self.app.current_ip(),
                                           hint_values))
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
        self._tray = None
        self._tray_option_widgets = []
        self.close_to_tray_var = tk.BooleanVar(
            value=self._saved_bool("close_to_tray", tray.AVAILABLE))
        self.minimize_to_tray_var = tk.BooleanVar(
            value=self._saved_bool("minimize_to_tray", tray.AVAILABLE))

        root.title("PS2 Servers")
        self._configure_window()
        self.content = self._build_scroll_body()
        self._build()
        self._refresh_scroll_body()
        self._restore()

        # On Windows, run from the system tray: closing or minimizing hides the
        # window (servers keep running) and the tray menu restores or quits.
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
            root.protocol("WM_DELETE_WINDOW", self._on_window_close)
            root.bind("<Unmap>", self._on_unmap)
            self.root.after(150, self._drain_tray)
        else:
            root.protocol("WM_DELETE_WINDOW", self._on_window_close)
        self._update_tray_option_controls()

        self.root.after(150, self._drain_logs)
        self.root.after(600, self._poll_status)
        if self.saved.get("pending_firewall_allow"):
            self.root.after(350, self._allow_pending)
        elif self.saved.get("pending_cleanup"):
            self.root.after(350, self._cleanup_pending)
        elif self.saved.get("pending_start"):
            self.root.after(350, self._start_pending)

    def _configure_window(self):
        screen_height = self.root.winfo_screenheight()
        height = min(APP_INITIAL_HEIGHT, max(APP_MIN_HEIGHT, screen_height - 80))
        self.root.geometry("{}x{}".format(APP_WINDOW_WIDTH, height))
        self.root.minsize(APP_WINDOW_WIDTH, APP_MIN_HEIGHT)
        self.root.maxsize(APP_WINDOW_WIDTH, max(APP_MIN_HEIGHT, screen_height))
        self.root.resizable(False, True)
        self.root.bind("<Configure>", self._enforce_fixed_width, add="+")

    def _enforce_fixed_width(self, event):
        if str(event.widget) != str(self.root) or event.height <= 1:
            return
        if event.width == APP_WINDOW_WIDTH and self.root.state() != "zoomed":
            return

        def resize():
            try:
                height = self.root.winfo_height()
                if height <= 1:
                    height = event.height
                height = min(max(APP_MIN_HEIGHT, height),
                             self.root.winfo_screenheight())
                if self.root.state() == "zoomed":
                    self.root.state("normal")
                self.root.geometry("{}x{}".format(APP_WINDOW_WIDTH, height))
            except tk.TclError:
                pass

        self.root.after_idle(resize)

    def _build_scroll_body(self):
        bg = self.root.cget("background")
        canvas = tk.Canvas(self.root, width=APP_CONTENT_WIDTH, highlightthickness=0,
                           bd=0, background=bg)
        scrollbar = ttk.Scrollbar(self.root, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        body = ttk.Frame(canvas)
        window = canvas.create_window((0, 0), window=body, anchor="nw",
                                      width=APP_CONTENT_WIDTH)

        def refresh_scroll_region(event=None):
            height = max(1, body.winfo_reqheight())
            current_width = str(canvas.itemcget(window, "width"))
            current_height = str(canvas.itemcget(window, "height"))
            if current_width != str(APP_CONTENT_WIDTH) or current_height != str(height):
                canvas.itemconfigure(window, width=APP_CONTENT_WIDTH, height=height)
                scrollregion = canvas.bbox("all")
                if scrollregion:
                    canvas.configure(scrollregion=scrollregion)

        body.bind("<Configure>", refresh_scroll_region)
        canvas.bind("<Configure>", refresh_scroll_region)
        self._scroll_canvas = canvas
        self._scrollbar = scrollbar
        self._scroll_window = window
        self._bind_body_mousewheel(canvas)
        self._refresh_scroll_body = refresh_scroll_region
        return body

    def _bind_body_mousewheel(self, canvas):
        def should_scroll_page(event):
            widget = event.widget
            if isinstance(widget, str):
                try:
                    widget = canvas.nametowidget(widget)
                except (KeyError, tk.TclError):
                    widget = None
            if hasattr(widget, "winfo_class") and widget.winfo_class() == "Text":
                return False
            first, last = canvas.yview()
            return first > 0.0 or last < 1.0

        def on_mousewheel(event):
            if not should_scroll_page(event):
                return None
            units = -1 if event.delta > 0 else 1
            canvas.yview_scroll(units, "units")
            return "break"

        def on_scroll_up(event):
            if should_scroll_page(event):
                canvas.yview_scroll(-1, "units")
                return "break"
            return None

        def on_scroll_down(event):
            if should_scroll_page(event):
                canvas.yview_scroll(1, "units")
                return "break"
            return None

        self.root.bind("<MouseWheel>", on_mousewheel, add="+")
        self.root.bind("<Button-4>", on_scroll_up, add="+")
        self.root.bind("<Button-5>", on_scroll_down, add="+")

    def _build(self):
        parent = self.content
        # header: LAN IP the user types into OPL
        header = ttk.Frame(parent, style="TopStrip.TFrame", padding=(12, 10))
        header.pack(fill="x", padx=16, pady=(12, 8))
        header.columnconfigure(3, weight=1)
        ttk.Label(header, text="LAN IP", font=("", 10, "bold"),
                  style="TopStripTitle.TLabel").grid(row=0, column=0, sticky="w")
        self.ip_var = tk.StringVar(value=netinfo.best_lan_ip())
        self.ip_combo = ttk.Combobox(header, textvariable=self.ip_var, width=18,
                                     values=netinfo.all_ipv4(), state="readonly")
        self.ip_combo.grid(row=0, column=1, sticky="w", padx=(10, 6))
        ttk.Button(header, text="Refresh", command=self._refresh_ips).grid(
            row=0, column=2, sticky="w")
        ttk.Label(header, text="Enter this in OPL where it asks for the PC/server IP.",
                  style="TopStripHint.TLabel", wraplength=420).grid(
            row=0, column=3, sticky="w", padx=(12, 0))

        # main tabs: one server per tab, plus a shared terminal tab
        self.nb = ttk.Notebook(parent)
        self.nb.pack(fill="x", padx=16, pady=(0, 12))
        self.server_tabs = {}

        for server in REGISTRY.values():
            tab = ttk.Frame(self.nb)
            tab.columnconfigure(0, weight=1)
            card = ServerCard(tab, self, server)
            card.grid(row=0, column=0, sticky="ew", padx=10, pady=10)
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

        self._build_about_tab()

        # Control bar: packed above the notebook (before=self.nb) so all controls
        # -- LAN IP, admin panel, and these actions -- sit together at the top.
        footer = ttk.Frame(parent, style="Footer.TFrame", padding=(12, 10))
        footer.pack(fill="x", padx=16, pady=(0, 8), before=self.nb)
        footer.columnconfigure(2, weight=1)
        allow = ttk.Button(footer, text="Allow through firewall",
                           command=self.allow_windows_setup)
        allow.grid(row=0, column=0, sticky="w")
        remove = ttk.Button(footer, text="Remove PS2 Servers firewall rules",
                            command=self.remove_windows_setup)
        remove.grid(row=0, column=1, sticky="w", padx=(8, 0))
        if not windows_setup.is_windows():
            allow.config(state="disabled")
            remove.config(state="disabled")
        ttk.Button(footer, text="Stop all", command=self.stop_all).grid(
            row=0, column=3, sticky="e")
        ttk.Button(footer, text="Restart", command=self.restart_app).grid(
            row=0, column=4, sticky="e", padx=(8, 0))
        ttk.Button(footer, text="Exit", command=self.exit_app).grid(
            row=0, column=5, sticky="e", padx=(8, 0))

    def _build_about_tab(self):
        about = ttk.Frame(self.nb)
        about.columnconfigure(0, weight=1)
        row = 0

        try:
            from . import theme_assets
            logo = theme_assets.photo_fit(sys.modules[__name__], "LOGO", owner=self,
                                          max_width=150, max_height=150)
        except (ImportError, tk.TclError):
            logo = None
        if logo:
            brand = ttk.Frame(about)
            brand.grid(row=row, column=0, sticky="ew", padx=8, pady=(8, 0))
            tk.Label(brand, image=logo, bd=0, highlightthickness=0,
                     background=self.root.cget("background")).pack(side="left")
            ttk.Label(brand, text="PS2 Servers", font=("", 14, "bold")).pack(
                side="left", padx=(10, 0))
            row += 1

        links = ttk.LabelFrame(about, text=" Links ")
        links.grid(row=row, column=0, sticky="ew", padx=8, pady=(8, 0))
        row += 1
        ttk.Button(links, text="Project page",
                   command=lambda: self._open_url(PROJECT_URL)).pack(side="left", padx=(6, 0), pady=6)
        ttk.Button(links, text="GitHub repo",
                   command=lambda: self._open_url(REPO_URL)).pack(side="left", padx=(6, 0), pady=6)
        ttk.Button(links, text="Releases",
                   command=lambda: self._open_url(RELEASES_URL)).pack(side="left", padx=(6, 0), pady=6)
        ttk.Button(links, text="Security notes",
                   command=lambda: self._open_url(SECURITY_URL)).pack(side="left", padx=(6, 0), pady=6)

        behavior = ttk.LabelFrame(about, text=" Window behavior ")
        behavior.grid(row=row, column=0, sticky="ew", padx=8, pady=(8, 0))
        behavior.columnconfigure(2, weight=1)
        row += 1
        close_to_tray = ttk.Checkbutton(
            behavior, text="Close to tray", variable=self.close_to_tray_var,
            command=self._save, style="Card.TCheckbutton")
        close_to_tray.grid(row=0, column=0, sticky="w", padx=(6, 12), pady=6)
        minimize_to_tray = ttk.Checkbutton(
            behavior, text="Minimize to tray", variable=self.minimize_to_tray_var,
            command=self._save, style="Card.TCheckbutton")
        minimize_to_tray.grid(row=0, column=1, sticky="w", padx=(0, 12), pady=6)
        self._tray_option_widgets.extend([close_to_tray, minimize_to_tray])

        text_frame = ttk.Frame(about)
        about.rowconfigure(row, weight=1)
        text_frame.grid(row=row, column=0, sticky="nsew", padx=8, pady=8)
        text_frame.rowconfigure(0, weight=1)
        text_frame.columnconfigure(0, weight=1)
        text = tk.Text(text_frame, wrap="word", height=18, state="normal")
        scroll = ttk.Scrollbar(text_frame, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        text.insert("1.0", ABOUT_TEXT)
        text.config(state="disabled")

        self.nb.add(about, text="ABOUT")

    def _open_url(self, url):
        try:
            webbrowser.open_new_tab(url)
        except Exception as e:
            messagebox.showerror("Cannot open link", str(e))

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
            notes = []
            try:
                setup_needed = windows_setup.needs_setup(key, values, log=notes.append)
            except Exception as e:
                error = str(e)
            self.root.after(0, lambda: self._handle_windows_setup_check(
                key, values, setup_needed, error, notes))

        threading.Thread(target=worker, daemon=True).start()

    def _handle_windows_setup_check(self, key, values, setup_needed, error=None, notes=None):
        for note in (notes or []):
            self._append_log(key, "[setup] {}\n".format(note))
        if error:
            self._append_log(key, "[setup] Windows setup check failed; elevation will retry: {}\n".format(error))

        take_445 = key == "smbv1" and bool(values.get("take_445"))
        admin_required = setup_needed or take_445
        if admin_required and not elevate.is_admin():
            self._set_card_busy(key, False, "Start")
            if not elevate.can_elevate():
                messagebox.showerror(
                    "Administrator required",
                    "Windows network setup needs administrator rights.")
                return

            summary = windows_setup.setup_summary(key, values)
            message = (
                "PS2 Servers needs administrator rights to {}.\n\n"
                "This will not enable Windows SMB1. It only manages PS2 Servers "
                "firewall rules, and advanced port 445 mode only pauses Windows "
                "file sharing while that server is running.\n\n"
                "Restart the launcher as administrator now? Your settings are "
                "saved and the server will continue automatically.".format(summary))
            if not take_445:
                message += (
                    "\n\nChoose No to start the server anyway without firewall "
                    "setup (if Windows Firewall is active, the PS2 may not be "
                    "able to connect).")
            if messagebox.askyesno("Administrator required", message):
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
            elif not take_445:
                self._append_log(key, "[setup] firewall setup skipped by user; starting anyway\n")
                self._launch_server(key, values)
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
        card._active_values = dict(values)
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
        proc = self.procs.get(key)
        if not proc:
            return
        if proc.is_running():
            proc.stop()
        self.cards[key]._active_values = None
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
                self.cards[key]._active_values = None
                self.cards[key].refresh_status(False)
                self.cards[key].toggle_btn.config(state="normal")
                self._append_log(key, "[launcher] server exited (code {})\n".format(
                    proc.returncode))
        self.root.after(600, self._poll_status)

    # -- config ----------------------------------------------------------- #
    def _saved_bool(self, key, default=False):
        value = self.saved.get(key, default)
        return bool(value) if isinstance(value, bool) else bool(default)

    def _restore(self):
        servers = self.saved.get("servers", {})
        for key, card in self.cards.items():
            card.set_values(servers.get(key, {}))
        ip = self.saved.get("ip")
        if ip and ip in netinfo.all_ipv4():
            self.ip_var.set(ip)
        self.close_to_tray_var.set(
            self._saved_bool("close_to_tray", self.close_to_tray_var.get()))
        self.minimize_to_tray_var.set(
            self._saved_bool("minimize_to_tray", self.minimize_to_tray_var.get()))

    def _save(self, pending_start=None, pending_cleanup=False,
              pending_firewall_allow=False):
        data = {"servers": {key: card.values() for key, card in self.cards.items()},
                "ip": self.ip_var.get(),
                "close_to_tray": bool(self.close_to_tray_var.get()),
                "minimize_to_tray": bool(self.minimize_to_tray_var.get())}
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
        self.exit_app(confirm=False)

    def exit_app(self, confirm=True):
        if confirm and not self._confirm_app_shutdown("Exit PS2 Servers?"):
            return
        self._shutdown_app()

    def restart_app(self):
        if not self._confirm_app_shutdown("Restart PS2 Servers?"):
            return
        self._save()
        command = self._restart_command()
        try:
            subprocess.Popen(command, cwd=None if is_frozen() else REPO_ROOT)
        except OSError as e:
            messagebox.showerror("Restart failed", str(e))
            return
        self._shutdown_app()

    def _restart_command(self):
        if is_frozen():
            return [frozen_self_exe()]
        return [sys.executable, "-m", "launcher"]

    def _confirm_app_shutdown(self, title):
        running = [TAB_TITLES.get(key, key.upper())
                   for key in self.procs if self.is_running(key)]
        if not running:
            return True
        return messagebox.askyesno(
            title,
            "This will stop running servers:\n\n{}\n\nContinue?".format(
                ", ".join(running)))

    def _shutdown_app(self):
        self._save()
        # hide first so the (up to a few seconds of) child termination doesn't
        # look like a frozen window
        self.root.withdraw()
        self.stop_all()
        if self._tray:
            self._tray.stop()
        self.root.destroy()

    # -- system tray (Windows) -------------------------------------------- #
    def _on_window_close(self):
        if self._should_close_to_tray():
            self._hide_to_tray()
            return
        self.exit_app(confirm=False)

    def _should_close_to_tray(self):
        return bool(self._tray and self.close_to_tray_var.get())

    def _should_minimize_to_tray(self):
        return bool(self._tray and self.minimize_to_tray_var.get())

    def _update_tray_option_controls(self):
        state = "normal" if self._tray else "disabled"
        for widget in self._tray_option_widgets:
            try:
                widget.config(state=state)
            except tk.TclError:
                pass

    def _hide_to_tray(self):
        # closing the window just hides it; the servers keep running in the tray
        self._save()
        self.root.withdraw()

    def _on_unmap(self, event):
        # minimizing can hide to the tray (off the taskbar) when enabled.
        if (event.widget is self.root and self.root.state() == "iconic"
                and self._should_minimize_to_tray()):
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
                    self.exit_app(confirm=False)
        except queue.Empty:
            pass
        self.root.after(150, self._drain_tray)

    def _quit_from_tray(self):
        self.exit_app(confirm=False)


def run_gui():
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista" if platform.system() == "Windows" else "clam")
    except tk.TclError:
        pass
    LauncherApp(root)
    root.mainloop()
    return 0
