# OpenWrt package source

This is an OpenWrt **package source layout**, not a generic binary renamed to
`.ipk`. Place `packaging/openwrt` under an SDK feed, set
`PS2SERVERS_REPO_DIR` to the PS2-Servers repository root,
select the package with `make menuconfig`, and build it with the SDK.

The SDK supplies the real OpenWrt package architecture identifier. The Go build
uses the target toolchain selected by OpenWrt, including endianness and soft-float
settings where applicable; this file deliberately does not guess an `.ipk`
architecture from a generic Go target name.

The `procd` service:

- runs as unprivileged `ps2edge`;
- restarts after failure or before a removable mount becomes available;
- reads `/etc/config/ps2servers-edge`;
- logs through the normal OpenWrt service logger;
- serves read-only.

The UCI file is the stable configuration contract: section and option names
are additive-only, so tooling that generates it can rely on them across
updates. `ps2servers-edge --dump-schema` prints the whole surface (sections,
options, types, defaults, ranges, and which layer rejects a bad value) as
JSON — validate generated configs against the binary being shipped rather
than a hardcoded copy of the option list.

For low-flash routers, install the package metadata/init files normally and place
the executable or game data on attached USB storage. Update the service path with
an init-script override if the executable itself lives on USB.
