"""The launcher's Stop must let a server run its own shutdown code.

On Windows, ``Popen.terminate()`` is TerminateProcess: nothing in the child
runs, so a stopped server never printed its totals or its final ``SESSION``
lines, and an SMB server started with take-445 never restarted Windows file
sharing (LanmanServer) until the next reboot.

The launcher now starts these servers with a stop pipe on stdin and closes it
to ask them to stop; ``launcher/serve.py`` turns that EOF into the same
KeyboardInterrupt Ctrl+C raises, and only after a grace period does Stop fall
back to terminate(). These tests drive the real ``--serve`` path with
terminate() disabled, so they pass only when the pipe alone stops the server.

Run:  python -m unittest tests.test_cooperative_stop -v
"""

import _thread
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _path in (ROOT, os.path.join(ROOT, "smb2_server"), os.path.join(ROOT, "udpbd_server")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from launcher import serve, servers  # noqa: E402
from launcher.process import STOP_PIPE_ENV, ServerProcess  # noqa: E402
import smb2_server  # noqa: E402
import udpbd_server  # noqa: E402
from unittest import mock  # noqa: E402
from smbv1_server import smbserver_opl  # noqa: E402

# Python 3.12 + -S style runs (see docs/ISSUE-249-HANDOFF.md) need the same
# interpreter flags in the child; plain sys.executable is what the launcher uses.
PYTHON = sys.executable


def _free_tcp_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _NoTerminate:
    """Fail loudly if Stop had to fall back to ending the process."""

    def __init__(self, proc):
        self.proc = proc
        self.called = False
        real = proc._proc

        def refuse():
            self.called = True
            real.kill()  # do not leave the child behind; the test then fails

        real.terminate = refuse


class StopHandlerUnitTests(unittest.TestCase):
    def test_no_pipe_flag_means_no_watcher(self):
        env = {}
        self.assertIsNone(serve.install_stop_handlers(environ=env, stdin=None,
                                                      interrupt=lambda: None))

    def test_eof_on_the_pipe_interrupts_once_and_consumes_the_flag(self):
        read_fd, write_fd = os.pipe()
        fired = threading.Event()
        env = {STOP_PIPE_ENV: "1", "OTHER": "kept"}
        with os.fdopen(read_fd, "rb") as reader:
            watcher = serve.install_stop_handlers(environ=env, stdin=reader,
                                                  interrupt=fired.set)
            self.assertIsNotNone(watcher)
            self.assertNotIn(STOP_PIPE_ENV, env)  # children do not inherit it
            self.assertEqual(env["OTHER"], "kept")
            self.assertFalse(fired.wait(0.2))     # an open pipe is not a stop
            os.close(write_fd)
            self.assertTrue(fired.wait(5))
            watcher.join(5)
            self.assertFalse(watcher.is_alive())

    def test_unreadable_stdin_never_stops_the_server(self):
        class Broken:
            def read(self, _n):
                raise OSError("no stdin")

        fired = threading.Event()
        watcher = serve.install_stop_handlers(environ={STOP_PIPE_ENV: "1"}, stdin=Broken(),
                                              interrupt=fired.set)
        watcher.join(5)
        self.assertFalse(fired.is_set())

    def test_registry_marks_only_checked_servers(self):
        cooperative = {k for k, s in servers.REGISTRY.items() if s.cooperative_stop}
        self.assertEqual(cooperative, {"smbv1", "smbv2", "smbv3", "udpfs", "http", "udpbd"})
        for key in cooperative:
            self.assertEqual(servers.REGISTRY[key].runtime, "python", key)


class ProcessGraceTests(unittest.TestCase):
    def test_non_cooperative_child_is_terminated_as_before(self):
        proc = ServerProcess("t", [PYTHON, "-c", "import time; time.sleep(60)"])
        proc.start()
        self.assertFalse(proc.request_stop())
        started = time.monotonic()
        proc.stop(timeout=5)
        self.assertLess(time.monotonic() - started, 5)
        self.assertFalse(proc.is_running())

    def test_child_that_ignores_the_pipe_is_ended_after_the_grace(self):
        proc = ServerProcess("t", [PYTHON, "-c", "import time; time.sleep(60)"],
                             cooperative_stop=True, stop_grace=0.5)
        proc.start()
        started = time.monotonic()
        proc.stop(timeout=5)
        self.assertLess(time.monotonic() - started, 5)
        self.assertFalse(proc.is_running())
        self.assertTrue(any("did not stop within" in line for line in proc.lines))

    def test_grace_counts_from_the_request(self):
        procs = [ServerProcess("t%d" % i, [PYTHON, "-c", "import time; time.sleep(60)"],
                               cooperative_stop=True, stop_grace=1.0) for i in range(3)]
        for proc in procs:
            proc.start()
        for proc in procs:
            self.assertTrue(proc.request_stop())
        started = time.monotonic()
        for proc in procs:
            proc.stop(timeout=5)
        # three servers asked together cost about one grace, not three
        self.assertLess(time.monotonic() - started, 2.5)


class RealServerStopTests(unittest.TestCase):
    """Each checked server, started the way the launcher starts it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _run(self, key, argv, ready, poke=None):
        command = [PYTHON, "-m", "launcher", "--serve", key, *argv]
        proc = ServerProcess(key, command, cwd=ROOT, cooperative_stop=True, stop_grace=10)
        proc.start()
        self.addCleanup(lambda: proc._proc.poll() is None and proc._proc.kill())
        deadline = time.monotonic() + 20
        while not any(ready in line for line in list(proc.lines)):
            if not proc.is_running():
                self.fail("{} exited early:\n{}".format(key, "\n".join(proc.lines)))
            if time.monotonic() > deadline:
                self.fail("{} never became ready:\n{}".format(key, "\n".join(proc.lines)))
            time.sleep(0.05)
        if poke:
            poke()
        guard = _NoTerminate(proc)
        started = time.monotonic()
        proc.stop(timeout=5)
        elapsed = time.monotonic() - started
        output = "\n".join(proc.lines)
        self.assertFalse(guard.called, "{} needed terminate():\n{}".format(key, output))
        self.assertLess(elapsed, 8, output)
        return proc, output

    def test_http_prints_its_final_session_line(self):
        port = _free_tcp_port()
        with open(os.path.join(self.tmp.name, "GAME.iso"), "wb") as f:
            f.write(b"\0" * 4096)

        def fetch():
            urllib.request.urlopen("http://127.0.0.1:%d/games.csv" % port, timeout=5).read()

        proc, output = self._run("http", ["--root-dir", self.tmp.name, "--port", str(port),
                                          "--bind", "127.0.0.1", "--session-summary"],
                                 ready="listening on", poke=fetch)
        self.assertIn("SESSION http client=127.0.0.1", output)
        self.assertIn("ended=stopped", output)
        self.assertEqual(proc.returncode, 0, output)

    def test_smbv1_unwinds_while_idle(self):
        port = _free_tcp_port()
        proc, output = self._run("smbv1", ["--share", "games=" + self.tmp.name,
                                           "--port", str(port), "--bind", "127.0.0.1"],
                                 ready="In OPL set")
        self.assertIn("shutting down", output)
        self.assertEqual(proc.returncode, 0, output)

    def test_smb3_unwinds_while_idle(self):
        port = _free_tcp_port()
        proc, _output = self._run("smbv3", ["--share", "games=" + self.tmp.name,
                                            "--port", str(port), "--bind", "127.0.0.1",
                                            "--smb-version", "3", "--open"],
                                  ready="server on")
        self.assertEqual(proc.returncode, 0)


class Smb2AcceptLoopTests(unittest.TestCase):
    def test_idle_accept_loop_lets_ctrl_c_land(self):
        server = smb2_server.Smb2Server({}, None)
        server.listen("127.0.0.1", 0)
        self.addCleanup(server.stop)
        timer = threading.Timer(0.3, _thread.interrupt_main)
        timer.start()
        self.addCleanup(timer.cancel)
        started = time.monotonic()
        with self.assertRaises(KeyboardInterrupt):
            server.serve_forever(poll_seconds=0.2)
        self.assertLess(time.monotonic() - started, 3)

    def test_accepted_connections_stay_blocking(self):
        server = smb2_server.Smb2Server({}, None)
        port = server.listen("127.0.0.1", 0)
        seen = []

        def record(sock, _peer):
            seen.append(sock.gettimeout())
            sock.close()
            server.stop()

        server._serve_client = record
        client = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.addCleanup(client.close)
        runner = threading.Thread(target=server.serve_forever, kwargs={"poll_seconds": 0.2},
                                  daemon=True)
        runner.start()
        runner.join(5)
        self.assertEqual(seen, [None])


class UdpbdLoopTests(unittest.TestCase):
    def _server(self, sock):
        server = udpbd_server.UdpbdServer.__new__(udpbd_server.UdpbdServer)
        server.sock = sock
        server.verbose = False
        server._bind_addr = "127.0.0.1"
        server._total_read = server._total_write = 0

        class Device:
            read_only = True
            path = "fake.img"
            size = 1024 * 1024

            def sector_count(self):
                return 2048

            def close(self):
                pass

        server.bd = Device()
        return server

    def test_idle_loop_lets_ctrl_c_land(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", 0))
        server = self._server(sock)
        timer = threading.Timer(0.3, _thread.interrupt_main)
        timer.start()
        self.addCleanup(timer.cancel)
        started = time.monotonic()
        server.run()  # returns after printing its totals
        self.assertLess(time.monotonic() - started, 4)

    def test_windows_connection_reset_does_not_end_the_server(self):
        calls = []

        class ResetSock:
            def fileno(self):
                return 0

            def recvfrom(self, _n):
                calls.append(1)
                if len(calls) == 1:
                    raise ConnectionResetError(10054, "reset by peer")
                raise KeyboardInterrupt()

            def close(self):
                pass

        server = self._server(ResetSock())
        real_select = udpbd_server.select.select
        udpbd_server.select.select = lambda r, w, x, t: (r, [], [])
        self.addCleanup(setattr, udpbd_server.select, "select", real_select)
        server.run()
        self.assertEqual(len(calls), 2)  # the reset was survived; Ctrl+C ended it


class _FakePs:
    """Stands in for subprocess.run in the take-445 helpers. Records each
    PowerShell command; raises KeyboardInterrupt on the first one containing
    `interrupt_on` (a stop request landing while that command runs)."""

    def __init__(self, interrupt_on=None, status="Running"):
        self.commands = []
        self.interrupt_on = interrupt_on
        self.status = status

    def __call__(self, argv, **kwargs):
        cmd = argv[-1]
        self.commands.append(cmd)
        if self.interrupt_on and self.interrupt_on in cmd:
            self.interrupt_on = None
            raise KeyboardInterrupt
        if "Stop-Service" in cmd:
            self.status = "Stopped"
        out = self.status if ".Status" in cmd else ""
        return mock.Mock(returncode=0, stdout=out, stderr="")

    def starts(self):
        return [c for c in self.commands if c.startswith("Start-Service LanmanServer")]


class Take445InterruptTests(unittest.TestCase):
    """A stop request can land before the accept loop: while LanmanServer is
    stopping, during the bind retries, or during the banner. Each case must
    still start Windows file sharing again, exactly once."""

    def test_smbv1_interrupt_while_service_stops_restores(self):
        fake = _FakePs(interrupt_on="Stop-Service")
        with mock.patch("subprocess.run", fake):
            with self.assertRaises(KeyboardInterrupt):
                smbserver_opl._take_445("127.0.0.1")
        self.assertEqual(len(fake.starts()), 1)

    def test_smbv1_restore_runs_once(self):
        fake = _FakePs()
        with mock.patch("subprocess.run", fake), \
                mock.patch.object(smbserver_opl, "_port_free", return_value=True):
            restore = smbserver_opl._take_445("127.0.0.1")
            restore()
            restore()
        self.assertEqual(len(fake.starts()), 1)

    def test_smbv1_interrupt_during_bind_restores(self):
        restore = mock.Mock()
        share = tempfile.mkdtemp()
        with mock.patch.object(smbserver_opl, "_take_445", return_value=restore), \
                mock.patch.object(smbserver_opl, "_bind_listener",
                                  side_effect=KeyboardInterrupt):
            rc = smbserver_opl.main(["--share", "games=" + share, "--take-445"])
        self.assertEqual(rc, 0)
        restore.assert_called_once_with()

    def test_smbv1_failed_bind_restores_once(self):
        restore = mock.Mock()
        share = tempfile.mkdtemp()
        with mock.patch.object(smbserver_opl, "_take_445", return_value=restore), \
                mock.patch.object(smbserver_opl, "_bind_listener", return_value=None):
            rc = smbserver_opl.main(["--share", "games=" + share, "--take-445"])
        self.assertEqual(rc, 2)
        restore.assert_called_once_with()

    def test_smb2_interrupt_while_service_stops_restores(self):
        fake = _FakePs(interrupt_on="Stop-Service")
        with mock.patch("subprocess.run", fake):
            with self.assertRaises(KeyboardInterrupt):
                smb2_server.take_445()
        self.assertEqual(len(fake.starts()), 1)

    def test_smb2_interrupt_during_listen_restores(self):
        restore = mock.Mock()
        share = tempfile.mkdtemp()
        with mock.patch.object(smb2_server, "take_445", return_value=restore):
            with mock.patch.object(smb2_server.Smb2Server, "listen", side_effect=KeyboardInterrupt):
                rc = smb2_server.main(["--share", "games=" + share, "--open", "--take-445"])
        self.assertEqual(rc, 0)
        restore.assert_called_once_with()

    def test_smb2_failed_bind_restores_once(self):
        restore = mock.Mock()
        share = tempfile.mkdtemp()
        with mock.patch.object(smb2_server, "take_445", return_value=restore):
            with mock.patch.object(smb2_server.Smb2Server, "listen",
                                   side_effect=OSError(98, "in use")):
                rc = smb2_server.main(["--share", "games=" + share, "--open", "--take-445"])
        self.assertEqual(rc, 1)
        restore.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
