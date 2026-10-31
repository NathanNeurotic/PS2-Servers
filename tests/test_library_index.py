"""Shared immutable path descriptors, root containment and read-only DB access."""
from dataclasses import FrozenInstanceError
import hashlib
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest import mock

from launcher.library_catalog import Catalog
from launcher.library_index import read_index
from tests.test_disc_identity import iso_fixture


class LibraryIndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "games"
        self.root.mkdir()
        self.database = Path(self.tmp.name) / "catalog.sqlite3"
        self.catalog = Catalog(self.database)
        self.addCleanup(self.catalog.db.close)

    def image(self, name="DVD/Game.iso", **options):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(iso_fixture(**options))
        return path

    def snapshot(self, inspect=True, **options):
        self.catalog.scan_detailed(self.root, recursive=True)
        if inspect:
            self.catalog.inspect_identities(self.root)
        return read_index(self.database, self.root, **options)

    def test_descriptor_has_rooted_path_identity_metadata_and_provenance(self):
        path = self.image("RPG/Subfolder/Game.iso")
        self.catalog.scan_detailed(self.root, recursive=True)
        row = self.catalog.list_games()[0]
        self.catalog.edit(row["id"], "Custom title", "", "Keep notes")
        game, = self.snapshot().games
        self.assertEqual(game.image_path, str(path.resolve()))
        self.assertEqual(game.relative_path, "RPG/Subfolder/Game.iso")
        self.assertEqual(game.root, str(self.root.resolve()))
        self.assertTrue(game.source_id.startswith("path-v1:"))
        self.assertEqual((game.size, game.mtime_ns), (path.stat().st_size, path.stat().st_mtime_ns))
        self.assertEqual((game.image_format, game.media, game.layout), ("ISO", "UNKNOWN", "nested path"))
        self.assertEqual((game.title, game.display_title_override), ("Custom title", "Custom title"))
        self.assertEqual((game.boot_serial, game.artwork_identity, game.identity_status),
                         ("SLUS_123.45", "SLUS_123.45", "verified"))
        self.assertIn("catalogue", game.provenance)
        with self.assertRaises(FrozenInstanceError):
            game.title = "overwritten"

    def test_same_descriptor_api_for_general_and_external_consumers(self):
        self.image()
        direct = self.snapshot()
        self.assertEqual(self.catalog.indexed_games(self.root), direct)

    def test_source_id_survives_custom_edits_rescans_and_root_selection_changes(self):
        self.image()
        original, = self.snapshot().games
        row = self.catalog.list_games()[0]
        self.catalog.edit(row["id"], "Edited", "SLES_111.11")
        self.catalog.scan(self.root)
        edited, = read_index(self.database, self.root / "DVD").games
        self.assertEqual(edited.source_id, original.source_id)
        self.assertEqual(edited.relative_path, "Game.iso")
        self.assertEqual(edited.layout, "root image")
        self.assertEqual((edited.boot_serial, edited.manual_id), ("SLUS_123.45", "SLES_111.11"))
        self.assertEqual((edited.identity_status, edited.artwork_identity), ("conflict", ""))

    def test_same_basename_or_serial_is_reported_without_collapsing_paths(self):
        self.image("DVD/One/Game.iso")
        self.image("DVD/Two/Game.iso")
        snapshot = self.snapshot()
        self.assertEqual(len(snapshot.games), 2)
        self.assertEqual(len({game.source_id for game in snapshot.games}), 2)
        self.assertEqual({c.kind for c in snapshot.conflicts}, {"duplicate basename", "duplicate boot serial"})
        for conflict in snapshot.conflicts:
            self.assertEqual(set(conflict.paths), {"DVD/One/Game.iso", "DVD/Two/Game.iso"})
        self.assertIn("duplicate basename", snapshot.report())

    def test_cross_platform_case_and_unicode_collisions_keep_distinct_source_ids(self):
        paths = [self.image(name) for name in (
            "DVD/Name.iso", "DVD/name.iso", "DVD/Café.iso", "DVD/Cafe\u0301.iso")]
        if any(one.samefile(two) for i, one in enumerate(paths) for two in paths[i + 1:]):
            self.skipTest("Filesystem aliases case or Unicode variants; distinct paths are tested on Linux")
        snapshot = self.snapshot(inspect=False)
        self.assertEqual(len(snapshot.games), 4)
        self.assertEqual(len({game.source_id for game in snapshot.games}), 4)
        self.assertEqual(sum(c.kind == "case/Unicode collision" for c in snapshot.conflicts), 2)

    def test_changed_image_withholds_old_inspected_serial_without_mutating_cache(self):
        path = self.image()
        self.snapshot()
        path.write_bytes(iso_fixture(boot="SLES_543.21") + b"changed")
        info = path.stat()
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 2_000_000_000))
        game, = read_index(self.database, self.root).games
        self.assertEqual((game.identity_status, game.boot_serial, game.artwork_identity), ("stale", "", ""))
        # Sharing is read-only; a real rescan/inspection owns cache invalidation.
        cached = self.catalog.db.execute("SELECT detected_disc_id FROM disc_identity").fetchone()
        self.assertEqual(cached[0], "SLUS_123.45")

    def test_missing_and_linked_images_are_skipped_and_reported(self):
        path = self.image()
        self.snapshot()
        path.unlink()
        snapshot = read_index(self.database, self.root)
        self.assertEqual((len(snapshot.games), snapshot.skipped), (0, 1))
        self.assertTrue(snapshot.details)
        outside = Path(self.tmp.name) / "outside.iso"
        outside.write_bytes(iso_fixture())
        try:
            path.symlink_to(outside)
        except OSError:
            self.skipTest("Host cannot create symlinks")
        snapshot = read_index(self.database, self.root)
        self.assertEqual((len(snapshot.games), snapshot.skipped), (0, 1))

    def test_outside_and_prefix_sibling_records_are_not_in_scope(self):
        self.image()
        sibling = self.root.with_name("games-other")
        sibling.mkdir()
        other = sibling / "Other.iso"
        other.write_bytes(iso_fixture())
        self.catalog.upsert(other)
        snapshot = self.snapshot()
        self.assertEqual((snapshot.examined, len(snapshot.games)), (1, 1))

    def test_noncanonical_imported_alias_cannot_duplicate_a_source(self):
        self.check_noncanonical_alias()

    def test_noncanonical_alias_under_a_selected_root_symlink(self):
        alias = Path(self.tmp.name) / "selected-root"
        try:
            alias.symlink_to(self.root, target_is_directory=True)
        except OSError:
            self.skipTest("Host cannot create directory symlinks")
        self.root = alias
        self.check_noncanonical_alias()

    def check_noncanonical_alias(self):
        path = self.image()
        self.snapshot()
        alias = str(path.parent.resolve()) + os.sep + ".." + os.sep + "DVD" + os.sep + path.name
        self.catalog.db.execute("INSERT INTO games(image_path,title,media,image_bytes,updated) VALUES(?,?,?,?,?)",
                                (alias, "Alias title", "DVD", path.stat().st_size, 0))
        self.catalog.db.commit()
        snapshot = read_index(self.database, self.root)
        self.assertEqual((len(snapshot.games), snapshot.skipped), (1, 1))
        self.assertIn("Noncanonical", snapshot.details[0])

    def test_adapter_never_opens_image_payloads_or_writes_the_catalogue(self):
        self.image()
        self.snapshot()
        before = hashlib.sha256(self.database.read_bytes()).hexdigest()
        with mock.patch.object(Path, "open", side_effect=AssertionError("no image reads")):
            snapshot = read_index(self.database, self.root)
        self.assertEqual(len(snapshot.games), 1)
        self.assertEqual(hashlib.sha256(self.database.read_bytes()).hexdigest(), before)

    def test_old_catalogue_without_identity_table_is_not_migrated(self):
        self.image()
        self.snapshot(inspect=False)
        self.catalog.db.execute("DROP TABLE disc_identity")
        self.catalog.db.commit()
        snapshot = read_index(self.database, self.root)
        self.assertEqual(snapshot.games[0].identity_status, "not inspected")
        self.assertIsNone(self.catalog.db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='disc_identity'").fetchone())

    def test_missing_database_is_not_created(self):
        missing = self.database.with_name("missing.sqlite3")
        with self.assertRaises(FileNotFoundError):
            read_index(missing, self.root)
        self.assertFalse(missing.exists())

    def test_bounded_thousand_image_inventory_and_skipped_detail_budget(self):
        for i in range(1000):
            self.image(f"DVD/{i}.iso")
        snapshot = self.snapshot(inspect=False, max_entries=100)
        self.assertEqual((snapshot.examined, len(snapshot.games)), (100, 100))
        self.assertTrue(snapshot.limit_reached)
        for path in (self.root / "DVD").iterdir():
            path.unlink()
        missing = read_index(self.database, self.root, max_entries=100)
        self.assertEqual(missing.skipped, 100)
        self.assertEqual(len(missing.details), 20)

    def test_precancel_and_invalid_limits_do_not_open_the_database(self):
        cancel = threading.Event()
        cancel.set()
        with mock.patch("launcher.library_index.sqlite3.connect", side_effect=AssertionError("no DB open")):
            self.assertTrue(read_index(self.database, self.root, cancel=cancel).cancelled)
            for limit in (0, -1, 10001, True):
                with self.assertRaises(ValueError):
                    read_index(self.database, self.root, max_entries=limit)

    def test_mid_read_cancellation_keeps_a_partial_explicit_snapshot(self):
        for i in range(5):
            self.catalog.upsert(self.image(f"DVD/{i}.iso"))
        cancel = threading.Event()
        original_stat = Path.stat

        def stop_after_first(path, *args, **options):
            info = original_stat(path, *args, **options)
            if path.name == "0.iso":
                cancel.set()
            return info

        with mock.patch.object(Path, "stat", stop_after_first):
            snapshot = read_index(self.database, self.root, cancel=cancel)
        self.assertTrue(snapshot.cancelled)
        self.assertLess(snapshot.examined, 5)
        self.assertIn("cancelled", str(snapshot))

    def test_read_only_connection_rejects_attempted_database_writes(self):
        self.image()
        self.snapshot(inspect=False)
        connect = sqlite3.connect
        observed = []

        def check(*args, **options):
            db = connect(*args, **options)
            with self.assertRaises(sqlite3.OperationalError):
                db.execute("CREATE TABLE adapter_writes(value)")
            observed.append(args[0])
            return db

        with mock.patch("launcher.library_index.sqlite3.connect", side_effect=check):
            self.assertEqual(len(read_index(self.database, self.root).games), 1)
        self.assertIn("mode=ro", observed[0])


if __name__ == "__main__":
    unittest.main()
