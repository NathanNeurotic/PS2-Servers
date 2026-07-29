"""The launcher's side of the status protocol.

The behaviour worth pinning is not "it can parse a packet" -- test_router_status
covers the format. It is the two judgement calls the launcher makes with the
answer:

  * a server that does not reply is UNKNOWN, never "down", because it may
    simply be a build that predates the protocol; and
  * a busy warning fires only when a server has positively said it is
    mid-transfer, never on a failure to reach it, or it would be dismissed
    reflexively and would not be there for the save that matters.
"""

import os
import socket
import sys
import tempfile
import threading
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from launcher import status_client  # noqa: E402


class FakeServer:
    """A socket that answers status queries however the test wants.

    Real UDP on loopback rather than a stubbed query(): the point is to
    exercise the actual send, receive and decode.
    """

    def __init__(self, reply_builder=None):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.reply_builder = reply_builder
        self.queries = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        self.sock.settimeout(0.2)
        while not self._stop.is_set():
            try:
                data, addr = self.sock.recvfrom(2048)
            except (socket.timeout, OSError):
                continue
            self.queries += 1
            if self.reply_builder is not None:
                try:
                    self.sock.sendto(self.reply_builder(data), addr)
                except OSError:
                    pass

    def close(self):
        self._stop.set()
        self._thread.join(timeout=1.0)
        try:
            self.sock.close()
        except OSError:
            pass


class ProtocolIsShared(unittest.TestCase):
    def test_launcher_loads_the_servers_copy(self):
        # Not a duplicate of the encoder. The launcher and the server must
        # agree byte for byte, and one copy is the only way to be sure.
        proto = status_client.protocol()
        self.assertIsNotNone(proto, "launcher could not load router_status")
        self.assertEqual(
            os.path.normcase(os.path.basename(proto.__file__)), "router_status.py")
        self.assertIn("udpfs_server", os.path.normcase(proto.__file__))


class Query(unittest.TestCase):
    def setUp(self):
        self.proto = status_client.protocol()
        if self.proto is None:
            self.skipTest("router_status not loadable")

    def test_reads_a_real_reply_over_the_wire(self):
        def reply(_data):
            return self.proto.build_status_reply(
                self.proto.STATE_BUSY, self.proto.FLAG_UDPFS, 2, 99, "PS2 Servers")

        server = FakeServer(reply)
        self.addCleanup(server.close)
        got = status_client.query("127.0.0.1", server.port, timeout=1.0)
        self.assertIsNotNone(got)
        self.assertEqual(got["state"], self.proto.STATE_BUSY)
        self.assertEqual(got["sessions"], 2)
        self.assertEqual(got["name"], "PS2 Servers")

    def test_silence_is_none_not_an_exception(self):
        # An older server ignores the query. That must be an ordinary answer of
        # "no answer", not something that can break a poll loop.
        server = FakeServer(reply_builder=None)
        self.addCleanup(server.close)
        self.assertIsNone(status_client.query("127.0.0.1", server.port, timeout=0.3))
        self.assertGreaterEqual(server.queries, 1, "the query never arrived")

    def test_garbage_reply_is_rejected(self):
        server = FakeServer(lambda _d: b"not a status reply at all")
        self.addCleanup(server.close)
        self.assertIsNone(status_client.query("127.0.0.1", server.port, timeout=0.5))

    def test_closed_port_and_bad_port_do_not_raise(self):
        server = FakeServer(reply_builder=None)
        server.close()
        self.assertIsNone(status_client.query("127.0.0.1", server.port, timeout=0.2))
        self.assertIsNone(status_client.query("127.0.0.1", 0, timeout=0.2))
        self.assertIsNone(status_client.query("no.such.host.invalid", 9, timeout=0.2))


class BusyIsPositiveOnly(unittest.TestCase):
    def setUp(self):
        self.proto = status_client.protocol()
        if self.proto is None:
            self.skipTest("router_status not loadable")

    def test_only_a_stated_busy_counts(self):
        self.assertTrue(status_client.is_busy({"state": self.proto.STATE_BUSY}))
        for other in (self.proto.STATE_READY, self.proto.STATE_STARTING,
                      self.proto.STATE_DEGRADED, self.proto.STATE_STOPPING):
            self.assertFalse(status_client.is_busy({"state": other}))

    def test_no_answer_is_not_busy(self):
        # The important negative. If an unreachable server counted as busy, the
        # warning would fire constantly against every older build and be
        # trained away before it ever mattered.
        self.assertFalse(status_client.is_busy(None))
        self.assertFalse(status_client.is_busy({}))
        self.assertFalse(status_client.is_busy({"state_name": status_client.UNKNOWN}))


class PollerBehaviour(unittest.TestCase):
    def setUp(self):
        self.proto = status_client.protocol()
        if self.proto is None:
            self.skipTest("router_status not loadable")

    def test_records_per_target_and_marks_silence_unknown(self):
        talking = FakeServer(lambda _d: self.proto.build_status_reply(
            self.proto.STATE_READY, self.proto.FLAG_UDPFS, 0, 5, "talks"))
        silent = FakeServer(reply_builder=None)
        self.addCleanup(talking.close)
        self.addCleanup(silent.close)

        poller = status_client.Poller(timeout=0.3)
        poller.set_target("udpfs", "127.0.0.1", talking.port)
        poller.set_target("old", "127.0.0.1", silent.port)
        poller.poll_once()

        snap = poller.snapshot()
        self.assertEqual(snap["udpfs"]["state"], self.proto.STATE_READY)
        self.assertEqual(snap["old"]["state_name"], status_client.UNKNOWN,
                         "a server that does not answer must be unknown, not down")

    def test_clearing_a_target_drops_its_result(self):
        server = FakeServer(lambda _d: self.proto.build_status_reply(
            self.proto.STATE_READY, 0, 0, 0, ""))
        self.addCleanup(server.close)

        poller = status_client.Poller(timeout=0.3)
        poller.set_target("udpfs", "127.0.0.1", server.port)
        poller.poll_once()
        self.assertIn("udpfs", poller.snapshot())

        poller.clear_target("udpfs")
        self.assertNotIn("udpfs", poller.snapshot())
        poller.poll_once()
        self.assertNotIn("udpfs", poller.snapshot(),
                         "a cleared target must not come back on the next sweep")

    def test_a_failing_sweep_does_not_kill_the_thread(self):
        # A poller that dies takes the status display with it, silently.
        poller = status_client.Poller(interval=0.2, timeout=0.1)

        calls = []

        def boom():
            calls.append(1)
            raise RuntimeError("sweep exploded")

        poller.poll_once = boom
        poller.start()
        self.addCleanup(poller.stop)
        deadline = threading.Event()
        deadline.wait(0.8)
        self.assertGreaterEqual(len(calls), 2,
                                "the loop stopped after the first failure")


class StopAllStaysUnconditional(unittest.TestCase):
    """stop_all() must never be able to decline to stop.

    Checked at the source level because the GUI cannot be instantiated
    headlessly, and because this is a mistake that was actually made: a busy
    prompt was put inside stop_all, where a "No" would skip the stopping while
    every caller carried on regardless. Seven of its eight callers are
    elevate-and-relaunch paths that free ports before the new instance binds
    them, and the eighth is _shutdown_app, which destroys the root immediately
    afterwards -- so declining would have quit the app and orphaned the very
    servers the user was trying to protect.

    The asking belongs at the points where a person chose to stop something.
    """

    @staticmethod
    def _method(name):
        import ast

        path = os.path.join(_ROOT, "launcher", "gui.py")
        with open(path, "r", encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=path)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "LauncherApp":
                for item in node.body:
                    if isinstance(item, ast.FunctionDef) and item.name == name:
                        return item
        return None

    @staticmethod
    def _calls(func):
        import ast

        names = set()
        for node in ast.walk(func):
            if isinstance(node, ast.Call):
                target = node.func
                if isinstance(target, ast.Attribute):
                    names.add(target.attr)
                elif isinstance(target, ast.Name):
                    names.add(target.id)
        return names

    def test_stop_all_does_not_prompt(self):
        func = self._method("stop_all")
        self.assertIsNotNone(func, "LauncherApp.stop_all not found")
        self.assertNotIn(
            "confirm_stop_while_busy", self._calls(func),
            "stop_all must not prompt: its callers proceed regardless of the "
            "answer, so a 'No' would skip stopping and quit or relaunch anyway")
        self.assertNotIn("askyesno", self._calls(func))

    def test_the_user_facing_paths_do_prompt(self):
        for name in ("stop_all_confirmed", "stop_server"):
            func = self._method(name)
            self.assertIsNotNone(func, f"LauncherApp.{name} not found")
            self.assertIn("confirm_stop_while_busy", self._calls(func),
                          f"{name} is a person deciding and should warn")

    def test_quit_confirmation_consults_busy_state(self):
        func = self._method("_confirm_app_shutdown")
        self.assertIsNotNone(func)
        self.assertIn("is_busy", self._calls(func),
                      "quitting mid-transfer must warn; _shutdown_app calls "
                      "stop_all unconditionally, so this is the only gate")

    def test_footer_button_uses_the_confirming_wrapper(self):
        path = os.path.join(_ROOT, "launcher", "gui.py")
        with open(path, "r", encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn('text="Stop all", command=self.stop_all_confirmed', source,
                      "the footer Stop all button should be the confirming one")


if __name__ == "__main__":
    unittest.main()
