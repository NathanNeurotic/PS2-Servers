"""DREAD skips an entry it cannot stat instead of ending the listing.

Answering end-of-dir for one unreadable entry cut every directory listing
short at that entry, and the console cannot tell a short listing from a
complete one: OPL (RiptOPL #1070) took every later ART/ cover to be missing,
and games after that entry never reached the list.

Run:  python -m unittest tests.test_udpfs_dread_skip_entry -v
"""

import os
import struct
import sys
import threading
import types
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UDPFS = os.path.join(_ROOT, "udpfs_server")
if _UDPFS not in sys.path:
    sys.path.insert(0, _UDPFS)

import udpfs_server as srv  # noqa: E402

ADDR = ("192.168.0.245", 62966)
HANDLE = 7


class _Entry:
    def __init__(self, name, fail=False):
        self.name = name
        self.path = name
        self._fail = fail

    def stat(self, follow_symlinks=True):
        if self._fail:
            raise PermissionError(13, "Access is denied", self.name)
        return os.stat_result((0o100644, 0, 0, 1, 0, 0, 1234, 0, 0, 0))


class DreadSkipEntryTests(unittest.TestCase):
    def _server(self, entries):
        server = object.__new__(srv.UdpfsServer)
        server.stats = {'dread': 0}
        server.verbose = False
        server.enable_compression = False
        server.events = []
        server._print_event = server.events.append
        server.replies = []
        server._send_dread_reply = (
            lambda addr, result, name=None, stat_info=None:
            server.replies.append((result, name)))
        # handles is a per-client property that reads the worker thread's session.
        server._local = threading.local()
        server._local.session = types.SimpleNamespace(handles={HANDLE: types.SimpleNamespace(
            obj={'entries': entries, 'index': 0}, is_dir=True)})
        return server

    def _list(self, server):
        payload = struct.pack('<BBBBi', 0, 0, 0, 0, HANDLE)
        for _ in range(10):
            server._handle_dread(ADDR, payload)
            if server.replies[-1][0] == 0:
                break
        return server.replies

    def test_unreadable_entry_is_skipped_not_end_of_dir(self):
        server = self._server([_Entry("A_COV.png"), _Entry("B_COV.png", fail=True),
                               _Entry("C_COV.png")])
        self.assertEqual(self._list(server),
                         [(1, "A_COV.png"), (1, "C_COV.png"), (0, None)])
        self.assertTrue(any("B_COV.png" in e for e in server.events))

    def test_unreadable_last_entries_end_the_listing(self):
        server = self._server([_Entry("A"), _Entry("B", fail=True), _Entry("C", fail=True)])
        self.assertEqual(self._list(server), [(1, "A"), (0, None)])

    def test_complete_listing_unchanged(self):
        server = self._server([_Entry("A"), _Entry("B")])
        self.assertEqual(self._list(server), [(1, "A"), (1, "B"), (0, None)])


if __name__ == "__main__":
    unittest.main()
