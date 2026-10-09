import http.server
import json
import threading
import unittest
import urllib.error
import urllib.request

from launcher.achievement_viewer import Viewer
from launcher.servers import RETROACHIEVEMENTS
from launcher.windows_setup import server_ports


class ViewerTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        owner = self

        class Account(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                owner.requests.append((self.path, self.headers.get("Host")))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"game": "Example", "path": self.path}).encode())

            def log_message(self, *_args):
                pass

        self.account = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Account)
        self.thread = threading.Thread(target=self.account.serve_forever, daemon=True)
        self.thread.start()
        self.viewer = Viewer(self.account.server_port, port=0, host="127.0.0.1")
        self.viewer.start()
        self.url = "http://127.0.0.1:{}/".format(self.viewer.server.server_port)

    def tearDown(self):
        self.viewer.close()
        self.account.shutdown()
        self.account.server_close()
        self.thread.join(timeout=2)

    def test_read_only_data_and_query_reach_only_private_engine(self):
        with urllib.request.urlopen(self.url + "game?id=12", timeout=2) as response:
            self.assertEqual(json.load(response)["path"], "/game?id=12")
            self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(self.requests, [("/game?id=12", "127.0.0.1:{}".format(self.account.server_port))])

    def test_account_writes_and_unlisted_routes_never_reach_engine(self):
        for path, method, status in (("logout", "POST", 403), ("apikey", "GET", 404),
                                     ("quit", "POST", 403), ("login", "PUT", 403)):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(urllib.request.Request(self.url + path, method=method), timeout=2)
            self.assertEqual(caught.exception.code, status)
            caught.exception.close()
        self.assertEqual(self.requests, [])

    def test_foreign_host_and_origin_cannot_read_account_data(self):
        for headers in ({"Host": "attacker.example"}, {"Origin": "https://attacker.example"},
                        {"Sec-Fetch-Site": "cross-site"}):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(urllib.request.Request(self.url + "state", headers=headers), timeout=2)
            self.assertEqual(caught.exception.code, 403)
            caught.exception.close()
        self.assertEqual(self.requests, [])

    def test_excess_viewers_do_not_consume_private_engine_connections(self):
        for _ in range(2):
            self.viewer.slots.acquire()
        try:
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(self.url + "state", timeout=2)
            self.assertEqual(caught.exception.code, 503)
            caught.exception.close()
            self.assertEqual(self.requests, [])
        finally:
            for _ in range(2):
                self.viewer.slots.release()

    def test_launcher_and_firewall_require_explicit_opt_in(self):
        self.assertNotIn("--lan-viewer", RETROACHIEVEMENTS.build_argv({}))
        args = RETROACHIEVEMENTS.build_argv({"lan_viewer": True, "obs_folder": "Stream labels"})
        self.assertIn("--lan-viewer", args)
        self.assertEqual(args[args.index("--obs-folder") + 1], "Stream labels")
        self.assertNotIn(18199, [p for _, p, _ in server_ports("retroachievements", {})])
        self.assertIn(("TCP", 18199, "Read-only achievements viewer"),
                      server_ports("retroachievements", {"lan_viewer": True}))
