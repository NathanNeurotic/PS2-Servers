"""LAN IP detection.

OPL needs the host's LAN IP typed into its network settings, so the launcher
surfaces a best-guess address. We deliberately prefer real private-LAN ranges
(192.168/x, 10/x, 172.16-31/x) over VPN / WSL / link-local addresses, matching
the warning the SMBv1 server already prints ("usually 192.168.x.x -- NOT a
VPN/WSL address").
"""

import socket


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
