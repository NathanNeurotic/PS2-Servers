"""ORBIT v1.0.2 source-derived wire cases; no claim of console acceptance.

Reference: danielnuld/ps2-launcher at 7a88a6a5fdc935001d977497460716762edef404,
achievements.c and third_party/neutrino-igr/raagent/src/main.c.
"""
import unittest

from launcher.ra_protocol import parse_request, parse_snapshot
from launcher.ra_receiver import Receiver
from launcher.ra_watchlist import validate_orbit_watchlist, validate_watchlist
from tests.test_ra_watchlist import watch_file


def orbit_snapshot(values, count=1, sequence=1):
    # Field order and widths are from send_snapshot, not our OPL fixture.
    header = (f"RA15 seq=000000 sz=0000 us=00000 mx=00000 rxq=000 fail=000000 "
              f"err=+000 sk=000000 lk=0000 sq={sequence:06d} ds=000000 bad=0000 "
              f"n={count:04d} vb={len(values):04d} pt=0 np=1 rc=0000000 fc=0000000 "
              "id=SLUS_213.76~~~~ ").encode("ascii")
    size = f"{len(header) + len(values):04d}".encode("ascii")
    return header[:19] + size + header[23:] + values


class OrbitReferenceTests(unittest.TestCase):
    def test_launcher_and_agent_use_different_reply_ports(self):
        for port in (18194, 18195):
            with self.subTest(port=port):
                request = parse_request(f"RAP1 192.0.2.10 {port}".encode())
                self.assertEqual(request.port, port)
        request = parse_request(b"RAQ1 " + b"a" * 32 + b" SLUS_213.76 192.0.2.10 18194")
        self.assertEqual(request.serial, "SLUS_213.76")

    def test_full_agent_header_at_single_packet_value_ceiling(self):
        values = bytes(range(256)) * 4 + bytes(range(192))
        packet = orbit_snapshot(values, count=304)
        self.assertLessEqual(len(packet), 1472)
        part = parse_snapshot(packet)
        self.assertEqual((part.serial, part.index, part.parts, part.count),
                         ("SLUS_213.76", 0, 1, 304))
        self.assertEqual(part.values, values)

    def test_prelaunch_transfer_survives_agent_discovery(self):
        receiver = Receiver()
        launcher = ("192.0.2.10", 18194)
        agent = ("192.0.2.10", 18195)
        query = b"RAQ1 " + b"a" * 32 + b" SLUS_213.76 192.0.2.10 18194"
        self.assertTrue(receiver.receive(query, launcher).reply.startswith(b"RAA1 WAIT"))
        job, = receiver.take_jobs()
        watch = watch_file([0x40080000])
        self.assertTrue(receiver.complete(job, watch, achievements=1))
        chunk = receiver.receive(b"RAG1 " + b"a" * 32 + b" 0 192.0.2.10 18194", launcher)
        self.assertTrue(chunk.reply.startswith(b"RAC1 0 "))
        discovery = receiver.receive(b"RAP1 192.0.2.10 18195", agent)
        self.assertEqual(discovery.destination, agent)
        self.assertTrue(discovery.reply.startswith(b"RAO1 OK"))
        self.assertEqual(receiver.receive(orbit_snapshot(b"abcd"), agent).values, b"abcd")

    def test_profile_rejects_sets_the_agent_cannot_fully_serve(self):
        at_limit = watch_file([0x40080000] * 304)
        self.assertEqual(validate_orbit_watchlist(at_limit).direct_bytes, 1216)
        too_large = watch_file([0x40080000] * 305)
        with self.assertRaisesRegex(ValueError, "1216"):
            validate_orbit_watchlist(too_large)
        chained = watch_file([0x40080000], [(4 << 13, 4)])
        with self.assertRaisesRegex(ValueError, "pointer-chain"):
            validate_orbit_watchlist(chained)
        # The generic OPL profile remains capable of both kinds of set.
        self.assertEqual(validate_watchlist(too_large).direct_bytes, 1220)
        self.assertEqual(validate_watchlist(chained).nodes, 1)
