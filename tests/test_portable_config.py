"""Portable builds keep their settings beside the program.

Run:
    python -m unittest tests.test_portable_config -v
"""

import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from launcher import config  # noqa: E402


class PortableConfigTest(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.mkdtemp(prefix="ps2-portable-test-")
        self.app = os.path.join(self.scratch, "app")
        self.user = os.path.join(self.scratch, "user")
        os.makedirs(self.app)
        os.makedirs(self.user)
        patches = [
            mock.patch.object(config, "_app_dir", return_value=self.app),
            mock.patch.object(config, "_user_config_dir", return_value=self.user),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.scratch, True)

    def _marker(self):
        open(os.path.join(self.app, config.PORTABLE_MARKER), "w").close()

    def test_without_marker_uses_user_dir(self):
        self.assertFalse(config.is_portable())
        self.assertEqual(config.config_dir(), self.user)

    def test_marker_keeps_settings_beside_program(self):
        self._marker()
        self.assertTrue(config.is_portable())
        config.save({"a": 1})
        self.assertEqual(os.path.dirname(config.config_path()), os.path.join(self.app, "data"))
        self.assertTrue(os.path.exists(os.path.join(self.app, "data", "launcher.json")))
        self.assertFalse(os.path.exists(os.path.join(self.user, "launcher.json")))
        self.assertEqual(config.load(), {"a": 1})

    def test_legacy_user_settings_are_read_once_then_saved_beside_program(self):
        with open(os.path.join(self.user, "launcher.json"), "w") as f:
            f.write('{"old": true}')
        self._marker()
        self.assertEqual(config.load(), {"old": True})
        config.save({"new": True})
        self.assertEqual(config.load(), {"new": True})

    def test_unwritable_data_dir_falls_back_to_user_dir(self):
        self._marker()
        with mock.patch.object(config.os, "access", return_value=False):
            self.assertFalse(config.is_portable())
            self.assertEqual(config.config_dir(), self.user)


if __name__ == "__main__":
    unittest.main()
