"""Read-only RetroAchievements PS1 hashing for POPStarter VCD images.

A VCD is 0x100000 bytes of POPS metadata followed by 2352-byte CD
sectors. Mode-2/Form-1 user data begins at byte 24 of each sector. Match
RiptOPL's PS1 BOOT/PS-X EXE hash input without extracting or changing
the original image (RiptOPL src/rahash.c, PR #894).
"""
import hashlib
from pathlib import Path
import re

VCD_HEADER = 0x100000
RAW_SECTOR = 2352
DATA_OFFSET = 24
ISO_SECTOR = 2048
MAX_BOOT = 64 * 1024 * 1024
MAX_DIRECTORY = 16 * 1024 * 1024


class VcdImage:
    def __init__(self, path):
        self.path = Path(path)
        self.size = self.path.stat().st_size
        self.file = None

    def __enter__(self):
        self.file = self.path.open("rb")
        try:
            if self.size < VCD_HEADER + RAW_SECTOR * 17:
                raise ValueError("Truncated POPStarter VCD image.")
            descriptor = self.read(16 * ISO_SECTOR, ISO_SECTOR)
            if descriptor[:7] != b"\\x01CD001\\x01":
                raise ValueError("VCD has no valid ISO9660 primary descriptor.")
        except BaseException:
            self.file.close()
            self.file = None
            raise
        return self

    def __exit__(self, *_exc):
        self.file.close()
        self.file = None

    def read(self, logical_offset, length):
        if logical_offset < 0 or length < 0:
            raise ValueError("Invalid VCD read offset or size.")
        result = bytearray()
        while length:
            sector, in_sector = divmod(logical_offset, ISO_SECTOR)
            count = min(length, ISO_SECTOR - in_sector)
            physical = VCD_HEADER + sector * RAW_SECTOR + DATA_OFFSET + in_sector
            if physical + count > self.size:
                raise ValueError("Truncated VCD sector or executable.")
            self.file.seek(physical)
            chunk = self.file.read(count)
            if len(chunk) != count:
                raise ValueError("Short read from VCD image.")
            result.extend(chunk)
            logical_offset += count
            length -= count
        return bytes(result)

    def root(self):
        descriptor = self.read(16 * ISO_SECTOR, ISO_SECTOR)
        if descriptor[:7] != b"\\x01CD001\\x01":
            raise ValueError("Invalid VCD ISO9660 descriptor.")
        record = descriptor[156:190]
        return self.extent(record)

    @staticmethod
    def extent(record):
        if len(record) < 34 or record[0] < 34 or record[0] > len(record):
            raise ValueError("Malformed ISO9660 directory extent.")
        lba = int.from_bytes(record[2:6], "little")
        size = int.from_bytes(record[10:14], "little")
        if not lba or size <= 0 or size > MAX_DIRECTORY + MAX_BOOT:
            raise ValueError("Invalid ISO9660 directory extent.")
        return lba, size

    def find(self, directory, name):
        lba, length = directory
        if length > MAX_DIRECTORY:
            raise ValueError("Oversized ISO9660 directory.")
        for offset in range(0, length, ISO_SECTOR):
            sector = self.read((lba * ISO_SECTOR) + offset, ISO_SECTOR)
            limit = min(ISO_SECTOR, length - offset)
            pos = 0
            while pos < limit:
                count = sector[pos]
                if not count:
                    break
                if count < 34 or pos + count > limit:
                    raise ValueError("Malformed ISO9660 directory record.")
                record = sector[pos:pos + count]
                n = record[32]
                if 33 + n > count:
                    raise ValueError("Invalid ISO9660 filename length.")
                entry_name = record[33:33 + n].decode("ascii", errors="replace").split(";", 1)[0]
                if entry_name.casefold() == name.casefold():
                    return self.extent(record)
                pos += count
        return None

    def path_extent(self, name):
        pieces = [piece for piece in name.replace("/", "\\\\").split("\\\\") if piece]
        if not pieces or len(pieces) > 16 or any(
                piece in (".", "..") or len(piece) >= 64 for piece in pieces):
            return None
        directory = self.root()
        for part in pieces:
            directory = self.find(directory, part)
            if directory is None:
                return None
        return directory

    def boot(self):
        cnf = self.path_extent("SYSTEM.CNF")
        if cnf:
            contents = self.read(cnf[0] * ISO_SECTOR, min(cnf[1], ISO_SECTOR - 1))
            text = contents.decode("latin-1")
            # BOOT, not BOOT2; the latter identifies PS2 games.
            match = re.search(r"(?m)^BOOT[ \\t]*=[ \\t]*(?:cdrom:)?\\\\*([^;\\s]+)", text)
            if match:
                name = match.group(1)
                if len(name) < 96 and self.path_extent(name):
                    return name
        if self.path_extent("PSX.EXE"):
            return "PSX.EXE"
        raise ValueError("No PS1 BOOT executable or PSX.EXE in this VCD.")

    def hash(self):
        name = self.boot()
        lba, size = self.path_extent(name)
        head = self.read(lba * ISO_SECTOR, 32)
        if head[:8] == b"PS-X EXE":
            payload = int.from_bytes(head[28:32], "little")
            if payload > MAX_BOOT - ISO_SECTOR:
                raise ValueError("Invalid PS-X EXE payload size.")
            size = payload + ISO_SECTOR
        if not 0 < size <= MAX_BOOT:
            raise ValueError("Invalid PS1 executable size.")
        try:
            digest = hashlib.md5(name.encode("ascii"))
        except UnicodeEncodeError as error:
            raise ValueError("PS1 BOOT name is not ASCII.") from error
        for offset in range(0, size, 64 * 1024):
            digest.update(self.read(lba * ISO_SECTOR + offset, min(64 * 1024, size - offset)))
        return digest.hexdigest()


def hash_vcd(path):
    with VcdImage(path) as image:
        return image.hash()
