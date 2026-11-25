"""FIND_FIRST2 / FIND_NEXT2 return as many entries as the client asks and has room for."""
import os
import struct
import tempfile
import types
import unittest

from smbv1_server import smbserver_opl as smb


def _request(max_data):
    params = bytearray(30)
    struct.pack_into("<H", params, 6, max_data)  # MaxDataCount
    return types.SimpleNamespace(tid=1, params=bytes(params), data=b"", msg=b"")


def _walk(data, count):
    names, off = [], 0
    for i in range(count):
        nxt, = struct.unpack_from("<I", data, off)
        length, = struct.unpack_from("<I", data, off + 60)
        names.append(data[off + 94 : off + 94 + length].decode())
        if i < count - 1:
            assert nxt and nxt % 8 == 0, "records are chained and 8-aligned"
            off += nxt
        else:
            assert nxt == 0, "the last record ends the chain"
            assert off + 94 + length == len(data), "nothing follows the last record"
    return names


class FindBatch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.names = ["GAME %03d.iso" % i for i in range(40)]
        for n in self.names:
            open(os.path.join(self.tmp.name, n), "wb").close()
        share = smb.Share("games", self.tmp.name)
        self.conn = smb.Conn(smb.SmbServer({"games": share}, read_only=False))
        self.conn.trees[1] = share
        self.addCleanup(self.conn.cleanup)

    def _reply(self, result):
        params, body, status = result
        self.assertEqual(status, smb.STATUS_SUCCESS)
        p_count, p_off, d_count, d_off = struct.unpack_from("<H H 2x H H", params, 6)
        # offsets count from the SMB header: 32 header + 1 word count + 20 words + 2 byte count
        base = 55
        return body[p_off - base : p_off - base + p_count], body[d_off - base : d_off - base + d_count]

    def _list(self, want, max_data):
        tp = struct.pack("<HHHHI", 0x16, want, 6, 0x104, 0) + b"\\*\x00"
        rp, rd = self._reply(smb.h_trans2_find_first2(self.conn, _request(max_data), tp, b""))
        sid, count, eos = struct.unpack_from("<HHH", rp, 0)
        names, requests, sizes = _walk(rd, count), 1, [len(rd)]
        while not eos:
            tp = struct.pack("<HHHIH", sid, want, 0x104, 0, 0xE) + b"\x00"
            rp, rd = self._reply(smb.h_trans2_find_next2(self.conn, _request(max_data), tp, b""))
            count, eos = struct.unpack_from("<HH", rp, 0)
            names += _walk(rd, count)
            requests += 1
            sizes.append(len(rd))
        return names, requests, sizes

    def test_one_at_a_time_client_is_served_as_before(self):
        names, requests, sizes = self._list(1, 3840)
        self.assertEqual(names, [".", ".."] + self.names)
        self.assertEqual(requests, 42)

    def test_batches_fill_the_clients_buffer_and_lose_nothing(self):
        names, requests, sizes = self._list(64, 2048)
        self.assertEqual(names, [".", ".."] + self.names)
        self.assertLess(requests, 5)
        self.assertLessEqual(max(sizes), 2048)

    def test_count_limits_a_batch(self):
        names, requests, _ = self._list(10, 2048)
        self.assertEqual(names, [".", ".."] + self.names)
        self.assertEqual(requests, 5)

    def test_a_tiny_buffer_still_gets_one_entry_per_reply(self):
        names, requests, _ = self._list(64, 16)
        self.assertEqual(names, [".", ".."] + self.names)
        self.assertEqual(requests, 42)

    def test_a_huge_request_is_capped(self):
        _, _, sizes = self._list(1000, 0xFFFF)
        self.assertLessEqual(max(sizes), smb.FIND_BATCH_MAX)


if __name__ == "__main__":
    unittest.main()
