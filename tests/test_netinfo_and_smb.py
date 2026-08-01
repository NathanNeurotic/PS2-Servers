"""Tests for netinfo IP helpers, SMB mode registration, and port settings."""

import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from launcher import netinfo, servers, windows_setup


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
    return values


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
        able to serve a file before the launcher offers the mode. Implement
        QUERY_DIRECTORY and a real READ, and this test goes green on its own --
        no edit here, nothing to remember.
        """
        sys.path.insert(0, os.path.join(_ROOT, "smbv1_server"))
        try:
            import smbserver_opl
        finally:
            sys.path.pop(0)

        SMB2_QUERY_DIRECTORY = 0x000E
        can_list = SMB2_QUERY_DIRECTORY in smbserver_opl.SMB2_HANDLERS

        for key, server in servers.REGISTRY.items():
            dialect = _smb_dialect_of(server)
            if dialect is None or dialect < 2:
                continue
            self.assertTrue(
                can_list,
                "{} offers SMB{}, but the server implements no QUERY_DIRECTORY, "
                "so the share cannot be listed. Finish the SMB2 handlers before "
                "putting the mode back in the REGISTRY.".format(key, dialect))

    def test_windows_setup_ports_for_smb(self):
        # The SMBv2/SMBv3 keys stay mapped here even though no mode offers them:
        # the firewall helper is keyed by name, and it is what the modes will
        # need again once the server can serve them.
        for key in ("smbv1", "smbv2", "smbv3"):
            rules = windows_setup._server_ports(key, {"port": "1025"})
            self.assertEqual(rules[0][1], 1025)


if __name__ == "__main__":
    unittest.main()
