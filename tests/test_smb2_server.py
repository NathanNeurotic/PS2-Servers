"""End-to-end tests for the SMB2/SMB3 server, over real sockets.

These drive a small SMB2 client -- written here rather than imported, so it
cannot share a misunderstanding with the server -- through the exchange a file
manager actually performs: negotiate, NTLMSSP session setup, tree connect, list
a directory, read a file back byte for byte, write one, and delete it.

What this cannot prove, stated plainly: both sides are this project's reading of
the protocol, so a structure that is wrong in the same way twice still passes.
The layouts are pinned against MS-SMB2's stated StructureSize values in
test_smb2_wire.py for that reason, and the only complete answer is a real client
-- which needs port 445, so it cannot run here.

Run:  python -m unittest tests.test_smb2_server -v
"""

import os
import socket
import struct
import sys
import tempfile
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "smb2_server"))

import smb2_spnego as spnego            # noqa: E402
import smb2_wire as wire                # noqa: E402
from smb2_auth import (                 # noqa: E402
    Authenticator, ntlmv2_proof, ntowfv2)
from smb2_paths import Share            # noqa: E402
from smb2_server import Smb2Server      # noqa: E402

USER, PASSWORD = "ripto", "correct horse battery"


class Client:
    """The smallest SMB2 client that can exercise the server."""

    def __init__(self, port):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.message_id = 0
        self.session_id = 0
        self.tree_id = 0

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass

    def call(self, command, body, tree_id=None):
        hdr = wire.pack_header(command, 0, self.message_id,
                               tree_id=self.tree_id if tree_id is None else tree_id,
                               session_id=self.session_id)
        # The client's own header must not carry the server-to-redir flag.
        hdr = hdr[:16] + struct.pack("<I", 0) + hdr[20:]
        self.message_id += 1
        wire.send_msg(self.sock, hdr + body)
        reply = wire.recv_msg(self.sock)
        if reply is None:
            raise AssertionError("server closed the connection")
        head = wire.parse_header(reply)
        return head, reply[wire.HEADER_SIZE:]

    # -- the exchange ------------------------------------------------------ #
    def negotiate(self, dialects=(wire.DIALECT_202, wire.DIALECT_210,
                                  wire.DIALECT_300, wire.DIALECT_302)):
        # 36 bytes of fixed part before the dialect array: StructureSize,
        # DialectCount, SecurityMode, Reserved, Capabilities, ClientGuid,
        # ClientStartTime. Short by those last 8 and the server reads the
        # dialects out of nothing.
        body = struct.pack("<HHHHI16sQ", 36, len(dialects), 0, 0, 0,
                           b"\x00" * 16, 0)
        body += b"".join(struct.pack("<H", d) for d in dialects)
        head, reply = self.call(wire.CMD_NEGOTIATE, body)
        assert head.status == 0, "NEGOTIATE failed 0x%08X" % head.status
        return struct.unpack_from("<H", reply, 4)[0]

    def session_setup(self, user, password, anonymous=False):
        negotiate = (spnego.NTLMSSP_SIGNATURE + struct.pack("<I", 1)
                     + struct.pack("<I", spnego.CHALLENGE_FLAGS)
                     + struct.pack("<HHI", 0, 0, 32) + struct.pack("<HHI", 0, 0, 32))
        head, reply = self._session_blob(negotiate)
        assert head.status == wire.STATUS_MORE_PROCESSING_REQUIRED, (
            "expected a challenge, got 0x%08X" % head.status)
        self.session_id = head.session_id

        off, length = struct.unpack_from("<HH", reply, 4)
        blob = reply[off - wire.HEADER_SIZE:off - wire.HEADER_SIZE + length]
        challenge_msg = spnego.extract_ntlm(blob)
        challenge = challenge_msg[24:32]
        info_len, _max, info_at = struct.unpack_from("<HHI", challenge_msg, 40)
        target_info = challenge_msg[info_at:info_at + info_len]

        if anonymous:
            auth = self._authenticate(b"", "", "")
        else:
            # A real NTLMv2 blob: 0x0101, reserved, timestamp, client challenge,
            # then the server's own TargetInfo folded back in.
            blob_body = (struct.pack("<BBHIQ", 1, 1, 0, 0, 0)
                         + os.urandom(8) + struct.pack("<I", 0)
                         + target_info + struct.pack("<I", 0))
            ntowf = ntowfv2(password, user, "WORKGROUP")
            proof = ntlmv2_proof(ntowf, challenge, blob_body)
            auth = self._authenticate(proof + blob_body, user, "WORKGROUP")
        head, _ = self._session_blob(auth)
        return head.status

    def _authenticate(self, nt_response, user, domain):
        user_u = user.encode("utf-16-le")
        domain_u = domain.encode("utf-16-le")
        # Signature(8) MessageType(4) then six 8-byte field triples (48),
        # NegotiateFlags(4), Version(8) -- 72 before the payload. No MIC.
        fixed = 72
        lm_at = fixed
        nt_at = lm_at + 24
        dom_at = nt_at + len(nt_response)
        user_at = dom_at + len(domain_u)
        ws_at = user_at + len(user_u)
        return (spnego.NTLMSSP_SIGNATURE + struct.pack("<I", 3)
                + struct.pack("<HHI", 24, 24, lm_at)
                + struct.pack("<HHI", len(nt_response), len(nt_response), nt_at)
                + struct.pack("<HHI", len(domain_u), len(domain_u), dom_at)
                + struct.pack("<HHI", len(user_u), len(user_u), user_at)
                + struct.pack("<HHI", 0, 0, ws_at)
                + struct.pack("<HHI", 0, 0, ws_at)
                + struct.pack("<I", spnego.CHALLENGE_FLAGS)
                + b"\x00" * 8
                + b"\x00" * 24 + nt_response + domain_u + user_u)

    def _session_blob(self, token):
        body = struct.pack("<HBBIIHHQ", 25, 0, 1, 0, 0,
                           wire.HEADER_SIZE + 24, len(token), 0)
        return self.call(wire.CMD_SESSION_SETUP, body + token)

    def tree_connect(self, share):
        path = ("\\\\127.0.0.1\\" + share).encode("utf-16-le")
        body = struct.pack("<HHHH", 9, 0, wire.HEADER_SIZE + 8, len(path)) + path
        head, _ = self.call(wire.CMD_TREE_CONNECT, body, tree_id=0)
        if head.status == 0:
            self.tree_id = head.tree_id
        return head.status

    def create(self, name, disposition=wire.FILE_OPEN, options=0, directory=False):
        raw = name.encode("utf-16-le")
        if directory:
            options |= wire.FILE_DIRECTORY_FILE
        body = struct.pack("<HBBIQQIIIIIHHII", 57, 0, 0, 2, 0, 0,
                           0x00120089, 0, 7, disposition, options,
                           wire.HEADER_SIZE + 56, len(raw), 0, 0) + raw
        head, reply = self.call(wire.CMD_CREATE, body)
        if head.status != 0:
            return head.status, None
        return 0, reply[64:80]

    def close_handle(self, file_id):
        body = struct.pack("<HHI", 24, 0, 0) + file_id
        head, _ = self.call(wire.CMD_CLOSE, body)
        return head.status

    def query_directory_raw(self, file_id, pattern="*",
                            info_class=wire.FILE_BOTH_DIRECTORY_INFORMATION):
        """Every response buffer, across as many round trips as it takes."""
        buffers = []
        flags = 0x01                     # RESTART_SCANS on the first call
        while True:
            raw = pattern.encode("utf-16-le")
            body = (struct.pack("<HBBI", 33, info_class, flags, 0) + file_id
                    + struct.pack("<HHI", wire.HEADER_SIZE + 32, len(raw), 65536)
                    + raw)
            head, reply = self.call(wire.CMD_QUERY_DIRECTORY, body)
            if head.status == wire.STATUS_NO_MORE_FILES:
                return buffers
            if head.status != 0:
                raise AssertionError("QUERY_DIRECTORY 0x%08X" % head.status)
            off, length = struct.unpack_from("<HI", reply, 2)
            buffers.append(reply[off - wire.HEADER_SIZE:
                                 off - wire.HEADER_SIZE + length])
            flags = 0

    def query_directory(self, file_id, pattern="*"):
        """The names in a directory, parsed as FileBothDirectoryInformation.

        Only that class is parsed here: the name sits at a different offset in
        every class, and a parser that guessed would report the wrong bytes as
        a filename rather than failing.
        """
        names = []
        for blob in self.query_directory_raw(file_id, pattern):
            names.extend(_parse_both_dir(blob))
        return names

    def read(self, file_id, offset, length):
        body = struct.pack("<HBBIQ", 49, 0, 0, length, offset) + file_id
        body += struct.pack("<IIIHHB", 0, 0, 0, 0, 0, 0)
        head, reply = self.call(wire.CMD_READ, body)
        if head.status != 0:
            return head.status, b""
        data_off, _res, data_len = struct.unpack_from("<BBI", reply, 2)
        start = data_off - wire.HEADER_SIZE
        return 0, reply[start:start + data_len]

    def write(self, file_id, offset, data):
        body = struct.pack("<HHIQ", 49, wire.HEADER_SIZE + 48, len(data), offset)
        body += file_id + struct.pack("<IIHHI", 0, 0, 0, 0, 0) + data
        head, reply = self.call(wire.CMD_WRITE, body)
        if head.status != 0:
            return head.status, 0
        return 0, struct.unpack_from("<I", reply, 4)[0]

    def query_info(self, file_id, info_type, info_class):
        body = (struct.pack("<HBBIHHIII", 41, info_type, info_class, 65536,
                            0, 0, 0, 0, 0) + file_id)
        head, reply = self.call(wire.CMD_QUERY_INFO, body)
        if head.status != 0:
            return head.status, b""
        off, length = struct.unpack_from("<HI", reply, 2)
        start = off - wire.HEADER_SIZE
        return 0, reply[start:start + length]

    def echo(self):
        head, _ = self.call(wire.CMD_ECHO, struct.pack("<HH", 4, 0))
        return head.status


def _parse_both_dir(blob):
    names, at = [], 0
    while at < len(blob):
        next_off, = struct.unpack_from("<I", blob, at)
        name_len, = struct.unpack_from("<I", blob, at + 60)
        name = blob[at + 94:at + 94 + name_len].decode("utf-16-le")
        names.append(name)
        if next_off == 0:
            break
        at += next_off
    return names


class ServerFixture(unittest.TestCase):
    read_only = False
    open_mode = False

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ps2smb2-")
        self.addCleanup(self._cleanup)
        with open(os.path.join(self.dir, "game.iso"), "wb") as f:
            f.write(bytes((i * 7) & 0xFF for i in range(4096)))
        os.mkdir(os.path.join(self.dir, "APPS"))
        with open(os.path.join(self.dir, "APPS", "boot.elf"), "wb") as f:
            f.write(b"ELF" * 100)

        auth = (Authenticator.open() if self.open_mode
                else Authenticator({USER: PASSWORD}))
        share = Share("games", self.dir, read_only=self.read_only)
        self.server = Smb2Server({"games": share}, auth,
                                 ceiling=wire.CEILING_SMB3,
                                 read_only=self.read_only)
        self.port = self.server.listen("127.0.0.1", 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def _cleanup(self):
        self.server.stop()
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def connected(self, user=USER, password=PASSWORD, anonymous=False):
        c = Client(self.port)
        self.addCleanup(c.close)
        c.negotiate()
        status = c.session_setup(user, password, anonymous=anonymous)
        self.assertEqual(status, 0, "session setup failed 0x%08X" % status)
        self.assertEqual(c.tree_connect("games"), 0)
        return c


class Negotiation(ServerFixture):
    def test_dialect_is_the_best_both_sides_speak(self):
        c = Client(self.port)
        self.addCleanup(c.close)
        self.assertEqual(c.negotiate(), wire.DIALECT_302)

    def test_a_client_capped_at_smb2_gets_smb2(self):
        c = Client(self.port)
        self.addCleanup(c.close)
        self.assertEqual(c.negotiate((wire.DIALECT_202, wire.DIALECT_210)),
                         wire.DIALECT_210)

    def test_311_is_not_offered_even_when_asked_for(self):
        """Claiming 3.1.1 without negotiate contexts makes Windows hang up.

        Answering 3.0.2 is the honest reply, and every SMB3 client speaks it.
        """
        c = Client(self.port)
        self.addCleanup(c.close)
        self.assertEqual(c.negotiate((wire.DIALECT_311, wire.DIALECT_302)),
                         wire.DIALECT_302)


class Authentication(ServerFixture):
    def test_the_right_password_gets_a_session(self):
        c = Client(self.port)
        self.addCleanup(c.close)
        c.negotiate()
        self.assertEqual(c.session_setup(USER, PASSWORD), 0)

    def test_the_wrong_password_does_not(self):
        c = Client(self.port)
        self.addCleanup(c.close)
        c.negotiate()
        self.assertEqual(c.session_setup(USER, "wrong"), wire.STATUS_LOGON_FAILURE)

    def test_an_unknown_user_does_not(self):
        c = Client(self.port)
        self.addCleanup(c.close)
        c.negotiate()
        self.assertEqual(c.session_setup("nobody", PASSWORD),
                         wire.STATUS_LOGON_FAILURE)

    def test_anonymous_is_refused_when_a_password_is_configured(self):
        """A null session must not be a way around the password.

        This is the failure that matters most: everything else here is a feature
        not working, and this one is the share being open when the operator
        believes it is not.
        """
        c = Client(self.port)
        self.addCleanup(c.close)
        c.negotiate()
        self.assertEqual(c.session_setup("", "", anonymous=True),
                         wire.STATUS_LOGON_FAILURE)

    def test_no_tree_connect_before_a_session(self):
        c = Client(self.port)
        self.addCleanup(c.close)
        c.negotiate()
        self.assertEqual(c.tree_connect("games"), wire.STATUS_USER_SESSION_DELETED)

    def test_a_share_that_does_not_exist_is_refused(self):
        c = Client(self.port)
        self.addCleanup(c.close)
        c.negotiate()
        c.session_setup(USER, PASSWORD)
        self.assertEqual(c.tree_connect("nope"), wire.STATUS_BAD_NETWORK_NAME)


class OpenMode(ServerFixture):
    open_mode = True

    def test_anonymous_is_allowed_only_where_the_operator_said_so(self):
        c = Client(self.port)
        self.addCleanup(c.close)
        c.negotiate()
        self.assertEqual(c.session_setup("", "", anonymous=True), 0)


class Browsing(ServerFixture):
    def test_the_share_lists(self):
        c = self.connected()
        status, fid = c.create("", options=wire.FILE_DIRECTORY_FILE)
        self.assertEqual(status, 0)
        names = c.query_directory(fid)
        self.assertIn("game.iso", names)
        self.assertIn("APPS", names)
        self.assertIn(".", names)

    def test_a_subdirectory_lists(self):
        c = self.connected()
        status, fid = c.create("APPS", options=wire.FILE_DIRECTORY_FILE)
        self.assertEqual(status, 0)
        self.assertIn("boot.elf", c.query_directory(fid))

    def test_listing_survives_more_entries_than_fit_in_one_reply(self):
        """The cursor has to carry across calls, or a big folder truncates."""
        for i in range(200):
            with open(os.path.join(self.dir, "game%03d.iso" % i), "wb") as f:
                f.write(b"x")
        c = self.connected()
        _status, fid = c.create("", options=wire.FILE_DIRECTORY_FILE)
        names = c.query_directory(fid)
        for i in range(200):
            self.assertIn("game%03d.iso" % i, names)

    def test_every_listing_class_a_client_may_ask_for(self):
        """Clients differ on which class they ask for; all of these are real."""
        c = self.connected()
        for info_class in (wire.FILE_DIRECTORY_INFORMATION,
                           wire.FILE_FULL_DIRECTORY_INFORMATION,
                           wire.FILE_BOTH_DIRECTORY_INFORMATION,
                           wire.FILE_ID_BOTH_DIRECTORY_INFORMATION,
                           wire.FILE_NAMES_INFORMATION):
            with self.subTest(info_class=info_class):
                _status, fid = c.create("", options=wire.FILE_DIRECTORY_FILE)
                buffers = c.query_directory_raw(fid, info_class=info_class)
                self.assertTrue(buffers, "no entries for class 0x%02x" % info_class)
                c.close_handle(fid)

    def test_an_unknown_listing_class_is_refused_not_guessed(self):
        c = self.connected()
        _status, fid = c.create("", options=wire.FILE_DIRECTORY_FILE)
        raw = "*".encode("utf-16-le")
        body = (struct.pack("<HBBI", 33, 0x7F, 0x01, 0) + fid
                + struct.pack("<HHI", wire.HEADER_SIZE + 32, len(raw), 65536) + raw)
        head, _ = c.call(wire.CMD_QUERY_DIRECTORY, body)
        self.assertEqual(head.status, wire.STATUS_INVALID_PARAMETER)


class ReadingAndWriting(ServerFixture):
    def test_a_file_reads_back_byte_for_byte(self):
        with open(os.path.join(self.dir, "game.iso"), "rb") as f:
            expected = f.read()
        c = self.connected()
        status, fid = c.create("game.iso")
        self.assertEqual(status, 0)
        got = b""
        while len(got) < len(expected):
            status, chunk = c.read(fid, len(got), 1024)
            if status == wire.STATUS_END_OF_FILE:
                break
            self.assertEqual(status, 0)
            got += chunk
        self.assertEqual(got, expected)

    def test_reading_past_the_end_says_so(self):
        c = self.connected()
        _status, fid = c.create("game.iso")
        status, _ = c.read(fid, 1 << 20, 512)
        self.assertEqual(status, wire.STATUS_END_OF_FILE)

    def test_a_write_lands_on_disk(self):
        c = self.connected()
        status, fid = c.create("new.bin", disposition=wire.FILE_OVERWRITE_IF)
        self.assertEqual(status, 0)
        payload = bytes(range(256)) * 4
        status, written = c.write(fid, 0, payload)
        self.assertEqual(status, 0)
        self.assertEqual(written, len(payload))
        c.close_handle(fid)
        with open(os.path.join(self.dir, "new.bin"), "rb") as f:
            self.assertEqual(f.read(), payload)

    def test_delete_on_close_removes_the_file(self):
        target = os.path.join(self.dir, "scratch.bin")
        with open(target, "wb") as f:
            f.write(b"temporary")
        c = self.connected()
        status, fid = c.create("scratch.bin", options=wire.FILE_DELETE_ON_CLOSE)
        self.assertEqual(status, 0)
        c.close_handle(fid)
        self.assertFalse(os.path.exists(target))

    def test_a_missing_file_is_not_found(self):
        c = self.connected()
        status, _ = c.create("nope.iso")
        self.assertEqual(status, wire.STATUS_OBJECT_NAME_NOT_FOUND)

    def test_the_share_boundary_holds_over_the_wire(self):
        """The path guard's rules have to survive being reached through SMB2.

        Tested here as well as in test_smb2_paths because a guard that is only
        called on some paths is not a guard.
        """
        c = self.connected()
        for escape in ("..\\secrets.txt", "APPS\\..\\..\\outside.txt",
                       "\\\\other-server\\share\\x"):
            with self.subTest(path=escape):
                status, _ = c.create(escape)
                self.assertNotEqual(status, 0, "%r was served" % escape)


class ReadOnlyShare(ServerFixture):
    read_only = True

    def test_reading_still_works(self):
        c = self.connected()
        status, fid = c.create("game.iso")
        self.assertEqual(status, 0)
        status, chunk = c.read(fid, 0, 16)
        self.assertEqual(status, 0)
        self.assertEqual(len(chunk), 16)

    def test_writing_is_refused(self):
        c = self.connected()
        status, fid = c.create("game.iso")
        self.assertEqual(status, 0)
        status, _ = c.write(fid, 0, b"nope")
        self.assertEqual(status, wire.STATUS_MEDIA_WRITE_PROTECTED)

    def test_creating_is_refused(self):
        c = self.connected()
        status, _ = c.create("new.bin", disposition=wire.FILE_CREATE)
        self.assertEqual(status, wire.STATUS_MEDIA_WRITE_PROTECTED)


class Metadata(ServerFixture):
    def test_a_client_can_size_a_file(self):
        c = self.connected()
        _status, fid = c.create("game.iso")
        status, blob = c.query_info(fid, wire.INFO_TYPE_FILE,
                                    wire.FILE_STANDARD_INFORMATION)
        self.assertEqual(status, 0)
        _alloc, eof = struct.unpack_from("<qq", blob, 0)
        self.assertEqual(eof, 4096)

    def test_a_client_can_size_the_volume(self):
        c = self.connected()
        _status, fid = c.create("", options=wire.FILE_DIRECTORY_FILE)
        status, blob = c.query_info(fid, wire.INFO_TYPE_FILESYSTEM,
                                    wire.FS_SIZE_INFORMATION)
        self.assertEqual(status, 0)
        self.assertEqual(len(blob), 24)

    def test_file_all_information_is_the_one_explorer_leans_on(self):
        c = self.connected()
        _status, fid = c.create("game.iso")
        status, blob = c.query_info(fid, wire.INFO_TYPE_FILE,
                                    wire.FILE_ALL_INFORMATION)
        self.assertEqual(status, 0)
        # Basic(40) + Standard(24) + Internal(8) + EA(4) + Access(4)
        # + Position(8) + Mode(4) + Alignment(4) + Name(4+n)
        self.assertGreaterEqual(len(blob), 100)

    def test_echo_answers(self):
        c = self.connected()
        self.assertEqual(c.echo(), 0)


class Compounding(ServerFixture):
    def test_chained_requests_all_get_answered(self):
        """Explorer opens a file as one compound CREATE + QUERY_INFO + CLOSE.

        Answering only the first request in the chain looks exactly like a hang.
        """
        c = self.connected()
        raw = "game.iso".encode("utf-16-le")
        create = struct.pack("<HBBIQQIIIIIHHII", 57, 0, 0, 2, 0, 0,
                             0x00120089, 0, 7, wire.FILE_OPEN, 0,
                             wire.HEADER_SIZE + 56, len(raw), 0, 0) + raw
        total = wire.HEADER_SIZE + len(create)
        aligned = (total + 7) & ~7
        create += b"\x00" * (aligned - total)

        hdr1 = wire.pack_header(wire.CMD_CREATE, 0, c.message_id,
                                tree_id=c.tree_id, session_id=c.session_id,
                                next_command=aligned)
        hdr1 = hdr1[:16] + struct.pack("<I", 0) + hdr1[20:]
        c.message_id += 1
        echo = struct.pack("<HH", 4, 0)
        hdr2 = wire.pack_header(wire.CMD_ECHO, 0, c.message_id,
                                tree_id=c.tree_id, session_id=c.session_id)
        hdr2 = hdr2[:16] + struct.pack("<I", 0) + hdr2[20:]
        c.message_id += 1

        wire.send_msg(c.sock, hdr1 + create + hdr2 + echo)
        reply = wire.recv_msg(c.sock)
        first = wire.parse_header(reply)
        self.assertEqual(first.status, 0)
        self.assertTrue(first.next_command, "the chain was not answered as a chain")
        second = wire.parse_header(reply[first.next_command:])
        self.assertEqual(second.command, wire.CMD_ECHO)
        self.assertEqual(second.status, 0)


class Robustness(ServerFixture):
    def test_a_truncated_request_does_not_take_the_server_down(self):
        c = self.connected()
        for command in (wire.CMD_CREATE, wire.CMD_READ, wire.CMD_WRITE,
                        wire.CMD_QUERY_DIRECTORY, wire.CMD_QUERY_INFO,
                        wire.CMD_SET_INFO, wire.CMD_TREE_CONNECT):
            with self.subTest(command=command):
                head, _ = c.call(command, b"\x00\x00")
                self.assertNotEqual(head.status, 0)
        self.assertEqual(c.echo(), 0, "server stopped answering after bad input")

    def test_a_stale_file_id_is_refused(self):
        c = self.connected()
        status, _ = c.read(struct.pack("<QQ", 999, 999), 0, 16)
        self.assertEqual(status, wire.STATUS_FILE_CLOSED)

    def test_a_mismatched_file_id_is_refused(self):
        """Persistent and volatile halves must agree, or it is not that handle."""
        c = self.connected()
        _status, fid = c.create("game.iso")
        volatile = struct.unpack("<QQ", fid)[1]
        status, _ = c.read(struct.pack("<QQ", volatile + 1, volatile), 0, 16)
        self.assertEqual(status, wire.STATUS_FILE_CLOSED)

    def test_garbage_is_not_mistaken_for_a_request(self):
        c = Client(self.port)
        self.addCleanup(c.close)
        wire.send_msg(c.sock, b"not an smb message at all")
        c2 = Client(self.port)
        self.addCleanup(c2.close)
        self.assertEqual(c2.negotiate(), wire.DIALECT_302)


if __name__ == "__main__":
    unittest.main()
