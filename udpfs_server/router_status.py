"""Router status protocol -- encoding only, no sockets.

See docs/ROUTER-STATUS.md for the wire format and the reasoning. The short
version: a status query is a DISCOVERY-shaped packet whose service ID is not
UDPFS, sent to the discovery port that is already open. Every server shipped
before this feature -- this one included, and udpfsd upstream -- drops such a
packet on the service-ID guard it already has, so the query is invisible to
them and a client simply reports "unknown". Nothing regresses, no new port is
opened, and the six-byte DISCOVERY/INFORM exchange the console depends on is
not touched by one byte.

Kept free of I/O so it can be tested against the Go implementation's bytes
without starting a server. The two must agree exactly; that is the point of
writing the format down.
"""

import struct

# Claimed by this project, adjacent to UDPRDMA_SVC_UDPFS (0xF5F5) and the
# default port (0xF5F6). That is upstream's numbering range, which is why
# parse_status_reply validates the echoed service ID and the version byte
# rather than trusting that any reply to this query is a status reply.
SVC_STATUS = 0xF5F7

# Identifies this protocol independently of the service ID.
#
# The number alone is not enough. 0xF5F5 is UDPFS and 0xF5F6 is the default
# port, so upstream allocates consecutively and 0xF5F7 is the next one it would
# reach for. Validating a reply protects our client from that; it does nothing
# about the other direction. Without this tag a server here would answer ANY
# discovery packet carrying 0xF5F7 -- including a future udpfsd client asking
# about something else. Requiring the magic means this server can only answer a
# question it actually understands.
#
# Safe to append: a longer packet whose service ID a server does not recognise
# is still dropped on the guard it already has, verified against both
# implementations before this was added.
MAGIC = b"PS2S"

# Payload version. A reply's first ten bytes -- header, service ID, magic,
# version -- are frozen across versions, being what a client needs to decide
# whether the rest is addressed to it and whether it understands the layout.
VERSION = 1

# Exact length of a query.
QUERY_LEN = 10

# Length of a reply before the variable-length name.
FIXED_LEN = 19

# Packet types, repeated here rather than imported so this module stays
# free of the server it describes.
_PKT_DISCOVERY = 0
_PKT_INFORM = 1

STATE_STARTING = 0
STATE_READY = 1
# A transfer is in flight. This is the field the whole protocol exists for: a
# board wired to console power must not be cut mid-write.
STATE_BUSY = 2
STATE_DEGRADED = 3
STATE_STOPPING = 4

STATE_NAMES = {
    STATE_STARTING: "starting",
    STATE_READY: "ready",
    STATE_BUSY: "busy",
    STATE_DEGRADED: "degraded",
    STATE_STOPPING: "stopping",
}

FLAG_UDPFS = 1 << 0
FLAG_UDPBD = 1 << 1
FLAG_SMB = 1 << 2
FLAG_READ_ONLY = 1 << 3
FLAG_DECOMPRESSES = 1 << 4


def _pack_header(packet_type, sequence):
    """The shared UDPRDMA header: type in the low nibble, 12-bit sequence."""
    return struct.pack("<H", (packet_type & 0xF) | ((sequence & 0xFFF) << 4))


def _unpack_header(data):
    (v,) = struct.unpack_from("<H", data, 0)
    return v & 0xF, (v >> 4) & 0xFFF


def is_status_query(data):
    """True if a received datagram is a status query.

    Must be checked before the UDPFS service-ID guard, and before anything
    that creates a session: a launcher polling once a second has to be
    invisible to the console being served.
    """
    if len(data) < QUERY_LEN:
        return False
    try:
        packet_type, _ = _unpack_header(data)
        (service_id,) = struct.unpack_from("<H", data, 2)
    except struct.error:
        return False
    # The magic, not just the number. See MAGIC.
    return (packet_type == _PKT_DISCOVERY
            and service_id == SVC_STATUS
            and data[6:10] == MAGIC)


def build_status_query():
    """The query, for a client or a test."""
    return _pack_header(_PKT_DISCOVERY, 0) + struct.pack("<HH", SVC_STATUS, 0) + MAGIC


def build_status_reply(state, flags, sessions, uptime_seconds, name=""):
    """Encode a reply.

    The name is truncated rather than rejected: a display string is never a
    reason to fail a health check, and the length field caps it at 255 bytes.
    """
    encoded = name.encode("utf-8", "replace")[:255]
    return (
        _pack_header(_PKT_INFORM, 0)
        + struct.pack("<H", SVC_STATUS)
        + MAGIC
        + struct.pack(
            "<BBHHIB",
            VERSION,
            state & 0xFF,
            flags & 0xFFFF,
            min(int(sessions), 0xFFFF),
            min(max(int(uptime_seconds), 0), 0xFFFFFFFF),
            len(encoded),
        )
        + encoded
    )


def parse_status_reply(data):
    """Decode a reply, or return None if it is not one.

    Rejects rather than guesses on every axis, because this runs against a
    service ID in a range this project does not own: a foreign reply must not
    be mistaken for a healthy server.
    """
    if len(data) < FIXED_LEN:
        return None
    try:
        packet_type, _ = _unpack_header(data)
        (service_id,) = struct.unpack_from("<H", data, 2)
        version, state, flags, sessions, uptime, name_len = struct.unpack_from(
            "<BBHHIB", data, 8)
    except struct.error:
        return None
    if packet_type != _PKT_INFORM or service_id != SVC_STATUS:
        return None
    if data[4:8] != MAGIC:
        return None
    if version != VERSION:
        # Not a guess. A client that does not know the version must report the
        # server as unknown rather than read fields that may have moved.
        return None
    name = ""
    if name_len:
        if len(data) < FIXED_LEN + name_len:
            return None
        name = data[FIXED_LEN:FIXED_LEN + name_len].decode("utf-8", "replace")
    if state > STATE_STOPPING:
        # An unrecognised state degrades rather than passing through. A client
        # taught only these five must not read a future value as benign.
        state = STATE_DEGRADED
    return {
        "version": version,
        "state": state,
        "state_name": STATE_NAMES.get(state, "degraded"),
        "flags": flags,
        "sessions": sessions,
        "uptime": uptime,
        "name": name,
    }
