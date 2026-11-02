"""A WRITE or BWRITE through a handle that cannot take one is answered.

A file opened O_RDONLY is held open 'rb', and writing to it raises
io.UnsupportedOperation, an OSError whose errno is None; a compressed image
opened through its virtual .iso name is held by a reader with no write method
at all. In both cases the write path raised out of its own except clause, the
session logged the error, and the console was sent no WRITE_DONE: it waited for
one until it gave up. Edge refuses both with EACCES before it takes any data,
and this server now does the same. The last test of the first class pins the
other side of the check, that a handle opened for writing still takes both
kinds of write.

The second class covers what happens after such a refusal, or any other one
answered at the request. The console sends every chunk of a write before it
waits for WRITE_DONE, so the rest of a refused write still arrives, and it must
be dropped rather than joined to the write before it.

The last two classes cover requests that went unanswered the same way: a BREAD
past the largest file offset, where seek() and pread() raise ValueError and
OverflowError rather than OSError, and an LSEEK with a whence seek() does not
take.

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
SEEK_CUR = 1
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
    def _chunk(data, nr=0, total=1):
        return struct.pack("<BBHHH", srv.MsgType.WRITE_DATA, 0, nr, len(data), total) + data

    @staticmethod
    def _write_req(handle, size):
        return struct.pack("<BBBBiI", srv.MsgType.WRITE_REQ, 0, 0, 0, handle, size)

    @staticmethod
    def _bwrite_req(handle, sector, sector_count):
        return struct.pack("<BBHiII", srv.MsgType.BWRITE_REQ, 0, sector_count,
                           handle, sector & 0xFFFFFFFF, sector >> 32)

    def write(self, handle, data):
        return self._answer(self._write_req(handle, len(data)) + self._chunk(data),
                            srv.MsgType.WRITE_DONE)

    def bwrite(self, handle, sector, data):
        return self._answer(self._bwrite_req(handle, sector, len(data) // SECTOR)
                            + self._chunk(data), srv.MsgType.WRITE_DONE)

    def bread_result(self, handle, sector, sector_count=1):
        """A BREAD's result, or None if the server sent no reply. bread(),
        inherited, is the one that returns the data."""
        msg = struct.pack("<BBHiII", srv.MsgType.BREAD_REQ, 0, sector_count,
                          handle, sector & 0xFFFFFFFF, sector >> 32)
        return self._answer(msg, srv.MsgType.RESULT_REPLY)

    def lseek_result(self, handle, offset, whence):
        """An LSEEK's result, or None if the server sent no reply."""
        msg = struct.pack("<BBHiq", srv.MsgType.LSEEK_REQ, whence, 0, handle,
                          offset)
        return self._answer(msg, srv.MsgType.LSEEK_REPLY)

    def send_in_chunks(self, request, data, chunk):
        """Send request with data split into chunks, without waiting for an
        answer: the IOP sends every chunk before it waits for WRITE_DONE
        (_write_with_combined_header in Neutrino's udpfs_core.c), the first
        one in the request's own datagram."""
        parts = [data[i:i + chunk] for i in range(0, len(data), chunk)]
        self._send_request(request + self._chunk(parts[0], 0, len(parts)))
        for nr, part in enumerate(parts[1:], 1):
            self._send_request(self._chunk(part, nr, len(parts)))

    def replies_before_seek(self, handle):
        """Every reply that arrives ahead of the answer to an LSEEK, as
        (message type, result) pairs, and the position the LSEEK returns. The
        server handles one peer's packets in order, so the LSEEK's answer comes
        after everything the packets sent before it caused."""
        self._send_request(struct.pack("<BBHiii", srv.MsgType.LSEEK_REQ,
                                       SEEK_CUR, 0, handle, 0, 0))
        replies = []
        while True:
            _dh, payload = self._recv_data_packet()
            self._send_ack()
            result = struct.unpack_from("<i", payload, 4)[0]
            if payload[0] == srv.MsgType.LSEEK_REPLY:
                return replies, result
            replies.append((payload[0], result))


class _Loopback(unittest.TestCase):
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

    def _console(self, compression=False, **options):
        server = ps2servers_core.AutoUdpfsServer(
            root_dir=self.root, port=0, bind_ip="127.0.0.1", data_port=0,
            enable_compression=compression, verbose=False, **options)
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


class WriteNeedsAWritableHandleTests(_Loopback):
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


class ARefusedWriteTakesNoChunksTests(_Loopback):
    """The rest of a write refused at its request is dropped.

    A finished write used to leave its handle, buffer and chunk count behind,
    and a refusal at the request did not clear them. Chunk 1 of a refused
    two-chunk write then matched the count a finished one-chunk write had left:
    it was added to that write's buffer and written into the earlier file at
    its current offset, and a second WRITE_DONE went out, which the console
    would take as the answer to its next request.
    """

    def _after_a_finished_write(self, **options):
        """A console that has written AAAA to save.bin in one chunk, and that
        file's handle, still open."""
        self.save = self._put("save.bin", b"")
        self._put("other.bin", b"O" * SECTOR)
        console = self._console(**options)
        handle = console.open_with("save.bin", O_RDWR)
        self.assertGreater(handle, 0)
        self.assertEqual(console.write(handle, b"AAAA"), 4)
        return console, handle

    def _assert_only_the_refusal(self, console, handle, want, what):
        replies, position = console.replies_before_seek(handle)
        self.assertEqual(
            replies, [(srv.MsgType.WRITE_DONE, want)],
            f"{what}: the console must get exactly the one refusal; "
            f"server events: {self.events}")
        self.assertEqual(self._content(self.save), b"AAAA",
                         f"{what}: the refused write's chunks reached save.bin")
        self.assertEqual(position, 4, what)
        # The session is not left with anything armed: the next write lands.
        self.assertEqual(console.write(handle, b"BBBB"), 4)
        self.assertEqual(self._content(self.save), b"AAAABBBB")

    def test_a_write_through_a_read_only_handle(self):
        console, handle = self._after_a_finished_write()
        other = console.open_with("other.bin", O_RDONLY)
        self.assertGreater(other, 0)
        console.send_in_chunks(console._write_req(other, 8), b"11112222", 4)
        self._assert_only_the_refusal(console, handle, -errno.EACCES,
                                      "WRITE through a read-only handle")

    def test_a_block_write_through_a_read_only_handle(self):
        console, handle = self._after_a_finished_write()
        other = console.open_with("other.bin", O_RDONLY)
        self.assertGreater(other, 0)
        console.send_in_chunks(console._bwrite_req(other, 0, 1), b"2" * SECTOR,
                               SECTOR // 2)
        self._assert_only_the_refusal(console, handle, -errno.EACCES,
                                      "BWRITE through a read-only handle")
        self.assertEqual(self._content(os.path.join(self.root, "other.bin")),
                         b"O" * SECTOR)

    def test_a_write_to_a_handle_that_is_not_open(self):
        console, handle = self._after_a_finished_write()
        console.send_in_chunks(console._write_req(999, 8), b"11112222", 4)
        self._assert_only_the_refusal(console, handle, -errno.EBADF,
                                      "WRITE to handle 999")

    def test_a_block_write_to_a_handle_that_is_not_open(self):
        console, handle = self._after_a_finished_write()
        console.send_in_chunks(console._bwrite_req(999, 0, 1), b"2" * SECTOR,
                               SECTOR // 2)
        self._assert_only_the_refusal(console, handle, -errno.EBADF,
                                      "BWRITE to handle 999")

    def test_a_write_declared_over_the_cap(self):
        cap = srv.MIN_MAX_TRANSFER_BYTES
        console, handle = self._after_a_finished_write(max_transfer_bytes=cap)
        console.send_in_chunks(console._write_req(handle, cap + 1), b"11112222", 4)
        self._assert_only_the_refusal(console, handle, -errno.EINVAL,
                                      "WRITE declared over the cap")

    def test_a_block_write_declared_over_the_cap(self):
        cap = srv.MIN_MAX_TRANSFER_BYTES
        console, handle = self._after_a_finished_write(max_transfer_bytes=cap)
        console.send_in_chunks(console._bwrite_req(handle, 0, cap // SECTOR + 1),
                               b"2" * SECTOR, SECTOR // 2)
        self._assert_only_the_refusal(console, handle, -errno.EINVAL,
                                      "BWRITE declared over the cap")

    def test_a_block_write_past_the_largest_file_offset(self):
        # Sector 2**54 is byte 2**63, one past the largest offset seek() takes:
        # it raised ValueError, not OSError, and the write went unanswered.
        console, handle = self._after_a_finished_write()
        console.send_in_chunks(console._bwrite_req(handle, 1 << 54, 1),
                               b"2" * SECTOR, SECTOR // 2)
        self._assert_only_the_refusal(console, handle, -errno.EINVAL,
                                      "BWRITE past the largest file offset")

    def test_a_block_write_to_the_image_past_the_largest_file_offset(self):
        # The same through the shared image, where pwrite() raised
        # OverflowError.
        image = self._put("disk.img", b"D" * (4 * SECTOR))
        console, handle = self._after_a_finished_write(block_device=image)
        console.send_in_chunks(console._bwrite_req(0, 1 << 54, 1),
                               b"2" * SECTOR, SECTOR // 2)
        self._assert_only_the_refusal(console, handle, -errno.EINVAL,
                                      "BWRITE to the image past the largest file offset")
        self.assertEqual(self._content(image), b"D" * (4 * SECTOR))

    def _give_up_on_a_write(self, console, handle):
        # The console stops sending a write's chunks when a send fails, and
        # its next request finds that write still being assembled here.
        console._send_request(console._write_req(handle, 12)
                              + console._chunk(b"XXXX", 0, 3))

    def test_a_refused_write_after_one_the_console_gave_up_on(self):
        console, handle = self._after_a_finished_write()
        self._give_up_on_a_write(console, handle)
        console.send_in_chunks(console._write_req(999, 8), b"11112222", 4)
        self._assert_only_the_refusal(console, handle, -errno.EBADF,
                                      "WRITE to handle 999 after an unfinished one")

    def test_a_refused_block_write_after_a_write_the_console_gave_up_on(self):
        console, handle = self._after_a_finished_write()
        self._give_up_on_a_write(console, handle)
        console.send_in_chunks(console._bwrite_req(999, 0, 1), b"2" * SECTOR,
                               SECTOR // 2)
        self._assert_only_the_refusal(console, handle, -errno.EBADF,
                                      "BWRITE to handle 999 after an unfinished write")

    def test_chunks_with_no_write_in_front_of_them(self):
        console, handle = self._after_a_finished_write()
        console._send_request(console._chunk(b"2222", 1, 2))
        console._send_request(console._chunk(b"3333", 0, 1))
        replies, position = console.replies_before_seek(handle)
        self.assertEqual(replies, [], "a stray chunk was answered")
        self.assertEqual(position, 4)
        self.assertEqual(self._content(self.save), b"AAAA")

        # The same after a block write.
        self.assertEqual(console.bwrite(handle, 1, b"4" * SECTOR), 0)
        console._send_request(console._chunk(b"5555", 1, 2))
        replies, _position = console.replies_before_seek(handle)
        self.assertEqual(replies, [], "a stray chunk was answered")
        self.assertEqual(self._content(self.save),
                         b"AAAA" + b"\0" * (SECTOR - 4) + b"4" * SECTOR)

    def test_a_write_in_several_chunks_still_lands(self):
        # The other side: chunks that belong to an accepted write are kept.
        console, handle = self._after_a_finished_write()
        console.send_in_chunks(console._write_req(handle, 8), b"11112222", 4)
        replies, position = console.replies_before_seek(handle)
        self.assertEqual(replies, [(srv.MsgType.WRITE_DONE, 8)])
        self.assertEqual(position, 12)
        self.assertEqual(self._content(self.save), b"AAAA11112222")
        console.send_in_chunks(console._bwrite_req(handle, 1, 1), b"3" * SECTOR,
                               SECTOR // 2)
        replies, position = console.replies_before_seek(handle)
        self.assertEqual(replies, [(srv.MsgType.WRITE_DONE, 0)])
        self.assertEqual(position, 2 * SECTOR)
        # And a write after a block write is a write again.
        console.send_in_chunks(console._write_req(handle, 8), b"44445555", 4)
        replies, position = console.replies_before_seek(handle)
        self.assertEqual(replies, [(srv.MsgType.WRITE_DONE, 8)])
        self.assertEqual(position, 2 * SECTOR + 8)
        self.assertEqual(self._content(self.save),
                         b"AAAA11112222" + b"\0" * (SECTOR - 12) + b"3" * SECTOR
                         + b"44445555")


class ReadsPythonCannotTakeAreAnsweredTests(_Loopback):
    """A read that Python's file calls refuse is answered with EINVAL.

    For a position past the largest file offset, 2**63 - 1, seek() raises
    ValueError and pread() OverflowError, not OSError. The handler's except
    did not catch them: the session logged the error and the console was sent
    no answer, the same as the block writes in the class above.
    """

    def _game(self, **options):
        """A console with game.bin, two sectors, open read-only."""
        self.data = bytes(range(256)) * 4
        self._put("game.bin", self.data)
        console = self._console(**options)
        handle = console.open_with("game.bin", O_RDONLY)
        self.assertGreater(handle, 0)
        return console, handle

    def test_a_block_read_past_the_largest_file_offset(self):
        # Sector 2**54 is byte 2**63, one past the largest offset seek() takes.
        console, handle = self._game()
        self.assertEqual(console.bread_result(handle, 1 << 54), -errno.EINVAL,
                         f"server events: {self.events}")
        # The session still serves the file.
        self.assertEqual(console.bread(1, 1, handle=handle), self.data[SECTOR:])

    def test_a_block_read_of_the_image_past_the_largest_file_offset(self):
        # The same through the shared image, where pread() raised
        # OverflowError.
        image = self._put("disk.img", b"D" * (4 * SECTOR))
        console, _game = self._game(block_device=image)
        self.assertEqual(console.bread_result(0, 1 << 54), -errno.EINVAL,
                         f"server events: {self.events}")
        self.assertEqual(console.bread(3, 1), b"D" * SECTOR)


class ASeekPythonCannotTakeIsAnsweredTests(_Loopback):
    """An LSEEK with a whence seek() does not take is answered with EINVAL.

    seek() takes whence 0, 1 and 2, and 3 and 4 only where the host has
    SEEK_DATA and SEEK_HOLE. For any other it raises ValueError, which the
    handler's except did not catch, so the client was sent no answer. A
    console cannot send one: ps2sdk's iomanX_lseek() refuses any whence but
    the three before the request leaves the IOP.
    """

    def test_a_whence_seek_does_not_take(self):
        self._put("game.bin", b"G" * SECTOR)
        console = self._console()
        handle = console.open_with("game.bin", O_RDONLY)
        self.assertGreater(handle, 0)
        for whence in (5, 255):
            self.assertEqual(console.lseek_result(handle, 0, whence), -errno.EINVAL,
                             f"whence {whence}; server events: {self.events}")
        # The three a console sends still work.
        self.assertEqual(console.lseek_result(handle, 0, 2), SECTOR)
        self.assertEqual(console.lseek_result(handle, -1, 1), SECTOR - 1)
        self.assertEqual(console.lseek_result(handle, 2, 0), 2)


if __name__ == "__main__":
    unittest.main()
