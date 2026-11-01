"""The packaged build must ship every server source the launcher loads.

The launcher loads each Python server by FILE PATH at runtime, so Nuitka cannot
see those imports and the files have to be listed as data. Nothing at build time
notices when that list falls out of step with the registry, and the failure only
appears in a packaged build -- running from source always works because the file
is sitting on disk.

That is exactly how a release shipped with UDPFS broken: the UDPFS entry point
moved to udpfs_server/ps2servers_core.py, build.py still listed only
udpfs_server/udpfs_server.py, and the packaged app died with

    FileNotFoundError: ...\\udpfs_server\\ps2servers_core.py

the moment anyone selected UDPFS. These tests fail in CI instead.

Run:  python -m unittest tests.test_packaging_data_files -v
"""

import ast
import importlib.util
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from launcher.servers import REGISTRY  # noqa: E402


def _load_build_module():
    """Import build/build.py by path; it is a script, not an installed module."""
    spec = importlib.util.spec_from_file_location(
        "_ps2_build", os.path.join(ROOT, "build", "build.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BUILD = _load_build_module()


def _rel(path):
    return os.path.relpath(path, ROOT).replace(os.sep, "/")


class DataFilesCoverRegistryTests(unittest.TestCase):
    def test_every_python_server_entry_point_is_shipped(self):
        for key, server in REGISTRY.items():
            if server.runtime != "python" or not server.module_file:
                continue
            with self.subTest(server=key):
                self.assertIn(
                    _rel(server.module_file), BUILD.DATA_FILES,
                    "{} loads {} at runtime but the packaged build does not ship "
                    "it; the packaged app will raise FileNotFoundError when this "
                    "server is selected".format(key, _rel(server.module_file)))

    def test_every_shipped_file_exists(self):
        for rel in BUILD.DATA_FILES:
            with self.subTest(path=rel):
                self.assertTrue(
                    os.path.isfile(os.path.join(ROOT, rel)),
                    "{} is listed for packaging but is not in the tree".format(rel))

    def test_registry_module_dirs_exist(self):
        # module_dir goes on sys.path so a server's sibling imports resolve.
        for key, server in REGISTRY.items():
            if server.runtime != "python" or not server.module_dir:
                continue
            with self.subTest(server=key):
                self.assertTrue(os.path.isdir(server.module_dir),
                                "{} module_dir is missing".format(key))


class SiblingImportsAreShippedTests(unittest.TestCase):
    """A shipped entry point may import a sibling module by name.

    udpfs_server/ps2servers_core.py does `from udpfs_server import (...)`. That
    sibling is not a registry entry, so deriving the list from REGISTRY alone
    would miss it and reproduce the same class of failure one level down.
    """

    @staticmethod
    def _imported_names(path):
        with open(path, "r", encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=path)
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    names.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0 and node.module:
                    names.add(node.module.split(".")[0])
        return names

    def test_sibling_modules_of_shipped_files_are_also_shipped(self):
        shipped = set(BUILD.DATA_FILES)
        packages = set(getattr(BUILD, "INCLUDE_PACKAGES", []))
        for rel in sorted(shipped):
            path = os.path.join(ROOT, rel)
            directory = os.path.dirname(path)
            for name in self._imported_names(path):
                sibling = os.path.join(directory, name + ".py")
                if not os.path.isfile(sibling):
                    continue  # stdlib, third-party, or a package
                if name in packages:
                    continue  # compiled in as a real package instead
                with self.subTest(source=rel, imports=name):
                    self.assertIn(
                        _rel(sibling), shipped,
                        "{} imports sibling module '{}' but {} is not shipped; "
                        "the packaged build would fail to import it".format(
                            rel, name, _rel(sibling)))

    def test_all_server_stdlib_imports_are_statically_imported_by_serve(self):
        serve_path = os.path.join(ROOT, "launcher", "serve.py")
        serve_imports = self._imported_names(serve_path)
        shipped = set(BUILD.DATA_FILES)
        for rel in sorted(shipped):
            path = os.path.join(ROOT, rel)
            directory = os.path.dirname(path)
            for name in self._imported_names(path):
                sibling = os.path.join(directory, name + ".py")
                if os.path.isfile(sibling):
                    continue  # local sibling, not stdlib
                # Ignore local packages / third-party libs handled via INCLUDE_PACKAGES or sys.path
                if name in ("compressed_iso", "lz4", "launcher", "udpfs_server", "smb2_server", "smbv1_server", "udpbd_server"):
                    continue
                with self.subTest(source=rel, stdlib_module=name):
                    self.assertIn(
                        name, serve_imports,
                        "{} imports stdlib module '{}' but launcher/serve.py does not import it; "
                        "frozen builds will fail with ModuleNotFoundError: No module named '{}'".format(
                            rel, name, name))



class LauncherImportsAreCompiledInTests(unittest.TestCase):
    """Shipped server files are data, so Nuitka never sees what they import.

    A `from launcher import session_stats` in http_server.py imports fine from
    a checkout and fails in the packaged app unless the module is listed in
    INCLUDE_MODULES (or imported statically by the launcher itself).
    """

    @staticmethod
    def _launcher_modules(path):
        with open(path, "r", encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=path)
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("launcher."):
                        found.add(alias.name)
            elif isinstance(node, ast.ImportFrom) and node.level == 1:
                # `from .x import y` inside launcher/ itself
                if node.module:
                    found.add("launcher." + node.module)
                else:
                    found.update("launcher." + a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                if node.module == "launcher":
                    found.update("launcher." + a.name for a in node.names)
                elif node.module.startswith("launcher."):
                    found.add(node.module)
        return found

    def test_launcher_modules_used_by_shipped_servers_are_included(self):
        included = set(BUILD.INCLUDE_MODULES)
        # The package __init__ and serve.py are always compiled, so what they
        # import statically is in the build too.
        for always in ("launcher/__init__.py", "launcher/serve.py"):
            included |= self._launcher_modules(os.path.join(ROOT, always))
        for rel in sorted(BUILD.DATA_FILES):
            if not rel.endswith(".py") or rel.startswith("launcher/"):
                continue
            for module in sorted(self._launcher_modules(os.path.join(ROOT, rel))):
                with self.subTest(source=rel, imports=module):
                    self.assertIn(
                        module, included,
                        "{} imports {} but build.py does not compile it in; the "
                        "packaged app fails with ModuleNotFoundError".format(rel, module))


if __name__ == "__main__":
    unittest.main()
