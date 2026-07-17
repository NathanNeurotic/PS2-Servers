"""Unit tests for the direct-link DHCP responder and its safety refusals.

Run from the repo root:  python -m unittest tests.test_directlink -v

Everything here is socket-free: handle_packet() is deliberately pure so the
protocol and the refusals can be tested without a wire.
"""

import socket
import struct
import unittest
from unittest import mock

from launcher import directlink, windows_setup
from launcher.directlink import (
    DhcpResponder, DirectLinkRefused, MAGIC_COOKIE,
    MSG_ACK, MSG_DISCOVER, MSG_NAK, MSG_OFFER, MSG_REQUEST,
    build_reply, choose_subnet, classify_adapter, networks_overlap,
    parse_packet, plan_rehome, taken_networks, _BOOTP, _free_host, _ip_to_int,
    _build_discover, _foreign_offer_server_id, _synthetic_probe_mac,
)
from launcher.gui import LauncherApp
from launcher.windows_setup import WindowsSetupError

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

    def test_request_for_other_address_is_silent(self):
        # Never a broadcast NAK: on a wire that turned out to be a real network
        # that would kick its clients off valid leases. Silence -> the client
        # times out and re-DISCOVERs, and a real PS2 then gets our address.
        r = responder()
        result = r.handle_packet(
            make_request(MSG_REQUEST, options=opt_ip(50, "10.0.0.5")),
            ("0.0.0.0", 68))
        self.assertIsNone(result)

    def test_init_reboot_foreign_address_is_silent(self):
        # INIT-REBOOT/REBINDING carries no server-id (option 54) and a foreign
        # ciaddr; this is exactly the packet a rebooting real-LAN client sends,
        # and it must never draw a NAK.
        r = responder()
        result = r.handle_packet(
            make_request(MSG_REQUEST, ciaddr="192.168.1.23"),
            ("0.0.0.0", 68))
        self.assertIsNone(result)

    def test_own_probe_mac_ignored(self):
        # The periodic foreign-server probe is heard back on port 67; it must
        # never be tracked (would trip the 1-MAC tripwire) or answered.
        r = responder()
        result = r.handle_packet(
            make_request(MSG_DISCOVER, mac=r.probe_mac), ("0.0.0.0", 68))
        self.assertIsNone(result)
        self.assertEqual(r.macs_seen, set())

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


class ServeTests(unittest.TestCase):
    class StopLoop(Exception):
        pass

    class FakeSocket:
        def __init__(self, first_error):
            self.first_error = first_error
            self.calls = 0

        def settimeout(self, _seconds):
            pass

        def recvfrom(self, _size):
            self.calls += 1
            if self.calls == 1:
                raise self.first_error
            raise ServeTests.StopLoop()

    def assert_reset_is_ignored(self, error):
        r = responder()
        r.sock = self.FakeSocket(error)
        with self.assertRaises(self.StopLoop):
            r.serve_forever()
        self.assertEqual(r.sock.calls, 2)

    def test_connection_reset_is_ignored(self):
        self.assert_reset_is_ignored(
            ConnectionResetError(10054, "connection reset by peer"))

    def test_windows_udp_reset_is_ignored(self):
        error = OSError("ICMP port unreachable")
        error.winerror = 10054
        self.assert_reset_is_ignored(error)


class RehomeTests(unittest.TestCase):
    # PC default is 192.168.137.1, DHCP client .10.
    def test_no_neighbors_no_move(self):
        self.assertIsNone(plan_rehome("192.168.137.1", "192.168.137.10", 24,
                                      [], our_ip_present=True))

    def test_neighbor_in_subnet_not_contesting_no_move(self):
        # A console at .10 (not our address) with our address still present:
        # nothing to fix.
        self.assertIsNone(plan_rehome("192.168.137.1", "192.168.137.10", 24,
                                      ["192.168.137.10"], our_ip_present=True))

    def test_conflict_steps_to_next_free_host(self):
        # GZAst's case: a console statically at 192.168.137.1 (our address);
        # Windows removed ours. Step the PC to a free host in the same subnet.
        plan = plan_rehome("192.168.137.1", "192.168.137.10", 24,
                           ["192.168.137.1"], our_ip_present=False)
        self.assertIsNotNone(plan)
        new_server, new_client, prefix = plan
        self.assertEqual(new_server, "192.168.137.2")   # .1 avoided
        self.assertEqual(new_client, "192.168.137.10")  # unchanged, free
        self.assertEqual(prefix, 24)

    def test_conflict_moves_client_if_it_would_collide(self):
        # Console occupies BOTH .1 and the client host .10 -> both move off.
        plan = plan_rehome("192.168.137.1", "192.168.137.10", 24,
                           ["192.168.137.1", "192.168.137.10"],
                           our_ip_present=False)
        new_server, new_client, _p = plan
        self.assertEqual(new_server, "192.168.137.2")
        self.assertNotIn(new_client, ("192.168.137.1", "192.168.137.10",
                                      new_server))

    def test_offsubnet_neighbor_adopts_its_subnet(self):
        # Console left static on an old router subnet (192.168.1.50): adopt
        # 192.168.1.x so the PC can reach it; it finds us by broadcast.
        plan = plan_rehome("192.168.137.1", "192.168.137.10", 24,
                           ["192.168.1.50"], our_ip_present=True)
        self.assertIsNotNone(plan)
        new_server, new_client, prefix = plan
        self.assertTrue(new_server.startswith("192.168.1."))
        self.assertNotEqual(new_server, "192.168.1.50")   # avoid the console
        self.assertNotEqual(new_client, "192.168.1.50")
        self.assertNotEqual(new_client, new_server)
        self.assertEqual(prefix, 24)

    def test_free_host_skips_avoided(self):
        net = _ip_to_int("192.168.137.0")
        first = _free_host(net, 24, {_ip_to_int("192.168.137.1")})
        self.assertEqual(first, _ip_to_int("192.168.137.2"))

    def test_parse_neighbors_filters(self):
        js = ('["192.168.137.1", "169.254.9.9", "224.0.0.251", '
              '"192.168.137.255", "192.168.137.2", "127.0.0.1"]')
        got = directlink._parse_neighbors(js, our_ips=("192.168.137.2",))
        self.assertEqual(got, ["192.168.137.1"])  # the rest are filtered

    def test_parse_neighbors_single_and_empty(self):
        self.assertEqual(directlink._parse_neighbors('"10.0.0.5"'), ["10.0.0.5"])
        self.assertEqual(directlink._parse_neighbors(""), [])
        self.assertEqual(directlink._parse_neighbors("not json"), [])


class MissingIpDiagnosisTests(unittest.TestCase):
    def test_up_adapter_means_address_conflict(self):
        # Link up but our address gone -> another device is using it.
        with mock.patch.object(directlink, "adapter_state",
                               return_value={"name": "Ethernet 2", "status": "Up",
                                             "ipv4": []}):
            msg = responder()._diagnose_missing_server_ip()
        self.assertIn("another", msg)
        self.assertIn("DHCP", msg)
        self.assertIn(CLIENT, msg)   # suggests the non-colliding address

    def test_down_adapter_means_cable_or_console(self):
        with mock.patch.object(directlink, "adapter_state",
                               return_value={"name": "Ethernet 2",
                                             "status": "Disconnected", "ipv4": []}):
            msg = responder()._diagnose_missing_server_ip()
        self.assertIn("link went down", msg)
        self.assertNotIn("another device", msg)

    def test_adapter_lookup_failure_is_safe(self):
        with mock.patch.object(directlink, "adapter_state",
                               side_effect=RuntimeError("boom")):
            msg = responder()._diagnose_missing_server_ip()
        self.assertIn("no longer configured", msg)  # never raises
        # A failed lookup must NOT be reported as either specific cause.
        self.assertNotIn("link went down", msg)
        self.assertNotIn("another device", msg)

    def test_adapter_absent_is_generic(self):
        # adapter_state returning None (port gone) is also "unknown cause".
        with mock.patch.object(directlink, "adapter_state", return_value=None):
            msg = responder()._diagnose_missing_server_ip()
        self.assertIn("no longer configured", msg)
        self.assertNotIn("link went down", msg)
        self.assertNotIn("another device", msg)

    def test_unknown_status_is_generic(self):
        # A missing/unfamiliar status must not be guessed as a link-down.
        for status in ("", None, "weird"):
            with mock.patch.object(directlink, "adapter_state",
                                   return_value={"name": "Ethernet 2",
                                                 "status": status, "ipv4": []}):
                msg = responder()._diagnose_missing_server_ip()
            self.assertNotIn("link went down", msg, status)
            self.assertNotIn("another device", msg, status)
            self.assertIn("no longer configured", msg, status)

    def test_serve_forever_refuses_with_diagnosis_when_ip_vanishes(self):
        # Exercise the real refusal path: the periodic recheck finds the
        # address gone, no coexist plan exists, and serve_forever raises with
        # the conflict diagnosis.
        r = responder()
        r.sock = ServeTests.FakeSocket(socket.timeout())  # recvfrom never reached
        with mock.patch.object(r, "_server_ip_still_present", return_value=False), \
                mock.patch.object(directlink.DhcpResponder,
                                  "IP_RECHECK_SECONDS", -1), \
                mock.patch.object(directlink, "interface_neighbors",
                                  return_value=[]), \
                mock.patch.object(directlink, "adapter_state",
                                  return_value={"name": "Ethernet 2",
                                                "status": "Up", "ipv4": []}):
            with self.assertRaises(DirectLinkRefused) as caught:
                r.serve_forever()
        self.assertIn("another device", str(caught.exception))

    def test_serve_forever_rehomes_when_a_device_shares_our_address(self):
        # A console statically on our address removes ours (DAD). Exercise the
        # REAL neighbour contract: Get-NetNeighbor reports .1, and because our
        # address is gone we must NOT filter it out (our_ips is empty), so the
        # conflict is seen and we coexist by re-homing instead of refusing.
        r = responder()
        r.if_index = 12
        r.sock = ServeTests.FakeSocket(socket.timeout())
        neigh = mock.Mock(returncode=0, stdout='["192.168.137.1"]')
        with mock.patch.object(r, "_server_ip_still_present", return_value=False), \
                mock.patch.object(directlink.DhcpResponder,
                                  "IP_RECHECK_SECONDS", -1), \
                mock.patch.object(directlink, "is_windows", return_value=True), \
                mock.patch.object(directlink, "_powershell", return_value=neigh):
            with self.assertRaises(directlink._Rehome) as caught:
                r.serve_forever()
        self.assertEqual(caught.exception.server_ip, "192.168.137.2")

    def test_plan_rehome_now_keeps_our_address_while_present(self):
        # While we still hold our address it is filtered from the neighbour list
        # (it is ours, not a device), so an idle wire yields no move.
        r = responder()
        r.if_index = 3
        neigh = mock.Mock(returncode=0, stdout='["192.168.137.1"]')  # only us
        with mock.patch.object(directlink, "is_windows", return_value=True), \
                mock.patch.object(directlink, "_powershell", return_value=neigh):
            self.assertIsNone(r._plan_rehome_now(server_ip_present=True))

    def test_open_socket_rehomes_on_startup_conflict(self):
        # A console already holding our address at launch makes bind() fail with
        # EADDRNOTAVAIL forever; once a device is seen, coexist via re-home.
        import errno
        r = responder()
        r.if_index = 7

        def fail_bind(*_a, **_k):
            err = OSError("address not available")
            err.errno = errno.EADDRNOTAVAIL
            raise err

        fake = mock.Mock()
        fake.bind.side_effect = fail_bind
        with mock.patch.object(directlink.socket, "socket", return_value=fake), \
                mock.patch.object(directlink.time, "sleep"), \
                mock.patch.object(
                    r, "_plan_rehome_now",
                    return_value=("192.168.137.2", "192.168.137.10", 24)):
            with self.assertRaises(directlink._Rehome) as caught:
                r.open_socket()
        self.assertEqual(caught.exception.server_ip, "192.168.137.2")

    def test_open_socket_stops_when_teardown_requested_while_waiting(self):
        # While bind() keeps failing (link down / no console), an unticked box
        # or an exited launcher must still tear the helper down instead of
        # sleeping forever -- the stop check has to run inside the wait loop,
        # even after the re-home probe finds nothing.
        import errno
        r = responder()

        def fail_bind(*_a, **_k):
            err = OSError("address not available")
            err.errno = errno.EADDRNOTAVAIL
            raise err

        fake = mock.Mock()
        fake.bind.side_effect = fail_bind
        with mock.patch.object(directlink.socket, "socket", return_value=fake), \
                mock.patch.object(directlink.time, "sleep"), \
                mock.patch.object(r, "_plan_rehome_now", return_value=None), \
                mock.patch.object(r, "_stop_requested",
                                  side_effect=[False, True]):
            with self.assertRaises(directlink._StopResponder):
                r.open_socket()


def make_offer(xid, server_ip, offered_ip="192.168.137.10", msg_type=MSG_OFFER,
               include_server_id=True, siaddr=None):
    """A server's BOOTREPLY (DHCPOFFER/ACK) as a foreign server would send it."""
    chaddr = b"\x02\x00\x00\x00\x00\x99" + b"\x00" * 10
    si = _ip_to_int(siaddr) if siaddr else _ip_to_int(server_ip)
    header = _BOOTP.pack(2, 1, 6, 0, xid, 0, 0x8000, 0,
                         _ip_to_int(offered_ip), si, 0, chaddr, b"", b"")
    opts = bytes([53, 1, msg_type])
    if include_server_id:
        opts += bytes([54, 4]) + struct.pack("!I", _ip_to_int(server_ip))
    opts += b"\xff"
    reply = header + MAGIC_COOKIE + opts
    return reply + b"\x00" * max(0, 300 - len(reply))


class ProbeTests(unittest.TestCase):
    def test_probe_mac_is_locally_administered_unicast(self):
        for _ in range(20):
            mac = _synthetic_probe_mac()
            self.assertEqual(len(mac), 6)
            self.assertTrue(mac[0] & 0x02)        # locally administered
            self.assertFalse(mac[0] & 0x01)       # unicast

    def test_discover_is_wellformed_and_parseable(self):
        disc = _build_discover(0xABCD1234, b"\x02\x00\x00\x00\x00\x01")
        pkt = parse_packet(disc)
        self.assertIsNotNone(pkt)
        self.assertEqual(pkt["options"][53], bytes([MSG_DISCOVER]))
        self.assertEqual(struct.unpack("!I", disc[4:8])[0], 0xABCD1234)
        self.assertEqual(disc[10:12], b"\x80\x00")  # broadcast flag set

    def test_foreign_offer_detected(self):
        offer = make_offer(0x1111, "192.168.1.1")
        self.assertEqual(
            _foreign_offer_server_id(offer, 0x1111), "192.168.1.1")

    def test_same_ip_reply_is_still_foreign(self):
        # Our own responder provably never answers the probe (single-threaded,
        # and handle_packet drops the probe MAC), so a reply claiming our own
        # address is a foreign machine using it -- a neighboring Windows ICS
        # host is literally 192.168.137.1. It must be detected, not excused.
        offer = make_offer(0x2222, "192.168.137.1")
        self.assertEqual(
            _foreign_offer_server_id(offer, 0x2222), "192.168.137.1")

    def test_wrong_xid_ignored(self):
        offer = make_offer(0x3333, "192.168.1.1")
        self.assertIsNone(_foreign_offer_server_id(offer, 0x9999))

    def test_offer_without_server_id_uses_siaddr(self):
        offer = make_offer(0x4444, "192.168.1.1", include_server_id=False,
                           siaddr="192.168.1.5")
        self.assertEqual(
            _foreign_offer_server_id(offer, 0x4444), "192.168.1.5")

    def test_bootrequest_is_not_a_foreign_reply(self):
        # A client's DISCOVER (op=1) must never read as a server answering.
        disc = _build_discover(0x5555, b"\x02\x00\x00\x00\x00\x01")
        self.assertIsNone(_foreign_offer_server_id(disc, 0x5555))

    def test_junk_is_not_a_foreign_reply(self):
        self.assertIsNone(_foreign_offer_server_id(b"\x00" * 300, 0x1))
        self.assertIsNone(_foreign_offer_server_id(b"short", 0x1))

    def test_probe_rx_honors_wildcard_mode(self):
        # A machine that forced the responder into wildcard mode would not
        # deliver broadcast OFFERs to a specifically-bound probe socket
        # either; the probe must mirror the responder's receive mode.
        for mode, expected in (("wildcard", True), ("specific", False)):
            r = responder(mode=mode)
            with mock.patch.object(directlink, "probe_for_foreign_dhcp_server",
                                   return_value=None) as probe:
                r.check_for_foreign_dhcp_server()
            self.assertEqual(probe.call_args.kwargs.get("wildcard_rx"),
                             expected, mode)

    def test_check_raises_on_foreign_server(self):
        r = responder()
        with mock.patch.object(directlink, "probe_for_foreign_dhcp_server",
                               return_value="192.168.1.1"):
            with self.assertRaises(DirectLinkRefused):
                r.check_for_foreign_dhcp_server()

    def test_check_passes_when_quiet(self):
        r = responder()
        with mock.patch.object(directlink, "probe_for_foreign_dhcp_server",
                               return_value=None):
            r.check_for_foreign_dhcp_server()  # no raise

    def test_check_fails_open_when_probe_unavailable(self):
        # Empty string == "could not probe"; the other layers still apply, so
        # we proceed rather than refuse a legitimate direct link.
        r = responder()
        with mock.patch.object(directlink, "probe_for_foreign_dhcp_server",
                               return_value=""):
            r.check_for_foreign_dhcp_server()  # no raise


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
                {"id": 5, "ipv4": [
                    {"ip": "192.168.1.100", "prefix": 24, "origin": "Dhcp"},
                    {"ip": "169.254.9.9", "prefix": 16, "origin": "WellKnown"},
                ]},
                {"id": 7, "ipv4": [
                    {"ip": "192.168.137.1", "prefix": 24, "origin": "Manual"},
                ]},
            ],
            "routes": [
                {"prefix": "0.0.0.0/0", "if_id": 5},
                {"prefix": "224.0.0.0/4", "if_id": 5},
                {"prefix": "255.255.255.255/32", "if_id": 5},
                {"prefix": "169.254.0.0/16", "if_id": 9},
                {"prefix": "10.50.0.0/16", "if_id": 9},
                {"prefix": "192.168.137.0/24", "if_id": 7},
            ],
        }
        # Excluding adapter 7 (the one being reconfigured) drops both its
        # address and its route, so its old subnet stays reusable.
        taken = taken_networks(enumerated, exclude_id=7)
        nets = {(net, plen) for net, plen in taken}
        self.assertIn((_ip_to_int("192.168.1.0"), 24), nets)
        self.assertIn((_ip_to_int("10.50.0.0"), 16), nets)
        self.assertNotIn((_ip_to_int("192.168.137.0"), 24), nets)
        self.assertNotIn((_ip_to_int("169.254.0.0"), 16), nets)
        self.assertNotIn((_ip_to_int("224.0.0.0"), 4), nets)

    def test_taken_networks_additive_retains_selected(self):
        # Unix setup is additive: the selected port KEEPS its address, so with
        # no exclusion its network and its route must stay in the taken set, or
        # choose_subnet could pick a subnet that collides with the very port we
        # are adding to. The Windows replacement flow (exclude_id) drops them.
        enumerated = {
            "adapters": [
                {"id": 7, "ipv4": [
                    {"ip": "192.168.137.1", "prefix": 24, "origin": "Manual"},
                ]},
            ],
            "routes": [
                {"prefix": "192.168.137.0/24", "if_id": 7},
                {"prefix": "10.50.0.0/16", "if_id": 7},
            ],
        }
        keep = {(net, plen) for net, plen in taken_networks(enumerated)}
        self.assertIn((_ip_to_int("192.168.137.0"), 24), keep)
        self.assertIn((_ip_to_int("10.50.0.0"), 16), keep)
        drop = {(net, plen) for net, plen
                in taken_networks(enumerated, exclude_id=7)}
        self.assertNotIn((_ip_to_int("192.168.137.0"), 24), drop)
        self.assertNotIn((_ip_to_int("10.50.0.0"), 16), drop)


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


class ConfigurationSafetyTests(unittest.TestCase):
    def test_invalid_server_ip_is_rejected_before_powershell(self):
        with mock.patch.object(directlink, "_powershell") as powershell:
            with self.assertRaises(WindowsSetupError):
                directlink.apply_adapter_config(
                    3, "192.168.137.1'; Remove-Item C:\\ -Recurse; '", CLIENT)
        powershell.assert_not_called()

    def test_invalid_restore_guard_is_rejected_before_powershell(self):
        with mock.patch.object(directlink, "_powershell") as powershell:
            with self.assertRaises(WindowsSetupError):
                directlink.restore_adapter_dhcp(
                    3, expect_ip="192.168.137.1'; Write-Output BAD; '")
        powershell.assert_not_called()

    def test_configuration_script_contains_failure_rollback(self):
        result = mock.Mock(returncode=0, stdout="CONFIGURED=Ethernet", stderr="")
        with mock.patch.object(directlink, "_powershell", return_value=result) as ps:
            directlink.apply_adapter_config(3, SERVER, CLIENT)
        script = ps.call_args.args[0]
        self.assertIn("catch {", script)
        self.assertIn("-Dhcp Enabled", script)
        self.assertIn("throw $originalError", script)

    def test_invalid_client_topology_is_rejected_before_powershell(self):
        with mock.patch.object(directlink, "_powershell") as powershell:
            with self.assertRaises(WindowsSetupError):
                directlink.apply_adapter_config(
                    3, SERVER, "192.168.138.10", 24)
        powershell.assert_not_called()

    def test_responder_refuses_non_windows(self):
        args = ["--server-ip", SERVER, "--client-ip", CLIENT,
                "--adapter", "Ethernet", "--if-index", "3"]
        with mock.patch.object(directlink, "is_windows", return_value=False):
            self.assertEqual(directlink.run_responder(args), 3)

    def test_topology_rejects_invalid_prefix(self):
        for prefix in (0, 31, 32, 33):
            with self.subTest(prefix=prefix), self.assertRaises(WindowsSetupError):
                directlink._validate_topology(SERVER, CLIENT, prefix)

    def test_topology_rejects_invalid_client_addresses(self):
        for client in (SERVER, "192.168.137.0", "192.168.137.255",
                       "192.168.138.10"):
            with self.subTest(client=client), self.assertRaises(WindowsSetupError):
                directlink._validate_topology(SERVER, client, 24)

    def test_topology_accepts_distinct_hosts_in_same_subnet(self):
        self.assertEqual(
            directlink._validate_topology(SERVER, CLIENT, 24),
            (SERVER, CLIENT, 24))


class FirewallSafetyTests(unittest.TestCase):
    def test_directlink_rule_is_address_scoped_without_app_wildcard(self):
        rules = windows_setup._firewall_rules(
            "directlink", {"server_ip": SERVER})
        self.assertEqual(len(rules), 1)
        self.assertIsNone(rules[0]["program"])
        self.assertEqual(rules[0]["local_address"], SERVER)

    def test_directlink_firewall_script_uses_local_address(self):
        result = mock.Mock(returncode=0, stdout="SETUP_CHANGED=True", stderr="")
        with (mock.patch.object(windows_setup, "is_windows", return_value=True),
              mock.patch.object(windows_setup, "_powershell",
                                return_value=result) as ps):
            windows_setup.apply_setup("directlink", {"server_ip": SERVER})
        self.assertIn("-LocalAddress '{}'".format(SERVER), ps.call_args.args[0])


class LauncherLifecycleTests(unittest.TestCase):
    def app(self):
        app = object.__new__(LauncherApp)
        app.saved = {"direct_link": {
            "enabled": False, "server_ip": SERVER, "client_ip": CLIENT,
            "adapter": "Ethernet", "if_index": 3, "prefix": 24,
        }}
        app._append_log = mock.Mock()
        app._save = mock.Mock()
        app._set_direct_checkbox = mock.Mock()
        app._set_direct_status = mock.Mock()
        app._shutting_down = False  # real __init__ sets this; _poll_status reads it
        return app

    def test_enable_is_not_saved_when_helper_fails_to_start(self):
        app = self.app()
        app._start_direct_responder = mock.Mock(return_value=False)
        app._direct_link_restore_async = mock.Mock()
        app.ip_var = mock.Mock()
        LauncherApp._direct_link_enabled(app, "")
        self.assertFalse(app.saved["direct_link"]["enabled"])
        app._save.assert_called_once_with()
        app._direct_link_restore_async.assert_called_once_with(
            app.saved["direct_link"], clear_saved=True, daemon=True)
        app.ip_var.set.assert_not_called()

    def test_firewall_failure_restores_configured_adapter(self):
        app = self.app()
        app.root = mock.Mock()
        app.root.after.side_effect = lambda _delay, callback: callback()
        app.nb = mock.Mock()
        app.terminal_tab = object()
        app._direct_link_fail = mock.Mock()
        with (mock.patch.object(directlink, "apply_adapter_config",
                               return_value="CONFIGURED=Ethernet"),
              mock.patch.object(windows_setup, "apply_setup",
                                side_effect=WindowsSetupError("firewall failed")),
              mock.patch.object(directlink, "restore_adapter_dhcp",
                                return_value="RESTORED=automatic (DHCP)") as restore,
              mock.patch("launcher.gui.threading.Thread") as thread):
            LauncherApp._direct_link_apply_async(app)
            thread.call_args.kwargs["target"]()
        restore.assert_called_once_with(3, expect_ip=SERVER)
        app._direct_link_fail.assert_called_once()
        self.assertIn("returned to automatic", app._direct_link_fail.call_args.args[0])

    def test_unexpected_helper_exit_restores_adapter(self):
        app = self.app()
        app.saved["direct_link"]["enabled"] = True
        app.procs = {}
        app._direct_expected = True
        app._direct_proc = mock.Mock()
        app._direct_proc.is_running.return_value = False
        app._direct_proc.returncode = 3
        app._direct_proc.lines = []  # no REHOME line -> normal exit path
        app._rollback_failed_direct_responder = mock.Mock()
        app.root = mock.Mock()
        LauncherApp._poll_status(app)
        app._rollback_failed_direct_responder.assert_called_once_with(
            app.saved["direct_link"])
        app.root.after.assert_called_once_with(600, app._poll_status)

    def test_rehome_exit_triggers_coexist_not_rollback(self):
        app = self.app()
        app.saved["direct_link"]["enabled"] = True
        app.procs = {}
        app._direct_expected = True
        app._direct_proc = mock.Mock()
        app._direct_proc.is_running.return_value = False
        app._direct_proc.returncode = 5
        app._direct_proc.lines = [
            "[direct link] moving…",
            "REHOME server_ip=192.168.1.1 client_ip=192.168.1.10 prefix=24"]
        app._direct_link_rehome = mock.Mock()
        app._rollback_failed_direct_responder = mock.Mock()
        app.root = mock.Mock()
        LauncherApp._poll_status(app)
        app._direct_link_rehome.assert_called_once_with(
            ("192.168.1.1", "192.168.1.10", 24))
        app._rollback_failed_direct_responder.assert_not_called()

    def test_stop_all_also_stops_direct_responder(self):
        app = self.app()
        app.procs = {}
        app._stop_direct_responder = mock.Mock()
        LauncherApp.stop_all(app)
        app._stop_direct_responder.assert_called_once_with()

    def test_failed_cleanup_retains_recovery_state(self):
        app = self.app()
        cfg = dict(app.saved["direct_link"])
        LauncherApp._fail_direct_cleanup(app, RuntimeError("busy"), cfg)
        self.assertEqual(app.saved["direct_link"], cfg)

    def test_successful_cleanup_clears_recovery_state(self):
        app = self.app()
        LauncherApp._finish_direct_cleanup(app, "RESTORED=automatic (DHCP)")
        self.assertNotIn("direct_link", app.saved)
        app._save.assert_called_once_with()

    def test_start_failure_restore_clears_recovery_state(self):
        app = self.app()
        app.saved["pending_direct_link_restore"] = True
        LauncherApp._direct_link_restored(
            app, "RESTORED=automatic (DHCP)", clear_saved=True)
        self.assertNotIn("direct_link", app.saved)
        self.assertNotIn("pending_direct_link_restore", app.saved)
        app._save.assert_called_once_with()

    def test_failed_helper_persists_recovery_before_worker(self):
        app = self.app()
        app._direct_link_restore_async = mock.Mock()
        cfg = app.saved["direct_link"]
        LauncherApp._rollback_failed_direct_responder(app, cfg)
        self.assertTrue(app.saved["pending_direct_link_restore"])
        self.assertFalse(cfg["enabled"])
        app._save.assert_called_once_with()
        app._direct_link_restore_async.assert_called_once_with(
            cfg, clear_saved=True, daemon=True)

    def test_failed_recovery_save_uses_non_daemon_restore(self):
        app = self.app()
        app._save.return_value = False
        app._direct_link_restore_async = mock.Mock()
        cfg = app.saved["direct_link"]
        LauncherApp._rollback_failed_direct_responder(app, cfg)
        app._direct_link_restore_async.assert_called_once_with(
            cfg, clear_saved=True, daemon=False)
        self.assertIn("could not save", app._append_log.call_args.args[1])

    def test_restore_worker_honors_non_daemon_policy(self):
        app = self.app()
        with mock.patch("launcher.gui.threading.Thread") as thread:
            LauncherApp._direct_link_restore_async(
                app, app.saved["direct_link"], daemon=False)
        self.assertFalse(thread.call_args.kwargs["daemon"])
        thread.return_value.start.assert_called_once_with()

    def test_pending_recovery_resumes_after_restart(self):
        app = self.app()
        app.saved["pending_direct_link_restore"] = True
        app.nb = mock.Mock()
        app.terminal_tab = object()
        app._direct_link_restore_async = mock.Mock()
        with mock.patch("launcher.gui.elevate.is_admin", return_value=True):
            LauncherApp._direct_link_recovery_pending(app)
        app._direct_link_restore_async.assert_called_once_with(
            app.saved["direct_link"], clear_saved=True)
        self.assertTrue(app.saved["pending_direct_link_restore"])

    def test_startup_preflight_failure_enters_recovery(self):
        app = self.app()
        app.saved["direct_link"]["enabled"] = True
        app._rollback_failed_direct_responder = mock.Mock()
        LauncherApp._direct_link_startup_done(app, "adapter reaches a router")
        app._rollback_failed_direct_responder.assert_called_once_with(
            app.saved["direct_link"])


if __name__ == "__main__":
    unittest.main()
