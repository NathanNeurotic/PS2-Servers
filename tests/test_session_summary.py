"""Opt-in SESSION summaries: bounded, one line per session, nothing per request."""
import http.client
import os
import sys
import tempfile
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _path in (ROOT, os.path.join(ROOT, "http_server"), os.path.join(ROOT, "udpfs_server")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import http_server as hs  # noqa: E402
import session_stats as ss  # noqa: E402
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
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

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
        self.server.shutdown()
        fields = ss.parse_line(self.stats.stop()[0])
        self.assertEqual((fields["list"], fields["open"], fields["read"], fields["bytes"], fields["errors"]),
                         (1, 1, 2, 8192, 2))
        self.assertGreaterEqual(fields["connections"], 1)

    def test_off_by_default(self):
        self.assertIsNone(hs.Ps2HTTPServer.__init__.__defaults__[-1])
        self.assertNotIn("--session-summary",
                         servers.HTTP.build_argv({"root_dir": "x"}))
        self.assertIn("--session-summary",
                      servers.HTTP.build_argv({"root_dir": "x", "session_summary": True}))


if __name__ == "__main__":
    unittest.main()
