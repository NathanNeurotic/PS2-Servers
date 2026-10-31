"""Offline ISO9660 fixtures for bounded, non-destructive PS2 serial inspection."""
import hashlib
from pathlib import Path
import tempfile
import threading
import struct
import unittest
from unittest import mock

from launcher import disc_identity as identity


SECTOR = 2048


def both(value, width=4):
    return value.to_bytes(width, "little") + value.to_bytes(width, "big")


def record(name, sector, size, directory=False):
    length = 33 + len(name)
    length += length % 2
    data = bytearray(length)
    data[0] = length
    data[2:10] = both(sector)
    data[10:18] = both(size)
    data[25] = 2 if directory else 0
    data[28:32] = both(1, 2)
    data[32] = len(name)
    data[33:33 + len(name)] = name
    return data


def iso_fixture(boot="SLUS_123.45", *, cnf=None, elf=b"\x7fELF" + b"fixture" * 20,
                nested=False, duplicate=False, boot_record=False):
    """Owned synthetic fixture; no retail image, network, account or PS2 needed."""
    image = bytearray(32 * SECTOR)
    boot_path = ("GAME/" if nested else "") + boot
    if cnf is None:
        cnf = ("BOOT2 = cdrom0:\\" + boot_path.replace("/", "\\") + ";1\r\nVER = 1.00\r\n").encode("ascii")
    if len(cnf) > SECTOR:
        raise ValueError("Fixture CNF must fit in its sector.")
    pvd = (17 if boot_record else 16) * SECTOR
    if boot_record:
        image[16 * SECTOR:16 * SECTOR + 7] = b"\0CD001\1"
    image[pvd:pvd + 7] = b"\1CD001\1"
    image[pvd + 80:pvd + 88] = both(len(image) // SECTOR)
    image[pvd + 128:pvd + 132] = both(SECTOR, 2)
    root = record(b"\0", 20, SECTOR, True)
    image[pvd + 156:pvd + 156 + len(root)] = root
    directory = root + record(b"\1", 20, SECTOR, True)
    directory += record(b"SYSTEM.CNF;1", 21, len(cnf))
    boot_entry = record(boot.encode("ascii") + b";1", 23, len(elf))
    if nested:
        directory += record(b"GAME", 22, SECTOR, True)
        subdirectory = record(b"\0", 22, SECTOR, True) + record(b"\1", 20, SECTOR, True) + boot_entry
        image[22 * SECTOR:22 * SECTOR + len(subdirectory)] = subdirectory
    else:
        directory += boot_entry
        if duplicate:
            directory += record(boot.lower().encode("ascii") + b";1", 24, len(elf))
            image[24 * SECTOR:24 * SECTOR + len(elf)] = elf
    image[20 * SECTOR:20 * SECTOR + len(directory)] = directory
    image[21 * SECTOR:21 * SECTOR + len(cnf)] = cnf
    image[23 * SECTOR:23 * SECTOR + len(elf)] = elf
    return image


def lz4_fixture(image, magic=b'ZISO', align=0):
    """Encode owned ISO sectors as LZ4 blocks, including real alignment padding."""
    import lz4.block
    count = len(image) // SECTOR
    entries = count + (magic == b'ZISO')
    alignment = 1 << align
    start = 24 + entries * 4
    payload = bytearray(bytes((-start) % alignment))
    offsets = []
    for index in range(count):
        offsets.append((start + len(payload)) >> align)
        block = image[index * SECTOR:(index + 1) * SECTOR]
        payload.extend(lz4.block.compress(bytes(block), store_size=False))
        payload.extend(bytes((-(start + len(payload))) % alignment))
    if magic == b'ZISO':
        offsets.append((start + len(payload)) >> align)
    header = struct.pack('<4sIQIBBH', magic, 24, len(image), SECTOR, 1, align, 0)
    return header + struct.pack('<{}I'.format(entries), *offsets) + payload


class DiscIdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "SLUS_999.99.Wrong filename.iso"

    def inspect(self, contents=None, **options):
        self.path.write_bytes(iso_fixture(**options) if contents is None else contents)
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        result = identity.inspect_disc(self.path)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)
        return result

    def test_serial_comes_from_cnf_and_referenced_elf_not_filename(self):
        result = self.inspect()
        self.assertEqual(result.status, "verified")
        self.assertEqual(result.disc_id, "SLUS_123.45")
        self.assertEqual(result.boot_path, "SLUS_123.45")
        self.assertEqual(identity.filename_serial(self.path), "SLUS_999.99")

    def test_nested_boot_path_case_and_leading_boot_record(self):
        result = self.inspect(boot="sles_543.21", nested=True, boot_record=True)
        self.assertEqual(result.status, "verified")
        self.assertEqual(result.disc_id, "SLES_543.21")
        self.assertEqual(result.boot_path, "GAME/sles_543.21")

    def test_homebrew_elf_and_ps1_boot_are_unknown_without_guessed_serial(self):
        for options in ({"boot": "HOMEBREW.ELF"}, {"cnf": b"BOOT=cdrom:\\SLUS_123.45;1\r\n"},
                        {"cnf": b"# BOOT2=cdrom0:\\SLUS_123.45;1\n"}):
            with self.subTest(options=options):
                result = self.inspect(**options)
                self.assertEqual(result.status, "unknown")
                self.assertEqual(result.disc_id, "")

    def test_multiple_boot_settings_and_unsafe_paths_are_not_picked_arbitrarily(self):
        for cnf in (b"BOOT2=cdrom0:\\SLUS_123.45;1\nBOOT2=cdrom0:\\SLUS_123.45;1\n",
                    b"BOOT2=cdrom0:\\..\\SLUS_123.45;1\n",
                    b"BOOT2=host:SLUS_123.45\n", b"BOOT2=cdrom0:\\SLUS_123.45;2\n"):
            with self.subTest(cnf=cnf):
                self.assertEqual(self.inspect(cnf=cnf).status, "unknown")

    def test_missing_executable_or_false_elf_is_invalid(self):
        for options in ({"cnf": b"BOOT2=cdrom0:\\SLUS_111.11;1\n"}, {"elf": b"not an executable"}):
            with self.subTest(options=options):
                result = self.inspect(**options)
                self.assertEqual(result.status, "invalid")
                self.assertEqual(result.disc_id, "")

    def test_duplicate_case_aliases_are_invalid(self):
        result = self.inspect(duplicate=True)
        self.assertEqual(result.status, "invalid")
        self.assertIn("Ambiguous", result.detail)

    def test_truncation_byte_order_mismatch_and_outside_extents_are_invalid(self):
        for location, value in ((16 * SECTOR + 84, b"\xff" * 4),
                                (16 * SECTOR + 156 + 2, both(1000)),
                                (16 * SECTOR + 156 + 25, b"\0")):
            data = iso_fixture()
            data[location:location + len(value)] = value
            with self.subTest(location=location):
                self.assertEqual(self.inspect(data).status, "invalid")
        for data in (b"", bytes(40_000), iso_fixture()[:24 * SECTOR]):
            with self.subTest(length=len(data)):
                self.assertEqual(self.inspect(data).status, "invalid")

    def test_interleaved_or_multi_extent_target_is_unsupported(self):
        for field, value in ((25, 0x80), (26, 1), (1, 1)):
            data = iso_fixture()
            pos = data.find(b"SLUS_123.45;1", 20 * SECTOR) - 33
            data[pos + field] = value
            self.assertEqual(self.inspect(data).status, "unsupported")

    def test_directory_record_and_byte_budgets_bound_sparse_work(self):
        for constant, value in (("MAX_DIRECTORY_BYTES", 1024), ("MAX_RECORDS", 2),
                                ("MAX_READ_BYTES", 2 * SECTOR), ("MAX_CNF_BYTES", 8),
                                ("MAX_DISC_BYTES", 20 * SECTOR)):
            with self.subTest(limit=constant), mock.patch.object(identity, constant, value):
                result = self.inspect()
                self.assertEqual(result.status, "unsupported")
                self.assertEqual(result.disc_id, "")

    def test_malformed_compressed_files_are_not_verified(self):
        for suffix in (".cso", ".zso", ".chd"):
            self.path = self.path.with_suffix(suffix)
            self.path.write_bytes(b"malformed compressed image")
            self.assertIn(identity.inspect_disc(self.path).status, ("invalid", "unavailable"))

    def test_cso_inspection_uses_logical_sectors_without_staging(self):
        from tests.test_ra_compressed_hash import make_indexed
        self.path = self.path.with_suffix('.cso')
        result = self.inspect(make_indexed(bytes(iso_fixture(nested=True))))
        self.assertEqual((result.status, result.disc_id), ('verified', 'SLUS_123.45'))
        self.assertEqual(result.boot_path, 'GAME/SLUS_123.45')

    def test_zso_and_ziso_inspection_when_decoder_installed(self):
        from tests.test_ra_compressed_hash import make_indexed
        from launcher.ra_compressed_hash import open_compressed
        self.path = self.path.with_suffix('.zso')
        for magic in (b'ZISO', b'ZSO\0'):
            self.path.write_bytes(make_indexed(bytes(iso_fixture()), magic=magic))
            try:
                stream = open_compressed(self.path, strict=True)
            except ImportError:
                self.skipTest('LZ4 is not installed')
            stream.close()
            self.assertEqual(identity.inspect_disc(self.path).status, 'verified')

    def test_cso_resource_limits_and_bad_offsets_are_distinct(self):
        from tests.test_ra_compressed_hash import make_indexed
        self.path = self.path.with_suffix('.cso')
        data = bytearray(make_indexed(bytes(iso_fixture())))
        struct.pack_into('<Q', data, 8, 32 * 1024**3)
        self.assertEqual(self.inspect(data).status, 'unsupported')
        data = bytearray(make_indexed(bytes(iso_fixture())))
        # The PVD block points into the index rather than the payload.
        struct.pack_into('<I', data, 24 + 16 * 4, 0x80000018)
        self.assertEqual(self.inspect(data).status, 'invalid')

    def test_real_lz4_identity_and_sparse_reads_with_alignment(self):
        try:
            import lz4.block
        except ImportError:
            self.skipTest('LZ4 is not installed')
        from launcher.ra_compressed_hash import open_compressed
        self.path = self.path.with_suffix('.zso')
        image = bytes(iso_fixture(nested=True))
        for magic, align in ((b'ZSO\0', 0), (b'ZISO', 0), (b'ZISO', 6)):
            with self.subTest(magic=magic, align=align):
                data = lz4_fixture(image, magic, align)
                self.assertLess(len(data), len(image) // 2)
                result = self.inspect(data)
                self.assertEqual((result.status, result.disc_id, result.boot_path),
                                 ('verified', 'SLUS_123.45', 'GAME/SLUS_123.45'))
                reader = open_compressed(self.path, strict=True)
                try:
                    # Cross block boundaries and revisit evicted blocks.
                    for offset in (SECTOR - 7, 20 * SECTOR - 3, 0, len(image) - 9):
                        reader.seek(offset)
                        self.assertEqual(reader.read(41), image[offset:offset + 41])
                        self.assertLessEqual(len(reader._cache), 2)
                finally:
                    reader.close()

    def test_malformed_lz4_and_oversized_output_are_invalid(self):
        try:
            import lz4.block
        except ImportError:
            self.skipTest('LZ4 is not installed')
        self.path = self.path.with_suffix('.zso')
        for payload in (b'\xff' * 16,
                        lz4.block.compress(bytes(SECTOR + 1), store_size=False)):
            with self.subTest(payload_length=len(payload)):
                data = bytearray(lz4_fixture(bytes(iso_fixture())))
                start = struct.unpack_from('<I', data, 24 + 16 * 4)[0]
                end = struct.unpack_from('<I', data, 24 + 17 * 4)[0]
                # Replace the PVD payload and update all subsequent offsets.
                delta = len(payload) - (end - start)
                data[start:end] = payload
                for index in range(17, 33):
                    old = struct.unpack_from('<I', data, 24 + index * 4)[0]
                    struct.pack_into('<I', data, 24 + index * 4, old + delta)
                self.assertEqual(self.inspect(data).status, 'invalid')
        self.path.unlink()  # Failed decoding released the file on Windows.

    def test_real_chd_identity_matches_iso_without_changing_source(self):
        from launcher.ra_compressed_hash import open_compressed
        self.path = self.path.with_suffix('.chd')
        data = (Path(__file__).parent / 'fixtures/ra_hash/synthetic-identity.chd').read_bytes()
        self.path.write_bytes(data)
        try:
            reader = open_compressed(self.path, strict=True)
        except ImportError:
            self.skipTest('build the pinned libchdr to exercise the real decoder')
        reader.close()
        result = self.inspect(data)
        self.assertEqual((result.status, result.disc_id, result.boot_path),
                         ('verified', 'SLUS_123.45', 'GAME/SLUS_123.45'))
        self.path.unlink()

    def test_cso_decode_budget_and_cancel_close_handles(self):
        from tests.test_ra_compressed_hash import make_indexed
        from launcher.ra_compressed_hash import open_compressed
        self.path = self.path.with_suffix('.cso')
        self.path.write_bytes(make_indexed(bytes(iso_fixture())))
        stream = open_compressed(self.path, strict=True)
        stream.close()
        from compressed_iso import limits
        with mock.patch.object(limits, 'MAX_DECODE', SECTOR):
            self.assertEqual(identity.inspect_disc(self.path).status, 'unsupported')
        event = threading.Event()
        stream = open_compressed(self.path, strict=True, cancel=event)
        event.set()
        try:
            with self.assertRaises(InterruptedError):
                stream.read(SECTOR)
        finally:
            stream.close()
        self.path.unlink()

    def test_cso_inflation_cannot_exceed_declared_block(self):
        from tests.test_ra_compressed_hash import make_indexed
        import zlib
        self.path = self.path.with_suffix('.cso')
        data = bytearray(make_indexed(bytes(iso_fixture())))
        offset = len(data)
        compressor = zlib.compressobj(wbits=-15)
        bomb = compressor.compress(b'A' * (SECTOR * 200)) + compressor.flush()
        data.extend(bomb)
        struct.pack_into('<II', data, 24 + 16 * 4, offset, offset + len(bomb))
        self.assertEqual(self.inspect(data).status, 'invalid')

    def test_missing_and_denied_files_are_unavailable(self):
        self.assertEqual(identity.inspect_disc(self.path).status, "unavailable")
        self.path.write_bytes(iso_fixture())
        with mock.patch.object(Path, "open", side_effect=PermissionError("denied")):
            result = identity.inspect_disc(self.path)
            self.assertEqual(result.status, "unavailable")
            self.assertIn("denied", result.detail)

    def test_file_changed_during_inspection_is_never_verified(self):
        self.path.write_bytes(iso_fixture())
        inspect = identity._IsoReader.inspect

        def change(reader):
            result = inspect(reader)
            with self.path.open("ab") as output:
                output.write(b"changed")
            return result

        with mock.patch.object(identity._IsoReader, "inspect", change):
            self.assertEqual(identity.inspect_disc(self.path).status, "changed")

    def test_cancellation_interrupts_between_sparse_reads(self):
        self.path.write_bytes(iso_fixture())
        event = threading.Event()
        read = identity._IsoReader.read

        def cancel(reader, offset, length):
            result = read(reader, offset, length)
            event.set()
            return result

        with mock.patch.object(identity._IsoReader, "read", cancel), self.assertRaises(InterruptedError):
            identity.inspect_disc(self.path, cancel=event)
        # File handle was closed on cancellation (including on Windows).
        self.path.unlink()

    def test_serial_hint_requires_ascii_and_a_complete_filename_prefix(self):
        for name in ("SLUS_123.45.Title.iso", "sles-543.21.iso"):
            self.assertTrue(identity.filename_serial(name))
        for name in ("Game SLUS_123.45.iso", "SLUS_123.456.iso", "SLUS_１２３.４５.iso"):
            self.assertEqual(identity.filename_serial(name), "")


if __name__ == "__main__":
    unittest.main()
