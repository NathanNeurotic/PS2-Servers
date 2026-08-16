#!/usr/bin/env python3
# PS2 Servers SMBv1 server -- a tiny, dependency-free SMB1/CIFS server that Open-PS2-Loader
# (and forks) can browse + read games from, so SMB keeps working on hosts where the OS has
# disabled SMBv1 / removed NTLMv1 (Windows 11 24H2/25H2). It speaks "NT LM 0.12" itself and
# accepts GUEST logons, so it bypasses the host's SMB stack entirely: OPL connects to THIS
# program on a custom TCP port (OPL's "SMB Port" field), not to Windows' own SMB service.
#
# Pure Python 3 stdlib (socket/struct/threading/os/argparse/time). No third-party deps, so it
# runs as a bare `.py` with zero antivirus false-positives. Protocol constants/offsets were
# authored from MS-CIFS and Open-PS2-Loader's own cdvdman/smb.h -- no third-party code copied.
#
#   python smbserver_opl.py --share games=D:/PS2Games
#   then in OPL: SMB IP = this PC's LAN IP, SMB Port = 1111 (the printed port), Share = games,
#   User = guest, Password = blank. Writable by default (OPL saves + VMC-on-SMB); pass --read-only to lock.
#
# License: same as the surrounding PS2 Servers project.

import argparse
import hmac
import os
import socket
import struct
import sys
import threading
import time

# --------------------------------------------------------------------------------------------
# SMB1 / CIFS constants (authored from MS-CIFS + cdvdman/smb.h, all little-endian on the wire)
# --------------------------------------------------------------------------------------------
SMB_MAGIC = b"\xffSMB"

# Commands
SMB_COM_CREATE_DIRECTORY = 0x00
SMB_COM_CLOSE = 0x04
SMB_COM_CHECK_DIRECTORY = 0x10
SMB_COM_QUERY_INFORMATION_DISK = 0x80
SMB_COM_TRANSACTION = 0x25
SMB_COM_ECHO = 0x2B
SMB_COM_OPEN_ANDX = 0x2D
SMB_COM_READ_ANDX = 0x2E
SMB_COM_WRITE_ANDX = 0x2F
SMB_COM_TRANS2 = 0x32
SMB_COM_TREE_DISCONNECT = 0x71
SMB_COM_NEGOTIATE = 0x72
SMB_COM_SESSION_SETUP_ANDX = 0x73
SMB_COM_LOGOFF_ANDX = 0x74
SMB_COM_TREE_CONNECT_ANDX = 0x75
SMB_COM_NT_CREATE_ANDX = 0xA2
SMB_COM_NONE = 0xFF  # AndX terminator: we never chain replies

# NTSTATUS values we use (only those whose bits 8..15 are zero, so the lossy
# ErrorClass/ErrorCode split OPL reconstructs as Eclass|(Ecode<<16) is exact)
STATUS_SUCCESS = 0x00000000
STATUS_NOT_IMPLEMENTED = 0xC0000002
STATUS_ACCESS_DENIED = 0xC0000022
STATUS_OBJECT_NAME_NOT_FOUND = 0xC0000034
STATUS_OBJECT_NAME_COLLISION = 0xC0000035
STATUS_LOGON_FAILURE = 0xC000006D

# NEGOTIATE response capabilities. UNICODE deliberately OFF so every string stays ASCII/OEM --
# that keeps the whole server single-byte and avoids the large UTF-16 surface (smbman supports
# the non-unicode branch fully).
CAP_LARGE_FILES = 0x00000008
CAP_NT_SMBS = 0x00000010
CAP_STATUS32 = 0x00000040
CAP_LARGE_READX = 0x00004000
SERVER_CAPS = CAP_NT_SMBS | CAP_STATUS32 | CAP_LARGE_FILES | CAP_LARGE_READX

# Header Flags2: KNOWS_LONG_NAMES | 32BIT_STATUS (NO unicode bit 0x8000)
FLAGS2 = 0x4001
FLAGS1_REPLY = 0x80  # SERVER_TO_REDIR

# Ext file attributes
ATTR_NORMAL = 0x80
ATTR_DIRECTORY = 0x10

# NT_CREATE_ANDX CreateDisposition values. These are not bit flags.
FILE_SUPERSEDE = 0x00000000
FILE_OPEN = 0x00000001
FILE_CREATE = 0x00000002
FILE_OPEN_IF = 0x00000003
FILE_OVERWRITE = 0x00000004
FILE_OVERWRITE_IF = 0x00000005

# NT_CREATE_ANDX CreateAction response values.
FILE_SUPERSEDED = 0x00000000
FILE_OPENED = 0x00000001
FILE_CREATED = 0x00000002
FILE_OVERWRITTEN = 0x00000003

# CreateOptions bits used here.
FILE_DIRECTORY_FILE = 0x00000001

# TRANS2 subcommands
TRANS2_FIND_FIRST2 = 0x01
TRANS2_FIND_NEXT2 = 0x02
TRANS2_QUERY_PATH_INFORMATION = 0x05

# FIND info level
SMB_FIND_FILE_BOTH_DIRECTORY_INFO = 0x0104
SMB_QUERY_FILE_BASIC_INFO = 0x0101
SMB_QUERY_FILE_STANDARD_INFO = 0x0102

VERBOSE = False


def log(*a):
    if VERBOSE:
        print("  [smb]", *a, file=sys.stderr, flush=True)


def to_filetime(unix_ts):
    """Windows FILETIME: 100ns ticks since 1601-01-01 UTC."""
    if unix_ts <= 0:
        return 0
    return int(unix_ts * 10_000_000) + 116444736000000000


# --------------------------------------------------------------------------------------------
# NetBIOS-over-TCP (direct-TCP) session framing: 4-byte header, the ONLY big-endian field.
# byte0 = 0x00 (session message); bytes1-3 = 24-bit BE length of the SMB message that follows.
# --------------------------------------------------------------------------------------------
def send_msg(sock, smb_msg):
    sock.sendall(b"\x00" + len(smb_msg).to_bytes(3, "big") + smb_msg)


def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def recv_msg(sock):
    """Return the next SMB message bytes, or b"" for a keep-alive, or None on EOF."""
    hdr = _recv_exact(sock, 4)
    if hdr is None:
        return None
    length = int.from_bytes(hdr[1:4], "big")
    if hdr[0] != 0x00:
        # Session keep-alive / other: consume any body, signal "ignore".
        if length:
            _recv_exact(sock, length)
        return b""
    if length == 0:
        return b""
    return _recv_exact(sock, length)


# --------------------------------------------------------------------------------------------
# SMB header (fixed 32 bytes, little-endian). The 32-bit NTSTATUS is written as
# ErrorClass(byte5) = status&0xff, reserved(byte6)=0, ErrorCode(bytes7-8) = status>>16.
# --------------------------------------------------------------------------------------------
def pack_header(cmd, status, tid, pid, uid, mid):
    h = bytearray(32)
    h[0:4] = SMB_MAGIC
    h[4] = cmd
    h[5] = status & 0xFF
    h[6] = 0
    struct.pack_into("<H", h, 7, (status >> 16) & 0xFFFF)
    h[9] = FLAGS1_REPLY
    struct.pack_into("<H", h, 10, FLAGS2)
    # bytes 12..23 = security features / reserved (zero)
    struct.pack_into("<H", h, 24, tid & 0xFFFF)
    struct.pack_into("<H", h, 26, pid & 0xFFFF)
    struct.pack_into("<H", h, 28, uid & 0xFFFF)
    struct.pack_into("<H", h, 30, mid & 0xFFFF)
    return bytes(h)


class Req:
    """Parsed request header + raw message (for body parsing)."""

    __slots__ = ("cmd", "tid", "pid", "uid", "mid", "msg", "wordcount", "params", "data")

    def __init__(self, msg):
        self.msg = msg
        self.cmd = msg[4]
        self.tid = struct.unpack_from("<H", msg, 24)[0]
        self.pid = struct.unpack_from("<H", msg, 26)[0]
        self.uid = struct.unpack_from("<H", msg, 28)[0]
        self.mid = struct.unpack_from("<H", msg, 30)[0]
        # Generic body: WordCount(1) + Params(WordCount*2) + ByteCount(2) + Data
        wc = msg[32]
        self.wordcount = wc
        self.params = msg[33 : 33 + wc * 2]
        bc_off = 33 + wc * 2
        bc = struct.unpack_from("<H", msg, bc_off)[0] if bc_off + 2 <= len(msg) else 0
        self.data = msg[bc_off + 2 : bc_off + 2 + bc]


def build_body(params, data):
    assert len(params) % 2 == 0, "params must be a whole number of 16-bit words"
    return bytes([len(params) // 2]) + params + struct.pack("<H", len(data)) + data


# --------------------------------------------------------------------------------------------
# Share + path handling
# --------------------------------------------------------------------------------------------
class Share:
    def __init__(self, name, root):
        self.name = name
        # realpath, not abspath: the root itself is commonly reached through a
        # symlink (/home/user/games -> /mnt/disk2/games), and if the root is not
        # already resolved then every containment test below compares against
        # the wrong string.
        self.root = os.path.realpath(root)

    def resolve(self, smb_path):
        """Map an SMB path ('\\dir\\file' or '/dir/file') under the share root, blocking escapes.
        Returns an absolute local path, or None if it would escape the share.

        Containment is decided on the REAL path. The previous version resolved
        with os.path.abspath, which normalises ".." textually but does not
        follow symbolic links, so a symlink inside the share resolved to a name
        that still began with the share root while opening a file outside it:

            share/elsewhere -> /tmp/outside
            resolve("elsewhere/secret.txt")
              -> .../share/elsewhere/secret.txt   (accepted: starts with root)
              real path .../outside/secret.txt    (outside the share)

        Never remotely exploitable on its own -- this server implements no
        command that creates a symlink, so a client cannot plant one -- but it
        meant the declared root was not the actual boundary, and symlinking a
        games folder onto a second disk is an ordinary thing to do. The UDPFS
        guard, the SMB2 server and Edge all already refused this; smb2_paths.py
        documented the gap by name and it stayed open here, in the SMB server
        most OPL users actually run.

        Deliberately NOT swapped for smb2_server.smb2_paths.Share. That resolver
        also refuses "..", reserved device names, alternate data streams and
        trailing dots -- all good rules, and all changes to which paths this
        hardware-validated server accepts. Adopting them is a separate change
        that wants a console to test against. This fixes the boundary only.
        """
        p = smb_path.replace("\\", "/").lstrip("/")
        # Drop a leading "share/" if the client included it (some paths are share-relative already).
        full = os.path.realpath(os.path.join(self.root, p))
        if self._inside(full):
            return full
        return None

    def _inside(self, real):
        """True if a resolved path is the share root or beneath it.

        os.path.commonpath rather than a startswith on root + os.sep, because
        the string test accepts a sibling whose name merely begins with the
        root's: with a root of /srv/games it lets /srv/games-private through.
        commonpath compares whole components.
        """
        if real == self.root:
            return True
        try:
            return os.path.commonpath([real, self.root]) == self.root
        except ValueError:
            # Different drives on Windows, which is conclusively outside.
            return False


class OpenFile:
    __slots__ = ("path", "fh", "is_dir", "size")

    def __init__(self, path, is_dir):
        self.path = path
        self.is_dir = is_dir
        self.size = 0 if is_dir else os.path.getsize(path)
        self.fh = None
        if not is_dir:
            self.fh = open(path, "rb")  # read-only handle; writes are gated separately

    def close(self):
        if self.fh:
            try:
                self.fh.close()
            except OSError:
                pass
            self.fh = None


class Conn:
    """Per-connection state for one PS2 TCP session."""

    def __init__(self, server):
        self.server = server
        self.uid = 0
        self.next_fid = 0x1000
        self.next_tid = 0x0001
        self.next_sid = 0x0001
        self.trees = {}  # tid -> Share or "IPC"
        self.files = {}  # fid -> OpenFile
        self.searches = {}  # sid -> (entries list, cursor)

    def alloc_fid(self):
        self.next_fid = (self.next_fid + 1) & 0xFFFF or 0x1000
        return self.next_fid

    def alloc_tid(self):
        self.next_tid = (self.next_tid + 1) & 0xFFFF or 0x0001
        return self.next_tid

    def alloc_sid(self):
        self.next_sid = (self.next_sid + 1) & 0xFFFF or 0x0001
        return self.next_sid

    def cleanup(self):
        for f in self.files.values():
            f.close()
        self.files.clear()


# --------------------------------------------------------------------------------------------
SMB2_MAGIC = b"\xfeSMB"


def pack_smb2_hdr(cmd, status=0, flags=0x00000001, message_id=0, session_id=0, async_id=0, credits=1):
    return struct.pack(
        "<4sHHIIHIIQQQ16s",
        SMB2_MAGIC,
        64,             # StructureSize
        0,              # CreditCharge
        status,         # Status
        cmd,            # Command
        credits,        # Credits
        flags,          # Flags (0x01 = SERVER_TO_REDIR)
        0,              # NextCommand
        message_id,     # MessageId
        async_id,       # AsyncId
        session_id,     # SessionId
        b"\x00" * 16    # Signature
    )


class Smb2Req:
    def __init__(self, msg):
        (self.magic, self.struct_size, self.credit_charge, self.status,
         self.cmd, self.credits, self.flags, self.next_cmd,
         self.message_id, self.async_id, self.session_id, self.sig) = struct.unpack_from("<4sHHIIHIIQQQ16s", msg, 0)
        self.body = msg[64:]


def h2_negotiate(conn, r, smb_version=2):
    dialect = 0x0300 if smb_version == 3 else 0x0202
    blob = b"\x60\x09\x06\x07\x2b\x06\x01\x04\x01\x82\x37\x02\x02\x0a"
    params = struct.pack(
        "<HHH16sIIIIQQHHH",
        65,             # StructureSize
        0x0001,         # SecurityMode
        dialect,        # DialectRevision
        b"\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f\x10",
        0x00000001,     # Capabilities
        65536,          # MaxTransactSize
        65536,          # MaxReadSize
        65536,          # MaxWriteSize
        0,              # SystemTime
        0,              # ServerStartTime
        128,            # SecurityBufferOffset
        len(blob),      # SecurityBufferLength
        0               # Reserved
    )
    return params + blob, STATUS_SUCCESS


def h2_session_setup(conn, r):
    if conn.uid == 0:
        conn.uid = 0x0000000100000001
    body = struct.pack(
        "<HHHH",
        9,              # StructureSize
        0x0001,         # SessionFlags (IS_GUEST)
        0,              # SecurityBufferOffset
        0               # SecurityBufferLength
    )
    return body, STATUS_SUCCESS


def h2_tree_connect(conn, r):
    body = struct.pack(
        "<BBII",
        16,             # StructureSize
        0x01,           # ShareType DISK
        0,              # Reserved
        0x00000030,     # ShareFlags
        0x000f001f      # MaximalAccess
    )
    return body, STATUS_SUCCESS


def h2_create(conn, r):
    file_id = b"\x01\x00\x00\x00\x00\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00\x00"
    body = struct.pack(
        "<BBIQQQQQQII16sII",
        89,             # StructureSize
        0x00,           # OplockLevel
        0x00000001,     # CreateAction
        0, 0, 0, 0, 0, 0,
        0x00000020,     # FileAttributes
        0,              # Reserved
        file_id,        # FileId
        0, 0
    )
    return body, STATUS_SUCCESS


def h2_read(conn, r):
    data = b""
    data_offset = 80
    hdr = struct.pack("<BBIBI", 16, data_offset, len(data), 0, 0)
    return hdr + data, STATUS_SUCCESS


def h2_write(conn, r):
    hdr = struct.pack("<HHII", 17, 0, 0, 0)
    return hdr, STATUS_SUCCESS


def h2_close(conn, r):
    body = struct.pack("<HHQQQQQI", 60, 0, 0, 0, 0, 0, 0, 0, 0)
    return body, STATUS_SUCCESS


def h2_echo(conn, r):
    body = struct.pack("<HH", 4, 0)
    return body, STATUS_SUCCESS


def h2_generic_ok(conn, r):
    return struct.pack("<HH", 4, 0), STATUS_SUCCESS


SMB2_HANDLERS = {
    0x0000: h2_negotiate,
    0x0001: h2_session_setup,
    0x0002: h2_generic_ok,
    0x0003: h2_tree_connect,
    0x0004: h2_generic_ok,
    0x0005: h2_create,
    0x0006: h2_close,
    0x0008: h2_read,
    0x0009: h2_write,
    0x0012: h2_echo,
}


def h_negotiate(conn, r):
    dialects = []
    d = r.data
    i = 0
    while i < len(d):
        if d[i] == 0x02:
            end = d.index(0, i + 1) if 0 in d[i + 1 :] else len(d)
            dialects.append(d[i + 1 : end].decode("ascii", "ignore"))
            i = end + 1
        else:
            i += 1

    ver = getattr(conn.server, "smb_version", 1)
    if ver in (2, 3):
        smb2_idx = None
        for smb2_name in ("SMB 2.002", "SMB 2.??? ", "2.002", "2.10"):
            if smb2_name in dialects:
                smb2_idx = dialects.index(smb2_name)
                break
        if smb2_idx is not None:
            params = struct.pack(
                "<HBHHIIIIqHB",
                smb2_idx,       # DialectIndex
                0x02,           # SecurityMode (SMB2 multi-protocol)
                1, 1, 65535, 65536, 0, SERVER_CAPS, 0, 0, 0
            )
            return params, b"WORKGROUP\x00", STATUS_SUCCESS

    try:
        idx = dialects.index("NT LM 0.12")
    except ValueError:
        idx = 0
    # 17-word (34-byte) NT LM 0.12 response. WordCount MUST be exactly 17 or OPL retries forever.
    params = struct.pack(
        "<HBHHIIIIqHB",
        idx,            # DialectIndex
        0x00,           # SecurityMode = share-level + plaintext  => guest path, no challenge
        1,              # MaxMpxCount
        1,              # MaxNumberVcs
        65535,          # MaxBufferSize (>= OPL's 8192)
        65536,          # MaxRawSize
        0,              # SessionKey (OPL echoes it back)
        SERVER_CAPS,    # Capabilities (unicode OFF)
        0,              # SystemTime (FILETIME; 0 is fine)
        0,              # ServerTimeZone
        0,              # EncryptionKeyLength = 0  => no challenge, plaintext/guest
    )
    assert len(params) == 34, len(params)
    data = b"WORKGROUP\x00"  # PrimaryDomain (OEM); OPL copies+re-sends it
    return params, data, STATUS_SUCCESS


def _parse_session_setup(r):
    """(account, password) from SESSION_SETUP_ANDX. Either may be empty.

    NT LM 0.12 puts two password fields at the front of the data -- the ANSI one
    then the Unicode one -- whose lengths are words 7 and 8 of the parameters.
    The account name is the first NUL-terminated string after them.

    The password is PLAINTEXT, because NEGOTIATE advertises EncryptionKeyLength
    0. See h_session_setup for why that is deliberately not being changed.
    """
    if r.wordcount < 13:
        return "", ""
    ansi_len, uni_len = struct.unpack_from("<HH", r.params, 14)
    d = r.data
    at = 0
    ansi_pw = d[at:at + ansi_len]
    at += ansi_len
    uni_pw = d[at:at + uni_len]
    at += uni_len
    end = d.index(0, at) if 0 in d[at:] else len(d)
    account = d[at:end].decode("ascii", "ignore")
    password = ansi_pw.rstrip(b"\x00").decode("ascii", "ignore")
    if not password and uni_pw:
        password = uni_pw.decode("utf-16-le", "ignore").rstrip("\x00")
    return account, password


def h_session_setup(conn, r):
    """Guest by default; a real check only when credentials are configured.

    With no users configured this is byte-for-byte what it has always been:
    accept unconditionally, hand out a UID, report Action=guest. That is the
    path OPL and POPSTARTER are tested against on real hardware, and it is not
    worth risking for an option nobody asked to have on by default.

    With users configured, the account and password in the request must match.
    The password arrives in the CLEAR -- NEGOTIATE advertises share-level
    security with no encryption key, and the alternative (NTLM challenge and
    response over SMB1) needs DES, which is no more present in the standard
    library than AES is. Changing NEGOTIATE to demand a challenge would also
    change the bytes OPL sees on every connection, which is exactly the kind of
    change that turns a working console into a black screen.

    So this keeps other machines off the share. It is not protection against
    someone watching the network; the SMBv2/SMBv3 modes do a real NTLMv2
    exchange for that.
    """
    users = getattr(conn.server, "users", None)
    action = 0x0001                                   # logged on as guest
    if users:
        account, password = _parse_session_setup(r)
        expected = users.get(account.casefold())
        # compare_digest, not ==: a byte-at-a-time comparison leaks how much of
        # the password was right through its timing.
        if expected is None or not hmac.compare_digest(password, expected):
            log("session setup refused for account %r" % account)
            return None, None, STATUS_LOGON_FAILURE
        action = 0x0000                               # a real account, not guest
        log("session setup accepted for account %r" % account)

    if conn.uid == 0:
        conn.uid = 0x0800
    params = struct.pack("<BBHH", SMB_COM_NONE, 0, 0, action)  # AndX none
    data = b"OPLSMB\x00OPLSMB\x00"  # NativeOS, NativeLanMan (ignored by OPL)
    return params, data, STATUS_SUCCESS, conn.uid  # 4-tuple => override UID in header


def _parse_treeconnect_path(r):
    # WordCount=4: AndXCommand,AndXReserved,AndXOffset,Flags,PasswordLength
    if r.wordcount >= 4:
        pwlen = struct.unpack_from("<H", r.params, 6)[0]
    else:
        pwlen = 1
    d = r.data
    off = pwlen  # skip the share-level password (1 byte in guest mode)
    end = d.index(0, off) if 0 in d[off:] else len(d)
    path = d[off:end].decode("ascii", "ignore")
    # path like "\\SERVER\share" -> take the last component
    share = path.replace("/", "\\").rstrip("\\").split("\\")[-1]
    return share


def h_tree_connect(conn, r):
    share_name = _parse_treeconnect_path(r)
    tid = conn.alloc_tid()
    if share_name.upper() == "IPC$":
        conn.trees[tid] = "IPC"
        service = b"IPC\x00"
    else:
        sh = conn.server.shares.get(share_name) or conn.server.shares.get(share_name.lower())
        if sh is None:
            # Be lenient: if exactly one share is configured, use it (OPL may send an odd name).
            if len(conn.server.shares) == 1:
                sh = next(iter(conn.server.shares.values()))
            else:
                return None, None, STATUS_OBJECT_NAME_NOT_FOUND
        conn.trees[tid] = sh
        service = b"A:\x00"
    log("tree connect", share_name, "-> tid", tid)
    params = struct.pack("<BBHH", SMB_COM_NONE, 0, 0, 0x0001)  # AndX none, OptionalSupport
    data = service + b"NTFS\x00"
    return params, data, STATUS_SUCCESS, None, tid  # 5-tuple => override TID in header


def _share_for_tid(conn, r):
    t = conn.trees.get(r.tid)
    if isinstance(t, Share):
        return t
    return None


def h_open_andx(conn, r):
    # In-game ISO open (and smbman fallback). Filename is an ASCII z-string in the data.
    sh = _share_for_tid(conn, r)
    d = r.data
    end = d.index(0) if 0 in d else len(d)
    name = d[:end].decode("ascii", "ignore")
    if sh is None:
        return None, None, STATUS_OBJECT_NAME_NOT_FOUND
    local = sh.resolve(name)
    if local is None or not os.path.isfile(local):
        log("open MISS", name)
        return None, None, STATUS_OBJECT_NAME_NOT_FOUND
    of = OpenFile(local, is_dir=False)
    fid = conn.alloc_fid()
    conn.files[fid] = of
    log("open", name, "fid", fid, "size", of.size)
    params = struct.pack(
        "<BBHHHIIHHHHIH",
        SMB_COM_NONE, 0, 0,        # AndX none
        fid,                       # FID
        0,                         # FileAttributes
        0,                         # LastWriteTime
        of.size & 0xFFFFFFFF,      # FileSize (low 32)
        0,                         # GrantedAccess
        0,                         # FileType
        0,                         # IPCState
        0x0001,                    # Action (existed/opened)
        0,                         # ServerFID
        0,                         # reserved
    )
    return params, b"", STATUS_SUCCESS


def h_nt_create_andx(conn, r):
    # OPL's ioman layer uses NT_CREATE_ANDX for ordinary opens AND for
    # O_CREAT/O_TRUNC. The old server treated every request as OPEN:
    # a missing file returned NAME_NOT_FOUND and overwrite requests did
    # not truncate, so a fresh CFG/VMC could never be born and shorter
    # rewrites left stale tail bytes behind.
    sh = _share_for_tid(conn, r)
    if sh is None:
        return None, None, STATUS_OBJECT_NAME_NOT_FOUND
    if len(r.params) < 39:
        return None, None, STATUS_ACCESS_DENIED

    name_len = struct.unpack_from("<H", r.params, 5)[0]
    disposition = struct.unpack_from("<I", r.params, 35)[0]
    create_options = struct.unpack_from("<I", r.params, 39)[0] if len(r.params) >= 43 else 0
    if disposition > FILE_OVERWRITE_IF:
        return None, None, STATUS_ACCESS_DENIED

    raw = r.data
    if raw[:1] == b"\x00":
        raw = raw[1:]
    name = raw[:name_len].split(b"\x00", 1)[0].decode("ascii", "ignore")
    local = sh.resolve(name)
    if local is None:
        return None, None, STATUS_ACCESS_DENIED

    exists = os.path.exists(local)
    requested_dir = bool(create_options & FILE_DIRECTORY_FILE)
    action = FILE_OPENED

    if exists:
        is_dir = os.path.isdir(local)
        if disposition == FILE_CREATE:
            return None, None, STATUS_OBJECT_NAME_COLLISION
        if requested_dir and not is_dir:
            return None, None, STATUS_ACCESS_DENIED
        if disposition in (FILE_SUPERSEDE, FILE_OVERWRITE, FILE_OVERWRITE_IF):
            if conn.server.read_only or is_dir:
                return None, None, STATUS_ACCESS_DENIED
            try:
                # Truncate during CREATE, before WRITE_ANDX, matching
                # O_TRUNC and preventing stale tail bytes on shorter CFGs.
                with open(local, "wb"):
                    pass
            except OSError:
                return None, None, STATUS_ACCESS_DENIED
            action = FILE_SUPERSEDED if disposition == FILE_SUPERSEDE else FILE_OVERWRITTEN
    else:
        if disposition in (FILE_OPEN, FILE_OVERWRITE):
            return None, None, STATUS_OBJECT_NAME_NOT_FOUND
        if conn.server.read_only:
            return None, None, STATUS_ACCESS_DENIED
        parent = os.path.dirname(local)
        if not os.path.isdir(parent):
            return None, None, STATUS_OBJECT_NAME_NOT_FOUND
        try:
            if requested_dir:
                os.mkdir(local)
            else:
                with open(local, "xb"):
                    pass
        except FileExistsError:
            return None, None, STATUS_OBJECT_NAME_COLLISION
        except OSError:
            return None, None, STATUS_ACCESS_DENIED
        action = FILE_CREATED

    is_dir = os.path.isdir(local)
    try:
        of = OpenFile(local, is_dir=is_dir)
        mtime = to_filetime(os.path.getmtime(local))
    except OSError:
        return None, None, STATUS_ACCESS_DENIED
    fid = conn.alloc_fid()
    conn.files[fid] = of
    size = of.size
    log("ntcreate", repr(name), "fid", fid,
        "dir" if is_dir else "size %d" % size, "action", action)
    params = struct.pack(
        "<BBHBHIqqqqIQQHHB",
        SMB_COM_NONE, 0, 0,
        0,
        fid,
        action,
        mtime, mtime, mtime, mtime,
        ATTR_DIRECTORY if is_dir else ATTR_NORMAL,
        size, size,
        0,
        0,
        1 if is_dir else 0,
    )
    return params, b"", STATUS_SUCCESS

def h_read_andx(conn, r):
    # HOT PATH. Response struct ReadAndXResponse_t is 59 bytes; data starts at offset 59
    # (DataOffset=59, zero pad) -- verified against cdvdman/smb.h:434-449 + smb.c read logic.
    p = r.params
    # ReadAndXRequest_t params: AndX(4) FID(2) OffsetLow(4) MaxCountLow(2) MinCount(2)
    #                           Timeout/MaxCountHigh(4) Remaining(2) OffsetHigh(4)
    fid = struct.unpack_from("<H", p, 4)[0]
    off_low = struct.unpack_from("<I", p, 6)[0]
    maxcount_low = struct.unpack_from("<H", p, 10)[0]
    maxcount_high = struct.unpack_from("<H", p, 14)[0]
    off_high = struct.unpack_from("<I", p, 20)[0] if len(p) >= 24 else 0
    maxcount = maxcount_low | (maxcount_high << 16)
    of = conn.files.get(fid)
    if of is None or of.fh is None:
        return None, None, STATUS_OBJECT_NAME_NOT_FOUND
    offset = off_low | (off_high << 32)
    of.fh.seek(offset)
    chunk = of.fh.read(min(maxcount, 0xFFFF) if maxcount else 0)
    n = len(chunk)
    DATA_OFFSET = 59  # == sizeof(ReadAndXResponse_t); data immediately follows ByteCount
    params = struct.pack(
        "<BBHHHHHHI6s",
        SMB_COM_NONE, 0, 0,        # AndX none
        0,                         # Remaining
        0,                         # DataCompactionMode
        0,                         # reserved
        n & 0xFFFF,                # DataLengthLow
        DATA_OFFSET,               # DataOffset
        n >> 16,                   # DataLengthHigh
        b"\x00\x00\x00\x00\x00\x00",  # reserved2[6]
    )
    return params, chunk, STATUS_SUCCESS


def h_write_andx(conn, r):
    if conn.server.read_only:
        return None, None, STATUS_ACCESS_DENIED
    p = r.params
    fid = struct.unpack_from("<H", p, 4)[0]
    off_low = struct.unpack_from("<I", p, 6)[0]
    dlen_high = struct.unpack_from("<H", p, 18)[0]
    dlen_low = struct.unpack_from("<H", p, 20)[0]
    data_off = struct.unpack_from("<H", p, 22)[0]
    off_high = struct.unpack_from("<I", p, 24)[0] if len(p) >= 28 else 0
    n = dlen_low | (dlen_high << 16)
    of = conn.files.get(fid)
    if of is None:
        return None, None, STATUS_ACCESS_DENIED
    payload = r.msg[data_off : data_off + n]
    try:
        with open(of.path, "r+b") as w:
            w.seek(off_low | (off_high << 32))
            w.write(payload)
        written = len(payload)
    except OSError:
        return None, None, STATUS_ACCESS_DENIED
    params = struct.pack("<BBHHHHH", SMB_COM_NONE, 0, 0, written & 0xFFFF, 0, 0, 0)
    return params, b"", STATUS_SUCCESS


def h_close(conn, r):
    fid = struct.unpack_from("<H", r.params, 0)[0] if len(r.params) >= 2 else 0
    of = conn.files.pop(fid, None)
    if of:
        of.close()
    return b"", b"", STATUS_SUCCESS


def h_echo(conn, r):
    # smbman keep-alive: bounce the payload back with SequenceNumber=1.
    params = struct.pack("<H", 1)
    return params, r.data, STATUS_SUCCESS


def h_tree_disconnect(conn, r):
    conn.trees.pop(r.tid, None)
    return b"", b"", STATUS_SUCCESS


def h_logoff(conn, r):
    params = struct.pack("<BBH", SMB_COM_NONE, 0, 0)
    return params, b"", STATUS_SUCCESS


def h_query_disk(conn, r):
    # All u16 to avoid overflow; smbman just needs non-zero free space.
    params = struct.pack("<HHHHH", 0xFFFF, 64, 512, 0xFFFF, 0)
    return params, b"", STATUS_SUCCESS


def h_create_directory(conn, r):
    # RiptOPL calls mkdir() for CFG/THM/LNG/ART/VMC/... on SMB connect.
    # SMB_COM_CREATE_DIRECTORY carries BufferFormat=0x04 before the
    # NUL-terminated share-relative directory name.
    if conn.server.read_only:
        return None, None, STATUS_ACCESS_DENIED
    sh = _share_for_tid(conn, r)
    if sh is None:
        return None, None, STATUS_OBJECT_NAME_NOT_FOUND
    raw = r.data[1:] if r.data[:1] == b"\x04" else r.data
    name = raw.split(b"\x00", 1)[0].decode("ascii", "ignore")
    local = sh.resolve(name)
    if local is None:
        return None, None, STATUS_ACCESS_DENIED
    if os.path.exists(local):
        return None, None, STATUS_OBJECT_NAME_COLLISION
    if not os.path.isdir(os.path.dirname(local)):
        return None, None, STATUS_OBJECT_NAME_NOT_FOUND
    try:
        os.mkdir(local)
    except FileExistsError:
        return None, None, STATUS_OBJECT_NAME_COLLISION
    except OSError:
        return None, None, STATUS_ACCESS_DENIED
    log("mkdir", repr(name))
    return b"", b"", STATUS_SUCCESS


def h_check_directory(conn, r):
    sh = _share_for_tid(conn, r)
    d = r.data
    end = d.index(0) if 0 in d else len(d)
    name = d[:end].decode("ascii", "ignore")
    if sh is None:
        return None, None, STATUS_OBJECT_NAME_NOT_FOUND
    local = sh.resolve(name or "\\")
    if local and os.path.isdir(local):
        return b"", b"", STATUS_SUCCESS
    return None, None, STATUS_OBJECT_NAME_NOT_FOUND


# --------------------------------------------------------------------------------------------
# TRANS2 (0x32): FIND_FIRST2 / FIND_NEXT2 / QUERY_PATH_INFORMATION
# --------------------------------------------------------------------------------------------
def _trans2_parse(r):
    p = r.params
    # Transaction2 request fixed words (we only need the offsets + setup)
    param_count = struct.unpack_from("<H", p, 18)[0]
    param_off = struct.unpack_from("<H", p, 20)[0]
    data_count = struct.unpack_from("<H", p, 22)[0]
    data_off = struct.unpack_from("<H", p, 24)[0]
    setup_count = p[26]
    setup = struct.unpack_from("<H", p, 28)[0] if setup_count >= 1 else 0
    t_params = r.msg[param_off : param_off + param_count]
    t_data = r.msg[data_off : data_off + data_count]
    return setup, t_params, t_data


def _build_trans2_reply(conn, r, status, t_params, t_data):
    if status != STATUS_SUCCESS:
        return None, None, status
    # Fixed 10-word Transaction2 response header, then 4-aligned params + 4-aligned data.
    base = 32 + 1 + 20 + 2  # header + WC + 10 words + ByteCount = 55
    param_off = (base + 3) & ~3
    pad1 = param_off - base
    data_start = param_off + len(t_params)
    data_off = (data_start + 3) & ~3
    pad2 = data_off - data_start
    params = struct.pack(
        "<HHHHHHHHHBB",
        len(t_params),  # TotalParameterCount
        len(t_data),    # TotalDataCount
        0,              # Reserved
        len(t_params),  # ParameterCount
        param_off,      # ParameterOffset (from SMB header start)
        0,              # ParameterDisplacement
        len(t_data),    # DataCount
        data_off,       # DataOffset
        0,              # DataDisplacement
        0,              # SetupCount
        0,              # Reserved2
    )
    body_data = (b"\x00" * pad1) + t_params + (b"\x00" * pad2) + t_data
    return params, body_data, STATUS_SUCCESS


def _dir_entry_104(name, local_path):
    """One SMB_FIND_FILE_BOTH_DIRECTORY_INFO (level 0x104) record (NextEntryOffset=0)."""
    try:
        st = os.stat(local_path)
        is_dir = os.path.isdir(local_path)
        size = 0 if is_dir else st.st_size
        ft = to_filetime(st.st_mtime)
    except OSError:
        is_dir = name in (".", "..")
        size = 0
        ft = 0
    nb = name.encode("ascii", "ignore")
    rec = struct.pack(
        "<IIqqqqQQIIIBB24s",
        0,                                          # NextEntryOffset (only/last)
        0,                                          # FileIndex
        ft, ft, ft, ft,                             # times
        size,                                       # EndOfFile
        size,                                       # AllocationSize
        ATTR_DIRECTORY if is_dir else ATTR_NORMAL,  # ExtFileAttributes
        len(nb),                                    # FileNameLength (byte-exact)
        0,                                          # EaSize
        0,                                          # ShortNameLength
        0,                                          # Reserved
        b"\x00" * 24,                               # ShortName
    )
    return rec + nb


def h_trans2_find_first2(conn, r, t_params, t_data):
    # request param: SearchAttributes(2) SearchCount(2) Flags(2) LevelOfInterest(2)
    #                StorageType(4) SearchPattern(asciiz)
    pat = t_params[12:]
    end = pat.index(0) if 0 in pat else len(pat)
    pattern = pat[:end].decode("ascii", "ignore")
    sh = _share_for_tid(conn, r)
    if sh is None:
        return None, None, STATUS_OBJECT_NAME_NOT_FOUND
    # strip trailing "\*" wildcard to get the directory
    dirpath = pattern.replace("/", "\\")
    if dirpath.endswith("\\*"):
        dirpath = dirpath[:-2]
    elif dirpath.endswith("*"):
        dirpath = dirpath[:-1]
    local = sh.resolve(dirpath or "\\")
    if local is None or not os.path.isdir(local):
        log("findfirst MISS", repr(pattern))
        return None, None, STATUS_OBJECT_NAME_NOT_FOUND
    try:
        names = [".", ".."] + sorted(os.listdir(local))
    except OSError:
        return None, None, STATUS_OBJECT_NAME_NOT_FOUND
    entries = [(n, os.path.join(local, n) if n not in (".", "..") else local) for n in names]
    sid = conn.alloc_sid()
    name, lp = entries[0]
    eos = 1 if len(entries) == 1 else 0
    conn.searches[sid] = (entries, 1)
    log("findfirst", repr(dirpath), "sid", sid, "entries", len(entries))
    rp = struct.pack("<HHHHH", sid, 1, eos, 0, 0)  # SID,SearchCount,EndOfSearch,EAErrOff,LastNameOff
    rd = _dir_entry_104(name, lp)
    return _build_trans2_reply(conn, r, STATUS_SUCCESS, rp, rd)


def h_trans2_find_next2(conn, r, t_params, t_data):
    sid = struct.unpack_from("<H", t_params, 0)[0]
    state = conn.searches.get(sid)
    if state is None:
        rp = struct.pack("<HHHH", 0, 1, 0, 0)  # SearchCount=0, EndOfSearch=1
        return _build_trans2_reply(conn, r, STATUS_SUCCESS, rp, b"")
    entries, cursor = state
    if cursor >= len(entries):
        conn.searches.pop(sid, None)
        rp = struct.pack("<HHHH", 0, 1, 0, 0)
        return _build_trans2_reply(conn, r, STATUS_SUCCESS, rp, b"")
    name, lp = entries[cursor]
    eos = 1 if cursor == len(entries) - 1 else 0
    conn.searches[sid] = (entries, cursor + 1)
    rp = struct.pack("<HHHH", 1, eos, 0, 0)  # NEXT2: no leading SID (8 bytes)
    rd = _dir_entry_104(name, lp)
    return _build_trans2_reply(conn, r, STATUS_SUCCESS, rp, rd)


def h_trans2_query_path(conn, r, t_params, t_data):
    level = struct.unpack_from("<H", t_params, 0)[0]
    nm = t_params[6:]
    end = nm.index(0) if 0 in nm else len(nm)
    name = nm[:end].decode("ascii", "ignore")
    sh = _share_for_tid(conn, r)
    if sh is None:
        return None, None, STATUS_OBJECT_NAME_NOT_FOUND
    local = sh.resolve(name or "\\")
    if local is None or not os.path.exists(local):
        return None, None, STATUS_OBJECT_NAME_NOT_FOUND
    is_dir = os.path.isdir(local)
    try:
        st = os.stat(local)
        size = 0 if is_dir else st.st_size
        ft = to_filetime(st.st_mtime)
    except OSError:
        size, ft = 0, 0
    if level == SMB_QUERY_FILE_BASIC_INFO:
        rd = struct.pack("<qqqqII", ft, ft, ft, ft, ATTR_DIRECTORY if is_dir else ATTR_NORMAL, 0)
    elif level == SMB_QUERY_FILE_STANDARD_INFO:
        rd = struct.pack("<QQIBBH", size, size, 0, 0, 1 if is_dir else 0, 0)
    else:
        rd = struct.pack("<qqqqII", ft, ft, ft, ft, ATTR_DIRECTORY if is_dir else ATTR_NORMAL, 0)
    return _build_trans2_reply(conn, r, STATUS_SUCCESS, b"", rd)


def h_trans2(conn, r):
    setup, t_params, t_data = _trans2_parse(r)
    if setup == TRANS2_FIND_FIRST2:
        return h_trans2_find_first2(conn, r, t_params, t_data)
    if setup == TRANS2_FIND_NEXT2:
        return h_trans2_find_next2(conn, r, t_params, t_data)
    if setup == TRANS2_QUERY_PATH_INFORMATION:
        return h_trans2_query_path(conn, r, t_params, t_data)
    log("trans2 unhandled subcmd 0x%02x" % setup)
    return None, None, STATUS_NOT_IMPLEMENTED


# --------------------------------------------------------------------------------------------
# SMB_COM_TRANSACTION (0x25): RAP NetShareEnum -- only used when OPL's Share field is empty
# (the share-picker). Returns the configured disk shares as type 0 (STYPE_DISKTREE).
# --------------------------------------------------------------------------------------------
def h_transaction(conn, r):
    shares = list(conn.server.shares.values())
    n = len(shares)
    # RAP NetShareEnum level 1 record: 13-byte name + pad(1) + type(2) + comment-ptr(4) = 20 bytes
    rec_size = 20
    comment_pool_off = rec_size * n
    data = b""
    comments = b""
    for i, sh in enumerate(shares):
        name13 = sh.name.encode("ascii", "ignore")[:13].ljust(13, b"\x00")
        comment_off = comment_pool_off + len(comments)
        data += name13 + b"\x00" + struct.pack("<HI", 0, comment_off)  # type 0 = disk
        comments += b"\x00"
    data += comments
    # RAP response param block: Status(2), Converter(2), EntryCount(2), AvailableEntries(2)
    rparams = struct.pack("<HHHH", 0, 0, n, n)
    # Build the SMB_COM_TRANSACTION response envelope (same shape as trans2, 10 fixed words).
    base = 32 + 1 + 20 + 2
    param_off = (base + 3) & ~3
    pad1 = param_off - base
    data_start = param_off + len(rparams)
    data_off = (data_start + 3) & ~3
    pad2 = data_off - data_start
    params = struct.pack(
        "<HHHHHHHHHBB",
        len(rparams), len(data), 0,
        len(rparams), param_off, 0,
        len(data), data_off, 0,
        0, 0,
    )
    body = (b"\x00" * pad1) + rparams + (b"\x00" * pad2) + data
    return params, body, STATUS_SUCCESS


# --------------------------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------------------------
HANDLERS = {
    SMB_COM_CREATE_DIRECTORY: h_create_directory,
    SMB_COM_NEGOTIATE: h_negotiate,
    SMB_COM_SESSION_SETUP_ANDX: h_session_setup,
    SMB_COM_TREE_CONNECT_ANDX: h_tree_connect,
    SMB_COM_TREE_DISCONNECT: h_tree_disconnect,
    SMB_COM_LOGOFF_ANDX: h_logoff,
    SMB_COM_OPEN_ANDX: h_open_andx,
    SMB_COM_NT_CREATE_ANDX: h_nt_create_andx,
    SMB_COM_READ_ANDX: h_read_andx,
    SMB_COM_WRITE_ANDX: h_write_andx,
    SMB_COM_CLOSE: h_close,
    SMB_COM_ECHO: h_echo,
    SMB_COM_QUERY_INFORMATION_DISK: h_query_disk,
    SMB_COM_CHECK_DIRECTORY: h_check_directory,
    SMB_COM_TRANS2: h_trans2,
    SMB_COM_TRANSACTION: h_transaction,
}


def serve_conn(server, sock, addr):
    conn = Conn(server)
    log("connect from", addr)
    try:
        while True:
            msg = recv_msg(sock)
            if msg is None:
                break
            if msg == b"":
                continue  # keep-alive
            if len(msg) >= 64 and msg[0:4] == SMB2_MAGIC:
                r2 = Smb2Req(msg)
                handler = SMB2_HANDLERS.get(r2.cmd)
                if handler is None:
                    hdr = pack_smb2_hdr(r2.cmd, status=STATUS_NOT_IMPLEMENTED, message_id=r2.message_id, session_id=r2.session_id)
                    send_msg(sock, hdr)
                    continue
                if r2.cmd == 0x0000:
                    body, status = handler(conn, r2, smb_version=server.smb_version)
                else:
                    body, status = handler(conn, r2)
                hdr = pack_smb2_hdr(r2.cmd, status=status, message_id=r2.message_id, session_id=conn.uid or r2.session_id)
                send_msg(sock, hdr + body)
                continue
            if len(msg) < 33 or msg[0:4] != SMB_MAGIC:
                continue
            r = Req(msg)
            handler = HANDLERS.get(r.cmd)
            if handler is None:
                log("unhandled cmd 0x%02x" % r.cmd)
                send_msg(sock, pack_header(r.cmd, STATUS_NOT_IMPLEMENTED, r.tid, r.pid, r.uid, r.mid)
                         + build_body(b"", b""))
                continue
            result = handler(conn, r)
            if result is None:
                continue
            # result: (params, data, status[, uid_override[, tid_override]])
            params, data, status = result[0], result[1], result[2]
            uid = result[3] if len(result) >= 4 and result[3] is not None else r.uid
            tid = result[4] if len(result) >= 5 and result[4] is not None else r.tid
            if status != STATUS_SUCCESS or params is None:
                send_msg(sock, pack_header(r.cmd, status, r.tid, r.pid, r.uid, r.mid) + build_body(b"", b""))
                continue
            hdr = pack_header(r.cmd, status, tid, r.pid, uid, r.mid)
            send_msg(sock, hdr + build_body(params, data))
    except (ConnectionError, OSError) as e:
        log("conn error", e)
    finally:
        conn.cleanup()
        try:
            sock.close()
        except OSError:
            pass
        log("disconnect", addr)


# --------------------------------------------------------------------------------------------
# Server + Windows port-445 (LanmanServer) takeover
# --------------------------------------------------------------------------------------------
class SmbServer:
    def __init__(self, shares, read_only, smb_version=1, users=None):
        self.shares = shares
        self.read_only = read_only
        self.smb_version = int(smb_version)
        # Empty means the guest behaviour this server has always had: every
        # SESSION_SETUP accepted, no password read. That path is what consoles
        # are tested against, so configuring credentials must be the thing that
        # changes it -- never the default.
        self.users = {u.casefold(): p for u, p in (users or {}).items()}


def _port_free(bind_addr, port):
    """True if a TCP bind on (bind_addr, port) succeeds right now -- the direct
    test for whether Windows has actually released a port, rather than trusting
    service states."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((bind_addr, port))
        return True
    except OSError:
        return False
    finally:
        probe.close()


def _take_445(bind_addr="0.0.0.0"):
    """Stop Windows' LanmanServer so we can bind 445. Returns a restore()
    callable, or None if the service would not stop or the port stays bound.
    Reversible: we only Stop (never Disable), so a reboot self-heals even on a
    hard kill."""
    import subprocess

    # Resolve PowerShell by absolute path so a stray powershell.* on PATH or in
    # the current directory cannot be run instead (binary hijacking). Fall back
    # to the bare name only if the system executable is somehow missing.
    system_root = os.environ.get("SystemRoot") or os.environ.get("windir") or r"C:\Windows"
    powershell = os.path.join(system_root, "System32", "WindowsPowerShell",
                              "v1.0", "powershell.exe")
    if not os.path.isfile(powershell):
        powershell = "powershell"

    def ps(cmd):
        return subprocess.run([powershell, "-NoProfile", "-Command", cmd],
                              capture_output=True, text=True)

    def status(name):
        return ps("(Get-Service %s -ErrorAction SilentlyContinue).Status" % name).stdout.strip()

    def wait_stopped(name):
        # Trust the service's actual state, not the command's exit code: a
        # denied stop (run without admin) and a slow stop both show up here.
        deadline = time.time() + 10
        while status(name) != "Stopped":
            if time.time() >= deadline:
                return False
            time.sleep(0.25)
        return True

    was = status("LanmanServer")
    browser = status("Browser")
    print("  [445] stopping LanmanServer (was: %s) ..." % (was or "?"))
    res = ps("Stop-Service -Name LanmanServer -Force -ErrorAction SilentlyContinue")
    if not wait_stopped("LanmanServer"):
        # Binding 445 while LanmanServer lives is hopeless, and nothing was
        # stopped, so there is nothing to restore.
        print("  [445] could not stop LanmanServer -- run as Administrator."
              + (" stderr: " + res.stderr.strip() if res.stderr.strip() else ""))
        return None

    def restore():
        print("  [445] restoring LanmanServer ...")
        ps("Start-Service LanmanServer -ErrorAction SilentlyContinue")
        if browser == "Running":
            ps("Start-Service Browser -ErrorAction SilentlyContinue")

    # Stopping LanmanServer frees 445 only when nothing else keeps the kernel
    # driver srvnet.sys -- the socket's real owner -- loaded. Docker Desktop's
    # service depends on it, and on such a machine the port stays bound. We do
    # not pause other people's software to win the port: name what holds it,
    # put LanmanServer back, and bail.
    if not _port_free(bind_addr, 445):
        deps = ps("(Get-Service srvnet -ErrorAction SilentlyContinue)"
                  ".DependentServices.Name").stdout.split()
        pinned = [n for n in deps if status(n) == "Running"]
        print("  [445] port 445 is still bound by the srvnet driver"
              + (" -- pinned by %s." % ", ".join(pinned) if pinned else ".")
              + " Not pausing other software: use --port N (e.g. 1111) instead "
              + "of --take-445.")
        restore()
        return None

    return restore


def _lan_ips():
    """All candidate IPv4 addresses, best-guess first. The address the PS2 must use is the one on
    the SAME subnet as the PS2 -- a VPN / WSL / Hyper-V adapter can outrank the real LAN on the
    default route, so we list them all instead of guessing a single (possibly wrong) one."""
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if ip not in ips and not ip.startswith("127."):
                ips.append(ip)
    except OSError:
        pass
    return ips or ["127.0.0.1"]


def _bind_listener(lsock, bind_addr, port, take_445, timeout=10.0, interval=0.25):
    """Bind lsock; return the bound port or None. Normal mode walks forward from
    the requested port and reports the real one; --take-445 must have exactly 445,
    so it retries the same port for a few seconds -- Windows' srvnet driver does
    not release 445 the moment LanmanServer reports Stopped."""
    if take_445:
        deadline = time.time() + timeout
        while True:
            try:
                lsock.bind((bind_addr, port))
                return port
            except OSError:
                if time.time() >= deadline:
                    return None
                time.sleep(interval)
    for cand in range(port, port + 12):
        try:
            lsock.bind((bind_addr, cand))
            return cand
        except OSError:
            continue
    return None


def main(argv=None):
    global VERBOSE
    ap = argparse.ArgumentParser(
        description="Minimal SMBv1 server for Open-PS2-Loader (guest auth, custom port).")
    ap.add_argument("--share", action="append", default=[], metavar="NAME=PATH",
                    help="a share, e.g. --share games=D:/PS2Games (repeatable)")
    ap.add_argument("--port", type=int, default=1111,
                    help="TCP port (default 1111; set OPL's SMB Port to match). "
                         "Avoid ports below 1033 -- Windows can reserve/block them.")
    ap.add_argument("--bind", default="0.0.0.0", help="bind address (default all interfaces)")
    ap.add_argument("--read-only", action="store_true",
                    help="serve the share read-only (no saves / no VMC writes); default is writable")
    ap.add_argument("--take-445", action="store_true",
                    help="bind the standard port 445 by pausing Windows LanmanServer (admin; reversible)")
    ap.add_argument("--user", action="append", metavar="NAME:PASSWORD",
                    help="require this account instead of accepting the "
                         "password-less guest logon; repeatable. SMBv1 sends "
                         "passwords in the clear -- see --help notes.")
    ap.add_argument("--smb-version", type=int, choices=[1, 2, 3], default=1,
                    help="SMB protocol version (1=SMBv1; 2 and 3 are INCOMPLETE "
                         "-- they negotiate and connect but serve no files)")
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
        shares[name] = Share(name, path)
    if not shares:
        ap.error("at least one --share NAME=PATH is required")

    # The SMB2/SMB3 handlers negotiate, authenticate and accept a tree connect,
    # then serve nothing: READ returns zero bytes, CREATE ignores the path it was
    # given, WRITE reports success without writing, and QUERY_DIRECTORY is absent
    # so the share cannot be listed. A client sees an empty folder and no error.
    # The flag stays because it is how the real implementation gets developed and
    # tested, but it must never look finished to whoever runs it.
    if args.smb_version != 1:
        print("WARNING: --smb-version %d is INCOMPLETE. The server will negotiate "
              "SMB%d and accept a connection, but it serves no files: reads return "
              "nothing and the share cannot be listed. Use --smb-version 1 to serve "
              "a console." % (args.smb_version, args.smb_version), file=sys.stderr)

    users = {}
    for spec in args.user or []:
        if ":" not in spec:
            ap.error("--user must be NAME:PASSWORD, got %r" % spec)
        name, password = spec.split(":", 1)
        if not name:
            ap.error("--user needs a name: %r" % spec)
        users[name] = password

    server = SmbServer(shares, read_only=args.read_only,
                       smb_version=args.smb_version, users=users)

    restore = None
    port = args.port
    if args.take_445:
        port = 445
        restore = _take_445(args.bind)
        if restore is None:
            return 2
    elif port < 1 or port > 65535:
        print("ERROR: port %d is out of the valid range (1-65535)." % port, file=sys.stderr)
        return 2
    elif port < 1033:
        # Low ports overlap well-known/privileged ports and Windows reserved /
        # excluded port ranges, which frequently block the bind. (--take-445
        # deliberately uses 445 and is handled above.)
        print(" WARNING: port %d is below 1033. Windows can reserve or block low "
              "ports; if the bind fails or OPL cannot connect, use a higher port "
              "such as the default 1111." % port)

    # Bind; if the chosen port is taken/reserved, walk forward and report the real one.
    lsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    lsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    bound = _bind_listener(lsock, args.bind, port, args.take_445)
    if bound is None:
        if args.take_445:
            print("ERROR: could not bind port 445 -- another process took it in "
                  "the meantime. Use --port N instead of --take-445.", file=sys.stderr)
        else:
            print("ERROR: could not bind a port near %d (try --port N)." % port, file=sys.stderr)
        if restore:
            restore()
        return 2
    lsock.listen(8)

    ips = _lan_ips()
    print("=" * 64)
    print(" PS2 Servers SMBv1 server -- listening on %s:%d" % (args.bind, bound))
    if users:
        first = sorted(users)[0]
        creds = "User: %s   Password: %s" % (
            first, "(as configured)" if users[first] else "blank")
    else:
        creds = "User: guest   Password: blank"
    print(" In OPL set:  SMB Port: %d   |   %s" % (bound, creds))
    print("              Share: %s" % "  ".join(sorted(shares)))
    if len(ips) == 1:
        print("              PC IP Address: %s" % ips[0])
    else:
        print("              PC IP Address: use the one on the SAME network as your PS2")
        print("              (usually 192.168.x.x -- NOT a VPN/WSL address):")
        for ip in ips:
            print("                  %s" % ip)
    for name in shares:
        print("              Share: %s   ->   %s%s" % (name, shares[name].root,
              "   (read-only)" if server.read_only else "   (writable)"))
    if server.read_only:
        print(" (read-only -- OPL cannot save per-game settings or VMCs to this share)")
    else:
        print(" (writable -- OPL can save settings + VMC-on-SMB here; pass --read-only to lock it)")
    print(" If OPL says connect failed (error 300): use the LAN IP above (not a VPN/WSL one),")
    print(" and allow TCP %d through Windows Firewall (the launcher .bat does this for you)." % bound)
    print("=" * 64)

    try:
        while True:
            csock, addr = lsock.accept()
            csock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            t = threading.Thread(target=serve_conn, args=(server, csock, addr), daemon=True)
            t.start()
    except KeyboardInterrupt:
        print("\nshutting down ...")
    finally:
        try:
            lsock.close()
        except OSError:
            pass
        if restore:
            restore()
    return 0


if __name__ == "__main__":
    sys.exit(main())
