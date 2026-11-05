"""Known-answer tests for the SMB2/SMB3 signing primitives.

Every primitive in smb2_crypto is hand-written, so each is pinned to a
published test vector: FIPS-197 (AES-128), RFC 4493 (AES-CMAC), the usual RC4
vector, and the SP800-108 KDF checked against the derivation the
`cryptography` package produces when it is installed.
"""

import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SMB2 = os.path.join(_ROOT, "smb2_server")
for path in (_ROOT, _SMB2):
    if path not in sys.path:
        sys.path.insert(0, path)

import smb2_crypto as crypto

_RFC4493_KEY = bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c")
_RFC4493_MSG = bytes.fromhex(
    "6bc1bee22e409f96e93d7e117393172a"
    "ae2d8a571e03ac9c9eb76fac45af8e51"
    "30c81c46a35ce411e5fbc1191a0a52ef"
    "f69f2445df4f9b17ad2b417be66c3710")


class AesTests(unittest.TestCase):
    def test_fips197_c1(self):
        cipher = crypto.AES128(bytes.fromhex("000102030405060708090a0b0c0d0e0f"))
        out = cipher.encrypt_block(bytes.fromhex("00112233445566778899aabbccddeeff"))
        self.assertEqual(out.hex(), "69c4e0d86a7b0430d8cdb78070b4c55a")

    def test_key_and_block_length_checked(self):
        with self.assertRaises(ValueError):
            crypto.AES128(b"short")
        with self.assertRaises(ValueError):
            crypto.AES128(bytes(16)).encrypt_block(b"x" * 15)


class CmacTests(unittest.TestCase):
    def test_rfc4493_vectors(self):
        cases = [
            (b"", "bb1d6929e95937287fa37d129b756746"),
            (_RFC4493_MSG[:16], "070a16b46b4d4144f79bdd9dd04a287c"),
            (_RFC4493_MSG[:40], "dfa66747de9ae63030ca32611497c827"),
            (_RFC4493_MSG, "51f0bebf7e3b9d92fc49741779363cfe"),
        ]
        for message, expected in cases:
            with self.subTest(length=len(message)):
                self.assertEqual(crypto.aes_cmac(_RFC4493_KEY, message).hex(), expected)


class Rc4Tests(unittest.TestCase):
    def test_wikipedia_vector(self):
        self.assertEqual(crypto.rc4(b"Key", b"Plaintext").hex(), "bbf316e8d940af0ad3")

    def test_symmetric(self):
        data = os.urandom(40)
        key = os.urandom(16)
        self.assertEqual(crypto.rc4(key, crypto.rc4(key, data)), data)


class KdfTests(unittest.TestCase):
    def test_smb3_signing_key_pinned(self):
        # Produced by cryptography's KBKDFHMAC(SHA256, CounterMode, rlen=4,
        # llen=4, label=b"SMB2AESCMAC\0", context=b"SmbSign\0") on key 00..0f.
        key = crypto.signing_key(bytes(range(16)), 0x0300)
        self.assertEqual(key.hex(), "6234814cbb8ea9227440ebfeb5eacbe1")

    def test_matches_cryptography_when_available(self):
        try:
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.kdf.kbkdf import (
                CounterLocation, KBKDFHMAC, Mode)
        except ImportError:
            self.skipTest("cryptography not installed")
        session_key = os.urandom(16)
        ref = KBKDFHMAC(algorithm=hashes.SHA256(), mode=Mode.CounterMode,
                        length=16, rlen=4, llen=4,
                        location=CounterLocation.BeforeFixed,
                        label=b"SMB2AESCMAC\x00", context=b"SmbSign\x00",
                        fixed=None).derive(session_key)
        self.assertEqual(crypto.kdf(session_key, b"SMB2AESCMAC", b"SmbSign"), ref)

    def test_smb2_uses_session_key_directly_and_truncates(self):
        long_key = bytes(range(24))
        self.assertEqual(crypto.signing_key(long_key, 0x0210), long_key[:16])
        self.assertEqual(crypto.signing_key(b"\x01" * 8, 0x0202), b"\x01" * 8)


class MessageSigningTests(unittest.TestCase):
    def _message(self):
        header = bytearray(64)
        header[:4] = b"\xfeSMB"
        header[4:6] = (64).to_bytes(2, "little")
        header[48:64] = b"\xaa" * 16  # garbage already in the Signature field
        return bytes(header) + b"body bytes" * 7

    def test_roundtrip_each_dialect(self):
        session_key = os.urandom(16)
        for dialect in (0x0202, 0x0210, 0x0300, 0x0302):
            with self.subTest(dialect=hex(dialect)):
                key = crypto.signing_key(session_key, dialect)
                out = crypto.signed(key, dialect, self._message())
                self.assertTrue(crypto.verify(key, dialect, out))
                self.assertNotEqual(out[48:64], b"\xaa" * 16)
                tampered = out[:70] + bytes([out[70] ^ 1]) + out[71:]
                self.assertFalse(crypto.verify(key, dialect, tampered))
                self.assertFalse(crypto.verify(key, dialect, out[:63]))

    def test_signature_field_is_zeroed_before_signing(self):
        key = crypto.signing_key(bytes(16), 0x0210)
        msg = self._message()
        clean = msg[:48] + bytes(16) + msg[64:]
        self.assertEqual(crypto.sign(key, 0x0210, msg), crypto.sign(key, 0x0210, clean))

    def test_smb2_signature_is_truncated_hmac_sha256(self):
        import hashlib
        import hmac
        key = os.urandom(16)
        msg = self._message()
        zeroed = msg[:48] + bytes(16) + msg[64:]
        expected = hmac.new(key, zeroed, hashlib.sha256).digest()[:16]
        self.assertEqual(crypto.sign(key, 0x0210, msg), expected)

    def test_smb3_signature_is_cmac(self):
        key = os.urandom(16)
        msg = self._message()
        zeroed = msg[:48] + bytes(16) + msg[64:]
        self.assertEqual(crypto.sign(key, 0x0300, msg), crypto.aes_cmac(key, zeroed))


if __name__ == "__main__":
    unittest.main()
