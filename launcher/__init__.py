"""PS2-Servers launcher.

A small GUI/engine that lets any user pick and run one or more of the PS2 OPL
network servers (SMBv1, UDPBD, UDPFS) without touching a terminal.
"""

from .release_metadata import DISPLAY_VERSION

# The human-facing version, including any pre-release qualifier (e.g. 0.4.5-rc1).
# The numeric build version lives in release_metadata.PRODUCT_VERSION.
__version__ = DISPLAY_VERSION


def _install_dependency_panel_hook():
    """Patch launcher.gui when it is imported so add-on panels/skin hooks mount.

    The normal, boring implementation would be direct calls from the GUI build
    path. This import hook keeps the changes self-contained in the launcher
    package while still avoiding heavy dependencies or runtime plugins.
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
        except ImportError:
            dependency_panel = None
        try:
            from . import asset_skin
        except ImportError:
            asset_skin = None

        original_build = gui.LauncherApp._build

        def build_with_addons(self):
            if asset_skin is not None:
                asset_skin.install(self, gui)
            if dependency_panel is not None:
                dependency_panel.add_panel(self, gui)
            return original_build(self)

        gui.LauncherApp._build = build_with_addons
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
            spec = None
            for finder in sys.meta_path:
                if finder is self:
                    continue
                if hasattr(finder, "find_spec"):
                    spec = finder.find_spec(fullname, path, target_module)
                    if spec:
                        break
            if spec and spec.loader and not isinstance(spec.loader, _GuiLoader):
                spec.loader = _GuiLoader(spec.loader)
            return spec

    for finder in sys.meta_path:
        if getattr(finder, "_ps2_dependency_panel_finder", False):
            return
    sys.meta_path.insert(0, _GuiFinder())


_install_dependency_panel_hook()
