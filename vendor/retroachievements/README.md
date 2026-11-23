# Bundled RetroAchievements engine

`sources.json` pins the exact upstream source revisions. xeRAbora's desktop
client and rcheevos are MIT licensed; their original license files are retained.
The embedded page also includes the htm, Preact and signals notices from
xeRAbora's `client/ui/vendor`. Build output stages these notices next to the
engine and the desktop packager includes them in its internal native directory.

Only the desktop client, shared protocol definitions and required rcheevos
source/include tree are vendored. No console-loader ELF or OPL source is
included. No Caduceus Electron source or binaries are copied.

The rcheevos pin is now `1433173220a7eaede6a9ed7a18e94117be1821e0`
(source version 12.5.0), including upstream API bounds and constant-recall
evaluator fixes. The desktop base remains pinned while its indexed watch compiler
and protocol adapt xeRAbora v2.0.0 at the separately recorded
`xerabora-watch-compiler` revision. Legacy RANL remains the default; explicit RAN2
selection is available in both desktop runtimes. See `docs/RA-UPSTREAM-AUDIT.md`
for dependency validation and remaining console acceptance boundaries.

The xeRAbora 2.1.0 and 2.2.0 desktop changes are adapted from the
`xerabora-2.2-client` revision: the console's once-a-second `RAI1 <serial>
<hash>` game announcement, the `RAH1` image hash that now closes each watch
list, pointer chains through Remember/Recall, the 512-chain (PS2) and 256-chain
(PS1) ceilings for RAN2, and answering only the console while it takes its
list. RANL output keeps the 128-chain ceiling that RANL consoles accept.

Local xeRAbora changes:

- Selectable RANL/RAN2 watch serialization with the v2 indexed/constant-base
  compiler, while retaining the legacy wire vectors. Required pointer pairs
  cannot be replaced by direct-only snapshots.

- Read the Web API hardcore earned date independently of the softcore date,
  adapting the alpha.18 correction while preserving the existing row layout.

- A PS2-Servers-owned profile directory selected through the child's environment.
- DPAPI-protected Windows credentials/API keys and private POSIX secret files.
- Tighten existing POSIX application profiles to 0700, reject links/foreign
  owners, and leave shared XDG/.config parent permissions unchanged.
- Account HTTP listener fixed to loopback and its requested port.
- The account page explains local access and directs integration updates/issues
  to PS2-Servers while retaining xeRAbora upstream credit.
- Reject foreign Host/Origin headers, cross-site requests and oversized fields.
- Fail on telemetry/account port conflicts instead of adopting another process.
- Keep the managed service alive after closing its account page.
- Exit when the supervising process exits, including abrupt Windows termination.
- Optional browser suppression for automated tests.
- A non-networking `--hash-file` command using the pinned rcheevos PS2 image
  identifier, for desktop library compatibility scans.
- A writable, DMA-sized reset-notice buffer.
- In Caduceus mode, let the companion send the enriched unlock notice first,
  so the console's duplicate-ID filter does not discard its achievement title.

The build identifies the engine as `0.1.0-alpha.16+ps2servers.ran2`, preserving its
upstream provenance. `launcher/caduceus.py` supplies the additional Caduceus
UDP protocols without altering rcheevos evaluation or enabling hardcore mode.

Protocol references:

- [xeRAbora wire contract at the pinned revision](https://github.com/hacan359/xerabora/blob/357d00e930c7921984f69650706bea69d56f32ef/protocol/PROTOCOL.md)
- [Caduceus compatibility and title-notice contract](https://github.com/Rian6/caduceus-opl/blob/cc1713a893a976c9b26ae1dd9975c200a461edfe/integrations/caduceus/caduceus-ra-bridge.ts)
- [Caduceus paired account-browser contract](https://github.com/Rian6/caduceus-opl/blob/cc1713a893a976c9b26ae1dd9975c200a461edfe/integrations/caduceus/caduceus-achievements.ts)
