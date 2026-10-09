"""Stable RA session identification without game-server-path polling."""
import threading
import time
import unittest

from launcher.ra_session import SessionPoller, SessionTracker, safe_title


def payload(packets, title="Test Game", connected=True, signed_in=True):
    return {"console": {"connected": connected, "packets": packets},
            "login": {"ok": signed_in},
            "game": {"title": title, "serial": "P123456789abcde"}}


class SessionTests(unittest.TestCase):
    def test_connected_does_not_mean_playing_without_advancing_packets(self):
        session = SessionTracker(stale_seconds=15)
        self.assertEqual(session.observe(payload(9), now=100)["state"], "connected")
        self.assertEqual(session.observe(payload(9), now=104)["state"], "connected")
        active = session.observe(payload(10), now=105)
        self.assertEqual(active["state"], "playing")
        self.assertEqual(active["title"], "Test Game")
        self.assertEqual(session.observe(payload(10), now=119)["state"], "playing")
        self.assertEqual(session.observe(payload(10), now=120)["state"], "stalled")
        self.assertEqual(session.observe(payload(11), now=121)["state"], "playing")

    def test_disconnect_reset_and_new_game_are_not_stale(self):
        session = SessionTracker()
        session.observe(payload(8), now=10)
        session.observe(payload(9), now=11)
        self.assertEqual(session.observe(payload(0, connected=False), now=12)["state"], "listening")
        self.assertEqual(session.observe(payload(100, title="Old Game"), now=20)["state"], "connected")
        active = session.observe(payload(101, title="New \n Game"), now=21)
        self.assertEqual(active["title"], "New Game")
        self.assertEqual(session.observe(payload(1, title="Another"), now=22)["state"], "connected")

    def test_different_checked_game_does_not_spoof_now_playing(self):
        session = SessionTracker()
        state = payload(4)
        state["game"]["serial"] = ""  # loaded set is not the streaming image
        self.assertEqual(session.observe(state, now=10)["state"], "connected")
        state["console"]["packets"] = 5
        self.assertEqual(session.observe(state, now=11)["state"], "connected")
        state["game"]["serial"] = "P123456789abcde"
        self.assertEqual(session.observe(state, now=12)["state"], "playing")

    def test_game_session_identity_resets_on_disconnect_and_stall(self):
        session = SessionTracker(stale_seconds=5)
        session.observe(payload(10), now=0)
        first = session.observe(payload(11), now=1)
        self.assertEqual(first["session"], 1)
        self.assertEqual(session.observe(payload(11), now=6)["state"], "stalled")
        again = session.observe(payload(12), now=7)
        self.assertGreater(again["session"], first["session"])
        session.observe(payload(0, connected=False), now=8)
        session.observe(payload(20), now=9)
        self.assertGreater(session.observe(payload(21), now=10)["session"], again["session"])
        changed = payload(22, title="Second game")
        self.assertGreater(session.observe(changed, now=11)["session"], again["session"])

    def test_offline_bad_payload_does_not_report_playing(self):
        session = SessionTracker()
        self.assertEqual(session.observe(None)["state"], "unreachable")
        self.assertIn("sign-in required", session.observe(payload(0, connected=False, signed_in=False))["text"])
        self.assertEqual(session.observe(payload(True))["state"], "connected")
        self.assertEqual(session.observe({"console": {"connected": True}})["state"], "connected")
        self.assertEqual(safe_title("hello\n\t world"), "hello world")

    def test_poller_reports_cached_state_from_worker_not_calling_fetch_on_read(self):
        invoked = threading.Event()
        def fetch():
            invoked.set()
            return payload(4)
        monitor = SessionPoller(fetch=fetch, period=0.01)
        monitor.start()
        self.addCleanup(monitor.close)
        self.assertEqual(monitor.snapshot()["state"], "stopped")
        self.assertFalse(invoked.is_set())
        monitor.set_running(True)
        self.assertTrue(invoked.wait(timeout=2), "poller did not start")
        monitor.set_running(False)
        time.sleep(0.3)
        self.assertEqual(monitor.snapshot()["state"], "stopped")
