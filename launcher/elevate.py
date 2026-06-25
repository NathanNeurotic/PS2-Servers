"""Windows UAC elevation for the one option that needs admin rights.

Only SMBv1's ``--take-445`` needs administrator privileges (it pauses Windows'
LanmanServer to bind the standard port 445). Managing a single elevated child
from a non-elevated parent is painful on Windows (UIPI blocks piping and
termination), so instead we relaunch the *whole* launcher elevated -- then every
server still streams its logs and stops normally.
"""

import os
import platform
import sys

from .servers import REPO_ROOT, is_frozen


def is_admin():
    """True if this process already has administrator/root rights."""
    if platform.system() == "Windows":
        try:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    try:
        return os.geteuid() == 0
    except AttributeError:
        return False


def can_elevate():
    """Whether we can offer to relaunch elevated on this platform."""
    return platform.system() == "Windows"


def relaunch_as_admin():
    """Relaunch the launcher elevated via a UAC prompt. Returns True if launched."""
    if platform.system() != "Windows":
        return False
    try:
        import ctypes
        if is_frozen():
            exe, params, cwd = sys.executable, "", None
        else:  # from source: re-run `python -m launcher` from the repo root
            exe, params, cwd = sys.executable, "-m launcher", REPO_ROOT
        # ShellExecuteW returns a value > 32 on success.
        rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, cwd, 1)
        return rc > 32
    except Exception:
        return False
