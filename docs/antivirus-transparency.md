# Antivirus Transparency

PS2 Servers is an open-source PS2 homebrew utility for user-controlled local
server setup. It starts LAN servers so Open PS2 Loader can load files from a PC.

This file is the canonical, plain-language statement of what the program does,
why antivirus engines sometimes flag it, how to verify a download, and how to
report a false positive to your vendor. A styled web version of the same
information is published at [falsepositives.html](falsepositives.html).

## Vendor and release identity

- Vendor / company name: NathanNeurotic
- Product name: PS2 Servers
- Windows executable: PS2Servers.exe
- Windows release package: PS2Servers-windows-x64.zip
- Windows portable package (alternative): PS2Servers-windows-x64-portable.zip
- 32-bit Windows release package: PS2Servers-windows-x86.zip
- 32-bit Windows portable package (alternative): PS2Servers-windows-x86-portable.zip
- Repository: https://github.com/NathanNeurotic/PS2-Servers

Packaged Windows builds include version metadata: company name, product name,
file description, product version, file version, and copyright.

## Why a download may be flagged

The packaged Windows build is **unsigned** (the project has no code-signing
certificate — see [Code signing](#code-signing)) and provides **local
network-server behavior by design**. An unsigned, low-reputation executable that
opens listening sockets is exactly the profile that generic, heuristic, and
machine-learning antivirus engines treat as suspicious, so a detection here is
not enough on its own to classify a file as either safe or malicious.
Characteristics that can trip heuristics:

- It is unsigned and not yet reputation-established with Microsoft SmartScreen or
  antivirus cloud services.
- It binds local server ports and uses UDP broadcast so the PS2 can
  auto-discover it.
- It is packaged with [Nuitka](https://nuitka.net). The **single-file** build
  self-extracts to a temporary directory on launch and then re-executes itself
  (with an internal `--serve` flag) so the embedded Python interpreter can run a
  server with no system Python installed. "Extract-then-run" and "launch a copy
  of myself" are generic packer/dropper heuristics. The **folder** download
  (below) has no self-extraction step and is the recommended Windows download.
- On Windows it may run a short, hidden PowerShell command to create firewall
  allow rules — but only after explicit user consent.

None of these is malicious behavior; each is inherent to "an unsigned, bundled,
local network tool."

## Two download shapes (portable is recommended on Windows)

**On Windows, the portable build is the recommended download.** Windows and Linux
each ship a **portable** build (`PS2Servers-windows-x64-portable.zip` — or
`PS2Servers-windows-x86-portable.zip` on 32-bit Windows — and
`PS2Servers-linux-x64-portable.tar.gz`): the same application laid out as a plain
folder of the executable plus its libraries, with **no self-extracting
bootstrap** — which is the thing that makes the single-file `.exe` trip
antivirus/SmartScreen heuristics. The portable build avoids that wrapper but remains unsigned and may also be
flagged. Packaging is not a guarantee of antivirus clearance. Unzip it and run `PS2Servers.exe` (Windows) or
`PS2Servers` (Linux) from inside the folder. A single-file build is also
published per platform for convenience. macOS already ships as a standalone
`.app`.

## Network behavior

Desktop/Core starts the serving modes selected through the GUI or CLI:

- SMBv1: built-in SMB/CIFS subset. Desktop defaults to TCP 1025; Core/standalone and Edge default to TCP port 1111. Use the running server’s printed port.
- UDPFS: UDP file/block serving, normally UDP port 0xF5F6 for discovery. A second UDP socket uses an OS-assigned data port unless pinned ("Data port"); single-port and Core forced-Modulo operation use discovery for data too. Auto selects a protocol profile per peer. Core **Bind address (PC)** pins the data socket, independently of the top LAN IP setup hint.
- UDPBD: UDP block-device serving, normally UDP port 0xBDBD.
- SMB2/3: authenticated file sharing, normally TCP 1445.
- HTTP game server: unauthenticated, read-only serving, normally TCP 1100.
- Direct-link helper: optional DHCP on a dedicated PS2 interface (UDP 67/68), with temporary interface configuration.

Edge has UDPFS, SMBv1, UDPBD, and an optional HTTP management dashboard on TCP
8082. The dashboard requires authentication for a LAN bind. SMB/UDPBD may also
answer the router-status query on UDP 62966 when enabled. See [Edge setup](EDGE.md).

The app opens project/release links in the browser when selected. In source
mode, a confirmed compression-dependency install invokes pip and contacts its
configured package index. Core/Edge servers can also be started by manually
installed services or containers; they are not limited to GUI starts.

The Windows SMB server uses PS2 Servers' own SMB/CIFS implementation. It does not enable Windows SMB1, does not disable Windows SMB1 automatic removal, and does not install or remove Windows optional features.

## Windows elevation, services, and firewall

- **Elevation (UAC):** the launcher starts non-elevated. It requests
  administrator rights through the standard Windows UAC prompt
  (`ShellExecute "runas"`) **only** when you choose to create or remove PS2
  Servers firewall rules, configure/restore the direct-link interface, or use
  the advanced port-445 mode. The packaged build
  keeps the default `asInvoker` manifest — it never silently auto-elevates on
  launch.
- **Firewall:** Windows Firewall changes require user action and consent. Rules
  created by the app use the prefix `PS2 Servers - ...` and can be removed from
  the GUI or with the command below. The firewall logic runs as inline
  PowerShell (`-Command`, not a `.ps1` script file) and does **not** pass
  `-ExecutionPolicy Bypass`. The one exception is the standalone
  `smbv1_server/Start-SMB-Server.bat`: it creates its single
  `PS2 Servers - SMB Server` rule through an elevated inline PowerShell that
  **does** pass `-ExecutionPolicy Bypass` (the batch file cannot otherwise run
  PowerShell on machines with a restrictive policy).
- **Windows file-sharing service:** the optional, off-by-default "Take port 445"
  SMB mode temporarily **stops** the Windows `LanmanServer` service while that
  server runs and **restarts it on exit**. It only stops the service (never
  disables it), so a reboot restores it. This mode is admin-gated and disclosed
  in the GUI.
- **Hidden windows:** server child processes and the firewall PowerShell call are
  launched with `CREATE_NO_WINDOW` so a GUI app does not flash console windows.
  This is cosmetic, not stealth.

Windows Firewall rules can be removed from the GUI or with:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\remove-windows-firewall-rules.ps1
```

## Saved settings, optional services, and cleanup

The launcher saves `launcher.json` in the per-user configuration directory;
see [the README](../README.md#direct-cable-and-saved-settings) for each OS path.
That file can contain configured SMB2/3 credentials. **Auto-start servers on
launch** starts them when the app opens; it does not register an OS startup task.

Direct-link mode changes a dedicated network interface and runs DHCP with
elevation. Disable it before removing the app so the saved network state can be
restored. Stop servers, delete the extracted app folder, remove the app's
firewall rules if desired, and optionally remove its per-user configuration.
Manually installed systemd/OpenWrt services or containers need separate removal.
The Edge dashboard can save configuration and restart supported services when
given the required permissions; it is an intentional remote-management feature.

The project does not add advertising, browser extensions, crypto-mining, or
credential collection for an external service. Configured credentials and
operator-installed services are distinct from those behaviors.

## False-positive review note

PS2 Servers has been submitted to Avast/Gen Threat Labs for false-positive review.
This records a submission, not vendor approval of every subsequent build.

The application is open source and built from the public GitHub repository. It is
a PS2 homebrew utility for user-controlled local server setup and does not
contain malware, credential collection, persistence, adware, browser
modification, or crypto-mining behavior.

Repository:
https://github.com/NathanNeurotic/PS2-Servers

## Reporting a false positive to your antivirus vendor

If your antivirus flags a PS2 Servers download, you can submit it for
false-positive review. First record the exact SHA-256 of the file you downloaded
(see [Release verification](#release-verification)), then use the detecting
vendor's official submission form. Common ones (links may change over time — if a
link moves, search the vendor's site for "false positive" or "sample
submission"):

| Vendor | False-positive / sample submission |
| --- | --- |
| Microsoft Defender / SmartScreen | https://www.microsoft.com/en-us/wdsi/filesubmission |
| Avast / AVG (Gen) | https://www.avast.com/false-positive-file-form.php |
| Bitdefender | https://www.bitdefender.com/consumer/support/answer/29358/ |
| Kaspersky | https://opentip.kaspersky.com/ |
| ESET | https://support.eset.com/en/kb141 |
| Malwarebytes | https://www.malwarebytes.com/false-positive |
| Any / multi-engine | https://www.virustotal.com (paste the SHA-256 or upload the file) |

When you report — to the vendor or to this project — please include:

- the exact release asset name (e.g. `PS2Servers-windows-x64.zip`);
- the release tag or commit SHA;
- the detecting product name and version;
- the full detection name;
- a VirusTotal or vendor report link if available.

## Release verification

Main automatic releases (built on every push to `main`) include:

- SHA256SUMS.txt
- GitHub artifact attestations (build provenance)
- a portable source ZIP

Tagged releases (`vX.Y.Z`) include a SHA-256 checksum sidecar file
(`<asset>.sha256.txt`) for each asset and GitHub artifact attestations.

Compute and compare a download's hash:

```powershell
# Windows (PowerShell)
Get-FileHash .\PS2Servers-windows-x64.zip -Algorithm SHA256
```

```sh
# Linux / macOS
sha256sum PS2Servers-linux-x64            # or: shasum -a 256 <file>
sha256sum -c SHA256SUMS.txt               # verify against a main release's manifest
```

Verify build provenance for an automatic-release asset with the GitHub CLI:

```sh
gh attestation verify PS2Servers-windows-x64.zip -R NathanNeurotic/PS2-Servers
```

Checksums prove the file downloaded intact; attestations prove it was built by
this repository's GitHub Actions. Neither is a guarantee that a program is
harmless — for that, read the source.

## Build it yourself

You do not have to trust the published binary. Desktop/Core source is Python; Edge is Go. Desktop can be rebuilt with:

```sh
python -m pip install -r requirements-build.txt
python build/build.py                       # single file -> dist/PS2Servers(.exe)
PS2_BUILD_MODE=standalone python build/build.py   # portable build -> dist/ps2servers.dist/
```

The official builds use Python 3.12 and the Nuitka version pinned in
`requirements-build.txt`. Nuitka output is not guaranteed bit-for-bit
reproducible, but the source is the authority: if you can build and run it
yourself, you never have to run a binary you did not produce.

## Build-time toolchain note

On Windows, Nuitka downloads a MinGW-w64 GCC toolchain (and ccache) from
nuitka.net the first time it builds. That is a **build-time** C compiler used to
produce the executable; it is not bundled into, or run by, the shipped app. CI
build logs show this download.

## Code signing

PS2 Servers releases are **unsigned by design**. A trusted Authenticode (Windows)
or Apple Developer ID (macOS) certificate is a recurring paid cost that is out of
scope for a free hobby project, and a *self-signed* certificate provides no
SmartScreen/AV benefit (an unknown signer is still untrusted). Instead of code
signing, the project's provenance comes from **GitHub artifact attestations**
(verifiable with `gh attestation verify`) and published SHA-256 checksums.

## Lowest-trust path

For the lowest-trust path, inspect the source and run from source.
