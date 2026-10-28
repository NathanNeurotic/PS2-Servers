"""Identity cache integration without changing existing metadata or either RA DB."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest import mock

from launcher import disc_identity
from launcher.library_catalog import Catalog
from tests.test_disc_identity import iso_fixture


class LibraryIdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "games"
        self.root.mkdir()
        self.database = Path(self.tmp.name) / "catalog.sqlite3"
        self.db = Catalog(self.database)
        self.addCleanup(self.db.db.close)

    def image(self, relative="DVD/Game.iso", **options):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(iso_fixture(**options))
        return path

    def scan_and_inspect(self):
        self.db.scan_detailed(self.root, recursive=True)
        return self.db.inspect_identities(self.root)

    def test_opt_in_inspection_keeps_custom_metadata_and_media(self):
        self.image("Arbitrary/Game.iso")
        self.db.scan_detailed(self.root, recursive=True)
        row = self.db.list_games()[0]
        self.assertEqual(row["identity_status"], "not inspected")
        self.db.edit(row["id"], "My title", "", "My notes")
        result = self.db.inspect_identities(self.root)
        updated = self.db.get_game(row["id"])
        self.assertEqual((result.verified, result.inspected, result.errors), (1, 1, 0))
        self.assertEqual(updated["detected_disc_id"], "SLUS_123.45")
        self.assertEqual((updated["title"], updated["disc_id"], updated["notes"], updated["media"]),
                         ("My title", "", "My notes", "UNKNOWN"))
        self.assertEqual(self.db.list_games("slus_123.45")[0]["id"], row["id"])

    def test_filename_and_manual_conflicts_retain_both_ids(self):
        self.image("DVD/SLUS_999.99.Wrong name.iso")
        result = self.scan_and_inspect()
        row = self.db.list_games()[0]
        self.assertEqual(result.conflicts, 1)
        self.assertEqual(row["identity_status"], "conflict")
        self.assertIn("Filename ID SLUS_999.99", row["identity_detail"])
        self.db.edit(row["id"], row["title"], "SLES_111.11")
        updated = self.db.get_game(row["id"])
        self.assertEqual(updated["disc_id"], "SLES_111.11")
        self.assertEqual(updated["detected_disc_id"], "SLUS_123.45")
        self.assertIn("Manual ID differs", updated["identity_detail"])

    def test_cached_result_rechecks_conflicts_after_manual_edit(self):
        self.image()
        self.scan_and_inspect()
        row = self.db.list_games()[0]
        self.db.edit(row["id"], "Custom", "SLUS_999.99")
        with mock.patch.object(disc_identity, "inspect_disc", side_effect=AssertionError("cached")):
            result = self.db.inspect_identities(self.root)
        self.assertEqual((result.cached, result.conflicts), (1, 1))
        self.db.edit(row["id"], "Custom", "slus-123.45")
        self.assertEqual(self.db.get_game(row["id"])["identity_status"], "verified")

    def test_changed_image_is_reinspected_and_rescan_invalidates_old_id(self):
        path = self.image()
        self.scan_and_inspect()
        with mock.patch.object(disc_identity, "inspect_disc", side_effect=AssertionError("cached")):
            self.assertEqual(self.db.inspect_identities(self.root).cached, 1)
        path.write_bytes(iso_fixture(boot="SLES_543.21"))
        # Ensure a distinct timestamp even on filesystems with coarse clocks.
        before = path.stat()
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 2_000_000_000))
        self.db.scan(self.root)
        self.assertEqual(self.db.list_games()[0]["identity_status"], "not inspected")
        self.assertEqual(self.db.inspect_identities(self.root).inspected, 1)
        self.assertEqual(self.db.list_games()[0]["detected_disc_id"], "SLES_543.21")

    def test_parser_version_change_invalidates_cached_inspection(self):
        self.image()
        self.scan_and_inspect()
        with mock.patch.object(disc_identity, "INSPECTOR_VERSION", disc_identity.INSPECTOR_VERSION + 1):
            self.assertEqual(self.db.list_games()[0]["identity_status"], "not inspected")
            self.assertEqual(self.db.inspect_identities(self.root).inspected, 1)

    def test_unavailable_files_clear_stale_verified_id_and_are_retried(self):
        path = self.image()
        self.scan_and_inspect()
        path.unlink()
        result = self.db.inspect_identities(self.root)
        self.assertEqual(result.errors, 1)
        self.assertEqual(self.db.list_games()[0]["identity_status"], "unavailable")
        self.assertEqual(self.db.list_games()[0]["detected_disc_id"], "")
        path.write_bytes(iso_fixture())
        self.assertEqual(self.db.inspect_identities(self.root).verified, 1)

    def test_source_change_between_cache_lookup_and_open_is_not_cached_as_verified(self):
        path = self.image()
        self.db.scan(self.root)
        inspect = disc_identity.inspect_disc

        def change(source, **options):
            path.write_bytes(iso_fixture(boot="SLES_543.21") + b"changed")
            return inspect(source, **options)

        with mock.patch.object(disc_identity, "inspect_disc", side_effect=change):
            self.assertEqual(self.db.inspect_identities(self.root).errors, 1)
        self.assertEqual(self.db.list_games()[0]["identity_status"], "changed")
        self.assertEqual(self.db.inspect_identities(self.root).verified, 1)

    def test_same_basename_and_serial_at_different_paths_are_separate_rows(self):
        self.image("DVD/One/Game.iso")
        self.image("DVD/Two/Game.iso")
        self.assertEqual(self.scan_and_inspect().verified, 2)
        rows = self.db.list_games()
        self.assertEqual(len({row["id"] for row in rows}), 2)
        self.assertEqual({row["detected_disc_id"] for row in rows}, {"SLUS_123.45"})

    def test_only_selected_root_is_read_even_with_imported_catalogue_paths(self):
        self.image()
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        other = outside / "Other.iso"
        other.write_bytes(iso_fixture())
        self.db.upsert(other)
        self.assertEqual(self.scan_and_inspect().visited, 1)
        rows = {r["image_path"]: r for r in self.db.list_games()}
        self.assertEqual(rows[str(other.resolve())]["identity_status"], "not inspected")

    def test_symlink_swapped_in_after_scan_is_not_opened(self):
        path = self.image()
        self.db.scan(self.root)
        outside = Path(self.tmp.name) / "Outside.iso"
        outside.write_bytes(iso_fixture())
        path.unlink()
        try:
            path.symlink_to(outside)
        except OSError:
            self.skipTest("Host cannot create symlinks; also exercised on Linux")
        with mock.patch.object(disc_identity, "inspect_disc", side_effect=AssertionError("must not open")):
            self.assertEqual(self.db.inspect_identities(self.root).errors, 1)

    def test_compressed_unknown_and_malformed_have_separate_counts(self):
        self.image("DVD/Game.cso")
        self.image("DVD/Homebrew.iso", boot="MAIN.ELF")
        path = self.image("DVD/Invalid.iso")
        path.write_bytes(b"invalid")
        result = self.scan_and_inspect()
        self.assertEqual((result.unsupported, result.unknown, result.invalid, result.errors), (1, 1, 1, 0))

    def test_limit_cancel_progress_and_safe_resumption(self):
        for i in range(20):
            self.image(f"DVD/{i}.iso")
        self.db.scan(self.root)
        limited = self.db.inspect_identities(self.root, max_entries=2)
        self.assertEqual(limited.visited, 2)
        self.assertTrue(limited.limit_reached)
        cancel = threading.Event()
        snapshots = []

        def progress(result):
            snapshots.append(result)
            cancel.set()

        result = self.db.inspect_identities(self.root, cancel=cancel, progress=progress)
        self.assertTrue(result.cancelled)
        self.assertEqual(result.visited, 16)
        self.assertFalse(snapshots[0].cancelled)
        self.assertEqual(self.db.inspect_identities(self.root).verified, 20)
        self.assertEqual(len(self.db.list_games()), 20)

    def test_precancel_and_options_are_checked_before_reading(self):
        self.image()
        self.db.scan(self.root)
        cancel = threading.Event()
        cancel.set()
        with mock.patch.object(disc_identity, "inspect_disc", side_effect=AssertionError("must not open")):
            self.assertTrue(self.db.inspect_identities(self.root, cancel=cancel).cancelled)
            for limit in (0, -1, 10001, True):
                with self.assertRaises(ValueError):
                    self.db.inspect_identities(self.root, max_entries=limit)
        self.assertEqual(self.db.list_games()[0]["identity_status"], "not inspected")

    def test_existing_schema_and_sqlite_backup_keep_manual_rows_compatible(self):
        self.image()
        self.db.scan(self.root)
        columns = [r[1] for r in self.db.db.execute("PRAGMA table_info(games)")]
        self.db.inspect_identities(self.root)
        self.assertEqual([r[1] for r in self.db.db.execute("PRAGMA table_info(games)")], columns)
        target = Path(self.tmp.name) / "backup.sqlite3"
        self.db.backup_sqlite(target)
        with Catalog(target) as copy:
            self.assertEqual(copy.list_games()[0]["detected_disc_id"], "SLUS_123.45")
        with closing(sqlite3.connect(target)) as legacy:
            self.assertEqual(len(legacy.execute("SELECT * FROM games").fetchone()), 8)

    def test_json_import_never_trusts_exported_verified_claims(self):
        self.image()
        self.scan_and_inspect()
        export = Path(self.tmp.name) / "metadata.json"
        self.db.export_json(export)
        self.assertEqual(json.loads(export.read_text())["games"][0]["detected_disc_id"], "SLUS_123.45")
        with Catalog(Path(self.tmp.name) / "other.sqlite3") as copy:
            copy.import_json(export)
            self.assertEqual(copy.list_games()[0]["identity_status"], "not inspected")
            self.assertEqual(copy.inspect_identities(self.root).verified, 1)


if __name__ == "__main__":
    unittest.main()
