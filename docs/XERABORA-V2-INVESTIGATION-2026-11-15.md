# xeRAbora 2.0.0 compatibility investigation

Investigation date: 2026-11-15. Implementation handoff: Monday, 2026-11-16.
This PR records findings and acceptance criteria; it does not update runtime
code, dependency pins or exported console loaders.

## Decision

Treat xeRAbora 2.0.0 as a coordinated host/console migration. The bundled
engine and owned runtime still generate the old pointer plan. Updating only
the loader download, version label, Python validator or vendored header is
insufficient. Keep existing supported pairings available until the new pairing
passes conformance and console acceptance. Do not advertise general v2 support
from successful discovery or direct-memory telemetry alone.

The attachment that prompted this audit announced the release. Its upgrade
instructions are upstream user guidance, not authorization to deploy an ELF,
overwrite game files or use a live achievement account during this investigation.

## Revisions and sources

| Component | Inspected revision |
| --- | --- |
| PS2-Servers remote `main` | `c5f021c4d38698860f56b03498adc261dbb8d8dd` |
| RiptOPL remote `rebuild/main` | `fd197694188fac365e85ca999d6dd5e401a6488a` |
| xeRAbora v2.0.0 and current main | `98949c0efba151f16b234ba5b392b5241791f40e` |
| v2's OPL submodule | `37c5b2f9fb1b4d0e890b21137c69cc16be826cf5` |
| v1.1.0's OPL submodule | `a162ae3e7e14c83ffb758dad387693ae7d779611` |
| Our xeRAbora source pin | `357d00e930c7921984f69650706bea69d56f32ef` |
| Our rcheevos pin AND v2's rcheevos submodule | `1433173220a7eaede6a9ed7a18e94117be1821e0` |

Primary references:

- [v2 release](https://github.com/hacan359/xerabora/releases/tag/v2.0.0),
  published 2026-11-13 19:58 UTC. Upstream reports a real-PS2 check; that is
  upstream evidence, not PS2-Servers/RiptOPL console acceptance.
- [v1.1.0 to v2 diff](https://github.com/hacan359/xerabora/compare/v1.1.0...v2.0.0)
  and [our pin to v2](https://github.com/hacan359/xerabora/compare/357d00e930c7921984f69650706bea69d56f32ef...98949c0efba151f16b234ba5b392b5241791f40e).
- [Protocol header](https://github.com/hacan359/xerabora/blob/98949c0efba151f16b234ba5b392b5241791f40e/protocol/ra_watch.h),
  [watch compiler](https://github.com/hacan359/xerabora/blob/98949c0efba151f16b234ba5b392b5241791f40e/client/src/watchlist.c),
  [diagnostics](https://github.com/hacan359/xerabora/blob/98949c0efba151f16b234ba5b392b5241791f40e/client/src/diag.c).
- [Console migration diff](https://github.com/hacan359/Open-PS2-Loader/compare/a162ae3e7e14c83ffb758dad387693ae7d779611...37c5b2f9fb1b4d0e890b21137c69cc16be826cf5).
- [Current local contracts](RA-OPL-COMPATIBILITY.md), [owned runtime](RA-OWNED-RUNTIME.md),
  [earlier upstream audit](RA-UPSTREAM-AUDIT.md). Their historical validation
  does not establish v2 compatibility.

Refresh remote heads and open PRs before implementing. The open PR lists on
2026-11-15 contained no dedicated xeRAbora-v2 migration. Separate audit reports
were open. The user's main checkout also contained uncommitted RA work;
this investigation used a clean worktree and did not incorporate or alter it.
Reconcile that work before starting implementation; do not overwrite it.

## What actually broke

| Contract | Existing projects | Upstream v2 |
| --- | --- | --- |
| RAWL header | version 1, direct count/byte total | unchanged |
| Pointer tail magic | `RANL` / `0x4C4E4152` | `RAN2` / `0x324E4152` |
| Serialized node | `w, offset`, 8 bytes | `w, offset, scale`, 12 bytes |
| Address expression | parent + offset | `((parent >> shift) & mask(width)) * scale + offset` |
| Node snapshot pair | address + value, 8 bytes | unchanged |
| Limits | 4096 direct entries, 128 nodes, 11529 value bytes | unchanged |
| Transfer/telemetry | RAP1/RAQ1/RAG1/RAC1, RA15, 896-byte chunks | same message families |

Arithmetic wraps at 32 bits; width 0 retains the shifted parent, width 32
does not invoke an undefined 32-bit shift. Parent index remains bits 0-11,
from-node bit 12, size bits 13-15. New shift occupies bits 16-20, width bits
21-26. Nodes remain in dependency order. The PS2 reader still masks final
addresses with `0x1FFFFFFF` and enforces its permitted RAM window.

`ra_watch_file.bytes` is still DIRECT values only; total snapshot bytes are
direct bytes + 8 * nodes. Neither RAWL version 1, RA15, nor a successful RAP1
exchange identifies the node format or certifies correct pointer evaluation.
Old agents can accept the direct prefix and ignore an unfamiliar tail. This
is a silent partial-data failure mode, not necessarily a crash or disconnect.

Upstream also adds constant-based indirect reads as direct entries, which can
change direct count/order even for a set whose serialized node count is zero.
The compiler supports bit/nibble extraction and constant multiply/add/subtract
and selected masks. It still rejects some expressions (including delta/prior
pointer bases, two memory operands combined, division and masks with holes).
Do not interpret v2 as arbitrary AddAddress expression support.

The protocol prose still mentions "1.2.0" for RAN2. Use the tagged release,
header and executable source as the baseline; do not create a nonexistent
required 1.2.0 release from that stale wording.

## Confirmed offline reproduction

Compiled the production pinned and upstream-v2 `watchlist.c`/`snapshot.c`
against the same vendored rcheevos with Windows GCC. A small harness derived
from `conformance/ra_dependency/watchlist.c` activates synthetic conditions,
builds and serializes the actual watch list; logging and `diag_reset` are
stubbed. No sockets, account, console memory or award submission are involved.

| Definition | Pinned nodes / snapshot bytes | v2 nodes / snapshot bytes |
| --- | --- | --- |
| `I:0xX001000_0xH000004=1` | 1 / 12, RANL | 1 / 12, RAN2 |
| `I:0xX001000_I:0xX000004_0xH000008=1` | 2 / 20, RANL | 2 / 20, RAN2 |
| `I:0xH001000*4_0xH080000=1` | 0 / 1 | 1 / 9 |
| `I:0xU001000*4_0xH080000=1` | 0 / 1 | 1 / 9 |
| `I:0xM001000*4_0xH080000=1` | 0 / 1 | 1 / 9 |
| `I:4_0xH080000=1` | build refused | 0 / 1, direct entry |

Four additional direct-width, bitfield, delta/prior-value and arithmetic cases
kept the same counts and snapshot dimensions. These results establish plan
generation differences; they do not prove a game unlock or the new PS2 reader.
The low synthetic addresses deliberately exercise compilation rather than our
owned profile's production RAM qualification.

The ordinary pointer's exact tail changed from
`52414e4c010000000020000004000000` to
`52414e3201000000002000000400000001000000`.
Passing those complete files to current `validate_watchlist` accepts the old
file and rejects v2 with `Invalid pointer-tail dimensions`.

The companion `investigations/xerabora-v2-watch-probe.py` reproduces this probe.
Run from this repository with `python docs/investigations/xerabora-v2-watch-probe.py
--upstream PATH_TO_XERABORA_V2_CHECKOUT`; it requires GCC and reads upstream
source without fetching or executing the upstream desktop application.

## Required implementation, in order

1. **Define the compatibility boundary first (P0).** Publish an explicit
   pairing matrix for legacy xeRAbora, v2, Caduceus, RiptOPL PS2, RiptOPL POPS
   and ORBIT/Neutrino. Existing discovery identifies the client, not all agent
   capabilities. Use explicit profiles or a compatible capability extension;
   never infer RAN2 support from a port number. Keep legacy generation if that
   pairing remains supported, or give a clear upgrade/recheck failure.
2. **Update BOTH host compilers and consumers (P0).** Adapt vendored
   `client/src/watchlist.c` and `protocol/ra_watch.h`; independently adapt
   `native/ra_watch_probe.c` for owned generation/qualification. Update
   `launcher/ra_watchlist.py` for exact 12-byte node dimensions and validation.
   `launcher/ra_evaluator.py::_reads` currently advances through the WATCH FILE
   with `start + 8 * index`; it needs format-aware stride (12 for RAN2), while
   received snapshot pairs still advance by 8. A header-only update breaks
   multi-node evaluation. Preserve whole-set rejection for unsupported owned
   expressions and profile RAM limits.
3. **Update limits and downgrade detection (P0).** The old absolute file bound
   17432 becomes 17944 = 16 + 4096*4 + 8 + 128*12. Audit
   `launcher/ra_protocol.py::watch_chunk`, validator and allocation sites.
   Keep snapshot capacity independent from file capacity. `WatchList.snapshot_sizes`
   currently accepts both direct-only and node-bearing snapshots: a v2-required
   set must not become award-eligible when the console omits required nodes.
   Distinguish an unresolved node (address 0 in a present pair) from a missing
   node region. Revoke old fragments, hit counts and pending results on
   game/account/profile/watch-plan changes, even with equal dimensions.
4. **Adapt the managed engine deliberately (P0).** Preserve profile isolation,
   protected credentials, loopback account UI, network controls and existing
   packaging patches. Do not replace the vendor directory blindly. Update
   `build/build_achievements.py` source selection, version definition, libraries
   and packaging for any adopted `diag.c`/Discord changes. The rcheevos SHA is
   already identical: a dependency bump is not needed to obtain this fix.
5. **Migrate artifacts and caches together (P0).**
   `launcher/achievement_loader.py` still exports alpha.16. After validation,
   add/update an explicitly paired v2 artifact with source commit, size, hash,
   license and provenance. Preserve separately pinned Caduceus/ORBIT targets.
   Tell users to update host and ELF, then re-run support checks for every
   played game and every relevant device/share copy of `RA/<startup>.wl`.
   Never rewrite RANL bytes under a RAN2 magic: old plans lack newly derived
   reads. Watch plans must be regenerated, not relabeled.
6. **Bring diagnostics to parity (P1).** Upstream `diag.c` records up to 3600
   snapshots within a 16 MiB value-data budget (large sets retain less than a
   minute), active conditions/values and per-address history. Add a bounded,
   explicit report action to the managed profile; redact credentials and
   sensitive paths before export. Record exact host/ELF/plan/profile revisions,
   packet loss, node coverage and session identity. Reset recordings on every
   game/plan change. Do not start automatic report uploads.
7. **Implement real test mode (P1).** Upstream unloads/reloads with spectator
   and encore modes to suppress submissions while rearming achievements.
   Adapt this to each runtime; ensure owned queued/retrying submissions,
   award callbacks and session transitions also obey the no-submission rule.
   Prove this at the submission boundary with a rejecting/mock transport.
   A UI toggle or rearming alone is insufficient. Clear runtime state when
   entering/leaving test mode; do not replay test unlocks after returning.
8. **Extend existing Discord support (P2).** `launcher/discord_presence.py`
   already owns desktop IPC and opt-in verified-title sharing. Upstream adds
   rich presence, game icon, last-unlock badge for 60 seconds and profile button.
   Reuse the existing ownership/consent model, prevent duplicate publishers
   from the embedded client, and clear stale game/unlock state on disconnect,
   logout and test-mode transitions. This is feature parity, not required for
   RAN2 transport correctness.

Release metadata currently describes `OPL-RA.ELF` as 1397508 bytes, SHA256
`6725bc9597696a8c51a7ebd32c605fed4715277f1863ae09657233547708d7d1`.
These are GitHub's published metadata, NOT an independently downloaded/hash-
verified artifact in this audit. Recheck bytes, SHA256SUMS, ELF identity and
source provenance before changing the export pin.

## Monday acceptance checklist

- [ ] Commit old/new direct, simple/nested pointer, bit/nibble-index,
  negative-scale/wrap and constant-base golden plans. Compare managed and
  owned output with the pinned v2 compiler; retain separate legacy goldens.
- [ ] Cover malformed/truncated RAN2 tails, parent ordering, invalid size,
  reserved/width bits, count/capacity ceilings, equal-size stale generations,
  missing node regions, zero-address pairs and multiple nodes in `_reads`.
- [ ] Re-run `python conformance/ra_dependency/check_watchlist.py` after
  extending it: today's six-case RANL baseline must not simply be overwritten
  without keeping the intended legacy contract and adding indexed cases.
- [ ] Run relevant `tests/test_ra_*`, frozen resolver/supervisor smoke checks,
  native managed/owned builds and package checks for supported desktop targets.
- [ ] Run a cross-repo matrix: v2 host + v2-capable RiptOPL, old/new mismatches,
  old/new cached `.wl`, PS2 native/Mode 6, POPS and explicitly constrained
  ORBIT/Neutrino. Unknown capability must produce an actionable state.
- [ ] Verify no requests to award APIs in test mode, including preexisting
  queued submissions and toggling mode while a resolver is running.
- [ ] Separately authorized real-console acceptance: direct control title,
  previously failing indexed-pointer title, stable telemetry, notices/IGR,
  loss/reconnect/game switch, with matching refreshed plans. Record exact
  binaries and evidence; emulation/host fixtures are separate.

No target build, emulator run, physical-console run, live login, award or loader
download was performed here. Implementation and hardware acceptance remain
open. Cross-repository coordination: [RiptOPL investigation](https://github.com/NathanNeurotic/RiptOPL/blob/docs/xerabora-v2-investigation-20261115/docs/XERABORA-V2-INVESTIGATION-2026-11-15.md).

The checked-in offline probe was run successfully against the exact source
revisions above and reproduced the table and validator rejection.
`git diff --check` passed. No application build or full runtime suite was run
for this documentation/research change; those remain implementation gates.
