"""Managed xeRAbora/rcheevos runtime and Caduceus protocol adapter.

RA credentials belong to the engine's private profile, never launcher.json or
command line arguments. The native engine exits if this supervisor disappears.
"""
import argparse
import os
from pathlib import Path
import signal
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


def main(argv=None):
    parser = argparse.ArgumentParser(description="RetroAchievements for real PS2 consoles")
    parser.add_argument("--mode", choices=("xerabora", "caduceus"), default="xerabora")
    parser.add_argument("--games-folder", type=Path)
    parser.add_argument("--no-sound", action="store_true")
    args = parser.parse_args(argv)
    binary = engine_path()
    if not binary.is_file():
        parser.error("The bundled achievement engine is missing. Source users: run python build/build_achievements.py.")
    if args.mode == "caduceus" and (not args.games_folder or not args.games_folder.is_dir()):
        parser.error("Caduceus needs the existing OPL games folder for its ART/CADUCEUS.KEY pairing file.")
    profile = profile_dir()
    env = dict(os.environ, PS2SERVERS_RA_PROFILE=str(profile), PS2SERVERS_RA_PARENT=str(os.getpid()),
               PS2SERVERS_RA_MODE=args.mode)
    command = [str(binary), "--port", "18194", "--ui-port", "18196"]
    if args.no_sound:
        command.append("--no-sound")
    stopped = threading.Event()
    for name in ("SIGINT", "SIGTERM"):
        signal.signal(getattr(signal, name), lambda *_: stopped.set())
    bridge = None
    try:
        if args.mode == "caduceus":
            from launcher.caduceus import Bridge
            bridge = Bridge(profile, args.games_folder)
            bridge.start()
        print("RetroAchievements: {} mode; softcore only.".format(args.mode), flush=True)
        print("Account, achievements and leaderboards: http://127.0.0.1:18196/", flush=True)
        with subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) as engine:
            while engine.poll() is None and not stopped.wait(0.25):
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
        if bridge:
            bridge.close()


if __name__ == "__main__":
    raise SystemExit(main())
