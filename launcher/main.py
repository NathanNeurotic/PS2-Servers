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
        run_serve(rest[0], rest[1:])
        return 0

    if "--list" in argv or "-l" in argv:
        _print_list()
        return 0

    # Milestone 2 will launch the Tkinter GUI here.
    _print_list()
    print()
    print("GUI not built yet (Milestone 2). For now, start a server directly, e.g.:")
    print("  python -m launcher --serve smbv1 --share games=D:/PS2Games")
    return 0


def _print_list():
    from .servers import REGISTRY

    print("PS2-Servers launcher -- servers on this machine ({}):".format(platform.system()))
    for key, s in REGISTRY.items():
        mark = "OK " if s.is_available() else "n/a"
        print("  [{}] {:7s} {:30s} port {:8s} ({})".format(
            mark, key, s.label, s.port_display(), s.runtime))


if __name__ == "__main__":
    sys.exit(main())
