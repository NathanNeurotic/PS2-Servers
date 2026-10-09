"""Exercise the real Tk first-run path that previously terminated frozen builds."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from launcher import library_panel


class LibraryStartupTests(unittest.TestCase):
    def test_panel_preserves_tk_root_method(self):
        self.assertIs(library_panel.LibraryPanel._root, library_panel.ttk.Frame._root)

    def test_first_run_guide_and_manual_reopen_use_real_tk_root(self):
        tk = library_panel.tk
        try:
            root = tk.Tk()
        except tk.TclError:
            self.skipTest("No graphical display")
        errors = []
        root.report_callback_exception = lambda *error: errors.append(error)
        try:
            with tempfile.TemporaryDirectory() as profile, \
                 mock.patch.object(library_panel, "config_dir", return_value=profile), \
                 mock.patch.object(library_panel.LibraryPanel, "_run"), \
                 mock.patch.object(library_panel.LibraryPanel, "_drain"):
                panel = library_panel.LibraryPanel(root, SimpleNamespace(ip_var=tk.StringVar(root, "127.0.0.1")))
                panel.pack()
                root.update()
                self.assertEqual(errors, [])
                self.assertIs(panel._root(), root)
                guides = [child for child in panel.winfo_children() if isinstance(child, tk.Toplevel)]
                self.assertEqual(len(guides), 1)
                self.assertEqual(str(guides[0].transient()), str(root))
                guides[0].destroy()
                panel._guide()
                root.update()
                self.assertEqual(errors, [])
                panel.path.set(profile)
                self.assertEqual(panel._games_root(), Path(profile).resolve())
        finally:
            root.destroy()
