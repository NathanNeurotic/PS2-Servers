"""Unit tests for the UDPFS configuration surface.

These cover the udpfsd-compatibility edges: a command line or compose file
written against udpfsd's README must land here without crashing. Each of the
duration/bind cases below was an unhandled traceback before.

Run:  python -m unittest tests.test_udpfs_config -v
"""

import os
import socket
import sys
import unittest

# udpfs_server/ is not a package; the server module and its compressed_iso
# package both resolve once that directory is on the path.
_UDPFS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'udpfs_server')
if _UDPFS_DIR not in sys.path:
    sys.path.insert(0, _UDPFS_DIR)

import udpfs_server as udpfs  # noqa: E402

from launcher import servers  # noqa: E402


class DurationTests(unittest.TestCase):
    """udpfsd takes Go duration strings for --peer-timeout/--metrics-period."""

    def test_plain_number_is_seconds(self):
        # The historical form: must keep working exactly as it did.
        self.assertEqual(udpfs.parse_duration('90'), 90.0)
        self.assertEqual(udpfs.parse_duration('0'), 0.0)
        self.assertEqual(udpfs.parse_duration('1.5'), 1.5)
        self.assertEqual(udpfs.parse_duration(90), 90.0)

    def test_go_duration_strings(self):
        self.assertEqual(udpfs.parse_duration('90s'), 90.0)
        self.assertEqual(udpfs.parse_duration('30m'), 1800.0)
        self.assertEqual(udpfs.parse_duration('1h'), 3600.0)
        self.assertEqual(udpfs.parse_duration('1h30m'), 5400.0)
        self.assertEqual(udpfs.parse_duration('500ms'), 0.5)
        self.assertEqual(udpfs.parse_duration(' 2M '), 120.0)  # case/space tolerant

    def test_ms_is_not_read_as_minutes(self):
        # Alternation order matters: 'ms' must win over 'm'.
        self.assertEqual(udpfs.parse_duration('100ms'), 0.1)
        self.assertNotEqual(udpfs.parse_duration('100ms'), 6000.0)

    def test_garbage_is_none_not_an_exception(self):
        for bad in ('abc', '1x', '1h junk', 'h', '', None, '  '):
            self.assertIsNone(udpfs.parse_duration(bad), repr(bad))

    def test_env_duration_falls_back_and_never_raises(self):
        # udpfsd ignores an unparseable duration and uses its default; dying
        # with a traceback would be strictly worse for the container users who
        # are the only people setting these.
        os.environ['_T_DUR'] = 'bogus'
        try:
            self.assertEqual(udpfs._env_duration('_T_DUR', 3600.0), 3600.0)
            os.environ['_T_DUR'] = '1h'
            self.assertEqual(udpfs._env_duration('_T_DUR', 3600.0), 3600.0)
            os.environ['_T_DUR'] = '45m'
            self.assertEqual(udpfs._env_duration('_T_DUR', 3600.0), 2700.0)
            os.environ['_T_DUR'] = ''
            self.assertEqual(udpfs._env_duration('_T_DUR', 12.0), 12.0)
        finally:
            os.environ.pop('_T_DUR', None)

    def test_unset_env_returns_default(self):
        os.environ.pop('_T_MISSING', None)
        self.assertEqual(udpfs._env_duration('_T_MISSING', 7.0), 7.0)


class BindTests(unittest.TestCase):
    """udpfsd's -bind is documented as 'Address and port for data connection'."""

    def test_bare_ip_keeps_its_old_meaning(self):
        self.assertEqual(udpfs.split_bind('192.168.1.5'), ('192.168.1.5', None))
        self.assertEqual(udpfs.split_bind(''), ('', None))
        self.assertEqual(udpfs.split_bind(None), ('', None))

    def test_host_port_forms(self):
        self.assertEqual(udpfs.split_bind('0.0.0.0:62966'), ('0.0.0.0', 62966))
        self.assertEqual(udpfs.split_bind(':41233'), ('', 41233))
        self.assertEqual(udpfs.split_bind('192.168.1.5:0'), ('192.168.1.5', 0))

    def test_hex_port_accepted(self):
        self.assertEqual(udpfs.split_bind(':0xF5F7'), ('', 0xF5F7))

    def test_malformed_port_raises_valueerror_not_gaierror(self):
        # These used to reach getaddrinfo intact and die with socket.gaierror.
        for bad in ('0.0.0.0:abc', '0.0.0.0:99999', ':-1'):
            with self.assertRaises(ValueError):
                udpfs.split_bind(bad)


class ExtensionAliasTests(unittest.TestCase):
    """'.ciso'/'.ziso' occur in the wild for the same containers."""

    def test_aliases_map_to_the_same_codec(self):
        self.assertEqual(udpfs._format_for_extension('game.cso'), 'cso')
        self.assertEqual(udpfs._format_for_extension('game.ciso'), 'cso')
        self.assertEqual(udpfs._format_for_extension('game.zso'), 'zso')
        self.assertEqual(udpfs._format_for_extension('game.ziso'), 'zso')
        self.assertEqual(udpfs._format_for_extension('game.chd'), 'chd')

    def test_extension_match_is_case_insensitive(self):
        self.assertEqual(udpfs._format_for_extension('GAME.CISO'), 'cso')
        self.assertEqual(udpfs._format_for_extension('Game.Chd'), 'chd')

    def test_plain_iso_is_not_compressed(self):
        self.assertIsNone(udpfs._format_for_extension('game.iso'))
        self.assertIsNone(udpfs._format_for_extension('game'))

    def test_alias_listed_whenever_its_base_format_is(self):
        # A format is only advertised when its library is present; the alias
        # must never be advertised separately from the extension it aliases.
        exts = udpfs.COMPRESSED_EXTENSIONS
        self.assertEqual('.cso' in exts, '.ciso' in exts)
        self.assertEqual('.zso' in exts, '.ziso' in exts)
        self.assertIn('.cso', exts)  # zlib is stdlib: always supported


class RecvBufferTests(unittest.TestCase):
    def test_widen_is_best_effort_and_never_shrinks(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            udpfs._widen_recv_buffer(s, udpfs.DATA_RECV_BUFFER_BYTES)
            got = s.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
            self.assertGreater(got, 0)
            # Asking for less must not shrink what we already have.
            before = got
            udpfs._widen_recv_buffer(s, 1024)
            self.assertGreaterEqual(
                s.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF), before)
        finally:
            s.close()

    def test_widen_tolerates_a_broken_socket(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.close()
        udpfs._widen_recv_buffer(s, 1 << 20)  # must not raise


class LauncherCompressionArgTests(unittest.TestCase):
    """The server decompresses by default, so UNTICKING is what must be sent."""

    def test_unticked_sends_no_compression(self):
        args = servers._udpfs_argv({'root_dir': '/games', 'enable_compression': False})
        self.assertIn('--no-compression', args)
        self.assertNotIn('--enable-compression', args)

    def test_ticked_sends_nothing_because_it_is_the_default(self):
        args = servers._udpfs_argv({'root_dir': '/games', 'enable_compression': True})
        self.assertNotIn('--no-compression', args)

    def test_only_long_flags_are_emitted(self):
        # The packaged app re-executes itself; Nuitka's self-exec guard aborts
        # on a bare '-c'/'-m' followed by another argument.
        args = servers._udpfs_argv({
            'root_dir': '/games', 'enable_compression': False,
            'read_only': True, 'verbose': True, 'modulo_mode': True,
        })
        for a in args:
            if a.startswith('-') and not a.startswith('--'):
                self.fail(f"short flag {a!r} would trip the Nuitka self-exec guard")


if __name__ == '__main__':
    unittest.main()
