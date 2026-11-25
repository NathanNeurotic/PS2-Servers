"""SMBv1 DELETE (0x06), DELETE_DIRECTORY (0x01) and RENAME (0x07).

Launchstation's file manager (smbman smb_Delete) removed ember.elf before writing
a fresh copy and the server answered "unsupported command=0x06". These drive the
handlers the way smbman builds the requests (BufferFormat 0x04 + ASCII z-string).
"""

import importlib.util
import os
import sys
import tempfile
import types
import unittest


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SERVER = os.path.join(_ROOT, "smbv1_server", "smbserver_opl.py")


def _load_server():
    spec = importlib.util.spec_from_file_location("smbv1_delete_ref", _SERVER)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _names(*names):
    return b"".join(b"\x04" + n.encode("ascii") + b"\x00" for n in names)


def _request(data, params=b"\x00\x00"):
    return types.SimpleNamespace(tid=1, params=params, data=data, msg=b"")


class SMBv1DeleteRename(unittest.TestCase):
    def setUp(self):
        self.smb = _load_server()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        os.makedirs(os.path.join(self.root, "EMBER", "games"))
        with open(os.path.join(self.root, "EMBER", "ember.elf"), "wb") as f:
            f.write(b"\x7fELF")
        self.share = self.smb.Share("ps2", self.root)
        self.server = self.smb.SmbServer({"ps2": self.share}, read_only=False)
        self.conn = self.smb.Conn(self.server)
        self.conn.trees[1] = self.share

    def tearDown(self):
        self.conn.cleanup()
        self.tmp.cleanup()

    def path(self, *parts):
        return os.path.join(self.root, *parts)

    def test_commands_are_registered(self):
        self.assertIs(self.smb.HANDLERS[0x06], self.smb.h_delete)
        self.assertIs(self.smb.HANDLERS[0x01], self.smb.h_delete_directory)
        self.assertIs(self.smb.HANDLERS[0x07], self.smb.h_rename)

    def test_delete_file(self):
        _, _, status = self.smb.h_delete(self.conn, _request(_names("\\EMBER\\ember.elf")))
        self.assertEqual(status, self.smb.STATUS_SUCCESS)
        self.assertFalse(os.path.exists(self.path("EMBER", "ember.elf")))

    def test_delete_missing_file(self):
        _, _, status = self.smb.h_delete(self.conn, _request(_names("\\EMBER\\none.elf")))
        self.assertEqual(status, self.smb.STATUS_OBJECT_NAME_NOT_FOUND)

    def test_delete_refuses_a_folder(self):
        _, _, status = self.smb.h_delete(self.conn, _request(_names("\\EMBER\\games")))
        self.assertEqual(status, self.smb.STATUS_ACCESS_DENIED)
        self.assertTrue(os.path.isdir(self.path("EMBER", "games")))

    def test_delete_directory_empty_only(self):
        _, _, status = self.smb.h_delete_directory(self.conn, _request(_names("\\EMBER")))
        self.assertEqual(status, self.smb.STATUS_ACCESS_DENIED)
        self.assertTrue(os.path.isdir(self.path("EMBER")))
        _, _, status = self.smb.h_delete_directory(self.conn, _request(_names("\\EMBER\\games")))
        self.assertEqual(status, self.smb.STATUS_SUCCESS)
        self.assertFalse(os.path.exists(self.path("EMBER", "games")))

    def test_share_root_is_never_removed(self):
        # Empty root: os.rmdir would succeed, so only the root guard refuses it.
        os.remove(self.path("EMBER", "ember.elf"))
        os.rmdir(self.path("EMBER", "games"))
        os.rmdir(self.path("EMBER"))
        _, _, status = self.smb.h_delete_directory(self.conn, _request(_names("\\")))
        self.assertEqual(status, self.smb.STATUS_ACCESS_DENIED)
        self.assertTrue(os.path.isdir(self.root))

    def test_rename_and_no_overwrite(self):
        _, _, status = self.smb.h_rename(self.conn, _request(_names("\\EMBER\\ember.elf", "\\EMBER\\old.elf")))
        self.assertEqual(status, self.smb.STATUS_SUCCESS)
        self.assertTrue(os.path.isfile(self.path("EMBER", "old.elf")))
        self.assertFalse(os.path.exists(self.path("EMBER", "ember.elf")))
        with open(self.path("EMBER", "ember.elf"), "wb") as f:
            f.write(b"new")
        _, _, status = self.smb.h_rename(self.conn, _request(_names("\\EMBER\\old.elf", "\\EMBER\\ember.elf")))
        self.assertEqual(status, self.smb.STATUS_OBJECT_NAME_COLLISION)
        with open(self.path("EMBER", "ember.elf"), "rb") as f:
            self.assertEqual(f.read(), b"new")

    def test_open_file_is_not_deleted_or_renamed(self):
        local = self.path("EMBER", "ember.elf")
        self.conn.files[0x1000] = self.smb.OpenFile(local, is_dir=False)
        for handler, data in ((self.smb.h_delete, _names("\\EMBER\\ember.elf")),
                              (self.smb.h_rename, _names("\\EMBER\\ember.elf", "\\EMBER\\x.elf")),
                              (self.smb.h_rename, _names("\\EMBER", "\\EMBER2"))):
            _, _, status = handler(self.conn, _request(data))
            self.assertEqual(status, self.smb.STATUS_SHARING_VIOLATION)
        self.assertTrue(os.path.isfile(local))
        self.conn.files.pop(0x1000).close()
        _, _, status = self.smb.h_delete(self.conn, _request(_names("\\EMBER\\ember.elf")))
        self.assertEqual(status, self.smb.STATUS_SUCCESS)

    def test_read_only_refuses_all(self):
        self.server.read_only = True
        for handler, data in ((self.smb.h_delete, _names("\\EMBER\\ember.elf")),
                              (self.smb.h_delete_directory, _names("\\EMBER\\games")),
                              (self.smb.h_rename, _names("\\EMBER\\ember.elf", "\\EMBER\\x.elf"))):
            _, _, status = handler(self.conn, _request(data))
            self.assertEqual(status, self.smb.STATUS_ACCESS_DENIED)
        self.assertTrue(os.path.isfile(self.path("EMBER", "ember.elf")))
        self.assertTrue(os.path.isdir(self.path("EMBER", "games")))

    def test_escape_is_refused(self):
        outside = tempfile.NamedTemporaryFile(delete=False)
        outside.close()
        try:
            rel = os.path.relpath(outside.name, self.root).replace(os.sep, "\\")
            _, _, status = self.smb.h_delete(self.conn, _request(_names("\\" + rel)))
            self.assertNotEqual(status, self.smb.STATUS_SUCCESS)
            self.assertTrue(os.path.exists(outside.name))
        finally:
            os.unlink(outside.name)


if __name__ == "__main__":
    unittest.main()
