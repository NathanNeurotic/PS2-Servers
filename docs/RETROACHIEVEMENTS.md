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
The managed service does not automatically open the upstream-derived account
window; use **Open achievement account (shared engine)** when you want to view
or configure it.

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
3. For Caduceus, use **Use shared games folder (Caduceus)** to reuse the
   configured SMB/UDPFS/HTTP/virtual exFAT root, or browse to the folder
   directly. Running server roots take precedence over edited settings. If
   multiple distinct roots exist, select the correct one manually; PS2-Servers
   will not guess or change a running RetroAchievements session. Once started,
   PS2-Servers creates `ART/CADUCEUS.KEY` for pairing and may add small
   `ART/<hash>_RA.png` icons. Share this same folder through your usual game
   server. Treat the pairing file as access to the account-progress browser;
   remove it and restart to revoke and replace the capability.
4. Start RetroAchievements and use **Open achievement account (shared engine)**. Sign in
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
PS2 CD/DVD images and PS1 POPS VCDs, editing catalogue metadata, importing local images, downloading
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

**Check RA compatibility** hashes PS2 ISOs using the pinned rcheevos PS2
implementation. For POPStarter `POPS/*.VCD`, PS2-Servers reads the CD user
data directly and uses the PS1 executable hash algorithm matching the RiptOPL
PS1 integration (without extracting a VCD). The catalogue queries both
PlayStation 1 (system 12) and PlayStation 2 (system 21) achievement hashes.
Sign in and enter your Web API key first. Hashes are cached against file size
and timestamps; the remote index refreshes daily and a prior PS2-only cache
is refreshed before PS1 checks. Either API request failing leaves the old
catalogue intact rather than incorrectly reporting unsupported games.
Compressed-image compatibility scans are not yet implemented.

VCDs are imported to `POPS/`, never `CD/` or `DVD/`. PS1 artwork identity is
the VCD filename, not the PS2 serial; importing a VCD does not alter existing
custom artwork, POPStarter modules or game contents. Only valid ISO9660 VCDs
are accepted. PS1 telemetry remains dependent on the matching RA-enabled
console loader and its supported USB POPStarter launch conditions.

Discord Rich Presence defaults to **server modes only**. Under
**About → Desktop settings**, opt into **Share game title during verified
RetroAchievements sessions** if you want Discord to display your currently
tracked PS1/PS2 title. A session only qualifies after the engine confirms a
matching game serial and advancing memory packets. It falls back to server
modes when RA is stopped, stale or untracked; raw paths, IP addresses,
credentials and account identities are never supplied to Discord. The optional
activity timer follows the verified game session while one is playing.

The RetroAchievements tab now shows the live console game when the managed
engine's packet counters actually advance. It shows a stalled connection when
packets stop for 15 seconds and distinguishes an unreachable engine or a
console awaiting tracking. Session polling runs outside the GUI thread and
does not query the game's storage backend or touch game-serving hot paths.
This is passive **RA telemetry** detection, not yet a general SMB/UDPFS
active-game monitor.

Set **OBS export folder** to write the upstream live text labels and
`data.json` for OBS. Leave it empty to disable file export. The folder is
created when the service starts; it should be writable and dedicated to these
labels.

## Native read-only live overview

The RetroAchievements card also offers **Live achievements (native view)**.
This optional Tk window reads the managed engine's local status from a
background worker with bounded responses; it displays account sign-in state,
connection packet/frame/loss counters, the loaded achievement set, measured
progress, recent engine unlocks, and active leaderboard trackers. It does not
show a previously checked set as a *currently playing* game: the game must
match the separately verified, advancing console telemetry session.
No RA credentials, unlock submissions or game-server filesystem reads are
performed in the native overview. The upstream-derived account page remains
available for login, editing account settings and deeper library/board views.
This is the **first native monitoring surface**, not full UI/engine parity.

Discord game-title sharing is separately opt-in under Desktop settings; the
default shares only server types. Live game names appear only for verified
RA sessions, and are cleared on disconnection, stalled telemetry or opt-out.
Caduceus has a manual **Use shared games folder** shortcut which suggests
the active server's game root and refuses to guess between distinct roots.

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
