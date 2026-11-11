"""Start on an SMBv2/SMBv3 card with no password must say why, not start-and-exit.

The server refuses to run with neither --user nor --open, and the card's
defaults on a fresh install are a username with a blank password. The GUI
used to launch the process anyway, so the card flashed Running and dropped
back to Stopped with the reason only in the Terminal tab.

Run:  python -m unittest tests.test_gui_smb2_start_needs_login -v
"""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class Smb2StartNeedsLogin(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from launcher import gui
        except ImportError:
            raise unittest.SkipTest("no tkinter")
        cls.gui = gui

    def _start(self, key, values):
        card = SimpleNamespace(values=lambda: values)
        app = SimpleNamespace(cards={key: card}, _launch_server=mock.Mock())
        with mock.patch.object(self.gui.messagebox, "showerror") as err, \
                mock.patch.object(self.gui.windows_setup, "is_windows", return_value=False):
            self.gui.LauncherApp.start_server(app, key)
        return err, app._launch_server

    def test_blank_password_is_refused_before_launch(self):
        for key in ("smbv2", "smbv3"):
            err, launch = self._start(
                key, {"games_folder": "/g", "username": "ps2"})
            err.assert_called_once()
            launch.assert_not_called()

    def test_password_or_open_share_launches(self):
        for extra in ({"password": "pw"}, {"open_share": True}):
            values = dict({"games_folder": "/g", "username": "ps2"}, **extra)
            err, launch = self._start("smbv2", values)
            err.assert_not_called()
            launch.assert_called_once()


if __name__ == "__main__":
    unittest.main()
