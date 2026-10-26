"""Startup diagnostics must describe bound sockets and effective settings."""

import contextlib
import importlib.util
import io
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
UDPFS_DIR = ROOT / "udpfs_server"
if str(UDPFS_DIR) not in sys.path:
    sys.path.insert(0, str(UDPFS_DIR))
SPEC = importlib.util.spec_from_file_location(
    "core_startup_diagnostics", UDPFS_DIR / "ps2servers_core.py")
CORE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CORE)


class StartupDiagnosticsTests(unittest.TestCase):
    def _banner(self, **options):
        with tempfile.TemporaryDirectory() as root:
            with contextlib.redirect_stdout(io.StringIO()):
                server = CORE.AutoUdpfsServer(
                    root_dir=root, port=0, bind_ip="127.0.0.1", data_port=0,
                    enable_compression=False, **options)
            try:
                discovery = server.sock.getsockname()
                data = server.dsock.getsockname()
                server._shutdown = True  # exercise startup without receiving traffic
                output = io.StringIO()
                with mock.patch.object(CORE, "_startup_version", return_value="v1.2.3 (abcdef0)"), \
                        contextlib.redirect_stdout(output):
                    server.run()
                return output.getvalue(), discovery, data
            finally:
                server._shutdown_all_sessions()
                server._cleanup()
                server.sock.close()
                if server.dsock is not server.sock:
                    server.dsock.close()

    def test_actual_ephemeral_ports_and_distinct_bind_interfaces(self):
        text, discovery, data = self._banner()
        self.assertNotEqual(discovery[1], 0)
        self.assertNotEqual(data[1], 0)
        self.assertIn(f"Discovery bind: {discovery[0]}:{discovery[1]}", text)
        self.assertIn(f"Data bind: {data[0]}:{data[1]}", text)
        self.assertEqual(data[0], "127.0.0.1")
        self.assertIn("Build: v1.2.3 (abcdef0)", text)

    def test_effective_clamped_timeout_and_read_only_mode(self):
        text, _, _ = self._banner(peer_timeout=1, read_only=True, verbose=True)
        self.assertIn("Idle timeout: 60s", text)
        self.assertIn("Mode: read-only", text)
        self.assertIn("Verbose: enabled", text)

    def test_overnight_timeout_and_default_write_mode(self):
        text, _, _ = self._banner(peer_timeout=86400)
        self.assertIn("Idle timeout: 86400s", text)
        self.assertIn("Mode: read-write", text)
        self.assertIn("Verbose: disabled", text)

    def test_single_port_reports_the_shared_bound_socket(self):
        text, discovery, data = self._banner(single_port=True)
        self.assertEqual(discovery, data)
        self.assertIn(f"Discovery bind: {discovery[0]}:{discovery[1]}", text)
        self.assertIn(f"Data bind: {data[0]}:{data[1]}", text)


if __name__ == "__main__":
    unittest.main()
