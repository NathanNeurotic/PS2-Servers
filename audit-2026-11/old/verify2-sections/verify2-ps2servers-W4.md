# FULL-VERIFICATION — PS2-Servers W4 (PS2SERVERS-076 … 094)

Pin checked before any work: `git rev-parse HEAD` = `c5f021c4d38698860f56b03498adc261dbb8d8dd`, `git status --porcelain` empty (re-checked after all work — still clean). All executions ran in a `/tmp/w4` copy, never in the clone. Sandbox note: UDP sendto is blocked here; the one UDP-bound execution (085) timed out for that reason alone and is not used as evidence either way — that finding is decided by code reading, which is decisive for a vacuity claim.

## 1. Verdict table

| ID | Verdict | Evidence (one line: key file:line at pin, or command + result) |
|---|---|---|
| PS2SERVERS-076 | VERIFIED | `udpfs_server/pathguard_selftest.py:189-192` returns after printing "skipped" on non-nt; `:231-237` prints `ALL UDPFS PATH-GUARD TESTS PASSED` unconditionally; executed in /tmp copy: output shows `[unc] skipped (Windows ntpath semantics only)` followed by `ALL UDPFS PATH-GUARD TESTS PASSED`; its only CI runs are ubuntu (ci.yml:141, edge.yml:74) |
| PS2SERVERS-077 | VERIFIED | `internal/smb/limits_test.go:63-93,94-110,111-130` assert only continued SUCCESS/answering at 128–256 ops, never a live count vs `MaxSearches 16 / MaxOpenFiles 64 / MaxTrees 16` (`internal/smb/wire.go:138,141,143`); direct-bound pattern exists at `internal/sessionstats/sessionstats_test.go:69`; all three tests pass at pin (go test, /tmp copy) |
| PS2SERVERS-078 | VERIFIED | Independent census = exactly 52 guarded methods: gui_layout 12 (setUpClass SkipTest :38), autostart 9, settings_feedback 8, library_panel 11 of 13 (unguarded: :128, :161), reset-settings GUI class 7, about 1, session_view panel class 2, protocol_mode_selector 1, riptopl WindowTests 1; only discovery job is headless ubuntu (ci.yml:89-97, workflow comment blesses the self-skip) |
| PS2SERVERS-079 | VERIFIED-DRIFT | `tests/test_udpfs_bind_control.py:11-20` setUp skipTests on no-tkinter/no-display before either body; no CI job runs it with a display — but the filed "no python3-tk step in any workflow" grep claim is wrong (see reasoning) |
| PS2SERVERS-080 | VERIFIED | `tests/test_chd_cd_size.py:20` `skipUnless(LIBCHDR_AVAILABLE)` on the only class (4 tests); ci.yml discovery at :97 precedes the libchdr smoke build at :143-144, and the post-build step :146-152 names only test_ra_compressed_hash / test_disc_identity tests; `grep test_chd .github/workflows/` = no hits anywhere |
| PS2SERVERS-081 | VERIFIED | `tests/test_retroachievements.py:254` `skipUnless(engine_path().is_file(), …)` on NativeEngineTests (5 tests, census-confirmed); sibling hard-fails present at test_ra_evaluator.py:27-29, test_ra_owned_service.py setUpClass (~:202), test_ra_dependency.py:17-19 — all `GITHUB_ACTIONS`+Linux `AssertionError`/`fail`; the class lacks any equivalent; engine job runs this module at ci.yml:37-39 |
| PS2SERVERS-082 | VERIFIED | `internal/filesystem/root_test.go:217-234` skips unless `runtime.GOOS == "windows"`; the only `go test` invocations in any workflow are edge.yml:56 and :82, both `ubuntu-latest`; ci.yml Windows jobs run Python only; edge-build.yml cross-compiles but runs no tests |
| PS2SERVERS-083 | VERIFIED | `internal/smb/limits_test.go:17-61`, own caveat at :29-33 ("meaningful on 64-bit only as a clamp check"); no workflow sets `GOARCH=386` for a test step (GOARCH appears only in edge-build.yml build matrices :266,330,375); executed on amd64 in /tmp copy: PASS (clamp only, as filed) |
| PS2SERVERS-084 | VERIFIED | `tests/test_desktop_instance.py:31,74` `skipUnless(platform.system() == "Windows")` on both real-mutex tests; ci.yml:97 discovery is ubuntu-only; Windows fixed lists at ci.yml:39 and :50 omit this module (grep-confirmed) |
| PS2SERVERS-085 | VERIFIED | `internal/udpbd/udpbd_test.go:466-489` asserts only `len(got) != SectorSize` after INFO, never bytes or write state; the named behaviour is `internal/udpbd/server.go:353` (`s.write = writeState{}`); every other readSectors site byte-compares (:300-305, :361, :382). Execution attempt was UDP-blocked in this sandbox (i/o timeout — environment effect, not evidence) |
| PS2SERVERS-086 | VERIFIED | `internal/webui/webui_test.go:638-668` constructs `Config{Port: 0}`, sleeps 100 ms, cancels, asserts only `Serve` returned nil — never dials or reads a bound address; behaviour at `internal/webui/webui.go:238-266` (Port>0 binds it; Port==0 tries DefaultPort 8082, else ephemeral); executed in /tmp copy: PASS in 0.10s |
| PS2SERVERS-087 | VERIFIED | `internal/smb/integration_test.go:65-70`: `tryTrans2` calls `cl.trans2` then `return p, d, nil` unconditionally, despite its "without the t.Fatal" comment; wrapped `trans2` Fatal's on non-success at :175-177; whole-tree grep: sole callers are `limits_test.go:84,89`, both guarding the never-non-nil err. (Cat-1 check: the helper is live-called, but as filed the dead thing is its error return / callers' error branches — confirmed) |
| PS2SERVERS-088 | VERIFIED | `tests/test_netinfo_and_smb.py:87-98` docstring states in present tense "SMB2/SMB3 currently negotiate… then serve nothing"; loop at :102-105 skips every dialect < 2; offered v2/v3 modes load the real server (`launcher/servers.py:430-458`), whose `_HANDLERS` include real READ + QUERY_DIRECTORY (`smb2_server/smb2_server.py:1432-1450`); the described fake is `smbv1_server/smbserver_opl.py:626-639` — its SMB2_HANDLERS has no 0x000E QUERY_DIRECTORY and `h2_read` (:600) returns `b""` — a module this test never loads |
| PS2SERVERS-089 | VERIFIED | `tests/test_udpfs_config.py:276-282` asserts only `getsockopt(SO_RCVBUF) > 0` after widening; the file's own `_FakeSock` docstring at :232-234 states the flaw verbatim; executed: fresh socket's SO_RCVBUF is 212992 before any widening, and the test passes at pin |
| PS2SERVERS-090 | VERIFIED | `tests/test_router_status.py:176-178` `except subprocess.TimeoutExpired: self.skipTest("go run timed out")`; the legitimate no-toolchain skip is at :142 and CI pre-empts it (ci.yml:71 setup-go in python-checks); non-timeout failure already `self.fail`s at :180 |
| PS2SERVERS-091 | VERIFIED | `tests/test_smb2_banner.py:125-132`: all three assertions inside `for ip in _local_ipv4_addresses():` with no non-empty guard and no mock; empty result is a contemplated state (neighbouring test :112-118 mocks `[]`; caller handles it at smb2_server.py:141-145); executed here: function returned `['198.19.0.2']` — live on this VM, vacuous exactly on loopback-only hosts, as filed |
| PS2SERVERS-092 | VERIFIED | `tests/test_game_library.py:191-192` `except OSError: return` in `test_backup_rejects_live_database_and_hardlink` — records a pass, not a skip; the same file's symlink cases at :107-108 and :124-125 correctly use `self.skipTest(...)` |
| PS2SERVERS-093 | WEAKENED | Bare return confirmed at `tests/test_ra_credentials.py:76-78` — but the filed CI consequence is refuted: no Windows CI job ever runs this module, so the phantom pass does not occur in CI (see reasoning) |
| PS2SERVERS-094 | VERIFIED | `tests/test_companion_fixtures.py:59-64`: every link/locked assertion inside `if self.made["link"]:` / `if self.made["locked"]:`; fixture flags go False at `tests/library_fixtures.py:80-92`; executed as root in /tmp copy: `fx.build(...)` returned `{'link': True, 'locked': False}` and the full module still reported `Ran 13 tests … OK` — the locked half silently absent, exactly as filed |

## 2. Reasoning for non-VERIFIED items

### PS2SERVERS-079 — VERIFIED-DRIFT
The core claim holds completely: `setUp` (test_udpfs_bind_control.py:11-20) skipTests on no-tkinter/no-display before either test body, the only discovery job is headless ubuntu (ci.yml:97), and no workflow runs this module under xvfb or a display — the bind-control → `--bind` argv path (asserted at :38-52) gates nothing in CI. The drift is in one supporting grep claim: the finding says "grep-verified: no `xvfb`/`DISPLAY`/`python3-tk` step in any workflow", but `python3-tk` **does** appear as an install step in two release workflows:

```
.github/workflows/release.yml:138:        run: sudo apt-get update && sudo apt-get install -y python3-tk tk-dev libcurl4-openssl-dev
.github/workflows/release-on-main.yml:81:          sudo apt-get install -y patchelf python3-tk libcurl4-openssl-dev
```

Neither release workflow runs any unittest/discovery step (grep for `unittest|discover` in both = no test runs), and neither provides a display, so the correction does not touch the finding's substance — only its grep-verified parenthetical overstates the absence of `python3-tk` in the workflow tree. Corrected statement: no xvfb/DISPLAY step anywhere; python3-tk is installed only by the two release (build) workflows, which run no tests.

### PS2SERVERS-093 — WEAKENED
The code defect as filed is real and confirmed verbatim:

```python
# tests/test_ra_credentials.py:76-78
    def test_posix_insecure_permissions_are_rejected(self):
        if os.name == "nt":
            return  # the platform roundtrip above exercises real DPAPI instead
```

versus the proper `self.skipTest("Creating symlinks requires host privileges")` in the same file at :70-71 — a bare `return` is recorded by unittest as *ok*, not *skipped*, on any Windows run. **However**, the filed consequence — "On Windows (both RA CI jobs are Windows) pass counts overstate coverage on exactly the platform where the branch runs" — is refuted on two counts:

1. No Windows job runs this module at all. The achievements-engine job's fixed list (ci.yml:39) is `tests.test_retroachievements tests.test_game_library tests.test_achievement_viewer tests.test_achievement_loader tests.test_discord_presence` — `test_ra_credentials` is absent; the windows-storage job's lists (ci.yml:50, :52) also omit it; `grep -rn ra_credentials .github/workflows/` returns nothing. Its only CI execution is the ubuntu discovery run (ci.yml:97), where `os.name != "nt"` and the test executes in full (executed here on Linux: `Ran 7 tests … OK`, including this test).
2. The achievements-engine matrix is not "both Windows": ci.yml:15-24 defines five targets — windows-x64, windows-x86, ubuntu-22.04 linux-x64, macos-arm64, macos-x64.

Narrowed surviving claim: a latent one-line hygiene defect — *if* this module is ever run on Windows (manually, or by a future Windows job), the POSIX-permissions test will report a phantom pass instead of a visible skip. It does not currently inflate any CI pass count. The one-line fix (bare `return` → `skipTest`) remains correct and worth taking on those narrower grounds.

## CANDIDATES

- **W4-CAND-1** (observation, from the 093 check): the achievements-engine step at ci.yml:38-39 is named "Exercise native discovery, credential protection and companion wire protocols" but its fixed module list omits `tests.test_ra_credentials` — the dedicated credential-store test module — which therefore receives CI coverage only incidentally via the ubuntu discovery job (ci.yml:97). No failure results today (discovery does run it, on Linux, where its POSIX test is live), but the named job's list and the module's actual coverage have drifted apart; worth a human look next time that list is edited.
