# Bundled RetroAchievements engine

`sources.json` pins the exact upstream source revisions. xeRAbora's desktop
client and rcheevos are MIT licensed; their original license files are retained.
The embedded page also includes the htm, Preact and signals notices from
xeRAbora's `client/ui/vendor`. Build output stages these notices next to the
engine and the desktop packager includes them in its internal native directory.

Only the desktop client, shared protocol definitions and required rcheevos
source/include tree are vendored. No console-loader ELF or OPL source is
included. No Caduceus Electron source or binaries are copied.

Local xeRAbora changes:

- A PS2-Servers-owned profile directory selected through the child's environment.
- DPAPI-protected Windows credentials/API keys and private POSIX secret files.
- Account HTTP listener fixed to loopback and its requested port.
- Fail on telemetry/account port conflicts instead of adopting another process.
- Keep the managed service alive after closing its account page.
- Exit when the supervising process exits, including abrupt Windows termination.
- Optional browser suppression for automated tests.
- A writable, DMA-sized reset-notice buffer.

The build identifies the engine as `0.1.0-alpha.16+ps2servers`, preserving its
upstream provenance. `launcher/caduceus.py` supplies the additional Caduceus
UDP protocols without altering rcheevos evaluation or enabling hardcore mode.

Protocol references:

- [xeRAbora wire contract at the pinned revision](https://github.com/hacan359/xerabora/blob/357d00e930c7921984f69650706bea69d56f32ef/protocol/PROTOCOL.md)
- [Caduceus compatibility and title-notice contract](https://github.com/Rian6/caduceus-opl/blob/cc1713a893a976c9b26ae1dd9975c200a461edfe/integrations/caduceus/caduceus-ra-bridge.ts)
- [Caduceus paired account-browser contract](https://github.com/Rian6/caduceus-opl/blob/cc1713a893a976c9b26ae1dd9975c200a461edfe/integrations/caduceus/caduceus-achievements.ts)
