"""Smoke-test actual desktop packages without an RA account or game image."""
import json
from contextlib import contextmanager
import os
from pathlib import Path
import platform
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from launcher.achievements import claim_service


def package_command():
    dist = ROOT / "dist"
    if platform.system() == "Darwin":
        return [str(next(dist.glob("*.app/Contents/MacOS/PS2Servers")))]
    name = "PS2Servers.exe" if os.name == "nt" else "PS2Servers"
    if os.environ.get("PS2_BUILD_MODE") == "standalone":
        return [str(next(dist.glob("*.dist/" + name)))]
    return [str(dist / name)]


@contextmanager
def temporary_profile():
    directory = tempfile.TemporaryDirectory()
    try:
        yield directory.name
    finally:
        # Windows retains a process's cwd until final process teardown, which
        # can follow release of the service lock and sockets. Never ignore a
        # persistent lock: a surviving child must still fail this gate.
        deadline = time.monotonic() + 15
        while True:
            try:
                directory.cleanup()
                break
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.1)


def check(command):
    for mode in ("xerabora", "caduceus"):
        with temporary_profile() as temporary:
            root = Path(temporary)
            folder = root / "games"
            folder.mkdir()
            env = dict(os.environ, LOCALAPPDATA=temporary, APPDATA=temporary,
                       XDG_CONFIG_HOME=temporary, HOME=temporary, PS2SERVERS_RA_NO_BROWSER="1")
            env.pop("PYTHONPATH", None)
            args = command + ["--serve", "retroachievements", "--mode", mode, "--no-sound"]
            if mode == "caduceus":
                args += ["--games-folder", str(folder)]
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            # The onefile child can retain stdout briefly after releasing its
            # service resources. Keep this delete-on-close file outside the
            # profile directory so Windows cleanup does not race that handle.
            with tempfile.TemporaryFile() as log, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as console:
                console.bind(("127.0.0.1", 0))
                console.settimeout(0.3)
                proc = subprocess.Popen(args, env=env, cwd=temporary, stdout=log, stderr=log,
                                        creationflags=flags)
                try:
                    deadline = time.monotonic() + 60
                    while True:
                        console.sendto(("RAP1 127.0.0.1 " + str(console.getsockname()[1])).encode(),
                                       ("127.0.0.1", 18194))
                        try:
                            if console.recvfrom(1024)[0].startswith(b"RAO1 OK"):
                                break
                        except (socket.timeout, ConnectionResetError):
                            pass
                        if time.monotonic() > deadline or proc.poll() is not None:
                            log.seek(0)
                            raise RuntimeError(log.read().decode(errors="replace"))
                    port_file = next(root.rglob("account-port"))
                    account_port = int(port_file.read_text("ascii"))
                    with urllib.request.urlopen("http://127.0.0.1:{}/state".format(account_port), timeout=3) as response:
                        state = json.load(response)
                    assert not state["login"]["ok"] and not state["lan"]["on"]
                    duplicate = subprocess.run(args, env=env, cwd=temporary, capture_output=True,
                                               timeout=30, creationflags=flags)
                    assert duplicate.returncode and b"already running" in duplicate.stderr, duplicate.stderr
                    if mode == "caduceus":
                        key = (folder / "ART/CADUCEUS.KEY").read_text("ascii").strip()
                        console.sendto(("CADA1 1 G 0 0 0 " + key).encode(), ("127.0.0.1", 18198))
                        assert console.recvfrom(1024)[0].rstrip(b"\0") == b"CADB1 1 OFFLINE"
                finally:
                    if proc.poll() is None:
                        proc.terminate()
                    proc.wait(timeout=15)
                deadline = time.monotonic() + 15
                while True:
                    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                        if os.name == "nt":
                            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                        try:
                            probe.bind(("0.0.0.0", 18194))
                            break
                        except OSError:
                            if time.monotonic() > deadline:
                                raise RuntimeError("Achievement engine survived its supervisor")
                    time.sleep(0.1)
                while True:
                    try:
                        claim_service(port_file.parent).close()
                        break
                    except OSError:
                        if time.monotonic() > deadline:
                            raise RuntimeError("Supervisor retained its profile lock")
                        time.sleep(0.1)
            print(mode + ": packaged startup, discovery, pairing, duplicate rejection and shutdown passed")


if __name__ == "__main__":
    check(sys.argv[1:] or package_command())
