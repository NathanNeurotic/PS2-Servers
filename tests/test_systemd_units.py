"""The headless deployment path has to keep working unattended.

Someone running PS2 Servers under systemd is not watching a window, and the
ways this breaks are all silent: a flag renamed out from under the env file, an
instance name that is not a real server key, or an import added to the launcher
that drags in tkinter and makes every service on a machine with no display fail
to start.

Requested by a Linux user who wanted to log out and have the server keep
running. Edge already had a unit but Edge serves no SMB, which is exactly what
that user needed.
"""

import os
import re
import subprocess
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_SYSTEMD = os.path.join(_ROOT, "packaging", "systemd")

from launcher.servers import REGISTRY  # noqa: E402


def _read(name):
    with open(os.path.join(_SYSTEMD, name), "r", encoding="utf-8") as handle:
        return handle.read()


def _directives(name):
    """The unit's actual settings, with comments stripped.

    Parsed rather than grepped, because these files explain themselves: a
    comment saying why a directive is deliberately absent would otherwise read
    as the directive being present.
    """
    settings = {}
    for line in _read(name).splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ";", "[")):
            continue
        if "=" in line:
            key, value = line.split("=", 1)
            settings.setdefault(key.strip(), []).append(value.strip())
    return settings


class HeadlessGuarantee(unittest.TestCase):
    def test_serve_does_not_import_tkinter(self):
        """The whole reason a systemd unit is possible.

        --serve is dispatched before the GUI is imported. If that ever stops
        being true, every headless install breaks at once with an import error
        about a missing display, and nobody running a service would connect it
        to a launcher change.
        """
        code = (
            "import sys; sys.argv = ['x', '--serve', 'smbv1', '--help'];"
            "import launcher.main;"
            "sys.exit(1 if 'tkinter' in sys.modules else 0)"
        )
        result = subprocess.run([sys.executable, "-c", code], cwd=_ROOT,
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(
            result.returncode, 0,
            "importing launcher.main pulled in tkinter; --serve must stay "
            "usable on a machine with no display.\n" + result.stderr[-500:])


class UnitFiles(unittest.TestCase):
    def test_template_instance_keys_are_real_servers(self):
        # The unit passes %i straight to --serve, so a name in the docs that is
        # not a registry key produces a service that starts and immediately
        # fails.
        readme = _read("README.md")
        for key in re.findall(r"ps2servers@([a-z0-9]+)", readme):
            with self.subTest(key=key):
                self.assertIn(key, REGISTRY,
                              f"README references ps2servers@{key} but {key!r} "
                              "is not a server key")

    def test_template_uses_unbraced_args_so_they_split(self):
        # systemd splits an unbraced $VAR into separate arguments and does not
        # split ${VAR}. Braced here would hand the server one long argument.
        exec_start = _directives("ps2servers@.service")["ExecStart"][0]
        self.assertIn("--serve %i $PS2SERVERS_ARGS", exec_start)
        self.assertNotIn("${PS2SERVERS_ARGS}", exec_start)

    def test_share_is_writable_under_protectsystem_strict(self):
        # ProtectSystem=strict without ReadWritePaths makes the whole
        # filesystem read-only, and OPL writes its settings to the share. The
        # failure would look like a network problem, not a permissions one.
        unit = _directives("ps2servers@.service")
        self.assertEqual(unit.get("ProtectSystem"), ["strict"])
        self.assertTrue(unit.get("ReadWritePaths"),
                        "the share must be writable or OPL cannot save")

    def test_no_memory_deny_write_execute_for_the_python_build(self):
        # Set on the Edge unit, which is a static Go binary. CPython allocates
        # write-then-execute pages, so the same directive here would stop the
        # service from starting at all.
        self.assertNotIn("MemoryDenyWriteExecute", _directives("ps2servers@.service"))
        self.assertIn("MemoryDenyWriteExecute", _directives("ps2servers-edge.service"))


class EnvFileFlagsAreReal(unittest.TestCase):
    def test_every_flag_in_the_env_file_exists(self):
        """A renamed flag would leave the env file pointing at nothing.

        Checked against the server's own --help rather than a hardcoded list,
        so this fails when the CLI changes rather than when someone remembers
        to update a test.
        """
        env = _read("ps2servers-smbv1.env")
        args = ""
        for line in env.splitlines():
            if line.startswith("PS2SERVERS_ARGS="):
                args = line.split("=", 1)[1]
        self.assertTrue(args, "no PS2SERVERS_ARGS line in the env file")

        flags = [tok for tok in args.split() if tok.startswith("--")]
        self.assertTrue(flags, "env file passes no flags")

        entry = REGISTRY["smbv1"]
        help_text = subprocess.run(
            [sys.executable, entry.module_file, "--help"],
            capture_output=True, text=True, timeout=120).stdout

        for flag in flags:
            name = flag.split("=", 1)[0]
            with self.subTest(flag=name):
                self.assertIn(name, help_text,
                              f"{name} is in the env file but not in the "
                              "smbv1 server's --help")


if __name__ == "__main__":
    unittest.main()
