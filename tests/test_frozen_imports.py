"""launcher/serve.py's bundler hints must cover what the servers really import.

The packaged launcher loads each server by file path, so Nuitka cannot see
those imports and does not bundle their dependencies. serve.py works around
that with a block of otherwise-unused `import` statements, and the list is
maintained by hand. Nothing checked it, so a server gaining a new stdlib
dependency produced a build that worked from source and died at runtime in the
packaged one -- which is how the FileNotFoundError on ps2servers_core.py
shipped, and the same shape of failure the packaging data-files test now
covers for source files.

This imports every registered server for real and compares what that pulled in
against what serve.py names, so the check follows the code rather than
somebody's memory.

Run:  python -m unittest tests.test_frozen_imports -v
"""

import ast
import importlib.util
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from launcher import servers  # noqa: E402

_SERVE = os.path.join(_ROOT, "launcher", "serve.py")

#: Modules serve.py has no reason to name.
#:
#: The hint list exists for what a SERVER needs. Anything the launcher package
#: itself imports is already visible to Nuitka through ordinary imports, and
#: anything a test drags in is irrelevant to a packaged build.
_NOT_HINTS = {
    # Imported by serve.py itself, by definition reachable.
    "importlib", "os", "sys",
    # Pulled in by the test harness rather than by a server.
    "unittest", "doctest", "pydoc", "difflib", "pdb", "bdb", "cmd", "code",
    "codeop", "inspect", "dis", "opcode", "linecache", "tokenize", "token",
    "ast", "traceback", "warnings", "contextlib", "functools", "itertools",
    "operator", "types", "weakref", "copy", "copyreg", "pickle", "reprlib",
    "abc", "collections", "keyword", "heapq", "bisect", "random", "string",
    "textwrap", "encodings", "codecs", "io", "atexit", "signal", "errno",
    "gc", "marshal", "builtins", "_thread", "posixpath", "ntpath", "genericpath",
    "stat", "fnmatch", "glob", "shutil", "tempfile", "zipfile", "tarfile",
    "logging", "argparse", "gettext", "locale", "platform", "sysconfig",
    "threading", "queue", "time", "datetime", "calendar", "math", "numbers",
    "decimal", "fractions", "re", "sre_compile", "sre_parse", "sre_constants",
    "enum", "dataclasses", "typing", "json", "base64", "binascii", "struct",
    "hashlib", "hmac", "secrets", "uuid", "socket", "select", "selectors",
    "ssl", "email", "http", "urllib", "subprocess", "shlex", "getpass",
    "zlib", "gzip", "bz2", "lzma", "ctypes", "ipaddress", "unicodedata",
}


def _serve_hint_names():
    """Top-level module names serve.py imports, from its AST."""
    with open(_SERVE, encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), _SERVE)
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def _modules_a_server_pulls_in(module_file, module_dir):
    """Import one server by path and report the stdlib it brought with it."""
    before = set(sys.modules)
    added_path = False
    if module_dir and module_dir not in sys.path:
        sys.path.insert(0, module_dir)
        added_path = True
    try:
        name = "_hintprobe_" + os.path.splitext(os.path.basename(module_file))[0]
        spec = importlib.util.spec_from_file_location(name, module_file)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    finally:
        if added_path:
            try:
                sys.path.remove(module_dir)
            except ValueError:
                pass
    return {m.split(".")[0] for m in set(sys.modules) - before}


class ServeHintsCoverTheServers(unittest.TestCase):
    def test_every_stdlib_module_a_server_imports_is_hinted(self):
        hinted = _serve_hint_names()
        missing = {}

        for key, server in servers.REGISTRY.items():
            if getattr(server, "runtime", "python") != "python":
                continue
            if not server.module_file or not os.path.isfile(server.module_file):
                continue
            pulled = _modules_a_server_pulls_in(server.module_file,
                                                server.module_dir)
            for name in sorted(pulled):
                if name.startswith("_") or name in _NOT_HINTS or name in hinted:
                    continue
                # Builtins are compiled into the interpreter, not shipped as
                # files, so a bundler never needs to be told about them. This
                # is what msvcrt is on Windows, and it is reached transitively
                # from the platform bits of subprocess/getpass rather than by
                # anything here.
                if name in sys.builtin_module_names:
                    continue
                # Only stdlib: a third-party package is a real dependency and
                # build.py handles those separately.
                spec = importlib.util.find_spec(name) if name in sys.modules else None
                origin = getattr(spec, "origin", "") or ""
                if "site-packages" in origin:
                    continue
                # Project modules are shipped as data files, not hints; that is
                # what tests/test_packaging_data_files.py covers.
                if _ROOT.replace(os.sep, "/") in origin.replace(os.sep, "/"):
                    continue
                missing.setdefault(name, []).append(key)

        self.assertEqual(
            missing, {},
            "these stdlib modules are imported by a server but not named in "
            "launcher/serve.py, so a frozen build may not bundle them:\n  "
            + "\n  ".join(f"{name} (via {', '.join(keys)})"
                          for name, keys in sorted(missing.items())))

    def test_the_hint_block_is_still_there(self):
        """A guard on the guard: the list is load-bearing and looks like litter.

        Every name in it is unused in the file, so a tidy-up that removes
        "dead imports" would silently break packaged builds. The comment above
        it says so; this makes the comment enforceable.
        """
        hinted = _serve_hint_names()
        self.assertGreater(
            len(hinted), 10,
            "launcher/serve.py's bundler-hint imports are gone. They look "
            "unused because they are -- they exist so Nuitka bundles what the "
            "servers need. See the comment above them.")
        for essential in ("socket", "struct", "threading", "hashlib"):
            with self.subTest(module=essential):
                self.assertIn(essential, hinted)


if __name__ == "__main__":
    unittest.main()
