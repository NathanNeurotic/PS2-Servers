"""Tkinter GUI -- the front-end any user sees.

One tab per server: pick a folder/file, hit Start, and the card shows exactly
what to enter in OPL. The Terminal tab shows live output from every server. No
terminal required. The GUI never blocks on a server; each runs as a subprocess
(see process.py) and its output is pumped to the log via a thread-safe queue
drained on the Tk main thread.
"""

import os
import platform
import queue
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from tkinter import filedialog, font as tkfont, messagebox, ttk

from . import status_dot


def _direct_link_supported():
    """OSes where the direct-link checkbox is offered at all."""
    return platform.system() in ("Windows", "Linux", "Darwin")


def _direct_link_experimental():
    """Non-Windows: the setup path is real but unverified on hardware."""
    return platform.system() in ("Linux", "Darwin")

from . import config, directlink, elevate, netinfo, posix_firewall, servers, status_client, theme, tray, windows_setup
from .process import ServerProcess
from .release_metadata import DISPLAY_VERSION
from .servers import REGISTRY, REPO_ROOT, frozen_self_exe, is_frozen, serve_command

APP_VERSION_LABEL = "v" + DISPLAY_VERSION

DOT_RUNNING = "●"  # filled circle, used in the in-card status label
# Per-tab running state. The primary indicator is a small coloured image dot
# (green disc = running, grey ring = stopped) set as the tab's image -- a tab's
# text is a single colour, so a glyph could never be just-the-dot green, but a
# per-tab image carries its own colour and renders identically everywhere. These
# glyphs remain the fallback for the rare case image generation is unavailable.
TAB_DOT_RUNNING = "●"
TAB_DOT_STOPPED = "○"
COLOR_RUNNING = "#2e9e44"
COLOR_STOPPED = "#b0b0b0"
COLOR_ERROR = "#d23c3c"

APP_WINDOW_WIDTH = 1024     # preferred opening width; the window resizes freely
APP_INITIAL_HEIGHT = 760
APP_MIN_WIDTH = 780
APP_MIN_HEIGHT = 420
# The tab strip's right-hand tail: the notebook's right tabmargin and border, which
# sit past the last tab and so are not included when the strip is measured, plus a
# few pixels of slack so the last tab keeps its whole border at the minimum width.
TAB_STRIP_TAIL = 20

# The window resizes in both directions and the scroll canvas hands its full width
# to the content, so nothing may carry a hard-coded wraplength: every wrapping label
# tracks the real width of a frame that resizes with the window (bind_wraplength).
#
# Card text measures against the NOTEBOOK, not the card: ttk unmaps unselected tabs,
# so a card the user has not visited has no width yet, and asking it would feed each
# label a made-up figure -- which the card then reports back as the width it needs,
# widening the whole page. The notebook is always laid out. These reserves are the
# padding chain between it and the text: notebook padding + tab border + the card's
# grid padx + card padding + label padx, then the field label column for help text.
CARD_TEXT_RESERVE = 72
HELP_RESERVE = 190
# Indent checkbox help past the indicator so it lines up under the label, not under the box.
CHECK_HELP_INDENT = 27
CHECK_HELP_RESERVE = CARD_TEXT_RESERVE + CHECK_HELP_INDENT
# Never wrap narrower than this, however small the window gets: past this point the
# text is unreadable anyway, and letting it clip is the honest failure -- the page
# scrollbar and the window's own minsize are what keep it from happening.
WRAP_MIN = 200
# Ignore sub-8px width changes. Setting wraplength re-requests the label's size, so a
# label that chases every pixel can trade requests with its container forever; a dead
# band that big is below one character and settles immediately.
WRAP_STEP = 8


def bind_wraplength(widget, source, reserve=0, siblings=(), minimum=WRAP_MIN):
    """Keep widget's wraplength tracking the real width of source.

    reserve is fixed padding to hold back; siblings are widgets sharing the row,
    whose measured width is held back too -- so the reserve follows the actual
    font, theme and DPI rather than a number guessed at one screen size.
    """
    def update(_event=None):
        try:
            width = source.winfo_width()
            if width <= 1:
                # Not laid out yet. Leave the current wrap alone rather than wrap
                # to a guess: the first <Configure> after the window is drawn is
                # what sets the real value, and it always arrives.
                return
            for sibling in siblings:
                width -= max(sibling.winfo_width(), sibling.winfo_reqwidth())
            width = max(minimum, width - reserve)
            current = int(widget.cget("wraplength") or 0)
            if abs(current - width) >= WRAP_STEP:
                widget.configure(wraplength=width)
        except (tk.TclError, ValueError):
            pass

    try:
        source.bind("<Configure>", update, add="+")
        widget.after_idle(update)
    except (tk.TclError, AttributeError):
        # Nothing to track (a card built outside a real window, say). The label
        # keeps its default wrap; text that does not resize is not worth an
        # exception out of a widget constructor.
        pass
    return update

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

A PS2 cabled straight into the PC has no router on the wire, so nothing hands the console an IP address and every network app fails the same way. Ticking "PS2 is plugged directly into this PC" fixes that: PS2 Servers gives the chosen network port a working address (one administrator prompt), and runs a small DHCP helper that answers only on that port, so the console configures itself. On Windows it also allows DHCP through the firewall and sets the port to a fixed address; on Linux and macOS it instead adds a temporary address to the port for the session and leaves the port's existing configuration untouched.

You normally do not configure anything on the PS2. On Windows, if the console already has a leftover static IP from an earlier setup, the helper notices the device on the wire and quietly moves THIS PC to a compatible address so the two coexist — including onto the console's own subnet if it is on a different one. The console finds the server by broadcasting, so it usually needs no changes. Only when no shared address can be found does the launcher fall back to asking you to set the PS2 to DHCP or a different static IP. (This automatic coexistence is Windows-only for now; on Linux and macOS a console with a leftover static IP may need setting to DHCP or a matching static address.)

The helper is deliberately paranoid, because a DHCP server answering on a real network could disrupt every device on it. It binds to the direct-link port alone, refuses to run if that port reaches a router or holds a DHCP lease, hands out exactly one address to one console, and stops itself if several devices start asking. Unticking the box stops the helper; on Windows it returns the port to automatic (DHCP) and "Remove PS2 Servers firewall rules" also undoes it, while on Linux and macOS it simply removes the temporary address it added and leaves your existing configuration as it was. Direct link mode works on Windows, and is experimental on Linux and macOS (there it runs the helper as administrator to configure the port and set it back when it stops; if anything looks wrong, untick it and send the TERMINAL output).

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
    "smbv2": "SMBv2",
    "smbv3": "SMBv3",
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


def tab_text(label):
    """The padded tab text, matching StyledNotebook.add's own wrapping.

    Both the initial nb.add() (via StyledNotebook) and the later nb.tab()
    refreshes must produce identical text, or the label shifts on the first
    status change. Making this the single source of truth removes that coupling
    -- and keeps them consistent even where StyledNotebook is not in play.
    """
    return "  {}  ".format(label.strip())


def opl_hint(key, ip, values):
    if key in ("smbv1", "smbv2", "smbv3"):
        port = "445" if values.get("take_445") else str(values.get("port") or 1025)
        # Read back what this card is actually running, not what the defaults
        # were: a hint that says 'games' next to a share called 'roms' sends the
        # user to check their network when the name was the whole problem.
        share = (values.get("share_name") or "games").strip() or "games"
        user = (values.get("username") or "").strip()
        password = (values.get("password") or "") if user else ""
        if values.get("open_share") and not user:
            creds = "Anonymous / Guest (No Auth)"
        else:
            user_display = user or "guest"
            creds = "User '{}'  ·  Password {}".format(
                user_display, "as set" if password else "blank")
        return ("In OPL → Network:  IP {}  ·  Port {}  ·  Share '{}'  "
                "·  NetBIOS off  ·  {}".format(ip, port, share, creds))
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

    def _wrap_source(self):
        """The widget whose width the card's wrapping text follows.

        The notebook, because it is always mapped (see CARD_TEXT_RESERVE). The
        card itself is the fallback for anywhere a card is built outside one --
        including a bare card in a test, which has no app at all.
        """
        return getattr(getattr(self, "app", None), "nb", None) or self

    # -- widget construction ---------------------------------------------- #
    def _build(self):
        self.configure(padding=(12, 10, 12, 12))
        self.columnconfigure(1, weight=1)
        row = 0

        # A one-line recommendation badge so a beginner knows which server to
        # reach for (UDPFS recommended, UDPBD legacy) instead of guessing.
        if self.server.recommendation:
            colour = (COLOR_RUNNING if self.server.recommendation_kind == "good"
                      else COLOR_STOPPED)
            ttk.Label(self, text=self.server.recommendation,
                      foreground=colour, style="CardStatus.TLabel").grid(
                row=row, column=0, columnspan=3, sticky="w", padx=4, pady=(2, 0))
            row += 1

        # header: blurb + status + start/stop
        blurb = ttk.Label(self, text=self.server.blurb, style="CardMuted.TLabel")
        blurb.grid(row=row, column=0, columnspan=3, sticky="w", padx=4, pady=(2, 6))
        bind_wraplength(blurb, self._wrap_source(), reserve=CARD_TEXT_RESERVE)
        row += 1

        self.status = ttk.Label(self, text=DOT_RUNNING + " Stopped",
                                foreground=COLOR_STOPPED,
                                style="CardStatus.TLabel")
        self.status.grid(row=row, column=0, sticky="w", padx=4, pady=(0, 4))
        self.toggle_btn = ttk.Button(self, text="Start", width=10,
                                     command=self.on_toggle, style="Accent.TButton")
        self.toggle_btn.grid(row=row, column=2, sticky="e", padx=4, pady=(0, 4))
        if not self.server.is_available():
            self.status.config(text="n/a on this OS", foreground=COLOR_ERROR)
            self.toggle_btn.config(state="disabled")
        row += 1

        # primary fields, then advanced fields (hidden behind a toggle).
        # windows_only fields (e.g. take-445, which pauses LanmanServer) are
        # dropped off Windows: their mechanism cannot work there, and showing a
        # dead control just invites a confusing permission error.
        shown = [f for f in self.server.fields
                 if windows_setup.is_windows() or not f.windows_only]
        primary = [f for f in shown if not f.advanced]
        advanced = [f for f in shown if f.advanced]
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

        self.hint = ttk.Label(self, text="", style="CardHint.TLabel")
        self.hint.grid(row=row, column=0, columnspan=3, sticky="w",
                       padx=4, pady=(4, 0))
        bind_wraplength(self.hint, self._wrap_source(), reserve=CARD_TEXT_RESERVE)

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
        img = self.app._tab_dot_image(running)
        try:
            if img is not None:
                title = TAB_TITLES.get(self.server.key, self.server.label)
                nb.tab(tab, text=tab_text(title), image=img, compound="left")
            else:  # image generation unavailable: fall back to the glyph
                nb.tab(tab, text=tab_text(tab_label(
                    self.server.key, running, fallback=self.server.label)))
        except tk.TclError:
            pass

    def _add_help(self, parent, text, row, column, indent, reserve):
        # Own row, so help never overlaps the entry or Browse button. columnspan
        # reaches the card's last column (2) from wherever it starts.
        label = ttk.Label(parent, text=text, style="CardHelp.TLabel", font=("", 8))
        label.grid(row=row, column=column, columnspan=3 - column, sticky="w",
                   padx=(indent, 4), pady=(0, 4))
        bind_wraplength(label, self._wrap_source(), reserve=reserve)
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
                row = self._add_help(parent, f.help, row, 0, CHECK_HELP_INDENT,
                                     CHECK_HELP_RESERVE)
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
        elif f.kind == "choice":
            # readonly, so the box can be opened and read but not typed into:
            # an editable combobox would let a typo reach the server as an
            # unknown value, and the server exits rather than guessing.
            labels = [label for label, _ in f.choices]
            var = tk.StringVar(value=str(f.default or (labels[0] if labels else "")))
            ttk.Combobox(parent, textvariable=var, values=labels,
                         state="readonly", width=18).grid(
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
            row = self._add_help(parent, f.help, row, 1, 6, HELP_RESERVE)
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
        # Migrate first. This loop only restores keys that have a widget, so a
        # setting whose control was retired is dropped here and can never come
        # back on save either -- values() walks the same dict. Honouring a
        # retired key further downstream cannot help, because nothing upstream
        # can still deliver it.
        saved = servers.migrate_saved(self.server.key, saved)
        for key, var in self.vars.items():
            if key in saved:
                var.set(saved[key])

    # -- lifecycle --------------------------------------------------------- #
    def on_toggle(self):
        if self.app.is_running(self.server.key):
            self.app.stop_server(self.server.key)
        else:
            self.app.start_server(self.server.key)

    # What the server says about itself, when it says anything. A build that
    # does not implement the status protocol reports nothing, and the label
    # falls back to the process-derived "Running" it has always shown.
    _STATE_LABELS = {"ready": "Running", "busy": "Transferring",
                     "degraded": "Degraded", "starting": "Starting",
                     "stopping": "Stopping"}

    def _reported_state(self):
        status = self.app.status_poller.status(self.server.key)
        if not status:
            return None
        name = status.get("state_name")
        return name if name in self._STATE_LABELS else None

    def _running_label(self):
        return self._STATE_LABELS.get(self._reported_state(), "Running")

    def _running_colour(self):
        # Degraded is the one that must not look healthy: the process is alive,
        # so the old display called it Running and sent people hunting for a
        # network fault when the share had simply been unplugged.
        return COLOR_ERROR if self._reported_state() == "degraded" else COLOR_RUNNING

    def refresh_status(self, running, error=False):
        if error or not running:
            self._active_values = None
        self._refresh_tab_dot(running and not error)
        if error:
            self.status.config(text=DOT_RUNNING + " Error", foreground=COLOR_ERROR)
        elif running:
            # "Running" is what the process check can say. Where the server
            # itself answers, say what it actually reports instead -- a share
            # that has been unplugged is Degraded, not Running, and a transfer
            # in flight is worth seeing before reaching for Stop.
            self.status.config(text=DOT_RUNNING + " " + self._running_label(),
                               foreground=self._running_colour())
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
        # Truthful per-server state, polled off the Tk thread. The dot used
        # to mean only "the child process is alive", which stays green when
        # the share has been unplugged and says nothing about a transfer in
        # flight. Servers that do not implement the protocol report unknown
        # and fall back to the process check, so nothing regresses.
        self.status_poller = status_client.Poller()
        self.status_poller.start()
        # Last state painted per card, so the status line is only rebuilt when
        # the answer actually changes rather than on every 600 ms tick.
        self._last_reported = {}
        self.out_queue = queue.Queue()
        self.logs = {}
        self.saved = config.load()
        self._firewall_ok = set()   # _restore fills it; never read before it exists
        self._direct_proc = None            # the DHCP helper child, when running
        self._direct_expected = False       # we started it and expect it alive
        self._direct_busy = False           # an enable/disable flow is mid-flight
        self._direct_rehomes = 0            # times we moved to coexist this run
        # Set once _shutdown_app starts destroying the root, so the self-
        # rescheduling loops and worker callbacks stop touching a dead Tk.
        self._shutting_down = False
        self._tray = None
        self._tray_option_widgets = []
        # Default close/minimize-to-tray ON only where the tray icon appears
        # reliably and synchronously (Windows). On Linux the tray is opt-in even
        # when available: the icon shows on most desktops (XFCE/Cinnamon/MATE/
        # KDE) but we don't want a first-run user's window-close to silently hide
        # the window before they've seen the icon work. A saved preference still
        # wins, so a Linux user who turns it on keeps it.
        _tray_default = tray.AVAILABLE and windows_setup.is_windows()
        self.close_to_tray_var = tk.BooleanVar(
            value=self._saved_bool("close_to_tray", _tray_default))
        self.minimize_to_tray_var = tk.BooleanVar(
            value=self._saved_bool("minimize_to_tray", _tray_default))

        root.title("PS2 Servers " + APP_VERSION_LABEL)
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
        # Once the window is drawn, so the tab strip has a width to measure.
        self.root.after(200, self._apply_tab_minimum_width)
        self.root.after(600, self._poll_status)
        if self.saved.get("pending_firewall_allow"):
            self.root.after(350, self._allow_pending)
        elif self.saved.get("pending_cleanup"):
            self.root.after(350, self._cleanup_pending)
        elif self.saved.get("pending_direct_link"):
            self.root.after(350, self._direct_link_pending)
        elif self.saved.get("pending_direct_link_off"):
            self.root.after(350, self._direct_link_off_pending)
        elif self.saved.get("pending_direct_link_restore"):
            self.root.after(350, self._direct_link_recovery_pending)
        elif self.saved.get("pending_start"):
            self.root.after(350, self._start_pending)
        elif (self.saved.get("direct_link") or {}).get("enabled"):
            if windows_setup.is_windows():
                # Re-arm the DHCP helper for an already-configured direct link
                # (the static address persists across reboots on Windows).
                # Skipped while a pending flow runs: those flows own the state.
                self.root.after(600, self._direct_link_startup)
            else:
                # Unix direct link is session-only: the additive address does
                # not survive a restart, and re-arming would re-prompt for a
                # password. Show it as off; the user re-ticks to set it up.
                self.root.after(600, self._direct_link_reset_stale_unix)

    def _configure_window(self):
        screen_width = max(640, self.root.winfo_screenwidth())
        screen_height = max(480, self.root.winfo_screenheight())
        width = min(APP_WINDOW_WIDTH, max(APP_MIN_WIDTH, screen_width - 96))
        height = min(APP_INITIAL_HEIGHT, max(APP_MIN_HEIGHT, screen_height - 80))
        x = max(0, int((screen_width - width) / 2))
        y = max(0, int((screen_height - height) / 3))
        self.root.geometry("{}x{}+{}+{}".format(width, height, x, y))
        # Free in both directions, with no maxsize: the content reflows to the
        # width it is given, so there is nothing left for a width lock to protect.
        # The minimum is what the button rows and field columns need, capped to the
        # screen so a small display can still show the whole window.
        self._min_height = min(APP_MIN_HEIGHT, screen_height - 40)
        self.root.minsize(min(APP_MIN_WIDTH, screen_width - 40), self._min_height)
        self.root.resizable(True, True)

    def _tab_strip_width(self):
        """How much width the tab row actually uses, measured rather than guessed.

        ttk offers no query for it, so probe the row: identify() names an element
        while x is over a tab and returns nothing past the last one. Binary search,
        so it costs about ten calls instead of one per pixel. Returns 0 if the
        notebook is not laid out or the probe finds no tab -- callers keep their
        default rather than act on a measurement that did not happen.
        """
        nb = getattr(self, "nb", None)
        if nb is None:
            return 0
        try:
            width = nb.winfo_width()
            if width <= 1:
                return 0
            # Start inside the first tab, past the notebook's own left tabmargin.
            probe = 20
            row = next((y for y in (10, 14, 18, 22, 6) if nb.identify(probe, y)),
                       None)
            if row is None:
                return 0
            if nb.identify(width - 2, row):
                return width              # already filling the notebook, or clipped
            low, high = probe, width - 2  # low is over a tab; high is past the last
            while high - low > 2:
                mid = (low + high) // 2
                if nb.identify(mid, row):
                    low = mid
                else:
                    high = mid
            return high
        except tk.TclError:
            return 0

    def _apply_tab_minimum_width(self):
        """Never let the window shrink past its own tab strip.

        The tabs are the one thing on the page that cannot reflow -- ttk neither
        wraps nor scrolls them, so a window narrower than the strip simply hides
        the last tab (ABOUT). Measuring beats a constant here: the strip's width
        follows the theme's tab padding, the user's font and DPI, and how many
        servers this build ships, none of which are known when writing a number.
        """
        strip = self._tab_strip_width()
        if strip <= 0:
            return
        # Everything the window spends before the notebook starts -- page padding,
        # window border, and the page scrollbar when it is out -- measured rather
        # than added up from the padx values, which would miss the scrollbar and
        # go stale the moment any of them changes.
        chrome = max(0, self.root.winfo_width() - self.nb.winfo_width())
        if not self._scrollbar.winfo_ismapped():
            chrome += self._scrollbar.winfo_reqwidth()
        screen_width = max(640, self.root.winfo_screenwidth())
        needed = strip + TAB_STRIP_TAIL + chrome
        minimum = min(max(APP_MIN_WIDTH, needed), screen_width - 40)
        try:
            self.root.minsize(minimum, self._min_height)
            if self.root.winfo_width() < minimum:
                self.root.geometry("{}x{}".format(minimum,
                                                  self.root.winfo_height()))
        except tk.TclError:
            pass

    def _build_scroll_body(self):
        bg = self.root.cget("background")
        shell = ttk.Frame(self.root)
        shell.pack(fill="both", expand=True)

        canvas = tk.Canvas(shell, highlightthickness=0, bd=0, background=bg)
        scrollbar = ttk.Scrollbar(shell, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)

        body = ttk.Frame(canvas)
        window = canvas.create_window((0, 0), window=body, anchor="nw")

        def refresh_scroll_region(event=None):
            try:
                width = max(1, canvas.winfo_width())
                view = max(1, canvas.winfo_height())
                content = max(1, body.winfo_reqheight())
                # Hand the body the full viewport, not just what it asked for, so
                # spare vertical space goes to the notebook (a taller TERMINAL)
                # instead of sitting as dead grey under the page.
                height = max(content, view)
                if (str(canvas.itemcget(window, "width")) != str(width)
                        or str(canvas.itemcget(window, "height")) != str(height)):
                    canvas.itemconfigure(window, width=width, height=height)
                canvas.configure(scrollregion=(0, 0, width, height))
                self._sync_body_scrollbar(content, view)
            except tk.TclError:
                pass

        body.bind("<Configure>", refresh_scroll_region)
        canvas.bind("<Configure>", refresh_scroll_region)
        self._scroll_shell = shell
        self._scroll_canvas = canvas
        self._scrollbar = scrollbar
        self._scroll_window = window
        self._bind_body_mousewheel(canvas)
        self._refresh_scroll_body = refresh_scroll_region
        return body

    def _sync_body_scrollbar(self, content_height, view_height):
        """Show the page scrollbar only when the page genuinely overflows.

        A permanent bar on a page that fits is noise, and it eats width the text
        could have used. Hiding it can only ever give the content MORE width, and
        more width never makes the page taller, so this settles in one pass
        instead of flickering between the two states.
        """
        bar, canvas = self._scrollbar, self._scroll_canvas
        needed = content_height > view_height + 2
        try:
            shown = bool(bar.winfo_ismapped())
            if needed and not shown:
                # before=canvas: the canvas is packed fill+expand and would
                # otherwise swallow the whole cavity, leaving the bar zero width.
                bar.pack(side="right", fill="y", before=canvas)
            elif not needed and shown:
                bar.pack_forget()
                canvas.yview_moveto(0)   # nothing left to scroll back with
        except tk.TclError:
            pass

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
        header.columnconfigure(4, weight=1)
        ttk.Label(header, text="LAN IP", font=("", 10, "bold"),
                  style="TopStripTitle.TLabel").grid(row=0, column=0, sticky="w")
        # Always-visible version, so a tester can read it off the screen without
        # opening About. Right-aligned in the header's stretchy column.
        ttk.Label(header, text="PS2 Servers " + APP_VERSION_LABEL,
                  style="TopStripHint.TLabel").grid(row=0, column=5, sticky="e",
                                                    padx=(12, 0))
        self.ip_var = tk.StringVar(value=netinfo.best_lan_ip())
        # Editable, not readonly: detection leans on getaddrinfo(gethostname()),
        # which misses or mis-ranks addresses on hosts with VPN/Hyper-V/WSL/Docker
        # adapters or a second NIC. When the right address is not in the list the
        # user has to be able to type it. This value only feeds the OPL hint text
        # -- what a server binds to is its own Bind address field.
        self.ip_combo = ttk.Combobox(header, textvariable=self.ip_var, width=18,
                                     values=netinfo.ip_choices(), state="normal")
        self.ip_combo.grid(row=0, column=1, sticky="w", padx=(10, 6))
        self.ip_combo.bind("<<ComboboxSelected>>", self._on_ip_combobox_select)
        # A typed address applies as you type: the OPL hint on any running card
        # follows it, and it persists without waiting for something else to save.
        # Without this, typing gave no feedback at all until the next start/stop,
        # which read as "it did not take".
        self.ip_var.trace_add("write", self._on_ip_edited)
        ttk.Button(header, text="Refresh", command=self._refresh_ips).grid(
            row=0, column=2, sticky="w")
        ttk.Button(header, text="What's my IP?", command=self._show_whats_my_ip).grid(
            row=0, column=3, sticky="w", padx=(4, 0))
        # Its own full-width row rather than a fifth column on the controls row:
        # four controls plus a paragraph cannot share one row at every window
        # width, and a row of its own reflows to any width without squeezing them.
        ip_hint = ttk.Label(header, text="Enter this in OPL where it asks for the "
                            "PC/server IP. Pick from the list, or type your own if "
                            "the right address isn't shown -- it saves as you type.",
                            style="TopStripHint.TLabel")
        ip_hint.grid(row=1, column=0, columnspan=5, sticky="w", pady=(6, 0))
        bind_wraplength(ip_hint, header, reserve=24)

        # direct PS2-to-PC link: adapter setup + DHCP helper behind one checkbox.
        # Available on every desktop OS; the per-OS network plumbing differs, and
        # the non-Windows paths are experimental (unverified on real hardware).
        if _direct_link_supported():
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
                style="TopStripHint.TLabel")
            self._direct_status.grid(row=0, column=1, sticky="w", padx=(12, 0))
            # Reserve the checkbox's measured width, so the status wraps against
            # whatever the tick box actually takes at this font and DPI.
            bind_wraplength(self._direct_status, direct, reserve=36,
                            siblings=(self._direct_check,))
            if _direct_link_experimental():
                osname = "macOS" if platform.system() == "Darwin" else "Linux"
                experimental = ttk.Label(
                    direct, style="TopStripHint.TLabel",
                    text="Experimental on {}: it sets up the port and needs your "
                         "password. If anything looks off, untick it and send the "
                         "TERMINAL output.".format(osname))
                experimental.grid(row=1, column=0, columnspan=2, sticky="w",
                                  pady=(4, 0))
                bind_wraplength(experimental, direct, reserve=24)
        else:
            self.direct_link_var = None
            self._direct_check = None
            self._direct_status = None

        # main tabs: one server per tab, plus a shared terminal tab. expand=True so
        # a taller window grows the tab body (mainly the TERMINAL log) instead of
        # leaving empty page below it.
        self.nb = ttk.Notebook(parent)
        self.nb.pack(fill="both", expand=True, padx=16, pady=(0, 12))
        self.server_tabs = {}
        self._init_tab_dots()

        for server in REGISTRY.values():
            tab = ttk.Frame(self.nb)
            tab.columnconfigure(0, weight=1)
            card = ServerCard(tab, self, server)
            card.grid(row=0, column=0, sticky="ew", padx=10, pady=10)
            img = self._tab_dot_image(running=False)
            if img is not None:
                title = TAB_TITLES.get(server.key, server.label)
                self.nb.add(tab, text=tab_text(title), image=img,
                            compound="left")
            else:
                self.nb.add(tab, text=tab_text(tab_label(
                    server.key, running=False, fallback=server.label)))
            self.server_tabs[server.key] = tab
            self.cards[server.key] = card

        self.terminal_tab = ttk.Frame(self.nb)
        self.terminal_tab.rowconfigure(0, weight=1)
        self.terminal_tab.columnconfigure(0, weight=1)
        _p = theme.PALETTE
        # wrap="word": a log line is read, not scrolled to. With no wrap, the long
        # lines servers print (paths, commands, firewall rules) ran off the right
        # edge with no horizontal bar to chase them, so the end of the line -- the
        # part that says what went wrong -- was simply unreachable. Wrapping is the
        # only setting here that can never hide text. height is a floor, not a size:
        # the tab expands, so the log takes whatever the window has spare.
        self.terminal = tk.Text(self.terminal_tab, height=10, wrap="word",
                                state="disabled", background=_p["entry"],
                                foreground=_p["text"], insertbackground=_p["accent"],
                                selectbackground=_p["panel3"], selectforeground=_p["text"],
                                borderwidth=0, highlightthickness=0)
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
        # These two manage named WINDOWS Firewall rules. On other OSes there is
        # nothing for them to do, so rather than show them permanently greyed --
        # which just makes a Linux user wonder what they're missing -- omit them
        # entirely (the admin panel is hidden the same way). Linux firewall
        # guidance is instead printed to the terminal when a server starts.
        if windows_setup.is_windows():
            allow = ttk.Button(footer, text="Allow through firewall",
                               command=self.allow_windows_setup)
            allow.grid(row=0, column=0, sticky="w")
            remove = ttk.Button(footer, text="Remove PS2 Servers firewall rules",
                                command=self.remove_windows_setup)
            remove.grid(row=0, column=1, sticky="w", padx=(8, 0))
        ttk.Button(footer, text="Stop all", command=self.stop_all_confirmed).grid(
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
            title_box = ttk.Frame(brand)
            title_box.pack(side="left", padx=(10, 0))
            ttk.Label(title_box, text="PS2 Servers", font=("", 14, "bold")).pack(
                anchor="w")
            ttk.Label(title_box, text=APP_VERSION_LABEL,
                      style="Muted.TLabel").pack(anchor="w")
            row += 1
        else:
            # No logo asset (e.g. source runs without theme assets): still show
            # the version so it is never hidden behind an optional image.
            ttk.Label(about, text="PS2 Servers " + APP_VERSION_LABEL,
                      font=("", 12, "bold")).grid(row=row, column=0, sticky="w",
                                                  padx=8, pady=(8, 0))
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

        # The tray options only mean anything with a tray, which is Windows-only.
        # Omit the frame entirely elsewhere rather than show two dead checkboxes
        # (on Linux closing the window quits, with a confirm if servers are up).
        # Gate on tray.AVAILABLE, not self._tray: the tray instance is created
        # AFTER _build() runs, so self._tray is still None here on every OS.
        if tray.AVAILABLE:
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
        _p = theme.PALETTE
        # height is a floor: the tab expands, so About uses the window it is given.
        text = tk.Text(text_frame, wrap="word", height=10, state="normal",
                       background=_p["panel"], foreground=_p["text"],
                       insertbackground=_p["accent"], selectbackground=_p["panel3"],
                       selectforeground=_p["text"], borderwidth=0,
                       highlightthickness=0, padx=12, pady=10)
        scroll = ttk.Scrollbar(text_frame, orient="vertical", command=text.yview)
        text.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        # Out only while there is something to scroll. Unlike the TERMINAL -- where
        # the bar is a standing sign that the log runs past the window -- About is a
        # page of prose, and on a tall window the whole thing fits.
        def sync(first, last):
            scroll.set(first, last)
            if float(first) <= 0.0 and float(last) >= 1.0:
                scroll.grid_remove()
            else:
                scroll.grid()

        text.configure(yscrollcommand=sync)
        text.insert("1.0", ABOUT_TEXT)
        text.config(state="disabled")

        self.nb.add(about, text="ABOUT")

    def _open_url(self, url):
        try:
            webbrowser.open_new_tab(url)
        except Exception as e:
            messagebox.showerror("Cannot open link", str(e))

    # -- per-tab running/stopped dots ------------------------------------- #
    def _init_tab_dots(self):
        """Build the two shared tab-status images (running / stopped), once.

        Sized from the tab font so the dot scales with the label at any DPI, and
        coloured to match the in-card status label. Any failure leaves
        self._tab_dots None, and the tabs fall back to the text glyph -- a dot is
        decoration and must never be able to take the window down.
        """
        self._tab_dots = None
        try:
            linespace = tkfont.Font(root=self.root, font=("", 10, "bold")).metrics(
                "linespace")
            diameter = max(9, min(48, round(linespace * 0.6)))
            online = tk.PhotoImage(data=status_dot.dot_png_base64(
                diameter, COLOR_RUNNING, filled=True))
            offline = tk.PhotoImage(data=status_dot.dot_png_base64(
                diameter, COLOR_STOPPED, filled=False))
            self._tab_dots = (online, offline)  # kept referenced so Tk won't GC them
        except Exception:
            self._tab_dots = None

    def _tab_dot_image(self, running):
        dots = getattr(self, "_tab_dots", None)
        if not dots:
            return None
        return dots[0] if running else dots[1]

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

    def _on_ip_combobox_select(self, event=None):
        if self.ip_var.get() == "Custom IP...":
            self.ip_var.set("")
            self.ip_combo.focus_set()

    def _show_whats_my_ip(self):
        self.nb.select(self.terminal_tab)

        def worker():
            info = netinfo.detailed_ip_info()
            self.root.after(0, lambda: self._append_log("setup", f"{info}\n"))

        threading.Thread(target=worker, daemon=True).start()

    def _refresh_ips(self):
        self.ip_combo.config(values=netinfo.ip_choices())
        current = self.ip_var.get()
        if not current or current in (netinfo.all_ipv4() + ["Custom IP..."]):
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

        # Windows REPLACES the port's config, so its current networks are about
        # to disappear and are excluded. Unix ADDS our address alongside, so the
        # port keeps its existing networks -- they must still count as taken, or
        # we could pick a direct-link subnet that collides with one still live
        # on that very port.
        exclude = adapter["id"] if windows_setup.is_windows() else None
        taken = directlink.taken_networks(enumerated, exclude_id=exclude)
        server_ip, client_ip = directlink.choose_subnet(taken)
        if not server_ip:
            self._direct_link_fail(
                "Could not find a private network range that does not "
                "collide with one this PC already uses.")
            return

        current = [i["ip"] for i in adapter.get("ipv4", [])
                   if i["ip"] and not i["ip"].startswith("169.254.")]
        note = ""
        if current and windows_setup.is_windows():
            note = ("\n\nIts current address ({}) will be replaced; unticking "
                    "the box returns the port to automatic (DHCP), not to "
                    "that address.".format(", ".join(current)))
        elif current:
            # Unix keeps the existing address and adds ours alongside it, then
            # removes just ours again when the helper stops.
            note = ("\n\nIts current address ({}) is kept; ours is added "
                    "alongside for the session and removed again when you "
                    "untick the box.".format(", ".join(current)))
        firewall_line = ("• allow DHCP (UDP 67) through the firewall\n"
                         if windows_setup.is_windows() else "")
        prompt_line = ("This needs one administrator prompt."
                       if windows_setup.is_windows()
                       else "You'll be asked for your password once.")
        if not messagebox.askyesno(
                "Set up the direct PS2 link?",
                "Use '{name}' ({desc}) as the direct PS2 link?\n\n"
                "PS2 Servers will:\n"
                "• give this PC the fixed address {server} on that port\n"
                "{fw}"
                "• run a small DHCP helper that answers ONLY on that port, "
                "handing the PS2 {client}\n\n"
                "{prompt} The helper refuses to run if that port turns out to "
                "be a real network (router or DHCP server present). Untick the "
                "box to undo everything.{note}".format(
                    name=adapter["name"], desc=adapter["desc"],
                    server=server_ip, client=client_ip, fw=firewall_line,
                    prompt=prompt_line, note=note)):
            self._set_direct_checkbox(False)
            self._set_direct_status(self._DIRECT_STATUS_OFF)
            return

        self.saved["direct_link"] = {
            "enabled": False,
            "adapter": adapter["name"],
            "if_index": adapter["if_index"],
            "id": adapter["id"],
            "server_ip": server_ip,
            "client_ip": client_ip,
            "prefix": directlink.PREFIX_LENGTH,
        }

        if not windows_setup.is_windows():
            self._direct_link_enable_unix()
            return

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
                self._destroy_root()
            else:
                self.saved.pop("pending_direct_link", None)
                self._save()
                self._direct_link_fail("Could not restart as administrator.")
            return

        self._direct_link_apply_async()

    # -- direct link: Unix (Linux/macOS, experimental) -------------------- #
    def _direct_stop_file(self):
        """Path the launcher touches to ask the root responder to stop.

        In the user's config dir (root can read it), so teardown works even
        when the launcher cannot signal a root-owned process directly.
        """
        return os.path.join(config.config_dir(), "directlink.stop")

    def _direct_link_enable_unix(self):
        """Start the responder elevated; it configures the port and, on exit,
        always removes the address again. Session-only: not re-armed on the
        next launch (that would re-prompt for a password)."""
        if not elevate.unix_privileged_tool():
            need = ("Install 'pkexec' (part of polkit) to let PS2 Servers set "
                    "this up for you." if platform.system() == "Linux"
                    else "This needs macOS's built-in 'osascript', which was "
                    "not found.")
            self._direct_link_fail(
                "The direct link needs to run one command as administrator, but "
                "no graphical password prompt is available.\n\n" + need)
            return
        cfg = self.saved.get("direct_link") or {}
        self._set_direct_checkbox(True, busy=True)
        self._set_direct_status(
            "Setting up '{}' — you'll be asked for your password…".format(
                cfg.get("adapter")))
        self.nb.select(self.terminal_tab)
        if not self._start_direct_responder():
            return  # _start_direct_responder already reported the failure
        cfg["enabled"] = True
        self.saved["direct_link"] = cfg
        self.ip_var.set(cfg["server_ip"])
        self._save()
        self._set_direct_checkbox(True)
        self._set_direct_status(self._direct_ready_status(cfg))

    def _direct_link_reset_stale_unix(self):
        """A Unix direct link marked enabled from a previous session is stale
        (session-only). Clear it so the checkbox honestly reads off."""
        cfg = self.saved.get("direct_link") or {}
        if cfg.get("enabled"):
            cfg["enabled"] = False
            self.saved["direct_link"] = cfg
            self._save()
        self._set_direct_checkbox(False)
        self._set_direct_status(self._DIRECT_STATUS_OFF)

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
                    cfg["if_index"], cfg["server_ip"], cfg["client_ip"],
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
        self._direct_rehomes = 0  # fresh coexist budget for this enable
        cfg = self.saved.get("direct_link") or {}
        if not self._start_direct_responder():
            # The adapter and firewall were already configured. Keep recovery
            # state until an asynchronous DHCP restore succeeds, so a failed
            # cleanup remains retryable instead of stranding a static port.
            self._rollback_failed_direct_responder(cfg)
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
                "--adapter", cfg.get("adapter", "")]
        if windows_setup.is_windows():
            # Windows configured the NIC already; the helper binds port 67
            # unprivileged and just serves.
            args += ["--if-index", str(cfg.get("if_index", 0))]
            command = serve_command("directlink", args)
        else:
            # Unix: port 67 is privileged, so the helper runs as root and
            # configures the port itself. Clear any stale stop-file first, then
            # wrap the command in the OS graphical elevation (pkexec/osascript).
            stop_file = self._direct_stop_file()
            try:
                os.remove(stop_file)
            except OSError:
                pass
            args += ["--adapter-id", str(cfg.get("id") or cfg.get("adapter", "")),
                     "--configure-ip", "--stop-file", stop_file,
                     # Watch THIS launcher's pid: the pkexec/osascript wrapper
                     # sits between us and the root helper, so the helper cannot
                     # notice us dying via its parent -- give it our pid to poll.
                     "--watch-pid", str(os.getpid())]
            command = elevate.unix_privileged_command(
                serve_command("directlink", args))
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
        # Unix: the helper runs as root, which we may not be able to signal, so
        # ask it to stop via the stop-file (it restores the port on exit). Do
        # this before terminating the process so a clean self-restore can win.
        if not windows_setup.is_windows():
            try:
                open(self._direct_stop_file(), "w").close()
            except OSError:
                pass
        if self._direct_proc is not None:
            if self._direct_proc.is_running():
                self._direct_proc.stop()
                self._append_log("directlink", "[launcher] DHCP helper stopped\n")
            self._direct_proc = None

    def _rollback_failed_direct_responder(self, cfg) -> None:
        cfg["enabled"] = False
        self.saved["direct_link"] = cfg
        self.saved["pending_direct_link_restore"] = True
        persisted = self._save()
        if not persisted:
            self._append_log(
                "directlink",
                "[launcher] WARNING: could not save the pending DHCP "
                "recovery; waiting for this restore attempt before exit\n")
        self._direct_link_restore_async(
            cfg, clear_saved=True, daemon=bool(persisted))

    def _direct_link_begin_disable(self):
        cfg = self.saved.get("direct_link") or {}
        if not cfg.get("enabled"):
            self._stop_direct_responder()
            self._set_direct_checkbox(False)
            self._set_direct_status(self._DIRECT_STATUS_OFF)
            return
        if not windows_setup.is_windows():
            # Unix: no separate restore step -- the root helper removes the
            # address itself when it stops. Just stop it.
            self._stop_direct_responder()
            cfg["enabled"] = False
            self.saved["direct_link"] = cfg
            self._save()
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
                self._destroy_root()
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

    def _direct_link_restore_async(self, cfg, clear_saved=False, daemon=True):
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
            self.root.after(
                0, lambda: self._direct_link_restored(output, clear_saved))

        # A non-daemon worker is used when the crash-recovery marker could not
        # be saved.  That keeps process shutdown from abandoning the only DHCP
        # restore attempt that can prevent a stranded static adapter.
        threading.Thread(target=worker, daemon=daemon).start()

    def _direct_link_restored(self, output, clear_saved=False):
        if output:
            self._append_log("directlink", "[setup] {}\n".format(
                output.replace("\n", "\n[setup] ")))
        recovery_was_pending = bool(
            self.saved.pop("pending_direct_link_restore", None))
        if clear_saved:
            self.saved.pop("direct_link", None)
        if clear_saved or recovery_was_pending:
            self._save()
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

    def _direct_link_recovery_pending(self):
        """Resume a DHCP restore that may have been interrupted by exit/crash."""
        cfg = self.saved.get("direct_link") or {}
        if not cfg.get("server_ip"):
            self.saved.pop("pending_direct_link_restore", None)
            self._save()
            return
        self.nb.select(self.terminal_tab)
        self._append_log(
            "directlink",
            "[launcher] resuming interrupted direct-link recovery\n")
        if not elevate.is_admin():
            if elevate.can_elevate() and elevate.relaunch_as_admin():
                self.stop_all()
                if self._tray:
                    self._tray.stop()
                self._destroy_root()
            else:
                self._set_direct_checkbox(False)
                self._set_direct_status(
                    "Direct-link recovery still needs administrator rights. "
                    "Restart PS2 Servers or use firewall cleanup to retry.")
            return
        self._direct_link_restore_async(cfg, clear_saved=True)

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
            self._append_log("directlink",
                             "[launcher] direct link not re-armed: {}\n".format(problem))
            self._rollback_failed_direct_responder(cfg)
            return
        if not self._start_direct_responder():
            self._rollback_failed_direct_responder(cfg)
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

        take_445 = key.startswith("smb") and bool(values.get("take_445"))
        raw_port = values.get("port")
        port_num = 0
        if raw_port:
            try:
                port_num = int(str(raw_port).strip(), 0)
            except (TypeError, ValueError):
                port_num = 0
        low_port_required = (0 < port_num < 1025)
        admin_required = setup_needed or take_445 or low_port_required
        if admin_required and not elevate.is_admin():
            self._set_card_busy(key, False, "Start")
            if not elevate.can_elevate():
                messagebox.showerror(
                    "Administrator required",
                    "Administrator privileges are required to configure network setup or bind ports below 1025.")
                return

            if low_port_required and not setup_needed and not take_445:
                message = (
                    "Binding ports below 1025 (such as port {}) requires administrator privileges.\n\n"
                    "Do you want to restart PS2 Servers as Administrator?".format(port_num)
                )
            else:
                summary = windows_setup.setup_summary(key, values)
                message = (
                    "PS2 Servers needs administrator rights to {}.\n\n"
                    "This will not enable Windows SMB1. It only manages PS2 Servers "
                    "firewall rules, and advanced port 445 mode only pauses Windows "
                    "file sharing while that server is running.\n\n"
                    "Restart the launcher as administrator now? Your settings are "
                    "saved and the server will continue automatically.".format(summary))
                if not take_445 and not low_port_required:
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
                    self._destroy_root()
                else:
                    messagebox.showerror(
                        "Elevation failed",
                        "Could not restart as administrator.")
            elif not take_445 and not low_port_required:
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
        if key.startswith("smb"):
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
        # Ask this server what state it is actually in, rather than inferring
        # it from the child process being alive. Loopback, because the launcher
        # is asking about a server it started itself.
        try:
            self.status_poller.set_target(key, "127.0.0.1", values.get("port") or 0)
        except Exception as e:
            self._append_log(key, "[launcher] status poller target setup warning: {}\n".format(e))
        card.refresh_status(True)
        card.toggle_btn.config(state="normal")
        self.nb.select(self.terminal_tab)
        if not windows_setup.is_windows():
            self._log_firewall_hint(key, values)

    def _log_firewall_hint(self, key, values):
        """Unix: after a server starts, tell the user how to open its ports if a
        firewall is blocking them. Windows manages allow-rules directly; on Unix
        we are not root, so guidance is the honest option -- and it turns a
        silent 'the PS2 sees nothing' into an actionable message. The systemctl
        active-probe runs OFF the Tk thread; the lines are appended back on it."""
        ports = windows_setup.server_ports(key, values)
        if not ports:
            return

        def worker():
            lines = posix_firewall.firewall_hint_lines(ports, probe_active=True)

            def emit():
                for line in lines:
                    self._append_log(key, "[firewall] {}\n".format(line))

            # The probe can take a few seconds; the window may be gone by now.
            if self._shutting_down:
                return
            try:
                self.root.after(0, emit)
            except tk.TclError:
                pass  # root destroyed between the check and the call

        threading.Thread(target=worker, daemon=True).start()

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
                self._destroy_root()
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
                self._destroy_root()
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
        self.saved.pop("pending_direct_link_restore", None)
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

    def confirm_stop_while_busy(self, keys):
        """Ask before stopping a server that says it is mid-transfer.

        Stopping a server while a console is writing truncates whatever it was
        writing, and a save is the one thing here a user cannot get back. The
        launcher could not previously tell, so it could not warn.

        Only a server that positively reports busy triggers this. A server that
        did not answer -- an older build, or one still starting -- is not busy,
        because a prompt that fired on every unreachable server would be
        dismissed reflexively long before it mattered.
        """
        busy = [k for k in keys
                if status_client.is_busy(self.status_poller.status(k))]
        if not busy:
            return True
        names = ", ".join(REGISTRY[k].label if k in REGISTRY else k for k in busy)
        return messagebox.askyesno(
            "Transfer in progress",
            "{} reports a transfer in progress.\n\n"
            "Stopping now will interrupt it. If a console is writing a save, "
            "that save will be incomplete.\n\nStop anyway?".format(names),
            icon="warning", default="no")

    def stop_server(self, key, confirm=True):
        proc = self.procs.get(key)
        if not proc:
            return
        if confirm and not self.confirm_stop_while_busy([key]):
            return
        if proc.is_running():
            proc.stop()
        self.status_poller.clear_target(key)
        self.cards[key]._active_values = None
        self.cards[key].refresh_status(False)
        self.cards[key].toggle_btn.config(state="normal")
        self._append_log(key, "[launcher] stopped\n")

    def stop_all(self):
        """Stop every server. Unconditional, and it must stay that way.

        Every caller but the footer button is a teardown or an
        elevate-and-relaunch that frees ports before the new instance binds
        them. A prompt in here would let a "No" skip the stopping while the
        caller carried on regardless -- quitting anyway and orphaning the
        children, or relaunching as admin into ports that were never released.
        The asking belongs at the points where a person actually chose to stop
        something: stop_all_confirmed, stop_server, and _confirm_app_shutdown.
        """
        for key in list(self.procs):
            self.stop_server(key, confirm=False)
        self._stop_direct_responder()

    def stop_all_confirmed(self):
        """The footer's Stop all: the one caller that is a person deciding."""
        if not self.confirm_stop_while_busy(list(self.procs)):
            return
        self.stop_all()

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
        if not self._shutting_down:
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
                self.status_poller.clear_target(key)
                self.cards[key].refresh_status(False)
                self.cards[key].toggle_btn.config(state="normal")
                self._append_log(key, "[launcher] server exited (code {})\n".format(
                    proc.returncode))
            elif running:
                # Repaint from the poller's latest answer. Cheap: the socket
                # work happened on the poller thread, and this only reads the
                # result it left behind.
                reported = self.cards[key]._reported_state()
                if reported != self._last_reported.get(key):
                    self._last_reported[key] = reported
                    self.cards[key].refresh_status(True)
        if (self._direct_expected and self._direct_proc is not None
                and not self._direct_proc.is_running()):
            code = self._direct_proc.returncode
            plan = self._parse_rehome(self._direct_proc.lines)
            self._direct_expected = False
            self._append_log("directlink",
                             "[launcher] DHCP helper exited (code {})\n".format(code))
            if code == 5 and plan and windows_setup.is_windows():
                # Auto-coexist re-home is Windows-only: it re-configures the NIC
                # via PowerShell (apply_adapter_config). The Unix helper never
                # emits a REHOME (neighbour discovery is Windows-only), but guard
                # the dispatch so a stray code 5 there can't hit the Windows path.
                self._direct_link_rehome(plan)
            elif code == 3:  # a safety refusal; the helper said why in the log
                self._set_direct_status(
                    "The DHCP helper stopped itself for safety — see the "
                    "TERMINAL tab. Untick and tick the box to retry.")
                self._finish_direct_exit()
            else:
                self._set_direct_status(
                    "The DHCP helper stopped (code {}) — see the TERMINAL "
                    "tab. Untick and tick the box to retry.".format(code))
                self._finish_direct_exit()
        if not self._shutting_down:
            self.root.after(600, self._poll_status)

    def _finish_direct_exit(self):
        cfg = self.saved.get("direct_link") or {}
        if not cfg.get("server_ip"):
            return
        if windows_setup.is_windows():
            # Windows configured the NIC separately, so a helper exit leaves a
            # static port that must be returned to DHCP. _rollback keeps the
            # pending_direct_link_restore marker set until that restore is
            # confirmed, so a crash mid-cleanup still recovers on the next launch.
            self._rollback_failed_direct_responder(cfg)
            return
        # Unix: the root helper removes its additive address in a finally on a
        # clean exit, but a forced kill (SIGKILL) can bypass that and strand the
        # address for the rest of the session (a reboot always clears it). The
        # launcher is not root here, so it cannot remove the address -- but a
        # read-only check needs no root, so confirm removal before reporting a
        # clean teardown we did not verify. _direct_link_reset_stale_unix
        # reconciles the leftover "enabled" on the next launch regardless.
        cfg["enabled"] = False
        self.saved["direct_link"] = cfg
        self._save()
        self._set_direct_checkbox(False)
        adapter_id = cfg.get("id") or cfg.get("adapter", "")
        if adapter_id and directlink.unix_interface_has_ipv4(
                adapter_id, cfg.get("server_ip", "")):
            self._set_direct_status(
                "The helper stopped without cleaning up: '{}' may still hold "
                "the temporary address {} until you reboot or remove it "
                "manually (see the TERMINAL tab).".format(
                    cfg.get("adapter") or adapter_id, cfg.get("server_ip")))
            self._append_log(
                "directlink",
                "[launcher] note: '{}' may still hold the temporary address "
                "{}; a reboot clears it, or remove it manually.\n".format(
                    adapter_id, cfg.get("server_ip")))

    @staticmethod
    def _parse_rehome(lines):
        """(server_ip, client_ip, prefix) from the helper's last REHOME line."""
        for line in reversed(list(lines or [])):
            if "REHOME " not in line:
                continue
            fields = {}
            for token in line.split("REHOME ", 1)[1].split():
                key, _, val = token.partition("=")
                fields[key] = val
            try:
                return (fields["server_ip"], fields["client_ip"],
                        int(fields.get("prefix", directlink.PREFIX_LENGTH)))
            except (KeyError, ValueError):
                return None
        return None

    _MAX_REHOMES = 4

    def _direct_link_rehome(self, plan):
        """The helper found a device already on the wire; move this PC's address
        to coexist with it and restart the helper. No console reconfiguration."""
        server_ip, client_ip, prefix = plan
        cfg = self.saved.get("direct_link") or {}
        if self._direct_rehomes >= self._MAX_REHOMES:
            self._append_log("directlink",
                             "[launcher] stopped moving after {} tries; the "
                             "wire looks busier than a single console\n".format(
                                 self._direct_rehomes))
            self._set_direct_status(
                "Couldn't find a clear address to share this link — see the "
                "TERMINAL tab. Untick and tick to retry.")
            self._finish_direct_exit()
            return
        self._direct_rehomes += 1
        cfg["server_ip"] = server_ip
        cfg["client_ip"] = client_ip
        cfg["prefix"] = prefix
        cfg["enabled"] = True
        self.saved["direct_link"] = cfg
        # Persist the recovery marker BEFORE touching the adapter, so a crash or
        # failure mid-move still returns the port to DHCP on the next launch
        # instead of stranding it static.
        self.saved["pending_direct_link_restore"] = True
        self._save()
        self._set_direct_checkbox(True, busy=True)
        self._set_direct_status(
            "A device is already on this cable — moving this PC to {} so they "
            "share the link (nothing to change on the PS2)…".format(server_ip))

        def worker():
            try:
                out = directlink.apply_adapter_config(
                    cfg["if_index"], server_ip, client_ip, prefix)
            except Exception as e:
                self.root.after(0, lambda err=e: self._direct_link_rehome_failed(err))
                return
            self.root.after(0, lambda: self._direct_link_rehome_done(out))

        threading.Thread(target=worker, daemon=True).start()

    def _direct_link_rehome_failed(self, err):
        self._append_log("directlink",
                         "[launcher] could not move the direct-link address: "
                         "{}\n".format(err))
        self._set_direct_status(
            "Couldn't move the direct-link address — see the TERMINAL tab. "
            "Untick and tick to retry.")
        # Route through the recovery path: it returns the port to DHCP and
        # clears the saved direct-link state.
        self._rollback_failed_direct_responder(self.saved.get("direct_link") or {})

    def _direct_link_rehome_done(self, output):
        if output:
            self._append_log("directlink", "[setup] {}\n".format(
                output.replace("\n", "\n[setup] ")))
        cfg = self.saved.get("direct_link") or {}
        self._direct_proc = None
        if not self._start_direct_responder():
            # The port was reconfigured but the helper won't start: recover
            # rather than leave a stranded static port.
            self._rollback_failed_direct_responder(cfg)
            return
        # Configured and serving on the new address -> the move is committed;
        # drop the crash-recovery marker.
        self.saved.pop("pending_direct_link_restore", None)
        self.ip_var.set(cfg["server_ip"])
        self._save()
        self._set_direct_checkbox(True)
        self._set_direct_status(self._direct_ready_status(cfg))

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
              pending_direct_link_off=False) -> bool:
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
        if self.saved.get("pending_direct_link_restore"):
            data["pending_direct_link_restore"] = True
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
            return False
        return True

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
        # A server that reports a transfer in flight is called out by name and
        # the prompt defaults to No. Quitting mid-write truncates whatever the
        # console was writing, and a save is the one thing here that cannot be
        # got back -- so this is the case where the habitual Enter should not
        # be the destructive answer.
        busy = [TAB_TITLES.get(key, key.upper()) for key in self.procs
                if status_client.is_busy(self.status_poller.status(key))]
        if busy:
            return messagebox.askyesno(
                title,
                "{} reports a transfer in progress.\n\n"
                "Quitting now will interrupt it. If a console is writing a "
                "save, that save will be incomplete.\n\n"
                "This will stop running servers:\n\n{}\n\nQuit anyway?".format(
                    ", ".join(busy), ", ".join(running)),
                icon="warning", default="no")
        return messagebox.askyesno(
            title,
            "This will stop running servers:\n\n{}\n\nContinue?".format(
                ", ".join(running)))

    def _destroy_root(self):
        """The ONLY place the root is destroyed. Sets _shutting_down first so the
        periodic loops (_drain_tray / _drain_logs / _poll_status) and any pending
        worker callback stop touching the root -- otherwise a reschedule or a
        queued after() raises TclError. Every teardown path routes through here,
        including the relaunch/elevation flows that used to destroy directly."""
        self._shutting_down = True
        self.root.destroy()

    def _shutdown_app(self):
        # Stop the loops before the (up to a few seconds of) child termination,
        # not just at destroy time, so nothing reschedules during stop_all.
        self._shutting_down = True
        self._save()
        # hide first so the (up to a few seconds of) child termination doesn't
        # look like a frozen window
        self.root.withdraw()
        self.stop_all()
        if self._tray:
            self._tray.stop()
        self._destroy_root()

    # -- system tray (Windows) -------------------------------------------- #
    def _on_window_close(self):
        if self._should_close_to_tray():
            self._hide_to_tray()
            return
        # No tray to fall back to (every OS but Windows, or the tray disabled):
        # closing the window really does quit. Confirm ONLY when servers are
        # running -- _confirm_app_shutdown returns True instantly otherwise, so
        # an idle close is still one click. Without this a Linux user clicking X
        # out of habit silently kills a server mid-load.
        self.exit_app()

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
                    # Confirm if servers are running, like the window-close and
                    # Exit paths -- an explicit Quit should not silently kill a
                    # transfer either. No-op prompt when nothing is running.
                    self.exit_app()
                    if self._shutting_down:
                        return  # root is being destroyed; don't reschedule
        except queue.Empty:
            pass
        if not self._shutting_down:
            self.root.after(150, self._drain_tray)

    def _quit_from_tray(self):
        self.exit_app()


def run_gui():
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista" if platform.system() == "Windows" else "clam")
    except tk.TclError:
        pass
    LauncherApp(root)
    root.mainloop()
    return 0
