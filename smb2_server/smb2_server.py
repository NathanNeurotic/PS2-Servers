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

  * Signing is supported, encryption is not. A password-proven session gets a
    signing key (HMAC-SHA256 on 2.x, AES-CMAC on 3.x, see smb2_crypto.py), the
    server verifies signed requests, refuses unsigned ones when the client
    declared signing required, and signs its replies. That is what Windows 11
    (which requires signing by default) needs to mount the share. Signing is
    advertised as enabled, never required, so clients that do not sign -- the
    PS2 loaders -- are served unsigned as before. The AES-CMAC runs on the
    OS crypto library where one is available (Windows bcrypt, OpenSSL) and
    falls back to pure Python elsewhere. Encryption (AES-CCM) is not offered.
  * Open mode (no password) logs the client on as a guest. Guest sessions have
    no session key, so they cannot be signed; Windows refuses unsigned guest
    access unless AllowInsecureGuestAuth is set. Use a password card from
    Windows.
  * Dialect 3.1.1 is not offered. It needs negotiate contexts (a
    pre-authentication integrity hash the client verifies), and claiming it
    without them makes Windows drop the connection. 3.0.2 is negotiated instead,
    which every SMB3 client speaks.
  * No oplocks, leases, or change notification. A client polls instead.
  * IPC$ carries one pipe, srvsvc, and answers one call on it, NetrShareEnum,
    so a client can list the shares (smb2_srvsvc.py). No other pipe exists.

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
import smb2_crypto
import smb2_spnego as spnego
import smb2_srvsvc as srvsvc
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
                 "delete_on_close", "listing", "cursor", "can_write", "pipe")

    def __init__(self, file_id, share, path, rel, is_dir):
        self.file_id = file_id
        self.share = share
        self.path = path
        self.rel = rel
        self.is_dir = is_dir
        self.fh = None
        self.delete_on_close = False
        # Whether the CREATE asked for write access. A handle opened to read
        # must not change the file: a client that writes through one by mistake
        # would otherwise overwrite a game image it only meant to read.
        self.can_write = True
        # A named pipe on IPC$ (srvsvc) instead of a file; path is None then.
        self.pipe = None
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


class _IpcTree:
    """The IPC$ tree: pipes only, never a path."""
    name = "IPC$"
    read_only = True
    root = None

    def __repr__(self):
        return "IPC$"


IPC_TREE = _IpcTree()


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
        # The NTLM NEGOTIATE and CHALLENGE messages of the exchange in flight:
        # the client's MIC in its AUTHENTICATE is computed over both.
        self.ntlm_negotiate = None
        self.ntlm_challenge = None
        # Signing state. signing_key is set when a password-proven session
        # exists; signing_required when the client said in SESSION_SETUP that
        # it signs everything, which is when every reply must be signed too.
        self.signing_key = None
        self.signing_required = False
        # What the client's SMB2 NEGOTIATE said, kept so that
        # FSCTL_VALIDATE_NEGOTIATE_INFO can confirm nobody rewrote it in transit.
        self.negotiate_claims = None
        # Set when the protocol says the only correct answer is to hang up.
        self.terminate = False
        self.trees = {}          # tree_id -> Share
        self.handles = {}        # volatile id -> Handle
        self._next_tree = 1
        self._next_handle = 1
        # Credits this client still holds. Every request spends at least one
        # and every reply grants more; see Server._grant_credits.
        self.credits = 0
        # The FileId a related compound's later requests mean when they send
        # the all-ones placeholder: the one the compound's CREATE opened.
        self.compound_file_id = None

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
        if raw_file_id == wire.COMPOUND_FILE_ID and self.compound_file_id is not None:
            # MS-SMB2 3.3.5.2.7.2: inside a related compound the all-ones FileId
            # stands for the file the CREATE before it opened. libsmb2 sends
            # every stat, unlink, rename and truncate this way.
            raw_file_id = self.compound_file_id
        persistent, volatile = struct.unpack("<QQ", raw_file_id)
        h = self.handles.get(volatile)
        if h is None or persistent != volatile:
            raise Smb2Error(wire.STATUS_FILE_CLOSED, "no such open handle")
        return h

    def close_all(self):
        for h in list(self.handles.values()):
            h.close()
        self.handles.clear()


# SESSION summary counters (--session-summary), one per console. A CREATE is
# an open whether or not the path exists; a QUERY_DIRECTORY is a folder list.
_SESSION_KINDS = {
    wire.CMD_CREATE: "open",
    wire.CMD_QUERY_DIRECTORY: "list",
    wire.CMD_READ: "read",
    wire.CMD_WRITE: "write",
    wire.CMD_CLOSE: "close",
}
# Failure statuses that are part of a normal exchange, not errors: a probe
# for an optional file, the end of a file or of a listing, and the NTLM
# challenge round-trip of SESSION_SETUP.
_EXPECTED_STATUSES = {
    (wire.CMD_CREATE, wire.STATUS_OBJECT_NAME_NOT_FOUND),
    (wire.CMD_READ, wire.STATUS_END_OF_FILE),
    (wire.CMD_QUERY_DIRECTORY, wire.STATUS_NO_MORE_FILES),
    (wire.CMD_SESSION_SETUP, wire.STATUS_MORE_PROCESSING_REQUIRED),
}


class Smb2Server:
    # Optional launcher.session_stats.SessionStats (--session-summary): one
    # counter update per request, summarised per console when it goes quiet.
    session_stats = None

    def __init__(self, shares, authenticator, ceiling=wire.CEILING_SMB2,
                 read_only=False, server_name="PS2SERVERS", domain="WORKGROUP"):
        # Share names are matched without regard to case (share_named), so two
        # names that differ only by case would make the choice between two
        # directories depend on configuration order. Refuse that up front.
        folded = {}
        for name in shares:
            other = folded.setdefault(name.casefold(), name)
            if other != name:
                raise ValueError("share names %r and %r differ only by case"
                                 % (other, name))
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
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((bind, port))
            sock.listen(backlog)
        except BaseException:
            sock.close()  # A failed 445 attempt must not leak a socket on retry.
            raise
        self.sock = sock
        return sock.getsockname()[1]

    def serve_forever(self, poll_seconds=1.0):
        # A timeout lets a stop request (Ctrl+C, or the launcher's Stop on
        # Windows) land in the main thread while nobody connects. Accepted
        # sockets are still blocking: Python resets them when the default
        # timeout is None.
        self.sock.settimeout(poll_seconds)
        next_expiry = time.monotonic() + 10.0
        while not self._stop.is_set():
            try:
                client, peer = self.sock.accept()
            except socket.timeout:
                # Not `continue` in the handler: see smbserver_opl.py (a
                # Ctrl+C landing there escapes the caller's try on 3.12).
                client = None
            except OSError:
                if self._stop.is_set():
                    return
                raise
            if self.session_stats is not None and time.monotonic() >= next_expiry:
                self.session_stats.expire()  # consoles quiet for a minute get their line
                next_expiry = time.monotonic() + 10.0
            if client is None:
                continue
            t = threading.Thread(target=self._serve_client, args=(client, peer),
                                 daemon=True)
            t.start()

    def stop(self):
        self._stop.set()
        if self.sock is not None:
            # On Linux, close() alone does not wake a thread blocked in
            # accept(); shutdown() does (accept fails with EINVAL).
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self.sock.close()
            except OSError:
                pass
        if self.session_stats is not None:
            self.session_stats.stop()  # one final SESSION line per console

    def _session_record(self, conn, kind, count=1):
        stats = self.session_stats
        if stats is not None:
            stats.record(conn.peer[0], kind, count)

    def _serve_client(self, sock, peer):
        conn = Connection(self, sock, peer)
        log("connect from", peer)
        self._session_record(conn, "connections")
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            while not self._stop.is_set():
                msg = wire.recv_msg(sock)
                if msg is None:
                    break
                if msg == b"":
                    continue
                self._dispatch_all(conn, msg)
                if conn.terminate:
                    log("terminating connection from", peer, "as the protocol requires")
                    break
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            log("client", peer, "went away")
        except OSError as e:
            # A reset or a broken pipe is a console switching off, which is
            # normal; any other socket error is counted.
            self._session_record(conn, "errors")
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
        conn.compound_file_id = None
        prev = None                     # (header, status) of the request before
        while offset < len(msg):
            hdr = wire.parse_header(msg[offset:])
            if hdr is None:
                if offset == 0:
                    self._not_smb2(conn, msg)
                return
            end = offset + hdr.next_command if hdr.next_command else len(msg)
            body = msg[offset + wire.HEADER_SIZE:end]
            related = bool(hdr.flags & wire.FLAG_RELATED) and prev is not None
            if related:
                # A related request inherits the session and tree of the one
                # before it, and fails with that one's status if it failed:
                # the file it would act on was never opened.
                if hdr.session_id == 0xFFFFFFFFFFFFFFFF:
                    hdr.session_id = prev[0].session_id
                if hdr.tree_id == 0xFFFFFFFF:
                    hdr.tree_id = prev[0].tree_id
            refusal = self._signature_check(conn, hdr, msg[offset:end])
            if related and prev[1] not in (None, wire.STATUS_SUCCESS):
                status, reply_body, tree_id = prev[1], wire.error_body(), None
            elif refusal is not None:
                status, reply_body, tree_id = refusal, wire.error_body(), None
            else:
                status, reply_body, tree_id = self._handle(conn, hdr, body)
            if (hdr.command == wire.CMD_CREATE and status == wire.STATUS_SUCCESS
                    and len(reply_body) >= 80):
                conn.compound_file_id = bytes(reply_body[64:80])
            if tree_id is not None:
                hdr.tree_id = tree_id
            prev = (hdr, status)
            grant = self._grant_credits(conn, hdr)
            # A CANCEL is answered with silence. Replying to one is itself a
            # protocol error, so "no reply" has to be representable.
            if status is not None:
                replies.append((hdr, status, reply_body, tree_id, grant))
            if not hdr.next_command:
                break
            offset = end
        conn.compound_file_id = None

        out = b""
        for i, (hdr, status, reply_body, tree_id, grant) in enumerate(replies):
            last = i == len(replies) - 1
            padded = reply_body
            next_command = 0
            if not last:
                total = wire.HEADER_SIZE + len(reply_body)
                aligned = (total + 7) & ~7
                padded = reply_body + b"\x00" * (aligned - total)
                next_command = aligned
            flags = wire.FLAG_RELATED if (i and hdr.flags & wire.FLAG_RELATED) else 0
            sign = self._should_sign(conn, hdr, status)
            if sign:
                flags |= wire.FLAG_SIGNED
            piece = wire.pack_header(
                hdr.command, status, hdr.message_id,
                tree_id=hdr.tree_id if tree_id is None else tree_id,
                session_id=conn.session_id or hdr.session_id,
                credits=grant, flags=flags,
                next_command=next_command) + padded
            if sign:
                # Each message of a compound is signed on its own, over its
                # header, body and the padding up to NextCommand.
                piece = smb2_crypto.signed(conn.signing_key, conn.dialect, piece)
            out += piece
        if out:
            wire.send_msg(conn.sock, out)
            # Count served bytes only once the reply left: a send that raised
            # did not serve anything. A READ reply is a 16-byte body + data.
            for hdr, status, reply_body, _tree_id, _grant in replies:
                if hdr.command == wire.CMD_READ and status == wire.STATUS_SUCCESS:
                    self._session_record(conn, "bytes", len(reply_body) - 16)

    def _not_smb2(self, conn, msg):
        """A first frame that is not SMB2 (MS-SMB2 3.3.5.3.1).

        Windows (and macOS) open with an SMB1-framed SMB_COM_NEGOTIATE that
        lists "SMB 2.???" and "SMB 2.002" among the dialects. The answer is
        an SMB2 NEGOTIATE response: dialect 0x02FF ("send a real SMB2
        NEGOTIATE") when the wildcard was offered, 2.0.2 when only that was.
        Leaving it unanswered is why Windows Explorer spent 100 seconds and
        five connections before giving up with "network name is no longer
        available". A client offering no SMB2 dialect at all speaks SMB1
        only, which is the SMBv1 server's job.
        """
        dialects = smb1_negotiate_dialects(msg)
        if dialects is None:
            log("ignoring a frame that is not SMB2 from", conn.peer, msg[:8].hex())
            return
        if "SMB 2.???" in dialects:
            dialect = 0x02FF
        elif "SMB 2.002" in dialects and wire.DIALECT_202 <= self.ceiling:
            dialect = wire.DIALECT_202
            conn.dialect = dialect
        else:
            note("%s offered only SMB1 dialects (%s); this server speaks SMB2/SMB3. "
                 "Run the SMBv1 server for this client (OPL needs it)."
                 % (conn.peer[0], ", ".join(dialects)))
            return
        log("SMB1-framed NEGOTIATE from %s -> SMB2 dialect 0x%04x" % (conn.peer, dialect))
        conn.credits += 1
        out = wire.pack_header(wire.CMD_NEGOTIATE, wire.STATUS_SUCCESS, 0, credits=1)
        wire.send_msg(conn.sock, out + negotiate_response(self, dialect))

    # -- signing ----------------------------------------------------------- #
    # Requests that are legitimately unsigned on a session that signs: the
    # ones that happen before a key exists, and CANCEL, which MS-SMB2 3.3.5.1
    # exempts outright.
    _UNSIGNED_OK = frozenset((wire.CMD_NEGOTIATE, wire.CMD_SESSION_SETUP,
                              wire.CMD_CANCEL))

    def _signature_check(self, conn, hdr, message):
        """A status to refuse `message` with, or None if it may be served.

        A signed request is verified against the session's key; a bad
        signature is a request from someone who does not hold the key, and it
        is refused rather than served under the session's identity. An
        unsigned request on a session whose client declared signing required
        is refused the same way: MS-SMB2 3.3.5.2.4. A signed request on a
        session with no key (open mode has no secret to sign with) cannot be
        verified and is served as if unsigned.
        """
        signed = bool(hdr.flags & wire.FLAG_SIGNED)
        if conn.signing_key is None:
            return None
        if signed:
            if smb2_crypto.verify(conn.signing_key, conn.dialect, message):
                return None
            log(hdr, "-> bad signature from", conn.peer)
            return wire.STATUS_ACCESS_DENIED
        if conn.signing_required and hdr.command not in self._UNSIGNED_OK:
            log(hdr, "-> unsigned request on a session that requires signing")
            return wire.STATUS_ACCESS_DENIED
        return None

    def _should_sign(self, conn, hdr, status):
        """Whether one reply is signed (MS-SMB2 3.3.4.1.1).

        Yes when the session has a key and either the client required signing
        or signed this very request. So a PS2 client, which never signs, gets
        the bytes it always did, and Windows, which signs everything once it
        has a key, gets every reply signed -- including the final
        SESSION_SETUP, whose signature it checks before trusting the session.
        The mid-exchange SESSION_SETUP (MORE_PROCESSING_REQUIRED) has no key
        yet and is never signed.
        """
        if conn.signing_key is None or status == wire.STATUS_MORE_PROCESSING_REQUIRED:
            return False
        return conn.signing_required or bool(hdr.flags & wire.FLAG_SIGNED)

    # The most credits one client may hold at once. Enough for libsmb2 to keep
    # a compound and a few large reads in flight; a client asks for what it
    # wants and is never handed more than this in total.
    CREDIT_LIMIT = 512

    def _grant_credits(self, conn, hdr):
        """Credits for this request's reply (MS-SMB2 3.3.1.2).

        Always granting one kept a client at a balance of one forever. libsmb2
        will not send a request it lacks the credits for, so its three-part
        CREATE/QUERY_INFO/CLOSE compound -- every stat -- sat in its queue and
        the PS2 browse driver hung there.
        """
        if hdr.command != wire.CMD_NEGOTIATE:
            conn.credits = max(0, conn.credits - max(1, hdr.credit_charge))
        want = max(1, hdr.credits)
        grant = max(1, min(want, self.CREDIT_LIMIT - conn.credits))
        conn.credits += grant
        return grant

    def _handle(self, conn, hdr, body):
        """(status, body, tree_id). status None means send nothing at all."""
        kind = _SESSION_KINDS.get(hdr.command)
        if kind is not None:
            self._session_record(conn, kind)
        status, reply_body, tree_id = self._dispatch(conn, hdr, body)
        if (status is not None and status != wire.STATUS_SUCCESS
                and (hdr.command, status) not in _EXPECTED_STATUSES):
            self._session_record(conn, "errors")
        elif kind is None and self.session_stats is not None:
            # ECHO, QUERY_INFO, FLUSH and the like count nothing, but a console
            # sending only those is not idle, so keep its session alive.
            self.session_stats.touch(conn.peer[0])
        return status, reply_body, tree_id

    def _dispatch(self, conn, hdr, body):
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

    def share_named(self, name):
        """The share a TREE_CONNECT names, matched without regard to case.

        Share names are case-insensitive in SMB. A console types its share name
        into a settings screen, and Neutrino passes it through as given, so
        "GAMES" on the PS2 and "games" in the launcher are the same share.
        """
        share = self.shares.get(name)
        if share is not None:
            return share
        folded = name.casefold()
        for key, candidate in self.shares.items():
            if key.casefold() == folded:
                return candidate
        return None

    # -- guards ------------------------------------------------------------ #
    def _require_session(self, conn):
        if not conn.authenticated:
            raise Smb2Error(wire.STATUS_USER_SESSION_DELETED, "no session")

    def _tree(self, conn, hdr, ipc_ok=False):
        self._require_session(conn)
        share = conn.trees.get(hdr.tree_id)
        if share is None:
            raise Smb2Error(wire.STATUS_NETWORK_NAME_DELETED, "no such tree")
        if share is IPC_TREE and not ipc_ok:
            # IPC$ holds pipes, not files: nothing that walks a directory or
            # resolves a path may be handed it.
            raise Smb2Error(wire.STATUS_INVALID_DEVICE_REQUEST, "not served on IPC$")
        return share

    def share_list(self):
        """(name, type, remark, path) for every share, IPC$ last, for srvsvc."""
        rows = [(name, srvsvc.STYPE_DISKTREE,
                 "read-only" if (self.read_only or share.read_only) else "",
                 "")              # level 2 path: the host's folders are not advertised
                for name, share in sorted(self.shares.items())]
        rows.append(("IPC$", srvsvc.STYPE_IPC | srvsvc.STYPE_SPECIAL, "Remote IPC", ""))
        return rows

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
    _size, dialect_count, mode, _res, caps = struct.unpack_from("<HHHHI", body, 0)
    offered = []
    for i in range(dialect_count):
        at = 36 + i * 2
        if at + 2 > len(body):
            break
        offered.append(struct.unpack_from("<H", body, at)[0])
    conn.negotiate_claims = (caps, bytes(body[12:28]), mode, tuple(offered))

    dialect = wire.pick_dialect(offered, srv.ceiling)
    if dialect is None:
        offered_text = ", ".join("0x%04x" % d for d in offered)
        if (srv.ceiling < wire.DIALECT_300
                and wire.pick_dialect(offered, wire.CEILING_SMB3) is not None):
            # Said at normal volume, not only under -v: a client that speaks
            # nothing below SMB 3.0 is a configuration problem the operator can
            # fix in one click, and silence here looks like a dead server.
            # Neutrino's SMB driver (usmb2) is such a client: it offers 3.0 only.
            note("%s offered only SMB 3.x (%s); this server is in SMBv2 mode. "
                 "Run the SMBv3 server for this client (Neutrino needs it)."
                 % (conn.peer[0], offered_text))
        raise Smb2Error(wire.STATUS_NOT_SUPPORTED,
                        "no common dialect; client offered %s" % offered_text)
    conn.dialect = dialect
    log("negotiated dialect 0x%04x with %s" % (dialect, conn.peer))
    return wire.STATUS_SUCCESS, negotiate_response(srv, dialect)


def negotiate_response(srv, dialect):
    """The NEGOTIATE response body for `dialect` (MS-SMB2 2.2.4)."""
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
    return body_out + token


# An SMB1-framed SMB_COM_NEGOTIATE (MS-CIFS 2.2.4.52): 32-byte header, then
# WordCount, ByteCount and the dialect strings, each 0x02 + NUL-terminated.
SMB1_MAGIC = b"\xffSMB"
SMB1_COM_NEGOTIATE = 0x72
SMB1_HEADER_SIZE = 32


def smb1_negotiate_dialects(msg):
    """The dialect strings of an SMB1 NEGOTIATE, or None if `msg` is not one."""
    if (len(msg) < SMB1_HEADER_SIZE + 3 or msg[:4] != SMB1_MAGIC
            or msg[4] != SMB1_COM_NEGOTIATE):
        return None
    word_count = msg[SMB1_HEADER_SIZE]
    at = SMB1_HEADER_SIZE + 1 + word_count * 2
    if at + 2 > len(msg):
        return None
    byte_count = struct.unpack_from("<H", msg, at)[0]
    blob = msg[at + 2:at + 2 + byte_count]
    dialects = []
    for item in blob.split(b"\x00"):
        if item.startswith(b"\x02"):
            dialects.append(item[1:].decode("ascii", "replace"))
    return dialects


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
    # Answer in the framing the client used. A client that sent bare NTLMSSP
    # reads the CHALLENGE at the very start of the security buffer and has no
    # DER parser; Neutrino's usmb2 is one, and it takes the TargetName and
    # TargetInfo offsets from whatever bytes sit there. Wrapped in SPNEGO it
    # reads DER as NTLMSSP and computes its proof over garbage.
    raw = spnego.is_raw_ntlmssp(blob)

    kind = spnego.message_type(token)
    if kind == spnego.NTLM_NEGOTIATE:
        # Round one: hand back a challenge and ask for the proof. The challenge
        # is kept per connection -- it is what the client's response is computed
        # against, so a shared or reused one would let a captured response be
        # replayed onto another connection.
        conn.challenge = server_challenge()
        # A bare-NTLMSSP client gets only the two AV pairs MS-NLMP requires.
        # usmb2 receives this reply and builds its AUTHENTICATE in one 512-byte
        # buffer, and copies TargetInfo into it; the DNS pairs are optional
        # and would only eat that room.
        chal = spnego.build_challenge(conn.challenge, srv.domain, srv.server_name,
                                      compact=raw,
                                      client_flags=spnego.negotiate_flags(token))
        conn.ntlm_negotiate = spnego.trim(token)
        conn.ntlm_challenge = chal
        conn.signing_key = None
        conn.signing_required = False
        if raw:
            out = chal
        else:
            out = spnego.neg_token_resp(spnego.ACCEPT_INCOMPLETE, chal,
                                        with_mech=True)
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
    session_key = None
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
        base_key = srv.auth.verify(creds.user, creds.nt_response, conn.challenge,
                                   domain=creds.domain)
        if base_key is None:
            # Open mode: nothing was proved, so there is no key to sign with.
            # Say so with IS_GUEST rather than pretend; a client whose policy
            # requires signing decides for itself whether a guest session
            # without it is acceptable (Windows: AllowInsecureGuestAuth).
            session_flags = wire.SESSION_FLAG_IS_GUEST
        else:
            session_key = spnego.exported_session_key(base_key, creds)
            if not spnego.verify_mic(session_key, conn.ntlm_negotiate,
                                     conn.ntlm_challenge, creds):
                raise Smb2Error(wire.STATUS_LOGON_FAILURE,
                                "NTLM MIC does not match for %r" % (creds.user,))

    conn.authenticated = True
    conn.user = creds.user or "(anonymous)"
    conn.challenge = None
    conn.ntlm_negotiate = conn.ntlm_challenge = None
    if session_key is not None:
        conn.signing_key = smb2_crypto.signing_key(session_key, conn.dialect or 0)
        conn.signing_required = bool(_mode & wire.SECURITY_MODE_SIGNING_REQUIRED)
    if conn.session_id == 0:
        conn.session_id = int.from_bytes(os.urandom(8), "little") or 1
    note("session established for %r from %s (dialect 0x%04x)"
         % (conn.user, conn.peer[0], conn.dialect or 0))

    # No SPNEGO accept-completed for a bare-NTLMSSP client: there is no SPNEGO
    # exchange to complete, and NTLMSSP has no fourth message.
    out = b"" if raw else spnego.neg_token_resp(spnego.ACCEPT_COMPLETED)
    body_out = struct.pack("<HHHH", 9, session_flags,
                           wire.HEADER_SIZE + 8 if out else 0, len(out))
    return wire.STATUS_SUCCESS, body_out + out


def h_logoff(srv, conn, hdr, body):
    conn.close_all()
    conn.authenticated = False
    conn.trees.clear()
    # The signing key stays until the next SESSION_SETUP replaces it: the
    # LOGOFF reply is packed after this returns and must still be signed.
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
        # IPC$ carries one pipe, srvsvc, so a client with no share name can ask
        # which shares there are (RiptOPL's share picker, Explorer's \\server
        # view, smbclient -L). See smb2_srvsvc.py.
        tree_id = conn._next_tree
        conn._next_tree += 1
        conn.trees[tree_id] = IPC_TREE
        log("tree connect IPC$ as tid", tree_id)
        body_out = struct.pack("<HBBIII", 16, wire.SHARE_TYPE_PIPE, 0,
                               0x00000030,      # SMB2_SHAREFLAG_NO_CACHING
                               0, 0x001F01FF)
        return wire.STATUS_SUCCESS, body_out, tree_id
    share = srv.share_named(name)
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


def _create_pipe(srv, conn, body):
    """CREATE on IPC$: the srvsvc pipe, or not found."""
    name_off, name_len = struct.unpack_from("<HH", body, 44)
    name = wire.from_utf16(request_buffer(body, name_off, name_len, "CREATE name",
                                          least=wire.HEADER_SIZE + 56))
    if name.strip("\\").lower() not in srvsvc.PIPE_NAMES:
        raise Smb2Error(wire.STATUS_OBJECT_NAME_NOT_FOUND, "no pipe %r" % name)
    h = conn.add_handle(IPC_TREE, None, name, False)
    h.pipe = srvsvc.SrvsvcPipe(srv.share_list, srv.server_name)
    log("create pipe", name, "-> id", h.file_id)
    body_out = struct.pack(
        "<HBBIQQQQqqII16sII",
        89, 0, 0, wire.FILE_OPENED,
        0, 0, 0, 0, 4096, 0,
        0x00000080, 0,                       # FILE_ATTRIBUTE_NORMAL
        struct.pack("<QQ", h.file_id, h.file_id),
        0, 0)
    return wire.STATUS_SUCCESS, body_out


def h_create(srv, conn, hdr, body):
    share = srv._tree(conn, hdr, ipc_ok=True)
    if len(body) < 56:
        raise Smb2Error(wire.STATUS_INVALID_PARAMETER, "short CREATE")
    if share is IPC_TREE:
        return _create_pipe(srv, conn, body)
    (_size, _sec_flags, _oplock, _impersonation, _flags1, _flags2,
     access, _attrs, _share_access, disposition, options,
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
    h.can_write = bool(access & wire.ACCESS_WRITE_ANY)
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
    srv._tree(conn, hdr, ipc_ok=True)
    if len(body) < 24:
        raise Smb2Error(wire.STATUS_INVALID_PARAMETER, "short CLOSE")
    _size, flags, _res = struct.unpack_from("<HHI", body, 0)
    h = conn.handle_from(body[8:24])

    try:
        st = os.stat(h.path) if h.pipe is None else None
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
    srv._tree(conn, hdr, ipc_ok=True)
    if len(body) < 48:
        raise Smb2Error(wire.STATUS_INVALID_PARAMETER, "short READ")
    _size, _pad, _flags, length, offset = struct.unpack_from("<HBBIQ", body, 0)
    h = conn.handle_from(body[16:32])
    if h.pipe is not None:
        data, more = h.pipe.read(min(length, wire.MAX_READ))
        if not data:
            raise Smb2Error(wire.STATUS_PIPE_EMPTY, "nothing written to the pipe yet")
        body_out = struct.pack("<HBBIII", 17, wire.HEADER_SIZE + 16, 0, len(data), 0, 0)
        # BUFFER_OVERFLOW is a warning with data: the client reads the rest.
        return (wire.STATUS_BUFFER_OVERFLOW if more else wire.STATUS_SUCCESS), body_out + data
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
    share = srv._tree(conn, hdr, ipc_ok=True)
    if len(body) < 48:
        raise Smb2Error(wire.STATUS_INVALID_PARAMETER, "short WRITE")
    _size, data_off, length, offset = struct.unpack_from("<HHIQ", body, 0)
    h = conn.handle_from(body[16:32])
    if h.pipe is not None:
        data = request_buffer(body, data_off, length, "WRITE data",
                              least=wire.HEADER_SIZE + 48)
        try:
            written = h.pipe.write(data)
        except srvsvc.RpcError as e:
            raise Smb2Error(wire.STATUS_INVALID_PARAMETER, "srvsvc: %s" % e)
        return wire.STATUS_SUCCESS, struct.pack("<HHIIHH", 17, 0, written, 0, 0, 0)
    srv._writable(share)
    if h.is_dir:
        raise Smb2Error(wire.STATUS_INVALID_DEVICE_REQUEST, "write to a directory")
    if not h.can_write:
        raise Smb2Error(wire.STATUS_ACCESS_DENIED, "write through a read-only handle")

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
        if not h.can_write:
            raise Smb2Error(wire.STATUS_ACCESS_DENIED, "resize through a read-only handle")
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
    srv._tree(conn, hdr, ipc_ok=True)
    if len(body) < 56:
        raise Smb2Error(wire.STATUS_INVALID_PARAMETER, "short IOCTL")
    ctl_code = struct.unpack_from("<I", body, 4)[0]
    in_off, in_len, _max_in, _out_off, _out_len, max_out, flags = struct.unpack_from(
        "<IIIIIII", body, 24)
    data = request_buffer(body, in_off, in_len, "IOCTL input",
                          least=wire.HEADER_SIZE + 56)
    status = wire.STATUS_SUCCESS
    if ctl_code == wire.FSCTL_VALIDATE_NEGOTIATE_INFO:
        out = validate_negotiate_info(srv, conn, data)
    elif ctl_code == wire.FSCTL_PIPE_TRANSCEIVE:
        h = conn.handle_from(body[8:24])
        if h.pipe is None:
            raise Smb2Error(wire.STATUS_INVALID_DEVICE_REQUEST, "transceive on a file")
        try:
            out = h.pipe.transact(data)
        except srvsvc.RpcError as e:
            raise Smb2Error(wire.STATUS_INVALID_PARAMETER, "srvsvc: %s" % e)
        if max_out and len(out) > max_out:
            # The rest waits in the pipe for READs, as on Windows.
            h.pipe.outbox = out[max_out:] + h.pipe.outbox
            out = out[:max_out]
            status = wire.STATUS_BUFFER_OVERFLOW
    else:
        raise Smb2Error(wire.STATUS_NOT_SUPPORTED, "ioctl 0x%08x is not served" % ctl_code)
    off = wire.HEADER_SIZE + 48
    body_out = struct.pack("<HHI16sIIIIII", 49, 0, ctl_code, bytes(body[8:24]),
                           off, 0, off, len(out), 0, 0)
    return status, body_out + out


def validate_negotiate_info(srv, conn, data):
    """FSCTL_VALIDATE_NEGOTIATE_INFO (MS-SMB2 3.3.5.15.12).

    An SMB 3.x client re-sends, inside a signed message, what it offered in its
    NEGOTIATE; the server checks it against what it actually received and
    answers with what it actually sent. The signature is what makes this
    worth doing: a man in the middle who downgraded the unsigned NEGOTIATE
    cannot forge this exchange. Windows and smbprotocol refuse the share when
    the answer is missing. A mismatch means the NEGOTIATE was tampered with
    and the connection is dropped, as the protocol requires.
    """
    if len(data) < 24:
        raise Smb2Error(wire.STATUS_INVALID_PARAMETER, "short VALIDATE_NEGOTIATE_INFO")
    caps, guid, mode, count = struct.unpack_from("<I16sHH", data, 0)
    dialects = tuple(struct.unpack_from("<%dH" % count, data, 24)) if (
        24 + count * 2 <= len(data)) else None
    claimed = (caps, guid, mode, dialects)
    if conn.negotiate_claims is None or claimed != conn.negotiate_claims:
        note("%s: VALIDATE_NEGOTIATE_INFO does not match its NEGOTIATE; "
             "dropping the connection" % (conn.peer[0],))
        conn.terminate = True
        raise Smb2Error(wire.STATUS_ACCESS_DENIED, "negotiate validation failed")
    return struct.pack("<I16sHH", wire.GLOBAL_CAP_LARGE_MTU, srv.guid,
                       wire.SECURITY_MODE_SIGNING_ENABLED, conn.dialect)


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

# The kernel side of Windows file sharing. srvnet.sys owns the TCP 445 listener
# and stays loaded while anything depends on it: srv2.sys (the SMB2/3 server)
# and srv.sys (the SMB1 server driver, loaded even when SMB1 is disabled).
# Stopping LanmanServer alone leaves all three running and 445 bound, so the
# takeover pauses them in dependency order -- srv, then srv2, then srvnet --
# and starts them again in reverse. Stop only, never disable.
_SMB_DRIVERS = ("srv", "srv2", "srvnet")
_TAKE_445_SERVICES = frozenset(("LanmanServer", "Browser") + _SMB_DRIVERS)


def _port_free(bind, port):
    """True if a TCP bind on (bind, port) succeeds right now: the direct test
    for whether Windows has released the port, rather than service states."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((bind, port))
        return True
    except OSError:
        return False
    finally:
        probe.close()


def take_445(stop_timeout=10.0, poll_interval=0.25, restore_timeout=10.0,
             bind="0.0.0.0", port_free=None):
    """Temporarily stop Windows file sharing before taking port 445.

    The service can report that Stop-Service returned while it is still
    stopping, so its state is verified before anything else happens. If TCP
    445 is still bound once LanmanServer is Stopped, the SMB kernel drivers
    (srv, srv2, srvnet) are paused too, in dependency order, and only when
    nothing outside Windows file sharing depends on them. Never enable SMB1
    or change any service's startup configuration.

    `port_free` is a zero-argument probe of TCP 445 (tests inject one; the
    default really binds `bind`:445). Returns a restore callback, or None if
    the takeover was refused or failed.
    """
    import subprocess

    system_root = (os.environ.get("SystemRoot") or os.environ.get("windir")
                   or r"C:\Windows")
    powershell = os.path.join(system_root, "System32", "WindowsPowerShell",
                              "v1.0", "powershell.exe")
    if not os.path.isfile(powershell):
        powershell = "powershell"
    if port_free is None:
        def port_free():
            return _port_free(bind, 445)

    def ps(cmd):
        return subprocess.run([powershell, "-NoProfile", "-Command", cmd],
                              capture_output=True, text=True)

    def status(name):
        return ps("(Get-Service %s -ErrorAction SilentlyContinue).Status" %
                  name).stdout.strip()

    def foreign_dependents(name):
        """Running services that depend on `name` and are not part of Windows
        file sharing itself. Other people's software is never paused."""
        out = ps("(Get-Service %s -ErrorAction SilentlyContinue).DependentServices"
                 " | Where-Object { $_.Status -eq 'Running' }"
                 " | ForEach-Object { $_.Name }" % name).stdout.split()
        return [n for n in out if n not in _TAKE_445_SERVICES]

    def wait_for(name, want, timeout):
        deadline = time.monotonic() + timeout
        while status(name) != want:
            if time.monotonic() >= deadline:
                return False
            time.sleep(poll_interval)
        return True

    initial = status("LanmanServer")
    browser = status("Browser")
    originally_running = initial == "Running"
    stopped_drivers = []  # in stop order; restored in reverse
    restored = []

    def start_service(name):
        """Start `name` only after its stop finishes; True once it is Running.

        A timed-out Stop-Service can leave Windows in StopPending. Start-Service
        cannot reliably run in that state, so wait for Stopped before starting.
        Only report success when the service actually reaches Running; on
        timeout, log the recovery command and let the caller retry later.
        """
        deadline = time.monotonic() + restore_timeout
        while True:
            state = status(name)
            if state == "Running":
                return True
            if state == "Stopped":
                res = ps("Start-Service %s -ErrorAction Stop" % name)
                if res.returncode:
                    note("[445] WARNING: Start-Service %s failed:" % name,
                         res.stderr.strip())
            elif state not in ("StopPending", "StartPending"):
                note("[445] WARNING: cannot verify %s state:" % name,
                     state or "unknown")
                return False
            if time.monotonic() >= deadline:
                note("[445] WARNING: %s was not restored; run 'Start-Service %s' "
                     "in elevated PowerShell after its pending transition "
                     "completes." % (name, name))
                return False
            time.sleep(poll_interval)

    def restore():
        """Put Windows file sharing back exactly as it was found, once.

        Drivers first, in reverse order (srvnet carries srv2; srv2 carries srv
        and LanmanServer), then the service. The callback stays retryable
        until LanmanServer is seen Running again.
        """
        if restored:
            return
        for name in reversed(stopped_drivers):
            note("[445] restoring %s driver..." % name)
            start_service(name)
        if not originally_running:
            restored.append(True)
            return
        note("[445] restoring LanmanServer...")
        if not start_service("LanmanServer"):
            return
        restored.append(True)
        if browser == "Running":
            ps("Start-Service Browser -ErrorAction SilentlyContinue")

    if initial == "Stopped":
        # The drivers can still hold 445 (for example after an interrupted
        # restore), so the port check below still runs.
        note("[445] LanmanServer was already stopped; leaving it unchanged.")
    elif not originally_running:
        note("[445] cannot verify LanmanServer is running or stopped (state: %s)." %
             (initial or "unknown"))
        return None

    try:
        if originally_running:
            note("[445] stopping LanmanServer (was: %s)..." % initial)
            res = ps("Stop-Service -Name LanmanServer -Force -ErrorAction Stop")
            if res.returncode:
                note("[445] could not stop LanmanServer; administrator rights "
                     "or a dependent service may prevent takeover.",
                     res.stderr.strip())
                restore()  # Even a failed stop request may have changed its state.
                return None
            if not wait_for("LanmanServer", "Stopped", stop_timeout):
                note("[445] LanmanServer did not reach Stopped within %.1fs." %
                     stop_timeout)
                restore()
                return None

        if port_free():
            return restore
        note("[445] TCP 445 is still bound by the SMB kernel drivers; "
             "pausing them too (srv, srv2, srvnet)...")
        for name in _SMB_DRIVERS:
            if status(name) != "Running":
                continue
            pinned = foreign_dependents(name)
            if pinned:
                note("[445] %s is pinned by %s. Not pausing other software: "
                     "keep the custom port instead of --take-445." %
                     (name, ", ".join(pinned)))
                restore()
                return None
            note("[445] stopping %s driver..." % name)
            stopped_drivers.append(name)  # A failed stop may still change state.
            # No -Force: Windows must refuse rather than stop a dependent we
            # did not inspect.
            res = ps("Stop-Service -Name %s -ErrorAction Stop" % name)
            if res.returncode:
                note("[445] could not stop the %s driver:" % name,
                     res.stderr.strip())
                restore()
                return None
            if not wait_for(name, "Stopped", stop_timeout):
                note("[445] %s did not reach Stopped within %.1fs." %
                     (name, stop_timeout))
                restore()
                return None
        if not port_free():
            # Release can lag the driver stop by a moment; the listener's own
            # retry covers that. Anything longer is reported by bind_hint.
            note("[445] 445 not released yet; the listener will retry briefly.")
    except BaseException:
        # Preserve the previous Windows file-sharing state if startup is
        # interrupted after the stop request.
        restore()
        raise
    return restore


def listen_after_take_445(server, bind, port, timeout=10.0, interval=0.25):
    """Retry only the expected Windows 445-release errors, on exactly 445.

    A successful Stop-Service is not proof that srvnet.sys has released its
    listener. No fallback port is safe: clients expecting 445 would miss it.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            return server.listen(bind, port)
        except OSError as exc:
            retryable = (exc.errno in (errno.EADDRINUSE, errno.EACCES)
                         or getattr(exc, "winerror", None) in (10013, 10048))
            if not retryable or time.monotonic() >= deadline:
                raise
            time.sleep(interval)


def bind_hint(port, err, take_445=False):
    """What to do about a failed bind, or "" when there is nothing specific.

    Port 445 is the case worth explaining. Neutrino's SMB driver and Windows'
    own client can only connect there, and the two ways it fails have
    different fixes on every OS.
    """
    if port != 445:
        return ""
    if take_445 and os.name == "nt":
        return ("TCP 445 remained unavailable after Windows file sharing "
                "(LanmanServer and the srv, srv2, srvnet drivers) was paused "
                "and the bind was retried. Another program or a Windows port "
                "reservation may still hold it. Check 'Get-NetTCPConnection "
                "-LocalPort 445' and 'netsh int ipv4 show excludedportrange "
                "tcp'. Do not disable Windows services; the custom port works "
                "for everything except Windows Explorer and Neutrino.")
    if err.errno in (errno.EADDRINUSE, getattr(errno, "WSAEADDRINUSE", -1)):
        if os.name == "nt":
            return ("Port 445 belongs to Windows file sharing. Tick 'Take port "
                    "445 (admin)' (--take-445) and run as Administrator.")
        if sys.platform == "darwin":
            return ("Something already serves port 445 -- usually macOS File "
                    "Sharing. Turn it off in System Settings > General > "
                    "Sharing while this server runs.")
        return ("Something already serves port 445 -- usually Samba (smbd). "
                "Stop it while this server runs, e.g. 'sudo systemctl stop smbd'.")
    if err.errno in (errno.EACCES, errno.EPERM, getattr(errno, "WSAEACCES", -1)):
        if os.name == "nt":
            return "Run as Administrator to bind port 445."
        return ("Ports below 1024 need root on Linux and macOS. Either run this "
                "server with sudo, or keep it on 1445 unprivileged and forward "
                "445 to it once (Linux): 'sudo iptables -t nat -A PREROUTING -p "
                "tcp --dport 445 -j REDIRECT --to-ports 1445'.")
    return ""


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
    ap.add_argument("--session-summary", action="store_true",
                    help="print one SESSION line per console when it goes quiet "
                         "for a minute or the server stops")
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

    try:
        server = Smb2Server(shares, auth, ceiling=ceiling, read_only=args.read_only,
                            server_name=args.server_name, domain=args.domain)
    except ValueError as e:
        ap.error(str(e))
    if args.session_summary:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if os.path.isdir(os.path.join(root, "launcher")) and root not in sys.path:
            sys.path.append(root)
        from launcher.session_stats import SessionStats
        server.session_stats = SessionStats("smbv%d" % args.smb_version)

    # One cleanup owner from take-445 on: a stop request can land during the
    # bind or the banner, not only in serve_forever(). take_445 restores by
    # itself if it is interrupted mid-stop.
    restore = None
    try:
        want_port = args.port
        if args.take_445:
            want_port = 445
            restore = take_445(bind=args.bind)
            if restore is None:
                return 1
        try:
            if args.take_445:
                port = listen_after_take_445(server, args.bind, want_port)
            else:
                port = server.listen(args.bind, want_port)
        except OSError as e:
            print("ERROR: cannot bind %s:%d -- %s" % (args.bind, want_port, e),
                  file=sys.stderr)
            hint = bind_hint(want_port, e, take_445=args.take_445)
            if hint:
                print(hint, file=sys.stderr)
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
        if port != 445:
            note("  Windows and Neutrino can only connect on port 445; this is %d. "
                 "Map it, or run the SMBv1 mode for OPL." % port)
        if ceiling < wire.DIALECT_300:
            note("  SMBv2 mode: Neutrino's SMB driver offers only SMB 3.0 and needs "
                 "the SMBv3 server.")

        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            server.stop()
        finally:
            # Always restore, even if server.stop() itself raises.
            if restore is not None:
                restore()
    return 0


if __name__ == "__main__":
    sys.exit(main())
