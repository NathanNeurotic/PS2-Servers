# Audit Report: PS2-Servers

- **Repository**: `NathanNeurotic/PS2-Servers`
- **Audited commit**: `c5f021c4`
- **Audit principle**: Less, not more. Every recommendation aims to reduce code size, duplicate logic, startup/RAM overhead, thread/UI blocking, or protocol/runtime bugs.

---

## Verification Run Scope and Observed Results

Checks run directly on the clean `PS2-Servers` clone (`c5f021c4`) on Windows (`Python 3.14`, Go test suite):

- `python tools/version_source.py --check-sources`: exited `0` (`version check passed: no workflow carries its own version literal`).
- `python tools/version_source.py`: exited `0` (`0.9.4`) and `python tools/version_source.py --print-display`: exited `0` (`0.9.4`).
- `python tools/verify_docs.py`: exited `0` (`Verification finished. Total errors: 0`).
- `python tools/check_release_compliance.py`: exited `0` (`Release compliance check passed.`).
- `python .github/scripts/check_ai_attribution.py --self-test`: exited `0` (`Attribution validator self-test passed (14 cases).`).
- `python udpbd_server/selftest.py`: exited `0` (`ALL UDPBD TESTS PASSED`).
- `python udpfs_server/compression_selftest.py`: exited `0` (`ALL UDPFS COMPRESSION TESTS PASSED`).
- `python udpfs_server/pathguard_selftest.py`: exited `0` (`ALL UDPFS PATH-GUARD TESTS PASSED`).
- `python tools/check_desktop_startup.py`: exited `2` when invoked without its required `executable` argument (verified at `5179b4b`; not tested against a frozen executable).
- `go test -count=1 ./...` in `native/ps2servers-edge`: exited `0`; 10 packages passed (`cmd/ps2servers-edge`, `internal/compression`, `internal/filesystem`, `internal/memory`, `internal/protocol`, `internal/sessionstats`, `internal/smb`, `internal/udpbd`, `internal/udpfs`, `internal/webui`), 3 packages reported `[no test files]` (`internal/logging`, `internal/session`, `internal/statuslistener`).
- `python -m unittest discover -s tests -p "test_*.py"`: ran `1556` tests in `215.218s`, exited `1` (`failures=7, errors=46, skipped=41`):
  - `test_desktop_instance` (`2` failures): `ModuleNotFoundError: No module named 'launcher'`.
  - `test_udpfs_symlink_path_ops` (`4` failures): `-2 != -20` and `0 != 14` on Windows.
  - `test_nfs4_server` (`1` failure).
  - `test_edge_flag_exposure` (`2` errors).
  - `test_udpfs_handoff_retried_write` (`6` errors) and `test_udpfs_write_needs_writable_handle` (`38` errors).

---

## Cross-Repo Notes

- Shared findings spanning `PS2-Servers` and the PlayStation 2 console repositories are consolidated once in `Gemini-SUMMARY.md`: `CROSS-010` (`20,190 B` of duplicated RetroAchievements `xerabora`/`rcheevos` `md5.h` fixtures shared with `RiptOPL`) and `CROSS-011` (byte-identical `AGENTS.md` and `CLAUDE.md` policy files across four repositories).

---

## Findings (Sorted by Benefit)

### PS2SERVERS-001
- **Title**: Edge WebUI JSON field names and form serialization break UDPFS config saves and truncate fractional delays
- **Category**: 6 (Bugs and security)
- **Location(s)**:
  - `PS2-Servers/native/ps2servers-edge/internal/webui/assets/index.html:115-119, 180`
  - `PS2-Servers/native/ps2servers-edge/internal/webui/assets/app.js:153-160, 177-179, 201-208`
  - `PS2-Servers/native/ps2servers-edge/internal/webui/config.go:15, 19, 29, 41, 53, 238`
  - `PS2-Servers/native/ps2servers-edge/internal/webui/schema.go:111, 115`
- **Problem**: `index.html` names the UDPFS protocol selector `protocol_mode` with capitalized values (`Auto`, `Standard`, `Modulo`) while `EdgeConfig` and `schema.go` expect `protocol` with lowercase values (`auto`, `standard`, `modulo`); saving from the WebUI replaces `fullConfig["udpfs"] = data` missing `protocol`, so `ValidateConfig` rejects saves. Additionally, `app.js` parses `tx_delay_ms` with `parseInt(val, 10)` instead of `parseFloat`, truncating fractional millisecond delays (`float64` in Go), and `index.html` omits `session_summary`, resetting `session_summary: true` to `false` on save.
- **Fix**: Rename the HTML `<select>` and option values in `index.html` to `protocol` and `auto`/`standard`/`modulo`, parse `tx_delay_ms` with `parseFloat` in `app.js`, add the `session_summary` checkbox or merge `data` into `fullConfig[service]` instead of overwriting unknown fields.
- **Justification**: Fixes broken WebUI configuration saves, precision loss, and silent setting resets with net zero architectural weight.
- **Benefit**: Fixes complete WebUI save failure for UDPFS, prevents fractional `tx_delay_ms` truncation, stops resetting `session_summary`.
- **Effort**: S. **Risk**: Low. **Confidence**: High (`READ`).

### PS2SERVERS-002
- **Title**: Collapse redundant and experimental retroachievements runtimes or isolate optional C/Python bridge (~3,700+ lines + native probe)
- **Category**: 8 (Half-finished or redundant features) & 4 (Size and memory)
- **Location(s)**:
  - `PS2-Servers/launcher/ra_*.py` (17 modules, including `PS2-Servers/launcher/ra_evaluator.py`, `PS2-Servers/launcher/ra_receiver.py`, `PS2-Servers/launcher/ra_resolver_process.py`, `PS2-Servers/launcher/ra_account_worker.py`, `PS2-Servers/launcher/ra_web.py`, `PS2-Servers/launcher/ra_credentials.py`, `PS2-Servers/launcher/ra_http.py`, `PS2-Servers/launcher/ra_set_client.py`)
  - `PS2-Servers/native/ra_watch_probe.c:1-373`
  - `PS2-Servers/launcher/caduceus.py:1-411`
  - `PS2-Servers/conformance/ra_dependency/`
  - `PS2-Servers/vendor/retroachievements/`
- **Problem**: The repo maintains two RetroAchievements runtimes side by side: the bundled `ps2ra` (`xerabora` + `caduceus.py`) and an opt-in `--owned-runtime` across 17 `ra_*.py` modules (~3,700+ lines) plus `ra_watch_probe.c` (`373` lines). Each `AccountWorker` task spawns a fresh Python interpreter via `multiprocessing.get_context("spawn")`, enlarging frozen Nuitka/PyInstaller bundles, runtime memory, and CI build surface (`build/build_achievements.py`, `build/build_ra_evaluator.py`).
- **Fix**: Pick one RetroAchievements backend (either complete migration to the owned runtime and remove `xerabora`/`caduceus.py`, or drop the 17-file experimental `--owned-runtime` if `ps2ra` remains primary), and run account/hash work on a worker thread or persistent worker instead of spawning fresh Python processes per task.
- **Benefit**: Removes ~1,000-3,700+ lines of duplicate runtime code, simplifies Windows packaging, eliminates per-task process-spawn latency and multi-MB RAM spikes.
- **Effort**: L. **Risk**: Med. **Confidence**: High (`READ`).

### PS2SERVERS-003
- **Title**: Unify duplicated game library SQLite stacks, catalogs, setup guides, and disc readers
- **Category**: 2 (Duplication) & 8 (Half-finished or redundant features)
- **Location(s)**:
  - `PS2-Servers/launcher/library_panel.py:1-561`
  - `PS2-Servers/launcher/library_catalog.py:1-642`
  - `PS2-Servers/launcher/library_gui.py:1-446`
  - `PS2-Servers/launcher/game_library.py:1-497`
  - `PS2-Servers/launcher/library_index.py:1-190`
  - `PS2-Servers/launcher/setup_guide.py:1-119`
  - `PS2-Servers/launcher/disc_identity.py:66-184`
  - `PS2-Servers/launcher/ps1_vcd.py:20-149`
  - `PS2-Servers/launcher/ra_compressed_hash.py`
  - `PS2-Servers/launcher/ps1_index.py:1-147`
- **Problem**: Two separate game library UIs and SQLite schemas (`library_panel.py` + `library_catalog.py` using `catalog.sqlite3` with a 5-step guide vs `library_gui.py` + `game_library.py` using `achievement-catalog.sqlite3` plus `ps1_index.py` and `.sqlite3` pre-import backup files at `game_library.py:210-211`) are bridged by `library_index.py`, while `setup_guide.py` duplicates onboarding instructions and `disc_identity.py` duplicates ISO9660/`SYSTEM.CNF` reading also implemented in `ps1_vcd.py` and `ra_compressed_hash.py`. Additionally, `game_library.py:245-256` follows symlinks when scanning installed titles (`installed()`).
- **Fix**: Consolidate onto one SQLite library store and one setup guide panel, delete the redundant bridge and `.sqlite3` backup file creation, share one ISO9660/`SYSTEM.CNF` reader helper, and skip symlinks (or verify containment inside the library root) in `installed()`.
- **Benefit**: Removes ~1,000+ lines of UI, SQLite schema, and parser code, eliminates duplicate disk scans and backup writes.
- **Effort**: L. **Risk**: Med. **Confidence**: High (`READ`).

### PS2SERVERS-004
- **Title**: Remove ~175 lines of dead SMB2/3 stubs in `smbv1_server` and extract shared SMB/NetInfo helpers
- **Category**: 1 (Dead code) & 2 (Duplication) & 6 (Bugs and security)
- **Location(s)**:
  - `PS2-Servers/smbv1_server/smbserver_opl.py:168-172, 179-206, 508-638, 652-666, 783, 960-967, 1015-1021, 1114-1125, 1259, 1278, 1435-1454, 1497-1501, 1532-1647, 1692-1694, 1724-1728`
  - `PS2-Servers/smb2_server/smb2_wire.py:23-54, 225-229`
  - `PS2-Servers/smb2_server/smb2_server.py:76-124, 1466-1650`
  - `PS2-Servers/launcher/servers.py:430-459`
  - `PS2-Servers/launcher/netinfo.py:84-234`
  - `PS2-Servers/smb2_server/smb2_paths.py:7-21` (stale docstring)
- **Problem**: `smbserver_opl.py` retains ~175 lines of dead SMB2/3 stubs (`SMB2_HANDLERS`, `h2_*`) even though `launcher/servers.py` dispatches SMB2/3 to `smb2_server.py`. Both servers duplicate wire time/filetime helpers (`smbserver_opl.py:168-206` vs `smb2_wire.py:23-54, 225-229`), network discovery (`smbserver_opl.py:1629-1647` vs `smb2_server.py:76-124` vs `launcher/netinfo.py`), and `take_445` Windows driver management where `smbv1_server` lacks `srv`/`srv2`/`srvnet` handling present in `smb2_server.py`. `smbserver_opl.py` also performs unchecked `struct.unpack_from` calls outside `try/except (ConnectionError, OSError)` that crash the client thread on truncated packets and fails mixed-case share lookup (`shares.get(share_name.lower())` vs `casefold()`).
- **Fix**: Delete `SMB2_HANDLERS` and all `h2_*` stubs from `smbserver_opl.py`; extract one shared helper module for filetime conversion, LAN IP discovery, and `take_445`; wrap packet unpacking in `struct.error` checks and normalize share names consistently with `casefold()`.
- **Justification**: Consolidating 3 copies of `take_445`/NetInfo/filetime removes net lines while fixing `srv`/`srv2`/`srvnet` drift in SMBv1.
- **Benefit**: Removes ~300+ lines of dead/duplicated Python code and prevents client thread crashes on malformed SMBv1 packets.
- **Effort**: M. **Risk**: Low. **Confidence**: High (`READ`).

### PS2SERVERS-005
- **Title**: SMB2 server directory symlink escape, stale session validation, open-handle rename failure on Windows, and signed EOF unpack
- **Category**: 6 (Bugs and security)
- **Location(s)**:
  - `PS2-Servers/smb2_server/smb2_server.py:252-270, 665-667, 1097, 1123, 1201, 1204-1207, 1248-1250, 1327-1331, 1354`
  - `PS2-Servers/launcher/servers.py:178`
- **Problem**: Multiple protocol/security defects in `smb2_server.py`: `h_query_directory` (`1201, 1248-1250`) stats symlink targets inside `h.path` without verifying `os.path.realpath(target)` stays inside the share, `Connection` never enforces `hdr.session_id == conn.session_id` after `SESSION_SETUP` (`252-270, 665-667`), `h_close` `POSTQUERY_ATTRIB` (`1097`) ignores `h.share.read_only`, `FILE_END_OF_FILE_INFORMATION` (`1327-1331`) unpacks signed `<q` allowing negative offsets, and `FILE_RENAME_INFORMATION` (`1354`) calls `os.replace(h.path, dest)` while `h.fh` is still open (fails with sharing violation on Windows). Also `launcher/servers.py:178` lacks an empty check on `v["games_folder"]`.
- **Fix**: Verify `share._inside(os.path.realpath(target))` in `h_query_directory`, check `hdr.session_id == conn.session_id` on authenticated commands, include `FILE_ATTRIBUTE_READONLY` in `h_close` when `h.share.read_only`, validate non-negative `eof` before truncating, close and reopen around `os.replace` on rename, and validate `games_folder` non-empty.
- **Justification**: Fixes real SMB2 symlink escape, Windows rename failures, and session validation gaps.
- **Benefit**: Fixes 5 SMB2 protocol/security bugs across Windows and POSIX hosts.
- **Effort**: S. **Risk**: Low. **Confidence**: High (`READ`).

### PS2SERVERS-006
- **Title**: Edge Go SMB handler reopens file per write chunk (TOCTOU symlink race) and skips containment in directory listing
- **Category**: 6 (Bugs and security) & 5 (Performance)
- **Location(s)**:
  - `PS2-Servers/native/ps2servers-edge/internal/smb/handlers.go:306-312, 338-347, 359, 489-495, 645-719, 873-884`
- **Problem**: `ntCreateAndX` closes a newly opened writable handle and re-opens it read-only (`os.Open(local)`), forcing `writeAndX` (`489-495`) to `os.OpenFile(of.path, os.O_WRONLY, 0)` and `Close()` on every single write chunk. Besides extra syscall overhead per packet, this creates a TOCTOU symlink race on `of.path`, omits checking `written != len(payload)` on `WriteAt`, and leaves `of.size` stale. Separately, `findFirst2`/`readDirNames` calls `os.Stat(localPath)` without passing entries through `sh.Root.Resolve` to keep symlinks inside the share root, and only trims trailing `\\*`/`*`.
- **Fix**: Keep the file descriptor opened with the requested access mode in `openFile` instead of close/re-opening per chunk, check full byte count on `WriteAt` and update `of.size`, and validate symlink containment through `sh.Root.Resolve` in `readDirNames`.
- **Justification**: Keeping one open descriptor removes per-chunk syscalls while fixing the TOCTOU symlink race, short-write accounting, and directory-listing traversal bugs.
- **Benefit**: Eliminates two extra syscalls per SMB write packet, fixes TOCTOU symlink and directory-traversal escapes, and fixes stale size/short-write accounting.
- **Effort**: S. **Risk**: Low. **Confidence**: High (`READ`).

### PS2SERVERS-007
- **Title**: Simplify 4-layer Tkinter skinning and MetaPathFinder chain
- **Category**: 3 (Overengineering) & 2 (Duplication) & 4 (Size and memory)
- **Location(s)**:
  - `PS2-Servers/launcher/__init__.py:14-92`
  - `PS2-Servers/launcher/main.py:107-409`
  - `PS2-Servers/launcher/asset_skin.py:39-164`
  - `PS2-Servers/launcher/full_skin_controls.py:9-204`
- **Problem**: GUI theming spans four layers (`MetaPathFinder` in `__init__.py`, monkey-patched widget factories in `main.py`, `asset_skin.py` re-configuring the same 22 `ttk` styles already configured in `main.py:164-262`, and `full_skin_controls.py`). Using `sys.meta_path` import hooks and duplicate style tables makes startup harder to trace and doubles style setup work.
- **Fix**: Configure the `ttk.Style` theme once using direct helper functions in one styling module and remove the `MetaPathFinder` hook and duplicate style-configuration block in `asset_skin.py`.
- **Benefit**: Removes ~250-350 lines of indirection and eliminates duplicate `ttk` theme initialization on startup.
- **Effort**: M. **Risk**: Med. **Confidence**: High (`READ`).

### PS2SERVERS-008
- **Title**: Edge UDPFS hot read loop allocates 5 slices per DATA packet and caches only one batch writer across two sockets
- **Category**: 5 (Performance) & 4 (Size and memory)
- **Location(s)**:
  - `PS2-Servers/native/ps2servers-edge/internal/udpfs/transfer.go:188-200`
  - `PS2-Servers/native/ps2servers-edge/internal/udpfs/negotiation.go:269-275`
- **Problem**: Every outgoing UDPFS DATA packet in `transfer.go` performs ~5 separate slice allocations/copies (`append([]byte(nil), ...)` plus `pad4` and header wrappers), creating heavy GC pressure under sustained ISO streaming on low-power router targets (OpenWrt MIPS/ARM). Additionally, `negotiation.go:269-275` caches only a single `s.batchWriteConn` while the server uses both `s.discovery` and `s.data` sockets.
- **Fix**: Reuse a preallocated packet scratch buffer on the session/chunk writer to assemble header + aligned payload in one write, and cache batch writers separately per socket (or bind batch writer directly to `s.data`).
- **Benefit**: Eliminates 5 heap allocations per 1-4 KB packet during game streaming and fixes batch writer thrashing between discovery and data sockets.
- **Effort**: S. **Risk**: Low. **Confidence**: High (`READ`).

### PS2SERVERS-009
- **Title**: Launcher UI thread blocks on IP refresh, server shutdown (up to 20s), dependency checks, and repeated `config.load()`/`_portable_dir()` probe writes
- **Category**: 5 (Performance)
- **Location(s)**:
  - `PS2-Servers/launcher/gui.py:270, 1502-1514, 1781-1787, 2748-2756, 2961-3013, 3533`
  - `PS2-Servers/launcher/config.py:13, 32-49`
  - `PS2-Servers/launcher/release_metadata.py:5`
  - `PS2-Servers/launcher/dependency_panel.py:43-49, 72`
  - `PS2-Servers/launcher/raw_storage.py:289, 309-310, 352, 409`
  - `PS2-Servers/launcher/optional_deps.py:178-186, 273`
- **Problem**: Multiple expensive operations run synchronously or redundantly: `stop_server`/`stop_all` blocks the Tkinter UI thread for up to `TAKE_445_STOP_GRACE_SECONDS = 20.0`, `_refresh_ips` and `dependency_panel.refresh_status` block UI redraws, `gui.py:1502-1514` loads and saves config on every notebook tab change, `config._portable_dir()` writes and deletes a probe file on every call, `open_raw_device` invokes PowerShell `_metadata()` (`Get-Disk`/`Get-Partition`/`Get-Volume`) twice (while `Get-Volume` skips unlettered volumes), `optional_deps.py:178-186` loads `libchdr` into the GUI process permanently, and `optional_deps.py:273` omits `CREATE_NO_WINDOW` when spawning `install_lz4`. Also config dir names disagree (`"PS2-Servers"` in `config.py:13` vs `"PS2 Servers"` in `release_metadata.py:5`).
- **Fix**: Move server stop waits and IP/dependency probes off the UI thread, cache `_portable_dir()` once at startup instead of probing disk on every call, drop tab-switch `load()`/`save()` writes, call `_metadata()` once in `open_raw_device`, remove duplicate trivial wrappers in `dependency_panel.py`, add `CREATE_NO_WINDOW` on Windows for `install_lz4`, and align the app config directory constant.
- **Benefit**: Eliminates up to 20s UI freezes, removes repeated disk writes and redundant PowerShell process launches.
- **Effort**: M. **Risk**: Low. **Confidence**: High (`READ`).

### PS2SERVERS-010
- **Title**: NFSv4 server serializes all operations behind one coarse lock and fsyncs on every write RPC
- **Category**: 5 (Performance) & 6 (Bugs and security)
- **Location(s)**:
  - `PS2-Servers/nfs4_server/nfs4_server.py` (`self.fs_lock` across `_compound`, `_op_write` `flush()` + `os.fsync()`, and `_op_rename` at `1026-1030`)
- **Problem**: A single `self.fs_lock` serializes every `_compound` operation across clients including blocking disk reads and per-`_op_write` `os.fsync()`, stalling concurrent transfers (e.g. PS2 reads vs directory listings). In addition, `_op_rename` (`1026-1030`) performs `os.rmdir(dst); os.rename(src, dst)` which is non-atomic and can destroy `dst` if `rename` fails.
- **Fix**: Limit `fs_lock` strictly to open-handle/state table mutations rather than wrapping disk I/O, honor the NFSv4 `stable_how` flag (`UNSTABLE4` skips per-write `os.fsync()` until `COMMIT`), and replace `os.rmdir`/`os.rename` with `os.replace` where permitted.
- **Benefit**: Unblocks concurrent NFSv4 reads/writes, removes per-write `fsync` stalls, and fixes non-atomic rename data-loss hazard.
- **Effort**: M. **Risk**: Med. **Confidence**: High (`READ`).

### PS2SERVERS-011
- **Title**: Live SSE log stream skips newline/CR sanitization in Edge WebUI and broadcasts shared `threading.Event` race in RA WebUI
- **Category**: 6 (Bugs and security)
- **Location(s)**:
  - `PS2-Servers/native/ps2servers-edge/internal/webui/api.go:286, 293, 299, 316`
  - `PS2-Servers/native/ps2servers-edge/internal/webui/logbuffer.go:29`
  - `PS2-Servers/launcher/ra_web.py:142-145, 385-393, 506-511`
  - `PS2-Servers/launcher/ra_receiver.py:166-173`
- **Problem**: `internal/webui/api.go:316` streams live log lines without calling `sanitizeSSE(line)` (even though snapshot replay lines `286, 293, 299` call it), and `logbuffer.go:29` splits lines only on `\n` (leaving `\r` intact). In the Python RA WebUI (`ra_web.py:385-393`), `container.innerHTML` interpolates unescaped `a.title`/`a.description` strings (DOM XSS), while `142-145, 506-511` shares one `threading.Event` across all SSE clients, calling `.clear()` racily and ignoring `wait()` while pushing on a 1.0s loop. Meanwhile `ra_receiver.py:166-173` lets any unauthenticated UDP sender lock `owner` and redirect replies to `advertised_replies` IP:port.
- **Fix**: Pass live SSE lines through `sanitizeSSE(line)` and strip `\r` in `logbuffer.go`; construct RA achievement DOM nodes via `textContent` instead of `innerHTML` and use per-client notification or a condition variable; restrict UDP owner binding/reply steering.
- **Benefit**: Fixes SSE frame injection, DOM XSS, dropped wakeup signals, and unauthenticated UDP reply redirection.
- **Effort**: S. **Risk**: Low. **Confidence**: High (`READ`).

### PS2SERVERS-016
- **Title**: `--compression-cache-size 0` fails to disable the decompressed-block cache in `ps2servers-edge`, and its test never calls `openImage`
- **Category**: 6 (Bugs and security) & 8 (Half-finished or redundant features)
- **Location(s)**:
  - `PS2-Servers/native/ps2servers-edge/internal/udpfs/blockdev.go:394-403`
  - `PS2-Servers/native/ps2servers-edge/internal/udpfs/server.go:59-60`
  - `PS2-Servers/native/ps2servers-edge/cmd/ps2servers-edge/main.go:156, 188-192`
  - `PS2-Servers/native/ps2servers-edge/internal/webui/schema.go:122`
  - `PS2-Servers/native/ps2servers-edge/internal/compression/image.go:140-149`
  - `PS2-Servers/native/ps2servers-edge/internal/udpfs/blockdev_test.go:256-278`
- **Problem**: Every configuration surface documents `0` as disabling the per-image decompressed-block cache (`schema.go:122`, `compression/image.go:140-142`, and `main.go:188-192`), yet `Config.CompressionCache` (`server.go:60`) is a plain `int` whose `0` collides with Go's zero-value for unset, causing `openImage` (`blockdev.go:398-402`) to replace `cache == 0` with `compression.DefaultCacheBlocks` (`32`). Meanwhile `TestCompressionCacheDefaultsAndDisables` (`blockdev_test.go:256-278`) never calls `openImage` and leaves dead `var _ = session.Pending` at line `278`.
- **Fix**: Separate "unset" from explicit `0` (for example default `Config.CompressionCache` in `New` when negative or carry a pointer/`DisableCompressionCache` flag and pass `0` straight to `compression.OpenCached`), test `openImage` directly in `TestCompressionCacheDefaultsAndDisables`, and delete `var _ = session.Pending`.
- **Justification**: Restores the documented zero-cache low-memory mode on routers without changing external CLI/WebUI options.
- **Benefit**: Fixes broken zero-cache router mode, false-confidence test coverage, and dead statement.
- **Effort**: S. **Risk**: Low. **Confidence**: High (`READ`).

### PS2SERVERS-017
- **Title**: Core Python UDPFS errno divergences from `reference` and `edge` are codified as test exemptions in `udpfs_parity.py`
- **Category**: 6 (Bugs and security) & 8 (Half-finished or redundant features)
- **Location(s)**:
  - `PS2-Servers/conformance/integration/udpfs_parity.py:515-520, 646-652`
  - `PS2-Servers/udpfs_server/udpfs_server.py:1186-1187, 1802-1818, 2323-2353`
  - `PS2-Servers/native/ps2servers-edge/internal/udpfs/operations.go:193-196, 297-369, 788-807`
  - `PS2-Servers/native/ps2servers-edge/internal/udpfs/transfer.go:233-244`
- **Problem**: `udpfs_parity.py` encodes two exemptions for `udpfs_server.py` where its wire errors disagree with `reference` (`udpfsd`) and `edge` (`ps2servers-edge`): with `--root-dir` omitted, `udpfs_server.py` returns `-errno.EACCES` (`-13`) instead of `-errno.ENOENT` (`-2`) on `OPEN`/`GETSTAT`; on `GETSTAT /NAMESPACE/Corrupt.cso.iso`, it returns `-errno.ENOENT` (`-2`) instead of `-errno.EIO` (`-5`) when `_get_compressed_stat` returns `None`.
- **Fix**: Return `-errno.ENOENT` in `udpfs_server.py` when `self.root_dir is None` and `-errno.EIO` when a resolved compressed image fails header parsing during `GETSTAT`, then delete the `core` exemptions from `udpfs_parity.py`.
- **Justification**: One-line error-code fixes align all three server implementations and allow deleting special-case parity overrides.
- **Benefit**: Restores wire-errno parity across all three UDPFS servers and removes test exemptions.
- **Effort**: S. **Risk**: Low. **Confidence**: High (`READ`).

### PS2SERVERS-012
- **Title**: Stale OpenWrt package version, mismatched UCI port defaults, and broken default systemd WebUI unit
- **Category**: 7 (Build/CI/repo clutter) & 6 (Bugs and security)
- **Location(s)**:
  - `PS2-Servers/packaging/openwrt/Makefile:4`
  - `PS2-Servers/packaging/openwrt/files/ps2servers-edge.init:123, 172`
  - `PS2-Servers/packaging/openwrt/files/ps2servers-edge.config:47, 63`
  - `PS2-Servers/packaging/systemd/ps2servers-edge-webui.env:4`
  - `PS2-Servers/packaging/systemd/ps2servers-edge@.service:2, 15, 36, 45, 48`
  - `PS2-Servers/packaging/systemd/ps2servers@.service:5-7`
  - `PS2-Servers/tools/version_source.py:1-158`
- **Problem**: `packaging/openwrt/Makefile:4` hardcodes `PKG_VERSION:=0.8.2` while the repo is at `v0.9.4` (unchecked by `version_source.py`). `ps2servers-edge.init:123, 172` defaults omitted `smb.status_port` and `udpbd.status_port` to `-1` (colliding on status listener `0xF5F6`), contradicting `ps2servers-edge.config:47, 63`, `schema.go`, and `DefaultConfig()`. On systemd, `ps2servers-edge-webui.env:4` binds `0.0.0.0` with no password or `--insecure` (failing `webui.go:98-100` startup), and `ps2servers-edge@.service` sets `User=ps2edge`, `ProtectSystem=strict`, `ReadWritePaths=-/srv/ps2`, `NoNewPrivileges=true`, making the WebUI's Save and Restart actions fail; `ps2servers@.service:5-7` also claims Edge lacks SMB support.
- **Fix**: Wire `packaging/openwrt/Makefile` into `tools/version_source.py` and bump `0.9.4`; align `ps2servers-edge.init` default `status_port` to `0` (`disabled`) matching `.config`; default `ps2servers-edge-webui.env` to `127.0.0.1` or document required `--insecure`/password alongside writable config paths; update stale comment in `ps2servers@.service`.
- **Benefit**: Stops shipping out-of-date OpenWrt package metadata, prevents UDPFS/SMB/UDPBD status-port collisions on OpenWrt, and fixes out-of-the-box systemd WebUI startup.
- **Effort**: S. **Risk**: Low. **Confidence**: High (`READ`).

### PS2SERVERS-013
- **Title**: Remove duplicate binary/doc assets, planning handoffs, and stale repository refs
- **Category**: 7 (Build/CI/repo clutter) & 2 (Duplication)
- **Location(s)**:
  - `PS2-Servers/ps2serversgithubbanner.png` == `PS2-Servers/docs/assets/ps2servers-banner.png` (`571,581 B`)
  - `PS2-Servers/docs/.nojekyll` == `PS2-Servers/native/ps2servers-edge/internal/webui/assets/.gitkeep` (`0 B`)
  - `PS2-Servers/docs/ISSUE-249-HANDOFF.md` (`166` lines)
  - `PS2-Servers/docs/PLAN-NBD-AND-PS2CLIENT.md` (`373` lines)
  - `PS2-Servers/docs/NETWORK-TOOLS-ROADMAP.md` (`233` lines)
  - `PS2-Servers/docs/RA-OWNED-RUNTIME.md`, `PS2-Servers/docs/RA-UPSTREAM-AUDIT.md`
  - Remote Git refs (`c5f021c4` snapshot): `161` non-`main` remote branches (out of `162` total, including `codex/*`, `claude/*`, `fix/*`, `feat/*`, `backup/*`, `release/*`, `release-trigger/*`, `release-repair/*`) and `83` rolling `main-*` tags (among `120` unique remote tags)
- **Problem**: The repo stores two identical copies of the 571 KB PNG banner (`ps2serversgithubbanner.png` and `docs/assets/ps2servers-banner.png`) alongside committed AI handoff/roadmap scratch docs (`ISSUE-249-HANDOFF.md`, `PLAN-NBD-AND-PS2CLIENT.md`, `NETWORK-TOOLS-ROADMAP.md`), plus 161 stale non-`main` branches and 83 rolling `main-*` tags.
- **Fix**: Delete the duplicate root `ps2serversgithubbanner.png` (update root `README.md` image link to `docs/assets/ps2servers-banner.png`), remove completed handoff docs, and prune merged/stale branches and rolling `main-*` tags.
- **Benefit**: Removes ~572 KB binary bloat, ~800+ lines of stale planning docs, and 244 stale git refs.
- **Effort**: S. **Risk**: Low. **Confidence**: High (`READ`).

### PS2SERVERS-014
- **Title**: Duplicated helper functions across launcher modules (`_NoRedirect`, DPAPI credentials, `is_frozen`/`libchdr`, `.bak` hint, redundant UI toggle, Edge minor bloat)
- **Category**: 2 (Duplication) & 1 (Dead code) & 3 (Overengineering)
- **Location(s)**:
  - `PS2-Servers/launcher/ra_http.py:21-24`, `PS2-Servers/launcher/caduceus.py:26-33`, `PS2-Servers/launcher/art_fetch.py:71-78`, unused import in `PS2-Servers/launcher/ra_set_client.py:7`
  - `PS2-Servers/launcher/ra_credentials.py:31-58` == `PS2-Servers/launcher/caduceus.py:55-89` (DPAPI encrypt/decrypt)
  - `PS2-Servers/launcher/optional_deps.py` vs `PS2-Servers/udpfs_server/compressed_iso/chd.py` (`is_frozen` / `libchdr` loader)
  - `PS2-Servers/launcher/servers.py:480-489` (redundant `enforce_modulo` checkbox alongside `protocol_mode` dropdown)
  - `PS2-Servers/launcher/riptopl_profile.py:224` (suggests `.bak` backups)
  - `PS2-Servers/launcher/game_library.py` (`repair_cover`/`download`) & `PS2-Servers/launcher/achievement_loader.py:34-39` (unbounded HTTP redirects without size/timeout bounds or sharing)
  - `PS2-Servers/native/ps2servers-edge/internal/webui/config_uci.go:14-21, 63-163, 310-316` (16 inline `val == "1" || val == "true"` instead of existing `uciBool`; ~38 sequential `/sbin/uci` spawns on save)
  - `PS2-Servers/native/ps2servers-edge/internal/webui/service.go:173-177` (`openwrtManager.ServiceLogs(lines int)` ignores `lines` parameter, running unbounded `logread`)
  - `PS2-Servers/native/ps2servers-edge/internal/webui/api.go:142-146, 452-461` + `assets/app.js:269-276` (`/api/info` duplicates `system` metadata already in `/api/status`)
  - `PS2-Servers/native/ps2servers-edge/internal/webui/webui.go:192, 207` (`browseRoots` captured at startup, stale after saving new share/root paths)
  - `PS2-Servers/native/ps2servers-edge/internal/udpfs/pathops.go:45-57` (package globals `removeRefusesReadOnly`, `removeTriesBoth` instead of struct config fields)
  - `PS2-Servers/native/ps2servers-edge/internal/compression/chd_cuebin_enabled.go:21, 205-215` (unused constant `cdBinCacheBytes`, duplicated LRU cache from `image.go:97-135`), `image.go:199, 331` (duplicate bounds check)
  - `PS2-Servers/native/ps2servers-edge/internal/udpbd/server.go:83-85` (redundant zero loop)
  - `PS2-Servers/native/ps2servers-edge/internal/smb/server.go:244-256` (custom `itoa` instead of `strconv.Itoa`)
  - `PS2-Servers/native/ps2servers-edge/internal/protocol/status.go:110` (`"six-byte"` typo in comment)
- **Problem**: Small duplicated utilities, unused constants/helpers, and minor WebUI inconsistencies accumulated across Python launcher modules and the Go Edge service (`_NoRedirect`, DPAPI wrappers, `libchdr` loading, redundant `enforce_modulo` toggle, custom `itoa`, unused `cdBinCacheBytes`, ignored `ServiceLogs(lines)`, redundant `/api/info` endpoint, stale `browseRoots`, package-global pathops flags, and 16 copies of inline UCI boolean parsing plus 38 sequential `uci set` forks).
- **Fix**: Delete unused `cdBinCacheBytes`, custom `itoa`, redundant `enforce_modulo`, zero-init loop, `.bak` advice, and `/api/info`; use `strconv.Itoa` and `uciBool` across all UCI bool fields (and batch `uci` edits via `uci batch`); pass `lines` to `logread` (`-l`) or truncate tail; refresh `browseRoots` from live config; extract one shared `_NoRedirect`/bounded fetch and DPAPI helper if both RA paths remain.
- **Benefit**: Removes ~250-400 lines across Go and Python, speeds up OpenWrt config saves (38 forks -> 1 `uci batch`), and prevents stale WebUI directory browsing.
- **Effort**: M. **Risk**: Low. **Confidence**: High (`READ`).

### PS2SERVERS-018
- **Title**: Duplicated boilerplate across the conformance runners and unclosed file handles in `neutrino_nfs/run.py` and `udpfs_parity.py`
- **Category**: 2 (Duplication) & 6 (Bugs and security)
- **Location(s)**:
  - `PS2-Servers/conformance/integration/libsmb2/run.py:64-100` (`UpstreamUnavailable`, `fetch`)
  - `PS2-Servers/conformance/integration/neutrino_nfs/run.py:60-149` (`UpstreamUnavailable`, `fetch`, `splice`, `build`, unclosed `open(path, "rb").read()` at `147`)
  - `PS2-Servers/conformance/integration/neutrino_smb/run.py:65-175` (`UpstreamUnavailable`, `fetch`, `splice`, `between`, `build`)
  - `PS2-Servers/conformance/integration/opl_http/run.py:30-140` (`between`, `UpstreamUnavailable`, `build`, `get`)
  - `PS2-Servers/conformance/integration/riptopl_http/run.py:40-160` (`UpstreamUnavailable`, `between`, `build`, `get`)
  - `PS2-Servers/conformance/integration/udpfs_parity.py:37-274, 313-336, 369-422` (`ParityClient`, `make_zso`, unclosed `self.log.open("wb")` at `369`)
  - `PS2-Servers/udpfs_server/multiclient_selftest.py:41-191, 601-606` (`UdpfsTestClient`, unused `sector_size=512` at `180`)
  - `PS2-Servers/udpfs_server/compression_selftest.py:84` (`make_zso_header`)
- **Problem**: Five `conformance/integration/*/run.py` runners duplicate `UpstreamUnavailable`, SHA256 fetch, C-template splicing, compiler fallback probing, and HTTP `get()`, while `udpfs_parity.py` (`ParityClient`, `make_zso`) duplicates ~150 lines of `multiclient_selftest.py` (`UdpfsTestClient`) and `compression_selftest.py`. In addition, `neutrino_nfs/run.py:147` opens files for hashing without closing them and `udpfs_parity.py:369` leaks `self.log.open("wb")` across `Server.stop()`.
- **Fix**: Factor shared fetch/build/HTTP helpers and one UDPFS test client across the conformance scripts, wrap `open(path, "rb")` in a `with` block, close the server log handle on `Server.stop()`, and remove unused `sector_size` from `UdpfsTestClient.bread`.
- **Benefit**: Removes hundreds of lines of duplicated test harness code and fixes leaked file descriptors.
- **Effort**: M. **Risk**: Low. **Confidence**: High (`READ`).

### PS2SERVERS-019
- **Title**: Repeated Go test helper stubs (`lockedBuffer`, `discardWriter`, synthetic CSO/ZSO/CHD container writers)
- **Category**: 2 (Duplication) & 7 (Build/CI/repo clutter)
- **Location(s)**:
  - `PS2-Servers/native/ps2servers-edge/internal/smb/session_summary_test.go:17-32` (`lockedBuffer`)
  - `PS2-Servers/native/ps2servers-edge/internal/udpbd/session_summary_test.go:18-33` (`lockedBuffer`)
  - `PS2-Servers/native/ps2servers-edge/internal/udpfs/session_summary_test.go:22-37` (`lockedBuffer`)
  - `PS2-Servers/native/ps2servers-edge/internal/udpfs/observability_test.go:24-39` (`synchronizedLogBuffer`)
  - `PS2-Servers/native/ps2servers-edge/internal/smb/integration_test.go:22-24`, `internal/udpbd/udpbd_test.go:127-129`, `internal/udpfs/integration_test.go:63-65` (`discard` / `discardWriter` duplicating `io.Discard`)
  - `PS2-Servers/native/ps2servers-edge/internal/compression/image_test.go:13-32`, `corrupt_index_test.go:20-40`, `sizeof_test.go:19-47`, `chd_test.go:14-27`, `chd_cuebin_test.go:11`, `internal/udpfs/compression_operations_test.go:16-45` (6 synthetic container writers)
- **Problem**: Go tests duplicate thread-safe `bytes.Buffer` wrappers four times, reimplement `io.Discard` three times (even though `transfer_test.go:18` and `udpwrite_test.go:36` already use `io.Discard`), and duplicate synthetic CSO/ZSO/CHD fixture builders six times.
- **Fix**: Replace `discard`/`discardWriter` with `io.Discard`, unify `lockedBuffer`/`synchronizedLogBuffer` in `internal/udpfs`, and consolidate the synthetic CSO/ZSO container builders inside package `compression`.
- **Benefit**: Cuts ~150 lines of duplicated Go test scaffolding with no change to assertions.
- **Effort**: S. **Risk**: Low. **Confidence**: High (`READ`).

### PS2SERVERS-020
- **Title**: `libsmb2/run.py` prints `"all passed"` and exits `0` when all upstream fetches skip, and diverges on `--require-upstream` exit code
- **Category**: 7 (Build/CI/repo clutter) & 6 (Bugs and security)
- **Location(s)**:
  - `PS2-Servers/conformance/integration/libsmb2/run.py:183-210`
  - Siblings: `PS2-Servers/conformance/integration/neutrino_nfs/run.py:263-267`, `neutrino_smb/run.py:270-274`, `opl_http/run.py:185-189`, `riptopl_http/run.py:207-211`
- **Problem**: Catching `UpstreamUnavailable` when `--require-upstream` is unset prints `SKIP:` and executes `continue`; if all pinned commits fail to fetch, `failures` stays empty and `main()` prints `"libsmb2 conformance: all passed"` and returns `0` (`lines 206-210`). It also returns exit code `2` on `--require-upstream` skips while all four siblings return `1`.
- **Fix**: Track executed pin count (`ran`), print an explicit `"did NOT run"` skip notice when `ran == 0` instead of `"all passed"`, and match the sibling exit code `1`.
- **Justification**: Prevents CI from mistaking zero executed `libsmb2` checks for a clean pass.
- **Benefit**: Eliminates false-positive CI conformance passes and aligns runner exit codes.
- **Effort**: S. **Risk**: Low. **Confidence**: High (`READ`).

### PS2SERVERS-021
- **Title**: `TestServerPortFallback` grabs host port `8082` without occupying it first and leaves the random-port fallback unverified
- **Category**: 6 (Bugs and security) & 8 (Half-finished or redundant features)
- **Location(s)**:
  - `PS2-Servers/native/ps2servers-edge/internal/webui/webui_test.go:598-611, 638-652`
  - `PS2-Servers/native/ps2servers-edge/internal/webui/webui.go:19-20, 238-257`
- **Problem**: `TestServerPortFallback` passes `Port: 0`, sleeps `100ms`, and closes the server (`642-652`); because `Serve()` tries `DefaultPort` (`8082`) first when `Port <= 0` and only falls back to `:0` if binding `8082` fails (`webui.go:244-257`), the test binds live host port `8082` whenever it is free and never enters or checks the fallback branch (meanwhile `TestLogLinesCannotInjectSSEEvents` at `598-611` queries `?format=json` instead of the live SSE endpoint).
- **Fix**: Pre-bind `127.0.0.1:8082` inside `TestServerPortFallback` (with `defer l.Close()`), wait deterministically on the logged URL/fallback warning instead of sleeping `100ms`, and assert that an alternate port was bound.
- **Justification**: Guarantees deterministic test isolation and exercises the actual port-fallback code path.
- **Benefit**: Removes a timing-sensitive host port conflict in Go tests and verifies fallback behavior.
- **Effort**: S. **Risk**: Low. **Confidence**: High (`READ`).

### PS2SERVERS-022
- **Title**: Fragile Python self-tests (hardcoded UDPBD port, fixed sleeps, monkeypatched globals) and split `launcher/__main__.py` guard
- **Category**: 1 (Dead code) & 6 (Bugs and security) & 7 (Build/CI/repo clutter)
- **Location(s)**:
  - `PS2-Servers/udpbd_server/selftest.py:26, 70, 151, 173`
  - `PS2-Servers/udpbd_server/udpbd_server.py:159, 333-346, 374-378`
  - `PS2-Servers/udpfs_server/compression_selftest.py:186-219, 226-242, 308-321, 345-361`
  - `PS2-Servers/launcher/__main__.py:1-10` vs `PS2-Servers/ps2servers.py:17-38`
- **Problem**: `udpbd_server/selftest.py:26` binds hardcoded `U.UDPBD_PORT` (failing with `EADDRINUSE` whenever UDPBD is running), relies on `time.sleep(0.3)`/`0.1`, and shuts down the server via `server.sock.close()`. Elsewhere, `compression_selftest.py` mutates module globals (`srv.COMPRESSED_EXTENSIONS`, `srv.os.path.exists`), and `launcher/__main__.py` splits `if __name__ == "__main__":` across two blocks (`3-5`, `9-10`) while omitting the `serve` alias from `ps2servers.py:24-38`.
- **Fix**: Add optional `port=UDPBD_PORT` to `UdpbdServer` so `selftest.py` can use ephemeral port `0` cleanly, replace monkeypatched globals in `compression_selftest.py` with explicit helper parameters or `unittest.mock.patch`, and collapse `launcher/__main__.py` into a single entry guard.
- **Justification**: Minimal parameter addition makes self-tests parallel-safe without colliding with a live UDPBD server.
- **Benefit**: Eliminates local `EADDRINUSE`/sleep flakiness and simplifies module entrypoints.
- **Effort**: S. **Risk**: Low. **Confidence**: High (`READ`).

### PS2SERVERS-015
- **Title**: Edge performance re-evaluation helper allocates per frame via `ctypes` closures
- **Category**: 5 (Performance)
- **Location(s)**:
  - `PS2-Servers/launcher/ra_evaluator.py:178-190`
- **Problem**: `evaluate()` rebuilds a `reads` dictionary and recreates two `ctypes.CFUNCTYPE` closures on every evaluation frame, creating steady allocation and C-thunk overhead in the polling loop.
- **Fix**: Allocate the `CFUNCTYPE` callback wrappers once at `Evaluator` initialization (bound via instance state) and reuse the read buffer/map across frames.
- **Benefit**: Eliminates per-frame `ctypes` closure compilation and dictionary allocation during gameplay polling.
- **Effort**: S. **Risk**: Low. **Confidence**: High (`READ`).

### PS2SERVERS-023
- **Title**: Redundant dual Debug+Info log emission on UDPFS `seq 0` session reset
- **Category**: 2 (Duplication) & 3 (Overengineering)
- **Location(s)**:
  - `PS2-Servers/native/ps2servers-edge/internal/udpfs/negotiation.go:464-484`
  - `PS2-Servers/native/ps2servers-edge/internal/udpfs/observability_test.go:477-484`
- **Problem**: When an unexpected `h.Sequence == 0` triggers a session reset, `negotiation.go` logs both `Debug("peer sequence mismatch", ... "outcome": "session_reset")` (`471-475`) and `Info("peer sequence reset (seq 0 received)", ... "outcome": "session_reset")` (`481-484`) for the same packet, duplicating `peer`, `socket`, `profile`, `expected`, `received`, and `outcome`.
- **Fix**: Emit only the single `Info("peer sequence reset (seq 0 received)", ...)` log record, remove lines `471-475`, and update `observability_test.go:477-484` to expect one record.
- **Benefit**: Eliminates duplicate `session_reset` log lines per console handoff/reboot.
- **Effort**: S. **Risk**: Low. **Confidence**: High (`READ`).

---

## Files Not Read

- `PS2-Servers/vendor/retroachievements/` (`115` files): third-party vendored `rcheevos` and `xerabora` C/UI sources and licenses; excluded as upstream vendor code except `README.md` and `protocol/*`.
- `PS2-Servers/tests/` (`128` files): Python unit test suite and binary CHD fixtures; executed via `unittest discover` (Go `*_test.go`, `conformance/integration/`, `udpbd_server/selftest.py`, and `udpfs_server/*_selftest.py` were read in full).
- `PS2-Servers/docs/` (`65` files): generated static HTML/CSS site pages and user guides (`ISSUE-249-HANDOFF.md`, `PLAN-NBD-AND-PS2CLIENT.md`, `NETWORK-TOOLS-ROADMAP.md`, `RA-OWNED-RUNTIME.md`, `RA-UPSTREAM-AUDIT.md` sampled `1-100`; `docs/` parity already checked via `tools/verify_docs.py`).
- `PS2-Servers/README.md` (`101-541`), `NOTICE.md` (`101-140`), `SECURITY.md`, `ANTIVIRUS-NOTICE.md`, `THIRD_PARTY_NOTICES.md`, `LICENSE`: legal/repository documentation prose excluded once source, build, test harness, and workflow files were covered.
