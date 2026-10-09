"""Wire-level Caduceus conformance and native xeRAbora engine smoke tests.

No real RA account is used, and no achievements are submitted by these tests.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import unittest
from unittest import mock
import urllib.request

from launcher import caduceus, servers, windows_setup
from launcher.achievements import engine_path


class WireTests(unittest.TestCase):
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

    def test_mode_validation_and_no_credentials_in_launcher_fields(self):
        server = servers.RETROACHIEVEMENTS
        self.assertEqual(server.build_argv({"mode": "xeRAbora"}), ["--mode", "xerabora"])
        with self.assertRaises(ValueError):
            server.build_argv({"mode": "caduceus"})
        self.assertFalse({"username", "password", "token", "api_key"} & {f.key for f in server.fields})


@unittest.skipUnless(engine_path().is_file(), "build the native achievement engine first")
class NativeEngineTests(unittest.TestCase):
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
            proc = subprocess.Popen([str(engine_path()), "--port", str(port), "--ui-port", str(ui_port), "--no-sound"],
                                    env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
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
                proc.terminate()
                proc.communicate(timeout=5)
