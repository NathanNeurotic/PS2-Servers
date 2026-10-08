"""The launcher access check reports real failures without changing targets."""

import pathlib
import stat
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from launcher import path_access


class PathAccessTests(unittest.TestCase):
    def test_file_check_preserves_contents_and_size(self):
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / "game.img"
            original = b"game sectors" * 100
            target.write_bytes(original)
            usable, report = path_access.check_path(str(target), "file")
            self.assertTrue(usable)
            self.assertIn("Writable open succeeded", report)
            self.assertIn("No data was written", report)
            self.assertEqual(target.read_bytes(), original)

    def test_folder_listing_does_not_claim_game_read_or_write_success(self):
        with tempfile.TemporaryDirectory() as directory:
            usable, report = path_access.check_path(directory, "folder")
            self.assertTrue(usable)
            self.assertIn("Folder listing succeeded", report)
            self.assertIn("have not been tested", report)
            self.assertEqual(list(pathlib.Path(directory).iterdir()), [])

    def test_missing_mount_is_reported(self):
        with patch.object(path_access.os, "stat", side_effect=FileNotFoundError(
                2, "missing")):
            usable, report = path_access.check_path("/media/games", "folder")
        self.assertFalse(usable)
        self.assertIn("connected and mounted", report)

    def test_permission_guidance_matches_host(self):
        for system, expected in (("Linux", "mount ownership/permissions"),
                                 ("Windows", "Security permissions")):
            with self.subTest(system=system), \
                    patch.object(path_access.platform, "system", return_value=system), \
                    patch.object(path_access.os, "stat", side_effect=PermissionError(
                        13, "denied")):
                usable, report = path_access.check_path("games", "folder")
                self.assertFalse(usable)
                self.assertIn(expected, report)

    def test_readable_file_with_write_denied_remains_usable(self):
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / "game.img"
            target.write_bytes(b"sectors")
            original_open = open

            def deny_write(path, mode, **kwargs):
                if mode == "r+b":
                    raise PermissionError(13, "write denied")
                return original_open(path, mode, **kwargs)

            with patch("builtins.open", side_effect=deny_write):
                usable, report = path_access.check_path(str(target), "file")
            self.assertTrue(usable)
            self.assertIn("Writable open failed", report)
            self.assertIn("write denied", report)
            self.assertEqual(target.read_bytes(), b"sectors")

    def test_read_only_does_not_attempt_writable_open(self):
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / "game.img"
            target.write_bytes(b"sectors")
            with patch("builtins.open", wraps=open) as opener:
                usable, report = path_access.check_path(str(target), "file", True)
            self.assertTrue(usable)
            self.assertIn("writable access was not checked", report)
            self.assertEqual(opener.call_count, 1)
            self.assertEqual(opener.call_args.args[1], "rb")

    def test_raw_device_is_not_opened(self):
        with patch.object(path_access.os, "stat", return_value=SimpleNamespace(
                st_mode=stat.S_IFBLK)), patch("builtins.open") as opener:
            usable, report = path_access.check_path("/dev/test", "file")
        self.assertFalse(usable)
        self.assertIn("separate device access", report)
        opener.assert_not_called()

    def test_invalid_path_returns_a_result(self):
        usable, report = path_access.check_path("invalid\0path", "file")
        self.assertFalse(usable)
        self.assertIn("ValueError", report)
