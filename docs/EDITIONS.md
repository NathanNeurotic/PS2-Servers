# PS2 Servers editions

PS2 Servers is one product family with three deployment shapes.

## PS2 Servers Desktop

Use Desktop on Windows, macOS, or a normal Linux workstation. It includes the
GUI launcher, SMBv1, UDPFS, UDPBD, compression support, logging, diagnostics,
Windows Firewall assistance, and the established release packaging. Desktop is
the most feature-complete edition and retains optional CHD support where the
required library is available.

## PS2 Servers Core

Core runs the Desktop server implementations without opening the GUI. It is
useful for terminals, services, containers, remote systems, and automation while
retaining the Python implementation's full feature set.

```text
ps2servers serve udpfs --root-dir /games --read-only
ps2servers serve udpbd /images/ps2.img --read-only
ps2servers serve smbv1 --share games=/games --read-only
```

The internal `--serve` spelling remains supported for existing packaged
re-execution and scripts.

## PS2 Servers Edge

Use Edge on OpenWrt, Raspberry Pi, NAS, embedded Linux, old x86 systems, ARM or
MIPS boards, and minimal headless Linux installations. Edge is a small,
statically linked Go executable and does not require Python or the desktop GUI.

Current Edge scope:

- read-only UDPFS
- automatic per-session standard/Modulo compatibility
- multiple concurrent clients
- ISO, CSO, and ZSO
- text or JSON logs
- generic Linux, systemd, Docker, and OpenWrt deployment foundations

Current Edge exclusions are explicit: no CHD in the generic static binaries and
no native UDPBD claim yet. Desktop/Core continue to provide those established
features where supported.

## Selection guide

| Environment | Recommended edition |
|---|---|
| Windows desktop | Desktop |
| macOS desktop | Desktop |
| Linux desktop | Desktop |
| Linux server with Python | Core |
| Docker host | Edge for minimal UDPFS; Core for full Python features |
| Raspberry Pi | Edge |
| OpenWrt router | Edge |
| NAS or embedded Linux | Edge |
| Need CHD now | Desktop/Core |
| Need UDPBD now | Desktop/Core |
