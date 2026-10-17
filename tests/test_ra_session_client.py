"""Tests for RetroAchievements session tracking and unlock client.

Never contacts live RetroAchievements endpoints.
"""
import hashlib
import io
import json
import threading
import unittest
from urllib.parse import parse_qs

from launcher.ra_session_client import RASessionClient, SessionInfo, AwardResult
from launcher.ra_sets import SetContext


class SessionClientTests(unittest.TestCase):
    def setUp(self):
        self.context = SetContext("TestUser", 1, "a" * 32, 1234, 21)

    def test_session_info_validation(self):
        info = SessionInfo(1234, 1600000000, (10, 11), (10,))
        self.assertEqual(info.game_id, 1234)
        self.assertEqual(info.server_now, 1600000000)
        self.assertEqual(info.unlocks, (10, 11))
        self.assertEqual(info.hardcore_unlocks, (10,))

        for game_id in (0, -1, 0x100000000, "1234"):
            with self.subTest(game_id=game_id), self.assertRaises(ValueError):
                SessionInfo(game_id)
        with self.assertRaises(ValueError):
            SessionInfo(1234, server_now=-1)
        with self.assertRaises(ValueError):
            SessionInfo(1234, unlocks=[10])
        with self.assertRaises(ValueError):
            SessionInfo(1234, unlocks=(0,))

    def test_award_result_validation(self):
        result = AwardResult(42, True, score=150, softcore_score=150, remaining=3, already_unlocked=False)
        self.assertEqual(result.achievement_id, 42)
        self.assertTrue(result.awarded)
        self.assertEqual(result.score, 150)
        self.assertFalse(result.already_unlocked)

        for aid in (0, -1, 0x100000000, "42"):
            with self.subTest(aid=aid), self.assertRaises(ValueError):
                AwardResult(aid, True)
        with self.assertRaises(ValueError):
            AwardResult(42, "True")
        with self.assertRaises(ValueError):
            AwardResult(42, True, score=-1)

    def test_client_init_and_credential_sanitization(self):
        client = RASessionClient("TestUser", "synthetic-secret-token")
        self.assertNotIn("synthetic-secret-token", repr(client))
        self.assertNotIn("TestUser", repr(client))

        for user in ("", "a" * 65, "bad\0user", "bad\nuser", 123):
            with self.subTest(user=user), self.assertRaises(ValueError):
                RASessionClient(user, "token")
        for token in ("", "t" * 4097, "bad\0token", 123):
            with self.subTest(token=token), self.assertRaises(ValueError):
                RASessionClient("TestUser", token)

    def test_start_session_payload_and_response(self):
        requests = []

        class Opener:
            def open(self, request, timeout):
                requests.append(request)
                body = json.dumps({
                    "Success": True,
                    "ServerNow": 1700000000,
                    "Unlocks": [{"ID": 100}, {"ID": 101}],
                    "HardcoreUnlocks": [{"ID": 100}],
                }).encode("ascii")
                resp = io.BytesIO(body)
                resp.status = 200
                return resp

        client = RASessionClient("TestUser", "synthetic-token", opener=Opener())
        info = client.start_session(self.context)
        self.assertEqual(info.game_id, 1234)
        self.assertEqual(info.server_now, 1700000000)
        self.assertEqual(info.unlocks, (100, 101))
        self.assertEqual(info.hardcore_unlocks, (100,))

        self.assertEqual(len(requests), 1)
        fields = parse_qs(requests[0].data.decode("ascii"))
        self.assertEqual(fields["r"], ["startsession"])
        self.assertEqual(fields["u"], ["TestUser"])
        self.assertEqual(fields["t"], ["synthetic-token"])
        self.assertEqual(fields["g"], ["1234"])
        self.assertEqual(fields["h"], ["0"])
        self.assertEqual(fields["m"], ["a" * 32])

    def test_award_achievement_softcore_signature_and_success(self):
        requests = []

        class Opener:
            def open(self, request, timeout):
                requests.append(request)
                body = json.dumps({
                    "Success": True,
                    "Score": 500,
                    "SoftcoreScore": 250,
                    "AchievementsRemaining": 10,
                }).encode("ascii")
                resp = io.BytesIO(body)
                resp.status = 200
                return resp

        client = RASessionClient("TestUser", "synthetic-token", opener=Opener())
        award = client.award_achievement(self.context, 9876)
        self.assertTrue(award.awarded)
        self.assertFalse(award.already_unlocked)
        self.assertEqual(award.achievement_id, 9876)
        self.assertEqual(award.score, 500)
        self.assertEqual(award.softcore_score, 250)
        self.assertEqual(award.remaining, 10)

        expected_sig = hashlib.md5("9876TestUser0".encode("ascii")).hexdigest()
        fields = parse_qs(requests[0].data.decode("ascii"))
        self.assertEqual(fields["r"], ["awardachievement"])
        self.assertEqual(fields["u"], ["TestUser"])
        self.assertEqual(fields["t"], ["synthetic-token"])
        self.assertEqual(fields["a"], ["9876"])
        self.assertEqual(fields["h"], ["0"])
        self.assertEqual(fields["m"], ["a" * 32])
        self.assertEqual(fields["v"], [expected_sig])

    def test_award_achievement_with_timing_offset(self):
        requests = []

        class Opener:
            def open(self, request, timeout):
                requests.append(request)
                resp = io.BytesIO(b'{"Success":true,"Score":10,"SoftcoreScore":10,"AchievementsRemaining":0}')
                resp.status = 200
                return resp

        client = RASessionClient("TestUser", "synthetic-token", opener=Opener())
        client.award_achievement(self.context, 555, seconds_since_unlock=45)
        expected_sig = hashlib.md5("555TestUser055545".encode("ascii")).hexdigest()
        fields = parse_qs(requests[0].data.decode("ascii"))
        self.assertEqual(fields["v"], [expected_sig])
        self.assertEqual(fields["o"], ["45"])

    def test_award_achievement_idempotent_already_unlocked(self):
        class Opener:
            def open(self, request, timeout):
                body = json.dumps({
                    "Success": False,
                    "Error": "User already has 'Heroic Quest' unlocked.",
                    "Score": 300,
                    "SoftcoreScore": 300,
                    "AchievementsRemaining": 2,
                }).encode("ascii")
                resp = io.BytesIO(body)
                resp.status = 200
                return resp

        client = RASessionClient("TestUser", "synthetic-token", opener=Opener())
        award = client.award_achievement(self.context, 123)
        self.assertTrue(award.awarded)
        self.assertTrue(award.already_unlocked)
        self.assertEqual(award.achievement_id, 123)
        self.assertEqual(award.score, 300)

    def test_award_achievement_failure_raises_sanitized_error(self):
        class Opener:
            def open(self, request, timeout):
                resp = io.BytesIO(b'{"Success":false,"Error":"Invalid token"}')
                resp.status = 200
                return resp

        client = RASessionClient("TestUser", "synthetic-token", opener=Opener())
        with self.assertRaises(ValueError) as ctx:
            client.award_achievement(self.context, 123)
        self.assertNotIn("synthetic-token", str(ctx.exception))
        self.assertNotIn("Invalid token", str(ctx.exception))

    def test_ping_with_and_without_rich_presence(self):
        requests = []

        class Opener:
            def open(self, request, timeout):
                requests.append(request)
                resp = io.BytesIO(b'{"Success":true}')
                resp.status = 200
                return resp

        client = RASessionClient("TestUser", "synthetic-token", opener=Opener())
        self.assertTrue(client.ping(self.context))
        self.assertTrue(client.ping(self.context, rich_presence="World 1-1 - Playing"))

        fields0 = parse_qs(requests[0].data.decode("ascii"))
        self.assertEqual(fields0["r"], ["ping"])
        self.assertEqual(fields0["u"], ["TestUser"])
        self.assertEqual(fields0["g"], ["1234"])
        self.assertEqual(fields0["h"], ["0"])
        self.assertEqual(fields0["x"], ["a" * 32])
        self.assertNotIn("m", fields0)

        fields1 = parse_qs(requests[1].data.decode("ascii"))
        self.assertEqual(fields1["m"], ["World 1-1 - Playing"])

    def test_context_mismatch_and_cancellation(self):
        class Opener:
            def open(self, request, timeout):
                raise AssertionError("Should not be contacted")

        client = RASessionClient("TestUser", "synthetic-token", opener=Opener())
        wrong_account = SetContext("OtherUser", 1, "a" * 32, 1234, 21)
        with self.assertRaises(ValueError):
            client.start_session(wrong_account)
        with self.assertRaises(ValueError):
            client.award_achievement(wrong_account, 1)
        with self.assertRaises(ValueError):
            client.ping(wrong_account)

        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(ValueError):
            client.ping(self.context, cancel=cancel)
