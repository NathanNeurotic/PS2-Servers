# UDPFS conformance

This directory owns externally observable expectations shared by the Python Core
server and PS2 Servers Edge. Fixtures contain no game data.

`fixtures/handshake_cases.json` is consumed by both language test suites. The
integration probe starts one or more UDPFS clients and verifies packet sequence,
selected response endpoint, concurrent OPEN/READ operations, ACK handling, and exact returned bytes.

Validation labels used by this project are deliberately separate:

- **unit tested** — parser/session/filesystem behavior in-process
- **integration tested** — real UDP sockets and returned bytes on the host
- **build tested** — a target compiled or inspected successfully
- **emulator tested** — verified with a named emulator
- **hardware tested** — verified on a physical PS2 with supplied results

No hardware claim is implied by CI.

## Three-server differential probe

`integration/udpfs_parity.py` starts `pcm720/udpfsd`, Python Core, and Edge on
one non-loopback IPv4 interface and runs identical standard-protocol fixtures:

- concurrent clients sharing an IP but using different source ports;
- root-relative paths, directory operations, reads, writes, and block access;
- collision-free compressed aliases, direct raw containers, and legacy aliases;
- empty-path block metadata and block-device-only startup;
- inherited FILEIDs across DATA-first restart and background discovery;
- NACK, duplicate-request, FIN-loss, and delayed-ACK behavior.

Run it against locally built binaries:

```sh
python conformance/integration/udpfs_parity.py \
  --reference-binary /path/to/udpfsd \
  --edge-binary /path/to/ps2servers-edge
```

The reference is built from `ee94414f9f45ed33b8dd4ca4e03847440df946e9` in CI.
The harness documents intentional differences instead of copying upstream bugs:
its virtual OPEN size is zero, virtual-alias GETSTAT is unsupported, legacy stem
aliases are Core/Edge extensions, and its short delayed-ACK budget differs from
Core/Edge's longer tolerance. DREAD, decoded bytes, raw-container semantics, and
shared success paths are still checked exactly. CHD decoding is covered by the
separate capability-tagged Edge suite because this reference build is CGO-free.

Multi-hour lifecycle, physical Raspberry Pi/OpenWrt runs, and physical PS2
listing/Neutrino launch/restart/relaunch/VMC scenarios remain separate acceptance
runs with exact commits and topology, not claims made by this host probe.
