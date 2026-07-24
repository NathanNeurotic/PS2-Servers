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
    option peer_timeout '1h'
    option log_format 'text'
    option verbose '0'
```

Note: there is no `read_only` UCI option — Edge is unconditionally read-only
(the init script always passes `--read-only`), so a `read_only '0'` line would
silently do nothing.

The `procd` service runs as the dedicated `ps2edge` user, restarts on failure,
and passes the UCI values to the first-party Edge CLI. If the game mount is not
yet available, Edge exits cleanly and `procd` retries it after the mount appears.

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
