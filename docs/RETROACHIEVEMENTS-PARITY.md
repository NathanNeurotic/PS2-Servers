# RetroAchievements feature parity

The goal is to replace the desktop responsibilities of xeRAbora and Caduceus
inside PS2-Servers, retaining the Python/Tk desktop app. Electron is not required.
Console-loader behavior and hardware validation remain separate from desktop
feature coverage. No claim of complete parity is made while this checklist has
unfinished items.

References are pinned in `vendor/retroachievements/sources.json`.

| Capability | Status | Acceptance |
| --- | --- | --- |
| Independent PS2-Servers RA runtime (no xeRAbora client executable or embedded page) | **Not implemented** | Own protocol receiver, snapshot assembly, watch-list lifecycle, rcheevos integration, account/RA API workflow and native/local UI. Keep upstream protocol and MIT dependency attribution. Confirm no bundled xeRAbora runtime remains. |
| True console-mode isolation | Protocol adapters only | One engine in both modes today; xeRAbora-compatible mode must not start CADQ/CADA listeners, while Caduceus mode must explicitly add them and send correct title notifications. Changing the GUI selector requires a stop/restart. |
| Shared xeRAbora telemetry and rcheevos evaluation | Implemented in #216 | Host wire tests; real console unlock pending |
| Local account, live progress, game sets, library, boards, follow account | Bundled upstream UI | Real account/runtime validation pending |
| Native read-only live achievements panel | Improved in #222 | Verified console session and local engine state; in-app searchable/filtered achievements, progress, recent events and trackers. Native login, game details, boards and account migration remain outstanding |
| Native unlock notifications | Opt-in local popups in #222 | Fresh event deduplication against packet-verified PS2 sessions, worker-only HTTP polling, stale/reconnect baseline, capped queue, independent from built-in engine sound. Real-console unlock acceptance pending |
| Caduceus game-root setup | Explicit selection in #222 | Prefer active SMB/UDPFS/HTTP/virtual exFAT game roots; refuse ambiguous auto-selection; real-console pairing acceptance pending |
| Caduceus compatibility, pairing, account pages, title notices | Implemented in #216 | Wire tests; console rendering pending |
| OBS text and JSON export | Implemented on feature branch | Export updates during a session; paths with spaces |
| Optional read-only LAN viewer | Implemented on feature branch | Phone view; account writes and foreign origins rejected; bounded shutdown |
| Local game catalogue and installed-game browser | Implemented on feature branch | Search, metadata editing, covers, installed/RA filters |
| Import local images into OPL folder | Implemented on feature branch | CD/DVD placement; no overwrite or partial published files |
| Download user-supplied game links | Implemented on feature branch | Progress/cancel; safe names; no incomplete file served |
| Caduceus JSON/SQLite catalogue import and backup | Implemented on feature branch | Compatible fields; preserve existing rows; snapshot live WAL; no active-DB overwrite |
| ISO achievement-compatibility scanning | Implemented on feature branch | Official rcheevos hash; bounded lookup/cache; API failures remain retryable |
| PS1 POPStarter VCD compatibility | First implementation in #222 | POPS discovery and atomic imports; RiptOPL PS1 BOOT executable hash and PS1 system 12 index, fixtures; physical console acceptance pending |
| RA game session detection | First implementation in #222 | Passive local state poll, packet advancement and 15 s stale detection; generic SMB/UDPFS active-game detection still missing |
| Cover repair and library storage selection | Implemented on feature branch | Avoid unique-file loss; protect active game sessions |
| Discord desktop activity | Opt-in verified game titles added in #222 | Official ID 1558114313619898409; activity disabled by default; distinct game-title opt-in; no account or path disclosure; live Discord acceptance pending |
| Matching loader export | Implemented on feature branch | Pinned upstream downloads, checksum and license verification; no overwrite; host tests pass |
| Sound controls and first-run/setup guide | Partially implemented in #222 | Six-step read-only guide and persistent invitation choices; engine mute and independent native-popup toggle; atomic per-event custom PCM WAV selection/reset in managed profile. Device/volume settings and packaged UI acceptance pending |
| Theme and catalogue preferences | In progress | Library view and CD/DVD choice persist; native ttk theme is shared with desktop; further catalogue preferences and packaged UI acceptance pending |

Reimplement application-specific behavior independently; Caduceus Electron
source is a protocol/behavior reference, not vendored code. Retain upstream
notices for the MIT xeRAbora/rcheevos components.

Discord is configured in **About / Options ? Desktop settings**. The official
Application ID is the default; users can supply another public ID. No login,
bot token or OAuth is used. Only allowlisted active server mode names are sent by default. With a
separate disabled-by-default opt-in, only the title of a verified live
RetroAchievements session may also be shared. File paths, host addresses,
console serials, account names, credentials and tokens remain excluded.
Duration is optional. Discord IPC runs on a separate worker, checks for changes
every five seconds, sends changed activity at most every fifteen seconds, and
retries unavailable Discord at fifteen-second intervals. Exit clears activity
and closes the IPC transport. Server backends do not import or call Discord.
