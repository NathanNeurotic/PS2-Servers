"""Regression tests for Windows TCP 445 takeover in SMBv2/SMBv3.

Service-control calls are mocked: these tests must not stop real Windows file
sharing or bind the real system's TCP 445.
"""

import errno
import os
import re
import subprocess
import sys
import unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SMB2 = os.path.join(_ROOT, "smb2_server")
for path in (_ROOT, _SMB2):
    if path not in sys.path:
        sys.path.insert(0, path)

import smb2_server as smb2
from launcher import gui


_GET = re.compile(r"\(Get-Service (\S+) -ErrorAction SilentlyContinue\)\.(\w+)")
_STOP = re.compile(r"Stop-Service -Name (\S+)")
_START = re.compile(r"Start-Service (\S+)")

# Who depends on whom on a real Windows 11 box (sc qc / sc enumdepend):
# LanmanServer and srv need srv2; srv2 needs srvnet. Docker Desktop's service
# also depends on srv2 when it is installed.
_NEEDS = {"LanmanServer": "srv2", "srv": "srv2", "srv2": "srvnet"}


class FakeServices:
    """A synthetic Windows file-sharing stack: the LanmanServer service plus
    the srv/srv2/srvnet kernel drivers, with the real dependency rules. TCP 445
    stays bound exactly as long as srvnet runs."""

    def __init__(self, initial="Running", delayed_polls=0, stop_failed=False,
                 drivers=None, pinned=()):
        self.state = initial  # LanmanServer
        self.drivers = dict(drivers) if drivers is not None else {
            "srv": "Running", "srv2": "Running", "srvnet": "Running"}
        self.pinned = list(pinned)  # foreign Running services that need srv2
        self.delayed_polls = delayed_polls
        self.stop_failed = stop_failed
        self.commands = []
        self.stop_issued = False

    def port_free(self):
        return self.drivers.get("srvnet") != "Running"

    def _status(self, name):
        if name == "LanmanServer":
            if self.stop_issued and self.state != "Stopped":
                if self.delayed_polls > 0:
                    self.delayed_polls -= 1
                    return "StopPending"
                self.state = "Stopped"
            return self.state
        if name == "Browser":
            return "Stopped"
        return self.drivers.get(name, "")

    def _running_dependents(self, name):
        deps = [n for n, need in _NEEDS.items() if need == name
                and self._status(n) == "Running"]
        if name == "srv2":
            deps += self.pinned
        return deps

    def run(self, args, **_kwargs):
        """Simulate PowerShell Get-Service, Stop-Service, and Start-Service."""
        cmd = args[-1]
        self.commands.append(cmd)
        m = _GET.match(cmd)
        if m:
            name, what = m.groups()
            if what == "DependentServices":
                return subprocess.CompletedProcess(
                    args, 0, "\n".join(self._running_dependents(name)) + "\n", "")
            return subprocess.CompletedProcess(args, 0, self._status(name) + "\n", "")
        m = _STOP.match(cmd)
        if m:
            name = m.group(1)
            if name == "LanmanServer":
                if self.stop_failed:
                    return subprocess.CompletedProcess(args, 1, "", "Access denied")
                self.stop_issued = True
                return subprocess.CompletedProcess(args, 0, "", "")
            if "-Force" not in cmd and self._running_dependents(name):
                return subprocess.CompletedProcess(
                    args, 1, "", "Cannot stop %s: dependent services running" % name)
            self.drivers[name] = "Stopped"
            return subprocess.CompletedProcess(args, 0, "", "")
        m = _START.match(cmd)
        if m:
            name = m.group(1)
            if name == "LanmanServer":
                if self.stop_issued and self.delayed_polls:
                    return subprocess.CompletedProcess(
                        args, 1, "", "Cannot start while StopPending")
                if self.drivers.get("srv2") != "Running":
                    return subprocess.CompletedProcess(
                        args, 1, "", "Dependency srv2 is not running")
                self.stop_issued = False  # The stop transition is over.
                self.state = "Running"
                return subprocess.CompletedProcess(args, 0, "", "")
            if name in self.drivers:
                need = _NEEDS.get(name)
                if need and self.drivers.get(need) != "Running":
                    return subprocess.CompletedProcess(
                        args, 1, "", "Dependency %s is not running" % need)
                self.drivers[name] = "Running"
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    def starts(self, name="LanmanServer"):
        """Return attempted service restart commands."""
        return [c for c in self.commands if c.startswith("Start-Service " + name)]

    def stops(self, name="LanmanServer"):
        """Return attempted service stop commands."""
        return [c for c in self.commands if c.startswith("Stop-Service -Name " + name)]

    def driver_stops(self):
        return [_STOP.match(c).group(1) for c in self.commands
                if _STOP.match(c) and _STOP.match(c).group(1) in self.drivers]

    def driver_starts(self):
        return [_START.match(c).group(1) for c in self.commands
                if _START.match(c) and _START.match(c).group(1) in self.drivers]


class WindowsTake445Tests(unittest.TestCase):
    def test_waits_for_stopped_service_and_restores_only_once(self):
        """Only restore a service after its stop completes, and restore once."""
        fake = FakeServices(delayed_polls=2)
        with mock.patch("subprocess.run", side_effect=fake.run), \
                mock.patch.object(smb2.time, "sleep") as sleep:
            restore = smb2.take_445(port_free=lambda: True)
            self.assertTrue(callable(restore))
            self.assertEqual(fake.state, "Stopped")
            self.assertEqual(sleep.call_count, 2)
            restore()
            restore()
        self.assertEqual(fake.state, "Running")
        self.assertEqual(len(fake.stops()), 1)
        self.assertEqual(len(fake.starts()), 1)

    def test_does_not_restart_file_sharing_if_initially_stopped(self):
        """Preserve a user's previously stopped Windows File Sharing."""
        fake = FakeServices(initial="Stopped")
        with mock.patch("subprocess.run", side_effect=fake.run):
            restore = smb2.take_445()
            self.assertTrue(callable(restore))
            restore()
        self.assertEqual(fake.stops(), [])
        self.assertEqual(fake.starts(), [])
        self.assertEqual(fake.state, "Stopped")

    def test_refuses_to_bind_if_service_stop_is_denied(self):
        """Do not bind 445 when Windows rejects the stop request."""
        fake = FakeServices(stop_failed=True)
        with mock.patch("subprocess.run", side_effect=fake.run):
            self.assertIsNone(smb2.take_445())
        self.assertEqual(len(fake.stops()), 1)
        self.assertEqual(fake.starts(), [])

    def test_stop_timeout_waits_for_pending_transition_before_restore(self):
        """A stop timeout must not start LanmanServer in StopPending."""
        fake = FakeServices(delayed_polls=2)
        with mock.patch("subprocess.run", side_effect=fake.run), \
                mock.patch.object(smb2.time, "sleep"):
            self.assertIsNone(smb2.take_445(stop_timeout=0, restore_timeout=1,
                                            port_free=lambda: True))
        self.assertEqual(len(fake.starts()), 1)
        self.assertEqual(fake.state, "Running")

    def test_persistent_stop_pending_is_not_started(self):
        """Refuse unsafe recovery if Windows never completes the pending stop."""
        fake = FakeServices(delayed_polls=100)
        with mock.patch("subprocess.run", side_effect=fake.run):
            self.assertIsNone(smb2.take_445(stop_timeout=0, restore_timeout=0,
                                            port_free=lambda: True))
        self.assertEqual(fake.starts(), [])

    def test_stop_interrupt_restores_original_service(self):
        """Attempt service restoration after interrupted stop calls."""
        fake = FakeServices()

        def interrupt_after_stop(args, **kwargs):
            if args[-1].startswith("Stop-Service"):
                fake.stop_issued = True
                raise KeyboardInterrupt
            return fake.run(args, **kwargs)

        with mock.patch("subprocess.run", side_effect=interrupt_after_stop):
            with self.assertRaises(KeyboardInterrupt):
                smb2.take_445()
        self.assertEqual(len(fake.starts()), 1)


class WindowsSmbDriverTests(unittest.TestCase):
    """Measured on Windows 11 (2026-11-04): Stop-Service LanmanServer leaves
    srv, srv2 and srvnet running and TCP 445 bound by the kernel (pid 4). The
    port is released only once srv -> srv2 -> srvnet are stopped, in that
    order, and all four come back cleanly in reverse."""

    def test_pauses_drivers_in_dependency_order_and_restores_in_reverse(self):
        fake = FakeServices()
        with mock.patch("subprocess.run", side_effect=fake.run), \
                mock.patch.object(smb2.time, "sleep"):
            restore = smb2.take_445(port_free=fake.port_free)
            self.assertTrue(callable(restore))
            self.assertEqual(fake.driver_stops(), ["srv", "srv2", "srvnet"])
            self.assertTrue(fake.port_free())
            self.assertEqual(fake.drivers, {"srv": "Stopped", "srv2": "Stopped",
                                            "srvnet": "Stopped"})
            restore()
            restore()
        self.assertEqual(fake.driver_starts(), ["srvnet", "srv2", "srv"])
        self.assertEqual(fake.drivers, {"srv": "Running", "srv2": "Running",
                                        "srvnet": "Running"})
        self.assertEqual(fake.state, "Running")
        self.assertEqual(len(fake.starts()), 1)
        # LanmanServer comes back last: the drivers it needs are already up.
        last_driver = max(i for i, c in enumerate(fake.commands)
                          if c.startswith("Start-Service srv"))
        lanman = fake.commands.index(fake.starts()[0])
        self.assertGreater(lanman, last_driver)
        for cmd in fake.commands:
            if cmd.startswith("Stop-Service -Name srv"):
                self.assertNotIn("-Force", cmd)

    def test_drivers_untouched_when_445_frees_with_the_service(self):
        fake = FakeServices()
        with mock.patch("subprocess.run", side_effect=fake.run):
            restore = smb2.take_445(port_free=lambda: True)
            restore()
        self.assertEqual(fake.driver_stops(), [])
        self.assertEqual(fake.driver_starts(), [])

    def test_refuses_to_pause_a_driver_pinned_by_other_software(self):
        """Docker Desktop's service depends on srv2: name it and bail, leaving
        the user's software alone, with everything already paused restored."""
        fake = FakeServices(pinned=["com.docker.service"])
        with mock.patch("subprocess.run", side_effect=fake.run), \
                mock.patch.object(smb2, "note") as note:
            self.assertIsNone(smb2.take_445(port_free=fake.port_free))
        self.assertEqual(fake.driver_stops(), ["srv"])
        self.assertEqual(fake.driver_starts(), ["srv"])
        self.assertEqual(fake.drivers["srv2"], "Running")
        self.assertEqual(fake.drivers["srvnet"], "Running")
        self.assertEqual(fake.state, "Running")
        messages = " ".join(" ".join(str(a) for a in c.args) for c in note.call_args_list)
        self.assertIn("com.docker.service", messages)

    def test_originally_stopped_driver_is_skipped_and_left_stopped(self):
        fake = FakeServices(drivers={"srv": "Stopped", "srv2": "Running",
                                     "srvnet": "Running"})
        with mock.patch("subprocess.run", side_effect=fake.run):
            restore = smb2.take_445(port_free=fake.port_free)
            self.assertTrue(callable(restore))
            restore()
        self.assertEqual(fake.driver_stops(), ["srv2", "srvnet"])
        self.assertEqual(fake.driver_starts(), ["srvnet", "srv2"])
        self.assertEqual(fake.drivers["srv"], "Stopped")
        self.assertEqual(fake.state, "Running")

    def test_interrupt_while_pausing_drivers_restores_everything(self):
        fake = FakeServices()

        def interrupt_on_srv2(args, **kwargs):
            res = fake.run(args, **kwargs)
            if args[-1].startswith("Stop-Service -Name srv2"):
                raise KeyboardInterrupt
            return res

        with mock.patch("subprocess.run", side_effect=interrupt_on_srv2):
            with self.assertRaises(KeyboardInterrupt):
                smb2.take_445(port_free=fake.port_free)
        self.assertEqual(fake.driver_starts(), ["srv2", "srv"])
        self.assertEqual(fake.drivers, {"srv": "Running", "srv2": "Running",
                                        "srvnet": "Running"})
        self.assertEqual(fake.state, "Running")

    def test_real_probe_is_not_used_by_tests_but_reports_a_free_port(self):
        """The default probe binds the requested address; here an ephemeral
        port on loopback stands in for 445."""
        held = smb2.socket.socket()
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        port = held.getsockname()[1]
        try:
            with mock.patch.object(smb2, "_port_free",
                                   wraps=smb2._port_free) as probe:
                self.assertFalse(smb2._port_free("127.0.0.1", port))
                probe.assert_called_once_with("127.0.0.1", port)
        finally:
            held.close()
        self.assertTrue(smb2._port_free("127.0.0.1", port))


class Port445RetryTests(unittest.TestCase):
    def test_retry_recovers_from_windows_access_denied_and_address_in_use(self):
        """Retry temporary access-denied and address-in-use on port 445."""
        server = mock.Mock()
        server.listen.side_effect = [
            OSError(errno.EACCES, "port still held"),
            OSError(errno.EADDRINUSE, "port still held"),
            445,
        ]
        with mock.patch.object(smb2.time, "sleep") as sleep:
            bound = smb2.listen_after_take_445(server, "0.0.0.0", 445)
        self.assertEqual(bound, 445)
        self.assertEqual(server.listen.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        for call in server.listen.call_args_list:
            self.assertEqual(call.args, ("0.0.0.0", 445))

    def test_permanent_conflict_fails_after_timeout_without_using_other_port(self):
        """Do not silently bind a different port when 445 remains busy."""
        server = mock.Mock()
        server.listen.side_effect = OSError(errno.EACCES, "reserved")
        with mock.patch.object(smb2.time, "sleep") as sleep:
            with self.assertRaises(OSError):
                smb2.listen_after_take_445(server, "0.0.0.0", 445, timeout=0)
        server.listen.assert_called_once_with("0.0.0.0", 445)
        sleep.assert_not_called()

    def test_unexpected_bind_error_is_not_retried(self):
        """Unexpected bind failures should surface without repeated attempts."""
        server = mock.Mock()
        server.listen.side_effect = OSError(errno.EINVAL, "invalid bind")
        with mock.patch.object(smb2.time, "sleep") as sleep:
            with self.assertRaises(OSError):
                smb2.listen_after_take_445(server, "0.0.0.0", 445)
        server.listen.assert_called_once()
        sleep.assert_not_called()

    def test_failed_socket_is_closed_before_a_retry(self):
        """Close each unsuccessful socket before retrying port 445."""
        server = smb2.Smb2Server({}, None)
        sock = mock.Mock()
        sock.bind.side_effect = OSError(errno.EACCES, "denied")
        with mock.patch.object(smb2.socket, "socket", return_value=sock):
            with self.assertRaises(OSError):
                server.listen("0.0.0.0", 445)
        sock.close.assert_called_once()
        self.assertIsNone(server.sock)

    def test_take_445_error_hint_does_not_assume_missing_elevation(self):
        """A persistent 445 conflict does not always mean missing admin rights."""
        with mock.patch.object(smb2.os, "name", "nt"):
            hint = smb2.bind_hint(445, OSError(errno.EACCES, "denied"),
                                  take_445=True)
        self.assertIn("srvnet", hint)
        self.assertIn("excludedportrange", hint)
        self.assertNotIn("Run as Administrator", hint)


class GuiEffectivePortTests(unittest.TestCase):
    def test_take_445_overrides_saved_port_in_all_smb_modes(self):
        """Use effective TCP 445 for SMB status, leaving HTTP unchanged."""
        for key in ("smbv1", "smbv2", "smbv3"):
            with self.subTest(key=key):
                self.assertEqual(gui._status_port(
                    key, {"take_445": True, "port": "1025"}), 445)
                self.assertEqual(gui._status_port(
                    key, {"take_445": False, "port": "1445"}), "1445")
        self.assertEqual(gui._status_port(
            "http", {"take_445": True, "port": 8080}), 8080)


if __name__ == "__main__":
    unittest.main()
