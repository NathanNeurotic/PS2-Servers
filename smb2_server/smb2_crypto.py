"""Message signing for the SMB2/SMB3 server, and the primitives it needs.

Windows 11 (24H2 and later) requires SMB signing on every outbound
connection by default, so a server that cannot sign is one Windows Explorer
refuses to mount even on port 445. Signing needs four primitives the Python
standard library does not provide: AES-128 (for AES-CMAC, the SMB 3.x
signature), AES-CMAC itself, the SP800-108 key derivation SMB 3.x builds its
keys with, and RC4 (NTLM wraps the exchanged session key with it). They are
implemented here rather than taken from a dependency, for the same reason
smb2_auth carries MD4: the server runs from a bare Python, is frozen into a
launcher, and this is the kind of code that has to be read, not trusted.

Only AES *encryption* is implemented: CMAC never decrypts. The pure-Python
AES costs roughly 20 microseconds per 16-byte block, under a megabyte per
second, and an SMB 3.x signature covers every byte of every READ reply --
Windows copying a game through a signed SMB3 session would crawl. So
aes_cmac prefers the operating system's own CMAC when one can be reached
through ctypes -- bcrypt.dll on Windows, OpenSSL's libcrypto on Linux -- and
the pure-Python code is the fallback and the reference the native paths are
tested against. SMB 2.x signatures are HMAC-SHA256 from hashlib and were
never slow.

What SMB signing is (MS-SMB2 3.1.4.1, 3.1.5.1): a 16-byte MAC over the whole
message -- header with the Signature field zeroed, plus body -- keyed from the
NTLM session key. SMB 2.0.2 and 2.1 use HMAC-SHA256 truncated to 16 bytes with
the session key itself. SMB 3.0 and 3.0.2 use AES-128-CMAC with a key derived
from the session key by KDF(label "SMB2AESCMAC", context "SmbSign"). The
Signature field occupies header bytes 48..63.
"""

import hmac
import struct
import sys
from hashlib import sha256

SIGNATURE_OFFSET = 48
SIGNATURE_SIZE = 16

# --- AES-128, encryption only (FIPS-197) ---------------------------------- #

_SBOX = [
    0x63, 0x7c, 0x77, 0x7b, 0xf2, 0x6b, 0x6f, 0xc5, 0x30, 0x01, 0x67, 0x2b, 0xfe, 0xd7, 0xab, 0x76,
    0xca, 0x82, 0xc9, 0x7d, 0xfa, 0x59, 0x47, 0xf0, 0xad, 0xd4, 0xa2, 0xaf, 0x9c, 0xa4, 0x72, 0xc0,
    0xb7, 0xfd, 0x93, 0x26, 0x36, 0x3f, 0xf7, 0xcc, 0x34, 0xa5, 0xe5, 0xf1, 0x71, 0xd8, 0x31, 0x15,
    0x04, 0xc7, 0x23, 0xc3, 0x18, 0x96, 0x05, 0x9a, 0x07, 0x12, 0x80, 0xe2, 0xeb, 0x27, 0xb2, 0x75,
    0x09, 0x83, 0x2c, 0x1a, 0x1b, 0x6e, 0x5a, 0xa0, 0x52, 0x3b, 0xd6, 0xb3, 0x29, 0xe3, 0x2f, 0x84,
    0x53, 0xd1, 0x00, 0xed, 0x20, 0xfc, 0xb1, 0x5b, 0x6a, 0xcb, 0xbe, 0x39, 0x4a, 0x4c, 0x58, 0xcf,
    0xd0, 0xef, 0xaa, 0xfb, 0x43, 0x4d, 0x33, 0x85, 0x45, 0xf9, 0x02, 0x7f, 0x50, 0x3c, 0x9f, 0xa8,
    0x51, 0xa3, 0x40, 0x8f, 0x92, 0x9d, 0x38, 0xf5, 0xbc, 0xb6, 0xda, 0x21, 0x10, 0xff, 0xf3, 0xd2,
    0xcd, 0x0c, 0x13, 0xec, 0x5f, 0x97, 0x44, 0x17, 0xc4, 0xa7, 0x7e, 0x3d, 0x64, 0x5d, 0x19, 0x73,
    0x60, 0x81, 0x4f, 0xdc, 0x22, 0x2a, 0x90, 0x88, 0x46, 0xee, 0xb8, 0x14, 0xde, 0x5e, 0x0b, 0xdb,
    0xe0, 0x32, 0x3a, 0x0a, 0x49, 0x06, 0x24, 0x5c, 0xc2, 0xd3, 0xac, 0x62, 0x91, 0x95, 0xe4, 0x79,
    0xe7, 0xc8, 0x37, 0x6d, 0x8d, 0xd5, 0x4e, 0xa9, 0x6c, 0x56, 0xf4, 0xea, 0x65, 0x7a, 0xae, 0x08,
    0xba, 0x78, 0x25, 0x2e, 0x1c, 0xa6, 0xb4, 0xc6, 0xe8, 0xdd, 0x74, 0x1f, 0x4b, 0xbd, 0x8b, 0x8a,
    0x70, 0x3e, 0xb5, 0x66, 0x48, 0x03, 0xf6, 0x0e, 0x61, 0x35, 0x57, 0xb9, 0x86, 0xc1, 0x1d, 0x9e,
    0xe1, 0xf8, 0x98, 0x11, 0x69, 0xd9, 0x8e, 0x94, 0x9b, 0x1e, 0x87, 0xe9, 0xce, 0x55, 0x28, 0xdf,
    0x8c, 0xa1, 0x89, 0x0d, 0xbf, 0xe6, 0x42, 0x68, 0x41, 0x99, 0x2d, 0x0f, 0xb0, 0x54, 0xbb, 0x16,
]


def _xtime(a):
    a <<= 1
    if a & 0x100:
        a ^= 0x11b
    return a & 0xff


# Multiplication by 2 and 3 in GF(2^8), tabulated once: MixColumns uses nothing else.
_MUL2 = [_xtime(i) for i in range(256)]
_MUL3 = [_MUL2[i] ^ i for i in range(256)]


def _expand_key(key):
    """The 11 round keys of AES-128 as a flat list of 176 bytes."""
    if len(key) != 16:
        raise ValueError("AES-128 needs a 16-byte key")
    w = list(key)
    rcon = 1
    for i in range(16, 176, 4):
        t = w[i - 4:i]
        if i % 16 == 0:
            t = [_SBOX[t[1]] ^ rcon, _SBOX[t[2]], _SBOX[t[3]], _SBOX[t[0]]]
            rcon = _xtime(rcon)
        w += [w[i - 16 + j] ^ t[j] for j in range(4)]
    return w


class AES128:
    """One expanded key; encrypt_block is the only operation."""

    def __init__(self, key):
        self._round_keys = _expand_key(bytes(key))

    def encrypt_block(self, block):
        if len(block) != 16:
            raise ValueError("AES block is 16 bytes")
        rk = self._round_keys
        s = [block[i] ^ rk[i] for i in range(16)]
        for rnd in range(1, 11):
            # SubBytes + ShiftRows: the state is column-major, so the byte at
            # row r, column c sits at index 4c + r and row r rotates by r.
            s = [_SBOX[s[(i + 4 * (i % 4)) % 16]] for i in range(16)]
            if rnd != 10:
                m = []
                for c in range(0, 16, 4):
                    a0, a1, a2, a3 = s[c:c + 4]
                    m += [_MUL2[a0] ^ _MUL3[a1] ^ a2 ^ a3,
                          a0 ^ _MUL2[a1] ^ _MUL3[a2] ^ a3,
                          a0 ^ a1 ^ _MUL2[a2] ^ _MUL3[a3],
                          _MUL3[a0] ^ a1 ^ a2 ^ _MUL2[a3]]
                s = m
            base = rnd * 16
            s = [s[i] ^ rk[base + i] for i in range(16)]
        return bytes(s)


# --- AES-CMAC (RFC 4493) -------------------------------------------------- #

def _shift_left_one(block):
    n = int.from_bytes(block, "big") << 1
    return (n & ((1 << 128) - 1)).to_bytes(16, "big"), bool(n >> 128)


def aes_cmac(key, message):
    """AES-128-CMAC of `message`, 16 bytes. Native when possible."""
    native = _native_cmac()
    if native is not None:
        return native(bytes(key), bytes(message))
    return aes_cmac_pure(key, message)


def aes_cmac_pure(key, message):
    """AES-128-CMAC in pure Python: the reference implementation."""
    cipher = AES128(key)
    l_block = cipher.encrypt_block(b"\x00" * 16)
    k1, carry = _shift_left_one(l_block)
    if carry:
        k1 = k1[:-1] + bytes([k1[-1] ^ 0x87])
    k2, carry = _shift_left_one(k1)
    if carry:
        k2 = k2[:-1] + bytes([k2[-1] ^ 0x87])

    message = bytes(message)
    n = (len(message) + 15) // 16
    if n == 0:
        n = 1
        complete = False
    else:
        complete = len(message) % 16 == 0
    last = message[(n - 1) * 16:]
    if complete:
        last = bytes(a ^ b for a, b in zip(last, k1))
    else:
        last = last + b"\x80" + b"\x00" * (15 - len(last))
        last = bytes(a ^ b for a, b in zip(last, k2))

    x = b"\x00" * 16
    for i in range(n - 1):
        block = message[i * 16:(i + 1) * 16]
        x = cipher.encrypt_block(bytes(a ^ b for a, b in zip(x, block)))
    return cipher.encrypt_block(bytes(a ^ b for a, b in zip(x, last)))


# --- native CMAC through ctypes ------------------------------------------ #
#
# Each backend is a function (key, message) -> 16 bytes, or None if the
# library it needs is not there. Nothing is loaded until the first signature,
# and a backend that fails to initialise leaves the pure-Python path in place.

_NATIVE = []            # [backend] once probed; [] means not yet, [None] means none


def _native_cmac():
    if not _NATIVE:
        backend = None
        if sys.platform == "win32":
            backend = _bcrypt_cmac()
        elif sys.platform.startswith("linux"):
            # Not macOS: ctypes.util.find_library("crypto") names Apple's
            # /usr/lib/libcrypto.dylib there, and loading it aborts the process.
            backend = _openssl_cmac()
        _NATIVE.append(backend)
    return _NATIVE[0]


def native_cmac_name():
    """Which CMAC is in use: "bcrypt", "openssl" or "pure-python". For logs."""
    backend = _native_cmac()
    return getattr(backend, "backend_name", "pure-python") if backend else "pure-python"


def _bcrypt_cmac():
    """Windows CNG: BCryptCreateHash with the AES-CMAC algorithm provider."""
    try:
        import ctypes
        from ctypes import wintypes
        bcrypt = ctypes.WinDLL("bcrypt.dll")
    except (ImportError, OSError, AttributeError):
        return None
    opener = bcrypt.BCryptOpenAlgorithmProvider
    opener.argtypes = [ctypes.POINTER(ctypes.c_void_p), wintypes.LPCWSTR,
                       wintypes.LPCWSTR, wintypes.ULONG]
    alg = ctypes.c_void_p()
    if opener(ctypes.byref(alg), "AES-CMAC", None, 0) != 0:
        return None
    create, update, finish, destroy = (bcrypt.BCryptCreateHash, bcrypt.BCryptHashData,
                                       bcrypt.BCryptFinishHash, bcrypt.BCryptDestroyHash)
    create.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
                       wintypes.ULONG, ctypes.c_char_p, wintypes.ULONG, wintypes.ULONG]
    update.argtypes = [ctypes.c_void_p, ctypes.c_char_p, wintypes.ULONG, wintypes.ULONG]
    finish.argtypes = [ctypes.c_void_p, ctypes.c_char_p, wintypes.ULONG, wintypes.ULONG]
    destroy.argtypes = [ctypes.c_void_p]

    def cmac(key, message):
        hash_handle = ctypes.c_void_p()
        if create(alg, ctypes.byref(hash_handle), None, 0, key, len(key), 0) != 0:
            raise OSError("BCryptCreateHash(AES-CMAC) failed")
        try:
            out = ctypes.create_string_buffer(16)
            if (update(hash_handle, message, len(message), 0) != 0
                    or finish(hash_handle, out, 16, 0) != 0):
                raise OSError("BCrypt AES-CMAC failed")
            return out.raw
        finally:
            destroy(hash_handle)

    try:
        if cmac(bytes(16), b"") != bytes.fromhex("66e94bd4ef8a2c3b884cfa59ca342b2e") \
                and aes_cmac_pure(bytes(16), b"") != cmac(bytes(16), b""):
            return None
    except OSError:
        return None
    cmac.backend_name = "bcrypt"
    return cmac


def _openssl_cmac():
    """OpenSSL libcrypto: EVP_MAC (3.x) or the CMAC_* API (1.1)."""
    try:
        import ctypes
        import ctypes.util
        path = ctypes.util.find_library("crypto")
        if not path:
            return None
        lib = ctypes.CDLL(path)
    except (ImportError, OSError):
        return None
    cmac = _openssl3_cmac(ctypes, lib) or _openssl1_cmac(ctypes, lib)
    if cmac is None:
        return None
    try:
        if cmac(bytes(16), b"") != aes_cmac_pure(bytes(16), b""):
            return None
    except OSError:
        return None
    cmac.backend_name = "openssl"
    return cmac


def _openssl3_cmac(ctypes, lib):
    try:
        fetch, ctx_new, init, update, final, ctx_free, mac_free = (
            lib.EVP_MAC_fetch, lib.EVP_MAC_CTX_new, lib.EVP_MAC_init,
            lib.EVP_MAC_update, lib.EVP_MAC_final, lib.EVP_MAC_CTX_free, lib.EVP_MAC_free)
    except AttributeError:
        return None

    class OsslParam(ctypes.Structure):
        _fields_ = [("key", ctypes.c_char_p), ("data_type", ctypes.c_uint),
                    ("data", ctypes.c_void_p), ("data_size", ctypes.c_size_t),
                    ("return_size", ctypes.c_size_t)]

    fetch.restype = ctypes.c_void_p
    fetch.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
    ctx_new.restype = ctypes.c_void_p
    ctx_new.argtypes = [ctypes.c_void_p]
    init.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_void_p]
    update.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
    final.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_size_t),
                      ctypes.c_size_t]
    ctx_free.argtypes = [ctypes.c_void_p]
    mac = fetch(None, b"CMAC", None)
    if not mac:
        return None
    cipher_name = ctypes.create_string_buffer(b"AES-128-CBC")
    params = (OsslParam * 2)()
    params[0] = OsslParam(b"cipher", 4, ctypes.cast(cipher_name, ctypes.c_void_p), 0, 0)

    def cmac(key, message):
        ctx = ctx_new(mac)
        if not ctx:
            raise OSError("EVP_MAC_CTX_new failed")
        try:
            out = ctypes.create_string_buffer(16)
            outl = ctypes.c_size_t(0)
            if (init(ctx, key, len(key), params) != 1
                    or update(ctx, message, len(message)) != 1
                    or final(ctx, out, ctypes.byref(outl), 16) != 1 or outl.value != 16):
                raise OSError("EVP_MAC CMAC failed")
            return out.raw
        finally:
            ctx_free(ctx)
    return cmac


def _openssl1_cmac(ctypes, lib):
    try:
        ctx_new, init, update, final, ctx_free, aes_cbc = (
            lib.CMAC_CTX_new, lib.CMAC_Init, lib.CMAC_Update, lib.CMAC_Final,
            lib.CMAC_CTX_free, lib.EVP_aes_128_cbc)
    except AttributeError:
        return None
    ctx_new.restype = ctypes.c_void_p
    aes_cbc.restype = ctypes.c_void_p
    init.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_void_p,
                     ctypes.c_void_p]
    update.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
    final.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_size_t)]
    ctx_free.argtypes = [ctypes.c_void_p]
    cipher = aes_cbc()

    def cmac(key, message):
        ctx = ctx_new()
        if not ctx:
            raise OSError("CMAC_CTX_new failed")
        try:
            out = ctypes.create_string_buffer(16)
            outl = ctypes.c_size_t(0)
            if (init(ctx, key, len(key), cipher, None) != 1
                    or update(ctx, message, len(message)) != 1
                    or final(ctx, out, ctypes.byref(outl)) != 1 or outl.value != 16):
                raise OSError("CMAC failed")
            return out.raw
        finally:
            ctx_free(ctx)
    return cmac


# --- SP800-108 KDF in counter mode with HMAC-SHA256 (MS-SMB2 3.1.4.2) ---- #

def kdf(key, label, context, length=16):
    """KDF-CTR-HMAC-SHA256, as SMB 3.x derives its keys.

    `label` and `context` are given without the trailing NUL MS-SMB2 shows in
    its tables ("SMB2AESCMAC\\0"); it is appended here, once, so a caller
    cannot forget it or add it twice. The counter and the output length are
    32-bit big-endian, and the length is in bits.
    """
    out = b""
    counter = 1
    while len(out) < length:
        out += hmac.new(key, struct.pack(">I", counter) + label + b"\x00"
                        + b"\x00" + context + b"\x00" + struct.pack(">I", length * 8),
                        sha256).digest()
        counter += 1
    return out[:length]


# --- RC4 (NTLM's key wrap) ------------------------------------------------ #

def rc4(key, data):
    """RC4 keystream XOR. Encryption and decryption are the same operation.

    Used for exactly one thing: NTLM's EncryptedRandomSessionKey, 16 bytes
    the client wrapped with the KeyExchangeKey when NTLMSSP_NEGOTIATE_KEY_EXCH
    was negotiated (MS-NLMP 3.1.5.1.2). RC4 is not fit for anything else and
    nothing else here uses it.
    """
    s = list(range(256))
    j = 0
    key = bytes(key)
    for i in range(256):
        j = (j + s[i] + key[i % len(key)]) & 0xff
        s[i], s[j] = s[j], s[i]
    out = bytearray()
    i = j = 0
    for byte in bytes(data):
        i = (i + 1) & 0xff
        j = (j + s[i]) & 0xff
        s[i], s[j] = s[j], s[i]
        out.append(byte ^ s[(s[i] + s[j]) & 0xff])
    return bytes(out)


# --- SMB2/SMB3 signing ---------------------------------------------------- #

def signing_key(session_key, dialect):
    """The per-session signing key for `dialect` (MS-SMB2 3.3.5.5.3).

    Session.SessionKey is the first 16 bytes of what NTLM produced; a longer
    key is truncated, a shorter one is used as it is. SMB 2.x signs with it
    directly. SMB 3.0 and 3.0.2 derive a separate key, so that a signing key
    never doubles as an encryption key.
    """
    key = bytes(session_key)[:16]
    if dialect >= 0x0300:
        return kdf(key, b"SMB2AESCMAC", b"SmbSign")
    return key


def sign(signing_key_bytes, dialect, message):
    """The 16-byte signature of one SMB2 message (header + body).

    The message is signed with its own Signature field as zeros; the caller
    is expected to pass the bytes it is about to send, so the field is zeroed
    here rather than trusted to be.
    """
    message = bytes(message)
    zeroed = (message[:SIGNATURE_OFFSET] + b"\x00" * SIGNATURE_SIZE
              + message[SIGNATURE_OFFSET + SIGNATURE_SIZE:])
    if dialect >= 0x0300:
        return aes_cmac(signing_key_bytes, zeroed)
    return hmac.new(signing_key_bytes, zeroed, sha256).digest()[:SIGNATURE_SIZE]


def signed(signing_key_bytes, dialect, message):
    """`message` with its Signature field filled in."""
    message = bytes(message)
    mac = sign(signing_key_bytes, dialect, message)
    return (message[:SIGNATURE_OFFSET] + mac
            + message[SIGNATURE_OFFSET + SIGNATURE_SIZE:])


def verify(signing_key_bytes, dialect, message):
    """True if `message` carries a valid signature. Constant-time compare."""
    message = bytes(message)
    if len(message) < SIGNATURE_OFFSET + SIGNATURE_SIZE:
        return False
    expected = sign(signing_key_bytes, dialect, message)
    return hmac.compare_digest(
        expected, message[SIGNATURE_OFFSET:SIGNATURE_OFFSET + SIGNATURE_SIZE])
