"""Owned login and HTTP pacing; fake responses, no real RA account traffic."""
import io
import threading
import time
import unittest
import urllib.error
from urllib.parse import parse_qs

from launcher.ra_account import AccountClient
from launcher.ra_http import Transport, RateLimited


class AccountTests(unittest.TestCase):
    def test_password_and_token_login_use_post_and_return_only_runtime_credentials(self):
        requests = []
        class Opener:
            def open(self, request, timeout):
                requests.append(request)
                response = io.BytesIO(b'{"Success":true,"User":"Canonical","Token":"new-token"}')
                response.status = 200
                return response
        client = AccountClient(opener=Opener())
        for options, field in (({"password": "transient-password"}, "p"), ({"token": "old-token"}, "t")):
            credentials = client.login("canonical", **options)
            self.assertEqual(credentials.username, "Canonical")
            self.assertEqual(credentials.token, "new-token")
            fields = parse_qs(requests[-1].data.decode())
            self.assertEqual(fields["r"], ["login2"])
            self.assertEqual(fields[field], [next(iter(options.values()))])
            self.assertNotIn(next(iter(options.values())), requests[-1].full_url)
            self.assertNotIn("new-token", repr(credentials))

    def test_failed_or_malformed_login_does_not_return_reflected_secrets(self):
        class Opener:
            def open(self, request, timeout):
                response = io.BytesIO(b'{"Success":false,"Error":"transient-password"}')
                response.status = 200
                return response
        with self.assertRaises(ValueError) as error:
            AccountClient(opener=Opener()).login("Synthetic", password="transient-password")
        self.assertNotIn("transient-password", str(error.exception))
        for options in ({}, {"password": "a", "token": "b"}, {"password": ""}):
            with self.assertRaises(ValueError):
                AccountClient(opener=Opener()).login("Synthetic", **options)

    def test_pacing_is_cancelled_before_another_request(self):
        calls = []
        class Opener:
            def open(self, request, timeout):
                calls.append(request)
                response = io.BytesIO(b"{}")
                response.status = 200
                return response
        transport = Transport(opener=Opener())
        transport.post({"r": "gameid"})
        transport._next_request = time.monotonic() + 30
        cancel = threading.Event()
        timer = threading.Timer(0.05, cancel.set)
        timer.start()
        try:
            started = time.monotonic()
            with self.assertRaises(ValueError):
                transport.post({"r": "gameid"}, cancel=cancel)
            self.assertLess(time.monotonic() - started, 1)
            self.assertEqual(len(calls), 1)
        finally:
            timer.join()

    def test_server_backoff_is_sanitized_and_bounded(self):
        for raw, expected in (("120", 120), ("99999", 300), ("nan", 60), ("invalid", 60)):
            class Opener:
                def open(self, request, timeout):
                    raise urllib.error.HTTPError(request.full_url, 429, "private-token", {"Retry-After": raw}, None)
            with self.assertRaises(RateLimited) as error:
                Transport(opener=Opener()).post({"r": "login2", "t": "private-token"})
            self.assertEqual(error.exception.retry_after, expected)
            self.assertNotIn("private-token", str(error.exception))
