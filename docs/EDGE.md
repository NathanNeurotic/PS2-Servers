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
  --read-only \
  --log-format text
```

Important options:

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

These are generic Linux executables, not OpenWrt `.ipk` packages. For OpenWrt,
build the source package for your exact target — see [OPENWRT.md](OPENWRT.md).

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

## Security model

Edge is read-only and enforces a configured filesystem root. It rejects `..`,
absolute and drive-qualified paths, unsafe symbolic links, oversized paths,
oversized reads, malformed packet lengths, unknown packet types, and excessive
open handles. Idle sessions are removed and their files are closed. Normal logs
do not print full requested host paths.

## Validation status

- unit tested: yes
- loopback integration tested: yes
- parser fuzz seeds: yes
- `go vet`: passed in the implementation environment
- eight Linux target builds: compiled and inspected
- emulator tested: no
- physical PS2 hardware tested: no

Hardware validation must be recorded separately before making a hardware-success
claim.
