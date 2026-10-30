# Companion implementation checkpoint - issue #249 / PR #261

This is a continuation checkpoint, not completion of the umbrella issue.
Keep related work in PR #261, unmerged, with at most 100 changed files. Create a
Part 2 only if that limit requires it. The user requested this push so another
agent can continue from the PR. Do not merge or close #249 on this evidence.

## Checkout and existing work

- Branch: `codex/library-discovery-identity`; base reviewed at
  `81ff0956524c947d636002a500e81910313edf9c` (`main`). Refresh before continuing.
- Local isolated checkout: `.worktrees/library-discovery-identity` inside
  `C:\Users\natha\Github\PS2-Servers`.
- The primary checkout has separate unfinished RA work and UDPFS edits. Preserve
  those files; they are outside this PR. Do not mix issue #223 or #238 into #249.
- Existing shared-index/RA-browser functional commit: `d7307826`.
  Portability follow-up: `2ed39c72`.

Implemented: bounded opt-in recursive discovery (already merged), independent
manual and inspected boot IDs, cache invalidation/conflicts, immutable rooted
read-only catalogue descriptors, duplicate/case/Unicode review, and opt-in sharing
of indexed paths/titles with the achievements browser. Both SQLite databases and
RA hash/ID namespaces remain independent. Browse/refresh/scan use workers with
cancellation and stale-result handling. See `LIBRARY-INDEXING.md` for the contract.

The latest checkpoint adds strict sparse compressed inspection. `limits.py`
provides compact indexes, input/output bounds and CHD geometry preflight;
`strict=True` on existing readers adds cancellation, a two-block cache and a
64 MiB decode budget. Serving/hashing defaults retain their existing mode.
`inspect_disc` now opens ISO/CSO/ZSO/CHD logical sectors read-only, fingerprints the
physical file, reports missing decoders, and uses inspector version 2. Native CHD
and blocking filesystem calls cannot be interrupted mid-call. Resource bounds
are not an OS sandbox or a guarantee against all native decoder defects.

## Validation and gates

Previous exact-head CI: `2ed39c72`, run
<https://github.com/NathanNeurotic/PS2-Servers/actions/runs/40688856360>:
all seven jobs passed, including 1,095 Linux tests (48 skips), Windows storage/Tk,
five native achievement-engine platform builds/tests, pinned OPL HTTP conformance,
release compliance, protocol selftests and real libchdr decoding/hash checks.
CodeRabbit passed functional `d7307826`; the test-only follow-up was rate-limited.
These results do not validate the new compressed-inspection commit.

New checkpoint focused Windows run: 83 tests passed, two skipped (native libchdr
unavailable in this local interpreter and one environment-dependent case):
`tests.test_disc_identity`, `tests.test_library_identity`,
`tests.test_ra_compressed_hash`, `tests.test_http_server`. Real CSO decoding and
raw ZSO/ZISO logical reads were exercised with LZ4 installed. Malformed offsets,
oversized headers, deflate output limits, decode budget, cancellation and file
closure have regression coverage. No physical-console tests were run.

### Paused continuation, 2026-10-30

The user requested pausing and pushing the current work for the next agent.
The original exact-head CI (`741c41c6`, run `40739398985`) subsequently passed;
Edge and UDPFS replay workflows also passed. Both inline review findings were
already resolved. Remote `main` remains `81ff0956` at this checkpoint.

This continuation adds real compressed-LZ4 identity fixtures for both ZSO
layouts, ZISO alignment padding, cross-block reads, bounded cache checks and
malformed/oversized decoded payload rejection. A new owned synthetic CHD fixture
exercises nested BOOT2 identity through the actual strict native reader. Native
CHD sparse reads, cancellation/closure and decode-budget enforcement are covered.
CI now installs LZ4 and explicitly reruns the CHD tests after building libchdr.
No production decoder behavior changed.

Focused Windows validation: 104 tests, two skips, no failures, using an existing
libchdr build at `scratch/ra-compatibility-audit/build/native` whose source checkout
matches the project's pinned commit `04a177ee3cea055d93da2d5839d3413168837c6f`.
Set `PS2SERVERS_NATIVE_LIB_DIR` to that absolute directory to reproduce locally.
Release compliance and whitespace checks passed. A broader full-suite attempt
reported Zig native-build/cache-write errors and was interrupted at the user's
pause request; it is not a full-suite pass. Its local log is
`.test-tmp.build/full-suite.log`. Prefer `RA_CC=gcc` for a fresh local full run, as
noted below. Finish final-head CI/review and packaged checks before calling
compressed inspection fully validated. The remaining software list below still
applies; no HTTP/provider/setup/telemetry implementation was added here.

Local test recipe: Python 3.12 with `-S`, `PYTHONPATH=.test-deps.build`, and
`TEMP`/`TMP=.test-tmp.build` (create directories first). The ignored dependency
folder contains Pillow, PyYAML and LZ4. Use a fresh APPDATA profile for GUI tests.
Default Python 3.14 has a PyPI `build` package that can mask this repo's namespace.
Full native tests may need `RA_CC=gcc` and project-local Go/Zig caches. An earlier
full local run had one unchanged RA wall-clock deadline failure; isolated retry
passed. Do not weaken that test or claim a new full-suite pass without running it.

## Remaining software work (continue, do not merely report the list)

1. Add source-pinned capability/acceptance matrix and representative fixtures for
   all #249 cases, including PS1 VCD/Ember descriptors without mixing PS2 identity.
2. Implement explicit HTTP `legacy-flat` (default, unchanged three columns) and
   `riptopl-path-aware` (four columns) profiles, Desktop/Core configuration parity,
   shared validated index, safe nested whitelist routes and virtual ISO decoding.
   Keep 8,192-byte catalogue protection until a larger client contract is proved.
   Test exact ranges/lengths/reuse, duplicates/case collisions, quoting, percent
   escapes/traversal, changes during streaming and both C/Python client parsers.
   Current RiptOPL parser was freshly read from `rebuild/main`:
   `src/httpcatalog.c` blob `c433179366a98fe7cb323f60ebbd847a3a681cf0`,
   `include/httpcatalog.h` blob `81d75da01caa198b733fc68c0bc309d399d6494a`.
   These are blob hashes, not pinned commits; pin a source commit before fixtures.
   Limits: startup 12 bytes, title 160, relative path 255; parser supports quoted
   fields and authoritative column 4, but `.zso` is listable, not launchable.
   Do not copy legacy server comments claiming RiptOPL client-side ZSO support.
3. Implement actual opt-in artwork provider search/matching, previews and confirmed
   missing-art batch acquisition. Research primary provider/license evidence;
   do not assume a public endpoint grants all artwork rights. Verified serial
   first, report ambiguous/unknown identities, preserve existing files. Reuse
   `Catalog.write_art`; it currently converts to RGB and loses alpha, so fix logo/
   disc transparency with tested palette PNG conversion. Keep offline import.
   Bound downloads, redirects, content/decode sizes, retries/backoff, cache,
   provenance, progress and cancellation; no automatic startup download.
4. Extend integrated setup using existing setup guide, server cards, adapter,
   firewall/direct-link and access-check engines. Preview and explicit UI
   confirmation before saved/active-root or OS changes; atomic settings and failed
   start rollback. Export only verified versioned RiptOPL keys with backup/import
   instructions; do not guess PS2 IP or expose credentials. Preserve independent
   roots and correct protocol/port distinctions from the issue.
5. Inventory existing protocol diagnostics, then add opt-in bounded cross-service
   session aggregation/view. Avoid per-packet logging, synchronous DB writes and
   throughput changes. Track discovery/list/open/read/write/close/errors distinctly;
   host reads do not prove gameplay. Test multiple clients, reconnect, errors,
   shutdown/cancel, packaged Windows and Core logs.
6. Optional P3 VMC/packaging conveniences need a justified, safe disposition;
   do not add an unvetted formatter, automatic card assignments or writable HTTP/
   virtual-exFAT promises. Update all required source/docs/site surfaces, run full
   suite/release gates and review exact PR head. Preserve physical-console gates.

No HTTP/provider/setup/telemetry implementation was started in this checkpoint.
The user explicitly requested pushing now for handoff; continue those areas in
the same PR within the file limit. Host/CI results alone cannot close the nested
launch, gameplay-transition, reset, artwork-loader or writable-save hardware gates.
