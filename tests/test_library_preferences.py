"""Check desktop library preferences without requiring a display."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]
LIBRARY_GUI = ROOT / "launcher" / "library_gui.py"
MAIN_GUI = ROOT / "launcher" / "gui.py"


class LibraryPreferencesTests(unittest.TestCase):
    def test_choices_persist_without_replacing_existing_settings(self):
        tree = ast.parse(LIBRARY_GUI.read_text(encoding="utf-8"))
        widget = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                      and node.name == "LibraryWindow")
        method = next(node for node in widget.body if isinstance(node, ast.FunctionDef)
                      and node.name == "save_preferences")
        writes = []
        original = {"servers": {"udpfs": {"port": 3000}},
                    "game_library_folder": "/my/library"}
        context = {"config": SimpleNamespace(load=lambda: dict(original),
                                              save=lambda value: writes.append(value))}
        exec(compile(ast.Module(body=[method], type_ignores=[]),
                     str(LIBRARY_GUI), "exec"), context)
        obj = SimpleNamespace(view=SimpleNamespace(get=lambda: "Catalogue"),
                              kind=SimpleNamespace(get=lambda: "CD"),
                              status=SimpleNamespace(set=lambda value: None))
        context["save_preferences"](obj)
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0]["game_library_view"], "Catalogue")
        self.assertEqual(writes[0]["game_library_kind"], "CD")
        self.assertEqual(writes[0]["servers"], original["servers"])
        self.assertEqual(writes[0]["game_library_folder"], "/my/library")

    def test_main_launcher_preserves_preferences_on_save(self):
        source = MAIN_GUI.read_text(encoding="utf-8")
        ast.parse(source)
        self.assertIn('"game_library_view", "game_library_kind"', source)
        gui_source = LIBRARY_GUI.read_text(encoding="utf-8")
        ast.parse(gui_source)
        self.assertIn('destination.bind("<<ComboboxSelected>>"', gui_source)
        self.assertIn('choice.bind("<<ComboboxSelected>>", self.on_view_changed)', gui_source)
        self.assertIn('self.save_preferences()\n        self.destroy()', gui_source)


if __name__ == "__main__":
    unittest.main()
