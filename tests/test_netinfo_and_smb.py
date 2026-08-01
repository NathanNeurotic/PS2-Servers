"""Tests for netinfo IP helpers, SMB2/3 registration, and port settings."""

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


class ServerRegistryTests(unittest.TestCase):
    def test_smb_servers_exist_and_default_to_1025(self):
        for key in ("smbv1", "smbv2", "smbv3"):
            self.assertIn(key, servers.REGISTRY)
            server = servers.REGISTRY[key]
            self.assertEqual(server.default_port, 1025)
            port_field = next(f for f in server.fields if f.key == "port")
            self.assertFalse(port_field.advanced, f"{key} port field should not be advanced hidden")
            self.assertEqual(port_field.default, 1025)

    def test_smb_argv_version_flags(self):
        v = {"games_folder": "C:/Games", "port": 1025}
        argv1 = servers.REGISTRY["smbv1"]._build_argv(v)
        argv2 = servers.REGISTRY["smbv2"]._build_argv(v)
        argv3 = servers.REGISTRY["smbv3"]._build_argv(v)
        self.assertIn("--smb-version", argv1)
        self.assertEqual(argv1[argv1.index("--smb-version") + 1], "1")
        self.assertIn("--smb-version", argv2)
        self.assertEqual(argv2[argv2.index("--smb-version") + 1], "2")
        self.assertIn("--smb-version", argv3)
        self.assertEqual(argv3[argv3.index("--smb-version") + 1], "3")

    def test_windows_setup_ports_for_smb(self):
        for key in ("smbv1", "smbv2", "smbv3"):
            rules = windows_setup._server_ports(key, {"port": "1025"})
            self.assertEqual(rules[0][1], 1025)

    def test_opl_hint_smb_versions(self):
        for key in ("smbv1", "smbv2", "smbv3"):
            hint = gui.opl_hint(key, "192.168.1.100", {"port": 1025})
            self.assertIn("192.168.1.100", hint)
            self.assertIn("1025", hint)
            self.assertIn("Port 1025", hint)
            self.assertIn("User 'guest'", hint)


if __name__ == "__main__":
    unittest.main()
