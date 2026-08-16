"""Regression coverage for writable SMBv1 operations used by OPL/RiptOPL.

Issue #180 exposed a gap that read/boot tests could not see: the server could
modify an existing file, but it could not create OPL's folders or create and
truncate per-game CFG files on a fresh share.  These tests drive the same SMB1
handlers with the create dispositions OPL can issue and verify the filesystem,
not merely the reply header.
"""

import importlib.util
import os
import struct
import sys
import tempfile
import types
import unittest


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SERVER = os.path.join(_ROOT, "smbv1_server", "smbserver_opl.py")


def _load_server():
    spec = importlib.util.spec_from_file_location("smbv1_write_ref", _SERVER)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _request(*, tid=1, params=b"", data=b"", msg=b""):
    return types.SimpleNamespace(tid=tid, params=params, data=data, msg=msg)


def _ntcreate_request(server, name, disposition, create_options=0):
    # SMB_COM_NT_CREATE_ANDX request parameters are 48 bytes.  The fields the
    # server needs are NameLength at byte 5, CreateDisposition at byte 35 and
    # CreateOptions at byte 39.  Unicode is not negotiated, so the pathname is
    # single-byte and OPL may precede it with one alignment byte.
    encoded = name.encode("ascii")
    params = bytearray(48)
    struct.pack_into("<H", params, 5, len(encoded))
    struct.pack_into("<I", params, 35, disposition)
    struct.pack_into("<I", params, 39, create_options)
    return _request(params=bytes(params), data=b"\x00" + encoded)


def _write_request(fid, payload, offset=0):
    params = bytearray(28)
    struct.pack_into("<H", params, 4, fid)
    struct.pack_into("<I", params, 6, offset & 0xFFFFFFFF)
    struct.pack_into("<H", params, 18, (len(payload) >> 16) & 0xFFFF)
    struct.pack_into("<H", params, 20, len(payload) & 0xFFFF)
    data_offset = 64
    struct.pack_into("<H", params, 22, data_offset)
    struct.pack_into("<I", params, 24, (offset >> 32) & 0xFFFFFFFF)
    return _request(params=bytes(params), msg=(b"\x00" * data_offset) + payload)


class SMBv1WriteSemantics(unittest.TestCase):
    def setUp(self):
        self.smb = _load_server()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        share = self.smb.Share("games", self.root)
        server = self.smb.SmbServer({"games": share}, read_only=False)
        self.conn = self.smb.Conn(server)
        self.conn.trees[1] = share

    def tearDown(self):
        self.conn.cleanup()
        self.tmp.cleanup()

    def test_create_directory_on_fresh_share(self):
        req = _request(data=b"\x04CFG\x00")
        params, data, status = self.smb.h_create_directory(self.conn, req)
        self.assertEqual(status, self.smb.STATUS_SUCCESS)
        self.assertEqual(params, b"")
        self.assertEqual(data, b"")
        self.assertTrue(os.path.isdir(os.path.join(self.root, "CFG")))

    def test_open_if_creates_new_cfg_then_write_commits(self):
        os.mkdir(os.path.join(self.root, "CFG"))
        req = _ntcreate_request(self.smb, r"CFG\SLUS_000.00.cfg", self.smb.FILE_OPEN_IF)
        params, _data, status = self.smb.h_nt_create_andx(self.conn, req)
        self.assertEqual(status, self.smb.STATUS_SUCCESS)
        fid = struct.unpack_from("<H", params, 5)[0]
        action = struct.unpack_from("<I", params, 7)[0]
        self.assertEqual(action, self.smb.FILE_CREATED)

        payload = b"compat=3\r\n"
        _params, _data, status = self.smb.h_write_andx(
            self.conn, _write_request(fid, payload)
        )
        self.assertEqual(status, self.smb.STATUS_SUCCESS)
        self.smb.h_close(self.conn, _request(params=struct.pack("<H", fid)))

        with open(os.path.join(self.root, "CFG", "SLUS_000.00.cfg"), "rb") as handle:
            self.assertEqual(handle.read(), payload)

    def test_overwrite_if_truncates_existing_cfg_before_shorter_write(self):
        cfg_dir = os.path.join(self.root, "CFG")
        os.mkdir(cfg_dir)
        target = os.path.join(cfg_dir, "SLUS_000.00.cfg")
        with open(target, "wb") as handle:
            handle.write(b"compat=7\r\nvmc=old-long-value\r\n")

        req = _ntcreate_request(self.smb, r"CFG\SLUS_000.00.cfg", self.smb.FILE_OVERWRITE_IF)
        params, _data, status = self.smb.h_nt_create_andx(self.conn, req)
        self.assertEqual(status, self.smb.STATUS_SUCCESS)
        fid = struct.unpack_from("<H", params, 5)[0]
        action = struct.unpack_from("<I", params, 7)[0]
        self.assertEqual(action, self.smb.FILE_OVERWRITTEN)
        self.assertEqual(os.path.getsize(target), 0, "overwrite must truncate before WRITE_ANDX")

        payload = b"compat=1\r\n"
        _params, _data, status = self.smb.h_write_andx(
            self.conn, _write_request(fid, payload)
        )
        self.assertEqual(status, self.smb.STATUS_SUCCESS)
        self.smb.h_close(self.conn, _request(params=struct.pack("<H", fid)))

        with open(target, "rb") as handle:
            self.assertEqual(handle.read(), payload, "old tail bytes must not survive O_TRUNC")

    def test_open_if_existing_file_does_not_truncate(self):
        cfg_dir = os.path.join(self.root, "CFG")
        os.mkdir(cfg_dir)
        target = os.path.join(cfg_dir, "SLUS_000.00.cfg")
        original = b"keep-me"
        with open(target, "wb") as handle:
            handle.write(original)

        req = _ntcreate_request(self.smb, r"CFG\SLUS_000.00.cfg", self.smb.FILE_OPEN_IF)
        params, _data, status = self.smb.h_nt_create_andx(self.conn, req)
        self.assertEqual(status, self.smb.STATUS_SUCCESS)
        action = struct.unpack_from("<I", params, 7)[0]
        self.assertEqual(action, self.smb.FILE_OPENED)
        with open(target, "rb") as handle:
            self.assertEqual(handle.read(), original)

    def test_read_only_refuses_directory_create_and_new_file(self):
        share = self.smb.Share("games", self.root)
        ro = self.smb.Conn(self.smb.SmbServer({"games": share}, read_only=True))
        ro.trees[1] = share
        try:
            _params, _data, status = self.smb.h_create_directory(
                ro, _request(data=b"\x04CFG\x00")
            )
            self.assertEqual(status, self.smb.STATUS_ACCESS_DENIED)
            self.assertFalse(os.path.exists(os.path.join(self.root, "CFG")))

            _params, _data, status = self.smb.h_nt_create_andx(
                ro,
                _ntcreate_request(self.smb, "new.cfg", self.smb.FILE_OPEN_IF),
            )
            self.assertEqual(status, self.smb.STATUS_ACCESS_DENIED)
            self.assertFalse(os.path.exists(os.path.join(self.root, "new.cfg")))
        finally:
            ro.cleanup()


if __name__ == "__main__":
    unittest.main()
