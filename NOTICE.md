# Notices and Third-Party Licensing

This file summarizes licensing and attribution for PS2 Servers. It is not legal
advice; it is a practical notice file for users, contributors, and release
reviewers.

## Project license

Unless otherwise stated, PS2 Servers is licensed under the **Academic Free
License 3.0 (AFL-3.0)**. See [`LICENSE`](LICENSE).

AFL-3.0 was selected because this repository redistributes upstream UDPFS code
from Rick Gaiser's Neutrino project, which is licensed under AFL-3.0.

## Original PS2 Servers code

The following parts are original to this repository unless otherwise noted:

- the Tkinter GUI launcher and tray/Windows setup glue;
- the RiptOPL SMBv1/CIFS server implementation in `smbv1_server/`;
- the pure-Python UDPBD server in `udpbd_server/udpbd_server.py`;
- release/build workflow glue and project documentation.

The RiptOPL SMBv1 server was authored from public protocol documentation and
Open PS2 Loader protocol/header behavior. No third-party SMB server source code
was copied into that implementation.

## Bundled upstream code

### UDPFS server from Neutrino

`udpfs_server/udpfs_server.py` is redistributed from Rick Gaiser's Neutrino host
tools:

- upstream project: https://github.com/rickgaiser/neutrino
- upstream file: `pc/udpfs_server.py`
- upstream license: Academic Free License 3.0

The upstream copyright and license terms remain with their respective owners.
This repository keeps that code under AFL-3.0 and documents it here.

## Protocol implementations and references

### UDPBD

`udpbd_server/udpbd_server.py` is a pure-Python implementation of the UDPBD v2
protocol. The protocol and original server are by Rick Gaiser. The GitHub-hosted
reference repository is maintained by El_isra:

- reference repository: https://github.com/israpps/udpbd-server
- note: the reference repository did not contain a license file when reviewed;
  this repository does not redistribute its source code in the Python UDPBD
  implementation.

A legacy `udpbd-server.exe` binary may exist in historical/source trees as a
fallback artifact. The launcher uses the Python UDPBD implementation, not that
legacy binary.

### pyudpbd

The `prodeveloper0/pyudpbd` project was consulted during development of the
Python UDPBD implementation:

- repository: https://github.com/prodeveloper0/pyudpbd
- license: WTFPL v2

No pyudpbd source code is intentionally copied into this repository.

### Open PS2 Loader

PS2 Servers interoperates with Open PS2 Loader and its protocol expectations:

- repository: https://github.com/ps2homebrew/Open-PS2-Loader

References to OPL, Open PS2 Loader, SMB, UDPFS, UDPBD, and related device names
are compatibility/descriptive references. This project is not affiliated with or
endorsed by the OPL maintainers.

## Optional runtime libraries

PS2 Servers itself is primarily standard-library Python. Some optional compressed
image paths rely on libraries that users may install separately:

- `lz4` for ZSO/LZ4 decompression;
- `libchdr` for CHD decompression.

`libchdr` is licensed under BSD 3-Clause and has its own third-party dependency
licenses. PS2 Servers loads libchdr dynamically when present; it does not vendor
libchdr source code in this repository.

## Build-only tooling

Release builds use build-only tools pinned in `requirements-build.txt`, such as
Nuitka, ordered-set, and zstandard. These tools are used to create packaged
artifacts and retain their own upstream licenses. They are not authored by this
project.

## Assets and names

PlayStation, PlayStation 2, PS2, and related marks are trademarks of their
respective owners. PS2 Servers is an independent homebrew utility and is not
affiliated with, sponsored by, or endorsed by Sony Interactive Entertainment.

User-supplied project artwork and PS2-themed UI assets are project assets unless
otherwise noted in the file, pull request, or release notes.

## Corrections

If any attribution, license classification, or upstream link is wrong or
incomplete, open an issue or pull request and it will be corrected.