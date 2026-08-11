"""Tests for the About tab "Reset all settings" action and updates button.

Run:
    python -m unittest tests.test_reset_settings -v
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class ConfigResetTest(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.mkdtemp(prefix="ps2-reset-test-")
        self.saved_env = {v: os.environ.get(v) for v in ("APPDATA", "XDG_CONFIG_HOME")}
        for var in ("APPDATA", "XDG_CONFIG_HOME"):
            os.environ[var] = self.scratch

    def tearDown(self):
        for var, was in self.saved_env.items():
            if was is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = was
        import shutil
        shutil.rmtree(self.scratch, ignore_errors=True)

    def test_reset_removes_config_file(self):
        from launcher import config
        config.save({"servers": {"udpfs": {"root": "/games"}}})
        self.assertTrue(os.path.exists(config.config_path()))
        config.reset()
        self.assertFalse(os.path.exists(config.config_path()))
        # A fresh load after reset is the defaults: an empty dict.
        self.assertEqual(config.load(), {})

    def test_reset_tolerates_missing_file(self):
        from launcher import config
        self.assertFalse(os.path.exists(config.config_path()))
        config.reset()  # must not raise
        self.assertFalse(os.path.exists(config.config_path()))


class ResetSettingsGUITest(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.mkdtemp(prefix="ps2-reset-gui-test-")
        self.saved_env = {v: os.environ.get(v) for v in ("APPDATA", "XDG_CONFIG_HOME")}
        for var in ("APPDATA", "XDG_CONFIG_HOME"):
            os.environ[var] = self.scratch

        from launcher import tray
        self.saved_tray_available = tray.AVAILABLE
        tray.AVAILABLE = False

    def tearDown(self):
        for var, was in self.saved_env.items():
            if was is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = was
        import shutil
        shutil.rmtree(self.scratch, ignore_errors=True)
        from launcher import tray
        tray.AVAILABLE = self.saved_tray_available

    def _make_app(self):
        try:
            import tkinter as tk
        except ImportError:
            raise unittest.SkipTest("no tkinter")
        from launcher import gui
        try:
            root = tk.Tk()
        except tk.TclError:
            raise unittest.SkipTest("no display")
        app = gui.LauncherApp(root)

        def cleanup(app=app, root=root):
            app._shutting_down = True
            root.destroy()

        self.addCleanup(cleanup)
        return gui, app

    def test_reset_deletes_config_blocks_save_and_restarts(self):
        gui, app = self._make_app()
        from launcher import config
        config.save({"servers": {"udpfs": {"root": "/poisoned"}}})
        self.assertTrue(os.path.exists(config.config_path()))

        popen_calls = []
        with (mock.patch.object(gui.messagebox, "askyesno", return_value=True),
              mock.patch.object(gui.messagebox, "showwarning"),
              mock.patch.object(gui.subprocess, "Popen",
                                side_effect=lambda *a, **k: popen_calls.append((a, k))
                                or mock.Mock()),
              mock.patch.object(app, "_shutdown_app")):
            app.reset_settings()

        self.assertFalse(os.path.exists(config.config_path()))
        self.assertTrue(app._config_reset)
        self.assertEqual(app.saved, {})
        self.assertEqual(len(popen_calls), 1)
        # After reset, _save is a no-op: nothing may resurrect the old config.
        self.assertTrue(app._save())
        self.assertFalse(os.path.exists(config.config_path()))

    def test_reset_cancelled_keeps_config(self):
        gui, app = self._make_app()
        from launcher import config
        config.save({"servers": {"udpfs": {"root": "/keep"}}})

        with (mock.patch.object(gui.messagebox, "askyesno", return_value=False),
              mock.patch.object(gui.subprocess, "Popen") as popen_mock):
            app.reset_settings()

        self.assertTrue(os.path.exists(config.config_path()))
        self.assertFalse(app._config_reset)
        popen_mock.assert_not_called()

    def test_reset_refused_while_direct_link_enabled(self):
        gui, app = self._make_app()
        from launcher import config
        config.save({"direct_link": {"enabled": True}})
        app.saved["direct_link"] = {"enabled": True}

        with (mock.patch.object(gui.messagebox, "askyesno") as ask_mock,
              mock.patch.object(gui.messagebox, "showwarning") as warn_mock,
              mock.patch.object(gui.subprocess, "Popen") as popen_mock):
            app.reset_settings()

        warn_mock.assert_called_once()
        ask_mock.assert_not_called()  # refused before the confirm prompt
        popen_mock.assert_not_called()
        self.assertTrue(os.path.exists(config.config_path()))
        self.assertFalse(app._config_reset)

    def test_reset_refused_with_retained_static_address(self):
        # "Turn off Direct Link but keep the fixed address" saves enabled=False
        # with the adapter metadata still attached: the port is still static,
        # and this config is the only thing that knows how to undo that.
        gui, app = self._make_app()
        from launcher import config
        stale = {"enabled": False, "adapter": "Ethernet", "server_ip": "10.0.0.1"}
        config.save({"direct_link": dict(stale)})
        app.saved["direct_link"] = dict(stale)

        with (mock.patch.object(gui.messagebox, "askyesno") as ask_mock,
              mock.patch.object(gui.messagebox, "showwarning") as warn_mock,
              mock.patch.object(gui.subprocess, "Popen") as popen_mock):
            app.reset_settings()

        warn_mock.assert_called_once()
        ask_mock.assert_not_called()  # refused before the confirm prompt
        popen_mock.assert_not_called()
        self.assertTrue(os.path.exists(config.config_path()))
        self.assertFalse(app._config_reset)

    def test_reset_delete_failure_keeps_everything(self):
        # A config that cannot be deleted (permissions, another process holding
        # it) must surface as an error, not escape the Tk callback -- and the
        # restart must not happen, or the app would come back on defaults while
        # the old config still sits on disk.
        gui, app = self._make_app()
        from launcher import config
        config.save({"servers": {"udpfs": {"root": "/keep"}}})

        with (mock.patch.object(gui.messagebox, "askyesno", return_value=True),
              mock.patch.object(gui.messagebox, "showerror") as err_mock,
              mock.patch.object(gui.config, "reset",
                                side_effect=PermissionError("locked")),
              mock.patch.object(gui.subprocess, "Popen") as popen_mock):
            app.reset_settings()

        err_mock.assert_called_once()
        popen_mock.assert_not_called()
        self.assertTrue(os.path.exists(config.config_path()))
        self.assertFalse(app._config_reset)

    def test_reset_restart_failure_restores_settings(self):
        gui, app = self._make_app()
        from launcher import config
        config.save({"servers": {"udpfs": {"root": "/keep"}}})

        with (mock.patch.object(gui.messagebox, "askyesno", return_value=True),
              mock.patch.object(gui.messagebox, "showerror"),
              mock.patch.object(gui.subprocess, "Popen",
                                side_effect=OSError("boom"))):
            app.reset_settings()

        # Rollback: the reset flag is cleared and the current state is saved again.
        self.assertFalse(app._config_reset)
        self.assertTrue(os.path.exists(config.config_path()))

    def test_about_tab_has_check_for_updates_button(self):
        gui, app = self._make_app()
        texts = []

        def collect(widget):
            if isinstance(widget, gui.ttk.Button):
                texts.append(widget.cget("text"))
            for child in widget.winfo_children():
                collect(child)

        # Scan every notebook page rather than naming the About tab: the
        # runtime shim (main._apply_gui_review_fixes) pads tab text, and whether
        # it ran yet depends on which tests ran before this one.
        for tab_id in app.nb.tabs():
            collect(app.nb.nametowidget(tab_id))
        self.assertIn("Check for updates", texts)
        self.assertNotIn("Releases", texts)
        self.assertIn("Reset all settings…", texts)


if __name__ == "__main__":
    unittest.main()
