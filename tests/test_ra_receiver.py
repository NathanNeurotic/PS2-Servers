"""Deterministic receiver ownership, asynchronous lifecycle and wire regressions."""
import unittest

from launcher.ra_receiver import Receiver
from tests.test_ra_protocol import packet
from tests.test_ra_watchlist import watch_file


class ReceiverTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.receiver = Receiver(clock=lambda: self.now)
        self.peer = ("192.0.2.10", 18195)
        self.hash = "a" * 32
        self.query = f"RAQ1 {self.hash} SLUS_210.65 192.0.2.10 18195".encode()
        self.watch = watch_file([0x40080000, 0x20080004])

    def identify(self):
        """Start a synthetic set resolution with no account or network calls."""
        result = self.receiver.receive(self.query, self.peer)
        self.assertEqual(result.reply.rstrip(), b"RAA1 WAIT")
        return self.receiver.take_jobs()[0]

    def chunk(self):
        """Request the current synthetic watch list."""
        return self.receiver.receive(f"RAG1 {self.hash} 0 192.0.2.10 18195".encode(), self.peer)

    def test_wait_ready_transfer_then_memory(self):
        """Memory is gated on a resolved set and completed watch transfer."""
        job = self.identify()
        self.assertTrue(self.receiver.complete(job, self.watch, title="Test", achievements=2))
        ready = self.receiver.receive(self.query, self.peer)
        self.assertEqual(ready.reply.rstrip(), b"RAA1 OK 24 1 2 0 0 Test")
        self.assertFalse(self.receiver.receive(packet(b"abcdef"), self.peer).values)
        self.assertTrue(self.chunk().reply.startswith(b"RAC1 0 24 "))
        memory = self.receiver.receive(packet(b"abcdef"), self.peer)
        self.assertEqual(memory.values, b"abcdef")
        self.assertGreater(memory.generation, 0)
        self.assertFalse(self.receiver.receive(packet(b"abcdef"), self.peer).values)

    def test_pending_deduplication_and_stale_game_result(self):
        """A game switch invalidates old asynchronous work and previous bytes."""
        old = self.identify()
        self.receiver.receive(self.query, self.peer)
        self.assertEqual(self.receiver.take_jobs(), ())
        other = self.query.replace(b"a" * 32, b"b" * 32)
        self.receiver.receive(other, self.peer)
        new = self.receiver.take_jobs()[0]
        self.assertFalse(self.receiver.complete(old, self.watch))
        self.assertTrue(self.receiver.complete(new, self.watch))
        self.assertFalse(self.chunk().reply)

    def test_unsupported_differs_from_transient_failure(self):
        """A failed API lookup remains retryable instead of claiming no support."""
        job = self.identify()
        self.receiver.complete(job)
        self.assertEqual(self.receiver.receive(self.query, self.peer).reply.rstrip(), b"RAA1 WAIT")
        retry = self.receiver.take_jobs()[0]
        self.assertNotEqual(retry.token, job.token)
        self.receiver.complete(retry, unsupported=True)
        self.assertEqual(self.receiver.receive(self.query, self.peer).reply.rstrip(), b"RAA1 NO")

    def test_owner_lease_and_account_reset_revoke_workers(self):
        """Another console cannot take a live lease; timeout or logout revokes it."""
        job = self.identify()
        other_peer = ("192.0.2.20", 18195)
        self.assertFalse(self.receiver.receive(self.query, other_peer).reply)
        self.now = 15
        self.assertTrue(self.receiver.receive(self.query, other_peer).reply)
        self.assertFalse(self.receiver.complete(job, self.watch))
        new = self.receiver.take_jobs()[0]
        self.receiver.clear()
        self.assertFalse(self.receiver.complete(new, self.watch))
        self.assertFalse(self.receiver.receive(packet(), other_peer).values)

    def test_expired_identification_retries_under_active_lease(self):
        """Menu retries keep the lease but cannot keep an old worker current."""
        old = self.identify()
        for moment in (10, 20, 30):
            self.now = moment
            self.receiver.receive(self.query, self.peer)
        new = self.receiver.take_jobs()[0]
        self.assertNotEqual(old.token, new.token)
        self.assertFalse(self.receiver.complete(old, self.watch))

    def test_reply_policy_preserves_nat_and_allows_explicit_source_only(self):
        """Address routing is explicit and independent of console ownership."""
        discovery = b"RAP1 192.0.2.30 18195"
        self.assertEqual(self.receiver.receive(discovery, self.peer).destination,
                         ("192.0.2.30", 18195))
        strict = Receiver(clock=lambda: self.now, advertised_replies=False)
        self.assertEqual(strict.receive(discovery, self.peer).destination, self.peer)

    def test_bounded_work_and_malformed_inputs(self):
        """Input floods cannot queue multiple jobs or return unbounded replies."""
        self.identify()
        for _ in range(100):
            self.receiver.receive(self.query, self.peer)
        self.assertFalse(self.receiver.receive(self.query, self.peer).reply)
        self.assertEqual(self.receiver.take_jobs(), ())
        self.now = 1
        self.assertTrue(self.receiver.receive(self.query, self.peer).reply)
        for invalid in (b"RA15 malformed", b"RAP1", None, b"x" * 2000):
            self.assertFalse(self.receiver.receive(invalid, self.peer).reply)

    def test_telemetry_budget_is_not_control_query_budget(self):
        """Normal 60 Hz snapshots must not be limited by the 40-query budget."""
        self.receiver.complete(self.identify(), self.watch)
        self.chunk()
        for sequence in range(100):
            self.assertEqual(self.receiver.receive(packet(b"abcdef", sequence=sequence),
                                                    self.peer).values, b"abcdef")
