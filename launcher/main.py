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

COMPRESSION_ABOUT_TEXT = """

Optional compression support

Compressed image support is optional. ZSO/LZ4 support needs the Python lz4 package. CHD support needs the native libchdr library.

Use the Compression support panel near the top of the launcher to check what is available. From source, the launcher can install lz4 with pip for you. Packaged builds cannot be modified with pip; lz4 should be bundled at build time if always-on ZSO support is desired.

libchdr is a native system library, so the launcher shows platform-specific help instead of silently installing DLLs or system packages.
"""


def _apply_gui_review_fixes(gui):
    """Apply terminal UX fixes, clearer tabs, a light PS2 skin, and admin UX."""
    original_text = gui.tk.Text
    original_notebook = gui.ttk.Notebook
    original_launcher_init = gui.LauncherApp.__init__
    original_build = gui.LauncherApp._build

    try:
        from . import theme_assets
        embedded_assets = getattr(theme_assets, "ASSETS", {})
    except Exception:
        embedded_assets = {}

    try:
        from . import optional_deps
    except Exception:
        optional_deps = None

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
    }

    if ADMIN_ABOUT_TEXT not in gui.ABOUT_TEXT:
        gui.ABOUT_TEXT += ADMIN_ABOUT_TEXT
    if COMPRESSION_ABOUT_TEXT not in gui.ABOUT_TEXT:
        gui.ABOUT_TEXT += COMPRESSION_ABOUT_TEXT

    gui.COLOR_RUNNING = palette["ok"]
    gui.COLOR_STOPPED = palette["muted"]
    gui.COLOR_ERROR = palette["error"]

    def asset_photo(app, name):
        data = embedded_assets.get(name)
        if not data:
            return None
        if isinstance(data, (tuple, list)):
            data = "".join(data)
        try:
            photo = gui.tk.PhotoImage(data=data)
        except Exception:
            return None
        photos = getattr(app, "_ps2_theme_photos", [])
        photos.append(photo)
        app._ps2_theme_photos = photos
        return photo

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
        style.configure("Admin.TLabel", background=palette["panel"], foreground=palette["muted"])
        style.configure("AdminYes.TLabel", background=palette["panel"], foreground=palette["ok"])
        style.configure("AdminNo.TLabel", background=palette["panel"], foreground=palette["warn"])
        style.configure("TButton", background=palette["panel2"], foreground=palette["text"],
                        borderwidth=1, focusthickness=1, focuscolor=palette["accent"])
        style.map("TButton",
                  background=[("active", palette["panel3"]), ("pressed", palette["accent"])],
                  foreground=[("disabled", "#5f6f8d"), ("pressed", "#ffffff")])
        style.configure("Accent.TButton", background=palette["accent"], foreground="#ffffff",
                        borderwidth=1, focusthickness=1, focuscolor=palette["accent2"])
        style.map("Accent.TButton",
                  background=[("active", "#12b8ff"), ("pressed", "#006fd0")],
                  foreground=[("disabled", "#5f6f8d"), ("!disabled", "#ffffff")])
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
        style.configure("Admin.TFrame", background=palette["panel"], relief="solid", borderwidth=1)
        style.configure("Server.TNotebook", background=palette["bg"], borderwidth=0,
                        tabmargins=(8, 6, 8, 0))
        style.configure("Server.TNotebook.Tab", padding=(16, 7), font=("", 10, "bold"),
                        background=palette["panel"], foreground=palette["muted"], borderwidth=1)
        style.map("Server.TNotebook.Tab",
                  background=[("selected", palette["panel3"]),
                              ("active", palette["panel2"]),
                              ("!selected", palette["panel"])],
                  foreground=[("selected", palette["accent2"]),
                              ("active", palette["text"]),
                              ("!selected", palette["muted"])],
                  expand=[("selected", (2, 2, 2, 0))])

    def draw_banner(canvas, width=760, height=78):
        canvas.delete("all")
        canvas.create_rectangle(0, 0, width, height, fill=palette["bg"], outline="")
        for y, color in ((10, "#061534"), (38, "#082351"), (68, "#061534")):
            canvas.create_line(0, y, width, y, fill=color)
        for x in range(32, width, 96):
            canvas.create_line(x, 0, x, height, fill="#081f48")
        canvas.create_line(0, height - 2, width, height - 2, fill=palette["accent"], width=2)
        canvas.create_line(0, height - 5, width, height - 5, fill="#013a7a")
        for x in (42, 132, width - 185, width - 72):
            canvas.create_rectangle(x, 18, x + 18, 36, outline=palette["accent"], width=1)
            canvas.create_line(x, 18, x + 9, 10, x + 27, 10, x + 18, 18, fill="#0b69ff")
            canvas.create_line(x + 18, 36, x + 27, 28, x + 27, 10, fill="#0848b8")
        canvas.create_text(24, 24, anchor="w", text="PS2", fill=palette["accent2"],
                           font=("", 26, "bold"))
        canvas.create_text(112, 26, anchor="w", text="SERVERS", fill=palette["text"],
                           font=("", 18, "bold"))
        canvas.create_text(24, 56, anchor="w", text="SMBv1  ·  UDPFS  ·  UDPBD  ·  no terminal required",
                           fill=palette["muted"], font=("", 9))

    def build_banner(self):
        frame = gui.ttk.Frame(self.root, style="Header.TFrame")
        frame.pack(fill="x", padx=10, pady=(10, 0))

        banner = asset_photo(self, "BANNER")
        if banner:
            label = gui.tk.Label(frame, image=banner, bg=palette["bg"], bd=0,
                                 highlightthickness=0)
            label.pack(anchor="w")
        else:
            canvas = gui.tk.Canvas(frame, height=78, highlightthickness=0,
                                   bg=palette["bg"], bd=0)
            canvas.pack(fill="x", expand=True)
            canvas.bind("<Configure>", lambda event: draw_banner(canvas, event.width, 78))
            self._ps2_theme_banner = canvas

        accent = asset_photo(self, "ACCENT")
        if accent:
            gui.tk.Label(frame, image=accent, bg=palette["bg"], bd=0,
                         highlightthickness=0).pack(anchor="w", pady=(2, 0))

    def add_admin_panel(self):
        if not gui.windows_setup.is_windows():
            return
        frame = gui.ttk.Frame(self.root, style="Admin.TFrame")
        frame.pack(fill="x", padx=10, pady=(6, 4))
        is_admin = gui.elevate.is_admin()
        status_style = "AdminYes.TLabel" if is_admin else "AdminNo.TLabel"
        status_text = "Administrator: Yes" if is_admin else "Administrator: No"
        gui.ttk.Label(frame, text=status_text, style=status_style,
                      font=("", 9, "bold")).pack(side="left", padx=(8, 10), pady=6)
        gui.ttk.Label(frame,
                      text="Normal launch stays non-admin. Elevate only for firewall changes or advanced port 445.",
                      style="Admin.TLabel").pack(side="left", padx=(0, 8), pady=6)
        button = gui.ttk.Button(frame, text="Restart as administrator",
                                style="Accent.TButton",
                                command=lambda: restart_as_admin(self))
        button.pack(side="right", padx=8, pady=5)
        if is_admin or not gui.elevate.can_elevate():
            button.config(state="disabled")
        self._ps2_admin_frame = frame

    def add_compression_panel(self):
        if optional_deps is None:
            return
        frame = gui.ttk.Frame(self.root, style="Admin.TFrame")
        frame.pack(fill="x", padx=10, pady=(0, 4))
        gui.ttk.Label(frame, text="Compression support:", style="Admin.TLabel",
                      font=("", 9, "bold")).pack(side="left", padx=(8, 8), pady=6)
        self._ps2_compression_var = gui.tk.StringVar(value="Checking…")
        gui.ttk.Label(frame, textvariable=self._ps2_compression_var,
                      style="Admin.TLabel").pack(side="left", padx=(0, 8), pady=6)
        gui.ttk.Button(frame, text="Check",
                       command=lambda: show_compression_status(self)).pack(side="right", padx=(4, 8), pady=5)
        gui.ttk.Button(frame, text="CHD/libchdr help",
                       command=show_libchdr_help).pack(side="right", padx=(4, 0), pady=5)
        install = gui.ttk.Button(frame, text="Install ZSO/LZ4 support",
                                 command=lambda: install_lz4_from_gui(self))
        install.pack(side="right", padx=(4, 0), pady=5)
        if optional_deps.is_frozen_app():
            install.config(state="disabled")
        self._ps2_lz4_button = install
        self._ps2_compression_frame = frame
        refresh_compression_status(self)

    def refresh_compression_status(app):
        statuses = optional_deps.check_all()
        bits = []
        for status in statuses:
            label = "ZSO/LZ4" if status.key == "lz4" else "CHD"
            bits.append("{} {}".format(label, "OK" if status.available else "missing"))
        app._ps2_compression_var.set("; ".join(bits))
        return statuses

    def show_compression_status(app):
        statuses = refresh_compression_status(app)
        gui.messagebox.showinfo("Optional compression support",
                                optional_deps.format_statuses(statuses))

    def install_lz4_from_gui(app):
        if optional_deps.is_frozen_app():
            gui.messagebox.showinfo(
                "Packaged app",
                "This packaged app cannot install Python packages into itself.\n\n"
                "Run from source to install lz4, or use a release build that bundles lz4."
            )
            return
        command = " ".join(optional_deps.lz4_install_command())
        if not gui.messagebox.askyesno(
                "Install ZSO/LZ4 support?",
                "Install the optional Python lz4 package now?\n\n"
                "Command:\n{}\n\n"
                "This affects the current Python environment only.".format(command)):
            return
        button = getattr(app, "_ps2_lz4_button", None)
        if button:
            button.config(state="disabled")
        app._append_log("setup", "[deps] installing optional lz4 package\n")

        def worker():
            try:
                optional_deps.install_lz4(
                    log=lambda line: app.root.after(
                        0, app._append_log, "setup", "[deps] {}\n".format(line)))
            except Exception as e:
                app.root.after(0, finish_lz4_install, app, False, str(e))
                return
            app.root.after(0, finish_lz4_install, app, True, "lz4 installed.")

        import threading
        threading.Thread(target=worker, daemon=True).start()

    def finish_lz4_install(app, success, detail):
        button = getattr(app, "_ps2_lz4_button", None)
        if button and not optional_deps.is_frozen_app():
            button.config(state="normal")
        refresh_compression_status(app)
        if success:
            app._append_log("setup", "[deps] lz4 install finished\n")
            gui.messagebox.showinfo("ZSO/LZ4 support", detail)
        else:
            app._append_log("setup", "[deps] lz4 install failed: {}\n".format(detail))
            gui.messagebox.showerror("ZSO/LZ4 install failed", detail)

    def show_libchdr_help():
        gui.messagebox.showinfo("CHD/libchdr support", optional_deps.libchdr_help_text())

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

    def wrapped_text(*args, **kwargs):
        if kwargs.get("wrap") == "none":
            kwargs["wrap"] = "char"
        kwargs.setdefault("background", palette["entry"])
        kwargs.setdefault("foreground", palette["text"])
        kwargs.setdefault("insertbackground", palette["text"])
        kwargs.setdefault("selectbackground", palette["accent"])
        kwargs.setdefault("selectforeground", "#ffffff")
        return original_text(*args, **kwargs)

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

    def launcher_build(self):
        build_banner(self)
        add_admin_panel(self)
        add_compression_panel(self)
        original_build(self)

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

    gui.tk.Text = wrapped_text
    gui.ttk.Notebook = StyledNotebook
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
