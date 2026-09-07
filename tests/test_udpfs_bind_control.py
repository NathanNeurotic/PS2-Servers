"""The visible UDPFS bind control must reach the existing --bind launch path."""

import unittest
from types import SimpleNamespace
from unittest import mock

from launcher.servers import UDPFS


class UdpfsBindControlTests(unittest.TestCase):
    def setUp(self):
        try:
            import tkinter as tk
        except ImportError:
            self.skipTest("no tkinter")
        try:
            self.root = tk.Tk()
        except tk.TclError:
            self.skipTest("no display")
        self.addCleanup(self.root.destroy)
        self.root.withdraw()

        from launcher.gui import ServerCard

        self.ip = tk.StringVar(value="192.168.0.2")
        self.saved = []
        self.app = SimpleNamespace(
            current_ip=self.ip.get,
            _save=mock.Mock(side_effect=lambda: self.saved.append(self.card.values())),
        )
        self.card = ServerCard(self.root, self.app, UDPFS)
        self.card.set_values({"root_dir": "/games", "bind": "192.168.0.3:41233"})

    def test_visible_button_copies_current_selection_into_saved_launch_args(self):
        # Direct children are on the main card, not in the hidden Advanced frame.
        buttons = [w for w in self.card.winfo_children()
                   if w.winfo_class() == "TButton" and w.cget("text") == "Use LAN IP"]
        self.assertEqual(len(buttons), 1)
        self.assertFalse(next(f for f in UDPFS.fields if f.key == "bind").advanced)

        # Selecting another LAN IP does not silently replace a saved explicit bind.
        self.ip.set(" 192.168.0.4 ")
        self.assertEqual(self.card.values()["bind"], "192.168.0.3:41233")
        buttons[0].invoke()
        self.app._save.assert_called_once_with()
        self.assertEqual(self.saved[0]["bind"], "192.168.0.4")
        argv = UDPFS.build_argv(self.saved[0])
        self.assertEqual(argv[argv.index("--bind") + 1], "192.168.0.4")

        self.card.vars["bind"].set("")
        self.card.set_values(self.saved[0])
        self.assertEqual(self.card.values()["bind"], "192.168.0.4")

    def test_manual_address_port_and_blank_keep_existing_behavior(self):
        argv = UDPFS.build_argv(self.card.values())
        self.assertEqual(argv[argv.index("--bind") + 1], "192.168.0.3:41233")
        self.card.vars["bind"].set("")
        self.assertNotIn("--bind", UDPFS.build_argv(self.card.values()))
        self.app._save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
