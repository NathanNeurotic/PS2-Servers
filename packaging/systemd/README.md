# systemd deployment

Two units here, and which one you want depends on whether you need SMB.

| unit | build | serves |
|---|---|---|
| `ps2servers@.service` | the normal Linux download | **SMBv1**, UDPFS, UDPBD |
| `ps2servers-edge@.service` | the Go Edge build | **SMBv1**, UDPFS, UDPBD |
| `ps2servers-edge.service` | the Go Edge build | UDPFS only (predates the others) |

**Edge now serves SMB too**, as of the `smb` subcommand. Use
`ps2servers-edge@smb` on a router or other small box where a Python runtime is
not practical, and `ps2servers@smbv1` on a normal machine. The older
`ps2servers-edge.service` runs udpfs only and is left alone for anyone already
using it.

```sh
sudo install -m 0644 packaging/systemd/ps2servers-edge@.service /etc/systemd/system/
sudo install -m 0644 packaging/systemd/ps2servers-edge-smb.env /etc/default/ps2servers-edge-smb
sudo systemctl enable --now ps2servers-edge@smb
```

The instance name is the Edge subcommand, so `ps2servers-edge@udpfs`,
`ps2servers-edge@udpbd`, and `ps2servers-edge@webui` work the same way, each
reading its own `/etc/default/ps2servers-edge-<subcommand>`. Several can run
at once. Enable the web GUI with `systemctl enable --now ps2servers-edge@webui`.

Edge's SMB defaults to **port 1111**, not 445, for the same reason as the
Python one: below 1024 needs root and these units run as `ps2edge`.

## Any server, on a normal Linux box (`ps2servers@.service`)

No desktop session required. `--serve` runs one server in the foreground and is
dispatched before anything imports tkinter, so it needs no X, no display and no
logged-in user.

**1. Install the binary and create the service user.**

```sh
# Grab the Linux build from the releases page (PS2Servers-linux-x64).
sudo install -m 0755 PS2Servers-linux-x64 /usr/local/bin/ps2servers

sudo useradd --system --home-dir /nonexistent --shell /usr/sbin/nologin ps2servers
sudo mkdir -p /srv/ps2
sudo chown root:ps2servers /srv/ps2
sudo chmod 0775 /srv/ps2          # group-writable: OPL saves settings here
```

**2. Install the unit and the env file for the server you want.**

```sh
sudo install -m 0644 packaging/systemd/ps2servers@.service /etc/systemd/system/
sudo install -m 0644 packaging/systemd/ps2servers-smbv1.env /etc/default/ps2servers-smbv1
```

The instance name is the server key. `ps2servers@udpfs` and `ps2servers@udpbd`
work the same way, each with its own file — install
`ps2servers-udpfs.env` or `ps2servers-udpbd.env` instead. The env file is
**required**: every server here refuses to start without arguments, so a
missing one fails immediately rather than looping. Run `ps2servers --list` to
see the keys available on your machine.

**3. Edit the env file before enabling anything.**

```sh
sudo nano /etc/default/ps2servers-smbv1
```

Set the share path and the port. The port defaults to **1111**, not 445:
binding 445 needs root, and on a machine already running Samba it is taken. Set
OPL's *SMB Port* to match.

**4. Open the port if a firewall is running.** The GUI prints this hint when a
server starts; running headless there is nobody to print it to. Ubuntu ships
`ufw` inactive by default, so check before assuming:

```sh
sudo ufw status                      # skip the rest if inactive
sudo ufw allow 1111/tcp              # smbv1; udpfs and udpbd are udp
```

`sudo firewall-cmd --add-port=1111/tcp --permanent && sudo firewall-cmd --reload`
on firewalld distributions. A blocked port looks exactly like a server that is
not running: the console simply never connects.

**5. Start it.**

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now ps2servers@smbv1
systemctl status ps2servers@smbv1
journalctl -u ps2servers@smbv1 -f   # blocks; Ctrl-C to stop watching
```

### Serving a share outside `/srv/ps2`

**Change the unit as well as the env file.** `ProtectSystem=strict` makes the
whole filesystem read-only apart from `ReadWritePaths=`, which ships as
`/srv/ps2`. Point `--share` at `/mnt/games` and leave the unit alone and you
get a share the console can read and never write — the server starts, the game
list loads, and only saving fails, which looks like a network fault rather than
a permissions one.

```sh
sudo systemctl edit ps2servers@smbv1
```

```ini
[Service]
ReadWritePaths=/mnt/games
```

The share is writable on purpose — OPL stores its settings on it and a
VMC-on-SMB lives there. Add `--read-only` only if you want saves to fail.

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
