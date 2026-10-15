"""Pinned API fields and read-only account transport; never contact real RA."""
import io
import json
import threading
import unittest
from urllib.parse import parse_qs

from launcher.ra_set_client import RASetClient, _NoRedirect
from launcher.ra_sets import SetContext, qualify_set, decode_response, MAX_RESPONSE


def responses(achievements=None, unlocks=()):
    """Encode the rcheevos patch and softcore unlock response field names."""
    achievements = achievements if achievements is not None else [
        {"ID": 10, "Flags": 3, "MemAddr": "0xH001000=1"},
        {"ID": 11, "Flags": 5, "MemAddr": "0xH001001=1"}]
    return (json.dumps({"Success": True, "PatchData": {
        "ID": 42, "ConsoleID": 21, "Title": "Synthetic", "Achievements": achievements}}).encode(),
        json.dumps({"Success": True, "UserUnlocks": list(unlocks)}).encode())


class SetTests(unittest.TestCase):
    def setUp(self):
        self.context = SetContext("Synthetic", 1, "a" * 32, 42, 21)

    def test_core_default_unofficial_optin_and_prior_unlocks(self):
        core = qualify_set(self.context, *responses())
        self.assertEqual(core.definitions, ((10, "0xH001000=1"),))
        selected = qualify_set(self.context, *responses(unlocks=(10,)), include_unofficial=True)
        self.assertEqual(selected.definitions, ((11, "0xH001001=1"),))
        self.assertEqual(selected.selected_ids, (10, 11))
        self.assertEqual(selected.unofficial_ids, (11,))
        self.assertEqual(selected.prior_unlocks, (10,))

    def test_no_remaining_is_distinct_from_failed_lookup(self):
        self.assertEqual(qualify_set(self.context, *responses(unlocks=(10,))).remaining, 0)
        with self.assertRaises(ValueError):
            qualify_set(self.context, responses()[0], b'{"Success":false,"Error":"private"}')

    def test_identity_mismatch_never_qualifies(self):
        patch, unlocks = responses()
        for name, value in (("ID", 43), ("ConsoleID", 12), ("ID", True)):
            data = json.loads(patch)
            data["PatchData"][name] = value
            with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                qualify_set(self.context, json.dumps(data).encode(), unlocks)
        for extra in ({"GameID": 43}, {"HardcoreMode": 1}, {"HardcoreMode": 0.0}):
            data = json.loads(unlocks)
            data.update(extra)
            with self.assertRaises(ValueError):
                qualify_set(self.context, patch, json.dumps(data).encode())

    def test_duplicate_and_malformed_achievement_ids_are_rejected(self):
        entry = {"ID": 10, "Flags": 3, "MemAddr": "0xH001000=1"}
        for items in ([entry, entry], [dict(entry, ID=True)], [dict(entry, MemAddr="\0")],
                      [dict(entry, Flags=True)], [dict(entry, MemAddr="é")]):
            with self.assertRaises(ValueError):
                qualify_set(self.context, *responses(items))
        with self.assertRaises(ValueError):
            qualify_set(self.context, *responses(unlocks=(True,)))

    def test_response_bounds_and_duplicate_keys(self):
        for data in (b"", b"x" * (MAX_RESPONSE + 1), b'{"Success":true,"Success":false}',
                     b'{"Success":1}', b"[]", b"invalid", b"\xff"):
            with self.assertRaises(ValueError):
                decode_response(data)

    def test_read_only_transport_keeps_token_out_of_url_and_uses_softcore(self):
        requests = []
        patch, unlocks = responses(unlocks=(10,))
        class Opener:
            def open(self, request, timeout):
                requests.append((request, timeout))
                response = io.BytesIO((b'{"Success":true,"GameID":42}', patch, unlocks)[len(requests) - 1])
                response.status = 200
                return response
        client = RASetClient("Synthetic", "private-token", opener=Opener())
        self.assertEqual(client.fetch(self.context).remaining, 0)
        fields = [parse_qs(request.data.decode()) for request, _ in requests]
        self.assertEqual([item["r"] for item in fields], [["gameid"], ["patch"], ["unlocks"]])
        self.assertEqual(fields[0], {"r": ["gameid"], "m": ["a" * 32]})
        self.assertEqual(fields[2]["h"], ["0"])
        self.assertTrue(all(item["t"] == ["private-token"] for item in fields[1:]))
        self.assertTrue(all(request.full_url == client.ENDPOINT and request.method == "POST"
                            for request, _ in requests))
        self.assertNotIn("private-token", repr(client))
        with self.assertRaises(ValueError):
            client._request("awardachievement", 42)

    def test_transport_failures_do_not_echo_credentials_or_follow_redirects(self):
        class Opener:
            def open(self, request, timeout):
                raise OSError("reflected private-token")
        client = RASetClient("Synthetic", "private-token", opener=Opener())
        with self.assertRaises(ValueError) as error:
            client.fetch(self.context)
        self.assertNotIn("private-token", str(error.exception))
        with self.assertRaises(ValueError):
            _NoRedirect().redirect_request(None, None, 307, "", {}, "https://example.org/")

    def test_cancelled_or_wrong_account_does_not_send(self):
        class Opener:
            def open(self, request, timeout):
                raise AssertionError("No request should be sent")
        client = RASetClient("Synthetic", "private-token", opener=Opener())
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(ValueError):
            client.fetch(self.context, cancel=cancel)
        with self.assertRaises(ValueError):
            client.fetch(SetContext("Other", 1, "a" * 32, 42, 21))

    def test_hash_mismatch_stops_before_authenticated_requests(self):
        requests = []
        class Opener:
            def open(self, request, timeout):
                requests.append(request)
                response = io.BytesIO(b'{"Success":true,"GameID":43}')
                response.status = 200
                return response
        with self.assertRaises(ValueError):
            RASetClient("Synthetic", "private-token", opener=Opener()).fetch(self.context)
        self.assertEqual(len(requests), 1)
        self.assertNotIn(b"private-token", requests[0].data)
