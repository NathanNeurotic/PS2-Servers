# Network tools expansion roadmap

Status: researched proposal, 2026-10-15. These targets are not implemented by
this document. Deliver working increments, not six empty tabs or an all-at-once
replacement suite. Preserve existing streaming and RetroAchievements work.

## Goal and boundaries

Provide one desktop application for serving PC files to PS2 homebrew, moving
files to/from a console, development/debugging, and managing network-accessible
PS2 HDD installations. Windows is the first packaged acceptance target; shared
protocol code must also support Linux/macOS. Edge/router support is a separate
capability decision: do not imply a desktop client job is a router server.

Extend [the existing NBD/PS2Client proposal](PLAN-NBD-AND-PS2CLIENT.md). It remains
useful for UI and read/write policy, but its earlier NBD GO-first assumption is
corrected below. The current `launcher/servers.py` registry describes listening
server processes; finite transfers and bidirectional sessions need an additional
job/session abstraction rather than being forced into that lifecycle.

## Targets and verified roles

| Requested target | PC role and console counterpart | Delivery decision |
| --- | --- | --- |
| RadHostClient parity | Serve PC files over the PS2Client/`host:` request channel used by compatible LaunchELF/PS2Link builds | Implement a contained HOST file service with PS2Client-compatible wire fixtures; RadHostClient is a behavior reference, not an assumed redistributable dependency. |
| OPLServer parity | PC SMBv1 server for OPL | Audit and close gaps in the SMB mode we already ship. The common OPL-Server is not a new transport. |
| FTP | PC FTP client, console FTP server such as ps2ftpd/LaunchELF | Start with browse/download, then explicitly managed uploads and mutations. A PC FTP server needs a confirmed PS2 client before becoming a separate target. |
| NBD | PC userspace client, OPL/lwNBD exports the console HDD | Implement listing and read-only image export first; no driver, WSL or mounted virtual disk required for this increment. |
| PS2Link | PC sends commands, receives logs and services HOST requests; console runs ps2link | Reuse the HOST transport/session core. Ship logs and basic launch/reset first, then advanced debugger commands by explicit capability. |
| HDL network installation | PC game-management client, console HDLGameInstaller or HDL server | Separate HDLGameInstaller/HDLGManClient and HDL Dump wire adapters behind one job UI. APA installation is not an FTP file upload. |

“OPLServer” here means [elmariolo/OPL-Server](https://github.com/elmariolo/OPL-Server).
If another product was intended, add its exact source/build to the compatibility
matrix before claiming parity. Docmine17's OPLServer4Linux wraps this application
through Wine; it does not establish a separate network protocol.

## Source evidence and pins

These are research snapshots, not new bundled dependency pins. Preserve licenses,
file-level notices, source revisions and binary hashes for any future reuse.

| Reference | Inspected revision | Relevant source / conclusion |
| --- | --- | --- |
| [OPL-Server](https://github.com/elmariolo/OPL-Server/tree/021e4589b90ea84fe33ae95b8ff758db6848876e) | `021e4589b90ea84fe33ae95b8ff758db6848876e` | `OPL_Server/OPLServer/Form1.cs`: direct TCP SMB, configurable port, PS2 share, Guest credentials, tray/log controls. Includes SMBLibrary files with LGPL notices; do not infer one license for the whole app. |
| [OPLServer4Linux](https://github.com/Docmine17/OPLServer4Linux/tree/d2bc63ed5abd36b9c54165f68be42699baf19ae5) | `d2bc63ed5abd36b9c54165f68be42699baf19ae5` | README links the original OPL-Server and requires Wine/Zenity. Use as a UX reference, not our runtime architecture. |
| [PS2Client](https://github.com/ps2dev/ps2client/tree/4af9c6a7a05a3a428270f2d836166cd3965bcb17) | `4af9c6a7a05a3a428270f2d836166cd3965bcb17` | `src/ps2link.c`, `src/ps2netfs.c`, protocol documents. Audit redistribution permission before bundling: this snapshot does not supply an obvious top-level license grant. |
| [lwNBD, OPL pin](https://github.com/bignaux/lwNBD/tree/15f1c14536d662e2e00d38d0aa91ef24149e8b96) | `15f1c14536d662e2e00d38d0aa91ef24149e8b96` | Verified against OPL's `tools/download_lwNBD.sh`; `servers/nbd/protocol-handshake.c` rejects GO/INFO. |
| [lwNBD, current research head](https://github.com/bignaux/lwNBD/tree/f56b3bedbe2755e52db8254f95451210be5d72ce) | `f56b3bedbe2755e52db8254f95451210be5d72ce` | Same GO limitation; READ/WRITE/DISC/FLUSH handlers exist. BSD-2-Clause license in this snapshot. Reverify the actual ELF's fork/pin. |
| [ps2ftpd](https://github.com/ps2homebrew/ps2ftpd/tree/7d0726131eaa8c428a04d297f3d4829cba2d2b95) | `7d0726131eaa8c428a04d297f3d4829cba2d2b95` | README, command handlers and AFL-2.0 license. Standard FTP plus PS2-specific filesystem/site operations. |
| [HDLGameInstaller](https://github.com/ps2homebrew/HDLGameInstaller/tree/ec37c81f8edb6fc45e2be51c86a322780641275a) | `ec37c81f8edb6fc45e2be51c86a322780641275a` | `HDLGManClient/client.c`, `HDLGManClient.h`, `HDLGManClient.c`, ISO/OSD/install code. Native Windows dependencies and PC-side redistribution terms require a separate audit; a font license is not a client-code license. |
| [HDL Dump](https://github.com/ps2homebrew/hdl-dump/tree/32c296c69cf9c263fcbe035004aa28c345b3b279) | `32c296c69cf9c263fcbe035004aa28c345b3b279` | `apa.c`, `hdl.c`, `hio_udpnet*.c`, README and GPL COPYING. Assess a separate process adapter before copying storage code into the launcher. |

RadHostClient source and redistribution permission were not established during
this audit. [PS2 Klient's author describes it as a PS2Client GUI alternative](https://github.com/gdomingues/ps2-klient).
Treat that as a lead and behavior reference, not proof that every RadHostClient
version has the same wire behavior. Validate LaunchELF's writable HOST extensions
and patched rename commands separately from stock PS2Link.

## Shared implementation foundation

1. Add a transport/session/job layer beside `ServerDef`: connect, capabilities,
   bounded operation, progress, cancel, close, and sanitized structured errors.
   Long-lived listeners, interactive debug sessions and finite disk transfers
   remain distinct. Keep existing `--serve` modes compatible.
2. Use immutable connection epochs and operation IDs. Reconnect/reset invalidates
   outstanding results; a completed old upload must not update a new console's UI.
   Socket timeouts are not overall deadlines. Bound queues, handles and payloads.
3. Build a shared streaming copy engine with exact byte counts, 64-bit offsets,
   bounded memory, temporary destinations, explicit overwrite policy, verification
   and an operation journal. Separate “sent”, “server acknowledged”, “read back”
   and “durable” states. Never invent resume or atomic rename support.
4. Scope listener/firewall rules to the selected interface and peer where the
   platform supports it. Do not request inbound rules for pure client sockets.
   Preserve conflict detection and owned-child shutdown on desktop exit.
5. Reuse library ISO identification, title/serial metadata and source fingerprints,
   but audit existing code before sharing it. Share transport-independent storage
   interfaces with UDPBD where appropriate; do not reuse its wire format for NBD.
6. Record console application, exact ELF hash/build, protocol variant and enabled
   operations in a compatibility matrix. “TCP connected” is not feature acceptance.

## Workstream A: OPLServer and HOST/PS2Link parity

OPLServer audit: compare share naming, Guest/authentication behavior, configurable
port, directory enumeration, large offsets, streaming reads, VMC/config writes,
concurrent clients, path case/Unicode, tray/autostart and useful error messages.
Reuse the existing SMB conformance tests and add only demonstrated missing cases.
Do not bundle another SMB daemon just to reproduce its application name.

HOST core: support open/read/seek/close, directory handles and stat with exact
packet sizes, endian conversion, signed errors and bounded handle tables. Map
explicit roots; reject traversal, absolute drive escapes, UNC/device paths,
symlink/reparse escapes and Windows reserved filenames. Begin read-only. Later
write support needs create/truncate/write/mkdir/delete/rename fixtures, including
LaunchELF-specific extensions. Preserve save metadata explicitly; raw file copies
must not be advertised as complete memory-card save backup/restore.

PS2Link's inspected PC client connects to console TCP `0x4711` and services file
requests over that connection. It listens locally for UDP logs on `0x4712` and
sends command packets to console UDP `0x4712`. Do not open an unnecessary PC TCP
4711 listener. Keep logs available while sending another command; use TCP_NODELAY
as the reference does, then measure rather than guessing at performance.

Deliver log capture, HOST read access, `execee`/`execiop` argument encoding and
reset/disconnect first. Advanced memory/register/VU/GS operations are later named
capabilities, with exact range validation and explicit user actions. They must
never run as an automatic connectivity probe. Optional PS2NetFS file management
uses console TCP `0x4713`; it is a separate protocol, not another name for HOST.

Acceptance: differential byte fixtures against the pinned client, partial TCP
reads/writes, malformed lengths/commands, stale handles, disconnect and root
escape tests; then physical LaunchELF HOST browsing and ELF execution, plus
PS2Link logs/launch/reset on exact console builds. Reusing ps2client as a child
requires pinned reproducible native builds and verified license terms first.

## Workstream B: FTP file management

Use a PC FTP client adapter (stdlib `ftplib` is an initial candidate) behind the
job layer. Identify the console server before promising features. Implement
USER/PASS, binary mode, listing fallback, CWD/PWD, RETR and exact transfer lengths.
Support PASV first with a peer-address policy; add active PORT only where needed
and test the PC inbound data connection explicitly. Do not require MLSD, EPSV,
UTF-8 or TLS from old PS2 servers. Plain FTP credentials are LAN-only secrets.

Model PS2 device namespaces and LIST formats per server, including absent memory
cards and mounted PFS devices. Expose ps2ftpd SITE MNT/UMNT/SYNC only after a
verified capability check. Uploads, rename, delete and recursive operations are a
later increment: preflight space/overwrite, partial-file visibility, cancellable
STOR, REST semantics and server close/flush behavior need real tests. Prefer
temporary-upload-and-rename only where that server actually supports it.

Acceptance: fake-server fragmented replies, misleading PASV host, binary data,
listing variants, >4 GiB offsets when supported, resume refusal, connection loss
and ENOSPC; then LaunchELF/ps2ftpd round trips with file hashes. FTP must never
claim to install a game into APA by copying an ISO to a filesystem directory.

## Workstream C: NBD HDD access

Implement a userspace client to OPL's lwNBD export, normally TCP 10809. Detect
handshake/flags and export names rather than assuming a full modern NBD server.
The inspected OPL pin and current lwNBD reject `NBD_OPT_GO`/INFO with UNSUP:
test LIST plus EXPORT_NAME as the required compatibility path. A standards-based
GO-first attempt is optional only if fallback is exercised. Handle advertised
padding/flags exactly; do not infer support from constants in a header.

First milestone: list/connect, inspect size/capabilities and stream a read-only
image to a local temporary file, with bounded READ requests, validated reply
handles/errors, 64-bit bounds, cancellation and verification. Default export
`hdd0` is a useful preset, not proof of identity. No kernel driver or writable
mount is needed. Persist endpoint preferences, never write permission.

Later WRITE/FLUSH needs explicit per-session enablement, READ_ONLY enforcement,
request alignment, exclusive access, failure journaling and validated media
identity. A protocol FLUSH handler does not prove device durability. Require
disposable-image failure tests before a spare-drive console test. OS integration
(libnbd/nbdfuse/Windows drivers) is optional later scope, not a release dependency.

Acceptance: fake minimal lwNBD replies and a pinned upstream server, GO rejection,
short I/O, invalid handles, overflow and read-only enforcement; then an OPL HDD
read-back comparison against a known image. A PC NBD server stays out of the first
scope until a confirmed PS2 NBD client needs it.

## Workstream D: network APA game installation

Build two adapters, never an automatic protocol fallback after a failed write:

- HDLGameInstaller/HDLGManClient: PC connects to console TCP 45061 for control;
  **PC listens on TCP 45062 and the PS2 connects back for game data** in the
  inspected client. Validate the connecting peer and make this firewall path
  visible in diagnostics. Version exchange expects `0x0C` in this snapshot;
  reject mismatches before allocation or writes. Decode packet widths/packing,
  payload bounds, response errors, sector counts, offsets and close status from
  both client and console source; do not send raw platform structs from Python.
- HDL Dump/HDL server: separately identify TCP/UDP server generation and command
  semantics. The current README describes the UDP 12345 server as deprecated and
  prefers OPL NBD. Decide whether a pinned subprocess backend or an owned adapter
  is justified; do not silently translate one install protocol into the other.

Start with version/identity, game list, free space, game metadata and read/export.
Then add queued installation of owned local disc images. Validate ISO9660 startup
and disc ID, title encodings/lengths, CD/DVD mode, layer sizes, compatibility flags,
game-partition naming and source stability. Model HDDOSD icons/bootstrap resources
as optional, separately licensed assets. Do not bundle proprietary system files.

Install state machine: preflight -> reserve/create -> transfer -> close/status ->
read-back verification -> confirmed. Persist enough state to identify a newly
created incomplete entry after interruption. Remote protocols may publish an APA
entry before data is complete: **do not claim atomic transactions or rollback**.
Never automatically delete/overwrite an existing game. Resume only after remote
partition identity, source fingerprint and acknowledged offset are revalidated.
On uncertainty, stop and present explicit recovery rather than reporting success.

Support conventional APA/HDL first. HDLGameInstaller explicitly excludes APAEXT
(ToxicOS); detect/refuse it before writes. GPT/exFAT, APA-Jail, large-disk sector
translation and mixed layouts are separate capabilities requiring exact source
and spare-drive validation. An unidentified layout must remain read-only.

Block conflicting writes from this application to the same console/disk across
NBD, HDL adapters and filesystem jobs. Determine what exclusivity the console
actually guarantees against other tools; a local mutex cannot protect against
another PC. Formatting, partition repair and generic raw restore are out of the
initial installer scope.

Acceptance: mock both socket directions; version mismatch; read-only catalog;
sector/size overflow; CD/DVD/DVD9 and >4 GiB inputs; malformed metadata; source
mutation; control/data disconnect; disk-full/allocation failures; cancellation at
every stage; close failure and read-back mismatch. Use an image-backed APA test
disk, compare with an independent reader, and verify unrelated partitions byte
for byte. Physical acceptance uses a spare HDD, known fixtures and a fresh game
list/OPL boot check after installation. Throughput alone is never acceptance.

## Delivery sequence and completion gates

| Milestone | Deliverable | Dependencies / exit gate |
| --- | --- | --- |
| M0 | Source/license/capability matrix; shared jobs, conflicts and diagnostics; OPLServer gap audit | Reproducible references; existing streaming/RA tests stay green. |
| M1 | FTP browse/download | Fragmentation/cancellation fixtures; one exact PS2 server accepted. |
| M2 | HOST read service, PS2Link logs/launch/reset | Root containment and packet fixtures; physical LaunchELF and PS2Link acceptance. |
| M3 | Userspace NBD read/export | Minimal lwNBD negotiation fixtures; known-image console comparison. |
| M4 | HDLGameInstaller read-only catalog/export; HDL Dump backend decision | Reverse data connection and version fixtures; source/license decision recorded. |
| M5 | Managed APA installation with verification/recovery | M4 plus image-backed APA failure tests, exclusive-write policy and spare-drive acceptance. |
| M6 | FTP/HOST mutations and NBD writes; broader layouts | Separate per-capability write reviews, failure tests and console acceptance. |

M1/M2/M3 are separable releases once M0 lands; implement one coherent workstream
at a time. M5 is the largest/highest-integrity workstream and is not a side effect
of adding FTP or NBD. Do not assign calendar promises until M0 prototypes expose
server/version behavior and licensing constraints.

Every delivered capability needs source/contract tests, packaged executable
tests on supported desktop platforms, clear unsupported states and a recorded
physical-console result. CI, an upstream success report and a built package are
distinct evidence levels. Unimplemented operations remain visible in the issue
checklist and compatibility matrix; they must not silently become “out of scope”
when calling the broader roadmap complete.
