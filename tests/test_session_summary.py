"""Opt-in SESSION summaries: bounded, one line per session, nothing per request."""
import http.client
import os
import struct
import sys
import tempfile
import threading
import time
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _path in (ROOT, os.path.join(ROOT, "http_server"), os.path.join(ROOT, "udpfs_server")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import http_server as hs  # noqa: E402
from launcher import session_stats as ss  # noqa: E402
from launcher import servers  # noqa: E402


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class CounterTests(unittest.TestCase):
    def setUp(self):
        self.clock, self.lines = Clock(), []
        self.stats = ss.SessionStats("http", max_clients=2, idle_seconds=60, clock=self.clock,
                                     emit=self.lines.append)

    def test_idle_client_is_summarised_once(self):
        self.stats.record("10.0.0.2", "list")
        self.stats.record("10.0.0.2", "read")
        self.stats.record("10.0.0.2", "bytes", 2048)
        self.clock.now += 30
        self.assertEqual(self.stats.expire(), [])
        self.stats.record("10.0.0.2", "read")
        self.clock.now += 61
        [line] = self.stats.expire()
        fields = ss.parse_line(line)
        self.assertEqual((fields["service"], fields["client"], fields["seconds"], fields["list"],
                          fields["read"], fields["bytes"], fields["ended"]),
                         ("http", "10.0.0.2", 30, 1, 2, 2048, "idle"))
        self.assertEqual(self.stats.expire(), [])
        self.assertEqual(self.stats.active(), 0)

    def test_touch_keeps_an_active_client(self):
        self.stats.record("10.0.0.2", "read")
        self.clock.now += 45
        self.assertTrue(self.stats.touch("10.0.0.2"))
        self.assertFalse(self.stats.touch("10.0.0.9"))  # never seen: nothing is created
        self.clock.now += 45
        self.assertEqual(self.stats.expire(), [])      # 90 s since the read, 45 s since touch
        self.assertEqual(self.stats.active(), 1)
        self.clock.now += 61
        [line] = self.stats.expire()
        self.assertEqual(ss.parse_line(line)["read"], 1)

    def test_clients_are_kept_apart_and_bounded(self):
        self.stats.record("a", "open")
        self.stats.record("b", "open")
        self.stats.record("c", "open")  # pushes out the least recently seen
        self.assertEqual(self.stats.active(), 2)
        self.assertEqual(ss.parse_line(self.lines[0])["client"], "a")
        self.assertEqual(ss.parse_line(self.lines[0])["ended"], "evicted")

    def test_stop_summarises_everyone(self):
        self.stats.record("a", "errors")
        self.stats.record("b", "connections")
        ended = {ss.parse_line(line)["client"]: ss.parse_line(line)["ended"] for line in self.stats.stop()}
        self.assertEqual(ended, {"a": "stopped", "b": "stopped"})

    def test_unknown_counter_is_refused(self):
        with self.assertRaises(ValueError):
            self.stats.record("a", "packets")

    def test_parse_line_ignores_other_output(self):
        self.assertIsNone(ss.parse_line("HTTP server listening on 0.0.0.0:1100"))
        self.assertIsNone(ss.parse_line("SESSION http garbage"))

    def test_concurrent_updates_are_not_lost(self):
        stats = ss.SessionStats("http", emit=lambda _l: None)
        threads = [threading.Thread(target=lambda: [stats.record("x", "read") for _ in range(500)])
                   for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(ss.parse_line(stats.stop()[0])["read"], 4000)


class HttpWiringTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        os.makedirs(os.path.join(tmp.name, "DVD"))
        with open(os.path.join(tmp.name, "DVD", "SLUS_201.74.Game.iso"), "wb") as handle:
            handle.write(b"\x01" * 8192)
        self.lines = []
        self.stats = ss.SessionStats("http", emit=self.lines.append)
        self.server = hs.Ps2HTTPServer(("127.0.0.1", 0), hs.GameIndex(tmp.name), stats=self.stats)
        # Track handler threads so _finish() can join them: a client can read the
        # whole body before its handler records the read.
        self.server.daemon_threads = False
        self.server.block_on_close = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def _finish(self):
        self.server.shutdown()
        self.server.server_close()  # joins every handler thread (block_on_close)

    def _get(self, conn, path, headers=None):
        conn.request("GET", path, headers=headers or {})
        response = conn.getresponse()
        response.read()
        return response.status

    def test_list_open_reads_and_errors_are_counted(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
        self.assertEqual(self._get(conn, "/games.csv"), 200)
        for start in (0, 4096):
            self.assertEqual(self._get(conn, "/SLUS_201.74.Game.iso",
                                       {"Range": "bytes={}-{}".format(start, start + 4095)}), 206)
        self.assertEqual(self._get(conn, "/missing.iso"), 404)
        self.assertEqual(self._get(conn, "/SLUS_201.74.Game.iso", {"Range": "bytes=9000-9001"}), 416)
        conn.close()
        self._finish()
        fields = ss.parse_line(self.stats.stop()[0])
        self.assertEqual((fields["list"], fields["open"], fields["read"], fields["bytes"], fields["errors"]),
                         (1, 1, 2, 8192, 2))
        self.assertGreaterEqual(fields["connections"], 1)

    def test_connection_close_request_still_counts_its_read(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
        self.assertEqual(self._get(conn, "/SLUS_201.74.Game.iso",
                                   {"Range": "bytes=0-4095", "Connection": "close"}), 206)
        conn.close()
        self._finish()
        fields = ss.parse_line(self.stats.stop()[0])
        self.assertEqual((fields["read"], fields["bytes"], fields["errors"]), (1, 4096, 0))

    def test_off_by_default(self):
        self.assertIsNone(hs.Ps2HTTPServer.__init__.__defaults__[-1])
        self.assertNotIn("--session-summary",
                         servers.HTTP.build_argv({"root_dir": "x"}))
        self.assertIn("--session-summary",
                      servers.HTTP.build_argv({"root_dir": "x", "session_summary": True}))


class UdpbdWiringTests(unittest.TestCase):
    """UDPBD counts per console: mounts, reads with bytes, writes and errors."""

    def _server(self, read_only):
        sys.path.insert(0, os.path.join(ROOT, "udpbd_server"))
        import udpbd_server as ub

        self.ub = ub
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        image = os.path.join(tmp.name, "disk.img")
        with open(image, "wb") as f:
            f.write(b"\0" * (64 * 512))
        device = ub.BlockDevice(image, read_only=read_only)
        self.addCleanup(device.close)

        class Sock:
            def __init__(self):
                self.sent = []

            def sendto(self, data, addr):
                self.sent.append((data, addr))

        server = ub.UdpbdServer.__new__(ub.UdpbdServer)
        server.bd = device
        server.verbose = False
        server._total_read = server._total_write = 0
        server._write_left = 0
        server._write_owner = None
        server._block_shift = None
        server._set_block_shift(5)
        server.sock = Sock()
        self.clock, self.lines = Clock(), []
        server.stats = ss.SessionStats("udpbd", clock=self.clock, emit=self.lines.append)
        return server

    def test_mount_read_and_write_are_counted_per_console(self):
        server = self._server(read_only=False)
        ps2 = ("192.168.0.10", 0xBDBD)
        other = ("192.168.0.11", 0xBDBD)
        server._handle_info(ps2, self.ub.pack_header(self.ub.CMD_INFO, 1, 0))
        read = self.ub.pack_header(self.ub.CMD_READ, 2, 0)
        read = struct.pack("<HIH", struct.unpack("<H", read)[0], 0, 4)
        server._handle_read(ps2, read)
        write = struct.pack("<HIH", struct.unpack("<H", self.ub.pack_header(
            self.ub.CMD_WRITE, 3, 0))[0], 8, 1)
        server._handle_write(ps2, write)
        server._handle_info(other, self.ub.pack_header(self.ub.CMD_INFO, 1, 0))
        lines = {ss.parse_line(line)["client"]: ss.parse_line(line)
                 for line in server.stats.stop()}
        mine = lines["192.168.0.10"]
        self.assertEqual((mine["service"], mine["connections"], mine["read"], mine["bytes"],
                          mine["write"], mine["errors"], mine["ended"]),
                         ("udpbd", 1, 1, 4 * 512, 1, 0, "stopped"))
        self.assertEqual((lines["192.168.0.11"]["connections"], lines["192.168.0.11"]["read"]),
                         (1, 0))

    def test_failed_send_does_not_count_bytes(self):
        server = self._server(read_only=False)

        class FailingSock:
            def sendto(self, data, addr):
                raise OSError(5, "send failed")

        server.sock = FailingSock()
        read = struct.pack("<HIH", struct.unpack("<H", self.ub.pack_header(
            self.ub.CMD_READ, 2, 0))[0], 0, 4)
        with self.assertRaises(OSError):
            server._handle_read(("10.0.0.2", 1), read)
        fields = ss.parse_line(server.stats.stop()[0])
        self.assertEqual((fields["read"], fields["bytes"]), (1, 0))

    def test_write_to_a_read_only_image_is_an_error(self):
        server = self._server(read_only=True)
        write = struct.pack("<HIH", struct.unpack("<H", self.ub.pack_header(
            self.ub.CMD_WRITE, 3, 0))[0], 0, 1)
        server._handle_write(("10.0.0.2", 1), write)
        fields = ss.parse_line(server.stats.stop()[0])
        self.assertEqual((fields["write"], fields["errors"]), (1, 1))

    def test_off_by_default(self):
        self.assertNotIn("--session-summary", servers.UDPBD.build_argv({"image_file": "x"}))
        self.assertIn("--session-summary",
                      servers.UDPBD.build_argv({"image_file": "x", "session_summary": True}))


class UdpfsWiringTests(unittest.TestCase):
    """A real UDPFS server on loopback, driven by the self-test's client."""

    def test_open_read_and_errors_are_counted_per_console(self):
        import multiclient_selftest as mc
        import udpfs_server as srv

        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        data = mc._make_file(root.name, "GAME.BIN", 3000, seed=7)
        server = srv.UdpfsServer(root_dir=root.name, port=mc._free_udp_port(),
                                 bind_ip="127.0.0.1")
        lines = []
        server.session_stats = ss.SessionStats("udpfs", emit=lines.append)
        runner = threading.Thread(target=server.run, daemon=True)
        runner.start()
        time.sleep(0.4)
        client = mc.UdpfsTestClient(("127.0.0.1", server.port))
        self.addCleanup(client.close)
        client.discover()
        handle, _ = client.open("GAME.BIN")
        self.assertEqual(client.read(handle, len(data)), data)
        with self.assertRaises(mc.UdpfsError):
            client.open("MISSING.CFG")      # ENOENT: a normal probe, not an error
        with self.assertRaises(mc.UdpfsError):
            client.read(9999, 16)           # EBADF: a real error
        server._shutdown = True     # stop the receive loop first, as Ctrl+C does
        runner.join(5)
        server._shutdown_all_sessions()
        server._cleanup()
        server.sock.close()
        if server.dsock is not server.sock:
            server.dsock.close()
        [line] = [l for l in lines if l.startswith("SESSION udpfs")]
        fields = ss.parse_line(line)
        self.assertEqual((fields["client"], fields["connections"], fields["open"],
                          fields["read"], fields["bytes"], fields["errors"],
                          fields["ended"]),
                         ("127.0.0.1", 1, 2, 2, len(data), 1, "stopped"))

    def test_long_read_keeps_the_summary_active(self):
        """A read whose transfer outlasts the idle timeout (a slow link acking
        one window every 30 s) must still produce one line with read=1 and its
        bytes, not an idle line without the bytes plus a second line without
        the read."""
        import udpfs_server as srv

        clock = Clock()
        peer = ("10.0.0.4", 1)
        server = srv.UdpfsServer.__new__(srv.UdpfsServer)
        server.session_stats = ss.SessionStats("udpfs", clock=clock, emit=lambda line: None)
        server._local = threading.local()      # tx_* fields live on the worker's Session
        server._local.session = types.SimpleNamespace(tx_seq_nr=0, tx_seq_nr_acked=0, tx_buffer=[])
        server._optimal_chunk_size = lambda total: 1024
        server._print_event = lambda *a, **k: None

        def send_packet(addr, payload, fin=False, hdr_size=0):
            server.tx_seq_nr += 1

        def wait_window(addr):            # the console acks a window after 30 s
            clock.now += 30
            server.session_stats.expire()  # the main loop's idle sweep runs meanwhile
            server.tx_seq_nr_acked = server.tx_seq_nr - 1
            return False

        server._send_data_packet = send_packet
        server._wait_for_window_ack = wait_window
        server._wait_for_final_ack = lambda addr: True
        # Treat the window as full after every packet so each one waits 30 s.
        server._in_flight = lambda: srv.SEND_WINDOW if server.tx_seq_nr_acked != server.tx_seq_nr - 1 else 0
        server.tx_seq_nr_acked = -1

        server._session_record(peer, "read")
        server._send_read_result(peer, 4096, b"x" * 4096)
        self.assertGreaterEqual(clock.now, 1000 + 90)  # three waits: well past the 60 s idle limit
        self.assertEqual(server.session_stats.expire(), [])
        [line] = server.session_stats.stop()
        fields = ss.parse_line(line)
        self.assertEqual((fields["read"], fields["bytes"]), (1, 4096))

    def test_unconfirmed_read_does_not_count_bytes(self):
        import udpfs_server as srv

        peer = ("10.0.0.3", 1)
        for confirmed, expected in ((False, 0), (None, 0), (True, 512)):
            server = srv.UdpfsServer.__new__(srv.UdpfsServer)
            server.session_stats = ss.SessionStats("udpfs", emit=lambda line: None)
            server._send_raw_data_with_header = lambda *a, c=confirmed: c
            server._session_record(peer, "read")
            server._send_read_result(peer, 512, b"x" * 512)
            fields = ss.parse_line(server.session_stats.stop()[0])
            with self.subTest(confirmed=confirmed):
                self.assertEqual(fields["bytes"], expected)

    def test_off_by_default(self):
        self.assertNotIn("--session-summary", servers.UDPFS.build_argv({"root_dir": "x"}))
        self.assertIn("--session-summary",
                      servers.UDPFS.build_argv({"root_dir": "x", "session_summary": True}))


class Smbv1WiringTests(unittest.TestCase):
    """serve_conn counts SMB1 commands; handlers are stubbed to fix their status."""

    def test_commands_bytes_and_errors_are_counted(self):
        import socket
        sys.path.insert(0, os.path.join(ROOT, "smbv1_server"))
        import smbserver_opl as smb

        def handler(params, data, status):
            return lambda conn, r: (params, data, status)

        stubs = {
            smb.SMB_COM_NT_CREATE_ANDX: handler(None, None, smb.STATUS_OBJECT_NAME_NOT_FOUND),
            smb.SMB_COM_READ_ANDX: handler(b"\0" * 24, b"x" * 100, smb.STATUS_SUCCESS),
            smb.SMB_COM_CLOSE: handler(None, None, smb.STATUS_ACCESS_DENIED),
        }
        saved = {k: smb.HANDLERS[k] for k in stubs}
        smb.HANDLERS.update(stubs)
        self.addCleanup(smb.HANDLERS.update, saved)

        server = smb.SmbServer({}, read_only=True)
        server.session_stats = ss.SessionStats("smbv1", emit=lambda _l: None)
        ours, theirs = socket.socketpair()
        self.addCleanup(theirs.close)
        runner = threading.Thread(target=smb.serve_conn,
                                  args=(server, ours, ("192.168.0.20", 50000)), daemon=True)
        runner.start()

        def request(cmd):
            smb.send_msg(theirs, smb.pack_header(cmd, 0, 1, 1, 1, 1) + smb.build_body(b"", b""))
            smb.recv_msg(theirs)

        request(smb.SMB_COM_NT_CREATE_ANDX)   # missing optional file: a probe
        request(smb.SMB_COM_READ_ANDX)        # 100 bytes served
        request(smb.SMB_COM_CLOSE)            # refused: an error
        request(0x99)                         # unsupported command: an error
        theirs.shutdown(socket.SHUT_RDWR)
        runner.join(5)
        fields = ss.parse_line(server.session_stats.stop()[0])
        self.assertEqual((fields["service"], fields["client"], fields["connections"],
                          fields["open"], fields["read"], fields["bytes"], fields["close"],
                          fields["errors"]),
                         ("smbv1", "192.168.0.20", 1, 1, 1, 100, 1, 2))

    def test_off_by_default(self):
        values = {"games_folder": "x"}
        self.assertNotIn("--session-summary", servers.SMBV1.build_argv(values))
        self.assertIn("--session-summary",
                      servers.SMBV1.build_argv(dict(values, session_summary=True)))


class Smb2WiringTests(unittest.TestCase):
    """A real SMB2 server on loopback, driven by the test suite's client."""

    def _server(self, lines):
        import shutil
        from tests import test_smb2_server as t2

        share_dir = tempfile.mkdtemp(prefix="ps2smb2-session-")
        self.addCleanup(shutil.rmtree, share_dir, True)
        with open(os.path.join(share_dir, "game.iso"), "wb") as f:
            f.write(b"\x5a" * 4096)
        os.mkdir(os.path.join(share_dir, "APPS"))
        server = t2.Smb2Server({"games": t2.Share("games", share_dir)},
                               t2.Authenticator({t2.USER: t2.PASSWORD}),
                               ceiling=t2.wire.CEILING_SMB3)
        server.session_stats = ss.SessionStats("smbv3", emit=lines.append)
        port = server.listen("127.0.0.1", 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        client = t2.Client(port)
        self.addCleanup(client.close)
        client.negotiate()
        self.assertEqual(client.session_setup(t2.USER, t2.PASSWORD), 0)
        self.assertEqual(client.tree_connect("games"), 0)
        return server, client, t2.wire

    def test_commands_bytes_and_errors_are_counted(self):
        lines = []
        server, client, wire = self._server(lines)

        status, fid = client.create("game.iso")
        self.assertEqual(status, 0)
        self.assertEqual(client.read(fid, 0, 1024)[1], b"\x5a" * 1024)
        self.assertEqual(client.read(fid, 1 << 20, 512)[0], wire.STATUS_END_OF_FILE)  # normal
        self.assertEqual(client.close_handle(fid), 0)
        status, _ = client.create("MISSING.CFG")                       # a probe, not an error
        self.assertEqual(status, wire.STATUS_OBJECT_NAME_NOT_FOUND)
        status, root = client.create("", directory=True)
        self.assertEqual(status, 0)
        # One listing is two QUERY_DIRECTORY requests: the page, then the
        # NO_MORE_FILES that ends it. Both count, as FIND_NEXT2 does on SMBv1.
        self.assertIn("game.iso", client.query_directory(root, "*"))
        self.assertNotEqual(client.read(b"\xff" * 16, 0, 16)[0], 0)   # bogus handle: an error
        self.assertNotEqual(client.write(root, 0, b"x")[0], 0)         # write to a folder: an error

        server.stop()                                                   # prints the final line
        self.assertEqual(len(lines), 1)
        fields = ss.parse_line(lines[0])
        self.assertEqual((fields["service"], fields["client"], fields["connections"],
                          fields["list"], fields["open"], fields["read"], fields["bytes"],
                          fields["write"], fields["close"], fields["errors"], fields["ended"]),
                         ("smbv3", "127.0.0.1", 1, 2, 3, 3, 1024, 1, 1, 2, "stopped"))

    def test_uncounted_commands_keep_the_session_alive(self):
        lines = []
        server, client, _wire = self._server(lines)
        clock = Clock()
        server.session_stats = ss.SessionStats("smbv3", emit=lines.append, clock=clock)
        server.session_stats.record("127.0.0.1", "connections")
        clock.now += 50
        client.echo()                      # counts nothing, but the console is here
        self.assertEqual(server.session_stats.expire(), [])
        clock.now += 50                    # 100 s after the connection, 50 s after the echo
        self.assertEqual(server.session_stats.expire(), [])
        clock.now += 20                    # now 70 s quiet
        self.assertEqual(len(server.session_stats.expire()), 1)
        self.assertEqual(ss.parse_line(lines[0])["ended"], "idle")
        server.stop()

    def test_ntlm_challenge_is_not_an_error(self):
        lines = []
        server, _client, _wire = self._server(lines)
        server.stop()
        self.assertEqual(ss.parse_line(lines[0])["errors"], 0)

    def test_off_by_default(self):
        values = {"games_folder": "x", "open_share": True}
        for definition in (servers.SMBV2, servers.SMBV3):
            self.assertNotIn("--session-summary", definition.build_argv(values))
            self.assertIn("--session-summary",
                          definition.build_argv(dict(values, session_summary=True)))


if __name__ == "__main__":
    unittest.main()
