"""The NFS version 4.0 server, driven the way unfs4 drives it.

unfs4 (the client inside Neutrino's NFS backing store and the nfs: driver)
reads replies at fixed offsets and has no slack: a reply that is valid NFS but
laid out differently, or larger than it asked for, is read wrong or overruns
an IOP buffer. So besides each operation's behaviour these tests pin the
layout itself -- where the status sits, how long each result is, exactly which
attributes come back -- and conformance/integration/neutrino_nfs runs the real
client against the server on top of that.

The requests are built here, independently of the server's own XDR code, so a
mistake there cannot hide in both halves.

Run:  python -m unittest tests.test_nfs4_server -v
"""

import os
import shutil
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "nfs4_server"))
sys.path.insert(0, ROOT)

import nfs4_server as nfs  # noqa: E402


def p32(v):
    return struct.pack(">I", v)


def p64(v):
    return struct.pack(">Q", v)


def opaque(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    return p32(len(data)) + data + b"\0" * (-len(data) & 3)


def bitmap(bits):
    words = [0] * ((max(bits) // 32 + 1) if bits else 0)
    for b in bits:
        words[b // 32] |= 1 << (b % 32)
    return p32(len(words)) + b"".join(p32(w) for w in words)


ATTRS = [1, 4, 53]          # TYPE, SIZE, TIME_MODIFY: all unfs4 asks for
ZERO_STATEID = b"\0" * 16


# -- operations, as unfs4 encodes them ---------------------------------------

def putrootfh():
    return p32(24)


def putfh(fh):
    return p32(22) + opaque(fh)


def lookup(name):
    return p32(15) + opaque(name)


def getfh():
    return p32(10)


def getattr_(bits=ATTRS):
    return p32(9) + bitmap(bits)


def read(offset, count):
    return p32(25) + ZERO_STATEID + p64(offset) + p32(count)


def readdir(cookie=0, maxcount=1032, dircount=1024, bits=ATTRS):
    return p32(26) + p64(cookie) + b"\0" * 8 + p32(dircount) + p32(maxcount) + bitmap(bits)


def savefh():
    return p32(32)


def setclientid(name=b"ps2 test"):
    return (p32(35) + b"\0" * 8 + opaque(name) + p32(0) + opaque(b"tcp")
            + opaque(b"0.0.0.0.0.0") + p32(0))


def setclientid_confirm(clientid, confirm):
    return p32(36) + p64(clientid) + confirm


def mode_attr(mode=0o644):
    return bitmap([33]) + opaque(p32(mode))


def open_(name, create=True, how=0, attrs=None, access=2, clientid=1):
    body = p32(18) + p32(0) + p32(access) + p32(0) + p64(clientid) + opaque(b"owner")
    if create:
        body += p32(1) + p32(how) + (attrs if attrs is not None else mode_attr())
    else:
        body += p32(0)
    return body + p32(0) + opaque(name)


def close(stateid, seqid=1):
    return p32(4) + p32(seqid) + stateid


def write(offset, data):
    return p32(38) + ZERO_STATEID + p64(offset) + p32(2) + opaque(data)


def setattr_size(size):
    return p32(34) + ZERO_STATEID + bitmap([4]) + opaque(p64(size))


def mkdir(name):
    return p32(6) + p32(2) + opaque(name) + mode_attr(0o755)


def remove(name):
    return p32(28) + opaque(name)


def rename(old, new):
    return p32(29) + opaque(old) + opaque(new)


def rpc_call(body, xid=7, prog=100003, vers=4, proc=1, rpcvers=2, flavor=1):
    cred = p32(0) + opaque(b"") + p32(0) + p32(0) + p32(0) if flavor == 1 else b""
    return (p32(xid) + p32(0) + p32(rpcvers) + p32(prog) + p32(vers) + p32(proc)
            + p32(flavor) + opaque(cred) + p32(0) + opaque(b"") + body)


def compound(ops, tag=b"", minor=0, count=None):
    return (opaque(tag) + p32(minor) + p32(len(ops) if count is None else count)
            + b"".join(ops))


# -- replies -----------------------------------------------------------------

class Reader(object):
    def __init__(self, data):
        self.data, self.pos = data, 0

    def u32(self):
        v = struct.unpack(">I", self.data[self.pos:self.pos + 4])[0]
        self.pos += 4
        return v

    def u64(self):
        v = struct.unpack(">Q", self.data[self.pos:self.pos + 8])[0]
        self.pos += 8
        return v

    def fixed(self, n):
        chunk = self.data[self.pos:self.pos + n]
        self.pos += n + (-n & 3)
        return chunk

    def opaque(self):
        return self.fixed(self.u32())

    def bitmap(self):
        words = [self.u32() for _ in range(self.u32())]
        return {w * 32 + b for w, word in enumerate(words) for b in range(32) if word >> b & 1}


def attrs_of(r):
    bits = r.bitmap()
    values = Reader(r.opaque())
    out = {}
    for bit in sorted(bits):
        if bit == 1:
            out["type"] = values.u32()
        elif bit == 4:
            out["size"] = values.u64()
        elif bit == 53:
            out["mtime"] = (values.u64(), values.u32())
    assert values.pos == len(values.data)
    return out


def result_body(op, r):
    if op in (24, 22, 15, 32, 36):
        return None
    if op == 10:
        return r.opaque()
    if op == 9:
        return attrs_of(r)
    if op == 25:
        eof = r.u32()
        return {"eof": eof, "data": r.opaque()}
    if op == 26:
        verifier, entries = r.fixed(8), []
        while r.u32():
            cookie, name = r.u64(), r.opaque()
            entries.append((cookie, name, attrs_of(r)))
        return {"verifier": verifier, "entries": entries, "eof": r.u32()}
    if op == 35:
        return {"clientid": r.u64(), "confirm": r.fixed(8)}
    if op == 18:
        stateid, _cinfo, rflags = r.fixed(16), r.fixed(20), r.u32()
        attrset = r.bitmap()
        assert r.u32() == 0  # no delegation
        return {"stateid": stateid, "rflags": rflags, "attrset": attrset}
    if op in (4, 20):
        return r.fixed(16)
    if op == 38:
        return {"count": r.u32(), "committed": r.u32(), "verifier": r.fixed(8)}
    if op == 34:
        return r.bitmap()
    if op == 6:
        r.fixed(20)
        return r.bitmap()
    if op == 28:
        return r.fixed(20)
    if op == 29:
        return r.fixed(40)
    raise AssertionError("no parser for op %d" % op)


class Reply(object):
    def __init__(self, raw):
        self.raw = raw
        r = Reader(raw)
        self.xid, self.msg_type, self.reply_stat = r.u32(), r.u32(), r.u32()
        if self.reply_stat != 0:
            self.denied = [r.u32() for _ in range((len(raw) - r.pos) // 4)]
            return
        self.verifier = (r.u32(), r.opaque())
        self.accept_stat = r.u32()
        self.tail = raw[r.pos:]
        if self.accept_stat != 0 or not self.tail:
            return
        self.status, self.tag = r.u32(), r.opaque()
        self.results = []
        for _ in range(r.u32()):
            op, status = r.u32(), r.u32()
            body = None
            if status == 0:
                body = result_body(op, r)
            elif op == 34:
                r.bitmap()  # SETATTR carries its bitmap even on failure
            self.results.append((op, status, body))
        assert r.pos == len(raw), "trailing bytes after the last result"

    def body(self, index):
        return self.results[index][2]

    def statuses(self):
        return [status for _op, status, _body in self.results]


class ServerTestBase(unittest.TestCase):
    allow_writes = False

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ps2-nfs-test-")
        self.root = os.path.join(self.tmp, "games")
        os.makedirs(os.path.join(self.root, "DVD"))
        self.payload = os.urandom(5000)
        with open(os.path.join(self.root, "DVD", "game.iso"), "wb") as f:
            f.write(self.payload)
        self.logs = []
        self.server = nfs.Nfs4Server(self.root, allow_writes=self.allow_writes,
                                     log=self.logs.append)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def call(self, *ops, **kw):
        raw = self.server.handle_record(rpc_call(compound(list(ops), **kw)))
        return Reply(raw)

    def path_ops(self, path):
        ops = [putrootfh()]
        for part in [p for p in path.split("/") if p]:
            ops.append(lookup(part))
        return ops

    def fh(self, path=""):
        reply = self.call(*(self.path_ops(path) + [getfh()]))
        self.assertEqual(reply.status, 0, path)
        return reply.results[-1][2]

    def snapshot(self):
        out = {}
        for top, dirs, files in os.walk(self.tmp):
            for name in dirs + files:
                path = os.path.join(top, name)
                st = os.lstat(path)
                content = None
                if os.path.isfile(path) and not os.path.islink(path):
                    with open(path, "rb") as f:
                        content = f.read()
                out[os.path.relpath(path, self.tmp)] = (st.st_mode, content)
        return out


class RpcTests(ServerTestBase):
    def test_null_procedure_answers_success_with_auth_none_verifier(self):
        reply = Reply(self.server.handle_record(rpc_call(b"", proc=0)))
        self.assertEqual((reply.xid, reply.msg_type, reply.reply_stat), (7, 1, 0))
        self.assertEqual(reply.verifier, (0, b""))
        self.assertEqual((reply.accept_stat, reply.tail), (0, b""))

    def test_wrong_program_version_procedure(self):
        self.assertEqual(Reply(self.server.handle_record(
            rpc_call(b"", prog=100005, proc=0))).accept_stat, 1)
        reply = Reply(self.server.handle_record(rpc_call(b"", vers=3, proc=0)))
        self.assertEqual(reply.accept_stat, 2)
        self.assertEqual(reply.tail, p32(4) + p32(4))
        self.assertEqual(Reply(self.server.handle_record(
            rpc_call(b"", proc=2))).accept_stat, 3)

    def test_rpc_version_and_auth_flavor_are_denied(self):
        reply = Reply(self.server.handle_record(rpc_call(b"", rpcvers=3, proc=0)))
        self.assertEqual((reply.reply_stat, reply.denied), (1, [0, 2, 2]))
        reply = Reply(self.server.handle_record(rpc_call(b"", flavor=6, proc=0)))
        self.assertEqual((reply.reply_stat, reply.denied), (1, [1, 5]))

    def test_auth_none_is_accepted(self):
        reply = Reply(self.server.handle_record(rpc_call(
            compound([putrootfh(), getfh()]), flavor=0)))
        self.assertEqual(reply.status, 0)

    def test_truncated_call_is_garbage_args_and_a_reply_is_ignored(self):
        self.assertIsNone(self.server.handle_record(b"\0\0\0\1"))
        self.assertIsNone(self.server.handle_record(p32(1) + p32(1) + b"\0" * 20))
        call = rpc_call(compound([putrootfh(), getfh()]))
        self.assertEqual(Reply(self.server.handle_record(call[:30])).accept_stat, 4)
        self.assertEqual(Reply(self.server.handle_record(call[:-2])).accept_stat, 4)


class CompoundTests(ServerTestBase):
    def test_tag_is_echoed(self):
        reply = self.call(putrootfh(), tag=b"neutrino")
        self.assertEqual((reply.status, reply.tag), (0, b"neutrino"))

    def test_minor_versions_one_and_two_are_refused_with_no_results(self):
        for minor in (1, 2):
            reply = self.call(putrootfh(), getfh(), minor=minor)
            self.assertEqual((reply.status, reply.results), (10021, []))

    def test_too_many_operations(self):
        reply = self.call(*([putrootfh()] * 129))
        self.assertEqual((reply.status, reply.results), (10018, []))
        self.assertEqual(self.call(*([putrootfh()] * 128)).status, 0)

    def test_unknown_operations(self):
        reply = self.call(putrootfh(), p32(99))
        self.assertEqual(reply.results[-1][:2], (10044, 10044))
        reply = self.call(putrootfh(), p32(3))   # ACCESS: real, not implemented
        self.assertEqual(reply.results[-1][:2], (3, 10004))

    def test_processing_stops_at_the_first_error(self):
        reply = self.call(putrootfh(), lookup("missing"), getfh())
        self.assertEqual(reply.status, 2)
        self.assertEqual(reply.statuses(), [0, 2])

    def test_no_current_filehandle(self):
        reply = self.call(getfh())
        self.assertEqual(reply.statuses(), [10020])

    def test_an_operation_that_does_not_decode_is_badxdr(self):
        reply = self.call(putrootfh(), p32(15) + p32(40) + b"abc")
        self.assertEqual(reply.statuses(), [0, 10036])


class LayoutTests(ServerTestBase):
    """The fixed offsets unfs4 reads (unfs4.c R_STATUS, R_RES and RES())."""

    def test_status_and_results_sit_where_unfs4_reads_them(self):
        raw = b"\0\0\0\0" + self.server.handle_record(rpc_call(compound(
            [putrootfh(), lookup("DVD"), lookup("game.iso"), getfh()])))
        get32 = lambda off: struct.unpack(">I", raw[off:off + 4])[0]
        self.assertEqual(get32(12) | get32(24), 0)        # reply and accept status
        self.assertEqual(get32(28), 0)                    # R_STATUS
        self.assertEqual(get32(40), 24)                   # R_RES: first op
        # PUTROOTFH and each LOOKUP are 8 bytes, so GETFH is at RES(3)
        self.assertEqual(get32(40 + 8 * 3), 10)
        self.assertEqual(get32(40 + 8 * 3 + 4), 0)
        self.assertEqual(get32(40 + 8 * 4), 16)          # handle length

    def test_getattr_is_exactly_type_size_mtime(self):
        fh = self.fh("DVD/game.iso")
        raw = self.server.handle_record(rpc_call(compound([putfh(fh), getattr_()])))
        res = raw[36 + 8 + 8:]                           # after PUTFH and GETATTR's head
        self.assertEqual(res[:12], p32(2) + p32(0x12) + p32(0x00200000))
        self.assertEqual(res[12:16], p32(24))
        self.assertEqual(struct.unpack(">I", res[16:20])[0], 1)
        self.assertEqual(struct.unpack(">Q", res[20:28])[0], len(self.payload))
        self.assertEqual(len(res), 40)

    def test_attributes_are_only_those_asked_for(self):
        reply = self.call(*self.path_ops("DVD/game.iso"), getattr_([4]))
        self.assertEqual(reply.body(-1), {"size": len(self.payload)})
        reply = self.call(*self.path_ops("DVD"), getattr_([1, 4, 53, 54, 70]))
        self.assertEqual(sorted(reply.body(-1)), ["mtime", "size", "type"])
        self.assertEqual(reply.body(-1)["type"], 2)

    def test_read_data_starts_where_unfs4_splits_the_reply(self):
        # unfs4 reads 60 bytes after the record mark into its buffer and the
        # data straight after that into the caller's.
        fh = self.fh("DVD/game.iso")
        raw = self.server.handle_record(rpc_call(compound([putfh(fh), read(0, 2048)])))
        self.assertEqual(struct.unpack(">I", raw[56:60])[0], 2048)   # buf[60]: count
        self.assertEqual(raw[60:60 + 2048], self.payload[:2048])
        self.assertEqual(len(raw), 60 + 2048)


class ReadTests(ServerTestBase):
    def test_reads_are_byte_exact_and_eof_is_set(self):
        fh = self.fh("DVD/game.iso")
        out = b""
        while True:
            reply = self.call(putfh(fh), read(len(out), 2048))
            body = reply.body(1)
            out += body["data"]
            if body["eof"]:
                break
            self.assertEqual(len(body["data"]), 2048)
        self.assertEqual(out, self.payload)
        body = self.call(putfh(fh), read(len(self.payload) + 10, 2048)).body(1)
        self.assertEqual((body["eof"], body["data"]), (1, b""))

    def test_never_more_than_count(self):
        fh = self.fh("DVD/game.iso")
        for count in (0, 1, 3, 512, 2047):
            body = self.call(putfh(fh), read(100, count)).body(1)
            self.assertEqual(body["data"], self.payload[100:100 + count])

    def test_count_is_capped(self):
        with open(os.path.join(self.root, "big.bin"), "wb") as f:
            f.truncate(nfs.MAX_IO + 4096)
        reply = self.call(*self.path_ops("big.bin"), read(0, 0xFFFFFFFF))
        self.assertEqual(len(reply.body(-1)["data"]), nfs.MAX_IO)

    def test_one_reply_holds_one_full_read(self):
        with open(os.path.join(self.root, "big.bin"), "wb") as f:
            f.truncate(nfs.MAX_IO + 4096)
        reply = self.call(*self.path_ops("big.bin"), *[read(0, nfs.MAX_IO)] * 126)
        self.assertEqual(reply.statuses(), [0, 0, 0, 10018])  # NFS4ERR_RESOURCE
        self.assertLess(len(reply.raw), nfs.MAX_IO + 4096)

    def test_a_file_past_4_gib_reads_at_its_tail(self):
        path = os.path.join(self.root, "DVD", "huge.iso")
        size = 5 * 1024 ** 3 + 123
        try:
            with open(path, "wb") as f:
                f.seek(size - 4)
                f.write(b"TAIL")
        except OSError:
            self.skipTest("no sparse files here")
        reply = self.call(*self.path_ops("DVD/huge.iso"), getattr_([4]), read(size - 4, 2048))
        self.assertEqual(reply.body(-2), {"size": size})
        self.assertEqual(reply.body(-1), {"eof": 1, "data": b"TAIL"})

    def test_read_of_a_folder_is_isdir(self):
        reply = self.call(*self.path_ops("DVD"), read(0, 10))
        self.assertEqual(reply.status, 21)

    def test_reads_follow_a_change_made_through_the_server(self):
        server = nfs.Nfs4Server(self.root, allow_writes=True, log=self.logs.append)
        conn = nfs._Connection(None, ("127.0.0.1", 1))

        def send(*ops):
            return Reply(server.handle_record(rpc_call(compound(list(ops))), conn))

        fh = Reply(server.handle_record(rpc_call(compound(
            self.path_ops("DVD/game.iso") + [getfh()])), conn)).body(-1)
        self.assertEqual(send(putfh(fh), read(0, 4)).body(1)["data"], self.payload[:4])
        self.assertEqual(len(conn.files), 1)
        send(putfh(fh), write(0, b"ABCD"))
        self.assertEqual(send(putfh(fh), read(0, 4)).body(1)["data"], b"ABCD")
        # A delete through the same connection drops its own cached handle
        # first; Windows would refuse to delete a file still open.
        self.assertEqual(len(conn.files), 1)
        self.assertEqual(send(*self.path_ops("DVD"), remove("game.iso")).status, 0)
        self.assertEqual(conn.files, {})


class LookupTests(ServerTestBase):
    def test_missing_and_not_a_folder(self):
        self.assertEqual(self.call(putrootfh(), lookup("nope")).status, 2)
        self.assertEqual(self.call(*self.path_ops("DVD/game.iso"), lookup("x")).status, 20)

    def test_names_no_share_serves(self):
        for name, status in ((".", 10041), ("..", 10041), ("a/b", 10041),
                             ("a\\b", 10041), ("game.iso:stream", 10041),
                             ("CON", 10041), ("nul.txt", 10041), ("trail.", 10041),
                             ("x" * 256, 63), ("", 22)):
            with self.subTest(name=name):
                reply = self.call(putrootfh(), lookup("DVD"), lookup(name))
                self.assertEqual(reply.status, status)

    @unittest.skipUnless(hasattr(os, "symlink"), "no symlinks")
    def test_a_symlink_out_of_the_share_is_refused_and_not_listed(self):
        outside = os.path.join(self.tmp, "outside")
        os.makedirs(outside)
        with open(os.path.join(outside, "secret.txt"), "wb") as f:
            f.write(b"secret")
        try:
            os.symlink(outside, os.path.join(self.root, "elsewhere"))
            os.symlink(os.path.join(self.root, "DVD"), os.path.join(self.root, "inside"))
        except (OSError, NotImplementedError):
            self.skipTest("cannot create symlinks here")
        reply = self.call(putrootfh(), lookup("elsewhere"), lookup("secret.txt"), read(0, 10))
        self.assertEqual(reply.status, 13)
        names = [e[1] for e in self.call(putrootfh(), readdir()).body(1)["entries"]]
        self.assertIn(b"inside", names)
        self.assertNotIn(b"elsewhere", names)
        reply = self.call(putrootfh(), lookup("inside"), lookup("game.iso"), read(0, 4))
        self.assertEqual(reply.body(-1)["data"], self.payload[:4])


class ReaddirTests(ServerTestBase):
    def setUp(self):
        super().setUp()
        self.names = sorted("game %03d with a long enough name.iso" % i for i in range(60))
        for name in self.names:
            with open(os.path.join(self.root, "DVD", name), "wb") as f:
                f.write(b"x" * 7)
        self.names = sorted(self.names + ["game.iso"])

    def walk(self, maxcount):
        fh = self.fh("DVD")
        seen, cookie = [], 0
        while True:
            raw = self.server.handle_record(rpc_call(compound(
                [putfh(fh), readdir(cookie, maxcount)])))
            reply = Reply(raw)
            self.assertEqual(reply.status, 0)
            body = reply.body(1)
            # READDIR4resok, from the verifier to eof, never exceeds maxcount
            self.assertLessEqual(len(raw) - 36 - 8 - 8, maxcount)
            for cookie, name, attrs in body["entries"]:
                seen.append(name.decode())
                self.assertEqual(sorted(attrs), ["mtime", "size", "type"])
            if body["eof"]:
                return seen

    def test_paging_returns_every_entry_once_within_maxcount(self):
        for maxcount in (1032, 300, 120, 65536):
            with self.subTest(maxcount=maxcount):
                self.assertEqual(self.walk(maxcount), self.names)

    @unittest.skipIf(os.name == "nt", "Windows has no backslash in a name")
    def test_a_name_with_a_backslash_is_not_listed(self):
        open(os.path.join(self.root, "DVD", "a" + chr(92) + "b.iso"), "wb").close()  # it could not be opened
        self.assertEqual(self.walk(65536), self.names)

    def test_unfs4_window_is_respected(self):
        # unfs4 sends dircount 1024 and maxcount 1032, and copies everything
        # after the verifier to a 1024-byte buffer: entries plus 8 must fit.
        raw = self.server.handle_record(rpc_call(compound(
            [putfh(self.fh("DVD")), readdir(0, 1032)])))
        self.assertLessEqual(len(raw) - 64, 1024)

    def test_bad_cookies_and_too_small(self):
        fh = self.fh("DVD")
        for cookie in (1, 2):
            self.assertEqual(self.call(putfh(fh), readdir(cookie)).status, 10003)
        self.assertEqual(self.call(putfh(fh), readdir(0, 40)).status, 10005)

    def test_names_that_cannot_be_opened_are_not_listed(self):
        for name in ("CON", "trailing.", "trailing "):
            try:
                open(os.path.join(self.root, "DVD", name), "wb").close()
            except OSError:
                continue
        self.assertEqual(self.walk(1032), self.names)

    def test_readdir_of_a_file_is_notdir(self):
        self.assertEqual(self.call(*self.path_ops("DVD/game.iso"), readdir()).status, 20)

    def test_a_new_file_shows_up(self):
        fh = self.fh("DVD")
        before = len(self.call(putfh(fh), readdir(0, 65536)).body(1)["entries"])
        open(os.path.join(self.root, "DVD", "zzz.iso"), "wb").close()
        after = self.call(putfh(fh), readdir(0, 65536)).body(1)["entries"]
        self.assertEqual(len(after), before + 1)


class HandleTests(ServerTestBase):
    def test_bad_and_stale_handles(self):
        fh = self.fh("DVD/game.iso")
        self.assertEqual(self.call(putfh(fh[:10])).status, 10001)
        self.assertEqual(self.call(putfh(b"\0" * 200)).status, 10001)
        other = nfs.Nfs4Server(self.root, log=self.logs.append)
        reply = Reply(other.handle_record(rpc_call(compound([putfh(fh)]))))
        self.assertEqual(reply.status, 70)
        self.assertEqual(self.call(putfh(fh[:8] + p64(999999))).status, 70)

    def test_a_handle_to_a_file_removed_behind_the_server_is_stale(self):
        fh = self.fh("DVD/game.iso")
        os.remove(os.path.join(self.root, "DVD", "game.iso"))
        self.assertEqual(self.call(putfh(fh), getattr_()).status, 70)

    def test_the_same_path_gets_the_same_handle(self):
        self.assertEqual(self.fh("DVD/game.iso"), self.fh("/DVD/game.iso"))
        self.assertNotEqual(self.fh("DVD"), self.fh(""))


class WritesOffTests(ServerTestBase):
    allow_writes = False

    def test_every_change_is_refused_and_nothing_is_touched(self):
        before = self.snapshot()
        dvd, game = self.fh("DVD"), self.fh("DVD/game.iso")
        refused = [
            [putfh(dvd), open_("new.iso")],
            [putfh(dvd), open_("game.iso", create=False, access=2)],
            [putfh(dvd), open_("game.iso", how=0, attrs=bitmap([4]) + opaque(p64(0)))],
            [putfh(game), write(0, b"overwrite")],
            [putfh(game), setattr_size(0)],
            [putfh(dvd), mkdir("folder")],
            [putfh(dvd), remove("game.iso")],
            [putfh(dvd), savefh(), putfh(dvd), rename("game.iso", "moved.iso")],
        ]
        for ops in refused:
            with self.subTest(op=struct.unpack(">I", ops[-1][:4])[0]):
                self.assertEqual(self.call(*ops).status, 30)
        self.assertEqual(self.snapshot(), before)

    def test_reading_still_works(self):
        reply = self.call(*self.path_ops("DVD/game.iso"), read(0, 16))
        self.assertEqual(reply.body(-1)["data"], self.payload[:16])

    def test_opening_for_read_only_is_allowed(self):
        reply = self.call(*self.path_ops("DVD"), open_("game.iso", create=False, access=1))
        self.assertEqual(reply.status, 0)


class WritesOnTests(ServerTestBase):
    allow_writes = True

    def test_a_rename_waits_for_a_compound_in_progress(self):
        # The lock covers a whole COMPOUND: a RENAME from another connection
        # cannot land between this one's PUTFH and its READ.
        fh, dvd = self.fh("DVD/game.iso"), self.fh("DVD")
        paused, go, renamed = threading.Event(), threading.Event(), threading.Event()
        real_one = self.server._one

        def one(op, r, state):
            out = real_one(op, r, state)
            if op == nfs.OP_PUTFH and not paused.is_set():
                paused.set()
                go.wait(5)
            return out

        self.server._one = one
        got = []
        reader = threading.Thread(target=lambda: got.append(Reply(self.server.handle_record(
            rpc_call(compound([putfh(fh), read(0, 4)]))))))
        reader.start()
        self.assertTrue(paused.wait(5))
        threading.Thread(target=lambda: (self.call(putfh(dvd), savefh(), putfh(dvd),
                                                   rename("game.iso", "moved.iso")),
                                         renamed.set()), daemon=True).start()
        try:
            self.assertFalse(renamed.wait(0.3))
        finally:
            go.set()
        reader.join(5)
        self.assertEqual(got[0].body(1)["data"], self.payload[:4])
        self.assertTrue(renamed.wait(5))

    def create(self, folder, name, how=0):
        """unfs4_open(O_CREAT): SETCLIENTID, then CONFIRM + OPEN, then CLOSE."""
        sc = self.call(setclientid()).body(0)
        reply = self.call(setclientid_confirm(sc["clientid"], sc["confirm"]),
                          *self.path_ops(folder), open_(name, how=how,
                                                        clientid=sc["clientid"]))
        if reply.status:
            return reply.status, None
        opened = reply.body(-1)
        self.assertEqual(opened["rflags"] & 2, 0)   # no OPEN_CONFIRM needed
        path = folder + "/" + name
        reply = self.call(*self.path_ops(path), getfh(), close(opened["stateid"]))
        self.assertEqual(reply.status, 0)
        return 0, reply.body(-2)

    def test_confirm_must_match(self):
        sc = self.call(setclientid()).body(0)
        self.assertEqual(self.call(setclientid_confirm(sc["clientid"], b"\1" * 8)).status, 10022)
        self.assertEqual(self.call(setclientid_confirm(sc["clientid"], sc["confirm"])).status, 0)

    def test_create_write_read_back_resize_and_delete(self):
        status, fh = self.create("DVD", "save.bin")
        self.assertEqual(status, 0)
        path = os.path.join(self.root, "DVD", "save.bin")
        self.assertEqual(os.path.getsize(path), 0)
        reply = self.call(putfh(fh), write(0, b"hello world"))
        self.assertEqual(reply.body(1)["count"], 11)
        self.assertEqual(reply.body(1)["committed"], 2)     # FILE_SYNC4
        self.call(putfh(fh), write(6, b"there"))
        self.assertEqual(self.call(putfh(fh), read(0, 100)).body(1)["data"], b"hello there")
        self.assertEqual(self.call(putfh(fh), setattr_size(5)).body(1), {4})
        with open(path, "rb") as f:
            self.assertEqual(f.read(), b"hello")
        self.assertEqual(self.call(putfh(fh), setattr_size(8)).status, 0)
        self.assertEqual(os.path.getsize(path), 8)
        self.assertEqual(self.call(*self.path_ops("DVD"), remove("save.bin")).status, 0)
        self.assertFalse(os.path.exists(path))
        self.assertEqual(self.call(putfh(fh), getattr_()).status, 70)

    def test_create_an_existing_file_unchecked_keeps_it_guarded_refuses(self):
        self.assertEqual(self.create("DVD", "game.iso")[0], 0)
        with open(os.path.join(self.root, "DVD", "game.iso"), "rb") as f:
            self.assertEqual(f.read(), self.payload)
        self.assertEqual(self.create("DVD", "game.iso", how=1)[0], 17)
        self.assertEqual(self.create("DVD", "game.iso", how=2)[0], 10004)  # EXCLUSIVE4

    def test_open_with_size_zero_truncates(self):
        dvd = self.fh("DVD")
        reply = self.call(putfh(dvd), open_("game.iso", attrs=bitmap([4]) + opaque(p64(0))))
        self.assertEqual(reply.body(1)["attrset"], {4})
        self.assertEqual(os.path.getsize(os.path.join(self.root, "DVD", "game.iso")), 0)

    def test_other_attributes_are_not_supported(self):
        reply = self.call(*self.path_ops("DVD"), open_("x.bin", attrs=bitmap([1]) + opaque(p32(1))))
        self.assertEqual(reply.status, 10032)
        self.assertFalse(os.path.exists(os.path.join(self.root, "DVD", "x.bin")))

    def test_new_entries_are_synced_to_disk(self):
        synced = []
        real = nfs._sync_dir
        nfs._sync_dir = synced.append
        self.addCleanup(setattr, nfs, "_sync_dir", real)
        self.assertEqual(self.create("DVD", "save.bin")[0], 0)
        self.assertEqual(self.call(putrootfh(), mkdir("VMC")).status, 0)
        self.assertEqual(self.call(putrootfh(), lookup("DVD"), savefh(), putrootfh(),
                                   rename("save.bin", "moved.bin")).status, 0)
        dvd, top = os.path.realpath(os.path.join(self.root, "DVD")), os.path.realpath(self.root)
        self.assertEqual(sorted(os.path.realpath(p) for p in synced), sorted([dvd, top, dvd, top]))
        real(self.root)  # the real one runs on this host

    def test_a_failed_sync_still_moves_the_handles(self):
        def fail(path):
            raise OSError(5, "sync failed")  # EIO
        real = nfs._sync_dir
        nfs._sync_dir = fail
        self.addCleanup(setattr, nfs, "_sync_dir", real)
        with open(os.path.join(self.root, "a.bin"), "wb") as f:
            f.write(b"A")
        open(os.path.join(self.root, "b.bin"), "wb").close()
        moved, replaced = self.fh("a.bin"), self.fh("b.bin")
        self.assertEqual(self.call(putrootfh(), savefh(), putrootfh(),
                                   rename("a.bin", "b.bin")).status, 5)  # the failed sync is reported ...
        self.assertEqual(self.call(putfh(moved), read(0, 1)).body(1)["data"], b"A")  # ... the moved handle follows
        self.assertEqual(self.call(putfh(replaced), getattr_()).status, 70)  # ... the replaced one is stale

    def test_folders(self):
        self.assertEqual(self.call(putrootfh(), mkdir("VMC")).status, 0)
        self.assertTrue(os.path.isdir(os.path.join(self.root, "VMC")))
        self.assertEqual(self.call(putrootfh(), mkdir("VMC")).status, 17)
        self.assertEqual(self.call(putrootfh(), p32(6) + p32(1) + opaque("f")
                                   + mode_attr()).status, 10007)
        self.assertEqual(self.call(putrootfh(), remove("DVD")).status, 66)
        self.assertEqual(self.call(putrootfh(), remove("VMC")).status, 0)
        self.assertFalse(os.path.exists(os.path.join(self.root, "VMC")))

    def test_rename_file_and_folder_and_handles_follow(self):
        game = self.fh("DVD/game.iso")
        dvd = self.fh("DVD")
        reply = self.call(putfh(dvd), savefh(), putrootfh(), rename("game.iso", "moved.iso"))
        self.assertEqual(reply.status, 0)
        self.assertTrue(os.path.isfile(os.path.join(self.root, "moved.iso")))
        self.assertEqual(self.call(putfh(game), read(0, 4)).body(1)["data"], self.payload[:4])
        self.assertEqual(self.call(putrootfh(), savefh(), putrootfh(),
                                   rename("DVD", "GAMES")).status, 0)
        self.assertEqual(self.call(putfh(dvd), getattr_([1])).body(1), {"type": 2})
        self.assertEqual(self.call(putfh(dvd), lookup("game.iso")).status, 2)

    def test_rename_onto_existing(self):
        open(os.path.join(self.root, "other.iso"), "wb").close()
        os.makedirs(os.path.join(self.root, "full", "x"))
        os.makedirs(os.path.join(self.root, "empty"))
        rn = lambda a, b: self.call(putrootfh(), savefh(), putrootfh(), rename(a, b)).status
        self.assertEqual(rn("other.iso", "DVD"), 17)       # file onto folder
        self.assertEqual(rn("empty", "full"), 17)          # onto a non-empty folder
        self.assertEqual(rn("DVD", "empty"), 0)            # onto an empty folder
        self.assertTrue(os.path.isfile(os.path.join(self.root, "empty", "game.iso")))
        with open(os.path.join(self.root, "a.bin"), "wb") as f:
            f.write(b"A")
        self.assertEqual(rn("a.bin", "other.iso"), 0)      # file replaces file
        with open(os.path.join(self.root, "other.iso"), "rb") as f:
            self.assertEqual(f.read(), b"A")
        kept = self.fh("other.iso")
        self.assertEqual(rn("other.iso", "other.iso"), 0)  # onto itself
        self.assertEqual(self.call(putfh(kept), getattr_([1])).status, 0)  # its handle still works
        self.assertEqual(rn("missing", "x"), 2)

    def test_rename_cannot_leave_the_share(self):
        before = self.snapshot()
        for new in ("..", "../escape.iso", "a/b"):
            self.assertEqual(self.call(putrootfh(), lookup("DVD"), savefh(), putrootfh(),
                                       rename("game.iso", new)).status, 10041)
        self.assertEqual(self.snapshot(), before)

    @unittest.skipUnless(hasattr(os, "symlink"), "no symlinks")
    def test_writes_never_reach_through_a_symlink_out_of_the_share(self):
        outside = os.path.join(self.tmp, "outside")
        os.makedirs(outside)
        with open(os.path.join(outside, "keep.txt"), "wb") as f:
            f.write(b"keep")
        try:
            os.symlink(outside, os.path.join(self.root, "link"))
        except (OSError, NotImplementedError):
            self.skipTest("cannot create symlinks here")
        self.assertEqual(self.call(putrootfh(), lookup("link"), mkdir("x")).status, 13)
        self.assertEqual(self.call(putrootfh(), lookup("link"), remove("keep.txt")).status, 13)
        self.assertEqual(self.create("link", "new.txt")[0], 13)
        # Like LOOKUP, a link out of the share cannot be named at all, so not
        # even removing it is served.
        self.assertEqual(self.call(putrootfh(), remove("link")).status, 13)
        self.assertEqual(os.listdir(outside), ["keep.txt"])

    @unittest.skipUnless(hasattr(os, "symlink"), "no symlinks")
    def test_removing_or_renaming_a_link_acts_on_the_link(self):
        target = os.path.join(self.root, "DVD", "game.iso")
        try:
            os.symlink(target, os.path.join(self.root, "alias.iso"))
            os.symlink(target, os.path.join(self.root, "alias2.iso"))
        except (OSError, NotImplementedError):
            self.skipTest("cannot create symlinks here")
        self.assertEqual(self.call(putrootfh(), remove("alias.iso")).status, 0)
        self.assertFalse(os.path.lexists(os.path.join(self.root, "alias.iso")))
        self.assertEqual(self.call(putrootfh(), savefh(), putrootfh(),
                                   rename("alias2.iso", "moved.iso")).status, 0)
        self.assertTrue(os.path.islink(os.path.join(self.root, "moved.iso")))
        with open(target, "rb") as f:
            self.assertEqual(f.read(), self.payload)

    def test_changes_are_logged(self):
        self.create("DVD", "new.bin")
        self.call(putrootfh(), mkdir("NEW"))
        joined = "\n".join(self.logs)
        self.assertIn("created DVD/new.bin", joined)
        self.assertIn("made folder NEW", joined)


class SocketTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ps2-nfs-sock-")
        with open(os.path.join(self.tmp, "game.iso"), "wb") as f:
            f.write(b"0123456789" * 1000)
        self.server = nfs.Nfs4Server(self.tmp, log=lambda m: None)
        self.port = self.server.listen("127.0.0.1", 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.stop()
        self.thread.join(timeout=5)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def connect(self):
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        self.addCleanup(sock.close)
        return sock

    @staticmethod
    def recv_exact(sock, n):
        out = b""
        while len(out) < n:
            chunk = sock.recv(n - len(out))
            if not chunk:
                return None
            out += chunk
        return out

    def exchange(self, sock, call, fragments=1):
        step = -(-len(call) // fragments)
        for i in range(0, len(call), step):
            part = call[i:i + step]
            last = 0x80000000 if i + step >= len(call) else 0
            sock.sendall(p32(last | len(part)) + part)
        mark = struct.unpack(">I", self.recv_exact(sock, 4))[0]
        self.assertTrue(mark & 0x80000000, "the reply must be one last fragment")
        return Reply(self.recv_exact(sock, mark & 0x7FFFFFFF))

    def test_one_fragment_or_several(self):
        sock = self.connect()
        call = rpc_call(compound([putrootfh(), lookup("game.iso"), read(5, 10)]))
        for fragments in (1, 3):
            reply = self.exchange(sock, call, fragments)
            self.assertEqual(reply.body(2)["data"], b"5678901234")

    def test_an_oversized_record_closes_the_connection(self):
        sock = self.connect()
        sock.sendall(p32(0x80000000 | (nfs.MAX_RECORD + 1)))
        self.assertIn(sock.recv(10), (b"",))

    def test_endless_empty_fragments_close_the_connection(self):
        sock = self.connect()
        try:
            sock.sendall(p32(0) * (nfs.MAX_RECORD // 4 + 1))
        except OSError:
            pass  # closed while sending: the server has already refused it
        self.assertIn(sock.recv(10), (b"",))

    def test_a_quiet_connection_stays_open_and_drops_its_file(self):
        sock = self.connect()
        call = rpc_call(compound([putrootfh(), lookup("game.iso"), read(0, 4)]))
        self.exchange(sock, call)
        with self.server.lock:
            conn = next(iter(self.server.connections))
        self.assertEqual(len(conn.files), 1)
        deadline = time.monotonic() + nfs.FILE_IDLE_SECONDS + 3
        while conn.files and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertEqual(conn.files, {}, "an idle read handle must be closed")
        self.assertEqual(self.exchange(sock, call).body(2)["data"], b"0123")

    def test_a_connection_that_cannot_start_leaves_the_server_running(self):
        real = threading.Thread.start
        failed = []

        def start(thread):
            if thread.name.startswith("nfs-") and not failed:
                failed.append(thread.name)
                raise RuntimeError("can't start new thread")
            return real(thread)
        threading.Thread.start = start
        self.addCleanup(setattr, threading.Thread, "start", real)
        first = self.connect()
        first.settimeout(5)
        self.assertEqual(first.recv(10), b"")         # that one is dropped ...
        with self.server.lock:
            self.assertEqual(len(self.server.connections), 0)
        call = rpc_call(compound([putrootfh(), lookup("game.iso"), read(0, 4)]))
        self.assertEqual(self.exchange(self.connect(), call).body(2)["data"], b"0123")  # ... the next is served
        self.assertEqual(len(failed), 1)

    def test_the_quietest_connection_goes_past_the_cap(self):
        socks = [self.connect()]
        call = rpc_call(b"", proc=0)
        self.exchange(socks[0], call)
        time.sleep(0.05)
        for _ in range(nfs.MAX_CONNECTIONS):
            s = self.connect()
            self.exchange(s, call)
            socks.append(s)
        socks[0].settimeout(5)
        self.assertEqual(socks[0].recv(10), b"")
        self.exchange(socks[-1], call)

    class _Sock(object):
        closed = False

        def shutdown(self, how):
            self.closed = True

    def _conn(self, address, port):
        return nfs._Connection(self._Sock(), (address, port))

    def test_past_the_cap_the_busiest_address_gives_way(self):
        server = nfs.Nfs4Server(self.tmp, log=lambda m: None)
        console = self._conn("10.0.0.5", 1000)
        server._admit(console)
        others = [self._conn("10.0.0.9", 2000 + i) for i in range(nfs.MAX_CONNECTIONS)]
        for c in others:
            server._admit(c)
        self.assertFalse(console.sock.closed)        # the quietest, but not 10.0.0.9's
        self.assertTrue(others[0].sock.closed)       # 10.0.0.9's own quietest went
        stranger = self._conn("10.0.0.7", 3000)
        server._admit(stranger)                      # one address cannot hold every slot
        self.assertFalse(stranger.sock.closed)
        self.assertTrue(others[1].sock.closed)
        # More arrive before any closed worker has gone: each closes one more
        # of 10.0.0.9's, so the cap holds, and none picks one already closing.
        for i in range(3):
            server._admit(self._conn("10.0.0.9", 4000 + i))
        self.assertEqual([c.sock.closed for c in others[:6]], [True] * 5 + [False])
        self.assertFalse(console.sock.closed or stranger.sock.closed)
        self.assertEqual(len([c for c in server.connections if not c.sock.closed]),
                         nfs.MAX_CONNECTIONS)

    def test_past_the_cap_one_connection_each_turns_the_newcomer_away(self):
        server = nfs.Nfs4Server(self.tmp, log=lambda m: None)
        held = [self._conn("10.0.1.%d" % i, 1000) for i in range(nfs.MAX_CONNECTIONS)]
        for c in held:
            server._admit(c)
        newcomer = self._conn("10.0.2.1", 1000)
        server._admit(newcomer)
        self.assertTrue(newcomer.sock.closed)
        self.assertFalse(any(c.sock.closed for c in held))
        again = self._conn("10.0.1.0", 1001)         # a console reconnecting
        server._admit(again)
        self.assertTrue(held[0].sock.closed)         # its own old connection goes
        self.assertFalse(again.sock.closed)


class LauncherTests(unittest.TestCase):
    def test_card_arguments(self):
        from launcher import servers
        card = servers.REGISTRY["nfs"]
        self.assertEqual(card.default_port, 2049)
        defaults = {f.key: f.default for f in card.fields}
        self.assertIs(defaults["allow_writes"], False)
        self.assertEqual(card.build_argv({"root_dir": "/g"}), ["--root-dir", "/g"])
        self.assertEqual(card.build_argv({"root_dir": "/g", "allow_writes": True,
                                          "bind": "10.0.0.2", "verbose": True}),
                         ["--root-dir", "/g", "--allow-writes", "--bind", "10.0.0.2",
                          "--verbose"])
        with self.assertRaises(ValueError):
            card.build_argv({})
        self.assertTrue(os.path.isfile(card.module_file))

    def test_firewall_port(self):
        from launcher import windows_setup
        self.assertEqual(windows_setup._server_ports("nfs", {}), [("TCP", 2049, "NFS")])

    def test_command_line(self):
        args = nfs.build_parser().parse_args(["--root-dir", "x"])
        self.assertEqual((args.port, args.allow_writes), (2049, False))
        self.assertTrue(nfs.build_parser().parse_args(
            ["--root-dir", "x", "--allow-writes"]).allow_writes)

    def test_main_refuses_a_missing_folder(self):
        self.assertEqual(nfs.main(["--root-dir", os.path.join(ROOT, "no such folder")]), 1)


if __name__ == "__main__":
    unittest.main()
