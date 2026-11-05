"""The SMB2/SMB3 server driven by an independent client: smbprotocol.

tests/test_smb2_server.py uses a client written in this repository, so a
structure that is wrong in the same way on both sides still passes. The
conformance runs cover the PS2 clients (libsmb2, usmb2), but those do not
sign. This module uses jborean93's smbprotocol -- a separate implementation
of NTLM, SPNEGO, SMB2 signing (HMAC-SHA256) and SMB3 signing (AES-CMAC) -- the
way a Windows client behaves: signing required, secure dialect negotiation
(FSCTL_VALIDATE_NEGOTIATE_INFO) on the tree connect, dialect pinned so that
each of 2.1, 3.0 and 3.0.2 is exercised rather than whichever the client
prefers.

Skips when smbprotocol is not installed; CI installs it, see
.github/workflows/ci.yml.

Run:  python -m unittest tests.test_smb2_independent_client -v
"""

import hashlib
import os
import shutil
import sys
import tempfile
import threading
import unittest
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "smb2_server"))

import smb2_wire as wire                # noqa: E402
from smb2_auth import Authenticator     # noqa: E402
from smb2_paths import Share            # noqa: E402
from smb2_server import Smb2Server      # noqa: E402

try:
    from smbprotocol.connection import Connection, Dialects
    from smbprotocol.exceptions import SMBResponseException
    from smbprotocol.file_info import FileInformationClass
    from smbprotocol.open import (
        CreateDisposition, CreateOptions, DirectoryAccessMask, FileAttributes,
        FilePipePrinterAccessMask, ImpersonationLevel, Open, ShareAccess)
    from smbprotocol.session import Session
    from smbprotocol.tree import TreeConnect
    HAVE_SMBPROTOCOL = True
except ImportError:
    HAVE_SMBPROTOCOL = False

USER, PASSWORD = "ps2", "correct horse battery"
GAME_SIZE = 3 * 1024 * 1024 + 123      # more than one MAX_READ, not a multiple


@unittest.skipUnless(HAVE_SMBPROTOCOL, "smbprotocol is not installed")
class IndependentClientFixture(unittest.TestCase):
    read_only = False

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ps2smb2-ind-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.game = bytes((i * 31 + (i >> 8)) & 0xFF for i in range(GAME_SIZE))
        with open(os.path.join(self.dir, "game.iso"), "wb") as f:
            f.write(self.game)
        os.mkdir(os.path.join(self.dir, "DVD"))
        with open(os.path.join(self.dir, "DVD", "Readme.txt"), "wb") as f:
            f.write(b"hi\n")

        share = Share("games", self.dir, read_only=self.read_only)
        self.server = Smb2Server({"games": share}, Authenticator({USER: PASSWORD}),
                                 ceiling=wire.CEILING_SMB3, read_only=self.read_only)
        self.port = self.server.listen("127.0.0.1", 0)
        self.addCleanup(self.server.stop)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def connect(self, dialect, require_signing=True, secure_negotiate=True):
        conn = Connection(uuid.uuid4(), "127.0.0.1", self.port,
                          require_signing=require_signing)
        conn.connect(dialect, timeout=10)
        self.addCleanup(conn.disconnect)
        sess = Session(conn, USER, PASSWORD, require_encryption=False,
                       auth_protocol="ntlm")
        sess.connect()
        self.addCleanup(sess.disconnect)
        tree = TreeConnect(sess, r"\\127.0.0.1\games")
        tree.connect(require_secure_negotiate=secure_negotiate)
        self.addCleanup(tree.disconnect)
        return conn, sess, tree

    @staticmethod
    def listdir(tree, path):
        d = Open(tree, path)
        d.create(ImpersonationLevel.Impersonation, DirectoryAccessMask.FILE_LIST_DIRECTORY,
                 FileAttributes.FILE_ATTRIBUTE_DIRECTORY, ShareAccess.FILE_SHARE_READ,
                 CreateDisposition.FILE_OPEN, CreateOptions.FILE_DIRECTORY_FILE)
        try:
            return sorted(
                e["file_name"].get_value().decode("utf-16-le")
                for e in d.query_directory(
                    "*", FileInformationClass.FILE_ID_BOTH_DIRECTORY_INFORMATION))
        finally:
            d.close()

    @staticmethod
    def read_all(conn, tree, path):
        f = Open(tree, path)
        f.create(ImpersonationLevel.Impersonation, FilePipePrinterAccessMask.GENERIC_READ,
                 FileAttributes.FILE_ATTRIBUTE_NORMAL, ShareAccess.FILE_SHARE_READ,
                 CreateDisposition.FILE_OPEN, CreateOptions.FILE_NON_DIRECTORY_FILE)
        try:
            size = f.end_of_file
            chunk = min(conn.max_read_size, 1 << 20)
            out = bytearray()
            while len(out) < size:
                out += f.read(len(out), min(chunk, size - len(out)))
            return bytes(out)
        finally:
            f.close()

    @staticmethod
    def write_file(tree, path, data):
        w = Open(tree, path)
        w.create(ImpersonationLevel.Impersonation,
                 FilePipePrinterAccessMask.GENERIC_WRITE | FilePipePrinterAccessMask.DELETE,
                 FileAttributes.FILE_ATTRIBUTE_NORMAL, ShareAccess.FILE_SHARE_READ,
                 CreateDisposition.FILE_OVERWRITE_IF, CreateOptions.FILE_NON_DIRECTORY_FILE)
        try:
            w.write(data, 0)
        finally:
            w.close()

    @staticmethod
    def delete_file(tree, path):
        r = Open(tree, path)
        r.create(ImpersonationLevel.Impersonation, FilePipePrinterAccessMask.DELETE,
                 FileAttributes.FILE_ATTRIBUTE_NORMAL, ShareAccess.FILE_SHARE_READ,
                 CreateDisposition.FILE_OPEN,
                 CreateOptions.FILE_NON_DIRECTORY_FILE | CreateOptions.FILE_DELETE_ON_CLOSE)
        r.close()


class SignedDialects(IndependentClientFixture):
    """Signing required, secure negotiate on: the Windows 11 client posture."""

    def exercise(self, requested, expected_dialect):
        conn, sess, tree = self.connect(requested)
        self.assertEqual(conn.dialect, expected_dialect)
        self.assertTrue(conn.require_signing)
        self.assertFalse(sess.encrypt_data)
        self.assertEqual(self.listdir(tree, ""), [".", "..", "DVD", "game.iso"])
        self.assertEqual(self.listdir(tree, "DVD"), [".", "..", "Readme.txt"])
        got = self.read_all(conn, tree, "game.iso")
        self.assertEqual(len(got), GAME_SIZE)
        self.assertEqual(hashlib.sha256(got).hexdigest(),
                         hashlib.sha256(self.game).hexdigest())
        name = "written-%04x.txt" % expected_dialect
        self.write_file(tree, name, b"hello from smbprotocol\n")
        with open(os.path.join(self.dir, name), "rb") as f:
            self.assertEqual(f.read(), b"hello from smbprotocol\n")
        self.delete_file(tree, name)
        self.assertFalse(os.path.exists(os.path.join(self.dir, name)))

    def test_smb_2_1(self):
        self.exercise(Dialects.SMB_2_1_0, wire.DIALECT_210)

    def test_smb_3_0(self):
        self.exercise(Dialects.SMB_3_0_0, wire.DIALECT_300)

    def test_smb_3_0_2(self):
        self.exercise(Dialects.SMB_3_0_2, wire.DIALECT_302)

    def test_client_choice_is_3_0_2(self):
        # Offered everything (including 3.1.1), the server must pick 3.0.2 and
        # the client must accept that without complaint.
        conn, _, _ = self.connect(None)
        self.assertEqual(conn.dialect, wire.DIALECT_302)

    def test_unsigned_client_still_served(self):
        # The PS2 loaders do not sign. A client that neither requires signing
        # nor validates the negotiate must keep working alongside the signers.
        conn, _, tree = self.connect(Dialects.SMB_3_0_2, require_signing=False,
                                     secure_negotiate=False)
        self.assertEqual(self.read_all(conn, tree, "DVD/Readme.txt"), b"hi\n")


class ReadOnlyShare(IndependentClientFixture):
    read_only = True

    def test_write_refused_read_still_signed(self):
        conn, _, tree = self.connect(Dialects.SMB_3_0_2)
        with self.assertRaises(SMBResponseException) as cm:
            self.write_file(tree, "nope.txt", b"x")
        self.assertEqual(cm.exception.status, wire.STATUS_MEDIA_WRITE_PROTECTED)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "nope.txt")))
        self.assertEqual(self.read_all(conn, tree, "DVD/Readme.txt"), b"hi\n")


if __name__ == "__main__":
    unittest.main()
