# Adversarial Verification — Redo Audit of PS2-Servers (RE-SERV-001 … RE-SERV-216)

- **Subject:** `~/workspace/Sable-audit-redo/Redo-PS2Servers.md` — 216 findings (RE-SERV-001…216), produced by a blind redo audit.
- **Code pin:** clone `~/workspace/audit-clones-2026-11-13/PS2-Servers` at `c5f021c4d38698860f56b03498adc261dbb8d8dd` — confirmed by every verification worker at start, and re-confirmed by the coordinator at the end with `git status` clean (0 entries). The clone was never written to; all executions ran in /tmp copies.
- **Date:** 2026-11-14 (America/Los_Angeles).
- **Method (refute-first):** for every finding — (1) every cited file, function, class and literal string was grepped for existence first; a citation to something nonexistent would have been an automatic REFUTED (none occurred — every cited file and symbol in all 216 findings exists); (2) the cited code was opened and the mechanism re-derived from scratch, hunting for the defeating guard first; (3) every EXECUTED claim was re-executed by the verifier, who reports only their own observed numbers; (4) proposed fixes were checked for coherence and LESS-NOT-MORE compliance; (5) finding bodies were read for mechanism-level duplicates, within batches by the workers and across batches by the coordinator.
- **Environment:** the sandbox blocks UDP `sendto`. Results depending on UDP are labelled BLOCKED-ENV and never counted as code verdicts. No finding's verdict rests on a UDP-blocked check; two findings carry BLOCKED-ENV *aspects* (RE-SERV-068's 32-bit execution, RE-SERV-188's multiclient selftest execution), each verdicted on reading/arithmetic plus an amd64/POSIX control.
- **Blindness:** nothing under `~/workspace/Sable-audit/` was read, listed, or grepped by the coordinator or any worker.

## Headline tally (counted from the verdict rows in this file)

| Batch | Range | Rows | VERIFIED | VERIFIED-DRIFT | WEAKENED | REFUTED | BLOCKED-ENV |
|---|---|---|---|---|---|---|---|
| A | 001–027 | 27 | 24 | 2 | 1 | 0 | 0 |
| B | 028–045 | 18 | 17 | 0 | 1 | 0 | 0 |
| C | 046–070 | 25 | 23 | 0 | 2 | 0 | 0 |
| D | 071–087 | 17 | 15 | 2 | 0 | 0 | 0 |
| E | 088–100 | 13 | 13 | 0 | 0 | 0 | 0 |
| F | 101–126 | 26 | 24 | 1 | 1 | 0 | 0 |
| G | 127–160 | 34 | 27 | 5 | 1 | 1 | 0 |
| H | 161–190 | 30 | 26 | 4 | 0 | 0 | 0 |
| I | 191–216 | 26 | 22 | 2 | 2 | 0 | 0 |
| **Total** | **001–216** | **216** | **191** | **16** | **8** | **1** | **0** |

**Baselines (assessed separately, not counted in the 216):** BASELINE-GO — **VERIFIED** (reproduced exactly: 246 top-level tests = 145 pass / 99 fail / 2 skip; all 99 failures classified UDP-blocked, zero genuine). BASELINE-PY — **VERIFIED-DRIFT** (reproduced at 1,544 run / 1,389 pass / 3 fail / 72 errors / 80 skip vs the claimed 1,388/73; the single differing test is the claimed "genuine" failure, which did not reproduce — see below).

**Bottom line:** 191 of 216 findings (88.4%) verified as filed, most of them re-executed. 16 carry citation/count drift with the mechanism intact. 8 are true only in narrower scope. Exactly 1 is refuted. No finding cited a nonexistent file, function, or string. The redo's single claimed genuine test failure (Python baseline) is its weakest claim: it is a timing-fragile test, not a proven defect.

## Non-VERIFIED index (full reasoning in the batch parts below)

### REFUTED (1)
- **RE-SERV-128** — "`_optimal_chunk_size` computes an optimum that is always 1408": **false**. Executing the actual method (udpfs_server.py:2746) returns 1024 for totals 0/100/1024, 1280 for 1025/12800, 1408 only for 20000/100000. The audit missed the tie-break limb (on a packet-count tie, higher alignment wins; candidates are visited smallest-first, so 1024 displaces the 1408 incumbent). Its proposed fix would have changed wire behaviour. *Coordinator independently re-executed this — same values.*

### WEAKENED (8) — mechanism real, scope narrower than filed
- **RE-SERV-022** — SMB2 stub handlers fake success as filed for session_setup/create/read/write/negotiate, and the auth-bypass dispatch is real; but `h2_tree_connect` does **not** succeed for any share name — it raises `struct.error` on every call (5 values packed into a 4-field format, smbv1_server/smbserver_opl.py:573-581) and kills the connection thread. Also, QUERY_DIRECTORY is command 0x000E, not the cited 0x000A (0x000A is LOCK); 0x000E is indeed unmapped, so the "cannot list" conclusion stands. The pack bug itself is candidate CAND-A1.
- **RE-SERV-035** — Wire `mode` does reach `os.mkdir` unsanitised and the sticky bit survives end-to-end (0o1755 → 0o1750 through the handler); the filed setgid example did not reproduce (0o2755 → 0o750 on this host/fs), so setgid survival is host/group-dependent, not guaranteed.
- **RE-SERV-059** — Core defect real and executed (`PROTOCOL_MODE=proper` passes argparse's choices, then raises an uncaught `ValueError` at construction); the filed trailing-space sub-claim is false — `__init__` strips and lowercases, and `"modulo "`, `" modulo"`, `"MODULO"`, `"Standard"` all construct fine. Only unmapped values fail.
- **RE-SERV-067** — The stale-handle mechanism is real for same-path replacement (re-dumped ISO served from the stale handle with the new entry's size); the filed `.chd`→`.iso` swap example cannot hit it, because `entry.path` differs between extensions and forces a reopen. Scope: same-path replacement only.
- **RE-SERV-102** — The census is exact (8 of 36 in-scope files lack `unittest.main()`), but the stated impact is false as written: plain `python tests/test_game_library.py` (all 8 files, executed) exits **1** with `ModuleNotFoundError: No module named 'launcher'`, not silent exit-0; the silent-0 behaviour occurs only with `PYTHONPATH=.`, and adding the guard alone cannot fix direct runs (no `sys.path` prologue). See candidate F-1.
- **RE-SERV-133** — The watch-profile vocabulary mismatch is real (sets exactly as cited; no producer emits `orbit-v1.0.2`), but the caduceus discard is latent, not live: the only production `ProcessResolver` (in `AccountWorker`) overrides the validator with `valid_account_result`, and the caduceus path never crosses `valid_identification_result`.
- **RE-SERV-213** — The core stands (portable/AppImage steps are fail-soft), but release.yml carries **11** `continue-on-error: true`, not 13; and the claimed `if-no-files-found: error` + `continue-on-error` pairing does not exist at release.yml:403-420 (the portable upload has no such key; the AppImage upload uses `ignore`; the `error` at :471 is on a non-continue-on-error step) — that pairing actually occurs in release-on-main.yml's portable upload.
- **RE-SERV-216** — Per-file sizes are exact, but the fixtures are not "generated" and not undocumented: `testdata/README.md` records them as unmodified upstream pcm720/udpfsd reference vectors at a pinned commit, which undercuts the filed fix. True sum is 3,215,374 B (≈3.22 MB), not ~3.16 MB.

### VERIFIED-DRIFT (16) — mechanism true, cited values/lines/counts corrected
- **RE-SERV-003** — `ACCESS_WRITE_ANY` is defined at smb2_wire.py:114-116, not the cited 143-145 (those lines are unrelated status constants). Substance true; no `can_read` exists anywhere.
- **RE-SERV-015** — The advertised 1 MiB maxima are at smb2_wire.py:206-208, not the cited 204-206.
- **RE-SERV-084** — The CredentialStore wedge is real and executed, but `load()` short-circuits to `None` when the `owned` subdir is missing: a profile-only 0755 wedges `save()`, not `load()`; the `load()` wedge requires `owned` itself at non-0700.
- **RE-SERV-085** — The claim executed true (shipped systemd env args refused by the built binary, exit 1, exact message), but the refusal rule lives at webui.go:63-65, 98-102 (reached from main.go:520), not the cited main.go:476-484.
- **RE-SERV-126** — Line count (53), consumer, and `fcntl` are exact, but the three enumeration mechanisms in `_local_ipv4_addresses` are conditional fallbacks (`if not ips:` twice), not three runs per call; on the verifying host only getaddrinfo ran.
- **RE-SERV-144** — DATA header duplication is real, but the oversize check exists only in `_route_data_via`, not in `_handle_data`; and the base class's third parse uses `DataHeader.unpack`, not the same shifts/masks code.
- **RE-SERV-145** — The Core↔SMB1 session-stats snippet is verbatim, but the parenthetical third copy is false: standalone `udpfs_server.py`'s main has no session-summary code at all.
- **RE-SERV-148** — The NFS `_op_close`/`_op_open_confirm` bodies are **not** byte-identical: they transpose their first two parse lines (required by wire order — stateid/seqid vs seqid/stateid). The shared tail is 2 lines; the fix saving is ~2 lines, not ~5.
- **RE-SERV-156** — The port-445 divergence is real, but SMBv1 *does* query srvnet dependents to name the pin-holder; what it lacks is driver *pausing* and SMBv2's LanmanServer foreign-dependent gate.
- **RE-SERV-160** — The count is 6 parse sites / 5 distinct functions (netinfo has two inline branches, not one); directlink's Linux parser is JSON grammar, not a copy of the inet-line text grammar. The same-name `_parse_macos_ifconfig` collision is real.
- **RE-SERV-170** — Three guides + the dead standalone window confirmed, but "both layers independently auto-show" is overstated: the panel auto-show is guarded off at library_panel.py:84-85 (`if not hasattr(app, "_open_ps2_setup_guide")`) in the integrated app.
- **RE-SERV-173** — The disc-serial pattern is re-expressed **six** times in five files (disc_identity holds two) — five PS2 spellings — not the body's "four PS2 expressions."
- **RE-SERV-176** — Release-build duplication is real (recount: 96 identical non-trivial lines vs the filed 90) and `verify-version` is genuinely missing from release-on-main; but the Windows desktop-startup check **does** exist there (release-on-main.yml:119-121, identical step), and the AppImage row is file-guarded (`if [ -f … ]`) with no step capable of producing the file — a dead conditional, never an emitted false row.
- **RE-SERV-188** — Executed as filed on POSIX (`[unc] skipped` printed, then `ALL UDPFS PATH-GUARD TESTS PASSED`; write-opcode grep = 0 in all three selftests). Correction: the separator-root assertions are not wholly vacuous — exact-join checks do run; only their containment limb is un-falsifiable under root `/`.
- **RE-SERV-202** — Duplicate disk enumeration is real but understated: a Linux-exclusive `open_raw_device` pays **3** `_metadata()` runs (raw_storage.py:289 `_exclusive_target`, :299 second `_exclusive_target` recheck — missed by the audit, :310 `mount_warning`), not "up to twice"; Windows-exclusive = 2, non-exclusive = 1.
- **RE-SERV-204** — All four dead-code items confirmed, but the ENDPOINT attribution is swapped: the test-referenced one (tests/test_ra_sets.py:84, via a `RASetClient`) belongs to the *set* client; the *session* client's has zero references. And `_NoRedirect` (unused in-file, AST-confirmed) is a load-bearing re-export imported by tests from `ra_set_client` — deleting it requires updating the test import to `launcher.ra_http`.

---

## Part A — RE-SERV-001–027 (Python SMB2 / SMB1 core)


- **Subject:** Blind redo audit of PS2-Servers, findings RE-SERV-001–027 in `Redo-PS2Servers.md` (Python SMB2 server + Python SMB1 server core).
- **Pin confirmation:** `git -C ~/workspace/audit-clones-2026-11-13/PS2-Servers rev-parse HEAD` → `c5f021c4d38698860f56b03498adc261dbb8d8dd` — matches the assigned pin exactly.
- **Date:** 2026-11-14 (America/Los_Angeles).
- **Method:** Refute-first. Every cited file/function/literal was grepped and opened at the cited lines in the pinned clone (read-only; clone never written). All executions ran on copies under `/tmp/vA/run/` driving the handlers directly. No finding in this batch required UDP, so no BLOCKED-ENV verdicts arose.
- **Duplicates:** No two findings in this range are the same mechanism. RE-SERV-002/003 are complementary halves of one design gap (write-side vs read-side DesiredAccess) but distinct mechanisms and distinct code paths. RE-SERV-015/025 and RE-SERV-017/025 are parallel instances of the same flaw families in two different servers (SMB2 `smb2_wire.py`/`smb2_server.py` vs SMB1 `smbserver_opl.py` — the SMB1 framing code is the code the SMB2 module says it lifted), not duplicates of each other.

---

### RE-SERV-001 — VERIFIED
- **Citations:** All exact. `_UNSIGNED_OK` at smb2_server.py:537-538; `_signature_check` returns `None` when `signing_key is None` at 552-553; `h_negotiate` overwrites `conn.dialect`/`conn.negotiate_claims` at 755/771 with no `authenticated` guard; SESSION_SETUP NTLM-NEGOTIATE branch sets `signing_key=None`, `signing_required=False` at 855-856 and never touches `conn.authenticated`. `validate_negotiate_info` at 1406-1429 compares against `conn.negotiate_claims` (1423).
- **Mechanism:** Re-derived from scratch; no defeating guard exists in either handler or in `_dispatch`/`_signature_check`.
- **Execution (mine):** With `authenticated=True`, `signing_required=True`, key set: unsigned SESSION_SETUP/NTLM NEGOTIATE → status `0xC0000016` (MORE_PROCESSING_REQUIRED), `authenticated=True` afterwards, `signing_key=None`, `signing_required=False`; a subsequent unsigned CREATE passed `_signature_check` (returned `None`). Unsigned mid-session NEGOTIATE offering only 0x0202 → SUCCESS, dialect `0x0302 → 0x0202`, `negotiate_claims` replaced with `(0, b'\x00'*16, 0, (514,))`. Matches the report's executed proof in every particular.
- **Fix:** Coherent and LESS-NOT-MORE compliant (a guard/teardown at two existing branches, no new abstraction).

### RE-SERV-002 — VERIFIED
- **Citations:** All exact. CREATE create/truncate effects at 1023-1040 precede the `can_write` assignment at 1050; DELETE_ON_CLOSE armed at 1051-1053; CLOSE delete at 1083-1088; SET_INFO disposition at 1333-1335 and rename at 1340-1357 have no `can_write` check, while WRITE (1177) and the EOF truncate (1328-1329) do.
- **Mechanism:** Confirmed by reading; the only gate on the destructive CREATE branches is `srv._writable(share)` (share-level, not handle-level).
- **Execution (mine):** (1) CREATE `DesiredAccess=FILE_READ_DATA` + `FILE_OVERWRITE` → SUCCESS, `can_write=False`, 100-byte file truncated to **0 bytes**. (2) SET_INFO rename through a read-only handle → SUCCESS, source gone, destination exists. (3) CREATE read-only + `FILE_DELETE_ON_CLOSE` → `delete_on_close=True`; CLOSE → SUCCESS, file deleted. (4) Bonus: SET_INFO FileDispositionInformation through a read-only handle → SUCCESS, `delete_on_close` armed. All three of the report's executed proofs reproduce exactly.
- **Fix:** Coherent; reuses the existing WRITE check at the points the report names.

### RE-SERV-003 — VERIFIED-DRIFT
- **Citations:** `h_read` at 1130-1157 and `Handle` at 150-166 are exact (slots contain `can_write`, no read field). One citation is wrong: the report cites `smb2_wire.py:143-145` for `ACCESS_WRITE_ANY`, but that constant is defined at **smb2_wire.py:114-116** (`ACCESS_WRITE_ANY = 0x00000002 | 0x00000004 | 0x02000000 | 0x10000000 | 0x40000000` at line 116); lines 143-145 are `STATUS_MEDIA_WRITE_PROTECTED`/`STATUS_DISK_FULL`/`STATUS_CANNOT_DELETE`. Grep for `can_read` across `smb2_server/*.py`: zero hits — the substance ("no read mask/read concept exists") is true.
- **Mechanism:** `h_read` checks only pipe/directory, then `_open_file(h, writable=False)` and reads; nothing consults DesiredAccess.
- **Execution (mine):** Handle created with `DesiredAccess=FILE_WRITE_DATA` only → `h_read` returned SUCCESS with data `b'TOPSECRET-DATA'` — byte-identical to the report's proof.
- **Fix:** Coherent (mirror the existing `can_write` pattern); no new abstraction.

### RE-SERV-004 — VERIFIED
- **Citations:** Exact — smb2_server.py:1336-1339 returns `STATUS_SUCCESS` for `FILE_BASIC_INFORMATION` with the Explorer comment and no payload validation (contrast the rename branch's length checks at 1340-1348 and the EOF branch's `len(payload) >= 8` at 1326).
- **Mechanism:** No `os.utime` or any other effect anywhere in the branch.
- **Execution (mine):** Zero-length payload → SUCCESS (`0x00000000`). A well-formed 40-byte payload carrying a LastWriteTime of 116444736000000000 (Unix epoch as FILETIME) → SUCCESS and the file's `st_mtime_ns` was **unchanged**.
- **Fix:** Coherent; the minimal half (validate payload length) is pure LESS.

### RE-SERV-005 — VERIFIED
- **Citations:** Exact. AUTHENTICATE failure paths raise at 884/894-905 before `conn.challenge = None`, which appears only on the success path at 909; `Authenticator.verify` (smb2_auth.py:277-324) keeps no attempt state. Grep: no counter/lockout/delay in the auth path; the only `time.sleep` calls in smb2_server.py are at 1527/1563/1668, all inside the Windows take-445 service choreography, as the report states.
- **Mechanism:** A failed AUTHENTICATE leaves `conn.challenge`, `conn.ntlm_negotiate`, `conn.ntlm_challenge` untouched, so the same challenge can be retried immediately and indefinitely.
- **Execution (mine):** Drove a failed AUTHENTICATE (anonymous token against a password-configured server) with `conn.challenge = b'\x11'*8` preset: raised `Smb2Error` with status `0xC000006D` (LOGON_FAILURE); afterwards `challenge` was still `b'\x11'*8` and `authenticated=False`. (The report filed this as READ/Med; the challenge-survival half is now executed. The no-throttling half is grep-verified absence, as the report framed it.)
- **Fix:** Coherent; the per-connection counter is small state at an existing point, justified in the finding itself.

### RE-SERV-006 — VERIFIED
- **Citations:** Exact. `h_logoff` (927-933) clears handles/trees/`authenticated` only; session ids are minted only `if conn.session_id == 0` at 862-863 and 914-915.
- **Mechanism:** Nothing after `h_logoff` clears the id either; the signing-key comment at 931-932 explains the key's survival for the LOGOFF reply but the id has no such need — and the reply header is built as `conn.session_id or hdr.session_id` (line 486), so the fix's own caution is well-placed.
- **Execution (mine):** `session_id=0x1122334455667788` → `h_logoff` returned SUCCESS, `authenticated=False`, `session_id` still `0x1122334455667788`. (Report filed READ/Med; now executed.)
- **Fix:** Coherent, with the reply-header interaction correctly flagged in the finding itself.

### RE-SERV-007 — VERIFIED
- **Citations:** Exact. `h_tree_disconnect` (974-976) pops and returns SUCCESS; `h_echo` (1363-1364) returns SUCCESS unconditionally; `h_logoff` (927-934) — none calls `_require_session`/`_tree`, unlike every other stateful handler.
- **Mechanism:** Confirmed; no dispatcher-level session gate exists either (`_dispatch` goes straight to the handler table).
- **Execution (mine):** On a fresh, never-authenticated connection: TREE_DISCONNECT for tree id 99 → `0x00000000`, ECHO → `0x00000000`, LOGOFF → `0x00000000`. Matches the report's proof.
- **Fix:** Coherent; correctly leaves ECHO's unauthenticated behaviour as an explicit choice.

### RE-SERV-008 — VERIFIED
- **Citations:** Exact — smb2_spnego.py:330-341: unwrap only when `NEGOTIATE_KEY_EXCH` is set **and** `len(encrypted_session_key) == 16`; otherwise `return session_base_key` silently. Call site at smb2_server.py:901 as the fix cites.
- **Mechanism:** No error, log, or distinguishing return on the fallback path.
- **Execution (mine):** Credentials stub with KEY_EXCH set and a 5-byte wrapped key → `exported_session_key` returned the 16-byte base key **unchanged** (`out == base` is True). Matches the report's proof.
- **Fix:** Coherent (fail at the existing call site with the existing error type).

### RE-SERV-009 — VERIFIED
- **Citations:** Exact — smb2_server.py:429-434: `parse_header` failure at `offset > 0` falls through to a bare `return`; replies accumulated in the local `replies` list are sent only after the loop (494-495).
- **Mechanism:** No partial-send path exists anywhere in `_dispatch_all`.
- **Execution (mine):** Compound = one valid ECHO (NextCommand=72) + 64 zero bytes, driven through `_dispatch_all` with a recording fake socket: **0 `sendall` calls, 0 bytes sent**. Matches the report's proof exactly.
- **Fix:** Coherent (send what was accumulated; no new machinery).

### RE-SERV-010 — VERIFIED
- **Citations:** Exact — smb2_server.py:1258-1263: the break condition is `if total + len(padded) > out_len and entries:`, so the first entry is always emitted.
- **Mechanism:** No first-entry oversize check; `STATUS_INFO_LENGTH_MISMATCH` exists (wire:142) and is used by `h_query_info` (1305-1306) as the fix notes.
- **Execution (mine):** `out_len=16` → SUCCESS with OutputBufferLength **96** using info class 0x03 (FileBothDirectoryInformation) — the report's exact figure; 112 with class 0x25 and 72 with class 0x02 in my runs, i.e. the oversize is class-dependent but always > 16 with SUCCESS.
- **Fix:** Coherent; reuses an already-defined, already-used status.

### RE-SERV-011 — VERIFIED
- **Citations:** Exact — smb2_server.py:1204-1207: `keep = [n for n in names[:2]]` unconditionally, `fnmatch` applied only to `names[2:]`.
- **Mechanism:** The dot entries' existence is deliberate (docstring 1193-1197); only the filter bypass is the defect, as the report says.
- **Execution (mine):** `_listing_for(handle, "*.iso")` on a directory containing `onlyfile.iso` + `other.txt` returned `['.', '..', 'onlyfile.iso']` — identical to the report's proof.
- **Fix:** Coherent and minimal.

### RE-SERV-012 — VERIFIED
- **Citations:** Exact. File branch 1146-1153 raises `STATUS_END_OF_FILE` whenever `fh.read()` returns empty; pipe branch 1137-1139 raises `STATUS_PIPE_EMPTY` on empty data.
- **Mechanism:** Pipe half re-derived: `SrvsvcPipe.read(length)` (smb2_srvsvc.py:139-142) with `length=0` returns `(b'', …)`, and `h_read` raises PIPE_EMPTY on `not data` regardless — so a zero-length pipe READ is PIPE_EMPTY even when the pipe holds data.
- **Execution (mine):** Zero-length READ at offset 0 of a non-empty file raised `Smb2Error` status `0xC0000011` ("read past the end") — the report's exact result. (Pipe half verified by reading, as above.)
- **Fix:** Coherent (one `length == 0` special case before either branch).

### RE-SERV-013 — VERIFIED
- **Citations:** Exact — smb2_server.py:748-757: the dialect loop `break`s at 752-753 when the body runs out; claims are stored from the truncated list at 755.
- **Mechanism:** No count-vs-body validation anywhere in `h_negotiate` beyond the initial `len(body) < 36` check.
- **Execution (mine):** NEGOTIATE with `DialectCount=3` but one dialect (0x0210) in the body → SUCCESS, `conn.dialect=0x0210`, stored claims `(0, b'\x00'*16, 0, (528,))` — the truncated one-dialect tuple enshrined, exactly as the report describes.
- **Fix:** Coherent; same error style as every other short request in the file.

### RE-SERV-014 — VERIFIED
- **Citations:** Exact — `h_flush` at 1102-1111 has no length check before `body[8:24]`/`handle_from`, and wraps flush+fsync in `except OSError: pass`. Contrast verified: `h_read` checks `len(body) < 48` at 1132-1133, as do CLOSE/CREATE/WRITE/QUERY_*/SET_INFO/IOCTL; FLUSH is the only FileId-taking handler without one.
- **Mechanism:** `handle_from` (265) does `struct.unpack("<QQ", raw_file_id)` — a short slice raises `struct.error`, which `_dispatch`'s generic `except Exception` (644-646) flattens to INVALID_PARAMETER, exactly the catch-all path the report describes.
- **Execution (mine):** (1) 8-byte FLUSH body raised `struct.error: unpack requires a buffer of 16 bytes` out of `h_flush` — the report's exact message. (2) A handle whose `fileno()`/fsync fails with OSError → `h_flush` returned SUCCESS (`0x00000000`).
- **Fix:** Coherent; both halves reuse existing patterns (`len` check; `_status_for_oserror` via the dispatcher's OSError mapping).

### RE-SERV-015 — VERIFIED-DRIFT
- **Citations:** Framing locations exact (`_recv_exact` smb2_wire.py:27-34 with `buf += chunk` at 33; `recv_msg` 37-54 trusts the 24-bit length, max 16,777,215). One line drift: the report cites the advertised 1 MiB maxima at "smb2_wire.py:204-206"; they are actually at **206-208** (`MAX_TRANSACT`/`MAX_READ`/`MAX_WRITE = 1 << 20`).
- **Mechanism:** No cap anywhere between `recv_msg` and dispatch; the length is honoured before any header/dialect/session check (`_serve_client` 391-397). The quadratic claim follows directly from repeated `bytes` concatenation at line 33.
- **Execution (mine):** `recv_msg` over a fake socket accepted and returned a **2,000,000-byte** frame — nearly 2× `MAX_TRANSACT` (1,048,576) — with no complaint. (Quadratic cost verified by reading; no timing run needed to establish the algorithm.)
- **Fix:** Coherent; the cap is tied to a number the server already advertises, and list/bytearray accumulation is a local change.

### RE-SERV-016 — VERIFIED
- **Citations:** Exact. `send_msg` at smb2_wire.py:23-24 (`len(msg).to_bytes(3, "big")`); `_dispatch_all` builds the compound reply `out` with no size bound (469-495); `_serve_client` (385-415) catches only `ConnectionResetError/ConnectionAbortedError/BrokenPipeError` and `OSError`.
- **Mechanism:** `OverflowError` is not an `OSError`, and the per-handler catch-all in `_dispatch` (644-646) does not cover `send_msg`, which is called from `_dispatch_all` after the handler loop — so the exception escapes `_serve_client` and kills the thread. (Thread death is inferred from the `except` clauses, exactly as the report itself labels it.)
- **Execution (mine):** `send_msg` with a 16 MiB + 1 message raised `OverflowError: int too big to convert` — the report's exact result.
- **Fix:** Coherent; the minimum variant (make the failure an OSError subclass) is pure LESS.

### RE-SERV-017 — VERIFIED
- **Citations:** Exact. `serve_forever` (337-362) spawns an unbounded daemon thread per accept (360-362); `_serve_client` sets only TCP_NODELAY on the accepted socket (390) — grep confirms the file's only `settimeout` is on the listening socket at 342; `add_handle` (245-251) never refuses; TREE_CONNECT allocates a tree per request (963-965, within the cited 949-972) with no cap.
- **Mechanism:** Grep-verified absence: no connection counter, no accepted-socket timeout, no handle/tree cap anywhere in the serving path. Handles do hold no descriptor until first use, as the report concedes.
- **Execution:** n/a — absence claim, established by full read + grep of the serving path (the report likewise files it READ/Med).
- **Fix:** Coherent; three small checks at three existing allocation points reusing the already-defined `STATUS_INSUFFICIENT_SERVER_RESOURCES` (wire:133 — note the wire constant is spelled `STATUS_INSUFF_SERVER_RESOURCES`). The additions carry the finding's own justification.

### RE-SERV-018 — VERIFIED
- **Citations:** Exact. CREATE records only the path and opens nothing (1049-1053, stat at 1042); `_open_file` opens `h.path` on first use (1114-1127); CLOSE stats/deletes by path (1077, 1083-1088); QUERY_INFO stats by path (1291).
- **Mechanism:** The CREATE-time `os.stat` result is used only for the CREATE reply; no identity (`st_dev`/`st_ino`) is stored on `Handle` (slots at 152-153 confirm none exists).
- **Execution (mine):** Handle created on a file containing `ORIGINAL`; after `os.replace` swapped in a file containing `REPLACED!`, `h_read` returned SUCCESS with data `b'REPLACED!'` — the report's exact proof, including the replacement string.
- **Fix:** Coherent and minimal (two integers + one comparison at the single lazy-open point); preserves the documented descriptor-saving rationale.

### RE-SERV-019 — VERIFIED
- **Citations:** Exact — smb2_wire.py:438-439: `FILE_ACCESS_INFORMATION` returns `struct.pack("<I", 0x001F01FF)` unconditionally; `file_info` receives `read_only` (422) and uses it for attributes but not for this class. The read-only tree-connect mask the fix cites is at smb2_server.py:969 (`0x001200A9`), as stated.
- **Mechanism:** No caller-dependent variation: `h_query_info` passes `read_only` in (1288, 1293) and it is ignored for this class.
- **Execution (mine):** `file_info(FILE_ACCESS_INFORMATION, st, read_only=True)` returned `0x001F01FF` — the report's exact result.
- **Fix:** Coherent; derives from state the caller already passes.

### RE-SERV-020 — VERIFIED
- **Citations:** Exact — smb2_server.py:1340-1357: the rename branch reads byte 0 (ReplaceIfExists) and the name length at offset 16, never bytes 8-15 (RootDirectory), resolves via `h.share.resolve(target)` (1350, share root), and stores the raw client string as `h.rel` (1356).
- **Mechanism:** No RootDirectory handling exists anywhere in the handler.
- **Execution (mine):** SET_INFO rename with a non-zero RootDirectory field and name `relname.iso` on a handle to `subdir/onlyfile.iso` → SUCCESS; the file landed at the **share root** (`relname.iso` exists at root, not in `subdir`), and `h.rel` was the raw `'relname.iso'`. Matches the report's READ claim in full.
- **Fix:** Coherent; the refuse-with-NOT_SUPPORTED variant is the LESS option and is offered as such.

### RE-SERV-021 — VERIFIED
- **Citations:** All exact. `--user NAME:PASSWORD` at smb2_server.py:1741-1742; `build_authenticator` splits the spec at 1708-1724 (split at 1713); the module docstring's own example shows `--user ripto:secret` at line 44.
- **Mechanism:** Argv visibility (process list / shell history / launcher logs) is a platform fact, not a code path; the code side — the password is taken verbatim from argv with no file alternative — is confirmed by reading `main()` in full: no `--users-file` or env alternative exists.
- **Execution:** n/a (READ claim, as filed).
- **Fix:** Coherent; the added `--users-file` feeds the same `user_specs` list `build_authenticator` already consumes, and the finding carries the required justification for an addition. Keeping `--user` working avoids breaking the launcher.

### RE-SERV-022 — WEAKENED
- **Citations:** Handler block at smbv1_server/smbserver_opl.py:537-637, SMB2 dispatch in `serve_conn` at 1435-1458, and the `main()` INCOMPLETE warning at 1724-1728 all exist as cited. The dispatch branch indeed never applies `_logon_required` — grep shows that gate is called only at line 1472, in the SMB1 path.
- **Mechanism — what verifies:** `h2_session_setup` succeeds as guest (SessionFlags `0x0001`) with no credential check; `h2_create` returns one constant FileId and CreateAction 1 (FILE_OPENED) for any path; `h2_read` returns 0 bytes with SUCCESS; `h2_write` returns SUCCESS with count 0 and writes nothing; `h2_negotiate` embeds the fixed sequential GUID `01 02 … 10` (line 545); the real ECHO command 0x000D is unmapped and 0x0012 (OPLOCK_BREAK) is mapped to `h2_echo`. All executed by me (below) except as noted.
- **Mechanism — what does NOT verify (narrowing evidence):** The report states "`h2_tree_connect` succeeds for any share name without consulting `server.shares`". It does not succeed at all: at smbserver_opl.py:573-581 it calls `struct.pack("<BBII", 16, 0x01, 0, 0x00000030, 0x000f001f)` — a 4-field format given **5 values** — and raises `struct.error: pack expected 4 items for packing (got 5)` on **every** call (executed by me). In `serve_conn` that exception escapes the `except (ConnectionError, OSError)` handler (1497), so an SMB2 TREE_CONNECT kills the connection thread instead of returning a fake success. The handler is still a stub that does no work and never consults `server.shares` — but its observable behaviour is a crash, not a success, so the finding's "every handler fabricates a successful reply" framing is true only of the other handlers. Second, smaller error: the report says QUERY_DIRECTORY is command 0x000A; in this codebase's own constants (smb2_wire.py:85-89) 0x000A is LOCK and QUERY_DIRECTORY is **0x000E**. 0x000E is indeed absent from `SMB2_HANDLERS`, so the "share can never be listed" conclusion stands, but the cited command value is wrong.
- **Execution (mine):** `h2_write` on a `read_only=True` server → status `0x00000000`, written count **0** (the report's exact executed claim). `h2_read` → SUCCESS, data length 0. `h2_create` → SUCCESS, CreateAction=1, FileId `01000000000000000100000000000000` (the constant). `h2_session_setup` with users configured → SUCCESS, SessionFlags `0x0001` (IS_GUEST), uid assigned. `h2_tree_connect` → `struct.error` (above), **not** SUCCESS.
- **Fix:** Unaffected and coherent — deleting the stub surface / refusing non-1 at startup remains the right LESS-NOT-MORE remedy; the tree-connect crash makes the stub *less* functional than the report believed, not more defensible.

### RE-SERV-023 — VERIFIED
- **Citations:** Exact. `h_read_andx` (957-985) and `h_write_andx` (1012-1035) unpack fixed offsets with no WordCount/length guard; `_trans2_parse` (1114-1126) likewise; `h_trans2_find_next2` unpacks `t_params` at 1259; `serve_conn`'s only handler is `except (ConnectionError, OSError)` at 1497. The contrast guards exist as claimed (`_parse_session_setup` wordcount guard at 702; `h_nt_create_andx` length guard). `Req` truncates silently exactly as described: `self.params = msg[33:33 + wc*2]` (line 245) — Python slicing shortens without error.
- **Mechanism:** `struct.error` is not an `OSError`, so it escapes `serve_conn` and kills the connection thread with no SMB error reply; the server process survives (thread-per-connection), as the report states.
- **Execution (mine):** With a 2-byte params block, **all four** raised `struct.error` (report executed two): `h_read_andx` ("requires a buffer of at least 6 bytes … at offset 4"), `h_write_andx` (same), `_trans2_parse` ("at least 20 bytes … at offset 18"), and `h_trans2_find_next2` with empty `t_params` ("at least 2 bytes … at offset 0"). `isinstance(e, OSError)` is False for each.
- **Fix:** Coherent; the per-handler guard matches the file's existing pattern and is the smaller change, as the report argues.

### RE-SERV-024 — VERIFIED
- **Citations:** Exact in substance. `h_session_setup` calls `hmac.compare_digest(password, expected)` at smbserver_opl.py:746; `--user` parsing in `main()` (1730-1737, within the cited 1731-1747) accepts any password string. One premise is slightly overbroad but widens rather than narrows the bug: the report says the incoming password "is always pure ASCII" — true of the ANSI path (line 713, `"ascii", "ignore"`), but the Unicode fallback at line 715 decodes UTF-16LE and can itself yield non-ASCII, which triggers the same `TypeError` from the other side.
- **Mechanism:** `hmac.compare_digest` on `str` operands raises `TypeError` if **either** side is non-ASCII; the configured side is unrestricted, so a non-ASCII configured password crashes every login attempt, and per RE-SERV-023's dispatch the exception kills the connection thread.
- **Execution (mine):** `hmac.compare_digest("abc", "päss")` → `TypeError: comparing strings with non-ASCII characters is not supported` (the report's exact message). End-to-end: `h_session_setup` with configured user `{"user": "päss"}` and a well-formed request for that account raised the same `TypeError` out of the handler — not a LOGON_FAILURE.
- **Fix:** Coherent and minimal (compare on encoded bytes).

### RE-SERV-025 — VERIFIED
- **Citations:** Exact in substance. `recv_msg`/`_recv_exact` at smbserver_opl.py:183-207 accept the 24-bit length as-is (identical framing code to RE-SERV-015's, in the SMB1 file); `h_negotiate` advertises MaxBufferSize 65535 at line 679 (within the cited 676-690); the accept loop spawns an unbounded daemon thread per connection at 1843-1845 and sets only TCP_NODELAY on accepted sockets (1843) — grep confirms the file's only `settimeout` is on the **listening** socket at 1828.
- **Mechanism:** 16,777,215 max frame ≈ 256× the advertised 65,535 — the report's arithmetic checks. No idle timeout and no thread cap exist anywhere in the file.
- **Execution:** n/a — READ claim as filed; the identical framing mechanism was executed against the SMB2 copy under RE-SERV-015 (2,000,000-byte frame accepted).
- **Fix:** Coherent; both bounds are constants tied to the negotiated maximum, no new configuration.

### RE-SERV-026 — VERIFIED
- **Citations:** Exact — smbserver_opl.py:1064-1068: `h_query_disk` packs `struct.pack("<HHHHH", 0xFFFF, 64, 512, 0xFFFF, 0)` with the comment "smbman just needs non-zero free space" at 1065. Grep confirms no `disk_usage` call exists in the file.
- **Mechanism:** Total and free units are constants independent of the volume: 0xFFFF × 64 × 512 = 2,147,450,880 bytes (~2 GiB), as the report states.
- **Execution:** n/a — READ claim as filed; the constants are on the face of the code.
- **Fix:** Coherent; `shutil.disk_usage` on the share root scaled into the existing u16 packing is a local change.

### RE-SERV-027 — VERIFIED
- **Citations:** Exact. `_env_bool` at udpfs_server.py:3297-3302 (`v.strip().lower() in ('1', 'true', 'yes', 'on')`, everything else False, no warning); the six `store_true` flags with `_env_bool` defaults at ps2servers_core.py:535-560 (`--read-only`/`RO` at 535-536, `--verbose` 537-538, `--single-port` 545-546, `--modulo-mode` 552, `--metrics` 558, `--session-summary` 559-562); the compression `--enable-compression`/`--no-compression` pair exists in the same parser at 540-541, as the report notes.
- **Mechanism:** `store_true` with an env-derived default has no CLI off-switch; grep of the built parser's option strings confirms no `--no-read-only` (or any negative counterpart for the six flags).
- **Execution (mine):** `_env_bool("RO")` with `RO=2` → `False` (with `RO=true` → `True` as control). End-to-end: `build_parser().parse_args([])` with `RO=2` → `read_only=False`; with `RO=1` → `read_only=True`. The report's executed claim reproduces exactly, through the real parser.
- **Fix:** Coherent; the warning-only half is the whole safety fix and adds nothing structural, exactly as scoped in the finding.

---

## Tally

| Verdict | Count | IDs |
|---|---|---|
| VERIFIED | 24 | 001, 002, 004, 005, 006, 007, 008, 009, 010, 011, 012, 013, 014, 016, 017, 018, 019, 020, 021, 023, 024, 025, 026, 027 |
| VERIFIED-DRIFT | 2 | 003, 015 |
| WEAKENED | 1 | 022 |
| REFUTED | 0 | — |
| BLOCKED-ENV | 0 | — |
| **Total** | **27** | |

## CANDIDATES (new defects found while verifying — separate from the verdicts)

- **CAND-A1 — `h2_tree_connect` in the SMB1 file's SMB2 stub crashes on every call (struct.pack arity bug).** `smbv1_server/smbserver_opl.py:573-581`: `struct.pack("<BBII", 16, 0x01, 0, 0x00000030, 0x000f001f)` passes 5 values to a 4-field format. Executed by me: raises `struct.error: pack expected 4 items for packing (got 5)` unconditionally. Because the SMB2 dispatch branch in `serve_conn` (1435-1454) sits under the same `except (ConnectionError, OSError)` handler (1497) identified in RE-SERV-023, the exception kills the connection thread — so with `--smb-version 2/3`, any SMB2 TREE_CONNECT drops the connection rather than returning even the stub's fake success. Distinct from RE-SERV-022 (which asserted this handler returns success) and from RE-SERV-023 (SMB1 parameter blocks); it is the same escape-path weakness manifesting inside the SMB2 stub itself.

---

## Part B — RE-SERV-028–045 (Python UDPFS / UDPBD / HTTP / NFS)


- **Pin confirmation:** `git -C ~/workspace/audit-clones-2026-11-13/PS2-Servers rev-parse HEAD` → `c5f021c4d38698860f56b03498adc261dbb8d8dd` — **matches** the required pin exactly. Clone was read-only for all work; every execution ran in-process against scratch files under `/tmp` (`/tmp/probe_b.py`, `/tmp/probe_udpbd.py`, `/tmp/probe_http_nfs.py`, `/tmp/probe_nfs_http2.py`, plus inline probes), with `PYTHONDONTWRITEBYTECODE=1`. No sockets were used (handlers driven directly, replies captured by overriding the `_send_*` methods), so the sandbox UDP block did not bite.
- **Go toolchain note (affects only the Execution bullet of RE-SERV-040…045):** `go version` → `command not found` in this sandbox. Those verdicts therefore rest on literal, deterministic source re-derivation (quoted below) rather than my own re-execution; the mechanisms are single-expression / single-branch facts, not timing or runtime behaviour. This is stated per-finding, not hidden.
- **Cross-cutting check:** the Python UDPFS handlers in RE-SERV-028…035 are *not* shadowed by the actually-launched subclass: `tests/test_server_override_contract.py` pins `AutoUdpfsServer`'s overrides to exactly `{__init__, _get_or_create_session, _handle_data, _handle_discovery, _sendto, run}` — none of the cited handlers — so the findings apply to the running server, not just to a dead base class.

---

### RE-SERV-028 — VERIFIED
- **Citations:** All exist at the cited lines. `udpfs_server/udpfs_server.py:2294` `_handle_getstat`; `:2356` `st = os.stat(resolved)`; `_resolve_path` at 1184, `_resolve_entry` at 1214.
- **Mechanism:** Re-derived. `_resolve_path` (1184) realpaths and returns `None` when the target escapes the root; `_resolve_entry` (1214) realpaths only the parent, so it returns `<root>/link.bin`. In `_handle_getstat`, `real_file_exists = (resolved is not None and os.path.exists(resolved)) or real_entry_exists` with `real_entry_exists = os.path.lexists(entry_path)` — `True` for the link itself — so the not-found branch (which would have sent `-EACCES` for `resolved is None`) is skipped and `os.stat(None)` is reached. The surrounding handler catch is `except OSError` (line 2363 region); `TypeError` escapes it, and the only remaining catch is `Session._run`'s generic `except Exception` at line 3275, which logs and sends nothing. `_handle_open` indeed lacks the bug: its guard at ~1820 (`resolved is None and compressed_resolved is None and virtual_view is None` → `-EACCES`) fires first.
- **Execution:** My probe: root in `/tmp`, symlink `link.bin` → file in a sibling outside dir. `_resolve_path('/link.bin') -> None`, `_resolve_entry('/link.bin') -> /tmp/probeB/root/link.bin`; `_handle_getstat` **raised `TypeError: stat: path should be string, bytes, os.PathLike or integer, not NoneType`, replies = []** (no reply captured). Control, exactly as filed: a dangling symlink to an *inside* target was answered normally, reply `('getstat', -2)` (ENOENT).
- **Fix:** Coherent and LESS-NOT-MORE: one `resolved is None` → `-errno.EACCES` branch mirroring `_handle_open`'s existing guard. No new machinery.

### RE-SERV-029 — VERIFIED
- **Citations:** All exist. `_handle_bread` 2483, `_handle_bwrite_req` 2551, `_complete_bwrite` 2156, `bd_read` 862, `bd_write` 870; `bd_sector_count` assigned at 807 and — confirmed by grep — used *only* at 1055 (banner) and 2307 (GETSTAT capacity): `grep -n bd_sector_count` returns exactly lines 796, 807, 1055, 2307.
- **Mechanism:** Both handlers cap `total_size`/`declared` against `max_transfer_bytes` and refuse offsets past 2⁶³−1, and neither compares `sector_nr + sector_count` against `bd_sector_count` or any file size. `_complete_bwrite` writes via `bd_write` → `os.pwrite(bd_fd, data, sector_nr*sector_size)` (or `fh.obj.seek/write` for file handles), which extends a regular file past EOF.
- **Execution (mine, required re-execution):** 4-sector image, **2,048 bytes** before. `BWRITE_REQ sector=100 count=1` (handle 0) → ack; one 512-byte WRITE_DATA chunk → **`WRITE_DONE 0` and the host file was 51,712 bytes after** — byte-for-byte the filed result (2,048 → 51,712). Same probe on a *file* handle (handle 5, 2,048-byte file, sector 100): also `WRITE_DONE 0`, file also grew to **51,712 bytes**. BREAD mirror on a *fresh* 4-sector image: `BREAD sector=100 count=1` → reply `result=0, data length 0` (silent empty success, no error); in-range `BREAD sector=3` → `result=512`, first byte `0x03` (control sane).
- **Fix:** BD leg is exactly right (the bound already exists and is already advertised). Caveat on the file-handle leg only: a plain file has no fixed sector count, so "refuse past the handle's current size" changes grow-by-seek semantics for file handles; it is defensible for sector-addressed BWRITE but is the one part of the fix worth an explicit maintainer decision. It does not affect the verdict — the mechanism and the BD execution are as filed.

### RE-SERV-030 — VERIFIED
- **Citations:** All exist. `_handle_write_data` 2083, slice `chunk_data = payload[8:8 + chunk_size]` at 2090, `write_data.extend` at 2122, `self.write_total_chunks = total_chunks` at 2123; `_handle_write_req` 2031 (declared `size` checked against the cap and never stored — the `Session` write state has no size field); `_complete_bwrite` 2156 (adds `expected_size` to `stats['bytes_written']` unconditionally, replies 0).
- **Mechanism:** Python slicing truncates silently, the truncated chunk still increments `write_received_chunks`, completion is decided purely by `write_received_chunks >= total_chunks` with `total_chunks` re-read from the *latest* chunk, and no byte-count comparison against the WRITE_REQ size or the BWRITE `sector_count × sector_size` exists anywhere in the path.
- **Execution (mine, three probes):** (a) File WRITE declared size 8, single chunk claiming `chunk_size=8` but carrying 3 bytes (`b'abc'`) → completed, `WRITE_DONE 3`, file content exactly `b'abc'`, no error. (b) BWRITE declaring 4 sectors (2,048 B) on a file handle, delivering one 100-byte chunk → **`WRITE_DONE 0`, exactly 100 non-zero bytes in the file, and `stats['bytes_written']` credited the full 2,048**. (c) `total_chunks` re-read, which the original only implied: chunk 0 declared `total_chunks=5`, chunk 1 declared `total_chunks=2` → the write **completed at chunk 1** (`WRITE_DONE 4`, file `b'AABB'`).
- **Fix:** Coherent: drop chunks with `len(payload)-8 < chunk_size`, latch `total_chunks` from chunk 0, compare assembled length to the declared size at completion. It needs one stored field (the WRITE_REQ size, currently discarded) — a concrete-bug-justified minimal addition, and it is exactly what the Go Edge session already records (`WriteExpectedLen`, `internal/session/session.go`). LESS-NOT-MORE compliant.

### RE-SERV-031 — VERIFIED
- **Citations:** All exist. `Session.__init__` at 3212–3213 inserts `server.bd_fh` itself as handle 0 (`self.handles[BLOCK_DEVICE_HANDLE] = server.bd_fh`); `_handle_read` uses `fh.obj.read(size)` (1996 region), `_handle_lseek` uses `fh.obj.seek(...)` (2206 region), `_complete_write` uses `fh.obj.write(data)` (2144 region). BREAD/BWRITE by contrast go through the positional `bd_read`/`bd_write` (862/870).
- **Mechanism:** One `FileHandle`, one underlying file object, one shared cursor, inserted by reference into every session's table. No lock guards the file-opcode path; the `bd_lock` fallback inside `bd_read`/`bd_write` shares the same cursor on hosts without `pread`/`pwrite`, as filed.
- **Execution:** My probe with two real `Session` objects on one 4-sector image (sector *i* filled with byte *i*): `sessA.handles[0] is sessB.handles[0] is server.bd_fh` → `True`. Session B READ handle 0 (512 B) → first byte `0x00` (sector 0). Session A LSEEK handle 0 → 1536 (replied 1536). Session B's next READ → **first byte `0x03` (sector 3), not sector 1** — deterministic cross-session cursor theft, exactly as filed.
- **Fix:** Either option is coherent; refusing file-opcode access to handle 0 (`-EBADF`) is the LESS option and the one I'd put first, since BREAD/BWRITE already cover the device. Per-session cursor + `bd_read`/`bd_write` is more code for compatibility nobody has demonstrated a need for.

### RE-SERV-032 — VERIFIED
- **Citations:** All exist. The two per-session opens are literally at 1923 and 1928 (`f = open(actual_resolved, py_mode)`); the lock census is exact — `grep` for Lock in the file returns only `sessions_lock`, `send_lock`, `bd_lock` (784–786) plus their uses. No per-path registry exists: `grep -n "EBUSY"` in `udpfs_server/udpfs_server.py` and `udpfs_server/ps2servers_core.py` → **zero hits**.
- **Mechanism:** As filed, and as the finding itself honestly frames it — a semantics gap, not a violated claim. Read-only is enforced globally/per-handle (`writable()`), but nothing records that a path already has a writable handle, in any session.
- **Execution:** Strengthened beyond the filed READ: two real sessions each ran the server's own `_handle_open` on the same `/save.bin` with RDWR flags — **both were granted a handle, the two file objects are distinct (`is not`), both report `writable() == True`** — and interleaved writes through them (A writes `AAAA` at 0, B seeks to 0 and writes `BB`) produced final head `b'BBAA'` with no error to either writer. Note the Go Edge UDPFS *does* implement this (`reserveWriter`, `internal/udpfs/server.go:134ff`, used at `operations.go:427`), which confirms the Python gap is an omission, not a design both implementations share.
- **Fix:** One server-level dict + one check on the open path, cleared on close/reap — justified in the finding and proportionate; it mirrors the Edge mechanism rather than inventing a new one. LESS-NOT-MORE compliant, with the stated Med risk (handle-reap cleanup must be careful).

### RE-SERV-033 — VERIFIED
- **Citations:** All exist. `_handle_read` 1969–2010 with `except OSError … -e.errno` at 2008; `_handle_bread` same pattern at 2537; `_flags_to_mode` 1469–1495 returns `'wb'` for O_WRONLY|O_TRUNC and `'ab'` for O_WRONLY|O_APPEND; `FileHandle` 554–590 has `writable()` (whose own comment explains the `errno is None` problem for writes) and no `readable()` counterpart.
- **Mechanism:** `io.UnsupportedOperation` is a subclass of `OSError` with `errno is None` (verified in-process: `io.UnsupportedOperation('x').errno is None`, `issubclass(..., OSError) is True`), so the read raises, the `except OSError` catches it, and computing the error reply `-e.errno` itself raises `TypeError`. No gate prevents reaching `read()` on a non-readable handle.
- **Execution:** My probes, both legs: (a) handle opened `'wb'`, then READ_REQ → handler logged `READ error: read` and then **raised `TypeError: bad operand type for unary -: 'NoneType'`, replies = []**. (b) handle opened `'ab'`, then BREAD_REQ → identical outcome, **same `TypeError`, replies = []**. No reply is sent in either case, exactly as filed.
- **Fix:** Both tiers are sound; the `readable()` gate symmetric with the existing `writable()` gate is the better fix and reuses an established pattern in the same class. The cheap `-(e.errno or errno.EIO)` variant is also coherent. LESS-NOT-MORE compliant either way.

### RE-SERV-034 — VERIFIED
- **Citations:** All exist. CSO: only `block_size == 0` checked (cso.py:76), offset-table read `(self._num_blocks + 1) * 4` at cso.py:84. ZSO: `header_size` taken from the header at zso.py:82 and used as the table seek, same table construction at zso.py:102, only `block_size == 0` checked (86). CHD: only `hunk_size == 0` checked (chd.py:280), `ctypes.create_string_buffer(hunk_size)` at chd.py:314. Base cache evicts by block *count* (base.py `_get_block`, `while len(self._cache) >= self._cache_size`), default cache size 32 (base.py:31ff), so cached bytes scale with the header's `block_size`. `open_compressed` (udpfs_server.py:364) constructs every wrapper with no `strict` argument; `limits.py:12-15` defines `MAX_DISC`/`MAX_BLOCK`/`MAX_TABLE`/`MAX_DECODE`, and every use of them is behind `if self._strict:` (grep of `strict` in `compressed_iso/` + the server).
- **Mechanism:** Non-strict `_parse_header` implementations trust header geometry for allocation sizes; the strict/inspection path that enforces the ceilings is opt-in and is not the serving path. `MemoryError` is indeed not in `_handle_open`'s catch tuple `(ImportError, ValueError, OSError, struct.error)` (lines 1867/1898).
- **Execution (CSO leg, mine):** 24-byte file, header claiming 64 GiB uncompressed / 2,048-byte blocks → `CsoFileWrapper` **tracemalloc peak 134,223,090 bytes (≈134.2 MB)** — the filed figure — before failing with `ValueError: Invalid CSO: offset table truncated` (claimed `_num_blocks` 33,554,432). ZSO/CHD/cache legs verified by reading, matching the finding's own confidence split (EXECUTED for CSO, READ for the rest). One precision worth recording: the CHD buffer allocation at line 314 happens *after* `chd_open` succeeds, so that leg additionally needs a file libchdr accepts; the size it allocates is still purely header-chosen.
- **Fix:** Correctly scoped: import only the numeric ceilings into the non-strict parsers, explicitly *not* the inspection path's format restrictions. That distinction is the LESS-NOT-MORE-compliant version of the fix.

### RE-SERV-035 — WEAKENED
- **Citations:** Exact. `_handle_mkdir` 2370–2392; `os.mkdir(resolved, mode if mode else 0o755)` at line 2392; `mode` is the raw uint16 unpacked at the top of the handler (`struct.unpack('<BBH', payload[:4])`). No masking anywhere between the wire and the call.
- **Mechanism (true part):** The wire field reaches `os.mkdir` verbatim, masked only by the process umask — that is code-certain, and at least one special bit demonstrably survives the full server path (below).
- **Execution (where the filed claim is broader than reality):** Through the server's own `_handle_mkdir` in this sandbox: mode `0o1755` → created directory mode **`0o1750`** (sticky bit **survived**; low bits umask-masked). But mode `0o2755` → created directory mode **`0o750`** — the **setgid bit was stripped**, and a direct `os.mkdir` control gave the same result (`0o2755 → 0o755`, `0o2000 → 0o0`, while `0o1755 → 0o1755`). So the filed example "an unauthenticated client can create setgid (0o2000) … directories" did **not** reproduce here: setgid-on-mkdir survival is host/filesystem/group dependent (the kernel/filesystem clears it in this environment even for root), whereas sticky survival is unconditional.
- **Narrower true scope:** *The wire `mode` is passed to `os.mkdir` unsanitised; special bits are therefore passed through to the OS, and the sticky bit demonstrably survives end-to-end (0o1755 → 0o1750 via the handler). Setgid survival — the finding's headline example and its group-inheritance impact — depends on the host filesystem and the server process's group membership and was refuted in this sandbox.* The defect (no masking) and the one-line fix are unaffected.
- **Fix:** `mode & 0o777` — one expression, exactly LESS-NOT-MORE; still correct and still worth doing on the narrowed claim.

### RE-SERV-036 — VERIFIED
- **Citations:** All exist. `BlockDevice.seek/read/write` at udpbd_server.py:118/121/127; the only extent check is at 208–209 in `_handle_read`, gated on `getattr(self.bd, "is_raw", False)`; `_handle_write` (238) has no bounds check at all. Contrast citations also exact: `launcher/raw_storage.py` `seek` (37ff) raises EINVAL outside capacity and its `write` raises `PermissionError(EROFS)`; `launcher/virtual_exfat.py` `seek` (228ff) raises EINVAL out of bounds.
- **Mechanism:** The plain file device never sets `is_raw` (my probe: `getattr(dev, 'is_raw', False) is False`), `seek` is a bare `self._f.seek(sector * SECTOR_SIZE)`, `read` deliberately zero-pads past EOF, `write` writes wherever the seek landed.
- **Execution (mine, fake socket, all three filed numbers reproduced):** (1) `CMD_WRITE sector=1000 count=2` on a 4-sector (2,048 B) image was accepted with `_write_left = 1024`; the following WRITE_RDMA **grew the file 2,048 → 513,024 bytes** and sent WRITE_DONE with result `0`. (2) Direct `seek(100)` + 512-byte write on a 1,024-byte image → **51,712 bytes**. (3) Read at sector 500 of a 2-sector image returned **512 zero bytes**, and `_handle_read` for sector 500 actually emitted a full 518-byte packet whose payload was all zeros — a fabricated zero disc, not an error.
- **Fix:** Dropping the `is_raw` gate so the *existing* check covers every backend is the minimal correct fix and adds nothing new. The caveat about keeping `read`'s padding for a short final in-image read is right and preserved in the finding.

### RE-SERV-037 — VERIFIED
- **Citations:** All exist. `BlockDevice.write` at 127–130 is exactly `if self.read_only: return`; `_handle_write` (245ff) prints the warning, then still seeks and arms `_write_left = sector_count * SECTOR_SIZE` (260); the `-1` result is computed only at 298ff, once `_write_left` reaches 0. Contrast citations exact: both other backends raise `PermissionError(errno.EROFS, …)` from `write` (raw_storage.py:78ff, virtual_exfat.py:319ff).
- **Mechanism / Execution (mine):** On a read-only file device: `BlockDevice.write(b'Z'*512)` **returned normally, no exception, file unchanged** (silent no-op). `CMD_WRITE` (2 sectors) armed `_write_left = 1024` despite read-only, with nothing sent. After the first 512-byte RDMA chunk: `_write_left = 512`, **still nothing sent**. Only after the second chunk did the server send WRITE_DONE — bytes `6604ffffffff`, i.e. result **−1** — with the file byte-identical throughout. The full transfer is swallowed before failure is reported, exactly as filed.
- **Fix:** Make `BlockDevice.write` raise `PermissionError(errno.EROFS, …)` like both sibling backends; the existing `OSError` path in `_handle_write_rdma` then produces the prompt −1 with zero handler changes. Exemplary LESS-NOT-MORE (conformance to an existing in-repo contract).

### RE-SERV-038 — VERIFIED
- **Citations:** All exist. `_scan_dirs` 319 (flat branch: `if os.path.isdir(path)` — follows a symlinked `DVD`/`CD`); `_entry_for` 439 (flat branch never calls `_contained`; `os.path.getsize`/`os.path.isfile` follow symlinks); `_contained` defined once at 389; its only caller is `_path_aware_entry` at 501ff. The docstring claim is at 262ff ("there is no traversal surface").
- **Mechanism:** The recursive walker refuses symlinked directories (`_is_real_dir`), but the default flat profile has no equivalent guard for files or for the scan directories themselves.
- **Execution (mine, `GameIndex` on scratch trees):** Flat profile (default, `path_aware is False`): symlink `DVD/SLUS_999.99.Escape.iso` → outside file of 12,345 bytes was **indexed with size 12,345, listed in `games.csv` (`SLUS_999.99,SLUS_999.99.Escape.iso,DVD`), and returned by `lookup()`**. Second tree: `DVD` itself a symlink to an outside folder was **scanned and served** (both the outside folder's conventional game and its non-conventional `secret.iso` were indexed; `lookup('SLUS_888.88.ViaDir.iso')` returned an entry). Control on the same first tree with `profile=PROFILE_RIPTOPL_PATH_AWARE`: the escape was skipped with the log line `skipped (links outside the games folder): SLUS_999.99.Escape.iso`, its CSV contained only the normal game, and `lookup()` for the escape returned `None`.
- **Fix:** Apply the already-written `_contained()` in `_entry_for` for both profiles and skip escaping scan dirs in `_scan_dirs` — no new mechanism, exactly as proposed.

### RE-SERV-039 — VERIFIED
- **Citations:** All exist. `_op_read` 761 (`r.fixed(16)  # stateid: any`); `_op_write` 941–959 (stateid read and discarded at 942, then writes at any offset subject only to `_writable()` and the FBIG check); `_op_setattr` 961–974 (same shape, `os.truncate` to any size); `_op_open_confirm` 880 / `_op_close` 886 fabricate `seqid+1` stateids without checking existence; `_op_open` 892–939 holds the access-mode (1/2/3) and GUARDED checks that nothing on the write path consults. `_Handles` at 261ff does use a random 8-byte prefix (`secrets.token_bytes(8)`), as the finding concedes.
- **Mechanism:** No open-state table exists for WRITE/SETATTR to consult; the only gates are a valid filehandle (via `s.cfh` from LOOKUP) and the global `--allow-writes` gate (`_writable()`). The finding's own scoping is accurate: this breaks the server's internal contract, it is not an exposure escalation beyond the documented no-auth model.
- **Execution (mine, hand-built COMPOUND records via `handle_record`, `allow_writes=True`):** PUTROOTFH + LOOKUP + LOOKUP + GETFH (no OPEN ever issued), then WRITE with an **all-zero stateid** at offset 0 → all op statuses 0, and the file's head read back as **`PWNED…`** (`b'PWNEDNAL-DATA-0123456789'`). SETATTR in a fresh compound with a **fabricated `0xff`*16 stateid** → all statuses 0, file size became exactly **10,485,760 bytes (10 MiB)**. Control: the same WRITE against `allow_writes=False` was refused (WRITE op status 30, others 0), so the documented gate does work — it is simply the *only* gate.
- **Fix:** Both offered options are honest; the LESS option (document that opens are advisory and authorisation is `--allow-writes` + a valid filehandle) is the proportionate one, and the finding correctly refuses to leave validated-looking checks unenforced. Tracking issued stateids per connection is more machinery than the documented threat model justifies.

### RE-SERV-040 — VERIFIED
- **Citations:** Exact. `native/ps2servers-edge/internal/smb/handlers.go:499-507`: the reply is built with `binary.LittleEndian.PutUint16(params[4:], uint16(written&0xFFFF)) // CountLow` and `binary.LittleEndian.PutUint16(params[8:], 0) // CountHigh` — the field names are the code's own comments. `MaxMessage = 128 << 10` at `internal/smb/wire.go:105`.
- **Mechanism:** Deterministic arithmetic: a WRITE_ANDX payload is bounded by the received message (up to 128 KiB), not by 64 KiB, so `written` can exceed 65,535 and every multiple of 65,536 above the low word is dropped from the reply. The filed probe numbers are internally exact: 70,000 − 65,536 = **4,464** = the reported low word, high word 0.
- **Execution:** n/a for me — no Go toolchain in this sandbox (`go: command not found`); I did not re-run the probe. The verdict rests on the literal code above plus the arithmetic, which leaves no runtime freedom. (The original's EXECUTED label is consistent with everything visible in source.)
- **Fix:** `uint16(written >> 16)` into `params[8:]` — one expression, correct, LESS-NOT-MORE.

### RE-SERV-041 — VERIFIED
- **Citations:** Exact. Defect at handlers.go:479: `n := int(dlenLow) | int(dlenHigh)<<16`. The fixed sibling is at 426 (`maxCount := uint32(maxLow) | uint32(maxHigh)<<16`) with the explanatory comment block at 402–425 naming mipsle/mips/arm/386 and the sign-bit mechanism. `req.slice` is at `internal/smb/server.go:514`: `if off < 0 || off > len(r.msg) || n <= 0 { return nil }`.
- **Mechanism:** On a 32-bit build, `int(dlenHigh)<<16` with `dlenHigh >= 0x8000` sets the sign bit → `n` negative → `slice` returns nil → `WriteAt(nil, …)` writes zero bytes and returns no error → the handler replies STATUS_SUCCESS with count 0. A silent no-op write, reachable only on the 32-bit targets — which is precisely the scope the finding claims (it does not claim the bug fires on 64-bit hosts, and it does not: there `int` is 64-bit and the expression is positive, then clamped by `slice` to the message). No defeating guard exists: nothing validates `n` between line 479 and the `slice` call.
- **Execution:** n/a — no Go toolchain, and the defect is additionally platform-conditional (needs a 32-bit build), so a 64-bit re-execution could not have settled it either. READ verdict, matching the finding's own High (READ) confidence.
- **Fix:** Assemble as `uint32` before narrowing, identical to the in-file sibling fix. Coherent, minimal, and removes a per-handler trap.

### RE-SERV-042 — VERIFIED
- **Citations:** Exact. `openAndX` stores `os.Open(local)` (read-only) at handlers.go:224ff; `ntCreateAndX` also stores an `os.Open` handle (359ff); `writeAndX` at 481–493 never touches `of.fh` — it does `os.OpenFile(of.path, os.O_WRONLY, 0)`, `WriteAt`, `Close`. The `openFile` struct (`internal/smb/server.go:366-371`) has fields `path`, `fh`, `isDir`, `size` only — **there is no access-mode field anywhere to check**, which is stronger than the finding states.
- **Mechanism:** Per-handle access does not exist in this server: both open paths grant a read-only descriptor, no mode is recorded, and the write path re-opens by path with write access. A read-only handle is therefore writable, and closing/evicting the stored `fh` has no effect on writability. The only write gate in `writeAndX` is the server-global `cfg.ReadOnly` check at its head.
- **Execution:** n/a for me — no Go toolchain. The verdict rests on the literal code path quoted above; there is no branch in `writeAndX` that could refuse based on the handle. The filed 70,000-byte probe is consistent with the code (and with RE-SERV-040's probe, which necessarily used this same re-open path).
- **Fix:** Sound and net-negative in machinery: one field on an existing struct, write through the handle's own descriptor, delete the per-write open/close. The finding's justification (no new mechanism/abstraction/configuration) is accurate.

### RE-SERV-043 — VERIFIED
- **Citations:** Exact. `device.writeAt` at `internal/udpbd/server.go:89-95`: `if d.readOnly { return nil }`. `handleWrite` at 440ff: the read-only block (~452–456) records an error and logs `"UDPBD WRITE on a read-only image, ignoring"` and has **no `return`**; the handler then unconditionally arms `s.write = writeState{… left: sectorCount * SectorSize}` and does `s.totalWrite += sectorCount * SectorSize` (461–468). Failure (`WriteDone` −1) is produced only in `handleWriteRDMA`'s completion block (~521–533), gated on the sequence running to completion and on `s.dev.readOnly`.
- **Mechanism:** Both stacked claims re-derived: (1) the device primitive reports success for work it did not do, deceiving any error-trusting caller; (2) the handler says "ignoring" and does the opposite — arms state and counts the full byte count. A client that aborts mid-sequence gets no `WriteDone` at all. The contrast with UDPFS's BWRITE (refuses read-only up front, Python `_handle_bwrite_req`) is real.
- **Execution:** n/a for me — no Go toolchain. Verdict rests on the literal branches quoted; the filed probe values (`writeAt` → `err=<nil>`, file unchanged; `handleWrite` → `write.left=1024, totalWrite=1024` for a 2-sector write) are exactly what this code must produce.
- **Fix:** Both halves are one-liners against existing patterns (`writeAt` returning an error; `handleWrite` returning after its warning). Coherent, LESS-NOT-MORE.

### RE-SERV-044 — VERIFIED
- **Citations:** Go: `handleInfo` at server.go:344, unconditional `s.write = writeState{}` at 353; `handleWrite` unconditional overwrite at 461; RDMA drop test `st.left <= 0 || !peerMatch` at 476ff. Python: `_write_left`/`_write_owner` are single server fields (udpbd_server.py:151–152); `_handle_info` resets `_write_left` at 198; `_handle_read` resets it at 214 (with **no** owner check — unlike Go's `handleRead`, see below); `_handle_write` overwrites state/owner at 248–260; `_handle_write_rdma` does check `_write_owner` at 263ff.
- **Mechanism:** One global write slot per server in both implementations; the RDMA side is peer-guarded but the state it protects is destroyed by unguarded INFO/READ/WRITE from any peer, and the victim is never sent a `WriteDone` failure. The Go contrast cited in the finding is real and I verified it: Go `handleRead` (line 393) resets state **only** `if s.write.left > 0 && (s.write.peer == peer.String() || … peerIP …)` — peer-guarded — while Go `handleInfo`/`handleWrite` are not. (In Python, `_handle_read` is *also* unguarded, which is why the Python half reproduces via READ specifically.)
- **Execution (Python half, mine):** Client A `CMD_WRITE` → `_write_left = 1024`, owner `(A, cmdid 1)`. An unrelated **READ from a different address** → `_write_left = 0`. A's subsequent WRITE_RDMA was dropped with **nothing sent to A** (no WRITE_DONE). Re-armed A, then an **INFO from a different address** → `_write_left = 0` again. Both destructive paths reproduced exactly as filed. Go half: READ-verified (no Go toolchain).
- **Fix:** Guard the resets/overwrite by idle-or-same-peer, and send the displaced peer a `WriteDone` failure — small, local, and it extends the discipline both codebases already apply on the RDMA side. LESS-NOT-MORE compliant.
- **Duplicates note:** this finding is *by construction* one mechanism in two implementations (the consolidation is stated in the finding itself), not two findings claiming the same code twice.

### RE-SERV-045 — VERIFIED
- **Citations:** Exact. `anyTransferInFlight` at `internal/udpfs/status.go:61-82`, with `active := st.Streaming || st.WriteActive` at line 75; `st.Streaming = true` at `internal/udpfs/negotiation.go:497` on the accepted-DATA path; default `PeerTimeout` of one hour at `internal/udpfs/server.go:190-191` (`cfg.PeerTimeout = time.Hour`), with expiry driven off that timeout at server.go:426.
- **Mechanism:** Grep over the whole Edge tree for `Streaming =`: in non-test code it is set to `true` only at negotiation.go:497 and to `false` only in `State.ResetTransport` (`internal/session/session.go:162`) — i.e. session reset/handoff — plus session expiry. It is never cleared when a transfer *ends*. `WriteActive`, by contrast, is a genuine in-flight signal (set at `operations.go:583` / `blockdev.go:297`, cleared by `ResetWrite`, session.go:119). So `Busy` (serverState, status.go:39-46, whose own comment gives the power-cut rationale the finding cites) latches from a session's first DATA packet until reset or up to an hour of idleness.
- **Execution:** n/a — READ finding, and no Go toolchain in any case. The latch is a whole-tree grep fact, quoted above, not a runtime inference.
- **Fix:** Base Busy on actual in-flight work (`WriteActive` plus a send-window/read-in-progress signal) instead of the latched negotiation flag. Coherent; it deletes a wrong input rather than adding machinery, though wiring a read-in-flight signal is the one part that is genuinely M-effort, as filed.

---

## Tally

| Verdict | Count | Findings |
|---|---|---|
| VERIFIED | 17 | RE-SERV-028, 029, 030, 031, 032, 033, 034, 036, 037, 038, 039, 040, 041, 042, 043, 044, 045 |
| VERIFIED-DRIFT | 0 | — |
| WEAKENED | 1 | RE-SERV-035 (unsanitised `mode` and sticky-bit survival confirmed; setgid survival is host-dependent and was refuted in this sandbox) |
| REFUTED | 0 | — |
| BLOCKED-ENV | 0 | — (Go findings 040–045 are READ-verified: no Go toolchain exists here, stated per-finding; no verdict depends on UDP, so the sandbox UDP block blocked nothing) |
| **Total** | **18** | RE-SERV-028 … RE-SERV-045 |

**Duplicates:** No two findings in this range duplicate each other. Three pairs are the same *defect class* in *different code*, each independently true and separately actionable, and are not duplicates: RE-SERV-029 (Python UDPFS extent) vs RE-SERV-036 (Python UDPBD extent); RE-SERV-037 (Python UDPBD read-only write) vs RE-SERV-043 (Go Edge UDPBD read-only write — parallel implementations, different files); RE-SERV-044 is a single consolidated finding covering both implementations by design. RE-SERV-040/041/042 all sit in Go `writeAndX` but are three distinct defects (reply count truncation, 32-bit length assembly, ignored handle access) with distinct fixes.

## CANDIDATES

None. Every adjacent anomaly I probed while verifying (the `-e.errno` pattern in neighbouring handlers, UDPBD Python's zero-padded out-of-range read packet, GETSTAT's dangling-inside-symlink path) turned out to be either already inside a filed finding's stated scope (RE-SERV-029/033/036) or guarded (the write paths are pre-gated by `writable()`, which is why RE-SERV-033 is correctly scoped to READ/BREAD only). No new defect is claimed without evidence I produced myself, and I produced none outside the filed range.

---

## Part C — RE-SERV-046–070 (mixed Go Edge + Python)


- **Pin confirmation:** `git -C ~/workspace/audit-clones-2026-11-13/PS2-Servers rev-parse HEAD` → `c5f021c4d38698860f56b03498adc261dbb8d8dd` — matches the assigned pin exactly. All reading was in that clone (read-only); all executions were in `/tmp/verifyC/` copies.
- **Method:** refute-first — every cited file/function/string grepped and opened, defeating guards/callers/tests hunted first, then direct probes where mandated/feasible.
- **Environment note:** 32-bit (GOARCH=386) Go binaries cannot run in this sandbox at all (a trivial 386 test also dies with `signal: trace/breakpoint trap`); that affects only the *execution* leg of RE-SERV-068, whose mechanism is re-derived arithmetically with a 64-bit control probe (see there). No finding in this batch failed only because of the UDP `sendto` block.

---

### RE-SERV-046 — VERIFIED
- **Citations:** Go `native/ps2servers-edge/internal/smb/handlers.go` `transaction` (filed 840-871, actual function starts ~841) and `server.go:740-741` (`case ComTransaction: return c.transaction(r)`); Python `smbv1_server/smbserver_opl.py:1331ff` `h_transaction`. All exist.
- **Mechanism:** Re-derived both. Go: `transaction(r *req)` never touches `r.params`/`r.data`; it sorts share names and unconditionally builds the level-1 NetShareEnum reply via `trans2Reply`. Python: `h_transaction(conn, r)` never reads `r` beyond the signature; unconditionally returns `STATUS_SUCCESS` with the share-enumeration body. No guard, caller pre-filter, or test anywhere in either dispatch path discriminates by transaction name/RAP function. Defeating-evidence hunt: none found.
- **Execution:** n/a (READ mechanism is unambiguous; both handlers are pure functions of the share list).
- **Fix:** Sane, LESS-NOT-MORE compliant — parse-and-refuse (`StatusNotImplemented`) is the server's own pattern for other unimplemented commands. This RE number is itself the consolidation of the Go (GOPROTO-17) and Python (PYSMB1-08) filings of the *same mechanism in parallel implementations* — correctly merged, not a duplicate pair of RE numbers.

### RE-SERV-047 — VERIFIED
- **Citations:** `internal/webui/api.go` `sanitizeSSE` (~62-70, with the filenames/peer-addresses rationale comment verbatim), sanitised service-log and backlog emits, unsanitised live emit. All exist.
- **Mechanism:** The live loop is literally `fmt.Fprintf(w, "data: %s\n\n", line)` on the subscription channel line, while the two sibling emits in the same `HandleLogs` pass through `sanitizeSSE(line)`. Live lines come from the same `LogBuffer`. No downstream sanitisation exists.
- **Execution:** n/a.
- **Fix:** One call-site change to `sanitizeSSE(line)` — minimal, no new mechanism. Correct.

### RE-SERV-048 — VERIFIED
- **Citations:** `launcher/raw_storage.py:358` `argv = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ...]`, `creationflags`-equivalent `flags = 0x08000000`; hardened pattern at `launcher/windows_setup.py:57-90` (`_powershell_executable`, hijacking docstring verbatim) and copies at `smbv1_server/smbserver_opl.py:1553-1563`, `smb2_server/smb2_server.py:1496-1502`. All exist.
- **Mechanism:** My own census grep for `powershell` across `launcher/`, `smbv1_server/`, `smb2_server/`: exactly 3 absolute-path resolutions (windows_setup, smbv1, smb2) and exactly 1 bare-name invocation (`raw_storage.py:358`). (`launcher/directlink.py` calls the imported `_powershell` helper — not a fourth resolution.) Minor non-defeating nuance: the hardened resolvers themselves fall back to the bare name if the System32 file is absent; `raw_storage` uses the bare name unconditionally, which is the filed defect.
- **Execution:** n/a (Windows-only path, READ).
- **Fix:** Reuse `windows_setup._powershell`/its resolver — sane, removes the hand-rolled flag too.

### RE-SERV-049 — VERIFIED
- **Citations:** `launcher/ra_web.py:385-390` `container.innerHTML = achievements.map(a => \`…${a.title …}…${a.description …}…${a.points …}\`)`. Exists verbatim.
- **Mechanism:** Every other text assignment in the same `render()` uses `textContent` (grep: lines 326-375, including the unlock banner at 375); only the achievements list uses `innerHTML` with unescaped interpolation. Data flow confirmed: `launcher/achievements.py:200-215` builds `achievements_data` (title from `res.qualified.metadata`, i.e. remote patch/RA data) and passes it via `RAState.set_game` (`ra_web.py:72-79`) into `state.achievements`, which `render()` consumes. No escaping helper exists in the file. Same-origin `/logout` POST handler is at the bottom of the same script.
- **Execution:** n/a.
- **Fix:** `textContent`/`createElement` rows like the rest of `render()` — sane, aligns with the file's own pattern.

### RE-SERV-050 — VERIFIED
- **Citations:** `launcher/caduceus.py` `Account.request` (~117-123): `params.update(y=key)`, URL built as `…/API/<endpoint>.php?` + `urlencode(params)`, key from `read_secret(self.profile / "apikey")`; `read_secret` at 55-90. All exist.
- **Mechanism:** The Web API key is in the GET query string on every `Account.request` call (all endpoints, incl. `API_GetUserCompletionProgress` at line 333). Mitigation noted in the finding is real: `open_remote` uses the `NoRedirect` opener (caduceus.py:26-32), so the `Referer`-on-redirect leak is closed, but proxy/history/crash-report URL logging is not. Contrast files exist (`ra_credentials.py:1-5` "never stores … Web API keys"; `ra_resolver_process.py` "No credentials go on argv").
- **Execution:** n/a.
- **Fix:** Correctly conditional as filed ("where the RA API accepts it, POST; where it does not, document") — the RA Web API is GET-oriented, so the documentation half may be the only implementable half. That caveat is already in the finding; it does not weaken the exposure claim.

### RE-SERV-051 — VERIFIED
- **Citations:** `launcher/caduceus.py:329-356` `Bridge.progress`. Exists.
- **Mechanism:** `game_id, earned, maximum = 0, 0, 0` at entry; the `kind == "G"` branch fills `total` and per-row `earned`/`total` but never assigns the header `earned`/`maximum`; the `else` ("A") branch does (`maximum, earned = len(rows), sum(...)` before slicing). Reply body at ~368 formats `kind, page, total, game_id, earned, maximum, …` from those zeros. So every library ("G") reply header is 0/0 while its rows are correct — exactly as filed.
- **Execution:** n/a (pure code path, no branch recomputes the header).
- **Fix:** Direction right, one sanity caveat the finding understates: the "G" branch fetches only one page (`o=page*3, c=3`, `Results[:3]`), so "sum of row earned/MaxPossible" would be *page-local*, not library-wide. A fully correct fix needs aggregation over all pages (or an API aggregate field); a page-local sum is better than 0/0 but must not be labelled library progress. Defect itself unaffected.

### RE-SERV-052 — VERIFIED
- **Citations:** `.github/workflows/edge-build.yml` generated `WHICH-DEVICE.txt` block; `native/ps2servers-edge/cmd/ps2servers-edge/main.go:150` (and 301, 359). All exist.
- **Mechanism:** Workflow text (my read, ~lines 387-397): `Access : read-only by default. Pass --read-only=false to allow the console to write (saves).` followed by a Quick Start running `udpfs --root …` with no `--read-only`. Binary source: all three subcommands use `fs.Bool("read-only", envBool("RO", false), "serve files read-only; omit to allow the console to write saves")` with an explicit comment "Writable by default, matching the Desktop/Core server". Docs agree with the binary, not the archive text: `docs/EDGE.md:22` ("writes are allowed by default"), `docs/EDITIONS.md:39`, `docs/INSTRUCTIONS.md:16-17`. `grep -rn WHICH-DEVICE tests/` returns nothing (rc=1) — no test guards the text.
- **Execution (mine, mandated re-execution):** Built in `/tmp/verifyC/edge` with Go 1.27.4 (`~/workspace/tools/go1.27.4/bin`): **binary size 11,366,917 bytes**. `/tmp/verifyC/ps2servers-edge udpfs -h` lists `-read-only` as a plain bool flag with help text "serve files read-only; omit to allow the console to write saves" and **no default-true** (Go prints no `(default true)`); `smb -h` and `udpbd -h` likewise show bare `-read-only` flags. Actual default for all three: **writable**. Claim vs behaviour compared directly: the shipped text is false, the binary is writable by default.
- **Fix:** Rewrite the generated block to match EDGE.md's wording — sane, text-only, LESS-NOT-MORE exemplary.

### RE-SERV-053 — VERIFIED
- **Citations:** Filed as `smb2_crypto.py:227-232`; actual path `smb2_server/smb2_crypto.py`, the `_bcrypt_cmac` self-test at ~227-232: `if cmac(bytes(16), b"") != bytes.fromhex("66e94bd4ef8a2c3b884cfa59ca342b2e") and aes_cmac_pure(bytes(16), b"") != cmac(bytes(16), b""): return None`. Exists verbatim.
- **Mechanism:** The condition is an `and`: a *correct* bcrypt backend fails clause 1 (its CMAC ≠ the constant) but passes clause 2 (pure == bcrypt), so the backend is accepted only via the fallback comparison — the hardcoded clause can never be true for a correct backend.
- **Execution (mine, mandated):** On the `/tmp` copy — `AES128(bytes(16)).encrypt_block(bytes(16)).hex()` = **`66e94bd4ef8a2c3b884cfa59ca342b2e`** (i.e. the filed constant is exactly AES-128(zero key, zero block) = the RFC 4493 §2.3 *L* block); `aes_cmac_pure(bytes(16), b"").hex()` = **`4387c14b46ef7e176dceefa862d72ff9`**, and the OpenSSL backend (`_openssl_cmac()(bytes(16), b"")`) independently returns the identical `4387c14b…f9`. Both filed values reproduced exactly.
- **Fix:** Replace the constant with `4387c14b46ef7e176dceefa862d72ff9` or drop the hardcoded clause and keep only the pure-comparison the OpenSSL path already uses — sane; the second option is the LESS-NOT-MORE one.

### RE-SERV-054 — VERIFIED
- **Citations:** Filed as `smb2_paths.py:70-102`, `part[-1]` at :97; actual path `smb2_server/smb2_paths.py`, `check_component` at ~70-102, trailing-dot/space test `if part[-1] in (".", " "):` confirmed. Docstring: "Raises PathError" / "Separate from Share.resolve so a server that walks a directory (the NFS server's READDIR)…". `Share.resolve` skips `part in ("", ".")` before calling it (~148-150). All as filed.
- **Mechanism:** Empty string passes the length, stream-separator, illegal-char, and reserved-name tests, then `part[-1]` raises `IndexError`, breaking the documented `except PathError` contract for any direct reuser.
- **Execution (mine):** `check_component("")` on the `/tmp` copy → **`IndexError: string index out of range`** (not `PathError`). Reproduced exactly.
- **Fix:** Empty-component `PathError` at the top — one guard, sane.

### RE-SERV-055 — VERIFIED
- **Citations:** `smbv1_server/smbserver_opl.py` `h_create_directory` (~1070-1096, comment "carries BufferFormat=0x04…" and strip `r.data[1:] if r.data[:1] == b"\x04"`) and `h_check_directory` (~1098-1110, decodes from byte 0, no strip). Both exist.
- **Mechanism:** `Share.resolve` treats the leading `\x04` as a literal first path component, so a spec-formed name resolves to a nonexistent `<root>/\x04DVD` path.
- **Execution (mine):** Drove the real handler on the `/tmp` copy against a share containing `DVD/`: plain `b"DVD\x00"` → `STATUS_SUCCESS` (0x00000000); spec-formed `b"\x04DVD\x00"` → **`STATUS_OBJECT_NAME_NOT_FOUND` (0xC0000034)**. Reproduced exactly as filed.
- **Fix:** Strip a leading `0x04` exactly as `h_create_directory` does — sane, symmetric.

### RE-SERV-056 — VERIFIED
- **Citations:** `smbv1_server/smbserver_opl.py:640-695` `h_negotiate`: `try: idx = dialects.index("NT LM 0.12") / except ValueError: idx = 0`, then unconditional `STATUS_SUCCESS` NT LM 0.12 response. Exists verbatim.
- **Mechanism:** No "no dialect chosen" (0xFFFF) or error path exists for the SMB1 branch (the SMB2 multi-protocol branch above it does return early only when an SMB2 dialect *is* found).
- **Execution (mine):** Drove the real handler: dialect list `["PC NETWORK PROGRAM 1.0"]` → **success, DialectIndex 0**; empty list → success, DialectIndex 0; `["NT LM 0.12"]` → success, DialectIndex 0 (correct there). Reproduced.
- **Fix:** Error status / 0xFFFF when "NT LM 0.12" is absent — sane.

### RE-SERV-057 — VERIFIED
- **Citations:** `udpfs_server/ps2servers_core.py:477-521` `AutoUdpfsServer.run()`; `udpfs_server/ps2servers_core.py:195-220` `_send_specific` (re-raises `OSError` on INFORM, deliberate, with the verbose-log comment); base `udpfs_server/udpfs_server.py:1048-1112` `run()`. All exist.
- **Mechanism:** Base loop calls `_handle_discovery`/`_route_data` *inside* the per-socket `try: … except OSError: pass`. Core's override restructures: `recvfrom` in its own narrow `try/except OSError: continue`, header parse in another, then `self._handle_discovery(data, addr)` / `self._route_data_via(...)` **unguarded**, inside a `while` whose only handler is `except KeyboardInterrupt`. `_handle_discovery` sends INFORMs via `_canonical_inform` → `_send_specific`, which re-raises `OSError`. One failed INFORM send therefore propagates out of `run()` and kills the server. (Scope is in fact slightly broader than filed: `_sweep_idle_sessions()`/`_emit_metrics()` are equally unguarded in Core's loop — same fix covers them.)
- **Execution:** n/a — correctly not claimed by the finding; a live send failure in this sandbox would be BLOCKED-ENV, not evidence. READ mechanism is complete.
- **Fix:** Per-datagram `except OSError: continue` around dispatch, mirroring the base — sane.

### RE-SERV-058 — VERIFIED
- **Citations:** `udpfs_server/ps2servers_core.py:529-543` (`_env_int` defaults for `--port`/`--sector-size`/`--compression-cache-size`), :566-567 (`_env_float` for `--tx-delay-ms`); definitions at `udpfs_server/udpfs_server.py:3297-3312` (`return int(v, 0) if v else default` / `return float(v) if v else default`, no guard). Neighbouring `_env_duration`/`_env_max_transfer`/`_env_max_sessions` exist lines away and are built never to raise. All as filed.
- **Mechanism:** The defaults are evaluated at `build_parser()` construction time, so a bad env value raises before argparse exists to format a usage error.
- **Execution (mine):** `PORT=oops` → `build_parser()` raises **`ValueError: invalid literal for int() with base 0: 'oops'`**; `TX_DELAY_MS=oops` → **`ValueError: could not convert string to float: 'oops'`**. Both reproduced. (`SECTOR_SIZE`/`COMPRESSION_CACHE_SIZE` share the identical helper — READ, as filed.)
- **Fix:** Warn-and-fall-back in the two helpers (fixes both entry points) — sane, matches the file's own standard.

### RE-SERV-059 — WEAKENED
- **Citations:** `udpfs_server/ps2servers_core.py:548-550` (`default=os.environ.get("PROTOCOL_MODE", "auto").lower()` on a `choices=` argument), :105-112 (`AutoUdpfsServer.__init__` validation), :591-690 (`main()`, construction unguarded); launcher alias table at `launcher/servers.py` (`_PROTOCOL_MODE_BY_NAME`, `"proper" → "standard"` at line 268, `protocol_mode_value` strips+lowercases and maps). All exist.
- **Mechanism — true part (executed by me):** `PROTOCOL_MODE=proper`: `build_parser().parse_args([])` succeeds with `protocol_mode='proper'` (argparse does not validate a *default* against `choices`), and constructing `AutoUdpfsServer(protocol_mode='proper')` raises **`ValueError: protocol_mode must be auto, standard, or modulo`**, which `main()` (source read in full) does not catch around construction. Legacy/unknown values from the environment do detonate as a traceback. Core defect stands.
- **Mechanism — false sub-claim (executed by me, defeating evidence):** The finding states "A trailing space (`PROTOCOL_MODE="modulo "`) fails the same way." It does not. `AutoUdpfsServer.__init__` normalises with `(protocol_mode or "auto").strip().lower()` (line 109) — I constructed the server with `"modulo "`, `" modulo"`, `"MODULO"`, and `"Standard"` and **all constructed without error**; only unmapped values (`"proper"`) raised. Whitespace/casing variants are already saved by the constructor's strip+lower, even though the parser default itself only lowercases. (The one wrinkle — `main()`'s exact-match `args.protocol_mode == PROFILE_MODULO` fast path is bypassed by `"modulo "` — is harmless: the Auto path then applies modulo itself, including `single_port`.)
- **True scope:** Only values that are not, after strip+lower, one of auto/standard/modulo — i.e. legacy aliases like `proper` and outright garbage — slip past argparse and raise the uncaught `ValueError`. Whitespace and case variants do not fail.
- **Fix:** Still sane in reduced form: map the env default through the launcher's existing alias table / `parser.error` on unrecognised values. The filed "normalise the same way `__init__` does (strip + lower)" half is already done by `__init__` and would change nothing.

### RE-SERV-060 — VERIFIED
- **Citations:** `udpfs_server/compressed_iso/zso.py:165-171` retry loop (`while compressed_data: … compressed_data = compressed_data[:-1]`); contrast `limits.py:78-90` `indexed_block` (caps padding at `(1 << align) - 1`, comment "Never retry once per byte of an attacker-controlled, potentially megabyte-sized span"). Both exist; serving path is non-strict by default (`open_compressed` in `udpfs_server.py:379` constructs the wrapper with no `strict=`).
- **Mechanism:** Unbounded one-byte-at-a-time retry; cost is attempts × shrinking-input decompression.
- **Execution (mine):** Built a minimal valid-header ZSO in `/tmp` with one 20,000-byte garbage (`0xFF`) block and a counting `lz4.block.decompress` wrapper: **`ValueError` after 20,000 attempts in 0.52 s**. Filed: 19,990 attempts in 0.48 s for its garbage block — the small count difference is input-dependent (garbage content), not a mechanism difference; magnitude and timing match.
- **Fix:** Bound retries to `(1 << align) - 1` exactly as `indexed_block` does — sane, already the in-tree pattern.

### RE-SERV-061 — VERIFIED
- **Citations:** `udpfs_server/compressed_iso/cso.py:115-150`, `zso.py:125-163`, `base.py:53-97`. All exist; serving wrappers are constructed non-strict (see RE-SERV-060), and the strict `limits.indexed_block` path does validate span/decoded length — the contrast is real.
- **Mechanism:** Re-derived all three sub-defects. (1) CSO: for compressed blocks `read_size = next_offset - offset` with no sign/span check — a non-monotonic table makes it negative and `self.file.read(negative)` reads to EOF; ZSO's `compressed_size = next_offset - offset` is the same arithmetic. (2) CSO pads *any* short inflate to `block_size` unconditionally (`if len(decompressed) < self.block_size: decompressed += b"\x00" * …`), not only for the final block. (3) ZSO uncompressed branch `return compressed_data` however short, and `base.read()` does `result.extend(block_data[block_offset:block_offset + read_size]); self.pos += read_size` — position advances by the requested `read_size`, not by the bytes actually appended, so a short block both short-serves and skips.
- **Execution:** n/a (filed READ/Med; each sub-mechanism follows directly from the quoted lines and Python's documented `read(-n)` semantics).
- **Fix:** Refuse negative/overspanning `read_size`, raise on short non-final blocks, advance `pos` by bytes appended — sane, mirrors the strict path's invariants.

### RE-SERV-062 — VERIFIED
- **Citations:** `udpfs_server/udpfs_server.py:1563-1567` `_status_flags`; `udpfs_server/router_status.py:66-76` flag definitions (`FLAG_UDPBD = 1<<1`, `FLAG_DECOMPRESSES = 1<<4`). Exist.
- **Mechanism:** Full-repo grep: `_status_flags` is defined exactly once and `FLAG_UDPBD`/`FLAG_DECOMPRESSES` are referenced *nowhere* outside their definitions — no Core override sets them either. `_status_flags()` returns `FLAG_UDPFS | (FLAG_READ_ONLY if read_only)`. The facts are available on the same object (`self.bd_fh is not None`, `self.enable_compression`, `COMPRESSED_EXTENSIONS` — all used elsewhere in the same file, e.g. the `run()` banner at 1053-1065).
- **Execution:** n/a.
- **Fix:** Set the two bits from those predicates — sane, no wire change (bits already reserved).

### RE-SERV-063 — VERIFIED
- **Citations:** `udpfs_server/compressed_iso/base.py:99-127` `CompressedFileWrapper.seek`; `udpfs_server/udpfs_server.py:2189-2230` `_handle_lseek` (maps `ValueError` → EINVAL); `chd_cuebin.py` `MemoryFile.seek` (~310, `if whence not in (0,1,2): raise ValueError`) and `ChdBinFile.seek` (~418, explicit `else: raise ValueError("invalid whence")`). All exist.
- **Mechanism:** The wrapper's seek has `if/elif/elif` for whence 0/1/2 and **no else** — any other whence falls through to the clamp `self.pos = max(0, min(self.pos, self.uncompressed_size))` and returns success, so the handler's `ValueError`→EINVAL arm can never fire for CSO/ZSO/CHD wrappers, while plain files (Python `file.seek` raises `ValueError` on bad whence) and both CUE/BIN views raise. The secondary divergence is also real: the wrapper clamps overshoot to `uncompressed_size` where plain files report the true position.
- **Execution:** n/a (READ; the fall-through is structural).
- **Fix:** `else: raise ValueError` in the wrapper — one branch, lets the existing handler mapping work uniformly. Sane.

### RE-SERV-064 — VERIFIED
- **Citations:** `udpbd_server/udpbd_server.py:82-116` `BlockDevice.__init__`/`sector_count`; :201-203 INFO reply `struct.pack("<II", SECTOR_SIZE, self.bd.sector_count())`; raw-backend contrast at `launcher/raw_storage.py:30-33` (capacity must be "positive, 512-byte aligned, and fit UDPBD's 32-bit sector count (less than 2 TiB)"). All exist.
- **Mechanism:** `__init__` validates only the target *type* (regular file / not a raw device), then records `self.size` with no upper bound and no `size % SECTOR_SIZE` check. `sector_count()` is bare `size // SECTOR_SIZE`. The run loop's dispatch catches `(struct.error, IndexError)` and logs only when verbose (lines 362-365) — so an INFO pack overflow is a silent no-reply at default verbosity, exactly as filed.
- **Execution (mine, upgrading the filed INFERRED leg):** Created a sparse exactly-2 TiB file (2,199,023,555,552 bytes) in `/tmp`, constructed the real `BlockDevice` (accepted without complaint), `sector_count()` = **4,294,967,296 (0x100000000)**, and `struct.pack("<II", 512, sector_count)` raised **`struct.error: 'I' format requires 0 <= number <= 4294967295`**. Separately, a 1,000-byte image was accepted with `sector_count() == 1` and **no warning** — 488 tail bytes silently unservable.
- **Fix:** Mirror the raw backend's construction-time validation (reject `sector_count() > 0xFFFFFFFF`, warn/reject non-multiple sizes) — sane, precedent exists in-repo.

### RE-SERV-065 — VERIFIED
- **Citations:** `http_server/http_server.py:543-580` `_rebuild_flat` (`path_entries[rel_path_key] = entry` at ~564, *before* the `seen_names` duplicate `continue` at ~567-575), :763-784 `lookup` (path entries consulted first; ambiguity guard only on the basename fallback), :607-670 `_rebuild_path_aware` ("Only what the list advertises is served"). All exist.
- **Mechanism:** Suppressed duplicates are recorded in `_path_entries` before suppression, and `lookup()` checks `_path_entries` first, so the suppressed copy is reachable by qualified path while unreachable by bare name and absent from the CSV.
- **Execution (mine):** Real `GameIndex` on a `/tmp` tree — root `SLUS_333.33.Same.iso` (1,024 B) + `DVD/SLUS_333.33.Same.iso` (4,096 B): scan logged "serving \<root copy\>, ignoring \<DVD copy\>", CSV listed only the root copy, `lookup("SLUS_333.33.Same.iso")` → `None` (ambiguous), but **`lookup("DVD/SLUS_333.33.Same.iso")` → the suppressed 4,096 B DVD entry**. Reproduced exactly as filed, byte sizes included.
- **Fix:** Move the `path_entries` assignment below the `seen_names` check — one-line reorder, sane.

### RE-SERV-066 — VERIFIED
- **Citations:** `http_server/http_server.py:898-931` `_dispatch` (generic `except OSError` → `_send_short(500, …)`), :1006-1034 `_serve_game` (206 status+headers via `end_headers()` before the body), :1085-1090 `_send_bytes` short-read raise. All exist.
- **Mechanism:** In `_send_bytes`' manual path, a short read does `self.close_connection = True; raise OSError("short read on …")` — the raise propagates through `_serve_game` (headers already sent) into `_dispatch`, whose `OSError` clause unconditionally writes a complete 500 response onto the same socket, i.e. into the byte stream the driver is counting. The earlier `except (ConnectionResetError, BrokenPipeError)` clause does not catch a plain `OSError`. Scope note (not a weakening): the *sendfile* `OSError` path in `_send_bytes` is already contained (returns `False`, drops the connection) — the live vector is the manual/compressed short-read raise and any other post-`end_headers()` `OSError` that propagates, which is what the finding describes.
- **Execution:** n/a (filed INFERRED/Med; control flow re-derived in full from the cited lines).
- **Fix:** Headers-sent flag; in the `OSError` branch, if sent, just `close_connection = True` and return — sane, preserves the file's own "drop, don't desynchronise" contract.

### RE-SERV-067 — WEAKENED
- **Citations:** `http_server/http_server.py:867-881` `_handle_for` (cache hit iff `_cached_path == entry.path`), :530-541 `refresh` (rebuilds entries under the lock, no connection-cache invalidation — per-connection caches live on handler instances, reset only in `setup()`/`finish()` at 833-865), :1043-1057 `_send_bytes` (length from `entry.size`). All exist.
- **Mechanism — true part:** The cache key is the path string alone — not size, mtime, compressed flag, or entry identity. `refresh()` *does* rebuild on replacement ( `_signature_of` includes per-file size+mtime, a comment there explicitly discusses re-dumped ISOs), producing a new entry for the same path, while a keep-alive handler keeps serving the old open handle paired with the new entry's `size`/`compressed`. Same-path replacement (a re-dumped ISO under the same name — the finding's second example) genuinely hits this: old-larger streams unadvertised bytes; old-smaller short-sends into the RE-SERV-066 path.
- **Mechanism — false example (defeating evidence for that half):** The finding's first example, "a `.chd` swapped for an `.iso` of the same advertised name", **cannot** hit the bug: `entry.path` is the filesystem path, which differs between `Game.chd` and `Game.iso`, so `_cached_path == entry.path` fails and the handler reopens. Only replacements that keep the identical filesystem path are affected.
- **True scope:** Stale-handle/new-entry pairing on an existing keep-alive connection after an index refresh, **for same-path file replacements only**.
- **Fix:** Keying on `(path, size, compressed)` (or entry identity) remains sane and sufficient for the true scope; reopen cost is once per refresh.

### RE-SERV-068 — VERIFIED
- **Citations:** `native/ps2servers-edge/internal/compression/lz4.go:13-29` `readLen`, :37-40 the checks and slice it feeds; `image.go:420-460` `decodeBlock` (`maxRaw := blockSize*2 + 4096`; header `blockSize` valid up to 16 MiB per the cache comment at image.go:35-40). All exist.
- **Mechanism:** `readLen` accumulates `n += int(src[i])` per 0xFF extension byte with no cap. Re-derived: a token `0xF0` followed by 8,500,000 × `0xFF` and a terminating `0x00` yields `lit = 15 + 8,500,000×255 = 2,167,500,015`. On 64-bit that is caught by `lit > dstSize-len(dst)`. On a 32-bit `int` it wraps to −2,127,467,281, which passes *both* checks at lz4.go:37 (`lit > len(src)-i` and `lit > dstSize-len(dst)` are both false for a negative), and `src[i:i+lit]` then panics with slice-bounds-out-of-range. The raw-size bound permits the input: 8.5 MB of extension bytes fits under `maxRaw` for a 16 MiB-block image (~33.5 MB). The match-length use of `readLen` shares the unbounded accumulation (its negative case degrades to a no-op loop rather than a panic — the literal path is the crash).
- **Execution (mine):** 64-bit control probe in a `/tmp` copy of the package: the crafted input above returns **`invalid literal length`** (no panic) on amd64 — exactly the predicted 64-bit behaviour. The 32-bit leg is **BLOCKED-ENV for execution only**: GOARCH=386 test binaries cannot run in this sandbox at all (a trivial 386 test dies identically with `signal: trace/breakpoint trap`), so the panic itself was not executed; the verdict rests on the arithmetic re-derivation above, which is also the basis the finding itself claimed (Med/INFERRED).
- **Fix:** Fail inside `readLen` as soon as `n` exceeds the maximum legitimate length (`dstSize` or remaining input) — sane, local, prevents overflow on every word size.

### RE-SERV-069 — VERIFIED
- **Citations:** `internal/logging/logging.go:45-49` (text-mode `emit`: header `Fprintf` with no newline, one `Fprintf` per field, bare `Fprintln`), `internal/webui/logbuffer.go:28-57` (`Write` splits each call on `\n` and stores every fragment, pushing each to subscribers), `cmd/ps2servers-edge/main.go:518` (`io.MultiWriter(os.Stdout, logBuffer)`). All exist. JSON branch is a single `Fprintln` (logging.go:36) — unaffected, as filed.
- **Mechanism:** `LogBuffer.Write` has no partial-line reassembly, so each producer fragment becomes a stored/pushed line; a bare `"\n"` write splits to `["", ""]`, of which the first empty string is stored (only the *last* empty fragment is skipped) — hence the empty ring slot in the filed evidence.
- **Execution (mine):** Probe test in the `/tmp` Edge copy driving the real `Logger` (text mode): `Info("server started", {"addr": …, "shares": 2})` + one fieldless `Info` produced **5 separate `Write` calls** — `"[info] server started"`, `" addr=127.0.0.1:8082"`, `" shares=2"`, `"\n"`, `"[info] plain line\n"` — which through `LogBuffer.Write`'s documented splitting yields exactly the filed 5 stored lines (including the empty one) from 2 log calls.
- **Fix:** Build the line in memory and emit once, at the producer — sane; correctly rejects the alternative of adding partial-line buffering to `LogBuffer` as well.

### RE-SERV-070 — VERIFIED
- **Citations:** `internal/webui/config.go:195-263` `ValidateConfig`; `cmd/ps2servers-edge/main.go:176-191, 218-222`; `internal/webui/schema.go:114, 120, 122`. All exist.
- **Mechanism:** All three divergences re-read and confirmed. (1) Sector size: API `SectorSize <= 0 || > 1<<16` (message claims range 1-65536, so 513/1000 pass); binary rejects `*sectorSize%512 != 0` (and ≤0 / >65536); schema `Min 512, Max 65536, MultipleOf 512`. (2) Compression cache: API checks only `< 0`; binary rejects `> compression.MaxCacheBlocks` (4096); schema `Max 4096`. (3) Peer timeout: `ValidateConfig` subjects `udpfs.peer_timeout` only to the `noControlChars` string-field check — `"soon"` passes; binary requires a parseable duration in [1m, 24h]; schema declares `MinValue "1m", MaxValue "24h"`. The validator's own doc comment states preventing exactly this ("a value that produces a server which refuses to start") as its purpose.
- **Execution:** n/a (READ; three independent code-to-code contradictions).
- **Fix:** Enforce the binary/schema rules in `ValidateConfig`, reusing the binary's duration parsing — sane; correctly directs *not* loosening the binary/schema.

---

## Duplicates within this range

- **No two distinct RE numbers in 046-070 share a mechanism.** RE-SERV-046 is itself a consolidation of two source filings (Go + Python) of one mechanism in parallel implementations — merged before numbering, not a duplicate pair.
- Related-but-distinct neighbours, checked and *not* duplicates: 058/059 (same file, different helpers — raising int/float parsers vs unvalidated `PROTOCOL_MODE` default); 060/061 (same ZSO/CSO serving wrappers — unbounded LZ4 retry vs unvalidated offsets/short-block fabrication); 065/066/067 (same HTTP server — flat-index suppression leak vs 500-after-206 vs stale per-connection handle; 067's short-send consequence flows into 066's path but the defects are independent); 047/069 (same log pipeline — 069's producer fragmentation feeds the stream 047's live path fails to sanitise; independent fixes).

## Tally

| Verdict | Count | Findings |
|---|---|---|
| VERIFIED | 23 | 046, 047, 048, 049, 050, 051, 052, 053, 054, 055, 056, 057, 058, 060, 061, 062, 063, 064, 065, 066, 068, 069, 070 |
| VERIFIED-DRIFT | 0 | — |
| WEAKENED | 2 | 059, 067 |
| REFUTED | 0 | — |
| BLOCKED-ENV | 0 | — (execution leg only of 068's 32-bit panic; see that block) |
| **Total** | **25** | RE-SERV-046 … RE-SERV-070 |

## CANDIDATES (new defects found during verification)

None. No new defect with file:line evidence emerged from this batch's re-derivation and probes. Two observations were checked and are explicitly **not** candidates: (a) `main()` in `ps2servers_core.py` compares `args.protocol_mode == PROFILE_MODULO` exactly, so an env value of `"modulo "` bypasses that fast path — but the `AutoUdpfsServer` path it falls into applies modulo (incl. `single_port`) itself, so behaviour is equivalent (see RE-SERV-059); (b) the LZ4 *match*-length accumulation in `lz4.go` shares RE-SERV-068's unbounded `readLen`, but its negative-wrap case degrades to a no-op loop rather than a panic — same finding, same fix, not a separate defect.

---

## Part D — RE-SERV-071–087 (Go launcher/core + shipped config)


**Pin confirmation:** `git -C ~/workspace/audit-clones-2026-11-13/PS2-Servers rev-parse HEAD` →
`c5f021c4d38698860f56b03498adc261dbb8d8dd` — matches the assigned pin exactly. Clone was read-only;
all executions ran against the clone's importable Python tree (read-only imports), `/tmp` copies
(Go probe test, CredentialStore fixtures), or a Go binary built to `/tmp/edgebuild/`.
No finding in this batch required UDP sendto, so no BLOCKED-ENV verdicts were needed.

---

### RE-SERV-071 — VERIFIED
- **Citations:** All exist. `native/ps2servers-edge/internal/webui/service.go:221-226` is
  `genericManager.Restart` calling `m.log.Info(...)` unconditionally and `return nil, nil`;
  `service.go:163-165` is openwrtManager's guarded `if m.log != nil`; `service.go:128-137`
  (`DetectServiceManager`) passes the logger through unchecked; `webui.go` `New` (79-…) sets
  defaults for LogLines/LogBuffer/MaxLogClients/Username but never requires `cfg.Log` non-nil.
- **Mechanism:** Re-derived. No defeating guard: `Serve` passes `s.cfg.Log` to
  `DetectServiceManager` (webui.go:173), and `HandleRestart` (api.go:227-241) serialises the
  returned slice — a nil slice renders as `"restarted": null` with `"ok": true`. The shipped
  binary always supplies a logger (main.go), so the panic path is library-construction-only,
  exactly as the finding's own INFERRED/latent note states.
- **Execution:** Probe test in a `/tmp` copy of the module:
  `(&genericManager{log: nil}).Restart("udpfs")` → **PANIC: runtime error: invalid memory
  address or nil pointer dereference** (test recovered and logged it). The false-success half
  is established by the code path above (`return nil, nil` → `{"ok": true, "restarted": null}`).
- **Fix:** Sound and minimal (guard like openwrtManager + return an error/manual-restart
  signal, inside the one method). LESS-NOT-MORE compliant.

### RE-SERV-072 — VERIFIED
- **Citations:** `status.go:48-49` — `// StatusQueryLen is the exact length of a query.` /
  `const StatusQueryLen = 10`. `status.go:107-126` — doc: "deliberately strict about length:
  a six-byte packet whose service ID is 0xF5F7 is the only thing that qualifies"; body tests
  `if len(packet) < StatusQueryLen`.
- **Mechanism:** Both halves of the doc are wrong as claimed. `MarshalStatusQuery` in the same
  file builds Header (2 B) + DiscoveryHeader (4 B) + `StatusMagic` (4 B) = 10 bytes, not six;
  and `<` (not `!=`) admits any packet ≥10 bytes with the right header/service/magic at
  bytes 6–10, trailing garbage included. No defeating comment elsewhere rescues the doc —
  the file header's "Safe to append because a longer packet … is still dropped" note actually
  supports the tolerance reading, making the IsStatusQuery doc the outlier.
- **Execution:** n/a (pure doc-vs-code; lengths counted from the marshal code).
- **Fix:** Sound — either rewrite the doc or tighten to `!=`; the finding correctly refuses
  to leave them disagreeing and does not prescribe which side wins.

### RE-SERV-073 — VERIFIED
- **Citations:** `launcher/servers.py:127-137` `_smb_argv` resolves
  `folder = (v.get("games_folder") or v.get("root_dir") or "").strip()` and raises
  `ValueError("SMB server requires a Games folder to share.")`; `servers.py:176-179`
  `_smb2_argv` does `"{}={}".format(share, v["games_folder"])` — direct indexing.
- **Mechanism:** Confirmed by reading both builders; no fallback or emptiness check in `_smb2_argv`.
- **Execution:** Ran the builders from the pinned tree:
  - `SMBV1.build_argv({"root_dir": "/games"})` → `['--share', 'games=/games', '--smb-version', '1']`
  - `SMBV2.build_argv({"root_dir": "/games"})` → **KeyError: 'games_folder'**
  - `SMBV2.build_argv({"games_folder": ""})` → `['--share', 'games=', '--smb-version', '2']` (silent empty share)
  - `SMBV1.build_argv({"games_folder": ""})` → ValueError as designed.
  All four outcomes match the finding exactly (SMBV3 shares `_smb2_argv`).
- **Fix:** Sound, minimal, mirrors the v1 resolution exactly.

### RE-SERV-074 — VERIFIED
- **Citations:** `launcher/windows_setup.py:146-157` — data-port rule skipped only
  `if not values.get("modulo_mode")`; `launcher/servers.py:271-299` `migrate_saved` deletes
  `modulo_mode`, setting `enforce_modulo=True` and `protocol_mode="Modulo"`;
  `servers.py:344-350` `_udpfs_argv` treats `enforce_modulo` or `modulo_mode` as single-port.
- **Mechanism:** After migration no GUI-produced dict can contain `modulo_mode` again (the
  migration's own docstring states the widget-drop rationale), so the firewall suppression
  keyed to it is dead for migrated/current configs. `posix_firewall.firewall_hint_lines`
  consumes the same port list shape, so the Linux-hint consequence holds.
- **Execution:** Ran `windows_setup.server_ports("udpfs", …)`:
  - `{"enforce_modulo": True, "protocol_mode": "Legacy / Modulo only", "data_port": 40000}` →
    includes `('UDP', 40000, 'UDPFS data')` (dead rule present)
  - `{"protocol_mode": "modulo", "data_port": 40000}` → same, includes 40000
  - `{"modulo_mode": True, "data_port": 40000}` → 40000 suppressed (only the retired key works)
- **Fix:** Sound — test the current vocabulary (reuse `protocol_mode_value`), keep the legacy
  check for un-migrated dicts. Related to RE-SERV-076 (same vocabulary-drift family; 076 itself
  names this finding as an instance) but a distinct mechanism/location — not a duplicate.

### RE-SERV-075 — VERIFIED
- **Citations:** `cooperative_stop` contract comment at `servers.py:72-77`; flag set at
  387/436/452/468/568/659 (SMBv1, SMBv2, SMBv3, UDPFS, HTTP, UDPBD — grep-confirmed, those six
  only); NFS def (624-652) and RetroAchievements def (712-746) have no flag (default False).
  `launcher/process.py`: non-cooperative children get `stdin=DEVNULL`, no `PS2SERVERS_STOP_PIPE`
  env, `request_stop()` returns False, `stop()` falls through to `terminate()`.
  `nfs4_server/nfs4_server.py:1094-1110`: `finally: server.stop()` plus SIGTERM→KeyboardInterrupt
  hook. `launcher/achievements.py:362-410`: SIGINT/SIGTERM `stopped` event, engine subprocess
  supervised in a loop, `finally` closes viewer/bridge/lease and unlinks the account-port file.
- **Mechanism:** Every server is dispatched through `launcher/serve.py:158`
  `install_stop_handlers()`, whose stdin-EOF watcher raises KeyboardInterrupt in the main
  thread — but it only installs when the stop-pipe env is present, which the launcher only
  sets for cooperative children. So the two excluded servers genuinely never receive the pipe.
  On POSIX, `terminate()`'s SIGTERM happens to reach NFS's hook / RA's `stopped` handler;
  on Windows it is TerminateProcess (documented at process.py:14-18), so NFS cleanup is
  skipped and the RA engine child (spawned with `subprocess.Popen`, untracked by the
  launcher) is orphaned holding UDP 18194. That last consequence is INFERRED from the
  documented Windows semantics — the finding labels it exactly that way.
- **Execution:** n/a (Windows stop semantics not executable in this sandbox; mechanism is
  fully code-established).
- **Fix:** Sound and correctly hedged — it requires the wake-on-EOF check per server before
  flipping the flag, and fixing the wait loop rather than the flag if a server fails it.

### RE-SERV-076 — VERIFIED
- **Citations:** RA `mode` Field `default="xerabora"` (servers.py:720) with
  `RA_MODE_CHOICES = (("xeRAbora", "xerabora"), ("Caduceus", "caduceus"))` (686); UDPFS
  `protocol_mode` default `"Auto (recommended)"` (485); HTTP `catalog_profile` default
  `"Legacy flat (OPL-HTTP)"` (581-582). Tolerant readers all present: `_achievements_argv`
  label-or-wire map (689-695), `protocol_mode_value` (302-312), `http_profile_value`
  (526-531), windows_setup mode tested against both `"caduceus"` and `"Caduceus"` (134-139),
  gui.py:881-885 wire→label restore conversion for UDPFS `protocol_mode` only, and
  `migrate_saved` writing the third spelling `"Modulo"` (293-298).
- **Mechanism:** `gui.py:715-718` fills the choice combobox with labels only and initialises
  the var to `str(f.default …)` — so the RA card's untouched var holds `"xerabora"`, a value
  not in its own dropdown's label list, while a touched card persists `"xeRAbora"`. The
  two-spellings-per-setting round-trip follows directly.
- **Execution:** Field-default census from the pinned tree:
  `udpfs protocol_mode → 'Auto (recommended)'`, `http catalog_profile → 'Legacy flat (OPL-HTTP)'`,
  `retroachievements mode → 'xerabora'` — exactly the claimed asymmetry.
- **Fix:** Sound direction (one stored vocabulary + load-time normalisation); effort/risk
  honestly rated M/Med. Not a duplicate of RE-SERV-074 — this is the systemic cause, 074 is
  one concrete victim in a different file.

### RE-SERV-077 — VERIFIED
- **Citations:** `windows_setup.py:142-144` — one fallback `_parse_port(values.get("port"), 1025)`
  for all three SMB keys; `servers.py:211-218` `_SMB2_FIELDS` port Field `default=1445`;
  SMBV1 `default_port=1025` vs SMBV2/SMBV3 `default_port=1445`. Third literal site confirmed:
  `gui.py:308` and `gui.py:389` both use `values.get("port") or ("1025" if key == "smbv1" else "1445")`.
- **Mechanism:** `server_ports`'s own docstring claims "one source so the two never disagree"
  (windows_setup.py:124-129) while its SMB fallback disagrees with the registry for v2/v3.
- **Execution:** Full registry sweep from the pinned tree — `server_ports(key, {})` vs
  `REGISTRY[key].default_port` for all eight servers: **only smbv2 and smbv3 mismatch**
  (1025 returned vs 1445 actual); smbv1, udpfs, http, nfs, udpbd, retroachievements all agree.
  The finding's scope is exact — no more, no less.
- **Fix:** Sound and minimal (take the fallback from `REGISTRY[key].default_port`).

### RE-SERV-078 — VERIFIED
- **Citations:** Module contract at `netinfo.py:1-8` (prefer private LAN over VPN/WSL);
  `primary_ip` (84-93) is the UDP-connect-to-8.8.8.8 default-route address; `_lan_rank`
  (122-131); `best_lan_ip` (224-234) returns `primary_ip()` whenever it is not loopback and
  consults the ranked candidates only for 127.x; `all_ipv4` also adds `primary_ip()`
  unconditionally. GUI use confirmed: `gui.py:1320` and `gui.py:1785` pre-fill from
  `best_lan_ip()`.
- **Mechanism:** The ranking is dead in the common case, as claimed. (Note the function's own
  docstring argues the opposite preference — "Prefer the interface that actually routes
  off-box" — which strengthens, not defeats, the finding: header, docstring, and code tell
  three different stories, and the header's stated purpose loses.)
- **Execution:** No VPN exists in the sandbox, so I executed the logic with the two network
  primitives monkeypatched to the full-tunnel case: `primary_ip() → "172.58.0.2"`,
  `all_ipv4() → ["192.168.1.50", "172.58.0.2"]` ⇒ `best_lan_ip()` returned **`172.58.0.2`**
  (the tunnel address), never consulting the rank-0 LAN candidate; with primary stubbed to
  `127.0.0.1` the same candidate list correctly yielded `192.168.1.50`. The bypass is
  deterministic from the code path.
- **Fix:** Sound (prefer best-ranked private candidate, fall back to primary).

### RE-SERV-079 — VERIFIED
- **Citations:** All verified in place. `ra_web.py:82-104` `note_unlock` emits unlock entries
  with `at` and no `ago`; `ra_web.py:106-140` `to_dict` sets `"webapi": self.logged_in` and
  hardcodes `"gaps": 0, "dupes": 0, "torn": 0` (122-124). `ra_notifications.py:14-28` `_event`
  requires `type(seconds_ago) is int` for `ago` — owned entries are therefore all rejected,
  and `UnlockTracker` (consumed by `ra_session.py:100`) can never fire on owned data.
  `ra_overview.py:58-76` derives status from `value.get("state") == 2` plus `measured`/`percent`;
  owned achievements are built in `achievements.py` (208) with an `unlocked` boolean and none
  of those fields → every owned achievement renders "Not unlocked". `achievements.py:274-285`:
  the owned main loop calls `update_console(packets=state.packets + 1, frames=state.frames + 1)`
  once per evaluation taken — synthetic counters, as claimed.
- **Mechanism:** No adapter between the dialects exists anywhere I grepped. Corroborating
  cross-effect: Caduceus `read_events` (caduceus.py:226-229) gates unlock notices on
  `event.get("ago", 999) <= 2`, so owned-runtime unlocks (no `ago`) are silently suppressed
  there too — same root divergence, second victim.
- **Execution:** n/a (schema divergence is fully determined by the quoted producers/consumers).
- **Fix:** Sound — converge on one `/state` contract and stop fabricating counters; the
  finding offers the either-side option for `view_model` rather than prescribing both.

### RE-SERV-080 — VERIFIED
- **Citations:** `achievements.py:247-256` `logout()`: `service.clear_account()` +
  `account_cancel.set()` inside `context_lock`, then
  `service.run_account(lambda session: session.logout(forget=True))`, then
  `if not submissions.close(): raise TimeoutError(...)`. `ra_account_worker.py:405-416`:
  `logout(forget=True)` ends in `self.store.clear()` — the on-disk token is erased.
  `ra_web.py:518-543` `do_POST /logout`: any exception → `503 {"ok": false}`, and
  `owner.state.logout()` runs only on success. `ra_submissions.py:8-59`: `Submissions`
  shares the caller's cancel event; once set, `offer()` returns False forever and the
  consumer thread has exited; `close()` default join timeout is 2 s.
- **Mechanism:** All three conflations hold: (1) a button labelled "Sign out" passes
  `forget=True` (credential destruction, recoverable only via CLI `--login`/`--import-credentials`);
  (2) `account_cancel` is never cleared anywhere and the submissions consumer is closed inside
  `logout()`, so the still-running process can never submit or ping again; (3) revocation
  (clear_account, cancel, credential clear) all happen *before* the fallible
  `submissions.close()`, so a timeout reports 503 "could not be completed" after the account
  is already revoked and credentials already deleted, with `RAState.logout()` skipped —
  UI state and real state disagree, as claimed.
- **Execution:** n/a (ordering is fully visible in the quoted code; the 2 s vs worker-deadline
  asymmetry is in the defaults: `Submissions.close(timeout=2)`, `AccountWorker(timeout=30)`).
- **Fix:** Sound — `forget=False` for web sign-out, fallible shutdown before publishing
  revocation (or report success once revocation is done), don't close the shared consumer.

### RE-SERV-081 — VERIFIED
- **Citations:** `caduceus.py:170-190` `Bridge.start` binds UDP 18197/18198 on `0.0.0.0`;
  `caduceus.py:262` — any packet matching only the format regex
  `CADQ([12]) ([a-f0-9]{32})` sets `self.peer = peer[0]` (no HMAC); `caduceus.py:205-229`
  `read_events` sends `RAU1` unlock notices to `self.peer` on port 18195;
  `caduceus.py:278` CADA is HMAC-checked against `self.key`; `caduceus.py:92-108`
  `pairing_key` stores the key as `CADUCEUS.KEY` inside the ART directory, and
  `Bridge.__init__` (151-153) sets `self.art = Path(folder) / "ART"` where `folder` is the
  games folder. The module docstring itself describes the key as a "share-scoped pairing
  capability".
- **Mechanism:** Both halves hold. CADQ's side effect (last-sender-wins notice routing) is
  unauthenticated; the 40 req/s cap in `run()` is global, not per-sender. The CADA HMAC key
  sits in the served games tree — the share-readability step is INFERRED from the folder's
  role as the OPL games root, exactly as the finding labels it (Med confidence is honest).
- **Execution:** n/a (LAN packet injection not exercisable here; the routing mutation is a
  single unconditional assignment in the quoted code).
- **Fix:** Sound and LESS-NOT-MORE as justified in the finding (remove a side effect,
  relocate an existing file — no new mechanism).

### RE-SERV-082 — VERIFIED
- **Citations:** `achievements.py:160-175` `resolve_job` constructs a fresh `Transport()` per
  job and POSTs `{"r": "gameid", …}` directly, outside `AccountWorker`; the whole block is
  wrapped in `except Exception: return IdentificationResult()`. `ra_http.py:28-47`:
  `Transport._next_request` pacing state is per-instance, so a fresh instance always starts
  unpaced. `ra_account_worker.py`: `AccountWorker` retains `minimum_interval` and arms
  `self._not_before = max(…, now + result.retry_after)` from `RateLimited` results
  (217-218, 253-265) — the mechanism this call circumvents.
- **Mechanism:** `RateLimited` is a `ValueError` subclass carrying `retry_after`
  (ra_http.py:14-18); the blanket `except Exception` in `resolve_job` flattens it into a
  generic transient `IdentificationResult()`, the worker's cooldown is never armed, and the
  owned service treats a bare result as retryable (`ra_owned_service.py` / receiver
  `complete(job)` "invalid set remains retryable"), so re-identification repeats the unpaced
  call at receiver pace. No defeating path: nothing upstream of `resolve_job` paces it.
- **Execution:** n/a (would require live RA traffic; the pacing-state lifetime and the
  exception swallow are both directly read).
- **Fix:** Sound — route through the existing `AccountWorker`, or minimally keep one
  long-lived `Transport` and propagate `RateLimited`.

### RE-SERV-083 — VERIFIED
- **Citations:** `caduceus.py:48-52` `pad`: `max(128, ((len(data) + 64) // 64) * 64)`;
  `ra_protocol.py:99-106` `pad_reply`: `max(128, ((len(body) + 63) // 64) * 64)`, docstring
  noting the base client uses spaces while "CADQ/CADA have a separate NUL-padding contract"
  — i.e. the fill byte differs by design, the length formula is not stated to.
- **Mechanism:** For a body already on a 64-byte multiple, `+64` rounds up one full extra
  block; `+63` does not. All non-aligned lengths agree.
- **Execution:** Ran both functions from the pinned tree over lengths 0–192. Divergences at
  exactly the aligned lengths, matching the finding precisely:
  len 128 → Caduceus **192** vs `pad_reply` **128**; len 192 → Caduceus **256** vs
  `pad_reply` **192**; lengths 1, 63, 64, 65, 127, 129, 191 all agree.
- **Fix:** Sound, one character (`+ 64` → `+ 63`).

### RE-SERV-084 — VERIFIED-DRIFT
- **Citations:** `ra_credentials.py:62-70` `_check_path` raises on any group/other permission
  bit (POSIX); `ra_credentials.py:95-105` `_prepare` uses `mkdir(exist_ok=True, mode=0o700)`
  — which does not repair an existing directory's mode — then immediately `_check_path`s it;
  `ra_credentials.py:124-141` `load()` calls `_prepare()` when the owned directory exists and
  propagates the `ValueError`. Call sites in `achievements.py` at 163 (inside `resolve_job`)
  and 230 (startup, in `run_owned_runtime`, unguarded) confirmed.
- **Mechanism:** The wedge is real, with one scope correction to the finding's wording:
  `load()` does **not** unconditionally call `_prepare()` / create directories on a read
  path — it short-circuits to `return None` when the `owned` subdir does not exist, without
  ever checking the profile dir. So a pre-existing 0755 *profile* dir alone does not wedge
  `load()` (it wedges `save()`); the `load()` wedge requires the `owned` subdir itself to
  exist with non-0700 permissions (or both dirs). The permanent-lockout consequence is
  otherwise exactly as described: no code path chmods, so every subsequent call raises the
  same error, and the startup `load()` turns it into a hard failure instead of signed-out.
- **Execution:** Fixtures in `/tmp` against the pinned tree:
  - profile 0755, no `owned` subdir → `load()` returned `None` (twice, no wedge);
    `save()` → **ValueError: Credential paths must be private to their owner**.
  - profile 0700, `owned` 0755 → `load()` → **ValueError**; `save()` → **ValueError**.
  - both 0755 → `load()` → **ValueError**; `save()` → **ValueError**; repeated calls keep
    raising (wedge persists, no self-repair).
- **Fix:** Sound (repair mode for directories the store owns; hard raise reserved for
  unexpected files/symlinks). The "make `load()` return None for a missing store without
  creating directories" half is already the current behaviour for a wholly missing store —
  worth keeping in the fix only for the owned-exists-but-unreadable case.

### RE-SERV-085 — VERIFIED-DRIFT
- **Citations:** The shipped file exists exactly as quoted —
  `packaging/systemd/ps2servers-edge-webui.env` (4 lines): line 4 is
  `PS2EDGE_ARGS=--webui-port 8082 --bind 0.0.0.0 --config-file /etc/ps2servers-edge/config.json`,
  no `--auth-pass`, no `--insecure`, and no warning comments, unlike the sibling env files
  (e.g. `ps2servers-edge-smb.env` carries a full warning block). `packaging/systemd/README.md`
  does disclose the trap in prose, as the finding concedes. **Citation drift:** the binary
  rule is not at `main.go:476-484` (those lines are inside `runWebUI`'s flag definitions);
  the actual refusal is `ErrInsecureBind` at `internal/webui/webui.go:63-65`, enforced in
  `webui.New` at `webui.go:98-102`, reached from `main.go:520` (`webui.New(...)` inside
  `runWebUI`, main.go:470).
- **Mechanism:** Non-loopback bind + empty password + not AllowInsecure → `New` returns
  `ErrInsecureBind` before anything serves; the subcommand exits 1.
- **Execution:** Built the pinned tree with Go 1.27.4 to `/tmp/edgebuild/ps2servers-edge` and
  ran it with exactly the shipped arguments:
  `ps2servers-edge webui --webui-port 8082 --bind 0.0.0.0 --config-file /etc/ps2servers-edge/config.json`
  → **exit 1**, stderr: `error: refusing to serve the web UI on a non-loopback address
  without a password: pass --auth-user/--auth-pass, or --insecure to accept the risk`.
- **Fix:** Sound (ship `--bind 127.0.0.1` or port the sibling warning block into the env file).

### RE-SERV-086 — VERIFIED
- **Citations:** `packaging/systemd/ps2servers@.service:5-7` quoted verbatim from the file:
  "Separate from ps2servers-edge.service, which runs the Go build and serves only UDPFS and
  UDPBD. Edge has no SMB at all, so a user who needs SMB … cannot use that unit"; line 2
  presents the template's instance keys as only "smbv1, udpfs or udpbd".
- **Mechanism:** Every clause fails against the shipped tree: the binary has `smb` and
  `webui` subcommands; `packaging/systemd/ps2servers-edge@.service` exists precisely to run
  them ("A template: %i is the Edge subcommand — udpfs, udpbd or smb"); the systemd README
  states "**Edge now serves SMB too**" and its table gives `ps2servers-edge@.service` as
  "**SMBv1**, UDPFS, UDPBD"; and `ps2servers-edge.service`'s ExecStart is `udpfs` only —
  it serves UDPFS, not "UDPFS and UDPBD". The three-way disagreement on the template's scope
  also holds: header (3 keys) vs `launcher/servers.py` REGISTRY (8 keys: smbv1, smbv2,
  smbv3, udpfs, http, nfs, udpbd, retroachievements) vs README table (SMBv1, SMB2/3, UDPFS,
  HTTP, UDPBD).
- **Execution:** Ran the built binary: `ps2servers-edge -h` lists `udpfs`, `udpbd`, `smb`,
  and `webui` subcommands; `smb -h` and `webui -h` both produce real flag sets.
- **Fix:** Sound — rewrite the header to match the systemd README and state the template's
  key scope in one place only.

### RE-SERV-087 — VERIFIED
- **Citations:** `packaging/openwrt/files/ps2servers-edge.init:123` and `:172` —
  `config_get status_port smb status_port -1` / `config_get status_port udpbd status_port -1`
  (grep-confirmed, both sites). Shipped `packaging/openwrt/files/ps2servers-edge.config`
  sets `option status_port '0'` in both the smb section (~line 47) and the udpbd section
  (~line 63), each with the rationale comment: off by default because udpfs owns UDP 62966
  and two owners race first-starter-wins.
- **Mechanism:** With the option present, UCI supplies `0` and the fallback never fires;
  deleting the option (a normal UCI "reset to default") makes `config_get` substitute `-1`,
  enabling the status listener on the port udpfs owns — recreating the exact race the
  config comments warn about. The init script's own comment at the udpbd instance even
  restates the race while its fallback defaults to the racing value. The safety property
  therefore lives only in the one config line, contradicted by the layer beneath it.
- **Execution:** Binary default confirmed from the built binary: both
  `ps2servers-edge smb -h` and `udpbd -h` show
  `-status-port int … (default -1)`; the flag definitions agree
  (`main.go:302` and `:363`, `envInt("STATUS_PORT", -1)`).
- **Fix:** Sound, minimal — change both init fallbacks to `0`.

---

## Tally

| Verdict | Count | Findings |
|---|---|---|
| VERIFIED | 15 | 071, 072, 073, 074, 075, 076, 077, 078, 079, 080, 081, 082, 083, 086, 087 |
| VERIFIED-DRIFT | 2 | 084 (load() short-circuits when `owned` is missing — the load wedge needs the `owned` subdir itself at non-0700; profile-only 0755 wedges save, not load), 085 (rule's real location is `webui.go:63-65, 98-102`, reached from `main.go:520`, not `main.go:476-484`) |
| WEAKENED | 0 | — |
| REFUTED | 0 | — |
| BLOCKED-ENV | 0 | — |
| **Total** | **17** | RE-SERV-071 … RE-SERV-087 |

**Duplicates within range:** none. Closest pair is RE-SERV-074 / RE-SERV-076 — 076 is the
systemic vocabulary-drift cause and itself names 074 as one instance, but the mechanisms,
files, and fixes are distinct (retired-key firewall suppression vs. choice-field stored
vocabulary). RE-SERV-077 is a different `_server_ports` defect (wrong numeric default, not
wrong key). RE-SERV-079 / RE-SERV-080 share the owned RA runtime but disjoint mechanisms
(schema divergence vs. logout semantics).

## CANDIDATES (new defects / extensions found during verification)

1. **Caduceus unlock notices are also suppressed for owned-runtime state (extension of
   RE-SERV-079's mechanism, second victim).** `launcher/caduceus.py:226-229` (`read_events`)
   gates `RAU1` notices on `event.get("ago", 999) <= 2`; owned-runtime unlock entries from
   `launcher/ra_web.py:82-104` carry `at` and no `ago`, so the gate is never satisfied and
   console unlock notices silently never fire on owned-runtime data — the same `at`-vs-`ago`
   divergence that blocks `UnlockTracker`, in a consumer the original finding did not cite.
2. **`best_lan_ip`'s own docstring contradicts the module contract (documentation drift
   adjacent to RE-SERV-078).** `launcher/netinfo.py:224-228` docstring says "Prefer the
   interface that actually routes off-box (the real LAN adapter)" while the module header
   (lines 1-8) states the opposite preference (private LAN over VPN/WSL). Even if the code
   is fixed per RE-SERV-078, the docstring would remain as a trap for the next reader.

---

## Part E — RE-SERV-088–100 (Go test suite) + Go baseline


- **Pin confirmation:** `git -C ~/workspace/audit-clones-2026-11-13/PS2-Servers rev-parse HEAD` = `c5f021c4d38698860f56b03498adc261dbb8d8dd` — matches the mandated pin exactly.
- **Method:** clone read-only; all runs in a fresh `/tmp` copy (`/tmp/edge-test-E`) of `native/ps2servers-edge`, Go 1.27.4 (`~/workspace/tools/go1.27.4/bin`). Mutations for RE-SERV-089/090/091 were applied only in the /tmp copy and restored from backups afterwards.
- **Repo house rule noted:** the clone's `AGENTS.md` (GitHub attribution policy) was read; it imposes no constraint on this read-only verification and no GitHub content was produced.

---

### BASELINE-GO — VERIFIED

- **Citations:** Baseline text (Redo-PS2Servers.md lines 14–97): build/vet clean; 246 top-level = 145 pass / 99 fail / 2 skip; 298 incl. subtests = 177/119/2; per-package top-level failures smb 4, udpbd 10, udpfs 84, webui 1; 89 outputs containing `sendto: operation not permitted`, 22 containing `i/o timeout`; 250 `func Test` definitions, 5 behind `//go:build cgo && chd`; packages ok 6 / FAIL 4 / no-test-files 3; skips = `TestEntryRefusesNamesWindowsWouldReinterpret`, `TestSharedHandshakeFixtures`.
- **Execution (mine, reproduced exactly):**
  - `go build ./...` exit 0, no output. `go vet ./...` exit 0, no output. `go test -json ./...` exit 1.
  - **My counts are identical in every particular:** 246 top-level results = **145 pass / 99 fail / 2 skip**; 298 incl. subtests = **177 pass / 119 fail / 2 skip**; per-package top-level failures **udpfs 84, udpbd 10, smb 4, webui 1**; packages ok 6 (`cmd/ps2servers-edge`, `compression`, `filesystem`, `memory`, `protocol`, `sessionstats`), FAIL 4 (`smb`, `udpbd`, `udpfs`, `webui`), no test files 3 (`logging`, `session`, `statuslistener`); the 2 skips are exactly the two named tests.
  - **Failure classification — every one of the 99 traced by me:** global output occurrences `sendto: operation not permitted` = **89**, `i/o timeout` = **22** (exact match to the claimed message counts). At top-level-test granularity: 70 failing tests carry `operation not permitted` in their own output, 21 carry `i/o timeout`; the remaining 8 resolve as follows — 7 (`TestBlockWriteOnAFileHandleSurvivesALoaderHandoff`, `TestReplacementLoaderCanRewriteAFileTheOldOneHadOpen`, `TestReplacementLoaderCanDeleteAFileTheOldOneHadOpen`, `TestBackgroundDiscoveryPreservesActiveStream`, `TestSendPacketsFallbackAndMetrics`, `TestBatchedTransferDoesNotPublishQueuedPacketsBeforeFlush`, `TestARefusedWriteTakesNoChunksAfterAnUnfinishedOne`) fail only in subtests whose output is `write udp4 …: sendto: operation not permitted` (one subtest of `TestSendPacketsFallbackAndMetrics` instead times out on read), and the 8th, webui `TestStatusIdentifiesTheServiceByFlags` (`webui_test.go:447`), is the one-step-removed case the baseline describes: I confirmed the fake server replies via `conn.WriteToUDP` (webui_test.go:440) and the code under test queries over UDP (`queryAllStatus`, service.go:276), so with `sendto` blocked no reply can arrive. **Genuine (non-environmental) failures found by me: 0.**
  - **Source reconciliation:** my grep counts **250** `func Test` definitions. The 5 gated behind `//go:build cgo && chd` are exactly 4 in `internal/compression/chd_enabled_test.go` + 1 in `internal/udpfs/chd_operations_test.go` (headers read directly). Note the complementary file `chd_disabled_test.go` (`//go:build !cgo || !chd`, 1 test) *is* compiled by default, which is consistent with the baseline arithmetic: 250 − 5 gated = 245 compiled + the `FuzzParseDataPacket` seed-corpus run = 246 top-level results. CI context also confirmed: `edge.yml` test job runs `go test -race ./... -count=2`, `go vet`, gofmt gate, and the CHD step `CGO_ENABLED=1 go test -tags chd ./internal/compression ./internal/udpfs` — so the 5 gated tests do run in CI.
- **Caveat (shared with the baseline, not a drift):** an early parse of my JSON while the run was still writing gave a truncated 219-result picture; the completed file (1,357 lines) gives the full counts above. The baseline's own warning stands and is corroborated: UDP wire behaviour is *unverified* in this sandbox, not verified good.

---

### RE-SERV-088 — VERIFIED

- **Citations:** `internal/smb/integration_test.go:65-69` (`tryTrans2`), `internal/smb/limits_test.go:63-93` (`TestSearchesAreBounded`) — both exist exactly as cited.
- **Mechanism:** Re-derived. `tryTrans2` body is `p, d := cl.trans2(sub, tParams); return p, d, nil` — error hard-wired to `nil`. Inner `trans2` (integration_test.go:156) checks `h.Status != StatusSuccess` and Fatalfs. Grep: `tryTrans2` has exactly two callers, `limits_test.go:84` and `:89`, both checking the unreachable `err != nil`. The test loops `MaxSearches*8` FIND_FIRST2 (`MaxSearches = 16`, wire.go:138, so 128 requests) and afterwards asserts only that the server still answers; production eviction is `evictOldest(…, MaxSearches, …)` at handlers.go:729, which is silent — a non-evicting implementation passes identically. The bound is genuinely unasserted.
- **Execution:** `TestSearchesAreBounded` **passed** in my full run (TCP/in-process, unaffected by the UDP block) — consistent with the finding: it passes while asserting nothing about the bound.
- **Fix:** Sound and LESS-NOT-MORE compliant: deleting `tryTrans2` removes dead scaffolding (the inner Fatal already fails the test); the proposed FIND_NEXT2-on-evicted-ID assertion is a real behavioural bound check using existing protocol surface, no production addition required (the alternative same-package count inspection is also test-only).

### RE-SERV-089 — VERIFIED

- **Citations:** `internal/webui/webui_test.go:638-669` (`TestServerPortFallback`); production `internal/webui/webui.go:237-257` — both exist as cited; schema doc at `internal/webui/schema.go:169` confirmed verbatim ("0 uses 8082 with fallback to an OS-assigned port"), `DefaultPort = 8082` (webui.go:20).
- **Mechanism:** The test constructs `Port: 0`, sleeps 100 ms, cancels, asserts only `Serve` returned nil. It never occupies 8082, never reads the bound address, never asserts a port. The fallback branch cannot be entered and a wrong-port bind cannot fail it.
- **Execution (mutation reproduced by me):** in the /tmp copy I replaced the default-port attempt at webui.go:245 with a direct bind to `"0"` (deleting the 8082 attempt entirely); `go test ./internal/webui/ -run TestServerPortFallback -count=1` still **passed** (`ok … 0.189s`). Unmutated test also passed in my full baseline run. File restored after.
- **Fix:** Sound, test-only, small: occupy 8082 first and assert reachability on a different port, plus assert 8082 in the unoccupied case. No production change; LESS-NOT-MORE compliant.

### RE-SERV-090 — VERIFIED

- **Citations:** `internal/udpfs/blockdev_test.go:256-276` (`TestCompressionCacheDefaultsAndDisables`); behaviour at `internal/udpfs/blockdev.go:398-402` — both exist as cited.
- **Mechanism:** The test only asserts `New` stored `cfg.CompressionCache` verbatim (0→0, 8→8) and never opens an image. The actual defaulting is in `openImage`: `cache := s.cfg.CompressionCache; if cache == 0 { cache = compression.DefaultCacheBlocks }` — a function the test never calls. No disable path/value is exercised (the only "disable" in production is the separate `NoCompression` flag, untouched here).
- **Execution (mutation reproduced by me):** in the /tmp copy I changed the substitution to `cache = 1`; `go test ./internal/udpfs/ -run 'TestCompressionCacheDefaultsAndDisables|TestNoCompressionStopsAdvertisingContainersAsIso' -count=1` still **passed** (`ok … 0.027s`). Unmutated test also passed in my full run. File restored after.
- **Fix:** Sound: drive `openImage` (same package, directly callable) with cache 0 and assert the opened image's limit — test-only, no new production surface. Renaming/dropping "Disables" is pure deletion of a false claim.

### RE-SERV-091 — VERIFIED

- **Citations:** `internal/compression/chd_cuebin_test.go:119-126`; production `internal/compression/chd_cuebin.go:378-388` — both exist as cited.
- **Mechanism:** The test asserts exactly two rejections (invalid UTF-8 `0xff…bin`; 252×`x` + `.bin` = 256 bytes) and no acceptance. Production `safeCueName` additionally rejects `"`, `\`, control chars `< 0x20` and `0x7f` (chd_cuebin.go, loop after the UTF-8/length check) — none tested. A reject-everything stub therefore passes, as claimed. The name quibble is also accurate: the overlong case is ASCII, not a UTF-8 case.
- **Execution (mutation reproduced by me):** in the /tmp copy I stubbed `safeCueName` to `return false` unconditionally; `go test ./internal/compression/ -run TestSafeCueNameRejectsInvalidAndOverlongUTF8 -count=1` still **passed** (`ok … 0.010s`). File restored after.
- **Fix:** Sound and minimal: add positive cases (ordinary name, 255-byte boundary, valid non-ASCII) and one case per rejection rule. Pure test additions to an existing test, each justified by an existing production rule.

### RE-SERV-092 — VERIFIED

- **Citations:** `internal/filesystem/root_test.go:216-235`; CI at `.github/workflows/edge.yml:37` — both exist as cited.
- **Mechanism:** The test opens with `if runtime.GOOS != "windows" { t.Skip("Windows path normalisation") }`. I checked **all** workflows, not just edge.yml: the only workflow that runs this Go module's tests is `edge.yml`, whose jobs are all `ubuntu-latest` (lines 37, 191, 239; `edge-build.yml:23` also ubuntu and builds without testing). Windows runners exist elsewhere (`ci.yml`, `release*.yml`) but run Python unittest suites / packaging, never `go test` for `native/ps2servers-edge` — so the finding's precise wording ("every CI job that runs this module's tests is Linux") holds. The guarded behaviour (trailing dot/space reinterpretation, `"..."` → parent/root, expecting `ErrEscape`) is security-relevant and gets zero automated execution.
- **Execution:** The test was one of exactly 2 skips in my reproduced baseline run.
- **Fix:** Sound; the preferred half (extract a platform-independent predicate, test it anywhere) is less code than a Windows CI job and LESS-NOT-MORE compliant. The Windows-runner alternative is heavier and the finding itself ranks it second.

### RE-SERV-093 — VERIFIED

- **Citations:** `internal/smb/wire_test.go:157-179`, `:211-220`; `internal/smb/limits_test.go:157-171`; constants `wire.go:84` (`DefaultPort = 1111`), `wire.go:105,118` (`MaxMessage = 128<<10`, `MaxConnections = 16`), `server.go:120` (`idleTimeout = 5 * time.Minute`) — all exist exactly as cited (I read each test body and each constant).
- **Mechanism:** Re-derived per test: (1) `TestDefaultPortIsUnprivileged` asserts only `DefaultPort >= 1024`, binds nothing. (2) `TestMaxMessageClearsTheLargestRealFrame` asserts only arithmetic: `MaxMessage > 65594` and `MaxMessage*MaxConnections <= 4<<20`; sends nothing. (3) `TestConnectionSlotsAreNotHeldByIdleSockets` asserts only `0 < idleTimeout <= 30m` and its own comment concedes it "asserts the mechanism exists rather than waiting for it"; opens no connection (the real deadline is set at server.go:569, unexercised). No behavioural regression in bind/dispatch/accept paths can fail any of the three.
- **Execution:** All three **passed** in my full run — consistent with constant pins passing while the constants are in range.
- **Fix:** Mostly sound. Renaming to `…Constant…` is pure honesty at zero cost. The behavioural additions are justified for port-bind and large-frame round-trip; the idle-socket case is the expensive half — `idleTimeout` is a production `const`, so observing a drop requires making it test-configurable (a small production change). That half should be scoped as such, not presented as test-only; it does not defeat the finding.

### RE-SERV-094 — VERIFIED

- **Citations:** `internal/udpfs/write_test.go:143-148` — exists exactly: `func result(payload []byte) int32 { if len(payload) < 8 { return 0 }; return int32(binary.LittleEndian.Uint32(payload[4:8])) }`, and 0 is the protocol success value.
- **Mechanism:** Grep confirms `result(` is used across the package's test files (I found it in 13 `*_test.go` files including all nine the finding names: write, blockdev, blockonly, fileid_bwrite, handoff_write, pathops, rediscovery, compression_operations, chd_operations). Any truncated (<8-byte) reply therefore reads as success for every `result(...) == 0` expectation. The claimed exception checks out: `pathOp` (pathops_test.go:28-35) does `if len(reply) < 8 || … != ResultReply { Fatalf }` before calling `result` — most callers do not.
- **Execution:** Helper and call sites read directly; the masking is a code-read certainty (no run can distinguish it in the UDP-blocked sandbox, where these wire tests fail at send anyway — in my run the udpfs wire tests fail on `sendto`, before any `result` decoding).
- **Fix:** Sound and minimal: thread `t` (every caller already has one, directly or via the test client's `c.t`) and `t.Fatalf` on short payloads, or return an impossible sentinel. Test-only change, no production code.

### RE-SERV-095 — VERIFIED

- **Citations:** `internal/udpfs/status_test.go:150-173` (`TestUnknownServiceIDIsStillIgnored`); neighbour at `:109-148` — both exist as cited.
- **Mechanism:** The test sends one DISCOVERY with `ServiceID: 0x1234` and asserts only that no reply arrives within 500 ms. No positive exchange against the same server instance, so server-side ignoring is indistinguishable from transport death, wedging, or broken discovery handling. The neighbour `TestOrdinaryDiscoveryStillWorksAlongsideStatus` does prove liveness but against a different `startServerAt` instance in a different test, exactly as claimed.
- **Execution:** In my full baseline run — with every UDP `sendto` blocked — `TestUnknownServiceIDIsStillIgnored` **passed**, while the positive-control neighbour `TestOrdinaryDiscoveryStillWorksAlongsideStatus` **failed**. That asymmetry is direct executed proof of the vacuity: the negative-only test is satisfied by a dead transport.
- **Fix:** Sound and small: positive DISCOVERY/INFORM exchange on the same server/socket first, then the unknown-ID silence assertion. (Consequence to note: after the fix this test will *fail* in UDP-blocked sandboxes like this one — that is the point of a positive control, and CI has working UDP.)

### RE-SERV-096 — VERIFIED

- **Citations:** `internal/udpfs/observability_test.go:22-40` (`synchronizedLogBuffer`) vs `internal/udpfs/session_summary_test.go:22-38` (`lockedBuffer`); copies at `internal/smb/session_summary_test.go:17-33`, `internal/udpbd/session_summary_test.go:18-34`; CSO builders at `internal/compression/image_test.go:13-33`, `sizeof_test.go:16-47`, `corrupt_index_test.go:22-40`, `internal/udpfs/compression_operations_test.go:16-36` — all exist as cited.
- **Mechanism:** I compared the bodies directly. The two udpfs types are identical in substance: `mu sync.Mutex; b bytes.Buffer` with mutex-guarded `Write`/`String` delegating to the buffer — differing only in type/receiver names and one comment. The smb and udpbd `lockedBuffer` copies are the same type again. For the container builders: all four hand-assemble the same 24-byte CISO header with the same field offsets (magic `[0:4]`, header size `[4:8]`, total size `[8:16]`, block size `[16:20]`, version/align bytes 20/21). Minor nuance I verified: `writeContainer` and `writeUDPFSContainer` are near-identical full header+index builders; `writeCorruptIndex` varies the index gap as its purpose requires; `writeBigIndexCSO` assembles the header but simulates the index by truncation rather than writing index entries. So "header hand-assembled four times" is exact; "index layout in four" is exact for three and by-simulation for the fourth. This does not change the duplication substance.
- **Execution:** Read/diff only (duplication is a source fact); all four packages compile and run in my baseline.
- **Fix:** Sound, with the finding's own prioritisation correct: the same-package udpfs dedupe (delete `synchronizedLogBuffer`, keep `lockedBuffer`) is a pure deletion and the valuable half. Cross-package copies cannot be shared without an exported test-support package — the finding concedes this is the weaker half, correctly. Parameterising one container builder per package is a net test-code reduction; `writeBigIndexCSO`'s truncation trick needs to survive as a parameter/variant, which the finding's parameter list (declared size, index gap, magic) mostly covers.

### RE-SERV-097 — VERIFIED

- **Citations:** `internal/protocol/packet_test.go:23-30` (`FuzzParseDataPacket`, 3 seed inputs: empty, 6-byte, one marshalled Data packet); `internal/udpfs/udpwrite_test.go:338-377` (`BenchmarkUDPBatch`) — both exist exactly as cited and are the only `func Fuzz` / `func Benchmark` in the module (my grep found no others).
- **Mechanism:** `go test` without `-fuzz` runs a fuzz target only over its seed corpus as a unit test, and never runs benchmarks. I grepped **all** workflows (not only edge.yml) plus looked for Makefiles: no `-fuzz`, `-bench`, `bench`, or `fuzz` invocation anywhere; `edge.yml`'s Go invocations are only the `-race -count=2` test run and the CHD-tagged run (lines 56, 82).
- **Execution:** In my `go test -json` run, `FuzzParseDataPacket` appears as an ordinary **passing** top-level test (it is the +1 in the 245+1=246 reconciliation) and there is no benchmark result of any kind — direct executed confirmation.
- **Fix:** Sound under the finding's own justification rule: either the bounded CI step (~60 s fuzz + `-benchtime=1x` smoke) — an addition, but bounded and the only way either artefact yields its intended signal — or delete the benchmark (pure LESS). Both branches are honestly presented; no production code involved.

### RE-SERV-098 — VERIFIED

- **Citations:** `internal/udpfs/blockdev_test.go:278` — exists verbatim: `var _ = session.Pending // keep the session import meaningful across refactors`.
- **Mechanism:** My grep of the file for `session.` returns exactly one hit — line 278 itself. Nothing else in the file uses the `session` package; the line exists solely to keep the import compiling, as its own comment admits.
- **Execution:** Grep result quoted above (single hit); the file compiles in my baseline build/test run with the line present.
- **Fix:** Ideal LESS-NOT-MORE: delete the line and the import. Pure deletion, zero risk beyond compilation, which the compiler itself verifies.

### RE-SERV-099 — VERIFIED

- **Citations:** `internal/udpbd/udpbd_test.go:50-65` (`TestBlockGeometryIsConsistent`); production `internal/udpbd/protocol.go:101-104` — both exist as cited: `blockSize = 1 << (shift + 2); return blockSize, rdmaMaxPayload / blockSize, SectorSize / blockSize`.
- **Mechanism:** Re-derived assertion by assertion over the test's shifts {3,5,6,7} (block sizes 32/128/256/512): (a) `size == 1<<(shift+2)` restates the first production line verbatim; (b) `perSector*size == SectorSize` holds for every shift in the set because each block size divides 512 — it is a property of the chosen inputs, not an independent expectation; (c) `perPacket*size <= rdmaMaxPayload` is identically true for any integer-division quotient `rdmaMaxPayload / size` (absent overflow, impossible at these magnitudes). No value external to the implementation appears anywhere in the test. The contrast also checks out: the neighbouring `TestBlockShiftMatchesReferenceTable` pins literal expected shifts against the reference implementation — a real external oracle in the same file. The finding's own Med/INFERRED confidence label is honest: the arithmetic identities are certain; "no external oracle" is a judgement I independently reach from the same two files.
- **Execution:** `TestBlockGeometryIsConsistent` **passed** in my full run (in-process, UDP-independent) — as it must, given (a)–(c).
- **Fix:** Sound and minimal: replace the derived assertions with a literal `(blockSize, blocksPerPacket, blocksPerSector)` table sourced from the reference geometry, mirroring the existing neighbour test. Test-only, no size growth of consequence.

### RE-SERV-100 — VERIFIED

- **Citations:** `internal/udpfs/server_test.go:15-49` (`TestClassify` at 11-21, `TestSharedHandshakeFixtures` at 23-49) — exists as cited; fixture at `conformance/fixtures/handshake_cases.json` in the clone, read directly by me.
- **Mechanism:** Both halves re-derived. (1) Duplication: the fixture's five cases — standard-zero 0/0→standard, modulo-fresh 0/1→modulo, modulo-resumed 7/8→modulo, modulo-wraparound 4095/0→modulo, nonmatching-fallback 7/0→standard — are exactly `TestClassify`'s hardcoded table `{{0,0,Standard},{0,1,Modulo},{7,8,Modulo},{4095,0,Modulo},{7,0,Standard}}`, case for case including profiles. As a Go test it adds no case; its value is only cross-implementation sharing of the file, as claimed. (2) Skip-to-pass: on `os.IsNotExist` it calls `t.Skipf`, and the path `../../../../conformance/…` escapes the Go module, so any module-only copy silently loses it with a green run.
- **Execution:** In my mandated /tmp module-only copy the test **skipped** (1 of the 2 baseline skips) while the fixture verifiably exists in the full clone — the exact evaporation the finding describes, reproduced.
- **Fix:** Sound: fail (not skip) when the fixture is missing in a full checkout, gated by an explicit standalone-copy marker, and log the executed case count. The env-marker condition is a small addition but is what keeps downstream module consumers working; the alternative (unconditional fail) would break them, so the scoped version is the right one.

---

## Tally

- **BASELINE-GO: VERIFIED** — every claimed number reproduced exactly (build/vet clean; 246 = 145/99/2; 298 = 177/119/2; per-package failures 84/10/4/1; 89 `sendto` + 22 `i/o timeout` output occurrences; 250 definitions, 5 CHD-gated; 0 genuine failures).
- **RE-SERV-088…100: 13 VERIFIED, 0 VERIFIED-DRIFT, 0 WEAKENED, 0 REFUTED, 0 BLOCKED-ENV.**
- Mutation proofs independently reproduced for 089, 090, 091 (mutated code in /tmp copy, named tests still passed, files restored). Vacuity of 095 proven from my own run (negative-only test passed under total UDP block while its positive-control neighbour failed). All other findings re-derived from direct reads/greps quoted above.

## CANDIDATES (new defects)

- **None.** No genuinely-failing test was found: all 99 failures in my run trace to the sandbox UDP `sendto` block or its downstream `i/o timeout` (classification in the BASELINE-GO block, including the 7 subtest-level and 1 webui one-step-removed cases). No test, helper, or production defect beyond RE-SERV-088…100 was identified in this batch's scope. The only nuance worth recording is inside RE-SERV-096 (documented there): `writeBigIndexCSO` simulates its index by file truncation rather than writing index entries, so "index layout hand-assembled four times" is exact for three builders and by-simulation for the fourth — a wording nuance, not a new defect and not a defeat of the finding.

---

## Part F — RE-SERV-101–126 (Python test suite) + Python baseline


- **Pin confirmation:** `git -C ~/workspace/audit-clones-2026-11-13/PS2-Servers rev-parse HEAD` → `c5f021c4d38698860f56b03498adc261dbb8d8dd` — matches the required pin exactly.
- **Clone hygiene:** clone used read-only for all reading/grep; `git status --porcelain` clean at the end, no `attribution-*` tags present. All execution ran in a `.git`-less copy at `/tmp/ps2sF` (mirroring the original baseline's `/tmp/ps2s`, `.git` excluded).
- **Environment:** Python 3.12.3, sandbox blocks UDP `sendto` (`PermissionError: [Errno 1] Operation not permitted`) — UDP failures classified, not verdicted as code defects.
- **Method:** refute-first — every citation grep/read-checked in the clone, counts re-derived by AST/script, direct-execution claims actually executed, full baseline re-run.

---

## BASELINE-PY — VERIFIED-DRIFT

- **Claimed:** `cd /tmp/ps2s && timeout 1200 python3 -m unittest discover -s tests -v` → 1,544 run = 1,388 pass / 3 fail / 73 errors / 80 skip; 76 events from 54 unique tests; classification 52 UDP-blocked / 1 copy-environment (attribution, needs `.git`) / 1 genuine (`test_nfs4_server…test_endless_empty_fragments_close_the_connection`, TCP `recv` timeout).
- **My reproduction (identical invocation, /tmp/ps2sF):** `Ran 1544 tests in 223.583s — FAILED (failures=3, errors=72, skipped=80)`, exit 1. So: **1,544 run / 1,389 pass / 3 fail / 72 errors / 80 skip**; **75 failure/error events from 53 unique tests**.
- **Classification of my 53 unique failing tests — identical to the claim except for the one missing test:**
  - **UDP-blocked: 52** — 48 direct `PermissionError` at UDP `sendto` (`test_ra_owned_service` 18, `test_udpfs_write_needs_writable_handle` 19, `test_router_status` 5, `test_udpfs_handoff_retried_write` 3, `test_ra_resolver_process` 2, `test_session_summary` 1; the 22 secondary teardown/`stop()` events claimed are 22 in mine too: 19 + 3 across the two UDPFS handoff/write files, each test reporting twice) + 4 downstream `test_status_client` (3 FAIL + `KeyError: 'state'` ERROR; its own message "the query never arrived", `tests/test_status_client.py:103`, reproduced verbatim in my log).
  - **Copy-environment: 1** — `test_attribution_policy…test_tag_with_no_new_commits_does_not_fail_validation`, ERROR at `tests/test_attribution_policy.py:22`, `git tag -a attribution-empty-range-<pid> … HEAD` exit 128 in the `.git`-less copy. Reproduced exactly.
  - **Genuine: 0 in my run.**
- **The claimed genuine failure did NOT reproduce:** `test_nfs4_server.SocketTests.test_endless_empty_fragments_close_the_connection` was `ok` in my full discover run, and passed **5/5 isolated re-runs** (2.5–5.1 s each). It is therefore **not established as a deterministic genuine failure**. It is, however, genuinely non-UDP (pure TCP loopback, `socket.create_connection(..., timeout=5)`, `tests/test_nfs4_server.py:889-893`) and structurally timing-fragile — see CANDIDATE F-2: the server must consume `MAX_RECORD//4 + 1` = 264,193 empty fragments one `recv` at a time before it may close, racing a 5 s socket timeout, so a timeout under full-suite load (the original run) vs. a pass unloaded (mine) is the expected flake signature. Correct classification: **intermittent/timing, root cause plausibly load-dependent; "genuine single failure" overstates what one observation proves.**
- **Drift summary:** every total is off by exactly that one test (errors 73→72, passed 1,388→1,389, events 76→75, unique 54→53). Run count, failures, and skips match exactly; the UDP/copy-environment classification matches test-for-test.

---

### RE-SERV-101 — VERIFIED
- **Citations:** `tests/test_attribution_policy.py:22-40` read in full: checks only `shutil.which("bash")` (ll.14-15, skip), then `git tag -a attribution-empty-range-{os.getpid()} HEAD` with `cwd=ROOT` (l.22), extracts the workflow script, runs it, deletes the tag in `finally` (ll.36-40). Workflow `.github/workflows/attribution-policy.yml:54-64` takes `BEFORE`/`AFTER` from env — the fix's premise holds.
- **Mechanism:** Exactly as claimed: mutates the real checkout, no usable-checkout guard, stray-tag risk on hard kill, cannot run read-only.
- **Execution:** Errored in my baseline for exactly the claimed reason (git exit 128 at line 22 in the `.git`-less copy). Clone itself untouched (no tags, clean status) because discover was never run against it.
- **Fix:** Throwaway temp repo + skip on `git rev-parse HEAD` failure — sound, LESS-NOT-MORE compliant.

### RE-SERV-102 — WEAKENED
- **Citations / census (verified):** My AST/script census over the 36 in-scope (a–n) files confirms exactly the 8 named files lack `unittest.main()` (`test_achievement_loader`, `test_achievement_viewer`, `test_caduceus_reference_wire`, `test_caduceus_setup`, `test_desktop_instance`, `test_discord_presence`, `test_game_library`, `test_library_panel`) and the other 28 end with the `__main__` guard. (Suite-wide there are 31 such files among 120 `test_*.py`, but the finding's scope is the 36, and within that scope it is exact.)
- **Mechanism / Execution (the stated impact is wrong as written):** The claim "`python tests/test_game_library.py` (etc.) exits 0 having run nothing — no error, no output" is **false for the cited invocation**. Executed in /tmp/ps2sF, **all 8 files exit 1** with `ModuleNotFoundError: No module named 'launcher'` (none inserts the repo root on `sys.path`, and Python puts `tests/` — not the root — on the path for a script run). The silent-exit-0 behaviour occurs only with the root already importable: `PYTHONPATH=. python3 tests/test_game_library.py` → exit 0, no output (verified), as does any environment where the package is otherwise importable. Module invocation works (`python -m unittest tests.test_game_library` → Ran 17, OK).
- **True scope:** a real consistency defect (8 files cannot be run directly in any mode that reaches the test code, unlike the 28), but the headline failure mode — a masquerading green pass — requires `PYTHONPATH`/installed-root conditions the finding does not state; the plain direct run is a loud import crash, not a silent pass.
- **Fix sanity:** Adding the two-line guard alone does **not** make `python tests/<file>.py` work for these 8 — the import crash precedes it (siblings *with* the guard, e.g. `tests/test_art_fetch.py`, also crash direct from the root for the same reason; see CANDIDATE F-1). The fix also needs the `sys.path` prologue several sibling files carry, or the finding should be re-scoped to the `PYTHONPATH=.` case.

### RE-SERV-103 — VERIFIED
- **Citations:** `_read` at `test_edge_manual.py:26-28` and `test_edge_service_modes.py:26-28` — source text identical. `_flags_of` at `test_edge_manual.py:71-80` / `test_edge_service_modes.py:208-218` — my script: **AST dumps equal** (only comments differ, as claimed). Third slicer family present: `_section` (manual:81, flag_exposure:102), `_section_options` (modes:223), `_udpfs_flags` (flag_exposure:85).
- **Mechanism:** Duplicated Go-flag parsers are the load-bearing extraction for the Edge guards; silent-degradation mode (`_flags_of` returns `set()` when the func is not found) confirmed in source.
- **Execution:** AST census run by me (above); baseline: all Edge tests passed.
- **Fix:** Hoist to a shared tests helper — sound, deletion-positive.

### RE-SERV-104 — VERIFIED
- **Citations:** `iso_fixture` defined `tests/test_disc_identity.py:34-59` (accepts `boot=`/`elf=`/`cnf=`/`nested=` etc.). Consumers grep-verified: `tests/library_fixtures.py:31` (`from tests.test_disc_identity import iso_fixture`), `test_library_identity.py:14`, `test_library_index.py:13/14`, `test_game_library.py:41` and `:74` (two separate in-method imports).
- **Mechanism:** `test_game_library.py:266-299` (`test_native_ps2_hash_matches_independent_boot_name_and_elf_digest`) hand-rolls a second ISO9660 builder (`record()` + manual PVD/directory assembly) while varying only boot name and ELF bytes — both parameters of the shared builder. Read in full; claim exact.
- **Execution:** Baseline: all involved tests passed/skipped on stated gates.
- **Fix:** Move fixture to `library_fixtures.py`/fixtures module, re-export, rewrite the hash test to call it — sound.

### RE-SERV-105 — VERIFIED
- **Citations:** `tests/test_library_preferences.py:17-33, 40-58, 60-68` read in full. Grep: `save_preferences`/`show_cover` occur only as AST search strings (ll.21, 46) and as calls on the exec'd copies (ll.33, 58) — never on a production object; `LibraryWindow` is never instantiated/imported.
- **Mechanism:** Stronger than claimed for the second test: `show_cover` is exec'd in a context that is literally `{}` (l.51-53), `save_preferences` in a namespace containing only a fake `config`. Any new module-global reference in either method → harness `NameError`, exactly as claimed. Third test (ll.60-68) is `assertIn` substring pins over source text.
- **Execution:** Baseline: file's 3 tests passed.
- **Fix:** Call real unbound methods with a fake `self` (pattern exists in `test_directlink.py`) — sound.

### RE-SERV-106 — VERIFIED
- **Citations:** `tests/test_netinfo_and_smb.py:20-22` asserts only `"[network] Current IP Addresses and Network Adapters:"` and `"Custom IP..."`. Both are formatter literals: header at `launcher/netinfo.py:206`, the Custom IP sentence at `launcher/netinfo.py:215` (grep-verified). Nothing about any detected address is asserted.
- **Mechanism:** Can only fail on a literal edit; host network data is silently unexamined. Duplication of intent with `test_ip_choices_contains_custom_option` (ll.15-17) confirmed.
- **Execution:** Both tests passed in my targeted re-run.
- **Fix sanity (one correction):** The proposed "patch `netinfo.all_ipv4`" is not quite the formatter's input — `detailed_ip_info()` calls `get_all_adapters_info()`, which only *falls back* to `all_ipv4()` (netinfo.py:202) when its adapter probes yield nothing. Patch `get_all_adapters_info` (or both) for a deterministic test. Finding stands; fix target needs that adjustment.

### RE-SERV-107 — VERIFIED
- **Citations:** `tests/test_http_path_aware.py:208-218` read: for `"../DVD/Game.iso"` the test asserts `assertIsNotNone(entry)` (folded by `normpath` back inside the whitelist), with the honest inline comment at ll.213-214; the other three names assert `IsNone`. The name `test_traversal_and_unknown_paths_resolve_to_nothing` contradicts the first case exactly as claimed.
- **Mechanism / Fix:** Name-only defect; split + rename, no assertion changes — sound, zero-risk.

### RE-SERV-108 — VERIFIED
- **Citations:** `tests/test_game_library.py:185-198`: `try: linked.hardlink_to(...) except OSError: return` (ll.191-194) — bare return, no skip, after the live-database assertion. Sibling form confirmed: `test_library_index.py:120-121` and `test_library_recursive_scan.py:156` use `except OSError: self.skipTest(...)` for symlinks.
- **Mechanism / Execution:** Vacuous-pass hole is portability-only; in my baseline (Linux, hardlinks work) the full test ran and passed.
- **Fix:** `skipTest` (or split the test) — sound.

### RE-SERV-109 — VERIFIED
- **Citations / census (recounted):** `test_autostart_and_firewall_options.py`: **9 × `SkipTest("no display")`, 10 × `tk.Tk()`** — script counts match the claim exactly; prologue repetitions at ll.46-64, 84, 106, 133, 205, 228, 258, 282, 306 region, with the drift claimed (two tests probe via `tk.Tk().destroy()` at ll.198/219 before `_app_with_fake_servers` at l.162, others construct directly). `test_library_panel.py`: **7 × `tk.Tk()`** with the same try/`TclError`/skip shape (ll.18, 43, 91, 135, 171, 199, 237). `test_about_page.py:106-152` carries the same shape once, plus env save/restore in its `setUp` — as cited.
- **Mechanism:** Copy-pasted display guard + full `LauncherApp` construction per test; drift already present.
- **Execution:** In my baseline all 9 autostart tests skipped `no display`, library-panel tests skipped `No graphical display` — the prologue is the gate in every case.
- **Fix:** One `_require_display()`/app factory (class-level pattern already in `test_gui_layout.py`) — sound.

### RE-SERV-110 — VERIFIED
- **Citations:** `tests/test_smb2_server.py:616-617` main block mid-file (file 944 lines); classes after it at 620/697/736/758/808/870/906/915. My AST recount: **23 tests after the block** (MalformedRequestBuffers 4, RenameSafety 3, ParentEntry 1, DomainHandling 3, SecurityBufferFraming 5, NeutrinoClientShape 3, ShareNameCollisions 1, BindHint 3), **56 total**. Parallel: `tests/test_smb2_paths.py:202-203` main block, `WindowsDeviceNames` at 206 with **5 tests**, **19 total** (file 249 lines).
- **Execution (mine, /tmp copy):** `python3 tests/test_smb2_server.py` → **Ran 33 tests, OK**; `python -m unittest tests.test_smb2_server` → **Ran 56 tests, OK**. `python3 tests/test_smb2_paths.py` → **Ran 14, OK**; module → **Ran 19, OK**. Numbers identical to the claim; both direct runs green, loss invisible, exactly as claimed. (Note: these two files *do* run directly, unlike RE-SERV-102's files — they carry their own path setup.)
- **Fix:** Move the block to EOF in both files — sound, minimal.

### RE-SERV-111 — VERIFIED
- **Citations:** `tests/test_router_status.py:177-178`: `except subprocess.TimeoutExpired: self.skipTest("go run timed out")` (timeout=180 on the `go run`). Non-zero exit path directly below `self.fail(...)`s ("the two encoders were never compared"); absence skips at ll.142/145 are toolchain/source checks, as the finding exempts.
- **Mechanism:** A hung Go encoder is reported as an environment skip in the one byte-parity test — re-derived from source.
- **Execution:** In my baseline this test skipped `no Go toolchain on PATH` (legitimate path), so the timeout branch was not exercised — mechanism is READ-verified, consistent with the finding's own confidence basis.
- **Fix:** Fail on timeout — sound.

### RE-SERV-112 — VERIFIED
- **Citations:** `tests/test_smb2_banner.py:125-131`: every assertion is inside `for ip in smb2_server._local_ipv4_addresses():`; no non-emptiness guard anywhere in the test.
- **Mechanism / Execution:** On *this* host the test is non-vacuous — I executed `_local_ipv4_addresses()` → `['198.19.0.2']`, and the test passed in isolation — which confirms the claim's conditional form: vacuity strikes exactly when the function returns `[]` (regression or restricted host), indistinguishable from success.
- **Fix:** Capture + `assertTrue(list)` first — one assertion, sound.

### RE-SERV-113 — VERIFIED
- **Citations:** `tests/test_systemd_units.py:119` (`test_template_instance_keys_are_real_servers`, loop over `re.findall(r"ps2servers@([a-z0-9]+)", readme)` — note `_read` resolves inside `packaging/systemd/`, so the README is the systemd one) and `:205` (`test_readwritepaths_tolerates_a_missing_directory`, loop over `_directives(...).get("ReadWritePaths", [])`). Neither asserts non-emptiness.
- **Mechanism / Execution:** Both loops are currently non-empty — my recount: README regex yields 8 matches / 3 unique keys (`smbv1`, `udpbd`, `udpfs`); the unit carries one real entry `ReadWritePaths=-/srv/ps2`; both tests passed in isolation. So the vacuity is structural (parse drift → zero iterations → pass), exactly the Medium-confidence claim made, no more.
- **Fix:** Non-emptiness guard (+ pin the instance set) — sound, adds assertions only.

### RE-SERV-114 — VERIFIED
- **Citations:** `tests/test_smbv1_share_and_credentials.py:182-187`: constructs `SmbServer({"roms": Share("roms", d)}, …)`, asserts `"roms" in server.shares`. Production `smbv1_server/smbserver_opl.py:1518-1519`: `__init__` is literally `self.shares = shares` — the test reads back the constructor argument verbatim.
- **Mechanism:** No TREE_CONNECT is driven; the real name resolution (`shares.get(share_name) or .get(share_name.lower())`, smbserver_opl.py:783) is unobserved. A key-normalising regression passes this test.
- **Execution:** Test passed in my targeted re-run.
- **Fix:** Drive a real TREE_CONNECT (helpers exist in the same file) or delete — sound.

### RE-SERV-115 — VERIFIED
- **Citations:** `tests/test_setup_guide.py:36-54`: `test_gui_button_connected_once` counts `source.count('text="PS2 setup guide…"') == 1` and pins fragments; `test_first_run_invitation_and_persistent_status` pins literal lines incl. `self.root.after(1200, self._offer_ps2_setup_guide)`. `:90-92`: the close-callback test ends in three `assertIn` source pins (`self.close(completed=True)`, `command=self.close`, `WM_DELETE_WINDOW` protocol line) after an executed portion.
- **Mechanism:** Spelling pins: fail on behaviour-preserving refactors, pass on wiring breaks that keep the strings — re-derived from the test bodies.
- **Execution:** Baseline: file passed.
- **Fix:** Keep ≤1 smoke source check, move semantics to the guide state machine (other tests in the file already exercise it) — sound.

### RE-SERV-116 — VERIFIED
- **Citations:** `tests/test_status_client.py:206-272`, class `StopAllStaysUnconditional`: `ast.parse` of `launcher/gui.py`, `_method`/`_calls` walk `ast.Call` nodes collecting attribute/name strings, asserting `confirm_stop_while_busy`/`askyesno` absent from `stop_all`. Docstring discloses the headless rationale, as the finding credits.
- **Mechanism:** Syntactic only — a prompt via an unwatched helper name or `getattr` indirection passes; a rename of a watched helper fails it. The counter-example cited also checks out in substance: `test_verbose_default_and_log_stamps.py`'s `TerminalStamp` (l.58) does import `launcher.gui` and skip only on failure (ll.63-67), so "cannot be instantiated" is stronger than suite practice.
- **Execution:** Baseline: class passed.
- **Fix:** Relabel as source lint + add one behavioural test with prompt functions stubbed to raise, under the tkinter guard — sound; the addition is justified as the only behavioural observation.

### RE-SERV-117 — VERIFIED
- **Citations:** `tests/test_release_downloads.py:71-82`: body is `text = SCRIPT.read_text(...)` (`SCRIPT` = `.github/scripts/release-downloads.sh`, l.9) then `for label in (…16 labels…): self.assertIn(label, text)` — nothing else. (Finding calls the variable `script_text`; actual name is `text` — immaterial.)
- **Mechanism:** A no-op target whose label string remains in the script passes; the name `test_every_edge_target_has_a_purpose` overstates the guarantee, as claimed.
- **Fix:** Assert per-target artefact/block, or rename to what is checked — sound.

### RE-SERV-118 — VERIFIED
- **Citations:** `tests/test_ra_credentials.py:76-78`: `if os.name == "nt": return  # the platform roundtrip above exercises real DPAPI instead` — bare return at the head of `test_posix_insecure_permissions_are_rejected`, exactly as claimed, comment included.
- **Mechanism:** On Windows this reports as *passed* having run nothing; the stated mitigation (DPAPI roundtrip above it) is present in the file.
- **Execution:** On this Linux host the POSIX branch ran and passed in baseline.
- **Fix:** `skipTest(...)` — sound, one line.

### RE-SERV-119 — VERIFIED
- **Citations / census (mine):** `stalled` (`test_ra_account_worker.py:31`) vs `stuck` (`test_ra_resolver_process.py:15`): function **bodies AST-identical** (`while True: time.sleep(1)`; resolver copy adds a comment). `_request` (`test_smbv1_compressed.py:44` vs `test_smbv1_read_after_write.py:22`): **full AST dumps identical** (same md5), same `SimpleNamespace(tid=1, params, data, msg)` factory.
- **Mechanism:** The finding's negative census also reproduces: my AST hash over all `test_*.py` found **zero duplicated test-method bodies** — duplication is helper-layer only, as claimed.
- **Fix:** Hoist each pair to shared test support — sound.

### RE-SERV-120 — VERIFIED
- **Citations:** `tests/test_transfer_limits.py:109` `read_payload` and `:118` `write_req_payload`: my AST comparison — **bodies identical**, both `return struct.pack("<BBBBiI", 0, 0, 0, 0, handle, size)`. The neighbouring `bread_payload` (l.113) is genuinely different, so the pair is not a pattern artefact.
- **Fix:** Delete one name, call the other — sound, pure deletion.

### RE-SERV-121 — VERIFIED
- **Citations:** `tests/test_udpfs_config.py:360-382`, `LauncherModeLaunchTests.test_every_mode_builds_argv_from_defaults` (def at l.367): sole assertion `assertIsInstance(server.build_argv(values), list)` at l.382 inside `subTest(mode=key)` over the whole `servers.REGISTRY`; class docstring claims "must build an argv from its own field defaults".
- **Mechanism:** `[]`/nonsense lists pass; only exceptions and non-list returns fail. The NameError-crash case the docstring motivates *is* covered — the finding concedes this and scopes the gap correctly.
- **Execution:** Passed in my targeted re-run.
- **Fix:** Assert non-empty + supplied root/target present in argv — sound, assertions only.

### RE-SERV-122 — VERIFIED
- **Citations:** `tests/test_udpfs_config.py:272-279` `test_widen_on_a_real_socket`: only assertion `assertGreater(s.getsockopt(SO_RCVBUF), 0)` at l.276. The `_FakeSock` docstring at ll.232-234 states verbatim "a real socket always reports a positive buffer, which would let a no-op implementation pass" — quoted correctly.
- **Execution (demonstration):** In /tmp I created a real UDP socket and read `SO_RCVBUF` **before any widen call: 212992 > 0** — a no-op `_widen_recv_buffer` passes this test by construction. The surrounding fake-socket tests (ll.259-270) do pin widen/no-shrink/best-effort properly, as claimed.
- **Fix:** Assert post-call ≥ requested (or vs. pre-call baseline), or delete the test — sound; the baseline-comparison variant is the safer of the two offered.

### RE-SERV-123 — VERIFIED
- **Citations:** `tests/test_smb2_take_445.py:73` `FakeServices.run` (class at l.35): regexes `_GET`/`_STOP`/`_START` at ll.25-27 match production's command shapes (`smb2_server/smb2_server.py:1511-1599` issues exactly `(Get-Service %s …).Status`, `Start-Service %s …`, `Stop-Service -Name LanmanServer …`). Fall-through `return subprocess.CompletedProcess(args, 0, "", "")` is at **line 117** (finding says 118 — one-line drift, immaterial).
- **Mechanism:** Any unrecognised/respelled command silently succeeds with exit 0 and empty output; production command drift is absorbed by the double. Re-derived from the full `run` body (ll.73-117).
- **Execution:** Baseline: file passed (all commands matched, so the fall-through was not hit — consistent with a latent, not active, defect).
- **Fix:** Raise / non-zero with the unmatched command named — sound.

### RE-SERV-124 — VERIFIED
- **Citations (all grep-verified):** manual `time.monotonic = lambda: fake_now` / restore-by-hand in `test_udpfs_live_discovery_handoff.py` — first site ll.239-242, and *passim* is accurate (10 assignment/restore pairs: ll.239, 285, 324, 362, 387, 418, 437, 474, 513, 587); `patch.object(CORE.threading, "Thread")` + `patch.object(CORE.time, "sleep")` at `test_udpfs_fallback_generation.py:47,51`; `patch.object(UDPFS.os, "name", …)` / `patch.object(UDPFS.threading, "Thread", …)` at `test_udpfs_stall_report.py:59,117,189,203`; `patch.object(srv.os, "lstat", …)` at `test_udpfs_symlink_path_ops.py:374`.
- **Mechanism:** `CORE.threading`, `UDPFS.os`, `srv.os` are the process-wide stdlib module objects (the production modules `import threading/os/time` by reference), so patching their attributes patches them for every module in the single discover process. The finding correctly self-scopes this as fragility, not an observed leak: the manual sites restore in `finally` (verified ll.236-242) and the `patch.object` uses are context managers.
- **Execution:** Baseline completed with no cross-file contamination symptom — consistent with "fragility, not observed leak".
- **Fix sanity (one correction):** The minimum fix (convert manual `time.monotonic` assignments to `mock.patch.object` contexts) is sound. But the fix text's parenthetical — "`patch.object(CORE, "time", fake)` is not possible for module attributes" — is **wrong**: rebinding `CORE.time` replaces the entry in CORE's own module globals and affects exactly the lookups made through CORE, which is precisely the contained patch the finding wants; it is the standard technique and should be recommended, not ruled out.

### RE-SERV-125 — VERIFIED
- **Citations (all grep-verified):** five `importlib.util.spec_from_file_location` loads of `udpfs_server/ps2servers_core.py`, none registering in `sys.modules` (no `sys.modules` assignment in any of the five; each does `module_from_spec` + `exec_module`):
  - `test_udpfs_fallback_generation.py:18-19` → `ps2servers_core_fallback`
  - `test_udpfs_inform_telemetry.py:13-14` → `ps2servers_core_inform_telemetry`
  - `test_udpfs_startup_diagnostics.py:16-17` → `core_startup_diagnostics`
  - `test_udpfs_live_discovery_handoff.py:23-24` → `ps2servers_core_handoff`
  - `test_udpfs_protocol_negotiation.py:14-15` → `ps2servers_core` (same *name*, still a distinct module object — not the imported one)
- **Mechanism:** Five distinct module objects → distinct `AutoUdpfsServer`/`Session` classes and module state in one discover process, while other files import the module normally (`test_server_override_contract.py:34`, `test_udpfs_ack_progress.py:15`, `test_udpfs_config.py:26`, `test_router_status.py:217`). Class-identity/`isinstance` divergence follows as claimed (INFERRED portion correctly labelled).
- **Fix:** Import normally; reserve spec-loading for a genuinely-isolated case with a comment — sound. (Caution for whoever applies it: if any of the five relies on fresh module-level state per file, that reliance should be named before converting — the finding's Medium risk already covers this.)

### RE-SERV-126 — VERIFIED-DRIFT
- **Citations:** `smb2_server/smb2_server.py:76-128` is exactly 53 lines (my count); consumer `_open_share_warning_lines` at ll.131-147 (called at l.141) appends `"  Reachable on: " + ", ".join(ips)` then the `--bind` advice line, and its docstring concedes "an empty address list is fine, the --bind advice is the load-bearing half" — quoted fairly. The `fcntl` import/ioctl at ll.96-101 is the only `fcntl` use **in that file** (other repo files use it for unrelated purposes; the finding's "in the server" is accurate read as the SMB2 server file).
- **Correction to the title/mechanism:** the function does not "run three enumeration mechanisms" per call. They are **sequential conditional fallbacks**: the ioctl pass runs only `if not ips:` (l.94) and the TEST-NET UDP-connect probe only `if not ips:` again (l.110). On a host where `getaddrinfo(gethostname())` yields a non-loopback address — including this sandbox, where the function returns `['198.19.0.2']` — only the first pass executes. The cost claim should read "three mechanisms *maintained*, up to three run, and then only on hosts the earlier passes fail".
- **Mechanism (as corrected):** ~53 lines, a platform-guarded syscall path and a network-stack probe, maintained solely for a cosmetic banner line whose actionable half needs no enumeration — verified.
- **Fix sanity:** Keeping getaddrinfo + the `--bind` line and deleting the other two passes is LESS-NOT-MORE compliant, with one trade-off the finding underplays: the ioctl pass exists (per the function's own docstring) for POSIX hosts whose hostname resolves only to loopback — on such hosts the banner would lose its address list entirely. Since the list is explicitly best-effort/cosmetic, the trade is defensible, but it should be stated as a knowing trade, not "loses nothing".

---

## Tally

| Verdict | Count | Findings |
|---|---|---|
| VERIFIED | 24 | 101, 103, 104, 105, 106, 107, 108, 109, 110, 111, 112, 113, 114, 115, 116, 117, 118, 119, 120, 121, 122, 123, 124, 125 |
| VERIFIED-DRIFT | 1 (+ baseline) | 126; BASELINE-PY assessed separately above |
| WEAKENED | 1 | 102 |
| REFUTED | 0 | — |
| BLOCKED-ENV | 0 | — (UDP block affected only baseline classification, which was reproduced and classified, not blocked) |

Total: 24 VERIFIED + 1 WEAKENED (102) + 1 VERIFIED-DRIFT (126) = 26 findings, plus BASELINE-PY VERIFIED-DRIFT. No finding was REFUTED outright; the only defeating evidence found was against 102's stated failure mode and the baseline's "genuine" NFS4 classification.

## CANDIDATES (new defects, with evidence — not in the redo report)

- **F-1 — Direct script execution is broken suite-wide by missing `sys.path` prologues, including in files that *do* have `unittest.main()`.** Evidence (executed in /tmp/ps2sF): `python3 tests/test_art_fetch.py` (has the `__main__` guard, no path prologue) → `ModuleNotFoundError: No module named 'launcher'`, same as all 8 RE-SERV-102 files. Only files that insert the root themselves (e.g. `test_smb2_server.py`, `test_netinfo_and_smb.py`) or import no first-party package (e.g. `test_attribution_policy.py`) actually run directly. So "add the main guard" cannot deliver the direct-execution convention RE-SERV-102 wants; the suite's real convention is `python -m unittest` from the root, and any direct-run fix must add the path prologue too.
- **F-2 — `test_nfs4_server…test_endless_empty_fragments_close_the_connection` is timing-fragile by construction.** Evidence: client socket timeout is 5 s (`tests/test_nfs4_server.py:890`, `create_connection(..., timeout=5)`); the test sends `p32(0) * (MAX_RECORD//4 + 1)` = 264,193 empty fragments (`MAX_RECORD = 1024*1024 + 8192`, `nfs4_server/nfs4_server.py:49-50`); the server consumes them one `_recv` per fragment and only closes after `total > MAX_RECORD` (`nfs4_server.py:505-517`). Observed: original baseline run → `TimeoutError` on `recv`; my full run → pass; 5/5 isolated runs → pass at 2.5–5.1 s each, i.e. operating near the timeout budget. A smaller fragment count over the cap, or a longer/assert-tolerant timeout, would make the close-condition test deterministic.

---

## Part G — RE-SERV-127–160 (Python duplication / structure)


- **Pin confirmation:** `git -C ~/workspace/audit-clones-2026-11-13/PS2-Servers rev-parse HEAD` → `c5f021c4d38698860f56b03498adc261dbb8d8dd` — matches the assigned pin exactly. Clone read-only; all execution ran on copies/extractions under `/tmp/verifyG`.
- **Method:** every cited file/function/literal grepped; duplication claims re-derived by extracting and diffing the cited bodies and recounting by grep; mechanism claims re-derived from code, with execution where noted. No UDP network execution was needed (no BLOCKED-ENV items in this batch).

---

### RE-SERV-127 — VERIFIED
- **Citations:** `smb2_server/smb2_srvsvc.py:134-137` (`write` → `self.outbox += self.transact(data)`) and `:144-161` (`transact`: `frag_len > len(data)` → `RpcError("not a DCE/RPC v5 PDU")`; REQUEST without `PFC_FIRST_FRAG|PFC_LAST_FRAG` → `_fault(..., NCA_UNSPEC_REJECT)`) — exact. No buffering anywhere upstream: `smb2_server.py:1170` (`h_write`) passes each SMB WRITE's payload straight to `h.pipe.write(data)`; `:1390` FSCTL_PIPE_TRANSCEIVE calls `transact` directly.
- **Mechanism / Execution (EXECUTED, /tmp copy of `smb2_srvsvc`):** built a valid 72-byte BIND PDU with the module's own `_header`/syntax constants. Whole PDU in one `write()` → returns 72, BIND_ACK in outbox. Same PDU split: `write(pdu[:10])` raised `RpcError: short PDU` (which `h_write` maps to `STATUS_INVALID_PARAMETER`); the second chunk also raised. A REQUEST PDU with fragment flags `0` returned a FAULT PDU (ptype 3). Both limbs confirmed live, not just by reading.
- **Fix:** Sound. Buffering in `write()` until `frag_len` bytes are present covers the WRITE-split case in a few lines; the audit correctly leaves RPC-fragment reassembly as a documented limit. One unstated sub-case: a single WRITE carrying *two coalesced* PDUs is also mishandled today (`transact` slices to `frag_len` and discards the tail) — a buffering fix should loop, not just wait.

### RE-SERV-128 — REFUTED
- **Citations:** Definition and both callers exist exactly as cited (`udpfs_server/udpfs_server.py:2746-2767`, callers `:2968`, `:3003`), and the test seam exists (`tests/test_session_summary.py:296` monkeypatches it to return 1024). The mechanism claim built on them is false.
- **Mechanism / Execution (EXECUTED — extracted the actual method via AST and ran it):** the audit's proof ignores the tie-break limb. Candidates are iterated in order `(1024,512), (1280,256), (1408,128)`; on a packet-count **tie**, a larger alignment wins, and 1024 is visited *first*. Results from the real code: `_optimal_chunk_size(0)=1024`, `(1)=1024`, `(100)=1024`, `(1024)=1024`, `(1025)=1280`, `(2048)=1024`, `(5000)=1280`, `(12800)=1280`, `(20000)=1408`, `(100000)=1408`. Distinct outputs over the swept domain: `{1024, 1280, 1408}`. The audit's premises "the 1024- and 1280-byte candidates always need *more* packets" and "for every input, including 0, the method returns 1408" are both false — small transfers tie at 1 packet and 1024's alignment 512 beats the incumbent's 128.
- **Fix:** Invalid as proposed. Replacing call sites with `MAX_DATA_PAYLOAD` (1408) and deleting the method would *change wire behaviour* for every transfer ≤1024 bytes and for the 1280 ranges, and would break the very test seam the finding itself flags. Withdraw.

### RE-SERV-129 — VERIFIED
- **Citations:** `nfs4_server/nfs4_server.py:1045-1064` `build_parser` has no `--session-summary`; `grep -ci session nfs4_server/nfs4_server.py` = **0** (the word does not occur once in the file). Present as cited in `http_server/http_server.py:1155,1183` (import comment at `:47` literally reads `shared by every server`) and `udpbd_server/udpbd_server.py:399-402,444-449`. Recount repo-wide: SMBv1 (`smbserver_opl.py:1701,1752-1753`), SMBv2 (`smb2_server.py:1755,1789-1790`), UDPFS Core (`ps2servers_core.py:559,587-588`), HTTP and UDPBD all wire `launcher.session_stats.SessionStats`; NFS wires nothing. The launcher agrees: `launcher/servers.py` appends `--session-summary` in five argv builders (`:156,199,354,378,555`) and `_nfs_argv` (`:611`) is not one of them — so no crash, just the silent gap described.
- **Mechanism:** Absence claim, grep-verified as above.
- **Fix:** The "state the exclusion in parser help/README" alternative is the LESS-NOT-MORE-compliant half and costs ~1 line. The wiring alternative adds event-recording code to NFS (net addition, justified only as completing a shared feature, not as deduplication). Footnote: the standalone `udpfs_server/udpfs_server.py` main also lacks the flag, but UDPFS-the-server has the feature through the Core entry point; NFS has no entry point with it at all, so the title stands.

### RE-SERV-130 — VERIFIED
- **Citations:** Go, `native/ps2servers-edge/internal/udpfs/operations.go`: `handleWriteRequest` at `:547-595` does `st.WriteBuffer = make([]byte, 0, size)` and stores the declared `size` nowhere else (no `WriteExpectedLen` assignment — grep for `WriteExpectedLen` returns only `session/session.go:97,107,126`, `blockdev.go:301`, `operations.go:634-635,658`). The `> 0` enforcement at `:634-643` therefore only ever fires for BWRITE. `completeWrite` (`:653-…`) routes on `WriteIsBlock`; the file branch writes `buf`, syncs and `sendWriteDone(st, int32(n))` with no comparison to any declared length. `completeBlockWrite` (`blockdev.go:344-…`) has the exact-length check and the quoted "same defect as the overflow bug in inRange" comment, verbatim.
- **Mechanism:** Re-derived: a plain WRITE sequence is bounded only by `transferCap` and driven by the client's `totalChunks`; declared-vs-delivered is never compared on that path. No defeating guard found (`ResetWrite` zeroes `WriteExpectedLen`, `session.go:126`, and only `startBlockWrite` sets it).
- **Fix:** Sound and small: set `WriteExpectedLen` in `handleWriteRequest` and add the completion check to the non-block branch. Two implementation notes the fix should carry: (1) a declared size of 0 would still be unenforced under the existing `> 0` sentinel; (2) the field's doc comment (`session.go:97`, "how many bytes a BWRITE said it would deliver") must be updated or the next reader re-learns the wrong invariant.

### RE-SERV-131 — VERIFIED
- **Citations:** `launcher/main.py:107-409` is `_apply_gui_review_fixes` (next def at `:412`; ~302 lines), docstring at `:112-113` says "This stays as a runtime shim so release-risk remains low", de-duplication comments at `:130-145` region present. Patched `append_log` at `main.py:393-406`; original at `launcher/gui.py:3042-3053`.
- **Mechanism / Diff:** Extracted both `_append_log` bodies: identical except exactly one behaviour — the patched copy computes `at_bottom = widget.yview()[1] >= 0.99` and only `see("end")` if already at bottom; gui.py's original calls `widget.see("end")` unconditionally. Guard flag `_ps2_gui_review_fixes_patched` confirmed at the tail (`main.py:408-409` region). Drift claim is precise, not overstated.
- **Fix:** Sound (finish the migration into gui.py); it is a move, not a net deletion, but it does remove the second copy, which is the point.

### RE-SERV-132 — VERIFIED
- **Citations:** `launcher/ra_account_worker.py:271-416` `AccountSession` with all seven `begin_*` methods and `accept` — exact. Production literals confirmed in `launcher/achievements.py`: `SetContext(creds.username, 1, …)`, `AccountTask(1, 1, "set", …)`, `AccountTask(2, 1, "start_session", …)` in the resolver (~`:183-190`), `service.bind_account(creds.username, 1)` at `:232`, and submission tasks taking `ctx.epoch` (the same literal-1 context) at `:298,311`.
- **Mechanism / Census (recounted):** `grep -rn "begin_login|begin_resume|begin_import|begin_set|begin_start_session|begin_award|begin_ping|\.accept(" launcher/ tests/` — every caller of `begin_*`/`accept(` is in `tests/test_ra_account_worker.py`, `tests/test_ra_evaluator.py`, `tests/test_ra_owned_service.py`; zero in `launcher/`. The only production touch of a session object is `achievements.py:254` `service.run_account(lambda session: session.logout(forget=True))`. `AccountSession.logout()` does `self._epoch += 1` (`ra_account_worker.py:405-410`), so the generation design exists and the shipped runtime never uses it.
- **Fix:** The either/or framing (wire it or delete the surface) is the right shape; no new mechanism proposed. Complies with LESS-NOT-MORE under either branch.

### RE-SERV-133 — WEAKENED
- **Citations:** All exact: `launcher/ra_sets.py:156-158` accepts `{"opl", "orbit-v1.0.2", "caduceus"}` in `derive_direct_watch`, orbit validator `launcher/ra_watchlist.py:60-73` (1216-byte ceiling), `launcher/ra_resolver_process.py:27-29` validator set is only `{"opl", "orbit-v1.0.2"}`, production profile production at `launcher/achievements.py:152` is `"caduceus"` or `"opl"` only. Repo grep confirms no code constructs an `IdentificationResult` with profile `orbit-v1.0.2` (the only orbit-profile derivations are in tests).
- **Mechanism — true scope is narrower than filed:** the caduceus limb is **latent, not live**. The only production `ProcessResolver` is inside `AccountWorker` (`ra_account_worker.py:238`), and it **overrides** the validator with `validate=valid_account_result` — it never runs `valid_identification_result`. The caduceus production path (`achievements.py` `resolve_job` → `OwnedService`) never crosses the IPC validator at all; default-validator `ProcessResolver` instances exist only in `ra_resolver_process.py`'s own probes (`:186,191`) and tests. So no shipped flow currently discards a caduceus result; what exists is a closed-set disagreement that would bite the first caller who wires the default validator to the owned runtime.
- **Fix:** Still sound and small (one set edited, or the orbit profile deleted), but it should be filed as a vocabulary/latent-consistency fix, not as a live discard bug.

### RE-SERV-134 — VERIFIED
- **Citations:** `launcher/__init__.py:14-92` contains `_GuiFinder`/`_GuiLoader`, inserted at `sys.meta_path[0]`, with the quoted docstring concession ("The normal, boring implementation would be direct calls from the GUI build path"). `launcher/asset_skin.py:152-164` is `install()`; its tab-icon patcher replaces `ttk.Notebook.add` (`asset_skin.py:42-56`) and it calls `full_skin_controls.install`. `launcher/full_skin_controls.py:9-204`: `install` replaces `ServerCard._build` (`:198`) and `LauncherApp.apply_server_settings`/`revert_server_defaults` (`:202-203`), guarded by `_ps2_full_skin_controls_patched`. `launcher/gui.py:439` is the `LauncherApp._build` being wrapped. Patch-order/guard-flag description matches the code.
- **Fix:** Sound: the two direct calls at the top of `LauncherApp._build` replace ~78 lines of import machinery with no intended behaviour change. This is a genuine net-removal fix.

### RE-SERV-135 — VERIFIED
- **Citations:** `launcher/ps1_index.py` is exactly 147 lines, scanner at `:88-147`, `ember_name_problem` at `:52-61`, `Ps1Game` at `:34-47`.
- **Mechanism / Census (recounted):** `grep -rn "ps1_index|Ps1Game" launcher/ tests/` — no import or use anywhere in `launcher/` outside the module itself; only consumer is `tests/test_companion_fixtures.py:22,129-157`. The capability matrix quote is real: `docs/COMPANION-CAPABILITY-MATRIX.md:53` "not shown in the Library tab yet". Shipped PS1 handling via `game_library.MEDIA_TYPES["POPS"]` confirmed as the separate path.
- **Fix:** Either/or (surface or delete) is compliant; deletion is the LESS-NOT-MORE default if the Library-tab feature is unscheduled.

### RE-SERV-136 — VERIFIED
- **Citations:** `launcher/app_icon.py` is exactly 246 lines with `_GLYPHS` (`:17`), `_rgba_pixels` (`:165`), `_draw_square`/`_draw_text` (`:214,221`), `_ico_image` (`:129`), and the per-pixel `tk_photo` at `:91-113`; docstring: generated "so the source tree does not need binary assets". `launcher/assets/theme/LOGO.png` exists (602,983 bytes) and is used for the About tab at `launcher/gui.py:1523-1524`. Third source confirmed: `launcher/tray_linux.py:61-95` `_icon_image` prefers LOGO.png and falls back to a Pillow-drawn rounded-rectangle "PS2" badge.
- **Fix:** Directionally sound, with one understated cost: `app_icon.py` is deliberately **stdlib-only** (that's why the rasterizer exists). "Keep only `write_ico`'s ICO container fed from the shipped image" is not pure deletion — feeding LOGO.png into an ICO without Pillow means embedding the PNG payload directly in the ICO container (possible, but new code), or accepting a Pillow dependency the module currently avoids. Effort Med is honest; the fix text should name that trade.

### RE-SERV-137 — VERIFIED
- **Citations / Diff:** `launcher/library_index.py:109` `read_index` is the full implementation. `launcher/library_catalog.py:451-454`: `Catalog.indexed_games` body is a docstring plus `return read_index(self.path, root, max_entries=max_entries, cancel=cancel)` — pure pass-through. `launcher/game_library.py:288-296`: `Library.shared_index` adds exactly one behaviour, translating the missing-catalogue `FileNotFoundError` into the friendlier `ValueError`. Caller census recounted: `indexed_games`' one production caller is `library_panel.py:326`; `shared_index`'s two call sites are `library_gui.py:177,399`; tests call all three layers.
- **Fix:** Sound and net-negative (~13 lines, one layer). "One wrapper maximum" is the right bound.

### RE-SERV-138 — VERIFIED
- **Citations / Census (recounted):** `conformance/integration/http_probe.py` is 219 lines. Repo-wide grep for `http_probe` across `*.py/*.yml/*.md/*.toml/*.txt` returns **zero** references outside the file itself. Sibling wiring confirmed in `.github/workflows/ci.yml:109,112,117` (`opl_http`, `riptopl_http`, `neutrino_smb`) and `edge.yml:70,127` (`udpfs_probe`).
- **Fix:** Wire-or-delete is compliant; no third option is defensible for a probe.

### RE-SERV-139 — VERIFIED
- **Citations / Diff:** `grep -n "session_id = int.from_bytes" smb2_server/smb2_server.py` → exactly two hits, `:863` and `:915`, both `conn.session_id = int.from_bytes(os.urandom(8), "little") or 1`, each immediately under `if conn.session_id == 0:` — one in the NTLM NEGOTIATE/CHALLENGE branch, one in the AUTHENTICATE branch of `h_session_setup`. Verbatim, as filed.
- **Fix:** Sound; a single post-dispatch check (or `ensure_session_id()`) removes the pair with no behaviour change.

### RE-SERV-140 — VERIFIED
- **Citations / Diff:** Extracted `smbv1_server/smbserver_opl.py:1128-1154` (`_build_trans2_reply`) and `:1344-1362` (RAP branch of `h_transaction`) and compared line-by-line: same `base = 32 + 1 + 20 + 2`, same `(base + 3) & ~3` param offset, same pad1/pad2 arithmetic, same `struct.pack("<HHHHHHHHHBB", …)` 10-word envelope, same body assembly. The RAP copy is the same construction with `rparams`/`data` substituted. ~18 duplicated lines, as filed.
- **Fix:** Sound; `_build_trans2_reply` already takes exactly the parameters the RAP copy would pass.

### RE-SERV-141 — VERIFIED
- **Citations:** `Share.compressed_source` at `smbv1_server/smbserver_opl.py:303-336` and the scan inside `_list_dir` at `:1156-1198` both exist as described: listdir → stem match → best extension in `exts` order. Differences recounted from the bodies: `compressed_source` requires the name to end `.iso` with `len(base) > 4`, collects `by_ext` case-insensitively on extension but case-sensitively on stem, then re-resolves and re-checks containment (`_inside`) + `isfile`; `_list_dir` instead tracks `taken` (real `.iso` names) and picks `chosen[stem]` by `exts.index(...)` comparison, with no containment re-check. The shared core (folder scan, stem, preference order) is genuinely implemented twice, and the surrounding rules genuinely differ.
- **Fix:** Sound as scoped — extract only the scan, keep each caller's own extra check at the call site. Net saving is small but real, and the anti-disagreement benefit is the actual justification.

### RE-SERV-142 — VERIFIED
- **Citations:** `smbv1_server/smbserver_opl.py:489-505`: `alloc_fid`/`alloc_tid`/`alloc_sid` are the same two-line body (`self.next_X = (self.next_X + 1) & 0xFFFF or <floor>; return self.next_X`) with floors `0x1000`/`0x0001`/`0x0001`. Call sites assign without any in-use check: `conn.files[fid] = of` at `:827` and `:938`, `conn.trees[tid] = …` at `:778`-region, `conn.searches[sid] = …` at `:1251`. `cleanup()` (`:501-504`) closes only what remains in `conn.files`.
- **Mechanism / Execution (simulated the allocator arithmetic):** starting from `next_fid=0x1000` (`:406`), the FID cycle has 61,440 distinct values (`0x1001…0xFFFF`, then `0x1000` via the `or` on wrap) before repeating — matching the filed "~61k opens". On repeat, `conn.files[fid] = of` silently evicts a still-open `OpenFile`, which `cleanup()` can then never close: the handle leak follows mechanically from the cited code, exactly as the finding's INFERRED label states.
- **Fix:** Sound. One parameterised helper plus skip-if-present on FID allocation; note the same skip logic should apply to TID/SID dicts, which the fix text mentions only via "the same unchecked pattern" in the problem statement.

### RE-SERV-143 — VERIFIED
- **Citations:** `udpfs_server/ps2servers_core.py:523-578` `build_parser` and `:591-686` `main`; standalone parser+main inside `udpfs_server/udpfs_server.py:3441-3639` (`main` — there is no separate `build_parser` there, a trivial framing difference). Both define the same option set (ports, bind, sector size, read-only, verbose, compression group, cache size, data-port, single-port, peer-timeout, metrics, max-transfer, max-sessions) with differing help text and differing validations (e.g. Core validates `--peer-timeout` range and `--port` via `_parse_port`; the standalone clamps timeout and exits differently on bad paths).
- **Mechanism / Counts (recounted by AST):** the compression-precedence block is the same four steps in both (`ENABLE_COMPRESSION` env → `NO_COMPRESSION` → `args.enable_compression` → `args.no_compression`; Core `:601-608`, standalone `:3563-3575` region, differing only in quote style and one extra `args.enable_compression = enable_compression` line in the standalone). Inside Core's `main`, the Modulo construction (`:625-652`) and Auto construction (`:655-682`) share exactly **17 keyword-argument names** (16 with identical values, plus `single_port` present in both with different values), differing by class, `modulo_compat=True`, and Auto's `protocol_mode`/`fallback_interval` — the filed count is correct.
- **Fix:** The in-`main` half (one kwargs dict, construct once) is an unambiguous net win. The cross-file half is honestly presented with its own alternative (declare the standalone the frozen copy and stop treating the parsers as peers); either branch complies.

### RE-SERV-144 — VERIFIED-DRIFT
- **Citations:** `udpfs_server/ps2servers_core.py:360-383` (`_route_data_via`) and `:384-400` (`_handle_data` preamble) both exist and do duplicate: `Header.unpack(data)`, `struct.unpack("<I", data[2:6])[0]`, `header_words = (data_header >> 14) & 0xF`, `data_bytes = (data_header >> 18) & 0x3FFF`, `payload_size = header_words * 4 + data_bytes`. `super()._handle_data` is called at `:475`, and the base handler (`udpfs_server.py:1641-1655`) parses again.
- **Corrections:** (1) The **oversize check is not duplicated** — `if payload_size > len(data) - 6: return` appears only in `_route_data_via`; `_handle_data` computes `payload_size` only to test `> 0` in its profile-resolution branches. The filed sentence "and apply the same oversize check" is wrong for the second copy. (2) The base class's third parse is not the same shifts/masks code: it uses `DataHeader.unpack(data[2:6])` and `data_hdr.hdr_word_count * 4 + data_hdr.data_byte_count` — the same *computation*, a different implementation. So: two identical manual parses in Core (minus the oversize check), plus one structurally different parse in the base.
- **Fix:** Still sound for the Core pair (parse once, carry on the queued item); the saving is the ~8-line preamble, not the preamble-plus-check the text implies.

### RE-SERV-145 — VERIFIED-DRIFT
- **Citations:** Core `_attach_session_stats` at `udpfs_server/ps2servers_core.py:581-588` and the inlined copy in `smbv1_server/smbserver_opl.py:1748-1754` are, extracted side by side, the identical dance: same repo-root computation from `__file__`, same `os.path.isdir(…/launcher)` + `sys.path.append` guard, same lazy `from launcher.session_stats import SessionStats`, differing only in the key (`"udpfs"` vs `"smbv1"`). That core claim is exact.
- **Correction:** the parenthetical "(A third server, the standalone UDPFS `main`, does the same again.)" is **false**. `grep -n "session_summary|SessionStats" udpfs_server/udpfs_server.py` returns nothing in its `main` at all — the standalone has no `--session-summary` argument and no attach code (its only hit is a comment at `:634` describing the attribute Core sets). The other servers that attach (SMBv2 `:1789-1790`, HTTP `:1183`, UDPBD `:448-449`) do it in one or two lines *without* the root/`sys.path` dance, so the identical-snippet population is exactly two, as the main text (but not the parenthetical) says.
- **Fix:** Sound for the two real copies (one helper parameterised by key). Delete the parenthetical's premise from any follow-up.

### RE-SERV-146 — VERIFIED
- **Citations:** `udpfs_server/udpfs_server.py:390-479` `get_compressed_info`, including the CD correction at `:425-440` with the quoted "same predicate as the CHD wrapper … so the size reported here can never disagree" comment — verbatim in the code. `udpfs_server/compressed_iso/chd.py:262-303` `ChdFileWrapper._parse_header` implements the same predicate (`unit_size == 2448 or any CD codec`) and the same correction (`total_frames * 2048`, then `exact_cd_iso_size` override) — the CD correction exists exactly **twice**, as filed. `cso.py:54-89` and `zso.py:64-107` wrapper parses duplicate the probe's magic/size/block extraction for CSO/ZSO. `limits.py:19-116` parses the same headers a third time (`indexed_header`, `chd_header`).
- **Mechanism nuance (does not change the verdict):** the third parse is not fully independent in the serving path — the wrappers, in `_strict` mode, *delegate* to `limits` (`indexed_header(self, header)` / `chd_header(header)`). `limits.py` is the opt-in inspection implementation; the duplication that is load-bearing by hand is probe ↔ wrapper, exactly where the finding puts the weight.
- **Fix:** Sound; the probe-from-wrapper variant must keep the probe allocation-light, which the fix text already stipulates.

### RE-SERV-147 — VERIFIED
- **Citations:** Pre-check at `udpbd_server/udpbd_server.py:208-209` (`is_raw and sector_nr + sector_count > self.bd.sector_count()` → `EINVAL`), followed at `:219-222` by `self.bd.seek(sector_nr, sector_count)` for raw backends. Census of `is_raw`: exactly two backends set it — `launcher/raw_storage.py:18` `RawDevice` and `launcher/virtual_exfat.py:64` `VirtualExfat`. Both enforce the same bound in `seek`: `RawDevice.seek` (`raw_storage.py:37-45`) raises `EINVAL` when `position + sector_count*512` exceeds the device size; `VirtualExfat.seek` (`virtual_exfat.py:228-233`) raises `EINVAL` when the request end exceeds its size. The write path (`udpbd_server.py:250-255`) has no pre-check and relies on `seek` raising, converting it to a `-1` reply — as filed.
- **Fix:** Sound; deleting lines 208-209 changes nothing observable for either raw backend (same exception type from the same call a few lines later).

### RE-SERV-148 — VERIFIED-DRIFT
- **Citations:** `nfs4_server/nfs4_server.py:880-884` (`_op_open_confirm`) and `:886-890` (`_op_close`), ops-table entries at `:368,371` — locations exact.
- **Correction — NOT byte-identical:** extracted bodies differ in the order of the first two statements:
  - `_op_open_confirm`: `stateid = r.fixed(16)` **then** `r.u32()`
  - `_op_close`: `r.u32()` **then** `stateid = r.fixed(16)`
  The remaining three lines (`self._need(s.cfh)` and the seqid-bump return) are identical. The transposition is not drift — it is required: OPEN_CONFIRM's wire order is stateid-then-seqid, CLOSE's is seqid-then-stateid. "Byte-identical bodies" and "the same five lines twice" are both wrong (the bodies are four lines, one pair transposed).
- **Fix:** Overstated as filed. A shared helper can only own the common *tail* (`_need` + bumped-stateid return, 2 lines) once each caller has parsed its two fields in wire order — a saving of ~2 lines, not ~5, and the helper signature must take the already-parsed `stateid`. The underlying observation (near-identical ops that must change together) stands.

### RE-SERV-149 — VERIFIED
- **Citations / Diff (executed):** extracted `http_server/http_server.py:1220-1237` and `nfs4_server/nfs4_server.py:1101-1113` and diffed: the executable code of `_stop_on_sigterm` is **identical**; the only difference is the 5-line docstring present in the HTTP copy and absent in the NFS copy — exactly the filed "identical code, already drifting in the small". Both servers' outside-directory imports confirmed (HTTP imports `launcher.session_stats` at `:47`; NFS inserts `smb2_server` into `sys.path` at `:40-43`), so the "standalone file" rebuttal holds.
- **Fix:** Sound (~13 lines removed, one home for the platform caveats). See the cross-check note: this fix and RE-SERV-157's fix should be executed once, together.

### RE-SERV-150 — VERIFIED
- **Citations:** Go, `native/ps2servers-edge/internal/compression/image.go`: `openImage` extent check at `:198-199` and `SizeOf`'s at `:330-331` — extracted both: `blocks := (size + block - 1) / block` (resp. `h.size`/`h.block`) followed by `if blocks > 1<<28 || uint64(headerSize)+(blocks+1)*4 > uint64(fileSize)` with the **identical** error string `"image index too large or extends past file size"` in both. `parseHeader`'s anti-drift comment at `:238-241` is verbatim as quoted, and it indeed covers only the 24-byte prologue.
- **Fix:** Sound; extracting the block-count/extent check is a small net deletion and completes the invariant `parseHeader`'s comment already claims.

### RE-SERV-151 — VERIFIED
- **Citations:** `parseCHDHeader` at `internal/compression/chd.go:31-95` and the header half of `readCDTrackTableOpt` at `internal/compression/chd_cuebin.go:193-230`: both open the file, read `chdV5HeaderBytes` (= **124**, `chd.go:15`), check magic `"MComprHD"`, version 5 and header size 124, and decode logical (`[32:40]`), hunk (`[56:60]`) and unit (`[60:64]`). Validation divergence confirmed: the track-table parser requires `logical % 2448 == 0` and `unit == 2448` (`logical%cdFrameBytes != 0` / `unit != cdFrameBytes` → error); `parseCHDHeader` requires neither and derives CD-ness from `unit == 2448` **or** any of the three CD codec IDs. And `parseCHDHeader` does call `exactCDISOSize` (`chd.go:92`), which re-opens the file and runs `readCDTrackTableOpt` (`chd.go:104`) — the double-parse is on one call path, not just in the codebase.
- **Fix:** Sound (one raw-header struct, per-caller extra constraints); net shrink plausible at Effort M.

### RE-SERV-152 — VERIFIED
- **Citations:** `itoa` at `internal/smb/server.go:244-256` (13 lines), sole caller at `:166` (`net.JoinHostPort(s.cfg.Bind, itoa(s.cfg.Port))`) — grep for `itoa(` returns exactly those two sites. Negative-input behaviour as filed: for `n < 0` the `for n > 0` loop never runs and the function returns `""`, where `strconv.Itoa` returns `"-…"`. The caller's value is range-checked upstream (`server.go:130`: `cfg.Port < 1 || cfg.Port > 65535` rejected), and the hand-sized `[8]byte` buffer is adequate only because of that check — all as filed.
- **Fix:** Sound; `strconv.Itoa` at the one call site is a pure deletion.

### RE-SERV-153 — VERIFIED
- **Citations / Diff:** `internal/smb/server.go:425-447`: `allocFID`/`allocTID`/`allocSID` extracted — identical increment-and-wrap bodies (`c.nextX++; if c.nextX == 0 { c.nextX = <wrap> }; return c.nextX`) differing only in the field and the wrap value (`0x1000` vs `0x0001`). The parenthetical is also correct: none of the three checks whether the wrapped ID is still in use.
- **Fix:** Sound (one helper parameterised by counter + wrap, or a small allocator type); net shrink.
- **Cross-reference:** this is the Go counterpart of RE-SERV-142's Python allocators — same pattern, different codebase and files; not a double-filing.

### RE-SERV-154 — VERIFIED
- **Citations / Count (recounted):** `grep -c 'val == "1" || val == "true"' internal/webui/config_uci.go` = **16**, at lines 63,79,81,93,95,103,109,115,125,133,135,139,149,157,159,163 (all inside `uciBackend.Load`, `:63-163`). `uciBool` at `:14-22` accepts `1/on/true/yes/enabled` case-insensitively and its only use in `Load` is at `:89` for `main.udp_batch` (its other references are tests). `Save` writing only `1`/`0` is consistent with the masking claim.
- **Fix:** Exemplary LESS-NOT-MORE: the helper already exists and is already tested; replacing the 16 inline tests is pure deletion of repeated logic. The resulting semantics change (accepting `on/yes/enabled` for all fields) is the finding's stated intent, not a side effect.

### RE-SERV-155 — VERIFIED
- **Citations / Counts (recounted by grep on `cmd/ps2servers-edge/main.go`):** `flag.NewFlagSet` = **4**; `"log-format"` definitions = **4** (`:161,304,365,489`); `--log-format` validations = **3** (`:194,322,381` — `runWebUI`, whose flag is at `:489`, has none, exactly as filed); `signal.NotifyContext` = **4** (`:247,335,431,541`); `edgelog.New` = **4** (`:241,325,384,518`). Logger/signal blocks at the cited ranges confirmed.
- **Fix:** Sound and self-justifying under the audit's own rule: identical logic in 4 places, drift already produced a behavioural bug (the missing webui validation), net line count goes down.

### RE-SERV-156 — VERIFIED-DRIFT
- **Citations:** SMBv1 `_take_445` at `smbv1_server/smbserver_opl.py:1546-1628`; SMBv2 `take_445` at `smb2_server/smb2_server.py:1479-1652` plus `listen_after_take_445` at `:1653`. Both stop LanmanServer, wait for Stopped, restore on exit. The absolute-PowerShell resolution is triplicated as filed: `launcher/windows_setup.py:57-71` (`_powershell_executable`), `smbserver_opl.py:1553-1560`, `smb2_server.py:1496-1502` — the two server copies are near-verbatim.
- **Correction (minor):** "the SMBv1 copy lacks foreign-dependent checks" overstates the gap. The SMBv1 copy *does* query srvnet's dependent services (`(Get-Service srvnet …).DependentServices.Name`) and names the running pin-holder before bailing when 445 stays bound. What it lacks is what SMBv2 added on top: **pausing** the srv/srv2/srvnet drivers (in dependency order, `_TAKE_445_SERVICES` at `smb2_server.py:1463`) and the `foreign_dependents()` gate on LanmanServer itself before pausing anything. The drift and the risk asymmetry are real; the SMBv1 copy is less primitive than the text implies.
- **Fix:** Sound in direction (one shared module, SMBv2 behaviour as the single behaviour; the cross-directory import precedent cited is real). Effort L is honest given this is the riskiest code in either server.

### RE-SERV-157 — VERIFIED
- **Citations:** `launcher/serve.py:79-102` `install_stop_handlers`, called at `serve.py:158` for every `--serve` child before the server module is loaded; it installs handlers for both SIGTERM and SIGBREAK (`_stop_like_ctrl_c`). HTTP calls its own `_stop_on_sigterm()` in `main` (`http_server.py:1203`, def `:1220`) and NFS likewise (`nfs4_server.py:1091`, def `:1101`); `signal.signal(SIGTERM, …)` replaces the serve.py-installed SIGTERM handler, while serve.py's SIGBREAK handler survives — so the effective handler set genuinely differs by entry point, as filed.
- **Fix:** Sound under either branch. **Overlap note:** the HTTP/NFS limbs of this finding rest on the same two function bodies as RE-SERV-149. The two findings are distinct claims (149: the two server copies duplicate *each other*; 157: in launcher mode they redundantly *re-install* what serve.py already installed), but they should be cross-referenced and fixed once — a single shared installer imported by both servers satisfies both.

### RE-SERV-158 — VERIFIED
- **Citations:** `launcher/process.py:19` `STOP_PIPE_ENV = "PS2SERVERS_STOP_PIPE"` (producer; set at `:59`, popped at `:61`) and `launcher/serve.py:72` `STOP_PIPE_ENV = "PS2SERVERS_STOP_PIPE"  # launcher/process.py sets it` (consumer; popped at `:105`). Exactly two definitions repo-wide, identical today, independently editable — as filed. serve.py already imports from the package (`from .servers import REGISTRY` in the serve path), so the shared-definition cost claim holds.
- **Fix:** Sound; one definition, imported.

### RE-SERV-159 — VERIFIED
- **Citations / Census (recounted):** `grep -rn "def _parse_port"` repo-wide returns exactly **3** definitions: `launcher/windows_setup.py:115-121` (unparseable/empty → caller-supplied `default`; `int(str(value), 0)`), `launcher/status_client.py:68-75` (unparseable/empty/`False` → `0`; also `int(str(port).strip(), 0)`), `udpfs_server/udpfs_server.py:3378-3383` (unparseable or out-of-range → raises `ValueError`; range `0-65535` checked). Three failure semantics — fall back, give up, fail hard — exactly as filed.
- **Fix:** Sound as scoped (one launcher parser with an explicit `default`; the UDPFS raising variant kept but renamed/commented as the strict CLI parser). Note it deliberately does *not* force one semantics on the server — compliant with LESS-NOT-MORE.

### RE-SERV-160 — VERIFIED-DRIFT
- **Citations:** All cited functions exist: `launcher/netinfo.py:28-41` `_parse_linux_ip_addr`, `:44-60` `_parse_macos_ifconfig` (returns `list[str]`), `launcher/directlink.py:356` `_parse_linux_adapters`, `:460-476` `_parse_macos_ifconfig` (returns `(status, [{ip, prefix, origin}])` — same name, different signature and return, confirmed by extraction). The same-name collision is real and is the strongest part of the finding.
- **Correction — the count "four times" is imprecise in both directions:** netinfo's `get_all_adapters_info` (`:139-…`) contains **two** inline re-parses (Linux branch `:172-183`, macOS branch `:185-198`), not one — so the site count is 2 helpers + 2 inline branches + 2 directlink functions = **6 parse sites / 5 distinct functions**. Conversely, directlink's Linux parser is *not* another copy of the inet-line grammar: it parses `ip -j` **JSON** (`json.loads(addr_json)`), a different input format that cannot share the text parsers. The genuinely shared-grammar population is: netinfo's two text helpers, netinfo's two inline branches, and the macOS pair across files. The inline branches have also drifted as filed (they add `127.`/`169.254.` filtering the helpers don't do).
- **Fix:** Half sound: making `get_all_adapters_info` build on netinfo's own helpers (extended to carry the interface name) and renaming directlink's macOS parser are both clean wins. Any version of the fix that tries to unify directlink's JSON parser with the text parsers would not be — the finding's fix text, to its credit, does not propose that.

---

## Cross-checks within the range (same-mechanism pairs)

- **RE-SERV-149 ↔ RE-SERV-157 — overlapping, cross-reference required.** Same two `_stop_on_sigterm` bodies; 149 claims HTTP↔NFS duplication, 157 claims triple installation (serve.py + both servers). Distinct angles, one fix. Neither is a pure duplicate of the other, but filing/fixing them independently risks two different "single homes" for the same function.
- **RE-SERV-131 ↔ RE-SERV-134 — adjacent, not duplicate.** Both are GUI monkeypatching, and both ultimately wrap `LauncherApp._build` (main.py's `launcher_build` and `__init__.py`'s `build_with_addons` stack on the same method), but the bodies, files and mechanisms (entry-point shim vs import-hook + skin patchers) are disjoint.
- **RE-SERV-142 ↔ RE-SERV-153 — same pattern, different codebases** (Python SMBv1 vs Go edge SMB). Not a double-filing.
- **RE-SERV-129 ↔ RE-SERV-145 — complementary** (NFS missing the session-stats feature; the attach snippet duplicated among servers that have it). No overlap in bodies.
- **RE-SERV-146 ↔ RE-SERV-150/151 — same theme** (compressed-header parsing in multiple places) **in different languages/files** (Python probe/wrappers/limits vs Go `image.go`/`chd.go`). No shared bodies.

## Fix-sanity / LESS-NOT-MORE summary

Fixes that are pure deletion or use an already-existing helper, fully compliant: 134, 137, 139, 140, 147, 149, 152, 153, 154, 155, 158. Fixes compliant as either/or (wire-or-delete / keep-or-document): 129, 132, 135, 138, 143, 157. Fixes whose stated saving is overstated: 144 (no duplicated oversize check to remove), 148 (~2 lines, not ~5). Fix that is **invalid because its premise is refuted**: 128. Fix with an understated dependency cost: 136 (stdlib-only constraint).

## Tally

| Verdict | Count | Findings |
|---|---|---|
| VERIFIED | 27 | 127, 129, 130, 131, 132, 134, 135, 136, 137, 138, 139, 140, 141, 142, 143, 146, 147, 149, 150, 151, 152, 153, 154, 155, 157, 158, 159 |
| VERIFIED-DRIFT | 5 | 144, 145, 148, 156, 160 |
| WEAKENED | 1 | 133 |
| REFUTED | 1 | 128 |
| BLOCKED-ENV | 0 | — |
| **Total** | **34** | RE-SERV-127 … RE-SERV-160 |

## CANDIDATES (new defects, separate from the findings above)

None confirmed. Two observations that did not rise to candidate defects:

1. `smb2_srvsvc.SrvsvcPipe.transact` slices `data[16:frag_len]` and silently discards any bytes past `frag_len` in the same WRITE, so two PDUs coalesced into one WRITE lose the second — noted inside RE-SERV-127's fix bullet as a constraint on the proposed buffering fix, not filed separately because it is the same transport-completeness mechanism.
2. `launcher/ra_account_worker.py:302` — `begin_login` constructs and discards an `AccountTask(1, 1, "login", …)` before calling `logout()` and minting the real task. It reads as an eager-validation probe (the discarded construction runs `AccountTask.__post_init__` before the session is torn down), so it is plausibly intentional; without a failing case it is not filed as a defect.

---

## Part H — RE-SERV-161–190 (duplication / dead code / NFS)


- **Pin confirmation:** `git -C ~/workspace/audit-clones-2026-11-13/PS2-Servers rev-parse HEAD` → `c5f021c4d38698860f56b03498adc261dbb8d8dd` — matches the assigned pin exactly. Clone read-only; executions ran in a `/tmp/ps2test` copy.
- **Method:** refute-first — every cited file/symbol grepped, duplication bodies extracted and diffed, zero-caller symbols grepped repo-wide (production + tests + attribute access + string references), mechanism claims re-derived from code, selftests executed where the sandbox allows (UDP sendto blocked → multiclient selftest not run, BLOCKED-ENV for that limb only).

---

### RE-SERV-161 — VERIFIED
- Citations: `launcher/directlink.py:1100 parse_packet`, `:1272 _foreign_offer_server_id` both exist at cited lines.
- Mechanism: extracted both TLV loops (1110–1119 vs 1294–1303) and diffed — byte-identical 11-line walk (pad/end/length slicing); only the trailing consumer differs. `parse_packet:1106` rejects `op != 1`, and `_foreign_offer_server_id:1282-1286` explicitly calls it first and returns None for requests, then re-parses replies by hand. No shared TLV helper exists.
- Execution: diff as above.
- Fix: sound, LESS-NOT-MORE (one helper, net removal).

### RE-SERV-162 — VERIFIED
- Citations: `launcher/config.py:20 _app_dir`, `launcher/servers.py:22 frozen_self_exe` exist.
- Mechanism: diff shows the identical candidate sequence (`NUITKA_ONEFILE_BINARY`, then `sys.argv[0]`, `abspath` + `os.path.exists` check). Real differences are in the wrapper, not the probe: config gates on `frozen/__compiled__`, returns `dirname(path)` and falls back to the repo root when unfrozen; servers is ungated, returns the path itself and falls back to bare `sys.executable`. Importers of the servers copy confirmed by grep: `elevate.py:12`, `windows_setup.py:14`, `main.py:420` — and also `gui.py:38` (finding omits gui, does not contradict it). config.py imports nothing from servers.
- Fix: sound; note the fix must keep config's frozen gate / dirname shaping when deriving from `frozen_self_exe()`.

### RE-SERV-163 — VERIFIED
- Citations: `launcher/servers.py:211 _SMB2_FIELDS`, `:441` and `:457 fields=_SMB2_FIELDS` exact.
- Execution: imported the module — `SMBV2.fields is SMBV3.fields is _SMB2_FIELDS` → `True`.
- Fix: `fields=list(_SMB2_FIELDS)` per server — sound, no behaviour change today.

### RE-SERV-164 — VERIFIED
- Citations: `launcher/ra_credentials.py:31 _dpapi`, `launcher/caduceus.py:55 read_secret` exist; grep confirms exactly two `CryptUnprotectData` implementations repo-wide.
- Mechanism: read both bodies. `_dpapi` sets argtypes/restype, validates `outgoing.size` against `LIMIT` (16384), memsets source **and** output before `LocalFree`, raises on failure. `read_secret` re-implements inline, memsets/frees only the result buffer, never wipes its source copy, has no output-size validation, and returns `""` on file-missing / oversize-input / DPAPI-failure / decode-failure alike.
- Fix: sound (decrypt-only reuse + exception→`""` translation at the one call site).

### RE-SERV-165 — VERIFIED
- Citations: all eight sites read at the cited lines: `ra_account.py:14-20`, `ra_credentials.py:25-28`, `ra_session_client.py:59-62`, `ra_set_client.py:16-19`, `ra_sets.py:55-56 (SetContext)`, `ra_account_worker.py:44-46 (AccountTask)`, `ra_evaluated_receiver.py:44`, `ra_owned_service.py:143-146`.
- Mechanism: three rule sets confirmed in the text: (a) username 1..64 **with** `\0\r\n` rejection (account, credentials, session_client, owned_service, account_worker); (b) username 1..64 **without** control-char rejection (`ra_set_client.py:16`, `SetContext` `ra_sets.py:55`, `ra_evaluated_receiver.py:44` — the last also lacks it, which the finding lists but does not individually flag); (c) username **may be empty** (`ra_account_worker.py:44`: `len(self.username) > 64` only, emptiness rejected later only for the `login` operation). Token legs are uniformly 1..4096 + `\0` where present. No shared validator exists.
- Fix: one validator in `ra_credentials` — justified, removes code overall.

### RE-SERV-166 — VERIFIED
- Citations: all four validation loops read: `ra_sets.py:130-145 qualify_set` (ID via `_uint` = 1..2³²−1, expr 1..16384, no NUL, ASCII via encode, total > `MAX_RESPONSE` = 4 MiB, dup-ID rejection), `ra_account_worker.py:138-152`, `ra_resolver_process.py:38-60` (near-verbatim pair loop), `ra_evaluator.py:62-73`.
- Mechanism: `award_achievement` (`ra_session_client.py:117-145`) hand-rolls `json.loads` + `raw.get("Success")` and coerces with bare `int(raw.get("Score") or 0)`, while `start_session`/`ping` in the same class use `ra_sets.decode_response`, whose `_unique_object` hook rejects duplicate keys — confirmed at `ra_sets.py:23-42`.
- Fix caveat (not a refutation): `decode_response` also *raises* unless `Success is True`, but award's "User already has" path consumes a Success-false response — a naive switch would break it; the fix needs a decode variant that returns the dict without the Success gate. Duplication/bypass claim itself stands.

### RE-SERV-167 — VERIFIED
- Citations: `ra_protocol.py:109 clean_title`, `ra_receiver.py:115-117` (inline in `Receiver.complete`), `caduceus.py:35 clean`, `ra_notifications.py:9 safe_title`, `ra_overview.py:18 short` — all read.
- Mechanism: `clean_title` and `caduceus.clean` are character-identical bodies (NFKD → strip combining → ASCII-printable-or-space → limit; caduceus makes `limit` required, protocol defaults 63). The receiver copy is the same logic inlined with the comprehension clauses reordered and `[:63]` hard-coded (and `str(title)` without the `or ""` guard — equivalent for None via the join, output-equal). `safe_title` (whitespace-collapse, 96, keeps non-ASCII) and `short` (whitespace-collapse, caller limit, default 120 in overview) are the distinct display family, as filed.
- Fix: two surviving functions as proposed — sound.

### RE-SERV-168 — VERIFIED
- Citations: `ra_overview.py:94 fetch_engine_state`, `ra_session.py:133 SessionPoller._fetch` read side by side.
- Mechanism: identical construction — `account_url() + "state"`, `urlopen(timeout=2)`, `read(1024*1024 + 1)`, reject > 1 MiB, `json.loads`. Only differences: session version caches an explicit `self.url` when set, and the error strings differ exactly as quoted ("reply exceeds 1 MiB" vs "reply exceeded limit").
- Fix: sound, trivial net removal.

### RE-SERV-169 — VERIFIED
- Citations: all read — `game_library.Library` DB at `game_library.py:63` (`achievement-catalog.sqlite3`, own `games` schema :66-70) vs `library_catalog.default_database()` at `library_catalog.py:105-106` (`catalog.sqlite3`), same `<config>/library/` directory; folder prefs `library_panel.py:69` (`library/games_root.txt`, load/save :87-91) vs `library_gui.py:28` (shared config `game_library_folder`); importers `Catalog.import_image/import_images` (`library_catalog.py:467/500`, `copyfileobj` into `xb`) vs `Library.install/transfer` (`game_library.py:299/316`, temp + validation + `publish_new`, CD/DVD/POPS vs CD/DVD only); backups `Library.backup` (`game_library.py:178`) vs `Catalog.backup_sqlite` (`library_catalog.py:619`), both SQLite-online-backup implementations; UIs at `gui.py:641-648` (LibraryWindow) and `gui.py:1416-1421` (LIBRARY tab / LibraryPanel).
- Fix: direction sound; it is an L-effort consolidation, correctly labelled as such.

### RE-SERV-170 — VERIFIED-DRIFT
- Citations: `setup_guide.py:9 STEPS`, `library_panel.py:16 GUIDE` + standalone window `:520-561`, `library_gui.py:416-424 guide()` prose, `gui.py:1685 _open_ps2_setup_guide` — all exist; three distinct guide texts confirmed.
- Mechanism (corrected limb): the standalone panel window **is** unreachable in the shipped app — `_guide()` at `library_panel.py:516-519` delegates to `app._open_ps2_setup_guide` whenever present, and the panel's only instantiation (`gui.py:1416`) passes the app that defines it. **But** the finding's "both layers independently decide to auto-show a guide on first run" is overstated: the panel's auto-show is explicitly guarded at `library_panel.py:84-85` — `if not hasattr(app, "_open_ps2_setup_guide"): self.after_idle(self._show_first_run_guide)` — so in the integrated app the panel never auto-shows; only the app layer (`gui.py:1661-1675`, status in config `ps2_setup_guide_status` :1677-1682, :3539-3541) does. The doubled first-run *state* (panel `library/first_run_complete` marker :70, written only by the standalone window's Finish at :551-552, vs the config key) is real but inert in production for the same reason.
- Verdict rationale: core claim (3 guides, dead standalone window + GUIDE, doubled state) verified; the independent-auto-show limb corrected above.
- Fix: deletion list as filed remains sound (the guard at :84-85 goes with the machinery it guards).

### RE-SERV-171 — VERIFIED
- Citations: `library_catalog.py:266 scan_detailed`, `game_library.py:227 Library.installed`, `library_index.py:109 read_index`, `ps1_index.py:88 scan` — all read; four independent walkers/contracts as described (tree walk / top-level CD-DVD-POPS + index overlay / re-stat of catalogue rows only / POPS+EMBER walk).
- Mechanism: limits confirmed — `scan_detailed` default `max_entries=10_000` with cap `MAX_SCAN_ENTRIES = 100_000` (:27, the finding's "100,000" is the cap, not the default), `installed` unbounded-with-cancel, `read_index` cap 10_000, `ps1_index` cap 10_000. Result types differ as filed.
- Fix: sound as a direction; L/Med correctly labelled.

### RE-SERV-172 — VERIFIED
- Citations: `disc_identity.py:66 _IsoReader` (`root:100`, `find:120`, `extent:86`, `inspect:153`) and `ps1_vcd.py:20 VcdImage` (`root:63`, `extent:71`, `find:80`, `path_extent:104`) both exist with the cited shapes.
- Mechanism: spot-read both — disc_identity verifies both-endian fields (`_both_endian` :60-63), budgets reads/records; ps1_vcd's `extent` is little-endian-only and its limits are `MAX_DIRECTORY = 16 MiB` / `MAX_BOOT = 64 MiB` (:16-17) vs disc_identity's `MAX_DIRECTORY_BYTES = 1 MiB` / `MAX_CNF_BYTES = 16 KiB`. Sector mapping difference (2352-byte raw + header, `ps1_vcd.read` :44-60) is an adapter difference, as filed.
- Fix: shared record-walking over a `read(offset,length)` callable — justified (one full copy removed).

### RE-SERV-173 — VERIFIED-DRIFT
- Citations: grep census re-run — expressions found at `art_fetch.py:32 _OPL_ID`, `disc_identity.py:21 SERIAL` + `:22 FILENAME_SERIAL`, `game_library.py:163` inline `re.fullmatch`, `library_catalog.py:30 SERIAL_PREFIX`, `ps1_index.py:30 _PS1_NAME_ID`.
- Mechanism (count corrected): that is **six expressions in five files**, not five expressions — disc_identity holds two. PS2 spellings are five (the four outside disc_identity plus both of disc_identity's counted as the canonical pair), so the body's "the four PS2 expressions" undercounts by one. Substantive divergences verified in the patterns themselves: `SERIAL` is unanchored + `re.I` (used via `fullmatch` at :179, and case-insensitive unlike the others), `FILENAME_SERIAL` accepts `_`/`-`, `repair_cover`'s inline form accepts `_` only on an already-uppercased string, `SERIAL_PREFIX` requires a trailing separator, PS1 makes the dot optional. Duplicate `_`↔`-` conversions confirmed (`serial_forms` art_fetch.py:56-62; `.replace("-", "_")` at art_fetch.py:58 — the finding also cites library_catalog.py:225, which I did not individually re-read).
- Fix: import the disc_identity canonical pair in art_fetch/game_library; leave SERIAL_PREFIX and the PS1 pattern — sound.

### RE-SERV-174 — VERIFIED
- Citations: `game_library.py:132 repair_cover`, `library_catalog.py:120 _artwork_palette_image` + `:548 write_art` read in full.
- Mechanism: divergence exactly as filed — repair_cover always `convert("RGBA")` → `thumbnail((512,512))` → FASTOCTREE, saves via temp + `os.replace` (library) and temp + `publish_new` (ART copy); `_artwork_palette_image` uses FASTOCTREE only for real transparency, MEDIANCUT on RGB otherwise; `write_art` writes via `xb` directly. Limits differ: 16 MB / 16 MP (repair_cover :139/:146) vs 10 000 px per side / 50 MP (write_art :565). Both carry the FASTOCTREE/RGBA explanatory comment (:151-153 vs :130-132).
- Fix: sound.

### RE-SERV-175 — VERIFIED
- Citations: inline `"{size}:{mtime_ns}:{ctime_ns}"` at `game_library.py:256` (installed) and `:477` (Compatibility.check); `disc_identity.fingerprint()` at `disc_identity.py:37-45` (`dev:ino:size:mtime_ns[:ctime_ns]`, ctime omitted on Windows with the comment quoted in the finding); `library_index.py:173` emits the game_library format again for `IndexedGame.stamp` while :164 compares the catalogue's stored fingerprint via `disc_identity.fingerprint(info)` — the two formats coexist in the same function, as filed.
- Fix: sound; stored-stamp one-time miss correctly noted in the finding.

### RE-SERV-176 — VERIFIED-DRIFT
- Citations: `.github/workflows/release.yml` = 606 lines, `release-on-main.yml` = 489 lines (wc-verified); same 5-target desktop matrix in both (:20-44 of release-on-main); `edge-build.yml` header confirms the reusable-workflow rationale verbatim.
- Mechanism (duplication verified, drift proof corrected): my recount with a >20-char non-trivial line threshold gives **96 identical lines** (finding says 90 — same order, threshold-dependent). Drift corrections: (1) `verify-version` genuinely has **no** counterpart in release-on-main — verified. (2) The Windows desktop-startup check **does** have a counterpart: release-on-main.yml:119-121 runs the identical step (`Verify fresh-profile Windows desktop stays open` → `tools/check_desktop_startup.py dist/PS2Servers.exe`) inside its build job; the finding's claim of no counterpart is wrong. (3) The AppImage row at release-on-main:333-334 is guarded by `if [ -f PS2Servers-x86_64.AppImage ]`, and no step in that workflow (nor any artifact it downloads — uploads are per-matrix-asset only) can produce that file, so the row can never actually be emitted: it is dead conditional code, not a download table advertising an artifact users will look for.
- Verdict rationale: duplication + the verify-version drift are real; two of the three drift examples are overstated/wrong as corrected above.
- Fix: reusable-workflow extraction remains sound.

### RE-SERV-177 — VERIFIED
- Citations: census re-run — `git ls-files docs/` = **34 `.md`** and **22 `.html`**, exactly as filed. Overlapping pairs confirmed on disk for EDITIONS/editions, EDGE/edge, SMB/smb, NFS/nfs, HTTP/http, OPENWRT/openwrt, PROTOCOL-COMPATIBILITY/protocol-compatibility; the antivirus pair exists as `antivirus-transparency.md` + `antivirus.html` (different stems, same topic — as the finding's pairing implies). `tools/build_docs_index.py` only builds `docs/data/search-index.json` from the HTML; no MD→HTML generator found; `tests/test_edge_manual.py` exists as the one pinning test.
- Fix: direction sound (designate one source per topic); L/Med correctly labelled.

### RE-SERV-178 — VERIFIED
- Citations / per-symbol zero-caller census (whole repo, production + tests + conformance, including `wire.` attribute access and `import *` checks — none exist for these modules):
  - `native_cmac_name` (`smb2_server/smb2_crypto.py:186`) — **1 hit: its own definition.** No caller, no test, no string reference. Its "For logs" docstring is unfulfilled.
  - `STATUS_PENDING` (`smb2_server/smb2_wire.py:120`) — **1 hit: definition only.** No `wire.STATUS_PENDING` anywhere.
  - `FILE_SUPERSEDED` (`smb2_server/smb2_wire.py:181`) — **1 hit in smb2_wire.** Repo grep also finds a *separate, live* same-named constant in `smbv1_server/smbserver_opl.py:90`, used at :909 — a different module's definition, not a caller of the smb2_wire one. Dead as filed (the finding is scoped to smb2_wire.py:181).
  - `GLOBAL_CAP_DFS` (`smb2_server/smb2_wire.py:200`) — **1 hit: definition only.**
  - `PTYPE_BIND_NAK` (`smb2_server/smb2_srvsvc.py:28`) — **1 hit: definition only.**
  - Contrast set re-checked as filed: `blob_timestamp` and `lmv2_response` (`smb2_server/smb2_auth.py:221/:169`) are exercised by `tests/test_smb2_auth.py:28-29,158-162` — test surface, not dead. (`Share.relative`/`Share.resolve(must_exist=…)` not re-grepped individually; not part of the dead claim.)
- Fix: delete the five — sound.

### RE-SERV-179 — VERIFIED
- Citations: `smb2_server/smb2_crypto.py:173-184 _native_cmac`, docstring :15-22 read.
- Mechanism: probe is `win32 → _bcrypt_cmac()`, `linux → _openssl_cmac()`, **no other platform branch** — on macOS (`darwin`) `_NATIVE` caches `None` and every CMAC falls back to the pure-Python AES. The comment at :179-180 confirms the libcrypto-abort rationale. Docstring quantifies pure-Python AES at "under a megabyte per second" with the signature covering every byte of every READ reply. No startup banner or log names the backend (see 178: `native_cmac_name` has no caller).
- Fix: banner-only variant is the LESS-NOT-MORE choice and is sound; a CommonCrypto probe is untestable from this Linux sandbox.

### RE-SERV-180 — VERIFIED
- Citations: `smbv1_server/smbserver_opl.py:1200 h_trans2_find_first2`, `:1258 h_trans2_find_next2` read in full.
- Mechanism: FIRST2 packs `struct.pack("<HHHHH", sid, 1, eos, 0, 0)` — SearchCount literally 1 — and a single `_dir_entry_104` record; stores `(entries, 1)` as the cursor. NEXT2 packs count 1 and one record, advancing the cursor by 1. The full entry list is already materialised by `_list_dir` into `conn.searches`. `_dir_entry_104` stats per entry as filed. (Edge note: FIRST2 indexes `entries[0]` unconditionally — an empty directory would raise rather than return an empty result; see CANDIDATES.)
- Fix: sound; hardware re-test caveat in the finding is appropriate.

### RE-SERV-181 — VERIFIED
- Citations: `smbv1_server/smbserver_opl.py:1012 h_write_andx`, `:360-385 OpenFile.__init__` read.
- Mechanism: `h_write_andx` does `with open(of.path, "r+b") as w:` per request (:1035-1037 in current numbering) despite holding the `OpenFile`; the code itself documents why the read handle stays unbuffered (:368-375 comment). `OpenFile.size` is snapshotted once (`self.size = os.path.getsize(path)` :366, or the decoder's `uncompressed_size` :364) and **never updated by any write path** (grep of `.size` assignments: only :364/:366). `record_read` at :439 computes `expected = min(requested, max(0, of.size - offset))` from that stale size — the diagnostic consequence follows exactly as filed. No defeating guard found.
- Fix: sound, with the finding's own flush-coherence caution.

### RE-SERV-182 — VERIFIED
- Citations: `smbv1_server/smbserver_opl.py:268 Share.resolve`; the comment is at :298 in current numbering (finding says :297 — one-line drift, immaterial): `# Drop a leading "share/" if the client included it (some paths are share-relative already).`
- Mechanism: the full body read — `p = smb_path.replace("\\", "/").lstrip("/")`, then `os.path.realpath(os.path.join(self.root, p))` and the `_inside` check. No share-name comparison or strip exists anywhere in the function, and no caller pre-strips (callers at :813, :868, :1081, :1105, :1240, :1285 pass raw client names). A `\games\DVD\file.iso` path on share `games` therefore resolves under `<root>/games/…` exactly as the finding states.
- Fix: delete the comment (LESS-NOT-MORE default) — sound.

### RE-SERV-183 — VERIFIED
- Citations: `udpfs_server/ps2servers_core.py:71-88 classify_profile` read.
- Execution: brute-forced the extracted logic over all 4096×4096 (discovery, first-data) pairs — **third-branch hits: 0**. Proof sketch matches the finding: reaching branch 3 with `first == modulo` requires branch 1's side condition to have failed, i.e. `first == 0 and discovery == 0`, which forces `modulo == 1 ≠ 0` — contradiction.
- Fix: delete branch 3 — sound.

### RE-SERV-184 — VERIFIED
- Citations: import list `udpfs_server/ps2servers_core.py:18-48`; `MAX_SEND_RETRIES` at :24, `_widen_send_buffer` at :46.
- Execution: occurrence census over the file — `grep -c` returns **1 for each name** (the import line only); both names exist in `udpfs_server/udpfs_server.py` (they are real exports, just unused here). No `getattr`/string/dynamic use in the file.
- Fix: remove the two names — sound.

### RE-SERV-185 — VERIFIED
- Citations: `ps2servers_core.py:639-663 main()` Modulo fast path, `:107-120 AutoUdpfsServer.__init__`, `:306-335 _handle_discovery` Modulo branch — all read.
- Mechanism: `main()` constructs a plain `UdpfsServer(single_port=True, modulo_compat=True)` and returns when `args.protocol_mode == PROFILE_MODULO` (:639ff); the `AutoUdpfsServer` construction at :664 is reached only for auto/standard and passes `protocol_mode=args.protocol_mode`, which by then cannot be modulo. Grep confirms no other production construction of `AutoUdpfsServer` exists. Auto's Modulo branch **is** exercised by tests — `tests/test_udpfs_live_discovery_handoff.py:376ff` builds the class via `__new__` and sets `protocol_mode` directly — exactly the "only a programmatic construction or a test can reach it" scope the finding claims. The `_last_disc` limb verified: `_handle_discovery:309` reads it via `getattr(sess, '_last_disc', None)` and :318 sets it, but `_init_compat` (:122-138) never initialises it — first use is safe only because of the `getattr` default.
- Fix: second option (delete Auto's Modulo branch, base owns Modulo) is the smaller deletion, as filed.

### RE-SERV-186 — VERIFIED
- Citations: `ps2servers_core.py:242 _schedule_fallback`, sole call site :358.
- Mechanism: `_schedule_fallback` unconditionally creates and starts a new daemon `threading.Thread` per call (:262-265), which sleeps `fallback_interval` then re-checks session identity, `handshake_generation`, profile, `first_data_seen`, `rx_streaming`, `fallback_sent` — all but the latest generation exit as no-ops, as filed. Call site :358 is reached on every non-streaming, non-standard discovery in auto mode (streaming discoveries return earlier at :340-358's first block; standard mode returns before it). Thread churn is therefore proportional to discovery rate on that path.
- Fix: single pending timer / deadline-in-select — sound; the receive loop's periodic wakeup exists as claimed (INFERRED limb on broadcast rate is the finding's own, correctly labelled there).

### RE-SERV-187 — VERIFIED
- Citations / per-symbol census (whole repo, `grep -rn tx_start_seq`):
  - In `udpfs_server/udpfs_server.py`: :845 session-proxy property definition, :1690 assignment, :2966 assignment, :3001 assignment, :3208 `Session.__init__` assignment — **5 lines, all writes/definitions, zero reads in the file**, as filed.
  - Outside the file: `ps2servers_core.py:151` assignment (the one the finding flags), plus test/tool *writes* only — `tests/test_udpfs_live_discovery_handoff.py:140` (`sess.tx_start_seq = 10`), `tests/test_udpfs_resent_request.py:58` and `tools/replay_neutrino_udpfs.py:188` (constructor kwargs setting it to 0). **No read of the value exists anywhere in the repo** — no `getattr`, no comparison, no return. The limb is stronger than filed, not weaker.
- Fix: delete property + field + assignments (including the ps2servers_core.py:151 line and the two test/tool kwargs, which would otherwise set a dead attribute harmlessly) — sound.

### RE-SERV-188 — VERIFIED-DRIFT
- Citations: `udpfs_server/pathguard_selftest.py:190-192` UNC skip, `:237` unconditional PASSED line, `:155-185` separator-root section; `multiclient_selftest.py` and `compression_selftest.py` whole-file.
- Execution: ran both runnable selftests in the `/tmp` copy on this POSIX sandbox — `pathguard_selftest.py` printed `[unc] skipped (Windows ntpath semantics only)` and then **`ALL UDPFS PATH-GUARD TESTS PASSED`** (exit 0); `compression_selftest.py` also green. `multiclient_selftest.py` not run (UDP sendto blocked in this sandbox — BLOCKED-ENV for execution only; its content was still grep-censused).
- Mechanism: write-opcode census re-run — `grep -c 'BWRITE|WRITE_REQ|WRITE_DATA'` = **0 in all three selftests**, as filed. Correction on the separator-root limb: with root `/` the section's assertions are not wholly vacuous — they still assert exact join results (`/PS2/CD/game.iso`, top-level file, root itself, `..` clamping) — but any *containment/escape* assertion in that section is indeed un-falsifiable on POSIX, since nothing is outside `/`. The genuinely load-bearing containment cases live in the separate local-containment section (tmp root), which does run on POSIX; the skipped-on-POSIX surface is the UNC section (and the Windows-reinterpreted-name cases), which is the finding's main point and stands.
- Fix: SKIPPED accounting in the summary — sound.

### RE-SERV-189 — VERIFIED
- Citations: `nfs4_server/nfs4_server.py:261-316 _Handles`, caps at :54 (`MAX_CONNECTIONS = 32`), :820-821 (dir cache clear at 64), :868-869 (client eviction at 256) — all read.
- Mechanism: `_Handles.handle()` (:276-284) allocates a fresh number per distinct comps tuple and inserts into both dicts; the only removals are `removed()` (:302) and `renamed()` (:307), invoked only from the server's own REMOVE (:1002) / RENAME (:1037) handlers. No cap, no eviction, no TTL anywhere in the class.
- Execution: in the `/tmp` copy, allocated handles for 30 distinct paths via `_Handles.handle()` — table held **31 entries (30 + root) in both `paths` and `numbers`**, matching the finding's executed count, with no reclamation path.
- Fix: cap + oldest-first eviction reusing the :868-869 pattern — sound (STALE is a handled client error).

### RE-SERV-190 — VERIFIED
- Citations: `nfs4_server/nfs4_server.py:683 _changed`, `:958 _op_write` (calls `_changed()` with no comps), `:768 _op_read` (cached handle discarded when `cached[0] != self.generation`), `:797 _listing` (cache valid only for current generation) — all read.
- Mechanism: `self.generation` is a single server-wide counter; **every** `_changed` caller bumps it — including callers that do pass comps (:926, :973, :986, :1003, :1038), which additionally clear `dir_cache`. `_op_write` (:941-959) has `comps` in hand (`self._need(s.cfh)`) and still calls `_changed()` bare. Consequence as filed: any write invalidates every connection's cached read handles (up to 4 per connection, :776-781) and every listing cache slot on next use. Correctness is preserved; only cache warmth is lost — the finding says exactly this.
- Fix: per-path generation dict — sound; `_op_write` already knows its comps, as filed.

---

## Tally

| Verdict | Count | Findings |
|---|---|---|
| VERIFIED | 26 | 161, 162, 163, 164, 165, 166, 167, 168, 169, 171, 172, 174, 175, 177, 178, 179, 180, 181, 182, 183, 184, 185, 186, 187, 189, 190 |
| VERIFIED-DRIFT | 4 | 170 (auto-show limb overstated — panel auto-show is guarded off in the integrated app), 173 (six expressions in five files, not five expressions; five PS2 spellings, not four), 176 (startup-check counterpart exists; AppImage row is a dead conditional, never emitted; identical-line count 96 by my threshold vs 90 filed), 188 (separator-root assertions not wholly vacuous — exact-join checks still run; containment limb un-falsifiable on POSIX as filed) |
| WEAKENED | 0 | — |
| REFUTED | 0 | — |
| BLOCKED-ENV | 0 | (multiclient_selftest execution only, within 188; its grep census was completed) |

Total: 30 findings.

## CANDIDATES (new defects, separate from the findings above)

- **CAND-H-1 — `h_trans2_find_first2` crashes on an empty directory.** `smbv1_server/smbserver_opl.py:1243-1244`: `name, lp, sz = entries[0]` with no emptiness check after `_list_dir` succeeds. An empty (or all-filtered) games subfolder raises `IndexError` inside the handler instead of returning an empty/end-of-search reply. Evidence: handler body read in full during RE-SERV-180 verification; no `if not entries` guard exists between the `_list_dir` try/except and `entries[0]`. Severity/effort not assessed — candidate only.
- **CAND-H-2 — Same `_last_disc` getattr pattern in the base server.** `udpfs_server/udpfs_server.py:1627/1634` reads/writes `sess._last_disc` via `getattr` default exactly as `ps2servers_core.py:309/318` does (found during the RE-SERV-185 grep). Not a defect on its own (the getattr makes it safe), but any fix for 185 that initialises the attribute in `_init_compat` should be mirrored or consciously not mirrored in the base class.
- **Fix-hazard note (not a defect):** RE-SERV-166's proposed switch of `award_achievement` to `decode_response` cannot be done literally — `decode_response` (`launcher/ra_sets.py:40-42`) raises unless `Success is True`, while award's already-unlocked path depends on reading a Success-false response's `Error` field. The fix needs a non-gating decode variant.

---

## Part I — RE-SERV-191–216 (performance / dead code / clutter)


- **Pin confirmation:** `git -C ~/workspace/audit-clones-2026-11-13/PS2-Servers rev-parse HEAD` → `c5f021c4d38698860f56b03498adc261dbb8d8dd` — matches the assigned pin exactly. All reading was in that clone (read-only); executions ran in `/tmp/vI` copies only.
- **Method:** refute-first — every cited file/symbol/asset grepped repo-wide; measurement claims re-measured; zero-caller claims grepped across code + tests + scripts; mechanism claims re-derived from source, hunting for defeating guards/caches first.
- **Environment note:** sandbox blocks UDP — the Go `internal/udpbd` package tests run in a `/tmp` copy fail with UDP loopback `i/o timeout`s. That is BLOCKED-ENV for those tests only and is not evidence for or against any finding here.

---

### RE-SERV-191 — VERIFIED
- **Citations:** All exist. `nfs4_server/nfs4_server.py:728-733` `_op_putfh` does `s.cfh = self.handles.lookup(fh)[1]` (keeps comps, discards element [0], the number); `_Compound` at :336-343 has `__slots__ = ("cfh", "sfh", "conn")` storing comps only; `_op_read` :761-766 and `_op_readdir` :825-835 both re-derive via `self.handles.handle(comps)`.
- **Mechanism:** `_Handles.handle` (:276-285) takes `self.lock` and, on a miss, inserts into both `self.numbers` and `self.paths` — i.e. a locked, potentially mutating lookup-or-insert on every READ/READDIR, exactly as claimed. No defeating guard: neither op checks a stored number first, because none is stored. `lookup()` already returns `(number, comps)`, so the number was in hand at PUTFH.
- **Execution:** n/a (code re-derivation; mechanism is deterministic from source).
- **Fix:** Sound and LESS-shaped: carry `(number, comps)` in the compound state; `handle()` then runs only in GETFH. No new subsystem.

### RE-SERV-192 — VERIFIED
- **Citations:** `native/ps2servers-edge/internal/udpbd/server.go` — `seen map[string]bool` declared :120, created :151; the only writes/reads in the whole file are :206 (`peers := len(s.seen)`), :350-351 (`first := !s.seen[...]`; insert). Repo grep for `seen` in the package returns no delete, no expiry, no reset.
- **Mechanism:** `statusReply` sets `Sessions: uint16(peers)` from `len(s.seen)` (:220) — a monotonically increasing ever-seen count, not a live-session count. Insertion is driven by any UDP INFO sender's source address, unauthenticated. Both limbs (unbounded growth + false status semantics) hold.
- **Execution:** n/a (the package's UDP tests are BLOCKED-ENV in this sandbox; the claim is a source census, which I reproduced).
- **Fix:** Sound: remove `seen` from `statusReply`; bound/expire it if kept for the first-contact log line.

### RE-SERV-193 — VERIFIED
- **Citations:** Go — `operations.go:282` (`prepareDirectory` → `compression.ListVirtualCD`), called from directory OPEN at :336; `resolveVirtualCD` :234-251 → `FindVirtualCD`, called at :344, :383, :781; `ListVirtualCD` `chd_cuebin.go:406-475` calls `readCDTrackTable` (:460) + `table.layout()` per `.chd`; `FindVirtualCD` :476-539 calls `readCDTrackTable` (:520). Python — `udpfs_server.py:1745-1790` (`_prepare_directory_entries` → `_get_compressed_stat` per compressed entry, :1760), `_get_compressed_stat` :1388-1405 → `get_compressed_info` :390-479 (opens the file; CD CHDs fall through to `exact_cd_iso_size`), `compressed_iso/chd.py:210-240` (`exact_cd_iso_size` opens again and walks `read_track_table`).
- **Mechanism:** Hunted for a track-table cache first: the Go compression package's only caches are decompressed-block/hunk caches (`image.go` block cache; `chd_cuebin_enabled.go` hunk cache) — no parsed-track-table/layout cache anywhere. Python side: no memoisation of `get_compressed_info` (grep for cache/memo near it returns only the unrelated decompression-block cache). Both implementations re-parse per listing/OPEN/GETSTAT as claimed.
- **Execution:** n/a (call-graph re-derivation in both languages; no timing claimed by me).
- **Fix:** Sound; the proposed invalidation key (`os.SameFile`+size+modtime in Go, `(path, st_mtime_ns, st_size)` in Python) already exists in the respective codebases. One small cache justified by a measured-by-inspection hot path — passes LESS-NOT-MORE.

### RE-SERV-194 — VERIFIED
- **Citations:** `native/ps2servers-edge/internal/smb/handlers.go` — `entries` is seeded with `"."` and `".."` (two appends before the names loop), then `eos := uint16(0); if len(entries) == 1 { eos = 1 }`.
- **Mechanism:** `len(entries) >= 2` by construction on every path reaching the check (early returns cover resolve/stat/readdir failures). The branch is unreachable and `eos` is always 0 on FIND_FIRST2, including for an empty directory. No defeating guard exists — the seeding is unconditional.
- **Execution:** n/a.
- **Fix:** Sound: delete the dead branch; derive EndOfSearch from the cursor/last-entry condition as `findNext2` does.

### RE-SERV-195 — VERIFIED
- **Citations:** `image.go:138`: `func Open(path string) (*Image, error) { return OpenCached(path, DefaultCacheBlocks) }` — a one-line wrapper, as cited.
- **Mechanism (zero-caller census):** Whole-module grep: every non-test reference to compression `Open(` is absent; production open paths are `compression.OpenCached` and `compression.OpenRaw`, both from `internal/udpfs/blockdev.go:396,402`. All `Open(` hits in the compression package outside `image.go` are in `*_test.go` files (`image_test.go`, `sizeof_test.go`, `chd_enabled_test.go`, `chd_disabled_test.go`, `corrupt_index_test.go`). Package is `internal/`, so no external consumer can exist.
- **Execution:** Census reproduced by grep over the whole Edge module.
- **Fix:** Sound deletion; tests call `OpenCached(path, DefaultCacheBlocks)`.

### RE-SERV-196 — VERIFIED
- **Citations:** `service.go:67-82` — `maxServiceLogLines = 500` with the stated rationale ("bounds one snapshot, and therefore the memory a request can make this process hold") and `clampLogLines`. `openwrtManager.ServiceLogs` (:173-176) ignores its `lines` parameter entirely: `return readLogCommand("logread", "-e", "ps2servers-edge")`. `systemdManager.ServiceLogs` (:208-212) clamps via `"-n", strconv.Itoa(clampLogLines(lines))`. Caller `api.go:284-295` streams every returned line into the response.
- **Mechanism:** Exactly as claimed — the bound exists, is honoured on systemd, and is not applied on OpenWrt. (Minor wording note: the finding's benefit says "all three managers"; the third, `genericManager`, returns an error and no lines, so the fix concerns the two log-returning managers. This does not weaken the defect.)
- **Execution:** n/a.
- **Fix:** Sound and minimal: truncate to `clampLogLines(lines)` inside the OpenWrt manager, reusing the existing clamp.

### RE-SERV-197 — VERIFIED
- **Citations:** `sessionstats.go:247-266` (`Parse`), `:67-74` (`WithClock`, `WithLimits`, both commented "for tests").
- **Mechanism (zero-caller census):** Repo-wide grep over all `.go` files: `sessionstats.Parse` is called only from `internal/smb/session_summary_test.go:100`, `internal/udpbd/session_summary_test.go:93`, `internal/udpfs/session_summary_test.go:43` (plus the package's own test); `WithClock`/`WithLimits` only from `internal/sessionstats/sessionstats_test.go`. The other `Parse(` hits in `main.go` are `flag.FlagSet.Parse`, a different symbol. Both mutators write `Stats` fields with no synchronisation, as claimed.
- **Execution:** Census reproduced by grep.
- **Fix:** Sound: move the helpers into test files (in-package tests can set unexported fields directly).

### RE-SERV-198 — VERIFIED
- **Citations / per-symbol census:**
  - `API.log` — field at `api.go:22`, assigned at `webui.go:206` (`log: s.cfg.Log`) and in a test literal (`webui_test.go:31`). Grep for reads of `a.log` in `api.go` returns none — handlers use `a.logBuffer`, `a.manager`, `a.logLines`, `a.logClients` (the `a.log*` hits are those distinct fields, not `a.log`).
  - `Server.GeneratedPassword` — `webui.go:106-107`; repo-wide grep returns only the definition. `Serve` already logs the generated password itself.
  - `Server.Close` (webui) — `webui.go:293-298`; no caller of the webui server's `Close` exists. The `server.Close()` calls in `main.go:334,436` are on the udpbd and smb server types, not webui; the webui path in `main.go` constructs `srv` via `webui.New(...)` and calls only `srv.Serve(ctx)`.
- **Mechanism:** All three items dead as claimed; I specifically checked the two `main.go` `Close()` calls as candidate defeaters and they are different types.
- **Execution:** Census reproduced by grep.
- **Fix:** Sound deletions, no replacements.

### RE-SERV-199 — VERIFIED
- **Citations:** `root.go:235-259` `List`: resolves the directory (:236), then per entry calls `r.Resolve(child, false)` (:247) and `os.Stat` on the result. `Resolve` (:66-97) runs `filepath.EvalSymlinks` plus a `filepath.Rel` containment check.
- **Mechanism:** Per-entry full resolution + stat on top of `ReadDir`, exactly as claimed; the only production caller is `internal/udpfs/operations.go:326` (the directory-listing path). No fast path for non-symlink entries exists. The finding itself labels the cost INFERRED/Med — fairly.
- **Execution:** n/a (no benchmark claimed or run).
- **Fix:** Sound in shape (Lstat first, full `Resolve` only for symlinks); correctly flagged Med risk — symlink semantics must be re-checked against `root_test.go`.

### RE-SERV-200 — VERIFIED
- **Citations:** `launcher/servers.py:70` `binary_rel` field; `:87-93` `resolve_binary`; `:96-111` native branches of `is_available`/`launch_command`; `launcher/serve.py:156-159` native-server rejection.
- **Mechanism (census):** All 8 `REGISTRY` ServerDefs declare `runtime="python"` (:386, 435, 451, 467, 567, 631, 658, 717). Repo-wide grep for `binary_rel`/`resolve_binary` returns only the declaration and its own two methods — no ServerDef populates `binary_rel`, no test exercises the native branch. The mechanism is unreachable as claimed.
- **Execution:** Census reproduced by grep.
- **Fix:** Sound deletions; keeping the `runtime` field itself is fine since `serve.py` reads it.

### RE-SERV-201 — VERIFIED
- **Citations:** `launcher/virtual_exfat.py:39-45` `upcase_table()` loops `for c in range(65536)`; `__init__` (:71-95) calls it and then RLE-encodes all 65,536 entries in a second loop; `name_units` (:31-37) encodes the name as UTF-16-LE twice per call.
- **Mechanism / measurement (my execution, in `/tmp`):** `len(upcase_table()) == 65536` confirmed in code and at runtime. My timings on this sandbox: `upcase_table()` alone ≈ 72 ms; `VirtualExfat` construction over a one-file folder ≈ 176 / 121 / 88 ms across three runs — the same order as the finding's claimed ~127-167 ms construction and ~180 ms table-only (my table-only figure is lower, hardware-dependent; the fixed-cost mechanism and its dominance of construction are confirmed). Table and encoded form are pure functions of the spec, identical per instance.
- **Execution:** Timed by me (see above); counts reproduced.
- **Fix:** Sound: compute once at module level (lazily) and reuse. Pure deletion of repeated work.

### RE-SERV-202 — VERIFIED-DRIFT
- **Citations:** `launcher/raw_storage.py:326-367` `_metadata()` (Windows: PowerShell `Get-Disk`/`Get-Partition`/`Get-Volume`, 20 s timeout at the `subprocess.run`; Linux: `lsblk` tree walk); `open_raw_device` :284-311; `mount_warning` :409-427; `list_devices` :392-407.
- **Mechanism:** The duplicate-enumeration claim holds, but the count is understated for one path. `_metadata()` call sites in one `open_raw_device`: (1) `_exclusive_target(path)` at :289 (exclusive only), (2) on Linux-exclusive, a **second** `_exclusive_target(path)` recheck at :299 after unmounting, (3) `mount_warning(path)` at :310, which calls `_metadata()` again (:413). Corrected counts: non-exclusive open = 1 enumeration; Windows-exclusive = 2; **Linux-exclusive = 3**, not "up to twice". The finding's body describes only calls (1) and (3) and misses the :299 recheck.
- **Execution:** Call-graph re-derivation from source (no Windows host here to time PowerShell; cost claim rests on the code's own 20 s timeout, as the finding states).
- **Fix:** Sound, and worth slightly more than filed: fetch metadata once per open and pass the record into both the recheck and the mount-warning computation.

### RE-SERV-203 — VERIFIED
- **Citations:** `launcher/session_stats.py:1-16` header: "Shared by every server (HTTP, SMBv1, UDPFS, UDPBD), so one parser reads all of their lines."
- **Mechanism:** Actual `SessionStats(...)` producers (repo grep, non-test): `http_server.py:1183` ("http"), `smbv1_server/smbserver_opl.py:1753` ("smbv1"), `smb2_server/smb2_server.py:1790` (`"smbv%d" % args.smb_version` → smbv2 **and** smbv3, neither named in the header), `udpfs_server/ps2servers_core.py:588` ("udpfs"), `udpbd_server/udpbd_server.py:449` ("udpbd") — six producers, two omitted. Conversely, the NFS (`servers.py:624-652`) and RetroAchievements (`:712-746`) ServerDefs have no `session_summary` field at all, so "every server" also overclaims. Both limbs hold.
- **Execution:** Census reproduced by grep.
- **Fix:** Sound: correct the parenthetical to (HTTP, SMBv1, SMBv2, SMBv3, UDPFS, UDPBD). Doc-only.

### RE-SERV-204 — VERIFIED-DRIFT
- **Citations / per-symbol census:**
  - `derive_watch` (`launcher/ra_sets.py:204`): repo-wide grep returns only the definition; all callers use `derive_direct_watch`. **Dead — confirmed.**
  - `import sys` (`launcher/achievements.py:13`): AST census by me — `sys` is never loaded as a name/attribute anywhere in the file. **Unused — confirmed.**
  - `_NoRedirect` import (`launcher/ra_set_client.py:7`): AST census — never used inside `ra_set_client.py`. **But** it is a load-bearing re-export: `tests/test_ra_sets.py:8` does `from launcher.ra_set_client import RASetClient, _NoRedirect` and uses it at :99. Deleting the import as filed would break that test import unless the test is switched to `launcher.ra_http` (where the class is defined, `ra_http.py:21`).
  - `ENDPOINT` attributes: the finding has the two clients **swapped**. It claims the set client's `ENDPOINT` (`ra_set_client.py:13`) "is referenced nowhere" and the session client's (`ra_session_client.py:56`) is referenced "only by a test assertion". My grep shows the opposite: the only test reference, `tests/test_ra_sets.py:84` (`request.full_url == client.ENDPOINT`), uses a **`RASetClient`** (constructed at :77); the **session** client's `ENDPOINT` has zero references anywhere. Both attributes remain test-only-or-less surface, so the clutter conclusion stands, but the per-client attribution and the keep/delete recommendation attached to it are reversed.
- **Mechanism:** Dead-code substance confirmed for all four items; two details corrected above.
- **Execution:** Grep + AST census reproduced by me.
- **Fix:** Mostly sound with corrections: delete the alias and the `sys` import; delete the **session** client's `ENDPOINT` outright; the set client's `ENDPOINT` is the one with a test contract; and the `_NoRedirect` deletion must be paired with updating the test's import to `launcher.ra_http`.

### RE-SERV-205 — VERIFIED
- **Citations:** `validate_watchlist` call sites confirmed at `ra_sets.py:200` (in `derive_direct_watch`), `ra_watchlist.py:101` (`WatchSession.install`), `ra_receiver.py:111` (`Receiver.complete`), `ra_evaluator.py:73` (`Evaluator.__init__`).
- **Mechanism:** Traced the owned chain: `EvaluatedReceiver.complete_set` (ra_evaluated_receiver.py:56) → `derive_direct_watch` (validation #1) → `EvaluatedReceiver.complete` (:111) constructs `Evaluator(...)` (validation #2, :130) and calls `receiver.complete(...)` → `Receiver.complete` validates again (#3, ra_receiver.py:111) → `session.install(...)` → `WatchSession.install` validates a fourth time (#4, ra_watchlist.py:101) — all over the same immutable `bytes` object within one activation. No later pass can fail where an earlier one in the same chain passed.
- **Execution:** Call-chain re-derivation from source.
- **Fix:** Sound: validate at derivation and at the IPC/trust boundary only; the fix correctly preserves the cross-process check.

### RE-SERV-206 — VERIFIED
- **Citations:** `launcher/ra_web.py:142-145` `wait_dirty` waits on the single `RAState._dirty` event then clears it; `:507` the `/events` handler calls `owner.state.wait_dirty(timeout=1.0)`, discards the return value, and unconditionally serialises `to_dict()` and writes it; `:567` `RAWebServer.close()` pokes `self.state._dirty.set()`.
- **Mechanism:** Exactly as claimed: one shared event for all clients (first waker clears it for everyone), result ignored, so every client gets a full-state push on the ~1 s timeout regardless of change. No per-client revision tracking exists anywhere in the file (grep for `_dirty`/`wait_dirty` returns only the sites above plus the `set()` calls in state mutators).
- **Execution:** n/a.
- **Fix:** Sound; the LESS option (delete `wait_dirty`/`_dirty`, keep the honest 1 s poll-push) matches current observable behaviour.

### RE-SERV-207 — VERIFIED
- **Citations / per-symbol zero-caller census (whole repo, code + tests + scripts + docs):**
  - `AVAST_GEN_PUBLIC_NOTE` (`launcher/release_metadata.py:73-79`): exactly one hit — its own definition. **Zero callers.**
  - `theme_assets.photo()` (`launcher/theme_assets.py:68-76`): zero callers; both real call sites use `photo_fit` (`launcher/asset_skin.py:20`, `launcher/gui.py:1523`). **Zero callers.**
  - `LauncherApp.on_close()` (`launcher/gui.py:3562-3563`): no caller of this method (other `on_close` hits are unrelated parameters/attributes in `setup_guide.py`, `achievement_viewer.py`, `ra_web.py`). **Zero callers.**
  - `LauncherApp._quit_from_tray()` (`launcher/gui.py:3774-3775`): exactly one hit — its own definition. **Zero callers.**
  - dependency_panel wrappers (`launcher/dependency_panel.py:43-48`): `summarize_status_for_ui()` is a one-line forward to `optional_deps.summarize_statuses()`; `_status_bits()` is a one-line forward to `summarize_status_for_ui()`; used at :53 and :60. **Double wrapper confirmed.**
- **Mechanism:** All five items as claimed.
- **Execution:** Census reproduced by repo-wide grep per symbol.
- **Fix:** Sound deletions; inlining `optional_deps.summarize_statuses()` at the two use sites.

### RE-SERV-208 — VERIFIED
- **Citations:** `launcher/game_library.py:227-286` (`Library.installed`); `launcher/library_catalog.py:94-103` (`_scan_path`: per-component `lstat` + `resolve(strict=True)`), `:178-218` (`list_games`/`_current_identity`); `launcher/library_index.py:157-158` (`read_index` already `_scan_path`-validates).
- **Mechanism:** In `installed()` with an index: `_scan_path` runs per media directory (:241), per file (:250), again per result row building `available` (:262), and again per indexed game (:272) — on top of the pass `read_index` already performed. In `Catalog.list_games()`, `_current_identity` runs on every row up to the 10,000-row LIMIT, and for verified/conflict rows does a fresh `Path.stat()` + fingerprint build (:206-218). Both limbs re-derived as claimed.
- **Execution:** n/a (call-graph re-derivation).
- **Fix:** Sound in direction; correctly Med risk (the root-mismatch check must be kept, as the finding says).

### RE-SERV-209 — VERIFIED
- **Citations / my measurements:** `stat`: `launcher/assets/theme/LOGO.png` = **602,983 bytes** — matches the claimed 602,983 / "603 KB". PNG header dimensions read by me: **1229×1185** — matches. Theme-asset total: 602,983 + 12,166 + 11,694 + 12,266 = **639,109 B**, so LOGO is 94.3% of shipped theme bytes (claimed 94% of 639 KB ✓).
- **Mechanism:** Consumers confirmed: About tab `photo_fit(..., max_width=150, max_height=150)` (`gui.py:1523-1524`); Linux tray resized to 64×64 (`tray_linux.py:71-76`); release workflow AppImage icon `-resize 256x256` (`.github/workflows/release.yml:284`). Nothing displays it above 256 px. `photo_fit` (`theme_assets.py:78-92`) shrinks by integer `subsample` only, as claimed.
- **Execution:** Measured by me (stat + PNG IHDR parse).
- **Fix:** Sound: re-encode at 256×256, data-only change.

### RE-SERV-210 — VERIFIED
- **Citations / my measurements:** Source segment `gui.py:126-221` measured by me at exactly **10,223 characters / 96 lines** — matches the finding's script count precisely. Two `+=` appends in the same file at :208 and :210; sole display site :1656 (`text.insert("1.0", ABOUT_TEXT)`).
- **Mechanism:** Runtime mutation from outside the module confirmed: `launcher/main.py:136-139` appends `ADMIN_ABOUT_TEXT`/`UNIX_ABOUT_TEXT` to `gui.ABOUT_TEXT` at startup. The displayed text exists in no single file, as claimed.
- **Execution:** Segment measured by me (the runtime-concatenated string is 10,159 chars by my AST evaluation — the finding's figure is the source-segment count, which is the number it cited).
- **Fix:** Sound: trim in place, delete duplicated guide/card-help prose, no new docs mechanism.

### RE-SERV-211 — VERIFIED
- **Citations:** `launcher/optional_deps.py:50-68,139-190`; `launcher/dependency_panel.py:51-69,71-83`.
- **Mechanism:** `check_all()` (:189-190) is `[check_lz4(), check_libchdr()]` with no memoisation; `check_libchdr()` walks candidates, calls `_prepare_native_dir` (mutating PATH/DLL dirs) and `ctypes.cdll.LoadLibrary` via `_try_load_library` on every call. Call sites re-run it on panel creation (`refresh_status_async`), on "Check" (`refresh_status`), and again in Details (`show_libchdr_details` → direct `check_libchdr()`). Conversely `_known_path_dirs()` caches the PATH snapshot in `_PATH_DIRS` forever (:47, :58-67) — process-stale, as claimed (the finding fairly labels that consequence INFERRED).
- **Execution:** n/a (call-chain re-derivation).
- **Fix:** Sound: memoise after first successful probe with invalidation on the install path; drop the `_PATH_DIRS` cache.

### RE-SERV-212 — VERIFIED
- **Citations:** `packaging/openwrt/Makefile:4` `PKG_VERSION:=0.8.2`; `:21` `TITLE:=PS2 Servers Edge UDPFS server`; `:26-28` description "A small read-only UDPFS server…". Source of truth `launcher/release_metadata.py:15` `PRODUCT_VERSION = "0.9.4"` — a full minor release ahead.
- **Mechanism:** `tools/version_source.py` guards only `edge-build.yml` (`VERSIONED_WORKFLOWS = (EDGE_WORKFLOW,)`; `--check-sources` checks that list) — the Makefile is not covered, as claimed. The binary's actual scope is four subcommands (`udpfs`, `udpbd`, `smb`, `webui` — usage text and dispatch in `cmd/ps2servers-edge/main.go:32-42,113-125`), and read-only is the package config's choice (`packaging/openwrt/files/ps2servers-edge.config` sets `read_only '1'`; the binary defaults writable, main.go comment at the udpfs flags). Title/description are stale on both axes.
- **Execution:** n/a (file reads + grep).
- **Fix:** Sound: extend the existing version guard to the Makefile; update title/description to the four-subcommand scope.

### RE-SERV-213 — WEAKENED
- **Citations:** `.github/workflows/release.yml` and `release-on-main.yml` read in full by me.
- **Mechanism — core stands, two filed details are wrong:**
  - **Count:** `grep -c "continue-on-error: true" .github/workflows/release.yml` = **11**, not the claimed 13 (the finding's own line list names only 11: 146, 228, 235, 252, 269, 343, 361, 379, 388, 403, 420). The 5 occurrences in `release-on-main.yml` are confirmed.
  - **The self-negating pairing is misattributed:** in `release.yml`, the portable upload step (:403) has `continue-on-error: true` but **no** `if-no-files-found` at all, and the AppImage upload (:420) pairs `continue-on-error: true` with `if-no-files-found: ignore` — the opposite setting. The only `if-no-files-found: error` in the file (:471, source-archive upload) is on a step **without** `continue-on-error`. The claimed pairing does exist, but in the *other* workflow: `release-on-main.yml`'s "Upload portable release asset artifact" step pairs `continue-on-error: true` with `if-no-files-found: error`.
  - What remains true: every portable/AppImage build, stage, checksum, attestation and upload step in `release.yml` is `continue-on-error: true`, and the publish job's only check is `ls -la release-assets` plus fallback rows in the notes generator — so a broken portable/AppImage build produces no red signal. The defect is real; its sharpest cited example is not in the file/line given.
- **Execution:** Workflow files grepped and read by me (counts above are mine).
- **Fix:** Still sound (drop `continue-on-error` from one final existence-verification step per optional artifact; remove the self-negating pairing — in `release-on-main.yml`, where it actually occurs).

### RE-SERV-214 — VERIFIED
- **Citations / my census of every Go toolchain pin in CI/build files:**
  1. `native/ps2servers-edge/go.mod:3` — `go 1.22`, consumed via `go-version-file` by `.github/workflows/edge.yml:44` (test job) and `.github/workflows/edge-build.yml:228`.
  2. `.github/workflows/edge.yml:198` — parity job hardcodes `go-version: "1.25"`.
  3. `packaging/docker/Dockerfile.edge:2` — `FROM golang:1.23-alpine`, and the Dockerfile runs `go test ./...` at image-build time with that third toolchain (line 7).
  No other pins exist (grep for `go-version`/`golang:`/Go version literals across workflows, packaging and tools returns only these).
- **Mechanism:** Three different compilers produce the unit-test evidence, the parity evidence and the shipped/container binary, exactly as claimed; nothing in-tree states the divergence is intentional.
- **Execution:** Census reproduced by grep.
- **Fix:** Sound: point the parity job at `go-version-file`; pin or document the Dockerfile divergence.

### RE-SERV-215 — VERIFIED
- **Citations / my measurements:** `stat`: both `ps2serversgithubbanner.png` and `docs/assets/ps2servers-banner.png` = **571,581 bytes**. `md5sum`: both `7d13770364c4192c47463f2ba0ee1ff0` — identical to the finding's claimed md5. `sha256sum` (mine): both `070f6dae62b290cef97cb16678d8acf2a8bb1052362d6931fcddef29dec3b28c`. `git ls-files -s`: both are the **same git blob** `b1469e3078d279a7515bcd646dd7df93ddf0cacf`. Byte-identity is total.
- **Mechanism:** The docs copy is the referenced one — `og:image`/`twitter:image` tags across `docs/*.html` point at `assets/ps2servers-banner.png`. Two tracked copies, two-place update hazard, as claimed.
- **Execution:** Measured by me (stat + md5 + sha256 + git blob id).
- **Fix:** Sound: keep one canonical copy. (GitHub's repo-social/banner use of the root file should be checked by whoever deletes — the finding already hedges this way.)

### RE-SERV-216 — WEAKENED
- **Citations / my measurements:** `stat` on `native/ps2servers-edge/internal/compression/testdata/`: `test.iso` = **1,110,016 B**, `test_cd.chd` = **1,055,592 B**, `test_dvd.chd` = **1,049,766 B** — each exactly as filed. My sum: **3,215,374 B = 3.22 MB** (3.07 MiB), not the "~3.16 MB" in the title — a small numerical drift.
- **Mechanism — two filed premises are refuted by an in-tree note the finding says does not exist:** the finding claims the fixtures are "generated" and that "no in-tree note says so" (about the deliberate choice). In fact `testdata/README.md` (842 B, tracked in the same directory) documents them: they are **unmodified copies from `pcm720/udpfsd` at a pinned commit**, kept as cross-implementation reference vectors whose decoded bytes and EOF behaviour the tagged tests check exactly, with the Core/Edge divergence explained. They are not synthetic fixtures this repo generated, and regenerating the ISO in-test would destroy the reference-vector property the README describes (the CHDs decode *to* that reference). The size cost in every clone/archive is real; the "undocumented, generated" framing is not.
- **Execution:** Sizes measured by me; README read by me.
- **Fix:** The filed fix (generate the ISO in-test, or record the choice) is largely moot — the choice is already recorded. What survives is only the bare size observation; any removal would need to argue against the documented reference-vector purpose, which the finding does not engage.

---

## Tally

- **VERIFIED:** 22 — RE-SERV-191, 192, 193, 194, 195, 196, 197, 198, 199, 200, 201, 203, 205, 206, 207, 208, 209, 210, 211, 212, 214, 215
- **VERIFIED-DRIFT:** 2 — RE-SERV-202 (Linux-exclusive open enumerates 3×, not up to 2×), RE-SERV-204 (ENDPOINT attribution swapped between the two clients; `_NoRedirect` is a test-used re-export)
- **WEAKENED:** 2 — RE-SERV-213 (11 not 13 `continue-on-error` steps in release.yml; the `if-no-files-found: error` + `continue-on-error` pairing is in release-on-main.yml, not release.yml:403-420), RE-SERV-216 (sizes exact but sum is 3,215,374 B ≈ 3.22 MB; fixtures are documented, unmodified upstream reference vectors, not undocumented generated files)
- **REFUTED:** 0
- **BLOCKED-ENV:** 0 findings (sandbox UDP block affected only the udpbd package tests, which no finding in this batch depends on)

Arithmetic check: 22 VERIFIED + 2 VERIFIED-DRIFT + 2 WEAKENED = 26 = RE-SERV-191…216. ✓

## CANDIDATES (new defects / corrections surfaced during verification)

1. **RE-SERV-202 undercount (folded into its DRIFT verdict):** `launcher/raw_storage.py:299` — the Linux-exclusive path in `open_raw_device` calls `_exclusive_target(path)` a second time after unmounting, so a Linux exclusive open pays **three** full `_metadata()` enumerations (:289 via `_exclusive_target`, :299 via `_exclusive_target`, :310 via `mount_warning`→:413), not two. Evidence: `grep -n "_metadata()\|_exclusive_target(path)\|mount_warning(path)" launcher/raw_storage.py` → lines 289, 299, 310, 316, 413.
2. **RE-SERV-213 adjacent gap:** `release.yml`'s portable upload step (:403) omits `if-no-files-found` entirely (upload-artifact default is `warn`), so even without `continue-on-error` a missing portable file would only warn at that step; the hard-`error` setting exists only on the non-optional source/primary uploads. Evidence: `.github/workflows/release.yml:401-410` (no `if-no-files-found` key) vs :471 (`error`, source upload).
3. No other new defects found. In particular, the two `server.Close()` calls in `main.go` that superficially contradict RE-SERV-198 are on udpbd/smb server types, and the `flag.FlagSet.Parse` hits that superficially contradict RE-SERV-197 are a different symbol — both checked and cleared.
---

# Coordinator's closing sections

## Consolidated re-execution results (verifiers' own numbers, not the audit's)

**Mandated re-executions:**
- **RE-SERV-001 (signing downgrade)** — Reproduced: unsigned SESSION_SETUP on an authenticated, signing-required connection returned MORE_PROCESSING_REQUIRED (0xC0000016) with `authenticated` still True, `signing_key=None`, `signing_required=False`; a subsequent unsigned CREATE passed `_signature_check`. Mid-session NEGOTIATE moved the dialect 0x0302 → 0x0202 and overwrote the negotiate claims.
- **RE-SERV-002 (DesiredAccess ignored)** — Reproduced ×3: read-only CREATE+OVERWRITE truncated a 100-byte file to 0; SET_INFO rename via a read-only handle moved the file; read-only CREATE+DELETE_ON_CLOSE deleted it on CLOSE. (Bonus: SET_INFO disposition via a read-only handle also armed delete-on-close.)
- **RE-SERV-003 (READ ungated)** — Reproduced: a write-only handle (`FILE_WRITE_DATA`) read back `b'TOPSECRET-DATA'` with SUCCESS.
- **RE-SERV-022 (SMB2 stubs)** — Reproduced for write (status 0, 0 written, on a read-only server), read (SUCCESS, 0 bytes), create (SUCCESS, constant FileId `01000000000000000100000000000000`), and session_setup (SUCCESS as guest with users configured). Tree-connect limb **not** reproduced — it raises `struct.error` (see WEAKENED).
- **RE-SERV-029 (BWRITE extent)** — Reproduced **exactly**: BWRITE sector=100 count=1 on a 4-sector image → `WRITE_DONE 0`, host file 2,048 → **51,712 bytes**; the file-handle leg reproduced identically; BREAD at sector 100 on a fresh image returned success with 0 bytes.
- **RE-SERV-052 (WHICH-DEVICE / read-only default)** — Reproduced: Edge built in /tmp with Go 1.27.4 (default build flags; binary 11,366,917 B — the audit's 7,667,975 B figure was its release-flag build with `-trimpath -ldflags="-s -w"`, a flag difference, not a contradiction). `-read-only` is a plain bool flag whose help reads "omit to allow the console to write saves" — **the actual default is writable**, while the shipped WHICH-DEVICE.txt text (edge-build.yml ~387-397) claims "read-only by default" and its Quick Start passes no `--read-only`. The repo's own docs (EDGE.md:22, EDITIONS.md:39, INSTRUCTIONS.md:16-17) agree with the binary. No test guards the text.

**Baselines:**
- **Go** — Reproduced exactly: build/vet clean; 246 top-level tests = 145 pass / 99 fail / 2 skip (298 incl. subtests = 177/119/2); per-package failures udpfs 84, udpbd 10, smb 4, webui 1; 89× `sendto: operation not permitted` + 22× `i/o timeout` in output; 250 `func Test` definitions, 5 behind `cgo && chd`. All 99 failures individually classified: every chain terminates in the UDP block. **Zero genuine failures.**
- **Python** — Reproduced at 1,544 run / 1,389 pass / 3 fail / 72 errors / 80 skip (claimed 1,388/73). Classification otherwise test-for-test identical: 52 UDP-blocked, 1 copy-environment (attribution test, git exit 128). The claimed one genuine failure, `test_nfs4_server`'s endless-fragments test, **did not reproduce** (passed in the full run and 5/5 isolated, 2.5–5.1 s each): it is non-UDP (TCP) but timing-fragile by construction — the server must consume 264,193 empty fragments one recv at a time before closing, racing a 5 s socket timeout. Correct class: intermittent/load-dependent, **not proven genuine**.

**Other executed proofs (selection):** RE-SERV-036 UDPBD CMD_WRITE sector 1000 grew 2,048 → 513,024 B with success; RE-SERV-039 NFS WRITE with zero stateid and no OPEN overwrote a file head, and SETATTR with a fabricated `0xff` stateid extended a file to exactly 10,485,760 B (the `allow_writes=False` control correctly refused); RE-SERV-053 pure-Python and OpenSSL CMAC of the empty string both give `4387c14b…f9` while the self-test's filed constant is the RFC 4493 L block; RE-SERV-055 spec-formed `\x04DVD\0` → STATUS_OBJECT_NAME_NOT_FOUND against the real handler while plain `DVD\0` succeeds; RE-SERV-064 a sparse exactly-2 TiB image is accepted by `BlockDevice` and then INFO packing raises `struct.error` (upgraded from the audit's INFERRED); RE-SERV-071 `genericManager.Restart` with a nil logger panics; RE-SERV-073 `SMBV2.build_argv({"root_dir": …})` raises `KeyError: 'games_folder'`; RE-SERV-083 Caduceus `pad()` over-pads by a full block at aligned lengths (128 → 192 vs the owned protocol's 128); RE-SERV-085 the shipped systemd env args make the built binary exit 1 with the exact refusal message; RE-SERV-127 a BIND PDU split across WRITEs draws `RpcError: short PDU` and a fragmented REQUEST draws a FAULT PDU; RE-SERV-142 the FID allocator cycle yields 61,440 distinct values before wrap-overwrite; RE-SERV-154 exactly 16 inline UCI boolean tests counted; RE-SERV-183 brute force over all 4096×4096 input pairs gives third-branch hits = 0; RE-SERV-189 the NFS `_Handles` table held 31 entries after 30 files + root with no eviction path; RE-SERV-201 the exFAT upcase table is 65,536 entries and one-file `VirtualExfat` construction measured ≈88–176 ms; RE-SERV-215 both banner PNGs are 571,581 B with identical md5/sha256 and the same git blob.

## Duplicate census (coordinator's cross-batch pass)

The audit's four pre-numbering merges (220 section findings → 216) stand. Within batches, workers confirmed no same-mechanism double-filings; the closest pairs are complementary halves or parallel instances in different implementations (002/003, 015/025, 017/025, 029/036, 037/043, 058/059, 065/066/067, 142/153, 146/150/151). Across batches, the coordinator's mechanism-level pass over all 216 finds **no double-filings**. Related-but-distinct families, recorded so a fixer does not mistake them for duplicates: the compressed-image family (034 bounds, 061 offsets, 146 triplicated parsing, 150 duplicated validation, 151 dual parsers, 193 no caching); the SMB transaction family (046 handler behaviour, 140 duplicated envelope construction); stop-handling (149 and 157 both touch the same `_stop_on_sigterm` bodies from different angles — duplication vs triple installation — and must be fixed once, as batch G flagged); PowerShell resolution (048's raw_storage PATH-hijack call site vs one limb of 156's copied-helper census — adjacent, different mechanisms); the release workflows (176 duplication/drift vs 213 fail-soft steps — different claims over the same two files).

## Candidates (new defects found during verification — separate from the 216)

1. **CAND-A1** — `h2_tree_connect` struct.pack arity bug (smbv1_server/smbserver_opl.py:573-581): `struct.pack("<BBII", 16, 0x01, 0, 0x00000030, 0x000f001f)` passes 5 values for 4 fields and raises `struct.error` on **every** SMB2 TREE_CONNECT under `--smb-version 2/3`, escaping serve_conn's `(ConnectionError, OSError)` handling and killing the connection thread. (Surfaced by RE-SERV-022's weakening.)
2. **CAND-D1** — Caduceus `read_events` (caduceus.py:226-229) gates unlock notices on `event.get("ago", 999) <= 2`, but owned-runtime unlocks carry `at`, not `ago` — console notices never fire on owned data. A second victim of RE-SERV-079's schema divergence, not cited there.
3. **CAND-D2** — `best_lan_ip`'s own docstring (netinfo.py:224-228) argues the opposite preference to its module header — documentation drift that would survive a code-only fix for RE-SERV-078.
4. **CAND-F1** — Direct script execution is broken suite-wide by missing `sys.path` prologues, including in files that *do* have `unittest.main()` (e.g. `test_art_fetch.py` crashes identically) — RE-SERV-102's fix as stated cannot deliver its stated benefit.
5. **CAND-F2** — The NFS4 endless-fragments test is timing-fragile by construction (264,193 fragments vs a 5 s timeout); a smaller over-cap fragment count would make it deterministic. (Explains the Python baseline discrepancy.)
6. **CAND-H1** — `h_trans2_find_first2` (smbserver_opl.py:1243) does `entries[0]` with no emptiness guard → IndexError on an empty directory instead of an end-of-search reply.
7. **CAND-H2** — The same `_last_disc` getattr pattern as RE-SERV-185 exists in base udpfs_server.py:1627/1634 — mirror any 185 fix consciously.
8. **CAND-I1** — release.yml's portable upload (:403) omits `if-no-files-found` entirely (defaults to warn) — adjacent to RE-SERV-213.
9. **Fix hazard (not a defect)** — RE-SERV-166: `decode_response` raises unless `Success is True`, but award's already-unlocked path needs the Success-false body; a literal switch to the shared decoder would break it. Any fix needs a non-gating decode variant.

## Coordinator spot-checks (personally re-derived before relaying)

- **RE-SERV-128 (the one REFUTED)** — Re-executed by the coordinator from the pinned source (AST-extracted `udpfs_server.py:2746`): totals 0→1024, 100→1024, 1024→1024, 1025→1280, 12800→1280, 20000→1408, 100000→1408. The tie-break limb at lines 2760-2761 (`packets == best_packets and alignment > best_align`, candidates visited smallest-first) is exactly what the audit missed. Refutation confirmed first-hand.
- **RE-SERV-022 (the headline WEAKENED)** — Re-read by the coordinator at smbv1_server/smbserver_opl.py:572-581: the 5-values-for-4-fields `struct.pack("<BBII", …)` is as batch A reported; the handler cannot return its claimed fake SUCCESS. Weakening confirmed first-hand.

## Final tally

**216 findings: 191 VERIFIED · 16 VERIFIED-DRIFT · 8 WEAKENED · 1 REFUTED · 0 BLOCKED-ENV.** Baselines: Go VERIFIED (exact), Python VERIFIED-DRIFT (one test; its "genuine" failure reclassified as timing-fragile, not proven). 9 candidates/hazards recorded separately. Pin `c5f021c4d38698860f56b03498adc261dbb8d8dd` confirmed at start by all nine workers and at end by the coordinator; clone `git status` clean — read-only held throughout.
