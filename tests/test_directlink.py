"""Unit tests for the direct-link DHCP responder and its safety refusals.

Run from the repo root:  python -m unittest tests.test_directlink -v

Everything here is socket-free: handle_packet() is deliberately pure so the
protocol and the refusals can be tested without a wire.
"""

import struct
import unittest
from unittest import mock

from launcher import directlink, windows_setup
from launcher.directlink import (
    DhcpResponder, DirectLinkRefused, MAGIC_COOKIE,
    MSG_ACK, MSG_DISCOVER, MSG_NAK, MSG_OFFER, MSG_REQUEST,
    build_reply, choose_subnet, classify_adapter, networks_overlap,
    parse_packet, taken_networks, _BOOTP, _ip_to_int,
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
        app._rollback_failed_direct_responder = mock.Mock()
        app.root = mock.Mock()
        LauncherApp._poll_status(app)
        app._rollback_failed_direct_responder.assert_called_once_with(
            app.saved["direct_link"])
        app.root.after.assert_called_once_with(600, app._poll_status)

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
