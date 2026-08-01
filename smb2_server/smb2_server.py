"""An SMB2/SMB3 server for PS2 Servers.

One server, two launcher modes. SMB2 and SMB3 are the same protocol family --
SMB3 is a higher dialect with crypto on top -- so the dialect ceiling is a flag
(--smb-version 2 or 3) rather than a second implementation to keep in step.

Why this exists rather than more handlers bolted onto smbv1_server:

  * Windows 10 and 11 ship with SMB1 disabled. Today nobody can browse their own
    games folder from a modern PC without turning an insecure protocol back on.
  * OPL is gaining an SMB2/3 client, so these modes have a console ahead of them
    as well as a file manager.
  * SMB1 here is guest-only by design and has no authentication at all. Real
    SMB2 clients require NTLMSSP, so the credential path had to be built rather
    than skipped -- see smb2_auth.py.

What it is NOT, stated plainly because a half-implemented SMB2 already shipped
once in this project and cost a release's worth of trust:

  * No signing and no encryption. Both are AES-CMAC/AES-CCM below SMB 3.1.1, and
    this repo is stdlib-only -- Python has no AES, and a pure-Python one would
    have to run over every byte of every transfer. The server therefore
    advertises signing as enabled-but-not-required, which is what a default
    Windows client accepts. A client whose policy REQUIRES signing will refuse
    this server, and that is an honest refusal rather than a broken transfer.
  * Dialect 3.1.1 is not offered. It needs negotiate contexts (a
    pre-authentication integrity hash the client verifies), and claiming it
    without them makes Windows drop the connection. 3.0.2 is negotiated instead,
    which every SMB3 client speaks.
  * No oplocks, leases, or change notification. A client polls instead.

Run it directly:

    python smb2_server/smb2_server.py --share games=/path/to/games \\
        --user ripto:secret --port 1445 --smb-version 3
"""

import argparse
import errno
import os
import socket
import stat as statmod
import struct
import sys
import threading
import time

from smb2_auth import AuthError, Authenticator, server_challenge
from smb2_paths import PathError, Share
import smb2_spnego as spnego
import smb2_wire as wire

VERBOSE = False


def log(*a):
    if VERBOSE:
        print("  [smb2]", *a, file=sys.stderr, flush=True)


def note(*a):
    print("[smb2]", *a, file=sys.stderr, flush=True)


class Handle:
    """One open file or directory, and everything the client can ask about it."""

    __slots__ = ("file_id", "share", "path", "rel", "is_dir", "fh",
                 "delete_on_close", "listing", "cursor")

    def __init__(self, file_id, share, path, rel, is_dir):
        self.file_id = file_id
        self.share = share
        self.path = path
        self.rel = rel
        self.is_dir = is_dir
        self.fh = None
        self.delete_on_close = False
        # Enumeration state. A directory listing is snapshotted at the first
        # QUERY_DIRECTORY and walked across calls: re-reading the directory on
        # every call would skip or repeat entries whenever anything changed
        # underneath, which is how a listing silently loses a game.
        self.listing = None
        self.cursor = 0

    def close(self):
        if self.fh is not None:
            try:
                self.fh.close()
            except OSError:
                pass
            self.fh = None


class Smb2Error(Exception):
    """A failure that already knows the NT status it becomes on the wire."""

    def __init__(self, status, detail=""):
        super().__init__(detail or ("0x%08X" % status))
        self.status = status
        self.detail = detail


class Connection:
    """Per-TCP state. One client, one dialect, one session, many handles."""

    def __init__(self, server, sock, peer):
        self.server = server
        self.sock = sock
        self.peer = peer
        self.dialect = None
        self.session_id = 0
        self.authenticated = False
        self.user = ""
        self.challenge = None
        self.trees = {}          # tree_id -> Share
        self.handles = {}        # volatile id -> Handle
        self._next_tree = 1
        self._next_handle = 1

    # -- handle plumbing --------------------------------------------------- #
    def add_handle(self, share, path, rel, is_dir):
        hid = self._next_handle
        self._next_handle += 1
        h = Handle(hid, share, path, rel, is_dir)
        self.handles[hid] = h
        return h

    def handle_from(self, raw_file_id):
        """The Handle a 16-byte FileId names.

        Both halves are checked. A client that echoes a stale persistent id with
        a fresh volatile one is confused about which file it has open, and
        serving it whatever the volatile half points at would hand it the
        contents of a different file under the name it thinks it asked for.
        """
        persistent, volatile = struct.unpack("<QQ", raw_file_id)
        h = self.handles.get(volatile)
        if h is None or persistent != volatile:
            raise Smb2Error(wire.STATUS_FILE_CLOSED, "no such open handle")
        return h

    def close_all(self):
        for h in list(self.handles.values()):
            h.close()
        self.handles.clear()


class Smb2Server:
    def __init__(self, shares, authenticator, ceiling=wire.CEILING_SMB2,
                 read_only=False, server_name="PS2SERVERS", domain="WORKGROUP"):
        self.shares = shares
        self.auth = authenticator
        self.ceiling = ceiling
        self.read_only = read_only
        self.server_name = server_name
        self.domain = domain
        self.guid = os.urandom(16)
        self.start_time = wire.to_filetime(time.time())
        self.sock = None
        self._stop = threading.Event()

    # -- lifecycle --------------------------------------------------------- #
    def listen(self, bind, port, backlog=8):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((bind, port))
        self.sock.listen(backlog)
        return self.sock.getsockname()[1]

    def serve_forever(self):
        while not self._stop.is_set():
            try:
                client, peer = self.sock.accept()
            except OSError:
                if self._stop.is_set():
                    return
                raise
            t = threading.Thread(target=self._serve_client, args=(client, peer),
                                 daemon=True)
            t.start()

    def stop(self):
        self._stop.set()
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass

    def _serve_client(self, sock, peer):
        conn = Connection(self, sock, peer)
        log("connect from", peer)
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            while not self._stop.is_set():
                msg = wire.recv_msg(sock)
                if msg is None:
                    break
                if msg == b"":
                    continue
                self._dispatch_all(conn, msg)
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            log("client", peer, "went away")
        except OSError as e:
            log("connection error from", peer, e)
        finally:
            conn.close_all()
            try:
                sock.close()
            except OSError:
                pass
            log("disconnect", peer)

    # -- dispatch ---------------------------------------------------------- #
    def _dispatch_all(self, conn, msg):
        """Handle one message, or every message in a compound one.

        A client is entitled to chain requests with NextCommand and expects the
        replies chained the same way. Answering only the first is why a compound
        CREATE/QUERY_INFO/CLOSE -- which is how Explorer opens a file -- would
        appear to hang.
        """
        replies = []
        offset = 0
        while offset < len(msg):
            hdr = wire.parse_header(msg[offset:])
            if hdr is None:
                return
            end = offset + hdr.next_command if hdr.next_command else len(msg)
            body = msg[offset + wire.HEADER_SIZE:end]
            status, reply_body, tree_id = self._handle(conn, hdr, body)
            # A CANCEL is answered with silence. Replying to one is itself a
            # protocol error, so "no reply" has to be representable.
            if status is not None:
                replies.append((hdr, status, reply_body, tree_id))
            if not hdr.next_command:
                break
            offset = end

        out = b""
        for i, (hdr, status, reply_body, tree_id) in enumerate(replies):
            last = i == len(replies) - 1
            padded = reply_body
            next_command = 0
            if not last:
                total = wire.HEADER_SIZE + len(reply_body)
                aligned = (total + 7) & ~7
                padded = reply_body + b"\x00" * (aligned - total)
                next_command = aligned
            out += wire.pack_header(
                hdr.command, status, hdr.message_id,
                tree_id=hdr.tree_id if tree_id is None else tree_id,
                session_id=conn.session_id or hdr.session_id,
                next_command=next_command) + padded
        if out:
            wire.send_msg(conn.sock, out)

    def _handle(self, conn, hdr, body):
        """(status, body, tree_id). status None means send nothing at all."""
        handler = _HANDLERS.get(hdr.command)
        if handler is None:
            log("unhandled command", hdr)
            return wire.STATUS_NOT_SUPPORTED, wire.error_body(), None
        try:
            result = handler(self, conn, hdr, body)
            # TREE_CONNECT is the one handler that decides the header's TreeId,
            # so handlers may return a third value to override it.
            if len(result) == 3:
                return result
            status, reply_body = result
            return status, reply_body, None
        except Smb2Error as e:
            log(hdr, "->", e.detail)
            return e.status, wire.error_body(), None
        except PathError as e:
            # The path guard already decided which refusal this is; carry its
            # answer rather than flattening everything to ACCESS_DENIED.
            log(hdr, "-> path refused:", e)
            status = wire.STATUS_BY_NAME.get(
                getattr(e, "status", ""), wire.STATUS_ACCESS_DENIED)
            return status, wire.error_body(), None
        except AuthError as e:
            log(hdr, "-> auth refused:", e)
            return (wire.STATUS_BY_NAME.get(getattr(e, "status", ""),
                                            wire.STATUS_LOGON_FAILURE),
                    wire.error_body(), None)
        except OSError as e:
            return _status_for_oserror(e), wire.error_body(), None
        except Exception as e:                       # never take the server down
            note("internal error handling", hdr, "->", repr(e))
            return wire.STATUS_INVALID_PARAMETER, wire.error_body(), None

    # -- guards ------------------------------------------------------------ #
    def _require_session(self, conn):
        if not conn.authenticated:
            raise Smb2Error(wire.STATUS_USER_SESSION_DELETED, "no session")

    def _tree(self, conn, hdr):
        self._require_session(conn)
        share = conn.trees.get(hdr.tree_id)
        if share is None:
            raise Smb2Error(wire.STATUS_NETWORK_NAME_DELETED, "no such tree")
        return share

    def _writable(self, share):
        if self.read_only or share.read_only:
            raise Smb2Error(wire.STATUS_MEDIA_WRITE_PROTECTED,
                            "share is read-only")


def request_buffer(body, offset, length, what, least=wire.HEADER_SIZE):
    """The slice a request declares, bounds-checked.

    SMB2 offsets are measured from the start of the MESSAGE, header included, so
    a valid one is never below 64. Subtracting the header from a smaller value
    gives a negative index, and Python then slices from the END of the body
    instead of failing -- so a WRITE with DataOffset 0 wrote whatever bytes
    happened to be there, at the offset the client asked for, and reported
    success. Everything the client sent is its own, so nothing leaks; what it
    loses is the file it was writing to.

    Bounds are checked once, here, rather than at each of the six call sites,
    because five of them only ended in a confusing refusal and were therefore
    easy to leave alone.
    """
    if length == 0:
        return b""
    start = offset - wire.HEADER_SIZE
    # `least` is the end of the request's own fixed structure. A buffer that
    # starts before it overlaps the fields that describe it, which no client can
    # have meant -- for WRITE it meant the file received the request header.
    if offset < least or start < 0 or length < 0 or start + length > len(body):
        raise Smb2Error(wire.STATUS_INVALID_PARAMETER,
                        "%s buffer offset %d length %d does not fit a %d-byte body"
                        % (what, offset, length, len(body)))
    return body[start:start + length]


def _status_for_oserror(e):
    if e.errno in (errno.ENOENT,):
        return wire.STATUS_OBJECT_NAME_NOT_FOUND
    if e.errno in (errno.EACCES, errno.EPERM):
        return wire.STATUS_ACCESS_DENIED
    if e.errno in (errno.EEXIST,):
        return wire.STATUS_OBJECT_NAME_COLLISION
    if e.errno in (errno.EISDIR,):
        return wire.STATUS_FILE_IS_A_DIRECTORY
    if e.errno in (errno.ENOTDIR,):
        return wire.STATUS_NOT_A_DIRECTORY
    if e.errno in (errno.ENOSPC,):
        return wire.STATUS_DISK_FULL
    if e.errno in (errno.ENOTEMPTY,):
        return wire.STATUS_DIRECTORY_NOT_EMPTY
    return wire.STATUS_INVALID_PARAMETER


# --------------------------------------------------------------------------- #
# handlers
# --------------------------------------------------------------------------- #

def h_negotiate(srv, conn, hdr, body):
    if len(body) < 36:
        raise Smb2Error(wire.STATUS_INVALID_PARAMETER, "short NEGOTIATE")
    _size, dialect_count, _mode, _res, _caps = struct.unpack_from("<HHHHI", body, 0)
    offered = []
    for i in range(dialect_count):
        at = 36 + i * 2
        if at + 2 > len(body):
            break
        offered.append(struct.unpack_from("<H", body, at)[0])

    dialect = wire.pick_dialect(offered, srv.ceiling)
    if dialect is None:
        raise Smb2Error(wire.STATUS_NOT_SUPPORTED,
                        "no common dialect; client offered %s" %
                        ", ".join("0x%04x" % d for d in offered))
    conn.dialect = dialect
    log("negotiated dialect 0x%04x with %s" % (dialect, conn.peer))

    token = spnego.neg_token_init()
    body_out = struct.pack(
        "<HHHH16sIIIIQQHHI",
        65,                                     # StructureSize
        wire.SECURITY_MODE_SIGNING_ENABLED,     # enabled, never required: see module docstring
        dialect,
        0,                                      # NegotiateContextCount (3.1.1 only)
        srv.guid,
        wire.GLOBAL_CAP_LARGE_MTU,
        wire.MAX_TRANSACT, wire.MAX_READ, wire.MAX_WRITE,
        wire.to_filetime(time.time()),
        srv.start_time,
        wire.HEADER_SIZE + 64,                  # SecurityBufferOffset
        len(token),
        0,                                      # NegotiateContextOffset
    )
    return wire.STATUS_SUCCESS, body_out + token


def h_session_setup(srv, conn, hdr, body):
    if len(body) < 24:
        raise Smb2Error(wire.STATUS_INVALID_PARAMETER, "short SESSION_SETUP")
    _size, _flags, _mode, _caps, _chan, buf_off, buf_len, _prev = struct.unpack_from(
        "<HBBIIHHQ", body, 0)
    blob = request_buffer(body, buf_off, buf_len, "SESSION_SETUP security",
                          least=wire.HEADER_SIZE + 24)
    token = spnego.extract_ntlm(blob)
    if token is None:
        raise Smb2Error(wire.STATUS_LOGON_FAILURE, "no NTLMSSP token offered")

    kind = spnego.message_type(token)
    if kind == spnego.NTLM_NEGOTIATE:
        # Round one: hand back a challenge and ask for the proof. The challenge
        # is kept per connection -- it is what the client's response is computed
        # against, so a shared or reused one would let a captured response be
        # replayed onto another connection.
        conn.challenge = server_challenge()
        chal = spnego.build_challenge(conn.challenge, srv.domain, srv.server_name)
        out = spnego.neg_token_resp(spnego.ACCEPT_INCOMPLETE, chal, with_mech=True)
        if conn.session_id == 0:
            conn.session_id = int.from_bytes(os.urandom(8), "little") or 1
        body_out = struct.pack("<HHHH", 9, 0, wire.HEADER_SIZE + 8, len(out))
        return wire.STATUS_MORE_PROCESSING_REQUIRED, body_out + out

    if kind != spnego.NTLM_AUTHENTICATE:
        raise Smb2Error(wire.STATUS_LOGON_FAILURE,
                        "unexpected NTLMSSP message type %r" % (kind,))

    if conn.challenge is None:
        raise Smb2Error(wire.STATUS_LOGON_FAILURE,
                        "AUTHENTICATE without a challenge")

    creds = spnego.parse_authenticate(token)
    session_flags = 0
    if creds.is_anonymous:
        # A null session proves nothing. It is accepted only where the operator
        # has already said the share needs no password; otherwise it is a
        # rejection, not a guest login, so that "no password configured" and
        # "anonymous allowed" can never be the same state by accident.
        if not srv.auth.is_open:
            raise Smb2Error(wire.STATUS_LOGON_FAILURE,
                            "anonymous session refused; this share needs a password")
        session_flags = wire.SESSION_FLAG_IS_NULL
    else:
        # Pass the domain through exactly as claimed, including empty. NTOWFv2
        # folds it into the key, so substituting the server's default for a
        # client that sent none derives a different key and fails a correct
        # password. Empty is a value here, not a missing one.
        srv.auth.verify(creds.user, creds.nt_response, conn.challenge,
                        domain=creds.domain)

    conn.authenticated = True
    conn.user = creds.user or "(anonymous)"
    conn.challenge = None
    if conn.session_id == 0:
        conn.session_id = int.from_bytes(os.urandom(8), "little") or 1
    note("session established for %r from %s (dialect 0x%04x)"
         % (conn.user, conn.peer[0], conn.dialect or 0))

    out = spnego.neg_token_resp(spnego.ACCEPT_COMPLETED)
    body_out = struct.pack("<HHHH", 9, session_flags, wire.HEADER_SIZE + 8, len(out))
    return wire.STATUS_SUCCESS, body_out + out


def h_logoff(srv, conn, hdr, body):
    conn.close_all()
    conn.authenticated = False
    conn.trees.clear()
    return wire.STATUS_SUCCESS, struct.pack("<HH", 4, 0)


def h_tree_connect(srv, conn, hdr, body):
    srv._require_session(conn)
    if len(body) < 8:
        raise Smb2Error(wire.STATUS_INVALID_PARAMETER, "short TREE_CONNECT")
    _size, _flags, path_off, path_len = struct.unpack_from("<HHHH", body, 0)
    path = wire.from_utf16(request_buffer(body, path_off, path_len,
                                          "TREE_CONNECT path",
                                          least=wire.HEADER_SIZE + 8))

    # \\server\share -- only the last component names anything here.
    name = path.rstrip("\\").rsplit("\\", 1)[-1]
    if name.upper() == "IPC$":
        # Explorer asks for IPC$ before anything else. Refusing it politely is
        # correct for a server with no named pipes; refusing it with the wrong
        # status makes the client abandon the whole connection.
        raise Smb2Error(wire.STATUS_BAD_NETWORK_NAME, "no IPC$ on this server")
    share = srv.shares.get(name) or srv.shares.get(name.lower())
    if share is None:
        raise Smb2Error(wire.STATUS_BAD_NETWORK_NAME, "no share named %r" % name)

    tree_id = conn._next_tree
    conn._next_tree += 1
    conn.trees[tree_id] = share
    log("tree connect", name, "->", share.root, "as tid", tree_id)

    read_only = srv.read_only or share.read_only
    access = 0x001200A9 if read_only else 0x001F01FF
    body_out = struct.pack("<HBBIII", 16, wire.SHARE_TYPE_DISK, 0, 0, 0, access)
    return wire.STATUS_SUCCESS, body_out, tree_id


def h_tree_disconnect(srv, conn, hdr, body):
    conn.trees.pop(hdr.tree_id, None)
    return wire.STATUS_SUCCESS, struct.pack("<HH", 4, 0)


def h_create(srv, conn, hdr, body):
    share = srv._tree(conn, hdr)
    if len(body) < 56:
        raise Smb2Error(wire.STATUS_INVALID_PARAMETER, "short CREATE")
    (_size, _sec_flags, _oplock, _impersonation, _flags1, _flags2,
     _access, _attrs, _share_access, disposition, options,
     name_off, name_len, _cc_off, _cc_len) = struct.unpack_from(
        "<HBBIQQIIIIIHHII", body, 0)
    rel = wire.from_utf16(request_buffer(body, name_off, name_len, "CREATE name",
                                        least=wire.HEADER_SIZE + 56))

    path = share.resolve(rel)
    exists = os.path.exists(path)
    want_dir = bool(options & wire.FILE_DIRECTORY_FILE)
    want_file = bool(options & wire.FILE_NON_DIRECTORY_FILE)

    if disposition == wire.FILE_OPEN and not exists:
        raise Smb2Error(wire.STATUS_OBJECT_NAME_NOT_FOUND, rel or "<root>")
    if disposition == wire.FILE_CREATE and exists:
        raise Smb2Error(wire.STATUS_OBJECT_NAME_COLLISION, rel)

    action = wire.FILE_OPENED
    if not exists:
        srv._writable(share)
        if want_dir:
            os.makedirs(path, exist_ok=True)
            action = wire.FILE_CREATED
        elif disposition in (wire.FILE_CREATE, wire.FILE_OPEN_IF,
                             wire.FILE_OVERWRITE_IF, wire.FILE_SUPERSEDE):
            with open(path, "wb"):
                pass
            action = wire.FILE_CREATED
        else:
            raise Smb2Error(wire.STATUS_OBJECT_NAME_NOT_FOUND, rel)
    elif disposition in (wire.FILE_OVERWRITE, wire.FILE_OVERWRITE_IF,
                         wire.FILE_SUPERSEDE) and not os.path.isdir(path):
        srv._writable(share)
        with open(path, "wb"):
            pass
        action = wire.FILE_OVERWRITTEN

    st = os.stat(path)
    is_dir = statmod.S_ISDIR(st.st_mode)
    if is_dir and want_file:
        raise Smb2Error(wire.STATUS_FILE_IS_A_DIRECTORY, rel)
    if not is_dir and want_dir:
        raise Smb2Error(wire.STATUS_NOT_A_DIRECTORY, rel)

    h = conn.add_handle(share, path, rel, is_dir)
    if options & wire.FILE_DELETE_ON_CLOSE:
        srv._writable(share)
        h.delete_on_close = True

    ctime, atime, mtime, chtime = wire._stat_times(st)
    read_only = srv.read_only or share.read_only
    body_out = struct.pack(
        "<HBBIQQQQqqII16sII",
        89, 0, 0, action,
        ctime, atime, mtime, chtime,
        wire.allocation_of(st), st.st_size,
        wire.attributes_for(st, read_only), 0,
        struct.pack("<QQ", h.file_id, h.file_id),
        0, 0)
    log("create", rel or "<root>", "->", "dir" if is_dir else "file", "id", h.file_id)
    return wire.STATUS_SUCCESS, body_out


def h_close(srv, conn, hdr, body):
    srv._tree(conn, hdr)
    if len(body) < 24:
        raise Smb2Error(wire.STATUS_INVALID_PARAMETER, "short CLOSE")
    _size, flags, _res = struct.unpack_from("<HHI", body, 0)
    h = conn.handle_from(body[8:24])

    try:
        st = os.stat(h.path)
    except OSError:
        st = None
    h.close()
    conn.handles.pop(h.file_id, None)

    if h.delete_on_close:
        try:
            if h.is_dir:
                os.rmdir(h.path)
            else:
                os.remove(h.path)
        except OSError as e:
            log("delete-on-close failed for", h.path, e)

    if flags & 0x0001 and st is not None:       # POSTQUERY_ATTRIB
        ctime, atime, mtime, chtime = wire._stat_times(st)
        return wire.STATUS_SUCCESS, struct.pack(
            "<HHIQQQQqqI", 60, flags, 0, ctime, atime, mtime, chtime,
            wire.allocation_of(st), st.st_size,
            wire.attributes_for(st, srv.read_only))
    return wire.STATUS_SUCCESS, struct.pack(
        "<HHIQQQQqqI", 60, 0, 0, 0, 0, 0, 0, 0, 0, 0)


def h_flush(srv, conn, hdr, body):
    srv._tree(conn, hdr)
    h = conn.handle_from(body[8:24])
    if h.fh is not None:
        try:
            h.fh.flush()
            os.fsync(h.fh.fileno())
        except OSError:
            pass
    return wire.STATUS_SUCCESS, struct.pack("<HH", 4, 0)


def _open_file(h, writable):
    """The lazily-opened file behind a handle.

    Opened on first use rather than at CREATE, because Explorer opens a handle
    just to read attributes far more often than it reads bytes, and holding a
    descriptor for every one of those is how a browse of a large folder runs the
    process out of file descriptors.
    """
    if h.fh is None:
        h.fh = open(h.path, "r+b" if writable else "rb")
    elif writable and h.fh.mode == "rb":
        h.fh.close()
        h.fh = open(h.path, "r+b")
    return h.fh


def h_read(srv, conn, hdr, body):
    srv._tree(conn, hdr)
    if len(body) < 48:
        raise Smb2Error(wire.STATUS_INVALID_PARAMETER, "short READ")
    _size, _pad, _flags, length, offset = struct.unpack_from("<HBBIQ", body, 0)
    h = conn.handle_from(body[16:32])
    if h.is_dir:
        raise Smb2Error(wire.STATUS_INVALID_DEVICE_REQUEST, "read on a directory")

    length = min(length, wire.MAX_READ)
    fh = _open_file(h, writable=False)
    fh.seek(offset)
    data = fh.read(length)
    if not data:
        # Not an error condition to a human, but it is to the protocol: a
        # zero-length success makes some clients retry the same offset forever.
        raise Smb2Error(wire.STATUS_END_OF_FILE, "read past the end")

    data_offset = wire.HEADER_SIZE + 16
    body_out = struct.pack("<HBBIII", 17, data_offset, 0, len(data), 0, 0)
    return wire.STATUS_SUCCESS, body_out + data


def h_write(srv, conn, hdr, body):
    share = srv._tree(conn, hdr)
    srv._writable(share)
    if len(body) < 48:
        raise Smb2Error(wire.STATUS_INVALID_PARAMETER, "short WRITE")
    _size, data_off, length, offset = struct.unpack_from("<HHIQ", body, 0)
    h = conn.handle_from(body[16:32])
    if h.is_dir:
        raise Smb2Error(wire.STATUS_INVALID_DEVICE_REQUEST, "write to a directory")

    if length > wire.MAX_WRITE:
        raise Smb2Error(wire.STATUS_INVALID_PARAMETER,
                        "write of %d bytes exceeds the advertised maximum" % length)
    data = request_buffer(body, data_off, length, "WRITE data",
                          least=wire.HEADER_SIZE + 48)
    fh = _open_file(h, writable=True)
    fh.seek(offset)
    written = fh.write(data)
    fh.flush()
    return wire.STATUS_SUCCESS, struct.pack("<HHIIHH", 17, 0, written, 0, 0, 0)


def _listing_for(h, pattern):
    """The snapshot a QUERY_DIRECTORY walks, with . and .. first.

    Explorer copes without the dot entries, but a client that computes a parent
    from the listing does not, and they cost nothing.
    """
    import fnmatch
    names = [".", ".."]
    try:
        names += sorted(os.listdir(h.path))
    except OSError:
        names = names
    if pattern and pattern not in ("*", "*.*"):
        keep = [n for n in names[:2]]
        keep += [n for n in names[2:] if fnmatch.fnmatch(n.lower(), pattern.lower())]
        names = keep
    return names


def h_query_directory(srv, conn, hdr, body):
    share = srv._tree(conn, hdr)
    if len(body) < 32:
        raise Smb2Error(wire.STATUS_INVALID_PARAMETER, "short QUERY_DIRECTORY")
    _size, info_class, flags, _index = struct.unpack_from("<HBBI", body, 0)
    h = conn.handle_from(body[8:24])
    name_off, name_len, out_len = struct.unpack_from("<HHI", body, 24)
    pattern = wire.from_utf16(request_buffer(
        body, name_off, name_len, "QUERY_DIRECTORY pattern",
        least=wire.HEADER_SIZE + 32)) or "*"

    if not h.is_dir:
        raise Smb2Error(wire.STATUS_INVALID_PARAMETER, "not a directory")

    RESTART_SCANS = 0x01
    SINGLE_ENTRY = 0x02
    if h.listing is None or flags & RESTART_SCANS:
        h.listing = _listing_for(h, pattern)
        h.cursor = 0

    out_len = min(out_len or wire.MAX_TRANSACT, wire.MAX_TRANSACT)
    read_only = srv.read_only or share.read_only

    entries = []
    total = 0
    while h.cursor < len(h.listing):
        name = h.listing[h.cursor]
        if name == ".":
            target = h.path
        elif name == "..":
            # At the share root the parent is outside the share, and the whole
            # point of the boundary is that nothing above the root is described
            # to a client -- not even its size and timestamps. Report the root
            # itself, which is what a client needs the entry to exist for.
            target = (h.path if os.path.realpath(h.path) == share.root
                      else os.path.dirname(h.path))
        else:
            target = os.path.join(h.path, name)
        try:
            st = os.stat(target)
        except OSError:
            h.cursor += 1
            continue
        record = wire.directory_entry(name, st, info_class, read_only)
        if record is None:
            raise Smb2Error(wire.STATUS_INVALID_PARAMETER,
                            "unsupported info class 0x%02x" % info_class)
        padded = record + b"\x00" * ((8 - len(record) % 8) % 8)
        if total + len(padded) > out_len and entries:
            break                      # the rest comes on the next call
        entries.append(padded)
        total += len(padded)
        h.cursor += 1
        if flags & SINGLE_ENTRY:
            break

    if not entries:
        # The end of an enumeration is reported, not implied by an empty buffer.
        # A client that gets success with nothing in it keeps asking.
        raise Smb2Error(wire.STATUS_NO_MORE_FILES, "end of directory")

    blob = b""
    for i, record in enumerate(entries):
        last = i == len(entries) - 1
        patched = struct.pack("<I", 0 if last else len(record)) + record[4:]
        blob += patched

    body_out = struct.pack("<HHI", 9, wire.HEADER_SIZE + 8, len(blob))
    return wire.STATUS_SUCCESS, body_out + blob


def h_query_info(srv, conn, hdr, body):
    share = srv._tree(conn, hdr)
    if len(body) < 40:
        raise Smb2Error(wire.STATUS_INVALID_PARAMETER, "short QUERY_INFO")
    _size, info_type, info_class, out_len = struct.unpack_from("<HBBI", body, 0)
    h = conn.handle_from(body[24:40])
    read_only = srv.read_only or share.read_only

    if info_type == wire.INFO_TYPE_FILE:
        st = os.stat(h.path)
        name = "\\" + h.rel.replace("/", "\\") if h.rel else "\\"
        blob = wire.file_info(info_class, st, name, read_only)
    elif info_type == wire.INFO_TYPE_FILESYSTEM:
        blob = wire.fs_info(info_class, share.root)
    else:
        # Security and quota are not answered. Explorer asks and carries on;
        # pretending otherwise would mean inventing a descriptor.
        raise Smb2Error(wire.STATUS_NOT_SUPPORTED,
                        "info type 0x%02x is not served" % info_type)

    if blob is None:
        raise Smb2Error(wire.STATUS_NOT_SUPPORTED,
                        "info class 0x%02x is not served" % info_class)
    if out_len and len(blob) > out_len:
        raise Smb2Error(wire.STATUS_INFO_LENGTH_MISMATCH, "buffer too small")

    body_out = struct.pack("<HHI", 9, wire.HEADER_SIZE + 8, len(blob))
    return wire.STATUS_SUCCESS, body_out + blob


def h_set_info(srv, conn, hdr, body):
    share = srv._tree(conn, hdr)
    srv._writable(share)
    if len(body) < 32:
        raise Smb2Error(wire.STATUS_INVALID_PARAMETER, "short SET_INFO")
    _size, info_type, info_class, buf_len, buf_off = struct.unpack_from(
        "<HBBIH", body, 0)
    h = conn.handle_from(body[16:32])
    payload = request_buffer(body, buf_off, buf_len, "SET_INFO",
                             least=wire.HEADER_SIZE + 32)

    if info_type != wire.INFO_TYPE_FILE:
        raise Smb2Error(wire.STATUS_NOT_SUPPORTED, "only file info can be set")

    if info_class == wire.FILE_END_OF_FILE_INFORMATION and len(payload) >= 8:
        size = struct.unpack_from("<q", payload, 0)[0]
        fh = _open_file(h, writable=True)
        fh.truncate(size)
        return wire.STATUS_SUCCESS, struct.pack("<H", 2)
    if info_class == wire.FILE_DISPOSITION_INFORMATION and payload:
        h.delete_on_close = bool(payload[0])
        return wire.STATUS_SUCCESS, struct.pack("<H", 2)
    if info_class == wire.FILE_BASIC_INFORMATION:
        # Timestamps are accepted and ignored rather than refused: Explorer sets
        # them on every copy, and failing the call fails the copy.
        return wire.STATUS_SUCCESS, struct.pack("<H", 2)
    if info_class == wire.FILE_RENAME_INFORMATION and len(payload) >= 20:
        # Byte 0 is ReplaceIfExists, and it is not advisory: a file manager sends
        # 0 for an ordinary rename and expects a collision to be refused.
        # os.replace overwrites unconditionally, so honouring the flag is the
        # difference between a refused rename and a destroyed file.
        replace_if_exists = bool(payload[0])
        name_len = struct.unpack_from("<I", payload, 16)[0]
        if 20 + name_len > len(payload):
            raise Smb2Error(wire.STATUS_INVALID_PARAMETER, "rename name runs past the buffer")
        target = wire.from_utf16(payload[20:20 + name_len])
        dest = h.share.resolve(target)
        if os.path.exists(dest) and not os.path.samefile(dest, h.path):
            if not replace_if_exists:
                raise Smb2Error(wire.STATUS_OBJECT_NAME_COLLISION, target)
        os.replace(h.path, dest)
        h.path = dest
        h.rel = target
        return wire.STATUS_SUCCESS, struct.pack("<H", 2)

    raise Smb2Error(wire.STATUS_NOT_SUPPORTED,
                    "set info class 0x%02x is not served" % info_class)


def h_echo(srv, conn, hdr, body):
    return wire.STATUS_SUCCESS, struct.pack("<HH", 4, 0)


def h_cancel(srv, conn, hdr, body):
    # Nothing here runs long enough to cancel, and a CANCEL is never answered:
    # a reply to one is itself a protocol error.
    return None, None, None


def h_ioctl(srv, conn, hdr, body):
    raise Smb2Error(wire.STATUS_NOT_SUPPORTED, "no ioctls are served")


_HANDLERS = {
    wire.CMD_NEGOTIATE: h_negotiate,
    wire.CMD_SESSION_SETUP: h_session_setup,
    wire.CMD_LOGOFF: h_logoff,
    wire.CMD_TREE_CONNECT: h_tree_connect,
    wire.CMD_TREE_DISCONNECT: h_tree_disconnect,
    wire.CMD_CREATE: h_create,
    wire.CMD_CLOSE: h_close,
    wire.CMD_FLUSH: h_flush,
    wire.CMD_READ: h_read,
    wire.CMD_WRITE: h_write,
    wire.CMD_QUERY_DIRECTORY: h_query_directory,
    wire.CMD_QUERY_INFO: h_query_info,
    wire.CMD_SET_INFO: h_set_info,
    wire.CMD_ECHO: h_echo,
    wire.CMD_IOCTL: h_ioctl,
    wire.CMD_CANCEL: h_cancel,
}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def take_445():
    """Stop Windows' LanmanServer so this server can bind 445. Returns restore().

    Windows' own SMB client cannot be pointed at a non-445 port, so for Explorer
    to reach this share at all, something has to give up 445. This is the same
    approach as smbv1_server/smbserver_opl.py's _take_445, deliberately kept
    identical in the two properties that matter:

      * Stop, never Disable. A hard kill that never runs restore() leaves the
        service stopped until the next boot, not disabled forever.
      * PowerShell resolved by absolute path, so a stray powershell.exe on PATH
        or in the working directory cannot be run in its place.
    """
    import subprocess

    system_root = (os.environ.get("SystemRoot") or os.environ.get("windir")
                   or r"C:\Windows")
    powershell = os.path.join(system_root, "System32", "WindowsPowerShell",
                              "v1.0", "powershell.exe")
    if not os.path.isfile(powershell):
        powershell = "powershell"

    def ps(cmd):
        return subprocess.run([powershell, "-NoProfile", "-Command", cmd],
                              capture_output=True, text=True)

    status = ps("(Get-Service LanmanServer).Status").stdout.strip()
    browser = ps("(Get-Service Browser -ErrorAction SilentlyContinue).Status"
                 ).stdout.strip()
    note("[445] stopping LanmanServer (was: %s)..." % (status or "?"))
    res = ps("Stop-Service -Name LanmanServer -Force -ErrorAction SilentlyContinue")
    if res.returncode != 0:
        note("[445] could not stop LanmanServer -- run as Administrator.",
             res.stderr.strip())

    def restore():
        note("[445] restoring LanmanServer...")
        ps("Start-Service LanmanServer -ErrorAction SilentlyContinue")
        if browser == "Running":
            ps("Start-Service Browser -ErrorAction SilentlyContinue")

    return restore


def build_authenticator(user_specs, allow_open):
    users = {}
    for spec in user_specs or []:
        if ":" not in spec:
            raise SystemExit("--user must be NAME:PASSWORD, got %r" % spec)
        name, password = spec.split(":", 1)
        if not name or not password:
            raise SystemExit("--user needs both a name and a password: %r" % spec)
        users[name] = password
    if users:
        return Authenticator(users)
    if allow_open:
        return Authenticator.open()
    raise SystemExit(
        "no --user given. SMB2/SMB3 requires a password by default; pass\n"
        "  --user NAME:PASSWORD\n"
        "or --open to serve the share to anyone who can reach the port.")


def main(argv=None):
    global VERBOSE
    ap = argparse.ArgumentParser(
        description="SMB2/SMB3 server for PS2 Servers (modern clients and "
                    "SMB2-capable OPL builds).")
    ap.add_argument("--share", action="append", required=True,
                    metavar="NAME=PATH", help="share to serve; repeatable")
    ap.add_argument("--port", type=int, default=1445,
                    help="TCP port (default 1445). Windows clients cannot use a "
                         "non-445 port; see --take-445.")
    ap.add_argument("--bind", default="0.0.0.0", help="bind address (default all)")
    ap.add_argument("--smb-version", type=int, choices=[2, 3], default=2,
                    help="dialect ceiling: 2 negotiates up to SMB 2.1, "
                         "3 up to SMB 3.0.2")
    ap.add_argument("--user", action="append", metavar="NAME:PASSWORD",
                    help="a user allowed to log in; repeatable")
    ap.add_argument("--open", action="store_true",
                    help="serve with no password at all (anyone who can reach "
                         "the port gets the share)")
    ap.add_argument("--read-only", action="store_true",
                    help="serve read-only (no writes, no saves)")
    ap.add_argument("--take-445", action="store_true",
                    help="bind the standard port 445 by pausing Windows file "
                         "sharing (admin, reversible). Windows Explorer cannot "
                         "connect to any other port.")
    ap.add_argument("--server-name", default="PS2SERVERS")
    ap.add_argument("--domain", default="WORKGROUP")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    VERBOSE = args.verbose

    shares = {}
    for spec in args.share:
        if "=" not in spec:
            ap.error("--share must be NAME=PATH, got %r" % spec)
        name, path = spec.split("=", 1)
        if not os.path.isdir(path):
            ap.error("share path does not exist: %s" % path)
        shares[name] = Share(name, path, read_only=args.read_only)
    if not shares:
        ap.error("at least one --share NAME=PATH is required")

    auth = build_authenticator(args.user, args.open)
    ceiling = wire.CEILING_SMB3 if args.smb_version == 3 else wire.CEILING_SMB2

    server = Smb2Server(shares, auth, ceiling=ceiling, read_only=args.read_only,
                        server_name=args.server_name, domain=args.domain)

    restore = None
    want_port = args.port
    if args.take_445:
        want_port = 445
        restore = take_445()
    try:
        port = server.listen(args.bind, want_port)
    except OSError as e:
        print("ERROR: cannot bind %s:%d -- %s" % (args.bind, want_port, e),
              file=sys.stderr)
        if restore is not None:
            restore()
        return 1

    note("SMB%d server on %s:%d" % (args.smb_version, args.bind, port))
    for name, share in shares.items():
        note("  share %r -> %s%s" % (name, share.root,
                                     " (read-only)" if args.read_only else ""))
    if auth.is_open:
        note("  NO PASSWORD: anyone who can reach this port can read the share.")
    else:
        note("  users: " + ", ".join(sorted(args.user and
                                            [u.split(":", 1)[0] for u in args.user])))
    note("  Windows cannot connect to a non-445 port. Map it, or run the SMBv1 "
         "mode for a console.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
        # Always, including on an exception: leaving Windows file sharing
        # stopped because this server crashed is a far worse outcome than
        # whatever went wrong here.
        if restore is not None:
            restore()
    return 0


if __name__ == "__main__":
    sys.exit(main())
