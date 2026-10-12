"""Reference-derived CADQ wire vectors, without accounts or network services.

Contract: Rian6/caduceus-opl at 7a85103eaf65bf19fc155dba239a86acdaae6a55,
integrations/caduceus/caduceus-ra-bridge.ts. Its reply closure constructs
CADR1/2 and Buffer.alloc(max(128, ceil((body.length + 1)/64)*64)).
The exact boundary vectors below preserve the trailing NUL at aligned lengths.
These validate the owned adapter, not a physical loader or the whole runtime.
"""
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

from launcher import caduceus


class ReferenceWireTests(unittest.TestCase):
    def test_catalog_reply_vectors_match_reference_bytes(self):
        image_hash = "a" * 32
        # 44-byte prefix + 84 title bytes = exactly 128, requiring 192 bytes.
        vectors = [
            (1, False, "NO", "CADR1 " + image_hash + " NO", 128),
            (2, False, "UNKNOWN", "CADR2 " + image_hash + " OFFLINE UNKNOWN", 128),
            (2, True, "NO", "CADR2 " + image_hash + " READY NO", 128),
            (1, True, "OK 1 " + "X" * 84,
             "CADR1 " + image_hash + " OK 1 " + "X" * 84, 192),
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bridge = caduceus.Bridge(root / "profile", root / "games")
            self.addCleanup(bridge.close)
            peer = ("192.0.2.10", 40000)
            for version, ready, answer, expected, packet_size in vectors:
                with self.subTest(version=version, ready=ready, answer=answer):
                    bridge.state = {"login": {"ok": ready}}
                    bridge.catalog[image_hash] = (time.monotonic(), answer)
                    sock = mock.Mock()
                    bridge.receive(sock, 18197,
                                   (f"CADQ{version} {image_hash}").encode("ascii"),
                                   peer, time.monotonic())
                    body = expected.encode("ascii")
                    sock.sendto.assert_called_once_with(
                        body + b"\0" * (packet_size - len(body)), peer)

    def test_reference_requires_exact_request_length_and_lowercase_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bridge = caduceus.Bridge(root / "profile", root / "games")
            self.addCleanup(bridge.close)
            sock = mock.Mock()
            with mock.patch.object(bridge, "submit") as submit:
                for packet in (b"CADQ2 " + b"A" * 32,
                               b"CADQ2 " + b"a" * 32 + b"\0",
                               b"CADQ2 " + b"a" * 31,
                               b"CADQ3 " + b"a" * 32):
                    bridge.receive(sock, 18197, packet,
                                   ("192.0.2.10", 40000), time.monotonic())
            sock.sendto.assert_not_called()
            submit.assert_not_called()
