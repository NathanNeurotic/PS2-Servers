"""Pinned RAWL/RANL layout vectors and game-generation lifecycle regressions."""
import struct
import unittest

from launcher.ra_watchlist import WatchSession, validate_watchlist
from tests.test_ra_protocol import packet


def watch_file(entries, nodes=(), declared_bytes=None):
    """Encode explicit little-endian reference structures for test fixtures."""
    size = sum(word >> 28 for word in entries) if declared_bytes is None else declared_bytes
    data = struct.pack("<4I", 0x4C574152, 1, len(entries), size)
    data += b"".join(struct.pack("<I", word) for word in entries)
    if nodes:
        data += struct.pack("<2I", 0x4C4E4152, len(nodes))
        data += b"".join(struct.pack("<2I", *node) for node in nodes)
    return data


class WatchValidationTests(unittest.TestCase):
    def test_direct_and_pointer_tail_layouts(self):
        """Node parents follow reference dependency order and permit signed offsets."""
        entries = [0x40080000, 0x20080004]
        direct = validate_watchlist(watch_file(entries))
        self.assertEqual((direct.count, direct.direct_bytes, direct.snapshot_sizes),
                         (2, 6, frozenset({6})))
        nodes = [(4 << 13, 0xFFFFFFFF), ((2 << 13) | (1 << 12), 4)]
        chained = validate_watchlist(watch_file(entries, nodes))
        self.assertEqual((chained.nodes, chained.snapshot_sizes), (2, frozenset({6, 22})))
        # Console returns zero outside RAM; do not reject an otherwise valid list.
        self.assertEqual(validate_watchlist(watch_file([0x10000000])).direct_bytes, 1)

    def test_truncated_or_corrupt_files(self):
        """Reject truncation, inconsistent totals, unsupported sizes and extra tails."""
        valid = watch_file([0x40080000])
        invalid = [valid[:i] for i in range(len(valid))]
        invalid += [valid + b"x", b"FAIL" + valid[4:],
                    valid[:4] + struct.pack("<I", 2) + valid[8:],
                    watch_file([0x30080000]), watch_file([0x40080000], declared_bytes=3),
                    watch_file([]), watch_file([0x40080000] * 4097)]
        for data in invalid:
            with self.subTest(length=len(data)), self.assertRaises(ValueError):
                validate_watchlist(data)

    def test_invalid_pointer_graphs_and_snapshot_capacity(self):
        """Forward/self references and oversized snapshots cannot be served."""
        entries = [0x40080000]
        for nodes in ([(4 << 13 | 1, 0)],
                      [(4 << 13 | 1 << 12, 0)],
                      [(3 << 13, 0)],
                      [(4 << 13, 0)] * 129):
            with self.subTest(nodes=len(nodes)), self.assertRaises(ValueError):
                validate_watchlist(watch_file(entries, nodes))
        with self.assertRaises(ValueError):
            validate_watchlist(watch_file([0x40080000] * 4096))


class WatchLifecycleTests(unittest.TestCase):
    def test_reinstall_same_count_drops_inflight_values(self):
        """A same-serial/count list replacement must still invalidate assembly."""
        session = WatchSession()
        session.install("a" * 32, "SLUS_210.65", watch_file([0x40080000, 0x20080004]))
        self.assertIsNone(session.feed(packet(b"abcd", parts=2)))
        previous = session.generation
        session.install("b" * 32, "SLUS_210.65", watch_file([0x40090000, 0x20090004]))
        self.assertGreater(session.generation, previous)
        self.assertIsNone(session.feed(packet(b"ef", index=1, parts=2)))
        self.assertIsNone(session.chunk("a" * 32, 0))
        self.assertIsNotNone(session.chunk("b" * 32, 0))
        self.assertEqual(session.feed(packet(b"abcd", parts=2)), b"abcdef")

    def test_clear_and_invalid_replacement(self):
        """Validation is atomic; explicit clear revokes all serving and evaluation."""
        session = WatchSession()
        session.install("a" * 32, "SLUS_210.65", watch_file([0x40080000]))
        generation = session.generation
        with self.assertRaises(ValueError):
            session.install("b" * 32, "SLUS_210.65", b"broken")
        self.assertEqual(session.generation, generation)
        session.clear()
        self.assertIsNone(session.chunk("a" * 32, 0))
        self.assertIsNone(session.feed(packet()))
