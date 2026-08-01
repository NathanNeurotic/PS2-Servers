"""The SMB2 structure sizes, pinned.

Every SMB2 body begins with a StructureSize the client checks before reading
anything else, and it is the fixed part of the body plus one wherever a variable
buffer follows. Get it wrong and a real client does not report an error -- it
stops talking, which is indistinguishable from a hang.

The end-to-end tests in test_smb2_server.py cannot catch that class of mistake
on their own: the client there is this project's own reading of the protocol, so
a field that is the wrong width in both places round-trips perfectly. These
tests are the counterweight. They record the sizes MS-SMB2 states, so a later
refactor cannot quietly move one.

Stated plainly: these are transcribed from the protocol as implemented here, not
re-derived from the specification document in this environment. They pin against
drift, not against a mistake made once and written down twice. The only complete
check is a real client, which needs port 445.

Run:  python -m unittest tests.test_smb2_wire -v
"""

import os
import struct
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "smb2_server"))

import smb2_wire as wire        # noqa: E402


class FakeStat:
    st_mode = 0o100644
    st_size = 4096
    st_ino = 1234
    st_ctime = 1700000000
    st_atime = 1700000001
    st_mtime = 1700000002


class Header(unittest.TestCase):
    def test_the_header_is_64_bytes(self):
        raw = wire.pack_header(wire.CMD_NEGOTIATE, 0, 1)
        self.assertEqual(len(raw), 64)
        self.assertEqual(wire.HEADER_SIZE, 64)

    def test_it_round_trips(self):
        raw = wire.pack_header(wire.CMD_READ, wire.STATUS_END_OF_FILE, 42,
                               tree_id=7, session_id=0x1122334455667788)
        hdr = wire.parse_header(raw)
        self.assertEqual(hdr.command, wire.CMD_READ)
        self.assertEqual(hdr.status, wire.STATUS_END_OF_FILE)
        self.assertEqual(hdr.message_id, 42)
        self.assertEqual(hdr.tree_id, 7)
        self.assertEqual(hdr.session_id, 0x1122334455667788)

    def test_a_reply_is_marked_as_one(self):
        hdr = wire.parse_header(wire.pack_header(wire.CMD_ECHO, 0, 1))
        self.assertTrue(hdr.flags & wire.FLAG_SERVER_TO_REDIR)

    def test_anything_that_is_not_smb2_is_not_a_header(self):
        self.assertIsNone(wire.parse_header(b"\xffSMB" + b"\x00" * 60))
        self.assertIsNone(wire.parse_header(b"too short"))

    def test_the_error_body_carries_its_one_byte_of_error_data(self):
        """StructureSize 9 means 8 fixed bytes and a byte of buffer.

        An 8-byte error body is malformed, which is a different failure from the
        one being reported and is the harder of the two to find.
        """
        body = wire.error_body()
        self.assertEqual(struct.unpack_from("<H", body, 0)[0], 9)
        self.assertEqual(len(body), 9)


class Framing(unittest.TestCase):
    def test_the_length_prefix_is_the_one_big_endian_field(self):
        class FakeSock:
            def __init__(self):
                self.sent = b""

            def sendall(self, data):
                self.sent += data

        sock = FakeSock()
        wire.send_msg(sock, b"x" * 0x010203)
        self.assertEqual(sock.sent[:4], b"\x00\x01\x02\x03")


class Dialects(unittest.TestCase):
    def test_the_best_common_dialect_wins(self):
        self.assertEqual(
            wire.pick_dialect([wire.DIALECT_202, wire.DIALECT_210], wire.CEILING_SMB3),
            wire.DIALECT_210)

    def test_the_ceiling_holds(self):
        offered = [wire.DIALECT_202, wire.DIALECT_210, wire.DIALECT_300,
                   wire.DIALECT_302]
        self.assertEqual(wire.pick_dialect(offered, wire.CEILING_SMB2),
                         wire.DIALECT_210)
        self.assertEqual(wire.pick_dialect(offered, wire.CEILING_SMB3),
                         wire.DIALECT_302)

    def test_311_is_never_chosen(self):
        """It needs negotiate contexts. Claiming it without them is refused by
        Windows outright, so answering 3.0.2 is what actually connects."""
        self.assertEqual(
            wire.pick_dialect([wire.DIALECT_311], wire.CEILING_SMB3), None)
        self.assertEqual(
            wire.pick_dialect([wire.DIALECT_311, wire.DIALECT_302],
                              wire.CEILING_SMB3),
            wire.DIALECT_302)

    def test_nothing_in_common_is_none_not_a_guess(self):
        self.assertIsNone(wire.pick_dialect([0x0999], wire.CEILING_SMB3))
        self.assertIsNone(wire.pick_dialect([], wire.CEILING_SMB3))


class FileInformationSizes(unittest.TestCase):
    """Fixed record sizes. A client reads these by offset, not by parsing."""

    def test_file_classes(self):
        st = FakeStat()
        for info_class, size in (
                (wire.FILE_BASIC_INFORMATION, 40),
                (wire.FILE_STANDARD_INFORMATION, 24),
                (wire.FILE_INTERNAL_INFORMATION, 8),
                (wire.FILE_EA_INFORMATION, 4),
                (wire.FILE_ACCESS_INFORMATION, 4),
                (wire.FILE_POSITION_INFORMATION, 8),
                (wire.FILE_NETWORK_OPEN_INFORMATION, 56)):
            with self.subTest(info_class=info_class):
                self.assertEqual(len(wire.file_info(info_class, st)), size)

    def test_file_all_information_is_its_parts(self):
        st = FakeStat()
        blob = wire.file_info(wire.FILE_ALL_INFORMATION, st, "\\game.iso")
        name = "\\game.iso".encode("utf-16-le")
        self.assertEqual(len(blob), 40 + 24 + 8 + 4 + 4 + 8 + 4 + 4 + 4 + len(name))

    def test_an_unencodable_class_is_none_not_an_empty_record(self):
        """None makes the caller answer NOT_SUPPORTED. An empty record would be
        read by the client as the class it asked for, with every field zero."""
        self.assertIsNone(wire.file_info(0x7F, FakeStat()))
        self.assertIsNone(wire.fs_info(0x7F, "."))

    def test_directory_entry_classes(self):
        st = FakeStat()
        name = "game.iso"
        encoded = len(name.encode("utf-16-le"))
        for info_class, fixed in (
                (wire.FILE_DIRECTORY_INFORMATION, 64),
                (wire.FILE_FULL_DIRECTORY_INFORMATION, 68),
                (wire.FILE_BOTH_DIRECTORY_INFORMATION, 94),
                (wire.FILE_ID_BOTH_DIRECTORY_INFORMATION, 104),
                (wire.FILE_NAMES_INFORMATION, 12)):
            with self.subTest(info_class=info_class):
                record = wire.directory_entry(name, st, info_class)
                self.assertEqual(len(record), fixed + encoded)

    def test_the_name_length_field_is_bytes_not_characters(self):
        st = FakeStat()
        record = wire.directory_entry("ab", st, wire.FILE_BOTH_DIRECTORY_INFORMATION)
        self.assertEqual(struct.unpack_from("<I", record, 60)[0], 4)

    def test_filesystem_classes(self):
        for info_class, size in ((wire.FS_SIZE_INFORMATION, 24),
                                 (wire.FS_FULL_SIZE_INFORMATION, 32),
                                 (wire.FS_DEVICE_INFORMATION, 8)):
            with self.subTest(info_class=info_class):
                self.assertEqual(len(wire.fs_info(info_class, ROOT)), size)

    def test_the_filesystem_is_not_advertised_as_case_sensitive(self):
        """Windows shares are not. Claiming otherwise makes a client treat
        Game.iso and game.iso as two files where the disk has one."""
        blob = wire.fs_info(wire.FS_ATTRIBUTE_INFORMATION, ROOT)
        attrs = struct.unpack_from("<I", blob, 0)[0]
        FILE_CASE_SENSITIVE_SEARCH = 0x00000001
        FILE_CASE_PRESERVED_NAMES = 0x00000002
        self.assertTrue(attrs & FILE_CASE_PRESERVED_NAMES)
        self.assertFalse(attrs & FILE_CASE_SENSITIVE_SEARCH)


class Attributes(unittest.TestCase):
    def test_a_directory_is_marked_as_one(self):
        class Dir(FakeStat):
            st_mode = 0o040755
        self.assertTrue(wire.attributes_for(Dir()) & wire.ATTR_DIRECTORY)
        self.assertFalse(wire.attributes_for(FakeStat()) & wire.ATTR_DIRECTORY)

    def test_read_only_is_carried_to_the_client(self):
        self.assertTrue(
            wire.attributes_for(FakeStat(), read_only=True) & wire.ATTR_READONLY)
        self.assertFalse(
            wire.attributes_for(FakeStat()) & wire.ATTR_READONLY)

    def test_filetime_is_1601_based(self):
        # 1970-01-01 is exactly the epoch delta, and 0 stays 0 rather than
        # becoming 1601, which a client would display as a real date.
        self.assertEqual(wire.to_filetime(0), 0)
        self.assertEqual(wire.to_filetime(1), 116444736000000000 + 10_000_000)


if __name__ == "__main__":
    unittest.main()
