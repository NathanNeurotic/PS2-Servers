"""Managed xeRAbora/rcheevos runtime and Caduceus protocol adapter.

RA credentials belong to the engine's private profile, never launcher.json or
command line arguments. The native engine exits if this supervisor disappears.
"""
import argparse
import ctypes
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading

from launcher.config import config_dir
from launcher.servers import is_frozen


def engine_path():
    root = Path(__file__).resolve().parents[1]
    name = "ps2ra.exe" if os.name == "nt" else "ps2ra"
    return root / ("native" if is_frozen()
                   else "build/native") / name


def profile_dir():
    # Windows local, not roaming; independent xeRAbora/Caduceus are untouched.
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", config_dir())) / "PS2-Servers"
    else:
        base = Path(config_dir())
    directory = base / "retroachievements"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    return directory


def account_url():
    try:
        port = int((profile_dir() / "account-port").read_text("ascii"))
        if 1024 <= port <= 65535:
            return "http://127.0.0.1:{}/".format(port)
    except (OSError, ValueError):
        pass
    return "http://127.0.0.1:18196/"


def claim_service(profile):
    lease = open(profile / "service.lock", "a+b")
    try:
        if os.name == "nt":
            import msvcrt
            if not lease.tell():
                lease.write(b"\0")
                lease.flush()
            lease.seek(0)
            msvcrt.locking(lease.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return lease
    except BaseException:
        lease.close()
        raise


def available_account_port():
    with socket.socket() as probe:
        if os.name == "nt":
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            probe.bind(("127.0.0.1", 18196))
        except OSError:
            probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def bootstrap_owner():
    """Hold the Windows onefile bootstrap identity until this service exits."""
    pid = os.environ.get("NUITKA_ONEFILE_PARENT", "")
    if os.name != "nt" or not is_frozen() or not pid.isdecimal():
        return lambda: True, lambda: None
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x00100000, False, int(pid))  # SYNCHRONIZE
    return (lambda: bool(handle) and kernel.WaitForSingleObject(handle, 0) == 258,
            lambda: kernel.CloseHandle(handle) if handle else None)


def main(argv=None):
    parser = argparse.ArgumentParser(description="RetroAchievements for real PS2 consoles")
    parser.add_argument("--mode", choices=("xerabora", "caduceus"), default="xerabora")
    parser.add_argument("--games-folder", type=Path)
    parser.add_argument("--no-sound", action="store_true")
    parser.add_argument("--obs-folder", type=Path)
    parser.add_argument("--lan-viewer", action="store_true")
    args = parser.parse_args(argv)
    binary = engine_path()
    if not binary.is_file():
        parser.error("The bundled achievement engine is missing. Source users: run python build/build_achievements.py.")
    if args.mode == "caduceus" and (not args.games_folder or not args.games_folder.is_dir()):
        parser.error("Caduceus needs the existing OPL games folder for its ART/CADUCEUS.KEY pairing file.")
    profile = profile_dir()
    try:
        lease = claim_service(profile)
    except OSError:
        parser.error("RetroAchievements is already running for this profile.")
    env = dict(os.environ, PS2SERVERS_RA_PROFILE=str(profile), PS2SERVERS_RA_PARENT=str(os.getpid()),
               PS2SERVERS_RA_MODE=args.mode)
    ui_port = available_account_port()
    command = [str(binary), "--port", "18194", "--ui-port", str(ui_port)]
    if args.no_sound:
        command.append("--no-sound")
    if args.obs_folder:
        args.obs_folder.mkdir(parents=True, exist_ok=True)
        command += ["--obs", str(args.obs_folder.resolve())]
    stopped = threading.Event()
    for name in ("SIGINT", "SIGTERM"):
        signal.signal(getattr(signal, name), lambda *_: stopped.set())
    bridge = None
    viewer = None
    owner_alive, close_owner = bootstrap_owner()
    try:
        (profile / "account-port").write_text(str(ui_port), encoding="ascii")
        if args.lan_viewer:
            from launcher.achievement_viewer import Viewer
            viewer = Viewer(ui_port)
            viewer.start()
            print("Read-only achievements viewer: http://<this PC's LAN IP>:18199/", flush=True)
        if args.mode == "caduceus":
            from launcher.caduceus import Bridge
            bridge = Bridge(profile, args.games_folder, account_port=ui_port)
            bridge.start()
        print("RetroAchievements: {} console compatibility; softcore only.".format(args.mode), flush=True)
        print("Desktop engine: bundled PS2-Servers-managed xeRAbora/rcheevos; this is not a separate Caduceus engine.", flush=True)
        print("Account, achievements and leaderboards: {}".format(account_url()), flush=True)
        with subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) as engine:
            while engine.poll() is None and not stopped.wait(0.25) and owner_alive():
                pass
            if engine.poll() is None:
                engine.terminate()
                try:
                    engine.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    engine.kill()
                    engine.wait()
            return engine.returncode
    finally:
        if viewer:
            viewer.close()
        if bridge:
            bridge.close()
        (profile / "account-port").unlink(missing_ok=True)
        lease.close()
        close_owner()


if __name__ == "__main__":
    raise SystemExit(main())
