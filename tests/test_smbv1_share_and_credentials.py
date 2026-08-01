"""SMBv1 gains a share name and optional credentials -- without moving the default.

The SMBv1 server is the one thing here that has been validated against real
hardware, and OPL and POPSTARTER reach it as guest with a blank password on a
share called games. So the test that matters most in this file is the boring
one: with the defaults, nothing about the command line or the wire changed.

Run:  python -m unittest tests.test_smbv1_share_and_credentials -v
"""

import os
import socket
import struct
import sys
import tempfile
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "smbv1_server"))

import smbserver_opl as smb1          # noqa: E402
from launcher import gui, servers     # noqa: E402

SMBV1 = servers.REGISTRY["smbv1"]


def _values(**overrides):
    v = {"games_folder": "C:/Games", "port": 1111}
    v.update(overrides)
    return v


class TheDefaultDoesNotMove(unittest.TestCase):
    """Every assertion here describes what a console already relies on."""

    def test_the_command_line_is_what_it_was(self):
        argv = SMBV1.build_argv(_values())
        self.assertEqual(argv[:4], ["--share", "games=C:/Games", "--smb-version", "1"])
        self.assertNotIn("--user", argv)

    def test_guest_with_a_blank_password_sends_no_user_flag(self):
        """A --user the console cannot satisfy would lock OPL out of the share."""
        argv = SMBV1.build_argv(_values(username="guest", password=""))
        self.assertNotIn("--user", argv)

    def test_a_username_without_a_password_still_sends_nothing(self):
        # Half-filled fields must not silently turn authentication on.
        argv = SMBV1.build_argv(_values(username="ripto", password=""))
        self.assertNotIn("--user", argv)

    def test_a_blank_share_name_falls_back_to_games(self):
        for blank in ("", "   ", None):
            with self.subTest(blank=blank):
                argv = SMBV1.build_argv(_values(share_name=blank))
                self.assertIn("games=C:/Games", argv)

    def test_a_server_with_no_users_accepts_anyone(self):
        server = smb1.SmbServer({}, read_only=False)
        self.assertEqual(server.users, {})


class TheNewOptions(unittest.TestCase):
    def test_a_custom_share_name_reaches_the_command_line(self):
        argv = SMBV1.build_argv(_values(share_name="roms"))
        self.assertIn("roms=C:/Games", argv)
        self.assertNotIn("games=C:/Games", argv)

    def test_credentials_reach_the_command_line(self):
        argv = SMBV1.build_argv(_values(username="ripto", password="hunter2"))
        self.assertIn("--user", argv)
        self.assertEqual(argv[argv.index("--user") + 1], "ripto:hunter2")

    def test_a_password_containing_a_colon_survives(self):
        argv = SMBV1.build_argv(_values(username="ripto", password="a:b:c"))
        self.assertEqual(argv[argv.index("--user") + 1], "ripto:a:b:c")

    def test_the_fields_exist_and_default_as_documented(self):
        by_key = {f.key: f for f in SMBV1.fields}
        self.assertEqual(by_key["share_name"].default, "games")
        self.assertEqual(by_key["username"].default, "guest")
        self.assertEqual(by_key["password"].default, "")


class TheOplHintFollowsTheSettings(unittest.TestCase):
    """A hint that names the defaults next to a differently-configured server
    sends the user to check their network when the name was the problem."""

    def test_it_reads_back_the_defaults(self):
        hint = gui.opl_hint("smbv1", "192.168.1.5", _values())
        self.assertIn("Share 'games'", hint)
        self.assertIn("User 'guest'", hint)
        self.assertIn("Password blank", hint)

    def test_it_reads_back_a_custom_share_and_user(self):
        hint = gui.opl_hint("smbv1", "192.168.1.5",
                            _values(share_name="roms", username="ripto",
                                    password="hunter2"))
        self.assertIn("Share 'roms'", hint)
        self.assertIn("User 'ripto'", hint)
        self.assertNotIn("Password blank", hint)

    def test_it_never_prints_the_password(self):
        hint = gui.opl_hint("smbv1", "192.168.1.5",
                            _values(username="ripto", password="hunter2"))
        self.assertNotIn("hunter2", hint)


# --- over a real socket ---------------------------------------------------- #

def _session_setup(account, password):
    """A SESSION_SETUP_ANDX carrying a plaintext password, as OPL's would be."""
    ansi = (password.encode("ascii") + b"\x00") if password else b"\x00"
    data = ansi + account.encode("ascii") + b"\x00" + b"WORKGROUP\x00OS\x00LM\x00"
    params = struct.pack("<BBHHHHIHHII",
                         0xFF, 0, 0,        # AndX none
                         8192, 1, 0, 0,     # MaxBuffer, MaxMpx, Vc, SessionKey
                         len(ansi), 0,      # ANSI / Unicode password lengths
                         0, 0)              # Reserved, Capabilities
    return params, data


class Fake:
    """The smallest object h_session_setup reads."""

    def __init__(self, params, data):
        self.wordcount = len(params) // 2
        self.params = params
        self.data = data


class Conn:
    def __init__(self, server):
        self.server = server
        self.uid = 0


class CredentialsAreActuallyChecked(unittest.TestCase):
    """A field the server never reads is a control that does nothing."""

    def _setup(self, users, account, password):
        server = smb1.SmbServer({}, read_only=False, users=users)
        params, data = _session_setup(account, password)
        return smb1.h_session_setup(Conn(server), Fake(params, data))

    def test_the_right_password_is_accepted(self):
        result = self._setup({"ripto": "hunter2"}, "ripto", "hunter2")
        self.assertEqual(result[2], smb1.STATUS_SUCCESS)

    def test_the_wrong_password_is_refused(self):
        result = self._setup({"ripto": "hunter2"}, "ripto", "wrong")
        self.assertEqual(result[2], smb1.STATUS_LOGON_FAILURE)

    def test_an_unknown_account_is_refused(self):
        result = self._setup({"ripto": "hunter2"}, "nobody", "hunter2")
        self.assertEqual(result[2], smb1.STATUS_LOGON_FAILURE)

    def test_a_blank_password_does_not_pass_a_configured_share(self):
        """The guest logon must stop working once a password is set, or the
        option protects nothing."""
        result = self._setup({"ripto": "hunter2"}, "guest", "")
        self.assertEqual(result[2], smb1.STATUS_LOGON_FAILURE)

    def test_account_names_are_case_insensitive(self):
        result = self._setup({"Ripto": "hunter2"}, "RIPTO", "hunter2")
        self.assertEqual(result[2], smb1.STATUS_SUCCESS)

    def test_with_no_users_the_guest_logon_is_still_accepted(self):
        result = self._setup({}, "guest", "")
        self.assertEqual(result[2], smb1.STATUS_SUCCESS)
        self.assertEqual(struct.unpack_from("<H", result[0], 4)[0], 0x0001,
                         "guest logons must still report Action=guest")

    def test_an_authenticated_account_is_not_reported_as_guest(self):
        result = self._setup({"ripto": "hunter2"}, "ripto", "hunter2")
        self.assertEqual(struct.unpack_from("<H", result[0], 4)[0], 0x0000)


class TheShareNameIsHonoured(unittest.TestCase):
    def test_a_named_share_is_found(self):
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        server = smb1.SmbServer({"roms": smb1.Share("roms", d)}, read_only=False)
        self.assertIn("roms", server.shares)


if __name__ == "__main__":
    unittest.main()
