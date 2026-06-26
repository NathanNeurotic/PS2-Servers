"""Windows setup helpers for the GUI launcher.

The GUI uses these helpers to keep end users out of PowerShell, Windows Features,
and Windows Firewall screens. All mutating actions require an elevated launcher;
the unelevated launcher only checks whether setup is already complete.
"""

import os
import platform
import subprocess
import sys

from .servers import frozen_self_exe, is_frozen


SMB1_FEATURES = (
    "SMB1Protocol",
    "SMB1Protocol-Client",
    "SMB1Protocol-Server",
)
SMB1_REMOVAL_FEATURE = "SMB1Protocol-Deprecation"

UDPBD_PORT = 0xBDBD


class WindowsSetupError(RuntimeError):
    """Raised when Windows setup cannot be checked or applied."""


def is_windows():
    return platform.system() == "Windows"


def _powershell(script):
    """Run a small PowerShell script and return CompletedProcess."""
    return subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-Command", script,
        ],
        capture_output=True,
        text=True,
    )


def _ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def _server_program_path():
    """Path to allow through Windows Firewall.

    Packaged builds must allow the original onefile executable, not Nuitka's
    temporary extracted inner exe. Source runs allow python.exe because the
    server child is also python.exe.
    """
    if is_frozen():
        return frozen_self_exe()
    return sys.executable


def _parse_port(value, default):
    if value in ("", None, False):
        return int(default)
    try:
        return int(str(value), 0)
    except (TypeError, ValueError):
        return int(default)


def _server_ports(key, values):
    """Return [(protocol, port, purpose), ...] for fixed inbound ports."""
    if key == "smbv1":
        port = 445 if values.get("take_445") else _parse_port(values.get("port"), 1445)
        return [("TCP", port, "SMBv1")]
    if key == "udpfs":
        return [("UDP", _parse_port(values.get("port"), 0xF5F6), "UDPFS discovery")]
    if key == "udpbd":
        return [("UDP", UDPBD_PORT, "UDPBD")]
    return []


def _rule_names(key, values):
    rules = [
        "PS2 Servers - App"
    ]
    for proto, port, purpose in _server_ports(key, values):
        rules.append("PS2 Servers - {} {} {}".format(purpose, proto, port))
    return rules


def needs_setup(key, values):
    """True if an elevated setup pass is needed before starting this server.

    Query failures deliberately return True: if Windows blocks the check, the
    elevated path is the safer and more useful fallback.
    """
    if not is_windows():
        return False

    feature_lines = []
    if key == "smbv1":
        feature_array = "@({})".format(",".join(_ps_quote(f) for f in SMB1_FEATURES))
        removal = _ps_quote(SMB1_REMOVAL_FEATURE)
        feature_lines = [
            "$features = {}".format(feature_array),
            "foreach ($name in $features) {",
            "  $f = Get-WindowsOptionalFeature -Online -FeatureName $name -ErrorAction SilentlyContinue",
            "  if ($null -ne $f -and $f.State -ne 'Enabled') { Write-Output ('NEED_FEATURE=' + $name) }",
            "}",
            "$d = Get-WindowsOptionalFeature -Online -FeatureName {} -ErrorAction SilentlyContinue".format(removal),
            "if ($null -ne $d -and $d.State -eq 'Enabled') { Write-Output ('NEED_DISABLE=' + {}) }".format(removal),
        ]

    port_rule_names = _rule_names(key, values)[1:]
    rule_array = "@({})".format(",".join(_ps_quote(n) for n in port_rule_names))
    program = _ps_quote(os.path.abspath(_server_program_path()))
    app_rule = _ps_quote("PS2 Servers - App")
    script = "\n".join(feature_lines + [
        "$appRuleName = {}".format(app_rule),
        "$appProgram = {}".format(program),
        "$appRule = Get-NetFirewallRule -DisplayName $appRuleName -ErrorAction SilentlyContinue",
        "if (-not $appRule) {",
        "  Write-Output ('NEED_RULE=' + $appRuleName)",
        "} else {",
        "  $appFilter = $appRule | Get-NetFirewallApplicationFilter",
        "  $programs = @($appFilter | ForEach-Object { $_.Program })",
        "  if ($programs -notcontains $appProgram) { Write-Output ('NEED_RULE=' + $appRuleName) }",
        "}",
        "$rules = {}".format(rule_array),
        "foreach ($name in $rules) {",
        "  if (-not (Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue)) {",
        "    Write-Output ('NEED_RULE=' + $name)",
        "  }",
        "}",
    ])
    res = _powershell(script)
    if res.returncode != 0:
        return True
    return bool((res.stdout or "").strip())


def apply_setup(key, values):
    """Enable required Windows features and firewall rules.

    Returns a dict:
      changed: whether anything was changed
      restart_needed: Windows reported a pending restart for an optional feature
      output: human-readable setup log
    """
    if not is_windows():
        return {"changed": False, "restart_needed": False, "output": ""}

    program = os.path.abspath(_server_program_path())
    rules = []
    rules.append({
        "name": "PS2 Servers - App",
        "protocol": "Any",
        "port": None,
        "program": program,
    })
    for proto, port, purpose in _server_ports(key, values):
        rules.append({
            "name": "PS2 Servers - {} {} {}".format(purpose, proto, port),
            "protocol": proto,
            "port": int(port),
            "program": None,
        })

    feature_script = []
    if key == "smbv1":
        feature_array = "@({})".format(",".join(_ps_quote(f) for f in SMB1_FEATURES))
        removal = _ps_quote(SMB1_REMOVAL_FEATURE)
        feature_script = [
            "$features = {}".format(feature_array),
            "foreach ($name in $features) {",
            "  $f = Get-WindowsOptionalFeature -Online -FeatureName $name -ErrorAction SilentlyContinue",
            "  if ($null -eq $f) {",
            "    Write-Output ('SMB1 missing optional feature: ' + $name)",
            "    continue",
            "  }",
            "  if ($f.State -ne 'Enabled') {",
            "    Write-Output ('Enabling Windows optional feature: ' + $name)",
            "    $r = Enable-WindowsOptionalFeature -Online -FeatureName $name -All -NoRestart -ErrorAction Stop",
            "    $changed = $true",
            "    if ($r.RestartNeeded) { $restartNeeded = $true }",
            "  } else {",
            "    Write-Output ('Windows optional feature already enabled: ' + $name)",
            "  }",
            "}",
            "$d = Get-WindowsOptionalFeature -Online -FeatureName {} -ErrorAction SilentlyContinue".format(removal),
            "if ($null -ne $d -and $d.State -eq 'Enabled') {",
            "  Write-Output ('Disabling SMB1 automatic removal: ' + {})".format(removal),
            "  $r = Disable-WindowsOptionalFeature -Online -FeatureName {} -NoRestart -ErrorAction Stop".format(removal),
            "  $changed = $true",
            "  if ($r.RestartNeeded) { $restartNeeded = $true }",
            "}",
        ]

    rule_lines = []
    for rule in rules:
        name = _ps_quote(rule["name"])
        proto = _ps_quote(rule["protocol"])
        rule_lines += [
            "$ruleName = {}".format(name),
            "$existing = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue",
            "if ($existing) {",
            "  Write-Output ('Refreshing firewall rule: ' + $ruleName)",
            "  Remove-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue",
            "} else {",
            "  Write-Output ('Creating firewall rule: ' + $ruleName)",
            "}",
        ]
        if rule["program"]:
            rule_lines.append(
                "New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow "
                "-Profile Any -Protocol Any -Program {} -ErrorAction Stop | Out-Null".format(
                    _ps_quote(rule["program"]))
            )
        else:
            rule_lines.append(
                "New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow "
                "-Profile Any -Protocol {} -LocalPort {} -ErrorAction Stop | Out-Null".format(
                    proto, int(rule["port"]))
            )
        rule_lines.append("$changed = $true")

    script = "\n".join([
        "$ErrorActionPreference = 'Stop'",
        "$changed = $false",
        "$restartNeeded = $false",
    ] + feature_script + rule_lines + [
        "Write-Output ('SETUP_CHANGED=' + $changed)",
        "Write-Output ('RESTART_NEEDED=' + $restartNeeded)",
    ])

    res = _powershell(script)
    output = ((res.stdout or "") + (res.stderr or "")).strip()
    if res.returncode != 0:
        raise WindowsSetupError(output or "Windows setup failed.")

    upper = output.upper()
    return {
        "changed": "SETUP_CHANGED=TRUE" in upper,
        "restart_needed": "RESTART_NEEDED=TRUE" in upper,
        "output": output,
    }


def setup_summary(key, values):
    if not is_windows():
        return ""
    parts = []
    if key == "smbv1":
        parts.append("enable the Windows SMB 1.0/CIFS feature tree")
    ports = _server_ports(key, values)
    if ports:
        parts.append("create Windows Firewall allow rules")
    if not parts:
        return "apply Windows network setup"
    return " and ".join(parts)
