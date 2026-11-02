"""A WRITE or BWRITE through a handle that cannot take one is answered.

A file opened O_RDONLY is held open 'rb', and writing to it raises
io.UnsupportedOperation, an OSError whose errno is None; a compressed image
opened through its virtual .iso name is held by a reader with no write method
at all. In both cases the write path raised out of its own except clause, the
session logged the error, and the console was sent no WRITE_DONE: it waited for
one until it gave up. Edge refuses both with EACCES before it takes any data,
and this server now does the same. The last test pins the other side of the
check, that a handle opened for writing still takes both kinds of write.

These drive the real AutoUdpfsServer receive loop over loopback.

Run:  python -m unittest tests.test_udpfs_write_needs_writable_handle -v
"""

import errno
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

O_RDONLY, O_RDWR = 0x0001, 0x0003
SECTOR = 512


def _cso(data, block=2048):
    """A CSO whose blocks are stored rather than deflated."""
    blocks = len(data) // block
    first = 24 + 4 * (blocks + 1)
    index = [(first + i * block) | 0x80000000 for i in range(blocks)]
    index.append(first + blocks * block)
    header = struct.pack("<4sIQIBBH", b"CISO", 24, len(data), block, 1, 0, 0)
    return header + struct.pack("<%dI" % len(index), *index) + data


class _Console(UdpfsTestClient):
    """The self-test client, with OPEN flags and both write requests."""

    def __init__(self, disc_addr):
        super().__init__(disc_addr, timeout=3.0)

    def _answer(self, msg, reply_type):
        """Send one request and return its reply's result, or None if the
        server sent no reply at all."""
        self._send_request(msg)
        try:
            _dh, payload = self._recv_data_packet()
        except socket.timeout:
            return None
        self._send_ack()
        if payload[0] != reply_type:
            raise UdpfsError(f"expected 0x{reply_type:02x}, got 0x{payload[0]:02x}")
        return struct.unpack_from("<i", payload, 4)[0]

    def open_with(self, path, flags):
        msg = (struct.pack("<BBHi", srv.MsgType.OPEN_REQ, 0, flags, 0)
               + path.encode("utf-8") + b"\0")
        return self._answer(msg, srv.MsgType.OPEN_REPLY)

    @staticmethod
    def _chunk(data):
        return struct.pack("<BBHHH", srv.MsgType.WRITE_DATA, 0, 0, len(data), 1) + data

    def write(self, handle, data):
        msg = (struct.pack("<BBBBiI", srv.MsgType.WRITE_REQ, 0, 0, 0, handle, len(data))
               + self._chunk(data))
        return self._answer(msg, srv.MsgType.WRITE_DONE)

    def bwrite(self, handle, sector, data):
        msg = (struct.pack("<BBHiII", srv.MsgType.BWRITE_REQ, 0, len(data) // SECTOR,
                           handle, sector & 0xFFFFFFFF, sector >> 32)
               + self._chunk(data))
        return self._answer(msg, srv.MsgType.WRITE_DONE)


class WriteNeedsAWritableHandleTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="udpfs_writable_")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        for module in (ps2servers_core, srv):  # the startup banner
            patcher = mock.patch.object(module, "print", create=True)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _put(self, name, data):
        path = os.path.join(self.root, name)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def _content(self, path):
        with open(path, "rb") as f:
            return f.read()

    def _console(self, compression=False):
        server = ps2servers_core.AutoUdpfsServer(
            root_dir=self.root, port=0, bind_ip="127.0.0.1", data_port=0,
            enable_compression=compression, verbose=False)
        self.events = []
        server._print_event = self.events.append
        server._update_status = lambda: None  # the progress line
        runner = threading.Thread(target=server.run, daemon=True)
        runner.start()

        def stop():
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
        console = _Console(server.sock.getsockname())
        self.addCleanup(console.close)
        console.discover()
        return console

    def _assert_refused(self, result, what):
        self.assertIsNotNone(
            result, f"{what}: no WRITE_DONE came back, so the console waits "
                    f"for one; server events: {self.events}")
        self.assertEqual(result, -errno.EACCES, what)

    def test_a_write_through_a_handle_opened_read_only_is_refused(self):
        save = self._put("save.bin", b"KEEP")
        console = self._console()
        handle = console.open_with("save.bin", O_RDONLY)
        self.assertGreater(handle, 0)

        self._assert_refused(console.write(handle, b"LOST"),
                             "WRITE through a handle opened read-only")
        # The session carries on, and nothing reached the file.
        self.assertEqual(console.read(handle, 16), b"KEEP")
        self.assertEqual(self._content(save), b"KEEP")

    def test_a_block_write_through_a_handle_opened_read_only_is_refused(self):
        save = self._put("save.bin", b"K" * SECTOR)
        console = self._console()
        handle = console.open_with("save.bin", O_RDONLY)
        self.assertGreater(handle, 0)

        self._assert_refused(console.bwrite(handle, 0, b"L" * SECTOR),
                             "BWRITE through a handle opened read-only")
        self.assertEqual(console.bread(0, 1, handle=handle), b"K" * SECTOR)
        self.assertEqual(self._content(save), b"K" * SECTOR)

    def test_a_write_to_a_compressed_image_is_refused(self):
        image = _cso(b"G" * 2048)
        cso = self._put("game.cso", image)
        console = self._console(compression=True)
        # The virtual name the folder listing shows for game.cso.
        handle = console.open_with("game.iso", O_RDWR)
        self.assertGreater(handle, 0)

        self._assert_refused(console.write(handle, b"LOST"),
                             "WRITE to a compressed image")
        self._assert_refused(console.bwrite(handle, 0, b"L" * SECTOR),
                             "BWRITE to a compressed image")
        self.assertEqual(console.read(handle, 4), b"GGGG")
        self.assertEqual(self._content(cso), image)

    def test_a_handle_opened_for_writing_still_takes_both(self):
        save = self._put("save.bin", b"0" * (2 * SECTOR))
        console = self._console()
        handle = console.open_with("save.bin", O_RDWR)
        self.assertGreater(handle, 0)

        self.assertEqual(console.write(handle, b"NEW!"), 4)
        self.assertEqual(console.bwrite(handle, 1, b"1" * SECTOR), 0)
        self.assertEqual(self._content(save),
                         b"NEW!" + b"0" * (SECTOR - 4) + b"1" * SECTOR)


if __name__ == "__main__":
    unittest.main()
