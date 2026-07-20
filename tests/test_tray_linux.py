"""Contract tests for the Linux tray backend (launcher/tray_linux.py).

The real pystray + a live session bus only exist on a Linux desktop, which this
suite can't assume. So these drive the backend against a FAKE pystray injected
into sys.modules, verifying the parts that matter regardless of platform:

  * available() gates correctly on platform / modules / session bus,
  * start() spins up an icon and returns True once it reports set up,
  * the Open/Quit menu items marshal to the on_open/on_quit callbacks,
  * stop() tears the icon down.

The actual icon rendering on a given desktop is what the Linux tester validates;
this locks the wiring so a regression there fails in CI, not on his machine.
"""

import sys
import types
import unittest
from unittest import mock

from launcher import tray_linux


def _fake_pystray():
    mod = types.ModuleType("pystray")

    class MenuItem:
        def __init__(self, text, action, default=False):
            self.text = text
            self.action = action
            self.default = default

    class Menu:
        def __init__(self, *items):
            self.items = list(items)

    class Icon:
        instances = []

        def __init__(self, name, image, title, menu):
            self.name = name
            self.image = image
            self.title = title
            self.menu = menu
            self.visible = False
            self.stopped = False
            Icon.instances.append(self)

        def run(self, setup=None):
            # Real run() blocks on the backend loop; the fake just reports the
            # icon came up (via setup) and returns, ending the daemon thread.
            if setup:
                setup(self)

        def stop(self):
            self.stopped = True

    mod.MenuItem = MenuItem
    mod.Menu = Menu
    mod.Icon = Icon
    return mod, Icon


class AvailableTests(unittest.TestCase):
    def test_available_true_when_all_pieces_present(self):
        with mock.patch.object(tray_linux.platform, "system", return_value="Linux"), \
                mock.patch.object(tray_linux, "_has_session_bus", return_value=True), \
                mock.patch.object(tray_linux, "_has_module", return_value=True):
            self.assertTrue(tray_linux.available())

    def test_unavailable_off_linux(self):
        with mock.patch.object(tray_linux.platform, "system", return_value="Darwin"), \
                mock.patch.object(tray_linux, "_has_session_bus", return_value=True), \
                mock.patch.object(tray_linux, "_has_module", return_value=True):
            self.assertFalse(tray_linux.available())

    def test_unavailable_without_session_bus(self):
        with mock.patch.object(tray_linux.platform, "system", return_value="Linux"), \
                mock.patch.object(tray_linux, "_has_session_bus", return_value=False), \
                mock.patch.object(tray_linux, "_has_module", return_value=True):
            self.assertFalse(tray_linux.available())

    def test_unavailable_without_pystray(self):
        def has(name):
            return name != "pystray"
        with mock.patch.object(tray_linux.platform, "system", return_value="Linux"), \
                mock.patch.object(tray_linux, "_has_session_bus", return_value=True), \
                mock.patch.object(tray_linux, "_has_module", side_effect=has):
            self.assertFalse(tray_linux.available())


class SystemTrayContractTests(unittest.TestCase):
    def setUp(self):
        self.fake, self.Icon = _fake_pystray()
        self.Icon.instances = []
        sys.modules["pystray"] = self.fake
        # Keep the test independent of the theme assets / Pillow.
        self._icon_patch = mock.patch.object(
            tray_linux, "_icon_image", return_value=object())
        self._icon_patch.start()

    def tearDown(self):
        self._icon_patch.stop()
        sys.modules.pop("pystray", None)

    def test_start_returns_true_and_marks_icon_visible(self):
        tray = tray_linux.SystemTray("PS2 Servers", lambda: None, lambda: None)
        self.assertTrue(tray.start())
        icon = self.Icon.instances[-1]
        self.assertTrue(icon.visible)

    def test_menu_items_marshal_to_callbacks(self):
        opened, quit_ = [], []
        tray = tray_linux.SystemTray(
            "PS2 Servers", lambda: opened.append(1), lambda: quit_.append(1))
        self.assertTrue(tray.start())
        icon = self.Icon.instances[-1]
        items = {i.text: i for i in icon.menu.items}
        self.assertIn("Open PS2 Servers", items)
        self.assertIn("Quit", items)
        # Left-click should invoke Open, so it must be the default item.
        self.assertTrue(items["Open PS2 Servers"].default)
        items["Open PS2 Servers"].action(icon, items["Open PS2 Servers"])
        items["Quit"].action(icon, items["Quit"])
        self.assertEqual(opened, [1])
        self.assertEqual(quit_, [1])

    def test_start_returns_false_when_no_icon_image(self):
        self._icon_patch.stop()
        with mock.patch.object(tray_linux, "_icon_image", return_value=None):
            tray = tray_linux.SystemTray("PS2 Servers", lambda: None, lambda: None)
            self.assertFalse(tray.start())
        self._icon_patch.start()  # keep tearDown balanced

    def test_stop_is_safe_before_and_after_start(self):
        tray = tray_linux.SystemTray("PS2 Servers", lambda: None, lambda: None)
        tray.stop()  # never started -- must not raise
        tray.start()
        icon = self.Icon.instances[-1]
        tray.stop()
        self.assertTrue(icon.stopped)


if __name__ == "__main__":
    unittest.main()
