# PS2-Servers

Network servers for loading PlayStation 2 games and apps over a LAN with
[Open PS2 Loader](https://github.com/ps2homebrew/Open-PS2-Loader) (OPL) and forks.
Three independent servers, pick whichever transport your setup uses:

| Folder | Server | What it is |
|--------|--------|------------|
| [`smbv1_server/`](smbv1_server/) | **RiptOPL SMBv1 server** | A tiny, dependency-free SMBv1/CIFS server so OPL's SMB game-loading keeps working even on Windows 11 hosts where the OS has removed SMB1/NTLMv1. Pure Python 3 stdlib. |
| [`udpbd_server/`](udpbd_server/) | **UDPBD server** | Prebuilt `udpbd-server.exe` — serves a block device to OPL over the UDPBD protocol. |
| [`udpfs_server/`](udpfs_server/) | **UDPFS server** | Python UDPFS server with on-the-fly handlers for compressed ISO formats (CHD / CSO / ZSO) under [`compressed_iso/`](udpfs_server/compressed_iso/). |

See each folder's own README / source for usage details. The SMBv1 server's
[README](smbv1_server/README.md) is the most complete walkthrough.
