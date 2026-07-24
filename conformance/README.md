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
