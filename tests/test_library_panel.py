"""Exercise the real Tk first-run path that previously terminated frozen builds."""
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock

from launcher import library_gui, library_panel


class LibraryStartupTests(unittest.TestCase):
    def test_index_review_captures_root_and_uses_existing_background_worker(self):
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
                panel._review_index()
                action = run.call_args.args[0]
                panel.path.set("changed after scheduling")
                db = mock.Mock()
                action(db)
                self.assertEqual(db.indexed_games.call_args.args[0], Path(profile).resolve())
                self.assertIs(db.indexed_games.call_args.kwargs["cancel"], panel._scan_cancel)
                panel.cancel_scan.invoke()
                self.assertTrue(panel._scan_cancel.is_set())
        finally:
            root.destroy()

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


    def test_artwork_identity_is_refreshed_before_it_is_suggested(self):
        tk = library_panel.tk
        try:
            root = tk.Tk()
        except tk.TclError:
            self.skipTest("No graphical display")
        try:
            with tempfile.TemporaryDirectory() as profile, \
                 mock.patch.object(library_panel, "config_dir", return_value=profile), \
                 mock.patch.object(library_panel.LibraryPanel, "_run"), \
                 mock.patch.object(library_panel.LibraryPanel, "_drain"), \
                 mock.patch.object(library_panel.filedialog, "askopenfilename", return_value="art.png"), \
                 mock.patch.object(library_panel.simpledialog, "askstring", return_value=None) as ask, \
                 mock.patch.object(library_panel.library_catalog, "Catalog") as catalog:
                panel = library_panel.LibraryPanel(root, SimpleNamespace(_open_ps2_setup_guide=lambda: None))
                panel.path.set(profile)
                panel.tree.insert("", "end", iid="7", values=("Game",))
                panel.tree.selection_set("7")
                panel._display_rows = {"7": {"disc_id": "", "detected_disc_id": "SLUS_123.45",
                                             "identity_status": "verified"}}
                catalog.return_value.__enter__.return_value.get_game.return_value = {
                    "disc_id": "", "detected_disc_id": "", "identity_status": "stale"}
                panel._import_art()
                catalog.return_value.__enter__.return_value.get_game.assert_called_once_with(7)
                self.assertEqual(ask.call_args.kwargs["initialvalue"], "")
        finally:
            root.destroy()

    def test_artwork_download_needs_a_confirmed_preview(self):
        tk = library_panel.tk
        try:
            root = tk.Tk()
        except tk.TclError:
            self.skipTest("No graphical display")
        try:
            with tempfile.TemporaryDirectory() as profile, \
                 mock.patch.object(library_panel, "config_dir", return_value=profile), \
                 mock.patch.object(library_panel.LibraryPanel, "_run") as run, \
                 mock.patch.object(library_panel.LibraryPanel, "_drain"), \
                 mock.patch.object(library_panel.simpledialog, "askstring",
                                   side_effect=["https://covers.example/{serial}.jpg", "cov"] * 2), \
                 mock.patch.object(library_panel.messagebox, "askokcancel",
                                   side_effect=[False, True]) as confirm, \
                 mock.patch.object(library_panel.library_catalog, "Catalog") as catalog:
                catalog.return_value.__enter__.return_value.list_games.return_value = [
                    {"identity_status": "verified", "detected_disc_id": "SLUS_201.74", "disc_id": ""}]
                panel = library_panel.LibraryPanel(root, SimpleNamespace(_open_ps2_setup_guide=lambda: None))
                run.reset_mock()
                panel.path.set(profile)
                panel._download_art()
                self.assertIn("https://covers.example/SLUS-20174.jpg", confirm.call_args.args[1])
                run.assert_not_called()  # declined: nothing is fetched
                self.assertEqual(panel.art_template_file.read_text(encoding="utf-8"),
                                 "https://covers.example/{serial}.jpg")
                panel._download_art()
                run.assert_called_once()
                fake = mock.Mock()
                with mock.patch.object(library_panel.art_fetch, "run", return_value=[]) as fetch_run:
                    report = run.call_args.args[0](fake)
                self.assertEqual(fetch_run.call_args.args[3], "COV")
                self.assertIs(fetch_run.call_args.kwargs["cancel"], panel._scan_cancel)
                self.assertIsInstance(report, library_panel.ArtReport)
        finally:
            root.destroy()


class AchievementLibraryIndexTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = library_gui.tk.Tk()
        except library_gui.tk.TclError:
            self.skipTest("No graphical display")
        self.addCleanup(self.root.destroy)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.profile = Path(self.tmp.name)
        self.settings = {"servers": {"udpfs": {"port": 3000}}}
        self.saved = []
        for patcher in (
                mock.patch("launcher.game_library.config_dir", return_value=str(self.profile)),
                mock.patch.object(library_gui.config, "load", side_effect=lambda: dict(self.settings)),
                mock.patch.object(library_gui.config, "save", side_effect=self.saved.append)):
            patcher.start()
            self.addCleanup(patcher.stop)
        with mock.patch.object(library_gui.LibraryWindow, "refresh"):
            self.window = library_gui.LibraryWindow(self.root, folder=str(self.profile))
        self.addCleanup(self.cleanup_window)

    def cleanup_window(self):
        if self.window.winfo_exists():
            self.window._closed = True
            self.window.cancel.set()
            self.window.after_cancel(self.window._poll_after)
            self.window.destroy()

    def pump_until(self, condition):
        deadline = time.monotonic() + 4
        while not condition() and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.005)
        self.assertTrue(condition(), "Background refresh did not complete")

    def test_index_is_opt_in_and_close_saves_it_without_replacing_server_settings(self):
        self.assertFalse(self.window.use_index.get())
        self.window.use_index.set(True)
        self.window.close()
        self.assertTrue(self.saved[-1]["game_library_use_index"])
        self.assertEqual(self.saved[-1]["servers"], self.settings["servers"])
        self.assertFalse(self.window.winfo_exists())

    def test_refresh_worker_discards_stale_scope_and_never_reads_tk_state_in_worker(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        calls = []
        gui_thread = threading.get_ident()

        def load(view, query, folder, indexed):
            calls.append((threading.get_ident(), query, folder, indexed))
            if len(calls) == 1:
                entered.set()
                release.wait(2)
            return [("image-0", {"path": query}, (query, "details", "Not checked"))], "Loaded"

        with mock.patch.object(self.window, "load_rows", side_effect=load):
            self.window.query.set("old scope")
            self.window.refresh()
            self.pump_until(entered.is_set)
            self.window.query.set("new scope")
            self.window.use_index.set(True)
            self.window.refresh()
            self.assertTrue(self.window.cancel.is_set())
            release.set()
            self.pump_until(lambda: not self.window.busy)
        self.assertEqual([call[1] for call in calls], ["old scope", "new scope"])
        self.assertTrue(all(call[0] != gui_thread for call in calls))
        self.assertTrue(calls[-1][3])
        self.assertEqual(self.window.tree.item("image-0", "values")[0], "new scope")

    def test_shared_refresh_displays_title_path_and_hash_cache_without_ra_requests(self):
        from launcher.library_catalog import Catalog
        from tests.test_disc_identity import iso_fixture
        games = self.profile / "games"
        path = games / "RPG" / "Game.iso"
        path.parent.mkdir(parents=True)
        path.write_bytes(iso_fixture())
        with Catalog(self.window.library.directory / "catalog.sqlite3") as general:
            row_id = general.upsert(path, "UNKNOWN")
            general.edit(row_id, "Custom title")
            general.inspect_identities(games)
        self.window.folder.set(str(games))
        self.window.use_index.set(True)
        with mock.patch.object(library_gui, "Compatibility", side_effect=AssertionError("No RA account request")):
            self.window.refresh()
            self.pump_until(lambda: not self.window.busy)
        row, = self.window.rows.values()
        self.assertNotIn("id", row)
        self.assertEqual(row["boot_serial"], "SLUS_123.45")
        values = self.window.tree.item("image-0", "values")
        self.assertEqual(values[0], "Custom title")
        self.assertIn("RPG/Game.iso", values[1])
        self.assertEqual(values[2], "Not checked")
        with self.window.library.connect() as db:
            db.execute("INSERT INTO image_status(path,stamp,status,count) VALUES(?,?,?,?)",
                       (row["path"], row["stamp"], "compatible", 12))
        self.window.view.set("Recognized achievement sets")
        self.window.refresh()
        self.pump_until(lambda: not self.window.busy)
        self.assertEqual(self.window.tree.item("image-0", "values")[2], "12 achievements")

    def test_failed_refresh_clears_old_selection_and_reports_missing_index(self):
        self.window.tree.insert("", "end", iid="old", values=("Old root",))
        self.window.rows = {"old": {"path": "/old/Game.iso"}}
        self.window.use_index.set(True)
        with mock.patch.object(library_gui.messagebox, "showerror") as error:
            self.window.refresh()
            self.pump_until(lambda: not self.window.busy)
            self.assertEqual(self.window.rows, {})
            self.assertEqual(self.window.tree.get_children(), ())
            self.assertIn("Desktop LIBRARY", error.call_args.args[1])

    def test_unselected_compatibility_scan_captures_scope_then_discovers_in_worker(self):
        with mock.patch.object(self.window, "run") as run, \
             mock.patch.object(self.window.library, "shared_index") as read, \
             mock.patch.object(self.window.library, "installed", return_value=[]) as installed:
            self.window.use_index.set(True)
            self.window.scan()
            read.assert_not_called()
            installed.assert_not_called()
            operation = run.call_args.args[0]
            self.window.folder.set("changed")
            self.window.use_index.set(False)
            self.assertEqual(operation(), "No installed images to check.")
            self.assertEqual(read.call_args.args[0], str(self.profile))
            self.assertIs(installed.call_args.kwargs["index"], read.return_value)
