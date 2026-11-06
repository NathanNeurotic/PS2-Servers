# UDPFS virtual CD CHD CUE/BIN: implementation status and acceptance plan

> **IMPLEMENTATION IN PROGRESS (2026-11-06).** This branch contains Python/Core and Go Edge providers plus synthetic metadata/layout tests. The PR remains draft. The focused Python provider and related regression set passes (48 tests, 3 real-libchdr skips). A full `python3 -m unittest discover` run is red: 1,420 tests, 300 errors, 3 failures, and 65 skips; output includes sandbox-denied socket binds and unavailable native `rcheevos`, so the full suite is not verified. The Go toolchain is unavailable, and no real-console acceptance has been performed. Treat unchecked items below as outstanding; do not describe the feature as release-ready.
>
> **Goal:** Maintain PS2-Servers behavioral parity or better with pcm720/udpfsd and compatible UDPFS clients (Neutrino as launched by NHDDL, RiptOPL, POPStarter via UDPFS Assault, and Ember), without discarding existing Core/Edge reliability wins. The largest missing feature identified is **CD-format CHD presented transparently as virtual CUE and raw BIN files**.

## Baselines and provenance (reviewed 2026-11-06)

Pin these source revisions for reproducibility; re-check HEAD before adopting a change:

| Repository | Reviewed commit | Purpose |
| --- | --- | --- |
| [NathanNeurotic/PS2-Servers](https://github.com/NathanNeurotic/PS2-Servers/commit/f430783ea2ab059b4e88184755ef0c7a26bbfa08) | f430783ea2ab059b4e88184755ef0c7a26bbfa08 | Existing Python/Core and Go Edge behavior |
| [pcm720/udpfsd](https://github.com/pcm720/udpfsd/commit/ee94414f9f45ed33b8dd4ca4e03847440df946e9) | ee94414f9f45ed33b8dd4ca4e03847440df946e9 | Reference Go server, transport, compression |
| [Okeanos86/udpfsd Plan B](https://github.com/Okeanos86/udpfsd/tree/16f68d55ac9b4fe68f1a2d12ce0ced9ed1052552) | 16f68d55ac9b4fe68f1a2d12ce0ced9ed1052552 | Experimental Go CHD/CUE/BIN and virtual-handler design |
| [Okeanos86/neutrino Python branch](https://github.com/Okeanos86/neutrino/tree/6aed059a289c000753f40b04ed1ded0f7e503830/pc) | 6aed059a289c000753f40b04ed1ded0f7e503830 | Experimental Python implementation, tests and fixtures |

Context: Discord technical discussion from Okeanos86, pcm720, Maximus32, and TnA (2026-10-19 through 2026-11-04). Three accompanying proposals were reviewed: "udpfsd patch proposals from the UDP FS Assault write client", "directory listing stops at the first invalid compressed image", and "CD CHD images as virtual CUE/BIN". This handoff transcribes the actionable behavior and limits; attachments themselves are **not** committed to this repository.

**Human/upstream credit:** retain credits and license notices to pcm720 (Ivan V), Okeanos86, Maximus32/rickgaiser, and appropriate upstream contributors as their code or concepts are incorporated. Follow the existing THIRD_PARTY_NOTICES and AGENTS.md policy. Do not fabricate author credits or replace existing provenance.

## Decision table: adopt only real gaps

| Item discussed | Source-confirmed baseline | Action |
| --- | --- | --- |
| Virtual CD CHD -> Game.cue + Game.bin, plus existing CHD ISO view | Not present in PS2-Servers Python/Core or Edge at reviewed commit | **IMPLEMENT** |
| Extensible compressed-image views | Current local decompression is mostly ISO-alias-specific | **DESIGN/IMPLEMENT INCREMENTALLY**, never overwrite existing filesystem behavior wholesale |
| DREAD skips invalid compressed images/unstatable entries | Python _handle_dread loops past errors; Edge Root.List and prepareDirectory skip failures | **PRESERVE; EXTEND TESTS** |
| Compressed-extension case folding | Both local backends already normalize extensions | **PRESERVE** |
| Negative Linux WriteBatch count (sendmmsg can yield n=-1) | pcm720/udpfsd and Edge both clamp n < 0 to 0 | **ADD EDGE FAULT TEST**, do not reapply patch |
| Close write handles on peer reset | pcm720 rejected this: Neutrino intentionally keeps RW handles for MC/HDD emulation; Edge already tests inherited writable FILEID | **REJECT blanket close**, investigate only proven abandoned-writer failures |
| GETSTAT on unopened files | Already supports unopened paths in Core and Edge, with compressed ISO alias resolution | **NO PORT NEEDED** |
| Handle bounds/invalid handles | Core/Edge already use handle maps/limits; verify malformed packets and wraparound | **AUDIT/TEST ONLY** |
| CSO/ZSO GetStat FD leak | Confirmed on pcm720/udpfsd: both GetStat paths os.Open without Close; PS2-Servers Edge SizeOf already defers Close, Python uses scoped access | **UPSTREAM FIX CANDIDATE**, not a local feature gap |
| mc?:/SYS-CONF/UDPFSCONFIG.DAT pinning | Already integrated in RiptOPL's POPS UDPFS Assault setup; no server-side memory-card access needed | **DOCUMENT/TEST DISCOVERY**, no PS2-Servers config-file parser |
| NHDDL compatibility | NHDDL launches Neutrino; server parity is measured against Neutrino wire/file/block semantics, not launcher GUI feature parity | **MATRIX/TEST** |

Relevant local evidence:
- [Python UDPFS server](../udpfs_server/udpfs_server.py), especially _prepare_directory_entries, _handle_dread, _handle_open, _handle_getstat, _resolve_compressed_sibling.
- [Python CHD wrapper](../udpfs_server/compressed_iso/chd.py).
- [Go Edge operations](../native/ps2servers-edge/internal/udpfs/operations.go), especially prepareDirectory, resolveImagePath, handleOpen, handleDRead, handleGetStat.
- [Go Edge CHD enabled/disabled builds](../native/ps2servers-edge/internal/compression/chd_enabled.go), [disabled](../native/ps2servers-edge/internal/compression/chd_disabled.go), and [compression SizeOf](../native/ps2servers-edge/internal/compression/image.go).
- [Core DREAD regression](../tests/test_udpfs_dread_skip_entry.py), [Edge rediscovery regression](../native/ps2servers-edge/internal/udpfs/rediscovery_test.go), [batch-write tests](../native/ps2servers-edge/internal/udpfs/udpwrite_test.go), and [three-way parity runner](../conformance/integration/udpfs_parity.py).

## Desired external behavior

Given a physical file at POPS/Game.chd, where Game.chd is a valid supported **CD** CHD, the server with compression enabled can present:

| Client-visible name | Server representation |
| --- | --- |
| POPS/Game.cue | Generated read-only CUE with CRLF, a single FILE "Game.bin" BINARY, correct TRACK/INDEX lines |
| POPS/Game.bin | Generated read-only raw BIN, **2352-byte CD sectors**, tracks concatenated without CHD-internal 4-frame padding |
| POPS/Game.chd.iso | Preserve current ISO/decompressed view and existing behavior where applicable |
| POPS/Game.chd | Never silently reinterpret a direct request for the real compressed container |

All advertised virtual files must behave consistently across **directory listing, GETSTAT, OPEN, READ, LSEEK and BREAD**. Existing real files win over virtual aliases. No CHD content is extracted to a permanent intermediate BIN on disk. Unsupported images must not advertise a fake playable CUE/BIN pair. A normal or corrupt compressed file cannot cause DREAD to report end-of-directory before valid following files.

**Do not claim universal compatibility.** Initial supported CHD tracks: MODE1_RAW, MODE2_RAW, AUDIO; reject cooked tracks and unsupported metadata as the upstream prototype does. The upstream design reads 2448-byte CHD frames (2352 sector bytes + 96 subcode), maps track metadata, removes CHD per-track padding, byte-swaps audio data where required for the target BIN convention, and serves decoded sectors with a bounded hunk cache. **Pregaps require independent testing:** stored gaps become INDEX 00/01; missing/non-stored gaps may be synthesized as silence, but this behavior is not fully hardware-validated. Keep existing DVD CHD/CSO/ZSO ISO reads unchanged.

Primary reference files:
- [Go virtual handler registry](https://github.com/Okeanos86/udpfsd/blob/16f68d55ac9b4fe68f1a2d12ce0ced9ed1052552/internal/fs/compression/common/handler.go)
- [Go CHD handler](https://github.com/Okeanos86/udpfsd/blob/16f68d55ac9b4fe68f1a2d12ce0ced9ed1052552/internal/fs/compression/chd/handler.go)
- [Go CHD track table/layout](https://github.com/Okeanos86/udpfsd/blob/16f68d55ac9b4fe68f1a2d12ce0ced9ed1052552/internal/fs/compression/chd/toc.go)
- [Go on-demand BIN decoder](https://github.com/Okeanos86/udpfsd/blob/16f68d55ac9b4fe68f1a2d12ce0ced9ed1052552/internal/fs/compression/chd/cdimage.go)
- [Go virtual-filesystem tests](https://github.com/Okeanos86/udpfsd/blob/16f68d55ac9b4fe68f1a2d12ce0ced9ed1052552/internal/fs/virtualfiles_test.go)
- [Python CHD/CUE/BIN](https://github.com/Okeanos86/neutrino/blob/6aed059a289c000753f40b04ed1ded0f7e503830/pc/compressed_iso/chd_cuebin.py)
- [Python tests](https://github.com/Okeanos86/neutrino/blob/6aed059a289c000753f40b04ed1ded0f7e503830/pc/tests/test_chd_cuebin.py)
- [Python server integration](https://github.com/Okeanos86/neutrino/blob/6aed059a289c000753f40b04ed1ded0f7e503830/pc/udpfs_server.py)

The contributor reported matching extracted BIN SHA-1 against chdman extractcd for 2-track and 14-track CHDs, ARMv7 decoding near 9.5 MB/s in one test, and hardware boot/read with POPStarter and Ember including 74/75-sector BREAD and VMC saves. Treat these as **upstream-reported observations**, not local reproduction. No corresponding test run was performed for this planning PR.

## Phase A: inspect + design without regressions (P0)

- [ ] Rebase/review PS2-Servers main, upstream udpfsd main, and both Okeanos branches; note changed assumptions and existing local PRs before touching code.
- [ ] Map the full namespace path for Python/Core and Go Edge: DOPEN/DREAD, path resolution, GETSTAT, OPEN, READ/BREAD, read-only enforcement, per-peer file-handle cleanup, and non-CHD compressed ISO aliases.
- [ ] Design a minimal virtual-view provider abstraction: compressed extension registration; list of presented names and stat; open by requested virtual name; cached/seekable stream; safe close. Implement format-specific track parsing outside the generic filesystem.
- [ ] Define collision behavior **for the entire generated pair**: real Game.cue with virtual Game.bin (or inverse) risks silently mismatching disc content. Either verify a compatible pair or suppress virtual aliases/pair with a clear diagnostic. Preserve strict real-file precedence for direct OPEN and GETSTAT. Do not silently serve a mismatched pair.
- [ ] Ensure the same virtual names are consistently advertised by DREAD and accepted by GETSTAT/OPEN; handle malformed input, file replacement while scanning, casing, symlinks, path escape, overlength names (255-byte UDPFS name boundary, plus RiptOPL's stricter title limits), and duplicate aliases.
- [ ] Decide codec availability by real runtime/build capability. Compression disabled, missing libchdr, invalid CHD, and unsupported CD track layouts must degrade safely.

**Do not:** cherry-pick the entire experimental filesystem, change the UDPFS wire protocol, alter default discovery behavior, or rewrite existing save/recovery paths to add this read-only feature.

## Phase B: Python/Core implementation (P0; first implementation work)

- [x] Add an isolated CHD CD track-table/CUE/BIN provider under udpfs_server/compressed_iso/. Okeanos86's prototype is credited as a behavioral reference; its source is not copied.
- [x] Parse bounded CHD v5 CD metadata and validate track order, frame bounds, supported raw track modes, pregaps and offsets before advertising a pair.
- [x] Implement a virtual in-memory CUE and a read-only, seekable raw BIN stream decoded through libchdr on demand. Core tests cover unaligned reads, seek, EOF and 74/75-sector BREAD patterns.
- [x] Use explicit handle cleanup and a 4 MiB decoded-hunk LRU; virtual aliases do not open a persistent file descriptor during directory listing or GETSTAT.
- [x] Extend Core DREAD, OPEN, GETSTAT and path resolution while preserving the existing compressed ISO aliases and real-file precedence.
- [x] Preserve root containment, reject symlink CHD sources and prevent writes through virtual aliases while retaining real-file behavior.
- [x] Add and run synthetic Core tests for track layout/CUE bytes, raw BIN samples, 74/75-sector BREAD reads, malformed metadata, directory enumeration, collisions, disabled compression and write protection.
- [ ] Add independent complete-fixture hashes against `chdman extractcd`, plus restart/reopen and concurrent-reader coverage. Current Core coverage uses synthetic CHD metadata and a fake libchdr reader.
- [ ] Verify existing non-CHD Core tests still pass, including UDPFS DREAD/ART regression and VMC write paths.

## Phase C: Go Edge implementation and packaging (P1, after Core passes)

- [x] Implement Go CHD metadata, virtual CUE and raw BIN streaming within Edge's existing filesystem and protocol handlers. A pure Go metadata/layout regression test is added but unrun because Go is unavailable here.
- [x] Keep existing Edge compressed ISO aliases stable and gate virtual CD views on the existing CGO + `chd` + libchdr availability constant; generic builds keep CHD raw and unadvertised.
- [ ] Avoid repeated metadata parses on directory/DREAD hot paths with bounded identity-keyed caching; decoded hunks already use a bounded 4 MiB cache.
- [ ] Add and run Edge protocol integration tests for DREAD, GETSTAT, OPEN, READ/LSEEK/BREAD and virtual-write rejection.
- [ ] Compile and test Edge on supported targets (including router/ARM, Windows and Linux as relevant); Go is unavailable in this environment.
- [ ] Extend Edge unit/integration tests for all Core fixtures and adverse cases; retain existing transfer caps and per-peer resource bounds.
- [ ] Compare Core and Edge byte-for-byte on a pinned fixture corpus and against independent chdman extractcd output; exclude incompatible upstream reference behavior from a false-positive parity claim.

## Phase D: protect already-implemented reliability fixes (P0/P1)

- [ ] DREAD: retain Core's skip-on-stat-error loop and Edge's skip-on-invalid-entry behavior; add a case where corrupt A_bad.cso precedes Z_after.txt/ART and subsequent valid files remain visible. Test compressed-extension case folding and multiple invalid entries.
- [ ] UDP Linux batching: existing Edge adapter clamps n<0 and upstream udpfsd now does as well. Add deterministic fake-batcher coverage returning **-1** and an error; no panic/slice underflow, safe fallback/telemetry. Existing full/partial/zero tests remain passing.
- [ ] GETSTAT: preserve unopened-path behavior, including decompressed aliases and real paths. Assert missing/invalid compressed alias returns an error consistently, not success or a zero-size fake file.
- [ ] Handles: test missing, negative, out-of-range and stale handles. Preserve resource caps and predictable error replies; avoid unrelated allocator refactoring.
- [ ] **Do NOT close all RW handles on ResetPeer.** pcm720 explicitly rejected that proposal because Neutrino MC/HDD emulation depends on live RW handles across resets. Edge has writable FILEID inheritance tests. Determine genuine abandoned-writer behavior by reproducer; only retire ownership after a confirmed replacement or session-expiry policy, never for a normal discovery packet.
- [ ] If a stale exclusive writer can block reopen after actual replacement, add a narrow regression test plus the smallest safe fix. Require save-recovery and Neutrino regressions before accepting it.
- [ ] The CSO/ZSO GetStat descriptor leak is in pcm720/udpfsd (GetStat opens without explicit close); local Edge SizeOf already uses defer Close and Python uses scoped file operations. If contributing upstream, use a separate upstream PR with explicit close + descriptor test; do not import the leak.
- [ ] Retain current UDPFS transport-replay and 3-way conformance test results as a baseline; investigate failures rather than weakening expected results.

## Phase E: client interoperability and real-hardware acceptance (release gate)

RiptOPL already scans UDPFS POPS/*.cue and .CUE as a virtual VCD source (see [RiptOPL vcdsupport.cpp](https://github.com/NathanNeurotic/RiptOPL/blob/main/src/sources/vcdsupport.cpp) and [VCD docs](https://github.com/NathanNeurotic/RiptOPL/blob/main/docs/VCD.md)). It also already supports mc0:/SYS-CONF/UDPFSCONFIG.DAT then mc1, including direct-IP discovery and fallback. Do not add an unnecessary server-side DAT parser.

NHDDL is a **Neutrino launcher**, not a server to clone. Its udpfs_ip parameter is the PS2's own address, not the UDPFS Assault server-pinning file ([NHDDL README](https://github.com/pcm720/nhddl/blob/main/README.md)). Server parity targets Neutrino's file/block/transport behavior.

Run the acceptance matrix (mark not run as unverified):
- [ ] Synthetic MODE2_RAW, MODE1_RAW, AUDIO and mixed CD CHDs, one/multiple tracks, a 14-track disc; compare virtual BIN SHA-1 to chdman extractcd.
- [ ] Stored pregap, non-stored pregap (real data, not only synthetic), INDEX 00/01, track boundary, audio byte order, silence and gapless playback.
- [ ] Unsupported cooked-track, GD-ROM/legacy metadata, corrupt/truncated, huge/invalid size/offset; no crash, uncontrolled allocations, or incorrect listing.
- [ ] Ordinary ISO, DVD CHD, CSO, ZSO unaffected; compressed image absent or disabled remains absent, not a ghost alias.
- [ ] Real/virtual alias collisions, mixed-case extensions, long UTF-8 names, duplicate variants, symlink/path-traversal attempts, concurrent file replacement.
- [ ] Entire DREAD directory visible after an unreadable/invalid image; covers/art and subsequent games still enumerate.
- [ ] Full virtual sequence DOPEN/DREAD, GETSTAT before and after OPEN, READ/LSEEK/BREAD, repeat/seek/EOF/CLOSE; two peers reading the same image.
- [ ] Reject attempts to WRITE/BWRITE to virtual CUE/BIN; allow normal VMC and other authorized writes unchanged.
- [ ] Network NACK, delayed/duplicate/out-of-order packets, sequence wrap, cable unplug/replug, server restart, client re-discovery, two-console isolation, preserved RW file handles and VMC saves.
- [ ] Real PS2 with RiptOPL -> POPStarter UDPFS Assault (game boot, multi-track audio, VMC save+reload), Ember, and NHDDL -> Neutrino (DVD CHD, gameplay, MC/HDD emulation).
- [ ] Monitor bounded peak RSS, CPU, directory-scan latency, file-descriptor counts, cache usage/eviction on low-RAM systems.

Hardware tests must be labeled as hardware tests. A build, unit-test pass or reported upstream result does **not** substitute for a physical-console acceptance result. Where hardware is unavailable, state the blocked test precisely and do not call the feature fully verified.

## Scope and execution order for the next coding agent

1. **Read** this handoff and AGENTS.md. Establish working branch, check current main/PRs, and confirm baseline reproduction/tests before any edit.
2. **Implement Phase A + Phase B** in small coherent commits, with an independently testable Python/Core CUE/BIN feature and no changes to unrelated protocols.
3. Add and run automated tests; report exact commands, pass/fail and fixtures. Request review rather than automatically merging.
4. **Implement Phase C** only after Core is stable. Packaging and feature-gating are part of the definition of done, not follow-ups.
5. Complete **Phase D regression protections** and **Phase E client/hardware acceptance** to the extent possible. Keep known gaps explicit in the PR.
6. Update UDPFS/compression documentation and legitimate upstream credits. Provide a final per-backend parity table: supported, tested, untested, intentionally not implemented.
7. Convert draft to ready for review only when implementation and automation are green and remaining hardware limitations are clearly stated. **Do not auto-merge.**

The agent may continue implementation commits in the PR branch, or split Python/Core and Edge into dependent implementation PRs if the review surface becomes too large; link the resulting PRs back to this plan. The planning commit alone must never be presented as an implemented feature.

## Definition of done

- [ ] Virtual CUE and raw BIN behave correctly and identically from both PS2-Servers backends when CHD support is available.
- [ ] All existing file, compressed ISO, UDPFS/UDPBD, multi-client, writable VMC, and reconnect behaviors still pass.
- [ ] No unnecessary upstream parity changes were copied over stronger local fixes.
- [ ] No virtual-image writes, path escapes, leaked handles, unbounded decode allocations, or phantom namespace entries.
- [ ] Automated test evidence and any real-console results are recorded with limits and build configurations.
- [ ] Clear documentation, build availability, licenses and human/upstream attribution are preserved.
- [ ] Human review occurs before merge.

## Explicitly out of scope

- Replacing PS2-Servers' UDPFS server with udpfsd or the experimental fork.
- Implementing NHDDL launcher functionality on the PC server.
- Changing RiptOPL's existing UDPFSCONFIG.DAT client-side behavior.
- Blanket closure of Neutrino MC/HDD RW handles on rediscovery.
- Writing to a compressed CHD container through a decompressed alias.
- Broad SMB, HTTP, UDPBD or unrelated app changes not demanded by a failing regression test.

**Safety rule:** preserve users' saves and current, working device paths. Prefer a narrow fix with a reproducer over speculative architecture churn.
