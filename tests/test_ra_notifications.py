"""Native unlock notifications must be fresh, verified and never replay history."""
import threading
import time
import unittest

from launcher.ra_notifications import UnlockTracker
from launcher.ra_session import SessionPoller


def live(session=1, title="Spyro the Dragon"):
    return {"state": "playing", "title": title, "session": session}


def payload(events=(), *, title="Spyro the Dragon", connected=True, packets=5):
    return {"console": {"connected": connected, "packets": packets},
            "game": {"title": title, "serial": "SCUS_942.28" if connected else ""},
            "unlocks": list(events)}


def event(id=101, ago=0, title="First Flame", points=5):
    return {"id": id, "title": title, "points": points, "ago": ago}


class UnlockTrackerTests(unittest.TestCase):
    def test_initial_history_never_triggers_popup_even_when_recent(self):
        tracker = UnlockTracker()
        self.assertEqual(tracker.observe(payload([event()]), live()), [])
        self.assertEqual(tracker.observe(payload([event()]), live()), [])
        newer = event(102, title="Second Flame", points=10)
        self.assertEqual(tracker.observe(payload([newer, event()]), live()),
                         [{"title": "Second Flame", "points": 10}])
        self.assertEqual(tracker.observe(payload([newer, event()]), live()), [])

    def test_offline_checked_and_stalled_sessions_do_not_replay(self):
        tracker = UnlockTracker()
        state = payload([event()])
        for status in ("connected", "stalled", "stopped", "unreachable", "listening"):
            self.assertEqual(tracker.observe(state, {"state": status, "session": 1}), [])
        self.assertEqual(tracker.observe(state, live()), [])
        self.assertEqual(tracker.observe(payload([event(102), event()]), live()),
                         [{"title": "First Flame", "points": 5}])
        self.assertEqual(tracker.observe(None, live()), [])
        self.assertEqual(tracker.observe(payload([event(102), event()]), live()), [])

    def test_new_game_and_reconnect_rebaseline(self):
        tracker = UnlockTracker()
        tracker.observe(payload([event()]), live(1))
        fresh = event(102)
        self.assertEqual(len(tracker.observe(payload([fresh, event()]), live(1))), 1)
        self.assertEqual(tracker.observe(payload([fresh, event()]), live(2)), [])
        self.assertEqual(tracker.observe(payload([fresh, event()]), live(3)), [])
        self.assertEqual(tracker.observe(payload([fresh, event()], title="Other"), live(3)), [])
        self.assertEqual(tracker.observe(payload([fresh, event()]), live(3)), [])

    def test_bursts_are_ordered_and_old_events_suppressed(self):
        tracker = UnlockTracker()
        tracker.observe(payload(), live())
        e1 = event(1, ago=2)
        e2 = event(2, ago=0)
        stale = event(3, ago=30)
        self.assertEqual(tracker.observe(payload([e2, stale, e1]), live()),
                         [{"title": "First Flame", "points": 5},
                          {"title": "First Flame", "points": 5}])
        self.assertEqual(tracker.observe(payload([e2, stale, e1]), live()), [])

    def test_malformed_events_and_title_mismatch_cannot_announce(self):
        tracker = UnlockTracker()
        tracker.observe(payload(), live())
        invalid = [event(-1), event(True), event(3, ago=-1),
                   event(4, points=-5), event(5, points=10001),
                   event(6, title=""), {"id": 7, "title": "Bad", "points": 5},
                   event(8, title="line\x00break")]
        self.assertEqual(tracker.observe(payload(invalid), live()), [])
        self.assertEqual(tracker.observe(payload([event()]), live(title="Different")), [])
        self.assertEqual(tracker.observe(payload([event()]), live()), [])


class SessionPollerUnlockTests(unittest.TestCase):
    def test_worker_queue_is_bounded_and_drained_on_ui_thread(self):
        state = {"packet": 1, "events": [event(101)]}
        signal = threading.Event()
        def fetch():
            state["packet"] += 1
            signal.set()
            return payload(state["events"], packets=state["packet"])
        monitor = SessionPoller(fetch=fetch, period=0.01)
        monitor.start()
        self.addCleanup(monitor.close)
        monitor.set_running(True)
        deadline = time.monotonic() + 2
        while monitor.snapshot()["state"] != "playing" and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(monitor.snapshot()["state"], "playing")
        # First playing snapshot already baselined achievement 101.
        time.sleep(.05)
        self.assertEqual(monitor.take_unlocks(), [])
        state["events"] = [event(102), event(101)]
        deadline = time.monotonic() + 2
        got = []
        while not got and time.monotonic() < deadline:
            got = monitor.take_unlocks()
            time.sleep(.01)
        self.assertEqual(got, [{"title": "First Flame", "points": 5}])
        self.assertEqual(monitor.take_unlocks(), [])
        monitor.set_running(False)
        self.assertEqual(monitor.take_unlocks(), [])
        time.sleep(.3)
        self.assertEqual(monitor.snapshot()["state"], "stopped")


if __name__ == "__main__":
    unittest.main()
