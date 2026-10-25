# Alpine/OpenRC: Python Core UDPFS

This example runs a source installation at `/opt/ps2-servers` using
`ps2servers.py serve udpfs`, the same automatic compatibility engine as Desktop.
It does not need Tk, X, or a logged-in desktop. The standalone
`udpfs_server/udpfs_server.py` entry point remains available for deliberate use.
Go Edge is a separate executable and is not configured by this example.

Install the source and its required Python dependencies first. Verify
`python3 /opt/ps2-servers/ps2servers.py serve udpfs --help` succeeds. Optional
LZ4/libchdr dependencies are needed only for the corresponding compressed formats.

## Install and configure

Run these installation commands as root on the Alpine host. If the service
account already exists, reuse it instead of running `addgroup`/`adduser` again.

```sh
addgroup -S ps2servers
adduser -S -D -H -s /sbin/nologin -G ps2servers ps2servers
install -m 0755 packaging/openrc/ps2servers-udpfs /etc/init.d/ps2servers-udpfs
install -m 0644 packaging/openrc/ps2servers-udpfs.confd /etc/conf.d/ps2servers-udpfs
```

Back up an existing init/conf file before replacing it. Edit the conf file for
your mounted game root, data port, timeout and optional bind address. The source
tree must be readable/traversable by the service account. The game root and its
parents must be readable/traversable too; grant writes only where settings or
saves require them. The service does not change game-directory ownership or
permissions. Do not start a second UDPFS service on the same discovery port.

Discovery listens on UDP 62966 on all interfaces; `--bind` selects the data
interface. This example fixes data to UDP 62967, above the privileged-port range.
Permit both UDP ports on the intended LAN path. A source deployment using a port
below 1024, such as 455, needs an explicit OS privilege arrangement; do not make
the whole Python service root just to preserve that arbitrary port.

`use`/`after localmount netmount` orders startup after configured mount services
when present. It does not prove that a particular remote or removable share is
mounted. Add the deployment's mount service to `need` when required, and verify
the game filesystem is mounted before starting. Merely having an empty mount
directory is insufficient.

OpenRC loads `/etc/conf.d/ps2servers-udpfs`; `ps2servers_args` is trusted shell
configuration, not untrusted input. For spaces, preserve inner shell quoting:

```sh
ps2servers_args="--root-dir '/mnt/PS2 Games' --data-port 62967 --protocol-mode auto --peer-timeout 3600"
```

## Start and inspect

```sh
rc-service ps2servers-udpfs start
rc-service ps2servers-udpfs status
tail -n 40 /var/log/ps2servers/udpfs.log
tail -n 40 /var/log/ps2servers/udpfs-error.log
# Enable at boot only after the foreground configuration works:
rc-update add ps2servers-udpfs default
```

The banner records build identity, actual socket endpoints, protocol mode,
effective idle timeout, write mode and verbose state. Check these after every
configuration change. `supervise-daemon` runs the foreground process and limits
rapid restart attempts; a startup/import/bind error belongs in the error log.
The files append across starts: preserve both logs and note the relevant banner.
Arrange log rotation on the host; this example does not install a rotation job.

After edits, use `rc-service ps2servers-udpfs restart` and start a fresh console
session. Service restart loses existing protocol/file state. For troubleshooting,
append `--verbose`, reproduce once, preserve both logs, then disable verbose.

For overnight pauses, choose `--peer-timeout 86400` explicitly. The default is
3600 seconds; supported values are 60 through 86400. Zero does not disable
expiry. Longer retention keeps abandoned sessions/resources longer. Expiry
closes open files and cannot repair a console that stopped making requests.

See [Core troubleshooting](../../docs/UDPFS-CORE-TROUBLESHOOTING.md) for a
controlled capture and evidence boundaries. This template follows the upstream
[OpenRC supervisor guide](https://github.com/OpenRC/openrc/blob/master/supervise-daemon-guide.md)
and [service script reference](https://github.com/OpenRC/openrc/blob/master/man/openrc-run.8).
Validated with Python 3.12.15 and OpenRC 0.62.6 in a disposable Alpine 3.22
container: start/status/stop, an unprivileged Python process, a root containing
spaces, and the configured data port/24-hour timeout in the banner. The container
does not validate boot-time mount/network ordering on a deployed Alpine host or
physical-console gameplay.
