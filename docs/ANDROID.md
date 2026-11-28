# PS2 Servers for Android

PS2 Servers for Android (`PS2Servers-android.apk`) is a native Kotlin app for
Android 5.0 (API 21) and newer phones or tablets. It lets a PlayStation 2 load
games from your device over home Wi-Fi, a phone Wi-Fi Hotspot, or USB-Ethernet
tethering, and can share the phone's Mobile Data connection for PS2 online play.

> **Validation scope:** All seven protocol engines, storage providers, and NAT
> helper commands are unit-tested on a JVM host and lint-checked for API 21+.
> Real Android hardware and physical PlayStation 2 testing have not run yet.

## Download & verify

Download **`PS2Servers-android.apk`** from
[GitHub Releases](https://github.com/NathanNeurotic/PS2-Servers/releases)
(both tagged releases and rolling `main` pre-releases attach it). One universal
APK covers ARM64, ARMv7, x86, and x86_64 Android 5.0+ devices with no runtime
downloads or telemetry.

Verify integrity against the release's checksums and provenance attestation:

```sh
sha256sum -c SHA256SUMS.txt --ignore-missing
gh attestation verify PS2Servers-android.apk -R NathanNeurotic/PS2-Servers
```

To install, copy `PS2Servers-android.apk` to the device and open it (or run
`adb install -r PS2Servers-android.apk` over USB debugging).

## Shared games folder

1. Open **PS2 Servers** and grant the notification prompt (Android 13+) so the
   foreground status bar stays visible while playing.
2. Tap **Choose Folder (SAF)** and select the parent folder containing your
   uppercase `DVD/` and `CD/` subfolders.
3. Alternatively, enter a direct directory path or tap **Use App Folder** to
   use `<app-external-files>/PS2Games`.

Compression & image formats on Android:

- **ISO / CSO / ZSO:** served natively, with transparent CSO/ZSO `.iso`
  decompression enabled by default for UDPFS and HTTP (optional for SMBv1).
- **CHD:** not bundled in the Android APK; use Desktop or Core for `.chd`.
- **UDPBD:** serves your chosen Games Folder as a **Virtual exFAT** drive
  automatically, or a single raw `.iso`/`.img` file selected via SAF or path.

## Supported servers & port 445

| Server | Protocol | Default Port | PS2 Client Targets |
| --- | --- | --- | --- |
| UDPFS | UDP | `62966` (`0xF5F6`) | NHDDL, Neutrino, RiptOPL (`Auto` / `Standard` / `Modulo`) |
| SMBv1 | TCP | `1025` | OPL, POPStarter, Launchstation (`share`: `games`, `user`: `guest`) |
| SMBv2 | TCP | `1445` | Neutrino `usmb2`, RiptOPL, Launchstation (dialect `2.0.2`–`2.1`) |
| SMBv3 | TCP | `1445` | Neutrino `bsd-smb`, Windows 11 clients (dialect `3.0.2`, `IPC$` `srvsvc`) |
| HTTP | TCP | `1100` | OPL-HTTP (`legacy-flat`) and RiptOPL (`riptopl-path-aware`) |
| NFSv4 | TCP | `2049` | `unfs4` (Neutrino, RiptOPL, Launchstation `nfs:`) |
| UDPBD | UDP | `48573` (`0xBDBD`) | OPL and Neutrino UDPBD (`Virtual exFAT` or raw `.iso`/`.img`) |

Android prevents unprivileged apps from binding TCP port `445`.

- **Unrooted devices:** keep `SMBv1` on `1025` and `SMBv2`/`SMBv3` on `1445` and
  set that custom port in OPL/RiptOPL/Launchstation, or use `UDPFS` (`62966`),
  `HTTP` (`1100`), `NFSv4` (`2049`), or `UDPBD` (`48573`).
- **Rooted devices:** open **Settings** on an SMB card and check **Redirect TCP
  445 -> Port (requires root)**. Starting the server installs an `iptables` NAT
  `REDIRECT` from TCP `445` to the unprivileged bound port and removes it on
  stop.

## Home Wi-Fi LAN & background locks

When your phone and PS2 share the same home Wi-Fi router (disable AP/Client/Guest
isolation so Wi-Fi and wired LAN ports can reach each other and pass UDP
broadcasts), `NetworkAddressHelper` surfaces your LAN IPv4 address. Starting any
server runs `Ps2ServerService` in the foreground with a persistent notification,
`PARTIAL_WAKE_LOCK`, `WifiLock`, and `MulticastLock` (for `UDPFS`/`UDPBD`
discovery).

## Connecting the PS2 to Mobile Data internet

Use the **PS2 Internet (Mobile Data)** card to give the PS2 internet access over
your phone's cellular connection (DNAS / community DNS / online multiplayer) while
serving games at the same time:

1. **Unrooted Android 11+ (USB-Ethernet tethering):** Connect a USB-Ethernet
   adapter between your phone and the PS2 Ethernet port, tap **Open Hotspot &
   Tethering**, and turn on **Ethernet tethering**. In PS2 Network Configuration
   (`NETCNF`), pick **Auto (DHCP)** — or enter the static IP, Subnet Mask,
   Gateway, and Primary DNS printed in the **PS2 Internet (Mobile Data)** card.
2. **Unrooted Android 5.0+ (Wi-Fi Hotspot + bridge):** Enable Android's **Wi-Fi
   Hotspot**, connect a travel router or Wi-Fi-to-Ethernet bridge wired to the
   PS2, and configure PS2 `NETCNF` using the card's DHCP or Static guidance.
3. **Custom PS2 Online DNS (optional):** Type a community revival DNS IPv4 into
   **Custom PS2 DNS** so the `NETCNF` summary and optional NAT rule route DNS
   lookups there while keeping local LAN traffic on your USB/hotspot subnet.
4. **Rooted Android (Android 5–10 USB-Ethernet OTG or forced DNS):** Toggle
   **Root NAT Bridge** to enable kernel IPv4 forwarding, `iptables MASQUERADE`
   out the active cellular interface (`rmnet*`/`ccmni*`), `FORWARD` rules, policy
   routing, and optional TCP/UDP port `53` `DNAT` redirection to your custom DNS.
