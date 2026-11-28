# PS2 Servers editions

PS2 Servers is one product family with four deployment shapes.

## PS2 Servers Desktop

Use Desktop on Windows, macOS, or a normal Linux workstation. It includes the
GUI launcher, SMBv1, SMB2/3, UDPFS, HTTP, NFS, UDPBD, compression support, logging, diagnostics,
Windows Firewall assistance, and the established release packaging. Desktop is
the most feature-complete edition and retains optional CHD support where the
required library is available.

## PS2 Servers Core

Core runs the Desktop server implementations without opening the GUI. It is
useful for terminals, services, containers, remote systems, and automation while
retaining the Python implementation's full feature set.

```text
ps2servers serve udpfs --root-dir /games --read-only
ps2servers serve udpbd /images/ps2.img --read-only
ps2servers serve smbv1 --share games=/games --read-only
ps2servers serve http --root-dir /games --port 1100
ps2servers serve nfs --root-dir /games
```

Here `ps2servers` means the packaged executable, optionally installed under that
name; from source use `python ps2servers.py`. The internal `--serve` spelling remains supported for existing packaged
re-execution and scripts.

## PS2 Servers Edge

Use Edge on OpenWrt, Raspberry Pi, NAS, embedded Linux, old x86 systems, ARM or
MIPS boards, and minimal headless Linux installations. Edge is a small,
statically linked Go executable and does not require Python or the desktop GUI.

Current Edge scope:

- UDPFS with reads, writes, `MKDIR`, `REMOVE` and `RMDIR`; writable by default,
  `--read-only` to restrict
- UDPFS block access (`BREAD`/`BWRITE`) for one disk image via `--block-device`,
  and for files a console opened, as Neutrino's FILEID backend uses it
- guest SMBv1 as the `smb` subcommand (no SMB2/3)
- native UDPBD as the `udpbd` subcommand
- embedded responsive Web GUI management dashboard (`ps2servers-edge webui`)
- automatic per-session standard/Modulo compatibility
- multiple concurrent clients
- ISO, CSO, and ZSO, with a bounded decompressed-block cache
- periodic transfer metrics via `--metrics`
- text or JSON logs
- generic Linux, systemd, Docker, and OpenWrt deployment foundations

Edge does not implement the HTTP game server, NFS or SMB2/3. Its generic static
binaries also exclude CHD, which needs a native decoder such as `libchdr`.
Desktop/Core continue to provide CHD where that library is available.

## PS2 Servers Android

Use Android (`PS2Servers-android.apk`) on Android 5.0+ (API 21+) phones and
tablets to serve PS2 games over Wi-Fi, hotspot, or USB-Ethernet tethering, and
optionally share Mobile Data internet with your PS2. See [Android setup](ANDROID.md).

- Pure-Kotlin implementation of UDPFS, SMBv1, SMBv2/SMBv3, HTTP, NFSv4, and UDPBD
- Storage Access Framework (`ACTION_OPEN_DOCUMENT_TREE`) + fallback directory
- Foreground service with wake lock, low-latency Wi-Fi lock, and multicast lock
- CSO/ZSO decompression and Virtual exFAT UDPBD mode (no CHD decoder bundled)
- Unrooted high ports (`1025`/`1445`) plus optional root TCP `445` redirect and Mobile Data NAT bridge

## Selection guide

| Environment | Recommended edition |
|---|---|
| Windows desktop | Desktop |
| macOS desktop | Desktop |
| Linux desktop | Desktop |
| Android 5.0+ phone or tablet | Android (`PS2Servers-android.apk`) |
| Android serving + Mobile Data internet sharing | Android (`PS2Servers-android.apk`) |
| Linux server with Python | Core |
| Docker host | Edge for minimal UDPFS; Core for full Python features |
| Raspberry Pi | Edge |
| OpenWrt router | Edge |
| NAS or embedded Linux | Edge |
| Need CHD now | Desktop/Core |
| Need UDPBD | Any edition; validate the chosen build and loader on your console |
| Need HTTP game serving, NFS or SMB2/3 | Desktop, Core, or Android |
