"""Tests for ignore_firewall_prompt and autostart_last_config launcher options.

Run:
    python -m unittest tests.test_autostart_and_firewall_options -v
"""

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class AutostartAndFirewallOptionsTest(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.mkdtemp(prefix="ps2-opts-test-")
        self.saved_env = {v: os.environ.get(v) for v in ("APPDATA", "XDG_CONFIG_HOME")}
        for var in ("APPDATA", "XDG_CONFIG_HOME"):
            os.environ[var] = self.scratch

        from launcher import config, tray
        self.saved_config_save = config.save
        self.saved_tray_available = tray.AVAILABLE
        config.save = lambda data: None
        tray.AVAILABLE = False

    def tearDown(self):
        for var, was in self.saved_env.items():
            if was is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = was
        if self.scratch:
            import shutil
            shutil.rmtree(self.scratch, ignore_errors=True)
        from launcher import config, tray
        config.save = self.saved_config_save
        tray.AVAILABLE = self.saved_tray_available

    def test_options_variables_and_persistence(self):
        try:
            import tkinter as tk
        except ImportError:
            raise unittest.SkipTest("no tkinter")

        from launcher import gui
        try:
            root = tk.Tk()
        except tk.TclError:
            raise unittest.SkipTest("no display")

        try:
            app = gui.LauncherApp(root)
            self.assertFalse(app.ignore_firewall_var.get())
            self.assertFalse(app.autostart_var.get())

            app.ignore_firewall_var.set(True)
            app.autostart_var.set(True)

            saved_data = {}
            def dummy_save(data):
                saved_data.update(data)
                return True
            from launcher import config
            config.save = dummy_save

            self.assertTrue(app._save())
            self.assertTrue(saved_data.get("ignore_firewall_prompt"))
            self.assertTrue(saved_data.get("autostart_last_config"))
            self.assertEqual(saved_data.get("last_active_servers"), [])
        finally:
            app._shutting_down = True
            root.destroy()

    def test_autostart_servers_saved_list(self):
        try:
            import tkinter as tk
        except ImportError:
            raise unittest.SkipTest("no tkinter")

        from launcher import gui
        try:
            root = tk.Tk()
        except tk.TclError:
            raise unittest.SkipTest("no display")

        try:
            app = gui.LauncherApp(root)
            started_keys = []
            app.start_server = lambda key: started_keys.append(key)
            app.saved["last_active_servers"] = ["udpfs"]

            app._autostart_servers()
            self.assertEqual(started_keys, ["udpfs"])
        finally:
            app._shutting_down = True
            root.destroy()

    def test_autostart_servers_empty_list_does_not_fallback(self):
        try:
            import tkinter as tk
        except ImportError:
            raise unittest.SkipTest("no tkinter")

        from launcher import gui
        try:
            root = tk.Tk()
        except tk.TclError:
            raise unittest.SkipTest("no display")

        try:
            app = gui.LauncherApp(root)
            started_keys = []
            app.start_server = lambda key: started_keys.append(key)
            app.saved["last_active_servers"] = []

            card = app.cards["udpfs"]
            card.set_values({"root": "/tmp/games", "port": 62966})

            app._autostart_servers()
            self.assertEqual(started_keys, [])
        finally:
            app._shutting_down = True
            root.destroy()

    def test_autostart_servers_fallback(self):
        try:
            import tkinter as tk
        except ImportError:
            raise unittest.SkipTest("no tkinter")

        from launcher import gui
        try:
            root = tk.Tk()
        except tk.TclError:
            raise unittest.SkipTest("no display")

        try:
            app = gui.LauncherApp(root)
            started_keys = []
            app.start_server = lambda key: started_keys.append(key)
            app.saved.pop("last_active_servers", None)

            # Seed a card with required fields populated
            card = app.cards["udpfs"]
            card.set_values({"root": "/tmp/games", "port": 62966})

            app._autostart_servers()
            self.assertIn("udpfs", started_keys)
        finally:
            app._shutting_down = True
            root.destroy()

    def test_cli_flags_precedence(self):
        try:
            import tkinter as tk
        except ImportError:
            raise unittest.SkipTest("no tkinter")

        old_argv = list(sys.argv)
        sys.argv.extend(["--ignore-firewall-prompt", "--autostart"])
        from launcher import gui
        try:
            root = tk.Tk()
        except tk.TclError:
            sys.argv = old_argv
            raise unittest.SkipTest("no display")

        try:
            app = gui.LauncherApp(root)
            self.assertTrue(app.ignore_firewall_var.get())
            self.assertTrue(app.autostart_var.get())
        finally:
            sys.argv = old_argv
            app._shutting_down = True
            root.destroy()


if __name__ == "__main__":
    unittest.main()
