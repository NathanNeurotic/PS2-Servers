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
- `--protocol-mode auto|standard|modulo`
- `--modulo-mode` — deprecated alias for strict Modulo mode
- `--single-port`
- `--data-port 0` — automatic data port; use a fixed value for strict firewalls
- `--peer-timeout 1h`
- `--log-format text|json`
- `--verbose`, `--quiet`, `--version`

Edge does not need root. Grant the service account read and directory-traverse
permission on the game root and open the selected UDP ports in the firewall.

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
[OPENWRT.md](OPENWRT.md).

Windows and macOS ship as `.zip`, which both Explorer and Finder open with a
double-click; everything else ships as `.tar.gz`, which preserves the
executable bit. The desktop builds are the **headless command-line server** —
for the graphical app use the `PS2Servers-*` downloads instead. They exist for
users who want one small native binary and no Python runtime, including those
whose antivirus flags the packaged Python build.

32-bit big-endian PowerPC (WD MyBook Live, OpenWrt `apm821xx`) has no Edge
build and never will: Go does not support that architecture. Use the
Desktop/Python UDPFS server there instead.

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

CHD normally requires CGO and `libchdr`. This pull request does not pretend that
CHD can be universally statically cross-compiled. A future CHD build family
should use an explicit build tag, supported host/target combinations, separate
artifact names, dependency documentation, and CHD integration fixtures. The
CGO-free router binaries remain independent of that path.

## UDPBD status

Native UDPBD is not advertised in this version. The CLI architecture reserves a
peer subcommand boundary, but verified native UDPBD requires protocol fixtures
and integration tests that demonstrate real sector reads and correct responses.
Desktop/Core UDPBD is unchanged.

## Writes

Edge supports file writes, so a console can save. They are **on by default**,
matching the Desktop/Core server and udpfsd. UDPFS is a two-way protocol in
practice — a console that loads a game off the share usually wants to write its
saves back to the same place — so a read-only default would make Edge quietly
less capable than its siblings for the ordinary case.

Pass `--read-only` (or `RO=1`) to serve read-only instead.

**The OpenWrt package still starts read-only** unless the operator opts in, via
`option read_only '1'` in `/etc/config/ps2servers-edge`. A router share is
frequently an entire attached disk, and the service is unauthenticated on the
LAN, so writes are opt-in on that platform regardless of the binary default.

With writes enabled:

| Behaviour | Result |
|---|---|
| Second peer opens the same file for writing | `EBUSY` — concurrent writers would interleave and corrupt |
| Write to a CSO/ZSO/CHD image | `EACCES` — the reader shows a decompressed view, so the offsets do not correspond to the container |
| Single assembled write over 8 MiB | `EFBIG` — bounds what one peer can make the server buffer |
| Chunk arrives out of order | `EIO`, whole write abandoned rather than silently misassembled |
| Write on a handle opened read-only | `EACCES` — access is decided at OPEN and re-checked |
| Write completes | `fsync` before `WRITE_DONE`, so a console pulled mid-session does not lose the save |

Block-device writes (`BWRITE`) are not implemented; file writes only.

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
- shared conformance probe (`conformance/integration/udpfs_probe.py`): yes, run
  against a live Edge process in CI — standard plus three Modulo-shaped clients
  concurrently, covering the 4095 → 0 sequence wrap
- parser fuzz seeds: yes
- `go vet`: passed in the implementation environment
- sixteen target builds (eleven Linux, three Windows, two macOS): compiled and
  inspected
- emulator tested: no
- physical PS2 hardware tested: no
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
