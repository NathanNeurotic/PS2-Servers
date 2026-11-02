"""The \\PIPE\\srvsvc share list, served on IPC$.

A client that has no share name asks the server for its shares. Every SMB2
client does it the same way: connect the IPC$ tree, open the named pipe
"srvsvc", bind DCE/RPC to the server service interface, and call NetrShareEnum
(opnum 15). RiptOPL's share picker, Explorer's \\\\server view and
``smbclient -L`` all take this path, and without it a server that is otherwise
working shows an empty network location.

Only that one call is served. It is answered for SHARE_INFO levels 0, 1 and 2,
which are the ones clients ask for (libsmb2 asks for 1, Explorer for 1, Samba
for 1). Anything else -- another opnum, another interface, an NDR64-only bind
-- gets the DCE/RPC refusal a real server gives, not a guess.

Wire references: DCE 1.1 RPC (C706) chapter 12 for the connection-oriented
PDUs, MS-RPCE 2.2.2 for the Microsoft extensions, MS-SRVS 3.1.4.8 for
NetrShareEnum and 2.2.4 for the SHARE_INFO structures.
"""

import struct

# DCE/RPC connection-oriented packet types (C706 12.6.4).
PTYPE_REQUEST = 0
PTYPE_RESPONSE = 2
PTYPE_FAULT = 3
PTYPE_BIND = 11
PTYPE_BIND_ACK = 12
PTYPE_BIND_NAK = 13
PTYPE_ALTER_CONTEXT = 14
PTYPE_ALTER_CONTEXT_RESP = 15

PFC_FIRST_FRAG = 0x01
PFC_LAST_FRAG = 0x02

# Data representation: little-endian integers, ASCII characters, IEEE floats.
DREP_LE = b"\x10\x00\x00\x00"

# 4b324fc8-1670-01d3-1278-5a47bf6ee188 version 3.0
SRVSVC_SYNTAX = (bytes.fromhex("c84f324b7016d30112785a47bf6ee188")
                 + struct.pack("<HH", 3, 0))
# 8a885d04-1ceb-11c9-9fe8-08002b104860 version 2.0
NDR32_SYNTAX = (bytes.fromhex("045d888aeb1cc9119fe808002b104860")
                + struct.pack("<I", 2))

OPNUM_NETR_SHARE_ENUM = 15

# Bind results (C706 12.6.3.1 p_cont_def_result_t).
RESULT_ACCEPTANCE = 0
RESULT_PROVIDER_REJECTION = 2
REASON_ABSTRACT_SYNTAX_NOT_SUPPORTED = 1
REASON_TRANSFER_SYNTAXES_NOT_SUPPORTED = 2

# Fault codes (C706 appendix E / MS-RPCE 3.1.1.5.5).
NCA_OP_RNG_ERROR = 0x1C010002       # no such operation
NCA_PROTO_ERROR = 0x1C01000B        # malformed request
NCA_UNSPEC_REJECT = 0x1C000009

# SHARE_INFO shi*_type (MS-SRVS 2.2.2.4).
STYPE_DISKTREE = 0x00000000
STYPE_IPC = 0x00000003
STYPE_SPECIAL = 0x80000000

ERROR_INVALID_LEVEL = 124

# Fragments we send are never larger than this, whatever the client allows.
MAX_FRAG = 4280

PIPE_NAMES = ("srvsvc",)


class RpcError(Exception):
    pass


def _header(ptype, flags, call_id, body_len):
    return struct.pack("<BBBB4sHHI", 5, 0, ptype, flags, DREP_LE,
                       16 + body_len, 0, call_id)


def _pad4(data):
    return data + b"\x00" * ((4 - len(data) % 4) % 4)


def _ndr_string(text):
    """A [string] wchar_t* body: conformant varying, NUL-terminated UTF-16."""
    raw = (text + "\x00").encode("utf-16-le")
    count = len(raw) // 2
    return _pad4(struct.pack("<III", count, 0, count) + raw)


class Reader:
    """Little-endian NDR reader over one request stub."""

    def __init__(self, data):
        self.data = data
        self.at = 0

    def align(self, n):
        self.at = (self.at + n - 1) & ~(n - 1)

    def u32(self):
        self.align(4)
        if self.at + 4 > len(self.data):
            raise RpcError("stub too short")
        v = struct.unpack_from("<I", self.data, self.at)[0]
        self.at += 4
        return v

    def skip_string(self):
        max_count = self.u32()
        _offset = self.u32()
        actual = self.u32()
        if actual > max_count or self.at + actual * 2 > len(self.data):
            raise RpcError("bad string")
        self.at += actual * 2


class SrvsvcPipe:
    """One open \\PIPE\\srvsvc: DCE/RPC in, DCE/RPC out.

    ``shares`` is a callable returning (name, type, remark, path) tuples, read
    when a call arrives so a share added while the pipe is open is listed.
    """

    def __init__(self, shares, server_name):
        self.shares = shares
        self.server_name = server_name
        self.bound = False
        self.context_id = 0
        self.max_frag = MAX_FRAG
        self.outbox = b""

    # -- the two transports ------------------------------------------------- #
    def write(self, data):
        """A WRITE to the pipe: run it, keep the answer for the next READ."""
        self.outbox += self.transact(data)
        return len(data)

    def read(self, length):
        """(data, more): up to ``length`` queued bytes, and whether any remain."""
        data, self.outbox = self.outbox[:length], self.outbox[length:]
        return data, bool(self.outbox)

    def transact(self, data):
        """One request PDU in, its whole answer out (FSCTL_PIPE_TRANSCEIVE)."""
        if len(data) < 16:
            raise RpcError("short PDU")
        (ver, minor, ptype, flags, _drep, frag_len, auth_len,
         call_id) = struct.unpack_from("<BBBB4sHHI", data, 0)
        if ver != 5 or minor not in (0, 1) or frag_len > len(data) or frag_len < 16:
            raise RpcError("not a DCE/RPC v5 PDU")
        body = data[16:frag_len - auth_len if auth_len else frag_len]
        if ptype in (PTYPE_BIND, PTYPE_ALTER_CONTEXT):
            return self._bind(ptype, call_id, body)
        if ptype == PTYPE_REQUEST:
            if not (flags & PFC_FIRST_FRAG and flags & PFC_LAST_FRAG):
                # Requests here are a few dozen bytes; a fragmented one is a
                # client this server does not understand, so say so.
                return self._fault(call_id, NCA_UNSPEC_REJECT)
            return self._request(call_id, body)
        raise RpcError("unexpected packet type %d" % ptype)

    # -- PDUs --------------------------------------------------------------- #
    def _bind(self, ptype, call_id, body):
        if len(body) < 12:
            raise RpcError("short bind")
        max_xmit, max_recv, assoc = struct.unpack_from("<HHI", body, 0)
        n_ctx = body[8]
        at = 12
        results = b""
        for _ in range(n_ctx):
            if at + 24 > len(body):
                raise RpcError("short bind context")
            ctx_id, n_syn = struct.unpack_from("<HB", body, at)
            abstract = body[at + 4:at + 24]
            syntaxes = [body[at + 24 + 20 * i:at + 44 + 20 * i] for i in range(n_syn)]
            at += 24 + 20 * n_syn
            if abstract != SRVSVC_SYNTAX:
                results += struct.pack("<HH", RESULT_PROVIDER_REJECTION,
                                       REASON_ABSTRACT_SYNTAX_NOT_SUPPORTED) + b"\x00" * 20
            elif NDR32_SYNTAX in syntaxes and not self._accepted_in(results):
                self.bound = True
                self.context_id = ctx_id
                results += struct.pack("<HH", RESULT_ACCEPTANCE, 0) + NDR32_SYNTAX
            else:
                # NDR64-only, the bind-time feature negotiation context, or a
                # second NDR32 context: refused, which every client handles.
                results += struct.pack("<HH", RESULT_PROVIDER_REJECTION,
                                       REASON_TRANSFER_SYNTAXES_NOT_SUPPORTED) + b"\x00" * 20
        # The smaller of what either side can take, and never past ours.
        self.max_frag = max(1024, min(MAX_FRAG, max_recv or MAX_FRAG))
        out = struct.pack("<HHI", self.max_frag, self.max_frag, assoc or 0x53F0)
        if ptype == PTYPE_BIND:
            sec = b"\\PIPE\\srvsvc\x00"
            out += struct.pack("<H", len(sec)) + sec
        else:
            out += struct.pack("<H", 0)     # alter_context_resp: empty address
        out = _pad4(out)
        out += struct.pack("<B3x", n_ctx) + results
        reply = PTYPE_BIND_ACK if ptype == PTYPE_BIND else PTYPE_ALTER_CONTEXT_RESP
        return _header(reply, PFC_FIRST_FRAG | PFC_LAST_FRAG, call_id, len(out)) + out

    @staticmethod
    def _accepted_in(results):
        return any(results[i:i + 2] == b"\x00\x00" for i in range(0, len(results), 24))

    def _fault(self, call_id, status):
        body = struct.pack("<IHBBII", 32, self.context_id, 0, 0, status, 0)
        return _header(PTYPE_FAULT, PFC_FIRST_FRAG | PFC_LAST_FRAG | 0x20,
                       call_id, len(body)) + body

    def _request(self, call_id, body):
        if len(body) < 8:
            raise RpcError("short request")
        _alloc, ctx_id, opnum = struct.unpack_from("<IHH", body, 0)
        if not self.bound or ctx_id != self.context_id:
            return self._fault(call_id, NCA_PROTO_ERROR)
        if opnum != OPNUM_NETR_SHARE_ENUM:
            return self._fault(call_id, NCA_OP_RNG_ERROR)
        try:
            stub = self._share_enum(body[8:])
        except RpcError:
            return self._fault(call_id, NCA_PROTO_ERROR)
        return self._response(call_id, stub)

    def _response(self, call_id, stub):
        """The answer, split into fragments no larger than the bind allowed."""
        room = self.max_frag - 16 - 8
        out = b""
        at = 0
        first = True
        while True:
            chunk = stub[at:at + room]
            at += len(chunk)
            last = at >= len(stub)
            flags = (PFC_FIRST_FRAG if first else 0) | (PFC_LAST_FRAG if last else 0)
            body = struct.pack("<IHBB", len(stub) - at + len(chunk),
                               self.context_id, 0, 0) + chunk
            out += _header(PTYPE_RESPONSE, flags, call_id, len(body)) + body
            first = False
            if last:
                return out

    # -- NetrShareEnum ------------------------------------------------------ #
    def _share_enum(self, stub):
        r = Reader(stub)
        if r.u32():                         # [in,string,unique] ServerName
            r.skip_string()
        level = r.u32()                     # InfoStruct->Level
        _switch = r.u32()                   # union discriminant
        # The rest (the empty container, PreferedMaximumLength, ResumeHandle)
        # asks for nothing this server would answer differently.

        shares = list(self.shares())
        if level not in (0, 1, 2):
            return struct.pack("<IIIIII", level, level, 0, 0, 0, ERROR_INVALID_LEVEL)

        fixed = b""
        deferred = b""
        ref = 0x00020000

        def ptr():
            nonlocal ref
            ref += 4
            return struct.pack("<I", ref)

        for name, stype, remark, path in shares:
            fixed += ptr()                                  # shiN_netname
            deferred += _ndr_string(name)
            if level >= 1:
                fixed += struct.pack("<I", stype) + ptr()   # type, remark
                deferred += _ndr_string(remark)
            if level == 2:
                fixed += struct.pack("<III", 0, 0xFFFFFFFF, 0)  # permissions, max_uses, current_uses
                fixed += ptr()                              # path
                deferred += _ndr_string(path)
                fixed += struct.pack("<I", 0)               # passwd: NULL

        out = struct.pack("<II", level, level)
        out += ptr()                                        # SHARE_INFO_N_CONTAINER*
        out += struct.pack("<I", len(shares))               # EntriesRead
        if shares:
            out += ptr() + struct.pack("<I", len(shares)) + fixed + deferred
        else:
            out += struct.pack("<I", 0)                     # Buffer: NULL
        out += struct.pack("<I", len(shares))               # TotalEntries
        out += ptr() + struct.pack("<I", 0)                 # ResumeHandle
        out += struct.pack("<I", 0)                         # WERROR: success
        return out
