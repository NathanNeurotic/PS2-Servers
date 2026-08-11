"""About tab: the version label carries the commit, and the prose matches the app.

ABOUT_TEXT once described three modes when five shipped, quoted the standalone
script's port (1111) while the SMBv1 card defaulted to 1025, and predated the
445 refusal behaviour. These tests keep the page honest about what it ships.

Run:
    python -m unittest tests.test_about_page -v
"""

import os
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class AboutTextTest(unittest.TestCase):
    def test_every_registered_mode_is_described(self):
        from launcher import gui, servers
        for server in servers.REGISTRY.values():
            self.assertIn(server.label.split()[0], gui.ABOUT_TEXT,
                          "ABOUT_TEXT never mentions " + server.label)

    def test_smb_default_ports_match_the_cards(self):
        # The prose once quoted 1111 -- the standalone script's default --
        # while the launcher's SMBv1 card has defaulted to 1025.
        from launcher import gui, servers
        for key in ("smbv1", "smbv2"):
            self.assertIn(servers.REGISTRY[key].port_display(), gui.ABOUT_TEXT)
        self.assertNotIn("1111", gui.ABOUT_TEXT)


class VersionLabelTest(unittest.TestCase):
    def test_git_wins_over_a_stale_bake(self):
        # build.py leaves _build_id.py in the tree; once HEAD moves, the bake
        # is stale and a source run must show the checkout's real commit.
        from launcher import release_metadata
        fake = types.ModuleType("launcher._build_id")
        fake.COMMIT = "abc1234"
        with (mock.patch.dict(sys.modules, {"launcher._build_id": fake}),
              mock.patch("subprocess.check_output", return_value=b"deadbee\n")):
            self.assertEqual(release_metadata.build_commit(), "deadbee")
            self.assertEqual(
                release_metadata.version_label(),
                "v" + release_metadata.DISPLAY_VERSION + " (deadbee)")

    def test_packaged_build_uses_the_bake_and_never_asks_git(self):
        # An exe has no checkout; worse, git run from wherever it sits could
        # answer for an unrelated surrounding repo. The bake is the truth.
        from launcher import release_metadata
        fake = types.ModuleType("launcher._build_id")
        fake.COMMIT = "abc1234"
        with (mock.patch.dict(sys.modules, {"launcher._build_id": fake}),
              mock.patch.object(release_metadata.sys, "frozen", True,
                                create=True),
              mock.patch("subprocess.check_output") as git_mock):
            self.assertEqual(release_metadata.build_commit(), "abc1234")
            git_mock.assert_not_called()

    def test_source_falls_back_to_the_bake_when_git_is_unavailable(self):
        from launcher import release_metadata
        fake = types.ModuleType("launcher._build_id")
        fake.COMMIT = "abc1234"
        with (mock.patch.dict(sys.modules, {"launcher._build_id": fake}),
              mock.patch("subprocess.check_output", side_effect=OSError)):
            self.assertEqual(release_metadata.build_commit(), "abc1234")

    def test_unknown_commit_shows_the_bare_version(self):
        from launcher import release_metadata
        with (mock.patch.dict(sys.modules, {"launcher._build_id": None}),
              mock.patch("subprocess.check_output", side_effect=OSError)):
            self.assertEqual(release_metadata.build_commit(), "")
            self.assertEqual(release_metadata.version_label(),
                             "v" + release_metadata.DISPLAY_VERSION)


class AboutTabVersionGUITest(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.mkdtemp(prefix="ps2-about-test-")
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

    def test_about_brand_shows_the_version_with_commit(self):
        try:
            import tkinter as tk
        except ImportError:
            raise unittest.SkipTest("no tkinter")
        from launcher import gui, release_metadata
        try:
            root = tk.Tk()
        except tk.TclError:
            raise unittest.SkipTest("no display")
        # The About tab is built inside LauncherApp.__init__, so the commit
        # lookup has to be fixed before construction.
        with mock.patch.object(release_metadata, "build_commit",
                               return_value="cafef00d"):
            app = gui.LauncherApp(root)

        def cleanup(app=app, root=root):
            app._shutting_down = True
            app._cancel_flashes()
            try:
                deadline = time.time() + 0.8
                while time.time() < deadline:
                    root.update()
                    time.sleep(0.1)
            except tk.TclError:
                pass
            root.destroy()

        self.addCleanup(cleanup)

        wanted = "v" + release_metadata.DISPLAY_VERSION + " (cafef00d)"
        texts = []

        def collect(widget):
            if isinstance(widget, (tk.Label, gui.ttk.Label)):
                texts.append(str(widget.cget("text")))
            for child in widget.winfo_children():
                collect(child)

        for tab_id in app.nb.tabs():
            collect(app.nb.nametowidget(tab_id))
        self.assertIn(wanted, texts)


if __name__ == "__main__":
    unittest.main()
