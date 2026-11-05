"""End-to-end tests for what a Windows client needs beyond test_smb2_server:
the SMB1-framed opening NEGOTIATE, NTLM key exchange with a MIC, signed
sessions on SMB 2.x and 3.x, and FSCTL_VALIDATE_NEGOTIATE_INFO.

The client side is built here from MS-NLMP and MS-SMB2, on top of the small
client in test_smb2_server, so the server's reading of the protocol is checked
against a second one. Signing is also checked against an independent client
(smbprotocol) in test_smb2_independent_client when that package is installed.

Run:  python -m unittest tests.test_smb2_signed_sessions -v
"""

import hashlib
import hmac
import os
import struct
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "smb2_server"))

import smb2_crypto as crypto            # noqa: E402
import smb2_spnego as spnego            # noqa: E402
import smb2_wire as wire                # noqa: E402
from smb2_auth import (                 # noqa: E402
    ntlmv2_proof, ntowfv2, session_base_key)
from tests.test_smb2_server import (    # noqa: E402
    Client, PASSWORD, READ_ONLY, ServerFixture, USER, _spnego_wrap)

CLIENT_GUID = bytes(range(16))
CLIENT_CAPS = wire.GLOBAL_CAP_LARGE_MTU
ALL_DIALECTS = (wire.DIALECT_202, wire.DIALECT_210, wire.DIALECT_300, wire.DIALECT_302)
SMB1_HEADER = b"\xffSMBr" + b"\x00" * 27


def smb1_negotiate(*dialects):
    blob = b"".join(b"\x02" + d.encode("ascii") + b"\x00" for d in dialects)
    return SMB1_HEADER + b"\x00" + struct.pack("<H", len(blob)) + blob


class SigningClient(Client):
    """test_smb2_server's client plus the parts of MS-NLMP and MS-SMB2 a
    signing Windows client performs: KEY_EXCH, the AUTHENTICATE MIC, a
    SecurityMode of SIGNING_REQUIRED, and a signature on every message."""

    def __init__(self, port):
        super().__init__(port)
        self.dialect = None
        self.signing_key = None
        self.sign_requests = False
        self.security_mode = wire.SECURITY_MODE_SIGNING_ENABLED
        self.reply_signatures = []       # (command, was_signed, valid)

    def call(self, command, body, tree_id=None):
        hdr = wire.pack_header(command, 0, self.message_id,
                               tree_id=self.tree_id if tree_id is None else tree_id,
                               session_id=self.session_id)
        flags = wire.FLAG_SIGNED if (self.sign_requests and self.signing_key) else 0
        hdr = hdr[:16] + struct.pack("<I", flags) + hdr[20:]
        self.message_id += 1
        msg = hdr + body
        if flags:
            msg = crypto.signed(self.signing_key, self.dialect, msg)
        wire.send_msg(self.sock, msg)
        reply = wire.recv_msg(self.sock)
        if reply is None:
            raise AssertionError("server closed the connection")
        head = wire.parse_header(reply)
        was_signed = bool(head.flags & wire.FLAG_SIGNED)
        valid = (crypto.verify(self.signing_key, self.dialect, reply)
                 if was_signed and self.signing_key else None)
        self.reply_signatures.append((command, was_signed, valid))
        self.last_raw_reply = reply
        return head, reply[wire.HEADER_SIZE:]

    def negotiate(self, dialects=ALL_DIALECTS):
        body = struct.pack("<HHHHI16sQ", 36, len(dialects), self.security_mode, 0,
                           CLIENT_CAPS, CLIENT_GUID, 0)
        body += b"".join(struct.pack("<H", d) for d in dialects)
        head, reply = self.call(wire.CMD_NEGOTIATE, body)
        assert head.status == 0, "NEGOTIATE failed 0x%08X" % head.status
        self.dialect = struct.unpack_from("<H", reply, 4)[0]
        self.negotiated_mode = self.security_mode
        self.server_guid = reply[8:24]
        self.server_mode, = struct.unpack_from("<H", reply, 2)
        self.server_caps, = struct.unpack_from("<I", reply, 24)
        return self.dialect

    def session_setup(self, user=USER, password=PASSWORD, require_signing=True,
                      key_exch=True, mic=True, wrap=True, domain="WORKGROUP",
                      corrupt_mic=False):
        if require_signing:
            self.security_mode = wire.SECURITY_MODE_SIGNING_REQUIRED
        flags = (spnego.CHALLENGE_FLAGS | spnego.NEGOTIATE_SIGN | spnego.NEGOTIATE_VERSION
                 | (spnego.NEGOTIATE_KEY_EXCH if key_exch else 0))
        negotiate = (spnego.NTLMSSP_SIGNATURE + struct.pack("<I", 1)
                     + struct.pack("<I", flags)
                     + struct.pack("<HHI", 0, 0, 40) + struct.pack("<HHI", 0, 0, 40)
                     + struct.pack("<BBHBBBB", 10, 0, 19041, 0, 0, 0, 15))
        token = _spnego_wrap(negotiate) if wrap else negotiate
        head, reply = self._session_blob(token)
        assert head.status == wire.STATUS_MORE_PROCESSING_REQUIRED, (
            "expected a challenge, got 0x%08X" % head.status)
        self.session_id = head.session_id

        off, length = struct.unpack_from("<HH", reply, 4)
        blob = reply[off - wire.HEADER_SIZE:off - wire.HEADER_SIZE + length]
        challenge_msg = spnego.trim(spnego.extract_ntlm(blob))
        self.challenge_flags, = struct.unpack_from("<I", challenge_msg, 20)
        challenge = challenge_msg[24:32]
        info_len, _max, info_at = struct.unpack_from("<HHI", challenge_msg, 40)
        target_info = challenge_msg[info_at:info_at + info_len]
        if mic:
            # MsvAvFlags with the MIC bit, spliced in before MsvAvEOL.
            target_info = (target_info[:-4]
                           + struct.pack("<HHI", spnego.MSV_AV_FLAGS, 4,
                                         spnego.AV_FLAG_MIC_PRESENT)
                           + target_info[-4:])

        blob_body = (struct.pack("<BBHIQ", 1, 1, 0, 0, 0) + os.urandom(8)
                     + struct.pack("<I", 0) + target_info + struct.pack("<I", 0))
        ntowf = ntowfv2(password, user, domain)
        proof = ntlmv2_proof(ntowf, challenge, blob_body)
        base_key = session_base_key(ntowf, proof)
        if key_exch:
            exported = os.urandom(16)
            encrypted = crypto.rc4(base_key, exported)
        else:
            exported, encrypted = base_key, b""
        auth = self._authenticate(proof + blob_body, user, domain, flags, encrypted)
        if mic:
            mac = hmac.new(exported, negotiate + challenge_msg + auth, hashlib.md5).digest()
            if corrupt_mic:
                mac = bytes(b ^ 0xFF for b in mac)
            auth = auth[:72] + mac + auth[88:]
        if wrap:
            tlv = spnego._tlv
            auth = tlv(0xa1, tlv(0x30, tlv(0xa2, tlv(0x04, auth))
                                 + tlv(0xa3, tlv(0x04, b"\x01\x00" * 8))))
        head, self.last_session_reply = self._session_blob(auth)
        if head.status == 0:
            self.signing_key = crypto.signing_key(exported, self.dialect)
            self.sign_requests = require_signing
            self.session_flags, = struct.unpack_from("<H", self.last_session_reply, 2)
            # The final SESSION_SETUP reply arrived before the key was known;
            # verify it now that it is.
            command, was_signed, _ = self.reply_signatures[-1]
            valid = (crypto.verify(self.signing_key, self.dialect, self.last_raw_reply)
                     if was_signed else None)
            self.reply_signatures[-1] = (command, was_signed, valid)
        return head.status

    def _authenticate(self, nt_response, user, domain, flags, encrypted_key):
        user_u = user.encode("utf-16-le")
        domain_u = domain.encode("utf-16-le")
        fixed = 88                                  # fields, Version, MIC
        lm_at = fixed
        nt_at = lm_at + 24
        dom_at = nt_at + len(nt_response)
        user_at = dom_at + len(domain_u)
        ws_at = user_at + len(user_u)
        key_at = ws_at
        return (spnego.NTLMSSP_SIGNATURE + struct.pack("<I", 3)
                + struct.pack("<HHI", 24, 24, lm_at)
                + struct.pack("<HHI", len(nt_response), len(nt_response), nt_at)
                + struct.pack("<HHI", len(domain_u), len(domain_u), dom_at)
                + struct.pack("<HHI", len(user_u), len(user_u), user_at)
                + struct.pack("<HHI", 0, 0, ws_at)
                + struct.pack("<HHI", len(encrypted_key), len(encrypted_key), key_at)
                + struct.pack("<I", flags)
                + struct.pack("<BBHBBBB", 10, 0, 19041, 0, 0, 0, 15)
                + b"\x00" * 16                                  # MIC, filled later
                + b"\x00" * 24 + nt_response + domain_u + user_u + encrypted_key)

    def _session_blob(self, token):
        body = struct.pack("<HBBIIHHQ", 25, 0, self.security_mode, 0, 0,
                           wire.HEADER_SIZE + 24, len(token), 0)
        return self.call(wire.CMD_SESSION_SETUP, body + token)

    def validate_negotiate(self, caps=CLIENT_CAPS, guid=CLIENT_GUID, mode=None,
                           dialects=ALL_DIALECTS):
        if mode is None:
            mode = self.negotiated_mode
        data = struct.pack("<I16sHH", caps, guid, mode, len(dialects))
        data += b"".join(struct.pack("<H", d) for d in dialects)
        body = struct.pack("<HHI16sIIIIIIII", 57, 0, wire.FSCTL_VALIDATE_NEGOTIATE_INFO,
                           b"\xff" * 16, wire.HEADER_SIZE + 56, len(data), 0, 0, 0,
                           24, 1, 0) + data
        head, reply = self.call(wire.CMD_IOCTL, body)
        if head.status != 0:
            return head.status, None
        out_off, out_len = struct.unpack_from("<II", reply, 32)
        start = out_off - wire.HEADER_SIZE
        return 0, reply[start:start + out_len]


class SigningFixture(ServerFixture):
    def signed_client(self, dialects=ALL_DIALECTS, **kw):
        c = SigningClient(self.port)
        self.addCleanup(c.close)
        c.negotiate(dialects)
        status = c.session_setup(**kw)
        self.assertEqual(status, 0, "session setup failed 0x%08X" % status)
        return c


class Smb1FramedNegotiate(SigningFixture):
    """MS-SMB2 3.3.5.3.1: the multi-protocol negotiate Windows opens with."""

    def _send_smb1(self, *dialects):
        c = SigningClient(self.port)
        self.addCleanup(c.close)
        wire.send_msg(c.sock, smb1_negotiate(*dialects))
        return c

    def test_wildcard_gets_02ff_and_a_real_negotiate_follows(self):
        c = self._send_smb1("NT LM 0.12", "SMB 2.002", "SMB 2.???")
        reply = wire.recv_msg(c.sock)
        head = wire.parse_header(reply)
        self.assertEqual(head.command, wire.CMD_NEGOTIATE)
        self.assertEqual(head.status, 0)
        self.assertEqual(head.message_id, 0)
        self.assertEqual(struct.unpack_from("<H", reply, wire.HEADER_SIZE + 4)[0], 0x02FF)
        # Now the real one, as Windows sends it next, on the same connection.
        c.message_id = 1
        self.assertEqual(c.negotiate(), wire.DIALECT_302)
        self.assertEqual(c.session_setup(), 0)

    def test_2002_only_negotiates_2002(self):
        c = self._send_smb1("NT LM 0.12", "SMB 2.002")
        reply = wire.recv_msg(c.sock)
        self.assertEqual(struct.unpack_from("<H", reply, wire.HEADER_SIZE + 4)[0],
                         wire.DIALECT_202)
        c.dialect = wire.DIALECT_202
        c.message_id = 1
        self.assertEqual(c.session_setup(), 0)

    def test_smb1_only_gets_no_reply_and_the_connection_stays_up(self):
        c = self._send_smb1("PC NETWORK PROGRAM 1.0", "NT LM 0.12")
        c.sock.settimeout(0.5)
        with self.assertRaises(OSError):
            c.sock.recv(1)
        c.sock.settimeout(5)
        self.assertEqual(c.negotiate(), wire.DIALECT_302)  # still an SMB2 server


class SignedSessions(SigningFixture):
    def test_every_reply_is_signed_once_the_client_requires_it(self):
        for dialects in ((wire.DIALECT_210,), (wire.DIALECT_302,)):
            with self.subTest(dialect=hex(dialects[0])):
                c = self.signed_client(dialects)
                self.assertEqual(c.tree_connect("games"), 0)
                status, fid = c.create("game.iso", access=READ_ONLY)
                self.assertEqual(status, 0)
                status, data = c.read(fid, 0, 4096)
                self.assertEqual(status, 0)
                self.assertEqual(data, bytes((i * 7) & 0xFF for i in range(4096)))
                self.assertEqual(c.close_handle(fid), 0)
                # The final SESSION_SETUP and everything after it is signed
                # and verifies; the challenge round trip is not.
                setups = [r for r in c.reply_signatures if r[0] == wire.CMD_SESSION_SETUP]
                self.assertEqual([s[1] for s in setups], [False, True])
                after = c.reply_signatures[c.reply_signatures.index(setups[-1]) + 1:]
                self.assertTrue(after)
                self.assertTrue(all(signed and valid for _c, signed, valid in after),
                                after)

    def test_key_exchange_is_optional(self):
        c = self.signed_client((wire.DIALECT_302,), key_exch=False)
        self.assertEqual(c.tree_connect("games"), 0)
        self.assertTrue(all(valid for _c, s, valid in c.reply_signatures if s))

    def test_a_bare_ntlmssp_client_still_logs_in_unsigned(self):
        c = SigningClient(self.port)
        self.addCleanup(c.close)
        c.negotiate((wire.DIALECT_300,))
        status = c.session_setup(require_signing=False, key_exch=False, mic=False,
                                 wrap=False)
        self.assertEqual(status, 0)
        self.assertNotEqual(c.challenge_flags & spnego.NEGOTIATE_KEY_EXCH,
                            spnego.NEGOTIATE_KEY_EXCH)
        self.assertEqual(c.tree_connect("games"), 0)
        self.assertFalse(any(signed for _c, signed, _v in c.reply_signatures))

    def test_a_signed_request_on_an_unsigned_session_is_answered_signed(self):
        c = self.signed_client((wire.DIALECT_210,), require_signing=False)
        self.assertEqual(c.tree_connect("games"), 0)
        self.assertFalse(c.reply_signatures[-1][1])
        c.sign_requests = True
        self.assertEqual(c.echo(), 0)
        self.assertEqual(c.reply_signatures[-1][1:], (True, True))

    def test_a_wrong_mic_is_a_logon_failure(self):
        c = SigningClient(self.port)
        self.addCleanup(c.close)
        c.negotiate()
        self.assertEqual(c.session_setup(corrupt_mic=True), wire.STATUS_LOGON_FAILURE)

    def test_an_unsigned_request_is_refused_when_signing_is_required(self):
        c = self.signed_client((wire.DIALECT_302,))
        c.sign_requests = False
        self.assertEqual(c.tree_connect("games"), wire.STATUS_ACCESS_DENIED)
        c.sign_requests = True
        self.assertEqual(c.tree_connect("games"), 0)

    def test_a_tampered_signature_is_refused(self):
        c = self.signed_client((wire.DIALECT_302,))
        self.assertEqual(c.tree_connect("games"), 0)
        real_key = c.signing_key
        c.signing_key = bytes(b ^ 1 for b in real_key)
        head, _ = c.call(wire.CMD_ECHO, struct.pack("<HH", 4, 0))
        self.assertEqual(head.status, wire.STATUS_ACCESS_DENIED)
        c.signing_key = real_key
        self.assertEqual(c.echo(), 0)

    def test_the_session_is_not_guest(self):
        c = self.signed_client()
        self.assertEqual(c.session_flags & wire.SESSION_FLAG_IS_GUEST, 0)


class ValidateNegotiateInfo(SigningFixture):
    def test_matching_claims_get_the_servers_values_back(self):
        c = self.signed_client((wire.DIALECT_302,))
        self.assertEqual(c.tree_connect("games"), 0)
        status, out = c.validate_negotiate(dialects=(wire.DIALECT_302,))
        self.assertEqual(status, 0)
        caps, guid, mode, dialect = struct.unpack("<I16sHH", out)
        self.assertEqual((caps, guid, mode, dialect),
                         (c.server_caps, c.server_guid, c.server_mode, c.dialect))
        self.assertEqual(c.reply_signatures[-1][1:], (True, True))

    def test_a_mismatch_drops_the_connection(self):
        c = self.signed_client((wire.DIALECT_302,))
        self.assertEqual(c.tree_connect("games"), 0)
        status, _ = c.validate_negotiate(dialects=(wire.DIALECT_302, wire.DIALECT_210))
        self.assertEqual(status, wire.STATUS_ACCESS_DENIED)
        c.sock.settimeout(5)
        self.assertEqual(c.sock.recv(1), b"")


class OpenModeIsGuest(SigningFixture):
    open_mode = True

    def test_a_named_user_in_open_mode_is_a_guest_without_a_key(self):
        c = SigningClient(self.port)
        self.addCleanup(c.close)
        c.negotiate()
        self.assertEqual(c.session_setup(require_signing=False), 0)
        self.assertTrue(c.session_flags & wire.SESSION_FLAG_IS_GUEST)
        self.assertFalse(c.reply_signatures[-1][1])
        self.assertEqual(c.tree_connect("games"), 0)


if __name__ == "__main__":
    unittest.main()
