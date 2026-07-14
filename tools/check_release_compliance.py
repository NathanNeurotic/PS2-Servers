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


def check_server_argv_nuitka_safe():
    """No server argv may contain a bare '-c' or '-m'.

    The packaged launcher re-executes ITSELF ('PS2Servers.exe --serve <key>
    ...') to run a server child, and Nuitka's self-execution guard aborts a
    compiled binary (exit 2) whenever a bare '-c' or '-m' is followed by
    another argument -- it assumes the CPython fork-bomb pattern. Passing
    '-c' for --enable-compression shipped exactly that crash once compression
    became the default, so server argv builders must use long flags for these.
    """
    sys.path.insert(0, str(ROOT))
    from launcher import servers

    errors = []
    for key, server in servers.REGISTRY.items():
        # Synthesize values that switch every flag on so every branch of the
        # argv builder is exercised.
        values = {}
        for field in server.fields:
            if field.kind == "bool":
                values[field.key] = True
            elif field.kind == "port":
                values[field.key] = server.default_port or 1111
            else:
                values[field.key] = "X"
        argv = server.build_argv(values)
        for i, arg in enumerate(argv):
            if arg in ("-c", "-m") and i + 1 < len(argv):
                errors.append(
                    "launcher/servers.py: '{}' argv contains bare '{}' followed by "
                    "'{}' -- Nuitka's self-execution guard aborts the packaged "
                    "binary on this; use the long flag".format(key, arg, argv[i + 1]))
    return errors


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
        "TCP port 1111",
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

    errors += check_server_argv_nuitka_safe()

    if errors:
        print("Release compliance check failed:")
        for error in errors:
            print(" - " + error)
        return 1

    print("Release compliance check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
