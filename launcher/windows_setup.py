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
# Windows Firewall queries are far slower than they look, and almost none of the
# cost is the query. Measured on a 1091-rule Windows 11 box, in a fresh
# powershell.exe, asking about two rules:
#   Get-NetFirewallRule per name (what we used to do)   48.1s cold
#   one wildcard Get-NetFirewallRule                    69.2s cold (batching is WORSE)
#   HNetCfg.FwPolicy2                                   16.8s cold ... 1.2s warm
# The same COM script measured 16.8s, then 48.5s, then 1.2s across one session:
# what actually costs tens of seconds is the firewall service's rule store being
# cold, not walking it. That is why this was intermittent -- the first start after
# a boot paid it and later ones did not -- and why no rewrite of the query fixes
# it. At 30s the cold path timed out, needs_setup fell back to "assume setup is
# needed", the elevated apply blew the same budget, and nothing was ever fixed: an
# un-exitable UAC loop on a machine whose rules were already correct. The check
# runs on a worker thread and only delays a server start, so a budget a slow
# machine cannot blow is worth far more than a tight one.
POWERSHELL_TIMEOUT = 120


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
        port = 445 if values.get("take_445") else _parse_port(values.get("port"), 1111)
        return [("TCP", port, "SMBv1")]
    if key == "udpfs":
        ports = [("UDP", _parse_port(values.get("port"), 0xF5F6), "UDPFS discovery")]
        # The data socket is normally ephemeral and covered by the program-wide
        # "PS2 Servers - App" rule. When the user pins it, allow it by port too so
        # the setting is usable behind manual/port-based firewall rules. Skipped in
        # single-port mode, where the server ignores data_port entirely -- a rule
        # there would open a port nothing ever listens on. Modulo mode implies
        # single-port, so it takes the same branch.
        if not values.get("modulo_mode"):
            data_port = _parse_port(values.get("data_port"), 0)
            if data_port:
                ports.append(("UDP", data_port, "UDPFS data"))
        return ports
    if key == "udpbd":
        return [("UDP", UDPBD_PORT, "UDPBD")]
    if key == "directlink":
        # The direct-link DHCP helper. Rule created only while enabling the
        # mode (never speculatively), removed with every other "PS2 Servers -"
        # rule by remove_setup.
        return [("UDP", 67, "Direct link DHCP")]
    return []


def _rule_names(key, values):
    rules = [
        "PS2 Servers - App"
    ]
    for proto, port, purpose in _server_ports(key, values):
        rules.append("PS2 Servers - {} {} {}".format(purpose, proto, port))
    return rules


def setup_fingerprint(key, values):
    """Everything needs_setup's answer depends on, as one string.

    The check costs tens of seconds because Windows charges by how many rules the
    machine has, not by how many we ask about -- so a caller that has already seen
    a clean answer can skip it while this string is unchanged. It covers the two
    things that can turn a clean answer dirty: the program in the App rule, and the
    per-port rule names (which embed the ports). Anything else that could invalidate
    it -- someone deleting the rules behind our back -- is what 'Allow through
    firewall' is for.
    """
    if not is_windows():
        return ""
    return "|".join([os.path.abspath(_server_program_path())] + _rule_names(key, values))


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


def needs_setup(key, values, log=None):
    """True if an elevated firewall setup pass is needed before starting.

    Query failures deliberately return True: if Windows blocks the check, the
    elevated path is the safer and more useful fallback.

    Exception: when the Windows Firewall service (mpssvc) is not running,
    return False. A stopped firewall does not filter traffic, so allow rules
    are pointless (and cannot be created); users on third-party firewalls or
    with Windows Firewall disabled should not see an elevation prompt on every
    start. The check runs on every start, so if the firewall is enabled later
    the missing rules are detected then.

    ``log``, when given, receives short human-readable strings explaining why
    setup is or is not needed, for the per-server launcher log.

    Important: this check deliberately does not inspect or request Windows SMB1
    optional features. The bundled SMB server speaks the OPL-compatible SMB1/CIFS
    subset itself and should normally run on a custom port.
    """
    if not is_windows():
        return False
    if log is None:
        log = lambda msg: None

    port_rule_names = _rule_names(key, values)[1:]
    rule_array = "@({})".format(",".join(_ps_quote(n) for n in port_rule_names))
    program = _ps_quote(os.path.abspath(_server_program_path()))
    app_rule = _ps_quote("PS2 Servers - App")
    # COM, looking each rule up by name, rather than Get-NetFirewallRule per name.
    # Same answers -- verified against a missing app rule, a missing port rule, a
    # stale program path, and both missing.
    #
    # Rules.Item() raises when a name is absent, so absence is the catch, not a
    # return value. It also returns only one rule per name where iterating saw
    # every match; apply_setup removes by name before creating, so duplicates do
    # not accumulate.
    script = "\n".join([
        "$svc = Get-Service -Name mpssvc -ErrorAction SilentlyContinue",
        "if ($svc -and $svc.Status -ne 'Running') {",
        "  Write-Output 'FIREWALL_OFF'",
        "} else {",
        "$appRuleName = {}".format(app_rule),
        "$appProgram = {}".format(program),
        "$wanted = {}".format(rule_array),
        "$fw = New-Object -ComObject HNetCfg.FwPolicy2",
        "$appFound = $null",
        "try { $appFound = $fw.Rules.Item($appRuleName) } catch {}",
        "if (-not $appFound) {",
        "  Write-Output ('NEED_RULE=' + $appRuleName)",
        "} elseif ($appFound.ApplicationName -ne $appProgram) {",
        "  Write-Output ('NEED_RULE=' + $appRuleName)",
        "}",
        "foreach ($name in $wanted) {",
        "  $found = $null",
        "  try { $found = $fw.Rules.Item($name) } catch {}",
        "  if (-not $found) { Write-Output ('NEED_RULE=' + $name) }",
        "}",
        "}",
    ])
    try:
        res = _powershell(script)
    except subprocess.TimeoutExpired:
        # Explicitly NOT "assume setup is needed". Elevating only helps if the
        # elevated pass can then succeed, and it cannot: apply_setup drives the
        # same Windows Firewall cmdlets that just ran out of time, so it blows the
        # same budget and changes nothing. Answering True here is what turned a
        # slow machine into an un-exitable UAC loop -- prompt, fail, prompt --
        # while its rules were already correct. Start instead, and say so: the
        # server is far more likely to work than not, and "Allow through firewall"
        # is one click away if it does not. Other failures below still elevate;
        # only a timeout is known to be unhelped by it.
        log("firewall check timed out after {}s; starting anyway. If the PS2 "
            "cannot see this server, click 'Allow through firewall'.".format(
                POWERSHELL_TIMEOUT))
        return False
    except OSError as e:
        log("firewall check could not run ({}); assuming setup is needed".format(e))
        return True
    if res.returncode != 0:
        log("firewall check failed; assuming setup is needed: {}".format(
            ((res.stderr or "") + (res.stdout or "")).strip() or "unknown error"))
        return True
    out = (res.stdout or "").strip()
    if "FIREWALL_OFF" in out:
        log("Windows Firewall service is not running; skipping firewall setup")
        return False
    if out:
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("NEED_RULE="):
                log("missing firewall rule: {}".format(line[len("NEED_RULE="):]))
        return True
    return False


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
    elif key == "directlink":
        parts.append("set a fixed address on the chosen network port and allow "
                     "the direct-link DHCP helper (UDP 67) through the firewall")
    else:
        ports = _server_ports(key, values)
        if ports:
            parts.append("create Windows Firewall allow rules")
    if not parts:
        return "apply Windows network setup"
    return " and ".join(parts)
