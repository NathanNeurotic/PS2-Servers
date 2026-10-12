"""Real loopback transport tests; no RA account or external network calls."""
import socket
import threading
import time
import unittest
from unittest import mock

from launcher.ra_owned_service import OwnedService, IdentificationResult
from tests.test_ra_protocol import packet
from tests.test_ra_watchlist import watch_file


class OwnedServiceTests(unittest.TestCase):
    def setUp(self):
        self.client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.client.bind(("127.0.0.1", 0))
        self.client.settimeout(1)
        self.services = []
        self.release = threading.Event()
        self.watch = watch_file([0x40080000, 0x20080004])
        self.query = b"RAQ1 " + b"a" * 32 + b" SLUS_210.65"

    def tearDown(self):
        self.release.set()
        for service in self.services:
            self.assertTrue(service.stop())
        self.client.close()

    def start(self, resolve, **options):
        service = OwnedService(resolve, advertised_replies=False, **options)
        self.services.append(service)
        self.address = service.start()
        return service

    def request(self, data):
        self.client.sendto(data, self.address)
        return self.client.recvfrom(2000)[0].rstrip()

    def ready(self, query=None):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            reply = self.request(query or self.query)
            if reply != b"RAA1 WAIT":
                return reply
            threading.Event().wait(0.04)
        self.fail("Identification did not finish")

    def test_blocked_lookup_keeps_udp_responsive_and_prevents_worker_accumulation(self):
        entered = threading.Event()
        def resolve(job, cancel):
            entered.set()
            self.release.wait(3)
            return IdentificationResult(self.watch)
        service = self.start(resolve)
        self.assertEqual(self.request(self.query), b"RAA1 WAIT")
        self.assertTrue(entered.wait(1))
        self.assertTrue(self.request(b"RAP1 127.0.0.1 18195").startswith(b"RAO1 OK"))
        self.assertFalse(service.stop(timeout=0.1))
        self.assertFalse(service._loop.is_alive())
        with self.assertRaises(RuntimeError):
            service.start()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.bind(self.address)  # listener released despite blocked callback

    def test_handshake_transfer_latest_frame_and_reset(self):
        service = self.start(lambda job, cancel: IdentificationResult(self.watch, title="Test"))
        self.assertTrue(self.ready().startswith(b"RAA1 OK 24 1"))
        self.assertTrue(self.request(b"RAG1 " + b"a" * 32 + b" 0").startswith(b"RAC1 0 24"))
        self.client.sendto(packet(b"abcdef"), self.address)
        deadline = time.monotonic() + 1
        frame = None
        while frame is None and time.monotonic() < deadline:
            frame = service.take_frame()
            threading.Event().wait(0.01)
        self.assertIsNotNone(frame)
        self.assertEqual(frame.values, b"abcdef")
        service.reset()
        self.assertIsNone(service.take_frame())
        self.assertTrue(self.ready().startswith(b"RAA1 OK"))

    def test_stale_game_result_is_rejected(self):
        entered = threading.Event()
        def resolve(job, cancel):
            if job.image_hash == "a" * 32:
                entered.set()
                self.release.wait(3)
                return IdentificationResult(unsupported=True)
            return IdentificationResult(self.watch)
        self.start(resolve)
        self.request(self.query)
        self.assertTrue(entered.wait(1))
        other = self.query.replace(b"a" * 32, b"b" * 32)
        self.assertEqual(self.request(other), b"RAA1 WAIT")
        self.release.set()
        self.assertTrue(self.ready(other).startswith(b"RAA1 OK"))

    def test_lookup_exception_is_retryable_and_bind_conflict_is_visible(self):
        calls = []
        def resolve(job, cancel):
            calls.append(job)
            if len(calls) == 1:
                raise OSError("Synthetic API timeout")
            return IdentificationResult(unsupported=True)
        self.start(resolve)
        conflict = OwnedService(resolve, address=self.address)
        with self.assertRaises(OSError):
            conflict.start()
        self.assertEqual(self.ready(), b"RAA1 NO")
        self.assertEqual(len(calls), 2)

    def test_clean_stop_allows_restart_with_no_previous_set(self):
        service = self.start(lambda job, cancel: IdentificationResult(self.watch))
        self.assertTrue(self.ready().startswith(b"RAA1 OK"))
        self.assertTrue(service.stop())
        self.address = service.start()
        self.assertEqual(self.request(self.query), b"RAA1 WAIT")
        self.assertIsNone(service.take_frame())

    def test_invalid_options_fail_before_socket_or_threads_exist(self):
        service = OwnedService(lambda job, cancel: None, lease_seconds=0)
        with mock.patch("launcher.ra_owned_service.socket.socket") as constructor:
            with self.assertRaises(ValueError):
                service.start()
            constructor.assert_not_called()
        self.assertTrue(service.stop())

    def test_icmp_receive_errors_do_not_stop_discovery(self):
        # Inject the two platform-specific ICMP errors, then use the actual
        # loopback socket to prove the listener remains operational.
        real_socket = socket.socket
        def make_socket(*args, **kwargs):
            listener = real_socket(*args, **kwargs)
            wrapper = mock.Mock(wraps=listener)
            remaining = [ConnectionResetError(10054, "Synthetic ICMP"),
                         ConnectionRefusedError(111, "Synthetic ICMP")]
            def recvfrom(size):
                if remaining:
                    raise remaining.pop(0)
                return listener.recvfrom(size)
            wrapper.recvfrom.side_effect = recvfrom
            return wrapper
        with mock.patch("launcher.ra_owned_service.socket.socket", side_effect=make_socket):
            service = self.start(lambda job, cancel: IdentificationResult(unsupported=True))
        self.assertTrue(self.request(b"RAP1 127.0.0.1 18195").startswith(b"RAO1 OK"))
        self.assertEqual(self.ready(), b"RAA1 NO")
        self.assertTrue(service._loop.is_alive())

    def test_complete_frames_are_fifo_and_overflow_is_observable(self):
        service = self.start(lambda job, cancel: IdentificationResult(self.watch),
                             frame_capacity=2)
        self.ready()
        self.request(b"RAG1 " + b"a" * 32 + b" 0")
        for sequence in (1, 2):
            self.client.sendto(packet(b"abcdef", sequence=sequence), self.address)
        self.request(b"RAP1 127.0.0.1 18195")
        self.assertEqual(service.take_frame().sequence, 1)
        self.assertEqual(service.take_frame().sequence, 2)
        self.assertIsNone(service.take_frame())
        for sequence in (3, 4, 5):
            self.client.sendto(packet(b"abcdef", sequence=sequence), self.address)
        self.request(b"RAP1 127.0.0.1 18195")
        self.assertEqual(service.dropped_frames, 2)
        self.assertEqual(service.take_frame().sequence, 5)
        self.assertIsNone(service.take_frame())

    def test_reset_clears_entire_frame_backlog(self):
        service = self.start(lambda job, cancel: IdentificationResult(self.watch))
        self.ready()
        self.request(b"RAG1 " + b"a" * 32 + b" 0")
        for sequence in (1, 2, 3):
            self.client.sendto(packet(b"abcdef", sequence=sequence), self.address)
        self.request(b"RAP1 127.0.0.1 18195")
        service.reset()
        self.assertIsNone(service.take_frame())
        self.request(b"RAP1 127.0.0.1 18195")
        self.assertIsNone(service.take_frame())
