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
from launcher.ra_receiver import Receiver
from tests.test_ra_protocol import packet
from tests.test_ra_watchlist import watch_file


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
