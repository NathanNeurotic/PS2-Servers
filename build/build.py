#!/usr/bin/env python3
"""Build a single-file PS2 Servers executable with Nuitka.

    python -m pip install nuitka
    python build/build.py

Produces dist/PS2Servers (.exe on Windows) that bundles the Tkinter launcher and
all three Python servers, so an end user needs no Python install -- they double
click one file.

Packaging mode (Windows/Linux) is controlled by the PS2_BUILD_MODE env var:

    PS2_BUILD_MODE=onefile     (default) a single self-extracting executable
    PS2_BUILD_MODE=standalone  a plain folder of the exe + its libraries

The standalone folder has no self-extraction bootstrap, which antivirus and
SmartScreen heuristics flag far less often, so releases publish it as an
alternative download for users whose AV trips on the onefile. macOS always
builds a standalone .app bundle (a Tkinter GUI needs one to show its window).

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
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from launcher import app_icon, release_metadata
from launcher.servers import REGISTRY

# Support modules that a server entry point imports by name rather than being
# an entry point itself, so they cannot be discovered from the registry.
# udpfs_server/ps2servers_core.py does `from udpfs_server import (...)`.
SUPPORT_FILES = [
    "udpfs_server/udpfs_server.py",
    # udpfs_server.py does `import router_status` on load. Without this the
    # packaged build imports fine in source mode and fails at runtime, which is
    # exactly the failure tests/test_packaging_data_files.py exists to catch.
    "udpfs_server/router_status.py",
    # smb2_server.py imports all four of these at load. _server_entry_points
    # only sees the entry point itself, so a sibling module missing here is a
    # packaged build whose SMBv2/SMBv3 modes die on ImportError at start.
    "smb2_server/smb2_wire.py",
    "smb2_server/smb2_spnego.py",
    "smb2_server/smb2_auth.py",
    "smb2_server/smb2_paths.py",
]


def _server_entry_points():
    """Every Python server module the launcher loads by file path at runtime.

    Derived from REGISTRY rather than hand-listed: this list previously drifted
    when the UDPFS entry point moved to ps2servers_core.py and build.py was not
    updated, which shipped a packaged build whose UDPFS mode died with
    FileNotFoundError on that exact file. Reading the registry means adding or
    moving a server can no longer silently break the packaged app.
    """
    paths = set()
    for server in REGISTRY.values():
        if server.runtime != "python" or not server.module_file:
            continue
        rel = os.path.relpath(server.module_file, ROOT).replace(os.sep, "/")
        paths.add(rel)
    return paths


# Server sources shipped as data at their original relative paths -- the launcher
# loads these by file path at runtime (Nuitka can't see those dynamic imports).
DATA_FILES = sorted(_server_entry_points() | set(SUPPORT_FILES))

_missing = [rel for rel in DATA_FILES if not os.path.isfile(os.path.join(ROOT, rel))]
if _missing:
    raise SystemExit(
        "build.py: server sources missing from the tree: " + ", ".join(_missing))
# udpfs_server.py does `from compressed_iso import ...` on load. --include-data-dir
# skips .py files, so compile it in as a real package instead (importable anywhere).
# lz4 is optional in source mode, but bundled in packaged releases so normal users
# do not need Python or pip for ZSO/LZ4 support.
INCLUDE_PACKAGES = [
    "compressed_iso",
    "lz4",
]

# Optional Linux system-tray backend. pystray (+ Pillow for the icon, + the
# pure-Python Xlib xorg fallback) is bundled so the packaged Linux build has a
# working tray. Each is included only if actually installed in the build
# environment, so a build without them still succeeds and the launcher simply
# degrades to close-to-quit. pystray's AppIndicator/GTK backends import `gi` at
# runtime from the system (Nuitka can't bundle a gobject-introspection stack);
# where gi is absent, pystray falls back to the bundled xorg backend.
LINUX_TRAY_PACKAGES = ["pystray", "PIL", "Xlib", "six"]


def _installed(module_name):
    import importlib.util
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, AttributeError, ValueError):
        return False


def main():
    system = platform.system()
    out = (release_metadata.WINDOWS_EXE_NAME if system == "Windows"
           else release_metadata.EXECUTABLE_BASENAME)
    icon_path = app_icon.write_ico(os.path.join(ROOT, "build", "PS2Servers.ico"))

    cmd = [
        sys.executable, "-m", "nuitka",
        "--enable-plugin=tk-inter",
        "--assume-yes-for-downloads",
        # The launcher re-executes this same binary with '--serve <key> ...'
        # to run a server child. Nuitka's self-execution guard would abort
        # (exit 2) if any bare '-c'/'-m' ever appears in that argv, so opt
        # this one deliberate self-exec pattern out of the guard.
        "--no-deployment-flag=self-execution",
        "--output-dir=" + os.path.join(ROOT, "dist"),
        "--output-filename=" + out,
        "--company-name=" + release_metadata.COMPANY_NAME,
        "--product-name=" + release_metadata.PRODUCT_NAME,
        "--product-version=" + release_metadata.PRODUCT_VERSION,
        "--file-version=" + release_metadata.FILE_VERSION,
        "--file-description=" + release_metadata.FILE_DESCRIPTION,
        "--copyright=" + release_metadata.COPYRIGHT,
    ]
    for rel in DATA_FILES:
        cmd.append("--include-data-files={}={}".format(os.path.join(ROOT, rel), rel))

    native_dir = os.path.join(ROOT, "build", "native")
    if os.path.isdir(native_dir):
        cmd.append("--include-data-dir={}={}".format(native_dir, "native"))

    theme_asset_dir = os.path.join(ROOT, "launcher", "assets", "theme")
    if os.path.isdir(theme_asset_dir):
        cmd.append("--include-data-dir={}={}".format(
            theme_asset_dir, "launcher/assets/theme"))

    for pkg in INCLUDE_PACKAGES:
        cmd.append("--include-package=" + pkg)

    if system == "Linux":
        for pkg in LINUX_TRAY_PACKAGES:
            if _installed(pkg):
                cmd.append("--include-package=" + pkg)
        # gi (PyGObject) is a system gobject-introspection stack, not a bundle-
        # able Python package; pystray imports it only at runtime. Tell Nuitka
        # not to try to follow it so the build never fails on a missing gi.
        cmd.append("--nofollow-import-to=gi")

    build_mode = os.environ.get("PS2_BUILD_MODE", "onefile").strip().lower()

    if system == "Darwin":
        # a Tkinter GUI needs a .app bundle on macOS, or its window opens in the
        # background with no dock / menu-bar presence. CI zips the .app for release.
        cmd += ["--standalone", "--macos-create-app-bundle",
                "--macos-app-name=PS2 Servers"]
        # optional cross-build target (e.g. x86_64 on an arm64 runner). Needs a
        # universal2 Python so Nuitka has the target-arch slice of every lib.
        arch = os.environ.get("MACOS_TARGET_ARCH")
        if arch:
            cmd.append("--macos-target-arch=" + arch)
    elif build_mode == "standalone":
        # A plain folder (dist/ps2servers.dist/) of the exe + libraries. No
        # self-extraction bootstrap -> softer on AV/SmartScreen heuristics.
        cmd.append("--standalone")
    else:
        cmd.append("--onefile")

    if system == "Windows":
        cmd.append("--windows-console-mode=disable")
        cmd.append("--windows-icon-from-ico=" + icon_path)

    cmd.append(os.path.join(ROOT, "ps2servers.py"))

    # compressed_iso lives under udpfs_server/, so make it importable for Nuitka.
    # Avoid a trailing os.pathsep (an empty entry means CWD, which can shadow imports).
    env = os.environ.copy()
    extra = os.path.join(ROOT, "udpfs_server")
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = extra + os.pathsep + existing if existing else extra

    print("Running:\n  " + " \\\n  ".join(cmd) + "\n")
    return subprocess.call(cmd, cwd=ROOT, env=env)


if __name__ == "__main__":
    sys.exit(main())
