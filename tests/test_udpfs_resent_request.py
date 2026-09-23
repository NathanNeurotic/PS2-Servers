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

        seqs = [srv.Header.unpack(p).seq_nr for p in self.sent]
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
                    wait(ADDR)
                    self.assertEqual(self.reads, 1)
                    self.assertEqual(len(self.sess.pushback), 1)


if __name__ == "__main__":
    unittest.main()
