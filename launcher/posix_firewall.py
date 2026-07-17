"""Best-effort firewall guidance for Linux/macOS -- no root, no automation.

Windows gets real allow-rule management (windows_setup.py). On Unix there is no
single firewall API and the launcher is not root, so instead of silently
succeeding while a default-deny firewall drops the console's packets, we tell
the user the exact command to open the ports. Fedora / RHEL / openSUSE ship
firewalld active by default and Ubuntu users who ran `ufw enable` are in the
same boat, so a blocked port is a real failure mode -- and one that otherwise
looks like "PS2 Servers is broken on Linux" because the server itself starts
fine and the packets just vanish.

Detection is deliberately cheap and root-free: `shutil.which` for the tool, and
`systemctl is-active` (which needs no privileges) to tell an ACTIVE firewall
from a merely-installed one. Everything degrades to a generic hint.
"""

import shutil
import subprocess


_TOOLS = (
    # (binary, systemd unit, command template). Order = preference.
    ("ufw", "ufw", "sudo ufw allow {port}/{proto}"),
    ("firewall-cmd", "firewalld",
     "sudo firewall-cmd --add-port={port}/{proto}"
     "  (add --permanent to keep it after a reboot)"),
)


def _unit_is_active(unit):
    """True only if systemctl reports the unit active. Never raises."""
    systemctl = shutil.which("systemctl")
    if not systemctl:
        return False
    try:
        res = subprocess.run(
            [systemctl, "is-active", unit],
            capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return False
    return res.stdout.strip() == "active"


def detect_firewall_tools(probe_active=True):
    """[(binary, template, active_bool), ...] for the firewall tools present.

    probe_active runs one short `systemctl is-active` per tool; pass False to
    skip the subprocess entirely (e.g. on a UI thread that must not block).
    """
    found = []
    for binary, unit, template in _TOOLS:
        if not shutil.which(binary):
            continue
        active = _unit_is_active(unit) if probe_active else False
        found.append((binary, template, active))
    return found


def firewall_hint_lines(ports, probe_active=True):
    """Terminal lines telling a Unix user how to open `ports`, or [].

    `ports` is the [(proto, port, purpose), ...] list the server already builds
    for its Windows firewall rules -- reused verbatim so the two never drift.
    """
    if not ports:
        return []
    tools = detect_firewall_tools(probe_active=probe_active)
    active = [t for t in tools if t[2]]

    if active:
        lead = ("Your {} firewall is active -- if the PS2 cannot see this "
                "server, allow these ports:".format(active[0][0]))
    else:
        lead = ("If the PS2 cannot see this server, allow these ports through "
                "your firewall:")
    lines = [lead]
    for proto, port, purpose in ports:
        lines.append("  {}: {} {}".format(purpose, proto, port))

    if tools:
        for proto, port, _purpose in ports:
            for binary, template, _act in tools:
                lines.append("    " + template.format(
                    port=port, proto=proto.lower()))
    else:
        lines.append("  (open them in whatever firewall your distro uses; most "
                     "desktop setups already allow LAN traffic)")
    return lines
