# Edge UDPFS operation and handoff diagnostics

This guide is for operating PS2 Servers Edge on OpenWrt and for tracing the
handoff from NHDDL to Neutrino. The examples use the A5-V11 test layout:

- router: `192.168.1.200`
- PS2: `192.168.1.201`
- USB root: `/mnt/ps2`
- UDPFS discovery port: UDP `62966` (`0xF5F6`)
- OpenWrt UCI section: `ps2servers-edge.main`

The commands and option names below match the current Edge command line,
OpenWrt init script, and default UCI configuration in this repository.

## Pick the right Edge mode

`ps2servers-edge` has three independent subcommands. The OpenWrt package can
run more than one at once as separate `procd` instances.

| Mode | What it serves | Use it for |
|---|---|---|
| `udpfs` | A directory tree, plus an optional block image through the same UDPFS instance | NHDDL, Neutrino's UDPFS backend, and UDPFS-aware file browsers such as wLaunchELF |
| `udpbd` | One raw disk image over the separate UDPBD protocol | A client explicitly using UDPBD, not a named-file UDPFS client |
| `smb` | One or more SMBv1 shares | OPL's SMB game list and POPSTARTER |

The NHDDL-to-Neutrino investigation is a **UDPFS** test. Starting `udpbd` does
not make a directory available to NHDDL, and SMB traffic cannot exercise the
UDPFS handoff code.

## What “UDPFS session” means

UDP is connectionless. There is no TCP-style connection to establish, close,
or query. Edge builds its own protocol session from the source IP, source port,
packet sequences, and recent activity it observes.

A normal two-port exchange is:

1. The PS2 sends a UDPFS `DISCOVERY` datagram to the router's discovery port,
   normally UDP 62966.
2. Edge replies with an `INFORM` that identifies its data port.
3. The PS2 sends file-operation and control datagrams to that data port.
4. Edge associates those datagrams with the observed PS2 endpoint and UDPFS
   sequence state.

With `data_port` set to `0`, the operating system chooses the data port at each
service start. The exact choice appears in the `UDPFS listening` log line:

```text
[info] UDPFS listening root=/mnt/ps2 discovery=0.0.0.0:62966 data=0.0.0.0:34724 ...
```

`single_port '1'` makes discovery and data share UDP 62966. In that mode Edge
does not open a separate data socket, so `data_port` has no effect for that
run. A fixed `data_port`, or single-port mode, is useful when a firewall cannot
allow the automatically selected data port. It should be changed as a
controlled test, not mixed into the initial baseline.

In `protocol=auto`, `session negotiated` means the pending session saw its
first payload-bearing DATA datagram, selected a profile, and selected the
response socket. Edge allocated the peer's server-side state earlier, when it
first accepted that peer. Forced `standard` and `modulo` modes do not emit this
auto-classification marker; use the verbose negotiation and packet fields in
those comparison runs. None of these events proves that the physical Ethernet
link will survive an IOP reset, that Neutrino started, or that a later datagram
will reach the process.

## UDPFS option map

Explicit command-line flags take precedence over environment defaults. The
OpenWrt service does not rely on those environment variables: its init script
reads UCI and emits explicit flags.

| Purpose | CLI flag | Environment | Current OpenWrt UCI option | Default / constraint |
|---|---|---|---|---|
| Enable service | — | — | `main.enabled` | `1` in the package |
| Directory root | `--root` | `FSROOT` | `main.root` | Required; `/mnt/sda1/PS2` in the package |
| IPv4 bind address | `--bind` | `BIND` | `main.bind` | `0.0.0.0` |
| Discovery port | `--port` | `PORT` | `main.port` | `62966`; `0` resolves to that default, otherwise range 1–65535 |
| Data port | `--data-port` | `DATA_PORT` | `main.data_port` | `0` chooses automatically; range 0–65535 |
| Protocol selection | `--protocol-mode` | `PROTOCOL_MODE` | `main.protocol` | `auto`, `standard`, or `modulo` |
| One UDP port | `--single-port` | `SINGLE_PORT` | `main.single_port` | Off |
| Idle-session expiry | `--peer-timeout` | `PEER_TIMEOUT` | `main.peer_timeout` | `1h`; range `1m`–`24h` |
| Transmit pacing | `--tx-delay-ms` | `TX_DELAY_MS` | `main.tx_delay_ms` | `0`; non-negative milliseconds |
| Read-only serving | `--read-only` | `RO` | `main.read_only` | Binary: writable; OpenWrt: read-only (`1`) |
| Log format | `--log-format` | `LOG_FORMAT` | `main.log_format` | `text` or `json` |
| Header/protocol trace | `--verbose` | `VERBOSE` | `main.verbose` | Off |
| Periodic counters | `--metrics` | `METRICS` | `main.metrics` | Off |
| Counter interval | `--metrics-period` | `METRICS_PERIOD` | `main.metrics_period` | `1m`; range `1s`–`24h` |
| UDPFS block image | `--block-device` | `BDPATH` | `main.block_device` | Empty disables it |
| Block-image sector size | `--sector-size` | `SECTOR_SIZE` | `main.sector_size` | `512`; multiple of 512, maximum 65536 |
| Raw compressed containers | `--no-compression` | `NO_COMPRESSION` | `main.no_compression` | Off; diagnostic only |
| Decompression cache | `--compression-cache-size` | `COMPRESSION_CACHE_SIZE` | `main.compression_cache_size` | `32`; range 0–4096 blocks per open image |

Direct CLI also supports `--quiet`, which suppresses informational logs but not
warnings or errors, and the deprecated `--modulo-mode` alias. Neither has a UCI
option. Do not use `--quiet` for a handoff trace.

### Protocol modes

| Value | Behaviour | When to use it |
|---|---|---|
| `auto` | Begins pending and classifies the client's sequence shape as standard or Modulo | Normal operation and the first diagnostic baseline |
| `standard` | Forces standard sequence interpretation | A one-variable comparison after `auto` has been captured |
| `modulo` | Forces Modulo sequence interpretation | A one-variable comparison for a known Modulo client |

Protocol mode controls sequence interpretation, not whether Edge uses one or
two local sockets. Keep `protocol=auto` unless the test is intentionally
isolating protocol classification.

### Compression and pacing are later-stage controls

`compression_cache_size` matters after a compressed game has been opened. It
cannot make a missing post-reset datagram appear. Likewise, `tx_delay_ms` paces
Edge's outgoing packets; it can help a slow link that is dropping bursts, but
it cannot repair a PS2 Ethernet link that never comes back up. Start both at
their defaults (`32` and `0`) for handoff isolation.

`no_compression '1'` makes Edge expose CSO/ZSO container bytes without decoding
them. A PS2 cannot boot those raw container bytes. Use it only to isolate the
decoder after traffic and file access are already proven.

## Operate the installed OpenWrt service

The current package installs `/usr/bin/ps2servers-edge`,
`/etc/init.d/ps2servers-edge`, and `/etc/config/ps2servers-edge`. The service
runs as the unprivileged `ps2edge` user and sends stdout/stderr to syslog.

| Task | Command |
|---|---|
| Start all enabled Edge instances | `/etc/init.d/ps2servers-edge start` |
| Stop all Edge instances | `/etc/init.d/ps2servers-edge stop` |
| Restart after a committed change | `/etc/init.d/ps2servers-edge restart` |
| Show service state | `/etc/init.d/ps2servers-edge status` |
| Start at boot | `/etc/init.d/ps2servers-edge enable` |
| Disable start at boot | `/etc/init.d/ps2servers-edge disable` |
| Show saved configuration | `uci show ps2servers-edge` |
| Show existing Edge log lines | `logread -e ps2servers-edge` |
| Follow Edge and Ethernet-link events | `logread -f | grep -E 'ps2servers-edge|link changed'` |
| Show running command lines | `ps w | grep '[p]s2servers-edge'` |
| Show listening UDP sockets | `netstat -lnup 2>/dev/null | grep ps2servers-edge` |

`start`, `stop`, and `restart` affect every enabled Edge mode. A router running
UDPFS and SMB will therefore have more than one `ps2servers-edge` process.

`uci set` changes UCI's pending value, `uci commit ps2servers-edge` writes the
package configuration to persistent storage, and `restart` rebuilds the
enabled `procd` instances with those values. Always inspect the new process
line and startup log after a change; editing UCI does not reconfigure an
already running process by itself.

The root must be mounted before Edge starts and must be readable and
directory-traversable by `ps2edge`. `read_only '1'` controls UDPFS writes; it
does not mount the USB filesystem read-only. To permit saves, both UCI
`read_only '0'` and operating-system write permission for `ps2edge` are needed.

Do not launch a second foreground UDPFS process while the service owns UDP
62966. To test the CLI directly, stop the service first:

```sh
/etc/init.d/ps2servers-edge stop
/usr/bin/ps2servers-edge udpfs \
  --root /mnt/ps2 \
  --bind 0.0.0.0 \
  --port 62966 \
  --data-port 0 \
  --protocol-mode auto \
  --peer-timeout 1h \
  --tx-delay-ms 0 \
  --compression-cache-size 32 \
  --read-only \
  --verbose \
  --metrics \
  --metrics-period 1s \
  --log-format text
```

Press Ctrl-C to stop the foreground process, then restart the managed service.

## Current UCI names versus older A5-V11 images

The current init script reads:

```text
ps2servers-edge.main.port
ps2servers-edge.main.protocol
```

Some earlier A5-V11 images reported options named `main.udpfs_port` and
`main.protocol_mode`. Those legacy names are **not read by the current init
script**. The hardware report's older image also ran the executable from
`/usr/sbin`, while the current package init script uses
`/usr/bin/ps2servers-edge`. The `/etc/init.d` commands avoid guessing the
binary location. After installing a current build, use the names in this guide.
On an unknown image, inspect the init script, saved values, and effective
command before drawing conclusions:

```sh
uci show ps2servers-edge
grep -n 'config_get.*main' /etc/init.d/ps2servers-edge
ps w | grep '[p]s2servers-edge'
```

A UCI assignment only proves the value was saved. The process line and
`UDPFS listening` log prove which value the running server actually received.

## Safe A5-V11 baseline

These commands keep the share read-only, preserve automatic protocol
selection, and enable only diagnostic output. They do not change PS2-side
configuration.

First preserve the current file in RAM for this boot:

```sh
cp -p /etc/config/ps2servers-edge /tmp/ps2servers-edge.before-handoff-trace
```

Apply the baseline:

```sh
uci set ps2servers-edge.main.enabled='1'
uci set ps2servers-edge.main.root='/mnt/ps2'
uci set ps2servers-edge.main.bind='0.0.0.0'
uci set ps2servers-edge.main.port='62966'
uci set ps2servers-edge.main.data_port='0'
uci set ps2servers-edge.main.protocol='auto'
uci set ps2servers-edge.main.single_port='0'
uci set ps2servers-edge.main.read_only='1'
uci set ps2servers-edge.main.peer_timeout='1h'
uci set ps2servers-edge.main.tx_delay_ms='0'
uci set ps2servers-edge.main.log_format='text'
uci set ps2servers-edge.main.verbose='1'
uci set ps2servers-edge.main.metrics='1'
uci set ps2servers-edge.main.metrics_period='1s'
uci set ps2servers-edge.main.compression_cache_size='32'
uci commit ps2servers-edge
/etc/init.d/ps2servers-edge restart
```

Verify the configuration before touching the PS2:

```sh
/etc/init.d/ps2servers-edge status
ps w | grep '[p]s2servers-edge'
netstat -lnup 2>/dev/null | grep ps2servers-edge
logread -e ps2servers-edge
```

The startup line must name `root=/mnt/ps2`, discovery port `62966`, and the
actual data port. If the process repeatedly exits, read `logread -e
ps2servers-edge`; `procd` respawns invalid configurations, so a bad value can
look like an unstable binary.

### Text versus JSON

Use `text` while watching a serial console:

```sh
uci set ps2servers-edge.main.log_format='text'
uci commit ps2servers-edge
/etc/init.d/ps2servers-edge restart
```

Use `json` when the output will be collected and parsed:

```sh
uci set ps2servers-edge.main.log_format='json'
uci commit ps2servers-edge
/etc/init.d/ps2servers-edge restart
```

The JSON object is emitted by Edge, but `logread` may prefix it with syslog
metadata. Capture the whole line rather than assuming the daemon log is a bare
JSON file.

## Controlled handoff trace

Keep one variable set for the whole run. Do not change protocol mode, port
topology, pacing, and compression at the same time.

1. In a second terminal, capture the complete live syslog stream for the
   positive control while displaying only Edge and switch-link events. `tee`
   writes before `grep`, so the file retains context that is not shown on
   screen and does not depend on the finite syslog ring still containing the
   beginning of the run.

   ```sh
   logread -f | tee /tmp/edge-wle-positive-control.log | grep -E 'ps2servers-edge|link changed'
   ```

2. In the first terminal, restart Edge so its startup line, counters, and
   session state all begin inside that live capture.

   ```sh
   /etc/init.d/ps2servers-edge restart
   ```

3. Run the wLaunchELF positive control. Browse the UDPFS root that maps to the
   router's `/mnt/ps2` directory and open or list content.

4. Stop that live capture with Ctrl-C. Then start a second live capture,
   writing to a different file:

   ```sh
   logread -f | tee /tmp/edge-nhddl-neutrino-handoff.log | grep -E 'ps2servers-edge|link changed'
   ```

5. In the first terminal, restart Edge once more to separate the runs and put
   the new startup line inside the second capture.

   ```sh
   /etc/init.d/ps2servers-edge restart
   ```

6. Start `mc0:/APP_NHDDL/nhddl.elf`, select the test game, and let it attempt
   the Neutrino launch without changing any router setting.

7. Continue recording through the Ethernet link-down event, any link-up event,
   the return to the Sony Browser, or a successful game open.

8. Stop the second live capture with Ctrl-C, then append runtime state to its
   file:

   ```sh
   {
     date
     /etc/init.d/ps2servers-edge status
     ps w | grep '[p]s2servers-edge'
     netstat -lnup 2>/dev/null | grep ps2servers-edge
   } >> /tmp/edge-nhddl-neutrino-handoff.log 2>&1
   ```

Both captures are in the router's RAM-backed `/tmp`; copy them off the router
before rebooting.

The Rumble Racing file on the A5-V11 host is:

```text
/mnt/ps2/CD/SLUS_201.74.Rumble Racing.iso
```

`UDPFS request` logs the client-supplied path before Edge resolves it below the
configured root. It therefore will not log the `/mnt/ps2` host prefix. For this
file, expect a relative request such as:

```text
CD/SLUS_201.74.Rumble Racing.iso
```

Edge rejects absolute client paths, so a leading `/` is not equivalent. Keep
the exact observed request in the trace rather than rewriting it to the host
path. A successful run should show that OPEN attempt followed by READ attempts
and increasing `bytes_read`, only after Neutrino has restored Ethernet and
resumed UDPFS.

## Read the trace in stages

### Startup and initial client

| Evidence | What it establishes |
|---|---|
| `UDPFS listening` | Edge opened its configured root and UDP sockets |
| `DISCOVERY received, console found this server` | A discovery datagram reached the Edge process from the shown endpoint |
| `session negotiated` | In `auto` mode, Edge saw the first payload-bearing DATA exchange and classified the pending profile/response socket; forced profiles do not emit this marker |
| First UDPFS operation / `OPEN` | The client progressed beyond transport negotiation into filesystem requests |
| `READ` and increasing `bytes_read` | Edge is actually returning file content |

The initial NHDDL discovery and negotiation are pre-handoff evidence. They do
not count as post-reset Neutrino traffic.

### The two handoff markers

Only one marker may be applicable to a given reset:

| Marker | Trigger | Meaning |
|---|---|---|
| `peer sequence reset (seq 0 received)` | The same PS2 IP:port sends payload-bearing DATA sequence zero when zero is neither the next expected sequence nor its immediately previous sequence | Edge discarded stale stream state and accepted a same-endpoint restart |
| `replacing stale session for IP` | A packet arrives from the same PS2 IP with a different UDP source port, and the old session has no active write or that write has been quiet for more than one second | Edge removed the old endpoint's worker/session and created one for the new endpoint |

Seeing a marker proves a datagram matching that restart/replacement condition
reached the corresponding Edge branch. Attribute it to Neutrino after the
reset only when its timestamp follows the captured launch and link-down
events. The marker does not, by itself, prove the following file open or game
boot succeeded. Absence of both markers is not a failed handoff implementation
when no post-reset datagram was received.

A payload-free control packet does not enter the reset branch. Sequence zero
is also processed normally when zero is already expected, and it is treated as
a duplicate/re-ACK when the next expected sequence is one. In those cases raw
RX and the verbose sequence fields, rather than the reset marker, are the
decisive packet evidence.

The sequence-reset line includes `expected`, `received`, `profile`, `socket`,
and `outcome`. The same-IP replacement line includes `old_peer`, `new_peer`,
both source ports, quiet time, active-write state, eligibility, and `reason`.
With verbose logging, `same-IP session replacement candidate` also records a
candidate that was deliberately retained to protect a recent active write.

### Datagram counters versus operation counters

Periodic `UDPFS metrics` lines are cumulative from service start. Raw RX/TX
counters answer whether packets crossed the process boundary; operation
counters (`open`, `read`, `dread`, `getstat`, and so on) count recognized
request attempts dispatched to a handler, including attempts that return an
error. They establish protocol-layer progress, not operation success. Positive
`bytes_read` or `bytes_written` establishes actual transferred file data.

The transport and handoff fields are:

| Metrics field | What it counts or records |
|---|---|
| `rx_discovery_datagrams`, `rx_discovery_bytes` | Datagrams and bytes read from the discovery socket |
| `rx_data_datagrams`, `rx_data_bytes` | Datagrams and bytes read from the data socket |
| `tx_discovery_datagrams`, `tx_discovery_bytes` | Datagrams and bytes successfully sent through the discovery socket |
| `tx_data_datagrams`, `tx_data_bytes` | Datagrams and bytes successfully sent through the data socket |
| `tx_send_errors` | UDP sends that returned an error |
| `malformed_datagrams` | Received datagrams rejected during protocol parsing before application request handling |
| `sequence_mismatches` | DATA sequence numbers that did not equal the session's next expected value |
| `sequence_resets` | Same-endpoint sequence-zero handoffs accepted by Edge |
| `same_ip_replacements` | Old sessions replaced after the same IP appeared from a new source port |
| `last_rx_peer`, `last_rx_socket` | Endpoint and Edge socket of the most recently read datagram |
| `last_rx_type`, `last_rx_sequence` | Parsed header values for that datagram, or `unknown` if its header could not be parsed |
| `last_rx_age` | Time since Edge read that datagram; a growing value makes silence explicit |

The `last_rx_*` fields are absent until the first datagram has been read.

| Pattern | Interpretation |
|---|---|
| RX remains unchanged after the NHDDL session and link-down | Edge did not read a later datagram on either UDP socket |
| Discovery RX grows, data RX does not | Discovery reaches Edge, but the client does not reach the advertised/fixed data path |
| Data RX grows, but operation counters do not | A datagram arrived; correlate its timestamp with the reset, then inspect header trace, malformed counts, ACK/sequence state, and handoff markers |
| `malformed_datagrams` grows | A datagram reached Edge but failed protocol parsing |
| `open` grows, `read` stays zero | A recognized OPEN attempt reached the filesystem layer; inspect the requested path. This build does not decode an OPEN outcome into a log field |
| `read` and `bytes_read` grow | Edge is serving content; a later boot failure is beyond initial UDPFS reachability |
| TX grows without later RX | Edge emitted replies locally, but UDP send success does not prove the PS2 received or processed them |
| Handoff counter/marker grows, then `open` grows | The server exercised the replacement path and a client resumed filesystem work; correlate the timeline before attributing it to Neutrino |

Verbose packet lines are header-level diagnostics. They identify direction,
socket, peer, packet type, size, sequence, ACK state, and parse failures without
dumping ISO or save payloads. Disable verbose logging after the controlled run;
on a transfer it can generate substantially more syslog traffic than the
periodic metrics alone.

The exact verbose markers are `UDP RX` and `UDP TX`. Each includes `socket`,
`local`, `peer`, `bytes`, `type`, and `sequence` when the header is parseable.
A parseable DATA datagram also includes `ack`, `flags`, `header_words`, and
`data_bytes`. A non-empty `UDP RX` request includes its `opcode`, because Edge
dispatches byte zero of that inbound payload as the UDPFS operation. On `UDP
TX`, an opcode is included only when nonzero `header_words` identifies an
application header; raw ISO/save continuation bytes are never interpreted or
logged as an opcode. `UDP send failed` carries the same safe header fields plus
the error. No packet payload is dumped.

`UDPFS request` identifies an application request after valid dispatch. A
safely decoded OPEN includes `path`, `is_dir`, and `flags`; a READ includes its
`handle` and requested `size`. This is the line that distinguishes “a DATA
packet arrived” from “Neutrino asked Edge to open the selected ISO.” It records
the attempt, not the OPEN reply's result, handle, or size.

## What silence proves—and what it does not

If Edge remains running, its periodic metrics continue, and RX does not change
after the NHDDL-created session, the defensible statement is:

> No post-negotiation datagram was read by the Edge UDP sockets during the
> captured interval.

That result proves the sequence-reset, same-IP replacement, and file-open paths
were not reached. It does **not** prove that:

- Neutrino never attempted to send;
- a packet was not dropped before reaching the process;
- the Ethernet PHY/link was up;
- the PS2 used the expected IP or destination port;
- a firewall did not discard the packet;
- the server's handoff logic would fail if a packet reached it.

For the reported A5-V11 failure, the missing post-reset link-up event plus
unchanged server RX focuses the next investigation on the NHDDL/Neutrino launch,
IOP/DEV9/SMAP initialization, configuration, or route to the server. It does not
exercise the Edge handoff branches.

## The wLaunchELF positive control

Browse `/mnt/ps2` through UDPFS from wLaunchELF with the same PS2, cable,
addresses, router, USB drive, and Edge configuration used for the failing run.

A successful browse proves, at that moment:

- the PS2 Ethernet hardware and physical path can work;
- `192.168.1.201` can reach the A5-V11 at `192.168.1.200`;
- Edge discovery and data traffic can cross the network;
- Edge can read and list the mounted `/mnt/ps2` tree.

It does not prove that NHDDL selected the intended Neutrino ELF, passed a
complete argument vector, that Neutrino loaded its network IRX modules, or that
Ethernet returned after Neutrino's reset. Preserve this distinction when
comparing the positive-control trace to the failing handoff trace.

## One-variable follow-up tests

Run these only after saving the `auto`, dynamic-data-port baseline.

### Single port

```sh
uci set ps2servers-edge.main.single_port='1'
uci commit ps2servers-edge
/etc/init.d/ps2servers-edge restart
```

This isolates the advertised dynamic data-port path. Return it to `0` before a
different comparison.

### Fixed data port

```sh
uci set ps2servers-edge.main.single_port='0'
uci set ps2servers-edge.main.data_port='62967'
uci commit ps2servers-edge
/etc/init.d/ps2servers-edge restart
```

Allow UDP 62966 and 62967 only from the trusted PS2 LAN. This test keeps the
two-port protocol shape but removes the per-restart data-port change.

### Forced standard mode

```sh
uci set ps2servers-edge.main.protocol='standard'
uci commit ps2servers-edge
/etc/init.d/ps2servers-edge restart
```

Use this only to compare against a trace that negotiated `profile=standard` in
`auto`. Restore `auto` afterward. A forced profile cannot make Neutrino restore
the Ethernet link or create its first socket.

### Transmit pacing

If packets arrive and large Edge responses show loss/retry symptoms, test a
small delay such as `0.25` ms, then `1` ms in a separate run:

```sh
uci set ps2servers-edge.main.tx_delay_ms='0.25'
uci commit ps2servers-edge
/etc/init.d/ps2servers-edge restart
```

Do not use pacing as an explanation for a run where post-reset RX is zero.

## Disable diagnostics and roll back

To keep the current functional settings but stop high-volume diagnostics:

```sh
uci set ps2servers-edge.main.verbose='0'
uci set ps2servers-edge.main.metrics='0'
uci set ps2servers-edge.main.metrics_period='1m'
uci set ps2servers-edge.main.log_format='text'
uci commit ps2servers-edge
/etc/init.d/ps2servers-edge restart
```

Also restore diagnostic comparison values if they were changed:

```sh
uci set ps2servers-edge.main.protocol='auto'
uci set ps2servers-edge.main.single_port='0'
uci set ps2servers-edge.main.data_port='0'
uci set ps2servers-edge.main.tx_delay_ms='0'
uci commit ps2servers-edge
/etc/init.d/ps2servers-edge restart
```

To restore the exact pre-trace file saved earlier during the same boot:

```sh
cp -p /tmp/ps2servers-edge.before-handoff-trace /etc/config/ps2servers-edge
/etc/init.d/ps2servers-edge restart
```

The `/tmp` backup is in RAM and disappears at reboot. Copy it somewhere
persistent first if the diagnostic session will span a reboot.
