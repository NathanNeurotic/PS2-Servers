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
import time

from launcher.config import config_dir
from launcher.ra_evaluator import library_path
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


def engine_environment(profile, mode):
    """Manage the native client without launching a second desktop window.

    The account interface is available from PS2-Servers' explicit button.
    Do not reuse a parent shell's browser-launch preference.
    """
    return dict(os.environ, PS2SERVERS_RA_PROFILE=str(profile),
                PS2SERVERS_RA_PARENT=str(os.getpid()),
                PS2SERVERS_RA_MODE=mode, PS2SERVERS_RA_NO_BROWSER="1")


def run_owned_runtime(args, profile, lease, stopped, owner_alive, close_owner):
    from launcher.ra_account_worker import AccountWorker, AccountTask
    from launcher.ra_owned_service import OwnedService, IdentificationResult
    from launcher.ra_credentials import CredentialStore
    from launcher.ra_submissions import Submissions
    from launcher.ra_http import Transport
    from launcher.ra_sets import decode_response, SetContext
    from launcher.ra_web import RAState, RAWebServer

    lib = library_path()
    if not lib.is_file():
        raise RuntimeError("The native rcheevos library is missing. Source users: run python build/build_ra_evaluator.py.")

    store = CredentialStore(str(profile))

    if getattr(args, "import_credentials", False):
        try:
            managed = store.read_managed(consent=True)
            if managed:
                from launcher.ra_account import AccountClient
                validated = AccountClient().login(managed.username, token=managed.token)
                store.save(validated)
                print(f"RetroAchievements: successfully imported credentials for {validated.username}.", flush=True)
            else:
                print("RetroAchievements: no existing managed credentials found to import.", flush=True)
        except Exception as err:
            print(f"RetroAchievements: credential import failed ({err}).", flush=True)

    if getattr(args, "login", None):
        import getpass
        username = args.login
        password = getpass.getpass(f"Password for RetroAchievements user '{username}': ")
        from launcher.ra_account import AccountClient
        try:
            validated = AccountClient().login(username, password=password)
            store.save(validated)
            print(f"RetroAchievements: successfully signed in and saved credentials for {validated.username}.", flush=True)
        except Exception as err:
            print(f"RetroAchievements: login failed ({err}).", flush=True)
            lease.close()
            close_owner()
            return 1

    profile_mode = "caduceus" if args.mode == "caduceus" else "opl"
    worker = AccountWorker(str(profile))
    active_context = None
    context_lock = threading.Lock()
    account_cancel = threading.Event()
    state = RAState()

    def resolve_job(job, cancel):
        nonlocal active_context
        if cancel.is_set() or account_cancel.is_set():
            return IdentificationResult()
        creds = store.load()
        if creds is None:
            return IdentificationResult()
        transport = Transport()
        try:
            resp = decode_response(transport.post({"r": "gameid", "m": job.image_hash}, cancel=cancel))
            game_id = resp.get("GameID")
            if type(game_id) is int and game_id == 0:
                return IdentificationResult(unsupported=True)
            if type(game_id) is not int or not 1 <= game_id <= 0xFFFFFFFF:
                return IdentificationResult()
        except Exception:
            return IdentificationResult()

        serial_lower = str(job.serial).lower()
        console_ids = (12, 21) if "vcd" in serial_lower or serial_lower.endswith(".vcd") else (21, 12)
        for console_id in console_ids:
            if cancel.is_set():
                return IdentificationResult()
            try:
                ctx = SetContext(creds.username, 1, job.image_hash, game_id, console_id)
                task = AccountTask(1, 1, "set", credentials=creds, context=ctx)
                res = worker(task, cancel, wait=True)
                if cancel.is_set():
                    return IdentificationResult()
                if res and res.status == "ok" and res.qualified:
                    try:
                        sess_task = AccountTask(2, 1, "start_session", credentials=creds, context=ctx)
                        worker(sess_task, cancel, wait=True)
                    except Exception:
                        pass
                    if cancel.is_set():
                        return IdentificationResult()
                    with context_lock:
                        if account_cancel.is_set():
                            return IdentificationResult()
                        active_context = ctx
                        achievements_data = []
                        prior_set = set(res.qualified.prior_unlocks)
                        for aid, pts, title in res.qualified.metadata:
                            achievements_data.append({
                                "id": aid,
                                "title": title,
                                "description": "",
                                "points": pts,
                                "unlocked": aid in prior_set,
                            })
                        state.set_game(
                            game_id=res.qualified.context.game_id,
                            title=res.qualified.title,
                            serial=job.serial,
                            image_hash=job.image_hash,
                            achievements=achievements_data,
                        )
                        state.update_console(connected=True, status="playing")
                    return IdentificationResult(qualified=res.qualified, profile=profile_mode)
            except Exception:
                continue
        return IdentificationResult()  # failed qualification is retryable, not no-support

    service = OwnedService(
        resolve_job,
        address=("0.0.0.0", 18194),
        library=lib,
        profile=str(profile),
    )
    service.start()
    creds = store.load()
    if creds:
        service.bind_account(creds.username, 1)
        state.set_account(creds.username, True)
        print(f"RetroAchievements: signed in as {creds.username} (owned runtime).", flush=True)
    else:
        state.set_account("", False)
        print("RetroAchievements: signed out (owned runtime). Run with --import-credentials or --login.", flush=True)
    print(f"RetroAchievements: {args.mode} console compatibility; softcore only (owned runtime).", flush=True)
    print("Desktop engine: PS2-Servers native runtime (Issue #223).", flush=True)

    bridge = None
    viewer = None
    web_server = None
    ui_port = available_account_port()
    submissions = Submissions(profile, account_cancel)
    submission_id = 0
    def logout():
        nonlocal active_context
        with context_lock:
            # Cancel the resolver's worker event before publishing local revocation.
            service.clear_account()
            account_cancel.set()
            active_context = None
        service.run_account(lambda session: session.logout(forget=True))
        if not submissions.close():
            raise TimeoutError("Submission worker is still stopping")

    try:
        (profile / "account-port").write_text(str(ui_port), encoding="ascii")
        web_server = RAWebServer(ui_port, state, logout=logout)
        web_server.start()
        print(f"Account, achievements and leaderboards: http://127.0.0.1:{ui_port}/", flush=True)
        if getattr(args, "lan_viewer", False):
            from launcher.achievement_viewer import Viewer
            viewer = Viewer(ui_port)
            viewer.start()
            print("Read-only achievements viewer: http://<this PC's LAN IP>:18199/", flush=True)
        if args.mode == "caduceus" and getattr(args, "games_folder", None):
            from launcher.caduceus import Bridge
            bridge = Bridge(profile, args.games_folder, account_port=ui_port)
            bridge.start()

        last_ping = time.monotonic()
        while not stopped.wait(0.25) and owner_alive():
            evaluated = service.take_evaluation()
            if evaluated:
                with context_lock:
                    if account_cancel.is_set():
                        continue
                    state.update_console(
                        connected=True,
                        packets=state.packets + 1,
                        frames=state.frames + 1,
                    )
                if evaluated.evaluation and evaluated.evaluation.triggered:
                    for aid in evaluated.evaluation.triggered:
                        with context_lock:
                            if account_cancel.is_set():
                                break
                            if evaluated.context is not None and evaluated.context == active_context:
                                state.note_unlock(aid)
                        print(f"RetroAchievements: achievement {aid} triggered on console!", flush=True)
                        current_creds = store.load()
                        ctx = evaluated.context
                        if (current_creds and ctx and ctx.account == current_creds.username
                                and not account_cancel.is_set()):
                            submission_id += 1
                            task = AccountTask(submission_id, ctx.epoch, "award",
                                               credentials=current_creds, context=ctx, achievement_id=aid)
                            if not submissions.offer(task):
                                print(f"RetroAchievements: unlock {aid} not queued (full or signed out).", flush=True)

            now = time.monotonic()
            if now - last_ping >= 120:
                last_ping = now
                with context_lock:
                    ctx = active_context
                current_creds = store.load()
                if current_creds and ctx and not account_cancel.is_set():
                    submission_id += 1
                    task = AccountTask(submission_id, ctx.epoch, "ping", credentials=current_creds, context=ctx)
                    if not submissions.offer(task):
                        print("RetroAchievements: ping not queued (full or signed out).", flush=True)

        return 0
    finally:
        account_cancel.set()
        if not submissions.close():
            print("RetroAchievements: submission worker did not stop within deadline.", flush=True)
        if web_server:
            web_server.close()
        if viewer:
            viewer.close()
        if bridge:
            bridge.close()
        (profile / "account-port").unlink(missing_ok=True)
        service.stop()
        lease.close()
        close_owner()


def main(argv=None):
    parser = argparse.ArgumentParser(description="RetroAchievements for real PS2 consoles")
    parser.add_argument("--mode", choices=("xerabora", "caduceus"), default="xerabora")
    parser.add_argument("--games-folder", type=Path)
    parser.add_argument("--no-sound", action="store_true")
    parser.add_argument("--obs-folder", type=Path)
    parser.add_argument("--lan-viewer", action="store_true")
    parser.add_argument("--owned-runtime", action="store_true",
                        help="Run the PS2-Servers native runtime (Issue #223 preview)")
    parser.add_argument("--login", type=str, metavar="USERNAME",
                        help="Sign in with RetroAchievements username and password prompt")
    parser.add_argument("--import-credentials", action="store_true",
                        help="Import credentials from existing managed profile with explicit consent")
    args = parser.parse_args(argv)
    if args.owned_runtime:
        lib = library_path()
        if not lib.is_file():
            parser.error("The native rcheevos library is missing. Source users: run python build/build_ra_evaluator.py.")
    else:
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
    stopped = threading.Event()
    for name in ("SIGINT", "SIGTERM"):
        signal.signal(getattr(signal, name), lambda *_: stopped.set())
    owner_alive, close_owner = bootstrap_owner()
    if args.owned_runtime:
        return run_owned_runtime(args, profile, lease, stopped, owner_alive, close_owner)
    env = engine_environment(profile, args.mode)
    ui_port = available_account_port()
    command = [str(binary), "--port", "18194", "--ui-port", str(ui_port)]
    if args.no_sound:
        command.append("--no-sound")
    if args.obs_folder:
        args.obs_folder.mkdir(parents=True, exist_ok=True)
        command += ["--obs", str(args.obs_folder.resolve())]
    bridge = None
    viewer = None
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
