"""A save retried after a new loader takes over the console's endpoint lands.

A new loader can replace the old one on the same UDP endpoint while the old
stream is live: a sequence-0 DISCOVERY, then DATA that starts again at 0
(Standard) or 1 (Modulo). AutoUdpfsServer then resets only the transport and
keeps the old loader's handles, because Neutrino's FILEID backend carries an
open handle across the switch. DATA 0 with no DISCOVERY in front of it resets
only the sequence counters, and keeps the handles as well.

Edge also kept the old loader's single-writer reservation, so the new loader's
OPEN of a file the old one was writing was refused with EBUSY for the rest of
the session. This server has no such reservation. These tests pin what a
console needs from it: a save the old loader left half-written can be opened
again, rewritten from the start with O_TRUNC and closed; the new OPEN is given
a new handle, never the old loader's; and the old loader's unfinished write
never lands. They drive the real AutoUdpfsServer receive loop over loopback.

Run:  python -m unittest tests.test_udpfs_handoff_retried_write -v
"""

import os
import shutil
import socket
import struct
import sys
import tempfile
import threading
import unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UDPFS = os.path.join(_ROOT, "udpfs_server")
if _UDPFS not in sys.path:
    sys.path.insert(0, _UDPFS)

import ps2servers_core  # noqa: E402
import udpfs_server as srv  # noqa: E402
from multiclient_selftest import UdpfsError, UdpfsTestClient  # noqa: E402

O_RDWR, O_CREAT, O_TRUNC = 0x0003, 0x0200, 0x0400
SAVE_FLAGS = O_RDWR | O_CREAT | O_TRUNC


class _Console(UdpfsTestClient):
    """The self-test client, with the write path and a loader handoff."""

    def _request(self, msg, reply_type):
        self._send_request(msg)
        _dh, payload = self._recv_data_packet()
        self._send_ack()
        if payload[0] != reply_type:
            raise UdpfsError(f"expected 0x{reply_type:02x}, got 0x{payload[0]:02x}")
        return struct.unpack_from("<i", payload, 4)[0]

    def open_for_write(self, path):
        msg = (struct.pack("<BBHi", srv.MsgType.OPEN_REQ, 0, SAVE_FLAGS, 0o644)
               + path.encode("utf-8") + b"\0")
        return self._request(msg, srv.MsgType.OPEN_REPLY)

    @staticmethod
    def _write_msg(handle, size, chunk, total_chunks):
        return (struct.pack("<BBBBiI", srv.MsgType.WRITE_REQ, 0, 0, 0, handle, size)
                + struct.pack("<BBHHH", srv.MsgType.WRITE_DATA, 0, 0, len(chunk),
                              total_chunks)
                + chunk)

    def write(self, handle, data):
        """One WRITE in a single chunk; returns WRITE_DONE's result."""
        return self._request(self._write_msg(handle, len(data), data, 1),
                             srv.MsgType.WRITE_DONE)

    def write_first_chunk(self, handle, size, chunk):
        """Start a two-chunk WRITE and stop after the first, as a loader that
        is replaced mid-write does. Waits for the server's ACK, so the chunk is
        known to be accepted before anything else is sent: a DISCOVERY goes to
        another socket and could otherwise be handled first."""
        sequence = self.tx_seq
        self._send_request(self._write_msg(handle, size, chunk, 2))
        while True:
            data, _addr = self.sock.recvfrom(4096)
            if len(data) < 6 or srv.Header.unpack(data).packet_type != srv.PacketType.DATA:
                continue
            dh = srv.DataHeader.unpack(data[2:6])
            if (dh.hdr_word_count == 0 and dh.data_byte_count == 0
                    and dh.flags & srv.DataFlags.ACK and dh.seq_nr_ack == sequence):
                return

    def close_handle(self, handle):
        msg = struct.pack("<BBBBi", srv.MsgType.CLOSE_REQ, 0, 0, 0, handle)
        return self._request(msg, srv.MsgType.CLOSE_REPLY)

    def replace_loader(self, first):
        """Be a new loader on this endpoint: DISCOVERY 0, then DATA from first.

        discover() skips what is still queued for the old loader. A Modulo
        client's first DATA draws the compatibility INFORM, packet 0 of the
        server's stream, so its first reply is 1."""
        self.discover(seq=0)
        self.tx_seq = first
        self.rx_expected = 1 if first == 1 else 0

    def restart_stream(self):
        """Be a new loader that starts at DATA 0 without a DISCOVERY."""
        self.tx_seq = 0
        self.rx_expected = 0


class RetriedSaveAfterHandoffTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="udpfs_handoff_")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        for module in (ps2servers_core, srv):  # the startup banner
            patcher = mock.patch.object(module, "print", create=True)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _start_server(self):
        server = ps2servers_core.AutoUdpfsServer(
            root_dir=self.root, port=0, bind_ip="127.0.0.1", data_port=0,
            enable_compression=False, verbose=True)
        events = []
        server._print_event = events.append
        server._update_status = lambda: None  # the progress line
        runner = threading.Thread(target=server.run, daemon=True)
        runner.start()

        def stop():
            if server.sock.fileno() < 0:
                return
            server._shutdown = True
            # Wake the receive loop now rather than at its select timeout.
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as wake:
                wake.sendto(b"\0", server.sock.getsockname())
            runner.join(5)
            server._shutdown_all_sessions()  # closes every session's files
            server._cleanup()
            server.sock.close()
            if server.dsock is not server.sock:
                server.dsock.close()

        self.addCleanup(stop)
        return server, events, stop

    def _console(self, server):
        console = _Console(server.sock.getsockname())
        self.addCleanup(console.close)
        console.discover()
        return console

    def _retry_save_after(self, takeover, marker):
        server, events, stop = self._start_server()
        console = self._console(server)
        save = os.path.join(self.root, "save.bin")

        old = console.open_for_write("save.bin")
        self.assertGreater(old, 0)
        self.assertEqual(console.write(old, b"OLD SAVE OLD"), 12)
        console.write_first_chunk(old, 8, b"HALF")

        takeover(console)

        new = console.open_for_write("save.bin")
        self.assertGreater(new, 0, "the new loader could not reopen the save")
        self.assertNotEqual(new, old, "the new OPEN was given the old loader's handle")
        self.assertEqual(console.write(new, b"NEW SAVE"), 8)
        self.assertEqual(console.close_handle(new), 0)
        # The file in the folder holds the new save alone: O_TRUNC dropped
        # the old one, and the old loader's unfinished write never landed.
        with open(save, "rb") as f:
            self.assertEqual(f.read(), b"NEW SAVE")
        self.assertTrue(any(marker in event for event in events),
                        f"the handoff did not take the path under test: {events}")
        # The old loader's handle is still open. Closing it, as the session's
        # end does, must not write anything it still holds over the new save.
        stop()
        with open(save, "rb") as f:
            self.assertEqual(f.read(), b"NEW SAVE")

    def test_after_a_standard_loader_takes_over(self):
        self._retry_save_after(lambda console: console.replace_loader(0),
                               "seq0 discovery resolved to replacement session")

    def test_after_a_modulo_loader_takes_over(self):
        self._retry_save_after(lambda console: console.replace_loader(1),
                               "seq0 discovery resolved to replacement session")

    def test_after_a_stream_restarts_at_zero_without_discovery(self):
        self._retry_save_after(lambda console: console.restart_stream(),
                               "assuming the peer was reset")


if __name__ == "__main__":
    unittest.main()
