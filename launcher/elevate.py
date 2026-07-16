"""Windows UAC elevation for setup tasks that need admin rights.

The launcher relaunches itself elevated instead of trying to manage one elevated
child process. That keeps server logs, shutdown, and tray handling normal.
"""

import os
import platform
import sys

from . import config
from .servers import REPO_ROOT, frozen_self_exe, is_frozen


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


def unix_privileged_tool(os_name=None):
    """The GUI privilege-escalation tool for this Unix, or None if absent.

    Linux uses pkexec (polkit's graphical prompt); macOS uses osascript's
    'with administrator privileges' dialog. Returns the tool name if it looks
    usable, else None so the caller can explain what to install.
    """
    import shutil
    os_name = os_name or platform.system()
    if os_name == "Linux":
        return "pkexec" if shutil.which("pkexec") else None
    if os_name == "Darwin":
        return "osascript" if shutil.which("osascript") else None
    return None


def unix_privileged_command(argv, os_name=None):
    """Wrap argv so it runs as root behind a graphical password prompt.

    Returns a new command list to hand to Popen. Linux: `pkexec <argv>`.
    macOS: an osascript 'do shell script ... with administrator privileges'
    that runs the (shell-quoted) argv. Both keep the elevated process in the
    foreground so the launcher can track its lifetime; the argv is shell-quoted
    (macOS) so paths and ids with spaces survive.
    """
    os_name = os_name or platform.system()
    argv = list(argv)
    if os_name == "Linux":
        # --disable-internal-agent off (default): use the desktop polkit agent.
        return ["pkexec"] + argv
    if os_name == "Darwin":
        import shlex
        inner = " ".join(shlex.quote(a) for a in argv)
        # Escape for an AppleScript double-quoted string literal.
        escaped = inner.replace("\\", "\\\\").replace('"', '\\"')
        script = ('do shell script "{}" with administrator privileges'
                  .format(escaped))
        return ["osascript", "-e", script]
    raise RuntimeError(
        "no graphical privilege escalation is defined for this OS")


def _clear_pending_start():
    """Remove a saved post-elevation auto-start request if relaunch fails."""
    try:
        data = config.load()
        if "pending_start" in data:
            data.pop("pending_start", None)
            config.save(data)
    except Exception:
        pass


def relaunch_as_admin():
    """Relaunch the launcher elevated via a UAC prompt. Returns True if launched."""
    if platform.system() != "Windows":
        return False
    try:
        import ctypes
        if is_frozen():
            exe, params, cwd = frozen_self_exe(), "", None
        else:  # from source: re-run `python -m launcher` from the repo root
            exe, params, cwd = sys.executable, "-m launcher", REPO_ROOT
        # ShellExecuteW returns a value > 32 on success.
        rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, cwd, 1)
        if rc > 32:
            return True
        _clear_pending_start()
        return False
    except Exception:
        _clear_pending_start()
        return False
