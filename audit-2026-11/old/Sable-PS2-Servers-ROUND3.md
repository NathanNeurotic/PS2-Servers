# Sable Audit — PS2-Servers, ROUND 3 (UDPFS/UDPBD per-opcode wire validation + srvsvc/SPNEGO/auth)

- **Auditor:** Sable
- **Repo:** NathanNeurotic/PS2-Servers
- **Pin (READ, verified in clone 2026-11-14):** `main` @ `c5f021c4d38698860f56b03498adc261dbb8d8dd` — identical to the round-2 pin. `git status` clean at start and at end of the round.
- **Method:** read-only in the repo. Every opcode handler of every UDPFS and UDPBD edition was line-read at this HEAD. Six claims were additionally **proven by executing the pinned handlers in-process** against temp files (sends stubbed; the sandbox blocks UDP `sendto`, so nothing was executed over live UDP, and the Go handlers were not executed — their tables rest on reading, as in round 2). The harness lived in `/tmp`, wrote nothing to the repo, and ran with `PYTHONDONTWRITEBYTECODE=1`. Executed items are labelled EXECUTED with the observed numbers.
- **Prior rounds:** `Sable-PS2-Servers.md` (PS2SERVERS-001…040), `Sable-PS2-Servers-ROUND2.md` (041…055). This file continues numbering at 056 and does not repeat earlier findings; round-2 §7 named the per-opcode UDPFS/UDPBD tables as the largest remaining gap — this round closes it.

---

## 1. UDPFS — per-opcode wire-validation tables

The protocol (all editions): UDPRDMA DISCOVERY/INFORM handshake, then DATA packets carrying one message: OPEN 0x10, CLOSE 0x12, READ 0x14, WRITE_REQ 0x16, WRITE_DATA 0x17, LSEEK 0x1A, DREAD 0x1C, GETSTAT 0x1E, MKDIR 0x20, REMOVE 0x22, RMDIR 0x24, and the UDPBD subset BREAD 0x28 / BWRITE_REQ 0x2A (handle 0 = the shared block-device image). 14 client opcodes.

### 1a. Python standalone (`udpfs_server/udpfs_server.py`) — also the Core base class

Framing: `_handle_data` (:1641) requires ≥6 bytes, then computes `payload_size = hdr_word_count*4 + data_byte_count` from the DataHeader and **slices** `payload[:payload_size]` (:1651-1653) — a packet declaring more than it carries is silently truncated and processed (see D-3 / PS2SERVERS-060). Sequence numbers are enforced with duplicate re-ACK and peer-reset handling (:1674-1701). Handler exceptions cannot kill the session: `Session._run` catches all of them (:3319-3337).

| Opcode | Handler (file:line) | Fields trusted from the packet | Validation before use | Verdict |
|---|---|---|---|---|
| DISCOVERY | `_handle_discovery` :1569 | service_id (u16) | len ≥ 6 (:1577); service_id must equal 0xF5F5 (:1590); status queries answered before session creation (:1581) | PASS |
| OPEN 0x10 | `_handle_open` :1789 | is_dir, flags, mode, NUL-terminated path | len ≥ 8 (:1791); path via realpath containment `_resolve_path` (:1184) + entry resolution `_resolve_entry` (:1214); read-only server refuses write flags (:1838); virtual-CHD views refuse all write flag combinations (:1849-1855); handle table capped at 64/session (`_alloc_handle` :1413, `MAX_HANDLES` :228). No path-length cap (bounded only by the 4,096-byte receive buffer, `run` :1083) | PASS |
| CLOSE 0x12 | `_handle_close` :1944 | handle (i32) | len ≥ 8 (:1946); handle 0 (block device) unclosable; unknown handle → EBADF | PASS |
| READ 0x14 | `_handle_read` :1969 | handle, size (u32) | len ≥ 12 (:1971); **size refused above `max_transfer_bytes` before the handle lookup and before any allocation** (:1984-1991; default 8 MiB, :298, floor 256 KiB); dir handle → EBADF | PASS |
| WRITE_REQ 0x16 | `_handle_write_req` :2031 | handle, declared size (u32) | state reset on entry (:2033); len ≥ 12; read-only → EACCES; declared size over cap → EINVAL before any data is accepted (:2051-2058); handle must exist, not be a dir, and be writable (:2059-2071). Declared size is **not** stored — completion is governed by WRITE_DATA's total_chunks, and accumulation is capped per chunk (next row) | PASS |
| WRITE_DATA 0x17 | `_handle_write_data` :2083 | chunk_nr, chunk_size, total_chunks (u16s) | len ≥ 8 (:2085); chunk bytes slice-clamped to what the datagram carries (:2091); dropped when no write is armed (:2097-2105); strict chunk ordering (:2107-2110); assembled buffer refused past the transfer cap, per chunk (:2117-2124) | PASS (but see BWRITE completion) |
| LSEEK 0x1A | `_handle_lseek` :2189 | whence (u8), handle, offset (two i32) | len ≥ 16 (:2191); bad handle → EBADF; seek errors answered, including the ValueError an out-of-range whence raises (:2233-2240) | PASS |
| DREAD 0x1C | `_handle_dread` :2222 | handle | len ≥ 8 (:2224); handle must be a directory handle; entries that fail to stat are skipped rather than ending the listing (:2247-2273) | PASS |
| GETSTAT 0x1E | `_handle_getstat` :2294 | NUL-terminated path | len ≥ 4 (:2296); empty path = block-device capacity only when a device is configured (:2307); otherwise the same `_resolve_path` containment as OPEN | PASS |
| MKDIR 0x20 | `_handle_mkdir` :2368 | mode (u16), path | len ≥ 4; read-only refused; acts on the entry via `_resolve_entry`, so mkdir over a dangling symlink is EEXIST, not a directory created at the target (:2383-2390) | PASS |
| REMOVE 0x22 | `_handle_remove` :2399 | path | len ≥ 4; read-only refused; `_resolve_entry` (the link itself is deleted, never its target); `_through_linked_folder` refuses deletion through a symlinked/junction folder (:1251, :2420-2424); a real directory is EISDIR from lstat on every host (:2428-2431) | PASS |
| RMDIR 0x24 | `_handle_rmdir` :2440 | path | as REMOVE, plus the share root itself is refused (:2458-2461) and a symlink is ENOTDIR even when it points at a folder (:2469-2473) | PASS |
| BREAD 0x28 | `_handle_bread` :2483 | sector_count (u16), handle, sector_nr (u64) | len ≥ 16 (:2485); total bytes over the transfer cap → EINVAL (:2512-2518); sector offset past 2⁶³−1 → EINVAL (:2524-2530); past-end reads answer short with the honest byte count. No refusal for reading past the image end — the in-range prefix is served | PASS |
| BWRITE_REQ 0x2A | `_handle_bwrite_req` :2551 | sector_count (u16), handle, sector_nr (u64) | len ≥ 16; read-only/handle/writable all checked; declared bytes over cap → EINVAL (:2595-2602); offset + declared past 2⁶³−1 → EINVAL (:2608-2615). **Never checked against the block device's actual size** — see PS2SERVERS-056 | PARTIAL |
| BWRITE completion | `_complete_bwrite` :2156 (via WRITE_DATA) | the assembled buffer | writes `data[:expected_size]` and reports success **without ever comparing delivered bytes to the declared sector count** — see PS2SERVERS-057 | FAIL |

**Standalone tally: 12 PASS / 1 PARTIAL / 1 FAIL, framing PARTIAL.**

### 1b. Python Core (`udpfs_server/ps2servers_core.py`, `AutoUdpfsServer`) — the shipped path

Core subclasses the standalone and overrides exactly six methods (class docstring :90-110); **all 14 opcode handlers above are inherited unchanged, verdicts included** — PS2SERVERS-056 and -057 therefore apply to the shipped Core path exactly as to the standalone. What differs is transport-level validation and session lifecycle:

| Surface | Location | Difference from standalone | Verdict |
|---|---|---|---|
| DATA framing | `_route_data_via` :360-375 | declared `payload_size > len(data) - 6` → **packet dropped before queueing** (:373-374). The standalone truncates and processes (D-3) | PASS |
| DISCOVERY | `_handle_discovery` :267 | len ≥ 6; `DiscHeader.unpack` failure caught (:276-280); service check :281. When not streaming, a fresh DISCOVERY **closes the peer's handles** and resets the session (`_reset_session_state` :168-186); during a stream, handles are preserved pending DATA resolution (:318-335). The standalone never closes handles on DISCOVERY (D-4) | PASS (validation); behaviour diverges |
| DATA sequence/profile | `_handle_data` :387-505 | pending-discovery resolution, per-session Standard/Modulo classification (`classify_profile` :73-86), then delegates to the inherited `_handle_data` | PASS |

**Core tally: inherited handlers 12 PASS / 1 PARTIAL / 1 FAIL; its own transport validation PASS.**

### 1c. Go Edge (`native/ps2servers-edge/internal/udpfs/` + `internal/protocol`, `internal/filesystem`, `internal/session`)

Framing: `protocol.ParseDataPacket` (packet.go:131-152) computes `n = HeaderWords*4 + DataBytes` and **errors when `n > len(packet)-6`** (:142) — the packet is dropped, matching Core, not the standalone. Receive buffer is 64 KiB (server.go `maxDatagram`). Discovery is handled at negotiation.go:32 (len ≥ 6, service check), with status queries answered before any session exists (server.go dispatch). Sessions cap at 256, refused at the cap (server.go:455 — see PS2SERVERS-062). Path resolution is `filesystem.Root` (root.go): `candidate()` refuses any `..` component, drive-qualified and device paths outright (root.go candidate, hasDotDot), `Resolve` EvalSymlinks + containment, `Entry`/`RealFolders` mirror the Python entry/link rules.

| Opcode | Handler (file:line) | Validation before use | Verdict |
|---|---|---|---|
| DISCOVERY | negotiation.go:32 | len ≥ 6; service_id checked; non-streaming → `State.Reset` (closes handles, session.go:207); streaming → pending-resolution, handles kept | PASS |
| OPEN 0x10 | operations.go:297 | len ≥ 8 (:298); **path capped at 1,024 bytes** (`maxPathBytes` server.go:24; `cString` operations.go:165-173) → ENAMETOOLONG; handles capped at 64 (`maxHandles`, checked :313); write opens refuse compressed/virtual targets and take the single-writer reservation, EBUSY on conflict (`openForWrite`, operations.go) | PASS |
| CLOSE 0x12 | operations.go:473 | len ≥ 8; handle 0 unclosable; unknown → EBADF | PASS |
| READ 0x14 | operations.go:491 | len ≥ 12; size over `transferCap` → EINVAL **before allocation** (:499-508; cap is RAM-derived, preferred 8 MiB — D-9); nil/dir handle → EBADF | PASS |
| WRITE_REQ 0x16 | operations.go:547 | len ≥ 12; state reset on entry; read-only → EACCES; declared size over cap compared in int64 (no 32-bit wrap on MIPS) → EFBIG (:563-570); read-only handle → EACCES | PASS |
| WRITE_DATA 0x17 | operations.go:598 | len ≥ 8; chunk_size clamped to delivered bytes (:617-619); no armed write → dropped; **out-of-order chunk aborts the whole write with EIO** (:621-629 — D-7); accumulated and BWRITE-declared-length caps enforced per chunk (:631-647) | PASS |
| LSEEK 0x1A | operations.go:696 | len ≥ 16; offset assembled as u32 pair (same bit pattern as Python's signed pair); seek error → errno reply | PASS |
| DREAD 0x1C | operations.go:724 | len ≥ 8; directory handle required; serves the OPEN-time snapshot (entries were resolved/stated at List time; out-of-root symlinks are omitted from the listing entirely — D-13) | PASS |
| GETSTAT 0x1E | operations.go:767 | len ≥ 4; path via `cString` (1,024 cap); empty path = block-device capacity only when a device exists | PASS |
| MKDIR 0x20 | pathops.go:91 | shared `pathRequest` (pathops.go): len ≥ 4, read-only/no-root refused before the path is read, `Root.Entry` semantics as Python | PASS |
| REMOVE 0x22 | pathops.go:115 | as Python, plus EBUSY when another peer holds the file open for writing (server.go `removeUnlessWriting`) | PASS |
| RMDIR 0x24 | pathops.go:168 | as Python; share root refused; lstat-decided ENOTDIR/EISDIR on every host | PASS |
| BREAD 0x28 | blockdev.go:132 | len ≥ 16 via `blockRequest` (:120-129); count ≥ 1; total over cap → EINVAL; file handles: sector range checked against offset overflow (:160-166); block device: **`inRange` requires the whole range inside the image** (:87-95, :199-207 — D-8) | PASS |
| BWRITE_REQ 0x2A + completion | blockdev.go:219, :329 | read-only refused at both layers; declared total over cap → EFBIG; `inRange` on the declared range before arming (:248-254); **completion requires delivered bytes == declared bytes exactly** (:330-335) | PASS |

**Go tally: 14 PASS / 0 PARTIAL / 0 FAIL, framing PASS.** One non-opcode PARTIAL: session admission at the 256 cap (PS2SERVERS-062).

### 1d. UDPFS divergence summary — where editions disagree on the same opcode

| # | Opcode / surface | Standalone | Core | Go Edge | Assessment |
|---|---|---|---|---|---|
| D-1 | BWRITE vs image size | no check (056) | no check (inherited) | `inRange` refuses | Python editions FAIL; Go is the correct model |
| D-2 | BWRITE delivered vs declared bytes | truncates, reports success (057) | inherited | exact-match or EINVAL | Python FAIL; Go correct |
| D-3 | DATA framing, declared > carried | truncates, processes (060) | drops | drops (error) | Standalone is the outlier |
| D-4 | DISCOVERY while not streaming | handles & write state survive | handles closed, session reset | handles closed (`Reset`) | Standalone diverges from both shipped paths; each behaviour is deliberate in its own comments (standalone preserves for loader handoff; Core/Go resolve handoff explicitly on DATA), but the same console gesture means different things per edition |
| D-5 | Path with `..` components | allowed if realpath stays inside root | inherited | refused outright (ErrEscape) | Both safe; Go stricter — a path Python serves (`games/../games/x.iso`) is EACCES on Edge |
| D-6 | Path length | no cap (≤ receive buffer) | inherited | 1,024 B cap → ENAMETOOLONG | Go stricter; no client impact known |
| D-7 | Out-of-order WRITE_DATA chunk | chunk dropped + ACK, write stays armed for retransmit | inherited | whole write aborted, EIO | Both corruption-safe; Python tolerates reordering, Go fails the transfer and makes the client restart it |
| D-8 | BREAD straddling image end | serves the in-range prefix, honest short count | inherited | refuses the whole read (EINVAL) | Python is the more standard block-device answer; Go's refusal is documented ("refuse rather than pad") |
| D-9 | READ/WRITE/BREAD size cap | fixed 8 MiB default, `--max-transfer-bytes`, floor 256 KiB | inherited (same option) | RAM-derived (preferred 8 MiB, smaller on low-RAM targets) | Agree on desktop-class machines; Edge refuses transfers Python serves on a 32 MB router — by design, documented both sides |
| D-10 | Session cap reached (256) | evicts least-recently-active peer (`_evict_oldest_session_locked`, udpfs_server.py) | inherited | refuses the new peer outright (server.go:455) | → PS2SERVERS-062 |

---

## 2. UDPBD — per-command tables

UDPBD (port 0xBDBD) has four client commands: INFO 0x00, READ 0x02, WRITE 0x04, WRITE_RDMA 0x05. Editions: the Python standalone server and Go Edge. (The block commands carried *inside* UDPFS are BREAD/BWRITE_REQ, tabled in §1; round-1 PS2SERVERS-005 covers the three-implementations duplication itself.) The Python server additionally drives two launcher-supplied device kinds — raw devices and virtual exFAT — through the same handlers; their extra validation is noted where it differs.

### 2a. Python (`udpbd_server/udpbd_server.py`)

Dispatch discipline is good: the run loop (:311-360) drops datagrams < 2 bytes, gates READ/WRITE at ≥ 8 bytes and WRITE_RDMA at ≥ 6 (:344-349), and catches `struct.error`/`IndexError` (logged, verbose-only) and `OSError` (always logged) per packet, so no single datagram kills the server. The device is single, the write state global (`_write_left`, `_write_owner`).

| Cmd | Handler (file:line) | Fields trusted | Validation before use | Verdict |
|---|---|---|---|---|
| INFO 0x00 | `_handle_info` :194 | cmdid only | none needed; replies sector size + image sector count; resets any armed write state (:199) | PASS |
| READ 0x02 | `_handle_read` :205 | sector_nr (u32), sector_count (u16) | **image files: no capacity check at all** — seek anywhere, `BlockDevice.read` zero-pads past the end to the full requested size (:121-125), so any sector_nr/count is served (up to 65,535 sectors ≈ 32 MiB of generated traffic). **Raw devices only:** `sector_nr + sector_count > sector_count()` raises OSError (:208-209), which the run loop logs and answers with silence | PARTIAL |
| WRITE 0x04 | `_handle_write` :238 | sector_nr (u32), sector_count (u16) | **image files: no capacity check** — the write is armed at any sector; the seek is not wrapped for images, and completing RDMA past the image end **extends the file** (EXECUTED, PS2SERVERS-058). Raw devices: `seek(sector_nr, sector_count)` validates and its OSError is answered with WRITE_DONE −1 (:256-262) | FAIL (image mode) |
| WRITE_RDMA 0x05 | `_handle_write_rdma` :263 | block_shift (4 bits), block_count (9 bits) from the packet itself | refused unless a write is armed (:266-272); **owner must match (addr, cmdid) exactly** (:273-274); claimed size must fit inside the datagram or the packet is dropped (:279-285); write clamped to the armed region (`min(size, _write_left)` :286); read-only images discard data but complete with WRITE_DONE −1 rather than fake success (:302-308) | PASS |

**Python UDPBD tally: 2 PASS / 1 PARTIAL / 1 FAIL.**

### 2b. Go Edge (`native/ps2servers-edge/internal/udpbd/server.go`, `protocol.go`)

Dispatch (server.go `dispatch`) mirrors Python's length gates (≥8 for READ/WRITE, ≥6 for WRITE_RDMA, <2 dropped). One write state, mutex-guarded, positional `ReadAt`/`WriteAt` throughout (no shared cursor). Image files only — no raw-device or virtual-exFAT modes exist in Edge.

| Cmd | Handler (file:line) | Validation before use | Verdict |
|---|---|---|---|
| INFO 0x00 | `handleInfo` server.go:344 | replies sector size + count; resets any armed write state, as Python | PASS |
| READ 0x02 | `handleRead` server.go:370 | count ≥ 1; **`sectorNr+sectorCount > sectorCount()` → request dropped** (error counted, no reply) (:379-386) — the check Python applies only to raw devices, applied to every image | PASS |
| WRITE 0x04 | `handleWrite` server.go:440 | count ≥ 1; same capacity refusal before arming (:446-452); read-only still arms (data discarded by `writeAt`) and completes with −1, as Python | PASS |
| WRITE_RDMA 0x05 | `handleWriteRDMA` server.go:471 | armed-write required; claimed size must fit the datagram (:483-489); clamped to the armed region (:490-492). **Owner check is weaker than Python's: the peer matches on full address *or on IP alone*, and cmdid is never compared** (:476) — a second client on the same host IP (different port, any cmdid) can contribute blocks into an armed write. → PS2SERVERS-061 | PARTIAL |

**Go UDPBD tally: 3 PASS / 1 PARTIAL.**

### 2c. UDPBD divergence summary

| # | Surface | Python | Go Edge | Assessment |
|---|---|---|---|---|
| D-11 | READ/WRITE capacity (image) | WRITE: none (058); READ: none, zero-pads | both refused past the image | Go (and Python's own raw path) are the correct models |
| D-12 | WRITE_RDMA ownership | exact (addr, cmdid) | addr *or* same-IP, cmdid ignored (061) | Python stricter and correct |
| D-13 | Foreign traffic vs an armed write | any peer's INFO **or READ** resets the write (`_write_left = 0`, :199, :214) | INFO resets (any peer); READ resets only the owner's write (server.go `handleRead`) | Python lets a second console's read silently abort the first console's save; Go only where INFO is concerned. INFO behaviour agrees; READ behaviour diverges |
| D-14 | READ past image end | served, zero-padded to full length | refused silently | Same shape as D-8, opposite winner: here the padding is the upstream protocol's documented behaviour (`BlockDevice.read` comment, :121-125) and Go's refusal is the deviation — recorded as divergence, not a defect, for READ specifically |

---

## 3. SMB2 surfaces round 2 left: srvsvc RPC, SPNEGO, NTLM auth

### 3a. srvsvc (`smb2_server/smb2_srvsvc.py`, 289 lines) — served on IPC$ via `smb2_server.py` `_create_pipe` (:979) and FSCTL_PIPE_TRANSCEIVE (:1385-1396)

| Surface | Location | Validation | Verdict |
|---|---|---|---|
| PDU framing | `transact` :144-152 | version must be 5, minor 0/1; `frag_len > len(data)` and `frag_len < 16` refused (:150). **But the body slice `data[16:frag_len - auth_len …]` (:152) is never checked: an `auth_len` larger than `frag_len − 16` makes the slice index negative, and Python wraps it — the parser then reads a body *longer* than the PDU declares instead of rejecting the PDU** (EXECUTED, PS2SERVERS-059) | PARTIAL |
| BIND / ALTER_CONTEXT | `_bind` :164 | body ≥ 12; per-context bounds checked (`at + 24 > len(body)` → RpcError); only the srvsvc abstract syntax + NDR32 transfer syntax accepted, everything else gets a proper provider rejection; `max_frag` clamped to [1024, 4280] | PASS |
| REQUEST | `_request` :212 | body ≥ 8; unbound pipe or wrong context id → NCA_PROTO_ERROR fault; any opnum other than 15 → NCA_OP_RNG_ERROR; fragmented requests refused with a fault (documented: requests here are a few dozen bytes) | PASS |
| NDR reading | `Reader` :97-118 | every `u32` bounds-checked; `skip_string` refuses `actual > max_count` and strings running past the stub | PASS |
| NetrShareEnum stub | `_share_enum` :245 | levels restricted to 0/1/2, others answered with ERROR_INVALID_LEVEL; the union discriminant is read but not checked against the level (cosmetic — the answer is built from the level alone) | PASS |
| Pipe transport | `write`/`read` :133-141 + `smb2_server.py` IOCTL glue | each SMB WRITE/TRANSCEIVE must carry a whole PDU — there is no reassembly buffer; a client that fragments a PDU across writes gets RpcError → STATUS_INVALID_PARAMETER. Real clients (Explorer, libsmb2, smbclient) transceive whole PDUs; recorded as a design limit, not a defect | PASS (by design) |

**srvsvc verdict: PARTIAL** — one framing wart (059); everything downstream of it is bounds-checked, and every malformed input ends in an RpcError or an RPC fault, never a crash or a fabricated answer.

### 3b. SPNEGO / NTLMSSP (`smb2_server/smb2_spnego.py`, 360 lines)

| Surface | Location | Validation | Verdict |
|---|---|---|---|
| Token extraction | `extract_ntlm` :117 | finds the NTLMSSP signature anywhere in the security buffer and takes the rest — accepts SPNEGO-wrapped and raw tokens alike; the leniency is deliberate and documented (:119-127), and every field inside the message is still bounds-checked downstream | PASS |
| Field decoding | `_field` :151 | (Len, MaxLen, Offset) triples: `at + length > len(token)` → AuthError; zero-length fields short-circuit | PASS |
| Message trimming | `trim` :179 | end computed from the message's own fields; result clamped to the actual token length; fields are re-validated by `_field` afterwards | PASS |
| AV pairs | `av_pairs` :278 | tolerant by design (a truncated final pair is sliced short and the loop ends); documented — the proof check decides whether the content mattered | PASS |
| AUTHENTICATE parsing | `parse_authenticate` :298 | < 64 bytes → AuthError; all six strings via `_field`; the MIC is accepted only when the client's own AV flags claim one *and* the payload provably starts after the MIC field, else AuthError (:317-330) | PASS |
| MIC verification | `verify_mic` :344 | HMAC-MD5 over NEGOTIATE+CHALLENGE+AUTHENTICATE (MIC zeroed), compared with `hmac.compare_digest`; absent MIC → pass, which is the protocol's rule (MIC exists only when the client negotiated it) | PASS |

**SPNEGO verdict: PASS.** No length field is used before it is checked; malformed input raises AuthError, which the SESSION_SETUP handler converts to a logon failure.

### 3c. NTLMv2 auth (`smb2_server/smb2_auth.py`, 324 lines)

| Surface | Location | Validation | Verdict |
|---|---|---|---|
| Response splitting | `parse_ntlmv2_response` :200 | < 24 bytes → AuthError; the fixed NTLMv2 blob header must match, so an NTLMv1 response is refused as unsupported instead of being sliced into a proof and a nonsense blob | PASS |
| Proof check | `Authenticator.verify` :277 | expected proof compared with `hmac.compare_digest` (:321); **unknown users get a full dummy proof computation against a random password before the same failure** (:305-313), so the handler is not a user-existence oracle | PASS |
| Challenge | `server_challenge` (auth module) | 8 bytes from `secrets` (OS CSPRNG), fresh per session setup; the blob timestamp is deliberately not freshness-checked (documented: a PS2 has no battery-backed clock; replay is answered by the fresh challenge) | PASS |
| MD4 / NT hash | `md4`, `nt_hash` | hand-rolled MD4 (hashlib lacks it under OpenSSL 3 — round-1 PS2SERVERS-014 territory). **EXECUTED this round: `nt_hash("password")` reproduces the published NT-hash constant `8846f7ea…b7586c`**, so the implementation is byte-correct against the reference vector | PASS |

**Auth verdict: PASS** — the strongest-validated surface audited in this round.

---

## 4. New findings (PS2SERVERS-056…), sorted by benefit

## PS2SERVERS-056 / UDPFS (Python, both editions): BWRITE is never checked against the block device's size — a client can extend the operator's image at any sector and is told it succeeded
- **Category:** 6 (Bug / security)
- **Location(s):** `PS2-Servers/udpfs_server/udpfs_server.py:2551-2618` (`_handle_bwrite_req`: the only bounds are the transfer cap and 2⁶³−1) and `:2156-2187` (`_complete_bwrite` → `bd_write` → `os.pwrite`, which extends the file); inherited unchanged by Core (`ps2servers_core.py` overrides no write handler). The correct model is in-repo: Go's `inRange` (`native/ps2servers-edge/internal/udpfs/blockdev.go:87-95`, enforced at :248-254)
- **Problem:** EXECUTED at this pin: against a 4-sector (2,048-byte) image, BWRITE_REQ sector=100 count=1 plus one 512-byte WRITE_DATA chunk produced **WRITE_DONE result 0 and an image grown to 51,712 bytes** (101 sectors) — the write landed at offset 51,200 in a file the operator sized at 2,048 bytes, with a sparse hole between. Any unauthenticated LAN peer that can speak UDPRDMA (sessions are created by any datagram — round-1 PS2SERVERS-021) can grow the image file arbitrarily (sector_nr is a full u64; only the per-write transfer cap and the 2⁶³ check apply), or scribble into a sparse region a later BREAD will read back as zeroes interspersed with the written block. The file-handle BWRITE path may legitimately extend *files* (both editions document that); the shared block-device image is the case with a known, fixed size — GETSTAT '' already reports it from `bd_sector_count` — and it is the one not checked.
- **Fix:** In `_handle_bwrite_req`, when the handle is the block device, refuse (EINVAL, as the cap checks already answer) when `sector_nr + sector_count > self.bd_sector_count` — the same comparison Go's `inRange` makes. One comparison at the site that already has both numbers.
- **Justification (addition):** Concrete, executed bug — unauthenticated extension/corruption of the operator's disk image. The check is a single comparison using state the server already keeps; the correct implementation exists in the same repo to copy.
- **Benefit:** The block-device image's size becomes a server-enforced invariant again; a whole class of image corruption (and unbounded sparse-file growth on the host) closes in both Python editions at once, since Core inherits the handler.
- **Effort:** S. **Risk:** Low (no conformant client writes past the image it was told the size of — GETSTAT '' and INFORM exist precisely so it knows). **Confidence:** High (READ + EXECUTED)

## PS2SERVERS-057 / UDPFS (Python, both editions): BWRITE completion never compares delivered bytes to the declared sector count — short writes are silently partial and reported as success
- **Category:** 6 (Bug — correctness)
- **Location(s):** `PS2-Servers/udpfs_server/udpfs_server.py:2156-2187` (`_complete_bwrite`: writes `data[:expected_size]`, adds `expected_size` to the byte stats, answers `_send_write_done(addr, 0)`); the correct model is Go's `completeBlockWrite` (`native/ps2servers-edge/internal/udpfs/blockdev.go:329-335`: `len(buf) != expected` → EINVAL, nothing written)
- **Problem:** EXECUTED at this pin: BWRITE_REQ declaring 4 sectors (2,048 bytes) followed by a single WRITE_DATA chunk carrying 512 bytes with `total_chunks=1` produced **WRITE_DONE result 0 (success), exactly 512 bytes changed in the image, and a session stat credited with 2,048 bytes written**. Completion is driven by the client's chunk *count*, never by byte count, so a client (or a lossy link, or a buggy loader stage) that under-delivers gets a partial block write blessed as complete — for VMC and HDD-image saves, that is a silently corrupted save the console believes committed. The file-write path does not have this defect (it reports the actual `fh.obj.write` return); only the block path substitutes the declared number for the delivered one, in both the reply semantics and the stats.
- **Fix:** In `_complete_bwrite`, before writing: if the assembled buffer's length differs from `sector_count * sector_size`, answer EINVAL and write nothing — Go's one comparison. (Padding/truncating to the declared size is the alternative; refusing is what the in-repo correct edition does, and a short block write has no honest partial form.)
- **Justification (addition):** Concrete, executed correctness bug on the save path — the server asserts a write happened that did not. One comparison; no new mechanism.
- **Benefit:** A false-success class on block writes closes in both Python editions; stats stop crediting undelivered bytes.
- **Effort:** S. **Risk:** Low (a client whose chunks are intact delivers exactly the declared bytes; only under-delivery changes behaviour, from silent corruption to a visible error the client can retry). **Confidence:** High (READ + EXECUTED)

## PS2SERVERS-058 / UDPBD (Python): image files get none of the capacity validation raw devices get — WRITE extends the image, READ streams up to ~32 MiB of padding for any range
- **Category:** 6 (Bug / security)
- **Location(s):** `PS2-Servers/udpbd_server/udpbd_server.py:205-236` (`_handle_read`: the capacity check at :208-209 is gated on `getattr(self.bd, "is_raw", False)`), `:238-261` (`_handle_write`: no capacity check for images; only the raw seek is validated, via its own OSError), `:121-125` (`BlockDevice.read` zero-pads to the requested size). The correct models are in-repo: the same file's raw path, and Go's `handleRead`/`handleWrite` (`native/ps2servers-edge/internal/udpbd/server.go:379,446`)
- **Problem:** EXECUTED at this pin: CMD_WRITE sector=1000 count=1 plus one WRITE_RDMA block against a 2,048-byte image produced **WRITE_DONE result 0 and an image grown to 512,512 bytes** — UDPBD is the protocol OPL uses for VMC saves, and its image is a fixed-size virtual disk; nothing in the image path ever compares sector_nr/sector_count to `bd.sector_count()`, even though the raw-device path in the same handlers does exactly that. On the read side, any sector_nr with sector_count up to 65,535 makes the server generate and stream the full count of zero-padded sectors (~32 MiB of UDP traffic per 8-byte request) — an amplification the Go edition refuses outright. (Go's refusal of out-of-range *reads* is itself a divergence from upstream's pad behaviour — see D-14; the WRITE half has no such defence: a disk image that grows when written past its end is wrong in every edition's own terms, and Python's raw path already treats it so.)
- **Fix:** Drop the `is_raw` gate: apply the existing `sector_nr + sector_count > self.bd.sector_count()` check in `_handle_read` and the equivalent arm-time check in `_handle_write` for every device kind, answering WRITE with the WRITE_DONE −1 the seek-failure path already sends. The check, the error and the reply shape all already exist in this file.
- **Benefit:** The image's size becomes server-enforced in the Python UDPBD (as it already is for raw devices and in Go); the read-amplification and image-extension holes close together.
- **Effort:** S. **Risk:** Low for WRITE (conformant clients write inside the image INFO told them); Med for READ if any client deliberately reads past the end expecting padding — INFO reports the sector count, so a conformant client never does, but this half can be split off and left as D-14 if the owner prefers. **Confidence:** High (READ + EXECUTED for WRITE)

## PS2SERVERS-059 / srvsvc: the PDU body slice wraps when auth_len exceeds the declared PDU — the parser reads past frag_len instead of rejecting the PDU
- **Category:** 6 (Bug — robustness)
- **Location(s):** `PS2-Servers/smb2_server/smb2_srvsvc.py:152` (`body = data[16:frag_len - auth_len if auth_len else frag_len]`), in `transact` (:144-161); reached from any SMB2 client via FSCTL_PIPE_TRANSCEIVE (`smb2_server/smb2_server.py:1385-1396`)
- **Problem:** EXECUTED at this pin: a BIND PDU declaring `frag_len=28` (header + 12 bytes — too short for its one declared context, so the control call with `auth_len=0` correctly raises `RpcError: short bind context`) was sent again with `auth_len=200`. The slice end `28 − 200` goes negative, Python wraps it to `len(data) − 172`, and the parser consumed a 112-byte body from beyond the declared PDU length — returning a **BIND_ACK with the pipe bound**, built entirely from bytes the PDU's own header said were not part of it. Impact is bounded — Python slicing cannot leave the received buffer, and every downstream parser is bounds-checked, so the worst case is a malformed PDU being parsed as a different malformed PDU — but the one length field that defines the PDU is the one not validated, in the module whose whole job is parsing untrusted RPC.
- **Fix:** In `transact`, refuse when `auth_len > frag_len - 16` (alongside the existing `frag_len > len(data)` / `frag_len < 16` checks) and slice with the validated numbers. One comparison.
- **Justification (addition):** A declared-length field that can invert the parser's bounds is a concrete defect even when today's blast radius is small; the fix is a single comparison next to two existing ones.
- **Benefit:** frag_len means what it says; a class of "parser reads outside the declared message" surprises closes in the RPC layer.
- **Effort:** S. **Risk:** Low (no conformant client sends auth_len beyond its PDU; NTLM auth trailers in RPC are sized within frag_len by definition). **Confidence:** High (READ + EXECUTED)

## PS2SERVERS-060 / UDPFS standalone truncates over-declared DATA packets and processes them; the shipped Core and Go drop them — the rollback copy validates framing differently from the product
- **Category:** 2 (Duplication — divergent validation) / 6
- **Location(s):** `PS2-Servers/udpfs_server/udpfs_server.py:1651-1653` (`actual_payload = payload[:payload_size]`, slicing clamps silently) vs `PS2-Servers/udpfs_server/ps2servers_core.py:373-374` (`if payload_size > len(data) - 6: return`) and `PS2-Servers/native/ps2servers-edge/internal/protocol/packet.go:142` (parse error → drop)
- **Problem:** EXECUTED at this pin: a DATA packet whose DataHeader declares 100 payload bytes but which carries a 12-byte READ_REQ was **dropped unqueued by Core** and **processed by the standalone** (it ACKed and ran the handler, entering its reply/retransmit machinery). Today's practical impact is small — every handler re-checks its minimum length, so the truncated packet reaches only checks that were going to refuse most of it anyway — but this is precisely the failure mode round-1 PS2SERVERS-001/-002 exist to warn about, now found *inside validation itself*: the standalone is kept as the rollback and upstream-redistribution copy, and its framing layer accepts a packet shape both shipped paths reject. Any future handler that trusts the framing layer's length (the natural assumption, since two of three editions guarantee it) becomes a bug that exists only in the standalone.
- **Fix:** Give the standalone the same one-line guard Core has — in `_route_data` or at the top of `_handle_data`, drop when the declared payload size exceeds the datagram. Delete the divergence, keep the copies behaviourally identical at the framing layer (which is the stated purpose of keeping the standalone: same protocol, older entry point).
- **Benefit:** One framing contract across all Python copies; the "edited/validated in one copy only" class loses its current live example.
- **Effort:** S. **Risk:** Low (no conformant client declares more than it sends; Core has shipped this check). **Confidence:** High (READ + EXECUTED)

## PS2SERVERS-061 / UDPBD (Go): WRITE_RDMA ownership is weaker than the Python original — cmdid is ignored and any peer on the same IP may feed blocks into another peer's write
- **Category:** 6 (Bug / security)
- **Location(s):** `PS2-Servers/native/ps2servers-edge/internal/udpbd/server.go:476` (`peerMatch := st.peer == peer.String() || (st.peerIP != "" && st.peerIP == peer.IP.String())`); the stricter original is `PS2-Servers/udpbd_server/udpbd_server.py:273-274` (`_write_owner != (addr, cmdid)` → drop)
- **Problem:** Python arms a write to exactly one (address, cmdid) pair: a block contributes only if it comes from the same UDP endpoint *and* carries the handshake's command id. Go accepts a block from the same endpoint, **or from any endpoint sharing the source IP** (e.g. a second console behind the same NAT, or another process on the same host), with **any cmdid**. UDPBD writes are sequential and unauthenticated by design — the owner check is the only thing deciding whose bytes land in the image mid-save — so the wider match widens the one guard the protocol has. The IP fallback appears to exist for consoles whose source port shifts; Python's history shows the same pressure was answered there by keeping the strict pair and letting the client re-handshake (its `_handle_write` re-arms on a fresh CMD_WRITE).
- **Fix:** Match Python: record the cmdid in `writeState` at `handleWrite` and require `(peer, cmdid)` equality in `handleWriteRDMA`; drop the IP-only branch. If a port-shifting console is a real, named case, handle it as Python does — a fresh CMD_WRITE from the new endpoint re-arms — rather than by loosening block admission for everyone.
- **Benefit:** Edge's block admission becomes exactly as strict as the hardware-validated implementation it ports; one cross-console save-corruption path closes.
- **Effort:** S. **Risk:** Low–Med (a console that legitimately changes source port mid-write would need to re-issue CMD_WRITE — which is the Python server's shipped behaviour). **Confidence:** High (READ)

## PS2SERVERS-062 / UDPFS (Go): at the 256-session cap Edge refuses new consoles outright, where Python evicts the least-recently-active peer — a full table of stale sessions locks real consoles out for up to the peer timeout
- **Category:** 5 (Performance / availability) / 6
- **Location(s):** `PS2-Servers/native/ps2servers-edge/internal/udpfs/server.go:455-458` (`getWorker`: `if len(s.sessions) >= 256 { … return nil }` — the DISCOVERY/DATA is then simply dropped); the contrasting model is `PS2-Servers/udpfs_server/udpfs_server.py` `_get_or_create_session` + `_evict_oldest_session_locked` (evict LRU at the cap, with the reasoning in comments: a session is created by an unauthenticated datagram, and a real console whose port shifted must still get in)
- **Problem:** Both editions cap sessions at 256 and reap after the peer timeout (1 hour by default in both). They differ at the cap: Python drops the *least recently active* session to admit the new peer; Go drops the *new peer*. After any burst of 256 distinct source ports (a port-scanning device, a misbehaving client, a busy household NAT), Edge serves nobody new until entries age out — up to an hour of a games server refusing every console while holding 256 idle sessions, with one log line per refused peer as the only sign. This is round-1 PS2SERVERS-021's cap with its failure mode inverted: the bound exists, but it binds the wrong side.
- **Fix:** Adopt Python's eviction: at the cap, expire/evict the least-recently-active session (the reaper's idle data is already tracked per session) and admit the new peer. No new mechanism — the policy exists in the sibling implementation, including its rationale.
- **Benefit:** Worst case at the cap becomes "an idle console loses its handles" (Python's shipped, reasoned trade-off) instead of "every console is refused for up to an hour".
- **Effort:** S–M. **Risk:** Low (eviction only fires at 256 concurrent peers, which no real deployment reaches with live consoles). **Confidence:** High (READ)

---

## 5. Revisions to rounds 1–2

No round-1 or round-2 verdict is overturned. Three are sharpened:

- **PS2SERVERS-001 / -002 (UDPFS implemented three times; standalone kept as rollback) — strengthened, not changed.** Round 3 found the predicted drift inside validation itself: the standalone's DATA framing (060) and DISCOVERY handle lifecycle (D-4) now differ from the shipped Core path, and the BWRITE defects (056, 057) live in the shared base — meaning they are in the shipped product *because* the handler layer is shared, while the framing defect exists *because* the transport layer is not. Both halves of the duplication finding now have per-opcode evidence.
- **PS2SERVERS-021 (session allocation) — addendum.** The 256 cap exists in all editions, as round 1 said; round 3 adds that the editions disagree about what happens *at* the cap (evict vs refuse), which is now PS2SERVERS-062.
- **PS2SERVERS-013 (single-writer exclusion) — re-confirmed in passing, unchanged.** Go's reservation was re-read at this pin in the OPEN-for-write path (`openForWrite`/`reserveWriter`); the Python handlers still have no equivalent — BREAD/BWRITE/WRITE in §1 were checked for it again as part of the opcode pass.
- **Round-2 §7 ("per-opcode UDPFS/UDPBD tables not done") — closed** by §1–§2 of this file, with the standing caveat that Go handlers were validated by reading (their test suites remain blocked by the sandbox's UDP `sendto` restriction) while the Python handlers were additionally executed.

## 6. Still unread / not done (honest limits of this round)

- **Go handlers were not executed.** The opcode tables for Edge rest on line-reading; the sandbox blocks UDP `sendto`, so neither Edge's own tests nor a live harness could run its handlers. Python handlers *were* executed in-process (six expectations, §3–§4 evidence).
- **`udpfs_server/compressed_iso/` internals** (CHD/CSO/ZSO header parsing behind the virtual OPEN/GETSTAT paths): the wire handlers validate before calling in, but the wrappers' own parsing of file-controlled headers was not line-audited this round.
- **`launcher/raw_storage.py`** (the UDPBD raw-device implementation): the raw-mode verdicts rest on its call sites in `udpbd_server.py` (the two-argument `seek` and its capacity OSError); its internals were not line-read. Same for **`launcher/virtual_exfat.py`** (UDPBD virtual mode).
- **`router_status.py`** (the status protocol answered inside `_handle_discovery`): surface-checked only — round-1 PS2SERVERS-012 covers its duplication; its parser was not line-audited.
- **UDPFS send/window machinery** (retransmit buffers, ACK windows, metrics): read only where it intersects validation; not audited as a subsystem. One harness observation, recorded without a finding: an un-ACKed reply retransmits up to the documented window-retry budget, by design.
- **`smb2_server.py`'s SESSION_SETUP glue** around §3's modules (how guest/open mode is selected, how the verified key is installed for signing): the auth/SPNEGO modules were line-read in full, the calling handler was spot-read at the pipe/IOCTL sites only.
- **`smb2_crypto.py` internals**: not revisited (round-1 PS2SERVERS-014 stands).
- **Go `internal/session` state machine** beyond Reset/HandOff/CloseHandle/Close, **`internal/compression`** image parsing, and **udpfs `status.go`**: surface-read only.
- **Secrets:** none found, none printed; round-1 PS2SERVERS-040 is not repeated.
- **Repo state:** `git status` clean at `c5f021c4d386` at the end of the round; all artefacts are in /tmp.
