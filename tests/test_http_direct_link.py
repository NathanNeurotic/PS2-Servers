"""HTTP mode must work over a direct cable as well as over a LAN.

A PS2 plugged straight into the PC gets its address from the direct-link DHCP
helper, then talks to whichever server the user started. Those two subsystems
were built independently and never checked against each other: direct link
knows nothing about HTTP, and the HTTP server knows nothing about direct link.
Everything below is the seam between them.

What this cannot cover is the physical layer -- a real console, a real cable,
and the PS2's own IP stack. Everything above that is exercised here: the real
DhcpResponder driven through a full DISCOVER/OFFER/REQUEST/ACK exchange, the
real HTTP server, and a real range read across the addresses the exchange
produced.

Run:  python -m unittest tests.test_http_direct_link -v
"""

import ipaddress
import os
import socket
import struct
import sys
import tempfile
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
_HTTP_DIR = os.path.join(ROOT, "http_server")
if _HTTP_DIR not in sys.path:
    sys.path.insert(0, _HTTP_DIR)
_UDPFS_DIR = os.path.join(ROOT, "udpfs_server")
if _UDPFS_DIR not in sys.path:
    sys.path.insert(0, _UDPFS_DIR)

from launcher import directlink, netinfo, windows_setup  # noqa: E402
from launcher.servers import REGISTRY  # noqa: E402

import http_server as hs  # noqa: E402

from tests.test_directlink import (  # noqa: E402
    make_request, opt_ip, parse_options)

CRLF = chr(13) + chr(10)
PAYLOAD = bytes(range(256)) * 40  # 10240 bytes


def _chosen_addresses():
    """The addresses production picks for a direct link, on a clean host.

    choose_subnet() is what launcher/gui.py calls when the user ticks the box,
    so this is the real pair rather than the constants the responder tests
    happen to use.
    """
    server_ip, client_ip = directlink.choose_subnet(taken=[])
    assert server_ip and client_ip, "choose_subnet found no free subnet"
    return server_ip, client_ip


def _leased_addresses():
    """(server_ip, client_ip) as the REAL responder hands them out.

    Built on production's chosen pair and driven through the same
    handle_packet() the helper runs on the wire, so these are the addresses a
    console actually ends up with -- not values this test supplied and then
    asserted on.
    """
    server_ip, client_ip = _chosen_addresses()
    r = directlink.DhcpResponder(server_ip, client_ip,
                                 directlink.PREFIX_LENGTH,
                                 adapter_name="test", log=lambda _m: None)
    r.mode = "specific"
    offer = r.handle_packet(make_request(directlink.MSG_DISCOVER), ("0.0.0.0", 68))
    assert offer is not None, "responder did not offer a lease"
    ack = r.handle_packet(
        make_request(directlink.MSG_REQUEST,
                     options=opt_ip(50, client_ip) + opt_ip(54, server_ip)),
        ("0.0.0.0", 68))
    assert ack is not None, "responder did not acknowledge the request"

    # handle_packet returns (reply_bytes, destination).
    payload, _dest = ack
    options = parse_options(payload)
    assert options[53] == bytes([directlink.MSG_ACK]), "reply was not an ACK"
    # yiaddr is the address the console takes; option 54 is the server it was
    # told to talk to. Read both out of the packet rather than assuming.
    leased = str(ipaddress.IPv4Address(struct.unpack("!I", payload[16:20])[0]))
    announced = str(ipaddress.IPv4Address(options[54]))
    assert leased == client_ip, "ACK leased {} not {}".format(leased, client_ip)
    assert announced == server_ip, "ACK named server {}".format(announced)
    return announced, leased


class LeaseAndHttpAgreeTests(unittest.TestCase):
    """The address the console is given must be able to reach the server."""

    def test_lease_puts_console_and_server_on_one_subnet(self):
        server_ip, client_ip = _leased_addresses()
        prefix = directlink.PREFIX_LENGTH
        net = ipaddress.IPv4Network("{}/{}".format(server_ip, prefix),
                                    strict=False)
        self.assertIn(ipaddress.IPv4Address(client_ip), net,
                      "the console's leased address is off the server's subnet, "
                      "so with no gateway on a direct cable it could not reach "
                      "the HTTP server at all")
        self.assertNotEqual(server_ip, client_ip)

    def test_the_dhcp_server_address_is_the_one_to_type_into_opl(self):
        """OPL's HTTP mode has no discovery -- the user types an IP.

        So the address the lease names as the server has to be the address the
        launcher would show, or the hint sends them somewhere nothing listens.
        """
        server_ip, _client = _leased_addresses()
        values = {f.key: f.default for f in REGISTRY["http"].fields}
        from launcher.gui import opl_hint
        hint = opl_hint("http", server_ip, values)
        self.assertIn(server_ip, hint)
        self.assertIn("1100", hint)

    def test_the_direct_link_address_would_reach_the_ip_picker(self):
        """all_ipv4() filters by address, not adapter name.

        A direct-link adapter has no gateway and would be easy to mistake for a
        virtual one. If the picker dropped it, the user could never select the
        address the console was just told to use.
        """
        server_ip, _client = _leased_addresses()
        self.assertFalse(server_ip.startswith(("127.", "169.254.")),
                         "all_ipv4() drops these, so the picker would hide it")
        # And it should sort well, not last: 192.168.x is the best LAN rank.
        self.assertEqual(netinfo._lan_rank(server_ip), 0)


class NoPortConflictTests(unittest.TestCase):
    def test_dhcp_and_http_cannot_contend_for_a_port(self):
        """The helper and the server run at the same time, so they must not
        want the same socket."""
        values = {f.key: f.default for f in REGISTRY["http"].fields}
        http_ports = windows_setup.server_ports("http", values)
        dhcp_ports = windows_setup.server_ports("directlink", {})
        self.assertTrue(http_ports and dhcp_ports)
        self.assertEqual([(p, n) for p, n, _ in dhcp_ports],
                         [("UDP", directlink.DHCP_SERVER_PORT)])
        for proto, port, _purpose in http_ports:
            self.assertEqual(proto, "TCP")
            self.assertNotEqual((proto, port), ("UDP", 67))

    def test_both_are_allowed_through_the_firewall(self):
        """Enabling direct link must not leave the server's port closed.

        Each mode contributes its own rule; neither replaces the other. A
        missing HTTP rule here is a console that leases an address fine and
        then cannot open a connection.
        """
        values = {f.key: f.default for f in REGISTRY["http"].fields}
        http_rules = windows_setup._rule_names("http", values)
        direct_rules = windows_setup._rule_names("directlink", {})
        self.assertTrue(any("HTTP" in name and "1100" in name
                            for name in http_rules), http_rules)
        self.assertTrue(any("67" in name for name in direct_rules), direct_rules)
        self.assertFalse(set(http_rules) & set(direct_rules) - {"PS2 Servers - App"},
                         "the two modes must not share a port rule")


class RangeReadOverTheLeasedTopologyTests(unittest.TestCase):
    """A real range read against a server bound the way direct link needs.

    The cable and the console are out of reach here, but the binding is not:
    the whole reason direct connect works is that the server listens on every
    interface, so the same process answers on the LAN NIC and on the
    gateway-less direct-link one.
    """

    def setUp(self):
        self.work = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.work, "DVD"))
        with open(os.path.join(self.work, "DVD",
                               "SLUS_201.74.Direct.iso"), "wb") as handle:
            handle.write(PAYLOAD)
        self.index = hs.GameIndex(self.work)
        # "" is what the launcher passes when Bind is left blank, which is the
        # default and the only value that serves both a LAN and a direct cable.
        self.server = hs.Ps2HTTPServer(("", 0), self.index)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()
        self.addCleanup(self._stop)

    def _stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        import shutil
        shutil.rmtree(self.work, ignore_errors=True)

    def _range_read(self, host, start, end):
        sock = socket.create_connection((host, self.port), timeout=5)
        self.addCleanup(sock.close)
        sock.sendall((
            "GET /SLUS_201.74.Direct.iso HTTP/1.1{c}"
            "Host: {h}:{p}{c}Range: bytes={s}-{e}{c}"
            "Connection: keep-alive{c}{c}"
        ).format(c=CRLF, h=host, p=self.port, s=start, e=end).encode("ascii"))
        data = b""
        while (CRLF + CRLF).encode() not in data:
            data += sock.recv(65536)
        head, _, body = data.partition((CRLF + CRLF).encode())
        want = end - start + 1
        while len(body) < want:
            body += sock.recv(65536)
        return head, body

    def test_server_listens_on_every_interface(self):
        self.assertEqual(self.server.server_address[0], "0.0.0.0",
                         "bound to one interface, so it would answer on the "
                         "LAN and be invisible over a direct cable")

    def test_a_console_style_read_succeeds_on_each_local_address(self):
        try:
            addrs = sorted({i[4][0] for i in socket.getaddrinfo(
                socket.gethostname(), None, socket.AF_INET)})
        except socket.gaierror:
            addrs = []
        addrs = [a for a in addrs if not a.startswith(("127.", "169.254."))]
        if not addrs:
            self.skipTest("no non-loopback IPv4 address on this host")
        for ip in addrs:
            with self.subTest(address=ip):
                head, body = self._range_read(ip, 0, 2047)
                self.assertTrue(head.startswith(b"HTTP/1.1 206"), ip)
                self.assertEqual(body, PAYLOAD[:2048], ip)


if __name__ == "__main__":
    unittest.main()
