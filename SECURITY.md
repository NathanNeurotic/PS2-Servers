# Security Policy

## Project security posture

PS2 Servers is an open-source local-network tool for PlayStation 2 homebrew use.
It starts small LAN server processes so Open PS2 Loader and compatible forks can
load files from a PC.

The packaged Windows executable is unsigned. Antivirus products may flag unsigned
network tools heuristically, especially when they open local server ports or ask
for Windows Firewall rules.

## Windows SMB behavior

The SMBv1/RiptOPL server does **not** enable or depend on Windows' built-in SMB1
optional feature tree.

Normal SMB mode uses PS2 Servers' own small SMB/CIFS implementation and listens
on a custom TCP port, normally `1445`. OPL connects to this program directly.
Windows file sharing does not need to expose SMB1.

The advanced "Take port 445" option is different:

- it is optional;
- it requires administrator rights;
- it temporarily pauses Windows File Sharing / `LanmanServer` while the PS2
  Servers SMB server is running;
- it does not enable Windows SMB1;
- it does not permanently disable Windows file sharing.

## Windows Firewall behavior

When needed, the launcher may ask for administrator rights to create inbound
Windows Firewall allow rules named with the prefix:

```text
PS2 Servers -
```

Manual cleanup from an elevated PowerShell prompt:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\remove-windows-firewall-rules.ps1
```

or:

```powershell
Get-NetFirewallRule -DisplayName "PS2 Servers - *" -ErrorAction SilentlyContinue |
  Remove-NetFirewallRule
```

## Release verification

Release assets are built by GitHub Actions from this public repository. Releases
include:

- packaged Windows/Linux/macOS assets;
- a portable source ZIP;
- `SHA256SUMS.txt` for release asset checksums;
- GitHub artifact attestations for build provenance.

GitHub artifact attestations can be verified with the GitHub CLI. Example:

```sh
gh attestation verify PS2Servers-windows-x64.exe -R NathanNeurotic/PS2-Servers
```

Checksums verify file integrity, and attestations verify build provenance. They do
not prove that a program is harmless. Users who want the lowest-trust path should
inspect the source and run from source instead of using the unsigned packaged EXE.

## Reporting a security issue

Please open a GitHub issue if the report can be public.

For malware or antivirus false-positive reports, include:

- the exact release asset name;
- the release tag or commit SHA;
- the detecting product name and version;
- the full detection name;
- a VirusTotal or vendor report link if available.

Do not upload third-party private samples or user data to the issue tracker.
