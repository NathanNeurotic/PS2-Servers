"""Pin source-port handshake telemetry for the packaged Python Core path."""
import importlib.util
import pathlib
import sys
import threading
import unittest
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[1]
UDPFS_DIR = ROOT / "udpfs_server"
if str(UDPFS_DIR) not in sys.path:
    sys.path.insert(0, str(UDPFS_DIR))
SPEC = importlib.util.spec_from_file_location(
    "ps2servers_core_inform_telemetry", UDPFS_DIR / "ps2servers_core.py")
CORE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CORE)


class _Socket:
    def __init__(self, port, fail=False):
        self.port = port
        self.fail = fail
        self.sent = []

    def getsockname(self):
        return "0.0.0.0", self.port

    def sendto(self, data, addr):
        if self.fail:
            raise OSError(22, "simulated send failure")
        self.sent.append((data, addr))


class InformTelemetryTests(unittest.TestCase):
    def _server(self, port=57996, fail=False):
        srv = CORE.AutoUdpfsServer.__new__(CORE.AutoUdpfsServer)
        srv.verbose = True
        srv.send_lock = threading.RLock()
        srv.tx_delay_s = 0.0
        srv.dsock = _Socket(port, fail=fail)
        srv.sock = _Socket(62966)
        logs = []
        srv._print_event = logs.append
        return srv, logs

    def test_canonical_inform_logs_actual_udp_source_port(self):
        srv, logs = self._server()
        peer = ("192.0.2.10", 62966)
        srv._canonical_inform(peer)
        self.assertEqual(len(srv.dsock.sent), 1)
        packet, destination = srv.dsock.sent[0]
        self.assertEqual(destination, peer)
        self.assertEqual(packet, bytes.fromhex("1100f5f50000"))
        self.assertTrue(any("INFORM sendto OK" in line
                            and "source-port=57996" in line
                            and "delivery is not confirmed" in line
                            for line in logs))

    def test_fallback_inform_logs_discovery_port(self):
        srv, logs = self._server()
        srv._info_payload = lambda: b"compat"
        peer = ("192.0.2.10", 62966)
        sess = SimpleNamespace(
            addr=peer, tx_seq_nr=0, tx_seq_nr_acked=0, fallback_sent=False)
        srv._compatibility_inform(sess)
        self.assertEqual(len(srv.sock.sent), 1)
        self.assertTrue(any("INFORM sendto OK" in line
                            and "source-port=62966" in line
                            for line in logs))

    def test_failed_inform_is_visible_even_without_verbose(self):
        srv, logs = self._server(fail=True)
        srv.verbose = False
        with self.assertRaises(OSError):
            srv._canonical_inform(("192.0.2.10", 62966))
        self.assertTrue(any("INFORM sendto FAILED" in line
                            and "source-port=57996" in line
                            for line in logs))

    def test_ordinary_data_does_not_spam_inform_logging(self):
        srv, logs = self._server()
        srv._send_specific(srv.dsock, bytes.fromhex("020000000000"),
                           ("192.0.2.10", 62966))
        self.assertEqual(logs, [])


if __name__ == "__main__":
    unittest.main()
