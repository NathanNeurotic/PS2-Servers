# SMB setup: choose the dialect your client supports

Desktop/Core has SMBv1, SMBv2, and SMBv3. Edge provides SMBv1 only.
An SMBv1-only PS2 loader cannot connect to SMB2/3 simply because both servers
share the same folder. Use the loader's supported protocol.

## SMBv1 for traditional OPL clients

Select the parent of `DVD/` and `CD/` as the games folder, start the SMBv1 card,
and copy the running card's IP, port and share into the loader. Use address type
IP, user `guest`, and a blank password. The share name defaults to `games`.

**Port defaults depend on the entry point:**

| Entry point | Default TCP port |
|---|---|
| Desktop SMBv1 card | 1025 |
| Core `serve smbv1` / standalone Python script | 1111 |
| `Start-SMB-Server.bat` | 1111 |
| Edge `smb` | 1111 |

Saved settings and explicit arguments override these defaults. The Python SMBv1
server may move forward to a free port when the requested port is occupied;
use the port it actually prints. Two servers cannot share the same listening
address/port. See [the standalone guide](../smbv1_server/README.md).

## SMB2 and SMB3

Use these cards only for a compatible client. SMBv2 negotiates up to SMB 2.1;
SMBv3 allows up to SMB 3.0.2. Both cards use the same implementation and default
to TCP 1445. Start only one on that port, or give each a different port.

Choose a games folder, enter a username (default `ps2`) and a password, then
start. The advanced **No password (anyone on the network can read it)** option
explicitly enables an open share. It removes the authentication requirement;
the separate **Read-only** control determines whether writes are allowed.

Windows Explorer uses TCP 445 rather than a custom SMB port. The optional
**Take port 445 (admin)** setting temporarily stops Windows File Sharing to
attempt to free that port; it can still fail if another service holds it. A
client that supports a custom port can use 1445 without that setup. These modes
do not enable the Windows SMB1 optional feature.

The launcher saves credentials in its per-user configuration. Treat that file
as private, and avoid including credentials or the full saved configuration in
bug reports. Authentication is not a reason to expose the server to the internet.

## Writes and testing

Writable is the default for the bare servers. The OS account running the server
must also have permission to write the share. **Read-only** prevents settings,
saves, or VMC writes to that share; the client needs another supported destination.
Writable serving does not add a VMC implementation to a loader.

Use a known-good ISO and verify listing, launching, and saving separately on the
chosen loader. Host SMB unit tests and Edge/Python wire comparisons run in CI;
those tests do not establish compatibility with every SMB dialect/client pair.

## SMBv1 session diagnostics

SMBv1 now prints connection/disconnection events, the first read of each opened
file, and activity summaries without enabling Verbose logging. The Desktop
Terminal captures these lines; Core/standalone prints them to stderr.

During activity, summaries are emitted at most once every five seconds, after
a response completes. They include session uptime, request/read/write counts,
MiB served, interval read throughput, open files/searches, protocol/connection
errors, unexpected short reads, and slow disk-read/response-send counts. A final
summary is printed on disconnect. An idle session or an operation that has not
returned does not produce a heartbeat; silence alone does not establish a hang.

Disk time measures seek/read on the host. Send time measures the socket send,
which can reflect client/network backpressure and is not a packet-loss counter.
Operations taking at least 250 ms generate a warning, as do reads shorter than
the expected bytes before the file's known EOF. Repeated warnings are limited
to one per five seconds per connection; aggregate counters include all events.
Normal EOF and the existing SMBv1 read-size cap are not short-read errors.
Verbose logging remains available for detailed setup/open/browse tracing.

For delayed loading, capture the terminal around the event and record the exact
PS2-Servers build, OPL build, game/Game ID, PS2 model, VMC/PADEMU states, host disk
and wired/Wi-Fi topology. Keep the same ISO, server, settings and route when
comparing OPL builds. A simulated aged-session host test does not establish
multi-hour gameplay stability; that still requires a physical PS2 reproduction.
