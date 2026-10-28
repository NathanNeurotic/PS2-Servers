"""Offline ISO9660 fixtures for bounded, non-destructive PS2 serial inspection."""
import hashlib
from pathlib import Path
import tempfile
import threading
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

    def test_compressed_files_report_unsupported_without_opening_readers(self):
        for suffix in (".cso", ".zso", ".chd"):
            self.path = self.path.with_suffix(suffix)
            self.path.write_bytes(b"malformed compressed image")
            with mock.patch.object(Path, "open", side_effect=AssertionError("Must not decode")):
                self.assertEqual(identity.inspect_disc(self.path).status, "unsupported")

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
