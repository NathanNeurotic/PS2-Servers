"""Verify that Caduceus pairing reuses only an unambiguous game share."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from launcher.ra_setup import candidate_roots
from launcher.gui import ServerCard


class CaduceusShareTests(unittest.TestCase):
    def test_prefers_launched_roots_and_deduplicates_shared_modes(self):
        with tempfile.TemporaryDirectory() as base:
            live = str(Path(base) / "live")
            values = {"smbv1": {"games_folder": "old", "password": "secret"},
                      "udpfs": {"root_dir": "unused"},
                      "http": {"root_dir": "unused"}}
            active = {"smbv1": {"games_folder": live, "password": "secret"},
                      "udpfs": {"root_dir": live}}
            result = candidate_roots(values, active)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0], (live, ("smbv1", "udpfs")))
            self.assertNotIn("secret", repr(result))
            self.assertNotIn("old", repr(result))

    def test_multiple_roots_remain_ambiguous(self):
        with tempfile.TemporaryDirectory() as base:
            a = str(Path(base) / "a")
            b = str(Path(base) / "b")
            result = candidate_roots({"smbv2": {"games_folder": a},
                                      "udpbd": {"virtual_folder": b}})
            self.assertEqual(len(result), 2)
            self.assertEqual({x[0] for x in result}, {a, b})

    def test_virtual_exfat_is_allowed_but_block_devices_are_not(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(candidate_roots({"udpbd": {"virtual_folder": root}}),
                             [(root, ("udpbd",))])
            self.assertEqual(candidate_roots({"udpbd": {"image_file": "/disk.img",
                                                       "raw_device": "/dev/sdb"}}), [])
            self.assertEqual(candidate_roots({"retroachievements": {"games_folder": root}}), [])

    def test_button_uses_folder_without_implicitly_starting_ra(self):
        with tempfile.TemporaryDirectory() as root:
            card = mock.Mock()
            card.app.is_running.return_value = False
            card.app.caduceus_root_candidates.return_value = [(root, ("smbv1",))]
            card.vars = {"games_folder": mock.Mock()}
            card.vars["games_folder"].get.return_value = ""
            card.app._save.return_value = True
            with mock.patch("launcher.gui.messagebox.showinfo"), \
                 mock.patch("launcher.gui.messagebox.showerror"), \
                 mock.patch("launcher.gui.messagebox.showwarning"):
                ServerCard._use_shared_game_folder(card)
            card.vars["games_folder"].set.assert_called_once_with(root)
            card.app._save.assert_called_once()
            card.app.is_running.assert_called_once_with("retroachievements")
            self.assertTrue({call[0] for call in card.app.mock_calls} <=
                            {"is_running", "caduceus_root_candidates", "_save"})

    def test_button_never_guesses_between_roots_or_edits_live_settings(self):
        card = mock.Mock()
        card.vars = {"games_folder": mock.Mock()}
        card.app.is_running.return_value = True
        with mock.patch("launcher.gui.messagebox.showinfo") as dialog:
            ServerCard._use_shared_game_folder(card)
        dialog.assert_called_once()
        card.app.caduceus_root_candidates.assert_not_called()
        card.vars["games_folder"].set.assert_not_called()
        card.app.is_running.return_value = False
        card.app.caduceus_root_candidates.return_value = [
            ("/first", ("udpfs",)), ("/second", ("smbv2",))]
        with mock.patch("launcher.gui.messagebox.showinfo") as dialog:
            ServerCard._use_shared_game_folder(card)
        dialog.assert_called_once()
        card.vars["games_folder"].set.assert_not_called()
        card.app._save.assert_not_called()

    def test_existing_user_folder_requires_confirmation(self):
        with tempfile.TemporaryDirectory() as root:
            card = mock.Mock()
            card.app.is_running.return_value = False
            card.app.caduceus_root_candidates.return_value = [(root, ("smbv3",))]
            card.vars = {"games_folder": mock.Mock()}
            card.vars["games_folder"].get.return_value = os.path.join(root, "existing")
            with mock.patch("launcher.gui.messagebox.askyesno", return_value=False) as ask:
                ServerCard._use_shared_game_folder(card)
            ask.assert_called_once()
            card.vars["games_folder"].set.assert_not_called()
            card.app._save.assert_not_called()
