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

from . import config, directlink, elevate, netinfo, tray, windows_setup
from .process import ServerProcess
from .servers import REGISTRY, REPO_ROOT, frozen_self_exe, is_frozen, serve_command

DOT_RUNNING = "●"  # filled circle
# Tab-header state. Filled = up, hollow = down, on the tab you can see without
# opening it. Deliberately not a red/green glyph: ttk.Notebook has no per-tab
# foreground, and a coloured emoji renders as a box wherever the font lacks it --
# shape survives every platform and every colourblindness.
TAB_DOT_RUNNING = "●"
TAB_DOT_STOPPED = "○"
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

Direct PS2-to-PC link

A PS2 cabled straight into the PC has no router on the wire, so nothing hands the console an IP address and every network app fails the same way. Ticking "PS2 is plugged directly into this PC" fixes that: PS2 Servers gives the chosen network port a fixed address (one administrator prompt), allows DHCP through the firewall, and runs a small DHCP helper that answers only on that port, so the console configures itself.

The helper is deliberately paranoid, because a DHCP server answering on a real network could disrupt every device on it. It binds to the direct-link port alone, refuses to run if that port reaches a router or holds a DHCP lease, hands out exactly one address to one console, and stops itself if a second device starts asking. Unticking the box stops the helper and returns the port to automatic (DHCP); "Remove PS2 Servers firewall rules" also undoes it. Direct link mode is currently Windows-only.

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
    "directlink": "DIRECT",
}


def tab_label(key, running, fallback=None):
    """Tab text carrying the server's state, so it reads from any tab.

    fallback keeps what the tabs did before they carried state: a server with no
    TAB_TITLES entry showed its own label, not a shouted key.
    """
    dot = TAB_DOT_RUNNING if running else TAB_DOT_STOPPED
    title = TAB_TITLES.get(key, fallback or key.upper())
    return "{} {}".format(dot, title)


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

    def _refresh_tab_dot(self, running):
        """Mark this server's tab up or down.

        Guarded: cards are built before the notebook finishes wiring itself up, and
        refresh_status runs during that. A tab that is not there yet has no state
        worth showing, and a TclError here would take the card down with it.
        """
        tab = getattr(self.app, "server_tabs", {}).get(self.server.key)
        nb = getattr(self.app, "nb", None)
        if tab is None or nb is None:
            return
        try:
            nb.tab(tab, text=tab_label(self.server.key, running,
                                       fallback=self.server.label))
        except tk.TclError:
            pass

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
        self._refresh_tab_dot(running and not error)
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
        self._firewall_ok = set()   # _restore fills it; never read before it exists
        self._direct_proc = None            # the DHCP helper child, when running
        self._direct_expected = False       # we started it and expect it alive
        self._direct_busy = False           # an enable/disable flow is mid-flight
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
        self._ip_trace_ready = True   # edits after this are the user's, not ours

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
        elif self.saved.get("pending_direct_link"):
            self.root.after(350, self._direct_link_pending)
        elif self.saved.get("pending_direct_link_off"):
            self.root.after(350, self._direct_link_off_pending)
        elif self.saved.get("pending_start"):
            self.root.after(350, self._start_pending)
        elif (self.saved.get("direct_link") or {}).get("enabled"):
            # Re-arm the DHCP helper for an already-configured direct link.
            # Skipped while any pending flow runs: those flows own the state.
            self.root.after(600, self._direct_link_startup)

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
        # Editable, not readonly: detection leans on getaddrinfo(gethostname()),
        # which misses or mis-ranks addresses on hosts with VPN/Hyper-V/WSL/Docker
        # adapters or a second NIC. When the right address is not in the list the
        # user has to be able to type it. This value only feeds the OPL hint text
        # -- what a server binds to is its own Bind address field.
        self.ip_combo = ttk.Combobox(header, textvariable=self.ip_var, width=18,
                                     values=netinfo.all_ipv4(), state="normal")
        self.ip_combo.grid(row=0, column=1, sticky="w", padx=(10, 6))
        # A typed address applies as you type: the OPL hint on any running card
        # follows it, and it persists without waiting for something else to save.
        # Without this, typing gave no feedback at all until the next start/stop,
        # which read as "it did not take".
        self.ip_var.trace_add("write", self._on_ip_edited)
        ttk.Button(header, text="Refresh", command=self._refresh_ips).grid(
            row=0, column=2, sticky="w")
        ttk.Label(header, text="Enter this in OPL where it asks for the PC/server IP. "
                  "Pick from the list, or type your own if the right address "
                  "isn't shown -- it saves as you type.",
                  style="TopStripHint.TLabel", wraplength=420).grid(
            row=0, column=3, sticky="w", padx=(12, 0))

        # direct PS2-to-PC link: adapter setup + DHCP helper behind one checkbox.
        # Windows-only for now -- adapter configuration is per-OS plumbing.
        if windows_setup.is_windows():
            direct = ttk.Frame(parent, style="TopStrip.TFrame", padding=(12, 8))
            direct.pack(fill="x", padx=16, pady=(0, 8))
            direct.columnconfigure(1, weight=1)
            self.direct_link_var = tk.BooleanVar(value=False)
            self._direct_check = ttk.Checkbutton(
                direct, text="PS2 is plugged directly into this PC",
                variable=self.direct_link_var, style="Card.TCheckbutton",
                command=self._on_direct_link_toggle)
            self._direct_check.grid(row=0, column=0, sticky="w")
            self._direct_status = ttk.Label(
                direct, text=self._DIRECT_STATUS_OFF,
                style="TopStripHint.TLabel", wraplength=640)
            self._direct_status.grid(row=0, column=1, sticky="w", padx=(12, 0))
        else:
            self.direct_link_var = None
            self._direct_check = None
            self._direct_status = None

        # main tabs: one server per tab, plus a shared terminal tab
        self.nb = ttk.Notebook(parent)
        self.nb.pack(fill="x", padx=16, pady=(0, 12))
        self.server_tabs = {}

        for server in REGISTRY.values():
            tab = ttk.Frame(self.nb)
            tab.columnconfigure(0, weight=1)
            card = ServerCard(tab, self, server)
            card.grid(row=0, column=0, sticky="ew", padx=10, pady=10)
            self.nb.add(tab, text=tab_label(server.key, running=False,
                                            fallback=server.label))
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
        self.logs["directlink"] = self.terminal

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

    def _on_ip_edited(self, *_args):
        # The trace is live before _restore() runs, so restoring the saved IP at
        # startup would schedule a pointless save of what was just loaded. An
        # explicit flag, not a probe of some unrelated attribute that happens to
        # be born at the right time.
        if not getattr(self, "_ip_trace_ready", False):
            return
        # Debounced: fires per keystroke, and half-typed addresses are not worth
        # saving or showing. 700ms after the last edit is "done typing".
        if getattr(self, "_ip_edit_job", None):
            self.root.after_cancel(self._ip_edit_job)
        self._ip_edit_job = self.root.after(700, self._commit_ip_edit)

    def _commit_ip_edit(self):
        self._ip_edit_job = None
        for key, card in self.cards.items():
            if self.is_running(key):
                card.refresh_status(running=True)
        self._save()

    def _refresh_ips(self):
        self.ip_combo.config(values=netinfo.all_ipv4())
        self.ip_var.set(netinfo.best_lan_ip())
        for key in self.procs:
            self.cards[key].refresh_status(self.is_running(key))

    # -- direct PS2-to-PC link -------------------------------------------- #
    _DIRECT_STATUS_OFF = (
        "Tick this if there is no router between the PS2 and this PC. It sets "
        "up the network port and gives the console its address automatically.")

    def _set_direct_status(self, text):
        if self._direct_status is not None:
            self._direct_status.config(text=text)

    def _set_direct_checkbox(self, ticked, busy=False):
        """Reflect state without re-entering the toggle handler (ttk fires the
        command only on clicks, so programmatic var changes are safe)."""
        self._direct_busy = busy
        if self.direct_link_var is not None:
            self.direct_link_var.set(bool(ticked))
        if self._direct_check is not None:
            self._direct_check.config(state="disabled" if busy else "normal")

    def _direct_ready_status(self, cfg):
        return ("Ready on '{adapter}': this PC is {server}, the PS2 gets "
                "{client} by itself. Use {server} wherever an app asks for "
                "the server IP.".format(adapter=cfg.get("adapter", "?"),
                                        server=cfg.get("server_ip", "?"),
                                        client=cfg.get("client_ip", "?")))

    def _on_direct_link_toggle(self):
        if self._direct_busy:
            return
        if self.direct_link_var.get():
            self._direct_link_begin_enable()
        else:
            self._direct_link_begin_disable()

    def _direct_link_begin_enable(self):
        self._set_direct_checkbox(True, busy=True)
        self._set_direct_status(
            "Looking for the network port the PS2 is plugged into…")

        def worker():
            try:
                enumerated = directlink.enumerate_adapters()
            except Exception as e:
                self.root.after(0, lambda err=e: self._direct_link_fail(
                    "Could not inspect this PC's network ports:\n\n{}".format(err)))
                return
            self.root.after(0, lambda: self._direct_link_choose(enumerated))

        threading.Thread(target=worker, daemon=True).start()

    def _direct_link_fail(self, message, title="Direct link setup failed"):
        messagebox.showerror(title, message)
        self._append_log("directlink", "[launcher] {}\n".format(
            message.replace("\n", " ").strip()))
        self._set_direct_checkbox(False)
        self._set_direct_status(self._DIRECT_STATUS_OFF)

    def _direct_link_choose(self, enumerated):
        candidates, rejected = directlink.find_candidates(enumerated)
        if not candidates:
            lines = ["No network port looks like a direct PS2 link right now.",
                     ""]
            for adapter, reason in rejected[:8]:
                lines.append("• {} — {}".format(adapter["name"], reason))
            lines += ["",
                      "Plug the PS2 straight into this PC with an ethernet "
                      "cable, turn the console on, then tick the box again."]
            messagebox.showinfo("No direct link found", "\n".join(lines))
            self._set_direct_checkbox(False)
            self._set_direct_status(self._DIRECT_STATUS_OFF)
            return

        adapter = (candidates[0] if len(candidates) == 1
                   else self._pick_adapter_dialog(candidates))
        if adapter is None:
            self._set_direct_checkbox(False)
            self._set_direct_status(self._DIRECT_STATUS_OFF)
            return

        taken = directlink.taken_networks(enumerated,
                                          exclude_if_index=adapter["if_index"])
        server_ip, client_ip = directlink.choose_subnet(taken)
        if not server_ip:
            self._direct_link_fail(
                "Could not find a private network range that does not "
                "collide with one this PC already uses.")
            return

        current = [i["ip"] for i in adapter.get("ipv4", [])
                   if i["ip"] and not i["ip"].startswith("169.254.")]
        note = ""
        if current:
            note = ("\n\nIts current address ({}) will be replaced; unticking "
                    "the box returns the port to automatic (DHCP), not to "
                    "that address.".format(", ".join(current)))
        if not messagebox.askyesno(
                "Set up the direct PS2 link?",
                "Use '{name}' ({desc}) as the direct PS2 link?\n\n"
                "PS2 Servers will:\n"
                "• give this PC the fixed address {server} on that port\n"
                "• allow DHCP (UDP 67) through the firewall\n"
                "• run a small DHCP helper that answers ONLY on that port, "
                "handing the PS2 {client}\n\n"
                "This needs one administrator prompt. The helper refuses to "
                "run if that port turns out to be a real network (router or "
                "DHCP server present). Untick the box to undo everything."
                "{note}".format(name=adapter["name"], desc=adapter["desc"],
                                server=server_ip, client=client_ip,
                                note=note)):
            self._set_direct_checkbox(False)
            self._set_direct_status(self._DIRECT_STATUS_OFF)
            return

        self.saved["direct_link"] = {
            "enabled": False,
            "adapter": adapter["name"],
            "if_index": adapter["if_index"],
            "server_ip": server_ip,
            "client_ip": client_ip,
            "prefix": directlink.PREFIX_LENGTH,
        }

        if not elevate.is_admin():
            if not elevate.can_elevate():
                self._direct_link_fail(
                    "Setting a fixed address on the port needs administrator "
                    "rights, and this environment cannot request them.")
                return
            self._save(pending_direct_link=True)
            if elevate.relaunch_as_admin():
                self.stop_all()
                if self._tray:
                    self._tray.stop()
                self.root.destroy()
            else:
                self.saved.pop("pending_direct_link", None)
                self._save()
                self._direct_link_fail("Could not restart as administrator.")
            return

        self._direct_link_apply_async()

    def _pick_adapter_dialog(self, candidates):
        win = tk.Toplevel(self.root)
        win.title("Which port is the PS2 in?")
        win.transient(self.root)
        win.grab_set()
        win.resizable(False, False)
        ttk.Label(win, justify="left",
                  text="More than one network port could be the PS2 link.\n"
                       "Pick the one the PS2 is plugged into:").pack(
            padx=14, pady=(12, 6), anchor="w")
        box = tk.Listbox(win, height=max(2, min(6, len(candidates))), width=64,
                         exportselection=False)
        for adapter in candidates:
            box.insert("end", "{}  —  {}".format(adapter["name"], adapter["desc"]))
        box.selection_set(0)
        box.pack(padx=14, pady=4)
        chosen = {"adapter": None}
        buttons = ttk.Frame(win)
        buttons.pack(fill="x", padx=14, pady=(6, 12))

        def use():
            selection = box.curselection()
            if selection:
                chosen["adapter"] = candidates[selection[0]]
            win.destroy()

        ttk.Button(buttons, text="Use this port", command=use).pack(
            side="right")
        ttk.Button(buttons, text="Cancel", command=win.destroy).pack(
            side="right", padx=(0, 8))
        box.bind("<Double-Button-1>", lambda _e: use())
        win.wait_window()
        return chosen["adapter"]

    def _direct_link_apply_async(self):
        cfg = self.saved.get("direct_link") or {}
        if not cfg.get("server_ip"):
            self._direct_link_fail("Direct link settings were lost; please "
                                   "tick the box again.")
            return
        self._set_direct_checkbox(True, busy=True)
        self._set_direct_status("Setting up '{}'…".format(cfg.get("adapter")))
        self.nb.select(self.terminal_tab)

        def worker():
            configured = None
            try:
                configured = directlink.apply_adapter_config(
                    cfg["if_index"], cfg["server_ip"],
                    cfg.get("prefix", directlink.PREFIX_LENGTH))
                firewall = windows_setup.apply_setup(
                    "directlink", {"server_ip": cfg["server_ip"]})
            except Exception as e:
                error = e
                if configured is not None:
                    try:
                        rollback = directlink.restore_adapter_dhcp(
                            cfg["if_index"], expect_ip=cfg["server_ip"])
                        error = RuntimeError(
                            "{}\n\nThe adapter was returned to automatic "
                            "(DHCP).\n{}".format(e, rollback))
                    except Exception as rollback_error:
                        error = RuntimeError(
                            "{}\n\nAutomatic rollback also failed: {}"
                            .format(e, rollback_error))
                self.root.after(0, lambda err=error: self._direct_link_fail(
                    "Could not set up the direct link:\n\n{}".format(err)))
                return
            output = "\n".join(
                filter(None, [configured, firewall.get("output") or ""]))
            self.root.after(0, lambda: self._direct_link_enabled(output))

        threading.Thread(target=worker, daemon=True).start()

    def _direct_link_enabled(self, output):
        if output:
            self._append_log("directlink", "[setup] {}\n".format(
                output.replace("\n", "\n[setup] ")))
        cfg = self.saved.get("direct_link") or {}
        if not self._start_direct_responder():
            return
        cfg["enabled"] = True
        self.saved["direct_link"] = cfg
        # The LAN IP box is what every OPL hint shows; the direct link has
        # exactly one address the PS2 can reach.
        self.ip_var.set(cfg["server_ip"])
        self._save()
        self._set_direct_checkbox(True)
        self._set_direct_status(self._direct_ready_status(cfg))

    def _start_direct_responder(self):
        if self._direct_proc is not None and self._direct_proc.is_running():
            return True
        cfg = self.saved.get("direct_link") or {}
        args = ["--server-ip", cfg["server_ip"],
                "--client-ip", cfg["client_ip"],
                "--prefix", str(cfg.get("prefix", directlink.PREFIX_LENGTH)),
                "--adapter", cfg.get("adapter", ""),
                "--if-index", str(cfg.get("if_index", 0))]
        command = serve_command("directlink", args)
        self._append_log("directlink",
                         "[launcher] starting DHCP helper: {}\n".format(
                             " ".join(command)))
        proc = ServerProcess("directlink", command, cwd=REPO_ROOT,
                             on_output=self._on_output)
        try:
            proc.start()
        except OSError as e:
            self._direct_link_fail("Could not start the DHCP helper: {}".format(e))
            return False
        self._direct_proc = proc
        self._direct_expected = True
        return True

    def _stop_direct_responder(self):
        self._direct_expected = False
        if self._direct_proc is not None:
            if self._direct_proc.is_running():
                self._direct_proc.stop()
                self._append_log("directlink", "[launcher] DHCP helper stopped\n")
            self._direct_proc = None

    def _direct_link_begin_disable(self):
        cfg = self.saved.get("direct_link") or {}
        if not cfg.get("enabled"):
            self._stop_direct_responder()
            self._set_direct_checkbox(False)
            self._set_direct_status(self._DIRECT_STATUS_OFF)
            return
        choice = messagebox.askyesnocancel(
            "Turn off the direct link?",
            "Return '{}' to automatic (DHCP)?\n\n"
            "Yes: undo the network setup (one administrator prompt if "
            "needed).\nNo: stop the DHCP helper but keep the fixed address "
            "{}.\nCancel: leave the direct link on.".format(
                cfg.get("adapter", "?"), cfg.get("server_ip", "?")))
        if choice is None:
            self._set_direct_checkbox(True)
            return

        self._stop_direct_responder()
        cfg["enabled"] = False
        self.saved["direct_link"] = cfg

        if choice is False:
            self._save()
            self._set_direct_checkbox(False)
            self._set_direct_status(
                "Off. '{}' keeps the fixed address {}; tick the box to use "
                "it again.".format(cfg.get("adapter", "?"),
                                   cfg.get("server_ip", "?")))
            return

        if not elevate.is_admin():
            if not elevate.can_elevate():
                self._save()
                self._direct_link_fail(
                    "Returning the port to automatic (DHCP) needs "
                    "administrator rights, and this environment cannot "
                    "request them. The DHCP helper is stopped.")
                return
            self._save(pending_direct_link_off=True)
            if elevate.relaunch_as_admin():
                self.stop_all()
                if self._tray:
                    self._tray.stop()
                self.root.destroy()
            else:
                self.saved.pop("pending_direct_link_off", None)
                self._save()
                self._direct_link_fail(
                    "Could not restart as administrator. The DHCP helper is "
                    "stopped, but '{}' still has the fixed address {} — untick "
                    "and tick later, or use Windows network settings, to "
                    "return it to DHCP.".format(cfg.get("adapter", "?"),
                                                cfg.get("server_ip", "?")))
            return

        self._save()
        self._direct_link_restore_async(cfg)

    def _direct_link_restore_async(self, cfg):
        self._set_direct_checkbox(False, busy=True)
        self._set_direct_status(
            "Returning '{}' to automatic (DHCP)…".format(cfg.get("adapter")))

        def worker():
            try:
                output = directlink.restore_adapter_dhcp(
                    cfg.get("if_index", 0), expect_ip=cfg.get("server_ip"))
            except Exception as e:
                self.root.after(0, lambda err=e: self._direct_link_fail(
                    "Could not return the port to automatic (DHCP):\n\n{}"
                    .format(err), title="Direct link cleanup failed"))
                return
            self.root.after(0, lambda: self._direct_link_restored(output))

        threading.Thread(target=worker, daemon=True).start()

    def _direct_link_restored(self, output):
        if output:
            self._append_log("directlink", "[setup] {}\n".format(
                output.replace("\n", "\n[setup] ")))
        self._set_direct_checkbox(False)
        self._set_direct_status(self._DIRECT_STATUS_OFF)

    def _direct_link_pending(self):
        self.saved.pop("pending_direct_link", None)
        self._save()
        cfg = self.saved.get("direct_link") or {}
        if not cfg.get("server_ip"):
            return
        self.nb.select(self.terminal_tab)
        self._append_log("directlink",
                         "[launcher] continuing direct link setup after "
                         "administrator restart\n")
        if not elevate.is_admin():
            self._direct_link_fail(
                "Administrator rights were not granted; the direct link was "
                "not set up.")
            return
        self._direct_link_apply_async()

    def _direct_link_off_pending(self):
        self.saved.pop("pending_direct_link_off", None)
        self._save()
        cfg = self.saved.get("direct_link") or {}
        self.nb.select(self.terminal_tab)
        self._append_log("directlink",
                         "[launcher] continuing direct link cleanup after "
                         "administrator restart\n")
        if not elevate.is_admin():
            self._direct_link_fail(
                "Administrator rights were not granted. '{}' still has the "
                "fixed address {}.".format(cfg.get("adapter", "?"),
                                           cfg.get("server_ip", "?")),
                title="Direct link cleanup failed")
            return
        if not cfg.get("if_index"):
            return
        self._direct_link_restore_async(cfg)

    def _direct_link_startup(self):
        """Re-arm an already-configured direct link on launch.

        The adapter's static address survives reboots; the helper does not.
        Verify the port still looks like ours, then start the helper (which
        re-checks the refusals itself before answering anything).
        """
        cfg = self.saved.get("direct_link") or {}
        if not cfg.get("enabled") or self.direct_link_var is None:
            return
        self._set_direct_checkbox(True, busy=True)
        self._set_direct_status("Checking the direct-link port…")

        def worker():
            problem = None
            try:
                adapter = directlink.adapter_state(cfg.get("if_index", 0),
                                                   cfg.get("adapter") or None)
                if adapter is None:
                    problem = "the direct-link port is no longer present"
                elif not any(i["ip"] == cfg.get("server_ip")
                             for i in adapter["ipv4"]):
                    problem = ("'{}' no longer has the address {}".format(
                        adapter["name"], cfg.get("server_ip")))
                else:
                    # An unplugged cable is fine -- the helper waits for it.
                    # A gateway or lease is not: refuse like the helper would.
                    ok, reason = directlink.classify_adapter(adapter,
                                                             allow_down=True)
                    if not ok:
                        problem = "'{}' {}".format(adapter["name"], reason)
            except Exception as e:
                problem = str(e)
            self.root.after(0, lambda: self._direct_link_startup_done(problem))

        threading.Thread(target=worker, daemon=True).start()

    def _direct_link_startup_done(self, problem):
        cfg = self.saved.get("direct_link") or {}
        if problem:
            cfg["enabled"] = False
            self.saved["direct_link"] = cfg
            self._save()
            self._append_log("directlink",
                             "[launcher] direct link not re-armed: {}\n".format(problem))
            self._set_direct_checkbox(False)
            self._set_direct_status(
                "Direct link is off: {}. Tick the box to set it up again."
                .format(problem))
            return
        if not self._start_direct_responder():
            cfg["enabled"] = False
            self.saved["direct_link"] = cfg
            self._save()
            return
        self._set_direct_checkbox(True)
        self._set_direct_status(self._direct_ready_status(cfg))

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
        # Windows charges for this by the machine's total rule count, not by how
        # many rules we ask about: tens of seconds on a box with a thousand of
        # them, every single start. Once an exe+ports combination has come back
        # clean there is nothing to re-learn, so skip it until that changes.
        fingerprint = windows_setup.setup_fingerprint(key, values)
        if fingerprint and fingerprint in self._firewall_ok:
            self._append_log(
                key, "[setup] firewall already allowed for this app and ports\n")
            self._launch_server(key, values)
            return

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
                key, values, setup_needed, error, notes, fingerprint))

        threading.Thread(target=worker, daemon=True).start()

    def _handle_windows_setup_check(self, key, values, setup_needed, error=None,
                                    notes=None, fingerprint=None):
        for note in (notes or []):
            self._append_log(key, "[setup] {}\n".format(note))
        if error:
            self._append_log(key, "[setup] Windows setup check failed; elevation will retry: {}\n".format(error))

        # Remember a clean answer so the next start skips the scan. Only a real
        # clean one: an error means we never learned anything, and a timeout means
        # we gave up rather than confirmed.
        if fingerprint and not setup_needed and not error \
                and not any("timed out" in n for n in (notes or [])):
            self._remember_firewall_ok(fingerprint)

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
        direct_cfg = self.saved.get("direct_link") or {}
        if direct_cfg.get("enabled"):
            values["directlink"] = {"server_ip": direct_cfg.get("server_ip")}

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
            message = ("This removes only Windows Firewall rules whose display names "
                       "start with:\n\nPS2 Servers -\n\n"
                       "It does not create block rules. After this, Windows returns to "
                       "having no PS2 Servers-specific firewall rules.")
            if (self.saved.get("direct_link") or {}).get("server_ip"):
                message += ("\n\nThe direct PS2 link is also undone: its DHCP "
                            "helper stops and the port returns to automatic "
                            "(DHCP).")
            if not messagebox.askyesno(
                    "Remove PS2 Servers firewall rules?", message + "\n\nContinue?"):
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

    def _detected_ips(self):
        """The pick-list as plain strings.

        Tk hands ["values"] back as whatever Tcl made of it: a tuple of str here,
        a bare '' when the list is empty, and on other builds Tcl_Obj. Against
        Tcl_Obj a str compares unequal, so a PICKED address would read as
        hand-typed and keep its stale-address check from ever running -- silently,
        and only on someone else's platform. splitlist asks Tcl to unpack its own
        value rather than guessing at the Python type it arrived as, which also
        covers a scalar Tcl_Obj that is neither a str nor iterable.
        """
        return [str(v) for v in self.ip_combo.tk.splitlist(self.ip_combo["values"])]

    def _remember_firewall_ok(self, fingerprint):
        if fingerprint in self._firewall_ok:
            return
        self._firewall_ok.add(fingerprint)
        self._save()

    def _forget_firewall_ok(self):
        """Drop every cached clean answer: the rules behind them are gone."""
        if not self._firewall_ok:
            return
        self._firewall_ok.clear()
        self._save()

    def _cleanup_windows_setup_async(self):
        self._append_log("setup", "[setup] removing PS2 Servers firewall rules\n")
        # The cache says "these rules exist and match". They are about to not.
        self._forget_firewall_ok()

        # Cleanup is the "give me my Windows back" button, so the direct link
        # goes too: helper stopped here (main thread owns the process), the
        # port returned to DHCP in the worker (we are already elevated).
        direct_cfg = dict(self.saved.get("direct_link") or {})
        if direct_cfg.get("server_ip"):
            self._stop_direct_responder()
            self._set_direct_checkbox(bool(direct_cfg.get("enabled")), busy=True)
            self._set_direct_status(
                "Returning '{}' to automatic (DHCP)…".format(
                    direct_cfg.get("adapter", "?")))

        def worker():
            if direct_cfg.get("server_ip"):
                try:
                    output = directlink.restore_adapter_dhcp(
                        direct_cfg.get("if_index", 0),
                        expect_ip=direct_cfg.get("server_ip"))
                    self.root.after(
                        0, lambda out=output: self._finish_direct_cleanup(out))
                except Exception as e:
                    # The firewall removal below still matters; report and go on.
                    self.root.after(
                        0, lambda err=e: self._fail_direct_cleanup(
                            err, direct_cfg))
            try:
                result = windows_setup.remove_setup()
            except Exception as e:
                self.root.after(0, lambda error=e: self._finish_cleanup_failure(error))
                return
            self.root.after(0, lambda: self._finish_cleanup_success(result))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_direct_cleanup(self, output):
        self._append_log("directlink", "[setup] {}\n".format(
            output.replace("\n", "\n[setup] ")))
        self.saved.pop("direct_link", None)
        self._save()
        self._set_direct_checkbox(False)
        self._set_direct_status(self._DIRECT_STATUS_OFF)

    def _fail_direct_cleanup(self, error, cfg):
        self._append_log(
            "directlink",
            "[setup] could not return the direct-link port to DHCP: {}\n"
            .format(error))
        self._set_direct_checkbox(bool(cfg.get("enabled")))
        self._set_direct_status(
            "Direct-link cleanup failed; the helper is stopped, but '{}' "
            "may still use {}. Retry cleanup to restore automatic (DHCP)."
            .format(cfg.get("adapter", "?"), cfg.get("server_ip", "?")))

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
        self._stop_direct_responder()

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
        if (self._direct_expected and self._direct_proc is not None
                and not self._direct_proc.is_running()):
            code = self._direct_proc.returncode
            self._direct_expected = False
            self._append_log("directlink",
                             "[launcher] DHCP helper exited (code {})\n".format(code))
            if code == 3:  # a safety refusal; the helper said why in the log
                self._set_direct_status(
                    "The DHCP helper stopped itself for safety — see the "
                    "TERMINAL tab. Untick and tick the box to retry.")
            else:
                self._set_direct_status(
                    "The DHCP helper stopped (code {}) — see the TERMINAL "
                    "tab. Untick and tick the box to retry.".format(code))
        self.root.after(600, self._poll_status)

    # -- config ----------------------------------------------------------- #
    def _saved_bool(self, key, default=False):
        value = self.saved.get(key, default)
        return bool(value) if isinstance(value, bool) else bool(default)

    def _restore(self):
        servers = self.saved.get("servers", {})
        for key, card in self.cards.items():
            card.set_values(servers.get(key, {}))
        # An auto-detected IP is only restored if this host still has it, so moving
        # between networks re-detects instead of showing a stale address. A typed
        # one is restored unconditionally -- it is not in the detected list by
        # definition, so the same check would throw it away on every launch.
        # The combo's values are what _build already detected; re-running
        # all_ipv4() here would block the startup path on getaddrinfo for nothing.
        # Fingerprints whose firewall state we have already confirmed clean. A
        # stale one only costs a rescan, never a wrong answer: the fingerprint
        # changes whenever the exe or ports do.
        self._firewall_ok = set(self.saved.get("firewall_ok") or [])
        ip = self.saved.get("ip")
        if ip and (ip in self._detected_ips() or self.saved.get("ip_custom")):
            self.ip_var.set(ip)
        self.close_to_tray_var.set(
            self._saved_bool("close_to_tray", self.close_to_tray_var.get()))
        self.minimize_to_tray_var.set(
            self._saved_bool("minimize_to_tray", self.minimize_to_tray_var.get()))

    def _save(self, pending_start=None, pending_cleanup=False,
              pending_firewall_allow=False, pending_direct_link=False,
              pending_direct_link_off=False):
        data = {"servers": {key: card.values() for key, card in self.cards.items()},
                "ip": self.ip_var.get(),
                "firewall_ok": sorted(getattr(self, "_firewall_ok", ())),
                # Not in the pick-list => the user typed it. See _restore.
                # Must be the combo's values, not a fresh all_ipv4(): _save runs on
                # every minimize/close-to-tray, so it would block the UI on
                # getaddrinfo -- and worse, an address that vanished since startup
                # (roamed, DHCP, cable out) would be misread as hand-typed and then
                # persist forever, defeating the stale check above.
                "ip_custom": self.ip_var.get() not in self._detected_ips(),
                "close_to_tray": bool(self.close_to_tray_var.get()),
                "minimize_to_tray": bool(self.minimize_to_tray_var.get())}
        if self.saved.get("direct_link"):
            data["direct_link"] = self.saved["direct_link"]
        if pending_start:
            data["pending_start"] = pending_start
        if pending_cleanup:
            data["pending_cleanup"] = True
        if pending_firewall_allow:
            data["pending_firewall_allow"] = True
        if pending_direct_link:
            data["pending_direct_link"] = True
        if pending_direct_link_off:
            data["pending_direct_link_off"] = True
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
