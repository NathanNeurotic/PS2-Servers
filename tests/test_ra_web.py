"""Tests for the native RetroAchievements HTTP status and SSE dashboard."""
import json
import threading
import time
import unittest
import urllib.request
import urllib.error

from launcher.ra_web import RAState, RAWebServer


class RAWebTests(unittest.TestCase):
    def setUp(self):
        self.state = RAState()
        self.server = RAWebServer(0, self.state)
        self.server.start()
        self.port = self.server.server_port
        self.base_url = f"http://127.0.0.1:{self.port}"

    def tearDown(self):
        self.server.close()

    def test_state_lifecycle_and_serialization(self):
        state = RAState()
        d = state.to_dict()
        self.assertEqual(d["login"]["ok"], False)
        self.assertEqual(d["console"]["connected"], False)
        self.assertEqual(d["game"]["id"], 0)
        self.assertEqual(d["unlocks"], [])

        state.set_account("PlayerOne", True)
        state.update_console(connected=True, ip="192.168.1.42", status="playing", packets=10, frames=5, watch_addresses=20)
        state.set_game(1001, "Test Game", serial="SLUS-20001", image_hash="a" * 32, achievements=[
            {"id": 1, "title": "First", "points": 5, "unlocked": False},
            {"id": 2, "title": "Second", "points": 10, "unlocked": True},
        ])
        state.note_unlock(1, title="First", points=5)

        d = state.to_dict()
        self.assertEqual(d["login"]["user"], "PlayerOne")
        self.assertEqual(d["login"]["ok"], True)
        self.assertEqual(d["console"]["connected"], True)
        self.assertEqual(d["console"]["ip"], "192.168.1.42")
        self.assertEqual(d["console"]["packets"], 10)
        self.assertEqual(d["console"]["frames"], 5)
        self.assertEqual(d["console"]["watch"]["addresses"], 20)
        self.assertEqual(d["game"]["id"], 1001)
        self.assertEqual(d["game"]["title"], "Test Game")
        self.assertEqual(d["game"]["serial"], "SLUS-20001")
        self.assertEqual(len(d["game"]["achievements"]), 2)
        # Check that note_unlock marked achievement 1 as unlocked
        self.assertTrue(d["game"]["achievements"][0]["unlocked"])
        self.assertEqual(len(d["unlocks"]), 1)
        self.assertEqual(d["unlocks"][0]["id"], 1)

    def test_get_index_html(self):
        for path in ("/", "/index.html"):
            req = urllib.request.Request(f"{self.base_url}{path}")
            with urllib.request.urlopen(req, timeout=3) as resp:
                self.assertEqual(resp.status, 200)
                self.assertIn("text/html", resp.headers.get("Content-Type", ""))
                body = resp.read().decode("utf-8")
                self.assertIn("PS2-Servers RetroAchievements", body)
                self.assertIn("Owned Runtime", body)

    def test_get_state_json(self):
        self.state.set_account("Gamer99", True)
        self.state.set_game(2002, "Racing Champions", serial="SCES-50001")

        req = urllib.request.Request(f"{self.base_url}/state")
        with urllib.request.urlopen(req, timeout=3) as resp:
            self.assertEqual(resp.status, 200)
            self.assertIn("application/json", resp.headers.get("Content-Type", ""))
            data = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(data["login"]["user"], "Gamer99")
            self.assertEqual(data["game"]["title"], "Racing Champions")

    def test_get_profile_and_game_endpoints(self):
        self.state.set_account("SpeedRunner", True)
        self.state.set_game(3003, "Platformer Deluxe", serial="SLUS-21000")

        with urllib.request.urlopen(f"{self.base_url}/profile", timeout=3) as resp:
            profile = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(profile["user"], "SpeedRunner")
            self.assertTrue(profile["ok"])

        with urllib.request.urlopen(f"{self.base_url}/game", timeout=3) as resp:
            game = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(game["id"], 3003)
            self.assertEqual(game["title"], "Platformer Deluxe")

        with urllib.request.urlopen(f"{self.base_url}/library", timeout=3) as resp:
            lib = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(len(lib["games"]), 1)
            self.assertEqual(lib["games"][0]["title"], "Platformer Deluxe")

    def test_get_events_sse_stream(self):
        self.state.set_account("SSEUser", True)
        req = urllib.request.Request(f"{self.base_url}/events")
        with urllib.request.urlopen(req, timeout=3) as resp:
            self.assertEqual(resp.status, 200)
            self.assertIn("text/event-stream", resp.headers.get("Content-Type", ""))
            # Read first event line
            line = resp.readline().decode("utf-8")
            self.assertTrue(line.startswith("data: "))
            data = json.loads(line[6:].strip())
            self.assertEqual(data["login"]["user"], "SSEUser")

    def test_post_logout(self):
        self.state.set_account("LoggedInUser", True)
        req = urllib.request.Request(f"{self.base_url}/logout", data=b"", method="POST")
        with urllib.request.urlopen(req, timeout=3) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(data.get("ok"))
        self.assertFalse(self.state.logged_in)
        self.assertEqual(self.state.user, "")

    def test_not_found(self):
        req = urllib.request.Request(f"{self.base_url}/unknown-route")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=3)
        self.assertEqual(ctx.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
