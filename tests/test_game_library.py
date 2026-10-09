import io
import hashlib
from importlib.util import find_spec
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest import mock

from launcher.game_library import Library, Compatibility, safe_name, hash_image
from launcher.achievements import engine_path


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.library = Library(self.root / "catalog")

    def tearDown(self):
        self.temporary.cleanup()

    def test_general_and_achievement_catalogues_coexist(self):
        from launcher import library_catalog
        common = self.root / "shared-library"
        common.mkdir()
        with library_catalog.Catalog(common / "catalog.sqlite3") as general:
            achievements = Library(common)
            achievements.save_game({"title": "Achievement catalogue title"})
            self.assertEqual(general.list_games(), [])
            self.assertEqual(achievements.games()[0]["title"], "Achievement catalogue title")
            self.assertNotEqual(general.path, achievements.database)
        with library_catalog.Catalog(common / "catalog.sqlite3") as general:
            self.assertEqual(general.list_games(), [])

    def test_caduceus_import_preserves_existing_and_backups_committed_data(self):
        self.library.save_game({"title": "Existing", "downloadUrl": "https://example.com/game.iso"})
        source = self.root / "catalog.json"
        source.write_text(json.dumps({"games": [{"title": "existing", "icon": "https://example.com/new.png"},
                                                {"title": "New", "_id": {"$oid": "unique"}, "gameId": "SLUS_123.45"}]}))
        added, skipped, backup = self.library.import_catalog(source)
        self.assertEqual((added, skipped), (1, 1))
        self.assertEqual(self.library.games("Existing")[0]["icon"], "")
        with closing(sqlite3.connect(backup)) as db:
            self.assertEqual(db.execute("SELECT title FROM games").fetchall(), [("Existing",)])
        self.assertEqual(self.library.games("New")[0]["game_id"], "SLUS_123.45")

    @unittest.skipUnless(find_spec("PIL"), "Pillow required for image export test")
    def test_cover_export_is_indexed_and_preserves_existing_opl_art(self):
        from PIL import Image

        image = Image.new("RGBA", (32, 32), (40, 110, 220, 128))
        source = io.BytesIO()
        image.save(source, "PNG")
        source_bytes = source.getvalue()
        game_root = self.root / "games"
        game_root.mkdir()
        artwork = game_root / "ART"
        artwork.mkdir()
        console_cover = artwork / "SLUS_123.45_COV.png"
        console_cover.write_bytes(b"custom user artwork")
        record = {"id": 7, "game_id": "SLUS_123.45",
                  "icon": "https://example.com/cover.png"}
        with mock.patch("launcher.game_library.urllib.request.urlopen",
                        side_effect=lambda *args, **kwargs: io.BytesIO(source_bytes)):
            cached = self.library.repair_cover(record, game_root)
            self.assertEqual(console_cover.read_bytes(), b"custom user artwork")
            console_cover.unlink()
            self.library.repair_cover(record, game_root)
        for path in (Path(cached), console_cover):
            with Image.open(path) as output:
                self.assertEqual(output.mode, "P")
                self.assertEqual(output.getpixel((0, 0)), 0)
                self.assertIn("transparency", output.info)

    def test_invalid_import_is_atomic(self):
        source = self.root / "catalog.json"
        source.write_text(json.dumps([{"title": "Good"}, {"title": "Bad", "downloadUrl": "file:///secret"}]))
        with self.assertRaises(ValueError):
            self.library.import_catalog(source)
        self.assertEqual(self.library.games(), [])

    def test_backup_rejects_live_database_and_hardlink(self):
        with self.assertRaises(ValueError):
            self.library.backup(self.library.database)
        linked = self.root / "linked.sqlite3"
        try:
            linked.hardlink_to(self.library.database)
        except OSError:
            return
        with self.assertRaises(ValueError):
            self.library.backup(linked)

    def test_completed_transfer_is_visible_and_never_overwrites(self):
        folder = self.root / "games"
        installed = Library.transfer(io.BytesIO(b"image data"), folder, "DVD", "Example.iso", 10)
        self.assertEqual(Path(installed).read_bytes(), b"image data")
        with self.assertRaises(FileExistsError):
            Library.transfer(io.BytesIO(b"replacement"), folder, "DVD", "Example.iso")
        self.assertEqual(Path(installed).read_bytes(), b"image data")
        self.assertEqual(len(Library.installed(folder)), 1)

    def test_cancel_and_short_download_leave_no_served_image_or_partial(self):
        folder = self.root / "games"
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(InterruptedError):
            Library.transfer(io.BytesIO(b"partial"), folder, "DVD", "Cancelled.iso", cancel=cancel)
        with self.assertRaises(ValueError):
            Library.transfer(io.BytesIO(b"partial"), folder, "DVD", "Short.iso", total=100)
        self.assertEqual(list((folder / "DVD").iterdir()), [])

    def test_cross_platform_unsafe_names_are_rejected(self):
        for name in ("../game.iso", "game:stream.iso", "CON.iso", "bad?.iso", "trailing.iso "):
            with self.assertRaises(ValueError):
                safe_name(name)

    def test_api_failure_never_caches_unmatched(self):
        image = self.root / "Example.iso"
        image.write_bytes(b"ISO fixture")
        account = mock.Mock()
        account.request.side_effect = OSError("offline")
        scanner = Compatibility(self.library, account)
        with self.assertRaises(OSError):
            scanner.check(image)
        with self.library.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM image_status").fetchone()[0], 0)

    def test_hash_cache_invalidates_when_image_changes(self):
        image = self.root / "Example.iso"
        image.write_bytes(b"ISO fixture")
        account = mock.Mock()
        account.request.return_value = [{"ID": 12, "Title": "Game", "NumAchievements": 5, "Hashes": ["a" * 32]}]
        scanner = Compatibility(self.library, account)
        with mock.patch("launcher.game_library.hash_image", return_value="a" * 32) as hashing:
            self.assertEqual(scanner.check(image)["status"], "compatible")
            self.assertEqual(scanner.check(image)["count"], 5)
            self.assertEqual(hashing.call_count, 1)
            image.write_bytes(b"Different image size")
            scanner.check(image)
            self.assertEqual(hashing.call_count, 2)
        self.assertEqual([call.kwargs["i"] for call in account.request.call_args_list], [21, 12])

    @unittest.skipUnless(engine_path().is_file(), "native achievement engine required")
    def test_native_ps2_hash_matches_independent_boot_name_and_elf_digest(self):
        # Minimal ISO9660 with known SYSTEM.CNF and a unique synthetic ELF.
        image = bytearray(24 * 2048)

        def record(name, sector, size, flags=0):
            length = 33 + len(name)
            length += length % 2
            result = bytearray(length)
            result[0] = length
            result[2:6] = sector.to_bytes(4, "little")
            result[6:10] = sector.to_bytes(4, "big")
            result[10:14] = size.to_bytes(4, "little")
            result[14:18] = size.to_bytes(4, "big")
            result[25] = flags
            result[28:32] = b"\1\0\0\1"
            result[32] = len(name)
            result[33:33 + len(name)] = name
            return result

        boot_name = b"SLUS_123.45"
        elf = b"\x7fELF" + bytes(range(256)) * 3
        cnf = b"BOOT2 = cdrom0:\\" + boot_name + b";1\r\nVER = 1.00\r\n"
        pvd = 16 * 2048
        image[pvd:pvd + 7] = b"\1CD001\1"
        image[pvd + 128:pvd + 132] = b"\0\10\10\0"
        root = record(b"\0", 20, 2048, 2)
        image[pvd + 156:pvd + 156 + len(root)] = root
        directory = root + record(b"\1", 20, 2048, 2) + record(b"SYSTEM.CNF;1", 21, len(cnf)) + record(boot_name + b";1", 22, len(elf))
        image[20 * 2048:20 * 2048 + len(directory)] = directory
        image[21 * 2048:21 * 2048 + len(cnf)] = cnf
        image[22 * 2048:22 * 2048 + len(elf)] = elf
        path = self.root / "Image with spaces.iso"
        path.write_bytes(image)
        self.assertEqual(hash_image(path), hashlib.md5(boot_name + elf).hexdigest())
