#!/usr/bin/env python3
"""PS2 Servers NFS entry point: a small NFS version 4.0 server for the PS2.

Built for unfs4 (github.com/sahlberg/unfs4, by Ronnie Sahlberg), the NFS client
inside Neutrino's NFS backing store and the nfsman driver. unfs4 is tiny and
reads replies the way Linux's server writes them, so these rules are hard
requirements, not style (docs/NFS.md has the whole contract):

  * A reply is one RPC fragment with an AUTH_NONE verifier, and a COMPOUND
    reply echoes the request's tag. unfs4 reads results at fixed offsets.
  * The only attributes are TYPE, SIZE and TIME_MODIFY, packed in that order.
    unfs4 reads them at fixed positions.
  * A reply never carries more than was asked for: READ at most `count`
    bytes, READDIR at most `maxcount`. Neutrino has 444 bytes for any other
    reply; every one here is far smaller.
  * A connection is never closed for being quiet. Neutrino connects once per
    game and cannot reconnect, so a long cutscene must not end the game.

Writes are off unless --allow-writes is given. NFS as these clients speak it
has no password: with writes on, anything that can reach port 2049 can change
or delete files in the shared folder.
"""

import argparse
import errno
import os
import secrets
import select
import signal
import socket
import stat
import struct
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
# The folder rules are the SMB2 server's: one implementation of "what a client
# may name", tested once (tests/test_smb2_paths.py).
_SMB2_DIR = os.path.join(os.path.dirname(_HERE), "smb2_server")
if os.path.isdir(_SMB2_DIR) and _SMB2_DIR not in sys.path:
    sys.path.insert(0, _SMB2_DIR)
from smb2_paths import PathError, Share, check_component  # noqa: E402

DEFAULT_PORT = 2049  # both PS2 clients are fixed to it

# A WRITE from nfsman carries at most 64 KiB; anything far beyond the largest
# legal request is a broken or hostile peer, and its connection is closed.
MAX_IO = 1024 * 1024
MAX_RECORD = MAX_IO + 8192
MAX_OPS = 128
MAX_READDIR = 64 * 1024
MAX_FH = 128            # NFS4_FHSIZE
MAX_CONNECTIONS = 32    # beyond this the busiest address's quietest connection is closed
FILE_IDLE_SECONDS = 1.0  # cached read handles close after this much quiet

# ONC RPC (RFC 5531)
_CALL, _REPLY = 0, 1
_MSG_ACCEPTED, _MSG_DENIED = 0, 1
_SUCCESS, _PROG_UNAVAIL, _PROG_MISMATCH, _PROC_UNAVAIL, _GARBAGE_ARGS = 0, 1, 2, 3, 4
_RPC_MISMATCH, _AUTH_ERROR = 0, 1
_AUTH_NONE, _AUTH_SYS = 0, 1
_AUTH_TOOWEAK = 5
_NFS_PROGRAM, _NFS_V4 = 100003, 4

# NFSv4.0 (RFC 7530)
NFS4_OK = 0
NFS4ERR_NOENT = 2
NFS4ERR_IO = 5
NFS4ERR_ACCESS = 13
NFS4ERR_EXIST = 17
NFS4ERR_XDEV = 18
NFS4ERR_NOTDIR = 20
NFS4ERR_ISDIR = 21
NFS4ERR_INVAL = 22
NFS4ERR_FBIG = 27
NFS4ERR_NOSPC = 28
NFS4ERR_ROFS = 30
NFS4ERR_NAMETOOLONG = 63
NFS4ERR_NOTEMPTY = 66
NFS4ERR_DQUOT = 69
NFS4ERR_STALE = 70
NFS4ERR_BADHANDLE = 10001
NFS4ERR_BAD_COOKIE = 10003
NFS4ERR_NOTSUPP = 10004
NFS4ERR_TOOSMALL = 10005
NFS4ERR_SERVERFAULT = 10006
NFS4ERR_BADTYPE = 10007
NFS4ERR_RESOURCE = 10018
NFS4ERR_NOFILEHANDLE = 10020
NFS4ERR_MINOR_VERS_MISMATCH = 10021
NFS4ERR_STALE_CLIENTID = 10022
NFS4ERR_ATTRNOTSUPP = 10032
NFS4ERR_BADXDR = 10036
NFS4ERR_BADNAME = 10041
NFS4ERR_OP_ILLEGAL = 10044

OP_CLOSE = 4
OP_CREATE = 6
OP_GETATTR = 9
OP_GETFH = 10
OP_LOOKUP = 15
OP_OPEN = 18
OP_OPEN_CONFIRM = 20
OP_PUTFH = 22
OP_PUTROOTFH = 24
OP_READ = 25
OP_READDIR = 26
OP_REMOVE = 28
OP_RENAME = 29
OP_SAVEFH = 32
OP_SETATTR = 34
OP_SETCLIENTID = 35
OP_SETCLIENTID_CONFIRM = 36
OP_WRITE = 38
OP_ILLEGAL = 10044
_FIRST_OP, _LAST_OP = 3, 39  # the NFSv4.0 operation numbers

NF4REG, NF4DIR = 1, 2
_FATTR4_TYPE, _FATTR4_SIZE, _FATTR4_MODE, _FATTR4_TIME_MODIFY = 1, 4, 33, 53
_GETTABLE = (_FATTR4_TYPE, _FATTR4_SIZE, _FATTR4_TIME_MODIFY)  # ascending
_FILE_SYNC4 = 2
_OPEN4_CREATE = 1
_UNCHECKED4, _GUARDED4 = 0, 1
_CLAIM_NULL = 0
_SHARE_ACCESS_WRITE = 2

_ERRNO_STATUS = {
    errno.ENOENT: NFS4ERR_NOENT, errno.EACCES: NFS4ERR_ACCESS,
    errno.EPERM: NFS4ERR_ACCESS, errno.EEXIST: NFS4ERR_EXIST,
    errno.ENOTDIR: NFS4ERR_NOTDIR, errno.EISDIR: NFS4ERR_ISDIR,
    errno.EINVAL: NFS4ERR_INVAL, errno.EFBIG: NFS4ERR_FBIG,
    errno.ENOSPC: NFS4ERR_NOSPC, errno.EROFS: NFS4ERR_ROFS,
    errno.ENAMETOOLONG: NFS4ERR_NAMETOOLONG, errno.ENOTEMPTY: NFS4ERR_NOTEMPTY,
    errno.EXDEV: NFS4ERR_XDEV,
}
if hasattr(errno, "EDQUOT"):
    _ERRNO_STATUS[errno.EDQUOT] = NFS4ERR_DQUOT

_PATH_STATUS = {
    "STATUS_ACCESS_DENIED": NFS4ERR_ACCESS,
    "STATUS_OBJECT_NAME_NOT_FOUND": NFS4ERR_NOENT,
}

_OP_NAMES = {v: k[3:] for k, v in list(globals().items()) if k.startswith("OP_")}


def _log(message):
    print("[{}] {}".format(time.strftime("%H:%M:%S"), message), flush=True)


class XdrError(Exception):
    """The request ended early or did not decode."""


class Nfs4Error(Exception):
    def __init__(self, status):
        super().__init__(status)
        self.status = status


def _p32(v):
    return struct.pack(">I", v)


def _p64(v):
    return struct.pack(">Q", v)


def _opaque(data):
    return _p32(len(data)) + data + b"\0" * (-len(data) & 3)


def _bitmap(bits):
    words = [0] * ((max(bits) // 32 + 1) if bits else 0)
    for bit in bits:
        words[bit // 32] |= 1 << (bit % 32)
    return _p32(len(words)) + b"".join(_p32(w) for w in words)


_EMPTY_BITMAP = _p32(0)


class _Reader(object):
    __slots__ = ("data", "pos")

    def __init__(self, data):
        self.data = data
        self.pos = 0

    def take(self, n):
        end = self.pos + n
        if n < 0 or end > len(self.data):
            raise XdrError()
        chunk = self.data[self.pos:end]
        self.pos = end
        return chunk

    def u32(self):
        return struct.unpack(">I", self.take(4))[0]

    def u64(self):
        return struct.unpack(">Q", self.take(8))[0]

    def fixed(self, n):
        chunk = self.take(n)
        self.take(-n & 3)
        return chunk

    def opaque(self):
        return self.fixed(self.u32())

    def bitmap(self):
        n = self.u32()
        if n > 8:
            raise XdrError()
        words = [self.u32() for _ in range(n)]
        return {w * 32 + b for w, word in enumerate(words) for b in range(32) if word >> b & 1}


def _sync_dir(path):
    """Make a folder's entries durable (POSIX). WRITE replies FILE_SYNC4, so a file it
    wrote must not vanish with its folder entry on a power loss. NTFS journals the
    entry, and Windows cannot open a folder this way."""
    if os.name == "nt":
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _status_of(exc):
    if isinstance(exc, FileNotFoundError):
        return NFS4ERR_NOENT
    return _ERRNO_STATUS.get(exc.errno, NFS4ERR_IO)


def _settable(reader):
    """The attributes of an OPEN, CREATE or SETATTR: SIZE and MODE only.

    MODE is accepted and ignored (unfs4 sends 0644 or 0755, and Windows has no
    such bits); SIZE is applied. Anything else is ATTRNOTSUPP.
    """
    bits = reader.bitmap()
    values = _Reader(reader.opaque())
    if bits - {_FATTR4_SIZE, _FATTR4_MODE}:
        raise Nfs4Error(NFS4ERR_ATTRNOTSUPP)
    out = {}
    for bit in sorted(bits):
        if bit == _FATTR4_SIZE:
            out["size"] = values.u64()
        else:
            values.u32()
    if values.pos != len(values.data):
        raise XdrError()
    return out


class _Handles(object):
    """Filehandles: a prefix for this run plus a number for each path.

    A handle from an earlier run, or for a path removed or renamed through this
    server, is STALE, never another file. Paths are kept as share-relative
    component tuples so a rename can follow them.
    """

    def __init__(self):
        self.prefix = secrets.token_bytes(8)
        self.lock = threading.Lock()
        self.paths = {0: ()}
        self.numbers = {(): 0}
        self.next = 1

    def handle(self, comps):
        with self.lock:
            number = self.numbers.get(comps)
            if number is None:
                number = self.next
                self.next += 1
                self.numbers[comps] = number
                self.paths[number] = comps
        return self.prefix + _p64(number)

    def lookup(self, fh):
        if len(fh) != 16:
            raise Nfs4Error(NFS4ERR_BADHANDLE)
        if fh[:8] != self.prefix:
            raise Nfs4Error(NFS4ERR_STALE)
        number = struct.unpack(">Q", fh[8:])[0]
        with self.lock:
            comps = self.paths.get(number)
        if comps is None:
            raise Nfs4Error(NFS4ERR_STALE)
        return number, comps

    def _under(self, comps):
        n = len(comps)
        return [c for c in self.numbers if c[:n] == comps]

    def removed(self, comps):
        with self.lock:
            for c in self._under(comps):
                del self.paths[self.numbers.pop(c)]

    def renamed(self, old, new):
        with self.lock:
            for c in self._under(new):
                del self.paths[self.numbers.pop(c)]
            n = len(old)
            for c in self._under(old):
                number = self.numbers.pop(c)
                moved = new + c[n:]
                self.numbers[moved] = number
                self.paths[number] = moved


class _Connection(object):
    def __init__(self, sock, address):
        self.sock = sock
        self.address = address
        self.last_active = time.monotonic()
        self.closing = False  # shut by _admit; its worker has not removed it yet
        self.files = {}  # handle number -> (generation, open file)

    def close_files(self):
        for _gen, f in self.files.values():
            try:
                f.close()
            except OSError:
                pass
        self.files.clear()


class _Compound(object):
    __slots__ = ("cfh", "sfh", "conn")

    def __init__(self, conn):
        self.cfh = None
        self.sfh = None
        self.conn = conn


class Nfs4Server(object):
    def __init__(self, root, allow_writes=False, verbose=False, log=_log):
        self.share = Share("nfs", root)
        self.allow_writes = allow_writes
        self.verbose = verbose
        self.log = log
        self.handles = _Handles()
        self.write_verifier = secrets.token_bytes(8)
        self.stateid_other = secrets.token_bytes(4)
        self.lock = threading.Lock()
        # One operation at a time across connections, so a path is checked and
        # used with no RENAME from another connection in between (that could
        # move a folder holding a link out of the export into the checked path).
        self.fs_lock = threading.Lock()
        self.generation = 0          # bumped by every change, drops caches
        self.counter = 0
        self.clients = {}            # clientid -> confirm verifier
        self.dir_cache = {}          # handle number -> (gen, mtime_ns, entries)
        self.connections = set()
        self.listener = None
        self.stopping = threading.Event()
        self.ops = {
            OP_CLOSE: self._op_close, OP_CREATE: self._op_create,
            OP_GETATTR: self._op_getattr, OP_GETFH: self._op_getfh,
            OP_LOOKUP: self._op_lookup, OP_OPEN: self._op_open,
            OP_OPEN_CONFIRM: self._op_open_confirm, OP_PUTFH: self._op_putfh,
            OP_PUTROOTFH: self._op_putrootfh, OP_READ: self._op_read,
            OP_READDIR: self._op_readdir, OP_REMOVE: self._op_remove,
            OP_RENAME: self._op_rename, OP_SAVEFH: self._op_savefh,
            OP_SETATTR: self._op_setattr, OP_SETCLIENTID: self._op_setclientid,
            OP_SETCLIENTID_CONFIRM: self._op_setclientid_confirm,
            OP_WRITE: self._op_write,
        }

    # -- network -------------------------------------------------------------

    def listen(self, host, port):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if os.name != "nt":
            # On Windows SO_REUSEADDR lets a second server steal a live port.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
            sock.listen(16)
        except OSError:
            sock.close()
            raise
        sock.settimeout(0.5)
        self.listener = sock
        return sock.getsockname()[1]

    def serve_forever(self):
        while not self.stopping.is_set():
            try:
                sock, address = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                if self.stopping.is_set():
                    break
                raise
            conn = None
            try:
                sock.settimeout(None)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                conn = _Connection(sock, address)
                self._admit(conn)
                threading.Thread(target=self._serve, args=(conn,), daemon=True,
                                 name="nfs-{}:{}".format(*address)).start()
            except (OSError, RuntimeError) as exc:  # the peer has gone already, or no thread to spare
                self.log("NFS: dropped a connection from {}: {}".format(address[0], exc))
                if conn is not None:
                    with self.lock:
                        self.connections.discard(conn)
                sock.close()

    def stop(self):
        self.stopping.set()
        if self.listener is not None:
            self.listener.close()
        with self.lock:
            conns = list(self.connections)
        for conn in conns:
            self._shut(conn)

    @staticmethod
    def _shut(conn):
        try:
            conn.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def _admit(self, conn):
        # An IOP reset leaves the old connection half-open, so they pile up
        # slowly. They are never closed for being quiet (Neutrino cannot
        # reconnect). Past the cap the quietest connection of the address
        # holding the most goes, the newcomer's own on a tie. So one address
        # cannot hold every slot, and an address with a single connection (a
        # console) loses it only to its own next connection.
        with self.lock:
            self.connections.add(conn)
            live = [c for c in self.connections if not c.closing]
            oldest = None
            if len(live) > MAX_CONNECTIONS:
                counts = {}
                for c in live:
                    counts[c.address[0]] = counts.get(c.address[0], 0) + 1
                top = max(counts, key=lambda a: (counts[a], a == conn.address[0]))
                oldest = min((c for c in live if c.address[0] == top),
                             key=lambda c: c.last_active)
                oldest.closing = True
        if oldest is not None:
            self.log("NFS: {} connections open; closing the quietest from {}".format(
                MAX_CONNECTIONS + 1, oldest.address[0]))
            self._shut(oldest)

    def _serve(self, conn):
        if self.verbose:
            self.log("NFS: {} connected from port {}".format(*conn.address))
        try:
            while True:
                record = self._read_record(conn)
                if record is None:
                    break
                conn.last_active = time.monotonic()
                reply = self.handle_record(record, conn)
                if reply is not None:
                    conn.sock.sendall(_p32(0x80000000 | len(reply)) + reply)
        except (OSError, ValueError):
            pass
        finally:
            conn.close_files()
            with self.lock:
                self.connections.discard(conn)
            try:
                conn.sock.close()
            except OSError:
                pass
            if self.verbose:
                self.log("NFS: {} disconnected".format(conn.address[0]))

    def _recv(self, conn, n):
        buf = bytearray(n)
        view = memoryview(buf)
        got = 0
        while got < n:
            k = conn.sock.recv_into(view[got:])
            if not k:
                return None
            got += k
        return bytes(buf)

    def _read_record(self, conn):
        # Read handles stay open only through a burst of reads, so a quiet
        # console never holds a file that Windows would then refuse to rename.
        if conn.files:
            ready = select.select([conn.sock], [], [], FILE_IDLE_SECONDS)[0]
            if not ready:
                conn.close_files()
        parts, total = [], 0
        while True:
            mark = self._recv(conn, 4)
            if mark is None:
                return None
            mark = struct.unpack(">I", mark)[0]
            total += 4 + (mark & 0x7fffffff)  # the mark too: empty fragments count
            if total > MAX_RECORD:
                raise ValueError("record too large")
            part = self._recv(conn, mark & 0x7fffffff)
            if part is None:
                return None
            parts.append(part)
            if mark & 0x80000000:
                return b"".join(parts)

    # -- RPC -----------------------------------------------------------------

    @staticmethod
    def _accepted(xid, accept_stat, body=b""):
        return (_p32(xid) + _p32(_REPLY) + _p32(_MSG_ACCEPTED) + _p32(_AUTH_NONE)
                + _p32(0) + _p32(accept_stat) + body)

    @staticmethod
    def _denied(xid, body):
        return _p32(xid) + _p32(_REPLY) + _p32(_MSG_DENIED) + body

    def handle_record(self, record, conn=None):
        """One RPC call in, its reply out (None: nothing to answer)."""
        r = _Reader(record)
        try:
            xid, msg_type = r.u32(), r.u32()
        except XdrError:
            return None
        if msg_type != _CALL:
            return None
        try:
            rpcvers, prog, vers, proc = r.u32(), r.u32(), r.u32(), r.u32()
            flavor = r.u32()
            r.opaque()
            r.u32()
            r.opaque()
        except XdrError:
            return self._accepted(xid, _GARBAGE_ARGS)
        if rpcvers != 2:
            return self._denied(xid, _p32(_RPC_MISMATCH) + _p32(2) + _p32(2))
        if flavor not in (_AUTH_NONE, _AUTH_SYS):
            return self._denied(xid, _p32(_AUTH_ERROR) + _p32(_AUTH_TOOWEAK))
        if prog != _NFS_PROGRAM:
            return self._accepted(xid, _PROG_UNAVAIL)
        if vers != _NFS_V4:
            return self._accepted(xid, _PROG_MISMATCH, _p32(_NFS_V4) + _p32(_NFS_V4))
        if proc == 0:
            return self._accepted(xid, _SUCCESS)
        if proc != 1:
            return self._accepted(xid, _PROC_UNAVAIL)
        try:
            body = self._compound(r, conn)
        except XdrError:
            return self._accepted(xid, _GARBAGE_ARGS)
        return self._accepted(xid, _SUCCESS, body)

    def _compound(self, r, conn):
        tag = r.opaque()
        minor = r.u32()
        count = r.u32()
        results = []
        status = NFS4_OK
        if minor != 0:
            status = NFS4ERR_MINOR_VERS_MISMATCH
        elif count > MAX_OPS:
            status = NFS4ERR_RESOURCE
        else:
            state = _Compound(conn)
            size = 0
            for _ in range(count):
                op = r.u32()  # a call cut short here is GARBAGE_ARGS
                if size >= MAX_IO:  # one full READ per reply, not 127 of them
                    status, body = NFS4ERR_RESOURCE, b""
                else:
                    status, body = self._one(op, r, state)
                if op < _FIRST_OP or op > _LAST_OP:
                    op = OP_ILLEGAL
                if status == NFS4_OK:
                    results.append(_p32(op) + _p32(NFS4_OK) + body)
                    size += len(body)
                    continue
                # SETATTR's result carries its bitmap even when it fails.
                tail = _EMPTY_BITMAP if op == OP_SETATTR else b""
                results.append(_p32(op) + _p32(status) + tail)
                if self.verbose:
                    self.log("NFS: {} -> error {}".format(_OP_NAMES.get(op, op), status))
                break
        return _p32(status) + _opaque(tag) + _p32(len(results)) + b"".join(results)

    def _one(self, op, r, state):
        if op < _FIRST_OP or op > _LAST_OP:
            return NFS4ERR_OP_ILLEGAL, b""
        handler = self.ops.get(op)
        if handler is None:
            return NFS4ERR_NOTSUPP, b""
        try:
            with self.fs_lock:
                return NFS4_OK, handler(r, state)
        except XdrError:
            return NFS4ERR_BADXDR, b""
        except Nfs4Error as exc:
            return exc.status, b""
        except PathError as exc:
            return _PATH_STATUS.get(exc.status, NFS4ERR_BADNAME), b""
        except OSError as exc:
            return _status_of(exc), b""
        except Exception as exc:  # a bug here must not take the connection down
            self.log("NFS: {} failed: {!r}".format(_OP_NAMES.get(op, op), exc))
            return NFS4ERR_SERVERFAULT, b""

    # -- paths ---------------------------------------------------------------

    def _real(self, comps):
        return self.share.resolve("/".join(comps))

    def _object(self, comps):
        """Real path and stat of a filehandle's object; STALE once it is gone."""
        try:
            real = self._real(comps)
            st = os.stat(real)
        except (FileNotFoundError, PathError):
            raise Nfs4Error(NFS4ERR_STALE)
        if not (stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode)):
            raise Nfs4Error(NFS4ERR_STALE)
        return real, st

    @staticmethod
    def _need(fh):
        if fh is None:
            raise Nfs4Error(NFS4ERR_NOFILEHANDLE)
        return fh

    def _dir(self, comps):
        real, st = self._object(self._need(comps))
        if not stat.S_ISDIR(st.st_mode):
            raise Nfs4Error(NFS4ERR_NOTDIR)
        return real, st

    @staticmethod
    def _name(raw):
        if not raw:
            raise Nfs4Error(NFS4ERR_INVAL)
        if len(raw) > 255:
            raise Nfs4Error(NFS4ERR_NAMETOOLONG)
        name = raw.decode("utf-8", "surrogateescape")
        if name in (".", "..") or "/" in name or "\\" in name or "\0" in name:
            raise Nfs4Error(NFS4ERR_BADNAME)
        check_component(name)
        return name

    def _child(self, parent_comps, raw):
        """A name in a directory: its components and its path on disk.

        The path is the parent's real path joined with the name, so REMOVE and
        RENAME act on a symlink itself rather than on what it points at. The
        name still has to resolve inside the share.
        """
        name = self._name(raw)
        parent_real, _ = self._dir(parent_comps)
        comps = parent_comps + (name,)
        self._real(comps)
        return comps, os.path.join(parent_real, name)

    def _writable(self, s=None):
        if not self.allow_writes:
            raise Nfs4Error(NFS4ERR_ROFS)
        # Windows will not delete or rename a file that is open, and the
        # likeliest holder is this connection's own read cache: a file manager
        # copies a file and then deletes it.
        if s is not None and s.conn is not None:
            s.conn.close_files()

    def _changed(self, comps=None):
        with self.lock:
            self.generation += 1
            if comps is not None:
                self.dir_cache.clear()

    def _cinfo(self, before, real_dir):
        try:
            after = os.stat(real_dir).st_mtime_ns
        except OSError:
            after = before + 1
        return _p32(0) + _p64(before & 0xFFFFFFFFFFFFFFFF) + _p64(after & 0xFFFFFFFFFFFFFFFF)

    def _stateid(self, seqid):
        with self.lock:
            self.counter += 1
            n = self.counter
        return _p32(seqid & 0xFFFFFFFF) + self.stateid_other + _p64(n)

    # -- attributes ----------------------------------------------------------

    @staticmethod
    def _attrs(want, kind, size, mtime_ns):
        bits = [b for b in _GETTABLE if b in want]
        values = b""
        for bit in bits:
            if bit == _FATTR4_TYPE:
                values += _p32(kind)
            elif bit == _FATTR4_SIZE:
                values += _p64(size)
            else:
                seconds, nanos = divmod(mtime_ns, 1000000000)
                values += struct.pack(">qI", seconds, nanos)
        return _bitmap(bits) + _opaque(values)

    @staticmethod
    def _kind(st):
        return NF4DIR if stat.S_ISDIR(st.st_mode) else NF4REG

    # -- read operations -----------------------------------------------------

    def _op_putrootfh(self, r, s):
        s.cfh = ()
        return b""

    def _op_putfh(self, r, s):
        fh = r.opaque()
        if len(fh) > MAX_FH:
            raise Nfs4Error(NFS4ERR_BADHANDLE)
        s.cfh = self.handles.lookup(fh)[1]
        return b""

    def _op_getfh(self, r, s):
        return _opaque(self.handles.handle(self._need(s.cfh)))

    def _op_savefh(self, r, s):
        s.sfh = self._need(s.cfh)
        return b""

    def _op_lookup(self, r, s):
        raw = r.opaque()
        name = self._name(raw)
        self._dir(s.cfh)
        comps = s.cfh + (name,)
        try:
            st = os.stat(self._real(comps))
        except FileNotFoundError:
            raise Nfs4Error(NFS4ERR_NOENT)
        if not (stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode)):
            raise Nfs4Error(NFS4ERR_NOENT)
        s.cfh = comps
        return b""

    def _op_getattr(self, r, s):
        want = r.bitmap()
        _real, st = self._object(self._need(s.cfh))
        return self._attrs(want, self._kind(st), st.st_size, st.st_mtime_ns)

    def _op_read(self, r, s):
        r.fixed(16)  # stateid: any, since nothing is ever locked
        offset, count = r.u64(), r.u32()
        comps = self._need(s.cfh)
        number = self.handles.handle(comps)
        number = struct.unpack(">Q", number[8:])[0]
        conn = s.conn
        cached = conn.files.get(number) if conn is not None else None
        if cached is None or cached[0] != self.generation:
            if cached is not None:
                cached[1].close()
                del conn.files[number]
            real, st = self._object(comps)
            if stat.S_ISDIR(st.st_mode):
                raise Nfs4Error(NFS4ERR_ISDIR)
            f = open(real, "rb")
            if conn is not None:
                if len(conn.files) >= 4:
                    oldest = next(iter(conn.files))
                    conn.files.pop(oldest)[1].close()
                conn.files[number] = (self.generation, f)
        else:
            f = cached[1]
        try:
            size = os.fstat(f.fileno()).st_size
            count = min(count, MAX_IO)
            data = b""
            if offset < size:
                f.seek(offset)
                data = f.read(count)
        finally:
            if conn is None:
                f.close()
        eof = offset + len(data) >= size
        return _p32(1 if eof else 0) + _opaque(data)

    def _listing(self, number, comps, real, st, fresh):
        with self.lock:
            cached = self.dir_cache.get(number)
        if (not fresh and cached is not None and cached[0] == self.generation
                and cached[1] == st.st_mtime_ns):
            return cached[2]
        entries = []
        with os.scandir(real) as it:
            for entry in it:
                try:
                    check_component(entry.name)
                    raw = entry.name.encode("utf-8", "surrogateescape")
                    if entry.is_symlink() and not self.share._inside(
                            os.path.realpath(entry.path)):
                        continue
                    est = entry.stat()
                except (PathError, OSError, UnicodeEncodeError):
                    continue
                if not (stat.S_ISDIR(est.st_mode) or stat.S_ISREG(est.st_mode)):
                    continue
                entries.append((raw, self._kind(est), est.st_size, est.st_mtime_ns))
        entries.sort()
        with self.lock:
            if len(self.dir_cache) >= 64:
                self.dir_cache.clear()
            self.dir_cache[number] = (self.generation, st.st_mtime_ns, entries)
        return entries

    def _op_readdir(self, r, s):
        cookie = r.u64()
        r.fixed(8)            # cookie verifier: always zero here
        r.u32()               # dircount: a hint, and maxcount is the limit
        maxcount = r.u32()
        want = r.bitmap()
        comps = self._need(s.cfh)
        real, st = self._dir(comps)
        if cookie in (1, 2):
            raise Nfs4Error(NFS4ERR_BAD_COOKIE)
        number = struct.unpack(">Q", self.handles.handle(comps)[8:])[0]
        entries = self._listing(number, comps, real, st, cookie == 0)
        start = cookie - 2 if cookie else 0
        # cookie verifier, then the entries, then "no more" and eof
        budget = min(maxcount, MAX_READDIR) - 16
        out, used, i = [], 0, start
        while i < len(entries):
            raw, kind, size, mtime_ns = entries[i]
            entry = (_p32(1) + _p64(i + 3) + _opaque(raw)
                     + self._attrs(want, kind, size, mtime_ns))
            if used + len(entry) > budget:
                break
            out.append(entry)
            used += len(entry)
            i += 1
        eof = i >= len(entries)
        if not out and not eof:
            raise Nfs4Error(NFS4ERR_TOOSMALL)
        return b"\0" * 8 + b"".join(out) + _p32(0) + _p32(1 if eof else 0)

    # -- state for creating files --------------------------------------------

    def _op_setclientid(self, r, s):
        r.fixed(8)
        r.opaque()
        r.u32()
        r.opaque()
        r.opaque()
        r.u32()
        confirm = secrets.token_bytes(8)
        with self.lock:
            self.counter += 1
            clientid = self.counter
            if len(self.clients) >= 256:
                self.clients.pop(next(iter(self.clients)))
            self.clients[clientid] = confirm
        return _p64(clientid) + confirm

    def _op_setclientid_confirm(self, r, s):
        clientid, confirm = r.u64(), r.fixed(8)
        with self.lock:
            if self.clients.get(clientid) != confirm:
                raise Nfs4Error(NFS4ERR_STALE_CLIENTID)
        return b""

    def _op_open_confirm(self, r, s):
        stateid = r.fixed(16)
        r.u32()
        self._need(s.cfh)
        return _p32((struct.unpack(">I", stateid[:4])[0] + 1) & 0xFFFFFFFF) + stateid[4:]

    def _op_close(self, r, s):
        r.u32()
        stateid = r.fixed(16)
        self._need(s.cfh)
        return _p32((struct.unpack(">I", stateid[:4])[0] + 1) & 0xFFFFFFFF) + stateid[4:]

    def _op_open(self, r, s):
        r.u32()                                   # seqid
        access, _deny = r.u32(), r.u32()
        r.u64()                                   # owner: client id ...
        r.opaque()                                # ... and name
        create = r.u32()
        attrs, mode = {}, None
        if create == _OPEN4_CREATE:
            mode = r.u32()
            if mode not in (_UNCHECKED4, _GUARDED4):
                raise Nfs4Error(NFS4ERR_NOTSUPP)  # EXCLUSIVE4
            attrs = _settable(r)
        elif create != 0:
            raise XdrError()
        if r.u32() != _CLAIM_NULL:
            raise Nfs4Error(NFS4ERR_NOTSUPP)
        raw = r.opaque()
        if access not in (1, 2, 3):
            raise Nfs4Error(NFS4ERR_INVAL)
        if create == _OPEN4_CREATE or access & _SHARE_ACCESS_WRITE:
            self._writable()
        parent_real, parent_st = self._dir(s.cfh)
        comps, path = self._child(s.cfh, raw)
        exists = os.path.lexists(path)
        if create == _OPEN4_CREATE:
            if exists and mode == _GUARDED4:
                raise Nfs4Error(NFS4ERR_EXIST)
            if not exists:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                             | getattr(os, "O_BINARY", 0), 0o666)
                os.close(fd)
                self.log("NFS: created {}".format("/".join(comps)))
            if "size" in attrs:
                os.truncate(path, attrs["size"])
            self._changed(comps)
            if not exists:
                _sync_dir(parent_real)  # after the bookkeeping: a failed sync must not leave it stale
        st = os.stat(path)
        if stat.S_ISDIR(st.st_mode):
            raise Nfs4Error(NFS4ERR_ISDIR)
        if not stat.S_ISREG(st.st_mode):
            raise Nfs4Error(NFS4ERR_INVAL)
        s.cfh = comps
        attrset = _bitmap([_FATTR4_SIZE]) if "size" in attrs else _EMPTY_BITMAP
        return (self._stateid(1) + self._cinfo(parent_st.st_mtime_ns, parent_real)
                + _p32(0) + attrset + _p32(0))  # no confirm needed, no delegation

    # -- write operations ----------------------------------------------------

    def _op_write(self, r, s):
        r.fixed(16)
        offset = r.u64()
        r.u32()                                   # stable: always FILE_SYNC here
        data = r.opaque()
        comps = self._need(s.cfh)
        self._writable()
        real, st = self._object(comps)
        if stat.S_ISDIR(st.st_mode):
            raise Nfs4Error(NFS4ERR_ISDIR)
        if offset + len(data) > 0x7FFFFFFFFFFFFFFF:
            raise Nfs4Error(NFS4ERR_FBIG)
        with open(real, "r+b") as f:
            f.seek(offset)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())  # unfs4 asks for FILE_SYNC and never COMMITs
        self._changed()
        return _p32(len(data)) + _p32(_FILE_SYNC4) + self.write_verifier

    def _op_setattr(self, r, s):
        r.fixed(16)
        attrs = _settable(r)
        comps = self._need(s.cfh)
        self._writable()
        real, st = self._object(comps)
        if "size" not in attrs:
            return _EMPTY_BITMAP
        if stat.S_ISDIR(st.st_mode):
            raise Nfs4Error(NFS4ERR_ISDIR)
        os.truncate(real, attrs["size"])
        self.log("NFS: set the size of {} to {}".format("/".join(comps), attrs["size"]))
        self._changed(comps)
        return _bitmap([_FATTR4_SIZE])

    def _op_create(self, r, s):
        if r.u32() != NF4DIR:
            raise Nfs4Error(NFS4ERR_BADTYPE)
        raw = r.opaque()
        _settable(r)
        self._writable()
        parent_real, parent_st = self._dir(s.cfh)
        comps, path = self._child(s.cfh, raw)
        os.mkdir(path)
        self.log("NFS: made folder {}".format("/".join(comps)))
        self._changed(comps)
        _sync_dir(parent_real)
        s.cfh = comps
        return self._cinfo(parent_st.st_mtime_ns, parent_real) + _EMPTY_BITMAP

    def _op_remove(self, r, s):
        raw = r.opaque()
        self._writable(s)
        parent_real, parent_st = self._dir(s.cfh)
        comps, path = self._child(s.cfh, raw)
        st = os.lstat(path)
        if stat.S_ISDIR(st.st_mode):
            os.rmdir(path)
        else:
            os.remove(path)
        self.log("NFS: deleted {}".format("/".join(comps)))
        self.handles.removed(comps)
        self._changed(comps)
        return self._cinfo(parent_st.st_mtime_ns, parent_real)

    def _op_rename(self, r, s):
        old_raw, new_raw = r.opaque(), r.opaque()
        src_dir, dst_dir = self._need(s.sfh), self._need(s.cfh)
        self._writable(s)
        src_real, src_st = self._dir(src_dir)
        dst_real, dst_st = self._dir(dst_dir)
        old, src = self._child(src_dir, old_raw)
        new, dst = self._child(dst_dir, new_raw)
        src_lst = os.lstat(src)
        try:
            dst_lst = os.lstat(dst)
        except FileNotFoundError:
            dst_lst = None
        if dst_lst is not None and os.path.samestat(src_lst, dst_lst):
            if src != dst:
                os.rename(src, dst)  # a change of case on a Windows folder
        elif dst_lst is not None:
            src_is_dir = stat.S_ISDIR(src_lst.st_mode)
            if src_is_dir != stat.S_ISDIR(dst_lst.st_mode):
                raise Nfs4Error(NFS4ERR_EXIST)
            if src_is_dir:
                if os.listdir(dst):
                    raise Nfs4Error(NFS4ERR_EXIST)
                os.rmdir(dst)
                os.rename(src, dst)
            else:
                os.replace(src, dst)
        else:
            os.rename(src, dst)
        self.log("NFS: renamed {} to {}".format("/".join(old), "/".join(new)))
        if old != new:  # onto itself: nothing moved, every handle stays
            self.handles.renamed(old, new)
        self._changed(old)
        for folder in {src_real, dst_real}:  # after the handles: a failed sync must not leave them stale
            _sync_dir(folder)
        return (self._cinfo(src_st.st_mtime_ns, src_real)
                + self._cinfo(dst_st.st_mtime_ns, dst_real))


def build_parser():
    parser = argparse.ArgumentParser(
        prog="ps2servers-nfs",
        description="Serve a games folder over NFS version 4.0 for Neutrino and "
                    "the nfs: driver (unfs4).")
    # Long flags only: the packaged launcher re-executes itself as
    # `--serve nfs ...`, and Nuitka's self-exec guard only tolerates long
    # options on that path.
    parser.add_argument("--root-dir", dest="root", required=True,
                        help="Games folder to share; the PS2 side uses export /")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help="TCP port (default 2049, which every PS2 client uses)")
    parser.add_argument("--bind", default="", help="Interface to bind (blank = all)")
    parser.add_argument("--allow-writes", action="store_true",
                        help="Let clients create, change, rename and delete files. "
                             "NFS has no password: anything that can reach the "
                             "port can then change the folder.")
    parser.add_argument("--verbose", action="store_true",
                        help="Log connections and every failed operation")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    root = os.path.realpath(args.root)
    if not os.path.isdir(root):
        _log("Games folder not found: {}".format(root))
        return 1
    if not 1 <= args.port <= 65535:
        _log("Port out of range: {}".format(args.port))
        return 2
    server = Nfs4Server(root, allow_writes=args.allow_writes, verbose=args.verbose)
    try:
        port = server.listen(args.bind, args.port)
    except OSError as exc:
        _log("Cannot bind TCP {}:{} -- {}. Another NFS server (on Linux, the "
             "kernel's nfsd) may already be using it.".format(
                 args.bind or "0.0.0.0", args.port, exc))
        return 1
    _log("NFS server (version 4.0) listening on TCP {}:{}".format(args.bind or "0.0.0.0", port))
    _log("Sharing {} as export /".format(root))
    if args.allow_writes:
        _log("Writes: ON. NFS has no password, so any device that can reach this "
             "port can change or delete files in this folder.")
    else:
        _log("Writes: OFF (read-only). Consoles can list and read, nothing else.")
    _stop_on_sigterm()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        _log("Stopping.")
    finally:
        server.stop()
    return 0


def _stop_on_sigterm():
    def _raise(_signum, _frame):
        raise KeyboardInterrupt()

    if hasattr(signal, "SIGTERM") and threading.current_thread() is threading.main_thread():
        try:
            signal.signal(signal.SIGTERM, _raise)
        except (ValueError, OSError):
            pass


if __name__ == "__main__":
    sys.exit(main())
