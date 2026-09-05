"""The HTTP server must satisfy OPL's HTTP client, not just the RFC.

Open PS2 Loader's HTTP mode (Docmine17/Open-PS2-Loader-HTTP) reads with an IOP
driver that is far stricter than a browser, and every one of its limits fails
quietly on the console rather than loudly on the wire:

  * A range read answered 200 instead of 206 is a hard failure (-5).
  * The body must be EXACTLY the requested byte count -- the driver ignores
    Content-Length and reads a fixed number of bytes, so one byte too many
    shifts every later response on the same keep-alive socket.
  * The response header block must fit a 512-byte buffer.
  * games.csv must carry Content-Length, must not be chunked, and must fit
    8192 bytes or the console truncates the tail mid-line.

These tests speak the driver's exact bytes over a real socket, because the
things that break here are framing details a requests-level test cannot see.

Run:  python -m unittest tests.test_http_server -v
"""

import os
import socket
import sys
import tempfile
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
_HTTP_DIR = os.path.join(ROOT, "http_server")
if _HTTP_DIR not in sys.path:
    sys.path.insert(0, _HTTP_DIR)
# compression_selftest (used to build real CSO fixtures) imports the
# top-level udpfs_server module. http_server adds that directory to
# sys.path as a side effect of its own compression import; relying on
# that would tie these tests to an implementation detail of the module
# under test.
_UDPFS_DIR = os.path.join(ROOT, "udpfs_server")
if _UDPFS_DIR not in sys.path:
    sys.path.insert(0, _UDPFS_DIR)

import http_server as hs  # noqa: E402


PATTERN = bytes(range(256)) * 128  # 32768 bytes, position-revealing
CRLF = chr(13) + chr(10)


def _recv_response(sock):
    """(headers_bytes, body_bytes) for one response, framed by Content-Length.

    Framing on Content-Length rather than on close is what lets a single
    connection be reused across assertions the way the console reuses it.
    """
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(65536)
        if not chunk:
            raise AssertionError("connection closed before headers")
        data += chunk
    head, _, rest = data.partition(b"\r\n\r\n")
    header_block = head + b"\r\n\r\n"
    length = 0
    for line in head.split(b"\r\n"):
        if line.lower().startswith(b"content-length:"):
            length = int(line.split(b":", 1)[1].strip())
    while len(rest) < length:
        chunk = sock.recv(65536)
        if not chunk:
            break
        rest += chunk
    return header_block, rest[:length]


class _ServerFixture(unittest.TestCase):
    """A running server over a temporary OPL folder."""

    compression = True
    # Loopback by default so the suite never opens a listener on a real
    # interface. ReachableOnEveryAdapterTests overrides it, because
    # binding everything is the property it exists to prove.
    bind_host = "127.0.0.1"

    def setUp(self):
        self.work = tempfile.mkdtemp()
        self.addCleanup(self._cleanup_tree)
        os.makedirs(os.path.join(self.work, "DVD"))
        os.makedirs(os.path.join(self.work, "CD"))
        self.write_games()

        self.index = hs.GameIndex(self.work, enable_compression=self.compression)
        self.server = hs.Ps2HTTPServer((self.bind_host, 0), self.index)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()
        self.addCleanup(self._stop)

    def write_games(self):
        self._write(os.path.join("DVD", "SLUS_201.74.Rumble Racing.iso"), PATTERN)
        self._write(os.path.join("CD", "SCUS_971.24.Some Game.iso"), b"\xAA" * 4096)
        self._write("not-a-convention.iso", b"\xBB" * 2048)
        # games.csv has no quoting, so a comma in a name would split the
        # filename field -- which makes the row assertions below meaningful.
        self._write(os.path.join("DVD", "SLUS_209.46.Grand Theft, Auto.iso"),
                    bytes([0xEE]) * 1024)

    def _write(self, rel, payload):
        path = os.path.join(self.work, rel)
        with open(path, "wb") as handle:
            handle.write(payload)
        return path

    def _stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def _cleanup_tree(self):
        import shutil
        shutil.rmtree(self.work, ignore_errors=True)

    # -- helpers ----------------------------------------------------------- #

    def connect(self):
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        self.addCleanup(sock.close)
        return sock

    def driver_request(self, sock, name, start, end):
        """The exact bytes modules/iopcore/cdvdman/http.c writes."""
        request = (
            "GET /{name} HTTP/1.1\r\n"
            "Host: 127.0.0.1:{port}\r\n"
            "Range: bytes={start}-{end}\r\n"
            "Connection: keep-alive\r\n\r\n"
        ).format(name=name, port=self.port, start=start, end=end)
        sock.sendall(request.encode("ascii"))
        return _recv_response(sock)

    def simple_get(self, path, extra=""):
        sock = self.connect()
        sock.sendall(("GET {} HTTP/1.1\r\nHost: 127.0.0.1\r\n{}\r\n"
                      .format(path, extra)).encode("ascii"))
        return _recv_response(sock)


class RangeSemanticsTests(_ServerFixture):
    NAME = "SLUS_201.74.Rumble%20Racing.iso"

    def test_range_read_is_206_with_an_exactly_sized_body(self):
        sock = self.connect()
        head, body = self.driver_request(sock, self.NAME, 0, 8191)
        self.assertTrue(head.startswith(b"HTTP/1.1 206"),
                        "a range read answered 200 fails the driver with -5; "
                        "got: " + head.split(b"\r\n")[0].decode())
        self.assertEqual(len(body), 8192)
        self.assertEqual(body, PATTERN[:8192])

    def test_header_block_fits_the_drivers_512_byte_buffer(self):
        sock = self.connect()
        head, _body = self.driver_request(sock, self.NAME, 0, 8191)
        self.assertLess(len(head), 512,
                        "the driver reads headers into a 512-byte buffer and "
                        "returns -3 when the terminator does not arrive inside "
                        "it; header block was {} bytes".format(len(head)))

    def test_never_chunked(self):
        head, _body = self.simple_get("/games.csv")
        self.assertNotIn(b"transfer-encoding", head.lower(),
                         "the client's chunked branch is commented out, so a "
                         "chunked response is an empty game list")
        self.assertIn(b"content-length", head.lower())

    def test_consecutive_reads_stay_framed_on_one_socket(self):
        """The real failure mode: a desync only shows on the SECOND read."""
        sock = self.connect()
        for start in (0, 8192, 16384, 24576):
            head, body = self.driver_request(sock, self.NAME, start,
                                             start + 8191)
            self.assertTrue(head.startswith(b"HTTP/1.1 206"))
            self.assertEqual(len(body), 8192, "desync at offset %d" % start)
            self.assertEqual(body, PATTERN[start:start + 8192],
                             "wrong bytes at offset %d" % start)

    def test_content_range_reports_the_total_size(self):
        sock = self.connect()
        head, _body = self.driver_request(sock, self.NAME, 2048, 4095)
        self.assertIn("bytes 2048-4095/{}".format(len(PATTERN)).encode(), head)

    def test_out_of_range_is_416_not_a_short_body(self):
        """416 beats clamping: a short body hangs the driver mid-read.

        The driver asks for a fixed count and blocks until it arrives, so
        returning fewer bytes than requested wedges the console. A 416 fails
        the read, which it can and does retry.
        """
        sock = self.connect()
        head, _body = self.driver_request(sock, self.NAME, 10 ** 9, 10 ** 9 + 10)
        self.assertTrue(head.startswith(b"HTTP/1.1 416"),
                        head.split(b"\r\n")[0].decode())

    def test_unknown_file_is_404(self):
        head, _body = self.simple_get("/nope.iso", "Range: bytes=0-10\r\n")
        self.assertTrue(head.startswith(b"HTTP/1.1 404"))


class GamesCsvTests(_ServerFixture):
    def test_served_at_root_and_under_any_share_name(self):
        """OPL asks '/games.csv' or '/<share>/games.csv' depending on a field
        we cannot see, so both must answer -- a share-name mismatch would
        otherwise be an empty list with nothing on the wire to explain it."""
        for path in ("/games.csv", "/games/games.csv", "/anything/games.csv"):
            head, body = self.simple_get(path)
            self.assertTrue(head.startswith(b"HTTP/1.1 200"), path)
            self.assertIn(b"SLUS_201.74", body, path)

    def test_rows_are_startup_filename_media(self):
        """Three columns, per the parser -- NOT the four its README documents."""
        _head, body = self.simple_get("/games.csv")
        rows = [line for line in body.decode().splitlines() if line]
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(len(row.split(",")), 3, row)
        by_startup = {r.split(",")[0]: r.split(",") for r in rows}
        self.assertEqual(by_startup["SLUS_201.74"][1],
                         "SLUS_201.74.Rumble Racing.iso")
        self.assertEqual(by_startup["SLUS_201.74"][2], "DVD")
        self.assertEqual(by_startup["SCUS_971.24"][2], "CD")

    def test_startup_never_exceeds_the_consoles_field(self):
        _head, body = self.simple_get("/games.csv")
        for row in body.decode().splitlines():
            if row:
                self.assertLessEqual(len(row.split(",")[0]), hs.STARTUP_MAX, row)

    def test_media_comes_from_the_folder_not_the_size(self):
        """A small file in DVD/ is still DVD media; the folder is the intent."""
        self._write(os.path.join("DVD", "SLES_502.10.Tiny.iso"), b"\x00" * 2048)
        self.index.refresh(force=True)
        _head, body = self.simple_get("/games.csv")
        row = [r for r in body.decode().splitlines() if "SLES_502.10" in r][0]
        self.assertTrue(row.endswith(",DVD"), row)

    def test_oversized_library_drops_whole_rows_rather_than_truncating(self):
        """The console clamps to 8192 and cuts the last line mid-field."""
        for i in range(400):
            self._write("SLUS_{:03d}.{:02d}.Padding Title Number {}.iso"
                        .format(i % 1000, i % 100, i), b"\x00" * 16)
        self.index.refresh(force=True)
        _head, body = self.simple_get("/games.csv")
        self.assertLessEqual(len(body), hs.GAMES_CSV_MAX)
        self.assertTrue(body.endswith(b"\n"),
                        "a clipped final line is exactly what the cap exists "
                        "to prevent")
        for row in body.decode().splitlines():
            self.assertEqual(len(row.split(",")), 3, row)


class IndexTests(_ServerFixture):
    def test_dvd_and_cd_are_flattened_onto_the_web_root(self):
        """The driver hardcodes its base path to '/', so a game in DVD/ must
        still answer at the root."""
        sock = self.connect()
        head, body = self.driver_request(
            sock, "SCUS_971.24.Some%20Game.iso", 0, 1023)
        self.assertTrue(head.startswith(b"HTTP/1.1 206"))
        self.assertEqual(body, b"\xAA" * 1024)

    def test_traversal_is_not_reachable(self):
        for path in ("/../../../etc/passwd", "/..%2f..%2fsecret.iso",
                     "/DVD/../../outside.iso"):
            head, _body = self.simple_get(path, "Range: bytes=0-10\r\n")
            self.assertTrue(head.startswith(b"HTTP/1.1 404"),
                            "{} -> {}".format(path, head.split(b"\r\n")[0]))

    def test_no_directory_listing_is_exposed(self):
        """The reference server hands out a listing of the whole games folder."""
        for path in ("/", "/DVD/", "/CD/"):
            head, _body = self.simple_get(path)
            self.assertFalse(head.startswith(b"HTTP/1.1 200"),
                             "{} produced a listing".format(path))

    def test_a_file_outside_the_root_is_never_served(self):
        outside = os.path.join(os.path.dirname(self.work), "OUTSIDE.iso")
        with open(outside, "wb") as handle:
            handle.write(b"\xCC" * 512)
        self.addCleanup(lambda: os.path.exists(outside) and os.remove(outside))
        head, _body = self.simple_get("/OUTSIDE.iso", "Range: bytes=0-10\r\n")
        self.assertTrue(head.startswith(b"HTTP/1.1 404"))

    def test_new_games_appear_without_a_restart(self):
        self._write(os.path.join("DVD", "SLES_502.10.Added Later.iso"),
                    b"\xDD" * 2048)
        _head, body = self.simple_get("/games.csv")
        self.assertIn(b"SLES_502.10", body)

    def test_names_with_a_comma_are_skipped(self):
        """A comma would cut the filename field short in games.csv.
    
        OPL splits rows on commas with no quoting, so the console would
        take "SLUS_209.46.Grand Theft" as the filename, request a name the
        index does not hold, and get a 404 at boot with nothing to explain
        it. Nothing can fix that server-side without renaming the user's
        file, so the entry is dropped and the log says which and why.
        """
        _head, body = self.simple_get("/games.csv")
        self.assertNotIn(b"SLUS_209.46", body)
    
    def test_names_with_a_newline_are_skipped(self):
        """A newline is legal on Linux and would inject a whole extra row."""
        name = "SLUS_209.47.Two" + chr(10) + "Lines.iso"
        try:
            self._write(os.path.join("DVD", name), bytes(32))
        except OSError:
            self.skipTest("filesystem rejects newlines in names")
        self.index.refresh(force=True)
        _head, body = self.simple_get("/games.csv")
        self.assertNotIn(b"SLUS_209.47", body)
    
    def test_names_too_long_for_the_console_are_skipped(self):
        """A name the console truncates would 404 at boot with no clue why."""
        long_name = "SLUS_200.01." + ("x" * 200) + ".iso"
        self._write(long_name, b"\x00" * 16)
        self.index.refresh(force=True)
        _head, body = self.simple_get("/games.csv")
        self.assertNotIn(b"SLUS_200.01", body)


class HotPathCostTests(_ServerFixture):
    """Streaming must not pay for a directory walk per read.

    This is a real regression, not a hypothetical: rescanning inside lookup()
    made this server 0.88x the speed of the SimpleHTTPRequestHandler script it
    is meant to beat, because the console issues thousands of 8 KB reads and
    every one of them walked root/, DVD/ and CD/. With the walk off the hot
    path the same benchmark runs about 3x the reference instead.

    Asserted structurally rather than by timing, so it cannot flake.
    """

    def test_lookup_touches_the_filesystem_not_at_all(self):
        calls = []
        real_listdir, real_stat = os.listdir, os.stat
        os.listdir = lambda *a, **k: (calls.append("listdir"), real_listdir(*a, **k))[1]
        os.stat = lambda *a, **k: (calls.append("stat"), real_stat(*a, **k))[1]
        try:
            for _ in range(50):
                self.assertIsNotNone(
                    self.index.lookup("SLUS_201.74.Rumble Racing.iso"))
        finally:
            os.listdir, os.stat = real_listdir, real_stat
        self.assertEqual(calls, [],
                         "lookup() hit the filesystem {} time(s) for 50 reads; "
                         "that cost is paid once per 8 KB of every game streamed"
                         .format(len(calls)))

    def test_the_game_list_still_notices_new_games(self):
        """The throttle must not turn into 'never rescans'."""
        # Not 0.0: time.monotonic() counts from boot, so on a machine with
        # under RESCAN_INTERVAL seconds of uptime 0.0 still reads as recent
        # and the rescan this test depends on would be skipped.
        self.index._last_scan = time.monotonic() - self.index.RESCAN_INTERVAL
        self._write(os.path.join("DVD", "SLES_502.11.Fresh.iso"), b"\x00" * 32)
        _head, body = self.simple_get("/games.csv")
        self.assertIn(b"SLES_502.11", body)


class ReachableOnEveryAdapterTests(_ServerFixture):
    """Direct connect and LAN are the same server on different adapters.

    A PS2 cabled straight into the PC arrives on a gateway-less adapter, so a
    server bound to loopback -- or to one chosen interface -- would answer on
    the LAN and be invisible over the cable, with nothing in the logs to say
    so. Binding every interface is what makes both work, and the default Bind
    field is blank precisely so that happens.
    """

    # What the launcher passes when the Bind field is left blank.
    bind_host = ""

    def test_default_bind_listens_on_all_interfaces(self):
        self.assertEqual(self.server.server_address[0], "0.0.0.0")

    def test_a_range_read_succeeds_over_a_non_loopback_address(self):
        try:
            addrs = sorted({info[4][0] for info in socket.getaddrinfo(
                socket.gethostname(), None, socket.AF_INET)}
                - {"127.0.0.1"})
        except socket.gaierror:
            addrs = []
        if not addrs:
            self.skipTest("no non-loopback IPv4 address on this host")
        for ip in addrs:
            with self.subTest(address=ip):
                sock = socket.create_connection((ip, self.port), timeout=5)
                self.addCleanup(sock.close)
                sock.sendall((
                    "GET /{} HTTP/1.1{}Host: {}:{}{}Range: bytes=0-2047{}"
                    "Connection: keep-alive{}{}".format(
                        "SLUS_201.74.Rumble%20Racing.iso", CRLF, ip, self.port,
                        CRLF, CRLF, CRLF, CRLF)).encode("ascii"))
                head, body = _recv_response(sock)
                self.assertTrue(head.startswith(b"HTTP/1.1 206"), ip)
                self.assertEqual(len(body), 2048, ip)


class StartupParsingTests(unittest.TestCase):
    def test_convention_is_split_into_id_and_title(self):
        self.assertEqual(hs.parse_startup("SLUS_201.74.Rumble Racing"),
                         ("SLUS_201.74", "Rumble Racing"))

    def test_lowercase_ids_are_normalised(self):
        startup, _title = hs.parse_startup("slus_201.74.Rumble Racing")
        self.assertEqual(startup, "SLUS_201.74")

    def test_non_conforming_names_still_produce_a_capped_id(self):
        startup, title = hs.parse_startup("Some Random Game Name")
        self.assertLessEqual(len(startup), hs.STARTUP_MAX)
        self.assertEqual(title, "Some Random Game Name")


class ZsoPassthroughTests(_ServerFixture):
    """ZSO is decoded on the console, above the device layer.

    cdvdman.c probes for ZSO and runs ziso_read_sector regardless of which
    device driver is underneath, so http_cdvdman.irx gets it too. Serving these
    raw keeps the transfer compressed and works even without lz4 on the PC --
    so they must be advertised as .zso, not decompressed into a virtual .iso.
    """

    def write_games(self):
        self._write("SLUS_200.02.Zso Game.zso", b"ZSO\x00" + b"\x00" * 1020)
        # '.ziso' is the same container under a spelling OPL's parser does not
        # accept, so it has to be advertised as '.zso' to be reachable at all.
        self._write("SLUS_200.03.Ziso Game.ziso", b"ZSO\x00" + b"\x00" * 1020)

    def test_zso_is_advertised_with_its_own_extension(self):
        _head, body = self.simple_get("/games.csv")
        self.assertIn(b"SLUS_200.02.Zso Game.zso", body)

    def test_ziso_is_advertised_as_zso(self):
        _head, body = self.simple_get("/games.csv")
        self.assertIn(b"SLUS_200.03.Ziso Game.zso", body)
        self.assertNotIn(b".ziso", body)

    def test_zso_bytes_are_passed_through_untouched(self):
        sock = self.connect()
        head, body = self.driver_request(sock, "SLUS_200.02.Zso%20Game.zso",
                                         0, 3)
        self.assertTrue(head.startswith(b"HTTP/1.1 206"))
        self.assertEqual(body, b"ZSO\x00")


class CompressedImageTests(_ServerFixture):
    """CHD/CSO are decompressed here, which the reference structurally cannot do.

    The console asks for byte ranges of a raw file, so a compressed image can
    only be streamed if the SERVER decodes it. Reusing UDPFS's
    CompressedFileWrapper (seek/read/tell with block caching) makes a CSO or
    CHD answer Range requests as a virtual .iso -- something neither the
    reference script nor nginx can offer.
    """

    PLAIN = bytes(range(256)) * 64  # 16384 bytes across 8 CSO blocks

    def write_games(self):
        import compression_selftest as cs
        cs.make_cso(os.path.join(self.work, "DVD", "SLUS_202.02.Cso Game.cso"),
                    self.PLAIN)

    def test_compressed_image_is_advertised_as_a_virtual_iso(self):
        _head, body = self.simple_get("/games.csv")
        self.assertIn(b"SLUS_202.02.Cso Game.iso", body)
        self.assertNotIn(b".cso", body)

    def test_range_read_returns_decompressed_bytes(self):
        sock = self.connect()
        head, body = self.driver_request(sock, "SLUS_202.02.Cso%20Game.iso",
                                         0, 2047)
        self.assertTrue(head.startswith(b"HTTP/1.1 206"))
        self.assertEqual(body, self.PLAIN[:2048])

    def test_total_size_is_the_uncompressed_size(self):
        """Content-Range must describe the ISO the console thinks it is reading,
        not the size of the container on disk."""
        sock = self.connect()
        head, _body = self.driver_request(sock, "SLUS_202.02.Cso%20Game.iso",
                                          0, 2047)
        self.assertIn("/{}".format(len(self.PLAIN)).encode(), head)

    def test_reads_across_block_boundaries_stay_framed(self):
        """The compressed path bypasses sendfile, so it needs its own framing
        proof -- and an offset mid-block exercises the wrapper's seek."""
        sock = self.connect()
        for start in (0, 1024, 3000, 8192):
            head, body = self.driver_request(sock, "SLUS_202.02.Cso%20Game.iso",
                                             start, start + 2047)
            self.assertTrue(head.startswith(b"HTTP/1.1 206"))
            self.assertEqual(len(body), 2048, "desync at offset %d" % start)
            self.assertEqual(body, self.PLAIN[start:start + 2048],
                             "wrong bytes at offset %d" % start)


class CompressionDisabledTests(_ServerFixture):
    compression = False

    def write_games(self):
        import compression_selftest as cs
        cs.make_cso(os.path.join(self.work, "DVD", "SLUS_202.02.Cso Game.cso"),
                    b"\x00" * 4096)
        self._write(os.path.join("DVD", "SLUS_201.74.Plain.iso"), PATTERN)

    def test_compressed_images_are_not_advertised_when_disabled(self):
        """Unadvertised rather than served raw: the console would read the
        container bytes as if they were an ISO and fail at boot."""
        _head, body = self.simple_get("/games.csv")
        self.assertNotIn(b"SLUS_202.02", body)
        self.assertIn(b"SLUS_201.74.Plain.iso", body)


if __name__ == "__main__":
    unittest.main()
