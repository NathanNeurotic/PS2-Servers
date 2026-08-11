"""The launcher window must reflow, and the TERMINAL must never scroll sideways.

Three things this locks down, all of them things a user reported as "text is cut
off" rather than as a layout bug:

  * The terminal wraps. It used to be wrap="none" with no horizontal scrollbar,
    so the end of a long line -- the path, the port, the reason a server exited --
    was simply unreachable.
  * Nothing carries a hard-coded wraplength. The window resizes in both
    directions now, so any fixed number is wrong at every other size.
  * The page scrollbar appears only when the page really does not fit, and the
    window cannot be dragged narrower than its own tab strip.

Needs a real, mapped window: widths do not exist until Tk has laid one out. The
test skips where there is no display, and where the window manager refuses the
geometry it asks for (a tiling WM), because then nothing it measures is about
this code.

Run:  python -m unittest tests.test_gui_layout -v
"""

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class LauncherLayout(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import tkinter as tk
        except ImportError:
            raise unittest.SkipTest("no tkinter")

        # Point the settings file at a scratch dir and stub the write, so a test
        # run cannot touch (or create) the user's real launcher.json. Both the
        # variables and the directory are put back in tearDownClass: this runs in
        # the same process as every other test, and a config path left pointing
        # at a temp dir is the kind of thing that makes an unrelated test pass
        # for the wrong reason.
        cls._scratch = tempfile.mkdtemp(prefix="ps2-layout-")
        cls._saved_env = {v: os.environ.get(v)
                          for v in ("APPDATA", "XDG_CONFIG_HOME")}
        for var in ("APPDATA", "XDG_CONFIG_HOME"):
            os.environ[var] = cls._scratch

        from launcher import config, main as launcher_main, tray
        cls._saved_config_save = config.save
        cls._saved_tray_available = tray.AVAILABLE
        config.save = lambda data: None
        tray.AVAILABLE = False              # no tray icon during the test

        from launcher import gui
        launcher_main._apply_gui_review_fixes(gui)
        cls.gui = gui
        cls.tk = tk

        try:
            cls.root = tk.Tk()
        except tk.TclError:
            cls._restore_env_and_stubs()
            raise unittest.SkipTest("no display")
        cls.app = gui.LauncherApp(cls.root)
        cls._settle()
        cls.app._apply_tab_minimum_width()  # normally fires 200ms after launch
        cls._settle()
        cls.min_width = cls.root.minsize()[0]

    @classmethod
    def _restore_env_and_stubs(cls):
        for var, was in getattr(cls, "_saved_env", {}).items():
            if was is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = was
        scratch = getattr(cls, "_scratch", None)
        if scratch:
            import shutil
            shutil.rmtree(scratch, ignore_errors=True)
            cls._scratch = None
        saved_config_save = getattr(cls, "_saved_config_save", None)
        if saved_config_save is not None:
            from launcher import config
            config.save = saved_config_save
        saved_tray = getattr(cls, "_saved_tray_available", None)
        if saved_tray is not None:
            from launcher import tray
            tray.AVAILABLE = saved_tray

    @classmethod
    def tearDownClass(cls):
        app = getattr(cls, "app", None)
        if app is not None:
            app._shutting_down = True
            try:
                app.status_poller.stop()
            except Exception:
                pass
        root = getattr(cls, "root", None)
        if root is not None:
            root.destroy()

        cls._restore_env_and_stubs()

    # The module patch _apply_gui_review_fixes applies is deliberately NOT undone:
    # it is idempotent (launcher/main.py guards it) and it is what the real app
    # runs, so a later GUI test that expects the launcher as users get it should
    # find it already in place rather than having to know to re-apply it.

    @classmethod
    def _settle(cls, rounds=6):
        for _ in range(rounds):
            cls.root.update_idletasks()
            cls.root.update()

    def resize(self, width, height):
        """Ask for a size, then report what we actually got."""
        self.root.geometry("{}x{}".format(width, height))
        self._settle()
        got = (self.root.winfo_width(), self.root.winfo_height())
        if abs(got[0] - width) > 8 or abs(got[1] - height) > 8:
            self.skipTest("window manager did not honour the requested geometry")
        return got

    def wrapping_labels(self, widget, found=None):
        found = [] if found is None else found
        for child in widget.winfo_children():
            if (child.winfo_class() == "TLabel"
                    and int(child.cget("wraplength") or 0)):
                found.append(child)
            self.wrapping_labels(child, found)
        return found

    # -- the terminal ----------------------------------------------------- #
    def test_terminal_wraps_and_never_scrolls_sideways(self):
        self.assertEqual(str(self.app.terminal.cget("wrap")), "word")
        self.assertFalse(str(self.app.terminal.cget("xscrollcommand")),
                         "a horizontal scrollbar means lines can hide off-screen")

    def test_a_long_unbroken_path_still_wraps(self):
        self.resize(self.min_width, 700)
        self.app.nb.select(self.app.terminal_tab)
        self.app._append_log("setup", "C:\\a_single_token_with_no_spaces_in_it_at_"
                                      "all_of_the_kind_a_server_prints_when_it_"
                                      "reports_which_file_it_is_serving.iso\n")
        self._settle()
        shown = int(self.app.terminal.count("1.0", "end", "displaylines")[0])
        self.assertGreater(shown, 1, "a word longer than the line must break")

    def test_terminal_grows_with_the_window(self):
        self.app.nb.select(self.app.terminal_tab)
        self.resize(1024, 640)
        short = self.app.terminal.winfo_height()
        self.resize(1024, 1000)
        self.assertGreater(self.app.terminal.winfo_height(), short + 50,
                           "spare height should go to the log, not to dead page")

    # -- reflow ------------------------------------------------------------ #
    def test_wrapping_text_follows_the_window_width(self):
        key = next(iter(self.app.cards))
        labels = self.wrapping_labels(self.app.cards[key])
        self.assertTrue(labels, "the cards should have wrapping text to check")
        self.resize(1400, 900)
        wide = [int(w.cget("wraplength")) for w in labels]
        self.resize(self.min_width, 900)
        narrow = [int(w.cget("wraplength")) for w in labels]
        for before, after in zip(wide, narrow):
            self.assertLess(after, before)

    def test_a_tab_never_opened_still_wraps_to_the_window(self):
        """ttk unmaps unselected tabs, so those cards have no width of their own.

        Wrapping them against their own (unknown) width fed each label a made-up
        number, which the card then reported back as the width it needed --
        widening the whole page for a tab nobody had looked at.
        """
        self.resize(self.min_width, 900)
        limit = self.app.nb.winfo_width()
        for key, card in self.app.cards.items():
            for label in self.wrapping_labels(card):
                self.assertLessEqual(int(label.cget("wraplength")), limit,
                                     "{} wraps wider than the window".format(key))

    def test_the_page_never_needs_more_width_than_it_has(self):
        for width in (self.min_width, 1024, 1400):
            with self.subTest(width=width):
                self.resize(width, 900)
                canvas = self.app._scroll_canvas
                item = int(float(canvas.itemcget(self.app._scroll_window, "width")))
                self.assertEqual(item, canvas.winfo_width(),
                                 "the body must be exactly as wide as the canvas")
                self.assertLessEqual(self.app.nb.winfo_reqwidth(),
                                     self.app.nb.winfo_width() + 2,
                                     "the notebook wants more width than it has")

    def test_the_window_cannot_shrink_past_its_own_tab_strip(self):
        strip = self.app._tab_strip_width()
        if strip <= 0:
            self.skipTest("tab strip could not be measured on this theme")
        self.assertGreaterEqual(self.min_width, strip,
                                "a narrower window would hide the last tab")

    # -- scrollbars -------------------------------------------------------- #
    def test_the_page_scrollbar_is_out_only_when_the_page_overflows(self):
        for width, height in ((1024, 460), (1024, 1000), (self.min_width, 700)):
            with self.subTest(size=(width, height)):
                self.resize(width, height)
                overflows = (self.app.content.winfo_reqheight()
                             > self.app._scroll_canvas.winfo_height() + 2)
                self.assertEqual(bool(self.app._scrollbar.winfo_ismapped()),
                                 overflows)

    def test_the_page_scrollbar_settles_instead_of_flickering(self):
        # Hiding the bar hands the content more width, and more width never makes
        # the page taller -- so the decision must hold, not oscillate.
        self.resize(1024, 1000)
        states = []
        for _ in range(6):
            self._settle(2)
            states.append(bool(self.app._scrollbar.winfo_ismapped()))
        self.assertEqual(len(set(states)), 1, "scrollbar flip-flopped: %s" % states)

    def test_the_bottom_of_a_grown_page_can_always_be_scrolled_to(self):
        """Opening Advanced grew the page but not the scrollregion: the body's
        height is pinned, so no <Configure> fired, the region stayed short, and
        the last advanced fields could never be scrolled into view.
        """
        self.resize(self.min_width, 640)
        self.app.nb.select(self.app.server_tabs["smbv1"])
        card = self.app.cards["smbv1"]
        opened = not card._advanced_shown
        try:
            if opened:
                card._toggle_advanced()
            self._settle()
            canvas = self.app._scroll_canvas
            # The 400 ms reqheight watcher needs real time to notice the growth;
            # give it a bounded window before declaring the bug still present.
            import time
            deadline = time.time() + 2.5
            while time.time() < deadline:
                self._settle(2)
                region = str(canvas.cget("scrollregion") or "0 0 0 0").split()
                if float(region[3]) >= self.app.content.winfo_reqheight():
                    break
                time.sleep(0.1)
            self.assertGreaterEqual(float(region[3]),
                                    self.app.content.winfo_reqheight(),
                                    "scrollregion is shorter than the page: "
                                    "its bottom can never scroll into view")
            # And with the region right, the bottom really is reachable.
            canvas.yview_moveto(1.0)
            self._settle(1)
            self.assertGreaterEqual(canvas.yview()[1], 0.999)
        finally:
            if opened and card._advanced_shown:
                card._toggle_advanced()
            # Settle clean for the tests that follow: at a height the page fits
            # even with the bar out, the scrollbar releases and the wrap labels
            # widen back (a bar left mapped would keep them narrow -- and tall).
            self.resize(1024, 1000)
            import time
            time.sleep(0.5)   # let the reqheight watcher run once for real
            self._settle()

    # -- fields ------------------------------------------------------------ #
    def test_take_445_greys_out_the_port_field_it_overrides(self):
        """A saved take-445 silently wins over the port field -- the field must
        show that, or the user edits a port the server will never use."""
        card = self.app.cards["smbv1"]
        if "take_445" not in card.vars:
            self.skipTest("the take-445 field is Windows-only")
        take = card.vars["take_445"]
        port_entry = card.field_widgets["port"]
        take.set(True)
        self.assertEqual(str(port_entry.cget("state")), "disabled")
        take.set(False)
        self.assertEqual(str(port_entry.cget("state")), "normal")


if __name__ == "__main__":
    unittest.main()
