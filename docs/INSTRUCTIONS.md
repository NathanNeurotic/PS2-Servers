# Edge operator manual

Edge is a separate Go executable with three game-serving subcommands and an
optional web management dashboard. It has no Desktop Tkinter window. For the
Desktop/Core launcher, start with the [project README](https://github.com/NathanNeurotic/PS2-Servers/blob/main/README.md).

## Start a server

```sh
ps2servers-edge udpfs --root /srv/ps2
ps2servers-edge smb --share games=/srv/ps2 --port 1111
ps2servers-edge udpbd --image /srv/images/ps2.img --read-only
```

Run one command per process. UDPFS needs a directory; UDPBD needs a disk image.
SMB serves guest SMBv1, not SMB2/3. UDPFS/SMB/UDPBD allow writes by default in
the bare binary; the OpenWrt and Docker packages ship read-only. A writable
server only supports saves when the PS2 client implements that write path.

Use the [build picker](EDGE-WHICH-BUILD.md) for the host's OS and architecture.
For Windows PowerShell, invoke a binary in the current directory as
`.\ps2servers-edge.exe`; quote paths containing spaces.

## Options for the installed version

```sh
ps2servers-edge --version
ps2servers-edge udpfs --help
ps2servers-edge smb --help
ps2servers-edge udpbd --help
ps2servers-edge webui --help
ps2servers-edge --dump-schema
```

`--help` prints the current flags and defaults (Edge may return exit status 2
after printing help). The schema describes the UCI/JSON configuration contract.
See [Edge configuration](EDGE.md) for explanations and examples.

## 1. Subcommand: `udpfs`

```sh
ps2servers-edge udpfs --root /srv/ps2
```

Serve a directory tree, optionally with a block image. Key distinctions:

- UDPFS discovery uses UDP 62966 and a second data port by default. Set
  `--data-port 62967` when a firewall needs a fixed pair. `--single-port` shares
  discovery's port; it is independent of `--protocol-mode auto|standard|modulo`.
- Edge `--bind` takes an IP address and binds **both** UDPFS sockets. Core binds
  only the data socket. Do not copy Core's `IP:port` bind syntax into Edge.
- UDPFS `--peer-timeout` defaults to `1h` and accepts `1m`–`24h`.
- CSO/ZSO are decoded by default. `--no-compression` exposes raw containers;
  it is a diagnostic, not a CPU-saving way to boot compressed games. No CHD in
  generic Edge builds.

## 2. Subcommand: `smb`

```sh
ps2servers-edge smb --share games=/srv/ps2 --port 1111
```

Serve guest SMBv1 shares. `--root /srv/ps2` is shorthand for the `games` share;
`--share NAME=PATH` can be repeated. Use `--read-only` to prevent writes and
set the loader's SMB port to the displayed TCP port.

For SMB and UDPBD, `--status-port 0` disables status replies; the binary default
`-1` selects UDP 62966. Only one process can own that status/discovery port.

## 3. Subcommand: `udpbd`

```sh
ps2servers-edge udpbd --image /srv/images/ps2.img --read-only
```

Serve one disk image over UDP 48573. This is the UDPBD block protocol, separate
from UDPFS file serving. `--bind` selects the local address and `--port` changes
the listening port; the client must support the selected port. The bare binary
allows writes unless `--read-only` is supplied.

## 4. Subcommand: `webui`

```sh
# Local browser only:
ps2servers-edge webui
# Other devices on a trusted LAN; choose your own password:
ps2servers-edge webui --bind 0.0.0.0 --auth-pass 'replace-with-your-password'
```

The default address is `http://127.0.0.1:8082`. LAN access requires a password;
the dashboard uses plain HTTP. `--insecure` generates and logs a password rather
than removing authentication. Browse is confined to configured game directories
unless `--browse-anywhere` is selected; `--no-browse` removes it.

Service restart requires an installed OpenWrt/procd or systemd setup and adequate
permissions. On other hosts the dashboard reports manual restart instructions;
it does not automatically supervise your terminal-launched processes. Logs show
a service-log snapshot where supported, followed by the dashboard's live output.

Use the supplied [systemd deployment](https://github.com/NathanNeurotic/PS2-Servers/blob/main/packaging/systemd/README.md),
[OpenWrt setup](https://github.com/NathanNeurotic/PS2-Servers/blob/main/docs/OPENWRT.md), or [Docker instructions](https://github.com/NathanNeurotic/PS2-Servers/blob/main/packaging/docker/README.md).
OpenWrt ships the dashboard disabled and its write/restart privileges opt-in.

## Troubleshooting

Check the startup error, mounted game directory, OS permissions, and actual
listening address first. For an optional detailed UDPFS trace, add `--verbose
--metrics --metrics-period 1s`, then disable them after testing. The
[handoff guide](https://github.com/NathanNeurotic/PS2-Servers/blob/main/docs/UDPFS-HANDOFF-DIAGNOSTICS.md) explains what each log establishes.
Host tests and successful cross-compilation do not establish PS2 gameplay.
