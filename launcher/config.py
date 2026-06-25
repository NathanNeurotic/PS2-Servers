"""Persisted launcher settings (last-used folders, ports, toggles).

Stored as JSON in the per-user config location for each OS so the GUI can
remember what the user picked between runs.
"""

import json
import os
import platform

APP_NAME = "PS2-Servers"


def config_dir():
    system = platform.system()
    if system == "Windows":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, APP_NAME)
    if system == "Darwin":
        return os.path.join(os.path.expanduser("~/Library/Application Support"), APP_NAME)
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "ps2-servers")


def config_path():
    return os.path.join(config_dir(), "launcher.json")


def load():
    try:
        with open(config_path(), "r", encoding="utf-8") as f:
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
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
