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
    PROTOCOL_MODE_CHOICES, REGISTRY, protocol_mode_value)

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


if __name__ == "__main__":
    unittest.main()
