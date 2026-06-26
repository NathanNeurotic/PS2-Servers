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

    try:
        from . import gui
    except ImportError as e:  # Tkinter not present in this Python build
        print("GUI unavailable ({}). Servers on this machine:".format(e))
        _print_list()
        return 1
    _apply_gui_review_fixes(gui)
    return gui.run_gui()


def _apply_gui_review_fixes(gui):
    """Apply terminal UX fixes and clearer tab styling for the launcher."""
    original_text = gui.tk.Text
    original_notebook = gui.ttk.Notebook

    def wrapped_text(*args, **kwargs):
        if kwargs.get("wrap") == "none":
            kwargs["wrap"] = "char"
        return original_text(*args, **kwargs)

    class StyledNotebook(original_notebook):
        def __init__(self, *args, **kwargs):
            kwargs.setdefault("style", "Server.TNotebook")
            super().__init__(*args, **kwargs)
            style = gui.ttk.Style(self)
            style.configure("Server.TNotebook", borderwidth=1, tabmargins=(8, 6, 8, 0))
            style.configure("Server.TNotebook.Tab", padding=(16, 7), font=("", 10, "bold"),
                            borderwidth=2)
            style.map("Server.TNotebook.Tab",
                      background=[("selected", "#ffffff"),
                                  ("active", "#eef4ff"),
                                  ("!selected", "#d8dee8")],
                      foreground=[("selected", "#0b2f5f"),
                                  ("!selected", "#111111")],
                      expand=[("selected", (2, 2, 2, 0))])

        def add(self, child, **kwargs):
            text = kwargs.get("text")
            if text:
                kwargs["text"] = "  {}  ".format(text.strip())
            kwargs.setdefault("padding", (8, 8))
            return super().add(child, **kwargs)

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
