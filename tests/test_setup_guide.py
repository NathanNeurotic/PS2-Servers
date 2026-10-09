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
        self.assertIn("SetupGuide(self.root, self.current_ip())", source)
        self.assertIn("existing.winfo_exists()", source)


if __name__ == "__main__":
    unittest.main()
