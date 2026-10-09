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
| OBS text and JSON export | In progress | Export updates during a session; paths with spaces |
| Optional read-only LAN viewer | In progress | Phone view; account writes and foreign origins rejected; bounded shutdown |
| Local game catalogue and installed-game browser | Pending | Search, metadata editing, covers, installed/RA filters |
| Import local images into OPL folder | Pending | CD/DVD placement; no overwrite or partial published files |
| Download user-supplied game links | Pending | Progress/cancel; safe names; no incomplete file served |
| Caduceus JSON/SQLite catalogue import and backup | Pending | Compatible fields; preserve existing rows; snapshot live WAL; no active-DB overwrite |
| ISO achievement-compatibility scanning | In progress | Official rcheevos hash; bounded lookup/cache; API failures remain retryable |
| Cover repair and library storage selection | Pending | Avoid unique-file loss; protect active game sessions |
| Discord desktop activity | Implemented on feature branch | Official ID 1558114313619898409; disabled by default; public server modes only; optional uptime; IPC and shutdown tests pass; live Discord acceptance pending |
| Matching loader export | Implemented on feature branch | Pinned upstream downloads, checksum and license verification; no overwrite; host tests pass |
| Sound controls and first-run/setup guide | In progress | Six-step read-only PS2 setup guide is accessible from Desktop Options; automatic first-run invitation, saved completion state and sound control parity still pending |
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
