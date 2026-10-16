"""Reordered ACKs must not rewind a live send or postpone its retry timer."""

import collections
import io
import pathlib
import queue
import sys
import threading
import types
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "udpfs_server"))
import udpfs_server as srv
import ps2servers_core as core

ADDR = ("192.168.0.10", 62966)


def packet(ack, flags=srv.DataFlags.ACK):
    return (srv.Header(packet_type=srv.PacketType.DATA, seq_nr=298).pack()
            + srv.DataHeader(seq_nr_ack=ack, flags=flags, hdr_word_count=0,
                             data_byte_count=0).pack(), ADDR, "data")


class AckProgressTests(unittest.TestCase):
    def server(self, cls=srv.UdpfsServer, acked=100, next_seq=109):
        server = object.__new__(cls)
        server._local = threading.local()
        sess = types.SimpleNamespace(
            queue=queue.Queue(), pushback=collections.deque(),
            tx_seq_nr=next_seq, tx_seq_nr_acked=acked, rx_seq_nr_expected=298,
            tx_buffer=[(seq & 0xFFF, b"data") for seq in range(acked + 1, acked + 9)],
            compat_lock=threading.RLock(), ingress="data", rx_streaming=True,
            protocol_profile=core.PROFILE_STANDARD, pending_zero_discovery_at=0.0)
        server._local.session = sess
        server.verbose = False
        server.protocol_mode = "auto"
        server._sendto = lambda *args: None
        server._print_event = lambda *args: None
        return server, sess

    def test_reordered_ack_does_not_rewind_packaged_or_legacy_worker(self):
        for cls in (srv.UdpfsServer, core.AutoUdpfsServer):
            for acked, next_seq, stale in ((100, 109, 99), (0, 9, 4095)):
                with self.subTest(cls=cls, acked=acked):
                    server, sess = self.server(cls, acked, next_seq)
                    before = list(sess.tx_buffer)
                    server._handle_data(packet(stale)[0], ADDR)
                    self.assertEqual(sess.tx_seq_nr_acked, acked)
                    self.assertEqual(sess.tx_buffer, before)

    def test_ack_for_unsent_data_does_not_discard_pending_reply(self):
        server, sess = self.server()
        before = list(sess.tx_buffer)
        server._handle_data(packet(109)[0], ADDR)
        self.assertEqual(sess.tx_seq_nr_acked, 100)
        self.assertEqual(sess.tx_buffer, before)

    def test_stale_nack_does_not_rewind_worker(self):
        server, sess = self.server()
        server._handle_data(packet(100, flags=0)[0], ADDR)
        self.assertEqual(sess.tx_seq_nr_acked, 100)

    def test_wraparound_ack_advances_and_prunes(self):
        server, sess = self.server(acked=4093, next_seq=6)
        server._handle_data(packet(1)[0], ADDR)
        self.assertEqual(sess.tx_seq_nr_acked, 1)
        self.assertEqual([seq for seq, _ in sess.tx_buffer], [2, 3, 4, 5])

    def test_stale_traffic_cannot_extend_either_wait_forever(self):
        for final in (False, True):
            for flags in (srv.DataFlags.ACK, 0):
                with self.subTest(final=final, flags=flags):
                    server, sess = self.server()
                    clock = [0.0]
                    reads = []

                    def receive(session, timeout=None):
                        reads.append(timeout)
                        if len(reads) > 10:
                            self.fail("stale packets restarted the full wait timeout")
                        clock[0] += 0.03
                        return packet(99, flags)

                    server._queue_get = receive
                    server._retransmit_from = lambda *args: None
                    with mock.patch.object(srv.time, "monotonic", side_effect=lambda: clock[0]):
                        if final:
                            self.assertFalse(server._wait_for_ack(ADDR, timeout=0.1))
                        else:
                            self.assertFalse(server._wait_for_window_ack(ADDR))
                    self.assertLessEqual(len(reads), 4)
                    self.assertEqual(sess.tx_seq_nr_acked, 100)
                    self.assertLess(reads[-1], reads[0])

    def test_stream_completes_after_reordered_window_ack(self):
        for start in (101, 4092):
            with self.subTest(start=start):
                server, sess = self.server(acked=(start - 1) & 0xFFF,
                                           next_seq=start)
                sess.tx_buffer = []
                sess.queue.put(packet((start + 5) & 0xFFF))
                sess.queue.put(packet((start + 3) & 0xFFF))  # delayed older ACK
                sess.queue.put(packet((start + 11) & 0xFFF))
                sent = []
                server._sendto = lambda data, addr: sent.append(data)
                self.assertTrue(server._send_raw_data_with_header(
                    ADDR, bytes(8), bytes(16384)))
                self.assertEqual([srv.Header.unpack(p).seq_nr for p in sent],
                                 [(start + i) & 0xFFF for i in range(12)])
                self.assertEqual(sess.tx_seq_nr_acked, (start + 11) & 0xFFF)
                self.assertFalse(sess.tx_buffer)

    def test_last_bread_records_fin_confirmation_separately_from_read(self):
        import struct
        for confirmed in (True, False):
            with self.subTest(confirmed=confirmed):
                server, sess = self.server(next_seq=101)
                sess.tx_buffer = []
                sess.handles = {33: types.SimpleNamespace(
                    is_dir=False, obj=io.BytesIO(bytes(96 * 512)))}
                server.stats = collections.Counter()
                server.max_transfer_bytes = 1024 * 1024
                server._update_status = lambda: None
                if confirmed:
                    sess.queue.put(packet(102))  # 2048-byte reply has two packets
                else:
                    server._queue_get = mock.Mock(side_effect=queue.Empty)
                with mock.patch.object(srv, 'MAX_WINDOW_RETRIES', 0):
                    server._handle_bread(ADDR, struct.pack('<BBHiII', 0, 0, 4, 33, 88, 0))
                self.assertEqual(sess.last_bread_reply, dict(
                    handle=33, sector=88, requested=2048, read=2048,
                    completion='fin-acked' if confirmed else 'unconfirmed'))


if __name__ == "__main__":
    unittest.main()
