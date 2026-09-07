# UDPFS protocol compatibility

## Automatic — recommended

Automatic mode is the default in PS2 Servers Core and Edge. Compatibility is
negotiated independently for each peer, so standards-compatible clients and
Modulo clients can use one server at the same time.

The server first sends the canonical INFORM from its data socket. If the peer
does not continue promptly, the server sends a compatibility INFORM from the
discovery socket. The first DATA sequence is compared with the recorded
DISCOVERY sequence:

```text
standard: DISCOVERY 0, DATA 0
Modulo:   DATA = (DISCOVERY + 1) modulo 4096
```

Therefore `DISCOVERY 4095` followed by `DATA 0` remains identifiable as Modulo.
The socket carrying the first DATA is recorded, but socket choice alone does
not classify the protocol profile.

Every session stores its own profile, response socket, receive sequence,
transmit sequence, fallback state, and file handles. Discovery traffic from one
peer cannot reset another peer, and a background sequence-zero discovery does
not reset a recently active transfer.

## Standards only — diagnostics

`--protocol-mode standard` disables Modulo sequence interpretation for the
session. DATA is still accepted through either local socket so this option does
not conflate protocol diagnosis with single-port or two-port topology.

## Modulo only — legacy diagnostics

`--protocol-mode modulo` forces the compatibility sequence behavior. It exists
for controlled diagnosis and migration, not as the normal user setting.

The historical `--modulo-mode` flag remains a deprecated alias for
`--protocol-mode modulo`. The Desktop card has an Auto/Standard/Modulo selector
and an **Enforce Modulo mode** checkbox; the checkbox overrides the selector.
Leave it unticked for Auto. Core routes forced Modulo through its legacy
single-port engine. Edge forces the sequence profile but keeps topology
controlled separately by `--single-port`.

## Single-port topology

In Auto and Standard, `--single-port` uses the discovery socket for all traffic.
Automatic negotiation supports either topology. Forced Modulo in Core already
uses a single socket; Edge keeps the profile and topology choices independent.

## Address binding and launch failures

For Desktop/Core, use **Bind address (PC)** to select the PC address on the PS2
network, or click **Use LAN IP**, then stop/start UDPFS. The top LAN IP selector
alone changes setup hints. Core CLI `--bind IP` pins the data socket; discovery
stays wildcard. `--bind IP:port` can also pin its data port, while an explicit
`--data-port` takes precedence. In single-port Core operation the separate data
bind is unused.

Edge `--bind IP` binds both discovery and data sockets and accepts no port suffix.
Binding one local address can affect broadcast reception on some hosts; verify
initial discovery as well as game launch. See the [simple Desktop steps](https://github.com/NathanNeurotic/PS2-Servers/blob/main/README.md#udpfs-games-list-but-fail-to-launch).

A DISCOVERY source address is the client endpoint. A new `seq=0` or a different
client IP is evidence to correlate with launch, not proof of a broken server
handshake. A read at EOF returning zero bytes is also not by itself a failed
ISO read.

## Fallback timing

The initial fallback is 250 ms. It is intentionally configurable in Core for
protocol diagnostics. This is an integration-tested starting value, not a
hardware-certified timing claim; physical-console testing should validate it
across direct links, switched LANs, Wi-Fi bridges, and slower routers.
