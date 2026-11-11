"""Verbose logging defaults on (one-time migration) and Terminal lines carry a time.

Run:  python -m unittest tests.test_verbose_default_and_log_stamps -v
"""

import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from launcher.servers import (  # noqa: E402
    REGISTRY, VERBOSE_MIGRATION_KEY, migrate_verbose_default)


def _verbose_fields():
    return [(k, f) for k, s in REGISTRY.items() for f in s.fields if f.key == "verbose"]


class VerboseDefault(unittest.TestCase):
    def test_every_verbose_field_defaults_on(self):
        fields = _verbose_fields()
        self.assertTrue(fields)
        for key, f in fields:
            with self.subTest(server=key):
                self.assertIs(f.default, True)

    def test_existing_config_is_switched_on_once(self):
        saved = {"servers": {"udpfs": {"verbose": False, "port": 62966},
                             "smbv2": {"verbose": False}},
                 "ip": "10.0.0.2"}
        out = migrate_verbose_default(saved)
        self.assertTrue(out[VERBOSE_MIGRATION_KEY])
        self.assertTrue(out["servers"]["udpfs"]["verbose"])
        self.assertEqual(out["servers"]["udpfs"]["port"], 62966)
        self.assertTrue(out["servers"]["smbv2"]["verbose"])
        self.assertEqual(out["ip"], "10.0.0.2")
        self.assertFalse(saved["servers"]["udpfs"]["verbose"])  # input untouched

    def test_choice_after_migration_is_respected(self):
        saved = {VERBOSE_MIGRATION_KEY: True,
                 "servers": {"udpfs": {"verbose": False}}}
        self.assertIs(migrate_verbose_default(saved), saved)
        self.assertFalse(saved["servers"]["udpfs"]["verbose"])

    def test_fresh_config_only_gets_the_marker(self):
        self.assertEqual(migrate_verbose_default({}), {VERBOSE_MIGRATION_KEY: True})

    def test_unknown_or_malformed_server_entries_survive(self):
        out = migrate_verbose_default({"servers": {"gone": {"x": 1}, "udpfs": None}})
        self.assertEqual(out["servers"]["gone"], {"x": 1})
        self.assertIsNone(out["servers"]["udpfs"])


class TerminalStamp(unittest.TestCase):
    def test_each_line_has_a_second_precision_time(self):
        try:
            from launcher import gui
        except ImportError as e:  # no tkinter on this runner
            self.skipTest(str(e))
        text = gui.LauncherApp._terminal_text(None, "udpfs", "one\n\ntwo\n") \
            if hasattr(gui, "LauncherApp") else None
        if text is None:
            self.skipTest("LauncherApp not found")
        lines = text.split("\n")
        self.assertRegex(lines[0], r"^\d\d:\d\d:\d\d \[[^\]]+\] one$")
        self.assertEqual(lines[1], "")
        self.assertRegex(lines[2], r"^\d\d:\d\d:\d\d \[[^\]]+\] two$")


if __name__ == "__main__":
    unittest.main()
