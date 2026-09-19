"""Regression: preserve active streaming session on seq0 DISCOVERY <1s.

Go parity: native/ps2servers-edge/internal/udpfs/negotiation.go:98
Python bug was: only nonzero discovery guarded an active stream.

This test mocks the minimal server state and injects DISCOVERY packets
without touching real sockets.
"""

import importlib.util
import io
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
    "ps2servers_core_handoff", UDPFS_DIR / "ps2servers_core.py")
CORE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CORE)


def _pack_discovery(seq_nr: int) -> bytes:
    hdr = CORE.Header(packet_type=CORE.PacketType.DISCOVERY, seq_nr=seq_nr)
    disc = CORE.DiscHeader(service_id=CORE.UDPRDMA_SVC_UDPFS, port=0)
    return hdr.pack() + disc.pack()


def _pack_data(seq_nr: int, payload: bytes = b"\x10\x00\x00\x00") -> bytes:
    """Build one payload-bearing DATA packet; 0x10 is OPEN_REQ."""
    hdr = CORE.Header(packet_type=CORE.PacketType.DATA, seq_nr=seq_nr)
    data_header = (len(payload) & 0x3FFF) << 18
    return hdr.pack() + struct.pack("<I", data_header) + payload


def _exercise_payload_data(server, sess, addr, seq_nr):
    """Run the real inherited DATA handler and return consumption/ACK details."""
    consumed = []
    server._local = threading.local()
    server._local.session = sess
    server._handle_open = lambda a, payload: consumed.append((a, payload))
    server._sent.clear()

    server._handle_data(_pack_data(seq_nr), addr)

    ack_packets = []
    for sock, packet, sent_addr in server._sent:
        if len(packet) != 6:
            continue
        hdr = CORE.Header.unpack(packet)
        if hdr.packet_type != CORE.PacketType.DATA:
            continue
        raw = struct.unpack("<I", packet[2:6])[0]
        ack_packets.append({
            "socket": sock,
            "addr": sent_addr,
            "ack_sequence": raw & 0xFFF,
            "flags": (raw >> 12) & 0x3,
        })
    return consumed, ack_packets


def _exercise_bread(server, sess, addr, handle, sector=0, count=1):
    """Issue a real inherited BREAD against an existing server-side handle."""
    server._local = threading.local()
    server._local.session = sess
    server.stats.setdefault("bread", 0)
    server.stats.setdefault("bytes_read", 0)
    server._update_status = lambda: None
    replies = []
    server._send_read_result = (
        lambda a, result, data: replies.append((a, result, data)))
    payload = struct.pack(
        "<BBHiII", 0x28, 0, count, handle,
        sector & 0xFFFFFFFF, (sector >> 32) & 0xFFFFFFFF)
    server._handle_bread(addr, payload)
    return replies


class _FakeHandle:
    def __init__(self, data=b""):
        self.closed = False
        self.obj = io.BytesIO(data)
        self.is_dir = False

    def close(self):
        self.closed = True
        self.obj.close()


def _make_server(protocol_mode="auto"):
    # Use __new__ to avoid binding real sockets; manually stub required attrs.
    server = CORE.AutoUdpfsServer.__new__(CORE.AutoUdpfsServer)
    server.protocol_mode = protocol_mode
    server.verbose = False
    server.single_port = False
    server.fallback_interval = 0.25
    server.sessions = {}
    server.sessions_lock = threading.RLock()
    server.stats = {"discovery": 0}
    server.sock = object()
    server.dsock = object()
    server.send_lock = threading.RLock()
    server.tx_delay_s = 0.0
    server.bd_fh = None
    server.max_transfer_bytes = 4 * 1024 * 1024
    server.server_name = "test"
    server.share_names = []
    server.modulo_compat = (protocol_mode == CORE.PROFILE_MODULO)
    server._print_event = lambda msg: None
    server._maybe_answer_status = lambda data, addr: False
    # Prevent background fallback thread in tests
    server._schedule_fallback = lambda sess, gen: None
    # Capture sends: canonical INFORM uses _send_specific(dsock, packet, addr)
    server._sent = []
    server._send_specific = lambda sock, packet, addr: server._sent.append((sock, packet, addr))
    return server


def _make_active_session(addr, *, quiet_seconds=0.3, handle_id=1):
    # Build a minimal session-like object with the fields _reset_session_state and discovery logic expect.
    now = time.monotonic()
    handle = _FakeHandle()
    sess = type("Sess", (), {})()
    sess.addr = addr
    sess.handles = {handle_id: handle, CORE.BLOCK_DEVICE_HANDLE: _FakeHandle()}
    # Preserve reference for later assertion that object itself survived (not recreated)
    sess._handle_ref = handle
    sess.next_handle = 7
    sess.tx_seq_nr = 42
    sess.tx_seq_nr_acked = 41
    sess.rx_seq_nr_expected = 99
    sess.tx_buffer = [(10, b"data")]
    sess.tx_start_seq = 10
    sess.write_handle = 5
    sess.write_is_block = False
    sess.write_sector_nr = 0
    sess.write_sector_count = 0
    sess.write_data = bytearray(b"hello")
    sess.write_total_chunks = 2
    sess.write_received_chunks = 2
    sess.rx_streaming = True
    sess.protocol_profile = CORE.PROFILE_STANDARD
    sess.response_socket = CORE.SOCKET_DATA
    sess.fallback_sent = True
    sess.first_data_seen = True
    sess.discovery_sequence = 7
    sess.handshake_generation = 3
    sess.pending_zero_discovery_at = 0.0
    sess.last_activity = now - quiet_seconds
    sess.compat_lock = threading.RLock()
    sess.ingress = None
    return sess


class LiveDiscoveryHandoffTests(unittest.TestCase):
    def test_active_stream_recent_seq0_preserves_session(self):
        """ACTIVE + seq0 + quiet<1s -> canonical INFORM, no reset, state preserved."""
        addr = ("192.0.2.10", 5000)
        server = _make_server(protocol_mode="auto")
        sess = _make_active_session(addr, quiet_seconds=0.3)
        # Snapshot state before discovery
        orig_next = sess.next_handle
        orig_tx = sess.tx_seq_nr
        orig_rx = sess.rx_seq_nr_expected
        orig_disc = sess.discovery_sequence
        orig_gen = sess.handshake_generation
        orig_tx_buf = list(sess.tx_buffer)
        orig_profile = sess.protocol_profile

        # Need existing entry for logging path
        server.sessions[addr] = sess
        # Stub _get_or_create_session to return our sess (avoid real Session creation)
        server._get_or_create_session = lambda a: sess

        canonical_calls = []
        reset_calls = []
        orig_canonical = CORE.AutoUdpfsServer._canonical_inform
        orig_reset = CORE.AutoUdpfsServer._reset_session_state

        def tracking_canonical(a):
            canonical_calls.append(a)
            return orig_canonical(server, a)

        def tracking_reset(s, profile):
            reset_calls.append(profile)
            return orig_reset(server, s, profile)

        server._canonical_inform = tracking_canonical
        server._reset_session_state = tracking_reset

        # Use monotonic patch to make quiet deterministic (<1s)
        real_mono = time.monotonic
        try:
            # sess.last_activity was now-0.3, so make monotonic return now
            fake_now = sess.last_activity + 0.3
            time.monotonic = lambda: fake_now
            server._handle_discovery(_pack_discovery(0), addr)
        finally:
            time.monotonic = real_mono

        # Assertions per spec
        self.assertEqual(len(canonical_calls), 1, "canonical INFORM should be sent once")
        self.assertEqual(canonical_calls[0], addr)
        self.assertEqual(len(reset_calls), 0, "_reset_session_state must NOT be called")
        # Handle and session state preserved
        self.assertIn(1, sess.handles)
        self.assertIs(sess.handles[1], sess._handle_ref)
        self.assertFalse(sess._handle_ref.closed, "active handle must remain open")
        self.assertEqual(sess.next_handle, orig_next)
        self.assertTrue(sess.rx_streaming)
        self.assertEqual(sess.tx_seq_nr, orig_tx)
        self.assertEqual(sess.rx_seq_nr_expected, orig_rx)
        self.assertEqual(sess.discovery_sequence, orig_disc)
        self.assertEqual(sess.handshake_generation, orig_gen)
        self.assertEqual(sess.tx_buffer, orig_tx_buf)
        self.assertEqual(sess.protocol_profile, orig_profile)
        self.assertGreater(sess.pending_zero_discovery_at, 0.0)
        # Verify packet was sent via data socket (dsock) with seq 1, port 0
        self.assertEqual(len(server._sent), 1)
        sock, packet, sent_addr = server._sent[0]
        self.assertIs(sock, server.dsock)
        self.assertEqual(sent_addr, addr)
        # Packet is INFORM seq1, service 0xF5F5, port 0
        self.assertEqual(packet, struct.pack("<HHH", 0x0011, CORE.UDPRDMA_SVC_UDPFS, 0))

    def test_stale_seq0_after_quiet_ge_1s_does_reset(self):
        """STALE + seq0 + quiet>=1s -> real reset/replacement behavior (handle closed)."""
        addr = ("192.0.2.10", 5001)
        server = _make_server(protocol_mode="auto")
        sess = _make_active_session(addr, quiet_seconds=1.5)
        # Use real _reset_session_state, do NOT mock it away
        server.sessions[addr] = sess
        server._get_or_create_session = lambda a: sess

        # Track canonical but allow real reset to run
        canonical_calls = []
        orig_canonical = CORE.AutoUdpfsServer._canonical_inform

        def tracking_canonical(a):
            canonical_calls.append(a)
            return orig_canonical(server, a)

        server._canonical_inform = tracking_canonical
        # Do NOT replace _reset_session_state; let real implementation run
        # Ensure fallback not interfering
        real_mono = time.monotonic
        try:
            fake_now = sess.last_activity + 1.5
            time.monotonic = lambda: fake_now
            server._handle_discovery(_pack_discovery(0), addr)
        finally:
            time.monotonic = real_mono

        # After reset, handle 1 must be gone/closed, rx_streaming cleared, tx seq reset
        self.assertNotIn(1, sess.handles, "stale seq0 must close active handle 1")
        self.assertTrue(sess._handle_ref.closed)
        # Block device handle retained if present
        self.assertIn(CORE.BLOCK_DEVICE_HANDLE, sess.handles)
        self.assertEqual(sess.next_handle, 1)
        self.assertFalse(sess.rx_streaming)
        self.assertEqual(sess.tx_seq_nr, 0)
        self.assertEqual(sess.rx_seq_nr_expected, 0)
        # After reset, a canonical INFORM is sent for the replacement handshake
        self.assertEqual(len(canonical_calls), 1)
        # Also sent list should have one inform
        self.assertEqual(len(server._sent), 1)

    def test_active_stream_nonzero_still_preserved_without_inform(self):
        """Existing nonzero guard must remain: active + seq!=0 + quiet<2s -> no reset, no inform."""
        addr = ("192.0.2.10", 5002)
        server = _make_server(protocol_mode="auto")
        sess = _make_active_session(addr, quiet_seconds=0.5)
        server.sessions[addr] = sess
        server._get_or_create_session = lambda a: sess

        canonical_calls = []
        reset_calls = []
        orig_canonical = CORE.AutoUdpfsServer._canonical_inform
        orig_reset = CORE.AutoUdpfsServer._reset_session_state
        server._canonical_inform = lambda a: canonical_calls.append(a) or orig_canonical(server, a)
        server._reset_session_state = lambda s, p: reset_calls.append(p) or orig_reset(server, s, p)

        real_mono = time.monotonic
        try:
            fake_now = sess.last_activity + 0.5
            time.monotonic = lambda: fake_now
            server._handle_discovery(_pack_discovery(7), addr)
        finally:
            time.monotonic = real_mono

        self.assertEqual(len(canonical_calls), 0)
        self.assertEqual(len(reset_calls), 0)
        self.assertIn(1, sess.handles)
        self.assertTrue(sess.rx_streaming)
        self.assertEqual(len(server._sent), 0)

    def test_modulo_mode_unchanged(self):
        """Modulo mode must not be affected by new seq0 guard (early modulo branch)."""
        addr = ("192.0.2.10", 5003)
        server = _make_server(protocol_mode=CORE.PROFILE_MODULO)
        sess = _make_active_session(addr, quiet_seconds=0.2)
        # Modulo branch uses tx_seq_nr and _last_disc etc.
        sess.tx_seq_nr = 5
        server.sessions[addr] = sess
        server._get_or_create_session = lambda a: sess
        # Enable counting of _compatibility_inform vs _canonical
        # In modulo mode, _handle_discovery always sends compatibility inform via _send_specific(sock)
        real_mono = time.monotonic
        try:
            fake_now = sess.last_activity + 0.2
            time.monotonic = lambda: fake_now
            server._handle_discovery(_pack_discovery(0), addr)
        finally:
            time.monotonic = real_mono

        # Modulo should have sent *something* (not our canonical preservation path alone)
        # It should have used sock (discovery socket path) with payload, not just dsock canonical
        # Just verify no reset logic interfered: handle still? In modulo path, reset is NOT called either,
        # but it goes through modulo's own inform. So handles should still survive (no reset).
        # The key is that modulo still processed as modulo, not as preserved auto.
        self.assertIn(1, sess.handles)
        self.assertEqual(len(server._sent), 1)
        # Modulo inform is sent on discovery sock and includes payload (longer than 6 bytes)
        # Our canonical inform is exactly 6 bytes; modulo compat is longer (includes info payload)
        sock, packet, sent_addr = server._sent[0]
        self.assertIs(sock, server.sock)
        self.assertGreater(len(packet), 6)

    def test_verbose_preservation_logs(self):
        """Verbose mode should log preservation event."""
        addr = ("192.0.2.10", 5004)
        server = _make_server(protocol_mode="standard")
        server.verbose = True
        sess = _make_active_session(addr, quiet_seconds=0.4)
        server.sessions[addr] = sess
        server._get_or_create_session = lambda a: sess
        logs = []
        server._print_event = lambda msg: logs.append(msg)
        real_mono = time.monotonic
        try:
            fake_now = sess.last_activity + 0.4
            time.monotonic = lambda: fake_now
            server._handle_discovery(_pack_discovery(0), addr)
        finally:
            time.monotonic = real_mono
        self.assertTrue(any("DISCOVERY seq=0 during active stream" in m for m in logs))
        self.assertTrue(any("defer replacement decision" in m for m in logs))

    def test_recent_seq0_then_old_expected_data_preserves_session(self):
        """Background seq0 discovery followed by the old stream stays live."""
        addr = ("192.0.2.10", 5006)
        server = _make_server(protocol_mode="auto")
        sess = _make_active_session(addr, quiet_seconds=0.2)
        sess.rx_seq_nr_expected = 2213
        server.sessions[addr] = sess
        server._get_or_create_session = lambda a: sess

        fake_now = sess.last_activity + 0.2
        real_mono = time.monotonic
        try:
            time.monotonic = lambda: fake_now
            server._handle_discovery(_pack_discovery(0), addr)
            self.assertGreater(sess.pending_zero_discovery_at, 0.0)

            reset_calls = []
            orig_reset = CORE.AutoUdpfsServer._reset_session_state
            server._reset_session_state = (
                lambda s, p: reset_calls.append(p) or orig_reset(server, s, p))
            server._compatibility_inform = lambda s: None
            consumed, ack_packets = _exercise_payload_data(
                server, sess, addr, 2213)
        finally:
            time.monotonic = real_mono

        self.assertEqual(reset_calls, [])
        self.assertIn(1, sess.handles)
        self.assertFalse(sess._handle_ref.closed)
        self.assertEqual(sess.rx_seq_nr_expected, 2214)
        self.assertEqual(sess.pending_zero_discovery_at, 0.0)
        self.assertEqual(len(consumed), 1, "resolving DATA must reach the request handler")
        self.assertEqual(len(ack_packets), 1, "resolving DATA must be ACKed exactly once")
        self.assertEqual(ack_packets[0]["ack_sequence"], 2213)
        self.assertEqual(ack_packets[0]["flags"] & 0x1, 0x1)
        self.assertEqual(ack_packets[0]["addr"], addr)

    def test_recent_seq0_then_modulo_data_replaces_old_session(self):
        """nuno trace: expected 2213, DISCOVERY 0, DATA 1 -> fresh Modulo."""
        addr = ("192.0.2.10", 5007)
        server = _make_server(protocol_mode="auto")
        sess = _make_active_session(addr, quiet_seconds=0.0)
        sess.rx_seq_nr_expected = 2213
        server.sessions[addr] = sess
        server._get_or_create_session = lambda a: sess

        fake_now = sess.last_activity
        real_mono = time.monotonic
        try:
            time.monotonic = lambda: fake_now
            server._handle_discovery(_pack_discovery(0), addr)
            server._compatibility_inform = (
                lambda s: setattr(s, "fallback_sent", True))
            consumed, ack_packets = _exercise_payload_data(
                server, sess, addr, 1)
        finally:
            time.monotonic = real_mono

        self.assertIn(1, sess.handles)
        self.assertFalse(sess._handle_ref.closed)
        self.assertEqual(sess.next_handle, 7)
        self.assertEqual(sess.protocol_profile, CORE.PROFILE_MODULO)
        self.assertEqual(sess.discovery_sequence, 0)
        self.assertEqual(sess.rx_seq_nr_expected, 2)
        self.assertEqual(sess.pending_zero_discovery_at, 0.0)
        self.assertEqual(len(consumed), 1, "replacement DATA must reach the request handler")
        self.assertEqual(len(ack_packets), 1, "replacement DATA must be ACKed exactly once")
        self.assertEqual(ack_packets[0]["ack_sequence"], 1)
        self.assertEqual(ack_packets[0]["flags"] & 0x1, 0x1)
        self.assertEqual(ack_packets[0]["addr"], addr)

    def test_recent_seq0_then_standard_data_replaces_old_session(self):
        """A fresh Standard client taking over the endpoint starts at DATA 0."""
        addr = ("192.0.2.10", 5008)
        server = _make_server(protocol_mode="auto")
        sess = _make_active_session(
            addr, quiet_seconds=0.1, handle_id=81)
        game_data = bytes((i & 0xFF) for i in range(1024))
        sess.handles[81] = _FakeHandle(game_data)
        sess._handle_ref = sess.handles[81]
        sess.next_handle = 82
        sess.rx_seq_nr_expected = 2213
        server.sessions[addr] = sess
        server._get_or_create_session = lambda a: sess

        fake_now = sess.last_activity + 0.1
        real_mono = time.monotonic
        try:
            time.monotonic = lambda: fake_now
            server._handle_discovery(_pack_discovery(0), addr)
            server._compatibility_inform = lambda s: None
            consumed, ack_packets = _exercise_payload_data(
                server, sess, addr, 0)
        finally:
            time.monotonic = real_mono

        self.assertIn(81, sess.handles)
        self.assertFalse(sess._handle_ref.closed)
        self.assertEqual(sess.next_handle, 82)
        self.assertEqual(sess.protocol_profile, CORE.PROFILE_STANDARD)
        self.assertEqual(sess.discovery_sequence, 0)
        self.assertEqual(sess.rx_seq_nr_expected, 1)
        self.assertEqual(sess.pending_zero_discovery_at, 0.0)
        self.assertEqual(len(consumed), 1, "replacement DATA must reach the request handler")
        self.assertEqual(len(ack_packets), 1, "replacement DATA must be ACKed exactly once")
        self.assertEqual(ack_packets[0]["ack_sequence"], 0)
        self.assertEqual(ack_packets[0]["flags"] & 0x1, 0x1)
        self.assertEqual(ack_packets[0]["addr"], addr)

        # Neutrino's FILEID backend now owns handle 81 across the loader
        # transition. Its first sector read must still resolve against the
        # pre-opened game file instead of failing EBADF after negotiation.
        replies = _exercise_bread(server, sess, addr, 81)
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0][0], addr)
        self.assertEqual(replies[0][1], 512)
        self.assertEqual(replies[0][2], game_data[:512])

    def test_standard_mode_preserves_too(self):
        """Preservation must be profile-agnostic (standard mode)."""
        addr = ("192.0.2.10", 5005)
        server = _make_server(protocol_mode=CORE.PROFILE_STANDARD)
        sess = _make_active_session(addr, quiet_seconds=0.8)
        sess.protocol_profile = CORE.PROFILE_STANDARD
        server.sessions[addr] = sess
        server._get_or_create_session = lambda a: sess
        reset_calls = []
        orig_reset = CORE.AutoUdpfsServer._reset_session_state
        server._reset_session_state = lambda s, p: reset_calls.append(p) or orig_reset(server, s, p)
        real_mono = time.monotonic
        try:
            fake_now = sess.last_activity + 0.8
            time.monotonic = lambda: fake_now
            server._handle_discovery(_pack_discovery(0), addr)
        finally:
            time.monotonic = real_mono
        self.assertEqual(len(reset_calls), 0)
        self.assertIn(1, sess.handles)


if __name__ == "__main__":
    unittest.main()
