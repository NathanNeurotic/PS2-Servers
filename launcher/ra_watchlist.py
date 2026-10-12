"""Owned validation and lifecycle for RAWL v1 watch lists.

Reference: pinned OPL src/rawatch.c and protocol/ra_watch.h. This validates
locally generated lists before serving them; it does not perform memory reads
or derive achievement addresses. Out-of-RAM addresses remain legal because
the console returns zero for them rather than rejecting the whole set.
"""
from dataclasses import dataclass
import re
import struct

from launcher.ra_protocol import MAX_VALUES, SnapshotAssembler, watch_chunk


@dataclass(frozen=True)
class WatchList:
    """Validated immutable wire bytes and their possible snapshot dimensions."""

    data: bytes
    count: int
    direct_bytes: int
    nodes: int

    @property
    def snapshot_sizes(self):
        """Allow older direct-only consoles and consoles with chain support."""
        return frozenset((self.direct_bytes, self.direct_bytes + self.nodes * 8))


def validate_watchlist(data):
    """Validate a complete locally generated RAWL file and optional RANL tail."""
    if not isinstance(data, bytes) or not 20 <= len(data) <= 17432:
        raise ValueError("Invalid watch-list length or type")
    magic, version, count, direct_bytes = struct.unpack_from("<4I", data)
    if magic != 0x4C574152 or version != 1 or not 1 <= count <= 4096:
        raise ValueError("Invalid watch-list header")
    end = 16 + count * 4
    if end > len(data):
        raise ValueError("Truncated watch entries")
    sizes = [struct.unpack_from("<I", data, 16 + i * 4)[0] >> 28 for i in range(count)]
    if any(size not in (1, 2, 4) for size in sizes) or sum(sizes) != direct_bytes:
        raise ValueError("Invalid direct-value dimensions")
    nodes = 0
    if end != len(data):
        if len(data) - end < 8:
            raise ValueError("Truncated pointer-tail header")
        node_magic, nodes = struct.unpack_from("<2I", data, end)
        if node_magic != 0x4C4E4152 or not 1 <= nodes <= 128 or end + 8 + nodes * 8 != len(data):
            raise ValueError("Invalid pointer-tail dimensions")
        for index in range(nodes):
            word, _ = struct.unpack_from("<2I", data, end + 8 + index * 8)
            parent, from_node, size = word & 0xFFF, (word >> 12) & 1, (word >> 13) & 7
            if size not in (1, 2, 4) or parent >= (index if from_node else count):
                raise ValueError("Invalid pointer dependency or read size")
    if not 1 <= direct_bytes <= direct_bytes + nodes * 8 <= MAX_VALUES:
        raise ValueError("Snapshot exceeds protocol capacity")
    return WatchList(data, count, direct_bytes, nodes)


def validate_orbit_watchlist(data):
    """Qualify a complete set for ORBIT v1.0.2's Neutrino agent.

    That agent drops snapshots above PAYLOAD - 256 (1216 bytes) and its
    EE reader ignores pointer tails. Reject the complete set rather than
    truncate reads or claim partial achievement support. Callers must select
    this profile explicitly; RAP1 alone cannot identify the loader family.
    This does not certify hardware behavior or enable a service route.
    """
    watch = validate_watchlist(data)
    if watch.direct_bytes > 1216:
        raise ValueError("ORBIT v1.0.2 agent cannot send more than 1216 value bytes")
    if watch.nodes:
        raise ValueError("ORBIT v1.0.2 agent does not resolve pointer-chain watch entries")
    return watch


class WatchSession:
    """Bind validated lists and assembly state to an identified game generation.

    Receiver must separately enforce peer/account ownership and clear on those
    changes. Reinstalling even an equal-size list always discards old fragments.
    """

    def __init__(self, **assembler_options):
        self.assembler_options = assembler_options
        self.generation = 0
        self.clear()

    def clear(self):
        """Invalidate transferable bytes and all in-flight memory values."""
        self.generation += 1
        self.image_hash = self.serial = None
        self.watch = self.assembler = None

    def install(self, image_hash, serial, data):
        """Replace a session atomically after validating its complete new list."""
        if not isinstance(image_hash, str) or not re.fullmatch(r"[a-fA-F0-9]{32}", image_hash):
            raise ValueError("Invalid image hash")
        if not isinstance(serial, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,15}", serial):
            raise ValueError("Invalid game serial")
        watch = validate_watchlist(data)
        assembler = SnapshotAssembler(serial, watch.count, watch.snapshot_sizes,
                                       **self.assembler_options)
        self.generation += 1
        self.image_hash, self.serial = image_hash.lower(), serial
        self.watch, self.assembler = watch, assembler

    def chunk(self, image_hash, index):
        """Serve only the installed image; stale-image requests get no bytes."""
        if self.watch is None or image_hash != self.image_hash:
            return None
        return watch_chunk(self.watch.data, index)

    def feed(self, packet):
        """Deliver memory only while a validated game generation is installed."""
        return self.assembler.feed(packet) if self.assembler else None
