# Design proposal: NBD and PS2Client/PS2Link tabs

**Not implemented.** These are proposed features, not current server modes or
setup instructions. See [the README](../README.md) for the shipped modes.
The remaining text records the design discussion dated below; it is not a
release commitment.

Handoff for the next agent. Written 2026-09-05, straight after HTTP mode landed
(#188). Read the whole "Direction" section before designing anything — it
changes what both of these tabs are.

## Direction: both of these invert the project

Every tab we ship today is a **server the PS2 connects to**. SMBv1/2/3, UDPFS,
UDPBD and HTTP all listen; the console dials in. Both new features are the other
way round, and this was confirmed from source, not assumed:

| Feature | Who listens | Who dials |
|---|---|---|
| SMB / UDPFS / UDPBD / HTTP | **PC** (us) | PS2 |
| **NBD** | **PS2** (OPL runs lwNBD, exporting its internal HDD) | PC |
| **ps2netfs** | **PS2** (TCP `0x4713`) | PC |
| **ps2link** | *both* — see below | both |

Evidence for NBD: OPL's tree carries `download_lwNBD.sh` (pinning
[bignaux/lwNBD](https://github.com/bignaux/lwNBD) at `15f1c145`) and
`labs/lwnbdsvr/`, and
[discussion #491](https://github.com/ps2homebrew/Open-PS2-Loader/discussions/491)
describes users running `nbd-client`/`nbdfuse` on the PC against the PS2. OPL
"currently only supports exporting the PS2's drive."

**So "treat NBD like the others" does not apply.** A PC-side NBD *server* would
have no consumer — OPL ships the server. The thing with real value is a PC-side
NBD **client**, and specifically a Windows-native one, because today Windows
users need WSL or Ceph-for-Windows to reach their own PS2 drive.

**DECIDED (Ripto, 2026-09-05): the target is a PC-side NBD client speaking to
OPL's lwNBD export.** Not a server, and not a role toggle — see "Shouldn't NBD
go both ways?" below for why, and for what to do so the server half stays cheap
to add if that ever changes.

---

## Part 1 — NBD tab

### What it should do

Connect to the lwNBD server OPL runs on the console and give the user access to
the PS2's internal drive from Windows without WSL.

### Protocol (verified against lwNBD's `include/lwnbd/nbd-protocol.h`)

- Port **10809** (NBD standard).
- **Fixed newstyle handshake**: server sends `NBDMAGIC`
  (`0x4e42444d41474943`) then `IHAVEOPT` (`0x49484156454F5054`), and advertises
  `NBD_FLAG_FIXED_NEWSTYLE` (bit 0).
- Client selects an export with `NBD_OPT_GO` (7) — modern — or the older
  `NBD_OPT_EXPORT_NAME` (1). lwNBD's header says "Modern clients use NBD_OPT_GO
  instead of this", so implement `NBD_OPT_GO` and keep `EXPORT_NAME` as fallback.
- Transmission: request magic `0x25609513`, simple reply magic `0x67446698`.
  Structured replies (`0x668e33ef`) exist in the header — check whether lwNBD
  actually negotiates them before assuming simple-only.
- Use `NBD_OPT_LIST` to discover export names, but we already know the default:
  **`hdd0`**. From `loadLwnbdSvr()` in OPL's `src/opl.c` — the user can set one
  via `CONFIG_NET_NBD_DEFAULT_EXPORT` (`gExportName`, max 32 chars), and when
  it is blank OPL falls back to `strcpy(config.defaultexport, "hdd0")`. Prefill
  the field with `hdd0` and let `NBD_OPT_LIST` correct it.

### What OPL actually does when it serves (verified in `src/opl.c`)

Three facts from `loadLwnbdSvr()` that shape the client:

- **It exports the internal ATA drive.** OPL loads `ps2atad_irx` and then
  `lwnbdsvr_irx`. That is the console's own HDD, not a mounted image.
- **The export is read-only unless the user enabled writes ON THE CONSOLE.**
  `config.readonly = !gEnableWrite;`, where `gEnableWrite` comes from
  `CONFIG_OPL_ENABLE_WRITE` and **defaults to 0** (`src/opl.c:1744`). This is a
  gate we get for free and must not paper over: if the server advertises
  read-only, say plainly that OPL's "Enable write" is off, rather than reporting
  a generic write failure.
- **The console is fully occupied while serving.** OPL calls `audioEnd()`,
  `ioBlockOps(1)`, `deinitAllSupport(...)` and `unloadPads()` before starting
  the server. The PS2 is not usable for anything else, and nothing else on it is
  listening — so do not expect to talk to any other PS2 Servers mode at the same
  time, and expect the user to be sitting at a "NBD Server running..." screen.

### "Shouldn't NBD go both ways?" — two questions, one answer each

Asked by Ripto 2026-09-05, and worth recording because the answer is not
obvious.

**Read vs write is bidirectional, and that IS a toggle.** One NBD connection
carries both. But keep two separate things separate, because they are easy to
collapse into one and this document did so in an earlier draft:

- `NBD_FLAG_READ_ONLY` is the **server's capability** — whether the export will
  accept writes at all. We read it; we cannot change it. For OPL it is driven by
  `gEnableWrite` on the console.
- The **Read-only checkbox is our client's policy** — whether we intend to send
  writes. We own it; it defaults to on.

A write needs both to line up, and the two failure modes read differently to a
user: capability says "the console will not accept writes, turn on Enable write
in OPL", policy says "you have not enabled writing here". Never derive one from
the other, and never report one using the other's message.

**Server vs client is not a toggle.** It is which program you run, fixed for the
life of a connection. NBD as a standard is perfectly happy either way — on Linux
you run `nbd-server` or `nbd-client` as you please — but in the PS2 world it is
currently one-directional:

- lwNBD is a *server* framework. `include/lwnbd/lwnbd-server.h`,
  `examples/lwnbd-server.c`, and plugins that **export** hardware (`atad` for
  the console's ATA drive, `bdm`). Its README frames contribution as adding
  "new Protocols (Servers) or Drivers (Plugins)". There is no client in it.
- OPL's menu item is "Start NBD server", and it "currently only supports
  exporting the PS2's drive".

So a PC-side NBD server would have nothing to talk to today. A PS2-side NBD
client would give the console block-device access over NBD/TCP — a distinct
protocol, but overlapping the use case UDPBD and UDPFS already cover over a UDP
transport tuned for the console. That overlap, not any limitation of NBD, is
likely why nobody has written one.

**What to do about it:** do not build the server half, but do not design it out
either. Put the wire protocol — handshake, option haggling, request/reply
framing — in its own module with the role as a thin layer on top, the way
`udpfs_server/compressed_iso/` sits under both UDPFS and HTTP. Client and server
share nearly all of that code, so if a PS2 NBD client ever appears, the server
half is a small addition rather than a rewrite. Note the trigger in the docs so
the next person knows what would justify building it.

### The Windows problem, and the recommended answer

Windows cannot present a block device without a kernel driver, so "mount the PS2
drive as a disk" is off the table for a Python launcher. Three options:

1. **Raw image read/write** — stream the export to a `.img` file (and optionally
   write one back). No driver, works everywhere, and it is what most people
   actually want: a backup, or a drive image to feed to existing tools.
   **Recommended first.**
2. **Browse and extract** — parse the PS2 APA partition table and PFS to list
   partitions and pull individual files out. Much more useful, much more work.
   Build (1) so this can sit on top of it.
3. Re-export locally so a Windows NBD client can attach — still needs a driver on
   the user's side. Skip.

### Card shape — an open UI question

The existing `ServerCard` is built around Start/Stop on a long-running listener.
An NBD client run is a **transfer with a beginning, an end, and progress**. Do
not force it into the server card without asking; a progress bar and a
byte/percentage readout are the honest UI. Raise this with Ripto.

Suggested fields: PS2 IP (required), Port (10809), Export (a dropdown filled
from `NBD_OPT_LIST`), Destination file, Read-only (default **on** — this is
someone's console drive).

### Writing to the console's drive: two gates, not one

Reading needs no ceremony — dumping an export to a `.img` cannot hurt anything.
**Writing is the one case that can destroy a console's drive**, and NBD writes
go straight to the physical disk with no filesystem or undo between them. A
mistyped export name or the wrong console on the network is an unrecoverable
loss of somebody's saves and installed games. It gets **two separate warnings**,
deliberately at different moments and answering different questions.

**Gate 1 — when write is enabled.** The moment the user unticks Read-only, an
`askyesno` in the style of the direct-link prompt (`launcher/gui.py:1388`),
itemising what write mode means:

> Allow writing to the PS2's drive?
>
> NBD writes go directly to the console's physical disk. There is no
> filesystem, no recycle bin and no undo.
>
> • existing partitions, saves and installed games can be overwritten
> • a wrong export or a wrong console cannot be taken back
> • PS2 Servers cannot verify the drive is the one you meant
>
> Leave this off unless you are restoring a backup you made yourself.

Declining must snap the tick back to Read-only, not leave it ambiguous.

**Gate 2 — when the write actually starts.** A second confirmation naming *this
specific operation*, so the user is agreeing to a target rather than to a
concept:

> Write to `<export>` on `<console IP>`?
>
> Source: `<path to image>` (`<size>`)
> This replaces the contents of that drive. It cannot be undone.
>
> Type the export name to confirm:  [_______]

The second gate must **not** be a plain OK button. After Gate 1 the user is
already in "yes" mode, and a default-focused OK is dismissed by muscle memory —
which would make the second warning decorative. Requiring the export name typed
back is what makes it a real second check, and it also catches the "two consoles
on the bench, wrong one selected" case that Gate 1 cannot.

Three supporting rules, without which the gates are theatre:

1. **Never persist write-enable.** The launcher saves card values between runs
   (`_save()` / `self.saved`). If this one survives a restart, a later Start
   could reach a real write with neither gate ever appearing. It must reset to
   Read-only on every launch — and resetting it after each completed run is
   better still.
2. **Name the mode in the terminal at start**, loudly, so the log makes plain
   which mode ran when someone asks what happened to their drive.
3. **Confirm the export is writable before promising anything.** NBD's
   transmission flags carry `NBD_FLAG_READ_ONLY`; if the server exports
   read-only, say so up front instead of failing partway through a write.

A verify-after-write pass (read the export back and compare) is the honest
fourth thing, but do not let it hold up the read path — mention it to Ripto and
scope it separately.

---

## Part 2 — PS2Client / PS2Link tab

Reference: [`doc/ps2link-protocol.txt`](https://github.com/ps2dev/ps2client/blob/master/doc/ps2link-protocol.txt)
and [`doc/ps2netfs-protocol.txt`](https://github.com/ps2dev/ps2client/blob/master/doc/ps2netfs-protocol.txt).
Both were read in full for this plan.

### ps2link uses three sockets, and we are on both ends

| Socket | Direction | What it carries |
|---|---|---|
| UDP `0x4712` | PC → PS2 | commands (reset, run an ELF, power off) |
| TCP `0x4711` | PS2 → PC, we answer | file I/O requests for the `host:` device |
| UDP `0x4712` | PS2 → PC | log/stdout text from the running program |

> The protocol doc lists the command port and the log port as the same number
> (`0x4712`). That is unusual enough to verify against `src/ps2link.c` in
> ps2client before building on it.

**Commands to send** (`0xBABE02xx`): `reset` `0201`, `execiop` `0202`,
`execee` `0203`, `poweroff` `0204`, `dumpmem` `0207`, `startvu` `0208`,
`stopvu` `0209`, `dumpreg` `020A`, `gsexec` `020B`. All are single UDP packets
shaped `{int number; short length; ...}` in network byte order.

**Requests to serve** (`0xBABE01xx`): `open` `0111`, `close` `0121`,
`read` `0131`, `write` `0141`, plus lseek and the directory calls. This is a
fileio server rooted at a folder the user picks — the PS2 runs an ELF, the ELF
opens `host:foo.dat`, and we answer. Read data follows its response packet as a
separate packet; write data follows its request.

### ps2netfs is a separate protocol, and we are purely the client

TCP `0x4713` on the **PS2**. One request, one response, connection closed —
`{int number; short length; ...}` with `0xBEEF80xx` opcodes: open `8011`,
close `8021`, read `8031`, write `8041`, lseek `8051`, delete `8071`,
mkdir `8081`, rmdir `8091`, dopen `80A1`, dclose `80B1`, dread `80C1`,
sync `8131`, mount `8141`, umount `8151`, devlist `8F21`. `ioctl` `8061`,
`format` `80F1` and `rename` `8111` are marked UNDOCUMENTED upstream.

This is what makes file management on the console's own drives possible
(`mc0:`, `hdd0:`, `mass:`), and it pairs naturally with the same tab.

### The interactive terminal is new UI work

Ripto asked for this tab to have its own interactive terminal. **Nothing in the
launcher can do that today:**

- `LauncherApp._append_log()` (`launcher/gui.py:2460`) writes into a `Text`
  widget that is held `state="disabled"` — read-only by construction.
- `ServerProcess` (`launcher/process.py`) captures merged stdout/stderr and
  **never opens stdin**.

So this needs an entry row beneath the terminal, command history, and a
dispatcher. **Dispatch in-process; do not pipe stdin to a child.** The commands
are UDP protocol packets, not text a subprocess would parse, and routing them
through a child's stdin would mean inventing a second text protocol for no gain.
The tab should own its sockets directly.

Sketch of the command line: `reset`, `run <path.elf> [args]`, `runiop <path.irx>`,
`poweroff`, `reset`, plus ps2netfs verbs (`ls <device:path>`, `get`, `put`,
`rm`, `mkdir`, `devices`). Print protocol errors with the opcode that failed.

### Safety

This tab **executes code on the console** and **serves a folder to it**. The
`host:` root must be contained the same way UDPFS does it — reuse the
realpath-plus-prefix approach in `udpfs_server/udpfs_server.py:_resolve_path`,
including the UNC/drive-root fix from PR #73. Do not write a fresh path check.

---

## Shared work both tabs need

**`ServerDef` assumes server semantics.** `default_port`, Start/Stop, and
`opl_hint`'s "In OPL → …" phrasing all read wrong for a client. Add a `role`
field (`"server" | "client" | "interactive"`) to `ServerDef` and branch on that
in `launcher/gui.py`, rather than adding more `if key == "..."` special cases —
there are already two such spots (`TAB_TITLES` ~line 205, `opl_hint` ~line 257)
and a third and fourth will not age well.

**A test will fail that you did not break.** `tests/test_linux_parity.py`'s
`EveryServerDeclaresItsPortsTests` requires every registered Python server to
declare at least one inbound firewall port. That guard was added deliberately
(HTTP mode shipped without one and was unreachable). A pure client like NBD has
no inbound port and will fail it. **Make the guard role-aware — do not delete
it.** Note that ps2link is *not* exempt: its TCP `0x4711` and UDP `0x4712` are
genuinely inbound and must be declared.

**The usual registry touchpoints**, all of which have tests that will tell you
if you miss one:

- `launcher/servers.py` — `ServerDef` + `REGISTRY`
- `launcher/gui.py` — `TAB_TITLES`, `opl_hint`, `ABOUT_TEXT`
  (`test_about_page` requires every registered mode be described)
- `ps2servers.py` — the headless `serve` error text lists valid keys
- `launcher/serve.py` and `build/build.py` — stdlib import hints; `DATA_FILES`
  derives from the registry automatically
- `.github/workflows/ci.yml` — add the new package to `compileall`

## Phases

1. Confirm the ps2link command/log port collision against `src/ps2link.c`.
   (The NBD direction is settled: client. See "Direction" above.)
2. `role` on `ServerDef` + role-aware firewall guard. Small, unblocks both.
3. ps2link: command sender + log listener + `host:` fileio server, contained.
4. The interactive terminal UI, wired to the ps2link dispatcher.
5. ps2netfs client verbs in the same tab.
6. NBD client: handshake, `NBD_OPT_LIST`, raw image read. **Ship this and stop.**
7. NBD write-back, as its own phase with its own review — both gates, the
   no-persist rule, and the `NBD_FLAG_READ_ONLY` check land together or not at
   all. Do not fold this into phase 6 to save a round trip.

## Verification

- Unit-test both protocols by replaying recorded packet bytes, the way
  `tests/test_http_server.py` and `tests/test_directlink.py` do — both are pure
  request/response and testable without a wire.
- A loopback ps2link fake (a script that sends the `0xBABE01xx` requests a real
  console would) proves the fileio server without hardware.
- For NBD, `nbdkit`/`nbd-server` on the PC is a real server to test the client
  against — do that before ever pointing it at a console's drive.
- Direct-link parity: `tests/test_http_direct_link.py` is the pattern to copy.
- **Neither of these can be called working without hardware.** ps2link executes
  code on the console and NBD writes to a real drive; both deserve more caution
  than HTTP did, not less.

## Risks

- **NBD write support can destroy a console's drive.** This is the
  highest-stakes thing the project would ship — writes land on the physical disk
  with no filesystem and no undo. Read-only by default, **two separate
  confirmations** before any write reaches the wire, and write-enable never
  persisted across launches. See "Writing to the console's drive: two gates, not
  one" above; that section is a requirement, not a suggestion. Note OPL gates
  this too — it exports read-only unless the user set `CONFIG_OPL_ENABLE_WRITE`
  on the console — so a write reaching the drive takes THREE deliberate acts.
  Do not treat OPL's gate as a substitute for ours; treat it as the reason a
  failed write is usually "the console says no" rather than a bug. If the
  schedule gets tight, ship the read path alone — a dump-only NBD client is genuinely
  useful, and a half-guarded write path is worse than none.
- ps2client's protocol docs are from 2004 and mark several opcodes
  UNDOCUMENTED; treat `src/ps2netfs.c` and `src/ps2link.c` as the real spec, the
  way the HTTP work treated `src/ethsupport.c` over the fork's README.
- lwNBD's default export is `hdd0`, and OPL exports the internal ATA drive —
  both read out of `loadLwnbdSvr()`. Still call `NBD_OPT_LIST` rather than
  assuming the user kept the default.
- ps2link has no authentication at all. Anyone on the network can run code on the
  console. Say so in the docs.
