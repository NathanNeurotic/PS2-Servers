"""Settings edits must say they took: transient feedback at the control used.

Covers the per-card Apply/Revert flash, the LAN IP "Saved ✓" flash, and the
About Options frame-title flash (including its restore).

Run:
    python -m unittest tests.test_settings_feedback -v
"""

import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class SettingsFeedbackTest(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.mkdtemp(prefix="ps2-flash-test-")
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
            app._cancel_flashes()
            try:
                # Let every periodic (status poller, scroll watcher, log drain)
                # fire once on the live interpreter and stand down: none of
                # them re-arms once _shutting_down is set, so nothing is left
                # armed to fire mid-destroy, where its command is already gone.
                deadline = time.time() + 0.8
                while time.time() < deadline:
                    root.update()
                    time.sleep(0.1)
            except tk.TclError:
                pass
            root.destroy()

        self.addCleanup(cleanup)
        return gui, app

    def test_apply_flashes_saved_on_the_card(self):
        gui, app = self._make_app()
        card = app.cards["udpfs"]
        self.assertEqual(card._saved_flash.cget("text"), "")
        card.apply_page_settings()
        self.assertEqual(card._saved_flash.cget("text"), "Saved ✓")

    def test_revert_flashes_defaults_restored_on_the_card(self):
        gui, app = self._make_app()
        card = app.cards["udpfs"]
        card.revert_to_defaults()
        self.assertEqual(card._saved_flash.cget("text"), "Defaults restored ✓")

    def test_ip_commit_flashes_saved(self):
        gui, app = self._make_app()
        app._commit_ip_edit()
        self.assertEqual(app._ip_feedback.cget("text"), "Saved ✓")

    def test_options_save_flashes_the_frame_title(self):
        gui, app = self._make_app()
        self.assertEqual(app._options_frame.cget("text"), " Options ")
        app._save_with_feedback()
        self.assertEqual(app._options_frame.cget("text"), " Options — Saved ✓ ")

    def test_flash_restores_the_original_text(self):
        gui, app = self._make_app()
        frame = app._options_frame
        app._flash_label(frame, " Options — Saved ✓ ", ms=50,
                         restore=" Options ")
        deadline = time.time() + 2.5
        while time.time() < deadline:
            app.root.update_idletasks()
            app.root.update()
            if frame.cget("text") == " Options ":
                break
            time.sleep(0.05)
        self.assertEqual(frame.cget("text"), " Options ")

    def test_a_second_flash_restarts_the_timer(self):
        gui, app = self._make_app()
        label = app._ip_feedback
        app._flash_label(label, "Saved ✓", ms=2500)
        first_job = label._flash_job
        app._flash_label(label, "Saved ✓", ms=2500)
        self.assertNotEqual(label._flash_job, first_job)
        self.assertEqual(label.cget("text"), "Saved ✓")


if __name__ == "__main__":
    unittest.main()
