"""PS2 achievement hashing directly from compressed disc images.

The PS2 RetroAchievements hash is MD5(BOOT2 filename + up to 64 MiB of
the referenced ELF), per the pinned rcheevos rc_hash_ps2 implementation.
Reuse the UDPFS compressed readers but perform only sparse ISO9660 reads:
never inflate a game-size temporary ISO or scan server/device hot paths.

PS1 POPStarter VCD uses a different hash path and is not handled here.
"""
import hashlib
from pathlib import Path
import re
import struct

from .ps1_vcd import VcdImage, ISO_SECTOR, MAX_BOOT

MAX_DISC_BYTES = 16 * 1024 * 1024 * 1024
MAX_TABLE_BYTES = 32 * 1024 * 1024
MAX_HUNK_BYTES = 16 * 1024 * 1024
MAX_BOOT_CNF_BYTES = ISO_SECTOR - 1
FORMATS = {".cso", ".zso", ".chd"}


def _preflight(path):
    """Reject implausible metadata before compressed readers allocate indexes."""
    with path.open("rb") as source:
        header = source.read(124)
    if path.suffix.lower() in (".cso", ".zso"):
        magic = header[:4]
        allowed = {".cso": (b"CISO",), ".zso": (b"ZISO", b"ZSO\x00")}[path.suffix.lower()]
        if magic not in allowed or len(header) < 24:
            raise ValueError("Invalid compressed ISO header.")
        size, block_size = struct.unpack_from("<QI", header, 8)
        if (not ISO_SECTOR * 17 <= size <= MAX_DISC_BYTES
                or not ISO_SECTOR <= block_size <= MAX_HUNK_BYTES
                or block_size % ISO_SECTOR):
            raise ValueError("Compressed ISO has invalid disc or block size.")
        blocks = (size + block_size - 1) // block_size
        count = blocks + (magic != b"ZSO\x00")
        if count * 4 > MAX_TABLE_BYTES or path.stat().st_size < 24 + count * 4:
            raise ValueError("Compressed ISO index is too large or truncated.")
        if magic == b"ZSO\x00":
            table_offset = struct.unpack_from("<I", header, 4)[0]
            if (table_offset < 24 or table_offset > 1024 * 1024
                    or path.stat().st_size < table_offset + count * 4):
                raise ValueError("Invalid ZSO index location.")
    else:
        if len(header) < 64 or header[:8] != b"MComprHD":
            raise ValueError("Invalid CHD header.")
        version = struct.unpack_from(">I", header, 12)[0]
        size = struct.unpack_from(">Q", header, 32)[0]
        hunk_size = struct.unpack_from(">I", header, 56)[0]
        if (version != 5 or not ISO_SECTOR * 17 <= size <= MAX_DISC_BYTES
                or not ISO_SECTOR <= hunk_size <= MAX_HUNK_BYTES):
            raise ValueError("Unsupported or oversized CHD image.")


def open_compressed(path):
    # UDPFS is a standalone module, not a Python package. Its sibling
    # compressed_iso/ is imported as a top-level package by UDPFS itself,
    # and Nuitka compiles that same package in frozen releases.
    from .servers import is_frozen
    if not is_frozen():
        import sys
        directory = str(Path(__file__).resolve().parents[1] / "udpfs_server")
        if directory not in sys.path:
            sys.path.insert(0, directory)
    from compressed_iso import CsoFileWrapper, ZsoFileWrapper, ChdFileWrapper
    kind = path.suffix.lower()
    reader = {".cso": CsoFileWrapper, ".zso": ZsoFileWrapper,
              ".chd": ChdFileWrapper}[kind]
    return reader(str(path))


class Ps2CompressedImage(VcdImage):
    """Reuse the bounds-checked ISO9660 navigation of the VCD reader.

    Compressed ISO readers supply a 2048-byte logical user-data stream
    directly, unlike a POPStarter VCD's 2352-byte raw sector layout.
    """

    def __enter__(self):
        _preflight(self.path)
        self.file = open_compressed(self.path)
        try:
            self.size = self.file.uncompressed_size
            if self.size < ISO_SECTOR * 17 or self.size > MAX_DISC_BYTES:
                raise ValueError("Invalid decompressed disc size.")
            if self.read(16 * ISO_SECTOR, 7) != b"\x01CD001\x01":
                raise ValueError("Compressed image is not an ISO9660 PS2 disc.")
        except BaseException:
            self.file.close()
            self.file = None
            raise
        return self

    def read(self, logical_offset, length):
        if (logical_offset < 0 or length < 0
                or logical_offset > self.size or length > self.size - logical_offset):
            raise ValueError("Compressed image has a truncated directory or executable.")
        self.file.seek(logical_offset)
        data = self.file.read(length)
        if len(data) != length:
            raise ValueError("Compressed image decompression returned a short read.")
        return data

    def boot2(self):
        cnf = self.path_extent("SYSTEM.CNF")
        if cnf is None:
            raise ValueError("No SYSTEM.CNF in compressed PS2 disc.")
        contents = self.read(cnf[0] * ISO_SECTOR, min(cnf[1], MAX_BOOT_CNF_BYTES))
        # The pinned rc_hash_find_playstation_executable accepts BOOT2 and
        # optionally strips cdrom0: followed by backslashes; preserve the
        # filename exactly, as it is part of the RA hash (case matters).
        for line in contents.decode("latin-1").splitlines():
            match = re.search(r"BOOT2\s*=\s*(?:cdrom0:)?\\*([^;\s]+)", line)
            if match:
                name = match.group(1)
                if len(name) >= 64:
                    break
                extent = self.path_extent(name)
                if extent is not None:
                    return name, extent
                break
        raise ValueError("No valid BOOT2 executable in compressed PS2 disc.")

    def hash(self):
        name, (lba, size) = self.boot2()
        if size <= 0:
            raise ValueError("Empty PS2 boot executable.")
        # rcheevos uses MAX_BUFFER_SIZE=64 MiB, including when the ELF
        # directory extent reports a larger file.
        digest = hashlib.md5(name.encode("ascii"))
        size = min(size, MAX_BOOT)
        origin = lba * ISO_SECTOR
        for offset in range(0, size, 64 * 1024):
            digest.update(self.read(origin + offset, min(64 * 1024, size - offset)))
        return digest.hexdigest()


def hash_compressed_ps2(path):
    path = Path(path)
    if path.suffix.lower() not in FORMATS:
        raise ValueError("Only CSO, ZSO and CHD PS2 images are supported here.")
    with Ps2CompressedImage(path) as disc:
        return disc.hash()
