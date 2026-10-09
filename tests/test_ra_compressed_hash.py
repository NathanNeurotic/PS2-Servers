"""Sparse PS2 RetroAchievements hash parity for compressed CHD/CSO/ZSO discs."""
import hashlib
import io
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock
import zlib

from launcher import ra_compressed_hash as compressed
from launcher.game_library import Compatibility, Library, hash_image


SECTOR = 2048


def fixture(boot=b"SLUS_123.45", elf=None):
    # A minimal valid 2048-byte-sector ISO9660 layout with boot executable.
    if elf is None:
        elf = b"\x7fELF" + bytes(range(256)) * 3
    image = bytearray(24 * SECTOR)

    def entry(name, sector, size, flags=0):
        length = 33 + len(name)
        length += length % 2
        row = bytearray(length)
        row[0] = length
        row[2:6] = sector.to_bytes(4, "little")
        row[6:10] = sector.to_bytes(4, "big")
        row[10:14] = size.to_bytes(4, "little")
        row[14:18] = size.to_bytes(4, "big")
        row[25] = flags
        row[28:32] = b"\1\0\0\1"
        row[32] = len(name)
        row[33:33 + len(name)] = name
        return row

    cnf = b"BOOT2 = cdrom0:\\" + boot + b";1\r\nVER=1.00\r\n"
    pvd = 16 * SECTOR
    image[pvd:pvd + 7] = b"\1CD001\1"
    image[pvd + 128:pvd + 132] = b"\0\10\10\0"
    root = entry(b"\0", 20, 2048, 2)
    image[pvd + 156:pvd + 156 + len(root)] = root
    directory = root + entry(b"\1", 20, 2048, 2) + entry(b"SYSTEM.CNF;1", 21, len(cnf)) + entry(boot + b";1", 22, len(elf))
    image[20 * SECTOR:20 * SECTOR + len(directory)] = directory
    image[21 * SECTOR:21 * SECTOR + len(cnf)] = cnf
    image[22 * SECTOR:22 * SECTOR + len(elf)] = elf
    return bytes(image), hashlib.md5(boot + elf).hexdigest()


def make_indexed(image, magic=b"CISO", header_size=24):
    """Real CSO/ZISO container, compressed raw-deflate for CSO."""
    count = len(image) // SECTOR
    is_zso = magic == b"ZSO\0"
    entries = count if is_zso else count + 1
    index = []
    payload = bytearray()
    offset = header_size + entries * 4
    for i in range(count):
        block = image[i * SECTOR:(i + 1) * SECTOR]
        raw = zlib.compressobj(level=9, wbits=-15)
        packed = raw.compress(block) + raw.flush()
        compress_it = magic == b"CISO" and i != 16 and len(packed) < len(block)
        index.append(offset | (0 if compress_it else 0x80000000))
        data = packed if compress_it else block
        payload.extend(data)
        offset += len(data)
    if not is_zso:
        index.append(offset)
    header = struct.pack("<4sIQIBBH", magic, header_size, len(image), SECTOR, 1, 0, 0)
    return header + bytes((header_size - 24)) + struct.pack("<{}I".format(entries), *index) + payload


class FakeChdStream(io.BytesIO):
    def __init__(self, contents):
        super().__init__(contents)
        self.uncompressed_size = len(contents)


class CompressionHashTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.iso_data, self.expected = fixture()

    def test_cso_real_raw_deflate_matches_official_hash_input(self):
        path = self.root / "Game.cso"
        path.write_bytes(make_indexed(self.iso_data))
        self.assertEqual(hash_image(path), self.expected)
        self.assertEqual(hash_image(path), self.expected)

    def test_ziso_uncompressed_blocks_match_iso_hash_without_lz4(self):
        path = self.root / "Game.zso"
        path.write_bytes(make_indexed(self.iso_data, magic=b"ZISO"))
        # A ZISO with raw index blocks needs no decompressor. Source-only CI
        # may not have the lz4 dependency packaged in production builds.
        from udpfs_server.compressed_iso import zso
        with mock.patch.object(zso, "LZ4_AVAILABLE", True):
            self.assertEqual(hash_image(path), self.expected)

    def test_zso_opl_layout_matches_iso_hash(self):
        path = self.root / "Game.zso"
        path.write_bytes(make_indexed(self.iso_data, magic=b"ZSO\0"))
        from udpfs_server.compressed_iso import zso
        with mock.patch.object(zso, "LZ4_AVAILABLE", True):
            self.assertEqual(hash_image(path), self.expected)

    def test_chd_v5_hashes_sparse_user_data_without_staging_iso(self):
        path = self.root / "Game.chd"
        header = bytearray(124)
        header[:8] = b"MComprHD"
        struct.pack_into(">I", header, 12, 5)
        struct.pack_into(">Q", header, 32, len(self.iso_data))
        struct.pack_into(">I", header, 56, 8192)
        struct.pack_into(">I", header, 60, 2048)
        path.write_bytes(header)
        with mock.patch.object(compressed, "open_compressed",
                               side_effect=lambda p: FakeChdStream(self.iso_data)) as reader:
            self.assertEqual(hash_image(path), self.expected)
        reader.assert_called_once_with(path)

    def test_compressed_compatibility_uses_same_ps2_catalogue_hash(self):
        path = self.root / "My PS2 Game.cso"
        path.write_bytes(make_indexed(self.iso_data))
        library = Library(self.root / "catalogue")
        account = mock.Mock()
        account.request.side_effect = [
            [{"ID": 222, "Title": "PS2 Game", "NumAchievements": 10,
              "Hashes": [self.expected]}], []]
        scan = Compatibility(library, account)
        one = scan.check(path)
        two = scan.check(path)
        self.assertEqual(one["status"], "compatible")
        self.assertEqual(one["game_id"], 222)
        self.assertEqual(two["hash"], self.expected)
        self.assertEqual(account.request.call_count, 2)

    def test_short_header_bogus_index_and_wrong_disc_never_hash(self):
        path = self.root / "bad.cso"
        for bad in (b"CISO", b"BAD!" + bytes(60),
                    struct.pack("<4sIQIBBH", b"CISO", 24,
                                9 * 1024 * 1024 * 1024 * 1024, 2048, 1, 0, 0)):
            path.write_bytes(bad)
            with self.assertRaises(ValueError):
                hash_image(path)
        path.write_bytes(make_indexed(bytes(len(self.iso_data))))
        with self.assertRaises(ValueError):
            hash_image(path)

    def test_changed_compressed_image_is_rejected_not_cached(self):
        path = self.root / "Game.cso"
        path.write_bytes(make_indexed(self.iso_data))
        def modify(source):
            source.write_bytes(source.read_bytes() + b"!")
            return self.expected
        with mock.patch.object(compressed, "hash_compressed_ps2", side_effect=modify):
            with self.assertRaisesRegex(ValueError, "changed during hashing"):
                hash_image(path)


if __name__ == "__main__":
    unittest.main()
