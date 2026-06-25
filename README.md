# PS2-Servers

Network servers for loading PlayStation 2 games and apps over a LAN with
[Open PS2 Loader](https://github.com/ps2homebrew/Open-PS2-Loader) (OPL) and forks —
plus a small **GUI launcher** so anyone can run them without touching a terminal.

## Quick start — the launcher

The launcher lets you pick a server, choose your games folder, and click **Start**.
It detects your PC's LAN IP and shows the exact settings to enter in OPL.

- **Packaged app (no Python needed):** double-click **`PS2Servers`** (`.exe` on
  Windows) — see [Build the app](#build-the-single-file-app) to produce it.
- **From source:** double-click **`Start-Launcher.bat`** (Windows) or run
  `./start-launcher.sh` (Linux/macOS). Requires Python 3.

![one card per server: pick a folder, Start, and it shows the OPL settings]

## What's inside

All three servers are pure-Python (standard library) and run on Windows, Linux and macOS.

| Folder | Server | What it does |
|--------|--------|--------------|
| [`smbv1_server/`](smbv1_server/) | **SMBv1 (RiptOPL)** | Shares a games folder over SMB — works even on Windows 11 where the OS removed SMB1. |
| [`udpfs_server/`](udpfs_server/) | **UDPFS** | Serves a folder and/or disk image over UDP; can transparently decompress CHD/CSO/ZSO. |
| [`udpbd_server/`](udpbd_server/) | **UDPBD** | Serves a disk image as a block device over UDP; the PS2 auto-discovers it. |

`udpbd_server/udpbd_server.py` is a pure-Python port of Rick Gaiser's UDPBD server —
see [its provenance](udpbd_server/SOURCE.md). UDPBD has largely been superseded by UDPFS.

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

## Build the single-file app

[Nuitka](https://nuitka.net) bundles the launcher and all three servers into one
executable per OS — no Python install required for the end user:

```sh
python -m pip install nuitka
python build/build.py            # -> dist/PS2Servers(.exe)
```

## Status

The UDPBD port is validated by `udpbd_server/selftest.py` at the protocol level
(INFO/READ/WRITE byte-for-byte). As with the SMBv1 server, **final validation is on
real hardware** — an actual PS2 running OPL, or PCSX2 with a network adapter.
