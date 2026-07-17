"""Unit tests for the Linux/macOS parity helpers.

Pure parsers and command-builders, exercised off-platform with captured sample
output -- the same approach as the direct-link Unix tests.

Run:  python -m unittest tests.test_linux_parity -v
"""

import unittest
from unittest import mock

from launcher import netinfo, posix_firewall, servers, windows_setup


class FirewallHintTests(unittest.TestCase):
    UDPFS_PORTS = [("UDP", 0xF5F6, "UDPFS discovery")]
    SMB_PORTS = [("TCP", 1111, "SMBv1")]

    def test_no_ports_no_hint(self):
        self.assertEqual(posix_firewall.firewall_hint_lines([]), [])

    def test_generic_hint_when_no_tool_present(self):
        with mock.patch.object(posix_firewall.shutil, "which", return_value=None):
            lines = posix_firewall.firewall_hint_lines(self.SMB_PORTS, probe_active=False)
        text = "\n".join(lines)
        self.assertIn("TCP 1111", text)
        self.assertIn("SMBv1", text)
        # No tool -> no sudo command, but still an actionable line.
        self.assertNotIn("sudo ", text)
        self.assertIn("firewall", text.lower())

    def test_ufw_command_when_ufw_present(self):
        def which(name):
            return "/usr/sbin/ufw" if name == "ufw" else None
        with mock.patch.object(posix_firewall.shutil, "which", side_effect=which):
            lines = posix_firewall.firewall_hint_lines(self.UDPFS_PORTS, probe_active=False)
        text = "\n".join(lines)
        self.assertIn("sudo ufw allow 62966/udp", text)

    def test_firewalld_command_is_copyable_with_note_separate(self):
        def which(name):
            return "/usr/bin/firewall-cmd" if name == "firewall-cmd" else None
        with mock.patch.object(posix_firewall.shutil, "which", side_effect=which):
            lines = posix_firewall.firewall_hint_lines(self.SMB_PORTS, probe_active=False)
        stripped = [ln.strip() for ln in lines]
        # The command line must be exactly the command -- copy-pasteable, no
        # trailing prose that a shell would choke on.
        self.assertIn("sudo firewall-cmd --add-port=1111/tcp", stripped)
        self.assertNotIn("sudo firewall-cmd --add-port=1111/tcp  "
                         "(add --permanent to keep it after a reboot)", stripped)
        # The persistence caveat is still present, just on its own line.
        self.assertTrue(any("--permanent" in ln and "firewall-cmd" in ln
                            for ln in stripped))
        self.assertFalse(any(ln.startswith("sudo") and "--permanent" in ln
                             for ln in stripped))

    def test_active_probe_sharpens_the_lead_line(self):
        def which(name):
            return "/usr/sbin/ufw" if name == "ufw" else None
        with mock.patch.object(posix_firewall.shutil, "which", side_effect=which), \
                mock.patch.object(posix_firewall, "_unit_is_active", return_value=True):
            lines = posix_firewall.firewall_hint_lines(self.SMB_PORTS, probe_active=True)
        self.assertIn("active", lines[0].lower())

    def test_probe_active_false_does_not_call_systemctl(self):
        with mock.patch.object(posix_firewall.shutil, "which", return_value="/x"), \
                mock.patch.object(posix_firewall, "_unit_is_active") as probe:
            posix_firewall.firewall_hint_lines(self.SMB_PORTS, probe_active=False)
            probe.assert_not_called()

    def test_ports_come_from_the_same_source_as_windows_rules(self):
        # The hint must describe exactly the ports the Windows firewall rules
        # would open, or the two drift. Drive it through the shared accessor.
        ports = windows_setup.server_ports("udpfs", {"port": "0xF5F6"})
        lines = posix_firewall.firewall_hint_lines(ports, probe_active=False)
        self.assertTrue(any("62966" in ln for ln in lines))


class LinuxIpParseTests(unittest.TestCase):
    IP_ADDR = (
        "1: lo    inet 127.0.0.1/8 scope host lo\\       valid_lft forever\n"
        "2: enp3s0    inet 192.168.1.50/24 brd 192.168.1.255 scope global enp3s0\n"
        "3: docker0    inet 172.17.0.1/16 brd 172.17.255.255 scope global docker0\n"
        "4: virbr0    inet 192.168.122.1/24 brd ... scope global virbr0\n"
        "5: tun0    inet 10.8.0.2/24 scope global tun0\n"
        "6: wlan0    inet 192.168.1.51/24 brd ... scope global wlan0\n"
    )

    def test_keeps_real_nics_drops_virtual(self):
        ips = netinfo._parse_linux_ip_addr(self.IP_ADDR)
        self.assertIn("192.168.1.50", ips)   # ethernet
        self.assertIn("192.168.1.51", ips)   # wifi
        self.assertNotIn("127.0.0.1", ips)   # lo
        self.assertNotIn("172.17.0.1", ips)  # docker
        self.assertNotIn("192.168.122.1", ips)  # virbr (libvirt)
        self.assertNotIn("10.8.0.2", ips)    # tun (VPN)

    def test_empty_and_garbage(self):
        self.assertEqual(netinfo._parse_linux_ip_addr(""), [])
        self.assertEqual(netinfo._parse_linux_ip_addr("nonsense line here"), [])


class MacosIfconfigParseTests(unittest.TestCase):
    IFCONFIG = (
        "lo0: flags=8049<UP,LOOPBACK,RUNNING,MULTICAST> mtu 16384\n"
        "\tinet 127.0.0.1 netmask 0xff000000\n"
        "en0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500\n"
        "\tinet 192.168.1.20 netmask 0xffffff00 broadcast 192.168.1.255\n"
        "\tstatus: active\n"
        "utun3: flags=8051<UP,POINTOPOINT,RUNNING,MULTICAST> mtu 1380\n"
        "\tinet 10.9.0.4 --> 10.9.0.4 netmask 0xffffffff\n"
        "bridge100: flags=8863 mtu 1500\n"
        "\tinet 192.168.64.1 netmask 0xffffff00 broadcast 192.168.64.255\n"
    )

    def test_keeps_en0_drops_loopback_vpn_bridge(self):
        ips = netinfo._parse_macos_ifconfig(self.IFCONFIG)
        self.assertEqual(ips, ["192.168.1.20"])


class WindowsOnlyFieldTests(unittest.TestCase):
    def test_take_445_is_flagged_windows_only(self):
        smb = servers.REGISTRY["smbv1"]
        f = next(f for f in smb.fields if f.key == "take_445")
        self.assertTrue(f.windows_only)

    def test_no_other_field_is_windows_only(self):
        # Guard against accidentally hiding a cross-platform field.
        for server in servers.REGISTRY.values():
            for f in server.fields:
                if f.windows_only:
                    self.assertEqual((server.key, f.key), ("smbv1", "take_445"))


if __name__ == "__main__":
    unittest.main()
