"""NTLMv2 credential checking for the SMB2/SMB3 server.

Kept in its own module for the same reason as the path guard: this is where a
mistake hands a stranger the share, and it should be readable and testable
without a socket or a protocol in the way. Nothing here speaks SMB. It takes
bytes that arrived in a SESSION_SETUP and answers one question -- do these
credentials prove knowledge of the password -- with no I/O in between.

The default is that a password is required. "Open" is a real mode, because a
PS2 on a LAN behind a router is the normal deployment and the older SMBv1
server here is guest-only, so refusing to offer it would just push people back
to the weaker server. But it is reachable only through Authenticator.open(),
never by passing an empty password or a falsy flag to the normal constructor.
A misconfiguration should fail closed and lock the operator out, rather than
open and let everyone in.

MD4 is implemented here rather than taken from hashlib. NTLM's NT hash is
MD4(UTF-16LE(password)), and OpenSSL 3 moved MD4 to the legacy provider, so
hashlib.new("md4") raises ValueError on a normal modern Python -- confirmed on
the Python this repo builds against:

    >>> hashlib.new("md4")
    ValueError: unsupported hash type md4

It is not conditionally used when present. One implementation that the tests
always exercise is worth more than a fast path that only runs on the machines
where nobody is looking, and this runs once per session setup, where the cost
is not measurable.

MD4 and MD5 are both broken as collision-resistant hashes and neither is used
here as one. NTLMv2 relies on them as HMAC keys and PRFs, which the published
collision attacks do not reach. NTLMv2 is what an SMB client will offer, so it
is what the server has to check; the protocol's real weaknesses -- an offline
guess against a captured exchange, and no channel binding -- are properties of
NTLM itself and are not fixable from this side. Hence the password advice in
the docstring for Authenticator.
"""

import hmac
import secrets
import struct
from hashlib import md5

# NTLMv2 fixes the response header at 0x01 0x01 followed by six zero bytes.
# A client that sends anything else is speaking NTLMv1 or is confused; either
# way this server does not implement it, and guessing would be worse.
_NTLMV2_HEADER = b"\x01\x01\x00\x00\x00\x00\x00\x00"

# NTProofStr, the HMAC-MD5 that opens an NTLMv2 response.
_PROOF_LEN = 16

# Windows counts 100-nanosecond ticks from 1601-01-01. Only used to expose the
# client's claimed time; see Authenticator.verify for why it is not enforced.
_FILETIME_EPOCH_DELTA = 11644473600


class AuthError(Exception):
    """Raised when credentials are refused. Carries an NT status name.

    The status travels with the error so the session-setup handler does not
    have to work out which of LOGON_FAILURE / ACCESS_DENIED to send, and so the
    reason is recorded once, where it was decided.

    The `detail` is for the operator's log. It is deliberately never the thing
    that goes on the wire: a client is told only STATUS_LOGON_FAILURE, so it
    cannot learn from the reply whether it got the user name right.
    """

    def __init__(self, status, detail):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def md4(data):
    """MD4 as specified in RFC 1320.

    Present because hashlib no longer provides it (see the module docstring).
    Used only to derive the NT hash, which is what NTLM defines the password to
    be; it is not relied on for collision resistance anywhere in this module.
    """
    a, b, c, d = 0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476
    mask = 0xFFFFFFFF

    def rol(x, n):
        x &= mask
        return ((x << n) | (x >> (32 - n))) & mask

    # Pad to a multiple of 64 bytes: 0x80, zeros, then the length in bits as a
    # little-endian 64-bit count. The length is of the *original* message.
    bit_len = (len(data) * 8) & 0xFFFFFFFFFFFFFFFF
    data = data + b"\x80"
    data += b"\x00" * ((56 - len(data) % 64) % 64)
    data += struct.pack("<Q", bit_len)

    for offset in range(0, len(data), 64):
        x = struct.unpack("<16I", data[offset:offset + 64])
        aa, bb, cc, dd = a, b, c, d

        # Round 1: F = (x AND y) OR (NOT x AND z)
        for i in range(0, 16, 4):
            a = rol(a + (((b & c) | (~b & d)) & mask) + x[i], 3)
            d = rol(d + (((a & b) | (~a & c)) & mask) + x[i + 1], 7)
            c = rol(c + (((d & a) | (~d & b)) & mask) + x[i + 2], 11)
            b = rol(b + (((c & d) | (~c & a)) & mask) + x[i + 3], 19)

        # Round 2: G = majority. Constant is the square root of 2, per RFC 1320.
        for i in range(4):
            a = rol(a + (((b & c) | (b & d) | (c & d)) & mask) + x[i] + 0x5A827999, 3)
            d = rol(d + (((a & b) | (a & c) | (b & c)) & mask) + x[i + 4] + 0x5A827999, 5)
            c = rol(c + (((d & a) | (d & b) | (a & b)) & mask) + x[i + 8] + 0x5A827999, 9)
            b = rol(b + (((c & d) | (c & a) | (d & a)) & mask) + x[i + 12] + 0x5A827999, 13)

        # Round 3: H = XOR. Constant is the square root of 3.
        for i in (0, 2, 1, 3):
            a = rol(a + ((b ^ c ^ d) & mask) + x[i] + 0x6ED9EBA1, 3)
            d = rol(d + ((a ^ b ^ c) & mask) + x[i + 8] + 0x6ED9EBA1, 9)
            c = rol(c + ((d ^ a ^ b) & mask) + x[i + 4] + 0x6ED9EBA1, 11)
            b = rol(b + ((c ^ d ^ a) & mask) + x[i + 12] + 0x6ED9EBA1, 15)

        a = (a + aa) & mask
        b = (b + bb) & mask
        c = (c + cc) & mask
        d = (d + dd) & mask

    return struct.pack("<4I", a, b, c, d)


def nt_hash(password):
    """The NT hash: MD4 of the password in UTF-16LE.

    Note what this is not. It is unsalted and uncounted, so it is a password
    equivalent -- anyone who reads it can authenticate without ever knowing the
    password. Whatever stores these is as sensitive as a password file, which
    is why Authenticator keeps them in memory only and the caller is expected
    to hold the password, not a hash file.
    """
    return md4(password.encode("utf-16-le"))


def ntowfv2(password, user, domain=""):
    """NTOWFv2: the per-identity key everything else in NTLMv2 is derived from.

    MS-NLMP defines this as HMAC_MD5(NT hash, UNICODE(Uppercase(User) + Domain))
    -- the user name is uppercased, the domain is used exactly as given. Getting
    that asymmetry backwards produces a key that is wrong for every password, so
    it is pinned by a published test vector in the tests rather than by reading.

    Uppercasing uses Python's str.upper, which is Unicode-aware and so can
    disagree with Windows for exotic scripts. For the ASCII user names this
    server is configured with, the two agree.
    """
    key = nt_hash(password)
    identity = (user.upper() + domain).encode("utf-16-le")
    return hmac.new(key, identity, md5).digest()


def ntlmv2_proof(ntowf, server_challenge, blob):
    """NTProofStr: HMAC-MD5 over the server challenge and the client's blob.

    The blob is the client's own contribution -- its nonce, its timestamp, and
    the target info it echoed back. Both sides feed the same bytes in, so the
    proof only matches if the client held the password AND was answering this
    server's challenge.
    """
    return hmac.new(ntowf, server_challenge + blob, md5).digest()


def lmv2_response(ntowf, server_challenge, client_challenge):
    """The LMv2 response: 16-byte HMAC followed by the 8-byte client nonce.

    Provided for completeness because a client may send it alongside the NTv2
    response. It is not accepted as proof on its own: it commits to nothing but
    the two nonces, so it carries strictly less than the NTv2 response does.
    """
    mac = hmac.new(ntowf, server_challenge + client_challenge, md5).digest()
    return mac + client_challenge


def session_base_key(ntowf, proof):
    """SessionBaseKey, the root of SMB2 signing and SMB3 encryption keys.

    Returned from a successful verify so the session layer never recomputes it
    from credentials, and so the key material has exactly one origin.
    """
    return hmac.new(ntowf, proof, md5).digest()


def server_challenge():
    """A fresh 8-byte challenge for one session setup.

    secrets, not random: the module-level Mersenne Twister is predictable from
    its own output, and a predictable challenge lets an attacker who once saw a
    valid exchange precompute a reply. This is the only thing standing between
    a replayed capture and a session, so it must come from the OS.
    """
    return secrets.token_bytes(8)


def parse_ntlmv2_response(data):
    """Split an NTv2 response into (proof, blob), or raise AuthError.

    Structure is NTProofStr(16) followed by the blob, whose first 8 bytes are
    the fixed NTLMv2 header. The header is checked because an NTLMv1 response
    is 24 bytes with no header at all, and it should be refused as unsupported
    rather than silently sliced into a proof and a nonsense blob.
    """
    if not isinstance(data, (bytes, bytearray)):
        raise AuthError("STATUS_LOGON_FAILURE", "response is not bytes")
    data = bytes(data)
    if len(data) < _PROOF_LEN + len(_NTLMV2_HEADER):
        raise AuthError("STATUS_LOGON_FAILURE",
                        "response too short to be NTLMv2 (%d bytes)" % len(data))
    proof, blob = data[:_PROOF_LEN], data[_PROOF_LEN:]
    if blob[:len(_NTLMV2_HEADER)] != _NTLMV2_HEADER:
        raise AuthError("STATUS_LOGON_FAILURE",
                        "not an NTLMv2 response (bad blob header); NTLMv1 is not served")
    return proof, blob


def blob_timestamp(blob):
    """The client's claimed time from an NTLMv2 blob, as a POSIX timestamp.

    Exposed for logging only. See Authenticator.verify for why it is not used
    to reject anything.
    """
    if len(blob) < 16:
        return None
    (filetime,) = struct.unpack("<Q", blob[8:16])
    if filetime == 0:
        return None
    return filetime / 10_000_000.0 - _FILETIME_EPOCH_DELTA


class Authenticator:
    """Checks NTLMv2 credentials for a set of configured users.

    Construct it with users to require a password. Use Authenticator.open() for
    the no-password mode, which is separate precisely so that it cannot be
    entered by accident -- an empty password dict raises rather than quietly
    admitting everyone.

    NTLM allows an offline guess against any exchange an attacker can capture,
    and this server cannot prevent that. So the password matters more here than
    it would behind a modern KDC: a long random one is fine, a dictionary word
    is not.
    """

    def __init__(self, users, domain="WORKGROUP"):
        if not users:
            raise ValueError(
                "no users configured; use Authenticator.open() if the share is "
                "meant to need no password")
        # Keyed by the casefolded name: SMB user names are case-insensitive, and
        # matching exactly would let "Ripto" fail where "ripto" worked, which
        # reads as a broken server rather than as a rejected login.
        self._users = {u.casefold(): p for u, p in users.items()}
        if len(self._users) != len(users):
            raise ValueError("user names differ only by case")
        self.domain = domain
        self.is_open = False

    @classmethod
    def open(cls, domain="WORKGROUP"):
        """The no-password mode, named so it cannot be reached by accident.

        Every session setup succeeds. Callers that log should say so plainly at
        startup; an operator who did not mean to do this should be able to tell
        from the log, not from a stranger's file appearing in the share.
        """
        self = cls.__new__(cls)
        self._users = {}
        self.domain = domain
        self.is_open = True
        return self

    def verify(self, user, response, challenge, domain=None):
        """Check an NTLMv2 response. Returns the SessionBaseKey, or raises.

        Returning the key rather than True means a caller cannot use a bare
        truth value to decide the session is authenticated and then go and
        derive keys from somewhere else.

        In open mode it returns None, and that is not a rejection -- failure is
        always an AuthError, never a falsy return. There is genuinely no key,
        because nothing was proved and there is no shared secret to derive one
        from, so a session authenticated this way cannot be signed or
        encrypted. A caller must branch on is_open when deciding what to
        negotiate, not on whether a key came back.

        The blob carries a client timestamp, and it is deliberately not checked
        for freshness. A PS2 has no battery-backed clock that survives being
        unplugged, so a console will routinely present a time that is years out
        and would fail any sane window. Replay is already answered by the
        challenge: it is fresh per session setup and comes from the OS CSPRNG,
        so a captured response is worthless against the next one.
        """
        if self.is_open:
            return None

        proof, blob = parse_ntlmv2_response(response)

        password = self._users.get((user or "").casefold())
        if password is None:
            # Do the same work for an unknown user as for a known one, and fail
            # with the same status. Returning early here would make "no such
            # user" measurably faster than "wrong password", which turns the
            # server into an oracle for which names are worth attacking.
            expected = ntlmv2_proof(
                ntowfv2(secrets.token_hex(32), user or "", domain or self.domain),
                challenge, blob)
            hmac.compare_digest(expected, proof)
            raise AuthError("STATUS_LOGON_FAILURE", "unknown user %r" % (user,))

        ntowf = ntowfv2(password, user, domain if domain is not None else self.domain)
        expected = ntlmv2_proof(ntowf, challenge, blob)

        # compare_digest, not ==. A byte-at-a-time comparison leaks how much of
        # the proof was right through its timing, which is enough to recover it
        # one byte at a time without ever knowing the password.
        if not hmac.compare_digest(expected, proof):
            raise AuthError("STATUS_LOGON_FAILURE", "bad password for %r" % (user,))

        return session_base_key(ntowf, proof)
