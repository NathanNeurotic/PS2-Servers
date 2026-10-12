# RetroAchievements OPL compatibility matrix

Audit baseline: PS2-Servers `9bd458ab5e715e0e4102666ec50081af036eddae`,
2026-10-11. This is a source and artifact inventory, not console certification.
The objective and completion criteria remain in [the handoff](RA-COMPATIBILITY-HANDOFF.md).

## Exact initial targets

These are the two loaders currently offered by `launcher/achievement_loader.py`.
They define the first acceptance targets, not an exhaustive historical support list.
Both release ELFs were downloaded into memory during this audit; size and SHA256
matched the exporter pins and GitHub release asset digests.

| Profile | Loader release | Source revision recorded by exporter | ELF bytes | SHA256 | Current evidence |
| --- | --- | --- | --- | --- | --- |
| xeRAbora | [alpha.16](https://github.com/hacan359/xerabora/releases/tag/v0.1.0-alpha.16) | [hacan359 OPL 531aad45](https://github.com/hacan359/Open-PS2-Loader/tree/531aad4584d65d1779b744739b81402150c7669c) | 1400084 | `0436f8a12fa4e93466687bcb70b00c25d806ecda91ef9669c04b3d5ad40801c9` | Artifact verified; existing host/package evidence; console acceptance missing |
| Caduceus | [2026-10-06 snapshot](https://github.com/Rian6/caduceus-opl/releases/tag/v0.1.1-snapshot.20261006) | [Caduceus OPL 7a85103e](https://github.com/Rian6/caduceus-opl/tree/7a85103eaf65bf19fc155dba239a86acdaae6a55) | 1512132 | `6c6f095f2cf466225109a77f4c64ba7014b523370e2cef93646f1af543f964a9` | Artifact verified; existing adapter/package evidence; console acceptance missing |

An asset digest establishes binary identity, not a reproducible source-to-binary
build. The Caduceus release API also identifies `7a85103e` as its target commit;
the xeRAbora release API names `main`, so its recorded loader source still needs
independent release/build provenance verification.

`vendor/retroachievements/sources.json` pins desktop xeRAbora at
`357d00e930c7921984f69650706bea69d56f32ef`, Caduceus desktop at
`311f4eabc4ddaad649cbd0d60859f27d0ffcbbfc`, and the Caduceus OPL **protocol
reference** at `cc1713a893a976c9b26ae1dd9975c200a461edfe`. That last revision is
different from the exported ELF source. Compare both before treating reference
fixtures as proof of the exported loader's behavior.

## Transport and feature boundaries from upstream

| Target | Upstream documented path | Required qualification |
| --- | --- | --- |
| xeRAbora alpha.16 family | ISO via USB/BDM, internal HDD over BDM, MMCE; original disc; PS1 VCD in USB POPS through POPStarter | These are upstream claims, not PS2-Servers console results. Establish exact loader behavior for each path. Original-disc mode lacks the emulated-disc VMC, compatibility patch and cheat features. |
| Caduceus snapshot family | Loader advertises USB/BDM, HDD and network; RA compatibility query specifically documents ISO on USB/BDM and ETH | General game loading support does not establish RA support on every transport. |
| Caduceus account browser | Pairing key in `ART/CADUCEUS.KEY`, read through ETH share; UDP 18198 | ETH share is required even when browsing achievements for a USB game. Keep the pairing key separate from RA credentials. |
| Ordinary OPL / other forks / older releases | No support established by this audit | Inventory actual RA-capable releases and protocol revisions before adding support claims. RiptOPL PS1 hash compatibility alone does not certify its full session protocol. |

Sources: pinned [xeRAbora README](https://github.com/hacan359/xerabora/blob/357d00e930c7921984f69650706bea69d56f32ef/README.md)
and [Caduceus OPL README](https://github.com/Rian6/caduceus-opl/blob/cc1713a893a976c9b26ae1dd9975c200a461edfe/README.md).
Both projects describe experimental behavior; preserve that qualification.

## Protocol requirements and evidence

| Requirement | Current implementation/source | Evidence and remaining gate |
| --- | --- | --- |
| Base UDP 18194 discovery and identification | Vendored `client/src/protocol.h`, `console.c`: RAP1/RAO1, RAQ1/RAA1 | Prior native discovery/package checks exist. Differential loader fixtures and console startup-order checks remain. |
| Watch-list transfer | RAG1/RAC1; 896-byte chunks; replies at least 128 bytes and aligned to 64 bytes | Alignment is a console DMA requirement, not cosmetic padding. Preserve it in the owned receiver. Test exact wire bytes, chunk boundaries, retries and stale lists. |
| Telemetry and snapshot assembly | RA15 datagrams; `protocol/ra_snap.h` defines RAS1 snapshot memory layout | Do not conflate RAS1 memory layout with the UDP text envelope. Test missing, duplicate, reordered parts and session changes against console source. |
| Watch-list extensions | `protocol/ra_watch.h`: RAWL version 1, optional pointer-chain tail | Older consumers may ignore the tail. Verify loader revision capability before claiming chain evaluation; version 1 alone is insufficient. |
| Base unlock display | Vendored `console.c`: RAU1; badge transfer defined as RAB1 | Legitimate unlock/submission, badge rendering and gameplay impact need physical acceptance. |
| Caduceus query/readiness | Owned `launcher/caduceus.py`: CADQ1/2 on UDP 18197 | Existing adapter tests cover both versions and unknown/error states; compare fixtures with actual exported loader. |
| Caduceus account browser | CADA1 on UDP 18198, pairing key, paged/filterable account data | Existing host tests cover pairing, formatting, filters and logout races. ETH key retrieval and console rendering remain untested here. |
| Caduceus titled notice | Owned adapter sends RAU1 title/points to UDP 18195 | Existing host/package checks do not prove card rendering or notification recovery on console. |
| Base-mode isolation | Supervisor launches shared ps2ra; Caduceus adapter is mode-dependent | Audit native unsolicited notices and all listeners/writes, not just Python adapter creation. Full isolation remains an acceptance item. |
| Owned evaluation/account service | Both modes still launch the xeRAbora-derived native engine | Missing independent implementation; retain rcheevos library and attribution. Track issue #223. |

Prior results are recorded in [host acceptance](RA-HOST-ACCEPTANCE.md).
Do not relabel those results as tests of this audit commit or a later package.

## Reconciled implementation backlog

| Work | Current classification | Next concrete gate |
| --- | --- | --- |
| Independent receiver, watch lists, snapshots, evaluator integration, account/API/session lifecycle | Missing; shared upstream runtime remains | Implement behind existing supervisor contract; compare identical inputs before migration |
| Native login/account, game details, leaderboards, opt-in profile migration | Incomplete; embedded upstream account UI still used | Native workflows with secure storage, rollback and offline/error behavior |
| Live overview, native notifications, OBS, viewer, Discord | Implemented with specific prior host/package evidence | Exact current-package UI acceptance and real-session/unlock checks |
| PS2 compressed hashing and PS1 VCD scanning | Implemented; synthetic/hash evidence exists | Representative real-image and loader/console acceptance; no inference from hash to unlock support |
| Generic SMB/UDPFS active-game detection | Missing from parity checklist; RA session detection is a separate path | Define authoritative session identity before exposing game presence |
| Sound device/volume and remaining catalogue preferences | Incomplete; mute/custom WAV selection already exists | Implement remaining controls and verify persistence in packaged UI |
| Earlier library access-denied report | Historical; user did not reproduce in latest tested artifact | Reopen only on reproduction or a demonstrated current defect |

## Ordered acceptance work

1. Compare exported loader source with protocol reference; inventory historical
   RA-enabled loaders and document variant/capability differences.
2. Add reference-derived fixtures for discovery, watch transfer, telemetry,
   timeout/reconnect, malformed/duplicate/reordered packets, mode isolation and
   Caduceus pairing/pages/notices. Avoid tests that merely mirror our adapter.
3. Implement the owned runtime in compatible slices, preserving existing paths
   until differential and packaged acceptance passes.
4. For each console run record loader SHA256, server commit and package SHA256,
   transport/topology, game identifier, startup order, expected/actual behavior,
   logs and result. Cover recognition, memory/evaluation, legitimate softcore
   unlock, reconnect, title change, IGR and gameplay impact. Never perform real
   account unlock submissions in CI.
5. Keep existing public standard-runner CI and release gates. Additional paid
   runners/services are outside the project's current zero-additional-spend constraint.
