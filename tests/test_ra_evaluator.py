"""Offline conditions evaluated by the real pinned rcheevos shared library."""
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import unittest
from unittest import mock
import ctypes

from build.build_ra_evaluator import build, library_name
from launcher.ra_evaluator import Evaluator
from launcher.ra_evaluated_receiver import EvaluatedReceiver
from launcher.ra_receiver import Receiver
from tests.test_ra_protocol import packet
from tests.test_ra_watchlist import watch_file
from tests.test_ra_sets import responses
from launcher.ra_sets import SetContext, qualify_set, derive_direct_watch


class EvaluatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("RA_CC", "gcc" if os.name == "nt" else "cc").split()[0]
        if not shutil.which(compiler):
            if os.environ.get("GITHUB_ACTIONS") == "true" and sys.platform.startswith("linux"):
                raise AssertionError("Linux CI requires compiler for native evaluator coverage")
            raise unittest.SkipTest("C compiler required for real rcheevos evaluation")
        cls.temporary = tempfile.TemporaryDirectory(prefix="ra-evaluator-")
        cls.addClassCleanup(cls.temporary.cleanup)
        with mock.patch.dict(os.environ, {"RA_CC": os.environ.get("RA_CC", compiler)}):
            cls.library = build(Path(cls.temporary.name) / library_name())
        # ctypes deliberately retains DLLs for process lifetime. These tests
        # own every load and close every runtime before releasing their loads,
        # so Windows can remove the temporary binary at class cleanup.
        original = ctypes.CDLL
        loads = []
        def load(*args, **kwargs):
            library = original(*args, **kwargs)
            loads.append(library)
            return library
        patcher = mock.patch("launcher.ra_evaluator.ctypes.CDLL", side_effect=load)
        patcher.start()
        cls.addClassCleanup(patcher.stop)
        def unload():
            import _ctypes
            release = _ctypes.FreeLibrary if os.name == "nt" else _ctypes.dlclose
            for library in loads:
                release(library._handle)
        cls.addClassCleanup(unload)

    def evaluator(self, expression="0xH001000=1", watch=None):
        runtime = Evaluator(self.library, {10: expression},
                            watch or watch_file([0x10001000]), 7)
        self.addCleanup(runtime.close)
        return runtime

    def owned_receiver(self):
        self.now = 0
        owner = EvaluatedReceiver(self.library, clock=lambda: self.now)
        self.addCleanup(owner.close)
        self.peer = ("127.0.0.1", 18195)
        self.query = b"RAQ1 " + b"a" * 32 + b" SLUS_210.65"
        owner.receive(self.query, self.peer)
        job, = owner.take_jobs()
        return owner, job

    def install_owned(self, owner, job, expression="0xH001000=1"):
        self.assertTrue(owner.complete(job, watch_file([0x10001000]),
                                       {10: expression}, title="Synthetic"))
        owner.receive(b"RAG1 " + b"a" * 32 + b" 0", self.peer)

    def test_owned_receiver_evaluates_only_after_watch_transfer(self):
        owner, job = self.owned_receiver()
        self.assertTrue(owner.complete(job, watch_file([0x10001000]),
                                       {10: "0xH001000=1"}))
        self.assertIsNone(owner.receive(packet(b"\0", count=1), self.peer).evaluation)
        owner.receive(b"RAG1 " + b"a" * 32 + b" 0", self.peer)
        owner.receive(packet(b"\0", count=1, sequence=1), self.peer)
        result = owner.receive(packet(b"\1", count=1, sequence=2), self.peer)
        self.assertEqual(result.evaluation.triggered, (10,))
        self.assertIsNone(owner.receive(packet(b"\1", count=1, sequence=2), self.peer).evaluation)

    def test_owned_reset_revokes_worker_result_before_native_allocation(self):
        owner, job = self.owned_receiver()
        owner.reset()
        owner.receive(self.query, self.peer)  # same image, new account generation
        with mock.patch("launcher.ra_evaluated_receiver.Evaluator") as allocate:
            self.assertFalse(owner.complete(job, watch_file([0x10001000]),
                                            {10: "0xH001000=1"}))
            allocate.assert_not_called()

    def test_owned_metadata_failure_revokes_partially_installed_watch(self):
        owner, job = self.owned_receiver()
        class InvalidTitle:
            def __str__(self):
                raise ValueError("Invalid title")
        allocated = []
        def allocate(*args):
            runtime = Evaluator(*args)
            allocated.append(runtime)
            return runtime
        with mock.patch("launcher.ra_evaluated_receiver.Evaluator", side_effect=allocate):
            with self.assertRaises(ValueError):
                owner.complete(job, watch_file([0x10001000]),
                               {10: "0xH001000=1"}, title=InvalidTitle())
        self.assertIsNone(owner._receiver.session.watch)
        self.assertIsNone(owner._evaluator)
        runtime, = allocated
        with self.assertRaises(RuntimeError):
            runtime.evaluate(b"\0", generation=runtime.generation, sequence=1)
        self.assertEqual(owner.receive(self.query, self.peer).wire.reply.rstrip(), b"RAA1 WAIT")

    def test_owned_expiry_closes_runtime_and_rejects_old_memory(self):
        owner, job = self.owned_receiver()
        self.install_owned(owner, job)
        runtime = owner._evaluator
        self.now = 16
        owner.tick()
        with self.assertRaises(RuntimeError):
            runtime.evaluate(b"\0", generation=runtime.generation, sequence=1)
        self.assertIsNone(owner.receive(packet(b"\1", count=1), self.peer).evaluation)

    def test_owned_game_change_discards_hits_and_closes_previous_runtime(self):
        owner, job = self.owned_receiver()
        self.install_owned(owner, job, "0xH001000=1.2.")
        old = owner._evaluator
        owner.receive(packet(b"\0", count=1, sequence=1), self.peer)
        owner.receive(packet(b"\1", count=1, sequence=2), self.peer)
        other = self.query.replace(b"a" * 32, b"b" * 32)
        owner.receive(other, self.peer)
        self.assertIsNone(owner._evaluator)
        with self.assertRaises(RuntimeError):
            old.evaluate(b"\1", generation=old.generation, sequence=3)
        job, = owner.take_jobs()
        self.assertTrue(owner.complete(job, watch_file([0x10001000]),
                                       {10: "0xH001000=1.2."}))
        owner.receive(b"RAG1 " + b"b" * 32 + b" 0", self.peer)
        owner.receive(packet(b"\0", count=1, sequence=1), self.peer)
        self.assertFalse(owner.receive(packet(b"\1", count=1, sequence=2), self.peer).evaluation.triggered)
        self.assertEqual(owner.receive(packet(b"\1", count=1, sequence=3), self.peer).evaluation.triggered, (10,))

    def test_owned_invalid_set_is_retryable_and_never_advertised(self):
        owner, job = self.owned_receiver()
        with self.assertRaises(ValueError):
            owner.complete(job, watch_file([0x10001000]), {10: "invalid"})
        reply = owner.receive(self.query, self.peer).wire.reply.rstrip()
        self.assertEqual(reply, b"RAA1 WAIT")
        self.assertIsNone(owner._evaluator)
        retry, = owner.take_jobs()
        self.install_owned(owner, retry)

    def test_owned_close_is_terminal_and_idempotent(self):
        owner, job = self.owned_receiver()
        self.install_owned(owner, job)
        owner.close()
        owner.close()
        with self.assertRaises(RuntimeError):
            owner.receive(self.query, self.peer)

    def frame(self, runtime, value, sequence, generation=7):
        return runtime.evaluate(value, generation=generation, sequence=sequence)

    def test_actual_condition_arming_and_trigger(self):
        runtime = self.evaluator()
        self.assertFalse(self.frame(runtime, b"\1", 0).triggered)
        self.assertFalse(self.frame(runtime, b"\0", 1).triggered)
        self.assertEqual(self.frame(runtime, b"\1", 2).triggered, (10,))
        self.assertFalse(self.frame(runtime, b"\1", 3).triggered)

    def test_duplicate_old_and_wrong_generation_cannot_advance_hit_count(self):
        runtime = self.evaluator("0xH001000=1.2.")
        self.frame(runtime, b"\0", 0)
        self.assertFalse(self.frame(runtime, b"\1", 1).triggered)
        self.assertFalse(self.frame(runtime, b"\1", 1).accepted)
        self.assertFalse(self.frame(runtime, b"\1", 0).accepted)
        self.assertFalse(self.frame(runtime, b"\1", 2, generation=8).accepted)
        self.assertEqual(self.frame(runtime, b"\1", 2).triggered, (10,))

    def test_gap_resets_accumulated_hits_and_does_not_repeat_unlock(self):
        runtime = self.evaluator("0xH001000=1.2.")
        self.frame(runtime, b"\0", 0)
        self.frame(runtime, b"\1", 1)
        gap = self.frame(runtime, b"\0", 3)
        self.assertTrue(gap.reset)
        self.assertFalse(gap.triggered)
        self.frame(runtime, b"\0", 4)
        self.frame(runtime, b"\1", 5)
        self.assertEqual(self.frame(runtime, b"\1", 6).triggered, (10,))
        self.frame(runtime, b"\0", 8)
        self.frame(runtime, b"\1", 9)
        self.assertFalse(self.frame(runtime, b"\1", 10).triggered)

    def test_gap_does_not_reuse_delta_memory_from_previous_frame(self):
        runtime = self.evaluator("d0xH001000=9_0xH001000=1")
        self.frame(runtime, b"\0", 0)
        self.frame(runtime, b"\11", 1)
        self.assertFalse(self.frame(runtime, b"\1", 3).triggered)
        self.frame(runtime, b"\11", 4)
        self.assertEqual(self.frame(runtime, b"\1", 5).triggered, (10,))

    def test_pointer_pairs_are_resolved_by_snapshot_address(self):
        watch = watch_file([0x40001000], [(1 << 13, 0x4)])
        runtime = self.evaluator("I:0xX001000_0xH000004=1", watch)
        def values(number):
            return struct.pack("<III", 0x2000, 0x2004, number)
        self.frame(runtime, values(0), 0)
        self.assertEqual(self.frame(runtime, values(1), 1).triggered, (10,))

    def test_missing_pointer_values_never_trigger_zero_condition(self):
        watch = watch_file([0x40001000], [(1 << 13, 0x4)])
        runtime = self.evaluator("I:0xX001000_0xH000004=0", watch)
        missing = self.frame(runtime, struct.pack("<I", 0x2000), 0)
        self.assertFalse(missing.accepted)
        self.assertIn((0x2004, 1), missing.missing)
        self.assertFalse(missing.triggered)

    def test_sequence_wrap_and_invalid_input_leave_runtime_usable(self):
        runtime = self.evaluator()
        self.frame(runtime, b"\0", 999999)
        with self.assertRaises(ValueError):
            self.frame(runtime, b"", 0)
        result = self.frame(runtime, b"\1", 0)
        self.assertFalse(result.reset)
        self.assertEqual(result.triggered, (10,))
        runtime.close()
        runtime.close()
        with self.assertRaises(RuntimeError):
            self.frame(runtime, b"\0", 1)

    def test_invalid_expression_fails_without_poisoning_next_runtime(self):
        with self.assertRaises(ValueError):
            self.evaluator("not an achievement")
        self.assertTrue(self.frame(self.evaluator(), b"\0", 0).accepted)

    def test_real_receiver_handoff_evaluates_complete_frames(self):
        receiver = Receiver()
        peer = ("127.0.0.1", 18195)
        image_hash = b"a" * 32
        receiver.receive(b"RAQ1 " + image_hash + b" SLUS_210.65", peer)
        job, = receiver.take_jobs()
        watch = watch_file([0x10001000])
        receiver.complete(job, watch)
        receiver.receive(b"RAG1 " + image_hash + b" 0", peer)
        first = receiver.receive(packet(b"\0", count=1, sequence=1), peer)
        with Evaluator(self.library, {10: "0xH001000=1"}, watch, first.generation) as runtime:
            self.assertFalse(runtime.evaluate_frame(first).triggered)
            second = receiver.receive(packet(b"\1", count=1, sequence=2), peer)
            self.assertEqual(runtime.evaluate_frame(second).triggered, (10,))

    def test_independent_runtimes_do_not_share_hits_or_unlocks(self):
        first = self.evaluator("0xH001000=1.2.")
        second = self.evaluator("0xH001000=1.2.")
        for runtime in (first, second):
            self.frame(runtime, b"\0", 0)
        self.frame(first, b"\1", 1)
        self.assertEqual(self.frame(first, b"\1", 2).triggered, (10,))
        self.assertFalse(self.frame(second, b"\1", 1).triggered)
        self.assertEqual(self.frame(second, b"\1", 2).triggered, (10,))

    def qualified(self, expressions, *, unlocks=(), console=21):
        context = SetContext("Synthetic", 1, "a" * 32, 42, console)
        patch, prior = responses([{"ID": index + 10, "Flags": 3, "MemAddr": expression}
                                  for index, expression in enumerate(expressions)], unlocks)
        if console != 21:
            import json
            data = json.loads(patch)
            data["PatchData"]["ConsoleID"] = console
            patch = json.dumps(data).encode()
        return qualify_set(context, patch, prior)

    def test_native_watch_sizes_alternatives_pause_and_deduplication(self):
        qualified = self.qualified([
            "0xH080000=1_0x080004=2_0xW080008=3_0xX08000C=4",
            "P:0xH080000=1S0xH080002=1"])
        data = derive_direct_watch(self.library, qualified)
        words = struct.unpack_from("<5I", data, 16)
        self.assertEqual(words, (0x10080000, 0x20080004, 0x40080008, 0x4008000C, 0x10080002))
        self.assertEqual(struct.unpack_from("<4I", data)[3], 12)

    def test_native_watch_pointer_chain_derivation_and_evaluation(self):
        # Single pointer chain on PS2
        qualified_single = self.qualified(["I:0xX080000_0xH000004=1"])
        data_single = derive_direct_watch(self.library, qualified_single)
        magic, ver, count, direct_bytes = struct.unpack_from("<4I", data_single)
        self.assertEqual((magic, ver, count, direct_bytes), (0x4C574152, 1, 1, 4))
        self.assertEqual(struct.unpack_from("<I", data_single, 16)[0], 0x40080000)
        node_magic, nodes = struct.unpack_from("<2I", data_single, 20)
        self.assertEqual((node_magic, nodes), (0x4C4E4152, 1))
        node_w, node_off = struct.unpack_from("<2I", data_single, 28)
        self.assertEqual((node_w, node_off), (0x2000, 4))
        self.assertEqual(len(data_single), 36)

        # Full evaluator trigger test with pointer pair
        evaluator = Evaluator(self.library, {10: "I:0xX080000_0xH000004=1"}, data_single, 1)
        self.addCleanup(evaluator.close)
        # Frame 1: base pointer points to 0x81000, target byte at 0x81004 is 0
        snapshot1 = struct.pack("<III", 0x81000, 0x81004, 0)
        result1 = evaluator.evaluate(snapshot1, generation=1, sequence=1)
        self.assertTrue(result1.accepted)
        self.assertFalse(result1.triggered)
        # Frame 2: target byte at 0x81004 is 1 -> triggers achievement 10
        snapshot2 = struct.pack("<III", 0x81000, 0x81004, 1)
        result2 = evaluator.evaluate(snapshot2, generation=1, sequence=2)
        self.assertTrue(result2.accepted)
        self.assertEqual(result2.triggered, (10,))

        # Nested pointer chain on PS2
        qualified_nested = self.qualified(["I:0xX080000_I:0xX000004_0xH000008=1"])
        data_nested = derive_direct_watch(self.library, qualified_nested)
        magic, ver, count, direct_bytes = struct.unpack_from("<4I", data_nested)
        self.assertEqual((magic, ver, count, direct_bytes), (0x4C574152, 1, 1, 4))
        node_magic, nodes = struct.unpack_from("<2I", data_nested, 20)
        self.assertEqual((node_magic, nodes), (0x4C4E4152, 2))
        node0_w, node0_off = struct.unpack_from("<2I", data_nested, 28)
        node1_w, node1_off = struct.unpack_from("<2I", data_nested, 36)
        self.assertEqual((node0_w, node0_off), (0x8000, 4))
        self.assertEqual((node1_w, node1_off), (0x3000, 8))
        self.assertEqual(len(data_nested), 44)

        # PS1 pointer chain matches golden baseline case 4 byte-for-byte
        qualified_ps1 = self.qualified(["I:0xX001000_0xH000004=1"], console=12)
        data_ps1 = derive_direct_watch(self.library, qualified_ps1)
        self.assertEqual(data_ps1.hex(),
                         "5241574c0100000001000000040000000010004052414e4c010000000020000004000000")

        # ORBIT profile rejects pointer chains
        with self.assertRaises(ValueError):
            derive_direct_watch(self.library, qualified_single, profile="orbit-v1.0.2")

    def test_native_watch_chain_invalid_expression_and_ram_limits_reject_whole_set(self):
        for expression, console in (("I:0xX001000_0xH000004=1", 21),
                                    ("invalid", 21), ("0xX1FFFFF=1", 12),
                                    ("0xH07FFFF=0", 21), ("0xH2000000=0", 21)):
            with self.subTest(expression=expression), self.assertRaises(ValueError):
                derive_direct_watch(self.library, self.qualified(["0xH080000=1", expression], console=console))

    def test_native_watch_prior_unlocks_are_not_requested_and_orbit_limit_is_enforced(self):
        data = derive_direct_watch(self.library, self.qualified(
            ["0xH080000=1", "0xH080001=1"], unlocks=(10,)))
        self.assertEqual(struct.unpack_from("<I", data, 16)[0], 0x10080001)
        expression = "_".join("0xX{:06X}=1".format(0x80000 + 4 * i) for i in range(305))
        qualified = self.qualified([expression])
        self.assertEqual(struct.unpack_from("<I", derive_direct_watch(self.library, qualified), 12)[0], 1220)
        with self.assertRaises(ValueError):
            derive_direct_watch(self.library, qualified, profile="orbit-v1.0.2")

    def test_qualified_account_set_watch_and_native_evaluation_handoff(self):
        owner = EvaluatedReceiver(self.library)
        self.addCleanup(owner.close)
        owner.bind_account("Synthetic", 1)
        peer = ("127.0.0.1", 18195)
        query = b"RAQ1 " + b"a" * 32 + b" SLUS_210.65"
        owner.receive(query, peer)
        job, = owner.take_jobs()
        self.assertTrue(owner.complete_set(job, self.qualified(["0xH080000=1"])))
        owner.receive(b"RAG1 " + b"a" * 32 + b" 0", peer)
        owner.receive(packet(b"\0", count=1, sequence=1), peer)
        self.assertEqual(owner.receive(packet(b"\1", count=1, sequence=2), peer).evaluation.triggered, (10,))
        owner.bind_account("Synthetic", 2)
        owner.receive(query, peer)
        new_job, = owner.take_jobs()
        self.assertFalse(owner.complete_set(new_job, self.qualified(["0xH080000=1"])))
        self.assertIsNone(owner._evaluator)
        owner.clear_account()
        self.assertFalse(owner.complete_set(new_job, self.qualified(["0xH080000=1"])))

    def test_account_session_qualified_set_commits_to_real_native_receiver(self):
        from launcher.ra_account_worker import AccountSession, AccountResult
        from launcher.ra_credentials import Credentials
        owner = EvaluatedReceiver(self.library)
        self.addCleanup(owner.close)
        store = mock.Mock()
        session = AccountSession(owner, store)
        login = session.begin_login("Synthetic", "transient-password")
        self.assertTrue(session.accept(login, AccountResult(login.task_id, login.epoch, "ok",
                                        credentials=Credentials("Synthetic", "synthetic-token"))))
        peer = ("127.0.0.1", 18195)
        owner.receive(b"RAQ1 " + b"a" * 32 + b" SLUS_210.65", peer)
        identification, = owner.take_jobs()
        lookup = session.begin_set(identification, 42, 21)
        qualified = self.qualified(["0xH080000=1"])
        result = AccountResult(lookup.task_id, lookup.epoch, "ok", qualified=qualified)
        self.assertTrue(session.accept(lookup, result))
        owner.receive(b"RAG1 " + b"a" * 32 + b" 0", peer)
        owner.receive(packet(b"\0", count=1, sequence=1), peer)
        self.assertEqual(owner.receive(packet(b"\1", count=1, sequence=2), peer).evaluation.triggered, (10,))
        session.logout()
        self.assertIsNone(owner._evaluator)
        self.assertFalse(session.accept(lookup, result))
        store.save.assert_not_called()
