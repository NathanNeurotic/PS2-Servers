# PS2 Servers Edge

PS2 Servers Edge is the native, headless member of the PS2 Servers family. It is
intended for routers, NAS devices, Raspberry Pi systems, embedded Linux, old
computers, and servers where the Desktop GUI or Python runtime is unsuitable.

## Run

```sh
ps2servers-edge udpfs \
  --root /mnt/games \
  --bind 0.0.0.0 \
  --port 62966 \
  --protocol-mode auto \
  --data-port 0 \
  --peer-timeout 1h \
  --log-format text
```

Important options:

- `--read-only` — serve read-only; writes are allowed by default
- `--bind IP` — bind both discovery and data sockets to this local IPv4 address;
  unlike Core, Edge does not accept an `IP:port` suffix. Verify broadcast
  discovery still reaches a specifically bound socket on your host.
- `--protocol-mode auto|standard|modulo`
- `--modulo-mode` — deprecated alias for strict Modulo mode
- `--single-port`
- `--data-port 0` — automatic data port; use a fixed value for strict firewalls
- `--peer-timeout 1h`
- `--tx-delay-ms 0` — inter-packet pacing; raise it if a slow link drops packets
- `--log-format text|json`
- `--verbose`, `--quiet`, `--version`

Serving a disk image alongside the folder:

- `--block-device /path/ps2.img` — also answer UDPFS block reads and writes
- `--sector-size 512` — sector size for that image; multiple of 512, max 65536

Compressed images:

- `--compression-cache-size 32` — decompressed blocks kept per open image
  (`0`–`4096`; `0` disables the cache)
- `--no-compression` — serve CSO/ZSO as raw bytes; a diagnostic, see below

Observability & Web Management:

- `--metrics` — log periodic transfer counters
- `--metrics-period 1m` — how often, from `1s` to `24h`; parsed (and validated) only when `--metrics` is on

Tooling & automation:

- `--version` — print the version and exit
- `--dump-schema` — print the whole configuration contract as JSON: every
  section and option of `/etc/config/ps2servers-edge` with its type, default,
  range, and which layer rejects a bad value. Firmware and provisioning tools
  that generate the config should validate against this output from the exact
  binary they ship; section and option names are additive-only across updates.

Web UI:

- `ps2servers-edge webui` — run embedded web dashboard on port `8082` (or OS assigned)
- Options: `--webui-port 8082`, `--bind 127.0.0.1`, `--auth-pass`, `--config-file /etc/ps2servers-edge/config.json`, `--no-browse`

For CLI and deployment instructions, see [the operator manual](INSTRUCTIONS.md).
For an optional packet-by-packet NHDDL-to-Neutrino diagnostic procedure, see
[UDPFS-HANDOFF-DIAGNOSTICS.md](https://github.com/NathanNeurotic/PS2-Servers/blob/main/docs/UDPFS-HANDOFF-DIAGNOSTICS.md).

Most serving options also read environment defaults (`FSROOT`, `BIND`, `PORT`,
`BDPATH`, `RO`, `NO_COMPRESSION`, `METRICS`, and others). CLI flags take
precedence. Some flags, including `--quiet` and `--modulo-mode`, have no
environment equivalent; use `--help` for the installed binary.

Edge does not need root. Grant the service account read and directory-traverse
permission on the game root and open the selected UDP ports in the firewall.

## Serving a disk image over UDPFS

UDPFS carries block access as well as files. `--block-device` serves one image
through the `BREAD`/`BWRITE` opcodes from the same instance already serving the
folder, so one server can offer a console both a game directory and a virtual
drive:

```sh
ps2servers-edge udpfs --root /mnt/games --block-device /mnt/ps2.img
```

This matches the Desktop server's `--block-device` and udpfsd's `-bdpath`. It is
**not** the same thing as the `udpbd` subcommand below, which speaks a different
protocol on a different port.

All access is positional. One image is shared by every session, so a seek cursor
would let two consoles move each other's read position. Reads past the end of
the image are refused rather than answered with zero padding, and the image
inherits `--read-only`.

## Compression cache

A console reading sequentially touches the same compressed block repeatedly
inside a single request. Without a cache each of those touches re-inflates the
block from disk — wasted CPU on exactly the low-power hardware Edge targets.
`--compression-cache-size` bounds how many decompressed blocks are retained per
open image; the default of 32 is a few hundred kilobytes.

`--no-compression` serves CSO/ZSO containers as the raw bytes on disk and stops
them standing in for a missing `.iso`. It is a **diagnostic** for comparing
against an undecoded transfer, not a way to save CPU: the PS2 cannot interpret
container bytes, so games will not load from a share configured this way.

## Generic Linux

1. Download the artifact matching the device. Run `uname -m` on the device and
   match it in **[EDGE-WHICH-BUILD.md](EDGE-WHICH-BUILD.md)** — that page is the
   full picker, including how to tell big-endian from little-endian MIPS.
2. Verify the archive against its adjacent `.sha256` file.
3. Install it as `/usr/local/bin/ps2servers-edge`.
4. Run it as an unprivileged account with the game directory mounted read-only.

Downloads are named for the device family rather than the Go architecture, so
the release page can be read without knowing what "armv6" means. Each archive
also contains a `WHICH-DEVICE.txt` stating exactly what it is for.

| Artifact ends in | Go target | For |
|---|---|---|
| `pc-64bit-amd64` | `GOARCH=amd64` | 64-bit PC, home server, x86 NAS, VMs |
| `pc-32bit-i386` | `GOARCH=386` | old 32-bit-only x86 |
| `arm64-pi3-pi4-pi5-nas` | `GOARCH=arm64` | Pi 3/4/5 on 64-bit OS, modern ARM NAS and routers |
| `armv7-pi2-32bit-routers` | `GOARCH=arm GOARM=7` | Pi 2, Pi 3/4/5 on a 32-bit OS, 32-bit ARM routers |
| `armv6-pi1-pi-zero` | `GOARCH=arm GOARM=6` | Pi 1, Pi Zero / Zero W |
| `armv5-pogoplug-sheevaplug-dockstar` | `GOARCH=arm GOARM=5` | Kirkwood plugs, early ARM NAS |
| `router-mips-little-endian-mt7621` | `GOARCH=mipsle GOMIPS=softfloat` | little-endian MIPS routers (ramips/mt7621, incl. EdgeRouter X) |
| `router-mips-big-endian-ath79` | `GOARCH=mips GOMIPS=softfloat` | big-endian MIPS routers (ath79/lantiq) |
| `edgerouter-octeon-mips64` | `GOARCH=mips64 GOMIPS64=softfloat` | Cavium Octeon — EdgeRouter Lite/PoE/4/6P |
| `mips64-little-endian` | `GOARCH=mips64le GOMIPS64=softfloat` | little-endian 64-bit MIPS (uncommon) |
| `riscv64-sbc` | `GOARCH=riscv64` | 64-bit RISC-V SBCs |
| `windows-64bit` | `GOOS=windows GOARCH=amd64` | 64-bit Windows, headless |
| `windows-32bit` | `GOOS=windows GOARCH=386` | 32-bit Windows, headless |
| `windows-arm64` | `GOOS=windows GOARCH=arm64` | Windows on ARM, headless |
| `macos-apple-silicon` | `GOOS=darwin GOARCH=arm64` | macOS on M-series, headless |
| `macos-intel` | `GOOS=darwin GOARCH=amd64` | macOS on Intel, headless |

The Linux builds are generic executables, not OpenWrt `.ipk` packages. For
OpenWrt, build the source package for your exact target — see
[OPENWRT.md](https://github.com/NathanNeurotic/PS2-Servers/blob/main/docs/OPENWRT.md).

Windows and macOS ship as `.zip`, which both Explorer and Finder open with a
double-click; everything else ships as `.tar.gz`, which preserves the
executable bit. The desktop builds are the **headless command-line server** —
for the graphical app use the `PS2Servers-*` downloads instead. They exist for
users who want one small native binary and no Python runtime, including those
whose antivirus flags the packaged Python build.

32-bit big-endian PowerPC has no current Edge target. Core is the alternative
where a suitable Python runtime is available; performance on those hosts is
unverified. See the [build picker](EDGE-WHICH-BUILD.md).

## Raspberry Pi and NAS

Select by `uname -m`, not by the board model. A Raspberry Pi 4 running a 32-bit
OS reports `armv7l` and needs the armv7 build even though the hardware is
64-bit capable — this is the most common mistake. For a NAS, use the
architecture reported by the NAS shell and keep the game share mounted before
the service starts.

## Compression matrix

| Format | Desktop/Core | Edge generic static builds |
|---|---:|---:|
| ISO | Yes | Yes |
| CSO/CISO | Yes | Yes, pure Go zlib |
| ZSO/ZISO | Yes when LZ4 is available | Yes, bounded pure-Go LZ4 block decoder |
| CHD | Yes when `libchdr` is available | No |

CHD is not implemented in the generic CGO-free Edge binaries. Use Desktop/Core
with `libchdr` for CHD; no separate Edge CHD download is currently provided.

## UDPBD

Edge serves a disk image as a network block device as well as serving folders
over UDPFS, so picking Edge does not cost you a server the Desktop build has:

```sh
ps2servers-edge udpbd --image /mnt/ps2.img          # read/write
ps2servers-edge udpbd --image /mnt/ps2.img --read-only
```

It is a separate protocol on its own fixed port (`0xBDBD`), so it is a separate
subcommand: a `udpbd` invocation serves one image and nothing else. In OPL,
choose UDPBD — there is no IP or port to enter, the console finds the server by
broadcast.

If you want a folder **and** an image from one server, that is the `udpfs`
subcommand's `--block-device` option above, not this one. The difference is
which protocol the console speaks, not which files you have.

**UDPFS versus UDPBD.** UDPFS is a network file share: the console asks for a
named file and a byte range, and the server understands files. UDPBD is a
network hard drive: the console asks for numbered 512-byte sectors, and the
server has no idea what is in them, because the filesystem lives inside the
image and only the PS2 interprets it.

Implemented: `INFO`, `READ` with RDMA streaming and the upstream block-size
optimizer, and `WRITE` with its RDMA handshake. Requests that run past the end
of the image are refused rather than answered with unbounded zero padding.
Truncated and unsolicited RDMA packets are dropped rather than written at an
arbitrary offset. A read-only image reports `-1` rather than faking a successful
save.

The wire format was ported from this repository's hardware-validated Python
UDPBD server, and `conformance/integration/udpbd_probe.py` runs against **both**
implementations in CI, finishing by asserting that the two produce byte-identical
images from identical writes. A divergence surfaces as a CI failure rather than
as a console that will not boot.

## SMBv1

Edge includes a standalone SMBv1 server (`ps2servers-edge smb`), enabling routers
and headless boards to serve shares to Open-PS2-Loader (OPL) and POPSTARTER
directly without host Samba dependencies:

```sh
ps2servers-edge smb --share games=/mnt/storage/ps2 --port 1111
```

Options:
- `--share NAME=PATH` — share mapping (repeatable)
- `--root PATH` — shorthand helper for `--share games=PATH`
- `--bind 0.0.0.0` — bind address
- `--port 1111` — TCP port (set OPL's SMB Port to match; ports below 1024 require root)
- `--read-only` — serve share read-only
- `--status-port -1` — router status query port (binary default: UDP 62966;
  `0` disables). Disable it when another process already owns that port, as
  the OpenWrt package and supplied SMB environment file do.

## Web Management GUI

Edge includes an embedded, responsive Web GUI dashboard (`ps2servers-edge webui`)
for configured OpenWrt/procd or systemd services. On other hosts, restart
operations report manual instructions; the dashboard does not supervise arbitrary
terminal-launched processes. Initial installation still requires host setup:

```sh
# Loopback only, no password needed:
ps2servers-edge webui --webui-port 8082

# Reachable from a phone on the LAN, which needs a password:
ps2servers-edge webui --bind 0.0.0.0 --auth-user admin --auth-pass 'something long'
```

Options:
- `--webui-port 8082` — HTTP port for web dashboard (`0` uses `8082` with fallback to OS-assigned port)
- `--bind 127.0.0.1` — bind address. **Loopback by default.** A non-loopback bind
  requires `--auth-pass` or `--insecure`; without one of them the server refuses
  to start rather than publishing a management API to the LAN.
- `--auth-user admin` / `--auth-pass ''` — HTTP basic credentials. An empty
  password means no authentication and is only accepted on loopback.
- `--insecure` — accept a LAN bind with no password supplied. A random password
  is generated and written to the log at startup; it changes on every restart.
- `--config-file /etc/ps2servers-edge/config.json` — path to JSON config file (used on generic Linux/systemd/Docker)
- `--no-browse` — disables the file browser modal/endpoint (`/api/browse`) entirely
- `--browse-anywhere` — lets the file browser leave the configured server
  directories. Off by default: browsing is confined to the UDPFS root and the
  SMB share paths, which is everywhere worth picking.
- `--log-lines 1000` — SSE log streaming buffer capacity
- `--max-log-clients 8` — concurrent `/api/logs` streams

> [!IMPORTANT]
> **Security & scope.** The dashboard rewrites configuration, restarts services
> and lists directories, so treat reaching it as equivalent to shell access on
> the box. It binds loopback by default for that reason. Exposing it to the LAN
> is supported, but requires a password (or `--insecure`, which generates one).
> There is no TLS: the password crosses the LAN in the clear, so use it to stop
> casual access, not as protection against someone already capturing traffic.

> [!NOTE]
> The **Logs** tab shows a snapshot of the servers' own output — read from
> `logread` on OpenWrt and `journalctl` on systemd — followed by the web UI's
> live log. The snapshot is taken when the page connects and refreshes when it
> reconnects: following those would mean a child process per open browser tab,
> which on a small router is a cost with no ceiling.
>
> On a host that is neither OpenWrt nor systemd there is nowhere to read from,
> and the tab says so rather than looking empty.

## Writes

Edge implements file writes. They are **on by default** in the bare binary.
The loader must implement the corresponding save/VMC path; writable serving
alone does not establish that a game can save over UDPFS.

Pass `--read-only` (or `RO=1`) to serve read-only instead.

**The OpenWrt package still starts read-only.** It ships `option read_only '1'`
in `/etc/config/ps2servers-edge`; set it to `'0'` to let a console write. A
router share is frequently an entire attached disk, and the service is
unauthenticated on the LAN, so writes are opt-in on that platform regardless of
the binary default.

With writes enabled:

| Behaviour | Result |
|---|---|
| Second peer opens the same file for writing | `EBUSY` — concurrent writers would interleave and corrupt |
| Write to a CSO/ZSO/CHD image | `EACCES` — the reader shows a decompressed view, so the offsets do not correspond to the container |
| Single assembled write over 8 MiB | `EFBIG` — bounds what one peer can make the server buffer |
| Chunk arrives out of order | `EIO`, whole write abandoned rather than silently misassembled |
| Write on a handle opened read-only | `EACCES` — access is decided at OPEN and re-checked |
| Write completes | `fsync` before `WRITE_DONE`; this does not make interrupted or multi-request saves atomic |

Block-device writes (`BWRITE`) are implemented too, when `--block-device` is
set. They reuse the same chunk-assembly path as file writes, so the table above
applies to them as well, with two additions: the request is refused unless it
lies wholly inside the image, and the image is `fsync`ed before `WRITE_DONE`.

The range check is written as a subtraction rather than `sector + count >
sectorCount()`. The sector number arrives from the client as `lo | hi<<32` and
Go wraps silently on signed overflow, so the addition form accepts a sector near
`MaxInt64` — the wrapped sum is negative, which passes both the lower and upper
bound — and the resulting offset can land inside the real file. On a write that
is silent corruption at a client-chosen offset.

## Security model

Edge enforces a configured filesystem root. It rejects `..`, absolute and
drive-qualified paths, unsafe symbolic links, oversized paths, oversized reads
and writes, malformed packet lengths, unknown packet types, and excessive open
handles. It serves writes unless started with `--read-only`.
Idle sessions are removed, their files are closed, and any write reservation
they held is released. Normal logs do not print full requested host paths.

## Validation status

- unit tested: yes
- loopback integration tested: yes
- shared UDPBD conformance probe (`conformance/integration/udpbd_probe.py`): yes,
  run against Edge AND the Python server in CI, asserting both produce
  byte-identical images from identical writes
- shared UDPFS conformance probe (`conformance/integration/udpfs_probe.py`): yes, run
  against a live Edge process in CI — standard plus three Modulo-shaped clients
  concurrently, covering the 4095 → 0 sequence wrap
- parser fuzz seeds: yes
- `go vet`: passed in the implementation environment
- sixteen target builds (eleven Linux, three Windows, two macOS): compiled and
  inspected
- emulator tested: no
- physical PS2 game launch/write validation: not established by the recorded tests;
  the historical wLaunchELF browsing control is described in the handoff guide
- **file writes: not verified on hardware or an emulator.** The write path is
  implemented against the Python server's wire behaviour and covered by unit
  and loopback tests only. No console has been observed writing through Edge.
  Treat the write path as the least-proven part of Edge.

Hardware validation must be recorded separately before making a hardware-success
claim.

The conformance probe is worth its place: it caught a real defect the Go unit
tests could not see. `classify()` returned `Modulo` correctly for the
`modulo-fresh` case (discovery 0, first data 1), but `handleDiscovery` committed
a sequence-zero discovery straight to `Standard` and never scheduled the
compatibility INFORM, so a fresh Modulo client waited forever. The classifier
was right; the path that reaches it was not.
