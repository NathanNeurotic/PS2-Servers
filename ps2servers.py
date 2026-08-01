#!/usr/bin/env python3
"""Single-file entry point for PS2 Servers Desktop and Core.

With no command, this opens the desktop launcher. Headless operation uses the
same bundled server modules:

    ps2servers serve udpfs --root-dir /games
    ps2servers serve udpbd /images/disk.img --read-only
    ps2servers serve smbv1 --share games=/games --read-only

The historical internal ``--serve`` spelling remains supported for packaged
re-execution and existing automation.
"""

import sys

from launcher.main import main


def _normalize_headless_alias(argv):
    args = list(argv)
    if args and args[0] == "serve":
        if len(args) < 2:
            print("error: serve requires udpfs, udpbd, or smbv1", file=sys.stderr)
            return None
        return ["--serve", args[1], *args[2:]]
    return args


if __name__ == "__main__":
    normalized = _normalize_headless_alias(sys.argv[1:])
    sys.exit(2 if normalized is None else main(normalized))
