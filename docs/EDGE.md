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

1. Download the artifact matching the CPU.
2. Verify the archive against its adjacent `.sha256` file.
3. Install it as `/usr/local/bin/ps2servers-edge`.
4. Run it as an unprivileged account with the game directory mounted read-only.

Target names:

| Artifact | Go target | Notes |
|---|---|---|
| `linux-386` | `GOARCH=386` | old 32-bit x86 |
| `linux-amd64` | `GOARCH=amd64` | x86-64 |
| `linux-armv6` | `GOARCH=arm GOARM=6` | older Raspberry Pi |
| `linux-armv7` | `GOARCH=arm GOARM=7` | 32-bit ARM |
| `linux-arm64` | `GOARCH=arm64` | 64-bit ARM |
| `linux-mips` | `GOARCH=mips GOMIPS=softfloat` | big-endian MIPS32 |
| `linux-mipsle` | `GOARCH=mipsle GOMIPS=softfloat` | little-endian MIPS32 |
| `linux-riscv64` | `GOARCH=riscv64` | 64-bit RISC-V |

These are generic Linux executables, not OpenWrt `.ipk` packages.

## Raspberry Pi and NAS

Use `linux-armv6`, `linux-armv7`, or `linux-arm64` according to the installed OS,
not merely the board model. For a NAS, select the architecture reported by the
NAS shell and keep the game share mounted before the service starts.

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
