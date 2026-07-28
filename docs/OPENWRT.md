# OpenWrt deployment

`packaging/openwrt/` is an OpenWrt package-source layout. Build it through the
OpenWrt SDK for the exact target and release used by the router. Do not rename a
generic Go binary to `.ipk`; OpenWrt package architecture identifiers come from
the SDK.

## Configuration

The package installs `/etc/config/ps2servers-edge`:

```text
config udpfs 'main'
    option enabled '1'
    option root '/mnt/sda1/PS2'
    option bind '0.0.0.0'
    option port '62966'
    option data_port '0'
    option protocol 'auto'
    option single_port '0'
    option read_only '1'
    option peer_timeout '1h'
    option tx_delay_ms '0'
    option log_format 'text'
    option verbose '0'
    option block_device ''
    option sector_size '512'
    option no_compression '0'
    option compression_cache_size '32'
    option metrics '0'
    option metrics_period '1m'
```

Every option maps to one Edge flag:

| Option | Flag | Notes |
|---|---|---|
| `enabled` | — | `0` stops the service starting at boot |
| `root` | `--root` | game directory; must be mounted before the service starts |
| `bind` | `--bind` | `0.0.0.0` listens on every interface |
| `port` | `--port` | discovery port, UDP 62966 (`0xF5F6`) |
| `data_port` | `--data-port` | `0` picks automatically; set it for strict firewalls |
| `protocol` | `--protocol-mode` | `auto`, `standard`, or `modulo` |
| `single_port` | `--single-port` | carry all traffic on the discovery port |
| `read_only` | `--read-only` | **`1` is read-only. Set `0` to let the console save.** |
| `peer_timeout` | `--peer-timeout` | idle session timeout, `1m`–`24h` |
| `tx_delay_ms` | `--tx-delay-ms` | inter-packet pacing; raise it if a slow router drops packets |
| `log_format` | `--log-format` | `text` or `json` |
| `verbose` | `--verbose` | protocol-level logging |
| `block_device` | `--block-device` | disk image served over UDPFS block access; empty is off |
| `sector_size` | `--sector-size` | multiple of 512, at most 65536; only applied with `block_device` |
| `no_compression` | `--no-compression` | serve CSO/ZSO as raw bytes; a diagnostic, see below |
| `compression_cache_size` | `--compression-cache-size` | decompressed blocks kept per image, `0`–`4096` |
| `metrics` | `--metrics` | log periodic transfer counters |
| `metrics_period` | `--metrics-period` | how often, `1s`–`24h`; only applied with `metrics` |

The `procd` service runs as the dedicated `ps2edge` user, restarts on failure,
and passes the UCI values to the first-party Edge CLI. If the game mount is not
yet available, Edge exits cleanly and `procd` retries it after the mount appears.

### Writes are opt-in on this platform

`read_only` ships as `1`, so a fresh install serves read-only even though the
Edge binary itself defaults to writable. A router share is frequently an entire
attached disk and the service is unauthenticated on the LAN, so the platform
default is the cautious one.

**Set `option read_only '0'` to let a console write its saves.** That is the
only switch; there is no separate write option, and it governs the block device
as well as the file share.

### Serving a disk image

`block_device` serves one image over UDPFS block access (`BREAD`/`BWRITE`) from
the same instance already serving files, so a console can reach a game folder
and a virtual drive without a second service:

```sh
uci set ps2servers-edge.main.block_device='/mnt/sda1/ps2.img'
uci set ps2servers-edge.main.read_only='0'
uci commit ps2servers-edge
service ps2servers-edge restart
```

The image must be readable — and, for saves, writable — by the `ps2edge` user
the service runs as, not by root. An image owned by `root:root` with mode `0644`
produces a share that mounts and then fails every write.

This is distinct from the `udpbd` subcommand, which speaks a different protocol
on its own port. The package's init script starts `udpfs` only.

### Compression and metrics

`compression_cache_size` bounds how many decompressed blocks Edge keeps per open
image. It exists because a console reading sequentially touches the same block
repeatedly, and re-inflating it each time is wasted CPU on exactly this class of
hardware. The default of 32 is a few hundred kilobytes; raise it on a router
with RAM to spare, and set `0` to disable caching.

`no_compression` serves CSO/ZSO containers as the raw bytes on disk and stops
them standing in for a missing `.iso`. It is a **diagnostic**, not a way to save
CPU: a PS2 cannot interpret container bytes, so a share configured this way will
not load compressed games.

`metrics` is off by default. The counters go to stdout, `procd` forwards stdout
to syslog, and a router logging a snapshot every minute indefinitely is a cost
worth opting into rather than inheriting.

### A bad value restarts the service in a loop

Edge validates these options at startup and exits with status 2 when one is out
of range. The service is configured `respawn 10 5 0`, where the trailing `0`
means unlimited retries, so a typo in a numeric option — `sector_size '500'`,
`compression_cache_size '99999'` — produces a service that restarts every five
seconds rather than one that fails visibly once. If the service will not stay
up, read the reason before re-checking the hardware:

```sh
logread -e ps2servers-edge
```

## SDK build outline

1. Install the matching OpenWrt SDK and its Go package infrastructure.
2. Place or link `packaging/openwrt` under `package/ps2servers-edge`.
3. Ensure the PS2-Servers source revision referenced by the package is available.
4. Run `make package/ps2servers-edge/{clean,compile} V=s`.
5. Install the resulting `.ipk` whose architecture is assigned by that SDK.

The checked-in package is a source package. Release automation should build
`.ipk` files inside named SDK matrices and publish the OpenWrt target/subtarget
next to each artifact. This pull request does not publish guessed `.ipk` files.

## Storage and flash limits

On small routers, keep game images and optionally the Edge binary on attached
USB storage. The init script must start only after that mount is available. Use
a read-only game mount where practical.

## Firewall

The default discovery port is UDP 62966 (`0xF5F6`). Normal two-port mode also
uses an automatic data port; routers with strict firewall rules should configure
`data_port` explicitly or use `single_port 1`. Restrict exposure to the trusted
LAN zone.
