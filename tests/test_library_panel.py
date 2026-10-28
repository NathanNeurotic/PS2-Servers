"""Exercise the real Tk first-run path that previously terminated frozen builds."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from launcher import library_panel


class LibraryStartupTests(unittest.TestCase):
    def test_identity_action_captures_root_and_supports_cancel_and_details(self):
        tk = library_panel.tk
        try:
            root = tk.Tk()
        except tk.TclError:
            self.skipTest("No graphical display")
        try:
            with tempfile.TemporaryDirectory() as profile, \
                 mock.patch.object(library_panel, "config_dir", return_value=profile), \
                 mock.patch.object(library_panel.LibraryPanel, "_run") as run, \
                 mock.patch.object(library_panel.LibraryPanel, "_drain"):
                panel = library_panel.LibraryPanel(root, SimpleNamespace(_open_ps2_setup_guide=lambda: None))
                panel.path.set(profile)
                panel._scan_cancel.set()
                panel._inspect_ids()
                self.assertFalse(panel._scan_cancel.is_set())
                action = run.call_args.args[0]
                panel.path.set("does not exist")
                db = mock.Mock()
                action(db)
                self.assertEqual(db.inspect_identities.call_args.args[0], Path(profile).resolve())
                self.assertIs(db.inspect_identities.call_args.kwargs["cancel"], panel._scan_cancel)
                panel.cancel_scan.invoke()
                self.assertTrue(panel._scan_cancel.is_set())
                row = {"id": 1, "image_path": "/games/Game.iso", "disc_id": "SLES_111.11",
                       "detected_disc_id": "SLUS_123.45", "identity_status": "conflict",
                       "boot_path": "SLUS_123.45", "identity_detail": "Manual ID differs."}
                panel._display_rows = {"1": row}
                panel.tree.insert("", "end", iid="1", values=("Game",))
                panel.tree.selection_set("1")
                panel.pack()
                root.update()
                panel.tree.focus_force()
                root.update()
                with mock.patch.object(library_panel.messagebox, "showinfo") as show:
                    panel.tree.event_generate("<Return>")
                    root.update()
                    show.assert_called_once()
                    self.assertIn("Manual ID differs", show.call_args.args[1])
                    self.assertIn("SLUS_123.45", show.call_args.args[1])
                panel._busy = True
                run.reset_mock()
                panel._inspect_ids()
                run.assert_not_called()
        finally:
            root.destroy()

    def test_recursive_scan_captures_options_and_cancel_keeps_ui_responsive(self):
        tk = library_panel.tk
        try:
            root = tk.Tk()
        except tk.TclError:
            self.skipTest("No graphical display")
        try:
            with tempfile.TemporaryDirectory() as profile, \
                 mock.patch.object(library_panel, "config_dir", return_value=profile), \
                 mock.patch.object(library_panel.LibraryPanel, "_run") as run, \
                 mock.patch.object(library_panel.LibraryPanel, "_drain"):
                panel = library_panel.LibraryPanel(root, SimpleNamespace(_open_ps2_setup_guide=lambda: None))
                self.assertFalse(panel.recursive.get())
                panel.path.set(profile)
                panel.recursive.set(True)
                panel.scan_depth.set(3)
                panel._scan()
                action = run.call_args.args[0]
                # Tk state can change after scheduling; worker uses captured values.
                panel.recursive.set(False)
                panel.scan_depth.set(0)
                db = mock.Mock()
                action(db)
                options = db.scan_detailed.call_args.kwargs
                self.assertTrue(options["recursive"])
                self.assertEqual(options["max_depth"], 3)
                self.assertIs(options["cancel"], panel._scan_cancel)
                self.assertFalse(panel._scan_cancel.is_set())
                panel.cancel_scan.invoke()
                self.assertTrue(panel._scan_cancel.is_set())
                for i in range(100):
                    options["progress"](library_panel.library_catalog.ScanResult(visited=i))
                self.assertEqual(panel._scan_progress.qsize(), 1)
                self.assertEqual(panel._scan_progress.get_nowait().visited, 99)
                panel._busy = True
                run.reset_mock()
                panel._scan()
                run.assert_not_called()
        finally:
            root.destroy()

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
