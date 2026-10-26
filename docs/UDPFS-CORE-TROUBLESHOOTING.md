# Python Core UDPFS: service setup and failure capture

Use `python3 ps2servers.py serve udpfs ...` from a source installation, or the
packaged executable with `serve udpfs`. This selects Core's automatic Standard/
Modulo compatibility handling. A direct launch of `udpfs_server/udpfs_server.py`
selects the standalone engine. The two entry points are deliberately retained;
a standalone log does not demonstrate Core behavior.

For Alpine service installation, use the [OpenRC example](../packaging/openrc/README.md).
For other Linux service installations, see [systemd](../packaging/systemd/README.md).
The [Edge/OpenWrt handoff guide](UDPFS-HANDOFF-DIAGNOSTICS.md) describes the separate
Go deployment and protocol evidence in more detail. Its UCI commands and Go log
markers do not apply to Python Core.

## Establish what is running

Retain the startup banner and both stdout/stderr logs. Core reports:

- Release version and commit when available, using existing release metadata.
  A source archive without Git/build metadata may have only a release version;
  also record the archive/release name. A source commit does not identify local
  uncommitted modifications, which should be disclosed separately.
- Root, optional block-device geometry, actual discovery and data socket binds.
  Discovery intentionally listens on all interfaces; `--bind` selects the data
  interface. An automatic data port can change after a restart.
- Protocol mode, effective idle timeout in seconds, read/write mode and verbose
  state. These are running settings, not merely the contents of a service file.

Do not run a second instance while the service owns the discovery port. Verify
the configured root is the intended mounted filesystem, including its contents,
and that the service account can traverse/read it. Read/write mode also needs
filesystem write permission for settings/saves; startup does not prove that.
Missing LZ4/libchdr warnings concern the relevant compressed formats, not plain ISO.

## Capture one failure

1. Record the server release/commit, console model, exact loader build and game,
   image format, service command/options and topology. Say whether the console
   and host share a subnet/VLAN and whether a router/firewall is on their path.
2. Preserve existing logs, enable `--verbose`, and restart the service once.
   Boot the console into a fresh session afterward. Restart loses handles and
   protocol state, so an old console session is not a clean reproduction.
3. Confirm the new banner matches the intended settings. Keep root, data port,
   protocol mode and topology unchanged during this capture.
4. Reproduce with the same game. For intermittent launch failures, try several
   launches while keeping the server running; record which attempt fails.
5. Record the time and console screen where it stops, and whether the game was
   actively loading, playing, paused, or resuming. Preserve at least another
   minute of logs after a failure. Note any supervisor restart/new banner.
6. Save both logs before disabling verbose. Share the relevant uninterrupted
   interval. Redact private paths/addresses as necessary, but preserve consistent
   peer identities, timestamps, sequence fields and ports needed for correlation.

For an overnight pause test, explicitly choose `--peer-timeout 86400` (24h) before
starting the fresh session. The default is 3600s and supported range is
60–86400s; zero does not disable expiry. Expiry closes open files. Raising the
timeout cannot restore old handles or establish why a console stopped sending.
Keep pause/resume testing separate from initial/repeated-launch testing.

## Read the evidence in order

| Evidence | What it establishes / next question |
| --- | --- |
| Startup banner | Sockets were bound and the shown settings were applied; not console reachability. |
| `DISCOVERY`, `INFORM sendto OK` | Discovery reached the host and the OS accepted a reply; not PS2 receipt. Repeated discovery alone does not prove packet loss. |
| Profile negotiation / first filesystem request | The client progressed into data exchange. Check subsequent operations rather than changing protocol mode speculatively. |
| Directory listing / ISO `OPEN` | Listing or open dispatch progressed; not game boot or sustained reads. |
| BREAD request or byte counters | Request/activity evidence. Correlate with response completion and acknowledgments; not physical-console acceptance. |
| `no request for ...` | Inspect last BREAD read/reply state, pending reply packets and last ACK. Neighbor reachability does not prove UDPFS/game progress. |
| `idle ... > peer-timeout ...` | Session cleanup after inactivity. It may break a paused game's resumption, or clean up a session that failed earlier. |
| Another startup banner / changed data port | Investigate process/supervisor restart and stderr; preserve the prior interval. |

If INFORM is sent but no DATA arrives, compare the logged source/destination
ports with a host packet capture and the actual firewall/NAT/container path.
For fixed-port deployments permit discovery UDP 62966 and the configured data
port; do not assume both are 62966. Change one setting at a time after preserving
the baseline. A different cable, fixed port or protocol mode is a controlled
comparison, not a root-cause conclusion by itself.
