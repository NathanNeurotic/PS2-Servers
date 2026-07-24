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

The historical `--modulo-mode` flag remains as a deprecated alias for
`--protocol-mode modulo`. It prints a warning rather than silently changing
meaning. New launcher sessions no longer display a global Modulo checkbox;
Automatic mode requires no user intervention.

## Single-port topology

`--single-port` is independent of protocol mode. It uses the discovery socket
for all traffic. Automatic negotiation works with either single-port or normal
two-port operation.

## Fallback timing

The initial fallback is 250 ms. It is intentionally configurable in Core for
protocol diagnostics. This is an integration-tested starting value, not a
hardware-certified timing claim; physical-console testing should validate it
across direct links, switched LANs, Wi-Fi bridges, and slower routers.
