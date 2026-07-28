"""The router status protocol must mean the same thing in every edition.

Two servers implement this -- Edge in Go and the Desktop server in Python --
and outside projects are invited to implement it too, so the wire format is
tested as a contract rather than as whatever each side happens to produce. The
cross-implementation test here compares Python's bytes against the Go
encoder's, byte for byte.

The compatibility tests matter as much as the format ones. This feature is
built entirely on the fact that every server shipped so far drops a discovery
packet whose service ID is not UDPFS. If that ever stopped being true, a status
query would start disturbing consoles, so it is pinned here.
"""

import os
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest

# Matches ps2servers_core.DEFAULT_FALLBACK_SECONDS; imported lazily below would
# drag the server in at module scope, which the wire-format tests do not need.
DEFAULT_FALLBACK_SECONDS = 0.25

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UDPFS_DIR = os.path.join(_ROOT, "udpfs_server")
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
if _UDPFS_DIR not in sys.path:
    sys.path.insert(0, _UDPFS_DIR)

import router_status  # noqa: E402


class WireFormat(unittest.TestCase):
    def test_query_is_discovery_shaped_and_carries_the_magic(self):
        # The shape is what makes an older server discard it on the guard it
        # already has. A query that did not look like a DISCOVERY would fall
        # through to some other path and might get an answer.
        q = router_status.build_status_query()
        self.assertEqual(len(q), router_status.QUERY_LEN)
        (header,) = struct.unpack_from("<H", q, 0)
        self.assertEqual(header & 0xF, 0, "packet type must be DISCOVERY")
        self.assertEqual(header >> 4, 0, "sequence must be 0")
        (service,) = struct.unpack_from("<H", q, 2)
        self.assertEqual(service, 0xF5F7)
        self.assertEqual(q[6:10], router_status.MAGIC)

    def test_query_without_the_magic_is_not_ours(self):
        # The whole point of the tag: 0xF5F7 sits in upstream's numbering
        # range, so the number alone must not be enough to make this server
        # answer. A future udpfsd client using that ID for something else must
        # get silence from us.
        bare = struct.pack("<HHH", 0, 0xF5F7, 0)
        self.assertFalse(router_status.is_status_query(bare))
        wrong = bare + b"XXXX"
        self.assertFalse(router_status.is_status_query(wrong))
        self.assertTrue(router_status.is_status_query(router_status.build_status_query()))

    def test_reply_round_trips(self):
        packet = router_status.build_status_reply(
            router_status.STATE_BUSY,
            router_status.FLAG_UDPFS | router_status.FLAG_READ_ONLY,
            3, 1234, "PS2 Servers 0.4.9")
        got = router_status.parse_status_reply(packet)
        self.assertIsNotNone(got)
        self.assertEqual(got["state"], router_status.STATE_BUSY)
        self.assertEqual(got["state_name"], "busy")
        self.assertEqual(got["sessions"], 3)
        self.assertEqual(got["uptime"], 1234)
        self.assertEqual(got["name"], "PS2 Servers 0.4.9")
        self.assertTrue(got["flags"] & router_status.FLAG_READ_ONLY)

    def test_fixed_offsets_are_where_the_spec_says(self):
        # The first ten bytes are frozen across payload versions; a client keys
        # its "is this mine, do I understand it" check on them. If this test
        # has to change, the spec changes and every implementation with it.
        packet = router_status.build_status_reply(router_status.STATE_READY, 0, 0, 0, "")
        self.assertEqual(struct.unpack_from("<H", packet, 2)[0], 0xF5F7)
        self.assertEqual(packet[4:8], router_status.MAGIC)
        self.assertEqual(packet[8], router_status.VERSION)
        self.assertEqual(len(packet), router_status.FIXED_LEN)

    def test_reply_without_the_magic_is_rejected(self):
        packet = bytearray(router_status.build_status_reply(
            router_status.STATE_READY, 0, 0, 0, "x"))
        packet[4:8] = b"NOPE"
        self.assertIsNone(router_status.parse_status_reply(bytes(packet)))

    def test_unknown_version_is_rejected_not_guessed(self):
        packet = bytearray(router_status.build_status_reply(
            router_status.STATE_READY, 0, 0, 0, "x"))
        packet[8] = 99
        self.assertIsNone(router_status.parse_status_reply(bytes(packet)))

    def test_unknown_state_degrades(self):
        # A client taught only the five states must not read a future value as
        # something benign.
        packet = bytearray(router_status.build_status_reply(
            router_status.STATE_READY, 0, 0, 0, ""))
        packet[9] = 200
        got = router_status.parse_status_reply(bytes(packet))
        self.assertEqual(got["state"], router_status.STATE_DEGRADED)

    def test_foreign_and_truncated_replies_are_rejected(self):
        good = router_status.build_status_reply(router_status.STATE_READY, 0, 0, 0, "hi")
        # Wrong service ID: the numbering range is not ours, so a reply from
        # something else must not be read as a healthy server.
        foreign = bytearray(good)
        struct.pack_into("<H", foreign, 2, 0xF5F5)
        self.assertIsNone(router_status.parse_status_reply(bytes(foreign)))
        for cut in range(len(good)):
            self.assertIsNone(router_status.parse_status_reply(good[:cut]),
                              f"accepted a reply truncated to {cut} bytes")
        # A name length that runs past the packet.
        lying = bytearray(good)
        lying[18] = 200
        self.assertIsNone(router_status.parse_status_reply(bytes(lying)))

    def test_long_name_is_truncated_not_rejected(self):
        got = router_status.parse_status_reply(
            router_status.build_status_reply(router_status.STATE_READY, 0, 0, 0, "n" * 400))
        self.assertIsNotNone(got, "an over-long display name must not fail a health check")
        self.assertEqual(len(got["name"]), 255)

    def test_is_status_query_rejects_ordinary_discovery(self):
        # The packet a console sends must never be mistaken for a status query.
        console = struct.pack("<HHH", 0, 0xF5F5, 0)
        self.assertFalse(router_status.is_status_query(console))
        self.assertTrue(router_status.is_status_query(router_status.build_status_query()))


class CrossImplementation(unittest.TestCase):
    """Python and Go must produce identical bytes."""

    def test_go_and_python_encoders_agree(self):
        go = shutil_which("go")
        if not go:
            self.skipTest("no Go toolchain on PATH")
        edge = os.path.join(_ROOT, "native", "ps2servers-edge")
        if not os.path.isdir(edge):
            self.skipTest("Edge source not present")

        program = '''
package main

import (
	"fmt"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/protocol"
)

func main() {
	r := protocol.StatusReply{
		State:    protocol.StateBusy,
		Flags:    protocol.FlagServesUDPFS | protocol.FlagReadOnly,
		Sessions: 3,
		Uptime:   1234,
		Name:     "PS2 Servers 0.4.9",
	}
	fmt.Printf("%x\\n", r.Marshal())
	fmt.Printf("%x\\n", protocol.MarshalStatusQuery())
}
'''
        # The probe must live INSIDE the Edge module. internal/ packages are
        # importable only from within the module that declares them, so a
        # program in the system temp directory cannot reach protocol.
        with tempfile.TemporaryDirectory(dir=edge) as tmp:
            src = os.path.join(tmp, "main.go")
            with open(src, "w", encoding="utf-8") as fh:
                fh.write(program)
            try:
                out = subprocess.run(
                    [go, "run", src], cwd=edge, capture_output=True, text=True, timeout=180)
            except subprocess.TimeoutExpired:
                self.skipTest("go run timed out")
            if out.returncode != 0:
                self.fail(f"go run failed, so the two encoders were never compared: {out.stderr[:600]}")

        lines = [l.strip() for l in out.stdout.splitlines() if l.strip()]
        self.assertEqual(len(lines), 2, f"unexpected go output: {out.stdout!r}")
        go_reply, go_query = bytes.fromhex(lines[0]), bytes.fromhex(lines[1])

        py_reply = router_status.build_status_reply(
            router_status.STATE_BUSY,
            router_status.FLAG_UDPFS | router_status.FLAG_READ_ONLY,
            3, 1234, "PS2 Servers 0.4.9")
        py_query = router_status.build_status_query()

        self.assertEqual(py_reply.hex(), go_reply.hex(), "reply encodings differ")
        self.assertEqual(py_query.hex(), go_query.hex(), "query encodings differ")

        # And Python must decode what Go produced.
        decoded = router_status.parse_status_reply(go_reply)
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded["state"], router_status.STATE_BUSY)
        self.assertEqual(decoded["name"], "PS2 Servers 0.4.9")


def shutil_which(name):
    import shutil
    return shutil.which(name)


class LiveServer(unittest.TestCase):
    """The Desktop server must answer through its real discovery handler.

    _handle_discovery is driven directly rather than through run(), which loops
    forever with no stop flag. The handler is the integration point under test
    -- it is where the status hook sits, ahead of the service-ID guard -- and
    it does its own real socket send, so nothing here is stubbed.
    """

    def _serve(self, read_only=False):
        import ps2servers_core

        root = tempfile.mkdtemp()
        self.addCleanup(lambda: os.rmdir(root) if os.path.isdir(root) else None)
        server = ps2servers_core.AutoUdpfsServer(
            root_dir=root, port=0, bind_ip="127.0.0.1", read_only=read_only)
        self.addCleanup(server.sock.close)
        if server.dsock is not server.sock:
            self.addCleanup(server.dsock.close)
        return server

    def _client(self, timeout):
        client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        client.bind(("127.0.0.1", 0))
        client.settimeout(timeout)
        self.addCleanup(client.close)
        return client

    def test_answers_status_and_creates_no_session(self):
        server = self._serve()
        client = self._client(2.0)

        for i in range(3):
            server._handle_discovery(
                router_status.build_status_query(), client.getsockname())
            data, _ = client.recvfrom(2048)
            got = router_status.parse_status_reply(data)
            self.assertIsNotNone(got, "server did not send a valid status reply")
            self.assertTrue(got["flags"] & router_status.FLAG_UDPFS)
            self.assertEqual(got["state"], router_status.STATE_READY)
            self.assertEqual(
                len(server.sessions), 0,
                f"after {i + 1} status polls the server has sessions; polling is creating them")
            self.assertEqual(got["sessions"], 0)

    def test_read_only_is_reported(self):
        server = self._serve(read_only=True)
        client = self._client(2.0)
        server._handle_discovery(router_status.build_status_query(), client.getsockname())
        got = router_status.parse_status_reply(client.recvfrom(2048)[0])
        self.assertIsNotNone(got)
        self.assertTrue(got["flags"] & router_status.FLAG_READ_ONLY)

    def test_degrades_when_the_share_disappears(self):
        server = self._serve()
        client = self._client(2.0)
        os.rmdir(server.root_dir)

        server._handle_discovery(router_status.build_status_query(), client.getsockname())
        got = router_status.parse_status_reply(client.recvfrom(2048)[0])
        self.assertIsNotNone(got)
        self.assertEqual(got["state"], router_status.STATE_DEGRADED,
                         "a vanished share must not be reported as ready")

    def test_unknown_service_id_is_still_ignored(self):
        # The rule the whole design rests on. If this ever fails, a status
        # query stops being invisible to servers that predate it.
        server = self._serve()
        client = self._client(0.4)
        server._handle_discovery(struct.pack("<HHH", 0, 0x1234, 0), client.getsockname())
        with self.assertRaises(socket.timeout):
            client.recvfrom(2048)

    def test_ordinary_discovery_still_gets_an_inform(self):
        # The other half: adding the status branch must not have disturbed the
        # exchange the console actually uses.
        server = self._serve()
        client = self._client(2.0)
        server._handle_discovery(struct.pack("<HHH", 0, 0xF5F5, 0), client.getsockname())
        data, _ = client.recvfrom(2048)
        self.assertGreaterEqual(len(data), 6)
        (header,) = struct.unpack_from("<H", data, 0)
        self.assertEqual(header & 0xF, 1, "reply to DISCOVERY must be an INFORM")
        self.assertEqual(struct.unpack_from("<H", data, 2)[0], 0xF5F5)

        # An ordinary DISCOVERY schedules a delayed compatibility INFORM on a
        # timer thread. Let it fire before cleanup closes the socket out from
        # under it -- otherwise the thread raises WinError 10038 into the test
        # output, which looks like a failure and is only this teardown race.
        # Nothing in the server does this; a real server outlives its sockets.
        time.sleep(DEFAULT_FALLBACK_SECONDS + 0.2)


if __name__ == "__main__":
    unittest.main()
