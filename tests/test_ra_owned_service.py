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

    def test_account_binding_requires_library_and_profile_requires_library(self):
        with self.assertRaises(ValueError):
            OwnedService(lambda job, cancel: None, profile="profile")
        service = OwnedService(lambda job, cancel: None)
        with self.assertRaises(RuntimeError):
            service.bind_account("Synthetic", 1)
        with self.assertRaises(RuntimeError):
            service.clear_account(forget=True)
        with self.assertRaises(RuntimeError):
            service.run_account(lambda session: None)


class EvaluatedOwnedServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import ctypes
        import os
        from pathlib import Path
        import shutil
        import sys
        import tempfile
        from build.build_ra_evaluator import build, library_name
        compiler = os.environ.get("RA_CC", "gcc" if os.name == "nt" else "cc").split()[0]
        if not shutil.which(compiler):
            if os.environ.get("GITHUB_ACTIONS") == "true" and sys.platform.startswith("linux"):
                raise AssertionError("Linux CI requires compiler for native evaluator coverage")
            raise unittest.SkipTest("C compiler required for real rcheevos evaluation")
        cls.temporary = tempfile.TemporaryDirectory(prefix="ra-owned-service-")
        cls.addClassCleanup(cls.temporary.cleanup)
        with mock.patch.dict(os.environ, {"RA_CC": os.environ.get("RA_CC", compiler)}):
            cls.library = build(Path(cls.temporary.name) / library_name())
        original = ctypes.CDLL
        loads = []
        def load(*args, **kwargs):
            library = original(*args, **kwargs)
            loads.append(library)
            return library
        patcher_eval = mock.patch("launcher.ra_evaluator.ctypes.CDLL", side_effect=load)
        patcher_sets = mock.patch("launcher.ra_sets.ctypes.CDLL", side_effect=load)
        patcher_eval.start()
        patcher_sets.start()
        cls.addClassCleanup(patcher_eval.stop)
        cls.addClassCleanup(patcher_sets.stop)
        def unload():
            import _ctypes
            release = _ctypes.FreeLibrary if os.name == "nt" else _ctypes.dlclose
            for library in loads:
                release(library._handle)
        cls.addClassCleanup(unload)

    def setUp(self):
        from pathlib import Path
        import tempfile
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.profile = Path(self.temp_dir.name) / "profile"
        self.client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.client.bind(("127.0.0.1", 0))
        self.client.settimeout(1)
        self.services = []
        self.query = b"RAQ1 " + b"a" * 32 + b" SLUS_210.65"

    def tearDown(self):
        for service in self.services:
            self.assertTrue(service.stop())
        self.client.close()

    def start(self, resolve, **options):
        service = OwnedService(resolve, advertised_replies=False, library=self.library, **options)
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

    def wait_evaluation(self, service):
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            evaluated = service.take_evaluation()
            if evaluated is not None:
                return evaluated
            threading.Event().wait(0.01)
        self.fail("Evaluated frame did not arrive")

    def qualified(self, expressions=("0xH080000=1",), *, account="Synthetic", epoch=1,
                  image_hash="a" * 32, unlocks=(), titles=None, points=None):
        from launcher.ra_sets import SetContext, qualify_set
        from tests.test_ra_sets import responses
        context = SetContext(account, epoch, image_hash, 42, 21)
        achievements = []
        for index, expression in enumerate(expressions):
            item = {"ID": index + 10, "Flags": 3, "MemAddr": expression}
            if titles and index < len(titles):
                item["Title"] = titles[index]
            if points and index < len(points):
                item["Points"] = points[index]
            achievements.append(item)
        patch, prior = responses(achievements, unlocks)
        return qualify_set(context, patch, prior)

    def test_qualified_set_handoff_derives_watch_and_evaluates_frames_over_udp(self):
        service = self.start(lambda job, cancel: self.qualified(image_hash=job.image_hash))
        service.bind_account("Synthetic", 1)
        self.assertEqual(service.account, "Synthetic")
        self.assertEqual(service.account_epoch, 1)
        reply = self.ready()
        self.assertTrue(reply.startswith(b"RAA1 OK 20 1 1 0 0 Synthetic"))
        self.assertTrue(self.request(b"RAG1 " + b"a" * 32 + b" 0").startswith(b"RAC1 0 20"))
        self.client.sendto(packet(b"\0", count=1, sequence=1), self.address)
        first = self.wait_evaluation(service)
        self.assertTrue(first.evaluation.accepted)
        self.assertFalse(first.evaluation.triggered)
        self.assertEqual(service.take_frame().values, b"\0")
        self.client.sendto(packet(b"\1", count=1, sequence=2), self.address)
        second = self.wait_evaluation(service)
        self.assertEqual(second.evaluation.triggered, (10,))
        self.assertEqual(service.take_frame().values, b"\1")

    def test_pointer_chain_qualified_set_evaluates_over_udp(self):
        import struct
        service = self.start(lambda job, cancel: self.qualified(
            ("I:0xX080000_0xH000004=1",), image_hash=job.image_hash))
        service.bind_account("Synthetic", 1)
        reply = self.ready()
        self.assertTrue(reply.startswith(b"RAA1 OK 36 1 1 0 0 Synthetic"))
        self.assertTrue(self.request(b"RAG1 " + b"a" * 32 + b" 0").startswith(b"RAC1 0 36"))
        self.client.sendto(packet(struct.pack("<III", 0x81000, 0x81004, 0), count=1, sequence=1), self.address)
        self.assertFalse(self.wait_evaluation(service).evaluation.triggered)
        self.client.sendto(packet(struct.pack("<III", 0x81000, 0x81004, 1), count=1, sequence=2), self.address)
        self.assertEqual(self.wait_evaluation(service).evaluation.triggered, (10,))

    def test_account_session_login_set_commit_and_logout_revocation_on_udp_owner(self):
        from launcher.ra_account_worker import AccountResult
        from launcher.ra_credentials import Credentials, CredentialStore
        service = self.start(lambda job, cancel: None, profile=self.profile)
        def login_and_commit(session):
            task = session.begin_login("Synthetic", "transient-password")
            result = AccountResult(task.task_id, task.epoch, "ok",
                                   credentials=Credentials("Synthetic", "synthetic-token"))
            return session.accept(task, result, remember=True)
        self.assertTrue(service.run_account(login_and_commit))
        self.assertEqual(service.account, "Synthetic")
        self.assertEqual(service.account_epoch, 1)
        self.assertEqual(CredentialStore(self.profile).load(),
                         Credentials("Synthetic", "synthetic-token"))
        # Now resolve returns AccountResult for epoch 1
        service.resolve = lambda job, cancel: AccountResult(
            1, 1, "ok", qualified=self.qualified(epoch=1, image_hash=job.image_hash))
        self.assertTrue(self.ready().startswith(b"RAA1 OK 20 1"))
        self.request(b"RAG1 " + b"a" * 32 + b" 0")
        self.client.sendto(packet(b"\0", count=1, sequence=1), self.address)
        self.wait_evaluation(service)
        # Logout with forget=True revokes active evaluation and clears stored token
        service.resolve = lambda job, cancel: None
        service.clear_account(forget=True)
        self.assertIsNone(service.account)
        self.assertIsNone(service.take_evaluation())
        self.assertIsNone(service.take_frame())
        self.assertIsNone(service.run_account(lambda session: session.username))
        self.assertEqual(self.request(self.query), b"RAA1 WAIT")
        self.assertIsNone(CredentialStore(self.profile).load())

    def test_mismatched_epoch_and_empty_remaining_set_remain_retryable_not_unsupported(self):
        calls = []
        def resolve(job, cancel):
            calls.append(job)
            if len(calls) == 1:
                # Wrong account epoch (epoch 99 vs bound epoch 1) -> rejected, remains retryable
                return self.qualified(epoch=99, image_hash=job.image_hash)
            if len(calls) == 2:
                # Zero remaining achievements (all unlocked) -> raises ValueError on derive, remains retryable
                return self.qualified(unlocks=(10,), image_hash=job.image_hash)
            return self.qualified(epoch=1, image_hash=job.image_hash)
        service = self.start(resolve)
        service.bind_account("Synthetic", 1)
        self.assertTrue(self.ready().startswith(b"RAA1 OK 20 1"))
        self.assertEqual(len(calls), 3)

    def test_evaluation_overflow_and_game_switch_discard_stale_evaluations(self):
        service = self.start(lambda job, cancel: self.qualified(image_hash=job.image_hash),
                             frame_capacity=2)
        service.bind_account("Synthetic", 1)
        self.ready()
        self.request(b"RAG1 " + b"a" * 32 + b" 0")
        for sequence in (1, 2, 3):
            self.client.sendto(packet(b"\1", count=1, sequence=sequence), self.address)
        self.request(b"RAP1 127.0.0.1 18195")
        self.assertEqual(service.dropped_evaluations, 2)
        evaluated = service.take_evaluation()
        self.assertEqual(evaluated.wire.sequence, 3)
        self.assertIsNone(service.take_evaluation())
        # Queue another frame, then switch game before reading it
        self.client.sendto(packet(b"\0", count=1, sequence=4), self.address)
        self.request(self.query.replace(b"a" * 32, b"b" * 32))
        self.assertIsNone(service.take_evaluation())
        self.assertIsNone(service.take_frame())

    def test_end_to_end_unlock_notice_delivery_base_opl_profile(self):
        service = self.start(lambda job, cancel: self.qualified(
            titles=["First Unlock"], points=[10], image_hash=job.image_hash))
        service.bind_account("Synthetic", 1)
        self.ready()
        self.request(b"RAG1 " + b"a" * 32 + b" 0")

        # Frame 1: value 0 (no trigger)
        self.client.sendto(packet(b"\0", count=1, sequence=1), self.address)
        first = self.wait_evaluation(service)
        self.assertFalse(first.evaluation.triggered)
        self.assertEqual(first.notices, ())

        # Frame 2: value 1 (triggers achievement 10)
        self.client.sendto(packet(b"\1", count=1, sequence=2), self.address)
        second = self.wait_evaluation(service)
        self.assertEqual(second.evaluation.triggered, (10,))
        self.assertEqual(len(second.notices), 1)
        self.assertTrue(second.notices[0].startswith(b"RAU1 10 10"))
        self.assertEqual(len(second.notices[0]) % 64, 0)

        # Receive RAU1 on the client socket
        notice_pkt, _ = self.client.recvfrom(2000)
        self.assertEqual(notice_pkt, second.notices[0])

        # Console sends RAK1 acknowledgment
        self.client.sendto(b"RAK1 10", self.address)
        raw = service._raw_receiver(service._receiver)
        deadline = time.monotonic() + 1
        while 10 not in raw.acknowledged and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertIn(10, raw.acknowledged)

    def test_end_to_end_unlock_notice_delivery_caduceus_profile(self):
        service = self.start(lambda job, cancel: IdentificationResult(
            qualified=self.qualified(titles=["Café Master"], points=[25], image_hash=job.image_hash),
            profile="caduceus"))
        service.bind_account("Synthetic", 1)
        self.ready()
        self.request(b"RAG1 " + b"a" * 32 + b" 0")

        self.client.sendto(packet(b"\0", count=1, sequence=1), self.address)
        first = self.wait_evaluation(service)
        self.assertFalse(first.evaluation.triggered)

        self.client.sendto(packet(b"\1", count=1, sequence=2), self.address)
        evaluated = self.wait_evaluation(service)
        self.assertEqual(evaluated.evaluation.triggered, (10,))
        self.assertEqual(len(evaluated.notices), 1)
        # Caduceus profile includes cleaned title in notice
        self.assertTrue(evaluated.notices[0].startswith(b"RAU1 10 25 Cafe Master"))
        self.assertEqual(len(evaluated.notices[0]) % 64, 0)

        notice_pkt, _ = self.client.recvfrom(2000)
        self.assertEqual(notice_pkt, evaluated.notices[0])

    def test_console_heartbeat_and_ack_handling(self):
        service = self.start(lambda job, cancel: self.qualified(image_hash=job.image_hash))
        service.bind_account("Synthetic", 1)
        self.ready()
        raw = service._raw_receiver(service._receiver)
        initial_activity = raw.last_activity

        time.sleep(0.05)
        self.client.sendto(b"RAH1", self.address)
        deadline = time.monotonic() + 1
        while raw.last_activity == initial_activity and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertGreater(raw.last_activity, initial_activity)

        self.client.sendto(b"RAK2 55 66", self.address)
        deadline = time.monotonic() + 1
        while not ({55, 66} <= raw.acknowledged) and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue({55, 66} <= raw.acknowledged)


