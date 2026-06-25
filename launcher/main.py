"""Launcher entry point.

Normally this opens the GUI (Milestone 2). It also understands two flags used
internally and for testing:

  --serve <key> [args...]   run one server in this process (re-exec target)
  --list                    print the servers available on this machine
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

    try:
        from .gui import run_gui
    except ImportError as e:  # Tkinter not present in this Python build
        print("GUI unavailable ({}). Servers on this machine:".format(e))
        _print_list()
        return 1
    return run_gui()


def _print_list():
    from .servers import REGISTRY

    print("PS2-Servers launcher -- servers on this machine ({}):".format(platform.system()))
    for key, s in REGISTRY.items():
        mark = "OK " if s.is_available() else "n/a"
        print("  [{}] {:7s} {:30s} port {:8s} ({})".format(
            mark, key, s.label, s.port_display(), s.runtime))


if __name__ == "__main__":
    sys.exit(main())
