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


def _directives(name, section="Service"):
    """One section's settings, with comments stripped.

    Parsed rather than grepped, because these files explain themselves: a
    comment saying why a directive is deliberately absent would otherwise read
    as the directive being present.

    Section-aware on purpose. A flat parse would let a directive in [Unit]
    satisfy an assertion about [Service], which is not merely untidy -- systemd
    ignores a [Service] directive placed in [Unit], so the test would pass
    while the setting did nothing.
    """
    settings = {}
    current = None
    for line in _read(name).splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
            continue
        if current == section and "=" in line:
            key, value = line.split("=", 1)
            settings.setdefault(key.strip(), []).append(value.strip())
    return settings


def _env_args(name):
    """The PS2SERVERS_ARGS token list from an env file."""
    for line in _read(name).splitlines():
        if line.startswith("PS2SERVERS_ARGS="):
            return line.split("=", 1)[1].split()
    return []


class HeadlessGuarantee(unittest.TestCase):
    def test_serve_does_not_import_tkinter(self):
        """The whole reason a systemd unit is possible.

        --serve must reach a running server without the GUI. If that stops
        being true, every headless install breaks at once with an error about a
        missing display, and nobody running a service would connect it to a
        launcher change.

        main() is CALLED, not merely imported. An earlier version of this test
        only imported launcher.main and checked sys.modules, which sounded
        equivalent and was not: an `import tkinter` placed directly in
        launcher/serve.py still passed it, because nothing ever executed the
        dispatch. Verified by doing exactly that before rewriting it.

        --help makes the server's own argparse exit as soon as it is reached,
        so this walks the whole path -- main() -> run_serve() -> the server
        module -- without binding a socket.
        """
        code = (
            "import sys\n"
            "import launcher.main\n"
            "try:\n"
            "    launcher.main.main(['--serve', 'smbv1', '--help'])\n"
            "except SystemExit:\n"
            "    pass\n"
            "sys.stderr.write('REACHED\\n')\n"
            "sys.exit(1 if 'tkinter' in sys.modules else 0)\n"
        )
        result = subprocess.run([sys.executable, "-c", code], cwd=_ROOT,
                                capture_output=True, text=True, timeout=120)
        # Proves the dispatch reached the SERVER, not merely that main()
        # returned. `except SystemExit` also swallows run_serve's own early
        # bail-outs ("unknown server", missing module), and in those cases
        # tkinter would not be loaded either -- so without this the test could
        # pass having executed almost nothing. This string comes from
        # smbserver_opl.py's own argparse description.
        self.assertIn("REACHED", result.stderr,
                      "the --serve dispatch did not complete:\n" + result.stderr[-800:])
        self.assertIn("Minimal SMBv1 server", result.stdout,
                      "the server's own --help never printed, so the dispatch "
                      "bailed out before reaching it and this test proved "
                      "nothing:\n" + result.stdout[-800:] + result.stderr[-800:])
        self.assertEqual(
            result.returncode, 0,
            "the --serve path pulled in tkinter; it must stay usable on a "
            "machine with no display.\n" + result.stderr[-500:])


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
        #
        # Matched exactly, not with assertIn: a substring check passes on
        # $PS2SERVERS_ARGS_EXTRA, which expands to a different (unset)
        # variable and silently drops every flag.
        exec_start = _directives("ps2servers@.service")["ExecStart"][0]
        self.assertTrue(
            exec_start.endswith("--serve %i $PS2SERVERS_ARGS"),
            f"ExecStart must end with the exact unbraced variable, got: {exec_start}")

    def test_share_is_writable_under_protectsystem_strict(self):
        # ProtectSystem=strict without ReadWritePaths makes the whole
        # filesystem read-only, and OPL writes its settings to the share. The
        # failure would look like a network problem, not a permissions one.
        unit = _directives("ps2servers@.service")
        self.assertEqual(unit.get("ProtectSystem"), ["strict"])
        self.assertTrue(unit.get("ReadWritePaths"),
                        "the share must be writable or OPL cannot save")

    def test_shipped_share_path_is_actually_writable(self):
        """The two shipped files must agree on the share path.

        ProtectSystem=strict re-opens only what ReadWritePaths lists, so an
        env file pointing --share somewhere the unit does not cover produces a
        share that reads fine and silently cannot be written. Nothing reports
        it -- the server starts and the game list loads.

        This checks only the defaults we ship. A user who moves the share has
        to edit both, which the unit and the README now say in the places they
        would be reading.
        """
        # EVERY absolute path in the args, not just the last one seen. An
        # earlier version overwrote a single variable as it scanned, so a
        # second --share would have gone unchecked -- exactly the mistake this
        # test exists to catch in the config it validates.
        shares = [tok.split("=", 1)[1] for tok in _env_args("ps2servers-smbv1.env")
                  if "=" in tok and tok.split("=", 1)[1].startswith("/")]
        self.assertTrue(shares, "no --share path found in the env file")

        writable = [p.lstrip("-") for p in
                    _directives("ps2servers@.service").get("ReadWritePaths", [])]
        for share in shares:
            covered = any(share == path or share.startswith(path.rstrip("/") + "/")
                          for path in writable)
            self.assertTrue(
                covered,
                f"the env file shares {share} but the unit only makes {writable} "
                "writable; under ProtectSystem=strict that share cannot be written "
                "and OPL's saves would fail silently")

    def test_every_advertised_instance_ships_an_env_file(self):
        """The env file is required, so an advertised instance without one fails.

        ps2servers@.service does not mark EnvironmentFile optional, so
        `systemctl enable --now ps2servers@udpfs` dies before ExecStart with
        "Failed to load environment files" if no sample was ever shipped for
        that key -- and the user has nothing to copy, because the flag set
        appears nowhere else.
        """
        readme = _read("README.md")
        for key in sorted(set(re.findall(r"ps2servers@([a-z0-9]+)", readme))):
            with self.subTest(key=key):
                path = os.path.join(_SYSTEMD, f"ps2servers-{key}.env")
                self.assertTrue(
                    os.path.isfile(path),
                    f"README advertises ps2servers@{key} but "
                    f"ps2servers-{key}.env does not exist; the unit requires it "
                    "and the instance would fail before starting")
                self.assertTrue(
                    _env_args(f"ps2servers-{key}.env"),
                    f"ps2servers-{key}.env sets no PS2SERVERS_ARGS; every server "
                    "here exits immediately without arguments")

    def test_readwritepaths_tolerates_a_missing_directory(self):
        # Without the `-` prefix a non-existent path is fatal at mount-namespace
        # setup (226/NAMESPACE) before ExecStart, and Restart=on-failure loops
        # it. A user serving /mnt/games has no reason to keep an empty /srv/ps2.
        writable = _directives("ps2servers@.service").get("ReadWritePaths", [])
        for path in writable:
            self.assertTrue(
                path.startswith("-"),
                f"ReadWritePaths={path} is fatal if the directory is missing; "
                "prefix it with - to make it tolerated")

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
        for key in ("smbv1", "udpfs", "udpbd"):
            args = _env_args(f"ps2servers-{key}.env")
            self.assertTrue(args, f"no PS2SERVERS_ARGS in ps2servers-{key}.env")

            flags = [tok.split("=", 1)[0] for tok in args if tok.startswith("-")]
            if not flags:
                continue  # udpbd takes a bare positional image path

            help_text = subprocess.run(
                [sys.executable, REGISTRY[key].module_file, "--help"],
                capture_output=True, text=True, timeout=120).stdout

            for flag in flags:
                with self.subTest(server=key, flag=flag):
                    # Word-bounded, not a substring search. `--root` appears
                    # inside `--root-dir`, and a flag named in prose would
                    # satisfy a plain `in` check while argparse rejects it.
                    self.assertRegex(
                        help_text, r"(?<![\w-])" + re.escape(flag) + r"(?![\w-])",
                        f"{flag} is in ps2servers-{key}.env but is not a real "
                        f"flag of the {key} server")


if __name__ == "__main__":
    unittest.main()
