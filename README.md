
<p align="center"> <img width="1774" height="887" alt="PS2-Servers" src="https://github.com/user-attachments/assets/ffb65171-64ae-42bf-83af-7215aa5f7441" /><br>
  <img width="400" height="92" alt="AI-Assisted-Software-Development" src="https://github.com/user-attachments/assets/68f0cac3-b256-4117-b344-39cf07f8b5d0" /><br>
</p>

# PS2-Servers

*in collaboration with https://github.com/FatBaldDad/PS2-EtherDrive*


PS2 Servers runs game and file servers for network-capable PlayStation 2
homebrew. **Desktop** provides a Tkinter launcher; **Core** runs the same Python
servers from a terminal; **Edge** is a separate Go server for routers, NAS,
Raspberry Pi, and headless computers.

Choose the protocol your loader supports. A server mode being available here
does not add that protocol to a PS2 application.

| Mode | Default port | Use |
|---|---|---|
| UDPFS | UDP 62966 (`0xF5F6`), plus an automatic data port | File and optional block-image serving for UDPFS clients such as NHDDL/Neutrino and RiptOPL; automatic Standard/Modulo negotiation |
| SMBv1 | TCP 1025 in Desktop; 1111 in Core/standalone | Guest file sharing for OPL and other SMBv1 clients |
| SMBv2 / SMBv3 | TCP 1445 | Authenticated file sharing for clients that support these dialects; not a replacement for SMBv1 in an SMBv1-only loader |
| HTTP | TCP 1100 | Experimental game streaming for compatible OPL HTTP clients; see [HTTP setup and validation](docs/HTTP.md) |
| UDPBD | UDP 48573 (`0xBDBD`) | One disk image served as a block device for UDPBD clients |

Desktop has six server cards. Edge provides UDPFS, SMBv1, UDPBD, and a separate
HTTP **management dashboard**; it does not provide the HTTP game server or SMB2/3.
See [editions](docs/EDITIONS.md) and [Edge setup](docs/EDGE.md).

## Quick start

1. Download from [GitHub Releases](https://github.com/NathanNeurotic/PS2-Servers/releases).
   Use the newest **pre-release** for current development builds; a tagged release
   may be older. Read that release's download table for the assets actually built.
2. Extract the Desktop package and launch `PS2Servers.exe` on Windows, the
   executable on Linux, or the `.app` on macOS. Keep a portable build's files together.
3. Select your server and games folder. For file serving, choose the parent of
   `DVD/` and `CD/`, for example `D:/PS2Games`, not an individual ISO.
4. Check that the selected **LAN IP** belongs to the PC interface connected to
   the PS2. Click **Start**, then use the displayed address, port, and share in
   your loader. The LAN IP selector supplies setup hints; it does not bind servers.

For SMBv1, use address type **IP**, the PC's LAN IP, the displayed port (normally
1025 in Desktop), share `games`, user `guest`, and an empty password. UDPFS and UDPBD require
a loader build with that protocol; discovery does not configure the PS2's own IP.
HTTP requires an explicit matching port on the console; follow [the HTTP guide](docs/HTTP.md).

Start with a known-good plain ISO. Compressed image support and writes depend on
the chosen protocol and loader. HTTP is read-only; enabling writes in a server
does not add VMC support to a loader that lacks it.

### Downloads and requirements

| System | Desktop download |
|---|---|
| Windows x64 | `PS2Servers-windows-x64-portable.zip` (recommended), or `PS2Servers-windows-x64.zip` |
| Windows x86 (32-bit OS) | `PS2Servers-windows-x86-portable.zip`, or `PS2Servers-windows-x86.zip` |
| Linux x64 | `PS2Servers-linux-x64`; use `PS2Servers-linux-x64-portable.tar.gz` when `/tmp` is `noexec` |
| Linux desktop integration | `PS2Servers-x86_64.AppImage`, when present in the release |
| macOS Apple Silicon / Intel | `PS2Servers-macos-arm64.zip` / `PS2Servers-macos-x64.zip` |
| ARM/MIPS/router/headless systems | See the [Edge build picker](docs/EDGE-WHICH-BUILD.md), or run Core with Python |

Packaged Desktop includes Python; a separate Python installation is unnecessary.
Source builds use Python 3.12 in CI. The GUI needs Tkinter (for example
`python3-tk` on Debian/Ubuntu); Core does not need a display. Both computers need
compatible IP settings and a working Ethernet path to the PS2; a wired PC is
preferable to Wi-Fi. Fat PS2s require an Ethernet-capable network adapter.

Portable builds avoid the single-file self-extraction wrapper. They are still
unsigned and **can be flagged by antivirus**. See [the antivirus notice](ANTIVIRUS-NOTICE.md).
Portable/AppImage availability depends on the release's successful build outputs.

### From source

Run `Start-Launcher.bat` on Windows or `./start-launcher.sh` on Linux/macOS.
You can also run the GUI directly:

```sh
python ps2servers.py
```

Core uses the same server implementations and requires no desktop session:

```sh
python ps2servers.py --list
python ps2servers.py serve udpfs --root-dir /srv/ps2
python ps2servers.py serve smbv1 --share games=/srv/ps2 --port 1111
python ps2servers.py serve http --root-dir /srv/ps2 --port 1100
python ps2servers.py serve udpbd /srv/images/ps2.img --read-only
python ps2servers.py serve udpfs --help
```

Replace paths with your own and quote paths containing spaces. A packaged
executable accepts the same `serve` commands. `--serve` remains an alias.
Use this entry point for UDPFS to get the same automatic compatibility engine
as Desktop; `udpfs_server/udpfs_server.py` is the underlying legacy engine.

## UDPFS: games list but fail to launch

On PCs with multiple network interfaces, automatic routing may select a reply
address that the PS2 cannot use. Explicit data binding is also the workaround
documented by [upstream udpfsd](https://github.com/pcm720/udpfsd/blob/v0.1.7/README.md#troubleshooting).

1. Select the **PC's IP connected to the PS2** in the launcher's LAN IP box.
2. On the UDPFS card, click **Use LAN IP** beside **Bind address (PC)**, or type
   that PC address directly. Do not enter the PS2's address or `0.0.0.0`.
3. **Stop and start UDPFS**, then try launching the game again.

The button copies and saves the address at that moment. Changing the LAN IP later
does not change a saved bind; repeat the step if your PC address changes. Blank
bind restores automatic behavior after a stop/start.

Equivalent Core command (replace the example PC address):

```sh
python ps2servers.py serve udpfs --root-dir /srv/ps2 --bind 192.168.0.3
```

This binds Core's **data socket**; discovery still listens on all interfaces.
It selects an address, not a fixed port. Use **Data port** separately if a manual
firewall requires one. In Core single-port/forced-Modulo operation there is no
separate data socket, so this data-address setting does not pin discovery.

A log prefix such as `[192.168.0.58:62966]` identifies the **client** endpoint.
A later `.1.10` prefix means packets arrived from that source; it does not show
the server changing its own bind address. Repeated discovery or `seq=0` alone
does not establish the cause of a launch failure. Check the launched loader's
network settings if its source IP changes.

### Protocol mode, ports, and idle timeout

- **Protocol mode: Auto** is the default. It negotiates Standard and Modulo per
  peer. Leave **Enforce Modulo mode** unticked unless explicitly testing Modulo;
  that override forces Core's legacy single-port Modulo behavior. A forced mode
  is not the fix for a wrong network interface.
- **Port** defaults to UDP 62966. In normal two-port mode, the OS chooses one
  data port when the server starts. **Data port** can pin it, for example 62967;
  allow both ports on a firewall that uses explicit port rules. CLI
  `--single-port` uses discovery's port for data too.
- **Idle timeout** defaults to 3600 seconds and accepts 60–86400 seconds.
  Expiry closes that peer's handles, so long pauses can be affected. Core
  rejects `0`; it does not disable the timeout. CLI: `--peer-timeout SECONDS`.

See [protocol compatibility](docs/PROTOCOL-COMPATIBILITY.md) for the Core/Edge
differences and [handoff diagnostics](docs/UDPFS-HANDOFF-DIAGNOSTICS.md) for optional
deeper investigation after the simple setup checks.

## Direct cable and saved settings

For a dedicated PS2-to-PC cable, enable **PS2 is plugged directly into this PC**.
The helper selects a suitable interface, temporarily configures its address,
and offers DHCP to one console. This requires administrator/root permission.
Use the DIRECT tab's reported values; do not enable the helper on a router LAN.

Windows also attempts to accommodate a console using a static IP. Linux/macOS
direct-link setup is experimental and may require changing the console to DHCP
or a matching static address. A launcher and the Neutrino ELF it starts may load
different network configuration, so a successful menu browse does not prove the
game-launch configuration matches.

Disable direct link before removing the app so it can restore its temporary
configuration. On Linux/macOS a reboot clears the temporary address if cleanup
was interrupted. Read the DIRECT/TERMINAL output if restoration reports a failure.

Launcher settings are saved per user in `launcher.json`:

- Windows: `%APPDATA%/PS2-Servers/`
- Linux: `$XDG_CONFIG_HOME/ps2-servers/`, or `~/.config/ps2-servers/`
- macOS: `~/Library/Application Support/PS2-Servers/`

**Auto-start servers on launch** starts saved servers when the app opens; it
does not install an OS login task. Windows supports a tray icon; Linux tray support
is experimental and depends on the desktop/backend. macOS has no tray support.

## Compression

Desktop/Core UDPFS exposes supported CHD/CSO/ZSO images as virtual `.iso` files.
CSO uses the standard library, ZSO needs `lz4`, and CHD needs native `libchdr`.
Release builds bundle the optional libraries; use the **Compression support**
panel to check the running build. Missing libraries leave those UDPFS formats
unadvertised. Edge supports CSO/ZSO but not CHD. HTTP decodes CHD/CSO on the
server and passes ZSO through for the client to decode.

See [compression dependencies](docs/optional-compression-dependencies.md).

## Windows security and release verification

The SMBv1 server does not enable Windows' built-in SMB1 optional feature tree.
Desktop defaults to TCP 1025; Core/standalone and Edge default to TCP 1111.
See [SMB setup](docs/SMB.md) for dialects, ports, authentication and writes. SMB2/3 uses the
separate authenticated server, normally on TCP 1445.

Windows Firewall changes are limited to rules named `PS2 Servers - ...`.
Firewall actions, direct-link configuration, and optional **Take port 445**
require elevation. Take port 445 temporarily stops Windows File Sharing while
the server runs; default custom-port serving does not need administrator rights.

Automatic `main` releases include `SHA256SUMS.txt`, source archives, and GitHub
artifact attestations. Tagged releases provide per-asset `.sha256.txt` files;
Edge archives have adjacent `.sha256` files. Use the checksum attached to the
exact release you downloaded.

```sh
sha256sum -c SHA256SUMS.txt
gh attestation verify PS2Servers-windows-x64.zip -R NathanNeurotic/PS2-Servers
```

Checksums verify integrity; attestations verify provenance. Neither establishes
safety or PS2 compatibility. See [SECURITY.md](SECURITY.md) and
[docs/antivirus-transparency.md](docs/antivirus-transparency.md) for behavior,
verification, cleanup, and reporting.

## Build and validation

```sh
python -m pip install -r requirements-build.txt
python build/build.py
python -m unittest discover -s tests -t . -v
python tools/check_release_compliance.py
```

Nuitka packages all Desktop server modes. For Edge build/deployment commands,
use the [operator manual](docs/INSTRUCTIONS.md). CI covers host tests, protocol
conformance, and builds; see [the evidence definitions](conformance/README.md).
Reported success with Desktop/Core and a particular loader does not validate all
games, networks, or Edge. HTTP remains experimental; Edge's game launch/write
paths still require separate console results.

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
- **Trusted networks only.** SMBv1, UDPFS, UDPBD, and HTTP serving are unauthenticated and designed
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

PS2 Servers builds on the following projects and contributions:

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
- **Aerosol** — tested UDPFS across Linux, Windows, and direct connections, identified the
  explicit-bind workaround, and isolated a shared-config line-ending issue affecting VMC paths.
- **[Open PS2 Loader](https://github.com/ps2homebrew/Open-PS2-Loader)** and the
  **[ps2homebrew](https://github.com/ps2homebrew)** team — the loader everything here
  serves, and the wider toolchain that makes PS2 homebrew possible.
- **[prodeveloper0/pyudpbd](https://github.com/prodeveloper0/pyudpbd)** — a pure‑Python
  UDPBD port we read while writing our own.
- The folks behind **CHD ([libchdr](https://github.com/rtissera/libchdr) / MAME)**,
  **CSO**, and **ZSO** — the compressed‑image formats UDPFS decompresses on the fly.
- **v_txl** (Discord) — for dedicated testing and hardware validation of Modulo UDPFS mode and direct ethernet link connectivity.

### What's original here

The **GUI launcher**, the **SMBv1** server, and the **pure‑Python UDPBD port**
were written for this repo. Everything at the protocol level is the community's —
we reimplemented from public protocols/source (rather than copying code) where we
could, and tried to attribute accurately.
