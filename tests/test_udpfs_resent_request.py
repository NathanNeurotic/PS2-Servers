"""A request the console sends again mid-transfer must not stall the transfer.

The console re-sends a request when its 500 ms send timer fires before our ACK
reaches it. eliminator1403's log (WWE SmackDown! HCTP over UDPFS) shows what
that did while the reply was still going out:

    BREAD handle=5 sector=3721268 count=28 (14336 bytes)
      Window ACK retries exhausted, aborting transfer
      Duplicate seq=297, re-ACK + retransmit
      Retransmitted 8 packets from seq=1574
      NACK received, retransmit from seq=1582
    no request for 46s -- the console still answers ARP ...

The window wait pushed the copy back, and the next read returned it again, 200
times over, without ever reaching the console's real window ACK queued behind
it. Packets 1582+ were never sent, Neutrino gave up after its 30 s receive
timeout, and its UDPFS driver never sends again after that. In the final-ACK
wait the same copy looped forever.

Run:  python -m unittest tests.test_udpfs_resent_request -v
"""

import collections
import os
import queue
import sys
import threading
import types
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UDPFS = os.path.join(_ROOT, "udpfs_server")
if _UDPFS not in sys.path:
    sys.path.insert(0, _UDPFS)

import udpfs_server as srv  # noqa: E402

ADDR = ("192.168.0.245", 62966)


def _packet(seq_nr, ack, payload=b"", flags=srv.DataFlags.ACK):
    hdr = srv.Header(packet_type=srv.PacketType.DATA, seq_nr=seq_nr)
    body = srv.DataHeader(seq_nr_ack=ack, flags=flags, hdr_word_count=0,
                          data_byte_count=len(payload))
    return (hdr.pack() + body.pack() + payload, ADDR, "data")


REQUEST = b"\x00" * 16


class ResentRequestTests(unittest.TestCase):
    def setUp(self):
        # Mid-stream state from the log: request 297 accepted, reply starts
        # at server seq 1574, console has acknowledged up to 1573.
        self.sess = types.SimpleNamespace(
            queue=queue.Queue(), pushback=collections.deque(),
            tx_seq_nr=1574, tx_seq_nr_acked=1573, rx_seq_nr_expected=298,
            tx_buffer=[], tx_start_seq=0)
        server = object.__new__(srv.UdpfsServer)
        server._local = threading.local()
        server._local.session = self.sess
        server.verbose = True
        self.sent, self.events, self.reads = [], [], 0
        server._sendto = lambda packet, addr: self.sent.append(packet)
        server._print_event = self.events.append

        def counted_get(sess, timeout=None):
            self.reads += 1
            if self.reads > 50:
                raise AssertionError("the wait keeps re-reading one packet")
            return srv.UdpfsServer._queue_get(server, sess, timeout)

        server._queue_get = counted_get
        self.server = server

    def queue(self, *items):
        for item in items:
            self.sess.queue.put(item)

    def test_bread_reply_completes_despite_a_resent_request(self):
        self.queue(_packet(297, 1573, REQUEST),  # the copy, queued first
                   _packet(298, 1581),           # window ACK behind it
                   _packet(298, 1584))           # FIN ACK
        self.server._send_raw_data_with_header(ADDR, b"\x00" * 8, bytes(14336))

        data_packets = [p for p in self.sent if srv.DataHeader.unpack(p[2:6]).data_byte_count]
        seqs = [srv.Header.unpack(p).seq_nr for p in data_packets]
        request_acks = [p for p in self.sent if not srv.DataHeader.unpack(p[2:6]).data_byte_count]
        self.assertEqual(len(request_acks), 1, "re-ACK a request whose separate ACK was lost")
        self.assertEqual(srv.DataHeader.unpack(request_acks[0][2:6]).seq_nr_ack, 297)
        self.assertEqual(seqs, list(range(1574, 1585)),
                         "all 11 packets, once each, no retransmit")
        self.assertFalse(any("aborting" in e for e in self.events), self.events)
        self.assertFalse(self.sess.pushback, "the copy must be dropped")
        self.assertTrue(self.sess.queue.empty())

    def test_final_ack_wait_drops_a_resent_request(self):
        self.sess.tx_seq_nr = 1576  # FIN was 1575
        self.queue(_packet(297, 1573, REQUEST), _packet(298, 1575))
        self.assertTrue(self.server._wait_for_ack(ADDR, timeout=1.0))
        self.assertFalse(self.sess.pushback, "the copy must be dropped")
        self.assertTrue(self.sess.queue.empty(), "the FIN ACK was consumed")
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(srv.DataHeader.unpack(self.sent[0][2:6]).flags, srv.DataFlags.ACK)
        self.assertEqual(srv.DataHeader.unpack(self.sent[0][2:6]).seq_nr_ack, 297)

    def test_reply_payload_does_not_acknowledge_a_later_request(self):
        for fin, payload, hdr_size in ((False, b"1234", 0), (True, b"1234", 0), (True, bytes(12), 8)):
            with self.subTest(fin=fin, hdr_size=hdr_size):
                self.sent.clear()
                self.server._send_data_packet(ADDR, payload, fin=fin, hdr_size=hdr_size)
                header = srv.DataHeader.unpack(self.sent[0][2:6])
                self.assertFalse(header.flags & srv.DataFlags.ACK)
                self.assertEqual(bool(header.flags & srv.DataFlags.FIN), fin)
        self.sent.clear()
        self.server._send_data(ADDR, b"RPC reply")
        header = srv.DataHeader.unpack(self.sent[0][2:6])
        self.assertEqual(header.flags, srv.DataFlags.FIN)

    def test_a_new_request_ends_either_wait_once(self):
        """Anything else is the console moving on: hand it back, stop waiting."""
        self.sess.tx_seq_nr = 1576
        for flags in (srv.DataFlags.ACK, 0):
            for wait in (self.server._wait_for_window_ack,
                         lambda addr: self.server._wait_for_ack(addr, 1.0)):
                with self.subTest(flags=flags, wait=wait):
                    self.sess.pushback.clear()
                    self.reads = 0
                    self.queue(_packet(298, 1573, REQUEST, flags))
                    self.assertTrue(wait(ADDR))
                    self.assertEqual(self.reads, 1)
                    self.assertEqual(len(self.sess.pushback), 1)

    def test_a_new_request_mid_window_stops_the_transfer(self):
        self.queue(_packet(298, 1573, REQUEST))
        self.server._send_raw_data_with_header(ADDR, b"\x00" * 8, bytes(14336))
        self.assertEqual(len(self.sent), 8, "only the first window went out")
        self.assertEqual(self.reads, 1)
        self.assertEqual(len(self.sess.pushback), 1, "left for the worker")
        self.assertFalse(any("aborting" in e for e in self.events), self.events)

    def test_nack_for_next_unsent_packet_releases_full_window(self):
        # The receiver got the whole window, but its ACK was lost. A duplicate
        # prompts a NACK for the first packet it has not seen: not yet sent.
        # Waiting for another ACK here deadlocks the sender and receiver.
        for start, with_header in ((776, True), (4092, True), (776, False), (4092, False)):
            with self.subTest(start=start, with_header=with_header):
                self.sent.clear()
                self.sess.pushback.clear()
                self.reads = 0
                self.sess.tx_seq_nr = start
                self.sess.tx_seq_nr_acked = (start - 1) & 0xFFF

                def receiver(packet, addr):
                    self.sent.append(packet)
                    seq = srv.Header.unpack(packet).seq_nr
                    if seq == (start + 7) & 0xFFF:
                        self.queue(_packet(298, (start + 8) & 0xFFF, flags=0))
                    elif seq == (start + 11) & 0xFFF:
                        self.queue(_packet(298, seq))

                def receive_now(sess, timeout=None):
                    try:
                        return sess.queue.get_nowait()
                    except queue.Empty:
                        self.fail("sender waits instead of sending the packet requested by NACK")

                self.server._sendto = receiver
                self.server._queue_get = receive_now
                if with_header:
                    self.server._send_raw_data_with_header(ADDR, b"\x00" * 8, bytes(16384))
                else:
                    self.server._send_raw_data(ADDR, bytes(16384))
                seqs = [srv.Header.unpack(p).seq_nr for p in self.sent]
                self.assertEqual(seqs, [(start + i) & 0xFFF for i in range(12)])
                self.assertTrue(self.sess.queue.empty())


if __name__ == "__main__":
    unittest.main()
