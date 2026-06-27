"""PS2-Servers launcher.

A small GUI/engine that lets any user pick and run one or more of the PS2 OPL
network servers (SMBv1, UDPBD, UDPFS) without touching a terminal.
"""

__version__ = "0.1.0"


def _install_dependency_panel_hook():
    """Patch launcher.gui when it is imported so the dependency panel is mounted.

    The normal, boring implementation would be a direct call from main.py's GUI
    build wrapper. This import hook keeps the change self-contained in the
    launcher package while still avoiding heavy dependencies or runtime plugins.
    """
    import importlib.abc
    import importlib.machinery
    import sys

    target = __name__ + ".gui"

    def patch_gui(gui):
        if getattr(gui, "_ps2_dependency_panel_patched", False):
            return
        try:
            from . import dependency_panel
        except Exception:
            return

        original_build = gui.LauncherApp._build

        def build_with_dependency_panel(self):
            dependency_panel.add_panel(self, gui)
            return original_build(self)

        gui.LauncherApp._build = build_with_dependency_panel
        gui._ps2_dependency_panel_patched = True

    if target in sys.modules:
        patch_gui(sys.modules[target])
        return

    class _GuiLoader(importlib.abc.Loader):
        def __init__(self, wrapped):
            self.wrapped = wrapped

        def create_module(self, spec):
            if hasattr(self.wrapped, "create_module"):
                return self.wrapped.create_module(spec)
            return None

        def exec_module(self, module):
            self.wrapped.exec_module(module)
            patch_gui(module)

    class _GuiFinder(importlib.abc.MetaPathFinder):
        _ps2_dependency_panel_finder = True

        def find_spec(self, fullname, path=None, target_module=None):
            if fullname != target:
                return None
            spec = importlib.machinery.PathFinder.find_spec(fullname, path)
            if spec and spec.loader and not isinstance(spec.loader, _GuiLoader):
                spec.loader = _GuiLoader(spec.loader)
            return spec

    for finder in sys.meta_path:
        if getattr(finder, "_ps2_dependency_panel_finder", False):
            return
    sys.meta_path.insert(0, _GuiFinder())


_install_dependency_panel_hook()
