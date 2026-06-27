"""Optional compression dependency checks and guided installation helpers.

These dependencies are optional because only compressed-image paths need them:

- lz4: Python package used for ZSO/LZ4 decompression.
- libchdr: native library used for CHD decompression, loaded dynamically.

The launcher exposes these helpers through the GUI so users do not need to type
commands just to understand what is available.
"""

import ctypes
import ctypes.util
import importlib.util
import os
import platform
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable, List, Optional


@dataclass
class OptionalDepStatus:
    key: str
    label: str
    available: bool
    detail: str


def is_frozen_app():
    """True when running as a packaged single-file app."""
    return bool(getattr(sys, "frozen", False)) or ("__compiled__" in globals())


def _in_venv():
    base_prefix = getattr(sys, "base_prefix", sys.prefix)
    real_prefix = getattr(sys, "real_prefix", None)
    return bool(real_prefix) or sys.prefix != base_prefix


def check_lz4():
    if importlib.util.find_spec("lz4.block") is not None:
        return OptionalDepStatus("lz4", "ZSO/LZ4 support", True,
                                 "Python package 'lz4' is available.")
    return OptionalDepStatus("lz4", "ZSO/LZ4 support", False,
                             "Python package 'lz4' is not available.")


def _try_load_library(candidates):
    errors = []
    for candidate in candidates:
        if not candidate:
            continue
        try:
            ctypes.cdll.LoadLibrary(candidate)
            return candidate, None
        except OSError as e:
            errors.append("{}: {}".format(candidate, e))
    return None, "; ".join(errors)


def check_libchdr():
    found = ctypes.util.find_library("chdr")
    candidates = [found, "libchdr.so.0", "libchdr.so", "libchdr.dylib", "chdr.dll", "libchdr.dll"]
    loaded, error = _try_load_library(candidates)
    if loaded:
        return OptionalDepStatus("libchdr", "CHD support", True,
                                 "libchdr is available ({})".format(loaded))
    detail = "libchdr was not found."
    if error:
        detail += " Last loader errors: {}".format(error)
    return OptionalDepStatus("libchdr", "CHD support", False, detail)


def check_all():
    return [check_lz4(), check_libchdr()]


def format_statuses(statuses=None):
    statuses = statuses or check_all()
    lines = []
    for status in statuses:
        marker = "OK" if status.available else "Missing"
        lines.append("{}: {} — {}".format(marker, status.label, status.detail))
    return "\n".join(lines)


def lz4_install_command():
    """Return the pip command used for source-mode lz4 installation."""
    cmd = [sys.executable, "-m", "pip", "install"]
    if not _in_venv():
        cmd.append("--user")
    cmd.append("lz4")
    return cmd


def install_lz4(log: Optional[Callable[[str], None]] = None):
    """Install the Python lz4 package for source-mode runs.

    Packaged single-file apps cannot be modified with pip, so this deliberately
    refuses to run in frozen builds. Release builds should bundle lz4 if always-on
    ZSO support is desired.
    """
    if is_frozen_app():
        raise RuntimeError(
            "This packaged app cannot install Python packages into itself. "
            "Run from source to install lz4, or use a release build that bundles lz4."
        )

    cmd = lz4_install_command()
    if log:
        log("Running: {}".format(" ".join(cmd)))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, cwd=os.getcwd())
    output_lines: List[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip("\n")
        output_lines.append(line)
        if log:
            log(line)
    rc = proc.wait()
    output = "\n".join(output_lines)
    if rc != 0:
        raise RuntimeError("pip install lz4 failed with exit code {}.\n{}".format(rc, output))
    return output or "lz4 installed."


def libchdr_help_text():
    system = platform.system()
    if system == "Windows":
        return (
            "CHD support needs a compatible libchdr DLL available to PS2 Servers.\n\n"
            "Windows automatic install is not offered because libchdr is a native DLL, "
            "not a Python package, and silently dropping DLLs beside a network tool is "
            "bad security practice.\n\n"
            "Safe options:\n"
            "- use a release build that explicitly bundles libchdr; or\n"
            "- place a trusted chdr.dll/libchdr.dll where the app can load it, such as beside "
            "the executable or on PATH."
        )
    if system == "Darwin":
        return (
            "CHD support needs libchdr. On macOS, install it with your package manager "
            "if available, then restart PS2 Servers.\n\n"
            "Common Homebrew path:\n"
            "  brew install libchdr\n\n"
            "If your package manager uses a different package name, install the package that "
            "provides libchdr.dylib."
        )
    if system == "Linux":
        return (
            "CHD support needs libchdr. Install the package that provides libchdr.so, then "
            "restart PS2 Servers.\n\n"
            "Common examples:\n"
            "  Debian/Ubuntu: sudo apt install libchdr0\n"
            "  Arch:          sudo pacman -S libchdr\n"
            "  Fedora:        sudo dnf install libchdr\n\n"
            "Package names can vary by distribution."
        )
    return "CHD support needs libchdr installed on the system library path."
