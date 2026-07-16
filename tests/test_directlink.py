"""Unit tests for the direct-link DHCP responder and its safety refusals.

Run from the repo root:  python -m unittest tests.test_directlink -v

Everything here is socket-free: handle_packet() is deliberately pure so the
protocol and the refusals can be tested without a wire.
"""

import struct
import unittest

from launcher import directlink
from launcher.directlink import (
    DhcpResponder, DirectLinkRefused, MAGIC_COOKIE,
    MSG_ACK, MSG_DISCOVER, MSG_NAK, MSG_OFFER, MSG_REQUEST,
    build_reply, choose_subnet, classify_adapter, networks_overlap,
    parse_packet, taken_networks, _BOOTP, _ip_to_int,
)

SERVER = "192.168.137.1"
CLIENT = "192.168.137.10"
MAC1 = b"\x00\x04\x1f\xaa\xbb\xcc"  # Sony OUI, why not


def make_request(msg_type, mac=MAC1, xid=0x1234, ciaddr="0.0.0.0",
                 options=b"", flags=0x8000):
    """A client BOOTREQUEST with the given DHCP message type."""
    chaddr = mac + b"\x00" * (16 - len(mac))
    header = _BOOTP.pack(1, 1, 6, 0, xid, 0, flags, _ip_to_int(ciaddr),
                         0, 0, 0, chaddr, b"", b"")
    opts = bytes([53, 1, msg_type]) + options + b"\xff"
    return header + MAGIC_COOKIE + opts


def opt_ip(tag, ip):
    return bytes([tag, 4]) + struct.pack("!I", _ip_to_int(ip))


def parse_options(reply):
    assert reply[236:240] == MAGIC_COOKIE
    options, i = {}, 240
    while i < len(reply):
        tag = reply[i]
        if tag == 0:
            i += 1
            continue
        if tag == 255:
            break
        length = reply[i + 1]
        options[tag] = reply[i + 2:i + 2 + length]
        i += 2 + length
    return options


def responder(mode="specific"):
    r = DhcpResponder(SERVER, CLIENT, 24, adapter_name="test",
                      log=lambda _msg: None)
    r.mode = mode
    return r


class ParseTests(unittest.TestCase):
    def test_discover_parses(self):
        pkt = parse_packet(make_request(MSG_DISCOVER))
        self.assertIsNotNone(pkt)
        self.assertEqual(pkt["xid"], 0x1234)
        self.assertEqual(pkt["mac"], MAC1)
        self.assertEqual(pkt["options"][53], bytes([MSG_DISCOVER]))

    def test_junk_and_short_are_none(self):
        self.assertIsNone(parse_packet(b""))
        self.assertIsNone(parse_packet(b"ps2srv-directlink-probe"))
        self.assertIsNone(parse_packet(b"\x00" * 300))  # no magic cookie

    def test_server_reply_is_none(self):
        data = bytearray(make_request(MSG_DISCOVER))
        data[0] = 2  # BOOTREPLY: never a thing we answer
        self.assertIsNone(parse_packet(bytes(data)))


class ReplyTests(unittest.TestCase):
    def test_offer_shape(self):
        pkt = parse_packet(make_request(MSG_DISCOVER))
        reply = build_reply(pkt, MSG_OFFER, SERVER, CLIENT, 24)
        self.assertGreaterEqual(len(reply), 300)
        self.assertEqual(reply[0], 2)  # BOOTREPLY
        yiaddr = struct.unpack("!I", reply[16:20])[0]
        self.assertEqual(yiaddr, _ip_to_int(CLIENT))
        opts = parse_options(reply)
        self.assertEqual(opts[53], bytes([MSG_OFFER]))
        self.assertEqual(opts[54], struct.pack("!I", _ip_to_int(SERVER)))
        self.assertEqual(opts[1], struct.pack("!I", 0xFFFFFF00))  # /24 mask
        self.assertEqual(opts[3], struct.pack("!I", _ip_to_int(SERVER)))
        self.assertIn(51, opts)  # lease time

    def test_nak_has_no_address(self):
        pkt = parse_packet(make_request(MSG_REQUEST))
        reply = build_reply(pkt, MSG_NAK, SERVER, CLIENT, 24)
        self.assertEqual(struct.unpack("!I", reply[16:20])[0], 0)
        opts = parse_options(reply)
        self.assertEqual(opts[53], bytes([MSG_NAK]))
        self.assertNotIn(51, opts)

    def test_xid_and_mac_echoed(self):
        pkt = parse_packet(make_request(MSG_DISCOVER, xid=0xDEADBEEF))
        reply = build_reply(pkt, MSG_OFFER, SERVER, CLIENT, 24)
        self.assertEqual(struct.unpack("!I", reply[4:8])[0], 0xDEADBEEF)
        self.assertEqual(reply[28:34], MAC1)


class HandleTests(unittest.TestCase):
    def test_discover_offer_broadcast(self):
        r = responder()
        result = r.handle_packet(make_request(MSG_DISCOVER), ("0.0.0.0", 68))
        self.assertIsNotNone(result)
        reply, dest = result
        self.assertEqual(parse_options(reply)[53], bytes([MSG_OFFER]))
        self.assertEqual(dest, ("255.255.255.255", 68))
        self.assertEqual(r.lease_mac, MAC1)

    def test_request_for_our_address_acks(self):
        r = responder()
        result = r.handle_packet(
            make_request(MSG_REQUEST, options=opt_ip(50, CLIENT)),
            ("0.0.0.0", 68))
        reply, _dest = result
        self.assertEqual(parse_options(reply)[53], bytes([MSG_ACK]))

    def test_request_for_other_address_naks(self):
        r = responder()
        result = r.handle_packet(
            make_request(MSG_REQUEST, options=opt_ip(50, "10.0.0.5")),
            ("0.0.0.0", 68))
        reply, _dest = result
        self.assertEqual(parse_options(reply)[53], bytes([MSG_NAK]))

    def test_foreign_server_id_is_silence(self):
        # The client accepted a different server's offer: not our business.
        r = responder()
        result = r.handle_packet(
            make_request(MSG_REQUEST,
                         options=opt_ip(54, "192.168.1.1") + opt_ip(50, CLIENT)),
            ("0.0.0.0", 68))
        self.assertIsNone(result)

    def test_renewal_is_unicast(self):
        r = responder()
        result = r.handle_packet(
            make_request(MSG_REQUEST, ciaddr=CLIENT),
            (CLIENT, 68))
        reply, dest = result
        self.assertEqual(parse_options(reply)[53], bytes([MSG_ACK]))
        self.assertEqual(dest, (CLIENT, 68))

    def test_wildcard_mode_uses_subnet_broadcast(self):
        # Containment: never a limited broadcast from a wildcard socket, which
        # could egress the machine's default (real-LAN) interface.
        r = responder(mode="wildcard")
        _reply, dest = r.handle_packet(make_request(MSG_DISCOVER),
                                       ("0.0.0.0", 68))
        self.assertEqual(dest, ("192.168.137.255", 68))

    def test_probe_and_junk_ignored(self):
        r = responder()
        self.assertIsNone(r.handle_packet(b"ps2srv-directlink-probe",
                                          ("0.0.0.0", 68)))
        self.assertIsNone(r.handle_packet(b"\x01" * 400, ("0.0.0.0", 68)))

    def test_multi_mac_tripwire(self):
        r = responder()
        for i in range(r.MAX_DISTINCT_MACS):
            mac = bytes([0, 0, 0, 0, 0, i + 1])
            r.handle_packet(make_request(MSG_DISCOVER, mac=mac), ("0.0.0.0", 68))
        with self.assertRaises(DirectLinkRefused):
            r.handle_packet(make_request(MSG_DISCOVER, mac=b"\x00\x00\x00\x00\x00\xFF"),
                            ("0.0.0.0", 68))

    def test_rate_limit_drops(self):
        r = responder()
        r._tokens = 1.0
        first = r.handle_packet(make_request(MSG_DISCOVER), ("0.0.0.0", 68))
        second = r.handle_packet(make_request(MSG_DISCOVER), ("0.0.0.0", 68))
        self.assertIsNotNone(first)
        self.assertIsNone(second)


class SubnetTests(unittest.TestCase):
    def test_overlap(self):
        n = _ip_to_int
        self.assertTrue(networks_overlap(n("192.168.137.0"), 24,
                                         n("192.168.0.0"), 16))
        self.assertFalse(networks_overlap(n("192.168.137.0"), 24,
                                          n("192.168.1.0"), 24))
        self.assertTrue(networks_overlap(n("10.0.0.0"), 8,
                                         n("10.213.77.0"), 24))

    def test_choose_skips_taken(self):
        taken = [(_ip_to_int("192.168.137.0"), 24)]
        server, client = choose_subnet(taken)
        self.assertEqual((server, client), ("192.168.183.1", "192.168.183.10"))

    def test_choose_default(self):
        server, client = choose_subnet([])
        self.assertEqual((server, client), ("192.168.137.1", "192.168.137.10"))

    def test_all_taken_returns_none(self):
        taken = [(_ip_to_int(base + ".0"), 24)
                 for base in directlink.CANDIDATE_SUBNETS]
        self.assertEqual(choose_subnet(taken), (None, None))

    def test_taken_networks_filters(self):
        enumerated = {
            "adapters": [
                {"if_index": 5, "ipv4": [
                    {"ip": "192.168.1.100", "prefix": 24, "origin": "Dhcp"},
                    {"ip": "169.254.9.9", "prefix": 16, "origin": "WellKnown"},
                ]},
                {"if_index": 7, "ipv4": [
                    {"ip": "192.168.137.1", "prefix": 24, "origin": "Manual"},
                ]},
            ],
            "routes": [
                {"prefix": "0.0.0.0/0", "ifIndex": 5},
                {"prefix": "224.0.0.0/4", "ifIndex": 5},
                {"prefix": "255.255.255.255/32", "ifIndex": 5},
                {"prefix": "169.254.0.0/16", "ifIndex": 9},
                {"prefix": "10.50.0.0/16", "ifIndex": 9},
                {"prefix": "192.168.137.0/24", "ifIndex": 7},
            ],
        }
        # Excluding adapter 7 (the one being reconfigured) drops both its
        # address and its route, so its old subnet stays reusable.
        taken = taken_networks(enumerated, exclude_if_index=7)
        nets = {(net, plen) for net, plen in taken}
        self.assertIn((_ip_to_int("192.168.1.0"), 24), nets)
        self.assertIn((_ip_to_int("10.50.0.0"), 16), nets)
        self.assertNotIn((_ip_to_int("192.168.137.0"), 24), nets)
        self.assertNotIn((_ip_to_int("169.254.0.0"), 16), nets)
        self.assertNotIn((_ip_to_int("224.0.0.0"), 4), nets)


class ClassifyTests(unittest.TestCase):
    def adapter(self, **overrides):
        base = {"name": "Ethernet", "if_index": 3, "desc": "NIC",
                "status": "Up", "media": "802.3", "physical": True,
                "has_gateway": False, "ipv4": []}
        base.update(overrides)
        return base

    def test_apipa_port_is_candidate(self):
        ok, _ = classify_adapter(self.adapter(
            ipv4=[{"ip": "169.254.10.20", "prefix": 16, "origin": "WellKnown"}]))
        self.assertTrue(ok)

    def test_gateway_refused(self):
        ok, reason = classify_adapter(self.adapter(has_gateway=True))
        self.assertFalse(ok)
        self.assertIn("router", reason)

    def test_dhcp_lease_refused(self):
        ok, reason = classify_adapter(self.adapter(
            ipv4=[{"ip": "192.168.1.50", "prefix": 24, "origin": "Dhcp"}]))
        self.assertFalse(ok)
        self.assertIn("DHCP", reason)

    def test_wireless_refused(self):
        ok, _ = classify_adapter(self.adapter(media="Native 802.11"))
        self.assertFalse(ok)

    def test_virtual_refused(self):
        ok, _ = classify_adapter(self.adapter(physical=False))
        self.assertFalse(ok)

    def test_down_refused(self):
        ok, reason = classify_adapter(self.adapter(status="Disconnected"))
        self.assertFalse(ok)
        self.assertIn("link", reason)

    def test_down_tolerated_for_rechecks(self):
        # Re-arming an already-ours port: console off at PC boot is normal.
        ok, _ = classify_adapter(self.adapter(status="Disconnected"),
                                 allow_down=True)
        self.assertTrue(ok)

    def test_down_with_gateway_still_refused(self):
        ok, reason = classify_adapter(
            self.adapter(status="Disconnected", has_gateway=True),
            allow_down=True)
        self.assertFalse(ok)
        self.assertIn("router", reason)


if __name__ == "__main__":
    unittest.main()
