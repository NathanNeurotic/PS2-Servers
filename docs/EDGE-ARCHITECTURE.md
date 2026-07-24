# PS2 Servers Edge architecture

This document records the implementation boundary for the first PS2 Servers
Edge pull request. It is intentionally separate from the desktop launcher and
from the established Python server code.

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
    ├── udpfs/                discovery, negotiation, transport, operations
    ├── filesystem/           rooted path and symlink enforcement
    ├── compression/          ISO, CSO, and ZSO readers
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
Sequence-zero background discovery is answered without resetting a recently
active transfer. A quiet or nonzero re-handshake replaces the old peer state and
closes its handles.

Strict `standard` and `modulo` modes remain diagnostic policies. They do not
force a network topology: DATA is still accepted on either local socket.

## Transport

The native sender uses bounded 1408-byte packets, an eight-packet send window,
cumulative ACK handling, immediate NACK retransmission, final-ACK confirmation,
and bounded retry counts. ACK/NACK control packets bypass the per-peer request
queue so a transfer can advance while the request worker is occupied.

## Filesystem and images

Edge is read-only. It rejects absolute paths, drive-qualified paths, `..`, and
symlink escapes before opening files. Directory enumeration omits entries that
cannot be resolved safely beneath the configured root.

Plain files and ISO images are read directly. CSO uses zlib and ZSO uses a
bounded raw-LZ4 decoder. Compressed images are exposed with virtual `.iso`
names and their uncompressed size. CHD is deliberately not in the default
CGO-free implementation; see `docs/EDGE.md`.

## UDPBD boundary

The command and package structure leaves room for an `udpbd` subcommand, but
this pull request does not claim native UDPBD support. The existing Python
UDPBD implementation remains intact. Native UDPBD requires packet fixtures and
real sector-read integration evidence before it should be advertised.

## Verification boundary

This implementation is unit tested, loopback integration tested, statically
analyzed with `go vet`, and cross-compiled for the documented Linux targets. It
has not been emulator-tested or hardware-tested in this pull request.
