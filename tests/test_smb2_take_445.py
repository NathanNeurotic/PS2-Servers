"""Regression tests for Windows TCP 445 takeover in SMBv2/SMBv3.

Service-control calls are mocked: these tests must not stop real Windows file
sharing or bind the real system's TCP 445.
"""

import errno
import os
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


class FakeServices:
    def __init__(self, initial="Running", delayed_polls=0, stop_failed=False):
        """Initialize a synthetic Windows service and its stop behavior."""
        self.state = initial
        self.delayed_polls = delayed_polls
        self.stop_failed = stop_failed
        self.commands = []
        self.stop_issued = False

    def run(self, args, **_kwargs):
        """Simulate PowerShell Get-Service, Stop-Service, and Start-Service."""
        cmd = args[-1]
        self.commands.append(cmd)
        if cmd.startswith("(Get-Service LanmanServer"):
            if self.stop_issued and self.state != "Stopped":
                if self.delayed_polls > 0:
                    self.delayed_polls -= 1
                    return subprocess.CompletedProcess(args, 0, "StopPending\n", "")
                self.state = "Stopped"
            return subprocess.CompletedProcess(args, 0, self.state + "\n", "")
        if cmd.startswith("(Get-Service Browser"):
            return subprocess.CompletedProcess(args, 0, "Stopped\n", "")
        if cmd.startswith("Stop-Service"):
            if self.stop_failed:
                return subprocess.CompletedProcess(args, 1, "", "Access denied")
            self.stop_issued = True
            return subprocess.CompletedProcess(args, 0, "", "")
        if cmd.startswith("Start-Service LanmanServer"):
            if self.stop_issued and self.delayed_polls:
                return subprocess.CompletedProcess(
                    args, 1, "", "Cannot start while StopPending")
            self.stop_issued = False  # The stop transition is over.
            self.state = "Running"
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    def starts(self):
        """Return attempted service restart commands."""
        return [c for c in self.commands if c.startswith("Start-Service LanmanServer")]

    def stops(self):
        """Return attempted service stop commands."""
        return [c for c in self.commands if c.startswith("Stop-Service")]


class WindowsTake445Tests(unittest.TestCase):
    def test_waits_for_stopped_service_and_restores_only_once(self):
        """Only restore a service after its stop completes, and restore once."""
        fake = FakeServices(delayed_polls=2)
        with mock.patch("subprocess.run", side_effect=fake.run), \
                mock.patch.object(smb2.time, "sleep") as sleep:
            restore = smb2.take_445()
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
            self.assertIsNone(smb2.take_445(stop_timeout=0, restore_timeout=1))
        self.assertEqual(len(fake.starts()), 1)
        self.assertEqual(fake.state, "Running")

    def test_persistent_stop_pending_is_not_started(self):
        """Refuse unsafe recovery if Windows never completes the pending stop."""
        fake = FakeServices(delayed_polls=100)
        with mock.patch("subprocess.run", side_effect=fake.run):
            self.assertIsNone(smb2.take_445(stop_timeout=0, restore_timeout=0))
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
        self.assertIn("srvnet.sys", hint)
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
