# Router status protocol

A launcher, a loader, or a script can ask a PS2-Servers box "are you ready?"
and get a useful answer, instead of discovering the answer by trying a transfer
and waiting for it to fail.

This exists for hardware like [PS2-EtherDrive](https://github.com/FatBaldDad/PS2-EtherDrive),
where the server is a router mounted inside the console. Such a board may be
power-cycled with the console, or reset independently, and it takes tens of
seconds to boot. Without a status channel the user changes a game and stares at
a loader that cannot distinguish "still booting" from "broken", so they wait
too long, or reset something that did not need resetting.

The second thing it answers is the one that matters for hardware: **is a
transfer in flight right now.** A board wired to console power should not be
cut mid-write. `busy` is how a launcher knows to say "don't".

## The compatibility rule this is built on

Every PS2-Servers release, and udpfsd upstream, drops a discovery packet whose
service ID is not UDPFS, without replying:

- Edge: `internal/udpfs/negotiation.go` — `if err != nil || dh.ServiceID != protocol.ServiceUDPFS { return }`
- Desktop: `udpfs_server/ps2servers_core.py` — `if hdr.packet_type != PacketType.DISCOVERY or disc.service_id != UDPRDMA_SVC_UDPFS: return`

So a status query is a discovery-shaped packet carrying a **different service
ID**, sent to the **discovery port that is already open**. Consequences, all of
them deliberate:

- A server that predates this spec ignores the query. The client times out and
  reports `unknown`, which is exactly the behaviour every client has today.
  Nothing regresses.
- No new port, so no new firewall rule and no second UAC prompt on Windows.
- No existing packet changes by one byte. In particular the DISCOVERY/INFORM
  exchange the PS2 itself uses is untouched, which is not negotiable: INFORM is
  six bytes with no spare field, and its exact shape is load-bearing for real
  clients.
- Only new clients ever send the query, so the reply format is unconstrained by
  history.

## Service ID and magic

    UDPRDMA_SVC_STATUS = 0xF5F7
    STATUS_MAGIC       = "PS2S"

The service ID is adjacent to `UDPRDMA_SVC_UDPFS = 0xF5F5` and the default port
`0xF5F6`. That is upstream's numbering range, allocated consecutively, so
`0xF5F7` is the next value upstream itself would reach for.

**The number alone is therefore not the identifier — the number plus the magic
is.** Validating a reply protects a client from a collision, but it does
nothing about the other direction: a server keyed on the number alone would
answer *any* discovery packet carrying `0xF5F7`, including a future udpfsd
client asking about something else entirely. Requiring the magic means a server
implementing this spec can only ever answer a question it actually understands.

Both sides are required to check it. A server MUST ignore a query without it; a
client MUST discard a reply without it.

## Query — client to server

Ten bytes. The first six are structurally identical to a DISCOVERY, which is
what makes older servers discard it on the path they already have — a longer
packet with an unrecognised service ID hits the same guard and is dropped just
the same.

| offset | size | field | value |
|---|---|---|---|
| 0 | 2 | header | `type = 0` (DISCOVERY), `sequence = 0` |
| 2 | 2 | service ID | `0xF5F7` |
| 4 | 2 | port | `0` — unused; the reply goes to the query's UDP source |
| 6 | 4 | magic | `PS2S` |

The header packs as the existing UDPRDMA header: `type & 0xF | (sequence & 0xFFF) << 4`,
little-endian, as does every multi-byte field below.

Send it to the server's discovery port (default `0xF5F6` / 62966). It may be
unicast or broadcast. A server answers each query exactly once and keeps no
state for it — a status query never creates a session, never disturbs one, and
never resets a live transfer.

## Reply — server to client

| offset | size | field |
|---|---|---|
| 0 | 2 | header: `type = 1` (INFORM), `sequence = 0` |
| 2 | 2 | service ID, echoed: `0xF5F7` |
| 4 | 4 | magic, echoed: `PS2S` |
| 8 | 1 | payload version — `1` |
| 9 | 1 | state |
| 10 | 2 | service flags |
| 12 | 2 | active sessions |
| 14 | 4 | uptime, seconds |
| 18 | 1 | name length, bytes |
| 19 | … | name, UTF-8, not NUL-terminated |

Fixed part is 19 bytes. A reply shorter than that, or whose service ID or magic
does not match, must be discarded.

**Version.** A client that does not recognise the version must treat the server
as `unknown` rather than guess. Everything after offset 8 may change in a future
version; **offsets 0–8 may not** — header, service ID, magic and version are
exactly what a client needs in order to decide whether the rest is addressed to
it and whether it understands the layout.

### State — offset 9

| value | name | meaning |
|---|---|---|
| 0 | `starting` | process is up, not yet able to serve |
| 1 | `ready` | serving, no transfer in flight — safe to power-cycle |
| 2 | `busy` | at least one transfer in flight — **do not cut power** |
| 3 | `degraded` | serving, but something is wrong (share unreadable, for example) |
| 4 | `stopping` | shutting down |

Unknown values must be treated as `degraded`, not as `ready`: a client that has
not been taught a new state should be cautious rather than confident.

### Service flags — offset 10, bitfield

| bit | meaning |
|---|---|
| 0 | UDPFS is being served |
| 1 | UDPBD is being served |
| 2 | SMB is being served |
| 3 | read-only |
| 4 | compressed images (CSO/ZSO) will be decompressed |

Unassigned bits are zero and must be ignored, so bits can be added without a
version bump.

### Active sessions — offset 12

Peers with live state. `busy` is the authoritative "in flight" signal; this is
for display.

### Uptime — offset 14

Seconds since the server began serving. Lets a launcher distinguish "it just
came up" from "it has been up for an hour and is simply idle", which is the
difference between waiting and investigating.

### Name — offset 18

The server's name and version, for display — e.g. `PS2 Servers Edge 0.4.9`.
Capped at 255 bytes by the length field; a client should not assume any
particular content.

## Client guidance

Poll rather than listen. A server does not broadcast state changes, because
broadcast is filtered on enough networks to make it an unreliable primary
signal. Once per second while a user is waiting is ample and costs ten bytes.

Timeout is `unknown`, never `down` — the server may simply be older than this
spec. Distinguish the two by whether ordinary UDPFS DISCOVERY gets an INFORM: a
box that answers DISCOVERY but not the status query is a working server that
predates this feature.

## Adoption

Anything may implement this; that is the point of writing it down. It is
deliberately small enough to add to a loader in an afternoon, and the
compatibility rule means adding it cannot break a client that does not.
