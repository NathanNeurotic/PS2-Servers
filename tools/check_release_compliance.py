#!/usr/bin/env python3
"""Check release transparency and AV false-positive hygiene requirements."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def require(rel, *needles):
    text = read(rel)
    missing = [needle for needle in needles if needle not in text]
    if missing:
        return ["{} missing: {}".format(rel, needle) for needle in missing]
    return []


def require_absent(rel, *needles):
    text = read(rel)
    present = [needle for needle in needles if needle in text]
    if present:
        return ["{} must not contain: {}".format(rel, needle) for needle in present]
    return []


def main():
    errors = []

    errors += require(
        "launcher/release_metadata.py",
        'APP_NAME = "PS2 Servers"',
        'EXECUTABLE_BASENAME = "PS2Servers"',
        'WINDOWS_EXE_NAME = EXECUTABLE_BASENAME + ".exe"',
        'WINDOWS_PACKAGE_NAME = EXECUTABLE_BASENAME + "-windows-x64.zip"',
        'COMPANY_NAME = "NathanNeurotic"',
        "FILE_DESCRIPTION",
        "Avast/Gen Threat Labs",
        "does not contain malware, credential collection, persistence, adware, browser modification, or crypto-mining behavior",
    )

    errors += require(
        "build/build.py",
        "--company-name=",
        "--product-name=",
        "--product-version=",
        "--file-version=",
        "--file-description=",
        "--copyright=",
        "release_metadata",
    )

    errors += require(
        ".github/workflows/release-on-main.yml",
        "PS2Servers-windows-x64.zip",
        "SHA256SUMS.txt",
        "GitHub artifact attestations",
        "does not enable Windows SMB1",
        "does not enable or disable Windows optional features",
        "Windows Firewall changes happen only after user action/consent",
        "tools/remove-windows-firewall-rules.ps1",
        "Avast/Gen Threat Labs",
        "does not contain malware, credential collection, persistence, adware, browser modification, or crypto-mining behavior",
    )

    errors += require(
        ".github/workflows/release.yml",
        "PS2Servers-windows-x64.zip",
        ".sha256.txt",
        "does not enable Windows SMB1",
        "does not enable or disable Windows optional features",
        "Windows Firewall changes happen only after user action/consent",
        "tools/remove-windows-firewall-rules.ps1",
        "Avast/Gen Threat Labs",
        "does not contain malware, credential collection, persistence, adware, browser modification, or crypto-mining behavior",
    )

    errors += require(
        "README.md",
        "PS2Servers-windows-x64.zip",
        "Windows' built-in SMB1 optional",
        "Windows Firewall changes are limited to rules named `PS2 Servers - ...`",
        "docs/antivirus-transparency.md",
        "SHA256SUMS.txt",
        "artifact attestations",
    )

    errors += require(
        "SECURITY.md",
        "Firewall changes require user action and consent",
        "does not silently add,",
        "remove, or modify firewall rules on launch",
        "Avast/Gen Threat Labs",
        "contain malware, credential collection, persistence, adware, browser",
    )

    errors += require(
        "docs/antivirus-transparency.md",
        "Vendor / company name: NathanNeurotic",
        "Windows executable: PS2Servers.exe",
        "Windows release package: PS2Servers-windows-x64.zip",
        "SMBv1/RiptOPL",
        "TCP port 1445",
        "UDP port 0xF5F6",
        "UDP port 0xBDBD",
        "does not enable Windows SMB1",
        "does not install or remove Windows optional features",
        "PS2 Servers has been submitted to Avast/Gen Threat Labs for false-positive review.",
        "contain malware, credential collection, persistence, adware, browser",
        "modification, or crypto-mining behavior",
    )

    # smbserver_opl.py is included because the real port-445 / LanmanServer logic
    # lives there: it must never enable Windows SMB1 or any optional feature.
    for rel in (
        "launcher/windows_setup.py",
        "launcher/gui.py",
        "smbv1_server/smbserver_opl.py",
    ):
        errors += require_absent(
            rel,
            "Enable-WindowsOptionalFeature",
            "Disable-WindowsOptionalFeature",
            "Enable-WindowsFeature",
            "Disable-WindowsFeature",
            "SMB1Protocol",
        )

    if errors:
        print("Release compliance check failed:")
        for error in errors:
            print(" - " + error)
        return 1

    print("Release compliance check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
