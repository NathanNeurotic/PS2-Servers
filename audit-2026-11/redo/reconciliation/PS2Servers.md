# PS2Servers extraction

## Verdicts
| ID | Title | Effort | Risk | Verdict |
|---|---|---|---|---|
| RE-SERV-001 | Pre-authentication commands stay live after authentication and rewrite the session's security state (signing downgrade + negotiate-claims overwrite) | M | Med | VERIFIED |
| RE-SERV-002 | CREATE, SET_INFO and CLOSE perform create / truncate / rename / delete without checking the handle's granted DesiredAccess | S | Low | VERIFIED |
| RE-SERV-003 | READ is never gated on DesiredAccess; there is no read-access concept at all | S | Low | VERIFIED-DRIFT |
| RE-SERV-004 | SET_INFO FileBasicInformation returns SUCCESS while doing nothing — and validates nothing | S | Low | VERIFIED |
| RE-SERV-005 | No throttling of failed logons, and the NTLM challenge survives a failed AUTHENTICATE | M | Med | VERIFIED |
| RE-SERV-006 | LOGOFF does not retire the SessionId; the next session on the connection silently reuses it | S | Low | VERIFIED |
| RE-SERV-007 | TREE_DISCONNECT, ECHO and LOGOFF answer SUCCESS with no session at all | S | Low | VERIFIED |
| RE-SERV-008 | A malformed KEY_EXCH wrapped key silently falls back to the SessionBaseKey | S | Low | VERIFIED |
| RE-SERV-009 | A corrupt trailing segment in a compound request discards every reply, including successes already computed | S | Low | VERIFIED |
| RE-SERV-010 | QUERY_DIRECTORY returns an entry larger than the client's OutputBufferLength | S | Low | VERIFIED |
| RE-SERV-011 | QUERY_DIRECTORY's pattern filter is bypassed for "." and ".." | S | Low | VERIFIED |
| RE-SERV-012 | A zero-length READ is answered STATUS_END_OF_FILE | S | Low | VERIFIED |
| RE-SERV-013 | NEGOTIATE silently accepts a dialect list shorter than DialectCount claims | S | Low | VERIFIED |
| RE-SERV-014 | FLUSH cannot fail: it is the only handle handler with no body-length check, and it swallows fsync errors | S | Low | VERIFIED |
| RE-SERV-015 | Receive framing has no size cap, and `_recv_exact` concatenates quadratically | S | Low | VERIFIED-DRIFT |
| RE-SERV-016 | `send_msg` raises OverflowError for a reply over 16 MiB, killing the connection thread | S | Low | VERIFIED |
| RE-SERV-017 | No resource limits: unbounded connection threads, no timeout on accepted sockets, unbounded handles and trees per connection | M | Med | VERIFIED |
| RE-SERV-018 | Handles name paths, not files: lazy open-by-path serves whatever the path names at first use | M | Med | VERIFIED |
| RE-SERV-019 | FileAccessInformation always reports full access (0x001F01FF) | S | Low | VERIFIED |
| RE-SERV-020 | Rename ignores the RootDirectory field, so a directory-relative rename lands at the share root | S | Low | VERIFIED |
| RE-SERV-021 | The account password is taken on the command line, where any local user can read it | M | Low | VERIFIED |
| RE-SERV-022 | The `--smb-version 2/3` SMB2 handlers are stubs that claim success without doing any work | S | Low | WEAKENED |
| RE-SERV-023 | Short or malformed parameter blocks raise `struct.error` out of the handlers and kill the connection, with no SMB error reply | S | Low | VERIFIED |
| RE-SERV-024 | A non-ASCII configured password makes every login attempt raise `TypeError` in `hmac.compare_digest` | S | Low | VERIFIED |
| RE-SERV-025 | No bound tying accepted message size to the advertised buffer, no connection idle timeout, and unbounded thread-per-connection growth | S | Low | VERIFIED |
| RE-SERV-026 | QUERY_INFORMATION_DISK reports invented disk geometry and free space | S | Low | VERIFIED |
| RE-SERV-027 | Boolean environment values outside a four-word vocabulary silently parse as False — including `RO`, so `RO=2` yields a writable server with no warning — and env-enabled booleans have no CLI off-switch | S | Low | VERIFIED |
| RE-SERV-028 | GETSTAT on a symlink pointing outside the root raises TypeError and sends no reply | S | Low | VERIFIED |
| RE-SERV-029 | BREAD/BWRITE never validate the sector range against the device or file size; BWRITE past the end extends the host image and reports success | S | Low | VERIFIED |
| RE-SERV-030 | WRITE_DATA trusts the chunk header: `chunk_size` is never checked against the datagram, `total_chunks` is re-read from every chunk, and BWRITE completion reports success for short data | S | Low | VERIFIED |
| RE-SERV-031 | Block-device handle 0 is one shared file object with one shared cursor across all sessions for READ, LSEEK and file-WRITE | M | Med | VERIFIED |
| RE-SERV-032 | No writer exclusion anywhere: two sessions can hold the same file open writable and interleave writes | M | Med | VERIFIED |
| RE-SERV-033 | READ/BREAD on a handle that is not open for reading crashes the reply: `-e.errno` on an errno-None exception | S | Low | VERIFIED |
| RE-SERV-034 | Serving-path compressed wrappers trust header geometry without bounds; the limits module only protects the opt-in inspection path | M | Med | VERIFIED |
| RE-SERV-035 | MKDIR passes the wire `mode` to `os.mkdir` unsanitised | S | Low | WEAKENED |
| RE-SERV-036 | UDPBD file-backed BlockDevice has no extent bounds: out-of-range WRITE silently extends the image, out-of-range READ silently returns zeros | S | Low | VERIFIED |
| RE-SERV-037 | UDPBD read-only file device silently swallows an entire write transfer and only reports failure at the very end — unlike both other backends, which raise on the first write | S | Low | VERIFIED |
| RE-SERV-038 | HTTP legacy-flat profile serves symlinks that escape the games folder; the containment check exists but is only applied in the path-aware profile | S | Low | VERIFIED |
| RE-SERV-039 | NFS: WRITE and SETATTR need no OPEN and no valid stateid — OPEN's access checks are dead letter for the write path | M | Med | VERIFIED |
| RE-SERV-040 | SMB WRITE_ANDX reports only the low 16 bits of the byte count it actually wrote | S | Low | VERIFIED |
| RE-SERV-041 | SMB WRITE_ANDX assembles its data length in a plain `int`, the exact 32-bit wrap `readAndX` was fixed for | S | Low | VERIFIED |
| RE-SERV-042 | SMB WRITE_ANDX ignores the handle's access mode and re-opens the file by path for writing | M | Med | VERIFIED |
| RE-SERV-043 | UDPBD read-only writes fake success at the device layer and arm a real write handshake at the handler layer | S | Low | VERIFIED |
| RE-SERV-044 | UDPBD write state is one global slot: any peer's INFO or READ silently destroys another peer's in-flight write (Go Edge and Python UDPBD) | S | Low | VERIFIED |
| RE-SERV-045 | UDPFS status "Busy" latches on for the life of a session after its first data packet | M | Med | VERIFIED |
| RE-SERV-046 | SMB TRANSACTION handler answers every transaction as a NetShareEnum, ignoring the request entirely — in both the Go Edge SMB server and the Python SMB1 server | S | Low | VERIFIED |
| RE-SERV-047 | Live `/api/logs` stream forwards lines without the SSE sanitisation applied to every other line | S | Low | VERIFIED |
| RE-SERV-048 | `raw_storage` runs bare `powershell.exe` from PATH/CWD while every other PowerShell call site in the repo resolves the absolute System32 path specifically to prevent binary hijacking | S | Low | VERIFIED |
| RE-SERV-049 | Native RA dashboard builds achievement list with `innerHTML` from unescaped server-supplied titles/descriptions | S | Low | VERIFIED |
| RE-SERV-050 | Caduceus Web API key is sent as a GET query parameter on every API request | S | Low | VERIFIED |
| RE-SERV-051 | Caduceus library (`G`) progress replies always report 0 earned / 0 maximum | S | Low | VERIFIED |
| RE-SERV-052 | WHICH-DEVICE.txt shipped in every Edge archive falsely claims the binary is read-only by default | S | Low | VERIFIED |
| RE-SERV-053 | The bcrypt CMAC self-test compares against the wrong constant: it is the CMAC *L* block, not a CMAC | S | Low | VERIFIED |
| RE-SERV-054 | `check_component("")` raises IndexError instead of PathError | S | Low | VERIFIED |
| RE-SERV-055 | `CHECK_DIRECTORY` never succeeds for a spec-formed request because it does not strip the `0x04` BufferFormat byte its sibling handler strips | S | Low | VERIFIED |
| RE-SERV-056 | NEGOTIATE returns success with DialectIndex 0 when the client never offered NT LM 0.12 | S | Low | VERIFIED |
| RE-SERV-057 | Core's `run()` drops the base server's per-datagram `OSError` containment, so one failed INFORM send can kill the whole server loop | S | Low | VERIFIED |
| RE-SERV-058 | Environment integer/float parsing crashes at parser construction, while the neighbouring duration/size parsers were deliberately made never to raise | S | Low | VERIFIED |
| RE-SERV-059 | `PROTOCOL_MODE` from the environment is neither stripped nor alias-mapped, slips past argparse's `choices`, and detonates later as an uncaught `ValueError` | S | Low | WEAKENED |
| RE-SERV-060 | ZSO decompression retries once per input byte: a garbage block costs one LZ4 attempt per byte of input | S | Low | VERIFIED |
| RE-SERV-061 | Compressed block offsets are never validated in the serving path, and short/corrupt blocks are silently fabricated into data | M | Low | VERIFIED |
| RE-SERV-062 | Router-status replies never set FLAG_UDPBD or FLAG_DECOMPRESSES, even when the server serves a block device / decompresses | S | Low | VERIFIED |
| RE-SERV-063 | LSEEK whence handling disagrees by handle type: the compressed wrapper silently accepts any whence | S | Low | VERIFIED |
| RE-SERV-064 | UDPBD `BlockDevice` constructor validates the target type but not its size: >2 TiB images break INFO packing, non-512-multiple sizes silently lose their tail | S | Low | VERIFIED |
| RE-SERV-065 | HTTP flat index: path lookup serves the duplicate copy that the scan explicitly suppressed and that `games.csv` does not list | S | Low | VERIFIED |
| RE-SERV-066 | HTTP dispatcher sends a 500 response after a 206 header (and possibly partial body) is already on the wire | S | Low | VERIFIED |
| RE-SERV-067 | HTTP per-connection cached handle is keyed by path only, so an index refresh / file replacement is served from the stale handle with the new entry's size | S | Low | WEAKENED |
| RE-SERV-068 | LZ4 block decoder's length accumulator is unbounded, defeating its own output checks on 32-bit targets | S | Low | VERIFIED |
| RE-SERV-069 | Text-mode log lines with fields are fragmented into multiple LogBuffer lines (and subscribers get the fragments) | S | Low | VERIFIED |
| RE-SERV-070 | `ValidateConfig` accepts values the binary then refuses at startup (sector size, compression cache, peer timeout) | S | Low | VERIFIED |
| RE-SERV-071 | `genericManager.Restart` dereferences a possibly-nil logger and reports success for a restart that did nothing | S | Low | VERIFIED |
| RE-SERV-072 | `IsStatusQuery`'s documentation claims exact-length strictness the code does not implement | S | Low | VERIFIED |
| RE-SERV-073 | SMBv2/SMBv3 argv builder crashes on `root_dir`-only values and silently emits an empty share, where SMBv1 accepts and validates | S | Low | VERIFIED |
| RE-SERV-074 | Windows/Linux firewall port list still keys UDPFS single-port mode off the retired `modulo_mode` setting, so a migrated config silently gains a dead data-port rule | S | Low | VERIFIED |
| RE-SERV-075 | NFS and RetroAchievements never get the cooperative stop pipe, so on Windows Stop kills them outright — skipping NFS cleanup and orphaning the RetroAchievements engine child | M | Med | VERIFIED |
| RE-SERV-076 | Choice-field values have no single stored vocabulary: the RetroAchievements mode default is the wire value while the other two choice fields default to display labels, and three separate tolerant readers compensate | M | Med | VERIFIED |
| RE-SERV-077 | The SMB port default lives in three places and one of them is wrong: the firewall layer defaults SMBv2/SMBv3 to 1025, the servers default to 1445 | S | Low | VERIFIED |
| RE-SERV-078 | `netinfo.best_lan_ip()` returns the default-route address unconditionally, bypassing the private-LAN ranking the module exists to apply | S | Low | VERIFIED |
| RE-SERV-079 | Owned-runtime `/state` schema diverges from the managed-engine schema its own consumers parse, and two counters are fabricated | M | Med | VERIFIED |
| RE-SERV-080 | Web "Sign out" deletes the stored credentials, permanently cancels submissions, and can report failure after revocation already happened | M | Med | VERIFIED |
| RE-SERV-081 | Caduceus: unauthenticated CADQ sender becomes the unlock-notice destination, and the CADA pairing key lives in the console-served games folder | M | Med | VERIFIED |
| RE-SERV-082 | Identification's `gameid` request bypasses all pacing, and its 429 backoff is discarded | M | Med | VERIFIED |
| RE-SERV-083 | Caduceus `pad()` over-pads by a full 64-byte block whenever the body is already block-aligned, diverging from the owned protocol's padding | S | Low | VERIFIED |
| RE-SERV-084 | `CredentialStore` wedges permanently if the profile directory pre-exists with non-0700 permissions, and `load()` can raise instead of returning "no credentials" | S | Med | VERIFIED-DRIFT |
| RE-SERV-085 | Shipped systemd webui env file configures a start the binary refuses | S | Low | VERIFIED-DRIFT |
| RE-SERV-086 | ps2servers@.service header falsely states "Edge has no SMB at all" | S | Low | VERIFIED |
| RE-SERV-087 | OpenWrt init script's fallback enables the shared status port that the shipped config deliberately disables | S | Low | VERIFIED |
| RE-SERV-088 | `tryTrans2` can never return an error, and `TestSearchesAreBounded` never asserts the bound | S | Low | VERIFIED |
| RE-SERV-089 | `TestServerPortFallback` never exercises the fallback or any port | S | Low | VERIFIED |
| RE-SERV-090 | `TestCompressionCacheDefaultsAndDisables` tests struct storage, not defaulting or disabling | S | Low | VERIFIED |
| RE-SERV-091 | `TestSafeCueNameRejectsInvalidAndOverlongUTF8` has no positive control; a reject-everything stub passes | S | Low | VERIFIED |
| RE-SERV-092 | Windows-only filesystem test can never run in CI | M | Low | VERIFIED |
| RE-SERV-093 | Three "behaviour" tests assert only that constants lie in a range | M | Low | VERIFIED |
| RE-SERV-094 | UDPFS test helper `result()` reports a truncated reply as success (0) | S | Low | VERIFIED |
| RE-SERV-095 | `TestUnknownServiceIDIsStillIgnored` is negative-only and passes with the transport completely dead | S | Low | VERIFIED |
| RE-SERV-096 | Duplicated test scaffolding: the same mutex log buffer twice in one package, and CSO fixture builders re-implemented four times | M | Low | VERIFIED |
| RE-SERV-097 | The fuzz target and the benchmark are never run as fuzzing or benchmarking anywhere | S | Low | VERIFIED |
| RE-SERV-098 | Dead keep-alive line for an unused import in `blockdev_test.go` | S | Low | VERIFIED |
| RE-SERV-099 | `TestBlockGeometryIsConsistent` asserts identities guaranteed by the implementation's own formulas | S | Low | VERIFIED |
| RE-SERV-100 | `TestSharedHandshakeFixtures` duplicates the hardcoded cases and degrades to a silent skip | S | Low | VERIFIED |
| RE-SERV-101 | The attribution test mutates, and depends on, the developer's real checkout | M | Low | VERIFIED |
| RE-SERV-102 | Eight test files silently run zero tests when executed directly | S | Low | WEAKENED |
| RE-SERV-103 | The Edge flag-extraction helpers are copied verbatim between test files | S | Low | VERIFIED |
| RE-SERV-104 | The shared ISO fixture lives inside a test module — and one consumer re-implements it anyway | M | Low | VERIFIED |
| RE-SERV-105 | test_library_preferences executes AST-extracted copies instead of the production methods | M | Low | VERIFIED |
| RE-SERV-106 | The netinfo detailed-format test asserts only the formatter's own constant strings | S | Low | VERIFIED |
| RE-SERV-107 | A traversal test whose name promises the opposite of what it asserts | S | Low | VERIFIED |
| RE-SERV-108 | The hardlink backup test passes vacuously where hardlinks are unavailable | S | Low | VERIFIED |
| RE-SERV-109 | The display-guard + full-app construction prologue is copy-pasted through the GUI tests | M | Low | VERIFIED |
| RE-SERV-110 | Mid-file `unittest.main()` orphans tests under direct execution in two SMB2 test files (23 + 5 tests) | S | Low | VERIFIED |
| RE-SERV-111 | Cross-implementation encoder test converts a Go timeout into a skip | S | Low | VERIFIED |
| RE-SERV-112 | Banner address test asserts nothing when the host returns no addresses | S | Low | VERIFIED |
| RE-SERV-113 | systemd unit tests assert only inside loops over parsed content, with no non-emptiness guard | S | Low | VERIFIED |
| RE-SERV-114 | Share-name test is tautological: it reads back the dict it just passed in | S | Low | VERIFIED |
| RE-SERV-115 | Setup-guide tests assert substrings of the GUI source instead of behaviour | M | Low | VERIFIED |
| RE-SERV-116 | Stop-all tests are AST inspections of gui.py, not executions of stop-all | M | Low | VERIFIED |
| RE-SERV-117 | "Every edge target has a purpose" test checks only that label strings exist | S | Low | VERIFIED |
| RE-SERV-118 | POSIX permission test passes by bare `return` on Windows instead of skipping | S | Low | VERIFIED |
| RE-SERV-119 | Identical helper bodies duplicated across test files | S | Low | VERIFIED |
| RE-SERV-120 | Two identical payload builders under different names inside test_transfer_limits.py | S | Low | VERIFIED |
| RE-SERV-121 | Whole-registry launcher test asserts only that build_argv returns a list | S | Low | VERIFIED |
| RE-SERV-122 | Real-socket recv-buffer test's only assertion cannot fail | S | Low | VERIFIED |
| RE-SERV-123 | FakeServices answers silent success to any PowerShell command it doesn't recognise | S | Low | VERIFIED |
| RE-SERV-124 | Tests patch shared stdlib module objects, so a missed restore leaks into the whole discover run | M | Medium | VERIFIED |
| RE-SERV-125 | Five test files each exec a private copy of ps2servers_core.py under a different module name | M | Medium | VERIFIED |
| RE-SERV-126 | `_local_ipv4_addresses` runs three enumeration mechanisms to print one cosmetic warning line | S | Low | VERIFIED-DRIFT |
| RE-SERV-127 | The srvsvc pipe does not reassemble a PDU split across WRITEs (or across RPC fragments) | M | Low | VERIFIED |
| RE-SERV-128 | `_optimal_chunk_size` computes an "optimum" that is always 1408 | S | Low | REFUTED |
| RE-SERV-129 | NFS is the only server without the shared `--session-summary` feature | M | Low | VERIFIED |
| RE-SERV-130 | UDPFS plain file WRITE is never held to the length it declared, unlike the BWRITE path that was hardened for exactly this | S | Low | VERIFIED |
| RE-SERV-131 | The GUI's theme, admin panel and log trimming live as a ~300-line monkeypatch shim in the entry point, and its copy of `_append_log` has already drifted from gui.py's original | M | Med | VERIFIED |
| RE-SERV-132 | The owned runtime bypasses `AccountSession` entirely: hardcoded epoch 1 and fixed task IDs, while the session's begin/accept state machine runs only in tests | L | Med | VERIFIED |
| RE-SERV-133 | Watch-profile vocabulary disagrees across three files: `orbit-v1.0.2` is accepted but never produced, `caduceus` is produced but rejected by the IPC validator | S | Low | WEAKENED |
| RE-SERV-134 | GUI behaviour is assembled by stacked monkey-patches, including a custom import-hook finder in `launcher/__init__.py` | M | Med | VERIFIED |
| RE-SERV-135 | `ps1_index.py` is a complete PS1 scanner with no consumer in the launcher | S | Low | VERIFIED |
| RE-SERV-136 | Three competing application-icon sources: shipped LOGO, a 246-line procedural icon generator, and a tray fallback badge | M | Med | VERIFIED |
| RE-SERV-137 | The "shared index" is wrapped in two pass-through layers on top of `read_index` | S | Low | VERIFIED |
| RE-SERV-138 | conformance/integration/http_probe.py is orphaned: no CI step, test or runner references it | S | Low | VERIFIED |
| RE-SERV-139 | The session-id minting line is duplicated inside `h_session_setup` | S | Low | VERIFIED |
| RE-SERV-140 | The Transaction response envelope is built twice, once for TRANS2 and once open-coded in the RAP handler | S | Low | VERIFIED |
| RE-SERV-141 | Compressed-image stem/extension matching is implemented twice, with subtly different rules | S | Low | VERIFIED |
| RE-SERV-142 | Three copy-pasted ID allocators, and FID wrap-around silently overwrites a live open file, leaking its handle | S | Low | VERIFIED |
| RE-SERV-143 | The entry-point surface (parser + validation + compression precedence + server construction) is duplicated between Core and the standalone server, and again inside Core's own `main()` | M | Low | VERIFIED |
| RE-SERV-144 | DATA header parsing and payload-size computation are duplicated between `_route_data_via` and `_handle_data` (and parsed a third time by the base handler) | S | Low | VERIFIED-DRIFT |
| RE-SERV-145 | The session-stats attachment snippet is copy-pasted between Core and the SMB1 server instead of shared | S | Low | VERIFIED-DRIFT |
| RE-SERV-146 | Compressed-header parsing is triplicated: the size probe, the three wrappers, and the limits module each parse the same headers, including the CD-CHD size correction twice | M | Med | VERIFIED |
| RE-SERV-147 | UDPBD `_handle_read`'s `is_raw` extent pre-check duplicates the bound both raw backends already enforce in `seek` | S | Low | VERIFIED |
| RE-SERV-148 | NFS `_op_close` and `_op_open_confirm` are byte-identical bodies under two names | S | Low | VERIFIED-DRIFT |
| RE-SERV-149 | `_stop_on_sigterm` is duplicated between the HTTP and NFS entry points (and has already drifted) | S | Low | VERIFIED |
| RE-SERV-150 | CSO/ZSO index-extent validation is duplicated verbatim between `openImage` and `SizeOf` | S | Low | VERIFIED |
| RE-SERV-151 | The CHDv5 header is parsed twice, by two parsers with different validation rules | M | Med | VERIFIED |
| RE-SERV-152 | SMB hand-rolls `itoa` instead of using `strconv.Itoa` | S | Low | VERIFIED |
| RE-SERV-153 | SMB's FID/TID/SID allocators are three copies of one function | S | Low | VERIFIED |
| RE-SERV-154 | UCI boolean parsing is duplicated 16 times inline and disagrees with the one use of the shared helper | S | Low | VERIFIED |
| RE-SERV-155 | The four subcommands copy-paste their flag scaffolding — and the copy in `runWebUI` already lost the `--log-format` validation | M | Low | VERIFIED |
| RE-SERV-156 | Port-445 takeover is two divergent full implementations (SMBv1 and SMBv2 servers), with the PowerShell-resolution helper copied a third time in the launcher | L | Med | VERIFIED-DRIFT |
| RE-SERV-157 | SIGTERM→KeyboardInterrupt stop handling is installed three times: once by serve.py for every launcher child, and again inside the HTTP and NFS servers | S | Low | VERIFIED |
| RE-SERV-158 | The stop-pipe environment name is a duplicated string literal in the two files whose agreement the whole cooperative-stop mechanism depends on | S | Low | VERIFIED |
| RE-SERV-159 | Three `_parse_port` implementations with three different failure semantics | S | Low | VERIFIED |
| RE-SERV-160 | Interface-address parsing is implemented four times across netinfo and directlink, including two different functions with the same name | M | Low | VERIFIED-DRIFT |
| RE-SERV-161 | DHCP option-TLV parsing is written twice inside directlink.py because `parse_packet` cannot be reused for replies | S | Low | VERIFIED |
| RE-SERV-162 | Frozen-executable probing is duplicated between config.py and servers.py | S | Low | VERIFIED |
| RE-SERV-163 | SMBV2 and SMBV3 share one mutable `fields` list object | S | Low | VERIFIED |
| RE-SERV-164 | DPAPI unprotect is implemented twice, with divergent hygiene | S | Low | VERIFIED |
| RE-SERV-165 | Account-identity / token validation re-implemented at eight sites with divergent rules | M | Low | VERIFIED |
| RE-SERV-166 | Achievement-definition validation duplicated four times, and `award_achievement` bypasses the shared response decoder | M | Low | VERIFIED |
| RE-SERV-167 | Five different title-sanitising implementations across the RA files | S | Low | VERIFIED |
| RE-SERV-168 | The loopback `/state` fetch is duplicated verbatim in the overview and the session poller | S | Low | VERIFIED |
| RE-SERV-169 | Two parallel library subsystems: two catalogue databases, two folder preferences, two importers, two backups, two UIs | L | Med | VERIFIED |
| RE-SERV-170 | Three setup guides and two first-run mechanisms; the standalone LibraryPanel guide is unreachable in the shipped app | M | Low | VERIFIED-DRIFT |
| RE-SERV-171 | Four separate games-folder scanners/indexers over the same trees | L | Med | VERIFIED |
| RE-SERV-172 | Two independent ISO9660 directory parsers (PS2 `disc_identity` vs PS1 `ps1_vcd`) | M | Med | VERIFIED |
| RE-SERV-173 | The PS2 disc-serial pattern is re-expressed five times, in five files, with five slightly different rules | S | Low | VERIFIED-DRIFT |
| RE-SERV-174 | Console artwork conversion is implemented twice, with different palette rules and different write paths | S | Low | VERIFIED |
| RE-SERV-175 | Two incompatible "has this file changed?" stamps for the same images | S | Low | VERIFIED |
| RE-SERV-176 | Desktop release build duplicated across release.yml and release-on-main.yml; the copies have already drifted | L | Med | VERIFIED-DRIFT |
| RE-SERV-177 | Two parallel documentation systems (34 Markdown + 22 HTML) cover the same topics with no generator between them | L | Med | VERIFIED |
| RE-SERV-178 | Dead code with zero callers anywhere in the repo | S | Low | VERIFIED |
| RE-SERV-179 | No native CMAC backend is probed on macOS, so signed SMB3 there runs on the pure-Python AES | M | Low | VERIFIED |
| RE-SERV-180 | FIND_FIRST2 / FIND_NEXT2 return exactly one directory entry per round trip | M | Med | VERIFIED |
| RE-SERV-181 | WRITE_ANDX re-opens the file from disk on every write, and the open file's cached size is never updated by writes | M | Med | VERIFIED |
| RE-SERV-182 | `Share.resolve`'s comment promises a share-prefix strip the code does not perform | S | Low | VERIFIED |
| RE-SERV-183 | `classify_profile` contains an unreachable third branch that restates the first | S | Low | VERIFIED |
| RE-SERV-184 | Two names are imported from `udpfs_server` and never used | S | Low | VERIFIED |
| RE-SERV-185 | `main()`'s Modulo fast path bypasses `AutoUdpfsServer`, leaving Auto's own Modulo handling unreachable on the shipped path | M | Med | VERIFIED |
| RE-SERV-186 | A thread is spawned for every fallback timer, one per discovery, although at most one can ever act | S | Low | VERIFIED |
| RE-SERV-187 | `tx_start_seq` is write-only dead state | S | Low | VERIFIED |
| RE-SERV-188 | Selftest blind spots: skipped sections still print ALL PASSED on POSIX, and no in-scope selftest exercises a single write opcode or hostile wire input | M | Low | VERIFIED-DRIFT |
| RE-SERV-189 | NFS `_Handles` grows without bound for the life of the server; every neighbouring table has a cap, this one has none | S | Low | VERIFIED |
| RE-SERV-190 | NFS: one global generation counter means any single write invalidates every connection's read handles and every cached directory listing | M | Med | VERIFIED |
| RE-SERV-191 | NFS PUTFH resolves the handle number and throws it away; READ and READDIR then re-derive it through the mutating `handle()` on the hot path | M | Med | VERIFIED |
| RE-SERV-192 | UDPBD `seen` peer map grows forever and the status reply reports it as the live session count | S | Low | VERIFIED |
| RE-SERV-193 | Every directory listing / OPEN / GETSTAT re-parses compressed-image headers from disk — no caching (Python UDPFS and Go Edge UDPFS) | M | Med | VERIFIED |
| RE-SERV-194 | SMB FIND_FIRST2's EndOfSearch branch is dead: the entry list can never have length 1 | S | Low | VERIFIED |
| RE-SERV-195 | `compression.Open` is a production-dead wrapper kept alive only by tests | S | Low | VERIFIED |
| RE-SERV-196 | OpenWrt `ServiceLogs` ignores its line limit, defeating the stated snapshot memory bound | S | Low | VERIFIED |
| RE-SERV-197 | `sessionstats` exports production API that only tests use: `Parse`, `WithClock`, `WithLimits` | S | Low | VERIFIED |
| RE-SERV-198 | Dead webui surface: an unread `API.log` field, and `Server.GeneratedPassword` / `Server.Close` with no callers | S | Low | VERIFIED |
| RE-SERV-199 | `Root.List` fully symlink-resolves and stats every directory entry, one `EvalSymlinks` per entry | M | Med | VERIFIED |
| RE-SERV-200 | The native-binary launch mechanism in the registry is dead: no registered server uses it | S | Low | VERIFIED |
| RE-SERV-201 | Virtual exFAT construction recomputes the full 65,536-entry Unicode upcase table on every instantiation (~130-180 ms for a one-file folder) | S | Low | VERIFIED |
| RE-SERV-202 | Opening one raw device re-runs the full OS disk enumeration up to twice, and every mount check re-runs it again | S | Low | VERIFIED-DRIFT |
| RE-SERV-203 | session_stats' header contract names the wrong server set | S | Low | VERIFIED |
| RE-SERV-204 | Verified dead code: `derive_watch` alias, unused `ENDPOINT` attributes, two unused imports | S | Low | VERIFIED-DRIFT |
| RE-SERV-205 | The same watch list is fully re-validated four times on every set activation | M | Med | VERIFIED |
| RE-SERV-206 | SSE `/events` uses one shared dirty-flag for all clients and ignores its result, so every client is sent the full state about once a second regardless of change | S | Low | VERIFIED |
| RE-SERV-207 | Zero-caller symbols: `AVAST_GEN_PUBLIC_NOTE`, `theme_assets.photo()`, `LauncherApp.on_close()`, `LauncherApp._quit_from_tray()`, and a double wrapper in dependency_panel | S | Low | VERIFIED |
| RE-SERV-208 | `Library.installed()` re-validates every path up to three times per call, and `Catalog.list_games()` stats every verified row on every listing | M | Med | VERIFIED |
| RE-SERV-209 | `LOGO.png` is 603 KB at 1229×1185 for display slots of at most 256 px; downscaling is integer-only subsampling | S | Low | VERIFIED |
| RE-SERV-210 | `gui.py` embeds ~10 KB of About/manual prose that is mutated at runtime by another module | S | Low | VERIFIED |
| RE-SERV-211 | Dependency checks are never cached: every panel refresh re-probes lz4 and re-`LoadLibrary`s libchdr candidates; the PATH cache is process-stale | S | Low | VERIFIED |
| RE-SERV-212 | OpenWrt package metadata is a release behind and describes a UDPFS-only read-only server that no longer exists | S | Low | VERIFIED |
| RE-SERV-213 | The portable/AppImage half of the release pipeline cannot fail, so missing downloads stay green | S | Low | WEAKENED |
| RE-SERV-214 | Go toolchain pinned three different ways across Edge's own build paths | S | Low | VERIFIED |
| RE-SERV-215 | Byte-identical 571,581-byte banner PNG tracked twice | S | Low | VERIFIED |
| RE-SERV-216 | ~3.16 MB of generated compression test fixtures are tracked as binaries | S | Low | WEAKENED |

## Narrowed scopes
- RE-SERV-022: SMB2 stub fake success holds for session_setup/create/read/write/negotiate and the auth-bypass dispatch is real, but h2_tree_connect does not succeed — it raises struct.error on every call and kills the connection thread; QUERY_DIRECTORY is 0x000E, not the cited 0x000A (0x000A is LOCK), though 0x000E is unmapped so the cannot-list conclusion stands.
- RE-SERV-035: Wire mode does reach os.mkdir unsanitised and the sticky bit survives end-to-end (0o1755 → 0o1750), but the filed setgid example did not reproduce (0o2755 → 0o750 on this host/fs), so setgid survival is host/group-dependent, not guaranteed.
- RE-SERV-059: Core defect is real (PROTOCOL_MODE=proper passes argparse's choices, then raises an uncaught ValueError at construction), but the trailing-space sub-claim is false — __init__ strips and lowercases, so "modulo ", " modulo", "MODULO" and "Standard" all construct fine; only unmapped values fail.
- RE-SERV-067: Stale-handle mechanism is real only for same-path replacement (a re-dumped ISO is served from the stale handle with the new entry's size); the filed .chd→.iso swap cannot hit it because entry.path differs between extensions and forces a reopen.
- RE-SERV-102: Census is exact (8 of 36 in-scope files lack unittest.main()), but the stated impact is narrower/false as written: plain `python tests/test_game_library.py` exits 1 with ModuleNotFoundError: No module named 'launcher', not silent exit-0 — the silent-0 behaviour occurs only with PYTHONPATH=. and adding the guard alone cannot fix direct runs.
- RE-SERV-133: Watch-profile vocabulary mismatch is real (sets exactly as cited; no producer emits orbit-v1.0.2), but the caduceus discard is latent, not live: the only production ProcessResolver (in AccountWorker) overrides the validator with valid_account_result, and the caduceus path never crosses valid_identification_result.
- RE-SERV-213: Core stands (portable/AppImage steps are fail-soft), but release.yml carries 11 continue-on-error: true, not 13, and the claimed if-no-files-found: error + continue-on-error pairing does not exist at release.yml:403-420 — that pairing actually occurs in release-on-main.yml's portable upload.
- RE-SERV-216: Per-file sizes are exact, but the fixtures are not 'generated' and not undocumented: testdata/README.md records them as unmodified upstream pcm720/udpfsd reference vectors at a pinned commit, which undercuts the filed fix; true sum is 3,215,374 B (≈3.22 MB), not ~3.16 MB.

## Defeating evidence
- RE-SERV-128: Executing the actual _optimal_chunk_size method (udpfs_server.py:2746) returns 1024 for totals 0/100/1024, 1280 for 1025/12800, and 1408 only for 20000/100000 — the audit missed the tie-break limb (on a packet-count tie higher alignment wins, candidates visited smallest-first, so 1024 displaces the 1408 incumbent).

## Candidates
### CAND-01
- Label: CAND-A1 — h2_tree_connect in the SMB1 file's SMB2 stub crashes on every call (struct.pack arity bug)
- Mechanism: h2_tree_connect packs struct.pack("<BBII", 16, 0x01, 0, 0x00000030, 0x000f001f) — 5 values into a 4-field format — so every SMB2 TREE_CONNECT raises struct.error unconditionally. The exception escapes serve_conn's except (ConnectionError, OSError) handling and kills the connection thread instead of returning even the stub's fake success.
- Locations: smbv1_server/smbserver_opl.py:573-581
- Proposed class: NEW
- Justification: Distinct crash mechanism underlying RE-SERV-022's failed tree-connect limb; no existing finding covers the pack-arity bug (RE-SERV-022 claimed fake success, RE-SERV-023 covers SMB1 parameter blocks).

### CAND-02
- Label: CAND-D1 — Caduceus read_events unlock notices suppressed for owned-runtime state (at-vs-ago, second victim of RE-SERV-079)
- Mechanism: launcher/caduceus.py read_events gates RAU1 unlock notices on event.get("ago", 999) <= 2, but owned-runtime unlock entries carry an at timestamp and no ago field. The gate is therefore never satisfied and console unlock notices silently never fire on owned-runtime data.
- Locations: launcher/caduceus.py:226-229; launcher/ra_web.py:82-104
- Proposed class: EXTENSION of RE-SERV-079
- Justification: Same at-vs-ago schema-divergence mechanism and same note_unlock fix site as RE-SERV-079, in an additional consumer (caduceus) the original finding did not cite.

### CAND-03
- Label: CAND-D2 — best_lan_ip docstring contradicts the module contract
- Mechanism: best_lan_ip's own docstring says to prefer the interface that actually routes off-box (the real LAN adapter), while the netinfo module header states the opposite preference (private LAN over VPN/WSL). Even a code fix per RE-SERV-078 would leave the contradictory docstring as a trap for the next reader.
- Locations: launcher/netinfo.py:224-228; launcher/netinfo.py:1-8
- Proposed class: EXTENSION of RE-SERV-078
- Justification: Same best_lan_ip preference mechanism and fix site as RE-SERV-078, adding the docstring location that a code-only fix for that finding would leave behind.

### CAND-04
- Label: CAND-F1 — Direct script execution broken suite-wide by missing sys.path prologues
- Mechanism: Files that do have unittest.main() (e.g. tests/test_art_fetch.py) crash on direct execution with ModuleNotFoundError: No module named 'launcher' because they lack a sys.path prologue, exactly like the 8 files in RE-SERV-102. Adding the __main__ guard alone therefore cannot deliver direct execution; a path prologue is also required.
- Locations: tests/test_art_fetch.py (example, no line given); the 8 files cited in RE-SERV-102
- Proposed class: EXTENSION of RE-SERV-102
- Justification: Same direct-execution failure and same test-file prologue fix site as RE-SERV-102, with additional instances beyond its 8 files and a second root cause at the same site.

### CAND-05
- Label: CAND-F2 — NFS4 endless-empty-fragments test is timing-fragile by construction
- Mechanism: The test sends 264,193 empty fragments (p32(0) * (MAX_RECORD//4 + 1)) which the server consumes one _recv per fragment, closing only after total > MAX_RECORD, against a 5 s client socket timeout. It passes only near the timeout budget (observed 2.5–5.1 s, 5/5 isolated) and failed with TimeoutError in the original baseline run, so its result is load-dependent rather than deterministic.
- Locations: tests/test_nfs4_server.py:890; nfs4_server/nfs4_server.py:49-50; nfs4_server/nfs4_server.py:505-517
- Proposed class: NEW
- Justification: Distinct test-fragility mechanism not covered by any redo finding; no existing finding addresses this test's fragment-count/timeout construction.

### CAND-06
- Label: CAND-H1 — h_trans2_find_first2 crashes on an empty directory
- Mechanism: h_trans2_find_first2 unpacks name, lp, sz = entries[0] with no emptiness check after _list_dir succeeds. An empty (or all-filtered) games subfolder therefore raises IndexError inside the handler instead of returning an empty/end-of-search reply.
- Locations: smbv1_server/smbserver_opl.py:1243-1244
- Proposed class: NEW
- Justification: Distinct empty-directory crash mechanism; RE-SERV-180 covers the same handler only for its one-entry-per-reply inefficiency, and no finding covers the entries[0] IndexError.

### CAND-07
- Label: CAND-H2 — Same _last_disc getattr pattern in the base server
- Mechanism: udpfs_server.py reads/writes sess._last_disc via a getattr default exactly as ps2servers_core.py does for RE-SERV-185. The verify report states this is not a defect on its own because the getattr makes it safe; it is only a note to mirror any RE-SERV-185 fix consciously.
- Locations: udpfs_server/udpfs_server.py:1627/1634
- Proposed class: REJECTED
- Justification: Not a defect on its own — the getattr default makes the pattern safe, per the verify report's own assessment.

### CAND-08
- Label: CAND-I1 — release.yml portable upload omits if-no-files-found entirely
- Mechanism: release.yml's portable upload step has no if-no-files-found key at all, so it defaults to warn: even without continue-on-error, a missing portable file would only warn at that step. The hard error setting exists only on the non-optional source/primary uploads.
- Locations: .github/workflows/release.yml:403 (step at :401-410)
- Proposed class: EXTENSION of RE-SERV-213
- Justification: Same fail-soft portable-upload mechanism and same release.yml step/fix site as RE-SERV-213, adding the omitted-key instance adjacent to that finding.
