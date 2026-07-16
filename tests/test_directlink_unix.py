"""Unit tests for the cross-platform (Linux/macOS) direct-link pieces.

These exercise the pure parsers and command builders with captured sample
output, so the Linux/macOS logic is verified without a Linux/macOS host.

Run:  python -m unittest tests.test_directlink_unix -v
"""

import os
import unittest

from launcher import directlink, elevate


LINUX_ADDR = r"""
[
 {"ifindex":1,"ifname":"lo","flags":["LOOPBACK","UP","LOWER_UP"],
  "addr_info":[{"family":"inet","local":"127.0.0.1","prefixlen":8}]},
 {"ifindex":2,"ifname":"enp3s0","flags":["BROADCAST","MULTICAST","UP","LOWER_UP"],
  "addr_info":[{"family":"inet","local":"192.168.1.50","prefixlen":24,"dynamic":true}]},
 {"ifindex":3,"ifname":"eth1","flags":["BROADCAST","MULTICAST","UP","LOWER_UP"],
  "addr_info":[{"family":"inet","local":"169.254.5.5","prefixlen":16}]},
 {"ifindex":4,"ifname":"wlan0","flags":["BROADCAST","MULTICAST","UP","LOWER_UP"],
  "addr_info":[]},
 {"ifindex":5,"ifname":"eth2","flags":["BROADCAST","MULTICAST"],"addr_info":[]}
]
"""

LINUX_ROUTE = r"""
[
 {"dst":"default","gateway":"192.168.1.1","dev":"enp3s0"},
 {"dst":"192.168.1.0/24","dev":"enp3s0"},
 {"dst":"169.254.0.0/16","dev":"eth1"}
]
"""


def _linux_parsed():
    def wireless(name):
        return name == "wlan0"

    def physical(_name):
        return True

    return directlink._parse_linux_adapters(LINUX_ADDR, LINUX_ROUTE,
                                            wireless, physical)


class LinuxEnumTests(unittest.TestCase):
    def test_loopback_dropped_and_names(self):
        names = [a["name"] for a in _linux_parsed()["adapters"]]
        self.assertEqual(names, ["enp3s0", "eth1", "wlan0", "eth2"])

    def test_gateway_and_dhcp_flagged(self):
        by = {a["name"]: a for a in _linux_parsed()["adapters"]}
        self.assertTrue(by["enp3s0"]["has_gateway"])
        self.assertEqual(by["enp3s0"]["ipv4"][0]["origin"], "dhcp")
        self.assertFalse(by["eth1"]["has_gateway"])
        self.assertEqual(by["eth1"]["ipv4"][0]["origin"], "manual")

    def test_status_and_media(self):
        by = {a["name"]: a for a in _linux_parsed()["adapters"]}
        self.assertEqual(by["eth1"]["status"], "up")
        self.assertEqual(by["eth2"]["status"], "down")   # no LOWER_UP
        self.assertEqual(by["wlan0"]["media"], "802.11")
        self.assertEqual(by["eth1"]["media"], "802.3")
        self.assertEqual(by["eth1"]["id"], "eth1")

    def test_routes_normalized(self):
        routes = _linux_parsed()["routes"]
        prefixes = {r["prefix"]: r["if_id"] for r in routes}
        self.assertEqual(prefixes.get("192.168.1.0/24"), "enp3s0")
        self.assertEqual(prefixes.get("169.254.0.0/16"), "eth1")
        self.assertNotIn("default", prefixes)  # default is not a usable subnet

    def test_only_direct_link_port_is_a_candidate(self):
        # enp3s0 = real network (gateway+dhcp), wlan0 = wireless, eth2 = down.
        candidates, _rej = directlink.find_candidates(_linux_parsed())
        self.assertEqual([a["name"] for a in candidates], ["eth1"])


MAC_PORTS = """Hardware Port: Wi-Fi
Device: en0
Ethernet Address: aa:bb:cc:dd:ee:ff

Hardware Port: USB 10/100/1000 LAN
Device: en5
Ethernet Address: 11:22:33:44:55:66

Hardware Port: Thunderbolt Bridge
Device: bridge0
Ethernet Address: (null)
"""

MAC_IFCONFIG = {
    "en0": ("en0: flags=8863<UP,BROADCAST,SMART,RUNNING> mtu 1500\n"
            "\tinet 192.168.1.20 netmask 0xffffff00 broadcast 192.168.1.255\n"
            "\tstatus: active\n"),
    "en5": ("en5: flags=8863<UP,BROADCAST,SMART,RUNNING> mtu 1500\n"
            "\tinet 169.254.10.20 netmask 0xffff0000\n"
            "\tstatus: active\n"),
    "bridge0": ("bridge0: flags=8863<UP,BROADCAST> mtu 1500\n"
                "\tstatus: inactive\n"),
}


def _macos_parsed():
    return directlink._parse_macos_adapters(MAC_PORTS, MAC_IFCONFIG, "en0")


class MacosEnumTests(unittest.TestCase):
    def test_hardware_ports_paired(self):
        pairs = directlink._parse_macos_hardware_ports(MAC_PORTS)
        self.assertEqual(pairs, [("Wi-Fi", "en0"),
                                 ("USB 10/100/1000 LAN", "en5"),
                                 ("Thunderbolt Bridge", "bridge0")])

    def test_hex_netmask_to_prefix(self):
        self.assertEqual(directlink._macos_hex_mask_to_prefix("0xffffff00"), 24)
        self.assertEqual(directlink._macos_hex_mask_to_prefix("0xffff0000"), 16)
        self.assertEqual(directlink._macos_hex_mask_to_prefix("255.255.255.0"), 24)

    def test_ifconfig_parse(self):
        status, ipv4 = directlink._parse_macos_ifconfig(MAC_IFCONFIG["en5"])
        self.assertEqual(status, "up")
        self.assertEqual(ipv4, [{"ip": "169.254.10.20", "prefix": 16,
                                 "origin": "manual"}])

    def test_classification(self):
        by = {a["name"]: a for a in _macos_parsed()["adapters"]}
        self.assertEqual(by["Wi-Fi"]["media"], "802.11")
        self.assertTrue(by["Wi-Fi"]["has_gateway"])          # default dev
        self.assertEqual(by["USB 10/100/1000 LAN"]["id"], "en5")
        self.assertFalse(by["USB 10/100/1000 LAN"]["has_gateway"])
        self.assertFalse(by["Thunderbolt Bridge"]["physical"])

    def test_only_wired_non_gateway_is_candidate(self):
        candidates, _rej = directlink.find_candidates(_macos_parsed())
        self.assertEqual([a["id"] for a in candidates], ["en5"])


class LocalIpCommandTests(unittest.TestCase):
    def test_linux_add_brings_link_up_then_adds_cidr(self):
        cmds = directlink.local_ip_commands("eth0", "192.168.137.1", 24,
                                            os_name="Linux")
        self.assertEqual(cmds, [["ip", "link", "set", "dev", "eth0", "up"],
                                ["ip", "addr", "add", "192.168.137.1/24",
                                 "dev", "eth0"]])

    def test_linux_teardown_is_inverse(self):
        self.assertEqual(
            directlink.local_ip_commands("eth0", "192.168.137.1", 24,
                                         teardown=True, os_name="Linux"),
            [["ip", "addr", "del", "192.168.137.1/24", "dev", "eth0"]])

    def test_macos_alias_and_unalias(self):
        self.assertEqual(
            directlink.local_ip_commands("en5", "192.168.137.1", 24,
                                         os_name="Darwin"),
            [["ifconfig", "en5", "inet", "192.168.137.1", "netmask",
              "255.255.255.0", "alias"]])
        self.assertEqual(
            directlink.local_ip_commands("en5", "192.168.137.1", 24,
                                         teardown=True, os_name="Darwin"),
            [["ifconfig", "en5", "inet", "192.168.137.1", "-alias"]])

    def test_bad_ip_rejected(self):
        from launcher.windows_setup import WindowsSetupError
        with self.assertRaises(WindowsSetupError):
            directlink.local_ip_commands("eth0", "not-an-ip", 24, os_name="Linux")


class MacosDhcpOriginTests(unittest.TestCase):
    def test_getinfo_dhcp_detected(self):
        self.assertTrue(directlink._macos_service_is_dhcp(
            "DHCP Configuration\nIP address: 192.168.1.5\n"))
        self.assertFalse(directlink._macos_service_is_dhcp(
            "Manual Configuration\nIP address: 192.168.1.5\n"))
        self.assertFalse(directlink._macos_service_is_dhcp(""))

    def test_dhcp_origin_makes_interface_a_real_network(self):
        # en5 (the otherwise-eligible wired port) now holds a DHCP lease -> it
        # must be rejected as a real network, not offered as a direct link.
        parsed = directlink._parse_macos_adapters(
            MAC_PORTS, MAC_IFCONFIG, "en0", dhcp_by_dev={"en5": True})
        by = {a["id"]: a for a in parsed["adapters"]}
        self.assertEqual(by["en5"]["ipv4"][0]["origin"], "dhcp")
        candidates, _rej = directlink.find_candidates(parsed)
        self.assertEqual(candidates, [])  # nothing eligible now


class MacosRouteTests(unittest.TestCase):
    NETSTAT = """Routing tables

Internet:
Destination        Gateway            Flags        Netif Expire
default            192.168.1.1        UGScg          en0
127                127.0.0.1          UCS            lo0
10.8.0.0/24        utun3              USc            utun3
192.168.5          link#6             UCS            en5
192.168.9.4        192.168.1.1        UGHS           en0
"""

    def test_explicit_cidr_and_hosts_taken_abbreviated_skipped(self):
        routes = directlink._parse_macos_routes(self.NETSTAT)
        prefixes = {r["prefix"] for r in routes}
        self.assertIn("10.8.0.0/24", prefixes)      # explicit CIDR (a VPN)
        self.assertIn("192.168.9.4/32", prefixes)   # full dotted host
        self.assertNotIn("192.168.5", prefixes)     # abbreviated -> skipped
        self.assertNotIn("default", str(prefixes))

    def test_vpn_route_blocks_a_colliding_subnet(self):
        # A VPN route on 192.168.137.0/24 must push the chooser off .137.
        netstat = "Internet:\n192.168.137.0/24   utun0   USc   utun0\n"
        routes = directlink._parse_macos_routes(netstat)
        taken = directlink.taken_networks({"adapters": [], "routes": routes})
        server, _client = directlink.choose_subnet(taken)
        self.assertNotEqual(server, "192.168.137.1")


@unittest.skipUnless(hasattr(os, "getpgid"),
                     "POSIX-only: os.kill(pid, 0) is a liveness check there; on "
                     "Windows signal 0 is CTRL_C_EVENT, and _pid_alive is Unix-only")
class PidWatchTests(unittest.TestCase):
    def test_current_pid_alive(self):
        self.assertTrue(directlink._pid_alive(os.getpid()))

    def test_none_is_alive(self):
        self.assertTrue(directlink._pid_alive(None))
        self.assertTrue(directlink._pid_alive(0))

    def test_dead_pid_detected(self):
        self.assertFalse(directlink._pid_alive(2 ** 22))


class UnixElevationTests(unittest.TestCase):
    def test_linux_wraps_with_pkexec(self):
        wrapped = elevate.unix_privileged_command(
            ["python", "-m", "launcher", "--serve", "directlink"],
            os_name="Linux")
        self.assertEqual(wrapped[0], "pkexec")
        self.assertEqual(wrapped[1:], ["python", "-m", "launcher", "--serve",
                                       "directlink"])

    def test_macos_osascript_quotes_spaces(self):
        wrapped = elevate.unix_privileged_command(
            ["/Applications/PS2 Servers.app/exe", "--stop-file",
             "/tmp/a b/directlink.stop"], os_name="Darwin")
        self.assertEqual(wrapped[0], "osascript")
        self.assertEqual(wrapped[1], "-e")
        script = wrapped[2]
        self.assertIn("with administrator privileges", script)
        # the space-containing args must be shell-quoted inside the script
        self.assertIn("'/Applications/PS2 Servers.app/exe'", script)
        self.assertIn("'/tmp/a b/directlink.stop'", script)


if __name__ == "__main__":
    unittest.main()
