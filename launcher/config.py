"""Persisted launcher settings (last-used folders, ports, toggles).

Stored as JSON in the per-user config location for each OS so the GUI can
remember what the user picked between runs.
"""

import json
import os
import platform
import sys

APP_NAME = "PS2-Servers"


PORTABLE_MARKER = "portable.txt"
PORTABLE_DATA_DIR = "data"


def _app_dir():
    """Folder holding the program: the executable when frozen, else the repo."""
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        for candidate in (os.environ.get("NUITKA_ONEFILE_BINARY"), sys.argv[0] if sys.argv else None):
            if candidate:
                path = os.path.abspath(candidate)
                if os.path.exists(path):
                    return os.path.dirname(path)
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _portable_dir():
    """<app folder>/data when a portable.txt marker sits beside the program and
    that folder is writable, else None (the per-user location is used)."""
    root = _app_dir()
    if not os.path.isfile(os.path.join(root, PORTABLE_MARKER)):
        return None
    directory = os.path.join(root, PORTABLE_DATA_DIR)
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError:
        return None
    return directory if os.access(directory, os.W_OK) else None


def _user_config_dir():
    system = platform.system()
    if system == "Windows":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, APP_NAME)
    if system == "Darwin":
        return os.path.join(os.path.expanduser("~/Library/Application Support"), APP_NAME)
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "ps2-servers")


def is_portable():
    return _portable_dir() is not None


def config_dir():
    return _portable_dir() or _user_config_dir()


def config_path():
    return os.path.join(config_dir(), "launcher.json")


def load():
    path = config_path()
    if not os.path.exists(path) and is_portable():
        # one-time fallback: settings a portable build saved to the per-user
        # folder before it kept them beside the program
        path = os.path.join(_user_config_dir(), "launcher.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError, OSError):
        return {}


def save(data):
    directory = config_dir()
    os.makedirs(directory, exist_ok=True)
    path = config_path()
    tmp = path + ".tmp"
    # write to a temp file then atomically replace, so a crash mid-write can't
    # leave a truncated/corrupt config behind
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        # clean up the temp file on any failure (e.g. non-serializable value)
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def reset():
    """Delete the persisted settings file. A missing file is already 'reset',
    so it is not an error."""
    try:
        os.remove(config_path())
    except FileNotFoundError:
        pass
