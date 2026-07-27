# PS2 Servers Edge

Native UDPFS for routers, NAS devices, Raspberry Pi systems, embedded Linux,
headless servers, and older computers. Also built for Windows and macOS as a
single binary for people who want no Python runtime.

```sh
go build ./cmd/ps2servers-edge
./ps2servers-edge udpfs --root /mnt/games --protocol-mode auto

# Serve read-only. Writes are allowed by default, so saves work without this.
./ps2servers-edge udpfs --root /mnt/games --read-only
```

Edge is one edition of PS2 Servers; it does not replace the desktop launcher.
The default `auto` mode negotiates standard and Modulo clients independently so
both can transfer concurrently.

## Implemented

- UDPFS discovery and canonical INFORM
- delayed Modulo-compatible fallback
- per-session sequence and response-socket state
- directory open/read, file open/read/seek/close, getstat
- file write: WRITE_REQ / WRITE_DATA / WRITE_DONE, chunk assembly, O_CREAT,
  O_TRUNC and O_APPEND, one writer per file at a time
- retransmission, NACK handling, send windows, sequence wraparound
- multiple clients and idle cleanup
- safe rooted filesystem; writes allowed unless `--read-only` is passed
- ISO, CSO/CISO, ZSO/ZISO
- text and JSON logs
- graceful SIGINT/SIGTERM shutdown

## Writes

On by default, matching the Desktop/Core server and udpfsd. UDPFS is a two-way
protocol in practice: a console loading a game off the share usually wants to
write its saves back to it. Pass `--read-only` (or `RO=1`) to serve read-only.

The OpenWrt package still starts read-only unless the operator sets
`option read_only '0'` — a router share is often an entire attached disk.

Guarantees:

- a second peer opening the same file for writing gets `EBUSY`, since two
  consoles interleaving into one file would corrupt it
- writes to CSO/ZSO/CHD are refused with `EACCES` — those readers present a
  decompressed view, so writing at a decompressed offset would land bytes at
  the wrong place in the container
- one assembled write is capped at 8 MiB, so a peer cannot make the server
  buffer an arbitrary amount before anything is validated
- a chunk arriving out of order aborts the whole write with `EIO` rather than
  silently concatenating it into the wrong place
- completed writes are flushed with `fsync` before `WRITE_DONE` is sent

## Not claimed

- CHD in generic static builds
- native UDPBD
- block-device writes (`BWRITE`); file writes only
- emulator or physical-console verification of the write path

See `docs/EDGE.md`, `docs/PROTOCOL-COMPATIBILITY.md`, and
`docs/EDGE-ARCHITECTURE.md` for installation, compatibility, security, and
validation details.
