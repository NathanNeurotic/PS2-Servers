"""The protocol-mode selector must reach the server as a value it accepts.

The mode used to be a global "Modulo mode" checkbox, which applied one client's
quirk to every client at once. It was replaced by per-session negotiation, and
the checkbox was removed -- correctly, but that left no way to override a
misjudgement except by hand-editing a settings file.

It is a visible three-way choice again: Auto, Proper, Modulo. What makes that
worth testing is that the label and the wire value deliberately differ. The user
picks "Proper"; the servers only accept "standard". A mapping that silently
returned "Proper" would be rejected by the server at startup, and under procd or
the launcher's respawn that is a mode that simply never comes up.

Run:  python -m unittest tests.test_protocol_mode_selector -v
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from launcher.servers import (  # noqa: E402
    PROTOCOL_MODE_CHOICES, REGISTRY, migrate_saved, protocol_mode_value)

UDPFS = REGISTRY["udpfs"]
# The three words both servers accept; anything else makes them exit.
ACCEPTED_ON_THE_WIRE = {"auto", "standard", "modulo"}


def _argv(**values):
    values.setdefault("root_dir", "/games")
    return UDPFS.build_argv(values)


def _mode_in(argv):
    if "--protocol-mode" not in argv:
        return None
    return argv[argv.index("--protocol-mode") + 1]


class ChoicesAreServable(unittest.TestCase):
    def test_every_choice_maps_to_a_value_the_servers_accept(self):
        for label, value in PROTOCOL_MODE_CHOICES:
            with self.subTest(label=label):
                self.assertIn(value, ACCEPTED_ON_THE_WIRE)

    def test_the_labels_are_the_ones_asked_for(self):
        self.assertEqual([label for label, _ in PROTOCOL_MODE_CHOICES],
                         ["Auto", "Proper", "Modulo"])

    def test_auto_is_first_so_it_is_what_an_empty_selection_lands_on(self):
        self.assertEqual(PROTOCOL_MODE_CHOICES[0], ("Auto", "auto"))

    def test_the_field_is_visible_not_advanced(self):
        """Hidden behind Advanced it is no use to whoever needs it."""
        field = next(f for f in UDPFS.fields if f.key == "protocol_mode")
        self.assertFalse(field.advanced)
        self.assertEqual(field.kind, "choice")
        self.assertEqual(field.default, "Auto")
        self.assertEqual(field.choices, PROTOCOL_MODE_CHOICES)


class ValueMapping(unittest.TestCase):
    def test_labels_map_to_wire_values(self):
        self.assertEqual(protocol_mode_value("Auto"), "auto")
        self.assertEqual(protocol_mode_value("Proper"), "standard")
        self.assertEqual(protocol_mode_value("Modulo"), "modulo")

    def test_wire_values_map_to_themselves(self):
        """A settings file written before the dropdown holds the wire value."""
        for value in ("auto", "standard", "modulo"):
            with self.subTest(value=value):
                self.assertEqual(protocol_mode_value(value), value)

    def test_casing_and_padding_are_tolerated(self):
        for raw in ("proper", "PROPER", "  Proper  ", "sTaNdArD"):
            with self.subTest(raw=raw):
                self.assertEqual(protocol_mode_value(raw), "standard")

    def test_unknown_values_do_not_become_arguments(self):
        """A bad string must not reach the server, which exits on one.

        Returning it unchanged would turn a typo in a settings file into a
        server that never starts, and under the launcher's respawn that reads as
        a mode which simply does not work.
        """
        for raw in ("nonsense", "", "  ", None, "auto-ish"):
            with self.subTest(raw=raw):
                self.assertIsNone(protocol_mode_value(raw))


class ArgvContract(unittest.TestCase):
    def test_proper_is_sent_as_standard(self):
        self.assertEqual(_mode_in(_argv(protocol_mode="Proper")), "standard")

    def test_modulo_is_sent(self):
        self.assertEqual(_mode_in(_argv(protocol_mode="Modulo")), "modulo")

    def test_auto_is_not_sent_at_all(self):
        """Auto is the server default; omitting it keeps the command line honest."""
        self.assertIsNone(_mode_in(_argv(protocol_mode="Auto")))

    def test_no_selection_sends_nothing(self):
        self.assertIsNone(_mode_in(_argv()))

    def test_a_bad_saved_value_sends_nothing_rather_than_itself(self):
        argv = _argv(protocol_mode="Propper")
        self.assertIsNone(_mode_in(argv))
        self.assertNotIn("Propper", argv)

    def test_the_legacy_checkbox_still_wins(self):
        """An upgraded settings file must not silently lose its Modulo setting."""
        self.assertEqual(_mode_in(_argv(modulo_mode=True)), "modulo")
        # Even against an explicit Auto, because the old checkbox was the only
        # way to say Modulo and dropping it would move a working console.
        self.assertEqual(_mode_in(_argv(protocol_mode="Auto", modulo_mode=True)),
                         "modulo")

    def test_every_emitted_value_is_one_the_servers_accept(self):
        for raw in ("Auto", "Proper", "Modulo", "standard", "modulo", "auto",
                    "nonsense", None):
            with self.subTest(raw=raw):
                mode = _mode_in(_argv(protocol_mode=raw))
                if mode is not None:
                    self.assertIn(mode, ACCEPTED_ON_THE_WIRE)


class LegacyKeySurvivesTheRealLoadPath(unittest.TestCase):
    """The argv test above is not enough, and was passing for the wrong reason.

    ServerCard.set_values() restores only keys that have a widget, and values()
    collects only keys that have a widget. modulo_mode has had no widget since
    the checkbox was retired, so the GUI can neither restore it nor emit it: the
    dict that reaches build_argv cannot contain it. Handing build_argv that key
    directly, as the ArgvContract test does, exercises a path the application
    cannot produce -- the test passed while the behaviour it claimed to protect
    did not exist.

    These tests go through the migration that runs on load instead.
    """

    def test_legacy_modulo_becomes_a_visible_selection(self):
        migrated = migrate_saved("udpfs", {"modulo_mode": True, "root_dir": "/games"})
        self.assertEqual(migrated.get("protocol_mode"), "Modulo")
        self.assertNotIn("modulo_mode", migrated)
        self.assertEqual(migrated["root_dir"], "/games", "unrelated keys must survive")

    def test_migrated_value_reaches_the_command_line(self):
        migrated = migrate_saved("udpfs", {"modulo_mode": True, "root_dir": "/games"})
        self.assertEqual(_mode_in(UDPFS.build_argv(migrated)), "modulo")

    def test_an_explicit_new_choice_is_not_overwritten(self):
        """Someone who has since chosen in the new control meant it."""
        migrated = migrate_saved(
            "udpfs", {"modulo_mode": True, "protocol_mode": "Proper"})
        self.assertEqual(migrated["protocol_mode"], "Proper")

    def test_nothing_happens_without_the_legacy_key(self):
        original = {"root_dir": "/games", "protocol_mode": "Auto"}
        self.assertEqual(migrate_saved("udpfs", original), original)

    def test_other_servers_are_untouched(self):
        original = {"modulo_mode": True}
        self.assertEqual(migrate_saved("smbv1", original), original)

    def test_empty_and_missing_saves_are_safe(self):
        for probe in ({}, None):
            with self.subTest(probe=probe):
                self.assertEqual(migrate_saved("udpfs", probe), probe)

    def test_the_card_actually_applies_the_migration(self):
        """End to end through the widgets, which is where it was being lost.

        Builds a real card, loads a settings dict written before the dropdown
        existed, then collects the values back out the way start_server does.
        """
        try:
            import tkinter as tk
        except ImportError:
            self.skipTest("no tkinter")
        try:
            root = tk.Tk()
        except tk.TclError:
            self.skipTest("no display")
        self.addCleanup(root.destroy)
        root.withdraw()

        from launcher import gui

        card = gui.ServerCard.__new__(gui.ServerCard)
        card.server = UDPFS
        card.vars = {}
        card.field_widgets = {}
        frame = gui.ttk.Frame(root)
        row = 0
        for field in UDPFS.fields:
            row = card._add_field(frame, field, row)

        card.set_values({"modulo_mode": True, "root_dir": "/games"})
        collected = card.values()
        self.assertEqual(collected.get("protocol_mode"), "Modulo")
        self.assertEqual(_mode_in(UDPFS.build_argv(collected)), "modulo")


if __name__ == "__main__":
    unittest.main()
