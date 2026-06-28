# Antivirus Transparency

PS2 Servers is an open-source PS2 homebrew utility for user-controlled local
server setup. It starts LAN servers so Open PS2 Loader can load files from a PC.

## Vendor and release identity

- Vendor / company name: NathanNeurotic
- Product name: PS2 Servers
- Windows executable: PS2Servers.exe
- Windows release package: PS2Servers-windows-x64.zip
- Repository: https://github.com/NathanNeurotic/PS2-Servers

Packaged Windows builds include version metadata: company name, product name,
file description, product version, file version, and copyright.

## Network behavior

PS2 Servers only exposes local server behavior that the user chooses from the
GUI:

- SMBv1/RiptOPL: built-in SMB/CIFS subset, normally TCP port 1445.
- UDPFS: UDP file/block serving, normally UDP port 0xF5F6.
- UDPBD: UDP block-device serving, normally UDP port 0xBDBD.

The Windows SMB server uses PS2 Servers' own SMB/CIFS implementation. It does not enable Windows SMB1, does not disable Windows SMB1 automatic removal, and does not install or remove Windows optional features.

Windows Firewall changes require user action and consent. Rules created by the
app use the prefix `PS2 Servers - ...` and can be removed from the GUI or with:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\remove-windows-firewall-rules.ps1
```

## False-positive review note

PS2 Servers has been submitted to Avast/Gen Threat Labs for false-positive review.

The application is open source and built from the public GitHub repository. It is
a PS2 homebrew utility for user-controlled local server setup and does not
contain malware, credential collection, persistence, adware, browser
modification, or crypto-mining behavior.

Repository:
https://github.com/NathanNeurotic/PS2-Servers

## Release verification

Main automatic releases include:

- SHA256SUMS.txt
- GitHub artifact attestations
- a portable source ZIP

Tagged releases include SHA-256 checksum sidecar files for each asset.

For the lowest-trust path, inspect the source and run from source.
