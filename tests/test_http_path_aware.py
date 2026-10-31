"""The opt-in riptopl-path-aware games.csv profile.

The flat default is covered by tests/test_http_server.py and must not change.
These tests cover the four-field RiptOPL catalogue: real relative paths,
quoting, the client's own row limits, and a lookup that never falls back to a
bare filename. conformance/integration/riptopl_http/run.py checks the same
output against RiptOPL's compiled C parser.

Run:  python -m unittest tests.test_http_path_aware -v
"""

import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _path in (ROOT, os.path.join(ROOT, "http_server"), os.path.join(ROOT, "udpfs_server")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import http_server as hs  # noqa: E402
from launcher import servers  # noqa: E402

PAYLOAD = bytes(range(256)) * 16


def _parse_row(line):
    """Split one four-field row the way RiptOPL's httpSplitFields does."""
    fields, i = [], 0
    while True:
        if line[i] == '"':
            i += 1
            value = ""
            while True:
                if line[i] == '"':
                    if i + 1 < len(line) and line[i + 1] == '"':
                        value += '"'
                        i += 2
                        continue
                    i += 1
                    break
                value += line[i]
                i += 1
        else:
            end = line.find(",", i)
            end = len(line) if end < 0 else end
            value, i = line[i:end], end
        fields.append(value)
        if i >= len(line):
            return fields
        assert line[i] == ",", line
        i += 1


class _Tree(unittest.TestCase):
    profile = hs.PROFILE_RIPTOPL_PATH_AWARE
    recursive = False

    def setUp(self):
        self.work = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.work, True)

    def write(self, rel, payload=PAYLOAD):
        path = os.path.join(self.work, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(payload)
        return path

    def index(self, **options):
        options.setdefault("profile", self.profile)
        options.setdefault("recursive", self.recursive)
        with mock.patch.object(hs, "_log"):
            return hs.GameIndex(self.work, **options)

    def rows(self, index):
        text = index.games_csv().decode("utf-8")
        return [_parse_row(line) for line in text.splitlines()]


class CatalogueTests(_Tree):
    def test_rows_carry_title_media_and_the_real_relative_path(self):
        self.write("DVD/SLUS_201.74.Rumble Racing.iso")
        self.write("CD/SCUS_971.24.Ratchet & Clank, The.iso")
        self.write("Loose.iso")
        rows = self.rows(self.index())
        self.assertEqual(rows, [
            ["SCUS_971.24", "Ratchet & Clank, The", "CD", "CD/SCUS_971.24.Ratchet & Clank, The.iso"],
            ["SLUS_201.74", "Rumble Racing", "DVD", "DVD/SLUS_201.74.Rumble Racing.iso"],
            ["Loose", "Loose", "CD", "Loose.iso"],
        ])

    def test_every_field_is_quoted_so_a_leading_hash_is_not_a_comment(self):
        self.write("DVD/#Hash, \"Quote\".iso")
        line = self.index().games_csv().decode("utf-8").splitlines()[0]
        self.assertTrue(line.startswith('"'))
        self.assertEqual(_parse_row(line)[3], 'DVD/#Hash, "Quote".iso')

    def test_flat_default_is_unchanged(self):
        self.write("DVD/SLUS_201.74.Rumble Racing.iso")
        flat = self.index(profile=hs.PROFILE_LEGACY_FLAT)
        self.assertEqual(flat.games_csv(), b"SLUS_201.74,SLUS_201.74.Rumble Racing.iso,DVD\n")

    def test_a_later_duplicate_startup_is_left_out_and_logged(self):
        self.write("CD/SLUS_201.74.Rumble Racing.iso")
        self.write("DVD/SLUS_201.74.Rumble Racing.iso")
        with mock.patch.object(hs, "_log") as log:
            index = hs.GameIndex(self.work, profile=self.profile)
        self.assertEqual([r[3] for r in self.rows(index)], ["CD/SLUS_201.74.Rumble Racing.iso"])
        self.assertTrue(any("already used" in str(c) for c in log.call_args_list))

    def test_rows_the_client_would_refuse_are_left_out(self):
        self.write(" leading space.iso")  # a root path the client would trim
        # 254 bytes is a legal filename but 258 bytes as a catalogue path.
        self.write("DVD/" + "\u00e9" * 125 + ".iso")
        self.write("DVD/Fine.iso")
        self.assertEqual([r[3] for r in self.rows(self.index())], ["DVD/Fine.iso"])

    def test_row_and_byte_limits_drop_rather_than_overflow(self):
        entries = [hs.GameEntry("S{:05d}".format(i), "g.iso", "/x", "DVD", 1, False, True,
                                rel_path="DVD/{:05d}.iso".format(i), title="T")
                   for i in range(hs.RIPTOPL_CATALOG_ROWS_MAX + 5)]
        index = self.index()
        body, dropped = index._build_riptopl_csv(entries)
        self.assertEqual(body.count(b"\n"), hs.RIPTOPL_CATALOG_ROWS_MAX)
        self.assertEqual(dropped, 5)
        with mock.patch.object(hs, "RIPTOPL_CATALOG_BYTES_MAX", 100):
            body, dropped = index._build_riptopl_csv(entries[:10])
        self.assertLessEqual(len(body), 100)
        self.assertEqual(body.count(b"\n") + dropped, 10)

    def test_row_problems_mirror_the_client_parser(self):
        good = ("SLUS_201.74", "Title", "DVD/Game.iso")
        self.assertIsNone(hs.riptopl_row_problem(*good))
        for startup, title, path in (
                ("", "T", "a.iso"), ("1234567890123", "T", "a.iso"), ("S", "T\x01", "a.iso"),
                ("S", "T", "/a.iso"), ("S", "T", "a/../b.iso"), ("S", "T", "a//b.iso"),
                ("S", "T", "a\\b.iso"), ("S", "T", "http://a.iso"), ("S", "T", "a.cso"),
                ("S", "T ", "a.iso"), ("S", "T" * 161, "a.iso"), ("S", "T", "a" * 256 + ".iso"),
                ("S", "T", "é" * 120 + ".iso")):
            self.assertIsNotNone(hs.riptopl_row_problem(startup, title, path),
                                 (startup, title, path))

    def test_encoding_matches_the_client(self):
        self.assertEqual(hs.riptopl_encoded_path("DVD/A b#%Ü.iso"),
                         "DVD/A%20b%23%25%C3%9C.iso")

    def test_unconventional_startup_is_cut_on_a_utf8_boundary(self):
        self.write("DVD/ÜÜÜÜÜÜÜ.iso")
        startup = self.rows(self.index())[0][0]
        self.assertLessEqual(len(startup.encode("utf-8")), hs.RIPTOPL_STARTUP_MAX)
        self.assertEqual(startup, "Ü" * 6)


class LookupTests(_Tree):
    def test_base_path_prefix_is_ignored_and_paths_are_exact(self):
        self.write("CD/SCUS_971.24.Some Game.iso", b"C" * 4096)
        self.write("DVD/SLUS_201.74.Other.iso", b"D" * 4096)
        index = self.index()
        self.assertEqual(index.lookup("PS2/games/CD/SCUS_971.24.Some Game.iso").path,
                         os.path.join(self.work, "CD", "SCUS_971.24.Some Game.iso"))
        self.assertEqual(index.lookup("DVD/SLUS_201.74.Other.iso").media, "DVD")

    def test_no_bare_filename_fallback(self):
        self.write("DVD/SLUS_201.74.Rumble Racing.iso")
        self.assertIsNone(self.index().lookup("SLUS_201.74.Rumble Racing.iso"))

    def test_traversal_and_unknown_paths_resolve_to_nothing(self):
        self.write("DVD/Game.iso")
        index = self.index()
        for name in ("../DVD/Game.iso", "DVD/../../etc/passwd", "DVD/game.isox", ""):
            entry = index.lookup(name)
            if name.startswith("../"):
                # normpath folds the dot-dot; the remainder is still whitelisted.
                self.assertIsNotNone(entry)
            else:
                self.assertIsNone(entry, name)

    @unittest.skipIf(os.path.normcase("A") == "a", "needs a case-sensitive filesystem")
    def test_case_aliases_need_an_exact_match(self):
        self.write("DVD/Game.iso", b"1" * 2048)
        self.write("DVD/GAME.iso", b"2" * 2048)
        self.write("DVD/Solo.iso")
        index = self.index()
        self.assertTrue(index.lookup("DVD/Game.iso").path.endswith("Game.iso"))
        self.assertTrue(index.lookup("DVD/GAME.iso").path.endswith("GAME.iso"))
        self.assertIsNone(index.lookup("DVD/game.iso"))
        self.assertIsNotNone(index.lookup("dvd/solo.iso"))

    def test_compressed_images_are_listed_and_served_as_virtual_iso(self):
        if not hs.COMPRESSION_AVAILABLE:
            self.skipTest("compression support unavailable")
        import compression_selftest as cs
        cs.make_cso(os.path.join(self.work, "SLUS_202.02.Cso Game.cso"), PAYLOAD)
        index = self.index()
        self.assertEqual(self.rows(index)[0][3], "SLUS_202.02.Cso Game.iso")
        entry = index.lookup("SLUS_202.02.Cso Game.iso")
        self.assertTrue(entry.compressed)
        self.assertEqual(entry.size, len(PAYLOAD))

    def test_raw_zso_is_listed_with_its_own_extension_when_not_decoded(self):
        self.write("DVD/Game.ziso")
        index = self.index(enable_compression=False)
        self.assertEqual(self.rows(index)[0][3], "DVD/Game.zso")


class RecursiveTests(_Tree):
    recursive = True

    def test_nested_games_to_four_levels_with_cd_dvd_media(self):
        self.write("a/b/c/d/Four.iso")
        self.write("a/b/c/d/e/Five.iso")
        self.write("DVD/Series/SLUS_100.01.Nested.iso")
        paths = {r[3]: r[2] for r in self.rows(self.index())}
        self.assertEqual(paths, {"a/b/c/d/Four.iso": "CD",
                                 "DVD/Series/SLUS_100.01.Nested.iso": "DVD"})

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_linked_folders_and_files_outside_the_root_are_not_listed(self):
        outside = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, outside, True)
        with open(os.path.join(outside, "Escape.iso"), "wb") as handle:
            handle.write(PAYLOAD)
        try:
            os.symlink(outside, os.path.join(self.work, "linked"))
            os.symlink(os.path.join(outside, "Escape.iso"), os.path.join(self.work, "File.iso"))
        except (OSError, NotImplementedError):
            self.skipTest("cannot create symlinks here")
        self.write("Real.iso")
        self.assertEqual([r[3] for r in self.rows(self.index())], ["Real.iso"])

    def test_recursive_flat_profile_is_refused(self):
        with self.assertRaises(ValueError):
            hs.GameIndex(self.work, profile=hs.PROFILE_LEGACY_FLAT, recursive=True)


class LauncherArgvTests(unittest.TestCase):
    def argv(self, **values):
        values.setdefault("root_dir", "/games")
        return servers.HTTP.build_argv(values)

    def test_default_card_command_line_is_unchanged(self):
        self.assertEqual(self.argv(), ["--root-dir", "/games"])
        self.assertEqual(self.argv(catalog_profile="Legacy flat (OPL-HTTP)"),
                         ["--root-dir", "/games"])

    def test_path_aware_label_or_value_adds_the_flag(self):
        for value in ("RiptOPL path-aware", "riptopl-path-aware"):
            self.assertEqual(self.argv(catalog_profile=value, recursive=True),
                             ["--root-dir", "/games", "--catalog-profile",
                              "riptopl-path-aware", "--recursive"])

    def test_nested_folders_checkbox_is_ignored_with_the_flat_list(self):
        self.assertEqual(self.argv(recursive=True), ["--root-dir", "/games"])

    def test_cli_refuses_recursive_without_the_profile(self):
        with mock.patch.object(hs, "_log"):
            self.assertEqual(hs.main(["--root-dir", ROOT, "--recursive"]), 2)


if __name__ == "__main__":
    unittest.main()
