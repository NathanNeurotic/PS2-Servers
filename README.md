<p align="center">
  <img src="ps2serversgithubbanner.png" alt="PS2 Servers" width="820">
</p>

# PS2-Servers

Standard PS2 network servers — **SMBv1 (RiptOPL)**, **UDPFS**, and **UDPBD** — for
loading PlayStation 2 games, apps, and media over a LAN. These are the protocols
PS2 homebrew uses to load over a network, so they are not OPL-only. Loaders
confirmed working include [Open PS2 Loader](https://github.com/ps2homebrew/Open-PS2-Loader)
(OPL) and its forks (RiptOPL, wOPL), plus **NHDDL**, **wLaunchELF-R3Z**, **SMS**,
and **POPStarter / POPSLoader**; other network-capable homebrew that speaks these
protocols should work too. (One known exception: **Modulo** doesn't follow the
UDPFS protocol and needs the launcher's dedicated Modulo mode — see below.) A
small **GUI launcher** runs them all without touching a terminal.

## Quick start — the launcher

The launcher lets you pick a server, choose your games folder, and click **Start**.
It detects your PC's LAN IP and shows the exact settings to enter in your loader
(OPL, RiptOPL, NHDDL, and the rest) — read them off the launcher window rather
than from any guide, since your address and port are specific to your machine.

- **Packaged app (no Python needed):** download the release for your OS.
  **On Windows, prefer the folder build** — unzip
  `PS2Servers-windows-x64-folder.zip` and run **`PS2Servers.exe`** from inside
  the folder. It is the same app, but without the self-extracting wrapper that
  makes the single-file `.exe` trip antivirus heuristics, so it comes up clean.
  A single-file `PS2Servers-windows-x64.zip` is still provided for convenience
  if your AV doesn't object. (Linux: `PS2Servers-linux-x64`, or
  `PS2Servers-linux-x64-folder.tar.gz` if `/tmp` is mounted `noexec`.)
- **From source:** double-click **`Start-Launcher.bat`** (Windows) or run
  `./start-launcher.sh` (Linux/macOS). Requires Python 3.

The launcher starts normally without administrator rights. The GUI shows whether
it is currently running as administrator and includes a **Restart as administrator**
button for the few Windows setup actions that actually need elevation.

The GUI uses a lightweight PS2-themed Tkinter skin. It does not use Electron,
Qt, a browser view, or any heavy UI framework.

## Windows security note

PS2 Servers is an unsigned open-source network tool. Because it runs local server
processes and may ask Windows Firewall to allow inbound LAN traffic, some
antivirus products may flag the packaged Windows EXE heuristically.

The SMBv1/RiptOPL server does **not** enable Windows' built-in SMB1 optional
feature tree. It speaks the OPL-compatible SMB1/CIFS subset itself and normally
listens on custom TCP port `1111`; OPL connects to this program directly. (Avoid
ports below 1033 — Windows can reserve or block low ports.)

Windows setup is intentionally narrow:

- no automatic enabling of Windows SMB1;
- no disabling of SMB1 automatic removal;
- Windows Firewall changes are limited to rules named `PS2 Servers - ...`;
- firewall allow/cleanup can be handled from the GUI without a terminal;
- administrator rights are requested only for firewall changes or advanced port
  `445` mode;
- advanced port `445` mode is optional and temporarily pauses Windows File
  Sharing only while that server mode is running.

Normal custom-port SMB mode, UDPFS, UDPBD, folder browsing, and logs do not need
the whole launcher to run as administrator. Keeping the default launch non-admin
reduces the blast radius of bugs and keeps the app easier to trust.

If a download is flagged, the antivirus statement
([docs/antivirus-transparency.md](docs/antivirus-transparency.md) — styled web
version at [docs/falsepositives.html](docs/falsepositives.html)) explains exactly
why heuristics fire, what the app does and does not do, how to verify a download,
and how to report a false positive to each vendor. A non-self-extracting
**folder** build is offered as an alternative download for AV-wary users.

See [SECURITY.md](SECURITY.md) for verification, cleanup, and reporting details.
See [docs/antivirus-transparency.md](docs/antivirus-transparency.md) for the
public antivirus false-positive review note, stable release identity, ports,
firewall behavior, and uninstall/cleanup details.

## What's inside

All three servers are pure-Python (standard library) and run on Windows, Linux and macOS.

| Folder | Server | What it does |
|--------|--------|--------------|
| [`smbv1_server/`](smbv1_server/) | **SMBv1 (RiptOPL)** | Shares a games folder over SMB — works even on Windows 11 where the OS removed SMB1. |
| [`udpfs_server/`](udpfs_server/) | **UDPFS** | Serves a folder and/or disk image over UDP; can transparently decompress CHD/CSO/ZSO. |
| [`udpbd_server/`](udpbd_server/) | **UDPBD** | Serves a disk image as a block device over UDP; the PS2 auto-discovers it. |

`udpbd_server/udpbd_server.py` is a pure-Python port of Rick Gaiser's UDPBD server —
see [its provenance](udpbd_server/SOURCE.md). UDPBD has largely been superseded by UDPFS.

UDPFS can transparently decompress CHD/CSO/ZSO images. How that support is bundled
in releases and provided in source mode is documented in
[docs/optional-compression-dependencies.md](docs/optional-compression-dependencies.md).

Want a UDPFS server without Python (handy on low‑end hardware, or to avoid antivirus
false positives on packaged builds)? **[udpfsd](https://github.com/pcm720/udpfsd)** by
pcm720 is a Go alternative — a single prebuilt binary with the same CHD/CSO/ZSO
support. See [Credits & thanks](#credits--thanks).

## UDPFS settings worth knowing

Defaults are right for every client we know of. These are the ones you might change.

### "Check this if you are using Modulo"

**Either/or — tick it only while you are actually using Modulo.**

[Modulo](https://github.com/AdityaKumar7209/Modulo-R1-Beta-Preview---PS2)'s client
does not follow the UDPFS protocol. It never restarts its sequence counter (on
hardware it climbs straight across a full server restart), and it cannot move to
the server's data port. That is not specific to us: it fails against
[pcm720's udpfsd](https://github.com/pcm720/udpfsd) for the same reasons. Modulo's
own repository ships a patched copy of a server to work around both, which is how
we established exactly what it expects.

This mode answers the way that patched server does. A correct client cannot follow
it — its INFORM consumes a sequence number, so the server's first reply arrives one
ahead of where a conformant client is listening. **While this is on, NHDDL, RiptOPL,
POPSTARTER, POPSLOADER and wLaunchELF-R3Z will not connect.** Untick it and they are
back. Nothing is lost either way; it is one at a time.

CLI: `--modulo-mode` (implies `--single-port`; env `MODULO_MODE`).

### Idle timeout

UDPFS drops a console once it goes quiet for this long, **closing whatever files it
had open**. UDPFS has no disconnect packet — a paused game and an unplugged console
are identical on the wire, both simply silent — so the timeout is the only thing
that tells them apart, and it does so by guessing.

Default **3600s (1 hour)**, matching udpfsd, bounded 60–86400. Lower it only to
clear stale consoles faster (handy with several PS2s sharing one address). Too low
and a long pause loses its game: `0` does **not** disable it and never did, so it
clamps up to the 60s minimum like any other out-of-range value.

CLI: `--peer-timeout SECONDS` (env `PEER_TIMEOUT`).

### Port and Data port

Leave both alone unless something needs them predictable. UDPFS normally serves
discovery on `0xF5F6` and hands each console off to an ephemeral data port, which a
manual firewall rule, port forwarding or a strict NAT cannot follow — pin **Data
port** in that case and a matching firewall rule is added for it.

`--single-port` serves discovery and data on the one port instead, for clients or
networks that cannot handle the two-port handshake. Unlike Modulo mode it changes
no protocol behavior, so normal clients keep working.

CLI: `--port`, `--data-port`, `--single-port`.

## Minimum system requirements

You need two machines on the **same LAN**: the **PC** that runs PS2 Servers, and
the **PS2** that loads games from it. The PC side is deliberately light — no
Python and no heavy runtime for the packaged app.

**Your PC (runs PS2 Servers)**

| | Requirement |
|---|---|
| OS | Windows 10/11 (x64), Linux (x64), or macOS (Apple Silicon or Intel) |
| Packaged app | No Python required — the download bundles everything |
| From source | Python 3 with Tkinter (Linux: `sudo apt install python3-tk`); official builds use Python 3.12 |
| Hardware | Any modern 64-bit PC for the **packaged GUI app** (x64 / Apple Silicon). Running a server from source needs only Python 3 and works on any architecture — see "On a Raspberry Pi, NAS, or other non-x64 box" below. |
| Network | Wired or Wi‑Fi LAN on the same subnet as the PS2 (wired recommended for large games) |
| Disk | A few hundred MB for the app, plus room for your game files |

**Your PS2 (loads over the network)**

| | Requirement |
|---|---|
| Console | A PS2 that boots homebrew (FreeMcBoot / FreeDVDBoot / modchip / etc.) |
| Loader | Network-capable PS2 homebrew — confirmed with OPL, RiptOPL, wOPL, NHDDL, wLaunchELF-R3Z, SMS, POPStarter / POPSLoader (Modulo needs the launcher's Modulo mode) |
| Network adapter | The PS2 Ethernet adapter — built into slims; SCPH‑10281 on fat units |
| Emulator (optional) | PCSX2 with a configured network adapter also works for testing |

**Optional — compressed images (UDPFS)**

Packaged builds bundle CHD (libchdr) and ZSO (lz4) support, so CHD/CSO/ZSO images
"just work" and appear as plain `.iso`. From source, install `lz4` for ZSO and a
`libchdr` native library for CHD; CSO always works (Python standard library). See
[docs/optional-compression-dependencies.md](docs/optional-compression-dependencies.md).

**Optional — system tray (Linux, experimental)**

On Windows the launcher can close/minimize to the system tray so the servers keep
running without a window. On Linux this is now available too (experimental): the
packaged **AppImage** bundles it, and from source it turns on if you
`pip install pystray` (Pillow is also needed for the icon). It shows a tray icon on
desktops with a system tray — XFCE, Cinnamon, MATE, KDE, or GNOME with the
AppIndicator extension — where you can enable **Close/Minimize to tray** in the
About tab. It is **off by default** on Linux; enable it once you see the icon
appear. Desktops without a tray simply show no icon, and closing the window quits
as normal. macOS has no tray (closing the window quits).

## Direct PS2-to-PC link (no router)

A PS2 cabled straight into the PC has no router on the wire, so nothing hands the
console an IP address — every network app then fails the same way (empty lists,
"check cable and DHCP"). Tick **"PS2 is plugged directly into this PC"** at the top
of the launcher and that whole problem disappears: PS2 Servers finds the right
network port, gives the PC a fixed address on it (one administrator prompt), and
runs a tiny DHCP helper on that port only, so the console — which asks for an
address by itself — just gets one. The LAN IP box fills in with the address to use
in OPL. Unticking the box undoes all of it.

**You normally never configure the PS2.** On Windows, if the console still has a
leftover static IP from an earlier setup, the helper sees the device on the wire
and quietly moves *this PC* to a compatible address so the two coexist — stepping
off a clashing address, or even adopting the console's subnet if it is on a
different one. The console finds the server by broadcasting, so it usually needs
no changes. Only if no shared address can be found — an unusually busy wire —
does the launcher fall back to asking you to set the PS2 to DHCP or a different
static IP. (This automatic coexistence is Windows-only for now; on Linux and
macOS a console with a leftover static IP may need setting to DHCP or a matching
static address.)

The helper is deliberately paranoid, because a DHCP server answering on a real
network could hand bad addresses to everything on it. It binds only to the
direct-link port, refuses to start if that port reaches a router or already got an
address from a real DHCP server, serves exactly one address to one console, and
stops itself if several devices start asking.

Direct link works on Windows. On **Linux and macOS it is experimental**: the port
setup and DHCP both need root there, so ticking the box runs the helper as
administrator (via `pkexec` on Linux, the standard admin prompt on macOS). It adds
the address to the chosen port for the session and normally removes it again when
the helper stops (on unticking the box, a crash, or the launcher exiting). Because
the address is only *added*, it is not persistent — a reboot always clears it. If
the helper is force-killed in a way that skips its cleanup, the launcher checks
the port afterwards and, if the temporary address is still there, tells you so
rather than reporting a clean stop; a reboot or a manual removal clears it. If
anything looks off, untick the box and send the TERMINAL output.

## Run a server on its own (terminal)

Each server still runs standalone, and the launcher can run them too:

```sh
python smbv1_server/smbserver_opl.py --share games=D:/PS2Games
python udpfs_server/udpfs_server.py  -d D:/PS2Games --enable-compression
python udpbd_server/udpbd_server.py  D:/PS2Games/game.iso
# or, via the launcher engine:
python -m launcher --serve udpfs -d D:/PS2Games
python -m launcher --list            # show servers available on this machine
```

### On a Raspberry Pi, NAS, or other non-x64 box

The servers are pure Python standard library, so they run on **any machine with
Python 3** — a Raspberry Pi (any model), an ARM/MIPS NAS, an OpenWrt router —
even where there is no packaged build for that architecture. Baseline serving
needs no pip installs and no GUI; point a server at your games folder and it
serves over the LAN just the same:

```sh
python3 udpfs_server/udpfs_server.py -d /srv/ps2games
```

CSO decompression is built in (it uses the standard library). The *optional*
compressed formats do have dependencies: ZSO needs the `lz4` package and CHD
needs a native `libchdr` library — install those if you want ZSO/CHD, and any
format whose library is missing is simply left untouched (uncompressed images
are unaffected).

(The **minimum requirements** table above describes the *packaged GUI app*, which
is x64/Apple-Silicon only. Running a server straight from source, as here, has no
such limit — any architecture with Python 3 works.) This is the box that is
already on 24/7 next to the console, so running the server there is often ideal.

## Build the packaged app

[Nuitka](https://nuitka.net) bundles the launcher and all three servers into one
executable per OS — no Python install required for the end user:

```sh
python -m pip install -r requirements-build.txt
python build/build.py            # -> dist/PS2Servers(.exe)
```

## Release verification

Automatic releases (built on every push to `main`) include `SHA256SUMS.txt`, a
portable source ZIP, and GitHub artifact attestations for the packaged assets.
Tagged `vX.Y.Z` releases include a per-asset `<asset>.sha256.txt` checksum file
plus attestations. Example verification (automatic-release assets):

```sh
sha256sum -c SHA256SUMS.txt
gh attestation verify PS2Servers-windows-x64.zip -R NathanNeurotic/PS2-Servers
```

Checksums prove the file was downloaded intact. Attestations prove build
provenance. Neither is a magic safety certificate. Because the app is plain
Python and `build/build.py` rebuilds the release from source (single-file or
`PS2_BUILD_MODE=standalone` folder build), the lowest-trust path is to inspect
the source and build/run it yourself — see
[docs/antivirus-transparency.md](docs/antivirus-transparency.md) for the full
verification, "build it yourself", and false-positive-reporting guide.

## Status

The UDPBD port is validated by `udpbd_server/selftest.py` at the protocol level
(INFO/READ/WRITE byte-for-byte). As with the SMBv1 server, **final validation is on
real hardware** — an actual PS2 running OPL (or another network loader), or PCSX2
with a network adapter.

## Legal & responsible use

Please read this before using PS2 Servers.

- **Your content, your responsibility.** PS2 Servers is a general-purpose file
  server. It ships **no games and no copyrighted content** — it only serves files
  *you* point it at, from *your* PC. You are solely responsible for ensuring you
  have the legal right to use, copy, and serve any games, disc images, saves, or
  other files, and for complying with the laws of your jurisdiction. This project
  does not condone or facilitate copyright infringement; the intended use is with
  homebrew and with backups of media you legally own.
- **Not affiliated with Sony.** "PlayStation", "PlayStation 2", and "PS2" are
  trademarks of Sony Interactive Entertainment. This is an independent, unofficial
  fan/homebrew project with no affiliation, sponsorship, or endorsement from Sony,
  the Open PS2 Loader team, or any other rights holder. See [`NOTICE.md`](NOTICE.md).
- **Trusted networks only.** The servers are unauthenticated (guest) and designed
  for a private home LAN. Do not expose them to the internet or run them on
  untrusted networks.
- **Data-loss risk.** Writable modes let the PlayStation 2 write to the folders and
  disc images you share (saves, VMC). Keep backups; use `--read-only` if you want a
  strictly read-only share.
- **No warranty.** PS2 Servers is provided **"as is", without warranty of any
  kind**, and the authors' liability is limited, as set out in the Academic Free
  License 3.0 (see §7 "Disclaimer of Warranty" and §8 "Limitation of Liability" in
  [`LICENSE`](LICENSE)). You use it at your own risk.

## License and notices

PS2 Servers is licensed under the **Academic Free License 3.0 (AFL-3.0)**. See
[`LICENSE`](LICENSE).

This repository also includes third-party notices and provenance details in
[`NOTICE.md`](NOTICE.md), including the redistributed Neutrino UDPFS server,
UDPBD protocol references, optional compression libraries, build tooling, and
trademark notes.

## Credits & thanks

This is a fan project that stands entirely on the shoulders of the PS2 homebrew
community. None of the clever parts are ours — we just wrapped brilliant existing
work in something click‑and‑go. With genuine gratitude:

- **Rick Gaiser — [@rickgaiser](https://github.com/rickgaiser)** — the heart of all
  of this. He designed the **UDPBD** and **UDPFS** network protocols and wrote the
  original servers, alongside **[Neutrino](https://github.com/rickgaiser/neutrino)**.
  [`udpfs_server/udpfs_server.py`](udpfs_server/udpfs_server.py) is his UDPFS server
  (from Neutrino's `pc/` host tools), and
  [`udpbd_server/udpbd_server.py`](udpbd_server/udpbd_server.py) is our independent
  Python re‑implementation of his UDPBD v2 protocol. The network game‑loading here
  simply does not exist without his work — thank you.
- **pcm720 — [@pcm720](https://github.com/pcm720)** — a core Neutrino contributor and
  author of **[udpfsd](https://github.com/pcm720/udpfsd)**, a UDPFS server written in
  Go. If you'd rather not run our Python UDPFS server — e.g. on a low‑end device, or
  to sidestep the antivirus heuristics that hit unsigned packaged Python builds —
  udpfsd is an excellent no‑Python alternative: one standalone binary, prebuilt for
  Windows, macOS, and low‑end ARM/MIPS targets, with the same transparent CHD/CSO/ZSO
  decompression. Thanks as well for generously taking the time to vet the
  network‑boot docs. 🙏
- **El_isra — [@israpps](https://github.com/israpps)** — maintains the canonical
  **[udpbd-server](https://github.com/israpps/udpbd-server)** on GitHub (Rick's code,
  with CI), which is the reference we ported from.
- **Alex Parrado** — the Windows port of udpbd-server.
- **[Open PS2 Loader](https://github.com/ps2homebrew/Open-PS2-Loader)** and the
  **[ps2homebrew](https://github.com/ps2homebrew)** team — the loader everything here
  serves, and the wider toolchain that makes PS2 homebrew possible.
- **[prodeveloper0/pyudpbd](https://github.com/prodeveloper0/pyudpbd)** — a pure‑Python
  UDPBD port we read while writing our own.
- The folks behind **CHD ([libchdr](https://github.com/rtissera/libchdr) / MAME)**,
  **CSO**, and **ZSO** — the compressed‑image formats UDPFS decompresses on the fly.

### What's original here

The **GUI launcher**, the **RiptOPL** SMBv1 server, and the **pure‑Python UDPBD port**
were written for this repo. Everything at the protocol level is the community's —
we reimplemented from public protocols/source (rather than copying code) where we
could, and tried to attribute accurately.

### To the authors above 🙏

This exists out of appreciation for what you've given the PS2 scene, not any sense of
ownership. If you'd like attribution changed, a link corrected, or your work removed
from this repo entirely, please [open an issue](../../issues) — we'll sort it out
right away, no questions asked. Thank you, sincerely.
