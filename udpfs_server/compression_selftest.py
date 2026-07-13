#!/usr/bin/env python3
"""UDPFS transparent-compression listing self-test (no PS2 hardware needed).

Drives a real UdpfsServer's DOPEN/DREAD/GETSTAT/OPEN handlers directly (reply
senders stubbed out) against a temp games folder holding plain and compressed
images. This is the safety net for the compressed-listing path -- the exact
code that once shipped a bug where CHD files silently vanished from OPL game
lists because the DREAD/GETSTAT handlers' hardcoded extension checks drifted
out of sync with the open path:

  * LISTING  -- supported compressed files appear as .iso with their
                UNCOMPRESSED sizes; plain .iso entries are untouched.
  * GETSTAT  -- a virtual .iso resolves to its compressed sibling; a raw
                compressed path reports the uncompressed size.
  * GATING   -- formats missing from COMPRESSED_EXTENSIONS (library not
                available) are never renamed, probed, or advertised.
  * DATA     -- a real CSO round-trips byte-exact through OPEN; a corrupt
                container advertised as .iso is refused (EIO), never served
                as raw garbage bytes.

CHD fixtures are header-only (a valid CHD v5 header is enough for the listing
and stat paths, which never decompress), so the test needs no libchdr. The
CSO fixtures are real zlib-compressed images, so the data test always runs.

Run:  python udpfs_server/compression_selftest.py
"""

import errno
import os
import shutil
import struct
import sys
import tempfile
import zlib

import udpfs_server as srv  # sys.path[0] is this dir when run as a script


# --------------------------------------------------------------------------- #
# Fixture builders
# --------------------------------------------------------------------------- #
def make_plain_iso(path, size=4096):
    data = bytes(range(256)) * (size // 256)
    with open(path, 'wb') as f:
        f.write(data)
    return data


def make_cso(path, data, block_size=2048):
    """Write a real CSO v1 image (raw-deflate blocks, align 0, n+1 offsets)."""
    num_blocks = (len(data) + block_size - 1) // block_size
    blobs = []
    for i in range(num_blocks):
        chunk = data[i * block_size:(i + 1) * block_size]
        comp = zlib.compressobj(9, zlib.DEFLATED, -15)
        blobs.append(comp.compress(chunk) + comp.flush())
    offsets = []
    cur = 24 + (num_blocks + 1) * 4
    for blob in blobs:
        offsets.append(cur)  # MSB clear = compressed block
        cur += len(blob)
    offsets.append(cur)      # end sentinel
    header = struct.pack('<IIQIBBH', srv.CSO_MAGIC, 24, len(data), block_size, 1, 0, 0)
    with open(path, 'wb') as f:
        f.write(header)
        f.write(struct.pack('<%dI' % len(offsets), *offsets))
        f.write(b''.join(blobs))


def make_chd_header(path, stored_size, hunk_bytes, unit_bytes, codec0=0):
    """Write a minimal valid CHD v5 header (no hunk data -- listing only)."""
    hdr = bytearray(64)
    hdr[0:8] = b'MComprHD'
    struct.pack_into('>I', hdr, 8, 64)           # header length
    struct.pack_into('>I', hdr, 12, 5)           # version 5
    struct.pack_into('>4I', hdr, 16, codec0, 0, 0, 0)
    struct.pack_into('>Q', hdr, 32, stored_size)  # logical bytes
    struct.pack_into('>I', hdr, 56, hunk_bytes)
    struct.pack_into('>I', hdr, 60, unit_bytes)
    with open(path, 'wb') as f:
        f.write(hdr)


def make_zso_header(path, uncompressed_size, block_size=2048):
    """Write a ZSO header (enough for the listing/stat paths, which only
    parse the header; no LZ4 needed)."""
    header = struct.pack('<4sIQIBBH', b'ZSO\x00', 24, uncompressed_size,
                         block_size, 1, 0, 0)
    with open(path, 'wb') as f:
        f.write(header)


# CHD sizes: DVD-style (unit 2048) keeps the stored size; CD-style (cdlz codec,
# 2448-byte frames) is corrected to frames * 2048.
DVD_CHD_SIZE = 0x30000                      # 196608, reported as-is
CD_CODEC_CDLZ = 0x63646C7A                  # 'cdlz'
CD_STORED = 3 * 4896                        # 3 hunks of 2 frames (2448 each)
CD_EXPECTED = 3 * 2 * 2048                  # 12288: frames presented as 2048/sector
ZSO_SIZE = 123456


# --------------------------------------------------------------------------- #
# Handler driver (stubs the UDP reply senders, calls handlers directly)
# --------------------------------------------------------------------------- #
class Driver:
    def __init__(self, root_dir, enable_compression=True):
        self.server = srv.UdpfsServer(root_dir=root_dir, port=0,
                                      enable_compression=enable_compression)
        self.addr = ('127.0.0.1', 4660)
        self.server._local.session = srv.Session(self.server, self.addr)
        self.replies = []
        self.server._send_open_reply = \
            lambda a, result, stat_info=None: self.replies.append((result, stat_info))
        self.server._send_dread_reply = \
            lambda a, result, name=None, stat_info=None: self.replies.append((result, name, stat_info))
        self.server._send_getstat_reply = \
            lambda a, result, stat_info=None: self.replies.append((result, stat_info))

    def close(self):
        # Close every handle this driver opened (compressed wrappers and raw
        # file objects) before deleting the fixtures -- otherwise Windows can't
        # unlink the still-open files and shutil.rmtree(ignore_errors=True)
        # silently leaves the temp dir behind. Mirrors Session._run's cleanup.
        session = getattr(self.server._local, 'session', None)
        if session is not None:
            for fh in list(session.handles.values()):
                try:
                    fh.close()
                except Exception:
                    pass
            session.handles.clear()
        self.server.sock.close()
        self.server.dsock.close()

    def open(self, path, is_dir=False, flags=0x01):
        payload = struct.pack('<BBHi', 0, 1 if is_dir else 0, flags, 0)
        self.server._handle_open(self.addr, payload + path.encode() + b'\x00')
        return self.replies.pop()

    def getstat(self, path):
        self.server._handle_getstat(self.addr, b'\x00' * 4 + path.encode() + b'\x00')
        return self.replies.pop()

    def listdir(self, path='/'):
        handle, _ = self.open(path, is_dir=True)
        assert handle >= 0, "DOPEN %r failed: %d" % (path, handle)
        entries = {}
        while True:
            self.server._handle_dread(self.addr, struct.pack('<BBBBi', 0, 0, 0, 0, handle))
            result, name, stat_info = self.replies.pop()
            if result <= 0:
                break
            entries[name] = stat_info['size']
        return entries

    def handle_obj(self, handle):
        return self.server.handles[handle].obj


def check(cond, what):
    if cond:
        print("  ok: %s" % what)
    else:
        print("  FAIL: %s" % what)
        raise AssertionError(what)


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
def test_listing_and_getstat(root):
    """All formats advertised: compressed entries list as .iso with
    uncompressed sizes; GETSTAT resolves both virtual and raw paths."""
    print("[listing] all formats advertised")
    saved = srv.COMPRESSED_EXTENSIONS
    srv.COMPRESSED_EXTENSIONS = ('.zso', '.cso', '.chd')
    d = Driver(root)
    try:
        entries = d.listdir()
        check(entries.get('Plain.iso') == 4096, "plain .iso listed unchanged")
        check(entries.get('GameA.iso') == 5000, "CSO listed as .iso with uncompressed size")
        check(entries.get('GameB.iso') == DVD_CHD_SIZE, "DVD CHD listed as .iso with stored size")
        check(entries.get('GameC.iso') == CD_EXPECTED, "CD CHD listed as .iso with frame-corrected size")
        check(entries.get('GameD.iso') == ZSO_SIZE, "ZSO listed as .iso with uncompressed size")
        check(entries.get('UPPER.iso') == DVD_CHD_SIZE,
              "uppercase-extension CHD listed as .iso")
        check('GameA.cso' not in entries and 'GameB.chd' not in entries,
              "raw compressed names not listed")

        print("[getstat]")
        result, stat_info = d.getstat('/GameB.iso')
        check(result == 0 and stat_info['size'] == DVD_CHD_SIZE,
              "GETSTAT virtual .iso resolves CHD sibling")
        # On case-sensitive filesystems (Linux CI) this exercises the
        # case-insensitive sibling fallback in _resolve_compressed_sibling
        result, stat_info = d.getstat('/UPPER.iso')
        check(result == 0 and stat_info['size'] == DVD_CHD_SIZE,
              "GETSTAT virtual .iso resolves uppercase-extension sibling")
        result, stat_info = d.getstat('/GameA.iso')
        check(result == 0 and stat_info['size'] == 5000,
              "GETSTAT virtual .iso resolves CSO sibling")
        result, stat_info = d.getstat('/GameB.chd')
        check(result == 0 and stat_info['size'] == DVD_CHD_SIZE,
              "GETSTAT raw .chd reports uncompressed size")
        result, _ = d.getstat('/Missing.iso')
        check(result == -errno.ENOENT, "GETSTAT missing .iso -> ENOENT")
    finally:
        srv.COMPRESSED_EXTENSIONS = saved
        d.close()


def test_gating(root):
    """Formats absent from COMPRESSED_EXTENSIONS (library unavailable) are
    never advertised: no rename in listings, no sibling probe, no open."""
    print("[gating] only CSO advertised")
    saved = srv.COMPRESSED_EXTENSIONS
    srv.COMPRESSED_EXTENSIONS = ('.cso',)
    d = Driver(root)
    try:
        entries = d.listdir()
        check(entries.get('GameA.iso') == 5000, "CSO still advertised")
        check(entries.get('GameB.chd') == 64, "unsupported CHD listed raw with container size")
        check('GameB.iso' not in entries, "unsupported CHD not renamed to .iso")
        result, _ = d.getstat('/GameB.iso')
        check(result == -errno.ENOENT, "GETSTAT does not probe unsupported sibling")
        result, _ = d.open('/GameB.iso')
        check(result == -errno.ENOENT, "OPEN does not probe unsupported sibling")
        handle, stat_info = d.open('/GameB.chd')
        check(handle >= 0 and stat_info['size'] == 64,
              "unsupported CHD still opens raw by its real name")
    finally:
        srv.COMPRESSED_EXTENSIONS = saved
        d.close()


def test_data_roundtrip(root, cso_data):
    """OPEN of the virtual .iso serves byte-exact decompressed data; a corrupt
    container is refused with EIO instead of being served raw."""
    print("[data] CSO round-trip and corrupt-container refusal")
    d = Driver(root)
    try:
        handle, stat_info = d.open('/GameA.iso')
        check(handle >= 0 and stat_info['size'] == 5000,
              "OPEN virtual .iso -> compressed wrapper handle")
        wrapper = d.handle_obj(handle)
        wrapper.seek(0)
        check(wrapper.read(len(cso_data)) == cso_data,
              "decompressed bytes match the original image exactly")

        # Uppercase container: on case-sensitive filesystems the exact-case
        # probe misses and the case-insensitive fallback must find MIXED.CSO
        handle, stat_info = d.open('/MIXED.iso')
        check(handle >= 0 and stat_info['size'] == 5000,
              "OPEN virtual .iso resolves uppercase-extension CSO sibling")
        wrapper = d.handle_obj(handle)
        wrapper.seek(0)
        check(wrapper.read(len(cso_data)) == cso_data,
              "uppercase sibling decompresses byte-exact too")

        result, _ = d.open('/Corrupt.iso')
        check(result == -errno.EIO, "OPEN virtual .iso over corrupt container -> EIO, not raw bytes")
        handle, stat_info = d.open('/Corrupt.cso')
        check(handle >= 0 and stat_info['size'] == os.path.getsize(os.path.join(root, 'Corrupt.cso')),
              "corrupt container still opens raw by its real name")
    finally:
        d.close()


def test_compression_disabled(root):
    """Without --enable-compression nothing is renamed or probed."""
    print("[disabled] compression off")
    d = Driver(root, enable_compression=False)
    try:
        entries = d.listdir()
        check('GameA.cso' in entries and 'GameA.iso' not in entries,
              "compressed files listed raw when compression is off")
        result, _ = d.open('/GameA.iso')
        check(result == -errno.ENOENT, "virtual .iso not probed when compression is off")
    finally:
        d.close()


def test_prefers_parseable_sibling(root):
    """A corrupt sibling that sorts earlier in COMPRESSED_EXTENSIONS must not
    hide a valid later one -- both GETSTAT and OPEN pick the decodable image."""
    print("[prefer-parseable] corrupt earlier sibling doesn't hide a valid one")
    saved = srv.COMPRESSED_EXTENSIONS
    srv.COMPRESSED_EXTENSIONS = ('.zso', '.cso', '.chd')
    d = Driver(root)
    try:
        # PrefG.zso is garbage (sorts first); PrefG.cso is a valid 5000-byte image.
        result, stat_info = d.getstat('/PrefG.iso')
        check(result == 0 and stat_info['size'] == 5000,
              "GETSTAT skips corrupt .zso, reports the valid .cso size")
        handle, stat_info = d.open('/PrefG.iso')
        check(handle >= 0 and stat_info['size'] == 5000,
              "OPEN skips corrupt .zso, opens the valid .cso")
    finally:
        srv.COMPRESSED_EXTENSIONS = saved
        d.close()


def test_exact_stem_no_case_collision(root):
    """On a case-sensitive filesystem, 'Coll.iso' must resolve to the exact-stem
    sibling 'Coll.CSO', never a different-cased 'coll.cso' (a distinct game)."""
    probe = os.path.join(root, 'CaseProbe.marker')
    with open(probe, 'w'):
        pass
    case_sensitive = not os.path.exists(os.path.join(root, 'caseprobe.marker'))
    os.remove(probe)
    if not case_sensitive:
        print("[collision] skipped (case-insensitive filesystem)")
        return
    print("[collision] exact-stem match on case-sensitive filesystem")
    saved = srv.COMPRESSED_EXTENSIONS
    srv.COMPRESSED_EXTENSIONS = ('.zso', '.cso', '.chd')
    d = Driver(root)
    try:
        up = d.server._resolve_compressed_sibling('/Coll.iso')
        check(up is not None and os.path.basename(up) == 'Coll.CSO',
              "Coll.iso resolves to exact-stem Coll.CSO, not coll.cso")
        lo = d.server._resolve_compressed_sibling('/coll.iso')
        check(lo is not None and os.path.basename(lo) == 'coll.cso',
              "coll.iso resolves to exact-stem coll.cso")
    finally:
        srv.COMPRESSED_EXTENSIONS = saved
        d.close()


def test_extension_gating_builder():
    """Pin the COMPRESSED_EXTENSIONS builder directly: monkeypatching the tuple
    in other tests would hide a regression in the library-availability gating
    (the construction that keeps undecompressable formats off the wire)."""
    print("[builder] library-availability gating")
    saved = (srv.LZ4_AVAILABLE, srv.LIBCHDR_AVAILABLE)
    try:
        srv.LZ4_AVAILABLE, srv.LIBCHDR_AVAILABLE = True, True
        check(srv._supported_compressed_extensions() == ('.zso', '.cso', '.chd'),
              "all libraries available -> all three formats")
        srv.LZ4_AVAILABLE, srv.LIBCHDR_AVAILABLE = False, True
        check(srv._supported_compressed_extensions() == ('.cso', '.chd'),
              "no lz4 -> ZSO dropped")
        srv.LZ4_AVAILABLE, srv.LIBCHDR_AVAILABLE = True, False
        check(srv._supported_compressed_extensions() == ('.zso', '.cso'),
              "no libchdr -> CHD dropped")
        srv.LZ4_AVAILABLE, srv.LIBCHDR_AVAILABLE = False, False
        check(srv._supported_compressed_extensions() == ('.cso',),
              "neither library -> only CSO (zlib is stdlib)")
    finally:
        srv.LZ4_AVAILABLE, srv.LIBCHDR_AVAILABLE = saved


def main():
    root = tempfile.mkdtemp(prefix='udpfs_comptest_')
    try:
        make_plain_iso(os.path.join(root, 'Plain.iso'), 4096)
        cso_data = make_plain_iso(os.path.join(root, '_src.bin'), 5120)[:5000]
        os.remove(os.path.join(root, '_src.bin'))
        make_cso(os.path.join(root, 'GameA.cso'), cso_data)
        make_cso(os.path.join(root, 'MIXED.CSO'), cso_data)
        make_chd_header(os.path.join(root, 'GameB.chd'), DVD_CHD_SIZE,
                        hunk_bytes=4096, unit_bytes=2048)
        make_chd_header(os.path.join(root, 'UPPER.CHD'), DVD_CHD_SIZE,
                        hunk_bytes=4096, unit_bytes=2048)
        make_chd_header(os.path.join(root, 'GameC.chd'), CD_STORED,
                        hunk_bytes=4896, unit_bytes=2448, codec0=CD_CODEC_CDLZ)
        make_zso_header(os.path.join(root, 'GameD.zso'), ZSO_SIZE)
        with open(os.path.join(root, 'Corrupt.cso'), 'wb') as f:
            f.write(b'this is not a CISO container at all........')
        # prefer-parseable fixtures: corrupt .zso (sorts first) + valid .cso
        with open(os.path.join(root, 'PrefG.zso'), 'wb') as f:
            f.write(b'not a real ZSO header, just garbage bytes')
        make_cso(os.path.join(root, 'PrefG.cso'), cso_data)
        # case-collision fixtures (only distinct on a case-sensitive filesystem)
        make_cso(os.path.join(root, 'Coll.CSO'), cso_data)
        make_cso(os.path.join(root, 'coll.cso'), cso_data)

        test_listing_and_getstat(root)
        test_gating(root)
        test_data_roundtrip(root, cso_data)
        test_compression_disabled(root)
        test_prefers_parseable_sibling(root)
        test_exact_stem_no_case_collision(root)
        test_extension_gating_builder()
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print()
    print("ALL UDPFS COMPRESSION TESTS PASSED")


if __name__ == '__main__':
    main()
