"""Static and content safety checks for the PS2 setup wizard."""
import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WIZARD = ROOT / "launcher" / "setup_guide.py"
GUI = ROOT / "launcher" / "gui.py"


class SetupWizardTests(unittest.TestCase):
    def test_guide_steps_are_complete_and_nonempty(self):
        module = ast.parse(WIZARD.read_text(encoding="utf-8"))
        steps = next(node.value for node in module.body
                     if isinstance(node, ast.Assign)
                     and any(isinstance(t, ast.Name) and t.id == "STEPS"
                             for t in node.targets))
        pairs = ast.literal_eval(steps)
        self.assertEqual(len(pairs), 6)
        self.assertTrue(all(title and len(body) >= 100 for title, body in pairs))
        for keyword in ("SMB", "UDPFS", "UDPBD", "RetroAchievements",
                        "first", "server", "LAN"):
            text = " ".join(" ".join(p) for p in pairs).lower()
            self.assertIn(keyword.lower(), text)

    def test_read_only_guide_has_no_mutating_services(self):
        module = ast.parse(WIZARD.read_text(encoding="utf-8"))
        self.assertFalse(any(isinstance(n, ast.ImportFrom) and n.module in
                             {"launcher.servers", "launcher.directlink", "subprocess",
                              "os", "shutil"} for n in ast.walk(module)))
        methods = {n.name for n in ast.walk(module) if isinstance(n, ast.FunctionDef)}
        self.assertTrue({"show_step", "previous", "advance"}.issubset(methods))
        self.assertFalse({"save", "configure_network", "format", "start_server"} & methods)

    def test_gui_button_connected_once(self):
        source = GUI.read_text(encoding="utf-8")
        ast.parse(source)
        self.assertEqual(source.count('text="PS2 setup guide…"'), 1)
        self.assertEqual(source.count("def _open_ps2_setup_guide("), 1)
        self.assertIn("self._ps2_setup_guide = SetupGuide(", source)
        self.assertIn("self.root, self.current_ip(),", source)
        self.assertIn("existing.winfo_exists()", source)

    def test_first_run_invitation_and_persistent_status(self):
        source = GUI.read_text(encoding="utf-8")
        ast.parse(source)
        self.assertIn("self._invite_ps2_setup_guide = not bool(self.saved)", source)
        self.assertIn("self.root.after(1200, self._offer_ps2_setup_guide)", source)
        self.assertIn("self._on_ps2_setup_guide_closed(False)", source)
        self.assertIn('"ps2_setup_guide_status"] = "completed"', source)
        self.assertIn('"ps2_setup_guide_status"] = "dismissed"', source)
        self.assertIn('guide_status in ("dismissed", "completed")', source)
        self.assertIn("on_close=self._on_ps2_setup_guide_closed", source)

    def test_guide_included_in_packaged_desktop(self):
        build = ast.parse((ROOT / "build" / "build.py").read_text(encoding="utf-8"))
        modules = next(node.value for node in build.body
                       if isinstance(node, ast.Assign)
                       and any(isinstance(t, ast.Name) and t.id == "INCLUDE_MODULES"
                               for t in node.targets))
        self.assertIn("launcher.setup_guide", ast.literal_eval(modules))

    def test_close_callback_distinguishes_finish_from_dismissal(self):
        module = ast.parse(WIZARD.read_text(encoding="utf-8"))
        guide = next(n for n in module.body if isinstance(n, ast.ClassDef)
                     and n.name == "SetupGuide")
        method = next(n for n in guide.body if isinstance(n, ast.FunctionDef)
                      and n.name == "close")
        namespace = {}
        exec(compile(ast.Module(body=[method], type_ignores=[]),
                     str(WIZARD), "exec"), namespace)
        class FakeWindow:
            def __init__(self):
                self.callbacks = []
                self.destroy_calls = 0
                self._on_close = self.callbacks.append

            def destroy(self):
                self.destroy_calls += 1

        for completed in (False, True):
            window = FakeWindow()
            namespace["close"](window, completed=completed)
            namespace["close"](window, completed=completed)
            self.assertEqual(window.callbacks, [completed])
            self.assertEqual(window.destroy_calls, 2)

        source = WIZARD.read_text(encoding="utf-8")
        self.assertIn("self.close(completed=True)", source)
        self.assertIn('command=self.close', source)
        self.assertIn('self.protocol("WM_DELETE_WINDOW", self.close)', source)


if __name__ == "__main__":
    unittest.main()
