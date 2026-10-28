"""Bounded, read-only PS2 boot identity inspection, independent of RA accounts.

An inspected serial describes SYSTEM.CNF and its referenced executable. It is
not a content hash, a media-type guess, or proof that a console can boot a disc.
"""
from dataclasses import dataclass
import os
from pathlib import Path
import re
import stat


INSPECTOR_VERSION = 1
SECTOR = 2048
MAX_DISC_BYTES = 16 * 1024**3
MAX_READ_BYTES = 4 * 1024**2
MAX_DIRECTORY_BYTES = 1024**2
MAX_CNF_BYTES = 16 * 1024
MAX_RECORDS = 8192
MAX_PATH_DEPTH = 8
SERIAL = re.compile(r"[A-Z]{4}_\d{3}\.\d{2}", re.ASCII | re.I)
FILENAME_SERIAL = re.compile(r"^([A-Z]{4})[_-](\d{3})\.(\d{2})(?=[. _-]|$)", re.ASCII | re.I)


@dataclass(frozen=True)
class DiscIdentity:
    status: str
    disc_id: str = ""
    boot_path: str = ""
    detail: str = ""


class UnsupportedImage(ValueError):
    """A format/layout outside this inspector's deliberately limited contract."""


def fingerprint(info):
    """Filesystem observation for cache invalidation, not a content checksum."""
    # Windows pathname stat and fstat can disagree about ctime (creation time
    # versus change time on newer Python versions). The inode identifies file
    # replacement; size/mtime detect ordinary writes consistently on both APIs.
    keys = ("st_dev", "st_ino", "st_size", "st_mtime_ns")
    if os.name != "nt":
        keys += ("st_ctime_ns",)
    return ":".join(str(getattr(info, key)) for key in keys)


def filename_serial(path):
    match = FILENAME_SERIAL.match(Path(path).stem)
    return "{}_{}.{}".format(*match.groups()).upper() if match else ""


def _check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise InterruptedError("Disc ID inspection cancelled.")


def _both(data):
    half = len(data) // 2
    little = int.from_bytes(data[:half], "little")
    if little != int.from_bytes(data[half:], "big"):
        raise ValueError("ISO9660 byte-order fields disagree.")
    return little


class _IsoReader:
    def __init__(self, source, size, cancel):
        self.source, self.size, self.cancel = source, size, cancel
        self.remaining = MAX_READ_BYTES
        self.records = 0
        self.volume_bytes = size

    def read(self, offset, length):
        _check_cancel(self.cancel)
        if offset < 0 or length < 0 or offset + length > self.volume_bytes:
            raise ValueError("ISO9660 extent lies outside the image/volume.")
        if length > self.remaining:
            raise UnsupportedImage("Disc metadata exceeds the inspection read budget.")
        self.remaining -= length
        self.source.seek(offset)
        data = self.source.read(length)
        if len(data) != length:
            raise ValueError("Truncated ISO9660 data.")
        return data

    def extent(self, record, directory):
        if len(record) < 34 or record[0] != len(record) or 33 + record[32] > len(record):
            raise ValueError("Malformed ISO9660 directory record.")
        if bool(record[25] & 2) != directory:
            raise ValueError("ISO9660 path has an unexpected file/directory type.")
        if record[1] or record[26] or record[27] or record[25] & 0x80:
            raise UnsupportedImage("Extended, interleaved or multi-extent boot metadata is unsupported.")
        lba, size = _both(record[2:10]), _both(record[10:18])
        if not lba or not size or lba * SECTOR + size > self.volume_bytes:
            raise ValueError("Invalid or truncated ISO9660 extent.")
        if directory and size > MAX_DIRECTORY_BYTES:
            raise UnsupportedImage("ISO9660 directory exceeds the inspection limit.")
        return lba * SECTOR, size

    def root(self):
        # A boot record may precede the primary volume descriptor.
        for sector in range(16, 48):
            descriptor = self.read(sector * SECTOR, SECTOR)
            if descriptor[1:7] != b"CD001\x01":
                raise ValueError("No valid ISO9660 volume descriptor.")
            if descriptor[0] == 255:
                raise ValueError("No ISO9660 primary volume descriptor.")
            if descriptor[0] != 1:
                continue
            if _both(descriptor[128:132]) != SECTOR:
                raise UnsupportedImage("Only 2048-byte ISO9660 logical blocks are inspected.")
            volume_bytes = _both(descriptor[80:88]) * SECTOR
            if not 17 * SECTOR <= volume_bytes <= self.size:
                raise ValueError("Invalid or truncated ISO9660 volume size.")
            self.volume_bytes = volume_bytes
            length = descriptor[156]
            return self.extent(descriptor[156:156 + length], directory=True)
        raise UnsupportedImage("No primary volume descriptor within the inspection limit.")

    def find(self, directory, name, *, is_directory=False):
        origin, size = directory
        found = None
        for offset in range(0, size, SECTOR):
            data = self.read(origin + offset, min(SECTOR, size - offset))
            pos = 0
            while pos < len(data):
                length = data[pos]
                if not length:
                    break
                self.records += 1
                if self.records > MAX_RECORDS:
                    raise UnsupportedImage("Too many ISO9660 records for one inspection.")
                if length < 34 or pos + length > len(data):
                    raise ValueError("Malformed ISO9660 directory record.")
                record = data[pos:pos + length]
                name_length = record[32]
                if not name_length or 33 + name_length > length:
                    raise ValueError("Malformed ISO9660 filename.")
                raw_name = record[33:33 + name_length]
                try:
                    entry_name = raw_name.decode("ascii")
                except UnicodeDecodeError:
                    entry_name = ""  # An unrelated non-ASCII entry is not a boot match.
                # Only an absent version or ;1 is supported. Never pick a
                # different version or the first of two case-ambiguous entries.
                if entry_name.upper() in (name.upper(), name.upper() + ";1"):
                    if found is not None:
                        raise ValueError("Ambiguous ISO9660 boot path (duplicate names/versions).")
                    found = self.extent(record, directory=is_directory)
                pos += length
        return found

    def inspect(self):
        root = self.root()
        cnf = self.find(root, "SYSTEM.CNF")
        if cnf is None:
            return DiscIdentity("unknown", detail="No SYSTEM.CNF in the ISO9660 root.")
        if cnf[1] > MAX_CNF_BYTES:
            raise UnsupportedImage("SYSTEM.CNF exceeds the inspection limit.")
        text = self.read(*cnf).decode("ascii")
        settings = re.findall(r"(?im)^[ \t]*BOOT2[ \t]*=[ \t]*([^\r\n]*)", text)
        if len(settings) != 1:
            return DiscIdentity("unknown", detail="Expected exactly one BOOT2 setting in SYSTEM.CNF.")
        match = re.fullmatch(r"cdrom0:[\\/]*([^;\s]+)(?:;1)?", settings[0].strip(), re.I)
        if not match:
            return DiscIdentity("unknown", detail="BOOT2 uses an unsupported device, path or version.")
        pieces = match.group(1).replace("\\", "/").split("/")
        if (len(pieces) > MAX_PATH_DEPTH or any(
                not part or part in (".", "..") or len(part) > 64 or ":" in part for part in pieces)):
            return DiscIdentity("unknown", detail="BOOT2 path is invalid or exceeds the inspection depth.")
        boot_path = "/".join(pieces)
        current = root
        for index, part in enumerate(pieces):
            current = self.find(current, part, is_directory=index < len(pieces) - 1)
            if current is None:
                return DiscIdentity("invalid", boot_path=boot_path, detail="BOOT2 executable/path is missing.")
        if current[1] < 4 or self.read(current[0], 4) != b"\x7fELF":
            return DiscIdentity("invalid", boot_path=boot_path, detail="BOOT2 target has no ELF signature.")
        if not SERIAL.fullmatch(pieces[-1]):
            return DiscIdentity("unknown", boot_path=boot_path,
                                detail="BOOT2 references an ELF without a canonical PS2 serial.")
        return DiscIdentity("verified", pieces[-1].upper(), boot_path,
                            "SYSTEM.CNF BOOT2 serial and referenced ELF signature inspected.")


def inspect_disc(path, *, cancel=None):
    """Inspect a cooked ISO without writing or inferring identity from its name.

    Compressed images remain indexable but are explicitly unsupported here.
    Sharing their serving readers would need a separate bounded decompression
    contract before exposing those readers to batches of malformed library files.
    """
    _check_cancel(cancel)
    path = Path(path)
    try:
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or
                getattr(info, "st_file_attributes", 0) & 0x400):
            raise ValueError("Disc inspection requires a regular file, without links/reparse points.")
        if path.suffix.lower() != ".iso":
            return DiscIdentity("unsupported", detail="Disc ID inspection currently supports plain ISO only.")
        if info.st_size > MAX_DISC_BYTES:
            return DiscIdentity("unsupported", detail="ISO size exceeds the inspection limit.")
        if info.st_size < 17 * SECTOR:
            raise ValueError("Truncated ISO image.")
        with path.open("rb") as source:
            if fingerprint(os.fstat(source.fileno())) != fingerprint(info):
                return DiscIdentity("changed", detail="Image changed before inspection; retry.")
            try:
                result = _IsoReader(source, info.st_size, cancel).inspect()
            except UnsupportedImage as exc:
                result = DiscIdentity("unsupported", detail=str(exc))
            except (ValueError, UnicodeError) as exc:
                result = DiscIdentity("invalid", detail=str(exc))
            if (fingerprint(os.fstat(source.fileno())) != fingerprint(info) or
                    fingerprint(path.lstat()) != fingerprint(info)):
                return DiscIdentity("changed", detail="Image changed during inspection; retry.")
            _check_cancel(cancel)
            return result
    except InterruptedError:
        raise
    except OSError as exc:
        return DiscIdentity("unavailable", detail=str(exc))
    except ValueError as exc:
        return DiscIdentity("invalid", detail=str(exc))
