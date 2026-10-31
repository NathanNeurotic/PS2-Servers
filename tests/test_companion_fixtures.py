"""Every #249 consumer over the same fixture library (tests/library_fixtures.py).

This is the shared acceptance fixture the capability matrix
(docs/COMPANION-CAPABILITY-MATRIX.md) points to. Each test reads one consumer:
the Desktop catalogue scan and inspection, the shared index, both HTTP list
profiles, the achievements library and the PS1 descriptors. Host behaviour
only: none of this shows a console can list or launch a game.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _path in (ROOT, os.path.join(ROOT, "http_server"), os.path.join(ROOT, "udpfs_server")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import http_server as hs  # noqa: E402
from launcher import game_library, ps1_index  # noqa: E402
from launcher.library_catalog import Catalog  # noqa: E402
from tests import library_fixtures as fx  # noqa: E402


class CompanionFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        cls.root = base / "library"
        cls.root.mkdir()
        cls.made = fx.build(cls.root, base / "outside")
        cls.catalog = Catalog(base / "state" / "library.sqlite3")
        cls.scan = cls.catalog.scan_detailed(cls.root, recursive=True, max_depth=4)
        cls.inspection = cls.catalog.inspect_identities(cls.root)
        cls.rows = {Path(r["image_path"]).relative_to(cls.root).as_posix(): r
                    for r in cls.catalog.list_games()}

    @classmethod
    def tearDownClass(cls):
        cls.catalog.__exit__()
        fx.unlock(cls.root)
        cls.tmp.cleanup()

    # Desktop catalogue -------------------------------------------------------

    def test_recursive_scan_indexes_ps2_images_to_four_levels_only(self):
        expected = set(fx.INSPECTED) | set(fx.COMPRESSED) | {fx.TRUNCATED}
        if not self.made["locked"]:
            expected.add(fx.LOCKED + "/Hidden.iso")  # still readable (root, or Windows)
        self.assertEqual(set(self.rows), expected)
        self.assertNotIn(fx.TOO_DEEP, self.rows)
        self.assertGreaterEqual(self.scan.skipped_depth, 1)

    def test_ps1_games_never_enter_the_ps2_catalogue(self):
        self.assertFalse(any(p.startswith(("POPS/", "EMBER/")) for p in self.rows))

    def test_links_out_of_the_root_and_locked_folders_are_not_indexed(self):
        self.assertFalse(any("Escaped" in p for p in self.rows))
        if self.made["link"]:
            self.assertGreaterEqual(self.scan.skipped_links, 1)
        if self.made["locked"]:
            self.assertFalse(any("Hidden" in p for p in self.rows))
            self.assertGreaterEqual(self.scan.errors, 1)

    def test_inspection_verifies_real_volumes_and_guesses_nothing_else(self):
        for relative, serial in fx.INSPECTED.items():
            row = self.rows[relative]
            self.assertEqual(row["detected_disc_id"], serial, relative)
            self.assertIn(row["identity_status"], ("verified", "conflict"), relative)
        for relative in (*fx.COMPRESSED, fx.TRUNCATED):
            self.assertEqual(self.rows[relative]["detected_disc_id"], "", relative)
            self.assertNotIn(self.rows[relative]["identity_status"], ("verified", "conflict"), relative)

    def test_filename_and_boot_serial_disagreement_is_a_conflict(self):
        # "Same Name.iso" carries no serial in its name, so it verifies; the
        # standard images carry the right one, so they verify too.
        self.assertEqual(self.rows["Games/A/Same Name.iso"]["identity_status"], "verified")
        self.assertEqual(self.rows["DVD/SLUS_201.74.Standard DVD.iso"]["identity_status"], "verified")

    # Shared index ------------------------------------------------------------

    def test_index_reports_duplicates_without_collapsing_paths(self):
        snapshot = self.catalog.indexed_games(self.root)
        paths = {g.relative_path for g in snapshot.games}
        self.assertIn("Games/A/Same Name.iso", paths)
        self.assertIn("Games/B/Same Name.iso", paths)
        kinds = {(c.kind, c.key) for c in snapshot.conflicts}
        self.assertTrue(any("same name.iso" in key.lower() for _k, key in kinds), kinds)
        self.assertTrue(any("SLUS_233.33" in key for _k, key in kinds), kinds)

    # HTTP --------------------------------------------------------------------

    def test_legacy_flat_list_is_top_level_only(self):
        csv = hs.GameIndex(str(self.root)).games_csv().decode("utf-8")
        self.assertIn("SLUS_201.74.Standard DVD.iso", csv)
        self.assertNotIn("Nested", csv)
        self.assertNotIn("Same Name", csv)

    def test_path_aware_list_serves_nested_and_same_named_games_by_path(self):
        index = hs.GameIndex(str(self.root), profile=hs.PROFILE_RIPTOPL_PATH_AWARE, recursive=True)
        csv = index.games_csv().decode("utf-8")
        for relative in ("Games/RPG/Deep/Four/SLPM_650.01.Nested.iso", "Games/A/Same Name.iso",
                         "Games/Ünïcode, comma.iso"):
            self.assertIn('"{}"'.format(relative), csv)
            self.assertIsNotNone(index.lookup(relative), relative)
        # RiptOPL keeps one row per STARTUP, and STARTUP comes from the
        # filename, so a second image with the same basename is left out of
        # the list (and not served) rather than shadowing the first.
        self.assertNotIn("Games/B/Same Name.iso", csv)
        self.assertIsNone(index.lookup("Games/B/Same Name.iso"))
        self.assertNotIn("TooDeep", csv)
        self.assertNotIn("Escaped", csv)
        self.assertNotIn(".VCD", csv)
        self.assertIsNone(index.lookup("Same Name.iso"))  # no bare-filename fallback

    # Achievements library and PS1 --------------------------------------------

    def test_achievements_library_lists_top_level_ps2_and_pops_vcd(self):
        found = {(row["kind"], row["name"]) for row in game_library.Library.installed(self.root)}
        self.assertIn(("DVD", "SLUS_201.74.Standard DVD.iso"), found)
        self.assertIn(("POPS", "SLUS_007.08.PS1 Game.VCD"), found)
        self.assertFalse(any("Nested" in name for _k, name in found))

    def test_ps1_descriptors_keep_ps1_identity_apart(self):
        games, _problems = ps1_index.scan(self.root)
        by_path = {g.relative_path: g for g in games}
        self.assertEqual(set(by_path), {fx.VCD, fx.EMBER_GAME})
        vcd, ember = by_path[fx.VCD], by_path[fx.EMBER_GAME]
        self.assertEqual((vcd.core, vcd.filename_id), ("popstarter", "SLUS_007.08"))
        self.assertEqual((ember.core, ember.disc_image, ember.filename_id),
                         ("ember", "Game.cue", "SCUS_944.26"))
        for game in games:
            self.assertEqual(game.ps2_boot_serial, "")
            self.assertEqual(game.identity_status, "not applicable (PS1)")


class Ps1RuleTests(unittest.TestCase):
    def test_ember_name_rules_match_riptopl(self):
        self.assertEqual(ps1_index.ember_name_problem("Crash Bandicoot"), "")
        for bad in ("..hidden", "..", "a:b", "a\\b", "a/b", "", "x" * 181):
            self.assertTrue(ps1_index.ember_name_problem(bad), bad)
        self.assertEqual(ps1_index.ember_name_problem("é" * 90), "")  # 180 bytes
        self.assertTrue(ps1_index.ember_name_problem("é" * 91))

    def test_ember_disc_priority_and_case(self):
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp, "ember", "GAMES", "Two Discs")
            game.mkdir(parents=True)
            for name in ("b.BIN", "a.EXE", "z.Cue"):
                (game / name).write_bytes(b"x")
            Path(tmp, "ember", "GAMES", "..dots").mkdir()
            (Path(tmp, "ember", "GAMES", "..dots") / "d.bin").write_bytes(b"x")
            games, _ = ps1_index.scan(tmp)
        by_name = {g.name: g for g in games}
        self.assertEqual(by_name["Two Discs"].disc_image, "z.Cue")
        self.assertFalse(by_name["..dots"].launchable)


class StressTests(unittest.TestCase):
    def test_thousand_images_stay_inside_the_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            fx.stress(tmp, 1000)
            with Catalog(Path(tmp) / "db.sqlite3") as db:
                result = db.scan_detailed(tmp, recursive=True, max_depth=4, max_entries=600)
                self.assertTrue(result.limit_reached)
                self.assertLessEqual(len(db.list_games()), 600)
            index = hs.GameIndex(tmp, profile=hs.PROFILE_RIPTOPL_PATH_AWARE, recursive=True)
            self.assertLessEqual(len(index.games_csv()), hs.RIPTOPL_CATALOG_BYTES_MAX)


if __name__ == "__main__":
    unittest.main()
