"""SMB2/SMB3 wire format: framing, the header, and the structures either side.

Kept apart from the server loop because these are the parts a client will not
forgive. A handler that answers the wrong question is a bug you can see in a
log; a structure whose StructureSize is off by one makes a real client hang up
with no message at all, and the only way to find it is to have the layouts
written down somewhere you can read them.

Everything here is little-endian except the 4-byte session framing, which is the
one big-endian field in the whole protocol -- the same trap SMB1 has, and the
reason send_msg/recv_msg are lifted from smbv1_server/smbserver_opl.py rather
than rewritten. That file is what consoles have actually been tested against.
"""

import os
import struct

# --- session framing (direct TCP, RFC 1002 style) ------------------------- #
# byte 0 = message type (0x00 session message), bytes 1-3 = 24-bit BIG-endian
# length. Identical to SMB1, so this is the tested implementation, moved.


def send_msg(sock, msg):
    sock.sendall(b"\x00" + len(msg).to_bytes(3, "big") + msg)


def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def recv_msg(sock):
    """The next SMB message, b"" for a keep-alive, or None at EOF.

    The three are deliberately different values: a keep-alive that read as EOF
    would drop a connection that was healthy, and an EOF that read as a
    keep-alive would spin.
    """
    hdr = _recv_exact(sock, 4)
    if hdr is None:
        return None
    length = int.from_bytes(hdr[1:4], "big")
    if hdr[0] != 0x00:
        if length:
            _recv_exact(sock, length)
        return b""
    if length == 0:
        return b""
    return _recv_exact(sock, length)


# --- constants ------------------------------------------------------------ #

SMB2_MAGIC = b"\xfeSMB"
HEADER_SIZE = 64

# Dialects. 2.0.2 is included because it is what a minimal or old client offers;
# 3.1.1 is deliberately NOT offered -- see pick_dialect.
DIALECT_202 = 0x0202
DIALECT_210 = 0x0210
DIALECT_300 = 0x0300
DIALECT_302 = 0x0302
DIALECT_311 = 0x0311

# What each launcher mode asks for. SMB2 and SMB3 are one server with a ceiling,
# not two servers: SMB3 is the same protocol with a higher dialect and crypto.
CEILING_SMB2 = DIALECT_210
CEILING_SMB3 = DIALECT_302

CMD_NEGOTIATE = 0x0000
CMD_SESSION_SETUP = 0x0001
CMD_LOGOFF = 0x0002
CMD_TREE_CONNECT = 0x0003
CMD_TREE_DISCONNECT = 0x0004
CMD_CREATE = 0x0005
CMD_CLOSE = 0x0006
CMD_FLUSH = 0x0007
CMD_READ = 0x0008
CMD_WRITE = 0x0009
CMD_LOCK = 0x000A
CMD_IOCTL = 0x000B
CMD_CANCEL = 0x000C
CMD_ECHO = 0x000D
CMD_QUERY_DIRECTORY = 0x000E
CMD_CHANGE_NOTIFY = 0x000F
CMD_QUERY_INFO = 0x0010
CMD_SET_INFO = 0x0011
CMD_OPLOCK_BREAK = 0x0012

COMMAND_NAMES = {
    CMD_NEGOTIATE: "NEGOTIATE", CMD_SESSION_SETUP: "SESSION_SETUP",
    CMD_LOGOFF: "LOGOFF", CMD_TREE_CONNECT: "TREE_CONNECT",
    CMD_TREE_DISCONNECT: "TREE_DISCONNECT", CMD_CREATE: "CREATE",
    CMD_CLOSE: "CLOSE", CMD_FLUSH: "FLUSH", CMD_READ: "READ",
    CMD_WRITE: "WRITE", CMD_LOCK: "LOCK", CMD_IOCTL: "IOCTL",
    CMD_CANCEL: "CANCEL", CMD_ECHO: "ECHO",
    CMD_QUERY_DIRECTORY: "QUERY_DIRECTORY", CMD_CHANGE_NOTIFY: "CHANGE_NOTIFY",
    CMD_QUERY_INFO: "QUERY_INFO", CMD_SET_INFO: "SET_INFO",
    CMD_OPLOCK_BREAK: "OPLOCK_BREAK",
}

FLAG_SERVER_TO_REDIR = 0x00000001
FLAG_ASYNC = 0x00000002
FLAG_RELATED = 0x00000004
FLAG_SIGNED = 0x00000008

STATUS_SUCCESS = 0x00000000
STATUS_PENDING = 0x00000103
STATUS_NO_MORE_FILES = 0x80000006
STATUS_INVALID_PARAMETER = 0xC000000D
STATUS_NO_SUCH_FILE = 0xC000000F
STATUS_END_OF_FILE = 0xC0000011
STATUS_MORE_PROCESSING_REQUIRED = 0xC0000016
STATUS_ACCESS_DENIED = 0xC0000022
STATUS_OBJECT_NAME_INVALID = 0xC0000033
STATUS_OBJECT_NAME_NOT_FOUND = 0xC0000034
STATUS_OBJECT_NAME_COLLISION = 0xC0000035
STATUS_OBJECT_PATH_NOT_FOUND = 0xC000003A
STATUS_OBJECT_PATH_SYNTAX_BAD = 0xC000003B
STATUS_LOGON_FAILURE = 0xC000006D
STATUS_INSUFF_SERVER_RESOURCES = 0xC0000205
STATUS_NOT_SUPPORTED = 0xC00000BB
STATUS_INVALID_DEVICE_REQUEST = 0xC0000010
STATUS_FILE_IS_A_DIRECTORY = 0xC00000BA
STATUS_NOT_A_DIRECTORY = 0xC0000103
STATUS_BAD_NETWORK_NAME = 0xC00000CC
STATUS_USER_SESSION_DELETED = 0xC0000203
STATUS_NETWORK_NAME_DELETED = 0xC00000C9
STATUS_FILE_CLOSED = 0xC0000128
STATUS_INFO_LENGTH_MISMATCH = 0xC0000004
STATUS_MEDIA_WRITE_PROTECTED = 0xC00000A2
STATUS_DISK_FULL = 0xC000007F
STATUS_CANNOT_DELETE = 0xC0000121
STATUS_DIRECTORY_NOT_EMPTY = 0xC0000101

# Names for the statuses smb2_paths raises, so a path decision made in one place
# arrives on the wire as the status it chose rather than a generic denial.
STATUS_BY_NAME = {
    "STATUS_ACCESS_DENIED": STATUS_ACCESS_DENIED,
    "STATUS_OBJECT_NAME_INVALID": STATUS_OBJECT_NAME_INVALID,
    "STATUS_OBJECT_NAME_NOT_FOUND": STATUS_OBJECT_NAME_NOT_FOUND,
    "STATUS_OBJECT_PATH_SYNTAX_BAD": STATUS_OBJECT_PATH_SYNTAX_BAD,
    "STATUS_OBJECT_PATH_NOT_FOUND": STATUS_OBJECT_PATH_NOT_FOUND,
    "STATUS_LOGON_FAILURE": STATUS_LOGON_FAILURE,
}

# File attributes
ATTR_READONLY = 0x00000001
ATTR_HIDDEN = 0x00000002
ATTR_DIRECTORY = 0x00000010
ATTR_ARCHIVE = 0x00000020
ATTR_NORMAL = 0x00000080

# CreateDisposition
FILE_SUPERSEDE = 0x00000000
FILE_OPEN = 0x00000001
FILE_CREATE = 0x00000002
FILE_OPEN_IF = 0x00000003
FILE_OVERWRITE = 0x00000004
FILE_OVERWRITE_IF = 0x00000005

# CreateAction (what the server actually did)
FILE_SUPERSEDED = 0x00000000
FILE_OPENED = 0x00000001
FILE_CREATED = 0x00000002
FILE_OVERWRITTEN = 0x00000003

# CreateOptions worth honouring
FILE_DIRECTORY_FILE = 0x00000001
FILE_NON_DIRECTORY_FILE = 0x00000040
FILE_DELETE_ON_CLOSE = 0x00001000

SHARE_TYPE_DISK = 0x01

SECURITY_MODE_SIGNING_ENABLED = 0x0001
SECURITY_MODE_SIGNING_REQUIRED = 0x0002

GLOBAL_CAP_DFS = 0x00000001
GLOBAL_CAP_LARGE_MTU = 0x00000004

SESSION_FLAG_IS_GUEST = 0x0001
SESSION_FLAG_IS_NULL = 0x0002

MAX_TRANSACT = 1 << 20
MAX_READ = 1 << 20
MAX_WRITE = 1 << 20


def pick_dialect(offered, ceiling):
    """The best dialect both sides can speak, or None.

    3.1.1 is never chosen even when a client offers it. It requires negotiate
    contexts -- a pre-authentication integrity hash the client verifies -- and a
    server that claims 3.1.1 without them is refused outright by Windows. The
    honest move is to negotiate 3.0.2 and work, rather than advertise a dialect
    and fail the connection. Every SMB3 client speaks 3.0.2.
    """
    usable = [d for d in offered if d <= ceiling and d in (
        DIALECT_202, DIALECT_210, DIALECT_300, DIALECT_302)]
    return max(usable) if usable else None


def to_filetime(unix_ts):
    """Windows FILETIME: 100ns ticks since 1601-01-01 UTC."""
    if unix_ts <= 0:
        return 0
    return int(unix_ts * 10_000_000) + 116444736000000000


def utf16(text):
    return (text or "").encode("utf-16-le")


def from_utf16(raw):
    return raw.decode("utf-16-le", "replace")


class Header:
    """A parsed SMB2 header. 64 bytes, fixed, every message."""

    __slots__ = ("credit_charge", "status", "command", "credits", "flags",
                 "next_command", "message_id", "tree_id", "session_id", "signature")

    def __init__(self, raw):
        (_magic, _size, self.credit_charge, self.status, self.command,
         self.credits, self.flags, self.next_command, self.message_id,
         _reserved, self.tree_id, self.session_id, self.signature) = struct.unpack(
            "<4sHHIHHIIQIIQ16s", raw[:HEADER_SIZE])

    def __repr__(self):
        return "<SMB2 %s mid=%d tid=%d sid=%#x>" % (
            COMMAND_NAMES.get(self.command, "0x%04x" % self.command),
            self.message_id, self.tree_id, self.session_id)


def parse_header(raw):
    if len(raw) < HEADER_SIZE or raw[:4] != SMB2_MAGIC:
        return None
    return Header(raw)


def pack_header(command, status, message_id, tree_id=0, session_id=0,
                credits=1, flags=0, next_command=0):
    return struct.pack(
        "<4sHHIHHIIQIIQ16s",
        SMB2_MAGIC,
        HEADER_SIZE,
        0,                                  # CreditCharge
        status,
        command,
        credits,
        flags | FLAG_SERVER_TO_REDIR,
        next_command,
        message_id,
        0,                                  # Reserved (SMB1 PID high)
        tree_id,
        session_id,
        b"\x00" * 16,                       # Signature
    )


def error_body(status=0):
    """The body every failure carries.

    StructureSize is 9 with a single byte of ErrorData, and that byte is not
    optional padding a client tolerates -- a body of 8 bytes is a malformed
    response, which is a different failure from the one being reported.
    """
    return struct.pack("<HBBI", 9, 0, 0, 0) + b"\x00"


# --- file information encoders -------------------------------------------- #
# The layouts a client reads to size, date and list what it is looking at. All
# of them are fixed-size records followed by a variable name, and all of them
# are silently wrong if a field is the wrong width -- so they live together.


def _stat_times(st):
    return (to_filetime(getattr(st, "st_ctime", 0)),
            to_filetime(getattr(st, "st_atime", 0)),
            to_filetime(getattr(st, "st_mtime", 0)),
            to_filetime(getattr(st, "st_mtime", 0)))


def attributes_for(st, read_only=False):
    import stat as statmod
    if statmod.S_ISDIR(st.st_mode):
        attrs = ATTR_DIRECTORY
    else:
        attrs = ATTR_ARCHIVE
    if read_only:
        attrs |= ATTR_READONLY
    return attrs


def allocation_of(st):
    """Rounded to a 4 KiB cluster, the way a real filesystem reports it."""
    size = st.st_size
    return (size + 4095) & ~4095


def both_directory_entry(name, st, read_only=False, with_file_id=False):
    """FileBothDirectoryInformation / FileIdBothDirectoryInformation.

    NextEntryOffset is filled in by the caller once it knows whether another
    entry follows, because only the caller knows that. It must be 8-byte
    aligned: an unaligned chain is the classic way a listing shows the first
    file and then stops.
    """
    ctime, atime, mtime, chtime = _stat_times(st)
    name_utf16 = utf16(name)
    fixed = struct.pack(
        "<IIQQQQqqIIIBB24s",
        0,                              # NextEntryOffset, patched by the caller
        0,                              # FileIndex
        ctime, atime, mtime, chtime,
        st.st_size,                     # EndOfFile
        allocation_of(st),
        attributes_for(st, read_only),
        len(name_utf16),
        0,                              # EaSize
        0,                              # ShortNameLength
        0,                              # Reserved
        b"\x00" * 24,                   # ShortName
    )
    if with_file_id:
        fixed += struct.pack("<HQ", 0, st.st_ino & 0xFFFFFFFFFFFFFFFF)
    return fixed + name_utf16


def directory_entry(name, st, info_class, read_only=False):
    """One listing entry in the class the client asked for.

    Returns None for a class this server does not encode, so the caller answers
    STATUS_INVALID_PARAMETER rather than sending a record the client will
    misread as whatever it did ask for.
    """
    ctime, atime, mtime, chtime = _stat_times(st)
    name_utf16 = utf16(name)
    attrs = attributes_for(st, read_only)
    if info_class == FILE_BOTH_DIRECTORY_INFORMATION:
        return both_directory_entry(name, st, read_only)
    if info_class == FILE_ID_BOTH_DIRECTORY_INFORMATION:
        return both_directory_entry(name, st, read_only, with_file_id=True)
    if info_class == FILE_DIRECTORY_INFORMATION:
        return struct.pack("<IIQQQQqqII", 0, 0, ctime, atime, mtime, chtime,
                           st.st_size, allocation_of(st), attrs,
                           len(name_utf16)) + name_utf16
    if info_class == FILE_FULL_DIRECTORY_INFORMATION:
        return struct.pack("<IIQQQQqqIII", 0, 0, ctime, atime, mtime, chtime,
                           st.st_size, allocation_of(st), attrs,
                           len(name_utf16), 0) + name_utf16
    if info_class == FILE_NAMES_INFORMATION:
        return struct.pack("<III", 0, 0, len(name_utf16)) + name_utf16
    return None


FILE_DIRECTORY_INFORMATION = 0x01
FILE_FULL_DIRECTORY_INFORMATION = 0x02
FILE_BOTH_DIRECTORY_INFORMATION = 0x03
FILE_BASIC_INFORMATION = 0x04
FILE_STANDARD_INFORMATION = 0x05
FILE_INTERNAL_INFORMATION = 0x06
FILE_EA_INFORMATION = 0x07
FILE_ACCESS_INFORMATION = 0x08
FILE_NAME_INFORMATION = 0x09
FILE_RENAME_INFORMATION = 0x0A
FILE_NAMES_INFORMATION = 0x0C
FILE_DISPOSITION_INFORMATION = 0x0D
FILE_POSITION_INFORMATION = 0x0E
FILE_MODE_INFORMATION = 0x10
FILE_ALIGNMENT_INFORMATION = 0x11
FILE_ALL_INFORMATION = 0x12
FILE_END_OF_FILE_INFORMATION = 0x14
FILE_NETWORK_OPEN_INFORMATION = 0x22
FILE_ID_BOTH_DIRECTORY_INFORMATION = 0x25

FS_VOLUME_INFORMATION = 0x01
FS_SIZE_INFORMATION = 0x03
FS_DEVICE_INFORMATION = 0x04
FS_ATTRIBUTE_INFORMATION = 0x05
FS_FULL_SIZE_INFORMATION = 0x07

INFO_TYPE_FILE = 0x01
INFO_TYPE_FILESYSTEM = 0x02
INFO_TYPE_SECURITY = 0x03


def file_info(info_class, st, name="", read_only=False):
    """A QUERY_INFO answer for the FILE type, or None if unencodable."""
    ctime, atime, mtime, chtime = _stat_times(st)
    attrs = attributes_for(st, read_only)
    import stat as statmod
    is_dir = statmod.S_ISDIR(st.st_mode)

    if info_class == FILE_BASIC_INFORMATION:
        return struct.pack("<QQQQII", ctime, atime, mtime, chtime, attrs, 0)
    if info_class == FILE_STANDARD_INFORMATION:
        return struct.pack("<qqIBBH", allocation_of(st), st.st_size,
                           1, 0, 1 if is_dir else 0, 0)
    if info_class == FILE_INTERNAL_INFORMATION:
        return struct.pack("<Q", st.st_ino & 0xFFFFFFFFFFFFFFFF)
    if info_class == FILE_EA_INFORMATION:
        return struct.pack("<I", 0)
    if info_class == FILE_ACCESS_INFORMATION:
        return struct.pack("<I", 0x001F01FF)
    if info_class == FILE_POSITION_INFORMATION:
        return struct.pack("<q", 0)
    if info_class == FILE_MODE_INFORMATION:
        return struct.pack("<I", 0)
    if info_class == FILE_ALIGNMENT_INFORMATION:
        return struct.pack("<I", 0)
    if info_class == FILE_NAME_INFORMATION:
        raw = utf16(name)
        return struct.pack("<I", len(raw)) + raw
    if info_class == FILE_NETWORK_OPEN_INFORMATION:
        return struct.pack("<QQQQqqII", ctime, atime, mtime, chtime,
                           allocation_of(st), st.st_size, attrs, 0)
    if info_class == FILE_ALL_INFORMATION:
        # Explorer asks for this one constantly: it is the other classes
        # concatenated in a fixed order, so it is built from them rather than
        # re-packed, or the two would drift.
        raw = utf16(name)
        return (file_info(FILE_BASIC_INFORMATION, st, read_only=read_only)
                + file_info(FILE_STANDARD_INFORMATION, st, read_only=read_only)
                + file_info(FILE_INTERNAL_INFORMATION, st)
                + file_info(FILE_EA_INFORMATION, st)
                + file_info(FILE_ACCESS_INFORMATION, st)
                + struct.pack("<q", 0)              # CurrentByteOffset
                + struct.pack("<I", 0)              # Mode
                + struct.pack("<I", 0)              # AlignmentRequirement
                + struct.pack("<I", len(raw)) + raw)
    return None


def fs_info(info_class, root, label="PS2SERVERS"):
    """A QUERY_INFO answer for the FILESYSTEM type, or None."""
    if info_class == FS_VOLUME_INFORMATION:
        raw = utf16(label)
        return struct.pack("<QIIBB", 0, 0x53324253, len(raw), 0, 0) + raw
    if info_class in (FS_SIZE_INFORMATION, FS_FULL_SIZE_INFORMATION):
        try:
            usage = os.statvfs(root)
            total = usage.f_blocks
            free = usage.f_bavail
            sectors_per_unit = max(1, usage.f_frsize // 512)
        except (AttributeError, OSError):
            # Windows has no statvfs. shutil.disk_usage is portable and is what
            # the number is for -- Explorer shows it in the status bar.
            import shutil
            try:
                du = shutil.disk_usage(root)
            except OSError:
                du = None
            if du is None:
                total, free, sectors_per_unit = 0, 0, 8
            else:
                sectors_per_unit = 8            # 4 KiB units of 512-byte sectors
                total = du.total // 4096
                free = du.free // 4096
        if info_class == FS_SIZE_INFORMATION:
            return struct.pack("<qqII", total, free, sectors_per_unit, 512)
        return struct.pack("<qqqII", total, free, free, sectors_per_unit, 512)
    if info_class == FS_DEVICE_INFORMATION:
        return struct.pack("<II", 0x00000007, 0x00000020)   # DISK, remote
    if info_class == FS_ATTRIBUTE_INFORMATION:
        name = utf16("NTFS")
        # 0x02 CASE_PRESERVED_NAMES | 0x04 UNICODE_ON_DISK. Deliberately NOT
        # 0x01 CASE_SENSITIVE_SEARCH: Windows shares are not case sensitive, and
        # claiming otherwise makes a client treat Game.iso and game.iso as two
        # files on a filesystem where they are one.
        return struct.pack("<III", 0x00000006, 255, len(name)) + name
    return None
