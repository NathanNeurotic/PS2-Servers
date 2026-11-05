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
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(ROOT, "smb2_server", "smb2_server.py")
sys.path.insert(0, os.path.join(ROOT, "smb2_server"))

import smb2_server  # noqa: E402


def _banner(extra_args):
    """Start the server on an ephemeral port and return its startup lines."""
    import time
    with tempfile.TemporaryDirectory() as tmp:
        argv = [sys.executable, SERVER, "--share", "games=" + tmp,
                "--port", "0"] + extra_args
        proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, text=True)
        try:
            # The banner prints at startup; give it a moment, stop the
            # server, then drain what it wrote (read() ends at EOF).
            time.sleep(1.0)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        return proc.stderr.read()


class BannerTests(unittest.TestCase):
    def test_an_open_share_on_every_interface_names_where_it_is_reachable(self):
        out = _banner(["--open"])
        self.assertIn("NO PASSWORD", out)
        self.assertIn("Reachable on:", out)
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


class LocalAddressListingTests(unittest.TestCase):
    def test_lists_only_real_ipv4_addresses(self):
        for ip in smb2_server._local_ipv4_addresses():
            self.assertNotIn(":", ip)
            self.assertFalse(ip.startswith("127."),
                             "loopback is not a reachable address: " + ip)
            self.assertTrue(re.match(r"^\d{1,3}(\.\d{1,3}){3}$", ip), ip)


if __name__ == "__main__":
    unittest.main()
