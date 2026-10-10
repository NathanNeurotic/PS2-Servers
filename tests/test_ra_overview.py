"""Read-only native achievement view: never confuse checked sets with live games."""
import io
import json
import unittest
from unittest import mock

from launcher import ra_overview


class OverviewTests(unittest.TestCase):
    def test_native_achievement_search_and_state_filter(self):
        rows = [("42", "Discover Dragonfly", "Unlocked", "", "5"),
                ("43", "Defeat Ripto", "Not unlocked", "8/10", "10"),
                ("44", "Collect Gems", "Not unlocked", "", "15")]
        self.assertEqual(len(ra_overview.filter_achievements(rows)), 3)
        self.assertEqual(ra_overview.filter_achievements(rows, "ripTO"), [rows[1]])
        self.assertEqual(ra_overview.filter_achievements(rows, "42"), [rows[0]])
        self.assertEqual(ra_overview.filter_achievements(rows, status="Unlocked"), [rows[0]])
        self.assertEqual(ra_overview.filter_achievements(rows, status="Not unlocked"),
                         rows[1:])
        self.assertEqual(ra_overview.filter_achievements(rows, "43", "Unlocked"), [])
        self.assertEqual(ra_overview.filter_achievements(rows, "dragonfly", "Unlocked"),
                         [rows[0]])

    def test_unavailable_and_malformed_data_are_not_fake_gameplay(self):
        for value in (None, [], "bad", {}):
            summary = ra_overview.view_model(value, {"state": "playing", "title": "Old game"})
            self.assertFalse(summary["playing"])
            self.assertEqual(summary["rows"], [])

    def test_engine_checked_set_is_not_current_console_game(self):
        state = {
            "login": {"ok": True, "user": "player", "webapi": True},
            "console": {"connected": True, "packets": 21, "frames": 200,
                        "gaps": 3, "dupes": 2, "torn": 1},
            "game": {"title": "Shadow", "serial": "", "checked_only": 1,
                     "achievements": [{"id": 1, "title": "Trial", "state": 1,
                                       "points": 10, "measured": "3/7", "percent": 30.0}]},
            "unlocks": [{"id": 4, "title": "Previous run", "points": 5}],
        }
        verified = {"state": "connected", "title": "Shadow"}
        data = ra_overview.view_model(state, verified)
        self.assertFalse(data["playing"])
        self.assertIn("not verified playing", data["game"])
        self.assertIn("21 packets", data["console"])
        self.assertEqual(data["rows"], [("1", "Trial", "Not unlocked", "3/7", "10")])
        self.assertIn("Previous run", data["unlocks"][0])

    def test_live_validated_console_includes_progress_and_trackers(self):
        state = {
            "login": {"ok": True, "user": "tester"},
            "console": {"connected": True, "packets": 200},
            "game": {"serial": "P123456789abcde", "title": "PS1 Example",
                     "achievements": [
                         {"id": 42, "title": "Clear", "state": 2, "points": 25, "measured": ""},
                         {"id": 43, "title": "Progress", "state": 1, "points": 10,
                          "measured": "", "percent": 56.5}],
                     "tracking": [{"title": "Time trial", "value": "1:03.25"}]},
        }
        verified = {"state": "playing", "title": "PS1 Example", "session": 1}
        data = ra_overview.view_model(state, verified)
        self.assertTrue(data["playing"])
        self.assertIn("Playing on PS2", data["game"])
        self.assertEqual(data["rows"][0][2], "Unlocked")
        self.assertEqual(data["rows"][1][3], "56.5%")
        self.assertEqual(data["tracking"], ["Time trial: 1:03.25"])
        verified["title"] = "Different"
        self.assertFalse(ra_overview.view_model(state, verified)["playing"])

    def test_large_or_untrusted_engine_payload_is_bounded(self):
        state = {"game": {"title": "x" * 1000, "achievements": [
            {"id": i, "title": "name" * 50, "state": 1, "points": 999}
            for i in range(1000)]}, "unlocks": [{"title": "unlock"}] * 200}
        result = ra_overview.view_model(state)
        self.assertEqual(len(result["rows"]), 400)
        self.assertEqual(len(result["unlocks"]), 16)
        self.assertLessEqual(len(result["rows"][0][1]), 100)

    def test_stopped_managed_engine_never_reads_an_unrelated_local_listener(self):
        # No Tk window required: exercise the worker with a fake owner.
        import queue
        owner = mock.Mock()
        owner._stop.is_set.side_effect = [False, True]
        owner._queue = queue.Queue(maxsize=2)
        owner.app.is_running.return_value = False
        with mock.patch.object(ra_overview, "fetch_engine_state") as fetch:
            ra_overview.OverviewWindow._run(owner)
        fetch.assert_not_called()
        self.assertIsNone(owner._queue.get_nowait())

    def test_fetch_refuses_oversize_status(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b"a" * (1024 * 1024 + 1)
        with mock.patch("launcher.achievements.account_url", return_value="http://127.0.0.1:18196/"), \
             mock.patch.object(ra_overview.urllib.request, "urlopen", return_value=response):
            with self.assertRaises(ValueError):
                ra_overview.fetch_engine_state()

    def test_fetch_local_engine_json_only(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(
            {"login": {"ok": False}}).encode("utf-8")
        with mock.patch("launcher.achievements.account_url", return_value="http://127.0.0.1:18196/"), \
             mock.patch.object(ra_overview.urllib.request, "urlopen", return_value=response) as request:
            self.assertEqual(ra_overview.fetch_engine_state()["login"]["ok"], False)
        self.assertEqual(request.call_args.args[0], "http://127.0.0.1:18196/state")
        self.assertEqual(request.call_args.kwargs["timeout"], 2)
