"""Regression tests for the optional Desktop library companion.

Runs offline and does not start any game server or make external RA requests.
"""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from launcher.library_catalog import Catalog, artwork_filename, visible_title


class LibraryCatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.games = self.root / "PS2"
        self.games.mkdir()
        self.database = self.root / "state" / "library.sqlite3"

    def catalog(self):
        return Catalog(self.database)

    def image(self, path="incoming/SLUS_222.22.Sample Game.iso", payload=b"ISO" * 1024):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        return target

    def test_scan_does_not_change_games_and_preserves_edits(self):
        original = self.image("PS2/DVD/SLUS_123.45.Game One.iso")
        other = self.image("PS2/CD/TEST_654.32.Game Two.ZSO", b"ZSO" * 500)
        (self.games / "DVD" / "readme.txt").write_text("untouched")
        with self.catalog() as db:
            self.assertEqual(db.scan(self.games), 2)
            entries = db.list_games()
            self.assertEqual(len(entries), 2)
            one = next(r for r in entries if r["media"] == "DVD")
            db.edit(one["id"], "My Custom Title", "SLUS_123.45", "Test")
            self.assertEqual(db.scan(self.games), 2)
            updated = db.get_game(one["id"])
            self.assertEqual(updated["title"], "My Custom Title")
            self.assertEqual(updated["disc_id"], "SLUS_123.45")
            self.assertEqual(updated["notes"], "Test")
        self.assertEqual(original.read_bytes(), b"ISO" * 1024)
        self.assertEqual(other.read_bytes(), b"ZSO" * 500)

    def test_import_is_copy_only_and_never_overwrites(self):
        source = self.image()
        with self.catalog() as db:
            target = db.import_image(source, self.games)
            self.assertEqual(target.read_bytes(), source.read_bytes())
            self.assertEqual(len(db.list_games()), 1)
            with self.assertRaises(FileExistsError):
                db.import_image(source, self.games)
        self.assertEqual(source.read_bytes(), b"ISO" * 1024)
        self.assertEqual(target.read_bytes(), b"ISO" * 1024)

    def test_copy_open_failure_does_not_remove_existing_destination(self):
        source = self.image()
        target = self.games / "DVD" / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"DO NOT ERASE")
        with self.catalog() as db:
            with self.assertRaises(FileExistsError):
                db.import_image(source, self.games)
        self.assertEqual(target.read_bytes(), b"DO NOT ERASE")

    def test_json_roundtrip_preserves_metadata_without_installing(self):
        source = self.image("PS2/DVD/ABCD_123.45.Test.iso")
        dump = self.root / "catalogue.json"
        with self.catalog() as db:
            db.scan(self.games)
            entry = db.list_games()[0]
            db.edit(entry["id"], "Edited title", "ABCD_123.45", "Detailed notes")
            db.export_json(dump)
        self.assertEqual(json.loads(dump.read_text())["format"], "PS2ServersLibrary/1")
        target_db = self.root / "second.sqlite3"
        with Catalog(target_db) as db:
            self.assertEqual(db.import_json(dump), 1)
            new = db.list_games()[0]
            self.assertEqual(new["title"], "Edited title")
            self.assertEqual(new["disc_id"], "ABCD_123.45")
            self.assertEqual(new["notes"], "Detailed notes")
            self.assertEqual(db.list_games("edited")[0]["id"], new["id"])
        self.assertEqual(source.read_bytes(), b"ISO" * 1024)

    def test_malformed_import_is_atomic(self):
        broken = self.root / "broken.json"
        broken.write_text(json.dumps({"format": "PS2ServersLibrary/1", "games": [
            {"image_path": "/temp/good.iso", "title": "Good", "media": "DVD"},
            {"image_path": "/temp/bad.iso", "media": "DVD"}]}))
        with self.catalog() as db:
            with self.assertRaises(ValueError):
                db.import_json(broken)
            self.assertEqual(db.list_games(), [])

    def test_sqlite_backup_is_consistent_and_non_destructive(self):
        image = self.image("PS2/DVD/ABCD_123.45.The Game.iso")
        backup = self.root / "backup.sqlite3"
        with self.catalog() as db:
            db.upsert(image)
            db.backup_sqlite(backup)
            with self.assertRaises(FileExistsError):
                db.backup_sqlite(backup)
            with self.assertRaises(FileExistsError):
                db.backup_sqlite(self.database)
        with sqlite3.connect(backup) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM games").fetchone()[0], 1)
        self.assertTrue(self.database.exists())
        self.assertTrue(backup.exists())

    def test_art_identity_and_format(self):
        self.assertEqual(artwork_filename("SLUS_123.45"), "SLUS_123.45_COV.png")
        self.assertEqual(artwork_filename("Game (USA)", "ICO"), "Game (USA)_ICO.png")
        for key in ("../escape", "hello\\name", ".", "a" * 110):
            with self.assertRaises(ValueError):
                artwork_filename(key)
        with self.assertRaises(ValueError):
            artwork_filename("SLUS_123.45", "BGM")
        self.assertEqual(visible_title("SLUS_123.45.Game_One.iso"), "Game One")

    def test_batch_import_continues_past_existing_and_invalid_images(self):
        one = self.image("incoming/Game One.iso", b"FIRST" * 128)
        two = self.image("incoming/Game Two.zso", b"SECOND" * 128)
        (self.games / "DVD").mkdir()
        (self.games / "DVD" / one.name).write_bytes(b"EXISTING SAVE")
        with self.catalog() as db:
            result = db.import_images([one, two, self.root / "missing.iso"], self.games)
            self.assertEqual(result["imported"], 1)
            self.assertEqual(result["skipped"], 2)
            self.assertEqual(db.list_games()[0]["image_path"], str(self.games / "DVD" / two.name))
        self.assertEqual((self.games / "DVD" / one.name).read_bytes(), b"EXISTING SAVE")
        self.assertEqual(one.read_bytes(), b"FIRST" * 128)
        self.assertEqual(two.read_bytes(), b"SECOND" * 128)

    def test_legacy_cover_repair_skips_existing_png_and_preserves_jpeg(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow optional for source checkout")
        art = self.games / "ART"
        art.mkdir()
        Image.new("RGB", (40, 60), (80, 30, 20)).save(art / "Game (USA)_COV.jpg")
        Image.new("RGB", (40, 60), (40, 90, 50)).save(art / "EXIST_123.45_COV.jpg")
        old = art / "EXIST_123.45_COV.png"
        old.write_bytes(b"existing PNG remains untouched")
        with self.catalog() as db:
            result = db.migrate_legacy_art(self.games)
            self.assertEqual(result, {"converted": 1, "skipped": 1, "errors": 0})
            self.assertEqual(db.migrate_legacy_art(self.games)["converted"], 0)
        with Image.open(art / "Game (USA)_COV.png") as icon:
            self.assertEqual(icon.format, "PNG")
            self.assertEqual(icon.mode, "P")
        self.assertTrue((art / "Game (USA)_COV.jpg").exists())
        self.assertEqual(old.read_bytes(), b"existing PNG remains untouched")

    def test_indexed_png_is_not_an_overwrite(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow is optional for source checkout")
        artwork = self.root / "cover.jpg"
        Image.new("RGB", (32, 48), (100, 20, 30)).save(artwork)
        with self.catalog() as db:
            target = db.write_art(artwork, self.games, "SLUS_123.45", "COV")
            with Image.open(target) as read:
                self.assertEqual(read.format, "PNG")
                self.assertEqual(read.mode, "P")
            with self.assertRaises(FileExistsError):
                db.write_art(artwork, self.games, "SLUS_123.45", "COV")
        self.assertTrue(artwork.exists())


if __name__ == "__main__":
    unittest.main()
