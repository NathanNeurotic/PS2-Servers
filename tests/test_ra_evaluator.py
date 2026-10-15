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
            "0xH001000=1_0x001004=2_0xW001008=3_0xX00100C=4",
            "P:0xH001000=1S0xH001002=1"])
        data = derive_direct_watch(self.library, qualified)
        words = struct.unpack_from("<5I", data, 16)
        self.assertEqual(words, (0x10001000, 0x10001002, 0x20001004, 0x40001008, 0x4000100C))
        self.assertEqual(struct.unpack_from("<4I", data)[3], 12)

    def test_native_watch_chain_invalid_expression_and_ram_limits_reject_whole_set(self):
        for expression, console in (("I:0xX001000_0xH000004=1", 21),
                                    ("invalid", 21), ("0xX1FFFFF=1", 12)):
            with self.subTest(expression=expression), self.assertRaises(ValueError):
                derive_direct_watch(self.library, self.qualified(["0xH001000=1", expression], console=console))

    def test_native_watch_prior_unlocks_are_not_requested_and_orbit_limit_is_enforced(self):
        data = derive_direct_watch(self.library, self.qualified(
            ["0xH001000=1", "0xH001001=1"], unlocks=(10,)))
        self.assertEqual(struct.unpack_from("<I", data, 16)[0], 0x10001001)
        expression = "_".join("0xX{:06X}=1".format(0x1000 + 4 * i) for i in range(305))
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
        self.assertTrue(owner.complete_set(job, self.qualified(["0xH001000=1"])))
        owner.receive(b"RAG1 " + b"a" * 32 + b" 0", peer)
        owner.receive(packet(b"\0", count=1, sequence=1), peer)
        self.assertEqual(owner.receive(packet(b"\1", count=1, sequence=2), peer).evaluation.triggered, (10,))
        owner.bind_account("Synthetic", 2)
        owner.receive(query, peer)
        new_job, = owner.take_jobs()
        self.assertFalse(owner.complete_set(new_job, self.qualified(["0xH001000=1"])))
        self.assertIsNone(owner._evaluator)
        owner.clear_account()
        self.assertFalse(owner.complete_set(new_job, self.qualified(["0xH001000=1"])))
