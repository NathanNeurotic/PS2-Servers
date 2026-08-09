# PS2-Servers Edge (`ps2servers-edge`) Operator & Command Manual

`ps2servers-edge` is the lightweight, high-performance standalone server for the PlayStation 2. It runs on Linux, OpenWrt micro-routers, macOS, and Windows without requiring a graphical desktop environment.

---

## 📑 Table of Contents
1. [Core Architecture & Modes](#1-core-architecture--modes)
2. [Quickstart Command Reference](#2-quickstart-command-reference)
3. [Subcommand: `udpfs` (Directory & ISO Serving)](#3-subcommand-udpfs-directory--iso-serving)
4. [Subcommand: `udpbd` (Virtual Hard Drive Emulation)](#4-subcommand-udpbd-virtual-hard-drive-emulation)
5. [Subcommand: `smb` (OPL SMBv1 Share Serving)](#5-subcommand-smb-opl-smbv1-share-serving)
6. [Subcommand: `webui` (Embedded Web Management GUI)](#6-subcommand-webui-embedded-web-management-gui)
7. [Diagnostics & Observability Guide](#7-diagnostics--observability-guide)
8. [System Deployment Guides (OpenWrt & Linux `systemd`)](#8-system-deployment-guides-openwrt--linux-systemd)

---

## 1. Core Architecture & Modes

`ps2servers-edge` provides four independent subcommands:

| Subcommand | Protocol | Client Applications | Purpose |
|---|---|---|---|
| **`udpfs`** | UDPFS | NHDDL, Neutrino, UDPFS File Browsers | Serves game ISOs, homebrew, VMCs, and save files directly from a directory folder structure. |
| **`udpbd`** | UDPBD | OPL (UDPBD mode) | Serves one raw hard drive disk image (`.img`) over UDP block-level hardware emulation. |
| **`smb`** | SMBv1 | Open-PS2-Loader (OPL), POPSTARTER | Serves directory shares over an OPL-compatible SMBv1 TCP port. |
| **`webui`** | HTTP/JSON | Web Browsers (Mobile & Desktop) | Embedded responsive web GUI dashboard for full configuration, live log streaming, and service control. |

---

## 2. Quickstart Command Reference

### Standard Terminal Usage
```bash
# 1. Serve a directory of PS2 games over UDPFS (Default Port 62966)
ps2servers-edge udpfs --root /srv/ps2/games

# 2. Serve a directory over SMBv1 for OPL (Default Port 1111)
ps2servers-edge smb --share games=/srv/ps2/games --port 1111

# 3. Serve a single disk image over UDPBD (Default Port 48573 / 0xBDBD)
ps2servers-edge udpbd --image /srv/ps2/hdd.img

# 4. Serve the Web Management GUI (Default Port 8082)
ps2servers-edge webui --webui-port 8082
```

---

## 3. Subcommand: `udpfs` (Directory & ISO Serving)

`udpfs` serves a root directory containing game ISOs (`DVD/`, `CD/`), Virtual Memory Cards (`VMC/`), and homebrew ELFs.

### Command Syntax
```bash
ps2servers-edge udpfs --root <PATH> [OPTIONS]
```

### Complete `udpfs` Arguments

| Argument Flag | Value Type | Default | Description & Usage |
|---|---|---|---|
| **`--root <PATH>`** | `string` | *(Required)* | Base directory containing PS2 game folders (`DVD/`, `CD/`, `APPS/`, `VMC/`). |
| **`--bind <IP>`** | `string` | `"0.0.0.0"` | IPv4 address to listen on. Use `0.0.0.0` for all interfaces or pin to a specific local IP. |
| **`--port <PORT>`** | `int` | `62966` | Discovery and control UDP port for UDPFS. |
| **`--data-port <PORT>`** | `int` | `0` | Fixed data UDP port. Default `0` dynamically allocates data ports. |
| **`--single-port`** | `bool` | `false` | Forces all control and data traffic through `--port` (62966). Essential for legacy clients or strict asymmetric firewalls. |
| **`--protocol-mode <MODE>`** | `string` | `"auto"` | Packet sequence handling mode: `auto` (auto-detects client type), `standard` (NHDDL/Neutrino), or `modulo`. |
| **`--read-only`** | `bool` | `false` | Restricts clients to read-only access. Omit this flag to allow VMC save creation/updates. |
| **`--no-compression`** | `bool` | `false` | Disables internal CSO/ZSO decompressor; serves compressed ISO files as raw byte streams (reduces CPU usage on low-end micro-routers). |
| **`--compression-cache-size <N>`** | `int` | `32` | Maximum decompressed CSO/ZSO blocks cached in RAM per open file. |
| **`--block-device <PATH>`** | `string` | `""` | Optional raw disk image path to serve over block calls alongside directory calls. |
| **`--sector-size <BYTES>`** | `int` | `512` | Sector size for `--block-device` (e.g., `512` or `2048`). |
| **`--peer-timeout <DURATION>`** | `string` | `"1h"` | Idle session timeout (e.g. `30m`, `1h`) before cleaning up inactive client connections. |
| **`--tx-delay-ms <MS>`** | `float` | `0.0` | Artificial inter-packet transmit delay in milliseconds (e.g., `0.5`, `1.0`) for sensitive network paths. |
| **`--metrics`** | `bool` | `false` | Enables periodic reporting of transfer rates, operation counts, and session liveness. |
| **`--metrics-period <DURATION>`** | `string` | `"1m"` | Interval between metric log reports (e.g., `1s`, `5s`, `1m`). |
| **`--verbose`** | `bool` | `false` | Enables detailed datagram header tracing, session resets, and operation logs. |
| **`--quiet`** | `bool` | `false` | Suppresses non-essential informational log messages. |
| **`--log-format <FORMAT>`** | `string` | `"text"` | Console log format: `text` (human-readable) or `json` (structured). |

#### Real-World `udpfs` Examples:

* **Production Game Server with Live Diagnostics:**
  ```bash
  ps2servers-edge udpfs \
    --root /mnt/storage/ps2 \
    --verbose \
    --metrics \
    --metrics-period 1s \
    --log-format json
  ```

* **Single-Port Read-Only Mode (For Micro-Routers / Simple Networks):**
  ```bash
  ps2servers-edge udpfs \
    --root /srv/ps2 \
    --single-port \
    --read-only
  ```

---

## 4. Subcommand: `udpbd` (Virtual Hard Drive Emulation)

`udpbd` presents a single raw disk image file (`.img`) to the PS2 as a virtual hardware block device.

### Command Syntax
```bash
ps2servers-edge udpbd --image <PATH> [OPTIONS]
```

### Complete `udpbd` Arguments

| Argument Flag | Value Type | Default | Description & Usage |
|---|---|---|---|
| **`--image <PATH>`** | `string` | *(Required)* | Path to the raw disk image file (`.img` / `.raw`). |
| **`--bind <IP>`** | `string` | `"0.0.0.0"` | IPv4 address to listen on. |
| **`--port <PORT>`** | `int` | `48573` | UDP port for UDPBD traffic (matches default PS2 UDPBD port `0xBDBD`). |
| **`--read-only`** | `bool` | `false` | Serves the disk image read-only. |
| **`--status-port <PORT>`** | `int` | `-1` | UDP port answering router status/health queries (`-1` disables). |
| **`--verbose`** | `bool` | `false` | Enables debug log traces for block read/write calls. |
| **`--quiet`** | `bool` | `false` | Suppresses non-essential informational logs. |
| **`--log-format <FORMAT>`** | `string` | `"text"` | Log format: `text` or `json`. |

#### Real-World `udpbd` Example:
```bash
ps2servers-edge udpbd \
  --image /var/storage/ps2_hdd.img \
  --port 48573 \
  --verbose
```

---

## 5. Subcommand: `smb` (OPL SMBv1 Share Serving)

`smb` provides a high-performance SMBv1 service specifically tuned for Open-PS2-Loader (OPL) and POPSTARTER.

### Command Syntax
```bash
ps2servers-edge smb --share NAME=PATH [OPTIONS]
```

### Complete `smb` Arguments

| Argument Flag | Value Type | Default | Description & Usage |
|---|---|---|---|
| **`--share <NAME=PATH>`** | `string` | *(Required)* | Defines an SMB share name mapped to a local folder. Repeatable for multiple shares. |
| **`--root <PATH>`** | `string` | `""` | Shorthand helper for `--share games=PATH`. |
| **`--bind <IP>`** | `string` | `"0.0.0.0"` | IPv4 bind address. |
| **`--port <PORT>`** | `int` | `1111` | TCP port for SMBv1 (Set OPL's **SMB Port** to match). |
| **`--read-only`** | `bool` | `false` | Restricts shares to read-only access. |
| **`--status-port <PORT>`** | `int` | `-1` | UDP status port for router health queries (`-1` disables). |
| **`--verbose`** | `bool` | `false` | Logs SMB session establishment, authentication, and file open calls. |
| **`--quiet`** | `bool` | `false` | Suppresses informational log output. |
| **`--log-format <FORMAT>`** | `string` | `"text"` | Log format: `text` or `json`. |

#### Real-World `smb` Example:
```bash
ps2servers-edge smb \
  --share games=/srv/ps2/games \
  --share popstarter=/srv/ps2/pops \
  --port 1111 \
  --verbose
```

---

## 6. Subcommand: `webui` (Embedded Web Management GUI)

`webui` serves a mobile-responsive single-page dashboard for full configuration, real-time log streaming, and process management across all Edge servers.

> [!IMPORTANT]
> **Network scope.** The web UI rewrites configuration and restarts services, so
> reaching it is equivalent to shell access on the box. It binds `127.0.0.1` by
> default and **refuses to start on any other address without `--auth-pass`**
> (or `--insecure`, which generates a password and logs it). There is no TLS.
>
> `--no-browse` removes `/api/browse` entirely; by default that endpoint is
> already confined to the directories this box serves, and `--browse-anywhere`
> is what lifts the confinement.

### Command Syntax
```bash
ps2servers-edge webui [OPTIONS]
```

### Complete `webui` Arguments

| Argument Flag | Value Type | Default | Description & Usage |
|---|---|---|---|
| **`--webui-port <PORT>`** | `int` | `8082` | HTTP port for the web dashboard (0 uses 8082 with fallback to OS-assigned port). |
| **`--bind <IP>`** | `string` | `"127.0.0.1"` | IPv4 bind address. Anything other than loopback requires `--auth-pass` or `--insecure`; without one the server exits rather than starting. |
| **`--auth-user <NAME>`** | `string` | `"admin"` | HTTP basic auth username. |
| **`--auth-pass <PASS>`** | `string` | `""` | HTTP basic auth password. Empty means no authentication, accepted only on a loopback bind. |
| **`--insecure`** | `bool` | `false` | Accept a LAN bind with no password given. One is generated and written to the log at each start. |
| **`--config-file <PATH>`** | `string` | `"/etc/ps2servers-edge/config.json"` | Path to JSON config file (used on generic Linux/systemd/Docker). |
| **`--log-lines <N>`** | `int` | `1000` | Capacity of the in-memory ring buffer for live SSE log streaming. |
| **`--max-log-clients <N>`** | `int` | `8` | Concurrent `/api/logs` streams. Each is a goroutine and a buffer. |
| **`--no-browse`** | `bool` | `false` | Removes the file browser endpoint (`/api/browse`) entirely. |
| **`--browse-anywhere`** | `bool` | `false` | Lets the file browser leave the configured server directories. By default it is confined to the UDPFS root and the SMB share paths. |
| **`--verbose`** | `bool` | `false` | Enables verbose HTTP access logging. |
| **`--quiet`** | `bool` | `false` | Suppresses non-essential log output. |
| **`--log-format <FORMAT>`** | `string` | `"text"` | Log format: `text` or `json`. |

#### Real-World `webui` Example:
```bash
# On the machine itself:
ps2servers-edge webui --webui-port 8082

# Reachable from a phone on the LAN:
ps2servers-edge webui \
  --webui-port 8082 \
  --bind 0.0.0.0 \
  --auth-user admin \
  --auth-pass 'pick something long'
```

> [!NOTE]
> The **Logs** tab carries the web UI's own output. `udpfs`, `smb` and `udpbd`
> are separate processes under both procd and systemd, so for their logs use
> `logread -e ps2servers-edge` or `journalctl -u 'ps2servers-edge@*'`.

---

## 7. Diagnostics & Observability Guide

When troubleshooting network connectivity or handoff behavior between loaders (e.g. NHDDL → Neutrino), enable the diagnostic suite:

```bash
ps2servers-edge udpfs \
  --root /srv/ps2/games \
  --verbose \
  --metrics \
  --metrics-period 1s \
  --log-format json
```

### Key Diagnostic Log Triggers

- **`UDP RX`**: Confirms a physical datagram reached Edge on the discovery or data socket.
- **`last_rx_age`**: Shows elapsed time since the last received packet. Growing `last_rx_age` with no RX increase confirms no packets reached Edge on this socket.
- **`UDPFS request operation=OPEN`**: Logs file open requests, path resolution, and returned file handles.
- **`peer sequence reset (seq 0 received)`**: Identifies an accepted console reset/restart.
- **`replacing stale session for IP`**: Logs when a console shifts source ports across loader transitions.

---

## 8. System Deployment Guides

### Option A: Running as an OpenWrt Service (`uci`)

On OpenWrt routers, configure Edge via `uci`:

```sh
uci set ps2servers-edge.main.root='/mnt/storage/ps2'
uci set ps2servers-edge.main.verbose='1'
uci set ps2servers-edge.main.metrics='1'
uci set ps2servers-edge.main.metrics_period='1s'
uci set ps2servers-edge.main.log_format='json'
uci commit ps2servers-edge

# Manage service
/etc/init.d/ps2servers-edge restart
logread -f | grep ps2servers-edge
```

---

### Option B: Running as a Linux Systemd Service

For Ubuntu, Debian, Proxmox LXC/VM, or Raspberry Pi OS:

Create `/etc/systemd/system/ps2servers-edge.service`:

```ini
[Unit]
Description=PS2 Servers Edge Service
After=network.target

[Service]
Type=simple
User=root
ExecStart=/usr/local/bin/ps2servers-edge udpfs --root /srv/ps2/games --verbose --metrics
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable and start the service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ps2servers-edge

# View live logs
sudo journalctl -u ps2servers-edge -f
```
