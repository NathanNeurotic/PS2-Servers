"""Wire-level Caduceus conformance and native xeRAbora engine smoke tests.

No real RA account is used, and no achievements are submitted by these tests.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
import urllib.request

from launcher import caduceus, gui, servers, windows_setup
from launcher.achievements import engine_path, available_account_port, claim_service
from launcher import achievements


def stop_native_smoke_process(proc, grace_seconds=15):
    """Reap native test children on every platform, including on timeout.

    macOS x64 runs under translation on the GitHub arm64 runner and its
    graceful shutdown may take more than the old 5-second test threshold.
    A wedged engine must still fail the test, *after* being force-reaped:
    otherwise the next test can use the wrong UDP listener.
    """
    if proc.poll() is None:
        proc.terminate()
    try:
        proc.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired as error:
        proc.kill()
        proc.wait(timeout=5)
        raise AssertionError(
            "Native RetroAchievements engine did not exit after SIGTERM "
            "within {} seconds (force-reaped).".format(grace_seconds)
        ) from error


class WireTests(unittest.TestCase):
    def test_native_engine_timeout_always_reaps_process(self):
        proc = mock.Mock()
        proc.poll.return_value = None
        proc.wait.side_effect = [subprocess.TimeoutExpired("ps2ra", 0.1), -9]
        with self.assertRaisesRegex(AssertionError, "force-reaped"):
            stop_native_smoke_process(proc, grace_seconds=0.1)
        proc.terminate.assert_called_once_with()
        proc.kill.assert_called_once_with()
        self.assertEqual(proc.wait.call_count, 2)

    @unittest.skipUnless(os.name == "nt", "Windows onefile bootstrap lifetime")
    def test_bootstrap_owner_uses_a_held_process_identity(self):
        owner = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        close = lambda: None
        try:
            with mock.patch.object(achievements, "is_frozen", return_value=True), \
                    mock.patch.dict(os.environ, {"NUITKA_ONEFILE_PARENT": str(owner.pid)}):
                alive, close = achievements.bootstrap_owner()
            self.assertTrue(alive())
            owner.terminate()
            owner.wait(timeout=5)
            self.assertFalse(alive())
        finally:
            close()
            if owner.poll() is None:
                owner.terminate()
                owner.wait(timeout=5)

    def test_account_port_can_change_without_sharing_a_listener(self):
        with socket.socket() as occupied:
            if os.name == "nt":
                occupied.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                occupied.bind(("127.0.0.1", 18196))
            except OSError:
                # A previous real connection may still own the port in
                # TIME_WAIT; that is also a valid reason to select another.
                self.assertNotEqual(available_account_port(), 18196)
                return
            occupied.listen()
            self.assertNotEqual(available_account_port(), 18196)

    def test_profile_cannot_have_two_supervisors(self):
        with tempfile.TemporaryDirectory() as directory:
            lease = claim_service(Path(directory))
            try:
                with self.assertRaises(OSError):
                    claim_service(Path(directory))
            finally:
                lease.close()
            claim_service(Path(directory)).close()

    def test_remote_redirects_are_not_followed(self):
        request = urllib.request.Request("https://retroachievements.org/API/test.php?y=secret")
        self.assertIsNone(caduceus.NoRedirect().redirect_request(
            request, None, 302, "Moved", {}, "https://example.com/collect"))

    def test_malformed_hash_lookup_is_not_unsupported(self):
        account = caduceus.Account(Path("unused"), lambda: {})
        for body in (b'[]', b'{"Success":true}', b'{"Success":false,"GameID":0}',
                     b'{"Success":true,"GameID":"invalid"}', b'{"Success":true,"GameID":true}'):
            response = mock.MagicMock()
            response.__enter__.return_value.read.return_value = body
            with mock.patch.object(caduceus, "open_remote", return_value=response):
                with self.assertRaises(ValueError):
                    account.resolve("a" * 32)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.bridge = caduceus.Bridge(self.root / "profile", self.root / "games")
        self.sock = mock.Mock()
        self.peer = ("192.168.1.10", 40000)
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(self.bridge.close)

    def response(self):
        packet, peer = self.sock.sendto.call_args.args
        self.assertEqual(peer, self.peer)
        self.assertGreaterEqual(len(packet), 128)
        self.assertEqual(len(packet) % 64, 0)
        self.assertLessEqual(len(packet), 960)
        return packet.rstrip(b"\0").decode("ascii")

    def test_both_catalog_versions_and_session_state(self):
        image_hash = "ab" * 16
        self.bridge.catalog[image_hash] = (time.monotonic(), "OK 120 Bully")
        self.bridge.receive(self.sock, 18197, ("CADQ1 " + image_hash).encode(), self.peer, time.monotonic())
        self.assertEqual(self.response(), "CADR1 " + image_hash + " OK 120 Bully")
        self.bridge.receive(self.sock, 18197, ("CADQ2 " + image_hash).encode(), self.peer, time.monotonic())
        self.assertEqual(self.response(), "CADR2 " + image_hash + " OFFLINE OK 120 Bully")
        self.bridge.state = {"login": {"ok": True}}
        self.bridge.receive(self.sock, 18197, ("CADQ2 " + image_hash).encode(), self.peer, time.monotonic())
        self.assertEqual(self.response(), "CADR2 " + image_hash + " READY OK 120 Bully")

    def test_unknown_is_not_reported_as_unsupported(self):
        with mock.patch.object(self.bridge, "submit"):
            self.bridge.receive(self.sock, 18197, b"CADQ2 " + b"b" * 32, self.peer, time.monotonic())
        self.assertTrue(self.response().endswith(" OFFLINE UNKNOWN"))

    def test_pairing_required_before_account_queries(self):
        request = b"CADA1 42 G 0 0 0 " + b"0" * 64
        self.bridge.receive(self.sock, 18198, request, self.peer, time.monotonic())
        self.sock.sendto.assert_not_called()
        request = request[:-64] + self.bridge.key.encode()
        self.bridge.receive(self.sock, 18198, request, self.peer, time.monotonic())
        self.assertEqual(self.response(), "CADB1 42 OFFLINE")

    def test_progress_filter_and_exact_tabular_wire_layout(self):
        account = mock.Mock()
        account.user.return_value = "tester"
        account.game.return_value = {"Title": "Bully", "Achievements": {
            "1": {"ID": 1, "Title": "Earned", "Points": 5, "DateEarned": "2026-10-01"},
            "2": {"ID": 2, "Title": "Café\tUnlocked?", "Description": "A\nB", "Points": 10}}}
        self.bridge.account = account
        with mock.patch.object(self.bridge, "artwork"):
            self.bridge.progress((self.peer, b"CADA1 7", "tester"), self.sock, self.peer,
                                 "CADB1 7 ", "A", 0, 2, "100", "tester")
        response = self.response()
        self.assertTrue(response.startswith("CADB1 7 OK\tA\t0\t1\t100\t1\t2\ttester\tBully\n2\t0\t0\t0\t10\t"))
        self.assertTrue(response.endswith("\tCafe Unlocked?\tA B\t"))

    def test_logout_during_query_discards_account_results(self):
        self.bridge.account = mock.Mock()
        self.bridge.account.user.return_value = ""
        self.bridge.finish((self.peer, b"CADA1 123", "old"), self.sock, self.peer,
                           "CADB1 123 OK private data", "old")
        self.assertEqual(self.response(), "CADB1 123 OFFLINE")

    def test_malformed_packets_do_not_start_work(self):
        with mock.patch.object(self.bridge, "submit") as submit:
            for packet in (b"CADQ2 ../", b"CADQ2 " + b"a" * 33, b"CADQ9 " + b"a" * 32,
                           b"CADA1 1 A 0 9 10 " + self.bridge.key.encode()):
                self.bridge.receive(self.sock, 18197 if packet.startswith(b"CADQ") else 18198,
                                    packet, self.peer, time.monotonic())
        submit.assert_not_called()
        self.sock.sendto.assert_not_called()

    def test_pairing_key_is_stable_and_never_an_ra_secret(self):
        key = caduceus.pairing_key(self.root / "games/ART")
        self.assertEqual(key, self.bridge.key)
        self.assertEqual(len(bytes.fromhex(key)), 32)

    def test_api_failures_do_not_poison_hash_cache(self):
        self.bridge.account = mock.Mock()
        self.bridge.account.resolve.side_effect = OSError("offline")
        self.bridge.lookup("a" * 32)
        self.assertFalse(self.bridge.catalog)

    def test_inbound_ports_exclude_private_account_page(self):
        self.assertEqual([p for _, p, _ in windows_setup.server_ports("retroachievements", {"mode": "caduceus"})],
                         [18194, 18197, 18198])
        self.assertEqual([p for _, p, _ in windows_setup.server_ports("retroachievements", {"mode": "xerabora"})], [18194])

    def test_managed_runtime_does_not_auto_open_upstream_window(self):
        with mock.patch.dict(os.environ, {"PS2SERVERS_RA_NO_BROWSER": ""}):
            env = achievements.engine_environment(self.root / "profile", "caduceus")
        self.assertEqual(env["PS2SERVERS_RA_NO_BROWSER"], "1")
        self.assertEqual(env["PS2SERVERS_RA_MODE"], "caduceus")
        self.assertEqual(env["PS2SERVERS_RA_PROFILE"], str(self.root / "profile"))
        self.assertEqual(env["PS2SERVERS_RA_PARENT"], str(os.getpid()))

    def test_compatibility_selection_discloses_shared_runtime(self):
        server = servers.RETROACHIEVEMENTS
        mode = next(field for field in server.fields if field.key == "mode")
        self.assertIn("NOT a separate desktop engine", mode.help)
        for label, expected in (("xeRAbora", "xeRAbora"), ("Caduceus", "Caduceus")):
            with self.subTest(label=label):
                argv = server.build_argv({
                    "mode": label,
                    "games_folder": str(self.root / "games"),
                })
                self.assertEqual(argv[:2], ["--mode", label.lower()])
                hint = gui.opl_hint("retroachievements", "192.168.1.2", {"mode": label})
                self.assertIn(expected + " console protocol", hint)
                self.assertIn("xeRAbora-derived engine", hint)

    def test_account_button_requires_this_service_running(self):
        card = mock.Mock()
        card.server.key = "retroachievements"
        card.app.is_running.return_value = False
        with mock.patch.object(gui.messagebox, "showinfo") as dialog, \
             mock.patch.object(gui.webbrowser, "open_new_tab") as browser:
            gui.ServerCard._open_achievement_account(card)
        dialog.assert_called_once()
        browser.assert_not_called()

    def test_running_mode_selector_is_locked_until_stop(self):
        card = mock.Mock()
        card.server = servers.RETROACHIEVEMENTS
        card.field_widgets = {"mode": mock.Mock()}
        card._active_values = {"mode": "caduceus"}
        card._running_label.return_value = "Running"
        card.app.current_ip.return_value = "192.168.1.2"
        gui.ServerCard.refresh_status(card, True)
        card.field_widgets["mode"].config.assert_called_with(state="disabled")
        self.assertIn("Caduceus console protocol",
                      card.hint.config.call_args.kwargs["text"])
        gui.ServerCard.refresh_status(card, False)
        card.field_widgets["mode"].config.assert_called_with(state="readonly")

    def test_mode_validation_and_no_credentials_in_launcher_fields(self):
        server = servers.RETROACHIEVEMENTS
        self.assertEqual(server.build_argv({"mode": "xeRAbora"}), ["--mode", "xerabora"])
        with self.assertRaises(ValueError):
            server.build_argv({"mode": "caduceus"})
        self.assertFalse({"username", "password", "token", "api_key"} & {f.key for f in server.fields})


@unittest.skipUnless(engine_path().is_file(), "build the native achievement engine first")
class NativeEngineTests(unittest.TestCase):
    def test_supervisor_restart_and_mode_switch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            games = root / "games"
            games.mkdir()
            env = dict(os.environ, LOCALAPPDATA=directory, APPDATA=directory,
                       XDG_CONFIG_HOME=directory, HOME=directory, PS2SERVERS_RA_NO_BROWSER="1")
            entry = Path(__file__).resolve().parents[1] / "ps2servers.py"
            for mode in ("xerabora", "caduceus"):
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as console, open(root / "run.log", "w+b") as log:
                    console.bind(("127.0.0.1", 0))
                    console.settimeout(0.2)
                    command = [sys.executable, str(entry), "--serve", "retroachievements",
                               "--mode", mode, "--no-sound", "--games-folder", str(games)]
                    proc = subprocess.Popen(command, env=env, stdout=log, stderr=log)
                    try:
                        deadline = time.monotonic() + 10
                        while True:
                            console.sendto(("RAP1 127.0.0.1 " + str(console.getsockname()[1])).encode(),
                                           ("127.0.0.1", 18194))
                            try:
                                packet, _ = console.recvfrom(1024)
                                if packet.startswith(b"RAO1 OK"):
                                    break
                            except (socket.timeout, ConnectionResetError):
                                pass
                            if time.monotonic() > deadline or proc.poll() is not None:
                                log.seek(0)
                                self.fail(log.read().decode(errors="replace"))
                        port_file = next(root.rglob("account-port"))
                        port = int(port_file.read_text("ascii"))
                        try:
                            with urllib.request.urlopen("http://127.0.0.1:{}/state".format(port), timeout=3) as response:
                                self.assertFalse(json.load(response)["lan"]["on"])
                        except OSError as error:
                            log.seek(0)
                            self.fail("{} port {}: {}\n{}".format(mode, port, error, log.read().decode(errors="replace")))
                        duplicate = subprocess.run(command, env=env, capture_output=True, timeout=5)
                        self.assertNotEqual(duplicate.returncode, 0)
                        self.assertIn(b"already running", duplicate.stderr)
                        if mode == "caduceus":
                            key = (games / "ART/CADUCEUS.KEY").read_text("ascii")
                            console.sendto(("CADA1 1 G 0 0 0 " + key).encode(), ("127.0.0.1", 18198))
                            self.assertEqual(console.recvfrom(1024)[0].rstrip(b"\0"), b"CADB1 1 OFFLINE")
                    finally:
                        proc.terminate()
                        proc.wait(timeout=10)
                    deadline = time.monotonic() + 10
                    while True:
                        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                            if os.name == "nt":
                                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                            try:
                                probe.bind(("0.0.0.0", 18194))
                                break
                            except OSError:
                                if time.monotonic() > deadline:
                                    self.fail("Engine retained the telemetry port after its supervisor exited")
                        time.sleep(0.05)

    def test_native_obs_exports_and_read_only_viewer_without_account(self):
        from launcher.achievement_viewer import Viewer
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exports = root / "Stream labels with spaces"
            exports.mkdir()
            env = dict(os.environ, PS2SERVERS_RA_PROFILE=str(root / "profile"),
                       PS2SERVERS_RA_NO_BROWSER="1", PS2SERVERS_RA_PARENT=str(os.getpid()))
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as available:
                available.bind(("127.0.0.1", 0))
                port = available.getsockname()[1]
            with socket.socket() as available:
                available.bind(("127.0.0.1", 0))
                ui_port = available.getsockname()[1]
            viewer = None
            with (root / "engine.log").open("wb") as log:
                proc = subprocess.Popen([str(engine_path()), "--port", str(port),
                                         "--ui-port", str(ui_port), "--no-sound",
                                         "--obs", str(exports)], env=env,
                                        stdin=subprocess.DEVNULL, stdout=log, stderr=log)
            try:
                deadline = time.monotonic() + 10
                while True:
                    try:
                        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as console:
                            console.sendto(b"RAS1 sq=000001 vb=0000 n=0000 pt=0 np=0 id=TEST_000.01~~~~ ",
                                           ("127.0.0.1", port))
                        state = json.loads((exports / "data.json").read_text())
                        break
                    except (OSError, ValueError):
                        if time.monotonic() >= deadline or proc.poll() is not None:
                            self.fail("Native OBS export did not become readable")
                        time.sleep(0.1)
                self.assertFalse(state["login"]["ok"])
                self.assertTrue(state["console"]["connected"])
                self.assertEqual((exports / "progress.txt").read_text(), "0 / 0")
                self.assertEqual((exports / "console.txt").read_text(), "connected")
                viewer = Viewer(ui_port, port=0, host="127.0.0.1")
                viewer.start()
                url = "http://127.0.0.1:{}/".format(viewer.server.server_port)
                with urllib.request.urlopen(url + "state", timeout=3) as response:
                    viewed = json.load(response)
                self.assertFalse(viewed["login"]["ok"])
                self.assertTrue(viewed["console"]["connected"])
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(urllib.request.Request(url + "logout", method="POST"), timeout=3)
                self.assertEqual(caught.exception.code, 403)
                caught.exception.close()
                # A second synthetic serial must replace the exported game.
                while (exports / "game.txt").read_text() != "TEST_000.02":
                    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as console:
                        console.sendto(b"RAS1 sq=000002 vb=0000 n=0000 pt=0 np=0 id=TEST_000.02~~~~ ",
                                       ("127.0.0.1", port))
                    self.assertLess(time.monotonic(), deadline, "OBS game export did not update")
                    time.sleep(0.1)
            finally:
                if viewer is not None:
                    viewer.close()
                stop_native_smoke_process(proc)

    def test_private_profile_key_protection_and_native_discovery(self):
        with tempfile.TemporaryDirectory() as directory, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as console:
            profile = Path(directory)
            env = dict(os.environ, PS2SERVERS_RA_PROFILE=directory, PS2SERVERS_RA_NO_BROWSER="1",
                       PS2SERVERS_RA_PARENT=str(os.getpid()))
            key = "synthetic-test-key"
            result = subprocess.run([str(engine_path()), "--api-key", key], env=env,
                                    capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(caduceus.read_secret(profile / "apikey"), key)
            if os.name == "nt":
                self.assertNotIn(key.encode(), (profile / "apikey").read_bytes())
            else:
                self.assertEqual((profile / "apikey").stat().st_mode & 0o777, 0o600)
            console.bind(("127.0.0.1", 0))
            console.settimeout(0.3)
            # Bind a temporary socket to reserve a currently free engine port.
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as available:
                available.bind(("127.0.0.1", 0))
                port = available.getsockname()[1]
            with socket.socket() as available:
                available.bind(("127.0.0.1", 0))
                ui_port = available.getsockname()[1]
            # A PIPE that is only drained in finally can itself stall a child
            # that logs more than the OS pipe capacity. File-backed stderr
            # keeps shutdown independent of log volume.
            engine_log_path = profile / "native-engine-stderr.log"
            with engine_log_path.open("wb") as engine_log:
                proc = subprocess.Popen(
                    [str(engine_path()), "--port", str(port),
                     "--ui-port", str(ui_port), "--no-sound"],
                    env=env, stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=engine_log)
            try:
                deadline = time.monotonic() + 10
                while True:
                    console.sendto("RAP1 127.0.0.1 {}".format(console.getsockname()[1]).encode(), ("127.0.0.1", port))
                    try:
                        packet, _ = console.recvfrom(1024)
                        break
                    except (socket.timeout, ConnectionResetError):
                        if time.monotonic() >= deadline or proc.poll() is not None:
                            self.fail("Native engine failed to answer discovery")
                self.assertTrue(packet.startswith(b"RAO1 OK xerabora/"), packet)
                self.assertGreaterEqual(len(packet), 128)
                self.assertEqual(len(packet) % 64, 0)
                with urllib.request.urlopen("http://127.0.0.1:{}/state".format(ui_port), timeout=3) as response:
                    state = json.load(response)
                self.assertFalse(state["login"]["ok"])
                self.assertFalse(state["lan"]["on"])
                with urllib.request.urlopen("http://127.0.0.1:{}/".format(ui_port), timeout=3) as response:
                    page = response.read().decode("utf-8")
                self.assertIn("Get updates and report integration issues through PS2-Servers", page)
                self.assertNotIn("onClick=${flip}", page)
                for headers in ({"Origin": "https://example.com"},
                                {"Host": "example.com"},
                                {"Sec-Fetch-Site": "cross-site"}):
                    request = urllib.request.Request(
                        "http://127.0.0.1:{}/state".format(ui_port), headers=headers)
                    with self.assertRaises(urllib.error.HTTPError) as forbidden:
                        urllib.request.urlopen(request, timeout=3)
                    self.assertEqual(forbidden.exception.code, 403)
                    forbidden.exception.close()
                conflict = subprocess.run([str(engine_path()), "--port", str(port), "--no-ui", "--no-sound"],
                                          env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=5)
                self.assertEqual(conflict.returncode, 1, "A busy telemetry port must fail instead of adopting another client")
            finally:
                try:
                    stop_native_smoke_process(proc)
                except AssertionError as error:
                    self.fail("{}\n{}".format(
                        error, engine_log_path.read_text(encoding="utf-8", errors="replace")[-8000:]))
