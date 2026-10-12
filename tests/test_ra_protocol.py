"""Console-source wire vectors and adversarial RA15 assembly checks.

Field order/widths come from ra_fields in the pinned console raudp.c, not
the owned parser. Payloads are synthetic and no RA API is contacted.
"""
import unittest

from launcher.ra_protocol import (MAX_VALUES, SnapshotAssembler, parse_snapshot,
                                  parse_request, pad_reply, watch_chunk)


def packet(values=b"abcd", sequence=1, index=0, parts=1, count=2,
           serial="SLUS_210.65"):
    """Build the pinned console's complete field layout with synthetic values."""
    return (f"RA15 seq=000001 sz=0000 us=00000 mx=00000 rxq=000 fail=000000"
            f" err=+000 sk=000000 lk=100F sq={sequence:06d} ds=000000 bad=0000"
            f" n={count:04d} rc=0000000 fc=0000000 vb={len(values):04d}"
            f" pt={index} np={parts} id={serial:~<15} ").encode("ascii") + values


class RequestTests(unittest.TestCase):
    def test_full_console_requests(self):
        """Decode reference discovery, identification and chunk request fields."""
        discovery = parse_request(b"RAP1 192.0.2.10 18195")
        self.assertEqual((discovery.kind, discovery.address, discovery.port),
                         ("RAP1", "192.0.2.10", 18195))
        identify = parse_request(b"RAQ1 " + b"a" * 32 + b" SLUS_210.65 192.0.2.10 18195")
        self.assertEqual((identify.image_hash, identify.serial), ("a" * 32, "SLUS_210.65"))
        chunk = parse_request(b"RAG1 " + b"A" * 32 + b" 12 192.0.2.10 18195")
        self.assertEqual((chunk.image_hash, chunk.index), ("a" * 32, 12))

    def test_invalid_requests(self):
        """Malformed endpoints or indices must not reach future receiver routing."""
        for data in (b"", b"RAP1 192.0.2.10 0", b"RAP1 192.0.2.10 65536",
                     b"RAP1 invalid 18195", b"RAP1 192.0.2.10 18195 extra",
                     b"RAG1 " + b"a" * 32 + b" -1 192.0.2.10 18195",
                     b"RAQ1 invalid SLUS_210.65 192.0.2.10 18195",
                     b"RAP1 192.0.2.10 18195\0", b"x" * 257):
            with self.subTest(data=data), self.assertRaises(ValueError):
                parse_request(data)

    def test_chunk_boundary_and_reference_space_padding(self):
        """RAC1 uses 896-byte slices, binary-safe lengths and space padding."""
        values = bytes(range(256)) * 4
        first = b"RAC1 0 896 " + values[:896]
        self.assertEqual(watch_chunk(values, 0), first + b" " * (960 - len(first)))
        second = b"RAC1 1 128 " + values[896:]
        self.assertEqual(watch_chunk(values, 1), second + b" " * (192 - len(second)))
        self.assertEqual(pad_reply(b"x" * 128), b"x" * 128)
        for index in (-1, 2, True):
            with self.assertRaises(ValueError):
                watch_chunk(values, index)
        with self.assertRaises(ValueError):
            pad_reply(b"x" * 960)


class ParserTests(unittest.TestCase):
    def test_reference_field_layout_and_binary_tail(self):
        """Header-looking binary values must never be parsed as fields."""
        values = b"\0 id=bad sq=999999\xff"
        result = parse_snapshot(packet(values))
        self.assertEqual((result.serial, result.sequence, result.count),
                         ("SLUS_210.65", 1, 2))
        self.assertEqual(result.values, values)

    def test_older_sender_without_optional_counters(self):
        """Optional timing/FIFO counters do not determine payload offsets."""
        data = packet().replace(b" rc=0000000 fc=0000000", b"")
        self.assertEqual(parse_snapshot(data).values, b"abcd")

    def test_truncation_and_invalid_dimensions(self):
        """Reject every truncated prefix and impossible packet dimensions."""
        valid = packet()
        for length in range(len(valid)):
            with self.subTest(length=length), self.assertRaises(ValueError):
                parse_snapshot(valid[:length])
        invalid = [valid + b"x", packet(parts=0), packet(index=1),
                   packet(count=4097), packet(b""), b"x" * 1473,
                   valid.replace(b"sq=000001", b"sq=00001x"),
                   valid.replace(b"sq=000001", b"sq=000001 sq=000001"),
                   valid.replace(b"id=SLUS_210.65~~~~", b"id=SLUS~210.65~~~~"),
                   valid.replace(b"RA15", b"RA14")]
        for data in invalid:
            with self.subTest(data=data), self.assertRaises(ValueError):
                parse_snapshot(data)


class AssemblyTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.assembler = SnapshotAssembler("SLUS_210.65", 2, {6, 14},
                                           clock=lambda: self.now)

    def test_last_fragment_first_and_duplicate(self):
        """An out-of-order last fragment must retain its real eventual offset."""
        last = packet(b"ef", index=1, parts=2)
        self.assertIsNone(self.assembler.feed(last))
        self.assertIsNone(self.assembler.feed(last))
        self.assertEqual(self.assembler.feed(packet(b"abcd", parts=2)), b"abcdef")
        self.assertIsNone(self.assembler.feed(last))

    def test_no_mixed_frame_or_delayed_old_frame(self):
        """Loss and delayed packets cannot combine values from different frames."""
        self.assembler.feed(packet(b"old!", sequence=1, parts=2))
        self.assembler.feed(packet(b"ef", sequence=2, index=1, parts=2))
        self.assertIsNone(self.assembler.feed(packet(b"old!", sequence=1, parts=2)))
        self.assertEqual(self.assembler.feed(packet(b"abcd", sequence=2, parts=2)),
                         b"abcdef")

    def test_conflicting_duplicates_poison_only_current_frame(self):
        """Contradictory fragments must not produce an evaluable frame."""
        self.assembler.feed(packet(b"abcd", parts=2))
        self.assembler.feed(packet(b"xxxx", parts=2))
        self.assertIsNone(self.assembler.feed(packet(b"ef", index=1, parts=2)))
        self.assertEqual(self.assembler.feed(packet(b"abcdef", sequence=2)), b"abcdef")

    def test_session_identity_and_watch_count(self):
        """Foreign serials and stale watch lists cannot advance assembly state."""
        self.assembler.feed(packet(b"abcd", parts=2))
        self.assertIsNone(self.assembler.feed(packet(b"ef", index=1, parts=2,
                                                    serial="SLUS_999.99")))
        self.assertIsNone(self.assembler.feed(packet(b"ef", index=1, parts=2, count=3)))
        self.assertEqual(self.assembler.feed(packet(b"ef", index=1, parts=2)), b"abcdef")

    def test_sequence_wrap_and_timeout_restart(self):
        """Six-digit sequence wrap and explicit idle restart both recover."""
        self.assertEqual(self.assembler.feed(packet(b"abcdef", sequence=999999)), b"abcdef")
        self.assertEqual(self.assembler.feed(packet(b"abcdef", sequence=0)), b"abcdef")
        self.assertIsNone(self.assembler.feed(packet(b"abcdef", sequence=999998)))
        self.now = 15
        self.assertEqual(self.assembler.feed(packet(b"abcdef", sequence=999998)), b"abcdef")

    def test_invalid_shape_size_and_part_count(self):
        """Only equal nonfinal chunks and supported complete sizes may evaluate."""
        cases = [
            [packet(b"abc", parts=2), packet(b"defg", index=1, parts=2)],
            [packet(b"ab", parts=3), packet(b"cde", index=1, parts=3),
             packet(b"f", index=2, parts=3)],
            [packet(b"abcd", parts=2), packet(b"ef", index=1, parts=3)],
            [packet(b"abcde")],
        ]
        for frames in cases:
            self.assembler.reset()
            for frame in frames:
                self.assertIsNone(self.assembler.feed(frame))

    def test_pointer_tail_size_and_memory_bound(self):
        """Accept configured node pairs without accepting arbitrary byte counts."""
        self.assertEqual(self.assembler.feed(packet(b"x" * 14)), b"x" * 14)
        self.assembler.reset()
        self.assertIsNone(self.assembler.feed(packet(b"x" * 15)))
        self.assertEqual(self.assembler.fragments, {})
        with self.assertRaises(ValueError):
            SnapshotAssembler("SLUS_210.65", 2, {MAX_VALUES + 1})

    def test_duplicate_replay_does_not_extend_timeout(self):
        """An old fragment replay must not prevent a console sequence restart."""
        first = packet(b"abcd", sequence=100, parts=2)
        self.assembler.feed(first)
        self.now = 14
        self.assembler.feed(first)
        self.now = 15
        self.assertEqual(self.assembler.feed(packet(b"abcdef", sequence=0)), b"abcdef")
