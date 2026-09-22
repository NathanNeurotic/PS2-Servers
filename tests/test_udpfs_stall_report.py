"""--verbose stall report: when a peer with open files goes quiet mid-stream, say
once whether the console is still sending, still answers ARP, or is gone.

A game that hangs mid-load used to leave a log that simply stopped, which reads
the same whether the console stopped asking or its network side died.
"""

import importlib.util
import pathlib
import struct
import subprocess
import sys
import threading
import time
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
UDPFS_DIR = ROOT / "udpfs_server"
if str(UDPFS_DIR) not in sys.path:
    sys.path.insert(0, str(UDPFS_DIR))
SPEC = importlib.util.spec_from_file_location(
    "udpfs_server_stall_report", UDPFS_DIR / "udpfs_server.py")
UDPFS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(UDPFS)

IP = "192.168.0.245"


class _NullSocket:
    def __init__(self, *args):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def sendto(self, data, addr):
        pass


class _ImmediateThread:
    def __init__(self, target=None, name=None, daemon=None):
        self.target = target

    def start(self):
        self.target()


class ConsoleAnswersArpTests(unittest.TestCase):
    def _probe(self, stdout, os_name, run=None):
        run = run or mock.Mock(
            return_value=subprocess.CompletedProcess([], 0, stdout, ""))
        with mock.patch.object(UDPFS.socket, "socket", _NullSocket), \
                mock.patch.object(UDPFS.time, "sleep"), \
                mock.patch.object(UDPFS.subprocess, "run", run), \
                mock.patch.object(UDPFS.os, "name", os_name):
            return UDPFS.console_answers_arp(IP), run

    def test_windows_neighbour_states(self):
        for stdout, want in (("Reachable\r\n", True), ("Unreachable\r\n", False),
                             ("Incomplete\r\n", False), ("Stale\r\n", None), ("", None),
                             ("Unreachable\r\nReachable\r\n", True)):  # two interfaces
            got, run = self._probe(stdout, "nt")
            self.assertEqual(got, want, stdout)
            self.assertIn("Get-NetNeighbor", " ".join(run.call_args[0][0]))

    def test_linux_neighbour_states(self):
        line = IP + " dev eth0 lladdr 00:11:22:33:44:55 "
        for state, want in (("REACHABLE", True), ("FAILED", False),
                            ("INCOMPLETE", False), ("STALE", None)):
            got, run = self._probe(line + state + "\n", "posix")
            self.assertEqual(got, want, state)
            self.assertEqual(run.call_args[0][0], ["ip", "neigh", "show", IP])

    def test_unknown_when_the_os_cannot_tell(self):
        got, _ = self._probe("", "posix", run=mock.Mock(side_effect=FileNotFoundError))
        self.assertIsNone(got)

        class _NoNetwork(_NullSocket):
            def sendto(self, data, addr):
                raise OSError("network unreachable")

        with mock.patch.object(UDPFS.socket, "socket", _NoNetwork):
            self.assertIsNone(UDPFS.console_answers_arp(IP))


def _server(verbose=True):
    server = UDPFS.UdpfsServer.__new__(UDPFS.UdpfsServer)
    server.verbose = verbose
    server.sessions = {}
    server.sessions_lock = threading.RLock()
    server.session_timeout = 3600.0
    server._last_sweep = 0.0
    server.lines = []
    server._print_event = server.lines.append
    return server


def _session(request_age, packet_age, handles=True):
    now = time.monotonic()
    sess = type("Sess", (), {})()
    sess.handles = {12: object()} if handles else {}
    sess.last_request = now - request_age
    sess.last_activity = now - packet_age
    sess.stall_reported = False
    return sess


class StallReportTests(unittest.TestCase):
    def _sweep(self, server, arp=True):
        probe = mock.Mock(return_value=arp)
        server._last_sweep = 0.0
        with mock.patch.object(UDPFS, "console_answers_arp", probe), \
                mock.patch.object(UDPFS.threading, "Thread", _ImmediateThread):
            server._sweep_idle_sessions()
        return probe

    def test_silent_console_gets_one_verdict_per_silence(self):
        server = _server()
        server.sessions[(IP, 62966)] = _session(60, 60)
        self._sweep(server, arp=True)
        self._sweep(server, arp=True)
        self.assertEqual(len(server.lines), 1, server.lines)
        self.assertIn("no request for 60s", server.lines[0])
        self.assertIn("still answers ARP", server.lines[0])

    def test_dead_console(self):
        server = _server()
        server.sessions[(IP, 62966)] = _session(60, 60)
        self._sweep(server, arp=False)
        self.assertIn("no longer answers ARP", server.lines[0])

    def test_console_still_sending_is_not_probed(self):
        server = _server()
        server.sessions[(IP, 62966)] = _session(60, 1)
        probe = self._sweep(server)
        probe.assert_not_called()
        self.assertIn("still sending", server.lines[0])

    def test_quiet_only_when_it_matters(self):
        cases = {
            "not verbose": (_server(verbose=False), _session(60, 60)),
            "no open files": (_server(), _session(60, 60, handles=False)),
            "recent request": (_server(), _session(5, 5)),
        }
        never = _session(60, 60)
        never.last_request = 0.0
        cases["no request yet"] = (_server(), never)
        for name, (server, sess) in cases.items():
            server.sessions[(IP, 62966)] = sess
            probe = self._sweep(server)
            probe.assert_not_called()
            self.assertEqual(server.lines, [], name)

    def test_an_accepted_request_rearms_the_report(self):
        server = _server()
        sess = _session(60, 60)
        sess.stall_reported = True
        sess.rx_seq_nr_expected = 7
        sess.tx_seq_nr_acked = 0
        sess.tx_buffer = []
        server._local = threading.local()
        server._local.session = sess
        server._send_ack = lambda addr, is_ack: None
        server._handle_open = lambda addr, payload: None
        payload = b"\x10\x00\x00\x00"  # OPEN_REQ
        packet = (UDPFS.Header(packet_type=UDPFS.PacketType.DATA, seq_nr=7).pack()
                  + struct.pack("<I", len(payload) << 18) + payload)

        before = time.monotonic()
        server._handle_data(packet, (IP, 62966))

        self.assertGreaterEqual(sess.last_request, before)
        self.assertFalse(sess.stall_reported)


if __name__ == "__main__":
    unittest.main()
