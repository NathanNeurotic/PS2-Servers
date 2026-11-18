# Sable Audit — PS2-Servers

**Pin:** `main` @ `5179b4bb1f488ef2eb91956c8792e2194dda2fca` (2026-11-13 09:04:41 -0800, "Docs site: softer dark-gray colour scheme (#314)"), fresh clone at `~/workspace/audit-clones-2026-11-13/PS2-Servers`, checked 2026-11-13. Read-only: no repo files edited, no branches/PRs/issues/workflows touched.

**Shape of the tree (READ, `git ls-files` + `wc -l`):** 596 tracked files. Python non-test ≈ 41,508 lines (242 files); Go Edge non-test 11,586 lines + 9,564 lines of Go tests; vendored C (RetroAchievements) 52,963 lines (115 files under `vendor/`); Python tests 122 files / 27,441 lines; `conformance/` ≈ 2,519 lines. Three deployment editions are documented in `docs/EDITIONS.md`: Desktop (Tk GUI), Core (same Python servers, headless), Edge (Go, for routers/NAS). That three-edition split is the root cause of most findings below: almost every protocol exists two or three times.

**Prior-context re-verification (2026-11-05 claims, re-checked at this pin):**
- Three UDPFS editions: **still true** — Python standalone, Python Core, Go Edge (PS2SERVERS-001).
- "Core trails on peer-reset handle recovery": **partly closed.** Core now has both a transport-only reset that preserves handles for loader handoff (`udpfs_server/ps2servers_core.py:141-166`) and a full reset that closes peer handles (`udpfs_server/ps2servers_core.py:168-180`, called from `_handle_discovery`). The standalone server still only closes a peer's handles when its session thread exits / is reaped (`udpfs_server/udpfs_server.py:3286-3294`).
- "Core trails on single-writer exclusion": **still true, and true of every edition** — no cross-peer/file write exclusion exists anywhere (PS2SERVERS-013).

**Secrets:** no real credential was found or is quoted in this report. One secret-like value (an example credential literal in a usage docstring) exists at `PS2-Servers/smb2_server/smb2_server.py:44`.

Findings are sorted by benefit. Benefit figures are estimates unless marked measured.

---

## PS2SERVERS-001 / UDPFS is implemented three times
- **Category:** 2 (Duplication)
- **Location(s):** `PS2-Servers/udpfs_server/udpfs_server.py:1-3643` (standalone, 3,643 lines); `PS2-Servers/udpfs_server/ps2servers_core.py:1-690` (Core); `PS2-Servers/native/ps2servers-edge/internal/udpfs/server.go`, `operations.go`, `negotiation.go`, `transfer.go`, `pathops.go`, `blockdev.go` (≈2,700 non-test lines)
- **Problem:** The same UDPRDMA/UDPFS wire protocol, session model, handle table, write assembly and discovery handling exist in Python twice and in Go once. Core is not a separate server: it subclasses the standalone (`class AutoUdpfsServer(UdpfsServer)`, `ps2servers_core.py:90`) and overrides exactly six methods (`__init__`, `run`, `_sendto`, `_get_or_create_session`, `_handle_data`, `_handle_discovery` — the class docstring at `ps2servers_core.py:90-110` admits a fix edited into only the standalone copy "produces code that never executes on the shipped path, which has already happened once"). Every protocol fix must be made, reviewed and tested up to three times, and the editions demonstrably drift (Core has per-session Standard/Modulo negotiation, standalone has global flags; see PS2SERVERS-002).
- **Fix:** Pick the two editions that earn their keep (Desktop/Core Python, Edge Go). Delete the standalone Python entry point as a shipped product — keep the module only as the base class Core imports, with no `main()` of its own (see PS2SERVERS-002) — and treat the Go port as a port, not a third design: generate its protocol constants/fixtures from the Python source of truth.
- **Benefit:** est. −3,600 lines of separately-maintained entry-point surface immediately (standalone CLI + duplicated `run()`), and one whole drift class removed from every future UDPFS fix.
- **Effort:** L. **Risk:** Med. **Confidence:** High (READ)

## PS2SERVERS-002 / Standalone UDPFS keeps its own CLI, parser and run loop purely as a rollback path
- **Category:** 8 (Half-finished / redundant feature)
- **Location(s):** `PS2-Servers/udpfs_server/udpfs_server.py:3441-3643` (`main()` + full argparse parser); `PS2-Servers/udpfs_server/ps2servers_core.py:523-586` (second parser for the same options); `PS2-Servers/udpfs_server/ps2servers_core.py:7-12` (module docstring: "The legacy udpfs_server.py entry point remains available for rollback")
- **Problem:** Two parsers describe the same ~25 options (port, bind, sector size, compression, protocol mode, peer timeout, metrics, tx delay, transfer/session caps) with two different env-var/flag precedence implementations, and the shipped launcher never uses the standalone one (`launcher/servers.py` UDPFS `module_file` points at `ps2servers_core.py`). The only stated reason to keep it is rollback and upstream redistribution.
- **Fix:** Delete `main()` and the parser from `udpfs_server.py`; keep the file as a library (it already is one — Core imports 25 names from it, `ps2servers_core.py:19-44`). Rollback is what git tags are for.
- **Benefit:** est. −200 lines, one parser and one env-precedence implementation removed; the "edited the wrong copy" failure mode disappears with it.
- **Effort:** S. **Risk:** Low. **Confidence:** High (READ)

## PS2SERVERS-003 / RetroAchievements is a second product inside this repo: ~53k lines of vendored C, ~5.2k lines of Python, two runtimes, two modes
- **Category:** 8 (Redundant / half-finished feature) / 3 (Overengineering)
- **Location(s):** `PS2-Servers/vendor/retroachievements/rcheevos/` (66 files) and `PS2-Servers/vendor/retroachievements/xerabora/` (47 files) — 52,963 lines of C total; `PS2-Servers/launcher/ra_*.py` + `achievements.py` + `achievement_loader.py` + `achievement_viewer.py` + `caduceus.py` = 5,201 lines; `PS2-Servers/launcher/achievements.py:339-341` (`--owned-runtime` flag); `PS2-Servers/launcher/servers.py:719-741` (registry entry: two modes, "Experimental — softcore only; console testing required"); `PS2-Servers/build/build_achievements.py:1-83`, `PS2-Servers/build/build_ra_evaluator.py:1-57`; 28 RA test files under `tests/`
- **Problem:** A games-server repo carries an entire achievements platform: a vendored C engine bigger than all the Python servers combined, a Python protocol/session/evaluator stack, a second ("owned") runtime offered as an opt-in preview flag alongside the bundled one, and a second console mode (Caduceus) layered on the same engine. Both runtimes and both modes must be kept wire-compatible forever, and none of it serves a file to a PS2.
- **Fix:** Ship one runtime and one mode. Delete the `--owned-runtime` preview path (or the bundled path, if the owned one is the future — but not both), and move the vendored C tree to a pinned build-time fetch (the build scripts already pin it: `build/build_achievements.py:48-49`) instead of tracking 115 C files in this repo.
- **Benefit:** est. −2,000+ Python lines for the losing runtime, −52,963 tracked C lines if vendoring becomes a fetch; clone and audit surface shrinks by more than half.
- **Effort:** L. **Risk:** Med. **Confidence:** High (READ)

## PS2SERVERS-004 / SMBv1 is implemented twice (Python and Go), with a test whose job is to keep them byte-identical
- **Category:** 2 (Duplication)
- **Location(s):** `PS2-Servers/smbv1_server/smbserver_opl.py:1-1862`; `PS2-Servers/native/ps2servers-edge/internal/smb/handlers.go` (902 lines), `server.go` (745), `wire.go` (263); `PS2-Servers/tests/test_smb_wire_parity.py:1-40` (docstring: "Edge's Go port therefore has to match *it*, not the SMB specification")
- **Problem:** Two full SMB1 servers — negotiate, session setup, tree connect, NT create, read/write, trans2 — in two languages. The parity test is an admission that the second implementation's only specification is the first one's behaviour, so every SMB1 fix is paid for twice plus once more in parity fixtures.
- **Fix:** No code fix inside either implementation; the simplification is at the product level: state plainly in `docs/EDITIONS.md` that SMB1 fixes land in Python first and the Go port is refreshed in batches, and drop per-fix parity maintenance. If Edge SMB1 has no console-validated user the Python one lacks, deleting the Go `smb` subcommand is the honest option.
- **Benefit:** est. up to −1,910 Go lines if removed; otherwise one fix-cycle per SMB1 change instead of two plus parity work.
- **Effort:** M. **Risk:** Med. **Confidence:** High (READ)

## PS2SERVERS-005 / UDPBD is implemented three times, even though UDPFS already contains it
- **Category:** 2 (Duplication)
- **Location(s):** `PS2-Servers/udpbd_server/udpbd_server.py:1-462`; `PS2-Servers/native/ps2servers-edge/internal/udpbd/server.go` (555 lines) + `protocol.go` (104); `PS2-Servers/udpfs_server/udpfs_server.py:5-6` ("UDPBD is a subset of UDPFS using block I/O messages (BREAD/BWRITE/INFO)") and its block handlers; `PS2-Servers/native/ps2servers-edge/internal/udpfs/blockdev.go` (409 lines)
- **Problem:** Block-device serving exists as a standalone Python server, a standalone Go server, and as the block subset inside both UDPFS implementations. The standalone Python server's own provenance note calls UDPBD "largely superseded by UDPFS" (`udpbd_server/SOURCE.md`), and the launcher already labels it "Legacy — prefer UDPFS" (`launcher/servers.py` UDPBD `recommendation`).
- **Fix:** Delete the standalone Python UDPBD server and serve block images through UDPFS's block device (already a registry-supported configuration). Keep the Go UDPBD only if a loader that speaks UDPBD-but-not-UDPFS is named and console-tested.
- **Benefit:** est. −462 Python lines, −179 selftest lines, one registry card, one port, one test cluster.
- **Effort:** M. **Risk:** Med (loader compatibility is the open question). **Confidence:** High (READ) for the duplication; INFERRED for which loaders still need native UDPBD

## PS2SERVERS-006 / The GUI is customised by four stacked monkey-patch layers, one installed through an import hook
- **Category:** 3 (Overengineering)
- **Location(s):** `PS2-Servers/launcher/__init__.py:20-92` (a `MetaPathFinder`/`Loader` wrapper that patches `launcher.gui` on import); `PS2-Servers/launcher/main.py:139-475` (`_apply_gui_review_fixes`: replaces `gui.ttk.Notebook`, `LauncherApp.__init__`, `LauncherApp._build`, `LauncherApp._append_log` at runtime); `PS2-Servers/launcher/asset_skin.py:35-56` (patches `Notebook.add`); `PS2-Servers/launcher/full_skin_controls.py:11-204` (patches `ServerCard._build`); `PS2-Servers/launcher/dependency_panel.py` (mounted by the `__init__` hook)
- **Problem:** What the GUI looks like cannot be determined by reading `gui.py`: four other modules rewrite its classes after import, each with its own `_patched` guard flag, and the first layer intercepts the import system itself. The comments in `main.py` record the cost — earlier rounds of this produced two admin panels, double-padded tabs and log lines written twice. This is the single largest maintainability liability in the launcher.
- **Fix:** Fold each patcher's changes into `gui.py` directly (theme application, tab styling, admin panel, tab icons, skin controls, dependency panel) and delete the patch layers and the import hook. `gui.py` grows, but the *system* shrinks: one definition per method, no guards, no wrap-order rules.
- **Benefit:** est. −460 lines of patch machinery (`__init__.py` 92, `asset_skin.py` 164, `full_skin_controls.py` 204) plus ~340 lines of shim inside `main.py`; every future GUI change becomes a normal edit.
- **Effort:** L. **Risk:** Med. **Confidence:** High (READ)

## PS2SERVERS-007 / The version of `_append_log` inside gui.py is dead — the main.py shim replaces it before the GUI runs
- **Category:** 1 (Dead code)
- **Location(s):** `PS2-Servers/launcher/gui.py:3042-3058` (`LauncherApp._append_log`); `PS2-Servers/launcher/main.py:460-475,475` (shim assigns `gui.LauncherApp._append_log = append_log`); entry path `PS2-Servers/ps2servers.py:20` → `launcher/main.py:65-72` (`_apply_gui_review_fixes(gui)` runs before `gui.run_gui()`)
- **Problem:** The only entry point patches the method before any window exists, so the in-class implementation (which scrolls unconditionally, unlike the shim's at-bottom check) never executes in the shipped app. Two implementations of log appending exist; the readable one is the wrong one.
- **Fix:** As part of PS2SERVERS-006, keep the shim's behaviour, move it into the class, delete the other copy.
- **Benefit:** est. −12 lines and one guaranteed misreading removed.
- **Effort:** S. **Risk:** Low. **Confidence:** High (READ)

## PS2SERVERS-008 / CHD/CSO/ZSO decompression is implemented twice, in Python and Go, including the CHD CUE/BIN view
- **Category:** 2 (Duplication)
- **Location(s):** `PS2-Servers/udpfs_server/compressed_iso/base.py` (196), `chd.py` (334), `chd_cuebin.py` (567), `cso.py` (152), `zso.py` (173), `limits.py` (116); `PS2-Servers/native/ps2servers-edge/internal/compression/image.go` (489), `chd_cuebin.go` (539), `chd_cuebin_enabled.go` (217), `chd_enabled.go` (87), `lz4.go` (70), `chd.go` (150); consumers in both worlds: `smbv1_server/smbserver_opl.py` (compression option), `http_server/http_server.py`, `launcher/disc_identity.py`, `launcher/ra_compressed_hash.py`
- **Problem:** The compressed-image stack — format detection, hunk/block caching, the virtual CUE/BIN view of a CD CHD, cache-budget rules — exists in two languages at ~1,550 Python lines and ~1,630 Go lines, with near-identical file splits (`chd_cuebin.py` 567 ≈ `chd_cuebin.go` 539). Format bugs (the CHD CD-size edge cases both trees test) must be fixed twice.
- **Fix:** Accept the split (Edge cannot call Python) but cut the duplicated *policy*: one shared, language-neutral test-vector set for image/chd/cuebin behaviour (the Go tree already carries binary fixtures, PS2SERVERS-016) and delete whichever per-language unit tests merely re-assert the other's vectors.
- **Benefit:** est. −500 lines of duplicated tests/fixtures; format fixes verified once instead of twice.
- **Effort:** M. **Risk:** Low. **Confidence:** High (READ)

## PS2SERVERS-009 / 159 remote branches and 118 tags, including 81 auto-generated `main-<n>-<hash>` tags and 9 `backup/*` branches
- **Category:** 7 (Repo clutter) — LIST only, per instructions
- **Location(s):** Git refs of `NathanNeurotic/PS2-Servers` at the pin (verified with `git branch -r` / `git tag` at clone time)
- **Problem:** Remote branches: 159, by prefix — `codex/` 36, `claude/` 32, `fix/` 29, `feat/` 23, `backup/` 9 (`backup/main-before-139` … `backup/main-before-142`, `backup/pre-av-cleanup-2026-06-30`, etc.), plus assorted `release/`, `docs/`, `dependabot/`. Tags: 118, of which 81 match `main-<number>-<hash>` (e.g. the `main-114-…` … `main-213-…` series) against only ~30 real `v*` release tags. Backup branches duplicate what tags and GitHub releases already preserve, and 81 snapshot tags make `git tag` output — and any tool that lists releases — mostly noise.
- **Fix:** (For the owner, not performed here.) Delete merged `codex/`/`claude/`/`fix/`/`feat/` branches, delete the `backup/*` branches, and stop minting `main-<n>-<hash>` tags (or move that bookkeeping to workflow artefacts).
- **Benefit:** Ref-listing and clone metadata shrink; release/tag tooling becomes readable again.
- **Effort:** S. **Risk:** Low. **Confidence:** High (READ — counts verified at pin)

## PS2SERVERS-010 / Two release workflows (~1,100 lines) duplicate the same build/source/edge/release pipeline
- **Category:** 7 (Build/CI clutter)
- **Location(s):** `PS2-Servers/.github/workflows/release.yml:1-606` (on `v*` tags; jobs `verify-version`, `create-release`, `build`, `source`, `edge`, `release`); `PS2-Servers/.github/workflows/release-on-main.yml:1-489` (on push to `main`; jobs `build`, `source`, `edge`, `release`); plus `edge.yml` (254 lines) and reusable `edge-build.yml` (466), `ci.yml` (152), `udpfs-transport-replay.yml` (49), `attribution-policy.yml` (70) — 7 workflows, ~1,970 lines total
- **Problem:** The per-push-to-main release and the tagged release are near-parallel pipelines (same Nuitka build matrix, libchdr build, Edge build, checksum/attestation steps), so a packaging change must be edited in two large YAML files and can silently diverge — the exact failure the repo's own version-consistency tests exist to catch.
- **Fix:** Make `release-on-main.yml` call the same reusable workflow `release.yml` is refactored into (the repo already knows this pattern: `edge-build.yml` is `workflow_call`), with a `draft/prerelease` input as the only difference.
- **Benefit:** est. −400 lines of YAML and one pipeline to keep correct.
- **Effort:** M. **Risk:** Med. **Confidence:** High (READ — job structure compared; step-by-step equivalence INFERRED)

## PS2SERVERS-011 / Session-summary statistics are implemented three times, plus a private stats dict inside every server
- **Category:** 2 (Duplication)
- **Location(s):** `PS2-Servers/launcher/session_stats.py:1-130` (`SessionStats`, shared by the Python servers, with the SESSION line format as a cross-process contract); `PS2-Servers/native/ps2servers-edge/internal/sessionstats/sessionstats.go` (the same contract in Go); per-server private counters that feed it: `PS2-Servers/udpfs_server/udpfs_server.py` (`self.stats[...]` throughout, e.g. bytes counters), `smb2_server/smb2_server.py:380-384` (`_session_record`), `udpbd_server/udpbd_server.py:188-193` (`_record`), `http_server/http_server.py:852-856` (`_stat`), `nfs4_server/nfs4_server.py` (`_admit`/`_serve` counting); parser on the receiving side: `launcher/session_stats.py` (`parse_line`) and `launcher/session_view.py`
- **Problem:** The counting rules, the SESSION line format and its parser are re-implemented per language and re-wired per server (eight `_record`/`_stat`/`_session_record` call-site families). A counter added to one server is missing from the others, and the launcher's view silently shows partial totals.
- **Fix:** In Python this is already 90% solved — finish it: make every Python server report through `SessionStats.record()` only and delete the private `self.stats` dicts where they duplicate its kinds. For Go, keep `sessionstats` but generate its line-format constants from the Python contract test rather than maintaining a second spec.
- **Benefit:** est. −150 lines and one counting implementation per language instead of one per server.
- **Effort:** M. **Risk:** Low. **Confidence:** High (READ)

## PS2SERVERS-012 / The router-status protocol is answered two different ways inside Edge, and a third way in Python
- **Category:** 2 (Duplication)
- **Location(s):** Wire format: `PS2-Servers/udpfs_server/router_status.py:1-140` (Python) and `PS2-Servers/native/ps2servers-edge/internal/protocol/status.go:112-172` (Go). Answering paths: Python in-band in the UDPFS demux (`udpfs_server/udpfs_server.py:1495-1513`, `_maybe_answer_status`, shared by standalone and Core); Go in-band for UDPFS (`native/ps2servers-edge/internal/udpfs/status.go:18-103`); Go separate listener for SMB and UDPBD (`native/ps2servers-edge/internal/statuslistener/listener.go:40-103`, used at `internal/smb/server.go:178` and `internal/udpbd/server.go:254`). Client: `launcher/status_client.py:37-108`
- **Problem:** One 19-byte status reply has three server-side implementations. UDPFS answers on its discovery socket; Edge's SMB/UDPBD open a *second* UDP listener for the same packet; Python's other servers don't answer at all, so the launcher's busy/stop-safety signal exists only for UDPFS on desktop (`status_client.py` docstring: "Only the UDPFS server implements it today").
- **Fix:** In Edge, answer in-band everywhere the discovery socket already exists and delete `internal/statuslistener`, or keep the listener and delete the UDPFS in-band special case — one mechanism. On the Python side, either wire `_maybe_answer_status` into the other servers' demuxes (it is already shared code) or document status as UDPFS-only and delete the per-server state/flag plumbing elsewhere (`router_status.py` FLAG_SMB/FLAG_UDPBD bits).
- **Benefit:** est. −150 Go lines (the listener package) and a status signal the launcher can actually rely on.
- **Effort:** M. **Risk:** Low. **Confidence:** High (READ)

## PS2SERVERS-013 / No single-writer exclusion in UDPFS — two peers can interleave writes to the same file, in every edition
- **Category:** 6 (Bug / security)
- **Location(s):** `PS2-Servers/udpfs_server/udpfs_server.py:2014-2033` (`_reset_write`: write-assembly state is per-session), `:1923-1928` (files opened with a plain `open()`, no lock/exclusive flag), `:3211-3216` (per-session `write_handle`/`write_data`); `PS2-Servers/native/ps2servers-edge/internal/udpfs/server.go:334-338` (per-peer goroutine/state model); tree-wide: no `flock`/`O_EXCL`/`LockFile` equivalent exists in `udpfs_server/` or `native/ps2servers-edge/internal/udpfs/` (grep-verified at pin)
- **Problem:** Write state is per *session*, handles are per *session*, and nothing stops two consoles (or one console plus a stale second session from the same console after a reboot) from opening the same file read-write and interleaving assembled writes. For VMC/save files — UDPFS's main write use — that is silent corruption, and the 2026-11-05 audit's trailing item is still open at this pin in all three editions.
- **Fix:** Add a per-canonical-path write claim in the server that already owns the session registry: second write-open of a claimed path gets `EBUSY` until the first handle closes or its session is reaped.
- **Justification (addition):** Concrete bug — interleaved save writes corrupt the file with no error on either console. This is the simplest fix: one claim map beside the existing session map, checked in the existing open-for-write path; no new protocol, no new options. Net win is a real bug fixed for ~30 lines per edition (and it only needs to exist in the editions that survive PS2SERVERS-001).
- **Benefit:** A corruption class removed; no lines saved (justified addition).
- **Effort:** M. **Risk:** Med. **Confidence:** High (READ — mechanism and absence both verified)

## PS2SERVERS-014 / SMB2 signing has four CMAC implementations stacked as fallbacks, plus hand-rolled MD4, AES, RC4 and SPNEGO
- **Category:** 3 (Overengineering) / 6 (Security surface)
- **Location(s):** `PS2-Servers/smb2_server/smb2_crypto.py:124-131` (dispatcher), `:132-170` (pure-Python AES-CMAC), `:173-340` (ctypes backends: Windows bcrypt `:192-236`, OpenSSL 3 `:237-305`, OpenSSL 1 `:306-340`), `:61-119` (pure-Python AES-128), `:341-360` (KDF), `:361-386` (RC4); `PS2-Servers/smb2_server/smb2_auth.py:75-141` (pure-Python MD4 + NT hash), `:141-235` (NTLMv2); `PS2-Servers/smb2_server/smb2_spnego.py:83-360` (hand-rolled DER/SPNEGO)
- **Problem:** ~750 lines of hand-rolled cryptography exist because the project is stdlib-only. The CMAC alone has four implementations selected by platform probing at first use; each backend is a separate place for a subtle, security-relevant bug, and the pure-Python paths are the ones that run exactly where no native library was found — the least-tested machines. (Honest constraint: `hashlib` on OpenSSL 3 systems genuinely lacks MD4, so "just use the stdlib" is not available for NTLM — this finding is about the fallback *stack*, not the existence of hand-rolled code.)
- **Fix:** Keep exactly two CMAC paths: one native (bcrypt on Windows / OpenSSL on Linux, chosen at import, not probed per call) and the pure-Python reference — delete the separate OpenSSL-1 backend (its install base is EOL) and let the dispatcher fail to the pure path. Keep the pure implementations, but pin them to published test vectors in one test file instead of per-backend tests.
- **Benefit:** est. −80 lines and one crypto backend removed from the audit surface; signing behaviour becomes identical across machines of the same OS.
- **Effort:** M. **Risk:** Med. **Confidence:** High (READ)

## PS2SERVERS-015 / 1,608 lines of self-tests live inside the shipped server directories and duplicate the real test suite
- **Category:** 7 (Repo clutter) / 1 (Dead weight in the shipped tree)
- **Location(s):** `PS2-Servers/udpfs_server/compression_selftest.py` (545 lines), `multiclient_selftest.py` (643), `pathguard_selftest.py` (241), `PS2-Servers/udpbd_server/selftest.py` (179); the real suite is `tests/` (122 files) and CI runs both (`.github/workflows/ci.yml:131-143` runs the four selftests as separate steps on top of the suite at `:89`)
- **Problem:** Test code ships inside the two server packages that get bundled by Nuitka and redistributed standalone, and several of these files are load-bearing for other tests (e.g. `tests/test_udpfs_write_needs_writable_handle.py` imports its fake console from `udpfs_server/multiclient_selftest.py`) — so they are neither cleanly tests nor cleanly product code.
- **Fix:** Move all four files under `tests/helpers/` (or `tests/` proper), fix the handful of importers, and delete the separate CI steps.
- **Benefit:** −1,608 lines from the shipped/bundled tree; one test layout; CI loses four steps.
- **Effort:** S. **Risk:** Low. **Confidence:** High (READ)

## PS2SERVERS-016 / ~3.1 MB of binary disc-image fixtures are tracked in the Go tree, and the repo banner is tracked twice, byte-identical
- **Category:** 7 (Repo clutter)
- **Location(s):** `PS2-Servers/native/ps2servers-edge/internal/compression/testdata/test.iso` (1,084 KB), `test_cd.chd` (1,032 KB), `test_dvd.chd` (1,028 KB); `PS2-Servers/ps2serversgithubbanner.png` and `PS2-Servers/docs/assets/ps2servers-banner.png` (571,581 bytes each; md5 `7d13770364c4192c47463f2ba0ee1ff0` for both — verified identical at pin); `PS2-Servers/launcher/assets/theme/LOGO.png` (592 KB)
- **Problem:** Three ~1 MB disc images are committed binaries whose content no reviewer can diff, and the same 560 KB banner is stored under two names for the README and the docs site.
- **Fix:** Generate the ISO/CHD fixtures in the Go test setup from a small seed (the Python suite already synthesises its CHD fixtures — `tests/fixtures/cd_chd/synthetic-*.chd` are small), and point the docs site at the root banner (or vice versa), deleting one copy.
- **Benefit:** est. −3.6 MB of tracked binary, −560 KB duplicated image.
- **Effort:** S. **Risk:** Low. **Confidence:** High (READ — sizes and hash verified)

## PS2SERVERS-017 / CLI/config parsing is re-implemented per server, per language, and per config format — at least nine parsers
- **Category:** 2 (Duplication)
- **Location(s):** Python argparse mains: `udpfs_server/udpfs_server.py:3441`, `udpfs_server/ps2servers_core.py:523`, `smb2_server/smb2_server.py:1727`, `smbv1_server/smbserver_opl.py:1674`, `nfs4_server/nfs4_server.py:1046`, `http_server/http_server.py:1131`, `udpbd_server/udpbd_server.py:381`, `launcher/achievements.py:333`, `launcher/directlink.py:1833`; Go flag sets: `native/ps2servers-edge/cmd/ps2servers-edge/main.go:129` (udpfs), `:296` (udpbd), `:350` (smb), `:471` (webui); Edge config formats: `internal/webui/config_json.go:14-53` and `internal/webui/config_uci.go:23-323` behind `config.go:134-147`, plus a generated schema (`internal/webui/schema.go:93-194`, `--dump-schema`); systemd env files: 8 under `packaging/systemd/*.env`; desktop JSON: `launcher/config.py:92-130`
- **Problem:** The same concepts (bind, port, read-only, share, verbose, session-summary) are parsed, defaulted (flag vs env vs file, in different precedence orders — compare `ps2servers_core.py:527-586`'s env-backed defaults with the standalone parser) and validated independently everywhere. Adding one option is a nine-place change, and the editions already disagree on spelling (e.g. UDPFS `--root-dir` in Python vs `--root` in Go's usage text, `main.go:35`).
- **Fix:** Within Python, one small shared `server_cli` helper for the six common options (it removes code overall: six copies of bind/port/read-only/verbose parsing collapse into one). Across languages, align flag *names* and delete the UCI/JSON schema's hand-maintained half — generate `schema.go` from the same table the flag sets use.
- **Benefit:** est. −300 lines across parsers; option changes become one-place.
- **Effort:** M. **Risk:** Low. **Confidence:** High (READ)

## PS2SERVERS-018 / Six path-containment implementations guard the shares; only one pair actually shares code
- **Category:** 2 (Duplication) / 6 (Security)
- **Location(s):** `PS2-Servers/smb2_server/smb2_paths.py:70-188` (`check_component`, `Share.resolve`) — shared by NFS via the cross-directory import at `nfs4_server/nfs4_server.py:36-44`; `PS2-Servers/smbv1_server/smbserver_opl.py:262-327` (its own `Share.resolve` with realpath + ADS/device-name rules); `PS2-Servers/udpfs_server/udpfs_server.py:1380-1440` (its own entry resolution/virtual-view logic, guarded by `pathguard_selftest.py`); `PS2-Servers/http_server/http_server.py:389-397` (`_contained`); `PS2-Servers/launcher/virtual_exfat.py:118-146` (its own traversal/nesting guards); `PS2-Servers/native/ps2servers-edge/internal/filesystem/root.go` and `internal/udpfs/pathops.go` (Go pair)
- **Problem:** "What may a client name?" is the security boundary of every server here, and it is answered six times with subtly different rules (symlinks, junctions/reparse points, ADS, `..`, device names). The one good sharing example — NFS importing SMB2's module — proves the pattern works; the others each carry their own bypass risk and their own test file.
- **Fix:** In Python, move `smb2_paths.py` to a neutral location (it is currently imported across directories by path manipulation, `nfs4_server.py:38-44`) and make SMBv1, UDPFS and HTTP use it, deleting their private resolvers. Go keeps its own (language), but its rule list should be generated from the same component-rule table the Python tests use.
- **Benefit:** est. −250 lines; the share boundary gets one audit and one test suite instead of six.
- **Effort:** M. **Risk:** Med. **Confidence:** High (READ)

## PS2SERVERS-019 / Verbose per-request logging is the default for every server, is force-migrated on for existing installs, and is pumped into the GUI every 150 ms
- **Category:** 5 (Performance) / 3 (Overengineering)
- **Location(s):** `PS2-Servers/launcher/servers.py` — `Field("verbose", …, default=True)` on six server cards (lines 239, 420, 504, 600, 647, 676) and `migrate_verbose_default()` + `VERBOSE_MIGRATION_KEY` at `:744-785` (a one-time migration that rewrites saved configs to turn verbose on); `PS2-Servers/udpfs_server/udpfs_server.py` (~115 print/event call sites, per-request when verbose); GUI pump: `launcher/gui.py:3025-3047` (`_drain_logs` rescheduled every 150 ms), `:3061-3115` (`_poll_status` every 600 ms), log widget capped at 2,000 lines (`gui.py:3053-3056` and the shim copy)
- **Problem:** A games server whose normal state is "print every request" spends its UI thread budget on string formatting, queue traffic and Tk text-widget inserts for output nobody reads during a successful session — on the low-end PCs and routers this project targets. The migration machinery (a persisted marker key honoured forever) exists only to flip that default once.
- **Fix:** Default verbose to off on all cards, delete `migrate_verbose_default` and its marker (a release note replaces it), and keep `--session-summary` as the always-available low-volume alternative it was designed to be.
- **Benefit:** est. −40 lines of migration code; per-request formatting/queue/Tk work eliminated in the default configuration (largest single GUI-responsiveness win available here).
- **Effort:** S. **Risk:** Low. **Confidence:** High (READ)

## PS2SERVERS-020 / UDPFS send path stacks two retry systems: 10 send retries with sleeps under a global send lock, over a 200-retry window retransmit loop
- **Category:** 3 (Overengineering) / 5 (Performance)
- **Location(s):** `PS2-Servers/udpfs_server/udpfs_server.py:122` (`MAX_SEND_RETRIES = 10`), `:159-176` (`_send_with_retry`: sleeps 1–10 ms between attempts, all inside `send_lock` when a tx delay is set the sleep is per packet, `:166`); `:275` (`MAX_WINDOW_RETRIES = 200`), `:2948-3023` (window ACK wait/retransmit loop); Core adds a third timing mechanism, a thread-per-discovery fallback INFORM timer (`udpfs_server/ps2servers_core.py:236-260`)
- **Problem:** UDP sends that fail transiently (ENOBUFS) are retried synchronously while holding the lock every other session's sends need, so one congested moment stalls all consoles; above it, the UDPRDMA window layer already provides retransmission, which is the layer the protocol expects to absorb loss. The retry-under-lock turns a per-packet problem into a server-wide stall.
- **Fix:** Drop `_send_with_retry` to a single send (keep the tx-delay pacing, outside the lock if possible) and let the window layer retransmit, as Edge does. Keep `MAX_WINDOW_RETRIES` — it is the protocol's own mechanism.
- **Benefit:** est. −25 lines; worst-case send-lock hold drops from ~55 ms of sleeps per packet to one syscall (estimate).
- **Effort:** S. **Risk:** Med (send behaviour on congested Windows hosts is the thing to console-test). **Confidence:** High (READ) for the mechanism; INFERRED for the stall magnitude

## PS2SERVERS-021 / UDPFS allocates a thread, a queue and up to 64 handles per unauthenticated datagram, up to 256 sessions
- **Category:** 5 (Performance) / 6 (Security)
- **Location(s):** `PS2-Servers/udpfs_server/udpfs_server.py:228` (`MAX_HANDLES = 64` per client), `:255-267` (`DEFAULT_MAX_SESSIONS = 256`; the comment itself notes a session "is created by an unauthenticated datagram"), `:3239-3240` (one thread per session), `:898-899` (same admission in `_get_or_create_session`)
- **Problem:** The caps are bounded, which is good, but the bound is 256 threads and up to 16,384 open descriptors (256 × 64, INFERRED arithmetic) on a home PC, reachable by any device on the LAN spraying source ports — while the code's own comment says real deployments are "single digits" of consoles. On Windows, thread + socket churn at that scale is also the GUI-freeze and antivirus-flag profile the project fights elsewhere.
- **Fix:** Lower the default cap to 32 sessions (the `--max-sessions` option already exists for anyone who truly runs more) — a one-number change, no new code.
- **Benefit:** Worst-case thread/descriptor ceiling drops 8×; no behaviour change for any real deployment.
- **Effort:** S. **Risk:** Low. **Confidence:** High (READ); the 16,384 figure is INFERRED from the two caps

## PS2SERVERS-022 / Connection admission is capped in NFS (32) and UDPFS (256) but uncapped in SMB1, SMB2 and HTTP
- **Category:** 6 (Bug / security) / 5 (Performance)
- **Location(s):** Caps that exist: `nfs4_server/nfs4_server.py:54` (`MAX_CONNECTIONS = 32`, with eviction), `udpfs_server/udpfs_server.py:263`. Thread-per-connection with no cap found: `smbv1_server/smbserver_opl.py:1844`, `smb2_server/smb2_server.py:360-361`, `http_server/http_server.py:1097-1098` (`ThreadingHTTPServer`, `daemon_threads = True`)
- **Problem:** Three of five servers will spawn an unbounded number of handler threads for anyone who can reach the port; a LAN device (or a port-forward mistake) can exhaust the host's threads/memory while the other two servers demonstrate the intended pattern.
- **Fix:** Reuse the NFS pattern (a small connection counter + refuse/evict in the accept loop) in the three accept loops — ~10 lines each, copied from in-repo code, no new abstraction.
- **Justification (addition):** Concrete risk with an in-repo proven fix; net win is a DoS ceiling where none exists.
- **Benefit:** Bounded worst-case memory/threads on all servers.
- **Effort:** S. **Risk:** Low. **Confidence:** High (READ) for the capped servers and the thread-per-connection sites; the *absence* of caps elsewhere is INFERRED from grep over the three server files

## PS2SERVERS-023 / Default posture is guest/writable/open on a server meant to be found by broadcast
- **Category:** 6 (Security)
- **Location(s):** `PS2-Servers/smbv1_server/smbserver_opl.py:11-13` (docstring: guest logons, "Writable by default"), `:1563-1572` (empty user map = every SESSION_SETUP accepted); `PS2-Servers/launcher/servers.py` SMB2 fields (`open_share` "anyone on the network can read it"); `PS2-Servers/nfs4_server/nfs4_server.py:20` and `:1060-1087` (no password, warnings only when writes are on); `PS2-Servers/http_server/http_server.py` (no authentication anywhere); `PS2-Servers/udpfs_server/udpfs_server.py:255` (unauthenticated session creation)
- **Problem:** Individually each is a documented LAN-trust decision with honest in-UI warnings (which is to the project's credit). Together they mean the default install — all interfaces, SMB1 writable, guest-accepted — exposes a writable view of the user's games folder to the whole LAN, and SMB1's optional password travels in the clear (noted in `launcher/servers.py` SMBv1 password help). Nothing here is a code bug; it is a defaults finding, rated honestly as such.
- **Fix:** Flip exactly one default: SMBv1 read-only unless the user ticks write (OPL reads games read-only; saves/VMC are the exception, not the rule). Leave the rest as documented choices.
- **Benefit:** The default install stops being LAN-writable; no code added.
- **Effort:** S. **Risk:** Low. **Confidence:** High (READ)

## PS2SERVERS-024 / SMBv1 can stop Windows' own file-sharing service (LanmanServer) via PowerShell to take port 445
- **Category:** 8 (Feature whose cost exceeds its value) / 6 (Security)
- **Location(s):** `PS2-Servers/smbv1_server/smbserver_opl.py:1576-1670` (`_take_445`, `wait_stopped` polling every 0.25 s, `restore()`), the same option on the SMB2 card (`launcher/servers.py` `take_445` fields) and `smb2_server/smb2_server.py` (take-445 handling); GUI admin panel and elevation flow built to support it (`launcher/main.py:296-340`, `launcher/elevate.py`, `launcher/windows_setup.py` 458 lines)
- **Problem:** A game-file server carries a feature that stops the host OS's file sharing, restarts it afterwards, polls service state, needs elevation, needs a cooperative-stop pipe so the restore runs, and needs its own failure UI — hundreds of lines across four files and the single largest support-risk surface in the product, for a port number the custom-port mode (1025/1445) already avoids for the actual console.
- **Fix:** Delete `--take-445` from both servers and the launcher fields. Windows Explorer browsing is served by SMB2 on 445 only when the user runs it elevated manually; the console path never needed it. If it must stay, it belongs in a separate helper tool, not in every server process.
- **Benefit:** est. −400 lines across `smbserver_opl.py`, `windows_setup.py` and GUI admin plumbing; the elevation prompt and the "my shares vanished" failure class disappear.
- **Effort:** M. **Risk:** Med. **Confidence:** High (READ)

## PS2SERVERS-025 / Direct-link mode is a second network-administration product inside the launcher: 1,927 lines plus ~600 lines of GUI state machine
- **Category:** 8 (Scope) / 3 (Overengineering)
- **Location(s):** `PS2-Servers/launcher/directlink.py:1-1927` (adapter enumeration ×3 OSes `:234-613`, subnet selection, DHCP responder, rehome protocol); `PS2-Servers/launcher/gui.py:1794-2395` (the `_direct_link_*` method family: begin/restore/recovery/startup/pending states); `PS2-Servers/launcher/serve.py:150-153` (the DHCP helper smuggled through the server re-exec path as a non-registry key)
- **Problem:** Configuring a point-to-point Ethernet link (enumerate adapters, pick a subnet, run DHCP, restore the NIC afterwards, recover after a crash) is an OS-administration tool living inside a game-server launcher, marked experimental on two of three OSes in the launcher's own About text (`launcher/main.py:106-112`). It accounts for roughly a fifth of the launcher's code and most of its startup-recovery complexity (five separate `after(350, …_pending)` recovery probes at `gui.py:1054-1080`).
- **Fix:** Split it out: a standalone `ps2servers-directlink` helper the launcher can launch but does not import, or delete the Unix paths (self-described as experimental/unverified) and keep the Windows path only. Either way the launcher's startup stops paying for it.
- **Benefit:** est. −1,900 lines from the launcher package (split) or −600 lines (Unix paths only); GUI startup sheds five recovery probes.
- **Effort:** L. **Risk:** Med. **Confidence:** High (READ)

## PS2SERVERS-026 / There are two SQLite game-library catalogues and three library UIs
- **Category:** 2 (Duplication) / 8 (Half-finished)
- **Location(s):** `PS2-Servers/launcher/game_library.py:1-497` (SQLite at `:63`, `achievement-catalog.sqlite3`, with `:178-224` backup/import); `PS2-Servers/launcher/library_catalog.py:1-642` (a second SQLite catalogue with its own `:582-630` backup/import); `PS2-Servers/launcher/library_index.py` (index over the same folders); UIs: `library_gui.py` (446), `library_panel.py` (561); support: `disc_identity.py` (239), `art_fetch.py` (219), `ps1_index.py`, `ps1_vcd.py`, `library_catalog.py`
- **Problem:** Two modules each own a SQLite database of the user's games, each with backup/restore/import, plus a third index module and two panel/window implementations. A user with a library has two catalogues that can disagree, and every schema or identity fix (disc identity, compressed hashing) lands in two data models.
- **Fix:** Pick the catalogue the Library tab actually opens, migrate the other's tables into it once, and delete the loser plus whichever of `library_gui`/`library_panel` duplicates it.
- **Benefit:** est. −600 to −1,000 lines; one game database.
- **Effort:** L. **Risk:** Med. **Confidence:** High (READ) for the duplication of mechanism; which one is canonical is INFERRED (both are wired into the GUI)

## PS2SERVERS-027 / Three separate HTTP servers serve three management/achievement pages
- **Category:** 2 (Duplication)
- **Location(s):** `PS2-Servers/launcher/achievement_viewer.py:88-99` (`ThreadingHTTPServer` for the LAN viewer); `PS2-Servers/launcher/ra_web.py:545-560` (a second `ThreadingHTTPServer` for the owned-runtime web UI); `PS2-Servers/native/ps2servers-edge/internal/webui/webui.go` + `api.go` (461) + `service.go` (350) (Edge's dashboard); the game-serving `http_server/http_server.py:1097` is a fourth, legitimate one
- **Problem:** Three status/page servers with three auth postures (all LAN-open), three log buffers and three start/stop lifecycles, all answering "what is the server doing" for different subsystems.
- **Fix:** On the desktop, serve the achievement viewer and the RA pages from one small HTTP host with two route prefixes; delete one `ThreadingHTTPServer` subclass and its lifecycle code. (Edge's webui is genuinely separate — different binary — and is counted in PS2SERVERS-028, not here.)
- **Benefit:** est. −200 lines; one LAN-exposed page server to audit instead of two.
- **Effort:** M. **Risk:** Low. **Confidence:** High (READ)

## PS2SERVERS-028 / Edge's WebUI is a second management plane: ~2,050 lines plus its own config store, schema and service supervisor
- **Category:** 2 (Duplication) / 3 (Overengineering)
- **Location(s):** `PS2-Servers/native/ps2servers-edge/internal/webui/webui.go` (298), `api.go` (461), `service.go` (350), `config.go` (264), `config_uci.go` (323), `config_json.go` (53), `schema.go` (194), `logbuffer.go` (97), `assets/` (embedded HTML/CSS/JS); entry `cmd/ps2servers-edge/main.go:471-530`
- **Problem:** Edge — sold in `docs/EDITIONS.md` as the "small, statically linked" edition — embeds a dashboard that can reconfigure and restart the servers, backed by two config backends (JSON and UCI), a hand-maintained JSON schema that must agree with both the flags and the UCI keys, and a service supervisor. That is a second desktop GUI, in Go, with a config-format surface (PS2SERVERS-017) larger than some of the servers it manages.
- **Fix:** Cut the WebUI down to status + logs (read-only) and let UCI/files remain the configuration path on OpenWrt, where a config UI already exists (LuCI). Delete `api.go`'s write endpoints, `service.go`'s supervisor and one of the two config backends' save paths.
- **Benefit:** est. −800 lines; Edge's attack surface (an HTTP config writer) shrinks to read-only.
- **Effort:** M. **Risk:** Med. **Confidence:** High (READ)

## PS2SERVERS-029 / Scope creep around the servers: Discord Rich Presence, achievement sounds, art fetching, OBS export
- **Category:** 8 (Half-finished / redundant features)
- **Location(s):** `PS2-Servers/launcher/discord_presence.py:1-286` (hand-rolled Discord IPC over ctypes/socket, with its own allow-list sanitiser `:22-31`); `launcher/ra_sounds.py`; `launcher/art_fetch.py:1-219` (cover-art downloads, with its own retry/backoff at `:132`); OBS export folder option (`launcher/servers.py` RetroAchievements `obs_folder` field, `launcher/achievements.py:337`)
- **Problem:** None of these serve a file to a PS2. Each is a small network/OS-integration surface (Discord IPC, outbound art HTTP, streaming-overlay files) maintained inside a file-server launcher, each with its own thread and failure modes in the GUI process.
- **Fix:** Delete Discord presence outright (it needs a per-game privacy sanitiser to be safe — that tells you the cost/benefit). Move art fetching into the library tooling that already owns identity (PS2SERVERS-026) instead of the launcher core.
- **Benefit:** est. −500 lines and two background threads out of the GUI process.
- **Effort:** S. **Risk:** Low. **Confidence:** High (READ)

## PS2SERVERS-030 / `launcher/serve.py` carries a 45-module explicit import list whose only job is to appease the freezer
- **Category:** 3 (Overengineering) / 7 (Build)
- **Location(s):** `PS2-Servers/launcher/serve.py:15-59` (explicit imports of `array`, `email.utils`, `getpass`, `gzip`, `html`, `mimetypes`, `quopri`, `uuid`, … "so PyInstaller, Nuitka, and static bundlers include them")
- **Problem:** The re-exec architecture (one binary that re-launches itself as every server) forces a hand-maintained import manifest: miss one module and a packaged server fails at runtime on a user's machine — the failure this list exists to prevent, and which `tests/test_frozen_imports.py` also guards. It is a tax the single-binary design pays on every new server feature.
- **Fix:** Generate the list in `build/build.py` from the server modules' actual imports (ast walk) and check it into `serve.py`, replacing the hand list; or drop one-file packaging for a one-folder build and delete the list entirely.
- **Benefit:** −45 hand-maintained lines and a whole "packaged build misses a module" bug class.
- **Effort:** M. **Risk:** Med. **Confidence:** High (READ)

## PS2SERVERS-031 / Two spellings of headless mode are kept forever: `serve` and `--serve`, normalised in two places
- **Category:** 3 (Overengineering)
- **Location(s):** `PS2-Servers/ps2servers.py:27-40` (`_normalize_headless_alias`); `PS2-Servers/launcher/main.py:44-53` (`--serve` handling); `PS2-Servers/launcher/serve.py:138-190` (dispatch, including the non-registry `directlink` key); `PS2-Servers/launcher/servers.py:40-45` (`serve_command` re-exec construction)
- **Problem:** A compatibility alias for the project's own internal re-exec spelling is now public API ("remains supported for packaged re-execution and existing automation", `ps2servers.py:9-11`), so argument handling is split across the entry script, `main()` and the dispatcher, and every future flag must be checked against both routes.
- **Fix:** Keep `--serve` as the only internal spelling (it is what `serve_command` emits) and let `ps2servers serve …` be a thin documented subcommand handled in exactly one place (`ps2servers.py`), deleting the `--list`/`-l` dual spelling on the way (`main.py:55`).
- **Benefit:** est. −30 lines; one argument path to test.
- **Effort:** S. **Risk:** Low. **Confidence:** High (READ)

## PS2SERVERS-032 / Portable-mode detection writes a probe file on every config access
- **Category:** 5 (Performance)
- **Location(s):** `PS2-Servers/launcher/config.py:32-49` (`_portable_dir()` creates the data dir and writes a `TemporaryFile` probe to prove writability), called from `config_dir()`/`config_path()`/`is_portable()` (`:63-72`) and therefore from every `load()` (`:92-106`) and `save()` (`:106-130`)
- **Problem:** Probing is the right check, but it runs on every settings read/write rather than once per process: directory creation + a file create/write/delete per call, on the GUI thread, for a property (portable-or-not) that cannot change while the app runs. On a slow or network home directory this is avoidable I/O in the startup and save paths.
- **Fix:** Cache the probe result for the process lifetime (one `functools.lru_cache` on `_portable_dir`, or a module-level sentinel). No new mechanism — the result is already treated as stable everywhere else.
- **Benefit:** Config I/O per call drops to the JSON file alone (estimate: 2 syscalls + a create/delete saved per access).
- **Effort:** S. **Risk:** Low. **Confidence:** High (READ)

## PS2SERVERS-033 / SMB2's IPC$ carries 289 lines for exactly one RPC call
- **Category:** 8 (Half-finished feature)
- **Location(s):** `PS2-Servers/smb2_server/smb2_srvsvc.py:1-289`; admitted in `PS2-Servers/smb2_server/smb2_server.py:36-38`: "IPC$ carries one pipe, srvsvc, and answers one call on it, NetrShareEnum… No other pipe exists"; pipe plumbing in `smb2_server.py:979-999` (`_create_pipe`)
- **Problem:** A DCE/RPC pipe implementation, NDR marshalling and a share-enumeration service exist solely so a client can list shares it could also learn from the launcher screen. It is the kind of completeness-for-its-own-sake that has to be maintained against every SMB2 change.
- **Fix:** Delete `smb2_srvsvc.py` and the IPC$ tree; answer share-enumeration attempts with the existing not-implemented status. (If a real client is shown to require it on console, keep it — but that client should be named in the docstring, replacing the current justification-free existence.)
- **Benefit:** est. −289 lines plus the pipe branch of `_dispatch`.
- **Effort:** S. **Risk:** Med (Windows Explorer's server enumeration is the plausible consumer — unverified). **Confidence:** High (READ) for size/purpose; INFERRED for who consumes it

## PS2SERVERS-034 / Go's only module dependency, `golang.org/x/net`, exists for one 27-line Linux batch-write file
- **Category:** 4 (Size / dependencies)
- **Location(s):** `PS2-Servers/native/ps2servers-edge/go.mod` (`require golang.org/x/net v0.35.0`, plus indirect `golang.org/x/sys v0.30.0`); sole use: `PS2-Servers/native/ps2servers-edge/internal/udpfs/udpwrite_linux.go:1-27` (`ipv4.NewPacketConn` + `WriteBatch`); non-Linux stub returns "unsupported" (`udpwrite_other.go:1-11`)
- **Problem:** A whole external module (and its checksum chain in `go.sum`) is vendored into every Edge build for `sendmmsg`-style batching on one platform, in a project whose other Go code is otherwise stdlib-only.
- **Fix:** Implement the batch write with `golang.org/x/sys/unix`-free raw syscalls — or simpler, loop `WriteTo` per packet (the non-Linux path already degrades to per-packet behaviour) and delete the dependency. Measure first on the router target; if batching wins measurably, keep it and close this finding with the number.
- **Benefit:** −1 direct + 1 indirect module from `go.mod`/`go.sum`; Edge becomes stdlib-only.
- **Effort:** S. **Risk:** Low. **Confidence:** High (READ)

## PS2SERVERS-035 / Build and packaging surface: four build scripts, three init systems, Docker and OpenWrt for one app
- **Category:** 7 (Build/repo clutter)
- **Location(s):** `PS2-Servers/build/build.py` (305), `build_achievements.py` (83), `build_libchdr.py` (151), `build_ra_evaluator.py` (57), `build/achievements.nuitka-package.config.yml`; `PS2-Servers/packaging/` (21 files: `systemd/` 8 env + 3 units incl. a template unit, `openrc/` 3, `openwrt/` 4, `docker/Dockerfile.edge`)
- **Problem:** Every packaging target is plausible alone; together they mean a flag rename (PS2SERVERS-017) must be mirrored into 8 env files, a UCI config, an OpenWrt Makefile, a Dockerfile and two systemd templates, and the test suite now carries packaging-shape tests (`tests/test_systemd_units.py`, `test_packaging_data_files.py`) just to keep the copies agreeing.
- **Fix:** Generate the systemd env files and OpenWrt config from the Edge `--dump-schema` output at release time (the schema machinery already exists, PS2SERVERS-028) and delete the hand-maintained copies plus their shape tests.
- **Benefit:** est. −300 lines of packaging copies and 2 test files' worth of agreement-checking.
- **Effort:** M. **Risk:** Low. **Confidence:** High (READ)

## PS2SERVERS-036 / Hand-rolled tray, twice, plus a third status-dot mechanism in the GUI
- **Category:** 2 (Duplication)
- **Location(s):** `PS2-Servers/launcher/tray.py:1-270` (Windows tray + Linux fallback contract); `PS2-Servers/launcher/tray_linux.py:1-198` (pystray backend "matching the Windows tray contract"); tab status dots generated as images in `launcher/gui.py:1715-1743` with a separate poller (`launcher/status_client.py:121-201`)
- **Problem:** Two tray implementations maintain the same Open/Quit contract against two backends, one of which is documented in the GUI's own About text as "experimental… off by default" on Linux (`launcher/main.py:106-112`). The Linux tray duplicates the Windows one's menu/state logic rather than sharing it, because the backends differ underneath a contract the code states but does not enforce in one place.
- **Fix:** Delete `tray_linux.py` and the Linux tray option (window-close quits, as it does on tray-less desktops already), or extract the shared menu/state into `tray.py` and leave `tray_linux.py` as a ~50-line backend adapter.
- **Benefit:** est. −150 to −198 lines.
- **Effort:** S. **Risk:** Low. **Confidence:** High (READ)

## PS2SERVERS-037 / HTTP game index rescans the tree by signature on a staleness check inside request handling
- **Category:** 5 (Performance)
- **Location(s):** `PS2-Servers/http_server/http_server.py:398-434` (`_signature_of`: stats every scanned file), `:530-543` (`refresh`), `:742-763` (`_refresh_if_stale` called from `games_csv`/`lookup`), `:319-379` (directory walk)
- **Problem:** Freshness is decided by stat-ing the whole game tree, and the check sits on the read path: a large library (thousands of images, possibly on a network drive) pays a full-tree stat sweep whenever the index is considered stale, in the request that a console is waiting on.
- **Fix:** Move the sweep to a background timer (the server already runs a stats-expiry thread, `http_server.py:1206`) and serve the last built index meanwhile; consoles tolerate a list that is one interval old.
- **Benefit:** Game-list latency stops scaling with library size (estimate; magnitude depends on library/drive).
- **Effort:** S. **Risk:** Low. **Confidence:** High (READ) for the mechanism; INFERRED for user-visible magnitude

## PS2SERVERS-038 / The "standalone" UDPBD server is not standalone: it imports the launcher package at runtime
- **Category:** 3 (Overengineering / layering)
- **Location(s):** `PS2-Servers/udpbd_server/udpbd_server.py:415-425` (raw-device and virtual-exFAT modes do `from launcher.raw_storage import open_raw_device` and construct `VirtualExfat` from `launcher.virtual_exfat`); `PS2-Servers/launcher/raw_storage.py:1-427`; `PS2-Servers/launcher/virtual_exfat.py:1-325`
- **Problem:** Every other server directory is self-contained (SMB1/SMB2/UDPFS import only siblings/stdlib), but UDPBD's two advanced modes reach back into the GUI package — so the redistributable server silently depends on 750 lines of launcher code, and `launcher/serve.py`'s path-loading has to make that import work in frozen builds.
- **Fix:** Move `raw_storage.py` and `virtual_exfat.py` under `udpbd_server/` (the launcher already imports server-tree modules by path elsewhere — `launcher/status_client.py:37-75` does exactly this for `router_status.py`) and have the launcher load them the same way.
- **Benefit:** One dependency direction (launcher → servers) instead of a cycle; no lines added, est. −0 net but a layering violation removed.
- **Effort:** S. **Risk:** Low. **Confidence:** High (READ)

## PS2SERVERS-039 / Agent/tooling and handoff clutter tracked in the repo root and docs
- **Category:** 7 (Repo clutter)
- **Location(s):** `PS2-Servers/.coderabbit.yaml` (9,084 bytes), `PS2-Servers/.claude/settings.json` (attribution settings), `PS2-Servers/AGENTS.md` + `CLAUDE.md` (1,254 bytes each, near-identical purpose), `PS2-Servers/docs/ISSUE-249-HANDOFF.md`, `RA-COMPATIBILITY-HANDOFF.md`, `UDPFS-HANDOFF-DIAGNOSTICS.md`, `PLAN-NBD-AND-PS2CLIENT.md`, `NETWORK-TOOLS-ROADMAP.md`, `FULL-TEST-RUN.md` — among 65 files under `docs/`
- **Problem:** Working notes, handoffs and roadmaps for features that do not exist (NBD, ps2client) are tracked alongside the user documentation and published by the docs site pipeline; two root files exist to instruct AI tools, in a repo whose own `AGENTS.md` is an attribution policy.
- **Fix:** Move handoff/plan docs out of `docs/` (repo wiki or a `notes/` excluded from the site build), and merge `CLAUDE.md` into `AGENTS.md`.
- **Benefit:** est. −6 published pages of internal notes; docs site shrinks to user documentation.
- **Effort:** S. **Risk:** Low. **Confidence:** High (READ)

## PS2SERVERS-040 / Example credential literal in the SMB2 module docstring
- **Category:** 6 (Security hygiene)
- **Location(s):** secret-like value at `PS2-Servers/smb2_server/smb2_server.py:44`
- **Problem:** The usage example in the module docstring embeds a `user:password`-shaped literal. It reads as a placeholder, but it is exactly the string shape secret scanners (and this audit's rules) must flag, it trains users to put passwords on command lines (visible in process lists and shell history — the launcher path has the same property via `--user` in `launcher/servers.py` `_smb_argv`/`_smb2_argv`), and it will false-positive every future scanner run.
- **Fix:** Change the example to `--user 'name:<password>'`-style placeholder text, and prefer the already-supported config/env route in docs over CLI passwords.
- **Benefit:** Scanner noise and one bad habit removed; no behaviour change.
- **Effort:** S. **Risk:** Low. **Confidence:** High (READ — value not reproduced here, per audit rules)

---

## Test results (run at pin, read-only)

- **Python:** `python3 -m unittest discover -s tests` — **1,544 tests ran in 108.6 s: FAILED (failures=3, errors=71, skipped=78)**. Root cause established for the overwhelming majority: this sandbox blocks UDP `sendto` (`PermissionError: [Errno 1] Operation not permitted`, reproduced in `tests/test_udpfs_write_needs_writable_handle.py` and `tests/test_status_client.py`), which fails every live-socket test (UDPFS wire tests, `test_router_status.LiveServer`, `test_ra_owned_service`, `test_status_client` — the 3 failures are all `test_status_client.Query`). These are **environmental, not repo defects**; the suite needs a host where loopback UDP is permitted for a true pass/fail. `pytest` is not installed in this VM, so the unittest runner was used. No per-file breakdown of the remaining errors was captured beyond the UDP cluster — stated plainly as a gap.
- **Go:** **not run** — no Go toolchain in this VM (`go: command not found`). All Go findings rest on reading, not execution.
- **Builds:** no Nuitka/packaging build attempted (would write artefacts into the tree, against the read-only rule).

## Files not read, and why

- `vendor/retroachievements/**` (115 files, ~53k lines): inventoried and sized, not line-read — it is pinned third-party code; the finding about it (PS2SERVERS-003) is about its presence and duplication of role, not its internals.
- Most of `native/ps2servers-edge/**` tests (9,564 lines) and `tests/**` (27,441 lines): sampled for structure and for the parity/selftest findings; not exhaustively read.
- `launcher/gui.py` read in sections (class/method map, log/status pumps, direct-link family), not all 3,786 lines line-by-line; same for `launcher/directlink.py` (1,927 — method map + OS enumeration sections) and `smb2_server/smb2_server.py` handler bodies beyond dispatch/auth.
- `docs/**` (65 files): inventoried; only `EDITIONS.md` and `ROUTER-STATUS.md`-adjacent material read in full.
- `conformance/**`: inventoried (≈2,519 lines), runners sampled, C probes (`*.c.in`) not read.
- Binary assets (PNG/CHD/ISO fixtures): hashed/sized only.

## Unfinished categories

- **Category 4 (size/memory) inside Edge:** no Go build was possible, so no binary-size or RSS numbers exist — all size claims are source-line estimates.
- **Category 5 (GUI responsiveness):** assessed from code (poll intervals, log pump, config probing), not from a running GUI — no Tk in this VM.
- **Category 6 (protocol correctness on the wire):** no live PS2/Neutrino/OPL interop was possible; wire claims rest on code reading and the repo's own conformance tests, which could not be run for UDP (see test note).
- **Per-handler SMB2/NFS opcode completeness:** dispatch tables were mapped, individual opcode bodies were not each audited line-by-line.
