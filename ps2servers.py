#!/usr/bin/env python3
"""Single-file entry point for the packaged PS2 Servers executable.

Running from source you can use `python -m launcher` instead; this top-level
script exists so Nuitka has a clean module to compile into one binary.
"""

import sys

from launcher.main import main

if __name__ == "__main__":
    sys.exit(main())
