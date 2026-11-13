# NFS server

The **NFS** card shares a games folder over NFS version 4.0 for the PS2 NFS
clients built on [unfs4](https://github.com/sahlberg/unfs4) by Ronnie Sahlberg:

- RiptOPL's **NFS** protocol: its game list, the OPL core's in-game reader and
  Ember PS1 games (RiptOPL pull requests 392 and 400; not in RiptOPLite),
- Neutrino's NFS backing store (`-bsd=nfs`, `-dvd=nfs:DVD/game.iso`;
  ps2max32/neutrino pull request 240), and
- Launchstation's `nfs:` file-manager driver (Launchstation pull request 366).

All three were merged on 2026-11-12. RiptOPL and Launchstation have them in
their rolling builds. No Neutrino release or development build has its NFS mode
yet: upstream's latest development build is still v1.8.0-61-g7be8de2 of 2026-08-31 (checked 2026-11-13), so
RiptOPL runs an NFS game set to Neutrino on its OPL core instead. POPSTARTER
(`.VCD`) games cannot launch over NFS in RiptOPL; use SMB for them. This mode is
new and has not been tested on a real PS2.

NFS adds nothing over SMB or UDPFS for a loader that already speaks those. It is
here for clients that speak NFS.

## Setting it up

1. Pick the **Games folder**. It is shared as export `/`, so `DVD/` and `CD/`
   inside it are `/DVD` and `/CD` to the console.
2. Leave **Allow writes** off unless you need it (see below).
3. Click **Start**. The card shows the PC's IP.

On the PS2 side there are only two things to enter: the server IP and the
export, `/`. The port is always **TCP 2049**; the PS2 clients cannot use another,
so the card has no port field. There is no user name or password.

For Neutrino by hand, that is `srv=<PC IP>` and `export=/` in the module
arguments of `config/bsd-nfs.toml` (its default export, `/srv/ps2`, is a Linux
path and does not exist on this server). RiptOPL writes these into that file
itself.

In RiptOPL, set **Network Connectivity** to Manual or Auto and **Protocol** to
**NFS** under Settings → Game Sources, then enter the PC's IP and **NFS Export**
`/` on the Network page. In Launchstation, add a share in Settings > Network >
SMB shares with **Server type** NFS and the URL `nfs://<PC IP>:2049/`.

Core, from a terminal:

```text
ps2servers serve nfs --root-dir /games
ps2servers serve nfs --root-dir /games --allow-writes
```

### Port 2049 is taken

On Linux the kernel's own NFS server (`nfsd`) normally holds TCP 2049. The card
says so when it cannot bind; stop that service (`sudo systemctl stop
nfs-server`) or serve from another PC. Windows desktop editions have no NFS
server of their own, so 2049 is normally free there. Windows Firewall setup adds
a rule for TCP 2049.

## Allow writes

Off by default. With it off, consoles can list folders and read files, and
everything else is refused: nothing in the folder can be created, changed,
renamed or deleted, whatever a client sends.

With it on, a client can also create files and folders, write, change a file's
size, rename and delete. Each write is on the PC's disk before the console is
told it is done, so a PC crash cannot lose a write the console thinks finished;
that makes large copies to the PC slower than a copy that is only cached.

**NFS has no password.** With writes on, any device that can reach this PC on
TCP 2049 has the same rights as the PS2. Turn it on only on a network you trust,
and set **Bind address** (Advanced) to the PC's address on the PS2's network.

Neutrino's NFS mode is read-only, so its games save to a memory card. RiptOPL
writes to the export (VMC saves, game settings, Rename and Delete) only while its
own **NFS Writes** row is On; with it Off, RiptOPL only reads, and its games save
to the real memory card. Launchstation writes when you copy, rename or delete in
its file manager. Each of these writes also needs Allow writes on here.

## What is shared

The same rules as the SMB2/SMB3 server, from one shared implementation
(`smb2_server/smb2_paths.py`):

- Nothing outside the games folder. `..` is refused, and a symbolic link that
  points outside the folder is neither listed nor opened, nor can it be deleted
  through NFS.
- Names Windows cannot use are not served, on every PC, so a share means the
  same thing whichever OS serves it: `CON`, `NUL`, `COM1` and the other device
  names, names ending in a dot or space, alternate data streams (`file:stream`),
  and the characters `<>:"|?*`.
- Only files and folders. Devices, sockets and pipes in the folder are skipped.

A filehandle from an earlier run of the server is refused as stale rather than
pointing at another file, so a console needs a fresh mount after the server
restarts.

## Connections

A connection is never closed for being quiet. Neutrino connects once each time
it loads its NFS modules and cannot reconnect, so a long cutscene must not end
the game. The IOP reboots a game does leave old connections behind half-open;
past 32 connections the quietest one from the address holding the most is
closed (the newcomer's own on a tie). One machine cannot hold every slot, and a
console's single connection goes only when that console connects again. When
every address holds one, a newcomer is turned away.

A file stays open between reads only while reads keep coming. After a second of
quiet it is closed, so a file the console was reading can be renamed or deleted
on the PC (Windows refuses that while a file is open).

## Limits

- NFS version 4.0 only (RFC 7530). Versions 4.1 and 4.2 are refused, and there
  is no version 3, MOUNT protocol or portmapper. `mount -t nfs` from a Linux
  or macOS PC is not a supported client.
- Credentials are accepted as given (AUTH_SYS or none). There is no Kerberos.
- A read returns at most 1 MiB, and a request stops at the first operation
  after 1 MiB of reply.
- No locks, delegations or exclusive create. None of the PS2 clients uses them.
- The only attributes served are type, size and modification time, which is
  all unfs4 asks for.

## For maintainers: what unfs4 needs from a server

unfs4 is small because it reads replies at fixed places. A reply that is valid
NFS but laid out differently is read wrong, and one longer than asked for can
overrun a buffer on the IOP. `nfs4_server.py` keeps to these, and the tests
below check them:

- One RPC fragment per reply, verifier AUTH_NONE, the request's tag echoed (the
  PS2 sends an empty one), so the COMPOUND status sits at byte 28 counting the
  record mark and the first result at byte 40.
- PUTFH, PUTROOTFH, LOOKUP, SAVEFH and SETCLIENTID_CONFIRM results are just the
  operation and its status, 8 bytes each.
- Attributes are exactly TYPE, SIZE and TIME_MODIFY, in that order, with the
  bitmap `[0x12, 0x00200000]`.
- READ returns at most `count` bytes, and Neutrino asks for at most 2048 so the
  whole reply fits its 2112-byte reassembly buffer. READDIR's reply, from the
  verifier to eof, never exceeds `maxcount` (unfs4 sends 1032 and copies the
  entries into a 1024-byte buffer).
- OPEN never asks for OPEN_CONFIRM and never hands out a delegation.
- WRITE is answered FILE_SYNC: unfs4 never sends COMMIT.

## Testing

```text
python -m unittest tests.test_nfs4_server -v
python conformance/integration/neutrino_nfs/run.py
```

The second downloads unfs4 at a pinned commit, checks it by SHA256, and builds
it twice with the host C compiler and AddressSanitizer: once as Neutrino builds
it (512-byte buffer), reading a game byte-exact, the tail of a 5 GiB file, and a
missing file; and once with every feature, running unfs4's own test program
with writes on (it must pass and leave the folder as it found it) and with
writes off (every change must fail and the folder must be untouched). A check
spliced into the client fails the run if any reply is longer than the client
asked for, or longer than Neutrino's reassembly buffer.

RiptOPL's own `tests/nfs-live` runs its in-game NFS reader against this server
(v0.9.3) in RiptOPL's CI, with writes allowed and refused.

What that proves: the real client works against this server on a PC, inside the
PS2 builds' buffer limits. What it does not prove: anything about the PS2's own
TCP stack, its timing, or a real console.
