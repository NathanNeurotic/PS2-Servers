"""The operator manual that ships must describe the binary that ships.

Two things went wrong at once and this pins both.

There were two copies of the manual: docs/INSTRUCTIONS.md, which
.github/workflows/edge-build.yml packages into every Edge download, and
native/ps2servers-edge/INSTRUCTIONS.md, which nobody packaged. They were created
identical, and then the web GUI PRs updated only the second one -- so the manual
users actually received documented three subcommands out of four, and there was
nothing in the tree that could notice.

Run:  python -m unittest tests.test_edge_manual -v
"""

import os
import re
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MANUAL = os.path.join(_ROOT, "docs", "INSTRUCTIONS.md")
_MAIN_GO = os.path.join(_ROOT, "native", "ps2servers-edge", "cmd",
                        "ps2servers-edge", "main.go")
_BUILD = os.path.join(_ROOT, ".github", "workflows", "edge-build.yml")


def _read(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


class ThereIsOnlyOneManual(unittest.TestCase):
    def test_no_second_copy_exists(self):
        stray = os.path.join(_ROOT, "native", "ps2servers-edge", "INSTRUCTIONS.md")
        self.assertFalse(
            os.path.exists(stray),
            "a second copy of the operator manual is back. Only "
            "docs/INSTRUCTIONS.md is packaged, so the other one silently "
            "becomes the version nobody reads and the packaged one goes stale.")

    def test_the_packaged_file_is_the_one_that_exists(self):
        self.assertTrue(os.path.isfile(_MANUAL))
        self.assertIn("docs/INSTRUCTIONS.md", _read(_BUILD),
                      "the build no longer packages docs/INSTRUCTIONS.md; this "
                      "test is checking the wrong file")


class TheManualCoversEverySubcommand(unittest.TestCase):
    @staticmethod
    def _subcommands():
        source = _read(_MAIN_GO)
        found = set(re.findall(r'os\.Args\[1\] [!=]= "([a-z0-9]+)"', source))
        return found - {"version", "--version"}

    def test_every_subcommand_has_a_section(self):
        manual = _read(_MANUAL)
        for command in sorted(self._subcommands()):
            with self.subTest(subcommand=command):
                self.assertRegex(
                    manual, r"##\s*\d+\.\s*Subcommand:\s*`" + command + "`",
                    f"the shipped manual has no section for `{command}`, so a "
                    "user who downloads Edge is not told the feature exists")


class DocumentedFlagsAreReal(unittest.TestCase):
    """A flag in the manual that the binary does not have is worse than none.

    Read from main.go rather than from --help, so this needs no Go toolchain.
    """

    @staticmethod
    def _flags_of(func_name):
        source = _read(_MAIN_GO)
        start = source.find(f"func {func_name}(")
        if start < 0:
            return set()
        end = source.find("\nfunc ", start + 1)
        body = source[start:end if end > 0 else len(source)]
        # fs.String("name", ...) and fs.Var(&value, "name", ...) both occur.
        return set(re.findall(r'fs\.\w+\(\s*(?:&\w+,\s*)?"([a-z0-9-]+)"', body))

    def _section(self, command):
        manual = _read(_MANUAL)
        match = re.search(
            r"##\s*\d+\.\s*Subcommand:\s*`" + command + r"`(.*?)(?=\n##\s|\Z)",
            manual, re.DOTALL)
        self.assertIsNotNone(match, f"no section for `{command}`")
        return match.group(1)

    def test_webui_flags_in_the_manual_exist(self):
        # webui is the one that shipped undocumented, so it gets checked by
        # name rather than only through the generic loop below.
        flags = self._flags_of("runWebUI")
        self.assertTrue(flags, "no flags parsed out of runWebUI")
        documented = set(re.findall(r"`--([a-z0-9-]+)", self._section("webui")))
        for flag in sorted(documented):
            with self.subTest(flag=flag):
                self.assertIn(
                    flag, flags,
                    f"the manual documents --{flag} for webui, but runWebUI "
                    "defines no such flag")

    def test_the_security_relevant_defaults_are_stated(self):
        section = self._section("webui")
        self.assertIn("127.0.0.1", section,
                      "the manual must state that webui binds loopback by "
                      "default; the previous text said 0.0.0.0, which is now "
                      "refused without a password")
        self.assertIn("--auth-pass", section,
                      "the manual must name the flag that a LAN bind requires")


if __name__ == "__main__":
    unittest.main()
