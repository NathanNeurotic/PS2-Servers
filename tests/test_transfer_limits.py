"""The Python servers must bound what one packet can make them allocate.

Everything here is reachable before any authentication, because there is none:
these are UDP datagrams from whoever sends them. The sizes involved are not
subtle -- a twelve-byte READ used to be able to ask for four gigabytes -- and
the fix is a comparison, so the only thing worth testing is that the comparison
is actually there and that it did not also break the ordinary case.

Edge has had these caps since it was written. The point of pinning them here is
that the two implementations speak the same wire protocol, so a client must not
be able to tell which one it is talking to by probing a limit.

Run:  python -m unittest tests.test_transfer_limits -v
"""

import errno
import os
import struct
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
# udpfs_server/ is not a package; the server module sits inside it and its
# sibling compressed_iso/ has to resolve, so the directory goes on the path.
_UDPFS = os.path.join(_ROOT, "udpfs_server")
if _UDPFS not in sys.path:
    sys.path.insert(0, _UDPFS)

import udpfs_server as srv  # noqa: E402


class _Recorder(srv.UdpfsServer):
    """A server with the sockets removed, so handlers can be called directly.

    The handlers are pure with respect to the wire: they take a payload and
    call a _send_* method. Replacing those captures the answer without binding
    a port, which keeps this test free of timing and of a real console.
    """

    def __init__(self, **kwargs):
        # Skip UdpfsServer.__init__ entirely: it opens sockets. Only the fields
        # the handlers under test touch are set up.
        self.replies = []
        self.root_dir = None
        self.read_only = False
        self.verbose = False
        self.bd_sector_size = 512
        self.bd_fh = None
        self.max_transfer_bytes = kwargs.get(
            "max_transfer_bytes", srv.DEFAULT_MAX_TRANSFER_BYTES)
        self.stats = {k: 0 for k in (
            "open", "close", "read", "write", "bread", "bwrite", "dread",
            "lseek", "discovery", "bytes_read", "bytes_written")}
        self._handles = {}
        self._write_state = {
            "handle": -1, "is_block": False, "sector_nr": 0,
            "sector_count": 0, "data": bytearray(),
            "total_chunks": 0, "received_chunks": 0,
        }

    # -- the per-session properties the handlers use --------------------
    @property
    def handles(self):
        return self._handles

    def _prop(name):  # noqa: N805
        def getter(self):
            return self._write_state[name]

        def setter(self, value):
            self._write_state[name] = value
        return property(getter, setter)

    write_handle = _prop("handle")
    write_is_block = _prop("is_block")
    write_sector_nr = _prop("sector_nr")
    write_sector_count = _prop("sector_count")
    write_data = _prop("data")
    write_total_chunks = _prop("total_chunks")
    write_received_chunks = _prop("received_chunks")
    del _prop

    # -- capture instead of transmit ------------------------------------
    def _print_event(self, msg):
        pass

    def _update_status(self):
        pass

    def _send_read_result(self, addr, result, data):
        self.replies.append(("read", result, data))

    def _send_write_done(self, addr, result):
        self.replies.append(("write", result))

    def _send_ack(self, addr, is_ack=True):
        self.replies.append(("ack", is_ack))


ADDR = ("192.0.2.10", 1234)


def read_payload(handle, size):
    return struct.pack("<BBBBiI", 0, 0, 0, 0, handle, size)


def bread_payload(handle, sector_nr, sector_count):
    return struct.pack("<BBHiII", 0, 0, sector_count, handle,
                       sector_nr & 0xFFFFFFFF, sector_nr >> 32)


def write_req_payload(handle, size):
    return struct.pack("<BBBBiI", 0, 0, 0, 0, handle, size)


class _Blob:
    """Stands in for an open file, without one existing."""

    def __init__(self, data=b"x" * 4096):
        self.data = data
        self.pos = 0
        self.is_dir = False

    def read(self, n):
        chunk = self.data[self.pos:self.pos + n]
        self.pos += len(chunk)
        return chunk

    def seek(self, pos):
        self.pos = pos


def _handle(blob=None):
    fh = srv.FileHandle(blob or _Blob(), is_dir=False)
    return fh


class ReadIsCapped(unittest.TestCase):
    def setUp(self):
        self.server = _Recorder()
        self.server.handles[5] = _handle()

    def test_a_four_gigabyte_read_is_refused(self):
        # The whole finding in one line: twelve bytes on the wire, 4 GiB of
        # allocation, from anyone who can reach the port.
        self.server._handle_read(ADDR, read_payload(5, 0xFFFFFFFF))
        kind, result, data = self.server.replies[-1]
        self.assertEqual(kind, "read")
        self.assertEqual(result, -errno.EINVAL)
        self.assertEqual(data, b"")

    def test_a_read_at_the_limit_still_works(self):
        cap = self.server.max_transfer_bytes
        self.server.handles[5] = _handle(_Blob(b"y" * cap))
        self.server._handle_read(ADDR, read_payload(5, cap))
        kind, result, data = self.server.replies[-1]
        self.assertEqual(kind, "read")
        self.assertEqual(result, cap)
        self.assertEqual(len(data), cap)

    def test_an_ordinary_console_read_is_untouched(self):
        # OPL reads in sector-sized chunks; nothing real comes near the cap.
        self.server._handle_read(ADDR, read_payload(5, 2048))
        _kind, result, data = self.server.replies[-1]
        self.assertEqual(result, 2048)
        self.assertEqual(len(data), 2048)

    def test_the_selftest_sized_read_is_served(self):
        """A 200,000-byte single READ must work.

        This is not hypothetical: udpfs_server/multiclient_selftest.py reads
        exactly this in one call, and an earlier version of this cap copied
        Edge's 64 KiB ceiling and broke it. The cap exists to stop a 4 GiB
        allocation, not to reshape how clients read -- and this server is the
        one validated against real consoles, so a limit that refuses a request
        an in-tree client makes is wrong however defensible the number looks.
        """
        size = 200000
        self.server.handles[5] = _handle(_Blob(b"q" * size))
        self.server._handle_read(ADDR, read_payload(5, size))
        _kind, result, data = self.server.replies[-1]
        self.assertEqual(result, size,
                         "a 200,000-byte READ was refused; multiclient_selftest "
                         "makes exactly this request")
        self.assertEqual(len(data), size)

    def test_the_default_cap_clears_every_in_tree_client(self):
        # Guards the number itself: shrinking it below what the selftests do
        # would break them, and the failure there is a timeout rather than a
        # sentence naming the cap.
        self.assertGreaterEqual(srv.DEFAULT_MAX_TRANSFER_BYTES, 200000)

    def test_the_cap_is_checked_before_the_handle(self):
        # Order matters: refusing on the handle first would still have
        # allocated nothing, but refusing on size first means a bad handle
        # cannot be used to probe which sizes are allowed.
        self.server._handle_read(ADDR, read_payload(999, 0xFFFFFFFF))
        _kind, result, _data = self.server.replies[-1]
        self.assertEqual(result, -errno.EINVAL,
                         "an oversized read must be EINVAL even on a bad handle")


class BlockReadIsCapped(unittest.TestCase):
    def setUp(self):
        self.server = _Recorder()
        self.server.handles[5] = _handle(_Blob(b"z" * 8192))

    def test_the_largest_expressible_request_is_refused(self):
        # 65535 sectors x 512 bytes is ~32 MiB in one allocation.
        self.server._handle_bread(ADDR, bread_payload(5, 0, 0xFFFF))
        kind, result, _data = self.server.replies[-1]
        self.assertEqual(kind, "read")
        self.assertEqual(result, -errno.EINVAL)

    def test_an_ordinary_block_read_still_works(self):
        self.server._handle_bread(ADDR, bread_payload(5, 0, 8))
        _kind, result, data = self.server.replies[-1]
        self.assertEqual(result, 4096)
        self.assertEqual(len(data), 4096)


class WritesAreCapped(unittest.TestCase):
    def setUp(self):
        self.server = _Recorder()
        self.server.handles[5] = _handle()

    def test_an_oversized_write_is_refused_at_the_request(self):
        self.server._handle_write_req(ADDR, write_req_payload(5, 0xFFFFFFFF))
        kind, result = self.server.replies[-1]
        self.assertEqual(kind, "write")
        self.assertEqual(result, -errno.EINVAL)

    def test_an_oversized_block_write_is_refused_at_the_request(self):
        self.server._handle_bwrite_req(ADDR, bread_payload(5, 0, 0xFFFF))
        kind, result = self.server.replies[-1]
        self.assertEqual(kind, "write")
        self.assertEqual(result, -errno.EINVAL)

    def test_the_assembled_buffer_cannot_grow_past_the_cap(self):
        """A client that under-declares must still be stopped mid-stream.

        The request-time check trusts a number the client chose. This is the
        one that does not: it counts what actually arrived.
        """
        small = _Recorder(max_transfer_bytes=srv.MIN_MAX_TRANSFER_BYTES)
        small.handles[5] = _handle()
        small._handle_write_req(ADDR, write_req_payload(5, 1024))

        chunk = b"A" * 1400
        # Claim a chunk count that would assemble far past the cap.
        total_chunks = 1000
        refused = False
        for i in range(total_chunks):
            payload = struct.pack("<BBHHH", 0, 0, i, len(chunk), total_chunks) + chunk
            small._handle_write_data(ADDR, payload)
            if small.replies[-1] == ("write", -errno.EINVAL):
                refused = True
                break
        self.assertTrue(refused, "the write buffer grew without limit")
        self.assertLessEqual(len(small.write_data), srv.MIN_MAX_TRANSFER_BYTES)
        self.assertEqual(small.write_handle, -1,
                         "a refused write must not leave its state armed")

    def test_a_malformed_env_var_does_not_abort_startup(self):
        """MAX_TRANSFER_BYTES=oops must not stop the server from launching.

        Read through _env_float, this raised while the argument parser was
        still being constructed -- before argparse, so the operator got a bare
        traceback rather than a message, and --help did not work either. On a
        headless box a crash on launch is the worst failure mode there is: the
        server simply is not there and nothing says why.
        """
        saved = os.environ.get("MAX_TRANSFER_BYTES")
        self.addCleanup(
            lambda: os.environ.__setitem__("MAX_TRANSFER_BYTES", saved)
            if saved is not None else os.environ.pop("MAX_TRANSFER_BYTES", None))

        for bad in ("invalid", "inf", "-inf", "nan", "", "1e999", "0x10"):
            with self.subTest(value=bad):
                os.environ["MAX_TRANSFER_BYTES"] = bad
                self.assertEqual(srv._env_max_transfer(),
                                 srv.DEFAULT_MAX_TRANSFER_BYTES,
                                 "a malformed value must fall back to the "
                                 "documented default, not raise")

        # And a good one still gets through.
        os.environ["MAX_TRANSFER_BYTES"] = str(4 << 20)
        self.assertEqual(srv._env_max_transfer(), 4 << 20)

    def test_the_floor_stops_a_typo_from_refusing_everything(self):
        # A mistyped 100 would otherwise make every real request fail while the
        # server looked healthy.
        self.assertEqual(srv._clamp_max_transfer(100), srv.MIN_MAX_TRANSFER_BYTES)
        self.assertEqual(srv._clamp_max_transfer(0), srv.DEFAULT_MAX_TRANSFER_BYTES)
        self.assertEqual(srv._clamp_max_transfer(-5), srv.DEFAULT_MAX_TRANSFER_BYTES)
        self.assertEqual(srv._clamp_max_transfer("nonsense"),
                         srv.DEFAULT_MAX_TRANSFER_BYTES)
        # A deliberate, sensible value passes through untouched.
        self.assertEqual(srv._clamp_max_transfer(4 << 20), 4 << 20)
        self.assertGreaterEqual(srv.DEFAULT_MAX_TRANSFER_BYTES,
                                srv.MIN_MAX_TRANSFER_BYTES)


class SmbV1ShareBoundaryIsReal(unittest.TestCase):
    """A symlink out of an SMBv1 share must not resolve.

    smb2_paths.py documented this weakness in smbserver_opl.py by name, and it
    stayed open in the SMB server most OPL users actually run.
    """

    def setUp(self):
        import tempfile
        import shutil
        smbv1_dir = os.path.join(_ROOT, "smbv1_server")
        if smbv1_dir not in sys.path:
            sys.path.insert(0, smbv1_dir)
        import smbserver_opl
        self.mod = smbserver_opl
        self.tmp = tempfile.mkdtemp(prefix="ps2-smbv1-share-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.share_root = os.path.join(self.tmp, "share")
        self.outside = os.path.join(self.tmp, "outside")
        os.makedirs(self.share_root)
        os.makedirs(self.outside)
        with open(os.path.join(self.outside, "secret.txt"), "w") as handle:
            handle.write("not yours")
        with open(os.path.join(self.share_root, "game.iso"), "w") as handle:
            handle.write("yours")

    def _share(self):
        return self.mod.Share("games", self.share_root)

    def test_an_ordinary_path_still_resolves(self):
        share = self._share()
        resolved = share.resolve("\\game.iso")
        self.assertIsNotNone(resolved)
        self.assertTrue(os.path.isfile(resolved))

    def test_textual_traversal_is_still_refused(self):
        self.assertIsNone(self._share().resolve("\\..\\outside\\secret.txt"))

    def test_a_sibling_directory_sharing_a_prefix_is_refused(self):
        # /srv/games-private starts with /srv/games as a string but is not
        # inside it. This is what commonpath buys over startswith.
        sibling = self.share_root + "-private"
        os.makedirs(sibling)
        share = self._share()
        # Reached by naming it absolutely through the join.
        self.assertIsNone(share.resolve("\\..\\" + os.path.basename(sibling)))

    @unittest.skipUnless(hasattr(os, "symlink"), "no symlink support")
    def test_a_symlink_out_of_the_share_is_refused(self):
        link = os.path.join(self.share_root, "elsewhere")
        try:
            os.symlink(self.outside, link, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            # Windows needs Developer Mode or elevation for symlinks.
            self.skipTest("cannot create a symlink here: %s" % exc)

        resolved = self._share().resolve("\\elsewhere\\secret.txt")
        self.assertIsNone(
            resolved,
            "a symlink inside the share resolved to a file outside it; the "
            "declared root is not the actual boundary")

    @unittest.skipUnless(hasattr(os, "symlink"), "no symlink support")
    def test_a_symlinked_share_root_still_serves_its_own_files(self):
        # The other half: /home/user/games -> /mnt/disk2/games is ordinary, and
        # resolving the root is what stops that from failing containment.
        link_root = os.path.join(self.tmp, "linked-share")
        try:
            os.symlink(self.share_root, link_root, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest("cannot create a symlink here: %s" % exc)

        share = self.mod.Share("games", link_root)
        resolved = share.resolve("\\game.iso")
        # The canonical path, not merely "not None". The old abspath resolver
        # also returned non-None here -- it returned the path *through* the
        # symlink -- so a non-None assertion would have passed against the bug
        # this test claims to guard.
        self.assertEqual(
            resolved, os.path.join(self.share_root, "game.iso"),
            "a share root must resolve to its canonical target path")


if __name__ == "__main__":
    unittest.main()
