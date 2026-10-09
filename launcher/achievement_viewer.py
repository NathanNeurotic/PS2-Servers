"""Optional read-only LAN view; account controls stay on the loopback engine."""
import http.server
import ipaddress
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request


class Viewer:
    def __init__(self, account_port, port=18199, host="0.0.0.0"):
        self.account_port = account_port
        self.stopped = threading.Event()
        self.slots = threading.BoundedSemaphore(4)
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def setup(self):
                super().setup()
                self.connection.settimeout(5)

            def log_message(self, *_args):
                pass

            def do_POST(self):
                self.send_error(403, "Account controls are available only on the PC")

            do_PUT = do_DELETE = do_PATCH = do_POST

            def do_GET(self):
                # Literal address hosts prevent DNS rebinding. Foreign browser
                # origins cannot use the viewer as an account-data proxy.
                host_header = self.headers.get("Host", "")
                try:
                    authority = urllib.parse.urlsplit("http://" + host_header)
                    address = ipaddress.ip_address(authority.hostname or "")
                    if authority.username or authority.password or authority.port != owner.server.server_port:
                        raise ValueError()
                    if address.is_unspecified or address.is_multicast:
                        raise ValueError()
                except ValueError:
                    self.send_error(403, "Open the viewer using this PC's IP address")
                    return
                origin = self.headers.get("Origin")
                if (origin and origin != "http://" + host_header) or self.headers.get("Sec-Fetch-Site") == "cross-site":
                    self.send_error(403)
                    return
                target = urllib.parse.urlsplit(self.path)
                if target.scheme or target.netloc or target.path not in (
                        "/", "/index.html", "/state", "/events", "/profile", "/library", "/game", "/boards"):
                    self.send_error(404)
                    return
                if not owner.slots.acquire(blocking=False):
                    self.send_error(503, "Four viewers are already connected")
                    return
                try:
                    request = urllib.request.Request(
                        "http://127.0.0.1:{}{}".format(owner.account_port, self.path))
                    with urllib.request.urlopen(request, timeout=3) as response:
                        self.send_response(200)
                        self.send_header("Content-Type", response.headers.get("Content-Type", "application/octet-stream"))
                        self.send_header("Cache-Control", "no-store")
                        self.send_header("X-Content-Type-Options", "nosniff")
                        self.send_header("Connection", "close")
                        self.end_headers()
                        self.close_connection = True
                        if target.path == "/events":
                            while not owner.stopped.is_set():
                                chunk = response.readline(65536)
                                if not chunk:
                                    break
                                self.wfile.write(chunk)
                                self.wfile.flush()
                        else:
                            data = response.read(2 * 1024 * 1024 + 1)
                            if len(data) <= 2 * 1024 * 1024:
                                self.wfile.write(data)
                except (OSError, urllib.error.URLError):
                    self.close_connection = True
                finally:
                    owner.slots.release()

        class Server(http.server.ThreadingHTTPServer):
            daemon_threads = True
            block_on_close = False
            allow_reuse_address = False

            def server_bind(self):
                if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                super().server_bind()

        self.server = Server((host, port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self):
        self.thread.start()

    def close(self):
        self.stopped.set()
        if self.thread.is_alive():
            self.server.shutdown()
            self.thread.join(timeout=3)
        self.server.server_close()
