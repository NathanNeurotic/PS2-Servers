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
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable, List, Optional

APP_DIR_NAME = "PS2 Servers"
_DLL_DIR_HANDLES = []


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


def app_data_dir():
    system = platform.system()
    if system == "Windows":
        base = os.environ.get("APPDATA") or os.path.expanduser("~\\AppData\\Roaming")
        return os.path.join(base, APP_DIR_NAME)
    if system == "Darwin":
        return os.path.expanduser("~/Library/Application Support/{}".format(APP_DIR_NAME))
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "ps2-servers")


def native_lib_dir():
    return os.path.join(app_data_dir(), "native")


def ensure_native_lib_path():
    """Make the per-user native library folder visible to current process loads."""
    path = native_lib_dir()
    os.environ["PS2SERVERS_NATIVE_LIB_DIR"] = path
    if os.path.isdir(path):
        current = os.environ.get("PATH", "")
        parts = current.split(os.pathsep) if current else []
        if path not in parts:
            os.environ["PATH"] = path + (os.pathsep + current if current else "")
        if platform.system() == "Windows" and hasattr(os, "add_dll_directory"):
            try:
                handle = os.add_dll_directory(path)
                _DLL_DIR_HANDLES.append(handle)
            except (OSError, AttributeError):
                pass
    return path


def _native_lib_candidates(names):
    folder = ensure_native_lib_path()
    return [os.path.join(folder, name) for name in names]


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


def libchdr_names():
    system = platform.system()
    if system == "Windows":
        return ["chdr.dll", "libchdr.dll"]
    if system == "Darwin":
        return ["libchdr.dylib"]
    return ["libchdr.so.0", "libchdr.so"]


def check_libchdr():
    system_names = libchdr_names()
    found = ctypes.util.find_library("chdr")
    candidates = _native_lib_candidates(system_names)
    if found:
        candidates.append(found)
    candidates.extend(system_names)
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


def missing_statuses(statuses=None):
    statuses = statuses or check_all()
    return [status for status in statuses if not status.available]


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


def _run_command(cmd, log: Optional[Callable[[str], None]] = None, env=None, cwd=None):
    if log:
        log("Running: {}".format(" ".join(cmd)))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, cwd=cwd or os.getcwd(), env=env)
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
        raise RuntimeError("Command failed with exit code {}: {}\n{}".format(rc, " ".join(cmd), output))
    return output


def install_lz4(log: Optional[Callable[[str], None]] = None):
    """Install the Python lz4 package for source-mode runs."""
    if is_frozen_app():
        raise RuntimeError(
            "This packaged app cannot install Python packages into itself. "
            "Use a release build that bundles lz4, or run from source to install lz4."
        )
    output = _run_command(lz4_install_command(), log=log)
    return output or "lz4 installed."


def command_exists(name):
    return shutil.which(name) is not None


def brew_path():
    found = shutil.which("brew")
    if found:
        return found
    for candidate in ("/opt/homebrew/bin/brew", "/usr/local/bin/brew"):
        if os.path.exists(candidate):
            return candidate
    return None


def install_macos_libchdr(log: Optional[Callable[[str], None]] = None):
    brew = brew_path()
    if not brew:
        raise RuntimeError("Homebrew is not installed, so PS2 Servers cannot run brew install libchdr yet.")
    output = _run_command([brew, "install", "libchdr"], log=log)
    return output or "libchdr installed with Homebrew."


def linux_libchdr_command() -> Optional[List[str]]:
    if command_exists("apt-get"):
        base = ["apt-get", "install", "-y", "libchdr0"]
    elif command_exists("dnf"):
        base = ["dnf", "install", "-y", "libchdr"]
    elif command_exists("pacman"):
        base = ["pacman", "-S", "--needed", "--noconfirm", "libchdr"]
    else:
        return None

    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return base
    if command_exists("pkexec"):
        return ["pkexec"] + base
    if command_exists("sudo"):
        return ["sudo"] + base
    return base


def install_linux_libchdr(log: Optional[Callable[[str], None]] = None):
    cmd = linux_libchdr_command()
    if not cmd:
        raise RuntimeError("No supported Linux package manager found for libchdr setup.")
    output = _run_command(cmd, log=log)
    return output or "libchdr install command completed."


def install_windows_libchdr_from_file(src_path, log: Optional[Callable[[str], None]] = None):
    if not src_path:
        raise RuntimeError("No libchdr DLL was selected.")
    name = os.path.basename(src_path)
    lower = name.lower()
    if not lower.endswith(".dll") or "chdr" not in lower:
        raise RuntimeError("Select a trusted chdr.dll or libchdr.dll file.")

    dest_dir = native_lib_dir()
    os.makedirs(dest_dir, exist_ok=True)
    dest_name = "chdr.dll" if lower == "chdr.dll" else "libchdr.dll"
    dest_path = os.path.join(dest_dir, dest_name)
    if log:
        log("Copying {} to {}".format(src_path, dest_path))
    shutil.copy2(src_path, dest_path)
    ensure_native_lib_path()
    loaded, error = _try_load_library([dest_path])
    if not loaded:
        raise RuntimeError("Copied DLL, but Windows could not load it. {}".format(error or ""))
    return "Installed CHD support DLL to {}".format(dest_path)


def install_libchdr_for_platform(windows_dll_path=None, log: Optional[Callable[[str], None]] = None):
    system = platform.system()
    if system == "Windows":
        return install_windows_libchdr_from_file(windows_dll_path, log=log)
    if system == "Darwin":
        return install_macos_libchdr(log=log)
    if system == "Linux":
        return install_linux_libchdr(log=log)
    raise RuntimeError("Automatic libchdr setup is not available on this platform.")


def libchdr_setup_text():
    system = platform.system()
    if system == "Windows":
        return (
            "CHD support needs a trusted chdr.dll or libchdr.dll.\n\n"
            "Use Install missing dependencies, then select the DLL. PS2 Servers will copy it "
            "into its per-user support folder and load it from there."
        )
    if system == "Darwin":
        if brew_path():
            return "Homebrew is available. PS2 Servers can run: brew install libchdr"
        return (
            "Homebrew is not installed. Install Homebrew first, then click Install missing "
            "dependencies again so PS2 Servers can run brew install libchdr."
        )
    if system == "Linux":
        cmd = linux_libchdr_command()
        if cmd:
            return "PS2 Servers can run: {}".format(" ".join(cmd))
        return "No supported Linux package manager was detected for automatic libchdr setup."
    return "CHD support needs libchdr installed on the system library path."
