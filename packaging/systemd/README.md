# systemd deployment

Two units here, and which one you want depends on whether you need SMB.

| unit | build | serves |
|---|---|---|
| `ps2servers@.service` | the normal Linux download | **SMBv1**, UDPFS, UDPBD |
| `ps2servers-edge.service` | the Go Edge build | UDPFS, UDPBD only |

**Edge has no SMB.** If a console needs SMB — OPL's network game list, or
POPSTARTER — Edge cannot serve it and `ps2servers@smbv1` is the unit you want.
Edge is for routers and other small boxes where a Python runtime is not
practical.

## Any server, on a normal Linux box (`ps2servers@.service`)

No desktop session required. `--serve` runs one server in the foreground and is
dispatched before anything imports tkinter, so it needs no X, no display and no
logged-in user.

```sh
# Grab the Linux build from the releases page (PS2Servers-linux-x64).
sudo install -m 0755 PS2Servers-linux-x64 /usr/local/bin/ps2servers

sudo useradd --system --home-dir /nonexistent --shell /usr/sbin/nologin ps2servers
sudo mkdir -p /srv/ps2
sudo chown root:ps2servers /srv/ps2
sudo chmod 0775 /srv/ps2          # group-writable: OPL saves settings here

sudo install -m 0644 packaging/systemd/ps2servers@.service /etc/systemd/system/
sudo install -m 0644 packaging/systemd/ps2servers-smbv1.env /etc/default/ps2servers-smbv1

sudo systemctl daemon-reload
sudo systemctl enable --now ps2servers@smbv1
journalctl -u ps2servers@smbv1 -f
```

The instance name is the server key, so `ps2servers@udpfs` and
`ps2servers@udpbd` work the same way — each reads its own
`/etc/default/ps2servers-<key>`. Run `ps2servers --list` to see the keys
available on your machine.

Edit `/etc/default/ps2servers-smbv1` to set the share path and port before
enabling. The port defaults to **1111**, not 445: binding 445 needs root, and
on a machine already running Samba it is taken. Set OPL's *SMB Port* to match.

The share is deliberately writable — OPL stores its settings on it, and a
VMC-on-SMB lives there — which is why the unit sets `ReadWritePaths=/srv/ps2`
under `ProtectSystem=strict`. Add `--read-only` only if you want saves to fail.

If the share is on removable storage, add the mount unit to
`After=`/`RequiresMountsFor=` with `systemctl edit ps2servers@smbv1`.

## Edge, on a router or other small box (`ps2servers-edge.service`)

```sh
sudo useradd --system --home-dir /nonexistent --shell /usr/sbin/nologin ps2edge
sudo install -m 0755 ps2servers-edge /usr/local/bin/ps2servers-edge
sudo install -m 0644 packaging/systemd/ps2servers-edge.service /etc/systemd/system/
sudo install -m 0644 packaging/systemd/ps2servers-edge.env /etc/default/ps2servers-edge
sudo mkdir -p /srv/ps2
sudo chown root:ps2edge /srv/ps2
sudo chmod 0750 /srv/ps2
sudo systemctl daemon-reload
sudo systemctl enable --now ps2servers-edge
```

Set `PS2EDGE_ROOT` to the mounted game directory before enabling the unit. The
unit intentionally exposes the root read-only and runs without administrator
privileges. If the directory is on removable storage, add the corresponding
mount unit to `After=`/`RequiresMountsFor=` or override `ConditionPathIsDirectory`.
