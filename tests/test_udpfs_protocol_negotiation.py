import importlib.util
import json
import pathlib
import struct
import sys
import threading
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
UDPFS_DIR = ROOT / "udpfs_server"
if str(UDPFS_DIR) not in sys.path:
    sys.path.insert(0, str(UDPFS_DIR))
SPEC = importlib.util.spec_from_file_location(
    "ps2servers_core", UDPFS_DIR / "ps2servers_core.py")
CORE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CORE)


class NegotiationTests(unittest.TestCase):
    def test_shared_handshake_fixtures(self):
        cases = json.loads((ROOT / "conformance" / "fixtures" / "handshake_cases.json").read_text())
        for case in cases:
            with self.subTest(case=case["name"]):
                self.assertEqual(
                    CORE.classify_profile(case["discovery"], case["first_data"]),
                    case["profile"],
                )

    def test_standard_sequence(self):
        self.assertEqual(CORE.classify_profile(0, 0), CORE.PROFILE_STANDARD)

    def test_modulo_fresh_sequence(self):
        self.assertEqual(CORE.classify_profile(0, 1), CORE.PROFILE_MODULO)

    def test_modulo_resumed_sequence(self):
        self.assertEqual(CORE.classify_profile(7, 8), CORE.PROFILE_MODULO)

    def test_modulo_wraparound(self):
        self.assertEqual(CORE.classify_profile(4095, 0), CORE.PROFILE_MODULO)

    def test_unrelated_sequence_falls_back_to_standard(self):
        self.assertEqual(CORE.classify_profile(7, 0), CORE.PROFILE_STANDARD)

    def test_default_protocol_mode_is_auto(self):
        parser = CORE.build_parser()
        args = parser.parse_args(["--root-dir", str(ROOT)])
        self.assertEqual(args.protocol_mode, "auto")

    def test_canonical_inform_uses_data_socket_and_zero_reserved_word(self):
        data_socket = object()
        discovery_socket = object()
        destination = ("192.0.2.10", 0xF5F6)
        sent = []
        server = CORE.AutoUdpfsServer.__new__(CORE.AutoUdpfsServer)
        server.dsock = data_socket
        server.sock = discovery_socket
        server._send_specific = lambda sock, packet, addr: sent.append(
            (sock, packet, addr))

        server._canonical_inform(destination)

        self.assertEqual(sent, [(
            data_socket,
            struct.pack("<HHH", 0x0011, CORE.UDPRDMA_SVC_UDPFS, 0),
            destination,
        )])





class _Handle:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class DiscoveryLifecycleTests(unittest.TestCase):
    def active_session(self, sequence):
        server = CORE.AutoUdpfsServer.__new__(CORE.AutoUdpfsServer)
        addr = ("192.0.2.50", 50000)
        server.protocol_mode = CORE.PROFILE_AUTO if hasattr(CORE, "PROFILE_AUTO") else "auto"
        server.bd_fh = None
        server.stats = {"discovery": 0}
        server.verbose = False
        server.sessions_lock = threading.Lock()
        server._maybe_answer_status = lambda data, addr: False
        server._print_event = lambda message: None
        informs = []
        server._canonical_inform = lambda peer: informs.append(peer)
        server._schedule_fallback = lambda sess, generation: None
        sess = CORE.Session(server, addr)
        server._init_compat(sess)
        server.sessions = {addr: sess}
        server._get_or_create_session = lambda peer: sess
        handle = _Handle()
        sess.handles[81] = handle
        sess.next_handle = 82
        sess.protocol_profile = CORE.PROFILE_STANDARD
        sess.rx_streaming = True
        sess.rx_seq_nr_expected = 2213
        sess.tx_seq_nr = 9
        sess.tx_buffer = [(8, b"old")]
        sess.discovery_sequence = 11
        sess.handshake_generation = 4
        sess.response_socket = CORE.SOCKET_DISCOVERY
        sess.last_activity = time.monotonic() - 30.0
        packet = (CORE.Header(packet_type=CORE.PacketType.DISCOVERY, seq_nr=sequence).pack()
                  + CORE.DiscHeader(service_id=CORE.UDPRDMA_SVC_UDPFS, port=0).pack())
        return server, sess, handle, packet, informs

    def test_active_discovery_preserves_handles_and_transport(self):
        for sequence in (0, 7, 4095):
            with self.subTest(sequence=sequence):
                server, sess, handle, packet, informs = self.active_session(sequence)
                server._handle_discovery(packet, sess.addr)
                self.assertFalse(handle.closed)
                self.assertIs(sess.handles[81], handle)
                self.assertEqual(sess.next_handle, 82)
                self.assertEqual(sess.rx_seq_nr_expected, 2213)
                self.assertEqual(sess.tx_seq_nr, 9)
                self.assertEqual(sess.tx_buffer, [(8, b"old")])
                self.assertEqual(sess.discovery_sequence, 11)
                self.assertEqual(sess.handshake_generation, 4)
                self.assertEqual(sess.response_socket, CORE.SOCKET_DISCOVERY)
                self.assertEqual(len(informs), 1)
                if sequence == 0:
                    self.assertGreater(sess.pending_zero_discovery_at, 0.0)
                else:
                    self.assertEqual(sess.pending_zero_discovery_at, 0.0)


if __name__ == "__main__":
    unittest.main()
