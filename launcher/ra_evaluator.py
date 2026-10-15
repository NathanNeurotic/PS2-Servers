"""Owned offline evaluation through rcheevos's public C runtime API.

No xeRAbora desktop sources, credentials, HTTP or unlock submission are used.
One instance represents one already-qualified set and watch-list generation.
Callers must supply every complete frame in order; skipped sequence numbers
reset temporal evaluation. This is not yet selected by the active service.
"""
import ctypes
from dataclasses import dataclass
import struct
import threading
from pathlib import Path
import platform

from launcher.ra_watchlist import validate_watchlist


class _Event(ctypes.Structure):
    _fields_ = [("id", ctypes.c_uint32), ("value", ctypes.c_int32), ("type", ctypes.c_uint8)]


_EVENT_CALLBACK = ctypes.CFUNCTYPE(None, ctypes.POINTER(_Event))
_PEEK_CALLBACK = ctypes.CFUNCTYPE(ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
                                ctypes.c_void_p)
TRIGGERED = 3  # RC_RUNTIME_EVENT_ACHIEVEMENT_TRIGGERED, pinned rc_runtime.h


def library_path():
    """Locate the staged owned library in a source or frozen installation."""
    from launcher.servers import is_frozen
    name = {"Windows": "rcheevos-owned.dll", "Darwin": "librcheevos-owned.dylib"}.get(
        platform.system(), "librcheevos-owned.so")
    return Path(__file__).resolve().parents[1] / ("native" if is_frozen() else "build/native") / name


@dataclass(frozen=True)
class Evaluation:
    """Local events only; these are never proof of a submitted RA unlock."""
    accepted: bool
    events: tuple = ()
    reset: bool = False
    missing: tuple = ()

    @property
    def triggered(self):
        """Achievement IDs whose conditions triggered in this accepted frame."""
        return tuple(event[1] for event in self.events if event[0] == TRIGGERED)


class Evaluator:
    """Opaque native runtime with bounded snapshots and explicit disposal.

    Definitions are a mapping of integer achievement IDs to rcheevos MemAddr
    strings. Account eligibility, core/unofficial flags and prior unlocks must
    be resolved before construction. Missing memory conservatively rejects
    the entire frame and resets conditions, rather than treating it as zero.
    """

    def __init__(self, library, definitions, watch_file, generation):
        if type(generation) is not int or generation < 1:
            raise ValueError("Invalid evaluator generation")
        if not isinstance(definitions, dict) or not 1 <= len(definitions) <= 4096:
            raise ValueError("Invalid achievement definitions")
        encoded = []
        for achievement_id, definition in definitions.items():
            if type(achievement_id) is not int or not 1 <= achievement_id <= 0xFFFFFFFF:
                raise ValueError("Invalid achievement ID")
            if not isinstance(definition, str) or not 1 <= len(definition) <= 16384 or "\0" in definition:
                raise ValueError("Invalid achievement expression")
            encoded.append((achievement_id, definition.encode("ascii")))
        if sum(len(data) for _, data in encoded) > 4 * 1024 * 1024:
            raise ValueError("Achievement set exceeds evaluator limit")
        self.watch = validate_watchlist(watch_file)
        self.generation = generation
        self._sequence = None
        self._triggered = set()
        self._definitions = encoded
        self._lock = threading.Lock()
        self._runtime = None
        self._library = lib = ctypes.CDLL(str(library))
        lib.rc_runtime_alloc.argtypes = []
        lib.rc_runtime_alloc.restype = ctypes.c_void_p
        lib.rc_runtime_destroy.argtypes = [ctypes.c_void_p]
        lib.rc_runtime_destroy.restype = None
        lib.rc_runtime_reset.argtypes = [ctypes.c_void_p]
        lib.rc_runtime_reset.restype = None
        lib.rc_runtime_activate_achievement.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                                       ctypes.c_char_p, ctypes.c_void_p, ctypes.c_int]
        lib.rc_runtime_activate_achievement.restype = ctypes.c_int
        lib.rc_runtime_do_frame.argtypes = [ctypes.c_void_p, _EVENT_CALLBACK,
                                           _PEEK_CALLBACK, ctypes.c_void_p, ctypes.c_void_p]
        lib.rc_runtime_do_frame.restype = None
        self._runtime = self._allocate()

    def _allocate(self):
        """Create a fresh runtime, including fresh delta/prior memory history."""
        lib = self._library
        runtime = lib.rc_runtime_alloc()
        if not runtime:
            raise MemoryError("Cannot allocate achievement runtime")
        try:
            for achievement_id, expression in self._definitions:
                if achievement_id in self._triggered:
                    continue
                code = lib.rc_runtime_activate_achievement(runtime, achievement_id,
                                                           expression, None, 0)
                if code != 0:
                    raise ValueError(f"Invalid achievement {achievement_id}: rcheevos error {code}")
        except BaseException:
            lib.rc_runtime_destroy(runtime)
            raise
        return runtime

    def _reset(self):
        # rc_runtime_reset resets conditions but retains memref history. Build
        # a replacement before retiring the old runtime to clear both safely.
        replacement = self._allocate()
        self._library.rc_runtime_destroy(self._runtime)
        self._runtime = replacement

    def close(self):
        """Release the native runtime; safe to call repeatedly."""
        with self._lock:
            if self._runtime:
                self._library.rc_runtime_destroy(self._runtime)
                self._runtime = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    @staticmethod
    def _add_reads(reads, address, value):
        # Match native memory callbacks: a read must fit inside one entry.
        for size in (1, 2, 4):
            for offset in range(len(value) - size + 1):
                reads.setdefault((address + offset, size),
                                 int.from_bytes(value[offset:offset + size], "little"))

    def _reads(self, values):
        reads, offset = {}, 0
        for index in range(self.watch.count):
            word, = struct.unpack_from("<I", self.watch.data, 16 + 4 * index)
            size, address = word >> 28, word & 0x0FFFFFFF
            self._add_reads(reads, address, values[offset:offset + size])
            offset += size
        if len(values) > self.watch.direct_bytes:
            start = 16 + 4 * self.watch.count + 8
            for index in range(self.watch.nodes):
                word, = struct.unpack_from("<I", self.watch.data, start + 8 * index)
                address, = struct.unpack_from("<I", values, offset)
                size = (word >> 13) & 7
                if address:
                    self._add_reads(reads, address, values[offset + 4:offset + 4 + size])
                offset += 8
        return reads

    def evaluate(self, values, *, generation, sequence):
        """Evaluate one complete snapshot; reject replays and reset after gaps."""
        with self._lock:
            if not self._runtime:
                raise RuntimeError("Evaluator is closed")
            if generation != self.generation:
                return Evaluation(False)
            if type(sequence) is not int or not 0 <= sequence < 1000000:
                raise ValueError("Invalid snapshot sequence")
            if not isinstance(values, bytes) or len(values) not in self.watch.snapshot_sizes:
                raise ValueError("Snapshot does not match watch list")
            delta = ((sequence - self._sequence) % 1000000
                     if self._sequence is not None else 1)
            if delta == 0 or delta >= 500000:
                return Evaluation(False)
            reset = delta != 1
            if reset:
                self._reset()
            reads, events, missing = self._reads(values), [], set()
            @_PEEK_CALLBACK
            def peek(address, size, _):
                key = (address, size)
                if key not in reads:
                    missing.add(key)
                    return 0
                return reads[key]
            @_EVENT_CALLBACK
            def event(pointer):
                value = pointer.contents
                events.append((value.type, value.id, value.value))
            self._library.rc_runtime_do_frame(self._runtime, event, peek, None, None)
            self._sequence = sequence
            if missing:
                self._reset()
                return Evaluation(False, reset=True, missing=tuple(sorted(missing)))
            events = [item for item in events
                      if item[0] != TRIGGERED or item[1] not in self._triggered]
            self._triggered.update(item[1] for item in events if item[0] == TRIGGERED)
            return Evaluation(True, tuple(events), reset)

    def evaluate_frame(self, frame):
        """Consume a complete ReceiverOutput, preserving generation and sequence."""
        return self.evaluate(frame.values, generation=frame.generation, sequence=frame.sequence)


def package_selfcheck():
    """Exercise native callbacks and memory-history recovery without an account."""
    from launcher.ra_sets import SetContext, QualifiedSet, derive_direct_watch
    watch = struct.pack("<5I", 0x4C574152, 1, 1, 1, 0x10080000)
    qualified = QualifiedSet(SetContext("Synthetic", 1, "a" * 32, 42, 21),
                             "Synthetic", ((10, "0xH080000=1"),), (10,), (), ())
    if derive_direct_watch(library_path(), qualified) != watch:
        raise RuntimeError("Native direct-watch derivation mismatch")
    with Evaluator(library_path(), {10: "0xH080000=1"}, watch, 1) as runtime:
        runtime.evaluate(b"\0", generation=1, sequence=0)
        if runtime.evaluate(b"\1", generation=1, sequence=1).triggered != (10,):
            raise RuntimeError("Native achievement condition did not trigger")
    with Evaluator(library_path(), {10: "d0xH080000=9_0xH080000=1"}, watch, 1) as runtime:
        runtime.evaluate(b"\0", generation=1, sequence=0)
        runtime.evaluate(b"\11", generation=1, sequence=1)
        if runtime.evaluate(b"\1", generation=1, sequence=3).triggered:
            raise RuntimeError("Native delta memory crossed a frame gap")
        runtime.evaluate(b"\11", generation=1, sequence=4)
        if runtime.evaluate(b"\1", generation=1, sequence=5).triggered != (10,):
            raise RuntimeError("Native evaluator did not recover after a frame gap")
    return {"native_condition": True, "temporal_history_reset": True, "direct_watch": True}
