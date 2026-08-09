"""Files that something executes must be committed with LF endings.

This is not tidiness. The OpenWrt init script runs under ash, where a line
ending in CRLF makes the last token `do\\r` rather than `do`, and the script
fails to parse -- so the package installs, starts nothing, and says only
"Syntax error: word unexpected". A shell script with a CRLF shebang fails with
"bad interpreter". A systemd unit takes the \\r as part of the value, so
ExecStart names a path that does not exist.

Checked against the COMMITTED blob rather than the working tree, because on
Windows the working tree legitimately holds CRLF: core.autocrlf converts on
checkout and normalises back on commit. The thing that ships is the blob. That
also means this test passes on a machine where `dash -n` on the checked-out
file fails, which is confusing exactly once -- hence this paragraph.

Run:  python -m unittest tests.test_line_endings -v
"""

import os
import subprocess
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Suffixes and exact paths whose content is executed or parsed by a tool that
#: does not tolerate CRLF.
_MUST_BE_LF = (".sh", ".init", ".service", ".env", ".config", ".mk")
_MUST_BE_LF_NAMES = ("Makefile", "AppRun")


def _tracked_files():
    out = subprocess.run(["git", "ls-files", "-z"], cwd=_ROOT,
                         capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        raise unittest.SkipTest("git ls-files failed; not a checkout?")
    return [p for p in out.stdout.split("\0") if p]


def _blob(path):
    out = subprocess.run(["git", "show", "HEAD:" + path], cwd=_ROOT,
                         capture_output=True, timeout=120)
    if out.returncode != 0:
        return None  # not in HEAD yet (a new file in the working tree)
    return out.stdout


class ExecutedFilesAreLF(unittest.TestCase):
    def test_no_committed_shell_or_unit_file_has_crlf(self):
        offenders = []
        for path in _tracked_files():
            name = os.path.basename(path)
            if not (path.endswith(_MUST_BE_LF) or name in _MUST_BE_LF_NAMES):
                continue
            data = _blob(path)
            if data is None:
                continue
            if b"\r\n" in data:
                offenders.append(path)
        self.assertEqual(
            offenders, [],
            "committed with CRLF, which breaks the tool that reads them:\n  "
            + "\n  ".join(offenders))

    def test_gitattributes_pins_the_rule(self):
        """A passing test today is luck without this; core.autocrlf is per-machine."""
        path = os.path.join(_ROOT, ".gitattributes")
        self.assertTrue(
            os.path.isfile(path),
            ".gitattributes is gone, so line endings are decided by each "
            "contributor's git config again")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        for pattern in ("*.sh", "*.init", "*.service", "*.env"):
            with self.subTest(pattern=pattern):
                self.assertRegex(
                    text, re_escape(pattern) + r"\s+text\s+eol=lf",
                    f"{pattern} is not pinned to LF")


def re_escape(value):
    import re
    return re.escape(value)


class TheInitScriptParsesUnderAPosixShell(unittest.TestCase):
    """The case that motivated all of the above.

    tests/test_edge_service_modes.py runs this too, but skips on Windows --
    which is where the CRLF comes from, so the check was absent exactly where
    the problem lives. This one feeds the shell the committed blob, so it runs
    everywhere a POSIX shell exists.
    """

    def test_dash_or_sh_accepts_the_committed_init(self):
        import shutil
        import tempfile

        rel = os.path.join("packaging", "openwrt", "files",
                           "ps2servers-edge.init").replace(os.sep, "/")
        data = _blob(rel)
        if data is None:
            self.skipTest("init script not in HEAD")
        self.assertNotIn(b"\r\n", data, "the committed init script has CRLF")

        shell = None
        for candidate in ("dash", "ash", "sh", "bash"):
            if shutil.which(candidate):
                shell = candidate
                break
        if shell is None:
            self.skipTest("no POSIX shell available")

        with tempfile.NamedTemporaryFile(suffix=".init", delete=False) as handle:
            handle.write(data)
            temp = handle.name
        self.addCleanup(os.unlink, temp)

        result = subprocess.run([shell, "-n", temp], capture_output=True,
                                text=True, timeout=60)
        self.assertEqual(result.returncode, 0,
                         f"{shell} -n rejected the committed init script:\n"
                         f"{result.stderr}")


if __name__ == "__main__":
    unittest.main()
