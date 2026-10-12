"""The delayed UDPFS fallback belongs only to its discovery generation.

These tests run the production fallback closure without network sockets or a
wall-clock wait. They distinguish an obsolete timer from the current one.
"""
import importlib.util
import pathlib
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
UDPFS_DIR = ROOT / "udpfs_server"
if str(UDPFS_DIR) not in sys.path:
    sys.path.insert(0, str(UDPFS_DIR))
SPEC = importlib.util.spec_from_file_location(
    "ps2servers_core_fallback", UDPFS_DIR / "ps2servers_core.py")
CORE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CORE)


class FallbackGenerationTests(unittest.TestCase):
    def _prepare(self):
        server = CORE.AutoUdpfsServer.__new__(CORE.AutoUdpfsServer)
        server.sessions = {}
        server.sessions_lock = threading.RLock()
        server.fallback_interval = 0.01
        peer = ("192.0.2.10", 62966)
        sess = SimpleNamespace(
            addr=peer,
            compat_lock=threading.RLock(),
            handshake_generation=2,
            protocol_profile=CORE.PROFILE_PENDING,
            first_data_seen=False,
            rx_streaming=False,
            fallback_sent=False,
        )
        server.sessions[peer] = sess
        sent = []
        server._compatibility_inform = lambda target: sent.append(target)
        return server, sess, sent

    def _run_timer(self, server, sess, generation):
        # Capture the real callback but never start a background thread.
        with patch.object(CORE.threading, "Thread") as thread_type:
            server._schedule_fallback(sess, generation)
            thread_type.return_value.start.assert_called_once()
            callback = thread_type.call_args.kwargs["target"]
        with patch.object(CORE.time, "sleep", return_value=None):
            callback()

    def test_old_timer_cannot_respond_to_new_discovery(self):
        server, sess, sent = self._prepare()
        self._run_timer(server, sess, 1)
        self.assertEqual(sent, [])

    def test_current_pending_timer_still_responds(self):
        server, sess, sent = self._prepare()
        self._run_timer(server, sess, 2)
        self.assertEqual(sent, [sess])

    def test_completed_handshake_suppresses_current_timer(self):
        server, sess, sent = self._prepare()
        sess.first_data_seen = True
        self._run_timer(server, sess, 2)
        self.assertEqual(sent, [])

    def test_evicted_session_suppresses_timer(self):
        server, sess, sent = self._prepare()
        server.sessions.clear()
        self._run_timer(server, sess, 2)
        self.assertEqual(sent, [])


if __name__ == "__main__":
    unittest.main()
