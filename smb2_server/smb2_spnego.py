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

import struct

# Flat, not relative. The launcher loads a server by file path with its own
# directory on sys.path (launcher/serve.py), so this package is never imported
# as a package -- a relative import here works in a source checkout and fails in
# the packaged build, which is the worst place to find out.
from smb2_auth import AuthError

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
NEGOTIATE_NTLM = 0x00000200
NEGOTIATE_ALWAYS_SIGN = 0x00008000
NEGOTIATE_EXTENDED_SESSIONSECURITY = 0x00080000
NEGOTIATE_TARGET_INFO = 0x00800000
NEGOTIATE_128 = 0x20000000
NEGOTIATE_56 = 0x80000000

CHALLENGE_FLAGS = (NEGOTIATE_UNICODE | REQUEST_TARGET | NEGOTIATE_NTLM
                   | NEGOTIATE_ALWAYS_SIGN | NEGOTIATE_EXTENDED_SESSIONSECURITY
                   | NEGOTIATE_TARGET_INFO | NEGOTIATE_128 | NEGOTIATE_56)

MSV_AV_EOL = 0x0000
MSV_AV_NB_COMPUTER_NAME = 0x0001
MSV_AV_NB_DOMAIN_NAME = 0x0002
MSV_AV_DNS_COMPUTER_NAME = 0x0003
MSV_AV_DNS_DOMAIN_NAME = 0x0004

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


def build_challenge(challenge, target_name="WORKGROUP", computer="PS2SERVERS"):
    """The CHALLENGE (type 2) message.

    TargetInfo is not decoration: an NTLMv2 client folds these very bytes into
    the blob it signs, so a server that omits them gets a proof it cannot
    reproduce and every login fails with the password correct.
    """
    target = target_name.encode("utf-16-le")
    info = (_av_pair(MSV_AV_NB_DOMAIN_NAME, target)
            + _av_pair(MSV_AV_NB_COMPUTER_NAME, computer.encode("utf-16-le"))
            + _av_pair(MSV_AV_DNS_DOMAIN_NAME, target)
            + _av_pair(MSV_AV_DNS_COMPUTER_NAME, computer.encode("utf-16-le"))
            + _av_pair(MSV_AV_EOL, b""))

    fixed = 56                      # signature..version, before the payload
    target_at = fixed
    info_at = target_at + len(target)
    return (NTLMSSP_SIGNATURE
            + struct.pack("<I", NTLM_CHALLENGE)
            + struct.pack("<HHI", len(target), len(target), target_at)
            + struct.pack("<I", CHALLENGE_FLAGS)
            + challenge
            + b"\x00" * 8                                   # Reserved
            + struct.pack("<HHI", len(info), len(info), info_at)
            + struct.pack("<BBHBBBB", 6, 1, 7601, 0, 0, 0, 15)   # Version
            + target + info)


class Credentials:
    """What an AUTHENTICATE message claims, before anything is checked."""

    __slots__ = ("user", "domain", "workstation", "nt_response", "lm_response")

    def __init__(self, user, domain, workstation, nt_response, lm_response):
        self.user = user
        self.domain = domain
        self.workstation = workstation
        self.nt_response = nt_response
        self.lm_response = lm_response

    @property
    def is_anonymous(self):
        """A null session: no user and no proof offered.

        Worth naming rather than letting it fall through the password check,
        because an empty NT response against a real account is a different
        thing from a client deliberately asking for anonymous access, and only
        one of them should ever be allowed anywhere.
        """
        return not self.user and len(self.nt_response) == 0


def parse_authenticate(token):
    """Pull the claim out of an AUTHENTICATE (type 3) message."""
    if len(token) < 64:
        raise AuthError("STATUS_INVALID_PARAMETER", "AUTHENTICATE message too short")
    lm = _field(token, 12)
    nt = _field(token, 20)
    domain = _field(token, 28).decode("utf-16-le", "replace")
    user = _field(token, 36).decode("utf-16-le", "replace")
    workstation = _field(token, 44).decode("utf-16-le", "replace")
    return Credentials(user, domain, workstation, nt, lm)
