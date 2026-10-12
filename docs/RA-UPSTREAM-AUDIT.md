# Upstream RA audit, 2026-10-11

Scope: the pinned xeRAbora, its OPL submodule, Caduceus, Caduceus OPL and
rcheevos references used by PS2-Servers. Source, published binary identity,
host tests and physical console acceptance remain separate evidence.

| Repository | Audited default-branch revision | Delta from our reference | Decision |
| --- | --- | --- | --- |
| hacan359/xerabora | `89d41e0dc5cc4090926ec477e9634e988ff2ee9d` | 3 commits after `357d00e9`: alpha.17 version, Mode 6 documentation, OPL submodule bump | No desktop protocol implementation change; retain existing runtime pin while adding alpha.17 as a candidate loader target |
| hacan359/Open-PS2-Loader | `fa2d2e42cc76881afa914da943da303addbba928` | 1 commit after `531aad45`, four hook/RA files | Console-side Mode 6 candidate; do not attempt a desktop workaround for absent console snapshots |
| Rian6/caduceus | `311f4eabc4ddaad649cbd0d60859f27d0ffcbbfc` | Identical to pin | No update required |
| Rian6/caduceus-opl | `cc1713a893a976c9b26ae1dd9975c200a461edfe` | Identical to protocol-reference pin | Preserve existing profile and exported artifact |
| RetroAchievements/rcheevos | `1433173220a7eaede6a9ed7a18e94117be1821e0` | 11 commits after `f1417fbf`; source version updated to 12.5.0 | Prioritize an isolated dependency validation; do not silently mix evaluator semantics into receiver changes |

## Alpha.17: Mode 6 without losing snapshots

[OPL commit fa2d2e42](https://github.com/hacan359/Open-PS2-Loader/commit/fa2d2e42cc76881afa914da943da303addbba928)
adds an RA-only VBLANK handler when Mode 6 disables IGR. Previously the snapshot
tick depended on the IGR handler, so that mode produced no telemetry. The new
path avoids the IGR thread and padOpen/CreateThread/ExecPS2 hooks, and ignores
network reset requests because there is no IGR thread to execute them.
Upstream explicitly records build success without hardware acceptance.

The four-file console diff does not change the UDP sender, RAWL/RANL layouts
or desktop protocol. This supports retaining the same host profile; it does
not establish hardware compatibility or prove the console-hook change safe.

The published [alpha.17 release](https://github.com/hacan359/xerabora/releases/tag/v0.1.0-alpha.17)
OPL-RA.ELF was downloaded into memory and checked during this audit:

- Size: `1400388` bytes; ELF magic present.
- SHA256: `7cff154d46a294b7415112a97c28109c2c68f8af01de6e5eda0f16e704590cd2`.
- Digest matches both GitHub asset metadata and release `SHA256SUMS`.
- Loader source: `fa2d2e42cc76881afa914da943da303addbba928`, as recorded by the
  xeRAbora submodule update. Reproducible binary provenance remains a gate.

Keep alpha.16 as the existing export baseline until alpha.17 console acceptance
covers Mode 6 on/off, recognition, telemetry/evaluation, notifications, normal
IGR and the expected absence of remote reset under Mode 6. A desktop must not
report a successful reset merely because it sent a datagram.

## rcheevos candidates

[Pinned-to-current comparison](https://github.com/RetroAchievements/rcheevos/compare/f1417fbf2236936e8334533628b2948dfa4a46d0...1433173220a7eaede6a9ed7a18e94117be1821e0)
includes the following relevant changes:

- `ed550c7b`: multiple bounds fixes, including null/short achievement titles in
  API parsing. Validate these against our actually compiled sources first.
- `fef2f83c`: constant recall across alternatives; current changelog records a
  crash fix. This affects evaluator correctness, not the console wire format.
- `e871ef53`: validation index correction around combined conditions.
- `cb4e1382`: replace sscanf in JSON datetime parsing.
- Other commits add PS3 hashing/maps and fix libretro denylist comparisons;
  those features are not evidence of improved PS2 loader compatibility.

Before adopting the revision, run upstream evaluator/API tests, rebuild every
supported native target, recheck PS1/PS2 ISO/compressed hash fixtures and packaged
service lifetime tests, and compare watch lists for representative sets. Do not
submit real unlocks in CI. Source version text alone does not prove a published
release or compatibility with the vendored client's private runtime structures.

## Compatible host improvement implemented

The owned receiver charged foreign-source traffic against its total packet
budget before checking the current console lease. An unrelated sender could
therefore starve valid active-console telemetry. Reject nonowners before charging
that budget. A regression sends 300 foreign queries and verifies that the
current console still delivers its complete frame in the same time window.
This changes no packet layout, valid-client response or active native engine.

Remaining limitations: source-IP leases are not authentication; same-source-IP
traffic can still contend. Measure owner/control/telemetry scheduling under
real load before runtime activation. Prefer internal recovery and compatible
extensions over requiring a new loader wire format.
