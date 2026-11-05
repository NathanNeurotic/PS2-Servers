"""The SPNEGO/NTLMSSP exchange a real SMB2 client insists on.

smb2_auth answers "do these credentials prove the password". This module is
everything between that and the wire: the three NTLMSSP messages, and the DER
wrapper SPNEGO puts around them. It is separate because it is pure encoding --
no files, no sockets, no policy -- and because getting it wrong produces a
client that disconnects during SESSION_SETUP with nothing in any log.

SMB1 in this repo has no authentication at all: it advertises SecurityMode 0 and
accepts every session setup without reading the password bytes. That is fine for
a console on a LAN and it is why OPL works. It is not an option here, because
the clients this exists for -- Windows Explorer above all -- will not talk to a
server that cannot do NTLMSSP.
"""

import hmac
import struct
from hashlib import md5

# Flat, not relative. The launcher loads a server by file path with its own
# directory on sys.path (launcher/serve.py), so this package is never imported
# as a package -- a relative import here works in a source checkout and fails in
# the packaged build, which is the worst place to find out.
from smb2_auth import AuthError
import smb2_crypto

NTLMSSP_SIGNATURE = b"NTLMSSP\x00"
NTLM_NEGOTIATE = 1
NTLM_CHALLENGE = 2
NTLM_AUTHENTICATE = 3

# Only the flags that change what the other side does. UNICODE and NTLM are what
# make it an NTLMv2 exchange at all; TARGET_INFO is what makes the client send
# the NTLMv2 blob rather than the old NTLMv1 response, and without
# EXTENDED_SESSIONSECURITY a client may fall back to something weaker.
NEGOTIATE_UNICODE = 0x00000001
REQUEST_TARGET = 0x00000004
NEGOTIATE_SIGN = 0x00000010
NEGOTIATE_NTLM = 0x00000200
NEGOTIATE_ALWAYS_SIGN = 0x00008000
NEGOTIATE_EXTENDED_SESSIONSECURITY = 0x00080000
NEGOTIATE_TARGET_INFO = 0x00800000
NEGOTIATE_VERSION = 0x02000000
NEGOTIATE_128 = 0x20000000
NEGOTIATE_KEY_EXCH = 0x40000000
NEGOTIATE_56 = 0x80000000

CHALLENGE_FLAGS = (NEGOTIATE_UNICODE | REQUEST_TARGET | NEGOTIATE_NTLM
                   | NEGOTIATE_ALWAYS_SIGN | NEGOTIATE_EXTENDED_SESSIONSECURITY
                   | NEGOTIATE_TARGET_INFO | NEGOTIATE_128 | NEGOTIATE_56)

# Flags the server agrees to only when the client asked for them. SIGN and
# KEY_EXCH are what a Windows client sends when it intends to sign the SMB2
# session; echoing them tells it to wrap a fresh session key in the
# AUTHENTICATE message (MS-NLMP 3.1.5.1.2). A client that never offered them
# -- Neutrino's usmb2 -- sees exactly the challenge it always did.
ECHOED_FLAGS = NEGOTIATE_SIGN | NEGOTIATE_KEY_EXCH | NEGOTIATE_VERSION

MSV_AV_EOL = 0x0000
MSV_AV_NB_COMPUTER_NAME = 0x0001
MSV_AV_NB_DOMAIN_NAME = 0x0002
MSV_AV_DNS_COMPUTER_NAME = 0x0003
MSV_AV_DNS_DOMAIN_NAME = 0x0004
MSV_AV_FLAGS = 0x0006
AV_FLAG_MIC_PRESENT = 0x00000002

# Where the MIC sits in an AUTHENTICATE message that carries one: after the
# 64 bytes of fixed fields and the 8-byte Version (MS-NLMP 2.2.1.3).
MIC_OFFSET = 72
MIC_SIZE = 16

SPNEGO_OID = bytes([0x06, 0x06, 0x2b, 0x06, 0x01, 0x05, 0x05, 0x02])
NTLMSSP_OID = bytes([0x06, 0x0a, 0x2b, 0x06, 0x01, 0x04, 0x01,
                     0x82, 0x37, 0x02, 0x02, 0x0a])

ACCEPT_COMPLETED = 0
ACCEPT_INCOMPLETE = 1
REJECT = 2


# --- the small amount of DER this needs ----------------------------------- #

def _der_len(n):
    if n < 0x80:
        return bytes([n])
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(raw)]) + raw


def _tlv(tag, value):
    return bytes([tag]) + _der_len(len(value)) + value


def neg_token_init():
    """What the server puts in its NEGOTIATE response: "I do NTLMSSP".

    A client that gets an empty security buffer here may still work by sending
    raw NTLMSSP, and this server accepts that too (see extract_ntlm), but
    advertising properly is what makes a strict client choose the mechanism
    instead of giving up.
    """
    mech_list = _tlv(0x30, NTLMSSP_OID)
    token = _tlv(0x30, _tlv(0xa0, mech_list))
    return _tlv(0x60, SPNEGO_OID + _tlv(0xa0, token))


def neg_token_resp(neg_state, response_token=None, with_mech=False):
    """The server's half of the SPNEGO round trip."""
    body = _tlv(0xa0, _tlv(0x0a, bytes([neg_state])))
    if with_mech:
        body += _tlv(0xa1, NTLMSSP_OID)
    if response_token is not None:
        body += _tlv(0xa2, _tlv(0x04, response_token))
    return _tlv(0xa1, _tlv(0x30, body))


def extract_ntlm(blob):
    """The NTLMSSP message inside a security buffer, or None.

    Found by its signature rather than by walking the DER. NTLMSSP messages are
    self-delimiting -- every variable field carries its own length and offset
    relative to the message start -- so anything after the token is harmless,
    and this accepts both a SPNEGO-wrapped token and a raw one without needing
    to know which it was given. A full ASN.1 parser here would be more code
    whose only job is to find an offset this already has.
    """
    if not blob:
        return None
    at = blob.find(NTLMSSP_SIGNATURE)
    if at < 0:
        return None
    return blob[at:]


def is_raw_ntlmssp(blob):
    """True when a security buffer is a bare NTLMSSP message, not SPNEGO.

    SPNEGO always starts with a DER tag (0x60 for the initial token, 0xa1 for a
    response), never with the NTLMSSP signature, so the first eight bytes
    decide it without parsing anything.
    """
    return bytes(blob[:len(NTLMSSP_SIGNATURE)]) == NTLMSSP_SIGNATURE


def message_type(token):
    if not token or len(token) < 12:
        return None
    return struct.unpack_from("<I", token, 8)[0]


def _field(token, offset):
    """A (Len, MaxLen, Offset) triple and the bytes it points at."""
    length, _maxlen, at = struct.unpack_from("<HHI", token, offset)
    if length == 0:
        return b""
    if at + length > len(token):
        raise AuthError("STATUS_INVALID_PARAMETER",
                        "NTLMSSP field runs past the end of the message")
    return token[at:at + length]


def _av_pair(kind, value):
    return struct.pack("<HH", kind, len(value)) + value


def negotiate_flags(token):
    """The NegotiateFlags a NEGOTIATE (type 1) message carries, or 0."""
    if len(token) < 16:
        return 0
    return struct.unpack_from("<I", token, 12)[0]


# Where each message type's (Len, MaxLen, Offset) fields sit, and the size of
# its fixed part without the optional Version and MIC.
_FIELD_OFFSETS = {NTLM_NEGOTIATE: ((16, 24), 32, 12),
                  NTLM_AUTHENTICATE: ((12, 20, 28, 36, 44, 52), 64, 60)}


def trim(token):
    """`token` cut to the length its own fields say the message has.

    extract_ntlm hands back everything from the signature to the end of the
    security buffer. Inside SPNEGO that can include DER that follows the
    message -- Windows puts a mechListMIC after its AUTHENTICATE -- and the
    NTLM MIC is computed over the message alone, so the exact end matters.
    """
    token = bytes(token)
    layout = _FIELD_OFFSETS.get(message_type(token))
    if layout is None:
        return token
    field_offsets, fixed, flags_at = layout
    if len(token) < fixed:
        return token
    end = fixed
    if struct.unpack_from("<I", token, flags_at)[0] & NEGOTIATE_VERSION:
        end += 8
    for off in field_offsets:
        length, _maxlen, at = struct.unpack_from("<HHI", token, off)
        if length:
            end = max(end, at + length)
    return token[:min(end, len(token))]


def build_challenge(challenge, target_name="WORKGROUP", computer="PS2SERVERS",
                    compact=False, client_flags=0):
    """The CHALLENGE (type 2) message.

    TargetInfo is not decoration: an NTLMv2 client folds these very bytes into
    the blob it signs, so a server that omits them gets a proof it cannot
    reproduce and every login fails with the password correct.

    compact keeps only the NetBIOS domain and computer pairs, the two MS-NLMP
    says a server MUST send. It exists for tiny clients that build their
    AUTHENTICATE in a fixed buffer -- see h_session_setup.

    client_flags is what the client's NEGOTIATE offered; of it, only
    ECHOED_FLAGS come back.
    """
    flags = CHALLENGE_FLAGS | (client_flags & ECHOED_FLAGS)
    target = target_name.encode("utf-16-le")
    info = (_av_pair(MSV_AV_NB_DOMAIN_NAME, target)
            + _av_pair(MSV_AV_NB_COMPUTER_NAME, computer.encode("utf-16-le")))
    if not compact:
        info += (_av_pair(MSV_AV_DNS_DOMAIN_NAME, target)
                 + _av_pair(MSV_AV_DNS_COMPUTER_NAME, computer.encode("utf-16-le")))
    info += _av_pair(MSV_AV_EOL, b"")

    fixed = 56                      # signature..version, before the payload
    target_at = fixed
    info_at = target_at + len(target)
    return (NTLMSSP_SIGNATURE
            + struct.pack("<I", NTLM_CHALLENGE)
            + struct.pack("<HHI", len(target), len(target), target_at)
            + struct.pack("<I", flags)
            + challenge
            + b"\x00" * 8                                   # Reserved
            + struct.pack("<HHI", len(info), len(info), info_at)
            + struct.pack("<BBHBBBB", 6, 1, 7601, 0, 0, 0, 15)   # Version
            + target + info)


class Credentials:
    """What an AUTHENTICATE message claims, before anything is checked.

    Besides the proof itself it keeps what key agreement needs: the flags the
    client settled on, the RC4-wrapped session key it sent if KEY_EXCH was
    negotiated, the MIC over the three messages if it computed one, and the
    whole message, because the MIC is checked over that.
    """

    __slots__ = ("user", "domain", "workstation", "nt_response", "lm_response",
                 "flags", "encrypted_session_key", "mic", "raw")

    def __init__(self, user, domain, workstation, nt_response, lm_response,
                 flags=0, encrypted_session_key=b"", mic=None, raw=b""):
        self.user = user
        self.domain = domain
        self.workstation = workstation
        self.nt_response = nt_response
        self.lm_response = lm_response
        self.flags = flags
        self.encrypted_session_key = encrypted_session_key
        self.mic = mic
        self.raw = raw

    @property
    def is_anonymous(self):
        """A null session: no user and no proof offered.

        Worth naming rather than letting it fall through the password check,
        because an empty NT response against a real account is a different
        thing from a client deliberately asking for anonymous access, and only
        one of them should ever be allowed anywhere.
        """
        return not self.user and len(self.nt_response) == 0


def av_pairs(nt_response):
    """The AV_PAIR list inside an NTLMv2 response, as {id: value}.

    The response is the 16-byte proof followed by the NTLMv2_CLIENT_CHALLENGE
    (MS-NLMP 2.2.2.7): 28 bytes of fixed fields, then the pairs the client
    copied from the server's TargetInfo plus any it added. Malformed input
    yields what could be read; the proof check decides whether it mattered.
    """
    pairs = {}
    at = 16 + 28
    while at + 4 <= len(nt_response):
        kind, length = struct.unpack_from("<HH", nt_response, at)
        at += 4
        if kind == MSV_AV_EOL:
            break
        pairs[kind] = bytes(nt_response[at:at + length])
        at += length
    return pairs


def parse_authenticate(token):
    """Pull the claim out of an AUTHENTICATE (type 3) message."""
    if len(token) < 64:
        raise AuthError("STATUS_INVALID_PARAMETER", "AUTHENTICATE message too short")
    token = trim(token)
    lm = _field(token, 12)
    nt = _field(token, 20)
    domain = _field(token, 28).decode("utf-16-le", "replace")
    user = _field(token, 36).decode("utf-16-le", "replace")
    workstation = _field(token, 44).decode("utf-16-le", "replace")
    encrypted_key = _field(token, 52)
    flags = struct.unpack_from("<I", token, 60)[0]

    # The MIC field exists only in messages laid out with room for it: the
    # client says it filled one in through MsvAvFlags in its own blob, and the
    # payload must start at or after where the MIC ends.
    mic = None
    av_flags = av_pairs(nt).get(MSV_AV_FLAGS, b"")
    if len(av_flags) >= 4 and struct.unpack_from("<I", av_flags)[0] & AV_FLAG_MIC_PRESENT:
        payload_start = min((struct.unpack_from("<I", token, off + 4)[0]
                             for off in (12, 20, 28, 36, 44, 52)
                             if struct.unpack_from("<H", token, off)[0]),
                            default=len(token))
        if len(token) < MIC_OFFSET + MIC_SIZE or payload_start < MIC_OFFSET + MIC_SIZE:
            raise AuthError("STATUS_INVALID_PARAMETER",
                            "AUTHENTICATE claims a MIC it has no room for")
        mic = token[MIC_OFFSET:MIC_OFFSET + MIC_SIZE]
    return Credentials(user, domain, workstation, nt, lm,
                       flags=flags, encrypted_session_key=encrypted_key,
                       mic=mic, raw=token)


def exported_session_key(session_base_key, creds):
    """The key both sides sign SMB2 with, from NTLM's SessionBaseKey.

    With KEY_EXCH negotiated the client chose a random session key and sent it
    RC4-wrapped under the KeyExchangeKey, which for NTLMv2 is the
    SessionBaseKey itself (MS-NLMP 3.4.5.1). Without it, the base key is the
    session key.
    """
    if (creds.flags & NEGOTIATE_KEY_EXCH
            and len(creds.encrypted_session_key) == 16):
        return smb2_crypto.rc4(session_base_key, creds.encrypted_session_key)
    return session_base_key


def verify_mic(exported_key, negotiate, challenge, creds):
    """Check the AUTHENTICATE's MIC: HMAC-MD5 over the three messages.

    The MIC binds the whole exchange -- which flags were offered, which
    challenge was answered -- so a server that negotiated signing is expected
    to check it (MS-NLMP 3.2.5.1.2). The AUTHENTICATE is hashed with its own
    MIC field zeroed.
    """
    if creds.mic is None:
        return True
    if not negotiate:
        return False
    auth = (creds.raw[:MIC_OFFSET] + b"\x00" * MIC_SIZE
            + creds.raw[MIC_OFFSET + MIC_SIZE:])
    expected = hmac.new(exported_key, bytes(negotiate) + bytes(challenge) + auth,
                        md5).digest()
    return hmac.compare_digest(expected, creds.mic)
