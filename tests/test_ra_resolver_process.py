"""Spawn-based hard cancellation tests with intentionally stuck callbacks."""
import multiprocessing
import io
import pickle
import os
import threading
import time
import unittest

from launcher.ra_owned_service import IdentificationResult, OwnedService
from launcher.ra_receiver import IdentificationJob
from launcher.ra_resolver_process import ProcessResolver, _ResultUnpickler


def stuck(job, cancel):
    while True:
        time.sleep(1)  # intentionally ignore cooperative cancellation


def supported(job, cancel):
    return IdentificationResult(unsupported=True)


def crash(job, cancel):
    raise RuntimeError("Synthetic lookup failure")


def oversized(job, cancel):
    return IdentificationResult(watch_file=b"x" * 17433)


def partial_write(job, cancel):
    """POSIX sender writes a header and one payload byte, then stalls."""
    import struct
    from multiprocessing.connection import Connection
    def send_partial(connection, data):
        connection._send(struct.pack("!i", len(data)))
        connection._send(bytes(data[:1]))
        while True:
            time.sleep(1)
    Connection._send_bytes = send_partial
    return IdentificationResult(unsupported=True)


class SignalledStuck:
    """Prove callback execution, rather than merely timing out process startup."""
    def __init__(self, entered):
        self.entered = entered

    def __call__(self, job, cancel):
        self.entered.set()
        return stuck(job, cancel)


class ProcessResolverTests(unittest.TestCase):
    def setUp(self):
        self.job = IdentificationJob(1, "a" * 32, "SLUS_210.65")
        self.before = {p.pid for p in multiprocessing.active_children()}

    def tearDown(self):
        self.assertEqual({p.pid for p in multiprocessing.active_children()}, self.before)

    def test_result_unpickler_rejects_executable_globals_before_validation(self):
        from launcher.ra_resolver_process import valid_identification_result
        from launcher.ra_sets import SetContext, QualifiedSet
        with self.assertRaises(pickle.UnpicklingError):
            _ResultUnpickler(io.BytesIO(b"cbuiltins\neval\n(V1+1\ntR.")).load()
        value = IdentificationResult(unsupported=True)
        self.assertEqual(_ResultUnpickler(io.BytesIO(pickle.dumps(value))).load(), value)
        context = SetContext("Synthetic", 1, "a" * 32, 42, 21)
        qualified = QualifiedSet(context, "Synthetic", ((10, "0xH080000=1"),), (10,), (), ())
        self.assertTrue(valid_identification_result(IdentificationResult(qualified=qualified)))
        self.assertTrue(valid_identification_result(IdentificationResult(
            watch_file=b"x" * 20, definitions=((10, "0xH080000=1"),))))
        self.assertFalse(valid_identification_result(IdentificationResult(
            watch_file=b"x" * 20, qualified=qualified)))
        self.assertFalse(valid_identification_result(IdentificationResult(
            definitions=((10, "0xH080000=1"),))))
        self.assertFalse(valid_identification_result(IdentificationResult(
            unsupported=True, profile="invalid")))

    def test_deadline_reaps_noncooperative_child(self):
        resolver = ProcessResolver(stuck, timeout=0.3)
        started = time.monotonic()
        self.assertIsNone(resolver(self.job, threading.Event()))
        self.assertLess(time.monotonic() - started, 3)
        resolver.resolve = supported
        # Recovery is the assertion here, not sub-300ms Windows spawn. Keep
        # the short deadline above for the deliberately stuck callback only.
        resolver.timeout = 5
        self.assertTrue(resolver(self.job, threading.Event()).unsupported)

    @unittest.skipIf(os.name == "nt", "POSIX byte-stream framing; Windows uses message-mode pipes")
    def test_partial_ipc_payload_cannot_block_deadline_or_leave_a_reader(self):
        resolver = ProcessResolver(partial_write, timeout=2)
        started = time.monotonic()
        self.assertIsNone(resolver(self.job, threading.Event()))
        self.assertLess(time.monotonic() - started, 4)
        self.assertIsNone(resolver._reader)
        resolver.resolve, resolver.timeout = supported, 5
        self.assertTrue(resolver(self.job, threading.Event()).unsupported)

    def test_cancellation_reaps_child_before_deadline(self):
        entered = multiprocessing.get_context("spawn").Event()
        resolver = ProcessResolver(SignalledStuck(entered), timeout=30)
        cancel = threading.Event()
        def cancel_after_entry():
            entered.wait(2)
            cancel.set()
        timer = threading.Thread(target=cancel_after_entry)
        timer.start()
        started = time.monotonic()
        try:
            self.assertIsNone(resolver(self.job, cancel))
        finally:
            timer.join()
        self.assertTrue(entered.is_set())
        self.assertLess(time.monotonic() - started, 3)

    def test_result_and_exception_are_transported_without_exception_text(self):
        self.assertTrue(ProcessResolver(supported)(self.job, threading.Event()).unsupported)
        self.assertIsNone(ProcessResolver(crash)(self.job, threading.Event()))
        self.assertIsNone(ProcessResolver(oversized)(self.job, threading.Event()))

    def test_account_reset_cancels_stuck_lookup_and_resolves_next_job(self):
        import socket
        entered = multiprocessing.get_context("spawn").Event()
        resolver = ProcessResolver(SignalledStuck(entered), timeout=30)
        service = OwnedService(resolver, advertised_replies=False)
        address = service.start()
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
                client.settimeout(1)
                query = b"RAQ1 " + b"a" * 32
                client.sendto(query, address)
                self.assertTrue(client.recv(1024).startswith(b"RAA1 WAIT"))
                self.assertTrue(entered.wait(2))
                resolver.resolve = supported
                service.reset()
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    client.sendto(query, address)
                    reply = client.recv(1024)
                    if reply.startswith(b"RAA1 NO"):
                        break
                    threading.Event().wait(0.05)
                else:
                    self.fail("Reset did not cancel the lookup and permit a new one")
        finally:
            self.assertTrue(service.stop())

    def test_service_stop_reaps_stuck_lookup_and_restarts(self):
        import socket
        service = OwnedService(ProcessResolver(stuck, timeout=30), advertised_replies=False)
        address = service.start()
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
                client.settimeout(1)
                client.sendto(b"RAQ1 " + b"a" * 32, address)
                self.assertTrue(client.recv(1024).startswith(b"RAA1 WAIT"))
            deadline = time.monotonic() + 2
            while service._active_job is None and time.monotonic() < deadline:
                threading.Event().wait(0.01)
            self.assertIsNotNone(service._active_job)
            self.assertTrue(service.stop())
            service.start()
        finally:
            self.assertTrue(service.stop())
