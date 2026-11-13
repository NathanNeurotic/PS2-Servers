"""SMBv1 --enable-compression: CHD and CSO images served as plain .iso files.

Drives the real SMB1 handlers. The decoders are the UDPFS ones the HTTP server
already uses, so these tests are about the SMB side: what is listed, what
opens, what can never be written, and that the option changes nothing when off.
"""

import os
import shutil
import struct
import sys
import tempfile
import types
import unittest
import zlib

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "smbv1_server"))

import smbserver_opl as smb  # noqa: E402

SECTOR = 2048


def make_cso(image):
    """A real CSO v1 container (raw deflate per sector)."""
    count = len(image) // SECTOR
    index, payload = [], bytearray()
    offset = 24 + (count + 1) * 4
    for i in range(count):
        block = image[i * SECTOR:(i + 1) * SECTOR]
        packer = zlib.compressobj(level=9, wbits=-15)
        packed = packer.compress(block) + packer.flush()
        compress_it = len(packed) < len(block)
        index.append(offset | (0 if compress_it else 0x80000000))
        data = packed if compress_it else block
        payload.extend(data)
        offset += len(data)
    index.append(offset)
    header = struct.pack("<4sIQIBBH", b"CISO", 24, len(image), SECTOR, 1, 0, 0)
    return header + struct.pack("<%dI" % len(index), *index) + payload


def _request(params=b"", data=b"", msg=b""):
    return types.SimpleNamespace(tid=1, params=params, data=data, msg=msg)


class CompressedOverSmb1(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.dvd = os.path.join(self.root, "DVD")
        os.mkdir(self.dvd)
        self.image = bytes((i * 7 + i // SECTOR) & 0xFF for i in range(40 * SECTOR))
        self.cso_bytes = make_cso(self.image)
        with open(os.path.join(self.dvd, "GAME.cso"), "wb") as f:
            f.write(self.cso_bytes)
        compression, why = smb.load_compression()
        self.assertIsNotNone(compression, why)
        self.compression = compression

    def _conn(self, enabled=True):
        share = smb.Share("games", self.root)
        server = smb.SmbServer({"games": share}, read_only=False,
                               compression=self.compression if enabled else None)
        conn = smb.Conn(server)
        conn.trees[1] = share
        self.addCleanup(conn.cleanup)
        return conn

    # -- helpers driving the handlers -------------------------------------
    def _list(self, conn, pattern="\\DVD\\*"):
        tp = struct.pack("<HHHHI", 0x16, 100, 0, 0x104, 0) + pattern.encode() + b"\x00"
        status = smb.h_trans2_find_first2(conn, _request(), tp, b"")[2]
        self.assertEqual(status, smb.STATUS_SUCCESS)
        entries, _ = conn.searches[max(conn.searches)]
        return {name: size for name, size in self._sizes(entries)}

    @staticmethod
    def _sizes(entries):
        for name, path, size in entries:
            if name in (".", ".."):
                continue
            rec = smb._dir_entry_104(name, path, size)
            yield name, struct.unpack_from("<Q", rec, 40)[0]

    def _query_size(self, conn, path):
        tp = struct.pack("<HI", smb.SMB_QUERY_FILE_STANDARD_INFO, 0) + path.encode() + b"\x00"
        result = smb.h_trans2_query_path(conn, _request(), tp, b"")
        return result[2], result

    def _create(self, conn, name, disposition=smb.FILE_OPEN):
        enc = name.encode("ascii")
        params = bytearray(48)
        struct.pack_into("<H", params, 5, len(enc))
        struct.pack_into("<I", params, 35, disposition)
        p, _, status = smb.h_nt_create_andx(conn, _request(bytes(params), b"\x00" + enc))[:3]
        return status, (struct.unpack_from("<H", p, 5)[0] if status == smb.STATUS_SUCCESS else None)

    def _read(self, conn, fid, offset, count):
        params = struct.pack("<BBHHIHHIHI", 0xFF, 0, 0, fid, offset, count, 0, 0, 0, 0)
        p, data, status = smb.h_read_andx(conn, _request(params))[:3]
        self.assertEqual(status, smb.STATUS_SUCCESS)
        return data[:struct.unpack_from("<H", p, 10)[0]]

    def _read_all(self, conn, fid, total):
        out = bytearray()
        while len(out) < total:
            chunk = self._read(conn, fid, len(out), 61440)
            if not chunk:
                break
            out += chunk
        return bytes(out)

    def _write(self, conn, fid, payload):
        params = bytearray(28)
        struct.pack_into("<H", params, 4, fid)
        struct.pack_into("<H", params, 20, len(payload))
        struct.pack_into("<H", params, 22, 64)
        return smb.h_write_andx(conn, _request(bytes(params), msg=b"\x00" * 64 + payload))[2]

    # -- off by default ------------------------------------------------------
    def test_off_by_default_nothing_changes(self):
        conn = self._conn(enabled=False)
        self.assertEqual(self._list(conn), {"GAME.cso": len(self.cso_bytes)})
        self.assertEqual(self._create(conn, "\\DVD\\GAME.iso")[0], smb.STATUS_OBJECT_NAME_NOT_FOUND)

    # -- on --------------------------------------------------------------------
    def test_image_listed_opened_and_read_as_iso(self):
        conn = self._conn()
        self.assertEqual(self._list(conn), {"GAME.iso": len(self.image)})
        self.assertEqual(self._query_size(conn, "\\DVD\\GAME.iso")[0], smb.STATUS_SUCCESS)
        status, fid = self._create(conn, "\\DVD\\GAME.iso")
        self.assertEqual(status, smb.STATUS_SUCCESS)
        self.assertEqual(self._read_all(conn, fid, len(self.image)), self.image)
        self.assertEqual(self._read(conn, fid, 5 * SECTOR + 100, 3000),
                         self.image[5 * SECTOR + 100:5 * SECTOR + 3100])
        self.assertEqual(self._read(conn, fid, len(self.image), 4096), b"")

    def test_open_andx_path_too(self):
        conn = self._conn()
        params, _, status = smb.h_open_andx(conn, _request(data=b"\\DVD\\GAME.iso\x00"))[:3]
        self.assertEqual(status, smb.STATUS_SUCCESS)
        fid = struct.unpack_from("<H", params, 4)[0]
        self.assertEqual(self._read(conn, fid, 0, SECTOR), self.image[:SECTOR])

    def test_image_is_never_written(self):
        conn = self._conn()
        status, fid = self._create(conn, "\\DVD\\GAME.iso")
        self.assertEqual(self._write(conn, fid, b"X" * 512), smb.STATUS_ACCESS_DENIED)
        for disposition in (smb.FILE_SUPERSEDE, smb.FILE_OVERWRITE, smb.FILE_OVERWRITE_IF):
            self.assertEqual(self._create(conn, "\\DVD\\GAME.iso", disposition)[0],
                             smb.STATUS_ACCESS_DENIED)
        self.assertEqual(self._create(conn, "\\DVD\\GAME.iso", smb.FILE_CREATE)[0],
                         smb.STATUS_OBJECT_NAME_COLLISION)
        with open(os.path.join(self.dvd, "GAME.cso"), "rb") as f:
            self.assertEqual(f.read(), self.cso_bytes)
        self.assertFalse(os.path.exists(os.path.join(self.dvd, "GAME.iso")))

    def test_real_iso_beside_it_wins(self):
        with open(os.path.join(self.dvd, "GAME.iso"), "wb") as f:
            f.write(b"R" * SECTOR)
        conn = self._conn()
        self.assertEqual(self._list(conn), {"GAME.iso": SECTOR, "GAME.cso": len(self.cso_bytes)})
        status, fid = self._create(conn, "\\DVD\\GAME.iso")
        self.assertEqual(self._read(conn, fid, 0, 16), b"R" * 16)

    def test_any_case_extension_lists_and_opens(self):
        os.rename(os.path.join(self.dvd, "GAME.cso"), os.path.join(self.dvd, "GAME.CsO"))
        conn = self._conn()
        self.assertEqual(self._list(conn), {"GAME.iso": len(self.image)})
        self.assertEqual(self._create(conn, "\\DVD\\GAME.iso")[0], smb.STATUS_SUCCESS)

    def test_unreadable_image_keeps_its_name(self):
        with open(os.path.join(self.dvd, "BAD.cso"), "wb") as f:
            f.write(b"not a cso at all")
        conn = self._conn()
        listing = self._list(conn)
        self.assertIn("BAD.cso", listing)
        self.assertNotIn("BAD.iso", listing)

    def test_zso_is_left_for_the_console(self):
        with open(os.path.join(self.dvd, "OTHER.zso"), "wb") as f:
            f.write(b"ZISO" + bytes(60))
        conn = self._conn()
        self.assertIn("OTHER.zso", self._list(conn))
        self.assertNotIn(".zso", self.compression[0])

    @unittest.skipIf(os.name == "nt", "symlinks need privileges on Windows")
    def test_symlinked_image_outside_the_share_is_refused(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        target = os.path.join(outside.name, "SECRET.cso")
        shutil.copy(os.path.join(self.dvd, "GAME.cso"), target)
        os.symlink(target, os.path.join(self.dvd, "LINK.cso"))
        conn = self._conn()
        self.assertEqual(self._create(conn, "\\DVD\\LINK.iso")[0], smb.STATUS_OBJECT_NAME_NOT_FOUND)

    def test_traversal_to_a_real_image_outside_the_share_is_refused(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        with open(os.path.join(outside.name, "GAME.cso"), "wb") as f:
            f.write(self.cso_bytes)
        sibling = os.path.basename(outside.name)
        self.assertEqual(os.path.dirname(outside.name), os.path.dirname(self.root))
        conn = self._conn()
        self.assertNotEqual(self._create(conn, "\\..\\%s\\GAME.iso" % sibling)[0],
                            smb.STATUS_SUCCESS)

    def test_huge_block_header_is_refused(self):
        # 34 bytes claiming 256 MiB blocks: opening must not decode a block.
        bomb = struct.pack("<4sIQIBBH", b"CISO", 24, 0x10000000, 0x10000000, 1, 0, 0)
        bomb += struct.pack("<II", 32, 34) + b"\x03\x00"
        with open(os.path.join(self.dvd, "BOMB.cso"), "wb") as f:
            f.write(bomb)
        conn = self._conn()
        self.assertNotEqual(self._create(conn, "\\DVD\\BOMB.iso")[0], smb.STATUS_SUCCESS)

    def test_inflate_never_exceeds_one_block(self):
        packer = zlib.compressobj(level=9, wbits=-15)
        packed = packer.compress(b"\x00" * (8 * 1024 * 1024)) + packer.flush()
        header = struct.pack("<4sIQIBBH", b"CISO", 24, 17 * SECTOR, SECTOR, 1, 0, 0)
        index = [24 + 18 * 4] + [24 + 18 * 4 + len(packed)] * 17
        path = os.path.join(self.dvd, "DEEP.cso")
        with open(path, "wb") as f:
            f.write(header + struct.pack("<18I", *index) + packed)
        image = self.compression[2](path)
        self.addCleanup(image.close)
        self.assertEqual(len(image._decompress_block(0)), SECTOR)

    def test_real_chd_fixture(self):
        if ".chd" not in self.compression[0]:
            self.skipTest("libchdr is not available on this machine")
        sys.path.insert(0, _ROOT)
        from tests.test_ra_compressed_hash import fixture
        image, _ = fixture()
        shutil.copy(os.path.join(_ROOT, "tests", "fixtures", "ra_hash", "synthetic-dvd.chd"),
                    os.path.join(self.dvd, "SYNTH.chd"))
        conn = self._conn()
        self.assertEqual(self._list(conn).get("SYNTH.iso"), len(image))
        status, fid = self._create(conn, "\\DVD\\SYNTH.iso")
        self.assertEqual(status, smb.STATUS_SUCCESS)
        self.assertEqual(self._read_all(conn, fid, len(image)), image)

    def test_cd_chd_shows_the_disc_length_not_the_hunk_padding(self):
        if ".chd" not in self.compression[0]:
            self.skipTest("libchdr is not available on this machine")
        # 1003 MODE1_RAW sectors; chdman pads it to 1004 frames and 1008 in hunks.
        sectors = 1003
        expected = b"".join(i.to_bytes(4, "little") * 512 for i in range(sectors))
        shutil.copy(os.path.join(_ROOT, "tests", "fixtures", "cd_chd", "synthetic-cd-1003.chd"),
                    os.path.join(self.dvd, "CDGAME.chd"))
        conn = self._conn()
        self.assertEqual(self._list(conn).get("CDGAME.iso"), len(expected))
        status, fid = self._create(conn, "\\DVD\\CDGAME.iso")
        self.assertEqual(status, smb.STATUS_SUCCESS)
        self.assertEqual(self._read_all(conn, fid, len(expected) + 4096), expected)


if __name__ == "__main__":
    unittest.main()
