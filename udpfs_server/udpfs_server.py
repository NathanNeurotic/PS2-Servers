#!/usr/bin/env python3
# SPDX-License-Identifier: AFL-3.0
#
# UDPFS server redistributed from Rick Gaiser's Neutrino project.
# Upstream: https://github.com/rickgaiser/neutrino/blob/master/pc/udpfs_server.py
# License: Academic Free License 3.0. See repository LICENSE and NOTICE.md.
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
"""

import argparse
import errno
import gzip
import math
import os
import select
import socket
import struct
import sys
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
MAX_HANDLES = 32

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
        cache_size: Optional cache size override
    
    Returns:
        CompressedFileWrapper or None if not compressed/unsupported
    """
    ext = os.path.splitext(file_path)[1].lower()
    try:
        if ext == '.zso' and LZ4_AVAILABLE:
            return ZsoFileWrapper(file_path, cache_size)
        elif ext == '.cso':
            return CsoFileWrapper(file_path, cache_size)
        elif ext == '.chd' and LIBCHDR_AVAILABLE:
            return ChdFileWrapper(file_path, cache_size)
    except Exception as e:
        print(f"Warning: Failed to open compressed file {file_path}: {e}", file=sys.stderr)
    return None


def is_compressed_image(filename: str) -> bool:
    """Check if filename is a supported compressed image."""
    ext = os.path.splitext(filename)[1].lower()
    return ext in ('.zso', '.cso', '.chd')


def display_name_for_compressed(filename: str) -> str:
    """Convert compressed image filename to .iso for directory listing."""
    base, ext = os.path.splitext(filename)
    if ext.lower() in ('.zso', '.cso', '.chd'):
        return base + '.iso'
    return filename


def get_compressed_info(file_path: str) -> Tuple[int, int]:
    """Get uncompressed size and block size for compressed image without full wrapper.
    
    Returns:
        (uncompressed_size, block_size) or (file_size, 2048) fallback
    """
    ext = os.path.splitext(file_path)[1].lower()
    try:
        if ext == '.zso' and LZ4_AVAILABLE:
            with open(file_path, 'rb') as f:
                header = f.read(24)
                if len(header) >= 24 and header[0:4] in (ZSO_MAGIC, b'ZISO'):
                    uncompressed_size = struct.unpack('<Q', header[8:16])[0]
                    block_size = struct.unpack('<I', header[16:20])[0]
                    return uncompressed_size, block_size
        elif ext == '.cso':
            with open(file_path, 'rb') as f:
                header = f.read(24)
                if len(header) >= 24 and struct.unpack('<I', header[0:4])[0] == CSO_MAGIC:
                    uncompressed_size = struct.unpack('<Q', header[8:16])[0]
                    block_size = struct.unpack('<I', header[16:20])[0]
                    return uncompressed_size, block_size
        elif ext == '.chd' and LIBCHDR_AVAILABLE:
            # Need to open with wrapper to get size
            wrapper = ChdFileWrapper(file_path, cache_size=1)
            size = wrapper.size
            wrapper.close()
            return size, 2048
    except Exception:
        pass
    # Fallback: file size as-is
    return os.path.getsize(file_path), 2048


@dataclass
class FileHandle:
    path: str
    file_obj: object  # Can be regular file or CompressedFileWrapper
    flags: int
    position: int = 0
    is_compressed: bool = False

    def read(self, size: int) -> bytes:
        if self.is_compressed:
            data = self.file_obj.read(size)
        else:
            data = self.file_obj.read(size)
        return data

    def seek(self, offset: int, whence: int = 0):
        self.file_obj.seek(offset, whence)
        self.position = self.file_obj.tell()

    def tell(self) -> int:
        return self.file_obj.tell()

    def close(self):
        self.file_obj.close()


class UDPFSServer:
    def __init__(self, root_dir: Optional[str] = None, block_device: Optional[str] = None,
                 port: int = UDPFS_PORT, bind_ip: str = '0.0.0.0', read_only: bool = False,
                 verbose: bool = False, enable_compression: bool = False):
        self.root_dir = os.path.abspath(root_dir) if root_dir else None
        self.block_device_path = os.path.abspath(block_device) if block_device else None
        self.port = port
        self.bind_ip = bind_ip
        self.read_only = read_only
        self.verbose = verbose
        self.enable_compression = enable_compression
        self.sock = None
        self.handles: Dict[int, FileHandle] = {}
        self.next_handle = 1
        self.compressed_files: Dict[str, CompressedFileWrapper] = {}  # Cache compressed wrappers

        if self.root_dir and not os.path.isdir(self.root_dir):
            raise ValueError(f"Root directory not found: {self.root_dir}")
        if self.block_device_path and not os.path.isfile(self.block_device_path):
            raise ValueError(f"Block device/image not found: {self.block_device_path}")
        if not self.root_dir and not self.block_device_path:
            raise ValueError("Either root_dir or block_device must be specified")

        # Open block device if provided
        if self.block_device_path:
            mode = 'rb' if read_only else 'r+b'
            self.block_file = open(self.block_device_path, mode)
            self.block_size = os.path.getsize(self.block_device_path)
        else:
            self.block_file = None
            self.block_size = 0

    def log(self, msg: str):
        if self.verbose:
            print(f"[UDPFS] {msg}")
