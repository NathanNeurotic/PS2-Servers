# UDPBD server — provenance

`udpbd_server.py` is a **pure-Python port** of the UDPBD v2 server, written from
the published wire protocol so PlayStation 2 clients (Open PS2 Loader) talk to
it unchanged. It is the active server used by the launcher and runs on Windows,
Linux and macOS.

## Credit

The UDPBD protocol and the original server are by **Rick Gaiser**.
Upstream: https://github.com/israpps/udpbd-server (brought to GitHub with CI by
El_isra; Windows port by Alex Parrado). UDPBD has largely been superseded by
UDPFS (also Rick Gaiser) — see `../udpfs_server/`.

This port was written from the protocol definitions (`udpbd.h`) and documented
behaviour (`main.cpp`); **no upstream source code was copied**. Upstream ships
without a license file, so this independent reimplementation avoids redistributing
their code.

## Files

- `udpbd_server.py` — the server (standard library only). CLI: `udpbd_server.py <image>`.
- `selftest.py` — protocol self-test (no PS2 needed): `python selftest.py`.

The launcher uses **only** the pure-Python server above. A legacy Windows
`udpbd-server.exe` (Alex Parrado's build) was previously vendored here but was
never used by the launcher and has been removed to keep the repository free of
unsigned third-party binaries. If you specifically want that native build, it
remains available upstream at https://github.com/israpps/udpbd-server (note it is
device-oriented and does not serve plain image files well on Windows — the reason
for the Python port).

## Verify

```sh
cd udpbd_server
python selftest.py
```
