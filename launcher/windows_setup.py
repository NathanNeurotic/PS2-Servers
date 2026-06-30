"""Windows setup helpers for the GUI launcher.

The launcher uses these helpers to keep end users out of Windows Firewall
screens. Normal PS2 Servers SMB mode uses the repo's own small SMB/CIFS server
on a custom TCP port and does not enable or depend on the Windows SMB1 optional
feature tree.
"""

import os
import platform
import subprocess
import sys

from .servers import frozen_self_exe, is_frozen

UDPBD_PORT = 0xBDBD
FIREWALL_RULE_PREFIX = "PS2 Servers - "
POWERSHELL_TIMEOUT = 30


class WindowsSetupError(RuntimeError):
    """Raised when Windows setup cannot be checked, applied, or removed."""


def is_windows():
    return platform.system() == "Windows"


def _hidden_subprocess_kwargs():
    """Windows subprocess flags that prevent console-window flashes."""
    if not is_windows():
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
        "startupinfo": startupinfo,
    }


def _powershell_executable():
    """Absolute path to the system Windows PowerShell.

    Resolving the bare name ``powershell`` via PATH/CWD could run an
    attacker-placed ``powershell.*`` from the current directory or an earlier
    PATH entry (binary hijacking). Prefer the fixed system location; fall back to
    the bare name only if that file is somehow absent. The packaged app is x64,
    so System32 is not WOW64-redirected.
    """
    system_root = os.environ.get("SystemRoot") or os.environ.get("windir") or r"C:\Windows"
    candidate = os.path.join(
        system_root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe"
    )
    return candidate if os.path.isfile(candidate) else "powershell"


def _powershell(script):
    """Run a small hidden PowerShell script and return CompletedProcess.

    We deliberately do NOT pass ``-ExecutionPolicy Bypass``: these are inline
    ``-Command`` scripts, not ``.ps1`` files, so script-execution policy never
    applies to them. Omitting the flag keeps the invocation off the
    "hidden window + ExecutionPolicy Bypass" pattern that antivirus/EDR engines
    weight heavily, with no change in behavior. The interpreter is resolved by
    absolute path to avoid PATH/CWD hijacking.
    """
    return subprocess.run(
        [
            _powershell_executable(),
            "-NoProfile",
            "-NonInteractive",
            "-WindowStyle", "Hidden",
            "-Command", script,
        ],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=POWERSHELL_TIMEOUT,
        **_hidden_subprocess_kwargs(),
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


def _firewall_rules(key, values):
    program = os.path.abspath(_server_program_path())
    rules = [{
        "name": "PS2 Servers - App",
        "protocol": "Any",
        "port": None,
        "program": program,
    }]
    for proto, port, purpose in _server_ports(key, values):
        rules.append({
            "name": "PS2 Servers - {} {} {}".format(purpose, proto, port),
            "protocol": proto,
            "port": int(port),
            "program": None,
        })
    return rules


def needs_setup(key, values):
    """True if an elevated firewall setup pass is needed before starting.

    Query failures deliberately return True: if Windows blocks the check, the
    elevated path is the safer and more useful fallback.

    Important: this check deliberately does not inspect or request Windows SMB1
    optional features. The bundled SMB server speaks the OPL-compatible SMB1/CIFS
    subset itself and should normally run on a custom port.
    """
    if not is_windows():
        return False

    port_rule_names = _rule_names(key, values)[1:]
    rule_array = "@({})".format(",".join(_ps_quote(n) for n in port_rule_names))
    program = _ps_quote(os.path.abspath(_server_program_path()))
    app_rule = _ps_quote("PS2 Servers - App")
    script = "\n".join([
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
    try:
        res = _powershell(script)
    except (OSError, subprocess.TimeoutExpired):
        return True
    if res.returncode != 0:
        return True
    return bool((res.stdout or "").strip())


def apply_setup(key, values):
    """Create required Windows Firewall allow rules.

    Returns a dict:
      changed: whether anything was changed
      restart_needed: always False; firewall-only setup does not require reboot
      output: human-readable setup log

    This function does not enable Windows SMB1 optional features.
    """
    if not is_windows():
        return {"changed": False, "restart_needed": False, "output": ""}

    rule_lines = []
    for rule in _firewall_rules(key, values):
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
    ] + rule_lines + [
        "Write-Output ('SETUP_CHANGED=' + $changed)",
        "Write-Output 'NOTE=No Windows SMB1 optional features were enabled or changed.'",
    ])

    try:
        res = _powershell(script)
    except subprocess.TimeoutExpired as e:
        raise WindowsSetupError("Windows setup timed out after {} seconds.".format(
            POWERSHELL_TIMEOUT)) from e
    except OSError as e:
        raise WindowsSetupError("Failed to run PowerShell: {}".format(e)) from e

    output = ((res.stdout or "") + (res.stderr or "")).strip()
    if res.returncode != 0:
        raise WindowsSetupError(output or "Windows setup failed.")

    upper = output.upper()
    return {
        "changed": "SETUP_CHANGED=TRUE" in upper,
        "restart_needed": False,
        "output": output,
    }


def remove_setup():
    """Remove PS2 Servers Windows Firewall rules.

    This is intentionally narrow: it removes only rules whose display names start
    with "PS2 Servers - ". It does not touch Windows optional features or other
    firewall rules.
    """
    if not is_windows():
        return {"changed": False, "restart_needed": False, "output": ""}

    prefix = _ps_quote(FIREWALL_RULE_PREFIX + "*")
    script = "\n".join([
        "$ErrorActionPreference = 'Stop'",
        "$rules = @(Get-NetFirewallRule -DisplayName {} -ErrorAction SilentlyContinue)".format(prefix),
        "if ($rules.Count -eq 0) {",
        "  Write-Output 'No PS2 Servers firewall rules found.'",
        "  Write-Output 'SETUP_CHANGED=False'",
        "} else {",
        "  foreach ($rule in $rules) { Write-Output ('Removing firewall rule: ' + $rule.DisplayName) }",
        "  $rules | Remove-NetFirewallRule -ErrorAction Stop",
        "  Write-Output ('Removed PS2 Servers firewall rules: ' + $rules.Count)",
        "  Write-Output 'SETUP_CHANGED=True'",
        "}",
    ])

    try:
        res = _powershell(script)
    except subprocess.TimeoutExpired as e:
        raise WindowsSetupError("Windows cleanup timed out after {} seconds.".format(
            POWERSHELL_TIMEOUT)) from e
    except OSError as e:
        raise WindowsSetupError("Failed to run PowerShell: {}".format(e)) from e

    output = ((res.stdout or "") + (res.stderr or "")).strip()
    if res.returncode != 0:
        raise WindowsSetupError(output or "Windows cleanup failed.")

    return {
        "changed": "SETUP_CHANGED=TRUE" in output.upper(),
        "restart_needed": False,
        "output": output,
    }


def setup_summary(key, values):
    if not is_windows():
        return ""
    parts = []
    if key == "smbv1":
        parts.append("create Windows Firewall allow rules for the built-in PS2 Servers SMB server")
        if values.get("take_445"):
            parts.append("temporarily use TCP 445 by pausing Windows file sharing while the server runs")
    else:
        ports = _server_ports(key, values)
        if ports:
            parts.append("create Windows Firewall allow rules")
    if not parts:
        return "apply Windows network setup"
    return " and ".join(parts)
