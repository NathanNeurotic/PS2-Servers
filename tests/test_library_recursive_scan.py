"""Bounded, opt-in metadata discovery without image mutation or link traversal."""
from pathlib import Path
import os
import stat
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

from launcher.library_catalog import Catalog


class RecursiveScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "games"
        self.root.mkdir()
        self.db = Catalog(Path(self.tmp.name) / "catalog.sqlite3")
        self.addCleanup(self.db.db.close)

    def image(self, relative, data=b"image remains untouched"):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def test_default_still_scans_only_top_level_cd_dvd(self):
        self.image("DVD/One.iso")
        self.image("CD/Two.ZSO")
        self.image("Root.iso")
        self.image("DVD/RPG/Nested.iso")
        self.assertEqual(self.db.scan(self.root), 2)
        self.assertEqual({r["media"] for r in self.db.list_games()}, {"CD", "DVD"})

    def test_root_nested_and_duplicate_names_keep_full_path_identity(self):
        files = [self.image(p) for p in (
            "Same.iso", "DVD/Same.iso", "DVD/RPG/Same.iso",
            "Games/RPG/Subfolder/Same.iso", "CD/Two.cso", "Other/Comma, 日本.chd")]
        result = self.db.scan_detailed(self.root, recursive=True)
        self.assertEqual(result.indexed, 6)
        rows = {r["image_path"]: r for r in self.db.list_games()}
        self.assertEqual(len(rows), 6)
        self.assertEqual(rows[str(files[0].resolve())]["media"], "UNKNOWN")
        self.assertEqual(rows[str(files[2].resolve())]["media"], "DVD")
        self.assertEqual(rows[str(files[3].resolve())]["media"], "UNKNOWN")
        self.assertEqual(rows[str(files[4].resolve())]["media"], "CD")
        self.assertTrue(all(p.read_bytes() == b"image remains untouched" for p in files))

    def test_depth_zero_includes_root_only_and_four_is_bounded(self):
        self.image("Root.iso")
        self.image("a/b/c/d/Four.iso")
        self.image("a/b/c/d/e/Five.iso")
        result = self.db.scan_detailed(self.root, recursive=True, max_depth=0)
        self.assertEqual(result.indexed, 1)
        self.assertGreater(result.skipped_depth, 0)
        result = self.db.scan_detailed(self.root, recursive=True, max_depth=4)
        self.assertEqual(result.indexed, 2)
        self.assertEqual(result.skipped_depth, 1)

    def test_rescan_preserves_edits_and_known_media(self):
        image = self.image("DVD/Edited.iso")
        self.db.scan(self.root)
        row = self.db.list_games()[0]
        self.db.edit(row["id"], "Custom", "SLUS_123.45", "Keep notes")
        image.write_bytes(b"changed size")
        self.db.scan_detailed(image.parent, recursive=True)
        updated = self.db.get_game(row["id"])
        self.assertEqual((updated["title"], updated["disc_id"], updated["notes"], updated["media"]),
                         ("Custom", "SLUS_123.45", "Keep notes", "DVD"))
        self.assertEqual(updated["image_bytes"], len(b"changed size"))

    def test_entry_budget_bounds_wide_directory(self):
        for i in range(200):
            self.image(f"{i}.iso")
        result = self.db.scan_detailed(self.root, recursive=True, max_entries=17)
        self.assertTrue(result.limit_reached)
        self.assertEqual(result.visited, 17)
        self.assertEqual(result.indexed, 17)

    def test_cancel_then_rescan_of_thousand_images_keeps_progress(self):
        for i in range(1000):
            self.image(f"{i}.iso")
        cancel = threading.Event()
        snapshots = []

        def progress(result):
            snapshots.append(result)
            cancel.set()

        result = self.db.scan_detailed(self.root, recursive=True, cancel=cancel, progress=progress)
        self.assertTrue(result.cancelled)
        self.assertEqual(result.indexed, 128)
        self.assertEqual(snapshots[0].visited, 128)
        self.assertFalse(snapshots[0].cancelled)  # snapshots are independent
        cancel.clear()
        result = self.db.scan_detailed(self.root, recursive=True, cancel=cancel)
        self.assertEqual(result.indexed, 1000)
        self.assertEqual(len(self.db.list_games()), 1000)

    def test_precancelled_scan_does_not_index_or_delete(self):
        self.image("DVD/Keep.iso")
        self.db.scan(self.root)
        event = threading.Event()
        event.set()
        result = self.db.scan_detailed(self.root, recursive=True, cancel=event)
        self.assertTrue(result.cancelled)
        self.assertEqual(result.visited, 0)
        self.assertEqual(len(self.db.list_games()), 1)

    def test_unreadable_directory_is_reported_and_other_files_continue(self):
        self.image("Denied/Hidden.iso")
        self.image("Visible.iso")
        real_scandir = os.scandir

        def scandir(path):
            if Path(path).name == "Denied":
                raise PermissionError("access denied")
            return real_scandir(path)

        with mock.patch("launcher.library_catalog.os.scandir", side_effect=scandir):
            result = self.db.scan_detailed(self.root, recursive=True)
        self.assertEqual(result.indexed, 1)
        self.assertEqual(result.errors, 1)
        self.assertIn("Denied", result.details[0])
        self.assertIn("access denied", str(result))

    def test_empty_and_unsupported_are_separate_from_errors(self):
        self.image("Empty.iso", b"")
        self.image("notes.txt")
        result = self.db.scan_detailed(self.root, recursive=True)
        self.assertEqual(result.indexed, 0)
        self.assertEqual(result.errors, 0)
        self.assertEqual(result.skipped_invalid, 1)
        self.assertEqual(result.skipped_unsupported, 1)

    def test_unknown_media_backup_roundtrip(self):
        self.image("Root.iso")
        self.db.scan_detailed(self.root, recursive=True)
        backup = Path(self.tmp.name) / "export.json"
        self.db.export_json(backup)
        with Catalog(Path(self.tmp.name) / "other.sqlite3") as other:
            self.assertEqual(other.import_json(backup), 1)
            self.assertEqual(other.list_games()[0]["media"], "UNKNOWN")

    def test_symlink_escape_cycle_and_file_links_are_not_followed(self):
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        (outside / "Secret.iso").write_bytes(b"secret")
        self.image("DVD/Good.iso")
        try:
            (self.root / "escape").symlink_to(outside, target_is_directory=True)
            (self.root / "cycle").symlink_to(self.root, target_is_directory=True)
            (self.root / "Alias.iso").symlink_to(outside / "Secret.iso")
        except OSError:
            self.skipTest("Host cannot create symlinks; exercised on Linux")
        result = self.db.scan_detailed(self.root, recursive=True)
        self.assertEqual(result.indexed, 1)
        self.assertEqual(result.skipped_links, 3)
        self.assertEqual((outside / "Secret.iso").read_bytes(), b"secret")

    def test_windows_junction_flag_prevents_directory_descent(self):
        self.image("Junction/Hidden.iso")
        self.image("Visible.iso")
        real_lstat = Path.lstat

        def lstat(path, *args, **kwargs):
            if path.name == "Junction":
                return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
            return real_lstat(path, *args, **kwargs)

        with mock.patch.object(Path, "lstat", lstat):
            result = self.db.scan_detailed(self.root, recursive=True)
        self.assertEqual(result.indexed, 1)
        self.assertEqual(result.skipped_links, 1)
        self.assertEqual(Path(self.db.list_games()[0]["image_path"]).name, "Visible.iso")

    def test_error_details_are_bounded_without_losing_error_count(self):
        for i in range(25):
            (self.root / f"Denied{i}").mkdir()
        real_scandir = os.scandir

        def scandir(path):
            if Path(path).name.startswith("Denied"):
                raise PermissionError("access denied")
            return real_scandir(path)

        with mock.patch("launcher.library_catalog.os.scandir", side_effect=scandir):
            result = self.db.scan_detailed(self.root, recursive=True)
        self.assertEqual(result.errors, 25)
        self.assertEqual(len(result.details), 20)

    def test_options_are_bounded_before_any_indexing(self):
        for options in ({"max_depth": -1}, {"max_depth": 9}, {"max_entries": 0},
                        {"max_entries": 100001}, {"max_entries": True}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.db.scan_detailed(self.root, recursive=True, **options)
        self.assertEqual(self.db.list_games(), [])


if __name__ == "__main__":
    unittest.main()
