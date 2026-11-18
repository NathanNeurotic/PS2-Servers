# PS2-Servers — Independent REDO Audit (LESS, NOT MORE)

**Repo:** NathanNeurotic/PS2-Servers (Windows PC game servers: Go Edge + Python editions)  
**Pin:** `c5f021c4d38698860f56b03498adc261dbb8d8dd` (main), clone `~/workspace/audit-clones-2026-11-13/PS2-Servers`, verified clean at start and end; read-only throughout — all builds/tests ran in /tmp copies.  
**Date:** 2026-11-15 · **Auditor:** Sable (redo)  
**Method:** blind redo from code only — the prior audit was not consulted. 13 section passes (Go core, Go protocol, Go tests, Python SMB2, Python SMB1+Core, Python UDPFS, Python UDPBD/HTTP/NFS, launcher ×3, Python tests ×2, CI/docs) consolidated with mechanism-level dedupe: 220 section findings → **216** after merging 4 cross-implementation duplicates. Every citation was grep-verified to exist by its worker; censuses are script counts. Sandbox blocks UDP `sendto`: failures traced to that are labelled BLOCKED-ENV, never counted as repo defects.

**Totals:** 216 findings — Cat 1: 13 · Cat 2: 45 · Cat 3: 5 · Cat 4: 8 · Cat 5: 14 · Cat 6: 95 · Cat 7: 27 · Cat 8: 9.

**Process note:** the commissioning brief named `smbv1_server/smbv1_server.py`; no such file exists at the pin. The SMB1 server is `smbv1_server/smbserver_opl.py` (1,862 lines) and was audited under that name.

---

## Test baselines (executed)

**Go** (`go test ./...` in a /tmp copy; build and vet clean): 246 top-level tests — 145 pass, 99 fail, 2 skip. **All 99 failures traced to the sandbox UDP block** (89 messages contain `sendto: operation not permitted`, 22 the downstream `i/o timeout`); zero genuine failures. UDP wire behaviour is therefore *unverified here*, not verified good.

<details><summary>Go baseline detail (from gotests section)</summary>

## Baseline

Commands (Go 1.27.4 from `~/workspace/tools/go1.27.4/bin`, run in `/tmp/edge-test`):

```
cp -r native/ps2servers-edge /tmp/edge-test
go build ./...        # exit 0, no output
go vet ./...          # exit 0, no output
go test ./...         # exit 1
go test -json ./...   # exit 1, used for the counts below
```

Package results from `go test ./...` (13 packages):

- **ok (6):** `cmd/ps2servers-edge`, `internal/compression`, `internal/filesystem`,
  `internal/memory`, `internal/protocol`, `internal/sessionstats`
- **FAIL (4):** `internal/smb`, `internal/udpbd`, `internal/udpfs`, `internal/webui`
- **no test files (3):** `internal/logging`, `internal/session`, `internal/statuslistener`

Test-result counts from `go test -json ./...`:

- 298 test results including subtests: **177 pass, 119 fail, 2 skip**.
- 246 top-level results: **145 pass, 99 fail, 2 skip**.
- Reconciliation with the source: 250 `func Test…` definitions exist (grep count).
  5 of them sit behind `//go:build cgo && chd` and are not compiled by a default
  `go test` (4 in `internal/compression/chd_enabled_test.go`, 1 in
  `internal/udpfs/chd_operations_test.go`); 245 compiled tests + the
  `FuzzParseDataPacket` seed-corpus run = the 246 top-level results.
  `BenchmarkUDPBatch` is not run by `go test` at all (see GOTEST-10).

### UDP-blocked accounting — every failure traced

The sandbox blocks UDP `sendto`. Failure messages across the run: 89 contain
`sendto: operation not permitted` and 22 contain `i/o timeout` (the downstream
symptom of a request or reply that could not be sent). Per package, top-level failures:

- `internal/smb` — 4: the four `TestSMB…Status…` tests in `status_test.go`; each dies in
  `askStatus` with `no status reply from the SMB server: read udp4 …: i/o timeout`
  (the status query/reply is UDP; the SMB TCP tests in the same package all pass).
- `internal/udpbd` — 10: 9 in `udpbd_test.go` + `TestSessionSummaryCountsAConsole`,
  all `read udp4 …: i/o timeout` on the first exchange.
- `internal/udpfs` — 84: the wire-driven tests; the overwhelming majority report
  `write udp4 …: sendto: operation not permitted` at the first client send, the rest
  report the resulting read timeout (status queries, `TestSendPackets…` receive loops).
- `internal/webui` — 1: `TestStatusIdentifiesTheServiceByFlags`
  (`webui_test.go:447: udpfs answered and set its flag, but was reported as not running`).
  Traced: the test's fake server replies with `conn.WriteToUDP` and the code under test
  (`queryAllStatus`) queries over UDP, so with `sendto` blocked no reply can arrive and
  the service is reported not running. Same environmental cause, one step removed.

**Genuine (non-environmental) failures identified: 0.** Every failing test's error
chain terminates in a blocked UDP send or the timeout it causes. The honest converse
also holds: in this environment the UDP wire behaviour is *unverified*, not verified
good — 145 passes include the TCP (SMB) and in-process tests, which are genuine, plus
one pass that is vacuous precisely because UDP is blocked (GOTEST-08).

Skips (2):

- `TestEntryRefusesNamesWindowsWouldReinterpret` (filesystem) — skips by design on
  non-Windows; see GOTEST-05 for why it therefore never runs in CI either.
- `TestSharedHandshakeFixtures` (udpfs) — skipped here only because the mandated
  module-only `/tmp` copy leaves its fixture path (`../../../../conformance/fixtures/
  handshake_cases.json`, relative to the package) outside the copy. The fixture exists
  in a full checkout (verified: `conformance/fixtures/handshake_cases.json` in the
  clone) and the test runs in CI. See GOTEST-13 for the residual fragility.

CI context (read from `.github/workflows/edge.yml`, the workflow that gates this
module): the test job runs `go test -race ./... -count=2`, `go vet ./...` and a
`gofmt -l` gate on `ubuntu-latest`, and a separate step installs `libchdr-dev` and
runs `CGO_ENABLED=1 go test -tags chd ./internal/compression ./internal/udpfs` — so
the 5 CHD-gated tests **do** run in CI and are not orphans. No workflow contains a
`-bench` or `-fuzz` step (grep over `.github/workflows/*.yml`: no hits).
</details>

**Python** (`python3 -m unittest discover -s tests` in a /tmp copy; pytest not installed in sandbox): 1,544 run — 1,388 passed, 3 failed, 73 errors, 80 skipped. Of 54 unique failing tests: 52 traced to the UDP block, 1 to the .git-less /tmp copy environment (attribution-policy), and 1 genuine failure — `test_nfs4_server` TCP recv timeout, with TCP loopback proven working elsewhere in the same run.

<details><summary>Python baseline detail (from pytesta section)</summary>

## Baseline

- **Where:** full copy of `PS2-Servers @ c5f021c4d386` at `/tmp/ps2s` (working tree copied from the clone, `.git` excluded; clone never modified).
- **Command:** `cd /tmp/ps2s && timeout 1200 python3 -m unittest discover -s tests -v` (Python 3.12.3). `python3 -m pytest` was tried first and is unavailable in this sandbox (`No module named pytest`), so the unittest fallback specified in the task was used. Run took 230.100s, exit code 1.
- **Totals (exact, from the runner summary `Ran 1544 tests … FAILED (failures=3, errors=73, skipped=80)`):** total run **1544**, passed **1388**, failed **3**, errors **73**, skipped **80**.
- **Event vs. test accounting:** the 76 failure/error events come from **54 unique tests**. 22 events are secondary errors in `stop()`/teardown (a second `PermissionError` from the wake-up `sendto` in e.g. `tests/test_udpfs_handoff_retried_write.py:133` and the `test_udpfs_write_needs_writable_handle` harness) re-reporting tests that had already errored in their bodies — 19 in `test_udpfs_write_needs_writable_handle`, 3 in `test_udpfs_handoff_retried_write`.

### Per-failure classification (all 54 unique failing/erroring tests)

| Class | Unique tests | Evidence |
|---|---|---|
| **UDP-blocked environment (BLOCKED-ENV)** | **52** | Direct: 48 tests whose tracebacks end in `PermissionError: [Errno 1] Operation not permitted` raised at a UDP `sendto` — `test_ra_owned_service` 18, `test_udpfs_write_needs_writable_handle` 19, `test_router_status` 5, `test_udpfs_handoff_retried_write` 3, `test_ra_resolver_process` 2, `test_session_summary` 1 (e.g. `tests/test_ra_owned_service.py:252` `self.client.sendto(...)`; `udpfs_server/multiclient_selftest.py:117` `self.sock.sendto(...)`). Downstream: all 4 `test_status_client` failures — 3 FAILs (`test_reads_a_real_reply_over_the_wire`, `test_hex_string_port_is_parsed`, `test_silence_is_none_not_an_exception`) plus the `KeyError: 'state'` ERROR in `test_records_per_target_and_marks_silence_unknown`. Traced: `launcher/status_client.py:91-104` opens a `SOCK_DGRAM` socket, `sendto`s the query and catches `OSError`, returning `None`; the test's own server records `queries == 0` ("the query never arrived", `tests/test_status_client.py:103`), so the replies the assertions expect can never arrive with UDP sends blocked. |
| **Copy-environment (`.git` excluded from the /tmp copy)** | **1** | `test_attribution_policy.AttributionWorkflowTests.test_tag_with_no_new_commits_does_not_fail_validation` — ERROR at `tests/test_attribution_policy.py:22`: `git tag -a … HEAD` in the repo root exits 128 because the copy has no `.git`/`HEAD`. Not UDP, not a repo defect in a real checkout (see PYTESTA-01 for the separate design problem this exposes). The two `test_line_endings` skips ("git ls-files failed; not a checkout?", "init script not in HEAD") have the same cause. |
| **Genuine failure (not environment-explained)** | **1** | `test_nfs4_server.SocketTests.test_endless_empty_fragments_close_the_connection` — ERROR `TimeoutError` at `tests/test_nfs4_server.py:932` on a **TCP** `sock.recv(10)` that never sees the connection close. TCP loopback demonstrably works in this sandbox (the HTTP, SMB and cooperative-stop tests in the same run pass over real TCP sockets), and nothing in the trace touches UDP, so this is not traceable to the sandbox UDP block. Recorded as a genuine single failure in this environment; root cause (server behaviour vs. timing) not determined within this section's scope. |

- **Skipped (80):** environment/feature gates stated by the tests themselves — no display / no graphical display (42 across the suite, incl. the GUI tests in this half), `libchdr is not available` (6), `smbprotocol is not installed` (6, missing-dependency environment), native achievement engine not built (6), no Go toolchain (4), pinned libchdr not built (3), Windows-only tests (named-pipe IPC, kernel mutex, PowerShell, onefile bootstrap; 6), no POSIX shell/git checkout (2, see above), no non-loopback IPv4 (1), remainder single platform gates. No skip in this half was caused by the UDP block.
- **In-scope (a–n) baseline result:** of the 54 unique failing tests, exactly **1** is in this section's half (`test_attribution_policy`, copy-environment above). All other in-scope tests passed or skipped on their stated display/libchdr gates. In-scope collection census (script): **516 tests** collected across the 36 in-scope `test_*.py` files.

---

# Findings — Python test suite, first half (a–n)

### PYTESTA-01 — The attribution test mutates, and depends on, the developer's real checkout
- **Category:** 7
- **Locations:** tests/test_attribution_policy.py:22-40
- **Problem:** The suite's only test of `.github/workflows/attribution-policy.yml` runs `git tag -a attribution-empty-range-<pid> HEAD` **in the repository under test** (`cwd=ROOT`), extracts the workflow's shell script, runs it, and deletes the tag in a `finally`. Three consequences, one of them EXECUTED in this baseline: (1) in any `.git`-less copy (source tarball, this audit's copy) the test errors with git exit 128 instead of skipping — it checks only for `bash`, never for a usable checkout; (2) a hard kill between create and delete leaves a stray `attribution-empty-range-*` tag in the user's real repo; (3) the test writes to the very repo it is auditing, so it cannot run in a read-only checkout. The PID-suffixed tag name mitigates parallel runs but none of the above.
- **Fix:** Build a throwaway repo in a temp directory (`git init`, one empty commit), copy or reference the workflow script against that repo's `HEAD`, and skip cleanly when `git rev-parse HEAD` fails in `ROOT`. The workflow script itself takes `BEFORE`/`AFTER` from the environment, so it does not need the real history.
- **Benefit:** The test runs in tarballs, read-only checkouts and sandboxes; running the suite can never leave tags behind in the user's repo.
- **Effort:** M · **Risk:** Low · **Confidence:** High (READ|EXECUTED — failed in the baseline run for exactly the `.git` reason)

### PYTESTA-02 — Eight test files silently run zero tests when executed directly
- **Category:** 7
- **Locations:** tests/test_achievement_loader.py:1-48 · tests/test_achievement_viewer.py:1-86 · tests/test_caduceus_reference_wire.py:1-60 · tests/test_caduceus_setup.py:1-91 · tests/test_desktop_instance.py:1-99 · tests/test_discord_presence.py:1-204 · tests/test_game_library.py:1-299 · tests/test_library_panel.py:1-360 (script census: these 8 of the 36 in-scope files contain no `unittest.main()`; the other 28 all end with the `if __name__ == "__main__": unittest.main()` guard at the final line)
- **Problem:** `python tests/test_game_library.py` (etc.) exits 0 having run nothing — no error, no output, no tests. Discovery and `python -m unittest tests.test_game_library` work, so CI is unaffected, but the direct-execution path — the one a developer reaches for when debugging a single file — reports success for a run that never happened. This is the same failure family `tests/__init__.py`'s docstring says already hid a failing test on main once ("CI silently runs whatever handful of modules are named by hand").
- **Fix:** Add the same two-line `__main__` guard the other 28 files already carry to these 8 files. No new mechanism; pure consistency with the suite's own convention.
- **Benefit:** A direct run can never again masquerade as a pass; the suite has one invocation convention instead of two.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ — census by AST/script over all 36 files)

### PYTESTA-03 — The Edge flag-extraction helpers are copied verbatim between test files
- **Category:** 2
- **Locations:** tests/test_edge_manual.py:26-28 and :71-80 · tests/test_edge_service_modes.py:26-28 and :208-218 · (near-copy) tests/test_edge_flag_exposure.py:96-111, tests/test_edge_service_modes.py:224-236
- **Problem:** `_read()` is byte-identical in `test_edge_manual.py` and `test_edge_service_modes.py`, and `_flags_of()` (extract a Go function's `fs.*("name", …)` flag names from `main.go`) is AST-identical in both files — only the comments differ (script census: exact AST-dump match). A third variant of the same "slice one section/function out of a text file" logic exists as `_section()`/`_section_options()`/`_udpfs_flags()` across the same three files. These parsers are the load-bearing part of the Edge packaging guards: if the Go source layout changes (e.g. `fs.Var` gains a new shape), each copy must be found and fixed separately, and a copy that is missed degrades silently to "no flags found" in exactly the tests whose job is noticing missing flags.
- **Fix:** Move `_read`, `_flags_of` and the section slicer into one shared tests helper (e.g. alongside `tests/library_fixtures.py`) and import them in the three Edge files; delete the copies.
- **Benefit:** One parser to fix when `main.go` changes shape; the three Edge guard files stay focused on their distinct assertions.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ|EXECUTED — AST census run by script)

### PYTESTA-04 — The shared ISO fixture lives inside a test module — and one consumer re-implements it anyway
- **Category:** 2
- **Locations:** tests/test_disc_identity.py:34-59 (`iso_fixture`) · tests/library_fixtures.py:31 · tests/test_library_identity.py:14 · tests/test_library_index.py:13 · tests/test_game_library.py:41,74 (imports) and :266-299 (inline re-implementation)
- **Problem:** `iso_fixture()` — the synthetic ISO9660 builder half the suite depends on — is defined in `test_disc_identity.py`, so `library_fixtures.py` and at least four other test files import a helper out of a sibling *test* module; renaming or splitting that test file breaks the fixture library for every consumer. Worse, `test_game_library.py` imports `iso_fixture` twice (lines 41, 74) and then, in `test_native_ps2_hash_matches_independent_boot_name_and_elf_digest` (266-299), hand-rolls a second private ISO9660 builder (`record()` plus manual PVD/directory assembly, ~30 lines) for a case the shared builder already covers — it accepts custom `boot` and `elf` arguments, which is all that test varies.
- **Fix:** Move `iso_fixture` (and its `record`/`both` helpers) into `tests/library_fixtures.py` or a `tests/fixtures` module, re-export it from `test_disc_identity` if its own tests want the local name, and rewrite the game-library hash test to call it with `boot=`/`elf=`. Delete the inline builder.
- **Benefit:** Fixture code has one home that is not itself a test file; one ISO builder to keep correct instead of two that can drift apart on exactly the byte layout the identity tests exist to pin.
- **Effort:** M · **Risk:** Low · **Confidence:** High (READ — all import sites grep-verified)

### PYTESTA-05 — test_library_preferences executes AST-extracted copies instead of the production methods
- **Category:** 6
- **Locations:** tests/test_library_preferences.py:17-33, :40-58, :60-68
- **Problem:** The file never instantiates `LibraryWindow` and never calls `save_preferences()` or `show_cover()` on a production object (grep-verified: the names occur only as AST search strings). Instead it parses `launcher/library_gui.py`, lifts the two method `FunctionDef`s out of the class, `exec`s them in a hand-built namespace containing only a fake `config`, and calls them with a `SimpleNamespace` fake `self`. Two failure modes follow: (1) false failures — the moment either method references any other module global, helper or import from `library_gui.py`, the extracted copy raises `NameError` for a harness reason, not a product reason; (2) unobservable behaviour — attribute wiring, the real `self`'s widget surface beyond the faked fields, and any interaction with the rest of the window are structurally outside what the test can see, while the test's name claims the preferences themselves were checked. The third test in the file (:60-68) is substring assertions over source text, which pin spelling, not behaviour. The suite already has the sounder pattern elsewhere — unbound production methods driven with a fake `self` (e.g. `LauncherLifecycleTests` in tests/test_directlink.py) — which keeps the real globals and the real function object.
- **Fix:** Call the real unbound methods (`LibraryWindow.save_preferences(fake_self)`) with the module imported normally, faking only the attributes/widgets the method touches; keep the AST only as the way to *find* nothing — it should not be the thing under test.
- **Benefit:** The test fails only when the production behaviour changes, survives internal refactors of `library_gui.py`, and actually exercises the shipped function objects.
- **Effort:** M · **Risk:** Low · **Confidence:** High (READ — production symbols' absence from the test verified by grep)

### PYTESTA-06 — The netinfo detailed-format test asserts only the formatter's own constant strings
- **Category:** 6
- **Locations:** tests/test_netinfo_and_smb.py:20-22 (with :15-17)
- **Problem:** `test_detailed_ip_info_formatting` calls the real `netinfo.detailed_ip_info()` against whatever host runs the suite, then asserts exactly two substrings: the literal header line and `"Custom IP..."`. The second is a hard-coded sentence inside the formatter itself (`launcher/netinfo.py:215`, grep-verified), and the first is its fixed title — so the test can only fail if someone edits those literals, and it asserts nothing about any address, adapter or formatting of actual detected data (which is host-dependent and therefore silently unexamined). Its `"Custom IP..."` assertion also duplicates the intent of `test_ip_choices_contains_custom_option` directly above it (:15-17) without testing the same code path.
- **Fix:** Patch `netinfo.all_ipv4` (the formatter's input) to a fixed address set and assert those addresses appear in the output in the formatted form; drop the duplicated literal assertion or point it at `ip_choices()` only.
- **Benefit:** The test would catch a formatter that drops, mangles or mislabels real addresses — currently impossible for it to do — and stops depending on the runner's network configuration.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

### PYTESTA-07 — A traversal test whose name promises the opposite of what it asserts
- **Category:** 6
- **Locations:** tests/test_http_path_aware.py:208-218
- **Problem:** `test_traversal_and_unknown_paths_resolve_to_nothing` loops over four names; for `"../DVD/Game.iso"` it asserts the lookup **succeeds** (`assertIsNotNone`), because `normpath` folds the `..` back inside the whitelist. The inline comment explains the whitelist logic honestly, but the test *name* — the thing a reader scanning a green run, or a future editor deciding what may be changed, actually sees — states that traversal resolves to nothing. A later change that made folded traversal return `None` would "fix" the code to match the name and break this test for the wrong-looking reason; a change that widened real traversal would still leave a test named as if traversal were refused.
- **Fix:** Split the folded-`..` case into its own test named for what is actually guaranteed (e.g. `test_dotdot_that_folds_back_inside_the_root_is_a_normal_lookup`), leaving the refusal test's name true of every case in it. Words only; no assertion changes.
- **Benefit:** The suite's names stay a trustworthy summary of the server's path-safety guarantees — important in a file whose other tests are the traversal defence.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

### PYTESTA-08 — The hardlink backup test passes vacuously where hardlinks are unavailable
- **Category:** 6
- **Locations:** tests/test_game_library.py:185-198
- **Problem:** `test_backup_rejects_live_database_and_hardlink` wraps `linked.hardlink_to(...)` in `try/except OSError: return`. On any filesystem where hardlinks cannot be created, the test therefore *passes* having verified only the live-database rejection — the hardlink rejection, half its name and the more interesting aliasing case, is silently untested with no skip recorded. Sibling tests in the same suite use the honest form for the same situation (`except OSError: self.skipTest(...)`, e.g. the symlink tests in tests/test_library_index.py and tests/test_library_recursive_scan.py). In this baseline's Linux environment hardlinks work and the full test did run, so this is a portability hole, not a local failure.
- **Fix:** Replace the bare `return` with `self.skipTest("Host cannot create hardlinks")` (after asserting the live-database half, or in a separate test method for the hardlink half).
- **Benefit:** A green run never again claims hardlink protection on a machine that could not exercise it; the skip shows up in the count where a reviewer can see it.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ|EXECUTED — full test ran and passed in the baseline)

### PYTESTA-09 — The display-guard + full-app construction prologue is copy-pasted through the GUI tests
- **Category:** 2
- **Locations:** tests/test_autostart_and_firewall_options.py:46-64 and the same block repeated at :84, :106, :133, :205, :228, :258, :282, :306 (script census: 9 × `SkipTest("no display")`, 10 × `tk.Tk()` in this file) · same shape at tests/test_about_page.py:106-152 and throughout tests/test_library_panel.py (7 × `tk.Tk()`)
- **Problem:** Every GUI test in `test_autostart_and_firewall_options.py` repeats the same ~9-line prologue — import tkinter, catch `ImportError`, construct `tk.Tk()`, catch `TclError`, raise the same skip — and then constructs a complete `LauncherApp`. The copies have already drifted: two tests in the file probe the display with a throwaway `tk.Tk().destroy()` before using the `_app_with_fake_servers` helper, the others construct the root directly, and `test_about_page.py`'s version additionally juggles environment save/restore that the autostart file does in `setUp`. On a machine *with* a display this is also the slowest pattern in the half: nine full application constructions for nine option assertions.
- **Fix:** Extract one `_require_display()` / app-factory helper (the pattern `test_gui_layout.py` already uses at class level with `setUpClass`) and have each test call it; keep per-test app construction only where a test genuinely needs a fresh app, and say so there.
- **Benefit:** One place to fix when Tk/display handling changes; the drift between the copies stops growing; display-machine runs get faster if construction can be shared per class.
- **Effort:** M · **Risk:** Low · **Confidence:** High (READ|EXECUTED — repetition counted by script)

---
</details>

**Edge binary** (built in /tmp, Go 1.27.4, `-trimpath -ldflags="-s -w"`): 7,667,975 bytes. Its `--help` defaults were compared line-by-line against shipped packaging/docs claims (see CIDOCS findings).

---

## Findings (sorted by benefit)

### RE-SERV-001 — Pre-authentication commands stay live after authentication and rewrite the session's security state (signing downgrade + negotiate-claims overwrite)
- **Filed as:** PYSMB2-01 (pysmb2.md)
- **Category:** 6
- **Locations:** smb2_server/smb2_server.py:537-538 (`_UNSIGNED_OK` exempts NEGOTIATE and SESSION_SETUP from the signing-required check), smb2_server/smb2_server.py:540-563 (`_signature_check` returns None whenever `signing_key` is None), smb2_server/smb2_server.py:745-755 (`h_negotiate` unconditionally replaces `conn.dialect` and `conn.negotiate_claims`), smb2_server/smb2_server.py:840-867 (SESSION_SETUP NTLM NEGOTIATE branch sets `conn.signing_key = None`, `conn.signing_required = False` at 855-856 and never clears `conn.authenticated`)
- **Problem:** Nothing retires the pre-auth commands once a session exists. Executed proof (handler-level, /tmp copy): with `authenticated=True`, `signing_required=True` and a signing key set, an unsigned SESSION_SETUP carrying an NTLMSSP NEGOTIATE returned MORE_PROCESSING_REQUIRED, left `authenticated=True`, and cleared both `signing_key` and `signing_required` — after which an unsigned CREATE passed `_signature_check`. An on-path attacker (or a confused client) can therefore strip signing from a live, signing-required session without re-authenticating, then inject unsigned commands under the victim's identity; every file handle and tree survives. The same hole exists for SMB2 NEGOTIATE: executed proof shows an unsigned mid-session NEGOTIATE changed the live dialect 0x0302 → 0x0202 and overwrote `conn.negotiate_claims` — the stored claims `validate_negotiate_info` (smb2_server.py:1406-1431) compares the client's signed re-statement against, i.e. the anti-downgrade check can be re-based by the very party it exists to catch.
- **Fix:** In `h_negotiate` and in the NTLM-NEGOTIATE branch of `h_session_setup`, refuse (or fully tear down the session — close handles/trees, clear `authenticated`) when `conn.authenticated` is already true. A re-negotiation must never be a silent in-place mutation of a live session.
- **Benefit:** Closes a signing-downgrade / session-hijack path and restores FSCTL_VALIDATE_NEGOTIATE_INFO to comparing against claims the attacker cannot replace.
- **Effort:** M · **Risk:** Med · **Confidence:** High (EXECUTED)

### RE-SERV-002 — CREATE, SET_INFO and CLOSE perform create / truncate / rename / delete without checking the handle's granted DesiredAccess
- **Filed as:** PYSMB2-02 (pysmb2.md)
- **Category:** 6
- **Locations:** smb2_server/smb2_server.py:1023-1040 (CREATE creates a file or `os.makedirs` a directory, and truncates an existing file for OVERWRITE / OVERWRITE_IF / SUPERSEDE), smb2_server/smb2_server.py:1050-1053 (`can_write` is computed only *after* those effects; FILE_DELETE_ON_CLOSE is armed on any handle), smb2_server/smb2_server.py:1083-1088 (CLOSE executes the delete), smb2_server/smb2_server.py:1333-1335 (SET_INFO FileDispositionInformation arms delete-on-close with no `can_write` check), smb2_server/smb2_server.py:1340-1357 (SET_INFO rename with no `can_write` check)
- **Problem:** Destructive effects are gated only on the share being writable (`srv._writable`), never on the access the CREATE actually granted — while WRITE (smb2_server.py:1177) and the SET_INFO EOF-truncate (smb2_server.py:1328-1329) *do* check `h.can_write`, proving the intended rule exists and these paths missed it. Executed proofs: (1) CREATE with DesiredAccess = FILE_READ_DATA only and disposition FILE_OVERWRITE returned SUCCESS with `can_write=False` and the target file truncated to 0 bytes; (2) SET_INFO rename through a read-only handle returned SUCCESS and moved the file; (3) CREATE with FILE_DELETE_ON_CLOSE through a read-only handle deleted the file at CLOSE. `os.makedirs` at 1026 also creates every missing intermediate directory as a side effect. A client (or a bug in one) holding only a read handle can destroy, move or delete files it was never granted write access to.
- **Fix:** Compute `can_write` from DesiredAccess before any filesystem effect in `h_create`, and require it (or the specific DELETE access) for the create/truncate branches, for arming delete-on-close, and in the SET_INFO disposition and rename branches — the same check WRITE already makes.
- **Benefit:** DesiredAccess becomes a real boundary instead of an annotation two handlers honour and five effects ignore.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED)

### RE-SERV-003 — READ is never gated on DesiredAccess; there is no read-access concept at all
- **Filed as:** PYSMB2-03 (pysmb2.md)
- **Category:** 6
- **Locations:** smb2_server/smb2_server.py:1130-1157 (`h_read` checks only pipe/directory, then reads), smb2_server/smb2_server.py:150-166 (`Handle` tracks `can_write` and nothing for read), smb2_server/smb2_wire.py:143-145 (only `ACCESS_WRITE_ANY` is defined; no read mask exists)
- **Problem:** Any successfully created handle can be read, including one opened with DesiredAccess = FILE_WRITE_DATA only, or with no data access at all. Executed proof: a handle created write-only returned the file's contents (`b'TOPSECRET-DATA'`) from `h_read` with STATUS_SUCCESS. Combined with PYSMB2-02, DesiredAccess is decorative in both directions.
- **Fix:** Record a `can_read` on the handle from the same CREATE access mask (FILE_READ_DATA | GENERIC_READ | MAXIMUM_ALLOWED | GENERIC_ALL) and check it in `h_read`'s file branch, mirroring the existing `can_write` check in `h_write`.
- **Benefit:** A write-only or attribute-only handle stops being a read channel for the file's contents.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED)

### RE-SERV-004 — SET_INFO FileBasicInformation returns SUCCESS while doing nothing — and validates nothing
- **Filed as:** PYSMB2-04 (pysmb2.md)
- **Category:** 6
- **Locations:** smb2_server/smb2_server.py:1336-1339
- **Problem:** The handler returns STATUS_SUCCESS for FileBasicInformation without applying the timestamps, and without even checking the payload exists: executed proof shows a SET_INFO with a zero-length payload returns SUCCESS. The in-code comment frames this as an Explorer-compatibility trade-off, but on the wire it is indistinguishable from success — a client copying files (or a backup tool preserving timestamps) is told the times were set when they were silently discarded, so copies quietly carry wrong timestamps with no error anywhere.
- **Fix:** Either apply the times (`os.utime` from the payload's LastWriteTime/ChangeTime fields, after a length check) or answer STATUS_NOT_SUPPORTED so the client knows. Keeping the Explorer behaviour is a defensible choice; reporting success for an empty payload is not — at minimum validate the payload length like the rename branch does.
- **Benefit:** Removes a false-success from the protocol surface; timestamp-preserving copies either work or fail visibly.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED)

### RE-SERV-005 — No throttling of failed logons, and the NTLM challenge survives a failed AUTHENTICATE
- **Filed as:** PYSMB2-05 (pysmb2.md)
- **Category:** 6
- **Locations:** smb2_server/smb2_server.py:871-905 (AUTHENTICATE path; failure raises and leaves connection state untouched), smb2_server/smb2_server.py:907-909 (`conn.challenge` is cleared only on success), smb2_auth.py:262-324 (`verify` keeps no attempt state)
- **Problem:** A failed SESSION_SETUP costs the attacker nothing: no delay, no per-connection or per-account failure counter, no teardown after repeated failures, and the same server challenge remains valid, so the client can retry AUTHENTICATE against it immediately, indefinitely. Grep-verified: no sleep, counter or lockout exists anywhere in the auth path (the only `time.sleep` calls in the file are in the Windows take-445 service choreography). Online password guessing against the one configured user is therefore limited only by round-trip time.
- **Fix:** Count consecutive failures per connection in `h_session_setup`; clear `conn.challenge` on any failed AUTHENTICATE (forcing a fresh NEGOTIATE round) and drop the connection after a small fixed number of failures.
- **Justification:** (only if fix ADDS) The counter is a few lines of per-connection state, not a new subsystem, and it is the only mechanism that makes online guessing expensive; NTLMv2's offline-guess weakness is documented in smb2_auth.py as unfixable from this side, which makes the online path the one worth closing.
- **Benefit:** Failed logons stop being a free, repeatable oracle against a single static challenge.
- **Effort:** M · **Risk:** Med · **Confidence:** Med (READ)

### RE-SERV-006 — LOGOFF does not retire the SessionId; the next session on the connection silently reuses it
- **Filed as:** PYSMB2-06 (pysmb2.md)
- **Category:** 6
- **Locations:** smb2_server/smb2_server.py:927-934 (`h_logoff` clears handles, trees and `authenticated` only), smb2_server/smb2_server.py:863 and smb2_server/smb2_server.py:915 (a session id is minted only `if conn.session_id == 0`)
- **Problem:** After LOGOFF, `conn.session_id` keeps the logged-off session's value, so the next successful SESSION_SETUP on the same connection is assigned the *same* SessionId as the session that was just ended. SessionIds are how clients key per-session state (including signing state); reissuing one invites exactly the old/new session confusion the id exists to prevent. The signing key's survival past LOGOFF is commented as deliberate for signing the LOGOFF reply itself — but nothing clears the id afterwards either.
- **Fix:** Set `conn.session_id = 0` at the end of `h_logoff` (the reply's header is built from the value the dispatcher already captured for this message — verify against `_dispatch_all`'s use of `conn.session_id or hdr.session_id` when making the change, and if the reply needs the old id, clear it in `_dispatch_all` after the LOGOFF reply is packed instead).
- **Benefit:** Each session gets the unique id the protocol promises; no state keyed by SessionId can alias across a logoff boundary.
- **Effort:** S · **Risk:** Low · **Confidence:** Med (READ)

### RE-SERV-007 — TREE_DISCONNECT, ECHO and LOGOFF answer SUCCESS with no session at all
- **Filed as:** PYSMB2-07 (pysmb2.md)
- **Category:** 6
- **Locations:** smb2_server/smb2_server.py:974-976 (`h_tree_disconnect`: pops a tree id, returns SUCCESS, no `_require_session`/`_tree`), smb2_server/smb2_server.py:1363-1364 (`h_echo`: returns SUCCESS unconditionally), smb2_server/smb2_server.py:927-934 (`h_logoff`)
- **Problem:** Every other stateful handler enforces a session via `_require_session`/`_tree`; these three do not. Executed proof: on a fresh, never-authenticated connection all three return STATUS_SUCCESS — including TREE_DISCONNECT for tree id 99, a tree that never existed. The impact is state-machine laxness rather than data exposure (nothing is served), but a server that reports success for operations on nonexistent state masks client bugs and makes its own session accounting (the `--session-summary` counters key off these commands) unreliable.
- **Fix:** Have `h_tree_disconnect` go through `_require_session` and report STATUS_NETWORK_NAME_DELETED for an unknown tree id, like `_tree` does; leave ECHO's behaviour a deliberate, commented choice if unauthenticated keepalive is actually wanted.
- **Benefit:** Success starts meaning the named thing existed and the caller was entitled to act on it.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED)

### RE-SERV-008 — A malformed KEY_EXCH wrapped key silently falls back to the SessionBaseKey
- **Filed as:** PYSMB2-09 (pysmb2.md)
- **Category:** 6
- **Locations:** smb2_spnego.py:330-341
- **Problem:** `exported_session_key` unwraps the client's session key only when NEGOTIATE_KEY_EXCH was negotiated *and* the wrapped key is exactly 16 bytes; otherwise it silently returns the SessionBaseKey. Executed proof: a Credentials with KEY_EXCH set and a 5-byte wrapped key returns the base key unchanged, with no error. If KEY_EXCH was negotiated, the client derived its signing key from the random key it sent — so server and client now hold different session keys and every signed request fails later as ACCESS_DENIED, far from the cause; if the client does not sign, the session proceeds on a key the client did not choose, with nothing logged.
- **Fix:** When KEY_EXCH is negotiated but the wrapped key is not 16 bytes, fail the session setup (raise AuthError / LOGON_FAILURE at the call site in `h_session_setup`, smb2_server.py:901) instead of substituting a different key.
- **Benefit:** A key-agreement failure fails at key agreement, with the right status, instead of surfacing as unexplained signature rejections.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED)

### RE-SERV-009 — A corrupt trailing segment in a compound request discards every reply, including successes already computed
- **Filed as:** PYSMB2-10 (pysmb2.md)
- **Category:** 6
- **Locations:** smb2_server/smb2_server.py:429-434
- **Problem:** `_dispatch_all` accumulates replies in a local list and sends them only after the whole compound is walked. If `parse_header` fails on any segment after the first, the function simply `return`s — the accumulated replies are never sent. Executed proof: a compound of one valid ECHO request followed by 64 zero bytes produced **zero** bytes sent on the socket. The client, which did nothing wrong in its first request, sees a hang/timeout rather than its reply plus an error. (The same early return is also what a truncated final segment produces.)
- **Fix:** On a mid-compound parse failure, stop walking, append an error reply for the bad segment if a header boundary is known, and send whatever replies were accumulated instead of dropping them.
- **Benefit:** One bad segment costs one error reply, not the whole compound and a client-side timeout.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED)

### RE-SERV-010 — QUERY_DIRECTORY returns an entry larger than the client's OutputBufferLength
- **Filed as:** PYSMB2-11 (pysmb2.md)
- **Category:** 6
- **Locations:** smb2_server/smb2_server.py:1258-1263
- **Problem:** The packing loop breaks on buffer exhaustion only `and entries` — i.e. the first entry is always emitted even when it alone exceeds the client's declared output length. Executed proof: with `out_len = 16`, the server returned STATUS_SUCCESS with OutputBufferLength = 96. A client that sized its buffer by its own request (libsmb2 and the PS2 browse drivers this server exists for work in fixed buffers) is handed more bytes than it agreed to receive.
- **Fix:** If the first entry does not fit, answer STATUS_INFO_LENGTH_MISMATCH (already defined and already used by `h_query_info`) instead of overrunning the declared length.
- **Benefit:** The server stays inside the buffer contract every other length in the protocol is checked against (`request_buffer`, MAX_READ/WRITE).
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED)

### RE-SERV-011 — QUERY_DIRECTORY's pattern filter is bypassed for "." and ".."
- **Filed as:** PYSMB2-12 (pysmb2.md)
- **Category:** 6
- **Locations:** smb2_server/smb2_server.py:1204-1207 (`_listing_for` unconditionally keeps `names[:2]`)
- **Problem:** When a client asks for a filtered listing, the dot entries are kept regardless of the pattern. Executed proof: pattern `*.iso` returned entries `['.', '..', 'onlyfile.iso']`. Clients that trust the server-side filter (rather than re-filtering) show phantom entries for every filtered browse; a client computing "does this directory contain an .iso" from the raw listing gets a wrong answer shape. The dot entries themselves are deliberate (see the `_listing_for` docstring) — bypassing the client's filter for them is the defect, not their existence.
- **Fix:** Keep the dot entries only when the pattern matches them too (or when there is no filter), i.e. run all names through the same `fnmatch` test.
- **Benefit:** A filtered listing is filtered; unfiltered listings keep the dot entries some clients need.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED)

### RE-SERV-012 — A zero-length READ is answered STATUS_END_OF_FILE
- **Filed as:** PYSMB2-13 (pysmb2.md)
- **Category:** 6
- **Locations:** smb2_server/smb2_server.py:1146-1153 (file branch), smb2_server/smb2_server.py:1137-1139 (pipe branch answers STATUS_PIPE_EMPTY for a zero-length read)
- **Problem:** `h_read` treats "no bytes came back" as EOF, but a request with Length = 0 legitimately returns no bytes at any offset. Executed proof: a zero-length READ on a handle positioned at offset 0 of a non-empty file raised STATUS_END_OF_FILE ("read past the end"). The comment at 1151-1152 justifies EOF for a zero-length *success*, but the conflation punishes a well-formed zero-length request with an error that tells the client the file ended. The pipe branch has the same shape: a zero-length pipe READ is PIPE_EMPTY rather than a successful empty read.
- **Fix:** Special-case `length == 0` before opening the file / reading the pipe and return STATUS_SUCCESS with an empty data buffer; keep END_OF_FILE for a non-zero read that hits the end.
- **Benefit:** Zero-length probes (a normal client pattern) stop looking like end-of-file.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED)

### RE-SERV-013 — NEGOTIATE silently accepts a dialect list shorter than DialectCount claims
- **Filed as:** PYSMB2-14 (pysmb2.md)
- **Category:** 6
- **Locations:** smb2_server/smb2_server.py:748-757
- **Problem:** The dialect loop `break`s when the body runs out and negotiation proceeds with whatever prefix was present. Executed proof: a NEGOTIATE claiming DialectCount = 3 but carrying one dialect (2.1) was accepted with STATUS_SUCCESS and dialect 0x0210. A truncated or lying NEGOTIATE is malformed input; accepting a prefix of it means the negotiated dialect — and the `negotiate_claims` tuple stored for FSCTL validation at line 755 — are built from a message the server knows was incomplete, and the stored claims enshrine the truncated list as "what the client offered".
- **Fix:** If fewer than `dialect_count` dialects fit in the body, raise STATUS_INVALID_PARAMETER like every other short request in the file.
- **Benefit:** Malformed negotiation fails at negotiation, and the stored claims always describe a complete message.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED)

### RE-SERV-014 — FLUSH cannot fail: it is the only handle handler with no body-length check, and it swallows fsync errors
- **Filed as:** PYSMB2-15 (pysmb2.md)
- **Category:** 6
- **Locations:** smb2_server/smb2_server.py:1102-1111 (contrast the length check every sibling makes before touching the FileId, e.g. `h_read` at smb2_server/smb2_server.py:1132-1133)
- **Problem:** Two ways FLUSH reports success without the work being known-good. (1) It slices `body[8:24]` and calls `handle_from` with no preceding length check; executed proof: an 8-byte body makes `h_flush` raise a bare `struct.error` ("unpack requires a buffer of 16 bytes"), which only the dispatcher's catch-all turns into STATUS_INVALID_PARAMETER — the handler's own contract (raise Smb2Error with a chosen status) is broken. (2) `fh.flush()` + `os.fsync()` failures are caught and discarded (`except OSError: pass`), so a client that issued FLUSH to learn its data is durable is told STATUS_SUCCESS even when the fsync failed.
- **Fix:** Add the same `len(body) < 24` check the other handlers open with, and let an fsync failure propagate as an OSError so `_dispatch` maps it to a real status (`_status_for_oserror`) instead of success.
- **Benefit:** FLUSH's success means the data was flushed; malformed FLUSHes fail with the handler's chosen status, not the catch-all's.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED)

### RE-SERV-015 — Receive framing has no size cap, and `_recv_exact` concatenates quadratically
- **Filed as:** PYSMB2-16 (pysmb2.md)
- **Category:** 4
- **Locations:** smb2_wire.py:37-53 (`recv_msg`: 24-bit length trusted, up to 16,777,215 bytes), smb2_wire.py:27-34 (`_recv_exact`: `buf += chunk` in a loop)
- **Problem:** The server advertises MAX_TRANSACT / MAX_READ / MAX_WRITE of 1 MiB (`smb2_wire.py:204-206`) but the framing layer will buffer a message sixteen times that size from any unauthenticated peer — the length is read and honoured before any header, dialect or session check exists. Each connection thread can thus be made to allocate ~16 MiB on a peer's say-so, and `_recv_exact` builds that buffer by repeated `bytes` concatenation, which copies the accumulated prefix on every `recv` — O(n²) copying for large messages (a 1 MiB legitimate message arrives as many chunks).
- **Fix:** Cap the framed length in `recv_msg` (the advertised 1 MiB plus header/compound headroom is the natural bound; a peer exceeding it is not speaking this server's protocol) and accumulate `_recv_exact` into a list or `bytearray`, joining once.
- **Benefit:** Per-connection memory becomes bounded by a number the server itself advertised, and large legitimate transfers stop paying quadratic copy costs.
- **Effort:** S · **Risk:** Low · **Confidence:** Med (READ)

### RE-SERV-016 — `send_msg` raises OverflowError for a reply over 16 MiB, killing the connection thread
- **Filed as:** PYSMB2-17 (pysmb2.md)
- **Category:** 6
- **Locations:** smb2_wire.py:23-24 (`len(msg).to_bytes(3, "big")`), smb2_server/smb2_server.py:417-530 (`_dispatch_all` builds the compound reply `out` with no size bound), smb2_server/smb2_server.py:385-415 (`_serve_client` catches connection errors and OSError only)
- **Problem:** The 24-bit framing length overflows at 16,777,216 bytes. Executed proof: `send_msg` with a 16 MiB + 1 message raises `OverflowError: int too big to convert`. A single READ reply cannot reach that size (MAX_READ is 1 MiB), but a compound reply is the sum of its parts and is built with no bound, so a client chaining enough large READs in one compound makes the server compute a full reply and then die mid-send — and because OverflowError is not an OSError, it escapes `_serve_client`'s handlers entirely and kills the thread with a traceback instead of closing the connection cleanly.
- **Fix:** Check the length in `send_msg` and raise a defined error (or cap compound reply size in `_dispatch_all`); at minimum, make the failure an OSError subclass so the existing connection teardown handles it.
- **Benefit:** An oversized compound degrades to a dropped connection with a log line, not an unhandled thread death.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED for the OverflowError; thread death INFERRED from the `except` clauses read at 385-415)

### RE-SERV-017 — No resource limits: unbounded connection threads, no timeout on accepted sockets, unbounded handles and trees per connection
- **Filed as:** PYSMB2-18 (pysmb2.md)
- **Category:** 4
- **Locations:** smb2_server/smb2_server.py:337-362 (`serve_forever` spawns a daemon thread per connection, no cap), smb2_server/smb2_server.py:385-404 (`_serve_client`: the accepted socket gets TCP_NODELAY at :390 and no timeout — the only `settimeout` in the server is on the *listening* socket at :342), smb2_server/smb2_server.py:245-251 (`add_handle` never refuses), smb2_server/smb2_server.py:949-972 (TREE_CONNECT allocates a tree per request, no cap)
- **Problem:** A peer can (a) open connections without limit, each costing a thread blocked forever in `recv_msg` even if it never sends a byte; (b) on one authenticated connection, CREATE handles and TREE_CONNECT trees without limit, growing `conn.handles` / `conn.trees` until the process runs out of memory or descriptors (handles hold no descriptor until first use, but each is an object plus, for directories, a listing snapshot). Nothing in the serving path — grep-verified — sets a client-socket timeout or counts connections, handles or trees. For a server whose deployment is "a PC on a LAN", one misbehaving or hostile client is a whole-process denial of service.
- **Fix:** Set a generous idle timeout on accepted sockets and a per-connection cap on handles/trees (refusing with the already-defined STATUS_INSUFFICIENT_SERVER_RESOURCES); a global connection cap can be a simple counter in `serve_forever`.
- **Justification:** (only if fix ADDS) These are three small checks at three existing allocation points, not a resource-management subsystem; each reuses a status the wire module already defines, and the alternative — documenting "one bad client kills the server" — leaves the stated LAN deployment one port-scan away from a hang.
- **Benefit:** Worst-case memory/thread usage becomes a function of configured caps, not of peer behaviour.
- **Effort:** M · **Risk:** Med · **Confidence:** Med (READ)

### RE-SERV-018 — Handles name paths, not files: lazy open-by-path serves whatever the path names at first use
- **Filed as:** PYSMB2-19 (pysmb2.md)
- **Category:** 6
- **Locations:** smb2_server/smb2_server.py:1114-1127 (`_open_file` opens `h.path` on first READ/WRITE), smb2_server/smb2_server.py:1049-1053 (CREATE records the path and opens nothing), smb2_server/smb2_server.py:1069-1088 (CLOSE stats and deletes by path), smb2_server/smb2_server.py:1290-1293 (QUERY_INFO stats by path)
- **Problem:** A handle pins a pathname, not a file. Between CREATE and first use, anything that replaces the file at that path — another SMB client renaming over it, a local process, a sync tool — silently retargets the handle. Executed proof: a handle created on a file containing `ORIGINAL` returned `REPLACED!` from `h_read` after the file was swapped with `os.replace`, with no error and no new CREATE. The mirror cases follow from the same mechanism: a file deleted and recreated is read as the new file; QUERY_INFO and CLOSE's post-query attributes describe whatever is at the path *now*, not what was opened; delete-on-close can remove a file the handle's creator never opened. The `_open_file` docstring gives the reason for laziness (descriptor pressure from Explorer's attribute-only opens) — the defect is not laziness itself but that the CREATE-time `os.stat` (smb2_server.py:1042) is never compared against the file eventually opened.
- **Fix:** Keep the lazy open, but remember the CREATE-time identity (`st_dev`, `st_ino`) on the handle and verify it at first `_open_file`; on mismatch, fail with STATUS_FILE_CLOSED instead of serving the replacement. That keeps the descriptor saving and closes the retargeting.
- **Justification:** (only if fix ADDS) Two integers per handle and one comparison at the single lazy-open point — no new abstraction, and it converts a silent wrong-file serve into a clean error.
- **Benefit:** A handle means the file that was opened, across the whole multi-client lifetime of the share.
- **Effort:** M · **Risk:** Med · **Confidence:** High (EXECUTED)

### RE-SERV-019 — FileAccessInformation always reports full access (0x001F01FF)
- **Filed as:** PYSMB2-20 (pysmb2.md)
- **Category:** 6
- **Locations:** smb2_wire.py:437-439
- **Problem:** `file_info` answers FILE_ACCESS_INFORMATION with the constant 0x001F01FF (FILE_ALL_ACCESS) for every file, in every share, through every handle — including on a read-only share and through a handle the same server would refuse a WRITE on (executed proof: queried with `read_only=True`, the mask returned is still 0x001F01FF). Clients use this class to decide what they may attempt; telling every client it has full access manufactures the very ACCESS_DENIED / MEDIA_WRITE_PROTECTED errors the access mask exists to prevent, and misleads any client that caches permissions from it.
- **Fix:** Derive the mask from the state the caller already passes in: when `read_only` is set, return the read/execute subset (the same 0x001200A9 shape `h_tree_connect` already uses for read-only shares at smb2_server.py:969) instead of full access.
- **Benefit:** The reported access agrees with the access the server will actually enforce.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED)

### RE-SERV-020 — Rename ignores the RootDirectory field, so a directory-relative rename lands at the share root
- **Filed as:** PYSMB2-21 (pysmb2.md)
- **Category:** 6
- **Locations:** smb2_server/smb2_server.py:1340-1357
- **Problem:** FileRenameInformation carries a RootDirectory handle (payload bytes 8–15): when it is non-zero, the FileName is relative to that directory. The handler reads only ReplaceIfExists (byte 0) and FileNameLength (offset 16), never looks at bytes 8–15, and resolves the name against the share root (`h.share.resolve(target)`). A client performing a rename relative to an open directory handle therefore moves its file to the share root instead — and with ReplaceIfExists set, can overwrite an unrelated same-named file at the root. The raw target string is also stored verbatim as `h.rel` (line 1356), so a later QUERY_INFO reports the un-normalised name.
- **Fix:** If the RootDirectory field is non-zero, either resolve the name relative to that handle's directory (the handles table is right there) or refuse with STATUS_NOT_SUPPORTED; do not silently reinterpret the name against a different base. Store the share-relative result of the resolution as `h.rel`, not the raw client string.
- **Benefit:** A rename goes where the client addressed it, or fails honestly — never to a different directory by default.
- **Effort:** S · **Risk:** Low · **Confidence:** Med (READ)

### RE-SERV-021 — The account password is taken on the command line, where any local user can read it
- **Filed as:** PYSMB2-22 (pysmb2.md)
- **Category:** 6
- **Locations:** smb2_server/smb2_server.py:1741-1742 (`--user NAME:PASSWORD`), smb2_server/smb2_server.py:1708-1724 (`build_authenticator` splits the spec), smb2_server/smb2_server.py:44 (the module docstring's own example shows `--user ripto:secret`)
- **Problem:** A password in argv is visible in the process list (`ps`, Task Manager's command-line column), in shell history, and in any launcher log that records how the server was started. The server is normally started by a GUI launcher on a shared family PC — exactly the setting where other local users/processes can see a process command line. This is the one place credentials exist outside the NTLM exchange, and it is the least protected.
- **Fix:** Accept users from a file (e.g. `--users-file` with `NAME:PASSWORD` lines, read once at startup) in addition to `--user`, and steer the launcher/docstring example to it; keep `--user` working for compatibility.
- **Justification:** (only if fix ADDS) One additional way to read the same `user_specs` list `build_authenticator` already consumes — no new config format or subsystem — and it removes the password from the one place it is broadcast to every local process.
- **Benefit:** The share password stops being world-readable on the host for the whole life of the server process.
- **Effort:** M · **Risk:** Low · **Confidence:** Med (READ)

### RE-SERV-022 — The `--smb-version 2/3` SMB2 handlers are stubs that claim success without doing any work
- **Filed as:** PYSMB1-01 (pysmb1core.md)
- **Category:** 8
- **Locations:** smbv1_server/smbserver_opl.py:537-637 (all `h2_*` handlers + `SMB2_HANDLERS`), smbv1_server/smbserver_opl.py:1435-1458 (SMB2 dispatch in `serve_conn`), smbv1_server/smbserver_opl.py:1724-1729 (the `main()` warning that admits this)
- **Problem:** Every SMB2 handler ignores the request body and fabricates a successful reply: `h2_session_setup` always succeeds as guest (no credential check, and the SMB2 dispatch branch never applies the SMB1 `_logon_required` gate), `h2_tree_connect` succeeds for any share name without consulting `server.shares`, `h2_create` returns one constant FileId and `FILE_OPENED` for any path, existing or not, `h2_read` always returns zero bytes with `STATUS_SUCCESS`, and `h2_write` returns `STATUS_SUCCESS` with a written count of 0 and writes nothing — including on a read-only server, where a real write would be refused (executed: `h2_write` on a `read_only=True` server returned status 0). `h2_negotiate` also embeds a fixed sequential-bytes server GUID. The command map is additionally misaligned with SMB2: 0x0012 (OPLOCK_BREAK) is mapped to the echo handler, while the real ECHO command (0x000D) and QUERY_DIRECTORY (0x000A) fall through to NOT_IMPLEMENTED, so the share can never be listed. `main()` prints an "INCOMPLETE" warning, but the flag remains a documented `--help` choice and any client that connects sees success-shaped replies and an empty share rather than an error.
- **Fix:** Either delete the SMB2 stub, the `SMB2_HANDLERS` dispatch branch, and the `--smb-version 2/3` choices (the repo already has a real SMB2 server in `smb2_server/`), or refuse at startup when `--smb-version != 1`. Do not keep handlers that return success for work not done.
- **Benefit:** Removes ~130 lines of fake protocol surface, an auth-bypassing dispatch branch, and a silent-data-loss trap (a client told a write succeeded when nothing was written).
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED — `h2_write`/`h2_read`/`h2_create` return values captured; dispatch and map read in full)

### RE-SERV-023 — Short or malformed parameter blocks raise `struct.error` out of the handlers and kill the connection, with no SMB error reply
- **Filed as:** PYSMB1-02 (pysmb1core.md)
- **Category:** 6
- **Locations:** smbv1_server/smbserver_opl.py:957-985 (`h_read_andx`), smbv1_server/smbserver_opl.py:1012-1035 (`h_write_andx`), smbv1_server/smbserver_opl.py:1114-1126 (`_trans2_parse`), smbv1_server/smbserver_opl.py:1258-1262 (`h_trans2_find_next2`), smbv1_server/smbserver_opl.py:1421-1510 (`serve_conn`: dispatch inside a `try` whose only handler is `except (ConnectionError, OSError)`)
- **Problem:** Several handlers unpack fixed offsets from `r.params` / `t_params` without checking the client-supplied WordCount first — unlike `h_session_setup` (wordcount guard) and `h_nt_create_andx` (`len(r.params) < 39` guard), which show the file already knows the pattern. `Req` itself silently truncates `params` when the declared WordCount overruns the message, so a short block reaches the handlers intact as a short buffer. Executed: `h_read_andx` with a 2-byte params block raises `struct.error`, and `_trans2_parse` with a 2-byte params block raises `struct.error`. `struct.error` is not an `OSError`, so it escapes `serve_conn`'s exception handler: the thread dies with a traceback, the session is dropped mid-stream, and the client gets no error response. One malformed packet from any client kills its own session (the server process survives, since each connection is a thread).
- **Fix:** Validate minimum params length once per handler that needs it (the guard style already used in `h_nt_create_andx`), returning `STATUS_ACCESS_DENIED`/an error status instead of raising; or catch `struct.error` alongside `OSError` in `serve_conn` and fail the request, not the connection. The per-handler guard is the smaller, more local change.
- **Benefit:** A buggy or hostile client gets an SMB error and keeps (or cleanly loses) its session by protocol, instead of an unexplained disconnect with a server-side traceback.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED for both cited handlers; the escape path is READ)

### RE-SERV-024 — A non-ASCII configured password makes every login attempt raise `TypeError` in `hmac.compare_digest`
- **Filed as:** PYSMB1-03 (pysmb1core.md)
- **Category:** 6
- **Locations:** smbv1_server/smbserver_opl.py:719-750 (`h_session_setup`, `compare_digest` at line 746), smbv1_server/smbserver_opl.py:1731-1747 (`--user` parsing in `main()`, which accepts any password string)
- **Problem:** The incoming password is decoded with `"ascii", "ignore"`, so it is always pure ASCII. The configured password is taken verbatim from `--user NAME:PASSWORD`, with no ASCII restriction. `hmac.compare_digest` on two `str` operands raises `TypeError` when either contains non-ASCII characters (executed: `hmac.compare_digest("abc", "päss")` → `TypeError: comparing strings with non-ASCII characters is not supported`). So a server configured with, e.g., a password containing an accented character does not get "wrong password → LOGON_FAILURE"; it gets an uncaught `TypeError` out of `h_session_setup`, which (per PYSMB1-02's dispatch) kills the connection thread on every single login attempt, with no hint that the configuration is the cause.
- **Fix:** Compare on bytes: encode both sides (e.g. UTF-8) before `compare_digest`, so any configured password either matches or fails as a normal logon failure.
- **Benefit:** Credential checking can never crash the session handler; misconfigurations degrade to ordinary logon failures.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED — the `TypeError` is reproduced; the asymmetry between the ASCII-forced wire password and the unrestricted configured password is READ)

### RE-SERV-025 — No bound tying accepted message size to the advertised buffer, no connection idle timeout, and unbounded thread-per-connection growth
- **Filed as:** PYSMB1-06 (pysmb1core.md)
- **Category:** 4
- **Locations:** smbv1_server/smbserver_opl.py:183-207 (`_recv_exact` / `recv_msg`: 24-bit NetBIOS length accepted as-is), smbv1_server/smbserver_opl.py:676-690 (`h_negotiate` advertises MaxBufferSize 65535), smbv1_server/smbserver_opl.py:1843-1862 (accept loop spawning an unbounded daemon thread per connection; accepted sockets are blocking with no timeout)
- **Problem:** `recv_msg` trusts the 24-bit length field, so a single "message" can make the server buffer up to ~16.7 MiB — roughly 256× the 64 KiB maximum buffer size the server itself negotiates — before any header validation happens. A connection that opens and then sends nothing (or trickles) holds its thread and file descriptor forever: there is no socket timeout or idle expiry on connection sockets, and the accept loop imposes no cap on concurrent connection threads. A handful of idle or slow clients therefore pins unbounded threads and up to ~16.7 MiB of buffer each, on a server whose legitimate traffic is ≤64 KiB messages from one console.
- **Fix:** Reject (drop the connection) when the NetBIOS length exceeds a bound tied to the negotiated maximum (e.g. 128 KiB of slack over MaxBufferSize), and give accepted sockets an idle timeout. No new configuration is needed; both can be constants.
- **Benefit:** Per-connection memory becomes bounded and proportional to real protocol traffic, and idle/dead connections release their thread and descriptors on their own.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ — length handling, advertised maximum, and accept loop all read in full; no timeout or cap exists anywhere in the file)

### RE-SERV-026 — QUERY_INFORMATION_DISK reports invented disk geometry and free space
- **Filed as:** PYSMB1-09 (pysmb1core.md)
- **Category:** 6
- **Locations:** smbv1_server/smbserver_opl.py:1064-1068 (`h_query_disk`: hard-coded `0xFFFF` total units and `0xFFFF` free units, with the comment "smbman just needs non-zero free space")
- **Problem:** Total and free space are constants (~2 GiB at the packed 64×512 scale) regardless of the actual volume. Any client that consults free space before creating or extending a file (a VMC image is the obvious case on this server) is told there is room even when the disk is full or nearly full, and only learns otherwise from a later write failure — or, on a large volume, is told the share is far smaller than it is.
- **Fix:** Fill the fields from `shutil.disk_usage` on the share root of the request's tree, scaled/clamped into the u16 fields as the current packing requires.
- **Benefit:** The one disk query the server answers stops being fabricated; clients making space decisions get real numbers.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-027 — Boolean environment values outside a four-word vocabulary silently parse as False — including `RO`, so `RO=2` yields a writable server with no warning — and env-enabled booleans have no CLI off-switch
- **Filed as:** PYSMB1-21 (pysmb1core.md)
- **Category:** 6
- **Locations:** udpfs_server/ps2servers_core.py:535-560 (`--read-only`, `--verbose`, `--single-port`, `--modulo-mode`, `--metrics`, `--session-summary`, all `action="store_true"` with `_env_bool` defaults), udpfs_server/udpfs_server.py:3297-3302 (`_env_bool`: only `1/true/yes/on` are True; everything else, recognised or not, is False)
- **Problem:** Two asymmetries compound. First, vocabulary: `_env_bool` treats any unrecognised value as False without a warning. Executed: `RO=2` produces `read_only = False`. For most flags that is a cosmetic surprise; for `RO` it silently defeats a safety setting — an operator who set `RO=2`, `RO=enabled`, or `RO=t` believing the share is read-only gets a read-write server and no message saying so. Second, direction: because these are `store_true` flags whose defaults come from the environment, an env-enabled boolean cannot be turned off from the command line at all — there is no `--no-read-only` counterpart (compression, tellingly, did get an explicit `--enable/--no` pair in this same parser).
- **Fix:** Warn on any non-empty boolean env value that is in neither the true nor the false vocabulary (`0/false/no/off`/empty), so an unrecognised value is loud rather than silently False. That is the whole fix for the safety half; adding negative flags is optional and larger.
- **Benefit:** A misspelled read-only setting can no longer silently produce a writable share.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED for `RO=2`; the vocabulary and the absence of negative flags are READ)

### RE-SERV-028 — GETSTAT on a symlink pointing outside the root raises TypeError and sends no reply
- **Filed as:** PYUDPFS-01 (pyudpfs.md)
- **Category:** 6
- **Locations:** `udpfs_server/udpfs_server.py:2294-2356` (`_handle_getstat`; the fall-through `os.stat(resolved)` is at line 2356)
- **Problem:** `_handle_getstat` computes `real_file_exists = (resolved is not None and os.path.exists(resolved)) or real_entry_exists`, where `real_entry_exists` is `os.path.lexists()` of the `_resolve_entry()` path. For a symlink inside the root whose target is outside it, `_resolve_path()` returns `None` (containment correctly refuses the target) but `lexists()` on the link itself is `True`, so the handler skips the ENOENT/EACCES branch and calls `os.stat(None)`. That raises `TypeError`, which the `except OSError` around it does not catch; the session worker's generic catch logs it and the client is never answered — it waits out its own timeout. EXECUTED in-process: `GETSTAT /link.bin` (link → file in a temp dir outside the root) raised `TypeError: stat: path should be string, bytes, os.PathLike or integer, not NoneType`; `_resolve_path -> None`, `_resolve_entry -> <root>/link.bin`. A dangling symlink to an *inside* target works (stat raises `OSError`, answered), so only the outside-target case crashes. `_handle_open` does not have this bug — it refuses when `resolved is None` and no compressed/virtual alias resolved.
- **Fix:** in `_handle_getstat`, after the not-found branch, if `resolved is None` send `-errno.EACCES` and return (mirroring `_handle_open`'s existing guard).
- **Benefit:** an ordinary library layout (a link whose target share is unmounted or outside the served folder) stops hanging the console's stat call.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED)

### RE-SERV-029 — BREAD/BWRITE never validate the sector range against the device or file size; BWRITE past the end extends the host image and reports success
- **Filed as:** PYUDPFS-02 (pyudpfs.md)
- **Category:** 6
- **Locations:** `udpfs_server/udpfs_server.py:2483-2549` (`_handle_bread`), `udpfs_server/udpfs_server.py:2551-2630` (`_handle_bwrite_req`), `udpfs_server/udpfs_server.py:2156-2187` (`_complete_bwrite`), `udpfs_server/udpfs_server.py:862-880` (`bd_read`/`bd_write`); `bd_sector_count` is computed at `udpfs_server/udpfs_server.py:807` but used only for the startup banner (line 1055) and the GETSTAT capacity reply (line 2307)
- **Problem:** both handlers cap the *byte count* (`max_transfer_bytes`) and refuse offsets past 2⁶³−1, but neither compares `sector_nr + sector_count` against `bd_sector_count` (handle 0) or the backing file's sector count (other handles). EXECUTED in-process against a 4-sector (2,048-byte) image: `BWRITE_REQ sector=100 count=1` plus one chunk returned `WRITE_DONE 0` and grew the host image file from 2,048 to 51,712 bytes via `pwrite` past EOF. Repeated out-of-range writes let any unauthenticated LAN peer grow/sparsify the image file arbitrarily (one ≤8 MiB write per request, unbounded count of requests) and write into space the console believes is a fixed-size device. BREAD past the end is the benign mirror: it silently returns a short/empty read instead of an error.
- **Fix:** in `_handle_bread` and `_handle_bwrite_req`, when `handle == BLOCK_DEVICE_HANDLE` refuse `sector_nr + sector_count > self.bd_sector_count` with `-errno.EINVAL`; for file handles, refuse a BWRITE range past the handle's current size in sectors the same way.
- **Benefit:** the block device the server advertises (its sector count is already computed and already told to clients via GETSTAT) becomes the actual bound; closes an unauthenticated disk-fill path.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED)

### RE-SERV-030 — WRITE_DATA trusts the chunk header: `chunk_size` is never checked against the datagram, `total_chunks` is re-read from every chunk, and BWRITE completion reports success for short data
- **Filed as:** PYUDPFS-03 (pyudpfs.md)
- **Category:** 6
- **Locations:** `udpfs_server/udpfs_server.py:2083-2140` (`_handle_write_data`; slice at line 2090, buffer extend at 2122, `write_total_chunks` overwritten at 2123), `udpfs_server/udpfs_server.py:2031-2081` (`_handle_write_req`; declared `size` is checked against the cap only and never stored), `udpfs_server/udpfs_server.py:2156-2187` (`_complete_bwrite`)
- **Problem:** `chunk_data = payload[8:8 + chunk_size]` silently truncates when the datagram carries fewer bytes than `chunk_size` claims, and the truncated chunk still counts as received. Completion is decided by chunk *count* (`write_received_chunks >= total_chunks`, with `total_chunks` taken from the most recent chunk, so a sender can shrink or extend the transfer mid-write), never by byte count against the size declared in WRITE_REQ or the `sector_count × sector_size` declared in BWRITE_REQ. EXECUTED in-process: (a) a file WRITE whose single chunk claimed `chunk_size=8` but carried 3 bytes completed and wrote the 3 bytes with no error; (b) a BWRITE declaring 4 sectors (2,048 bytes) but delivering one 100-byte chunk returned `WRITE_DONE 0` (success), wrote 100 bytes, and credited `stats['bytes_written']` with the full 2,048 (`_complete_bwrite` adds `expected_size` unconditionally and always replies 0). A lost/truncated final datagram therefore corrupts a save or image silently instead of failing the write.
- **Fix:** in `_handle_write_data`, drop/refuse the chunk when `len(payload) - 8 < chunk_size`; latch `total_chunks` from the first chunk; at completion require `len(write_data)` to equal the declared size (WRITE_REQ) or `expected_size` (BWRITE) and answer `-errno.EIO` otherwise.
- **Benefit:** short or lying transfers fail loudly at WRITE_DONE instead of persisting truncated data as a successful write.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED)

### RE-SERV-031 — Block-device handle 0 is one shared file object with one shared cursor across all sessions for READ, LSEEK and file-WRITE
- **Filed as:** PYUDPFS-04 (pyudpfs.md)
- **Category:** 6
- **Locations:** `udpfs_server/udpfs_server.py:3212-3213` (every `Session` inserts the *same* `server.bd_fh` FileHandle as handle 0), `udpfs_server/udpfs_server.py:1996` (`_handle_read`: `fh.obj.read(size)`), `udpfs_server/udpfs_server.py:2206` (`_handle_lseek`: `fh.obj.seek(offset, whence)`), `udpfs_server/udpfs_server.py:2144` (`_complete_write`: `fh.obj.write(data)`)
- **Problem:** BREAD/BWRITE on handle 0 correctly use the positional, lock-aware `bd_read`/`bd_write` (lines 862-880), but READ, LSEEK and WRITE_REQ-style writes on the same handle go through the shared object's own file position, which no lock guards and every session shares. EXECUTED in-process with two sessions on one 4-sector image: session B read 512 bytes (sector 0), session A then LSEEKed handle 0 to sector 3, and session B's next READ returned sector 3's bytes instead of sector 1's. On hosts without `pread`/`pwrite` the same cursor is also what the `bd_lock` fallback in `bd_read`/`bd_write` seeks, so an LSEEK can land between another session's seek and read there too. Two consoles using the block device through the file opcodes corrupt each other's stream position deterministically.
- **Fix:** special-case handle 0 in `_handle_read`/`_handle_lseek`/`_complete_write` the way BREAD/BWRITE already do — track a per-session cursor for handle 0 and serve it via `bd_read`/`bd_write` — or refuse file-opcode access to handle 0 with `-errno.EBADF` since the block opcodes cover it.
- **Benefit:** per-client isolation, the property the multi-client refactor exists for, stops leaking through the oldest handle in the table.
- **Effort:** M · **Risk:** Med · **Confidence:** High (EXECUTED)

### RE-SERV-032 — No writer exclusion anywhere: two sessions can hold the same file open writable and interleave writes
- **Filed as:** PYUDPFS-05 (pyudpfs.md)
- **Category:** 6
- **Locations:** `udpfs_server/udpfs_server.py:1923-1928` (`_handle_open` opens a fresh OS handle per session, no per-path registry), `udpfs_server/udpfs_server.py:784-786` (the only locks in the file are `sessions_lock`, `send_lock`, `bd_lock` — census by grep)
- **Problem:** read-only semantics are enforced (global `read_only` flag plus the per-handle `writable()` gate), and no file in scope *claims* single-writer semantics — but none is provided either. Any two consoles (or one console twice) can OPEN the same path `r+b`, and their writes land at independent cursors with no error to either; the same is true of handle 0 via PYUDPFS-04's file-WRITE path. For game saves and VMC images — the writable files this server exists to serve — that is silent interleave corruption with both clients told the writes succeeded. This is filed as a semantics gap, not a violated claim: if multi-writer is intended, it is currently indistinguishable from an accident.
- **Fix:** keep a server-level dict of paths with a writable handle open (populated in `_handle_open`, cleared on close/reap); refuse a second writable open of the same resolved path with `-errno.EBUSY`. Read opens stay unrestricted.
- **Justification:** the addition is one dict and one check on an existing path, and it is the only thing that makes the existing read-only/`writable()` story coherent for the multi-client server this file now is.
- **Benefit:** second writer gets an immediate, diagnosable refusal instead of both writers corrupting the file.
- **Effort:** M · **Risk:** Med · **Confidence:** Med (READ)

### RE-SERV-033 — READ/BREAD on a handle that is not open for reading crashes the reply: `-e.errno` on an errno-None exception
- **Filed as:** PYUDPFS-06 (pyudpfs.md)
- **Category:** 6
- **Locations:** `udpfs_server/udpfs_server.py:1969-2010` (`_handle_read` and its `except OSError` reply), `udpfs_server/udpfs_server.py:2483-2549` (`_handle_bread`, same pattern), `udpfs_server/udpfs_server.py:1469-1495` (`_flags_to_mode` returns `'wb'`/`'ab'` for O_WRONLY|O_TRUNC / O_APPEND), `udpfs_server/udpfs_server.py:554-590` (`FileHandle` has `writable()` but no readable counterpart)
- **Problem:** writes are pre-gated by `writable()` precisely because an `io.UnsupportedOperation` has `errno is None` (the class comment at lines 566-590 says so), but reads have no symmetric gate. EXECUTED in-process: OPEN a file with O_WRONLY|O_TRUNC (mode `'wb'`), then READ_REQ — `fh.obj.read()` raised `io.UnsupportedOperation`, the handler logged it and then itself raised `TypeError: bad operand type for unary -: 'NoneType'` computing `-e.errno` for the error reply. No reply is sent; the console waits out its timeout. The same `-e.errno` shape appears in `_handle_bread`'s except clause (an `'ab'`-mode handle BREAD hits it via seek+read).
- **Fix:** cheapest correct step: in the READ/BREAD except clauses send `-(e.errno or errno.EIO)`. Better, and symmetric with the existing write gate: add a `readable()` check next to `writable()` and refuse non-readable handles with `-errno.EACCES` before touching the object.
- **Benefit:** a mode-confused or hostile client gets an error reply instead of a hung request and a thread traceback.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED)

### RE-SERV-034 — Serving-path compressed wrappers trust header geometry without bounds; the limits module only protects the opt-in inspection path
- **Filed as:** PYUDPFS-07 (pyudpfs.md)
- **Category:** 6
- **Locations:** `udpfs_server/compressed_iso/cso.py:54-89` (only validation is `block_size == 0`; offset table read of `(_num_blocks + 1) * 4` bytes at line 84), `udpfs_server/compressed_iso/zso.py:64-107` (`header_size`, `align`, version byte all taken from the file unchecked), `udpfs_server/compressed_iso/chd.py:262-314` (only `hunk_size == 0` checked; `ctypes.create_string_buffer(hunk_size)` at line 314), `udpfs_server/compressed_iso/base.py:134-168` (block cache evicts by block *count*, so cache memory is `cache_size × block_size` with `block_size` attacker-chosen), `udpfs_server/udpfs_server.py:364-387` (`open_compressed` never passes `strict`), `udpfs_server/compressed_iso/limits.py:12-15` (the `MAX_DISC`/`MAX_BLOCK`/`MAX_TABLE`/`MAX_DECODE` caps that exist but do not apply here)
- **Problem:** the server decompresses whatever images sit in the served library — files the user downloaded — triggered by unauthenticated console requests, yet the non-strict parse paths validate almost nothing the header claims. EXECUTED in-process: a 24-byte file containing only a CSO header claiming 64 GiB uncompressed with 2,048-byte blocks made `CsoFileWrapper` allocate **134.2 MB** (tracemalloc peak) reading its "offset table" before failing as truncated; the allocation scales linearly with the claimed size (a claim near 2⁶⁴ requests an exabyte-scale read and dies as `MemoryError`, which `_handle_open`'s `except (ImportError, ValueError, OSError, struct.error)` does not catch). ZSO has the same table construction; CHD turns a header `hunk_size` into a ctypes buffer; the base cache multiplies the same unchecked `block_size` by 32 entries. `limits.py` already contains the right ceilings, but they run only when `strict=True` — i.e. for launcher metadata inspection, not for the network serving path.
- **Fix:** enforce the numeric ceilings (uncompressed size, block/hunk size, table bytes — the `limits.py` constants) inside the non-strict `_parse_header` implementations as well. Do not import the inspection path wholesale: its format restrictions (version, sector alignment) are deliberately stricter than serving compatibility allows; only the resource ceilings transfer.
- **Benefit:** a crafted or corrupt image in the library costs a refused OPEN instead of a memory blow-up of the whole server process.
- **Effort:** M · **Risk:** Med · **Confidence:** High (EXECUTED for the CSO leg; READ for ZSO/CHD/cache legs)

### RE-SERV-035 — MKDIR passes the wire `mode` to `os.mkdir` unsanitised
- **Filed as:** PYUDPFS-14 (pyudpfs.md)
- **Category:** 6
- **Locations:** `udpfs_server/udpfs_server.py:2370-2392` (`_handle_mkdir`; `os.mkdir(resolved, mode if mode else 0o755)` at line 2392)
- **Problem:** `mode` is a raw uint16 from the request. It is passed to `os.mkdir` masked only by the process umask, so the special bits survive: an unauthenticated client can create setgid (0o2000) or sticky directories anywhere in the served root. Impact is small — the client can already create and delete plain directories — but it is the one place a wire field reaches a filesystem permission verbatim, and setgid directories change group-inheritance semantics for everything later written under them by the *host* user.
- **Fix:** `os.mkdir(resolved, (mode & 0o777) if mode else 0o755)`.
- **Benefit:** directory creation stays inside the permission model the console protocol actually has (plain rwx bits).
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-036 — UDPBD file-backed BlockDevice has no extent bounds: out-of-range WRITE silently extends the image, out-of-range READ silently returns zeros
- **Filed as:** PYSMALL-01 (pysmall.md)
- **Category:** 6
- **Locations:** `udpbd_server/udpbd_server.py:118-130` (`BlockDevice.seek/read/write`), `udpbd_server/udpbd_server.py:205-222` (`_handle_read`: bounds check only under `is_raw`), `udpbd_server/udpbd_server.py:238-261` (`_handle_write`: no bounds check at all)
- **Problem:** `BlockDevice.seek` is a bare `self._f.seek(sector * SECTOR_SIZE)` with no comparison against `self.size`; `read` deliberately zero-pads past EOF ("past end of image — pad so packet sizing stays correct") and `write` writes wherever the seek landed. The only extent check in the server (`sector_nr + sector_count > sector_count()`, line 208-209) is gated on `getattr(self.bd, "is_raw", False)`, which the plain file device never sets — both raw backends set `is_raw = True` and enforce the same bound again in their own `seek` (`launcher/raw_storage.py:37-45`, `launcher/virtual_exfat.py:228-233`, read for contrast). So the file device — the default mode — is the only one of the three backends with no clamp. EXECUTED (fake socket, 4-sector image): a `CMD_WRITE` for sector 1000 count 2 was accepted (`_write_left = 1024`), and the following WRITE_RDMA grew the file from 2,048 to 513,024 bytes and replied success; a direct `seek(100)` + 512-byte write grew a 1,024-byte image to 51,712 bytes; a read at sector 500 of a 2-sector image returned 512 zero bytes. A buggy or hostile LAN client can therefore bloat/corrupt the served image with sparse writes at offsets up to ~2 TiB (32-bit sector × 512), and reads past the end fabricate a zero-filled disc instead of failing.
- **Fix:** In `_handle_read`/`_handle_write`, drop the `is_raw` gate and reject (or clamp-and-error) any request with `sector_nr + sector_count > self.bd.sector_count()` for every backend — the check already exists, it is just conditional on the wrong thing. Keep `BlockDevice.read`'s padding for short final reads within the image.
- **Benefit:** The default serving mode gets the same extent guarantee the raw and virtual modes already have; a malformed request can no longer resize the user's image file.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED)

### RE-SERV-037 — UDPBD read-only file device silently swallows an entire write transfer and only reports failure at the very end — unlike both other backends, which raise on the first write
- **Filed as:** PYSMALL-04 (pysmall.md)
- **Category:** 6
- **Locations:** `udpbd_server/udpbd_server.py:127-130` (`BlockDevice.write`: `if self.read_only: return`), `udpbd_server/udpbd_server.py:245-261` (`_handle_write` warns, then still seeks and arms `_write_left`), `udpbd_server/udpbd_server.py:285-304` (failure result `-1` only computed once `_write_left` reaches 0)
- **Problem:** On a read-only image the server accepts the `CMD_WRITE` handshake, receives and discards every WRITE_RDMA packet (`BlockDevice.write` is a silent no-op), and only after the client has sent the whole region does it reply `-1`. Both other backends behave differently: `RawDevice.write` raises `PermissionError(EROFS)` (`launcher/raw_storage.py:78-80`) and `VirtualExfat.write` raises the same (`launcher/virtual_exfat.py:319-320`), which `_handle_write_rdma`'s `OSError` path turns into an immediate `-1` reply and abort. The file backend's silent no-op also means any future caller of `BlockDevice.write` gets a false impression of success — the "signal failure instead of faking success" intent in the RDMA handler's own comment is undermined one layer down.
- **Fix:** Make `BlockDevice.write` raise `PermissionError(errno.EROFS, ...)` when `read_only`, exactly like the two other backends; the existing `OSError` handling in `_handle_write_rdma` then produces the prompt `-1` with no handler changes.
- **Benefit:** Read-only write attempts fail on the first packet instead of after a full transfer, and all three backends share one write-failure contract.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-038 — HTTP legacy-flat profile serves symlinks that escape the games folder; the containment check exists but is only applied in the path-aware profile
- **Filed as:** PYSMALL-06 (pysmall.md)
- **Category:** 6
- **Locations:** `http_server/http_server.py:319-343` (`_scan_dirs`: follows a symlinked `DVD`/`CD` directory via `os.path.isdir`), `http_server/http_server.py:439-499` (`_entry_for`: flat branch never checks containment; `os.path.getsize`/`os.path.isfile` follow symlinks), `http_server/http_server.py:389-392` (`_contained`, defined once), `http_server/http_server.py:501-511` (`_path_aware_entry`: the only caller of `_contained`)
- **Problem:** The class docstring claims the index whitelist means "there is no traversal surface", and the recursive walker carefully refuses symlinked directories — but the default flat profile has no equivalent guard for files or for the `DVD`/`CD` folders themselves. EXECUTED with `GameIndex` on a scratch tree: a symlink `DVD/SLUS_999.99.Escape.iso` pointing at a file outside the games folder was indexed, listed in `games.csv`, and returned by `lookup()` with the outside file's size; a `DVD` directory that was itself a symlink to an outside folder was likewise scanned and served. The path-aware profile on the same tree correctly skipped the escape ("links outside the games folder"). Anyone who can drop a symlink into the library (sync tools, archive extraction, another app's folder) silently publishes the target file to the LAN.
- **Fix:** Apply the existing `_contained(path)` check in `_entry_for` for both profiles (it is already written and tested by the path-aware path), and skip scan directories whose real path escapes the root in `_scan_dirs`.
- **Benefit:** The flat profile — the default every Docmine17 client uses — gets the escape protection the codebase already built for the other profile; no new mechanism.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED)

### RE-SERV-039 — NFS: WRITE and SETATTR need no OPEN and no valid stateid — OPEN's access checks are dead letter for the write path
- **Filed as:** PYSMALL-10 (pysmall.md)
- **Category:** 6
- **Locations:** `nfs4_server/nfs4_server.py:761-762` (`_op_read`: `r.fixed(16)  # stateid: any`), `nfs4_server/nfs4_server.py:941-959` (`_op_write`: stateid discarded, no open/access check, writes at any offset), `nfs4_server/nfs4_server.py:961-974` (`_op_setattr`: same, truncates/extends to any size), `nfs4_server/nfs4_server.py:880-890` (`_op_close`/`_op_open_confirm` fabricate a `seqid + 1` stateid without checking one exists), `nfs4_server/nfs4_server.py:892-939` (`_op_open`: the access-mode and GUARDED checks that are thereby bypassed)
- **Problem:** EXECUTED against `Nfs4Server(allow_writes=True)` with hand-built COMPOUND records: after only PUTROOTFH + LOOKUP + GETFH (no OPEN ever issued), a WRITE carrying an all-zero stateid overwrote the file's head (`PWNED` read back from disk), and a SETATTR with a fabricated stateid extended the file to 10 MiB. Filehandles are unguessable per run (random 8-byte prefix, `_Handles` at lines 261-284), and writes still require `--allow-writes`, whose no-authentication risk is honestly documented in the module header — so this is not an exposure escalation beyond the documented model. What it does break is the server's own internal contract: `_op_open`'s share-access and create-mode validation can never constrain a write, because the write operations never consult any open state. Any future tightening added to OPEN (read-only opens, per-file policy) would silently not apply.
- **Fix:** Either track issued stateids per connection (the `_Connection` object already exists and holds per-connection file state) and require WRITE/SETATTR to present one, or — the LESS option — delete the pretence: state in `_op_open`'s comment that opens are advisory and all authorisation is the `--allow-writes` gate plus a valid filehandle. Do not leave validated-looking checks that nothing enforces.
- **Benefit:** The write path's real authorisation rule becomes explicit and auditable instead of implied by checks that are never consulted.
- **Effort:** M · **Risk:** Med · **Confidence:** High (EXECUTED)

### RE-SERV-040 — SMB WRITE_ANDX reports only the low 16 bits of the byte count it actually wrote
- **Filed as:** GOPROTO-01 (goproto.md)
- **Category:** 6
- **Locations:** native/ps2servers-edge/internal/smb/handlers.go:499-507
- **Problem:** `writeAndX` builds its 12-byte reply with `CountLow = uint16(written & 0xFFFF)` at params[4:] and hard-zeros the field its own comment names `CountHigh` at params[8:]. A single WRITE_ANDX payload is bounded by the message, not by 64 KiB — `MaxMessage` is 128 KiB (internal/smb/wire.go) — so any write of 65,536+ bytes is under-reported by a multiple of 65,536. Executed probe: a 70,000-byte WRITE_ANDX landed all 70,000 bytes on disk, but the reply reported low=4464, high=0, i.e. 4,464 bytes written. A client that trusts the count (retry/resume logic, or a save verifier) is told a completed write mostly did not happen.
- **Fix:** Write `uint16(written >> 16)` into params[8:] instead of 0.
- **Benefit:** The reply count matches the bytes written for every write the server accepts, up to the full message size.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED)

### RE-SERV-041 — SMB WRITE_ANDX assembles its data length in a plain `int`, the exact 32-bit wrap `readAndX` was fixed for
- **Filed as:** GOPROTO-02 (goproto.md)
- **Category:** 6
- **Locations:** native/ps2servers-edge/internal/smb/handlers.go:479 (defect); native/ps2servers-edge/internal/smb/handlers.go:426-430 (the fixed sibling pattern)
- **Problem:** `writeAndX` computes `n := int(dlenLow) | int(dlenHigh)<<16`. `readAndX` carries a long comment (handlers.go:402-425) explaining that this expression is 32-bit on mipsle/mips/arm/386 — every small target this build exists for — so a high word ≥ 0x8000 sets the sign bit, makes the count negative, and the clamp misses it. `writeAndX` was left with the unfixed form: on a 32-bit build a declared length with `dlenHigh >= 0x8000` makes `n` negative, `req.slice` returns nil for `n <= 0` (internal/smb/server.go, `slice`), and the handler writes zero bytes and returns STATUS_SUCCESS with a zero count — a silent no-op write. The in-file fix (`uint32` assembly, then clamp, then narrow) was applied to one of the two handlers that need it.
- **Fix:** Assemble the length as `uint32(dlenLow) | uint32(dlenHigh)<<16` in `writeAndX`, exactly as `readAndX` does, before any narrowing to `int`.
- **Benefit:** Removes a silent-success zero-byte write on the 32-bit routers Edge targets, and makes the two AndX handlers consistent so the documented trap cannot be re-learned per handler.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-042 — SMB WRITE_ANDX ignores the handle's access mode and re-opens the file by path for writing
- **Filed as:** GOPROTO-03 (goproto.md)
- **Category:** 6
- **Locations:** native/ps2servers-edge/internal/smb/handlers.go:481-493 (write path); native/ps2servers-edge/internal/smb/handlers.go:224 (`openAndX` opens with `os.Open`, read-only); native/ps2servers-edge/internal/smb/handlers.go:359 (`ntCreateAndX` also stores an `os.Open` handle)
- **Problem:** Both open paths store a read-only `*os.File` in the handle and record no access mode. `writeAndX` never looks at that handle's mode (or at `of.fh` at all): it opens a brand-new `os.O_WRONLY` file from `of.path` and writes through it. So a handle the client opened read-only is silently writable, per-handle access simply does not exist in this server, and closing/evicting the stored `fh` has no effect on writability. Executed probe: the handle registered in the probe held a read-only `os.Open` file, and `writeAndX` still wrote 70,000 bytes to the file. The fresh open-per-write also means every WRITE_ANDX pays an open/close pair and bypasses any state on the original descriptor.
- **Fix:** Record the granted access on `openFile` at open time, refuse WRITE_ANDX on a handle not opened for writing, and write through the handle's own descriptor (opened with the right flags at OPEN/NT_CREATE time) instead of re-opening by path.
- **Justification:** This adds one field to an existing struct and deletes the per-write re-open; it adds no new mechanism, abstraction, or configuration.
- **Benefit:** Handle access means what the protocol says it means; a read handle cannot be used to modify a file, and the write path stops paying an extra open/close per request.
- **Effort:** M · **Risk:** Med · **Confidence:** High (EXECUTED)

### RE-SERV-043 — UDPBD read-only writes fake success at the device layer and arm a real write handshake at the handler layer
- **Filed as:** GOPROTO-04 (goproto.md)
- **Category:** 6
- **Locations:** native/ps2servers-edge/internal/udpbd/server.go:89-95 (`device.writeAt`); native/ps2servers-edge/internal/udpbd/server.go:452-469 (`handleWrite`); native/ps2servers-edge/internal/udpbd/server.go:501-533 (the only place failure is ever reported)
- **Problem:** Two stacked lies. (1) `device.writeAt` returns `nil` — success — when the device is read-only, having written nothing; any caller that trusts the error is deceived. (2) `handleWrite` logs "WRITE on a read-only image, ignoring" and then does not ignore it: the `if s.dev.readOnly` block (452-456) has no `return`, so the handler arms a full `writeState` and adds the full byte count to `totalWrite` (460-468). Every following WRITE_RDMA chunk is passed to the no-op `writeAt` and counted as consumed; failure (`WriteDone` result −1) is produced only if the sequence happens to run to completion (521-533). A client that aborts mid-sequence hears nothing at all. Executed probes confirmed both halves: `writeAt` on a read-only device returned `err=<nil>` with the file unchanged, and `handleWrite` on a read-only image left `write.left=1024, totalWrite=1024` for a 2-sector write. Contrast UDPFS's BWRITE path, which refuses a read-only device up front.
- **Fix:** Make `writeAt` return an error (e.g. `syscall.EACCES`, as UDPFS's block device already does) when read-only, and make `handleWrite` return immediately after its read-only warning without arming state or counting bytes.
- **Benefit:** A read-only image refuses writes at the handshake, the device primitive stops reporting success for work it did not do, and no counter claims bytes that were discarded.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED)

### RE-SERV-044 — UDPBD write state is one global slot: any peer's INFO or READ silently destroys another peer's in-flight write (Go Edge and Python UDPBD)
- **Filed as:** GOPROTO-05, PYSMALL-02 (goproto.md)
- **Category:** 6
- **Locations:** native/ps2servers-edge/internal/udpbd/server.go:352-353 (`handleInfo` resets state); native/ps2servers-edge/internal/udpbd/server.go:460-467 (`handleWrite` overwrites state); native/ps2servers-edge/internal/udpbd/server.go:476-490 (victim's RDMA packets are then dropped silently)
- **Problem:** There is exactly one `writeState` for the server. `handleInfo` clears it unconditionally — `s.write = writeState{}` runs for an INFO from *any* peer, with no peer check, even though the struct comment says the peer is recorded precisely so a different console cannot land in the middle of a write. `handleWrite` likewise overwrites the state unconditionally. After either event, the first console's remaining WRITE_RDMA packets fail the `left <= 0 || !peerMatch` test and are dropped with only a Debug log; no `WriteDone` failure is ever sent to the victim, whose console is left believing a save is still in progress. `handleRead`'s state clear is peer-guarded (same peer only), which shows the intended discipline — INFO and WRITE simply don't apply it.
- **Fix:** Only reset the write state in `handleInfo`/`handleWrite` when it is idle or belongs to the requesting peer; if a different peer's write is displaced, send that peer a `WriteDone` failure before discarding its state.
- **Benefit:** One console's discovery broadcast or write attempt can no longer silently corrupt/strand another console's in-flight save.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

**Same mechanism in the parallel implementation** (filed separately as PYSMALL-02 in pysmall.md, merged at consolidation):

- **Category:** 6
- **Locations:** `udpbd_server/udpbd_server.py:151-152` (`_write_left`/`_write_owner` on the single server object), `udpbd_server/udpbd_server.py:198` (`_handle_info` resets `_write_left`), `udpbd_server/udpbd_server.py:214` (`_handle_read` resets `_write_left`), `udpbd_server/udpbd_server.py:248-249` (`_handle_write` overwrites state/owner), `udpbd_server/udpbd_server.py:263-272` (RDMA side does check `_write_owner`)
- **Problem:** The RDMA handler carefully refuses packets from a different `(addr, cmdid)` owner, but the state it protects is global and is zeroed unconditionally by `_handle_info` and `_handle_read` from *any* address, and overwritten by any second client's `CMD_WRITE`. EXECUTED: client A opened a write (`_write_left = 1024`); an unrelated READ from a different address left `_write_left = 0` — A's subsequent RDMA packets are then dropped as "unsolicited" and A never receives a `CMD_WRITE_DONE`, so the console hangs or retries a save that was silently discarded. On a multi-console network (or one console rebooting into INFO while another writes), saves corrupt/hang without any error being logged.
- **Fix:** Only reset `_write_left` in `_handle_info`/`_handle_read` when the packet's address matches `_write_owner` (or there is no owner); a `CMD_WRITE` from a second address while a write is in flight should be refused/ignored rather than stealing the slot.
- **Benefit:** One console's discovery/read traffic can no longer destroy another console's in-flight VMC/save write.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED)

### RE-SERV-045 — UDPFS status "Busy" latches on for the life of a session after its first data packet
- **Filed as:** GOPROTO-07 (goproto.md)
- **Category:** 6
- **Locations:** native/ps2servers-edge/internal/udpfs/status.go:61-82 (`anyTransferInFlight`); native/ps2servers-edge/internal/udpfs/negotiation.go:497 (`st.Streaming = true`); native/ps2servers-edge/internal/udpfs/server.go:190-191 (default `PeerTimeout` of one hour)
- **Problem:** `anyTransferInFlight` reports a transfer in flight when `st.Streaming || st.WriteActive`. `WriteActive` is a real in-flight signal; `Streaming` is not — within the udpfs package it is only ever set to `true` (negotiation.go:497, on the first accepted DATA packet) and is cleared only by a session reset/handoff or by session expiry, which by default takes an hour of idleness. The stated purpose of the Busy state (status.go:39-41) is to warn a launcher not to cut power mid-transfer; as implemented, the server reports Busy continuously from the first packet of a session until up to an hour after the last one, so the warning is permanently on and therefore meaningless — the failure mode the state exists to prevent (a user learning to ignore it) is built in.
- **Fix:** Base Busy on actual in-flight work only (`WriteActive` and an equivalent read/transfer-in-progress signal from the send-window path), not on the latched `Streaming` negotiation flag.
- **Benefit:** The router-status Busy state again means "a transfer is in flight now", which is the only version of it that protects a save.
- **Effort:** M · **Risk:** Med · **Confidence:** High (READ)

### RE-SERV-046 — SMB TRANSACTION handler answers every transaction as a NetShareEnum, ignoring the request entirely — in both the Go Edge SMB server and the Python SMB1 server
- **Filed as:** GOPROTO-17, PYSMB1-08 (goproto.md)
- **Category:** 6
- **Locations:** native/ps2servers-edge/internal/smb/handlers.go:840-871 (`transaction`); native/ps2servers-edge/internal/smb/server.go (dispatch routes `ComTransaction` here unconditionally)
- **Problem:** `transaction` never reads `r.params` or `r.data`: it does not look at the transaction name (`\PIPE\LANMAN` or anything else), the RAP function code, or the requested information level, and unconditionally returns a level-1 share enumeration in a TRANS2-shaped envelope. Any TRANSACTION a client sends — a different RAP call, a different pipe, a malformed one — receives a successful share list as its answer. Today OPL's share picker is the only known sender, which is why this works; but as a protocol handler it claims success for requests it has not identified, and any future or incidental transaction from a client will be silently mis-answered instead of refused with NOT_IMPLEMENTED, which is how the server treats every other unimplemented command.
- **Fix:** Parse the transaction name/function from the request and answer only the NetShareEnum call this handler implements; return `StatusNotImplemented` for anything else.
- **Benefit:** The handler stops returning successful, wrong-shaped data for unidentified requests; unknown transactions fail visibly instead of being mis-answered.
- **Effort:** S · **Risk:** Low · **Confidence:** Med (READ)

---

**Same mechanism in the parallel implementation** (filed separately as PYSMB1-08 in pysmb1core.md, merged at consolidation):

- **Category:** 8
- **Locations:** smbv1_server/smbserver_opl.py:1331-1362 (`h_transaction`: request setup/function word never read)
- **Problem:** The handler is documented as "RAP NetShareEnum — only used when OPL's Share field is empty", but it never inspects the transaction's function code or name. Any `SMB_COM_TRANSACTION` request — any RAP function, any pipe — receives the share-enumeration reply with `STATUS_SUCCESS`. A client asking for anything else is silently given share data in a format it did not request, instead of a not-implemented/unsupported answer.
- **Fix:** Check the RAP function code in the request and return `STATUS_NOT_IMPLEMENTED` for anything other than NetShareEnum.
- **Benefit:** Unsupported requests fail honestly; the one supported function keeps working unchanged.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ — the request is never parsed in the handler)

### RE-SERV-047 — Live `/api/logs` stream forwards lines without the SSE sanitisation applied to every other line
- **Filed as:** GOCORE-02 (gocore.md)
- **Category:** 6
- **Locations:** `native/ps2servers-edge/internal/webui/api.go:62-70` (`sanitizeSSE` and its rationale); `native/ps2servers-edge/internal/webui/api.go:285-299` (sanitised paths); `native/ps2servers-edge/internal/webui/api.go:316` (unsanitised live path)
- **Problem:** `sanitizeSSE` exists because SSE framing is line-based: a raw `\n` in a line lets its producer inject extra events into the stream, and the comment names filenames and peer addresses as sources. The service-log snapshot (api.go:293) and the backlog replay (api.go:299) both pass through `sanitizeSSE`; the live subscription loop at api.go:316 writes the channel line verbatim (`fmt.Fprintf(w, "data: %s\n\n", line)`). Live lines come from the same `LogBuffer` fed by logger output carrying those same filenames/peers, so the one path that runs indefinitely is the one path unprotected.
- **Fix:** Pass the live line through `sanitizeSSE` at api.go:316, exactly as at lines 293 and 299. No new mechanism.
- **Benefit:** Closes an SSE event-injection hole in the authenticated management UI and makes all three emit sites in `HandleLogs` behave identically.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-048 — `raw_storage` runs bare `powershell.exe` from PATH/CWD while every other PowerShell call site in the repo resolves the absolute System32 path specifically to prevent binary hijacking
- **Filed as:** LNCHA-06 (lncha.md)
- **Category:** 6
- **Locations:** launcher/raw_storage.py:358-367 (`argv = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ...]`, `creationflags=0x08000000`); the hardened pattern at launcher/windows_setup.py:57-90 (`_powershell_executable` docstring: "Resolving the bare name ``powershell`` via PATH/CWD could run an attacker-placed ``powershell.*`` … (binary hijacking)"), and the same absolute-path resolution copied at smbv1_server/smbserver_opl.py:1553-1563 and smb2_server/smb2_server.py:1496-1502
- **Problem:** The repo has an explicit, documented security rule for invoking PowerShell, and the raw-device metadata path — which runs an elevated-adjacent disk-enumeration script (`Get-Disk`/`Get-Partition`/`Get-Volume`) whenever a user lists or opens a raw drive — is the one call site that does not follow it. A `powershell.exe` planted in the working directory or an early PATH entry would be executed instead of the system interpreter. Census (scripted grep for `WindowsPowerShell` / `powershell.exe` across launcher + standalone servers): 3 absolute-path resolutions, 1 bare-name invocation.
- **Fix:** Call `windows_setup._powershell` (or its executable resolver) from `raw_storage._metadata` instead of building a bare-name argv; the function already sets the hidden-window flags raw_storage hand-rolls as `0x08000000`.
- **Benefit:** One hijack-resistant invocation path for all PowerShell use; also removes raw_storage's hand-copied hidden-window flag.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-049 — Native RA dashboard builds achievement list with `innerHTML` from unescaped server-supplied titles/descriptions
- **Filed as:** LNCHB-02 (lnchb.md)
- **Category:** 6
- **Locations:** launcher/ra_web.py:385-390 (`container.innerHTML = achievements.map(a => \`…${a.title …}…${a.description …}…\`)`)
- **Problem:** Achievement titles/descriptions originate from the RetroAchievements patch response (remote server data, relayed through `RAState.set_game` in launcher/achievements.py) and are interpolated into an `innerHTML` template with no escaping. A title containing markup (e.g. an event-handler payload) executes in the dashboard page's origin — the same origin that can `POST /logout`, whose Host/Origin checks a same-origin script passes trivially. Every other text field in the same `render()` function correctly uses `textContent`; only the achievements list uses `innerHTML`.
- **Fix:** Build the rows with `textContent`/`createElement` like the rest of `render()`, or escape `&<>"'` in title/description before interpolation.
- **Benefit:** Removes script injection into a credential-adjacent local page; aligns the one unsafe sink with the file's own safe pattern.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-050 — Caduceus Web API key is sent as a GET query parameter on every API request
- **Filed as:** LNCHB-06 (lnchb.md)
- **Category:** 6
- **Locations:** launcher/caduceus.py:117-123 (`Account.request`: key read from the profile `apikey` file — location only — merged into `params` and placed in the request URL query string), launcher/caduceus.py:55-90 (`read_secret`, the loader for that file and for the profile `credentials` file)
- **Problem:** The RetroAchievements Web API key travels in the URL (`…API_<Endpoint>.php?…&y=<key>`) rather than a header/body. URLs are the most-logged artifact in any HTTP path (proxies, crash reports, history, `Referer` on redirects — mitigated here only by the `NoRedirect` opener). The owned-runtime stack was explicitly built to keep this class of secret out of URLs and argv (`ra_credentials.py:1-5`, `ra_resolver_process.py:5`); the Caduceus bridge predates/loosens that standard. Location of the stored key file is noted above; its value is never reproduced here.
- **Fix:** Where the RA API accepts it, move the key to a POST body; where it does not, at minimum document the exposure at the point the user is asked to paste the key, so the consent is informed.
- **Benefit:** Aligns the bridge with the repo's own credential-handling standard, or at least makes the exception explicit.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-051 — Caduceus library (`G`) progress replies always report 0 earned / 0 maximum
- **Filed as:** LNCHB-13 (lnchb.md)
- **Category:** 6
- **Locations:** launcher/caduceus.py:329-356 (`Bridge.progress`: `game_id, earned, maximum = 0, 0, 0`; the `kind == "G"` branch fills `total` and per-row `earned` counts but never recomputes the header `earned`/`maximum`, unlike the `A` branch at lines 348-351; the reply body is then formatted from those zeros)
- **Problem:** Every "My library" page sent to the console carries header progress of 0/0 regardless of the user's actual completion, while the per-row data in the same reply is correct — the console UI's aggregate progress for the library view is permanently wrong, not merely stale.
- **Fix:** In the `G` branch, derive the header fields from the fetched totals (sum of row `earned`/`MaxPossible`, or the API's aggregate fields) before formatting, the way the `A` branch does.
- **Benefit:** Library progress on the console matches the account it was fetched from.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-052 — WHICH-DEVICE.txt shipped in every Edge archive falsely claims the binary is read-only by default
- **Filed as:** CIDOCS-01 (cidocs.md)
- **Category:** 6
- **Locations:** `.github/workflows/edge-build.yml:387-390` (generated `WHICH-DEVICE.txt` text: "Access: read-only by default. Pass --read-only=false to allow the console to write (saves)"), contradicted by `native/ps2servers-edge/cmd/ps2servers-edge/main.go:150` (`--read-only` defaults `envBool("RO", false)`) and by the same file's own Quick Start at `edge-build.yml:391-397` (`udpfs --root …` with no `--read-only`)
- **Problem:** The binary is **writable by default** (proven by execution: `udpfs -h` lists `-read-only` with no default-true, help text "omit to allow the console to write saves"; same for `udpbd`/`smb`). Every Edge download's first-read file tells the user the opposite, and its own Quick Start command produces an unauthenticated, LAN-writable share while the text above it says read-only. `docs/EDGE.md`, `docs/EDITIONS.md:39` and `docs/INSTRUCTIONS.md:16-17` all state the true default, so the archive contradicts the docs packaged alongside it. No test guards this text (grep of `tests/` for `WHICH-DEVICE` returns nothing).
- **Fix:** Change the generated block to state writes are allowed by default and `--read-only` restricts them (matching EDGE.md's wording); delete the `--read-only=false` instruction.
- **Benefit:** Removes a security-relevant false assurance from the single most-read file in every Edge release.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED)

### RE-SERV-053 — The bcrypt CMAC self-test compares against the wrong constant: it is the CMAC *L* block, not a CMAC
- **Filed as:** PYSMB2-08 (pysmb2.md)
- **Category:** 6
- **Locations:** smb2_crypto.py:227-232
- **Problem:** `_bcrypt_cmac`'s sanity check expects `cmac(zero key, b"")` to equal `66e94bd4…2e`. Executed on the /tmp copy: that constant is `AES-128(zero key, zero block)` — the *L* subkey block from RFC 4493 §2.3 — while the actual CMAC of the empty message under the zero key is `4387c14b…f9` (pure-Python and OpenSSL backends agree). So the first clause of the check is false for every correct bcrypt backend, forever, and the backend is accepted only because the condition is an `and` whose second clause compares against the pure implementation. The test as written can never pass on its own terms; it survives by accident of its fallback, and a reader trusting the constant would "fix" a working backend into rejection.
- **Fix:** Replace the constant with the real zero-key empty-message CMAC (`4387c14b46ef7e176dceefa862d72ff9`), or drop the hardcoded clause and keep only the pure-implementation comparison the OpenSSL path already uses (`smb2_crypto.py` `_openssl_cmac`) — one self-test mechanism, not two divergent ones.
- **Benefit:** The Windows signing path's only startup correctness gate tests what it claims to test.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED for both values; the bcrypt branch itself is Windows-only and was READ, not run)

### RE-SERV-054 — `check_component("")` raises IndexError instead of PathError
- **Filed as:** PYSMB2-27 (pysmb2.md)
- **Category:** 6
- **Locations:** smb2_paths.py:70-102 (the crash is the `part[-1]` test at smb2_paths.py:97)
- **Problem:** The path guard's component check documents "Raises PathError" as its contract, but for an empty component it falls through every test and dies at `part[-1]` with `IndexError: string index out of range` — executed proof on the /tmp copy. Inside this scope the crash is unreachable (`Share.resolve` skips empty components at smb2_paths.py:148-150 before calling it), but the function exists precisely to be the reusable guard — its docstring says it is separated out so other servers (the NFS server's READDIR path) can call it per directory entry — and a guard that raises the wrong exception type defeats the caller's `except PathError` handling wherever it is reused without resolve's pre-filter.
- **Fix:** Reject an empty component at the top of `check_component` with the same PathError the other refusals use.
- **Benefit:** The guard's contract holds for every input, not just the inputs its current in-scope caller happens to pre-filter.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED)

### RE-SERV-055 — `CHECK_DIRECTORY` never succeeds for a spec-formed request because it does not strip the `0x04` BufferFormat byte its sibling handler strips
- **Filed as:** PYSMB1-04 (pysmb1core.md)
- **Category:** 6
- **Locations:** smbv1_server/smbserver_opl.py:1098-1110 (`h_check_directory`), smbv1_server/smbserver_opl.py:1070-1096 (`h_create_directory`, which documents and strips the same prefix)
- **Problem:** `h_create_directory`'s own comment states the command "carries BufferFormat=0x04 before the NUL-terminated share-relative directory name", and it strips that byte (`r.data[1:] if r.data[:1] == b"\x04"`); the project's write-semantics tests drive it with `b"\x04CFG\x00"`. `CHECK_DIRECTORY` has the same data format, but `h_check_directory` decodes the name starting at byte 0, so the resolved path begins with a literal `\x04` component and can never be a directory. Executed against an existing `DVD/` directory: a plain name returns `STATUS_SUCCESS`, the `0x04`-prefixed (spec-formed) name returns `STATUS_OBJECT_NAME_NOT_FOUND` (0xC0000034).
- **Fix:** Strip a leading `0x04` in `h_check_directory` exactly as `h_create_directory` does.
- **Benefit:** Directory-existence checks answer for the directory the client actually named, instead of always reporting "not found" to any client that follows the same format the create path already handles.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED — both request forms driven against the handler)

### RE-SERV-056 — NEGOTIATE returns success with DialectIndex 0 when the client never offered NT LM 0.12
- **Filed as:** PYSMB1-07 (pysmb1core.md)
- **Category:** 6
- **Locations:** smbv1_server/smbserver_opl.py:640-695 (`h_negotiate`: `except ValueError: idx = 0`, then an unconditional success response)
- **Problem:** If the dialect list does not contain "NT LM 0.12" — including when it is empty or contains only unrelated dialects — the handler does not fail negotiation; it claims dialect index 0 (whatever the client happened to list first) and returns the full NT LM 0.12 response with `STATUS_SUCCESS`. Executed with a dialect list of only "PC NETWORK PROGRAM 1.0": the reply is success with DialectIndex 0. A client that cannot actually speak NT LM 0.12 is told negotiation succeeded and proceeds into a session the server cannot honour, failing later and less diagnosably.
- **Fix:** Return an error status (or a dialect index of 0xFFFF, "no dialect chosen") when "NT LM 0.12" is absent, instead of defaulting `idx` to 0.
- **Benefit:** Incompatible clients fail at negotiation, where the failure belongs, instead of mid-session.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED)

### RE-SERV-057 — Core's `run()` drops the base server's per-datagram `OSError` containment, so one failed INFORM send can kill the whole server loop
- **Filed as:** PYSMB1-18 (pysmb1core.md)
- **Category:** 6
- **Locations:** udpfs_server/ps2servers_core.py:477-521 (`run()`: handler calls sit outside any `OSError` guard; the loop's only exception handler is `except KeyboardInterrupt`), udpfs_server/ps2servers_core.py:195-220 (`_send_specific` deliberately re-raises `OSError` from INFORM sends), udpfs_server/udpfs_server.py:1048-1112 (base `run()`, where dispatch happens inside the recv `try` and `OSError` is caught per datagram)
- **Problem:** In the base server's loop, `_handle_discovery` is called inside the same `try` that guards `recvfrom`, so a send failure during discovery handling is swallowed per-datagram and the server keeps serving. Core's override restructured the loop: `recvfrom` gets its own narrow `try`, header parsing another, and then `_handle_discovery` / `_route_data_via` are called unguarded inside a loop whose only handler is for `KeyboardInterrupt`. But Core also made `_send_specific` re-raise `OSError` on INFORM send failure, and `_handle_discovery` sends INFORMs (`_canonical_inform`) on the normal path. One transient send error on a discovery reply therefore propagates out of `run()` and terminates the server, where the implementation it overrides would have logged nothing, dropped one datagram, and continued.
- **Fix:** Wrap the dispatch calls in `run()` with the same per-datagram `except OSError: continue` containment the base loop has.
- **Benefit:** A single failed UDP send costs one datagram, as in the base server, instead of the whole UDPFS server.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ — both loops and the re-raise read in full; no execution claimed, since this sandbox blocks UDP `sendto` and any live failure here would be BLOCKED-ENV, not evidence)

### RE-SERV-058 — Environment integer/float parsing crashes at parser construction, while the neighbouring duration/size parsers were deliberately made never to raise
- **Filed as:** PYSMB1-19 (pysmb1core.md)
- **Category:** 6
- **Locations:** udpfs_server/ps2servers_core.py:529-543 (`--port`, `--sector-size`, `--compression-cache-size` defaults via `_env_int`), udpfs_server/ps2servers_core.py:566-567 (`--tx-delay-ms` default via `_env_float`), udpfs_server/udpfs_server.py:3297-3312 (`_env_int` / `_env_float` definitions: bare `int(v, 0)` / `float(v)`)
- **Problem:** Executed: with `PORT=oops` or `TX_DELAY_MS=oops` in the environment, merely calling `build_parser()` raises `ValueError` — the server dies with a traceback before argparse can produce a usage error. The same file's other environment parsers (`_env_duration`, `_env_max_transfer`, `_env_max_sessions`, imported and used lines away) were explicitly built and documented to never raise, because container/compose users set these variables and a traceback is the worst outcome — a standard the int/float path does not meet. `SECTOR_SIZE` and `COMPRESSION_CACHE_SIZE` behave identically.
- **Fix:** Give `_env_int`/`_env_float` the same warn-and-fall-back treatment at their definitions in `udpfs_server.py`, or validate the four affected values in `main()` via `parser.error` after parsing. Fixing the two helpers fixes both entry points at once.
- **Benefit:** A typo in an environment variable produces a warning or a clean usage error instead of a traceback, uniformly across the config surface.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED for `PORT` and `TX_DELAY_MS`; the other two variables share the identical helper, READ)

### RE-SERV-059 — `PROTOCOL_MODE` from the environment is neither stripped nor alias-mapped, slips past argparse's `choices`, and detonates later as an uncaught `ValueError`
- **Filed as:** PYSMB1-20 (pysmb1core.md)
- **Category:** 6
- **Locations:** udpfs_server/ps2servers_core.py:548-550 (`default=os.environ.get("PROTOCOL_MODE", "auto").lower()` on a `choices=` argument), udpfs_server/ps2servers_core.py:105-112 (`AutoUdpfsServer.__init__` raises `ValueError` for anything outside auto/standard/modulo), udpfs_server/ps2servers_core.py:591-690 (`main()`: the construction is not guarded, so the `ValueError` escapes as a traceback)
- **Problem:** The launcher's config layer accepts a wide vocabulary for this setting forever — labels ("Standard"), wire values, any casing, and the retired word "Proper" → standard (`launcher/servers.py` `protocol_mode_value` and `_PROTOCOL_MODE_BY_NAME`, read for this cross-check) — specifically so old saved settings keep working. Core's parser accepts only the three exact wire words, lowercased but not stripped. Executed: `PROTOCOL_MODE=proper` passes `build_parser().parse_args()` untouched (argparse does not validate a default against `choices`), and constructing the server exactly as `main()` does then raises `ValueError: protocol_mode must be auto, standard, or modulo`, uncaught. A trailing space (`PROTOCOL_MODE="modulo "`) fails the same way. The env var is the documented configuration route for container/systemd users, i.e. precisely the users with no launcher normalising values for them.
- **Fix:** Normalise the env default the same way `__init__` normalises its argument (strip + lower) and reject an unrecognised value with `parser.error` in `main()` before construction — or map it through the same alias table the launcher uses. No new vocabulary needs inventing; the launcher's table already exists.
- **Benefit:** A legacy or slightly-off `PROTOCOL_MODE` produces either the mode the user meant or one clean error line, not a traceback at server construction.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED — parse and construction both reproduced)

### RE-SERV-060 — ZSO decompression retries once per input byte: a garbage block costs one LZ4 attempt per byte of input
- **Filed as:** PYUDPFS-08 (pyudpfs.md)
- **Category:** 6
- **Locations:** `udpfs_server/compressed_iso/zso.py:165-171` (retry loop slicing `compressed_data[:-1]`), contrast `udpfs_server/compressed_iso/limits.py:78-90` (`indexed_block` caps the same retry at the alignment padding, with a comment naming this exact hazard)
- **Problem:** the serving ZSO path handles trailing alignment padding by retrying `lz4.block.decompress` after removing one byte at a time, with no bound. EXECUTED in-process: a single 20,000-byte garbage block produced **19,990 LZ4 decompress attempts** in 0.48 s before failing. Cost grows ~quadratically with block size (attempts × shrinking input), and `block_size` itself is header-controlled (PYUDPFS-07), so one READ against a crafted .zso can pin a session worker — and with a large claimed block, the CPU — for minutes. The in-tree inspection path already fixed this exact problem ("Never retry once per byte of an attacker-controlled, potentially megabyte-sized span"); the fix was never carried to the path that serves the wire.
- **Fix:** bound the retry loop exactly as `indexed_block` does: at most `(1 << align) - 1` padding bytes (63 maximum in practice), then raise `ValueError`.
- **Benefit:** worst case per bad block drops from O(n²) decompression attempts to at most 64.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED)

### RE-SERV-061 — Compressed block offsets are never validated in the serving path, and short/corrupt blocks are silently fabricated into data
- **Filed as:** PYUDPFS-09 (pyudpfs.md)
- **Category:** 6
- **Locations:** `udpfs_server/compressed_iso/cso.py:115-150` (`read_size = next_offset - offset` at line 124, unchecked; short deflate output zero-padded at lines 148-150), `udpfs_server/compressed_iso/zso.py:125-163` (same next-offset arithmetic; the uncompressed branch returns the raw read as-is however short), `udpfs_server/compressed_iso/base.py:53-97` (`read()` advances `self.pos += read_size` at line 95 even when the block slice at line 94 delivered fewer bytes)
- **Problem:** three integrity holes share one mechanism — nothing checks a block's on-disk span or decoded length outside strict mode. (1) Non-monotonic CSO/ZSO offset tables make `read_size`/`compressed_size` negative, and `file.read(negative)` reads to end-of-file, pulling the whole remainder of the image into memory for one block. (2) A compressed CSO block that inflates to less than `block_size` is zero-padded and served as if valid (padding is correct only for the final block), so corruption mid-image reads back as zeros instead of an error. (3) A short raw (uncompressed-flag) ZSO block returns short, and `base.read()` still advances the position by the full `read_size`, so the reply is short *and* the stream skips the missing bytes. The strict path (`limits.indexed_block`) validates every one of these — span inside the file, decoded length within `[expected, block_size]` — which is an admission of the invariant the serving path skips.
- **Fix:** in both wrappers' `_decompress_block`, refuse `read_size < 0` or a span past end-of-file, and raise `ValueError` when a non-final block decodes short instead of padding it; in `base.read()`, advance by the number of bytes actually appended.
- **Benefit:** corrupt images fail the read (EIO to the console, visible in the log) instead of silently serving zeros and gaps into a running game.
- **Effort:** M · **Risk:** Low · **Confidence:** Med (READ)

### RE-SERV-062 — Router-status replies never set FLAG_UDPBD or FLAG_DECOMPRESSES, even when the server serves a block device / decompresses
- **Filed as:** PYUDPFS-13 (pyudpfs.md)
- **Category:** 6
- **Locations:** `udpfs_server/udpfs_server.py:1563-1567` (`_status_flags`), `udpfs_server/router_status.py:66-70` (flag definitions)
- **Problem:** `_status_flags()` starts from `FLAG_UDPFS` and adds only `FLAG_READ_ONLY`. The protocol defines `FLAG_UDPBD` and `FLAG_DECOMPRESSES` and the server knows both facts (`self.bd_fh is not None`, `self.enable_compression` plus a non-empty `COMPRESSED_EXTENSIONS`), but never reports them, so a launcher/dashboard polling status cannot distinguish a file-only server from a block-device one — the distinction the flags exist to carry. `STATE_BUSY`/`STATE_DEGRADED` in the same module *are* computed from real state, so this reads as an unfinished corner rather than a protocol decision.
- **Fix:** set `FLAG_UDPBD` when `self.bd_fh is not None` and `FLAG_DECOMPRESSES` when compression is enabled and at least one format is available.
- **Benefit:** status consumers get the capability bits the protocol already reserves; no wire change.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-063 — LSEEK whence handling disagrees by handle type: the compressed wrapper silently accepts any whence
- **Filed as:** PYUDPFS-15 (pyudpfs.md)
- **Category:** 6
- **Locations:** `udpfs_server/compressed_iso/base.py:99-127` (`CompressedFileWrapper.seek` has branches for whence 0/1/2 and no error case), contrasted with `udpfs_server/udpfs_server.py:2189-2230` (`_handle_lseek` maps `ValueError` from plain files to EINVAL) and `udpfs_server/compressed_iso/chd_cuebin.py:300-311, 371-385` (`MemoryFile.seek`/`ChdBinFile.seek` do raise on bad whence)
- **Problem:** the same LSEEK_REQ with `whence=3` (or 99) is EINVAL against a plain file, EINVAL against a virtual CUE/BIN view, but a silent success returning the clamped current position against a CSO/ZSO/CHD wrapper — the handler's `ValueError` arm can never fire for the wrapper because the wrapper never raises. A client probing SEEK_DATA/SEEK_HOLE-style behaviour, or simply sending a corrupt whence, gets three different protocols depending on which file it opened. The wrapper also clamps seeks past EOF to `uncompressed_size` where plain files report the true overshot position, a second, quieter divergence in the same method.
- **Fix:** raise `ValueError` in `CompressedFileWrapper.seek` for whence outside (0, 1, 2), letting the existing handler mapping answer EINVAL uniformly.
- **Benefit:** one LSEEK contract for every handle type; corrupt requests are visible as errors instead of plausible-looking positions.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-064 — UDPBD `BlockDevice` constructor validates the target type but not its size: >2 TiB images break INFO packing, non-512-multiple sizes silently lose their tail
- **Filed as:** PYSMALL-03 (pysmall.md)
- **Category:** 6
- **Locations:** `udpbd_server/udpbd_server.py:82-116` (`__init__` + `sector_count`), `udpbd_server/udpbd_server.py:201-203` (INFO reply `struct.pack("<II", SECTOR_SIZE, self.bd.sector_count())`)
- **Problem:** `sector_count()` is `size // SECTOR_SIZE` with no upper bound and no alignment check. The INFO reply packs it as a uint32, so an image larger than 2 TiB − 512 B raises `struct.error` inside `_handle_info`; the run loop catches `struct.error` as a "bad packet" (verbose-only log) and sends no reply — the console simply never discovers the server, with nothing logged at default verbosity. The raw backend rejects exactly this case at construction ("fit UDPBD's 32-bit sector count (less than 2 TiB)", `launcher/raw_storage.py:30-33`, read for contrast); the file backend does not. Separately, a file whose size is not a multiple of 512 has its trailing bytes silently unservable (`size // 512` truncates) with no warning. Note PYSMALL-01 makes the >2 TiB case reachable at runtime too, by growing an image through out-of-range writes.
- **Fix:** In `BlockDevice.__init__`, after measuring `self.size`, raise `ValueError` if `sector_count()` exceeds `0xFFFFFFFF` and warn (or reject) when `size % SECTOR_SIZE != 0` — mirroring the validation the raw backend already performs.
- **Benefit:** An unservable image fails loudly at startup with the real reason instead of silently never answering discovery.
- **Effort:** S · **Risk:** Low · **Confidence:** Med (INFERRED — the pack overflow follows from the cited lines; the constructor gap is READ)

### RE-SERV-065 — HTTP flat index: path lookup serves the duplicate copy that the scan explicitly suppressed and that `games.csv` does not list
- **Filed as:** PYSMALL-07 (pysmall.md)
- **Category:** 6
- **Locations:** `http_server/http_server.py:543-580` (`_rebuild_flat`: `path_entries[rel_path_key] = entry` at line 564 is assigned *before* the duplicate-name `continue` at lines 567-575), `http_server/http_server.py:763-784` (`lookup`: consults `_path_entries` first, ambiguity guard applies only to the basename fallback), contrast `http_server/http_server.py:607-670` (`_rebuild_path_aware` serves only rows the catalogue retained)
- **Problem:** When the same advertised name exists in the root and in `DVD/`, the scan logs "serving X, ignoring Y", the CSV lists only X, and a bare-name lookup correctly returns `None` (ambiguous) — but a path-qualified lookup (`DVD/<name>`) returns Y, the copy that was supposedly ignored and that no catalogue row advertises. EXECUTED: root copy 1,024 B listed in the CSV; `lookup("DVD/SLUS_333.33.Same.iso")` returned the suppressed 4,096 B DVD copy. Two different clients can therefore boot two different discs for the same catalogue entry depending on how the request path is formed, contradicting the log line and the flat profile's own duplicate policy (the path-aware profile documents the opposite rule: "Only what the list advertises is served").
- **Fix:** In `_rebuild_flat`, only record `path_entries` for entries that survive duplicate suppression (move the assignment below the `seen_names` check), so suppressed copies are unreachable by path as well as by name.
- **Benefit:** One duplicate policy, consistently enforced: what the log says is ignored is actually ignored.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED)

### RE-SERV-066 — HTTP dispatcher sends a 500 response after a 206 header (and possibly partial body) is already on the wire
- **Filed as:** PYSMALL-08 (pysmall.md)
- **Category:** 6
- **Locations:** `http_server/http_server.py:898-931` (`_dispatch`: generic `except OSError` at line 928 calls `_send_short(500, ...)`), `http_server/http_server.py:1006-1034` (`_serve_game`: sends the 206 status and headers before the body), `http_server/http_server.py:1085-1090` (`_send_bytes` raises `OSError("short read …")` after setting `close_connection`)
- **Problem:** `_send_bytes`' short-read path is carefully designed around the file's own contract — drop the connection, because a short body desynchronises the driver's fixed-count reads. But the raised `OSError` propagates into `_dispatch`, whose handler unconditionally writes a complete second response (`_send_short(500)`) onto the same socket. Those 500-header bytes land exactly where the driver is counting game bytes: the "recoverable reconnect" the `_send_bytes` comment promises becomes silent data corruption of the current read, the precise failure mode the file header says this server exists to avoid. The same applies to any `OSError` raised by `_serve_game` after `end_headers()`.
- **Fix:** Track whether response headers have been sent (e.g. a flag set in `_serve_game`/`_serve_games_csv` after `end_headers()`); in `_dispatch`'s `OSError` branch, if they have, just set `close_connection = True` and return without `_send_short`.
- **Benefit:** A mid-stream file error degrades to a reconnect-and-retry, as designed, instead of injecting a second response into the game-data stream.
- **Effort:** S · **Risk:** Low · **Confidence:** Med (INFERRED — control flow is READ from the cited lines; the handler was not executed end-to-end over a socket)

### RE-SERV-067 — HTTP per-connection cached handle is keyed by path only, so an index refresh / file replacement is served from the stale handle with the new entry's size
- **Filed as:** PYSMALL-09 (pysmall.md)
- **Category:** 6
- **Locations:** `http_server/http_server.py:867-881` (`_handle_for`: cache hit iff `_cached_path == entry.path`), `http_server/http_server.py:530-541` (`refresh` rebuilds entries under the lock, with no connection-cache invalidation), `http_server/http_server.py:1043-1057` (`_send_bytes` trusts `entry.size`-derived length against whatever handle the cache returned)
- **Problem:** A long-lived keep-alive connection (the normal case — the driver issues thousands of reads down one socket) caches the open handle for a path. If the underlying file is replaced or the index is rebuilt with a different entry for the same path (e.g. a `.chd` swapped for an `.iso` of the same advertised name, or the ISO re-dumped — the exact scenario `_signature_of`'s comment says people do), the next request pairs the *new* entry's `size`/`compressed` flag with the *old* open handle. For the raw path, `sendfile` against the old descriptor with the new length either short-sends (recovered by the manual loop only until the old file's EOF, then the PYSMALL-08 path) or, if the old file was larger, streams bytes from a disc image the catalogue no longer advertises.
- **Fix:** Key the cache on the entry object identity (or `(path, size, compressed)`) instead of the path alone, so a rebuilt entry forces a reopen; the reopen cost is paid once per refresh, not per request.
- **Benefit:** A replaced game can never be half-served from its predecessor's descriptor on an existing connection.
- **Effort:** S · **Risk:** Low · **Confidence:** Med (INFERRED)

### RE-SERV-068 — LZ4 block decoder's length accumulator is unbounded, defeating its own output checks on 32-bit targets
- **Filed as:** GOPROTO-14 (goproto.md)
- **Category:** 6
- **Locations:** native/ps2servers-edge/internal/compression/lz4.go:13-29 (`readLen`); native/ps2servers-edge/internal/compression/lz4.go:37-40 (the checks it feeds); native/ps2servers-edge/internal/compression/image.go:420-460 (`decodeBlock`, the caller that bounds raw size to `blockSize*2 + 4096` with `blockSize` up to 16 MiB)
- **Problem:** `readLen` accumulates `n += v` for each 0xFF extension byte with no cap against `dstSize` or the remaining input. The decoder's safety argument is the post-hoc checks (`lit > dstSize-len(dst)` etc.), which assume `lit` is a sane positive number. On a 32-bit `int` — the MIPS/ARM routers this codebase repeatedly designs for — a crafted ZSO block consisting of a long run of 0xFF length bytes (the raw block may be up to ~32 MiB under `decodeBlock`'s bound, so ~8.6 billion can be accumulated) wraps `n` negative; a negative `lit` passes both checks at lz4.go:37 and reaches `src[i:i+lit]` at line 40 as a slice-bounds panic in the serving path. On 64-bit the same input is merely rejected, which is why the defect is invisible on a development machine.
- **Fix:** Bound the accumulation inside `readLen`: fail as soon as `n` exceeds the maximum legitimate length (`dstSize`, or the remaining input size), before it can overflow.
- **Benefit:** A malformed compressed image returns an error instead of panicking the server on 32-bit builds; the decoder's documented "malformed input cannot grow memory" guarantee extends to "cannot crash the process".
- **Effort:** S · **Risk:** Low · **Confidence:** Med (INFERRED — overflow depends on 32-bit `int`; the unbounded accumulation itself is READ)

### RE-SERV-069 — Text-mode log lines with fields are fragmented into multiple LogBuffer lines (and subscribers get the fragments)
- **Filed as:** GOCORE-01 (gocore.md)
- **Category:** 6
- **Locations:** `native/ps2servers-edge/internal/logging/logging.go:45-49`; `native/ps2servers-edge/internal/webui/logbuffer.go:28-57`; `native/ps2servers-edge/cmd/ps2servers-edge/main.go:518`
- **Problem:** In text mode, `Logger.emit` writes one log line as several separate `Write` calls: the header `[%s] %s` with no newline, one ` %s=%v` per field, then a bare `Fprintln` newline. `LogBuffer.Write` treats each `Write` chunk as complete lines (`bytes.Split` on `\n`, storing every fragment). The web UI tees the logger into the buffer via `io.MultiWriter(os.Stdout, logBuffer)` (main.go:518), so every fielded text-mode line lands in the ring buffer — and is pushed to `/api/logs` subscribers — as one fragment per field plus an empty line that also consumes a ring slot. Executed proof (probe test in a `/tmp` copy): `Info("server started", {"addr": …, "shares": 2})` followed by one fieldless `Info` produced 5 stored lines: `"[info] server started"`, `" addr=127.0.0.1:8082"`, `" shares=2"`, `""`, `"[info] plain line"` — 2 log calls, 5 buffer lines. JSON mode is unaffected (single `Fprintln` at logging.go:36).
- **Fix:** Build the complete text line in memory inside `emit` and emit it with a single `Write`/`Fprintln`, as the JSON branch already effectively does. Do not add partial-line buffering to `LogBuffer` as well — one fix, at the producer.
- **Benefit:** The dashboard log view and the ring buffer contain the same lines the console shows; ring capacity (default 1,000) stops being burned ~4× faster by fielded lines and empty strings.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED)

### RE-SERV-070 — `ValidateConfig` accepts values the binary then refuses at startup (sector size, compression cache, peer timeout)
- **Filed as:** GOCORE-03 (gocore.md)
- **Category:** 6
- **Locations:** `native/ps2servers-edge/internal/webui/config.go:195-263` (esp. 254-262); `native/ps2servers-edge/cmd/ps2servers-edge/main.go:176-191` and `native/ps2servers-edge/cmd/ps2servers-edge/main.go:218-222`; `native/ps2servers-edge/internal/webui/schema.go:114`, `native/ps2servers-edge/internal/webui/schema.go:120`, `native/ps2servers-edge/internal/webui/schema.go:122`
- **Problem:** The API validator and the binary disagree, which is the exact failure `ValidateConfig`'s own doc says it exists to prevent ("a value that produces a server which refuses to start with no visible reason"). Three concrete divergences, all read from the code: (1) `sector_size` — API accepts any 1–65536 (config.go:254-255); the binary requires a multiple of 512, at least 512 in effect, at most 65536 (main.go:176-182), and the schema declares `Min 512, MultipleOf 512` (schema.go:120). So 513 or 1000 saves cleanly via the dashboard and udpfs then exits with an error. (2) `compression_cache_size` — API checks only `< 0` (config.go:257-259); the binary rejects `> compression.MaxCacheBlocks` (4096, main.go:188-191) and the schema declares `Max 4096` (schema.go:122), so a large saved value passes the API and fails (or, per main.go's own comment, was bounded precisely to avoid a huge allocation). (3) `peer_timeout` — `ValidateConfig` never parses it at all; the binary requires a parseable duration between 1m and 24h (main.go:218-222) and the schema declares that range (schema.go:114). A dashboard-saved `"peer_timeout": "soon"` is accepted and the service then refuses to start.
- **Fix:** Make `ValidateConfig` enforce the same three rules the binary and the schema already state (multiple-of-512 sector size in 512–65536; cache size 0–4096; peer_timeout parseable and within 1m–24h, reusing the duration parsing the binary uses rather than a second implementation). Do not loosen the binary or the schema to match the API.
- **Benefit:** A config the dashboard reports as saved is a config the services will actually start with; three documented contracts (API, binary, `--dump-schema`) stop contradicting each other.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-071 — `genericManager.Restart` dereferences a possibly-nil logger and reports success for a restart that did nothing
- **Filed as:** GOCORE-07 (gocore.md)
- **Category:** 6
- **Locations:** `native/ps2servers-edge/internal/webui/service.go:221-226`; contrast `native/ps2servers-edge/internal/webui/service.go:163-165` (openwrtManager's nil check); `native/ps2servers-edge/internal/webui/service.go:128-137` (`DetectServiceManager` passes the logger through unchecked); `native/ps2servers-edge/internal/webui/webui.go:198-206` (Serve passes `s.cfg.Log`, which `New` never requires to be non-nil)
- **Problem:** Two defects in four lines. (1) `genericManager.Restart` calls `m.log.Info(...)` unconditionally; the OpenWrt manager guards the identical call with `if m.log != nil`. A `webui.Server` built without a `Log` (nothing in `New` requires one) on a host with neither procd nor systemctl panics on the first restart request instead of answering it. (2) Even when it doesn't panic, it returns `(nil, nil)`: `HandleRestart` then reports `{"ok": true, "restarted": null}` for a restart that by definition did not happen, on exactly the hosts (generic/manual installs) where the operator most needs to be told to restart by hand.
- **Fix:** Guard the log call the same way openwrtManager does, and return an error (or an explicit empty restarted set with the manual-restart message surfaced) instead of a nil error. Keep the change inside this one method.
- **Benefit:** No panic path in a management endpoint, and the dashboard stops claiming a successful restart on generic hosts.
- **Effort:** S · **Risk:** Low · **Confidence:** Med (INFERRED — the nil-logger path is latent: `main.go` always supplies a logger, so it is reachable via library-style construction of `webui.Server`, not via the shipped binary's webui subcommand)

### RE-SERV-072 — `IsStatusQuery`'s documentation claims exact-length strictness the code does not implement
- **Filed as:** GOCORE-10 (gocore.md)
- **Category:** 6
- **Locations:** `native/ps2servers-edge/internal/protocol/status.go:48-49` (`StatusQueryLen` — "the exact length of a query"); `native/ps2servers-edge/internal/protocol/status.go:107-126` (`IsStatusQuery` doc and body)
- **Problem:** The doc says the check is "deliberately strict about length: a six-byte packet whose service ID is 0xF5F7 is the only thing that qualifies." Both halves are wrong against the code below it: a query is ten bytes, not six (2-byte header + 4-byte discovery header + 4-byte magic, per `MarshalStatusQuery` in the same file), and the length test is `len(packet) < StatusQueryLen` — any packet of 10 or more bytes with the right header, service ID and magic at bytes 6–10 qualifies, trailing garbage included. Either the strictness claim is stale documentation on a security-relevant guard (this check is what stops the server answering foreign discovery traffic, per the file's own header), or the intended `!=` check was never written. As shipped, a reader auditing the guard is told the opposite of what it does.
- **Fix:** Decide once: if trailing bytes are intentionally tolerated (the header comment argues longer packets are safely dropped elsewhere), rewrite the doc to say so and drop "exact"/"strict"/"six-byte"; if they are not, change the test to `len(packet) != StatusQueryLen`. Do not leave doc and code disagreeing on a packet guard.
- **Benefit:** The status-query guard's real acceptance rule becomes auditable; no future change "fixes" the code to match a comment that was simply wrong, or vice versa.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-073 — SMBv2/SMBv3 argv builder crashes on `root_dir`-only values and silently emits an empty share, where SMBv1 accepts and validates
- **Filed as:** LNCHA-01 (lncha.md)
- **Category:** 6
- **Locations:** launcher/servers.py:127-137 (`_smb_argv`: folder is `v.get("games_folder") or v.get("root_dir")`, empty folder raises `ValueError("SMB server requires a Games folder to share.")`); launcher/servers.py:176-179 (`_smb2_argv`: `"{}={}".format(share, v["games_folder"])` — direct indexing, no fallback, no emptiness check)
- **Problem:** The two SMB argv builders disagree on the same saved-config vocabulary. Executed: `SMBV1.build_argv({"root_dir": "/games"})` → `['--share', 'games=/games', '--smb-version', '1']`, but `SMBV2.build_argv({"root_dir": "/games"})` raises `KeyError: 'games_folder'` (an unhandled crash, not the friendly ValueError the GUI surfaces for SMBv1), and `SMBV2.build_argv({"games_folder": ""})` silently returns `['--share', 'games=', '--smb-version', '2']` — an empty share root handed to the server — where SMBv1 raises the clear ValueError. Any config source that supplies the folder under `root_dir` (the key UDPFS/HTTP/NFS all use), or an empty string, behaves differently per SMB dialect for no protocol reason.
- **Fix:** Make `_smb2_argv` resolve the folder exactly as `_smb_argv` does (`games_folder` or `root_dir`, stripped) and raise the same ValueError when empty.
- **Benefit:** One folder vocabulary across all folder servers; a bad config produces the designed error message instead of a KeyError traceback or a server started on an empty share.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED)

### RE-SERV-074 — Windows/Linux firewall port list still keys UDPFS single-port mode off the retired `modulo_mode` setting, so a migrated config silently gains a dead data-port rule
- **Filed as:** LNCHA-02 (lncha.md)
- **Category:** 6
- **Locations:** launcher/windows_setup.py:146-157 (data-port rule skipped only `if not values.get("modulo_mode")`); launcher/servers.py:271-299 (`migrate_saved` deletes `modulo_mode`, replacing it with `enforce_modulo=True` + `protocol_mode`); launcher/servers.py:344-350 (`_udpfs_argv` treats `enforce_modulo` or `protocol_mode == "modulo"` as single-port Modulo)
- **Problem:** This is a config round-trip that silently changes behaviour on save. `migrate_saved` exists precisely because a key with no widget is dropped on the next save — so after migration, no value dict the current GUI produces can ever contain `modulo_mode` again. Yet `windows_setup._server_ports` suppresses the UDPFS data-port rule only for that retired key. Executed: `server_ports("udpfs", {"enforce_modulo": True, "protocol_mode": "Legacy / Modulo only", "data_port": 40000})` and `server_ports("udpfs", {"protocol_mode": "modulo", "data_port": 40000})` both return the data port 40000 as a second rule; only `{"modulo_mode": True, ...}` suppresses it. In Modulo (single-port) mode the server ignores the data port (per the comment at windows_setup.py:148-153 itself), so a user who pinned a data port and later ticks Enforce Modulo — or whose legacy config was migrated — gets a Windows allow rule and a Linux hint line (via posix_firewall, which reuses this list) for a port nothing ever listens on.
- **Fix:** Test the current vocabulary in `_server_ports`: suppress the data-port rule when `values.get("enforce_modulo")` or the protocol mode resolves to modulo (reuse `servers.protocol_mode_value`), keeping the legacy `modulo_mode` check for un-migrated dicts.
- **Benefit:** The firewall surface matches what the server actually binds; the migration stops changing firewall behaviour as a side effect.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED)

### RE-SERV-075 — NFS and RetroAchievements never get the cooperative stop pipe, so on Windows Stop kills them outright — skipping NFS cleanup and orphaning the RetroAchievements engine child
- **Filed as:** LNCHA-03 (lncha.md)
- **Category:** 6
- **Locations:** launcher/servers.py:72-77 (the `cooperative_stop` contract: child "unwinds on the launcher's stop pipe … which on Windows is the only way its shutdown code … gets to run"); set at launcher/servers.py:387, 436, 452, 468, 568, 659 (SMBv1, SMBv2, SMBv3, UDPFS, HTTP, UDPBD) but absent from launcher/servers.py:624-652 (NFS) and launcher/servers.py:712-746 (RetroAchievements); launcher/process.py:114-178 (`request_stop` returns False for non-cooperative children, `stop()` falls through to `terminate()`); nfs4_server/nfs4_server.py:1094-1110 (cleanup exists: `finally: server.stop()`, plus its own SIGTERM→KeyboardInterrupt hook); launcher/achievements.py:362-410 (SIGINT/SIGTERM `stopped` event, engine subprocess supervised in a loop, `finally` closes viewer/bridge/lease and unlinks the account-port file)
- **Problem:** Both excluded servers have shutdown code that only runs on a graceful unwind, exactly like the six included ones. NFS's `main()` stops the server in a `finally`; the achievements process terminates its bundled engine subprocess, closes the Caduceus bridge/viewer, closes its service lease and removes its account-port file in a `finally`. With `cooperative_stop=False`, the launcher starts them with stdin=DEVNULL and no stop-pipe env, so on Windows Stop is `TerminateProcess`: NFS cleanup never runs, and the achievements engine child — a separate process the launcher does not track — is left running holding UDP 18194, so the next RetroAchievements start collides with the orphan. On POSIX the same Stop happens to work because `terminate()` delivers SIGTERM and serve.py's handler converts it — so stop behaviour for these two servers silently depends on the OS.
- **Fix:** Verify each of the two servers wakes on stdin EOF while idle (the check the flag's own comment requires), then set `cooperative_stop=True` on both ServerDefs; if either fails the wake-up check, fix that server's wait loop rather than leaving the flag off.
- **Benefit:** Stop means the same thing on every OS; no orphaned engine process holding the RetroAchievements port after the launcher says "stopped".
- **Effort:** M · **Risk:** Med · **Confidence:** High (READ for the flag/process mechanism; INFERRED for the Windows orphan consequence, from the documented TerminateProcess semantics in launcher/process.py:14-18 and launcher/serve.py:84-93)

### RE-SERV-076 — Choice-field values have no single stored vocabulary: the RetroAchievements mode default is the wire value while the other two choice fields default to display labels, and three separate tolerant readers compensate
- **Filed as:** LNCHA-04 (lncha.md)
- **Category:** 6
- **Locations:** launcher/servers.py:720 (RA `mode` Field `default="xerabora"` — the wire value; its choices are `("xeRAbora", "xerabora"), ("Caduceus", "caduceus")` at launcher/servers.py:686); contrast launcher/servers.py:485 (UDPFS `protocol_mode` default `"Auto (recommended)"` — a label) and launcher/servers.py:581-582 (HTTP `catalog_profile` default `"Legacy flat (OPL-HTTP)"` — a label); tolerant readers at launcher/servers.py:689-695 (`_achievements_argv` label-or-wire map), launcher/servers.py:302-312 (`protocol_mode_value`), launcher/servers.py:526-531 (`http_profile_value`), launcher/windows_setup.py:134-139 (mode tested against both `"caduceus"` and `"Caduceus"`); launcher/gui.py:881-885 converts wire→label on restore for UDPFS `protocol_mode` only — no equivalent conversion exists for `mode` or `catalog_profile`; launcher/servers.py:293-298 (`migrate_saved` writes a third spelling, `"Modulo"`, which is neither the label `"Legacy / Modulo only"` nor the wire value `"modulo"`)
- **Problem:** Executed census of the three choice fields: `protocol_mode` default is a label, `catalog_profile` default is a label, `mode` default is a wire value that does not appear in its own combobox's label list. The GUI choice widget is filled with labels, so an untouched RetroAchievements card persists `"xerabora"` while a touched one persists `"xeRAbora"` — the same setting round-trips through config in two spellings depending on whether the user opened the dropdown. Every downstream reader has grown its own case/spelling tolerance to cope (three converter functions in servers.py, a dual-spelling tuple in windows_setup.py, a UDPFS-only restore conversion in gui.py), and each new reader must remember to be tolerant or it silently misreads the setting (as LNCHA-02 shows one did).
- **Fix:** Pick the label as the one stored vocabulary for all choice fields (it is what the widget holds): change the RA `mode` Field default to `"xeRAbora"`, and let `migrate_saved`-style normalisation — not per-reader tolerance — handle legacy wire spellings at load. The existing converters can then shrink to the load path.
- **Benefit:** A saved setting means the same thing to every reader; the class of bug in LNCHA-02 (a reader keyed to the wrong spelling) stops recurring.
- **Effort:** M · **Risk:** Med · **Confidence:** High (EXECUTED — field-default census and per-spelling argv/firewall outputs)

### RE-SERV-077 — The SMB port default lives in three places and one of them is wrong: the firewall layer defaults SMBv2/SMBv3 to 1025, the servers default to 1445
- **Filed as:** LNCHA-05 (lncha.md)
- **Category:** 6
- **Locations:** launcher/windows_setup.py:142-144 (`port = 445 if take_445 else _parse_port(values.get("port"), 1025)` — one default for all three SMB servers); launcher/servers.py:211-218 (`_SMB2_FIELDS` port Field `default=1445`, used by both SMBV2 and SMBV3); launcher/servers.py:386-441 (SMBV1 `default_port=1025` vs SMBV2 `default_port=1445`)
- **Problem:** `windows_setup.server_ports` is documented as the "one source so the two never disagree" (launcher/windows_setup.py:124-129), but executed: `server_ports("smbv2", {})` and `server_ports("smbv3", {})` both return `[("TCP", 1025, ...)]` while the servers themselves listen on 1445 when no port is supplied. The GUI normally always supplies a port, which is why this stays latent — but any caller passing a sparse/hand-edited/older saved dict gets firewall rules (and Linux hint lines) for a port the server is not on, and the setup fingerprint is computed from the same wrong list. The default also exists a third time as literals in the GUI's hint/status helpers, so there are three places to keep in step.
- **Fix:** Take the fallback default from the registry (`REGISTRY[key].default_port`) in `_server_ports` instead of the hard-coded 1025.
- **Benefit:** Firewall rules and hints follow the server definition by construction; one less hand-synced constant.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED)

### RE-SERV-078 — `netinfo.best_lan_ip()` returns the default-route address unconditionally, bypassing the private-LAN ranking the module exists to apply
- **Filed as:** LNCHA-07 (lncha.md)
- **Category:** 6
- **Locations:** launcher/netinfo.py:1-8 (module contract: "We deliberately prefer real private-LAN ranges (192.168/x, 10/x, 172.16-31/x) over VPN / WSL / link-local addresses"); launcher/netinfo.py:84-93 (`primary_ip`: UDP-connect to 8.8.8.8, i.e. whatever the default route is); launcher/netinfo.py:122-131 (`_lan_rank`); launcher/netinfo.py:224-234 (`best_lan_ip`: returns `primary_ip()` whenever it is not loopback; the ranked candidate list is consulted only when the primary is 127.x); launcher/netinfo.py:107-120 (`all_ipv4` also adds `primary_ip()` unconditionally)
- **Problem:** The ranking that implements the module's stated purpose is effectively dead in the common case: it runs only when the machine has no default route at all. With a full-tunnel VPN up — the exact case the header warns about ("NOT a VPN/WSL address", quoting the SMBv1 server's own warning) — the default route *is* the VPN, so `primary_ip()` returns the tunnel address and `best_lan_ip()` returns it without ever consulting `_lan_rank` or the interface list. That address is what the GUI pre-fills as the IP to type into OPL (gui.py uses `best_lan_ip()` for the LAN-IP field), so the user is handed the one address the PS2 cannot reach.
- **Fix:** In `best_lan_ip`, prefer the best-ranked private candidate from `all_ipv4()` and fall back to `primary_ip()` only when no private candidate exists (or when the primary is itself the top-ranked private address).
- **Benefit:** The pre-filled LAN IP matches the module's documented preference; the VPN case stops producing an unreachable suggestion.
- **Effort:** S · **Risk:** Low · **Confidence:** Med (INFERRED — the logic path is read directly; no VPN was available in the sandbox to execute against)

### RE-SERV-079 — Owned-runtime `/state` schema diverges from the managed-engine schema its own consumers parse, and two counters are fabricated
- **Filed as:** LNCHB-01 (lnchb.md)
- **Category:** 6
- **Locations:** launcher/ra_web.py:82-104 (`note_unlock` emits unlock entries with an `at` timestamp and no `ago`), launcher/ra_web.py:106-140 (`to_dict`: `"webapi"` is just `logged_in` copied, `gaps`/`dupes`/`torn` hardcoded to 0 at lines 122-124), launcher/ra_notifications.py:14-28 (`_event` requires an integer `ago` field — owned unlock entries are therefore all rejected, so `UnlockTracker` can never fire on owned-runtime data), launcher/ra_overview.py:58-76 (`view_model` derives status from `value.get("state") == 2` plus `measured`/`percent` — owned achievements built in launcher/achievements.py carry an `unlocked` boolean and none of those fields, so every owned achievement renders "Not unlocked" with no progress), launcher/achievements.py:274-285 (main loop increments `state.packets`/`state.frames` by exactly 1 per evaluation taken, regardless of real console counters)
- **Problem:** One producer, two incompatible `/state` dialects. The desktop overview, session tracker and unlock notifier were written against the bundled engine's dialect; the owned runtime's `RAState` speaks a different one (field names `unlocked` vs `state`, `at` vs `ago`) and fills the gaps with invented values (zero gap/dupe/torn counts, synthetic packet/frame increments, `webapi` equated with logged-in even though the owned client never uses a Web API key). A user on the owned preview sees unlocks that never notify, achievements that never show as unlocked in the native overview, and telemetry numbers that look measured but are not.
- **Fix:** Pick the managed-engine dialect as the single `/state` contract: make `RAState.note_unlock` also emit `ago` (seconds since the unlock, which it can compute from `at`), make owned achievement entries carry the same status fields `view_model` reads (or change `view_model` to accept the `unlocked` boolean — one side only), drop the hardcoded gap/dupe/torn zeros or remove those keys, stop equating `webapi` with `logged_in`, and feed real receiver counters into `update_console` instead of +1-per-evaluation.
- **Benefit:** The owned preview becomes observable through the existing UI instead of silently misreporting; no fabricated telemetry reaches the user.
- **Effort:** M · **Risk:** Med · **Confidence:** High (READ)

### RE-SERV-080 — Web "Sign out" deletes the stored credentials, permanently cancels submissions, and can report failure after revocation already happened
- **Filed as:** LNCHB-04 (lnchb.md)
- **Category:** 6
- **Locations:** launcher/achievements.py:247-256 (`logout()`: `service.clear_account()`, `account_cancel.set()`, then `session.logout(forget=True)` — i.e. `CredentialStore.clear()` — then `submissions.close()` and raises `TimeoutError` if the submission thread does not join in 2 s), launcher/ra_web.py:518-543 (`do_POST /logout` maps any exception from `logout()` to `503 {"ok": false}` and only calls `state.logout()` on success), launcher/ra_submissions.py:8-59 (`Submissions` shares the caller's cancel event; once set, `offer()` returns False forever and the consumer thread has exited)
- **Problem:** Three conflations in one button. (1) Sign-out semantics: the dashboard button labelled "Sign out" passes `forget=True`, erasing the on-disk runtime token — there is no login form in the native page, so the user can only recover by restarting with CLI `--login`/`--import-credentials`. (2) Irreversibility: `account_cancel` is never cleared and the submissions thread is closed inside `logout()`, so even the still-running main loop can never submit or ping again in this process. (3) Partial failure: revocation (`clear_account`, cancel) happens *before* the fallible `submissions.close()`; if the worker child is mid-request (AccountWorker deadline is up to 30 s vs the 2 s join), the page reports 503 "sign out could not be completed" while the account is in fact already revoked and credentials already deleted — the UI state and the real state disagree, and `RAState.logout()` is skipped.
- **Fix:** Make web sign-out `forget=False` (forgetting stays an explicit CLI action), perform the fallible thread shutdown before publishing revocation (or report success once revocation is done and log the shutdown timeout separately), and do not close the shared submissions consumer on a transient sign-out.
- **Benefit:** Sign-out means sign-out; the reported result matches the resulting state; no silent credential destruction from a status page.
- **Effort:** M · **Risk:** Med · **Confidence:** High (READ)

### RE-SERV-081 — Caduceus: unauthenticated CADQ sender becomes the unlock-notice destination, and the CADA pairing key lives in the console-served games folder
- **Filed as:** LNCHB-05 (lnchb.md)
- **Category:** 6
- **Locations:** launcher/caduceus.py:170-190 (`Bridge.start` binds UDP 18197/18198 on `0.0.0.0`), launcher/caduceus.py:262 (any CADQ packet — no HMAC, format check only — sets `self.peer = peer[0]`), launcher/caduceus.py:205-229 (`read_events` sends `RAU1` unlock notices to `self.peer` on port 18195), launcher/caduceus.py:278 (CADA is HMAC-checked against `self.key`), launcher/caduceus.py:92-108 (`pairing_key` stores that key as `CADUCEUS.KEY` inside `<games folder>/ART`)
- **Problem:** The authenticated half of the protocol (CADA) is keyed by a secret stored in the ART subfolder of the very games folder the PS2 (and anything else that can read the share) is served — share-scoped readability is the design's own description in the module docstring, so the HMAC key is only as secret as the games share. Independently, the unauthenticated half (CADQ) has a side effect beyond its reply: the last CADQ sender on the LAN becomes the destination for subsequent unlock notices, so any LAN host can redirect those notices (or suppress the real console's, by being last) with one well-formed datagram. The 40 req/s global cap in `run()` does not distinguish senders.
- **Fix:** Do not let CADQ mutate notice routing — send notices only to the console address established by the authenticated/engine state; keep the pairing key out of the served tree (e.g. in the private RA profile, with only artwork in ART), accepting the pairing-flow change that implies.
- **Justification:** No new mechanism: both fixes remove an existing side effect / relocate an existing file.
- **Benefit:** The HMAC check stops being theatre against share readers, and notice routing stops being last-sender-wins on an unauthenticated packet.
- **Effort:** M · **Risk:** Med · **Confidence:** Med (READ; share-readability of the games folder is INFERRED from the folder's role as the OPL games root)

### RE-SERV-082 — Identification's `gameid` request bypasses all pacing, and its 429 backoff is discarded
- **Filed as:** LNCHB-12 (lnchb.md)
- **Category:** 6
- **Locations:** launcher/achievements.py:160-175 (`resolve_job` constructs a fresh `Transport()` per job and POSTs `gameid` directly, outside `AccountWorker`), launcher/ra_http.py:28-47 (`Transport` pacing state `_next_request` is per-instance, so a new instance always starts unpaced), launcher/ra_account_worker.py (`AccountWorker` retains `minimum_interval`/`retry_after` across children — the mechanism this call circumvents)
- **Problem:** The stack goes to considerable lengths to pace RA requests and to remember server backoff across child processes, then the owned runtime's resolver — the entry point of every identification — makes an unpaced request through a throwaway transport before the paced worker is ever invoked. Worse, the whole block is wrapped in `except Exception: return IdentificationResult()`, so a `RateLimited` from this call (which carries `retry_after`) is flattened into a generic transient failure: the worker's cooldown is never armed, and the receiver will re-queue identification and repeat the unpaced call. Under a rate-limiting server this is a self-sustaining retry loop at receiver pace, not server pace.
- **Fix:** Route the `gameid` lookup through the same `AccountWorker` (it already has a process boundary and pacing) or, minimally, keep one long-lived `Transport` for `resolve_job` and let `RateLimited` propagate to the worker's `_not_before` handling instead of swallowing it.
- **Benefit:** The documented pacing/backoff design actually covers the first request of every identification; 429s slow the client down instead of speeding retries up.
- **Effort:** M · **Risk:** Med · **Confidence:** High (READ)

### RE-SERV-083 — Caduceus `pad()` over-pads by a full 64-byte block whenever the body is already block-aligned, diverging from the owned protocol's padding
- **Filed as:** LNCHB-14 (lnchb.md)
- **Category:** 6
- **Locations:** launcher/caduceus.py:48-52 (`pad`: `max(128, ((len(data) + 64) // 64) * 64)`), launcher/ra_protocol.py:99-106 (`pad_reply`: `max(128, ((len(body) + 63) // 64) * 64)`, with the docstring noting CADQ/CADA deliberately use NUL rather than space padding — the fill byte differs by design, the length formula should not)
- **Problem:** Executed comparison (in a `/tmp` copy): body lengths 128 and 192 produce Caduceus datagrams of 192 and 256 bytes respectively, versus 128 and 192 from `pad_reply`; all non-aligned lengths agree. Any CADB reply whose formatted body lands exactly on a 64-byte multiple is sent 64 bytes longer than the shared contract — wasted datagram at best, and a length the console's DMA-aligned receive path was not sized from this formula at worst.
- **Fix:** Change `+ 64` to `+ 63` in `caduceus.pad` so only the intentional NUL-vs-space difference remains.
- **Benefit:** One padding contract across both console protocols; removes a boundary case that only triggers on exact-multiple bodies (i.e. rarely, and therefore late).
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED)

### RE-SERV-084 — `CredentialStore` wedges permanently if the profile directory pre-exists with non-0700 permissions, and `load()` can raise instead of returning "no credentials"
- **Filed as:** LNCHB-18 (lnchb.md)
- **Category:** 6
- **Locations:** launcher/ra_credentials.py:62-70 (`_check_path` raises on any group/other permission bit), launcher/ra_credentials.py:95-105 (`_prepare` uses `mkdir(exist_ok=True)` — which does not repair permissions of an existing directory — then immediately `_check_path`s it), launcher/ra_credentials.py:124-141 (`load()` calls `_prepare()`, creating directories on a read path, and propagates the permission `ValueError`), launcher/achievements.py:163,230 (both `store.load()` call sites in the owned runtime are outside any recovery path — the startup one is entirely unguarded)
- **Problem:** The owned profile directory is shared territory: `achievements.profile_dir()` and the bundled engine also create/use it. If that directory (or the `owned` subdir) ever exists with ordinary 0755 permissions — created by another tool, an archive extraction, or a umask/platform difference — every `save`/`load`/`clear` raises the same error forever, with no in-app remedy short of manually chmodding, and the owned runtime's startup `load()` turns it into a hard failure rather than a signed-out state. The check is doing real security work; the failure mode around it is the defect.
- **Fix:** In `_prepare`, when the directories are ones this store created/owns, repair their mode (`os.chmod(..., 0o700)`) before checking, and reserve the hard raise for files/symlinks it did not expect; make `load()` return `None` for a missing store without creating directories.
- **Benefit:** A recoverable permissions drift stops being a permanent, opaque lockout of the owned account feature.
- **Effort:** S · **Risk:** Med · **Confidence:** Med (READ/INFERRED — the wedge follows from the code paths; the pre-existing-0755 precondition was not executed)

### RE-SERV-085 — Shipped systemd webui env file configures a start the binary refuses
- **Filed as:** CIDOCS-02 (cidocs.md)
- **Category:** 6
- **Locations:** `packaging/systemd/ps2servers-edge-webui.env:4` (`--webui-port 8082 --bind 0.0.0.0 --config-file /etc/ps2servers-edge/config.json` — no `--auth-pass`, no `--insecure`); binary rule at `native/ps2servers-edge/cmd/ps2servers-edge/main.go:476-484`
- **Problem:** Executed with exactly those arguments, the binary exits 1: `error: refusing to serve the web UI on a non-loopback address without a password`. So `systemctl enable --now ps2servers-edge@webui` with the shipped env file fails on first start. Every other shipped env file carries extensive warning comments; this 4-line file carries none. `packaging/systemd/README.md` does disclose the trap in prose, but the file a user actually edits does not.
- **Fix:** Either ship the env file with `--bind 127.0.0.1` (the binary's own default, which starts cleanly) or add the same warning comment block the sibling env files carry, in the env file itself.
- **Benefit:** First-run systemd webui deployment works, or fails with the explanation at the point of configuration instead of only in journal output.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED)

### RE-SERV-086 — ps2servers@.service header falsely states "Edge has no SMB at all"
- **Filed as:** CIDOCS-03 (cidocs.md)
- **Category:** 6
- **Locations:** `packaging/systemd/ps2servers@.service:5-7` ("Separate from ps2servers-edge.service, which runs the Go build and serves only UDPFS and UDPBD. Edge has no SMB at all, so a user who needs SMB … cannot use that unit")
- **Problem:** Both clauses are wrong against the shipped tree: the binary has `smb` and `webui` subcommands (executed `-h` for both), `packaging/systemd/ps2servers-edge@.service` exists precisely to run them, `packaging/systemd/README.md` documents "Edge now serves SMB too", and `ps2servers-edge.service` in fact serves UDPFS only (not "UDPFS and UDPBD"). A user trusting this comment concludes the Edge SMB unit they need does not exist. The same header (line 2) also presents the template's instance keys as only "smbv1, udpfs or udpbd", while `launcher/servers.py:738` REGISTRY has eight keys (smbv1, smbv2, smbv3, udpfs, http, nfs, udpbd, retroachievements) and the systemd README table claims the template serves SMB2/3 and HTTP too — the three texts disagree with each other.
- **Fix:** Rewrite the header to match the systemd README: Edge serves SMBv1 via `ps2servers-edge@smb`; this template is for the Python/Desktop build. State the template's real key scope in one place only.
- **Benefit:** Operators stop being routed away from a working deployment path by a stale comment in the unit they are installing.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ; binary subcommands EXECUTED)

### RE-SERV-087 — OpenWrt init script's fallback enables the shared status port that the shipped config deliberately disables
- **Filed as:** CIDOCS-05 (cidocs.md)
- **Category:** 6
- **Locations:** `packaging/openwrt/files/ps2servers-edge.init:123` and `:172` (`config_get status_port smb|udpbd status_port -1`) vs shipped `packaging/openwrt/files/ps2servers-edge.config:47,63` (`option status_port '0'`) and the config's own rationale comments (off by default because udpfs owns UDP 62966 and two owners race)
- **Problem:** The shipped config and the init fallback disagree about the default. With the option present the config wins (`0`, off); if a user deletes the option — a normal way to "reset to default" in UCI — the init script silently substitutes `-1`, enabling the status listener on the port udpfs already owns, recreating exactly the first-starter-wins race the config comments warn about. The binary default is also `-1` (executed), so the package's safety property currently lives only in one config line, contradicted by the layer beneath it.
- **Fix:** Change both init fallbacks to `0` so every layer below the binary agrees that smb/udpbd status is opt-in on this platform.
- **Benefit:** Deleting a UCI option can no longer silently change which service answers discovery on the router.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ; binary default EXECUTED)

### RE-SERV-088 — `tryTrans2` can never return an error, and `TestSearchesAreBounded` never asserts the bound
- **Filed as:** GOTEST-01 (gotests.md)
- **Category:** 7
- **Locations:** `internal/smb/integration_test.go:65-69`, `internal/smb/limits_test.go:63-93`
- **Problem:** The helper is documented as "trans2 without the t.Fatal, for tests that drive it in a loop", but its body is `p, d := cl.trans2(sub, tParams); return p, d, nil` — the error return is hard-wired to `nil`, and the inner `trans2` already `t.Fatalf`s on a non-success status (`integration_test.go`, `trans2`: `cl.t.Fatalf("trans2 %#x status %#x", …)`). Both callers — the only two uses, at `limits_test.go:84` and `:89` — check `err != nil`, a branch that is unreachable. Worse, the test's stated claim is unasserted: `TestSearchesAreBounded` issues `MaxSearches*8` FIND_FIRST2 requests and then only checks the server still answers. FIND_FIRST2 succeeds whether or not old searches are evicted (eviction is silent, `handlers.go` `evictOldest(…, MaxSearches, …)`), so an implementation that held all 128 listings and grew without bound passes this test exactly as a bounded one does — the heap growth the comment describes (~80 MiB per 2,000 searches) is never measured or inspected. Proof: read of both functions; grep shows `tryTrans2` has no other callers.
- **Fix:** Delete `tryTrans2` and call `trans2` directly (its Fatal already provides the failure the error checks pretend to add); add a real bound assertion to the test — e.g. after the loop, verify via a FIND_NEXT2 on an early search ID that the oldest search was evicted, or expose/inspect the per-connection search count from the same package.
- **Benefit:** A regression that removes search eviction (a 32 MB-board killer, per the test's own comment) currently passes CI; after the fix it would fail.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-089 — `TestServerPortFallback` never exercises the fallback or any port
- **Filed as:** GOTEST-02 (gotests.md)
- **Category:** 7
- **Locations:** `internal/webui/webui_test.go:638-669`; production behaviour at `internal/webui/webui.go:237-257`
- **Problem:** Production `Serve` with `Port == 0` first tries `DefaultPort` (8082) and only on failure falls back to an OS-assigned port. The test constructs `Port: 0`, starts `Serve`, sleeps 100 ms, cancels, and asserts only that `Serve` returned nil. It never occupies 8082, never reads the bound address, and never asserts which port was bound — so the fallback branch is never entered and a wrong-port bind also passes. Proof (EXECUTED, in the /tmp copy): the production line was mutated to bind `"0"` directly, deleting the default-port attempt entirely; `go test ./internal/webui/ -run TestServerPortFallback` still **passed**.
- **Fix:** Occupy 8082 with a listener first, then assert `Serve` succeeds and the server is reachable on a different, OS-assigned port; also assert the unoccupied case binds 8082.
- **Benefit:** The schema documents "0 uses 8082 with fallback to an OS-assigned port" (`internal/webui/schema.go:169`); today that documented behaviour has no working test despite a test named for it.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED)

### RE-SERV-090 — `TestCompressionCacheDefaultsAndDisables` tests struct storage, not defaulting or disabling
- **Filed as:** GOTEST-03 (gotests.md)
- **Category:** 7
- **Locations:** `internal/udpfs/blockdev_test.go:256-276`; the behaviour it names lives at `internal/udpfs/blockdev.go:398-402`
- **Problem:** The test builds servers with `CompressionCache: 0` and `CompressionCache: 8` and asserts `New` stored the value verbatim in `cfg`. It never opens an image. The actual defaulting — `openImage` substituting `compression.DefaultCacheBlocks` when the configured value is 0 — is in a different function the test never calls, and no "disable" value or path is exercised at all. Proof (EXECUTED, in the /tmp copy): the substitution was mutated to `cache = 1`; `TestCompressionCacheDefaultsAndDisables` (and its neighbour `TestNoCompressionStopsAdvertisingContainersAsIso`) still **passed**.
- **Fix:** Drive `openImage` (or an OPEN of a `.cso` through a running server) with cache 0 and assert the opened image's cache limit is `compression.DefaultCacheBlocks`; drop "Disables" from the name or add the case that actually disables.
- **Benefit:** The 0-means-default convention is exactly the kind of silent change that doubles or quarters memory use on a 32 MB board; it is currently unguarded.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED)

### RE-SERV-091 — `TestSafeCueNameRejectsInvalidAndOverlongUTF8` has no positive control; a reject-everything stub passes
- **Filed as:** GOTEST-04 (gotests.md)
- **Category:** 7
- **Locations:** `internal/compression/chd_cuebin_test.go:119-126`; production rules at `internal/compression/chd_cuebin.go:378-388`
- **Problem:** The test asserts only two rejections (invalid UTF-8, a 256-byte name). It never asserts that any valid name is accepted, so the function can regress to "reject everything" — breaking every legitimate CUE/BIN pair — and the test stays green. Proof (EXECUTED, in the /tmp copy): `safeCueName` was stubbed to `return false` unconditionally; the test still **passed**. The same gap leaves three of the production rejection rules untested: embedded `"`, `\`, and control characters (`chd_cuebin.go:382-386`). (The test name is also slightly off: the overlong case is 252 ASCII `x`s + `.bin`, not a UTF-8 case.)
- **Fix:** Add positive cases (an ordinary `disc.bin`, a 255-byte name, a non-ASCII-but-valid name) and one case per rejection rule.
- **Benefit:** Converts a one-sided test into one that can fail in both directions; the positive side is the side users hit.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED)

### RE-SERV-092 — Windows-only filesystem test can never run in CI
- **Filed as:** GOTEST-05 (gotests.md)
- **Category:** 7
- **Locations:** `internal/filesystem/root_test.go:216-235`; CI at `.github/workflows/edge.yml:37` (test job `runs-on: ubuntu-latest`; the other Edge jobs are also `ubuntu-latest` and `edge-build.yml` builds but does not test)
- **Problem:** `TestEntryRefusesNamesWindowsWouldReinterpret` begins `if runtime.GOOS != "windows" { t.Skip("Windows path normalisation") }`. Every CI job that runs this module's tests is Linux, so the test is skipped on every CI run and on every non-Windows developer machine — it was one of the only 2 skips in the baseline run. The behaviour it guards (Windows stripping trailing dots/spaces so the OS acts on a different entry than the one checked — `"..."` resolving to the parent/root) is security-relevant path handling that currently has zero executed coverage anywhere automated. This is a feature-detection skip where the feature platform is never present in CI.
- **Fix:** Extract the name-rejection check into a platform-independent predicate and test it directly on any OS (keeping a thin Windows integration case), or add a Windows runner for the filesystem package alone.
- **Benefit:** A named, security-motivated check stops being dead coverage; the fix is less code than a new CI platform.
- **Effort:** M · **Risk:** Low · **Confidence:** High (READ|EXECUTED — skip observed in the baseline run)

### RE-SERV-093 — Three "behaviour" tests assert only that constants lie in a range
- **Filed as:** GOTEST-06 (gotests.md)
- **Category:** 7
- **Locations:** `internal/smb/wire_test.go:157-179` (`TestMaxMessageClearsTheLargestRealFrame`), `internal/smb/wire_test.go:211-220` (`TestDefaultPortIsUnprivileged`), `internal/smb/limits_test.go:157-171` (`TestConnectionSlotsAreNotHeldByIdleSockets`); constants at `internal/smb/wire.go:84` (`DefaultPort = 1111`), `internal/smb/wire.go:105,118` (`MaxMessage`, `MaxConnections`), `internal/smb/server.go:120` (`idleTimeout = 5 * time.Minute`)
- **Problem:** None of the three performs the behaviour in its name. `TestDefaultPortIsUnprivileged` never binds a socket — it asserts `DefaultPort >= 1024`. `TestMaxMessageClearsTheLargestRealFrame` never sends a message — it asserts arithmetic over `MaxMessage` and `MaxConnections`. `TestConnectionSlotsAreNotHeldByIdleSockets` never opens a connection — it asserts `0 < idleTimeout <= 30m`, and its own comment concedes it "asserts the mechanism exists rather than waiting for it". These tests can only fail if someone edits a constant past the bound; no behavioural regression (bind path ignoring `DefaultPort`, dispatcher rejecting a largest-frame message, accept loop never setting a read deadline) can fail them. They are pins on values, presented as tests of mechanisms.
- **Fix:** Keep the constant pins if wanted, but rename them as such (e.g. `TestDefaultPortConstantIsUnprivileged`) and add one behavioural case each: bind with the default port config, round-trip a near-`MaxMessage` frame, and open a silent connection against a server with a test-shortened idle timeout and observe it being dropped.
- **Benefit:** Test names stop claiming coverage that does not exist — the failure mode that lets a reader (or a future audit) mark these areas "tested".
- **Effort:** M · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-094 — UDPFS test helper `result()` reports a truncated reply as success (0)
- **Filed as:** GOTEST-07 (gotests.md)
- **Category:** 6
- **Locations:** `internal/udpfs/write_test.go:143-148`
- **Problem:** `result(payload)` is the shared reply decoder for the whole `udpfs` test package (used across `write_test.go`, `blockdev_test.go`, `blockonly_test.go`, `fileid_bwrite_test.go`, `handoff_write_test.go`, `pathops_test.go`, `rediscovery_test.go`, `compression_operations_test.go`, `chd_operations_test.go`). For any payload shorter than 8 bytes it returns `0` — which is the protocol's success value. A server regression that truncates a reply therefore satisfies every `result(...) == 0` expectation and every `result(...) <= 0`-style check in the package instead of failing the test; only expectations of a specific negative errno or a positive byte count would notice, and several success-path assertions would then read unrelated bytes or panic on slicing, misreporting the cause. (One caller, `pathOp` in `pathops_test.go`, does length-check before calling `result`; most do not.)
- **Fix:** Make the helper take `t` and `t.Fatalf` on a short payload (it is test-only code; every caller already has a `*testing.T` in scope via the client), or return a sentinel that no expectation accepts.
- **Benefit:** Removes a package-wide mask over exactly the class of bug (short/malformed replies) these wire tests exist to catch.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-095 — `TestUnknownServiceIDIsStillIgnored` is negative-only and passes with the transport completely dead
- **Filed as:** GOTEST-08 (gotests.md)
- **Category:** 7
- **Locations:** `internal/udpfs/status_test.go:150-173`
- **Problem:** The test sends one DISCOVERY with an unknown service ID and asserts no reply arrives within 500 ms. It performs no positive exchange against the same server instance, so "correctly ignored" is indistinguishable from "request never arrived", "server wedged", or "discovery handling broken for all packets". Proof (EXECUTED): in the baseline run, with every UDP `sendto` in the sandbox blocked (`operation not permitted`), this test **passed** — its assertion was satisfied by a transport on which the server could not have replied to anything. Contrast the neighbouring `TestOrdinaryDiscoveryStillWorksAlongsideStatus` (`status_test.go:109-148`), which does prove liveness, but against a different server instance in a different test.
- **Fix:** On the same server and socket, first complete an ordinary DISCOVERY/INFORM exchange (positive control), then send the unknown-service-ID query and assert silence.
- **Benefit:** The anti-reflector guarantee this test pins becomes a statement about the server, not about the network.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED)

### RE-SERV-096 — Duplicated test scaffolding: the same mutex log buffer twice in one package, and CSO fixture builders re-implemented four times
- **Filed as:** GOTEST-09 (gotests.md)
- **Category:** 2
- **Locations:** `internal/udpfs/observability_test.go:22-40` (`synchronizedLogBuffer`) vs `internal/udpfs/session_summary_test.go:22-38` (`lockedBuffer`); further copies at `internal/smb/session_summary_test.go:17-33` and `internal/udpbd/session_summary_test.go:18-34`. CSO/CISO header builders: `internal/compression/image_test.go:13-33` (`writeContainer`), `internal/compression/sizeof_test.go:16-47` (`writeBigIndexCSO`), `internal/compression/corrupt_index_test.go:22-40` (`writeCorruptIndex`), `internal/udpfs/compression_operations_test.go:16-36` (`writeUDPFSContainer`)
- **Problem:** Within the single `udpfs` test package, two differently-named types implement byte-identical mutex-guarded `bytes.Buffer` log capture (`Write`/`String`); the same type is then copied into two more packages. Separately, the 24-byte CISO header + index layout is hand-assembled in at least four test helpers across two packages, each with its own field offsets — a format change (or an offset mistake in one copy) has to be found and fixed four times, and the copies can silently diverge so different tests exercise slightly different containers.
- **Fix:** Keep one log-buffer type per package (delete `synchronizedLogBuffer` in favour of the existing `lockedBuffer` in `udpfs`); promote one container-builder helper per package with parameters for the fields the corrupt/sizeof cases vary (declared size, index gap, magic), instead of four open-coded copies.
- **Benefit:** Less test code to keep correct; one place to fix when the container layout or log-capture needs change. (Cross-package copies of `lockedBuffer` are the weaker half — Go test packages cannot share helpers without an exported test-support package — and are worth less than the same-package duplicate.)
- **Effort:** M · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-097 — The fuzz target and the benchmark are never run as fuzzing or benchmarking anywhere
- **Filed as:** GOTEST-10 (gotests.md)
- **Category:** 7
- **Locations:** `internal/protocol/packet_test.go:23-30` (`FuzzParseDataPacket`), `internal/udpfs/udpwrite_test.go:338-377` (`BenchmarkUDPBatch`); CI: `.github/workflows/edge.yml:50-82`
- **Problem:** `go test` executes `FuzzParseDataPacket` only against its 3 seed inputs, as an ordinary unit test; no workflow runs `go test -fuzz` for any duration, and no workflow runs `-bench`, so `BenchmarkUDPBatch` — the only performance regression check for the UDP batch path — has never executed in CI. Proof: grep for `bench`/`fuzz` over `.github/workflows/*.yml` returns no hits; the baseline `go test -json` run contains no benchmark result. Both artefacts look like coverage (a fuzz target for the packet parser that faces untrusted datagrams; a benchmark for the hot send path) while functioning, in automation, as one small table test and as dead code respectively.
- **Fix:** Add a short bounded CI step (e.g. `-fuzztime=60s` for `internal/protocol` on a schedule or in the Edge workflow, and `-bench=. -benchtime=1x` as a compile/smoke gate for the benchmark), or delete the benchmark if nobody intends to read its numbers.
- **Justification:** (only if fix ADDS) The added CI time is bounded (~1 minute) and is the only way either artefact produces the signal it exists for; the alternative half of the fix — deletion — adds nothing.
- **Benefit:** The parser that faces malformed network input actually gets fuzzed; the batch benchmark either runs or stops implying a perf gate exists.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ|EXECUTED — grep over workflows; baseline run contents)

### RE-SERV-098 — Dead keep-alive line for an unused import in `blockdev_test.go`
- **Filed as:** GOTEST-11 (gotests.md)
- **Category:** 1
- **Locations:** `internal/udpfs/blockdev_test.go:278`
- **Problem:** `var _ = session.Pending // keep the session import meaningful across refactors` exists solely to keep the `session` import compiling; nothing else in the file uses the package (grep of the file shows no other `session.` reference). It is dead code whose comment announces that the import, not any test need, is what is being preserved.
- **Fix:** Remove the line and the import.
- **Benefit:** One less misleading artefact for a reader tracing which session behaviour this file tests (none).
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-099 — `TestBlockGeometryIsConsistent` asserts identities guaranteed by the implementation's own formulas
- **Filed as:** GOTEST-12 (gotests.md)
- **Category:** 7
- **Locations:** `internal/udpbd/udpbd_test.go:50-65`; production at `internal/udpbd/protocol.go:101-104`
- **Problem:** Production `blockGeometry` is one expression set: `blockSize = 1 << (shift+2); return blockSize, rdmaMaxPayload / blockSize, SectorSize / blockSize`. The test asserts (a) `size == 1<<(shift+2)` — a restatement of the first line; (b) `perSector * size == SectorSize` for shifts {3,5,6,7} — true for exactly the shifts whose block sizes (32/128/256/512) divide 512, which are the only shifts ever passed; and (c) `perPacket * size <= rdmaMaxPayload` — identically true for any integer-division quotient. Unlike its neighbour `TestBlockShiftMatchesReferenceTable`, which pins values from the hardware-validated Python server (a real external oracle), this test has no oracle outside the code it tests: it can catch a formula edit, but it cannot catch the implementation and the expectation being wrong together, and two of its three assertions cannot fail for any values the function can produce.
- **Fix:** Replace the derived assertions with a literal table of expected `(blockSize, blocksPerPacket, blocksPerSector)` per shift, taken from the reference implementation's geometry, as the block-shift test already does.
- **Benefit:** The geometry that decides RDMA packetisation gets an external pin instead of a self-consistency check.
- **Effort:** S · **Risk:** Low · **Confidence:** Med (READ|INFERRED — the integer-division identity is arithmetic; the "no external oracle" judgement is inferred from the two files)

### RE-SERV-100 — `TestSharedHandshakeFixtures` duplicates the hardcoded cases and degrades to a silent skip
- **Filed as:** GOTEST-13 (gotests.md)
- **Category:** 7
- **Locations:** `internal/udpfs/server_test.go:15-49`
- **Problem:** Two weaknesses compound. (1) Duplication: the fixture file it loads (`conformance/fixtures/handshake_cases.json`: standard-zero 0/0, modulo-fresh 0/1, modulo-resumed 7/8, modulo-wraparound 4095/0, nonmatching-fallback 7/0) contains exactly the five cases already hardcoded in `TestClassify` in the same file ({0,0},{0,1},{7,8},{4095,0},{7,0}) — as a Go test it adds no case the hardcoded test lacks; its value is purely cross-implementation sharing of the file. (2) Skip-to-pass: when the file is absent it calls `t.Skipf` and the run is green — and the path (`../../../../conformance/…`) points outside the Go module, so any module-only copy, vendored checkout, or downstream consumer of the module silently loses the test, as happened in this audit's mandated /tmp copy (it was 1 of the 2 baseline skips). Nothing in CI distinguishes "ran with 5 cases" from "skipped".
- **Fix:** Keep the fixture as the cross-language artefact, but make the Go test fail — not skip — when the file is missing in a full checkout (e.g. skip only when an explicit env marker says the module was copied standalone), and log the number of cases executed so a zero-case run is visible.
- **Benefit:** The shared-conformance claim stays honest in exactly the environments (copies, vendoring) where it currently evaporates silently.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ|EXECUTED — skip observed in the baseline; fixture contents compared line by line with the hardcoded table)

### RE-SERV-101 — The attribution test mutates, and depends on, the developer's real checkout
- **Filed as:** PYTESTA-01 (pytesta.md)
- **Category:** 7
- **Locations:** tests/test_attribution_policy.py:22-40
- **Problem:** The suite's only test of `.github/workflows/attribution-policy.yml` runs `git tag -a attribution-empty-range-<pid> HEAD` **in the repository under test** (`cwd=ROOT`), extracts the workflow's shell script, runs it, and deletes the tag in a `finally`. Three consequences, one of them EXECUTED in this baseline: (1) in any `.git`-less copy (source tarball, this audit's copy) the test errors with git exit 128 instead of skipping — it checks only for `bash`, never for a usable checkout; (2) a hard kill between create and delete leaves a stray `attribution-empty-range-*` tag in the user's real repo; (3) the test writes to the very repo it is auditing, so it cannot run in a read-only checkout. The PID-suffixed tag name mitigates parallel runs but none of the above.
- **Fix:** Build a throwaway repo in a temp directory (`git init`, one empty commit), copy or reference the workflow script against that repo's `HEAD`, and skip cleanly when `git rev-parse HEAD` fails in `ROOT`. The workflow script itself takes `BEFORE`/`AFTER` from the environment, so it does not need the real history.
- **Benefit:** The test runs in tarballs, read-only checkouts and sandboxes; running the suite can never leave tags behind in the user's repo.
- **Effort:** M · **Risk:** Low · **Confidence:** High (READ|EXECUTED — failed in the baseline run for exactly the `.git` reason)

### RE-SERV-102 — Eight test files silently run zero tests when executed directly
- **Filed as:** PYTESTA-02 (pytesta.md)
- **Category:** 7
- **Locations:** tests/test_achievement_loader.py:1-48 · tests/test_achievement_viewer.py:1-86 · tests/test_caduceus_reference_wire.py:1-60 · tests/test_caduceus_setup.py:1-91 · tests/test_desktop_instance.py:1-99 · tests/test_discord_presence.py:1-204 · tests/test_game_library.py:1-299 · tests/test_library_panel.py:1-360 (script census: these 8 of the 36 in-scope files contain no `unittest.main()`; the other 28 all end with the `if __name__ == "__main__": unittest.main()` guard at the final line)
- **Problem:** `python tests/test_game_library.py` (etc.) exits 0 having run nothing — no error, no output, no tests. Discovery and `python -m unittest tests.test_game_library` work, so CI is unaffected, but the direct-execution path — the one a developer reaches for when debugging a single file — reports success for a run that never happened. This is the same failure family `tests/__init__.py`'s docstring says already hid a failing test on main once ("CI silently runs whatever handful of modules are named by hand").
- **Fix:** Add the same two-line `__main__` guard the other 28 files already carry to these 8 files. No new mechanism; pure consistency with the suite's own convention.
- **Benefit:** A direct run can never again masquerade as a pass; the suite has one invocation convention instead of two.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ — census by AST/script over all 36 files)

### RE-SERV-103 — The Edge flag-extraction helpers are copied verbatim between test files
- **Filed as:** PYTESTA-03 (pytesta.md)
- **Category:** 2
- **Locations:** tests/test_edge_manual.py:26-28 and :71-80 · tests/test_edge_service_modes.py:26-28 and :208-218 · (near-copy) tests/test_edge_flag_exposure.py:96-111, tests/test_edge_service_modes.py:224-236
- **Problem:** `_read()` is byte-identical in `test_edge_manual.py` and `test_edge_service_modes.py`, and `_flags_of()` (extract a Go function's `fs.*("name", …)` flag names from `main.go`) is AST-identical in both files — only the comments differ (script census: exact AST-dump match). A third variant of the same "slice one section/function out of a text file" logic exists as `_section()`/`_section_options()`/`_udpfs_flags()` across the same three files. These parsers are the load-bearing part of the Edge packaging guards: if the Go source layout changes (e.g. `fs.Var` gains a new shape), each copy must be found and fixed separately, and a copy that is missed degrades silently to "no flags found" in exactly the tests whose job is noticing missing flags.
- **Fix:** Move `_read`, `_flags_of` and the section slicer into one shared tests helper (e.g. alongside `tests/library_fixtures.py`) and import them in the three Edge files; delete the copies.
- **Benefit:** One parser to fix when `main.go` changes shape; the three Edge guard files stay focused on their distinct assertions.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ|EXECUTED — AST census run by script)

### RE-SERV-104 — The shared ISO fixture lives inside a test module — and one consumer re-implements it anyway
- **Filed as:** PYTESTA-04 (pytesta.md)
- **Category:** 2
- **Locations:** tests/test_disc_identity.py:34-59 (`iso_fixture`) · tests/library_fixtures.py:31 · tests/test_library_identity.py:14 · tests/test_library_index.py:13 · tests/test_game_library.py:41,74 (imports) and :266-299 (inline re-implementation)
- **Problem:** `iso_fixture()` — the synthetic ISO9660 builder half the suite depends on — is defined in `test_disc_identity.py`, so `library_fixtures.py` and at least four other test files import a helper out of a sibling *test* module; renaming or splitting that test file breaks the fixture library for every consumer. Worse, `test_game_library.py` imports `iso_fixture` twice (lines 41, 74) and then, in `test_native_ps2_hash_matches_independent_boot_name_and_elf_digest` (266-299), hand-rolls a second private ISO9660 builder (`record()` plus manual PVD/directory assembly, ~30 lines) for a case the shared builder already covers — it accepts custom `boot` and `elf` arguments, which is all that test varies.
- **Fix:** Move `iso_fixture` (and its `record`/`both` helpers) into `tests/library_fixtures.py` or a `tests/fixtures` module, re-export it from `test_disc_identity` if its own tests want the local name, and rewrite the game-library hash test to call it with `boot=`/`elf=`. Delete the inline builder.
- **Benefit:** Fixture code has one home that is not itself a test file; one ISO builder to keep correct instead of two that can drift apart on exactly the byte layout the identity tests exist to pin.
- **Effort:** M · **Risk:** Low · **Confidence:** High (READ — all import sites grep-verified)

### RE-SERV-105 — test_library_preferences executes AST-extracted copies instead of the production methods
- **Filed as:** PYTESTA-05 (pytesta.md)
- **Category:** 6
- **Locations:** tests/test_library_preferences.py:17-33, :40-58, :60-68
- **Problem:** The file never instantiates `LibraryWindow` and never calls `save_preferences()` or `show_cover()` on a production object (grep-verified: the names occur only as AST search strings). Instead it parses `launcher/library_gui.py`, lifts the two method `FunctionDef`s out of the class, `exec`s them in a hand-built namespace containing only a fake `config`, and calls them with a `SimpleNamespace` fake `self`. Two failure modes follow: (1) false failures — the moment either method references any other module global, helper or import from `library_gui.py`, the extracted copy raises `NameError` for a harness reason, not a product reason; (2) unobservable behaviour — attribute wiring, the real `self`'s widget surface beyond the faked fields, and any interaction with the rest of the window are structurally outside what the test can see, while the test's name claims the preferences themselves were checked. The third test in the file (:60-68) is substring assertions over source text, which pin spelling, not behaviour. The suite already has the sounder pattern elsewhere — unbound production methods driven with a fake `self` (e.g. `LauncherLifecycleTests` in tests/test_directlink.py) — which keeps the real globals and the real function object.
- **Fix:** Call the real unbound methods (`LibraryWindow.save_preferences(fake_self)`) with the module imported normally, faking only the attributes/widgets the method touches; keep the AST only as the way to *find* nothing — it should not be the thing under test.
- **Benefit:** The test fails only when the production behaviour changes, survives internal refactors of `library_gui.py`, and actually exercises the shipped function objects.
- **Effort:** M · **Risk:** Low · **Confidence:** High (READ — production symbols' absence from the test verified by grep)

### RE-SERV-106 — The netinfo detailed-format test asserts only the formatter's own constant strings
- **Filed as:** PYTESTA-06 (pytesta.md)
- **Category:** 6
- **Locations:** tests/test_netinfo_and_smb.py:20-22 (with :15-17)
- **Problem:** `test_detailed_ip_info_formatting` calls the real `netinfo.detailed_ip_info()` against whatever host runs the suite, then asserts exactly two substrings: the literal header line and `"Custom IP..."`. The second is a hard-coded sentence inside the formatter itself (`launcher/netinfo.py:215`, grep-verified), and the first is its fixed title — so the test can only fail if someone edits those literals, and it asserts nothing about any address, adapter or formatting of actual detected data (which is host-dependent and therefore silently unexamined). Its `"Custom IP..."` assertion also duplicates the intent of `test_ip_choices_contains_custom_option` directly above it (:15-17) without testing the same code path.
- **Fix:** Patch `netinfo.all_ipv4` (the formatter's input) to a fixed address set and assert those addresses appear in the output in the formatted form; drop the duplicated literal assertion or point it at `ip_choices()` only.
- **Benefit:** The test would catch a formatter that drops, mangles or mislabels real addresses — currently impossible for it to do — and stops depending on the runner's network configuration.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-107 — A traversal test whose name promises the opposite of what it asserts
- **Filed as:** PYTESTA-07 (pytesta.md)
- **Category:** 6
- **Locations:** tests/test_http_path_aware.py:208-218
- **Problem:** `test_traversal_and_unknown_paths_resolve_to_nothing` loops over four names; for `"../DVD/Game.iso"` it asserts the lookup **succeeds** (`assertIsNotNone`), because `normpath` folds the `..` back inside the whitelist. The inline comment explains the whitelist logic honestly, but the test *name* — the thing a reader scanning a green run, or a future editor deciding what may be changed, actually sees — states that traversal resolves to nothing. A later change that made folded traversal return `None` would "fix" the code to match the name and break this test for the wrong-looking reason; a change that widened real traversal would still leave a test named as if traversal were refused.
- **Fix:** Split the folded-`..` case into its own test named for what is actually guaranteed (e.g. `test_dotdot_that_folds_back_inside_the_root_is_a_normal_lookup`), leaving the refusal test's name true of every case in it. Words only; no assertion changes.
- **Benefit:** The suite's names stay a trustworthy summary of the server's path-safety guarantees — important in a file whose other tests are the traversal defence.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-108 — The hardlink backup test passes vacuously where hardlinks are unavailable
- **Filed as:** PYTESTA-08 (pytesta.md)
- **Category:** 6
- **Locations:** tests/test_game_library.py:185-198
- **Problem:** `test_backup_rejects_live_database_and_hardlink` wraps `linked.hardlink_to(...)` in `try/except OSError: return`. On any filesystem where hardlinks cannot be created, the test therefore *passes* having verified only the live-database rejection — the hardlink rejection, half its name and the more interesting aliasing case, is silently untested with no skip recorded. Sibling tests in the same suite use the honest form for the same situation (`except OSError: self.skipTest(...)`, e.g. the symlink tests in tests/test_library_index.py and tests/test_library_recursive_scan.py). In this baseline's Linux environment hardlinks work and the full test did run, so this is a portability hole, not a local failure.
- **Fix:** Replace the bare `return` with `self.skipTest("Host cannot create hardlinks")` (after asserting the live-database half, or in a separate test method for the hardlink half).
- **Benefit:** A green run never again claims hardlink protection on a machine that could not exercise it; the skip shows up in the count where a reviewer can see it.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ|EXECUTED — full test ran and passed in the baseline)

### RE-SERV-109 — The display-guard + full-app construction prologue is copy-pasted through the GUI tests
- **Filed as:** PYTESTA-09 (pytesta.md)
- **Category:** 2
- **Locations:** tests/test_autostart_and_firewall_options.py:46-64 and the same block repeated at :84, :106, :133, :205, :228, :258, :282, :306 (script census: 9 × `SkipTest("no display")`, 10 × `tk.Tk()` in this file) · same shape at tests/test_about_page.py:106-152 and throughout tests/test_library_panel.py (7 × `tk.Tk()`)
- **Problem:** Every GUI test in `test_autostart_and_firewall_options.py` repeats the same ~9-line prologue — import tkinter, catch `ImportError`, construct `tk.Tk()`, catch `TclError`, raise the same skip — and then constructs a complete `LauncherApp`. The copies have already drifted: two tests in the file probe the display with a throwaway `tk.Tk().destroy()` before using the `_app_with_fake_servers` helper, the others construct the root directly, and `test_about_page.py`'s version additionally juggles environment save/restore that the autostart file does in `setUp`. On a machine *with* a display this is also the slowest pattern in the half: nine full application constructions for nine option assertions.
- **Fix:** Extract one `_require_display()` / app-factory helper (the pattern `test_gui_layout.py` already uses at class level with `setUpClass`) and have each test call it; keep per-test app construction only where a test genuinely needs a fresh app, and say so there.
- **Benefit:** One place to fix when Tk/display handling changes; the drift between the copies stops growing; display-machine runs get faster if construction can be shared per class.
- **Effort:** M · **Risk:** Low · **Confidence:** High (READ|EXECUTED — repetition counted by script)

---

### RE-SERV-110 — Mid-file `unittest.main()` orphans tests under direct execution in two SMB2 test files (23 + 5 tests)
- **Filed as:** PYTESTB-01, PYTESTB-02 (pytestb.md)
- **Category:** 7
- **Locations:** tests/test_smb2_server.py:616-617 (main block), with 8 test classes defined after it at lines 620, 697, 736, 758, 808, 870, 906, 915 (file is 944 lines)
- **Problem:** `if __name__ == "__main__": unittest.main()` sits mid-file. `unittest.main()` calls `sys.exit`, so running `python tests/test_smb2_server.py` — a natural way to run one file while debugging, and the style several sibling files document in their docstrings ("Run: python -m unittest …" is safe, bare-script is not distinguished) — silently never defines or runs MalformedRequestBuffers (4 tests), RenameSafety (3), ParentEntry (1), DomainHandling (3), SecurityBufferFraming (5), NeutrinoClientShape (3), ShareNameCollisions (1) and BindHint (3): 23 tests by AST count. Proven by execution in a /tmp copy: direct run reports "Ran 33 tests … OK", `python -m unittest tests.test_smb2_server` reports "Ran 56 tests … OK" — both green, so the loss is invisible. Under CI's `unittest discover` the classes *are* collected (the main block is name-guarded), so this is a direct-execution orphaning, not a CI gap — but a developer running the file directly gets a green result from 59% of it.
- **Fix:** Move the `if __name__ == "__main__": unittest.main()` block to the end of the file.
- **Benefit:** Direct execution runs the same 56 tests CI does; no silent partial-green.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED)

**Same mechanism in the parallel implementation** (filed separately as PYTESTB-02 in pytestb.md, merged at consolidation):

- **Category:** 7
- **Locations:** tests/test_smb2_paths.py:202-203 (main block), class WindowsDeviceNames at line 206 (file is 249 lines)
- **Problem:** Identical pattern to PYTESTB-01: the main block precedes the WindowsDeviceNames class (5 tests). Proven by execution in a /tmp copy: direct run "Ran 14 tests … OK" vs discover "Ran 19 tests … OK". The orphaned class covers Windows reserved device names — exactly the tests a developer on Windows would run directly.
- **Fix:** Move the main block to the end of the file.
- **Benefit:** Direct execution and discover agree on the test count.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED)

### RE-SERV-111 — Cross-implementation encoder test converts a Go timeout into a skip
- **Filed as:** PYTESTB-03 (pytestb.md)
- **Category:** 6
- **Locations:** tests/test_router_status.py:177-178
- **Problem:** In `CrossImplementation.test_go_and_python_encoders_agree`, a `subprocess.TimeoutExpired` from `go run` (timeout=180s) is caught and turned into `self.skipTest("go run timed out")`. A hung or pathologically slow Go encoder — precisely the divergence this test exists to catch — is therefore reported as a skip, not a failure, and CI stays green. The same file correctly `self.fail()`s when `go run` exits non-zero (the encoders "were never compared"), and the sibling parity test in tests/test_smb_wire_parity.py:104 does the same; only the timeout path is downgraded. (The absence skips at lines 142/145 — no Go toolchain, no Edge source — are legitimate environment skips and are not part of this finding.)
- **Fix:** Let `TimeoutExpired` fail the test (or re-raise as a failure with the timeout noted), as the non-zero-exit path already does.
- **Benefit:** A wedged Go build can no longer masquerade as an environment skip in the one test that compares the two status encoders byte-for-byte.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-112 — Banner address test asserts nothing when the host returns no addresses
- **Filed as:** PYTESTB-04 (pytestb.md)
- **Category:** 6
- **Locations:** tests/test_smb2_banner.py:125-131
- **Problem:** `LocalAddressListingTests.test_lists_only_real_ipv4_addresses` places every assertion inside `for ip in smb2_server._local_ipv4_addresses():`. If that function returns an empty list — e.g. a regression that makes it return nothing, or a sandboxed/container host with no non-loopback IPv4 — the test passes having asserted nothing. The failure mode it guards against (loopback or IPv6 leaking into the banner) and the failure mode "returns nothing at all" are indistinguishable to it.
- **Fix:** Capture the list first, assert it is non-empty, then run the per-address assertions.
- **Justification:** The fix adds one assertion; it is the only way to make the existing assertions reachable-by-construction rather than by host luck.
- **Benefit:** The test can actually fail on the hosts where it currently vacuously passes, including restricted CI sandboxes.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-113 — systemd unit tests assert only inside loops over parsed content, with no non-emptiness guard
- **Filed as:** PYTESTB-05 (pytestb.md)
- **Category:** 6
- **Locations:** tests/test_systemd_units.py:119 (test_template_instance_keys_are_real_servers), tests/test_systemd_units.py:205 (test_readwritepaths_tolerates_a_missing_directory)
- **Problem:** Both tests derive their subjects by parsing the shipped unit files (template instances / `ReadWritePaths` entries) and assert only inside the resulting loops. If the parsing finds nothing — a renamed section, a changed directive spelling, a template that stops matching the extraction pattern — the loops run zero times and the tests pass. The tests therefore cannot distinguish "units are correct" from "my extraction stopped seeing the units".
- **Fix:** Assert each extracted collection is non-empty before looping (and, for the instance-keys test, pin the expected instance set size or names).
- **Justification:** Adds guard assertions only; no new machinery.
- **Benefit:** A parsing drift in the test itself stops being a silent pass over the repo's service definitions.
- **Effort:** S · **Risk:** Low · **Confidence:** Medium (READ)

### RE-SERV-114 — Share-name test is tautological: it reads back the dict it just passed in
- **Filed as:** PYTESTB-06 (pytestb.md)
- **Category:** 6
- **Locations:** tests/test_smbv1_share_and_credentials.py:182-187
- **Problem:** `TheShareNameIsHonoured.test_a_named_share_is_found` constructs `SmbServer({"roms": Share("roms", d)}, …)` and then asserts `"roms" in server.shares`. That reads back exactly the mapping handed to the constructor; it performs no tree-connect by name, so a server that stored shares under a normalised/other key — or that failed to resolve "roms" at connect time — would still pass. The class name claims the share *name* is honoured on the wire; the test observes only dict storage.
- **Fix:** Drive a real TREE_CONNECT for the name "roms" through the server (the file already has socket-level helpers in neighbouring classes) and assert the connect succeeds, or delete the test if constructor storage is covered elsewhere.
- **Benefit:** The named-share behaviour the class advertises becomes observable; today it is structurally unobservable.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-115 — Setup-guide tests assert substrings of the GUI source instead of behaviour
- **Filed as:** PYTESTB-07 (pytestb.md)
- **Category:** 7
- **Locations:** tests/test_setup_guide.py:36-54 (test_gui_button_connected_once, test_first_run_invitation_and_persistent_status), tests/test_setup_guide.py:90-92 (test_close_callback_distinguishes_finish_from_dismissal)
- **Problem:** Several tests read `launcher/gui.py` / `launcher/setup_guide.py` as text and `assertIn` exact source fragments — e.g. counting that `text="PS2 setup guide…"` occurs once, and pinning literal lines such as `self.root.after(1200, self._offer_ps2_setup_guide)`. These fail on any behaviour-preserving refactor (renamed variable, reformatted call) and pass on behaviour-breaking changes that keep the strings (e.g. the callback wired but never invoked due to an early return elsewhere). They pin spelling, not the invitation/close semantics their names claim.
- **Fix:** Where the GUI cannot be instantiated headlessly, keep at most one smoke-level source check and move the semantic assertions to the guide's own state machine (the file's other tests already exercise `setup_guide.py` data directly); otherwise delete the substring pins.
- **Benefit:** Refactors stop breaking tests that were never measuring behaviour; real wiring regressions are caught by tests that execute the path.
- **Effort:** M · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-116 — Stop-all tests are AST inspections of gui.py, not executions of stop-all
- **Filed as:** PYTESTB-08 (pytestb.md)
- **Category:** 7
- **Locations:** tests/test_status_client.py:206-272 (class StopAllStaysUnconditional)
- **Problem:** These tests `ast.parse` `launcher/gui.py`, locate `LauncherApp.stop_all`, and walk its Call nodes to prove it never calls prompting functions. The docstring discloses why (the GUI "cannot be instantiated headlessly"), and the property guarded — stop-all must never block on a prompt — is real. But the check is syntactic: a prompt introduced via a helper whose name is not in the watched set, or via `getattr`/indirection, passes; conversely a comment-free rename of a watched helper fails it. Other in-scope tests do instantiate GUI pieces with a skip-on-no-display pattern (e.g. tests/test_verbose_default_and_log_stamps.py's TerminalStamp test imports `launcher.gui` and skips only if tkinter is absent), so "cannot be instantiated" is stronger than the suite's own practice elsewhere.
- **Fix:** Keep the AST check if desired, but label it as a source lint in the class name/docstring, and add one behavioural test that calls the stop path with the prompt functions stubbed to raise, under the same skip-if-no-tkinter guard used elsewhere.
- **Justification:** The added test is the only part that observes the claimed behaviour; the AST half alone cannot see indirect prompts.
- **Benefit:** The "unconditional" guarantee rests on execution, not on call-graph spelling.
- **Effort:** M · **Risk:** Low · **Confidence:** Medium (READ)

### RE-SERV-117 — "Every edge target has a purpose" test checks only that label strings exist
- **Filed as:** PYTESTB-09 (pytestb.md)
- **Category:** 7
- **Locations:** tests/test_release_downloads.py:71 (test_every_edge_target_has_a_purpose)
- **Problem:** The test body loops over the Edge target labels and performs only `assertIn(label, script_text)` against the build script text. It verifies that each label string appears somewhere in the script — not that the target has a purpose, a distinct output, or any effect. A target reduced to a no-op echo would still pass, and the test name overstates the guarantee to anyone reading a failure list.
- **Fix:** Either assert something about each target's block in the script (e.g. the artefact name it produces), or rename the test to what it checks (labels are referenced in the script).
- **Benefit:** The test's claim matches its evidence; release-target rot becomes detectable.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-118 — POSIX permission test passes by bare `return` on Windows instead of skipping
- **Filed as:** PYTESTB-10 (pytestb.md)
- **Category:** 7
- **Locations:** tests/test_ra_credentials.py:76-78
- **Problem:** `test_posix_insecure_permissions_are_rejected` begins `if os.name == "nt": return` — a bare return, so on Windows the test reports as *passed*, not skipped, while having run nothing. Test counts and pass reports on Windows therefore overstate coverage by one, and a future reader cannot tell from results that the POSIX branch never ran. Mitigation, stated in the test's own comment: the platform roundtrip test above it does exercise real DPAPI on Windows, so the credential store is not untested there — the defect is the reporting, not total absence of coverage.
- **Fix:** Use `self.skipTest("POSIX permission bits do not apply on Windows")` instead of the bare return.
- **Benefit:** Windows runs report an honest skip; pass counts mean the test ran.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-119 — Identical helper bodies duplicated across test files
- **Filed as:** PYTESTB-11 (pytestb.md)
- **Category:** 2
- **Locations:** tests/test_ra_account_worker.py:31 (`stalled`), tests/test_ra_resolver_process.py:15 (`stuck`); tests/test_smbv1_compressed.py:44 (`_request`), tests/test_smbv1_read_after_write.py:22 (`_request`)
- **Problem:** Census by AST body-hash across all 84 files found **zero** duplicated *test-method* bodies — but the helper layer duplicates: `stalled` and `stuck` are the same infinite-sleep job body under two names in two RetroAchievements test files, and the SMBv1 `_request` SimpleNamespace factory is copied verbatim into two files. Each copy can drift independently (e.g. a new field added to one `_request` only), producing file-dependent fake shapes for the same production request type.
- **Fix:** Hoist each pair into the shared test-support module the suite already uses for fixtures, and import it.
- **Benefit:** One definition per fake shape; drift between copies becomes impossible.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-120 — Two identical payload builders under different names inside test_transfer_limits.py
- **Filed as:** PYTESTB-12 (pytestb.md)
- **Category:** 2
- **Locations:** tests/test_transfer_limits.py:109 (read_payload), tests/test_transfer_limits.py:118 (write_req_payload)
- **Problem:** `read_payload(handle, size)` and `write_req_payload(handle, size)` have byte-identical bodies — both return `struct.pack("<BBBBiI", 0, 0, 0, 0, handle, size)`. Two names for one wire shape invites the reader to believe READ and WRITE_REQ headers differ where the test itself says they don't, and a future edit to one (e.g. adding a flags byte) silently desynchronises the pair.
- **Fix:** Delete one and call the other from both places (or keep one name that says what the shape is).
- **Benefit:** Less code, and the read/write header equivalence the file relies on is stated once.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-121 — Whole-registry launcher test asserts only that build_argv returns a list
- **Filed as:** PYTESTB-13 (pytestb.md)
- **Category:** 6
- **Locations:** tests/test_udpfs_config.py:360-382 (LauncherModeLaunchTests.test_every_mode_builds_argv_from_defaults)
- **Problem:** The class docstring claims "Every registered mode must build an argv from its own field defaults", guarding against a builder NameError presenting as a mode that refuses to start. The sole assertion is `assertIsInstance(server.build_argv(values), list)`. A builder that returns `[]`, or a list of nonsense, for every mode passes; only `None`/non-list returns and exceptions fail. The NameError regression is covered (an exception fails any test), but "builds an argv" is not — an empty argv launches nothing and would pass here while failing the sibling per-mode tests only for the modes those cover.
- **Fix:** Assert the returned list is non-empty, and for folder/file modes assert the supplied root/target value appears in it.
- **Justification:** Adds assertions to an existing test; no new scaffolding.
- **Benefit:** The registry-wide guard matches its stated claim, not just its crash case.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-122 — Real-socket recv-buffer test's only assertion cannot fail
- **Filed as:** PYTESTB-14 (pytestb.md)
- **Category:** 6
- **Locations:** tests/test_udpfs_config.py:272-279 (test_widen_on_a_real_socket)
- **Problem:** The test calls `udpfs._widen_recv_buffer` on a real UDP socket and asserts only `getsockopt(SO_RCVBUF) > 0`. The file's own `_FakeSock` docstring (same file) states the problem plainly: "a real socket always reports a positive buffer, which would let a no-op implementation pass." The fake-socket tests around it do pin the widen/no-shrink/best-effort logic properly, so this real-socket test adds only the illusion of integration coverage: replace `_widen_recv_buffer` with a no-op and it still passes.
- **Fix:** Assert the resulting buffer is at least the requested `DATA_RECV_BUFFER_BYTES` (allowing for the kernel's doubling/clamping by comparing against a pre-call baseline: post-call value must exceed the pre-call value when the pre-call value was smaller), or delete the test and let the fake-socket tests carry the logic.
- **Benefit:** The one real-socket test in the class becomes capable of failing.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-123 — FakeServices answers silent success to any PowerShell command it doesn't recognise
- **Filed as:** PYTESTB-15 (pytestb.md)
- **Category:** 6
- **Locations:** tests/test_smb2_take_445.py:73 (FakeServices.run), fall-through return at line 118
- **Problem:** `FakeServices.run` regex-matches production's PowerShell command strings for Get/Stop/Start-Service; any command matching none of the patterns falls through to `return subprocess.CompletedProcess(args, 0, "", "")` — exit 0, empty output. If production's take-445 code starts issuing a new or respelled command (a fourth verb, a renamed parameter the regexes miss), the fake reports success and the tests keep passing while the real service control would do something else entirely. The docstring discloses that service control is simulated; the silent-success default for *unrecognised* commands is the sharp edge.
- **Fix:** Make the fall-through raise AssertionError (or return a non-zero exit with stderr naming the unmatched command), so an unmatched command is loud in tests.
- **Benefit:** Production command drift breaks the test instead of being absorbed by the double.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-124 — Tests patch shared stdlib module objects, so a missed restore leaks into the whole discover run
- **Filed as:** PYTESTB-16 (pytestb.md)
- **Category:** 7
- **Locations:** tests/test_udpfs_live_discovery_handoff.py:239-242 and passim (`time.monotonic = lambda: fake_now`, restored by hand in finally); tests/test_udpfs_fallback_generation.py:47,51 (`patch.object(CORE.threading, "Thread")`, `patch.object(CORE.time, "sleep")`); tests/test_udpfs_stall_report.py:59,117,189,203 (`patch.object(UDPFS.os, "name", …)`, `patch.object(UDPFS.threading, "Thread", …)`); tests/test_udpfs_symlink_path_ops.py:374 (`patch.object(srv.os, "lstat", …)`)
- **Problem:** `CORE.threading`, `UDPFS.os`, `srv.os` etc. are not private copies — they are the process-wide stdlib `threading`/`os`/`time` modules, imported by reference into the production modules. Patching their attributes patches them for every module in the single `unittest discover` process. The manual `time.monotonic` assignment in the handoff tests is restored in `finally`, and the `mock.patch.object` uses are context-scoped, so this is fragility rather than an observed leak — but any future edit that returns early inside one of these regions, or an exception in a restore path, leaves `Thread`, `os.name`, `os.lstat` or `monotonic` replaced for every subsequently-run test file, producing failures (or worse, passes) far from the cause.
- **Fix:** Where production code reads the attribute through its own module namespace, patch it there (e.g. `patch.object(CORE, "time", fake)` is not possible for module attributes — instead patch the specific attribute on the production module object when it holds its own reference); where it doesn't, prefer injecting the fake at the server-object level as these files already do for sockets. At minimum, convert the manual `time.monotonic` assignments to `mock.patch.object(time, "monotonic", …)` context managers so restoration is structural.
- **Benefit:** One test file's fakes can no longer contaminate the other 83 files in the shared discover process.
- **Effort:** M · **Risk:** Medium · **Confidence:** Medium (READ)

### RE-SERV-125 — Five test files each exec a private copy of ps2servers_core.py under a different module name
- **Filed as:** PYTESTB-17 (pytestb.md)
- **Category:** 7
- **Locations:** tests/test_udpfs_fallback_generation.py:18 (`ps2servers_core_fallback`), tests/test_udpfs_inform_telemetry.py:13 (`ps2servers_core_inform_telemetry`), tests/test_udpfs_startup_diagnostics.py:16 (`core_startup_diagnostics`), tests/test_udpfs_live_discovery_handoff.py:23 (`ps2servers_core_handoff`), tests/test_udpfs_protocol_negotiation.py:14 (`ps2servers_core`)
- **Problem:** Each file loads `udpfs_server/ps2servers_core.py` fresh via `importlib.util.spec_from_file_location` under its own module name, creating up to five distinct module objects — with distinct `AutoUdpfsServer`/`Session` classes and module-level state — inside one discover process, none of them the `ps2servers_core` that other test files (and the launcher path) import normally. Within each file the copy is self-consistent, so tests pass; but class identity, `isinstance` reasoning, and any cross-file patching silently operate on different classes, and a reader cannot tell from a failure which copy misbehaved. It also quintuples import side effects of the core module per run.
- **Fix:** Import `ps2servers_core` normally (the directory is already put on `sys.path` by these files) and reserve spec-loading for the one case that genuinely needs an isolated copy, with a comment saying why.
- **Benefit:** One core module, one class identity, across the UDPFS test files; failures point at the code production actually imports.
- **Effort:** M · **Risk:** Medium · **Confidence:** Medium (READ/INFERRED)

---

### RE-SERV-126 — `_local_ipv4_addresses` runs three enumeration mechanisms to print one cosmetic warning line
- **Filed as:** PYSMB2-25 (pysmb2.md)
- **Category:** 3
- **Locations:** smb2_server/smb2_server.py:76-128 (getaddrinfo pass, then a POSIX `fcntl` SIOCGIFADDR per-interface ioctl pass, then a UDP connect-to-TEST-NET probe), consumed at smb2_server/smb2_server.py:131-147
- **Problem:** ~53 lines and three fallback strategies exist solely to fill in "Reachable on: …" in the open-share warning. The code itself concedes the list is best-effort and that "the --bind advice is the load-bearing half" — the addresses change nothing the operator does; the actionable content is the second line, which needs no enumeration at all. The ioctl pass is also the only `fcntl` use in the server, with its own platform guards to maintain.
- **Fix:** Keep the getaddrinfo pass and the --bind advice; delete the ioctl enumeration and the connect probe. If the list comes back empty, the warning loses nothing it could act on.
- **Benefit:** Removes a platform-specific syscall path and a network-stack probe from startup, for a banner line.
- **Effort:** S · **Risk:** Low · **Confidence:** Med (READ)

### RE-SERV-127 — The srvsvc pipe does not reassemble a PDU split across WRITEs (or across RPC fragments)
- **Filed as:** PYSMB2-28 (pysmb2.md)
- **Category:** 8
- **Locations:** smb2_srvsvc.py:134-137 (`SrvsvcPipe.write` hands each WRITE straight to `transact`), smb2_srvsvc.py:144-165 (`transact` requires one complete PDU per call: `frag_len > len(data)` is an error, and a REQUEST without FIRST+LAST fragment flags gets a fault)
- **Problem:** SMB pipe semantics let a client write a request in several WRITEs, and DCE/RPC lets it fragment one request across PDUs; this pipe implements neither — each WRITE must be a whole PDU, and each PDU must be a whole request. The comment at the fragment check frames a fragmented request as "a client this server does not understand", which is true of the clients tested (libsmb2, Explorer, smbclient all send small single writes), but it makes the one pipe the server has a partial implementation of its own transport: a conforming client that splits a bind or a request — legal on both layers — gets INVALID_PARAMETER / a fault instead of a share list.
- **Fix:** Buffer incoming bytes in `write` until a complete PDU (per its own frag_len) is present before calling `transact`; that covers the WRITE-split case with a few lines. Leave REQUEST-fragment reassembly unimplemented, but say so at the fault site as a known limit rather than a client error.
- **Benefit:** The pipe accepts the write patterns its transport permits, and its one real limit is documented where it triggers.
- **Effort:** M · **Risk:** Low · **Confidence:** Med (READ)

---

### RE-SERV-128 — `_optimal_chunk_size` computes an "optimum" that is always 1408

> **WITHDRAWN (Stage B):** Executing the actual method returns 1024 for totals 0/100/1024, 1280 for 1025/12800 and 1408 only for 20000/100000 — the audit missed the tie-break limb, so the value is not always 1408.
- **Filed as:** PYUDPFS-12 (pyudpfs.md)
- **Category:** 3
- **Locations:** `udpfs_server/udpfs_server.py:2746-2767` (definition), callers at `udpfs_server/udpfs_server.py:2968` and `udpfs_server/udpfs_server.py:3003`
- **Problem:** the candidate loop can never change its answer. `best` starts at chunk 1408 with `ceil(total/1408)` packets; the 1024- and 1280-byte candidates always need *more* packets, and the 1408 candidate ties on packets while its alignment (128) is not `>` the incumbent's 128. For every input, including 0, the method returns 1408 — which is already the named constant `MAX_DATA_PAYLOAD`. The loop, the `math.ceil` calls and the "DMA efficiency" framing are machinery around a constant. One external seam exists: `tests/test_session_summary.py:296` monkeypatches the method to force 1024-byte chunks (outside this scope; the fix must keep or relocate that seam).
- **Fix:** replace both call sites with `MAX_DATA_PAYLOAD` and delete the method, moving the test's patch point to the constant's consumer if the seam is still needed.
- **Benefit:** ~20 lines of misleading optimisation removed from the send path; chunk size becomes greppable as the protocol constant it is.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-129 — NFS is the only server without the shared `--session-summary` feature
- **Filed as:** PYSMALL-16 (pysmall.md)
- **Category:** 8
- **Locations:** `nfs4_server/nfs4_server.py:1045-1064` (`build_parser`: no `--session-summary`), `nfs4_server/nfs4_server.py:1067-1099` (`main`: no `SessionStats` wiring, no stats object on `Nfs4Server`); present in scope at `http_server/http_server.py:1155-1159,1183` and `udpbd_server/udpbd_server.py:400-402,443-449` (both via the shared `launcher.session_stats.SessionStats`)
- **Problem:** The session-summary mechanism is shared infrastructure ("shared by every server" is the HTTP import's own comment) and is wired into the HTTP and UDPBD entry points in this scope — grep confirms zero occurrences of `session` in `nfs4_server.py`. An operator running the launcher's session view gets per-console lines for the other protocols and a silent gap for NFS, with nothing in the NFS docs surface (parser help) saying the omission is deliberate. This is a half-finished rollout of an existing feature, not a request for a new one.
- **Fix:** Either wire NFS the same way the other two in-scope servers do (construct `SessionStats("nfs")` under the same flag and record connection/read/write events at the points `_serve`/`_op_read`/`_op_write` already identify), or state the exclusion explicitly in the parser help/README so the gap reads as a decision. The wiring option reuses the existing shared class and adds no new abstraction.
- **Justification:** The fix completes an already-built, already-shared mechanism in the one server that lacks it; no new subsystem is introduced.
- **Benefit:** Uniform per-console session reporting across all servers the launcher runs.
- **Effort:** M · **Risk:** Low · **Confidence:** High (READ, grep-verified absence)

---

### RE-SERV-130 — UDPFS plain file WRITE is never held to the length it declared, unlike the BWRITE path that was hardened for exactly this
- **Filed as:** GOPROTO-15 (goproto.md)
- **Category:** 8
- **Locations:** native/ps2servers-edge/internal/udpfs/operations.go:547-595 (`handleWriteRequest` reserves `make([]byte, 0, size)` from the declared size but stores it nowhere); native/ps2servers-edge/internal/udpfs/operations.go:634-643 (declared-length enforcement exists only when `WriteExpectedLen > 0`); native/ps2servers-edge/internal/udpfs/operations.go:653-663 (`completeWrite` routes on `WriteIsBlock` and only the block branch checks the length); native/ps2servers-edge/internal/udpfs/blockdev.go:301, 344-409 (`WriteExpectedLen` is set, and exact-length-checked, only for BWRITE)
- **Problem:** The block-write path was deliberately hardened so the delivered byte count must equal the declared count exactly — `completeBlockWrite`'s comment calls writing a different quantity than was validated "the same defect as the overflow bug in inRange". The plain file WRITE path never got the same treatment: the declared size in WRITE_REQUEST is used only as a buffer capacity hint, completion is driven purely by the client's per-chunk `totalChunks` claim, and `completeWrite` flushes and reports success for whatever arrived. A sequence that declares one size and delivers another (truncated, or padded past the declaration up to the transfer cap) is written and synced as a successful save. The feature is half-finished: the invariant exists in one of the two write paths that share the same chunk machinery.
- **Fix:** Store the WRITE_REQUEST declared size as the expected length for file writes too (the state field already exists) and let the existing `WriteExpectedLen` enforcement and a completion length check cover both paths.
- **Benefit:** Both write paths enforce the same declared-vs-delivered invariant the codebase already decided is required; a short or over-long save cannot be reported as a clean write.
- **Effort:** S · **Risk:** Low · **Confidence:** Med (READ)

### RE-SERV-131 — The GUI's theme, admin panel and log trimming live as a ~300-line monkeypatch shim in the entry point, and its copy of `_append_log` has already drifted from gui.py's original
- **Filed as:** LNCHA-09 (lncha.md)
- **Category:** 8
- **Locations:** launcher/main.py:107-409 (`_apply_gui_review_fixes`: wraps `ttk.Notebook`, `LauncherApp.__init__`, `LauncherApp._build`, replaces `LauncherApp._append_log`, injects the admin panel and the full ttk palette); launcher/main.py:393-406 (patched `append_log`); launcher/gui.py:3042-3053 (the original `_append_log` it replaces)
- **Problem:** This is a half-finished migration kept as runtime patching ("This stays as a runtime shim so release-risk remains low", main.py:112-113). The file's own comments record the cost: window sizing and the scrolling body *were* also patched here and had to be de-duplicated because "keeping a second copy here only gave the two a way to disagree" (main.py:130-145, 291-295). The log function is the remaining second copy, and it has already drifted by exactly one behaviour: the patched version scrolls only when the view was already at the bottom (`yview()[1] >= 0.99`), gui.py's original scrolls unconditionally — so the effective behaviour of the app depends on which copy a reader happens to open, and a fix applied to gui.py's version silently does nothing. The guard flag (`_ps2_gui_review_fixes_patched`) also means the patch set is invisible to anyone reading gui.py alone.
- **Fix:** Finish the migration the file already started for sizing: move `apply_theme`, the admin panel, `StyledNotebook` and the at-bottom `append_log` into gui.py (where the palette already has a single owner, launcher/theme.py), and delete `_apply_gui_review_fixes`.
- **Benefit:** One copy of each behaviour, in the file a maintainer edits; the drift class the comments describe stops recurring.
- **Effort:** M · **Risk:** Med · **Confidence:** High (READ)

### RE-SERV-132 — The owned runtime bypasses `AccountSession` entirely: hardcoded epoch 1 and fixed task IDs, while the session's begin/accept state machine runs only in tests
- **Filed as:** LNCHB-03 (lnchb.md)
- **Category:** 8
- **Locations:** launcher/ra_account_worker.py:271-416 (`AccountSession` with `begin_login`/`begin_resume`/`begin_import`/`begin_set`/`begin_start_session`/`begin_award`/`begin_ping`/`accept`), launcher/achievements.py:183-190 (production resolver constructs `SetContext(creds.username, 1, …)`, `AccountTask(1, 1, "set", …)` and `AccountTask(2, 1, "start_session", …)` with literal epoch/task IDs), launcher/achievements.py:232 (`service.bind_account(creds.username, 1)`), launcher/achievements.py:298,311 (submission tasks take `ctx.epoch`, which is the same literal 1)
- **Problem:** Script census: `begin_*`/`accept(` have zero production callers — every caller is in `tests/` (`test_ra_account_worker.py`, `test_ra_evaluator.py`, `test_ra_owned_service.py`). The only production use of the session object inside `OwnedService` is `logout` via `run_account` (launcher/achievements.py:254). Meanwhile the production path hardcodes the epoch the whole generation-checking design exists to vary: `AccountSession.logout()` increments its epoch (launcher/ra_account_worker.py, `logout`), and `OwnedService` syncs that epoch internally, but the outer runtime keeps minting epoch-1 contexts and tasks, so the generation guarantees documented in `ra_account_worker.py:1-8` and `ra_evaluated_receiver.py` do not actually govern the shipped integration — they govern a parallel, test-only one.
- **Fix:** Either wire `run_owned_runtime` through `AccountSession` (begin/accept on the owner thread, epoch taken from the session), or delete the unused begin/accept surface and document the direct-worker flow as the design. Do not keep both.
- **Justification:** The fix is a choice between wiring existing code and deleting it; no new mechanism is proposed.
- **Benefit:** One commit boundary instead of two; the epoch/cancellation guarantees either become real or stop being claimed.
- **Effort:** L · **Risk:** Med · **Confidence:** High (READ; caller census EXECUTED by script)

### RE-SERV-133 — Watch-profile vocabulary disagrees across three files: `orbit-v1.0.2` is accepted but never produced, `caduceus` is produced but rejected by the IPC validator
- **Filed as:** LNCHB-15 (lnchb.md)
- **Category:** 8
- **Locations:** launcher/ra_sets.py:156-158,200 (profiles `{"opl", "orbit-v1.0.2", "caduceus"}` accepted; orbit gets its own validator), launcher/ra_watchlist.py:60-73 (`validate_orbit_watchlist`), launcher/ra_resolver_process.py:27-29 (`valid_identification_result` accepts only `{"opl", "orbit-v1.0.2"}`), launcher/achievements.py:152 (production produces only `"caduceus"` or `"opl"`)
- **Problem:** Repo-wide grep: no code anywhere ever constructs an `IdentificationResult` with profile `orbit-v1.0.2` — the orbit validator, its 1216-byte ceiling logic and its IPC allowance are dead capability. Conversely, the one non-default profile production *does* produce, `caduceus`, is not in the IPC validator's set, so a caduceus-mode qualified result crossing `ProcessResolver` validation would be discarded as invalid. The two halves of the profile system were each extended without the other.
- **Fix:** Decide once: either add `caduceus` to the resolver validator and delete the orbit profile until a producer exists, or wire an orbit producer and keep both. Do not keep an unproducible profile and an unvalidatable one.
- **Benefit:** Profile handling becomes a single closed set that producer, validator and deriver all agree on.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ; producer census EXECUTED by grep)

### RE-SERV-134 — GUI behaviour is assembled by stacked monkey-patches, including a custom import-hook finder in `launcher/__init__.py`
- **Filed as:** LNCHC-01 (lnchc.md)
- **Category:** 3
- **Locations:** launcher/__init__.py:14-92; launcher/asset_skin.py:152-164; launcher/full_skin_controls.py:10-204; launcher/gui.py:439 (the `_build` being patched)
- **Problem:** Importing the `launcher` package installs a `sys.meta_path` finder + loader wrapper (`_GuiFinder`/`_GuiLoader`, __init__.py:58-92) whose only job is to intercept the future import of `launcher.gui` and replace `LauncherApp._build` with a wrapper that calls `asset_skin.install()` and `dependency_panel.add_panel()` *before* the original build. `asset_skin.install` then patches `ttk.Notebook.add` globally (asset_skin.py:42-56) and invokes `full_skin_controls.install`, which in turn replaces `ServerCard._build`, `ServerCard.reset_to_defaults/apply_page_settings/revert_to_defaults`, and `LauncherApp.apply_server_settings/revert_server_defaults` (full_skin_controls.py:10-204). The docstring in `__init__.py` itself concedes "The normal, boring implementation would be direct calls from the GUI build path." The result is that what the GUI actually does cannot be determined from `gui.py` alone, patch order is load-bearing (dependency panel and skin must run pre-build; tab icons post-build), every patcher needs its own `_ps2_*_patched` guard flag, and import tooling/freezers must tolerate a bespoke finder at position 0 of `meta_path` for the lifetime of the process.
- **Fix:** Delete the meta-path finder/loader from `__init__.py` entirely and make the two direct calls (`asset_skin.install(self, gui_module)` / `dependency_panel.add_panel(self, gui_module)`, or plain in-module equivalents) at the top of `LauncherApp._build` in gui.py; likewise call the `full_skin_controls` setup directly instead of via `asset_skin`. Keep the existing idempotence guards only where a genuine re-entry exists.
- **Benefit:** Removes ~78 lines of import machinery, one whole failure mode (finder interfering with other importers), and makes the build path readable in one file; no behaviour change intended.
- **Effort:** M · **Risk:** Med · **Confidence:** High (READ)

### RE-SERV-135 — `ps1_index.py` is a complete PS1 scanner with no consumer in the launcher
- **Filed as:** LNCHC-03 (lnchc.md)
- **Category:** 8
- **Locations:** launcher/ps1_index.py:1-147 (whole file; scanner at 88-147)
- **Problem:** The module implements a finished, bounded scan of POPSTARTER `POPS/*.VCD` files and Ember `EMBER/games/<name>/` folders, with launchability validation (`ember_name_problem`, 52-61) and a deliberately PS2-safe descriptor type (`Ps1Game`, 34-47). Grep over `launcher/*.py` shows no import or use of `ps1_index`/`Ps1Game` anywhere outside the module itself — its only consumers are tests, and the project's own capability matrix describes it as "not shown in the Library tab yet". Meanwhile PS1/VCD handling that *is* shipped goes through a different path (`game_library.MEDIA_TYPES["POPS"]`, game_library.py:19-21), so the Ember/POPSTARTER knowledge exists only in dead-ended code that must still be maintained and tested.
- **Fix:** Either surface it in the existing LIBRARY tab (one list, reusing `LibraryPanel`) or delete the module and its tests until the feature is scheduled. Do not keep a third, invisible index.
- **Benefit:** Removes 147 lines of unmaintainable-in-practice code (or, if surfaced, removes the duplication of PS1 knowledge between it and `game_library`).
- **Effort:** S (delete) / M (surface) · **Risk:** Low · **Confidence:** High (READ — grep-verified no launcher consumer)

### RE-SERV-136 — Three competing application-icon sources: shipped LOGO, a 246-line procedural icon generator, and a tray fallback badge
- **Filed as:** LNCHC-14 (lnchc.md)
- **Category:** 3
- **Locations:** launcher/app_icon.py:1-246; launcher/tray_linux.py:61-95; launcher/assets/theme/LOGO.png; launcher/gui.py:1523-1524
- **Problem:** `app_icon.py` exists, per its own docstring, "so the source tree does not need binary assets" — it rasterizes a "PS2" glyph icon pixel-by-pixel in pure Python (`_GLYPHS`, `_rgba_pixels`, `_draw_text`, `_ico_image`, `tk_photo` with per-pixel string building at 91-113) to produce the window/taskbar/EXE icon. But the tree *does* ship a binary logo (LOGO.png, used for the About tab and the Linux tray), and `tray_linux._icon_image` contains a *third* icon: a Pillow-drawn rounded-rectangle "PS2" badge used when LOGO.png is missing. The app therefore has three different visual identities depending on platform/surface, and maintains 246 lines of bitmap code — including a hand-built ICO encoder — to avoid using the asset it already ships.
- **Fix:** Pick one source: use LOGO.png for the Tk window icon and tray (both already have that code path), and keep only `write_ico`'s ICO container writing fed from the shipped image for the Windows/build consumers; delete the glyph rasterizer (`_GLYPHS`, `_rgba_pixels`, `_draw_square`, `_draw_text`, `tk_photo`'s pixel loop) and the tray fallback badge.
- **Benefit:** ~200 lines of pixel code gone; the icon the user sees is the same artwork on every surface.
- **Effort:** M · **Risk:** Med · **Confidence:** High (READ)

### RE-SERV-137 — The "shared index" is wrapped in two pass-through layers on top of `read_index`
- **Filed as:** LNCHC-15 (lnchc.md)
- **Category:** 3
- **Locations:** launcher/library_index.py:109-190; launcher/library_catalog.py:451-454; launcher/game_library.py:288-296
- **Problem:** `read_index(database, root, …)` is the whole feature, yet callers reach it through two additional named layers: `Catalog.indexed_games()` is a 4-line method whose body is only `return read_index(self.path, …)`, and `Library.shared_index()` is a 9-line wrapper whose only added behaviour is translating one `FileNotFoundError` case into a friendlier `ValueError`. The three names (`read_index` / `indexed_games` / `shared_index`) for one operation force every reader to check whether the layers differ, and the panel, the RA window, and tests each picked a different layer to call.
- **Fix:** Delete `Catalog.indexed_games` (its one production caller, library_panel.py:326, can call `read_index(db.path, …)` directly) and inline `shared_index`'s single error translation at its two call sites in library_gui.py, or keep only `shared_index` and delete the Catalog method — one wrapper maximum.
- **Benefit:** One obvious entry point for the index; ~13 lines and one layer of indirection gone.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-138 — conformance/integration/http_probe.py is orphaned: no CI step, test or runner references it
- **Filed as:** CIDOCS-09 (cidocs.md)
- **Category:** 8
- **Locations:** `conformance/integration/http_probe.py` (219 lines, complete socket-level OPL HTTP probe)
- **Problem:** Grep-verified across workflows, tests and docs: nothing invokes or imports this file — its only usage text is its own docstring. Every sibling is wired: `opl_http`, `riptopl_http`, `neutrino_smb`, `neutrino_nfs`, `libsmb2` run in `ci.yml`; `udpfs_probe`, `udpbd_probe`, `udpfs_parity` run in `edge.yml`; `ra_dependency/*` runs via `tests/test_ra_dependency.py` under `unittest discover`. HTTP is the one protocol whose conformance probe silently rots, in a repo whose README calls HTTP "experimental" — exactly where a live probe matters most.
- **Fix:** Either wire it into `ci.yml` alongside the other HTTP conformance runners (it takes `--host/--port` against a running server, like the Edge probes in edge.yml), or delete it. Keeping an unrunnable-by-CI probe is the worst of the three states.
- **Benefit:** HTTP wire behaviour is either actually gated or no longer appears to be.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ, grep-verified)

### RE-SERV-139 — The session-id minting line is duplicated inside `h_session_setup`
- **Filed as:** PYSMB2-24 (pysmb2.md)
- **Category:** 2
- **Locations:** smb2_server/smb2_server.py:863, smb2_server/smb2_server.py:915
- **Problem:** `conn.session_id = int.from_bytes(os.urandom(8), "little") or 1`, guarded by the same `if conn.session_id == 0`, appears verbatim in both the NTLM-NEGOTIATE branch and the AUTHENTICATE branch of the same function. It is the kind of line that gets fixed in one copy (e.g. as part of PYSMB2-06's logoff change) and not the other.
- **Fix:** Mint the id in one place — e.g. a tiny `Connection.ensure_session_id()` next to `add_handle`, or a single check after the branch dispatch in `h_session_setup` before the reply is built.
- **Benefit:** One definition of how a session gets its id; the two branches cannot drift.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-140 — The Transaction response envelope is built twice, once for TRANS2 and once open-coded in the RAP handler
- **Filed as:** PYSMB1-10 (pysmb1core.md)
- **Category:** 2
- **Locations:** smbv1_server/smbserver_opl.py:1128-1154 (`_build_trans2_reply`), smbv1_server/smbserver_opl.py:1344-1362 (the same 10-word header, 4-byte alignment padding, and offset arithmetic repeated in `h_transaction`)
- **Problem:** The fixed 10-word transaction response header, the `base = 32 + 1 + 20 + 2` computation, and both alignment pads are duplicated almost line-for-line. Any correction to the envelope layout (offsets, alignment) has to be made in two places and can silently drift — in a file whose comments repeatedly stress that exact response sizes are what keep OPL from retrying forever.
- **Fix:** Have `h_transaction` call the existing `_build_trans2_reply`-shaped builder (generalised only by the params/data it already takes), deleting the open-coded copy.
- **Benefit:** One envelope implementation; ~18 duplicated lines removed and one place to fix wire-layout bugs.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ — both constructions read in full)

### RE-SERV-141 — Compressed-image stem/extension matching is implemented twice, with subtly different rules
- **Filed as:** PYSMB1-11 (pysmb1core.md)
- **Category:** 2
- **Locations:** smbv1_server/smbserver_opl.py:303-336 (`Share.compressed_source`: listdir, stem match, first extension in `exts` order, re-resolve + containment check), smbv1_server/smbserver_opl.py:1156-1198 (`_list_dir`: listdir, stem match, best extension in `exts` order, real-`.iso`-wins rule)
- **Problem:** "Which `.chd`/`.cso` stands in for a given `.iso`, and in what preference order" is decided in two separate scans. They already differ in their surrounding rules (one re-checks containment of the real path, the other checks whether a real `.iso` name is taken), so a future format or ordering change made in one place leaves listing and opening disagreeing about which image a name refers to — the listing would advertise one file and the open would serve another, or none.
- **Fix:** Extract the shared scan (folder listing → stem → best extension in order) into one helper on `Share` that both call sites use, keeping each caller's own extra check (containment / real-iso-wins) at the call site.
- **Benefit:** Listing and opening can never disagree about image selection; the preference order lives in one place.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ — both implementations read in full)

### RE-SERV-142 — Three copy-pasted ID allocators, and FID wrap-around silently overwrites a live open file, leaking its handle
- **Filed as:** PYSMB1-12 (pysmb1core.md)
- **Category:** 2
- **Locations:** smbv1_server/smbserver_opl.py:489-505 (`alloc_fid` / `alloc_tid` / `alloc_sid`, three identical two-line bodies; `cleanup` closes only what remains in `conn.files`)
- **Problem:** The three allocators are the same increment-and-wrap logic with different floors, triplicated. Worse, none of them checks for collision with an ID still in use: `alloc_fid` cycles through 0x1000–0xFFFF, and on wrap the caller does `conn.files[fid] = of`, overwriting the entry for a file that may still be open — the overwritten `OpenFile` is no longer in the dict, so `cleanup()` never closes it and its OS handle leaks for the life of the connection. The same unchecked pattern applies to tree and search IDs and their dicts.
- **Fix:** One small allocator helper parameterised by floor, used by all three; and on FID allocation, skip (or close) any ID still present in `conn.files` before reissuing it.
- **Benefit:** Removes the triplication and closes a slow handle leak on very long sessions (the leak needs ~61k opens without closes on one connection, so it is a correctness fix, not an urgent one).
- **Effort:** S · **Risk:** Low · **Confidence:** Med (READ for the allocator/dict mechanics; INFERRED for the wrap-around leak, which follows directly from them but was not executed through a full wrap)

### RE-SERV-143 — The entry-point surface (parser + validation + compression precedence + server construction) is duplicated between Core and the standalone server, and again inside Core's own `main()`
- **Filed as:** PYSMB1-22 (pysmb1core.md)
- **Category:** 2
- **Locations:** udpfs_server/ps2servers_core.py:523-579 (`build_parser`) and 591-690 (`main`), udpfs_server/udpfs_server.py:3441-3600 (the standalone parser and `main`, near-identical in structure, including the identical four-step compression-precedence block at 3563-3575 vs Core's 601-608), udpfs_server/ps2servers_core.py:625-682 (the two server constructions inside Core's `main` itself)
- **Problem:** Core was supposed to be the standalone server plus negotiation; instead its CLI surface is a second copy of it. The two parsers already differ in help text, in which validations run, and in options present, and every shared option (ports, timeouts, limits, compression) must be changed twice. The duplication repeats at smaller scale inside Core's `main()`: the Modulo and Auto construction blocks share 17 keyword arguments (counted by script over the AST) and differ only in the class, two forced flags, and Auto's two extra arguments — so a new server option has to be threaded into two call sites in this one function, plus a third in the standalone file.
- **Fix:** In Core's `main()`, build one `kwargs` dict and construct once (the Modulo case then differs by class + two entries, per PYSMB1-17's resolution). Across files, have one parser builder own the shared options, with Core adding its three extra arguments — or accept the standalone file as the frozen upstream copy its header says it is, and stop treating the two parsers as peers by removing the shared-option definitions from one side.
- **Benefit:** Each config option gains exactly one definition per shipped entry point; the 17-argument construction stops being a drift hazard.
- **Effort:** M · **Risk:** Low · **Confidence:** High (READ — both parsers/mains read; the 17 shared kwargs are script-counted)

### RE-SERV-144 — DATA header parsing and payload-size computation are duplicated between `_route_data_via` and `_handle_data` (and parsed a third time by the base handler)
- **Filed as:** PYSMB1-23 (pysmb1core.md)
- **Category:** 2
- **Locations:** udpfs_server/ps2servers_core.py:360-383 (`_route_data_via`), udpfs_server/ps2servers_core.py:384-400 (`_handle_data`'s identical preamble)
- **Problem:** Both methods unpack `Header`, unpack the same `<I` data header, extract `header_words`/`data_bytes` with the same shifts, compute `payload_size` the same way, and apply the same oversize check — one to decide whether to queue the packet, the other to decide how to classify it, after which `super()._handle_data` parses it all again. The shifts and masks are protocol constants; a correction to the payload-size rule (or a bug in it) currently has three homes, two of them in this file.
- **Fix:** Parse once in `_route_data_via` and carry the parsed header/payload size in the queued item alongside the ingress it already carries, so `_handle_data` consumes the parse instead of repeating it. (The third parse, inside the base class, is the base file's business and is not required for this fix to pay off.)
- **Benefit:** One payload-size rule in Core; the queue item already exists as the place to carry per-packet facts.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ — both preambles read side by side)

### RE-SERV-145 — The session-stats attachment snippet is copy-pasted between Core and the SMB1 server instead of shared
- **Filed as:** PYSMB1-25 (pysmb1core.md)
- **Category:** 2
- **Locations:** udpfs_server/ps2servers_core.py:581-588 (`_attach_session_stats`), smbv1_server/smbserver_opl.py:1748-1754 (the same repo-root computation, `sys.path` insertion, lazy import, and construction, inlined in `main()`)
- **Problem:** Both shipped entry points attach `launcher.session_stats.SessionStats` with the identical dance — compute the repo root from `__file__`, append it to `sys.path` if the `launcher` package is there, import, construct with the server's key — written out twice, in the only two files in this audit's scope. (A third server, the standalone UDPFS `main`, does the same again.) If the launcher package moves or the attach contract changes, each copy drifts independently and the failure mode is a server that silently never emits SESSION lines.
- **Fix:** One tiny helper in one of the existing modules (Core's `_attach_session_stats` already has the right shape) imported by the other entry point, parameterised only by the stats key.
- **Benefit:** The attach contract lives in one place; both servers gain or lose the behaviour together.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ — both copies read in full)

---

### RE-SERV-146 — Compressed-header parsing is triplicated: the size probe, the three wrappers, and the limits module each parse the same headers, including the CD-CHD size correction twice
- **Filed as:** PYUDPFS-10 (pyudpfs.md)
- **Category:** 2
- **Locations:** `udpfs_server/udpfs_server.py:390-479` (`get_compressed_info`, incl. the CD-frame correction at lines 425-440), `udpfs_server/compressed_iso/chd.py:262-303` (the same CD predicate and correction in `ChdFileWrapper._parse_header`), `udpfs_server/compressed_iso/cso.py:54-89` and `udpfs_server/compressed_iso/zso.py:64-107` (wrapper header parses duplicating the probe's magic/size extraction), `udpfs_server/compressed_iso/limits.py:19-116` (a third parse of the same CSO/ZSO/CHD headers for inspection)
- **Problem:** the CD-CHD correction in `get_compressed_info` carries a comment that it must use "the same predicate as the CHD wrapper … so the size reported here can never disagree with what the read path extracts" — i.e. the duplication is load-bearing and maintained by hand across two files, with a third implementation of the header rules in `limits.py`. The file's own history (comments at `udpfs_server.py:78-113`) records a previous listing bug caused by exactly this kind of drift between the stat path and the open path. Every future format tweak (a codec id, a header field) must currently be made in two or three places or the listing size and the served size diverge.
- **Fix:** make `get_compressed_info` obtain its answer from the wrapper layer — open the wrapper, read `uncompressed_size`, close — or extract one shared header-parse helper the probe and the wrappers both call; delete the duplicated CD correction from one side. (The probe is on listing/stat paths, so keep it allocation-light: parse header fields only, as today, but from one implementation.)
- **Benefit:** one place defines what a format's size is; the class of bug the file already shipped once cannot recur by drift.
- **Effort:** M · **Risk:** Med · **Confidence:** High (READ)

### RE-SERV-147 — UDPBD `_handle_read`'s `is_raw` extent pre-check duplicates the bound both raw backends already enforce in `seek`
- **Filed as:** PYSMALL-05 (pysmall.md)
- **Category:** 2
- **Locations:** `udpbd_server/udpbd_server.py:208-209` (pre-check), `udpbd_server/udpbd_server.py:219-222` (the `seek(sector_nr, sector_count)` call immediately after); other side: `launcher/raw_storage.py:37-45` (`RawDevice.seek` raises `EINVAL` when the request end exceeds the device), `launcher/virtual_exfat.py:228-233` (`VirtualExfat.seek` raises `EINVAL` when the request is out of bounds)
- **Problem:** For every device where `is_raw` is true, the identical capacity check runs twice — once in the handler and once inside `seek`, which is called with the same `sector_count` a few lines later and raises the same `EINVAL`. The write path (`udpbd_server/udpbd_server.py:250-255`) proves the pre-check is unnecessary: it has none and relies on `seek` raising, converting it to a `-1` reply. Two copies of one bound are how bounds drift apart (and the file device's missing bound in PYSMALL-01 is the other half of that drift).
- **Fix:** Delete the pre-check at lines 208-209; if PYSMALL-01's unconditional check is adopted in the handlers, it subsumes this for the file device too.
- **Benefit:** One implementation of the raw-device extent bound instead of two; ~3 lines removed.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-148 — NFS `_op_close` and `_op_open_confirm` are byte-identical bodies under two names
- **Filed as:** PYSMALL-13 (pysmall.md)
- **Category:** 2
- **Locations:** `nfs4_server/nfs4_server.py:880-884` (`_op_open_confirm`), `nfs4_server/nfs4_server.py:886-890` (`_op_close`)
- **Problem:** Both operations parse a stateid, bump its seqid by one, check a current filehandle exists, and return the result — the same five lines twice, registered as separate entries in the ops table (lines 365-379). Any change to the fabricated-stateid behaviour (see PYSMALL-10) must currently be made in two places, and the two copies can drift into subtly different state handling for two operations clients use in sequence.
- **Fix:** Extract the shared five lines into one private helper (e.g. `_seqid_reply(r, s)`) and have both op methods call it; the ops-table entries stay separate.
- **Benefit:** One implementation of stateid-seqid handling; ~5 lines removed.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-149 — `_stop_on_sigterm` is duplicated between the HTTP and NFS entry points (and has already drifted)
- **Filed as:** PYSMALL-14 (pysmall.md)
- **Category:** 2
- **Locations:** `http_server/http_server.py:1220-1237`, `nfs4_server/nfs4_server.py:1101-1113`
- **Problem:** The executable code of the two copies is identical (verified by diff); only the HTTP copy has acquired a docstring the NFS copy lacks — the copies are already drifting in the small, which is how the behavioural notes in that docstring (SIGTERM semantics per platform) end up documented for one server and not the other. Both servers already reach outside their own directory for shared code (HTTP imports `launcher.session_stats` at line 47; NFS inserts `smb2_server` into `sys.path` at lines 40-43), so "standalone file" is not the reason for the duplication.
- **Fix:** Move the function into a module both already can import (the `launcher` package HTTP already depends on) and import it in both; keep the docstring with the single copy.
- **Benefit:** One SIGTERM-handling implementation and one place for its platform caveats; ~13 lines removed.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ, diff-verified)

### RE-SERV-150 — CSO/ZSO index-extent validation is duplicated verbatim between `openImage` and `SizeOf`
- **Filed as:** GOPROTO-09 (goproto.md)
- **Category:** 2
- **Locations:** native/ps2servers-edge/internal/compression/image.go:198-202; native/ps2servers-edge/internal/compression/image.go:330-333
- **Problem:** The block-count computation and the `blocks > 1<<28 || headerSize+(blocks+1)*4 > fileSize` extent check, including the identical error string, exist in two copies. `parseHeader`'s own comment (image.go:238-241) says it was factored out "so that SizeOf and Open cannot drift apart on what counts as a valid image" — but only the 24-byte prologue half was factored; the index-extent half, which is the half that decides whether a corrupt header is rejected, was left duplicated. A future tightening (or loosening) applied to one copy silently re-creates exactly the probe-accepts-what-the-opener-rejects drift the comment warns about.
- **Fix:** Extract the block-count/extent check into one small function used by both `openImage` and `SizeOf`.
- **Benefit:** One definition of a valid image header; the stated anti-drift invariant becomes true for the whole header, not half of it. Net code shrinks.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-151 — The CHDv5 header is parsed twice, by two parsers with different validation rules
- **Filed as:** GOPROTO-10 (goproto.md)
- **Category:** 2
- **Locations:** native/ps2servers-edge/internal/compression/chd.go:31-95 (`parseCHDHeader`); native/ps2servers-edge/internal/compression/chd_cuebin.go:193-230 (`readCDTrackTableOpt` header half)
- **Problem:** Both functions open the same file, read the same 124-byte header, check the same magic/version/header-size fields, and decode the same logical-size/hunk/unit fields — then validate them differently (e.g. the track-table parser requires `logical % 2448 == 0` and `unit == 2448`; `parseCHDHeader` requires neither, and derives CD-ness from the unit and codec IDs instead). `parseCHDHeader` additionally calls `exactCDISOSize`, which re-opens the file and runs the second parser. Two parsers for one fixed header is a standing drift hazard in a format-validation path: a bounds fix (hunk ceiling, overflow check) made in one is not made in the other, and the two already disagree on what a valid header is.
- **Fix:** Parse the 124-byte header once into a shared raw-header struct; let each caller apply only its own additional constraints (CD geometry for the track table, codec/unit interpretation for the image opener) on top.
- **Benefit:** One place owns CHD header bounds/overflow validation; callers keep their differing semantics explicitly instead of by parser divergence. Net code shrinks.
- **Effort:** M · **Risk:** Med · **Confidence:** High (READ)

### RE-SERV-152 — SMB hand-rolls `itoa` instead of using `strconv.Itoa`
- **Filed as:** GOPROTO-12 (goproto.md)
- **Category:** 2
- **Locations:** native/ps2servers-edge/internal/smb/server.go:244-256 (definition); native/ps2servers-edge/internal/smb/server.go:166 (sole caller)
- **Problem:** `itoa` is a 13-line reimplementation of `strconv.Itoa` with one caller, a narrower contract (it silently mishandles negative input, returning an empty string, where the stdlib does not), and an 8-byte buffer sized by hand for a value the caller has already range-checked. It is pure maintenance surface duplicating the standard library in a protocol package.
- **Fix:** Delete `itoa` and call `strconv.Itoa` at the single call site.
- **Benefit:** Less code, stdlib-correct behaviour for every input, one less bespoke helper to audit.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-153 — SMB's FID/TID/SID allocators are three copies of one function
- **Filed as:** GOPROTO-13 (goproto.md)
- **Category:** 2
- **Locations:** native/ps2servers-edge/internal/smb/server.go:425-447
- **Problem:** `allocFID`, `allocTID` and `allocSID` are byte-for-byte the same increment-and-wrap logic, differing only in the field they touch and the wrap value (0x1000 vs 0x0001). Any fix to the wrap behaviour (e.g. skipping IDs still in use — all three currently wrap onto live IDs without checking) has to be made three times, and the three can drift.
- **Fix:** Replace the three with one allocator helper parameterised by the counter and its wrap value, or a tiny allocator type used for all three counters.
- **Benefit:** One implementation of ID allocation for the connection; net code shrinks and wrap fixes land once.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-154 — UCI boolean parsing is duplicated 16 times inline and disagrees with the one use of the shared helper
- **Filed as:** GOCORE-04 (gocore.md)
- **Category:** 2
- **Locations:** `native/ps2servers-edge/internal/webui/config_uci.go:14-22` (`uciBool`); `native/ps2servers-edge/internal/webui/config_uci.go:89` (its only use in `Load`); `native/ps2servers-edge/internal/webui/config_uci.go:63-163` (16 inline occurrences of `val == "1" || val == "true"`, counted by script)
- **Problem:** `uciBackend.Load` parses booleans two ways in the same switch. `uciBool` accepts `1/on/true/yes/enabled` (case-insensitive) and is used exactly once, for `main.udp_batch` (line 89). Every other boolean field — all of main/smb/udpbd/webui, lines 63–163 — uses the narrower inline test `val == "1" || val == "true"`. A hand-written or third-party-generated UCI file (the schema in schema.go exists precisely for config generators) that writes e.g. `verbose 'on'` or `enabled 'yes'` is honoured for `udp_batch` and silently read as false for the other fifteen-plus booleans. `Save` only ever writes `1`/`0`, which masks the divergence until a config arrives from anywhere else.
- **Fix:** Replace all 16 inline tests with the existing `uciBool(val)` call. Pure deletion of repeated logic; no new helper needed because the helper already exists.
- **Benefit:** One boolean semantics for the whole UCI file, ~16 repeated expressions removed, and a class of "setting ignored" misreads eliminated.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-155 — The four subcommands copy-paste their flag scaffolding — and the copy in `runWebUI` already lost the `--log-format` validation
- **Filed as:** GOCORE-05 (gocore.md)
- **Category:** 2
- **Locations:** `native/ps2servers-edge/cmd/ps2servers-edge/main.go:161-163` + `:193-195` (udpfs); `native/ps2servers-edge/cmd/ps2servers-edge/main.go:304-307` + `:321-322` (udpbd); `native/ps2servers-edge/cmd/ps2servers-edge/main.go:365-368` + `:380-381` (smb); `native/ps2servers-edge/cmd/ps2servers-edge/main.go:489-491` (webui, no validation follows); logger/signal setup repeated at `native/ps2servers-edge/cmd/ps2servers-edge/main.go:241-249`, `:325-335`, `:384-431`, `:518-541`
- **Problem:** Each of `main` (udpfs), `runUDPBD`, `runSMB` and `runWebUI` re-declares the same `--log-format`/`--verbose`/`--quiet` flags (and the first three plus webui variously re-declare `--session-summary`, `--bind`-family env defaults), re-creates the logger, and re-creates the signal context. The copies have already drifted: udpfs, udpbd and smb each validate `--log-format` must be `text` or `json` and exit 2 otherwise; `runWebUI` declares the flag at line 489 and never validates it, so `--log-format garbage` is silently treated as text by `logging.New` (any non-`"json"` value is text) in the webui subcommand only. Script counts: 4 `flag.NewFlagSet`, 4 `log-format` definitions, 3 validations, 4 `signal.NotifyContext`, 4 `edgelog.New`.
- **Fix:** Extract one small helper that registers the common logging flags on a `FlagSet` and returns the validated logger inputs (rejecting a bad `--log-format` there, once), and call it from all four subcommands. This is the one place a shared helper removes code overall: four declaration blocks and three validation blocks collapse to one of each.
- **Justification:** The fix adds one helper function; it is justified because the identical logic exists in 4 places, the copies have already produced a behavioural bug (missing validation), and the net line count goes down.
- **Benefit:** One flag contract for all subcommands; the webui `--log-format` gap closes as a side effect of deduplication rather than as a fifth copy of the check.
- **Effort:** M · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-156 — Port-445 takeover is two divergent full implementations (SMBv1 and SMBv2 servers), with the PowerShell-resolution helper copied a third time in the launcher
- **Filed as:** LNCHA-08 (lncha.md)
- **Category:** 2
- **Locations:** smbv1_server/smbserver_opl.py:1546-1648 (`_take_445`: stop LanmanServer, wait, restore); smb2_server/smb2_server.py:1479-1652 (`take_445` + `listen_after_take_445`: same LanmanServer stop/wait/restore, plus srv/srv2/srvnet driver handling and foreign-dependent checks the SMBv1 copy lacks); absolute-PowerShell-path resolution at launcher/windows_setup.py:57-71, smbv1_server/smbserver_opl.py:1553-1560, smb2_server/smb2_server.py:1496-1502
- **Problem:** One mechanism — "pause Windows file sharing, bind 445, restore on exit" — exists as two independent implementations that have already drifted: the SMBv2 copy learned to pause the SMB kernel drivers and to refuse when foreign services depend on LanmanServer; the SMBv1 copy did not, so the two servers behave differently for the same user-facing "Take port 445" checkbox built from near-identical launcher fields. Any future fix to the takeover/restore dance (the riskiest code in either server — it stops a Windows service) must be made twice, and the drift shows it already hasn't been. The small security-critical helper both need (absolute PowerShell path) is likewise copied per file instead of shared, which is how LNCHA-06's bare-name call site happened elsewhere.
- **Fix:** Move `take_445`/restore and the PowerShell resolver into one shared module both servers import (precedent exists: udpbd_server imports `launcher.raw_storage`/`launcher.virtual_exfat`, http_server imports `launcher.session_stats`), keeping SMBv2's stricter driver/dependent behaviour as the single behaviour.
- **Benefit:** One takeover implementation to verify on hardware; SMBv1 gains the driver/dependent safety the SMBv2 copy already proved necessary.
- **Effort:** L · **Risk:** Med · **Confidence:** High (READ)

### RE-SERV-157 — SIGTERM→KeyboardInterrupt stop handling is installed three times: once by serve.py for every launcher child, and again inside the HTTP and NFS servers
- **Filed as:** LNCHA-11 (lncha.md)
- **Category:** 2
- **Locations:** launcher/serve.py:79-102 (`install_stop_handlers`, called for every `--serve` child at launcher/serve.py:158); http_server/http_server.py:1220-1233 (`_stop_on_sigterm`, near-verbatim); nfs4_server/nfs4_server.py:1101-1110 (`_stop_on_sigterm`, near-verbatim)
- **Problem:** In launcher mode the same mechanism is installed twice for HTTP and NFS: serve.py installs its handler first, then the server's `main()` overwrites it with its own identical one (serve.py additionally covers SIGBREAK, which the per-server copies omit — so the effective handler set differs by entry point). The per-server copies exist for standalone runs, but nothing states that, and a change to the launcher's stop semantics (e.g. a new signal) must be mirrored by hand into each server that copied the snippet.
- **Fix:** Keep the standalone copies only where a server genuinely runs outside the launcher, and note that in one line at each copy; or have HTTP/NFS import the installer from the launcher package as other servers already import `launcher.session_stats` / `launcher.raw_storage`.
- **Benefit:** Stop semantics defined once; SIGBREAK coverage stops depending on which entry point started the server.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-158 — The stop-pipe environment name is a duplicated string literal in the two files whose agreement the whole cooperative-stop mechanism depends on
- **Filed as:** LNCHA-12 (lncha.md)
- **Category:** 2
- **Locations:** launcher/process.py:19 (`STOP_PIPE_ENV = "PS2SERVERS_STOP_PIPE"`); launcher/serve.py:72 (`STOP_PIPE_ENV = "PS2SERVERS_STOP_PIPE"  # launcher/process.py sets it`)
- **Problem:** The producer (process.py sets the env var) and the consumer (serve.py pops it to decide whether to arm the stdin watcher) each define the constant independently. If either string is edited, cooperative stop silently stops working — no error, servers just go back to being hard-killed on Windows, which is the failure mode the mechanism exists to prevent (see LNCHA-03). serve.py already imports from the launcher package at call time (`from .servers import REGISTRY`), so a shared definition costs nothing.
- **Fix:** Define `STOP_PIPE_ENV` once (in process.py, as the comment already claims) and import it in serve.py.
- **Benefit:** The stop contract cannot drift apart silently.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-159 — Three `_parse_port` implementations with three different failure semantics
- **Filed as:** LNCHA-13 (lncha.md)
- **Category:** 2
- **Locations:** launcher/windows_setup.py:115-121 (unparseable → caller-supplied default); launcher/status_client.py:68-75 (unparseable/empty/False → 0, i.e. "do not query"); udpfs_server/udpfs_server.py:3378-3383 (unparseable or out-of-range → raise ValueError)
- **Problem:** The same-named helper means three different things depending on which file a maintainer is reading: fall back, give up, or fail hard. The two launcher copies both accept hex via `int(str(v), 0)` and both swallow the same exceptions, yet return different sentinels — a reader moving code between the firewall path and the status-poll path (both consume the same saved `port` values) can silently change behaviour. This is the same vocabulary-drift pattern as LNCHA-04/05 at function scale.
- **Fix:** One launcher-side parser with an explicit `default` parameter (windows_setup's shape) used by status_client too; the UDPFS server's raising variant stays, renamed or commented as the strict CLI parser it is.
- **Benefit:** Saved port values are interpreted one way in the launcher; the strict server parser stops masquerading as the same helper.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ, census by script: exactly 3 definitions repo-wide)

### RE-SERV-160 — Interface-address parsing is implemented four times across netinfo and directlink, including two different functions with the same name
- **Filed as:** LNCHA-14 (lncha.md)
- **Category:** 2
- **Locations:** launcher/netinfo.py:28-41 (`_parse_linux_ip_addr`); launcher/netinfo.py:44-60 (`_parse_macos_ifconfig` → returns a list of IPs); launcher/netinfo.py:172-183 and 185-198 (`get_all_adapters_info` re-parses the same `ip -o -4 addr show` and `ifconfig` output inline instead of calling the two helpers above it in the same file); launcher/directlink.py:356-... (`_parse_linux_adapters`, a third Linux parser, for `ip -j` JSON); launcher/directlink.py:460-476 (`_parse_macos_ifconfig` — same name as netinfo's, different signature and return: `(status, [{ip, prefix, origin}])`)
- **Problem:** The virtual-interface exclusion list and the inet-line grammar live in several parsers that must agree on what counts as a usable LAN address; the same-name pair is a standing trap — importing the wrong `_parse_macos_ifconfig` (or assuming the two are interchangeable in a refactor) yields a type error at best and wrong adapter data at worst. netinfo's inline copies already differ in detail from its own helpers (the inline macOS branch tracks the current interface header itself; the helper does the same — duplicated logic, two places to fix a parsing edge).
- **Fix:** Have `get_all_adapters_info` build on netinfo's own `_parse_linux_ip_addr`/`_parse_macos_ifconfig` output (extended to carry the interface name), and rename directlink's parser (e.g. `_parse_macos_ifconfig_block`) to end the name collision.
- **Benefit:** One inet-line grammar per OS in the launcher; no same-name/different-shape functions across sibling modules.
- **Effort:** M · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-161 — DHCP option-TLV parsing is written twice inside directlink.py because `parse_packet` cannot be reused for replies
- **Filed as:** LNCHA-15 (lncha.md)
- **Category:** 2
- **Locations:** launcher/directlink.py:1100-1125 (`parse_packet`: BOOTP unpack + option TLV loop at 1110-1120); launcher/directlink.py:1272-1315 (`_foreign_offer_server_id`: hand-rolled BOOTP header checks + a second copy of the same TLV loop at 1294-1304)
- **Problem:** `_foreign_offer_server_id` re-implements the header validation and the option walk (pad/end handling, length slicing) that `parse_packet` already does, solely because `parse_packet` rejects `op != 1` replies. Two copies of a wire-format parser in one file will drift — a bounds or option-handling fix applied to the request parser (the one tests exercise through `handle_packet`) does not reach the foreign-server detector, which is a safety path (it is what refuses to run on a real network).
- **Fix:** Extract the option-TLV walk into one small helper used by both, and let `parse_packet` take the expected op (or return the raw op) so the reply path reuses the header validation too.
- **Benefit:** The safety-critical probe parses packets with the same, tested code as the responder.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-162 — Frozen-executable probing is duplicated between config.py and servers.py
- **Filed as:** LNCHA-16 (lncha.md)
- **Category:** 2
- **Locations:** launcher/config.py:20-29 (`_app_dir`: probes `NUITKA_ONEFILE_BINARY`, then `sys.argv[0]`, existence-checks each, falls back to `sys.executable`); launcher/servers.py:22-33 (`frozen_self_exe`: the same probe sequence for the same purpose)
- **Problem:** Both functions answer "where is the real program file in a frozen build" by walking the same candidate list with the same existence check — the subtle part (Nuitka onefile's inner exe vs the outer binary) is exactly the part that must not diverge, because config placement and server re-exec must agree on the program's folder. elevate.py, windows_setup.py and main.py all import the servers.py copy; config.py is the lone re-implementation.
- **Fix:** Have config.py derive its app dir from `servers.frozen_self_exe()` (servers.py imports nothing from the launcher package, so no cycle), or move `frozen_self_exe` into config.py and import it from servers.py.
- **Benefit:** One definition of the frozen-program location for both config storage and re-exec.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-163 — SMBV2 and SMBV3 share one mutable `fields` list object
- **Filed as:** LNCHA-17 (lncha.md)
- **Category:** 2
- **Locations:** launcher/servers.py:211 (`_SMB2_FIELDS = [...]`); launcher/servers.py:441 and 457 (`fields=_SMB2_FIELDS` for both SMBV2 and SMBV3 — executed: `SMBV2.fields is SMBV3.fields is _SMB2_FIELDS` is True)
- **Problem:** Sharing the definition list is intentional (one protocol, two dialects), but sharing the *same list object* means any in-place mutation — a future field addition for SMBv3 only, a test that appends, GUI code that sorts or filters in place — silently changes the other server's card too. The two ServerDefs already diverge where they should (separate `_build_argv` wrappers, separate labels/blurbs); the fields list is the one place the sharing is by reference rather than by construction.
- **Fix:** Pass a copy per server (`fields=list(_SMB2_FIELDS)`), keeping the single source list.
- **Benefit:** SMBv2/SMBv3 cards can diverge later without a shared-mutation surprise; no behaviour change today.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED — identity check)

### RE-SERV-164 — DPAPI unprotect is implemented twice, with divergent hygiene
- **Filed as:** LNCHB-07 (lnchb.md)
- **Category:** 2
- **Locations:** launcher/ra_credentials.py:31-57 (`_dpapi`: sets argtypes/restype, validates output size against `LIMIT`, zeroes both the source buffer and the native output buffer before `LocalFree`, raises on failure), launcher/caduceus.py:55-90 (`read_secret`: re-implements the same `CryptUnprotectData` call inline; zeroes/frees only the result buffer, never zeroes its source copy, and returns `""` on every failure)
- **Problem:** Same platform primitive, two implementations, different guarantees: the Caduceus copy silently conflates "file missing", "DPAPI failure" and "empty secret" into `""` (callers then report a generic sign-in problem), and skips the source-buffer wipe and size validation the owned store treats as mandatory. Fixes to one copy (e.g. a buffer-handling correction) will not reach the other.
- **Fix:** Have `caduceus.read_secret` call the existing unprotect helper from `ra_credentials` (exposed for decrypt-only use) and translate its exception to `""` at that one call site if the silent behaviour is still wanted.
- **Benefit:** Net code removal; one audited DPAPI path for all credential files.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-165 — Account-identity / token validation re-implemented at eight sites with divergent rules
- **Filed as:** LNCHB-08 (lnchb.md)
- **Category:** 2
- **Locations:** launcher/ra_account.py:14-20, launcher/ra_credentials.py:24-28 (`Credentials.__post_init__`), launcher/ra_session_client.py:58-66, launcher/ra_set_client.py:15-24 (username check omits the `\0\r\n` rejection the otherwise-identical check in `ra_session_client.py` has), launcher/ra_sets.py:50-60 (`SetContext`: account check omits control-character rejection), launcher/ra_account_worker.py:36-40 (`AccountTask`: username may be empty and is length-checked differently), launcher/ra_evaluated_receiver.py:44-46, launcher/ra_owned_service.py:139-143
- **Problem:** Script census found eight separate spellings of "username ≤ 64, token ≤ 4096" with three different rule sets (with/without control-character rejection, with/without non-empty username). Each layer currently catches most of what the laxer layers miss, so this is not a demonstrated bypass — it is eight places to keep in sync, and the divergence is already visible (set-client and context accept usernames the session client rejects, so the same bad value fails at different depths with different errors).
- **Fix:** One shared validator in `ra_credentials` (where `Credentials` already defines the canonical rule) called by the clients and `SetContext`; keep the extra per-operation checks (e.g. login requires a password) local.
- **Justification:** Sharing removes code overall: eight near-identical blocks become one plus call sites.
- **Benefit:** A future rule change (e.g. RA username charset) lands once; error behaviour stops depending on which layer is hit first.
- **Effort:** M · **Risk:** Low · **Confidence:** High (READ; census EXECUTED by script)

### RE-SERV-166 — Achievement-definition validation duplicated four times, and `award_achievement` bypasses the shared response decoder
- **Filed as:** LNCHB-09 (lnchb.md)
- **Category:** 2
- **Locations:** launcher/ra_sets.py:130-145 (`qualify_set` definition loop), launcher/ra_account_worker.py:138-152 (`valid_account_result` re-validates the same definitions), launcher/ra_resolver_process.py:38-60 (`valid_identification_result` re-validates them a third time, near-verbatim), launcher/ra_evaluator.py:62-73 (`Evaluator.__init__` validates them a fourth time), launcher/ra_session_client.py:117-145 (`award_achievement` hand-rolls `json.loads` + `Success` checking instead of the `decode_response` used by its own `start_session`/`ping` in the same class)
- **Problem:** The "ID in 1..2³²−1, expression 1..16384 chars, no NUL, ASCII, total ≤ 4 MiB, no duplicates" rule exists in four copies (script census on the shared constants), and the award path opts out of the one decoder that rejects duplicate JSON keys and non-dict responses (`ra_sets.decode_response` with `_unique_object`) — it accepts duplicate-key responses and coerces scores with bare `int(...)` on unvalidated values. Any bound change must be made four times and can silently desynchronise the IPC validators from the producer, causing valid sets to be dropped at the process boundary as generic failures.
- **Fix:** Keep validation at the trust boundaries that need it (IPC receive, native activation) but generate both from one function in `ra_sets`; switch `award_achievement` to `decode_response` and validate score fields the way `AwardResult.__post_init__` already does.
- **Benefit:** Bounds cannot drift apart; the award response gets the same duplicate-key/size discipline as every other RA response.
- **Effort:** M · **Risk:** Low · **Confidence:** High (READ; census EXECUTED by script)

### RE-SERV-167 — Five different title-sanitising implementations across the RA files
- **Filed as:** LNCHB-10 (lnchb.md)
- **Category:** 2
- **Locations:** launcher/ra_protocol.py:109-114 (`clean_title`: NFKD, strip combining, ASCII-only, limit 63), launcher/ra_receiver.py:118-121 (the same NFKD/combining/ASCII logic inlined again in `Receiver.complete`), launcher/caduceus.py:35-39 (`clean`: same logic, caller-supplied limit), launcher/ra_notifications.py:9-10 (`safe_title`: whitespace-collapse only, limit 96, keeps non-ASCII and control-adjacent characters the others strip), launcher/ra_overview.py:18-19 (`short`: whitespace-collapse, caller limit)
- **Problem:** Three copies of the wire-facing ASCII cleaner and two different display cleaners, with different limits and different character rules. Cross-file comparisons depend on this: `ra_overview.view_model` and `ra_session.SessionTracker` both compare titles produced through `safe_title`, while the console-facing notice path uses `clean_title` — a title that round-trips differently through the two families (accents, non-ASCII) compares unequal or displays differently per surface, and a fix to the wire cleaner must be made in three places.
- **Fix:** Keep exactly two functions — the wire cleaner in `ra_protocol` (used by `ra_receiver` and `caduceus`, which already import from the RA package) and the display cleaner in `ra_notifications` — and delete the inline copies.
- **Benefit:** Net removal; title equality across overview/session/notifications stops depending on which copy ran.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ; census EXECUTED by script)

### RE-SERV-168 — The loopback `/state` fetch is duplicated verbatim in the overview and the session poller
- **Filed as:** LNCHB-11 (lnchb.md)
- **Category:** 2
- **Locations:** launcher/ra_overview.py:94-100 (`fetch_engine_state`), launcher/ra_session.py:133-144 (`SessionPoller._fetch`)
- **Problem:** Both functions build `account_url() + "state"`, `urlopen` with a 2 s timeout, read 1 MiB + 1, reject over 1 MiB, and `json.loads` — identical except the error-message wording. They will drift (one already says "reply exceeds 1 MiB", the other "reply exceeded limit"), and any change to the state endpoint (port file, size cap, auth) must be made twice in the two components the GUI runs simultaneously.
- **Fix:** Keep `fetch_engine_state` in `ra_overview` (or move it to `ra_session`) and call it from the other.
- **Benefit:** One /state client; trivial net removal.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-169 — Two parallel library subsystems: two catalogue databases, two folder preferences, two importers, two backups, two UIs
- **Filed as:** LNCHC-04 (lnchc.md)
- **Category:** 2
- **Locations:** launcher/game_library.py:58-85,132-193,227-375; launcher/library_catalog.py:105-176,467-642; launcher/library_gui.py:14-446; launcher/library_panel.py:58-561; launcher/gui.py:641-648,1416-1421
- **Problem:** The launcher ships two game libraries over the same games folders. (a) Databases: `game_library.Library` creates `<config>/library/achievement-catalog.sqlite3` (game_library.py:63) while `library_catalog.Catalog` creates `<config>/library/catalog.sqlite3` (library_catalog.py:105-106) — two SQLite catalogues in the same directory, with different `games` schemas. (b) Folder preference: `LibraryPanel` persists the games root to a private text file `library/games_root.txt` (library_panel.py:69,87-91,175-187), while `LibraryWindow` persists it in the shared config as `game_library_folder` (library_gui.py:27-28,133-143) — the two windows can silently point at different roots. (c) Import: `Catalog.import_image/import_images` (library_catalog.py:467-512, `copyfileobj` into an `xb` file) duplicates `Library.install/transfer` (game_library.py:299-375, temp file + validation + `publish_new`) with different safety rules and different supported kinds (CD/DVD only vs CD/DVD/POPS). (d) Backup: `Library.backup` (game_library.py:178-193) duplicates `Catalog.backup_sqlite` (library_catalog.py:619-642), and `Library.import_catalog`'s automatic pre-import backup duplicates it a third time in intent. (e) UI: the LIBRARY tab (`LibraryPanel`, gui.py:1416-1421) and the RetroAchievements "Manage game library…" window (`LibraryWindow`, gui.py:641-648) both offer browse/scan/list/import/backup/cover-repair/guide over overlapping data.
- **Fix:** Pick one catalogue as the catalogue: keep `library_catalog.Catalog` (it is the one the LIBRARY tab and the shared index use) and reduce `game_library.Library` to its genuinely distinct job — the RetroAchievements hash/compatibility store (`image_status`, `Compatibility`) — deleting its `games` table CRUD, `import_catalog`, `backup`, and cover-repair catalogue role; store the games root in one place (the shared config key) for both windows.
- **Benefit:** One schema, one root preference, one import path with one set of safety rules, one backup implementation; eliminates the whole class of "the two windows disagree" bugs.
- **Effort:** L · **Risk:** Med · **Confidence:** High (READ)

### RE-SERV-170 — Three setup guides and two first-run mechanisms; the standalone LibraryPanel guide is unreachable in the shipped app
- **Filed as:** LNCHC-05 (lnchc.md)
- **Category:** 2
- **Locations:** launcher/setup_guide.py:9-119; launcher/library_panel.py:16-30,82-85,509-561; launcher/library_gui.py:416-424; launcher/gui.py:1661-1694,3539-3541
- **Problem:** Setup guidance exists three times with three different texts: the six-step `setup_guide.STEPS`/`SetupGuide` window, the five-step `library_panel.GUIDE` with its own hand-rolled Previous/Next window (library_panel.py:513-561), and a third, unrelated prose summary hardcoded in `LibraryWindow.guide()` as a messagebox string (library_gui.py:416-424). Worse, the LibraryPanel guide window is dead in production: `LibraryPanel._guide()` delegates to `app._open_ps2_setup_guide` whenever that attribute exists (library_panel.py:513-519), and the panel's only instantiation is inside `LauncherApp` (gui.py:1416-1421), which always defines that method (gui.py:1685-1694) — so the 49-line standalone window and its `GUIDE` tuple can only execute in tests that pass a stub app. First-run state is likewise doubled: the panel tracks completion in a marker file `library/first_run_complete` (library_panel.py:70,509-512,548-556) while the app tracks it as `ps2_setup_guide_status` in the config (gui.py:1677-1682,3539-3541), and both layers independently decide to auto-show a guide on first run (library_panel.py:82-85 vs gui.py:1661-1675).
- **Fix:** Delete `library_panel.GUIDE`, the standalone guide window in `_guide()`, the `guide_marker`/`_show_first_run_guide` machinery, and `LibraryWindow.guide()`'s prose; every "Setup guide" button calls the one `SetupGuide`, and first-run state lives only in `ps2_setup_guide_status`.
- **Benefit:** ~90 lines and one entire guide text gone; users can no longer be shown two different "first run" guides with different step counts.
- **Effort:** M · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-171 — Four separate games-folder scanners/indexers over the same trees
- **Filed as:** LNCHC-06 (lnchc.md)
- **Category:** 2
- **Locations:** launcher/library_catalog.py:266-368 (`Catalog.scan_detailed`); launcher/game_library.py:227-286 (`Library.installed`); launcher/library_index.py:109-190 (`read_index`); launcher/ps1_index.py:88-147 (`scan`)
- **Problem:** The same question — "which game images are under this root?" — is answered by four independent implementations with four different contracts: `scan_detailed` walks the tree itself (bounded, link-rejecting, media inferred from a top-level CD/DVD ancestor); `Library.installed` walks only the top level of CD/DVD/POPS and optionally overlays an index snapshot; `read_index` does not walk at all but re-stats and re-validates every catalogue row; `ps1_index.scan` walks POPS/EMBER with its own link/case rules. Each has its own limits (100,000 / unbounded-with-cancel / 10,000 / 10,000), its own result type (`ScanResult` / list-of-dicts / `IndexSnapshot` / `(games, problems)`), and its own notion of media/kind/layout, so behaviour differs by which window the user opened (e.g. a nested image is UNKNOWN-media via one path, invisible via another, "nested path" via a third).
- **Fix:** Keep `scan_detailed` as the single tree walker that populates the catalogue, and `read_index` as the single read path out of it; make `Library.installed` a thin filter over `read_index` output (plus its POPS top-level case) instead of a walker, and fold `ps1_index` in or out per LNCHC-03.
- **Benefit:** One traversal/link/depth policy to audit for safety; scan results become identical regardless of entry point.
- **Effort:** L · **Risk:** Med · **Confidence:** High (READ)

### RE-SERV-172 — Two independent ISO9660 directory parsers (PS2 `disc_identity` vs PS1 `ps1_vcd`)
- **Filed as:** LNCHC-08 (lnchc.md)
- **Category:** 2
- **Locations:** launcher/disc_identity.py:66-185 (`_IsoReader.root/find/extent/inspect`); launcher/ps1_vcd.py:20-152 (`VcdImage.root/extent/find/path_extent`)
- **Problem:** Both modules hand-roll the same ISO9660 work — locate the primary volume descriptor at sector 16, parse the root directory record, walk directory records matching a filename, extract LBA/size extents, then find SYSTEM.CNF and follow its BOOT/BOOT2 line — with divergent strictness: `disc_identity` verifies both-endian fields, rejects multi-extent/interleaved records, budgets reads/records, and detects ambiguous duplicates; `ps1_vcd` reads little-endian only, takes the first name match, splits on `;` instead of validating the version, and has different size limits (`MAX_DIRECTORY`/`MAX_BOOT` vs `MAX_DIRECTORY_BYTES`/`MAX_CNF_BYTES`). A parsing fix or a malformed-image lesson learned in one parser does not exist in the other. (The sector-mapping difference is real — VCD sectors are 2352-byte raw with a 0x100000 header — but that is a `read(offset,length)` adapter difference, not a reason to duplicate record walking.)
- **Fix:** Extract nothing new as a framework: move the directory-record walking (`root`/`find`/`extent` over a `read(offset,length)` callable) into `disc_identity`'s reader shape and have `VcdImage` supply only its raw-sector `read`; keep each module's own SYSTEM.CNF/BOOT semantics and limits as parameters already present at the call sites.
- **Justification:** The shared helper is justified only because the identical record-walking logic exists in exactly these 2 places and sharing it removes one full copy (~60 lines) overall.
- **Benefit:** One ISO9660 parser to harden; PS1 hashing inherits the duplicate/ambiguity and byte-order checks the PS2 inspector already has.
- **Effort:** M · **Risk:** Med · **Confidence:** High (READ)

### RE-SERV-173 — The PS2 disc-serial pattern is re-expressed five times, in five files, with five slightly different rules
- **Filed as:** LNCHC-09 (lnchc.md)
- **Category:** 2
- **Locations:** launcher/art_fetch.py:32; launcher/disc_identity.py:21-22; launcher/game_library.py:163; launcher/library_catalog.py:30; launcher/ps1_index.py:30
- **Problem:** Grep for the serial pattern finds: `art_fetch._OPL_ID` (`^([A-Z]{4})_(\d{3})\.(\d{2})$`), `disc_identity.SERIAL` (unanchored fullmatch use), `disc_identity.FILENAME_SERIAL` (prefix with lookahead), an inline `re.fullmatch(r"[A-Z]{4}_\d{3}\.\d{2}", serial)` inside `Library.repair_cover` (game_library.py:163), `library_catalog.SERIAL_PREFIX` (prefix *stripper* with a trailing-separator requirement), and `ps1_index._PS1_NAME_ID` (dot optional). The PS1 variant legitimately differs, but the four PS2 expressions are the same concept — canonical `XXXX_NNN.NN` — re-derived per module, so they drift independently (e.g. `repair_cover` accepts only the `_` form while `filename_serial` accepts `_`/`-`; `SERIAL_PREFIX` requires a trailing separator the others do not). Each consumer then re-implements the `_`↔`-` conversion too (`serial_forms`, art_fetch.py:56-62; manual `.replace("-", "_")` at library_catalog.py:225 and art_fetch.py:158).
- **Fix:** Keep one canonical definition where it already lives (`disc_identity`: `SERIAL`/`FILENAME_SERIAL`/`filename_serial`) and import it in `art_fetch` and `game_library` instead of re-writing the pattern; leave `SERIAL_PREFIX` (a title-stripping operation, not a validation) and the PS1 pattern alone.
- **Benefit:** Serial acceptance becomes consistent across cover repair, art download, and identity inspection; ~3 pattern copies and the duplicate conversions gone.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ — census by grep, quoted above)

### RE-SERV-174 — Console artwork conversion is implemented twice, with different palette rules and different write paths
- **Filed as:** LNCHC-10 (lnchc.md)
- **Category:** 2
- **Locations:** launcher/game_library.py:132-176 (`Library.repair_cover`); launcher/library_catalog.py:120-135 (`_artwork_palette_image`),545-578 (`Catalog.write_art`)
- **Problem:** Both code paths turn a downloaded/chosen image into the 8-bit palette PNG RiptOPL requires, and both even carry the same explanatory comment about FASTOCTREE/RGBA — but they disagree: `repair_cover` always converts to RGBA, thumbnails to 512×512, and quantizes with FASTOCTREE, while `_artwork_palette_image` uses FASTOCTREE only when real transparency is present and MEDIANCUT on RGB otherwise. They also write differently: `repair_cover` saves via a temp file + `os.replace` for the library cover but temp file + `publish_new` (hardlink) for the ART copy, while `write_art` writes with `xb` directly. The same source image therefore produces different console artwork depending on whether the user repaired it from the RA window or imported/repaired it from the LIBRARY tab, and size limits differ too (16 MB / 16 MP in `repair_cover`; 10,000 px / 50 MP in `write_art`).
- **Fix:** Make `repair_cover` call `Catalog.write_art`'s conversion (i.e. `_artwork_palette_image`) for both its outputs and keep only its download/size gate; delete its inline quantize block.
- **Benefit:** One palette policy, one set of image limits, one write protocol for all console artwork.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-175 — Two incompatible "has this file changed?" stamps for the same images
- **Filed as:** LNCHC-11 (lnchc.md)
- **Category:** 2
- **Locations:** launcher/game_library.py:256,477; launcher/disc_identity.py:37-46; launcher/library_index.py:165-176
- **Problem:** Change detection for a game image is formatted two ways: `game_library` builds `"{size}:{mtime_ns}:{ctime_ns}"` inline in both `installed()` (256) and `Compatibility.check()` (477) and stores it in `image_status.stamp`, while `disc_identity.fingerprint()` builds `"{dev}:{ino}:{size}:{mtime_ns}[:{ctime_ns}]"` (ctime omitted on Windows, deliberately) and the catalogue/index store *that* in `disc_identity.fingerprint`. `library_index` then emits the game_library format again as `IndexedGame.stamp` (165-176). The two formats disagree on Windows ctime semantics — the exact platform subtlety `fingerprint()`'s comment says it exists to handle — so the RA cache and the identity cache invalidate on different events for the same file.
- **Fix:** Use `disc_identity.fingerprint()` in the two `game_library` sites and for `IndexedGame.stamp`; delete both inline format strings. (Existing stored stamps simply miss once and are rewritten.)
- **Benefit:** One invalidation rule, including the documented Windows ctime behaviour, for every cache that tracks an image.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-176 — Desktop release build duplicated across release.yml and release-on-main.yml; the copies have already drifted
- **Filed as:** CIDOCS-07 (cidocs.md)
- **Category:** 2
- **Locations:** `.github/workflows/release.yml` (606 lines) and `.github/workflows/release-on-main.yml` (489 lines): same 5-target desktop matrix, libchdr build, portable build/stage, source-archive and checksum logic — 90 identical non-trivial lines by script comparison. Drift proof: `release-on-main.yml:333-334` emits an AppImage row in its download table, but that workflow contains no AppImage build step at all (release.yml has the full AppImage chain); conversely release.yml's `verify-version` job and Windows desktop-startup check have no counterpart in release-on-main.yml
- **Problem:** The repo already solved this exact problem for Edge — `edge-build.yml`'s header says the reusable workflow exists so callers "cannot drift apart" — but the larger desktop build was left as two hand-copies, and the predicted drift is observable now (a download-table row for an artifact one workflow can never produce; version verification present on tagged releases, absent on the rolling releases users actually download day-to-day).
- **Fix:** Extract the desktop build matrix into a reusable workflow the way `edge-build.yml` was, with the two callers keeping only their publish/prune differences.
- **Benefit:** One place to fix build bugs; rolling and tagged releases get identical gating (including the version check).
- **Effort:** L · **Risk:** Med · **Confidence:** High (READ)

### RE-SERV-177 — Two parallel documentation systems (34 Markdown + 22 HTML) cover the same topics with no generator between them
- **Filed as:** CIDOCS-11 (cidocs.md)
- **Category:** 2
- **Locations:** `docs/*.md` (34 tracked) vs `docs/*.html` (22 tracked), overlapping pairs including EDITIONS/editions, EDGE/edge, SMB/smb, NFS/nfs, HTTP/http, OPENWRT/openwrt, PROTOCOL-COMPATIBILITY/protocol-compatibility, antivirus-transparency/antivirus; `tools/build_docs_index.py` only indexes the HTML into the (correctly drift-gated) `docs/data/search-index.json` — nothing generates HTML from MD or checks the pair's agreement
- **Problem:** Git history shows both copies edited in the same documentation commits (e.g. the NFS/SMB2 docs pass touched both), i.e. every behaviour change is written twice by hand, in two formats, with no consistency gate comparable to `tests/test_edge_manual.py` (which pins the one manual that ships in Edge archives). The Edge defaults audit above is the cost made concrete: claims had to be checked in four places, and the false ones survived in the places without a pinning test.
- **Fix:** Designate one source per topic (Markdown for repo/operator docs is the natural survivor) and generate or link the site pages from it; where a pair must both exist, extend the existing manual-pinning test pattern to the pair's load-bearing claims (ports, defaults, edition scope).
- **Benefit:** Behaviour changes update one text; the class of stale-claim findings above stops recurring.
- **Effort:** L · **Risk:** Med · **Confidence:** High (READ, census by script)

### RE-SERV-178 — Dead code with zero callers anywhere in the repo
- **Filed as:** PYSMB2-23 (pysmb2.md)
- **Category:** 1
- **Locations:** smb2_crypto.py:186-189 (`native_cmac_name`), smb2_wire.py:120 (`STATUS_PENDING`), smb2_wire.py:181 (`FILE_SUPERSEDED`), smb2_wire.py:200 (`GLOBAL_CAP_DFS`), smb2_srvsvc.py:28 (`PTYPE_BIND_NAK`)
- **Problem:** A repo-wide grep census (production code and tests) finds exactly one reference to each of these: its own definition. `native_cmac_name` even carries a "For logs" docstring, but no log call uses it — the one place that would want it (startup, to say which CMAC backend is in force) never calls it. The four constants are protocol values no code path emits or compares. Each is small; together they are the kind of inventory that makes a reader check whether a status or packet type is handled when it is not. (Checked and *not* dead, for contrast: `Share.relative`, `Share.resolve(must_exist=…)`, `blob_timestamp` and `lmv2_response` have no production callers either, but tests exercise them, so they are test surface, not dead code.)
- **Fix:** Delete the five definitions. If the CMAC backend name is worth having, call it in the startup banner instead of keeping it for a caller that does not exist.
- **Benefit:** Five fewer symbols a reader must chase to discover nothing uses them.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ — grep census, counted not estimated)

### RE-SERV-179 — No native CMAC backend is probed on macOS, so signed SMB3 there runs on the pure-Python AES
- **Filed as:** PYSMB2-26 (pysmb2.md)
- **Category:** 5
- **Locations:** smb2_crypto.py:173-184 (`_native_cmac` probes win32 → bcrypt, linux → OpenSSL, nothing else), smb2_crypto.py:15-22 (module docstring: pure-Python AES is "under a megabyte per second, and an SMB 3.x signature covers every byte of every READ reply")
- **Problem:** On macOS the backend probe returns None by construction (the comment explains loading Apple's libcrypto aborts the process), so every signed SMB3 message is CMAC'd by the pure-Python AES at the throughput the docstring itself calls a crawl. Windows and Linux get OS crypto; macOS silently gets a ~100× slower signing path with no startup note saying so.
- **Fix:** Either probe a macOS-native route that does not load libcrypto (e.g. CommonCrypto via ctypes), or state the limitation where the operator will see it — the startup banner that already reports dialect and shares — so a slow macOS SMB3 server is a known property, not a mystery.
- **Justification:** (only if fix ADDS) The cost is quantified in the module's own docstring and falls on the server's stated purpose (copying games through Explorer); the banner-only variant adds one line and no mechanism at all.
- **Benefit:** Signed SMB3 on macOS is either fast or honestly labelled slow at startup.
- **Effort:** M · **Risk:** Low · **Confidence:** Med (READ)

### RE-SERV-180 — FIND_FIRST2 / FIND_NEXT2 return exactly one directory entry per round trip
- **Filed as:** PYSMB1-05 (pysmb1core.md)
- **Category:** 5
- **Locations:** smbv1_server/smbserver_opl.py:1200-1256 (`h_trans2_find_first2`: `SearchCount` packed as 1, one `_dir_entry_104` record), smbv1_server/smbserver_opl.py:1258-1275 (`h_trans2_find_next2`: same, one record per call)
- **Problem:** A directory listing costs one SMB request/response per file, plus one per-entry `os.stat` inside `_dir_entry_104`. A games folder with a few hundred entries — the normal case for this server — needs hundreds of sequential round trips over a PS2 network link to enumerate, where the protocol and the negotiated 64 KiB buffer allow many level-0x104 records per response. The full entry list is already materialised in `_list_dir` and stored in `conn.searches`; only the reply construction limits it to one.
- **Fix:** Pack as many entries as fit in the response buffer per FIND_FIRST2/FIND_NEXT2 reply, set `SearchCount` and `EndOfSearch` accordingly, and advance the stored cursor by the number actually sent.
- **Benefit:** Directory listing time drops from O(entries) round trips to O(entries ÷ entries-per-buffer), the single most common operation this server performs.
- **Effort:** M · **Risk:** Med (reply layout is hardware-validated; wants a console re-test) · **Confidence:** High (READ — both handlers pack a count of 1 and a single record)

### RE-SERV-181 — WRITE_ANDX re-opens the file from disk on every write, and the open file's cached size is never updated by writes
- **Filed as:** PYSMB1-13 (pysmb1core.md)
- **Category:** 5
- **Locations:** smbv1_server/smbserver_opl.py:1012-1045 (`h_write_andx`: `with open(of.path, "r+b") as w:` per request), smbv1_server/smbserver_opl.py:360-385 (`OpenFile.__init__` snapshots `self.size` once at open)
- **Problem:** Every WRITE_ANDX pays an open+close of the target path instead of writing through state already held for the FID, on the path VMC/config saves exercise repeatedly. Separately, because writes bypass the `OpenFile`, its `size` stays at whatever it was when the file was opened: after a write extends the file, `record_read`'s expected-length computation (which uses `of.size`) misjudges subsequent reads of the grown region, producing wrong short-read diagnostics for exactly the read-back-after-write pattern this server was already bitten by once.
- **Fix:** Keep a writable handle (or a lazily opened one) on `OpenFile` for files opened on a writable share and write through it, updating `of.size` to at least `offset + len(payload)` after each write.
- **Benefit:** Fewer syscalls per write on the save path, and read diagnostics that reflect the file as written.
- **Effort:** M · **Risk:** Med (touches the deliberately unbuffered read design; the read handle must stay unbuffered or be flushed coherently) · **Confidence:** High (READ — per-write open and one-time size snapshot are both explicit in the code)

### RE-SERV-182 — `Share.resolve`'s comment promises a share-prefix strip the code does not perform
- **Filed as:** PYSMB1-14 (pysmb1core.md)
- **Category:** 7
- **Locations:** smbv1_server/smbserver_opl.py:280-301 (`resolve`: comment at line 297, "# Drop a leading \"share/\" if the client included it", immediately above code that does no such thing)
- **Problem:** The comment describes a normalisation step that does not exist: a client path of the form `\games\DVD\file.iso` on share `games` resolves to `<root>/games/DVD/file.iso` and misses, while the comment leads a reader to believe it resolves to `<root>/DVD/file.iso`. Either the behaviour was removed and the comment survived, or the comment describes intent that was never implemented — in a security-sensitive path function whose other comments are detailed and accurate, a false one is a trap for the next editor.
- **Fix:** Delete the stale comment, or implement the strip if a real client needs it (that is a behaviour change and wants a console test; deleting the comment does not).
- **Benefit:** The path-boundary function documents only what it actually does.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ — comment and the full body of `resolve` read together)

---

### RE-SERV-183 — `classify_profile` contains an unreachable third branch that restates the first
- **Filed as:** PYSMB1-15 (pysmb1core.md)
- **Category:** 1
- **Locations:** udpfs_server/ps2servers_core.py:71-88 (`classify_profile`)
- **Problem:** The third test, `if first_data_sequence == modulo_sequence: return PROFILE_MODULO`, can only be reached when the first test (same condition, plus a side condition) and the second both failed. Brute-forced over the entire input space (all 4096×4096 discovery/first-data sequence pairs, executed): zero inputs reach the third branch with its condition true — when `first == (discovery+1) & 0xFFF` and the first branch's side condition fails, both sequences are 0, which would require the modulo sequence to be 0 rather than 1. The branch is dead, and its presence makes a 12-bit negotiation rule that hardware depends on look like it has a case it does not have.
- **Fix:** Delete the third branch; the first two tests plus the fall-through already cover the space (verified exhaustively).
- **Benefit:** The profile rule becomes exactly as large as the behaviour it implements; no reader has to re-derive which case the dead branch was meant to catch.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED — exhaustive over the full 2²⁴ input space)

### RE-SERV-184 — Two names are imported from `udpfs_server` and never used
- **Filed as:** PYSMB1-16 (pysmb1core.md)
- **Category:** 1
- **Locations:** udpfs_server/ps2servers_core.py:18-48 (import list; `MAX_SEND_RETRIES` at line 24, `_widen_send_buffer` at line 46)
- **Problem:** A word-count census of the file (scripted, not estimated) shows each of `MAX_SEND_RETRIES` and `_widen_send_buffer` occurs exactly once in `ps2servers_core.py` — on its own import line. Every other name in the 31-name import list occurs at least twice. Dead imports in a from-import of private helpers (`_widen_send_buffer` is underscore-private to the base module) misstate what Core actually depends on, in a file whose packaging history (noted in `build/build.py` and the packaging tests) already includes a breakage caused by this module's import surface being misunderstood.
- **Fix:** Remove the two names from the import list.
- **Benefit:** The dependency surface of the shipped entry point is exactly what it uses.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED — scripted occurrence census over the file)

### RE-SERV-185 — `main()`'s Modulo fast path bypasses `AutoUdpfsServer`, leaving Auto's own Modulo handling unreachable on the shipped path
- **Filed as:** PYSMB1-17 (pysmb1core.md)
- **Category:** 1
- **Locations:** udpfs_server/ps2servers_core.py:625-652 (`main()`: `protocol_mode == modulo` constructs a plain `UdpfsServer` and returns), udpfs_server/ps2servers_core.py:105-120 (`AutoUdpfsServer.__init__` Modulo kwargs handling), udpfs_server/ps2servers_core.py:305-335 (`_handle_discovery`'s dedicated Modulo branch, including its duplicate-discovery suppression via `sess._last_disc`, an attribute `_init_compat` never initialises)
- **Problem:** There are two complete Modulo implementations: the base server's (`modulo_compat=True`, used by `main()` whenever Modulo is forced — which is also what the launcher's "Enforce Modulo mode" checkbox produces) and Auto's (its `__init__` forcing plus the `_handle_discovery` Modulo branch with its own duplicate-probe logic). Because `main()` never constructs `AutoUdpfsServer` with `protocol_mode="modulo"`, the second implementation cannot execute on any shipped launch path — only a programmatic construction or a test can reach it. It still has to be read, reviewed, and kept consistent with the override contract the tests pin, and its uninitialised `_last_disc` attribute is a latent trap for anyone who does reach it.
- **Fix:** Pick one path: either route Modulo through `AutoUdpfsServer` like every other mode (deleting the `main()` special case) or delete Auto's Modulo branch and `__init__` forcing and let the base server own Modulo outright. The second is the smaller deletion.
- **Benefit:** One Modulo implementation to validate against hardware instead of two that can drift; the override surface the contract test pins shrinks.
- **Effort:** M · **Risk:** Med (Modulo behaviour is hardware-sensitive; whichever path survives needs a console check) · **Confidence:** High (READ — construction paths in `main()`, `__init__`, and the discovery branch all read; no other production construction of `AutoUdpfsServer` exists in the tree, grep-verified)

### RE-SERV-186 — A thread is spawned for every fallback timer, one per discovery, although at most one can ever act
- **Filed as:** PYSMB1-24 (pysmb1core.md)
- **Category:** 5
- **Locations:** udpfs_server/ps2servers_core.py:242-265 (`_schedule_fallback`: one daemon `threading.Thread` per call, sleeping `fallback_interval`), udpfs_server/ps2servers_core.py:340-358 (`_handle_discovery` calls it on every non-streaming auto-mode discovery)
- **Problem:** Consoles broadcast discovery repeatedly while looking for a server, and every such packet in auto mode starts a new thread that sleeps 0.25 s and then, in all but the latest generation, checks the guards and exits having done nothing. The generation/profile/first-data guards make the waste safe but not free: thread creation churn proportional to discovery broadcast rate, for a timer that has exactly one useful firing per handshake.
- **Fix:** Keep a single pending timer per session (cancel/replace on re-schedule, or record a deadline the receive loop checks during its existing 1-second `select` wakeup — the loop already wakes every second for the idle sweep).
- **Benefit:** No per-packet thread creation on the discovery path; the fallback mechanism's cost stops scaling with broadcast rate.
- **Effort:** S · **Risk:** Low · **Confidence:** Med (READ for the spawn-per-call structure; INFERRED for the broadcast-rate cost, which depends on client behaviour the file's own comments describe)

### RE-SERV-187 — `tx_start_seq` is write-only dead state
- **Filed as:** PYUDPFS-11 (pyudpfs.md)
- **Category:** 1
- **Locations:** `udpfs_server/udpfs_server.py:845` (session-proxy property), `udpfs_server/udpfs_server.py:1690`, `udpfs_server/udpfs_server.py:2966`, `udpfs_server/udpfs_server.py:3001`, `udpfs_server/udpfs_server.py:3208` (`Session.__init__` field)
- **Problem:** grep census of the whole file: five writes, zero reads. Nothing ever consumes the value; it is bookkeeping maintained in the two hottest send paths (`_send_raw_data`, `_send_raw_data_with_header`) for no reader. (One more assignment exists in `ps2servers_core.py:151` — outside this scope, flagged for that worker; deleting the field here requires dropping that line too.)
- **Fix:** delete the property, the `Session` field, and the three assignments in this file.
- **Benefit:** less per-session state to reason about in the retransmit logic, where unused sequence fields are actively misleading.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ, grep-verified)

### RE-SERV-188 — Selftest blind spots: skipped sections still print ALL PASSED on POSIX, and no in-scope selftest exercises a single write opcode or hostile wire input
- **Filed as:** PYUDPFS-17 (pyudpfs.md)
- **Category:** 7
- **Locations:** `udpfs_server/pathguard_selftest.py:190-192` (UNC section prints "skipped" and returns), `udpfs_server/pathguard_selftest.py:237` (unconditional `ALL UDPFS PATH-GUARD TESTS PASSED`), `udpfs_server/pathguard_selftest.py:155-185` (separator-root test uses `/` as the root on POSIX, where every absolute path is trivially "inside"), `udpfs_server/multiclient_selftest.py` (whole file), `udpfs_server/compression_selftest.py` (whole file)
- **Problem:** the three selftests are substantive — they assert exact bytes, sizes, errno values and gating behaviour, they run in CI, and other suites import them as fixture/client libraries; pathguard and compression were EXECUTED green in `/tmp` during this audit (multiclient is BLOCKED-ENV here, see header). Two blind spots remain. (1) On POSIX — the CI platform — the UNC-root and Windows-reinterpreted-name sections skip silently and the separator-root test degenerates (root `/` makes the containment assertions vacuous), yet the run still ends with ALL PASSED; the Windows-only containment cases are exactly the ones guarding the field-reported UNC regression the file's docstring leads with. (2) Coverage census by grep: **zero** mentions of `BWRITE`/`WRITE_REQ`/`WRITE_DATA` across all three selftests — the entire write path (WRITE_REQ/WRITE_DATA/BWRITE) has no selftest coverage, and none of the hostile-input cases behind PYUDPFS-01/02/03/06/07/08 (outside-symlink GETSTAT, out-of-range BWRITE, lying chunk header, write-only-handle READ, crafted compressed headers) is exercised anywhere in scope. Handler-level write tests do exist in the main `tests/` tree (another scope), so this is a gap in the *selftest* layer specifically — the layer a user is told to run standalone.
- **Fix:** print `SKIPPED` sections in the final summary (and don't count them toward ALL PASSED on POSIX); add handler-level cases to `multiclient_selftest.py`'s Driver-style harness or a small fourth section in `compression_selftest.py` for the six hostile cases above — they are all in-process and need no UDP.
- **Benefit:** a green selftest run means the same thing on every platform, and the write path gets the same regression net the read path already has.
- **Effort:** M · **Risk:** Low · **Confidence:** High (READ + EXECUTED for the green runs and the grep census)

---

### RE-SERV-189 — NFS `_Handles` grows without bound for the life of the server; every neighbouring table has a cap, this one has none
- **Filed as:** PYSMALL-11 (pysmall.md)
- **Category:** 4
- **Locations:** `nfs4_server/nfs4_server.py:261-316` (`_Handles`: `paths`/`numbers` only ever shrink via `removed`/`renamed`), `nfs4_server/nfs4_server.py:276-284` (`handle()` allocates a new number per distinct path); contrast caps in the same file: `nfs4_server/nfs4_server.py:54` (`MAX_CONNECTIONS = 32`), `nfs4_server/nfs4_server.py:820-821` (dir cache cleared at 64), `nfs4_server/nfs4_server.py:868-869` (client table evicts at 256)
- **Problem:** Every distinct path a client ever GETFHs (or READs — see PYSMALL-15) permanently consumes an entry in both dicts until that path is removed or renamed *through this server*. EXECUTED: browsing 30 files via PUTROOTFH+LOOKUP+GETFH left 31 entries (30 files + root) that nothing will ever reclaim. A long-running server browsed by file managers over a large library accumulates one entry per file ever touched — small per entry, but unbounded by design in a codebase that demonstrably caps every comparable structure.
- **Fix:** Give `_Handles` the same treatment as the client table: cap the table (e.g. evict the oldest numbers past a limit; their handles become STALE, which clients already handle by re-looking-up), reusing the eviction pattern at lines 868-869 rather than inventing a new one.
- **Benefit:** Handle-table memory becomes a bounded constant like every other long-lived table in the server.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED — growth; the unbounded-by-design conclusion is READ)

### RE-SERV-190 — NFS: one global generation counter means any single write invalidates every connection's read handles and every cached directory listing
- **Filed as:** PYSMALL-12 (pysmall.md)
- **Category:** 5
- **Locations:** `nfs4_server/nfs4_server.py:683-687` (`_changed` bumps the single `self.generation`), `nfs4_server/nfs4_server.py:941-959` (`_op_write` calls `_changed()` with no path), `nfs4_server/nfs4_server.py:761-781` (`_op_read`: cached handle discarded whenever `cached[0] != self.generation`), `nfs4_server/nfs4_server.py:797-822` (`_listing`: cache valid only for the current generation)
- **Problem:** Correctness is preserved, but the invalidation granularity is the whole server: one client writing one file closes and reopens the cached read handles of *all* connections (up to 4 per connection) on their next read and drops all 64 directory-listing cache slots' validity. During mixed read/write workloads (e.g. a file manager copying into the share while a console streams from it) the caches the code deliberately built — with comments explaining the Windows rename and burst-read motivations — are effectively always cold.
- **Fix:** Key the generation per path/comps (a dict of counters, defaulting to 0) and have `_changed(comps)` bump only the affected path and its parent directory's listing; `_op_write` already knows its `comps`.
- **Benefit:** Read-handle and listing caches survive unrelated writes, which is the common case they were built for.
- **Effort:** M · **Risk:** Med · **Confidence:** High (READ)

### RE-SERV-191 — NFS PUTFH resolves the handle number and throws it away; READ and READDIR then re-derive it through the mutating `handle()` on the hot path
- **Filed as:** PYSMALL-15 (pysmall.md)
- **Category:** 5
- **Locations:** `nfs4_server/nfs4_server.py:728-733` (`_op_putfh`: `self.handles.lookup(fh)[1]` — keeps comps, discards the number), `nfs4_server/nfs4_server.py:336-343` (`_Compound` stores only comps in `cfh`/`sfh`), `nfs4_server/nfs4_server.py:761-766` (`_op_read`: `self.handles.handle(comps)` then unpacks the number back out), `nfs4_server/nfs4_server.py:825-835` (`_op_readdir`: same re-derivation)
- **Problem:** Every READ and READDIR pays a locked dict lookup-or-*insert* (`handle()` takes `_Handles.lock` and mutates both dicts when the path is new) purely to recover a number the server already had when the client presented the filehandle. It also means pure read traffic can grow the handle table (compounding PYSMALL-11) for paths that were resolved from a handle the client supplied. The compound state object is the natural home for the number — it already exists to carry per-compound state.
- **Fix:** Store `(number, comps)` in `_Compound.cfh`/`sfh` at PUTFH/PUTROOTFH time and have READ/READDIR use the stored number; `handle()` then runs only in GETFH, where allocation is the point.
- **Benefit:** The streaming hot path stops taking a global lock and mutating shared tables per operation; handle allocation happens only where a handle is actually issued.
- **Effort:** M · **Risk:** Med · **Confidence:** High (READ)

### RE-SERV-192 — UDPBD `seen` peer map grows forever and the status reply reports it as the live session count
- **Filed as:** GOPROTO-06 (goproto.md)
- **Category:** 4
- **Locations:** native/ps2servers-edge/internal/udpbd/server.go:350-351 (only insertion); native/ps2servers-edge/internal/udpbd/server.go:205-206,220 (reported as `Sessions`)
- **Problem:** Every distinct peer address string (`IP:port`) that ever sends an INFO is inserted into `s.seen` and nothing ever removes an entry — a script census of the file shows the map is written at 350-351 and read at 205-206/350 only. Consoles that reappear from a new source port accumulate permanently, so (a) the map is an unbounded, slow leak on a long-lived router process, driven by unauthenticated UDP source addresses, and (b) the router-status `Sessions` field is not a session count at all but a monotonically increasing count of every peer ever seen, which is exactly the number a launcher uses to decide whether the server is in use.
- **Fix:** Delete the `seen` map's role in `statusReply` (report an actual live-peer notion or 0/1 in-flight state), and keep `seen` — if it is kept at all for the first-contact log line — bounded or expiry-based like the other servers' session tables.
- **Benefit:** Status reports something true, and an unauthenticated packet source can no longer grow server memory without limit.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-193 — Every directory listing / OPEN / GETSTAT re-parses compressed-image headers from disk — no caching (Python UDPFS and Go Edge UDPFS)
- **Filed as:** GOPROTO-08, PYUDPFS-16 (goproto.md)
- **Category:** 5
- **Locations:** native/ps2servers-edge/internal/udpfs/operations.go:282 (`prepareDirectory` → `ListVirtualCD`, called from every directory OPEN at operations.go:336); native/ps2servers-edge/internal/udpfs/operations.go:234-251 (`resolveVirtualCD` → `FindVirtualCD`, called from operations.go:344, 383, 781); native/ps2servers-edge/internal/compression/chd_cuebin.go:406-475 (`ListVirtualCD`); native/ps2servers-edge/internal/compression/chd_cuebin.go:476-539 (`FindVirtualCD`)
- **Problem:** There is no caching of CD track tables anywhere in the compression package (its only caches hold decompressed blocks/hunks). `ListVirtualCD` does a directory read and, for *every* `.chd` in the folder, a full `readCDTrackTable` (open, 124-byte header parse, metadata-chain walk, track parse, layout build) plus a second `layout()` — and `prepareDirectory` calls it on every directory OPEN, i.e. every time a console browses a folder. `FindVirtualCD` repeats the same directory scan and full parse for a single file, and it is invoked on the OPEN, write-OPEN and GETSTAT paths for every `.cue`/`.bin` name. On a share with dozens of CD CHDs, merely listing the folder re-reads and re-parses every CHD's metadata chain, every time, on the low-power routers Edge targets.
- **Fix:** Cache the parsed track table/layout per CHD path keyed by the file identity the code already checks (`os.SameFile` + size + modtime, as `VirtualCDFile.Open` does), and reuse it in both `ListVirtualCD` and `FindVirtualCD`.
- **Justification:** This adds one small cache, but it removes repeated disk reads and parses of the same immutable metadata on the browsing hot path — a concrete, measured-by-inspection cost with a net win on the target hardware; the invalidation key already exists in the code.
- **Benefit:** Directory browsing and virtual-CD opens stop scaling with total CHD metadata size per request; repeat listings become directory reads only.
- **Effort:** M · **Risk:** Med · **Confidence:** High (READ)

**Same mechanism in the parallel implementation** (filed separately as PYUDPFS-16 in pyudpfs.md, merged at consolidation):

- **Category:** 5
- **Locations:** `udpfs_server/udpfs_server.py:1745-1790` (`_prepare_directory_entries` calls `_get_compressed_stat` per compressed entry), `udpfs_server/udpfs_server.py:1388-1405` (`_get_compressed_stat`), `udpfs_server/udpfs_server.py:390-479` (`get_compressed_info` opens the file and, for CD CHDs, calls `exact_cd_iso_size`), `udpfs_server/compressed_iso/chd.py:215-240` (`exact_cd_iso_size` opens the file again and walks the metadata chain via `read_track_table`)
- **Problem:** nothing is cached between calls. One DOPEN of a directory with N compressed games costs N file opens + header parses, and for each CD-format CHD two opens plus a full track-table parse — repeated identically on every listing refresh and every GETSTAT, which OPL issues constantly while browsing. On the low-power/router hosts this server targets, listing a large CHD library is the hot path, and it is currently O(directory) parses per refresh with zero reuse. (INFERRED from the call graph; no timing was executed.)
- **Fix:** memoise `get_compressed_info` results keyed by `(path, st_mtime_ns, st_size)` in a small dict on the server; the stat data needed for the key is already in hand at both call sites.
- **Justification:** the cache is justified only because the same immutable-file parse is repeated per listing; keying on mtime+size keeps it honest when a file is replaced, and it removes work rather than adding a subsystem.
- **Benefit:** repeat listings of an unchanged library become stat-only; first listing cost unchanged.
- **Effort:** M · **Risk:** Low · **Confidence:** Med (INFERRED)

### RE-SERV-194 — SMB FIND_FIRST2's EndOfSearch branch is dead: the entry list can never have length 1
- **Filed as:** GOPROTO-11 (goproto.md)
- **Category:** 1
- **Locations:** native/ps2servers-edge/internal/smb/handlers.go:713-725
- **Problem:** `findFirst2` seeds `entries` with two entries, `"."` and `".."` (713-716), then computes `eos = 1` only `if len(entries) == 1` (722-725). The list always has at least two entries, so the branch is unreachable and `eos` is always 0 on the first reply — including for an empty directory, where the client must still issue a FIND_NEXT2 to learn the listing is finished. Dead condition plus a wasted round trip on every empty-folder browse.
- **Fix:** Delete the dead branch and set EndOfSearch from the real condition (cursor has reached the last entry, as `findNext2` already computes it).
- **Benefit:** Removes unreachable code and gives an empty listing a correct EndOfSearch on the first reply.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-195 — `compression.Open` is a production-dead wrapper kept alive only by tests
- **Filed as:** GOPROTO-16 (goproto.md)
- **Category:** 1
- **Locations:** native/ps2servers-edge/internal/compression/image.go:137-138
- **Problem:** `Open` is a one-line wrapper over `OpenCached(path, DefaultCacheBlocks)`. A script census of the whole Edge module finds zero production callers: the only production open paths are `OpenCached` and `OpenRaw` (both called from internal/udpfs/blockdev.go:396,402), while `Open` is referenced exclusively from `*_test.go` files in its own package. In an `internal` package there are no external consumers to keep it for; it is an API surface that exists only so tests can call the shorter name.
- **Fix:** Delete `Open` and have the tests call `OpenCached(path, DefaultCacheBlocks)`.
- **Benefit:** One fewer entry point into the image opener; the production API is exactly the two calls production actually makes.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ — census by script over the whole module)

### RE-SERV-196 — OpenWrt `ServiceLogs` ignores its line limit, defeating the stated snapshot memory bound
- **Filed as:** GOCORE-06 (gocore.md)
- **Category:** 4
- **Locations:** `native/ps2servers-edge/internal/webui/service.go:67-82` (`maxServiceLogLines`/`clampLogLines` and the bound's rationale); `native/ps2servers-edge/internal/webui/service.go:173-176` (openwrtManager); `native/ps2servers-edge/internal/webui/service.go:208-212` (systemdManager, which does clamp); `native/ps2servers-edge/internal/webui/api.go:284-295` (the caller that then streams every returned line)
- **Problem:** service.go states the design rule itself: `maxServiceLogLines` "bounds one snapshot, and therefore the memory a request can make this process hold." The systemd manager honours it via `clampLogLines(lines)`; the OpenWrt manager ignores its `lines` parameter entirely and returns the full output of `logread -e ps2servers-edge` — however many lines the router's log ring holds — which `HandleLogs` then holds in a `[]string` and writes out line by line. OpenWrt is the primary router target (32 MB class hardware per the package comments), i.e. the platform where the bound matters most is the one that doesn't apply it.
- **Fix:** Truncate the `readLogCommand` result to `clampLogLines(lines)` entries (keeping the most recent) inside `openwrtManager.ServiceLogs`, mirroring the systemd path. No new mechanism — reuse the existing clamp.
- **Benefit:** The documented per-request memory bound becomes true on all three managers; a `/api/logs` connect on OpenWrt can no longer pull an unbounded log snapshot into the webui process.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-197 — `sessionstats` exports production API that only tests use: `Parse`, `WithClock`, `WithLimits`
- **Filed as:** GOCORE-08 (gocore.md)
- **Category:** 1
- **Locations:** `native/ps2servers-edge/internal/sessionstats/sessionstats.go:247-266` (`Parse`); `native/ps2servers-edge/internal/sessionstats/sessionstats.go:67-74` (`WithClock`, `WithLimits`)
- **Problem:** A grep census over all `.go` files in the edge tree shows zero non-test callers for all three. `Parse` is called only from `internal/smb/session_summary_test.go`, `internal/udpbd/session_summary_test.go` and `internal/udpfs/session_summary_test.go`; `WithClock` and `WithLimits` are called only from `internal/sessionstats/sessionstats_test.go`, and their own comments say so ("for tests"). They are exported, mutable-after-construction surface on a concurrency-sensitive type: `WithClock`/`WithLimits` rewrite `Stats` fields with no synchronisation, safe only because tests call them before first use — an invariant nothing enforces for a future production caller.
- **Fix:** Move the three helpers into the test files that use them (an in-package `sessionstats_test` helper can set the unexported fields directly), or if `Parse` is genuinely wanted by an external consumer, name that consumer in its doc. Do not keep exported production API whose only callers are tests.
- **Benefit:** Smaller, honest API surface for a package every server links; the unsynchronised mutators can no longer be picked up by production code by accident.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-198 — Dead webui surface: an unread `API.log` field, and `Server.GeneratedPassword` / `Server.Close` with no callers
- **Filed as:** GOCORE-09 (gocore.md)
- **Category:** 1
- **Locations:** `native/ps2servers-edge/internal/webui/api.go:22` (field) set at `native/ps2servers-edge/internal/webui/webui.go:206`; `native/ps2servers-edge/internal/webui/webui.go:106-107` (`GeneratedPassword`); `native/ps2servers-edge/internal/webui/webui.go:293-298` (`Server.Close`)
- **Problem:** Three grep-verified dead items in one package. (1) `API.log` is assigned in `Serve` but never read anywhere in api.go — every handler uses `a.logBuffer`, `a.manager` or `jsonError` (which writes to stderr directly). (2) `Server.GeneratedPassword` has no caller anywhere in the tree, test or non-test: `Serve` itself already logs the generated password at webui.go:271-273, so the accessor's stated purpose ("so the caller can print it") is already served and the method is pure extra surface. (3) `Server.Close` likewise has no caller; shutdown is driven by the context goroutine inside `Serve` (webui.go:281-289), and `main.go`'s webui path never calls it.
- **Fix:** Delete the `log` field from `API` and its assignment, and delete `GeneratedPassword` and `Close` (or, for `Close`, wire it only if a caller actually appears). All deletions, no replacements.
- **Benefit:** Less misleading surface: a reader no longer has to trace a logger field that does nothing or lifecycle methods that are never invoked.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-199 — `Root.List` fully symlink-resolves and stats every directory entry, one `EvalSymlinks` per entry
- **Filed as:** GOCORE-11 (gocore.md)
- **Category:** 5
- **Locations:** `native/ps2servers-edge/internal/filesystem/root.go:235-259` (`List`); `native/ps2servers-edge/internal/filesystem/root.go:66-97` (`Resolve`, called per entry at root.go:247)
- **Problem:** `List` first resolves the directory itself (root.go:236), then for **every** entry calls `r.Resolve(child, false)` — which runs `filepath.EvalSymlinks` on the full path plus a `filepath.Rel` containment check — and then `os.Stat` on the result. A directory listing (the DREAD path a console issues per folder) therefore costs at least two path-walking syscalls per entry on top of `ReadDir`, on the router-class hardware this build targets, for folders that in the common case contain no symlinks at all. The per-entry escape check is a real security requirement and must stay for links; it is only the unconditional full resolution of plain files/directories that is wasted work.
- **Fix:** `Lstat` each entry first (one syscall, already implied by `ReadDir`'s type bits); only when the entry is a symlink run the existing `Resolve` containment path, otherwise join the already-resolved parent directory with the entry name and `Stat` only if size/mode are needed beyond the `Lstat`. Keep `Resolve` itself untouched for all other callers.
- **Benefit:** Directory listings drop from ~2 path resolutions per entry to ~1 `Lstat` per plain entry, with identical escape behaviour for the symlink entries that are the actual threat.
- **Effort:** M · **Risk:** Med · **Confidence:** Med (INFERRED — cost structure is read from the code and syscall counts are per-entry by construction; no benchmark was run, and symlink semantics must be re-verified against `root_test.go` when changed)

---

### RE-SERV-200 — The native-binary launch mechanism in the registry is dead: no registered server uses it
- **Filed as:** LNCHA-10 (lncha.md)
- **Category:** 1
- **Locations:** launcher/servers.py:70 (`binary_rel` field, never populated by any ServerDef); launcher/servers.py:87-93 (`resolve_binary`); launcher/servers.py:96-111 (`is_available` native branch, `launch_command` native branch); launcher/serve.py:156-159 (the "native server; run its binary directly" rejection); census: all 8 REGISTRY definitions declare `runtime="python"` (launcher/servers.py:386, 435, 451, 467, 567, 631, 658, 717), and a scripted grep for `binary_rel`/`resolve_binary` across launcher + tests returns only the declaration and its own methods
- **Problem:** A complete second launch path — per-OS binary resolution, availability checks, a separate serve.py rejection — exists for a `runtime="native"` server kind that no server in the registry has. It is untested by construction (nothing can reach it) and every reader of ServerDef must reason about a branch that cannot execute.
- **Fix:** Delete `binary_rel`, `resolve_binary`, and the native branches of `is_available`/`launch_command` (keep the `runtime` field itself only if a caller still reads it); re-add when a native server actually lands.
- **Benefit:** The registry shrinks to the one launch mechanism that exists; no untestable branch in the start path.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ, census by script)

### RE-SERV-201 — Virtual exFAT construction recomputes the full 65,536-entry Unicode upcase table on every instantiation (~130-180 ms for a one-file folder)
- **Filed as:** LNCHA-18 (lncha.md)
- **Category:** 5
- **Locations:** launcher/virtual_exfat.py:39-45 (`upcase_table()` — pure function of the exFAT spec); launcher/virtual_exfat.py:71-95 (`__init__` calls it, then RLE-encodes all 65,536 entries with a second full loop); also launcher/virtual_exfat.py:31-37 (`name_units` encodes the name as UTF-16-LE twice per call and is re-invoked per node in scan, sizing and directory generation — 6 calls measured for a 2-entry tree)
- **Problem:** Executed in the sandbox: `upcase_table()` alone took ~180 ms; constructing `VirtualExfat` over a folder containing a single file took ~127-167 ms, essentially all of it this fixed cost that does not depend on the folder at all. Every UDPBD virtual-folder start pays it, and the table + encoded form are bit-identical for every instance and every process.
- **Fix:** Compute the upcase table and its encoded bytes once at module level (lazily, on first use) and reuse them across instances.
- **Benefit:** Virtual-folder server start drops the fixed ~0.15 s of pure-Python table building; repeated constructions in one process become nearly free.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED — timings)

### RE-SERV-202 — Opening one raw device re-runs the full OS disk enumeration up to twice, and every mount check re-runs it again
- **Filed as:** LNCHA-19 (lncha.md)
- **Category:** 5
- **Locations:** launcher/raw_storage.py:326-367 (`_metadata()`: on Windows a PowerShell `Get-Disk`/`Get-Partition`/`Get-Volume` sweep of every disk, 20 s timeout; on Linux an `lsblk` tree walk); launcher/raw_storage.py:284-311 (`open_raw_device`: calls `_exclusive_target` — which calls `_metadata()` — and then unconditionally sets `device.mount_warning = mount_warning(path)`, which calls `_metadata()` again at launcher/raw_storage.py:409-427); launcher/raw_storage.py:392-407 (`list_devices` calls it a further time per invocation)
- **Problem:** The enumeration is the expensive operation in this module (a fresh powershell.exe process walking all disks/partitions/volumes), yet a single exclusive raw-device open pays it twice back-to-back for the same answer, and `path_access`'s device check plus the UDPBD server's own open each pay it again. On a multi-disk Windows machine this is seconds of avoidable latency on the start path of the raw-drive mode, for data that cannot change between the two calls inside one open.
- **Fix:** Have `open_raw_device` fetch the metadata once and pass the matching record into the mount-warning computation (or memoise `_metadata()` for the duration of one open).
- **Benefit:** One enumeration per device open instead of two; raw-drive start and access checks stop paying duplicate PowerShell startup + disk sweeps.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ — call graph within the one file; the per-call cost is documented in the code's own timeout and in windows_setup.py:19-34's measurements of PowerShell/firewall query cost)

### RE-SERV-203 — session_stats' header contract names the wrong server set
- **Filed as:** LNCHA-20 (lncha.md)
- **Category:** 7
- **Locations:** launcher/session_stats.py:1-16 ("Shared by every server (HTTP, SMBv1, UDPFS, UDPBD), so one parser reads all of their lines"); counter-evidence: smb2_server/smb2_server.py:1789-1790 constructs `SessionStats("smbv%d" % args.smb_version)` — SMBv2/SMBv3 are users too and are not named; launcher/servers.py:624-652 and 712-746 — NFS and RetroAchievements have no `session_summary` field at all, so "every server" is also an overclaim
- **Problem:** The docstring is the contract a maintainer reads before touching the SESSION line format ("The line is a contract the launcher can read"). It omits two of the six actual producers and implies two non-producers are covered, so anyone extending the format (or the launcher-side parser in session_view.py) from this header will miscount the producers.
- **Fix:** Correct the parenthetical to the actual producer set (HTTP, SMBv1, SMBv2, SMBv3, UDPFS, UDPBD).
- **Benefit:** The format contract lists its real producers; no code change.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

---

### RE-SERV-204 — Verified dead code: `derive_watch` alias, unused `ENDPOINT` attributes, two unused imports
- **Filed as:** LNCHB-16 (lnchb.md)
- **Category:** 1
- **Locations:** launcher/ra_sets.py:204 (`derive_watch = derive_direct_watch` — repo-wide grep finds no caller other than the definition), launcher/ra_set_client.py:7 (imports `_NoRedirect`, never used — AST census), launcher/ra_set_client.py:13 and launcher/ra_session_client.py:56 (`ENDPOINT = Transport.ENDPOINT` class attributes; the set client's is referenced nowhere, the session client's only by a test assertion), launcher/achievements.py:13 (`import sys`, never referenced — AST census)
- **Problem:** Small but real clutter at exactly the seams a reader uses to orient: an alias that suggests a second derivation entry point, and endpoint attributes that suggest per-client endpoints when both clients actually post through `Transport.ENDPOINT`.
- **Fix:** Delete the alias, the set client's `ENDPOINT`, and both unused imports; keep the session client's `ENDPOINT` only if the test contract is considered public, and say so in a comment if kept.
- **Benefit:** Fewer false trails in the most security-sensitive files in the subsystem.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED — AST/grep census, zero callers)

### RE-SERV-205 — The same watch list is fully re-validated four times on every set activation
- **Filed as:** LNCHB-17 (lnchb.md)
- **Category:** 5
- **Locations:** launcher/ra_sets.py:200 (`derive_direct_watch` validates the derived bytes), launcher/ra_watchlist.py:101 (`WatchSession.install` validates again), launcher/ra_receiver.py:111 (`Receiver.complete` validates again before installing), launcher/ra_evaluator.py:73 (`Evaluator.__init__` validates a fourth time)
- **Problem:** `validate_watchlist` is an O(count + nodes) structural pass over up to 17,432 bytes with per-entry struct unpacking. On the owned path a single identification runs it at derivation, at receiver publication, at session installation and at evaluator construction — four passes over identical, already-immutable `bytes` within one call chain (`EvaluatedReceiver.complete_set` → `complete` → `Receiver.complete` → `WatchSession.install`, plus `Evaluator`). None of the later passes can fail if the earlier one in the same chain passed, because the object is the same immutable bytes object.
- **Fix:** Validate once at derivation and once at the IPC/trust boundary (where bytes arrive from another process); drop the redundant in-chain revalidations in `Receiver.complete`/`WatchSession.install` when the caller is the in-process evaluated path, or pass the already-produced `WatchList` object instead of raw bytes so revalidation is structurally unnecessary.
- **Benefit:** Removes repeated per-activation work on the identification hot path without weakening the cross-process check.
- **Effort:** M · **Risk:** Med · **Confidence:** High (READ; call sites grep-verified)

### RE-SERV-206 — SSE `/events` uses one shared dirty-flag for all clients and ignores its result, so every client is sent the full state about once a second regardless of change
- **Filed as:** LNCHB-19 (lnchb.md)
- **Category:** 5
- **Locations:** launcher/ra_web.py:142-145 (`wait_dirty`: waits on the single `RAState._dirty` event, then clears it — one client's wait clears the flag for all others), launcher/ra_web.py:507 (the `/events` handler calls `wait_dirty(timeout=1.0)`, discards the returned flag, and unconditionally serialises and writes the full state)
- **Problem:** The dirty mechanism cannot work as designed: with N connected browsers, whichever handler wakes first clears the event, and since the return value is ignored every handler wakes on the 1 s timeout anyway and pushes a complete `to_dict()` JSON. The result is per-second full-state serialisation per client (the exact cost the dirty flag exists to avoid), plus `RAWebServer.close()` poking the private `state._dirty` (launcher/ra_web.py:567) to compensate. On the LAN viewer path this state is also relayed onward.
- **Fix:** Either delete `wait_dirty`/`_dirty` and keep the honest 1 s poll-push, or make change notification per-client (each handler tracks the last-sent revision counter on `RAState`). The first option is the LESS fix and matches current observable behaviour.
- **Benefit:** Removes a misleading synchronisation mechanism (and a private-attribute reach from the server class) without changing what clients actually receive.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

---

### RE-SERV-207 — Zero-caller symbols: `AVAST_GEN_PUBLIC_NOTE`, `theme_assets.photo()`, `LauncherApp.on_close()`, `LauncherApp._quit_from_tray()`, and a double wrapper in dependency_panel
- **Filed as:** LNCHC-02 (lnchc.md)
- **Category:** 1
- **Locations:** launcher/release_metadata.py:73-79; launcher/theme_assets.py:68-76; launcher/gui.py:3562-3563; launcher/gui.py:3774-3775; launcher/dependency_panel.py:43-48
- **Problem:** Repo-wide grep (all files, excluding `.git`/`__pycache__`) returns exactly one hit for `AVAST_GEN_PUBLIC_NOTE` — its own definition; nothing in code, build scripts, tools, or tests references the Avast submission note. `theme_assets.photo()` (the `subsample=` variant) has zero callers: both real call sites use `photo_fit` (asset_skin.py:20, gui.py:1523). `LauncherApp.on_close()` has zero callers — window close is wired to `_on_window_close` — and `_quit_from_tray()` has zero callers — tray quit flows through `_tray_queue` → `_drain_tray` → `exit_app()`. In dependency_panel.py, `summarize_status_for_ui()` (43-44) is a one-line forward to `optional_deps.summarize_statuses()`, and `_status_bits()` (47-48) is a one-line forward to `summarize_status_for_ui()`: two indirection layers for a single call, used at dependency_panel.py:53,60.
- **Fix:** Delete all five: the Avast constant (move the text to the existing `ANTIVIRUS-NOTICE.md`/docs if it must be kept), `photo()`, `on_close()`, `_quit_from_tray()`, and both dependency-panel wrappers (call `optional_deps.summarize_statuses()` directly at the two use sites).
- **Benefit:** ~30 lines gone; removes the false impression that the Avast note is displayed somewhere and that `photo()` is a supported loader entry point.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ — grep-verified zero external references)

### RE-SERV-208 — `Library.installed()` re-validates every path up to three times per call, and `Catalog.list_games()` stats every verified row on every listing
- **Filed as:** LNCHC-07 (lnchc.md)
- **Category:** 5
- **Locations:** launcher/game_library.py:227-286; launcher/library_catalog.py:94-103 (`_scan_path`),178-218 (`list_games`/`_current_identity`)
- **Problem:** `_scan_path` is expensive by design: it `lstat`s every path component and then `resolve(strict=True)`s the whole path. `Library.installed()` calls it once for each media directory, once for *every file* in those directories, then — when an index is supplied — again for every result row while building its `available` dict, and again for every indexed game, even though `read_index` already performed the same `_scan_path` validation when it built the snapshot (library_index.py:157-158). A refresh of the RA library window therefore does 2–3 full containment resolutions per image on top of the index's own pass. Separately, `Catalog.list_games()` runs `_current_identity` on every returned row (up to the 10,000-row LIMIT), and for each row whose cached status is verified/conflict that does a fresh `Path.stat()` + fingerprint string build (library_catalog.py:206-218) — i.e. a filesystem stat per row on every search keystroke-driven refresh and panel reload, purely to detect staleness that the scan/inspect paths already check.
- **Fix:** In `installed()`, trust the snapshot `read_index` already validated in the same call chain (drop the second and third `_scan_path` rounds; keep the root-mismatch check, which is string comparison). In `list_games()`, only run the staleness stat for the page actually displayed, or mark staleness during scan/inspect instead of on read.
- **Benefit:** Library refresh goes from O(3×paths × components) resolutions + O(rows) stats to one validated pass; noticeable on large/network game folders, which are this app's normal case.
- **Effort:** M · **Risk:** Med · **Confidence:** High (READ)

### RE-SERV-209 — `LOGO.png` is 603 KB at 1229×1185 for display slots of at most 256 px; downscaling is integer-only subsampling
- **Filed as:** LNCHC-12 (lnchc.md)
- **Category:** 4
- **Locations:** launcher/assets/theme/LOGO.png (602,983 bytes, 1229×1185, measured by script); launcher/theme_assets.py:78-92; launcher/gui.py:1523-1524; launcher/tray_linux.py:71-76
- **Problem:** The logo is 94% of all shipped theme-asset bytes (603 KB of 639 KB). Its largest consumers are: the About tab at `max_width=150, max_height=150` (gui.py:1523-1524), the Linux tray resized to 64×64 (tray_linux.py:74), and the release workflow's AppImage icon resized to 256×256 (`.github/workflows/release.yml:284`). Nothing ever shows it above 256 px. The Tk loader makes this worse: `photo_fit` can only shrink by integer `subsample` (theme_assets.py:85-92), so the 1229-px source is decoded in full and subsampled by 9 for the About tab — full decode cost and memory for a 137-px result, every launch.
- **Fix:** Re-encode the shipped LOGO.png at 256×256 (the largest real consumer) — a data change, no code change; keep the ICON_*.png files as they are (203×100 for a 56×28 slot is proportionate).
- **Benefit:** ~590 KB off every download/AppImage and off the in-memory decoded image; no visible change at any display size in the app.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED — dimensions/sizes measured with PIL/`file`; display sizes READ)

### RE-SERV-210 — `gui.py` embeds ~10 KB of About/manual prose that is mutated at runtime by another module
- **Filed as:** LNCHC-13 (lnchc.md)
- **Category:** 4
- **Locations:** launcher/gui.py:126-221 (`ABOUT_TEXT`, 10,223 characters / 96 lines by script count); launcher/gui.py:1656 (sole display site)
- **Problem:** A full user manual — server descriptions, the port-445 service-pausing explanation, firewall policy, direct-link behaviour, troubleshooting, credits — lives as a triple-quoted literal inside the 3,786-line GUI module, is extended by two further `+=` appends in the same file (gui.py:208,210), and is then mutated *again* at import/startup time from outside the module (`launcher/main.py` appends its `ADMIN_ABOUT_TEXT`/`UNIX_ABOUT_TEXT` to `gui.ABOUT_TEXT`). The displayed text therefore exists in no single file, cannot be edited as documentation, and overlaps in content with the setup guides (LNCHC-05) and the server-card help strings. Tests must assert against prose substrings to keep it honest.
- **Fix:** Trim `ABOUT_TEXT` in place to what an About tab needs (what it is, the mode list in one line each, links/credits — the Links buttons already sit directly above it) and delete the duplicated firewall/direct-link/troubleshooting prose, which is already covered by the guides and card help; do not replace it with a new docs mechanism.
- **Benefit:** Several KB and ~60 lines out of the largest file in the launcher; one less runtime-mutated global; About stops drifting against the guides because it no longer restates them.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ — size counted by script)

### RE-SERV-211 — Dependency checks are never cached: every panel refresh re-probes lz4 and re-`LoadLibrary`s libchdr candidates; the PATH cache is process-stale
- **Filed as:** LNCHC-16 (lnchc.md)
- **Category:** 5
- **Locations:** launcher/optional_deps.py:50-68,139-190; launcher/dependency_panel.py:51-69,71-83
- **Problem:** `check_libchdr()` walks all candidate directories, mutates `PATH`/DLL directories via `_prepare_native_dir`, and calls `ctypes.cdll.LoadLibrary` on candidates until one succeeds (optional_deps.py:165-190) — and it is re-run in full on every `check_all()`, which happens on panel creation (`refresh_status_async`), on every "Check" click (`refresh_status`), and again inside "Details" via a direct `check_libchdr()` call. Nothing memoizes a successful load, so the panel's lifetime cost is repeated native loads and repeated `PATH` prepends guarded only by module-level sets. Conversely, `_known_path_dirs()` caches the process `PATH` snapshot forever in `_PATH_DIRS` (optional_deps.py:50-68): if `PATH` changes after the first check, `_prepare_native_dir` decides prepends from the stale snapshot (INFERRED: can re-prepend a directory or skip a needed one).
- **Fix:** Memoize `check_all()`'s result in `optional_deps` after the first successful probe (invalidate only in `install_lz4`'s completion path, which already refreshes), and drop the `_PATH_DIRS` cache — read `os.environ["PATH"]` when preparing a directory.
- **Benefit:** One native probe per process instead of one per click/refresh; removes a stale-global correctness trap.
- **Effort:** S · **Risk:** Low · **Confidence:** Med (READ for the uncached call chain; INFERRED for the stale-PATH consequence)

---

### RE-SERV-212 — OpenWrt package metadata is a release behind and describes a UDPFS-only read-only server that no longer exists
- **Filed as:** CIDOCS-04 (cidocs.md)
- **Category:** 7
- **Locations:** `packaging/openwrt/Makefile:4` (`PKG_VERSION:=0.8.2`), `:21` (`TITLE:=PS2 Servers Edge UDPFS server`), `:26-28` (description: "A small read-only UDPFS server…"); source of truth `launcher/release_metadata.py:15` (`PRODUCT_VERSION = "0.9.4"`)
- **Problem:** The package version is hand-maintained and has drifted a full minor release behind the product version, and nothing catches it: `tools/version_source.py --check-sources` guards only `edge-build.yml`'s literals. The title/description also predate three of the binary's four subcommands (executed: udpfs, udpbd, smb, webui) and call the server itself read-only when read-only is only the package config's choice (`packaging/openwrt/files/ps2servers-edge.config` sets `read_only '1'`; the binary defaults writable). `opkg` users see a wrong version and a wrong capability summary.
- **Fix:** Derive or check `PKG_VERSION` against `tools/version_source.py` (extend its workflow list to the Makefile), and update TITLE/description to the four-subcommand scope with read-only attributed to the package defaults.
- **Benefit:** One version number across launcher, Edge archives and the OpenWrt package; package browsers describe the software actually installed.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-213 — The portable/AppImage half of the release pipeline cannot fail, so missing downloads stay green
- **Filed as:** CIDOCS-06 (cidocs.md)
- **Category:** 7
- **Locations:** `.github/workflows/release.yml` — 13 `continue-on-error: true` steps (lines 146, 228, 235, 252, 269, 343, 361, 379, 388, 403, 420 and the portable/AppImage build-stage steps), including `:403-420` where the portable **upload** step pairs `if-no-files-found: error` with `continue-on-error: true`, negating the error; same pattern, 5 occurrences, in `.github/workflows/release-on-main.yml`
- **Problem:** The intent (optional variants must not block the primary release) is documented and the release-notes generator has fallback rows, but the composition means a broken portable or AppImage build produces no red signal anywhere: build, checksum, attestation and upload all swallow failure, and the upload's own `if-no-files-found: error` is explicitly overridden. A regression in `build/build.py`'s standalone mode would silently remove the antivirus-friendly portable download — the variant that exists for users who cannot run the primary one — from every release until a user reports it.
- **Fix:** Keep `continue-on-error` on build/stage steps if desired, but drop it from one final verification step per optional artifact (assert the file exists when its build step reported success), or surface absence as a job summary warning that a human sees. Remove the self-negating `if-no-files-found: error` + `continue-on-error` pairing.
- **Benefit:** Optional downloads can still fail soft for users, but no longer fail invisible for maintainers.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-214 — Go toolchain pinned three different ways across Edge's own build paths
- **Filed as:** CIDOCS-08 (cidocs.md)
- **Category:** 7
- **Locations:** `native/ps2servers-edge/go.mod:3` (`go 1.22`, consumed via `go-version-file` by `.github/workflows/edge.yml:44` test job and `edge-build.yml`), `.github/workflows/edge.yml:198` (parity job hardcodes `go-version: "1.25"`), `packaging/docker/Dockerfile.edge:2` (`golang:1.23-alpine`)
- **Problem:** The differential parity evidence, the unit-test evidence and the shipped binary are produced by three different compilers, none of which is stated to be intentional. Binary size and codegen demonstrably move with toolchain version (edge-build.yml's own size comment documents ~1 MiB of drift between Go versions), so "parity passed" and "the released binary" are weaker claims than they appear. The Docker image additionally runs `go test ./...` with its third toolchain at image-build time.
- **Fix:** Point the parity job at `go-version-file` like the test job, and either pin the Dockerfile to the go.mod version or document why the container intentionally differs.
- **Benefit:** Test, parity and shipped artifacts describe the same compiler; size-ceiling reasoning in edge-build.yml stays valid.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

### RE-SERV-215 — Byte-identical 571,581-byte banner PNG tracked twice
- **Filed as:** CIDOCS-10 (cidocs.md)
- **Category:** 7
- **Locations:** `ps2serversgithubbanner.png` and `docs/assets/ps2servers-banner.png` (identical size 571,581 B and identical md5 `7d13770364c4192c47463f2ba0ee1ff0`, census by script over `git ls-files`)
- **Problem:** The same image is stored twice in the tree; the docs copy is the one referenced by the site's `og:image` tags. Any future banner update must be made in two places or the GitHub-facing and site-facing banners diverge.
- **Fix:** Keep one canonical copy and reference it from the other location (or accept the docs copy as canonical and delete the root duplicate if nothing references it).
- **Benefit:** 571 KB out of every clone and no two-place update hazard.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED census)

### RE-SERV-216 — ~3.16 MB of generated compression test fixtures are tracked as binaries
- **Filed as:** CIDOCS-12 (cidocs.md)
- **Category:** 7
- **Locations:** `native/ps2servers-edge/internal/compression/testdata/test.iso` (1,110,016 B), `test_cd.chd` (1,055,592 B), `test_dvd.chd` (1,049,766 B) — census by script over `git ls-files`
- **Problem:** Three of the four largest non-vendor files in the tree are test fixtures for formats (ISO/CHD) whose test content is synthetic; they are paid for in every clone and every `git archive` source download (which release.yml ships as the "inspection" artifact). This is the smallest finding here and may be a deliberate choice (CHD fixtures are awkward to regenerate without libchdr tooling), but no in-tree note says so.
- **Fix:** Either generate the ISO fixture in-test and keep only the CHDs with a one-line justification in the testdata directory, or record the deliberate choice so it is not re-litigated.
- **Benefit:** ~1–3 MB out of every clone/source archive, or a documented reason it stays.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED census)

---

---

## Checked and cleared (negative results)

## Verified clean (checked, no finding)

- **Crypto primitives are correct.** Executed: MD4 passes all four RFC 1320 vectors tested and `nt_hash("password")` matches the published NT-hash vector; AES-128 passes the FIPS-197 vector; pure-Python AES-CMAC passes RFC 4493 examples 1–2 and agrees with the OpenSSL native backend on the zero-key/empty case. Signature verification uses `hmac.compare_digest` (`smb2_crypto.py` `verify`), and the unknown-user path in `Authenticator.verify` does dummy proof work before failing (`smb2_auth.py:301-315`).
- **Path containment is sound.** `Share.resolve` (`smb2_paths.py:117-176`) refuses `..` outright, UNC paths, ADS separators, device names, trailing dot/space, and tests containment on the real path with `commonpath`. No traversal bypass found by reading.
- **`request_buffer` bounds-checking is sound** and is used by SESSION_SETUP, TREE_CONNECT, CREATE, WRITE, QUERY_DIRECTORY, SET_INFO and IOCTL (the one handler that does not use it for its FileId is PYSMB2-15).
- **KDF construction** (`smb2_crypto.py` `kdf`) was read against MS-SMB2 3.1.4.2: the double NUL between label and context is the label's own trailing NUL plus the SP800-108 separator, not a duplication bug. INFERRED — no published KDF test vector was available in the sandbox to execute against.

---

### PYSMB2-01 — Pre-authentication commands stay live after authentication and rewrite the session's security state (signing downgrade + negotiate-claims overwrite)
- **Category:** 6
- **Locations:** smb2_server/smb2_server.py:537-538 (`_UNSIGNED_OK` exempts NEGOTIATE and SESSION_SETUP from the signing-required check), smb2_server/smb2_server.py:540-563 (`_signature_check` returns None whenever `signing_key` is None), smb2_server/smb2_server.py:745-755 (`h_negotiate` unconditionally replaces `conn.dialect` and `conn.negotiate_claims`), smb2_server/smb2_server.py:840-867 (SESSION_SETUP NTLM NEGOTIATE branch sets `conn.signing_key = None`, `conn.signing_required = False` at 855-856 and never clears `conn.authenticated`)
- **Problem:** Nothing retires the pre-auth commands once a session exists. Executed proof (handler-level, /tmp copy): with `authenticated=True`, `signing_required=True` and a signing key set, an unsigned SESSION_SETUP carrying an NTLMSSP NEGOTIATE returned MORE_PROCESSING_REQUIRED, left `authenticated=True`, and cleared both `signing_key` and `signing_required` — after which an unsigned CREATE passed `_signature_check`. An on-path attacker (or a confused client) can therefore strip signing from a live, signing-required session without re-authenticating, then inject unsigned commands under the victim's identity; every file handle and tree survives. The same hole exists for SMB2 NEGOTIATE: executed proof shows an unsigned mid-session NEGOTIATE changed the live dialect 0x0302 → 0x0202 and overwrote `conn.negotiate_claims` — the stored claims `validate_negotiate_info` (smb2_server.py:1406-1431) compares the client's signed re-statement against, i.e. the anti-downgrade check can be re-based by the very party it exists to catch.
- **Fix:** In `h_negotiate` and in the NTLM-NEGOTIATE branch of `h_session_setup`, refuse (or fully tear down the session — close handles/trees, clear `authenticated`) when `conn.authenticated` is already true. A re-negotiation must never be a silent in-place mutation of a live session.
- **Benefit:** Closes a signing-downgrade / session-hijack path and restores FSCTL_VALIDATE_NEGOTIATE_INFO to comparing against claims the attacker cannot replace.
- **Effort:** M · **Risk:** Med · **Confidence:** High (EXECUTED)

### PYSMB2-02 — CREATE, SET_INFO and CLOSE perform create / truncate / rename / delete without checking the handle's granted DesiredAccess
- **Category:** 6
- **Locations:** smb2_server/smb2_server.py:1023-1040 (CREATE creates a file or `os.makedirs` a directory, and truncates an existing file for OVERWRITE / OVERWRITE_IF / SUPERSEDE), smb2_server/smb2_server.py:1050-1053 (`can_write` is computed only *after* those effects; FILE_DELETE_ON_CLOSE is armed on any handle), smb2_server/smb2_server.py:1083-1088 (CLOSE executes the delete), smb2_server/smb2_server.py:1333-1335 (SET_INFO FileDispositionInformation arms delete-on-close with no `can_write` check), smb2_server/smb2_server.py:1340-1357 (SET_INFO rename with no `can_write` check)
- **Problem:** Destructive effects are gated only on the share being writable (`srv._writable`), never on the access the CREATE actually granted — while WRITE (smb2_server.py:1177) and the SET_INFO EOF-truncate (smb2_server.py:1328-1329) *do* check `h.can_write`, proving the intended rule exists and these paths missed it. Executed proofs: (1) CREATE with DesiredAccess = FILE_READ_DATA only and disposition FILE_OVERWRITE returned SUCCESS with `can_write=False` and the target file truncated to 0 bytes; (2) SET_INFO rename through a read-only handle returned SUCCESS and moved the file; (3) CREATE with FILE_DELETE_ON_CLOSE through a read-only handle deleted the file at CLOSE. `os.makedirs` at 1026 also creates every missing intermediate directory as a side effect. A client (or a bug in one) holding only a read handle can destroy, move or delete files it was never granted write access to.
- **Fix:** Compute `can_write` from DesiredAccess before any filesystem effect in `h_create`, and require it (or the specific DELETE access) for the create/truncate branches, for arming delete-on-close, and in the SET_INFO disposition and rename branches — the same check WRITE already makes.
- **Benefit:** DesiredAccess becomes a real boundary instead of an annotation two handlers honour and five effects ignore.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED)

### PYSMB2-03 — READ is never gated on DesiredAccess; there is no read-access concept at all
- **Category:** 6
- **Locations:** smb2_server/smb2_server.py:1130-1157 (`h_read` checks only pipe/directory, then reads), smb2_server/smb2_server.py:150-166 (`Handle` tracks `can_write` and nothing for read), smb2_server/smb2_wire.py:143-145 (only `ACCESS_WRITE_ANY` is defined; no read mask exists)
- **Problem:** Any successfully created handle can be read, including one opened with DesiredAccess = FILE_WRITE_DATA only, or with no data access at all. Executed proof: a handle created write-only returned the file's contents (`b'TOPSECRET-DATA'`) from `h_read` with STATUS_SUCCESS. Combined with PYSMB2-02, DesiredAccess is decorative in both directions.
- **Fix:** Record a `can_read` on the handle from the same CREATE access mask (FILE_READ_DATA | GENERIC_READ | MAXIMUM_ALLOWED | GENERIC_ALL) and check it in `h_read`'s file branch, mirroring the existing `can_write` check in `h_write`.
- **Benefit:** A write-only or attribute-only handle stops being a read channel for the file's contents.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED)

### PYSMB2-04 — SET_INFO FileBasicInformation returns SUCCESS while doing nothing — and validates nothing
- **Category:** 6
- **Locations:** smb2_server/smb2_server.py:1336-1339
- **Problem:** The handler returns STATUS_SUCCESS for FileBasicInformation without applying the timestamps, and without even checking the payload exists: executed proof shows a SET_INFO with a zero-length payload returns SUCCESS. The in-code comment frames this as an Explorer-compatibility trade-off, but on the wire it is indistinguishable from success — a client copying files (or a backup tool preserving timestamps) is told the times were set when they were silently discarded, so copies quietly carry wrong timestamps with no error anywhere.
- **Fix:** Either apply the times (`os.utime` from the payload's LastWriteTime/ChangeTime fields, after a length check) or answer STATUS_NOT_SUPPORTED so the client knows. Keeping the Explorer behaviour is a defensible choice; reporting success for an empty payload is not — at minimum validate the payload length like the rename branch does.
- **Benefit:** Removes a false-success from the protocol surface; timestamp-preserving copies either work or fail visibly.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED)

### PYSMB2-05 — No throttling of failed logons, and the NTLM challenge survives a failed AUTHENTICATE
- **Category:** 6
- **Locations:** smb2_server/smb2_server.py:871-905 (AUTHENTICATE path; failure raises and leaves connection state untouched), smb2_server/smb2_server.py:907-909 (`conn.challenge` is cleared only on success), smb2_auth.py:262-324 (`verify` keeps no attempt state)
- **Problem:** A failed SESSION_SETUP costs the attacker nothing: no delay, no per-connection or per-account failure counter, no teardown after repeated failures, and the same server challenge remains valid, so the client can retry AUTHENTICATE against it immediately, indefinitely. Grep-verified: no sleep, counter or lockout exists anywhere in the auth path (the only `time.sleep` calls in the file are in the Windows take-445 service choreography). Online password guessing against the one configured user is therefore limited only by round-trip time.
- **Fix:** Count consecutive failures per connection in `h_session_setup`; clear `conn.challenge` on any failed AUTHENTICATE (forcing a fresh NEGOTIATE round) and drop the connection after a small fixed number of failures.
- **Justification:** (only if fix ADDS) The counter is a few lines of per-connection state, not a new subsystem, and it is the only mechanism that makes online guessing expensive; NTLMv2's offline-guess weakness is documented in smb2_auth.py as unfixable from this side, which makes the online path the one worth closing.
- **Benefit:** Failed logons stop being a free, repeatable oracle against a single static challenge.
- **Effort:** M · **Risk:** Med · **Confidence:** Med (READ)

### PYSMB2-06 — LOGOFF does not retire the SessionId; the next session on the connection silently reuses it
- **Category:** 6
- **Locations:** smb2_server/smb2_server.py:927-934 (`h_logoff` clears handles, trees and `authenticated` only), smb2_server/smb2_server.py:863 and smb2_server/smb2_server.py:915 (a session id is minted only `if conn.session_id == 0`)
- **Problem:** After LOGOFF, `conn.session_id` keeps the logged-off session's value, so the next successful SESSION_SETUP on the same connection is assigned the *same* SessionId as the session that was just ended. SessionIds are how clients key per-session state (including signing state); reissuing one invites exactly the old/new session confusion the id exists to prevent. The signing key's survival past LOGOFF is commented as deliberate for signing the LOGOFF reply itself — but nothing clears the id afterwards either.
- **Fix:** Set `conn.session_id = 0` at the end of `h_logoff` (the reply's header is built from the value the dispatcher already captured for this message — verify against `_dispatch_all`'s use of `conn.session_id or hdr.session_id` when making the change, and if the reply needs the old id, clear it in `_dispatch_all` after the LOGOFF reply is packed instead).
- **Benefit:** Each session gets the unique id the protocol promises; no state keyed by SessionId can alias across a logoff boundary.
- **Effort:** S · **Risk:** Low · **Confidence:** Med (READ)

### PYSMB2-07 — TREE_DISCONNECT, ECHO and LOGOFF answer SUCCESS with no session at all
- **Category:** 6
- **Locations:** smb2_server/smb2_server.py:974-976 (`h_tree_disconnect`: pops a tree id, returns SUCCESS, no `_require_session`/`_tree`), smb2_server/smb2_server.py:1363-1364 (`h_echo`: returns SUCCESS unconditionally), smb2_server/smb2_server.py:927-934 (`h_logoff`)
- **Problem:** Every other stateful handler enforces a session via `_require_session`/`_tree`; these three do not. Executed proof: on a fresh, never-authenticated connection all three return STATUS_SUCCESS — including TREE_DISCONNECT for tree id 99, a tree that never existed. The impact is state-machine laxness rather than data exposure (nothing is served), but a server that reports success for operations on nonexistent state masks client bugs and makes its own session accounting (the `--session-summary` counters key off these commands) unreliable.
- **Fix:** Have `h_tree_disconnect` go through `_require_session` and report STATUS_NETWORK_NAME_DELETED for an unknown tree id, like `_tree` does; leave ECHO's behaviour a deliberate, commented choice if unauthenticated keepalive is actually wanted.
- **Benefit:** Success starts meaning the named thing existed and the caller was entitled to act on it.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED)

### PYSMB2-08 — The bcrypt CMAC self-test compares against the wrong constant: it is the CMAC *L* block, not a CMAC
- **Category:** 6
- **Locations:** smb2_crypto.py:227-232
- **Problem:** `_bcrypt_cmac`'s sanity check expects `cmac(zero key, b"")` to equal `66e94bd4…2e`. Executed on the /tmp copy: that constant is `AES-128(zero key, zero block)` — the *L* subkey block from RFC 4493 §2.3 — while the actual CMAC of the empty message under the zero key is `4387c14b…f9` (pure-Python and OpenSSL backends agree). So the first clause of the check is false for every correct bcrypt backend, forever, and the backend is accepted only because the condition is an `and` whose second clause compares against the pure implementation. The test as written can never pass on its own terms; it survives by accident of its fallback, and a reader trusting the constant would "fix" a working backend into rejection.
- **Fix:** Replace the constant with the real zero-key empty-message CMAC (`4387c14b46ef7e176dceefa862d72ff9`), or drop the hardcoded clause and keep only the pure-implementation comparison the OpenSSL path already uses (`smb2_crypto.py` `_openssl_cmac`) — one self-test mechanism, not two divergent ones.
- **Benefit:** The Windows signing path's only startup correctness gate tests what it claims to test.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED for both values; the bcrypt branch itself is Windows-only and was READ, not run)

### PYSMB2-09 — A malformed KEY_EXCH wrapped key silently falls back to the SessionBaseKey
- **Category:** 6
- **Locations:** smb2_spnego.py:330-341
- **Problem:** `exported_session_key` unwraps the client's session key only when NEGOTIATE_KEY_EXCH was negotiated *and* the wrapped key is exactly 16 bytes; otherwise it silently returns the SessionBaseKey. Executed proof: a Credentials with KEY_EXCH set and a 5-byte wrapped key returns the base key unchanged, with no error. If KEY_EXCH was negotiated, the client derived its signing key from the random key it sent — so server and client now hold different session keys and every signed request fails later as ACCESS_DENIED, far from the cause; if the client does not sign, the session proceeds on a key the client did not choose, with nothing logged.
- **Fix:** When KEY_EXCH is negotiated but the wrapped key is not 16 bytes, fail the session setup (raise AuthError / LOGON_FAILURE at the call site in `h_session_setup`, smb2_server.py:901) instead of substituting a different key.
- **Benefit:** A key-agreement failure fails at key agreement, with the right status, instead of surfacing as unexplained signature rejections.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED)

### PYSMB2-10 — A corrupt trailing segment in a compound request discards every reply, including successes already computed
- **Category:** 6
- **Locations:** smb2_server/smb2_server.py:429-434
- **Problem:** `_dispatch_all` accumulates replies in a local list and sends them only after the whole compound is walked. If `parse_header` fails on any segment after the first, the function simply `return`s — the accumulated replies are never sent. Executed proof: a compound of one valid ECHO request followed by 64 zero bytes produced **zero** bytes sent on the socket. The client, which did nothing wrong in its first request, sees a hang/timeout rather than its reply plus an error. (The same early return is also what a truncated final segment produces.)
- **Fix:** On a mid-compound parse failure, stop walking, append an error reply for the bad segment if a header boundary is known, and send whatever replies were accumulated instead of dropping them.
- **Benefit:** One bad segment costs one error reply, not the whole compound and a client-side timeout.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED)

### PYSMB2-11 — QUERY_DIRECTORY returns an entry larger than the client's OutputBufferLength
- **Category:** 6
- **Locations:** smb2_server/smb2_server.py:1258-1263
- **Problem:** The packing loop breaks on buffer exhaustion only `and entries` — i.e. the first entry is always emitted even when it alone exceeds the client's declared output length. Executed proof: with `out_len = 16`, the server returned STATUS_SUCCESS with OutputBufferLength = 96. A client that sized its buffer by its own request (libsmb2 and the PS2 browse drivers this server exists for work in fixed buffers) is handed more bytes than it agreed to receive.
- **Fix:** If the first entry does not fit, answer STATUS_INFO_LENGTH_MISMATCH (already defined and already used by `h_query_info`) instead of overrunning the declared length.
- **Benefit:** The server stays inside the buffer contract every other length in the protocol is checked against (`request_buffer`, MAX_READ/WRITE).
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED)

### PYSMB2-12 — QUERY_DIRECTORY's pattern filter is bypassed for "." and ".."
- **Category:** 6
- **Locations:** smb2_server/smb2_server.py:1204-1207 (`_listing_for` unconditionally keeps `names[:2]`)
- **Problem:** When a client asks for a filtered listing, the dot entries are kept regardless of the pattern. Executed proof: pattern `*.iso` returned entries `['.', '..', 'onlyfile.iso']`. Clients that trust the server-side filter (rather than re-filtering) show phantom entries for every filtered browse; a client computing "does this directory contain an .iso" from the raw listing gets a wrong answer shape. The dot entries themselves are deliberate (see the `_listing_for` docstring) — bypassing the client's filter for them is the defect, not their existence.
- **Fix:** Keep the dot entries only when the pattern matches them too (or when there is no filter), i.e. run all names through the same `fnmatch` test.
- **Benefit:** A filtered listing is filtered; unfiltered listings keep the dot entries some clients need.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED)

### PYSMB2-13 — A zero-length READ is answered STATUS_END_OF_FILE
- **Category:** 6
- **Locations:** smb2_server/smb2_server.py:1146-1153 (file branch), smb2_server/smb2_server.py:1137-1139 (pipe branch answers STATUS_PIPE_EMPTY for a zero-length read)
- **Problem:** `h_read` treats "no bytes came back" as EOF, but a request with Length = 0 legitimately returns no bytes at any offset. Executed proof: a zero-length READ on a handle positioned at offset 0 of a non-empty file raised STATUS_END_OF_FILE ("read past the end"). The comment at 1151-1152 justifies EOF for a zero-length *success*, but the conflation punishes a well-formed zero-length request with an error that tells the client the file ended. The pipe branch has the same shape: a zero-length pipe READ is PIPE_EMPTY rather than a successful empty read.
- **Fix:** Special-case `length == 0` before opening the file / reading the pipe and return STATUS_SUCCESS with an empty data buffer; keep END_OF_FILE for a non-zero read that hits the end.
- **Benefit:** Zero-length probes (a normal client pattern) stop looking like end-of-file.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED)

### PYSMB2-14 — NEGOTIATE silently accepts a dialect list shorter than DialectCount claims
- **Category:** 6
- **Locations:** smb2_server/smb2_server.py:748-757
- **Problem:** The dialect loop `break`s when the body runs out and negotiation proceeds with whatever prefix was present. Executed proof: a NEGOTIATE claiming DialectCount = 3 but carrying one dialect (2.1) was accepted with STATUS_SUCCESS and dialect 0x0210. A truncated or lying NEGOTIATE is malformed input; accepting a prefix of it means the negotiated dialect — and the `negotiate_claims` tuple stored for FSCTL validation at line 755 — are built from a message the server knows was incomplete, and the stored claims enshrine the truncated list as "what the client offered".
- **Fix:** If fewer than `dialect_count` dialects fit in the body, raise STATUS_INVALID_PARAMETER like every other short request in the file.
- **Benefit:** Malformed negotiation fails at negotiation, and the stored claims always describe a complete message.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED)

### PYSMB2-15 — FLUSH cannot fail: it is the only handle handler with no body-length check, and it swallows fsync errors
- **Category:** 6
- **Locations:** smb2_server/smb2_server.py:1102-1111 (contrast the length check every sibling makes before touching the FileId, e.g. `h_read` at smb2_server/smb2_server.py:1132-1133)
- **Problem:** Two ways FLUSH reports success without the work being known-good. (1) It slices `body[8:24]` and calls `handle_from` with no preceding length check; executed proof: an 8-byte body makes `h_flush` raise a bare `struct.error` ("unpack requires a buffer of 16 bytes"), which only the dispatcher's catch-all turns into STATUS_INVALID_PARAMETER — the handler's own contract (raise Smb2Error with a chosen status) is broken. (2) `fh.flush()` + `os.fsync()` failures are caught and discarded (`except OSError: pass`), so a client that issued FLUSH to learn its data is durable is told STATUS_SUCCESS even when the fsync failed.
- **Fix:** Add the same `len(body) < 24` check the other handlers open with, and let an fsync failure propagate as an OSError so `_dispatch` maps it to a real status (`_status_for_oserror`) instead of success.
- **Benefit:** FLUSH's success means the data was flushed; malformed FLUSHes fail with the handler's chosen status, not the catch-all's.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED)

### PYSMB2-16 — Receive framing has no size cap, and `_recv_exact` concatenates quadratically
- **Category:** 4
- **Locations:** smb2_wire.py:37-53 (`recv_msg`: 24-bit length trusted, up to 16,777,215 bytes), smb2_wire.py:27-34 (`_recv_exact`: `buf += chunk` in a loop)
- **Problem:** The server advertises MAX_TRANSACT / MAX_READ / MAX_WRITE of 1 MiB (`smb2_wire.py:204-206`) but the framing layer will buffer a message sixteen times that size from any unauthenticated peer — the length is read and honoured before any header, dialect or session check exists. Each connection thread can thus be made to allocate ~16 MiB on a peer's say-so, and `_recv_exact` builds that buffer by repeated `bytes` concatenation, which copies the accumulated prefix on every `recv` — O(n²) copying for large messages (a 1 MiB legitimate message arrives as many chunks).
- **Fix:** Cap the framed length in `recv_msg` (the advertised 1 MiB plus header/compound headroom is the natural bound; a peer exceeding it is not speaking this server's protocol) and accumulate `_recv_exact` into a list or `bytearray`, joining once.
- **Benefit:** Per-connection memory becomes bounded by a number the server itself advertised, and large legitimate transfers stop paying quadratic copy costs.
- **Effort:** S · **Risk:** Low · **Confidence:** Med (READ)

### PYSMB2-17 — `send_msg` raises OverflowError for a reply over 16 MiB, killing the connection thread
- **Category:** 6
- **Locations:** smb2_wire.py:23-24 (`len(msg).to_bytes(3, "big")`), smb2_server/smb2_server.py:417-530 (`_dispatch_all` builds the compound reply `out` with no size bound), smb2_server/smb2_server.py:385-415 (`_serve_client` catches connection errors and OSError only)
- **Problem:** The 24-bit framing length overflows at 16,777,216 bytes. Executed proof: `send_msg` with a 16 MiB + 1 message raises `OverflowError: int too big to convert`. A single READ reply cannot reach that size (MAX_READ is 1 MiB), but a compound reply is the sum of its parts and is built with no bound, so a client chaining enough large READs in one compound makes the server compute a full reply and then die mid-send — and because OverflowError is not an OSError, it escapes `_serve_client`'s handlers entirely and kills the thread with a traceback instead of closing the connection cleanly.
- **Fix:** Check the length in `send_msg` and raise a defined error (or cap compound reply size in `_dispatch_all`); at minimum, make the failure an OSError subclass so the existing connection teardown handles it.
- **Benefit:** An oversized compound degrades to a dropped connection with a log line, not an unhandled thread death.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED for the OverflowError; thread death INFERRED from the `except` clauses read at 385-415)

### PYSMB2-18 — No resource limits: unbounded connection threads, no timeout on accepted sockets, unbounded handles and trees per connection
- **Category:** 4
- **Locations:** smb2_server/smb2_server.py:337-362 (`serve_forever` spawns a daemon thread per connection, no cap), smb2_server/smb2_server.py:385-404 (`_serve_client`: the accepted socket gets TCP_NODELAY at :390 and no timeout — the only `settimeout` in the server is on the *listening* socket at :342), smb2_server/smb2_server.py:245-251 (`add_handle` never refuses), smb2_server/smb2_server.py:949-972 (TREE_CONNECT allocates a tree per request, no cap)
- **Problem:** A peer can (a) open connections without limit, each costing a thread blocked forever in `recv_msg` even if it never sends a byte; (b) on one authenticated connection, CREATE handles and TREE_CONNECT trees without limit, growing `conn.handles` / `conn.trees` until the process runs out of memory or descriptors (handles hold no descriptor until first use, but each is an object plus, for directories, a listing snapshot). Nothing in the serving path — grep-verified — sets a client-socket timeout or counts connections, handles or trees. For a server whose deployment is "a PC on a LAN", one misbehaving or hostile client is a whole-process denial of service.
- **Fix:** Set a generous idle timeout on accepted sockets and a per-connection cap on handles/trees (refusing with the already-defined STATUS_INSUFFICIENT_SERVER_RESOURCES); a global connection cap can be a simple counter in `serve_forever`.
- **Justification:** (only if fix ADDS) These are three small checks at three existing allocation points, not a resource-management subsystem; each reuses a status the wire module already defines, and the alternative — documenting "one bad client kills the server" — leaves the stated LAN deployment one port-scan away from a hang.
- **Benefit:** Worst-case memory/thread usage becomes a function of configured caps, not of peer behaviour.
- **Effort:** M · **Risk:** Med · **Confidence:** Med (READ)

### PYSMB2-19 — Handles name paths, not files: lazy open-by-path serves whatever the path names at first use
- **Category:** 6
- **Locations:** smb2_server/smb2_server.py:1114-1127 (`_open_file` opens `h.path` on first READ/WRITE), smb2_server/smb2_server.py:1049-1053 (CREATE records the path and opens nothing), smb2_server/smb2_server.py:1069-1088 (CLOSE stats and deletes by path), smb2_server/smb2_server.py:1290-1293 (QUERY_INFO stats by path)
- **Problem:** A handle pins a pathname, not a file. Between CREATE and first use, anything that replaces the file at that path — another SMB client renaming over it, a local process, a sync tool — silently retargets the handle. Executed proof: a handle created on a file containing `ORIGINAL` returned `REPLACED!` from `h_read` after the file was swapped with `os.replace`, with no error and no new CREATE. The mirror cases follow from the same mechanism: a file deleted and recreated is read as the new file; QUERY_INFO and CLOSE's post-query attributes describe whatever is at the path *now*, not what was opened; delete-on-close can remove a file the handle's creator never opened. The `_open_file` docstring gives the reason for laziness (descriptor pressure from Explorer's attribute-only opens) — the defect is not laziness itself but that the CREATE-time `os.stat` (smb2_server.py:1042) is never compared against the file eventually opened.
- **Fix:** Keep the lazy open, but remember the CREATE-time identity (`st_dev`, `st_ino`) on the handle and verify it at first `_open_file`; on mismatch, fail with STATUS_FILE_CLOSED instead of serving the replacement. That keeps the descriptor saving and closes the retargeting.
- **Justification:** (only if fix ADDS) Two integers per handle and one comparison at the single lazy-open point — no new abstraction, and it converts a silent wrong-file serve into a clean error.
- **Benefit:** A handle means the file that was opened, across the whole multi-client lifetime of the share.
- **Effort:** M · **Risk:** Med · **Confidence:** High (EXECUTED)

### PYSMB2-20 — FileAccessInformation always reports full access (0x001F01FF)
- **Category:** 6
- **Locations:** smb2_wire.py:437-439
- **Problem:** `file_info` answers FILE_ACCESS_INFORMATION with the constant 0x001F01FF (FILE_ALL_ACCESS) for every file, in every share, through every handle — including on a read-only share and through a handle the same server would refuse a WRITE on (executed proof: queried with `read_only=True`, the mask returned is still 0x001F01FF). Clients use this class to decide what they may attempt; telling every client it has full access manufactures the very ACCESS_DENIED / MEDIA_WRITE_PROTECTED errors the access mask exists to prevent, and misleads any client that caches permissions from it.
- **Fix:** Derive the mask from the state the caller already passes in: when `read_only` is set, return the read/execute subset (the same 0x001200A9 shape `h_tree_connect` already uses for read-only shares at smb2_server.py:969) instead of full access.
- **Benefit:** The reported access agrees with the access the server will actually enforce.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED)

### PYSMB2-21 — Rename ignores the RootDirectory field, so a directory-relative rename lands at the share root
- **Category:** 6
- **Locations:** smb2_server/smb2_server.py:1340-1357
- **Problem:** FileRenameInformation carries a RootDirectory handle (payload bytes 8–15): when it is non-zero, the FileName is relative to that directory. The handler reads only ReplaceIfExists (byte 0) and FileNameLength (offset 16), never looks at bytes 8–15, and resolves the name against the share root (`h.share.resolve(target)`). A client performing a rename relative to an open directory handle therefore moves its file to the share root instead — and with ReplaceIfExists set, can overwrite an unrelated same-named file at the root. The raw target string is also stored verbatim as `h.rel` (line 1356), so a later QUERY_INFO reports the un-normalised name.
- **Fix:** If the RootDirectory field is non-zero, either resolve the name relative to that handle's directory (the handles table is right there) or refuse with STATUS_NOT_SUPPORTED; do not silently reinterpret the name against a different base. Store the share-relative result of the resolution as `h.rel`, not the raw client string.
- **Benefit:** A rename goes where the client addressed it, or fails honestly — never to a different directory by default.
- **Effort:** S · **Risk:** Low · **Confidence:** Med (READ)

### PYSMB2-22 — The account password is taken on the command line, where any local user can read it
- **Category:** 6
- **Locations:** smb2_server/smb2_server.py:1741-1742 (`--user NAME:PASSWORD`), smb2_server/smb2_server.py:1708-1724 (`build_authenticator` splits the spec), smb2_server/smb2_server.py:44 (the module docstring's own example shows `--user ripto:secret`)
- **Problem:** A password in argv is visible in the process list (`ps`, Task Manager's command-line column), in shell history, and in any launcher log that records how the server was started. The server is normally started by a GUI launcher on a shared family PC — exactly the setting where other local users/processes can see a process command line. This is the one place credentials exist outside the NTLM exchange, and it is the least protected.
- **Fix:** Accept users from a file (e.g. `--users-file` with `NAME:PASSWORD` lines, read once at startup) in addition to `--user`, and steer the launcher/docstring example to it; keep `--user` working for compatibility.
- **Justification:** (only if fix ADDS) One additional way to read the same `user_specs` list `build_authenticator` already consumes — no new config format or subsystem — and it removes the password from the one place it is broadcast to every local process.
- **Benefit:** The share password stops being world-readable on the host for the whole life of the server process.
- **Effort:** M · **Risk:** Low · **Confidence:** Med (READ)

### PYSMB2-23 — Dead code with zero callers anywhere in the repo
- **Category:** 1
- **Locations:** smb2_crypto.py:186-189 (`native_cmac_name`), smb2_wire.py:120 (`STATUS_PENDING`), smb2_wire.py:181 (`FILE_SUPERSEDED`), smb2_wire.py:200 (`GLOBAL_CAP_DFS`), smb2_srvsvc.py:28 (`PTYPE_BIND_NAK`)
- **Problem:** A repo-wide grep census (production code and tests) finds exactly one reference to each of these: its own definition. `native_cmac_name` even carries a "For logs" docstring, but no log call uses it — the one place that would want it (startup, to say which CMAC backend is in force) never calls it. The four constants are protocol values no code path emits or compares. Each is small; together they are the kind of inventory that makes a reader check whether a status or packet type is handled when it is not. (Checked and *not* dead, for contrast: `Share.relative`, `Share.resolve(must_exist=…)`, `blob_timestamp` and `lmv2_response` have no production callers either, but tests exercise them, so they are test surface, not dead code.)
- **Fix:** Delete the five definitions. If the CMAC backend name is worth having, call it in the startup banner instead of keeping it for a caller that does not exist.
- **Benefit:** Five fewer symbols a reader must chase to discover nothing uses them.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ — grep census, counted not estimated)

### PYSMB2-24 — The session-id minting line is duplicated inside `h_session_setup`
- **Category:** 2
- **Locations:** smb2_server/smb2_server.py:863, smb2_server/smb2_server.py:915
- **Problem:** `conn.session_id = int.from_bytes(os.urandom(8), "little") or 1`, guarded by the same `if conn.session_id == 0`, appears verbatim in both the NTLM-NEGOTIATE branch and the AUTHENTICATE branch of the same function. It is the kind of line that gets fixed in one copy (e.g. as part of PYSMB2-06's logoff change) and not the other.
- **Fix:** Mint the id in one place — e.g. a tiny `Connection.ensure_session_id()` next to `add_handle`, or a single check after the branch dispatch in `h_session_setup` before the reply is built.
- **Benefit:** One definition of how a session gets its id; the two branches cannot drift.
- **Effort:** S · **Risk:** Low · **Confidence:** High (READ)

### PYSMB2-25 — `_local_ipv4_addresses` runs three enumeration mechanisms to print one cosmetic warning line
- **Category:** 3
- **Locations:** smb2_server/smb2_server.py:76-128 (getaddrinfo pass, then a POSIX `fcntl` SIOCGIFADDR per-interface ioctl pass, then a UDP connect-to-TEST-NET probe), consumed at smb2_server/smb2_server.py:131-147
- **Problem:** ~53 lines and three fallback strategies exist solely to fill in "Reachable on: …" in the open-share warning. The code itself concedes the list is best-effort and that "the --bind advice is the load-bearing half" — the addresses change nothing the operator does; the actionable content is the second line, which needs no enumeration at all. The ioctl pass is also the only `fcntl` use in the server, with its own platform guards to maintain.
- **Fix:** Keep the getaddrinfo pass and the --bind advice; delete the ioctl enumeration and the connect probe. If the list comes back empty, the warning loses nothing it could act on.
- **Benefit:** Removes a platform-specific syscall path and a network-stack probe from startup, for a banner line.
- **Effort:** S · **Risk:** Low · **Confidence:** Med (READ)

### PYSMB2-26 — No native CMAC backend is probed on macOS, so signed SMB3 there runs on the pure-Python AES
- **Category:** 5
- **Locations:** smb2_crypto.py:173-184 (`_native_cmac` probes win32 → bcrypt, linux → OpenSSL, nothing else), smb2_crypto.py:15-22 (module docstring: pure-Python AES is "under a megabyte per second, and an SMB 3.x signature covers every byte of every READ reply")
- **Problem:** On macOS the backend probe returns None by construction (the comment explains loading Apple's libcrypto aborts the process), so every signed SMB3 message is CMAC'd by the pure-Python AES at the throughput the docstring itself calls a crawl. Windows and Linux get OS crypto; macOS silently gets a ~100× slower signing path with no startup note saying so.
- **Fix:** Either probe a macOS-native route that does not load libcrypto (e.g. CommonCrypto via ctypes), or state the limitation where the operator will see it — the startup banner that already reports dialect and shares — so a slow macOS SMB3 server is a known property, not a mystery.
- **Justification:** (only if fix ADDS) The cost is quantified in the module's own docstring and falls on the server's stated purpose (copying games through Explorer); the banner-only variant adds one line and no mechanism at all.
- **Benefit:** Signed SMB3 on macOS is either fast or honestly labelled slow at startup.
- **Effort:** M · **Risk:** Low · **Confidence:** Med (READ)

### PYSMB2-27 — `check_component("")` raises IndexError instead of PathError
- **Category:** 6
- **Locations:** smb2_paths.py:70-102 (the crash is the `part[-1]` test at smb2_paths.py:97)
- **Problem:** The path guard's component check documents "Raises PathError" as its contract, but for an empty component it falls through every test and dies at `part[-1]` with `IndexError: string index out of range` — executed proof on the /tmp copy. Inside this scope the crash is unreachable (`Share.resolve` skips empty components at smb2_paths.py:148-150 before calling it), but the function exists precisely to be the reusable guard — its docstring says it is separated out so other servers (the NFS server's READDIR path) can call it per directory entry — and a guard that raises the wrong exception type defeats the caller's `except PathError` handling wherever it is reused without resolve's pre-filter.
- **Fix:** Reject an empty component at the top of `check_component` with the same PathError the other refusals use.
- **Benefit:** The guard's contract holds for every input, not just the inputs its current in-scope caller happens to pre-filter.
- **Effort:** S · **Risk:** Low · **Confidence:** High (EXECUTED)

### PYSMB2-28 — The srvsvc pipe does not reassemble a PDU split across WRITEs (or across RPC fragments)
- **Category:** 8
- **Locations:** smb2_srvsvc.py:134-137 (`SrvsvcPipe.write` hands each WRITE straight to `transact`), smb2_srvsvc.py:144-165 (`transact` requires one complete PDU per call: `frag_len > len(data)` is an error, and a REQUEST without FIRST+LAST fragment flags gets a fault)
- **Problem:** SMB pipe semantics let a client write a request in several WRITEs, and DCE/RPC lets it fragment one request across PDUs; this pipe implements neither — each WRITE must be a whole PDU, and each PDU must be a whole request. The comment at the fragment check frames a fragmented request as "a client this server does not understand", which is true of the clients tested (libsmb2, Explorer, smbclient all send small single writes), but it makes the one pipe the server has a partial implementation of its own transport: a conforming client that splits a bind or a request — legal on both layers — gets INVALID_PARAMETER / a fault instead of a share list.
- **Fix:** Buffer incoming bytes in `write` until a complete PDU (per its own frag_len) is present before calling `transact`; that covers the WRITE-split case with a few lines. Leave REQUEST-fragment reassembly unimplemented, but say so at the fault site as a known limit rather than a client error.
- **Benefit:** The pipe accepts the write patterns its transport permits, and its one real limit is documented where it triggers.
- **Effort:** M · **Risk:** Low · **Confidence:** Med (READ)

---

## Checked and cleared (verified non-findings)

- **test_session_cap.py vs production:** the tests target base `UdpfsServer._get_or_create_session`, and the shipped `AutoUdpfsServer._get_or_create_session` (`udpfs_server/ps2servers_core.py:185-186`) delegates via `super()` — the base cap logic *is* the shipped path.
- **test_protocol_mode_selector.py:** its hardcoded wire set `{"auto","standard","modulo"}` matches production (`udpfs_server/ps2servers_core.py:111` and the argparse `choices` at :548). Its LegacyKeySurvivesTheRealLoadPath class explicitly documents and repairs an earlier pass-for-wrong-reason pattern — good practice.
- **test_router_status.py's hardcoded `DEFAULT_FALLBACK_SECONDS = 0.25`** matches production (`udpfs_server/ps2servers_core.py:57`) — agreement, not drift (the comment in the test admits the duplication).
- **test_ps1_vcd.py's patch of `launcher.ps1_vcd.hash_vcd`** is effective, not vacuous: `launcher/game_library.py:382-384` imports `hash_vcd` from the module at call time inside `hash_image`, so the patched attribute is the one called.
- **Duplication census (AST body-hash, scripted):** zero exact-duplicate test-method bodies across the 84 files; duplication exists only at helper level (PYTESTB-11/12).
- **Strong real coverage, no finding:** test_nfs4_server.py (independent XDR encoding in the test, drives real `Nfs4Server.handle_record`; independence rationale stated at lines 11-12), test_smb2_server.py's collected classes (real sockets, own client), test_smbv1_logon_required.py (real `serve_conn` over socketpair), test_ra_owned_service.py / test_ra_resolver_process.py (real UDP loopback / real subprocess), test_theme.py (sentinel-palette patch proves a live read), test_udpfs_symlink_path_ops.py and test_udpfs_write_needs_writable_handle.py (real handlers / real receive loop, both server classes), test_smb2_independent_client.py (independent smbprotocol client; skips only when the library is absent). test_smb2_wire.py's docstring honestly discloses that its StructureSize pins are transcribed from the implementation rather than re-derived from the spec — a disclosed limitation, not hidden vacuity. test_smb2_signed_sessions.py's shared-`smb2_crypto` circularity is mitigated by published-vector known-answer tests in test_smb2_signing.py.

---

## Files not read

Per-section, in-scope remainders (verbatim from the section files):

- **pysmb2.md:** - None. All seven in-scope source files were read completely.
- `smb2_server/__pycache__/*.pyc` (7 compiled caches) were not read; they are not source, are not tracked in git (`git ls-files smb2_server/` lists only the seven `.py` files), and no finding relies on them.
- **pysmb1core.md:** - None for scope items (a) and (b): both in-scope files were read fully. (The task's literal path `smbv1_server/smbv1_server.py` does not exist at this pin — see the scope note at the top; `smbv1_server/smbserver_opl.py` was audited in its place.)
- Not read, and not needed for any finding: the rest of `smbv1_server/` (`README.md`, `Start-SMB-Server.bat`, `build_exe.ps1`) and the rest of `udpfs_server/` beyond the cross-check ranges listed above (`udpfs_server.py` in full, `router_status.py`, the `compressed_iso/` package, the `*_selftest.py` files).
- **pyudpfs.md:** - None. Every file named in the scope was read in full. `multiclient_selftest.py` was read in full but could not be *executed* in this sandbox (UDP `sendto` blocked — BLOCKED-ENV, `PermissionError: [Errno 1]`); `compression_selftest.py` and `pathguard_selftest.py` were executed green in a `/tmp` copy.
- **pysmall.md:** - None. All in-scope `.py` files (`udpbd_server/udpbd_server.py`, `udpbd_server/selftest.py`, `http_server/http_server.py`, `nfs4_server/nfs4_server.py`) were read in full. The only other in-scope file, `udpbd_server/SOURCE.md`, was also read. (`__pycache__/*.pyc` binaries under the three directories are untracked build artifacts, not source, and were not read.)
- **goproto.md:** None. Every non-test `.go` file in the four in-scope directories was read in full. (Test files `*_test.go` and the non-Go file `internal/compression/chd_libchdr.h` were excluded by the scope definition; `chd_libchdr.h` is a C header for the cgo binding, not Go source.)
- **gocore.md:** - None. Every non-test `.go` file in scope was read in full. (`*_test.go` files were excluded by the task definition and were only grep-searched for caller censuses.)
- **lncha.md:** None — every file in the assigned scope was read fully.
- **lnchb.md:** None — all 28 in-scope files were read in full.
- **lnchc.md:** - None. Every file named in the scope was read fully.
- **cidocs.md:** - `tools/remove-windows-firewall-rules.ps1` — PowerShell helper, listed in tools/ but not opened; no docs/CI claim about it was in scope of the claims checked.
- `conformance/integration/` runner bodies (`opl_http/run.py`, `riptopl_http/run.py`, `neutrino_smb/run.py`, `neutrino_nfs/run.py`, `libsmb2/run.py`, `udpfs_probe.py`, `udpbd_probe.py`, `udpfs_parity.py`) — read for wiring/line counts and CI invocation only, not line-by-line; their internal correctness belongs to the protocol scopes.
- `docs/*.html` bodies (22 files) — inventoried, categorised via `tools/build_docs_index.py`'s page map and spot-read (`editions.html` head); not read in full.
- Remaining `docs/*.md` beyond the Edge/editions/instructions set (RA-*, UDPFS-* handoffs, plans) — inventoried for the duplication census; their protocol claims belong to other scopes.
- `build/native/`, `build/__pycache__/`, `tools/__pycache__/` in the working tree — untracked local artefacts; `git ls-files` confirms no `__pycache__`/`.pyc`/binary build output is tracked (census clean, no finding).
- `.github/scripts/check_ai_attribution.py` — exercised only via its workflow; attribution logic is outside this scope.
- **gotests.md:** None. Every `*_test.go` file in scope was read in full. Binary fixtures under
`internal/compression/testdata/` (`test.iso`, `test_cd.chd`, `test_dvd.chd`) were
listed but not read — they are data, not test code. Production files other than the
spot-checks listed above were not read; this section's scope was the test suite.
- **pytesta.md:** None — every file in the assigned scope (tests/library_fixtures.py, tests/__init__.py, and all tests/test_*.py from test_about_page.py through test_netinfo_and_smb.py inclusive, 38 files, 8,934 lines) was read in full. All other test files (test_nfs4_server.py onward, o–z) belong to the second half and were not read, except the baseline-tracing excerpt of tests/test_status_client.py noted above.
- **pytestb.md:** None — all 84 in-scope test files were read fully.

Repo-level files no section line-read (outside every section's scope, or censused only):
- `vendor/retroachievements/rcheevos/**` — vendored upstream C library (~50 .c/.h files): not line-read; treated as vendored-pristine, only its integration surface (launcher/ra_*.py, build/build_ra_evaluator.py, native/ra_watch_probe.c build wiring) was in scope.
- `native/ra_watch_probe.c` and `conformance/**/*.c`, `conformance/**/*.c.in` — small C probes/templates: build-wiring checked by the CI/docs pass, bodies not line-audited.
- `conformance/fixtures/handshake_cases.json` — data file, consumed by tests; not independently audited.
- `docs/*.html` (22 files) — covered as claims only where the CI/docs pass compared them against code; not proofread line-by-line (see the dual-docs finding).
- `docs/*.md` beyond the defaults/claims spot-checks (RA-*.md, UDPFS-*.md handoff/diagnostic notes, PLAN-*.md, NETWORK-TOOLS-ROADMAP.md) — read for claims about shipped behaviour, not audited as code.
- `launcher/assets/**` binary contents — censused by script (4 files, all referenced); image bytes not inspected beyond size/dimensions.
- `tests/fixtures/**` binary fixtures and `ps2serversgithubbanner.png` — censused for size/duplication only.
- Root meta files (`AGENTS.md`, `CLAUDE.md`, `.coderabbit.yaml`, `.claude/settings.json`, `NOTICE.md`, `ANTIVIRUS-NOTICE.md`, `SECURITY.md`) — read as claims where relevant; not findings-bearing code. Licences were not audited (settled).
- `__pycache__/` directories — generated bytecode, not source; not read.

---

## Appendix — section-to-final ID map

| Final | Filed as | Section |
|---|---|---|
| RE-SERV-001 | PYSMB2-01 | pysmb2.md |
| RE-SERV-002 | PYSMB2-02 | pysmb2.md |
| RE-SERV-003 | PYSMB2-03 | pysmb2.md |
| RE-SERV-004 | PYSMB2-04 | pysmb2.md |
| RE-SERV-005 | PYSMB2-05 | pysmb2.md |
| RE-SERV-006 | PYSMB2-06 | pysmb2.md |
| RE-SERV-007 | PYSMB2-07 | pysmb2.md |
| RE-SERV-008 | PYSMB2-09 | pysmb2.md |
| RE-SERV-009 | PYSMB2-10 | pysmb2.md |
| RE-SERV-010 | PYSMB2-11 | pysmb2.md |
| RE-SERV-011 | PYSMB2-12 | pysmb2.md |
| RE-SERV-012 | PYSMB2-13 | pysmb2.md |
| RE-SERV-013 | PYSMB2-14 | pysmb2.md |
| RE-SERV-014 | PYSMB2-15 | pysmb2.md |
| RE-SERV-015 | PYSMB2-16 | pysmb2.md |
| RE-SERV-016 | PYSMB2-17 | pysmb2.md |
| RE-SERV-017 | PYSMB2-18 | pysmb2.md |
| RE-SERV-018 | PYSMB2-19 | pysmb2.md |
| RE-SERV-019 | PYSMB2-20 | pysmb2.md |
| RE-SERV-020 | PYSMB2-21 | pysmb2.md |
| RE-SERV-021 | PYSMB2-22 | pysmb2.md |
| RE-SERV-022 | PYSMB1-01 | pysmb1core.md |
| RE-SERV-023 | PYSMB1-02 | pysmb1core.md |
| RE-SERV-024 | PYSMB1-03 | pysmb1core.md |
| RE-SERV-025 | PYSMB1-06 | pysmb1core.md |
| RE-SERV-026 | PYSMB1-09 | pysmb1core.md |
| RE-SERV-027 | PYSMB1-21 | pysmb1core.md |
| RE-SERV-028 | PYUDPFS-01 | pyudpfs.md |
| RE-SERV-029 | PYUDPFS-02 | pyudpfs.md |
| RE-SERV-030 | PYUDPFS-03 | pyudpfs.md |
| RE-SERV-031 | PYUDPFS-04 | pyudpfs.md |
| RE-SERV-032 | PYUDPFS-05 | pyudpfs.md |
| RE-SERV-033 | PYUDPFS-06 | pyudpfs.md |
| RE-SERV-034 | PYUDPFS-07 | pyudpfs.md |
| RE-SERV-035 | PYUDPFS-14 | pyudpfs.md |
| RE-SERV-036 | PYSMALL-01 | pysmall.md |
| RE-SERV-037 | PYSMALL-04 | pysmall.md |
| RE-SERV-038 | PYSMALL-06 | pysmall.md |
| RE-SERV-039 | PYSMALL-10 | pysmall.md |
| RE-SERV-040 | GOPROTO-01 | goproto.md |
| RE-SERV-041 | GOPROTO-02 | goproto.md |
| RE-SERV-042 | GOPROTO-03 | goproto.md |
| RE-SERV-043 | GOPROTO-04 | goproto.md |
| RE-SERV-044 | GOPROTO-05, PYSMALL-02 | goproto.md |
| RE-SERV-045 | GOPROTO-07 | goproto.md |
| RE-SERV-046 | GOPROTO-17, PYSMB1-08 | goproto.md |
| RE-SERV-047 | GOCORE-02 | gocore.md |
| RE-SERV-048 | LNCHA-06 | lncha.md |
| RE-SERV-049 | LNCHB-02 | lnchb.md |
| RE-SERV-050 | LNCHB-06 | lnchb.md |
| RE-SERV-051 | LNCHB-13 | lnchb.md |
| RE-SERV-052 | CIDOCS-01 | cidocs.md |
| RE-SERV-053 | PYSMB2-08 | pysmb2.md |
| RE-SERV-054 | PYSMB2-27 | pysmb2.md |
| RE-SERV-055 | PYSMB1-04 | pysmb1core.md |
| RE-SERV-056 | PYSMB1-07 | pysmb1core.md |
| RE-SERV-057 | PYSMB1-18 | pysmb1core.md |
| RE-SERV-058 | PYSMB1-19 | pysmb1core.md |
| RE-SERV-059 | PYSMB1-20 | pysmb1core.md |
| RE-SERV-060 | PYUDPFS-08 | pyudpfs.md |
| RE-SERV-061 | PYUDPFS-09 | pyudpfs.md |
| RE-SERV-062 | PYUDPFS-13 | pyudpfs.md |
| RE-SERV-063 | PYUDPFS-15 | pyudpfs.md |
| RE-SERV-064 | PYSMALL-03 | pysmall.md |
| RE-SERV-065 | PYSMALL-07 | pysmall.md |
| RE-SERV-066 | PYSMALL-08 | pysmall.md |
| RE-SERV-067 | PYSMALL-09 | pysmall.md |
| RE-SERV-068 | GOPROTO-14 | goproto.md |
| RE-SERV-069 | GOCORE-01 | gocore.md |
| RE-SERV-070 | GOCORE-03 | gocore.md |
| RE-SERV-071 | GOCORE-07 | gocore.md |
| RE-SERV-072 | GOCORE-10 | gocore.md |
| RE-SERV-073 | LNCHA-01 | lncha.md |
| RE-SERV-074 | LNCHA-02 | lncha.md |
| RE-SERV-075 | LNCHA-03 | lncha.md |
| RE-SERV-076 | LNCHA-04 | lncha.md |
| RE-SERV-077 | LNCHA-05 | lncha.md |
| RE-SERV-078 | LNCHA-07 | lncha.md |
| RE-SERV-079 | LNCHB-01 | lnchb.md |
| RE-SERV-080 | LNCHB-04 | lnchb.md |
| RE-SERV-081 | LNCHB-05 | lnchb.md |
| RE-SERV-082 | LNCHB-12 | lnchb.md |
| RE-SERV-083 | LNCHB-14 | lnchb.md |
| RE-SERV-084 | LNCHB-18 | lnchb.md |
| RE-SERV-085 | CIDOCS-02 | cidocs.md |
| RE-SERV-086 | CIDOCS-03 | cidocs.md |
| RE-SERV-087 | CIDOCS-05 | cidocs.md |
| RE-SERV-088 | GOTEST-01 | gotests.md |
| RE-SERV-089 | GOTEST-02 | gotests.md |
| RE-SERV-090 | GOTEST-03 | gotests.md |
| RE-SERV-091 | GOTEST-04 | gotests.md |
| RE-SERV-092 | GOTEST-05 | gotests.md |
| RE-SERV-093 | GOTEST-06 | gotests.md |
| RE-SERV-094 | GOTEST-07 | gotests.md |
| RE-SERV-095 | GOTEST-08 | gotests.md |
| RE-SERV-096 | GOTEST-09 | gotests.md |
| RE-SERV-097 | GOTEST-10 | gotests.md |
| RE-SERV-098 | GOTEST-11 | gotests.md |
| RE-SERV-099 | GOTEST-12 | gotests.md |
| RE-SERV-100 | GOTEST-13 | gotests.md |
| RE-SERV-101 | PYTESTA-01 | pytesta.md |
| RE-SERV-102 | PYTESTA-02 | pytesta.md |
| RE-SERV-103 | PYTESTA-03 | pytesta.md |
| RE-SERV-104 | PYTESTA-04 | pytesta.md |
| RE-SERV-105 | PYTESTA-05 | pytesta.md |
| RE-SERV-106 | PYTESTA-06 | pytesta.md |
| RE-SERV-107 | PYTESTA-07 | pytesta.md |
| RE-SERV-108 | PYTESTA-08 | pytesta.md |
| RE-SERV-109 | PYTESTA-09 | pytesta.md |
| RE-SERV-110 | PYTESTB-01, PYTESTB-02 | pytestb.md |
| RE-SERV-111 | PYTESTB-03 | pytestb.md |
| RE-SERV-112 | PYTESTB-04 | pytestb.md |
| RE-SERV-113 | PYTESTB-05 | pytestb.md |
| RE-SERV-114 | PYTESTB-06 | pytestb.md |
| RE-SERV-115 | PYTESTB-07 | pytestb.md |
| RE-SERV-116 | PYTESTB-08 | pytestb.md |
| RE-SERV-117 | PYTESTB-09 | pytestb.md |
| RE-SERV-118 | PYTESTB-10 | pytestb.md |
| RE-SERV-119 | PYTESTB-11 | pytestb.md |
| RE-SERV-120 | PYTESTB-12 | pytestb.md |
| RE-SERV-121 | PYTESTB-13 | pytestb.md |
| RE-SERV-122 | PYTESTB-14 | pytestb.md |
| RE-SERV-123 | PYTESTB-15 | pytestb.md |
| RE-SERV-124 | PYTESTB-16 | pytestb.md |
| RE-SERV-125 | PYTESTB-17 | pytestb.md |
| RE-SERV-126 | PYSMB2-25 | pysmb2.md |
| RE-SERV-127 | PYSMB2-28 | pysmb2.md |
| RE-SERV-128 | PYUDPFS-12 | pyudpfs.md |
| RE-SERV-129 | PYSMALL-16 | pysmall.md |
| RE-SERV-130 | GOPROTO-15 | goproto.md |
| RE-SERV-131 | LNCHA-09 | lncha.md |
| RE-SERV-132 | LNCHB-03 | lnchb.md |
| RE-SERV-133 | LNCHB-15 | lnchb.md |
| RE-SERV-134 | LNCHC-01 | lnchc.md |
| RE-SERV-135 | LNCHC-03 | lnchc.md |
| RE-SERV-136 | LNCHC-14 | lnchc.md |
| RE-SERV-137 | LNCHC-15 | lnchc.md |
| RE-SERV-138 | CIDOCS-09 | cidocs.md |
| RE-SERV-139 | PYSMB2-24 | pysmb2.md |
| RE-SERV-140 | PYSMB1-10 | pysmb1core.md |
| RE-SERV-141 | PYSMB1-11 | pysmb1core.md |
| RE-SERV-142 | PYSMB1-12 | pysmb1core.md |
| RE-SERV-143 | PYSMB1-22 | pysmb1core.md |
| RE-SERV-144 | PYSMB1-23 | pysmb1core.md |
| RE-SERV-145 | PYSMB1-25 | pysmb1core.md |
| RE-SERV-146 | PYUDPFS-10 | pyudpfs.md |
| RE-SERV-147 | PYSMALL-05 | pysmall.md |
| RE-SERV-148 | PYSMALL-13 | pysmall.md |
| RE-SERV-149 | PYSMALL-14 | pysmall.md |
| RE-SERV-150 | GOPROTO-09 | goproto.md |
| RE-SERV-151 | GOPROTO-10 | goproto.md |
| RE-SERV-152 | GOPROTO-12 | goproto.md |
| RE-SERV-153 | GOPROTO-13 | goproto.md |
| RE-SERV-154 | GOCORE-04 | gocore.md |
| RE-SERV-155 | GOCORE-05 | gocore.md |
| RE-SERV-156 | LNCHA-08 | lncha.md |
| RE-SERV-157 | LNCHA-11 | lncha.md |
| RE-SERV-158 | LNCHA-12 | lncha.md |
| RE-SERV-159 | LNCHA-13 | lncha.md |
| RE-SERV-160 | LNCHA-14 | lncha.md |
| RE-SERV-161 | LNCHA-15 | lncha.md |
| RE-SERV-162 | LNCHA-16 | lncha.md |
| RE-SERV-163 | LNCHA-17 | lncha.md |
| RE-SERV-164 | LNCHB-07 | lnchb.md |
| RE-SERV-165 | LNCHB-08 | lnchb.md |
| RE-SERV-166 | LNCHB-09 | lnchb.md |
| RE-SERV-167 | LNCHB-10 | lnchb.md |
| RE-SERV-168 | LNCHB-11 | lnchb.md |
| RE-SERV-169 | LNCHC-04 | lnchc.md |
| RE-SERV-170 | LNCHC-05 | lnchc.md |
| RE-SERV-171 | LNCHC-06 | lnchc.md |
| RE-SERV-172 | LNCHC-08 | lnchc.md |
| RE-SERV-173 | LNCHC-09 | lnchc.md |
| RE-SERV-174 | LNCHC-10 | lnchc.md |
| RE-SERV-175 | LNCHC-11 | lnchc.md |
| RE-SERV-176 | CIDOCS-07 | cidocs.md |
| RE-SERV-177 | CIDOCS-11 | cidocs.md |
| RE-SERV-178 | PYSMB2-23 | pysmb2.md |
| RE-SERV-179 | PYSMB2-26 | pysmb2.md |
| RE-SERV-180 | PYSMB1-05 | pysmb1core.md |
| RE-SERV-181 | PYSMB1-13 | pysmb1core.md |
| RE-SERV-182 | PYSMB1-14 | pysmb1core.md |
| RE-SERV-183 | PYSMB1-15 | pysmb1core.md |
| RE-SERV-184 | PYSMB1-16 | pysmb1core.md |
| RE-SERV-185 | PYSMB1-17 | pysmb1core.md |
| RE-SERV-186 | PYSMB1-24 | pysmb1core.md |
| RE-SERV-187 | PYUDPFS-11 | pyudpfs.md |
| RE-SERV-188 | PYUDPFS-17 | pyudpfs.md |
| RE-SERV-189 | PYSMALL-11 | pysmall.md |
| RE-SERV-190 | PYSMALL-12 | pysmall.md |
| RE-SERV-191 | PYSMALL-15 | pysmall.md |
| RE-SERV-192 | GOPROTO-06 | goproto.md |
| RE-SERV-193 | GOPROTO-08, PYUDPFS-16 | goproto.md |
| RE-SERV-194 | GOPROTO-11 | goproto.md |
| RE-SERV-195 | GOPROTO-16 | goproto.md |
| RE-SERV-196 | GOCORE-06 | gocore.md |
| RE-SERV-197 | GOCORE-08 | gocore.md |
| RE-SERV-198 | GOCORE-09 | gocore.md |
| RE-SERV-199 | GOCORE-11 | gocore.md |
| RE-SERV-200 | LNCHA-10 | lncha.md |
| RE-SERV-201 | LNCHA-18 | lncha.md |
| RE-SERV-202 | LNCHA-19 | lncha.md |
| RE-SERV-203 | LNCHA-20 | lncha.md |
| RE-SERV-204 | LNCHB-16 | lnchb.md |
| RE-SERV-205 | LNCHB-17 | lnchb.md |
| RE-SERV-206 | LNCHB-19 | lnchb.md |
| RE-SERV-207 | LNCHC-02 | lnchc.md |
| RE-SERV-208 | LNCHC-07 | lnchc.md |
| RE-SERV-209 | LNCHC-12 | lnchc.md |
| RE-SERV-210 | LNCHC-13 | lnchc.md |
| RE-SERV-211 | LNCHC-16 | lnchc.md |
| RE-SERV-212 | CIDOCS-04 | cidocs.md |
| RE-SERV-213 | CIDOCS-06 | cidocs.md |
| RE-SERV-214 | CIDOCS-08 | cidocs.md |
| RE-SERV-215 | CIDOCS-10 | cidocs.md |
| RE-SERV-216 | CIDOCS-12 | cidocs.md |