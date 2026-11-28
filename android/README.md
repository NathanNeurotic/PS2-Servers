# PS2 Servers for Android

Native Kotlin Android implementation (Android 5.0 / API 21+) of the network servers from [PS2-Servers](../README.md), letting a PlayStation 2 load games from a phone or tablet over Wi-Fi or USB-Ethernet tethering.

## Implemented Servers & Default Ports

| Server | Protocol | Default Port | Notes |
| --- | --- | --- | --- |
| UDPFS | UDP | `62966` (`0xF5F6`) | OPL / Neutrino UDPFS (`auto`, `standard`, `modulo`), CSO/ZSO decompression, router status (`0xF5F7`) |
| SMBv1 | TCP | `1025` | OPL / POPStarter / Launchstation direct-TCP SMB1, RAP `NetShareEnum`, capped TRANS2 `FIND_FIRST2`/`FIND_NEXT2` |
| SMBv2 | TCP | `1445` | Neutrino `usmb2` / RiptOPL / Launchstation SMB `2.0.2`–`2.1` |
| SMBv3 | TCP | `1445` | Neutrino `bsd-smb` / Windows 11 compatible SMB `3.0.2` (`IPC$` `srvsvc`) |
| HTTP | TCP | `1100` | OPL `legacy-flat` and RiptOPL `riptopl-path-aware` (`games.csv` + `Range` 206) |
| NFSv4 | TCP | `2049` | `unfs4` (Neutrino / `nfsman` / RiptOPL / Launchstation) ONC RPC NFSv4.0 |
| UDPBD | UDP | `48573` (`0xBDBD`) | Virtual exFAT from the shared folder, or raw `.iso`/`.img` disk image |

## Port 445 on Unrooted Android

Android prevents unprivileged apps from binding TCP ports below `1024`, including standard SMB port `445`.

- **Unrooted devices:** SMBv1 defaults to port `1025`, and SMBv2/SMBv3 default to port `1445`. Configure the custom port in OPL/RiptOPL/Launchstation when supported by your loader build.
- **Rooted devices:** Each SMB card provides a `Redirect TCP 445 -> Port (requires root)` toggle that runs `iptables -t nat -A PREROUTING -p tcp --dport 445 -j REDIRECT --to-ports <port>` while the SMB server is active and removes the rule on stop.

## Shared Storage Setup

1. Tap **Choose Folder (SAF)** to grant persistent Access Framework permission (`ACTION_OPEN_DOCUMENT_TREE`) to your `PS2SMB` / `DVD` / `CD` folder on internal storage or an SD card.
2. Or enter a direct filesystem path in **Fallback path** (or tap **Use App Folder** to serve `<app-external-files>/PS2Games`).

## Background Stability

Starting any server promotes `Ps2ServerService` to a foreground service with a persistent notification and holds a `PARTIAL_WAKE_LOCK` plus a `WifiLock` (`WIFI_MODE_FULL_LOW_LATENCY` on API 29+, `WIFI_MODE_FULL_HIGH_PERF` on API 21–28). `MulticastLock` is acquired while `UDPFS` or `UDPBD` is active.

## Build & Host Tests

From `android/`:

```bash
./gradlew testDebugUnitTest lintDebug assembleDebug
```

Outputs the debug APK to `android/app/build/outputs/apk/debug/app-debug.apk`.
