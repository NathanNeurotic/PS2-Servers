#!/usr/bin/env python3
"""
UDPFS Server over UDPRDMA

Unified server for PS2 file and block device access.
UDPBD is a subset of UDPFS using block I/O messages (BREAD/BWRITE/INFO).

Usage:
    python udpfs_server.py --block-device disk.iso
    python udpfs_server.py --root-dir /path/to/serve
    python udpfs_server.py --block-device disk.iso --root-dir /path/to/serve

Examples:
    python udpfs_server.py -b game.iso                     # Block device only (UDPBD mode)
    python udpfs_server.py -d /home/user/ps2games          # Filesystem only
    python udpfs_server.py -b game.iso -d /games           # Both block device and filesystem
    python udpfs_server.py -b game.iso --sector-size 2048  # Custom sector size
    python udpfs_server.py -b game.iso --read-only         # Read-only mode
    python udpfs_server.py -d /games --enable-compression  # Transparent .zso/.cso/.chd decompression

Compression Support:
    With --enable-compression, the server transparently decompresses .zso (LZ4),
    .cso (zlib), and .chd (MAME CHD v5) files. Compressed files appear as .iso in
    directory listings.
    Requires 'lz4' package for ZSO support: pip install lz4
    Formats whose library is unavailable are not advertised: their files keep
    their real extension in listings instead of appearing as an unreadable .iso.
"""

import argparse
import errno
import gzip
import math
import os
import queue
import select
import socket
import struct
import sys
import threading
import time
import zlib
from collections import OrderedDict
from dataclasses import dataclass
from enum import IntEnum
from typing import Dict, List, Optional, Tuple, Union

# Compressed ISO support
from compressed_iso import CompressedFileWrapper, ZsoFileWrapper, CsoFileWrapper, ChdFileWrapper, LIBCHDR_AVAILABLE

# Check LZ4 availability for ZSO format
try:
    import lz4
    LZ4_AVAILABLE = True
except ImportError:
    LZ4_AVAILABLE = False


def _supported_compressed_extensions() -> Tuple[str, ...]:
    """Compressed-image extensions this server can actually decompress.

    Built once at import and gated on library availability: a format whose
    library is missing must never be renamed to .iso in listings or probed as
    an .iso sibling, because the client would then open a file the server can
    only serve as raw container bytes. The tuple order is also the probe order
    when several compressed siblings of the same .iso exist.
    """
    exts = []
    if LZ4_AVAILABLE:
        exts.append('.zso')
    exts.append('.cso')  # zlib is stdlib; CSO is always supported
    if LIBCHDR_AVAILABLE:
        exts.append('.chd')
    return tuple(exts)


# The single source of truth for every extension predicate below (listing
# transform, .iso sibling probes, is-compressed checks). Do NOT hardcode
# '.zso'/'.cso'/'.chd' lists at call sites -- that drift is exactly how CHD
# files silently vanished from directory listings once before.
COMPRESSED_EXTENSIONS = _supported_compressed_extensions()


# UDPRDMA Protocol Constants
UDPFS_PORT = 0xF5F6
UDPRDMA_SVC_UDPFS = 0xF5F5

# UDPRDMA Packet Types
class PacketType(IntEnum):
    DISCOVERY = 0
    INFORM = 1
    DATA = 2

# UDPRDMA Data Flags
class DataFlags(IntEnum):
    ACK = 1
    FIN = 2

# UDPFS Message Types (unified protocol - includes UDPBD subset)
class MsgType(IntEnum):
    # File operations
    OPEN_REQ      = 0x10
    OPEN_REPLY    = 0x11
    CLOSE_REQ     = 0x12
    CLOSE_REPLY   = 0x13
    READ_REQ      = 0x14
    WRITE_REQ     = 0x16
    WRITE_DATA    = 0x17
    WRITE_DONE    = 0x18
    LSEEK_REQ     = 0x1A
    LSEEK_REPLY   = 0x1B
    DREAD_REQ     = 0x1C
    DREAD_REPLY   = 0x1D
    GETSTAT_REQ   = 0x1E
    GETSTAT_REPLY = 0x1F
    MKDIR_REQ     = 0x20
    REMOVE_REQ    = 0x22
    RMDIR_REQ     = 0x24
    RESULT_REPLY  = 0x26
    # Block I/O operations (UDPBD subset)
    BREAD_REQ     = 0x28
    BWRITE_REQ    = 0x2A

# PS2 file mode flags
FIO_S_IFREG = 0x2000
FIO_S_IFDIR = 0x1000

# Limits
MAX_DATA_PAYLOAD = 1408  # Maximum UDPRDMA payload
MAX_HANDLES = 64  # open handles per client (matches udpfsd; was 32 originally)

# Multi-client session management
#
# A reap closes the peer's open file handles (see Session._run), and UDPFS has no
# DISCONNECT packet -- DISCOVERY/INFORM/DATA is the whole protocol -- so a paused
# console and a powered-off one are indistinguishable: both simply go quiet. The
# timeout is therefore a guess about which one we are looking at, and guessing
# wrong on a paused game costs the player their handles. Default matches upstream
# udpfsd (1 hour), which is field-proven and forgiving of idle play and long
# transfers. It cannot be disabled: without it, every console reboot strands a
# session (thread + queue + handles) until the server exits.
SESSION_TIMEOUT = 3600.0         # default seconds of peer inactivity before reaping (see --peer-timeout)
# Floor: 12x SESSION_SWEEP_INTERVAL (a reap is only ever +/-5s accurate) and 12x
# the client's own 5s udprdma_recv timeout, so a peer mid-operation can never be
# reaped. Ceiling: past a day it is "never" in all but name, and each stranded
# session holds a thread and up to MAX_HANDLES descriptors.
SESSION_TIMEOUT_MIN = 60.0
SESSION_TIMEOUT_MAX = 86400.0
SESSION_SWEEP_INTERVAL = 5.0     # how often the demux loop checks for idle sessions
HAS_PREAD = hasattr(os, 'pread')     # POSIX: lock-free positional block-device reads
HAS_PWRITE = hasattr(os, 'pwrite')

# Flow control
SEND_WINDOW = 8           # Max unacked packets in flight
WINDOW_ACK_TIMEOUT = 0.1  # Seconds to wait for window ACK
MAX_WINDOW_RETRIES = 4    # Max retries waiting for window ACK (matches IOP)

# Fixed handle for block device
BLOCK_DEVICE_HANDLE = 0

# Compressed file format constants (for get_compressed_info)
ZSO_MAGIC = b'ZSO\x00'
CSO_MAGIC = 0x4F534943  # "CISO"


def open_compressed(file_path: str, cache_size: int = None) -> Optional[CompressedFileWrapper]:
    """Open a compressed file and return appropriate wrapper based on extension.
    
    Args:
        file_path: Path to the file
        cache_size: Number of blocks to cache (default: 32)
        
    Returns:
        CompressedFileWrapper subclass instance, or None if unsupported format
    """
    ext = os.path.splitext(file_path)[1].lower()
    
    if ext == '.zso':
        if not LZ4_AVAILABLE:
            return None
        return ZsoFileWrapper(file_path, cache_size)
    elif ext == '.cso':
        return CsoFileWrapper(file_path, cache_size)
    elif ext == '.chd':
        return ChdFileWrapper(file_path, cache_size)
    
    return None


def get_compressed_info(file_path: str) -> Optional[Tuple[int, str]]:
    """
    Get info about a compressed file without fully opening it.
    Returns (uncompressed_size, format_name) or None if not a supported compressed file.
    """
    ext = os.path.splitext(file_path)[1].lower()
    
    try:
        with open(file_path, 'rb') as f:
            if ext == '.chd':
                # CHD has different header structure
                header = f.read(64)
                if len(header) < 64:
                    return None
                
                # Check CHD magic "MComprHD"
                magic = header[0:8]
                if magic != b'MComprHD':
                    return None
                
                # Check version (only v5 supported)
                version = struct.unpack('>I', header[12:16])[0]
                if version != 5:
                    return None
                
                # Uncompressed size is at offset 32 (big-endian uint64 in v5)
                uncompressed_size = struct.unpack('>Q', header[32:40])[0]
                compressors = struct.unpack('>4I', header[16:32])
                hunk_size = struct.unpack('>I', header[56:60])[0]

                # For CD-format CHDs (cdlz/cdzl/cdfl), the stored uncompressed_size
                # counts full 2448-byte CD frames (2352 sector + 96 subcode).
                # We present as 2048-byte/sector ISO, so correct the size.
                _CD_FRAME_SIZE = 2448
                _CD_CODECS = (0x63646c7a, 0x63647a6c, 0x6364666c)  # cdlz, cdzl, cdfl
                unit_size = struct.unpack('>I', header[60:64])[0]
                # Same predicate as the CHD wrapper (compressed_iso/chd.py) and
                # udpfsd: a CD unit size OR a CD codec, AND whole 2448-byte-frame
                # hunks -- so the size reported here can never disagree with what
                # the read path extracts.
                if ((unit_size == _CD_FRAME_SIZE
                     or any(c in _CD_CODECS for c in compressors if c != 0))
                        and hunk_size > 0 and hunk_size % _CD_FRAME_SIZE == 0):
                    frames_per_hunk = hunk_size // _CD_FRAME_SIZE
                    total_hunks = (uncompressed_size + hunk_size - 1) // hunk_size
                    uncompressed_size = total_hunks * frames_per_hunk * 2048

                return (uncompressed_size, 'CHD')
            
            header = f.read(24)
            if len(header) < 16:
                return None
            
            magic = header[0:4]
            magic_int = struct.unpack('<I', magic)[0]
            
            if ext == '.zso':
                if magic == ZSO_MAGIC:
                    uncompressed_size = struct.unpack('<Q', header[8:16])[0]
                    return (uncompressed_size, 'ZSO')
                elif magic == b'ZISO':
                    uncompressed_size = struct.unpack('<Q', header[8:16])[0]
                    return (uncompressed_size, 'ZISO')
            elif ext == '.cso':
                if magic_int == CSO_MAGIC:
                    uncompressed_size = struct.unpack('<Q', header[8:16])[0]
                    return (uncompressed_size, 'CSO')
    except (IOError, struct.error):
        pass
    
    return None


@dataclass
class Header:
    """UDPRDMA base header (2 bytes)"""
    packet_type: int  # 4 bits
    seq_nr: int       # 12 bits

    @classmethod
    def unpack(cls, data: bytes) -> 'Header':
        val = struct.unpack('<H', data[:2])[0]
        return cls(
            packet_type=val & 0xF,
            seq_nr=(val >> 4) & 0xFFF
        )

    def pack(self) -> bytes:
        val = (self.packet_type & 0xF) | ((self.seq_nr & 0xFFF) << 4)
        return struct.pack('<H', val)


@dataclass
class DiscHeader:
    """Discovery/Inform header (4 bytes)"""
    service_id: int
    port: int = 0

    @classmethod
    def unpack(cls, data: bytes) -> 'DiscHeader':
        service_id, port = struct.unpack('<HH', data[:4])
        return cls(service_id=service_id, port=port)

    def pack(self) -> bytes:
        return struct.pack('<HH', self.service_id, self.port)


@dataclass
class DataHeader:
    """Data header (4 bytes)"""
    seq_nr_ack: int       # 12 bits
    flags: int            # 2 bits
    hdr_word_count: int   # 4 bits: app header size in 4-byte words
    data_byte_count: int  # 14 bits: data payload size

    @classmethod
    def unpack(cls, data: bytes) -> 'DataHeader':
        val = struct.unpack('<I', data[:4])[0]
        return cls(
            seq_nr_ack=val & 0xFFF,
            flags=(val >> 12) & 0x3,
            hdr_word_count=(val >> 14) & 0xF,
            data_byte_count=(val >> 18) & 0x3FFF
        )

    def pack(self) -> bytes:
        val = ((self.seq_nr_ack & 0xFFF) |
               ((self.flags & 0x3) << 12) |
               ((self.hdr_word_count & 0xF) << 14) |
               ((self.data_byte_count & 0x3FFF) << 18))
        return struct.pack('<I', val)


def _clamp_peer_timeout(seconds, warn=True):
    """Hold --peer-timeout inside [SESSION_TIMEOUT_MIN, SESSION_TIMEOUT_MAX]."""
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        value = SESSION_TIMEOUT
    except OverflowError:
        # An int too big for a float (10**1000) is still a number, just an
        # unrepresentable one, so clamp it by sign like inf/-inf rather than
        # silently resetting it to the default and ignoring what was asked for.
        value = SESSION_TIMEOUT_MAX if seconds > 0 else SESSION_TIMEOUT_MIN
    if math.isnan(value):  # every comparison against NaN is False, so it would
        value = SESSION_TIMEOUT  # sail through the clamp and disable reaping
    clamped = min(max(value, SESSION_TIMEOUT_MIN), SESSION_TIMEOUT_MAX)
    if warn and clamped != value:
        print(f"Warning: --peer-timeout {value:g} is outside the supported "
              f"{SESSION_TIMEOUT_MIN:g}-{SESSION_TIMEOUT_MAX:g}s range; "
              f"using {clamped:g}.")
    return clamped


class FileHandle:
    """Represents an open file handle on the server"""
    def __init__(self, obj, is_dir: bool = False):
        self.obj = obj
        self.is_dir = is_dir

    def close(self):
        if self.is_dir:
            pass  # Directory entries are a list, nothing to close
        else:
            self.obj.close()


def _session_prop(name):
    """A property that proxies a per-client field to the calling worker thread's
    Session (server._local.session). This lets every request handler keep using
    self.tx_seq_nr / self.handles / self.write_* verbatim while the underlying
    state is actually isolated per client."""
    def getter(self):
        return getattr(self._local.session, name)

    def setter(self, value):
        setattr(self._local.session, name, value)

    return property(getter, setter)


class UdpfsServer:
    """UDPFS Server over UDPRDMA - unified file and block device server"""

    def __init__(self, root_dir: Optional[str] = None,
                 block_device: Optional[str] = None,
                 port: int = UDPFS_PORT,
                 bind_ip: str = '',
                 sector_size: int = 512,
                 read_only: bool = False, verbose: bool = False,
                 enable_compression: bool = False,
                 compression_cache_size: int = 32,
                 peer_timeout: float = SESSION_TIMEOUT,
                 metrics: bool = False,
                 metrics_period: float = 60.0,
                 single_port: bool = False,
                 modulo_compat: bool = False,
                 data_port: int = 0):
        # Modulo's client only ever talked to Modulo's own bundled server, which is
        # single-socket, so the two always travel together.
        if modulo_compat:
            single_port = True
        self.modulo_compat = modulo_compat
        self.root_dir = os.path.realpath(root_dir) if root_dir else None
        self.port = port
        self.bind_ip = bind_ip
        self.sector_size = sector_size
        self.read_only = read_only
        self.verbose = verbose
        self.enable_compression = enable_compression
        self.compression_cache_size = compression_cache_size
        # Clamped here rather than in argparse so the CLI, PEER_TIMEOUT and the
        # launcher all get the same bounds. Clamp instead of reject: a bad number
        # should not stop someone from serving their games. 0 is NOT "disabled" --
        # it would reap every peer at the next sweep -- so it clamps up like any
        # other out-of-range value.
        self.session_timeout = _clamp_peer_timeout(peer_timeout)
        self.metrics = metrics
        self.metrics_period = metrics_period
        self.single_port = single_port
        # Only ever sent in modulo_compat: its server advertises a friendly name in
        # the INFORM so its loader can label the source. Shares stay empty, matching
        # its CLI server (only its GUI wrapper ever fills them in). gethostname can
        # raise on a container or a host with no resolvable name, and this runs for
        # everyone -- a cosmetic label must not stop a server nobody asked to name.
        try:
            self.server_name = socket.gethostname().split('.')[0]
        except OSError:
            self.server_name = 'UDPFS'
        self.share_names: List[str] = []
        self._last_metrics = time.monotonic()

        if self.root_dir and not os.path.isdir(self.root_dir):
            print(f"Error: '{root_dir}' is not a directory")
            sys.exit(1)

        # Two sockets on one port is not a shared-port mode -- SO_REUSEADDR lets
        # both bind and then splits arriving datagrams between them, which silently
        # breaks discovery. Refuse it and point at the mode that does this properly.
        if not single_port and data_port and data_port == port:
            print(f"Error: --data-port 0x{data_port:04X} is the same as the discovery "
                  f"port. Pick a different data port, or use --single-port to serve "
                  f"discovery and data on one port.")
            sys.exit(1)

        # Discovery socket, broadcast UDP
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.sock.bind(('', port))
        self.sock.setblocking(False)

        if single_port:
            # Compatibility mode: carry DISCOVERY *and* DATA on the one discovery
            # port instead of handing the client off to a separate ephemeral data
            # socket. Aliasing dsock to sock makes every send (INFORM included)
            # leave from the discovery port, and makes _send_inform advertise that
            # same port as the data port -- so a client that cannot follow the
            # normal two-port handshake never has to move. Costs nothing but the
            # per-peer port isolation, which nothing here relies on.
            self.dsock = self.sock
        else:
            # Data socket. Port 0 = ephemeral (the default): fine when the app's
            # own firewall rule allows the program on any port. Pin it with
            # --data-port when the data endpoint has to be predictable -- manual
            # firewall rules, port forwarding, or a restrictive NAT can't follow a
            # port that changes every launch.
            self.dsock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.dsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.dsock.bind((bind_ip, data_port))
            self.dsock.setblocking(False)

        # Windows: a UDP recvfrom() raises ConnectionResetError (WinError 10054)
        # when a *previous* sendto() drew an ICMP port-unreachable -- e.g. the PS2
        # briefly not reading, or a transient network hiccup. UDP is
        # connectionless, so these resets are spurious; left uncaught they kill the
        # receive loop and drop the transfer mid-load (a "black screen on load" on
        # Win11). SIO_UDP_CONNRESET tells Windows to stop reporting them. Harmless
        # / unavailable no-op elsewhere.
        if sys.platform == 'win32':
            SIO_UDP_CONNRESET = 0x9800000C
            for _s in (self.sock, self.dsock):
                try:
                    _s.ioctl(SIO_UDP_CONNRESET, struct.pack('I', 0))
                except (OSError, AttributeError, ValueError):
                    pass

        # Multi-client state: one Session per peer address, each with its own
        # protocol state (sequence numbers, handle table, write state) and worker
        # thread. The demultiplexer (run) never blocks on protocol work; per-client
        # blocking waits read from that client's own queue. See class Session and
        # the _session_prop(...) proxies below, which route the per-client fields
        # the handler methods use (self.tx_seq_nr, self.handles, ...) to the
        # calling worker thread's Session -- so the handler bodies are unchanged.
        self._local = threading.local()      # _local.session = current worker's Session
        self.sessions: Dict[Tuple[str, int], 'Session'] = {}
        self.sessions_lock = threading.Lock()
        self.send_lock = threading.Lock()    # serialize sends on the shared data socket
        self.bd_lock = threading.Lock()      # guards the block-device seek+read fallback
        self._shutdown = False
        self._last_sweep = time.monotonic()

        # Shared block device (handle 0): opened once, shared across all sessions.
        # Concurrent access uses positional I/O (bd_read/bd_write), never the
        # object's own file position.
        self.bd_fh: Optional[FileHandle] = None
        self.bd_fd: Optional[int] = None
        self.bd_sector_size = sector_size
        self.bd_sector_count = 0
        if block_device:
            mode = 'rb' if read_only else 'r+b'
            try:
                bd_file = open(block_device, mode)
            except IOError as e:
                print(f"Error: Cannot open '{block_device}': {e}")
                sys.exit(1)
            bd_file.seek(0, os.SEEK_END)
            bd_size = bd_file.tell()
            bd_file.seek(0)
            self.bd_sector_count = bd_size // sector_size
            self.bd_fh = FileHandle(bd_file, is_dir=False)
            try:
                self.bd_fd = bd_file.fileno()
            except (OSError, AttributeError):
                self.bd_fd = None

        # Statistics
        self.stats = {
            'discovery': 0,
            'open': 0,
            'close': 0,
            'read': 0,
            'write': 0,
            'bread': 0,
            'bwrite': 0,
            'lseek': 0,
            'dread': 0,
            'getstat': 0,
            'mkdir': 0,
            'remove': 0,
            'rmdir': 0,
            'bytes_read': 0,
            'bytes_written': 0,
        }

        # Live status line
        self._start_time = time.monotonic()
        self._last_status_time = 0.0
        self._last_status_bytes = 0
        self._status_visible = False

    # -- per-client state proxies (route to self._local.session; see Session) --
    peer_addr = _session_prop('peer_addr')
    tx_seq_nr = _session_prop('tx_seq_nr')
    tx_seq_nr_acked = _session_prop('tx_seq_nr_acked')
    rx_seq_nr_expected = _session_prop('rx_seq_nr_expected')
    tx_buffer = _session_prop('tx_buffer')
    tx_start_seq = _session_prop('tx_start_seq')
    handles = _session_prop('handles')
    next_handle = _session_prop('next_handle')
    write_handle = _session_prop('write_handle')
    write_is_block = _session_prop('write_is_block')
    write_sector_nr = _session_prop('write_sector_nr')
    write_sector_count = _session_prop('write_sector_count')
    write_data = _session_prop('write_data')
    write_total_chunks = _session_prop('write_total_chunks')
    write_received_chunks = _session_prop('write_received_chunks')

    # -- shared data-socket send + positional block-device I/O -------------
    def _sendto(self, packet: bytes, addr):
        """Send on the shared data socket under a lock (many worker threads)."""
        sock = self.dsock
        with self.send_lock:
            sock.sendto(packet, addr)

    def bd_read(self, offset: int, n: int) -> bytes:
        """Thread-safe positional read of the shared block device."""
        if HAS_PREAD and self.bd_fd is not None:
            return os.pread(self.bd_fd, n, offset)
        with self.bd_lock:
            self.bd_fh.obj.seek(offset)
            return self.bd_fh.obj.read(n)

    def bd_write(self, offset: int, data: bytes) -> int:
        """Thread-safe positional write to the shared block device."""
        if HAS_PWRITE and self.bd_fd is not None:
            return os.pwrite(self.bd_fd, data, offset)
        with self.bd_lock:
            self.bd_fh.obj.seek(offset)
            n = self.bd_fh.obj.write(data)
            self.bd_fh.obj.flush()
            return n

    # -- session lifecycle -------------------------------------------------
    def _get_or_create_session(self, addr):
        with self.sessions_lock:
            sess = self.sessions.get(addr)
            if sess is None:
                sess = Session(self, addr)
                self.sessions[addr] = sess
                sess.start()
            sess.last_activity = time.monotonic()
            return sess

    def _route_data(self, data: bytes, addr):
        """Demux a data datagram to its peer's session queue (demux thread)."""
        if len(data) < 2:
            return
        hdr = Header.unpack(data)
        if hdr.packet_type != PacketType.DATA:
            return
        self._get_or_create_session(addr).queue.put((data, addr))

    def _sweep_idle_sessions(self):
        now = time.monotonic()
        if now - self._last_sweep < SESSION_SWEEP_INTERVAL:
            return
        self._last_sweep = now
        with self.sessions_lock:
            idle = [(a, now - s.last_activity) for a, s in self.sessions.items()
                    if now - s.last_activity > self.session_timeout]
            for a, quiet in idle:
                self.sessions.pop(a).shutdown()
                # Not verbose-gated. A reap tells the client nothing (no packet
                # exists to tell it with) and takes its open handles and their
                # read positions with it, so if that console was only paused, its
                # next read fails against a session that no longer knows it. This
                # line is the sole trace anyone gets of why.
                self._print_event(
                    f"[{a[0]}:{a[1]}] idle {quiet:.0f}s > peer-timeout "
                    f"{self.session_timeout:.0f}s -- dropped, its open files "
                    f"closed (raise --peer-timeout if it was only paused)")

    def _emit_metrics(self):
        """Periodically log transfer/op stats when --metrics is enabled."""
        if not self.metrics:
            return
        now = time.monotonic()
        if now - self._last_metrics < self.metrics_period:
            return
        self._last_metrics = now
        with self.sessions_lock:
            nclients = len(self.sessions)
        s = self.stats
        self._print_event(
            "[metrics] clients=%d read=%s written=%s "
            "ops(open=%d read=%d bread=%d write=%d bwrite=%d dread=%d)" % (
                nclients, self._format_bytes(s['bytes_read']),
                self._format_bytes(s['bytes_written']),
                s['open'], s['read'], s['bread'], s['write'], s['bwrite'], s['dread']))

    def _shutdown_all_sessions(self):
        self._shutdown = True
        with self.sessions_lock:
            sessions = list(self.sessions.values())
            self.sessions.clear()
        for s in sessions:
            s.shutdown()
        # Wait for worker threads to finish before the caller closes shared
        # resources (the block device in _cleanup), avoiding a shutdown race.
        for s in sessions:
            s._thread.join(timeout=2.0)

    def run(self):
        """Main server loop"""
        print(f"UDPFS Server")
        if self.root_dir:
            print(f"  Root: {self.root_dir}")
        if self.bd_fh is not None:
            print(f"  Block device: handle={BLOCK_DEVICE_HANDLE}, "
                  f"sectors={self.bd_sector_count:,}, "
                  f"sector_size={self.bd_sector_size}")
        print(f"  Discovery bind: 0.0.0.0:{self.port} (0x{self.port:04X})")
        addr, port = self.dsock.getsockname()
        if self.single_port:
            print(f"  Data bind: {addr}:{port} (0x{port:04X}) [single-port mode]")
        else:
            print(f"  Data bind: {addr}:{port} (0x{port:04X})")
        print(f"  Mode: {'read-only' if self.read_only else 'read-write'}")
        if self.enable_compression:
            formats = [ext[1:].upper() for ext in COMPRESSED_EXTENSIONS]
            print(f"  Compression: enabled ({', '.join(formats)})")

        print(f"  Listening...")
        print()
        while not self._shutdown:
            try:
                socks = [self.sock] if self.single_port else [self.sock, self.dsock]
                r, _, _ = select.select(socks, [], [], 1.0)
                for ready in r:
                    if ready is self.sock:
                        try:
                            if self.single_port:
                                # One socket carries both packet types here, so
                                # dispatch on the UDPRDMA type instead of on which
                                # socket it arrived from.
                                data, addr = self.sock.recvfrom(4096)
                                if (len(data) >= 2 and Header.unpack(data).packet_type
                                        == PacketType.DISCOVERY):
                                    self._handle_discovery(data, addr)
                                else:
                                    self._route_data(data, addr)
                            else:
                                data, addr = self.sock.recvfrom(2048)
                                self._handle_discovery(data, addr)
                        except OSError:
                            # BlockingIOError (no datagram) or a spurious Windows
                            # ConnectionResetError -- never let one datagram's
                            # error kill the whole server loop.
                            pass
                    elif ready is self.dsock:
                        try:
                            data, addr = self.dsock.recvfrom(4096)
                            self._route_data(data, addr)
                        except OSError:
                            # BlockingIOError (no datagram) or a spurious Windows
                            # ConnectionResetError -- never let one datagram's
                            # error kill the whole server loop.
                            pass
                self._sweep_idle_sessions()
                self._emit_metrics()
            except KeyboardInterrupt:
                if self._status_visible:
                    sys.stdout.write(f"\r{'':<79}\r")
                print("\nShutting down...")
                self._shutdown_all_sessions()
                self._cleanup()
                self._print_stats()
                break

    def _format_bytes(self, n: int) -> str:
        """Format byte count as human-readable string"""
        if n < 1024:
            return f"{n} B"
        elif n < 1024 * 1024:
            return f"{n / 1024:.1f} KB"
        elif n < 1024 * 1024 * 1024:
            return f"{n / (1024 * 1024):.1f} MB"
        else:
            return f"{n / (1024 * 1024 * 1024):.1f} GB"

    def _update_status(self):
        """Update in-place status line (throttled to 1/sec)"""
        now = time.monotonic()
        if now - self._last_status_time < 1.0:
            return
        elapsed = now - self._start_time
        dt = now - self._last_status_time if self._last_status_time else elapsed
        bytes_delta = self.stats['bytes_read'] + self.stats['bytes_written'] - self._last_status_bytes

        # Build compact op counts (only non-zero)
        ops = []
        for key in ('bread', 'read', 'write', 'bwrite', 'open', 'close', 'dread', 'getstat', 'lseek'):
            v = self.stats[key]
            if v > 0:
                ops.append(f"{key}:{v:,}")
        op_str = ' '.join(ops) if ops else 'idle'

        # Throughput
        rate = bytes_delta / dt if dt > 0 else 0
        total_bytes = self.stats['bytes_read'] + self.stats['bytes_written']
        h, rem = divmod(int(elapsed), 3600)
        m, s = divmod(rem, 60)

        line = f"[{h:02d}:{m:02d}:{s:02d}] {op_str} | {self._format_bytes(total_bytes)} @ {self._format_bytes(int(rate))}/s"
        sys.stdout.write(f"\r{line:<79}")
        sys.stdout.flush()

        self._last_status_time = now
        self._last_status_bytes = total_bytes
        self._status_visible = True

    def _print_event(self, msg: str):
        """Print an event message, clearing the status line first.

        Never raises: a filename/path with characters outside the console's code
        page (e.g. Polish 'l-stroke' on a cp1252 console) must not abort the
        calling handler -- doing so would skip the reply sent after this log line
        and stall the PS2 (empty game list / black screen). Belt-and-suspenders
        alongside the stream-level errors='backslashreplace' set in serve.py, so
        standalone runs (python udpfs_server.py ...) are covered too.
        """
        if self._status_visible:
            sys.stdout.write(f"\r{'':<79}\r")
            self._status_visible = False
        try:
            print(msg)
        except UnicodeEncodeError:
            enc = sys.stdout.encoding or 'ascii'
            print(msg.encode(enc, 'backslashreplace').decode(enc, 'replace'))

    def _cleanup(self):
        """Close the shared block device (each session closes its own handles)."""
        if self.bd_fh is not None:
            try:
                self.bd_fh.close()
            except Exception:
                pass

    def _resolve_path(self, client_path: str) -> Optional[str]:
        """Resolve client path to absolute path within root_dir."""
        if self.root_dir is None:
            return None

        # Strip leading slashes
        while client_path.startswith('/') or client_path.startswith('\\'):
            client_path = client_path[1:]

        # Normalize separators
        client_path = client_path.replace('\\', '/')
        
        # Resolve
        resolved = os.path.realpath(os.path.join(self.root_dir, client_path))

        # Ensure within root. Compare against root_dir verbatim: it was
        # realpath()-normalized at startup, so it never needs cleanup here --
        # a doubled backslash is only ever the legitimate lead of a UNC root
        # (\\server\share\...), and collapsing it made every request against a
        # NAS root fail containment (EACCES on all opens). Append os.sep only
        # when missing: realpath keeps the trailing separator on bare drive
        # roots ('C:\') and on '/', and blindly appending another would make
        # serving a whole drive fail the same way.
        prefix = (self.root_dir if self.root_dir.endswith(os.sep)
                  else self.root_dir + os.sep)
        if not resolved.startswith(prefix) and resolved != self.root_dir:
            return None

        return resolved

    def _transform_compressed_name(self, name: str) -> str:
        """Transform supported compressed extensions to .iso for directory listing."""
        if not self.enable_compression:
            return name
        lower_name = name.lower()
        for ext in COMPRESSED_EXTENSIONS:
            if lower_name.endswith(ext):
                return name[:-len(ext)] + '.iso'
        return name

    def _resolve_compressed_sibling(self, path: str) -> Optional[str]:
        """Resolve a client '.iso' path to an existing compressed sibling file.

        Directory listings rename e.g. 'Game.chd' to 'Game.iso'; this is the
        reverse mapping used when the client then opens or stats that virtual
        name. Collects existing siblings in COMPRESSED_EXTENSIONS order, then
        prefers one whose header actually parses -- so a corrupt file that
        sorts earlier (an interrupted 'Game.zso' download beside a good
        'Game.cso') never hides a decodable later one. Returns the resolved
        host path, or None.
        """
        if not self.enable_compression or not path.lower().endswith('.iso'):
            return None
        base_path = path[:-4]  # Remove .iso

        candidates: List[str] = []
        seen = set()

        def _add(host_path):
            if host_path and host_path not in seen and os.path.exists(host_path):
                seen.add(host_path)
                candidates.append(host_path)

        # Exact-case probe (the common path); each candidate is containment-checked.
        for ext in COMPRESSED_EXTENSIONS:
            _add(self._resolve_path(base_path + ext))

        # Case-insensitive fallback for the EXTENSION only. The listing transform
        # preserves stem case round-trip, so match the stem exactly and just fold
        # the extension -- otherwise 'Game.iso' could bind to a different image
        # 'game.cso' on a case-sensitive filesystem.
        resolved_base = self._resolve_path(base_path)
        # resolved_base == root means base_path was empty (e.g. '/.iso'); its
        # parent dir is OUTSIDE root, so splitting it would let a sibling of the
        # games folder escape containment. Require a real name component.
        if resolved_base and resolved_base != self.root_dir:
            dirname, stem = os.path.split(resolved_base)
            try:
                listing = os.listdir(dirname)
            except OSError:
                listing = []
            by_ext: Dict[str, str] = {}
            for entry in listing:
                entry_stem, entry_ext = os.path.splitext(entry)
                if entry_stem == stem:
                    by_ext.setdefault(entry_ext.lower(), entry)
            for ext in COMPRESSED_EXTENSIONS:
                entry = by_ext.get(ext)
                if not entry:
                    continue
                candidate = os.path.join(dirname, entry)
                # dirname came from a split, not _resolve_path -- re-validate
                # that the match is still contained before considering it.
                if self._resolve_path(os.path.relpath(candidate, self.root_dir)):
                    _add(candidate)

        if not candidates:
            return None
        # Prefer a sibling whose header parses so a corrupt earlier-sorted file
        # never hides a decodable one; else return the first existing candidate
        # so callers surface a coherent EIO (open) / ENOENT (stat).
        for candidate in candidates:
            if get_compressed_info(candidate) is not None:
                return candidate
        return candidates[0]

    def _get_compressed_stat(self, file_path: str, original_stat) -> Optional[dict]:
        """Get stat info for a compressed file, returning uncompressed size."""
        info = get_compressed_info(file_path)
        if info is None:
            return None

        uncompressed_size, format_name = info
        return {
            'mode': FIO_S_IFREG,
            'attr': 0,
            'size': uncompressed_size & 0xFFFFFFFF,
            'hisize': (uncompressed_size >> 32) & 0xFFFFFFFF,
            'ctime': self._encode_time(original_stat.st_ctime),
            'atime': self._encode_time(original_stat.st_atime),
            'mtime': self._encode_time(original_stat.st_mtime),
        }

    def _alloc_handle(self, obj, is_dir: bool = False) -> int:
        """Allocate a new handle"""
        if len(self.handles) >= MAX_HANDLES:
            return -errno.EMFILE

        handle_id = self.next_handle
        self.next_handle += 1
        self.handles[handle_id] = FileHandle(obj, is_dir)
        return handle_id

    def _free_handle(self, handle_id: int):
        """Free a handle"""
        # Block device handle cannot be closed
        if handle_id == BLOCK_DEVICE_HANDLE:
            return
        if handle_id in self.handles:
            try:
                self.handles[handle_id].close()
            except Exception:
                pass
            del self.handles[handle_id]

    def _encode_time(self, timestamp: float) -> bytes:
        """Encode Unix timestamp to PS2 iox_stat_t time format (8 bytes)."""
        try:
            t = time.localtime(timestamp)
            return struct.pack('<BBBBBBH',
                0,           # unused
                t.tm_sec,
                t.tm_min,
                t.tm_hour,
                t.tm_mday,
                t.tm_mon,
                t.tm_year
            )
        except (OSError, ValueError):
            return b'\x00' * 8

    def _stat_to_bytes(self, st) -> dict:
        """Convert os.stat_result to PS2-compatible stat fields"""
        import stat as stat_mod
        mode = 0
        if stat_mod.S_ISREG(st.st_mode):
            mode = FIO_S_IFREG
        elif stat_mod.S_ISDIR(st.st_mode):
            mode = FIO_S_IFDIR

        return {
            'mode': mode,
            'attr': 0,
            'size': st.st_size & 0xFFFFFFFF,
            'hisize': (st.st_size >> 32) & 0xFFFFFFFF,
            'ctime': self._encode_time(st.st_ctime),
            'atime': self._encode_time(st.st_atime),
            'mtime': self._encode_time(st.st_mtime),
        }

    def _flags_to_mode(self, flags: int, file_exists: bool = True) -> str:
        """Convert PS2 open flags to Python file mode"""
        access = flags & 0x03
        if access == 0x01:  # O_RDONLY
            return 'rb'
        elif access == 0x02:  # O_WRONLY
            if flags & 0x0100:  # O_APPEND
                return 'ab'
            elif flags & 0x0400:  # O_TRUNC
                return 'wb'
            elif flags & 0x0200:  # O_CREAT
                return 'r+b' if file_exists else 'wb'
            else:
                return 'r+b'
        elif access == 0x03:  # O_RDWR
            if flags & 0x0200:  # O_CREAT
                if flags & 0x0400:  # O_TRUNC
                    return 'w+b'
                else:
                    return 'a+b' if (flags & 0x0100) else ('r+b' if file_exists else 'w+b')
            else:
                return 'r+b'
        return 'rb'

    # --- UDPRDMA packet handling ---

    def _handle_discovery(self, data: bytes, addr: Tuple[str, int]):
        """Handle DISCOVERY packet"""
        if len(data) < 6:
            return

        hdr = Header.unpack(data)
        if hdr.packet_type != PacketType.DISCOVERY:
            return

        disc = DiscHeader.unpack(data[2:10])

        if disc.service_id != UDPRDMA_SVC_UDPFS:
            return

        self.stats['discovery'] += 1

        # Ensure a per-client session (and its worker thread) exists for this peer.
        sess = self._get_or_create_session(addr)
        if self.modulo_compat and (not sess.rx_streaming or hdr.seq_nr != 0):
            # Modulo's client keeps one monotonic sequence for its whole life -- it
            # never restarts at 0, not even across a server restart -- so the
            # reset-on-seq-0 path below never fires for it and every packet is NACKed
            # forever. Its own server resyncs off the DISCOVERY instead, so do that.
            #
            # But not out from under a running stream. Modulo keeps a background
            # DISCOVERY going while it reads: on hardware one carrying seq=0 arrived
            # mid-transfer while the data stream was at 77, and resyncing on it left
            # us demanding 1 forever -- the transfer desynced, exhausted its retries
            # and stalled, over and over. The client carries straight on after those
            # discoveries, which is what proves they re-establish nothing.
            #
            # seq != 0 is the exception, and it has to be: a console that soft-reboots
            # mid-session comes back streaming with a non-zero counter, which the
            # reset-on-seq-0 path cannot catch either -- so keying only on
            # rx_streaming would strand it until the peer timeout reaped it, an hour
            # by default. That is the very lockout this mode exists to fix. Every
            # background discovery observed on hardware carried seq=0, so the two
            # cases separate cleanly -- on one sample, which is worth knowing if a
            # stall ever reappears at a non-zero discovery.
            #
            # Assigned on the session, not through the _session_prop proxies: we are
            # on the demux thread, where _local.session is unset.
            sess.rx_seq_nr_expected = (hdr.seq_nr + 1) & 0xFFF
        self._print_event(f"[{addr[0]}:{addr[1]}] DISCOVERY -> INFORM")
        self._send_inform(addr, sess)

    def _handle_data(self, data: bytes, addr: Tuple[str, int]):
        """Handle DATA packet containing UDPFS message"""
        if len(data) < 6:
            return

        hdr = Header.unpack(data)
        if hdr.packet_type != PacketType.DATA:
            return

        data_hdr = DataHeader.unpack(data[2:6])
        payload = data[6:]
        hdr_size = data_hdr.hdr_word_count * 4
        payload_size = hdr_size + data_hdr.data_byte_count
        actual_payload = payload[:payload_size] if payload_size > 0 else b''

        # Process piggybacked ACK from any packet with ACK flag
        if data_hdr.flags & DataFlags.ACK:
            self.tx_seq_nr_acked = data_hdr.seq_nr_ack
            if self.tx_buffer:
                self.tx_buffer = [
                    (seq, pkt) for seq, pkt in self.tx_buffer
                    if ((seq - data_hdr.seq_nr_ack - 1) & 0xFFF) < 2048
                ]

        # Pure ACK (no payload) - done
        if payload_size == 0 and (data_hdr.flags & DataFlags.ACK):
            return

        # Handle NACK - update acked position and retransmit
        if payload_size == 0 and not (data_hdr.flags & DataFlags.ACK):
            # NACK seq_nr_ack = expected seq, so acked up to expected-1
            self.tx_seq_nr_acked = (data_hdr.seq_nr_ack - 1) & 0xFFF
            if self.verbose:
                self._print_event(f"  NACK received, retransmit from seq={data_hdr.seq_nr_ack}")
            self._retransmit_from(addr, data_hdr.seq_nr_ack)
            return

        # Check sequence number
        if hdr.seq_nr != self.rx_seq_nr_expected:
            prev_seq = (self.rx_seq_nr_expected - 1) & 0xFFF
            if hdr.seq_nr == prev_seq:
                # Duplicate of last processed packet - re-ACK and retransmit response
                if self.verbose:
                    self._print_event(f"  Duplicate seq={hdr.seq_nr}, re-ACK + retransmit")
                self._send_ack(addr, is_ack=True)
                if self.tx_buffer:
                    self._retransmit_from(addr, self.tx_buffer[0][0])
                return
            elif hdr.seq_nr == 0:
                # Assume the peer is reestablishing the connection, reset the connection state
                if self.verbose:
                    self._print_event(f"  Received seq={hdr.seq_nr}, assuming the peer was reset")
                self.tx_seq_nr = 0
                self.tx_buffer = ()
                self.tx_start_seq = 0
                self.tx_seq_nr_acked = 0
                self.rx_seq_nr_expected = 0
            else:
                if self.verbose:
                    self._print_event(f"  WARNING: Expected seq={self.rx_seq_nr_expected}, got {hdr.seq_nr}")
                self._send_ack(addr, is_ack=False)
                return

        self.rx_seq_nr_expected = (self.rx_seq_nr_expected + 1) & 0xFFF
        self._local.session.rx_streaming = True

        # Immediate ACK - lets PS2's udprdma_send() return quickly,
        # so it can enter udprdma_recv() (5s timeout) while we process
        self._send_ack(addr, is_ack=True)

        if len(actual_payload) == 0:
            return

        msg_type = actual_payload[0]

        # File operations
        if msg_type == MsgType.OPEN_REQ:
            self._handle_open(addr, actual_payload)
        elif msg_type == MsgType.CLOSE_REQ:
            self._handle_close(addr, actual_payload)
        elif msg_type == MsgType.READ_REQ:
            self._handle_read(addr, actual_payload)
        elif msg_type == MsgType.WRITE_REQ:
            self._handle_write_req(addr, actual_payload)
        elif msg_type == MsgType.WRITE_DATA:
            self._handle_write_data(addr, actual_payload)
        elif msg_type == MsgType.LSEEK_REQ:
            self._handle_lseek(addr, actual_payload)
        elif msg_type == MsgType.DREAD_REQ:
            self._handle_dread(addr, actual_payload)
        elif msg_type == MsgType.GETSTAT_REQ:
            self._handle_getstat(addr, actual_payload)
        elif msg_type == MsgType.MKDIR_REQ:
            self._handle_mkdir(addr, actual_payload)
        elif msg_type == MsgType.REMOVE_REQ:
            self._handle_remove(addr, actual_payload)
        elif msg_type == MsgType.RMDIR_REQ:
            self._handle_rmdir(addr, actual_payload)
        # Block I/O operations (UDPBD subset)
        elif msg_type == MsgType.BREAD_REQ:
            self._handle_bread(addr, actual_payload)
        elif msg_type == MsgType.BWRITE_REQ:
            self._handle_bwrite_req(addr, actual_payload)
        else:
            self._print_event(f"[{addr[0]}:{addr[1]}] Unknown message type: 0x{msg_type:02x}")
            self._send_ack(addr, is_ack=True)

    # --- File operation handlers ---

    def _handle_open(self, addr: Tuple[str, int], payload: bytes):
        """Handle OPEN_REQ"""
        if len(payload) < 8:
            self._send_open_reply(addr, -errno.EINVAL)
            return

        _, is_dir, flags, mode = struct.unpack('<BBHi', payload[:8])
        path_bytes = payload[8:]
        path = path_bytes.split(b'\x00')[0].decode('utf-8', errors='replace')

        self.stats['open'] += 1

        resolved = self._resolve_path(path)
        
        # Check for compressed version when .iso is requested but file doesn't exist
        compressed_resolved = None
        if resolved is None or not os.path.exists(resolved):
            compressed_resolved = self._resolve_compressed_sibling(path)
        
        if resolved is None and compressed_resolved is None:
            self._print_event(f"[{addr[0]}:{addr[1]}] OPEN '{path}' -> EACCES (path traversal or no root_dir)")
            self._send_open_reply(addr, -errno.EACCES)
            return

        if is_dir:
            # Directory open
            try:
                entries = list(os.scandir(resolved))
                handle = self._alloc_handle({'entries': entries, 'index': 0}, is_dir=True)
                if handle < 0:
                    self._send_open_reply(addr, handle)
                    return
                st = os.stat(resolved)
                stat_info = self._stat_to_bytes(st)
                self._print_event(f"[{addr[0]}:{addr[1]}] DOPEN '{path}' -> handle={handle}")
                self._send_open_reply(addr, handle, stat_info=stat_info)
            except OSError as e:
                self._print_event(f"[{addr[0]}:{addr[1]}] DOPEN '{path}' -> error: {e}")
                self._send_open_reply(addr, -e.errno)
        else:
            # File open
            if self.read_only and (flags & 0x02):  # O_WRONLY or O_RDWR
                self._print_event(f"[{addr[0]}:{addr[1]}] OPEN '{path}' -> EACCES (read-only)")
                self._send_open_reply(addr, -errno.EACCES)
                return

            # Check if we should open compressed version
            actual_resolved = compressed_resolved if compressed_resolved else resolved
            
            # Check if the file exists (skip for O_CREAT)
            if not os.path.exists(actual_resolved) and not (flags & 0x0200):
                self._print_event(f"[{addr[0]}:{addr[1]}] OPEN '{path}' -> ENOENT (file not found)")
                self._send_open_reply(addr, -errno.ENOENT)
                return

            # Check if the file is a compressed format
            is_compressed = False
            if self.enable_compression:
                if actual_resolved.lower().endswith(COMPRESSED_EXTENSIONS):
                    is_compressed = True

            py_mode = self._flags_to_mode(flags, file_exists=os.path.exists(actual_resolved))
            try:
                # Create parent directories if O_CREAT and they don't exist
                if (flags & 0x0200) and not os.path.exists(os.path.dirname(actual_resolved)):
                    os.makedirs(os.path.dirname(actual_resolved), exist_ok=True)

                if is_compressed:
                    # Open compressed file with wrapper
                    wrapper = None
                    try:
                        wrapper = open_compressed(actual_resolved, self.compression_cache_size)
                    except (ImportError, ValueError, OSError, struct.error) as e:
                        self._print_event(f"[{addr[0]}:{addr[1]}] OPEN '{path}' -> compression error: {type(e).__name__}: {e}")
                    if wrapper is not None:
                        # Use wrapper for handle
                        st = os.stat(actual_resolved)
                        stat_info = self._get_compressed_stat(actual_resolved, st)
                        if stat_info is None:
                            stat_info = self._stat_to_bytes(st)
                        handle = self._alloc_handle(wrapper, is_dir=False)
                        if handle < 0:
                            wrapper.close()
                            self._send_open_reply(addr, handle)
                            return
                        self._print_event(f"[{addr[0]}:{addr[1]}] OPEN '{path}' -> handle={handle}, size={wrapper.uncompressed_size} (compressed)")
                        self._send_open_reply(addr, handle, stat_info=stat_info)
                        return
                    if compressed_resolved:
                        # The client asked for a virtual .iso we advertised; raw
                        # container bytes are not that file, so refuse rather than
                        # serve garbage to the PS2.
                        self._print_event(f"[{addr[0]}:{addr[1]}] OPEN '{path}' -> EIO (cannot decompress '{os.path.basename(actual_resolved)}')")
                        self._send_open_reply(addr, -errno.EIO)
                        return
                    # The client asked for the compressed file by its real name:
                    # fall back to serving the container bytes as-is.
                    f = open(actual_resolved, py_mode)
                    st = os.fstat(f.fileno())
                    stat_info = self._stat_to_bytes(st)
                    handle = self._alloc_handle(f, is_dir=False)
                else:
                    f = open(actual_resolved, py_mode)
                    st = os.fstat(f.fileno())
                    stat_info = self._stat_to_bytes(st)
                    handle = self._alloc_handle(f, is_dir=False)
                
                if handle < 0:
                    if 'f' in dir():
                        f.close()
                    self._send_open_reply(addr, handle)
                    return
                self._print_event(f"[{addr[0]}:{addr[1]}] OPEN '{path}' -> handle={handle}, size={st.st_size}")
                self._send_open_reply(addr, handle, stat_info=stat_info)
            except OSError as e:
                self._print_event(f"[{addr[0]}:{addr[1]}] OPEN '{path}' -> error: {e}")
                self._send_open_reply(addr, -e.errno)

    def _handle_close(self, addr: Tuple[str, int], payload: bytes):
        """Handle CLOSE_REQ"""
        if len(payload) < 8:
            self._send_close_reply(addr, -errno.EINVAL)
            return

        _, _, _, _, handle = struct.unpack('<BBBBi', payload[:8])

        self.stats['close'] += 1

        if handle == BLOCK_DEVICE_HANDLE:
            # Block device handle cannot be closed
            self._send_close_reply(addr, 0)
            return

        if handle not in self.handles:
            self._send_close_reply(addr, -errno.EBADF)
            return

        self._free_handle(handle)
        if self.verbose:
            self._print_event(f"[{addr[0]}:{addr[1]}] CLOSE handle={handle}")
        self._send_close_reply(addr, 0)

    def _handle_read(self, addr: Tuple[str, int], payload: bytes):
        """Handle READ_REQ - combined RESULT_REPLY header + raw data"""
        if len(payload) < 12:
            self._send_read_result(addr, -errno.EINVAL, b'')
            return

        _, _, _, _, handle, size = struct.unpack('<BBBBiI', payload[:12])

        self.stats['read'] += 1

        fh = self.handles.get(handle)
        if fh is None or fh.is_dir:
            self._send_read_result(addr, -errno.EBADF, b'')
            return

        try:
            data = fh.obj.read(size)
            bytes_read = len(data)

            if self.verbose:
                self._print_event(f"[{addr[0]}:{addr[1]}] READ handle={handle} size={size} -> {bytes_read} bytes")

            self.stats['bytes_read'] += bytes_read
            self._update_status()
            self._send_read_result(addr, bytes_read, data)

        except OSError as e:
            self._print_event(f"[{addr[0]}:{addr[1]}] READ error: {e}")
            self._send_read_result(addr, -e.errno, b'')

    def _handle_write_req(self, addr: Tuple[str, int], payload: bytes):
        """Handle WRITE_REQ - start of file write operation"""
        if len(payload) < 12:
            self._send_ack(addr, is_ack=True)
            return

        _, _, _, _, handle, size = struct.unpack('<BBBBiI', payload[:12])

        self.stats['write'] += 1

        if self.read_only:
            self._print_event(f"[{addr[0]}:{addr[1]}] WRITE -> EACCES (read-only)")
            self._send_write_done(addr, -errno.EACCES)
            return

        fh = self.handles.get(handle)
        if fh is None or fh.is_dir:
            self._send_write_done(addr, -errno.EBADF)
            return

        if self.verbose:
            self._print_event(f"[{addr[0]}:{addr[1]}] WRITE_REQ handle={handle} size={size}")

        # Initialize write state (file write mode)
        self.write_handle = handle
        self.write_is_block = False
        self.write_data = bytearray()
        self.write_total_chunks = 0
        self.write_received_chunks = 0

        # Check for inline WRITE_DATA (combined WRITE_REQ + first chunk)
        if len(payload) > 12:
            self._handle_write_data(addr, payload[12:])
        else:
            self._send_ack(addr, is_ack=True)

    def _handle_write_data(self, addr: Tuple[str, int], payload: bytes):
        """Handle WRITE_DATA chunk (shared between file write and block write)"""
        if len(payload) < 8:
            self._send_ack(addr, is_ack=True)
            return

        _, _, chunk_nr, chunk_size, total_chunks = struct.unpack('<BBHHH', payload[:8])
        chunk_data = payload[8:8 + chunk_size]

        if self.verbose:
            self._print_event(f"[{addr[0]}:{addr[1]}] WRITE_DATA chunk={chunk_nr}/{total_chunks} size={chunk_size}")

        if chunk_nr != self.write_received_chunks:
            self._print_event(f"  ERROR: Chunk order error (expected {self.write_received_chunks}, got {chunk_nr})")
            self._send_ack(addr, is_ack=True)
            return

        self.write_data.extend(chunk_data)
        self.write_total_chunks = total_chunks
        self.write_received_chunks += 1

        if self.write_received_chunks >= total_chunks:
            if self.write_is_block:
                self._complete_bwrite(addr)
            else:
                self._complete_write(addr)
        else:
            self._send_ack(addr, is_ack=True)

    def _complete_write(self, addr: Tuple[str, int]):
        """Complete a file write operation"""
        fh = self.handles.get(self.write_handle)
        if fh is None or fh.is_dir:
            self._send_write_done(addr, -errno.EBADF)
            return

        try:
            bytes_written = fh.obj.write(self.write_data)
            fh.obj.flush()
            self.stats['bytes_written'] += bytes_written

            if self.verbose:
                self._print_event(f"[{addr[0]}:{addr[1]}] WRITE handle={self.write_handle} -> {bytes_written} bytes")
            self._update_status()
            self._send_write_done(addr, bytes_written)
        except OSError as e:
            self._print_event(f"[{addr[0]}:{addr[1]}] WRITE error: {e}")
            self._send_write_done(addr, -e.errno)

    def _complete_bwrite(self, addr: Tuple[str, int]):
        """Complete a block write operation"""
        fh = self.handles.get(self.write_handle)
        if fh is None or fh.is_dir:
            self._send_write_done(addr, -errno.EBADF)
            return

        sector_size = self.bd_sector_size if self.write_handle == BLOCK_DEVICE_HANDLE else 512
        expected_size = self.write_sector_count * sector_size

        try:
            if self.write_handle == BLOCK_DEVICE_HANDLE:
                # Shared block device: positional write (no shared file position).
                self.bd_write(self.write_sector_nr * sector_size,
                              bytes(self.write_data[:expected_size]))
            else:
                fh.obj.seek(self.write_sector_nr * sector_size)
                fh.obj.write(self.write_data[:expected_size])
                fh.obj.flush()
            self.stats['bytes_written'] += expected_size

            if self.verbose:
                self._print_event(f"[{addr[0]}:{addr[1]}] BWRITE handle={self.write_handle} "
                      f"sector={self.write_sector_nr} count={self.write_sector_count}")
            self._update_status()
            self._send_write_done(addr, 0)
        except OSError as e:
            self._print_event(f"[{addr[0]}:{addr[1]}] BWRITE error: {e}")
            self._send_write_done(addr, -e.errno)

    def _handle_lseek(self, addr: Tuple[str, int], payload: bytes):
        """Handle LSEEK_REQ"""
        if len(payload) < 16:
            self._send_lseek_reply(addr, -errno.EINVAL)
            return

        _, whence, _, handle, offset_lo, offset_hi = struct.unpack('<BBHiii', payload[:16])
        offset = (offset_hi << 32) | (offset_lo & 0xFFFFFFFF)

        self.stats['lseek'] += 1

        fh = self.handles.get(handle)
        if fh is None or fh.is_dir:
            self._send_lseek_reply(addr, -errno.EBADF)
            return

        try:
            new_pos = fh.obj.seek(offset, whence)

            if self.verbose:
                self._print_event(f"[{addr[0]}:{addr[1]}] LSEEK handle={handle} offset={offset} whence={whence} -> {new_pos}")

            self._send_lseek_reply(addr, new_pos)
        except OSError as e:
            self._print_event(f"[{addr[0]}:{addr[1]}] LSEEK error: {e}")
            self._send_lseek_reply(addr, -e.errno)

    def _handle_dread(self, addr: Tuple[str, int], payload: bytes):
        """Handle DREAD_REQ"""
        if len(payload) < 8:
            self._send_dread_reply(addr, result=-errno.EINVAL)
            return

        _, _, _, _, handle = struct.unpack('<BBBBi', payload[:8])

        self.stats['dread'] += 1

        fh = self.handles.get(handle)
        if fh is None or not fh.is_dir:
            self._send_dread_reply(addr, result=-errno.EBADF)
            return

        dir_data = fh.obj
        if dir_data['index'] >= len(dir_data['entries']):
            if self.verbose:
                self._print_event(f"[{addr[0]}:{addr[1]}] DREAD handle={handle} -> end of dir")
            self._send_dread_reply(addr, result=0)
            return

        entry = dir_data['entries'][dir_data['index']]
        dir_data['index'] += 1

        try:
            st = entry.stat(follow_symlinks=False)
            
            # Check if this is a compressed file and transform display
            display_name = entry.name
            stat_info = self._stat_to_bytes(st)
            
            if self.enable_compression:
                # Present supported compressed files as .iso with their
                # uncompressed size (the transform is a no-op for other names)
                display_name = self._transform_compressed_name(entry.name)
                if display_name != entry.name:
                    compressed_stat = self._get_compressed_stat(entry.path, st)
                    if compressed_stat:
                        stat_info = compressed_stat

            if self.verbose:
                if display_name != entry.name:
                    self._print_event(f"[{addr[0]}:{addr[1]}] DREAD handle={handle} -> '{display_name}' (from {entry.name})")
                else:
                    self._print_event(f"[{addr[0]}:{addr[1]}] DREAD handle={handle} -> '{entry.name}'")

            self._send_dread_reply(addr, result=1, name=display_name, stat_info=stat_info)
        except OSError as e:
            self._print_event(f"[{addr[0]}:{addr[1]}] DREAD stat error: {e}")
            # Skip this entry and return end-of-dir
            self._send_dread_reply(addr, result=0)

    def _handle_getstat(self, addr: Tuple[str, int], payload: bytes):
        """Handle GETSTAT_REQ"""
        if len(payload) < 4:
            self._send_getstat_reply(addr, result=-errno.EINVAL)
            return

        path_bytes = payload[4:]
        path = path_bytes.split(b'\x00')[0].decode('utf-8', errors='replace')

        self.stats['getstat'] += 1

        # Empty path = block device capacity query (BD variant sends no path)
        if path == '' and BLOCK_DEVICE_HANDLE in self.handles:
            total_bytes = self.bd_sector_size * self.bd_sector_count
            stat_info = {
                'mode': 0, 'attr': 0,
                'size': total_bytes & 0xFFFFFFFF,
                'hisize': (total_bytes >> 32) & 0xFFFFFFFF,
                'ctime': b'\x00' * 8, 'atime': b'\x00' * 8, 'mtime': b'\x00' * 8,
            }
            if self.verbose:
                self._print_event(f"[{addr[0]}:{addr[1]}] GETSTAT '' -> block device {total_bytes} bytes")
            self._send_getstat_reply(addr, result=0, stat_info=stat_info)
            return

        resolved = self._resolve_path(path)
        if resolved is None or not os.path.exists(resolved):
            # Check for compressed version when .iso is requested
            compressed_resolved = self._resolve_compressed_sibling(path)
            if compressed_resolved:
                try:
                    st = os.stat(compressed_resolved)
                    compressed_stat = self._get_compressed_stat(compressed_resolved, st)
                    if compressed_stat:
                        if self.verbose:
                            self._print_event(f"[{addr[0]}:{addr[1]}] GETSTAT '{path}' -> compressed size={compressed_stat['size']} (from {os.path.basename(compressed_resolved)})")
                        self._send_getstat_reply(addr, result=0, stat_info=compressed_stat)
                        return
                except OSError:
                    pass
            self._send_getstat_reply(
                addr, result=-errno.EACCES if resolved is None else -errno.ENOENT)
            return

        try:
            st = os.stat(resolved)
            stat_info = self._stat_to_bytes(st)
            
            # Check if this is a compressed file
            if self.enable_compression:
                if resolved.lower().endswith(COMPRESSED_EXTENSIONS):
                    compressed_stat = self._get_compressed_stat(resolved, st)
                    if compressed_stat:
                        stat_info = compressed_stat

            if self.verbose:
                self._print_event(f"[{addr[0]}:{addr[1]}] GETSTAT '{path}' -> size={stat_info['size']}")

            self._send_getstat_reply(addr, result=0, stat_info=stat_info)
        except OSError as e:
            if self.verbose:
                self._print_event(f"[{addr[0]}:{addr[1]}] GETSTAT '{path}' -> error: {e}")
            self._send_getstat_reply(addr, result=-e.errno)

    def _handle_mkdir(self, addr: Tuple[str, int], payload: bytes):
        """Handle MKDIR_REQ"""
        if len(payload) < 4:
            self._send_result_reply(addr, -errno.EINVAL)
            return

        _, _, mode = struct.unpack('<BBH', payload[:4])
        path_bytes = payload[4:]
        path = path_bytes.split(b'\x00')[0].decode('utf-8', errors='replace')

        self.stats['mkdir'] += 1

        if self.read_only:
            self._send_result_reply(addr, -errno.EACCES)
            return

        resolved = self._resolve_path(path)
        if resolved is None:
            self._send_result_reply(addr, -errno.EACCES)
            return

        try:
            os.mkdir(resolved, mode if mode else 0o755)
            self._print_event(f"[{addr[0]}:{addr[1]}] MKDIR '{path}'")
            self._send_result_reply(addr, 0)
        except OSError as e:
            self._print_event(f"[{addr[0]}:{addr[1]}] MKDIR '{path}' -> error: {e}")
            self._send_result_reply(addr, -e.errno)

    def _handle_remove(self, addr: Tuple[str, int], payload: bytes):
        """Handle REMOVE_REQ"""
        if len(payload) < 4:
            self._send_result_reply(addr, -errno.EINVAL)
            return

        path_bytes = payload[4:]
        path = path_bytes.split(b'\x00')[0].decode('utf-8', errors='replace')

        self.stats['remove'] += 1

        if self.read_only:
            self._send_result_reply(addr, -errno.EACCES)
            return

        resolved = self._resolve_path(path)
        if resolved is None:
            self._send_result_reply(addr, -errno.EACCES)
            return

        try:
            os.remove(resolved)
            self._print_event(f"[{addr[0]}:{addr[1]}] REMOVE '{path}'")
            self._send_result_reply(addr, 0)
        except OSError as e:
            self._print_event(f"[{addr[0]}:{addr[1]}] REMOVE '{path}' -> error: {e}")
            self._send_result_reply(addr, -e.errno)

    def _handle_rmdir(self, addr: Tuple[str, int], payload: bytes):
        """Handle RMDIR_REQ"""
        if len(payload) < 4:
            self._send_result_reply(addr, -errno.EINVAL)
            return

        path_bytes = payload[4:]
        path = path_bytes.split(b'\x00')[0].decode('utf-8', errors='replace')

        self.stats['rmdir'] += 1

        if self.read_only:
            self._send_result_reply(addr, -errno.EACCES)
            return

        resolved = self._resolve_path(path)
        if resolved is None:
            self._send_result_reply(addr, -errno.EACCES)
            return

        try:
            os.rmdir(resolved)
            self._print_event(f"[{addr[0]}:{addr[1]}] RMDIR '{path}'")
            self._send_result_reply(addr, 0)
        except OSError as e:
            self._print_event(f"[{addr[0]}:{addr[1]}] RMDIR '{path}' -> error: {e}")
            self._send_result_reply(addr, -e.errno)

    # --- Block I/O handlers (UDPBD subset) ---

    def _handle_bread(self, addr: Tuple[str, int], payload: bytes):
        """Handle BREAD_REQ - unified RESULT_REPLY + raw data response"""
        if len(payload) < 16:
            self._send_read_result(addr, -errno.EINVAL, b'')
            return

        # msg_type(1) + reserved(1) + sector_count(2) + handle(4) + sector_nr_lo(4) + sector_nr_hi(4)
        _, _, sector_count, handle, sector_nr_lo, sector_nr_hi = \
            struct.unpack('<BBHiII', payload[:16])
        sector_nr = sector_nr_lo | (sector_nr_hi << 32)

        self.stats['bread'] += 1

        fh = self.handles.get(handle)
        if fh is None or fh.is_dir:
            self._send_read_result(addr, -errno.EBADF, b'')
            return

        sector_size = self.bd_sector_size if handle == BLOCK_DEVICE_HANDLE else 512
        total_size = sector_count * sector_size

        if self.verbose:
            self._print_event(f"[{addr[0]}:{addr[1]}] BREAD handle={handle} sector={sector_nr} count={sector_count} ({total_size} bytes)")

        try:
            if handle == BLOCK_DEVICE_HANDLE:
                # Shared block device: positional read (no shared file position).
                data = self.bd_read(sector_nr * sector_size, total_size)
            else:
                fh.obj.seek(sector_nr * sector_size)
                data = fh.obj.read(total_size)
        except OSError as e:
            self._print_event(f"[{addr[0]}:{addr[1]}] BREAD error: {e}")
            self._send_read_result(addr, -e.errno, b'')
            return

        self.stats['bytes_read'] += len(data)
        self._update_status()

        self._send_read_result(addr, len(data), data)

    def _handle_bwrite_req(self, addr: Tuple[str, int], payload: bytes):
        """Handle BWRITE_REQ - start of block write operation"""
        if len(payload) < 16:
            self._send_ack(addr, is_ack=True)
            return

        # msg_type(1) + reserved(1) + sector_count(2) + handle(4) + sector_nr_lo(4) + sector_nr_hi(4)
        _, _, sector_count, handle, sector_nr_lo, sector_nr_hi = \
            struct.unpack('<BBHiII', payload[:16])
        sector_nr = sector_nr_lo | (sector_nr_hi << 32)

        self.stats['bwrite'] += 1

        if self.read_only:
            self._print_event(f"[{addr[0]}:{addr[1]}] BWRITE -> EACCES (read-only)")
            self._send_write_done(addr, -errno.EACCES)
            return

        fh = self.handles.get(handle)
        if fh is None or fh.is_dir:
            self._send_write_done(addr, -errno.EBADF)
            return

        if self.verbose:
            self._print_event(f"[{addr[0]}:{addr[1]}] BWRITE_REQ handle={handle} sector={sector_nr} count={sector_count}")

        # Initialize write state (block write mode)
        self.write_handle = handle
        self.write_is_block = True
        self.write_sector_nr = sector_nr
        self.write_sector_count = sector_count
        self.write_data = bytearray()
        self.write_total_chunks = 0
        self.write_received_chunks = 0

        # Check for inline WRITE_DATA (combined BWRITE_REQ + first chunk)
        if len(payload) > 16:
            self._handle_write_data(addr, payload[16:])
        else:
            self._send_ack(addr, is_ack=True)

    # --- Send helpers ---

    def _info_payload(self) -> bytes:
        """Name + shares trailer that Modulo's server appends to its INFORM.

        Byte-for-byte parity with it: length-prefixed name, length-prefixed shares,
        padded to a multiple of 4 because the PS2 reads the SMAP RX FIFO in 32-bit
        words. Only sent in modulo_compat -- udpfsd and every conformant client
        expect a bare 6-byte INFORM.
        """
        # Sliced in chars like Modulo's, but shares is sliced AFTER encoding: 95
        # chars of 4-byte UTF-8 is 380 bytes and bytes([380]) raises. Modulo's has
        # that landmine too (its GUI wrapper is what fills shares in), and matching
        # a crash is not parity worth having. name needs no such guard -- 31 chars
        # cannot exceed 124 bytes -- and byte-slicing it would only add a way to
        # split a character mid-sequence.
        name = (self.server_name or '')[:31].encode('utf-8', 'ignore')
        shares_str = ', '.join(self.share_names) if self.share_names else ''
        shares = shares_str.encode('utf-8', 'ignore')[:95]
        payload = bytes([len(name)]) + name + bytes([len(shares)]) + shares
        if len(payload) % 4:
            payload += b'\x00' * (4 - len(payload) % 4)
        return payload

    def _send_inform(self, addr: Tuple[str, int], sess=None):
        """Send INFORM packet"""
        if self.modulo_compat and sess is not None:
            # Modulo's server informs from its running tx_seq_nr, leaves the second
            # DiscHeader field 0 (its client takes the data port from the INFORM's
            # UDP source port, as udpfsd documents), and appends the name trailer.
            hdr = Header(packet_type=PacketType.INFORM, seq_nr=sess.tx_seq_nr)
            disc = DiscHeader(service_id=UDPRDMA_SVC_UDPFS, port=0)
            self._sendto(hdr.pack() + disc.pack() + self._info_payload(), addr)
            sess.tx_seq_nr = (sess.tx_seq_nr + 1) & 0xFFF
            sess.tx_seq_nr_acked = (sess.tx_seq_nr - 1) & 0xFFF  # the INFORM we just sent
            return

        hdr = Header(packet_type=PacketType.INFORM, seq_nr=1) # INFORM sequence number is always 1
        disc = DiscHeader(service_id=UDPRDMA_SVC_UDPFS, port = self.dsock.getsockname()[1])

        packet = hdr.pack() + disc.pack()
        self._sendto(packet, addr)

    def _send_ack(self, addr: Tuple[str, int], is_ack: bool = True):
        """Send ACK or NACK packet"""
        hdr = Header(packet_type=PacketType.DATA, seq_nr=self.tx_seq_nr)
        data_hdr = DataHeader(
            seq_nr_ack=(self.rx_seq_nr_expected - 1) & 0xFFF if is_ack else self.rx_seq_nr_expected,
            flags=DataFlags.ACK if is_ack else 0,
            hdr_word_count=0,
            data_byte_count=0
        )

        packet = hdr.pack() + data_hdr.pack()
        self._sendto(packet, addr)

    def _send_data(self, addr: Tuple[str, int], payload: bytes):
        """Send DATA packet with payload (single packet, always FIN) and confirm receipt."""
        padded_size = (len(payload) + 3) & ~3
        padded_payload = payload.ljust(padded_size, b'\x00')

        hdr = Header(packet_type=PacketType.DATA, seq_nr=self.tx_seq_nr)
        data_hdr = DataHeader(
            seq_nr_ack=(self.rx_seq_nr_expected - 1) & 0xFFF,
            flags=DataFlags.ACK | DataFlags.FIN,
            hdr_word_count=0,
            data_byte_count=padded_size
        )

        packet = hdr.pack() + data_hdr.pack() + padded_payload
        self.tx_buffer = [(self.tx_seq_nr, packet)]
        self._sendto(packet, addr)
        self.tx_seq_nr = (self.tx_seq_nr + 1) & 0xFFF
        self._wait_for_final_ack(addr)

    def _send_data_packet(self, addr: Tuple[str, int], payload: bytes,
                          fin: bool = False, hdr_size: int = 0):
        """Send DATA packet and store for retransmit.

        If hdr_size > 0, the first hdr_size bytes of payload are treated as
        an app-level header (encoded via hdr_word_count in the UDPRDMA header).
        """
        data_size = len(payload) - hdr_size
        padded_data_size = (data_size + 3) & ~3
        padded_payload = payload[:hdr_size] + payload[hdr_size:].ljust(padded_data_size, b'\x00')

        hdr = Header(packet_type=PacketType.DATA, seq_nr=self.tx_seq_nr)
        data_hdr = DataHeader(
            seq_nr_ack=(self.rx_seq_nr_expected - 1) & 0xFFF,
            flags=DataFlags.ACK | (DataFlags.FIN if fin else 0),
            hdr_word_count=hdr_size // 4,
            data_byte_count=padded_data_size
        )

        packet = hdr.pack() + data_hdr.pack() + padded_payload

        self.tx_buffer.append((self.tx_seq_nr, packet))

        self._sendto(packet, addr)
        self.tx_seq_nr = (self.tx_seq_nr + 1) & 0xFFF

    def _retransmit_from(self, addr: Tuple[str, int], from_seq: int):
        """Retransmit packets starting from sequence number"""
        count = 0
        for seq, packet in self.tx_buffer:
            seq_diff = (seq - from_seq) & 0xFFF
            if seq_diff < 2048:
                self._sendto(packet, addr)
                count += 1
        if self.verbose and count > 0:
            self._print_event(f"  Retransmitted {count} packets from seq={from_seq}")

    def _optimal_chunk_size(self, total_bytes: int) -> int:
        """Choose chunk size for DMA efficiency"""
        candidates = [
            (1024, 512),
            (1280, 256),
            (1408, 128),
        ]

        best_chunk = 1408
        best_packets = math.ceil(total_bytes / 1408)
        best_align = 128

        for max_chunk, alignment in candidates:
            packets = math.ceil(total_bytes / max_chunk)
            if (packets < best_packets or
                (packets == best_packets and alignment > best_align)):
                best_packets = packets
                best_chunk = max_chunk
                best_align = alignment

        return best_chunk

    def _wait_for_window_ack(self, addr: Tuple[str, int]):
        """Wait for a window ACK/NACK during a multi-packet send. Reads from this
        client's session queue (the demux routes the client's ACKs there), so one
        client's wait never blocks another. Updates tx_seq_nr_acked; retransmits on
        NACK; retransmits unacked on timeout."""
        sess = self._local.session
        while True:
            try:
                pkt, _recv_addr = sess.queue.get(timeout=WINDOW_ACK_TIMEOUT)
            except queue.Empty:
                if self.tx_buffer:
                    self._retransmit_from(addr, self.tx_buffer[0][0])
                return
            if pkt is None:
                return  # session shutting down
            if len(pkt) < 6:
                continue
            hdr = Header.unpack(pkt)
            if hdr.packet_type != PacketType.DATA:
                continue
            data_hdr = DataHeader.unpack(pkt[2:6])
            if data_hdr.data_byte_count > 0 or data_hdr.hdr_word_count > 0:
                continue  # Not an ACK/NACK packet
            if data_hdr.flags & DataFlags.ACK:
                # Window ACK - advance acked position
                self.tx_seq_nr_acked = data_hdr.seq_nr_ack
                self.tx_buffer = [
                    (seq, p) for seq, p in self.tx_buffer
                    if ((seq - data_hdr.seq_nr_ack - 1) & 0xFFF) < 2048
                ]
                return
            else:
                # NACK - retransmit and keep waiting for the confirming ACK.
                self.tx_seq_nr_acked = (data_hdr.seq_nr_ack - 1) & 0xFFF
                self._retransmit_from(addr, data_hdr.seq_nr_ack)

    def _in_flight(self) -> int:
        """Number of unacknowledged packets in flight"""
        return (self.tx_seq_nr - self.tx_seq_nr_acked - 1) & 0xFFF

    def _wait_for_ack(self, addr: Tuple[str, int], timeout: float = 5.0) -> bool:
        """Wait for ACK that confirms the FIN packet was received.
        Mid-stream window ACKs (seq_nr_ack < fin_seq) are handled but do
        not complete the wait — only an ACK covering the FIN does."""
        sess = self._local.session
        fin_seq = (self.tx_seq_nr - 1) & 0xFFF
        while True:
            try:
                pkt, _recv_addr = sess.queue.get(timeout=timeout)
            except queue.Empty:
                return False
            if pkt is None:
                return False  # session shutting down
            if len(pkt) < 6:
                continue
            hdr = Header.unpack(pkt)
            if hdr.packet_type != PacketType.DATA:
                continue
            data_hdr = DataHeader.unpack(pkt[2:6])
            if data_hdr.data_byte_count == 0 and data_hdr.hdr_word_count == 0:
                if data_hdr.flags & DataFlags.ACK:
                    # Always advance acked position and prune tx_buffer
                    self.tx_seq_nr_acked = data_hdr.seq_nr_ack
                    if self.tx_buffer:
                        self.tx_buffer = [
                            (seq, p) for seq, p in self.tx_buffer
                            if ((seq - data_hdr.seq_nr_ack - 1) & 0xFFF) < 2048
                        ]
                    # Only accept as final ACK if it covers the FIN packet
                    if data_hdr.seq_nr_ack == fin_seq:
                        self.tx_buffer = []
                        return True
                    # else: mid-stream window ACK — keep waiting
                else:
                    # NACK - retransmit and keep waiting
                    self._retransmit_from(addr, data_hdr.seq_nr_ack)

    def _wait_for_final_ack(self, addr: Tuple[str, int]):
        """Confirm transfer completion: wait for ACK, retransmit on NACK or timeout.
        Senders must always confirm receipt — single packet or stream."""
        for attempt in range(MAX_WINDOW_RETRIES + 1):
            if self._wait_for_ack(addr, timeout=WINDOW_ACK_TIMEOUT):
                return  # Transfer confirmed
            if not self.tx_buffer:
                return
            start_seq = self.tx_buffer[0][0]
            if self.verbose:
                self._print_event(
                    f"  Final ACK timeout, retransmit from seq={start_seq} (attempt {attempt+1})")
            self._retransmit_from(addr, start_seq)

    def _send_raw_data(self, addr: Tuple[str, int], data: bytes):
        """Send raw data as UDPRDMA multi-packet transfer with flow control"""
        self.tx_buffer = []
        self.tx_start_seq = self.tx_seq_nr

        max_chunk = self._optimal_chunk_size(len(data))

        offset = 0
        window_retries = 0
        while offset < len(data):
            # Flow control: wait if send window is full
            if self._in_flight() >= SEND_WINDOW:
                old_acked = self.tx_seq_nr_acked
                self._wait_for_window_ack(addr)
                if self.tx_seq_nr_acked == old_acked:
                    window_retries += 1
                    if window_retries >= MAX_WINDOW_RETRIES:
                        self._print_event("  Window ACK retries exhausted, aborting transfer")
                        return
                else:
                    window_retries = 0
                continue

            window_retries = 0
            chunk_size = min(max_chunk, len(data) - offset)
            chunk_data = data[offset:offset + chunk_size]
            is_last = (offset + chunk_size >= len(data))

            self._send_data_packet(addr, chunk_data, fin=is_last)
            offset += chunk_size

        self._wait_for_final_ack(addr)

    def _send_raw_data_with_header(self, addr: Tuple[str, int],
                                   header: bytes, data: bytes):
        """Send raw data with app header on first packet, with flow control"""
        self.tx_buffer = []
        self.tx_start_seq = self.tx_seq_nr

        max_chunk = self._optimal_chunk_size(len(data))

        # First packet: header + data (cap data to fit in max payload)
        first_data_max = min(max_chunk, MAX_DATA_PAYLOAD - len(header))
        first_chunk_size = min(first_data_max, len(data))
        is_last = (first_chunk_size >= len(data))
        self._send_data_packet(addr, header + data[:first_chunk_size],
                               fin=is_last, hdr_size=len(header))

        # Remaining packets: data only
        offset = first_chunk_size
        window_retries = 0
        while offset < len(data):
            # Flow control: wait if send window is full
            if self._in_flight() >= SEND_WINDOW:
                old_acked = self.tx_seq_nr_acked
                self._wait_for_window_ack(addr)
                if self.tx_seq_nr_acked == old_acked:
                    window_retries += 1
                    if window_retries >= MAX_WINDOW_RETRIES:
                        self._print_event("  Window ACK retries exhausted, aborting transfer")
                        return
                else:
                    window_retries = 0
                continue

            window_retries = 0
            chunk_size = min(max_chunk, len(data) - offset)
            is_last = (offset + chunk_size >= len(data))
            self._send_data_packet(addr, data[offset:offset + chunk_size],
                                   fin=is_last)
            offset += chunk_size

        self._wait_for_final_ack(addr)

    # --- Response builders ---

    def _send_open_reply(self, addr: Tuple[str, int], handle: int,
                         stat_info: Optional[dict] = None):
        """Send OPEN_REPLY"""
        if stat_info is None:
            stat_info = {'mode': 0, 'size': 0, 'hisize': 0,
                        'ctime': b'\x00' * 8, 'mtime': b'\x00' * 8}

        reply = struct.pack('<BBBBiIII',
            MsgType.OPEN_REPLY, 0, 0, 0,
            handle,
            stat_info['mode'],
            stat_info['size'],
            stat_info['hisize'],
        ) + stat_info['ctime'] + stat_info['mtime']

        self._send_data(addr, reply)

    def _send_close_reply(self, addr: Tuple[str, int], result: int):
        """Send CLOSE_REPLY"""
        reply = struct.pack('<BBBBi', MsgType.CLOSE_REPLY, 0, 0, 0, result)
        self._send_data(addr, reply)

    def _send_read_result(self, addr: Tuple[str, int], result: int, data: bytes):
        """Send READ response: RESULT_REPLY as app header + optional data"""
        result_reply = struct.pack('<BBBBi', MsgType.RESULT_REPLY, 0, 0, 0, result)
        self._send_raw_data_with_header(addr, result_reply, data)

    def _send_result_reply(self, addr: Tuple[str, int], result: int):
        """Send RESULT_REPLY"""
        reply = struct.pack('<BBBBi', MsgType.RESULT_REPLY, 0, 0, 0, result)
        self._send_data(addr, reply)

    def _send_write_done(self, addr: Tuple[str, int], result: int):
        """Send WRITE_DONE"""
        reply = struct.pack('<BBBBi', MsgType.WRITE_DONE, 0, 0, 0, result)
        self._send_data(addr, reply)

    def _send_lseek_reply(self, addr: Tuple[str, int], position: int):
        """Send LSEEK_REPLY"""
        if position < 0:
            # Error code: pack as signed
            reply = struct.pack('<BBBBii', MsgType.LSEEK_REPLY, 0, 0, 0, position, -1)
        else:
            # Position: pack as unsigned to handle values > 2GB
            pos_lo = position & 0xFFFFFFFF
            pos_hi = (position >> 32) & 0xFFFFFFFF
            reply = struct.pack('<BBBBII', MsgType.LSEEK_REPLY, 0, 0, 0, pos_lo, pos_hi)
        self._send_data(addr, reply)

    def _send_dread_reply(self, addr: Tuple[str, int], result: int,
                          name: Optional[str] = None,
                          stat_info: Optional[dict] = None):
        """Send DREAD_REPLY"""
        if name is None:
            name = ''
        if stat_info is None:
            stat_info = {'mode': 0, 'attr': 0, 'size': 0, 'hisize': 0,
                        'ctime': b'\x00' * 8, 'atime': b'\x00' * 8,
                        'mtime': b'\x00' * 8}

        name_bytes = name.encode('utf-8') + b'\x00'
        name_len = len(name_bytes) - 1  # Exclude null terminator from length

        # Pad name to 4-byte boundary
        padded_name_len = (len(name_bytes) + 3) & ~3
        name_padded = name_bytes.ljust(padded_name_len, b'\x00')

        reply = struct.pack('<BBHiIIII',
            MsgType.DREAD_REPLY, 0,
            name_len,
            result,
            stat_info['mode'],
            stat_info['attr'],
            stat_info['size'],
            stat_info['hisize'],
        ) + stat_info['ctime'] + stat_info['atime'] + stat_info['mtime']

        if result > 0:
            reply += name_padded

        self._send_data(addr, reply)

    def _send_getstat_reply(self, addr: Tuple[str, int], result: int,
                            stat_info: Optional[dict] = None):
        """Send GETSTAT_REPLY"""
        if stat_info is None:
            stat_info = {'mode': 0, 'attr': 0, 'size': 0, 'hisize': 0,
                        'ctime': b'\x00' * 8, 'atime': b'\x00' * 8,
                        'mtime': b'\x00' * 8}

        reply = struct.pack('<BBBBiIIII',
            MsgType.GETSTAT_REPLY, 0, 0, 0,
            result,
            stat_info['mode'],
            stat_info['attr'],
            stat_info['size'],
            stat_info['hisize'],
        ) + stat_info['ctime'] + stat_info['atime'] + stat_info['mtime']

        self._send_data(addr, reply)

    def _print_stats(self):
        """Print final statistics summary"""
        elapsed = time.monotonic() - self._start_time
        h, rem = divmod(int(elapsed), 3600)
        m, s = divmod(rem, 60)

        print()
        print(f"Session: {h:02d}:{m:02d}:{s:02d}")
        print()

        # Operations table
        ops = [(k, v) for k, v in self.stats.items()
               if k not in ('bytes_read', 'bytes_written') and v > 0]
        if ops:
            print("Operations:")
            for key, val in ops:
                print(f"  {key:<12s} {val:>10,}")

        # Throughput
        total_read = self.stats['bytes_read']
        total_written = self.stats['bytes_written']
        if total_read > 0 or total_written > 0:
            print()
            print("Transfer:")
            if total_read > 0:
                rate = total_read / elapsed if elapsed > 0 else 0
                print(f"  read         {self._format_bytes(total_read):>10s}  ({self._format_bytes(int(rate))}/s)")
            if total_written > 0:
                rate = total_written / elapsed if elapsed > 0 else 0
                print(f"  written      {self._format_bytes(total_written):>10s}  ({self._format_bytes(int(rate))}/s)")


class Session:
    """Per-client protocol state plus a worker thread that runs the server's
    request handlers for exactly one peer. Created on first contact and reaped
    after SESSION_TIMEOUT of inactivity. All per-client fields mirror the initial
    values the single-client server used, so handler logic is unchanged."""

    def __init__(self, server: 'UdpfsServer', addr):
        self.server = server
        self.addr = addr
        # per-client protocol state
        self.peer_addr = addr
        self.tx_seq_nr = 0
        self.tx_seq_nr_acked = 0
        self.rx_seq_nr_expected = 0
        self.tx_buffer: List[Tuple[int, bytes]] = []
        self.tx_start_seq = 0
        # handle 0 = the shared block device (same FileHandle across sessions;
        # concurrent access uses server.bd_read/bd_write positional I/O).
        self.handles: Dict[int, FileHandle] = {}
        if server.bd_fh is not None:
            self.handles[BLOCK_DEVICE_HANDLE] = server.bd_fh
        self.next_handle = 1
        self.write_handle = -1
        self.write_is_block = False
        self.write_sector_nr = 0
        self.write_sector_count = 0
        self.write_data = bytearray()
        self.write_total_chunks = 0
        self.write_received_chunks = 0
        # True once this peer's data stream is running. Only modulo_compat reads
        # it: its client keeps a background DISCOVERY going while it streams, and
        # a live stream must not be resynced out from under itself.
        self.rx_streaming = False
        # concurrency plumbing
        self.queue: "queue.Queue" = queue.Queue()
        self.last_activity = time.monotonic()
        self._closing = False
        self._thread = threading.Thread(
            target=self._run, name=f"udpfs-{addr[0]}:{addr[1]}", daemon=True)

    def start(self):
        self._thread.start()

    def shutdown(self):
        self._closing = True
        self.queue.put(None)  # wake the worker if it is blocked on get()

    def _run(self):
        # Bind this worker thread to its session so the server's _session_prop
        # proxies resolve self.tx_seq_nr / self.handles / ... to THIS client.
        self.server._local.session = self
        while not self._closing and not self.server._shutdown:
            try:
                item = self.queue.get(timeout=1.0)
            except queue.Empty:
                continue
            if item is None:
                break
            data, addr = item
            try:
                self.server._handle_data(data, addr)
            except Exception as e:  # keep the session alive on a handler error
                # Always surface handler errors -- silent swallowing makes
                # multi-client issues very hard to debug.
                self.server._print_event(
                    f"[{addr[0]}:{addr[1]}] session error: {type(e).__name__}: {e}")
        # Close this session's own file handles (never the shared block device).
        for hid, fh in list(self.handles.items()):
            if hid == BLOCK_DEVICE_HANDLE:
                continue
            try:
                fh.close()
            except Exception:
                pass
        self.handles.clear()


def _env_bool(name, default=False):
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ('1', 'true', 'yes', 'on')


def _env_int(name, default):
    v = os.environ.get(name)
    return int(v, 0) if v else default


def _env_float(name, default):
    v = os.environ.get(name)
    return float(v) if v else default


def main():
    parser = argparse.ArgumentParser(
        description='UDPFS Server - Unified file and block device server over UDPRDMA',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    # Every option also reads an environment variable (container-friendly, like
    # udpfsd); the CLI flag overrides the env var, which overrides the default.
    parser.add_argument(
        '--block-device', '-b', default=os.environ.get('BDPATH'),
        help='Block device or disk image to serve as handle 0 (env: BDPATH)'
    )
    parser.add_argument(
        '--root-dir', '-d', default=os.environ.get('FSROOT'),
        help='Root directory to serve files from (env: FSROOT)'
    )
    parser.add_argument(
        '--port', '-p', type=lambda x: int(x, 0),
        default=_env_int('PORT', UDPFS_PORT),
        help=f'UDP port to listen on (default: 0x{UDPFS_PORT:04X}; env: PORT)'
    )
    parser.add_argument(
        '--bind', '-i', default=os.environ.get('BIND', ''), metavar='IP',
        help='IP address to bind/listen on (default: all interfaces; env: BIND)'
    )
    parser.add_argument(
        '--sector-size', '-s', type=int, default=_env_int('SECTOR_SIZE', 512),
        help='Sector size for block device (default: 512; env: SECTOR_SIZE)'
    )
    parser.add_argument(
        '--read-only', '-r', action='store_true', default=_env_bool('RO'),
        help='Serve in read-only mode (env: RO)'
    )
    parser.add_argument(
        '--verbose', '-v', action='store_true', default=_env_bool('VERBOSE'),
        help='Verbose output (env: VERBOSE)'
    )
    parser.add_argument(
        '--enable-compression', '-c', action='store_true',
        default=_env_bool('ENABLE_COMPRESSION'),
        help='Enable transparent decompression of .zso (LZ4), .cso (zlib), and .chd (CHD v5) '
             'files. Compressed files appear as .iso in directory listings. (env: ENABLE_COMPRESSION)'
    )
    parser.add_argument(
        '--compression-cache-size', type=int, default=_env_int('COMPRESSION_CACHE_SIZE', 32),
        help='Number of decompressed blocks to cache per file (default: 32; env: COMPRESSION_CACHE_SIZE)'
    )
    parser.add_argument(
        '--data-port', type=lambda x: int(x, 0), default=_env_int('DATA_PORT', 0),
        help='Pin the UDP data port instead of using an ephemeral one (default: 0 = '
             'auto). Use when the data endpoint must be predictable for a manual '
             'firewall rule, port forwarding, or NAT. Ignored with --single-port '
             '(env: DATA_PORT)'
    )
    parser.add_argument(
        '--single-port', action='store_true', default=_env_bool('SINGLE_PORT'),
        help='Serve DISCOVERY and DATA on the one discovery port instead of handing '
             'clients off to a separate data port. Compatibility mode for clients that '
             'cannot follow the standard two-port UDPFS handshake (env: SINGLE_PORT)'
    )
    parser.add_argument(
        '--modulo-mode', action='store_true', default=_env_bool('MODULO_MODE'),
        help='Compatibility mode for Modulo, whose client only ever worked against '
             'the patched single-socket server bundled in its own repo: serve on one '
             'port, resync the peer sequence off its DISCOVERY, and send that '
             "server's INFORM (running seq_nr, zeroed port field, name trailer). "
             'Implies --single-port. Deviates from udpfsd on purpose -- leave it off '
             'for every other client (env: MODULO_MODE)'
    )
    parser.add_argument(
        '--peer-timeout', type=float, default=_env_float('PEER_TIMEOUT', SESSION_TIMEOUT),
        help=f'Seconds of client inactivity before its session is reaped, closing '
             f'the files it had open. Clamped to '
             f'{int(SESSION_TIMEOUT_MIN)}-{int(SESSION_TIMEOUT_MAX)}; 0 does not '
             f'disable it (there is nothing to disable -- a stranded session holds '
             f'a thread and its handles until the server exits) '
             f'(default: {int(SESSION_TIMEOUT)}; env: PEER_TIMEOUT)'
    )
    parser.add_argument(
        '--metrics', action='store_true', default=_env_bool('METRICS'),
        help='Periodically log transfer/op statistics (env: METRICS)'
    )
    parser.add_argument(
        '--metrics-period', type=float, default=_env_float('METRICS_PERIOD', 60.0),
        help='Seconds between metrics log lines (default: 60; env: METRICS_PERIOD)'
    )

    args = parser.parse_args()

    if not args.block_device and not args.root_dir:
        parser.error("At least one of --block-device or --root-dir is required")

    if args.root_dir and not os.path.isdir(args.root_dir):
        print(f"Error: '{args.root_dir}' is not a directory")
        sys.exit(1)

    if args.block_device and not os.path.exists(args.block_device):
        print(f"Error: '{args.block_device}' not found")
        sys.exit(1)

    # Check LZ4 availability if compression is enabled
    if args.enable_compression and not LZ4_AVAILABLE:
        print("Warning: LZ4 library not available. ZSO files will not be listed or decompressed.")
        print("Install with: pip install lz4")
    if args.enable_compression and not LIBCHDR_AVAILABLE:
        print("Warning: libchdr not found. CHD files will not be listed or decompressed.")
        print("Install with: apt install libchdr0")

    server = UdpfsServer(
        root_dir=args.root_dir,
        block_device=args.block_device,
        port=args.port,
        bind_ip=args.bind,
        sector_size=args.sector_size,
        read_only=args.read_only,
        verbose=args.verbose,
        enable_compression=args.enable_compression,
        compression_cache_size=args.compression_cache_size,
        peer_timeout=args.peer_timeout,
        metrics=args.metrics,
        metrics_period=args.metrics_period,
        single_port=args.single_port,
        modulo_compat=args.modulo_mode,
        data_port=args.data_port
    )
    server.run()


if __name__ == '__main__':
    main()
