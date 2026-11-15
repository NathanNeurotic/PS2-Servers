# Sable Audit — PS2-Servers, ROUND 2 (per-opcode protocol audit + Go Edge measured)

- **Auditor:** Sable
- **Repo:** NathanNeurotic/PS2-Servers
- **Pin (READ, verified in clone 2026-11-14):** `main` @ `c5f021c4d38698860f56b03498adc261dbb8d8dd` ("style(docs): complete neutral palette… (#318)"). Per PINS-ROUND2.md the delta from the round-1 pin `5179b4bb1f48` is docs CSS only — **no code changed**, so every round-1 line citation re-verified by spot-reading at this HEAD still stands, and all citations below are at this HEAD.
- **Method:** read-only in the repo. `git status` clean at start and at end (verified). A user-space Go toolchain was installed outside the repo (see §1) and all builds/tests wrote only to `/tmp` and `~/workspace/tools`. Two findings (PS2SERVERS-043, -044) and one handler table (the embedded mini-SMB2) were additionally **proven by executing the handlers in a Python harness** against the pinned tree — labelled EXECUTED where so.
- **Round-1 file:** `Sable-PS2-Servers.md` (PS2SERVERS-001…040). This file continues numbering at 041 and does not repeat round-1 findings; where round 2 measured something round 1 could only estimate, it says so explicitly.

---

## 1. Go toolchain + Edge, MEASURED (closes round-1 Category 4 gap)

**Toolchain outcome: SUCCESS.** No `go` was present (`go: command not found`). Official tarball `go1.27.4.linux-amd64.tar.gz` was downloaded from go.dev into `~/workspace/tools/`, sha256 verified against the value published in go.dev's `dl/?mode=json` feed (`7226736c…24840`, `sha256sum -c` = OK), extracted to `~/workspace/tools/go1.27.4/`. No sudo, no repo changes. GOPATH/GOCACHE were pointed at `~/workspace/tools/` so not even caches touched the repo.

**Caveat on comparability:** the repo's own CI does not use one Go version — release builds use `go-version-file` (= go.mod's `go 1.22`, .github/workflows/edge-build.yml:226-228), the parity job pins Go 1.25 (.github/workflows/edge.yml:196-198), and edge-build.yml:296-303 itself notes a newer local toolchain produces ~1 MB larger binaries. My numbers are Go 1.27.4 / linux-amd64 and are labelled as such everywhere. See PS2SERVERS-054.

### Builds (all `-o /tmp/...`, repo tree untouched)

| Build | Command shape | Size (bytes) |
|---|---|---|
| Default | `go build ./cmd/ps2servers-edge` (CGO on) | **11,371,165** |
| Stripped only | `-ldflags="-s -w"` | 7,704,839 |
| **Release-style (the shipped shape)** | `CGO_ENABLED=0 go build -trimpath -buildvcs=true -ldflags="-s -w -buildid= -X main.version=audit"` (flags copied from .github/workflows/edge-build.yml:278-280) | **7,622,780** (7.27 MiB) |

Release-style sections (`size`): text 7,290,708 · data 322,371 · **bss 33,828,232**. The bss is one symbol: `crypto/internal/fips140/drbg.memory` = exactly **33,554,432 B** (32 MiB) of `.noptrbss` (`go tool nm -size`, READ).

**What the 32 MiB bss is and is not (mechanism reproduced, not guessed):** a 9-line program importing only `crypto/rand` + `net/http` built with the same toolchain also gains the identical 33,554,432 B `drbg.memory`; a minimal program's total bss is 219,136 B. So the reserve arrives with the stdlib crypto chain, not with repo code. And it does **not** cost resident memory when untouched: the release binary run as `udpfs --root /tmp/edgegames --port 45011` idled at **VmRSS 6,984 kB** (Pss 6,977 kB) with VmSize 1,266,068 kB. Flash cost is the file (7.27 MiB); RAM cost at idle is ~7 MB. The bss matters only under strict overcommit accounting (INFERRED for router impact). Reported here so nobody has to guess at it again — the honest headline is the file size and the package split below, not the bss.

### Where the binary's code actually is (`go tool nm -size` on the default build; sized-symbol bytes)

Project packages (all of them — the whole product is ~250 KB of symbols):

| Package | Symbol bytes | Symbols |
|---|---|---|
| internal/udpfs | 102,527 | 123 |
| internal/webui | 55,175 | 86 |
| internal/smb | 42,440 | 53 |
| internal/udpbd | 18,007 | 20 |
| internal/compression | 11,448 | 29 |
| internal/sessionstats | 7,148 | 9 |
| internal/filesystem | 4,115 | 14 |
| internal/logging | 3,435 | 6 |
| internal/session | 1,826 | 7 |
| internal/protocol | 1,810 | 8 |
| internal/statuslistener | 1,782 | 5 |

Largest stdlib contributors: runtime 725,674 · crypto/tls 252,289 · encoding/json/v2 162,351 · net 156,007 · **net/http 142,728 · net/http/internal/http2 128,822** · crypto/x509 105,726 · reflect 96,666. Plus the WebUI's embedded assets: **55,307 B** total (`internal/webui/embed.go:5` `//go:embed assets/*` — app.js 18,958 + index.html 25,992 + styles.css 10,357, measured).

The repo's own size gate already knows the conclusion: edge-build.yml:288-290 records the embedded web GUI taking the mipsle build "from 3.31 MiB to 7.19 MiB in one merge — net/http, encoding/json and the embedded assets", sets a loose 9 MiB ceiling (edge-build.yml:293), and at edge-build.yml:305-308 names the remedy itself: *"put a feature behind a build tag (the web GUI is the obvious candidate — it is the only thing pulling in net/http)"*. Round 2's contribution is confirming it with a local build: on amd64 the release-style binary is 7,622,780 B against that same ceiling logic, and net/http + http2 + tls + json symbol bytes alone exceed the entire project code several times over.

### `go vet` / `go test` (results only)

- `go vet ./...` — **clean**, no diagnostics (exit 0).
- `go test ./...` — 10 packages: **6 pass** (cmd/ps2servers-edge, internal/compression, internal/filesystem, internal/memory, internal/protocol, internal/sessionstats); **4 fail** (internal/smb, internal/udpbd, internal/udpfs, internal/webui) with 99 top-level `--- FAIL` lines. **Every failure is the sandbox, not the code:** 89 failure lines are literally `sendto: operation not permitted` on loopback UDP, and the rest are status-probe tests timing out (3.0 s) because their UDP probes can never be sent — the identical sandbox restriction that invalidated the live-socket Python tests in round 1. No non-network failure was found in the output. Reported as *environmentally blocked*, not as a pass and not as defects.
- One corroboration worth recording: `internal/udpfs` contains `TestSecondPeerCannotOpenTheSameFileForWriting` — **Go Edge does implement the single-writer exclusion** whose absence in the Python editions is round-1 PS2SERVERS-013. The fix pattern exists in-repo, in Go, with a test.
- **CHD variant not buildable here:** `CGO_ENABLED=1 go build -tags chd` fails with `fatal error: libchdr/chd.h: No such file or directory` (internal/compression/chd_cuebin_enabled.go:7-8, `#cgo LDFLAGS: -lchdr`). libchdr headers are absent in this VM and installing system packages was out of scope. The non-CHD build above is the portable/release shape; CHD Edge size remains unmeasured.

---

## 2. Per-opcode coverage — Python SMB2 server (`smb2_server/`)

Dispatch: `Smb2Server._dispatch` (smb2_server/smb2_server.py:614-645) looks up `_HANDLERS` (smb2_server/smb2_server.py:1432-1449) and converts every exception to a status — Smb2Error, PathError, AuthError, OSError each mapped, and a catch-all `Exception` → STATUS_INVALID_PARAMETER. Unknown commands → STATUS_NOT_SUPPORTED (smb2_server/smb2_server.py:615-618). **16 of the 19 commands in smb2_wire.py:75-93 are registered.** Buffer discipline is centralised in `request_buffer` (smb2_server/smb2_server.py:695-720), which exists precisely because a DataOffset below the header once made Python slice from the end of the body — the docstring says so; every handler that takes a client buffer now routes through it.

| Cmd | Handler (file:line) | Length/offset/count validation | Verdict |
|---|---|---|---|
| 0x0000 NEGOTIATE | h_negotiate :745 | `len(body)<36` refused; dialect list loop breaks at body end (:751-755) | PASS |
| 0x0001 SESSION_SETUP | h_session_setup :822 | `len<24` refused; security blob via request_buffer (least = header+24) | PASS |
| 0x0002 LOGOFF | h_logoff :927 | body unused; fixed reply | PASS |
| 0x0003 TREE_CONNECT | h_tree_connect :936 | `len<8` refused; path via request_buffer | PASS |
| 0x0004 TREE_DISCONNECT | h_tree_disconnect :974 | body unused; `trees.pop(..., None)` | PASS |
| 0x0005 CREATE | h_create :999 | `len<56` refused; name via request_buffer; dispositions validated; create **contexts ignored** (offsets unpacked :1007-1010, never read — legal: no context is ever granted in the reply) — but see -043 for the DELETE_ON_CLOSE access gap | PASS with -043 |
| 0x0006 CLOSE | h_close :1069 | `len<24` refused; handle_from checks both FileId halves (:252-273) | PASS |
| 0x0007 FLUSH | h_flush :1102 | **no length check** (short body only fails indirectly via handle_from's struct.error → catch-all); **flush/fsync OSError swallowed, SUCCESS returned regardless** | **FAIL → -050** |
| 0x0008 READ | h_read :1130 | `len<48` refused; length clamped to MAX_READ (1 MiB, smb2_wire.py:207); zero-byte read answered END_OF_FILE, not empty success | PASS |
| 0x0009 WRITE | h_write :1160 | `len<48` refused; `length > MAX_WRITE` refused *before* slicing; data via request_buffer; can_write checked | PASS |
| 0x000A LOCK | — none | → NOT_SUPPORTED | Absent (honest refusal; no byte-range locking) |
| 0x000B IOCTL | h_ioctl :1373 | `len<56` refused; input via request_buffer; only FSCTL_VALIDATE_NEGOTIATE_INFO (real check against the recorded NEGOTIATE, :1406-1429, mismatch drops the connection) and FSCTL_PIPE_TRANSCEIVE served; all else NOT_SUPPORTED | PASS |
| 0x000C CANCEL | h_cancel :1367 | returns `(None, None, None)` = no reply, which is what the protocol requires | PASS |
| 0x000D ECHO | h_echo :1363 | fixed 4-byte body | PASS |
| 0x000E QUERY_DIRECTORY | h_query_directory :1211 | `len<32` refused; pattern via request_buffer; out_len clamped to MAX_TRANSACT; unsupported info class → INVALID_PARAMETER, not a fabricated record | PASS |
| 0x000F CHANGE_NOTIFY | — none | → NOT_SUPPORTED | Absent (clients must poll) |
| 0x0010 QUERY_INFO | h_query_info :1282 | `len<40` refused; `len(blob) > out_len` → INFO_LENGTH_MISMATCH; unknown type/class → NOT_SUPPORTED | PASS |
| 0x0011 SET_INFO | h_set_info :1312 | `len<32` refused; payload via request_buffer; rename name-length validated against payload (:1349-1351); BASIC_INFORMATION accepted-and-ignored **deliberately** (documented :1338-1341) — **but FILE_DISPOSITION sets delete_on_close with no per-handle access check** | **FAIL → -043** |
| 0x0012 OPLOCK_BREAK | — none | → NOT_SUPPORTED; server never grants oplocks (CREATE answers OplockLevel 0), so there is nothing to break | N/A — correct |

Framing layer (smb2_wire.py): `parse_header` checks magic + 64-byte minimum (:246-249). `recv_msg` (:37-54) trusts the 24-bit session length with **no ceiling** → **-045**. Compound chaining in `_dispatch_all` (smb2_server.py:417-475) has **no cap on chained requests** → **-051**. Per-connection handle table has **no cap** → **-053**.

**Overall SMB2 verdict:** the best-validated surface in the repo. Four real defects (-043, -045, -050, -051) and one missing cap (-053); everything else checked validates before use, and the one "success without work" that is deliberate (BASIC_INFORMATION timestamps) is documented in its own comment with the reason.

## 3. Per-opcode coverage — NFSv4 server (`nfs4_server/nfs4_server.py`)

Dispatch: RPC record layer (`_read_record` :498-521) caps total record size at MAX_RECORD = MAX_IO + 8,192 (:50); `_compound` (:567-600) caps op count at MAX_OPS = 128 (:51) and total reply at one MAX_IO; `_one` (:601-621) maps unknown ops (outside 3–39) to OP_ILLEGAL and in-range unregistered ops to NOTSUPP, with XdrError/Nfs4Error/PathError/OSError/Exception each converted. The XDR `_Reader` (:187-218) bounds-checks **every** take (`n < 0 or end > len` → XdrError) and caps bitmaps at 8 words. **18 ops registered** (self.ops, :367-378).

| Op | Handler (line) | Validation | Verdict |
|---|---|---|---|
| 24 PUTROOTFH | _op_putrootfh :724 | trivial | PASS |
| 22 PUTFH | _op_putfh :728 | opaque bounds by Reader; `len(fh) > MAX_FH (128)` → BADHANDLE; handle prefix+number verified in _Handles.lookup (:286-296) | PASS |
| 10 GETFH | _op_getfh :735 | requires current fh (_need :638-641) | PASS |
| 32 SAVEFH | _op_savefh :739 | _need | PASS |
| 15 LOOKUP | _op_lookup :743 | name via _name (:650-660): empty/>255/`.`/`..`/separators/NUL all refused + check_component; parent must be a dir | PASS |
| 9 GETATTR | _op_getattr :754 | bitmap capped by Reader; only gettable attrs emitted (_attrs :696-705) | PASS |
| 25 READ | _op_read :759 | stateid skipped (16 fixed bytes — nothing is stateful, documented); count clamped to MAX_IO (:785); per-connection read-file cache capped at **4** files (:792-795) | PASS |
| 26 READDIR | _op_readdir :827 | cookie 1/2 → BAD_COOKIE; budget = min(maxcount, MAX_READDIR 64 KiB) − 16; TOOSMALL when nothing fits | PASS |
| 35 SETCLIENTID | _op_setclientid :875 | all fields consumed via Reader; clients dict capped at 256 with oldest evicted (:887-889) | PASS |
| 36 SETCLIENTID_CONFIRM | _op_setclientid_confirm :892 | verifier compared under lock; mismatch → STALE_CLIENTID | PASS |
| 20 OPEN_CONFIRM | _op_open_confirm :898 | fixed-size reads only | PASS |
| 4 CLOSE | _op_close :904 | fixed-size reads only | PASS |
| 18 OPEN | _op_open :910 | create-mode restricted to UNCHECKED/GUARDED (EXCLUSIVE → NOTSUPP); claim type must be CLAIM_NULL; access ∈ {1,2,3}; _settable restricts attrs to SIZE/MODE with exact-consumption check (:240-259); O_EXCL create | PASS |
| 38 WRITE | _op_write :944 | data via opaque (record-capped ≈1 MiB); `offset + len > 0x7FFF…` → FBIG; fsync before reply (FILE_SYNC honesty) | PASS |
| 34 SETATTR | _op_setattr :962 | _settable; SIZE-only effect; dir → ISDIR | PASS |
| 6 CREATE | _op_create :977 | dirs only (else BADTYPE); _settable consumed | PASS |
| 28 REMOVE | _op_remove :990 | _writable closes the connection's cached read files first (:674-683); _child validates the name and containment | PASS |
| 29 RENAME | _op_rename :1001 | both names via _child; same-file, type-mismatch and non-empty-dir cases handled explicitly | PASS |
| All others in 3–39 (ACCESS, COMMIT, LINK, LOCK, READLINK, RENEW, VERIFY, …) | — | → NFS4ERR_NOTSUPP via _one | Absent, honestly refused |

**Overall NFS verdict:** no per-op validation defect found. This is the reference implementation the other servers should be measured against — it is also the one that caps connections (MAX_CONNECTIONS = 32, :54, with per-address eviction in _admit :438-460). Its one long-term issue is not per-op: the handle registry never shrinks → **-047**.

## 4. Handler-table coverage — SMB1 server (`smbv1_server/smbserver_opl.py`) + its embedded mini-SMB2

SMB1 dispatch: `HANDLERS` (:1369-1384), **16 commands**; unknown → STATUS_NOT_IMPLEMENTED with a proper error reply (serve_conn :1472-1479); a logon gate (`_logon_required` :1408, `_PRE_LOGON_COMMANDS` :1403-1405) refuses everything except NEGOTIATE/SESSION_SETUP/ECHO/LOGOFF until SESSION_SETUP succeeds **when users are configured**. `Req` (:230-246) parses WordCount/ByteCount with the byte-count offset guarded, but **params are sliced, never length-checked** — handlers that `struct.unpack_from` fixed offsets trust the client to have sent them.

| Cmd | Handler (line) | Validation | Verdict |
|---|---|---|---|
| 0x72 NEGOTIATE | h_negotiate :642 | dialect scan bounds-guarded (`0 in d[i+1:]` checks) | PASS |
| 0x73 SESSION_SETUP_ANDX | h_session_setup :714 | wordcount < 13 → empty creds (_parse_session_setup :696-712); compare_digest for the password check | PASS |
| 0x75 TREE_CONNECT_ANDX | h_tree_connect :779 | share lookup + one-share leniency; IPC$ typed separately | PASS |
| 0x71 TREE_DISCONNECT | h_tree_disconnect :1056 | trivial | PASS |
| 0x74 LOGOFF_ANDX | h_logoff (after :1059) | clears logged_on | PASS |
| 0x2D OPEN_ANDX | h_open_andx :806 | name is NUL-scanned from data; resolve() containment (realpath + commonpath, Share.resolve :263-296) | PASS |
| 0xA2 NT_CREATE_ANDX | h_nt_create_andx :844 | **`len(r.params) < 39` refused**; disposition range-checked; name_len honoured; create/truncate semantics explicit | PASS — the model the others lack |
| 0x2E READ_ANDX | h_read_andx :962 | **unpacks params at offsets 4–20 unconditionally** (only the offset-high read at :969 is length-guarded) | **FAIL → -044 (EXECUTED)** |
| 0x2F WRITE_ANDX | h_write_andx :1003 | **unpacks params at offsets 4–22 unconditionally**; payload sliced from the raw message by wire DataOffset (bounded by message length); compressed-image handles refused | **FAIL → -044 (EXECUTED)** |
| 0x04 CLOSE | h_close :1030 | guards `len(r.params) >= 2` | PASS |
| 0x2B ECHO | h_echo :1038 | echoes data back | PASS |
| 0x80 QUERY_INFORMATION_DISK | h_query_disk :1063 | returns **fabricated geometry** (0xFFFF units, "non-zero free space" by design, commented as such) | Stub-by-design (documented) |
| 0x10 CHECK_DIRECTORY | h_check_directory :1093 | NUL-scan guarded | PASS |
| 0x00 CREATE_DIRECTORY | h_create_directory :1071 | read-only refused; resolve() containment; parent must exist | PASS |
| 0x32 TRANS2 | h_trans2 :1301 → _trans2_parse :1109 | **_trans2_parse unpacks params at offsets 18–28 with no length check**; subcommands FIND_FIRST2/FIND_NEXT2/QUERY_PATH_INFORMATION served, everything else NOT_IMPLEMENTED; FIND_NEXT2 with unknown SID returns an empty **success** instead of an error | **FAIL → -044 (EXECUTED)**; empty-success noted |
| 0x25 TRANSACTION | h_transaction :1332 | RAP NetShareEnum only; counts derived from the actual share list | PASS |

### The second SMB2 inside the SMB1 file (smbserver_opl.py:511-640) — EXECUTED

`serve_conn` (:1436-1457) diverts any ≥64-byte frame starting with the SMB2 magic to `SMB2_HANDLERS` (:626-640) — a **separate, miniature SMB2 implementation that is not the smb2_server package**. Verified by importing the pinned module and calling the handlers directly:

| Cmd | Handler (line) | What it actually does (EXECUTED where marked) |
|---|---|---|
| 0x0000 NEGOTIATE | h2_negotiate :545 | Fixed dialect by server mode flag; **server GUID is the constant bytes 01 02 … 10** (:556); ignores which dialects the client offered |
| 0x0001 SESSION_SETUP | h2_session_setup :565 | Hands out a fixed UID and SessionFlags=IS_GUEST **unconditionally — no credential check, and the serve_conn SMB2 branch never applies `_logon_required`**, so with `--user` configured an SMB2-speaking client skips the password gate the SMB1 path enforces |
| 0x0002 LOGOFF | h2_generic_ok :621 | SUCCESS, does nothing (no session exists to end) |
| 0x0003 TREE_CONNECT | h2_tree_connect :576 | SUCCESS for **any** path; no share lookup, no tree recorded |
| 0x0004 TREE_DISCONNECT | h2_generic_ok :621 | SUCCESS, does nothing |
| 0x0005 CREATE | h2_create :594 | **SUCCESS with a hard-coded FileId of 1; opens nothing** (EXECUTED: returns STATUS_SUCCESS, `conn.files` stays empty) |
| 0x0006 CLOSE | h2_close :618 | SUCCESS; closes nothing |
| 0x0008 READ | h2_read :611 | **SUCCESS with zero bytes, always** (EXECUTED) |
| 0x0009 WRITE | h2_write :618 | **SUCCESS reporting 0 bytes written; discards the data** |
| 0x0012 (OPLOCK_BREAK) | h2_echo :624 | **Mis-mapped:** 0x0012 is OPLOCK_BREAK; ECHO is 0x000D, which is **not in the table** and gets NOT_IMPLEMENTED (EXECUTED: table keys are exactly 0,1,2,3,4,5,6,8,9,0x12) |
| 0x000E/0x0010/0x0011/0x0007/0x000B QUERY_DIRECTORY, QUERY_INFO, SET_INFO, FLUSH, IOCTL | — | NOT_IMPLEMENTED — i.e. a client cannot even list a directory through this path |

This is a third SMB2 implementation in the repo (after smb2_server and the Go Edge SMB1-only port), and unlike the other two it is a façade: it completes a handshake and then lies on every verb. Whatever client it was built to placate, a client that believes its CREATE/WRITE answers has been told operations succeeded that never happened. → **-042**, the largest finding of this round.

## 5. GUI responsiveness + startup (Categories 5/4, code-assessed + measured)

**What runs before the window can appear** (entry: ps2servers.py:27 → launcher/main.py:65-93 → launcher/gui.py:951 LauncherApp.__init__ → run_gui :3778-3786):

1. **Import chain — measured 0.40 s in this VM** (`import launcher.main` 0.083 s + `import launcher.gui` a further 0.313 s; `import launcher.servers` alone 0.137 s). gui.py:33 imports directlink, elevate, netinfo, posix_firewall, ra_setup, ra_sounds, servers, status_client, tray, windows_setup wholesale, on top of launcher/__init__.py's import-hook patching (round-1 -006). For scale: this VM is fast; the packaged one-file build pays more. → **-049**
2. `DesktopInstance` mutex (launcher/single_instance.py:21-51) — cheap, non-Windows is a no-op. Not a finding.
3. `config.load()` (gui.py:970) — JSON read **plus the portable-mode probe-file write on every call** (launcher/config.py:32-49; round-1 -032, still present at this HEAD) — and `servers.migrate_verbose_default(self.saved)` runs on **every** launch (gui.py:974; servers.py:747-768): once the marker exists it is a dict copy, but the machinery round-1 -019 flagged is still on the startup path.
4. Two poller **threads** start (gui.py:957-960, status_client.Poller + RA SessionPoller) — threaded correctly, not a finding.
5. `_build()` (gui.py:1307) constructs **every tab and card**, including the Library tab + LibraryPanel (gui.py:1416; the panel's catalogue load is threaded, library_panel.py:189-208 — good) — and, synchronously on the GUI thread, the header's network detection: `netinfo.best_lan_ip()` (gui.py:1320) **and** `netinfo.ip_choices()` (gui.py:1327). ip_choices() → all_ipv4() (netinfo.py:107-119) = getaddrinfo(gethostname()) **plus a subprocess**: on Linux `ip -o -4 addr show`, on macOS `ifconfig`, each `subprocess.run(..., timeout=3)` (netinfo.py:70-80). Measured here: all_ipv4() 0.030 s warm. Worst case is the 3 s timeout, paid before first paint, for a dropdown. _restore's own comment (gui.py:3472-3474) already refuses to re-run all_ipv4() at startup "for nothing" — _build does it anyway. → **-048**
6. After first paint, timers: _drain_logs 150 ms (:1047), _poll_status 600 ms (:1050), plus conditional one-shots at 350 ms (pending direct-link/firewall/start recovery probes, :1056-1066), direct-link startup at 600 ms (:1072-1077), autostart at 750 ms (:1080), setup-guide offer at 1200 ms (:1054).

**Steady-state GUI thread:** the design is mostly honest — server output arrives via queue, `_drain_logs` (gui.py:3025-3040) caps work at 500 lines/tick and batches one widget update per server per tick; `_poll_status` (gui.py:3061) repaints only on change for server cards. Two residual costs, both downstream of round-1 -019's verbose default: (a) `_append_log` (gui.py:3042-3051) calls `widget.index("end-1c")` and an int parse **on every batch** and the in-class version scrolls unconditionally (`see("end")`) — the shim's at-bottom check exists only in main.py's replacement (round-1 -007); (b) `_terminal_text` (gui.py:3053-3060) builds a new prefixed string for **every line** on the GUI thread. At the 500-line cap that is 500 string builds + 500-line Text insertions per 150 ms tick, i.e. the pump's ceiling is ~3,300 formatted lines/second of GUI-thread work whenever a verbose server is chatty. → **-052** (mechanism READ; the saturation magnitude is INFERRED — no Tk in this VM to profile with).

**Blocking calls found off the startup path:** direct-link adapter enumeration is correctly threaded (gui.py:1838-1846, worker thread + after(0) callbacks; directlink._run subprocess timeout=20 at directlink.py:76-82 runs on that thread). Windows-setup and firewall flows are likewise threaded with after(0) finishes (gui.py:2469, :2575-2577, :2757-2759). The access-check flow polls with after(100) (gui.py:798-823). **No other main-thread network scan, library scan, or subprocess wait was found in gui.py** — the library scan itself is threaded with a cancel event (library_panel.py:64-66, :189-208). This section is the honest negative result: apart from -048/-049/-052, the GUI's blocking discipline is good.

---

## 6. New findings (PS2SERVERS-041…), sorted by benefit

## PS2SERVERS-041 / Edge, measured: the WebUI is the binary — put it behind the build tag the repo's own CI already names
- **Category:** 4 (Size) — *round-1 -028 quantified, now with numbers*
- **Location(s):** `PS2-Servers/native/ps2servers-edge/internal/webui/` (55,175 symbol bytes + 55,307 B embedded assets at internal/webui/embed.go:5); net/http + http2 + crypto/tls + encoding/json pulled in only by it (nm table in §1); size-gate history and remedy at `PS2-Servers/.github/workflows/edge-build.yml:288-310`
- **Problem:** The release-shaped Edge binary measures **7,622,780 B** (Go 1.27.4, amd64, this VM) for a product whose own project code is ~250 KB of symbols; the WebUI's stdlib entourage (net/http 142,728 + http2 128,822 + tls 252,289 + json/v2 162,351 symbol bytes) outweighs all five server packages combined several times. CI's comment records the same event from the other side: WebUI took mipsle from 3.31 MiB to 7.19 MiB in one merge.
- **Fix:** Exactly what edge-build.yml:305-308 already proposes: a `webui` build tag, on by default for desktop-ish targets, off for the router targets Edge exists for. No new mechanism — Go build tags are already how CHD is gated in this tree (internal/compression/chd_enabled.go:1).
- **Benefit:** CI's own before/after suggests roughly **half the file** on router targets (≈3.9 MiB on the mipsle figures the workflow quotes); on this VM's amd64 build the http/tls/json symbol mass is ~690 KB before counting the runtime cost of linking it (est. from nm, labelled as such).
- **Effort:** M. **Risk:** Low (a tag, with the default unchanged). **Confidence:** High (READ + MEASURED)

## PS2SERVERS-042 / The SMB1 server contains a third SMB2 implementation that answers success without doing the work
- **Category:** 8 (Half-finished feature) / 6 (Correctness/security)
- **Location(s):** `PS2-Servers/smbv1_server/smbserver_opl.py:511-640` (SMB2_HANDLERS + h2_* handlers), dispatched at `PS2-Servers/smbv1_server/smbserver_opl.py:1436-1457`
- **Problem:** EXECUTED at this pin: h2_create returns STATUS_SUCCESS with a hard-coded FileId and opens nothing; h2_read always returns success with zero bytes; h2_write reports success and discards the data; h2_tree_connect accepts any path without a share lookup; h2_session_setup issues a guest session unconditionally and the SMB2 branch of serve_conn never applies the `_logon_required` gate the SMB1 branch enforces — so with credentials configured, an SMB2-speaking client bypasses them entirely through this path. ECHO (0x000D) is unimplemented while OPLOCK_BREAK (0x0012) is mis-mapped to the echo handler, and QUERY_DIRECTORY does not exist, so no client can list a folder through it. It is ~130 lines that complete a handshake and then lie on every verb.
- **Fix:** Delete SMB2_HANDLERS, the h2_* handlers and the SMB2 diversion in serve_conn. SMB2 clients belong to the real smb2_server, which the launcher already runs as its own server card; an SMB1 client that probes with an SMB2 frame gets a clean NOT_IMPLEMENTED from the normal path instead of a fake session. If a named console client is shown to depend on the handshake alone, keep only NEGOTIATE→(dialect redirect) and delete the rest — but that client should be named in a comment, replacing the current justification-free existence.
- **Benefit:** est. −130 lines; removes a credential-gate bypass and a silent "your write succeeded" data-loss illusion.
- **Effort:** S. **Risk:** Med (the unknown is whether any console flow uses the handshake as a probe — console-test before/after). **Confidence:** High (READ + EXECUTED)

## PS2SERVERS-043 / SMB2: a read-only handle can delete any file on a writable share via SET_INFO disposition + CLOSE
- **Category:** 6 (Bug / security)
- **Location(s):** `PS2-Servers/smb2_server/smb2_server.py:1342-1344` (FILE_DISPOSITION_INFORMATION sets `h.delete_on_close` checking only share writability via the `_writable` call at :1314); deletion executed at `PS2-Servers/smb2_server/smb2_server.py:1082-1089` (h_close); same pattern at CREATE: `PS2-Servers/smb2_server/smb2_server.py:1053-1055` (FILE_DELETE_ON_CLOSE honoured after only the share-level check). Contrast: the EOF-truncate branch of the same function *does* check `h.can_write` (:1331-1333), and h_write checks it too (:1179-1180)
- **Problem:** EXECUTED at this pin: a handle opened with read-only DesiredAccess (`can_write == False`, set at :1049 from ACCESS_WRITE_ANY) set FILE_DISPOSITION_INFORMATION delete=1 → STATUS_SUCCESS, `delete_on_close == True`; CLOSE then removed the file from disk. MS-SMB2 expects disposition/delete-on-close to require DELETE access on the handle; here the only gate is that the *share* is writable, so any authenticated (or guest, on an open share) client that can open a file for reading can delete it without ever holding write access.
- **Fix:** Require `h.can_write` (or track the DELETE bit separately at CREATE) in both places: the SET_INFO disposition branch and the CREATE delete-on-close branch — the same one-line check the EOF branch already makes.
- **Justification (addition):** Concrete, executed bug — unauthorised deletion on the server's primary use case (a games/saves share). The fix is one existing check copied to two sites; no new mechanism.
- **Benefit:** A deletion path closed; no lines saved.
- **Effort:** S. **Risk:** Low. **Confidence:** High (READ + EXECUTED)

## PS2SERVERS-044 / SMB1: a truncated request kills the connection thread with an uncaught struct.error
- **Category:** 6 (Bug / robustness)
- **Location(s):** Unchecked `struct.unpack_from` on client-controlled params in `PS2-Servers/smbv1_server/smbserver_opl.py` — h_read_andx :966-971, h_write_andx :1007-1012, _trans2_parse :1113-1118 (via h_trans2 :1301); dispatch loop `serve_conn` :1421-1524 catches only `(ConnectionError, OSError)` (:1506); the handler runs as a bare thread target (:1844)
- **Problem:** EXECUTED at this pin: READ_ANDX, WRITE_ANDX and TRANS2 requests whose WordCount declares fewer parameter bytes than the handler unpacks raise `struct.error` ("unpack_from requires a buffer of at least…"), which is not an OSError, escapes serve_conn's handler, and terminates the connection thread with a traceback. The server process survives (thread-per-connection), so impact is a dropped session + console-visible hang/retry, not a crash of the server — but any LAN device can drop any SMB1 session at will with a 37-byte packet, and the same pattern is why h_nt_create_andx's explicit `len(r.params) < 39` check (:850) exists: it was fixed there and not in the three neighbouring handlers.
- **Fix:** The smallest change that closes the class: catch `struct.error` alongside OSError in serve_conn and drop the connection quietly (one line), **or** length-guard the three handlers the way NT_CREATE already is. Doing both is belt-and-braces; either alone fixes the observed failure. No new abstraction.
- **Benefit:** Malformed-packet session drops eliminated; traceback noise out of the log.
- **Effort:** S. **Risk:** Low. **Confidence:** High (READ + EXECUTED)

## PS2SERVERS-045 / Neither SMB framing layer caps the declared message length (24-bit, up to 16.7 MB) against the 1 MiB the servers advertise
- **Category:** 6 (Security) / 5 (Performance)
- **Location(s):** `PS2-Servers/smb2_server/smb2_wire.py:37-54` (`recv_msg`: length taken from the 3-byte header, `_recv_exact(sock, length)` with no ceiling); the duplicated copy at `PS2-Servers/smbv1_server/smbserver_opl.py:193-206`; advertised limits at `PS2-Servers/smb2_server/smb2_wire.py:206-208` (MAX_TRANSACT/READ/WRITE = 1 MiB). Contrast: NFS caps its record at MAX_RECORD ≈ 1.008 MiB (nfs4_server/nfs4_server.py:50, enforced :498-521)
- **Problem:** A peer — unauthenticated; NEGOTIATE is the first message — can declare a 16,777,215-byte message and make the server buffer it whole before any handler runs (`_dispatch_all` then holds it again as slices). Per connection that is ~16× the server's own advertised maximum, and SMB2 connections are uncapped (round-1 -022), so the multiplier is unbounded. On the low-RAM Windows PCs and the Edge-class hosts this project targets, a handful of such frames is a memory-exhaustion attack from the LAN.
- **Fix:** In both `recv_msg` copies, refuse (drop the connection) when the declared length exceeds a ceiling — the honest value is the server's own: 64 + MAX_TRANSACT plus compound headroom, or simply NFS's MAX_RECORD reused as the house number. One comparison per copy; no new mechanism.
- **Justification (addition):** Concrete unauthenticated memory bound where none exists, with the in-repo precedent (NFS) and the in-repo number (MAX_TRANSACT) already present.
- **Benefit:** Per-connection pre-auth memory bounded to ~1 MiB instead of ~16.7 MiB.
- **Effort:** S. **Risk:** Low (no legitimate SMB2 message to these servers approaches the cap; compound CREATE/WRITE traffic is ≤ 1 MiB + header by the server's own advertised limits). **Confidence:** High (READ)

## PS2SERVERS-046 / SMB1: open files and search snapshots per connection are uncapped — real file descriptors, held indefinitely
- **Category:** 5 (Performance) / 6 (Security)
- **Location(s):** `PS2-Servers/smbv1_server/smbserver_opl.py:411` (conn.files / conn.searches dicts); opens add unconditionally — h_open_andx :837, h_nt_create_andx :931; OpenFile opens a real fd at construction (:371, `open(path, "rb", buffering=0)`); searches store a full directory-listing snapshot at :1251 and are only popped when the listing is exhausted (:1266) — an abandoned FIND_FIRST2 keeps its snapshot for the connection's life. No cap constant exists anywhere in the file (grep-verified)
- **Problem:** A client (or a stuck console retry loop) that opens files without closing them accumulates one OS file descriptor per open on the server process; descriptor exhaustion then fails *every* server in the process, not just this connection. NFS solves exactly this in-repo: its per-connection read cache is capped at 4 files with eviction (nfs4_server/nfs4_server.py:792-795), and UDPFS caps handles at 64 per client (round-1 -021).
- **Fix:** Refuse a new open/search past a per-connection cap (64 files, matching the UDPFS house number) with the server's existing error statuses; when a search cap is hit, evict the oldest search like NFS evicts its read cache. No new subsystem — two comparisons at the two allocation sites.
- **Justification (addition):** Concrete fd-exhaustion path with two in-repo precedents for the exact fix shape.
- **Benefit:** Worst-case per-connection cost becomes bounded (64 fds + 64 listing snapshots).
- **Effort:** S. **Risk:** Low (no real console holds 64 simultaneous opens on one connection — INFERRED from OPL's usage pattern). **Confidence:** High (READ) for the absence of caps; Med for the safe cap value (INFERRED)

## PS2SERVERS-047 / NFS: the filehandle registry only ever grows — every path ever looked up is remembered for the server's lifetime
- **Category:** 4 (Memory) / 1 (Dead state)
- **Location(s):** `PS2-Servers/nfs4_server/nfs4_server.py:269-315` (_Handles: `handle()` mints a permanent number↔path pair for every distinct path, :276-285; entries are removed only by `removed()`/`renamed()` when the change goes *through this server*, :298-315)
- **Problem:** A LOOKUP of a file that is later deleted by Windows Explorer, a renamer, or another server leaves its pair in both dicts forever; a long-lived server browsed against a large, changing library accumulates one entry per path ever touched, unbounded. The same file shows the intended pattern twice: clients are capped at 256 with eviction (:887-889) and the dir cache is capped at 64 with a clear (:822-824) — the handle registry is the one table nobody capped.
- **Fix:** Cap the registry (e.g. 8,192 paths) and evict the oldest minted numbers past it, exactly like the clients dict does. An evicted live handle answers STALE, which is a defined NFS4 error the client already has to handle — the _Handles docstring (:261-267) designs for precisely that answer.
- **Benefit:** Long-uptime memory becomes bounded; no behaviour change inside the cap.
- **Effort:** S. **Risk:** Low. **Confidence:** High (READ)

## PS2SERVERS-048 / Startup: the LAN-IP detection runs a subprocess synchronously in _build, before first paint
- **Category:** 5 (Performance — startup)
- **Location(s):** `PS2-Servers/launcher/gui.py:1320` (`netinfo.best_lan_ip()`) and `:1327` (`netinfo.ip_choices()`), both inside LauncherApp._build (gui.py:1307), called from __init__ (:1027) before mainloop; the subprocess at `PS2-Servers/launcher/netinfo.py:70-80` (`ip`/`ifconfig`, timeout=3), reached via all_ipv4 (netinfo.py:107-119)
- **Problem:** Measured on this VM: all_ipv4() takes 0.030 s warm — but its worst case is the subprocess's 3-second timeout, and it runs on the GUI thread while the window is still being constructed, to populate a dropdown the user may never open. best_lan_ip() additionally does the UDP-connect probe to 8.8.8.8 (netinfo.py:84-93) on the same path. The codebase already knows better: _restore refuses to re-run all_ipv4() at startup because it "would block the startup path on getaddrinfo for nothing" (gui.py:3472-3474) — _build runs it before _restore is even reached.
- **Fix:** Build the header with the saved IP (or "detecting…") and fill the combo from an `after(200, …)` callback — the same deferral _apply_tab_minimum_width already uses (gui.py:1049). Delete nothing; move one call.
- **Benefit:** Worst-case ~3 s (est., the subprocess timeout) off time-to-first-paint on Linux/macOS; typical saving here measured at ~30 ms plus a getaddrinfo.
- **Effort:** S. **Risk:** Low. **Confidence:** High (READ + MEASURED typical case); the 3 s worst case is the documented timeout value, INFERRED as a possible delay, not observed

## PS2SERVERS-049 / Startup: ~0.4 s of imports happen before Tk exists, including subsystems the first screen never uses
- **Category:** 5 (Performance — startup) / 7
- **Location(s):** `PS2-Servers/launcher/gui.py:33` (wholesale import of directlink, ra_setup, ra_sounds, windows_setup, tray, elevate, posix_firewall, …); measured at this pin in this VM: `import launcher.main` 0.083 s, `import launcher.gui` +0.313 s, `import launcher.servers` alone 0.137 s
- **Problem:** Everything is imported eagerly — the direct-link network-administration stack (round-1 -025), the RA setup/sound modules (round-1 -003/-029), Windows-only setup — before `tk.Tk()` is even created (run_gui, gui.py:3778-3786), so every launch pays for features most launches never open. The file already demonstrates the alternative where it matters: library_panel is imported inside _build (gui.py:1414-1416 area, `from .library_panel import LibraryPanel`), ra_overview lazily at click time (gui.py:633).
- **Fix:** Apply the file's own lazy-import pattern to directlink and ra_setup/ra_sounds (move the names into the functions that use them). No new mechanism, no behaviour change.
- **Benefit:** est. a meaningful fraction of the measured 0.313 s gui import (the exact split per module was not measured — labelled INFERRED); the certain win is that a broken optional subsystem can no longer break launch by failing to import.
- **Effort:** S. **Risk:** Low. **Confidence:** High (READ + MEASURED total); Med for the per-module split (INFERRED)

## PS2SERVERS-050 / SMB2 FLUSH reports success when the flush/fsync failed — and is the one handler with no body-length check
- **Category:** 6 (Bug — correctness)
- **Location(s):** `PS2-Servers/smb2_server/smb2_server.py:1102-1112` (`except OSError: pass` around flush+fsync, then unconditional STATUS_SUCCESS); missing `len(body)` guard at :1103 (every other fixed-size handler has one — see coverage table §2)
- **Problem:** FLUSH is the call a client makes to learn its writes are durable; answering SUCCESS after the fsync raised tells the client the opposite of what happened, silently, for e.g. a removed USB drive or a full disk. The missing length check is today only a style break — a short body fails inside handle_from's struct.unpack and is converted by the dispatch catch-all — but FLUSH is thereby the one handler whose correctness depends on that net.
- **Fix:** Return the OSError's mapped status (`_status_for_oserror`, already used by dispatch) when flush/fsync fails, and add the same `len(body) < 24` guard CLOSE has. Two lines changed, no additions of mechanism.
- **Benefit:** A false durability answer removed.
- **Effort:** S. **Risk:** Low. **Confidence:** High (READ)

## PS2SERVERS-051 / SMB2: a single compound message can chain an unbounded number of requests
- **Category:** 5 (Performance) / 6 (Security)
- **Location(s):** `PS2-Servers/smb2_server/smb2_server.py:417-475` (_dispatch_all loop over NextCommand offsets; no counter, no work budget — bounded only by message size, which is itself uncapped per -045)
- **Problem:** One 16.7 MB frame can chain on the order of 10⁵ minimal requests, each dispatched synchronously on the connection's thread, each potentially a filesystem operation — one packet, minutes of work, while the client waits for the chained reply. NFS, in-repo, caps this exact shape twice: MAX_OPS = 128 per compound and a reply-size budget that stops after one MAX_IO of results (nfs4_server/nfs4_server.py:51, :575-583).
- **Fix:** Count chained requests in _dispatch_all and stop answering past 128 (NFS's house number) — treat the rest as a protocol error and close, as the signature-refusal path already does with conn.terminate.
- **Justification (addition):** One counter against a concrete amplification shape, using the sibling server's number and pattern.
- **Benefit:** Worst-case work per received frame bounded to 128 handler executions.
- **Effort:** S. **Risk:** Low (real compounds are 2–4 requests; Explorer's CREATE/QUERY_INFO/CLOSE is 3). **Confidence:** High (READ)

## PS2SERVERS-052 / GUI log pump: per-line string building + a Tk index() call per batch are the steady-state cost the verbose default (round-1 -019) multiplies
- **Category:** 5 (Performance)
- **Location(s):** `PS2-Servers/launcher/gui.py:3025-3040` (_drain_logs: 150 ms tick, 500-line cap), `:3042-3051` (_append_log: `widget.index("end-1c")` + parse on every batch, unconditional `see("end")` in the in-class copy), `:3053-3060` (_terminal_text: a newly prefixed string built for every line)
- **Problem:** Round 1 established that verbose per-request logging defaults **on** for six server cards; round 2 prices the receiving end: every line a server prints is re-built with a timestamp/tab prefix on the GUI thread, inserted into a Tk Text widget, and followed by a line-count index query — up to 500 times per 150 ms tick before the cap defers the rest to the next tick, so a busy server can keep the GUI thread saturated on log formatting indefinitely while the user is trying to click Stop. The shim's smarter _append_log (main.py, round-1 -007) still does the same index() per batch.
- **Fix:** This is -019's fix seen from the other end — default verbose off and the pump's cost collapses with it. If any part is kept: track the log's line count in Python (increment by the number of lines inserted) instead of asking Tk per batch, and skip _terminal_text entirely for the un-prefixed fast path… no — LESS: keep the code, change the default (-019). The index-per-batch can go when the shim is folded into the class under round-1 -006.
- **Benefit:** With -019 applied, steady-state GUI log work drops to near zero in the default configuration (est.; mechanism READ, saturation magnitude INFERRED — no Tk in this VM to profile).
- **Effort:** S (as part of -019/-006). **Risk:** Low. **Confidence:** High (READ) for mechanism; Med for magnitude (INFERRED)

## PS2SERVERS-053 / SMB2: handles per connection are uncapped (CREATE mints forever; descriptors follow on first read/write)
- **Category:** 5 / 6 — the SMB2 twin of -046
- **Location(s):** `PS2-Servers/smb2_server/smb2_server.py:248-251` (Connection.add_handle, no cap), handles dict :233; the backing fd is opened lazily by _open_file (:1114-1127), so the fd cost lands on first READ/WRITE per handle
- **Problem:** A client that CREATEs without CLOSE grows the connection's handle table without limit; each handle it then touches once becomes an open descriptor. SMB1's version of this is -046; NFS caps its equivalent at 4 cached files per connection and UDPFS caps handles at 64 per client — SMB2 is the only file server in the repo with no number at all.
- **Fix:** Refuse CREATE past 64 live handles per connection with STATUS_INSUFFICIENT_RESOURCES (the constant already exists, smb2_wire.py:141) — the UDPFS house cap, one comparison in add_handle's caller.
- **Justification (addition):** Same concrete exhaustion shape as -046, same in-repo precedent and number.
- **Benefit:** Per-connection handle/fd ceiling where none exists.
- **Effort:** S. **Risk:** Low. **Confidence:** High (READ) for the absence; Med for the cap value (INFERRED)

## PS2SERVERS-054 / Edge's binary size is measured against three different Go toolchains — the size gate is partly measuring the compiler
- **Category:** 7 (Build/CI)
- **Location(s):** `PS2-Servers/native/ps2servers-edge/go.mod:3` (`go 1.22`); release builds resolve the toolchain from that file (`PS2-Servers/.github/workflows/edge-build.yml:226-228`, go-version-file); the parity job pins **1.25** (`PS2-Servers/.github/workflows/edge.yml:196-198`); the size gate's own comment admits toolchain drift is worth ~1 MB per target (`PS2-Servers/.github/workflows/edge-build.yml:296-303`)
- **Problem:** Round 2 made this concrete: the same source built here with Go 1.27.4 produces a 7,622,780 B amd64 binary while CI quotes 6.19 MiB as its largest target (mipsle, older toolchain) — cross-arch differences explain part of that, but the workflow itself attributes ~1 MB to the toolchain alone. A 9 MiB ceiling that must "clear the toolchain this repo might move to" is a ceiling on the compiler, not the code.
- **Fix:** Pin one toolchain for Edge everywhere: raise the go.mod directive to the version CI actually tests with and let go-version-file do its job in every workflow (delete the parity job's separate 1.25 pin). The size gate then measures the product again, and -041's tag work becomes visible in it.
- **Benefit:** No bytes directly; the 9 MiB gate becomes a real regression alarm instead of a loose one (the workflow's own stated intent, edge-build.yml:293).
- **Effort:** S. **Risk:** Low. **Confidence:** High (READ + MEASURED locally)

## PS2SERVERS-055 / The SMB1↔SMB2 framing code exists twice (send_msg/recv_msg), despite the SMB2 module claiming it was "moved"
- **Category:** 2 (Duplication)
- **Location(s):** `PS2-Servers/smb2_server/smb2_wire.py:17-54` (docstring: send/recv "are lifted from smbv1_server/smbserver_opl.py rather than rewritten… this is the tested implementation, moved") vs the originals still present at `PS2-Servers/smbv1_server/smbserver_opl.py:179-206`
- **Problem:** "Moved" code that also stayed is two copies: -045's length ceiling now has to be fixed in both, and any future framing fix (partial-send handling, keep-alive semantics — recv_msg's three-way None/b""/bytes contract is subtle and documented in both) will drift exactly like the UDPFS copies did in round-1 -001. This is the smallest instance of the repo's signature disease, which is why it is worth listing: it is also the cheapest to cure.
- **Fix:** One framing module both servers import (SMB1 already imports across directories — nfs4_server imports smb2_paths, round-1 -018 — so the pattern is established); delete the SMB1 copy. If the SMB1 file must stay standalone-redistributable (the reason its Share class gives for not importing smb2_paths, smbserver_opl.py:263-296), say so in one comment and mark the pair as mirrors — but then -045 must land in both in the same commit.
- **Benefit:** est. −30 lines; one framing implementation to fix and test.
- **Effort:** S. **Risk:** Low. **Confidence:** High (READ)

---

## 7. Coverage / still unread (honest limits of this round)

- **Read in full:** smb2_server.py handlers + dispatch + wire (all lines of smb2_wire.py; smb2_server.py :180-1465 in full, rest sampled in round 1), nfs4_server.py in full, smbserver_opl.py :179-1550 in full (rest read in round 1), launcher/main.py in full, launcher/gui.py startup/log/status paths and all subprocess/threading call sites by grep + targeted reads, launcher/netinfo.py relevant functions, launcher/single_instance.py, ps2servers.py.
- **Executed, not just read:** the h2_* mini-SMB2 handlers, SMB1 short-params crash (three handlers), SMB2 SET_INFO→CLOSE deletion (full sequence against a temp share), Edge builds (3 shapes), Edge idle RSS, go vet, go test, netinfo/import timing in this VM.
- **Not done / still open:** per-opcode audit of **UDPFS and UDPBD** (round 1 covered their duplication and the write-exclusion bug; a handler-by-handler wire-validation table like §2–§4 has **not** been done for udpfs_server.py's opcode set or the Go udpfs/udpbd/smB handlers — the Go side was tested only via its own test suite, which the sandbox's UDP block prevented from running live); SMB2's srvsvc RPC internals (smb2_srvsvc.py) beyond the IOCTL/pipe surface; smb2_spnego/smb2_auth parsing (round-1 -014 territory); GUI profiling with a real Tk (none in this VM — Category 5 GUI magnitudes are mechanism-READ, magnitude-INFERRED); Edge CHD build (libchdr absent, §1); Edge on any non-amd64 target; anything requiring a console or live UDP (sandbox blocks sendto).
- **Secrets:** none found, none printed. Round-1 -040 (docstring literal at smb2_server.py:44) is not repeated, per instructions.
- **Repo state:** `git status` clean at `c5f021c4d386` at the end of the round; all artefacts are in /tmp and ~/workspace/tools.
