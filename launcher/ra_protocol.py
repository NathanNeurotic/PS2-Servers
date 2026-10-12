"""Owned RA15 parsing and assembly; not yet connected to the active engine.

Wire reference: hacan359/Open-PS2-Loader 531aad4584d65d1779b744739b81402150c7669c,
modules/network/raudp/raudp.c and protocol/ra_watch.h. This implementation
retains complete frames only, with explicit session identity and bounded memory.
It contains no socket, account, evaluator, or unlock-submission side effects.
"""
from dataclasses import dataclass
import ipaddress
import re
import time


MAX_DATAGRAM = 1472
MAX_PARTS = 9
MAX_VALUES = (1472 - 191) * MAX_PARTS
SEQUENCE_MODULUS = 1000000
_WIDTHS = {b"sq": 6, b"vb": 4, b"n": 4, b"pt": 1, b"np": 1}


@dataclass(frozen=True)
class ConsoleRequest:
    """Base discovery/identify/chunk request with an explicit reply endpoint."""

    kind: str
    address: str
    port: int
    image_hash: str = ""
    serial: str = ""
    index: int = 0


def parse_request(packet):
    """Decode the full requests emitted by the pinned console, without I/O.

    Peer authorization and NAT reply routing belong to the future receiver.
    This parser does not send traffic to the advertised address.
    """
    if not isinstance(packet, bytes) or not 1 <= len(packet) <= 256:
        raise ValueError("Invalid request size or type")
    try:
        fields = packet.decode("ascii").split()
    except UnicodeDecodeError as error:
        raise ValueError("Non-ASCII request") from error
    if not fields or fields[0] not in ("RAP1", "RAQ1", "RAG1"):
        raise ValueError("Unknown request")
    kind = fields[0]
    if len(fields) != (3 if kind == "RAP1" else 5):
        raise ValueError("Invalid request field count")
    try:
        address = str(ipaddress.IPv4Address(fields[-2]))
        if not fields[-1].isascii() or not fields[-1].isdigit():
            raise ValueError("Invalid port")
        port = int(fields[-1])
        if not 1 <= port <= 65535:
            raise ValueError("Invalid port")
    except (ipaddress.AddressValueError, ValueError) as error:
        raise ValueError("Invalid reply endpoint") from error
    if kind == "RAP1":
        return ConsoleRequest(kind, address, port)
    image_hash = fields[1]
    if not re.fullmatch(r"[a-fA-F0-9]{32}", image_hash):
        raise ValueError("Invalid image hash")
    if kind == "RAQ1":
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,15}", fields[2]):
            raise ValueError("Invalid game serial")
        return ConsoleRequest(kind, address, port, image_hash.lower(), fields[2])
    if not re.fullmatch(r"[0-9]{1,5}", fields[2]):
        raise ValueError("Invalid chunk index")
    return ConsoleRequest(kind, address, port, image_hash.lower(), index=int(fields[2]))


def pad_reply(body):
    """Space-pad base replies for the 128-minimum/64-aligned DMA path.

    The base client uses spaces; CADQ/CADA have a separate NUL-padding contract.
    """
    if not isinstance(body, bytes) or not 1 <= len(body) < 960:
        raise ValueError("Reply exceeds console receive buffer")
    return body + b" " * (max(128, ((len(body) + 63) // 64) * 64) - len(body))


def watch_chunk(watch_file, index):
    """Encode a bounded RAC1 chunk of a caller-validated watch-list file.

    This transfer codec does not derive or authorize addresses. The runtime
    must bind the file to its identified image/hash and achievement set.
    """
    if (not isinstance(watch_file, bytes) or not 1 <= len(watch_file) <= 17432
            or type(index) is not int or not 0 <= index < (len(watch_file) + 895) // 896):
        raise ValueError("Invalid watch file or chunk index")
    data = watch_file[index * 896:(index + 1) * 896]
    return pad_reply(f"RAC1 {index} {len(data)} ".encode("ascii") + data)


@dataclass(frozen=True)
class SnapshotPart:
    """Validated fragment, whose payload is isolated from the text header."""

    serial: str
    sequence: int
    count: int
    index: int
    parts: int
    values: bytes


def parse_snapshot(packet):
    """Parse one bounded RA15 datagram, raising ValueError on invalid input."""
    if not isinstance(packet, bytes) or not 1 <= len(packet) <= MAX_DATAGRAM:
        raise ValueError("Invalid datagram size or type")
    if not packet.startswith(b"RA15 "):
        raise ValueError("Not RA15 telemetry")
    marker = packet.find(b" id=", 0, 191)
    if marker < 0:
        raise ValueError("Missing final serial field")
    offset = marker + 4 + 15 + 1
    if offset > 191 or offset > len(packet) or packet[offset - 1:offset] != b" ":
        raise ValueError("Truncated or oversized telemetry header")
    serial_field = packet[marker + 4:offset - 1]
    if not re.fullmatch(rb"[A-Za-z0-9_.-]+~*", serial_field):
        raise ValueError("Invalid game serial")
    fields = {}
    for token in packet[5:marker].split(b" "):
        match = re.fullmatch(rb"([a-z]+)=([A-Za-z0-9+.-]+)", token)
        if not match or match[1] in fields:
            raise ValueError("Invalid or duplicate telemetry field")
        fields[match[1]] = match[2]
    numbers = {}
    for name, width in _WIDTHS.items():
        value = fields.get(name, b"")
        if len(value) != width or not value.isdigit():
            raise ValueError("Missing or invalid telemetry counter")
        numbers[name] = int(value)
    count, size = numbers[b"n"], numbers[b"vb"]
    index, parts = numbers[b"pt"], numbers[b"np"]
    if not 1 <= count <= 4096 or not 1 <= parts <= MAX_PARTS or index >= parts:
        raise ValueError("Invalid snapshot dimensions")
    if not 1 <= size <= MAX_VALUES or len(packet) - offset != size:
        raise ValueError("Telemetry payload length mismatch")
    return SnapshotPart(serial_field.rstrip(b"~").decode("ascii"),
                        numbers[b"sq"], count, index, parts, packet[offset:])


class SnapshotAssembler:
    """Assemble one explicit game/watch-list session without mixed-frame values.

    Caller owns peer identity and must reset or create an assembler on a new
    peer, game or watch list. A timeout permits the same console to restart its
    sequence. Direct-only and pointer-tail sizes are both accepted when supplied.
    """

    def __init__(self, serial, count, sizes, timeout=15.0, clock=time.monotonic):
        sizes = frozenset(sizes)
        if not serial or not 1 <= count <= 4096 or not sizes:
            raise ValueError("Invalid watch-list session")
        if any(type(size) is not int or not 1 <= size <= MAX_VALUES for size in sizes):
            raise ValueError("Invalid expected snapshot sizes")
        if timeout <= 0:
            raise ValueError("Invalid snapshot timeout")
        self.serial, self.count, self.sizes = serial, count, sizes
        self.timeout, self.clock = timeout, clock
        self.reset()

    def reset(self):
        """Forget in-flight and completed frames at a session boundary."""
        self.sequence = None
        self.parts = None
        self.fragments = {}
        self.blocked = False
        self.completed = False
        self.last_seen = None

    def feed(self, packet):
        """Return immutable values once per complete frame, otherwise None."""
        part = parse_snapshot(packet)
        if part.serial != self.serial or part.count != self.count:
            return None
        now = self.clock()
        if self.last_seen is not None and now - self.last_seen >= self.timeout:
            self.reset()
        if self.sequence is not None:
            distance = (part.sequence - self.sequence) % SEQUENCE_MODULUS
            if distance >= SEQUENCE_MODULUS // 2:
                return None  # delayed old frame; do not replace current assembly
        else:
            distance = 1
        if distance:
            self.sequence, self.parts = part.sequence, part.parts
            self.fragments = {}
            self.blocked = self.completed = False
        if self.blocked or self.completed:
            return None
        if part.parts != self.parts:
            self.blocked = True
            self.fragments.clear()
            return None
        previous = self.fragments.get(part.index)
        if previous is not None and previous != part.values:
            self.blocked = True
            self.fragments.clear()
            return None
        self.fragments[part.index] = part.values
        if sum(map(len, self.fragments.values())) > max(self.sizes):
            self.blocked = True
            self.fragments.clear()
            return None
        if previous is None:
            self.last_seen = now  # duplicate replay cannot keep a session alive
        if len(self.fragments) != self.parts:
            return None
        lengths = [len(self.fragments[i]) for i in range(self.parts)]
        if (len(set(lengths[:-1])) > 1 or
                (self.parts > 1 and lengths[-1] > lengths[0]) or
                sum(lengths) not in self.sizes):
            self.blocked = True
            self.fragments.clear()
            return None
        values = b"".join(self.fragments[i] for i in range(self.parts))
        self.completed = True
        self.fragments.clear()
        return values
