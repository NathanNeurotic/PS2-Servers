"""What libsmb2 needs from the SMB2 server, over real sockets.

RiptOPL's and Launchstation's SMB2 browse drivers (smb2man.irx) are built on
libsmb2. Each case here is one thing libsmb2 does that the rest of the suite's
client never did, and that failed a whole PS2-side feature when the server
lacked it:

  * listing asks for FileIdFullDirectoryInformation (0x26) and nothing else;
  * every stat sends FileAttributeTagInformation (0x23) beside
    FileAllInformation and keeps the first failure;
  * stat, unlink and truncate are related CREATE + ... + CLOSE compounds that
    use the all-ones FileId, and libsmb2 holds a request back until it has the
    credits for it, so a server that grants one credit at a time stalls it;
  * the share picker lists shares through IPC$ and the srvsvc pipe.

conformance/integration/libsmb2/run.py runs the real library; these pin the
same behaviour without a network fetch or a C compiler.

Run:  python -m unittest tests.test_smb2_libsmb2_compat -v
"""

import os
import struct
import unittest

from tests.test_smb2_server import (READ_ONLY, Client, ServerFixture,  # noqa: F401
                                    wire)

import smb2_srvsvc as srvsvc

COMPOUND_FID = b"\xff" * 16


def _hdr(c, command, flags=0, next_command=0, credits=1):
    hdr = wire.pack_header(command, 0, c.message_id, tree_id=c.tree_id,
                           session_id=c.session_id, credits=credits,
                           next_command=next_command)
    # A request: no server-to-redir flag, and the caller's own flags.
    hdr = hdr[:16] + struct.pack("<I", flags) + hdr[20:]
    c.message_id += 1
    return hdr


def _create_body(name, access=0x00120089, disposition=wire.FILE_OPEN, options=0):
    raw = name.encode("utf-16-le")
    return struct.pack("<HBBIQQIIIIIHHII", 57, 0, 0, 2, 0, 0, access, 0, 7,
                       disposition, options, wire.HEADER_SIZE + 56, len(raw),
                       0, 0) + raw


def _query_info_body(fid, info_class, info_type=wire.INFO_TYPE_FILE):
    return struct.pack("<HBBIHHIII", 41, info_type, info_class, 65536,
                       0, 0, 0, 0, 0) + fid


def _close_body(fid):
    return struct.pack("<HHI", 24, 0, 0) + fid


def _chain(parts):
    """Join (header, body) pairs into one compound, patching NextCommand."""
    out = b""
    for i, (hdr, body) in enumerate(parts):
        msg = hdr + body
        if i != len(parts) - 1:
            aligned = (len(msg) + 7) & ~7
            msg = msg[:20] + struct.pack("<I", aligned) + msg[24:]
            msg += b"\x00" * (aligned - len(msg))
        out += msg
    return out


def _split(reply):
    """The individual (header, body) replies in a compound reply."""
    out = []
    at = 0
    while True:
        head = wire.parse_header(reply[at:])
        end = at + head.next_command if head.next_command else len(reply)
        out.append((head, reply[at + wire.HEADER_SIZE:end]))
        if not head.next_command:
            return out
        at = end


class LibSmb2Listing(ServerFixture):
    def test_file_id_full_directory_information_lists_names_and_ids(self):
        c = self.connected()
        _status, fid = c.create("", options=wire.FILE_DIRECTORY_FILE)
        buffers = c.query_directory_raw(
            fid, info_class=wire.FILE_ID_FULL_DIRECTORY_INFORMATION)
        names = {}
        for blob in buffers:
            at = 0
            while True:
                nxt = struct.unpack_from("<I", blob, at)[0]
                size = struct.unpack_from("<q", blob, at + 40)[0]
                name_len = struct.unpack_from("<I", blob, at + 60)[0]
                file_id = struct.unpack_from("<Q", blob, at + 72)[0]
                name = blob[at + 80:at + 80 + name_len].decode("utf-16-le")
                names[name] = (size, file_id)
                if not nxt:
                    break
                self.assertEqual(nxt % 8, 0, "unaligned NextEntryOffset")
                at += nxt
        self.assertEqual(names["game.iso"][0], 4096)
        self.assertEqual(names["game.iso"][1],
                         os.stat(os.path.join(self.dir, "game.iso")).st_ino)
        self.assertIn("APPS", names)


class LibSmb2Stat(ServerFixture):
    def test_attribute_tag_information_is_answered(self):
        c = self.connected()
        _status, fid = c.create("game.iso")
        status, data = c.query_info(fid, wire.INFO_TYPE_FILE,
                                    wire.FILE_ATTRIBUTE_TAG_INFORMATION)
        self.assertEqual(status, 0)
        attrs, tag = struct.unpack("<II", data)
        self.assertEqual(attrs & wire.ATTR_DIRECTORY, 0)
        self.assertEqual(tag, 0)

    def test_a_related_stat_compound_is_answered_in_full(self):
        """CREATE + QUERY_INFO + CLOSE with the all-ones FileId, as libsmb2 sends."""
        c = self.connected()
        msg = _chain([
            (_hdr(c, wire.CMD_CREATE, credits=8), _create_body("game.iso")),
            (_hdr(c, wire.CMD_QUERY_INFO, flags=wire.FLAG_RELATED),
             _query_info_body(COMPOUND_FID, wire.FILE_ALL_INFORMATION)),
            (_hdr(c, wire.CMD_CLOSE, flags=wire.FLAG_RELATED), _close_body(COMPOUND_FID)),
        ])
        wire.send_msg(c.sock, msg)
        replies = _split(wire.recv_msg(c.sock))
        self.assertEqual([h.command for h, _ in replies],
                         [wire.CMD_CREATE, wire.CMD_QUERY_INFO, wire.CMD_CLOSE])
        self.assertEqual([h.status for h, _ in replies], [0, 0, 0])
        off, length = struct.unpack_from("<HI", replies[1][1], 2)
        info = replies[1][1][off - wire.HEADER_SIZE:off - wire.HEADER_SIZE + length]
        # FileStandardInformation.EndOfFile follows the 40-byte basic block.
        self.assertEqual(struct.unpack_from("<q", info, 48)[0], 4096)
        # The CLOSE closed the file the CREATE opened, not nothing.
        real_fid = replies[0][1][64:80]
        head, _ = c.call(wire.CMD_QUERY_INFO,
                         _query_info_body(real_fid, wire.FILE_ALL_INFORMATION))
        self.assertEqual(head.status, wire.STATUS_FILE_CLOSED)

    def test_a_failed_create_fails_the_rest_of_its_compound(self):
        c = self.connected()
        msg = _chain([
            (_hdr(c, wire.CMD_CREATE), _create_body("missing.iso")),
            (_hdr(c, wire.CMD_QUERY_INFO, flags=wire.FLAG_RELATED),
             _query_info_body(COMPOUND_FID, wire.FILE_ALL_INFORMATION)),
            (_hdr(c, wire.CMD_CLOSE, flags=wire.FLAG_RELATED), _close_body(COMPOUND_FID)),
        ])
        wire.send_msg(c.sock, msg)
        replies = _split(wire.recv_msg(c.sock))
        self.assertEqual([h.status for h, _ in replies],
                         [wire.STATUS_OBJECT_NAME_NOT_FOUND] * 3)

    def test_the_all_ones_file_id_means_nothing_outside_a_compound(self):
        c = self.connected()
        head, _ = c.call(wire.CMD_QUERY_INFO,
                         _query_info_body(COMPOUND_FID, wire.FILE_ALL_INFORMATION))
        self.assertEqual(head.status, wire.STATUS_FILE_CLOSED)


class Credits(ServerFixture):
    def test_a_client_that_asks_for_credits_gets_them(self):
        c = self.connected()
        wire.send_msg(c.sock, _hdr(c, wire.CMD_ECHO, credits=64) + struct.pack("<HH", 4, 0))
        head = wire.parse_header(wire.recv_msg(c.sock))
        self.assertEqual(head.status, 0)
        self.assertGreater(head.credits, 1)

    def test_every_reply_grants_at_least_one(self):
        c = self.connected()
        for _ in range(3):
            wire.send_msg(c.sock, _hdr(c, wire.CMD_ECHO, credits=0) + struct.pack("<HH", 4, 0))
            head = wire.parse_header(wire.recv_msg(c.sock))
            self.assertGreaterEqual(head.credits, 1)

    def test_the_total_held_is_bounded(self):
        c = self.connected()
        held = 0
        for _ in range(40):
            wire.send_msg(c.sock, _hdr(c, wire.CMD_ECHO, credits=0xFFFF)
                          + struct.pack("<HH", 4, 0))
            held += wire.parse_header(wire.recv_msg(c.sock)).credits - 1
        self.assertLessEqual(held, 600)


class ReadOnlyHandles(ServerFixture):
    def test_a_handle_opened_to_read_cannot_write(self):
        c = self.connected()
        _status, fid = c.create("game.iso", access=READ_ONLY)
        status, _n = c.write(fid, 0, b"XXXX")
        self.assertEqual(status, wire.STATUS_ACCESS_DENIED)
        with open(os.path.join(self.dir, "game.iso"), "rb") as f:
            self.assertEqual(f.read(4), bytes((i * 7) & 0xFF for i in range(4)))

    def test_a_handle_opened_to_read_cannot_resize(self):
        c = self.connected()
        _status, fid = c.create("game.iso", access=READ_ONLY)
        body = (struct.pack("<HBBIHHI", 33, wire.INFO_TYPE_FILE,
                            wire.FILE_END_OF_FILE_INFORMATION, 8,
                            wire.HEADER_SIZE + 32, 0, 0)
                + fid + struct.pack("<q", 0))
        head, _ = c.call(wire.CMD_SET_INFO, body)
        self.assertEqual(head.status, wire.STATUS_ACCESS_DENIED)
        self.assertEqual(os.path.getsize(os.path.join(self.dir, "game.iso")), 4096)


def _bind(call_id=1, contexts=None):
    if contexts is None:
        contexts = [(0, srvsvc.SRVSVC_SYNTAX, [srvsvc.NDR32_SYNTAX])]
    body = struct.pack("<HHIB3x", 4280, 4280, 0, len(contexts))
    for ctx_id, abstract, syntaxes in contexts:
        body += struct.pack("<HBx", ctx_id, len(syntaxes)) + abstract + b"".join(syntaxes)
    return srvsvc._header(srvsvc.PTYPE_BIND, 3, call_id, len(body)) + body


def _share_enum(level=1, call_id=2, opnum=srvsvc.OPNUM_NETR_SHARE_ENUM):
    server = srvsvc._ndr_string("\\\\127.0.0.1")
    stub = (struct.pack("<I", 0x20000) + server
            + struct.pack("<IIII", level, level, 0x20004, 0)  # level, switch, ctr, count
            + struct.pack("<I", 0)                            # buffer NULL
            + struct.pack("<I", 0xFFFFFFFF)                   # PreferedMaximumLength
            + struct.pack("<I", 0))                           # ResumeHandle NULL
    body = struct.pack("<IHH", len(stub), 0, opnum) + stub
    return srvsvc._header(srvsvc.PTYPE_REQUEST, 3, call_id, len(body)) + body


def _parse_level1(response):
    """Share names and types from a NetrShareEnum level-1 response PDU."""
    stub = response[24:]
    level, _sw, _ref, count, _bref, max_count = struct.unpack_from("<IIIIII", stub, 0)
    assert level == 1 and count == max_count
    at = 24
    types = []
    for _ in range(count):
        _name_ref, stype, _remark_ref = struct.unpack_from("<III", stub, at)
        types.append(stype)
        at += 12
    names = []
    for i in range(count * 2):
        _mx, _off, actual = struct.unpack_from("<III", stub, at)
        at += 12
        text = stub[at:at + actual * 2].decode("utf-16-le").rstrip("\x00")
        at += (actual * 2 + 3) & ~3
        if i % 2 == 0:
            names.append(text)
    werror = struct.unpack_from("<I", stub, len(stub) - 4)[0]
    assert werror == 0
    return list(zip(names, types))


class ShareList(ServerFixture):
    def _ipc(self):
        c = self.connected()
        self.assertEqual(c.tree_connect("IPC$"), 0)
        status, fid = c.create("srvsvc", access=0x0012019F)
        self.assertEqual(status, 0)
        return c, fid

    def _transceive(self, c, fid, data, max_out=65536):
        off = wire.HEADER_SIZE + 56
        body = struct.pack("<HHI16sIIIIIIII", 57, 0, wire.FSCTL_PIPE_TRANSCEIVE, fid,
                           off, len(data), 0, off, 0, max_out, 1, 0) + data
        head, reply = c.call(wire.CMD_IOCTL, body)
        out_off, out_len = struct.unpack_from("<II", reply, 32)
        start = out_off - wire.HEADER_SIZE
        return head.status, reply[start:start + out_len]

    def test_ioctl_transceive_lists_the_shares(self):
        c, fid = self._ipc()
        status, ack = self._transceive(c, fid, _bind())
        self.assertEqual(status, 0)
        self.assertEqual(ack[2], srvsvc.PTYPE_BIND_ACK)
        status, resp = self._transceive(c, fid, _share_enum())
        self.assertEqual(status, 0)
        self.assertEqual(resp[2], srvsvc.PTYPE_RESPONSE)
        shares = _parse_level1(resp)
        self.assertIn(("games", srvsvc.STYPE_DISKTREE), shares)
        self.assertIn(("IPC$", srvsvc.STYPE_IPC | srvsvc.STYPE_SPECIAL), shares)
        self.assertEqual(c.close_handle(fid), 0)

    def test_write_then_read_lists_the_shares(self):
        """Windows binds with WRITE + READ before it transceives."""
        c, fid = self._ipc()
        status, n = c.write(fid, 0, _bind())
        self.assertEqual(status, 0)
        status, ack = c.read(fid, 0, 4096)
        self.assertEqual(status, 0)
        self.assertEqual(ack[2], srvsvc.PTYPE_BIND_ACK)
        c.write(fid, 0, _share_enum())
        status, resp = c.read(fid, 0, 4096)
        self.assertEqual(status, 0)
        self.assertIn("games", [n for n, _t in _parse_level1(resp)])

    def test_a_small_ioctl_buffer_leaves_the_rest_for_read(self):
        c, fid = self._ipc()
        self._transceive(c, fid, _bind())
        status, first = self._transceive(c, fid, _share_enum(), max_out=40)
        self.assertEqual(status, wire.STATUS_BUFFER_OVERFLOW)
        self.assertEqual(len(first), 40)
        status, rest = c.read(fid, 0, 4096)
        self.assertEqual(status, 0)
        self.assertIn("games", [n for n, _t in _parse_level1(first + rest)])

    def test_an_unknown_operation_is_a_fault_not_a_guess(self):
        c, fid = self._ipc()
        self._transceive(c, fid, _bind())
        _status, resp = self._transceive(c, fid, _share_enum(opnum=16))
        self.assertEqual(resp[2], srvsvc.PTYPE_FAULT)
        self.assertEqual(struct.unpack_from("<I", resp, 24)[0], srvsvc.NCA_OP_RNG_ERROR)

    def test_an_ndr64_only_bind_is_refused(self):
        c, fid = self._ipc()
        ndr64 = bytes(16) + struct.pack("<I", 1)
        _status, ack = self._transceive(c, fid, _bind(contexts=[
            (0, srvsvc.SRVSVC_SYNTAX, [ndr64])]))
        # One result, provider rejection, after the secondary address.
        sec_len = struct.unpack_from("<H", ack, 24)[0]
        at = (26 + sec_len + 3) & ~3
        self.assertEqual(ack[at], 1)
        self.assertEqual(struct.unpack_from("<H", ack, at + 4)[0],
                         srvsvc.RESULT_PROVIDER_REJECTION)

    def test_other_pipes_are_not_found(self):
        c = self.connected()
        c.tree_connect("IPC$")
        status, _fid = c.create("lsarpc")
        self.assertEqual(status, wire.STATUS_OBJECT_NAME_NOT_FOUND)

    def test_ipc_is_not_a_folder(self):
        c = self.connected()
        c.tree_connect("IPC$")
        _status, fid = c.create("srvsvc")
        raw = "*".encode("utf-16-le")
        body = (struct.pack("<HBBI", 33, wire.FILE_BOTH_DIRECTORY_INFORMATION, 1, 0)
                + fid + struct.pack("<HHI", wire.HEADER_SIZE + 32, len(raw), 65536) + raw)
        head, _ = c.call(wire.CMD_QUERY_DIRECTORY, body)
        self.assertNotEqual(head.status, 0)


if __name__ == "__main__":
    unittest.main()
