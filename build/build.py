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

# Server sources shipped as data at their original relative paths -- the launcher
# loads these by file path at runtime (Nuitka can't see those dynamic imports).
DATA_FILES = [
    "smbv1_server/smbserver_opl.py",
    "udpfs_server/udpfs_server.py",
    "udpbd_server/udpbd_server.py",
]
# udpfs_server.py does `from compressed_iso import ...` on load. --include-data-dir
# skips .py files, so compile it in as a real package instead (importable anywhere).
# lz4 is optional in source mode, but bundled in packaged releases so normal users
# do not need Python or pip for ZSO/LZ4 support.
INCLUDE_PACKAGES = [
    "compressed_iso",
    "lz4",
]


def main():
    system = platform.system()
    out = (release_metadata.WINDOWS_EXE_NAME if system == "Windows"
           else release_metadata.EXECUTABLE_BASENAME)
    icon_path = app_icon.write_ico(os.path.join(ROOT, "build", "PS2Servers.ico"))

    cmd = [
        sys.executable, "-m", "nuitka",
        "--enable-plugin=tk-inter",
        "--assume-yes-for-downloads",
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
