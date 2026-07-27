# Which PS2 Servers Edge build do I need?

Edge ships one file per device family. This page tells you which one to take.

> **On Windows or macOS, you probably do not want Edge at all.** Edge is the
> headless command-line server for routers, NAS boxes and Raspberry Pi. On a
> desktop you almost certainly want the graphical launcher — the
> `PS2Servers-windows-…` or `PS2Servers-macos-…` download. Take the Edge
> desktop build only if you specifically want one small native binary with no
> Python runtime, for example because antivirus flags the packaged Python
> build. See [Windows and macOS](#windows-and-macos) below.

**If you are running Linux on an ordinary 64-bit PC, mini PC, or x86 NAS — take
`pc-64bit-amd64` and stop reading.** That covers most people.

---

## The 30-second answer

Run this on the device that will serve the games (not on your PS2, and not on
the computer you are browsing from):

```sh
uname -m
```

Match the output against this table:

| `uname -m` says | Download the file ending in | Typical devices |
| --- | --- | --- |
| `x86_64` | `pc-64bit-amd64` | Any normal PC, mini PC, home server, Intel/AMD NAS |
| `i386` `i486` `i586` `i686` | `pc-32bit-i386` | Old 32-bit-only PC, thin client, early Atom |
| `aarch64` or `arm64` | `arm64-pi3-pi4-pi5-nas` | Raspberry Pi 3/4/5 and Zero 2 W on a 64-bit OS, modern ARM NAS, newer 64-bit routers |
| `armv7l` | `armv7-pi2-32bit-routers` | Raspberry Pi 2, **Pi 3/4/5 running a 32-bit OS**, 32-bit ARM routers |
| `armv6l` | `armv6-pi1-pi-zero` | Raspberry Pi 1, Pi Zero, Pi Zero W |
| `armv5tel` or `armv5tejl` | `armv5-pogoplug-sheevaplug-dockstar` | SheevaPlug, DreamPlug, Pogoplug, Seagate DockStar, ZyXEL NSA320 |
| `mips` | one of the two MIPS router builds — see [below](#mips-routers-endianness) | Most home routers |
| `mips64` | `edgerouter-octeon-mips64` (or the little-endian one) | Ubiquiti EdgeRouter Lite / PoE / 4 / 6P |
| `riscv64` | `riscv64-sbc` | StarFive VisionFive 2, Milk-V, LicheeRV |

> **Raspberry Pi owners:** check `uname -m`, do not go by the box. A Pi 4
> running 32-bit Raspberry Pi OS reports `armv7l` and needs the **armv7**
> build, even though the hardware is 64-bit capable. This is the single most
> common mix-up.

### Picking wrong is harmless

If you download the wrong build it simply will not start, usually with
`cannot execute binary file` or `Exec format error`. Nothing is damaged and
nothing is written. Just delete it and try another. You cannot brick a device
by trying the wrong Edge build.

---

## MIPS routers: endianness

`uname -m` prints `mips` for both big-endian and little-endian MIPS, so it
cannot tell them apart on its own. Pick one of:

**On OpenWrt** — this is the reliable check:

```sh
opkg print-architecture
```

- Output contains `mipsel_…` (note the **el**) → little-endian → take
  **`router-mips-little-endian-mt7621`**
- Output contains `mips_…` with no `el` → big-endian → take
  **`router-mips-big-endian-ath79`**

**On any Linux** — inspect an existing binary:

```sh
readelf -h /bin/sh | grep Data
```

Look for `little endian` or `big endian` in the result.

**Or just try both.** There are only two, and a wrong pick fails harmlessly.

Rough guide to which is which:

- **Little-endian** (`ramips` / `mt7620` / `mt7621` / `mt76x8`) is the more
  common modern budget router: many GL.iNet travel routers, Xiaomi and Redmi
  routers, Netgear R6220, and the **Ubiquiti EdgeRouter X (ER-X)**.
- **Big-endian** (`ath79` / `ar71xx` / `lantiq`) is typical of older Atheros
  and Lantiq gear: many TP-Link Archer C7, WDR3600 and WDR4300 units, older
  Netgear models, and various AVM Fritz!Box devices.

> **EdgeRouter warning:** the EdgeRouter **X** (ER-X) is *not* an Octeon
> device — it is MediaTek MT7621, so it takes the **little-endian MIPS** build.
> The EdgeRouter **Lite**, **PoE**, **4**, **6P** and **12** are Octeon and take
> the **mips64** build. Same product line, different chips.

---

## Full build list

| File ends in | Go target | For |
| --- | --- | --- |
| `pc-64bit-amd64` | `GOARCH=amd64` | 64-bit x86 — PCs, servers, x86 NAS, VMs |
| `pc-32bit-i386` | `GOARCH=386` | 32-bit-only x86 |
| `arm64-pi3-pi4-pi5-nas` | `GOARCH=arm64` | 64-bit ARM — Pi 3/4/5 on 64-bit OS, modern ARM NAS and routers, ARM SBCs |
| `armv7-pi2-32bit-routers` | `GOARCH=arm GOARM=7` | 32-bit ARMv7 — Pi 2, Pi 3/4/5 on 32-bit OS, ipq40xx/ipq806x routers |
| `armv6-pi1-pi-zero` | `GOARCH=arm GOARM=6` | ARMv6 — Pi 1, Pi Zero / Zero W |
| `armv5-pogoplug-sheevaplug-dockstar` | `GOARCH=arm GOARM=5` | ARMv5TE — Kirkwood plugs and early ARM NAS |
| `router-mips-little-endian-mt7621` | `GOARCH=mipsle GOMIPS=softfloat` | Little-endian MIPS routers |
| `router-mips-big-endian-ath79` | `GOARCH=mips GOMIPS=softfloat` | Big-endian MIPS routers |
| `edgerouter-octeon-mips64` | `GOARCH=mips64 GOMIPS64=softfloat` | Cavium Octeon (EdgeRouter Lite/PoE/4/6P) |
| `mips64-little-endian` | `GOARCH=mips64le GOMIPS64=softfloat` | Little-endian 64-bit MIPS — uncommon |
| `riscv64-sbc` | `GOARCH=riscv64` | 64-bit RISC-V SBCs |
| `windows-64bit` | `GOOS=windows GOARCH=amd64` | 64-bit Windows |
| `windows-32bit` | `GOOS=windows GOARCH=386` | 32-bit Windows |
| `windows-arm64` | `GOOS=windows GOARCH=arm64` | Windows on ARM |
| `macos-apple-silicon` | `GOOS=darwin GOARCH=arm64` | macOS, M-series |
| `macos-intel` | `GOOS=darwin GOARCH=amd64` | macOS, Intel |

Device names are **representative examples, not an exhaustive list**. Hardware
revisions of the same model sometimes change chips entirely. `uname -m` and
`opkg print-architecture` are the authority; a model name never is.

Every build is statically linked with CGO disabled, so it needs no shared
libraries and runs on both musl and glibc systems.

---

## Windows and macOS

These builds ship as `.zip` (double-click to open) rather than `.tar.gz`.

| Your machine | Take |
| --- | --- |
| Windows 10/11, normal Intel or AMD PC | `windows-64bit` |
| Older 32-bit-only Windows | `windows-32bit` |
| Windows on ARM (Snapdragon laptop, ARM VM) | `windows-arm64` |
| Mac with M1/M2/M3/M4 | `macos-apple-silicon` |
| Intel Mac | `macos-intel` |

Remember these are the **headless server**: you run them from a terminal, there
is no window. Start one with:

```sh
ps2servers-edge.exe udpfs --root D:\PS2Games      # Windows
./ps2servers-edge udpfs --root /Users/you/PS2     # macOS
```

macOS will quarantine a downloaded binary; clear it with
`xattr -d com.apple.quarantine ps2servers-edge` or approve it once in
System Settings → Privacy & Security.

## Letting the console write (saves)

Every build is **read-only by default**. To let the PS2 write — memory-card
saves, for instance — add `--read-only=false`:

```sh
./ps2servers-edge udpfs --root /path/to/games --read-only=false
```

Only one console may hold a given file open for writing at a time; a second one
gets "device or resource busy". Writes to compressed images (CSO/ZSO/CHD) are
refused, because writing into a compressed container at a decompressed offset
would destroy the image — keep anything you want written as a plain file.

## What if none of these fit?

### OpenWrt: build the package for your exact target

The prebuilt files above are **generic Linux binaries, not `.ipk` packages**.
OpenWrt coverage is not limited to this list — `packaging/openwrt/` is a source
package that builds through the OpenWrt SDK for whatever target your router
uses. See [OPENWRT.md](OPENWRT.md). This is the supported route for any OpenWrt
architecture not listed above.

### 32-bit big-endian PowerPC — use the Python server instead

Some older NAS units, most notably the **WD MyBook Live** and Netgear WNDR4700
(OpenWrt target `apm821xx`), are 32-bit big-endian PowerPC. **Go cannot target
this architecture at all** — `go tool dist list` offers `ppc64` and `ppc64le`
only — so there will never be an Edge build for it.

Use the Desktop/Python UDPFS server on those devices instead. It runs there
fine: these are disk-booted NAS units rather than flash-constrained routers, so
they have room for a Python runtime, and the server's packet handling is
explicitly byte-order safe. Throughput on an ~800 MHz PowerPC has not been
measured, so treat performance as unverified.

### Anything else

Build from source. Any platform in `go tool dist list` will work:

```sh
cd native/ps2servers-edge
CGO_ENABLED=0 GOOS=linux GOARCH=<arch> go build ./cmd/ps2servers-edge
```

---

## After downloading

```sh
tar -xzf PS2ServersEdge-<version>-<your-build>.tar.gz
cd PS2ServersEdge-<version>-<your-build>
cat WHICH-DEVICE.txt          # confirms what this build is for
./ps2servers-edge udpfs --root /path/to/games
```

Each archive also carries `README.md`, `EDGE.md`, this guide, a
`BUILD-METADATA.txt` recording the exact toolchain, and a `.sha256` file
alongside the download for verification.

**CHD note:** Edge supports **CSO and ZSO** but **not CHD** — CHD needs a native
library that would break static linking. If your games are CHD files, use the
Desktop server, or convert them.
