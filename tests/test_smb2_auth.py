"""The SMB2/SMB3 credential check must accept exactly one password.

Tested on its own, without a socket, for the same reason as the share
boundary: this is where a mistake hands a stranger the share.

Two layers here. The published vectors pin the primitives to the specification
rather than to this implementation -- MD4 had to be written by hand because
OpenSSL 3 removed it from hashlib, and an MD4 that is subtly wrong would still
be perfectly self-consistent, so a round-trip test alone would pass while no
real Windows client could ever log in. The vectors are from RFC 1320 appendix
A.5 and MS-NLMP section 4.2.4. On top of that sit the round-trip tests, which
build a response the way a client does and check the server's answer.
"""

import os
import struct
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

sys.path.insert(0, os.path.join(ROOT, "smb2_server"))
from smb2_auth import (  # noqa: E402
    AuthError,
    Authenticator,
    blob_timestamp,
    lmv2_response,
    md4,
    nt_hash,
    ntlmv2_proof,
    ntowfv2,
    parse_ntlmv2_response,
    server_challenge,
    session_base_key,
)

# A plausible AV_PAIR target info blob: NetBIOS domain name, then the list
# terminator. The server treats it as opaque bytes, but a real client always
# sends something here, so the tests should too.
TARGET_INFO = struct.pack("<HH", 2, 8) + "PS2SRV".encode("utf-16-le")[:8] + struct.pack("<HH", 0, 0)


def client_ntlmv2_response(password, user, domain, challenge,
                           client_challenge=b"\xaa" * 8, filetime=None,
                           target_info=TARGET_INFO):
    """Build an NTLMv2 response the way a client does.

    Deliberately written from the structure in MS-NLMP rather than by calling
    the server's own helpers to assemble the blob, so that a round trip is
    testing agreement between two sides and not a function agreeing with
    itself.
    """
    if filetime is None:
        filetime = 133_000_000_000_000_000  # a fixed, ordinary 2022-ish time
    blob = (b"\x01\x01\x00\x00\x00\x00\x00\x00"
            + struct.pack("<Q", filetime)
            + client_challenge
            + b"\x00" * 4
            + target_info
            + b"\x00" * 4)
    ntowf = ntowfv2(password, user, domain)
    proof = ntlmv2_proof(ntowf, challenge, blob)
    return proof + blob


class PublishedVectors(unittest.TestCase):
    """Pins the primitives to the specifications, not to this implementation."""

    def test_md4_rfc1320_suite(self):
        # RFC 1320 appendix A.5, in full. MD4 is hand-written here, so the
        # whole published suite is used rather than a sample of it.
        for message, expected in [
            (b"", "31d6cfe0d16ae931b73c59d7e0c089c0"),
            (b"a", "bde52cb31de33e46245e05fbdbd6fb24"),
            (b"abc", "a448017aaf21d8525fc10ae87aa6729d"),
            (b"message digest", "d9130a8164549fe818874806e1c7014b"),
            (b"abcdefghijklmnopqrstuvwxyz", "d79e1c308aa5bbcdeea8ed63df412da9"),
            (b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789",
             "043f8582f241db351ce627e153e7f0e4"),
            (b"1234567890" * 8, "e33b4ddc9c38f2199c3e7b164fcc0536"),
        ]:
            with self.subTest(message=message[:16]):
                self.assertEqual(md4(message).hex(), expected)

    def test_md4_spans_a_block_boundary(self):
        # The padding rule is the easy thing to get wrong: a message of exactly
        # 56 bytes needs a whole extra block, because the 0x80 plus the 8-byte
        # length no longer fit in the first one. RFC 1320's suite tops out at
        # 62 bytes and so never exercises that, which leaves the case where an
        # off-by-one in the padding still passes every published vector.
        #
        # These three are regression pins, not spec values: they were taken
        # from this implementation once the RFC suite above had already proved
        # it correct. They exist to fail loudly if the padding ever changes.
        self.assertEqual(md4(b"A" * 55).hex(), "1d7bb1528414bdcab709d5107f766d88")
        self.assertEqual(md4(b"A" * 56).hex(), "2eae267be1bd32ff073b50f7b654aed2")
        self.assertEqual(md4(b"A" * 57).hex(), "95e060c139fbd884c156222546efeca0")

        # Every length is still a 16-byte digest, including the multi-block ones.
        for length in (0, 63, 64, 65, 119, 120, 128, 1000):
            with self.subTest(length=length):
                self.assertEqual(len(md4(b"A" * length)), 16)

    def test_nt_hash_known_passwords(self):
        # The canonical NTLM examples. UTF-16LE and no salt, so these are fixed
        # for all time and any deviation means the encoding is wrong.
        self.assertEqual(nt_hash("password").hex(), "8846f7eaee8fb117ad06bdd830b7586c")
        self.assertEqual(nt_hash("Password").hex(), "a4f49c406510bdcab6824ee7c30fd852")

    def test_ntowfv2_ms_nlmp_vector(self):
        # MS-NLMP 4.2.4.1.1: Password "Password", User "User", Domain "Domain".
        self.assertEqual(
            ntowfv2("Password", "User", "Domain").hex(),
            "0c868a403bfd7a93a3001ef22ef02e3f")

    def test_user_is_uppercased_and_domain_is_not(self):
        # The asymmetry in MS-NLMP: Uppercase(User) + Domain, domain untouched.
        # Backwards here yields a key that is wrong for every password, and the
        # only symptom would be that nothing can ever log in.
        self.assertEqual(ntowfv2("Password", "user", "Domain"),
                         ntowfv2("Password", "USER", "Domain"))
        self.assertNotEqual(ntowfv2("Password", "User", "domain"),
                            ntowfv2("Password", "User", "DOMAIN"))

    def test_non_ascii_password_is_utf16le(self):
        # A password outside ASCII must not be silently latin-1'd or rejected.
        self.assertEqual(nt_hash("pässwörd"), md4("pässwörd".encode("utf-16-le")))


class ResponseParsing(unittest.TestCase):
    def test_splits_proof_from_blob(self):
        response = client_ntlmv2_response("pw", "user", "WORKGROUP", b"\x01" * 8)
        proof, blob = parse_ntlmv2_response(response)
        self.assertEqual(len(proof), 16)
        self.assertEqual(blob[:8], b"\x01\x01\x00\x00\x00\x00\x00\x00")

    def test_ntlmv1_response_is_refused_not_sliced(self):
        # An NTLMv1 response is 24 bytes with no header. Without the header
        # check it would be split into a 16-byte "proof" and an 8-byte
        # "blob" and then simply fail to match, which is the right outcome
        # for the wrong reason and logs a misleading cause.
        with self.assertRaises(AuthError) as caught:
            parse_ntlmv2_response(b"\x11" * 24)
        self.assertIn("NTLMv1", caught.exception.detail)

    def test_short_and_empty_responses_are_refused(self):
        for probe in (b"", b"\x00", b"\x11" * 23):
            with self.subTest(length=len(probe)):
                with self.assertRaises(AuthError):
                    parse_ntlmv2_response(probe)

    def test_non_bytes_is_refused_rather_than_raising_typeerror(self):
        with self.assertRaises(AuthError):
            parse_ntlmv2_response("not bytes")

    def test_blob_timestamp_round_trips(self):
        response = client_ntlmv2_response("pw", "u", "d", b"\x01" * 8,
                                          filetime=133_000_000_000_000_000)
        _, blob = parse_ntlmv2_response(response)
        self.assertAlmostEqual(blob_timestamp(blob), 1_655_526_400.0, delta=86400)


class PasswordRequired(unittest.TestCase):
    def setUp(self):
        self.auth = Authenticator({"ripto": "correct horse battery staple"})
        self.challenge = b"\x0f" * 8

    def test_correct_password_authenticates(self):
        response = client_ntlmv2_response(
            "correct horse battery staple", "ripto", "WORKGROUP", self.challenge)
        key = self.auth.verify("ripto", response, self.challenge)
        self.assertEqual(len(key), 16)

    def test_wrong_password_is_refused(self):
        response = client_ntlmv2_response("wrong", "ripto", "WORKGROUP", self.challenge)
        with self.assertRaises(AuthError) as caught:
            self.auth.verify("ripto", response, self.challenge)
        self.assertEqual(caught.exception.status, "STATUS_LOGON_FAILURE")

    def test_unknown_user_is_refused_with_the_same_status(self):
        # Same status as a wrong password, so a client cannot use the reply to
        # discover which user names exist.
        response = client_ntlmv2_response("whatever", "nobody", "WORKGROUP", self.challenge)
        with self.assertRaises(AuthError) as caught:
            self.auth.verify("nobody", response, self.challenge)
        self.assertEqual(caught.exception.status, "STATUS_LOGON_FAILURE")

    def test_user_names_are_case_insensitive(self):
        for spelling in ("ripto", "Ripto", "RIPTO"):
            with self.subTest(spelling=spelling):
                response = client_ntlmv2_response(
                    "correct horse battery staple", spelling, "WORKGROUP", self.challenge)
                self.assertIsNotNone(self.auth.verify(spelling, response, self.challenge))

    def test_response_for_another_challenge_is_refused(self):
        # The replay defence. A response captured against one challenge must be
        # worthless against the next session's.
        response = client_ntlmv2_response(
            "correct horse battery staple", "ripto", "WORKGROUP", b"\xaa" * 8)
        with self.assertRaises(AuthError):
            self.auth.verify("ripto", response, self.challenge)

    def test_tampered_blob_is_refused(self):
        # The proof covers the blob, so changing the client's own nonce after
        # the fact must invalidate it.
        response = bytearray(client_ntlmv2_response(
            "correct horse battery staple", "ripto", "WORKGROUP", self.challenge))
        response[-1] ^= 0xFF
        with self.assertRaises(AuthError):
            self.auth.verify("ripto", bytes(response), self.challenge)

    def test_stale_client_clock_still_authenticates(self):
        # Deliberate: a PS2 has no clock that survives being unplugged, so a
        # console routinely presents a time that is years out. Freshness is not
        # checked, and this test exists so that adding a window later is a
        # conscious decision rather than an accident.
        for filetime in (0, 1, 200_000_000_000_000_000):
            with self.subTest(filetime=filetime):
                response = client_ntlmv2_response(
                    "correct horse battery staple", "ripto", "WORKGROUP",
                    self.challenge, filetime=filetime)
                self.assertIsNotNone(self.auth.verify("ripto", response, self.challenge))

    def test_session_key_matches_what_the_client_would_derive(self):
        # Both sides must land on the same SessionBaseKey or SMB2 signing fails
        # later with an error that points nowhere near here.
        password = "correct horse battery staple"
        response = client_ntlmv2_response(password, "ripto", "WORKGROUP", self.challenge)
        proof, _ = parse_ntlmv2_response(response)
        expected = session_base_key(ntowfv2(password, "ripto", "WORKGROUP"), proof)
        self.assertEqual(self.auth.verify("ripto", response, self.challenge), expected)


class ConfigurationFailsClosed(unittest.TestCase):
    def test_no_users_is_an_error_not_an_open_share(self):
        # The whole point of the split: a misconfiguration that leaves no users
        # must lock the operator out, never admit everyone.
        for empty in ({}, None):
            with self.subTest(users=empty):
                with self.assertRaises(ValueError):
                    Authenticator(empty)

    def test_users_differing_only_by_case_are_rejected(self):
        # Otherwise one silently shadows the other and the operator cannot tell
        # which password is live.
        with self.assertRaises(ValueError):
            Authenticator({"ripto": "a", "Ripto": "b"})

    def test_open_mode_accepts_anyone(self):
        auth = Authenticator.open()
        self.assertTrue(auth.is_open)
        self.assertIsNone(auth.verify("anyone", b"", b"\x00" * 8))

    def test_password_mode_is_not_open(self):
        self.assertFalse(Authenticator({"u": "p"}).is_open)


class Challenge(unittest.TestCase):
    def test_is_eight_bytes_and_not_repeated(self):
        challenges = {server_challenge() for _ in range(64)}
        self.assertEqual(len(challenges), 64)
        for value in challenges:
            self.assertEqual(len(value), 8)


class Lmv2(unittest.TestCase):
    def test_shape_is_mac_then_client_nonce(self):
        ntowf = ntowfv2("Password", "User", "Domain")
        response = lmv2_response(ntowf, b"\x01" * 8, b"\x02" * 8)
        self.assertEqual(len(response), 24)
        self.assertEqual(response[16:], b"\x02" * 8)


if __name__ == "__main__":
    unittest.main()
