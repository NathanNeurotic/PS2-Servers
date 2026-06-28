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


_DLL_DIR_HANDLES = []
_DLL_DIR_PATHS = set()
_PATH_DIRS = None
_PREPARED_NATIVE_DIRS = set()


def _normalize_path_entry(path):
    if os.path.isabs(path):
        return os.path.normcase(os.path.normpath(path))
    return os.path.normcase(os.path.abspath(path))


def _known_path_dirs():
    global _PATH_DIRS
    if _PATH_DIRS is None:
        current = os.environ.get("PATH", "")
        parts = current.split(os.pathsep) if current else []
        _PATH_DIRS = {
            _normalize_path_entry(part)
            for part in parts
            if part
        }
    return _PATH_DIRS


def _libchdr_names():
    system = platform.system()
    if system == "Windows":
        return ["chdr.dll", "libchdr.dll", "libchdr-0.dll"]
    if system == "Darwin":
        return ["libchdr.dylib", "libchdr.0.dylib"]
    return ["libchdr.so.0", "libchdr.so"]


def _candidate_native_dirs():
    package_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.dirname(package_dir)
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    cwd = os.getcwd()
    dirs = [
        os.environ.get("PS2SERVERS_NATIVE_LIB_DIR"),
        os.path.join(root_dir, "native"),
        os.path.join(root_dir, "build", "native"),
    ]

    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        dirs.append(os.path.join(frozen_root, "native"))

    dirs.extend([
        os.path.join(exe_dir, "native"),
        os.path.join(cwd, "native"),
        os.path.join(cwd, "build", "native"),
    ])

    seen = set()
    out = []
    for path in dirs:
        if not path:
            continue
        abs_path = os.path.abspath(path)
        norm = os.path.normcase(abs_path)
        if norm not in seen:
            out.append(abs_path)
            seen.add(norm)
    return out


def _prepare_native_dir(path):
    abs_path = os.path.abspath(path)
    norm_path = os.path.normcase(abs_path)
    if norm_path in _PREPARED_NATIVE_DIRS:
        return
    if not os.path.isdir(abs_path):
        return

    path_dirs = _known_path_dirs()
    if norm_path not in path_dirs:
        current = os.environ.get("PATH", "")
        os.environ["PATH"] = abs_path + (os.pathsep + current if current else "")
        path_dirs.add(norm_path)

    if platform.system() == "Windows" and hasattr(os, "add_dll_directory"):
        if norm_path in _DLL_DIR_PATHS:
            _PREPARED_NATIVE_DIRS.add(norm_path)
            return
        try:
            _DLL_DIR_HANDLES.append(os.add_dll_directory(abs_path))
            _DLL_DIR_PATHS.add(norm_path)
        except (OSError, ValueError, AttributeError):
            pass
    _PREPARED_NATIVE_DIRS.add(norm_path)


def _libchdr_candidates():
    candidates = []
    names = _libchdr_names()
    for directory in _candidate_native_dirs():
        _prepare_native_dir(directory)
        for name in names:
            candidates.append(os.path.join(directory, name))

    found = ctypes.util.find_library("chdr")
    if found:
        candidates.append(found)
    candidates.extend(names)

    seen = set()
    out = []
    for candidate in candidates:
        if not candidate:
            continue
        has_sep = os.path.sep in candidate or (os.path.altsep and os.path.altsep in candidate)
        norm = os.path.normcase(os.path.abspath(candidate)) if has_sep else candidate
        if norm not in seen:
            out.append(candidate)
            seen.add(norm)
    return out


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
    loaded, error = _try_load_library(_libchdr_candidates())
    if loaded:
        return OptionalDepStatus("libchdr", "CHD support", True,
                                 "libchdr is available ({})".format(loaded))
    detail = "libchdr was not found."
    if error:
        detail += " Last loader errors: {}".format(error)
    return OptionalDepStatus("libchdr", "CHD support", False, detail)


def check_all():
    return [check_lz4(), check_libchdr()]


def summarize_statuses(statuses):
    if not statuses:
        return "Unknown"
    missing = [status for status in statuses if not status.available]
    if not missing:
        return "Ready"
    if is_frozen_app():
        return "Limited"
    if all(status.key == "libchdr" for status in missing):
        return "Limited"
    return "Needs setup"


def _split_loader_errors(detail):
    marker = " Last loader errors: "
    if marker not in detail:
        return detail, []
    summary, raw_errors = detail.split(marker, 1)
    errors = [part.strip() for part in raw_errors.split("; ") if part.strip()]
    return summary, errors


def _shorten(text, limit=220):
    if len(text) <= limit:
        return text
    return text[:limit - 3].rstrip() + "..."


def _status_line(status):
    state = "Ready" if status.available else "Missing"
    detail, loader_errors = _split_loader_errors(status.detail)
    if loader_errors:
        detail = "{} {} loader attempts failed; see technical details below.".format(
            detail, len(loader_errors))
    return "{}: {} - {}".format(state, status.label, detail)


def _technical_lines(statuses):
    lines = []
    for status in statuses:
        detail, loader_errors = _split_loader_errors(status.detail)
        if loader_errors:
            lines.append("{} loader errors: {} attempts failed.".format(
                status.label, len(loader_errors)))
            lines.append("Last loader error: {}".format(_shorten(loader_errors[-1])))
        elif not status.available:
            lines.append("{}: {}".format(status.label, detail))
    return lines


def format_statuses(statuses=None):
    statuses = statuses or check_all()
    lines = ["Compression support: {}".format(summarize_statuses(statuses)), ""]
    for status in statuses:
        lines.append(_status_line(status))
    technical = _technical_lines(statuses)
    if technical:
        lines.extend(["", "Technical details:"])
        lines.extend(technical)
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
                            text=True, errors="replace", cwd=os.getcwd())
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
    if is_frozen_app():
        return (
            "Packaged releases are expected to include CHD support. If CHD still reports "
            "missing in a packaged build, that is a release packaging problem rather than "
            "something the user should fix manually."
        )
    system = platform.system()
    if system == "Windows":
        return (
            "CHD support uses native libchdr. Source users can place a trusted libchdr DLL "
            "on PATH, but normal Windows users should use the packaged release."
        )
    if system == "Darwin":
        return (
            "CHD support uses native libchdr. Source users can install it with Homebrew: "
            "brew install libchdr. Normal macOS users should use the packaged release."
        )
    if system == "Linux":
        return (
            "CHD support uses native libchdr. Source users can install the distribution "
            "package that provides libchdr.so. Normal users should use the packaged release."
        )
    return "CHD support needs libchdr installed on the system library path."
