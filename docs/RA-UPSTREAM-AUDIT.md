# Upstream RA audit, 2026-10-11

## Refresh, 2026-10-15

Verified default-branch heads and published release metadata directly against
the pins. No dependency or loader artifact pin was changed by this refresh.

| Reference | Current head | Delta / decision |
| --- | --- | --- |
| xeRAbora | `0e5d35a3e9c64594973aff995f5d1a2db62310d4`, alpha.18 | Four commits after our pin. Inspect/adapt focused changes; preserve our managed profile, secret protection and native conformance patches. |
| Caduceus | `311f4eabc4ddaad649cbd0d60859f27d0ffcbbfc` | Matches the existing pin; latest release remains v1.2.1. |
| Caduceus OPL | `cc1713a893a976c9b26ae1dd9975c200a461edfe` | Matches the existing pin. |
| ORBIT | `28a23666fbc8b11b5edabcfff0f780b2b2161219` | One merge commit beyond v1.0.2, with no tree difference. Latest release remains v1.0.2. |

[xeRAbora alpha.18](https://github.com/hacan359/xerabora/releases/tag/v0.1.0-alpha.18)
reports large-set telemetry repair and hardware results for Mortal Kombat:
Shaolin Monks. These are upstream-reported results, not our console acceptance.
Its OPL submodule advances from `531aad4584d65d1779b744739b81402150c7669c` to
`c6e14e6b943be74210981e3a7bd51a25702a8df0`, adding the previously audited Mode 6
snapshot hook and a binary chunk parser correction.

The [parser fix](https://github.com/hacan359/Open-PS2-Loader/commit/c6e14e6b943be74210981e3a7bd51a25702a8df0)
removes scanf whitespace before `%n`, which otherwise consumes leading binary
payload bytes 0x09--0x0D or 0x20. RiptOPL's remote `rebuild/main` already has
its corresponding bounded parser in
[`c0841e4`](https://github.com/NathanNeurotic/Open-PS2-Loader/commit/c0841e4af500a87cc4e4536ce61adf5ba4b49b69).
The local RiptOPL checkout inspected first was behind; do not duplicate that
fix or try to hide it by changing the server's compatible chunk wire format.
Upstream also removes the cheat engine in that commit; adopting that separate
product decision is not required to adapt the parser repair.

Desktop follow-ups from alpha.18: independent softcore/hardcore date flags,
a persistent TV-notice toggle, rich presence in console diagnostics and richer
list/grid presentation. Our vendored `raweb_game_progress` had the old
date precedence at the initial refresh. The 2026-10-16 follow-up adapts the
hardcore-date correction with an eight-case offline fixture compiled against
the production native parser. It preserves our existing row layout and displayed
date fallback. The notice toggle, rich-presence diagnostics and presentation
changes remain unadapted; dependency pins are unchanged. The current embedded
game-detail response exposes the earned date, not this per-row mode flag, so
the parser correction alone does not add mode-aware badges or enable hardcore.

The new unread-memory survey confirms a PS2 agent constraint: low/kernel RAM
below 0x80000 and the scratchpad above 0x02000000 are not streamed as authentic
values by the current EE reader. Our owned direct-watch qualification now
rejects those sets in full, with native fixtures at the boundaries. This does
not alter PS1 logical RAM bounds or the managed engine's routing. Broadening
agent reads needs console work and acceptance, not a host-generated zero value.

References: [Caduceus v1.2.1](https://github.com/Rian6/caduceus/releases/tag/v1.2.1),
[ORBIT v1.0.2](https://github.com/danielnuld/ps2-launcher/releases/tag/v1.0.2).

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

## rcheevos candidate adoption

The table above records the initial audit. A subsequent isolated update adopts
`1433173220a7eaede6a9ed7a18e94117be1821e0` in `sources.json` and the vendored
source tree. Eighteen source files differ after line-ending normalization;
public headers and the xeRAbora client remain unchanged. The relevant bounds,
datetime and evaluator fixes are in sources compiled by our native build.

Validation on Windows:

- Compile and run the candidate's upstream tests with GCC, hash support enabled:
  2,825 of 2,825 passed, including evaluator, API, client and disc hashing.
  This is the default client configuration, without optional external-client
  and RAINTEGRATION extensions.
- Build our native engine against the candidate with `RA_CC=gcc`: passed.
- Run full Python discovery with that engine present: 870 tests passed,
  14 skipped. The prior run without a native engine skipped 18.
- Compile our unchanged watch-list implementation separately against the old
  and new dependency. Six synthetic cases produce identical serialized RAWL/RANL
  bytes and counts: direct widths, bitfields, delta/prior, arithmetic,
  single-level and nested pointer chains. The fixture baseline comes from
  `f1417fbf2236936e8334533628b2948dfa4a46d0`; the checked-in conformance command
  is `python conformance/ra_dependency/check_watchlist.py`. Existing Python CI
  discovers `tests.test_ra_dependency`, which runs this check where a native
  compiler is available, to guard private-structure dependency changes.

This does not cover all achievement sets, physical console behavior, a newly
built desktop package, or alpha.17 loader acceptance. Cross-platform native
builds and exact-head CI must pass before merge. Existing loader export pins
and host wire profiles are unchanged. No account or real unlock is used by
the conformance harness.
