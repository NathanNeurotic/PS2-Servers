"""Launcher entry point.

Normally this opens the GUI. It also understands a few flags used internally and
for testing:

  --serve <key> [args...]   run one server in this process (the re-exec target)
  --list                    print the servers available on this machine
  --selfcheck               verify the re-exec path can actually start a server
                            (used to confirm the packaged build works)
"""

import platform
import sys

from . import app_icon


def main(argv=None):
    if argv is None:
        argv = (getattr(sys, "argv", None) or [])[1:]
    argv = list(argv)

    if "--serve" in argv:
        i = argv.index("--serve")
        rest = argv[i + 1:]
        if not rest:
            print("error: --serve requires a server key", file=sys.stderr)
            return 2
        from .serve import run_serve
        return run_serve(rest[0], rest[1:]) or 0

    if "--list" in argv or "-l" in argv:
        _print_list()
        return 0

    if "--selfcheck" in argv:
        return _selfcheck()

    app_icon.set_windows_app_id()
    try:
        from . import gui
    except ImportError as e:  # Tkinter not present in this Python build
        print("GUI unavailable ({}). Servers on this machine:".format(e))
        _print_list()
        return 1
    _apply_gui_review_fixes(gui)
    return gui.run_gui()


ADMIN_ABOUT_TEXT = """

Administrator rights

PS2 Servers is designed to start normally without administrator rights. Normal custom-port SMB mode, UDPFS, UDPBD, browsing folders, and reading logs do not need the whole launcher to run elevated.

Administrator rights are requested only when Windows requires them:

- creating or refreshing PS2 Servers Windows Firewall allow rules;
- removing PS2 Servers Windows Firewall rules;
- using the advanced SMB port 445 mode.

The launcher shows whether it is currently running as administrator. Use "Restart as administrator" only when you intentionally need elevated Windows setup actions. Keeping the default launch non-admin reduces the blast radius of bugs and makes the app easier to trust.
"""


def _apply_gui_review_fixes(gui):
    """Apply the active launcher theme and a stable, responsive layout baseline.

    This stays as a runtime shim so release-risk remains low, but it deliberately
    avoids image stretching, fixed-width root geometry, and off-screen button rows.
    """
    original_notebook = gui.ttk.Notebook
    original_launcher_init = gui.LauncherApp.__init__
    original_build = gui.LauncherApp._build

    palette = {
        "bg": "#030713",
        "panel": "#071226",
        "panel2": "#0b1a35",
        "panel3": "#10254b",
        "text": "#d9ecff",
        "muted": "#8aa9d6",
        "accent": "#0094ff",
        "accent2": "#37d7ff",
        "ok": "#46f6b1",
        "warn": "#ffcf5a",
        "error": "#ff426d",
        "entry": "#07101f",
        "disabled": "#5f6f8d",
    }

    if ADMIN_ABOUT_TEXT not in gui.ABOUT_TEXT:
        gui.ABOUT_TEXT += ADMIN_ABOUT_TEXT

    gui.COLOR_RUNNING = palette["ok"]
    gui.COLOR_STOPPED = palette["muted"]
    gui.COLOR_ERROR = palette["error"]

    def content_parent(app):
        return getattr(app, "content", app.root)

    def apply_theme(root):
        root.configure(background=palette["bg"])
        style = gui.ttk.Style(root)
        try:
            style.theme_use("clam")
        except gui.tk.TclError:
            pass

        style.configure(".",
                        background=palette["bg"],
                        foreground=palette["text"],
                        fieldbackground=palette["entry"],
                        bordercolor=palette["panel3"],
                        lightcolor=palette["panel3"],
                        darkcolor=palette["panel"],
                        troughcolor=palette["panel"])
        style.configure("TFrame", background=palette["bg"])
        style.configure("Header.TFrame", background=palette["bg"])
        style.configure("TLabel", background=palette["bg"], foreground=palette["text"])
        style.configure("Muted.TLabel", background=palette["bg"], foreground=palette["muted"])
        style.configure("TopStrip.TFrame", background=palette["panel"], relief="flat")
        style.configure("TopStripTitle.TLabel", background=palette["panel"],
                        foreground=palette["text"])
        style.configure("TopStripHint.TLabel", background=palette["panel"],
                        foreground=palette["muted"])
        style.configure("Footer.TFrame", background=palette["panel"], relief="flat")
        style.configure("Admin.TLabel", background=palette["panel"], foreground=palette["muted"])
        style.configure("AdminYes.TLabel", background=palette["panel"], foreground=palette["ok"])
        style.configure("AdminNo.TLabel", background=palette["panel"], foreground=palette["warn"])
        style.configure("TButton", background=palette["panel2"], foreground=palette["text"],
                        borderwidth=1, focusthickness=1, focuscolor=palette["accent"])
        style.map("TButton",
                  background=[("active", palette["panel3"]), ("pressed", palette["accent"])],
                  foreground=[("disabled", palette["disabled"]), ("pressed", "#ffffff")])
        style.configure("Accent.TButton", background=palette["accent"], foreground="#ffffff",
                        borderwidth=1, focusthickness=1, focuscolor=palette["accent2"])
        style.map("Accent.TButton",
                  background=[("active", "#12b8ff"), ("pressed", "#006fd0")],
                  foreground=[("disabled", palette["disabled"]), ("!disabled", "#ffffff")])
        style.configure("TEntry", fieldbackground=palette["entry"], foreground=palette["text"],
                        insertcolor=palette["text"], bordercolor=palette["panel3"])
        style.configure("TCombobox", fieldbackground=palette["entry"], foreground=palette["text"],
                        arrowcolor=palette["accent"], bordercolor=palette["panel3"])
        style.map("TCombobox",
                  fieldbackground=[("readonly", palette["entry"])],
                  foreground=[("readonly", palette["text"])])
        style.configure("TCheckbutton", background=palette["bg"], foreground=palette["text"])
        style.map("TCheckbutton", background=[("active", palette["panel"])])
        style.configure("TLabelframe", background=palette["panel"], foreground=palette["text"],
                        bordercolor=palette["accent"], relief="solid")
        style.configure("TLabelframe.Label", background=palette["bg"], foreground=palette["accent2"],
                        font=("", 10, "bold"))
        style.configure("Card.TLabelframe", background=palette["panel"], foreground=palette["text"],
                        bordercolor="#18416f", relief="solid", borderwidth=1)
        style.configure("Card.TLabelframe.Label", background=palette["bg"],
                        foreground=palette["accent2"], font=("", 10, "bold"))
        style.configure("Card.TFrame", background=palette["panel"])
        style.configure("Card.TLabel", background=palette["panel"], foreground=palette["text"])
        style.configure("CardMuted.TLabel", background=palette["panel"], foreground=palette["muted"])
        style.configure("CardHelp.TLabel", background=palette["panel"], foreground=palette["muted"])
        style.configure("CardHint.TLabel", background=palette["panel"], foreground=palette["accent2"])
        style.configure("CardStatus.TLabel", background=palette["panel"], foreground=palette["muted"])
        style.configure("Card.TCheckbutton", background=palette["panel"], foreground=palette["text"])
        style.map("Card.TCheckbutton", background=[("active", palette["panel2"])])
        style.configure("PageActions.TFrame", background=palette["panel"], relief="flat")
        style.configure("PageActions.TLabel", background=palette["panel"], foreground=palette["muted"])
        style.configure("Admin.TFrame", background=palette["panel"], relief="solid", borderwidth=1)
        style.configure("Server.TNotebook", background=palette["bg"], borderwidth=0,
                        tabmargins=(8, 6, 8, 0))
        style.configure("Server.TNotebook.Tab", padding=(12, 6), font=("", 10, "bold"),
                        background=palette["panel"], foreground=palette["muted"], borderwidth=1)
        style.map("Server.TNotebook.Tab",
                  background=[("selected", palette["panel3"]),
                              ("active", palette["panel2"]),
                              ("!selected", palette["panel"])],
                  foreground=[("selected", palette["accent2"]),
                              ("active", palette["text"]),
                              ("!selected", palette["muted"])])

    def configure_window(self):
        screen_width = max(640, self.root.winfo_screenwidth())
        screen_height = max(480, self.root.winfo_screenheight())
        width = min(1040, max(760, screen_width - 96))
        height = min(780, max(520, screen_height - 96))
        min_width = min(760, max(640, screen_width - 96))
        min_height = min(480, max(420, screen_height - 96))
        x = max(0, int((screen_width - width) / 2))
        y = max(0, int((screen_height - height) / 3))
        self.root.geometry("{}x{}+{}+{}".format(width, height, x, y))
        self.root.minsize(min_width, min_height)
        self.root.resizable(True, True)

    def build_scroll_body(self):
        bg = self.root.cget("background")
        shell = gui.ttk.Frame(self.root)
        shell.pack(fill="both", expand=True)

        canvas = gui.tk.Canvas(shell, highlightthickness=0, bd=0, background=bg)
        scrollbar = gui.ttk.Scrollbar(shell, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        body = gui.ttk.Frame(canvas)
        window = canvas.create_window((0, 0), window=body, anchor="nw")

        def refresh_scroll_region(event=None):
            try:
                width = max(1, canvas.winfo_width())
                height = max(1, body.winfo_reqheight())
                canvas.itemconfigure(window, width=width)
                canvas.configure(scrollregion=(0, 0, width, height))
            except gui.tk.TclError:
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

    def set_wrap(widget, width):
        try:
            widget.configure(wraplength=max(220, int(width)))
        except gui.tk.TclError:
            pass

    def bind_wrap(widget, offset=48):
        def update(event=None):
            try:
                root_width = widget.winfo_toplevel().winfo_width()
                set_wrap(widget, root_width - offset)
            except gui.tk.TclError:
                pass
        widget.bind("<Configure>", update, add="+")
        widget.after_idle(update)

    def draw_banner(canvas, width, height=88):
        width = max(320, int(width))
        canvas.delete("all")
        canvas.create_rectangle(0, 0, width, height, fill=palette["bg"], outline="")
        canvas.create_rectangle(0, 0, width, height, fill=palette["panel"], outline="#18416f")
        for y, color in ((12, "#061534"), (42, "#082351"), (78, "#061534")):
            canvas.create_line(0, y, width, y, fill=color)
        for x in range(36, width + 96, 96):
            canvas.create_line(x, 0, x - 38, height, fill="#081f48")
        canvas.create_line(0, height - 3, width, height - 3, fill=palette["accent"], width=2)
        for x in (42, 132, max(220, width - 190), max(300, width - 82)):
            if 0 <= x < width - 28:
                canvas.create_rectangle(x, 18, x + 18, 36, outline=palette["accent"], width=1)
                canvas.create_line(x, 18, x + 9, 10, x + 27, 10, x + 18, 18,
                                   fill="#0b69ff")
                canvas.create_line(x + 18, 36, x + 27, 28, x + 27, 10, fill="#0848b8")
        canvas.create_text(24, 25, anchor="w", text="PS2", fill=palette["accent2"],
                           font=("", 26, "bold"))
        canvas.create_text(112, 27, anchor="w", text="SERVERS", fill=palette["text"],
                           font=("", 18, "bold"))
        canvas.create_text(24, 61, anchor="w",
                           text="SMBv1  ·  UDPFS  ·  UDPBD  ·  no terminal required",
                           fill=palette["muted"], font=("", 9))

    def build_banner(self):
        frame = gui.ttk.Frame(content_parent(self), style="Header.TFrame")
        frame.pack(fill="x", padx=16, pady=(12, 0))
        canvas = gui.tk.Canvas(frame, height=88, highlightthickness=0,
                               bg=palette["bg"], bd=0)
        canvas.pack(fill="x", expand=True)

        def redraw(event=None):
            try:
                draw_banner(canvas, event.width if event else canvas.winfo_width())
            except gui.tk.TclError:
                pass

        canvas.bind("<Configure>", redraw)
        canvas.after_idle(redraw)
        self._ps2_theme_banner = canvas

    def add_admin_panel(self):
        if not gui.windows_setup.is_windows():
            return
        frame = gui.ttk.Frame(content_parent(self), style="Admin.TFrame",
                              padding=(12, 8))
        frame.pack(fill="x", padx=16, pady=(8, 8))
        frame.columnconfigure(1, weight=1)
        is_admin = gui.elevate.is_admin()
        status_style = "AdminYes.TLabel" if is_admin else "AdminNo.TLabel"
        status_text = "Administrator: Yes" if is_admin else "Administrator: No"
        gui.ttk.Label(frame, text=status_text, style=status_style,
                      font=("", 9, "bold")).grid(row=0, column=0, sticky="w",
                                                 padx=(0, 12), pady=(0, 4))
        note = gui.ttk.Label(
            frame,
            text="Normal launch stays non-admin. Elevate only for firewall changes or advanced port 445.",
            style="Admin.TLabel")
        note.grid(row=0, column=1, sticky="ew", pady=(0, 4))
        bind_wrap(note, offset=360)
        button = gui.ttk.Button(frame, text="Restart as administrator",
                                style="Accent.TButton",
                                command=lambda: restart_as_admin(self))
        button.grid(row=0, column=2, sticky="e", padx=(12, 0), pady=(0, 4))
        if is_admin or not gui.elevate.can_elevate():
            button.config(state="disabled")
        self._ps2_admin_frame = frame

    def restart_as_admin(app):
        if not gui.windows_setup.is_windows():
            gui.messagebox.showinfo("Windows only", "Administrator restart is only needed on Windows.")
            return
        if gui.elevate.is_admin():
            gui.messagebox.showinfo("Already administrator", "PS2 Servers is already running as administrator.")
            return
        if not gui.elevate.can_elevate():
            gui.messagebox.showerror("Administrator unavailable", "This environment cannot request administrator rights.")
            return
        if not gui.messagebox.askyesno(
                "Restart as administrator?",
                "Restart PS2 Servers as administrator?\n\n"
                "Use this only when you need to manage Windows Firewall rules "
                "or use advanced SMB port 445 mode. Normal servers do not need it."):
            return
        app._save()
        if gui.elevate.relaunch_as_admin():
            app.stop_all()
            if app._tray:
                app._tray.stop()
            app.root.destroy()
        else:
            gui.messagebox.showerror("Elevation failed", "Could not restart as administrator.")

    class StyledNotebook(original_notebook):
        def __init__(self, *args, **kwargs):
            kwargs.setdefault("style", "Server.TNotebook")
            super().__init__(*args, **kwargs)

        def add(self, child, **kwargs):
            text = kwargs.get("text")
            if text:
                kwargs["text"] = "  {}  ".format(text.strip())
            kwargs.setdefault("padding", (8, 8))
            return super().add(child, **kwargs)

    def normalize_original_widgets(self):
        try:
            self.nb.pack_configure(fill="both", expand=True)
        except (gui.tk.TclError, AttributeError):
            pass

        for card in getattr(self, "cards", {}).values():
            for child in card.winfo_children():
                if getattr(child, "winfo_class", lambda: "")() == "TLabel":
                    text = str(child.cget("text"))
                    if text:
                        bind_wrap(child, offset=96)

        parent = content_parent(self)
        for child in parent.winfo_children():
            try:
                style = child.cget("style")
            except gui.tk.TclError:
                style = ""
            if style == "Footer.TFrame":
                for index in range(6):
                    child.columnconfigure(index, weight=0)
                child.columnconfigure(2, weight=1)

    def launcher_build(self):
        build_banner(self)
        add_admin_panel(self)
        original_build(self)
        normalize_original_widgets(self)

    def launcher_init(self, root):
        apply_theme(root)
        app_icon.apply_to_tk_root(root, gui.tk)
        original_launcher_init(self, root)

    def append_log(self, key, text):
        widget = self.logs[key]
        at_bottom = widget.yview()[1] >= 0.99
        widget.config(state="normal")
        widget.insert("end", self._terminal_text(key, text))
        lines = int(widget.index("end-1c").split(".")[0])
        if lines > 2000:  # keep the log bounded so memory/redraw stay cheap
            widget.delete("1.0", "{}.0".format(lines - 2000))
        if at_bottom:
            widget.see("end")
        widget.config(state="disabled")

    gui.ttk.Notebook = StyledNotebook
    gui.LauncherApp._configure_window = configure_window
    gui.LauncherApp._build_scroll_body = build_scroll_body
    gui.LauncherApp._build = launcher_build
    gui.LauncherApp.__init__ = launcher_init
    gui.LauncherApp._append_log = append_log


def _selfcheck():
    """Verify the re-exec path works in this build: build the command the GUI
    uses to start a server and confirm it actually launches."""
    import os
    import shutil
    import subprocess
    import tempfile
    import time
    from .servers import frozen_self_exe, is_frozen, serve_command

    argv = getattr(sys, "argv", None)
    print("is_frozen:", is_frozen())
    print("sys.executable:", sys.executable)
    print("sys.argv[0]:", argv[0] if argv else None)
    print("NUITKA_ONEFILE_BINARY:", os.environ.get("NUITKA_ONEFILE_BINARY"))
    print("frozen_self_exe:", frozen_self_exe())

    # mkdtemp + shutil.rmtree(ignore_errors=True): works on any Python (no 3.10+
    # kwarg) and tolerates a lingering file lock if a re-exec'd child is slow to die
    tmpdir = tempfile.mkdtemp(prefix="ps2chk_")
    try:
        img = os.path.join(tmpdir, "a.img")
        with open(img, "wb") as f:
            f.write(b"\0" * 65536)
        cmd = serve_command("udpbd", [img])
        print("serve_command:", cmd)
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True)
        except OSError as e:
            print("SPAWN FAILED:", e)
            return 1
        alive = False
        try:
            time.sleep(3)
            alive = proc.poll() is None
            print("server alive:", alive)
            if not alive:
                print("output:", (proc.stdout.read() or "")[:500])
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        print("RESULT:", "PASS" if alive else "FAIL")
        return 0 if alive else 1
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _print_list():
    from .servers import REGISTRY

    print("PS2-Servers launcher -- servers on this machine ({}):".format(platform.system()))
    for key, s in REGISTRY.items():
        mark = "OK " if s.is_available() else "n/a"
        print("  [{}] {:7s} {:30s} port {:8s} ({})".format(
            mark, key, s.label, s.port_display(), s.runtime))


if __name__ == "__main__":
    sys.exit(main())
