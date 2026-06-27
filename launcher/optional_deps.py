"""Optional compression dependency checks and safe source-mode setup."""

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
    return bool(getattr(sys, "frozen", False)) or ("__compiled__" in globals())


def _in_venv():
    base_prefix = getattr(sys, "base_prefix", sys.prefix)
    real_prefix = getattr(sys, "real_prefix", None)
    return bool(real_prefix) or sys.prefix != base_prefix


def check_lz4():
    try:
        has_lz4 = importlib.util.find_spec("lz4") is not None
        has_block = has_lz4 and importlib.util.find_spec("lz4.block") is not None
    except (ImportError, AttributeError, ValueError):
        has_block = False
    if has_block:
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
    cmd = [sys.executable, "-m", "pip", "install"]
    if not _in_venv():
        cmd.append("--user")
    cmd.append("lz4")
    return cmd


def install_lz4(log: Optional[Callable[[str], None]] = None):
    if is_frozen_app():
        raise RuntimeError(
            "This packaged app cannot install Python packages into itself. "
            "Use a release build that bundles lz4, or run from source to install lz4."
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


def libchdr_setup_text():
    system = platform.system()
    if system == "Windows":
        return (
            "CHD support uses the native libchdr library. Windows release builds should "
            "bundle a vetted libchdr binary. This launcher does not download native binaries."
        )
    if system == "Darwin":
        return (
            "CHD support uses the native libchdr library. If Homebrew is installed, install "
            "libchdr with: brew install libchdr. If Homebrew is missing, install Homebrew first."
        )
    if system == "Linux":
        return (
            "CHD support uses the native libchdr library. Install the package that provides "
            "libchdr.so through your distribution package manager."
        )
    return "CHD support needs libchdr installed on the system library path."
