"""Reaping a session mid-transfer must be quiet, not an exception.

Session.shutdown() enqueues a bare ``None`` to wake a worker blocked on
``queue.get``. Three places consume that queue. ``Session._run`` checked the
item before unpacking it and handled the sentinel correctly; the two ACK waits
unpacked first and only then asked whether it was ``None`` -- so their guards
could never run, and the sentinel raised

    TypeError: cannot unpack non-iterable NoneType object

which unwound the whole handler stack and was logged as "session error".

The session still ended, because ``_run`` exits on ``_closing`` rather than on
the sentinel, so this was never a hang. It was worse in a quieter way: a log
line that reads like a fault, emitted on a path that is working exactly as
designed, on the one server whose logs are how a headless setup gets debugged.
The reap message right above it already explains that a peer went away.

Run:  python -m unittest tests.test_session_shutdown -v
"""

import os
import queue
import sys
import threading
import time
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_UDPFS = os.path.join(_ROOT, "udpfs_server")
if _UDPFS not in sys.path:
    sys.path.insert(0, _UDPFS)

import udpfs_server as srv  # noqa: E402


class _Waiter(srv.UdpfsServer):
    """Enough of a server to call the two ACK waits, with no sockets."""

    def __init__(self, sess):
        self._local = threading.local()
        self._local.session = sess
        self.verbose = False
        self.tx_buffer = []
        self.tx_seq_nr = 1
        self.tx_seq_nr_acked = 0
        self.retransmits = []

    # _session_prop proxies would need a real server; these two are all the
    # waits touch.
    @property
    def tx_buffer(self):
        return self._tx_buffer

    @tx_buffer.setter
    def tx_buffer(self, value):
        self._tx_buffer = value

    @property
    def tx_seq_nr(self):
        return self._tx_seq_nr

    @tx_seq_nr.setter
    def tx_seq_nr(self, value):
        self._tx_seq_nr = value

    @property
    def tx_seq_nr_acked(self):
        return self._tx_seq_nr_acked

    @tx_seq_nr_acked.setter
    def tx_seq_nr_acked(self, value):
        self._tx_seq_nr_acked = value

    def _print_event(self, msg):
        pass

    def _retransmit_from(self, addr, seq):
        self.retransmits.append(seq)


class _FakeSession:
    def __init__(self):
        self.queue = queue.Queue()


ADDR = ("192.0.2.10", 1234)


class ShutdownSentinelIsHandledByEveryConsumer(unittest.TestCase):
    """Each queue consumer must recognise the sentinel shutdown() enqueues."""

    def setUp(self):
        self.sess = _FakeSession()
        self.server = _Waiter(self.sess)

    def test_the_sentinel_is_a_bare_none(self):
        """Pins the shape the consumers below are written against.

        If shutdown() ever enqueues a tuple instead, these tests would keep
        passing while the real code broke the other way round.
        """
        sess = object.__new__(srv.Session)
        sess.queue = queue.Queue()
        sess._closing = False
        srv.Session.shutdown(sess)
        self.assertTrue(sess._closing)
        self.assertIsNone(sess.queue.get_nowait(),
                          "shutdown() no longer enqueues a bare None; the "
                          "guards in the ACK waits are written for that shape")

    def test_wait_for_ack_returns_instead_of_raising(self):
        self.sess.queue.put(None)
        # Before the fix this raised TypeError out of the unpack.
        self.assertFalse(
            self.server._wait_for_ack(ADDR, timeout=1.0),
            "the shutdown sentinel must end the wait as a plain failure")

    def test_wait_for_window_ack_returns_instead_of_raising(self):
        self.sess.queue.put(None)
        self.server._wait_for_window_ack(ADDR)  # must not raise
        self.assertEqual(self.server.retransmits, [],
                         "a shutdown must not trigger a retransmit")

    def test_a_real_ack_still_works(self):
        """The guard must not swallow ordinary traffic."""
        hdr = srv.Header(packet_type=srv.PacketType.DATA, seq_nr=0)
        ack = srv.DataHeader(flags=srv.DataFlags.ACK, seq_nr_ack=0,
                             hdr_word_count=0, data_byte_count=0)
        self.sess.queue.put((hdr.pack() + ack.pack(), ADDR))
        self.assertTrue(
            self.server._wait_for_ack(ADDR, timeout=1.0),
            "an ACK covering the FIN must still complete the wait")


class AReapedTransferLogsNoError(unittest.TestCase):
    """End to end through a real Session, which is where this was observed."""

    def test_shutdown_during_a_wait_is_silent(self):
        events = []

        class _Server:
            _shutdown = False
            _local = threading.local()

            def _print_event(self, msg):
                events.append(msg)

            def _handle_data(self, data, addr):
                # Stand in for a handler that is mid-reply and blocks on the
                # session queue, which is exactly where the reap caught it.
                waiter = _Waiter(sess)
                waiter._wait_for_ack(addr, timeout=2.0)

        server = _Server()
        sess = object.__new__(srv.Session)
        sess.server = server
        sess.addr = ADDR
        sess.queue = queue.Queue()
        sess.handles = {}
        sess._closing = False
        sess._thread = threading.Thread(target=sess._run, daemon=True)
        sess.start()

        # Get the worker into the handler, then reap it.
        sess.queue.put((b"\x00" * 8, ADDR))
        time.sleep(0.2)
        sess.shutdown()
        sess._thread.join(timeout=3.0)

        self.assertFalse(sess._thread.is_alive(), "the worker did not stop")
        errors = [e for e in events if "session error" in e]
        self.assertEqual(
            errors, [],
            "reaping a session mid-transfer logged an error: %s" % errors)


if __name__ == "__main__":
    unittest.main()
