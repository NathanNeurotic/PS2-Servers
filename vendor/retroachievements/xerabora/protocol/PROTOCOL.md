# Wire protocol

The protocol between a console-side agent and the PC client. It carries
two things: a request/response exchange that identifies the running game
and hands the console a watch list, and a one-way stream of memory
snapshots the client feeds to rcheevos.

Everything runs over UDP. Text requests are ASCII; snapshot values are
raw bytes. The default port is 18194.

The design splits cleanly in two. The client and rcheevos are
console-agnostic: the client identifies a game from its hash (the RA
server resolves which console and title it is), derives the watch list
from the achievement set, reassembles snapshots, and unlocks
achievements. Everything console-specific lives in the agent: it reads
that console's memory, computes the RA image hash for that console, and
streams snapshots. Porting to another console means writing a new agent
that speaks this protocol, not changing the client.

`ra_snap.h` and `ra_watch.h` in this directory define the binary
structures. Both sides keep a matching copy; this document is the
contract.

## Addressing and NAT

Each request from the console includes the console's own IP and port.
The client replies to that address, not to the datagram's source. This
matters when the client runs behind NAT (a container), where the source
address is rewritten and a reply sent back to it would not reach the
console.

## Padding

Replies are padded to a multiple of 64 bytes and to at least 128 bytes.
A receiver that assembles datagrams through a DMA path (as the reference
PS2 agent does) can lose short or unaligned tails; padding keeps every
reply on the reliable path. One watch-list chunk is 896 bytes, which
with header and padding stays under a 960-byte receive ceiling.

## Discovery

    console -> PC   RAP1 <console-ip> <port>
    PC -> console   RAO1 OK <client>/<version>

The console broadcasts `RAP1`; a listening client answers with its name
and version. Used to confirm the link.

## Identify a game

    console -> PC   RAQ1 <hash> <serial> <console-ip> <port>
    PC -> console   RAA1 OK <bytes> <chunks> [<achievements> <title>]
                    RAA1 WAIT
                    RAA1 NO

`hash` is the RA image hash the agent computed. The client asks the RA
server about it:

- `RAA1 OK <bytes> <chunks>` — the game is known and its watch list is
  ready, `bytes` long, split into `chunks`. Newer clients append the
  achievement count and title as extra fields; older agents ignore them.
- `RAA1 WAIT` — the client is still asking the server. The console
  retries.
- `RAA1 NO` — the server does not know the image.

## Fetch the watch list

    console -> PC   RAG1 <hash> <index> <console-ip> <port>
    PC -> console   RAC1 <index> <length> <raw bytes>

The console requests each chunk by index until it has all `chunks`. Each
`RAC1` carries `length` raw bytes of the watch-list file after the text
header.

The watch-list file is a small header (`struct ra_watch_file`) followed
by `count` 32-bit entries. Each entry packs an address and a read size:

    address = entry & 0x0FFFFFFF        (low 28 bits, up to 256 MB)
    size    = entry >> 28               (high 4 bits)

The client builds this list from the game's achievement set, so the
agent never needs to know which addresses matter.

After the entries the file may carry a tail of pointer-chain nodes: a
`struct ra_node_file` header with the magic `RANL` and a count, then
that many `struct ra_node`. Each node names a parent, a static offset
and a read size. The parent is either an entry or an earlier node, and
nodes come in dependency order, so one pass resolves a chain of any
depth. The version in the file header stays 1: an agent that stops
after `count` entries never sees the tail and works as before.

An agent that reads the tail resolves every chain in the same frame it
takes the snapshot, and appends an (address, value) pair per node after
the direct values. An address of 0 means the chain led outside memory
that frame. The `bytes` field in the file header counts the direct
values only; the snapshot header carries the full length, which is how
the client tells which kind of agent it is talking to.

## Telemetry

    console -> PC   RA15 <text header> <raw values>

While the game runs, the agent sends snapshots, several UDP packets per
snapshot. Each packet has a fixed-width text header (the field layout is
in the agent) and a block of raw values. The values follow watch-list
order, so addresses are not sent again.

One snapshot is described by `struct ra_snap`: a 48-byte header (magic,
sequence number, frame counters, entry count, byte count, and the game
serial) followed by the packed values and a trailer word.

Tear protection: the agent may build the snapshot in a shared buffer
that is copied out non-atomically. The sequence number `seq` is repeated
in a trailer word placed after the values. A reader takes `seq` from the
header, copies the values, then checks the trailer against the header; a
mismatch means the snapshot changed mid-copy and is dropped.

## Unlock notice

    PC -> console   RAU1 <achievement-id> <points>

Sent when rcheevos unlocks an achievement, to the address the console
gave in `RAP1`. It is the one message the client sends unprompted, so
the console must keep listening on that port after discovery. What the
console does with it is its own business -- the reference PS2 agent
shows a brief notice over the game. It may arrive more than once for the
same unlock; the console should show one notice per message it acts on
and may ignore repeats within a second. A console that does not
implement notices ignores it.

    console -> PC   RAK1 <seq> <detail...>

Optional acknowledgement, sent back to the datagram's source. `seq`
counts the notices the console has acted on; what follows is agent
specific and meant for a log (the PS2 agent reports the address of its
EE-side buffer and the SIF DMA result). The client logs the line.

    console -> PC   RAH1 <datagrams> <notices> <chunks> <mask> <done>

Optional heartbeat, every ten seconds, with what the agent's receive
side has seen since the game started. The client logs it; no heartbeat
means the agent's send path is down, zero datagrams while the client is
sending means nothing reaches the agent. The last three fields belonged
to the badge experiment below and are sent as zeros.

## Reset

    PC -> console   RAR1 <reserved>

Asks the agent to leave the game and return to its own menu, the way
the loader's in-game reset does. Sent to the same address as `RAU1`,
and answered with the same `RAK1`. The argument is not read; it is
there because the reference agent takes every message as a tag, a
space and a value. An agent that cannot reset ignores it. Where the
console lands afterwards is the loader's setting, not the protocol's.

`RAB1` and `RAK2` (a badge picture pushed to the console) were an
experiment; the reference agent no longer takes them, and the client
sends them only when started with `--badge`.

## What an agent must do

1. Answer `RAP1` with `RAO1`.
2. On request, compute the game's RA image hash and its serial, send
   `RAQ1`, and handle `OK` / `WAIT` / `NO`.
3. Fetch the watch list with `RAG1` and keep it.
4. Every frame, read the listed addresses and stream them as `RA15`
   snapshots in watch-list order.
5. Optionally, keep the discovery port open and act on `RAU1` and
   `RAR1`.

The hash must match what RetroAchievements expects for that console. The
memory addresses and the hashing scheme are the only console-specific
parts; everything above the wire is generic.
