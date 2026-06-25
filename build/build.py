#!/usr/bin/env python3
"""Build a single-file PS2 Servers executable with Nuitka.

    python -m pip install nuitka
    python build/build.py

Produces dist/PS2Servers (.exe on Windows) that bundles the Tkinter launcher and
all three Python servers, so an end user needs no Python install -- they double
click one file.

How the bundling works
----------------------
The launcher loads each server module *by file path at runtime* (see
launcher/serve.py), and UDPFS imports its `compressed_iso` package. Nuitka cannot
see those dynamic imports, so we ship the server sources as data laid out at the
same relative paths the launcher expects (REPO_ROOT/<server_dir>/...). Inside the
onefile bundle REPO_ROOT resolves to the extraction dir, so the same path logic
works unchanged.
"""

import os
import platform
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Server sources shipped as data at their original relative paths -- the launcher
# loads these by file path at runtime (Nuitka can't see those dynamic imports).
DATA_FILES = [
    "smbv1_server/smbserver_opl.py",
    "udpfs_server/udpfs_server.py",
    "udpbd_server/udpbd_server.py",
]
# udpfs_server.py does `from compressed_iso import ...` on load. --include-data-dir
# skips .py files, so compile it in as a real package instead (importable anywhere).
INCLUDE_PACKAGES = [
    "compressed_iso",
]


def main():
    system = platform.system()
    out = "PS2Servers.exe" if system == "Windows" else "PS2Servers"

    cmd = [
        sys.executable, "-m", "nuitka",
        "--onefile",
        "--enable-plugin=tk-inter",
        "--assume-yes-for-downloads",
        "--output-dir=" + os.path.join(ROOT, "dist"),
        "--output-filename=" + out,
        "--company-name=PS2-Servers",
        "--product-name=PS2 Servers",
        "--product-version=0.1.0",
        "--file-version=0.1.0",
    ]
    for rel in DATA_FILES:
        cmd.append("--include-data-files={}={}".format(os.path.join(ROOT, rel), rel))
    for pkg in INCLUDE_PACKAGES:
        cmd.append("--include-package=" + pkg)

    if system == "Windows":
        cmd.append("--windows-console-mode=disable")
    elif system == "Darwin":
        cmd.append("--macos-create-app-bundle")

    cmd.append(os.path.join(ROOT, "ps2servers.py"))

    # compressed_iso lives under udpfs_server/, so make it importable for Nuitka.
    env = os.environ.copy()
    env["PYTHONPATH"] = (os.path.join(ROOT, "udpfs_server") + os.pathsep
                         + env.get("PYTHONPATH", ""))

    print("Running:\n  " + " \\\n  ".join(cmd) + "\n")
    return subprocess.call(cmd, cwd=ROOT, env=env)


if __name__ == "__main__":
    sys.exit(main())
