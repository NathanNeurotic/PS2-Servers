"""SMBv1 READ_ANDX must return bytes written by WRITE_ANDX, at once.

The read handle used to be buffered: it kept up to 8 KiB from an earlier read
and served it again after a write (through the separate write handle) had
changed those bytes on disk. A VMC block read back right after it was written
came back old. This drives the real handlers on one connection and two.
"""

import os
import struct
import sys
import tempfile
import types
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "smbv1_server"))

import smbserver_opl as smb  # noqa: E402


def _request(params=b"", data=b"", msg=b""):
    return types.SimpleNamespace(tid=1, params=params, data=data, msg=msg)


def _open(conn, name):
    enc = name.encode("ascii")
    params = bytearray(48)
    struct.pack_into("<H", params, 5, len(enc))
    struct.pack_into("<I", params, 35, smb.FILE_OPEN)
    p, _, status = smb.h_nt_create_andx(conn, _request(bytes(params), b"\x00" + enc))[:3]
    assert status == smb.STATUS_SUCCESS, hex(status)
    return struct.unpack_from("<H", p, 5)[0]


def _read(conn, fid, offset, count):
    params = struct.pack("<BBHHIHHIHI", 0xFF, 0, 0, fid, offset, count, 0, 0, 0, 0)
    p, data, status = smb.h_read_andx(conn, _request(params))[:3]
    assert status == smb.STATUS_SUCCESS, hex(status)
    return data[:struct.unpack_from("<H", p, 10)[0]]


def _write(conn, fid, offset, payload):
    data_off = 64
    params = bytearray(28)
    struct.pack_into("<H", params, 4, fid)
    struct.pack_into("<I", params, 6, offset)
    struct.pack_into("<H", params, 20, len(payload))
    struct.pack_into("<H", params, 22, data_off)
    status = smb.h_write_andx(conn, _request(bytes(params), msg=b"\x00" * data_off + payload))[2]
    assert status == smb.STATUS_SUCCESS, hex(status)


class ReadAfterWrite(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        with open(os.path.join(self.tmp.name, "VMC.BIN"), "wb") as f:
            f.write(b"\x11" * 65536)
        share = smb.Share("games", self.tmp.name)
        self.server = smb.SmbServer({"games": share}, read_only=False)
        self.conns = []
        for _ in range(2):
            conn = smb.Conn(self.server)
            conn.trees[1] = share
            self.conns.append(conn)

    def tearDown(self):
        for conn in self.conns:
            conn.cleanup()
        self.tmp.cleanup()

    def test_same_connection_sees_its_write(self):
        conn = self.conns[0]
        fid = _open(conn, "VMC.BIN")
        self.assertEqual(_read(conn, fid, 0, 1024), b"\x11" * 1024)  # warms any buffer
        _write(conn, fid, 2048, b"\x22" * 512)
        self.assertEqual(_read(conn, fid, 2048, 512), b"\x22" * 512)

    def test_other_connection_sees_the_write(self):
        a, b = self.conns
        fa, fb = _open(a, "VMC.BIN"), _open(b, "VMC.BIN")
        self.assertEqual(_read(b, fb, 0, 1024), b"\x11" * 1024)
        _write(a, fa, 512, b"\x33" * 256)
        self.assertEqual(_read(b, fb, 512, 256), b"\x33" * 256)

    def test_full_size_reads_unchanged(self):
        conn = self.conns[0]
        fid = _open(conn, "VMC.BIN")
        self.assertEqual(len(_read(conn, fid, 0, 61440)), 61440)
        self.assertEqual(len(_read(conn, fid, 61440, 61440)), 65536 - 61440)
        self.assertEqual(_read(conn, fid, 65536, 4096), b"")


if __name__ == "__main__":
    unittest.main()
