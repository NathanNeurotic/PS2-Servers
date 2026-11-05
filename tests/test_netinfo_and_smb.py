"""Tests for netinfo IP helpers, SMB mode registration, and port settings."""

import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from launcher import gui, netinfo, servers, windows_setup


class NetInfoTests(unittest.TestCase):
    def test_ip_choices_contains_custom_option(self):
        choices = netinfo.ip_choices()
        self.assertIn("Custom IP...", choices)
        self.assertEqual(choices[-1], "Custom IP...")

    def test_detailed_ip_info_formatting(self):
        info = netinfo.detailed_ip_info()
        self.assertIn("[network] Current IP Addresses and Network Adapters:", info)
        self.assertIn("Custom IP...", info)


def _probe_values(server):
    """Enough values to build any server's command line.

    Derived from the server's own fields rather than hard-coded, so a mode that
    grows a new required field is still covered instead of raising here.
    """
    values = {}
    for f in server.fields:
        if f.kind in ("folder", "file"):
            values[f.key] = "C:/Games"
        elif f.kind == "port":
            values[f.key] = f.default or 1025
        elif f.kind == "bool":
            values[f.key] = bool(f.default)
        elif f.kind == "choice":
            values[f.key] = f.default or (f.choices[0][0] if f.choices else "")
        elif f.default:
            values[f.key] = f.default
    if server.key == "udpbd":
        values["virtual_folder"] = ""  # probe the image mode, not two targets
    return values


def _load_server_module(server):
    """Import a REGISTRY server's entry point the way the launcher does.

    By file path with its own directory on sys.path, because that is how
    launcher/serve.py loads it -- importing it any other way would test a module
    that resolves its siblings differently from the one users run.
    """
    import importlib.util
    if server.module_dir and server.module_dir not in sys.path:
        sys.path.insert(0, server.module_dir)
    spec = importlib.util.spec_from_file_location(
        "_probe_" + server.key, server.module_file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _smb_dialect_of(server):
    """The dialect a REGISTRY entry asks the server for, or None if it is not SMB."""
    argv = server._build_argv(_probe_values(server))
    if "--smb-version" not in argv:
        return None
    return int(argv[argv.index("--smb-version") + 1])


class ServerRegistryTests(unittest.TestCase):
    def test_smb_server_exists_and_defaults_to_1025(self):
        self.assertIn("smbv1", servers.REGISTRY)
        server = servers.REGISTRY["smbv1"]
        self.assertEqual(server.default_port, 1025)
        port_field = next(f for f in server.fields if f.key == "port")
        self.assertFalse(port_field.advanced, "smbv1 port field should not be advanced hidden")
        self.assertEqual(port_field.default, 1025)

    def test_smb_argv_version_flag(self):
        self.assertEqual(_smb_dialect_of(servers.REGISTRY["smbv1"]), 1)

    def test_no_offered_mode_asks_for_a_dialect_that_cannot_serve_a_file(self):
        """A mode in the REGISTRY is a mode a user can pick and expect to work.

        SMB2/SMB3 currently negotiate, authenticate and accept a tree connect,
        then serve nothing -- READ returns zero bytes and QUERY_DIRECTORY is not
        implemented, so the share cannot even be listed. Offering that meant a
        user connected successfully and saw an empty folder with no error.

        This is not "SMB2 is banned". It is the ordering: the server has to be
        able to serve a file before the launcher offers the mode. The check is
        against whichever server the mode actually runs, so it keeps meaning
        something when a mode is repointed at a different implementation.
        """
        SMB2_QUERY_DIRECTORY = 0x000E

        for key, server in servers.REGISTRY.items():
            dialect = _smb_dialect_of(server)
            if dialect is None or dialect < 2:
                continue
            module = _load_server_module(server)
            handlers = (getattr(module, "SMB2_HANDLERS", None)
                        or getattr(module, "_HANDLERS", None) or {})
            self.assertIn(
                SMB2_QUERY_DIRECTORY, handlers,
                "{} offers SMB{} via {}, which implements no QUERY_DIRECTORY, so "
                "the share cannot be listed. Finish the handlers before offering "
                "the mode.".format(key, dialect,
                                   os.path.basename(server.module_file or "?")))

    def test_take_445_reaches_every_smb_mode_that_offers_it(self):
        """A checkbox the server never hears about is a control that does
        nothing -- the exact defect the SMBv2/SMBv3 modes were pulled for."""
        for key in ("smbv1", "smbv2", "smbv3"):
            with self.subTest(key=key):
                server = servers.REGISTRY[key]
                self.assertTrue(any(f.key == "take_445" for f in server.fields))
                values = _probe_values(server)
                values["take_445"] = True
                self.assertIn("--take-445", server._build_argv(values))

    def test_the_headless_error_lists_exactly_what_is_registered(self):
        """The `serve` error text and the REGISTRY drifted apart twice."""
        import ps2servers
        import io
        import contextlib
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            ps2servers._normalize_headless_alias(["serve"])
        message = err.getvalue()
        for key in servers.REGISTRY:
            self.assertIn(key, message, "%s is registered but not offered" % key)

    def test_windows_setup_ports_for_smb(self):
        for key in ("smbv1", "smbv2", "smbv3"):
            rules = windows_setup._server_ports(key, {"port": "1025"})
            self.assertEqual(rules[0][1], 1025)

    def test_opl_hint_smb_versions(self):
        for key in ("smbv1", "smbv2", "smbv3"):
            hint = gui.opl_hint(key, "192.168.1.100", {"port": 1025, "share_name": "games", "username": "", "password": ""})
            self.assertIn("192.168.1.100", hint)
            self.assertIn("1025", hint)
            self.assertIn("Port 1025", hint)
            self.assertIn("User 'guest'", hint)

            # Omitted/empty port falls back to the server's own default:
            # 1025 for SMBv1, 1445 for SMBv2/SMBv3.
            default = "1025" if key == "smbv1" else "1445"
            hint_fallback = gui.opl_hint(key, "192.168.1.100", {"share_name": "games", "username": "", "password": ""})
            self.assertIn("Port " + default, hint_fallback)
            self.assertIn("URL = smb://192.168.1.100:" + default + "/games", hint_fallback)

    def test_console_config_block_smb_cards(self):
        # The paste-ready Launchstation/wLaunchELF block uses the card's own
        # values and the dialect marker each card serves.
        block = gui.console_config_block("smbv2", "192.168.1.100",
                                         {"port": 1025, "share_name": "games",
                                          "username": "player", "password": "secret"})
        self.assertEqual(block,
                         "NAME = PC\n"
                         "USERNAME = player\n"
                         "PASSWORD = secret\n"
                         "URL = smb://192.168.1.100:1025/games")

        # No user (No-password mode): the client's Guest with a blank password
        # is what the server accepts.
        block = gui.console_config_block("smbv2", "192.168.1.100",
                                         {"port": 1025, "share_name": "games",
                                          "username": "", "password": ""})
        self.assertIn("USERNAME = guest\nPASSWORD =\n", block)

        # No-password mode wins over a stale user field.
        block = gui.console_config_block("smbv2", "192.168.1.100",
                                         {"port": 1025, "open_share": True,
                                          "username": "ps2", "password": "x"})
        self.assertIn("USERNAME = guest\nPASSWORD =\n", block)

        # SMBv3 card is marked VERSION = 3; SMBv1 VERSION = 1.
        block = gui.console_config_block("smbv3", "192.168.1.100",
                                         {"port": 1025, "username": "p", "password": "s"})
        self.assertIn("VERSION = 3\n", block)
        block = gui.console_config_block("smbv1", "192.168.1.100",
                                         {"port": 1025, "username": "", "password": ""})
        self.assertIn("VERSION = 1\n", block)
        self.assertNotIn("USERNAME", block)

        # take-445 serves on 445 no matter what the port field holds.
        block = gui.console_config_block("smbv2", "192.168.1.100",
                                         {"port": 1025, "take_445": True,
                                          "username": "p", "password": "s"})
        self.assertIn("URL = smb://192.168.1.100:445/", block)

        # Non-SMB cards have no console config to copy.
        self.assertEqual(gui.console_config_block("udpfs", "192.168.1.100", {}), "")

    def test_open_share_without_bind_warns_about_every_interface(self):
        # No password plus the default bind (every interface) is the most
        # exposed combination: the hint says so and points at Bind address.
        hint = gui.opl_hint("smbv2", "192.168.1.100",
                            {"port": 1445, "open_share": True})
        self.assertIn("every interface", hint)
        self.assertIn("Bind address", hint)

        # A wildcard bind is still every interface: the warning stays.
        hint = gui.opl_hint("smbv2", "192.168.1.100",
                            {"port": 1445, "open_share": True,
                             "bind": "0.0.0.0"})
        self.assertIn("every interface", hint)

        # A Bind address set, or a password, silences it.
        hint = gui.opl_hint("smbv2", "192.168.1.100",
                            {"port": 1445, "open_share": True,
                             "bind": "192.168.1.100"})
        self.assertNotIn("every interface", hint)
        hint = gui.opl_hint("smbv2", "192.168.1.100",
                            {"port": 1445, "username": "p", "password": "s"})
        self.assertNotIn("every interface", hint)

    def test_opl_hint_udpfs_names_no_server_ip(self):
        # OPL finds UDPFS by broadcast and never asks for a server IP; the hint
        # must not tell the user to enter one.
        hint = gui.opl_hint("udpfs", "192.168.1.7", {"bind": ""})
        self.assertNotIn("if prompted", hint)
        self.assertIn("no server IP", hint)
        self.assertIn("same network as 192.168.1.7", hint)
        # A bind address is what the server answers from, so it wins over the LAN IP.
        hint = gui.opl_hint("udpfs", "192.168.1.7", {"bind": "192.168.0.4"})
        self.assertIn("same network as 192.168.0.4", hint)
        self.assertNotIn("192.168.1.7", hint)
        # udpfsd's IP:PORT grammar: the port is not part of the network, and a
        # wildcard or bare :PORT names no network, so the LAN IP stands in.
        hint = gui.opl_hint("udpfs", "192.168.1.7", {"bind": "192.168.0.4:41233"})
        self.assertIn("same network as 192.168.0.4", hint)
        self.assertNotIn("41233", hint)
        for bind in (":41233", "0.0.0.0:62966", "0.0.0.0"):
            hint = gui.opl_hint("udpfs", "192.168.1.7", {"bind": bind})
            self.assertIn("same network as 192.168.1.7", hint, bind)

if __name__ == "__main__":
    unittest.main()
