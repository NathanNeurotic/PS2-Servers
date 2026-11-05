# PS2 Servers Edge architecture

This document describes the native Edge implementation and its boundaries
relative to the Desktop launcher and Python Core servers.

## Product boundary

- **PS2 Servers Desktop** remains the full Windows, Linux, and macOS launcher.
- **PS2 Servers Core** is the existing Python server stack exposed without the
  GUI. `ps2servers serve udpfs ...` uses the same file, compression, UDPBD, and
  diagnostics implementation as Desktop.
- **PS2 Servers Edge** is a first-party Go executable for Linux appliances,
  routers, NAS devices, Raspberry Pi systems, and older computers where a GUI
  and Python runtime are inappropriate.

Edge does not replace Desktop. The native code has no import or runtime
relationship with the GUI.

## Native package boundaries

```text
native/ps2servers-edge/
├── cmd/ps2servers-edge/      command parsing and process lifecycle
└── internal/
    ├── protocol/             bounded UDPRDMA packet codecs
    ├── session/              per-peer protocol and handle state
    ├── udpfs/                discovery, negotiation, transport, operations,
    │                         and BREAD/BWRITE block access
    ├── udpbd/                the separate UDPBD block protocol
    ├── smb/                  native SMBv1 protocol server
    ├── webui/                embedded responsive web dashboard, JSON API,
    │                         log ring buffer, and UCI/JSON config backends
    ├── filesystem/           rooted path and symlink enforcement
    ├── compression/          ISO, CSO, and ZSO readers, with a block cache
    └── logging/              text and JSON event output
```

Each peer owns its profile, discovery sequence, response socket, receive and
transmit sequences, retransmit buffer, activity timestamp, fallback generation,
and open handles. No global client profile exists.

## Automatic compatibility state machine

1. Record the 12-bit sequence from a valid UDPFS DISCOVERY.
2. Send canonical INFORM immediately from the normal data socket.
3. Keep the session `pending` and schedule a short fallback.
4. If no DATA has classified the peer, send a compatibility INFORM from the
   discovery socket. This consumes that session's first server sequence only.
5. Accept the first DATA through either local socket.
6. Classify by sequence behavior:
   - standard: discovery `0`, DATA `0`
   - Modulo: DATA equals `(discovery + 1) mod 4096`
   - the nonzero discovery value preserves `4095 -> 0` as Modulo
7. Preserve the selected response socket and protocol counters for that peer.

Sequence, socket, fallback, and file state cannot leak between sessions.
Background discovery of any sequence is answered without resetting an active
transfer or closing its handles; only sequence zero records an ambiguous loader
handoff for subsequent DATA to resolve. A quiet or nonzero re-handshake of an
idle, non-streaming session replaces the old peer state and closes its handles.
A sequence-zero discovery followed by DATA that starts again at 0 or 1, or
unexpected payload DATA zero without rediscovery, resets transport while keeping
the old loader's handles open, as Neutrino's FILEID backend needs
(`session.State.HandOff`). The new loader opening for writing a file one of
them holds for writing closes that handle and takes over its write reservation;
deleting such a file closes the handle the same way.

Strict `standard` and `modulo` modes remain diagnostic policies. They do not
force a network topology: DATA is still accepted on either local socket.

## Transport

The native sender uses bounded 1408-byte packets, an eight-packet send window,
cumulative ACK handling, immediate NACK retransmission, final-ACK confirmation,
and bounded retry counts. ACK/NACK control packets bypass the per-peer request
queue so a transfer can advance while the request worker is occupied. Optional
`--udp-batch` mode submits each unpaced window or retransmission group through
Linux `ipv4.PacketConn.WriteBatch`, with per-socket cached writers and portable
per-packet fallback for every unsent suffix. Disabled batching, non-Linux hosts,
and nonzero `--tx-delay-ms` retain the per-packet path; sequence numbers,
retransmission contents, metrics, and traces are identical.

## Filesystem and images

Edge serves writes unless started with `--read-only`. Leading separators are
UDPFS root-relative syntax; it rejects drive-qualified and device paths, every
`..` component, and symlink escapes before opening files. Directory
enumeration omits entries that cannot be resolved safely beneath the configured
root. `MKDIR`, `REMOVE` and `RMDIR` resolve only the parent of their path
(`filesystem.Root.Entry`), so like `mkdir(2)`, `unlink(2)` and `rmdir(2)` they
act on a symlink itself and never on its target. Directory enumeration reports
a link to a folder as a folder, so `REMOVE` and `RMDIR` also refuse a path that
passes through one (`filesystem.Root.RealFolders`): a console deleting a folder
recursively would otherwise empty the folder the link points at.

A refused or failed request is answered with a negated errno number, and it is
the number the Desktop server sends from the same host: the host's own on Linux
and macOS, and on Windows the C runtime's, which Python derives from the Windows
error code (`crtErrno` in `internal/udpfs/errno.go` follows CPython's table).
Replies never use Go's `syscall` constants for this: on Windows most of those
are values Go invented from 2^29 up rather than errno numbers.

Plain files, ISO images, and directly named compressed containers are read as
raw bytes with their on-disk size. CSO uses zlib and ZSO uses a bounded raw-LZ4
decoder. A compressed container is advertised by appending `.iso` to its full
name (`game.cso` becomes `game.cso.iso`) and reporting its uncompressed size, so
a same-stem plain ISO cannot collide with it. That canonical alias decodes its
exact source; legacy `game.iso` aliases still resolve same-stem containers when
no plain ISO exists. Decompressed blocks are cached per open image up to
`--compression-cache-size`. `--no-compression` disables aliases and decoding, so
a container is served as raw bytes under its real name. CHD is deliberately not
in the default CGO-free implementation. An explicit `-tags chd` CGO build adds a
bounded libchdr backend with the same Image read/seek/cache path and enables
`.chd.iso` aliases only when that backend is present; see
[Edge configuration](EDGE.md).

`--block-device` additionally serves one image through the UDPFS `BREAD`/
`BWRITE` opcodes on handle 0, which `OPEN` never returns. Access is positional
because one image is shared by every session; a seek cursor would let two
consoles move each other's read position. On any other handle the same opcodes
address the file that handle opened, in 512-byte sectors: Neutrino's FILEID
backend reads a game and writes a VMC that way, through handles its loader
opened and the stage running the game inherits.

## UDPBD boundary

`internal/udpbd/` implements the protocol natively and `ps2servers-edge udpbd`
exposes it: `INFO`, `READ` with RDMA streaming and the upstream block-size
optimizer, and `WRITE` with its RDMA handshake. The wire format was ported from
this repository's hardware-validated Python UDPBD server, which remains intact.

The two are held together by `conformance/integration/udpbd_probe.py`, which
runs against both implementations in CI and finishes by asserting they produce
byte-identical images from identical writes. The probe defines its own packet
encoding rather than importing either server's: a probe that shares an encoder
with an implementation cannot catch that implementation's encoding bugs.

Still outstanding: no console or emulator has driven native UDPBD.

## Verification boundary

This implementation is unit tested, loopback integration tested, statically
analyzed with `go vet`, and cross-compiled for the documented Linux targets. It
does not establish emulator or physical-console compatibility. Record those
results with the exact Edge and loader builds separately.
