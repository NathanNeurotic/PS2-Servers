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
            app._invite_ps2_setup_guide = False  # No first-run modal in unrelated GUI tests.
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
            app._invite_ps2_setup_guide = False  # No first-run modal in unrelated GUI tests.
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
            app._invite_ps2_setup_guide = False  # No first-run modal in unrelated GUI tests.
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
            app._invite_ps2_setup_guide = False  # No first-run modal in unrelated GUI tests.
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

    def _app_with_fake_servers(self, tk, gui):
        """An app whose udpfs and smbv1 'run' as fakes, and the list of saves."""
        from launcher import config

        class FakeProc:
            cooperative_stop = False
            stop_grace = 0
            returncode = 0

            def __init__(self):
                self.alive = True

            def is_running(self):
                return self.alive

            def request_stop(self):
                pass

            def stop(self):
                self.alive = False

        writes = []
        config.save = lambda data: writes.append(dict(data)) or True
        root = tk.Tk()
        app = gui.LauncherApp(root)
        app._invite_ps2_setup_guide = False
        app.procs = {"udpfs": FakeProc(), "smbv1": FakeProc()}
        return root, app, writes

    def test_quit_keeps_running_servers_for_autostart(self):
        try:
            import tkinter as tk
        except ImportError:
            raise unittest.SkipTest("no tkinter")
        from launcher import gui
        try:
            tk.Tk().destroy()
        except tk.TclError:
            raise unittest.SkipTest("no display")

        root, app, writes = self._app_with_fake_servers(tk, gui)
        try:
            app._shutdown_app()
        finally:
            app._shutting_down = True
        # stop_server saves after each server it stops; the last save on disk
        # is what the next launch reads, and it must still name both servers.
        self.assertEqual(sorted(writes[-1]["last_active_servers"]),
                         ["smbv1", "udpfs"])

    def test_footer_stop_all_still_clears_the_remembered_list(self):
        try:
            import tkinter as tk
        except ImportError:
            raise unittest.SkipTest("no tkinter")
        from launcher import gui
        try:
            tk.Tk().destroy()
        except tk.TclError:
            raise unittest.SkipTest("no display")

        root, app, writes = self._app_with_fake_servers(tk, gui)
        try:
            app.stop_all(remember_active=False)
            self.assertEqual(writes[-1]["last_active_servers"], [])
            self.assertIsNone(app._active_override)
            app._save()
            self.assertEqual(writes[-1]["last_active_servers"], [])
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
            app._invite_ps2_setup_guide = False  # No first-run modal in unrelated GUI tests.
            self.assertTrue(app.ignore_firewall_var.get())
            self.assertTrue(app.autostart_var.get())
        finally:
            sys.argv = old_argv
            app._shutting_down = True
            root.destroy()


if __name__ == "__main__":
    unittest.main()
