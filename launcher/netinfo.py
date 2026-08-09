"""LAN IP detection.

OPL needs the host's LAN IP typed into its network settings, so the launcher
surfaces a best-guess address. We deliberately prefer real private-LAN ranges
(192.168/x, 10/x, 172.16-31/x) over VPN / WSL / link-local addresses, matching
the warning the SMBv1 server already prints ("usually 192.168.x.x -- NOT a
VPN/WSL address").
"""

import shutil
import socket
import subprocess
import sys


# Interfaces the PS2 is never reachable on: containers, bridges, VPN tunnels,
# VM host-only nets. Matched as name prefixes.
_VIRTUAL_IFACE_PREFIXES = (
    "lo", "docker", "veth", "virbr", "br-", "tun", "tap", "vmnet", "vboxnet",
    "zt", "wg", "utun", "llw", "awdl", "bridge", "gif", "stf", "ap",
)


def _iface_is_virtual(name):
    return name.startswith(_VIRTUAL_IFACE_PREFIXES)


def _parse_linux_ip_addr(text):
    """IPv4s from `ip -o -4 addr show`, skipping virtual interfaces.

    A line looks like: '3: enp3s0    inet 192.168.1.50/24 brd ... scope global'
    """
    ips = []
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[2] != "inet":
            continue
        if _iface_is_virtual(parts[1]):
            continue
        ips.append(parts[3].split("/")[0])
    return ips


def _parse_macos_ifconfig(text):
    """IPv4s from `ifconfig`, skipping virtual interfaces.

    Interface headers start in column 0 ('en0: flags=...'); their addresses
    follow on indented 'inet <ip> netmask ...' lines.
    """
    ips = []
    current = None
    for line in (text or "").splitlines():
        if line and not line[0].isspace():
            current = line.split(":", 1)[0]
            continue
        stripped = line.strip()
        if stripped.startswith("inet ") and current and not _iface_is_virtual(current):
            ips.append(stripped.split()[1])
    return ips


def _posix_extra_ipv4():
    """Interface IPv4s that getaddrinfo(hostname) misses on Unix.

    On Debian/Ubuntu the hostname resolves to 127.0.1.1, so getaddrinfo returns
    only loopback and the machine's real NICs never reach the LAN-IP dropdown --
    a multi-NIC, NAS, or VPN user is then left to type the address by hand. Read
    the interfaces directly instead. Best-effort: any failure yields nothing.
    """
    try:
        if sys.platform.startswith("linux") and shutil.which("ip"):
            out = subprocess.run(["ip", "-o", "-4", "addr", "show"],
                                 capture_output=True, text=True, timeout=3).stdout
            return _parse_linux_ip_addr(out)
        if sys.platform == "darwin" and shutil.which("ifconfig"):
            out = subprocess.run(["ifconfig"],
                                 capture_output=True, text=True, timeout=3).stdout
            return _parse_macos_ifconfig(out)
    except (OSError, subprocess.SubprocessError):
        pass
    return []


def primary_ip():
    """Best outbound-interface IPv4 via the UDP-connect trick (sends no packets)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def _is_private(ip):
    if ip.startswith("192.168.") or ip.startswith("10."):
        return True
    if ip.startswith("172."):
        try:
            return 16 <= int(ip.split(".")[1]) <= 31
        except (IndexError, ValueError):
            return False
    return False


def all_ipv4():
    """All non-loopback, non-link-local IPv4 addresses on this host, sorted."""
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    ips.add(primary_ip())
    # getaddrinfo(hostname) under-reports on Unix (Debian/Ubuntu point the
    # hostname at 127.0.1.1); read the interfaces directly to fill the dropdown.
    ips.update(_posix_extra_ipv4())
    return sorted(ip for ip in ips if not ip.startswith(("127.", "169.254.")))


def _lan_rank(ip):
    # Lower is better. Prefer common home-LAN ranges; 172.16-31 is frequently a
    # virtual adapter (WSL / Docker / Hyper-V), so rank it last among privates.
    if ip.startswith("192.168."):
        return 0
    if ip.startswith("10."):
        return 1
    if _is_private(ip):
        return 2
    return 3


def ip_choices():
    """All detected IPv4 addresses plus a Custom IP choice."""
    return all_ipv4() + ["Custom IP..."]


def get_all_adapters_info():
    """Returns (active_pairs, unconfigured_adapters) for system network interfaces.

    active_pairs is [(adapter_name, ip_address), ...] for active IPv4 interfaces.
    unconfigured_adapters is [adapter_name, ...] for physical NICs without a valid LAN IPv4.
    """
    pairs = []
    unconfigured = set()
    try:
        if sys.platform == "win32" and shutil.which("ipconfig"):
            out = subprocess.run(["ipconfig"], capture_output=True, text=True, timeout=3).stdout
            current_adapter = None
            has_valid_ip = False
            for line in (out or "").splitlines():
                line_s = line.strip()
                if line and not line[0].isspace() and ("adapter" in line.lower() or ":" in line):
                    if current_adapter and not has_valid_ip and not _iface_is_virtual(current_adapter.lower()):
                        unconfigured.add(current_adapter)
                    current_adapter = line.split(":", 1)[0].strip()
                    has_valid_ip = False
                    continue
                if "IPv4 Address" in line_s or "IP Address" in line_s:
                    parts = line_s.split(":", 1)
                    if len(parts) == 2:
                        ip = parts[1].replace("(Preferred)", "").strip()
                        if ip and not ip.startswith("127."):
                            if not ip.startswith("169.254."):
                                name = current_adapter or "Network Adapter"
                                pairs.append((name, ip))
                                has_valid_ip = True
            if current_adapter and not has_valid_ip and not _iface_is_virtual(current_adapter.lower()):
                unconfigured.add(current_adapter)
            return pairs, sorted(unconfigured)
        elif sys.platform.startswith("linux") and shutil.which("ip"):
            out = subprocess.run(["ip", "-o", "-4", "addr", "show"],
                                 capture_output=True, text=True, timeout=3).stdout
            for line in (out or "").splitlines():
                parts = line.split()
                if len(parts) >= 4 and parts[2] == "inet":
                    iface = parts[1]
                    if not _iface_is_virtual(iface):
                        ip = parts[3].split("/")[0]
                        if not ip.startswith(("127.", "169.254.")):
                            pairs.append((iface, ip))
            if pairs:
                return pairs, []
        elif sys.platform == "darwin" and shutil.which("ifconfig"):
            out = subprocess.run(["ifconfig"],
                                 capture_output=True, text=True, timeout=3).stdout
            current = None
            for line in (out or "").splitlines():
                if line and not line[0].isspace():
                    current = line.split(":", 1)[0]
                    continue
                stripped = line.strip()
                if stripped.startswith("inet ") and current and not _iface_is_virtual(current):
                    ip = stripped.split()[1]
                    if not ip.startswith(("127.", "169.254.")):
                        pairs.append((current, ip))
            if pairs:
                return pairs, []
    except Exception:
        pass

    return [("Network Adapter", ip) for ip in all_ipv4()], []


def detailed_ip_info():
    """Returns a formatted multi-line string listing current network adapters and IP addresses."""
    pairs, unconfigured = get_all_adapters_info()
    lines = ["[network] Current IP Addresses and Network Adapters:"]
    if not pairs:
        lines.append("  • No active network adapters found.")
    else:
        for adapter, ip in pairs:
            lines.append(f"  • {adapter}: {ip}")
    lines.append("(If your PS2 is connected to a specific network device, select its IP in the dropdown above or pick 'Custom IP...' to type your own.)")
    if unconfigured:
        lines.append("\n[network] Unconfigured / Direct Cable Adapters (No IPv4 assigned):")
        for adapter in unconfigured:
            lines.append(f"  • {adapter}: Disconnected or direct cable without DHCP")
        lines.append("  --> TIP: If your PS2 is plugged directly into one of these Ethernet ports with a cable, tick 'PS2 is plugged directly into this PC' above!")
    return "\n".join(lines)


def best_lan_ip():
    """The address most likely to be the one OPL should connect to.

    Prefer the interface that actually routes off-box (the real LAN adapter);
    this avoids virtual adapters like WSL/Hyper-V (often 172.x).
    """
    primary = primary_ip()
    if not primary.startswith("127."):
        return primary
    candidates = sorted(all_ipv4(), key=_lan_rank)
    return candidates[0] if candidates else "127.0.0.1"

