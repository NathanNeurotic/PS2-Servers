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
    argv = list(sys.argv[1:] if argv is None else argv)

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
        from .gui import run_gui
    except ImportError as e:  # Tkinter not present in this Python build
        print("GUI unavailable ({}). Servers on this machine:".format(e))
        _print_list()
        return 1
    return run_gui()


def _selfcheck():
    """Verify the re-exec path works in this build: build the command the GUI
    uses to start a server and confirm it actually launches."""
    import os
    import subprocess
    import tempfile
    import time
    from .servers import frozen_self_exe, is_frozen, serve_command

    print("is_frozen:", is_frozen())
    print("sys.executable:", sys.executable)
    print("sys.argv[0]:", sys.argv[0])
    print("NUITKA_ONEFILE_BINARY:", os.environ.get("NUITKA_ONEFILE_BINARY"))
    print("frozen_self_exe:", frozen_self_exe())

    img = os.path.join(tempfile.mkdtemp(prefix="ps2chk_"), "a.img")
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
    time.sleep(3)
    alive = proc.poll() is None
    print("server alive:", alive)
    if not alive:
        print("output:", (proc.stdout.read() or "")[:500])
    proc.terminate()
    print("RESULT:", "PASS" if alive else "FAIL")
    return 0 if alive else 1


def _print_list():
    from .servers import REGISTRY

    print("PS2-Servers launcher -- servers on this machine ({}):".format(platform.system()))
    for key, s in REGISTRY.items():
        mark = "OK " if s.is_available() else "n/a"
        print("  [{}] {:7s} {:30s} port {:8s} ({})".format(
            mark, key, s.label, s.port_display(), s.runtime))


if __name__ == "__main__":
    sys.exit(main())
