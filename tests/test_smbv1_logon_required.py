"""With --user set, the SMBv1 server must not hand out files without a login.

Before this test existed, SESSION_SETUP refused a wrong password but nothing
after it checked: a client could skip the login, or ignore its failure, and
send TREE_CONNECT, OPEN and READ anyway. These tests drive serve_conn over a
socket pair, the same dispatch path a console uses.
"""

import os
import socket
import struct
import sys
import tempfile
import threading
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "smbv1_server"))

import smbserver_opl as smb  # noqa: E402


class _Client:
    def __init__(self, sock):
        self.s = sock
        self.uid = self.tid = 0
        self.mid = 1

    def _recv(self, n):
        b = b""
        while len(b) < n:
            chunk = self.s.recv(n - len(b))
            if not chunk:
                raise ConnectionError("server closed")
            b += chunk
        return b

    def tx(self, cmd, params=b"", data=b""):
        h = bytearray(32)
        h[0:4] = smb.SMB_MAGIC
        h[4] = cmd
        struct.pack_into("<HHHH", h, 24, self.tid, 1, self.uid, self.mid)
        self.mid += 1
        msg = bytes(h) + smb.build_body(params, data)
        self.s.sendall(b"\x00" + len(msg).to_bytes(3, "big") + msg)
        length = int.from_bytes(self._recv(4)[1:4], "big")
        body = self._recv(length)
        status = body[5] | (struct.unpack_from("<H", body, 7)[0] << 16)
        return status, body

    def negotiate(self):
        return self.tx(smb.SMB_COM_NEGOTIATE, data=b"\x02NT LM 0.12\x00")[0]

    def session_setup(self, user, password):
        pw = password.encode("ascii") + b"\x00"
        params = struct.pack("<BBHHHHIHHII", 0xFF, 0, 0, 0xFFFF, 2, 0, 0, len(pw), 0, 0, 0)
        status, body = self.tx(smb.SMB_COM_SESSION_SETUP_ANDX, params,
                               pw + user.encode("ascii") + b"\x00WORKGROUP\x00")
        if status == smb.STATUS_SUCCESS:
            self.uid = struct.unpack_from("<H", body, 28)[0]
        return status

    def tree_connect(self, share):
        params = struct.pack("<BBHHH", 0xFF, 0, 0, 0, 1)
        status, body = self.tx(smb.SMB_COM_TREE_CONNECT_ANDX, params,
                               b"\x00\\\\PC\\" + share.encode("ascii") + b"\x00?????\x00")
        if status == smb.STATUS_SUCCESS:
            self.tid = struct.unpack_from("<H", body, 24)[0]
        return status

    def open(self, name):
        enc = name.encode("ascii")
        params = bytearray(48)
        params[0] = 0xFF
        struct.pack_into("<H", params, 5, len(enc))
        struct.pack_into("<I", params, 35, smb.FILE_OPEN)
        return self.tx(smb.SMB_COM_NT_CREATE_ANDX, bytes(params), b"\x00" + enc + b"\x00")[0]

    def logoff(self):
        return self.tx(smb.SMB_COM_LOGOFF_ANDX, struct.pack("<BBH", 0xFF, 0, 0))[0]


class LogonRequired(unittest.TestCase):
    def _serve(self, users):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with open(os.path.join(tmp.name, "SECRET.BIN"), "wb") as f:
            f.write(b"\x5a" * 64)
        server = smb.SmbServer({"games": smb.Share("games", tmp.name)},
                               read_only=False, users=users)
        ours, theirs = socket.socketpair()
        t = threading.Thread(target=smb.serve_conn, args=(server, theirs, ("127.0.0.1", 1)),
                             daemon=True)
        t.start()
        self.addCleanup(t.join, 5)
        self.addCleanup(ours.close)
        ours.settimeout(5)
        client = _Client(ours)
        self.assertEqual(client.negotiate(), smb.STATUS_SUCCESS)
        return client

    def test_tree_connect_without_session_setup_is_refused(self):
        c = self._serve({"alice": "secret"})
        self.assertEqual(c.tree_connect("games"), smb.STATUS_ACCESS_DENIED)
        c.tid = 1  # a guessed tree id must not work either
        self.assertEqual(c.open("\\SECRET.BIN"), smb.STATUS_ACCESS_DENIED)

    def test_failed_login_does_not_open_the_share(self):
        c = self._serve({"alice": "secret"})
        self.assertEqual(c.session_setup("alice", "wrong"), smb.STATUS_LOGON_FAILURE)
        self.assertEqual(c.tree_connect("games"), smb.STATUS_ACCESS_DENIED)

    def test_good_login_opens_the_share(self):
        c = self._serve({"alice": "secret"})
        self.assertEqual(c.session_setup("alice", "secret"), smb.STATUS_SUCCESS)
        self.assertEqual(c.tree_connect("games"), smb.STATUS_SUCCESS)
        self.assertEqual(c.open("\\SECRET.BIN"), smb.STATUS_SUCCESS)

    def test_logoff_ends_access(self):
        c = self._serve({"alice": "secret"})
        self.assertEqual(c.session_setup("alice", "secret"), smb.STATUS_SUCCESS)
        self.assertEqual(c.tree_connect("games"), smb.STATUS_SUCCESS)
        self.assertEqual(c.logoff(), smb.STATUS_SUCCESS)
        self.assertEqual(c.open("\\SECRET.BIN"), smb.STATUS_ACCESS_DENIED)

    def test_guest_mode_is_unchanged(self):
        # No --user: the console path. No SESSION_SETUP is needed, as before.
        c = self._serve({})
        self.assertEqual(c.tree_connect("games"), smb.STATUS_SUCCESS)
        self.assertEqual(c.open("\\SECRET.BIN"), smb.STATUS_SUCCESS)


if __name__ == "__main__":
    unittest.main()
