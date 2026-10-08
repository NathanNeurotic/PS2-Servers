"""SMB read diagnostics preserve bytes, distinguish EOF and bound logging."""
import contextlib
import io
import os
import struct
import tempfile
import types
import unittest
from unittest.mock import patch

from smbv1_server import smbserver_opl as smb


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = os.path.join(self.directory.name, "game.iso")
        self.data = bytes(range(256)) * 256
        with open(self.path, "wb") as f:
            f.write(self.data)
        self.conn = smb.Conn(smb.SmbServer({}, False))
        self.conn.files[7] = smb.OpenFile(self.path, False)
        self.addCleanup(self.conn.cleanup)

    def read(self, offset, size):
        p = bytearray(24)
        struct.pack_into("<H", p, 4, 7)
        struct.pack_into("<I", p, 6, offset & 0xffffffff)
        struct.pack_into("<H", p, 10, size & 0xffff)
        struct.pack_into("<H", p, 14, size >> 16)
        struct.pack_into("<I", p, 20, offset >> 32)
        return smb.h_read_andx(self.conn, types.SimpleNamespace(params=p))

    def test_repeated_random_reads_after_eight_hour_clock_advance(self):
        with patch.object(smb.time, "monotonic", return_value=self.conn.started + 28800), patch.object(smb, "activity") as log:
            for i in range(4000):
                offset = (i * 997) % (len(self.data) - 2048)
                params, data, status = self.read(offset, 2048)
                self.assertEqual(status, smb.STATUS_SUCCESS)
                self.assertEqual(data, self.data[offset:offset + 2048])
                self.assertEqual(struct.unpack_from("<H", params, 10)[0], 2048)
            self.conn.summary()
        self.assertEqual(self.conn.reads, 4000)
        self.assertEqual(self.conn.bytes_read, 4000 * 2048)
        self.assertEqual(len(self.conn.files), 1)
        self.assertEqual(log.call_count, 2)  # first read and one aggregate summary

    def test_normal_eof_and_high_offset_are_not_short_read_errors(self):
        with patch.object(smb, "activity"):
            self.assertEqual(self.read(len(self.data) - 10, 64)[1], self.data[-10:])
            self.assertEqual(self.read(0x100000000, 64)[1], b"")
        self.assertEqual(self.conn.short_reads, 0)

    def test_truncated_file_reports_actual_short_read(self):
        with open(self.path, "r+b") as f:
            f.truncate(10)
        with patch.object(smb, "activity") as log:
            self.assertEqual(self.read(0, 64)[1], self.data[:10])
        self.assertEqual(self.conn.short_reads, 1)
        self.assertTrue(any("SHORT READ" in str(c) for c in log.call_args_list))

    def test_slow_read_warnings_are_bounded_but_counts_are_exact(self):
        of = self.conn.files[7]
        with patch.object(smb.time, "monotonic", return_value=10), patch.object(smb, "activity") as log:
            for _ in range(100):
                self.conn.record_read(7, of, 0, 64, 64, .3)
        self.assertEqual(self.conn.slow_reads, 100)
        self.assertEqual(log.call_count, 2)

    def test_send_records_backpressure_and_preserves_wire_bytes(self):
        clock = [10.0]
        sent = []
        def sendall(data):
            sent.append(data)
            clock[0] += .4
        self.conn.sock = types.SimpleNamespace(sendall=sendall)
        with patch.object(smb.time, "monotonic", side_effect=lambda: clock[0]), patch.object(smb, "activity"):
            self.conn.send(b"reply")
        self.assertEqual(sent, [b"\x00\x00\x00\x05reply"])
        self.assertEqual(self.conn.slow_sends, 1)

    def test_default_diagnostics_visible_without_verbose(self):
        output = io.StringIO()
        with patch.object(smb, "VERBOSE", False), contextlib.redirect_stderr(output):
            self.read(0, 64)
            self.conn.summary(final=True)
        self.assertIn("reading", output.getvalue())
        self.assertIn("reads=1", output.getvalue())


if __name__ == "__main__":
    unittest.main()
