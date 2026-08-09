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
        self.assertEqual(edge_subcommands(), {"udpfs", "udpbd", "smb", "webui"},
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

    def test_every_instance_applies_the_shared_hardening(self):
        init = _read(os.path.join(_OPENWRT, "ps2servers-edge.init"))
        opens = init.count("procd_open_instance")
        # common_params applies the user, the respawn policy and the fd limit.
        # Each instance must call it, or one of them quietly runs without them.
        self.assertEqual(init.count("common_params"), opens + 1,
                         "an instance is not applying common_params, so it may "
                         "run without the user/limits the others have")

    def test_only_the_web_ui_can_run_as_root_and_does_not_by_default(self):
        """One instance may differ, by explicit opt-in, defaulting to off.

        Save and Restart genuinely need root on this platform -- `uci commit`
        rewrites /etc/config and restarting goes through /etc/init.d -- so the
        dashboard is read-only unless the operator says otherwise. What must
        not happen is that becoming the default, or spreading to a server that
        has no reason to be root.
        """
        init = _read(os.path.join(_OPENWRT, "ps2servers-edge.init"))
        config = _read(os.path.join(_OPENWRT, "ps2servers-edge.config"))

        self.assertRegex(
            config, r"option\s+run_as_root\s+'0'",
            "run_as_root must ship as 0; a network-facing dashboard running as "
            "root should never be what you get by not choosing")

        # Exactly one instance passes a user to common_params, and it is webui.
        # [ \t] rather than \s: \s matches the newline, so the bare calls would
        # each "match" with the next line's first word as their argument.
        with_arg = re.findall(r"common_params[ \t]+(\S+)", init)
        self.assertEqual(
            len(with_arg), 1,
            "more than one instance overrides the shared user: %s" % with_arg)
        webui_body = re.search(r"^start_webui\(\)\s*\{(.*?)^\}", init,
                               re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(webui_body, "no start_webui() in the init script")
        self.assertIn("common_params \"$webui_user\"", webui_body.group(1),
                      "the webui instance no longer selects its own user")

        # And no server selects a user at all -- neither by passing one to
        # common_params nor by setting it directly. Matched precisely rather
        # than by searching for "root": start_udpfs legitimately contains
        # `config_get root main root`, which is the share, not the account.
        for func in ("start_udpfs", "start_smb", "start_udpbd"):
            body = re.search(r"^" + func + r"\(\)\s*\{(.*?)^\}", init,
                             re.MULTILINE | re.DOTALL)
            self.assertIsNotNone(body, f"no {func}() in the init script")
            with self.subTest(function=func):
                self.assertNotRegex(
                    body.group(1), r"common_params[ \t]+\S",
                    f"{func} passes a user to common_params; only the web UI "
                    "has a reason to differ")
                self.assertNotRegex(
                    body.group(1), r"procd_set_param[ \t]+user\b",
                    f"{func} sets its own user, bypassing common_params")

    def test_the_web_ui_password_does_not_go_on_a_command_line(self):
        """argv is world-readable in /proc and in `ps` output."""
        init = _read(os.path.join(_OPENWRT, "ps2servers-edge.init"))
        self.assertNotRegex(
            init, r"procd_append_param\s+command\s+--auth-pass",
            "the password is passed as an argument, so every local account can "
            "read it out of /proc; pass WEBUI_PASS through procd_set_param env")
        self.assertIn("procd_set_param env WEBUI_PASS=", init,
                      "the password should reach the binary through the "
                      "environment, which runWebUI already reads")

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


class NewSectionOptionsMapToRealFlags(unittest.TestCase):
    """Every smb/udpbd UCI option must correspond to a flag its server accepts.

    The same check test_edge_flag_exposure applies to udpfs. Without it an
    option can be shipped, documented and set, and do nothing -- which is
    exactly the bug that once made udpfs's `read_only` decorative.

    Flags are read from main.go rather than by running the binary, so this
    needs no Go toolchain and still fails when a flag is renamed.
    """

    @staticmethod
    def _flags_of(func_name):
        source = _read(_MAIN_GO)
        start = source.find(f"func {func_name}(")
        if start < 0:
            return set()
        # To the next top-level func, which is where this one ends.
        end = source.find("\nfunc ", start + 1)
        body = source[start:end if end > 0 else len(source)]
        # Two shapes: fs.String("name", ...) puts the name first, while
        # fs.Var(&value, "name", ...) puts the value first. Matching only the
        # first misses every repeatable flag -- --share among them, which is
        # the one option smb cannot run without.
        return set(re.findall(r'fs\.\w+\(\s*(?:&\w+,\s*)?"([a-z0-9-]+)"', body))

    @staticmethod
    def _section_options(config, section_name):
        options, inside = set(), False
        for line in config.splitlines():
            if line.startswith("config "):
                inside = line.rstrip().endswith(f"'{section_name}'")
                continue
            if inside:
                m = re.match(r"\s*option\s+([a-z0-9_]+)\s+'", line)
                if m:
                    options.add(m.group(1))
        return options

    def test_smb_and_udpbd_options_all_reach_a_flag(self):
        config = _read(os.path.join(_OPENWRT, "ps2servers-edge.config"))
        init = _read(os.path.join(_OPENWRT, "ps2servers-edge.init"))

        for section, func in (("smb", "runSMB"), ("udpbd", "runUDPBD")):
            flags = self._flags_of(func)
            self.assertTrue(flags, f"no flags parsed out of {func}")
            for option in sorted(self._section_options(config, section)):
                if option == "enabled":
                    continue  # consumed by the init script, not the binary
                with self.subTest(section=section, option=option):
                    flag = option.replace("_", "-")
                    self.assertIn(
                        flag, flags,
                        f"{section}.{option} does not map to a --{flag} flag on "
                        f"{func}, so setting it does nothing")
                    # And the init must actually read it, or it is decorative
                    # even though the flag exists.
                    #
                    # Two spellings count, as in test_edge_flag_exposure:
                    # scalars go through config_get, booleans through the
                    # append_bool_flag helper. Matching only the first reports
                    # every boolean as unread, which is the opposite of true.
                    direct = (r"config_get(?:_bool)?\s+\w+\s+" + section +
                              r"\s+" + re.escape(option) + r"\b")
                    helper = (r"append_bool_flag\s+" + section + r"\s+" +
                              re.escape(option) + r"\b")
                    self.assertTrue(
                        re.search(direct, init) or re.search(helper, init),
                        f"the init never reads {section}.{option}, so setting "
                        "it would be decorative")


class ShippedStatusPortsDoNotRace(unittest.TestCase):
    """Only one service may claim the shared status socket by default.

    -1 resolves to UDPFS's discovery port in every subcommand, and bind
    failures are deliberately non-fatal, so shipping -1 for several services
    would make whichever started first the one that answers -- a launcher would
    get a different service's status across a reboot, for no visible reason.
    """

    def test_openwrt_ships_at_most_one_claimant(self):
        config = _read(os.path.join(_OPENWRT, "ps2servers-edge.config"))
        claiming = re.findall(r"^\s*option\s+status_port\s+'(-?\d+)'",
                              config, re.MULTILINE)
        self.assertLessEqual(
            [c for c in claiming].count("-1"), 1,
            "more than one shipped section claims the standard status port; "
            "which one answers would depend on procd start order")

    def test_systemd_env_files_ship_no_claimant(self):
        # udpfs owns the port implicitly by serving discovery on it, so no
        # Edge env file should claim it by default.
        # Go's flag package accepts -flag=v, --flag=v, -flag v and --flag v, so
        # a literal "--status-port -1" check would pass on three spellings that
        # claim the port just as effectively.
        claims = re.compile(r"--?status-port[=\s]+-1\b")
        for name in ("smb", "udpbd"):
            path = os.path.join(_SYSTEMD, f"ps2servers-edge-{name}.env")
            args = [line for line in _read(path).splitlines()
                    if line.startswith("PS2EDGE_ARGS=")]
            self.assertEqual(len(args), 1)
            self.assertNotRegex(
                args[0], claims,
                f"ps2servers-edge-{name}.env claims the shared status port by "
                "default, which races ps2servers-edge@udpfs")


class ShippedPortsMatchTheBinary(unittest.TestCase):
    """Packaging may not ship a port the binary would not have chosen itself.

    ps2servers-edge-udpbd.env shipped `--port 48301` for several releases. The
    PS2's udpbd client broadcasts to 0xBDBD (48573) and cannot be told to use
    anything else, so that unit served a port no console would ever reach --
    and udpbd has no "nobody answered" error path, so both ends stayed silent
    and it presented as a cabling or firewall fault.

    The existing flag check (tests/test_systemd_units.py) only proves a flag
    *exists*; it never looks at the value, which is why this went through. Read
    from the Go source rather than by running the binary, so no toolchain is
    needed and a renamed constant still fails.
    """

    #: subcommand -> (file, regex capturing the default as a Go int literal)
    _DEFAULTS = {
        "udpbd": (os.path.join("internal", "udpbd", "protocol.go"),
                  r"\bPort\s*=\s*(0[xX][0-9a-fA-F]+|\d+)"),
        "smb": (os.path.join("internal", "smb", "wire.go"),
                r"\bDefaultPort\s*=\s*(0[xX][0-9a-fA-F]+|\d+)"),
    }

    @classmethod
    def _binary_default(cls, subcommand):
        if subcommand == "udpfs":
            # Spelled inline in main.go rather than as a named constant.
            m = re.search(r'envInt\("PORT",\s*(0[xX][0-9a-fA-F]+|\d+)\)',
                          _read(_MAIN_GO))
        else:
            rel, pattern = cls._DEFAULTS[subcommand]
            path = os.path.join(_ROOT, "native", "ps2servers-edge", rel)
            m = re.search(pattern, _read(path))
        assert m, f"could not read the default port for {subcommand}"
        return int(m.group(1), 0)

    def test_systemd_env_files_ship_the_binary_default_port(self):
        # Go's flag package accepts -flag=v, --flag=v, -flag v and --flag v.
        port_arg = re.compile(r"--?port[=\s]+(\d+)\b")
        for name in ("smb", "udpbd"):
            expected = self._binary_default(name)
            args = [l for l in _read(os.path.join(_SYSTEMD, f"ps2servers-edge-{name}.env")).splitlines()
                    if l.startswith("PS2EDGE_ARGS=")]
            self.assertEqual(len(args), 1)
            found = port_arg.search(args[0])
            if found is None:
                continue  # omitting it is fine; the binary's own default applies
            with self.subTest(service=name):
                self.assertEqual(
                    int(found.group(1)), expected,
                    f"ps2servers-edge-{name}.env pins --port {found.group(1)} "
                    f"but the binary defaults to {expected}; a shipped override "
                    "must be deliberate, and for udpbd the console cannot "
                    "follow it anywhere")

    def test_openwrt_init_falls_back_to_the_binary_default(self):
        # The fallback is what a section created without an explicit `port`
        # gets -- including one the web UI writes.
        init = _read(os.path.join(_OPENWRT, "ps2servers-edge.init"))
        for section, sub in (("main", "udpfs"), ("smb", "smb"), ("udpbd", "udpbd")):
            m = re.search(r"config_get\s+port\s+" + section + r"\s+port\s+(\d+)", init)
            with self.subTest(section=section):
                self.assertIsNotNone(m, f"no port fallback for the {section} section")
                self.assertEqual(
                    int(m.group(1)), self._binary_default(sub),
                    f"the init falls back to port {m.group(1)} for {section}, "
                    f"but the binary defaults to {self._binary_default(sub)}")

    def test_openwrt_config_ships_the_binary_default_port(self):
        config = _read(os.path.join(_OPENWRT, "ps2servers-edge.config"))
        for section, sub in (("main", "udpfs"), ("smb", "smb"), ("udpbd", "udpbd")):
            body = re.search(r"^config\s+\w+\s+'" + section + r"'(.*?)(?=^config\s|\Z)",
                             config, re.MULTILINE | re.DOTALL)
            self.assertIsNotNone(body, f"no '{section}' section in the UCI config")
            m = re.search(r"^\s*option\s+port\s+'(\d+)'", body.group(1), re.MULTILINE)
            with self.subTest(section=section):
                self.assertIsNotNone(m, f"the {section} section ships no port")
                self.assertEqual(
                    int(m.group(1)), self._binary_default(sub),
                    f"the {section} section ships port {m.group(1)}, but the "
                    f"binary defaults to {self._binary_default(sub)}")


class InitScriptIsValidShell(unittest.TestCase):
    def test_posix_sh_parses_it(self):
        if sys.platform == "win32":
            self.skipTest("POSIX shell syntax check skipped on Windows platform")
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
