# RetroAchievements feature parity

The goal is to replace the desktop responsibilities of xeRAbora and Caduceus
inside PS2-Servers, retaining the Python/Tk desktop app. Electron is not required.
Console-loader behavior and hardware validation remain separate from desktop
feature coverage. No claim of complete parity is made while this checklist has
unfinished items.

References are pinned in `vendor/retroachievements/sources.json`.

| Capability | Status | Acceptance |
| --- | --- | --- |
| Shared xeRAbora telemetry and rcheevos evaluation | Implemented in #216 | Host wire tests; real console unlock pending |
| Local account, live progress, game sets, library, boards, follow account | Bundled upstream UI | Real account/runtime validation pending |
| Caduceus compatibility, pairing, account pages, title notices | Implemented in #216 | Wire tests; console rendering pending |
| OBS text and JSON export | Implemented on feature branch | Export updates during a session; paths with spaces |
| Optional read-only LAN viewer | Implemented on feature branch | Phone view; account writes and foreign origins rejected; bounded shutdown |
| Local game catalogue and installed-game browser | Implemented on feature branch | Search, metadata editing, covers, installed/RA filters |
| Import local images into OPL folder | Implemented on feature branch | CD/DVD placement; no overwrite or partial published files |
| Download user-supplied game links | Implemented on feature branch | Progress/cancel; safe names; no incomplete file served |
| Caduceus JSON/SQLite catalogue import and backup | Implemented on feature branch | Compatible fields; preserve existing rows; snapshot live WAL; no active-DB overwrite |
| ISO achievement-compatibility scanning | Implemented on feature branch | Official rcheevos hash; bounded lookup/cache; API failures remain retryable |
| Cover repair and library storage selection | Implemented on feature branch | Avoid unique-file loss; protect active game sessions |
| Discord desktop activity | Implemented on feature branch | Official ID 1558114313619898409; disabled by default; public server modes only; optional uptime; IPC and shutdown tests pass; live Discord acceptance pending |
| Matching loader export | Implemented on feature branch | Pinned upstream downloads, checksum and license verification; no overwrite; host tests pass |
| Sound controls and first-run/setup guide | In progress | Six-step read-only guide, optional first-launch invitation, persisted dismissed/completed state and existing mute toggle implemented; further sound controls and packaged UI acceptance pending |
| Theme and catalogue preferences | Pending | Existing native theme support applied to new views |

Reimplement application-specific behavior independently; Caduceus Electron
source is a protocol/behavior reference, not vendored code. Retain upstream
notices for the MIT xeRAbora/rcheevos components.

Discord is configured in **About / Options ? Desktop settings**. The official
Application ID is the default; users can supply another public ID. No login,
bot token or OAuth is used. Only allowlisted active server mode names are sent;
paths, addresses, game metadata and account information cannot enter activity.
Uptime is optional. Discord IPC runs on a separate worker, checks for changes
every five seconds, sends changed activity at most every fifteen seconds, and
retries unavailable Discord at fifteen-second intervals. Exit clears activity
and closes the IPC transport. Server backends do not import or call Discord.
