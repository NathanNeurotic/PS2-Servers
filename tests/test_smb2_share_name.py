"""SMBv2/SMBv3 cards take a share name, like SMBv1 -- without moving the default.

Run:  python -m unittest tests.test_smb2_share_name -v
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from launcher import gui, servers     # noqa: E402

KEYS = ("smbv2", "smbv3")


def _values(**overrides):
    v = {"games_folder": "C:/Games", "port": 1445,
         "username": "ps2", "password": "pw"}
    v.update(overrides)
    return v


class TheShareName(unittest.TestCase):
    def test_the_default_is_still_games(self):
        for key in KEYS:
            with self.subTest(key=key):
                argv = servers.REGISTRY[key].build_argv(_values())
                self.assertEqual(argv[:2], ["--share", "games=C:/Games"])

    def test_a_blank_share_name_falls_back_to_games(self):
        for key in KEYS:
            for blank in ("", "   ", None):
                with self.subTest(key=key, blank=blank):
                    argv = servers.REGISTRY[key].build_argv(_values(share_name=blank))
                    self.assertIn("games=C:/Games", argv)

    def test_a_custom_share_name_reaches_the_command_line(self):
        for key in KEYS:
            with self.subTest(key=key):
                argv = servers.REGISTRY[key].build_argv(_values(share_name="PS2SMB"))
                self.assertIn("PS2SMB=C:/Games", argv)
                self.assertNotIn("games=C:/Games", argv)

    def test_the_field_exists_with_the_documented_default(self):
        for key in KEYS:
            with self.subTest(key=key):
                by_key = {f.key: f for f in servers.REGISTRY[key].fields}
                self.assertEqual(by_key["share_name"].default, "games")

    def test_the_opl_hint_reads_back_a_custom_share(self):
        for key in KEYS:
            with self.subTest(key=key):
                hint = gui.opl_hint(key, "192.168.1.5", _values(share_name="PS2SMB"))
                self.assertIn("Share 'PS2SMB'", hint)


if __name__ == "__main__":
    unittest.main()
