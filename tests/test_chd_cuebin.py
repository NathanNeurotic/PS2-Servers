"""Virtual read-only CUE/BIN views over supported CD CHD images."""

import ctypes
import errno
import os
import struct
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UDPFS = os.path.join(_ROOT, "udpfs_server")
if _UDPFS not in sys.path:
    sys.path.insert(0, _UDPFS)

import udpfs_server as server_module  # noqa: E402
from compressed_iso import chd_cuebin as cuebin  # noqa: E402


def _track(number, kind, frames, pregap=0, pgtype="MODE1", postgap=0):
    return (f"TRACK:{number} TYPE:{kind} SUBTYPE:NONE FRAMES:{frames} "
            f"PREGAP:{pregap} PGTYPE:{pgtype} PGSUB:NONE POSTGAP:{postgap}\0").encode("ascii")


def _write_chd(path, tracks, *, hunk_frames=4, logical_frames=None):
    physical_frames = 0
    for item in tracks:
        physical_frames += item["frames"] + (-item["frames"]) % 4
    logical_frames = logical_frames or max(physical_frames, hunk_frames)

    encoded = []
    offset = cuebin.HEADER_BYTES
    for i, item in enumerate(tracks):
        payload = _track(**item)
        next_offset = offset + 16 + len(payload) if i + 1 < len(tracks) else 0
        header = (b"CHT2" + b"\0" + len(payload).to_bytes(3, "big") +
                  struct.pack(">Q", next_offset))
        encoded.append(header + payload)
        offset = next_offset

    hunk_bytes = hunk_frames * cuebin.CD_FRAME_BYTES
    header = bytearray(cuebin.HEADER_BYTES)
    header[:8] = b"MComprHD"
    struct.pack_into(">I", header, 8, cuebin.HEADER_BYTES)
    struct.pack_into(">I", header, 12, 5)
    struct.pack_into(">I", header, 16, int.from_bytes(b"cdlz", "big"))
    struct.pack_into(">Q", header, 32, logical_frames * cuebin.CD_FRAME_BYTES)
    struct.pack_into(">Q", header, 48, cuebin.HEADER_BYTES if tracks else 0)
    struct.pack_into(">I", header, 56, hunk_bytes)
    struct.pack_into(">I", header, 60, cuebin.CD_FRAME_BYTES)
    with open(path, "wb") as image:
        image.write(header)
        for record in encoded:
            image.write(record)
        image.write(bytes(max(0, ((logical_frames + hunk_frames - 1) // hunk_frames) * hunk_bytes)))


class _FakeHunkHandle(ctypes.Structure):
    _fields_ = []


class _FakeLibchdr:
    _chd_file_p = ctypes.POINTER(_FakeHunkHandle)

    def __init__(self, hunk_frames):
        self.hunk_bytes = hunk_frames * cuebin.CD_FRAME_BYTES
        self.closed = 0
        self.opened = 0
        self.reads = []

    def chd_open(self, path, mode, parent, out_handle):
        pointer = ctypes.cast(out_handle, ctypes.POINTER(self._chd_file_p))
        pointer.contents = ctypes.pointer(_FakeHunkHandle())
        self.opened += 1
        return 0

    def chd_read(self, handle, index, buffer):
        self.reads.append(index)
        first_frame = index * (self.hunk_bytes // cuebin.CD_FRAME_BYTES)
        payload = bytearray()
        for frame in range(first_frame,
                           first_frame + self.hunk_bytes // cuebin.CD_FRAME_BYTES):
            pair = bytes((frame & 0xFF, (frame + 1) & 0xFF))
            payload.extend(pair * (cuebin.CD_SECTOR_BYTES // 2))
            payload.extend(bytes(cuebin.CD_FRAME_BYTES - cuebin.CD_SECTOR_BYTES))
        ctypes.memmove(buffer, bytes(payload), self.hunk_bytes)
        return 0

    def chd_close(self, handle):
        self.closed += 1


class _ChdFixtureMixin:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = os.path.join(self.temp.name, "Game.chd")
        self.tracks = [
            {"number": 1, "kind": "MODE1_RAW", "frames": 3},
            {"number": 2, "kind": "AUDIO", "frames": 3,
             "pregap": 1, "pgtype": "VAUDIO"},
            {"number": 3, "kind": "AUDIO", "frames": 2,
             "pregap": 1, "pgtype": "AUDIO"},
        ]
        _write_chd(self.path, self.tracks, hunk_frames=4, logical_frames=10)


class ChdCueLayoutTests(_ChdFixtureMixin, unittest.TestCase):
    def test_track_table_layout_and_cue_bytes(self):
        table = cuebin.read_track_table(self.path)
        layout = table.layout()
        self.assertEqual(layout.size, 9 * cuebin.CD_SECTOR_BYTES)
        self.assertEqual(
            [(span.start, span.frames, span.physical_start, span.audio)
             for span in layout.spans],
            [(0, 3, 0, False), (3, 3, 4, True), (6, 1, -1, True),
             (7, 2, 8, True)])
        self.assertEqual(layout.cue_sheet("Game.bin"), (
            b'FILE "Game.bin" BINARY\r\n'
            b'  TRACK 01 MODE1/2352\r\n'
            b'    INDEX 01 00:00:00\r\n'
            b'  TRACK 02 AUDIO\r\n'
            b'    INDEX 00 00:00:03\r\n'
            b'    INDEX 01 00:00:04\r\n'
            b'  TRACK 03 AUDIO\r\n'
            b'    INDEX 00 00:00:06\r\n'
            b'    INDEX 01 00:00:07\r\n'))

    def test_nonstored_first_track_pregap_is_materialized(self):
        table = cuebin.TrackTable(
            hunk_bytes=4 * cuebin.CD_FRAME_BYTES,
            logical_bytes=4 * cuebin.CD_FRAME_BYTES,
            tracks=(cuebin.Track(1, "AUDIO", 4, 2, "AUDIO", 0),),
        )
        layout = table.layout()
        self.assertEqual(
            [(span.start, span.frames, span.physical_start)
             for span in layout.spans],
            [(0, 2, -1), (2, 4, 0)],
        )
        self.assertEqual(layout.tracks[0].index0, 0)
        self.assertEqual(layout.tracks[0].index1, 2)

    def test_unrepresentable_virtual_names_are_rejected(self):
        self.assertFalse(cuebin._safe_cue_name("x" * 252 + ".bin"))
        self.assertFalse(cuebin._safe_cue_name("\udcff.bin"))

    def test_unsupported_or_malformed_images_are_not_advertised(self):
        unsupported = os.path.join(self.temp.name, "cooked.chd")
        _write_chd(unsupported, [{"number": 1, "kind": "MODE1", "frames": 4}])
        with self.assertRaises(cuebin.ChdCueError):
            cuebin.read_track_table(unsupported)

        truncated = os.path.join(self.temp.name, "truncated.chd")
        with open(truncated, "wb") as image:
            image.write(b"MComprHD" + bytes(20))
        with self.assertRaises(cuebin.ChdCueError):
            cuebin.read_track_table(truncated)

    def test_bad_metadata_chain_is_rejected(self):
        with open(self.path, "r+b") as image:
            image.seek(48)
            image.write(struct.pack(">Q", os.path.getsize(self.path) + 100))
        with self.assertRaises(cuebin.ChdCueError):
            cuebin.read_track_table(self.path)


class ChdCueReadTests(_ChdFixtureMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.lib = _FakeLibchdr(4)
        self.lib_available = mock.patch.object(cuebin._chd, "LIBCHDR_AVAILABLE", True)
        self.lib_handle = mock.patch.object(cuebin._chd, "_lib", self.lib)
        self.lib_available.start()
        self.lib_handle.start()
        self.addCleanup(self.lib_handle.stop)
        self.addCleanup(self.lib_available.stop)

    def test_bin_read_seek_audio_swap_and_eof(self):
        table = cuebin.read_track_table(self.path)
        layout = table.layout()
        image = cuebin.ChdBinFile(self.path, table, layout)
        self.addCleanup(image.close)
        data = image.read(layout.size + 20)
        self.assertEqual(len(data), layout.size)
        expected = bytearray()
        for physical_frame, audio in ((0, False), (1, False), (2, False),
                                      (4, True), (5, True), (6, True),
                                      (None, True), (8, True), (9, True)):
            if physical_frame is None:
                expected.extend(bytes(cuebin.CD_SECTOR_BYTES))
                continue
            sector = bytes((physical_frame, physical_frame + 1)) * (cuebin.CD_SECTOR_BYTES // 2)
            if audio:
                sector = cuebin._swap_audio_bytes(sector)
            expected.extend(sector)
        self.assertEqual(data, bytes(expected))

        image.seek(4 * cuebin.CD_SECTOR_BYTES - 5)
        self.assertEqual(image.read(24), bytes(expected)[4 * cuebin.CD_SECTOR_BYTES - 5:
                                                        4 * cuebin.CD_SECTOR_BYTES + 19])
        image.seek(-17, 2)
        self.assertEqual(image.read(40), bytes(expected)[-17:])
        self.assertEqual(image.read(1), b"")
        with self.assertRaises(ValueError):
            image.seek(-1, 0)
        with self.assertRaises(ValueError):
            image.seek(0, 99)

    def test_close_releases_libchdr_handle(self):
        table = cuebin.read_track_table(self.path)
        image = cuebin.ChdBinFile(self.path, table, table.layout())
        image.close()
        image.close()
        self.assertEqual(self.lib.opened, 1)
        self.assertEqual(self.lib.closed, 1)
        with self.assertRaises(ValueError):
            image.read(1)

    def test_cache_size_zero_disables_caching_and_bounds_hold(self):
        table = cuebin.read_track_table(self.path)
        layout = table.layout()
        uncached = cuebin.ChdBinFile(self.path, table, layout, cache_size=0)
        self.addCleanup(uncached.close)
        uncached.read(cuebin.CD_SECTOR_BYTES)
        self.assertEqual(len(uncached._cache), 0)

        bounded = cuebin.ChdBinFile(self.path, table, layout, cache_size=1)
        self.addCleanup(bounded.close)
        bounded.read(layout.size)
        self.assertEqual(len(bounded._cache), 1)

    def test_server_bread_74_and_75_sector_requests(self):
        path = os.path.join(self.temp.name, "Large.chd")
        _write_chd(path, [{"number": 1, "kind": "MODE2_RAW", "frames": 100}],
                   hunk_frames=4, logical_frames=100)
        table = cuebin.read_track_table(path)
        lib = _FakeLibchdr(4)
        with mock.patch.object(cuebin._chd, "LIBCHDR_AVAILABLE", True), \
                mock.patch.object(cuebin._chd, "_lib", lib):
            stream = cuebin.ChdBinFile(path, table, table.layout())
            server = object.__new__(server_module.UdpfsServer)
            replies = []
            server._local = threading.local()
            server._local.session = types.SimpleNamespace(
                handles={9: server_module.FileHandle(stream)}, last_bread_reply=None)
            server.stats = {"bread": 0, "bytes_read": 0}
            server.max_transfer_bytes = 65536
            server.bd_sector_size = 512
            server.verbose = False
            server._session_record = lambda *args, **kwargs: None
            server._update_status = lambda: None

            def capture(addr, result, data):
                replies.append((result, data))
                return True

            server._send_read_result = capture
            expected = b"".join(
                bytes((frame, frame + 1)) * (cuebin.CD_SECTOR_BYTES // 2)
                for frame in range(100))
            addr = ("127.0.0.1", 62966)
            for count, first_sector in ((74, 0), (75, 74)):
                request = struct.pack("<BBHiII", 0, 0, count, 9, first_sector, 0)
                server._handle_bread(addr, request)
                result, data = replies.pop()
                start = first_sector * 512
                self.assertEqual(result, count * 512)
                self.assertEqual(data, expected[start:start + count * 512])
            self.assertEqual(server.stats["bread"], 2)
            stream.close()
        self.assertEqual(lib.closed, 1)


class ChdCueNamespaceTests(_ChdFixtureMixin, unittest.TestCase):
    def _entries(self):
        return list(os.scandir(self.temp.name))

    def test_pair_listing_collision_and_casefolding(self):
        with mock.patch.object(cuebin, "_AVAILABLE", True):
            entries = cuebin.list_virtual_entries(self.temp.name, self._entries())
            self.assertEqual([name for name, _ in entries], ["Game.cue", "Game.bin"])
            self.assertEqual(
                cuebin.find_virtual_view(os.path.join(self.temp.name, "Game.BIN")).kind,
                "bin")

            with open(os.path.join(self.temp.name, "Game.CUE"), "wb") as real:
                real.write(b"real cue")
            self.assertEqual(cuebin.list_virtual_entries(self.temp.name, self._entries()), [])
            self.assertIsNone(cuebin.find_virtual_view(os.path.join(self.temp.name, "Game.bin")))

    def test_duplicate_chd_stems_and_bad_chd_are_suppressed(self):
        invalid = os.path.join(self.temp.name, "bad.chd")
        with open(invalid, "wb") as image:
            image.write(b"not a CHD")
        entries = self._entries() + [
            types.SimpleNamespace(name="Game.CHD", path=self.path)
        ]
        with mock.patch.object(cuebin, "_AVAILABLE", True):
            listed = cuebin.list_virtual_entries(self.temp.name, entries)
        self.assertEqual(listed, [])

    def test_case_distinct_stems_are_preserved_on_case_sensitive_filesystems(self):
        entries = [
            types.SimpleNamespace(name="Game.chd", path=self.path),
            types.SimpleNamespace(name="game.chd", path=self.path),
        ]
        with mock.patch.object(cuebin, "_AVAILABLE", True), \
                mock.patch.object(cuebin.os, "name", "posix"):
            listed = cuebin.list_virtual_entries(self.temp.name, entries)
        self.assertEqual(
            [name for name, _ in listed],
            ["Game.cue", "Game.bin", "game.cue", "game.bin"],
        )

    def test_virtual_pair_is_not_advertised_when_compression_is_disabled(self):
        with mock.patch.object(cuebin, "_AVAILABLE", True):
            server = object.__new__(server_module.UdpfsServer)
            server.enable_compression = False
            server.root_dir = self.temp.name
            server.verbose = False
            server._encode_time = lambda _timestamp: bytes(8)
            prepared = server._prepare_directory_entries(self._entries())
        names = [entry["name"] for entry in prepared]
        self.assertNotIn("Game.cue", names)
        self.assertNotIn("Game.bin", names)

    def test_generated_cue_is_seekable_and_read_only(self):
        with mock.patch.object(cuebin, "_AVAILABLE", True):
            view = cuebin.find_virtual_view(os.path.join(self.temp.name, "Game.cue"))
        stream = view.open()
        self.addCleanup(stream.close)
        expected = view.cue_data
        self.assertEqual(stream.read(12), expected[:12])
        stream.seek(0)
        self.assertEqual(stream.read(), expected)
        self.assertFalse(hasattr(stream, "write"))

    def test_server_open_getstat_dread_and_write_protection(self):
        lib = _FakeLibchdr(4)
        patches = [
            mock.patch.object(cuebin, "_AVAILABLE", True),
            mock.patch.object(cuebin._chd, "LIBCHDR_AVAILABLE", True),
            mock.patch.object(cuebin._chd, "_lib", lib),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

        with open(os.path.join(self.temp.name, "A_bad.chd"), "wb") as invalid:
            invalid.write(b"not a CHD")
        with open(os.path.join(self.temp.name, "Z_after.txt"), "wb") as later:
            later.write(b"visible after invalid CHD")

        server = object.__new__(server_module.UdpfsServer)
        server.root_dir = self.temp.name
        server.enable_compression = True
        server.read_only = False
        server.verbose = False
        server.compression_cache_size = 4
        server.max_transfer_bytes = 65536
        server.stats = {"open": 0, "getstat": 0, "dread": 0, "read": 0,
                        "bytes_read": 0, "close": 0}
        server._local = threading.local()
        server._local.session = types.SimpleNamespace(handles={}, next_handle=1)
        server._print_event = lambda _message: None
        server._session_record = lambda *args, **kwargs: None
        server._update_status = lambda: None
        server._send_getstat_reply = lambda addr, result, stat_info=None: replies.append(
            ("getstat", result, stat_info))
        server._send_open_reply = lambda addr, result, stat_info=None: replies.append(
            ("open", result, stat_info))
        server._send_read_result = lambda addr, result, data: replies.append(
            ("read", result, data))
        server._send_close_reply = lambda addr, result: replies.append(("close", result))
        server._send_dread_reply = lambda addr, result, name=None, stat_info=None: replies.append(
            ("dread", result, name, stat_info))
        replies = []
        addr = ("127.0.0.1", 62966)

        getstat = lambda name: server._handle_getstat(
            addr, bytes(4) + name.encode("utf-8") + b"\0")
        getstat("Game.cue")
        cue_stat = replies.pop()
        self.assertEqual(cue_stat[1], 0)
        self.assertEqual(cue_stat[2]["size"], len(cuebin.read_track_table(self.path).layout().cue_sheet("Game.bin")))
        getstat("Game.bin")
        bin_stat = replies.pop()
        self.assertEqual(bin_stat[1], 0)
        self.assertEqual(bin_stat[2]["size"], 9 * cuebin.CD_SECTOR_BYTES)

        # Missing parent folders do not raise OSError and reply with ENOENT.
        getstat("missing/dir/Game.bin")
        self.assertEqual(replies.pop()[1], -errno.ENOENT)
        server._handle_open(addr, struct.pack("<BBHi", 0, 0, 1, 0) + b"missing/dir/Game.bin\0")
        self.assertEqual(replies.pop()[1], -errno.ENOENT)

        # A writable server still refuses write access to the virtual views.
        server._handle_open(addr, struct.pack("<BBHi", 0, 0, 2, 0) + b"Game.bin\0")
        self.assertEqual(replies.pop()[1], -errno.EACCES)

        # Read access uses the virtual stream and ordinary BREAD-compatible
        # seeking. The generated aliases appear in the same directory listing.
        server._handle_open(addr, struct.pack("<BBHi", 0, 0, 1, 0) + b"Game.bin\0")
        opened = replies.pop()
        self.assertEqual(opened[0:2], ("open", 1))
        server._handle_read(addr, struct.pack("<BBBBiI", 0, 0, 0, 0, 1, 16))
        self.assertEqual(replies.pop()[2], bytes((0, 1)) * 8)

        server._handle_open(addr, struct.pack("<BBHi", 0, 1, 1, 0) + b"\0")
        dir_handle = replies.pop()[1]
        emitted = []
        for _ in range(20):
            server._handle_dread(addr, struct.pack("<BBBBi", 0, 0, 0, 0, dir_handle))
            entry = replies.pop()
            if entry[1] == 0:
                break
            emitted.append(entry[2])
        listed = [entry["name"] for entry in server._local.session.handles[dir_handle].obj["entries"]
                  if isinstance(entry, dict)]
        self.assertIn("Game.cue", listed)
        self.assertIn("Game.bin", listed)
        self.assertIn("Game.cue", emitted)
        self.assertIn("Game.bin", emitted)
        self.assertIn("Z_after.txt", emitted)
        server._handle_close(addr, struct.pack("<BBBBi", 0, 0, 0, 0, dir_handle))
        replies.pop()

        server._handle_close(addr, struct.pack("<BBBBi", 0, 0, 0, 0, 1))
        self.assertEqual(replies.pop(), ("close", 0))

        # A real CUE takes precedence over the generated pair, including its
        # BIN sibling: opening the real name returns its stored bytes.
        with open(os.path.join(self.temp.name, "Game.cue"), "wb") as real:
            real.write(b"real cue")
        getstat("Game.cue")
        self.assertEqual(replies.pop()[2]["size"], 8)
        server._handle_open(addr, struct.pack("<BBHi", 0, 0, 1, 0) + b"Game.cue\0")
        real_open = replies.pop()
        self.assertEqual(real_open[1], 3)
        server._handle_read(addr, struct.pack("<BBBBiI", 0, 0, 0, 0, 3, 32))
        self.assertEqual(replies.pop()[2], b"real cue")
        getstat("Game.bin")
        self.assertEqual(replies.pop()[1], -errno.ENOENT)
        server._handle_close(addr, struct.pack("<BBBBi", 0, 0, 0, 0, 3))
        replies.pop()
        self.assertEqual(lib.closed, 1)


if __name__ == "__main__":
    unittest.main()
