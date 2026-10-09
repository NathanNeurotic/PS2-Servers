# RetroAchievements

PS2-Servers can run its bundled xeRAbora/rcheevos achievement engine alongside
SMB, UDPFS, HTTP or UDPBD. No separate xeRAbora or Caduceus desktop installation
is required. This is experimental, softcore-only support for a real PS2 running
an achievements-enabled OPL fork. Ordinary OPL builds do not send the required
memory snapshots.

## What the mode selector actually does

**Console mode is not a desktop engine selector.** Both xeRAbora and Caduceus
use one PS2-Servers-managed native executable, `ps2ra`, currently compiled from
a modified, MIT-licensed xeRAbora desktop client and rcheevos. The shared
account/library page also originates from xeRAbora. Selecting **Caduceus**
adds the independently implemented CADQ/CADA companion services and enriched
achievement notices; it does not launch the Caduceus Electron desktop app or
change the underlying achievement evaluator. The console must still use a
matching loader. Stop RetroAchievements before switching compatibility modes.

**A standalone PS2-Servers implementation of the telemetry receiver, RA client
and account UI does not exist yet.** The current code is integration and
adaptation, not an independently implemented achievement runtime. See
[the parity checklist](RETROACHIEVEMENTS-PARITY.md) for the missing architectural
work and validation limits. Upstream licenses and provenance are preserved.

## Setup

1. Use the achievements-enabled console loader from
   [xeRAbora](https://github.com/hacan359/xerabora) or
   [Caduceus OPL](https://github.com/Rian6/caduceus-opl). The desktop mode does not
   add achievement support to an unmodified console loader.
2. Open **RetroAchievements** in PS2-Servers. Select **xeRAbora** or **Caduceus**
   to match the loader. Both use the same telemetry engine; Caduceus mode also
   enables its companion protocols described below.
3. For Caduceus, select the existing OPL games folder shared with the console.
   PS2-Servers creates `ART/CADUCEUS.KEY` for pairing and may add small
   `ART/<hash>_RA.png` icons. Share this same folder through your usual game
   server. Treat the pairing file as access to the account-progress browser;
   remove it and restart to revoke and replace the capability.
4. Start RetroAchievements and use **Open account and achievements**. Sign in
   on the local page, then enter the Web API key from your RA account settings
   for the library, progress and leaderboard views. The password is used for
   login and is not saved. Sign out on that page to remove the saved login token.
5. On the console, configure the PC's LAN address, test the RA connection, and
   run the loader's game-support check before launching. An identified image
   and a prepared watch list are necessary for tracking. Having a game title
   in RA's catalogue does not establish that a particular ISO hash is supported.

Close other achievement clients first: only one receiver can own UDP 18194.
Starting a second copy fails explicitly. The account page prefers TCP 18196 but
selects a free local port when necessary, including immediate Windows restarts.
Use the account button or the URL printed in the terminal to reach it. Closing the account page leaves the
service running; stop it with the RetroAchievements tab or **Stop all**.

If sharing that folder through UDPBD virtual exFAT, start RetroAchievements
first so the pairing file is included in the virtual disk's frozen inventory.
Restart the virtual disk server after adding pairing files or downloaded
artwork; it does not pick up new files while running.

Upstream Caduceus documents an SMB loading problem in the console loader.
Start physical validation with a supported USB/disc launch. Sharing games and
receiving RA telemetry are separate functions; PS2-Servers does not repair the
loader's networking implementation or claim that all game transports work.

## Modes and ports

| Function | xeRAbora | Caduceus | Inbound PC port |
| --- | --- | --- | --- |
| Discovery, game hash, watch list, memory snapshots, unlock evaluation | Yes | Yes | UDP 18194 |
| Local account, live achievements, library and leaderboards | Yes | Yes | TCP 18196 when available, otherwise a free loopback port |
| `CADQ1` / `CADQ2` compatibility and session-readiness replies | No | Yes | UDP 18197 |
| Paired `CADA1` account library and achievement pages | No | Yes | UDP 18198 |
| Enriched `RAU1` achievement-title notices | No | Yes | Sent to console UDP 18195 |

The account page is never opened to the LAN. **View achievements on other
devices** optionally enables a separate read-only viewer at
`http://<PC LAN IP>:18199/`. Devices on that network can view your profile and
progress; login, logout, settings and shutdown requests remain local-only.
Foreign browser origins and non-IP Host headers are rejected. This viewer is
off by default. Windows setup adds the relevant
UDP port rules; Linux users should allow the corresponding UDP ports on their
trusted LAN. RA service calls use outbound HTTPS. Caduceus replies are padded
to the console's 64-byte DMA alignment, at least 128 bytes and at most 960 bytes.
The companion browser uses three entries per page and supports earned, locked
and hardcore-history filters. New unlocks remain softcore in both modes.

The current adapter follows upstream's single-active-console behavior.
Compatibility failures remain `UNKNOWN` when an API request fails, rather than
being cached as an unsupported image. A first uncached check may require retrying
after the asynchronous lookup completes. API work is bounded and runs outside
the engine's telemetry loop. Account requests require the share pairing key;
the console never receives an RA login token, password or Web API key.

## Desktop library and streaming

**Manage game library** opens a native desktop window for searching installed
CD/DVD images, editing catalogue metadata, importing local images, downloading
user-provided image links, importing Caduceus JSON/SQLite catalogues and making
catalogue backups. Existing catalogue records are preserved during import, and
a database snapshot is saved first. Backups contain catalogue metadata, not
game images, covers, credentials or saves. Catalogue deletion never removes
game files.

Choose the same OPL folder as the game-server tab. Imports and downloads stage
files with a `.part` suffix, publish only complete images, and refuse to replace
an existing destination. Cancelled and failed transfers remove their partial
file. Selecting a different library folder does not move existing files or
change a running game server's configuration.

**Check RA compatibility** hashes ISO images using the pinned rcheevos PS2 hash
implementation and compares them with the RA hash catalogue. Sign in and enter
your Web API key first. Hashes are cached against file size and timestamps;
the remote catalogue refreshes daily. API errors do not become unsupported
results. Compressed-image scanning is not yet implemented.

Set **OBS export folder** to write the upstream live text labels and
`data.json` for OBS. Leave it empty to disable file export. The folder is
created when the service starts; it should be writable and dedicated to these
labels.

## Credentials and packaging

The private profile lives under `%LOCALAPPDATA%/PS2-Servers/retroachievements`
on Windows and under the normal PS2-Servers configuration directory elsewhere.
It is separate from independent xeRAbora/Caduceus profiles and `launcher.json`.
Windows protects login tokens and Web API keys with user-bound DPAPI. POSIX
profile directories and secret files are created with modes 0700 and 0600.
Credentials are not passed to the managed engine on its command line.

Packaged desktop builds include the native engine, rcheevos and their license
notices. Source users first run `python build/build_achievements.py`; Linux
needs a C compiler and libcurl development headers, macOS uses the system
compiler/libcurl, and Windows builds use a checksum-verified Zig 0.13.0 toolchain
for x64 or x86. `RA_CC` can select a locally installed compiler explicitly.

The built executable is a supervised internal component. Users start one
PS2-Servers application; they do not install or separately manage another RA
desktop application. The engine exits if its supervising process disappears.

## Source provenance and validation limits

Pinned revisions and licenses are in
[`vendor/retroachievements`](../vendor/retroachievements/README.md). The engine
uses the original rcheevos evaluator and RA submission implementation, rather
than a new Python implementation of achievement conditions. The Caduceus
companion protocols are independently implemented from the upstream wire
contracts; the Caduceus Electron application and console ELF are not bundled.

Host tests cover discovery, DMA padding, the private profile, pairing rejection,
account wire layout/filtering, logout during a request, and failure handling.
They do not prove real-console memory correctness, legitimate RA unlocks,
in-game notification rendering, or compatibility with every loader revision.
Validate those on the intended console, loader, game image and storage path.
