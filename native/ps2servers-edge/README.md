# PS2 Servers Edge

Native read-only UDPFS for routers, NAS devices, Raspberry Pi systems, embedded
Linux, headless servers, and older computers.

```sh
go build ./cmd/ps2servers-edge
./ps2servers-edge udpfs --root /mnt/games --protocol-mode auto --read-only
```

Edge is one edition of PS2 Servers; it does not replace the desktop launcher.
The default `auto` mode negotiates standard and Modulo clients independently so
both can transfer concurrently.

## Implemented

- UDPFS discovery and canonical INFORM
- delayed Modulo-compatible fallback
- per-session sequence and response-socket state
- directory open/read, file open/read/seek/close, getstat
- retransmission, NACK handling, send windows, sequence wraparound
- multiple clients and idle cleanup
- safe rooted read-only filesystem
- ISO, CSO/CISO, ZSO/ZISO
- text and JSON logs
- graceful SIGINT/SIGTERM shutdown

## Not claimed

- CHD in generic static builds
- native UDPBD
- emulator or physical-console verification

See `docs/EDGE.md`, `docs/PROTOCOL-COMPATIBILITY.md`, and
`docs/EDGE-ARCHITECTURE.md` for installation, compatibility, security, and
validation details.
