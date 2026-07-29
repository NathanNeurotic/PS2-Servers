"""Edge's packaging must be able to start every server the binary has.

This exists because it once could not. The binary grew an `smb` subcommand and
the OpenWrt init still hardcoded `udpfs`, so serving SMB -- the whole reason a
router user wanted Edge -- meant sshing in to run it by hand, and it did not
survive a reboot. Nothing failed; the capability simply was not reachable.

That class of gap is invisible to every other test in the tree: the Go tests
prove the server works, the packaging tests prove the files ship, and neither
notices that no shipped file ever invokes it.
"""

import os
import re
import subprocess
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_OPENWRT = os.path.join(_ROOT, "packaging", "openwrt", "files")
_SYSTEMD = os.path.join(_ROOT, "packaging", "systemd")
_MAIN_GO = os.path.join(_ROOT, "native", "ps2servers-edge", "cmd",
                        "ps2servers-edge", "main.go")


def _read(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def edge_subcommands():
    """The subcommands main.go actually dispatches, read from the source.

    Derived rather than hardcoded, so adding a fourth server to Edge fails
    these tests until its packaging exists -- which is the point.
    """
    source = _read(_MAIN_GO)
    # Both comparison forms: udpbd and smb dispatch on ==, while udpfs is the
    # fallthrough and is written as `os.Args[1] != "udpfs"`. Matching only ==
    # silently omits it, which would leave the one server that has always
    # shipped unchecked.
    found = set(re.findall(r'os\.Args\[1\] [!=]= "([a-z0-9]+)"', source))
    # --version and version go through the same shape but are not servers.
    return found - {"version", "--version"}


class EveryServerIsLaunchable(unittest.TestCase):
    def test_main_go_dispatches_the_expected_set(self):
        # A guard on the guard: if this changes, the tests below are checking
        # the wrong list and should be looked at rather than silently passing.
        self.assertEqual(edge_subcommands(), {"udpfs", "udpbd", "smb"},
                         "Edge's subcommands changed; update the packaging too")

    def test_openwrt_init_starts_each_one(self):
        init = _read(os.path.join(_OPENWRT, "ps2servers-edge.init"))

        # start_service is procd's entry point, so a start_<cmd> function that
        # nothing calls is exactly the original bug wearing a disguise: the
        # command appears in the file and is never reached. An earlier version
        # of this test checked only that the string was present, and a mutation
        # deleting the call from start_service sailed past it.
        match = re.search(r"^start_service\(\)\s*\{(.*?)^\}", init,
                          re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(match, "no start_service() in the init script")
        body = match.group(1)

        for cmd in sorted(edge_subcommands()):
            with self.subTest(subcommand=cmd):
                self.assertRegex(
                    init, r'procd_set_param command "\$PROG" ' + cmd + r'\b',
                    f"the OpenWrt init never launches `{cmd}`, so the package "
                    "ships a server it cannot start")
                # re.MULTILINE explicitly: assertRegex does not apply it, so a
                # `^` anchor would only ever match the start of the file.
                self.assertIsNotNone(
                    re.search(r"^start_" + cmd + r"\(\)\s*\{", init, re.MULTILINE),
                    f"no start_{cmd}() function")
                self.assertIsNotNone(
                    re.search(r"\bstart_" + cmd + r"\b", body),
                    f"start_service() never calls start_{cmd}, so `{cmd}` is "
                    "defined but unreachable -- the package still cannot start it")

    def test_openwrt_config_has_a_section_per_service(self):
        config = _read(os.path.join(_OPENWRT, "ps2servers-edge.config"))
        sections = set(re.findall(r"^config\s+(\w+)", config, re.MULTILINE))
        for cmd in sorted(edge_subcommands()):
            with self.subTest(subcommand=cmd):
                self.assertIn(cmd, sections,
                              f"no UCI section for {cmd}; it could be started "
                              "but not configured")

    def test_each_instance_is_named(self):
        # procd needs distinct instance names or the later ones replace the
        # earlier: a single unnamed instance would mean enabling smb silently
        # stopped udpfs.
        init = _read(os.path.join(_OPENWRT, "ps2servers-edge.init"))
        names = re.findall(r"procd_open_instance\s+(\w+)", init)
        self.assertEqual(len(names), len(edge_subcommands()),
                         "every instance must be named, or they overwrite each other")
        self.assertEqual(len(names), len(set(names)), f"duplicate instance names: {names}")

    def test_every_instance_runs_unprivileged(self):
        init = _read(os.path.join(_OPENWRT, "ps2servers-edge.init"))
        opens = init.count("procd_open_instance")
        # common_params applies the hardening; each instance must call it, or
        # one of them quietly runs as root.
        self.assertEqual(init.count("common_params"), opens + 1,
                         "an instance is not applying common_params, so it may "
                         "run without the user/limits the others have")

    def test_systemd_template_covers_each_one(self):
        env_files = os.listdir(_SYSTEMD)
        for cmd in sorted(edge_subcommands()):
            with self.subTest(subcommand=cmd):
                self.assertIn(f"ps2servers-edge-{cmd}.env", env_files,
                              f"no env sample for ps2servers-edge@{cmd}; the "
                              "unit requires one and the instance would fail "
                              "before starting")

    def test_systemd_template_splits_its_arguments(self):
        unit = _read(os.path.join(_SYSTEMD, "ps2servers-edge@.service"))
        exec_line = [l for l in unit.splitlines() if l.startswith("ExecStart=")]
        self.assertEqual(len(exec_line), 1)
        # Unbraced: systemd splits $VAR on whitespace and does not split ${VAR}.
        self.assertTrue(exec_line[0].endswith("%i $PS2EDGE_ARGS"),
                        f"ExecStart must end with the exact unbraced variable: {exec_line[0]}")

    def test_readwritepaths_tolerates_a_missing_directory(self):
        unit = _read(os.path.join(_SYSTEMD, "ps2servers-edge@.service"))
        for line in unit.splitlines():
            if line.startswith("ReadWritePaths="):
                value = line.split("=", 1)[1].strip()
                self.assertTrue(value.startswith("-"),
                                f"ReadWritePaths={value} is fatal if the path is "
                                "missing; prefix with - to tolerate it")


class InitScriptIsValidShell(unittest.TestCase):
    def test_posix_sh_parses_it(self):
        init = os.path.join(_OPENWRT, "ps2servers-edge.init")
        for shell in ("sh", "dash", "bash"):
            try:
                result = subprocess.run([shell, "-n", init],
                                        capture_output=True, text=True, timeout=60)
            except (FileNotFoundError, OSError):
                continue
            self.assertEqual(result.returncode, 0,
                             f"{shell} -n rejected the init script:\n{result.stderr}")
            return
        self.skipTest("no POSIX shell available to check syntax")


class StatusIsOfferedByEveryServer(unittest.TestCase):
    """The router status handshake was requested for a board serving SMB.

    It only ever answered on UDPFS, so the one service the request was about
    reported nothing.
    """

    def test_smb_and_udpbd_accept_a_status_port(self):
        source = _read(_MAIN_GO)
        self.assertEqual(source.count('fs.Int("status-port"'), 2,
                         "smb and udpbd should each expose --status-port")

    def test_openwrt_config_exposes_it(self):
        config = _read(os.path.join(_OPENWRT, "ps2servers-edge.config"))
        self.assertGreaterEqual(
            config.count("option status_port"), 2,
            "the smb and udpbd sections should each expose status_port")


if __name__ == "__main__":
    unittest.main()
