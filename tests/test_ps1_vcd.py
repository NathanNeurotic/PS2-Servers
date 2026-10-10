"""Independent POPStarter VCD fixtures exercising the production PS1 hasher.

Same geometry/hash inputs as RiptOPL .github/scripts/test_ra_ps1.py,
without real game images or private achievement credentials.
"""
import hashlib
import io
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock

from launcher.game_library import Library, Compatibility, hash_image
from launcher.ps1_vcd import hash_vcd


def record(name, lba, size):
    raw_name = name if isinstance(name, bytes) else name.encode("ascii")
    length = 33 + len(raw_name)
    length += length & 1
    result = bytearray(length)
    result[0] = length
    struct.pack_into("<I", result, 2, lba)
    struct.pack_into("<I", result, 10, size)
    result[32] = len(raw_name)
    result[33:33 + len(raw_name)] = raw_name
    return result


def synthetic_vcd(boot="SLUS_012.15", use_cnf=True, overflow=False, nested=False):
    iso = bytearray(40 * 2048)
    pvd = 16 * 2048
    iso[pvd:pvd + 7] = b"\x01CD001\x01"
    iso[pvd + 156:pvd + 190] = record(b"\0", 20, 2048)
    payload = bytearray(i % 251 for i in range(2048 * 3 + 17))
    payload[:8] = b"PS-X EXE"
    struct.pack_into("<I", payload, 28, 0xfffff800 if overflow else len(payload) - 2048)
    name = ("DATA\\GAME.EXE" if nested else "PSX.EXE" if not use_cnf else boot)
    entries = bytearray()
    if use_cnf:
        cfg = ("BOOT = cdrom:\\" + name + ";1\r\n").encode("ascii")
        iso[22 * 2048:22 * 2048 + len(cfg)] = cfg
        entries.extend(record("SYSTEM.CNF;1", 22, len(cfg)))
    if nested:
        entries.extend(record("DATA;1", 21, 2048))
        child = record("GAME.EXE;1", 24, len(payload))
        iso[21 * 2048:21 * 2048 + len(child)] = child
    else:
        entries.extend(record(name + ";1", 24, len(payload)))
    iso[20 * 2048:20 * 2048 + len(entries)] = entries
    iso[24 * 2048:24 * 2048 + len(payload)] = payload
    raw = bytearray(0x100000 + 40 * 2352)
    for sector in range(40):
        raw[0x100000 + sector * 2352 + 24:
            0x100000 + sector * 2352 + 24 + 2048] = iso[sector * 2048:(sector + 1) * 2048]
    expected_name = "DATA\\GAME.EXE" if nested else name
    return bytes(raw), hashlib.md5(expected_name.encode("ascii") + payload).hexdigest()


class VcdTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.library = Library(self.root / "library")
        self.addCleanup(self.folder.cleanup)

    def image(self, name="Game.VCD", **kwargs):
        data, expected = synthetic_vcd(**kwargs)
        path = self.root / name
        path.write_bytes(data)
        return path, expected

    def test_ps1_hash_matches_independent_boot_executable_fixture(self):
        for name, args in (("retail.VCD", {}),
                           ("nested.vcd", {"nested": True}),
                           ("fallback.vcd", {"use_cnf": False})):
            with self.subTest(name=name):
                image, expected = self.image(name, **args)
                before = image.read_bytes()
                self.assertEqual(hash_vcd(image), expected)
                self.assertEqual(hash_image(image), expected)
                self.assertEqual(image.read_bytes(), before)

    def test_truncated_bad_magic_and_overflow_are_rejected(self):
        valid, _ = self.image()
        invalid = self.root / "truncated.vcd"
        invalid.write_bytes(valid.read_bytes()[:0x100000 + 26 * 2352])
        with self.assertRaises(ValueError):
            hash_vcd(invalid)
        other, _ = self.image("overflow.vcd", overflow=True)
        with self.assertRaises(ValueError):
            hash_vcd(other)
        other, _ = self.image("wrong.vcd")
        content = bytearray(other.read_bytes())
        content[0x100000 + 16 * 2352 + 24 + 1] = 0
        other.write_bytes(content)
        with self.assertRaises(ValueError):
            hash_vcd(other)

    def test_pops_discovery_and_import_preserve_source(self):
        source, expected = self.image()
        game_root = self.root / "games"
        source_before = source.read_bytes()
        installed = Library.install(source, game_root, "POPS")
        self.assertEqual(Path(installed).name, "Game.VCD")
        self.assertEqual(hash_vcd(installed), expected)
        self.assertEqual(source.read_bytes(), source_before)
        self.assertEqual([(row["kind"], row["name"]) for row in Library.installed(game_root)],
                         [("POPS", "Game.VCD")])
        with self.assertRaises(FileExistsError):
            Library.install(source, game_root, "POPS")
        with self.assertRaises(ValueError):
            Library.install(source, game_root, "DVD")

    def test_mutated_vcd_cannot_publish_a_hash(self):
        image, _ = self.image()
        def changed(path):
            with open(path, "ab") as stream:
                stream.write(b"changed during the scan")
            return "a" * 32
        with mock.patch("launcher.ps1_vcd.hash_vcd", side_effect=changed):
            with self.assertRaisesRegex(ValueError, "changed during hashing"):
                hash_image(image)

    def test_invalid_download_cannot_install_vcd(self):
        folder = self.root / "games"
        with self.assertRaises(ValueError):
            Library.transfer(io.BytesIO(b"Not a real PS1 image"), folder, "POPS",
                             "Bad.vcd", validate_image=True)
        self.assertEqual(list((folder / "POPS").iterdir()), [])

    def test_ra_catalogue_queries_both_systems_and_preserves_cache(self):
        image, expected = self.image()
        account = mock.Mock()
        def catalog(_endpoint, **params):
            if params["i"] == 12:
                return [{"ID": 7, "Title": "PS1 Test", "NumAchievements": 2,
                         "Hashes": [expected]}]
            if params["i"] == 21:
                return [{"ID": 8, "Title": "PS2 Test", "NumAchievements": 3,
                         "Hashes": ["b" * 32]}]
            raise AssertionError("unexpected console")
        account.request.side_effect = catalog
        scanner = Compatibility(self.library, account)
        self.assertEqual(scanner.check(image)["status"], "compatible")
        self.assertEqual(scanner.check(image)["title"], "PS1 Test")
        self.assertEqual(account.request.call_count, 2)
        self.assertEqual(scanner.check(image)["count"], 2)
        self.assertEqual(account.request.call_count, 2)
        index = self.library.directory / "ra-hashes.json"
        self.assertEqual(set(__import__("json").loads(index.read_text())["consoles"]), {12, 21})

    def test_missing_ps1_catalogue_is_not_cached_as_unmatched(self):
        image, _ = self.image()
        account = mock.Mock()
        account.request.side_effect = [[], OSError("offline")]
        scanner = Compatibility(self.library, account)
        with self.assertRaises(OSError):
            scanner.check(image)
        with self.library.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM image_status").fetchone()[0], 0)
        self.assertFalse((self.library.directory / "ra-hashes.json").exists())
