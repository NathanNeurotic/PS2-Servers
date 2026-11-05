"""Startup-banner tests for the SMB2/SMB3 server: what an open share admits.

An --open share on the default bind (every interface) is the most exposed
combination this server offers, so the banner says so in concrete terms:
the addresses it answers on, and the --bind switch that narrows it. A bound
or password-protected server stays quiet about it.

Run:  python -m unittest tests.test_smb2_banner -v
"""

import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(ROOT, "smb2_server", "smb2_server.py")
sys.path.insert(0, os.path.join(ROOT, "smb2_server"))

import smb2_server  # noqa: E402

DEADLINE = 15.0


def _banner(extra_args):
    """Start the server on an ephemeral port and return its startup output.

    A reader thread collects stderr until the banner's end marker appears or
    the deadline passes, whichever comes first; the server is then stopped
    and whatever was written is returned.
    """
    with tempfile.TemporaryDirectory() as tmp:
        argv = [sys.executable, SERVER, "--share", "games=" + tmp,
                "--port", "0"] + extra_args
        proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, text=True)
        lines = []
        done = threading.Event()

        def _read():
            for line in proc.stderr:
                lines.append(line)
                if ("Bound to every interface" in line
                        or "users:" in line):
                    done.set()
                    return
                if "NO PASSWORD" in line:
                    # The warning block, if any, follows immediately; give it
                    # a brief moment, then hand back: the caller stops the
                    # server and the drain below collects whatever is left.
                    time.sleep(0.75)
                    done.set()
                    return
            done.set()

        reader = threading.Thread(target=_read, daemon=True)
        reader.start()
        deadline = time.monotonic() + DEADLINE
        while not done.is_set() and time.monotonic() < deadline:
            time.sleep(0.05)
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        reader.join(timeout=5)
        try:
            rest = proc.stderr.read()
        except ValueError:
            rest = ""
        return "".join(lines) + rest


class BannerTests(unittest.TestCase):
    def test_an_open_share_on_every_interface_warns_and_points_at_bind(self):
        out = _banner(["--open"])
        self.assertIn("NO PASSWORD", out)
        self.assertIn("Bound to every interface", out)
        self.assertIn("--bind", out)

    def test_a_bound_open_share_stays_quiet_about_interfaces(self):
        out = _banner(["--open", "--bind", "127.0.0.1"])
        self.assertIn("NO PASSWORD", out)
        self.assertNotIn("Bound to every interface", out)

    def test_a_password_protected_share_stays_quiet_about_interfaces(self):
        out = _banner(["--user", "ripto:secret"])
        self.assertIn("users: ripto", out)
        self.assertNotIn("Bound to every interface", out)


class WarningLineTests(unittest.TestCase):
    """The lines themselves, with the address listing under control."""

    def test_all_interface_bind_lists_addresses_and_advice(self):
        with unittest.mock.patch.object(smb2_server, "_local_ipv4_addresses",
                                        return_value=["10.0.0.5", "192.168.1.100"]):
            lines = smb2_server._open_share_warning_lines("0.0.0.0")
        self.assertEqual(lines, [
            "  Reachable on: 10.0.0.5, 192.168.1.100",
            "  Bound to every interface; use --bind <address> to serve "
            "only one network, such as the LAN the console is on.",
        ])

    def test_empty_address_list_still_gives_the_advice(self):
        with unittest.mock.patch.object(smb2_server, "_local_ipv4_addresses",
                                        return_value=[]):
            lines = smb2_server._open_share_warning_lines("")
        self.assertEqual(len(lines), 1)
        self.assertIn("--bind", lines[0])

    def test_a_specific_bind_has_no_warning(self):
        with unittest.mock.patch.object(smb2_server, "_local_ipv4_addresses",
                                        return_value=["192.168.1.100"]):
            self.assertEqual(smb2_server._open_share_warning_lines("192.168.1.100"), [])


class LocalAddressListingTests(unittest.TestCase):
    def test_lists_only_real_ipv4_addresses(self):
        for ip in smb2_server._local_ipv4_addresses():
            self.assertNotIn(":", ip)
            self.assertFalse(ip.startswith("127."),
                             "loopback is not a reachable address: " + ip)
            self.assertTrue(re.match(r"^\d{1,3}(\.\d{1,3}){3}$", ip), ip)


if __name__ == "__main__":
    unittest.main()
