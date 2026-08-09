"""A peer cannot allocate an unbounded number of sessions.

A session is a worker thread, a queue and up to MAX_HANDLES descriptors, and
any unauthenticated datagram creates one -- the server has no authentication by
design, so "who may create a session" is "anything that can reach the port".
Sessions are keyed on (ip, port), so one host walking its source port could
create them without limit and hold each for --peer-timeout, an hour by default.
The code already knew half of this: the comment on SESSION_TIMEOUT says a
stranded session holds "a thread and its handles until the server exits".

Eviction rather than refusal is deliberate. A console whose source port shifts
has to be able to get back in -- the UDPBD server has explicit handling for
exactly that -- so the cap drops the least recently active peer instead of
turning the new one away.

Run:  python -m unittest tests.test_session_cap -v
"""

import os
import sys
import threading
import time
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_UDPFS = os.path.join(_ROOT, "udpfs_server")
if _UDPFS not in sys.path:
    sys.path.insert(0, _UDPFS)

import udpfs_server as srv  # noqa: E402


class _Server(srv.UdpfsServer):
    """Session bookkeeping only -- no sockets, no worker threads."""

    def __init__(self, max_sessions):
        self.sessions = {}
        self.sessions_lock = threading.Lock()
        self.max_sessions = srv._clamp_max_sessions(max_sessions)
        self._sessions_evicted = 0
        self._last_evict_log = 0.0
        self.events = []
        self.started = []
        self.stopped = []
        self.bd_fh = None

    def _print_event(self, msg):
        self.events.append(msg)


class _FakeSession:
    def __init__(self, server, addr):
        self.server = server
        self.addr = addr
        self.last_activity = time.monotonic()
        server.started.append(addr)

    def start(self):
        pass

    def shutdown(self):
        self.server.stopped.append(self.addr)


def _peer(n):
    return ("192.0.2.%d" % (n % 250 + 1), 1024 + n)


class TheSessionTableIsBounded(unittest.TestCase):
    def setUp(self):
        self._real_session = srv.Session
        srv.Session = _FakeSession
        self.addCleanup(lambda: setattr(srv, "Session", self._real_session))

    def test_a_flood_cannot_grow_the_table_past_the_cap(self):
        server = _Server(max_sessions=8)
        for i in range(500):
            server._get_or_create_session(_peer(i))
        self.assertEqual(
            len(server.sessions), 8,
            "500 distinct source ports produced %d sessions; the cap did not "
            "hold" % len(server.sessions))
        # Everything created beyond the cap must have been shut down, not
        # merely dropped from the dict -- a forgotten session keeps its thread
        # and its file handles.
        self.assertEqual(len(server.stopped), 500 - 8)

    def test_the_least_recently_active_peer_is_the_one_dropped(self):
        server = _Server(max_sessions=3)
        a, b, c = _peer(1), _peer(2), _peer(3)
        for addr in (a, b, c):
            server._get_or_create_session(addr)

        # Timestamps set outright rather than by sleeping between calls.
        # time.monotonic() has ~15 ms resolution on Windows, so a sleep short
        # enough to keep the test fast leaves every session with the SAME
        # last_activity -- min() then returns whichever came first in
        # iteration order and the test passes or fails on dict ordering.
        #
        # `a` is the oldest by creation but is still talking; `b` has gone
        # quiet. The peer mid-transfer must survive.
        server.sessions[a].last_activity = 300.0
        server.sessions[b].last_activity = 100.0   # least recently active
        server.sessions[c].last_activity = 200.0

        server._get_or_create_session(_peer(4))
        self.assertEqual(server.stopped, [b],
                         "eviction dropped %s; it must drop the least recently "
                         "ACTIVE peer, not the oldest one" % (server.stopped,))
        self.assertIn(a, server.sessions, "an active peer was evicted")

    def test_an_existing_peer_never_triggers_an_eviction(self):
        server = _Server(max_sessions=2)
        a, b = _peer(1), _peer(2)
        server._get_or_create_session(a)
        server._get_or_create_session(b)
        for _ in range(50):
            server._get_or_create_session(a)
            server._get_or_create_session(b)
        self.assertEqual(server.stopped, [],
                         "traffic from known peers caused an eviction")
        self.assertEqual(len(server.sessions), 2)

    def test_the_eviction_is_reported_but_not_once_per_packet(self):
        server = _Server(max_sessions=2)
        for i in range(200):
            server._get_or_create_session(_peer(i))
        notices = [e for e in server.events if "session limit" in e]
        self.assertTrue(notices,
                        "sessions were evicted with nothing said; a console "
                        "that stops working needs a reason in the log")
        self.assertEqual(
            len(notices), 1,
            "the eviction notice is rate-limited to one a minute, so a flood "
            "must not produce %d lines" % len(notices))
        self.assertIn("--max-sessions", notices[0],
                      "the notice should name the flag that changes it")

    def test_the_cap_is_configurable_and_floored(self):
        self.assertEqual(srv._clamp_max_sessions(0), srv.MIN_MAX_SESSIONS,
                         "a cap of zero would refuse the first console")
        self.assertEqual(srv._clamp_max_sessions(-10), srv.MIN_MAX_SESSIONS)
        self.assertEqual(srv._clamp_max_sessions("nonsense"),
                         srv.DEFAULT_MAX_SESSIONS)
        self.assertEqual(srv._clamp_max_sessions(4), 4)

    def test_the_default_is_far_above_any_real_deployment(self):
        # The population is consoles on a LAN. If this ever needs raising, the
        # flag exists; the number here should stay boring.
        self.assertGreaterEqual(srv.DEFAULT_MAX_SESSIONS, 64)


class TheCapDoesNotChangeOrdinaryBehaviour(unittest.TestCase):
    """The cap must be invisible to a normal number of consoles."""

    def setUp(self):
        self._real_session = srv.Session
        srv.Session = _FakeSession
        self.addCleanup(lambda: setattr(srv, "Session", self._real_session))

    def test_eight_consoles_all_keep_their_sessions(self):
        server = _Server(max_sessions=srv.DEFAULT_MAX_SESSIONS)
        peers = [_peer(i) for i in range(8)]
        for addr in peers:
            server._get_or_create_session(addr)
        for _ in range(20):
            for addr in peers:
                server._get_or_create_session(addr)
        self.assertEqual(len(server.sessions), 8)
        self.assertEqual(server.stopped, [])
        self.assertEqual(server.events, [])


if __name__ == "__main__":
    unittest.main()
