# RA replacement and OPL compatibility handoff

## User-approved objective

PS2-Servers must become an independently owned, complete substitute for the
desktop responsibilities of xeRAbora and Caduceus, compatible with the OPL
versions and forks those applications support. Compatibility here means their
OPL ecosystem, not merely game-image decoding or our preferred loader build.

Compatibility is the baseline, not the ceiling. Improve reliability,
performance, diagnostics, recovery, security and usability where possible,
while preserving existing supported clients. Use negotiated extensions or
compatible fallbacks for new protocol capabilities. Do not reproduce internal
implementation details unnecessarily, or tighten validation in ways that
silently reject supported clients. Claims of improvement need measured evidence.

## Confirmed baseline and evidence boundaries

- PR #222 merged as `0484af5dadad85a3292e3819b2275a952b1c9958`.
  Its tested head was `2ab06f628535cede16cff99560015f07fbc80091`.
- Windows host validation reported 838 tests with 13 skips and no failures;
  final-head CI, CodeQL and Edge passed. CodeRabbit's latest status was rate
  limited, not fresh review approval; earlier findings were addressed.
- Native integration and downloaded Windows package checks establish specific
  host behavior, not complete parity, real-account unlocks or console acceptance.
  See [host acceptance](RA-HOST-ACCEPTANCE.md) for exact package provenance.
- The user reports that the library access-denied error in an earlier screenshot
  **did not recur in the last artifact they tested**. Do not carry it as a
  confirmed current blocker or claim all library/UI flows passed. Exact artifact
  identity and tested action were not supplied in this clarification.
- The independent runtime remains unfinished in
  [issue #223](https://github.com/NathanNeurotic/PS2-Servers/issues/223).
  Existing modes still supervise the xeRAbora-derived engine. A completed backend
  awaiting only a frontend is an incorrect description of current status.

## Priority 1: prove compatibility with their OPL ecosystem

1. Audit the pinned references in `vendor/retroachievements/sources.json`, their
   protocol implementations, release notes and supported loader artifacts.
   Establish the actual supported OPL versions/forks for each application;
   do not invent a support list or assume every OPL build has RA support.
2. Create a compatibility matrix identifying application/reference version,
   loader repository, exact version/commit and artifact hash, protocol variant,
   required features, our artifact/commit, test evidence and remaining failures.
3. Review discovery, pairing/authentication, telemetry, memory request/response,
   snapshot assembly, watch-list updates, timing and session lifecycle across
   those clients. Identify client-dependent behavior and version differences.
4. Add meaningful protocol fixtures and differential tests against reference
   behavior. Cover malformed/truncated/out-of-order packets, stale data, packet
   loss, reconnect, startup order, title changes and resource cleanup where
   applicable. Keep credentials and private game data out of fixtures/logs.
5. Validate actual supported OPL builds on console: recognition, memory reads,
   evaluation, legitimate softcore unlock, reconnect, game change and IGR/relaunch.
   Record exact builds and topology. A synthetic session or matching disc hash
   does not prove achievement evaluation or end-to-end compatibility.

Real-game/image coverage remains necessary, including representative CHD and
compressed LZ4 ZSO inputs and PS1 POPStarter/Caduceus paths, but is only one part
of the substitute requirement. Do not distribute copyrighted game contents.

## Priority 2: complete implementation and reconcile parity

Use [the parity checklist](RETROACHIEVEMENTS-PARITY.md) as a starting inventory,
not proof that parity is complete. Audit every row against current source and
mark implementation, host proof, package proof and console proof separately.

Implement #223: an owned protocol receiver, snapshot assembly, watch-list
lifecycle, rcheevos integration, account/RA API workflow and native/local UI.
Keep legitimate upstream dependency attribution. The intended replacement must
operate without the original desktop application or bundled xeRAbora runtime.
Compare the owned runtime with the existing engine on identical inputs before
switching working paths; preserve client compatibility throughout migration.

Known checklist gaps include native login/account and game details/boards,
account migration, mode isolation, generic SMB/UDPFS active-game detection,
sound device/volume controls and remaining catalogue preferences. Verify each
gap against current main before implementing; subsequent agents may have moved
the work forward. Do not equate these examples with an exhaustive backlog.

## Priority 3: frontend acceptance and demonstrated fixes

Use the latest identifiable package and a disposable profile where practical.
Exercise library browsing/import and path handling, native overview and stale
session state, account flows as implemented, setup/pairing guidance, custom
sound selection/reset and persistence. Record the exact action and result.

The earlier access-denied report is historical and not reproduced in the user's
latest artifact test. Reopen it if it recurs or source evidence demonstrates an
unresolved cause. Do not demand repeated testing of an already passing action
without a relevant change or specific unanswered question.

## Next-agent execution and completion criteria

Start by verifying branch/remotes, current main, open PRs, issue #223 and release
artifact provenance. #222 is merged; continue in a new branch/PR. Preserve local
logs, build tools and unrelated untracked files. Recheck publication rather than
assuming the post-merge workflow completed.

First deliver a source-backed OPL compatibility matrix and reconciled parity
backlog, then implement missing behavior with focused regression coverage.
Prepare exact console artifacts and a short acceptance sequence for the user;
the agent owns source diagnosis and fixes, while physical-console operation
requires the user. Keep console results separate from CI and host results.

Completion means an independently owned substitute with documented supported
OPL builds, required capabilities implemented, and end-to-end evidence for
those builds. Improvements are welcome when compatible and demonstrated.
Do not claim complete parity, compatibility or superiority from a green build,
a synthetic packet, an image hash, or the presence of a UI control alone.
