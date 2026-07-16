"""Direct PS2-to-PC link mode ("PS2 is plugged directly into this PC").

A PS2 cabled straight into a PC has no router on the wire, so nothing answers
its DHCP request and it never gets an IP address -- every network client then
fails identically (empty lists, "check cable and DHCP"). The manual fix is
static addresses on both ends, which means teaching users subnets. This module
removes that: it configures the chosen network port with a fixed address and
runs a tiny single-lease DHCP responder on that port only, so the console --
which defaults to DHCP -- configures itself.

The one real hazard is answering DHCP on a network that already has a DHCP
server (a "rogue DHCP server" can hand garbage leases to every device on a
LAN). Containment is layered:

  * The responder binds to the chosen adapter's own address. Windows delivers
    broadcasts to specifically-bound sockets (verified empirically), so the
    socket cannot even hear DHCP traffic arriving on other interfaces. A
    startup self-probe confirms that delivery works on this machine; if it
    does not, the responder falls back to a wildcard bind but then sends
    replies only as subnet-directed broadcasts, which egress the chosen
    adapter alone.
  * Hard refusals, re-checked in the elevated context at the moment the
    adapter is configured and again when the responder starts: never touch an
    adapter that has a default gateway or holds a DHCP lease -- both mean
    "this is a real network, not a direct link".
  * A tripwire while running: a direct link has exactly one device, so seeing
    several distinct client MACs means this is not a direct link; the
    responder stops rather than keep answering.
  * One lease, one address, and rate-limited replies.

Windows ICS does this job too but is flaky across updates, drags internet
sharing along, and is not ours to debug; this is ~150 lines we can fix.
"""

import errno
import json
import socket
import struct
import time

from .windows_setup import (WindowsSetupError, _powershell, is_windows)

DHCP_SERVER_PORT = 67
DHCP_CLIENT_PORT = 68
LEASE_SECONDS = 86400
PREFIX_LENGTH = 24
SERVER_HOST_NUM = 1   # PC gets .1
CLIENT_HOST_NUM = 10  # PS2 gets .10

# /24 networks tried in order; the first that overlaps nothing local wins.
# Deliberately NOT 192.168.0/1.x -- half the home routers in the world use
# those, and a collision would blackhole the user's real LAN traffic. .137 is
# what Windows ICS picked for exactly this job, so it is the least likely to
# be squatted by other software.
CANDIDATE_SUBNETS = ("192.168.137", "192.168.183", "192.168.73",
                     "10.213.77", "172.31.213")

MAGIC_COOKIE = b"\x63\x82\x53\x63"
_BOOTP = struct.Struct("!BBBBIHHIIII16s64s128s")  # fixed header, 236 bytes

MSG_DISCOVER, MSG_OFFER, MSG_REQUEST, MSG_DECLINE = 1, 2, 3, 4
MSG_ACK, MSG_NAK, MSG_RELEASE, MSG_INFORM = 5, 6, 7, 8


class DirectLinkRefused(RuntimeError):
    """A safety refusal: the situation does not look like a direct PS2 link."""


# --------------------------------------------------------------------------- #
# Small IPv4 math (no ipaddress import: these five lines are the whole need)
# --------------------------------------------------------------------------- #
def _ip_to_int(ip):
    return struct.unpack("!I", socket.inet_aton(ip))[0]


def _int_to_ip(n):
    return socket.inet_ntoa(struct.pack("!I", n & 0xFFFFFFFF))


def _network_of(ip, prefixlen):
    mask = (0xFFFFFFFF << (32 - prefixlen)) & 0xFFFFFFFF if prefixlen else 0
    return _ip_to_int(ip) & mask


def networks_overlap(net1, plen1, net2, plen2):
    plen = min(plen1, plen2)
    if plen <= 0:
        return True
    shift = 32 - plen
    return (net1 >> shift) == (net2 >> shift)


# --------------------------------------------------------------------------- #
# Adapter enumeration and classification (Windows)
# --------------------------------------------------------------------------- #
_ENUMERATE_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$phys = @{}
foreach ($a in @(Get-NetAdapter -Physical)) { $phys[[int]$a.ifIndex] = $true }
$gws = @{}
foreach ($r in @(Get-NetRoute -DestinationPrefix '0.0.0.0/0' -AddressFamily IPv4)) {
  $gws[[int]$r.InterfaceIndex] = $true
}
$addrs = @{}
foreach ($ip in @(Get-NetIPAddress -AddressFamily IPv4)) {
  $k = [int]$ip.InterfaceIndex
  if (-not $addrs.ContainsKey($k)) { $addrs[$k] = @() }
  $addrs[$k] += ,@{ ip = [string]$ip.IPAddress; prefix = [int]$ip.PrefixLength;
                    origin = [string]$ip.PrefixOrigin }
}
$adapters = @()
foreach ($a in @(Get-NetAdapter)) {
  $k = [int]$a.ifIndex
  $list = @()
  if ($addrs.ContainsKey($k)) { $list = @($addrs[$k]) }
  $adapters += ,@{
    name = [string]$a.Name; ifIndex = $k
    desc = [string]$a.InterfaceDescription
    status = [string]$a.Status
    media = [string]$a.PhysicalMediaType
    physical = [bool]$phys.ContainsKey($k)
    gateway = [bool]$gws.ContainsKey($k)
    ipv4 = $list
  }
}
$routes = @()
foreach ($r in @(Get-NetRoute -AddressFamily IPv4)) {
  $routes += ,@{ prefix = [string]$r.DestinationPrefix
                 ifIndex = [int]$r.InterfaceIndex }
}
ConvertTo-Json -InputObject @{ adapters = $adapters; routes = $routes } -Depth 6
"""


def enumerate_adapters():
    """All adapters plus the IPv4 routing table, as plain dicts.

    Returns {"adapters": [...], "routes": [...]}. Each adapter:
      name, if_index, desc, status, media, physical, has_gateway,
      ipv4: [{ip, prefix, origin}, ...]
    """
    if not is_windows():
        raise WindowsSetupError("Direct link mode is only supported on Windows.")
    res = _powershell(_ENUMERATE_SCRIPT)
    if res.returncode != 0:
        raise WindowsSetupError(
            "Could not list network adapters: {}".format(
                ((res.stderr or "") + (res.stdout or "")).strip() or "unknown error"))
    try:
        data = json.loads((res.stdout or "").strip() or "{}")
    except ValueError as e:
        raise WindowsSetupError("Could not parse adapter list: {}".format(e))
    adapters = data.get("adapters") or []
    if isinstance(adapters, dict):  # ConvertTo-Json collapses 1-element arrays
        adapters = [adapters]
    routes = data.get("routes") or []
    if isinstance(routes, dict):
        routes = [routes]
    out = []
    for a in adapters:
        ipv4 = a.get("ipv4") or []
        if isinstance(ipv4, dict):
            ipv4 = [ipv4]
        out.append({
            "name": a.get("name") or "",
            "if_index": int(a.get("ifIndex") or 0),
            "desc": a.get("desc") or "",
            "status": a.get("status") or "",
            "media": a.get("media") or "",
            "physical": bool(a.get("physical")),
            "has_gateway": bool(a.get("gateway")),
            "ipv4": [{"ip": i.get("ip") or "",
                      "prefix": int(i.get("prefix") or 0),
                      "origin": i.get("origin") or ""} for i in ipv4],
        })
    return {"adapters": out, "routes": routes}


def classify_adapter(adapter, allow_down=False):
    """(is_candidate, reason_if_not) -- can this adapter be a direct PS2 link?

    The reasons are user-facing: when nothing qualifies, the launcher shows
    what was seen and why each port was rejected, which is the difference
    between a support conversation and a shrug.

    allow_down tolerates a link that is merely not up -- right for re-checking
    a port that is ALREADY ours (the console being off is the normal state of
    the world at PC boot, and a down port can receive nothing anyway), wrong
    for choosing a new one (a dead port tells us nothing about what it is
    plugged into).
    """
    media = (adapter.get("media") or "").lower()
    if not adapter.get("physical"):
        return False, "virtual adapter"
    if "802.11" in media or "wireless" in media or "bluetooth" in media:
        return False, "wireless (the PS2 needs a wired port)"
    if not allow_down and (adapter.get("status") or "").lower() != "up":
        return False, "no link (cable unplugged, or the console is off)"
    if adapter.get("has_gateway"):
        return False, "reaches a router (a real network, not a direct link)"
    if any((i.get("origin") or "").lower() == "dhcp" for i in adapter.get("ipv4", [])):
        return False, "got its address from a DHCP server (a real network)"
    return True, ""


def find_candidates(enumerated):
    """(candidates, rejected) where rejected is [(adapter, reason), ...]."""
    candidates, rejected = [], []
    for adapter in enumerated["adapters"]:
        ok, reason = classify_adapter(adapter)
        if ok:
            candidates.append(adapter)
        else:
            rejected.append((adapter, reason))
    return candidates, rejected


def taken_networks(enumerated, exclude_if_index=None):
    """Every IPv4 network this host can already reach, as (net_int, prefixlen).

    Both adapter subnets and routing-table entries count: a VPN's remote
    subnet would collide just as hard as a local one. The chosen adapter's own
    networks are excluded -- it is about to be reconfigured.
    """
    taken = []
    for adapter in enumerated["adapters"]:
        if exclude_if_index is not None and adapter["if_index"] == exclude_if_index:
            continue
        for entry in adapter["ipv4"]:
            ip = entry["ip"]
            if not ip or ip.startswith(("127.", "169.254.")):
                continue
            plen = entry["prefix"] or 32
            taken.append((_network_of(ip, plen), plen))
    for route in enumerated.get("routes", []):
        if exclude_if_index is not None and route.get("ifIndex") == exclude_if_index:
            continue
        prefix = route.get("prefix") or ""
        if "/" not in prefix:
            continue
        ip, _, plen_s = prefix.partition("/")
        try:
            plen = int(plen_s)
            net = _network_of(ip, plen)
        except (ValueError, OSError):
            continue
        first_octet = _ip_to_int(ip) >> 24 if plen else 0
        # Skip entries that say nothing about usable unicast space.
        if plen == 0 or first_octet == 127 or first_octet >= 224:
            continue
        if ip.startswith("169.254.") or prefix == "255.255.255.255/32":
            continue
        taken.append((net, plen))
    return taken


def choose_subnet(taken):
    """First candidate /24 that overlaps nothing the host already reaches.

    Returns (server_ip, client_ip) or (None, None) when every candidate
    collides (five networks from three different RFC1918 blocks -- in
    practice this means something is very unusual about the host).
    """
    for base in CANDIDATE_SUBNETS:
        net = _ip_to_int(base + ".0")
        if not any(networks_overlap(net, PREFIX_LENGTH, tn, tp) for tn, tp in taken):
            return ("{}.{}".format(base, SERVER_HOST_NUM),
                    "{}.{}".format(base, CLIENT_HOST_NUM))
    return None, None


# --------------------------------------------------------------------------- #
# Adapter configure / restore (need administrator rights)
# --------------------------------------------------------------------------- #
def apply_adapter_config(if_index, server_ip, prefixlen=PREFIX_LENGTH):
    """Give the chosen adapter the fixed server address (elevated).

    The gateway/lease refusals run again HERE, inside the elevated pass, so a
    stale answer from the earlier scan (or a cable moved in between) cannot
    configure the wrong port.
    """
    script = "\n".join([
        "$ErrorActionPreference = 'Stop'",
        "$idx = {}".format(int(if_index)),
        "if (@(Get-NetRoute -InterfaceIndex $idx -DestinationPrefix '0.0.0.0/0' "
        "-AddressFamily IPv4 -ErrorAction SilentlyContinue).Count -gt 0) {",
        "  throw 'REFUSED: that adapter reaches a router (default gateway); "
        "it is not a direct PS2 link.'",
        "}",
        "if (@(Get-NetIPAddress -InterfaceIndex $idx -AddressFamily IPv4 "
        "-ErrorAction SilentlyContinue | Where-Object { $_.PrefixOrigin -eq 'Dhcp' "
        "}).Count -gt 0) {",
        "  throw 'REFUSED: that adapter holds a DHCP lease from a real network.'",
        "}",
        "$name = (Get-NetAdapter -InterfaceIndex $idx -ErrorAction Stop).Name",
        "Set-NetIPInterface -InterfaceIndex $idx -AddressFamily IPv4 -Dhcp Disabled",
        "Get-NetIPAddress -InterfaceIndex $idx -AddressFamily IPv4 "
        "-ErrorAction SilentlyContinue | "
        "Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue",
        "New-NetIPAddress -InterfaceIndex $idx -IPAddress '{}' -PrefixLength {} "
        "-ErrorAction Stop | Out-Null".format(server_ip, int(prefixlen)),
        "Set-DnsClientServerAddress -InterfaceIndex $idx -ResetServerAddresses "
        "-ErrorAction SilentlyContinue",
        "Write-Output ('CONFIGURED=' + $name)",
    ])
    res = _powershell(script)
    output = ((res.stdout or "") + (res.stderr or "")).strip()
    if res.returncode != 0 or "CONFIGURED=" not in output:
        raise WindowsSetupError(output or "Could not configure the adapter.")
    return output


def restore_adapter_dhcp(if_index, expect_ip=None):
    """Return the adapter to automatic (DHCP) configuration (elevated).

    When expect_ip is given, restore only if the adapter still carries that
    address: if the user (or another tool) has since given the port its own
    configuration, it is not ours to clobber.
    """
    guard = ""
    if expect_ip:
        guard = "\n".join([
            "$cur = @(Get-NetIPAddress -InterfaceIndex $idx -AddressFamily IPv4 "
            "-ErrorAction SilentlyContinue | ForEach-Object { [string]$_.IPAddress })",
            "if (-not ($cur -contains '{}')) {{".format(expect_ip),
            "  Write-Output 'SKIPPED=the port no longer has the direct-link "
            "address; leaving it as it is'",
            "  return",
            "}",
        ])
    script = "\n".join([
        "$ErrorActionPreference = 'Stop'",
        "& {",
        "$idx = {}".format(int(if_index)),
        guard,
        "Get-NetIPAddress -InterfaceIndex $idx -AddressFamily IPv4 "
        "-ErrorAction SilentlyContinue | "
        "Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue",
        "Set-NetIPInterface -InterfaceIndex $idx -AddressFamily IPv4 -Dhcp Enabled",
        "Set-DnsClientServerAddress -InterfaceIndex $idx -ResetServerAddresses "
        "-ErrorAction SilentlyContinue",
        "Write-Output 'RESTORED=automatic (DHCP)'",
        "}",
    ])
    res = _powershell(script)
    output = ((res.stdout or "") + (res.stderr or "")).strip()
    if res.returncode != 0:
        raise WindowsSetupError(output or "Could not restore the adapter.")
    return output


def adapter_state(if_index, name=None):
    """The adapter as enumerate_adapters sees it now, or None if gone.

    Matched by name first (stable across reboots), then by interface index.
    """
    enumerated = enumerate_adapters()
    by_index = None
    for adapter in enumerated["adapters"]:
        if name and adapter["name"] == name:
            return adapter
        if adapter["if_index"] == if_index:
            by_index = adapter
    return by_index


# --------------------------------------------------------------------------- #
# DHCP packets
# --------------------------------------------------------------------------- #
def parse_packet(data):
    """A BOOTREQUEST as a dict, or None for anything else (junk included)."""
    if len(data) < 240 or data[236:240] != MAGIC_COOKIE:
        return None
    (op, htype, hlen, _hops, xid, _secs, flags, ciaddr, _yiaddr, _siaddr,
     giaddr, chaddr, _sname, _file) = _BOOTP.unpack(data[:236])
    if op != 1:  # only client -> server
        return None
    options = {}
    i = 240
    while i < len(data):
        tag = data[i]
        if tag == 0:
            i += 1
            continue
        if tag == 255 or i + 1 >= len(data):
            break
        length = data[i + 1]
        options[tag] = data[i + 2:i + 2 + length]
        i += 2 + length
    mac_len = hlen if 1 <= hlen <= 16 else 6
    return {
        "htype": htype, "hlen": hlen, "xid": xid, "flags": flags,
        "ciaddr": ciaddr, "giaddr": giaddr,
        "mac": chaddr[:mac_len], "chaddr": chaddr,
        "options": options,
    }


def build_reply(pkt, msg_type, server_ip, client_ip, prefixlen=PREFIX_LENGTH,
                lease=LEASE_SECONDS):
    """An OFFER/ACK/NAK for the given request, padded to the BOOTP minimum."""
    server = _ip_to_int(server_ip)
    yiaddr = _ip_to_int(client_ip) if msg_type in (MSG_OFFER, MSG_ACK) else 0
    ciaddr = pkt["ciaddr"] if msg_type == MSG_ACK else 0
    mask = (0xFFFFFFFF << (32 - prefixlen)) & 0xFFFFFFFF
    options = bytes([53, 1, msg_type, 54, 4]) + struct.pack("!I", server)
    if msg_type != MSG_NAK:
        options += bytes([51, 4]) + struct.pack("!I", lease)
        options += bytes([1, 4]) + struct.pack("!I", mask)
        # Router = us. A two-node wire has nowhere to route, but clients that
        # insist on a gateway (and OPL configs that copy one) get a sane value.
        options += bytes([3, 4]) + struct.pack("!I", server)
    options += b"\xff"
    reply = _BOOTP.pack(
        2, pkt["htype"], pkt["hlen"], 0, pkt["xid"], 0, pkt["flags"],
        ciaddr, yiaddr, server, pkt["giaddr"], pkt["chaddr"], b"", b""
    ) + MAGIC_COOKIE + options
    if len(reply) < 300:  # some clients drop sub-minimum BOOTP frames
        reply += b"\x00" * (300 - len(reply))
    return reply


def _mac_text(mac):
    return ":".join("{:02x}".format(b) for b in mac)


# --------------------------------------------------------------------------- #
# The responder
# --------------------------------------------------------------------------- #
class DhcpResponder:
    """Single-lease DHCP for exactly one directly-attached console."""

    # A direct link has one device on it. Several distinct MACs asking for
    # addresses means this wire is a real network -- stop, do not adapt.
    MAX_DISTINCT_MACS = 3
    REPLY_BURST = 8          # token bucket: at most this many queued replies
    REPLY_RATE = 4.0         # ...refilled at this many per second
    IP_RECHECK_SECONDS = 30  # confirm our address still exists this often

    def __init__(self, server_ip, client_ip, prefixlen=PREFIX_LENGTH,
                 adapter_name="", log=print):
        self.server_ip = server_ip
        self.client_ip = client_ip
        self.prefixlen = prefixlen
        self.adapter_name = adapter_name
        self.log = log
        self.sock = None
        self.mode = None  # 'specific' | 'wildcard'
        self.macs_seen = set()
        self.lease_mac = None
        self._tokens = float(self.REPLY_BURST)
        self._token_stamp = time.monotonic()
        self._server_ip_bytes = struct.pack("!I", _ip_to_int(server_ip))
        self._client_ip_bytes = struct.pack("!I", _ip_to_int(client_ip))

    # -- sockets ----------------------------------------------------------- #
    def open_socket(self):
        """Bind to the adapter's own address; fall back to wildcard if this
        machine does not deliver broadcasts to specifically-bound sockets
        (checked with a real self-probe, not an assumption).

        While the link is down Windows parks the address and the bind fails
        with "address not available" -- the normal state when the PC boots
        before the console is switched on. That is a reason to wait, not to
        die: nothing can arrive on a down port, and the PS2's DHCP client
        retransmits, so coming up a few seconds after the link does is fine.
        """
        waiting_logged = False
        while True:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            try:
                sock.bind((self.server_ip, DHCP_SERVER_PORT))
                break
            except OSError as e:
                sock.close()
                not_ready = (e.errno == errno.EADDRNOTAVAIL
                             or getattr(e, "winerror", None) == 10049)
                if not not_ready:
                    raise DirectLinkRefused(
                        "cannot listen on {}:{} -- {}. Is another DHCP "
                        "service running (Windows ICS, or a leftover PS2 "
                        "Servers helper)?".format(
                            self.server_ip, DHCP_SERVER_PORT, e))
                if not waiting_logged:
                    waiting_logged = True
                    self.log("waiting for the link to come up ({} is not "
                             "ready -- cable unplugged or console off?)"
                             .format(self.server_ip))
                time.sleep(3)
        if waiting_logged:
            self.log("link is up")
        if self._probe_broadcast_delivery(sock):
            self.sock = sock
            self.mode = "specific"
            self.log("listening on {}:{} (isolated to its own port)".format(
                self.server_ip, DHCP_SERVER_PORT))
            return
        sock.close()
        self.log("this machine does not deliver broadcasts to a "
                 "specifically-bound socket; using guarded wildcard mode")
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            sock.bind(("0.0.0.0", DHCP_SERVER_PORT))
        except OSError as e:
            sock.close()
            raise DirectLinkRefused(
                "cannot listen on UDP {} -- {}. Is another DHCP service "
                "(Windows ICS?) running?".format(DHCP_SERVER_PORT, e))
        self.sock = sock
        self.mode = "wildcard"

    def _probe_broadcast_delivery(self, sock):
        """Send one broadcast from the adapter and see if `sock` hears it.

        The probe body is deliberately not a DHCP packet (no magic cookie), so
        anything else that might be listening ignores it as junk.
        """
        probe = b"ps2srv-directlink-probe"
        tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        tx.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            tx.bind((self.server_ip, 0))
            tx.sendto(probe, ("255.255.255.255", DHCP_SERVER_PORT))
        except OSError:
            return False
        finally:
            tx.close()
        sock.settimeout(1.0)
        try:
            deadline = time.monotonic() + 1.5
            while time.monotonic() < deadline:
                data, _src = sock.recvfrom(2048)
                if data == probe:
                    return True
        except socket.timeout:
            pass
        except OSError:
            return False
        return False

    def _server_ip_still_present(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.bind((self.server_ip, 0))
            return True
        except OSError:
            return False
        finally:
            s.close()

    # -- refusals ---------------------------------------------------------- #
    def _take_token(self):
        now = time.monotonic()
        self._tokens = min(self.REPLY_BURST,
                           self._tokens + (now - self._token_stamp) * self.REPLY_RATE)
        self._token_stamp = now
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False

    def _track_mac(self, mac):
        if mac not in self.macs_seen:
            self.macs_seen.add(mac)
            if len(self.macs_seen) > self.MAX_DISTINCT_MACS:
                raise DirectLinkRefused(
                    "{} different devices are asking for addresses -- this "
                    "wire is a real network, not a direct PS2 link. Stopping "
                    "so a real network is never disrupted.".format(
                        len(self.macs_seen)))

    # -- protocol ---------------------------------------------------------- #
    def handle_packet(self, data, src):
        """(reply_bytes, (dst_ip, dst_port)) or None. Pure enough to test."""
        pkt = parse_packet(data)
        if pkt is None:
            return None
        mtype_raw = pkt["options"].get(53)
        mtype = mtype_raw[0] if mtype_raw else None
        if mtype not in (MSG_DISCOVER, MSG_REQUEST, MSG_DECLINE, MSG_RELEASE,
                         MSG_INFORM):
            return None  # server-to-server chatter or junk
        self._track_mac(pkt["mac"])
        mac = _mac_text(pkt["mac"])

        if mtype == MSG_DECLINE:
            # The client ARP-checked the offered address and someone answered.
            # On a true two-node wire that cannot happen.
            self.log("client {} DECLINED {} -- is something else on this "
                     "wire?".format(mac, self.client_ip))
            self.lease_mac = None
            return None
        if mtype == MSG_RELEASE:
            self.log("client {} released its address".format(mac))
            if pkt["mac"] == self.lease_mac:
                self.lease_mac = None
            return None
        if mtype == MSG_INFORM:
            return None

        if not self._take_token():
            self.log("reply rate limit hit; dropping a request")
            return None

        if mtype == MSG_DISCOVER:
            self.lease_mac = pkt["mac"]
            self.log("offering {} to {}".format(self.client_ip, mac))
            return (build_reply(pkt, MSG_OFFER, self.server_ip, self.client_ip,
                                self.prefixlen),
                    self._reply_dest(pkt, src))

        # REQUEST. A server id naming someone else means the client chose
        # another server's offer -- none of our business, stay silent.
        server_id = pkt["options"].get(54)
        if server_id and server_id != self._server_ip_bytes:
            return None
        requested = pkt["options"].get(50)
        if requested is None and pkt["ciaddr"]:
            requested = struct.pack("!I", pkt["ciaddr"])
        if requested == self._client_ip_bytes:
            self.lease_mac = pkt["mac"]
            self.log("acknowledging {} for {}".format(self.client_ip, mac))
            return (build_reply(pkt, MSG_ACK, self.server_ip, self.client_ip,
                                self.prefixlen),
                    self._reply_dest(pkt, src))
        want = (socket.inet_ntoa(requested) if requested and len(requested) == 4
                else "?")
        self.log("client {} asked for {}; sending NAK so it re-discovers"
                 .format(mac, want))
        return (build_reply(pkt, MSG_NAK, self.server_ip, self.client_ip,
                            self.prefixlen),
                self._reply_dest(pkt, src, nak=True))

    def _reply_dest(self, pkt, src, nak=False):
        # A renewing client already has an address and expects unicast.
        if not nak and pkt["ciaddr"] and src and src[0] not in ("0.0.0.0", ""):
            return (src[0], DHCP_CLIENT_PORT)
        if self.mode == "wildcard":
            # Containment: a subnet-directed broadcast routes out the chosen
            # adapter alone; a limited broadcast from a wildcard socket could
            # egress the machine's default (real-LAN) interface instead.
            net = _network_of(self.server_ip, self.prefixlen)
            bcast = _int_to_ip(net | (0xFFFFFFFF >> self.prefixlen))
            return (bcast, DHCP_CLIENT_PORT)
        return ("255.255.255.255", DHCP_CLIENT_PORT)

    # -- main loop ---------------------------------------------------------- #
    def serve_forever(self):
        self.sock.settimeout(1.0)
        last_check = time.monotonic()
        self.log("waiting for the PS2 to ask for an address "
                 "(it will get {})".format(self.client_ip))
        while True:
            now = time.monotonic()
            if now - last_check > self.IP_RECHECK_SECONDS:
                last_check = now
                if not self._server_ip_still_present():
                    raise DirectLinkRefused(
                        "{} is no longer configured on this PC; "
                        "stopping".format(self.server_ip))
            try:
                data, src = self.sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError as e:
                raise DirectLinkRefused("socket error: {}".format(e))
            result = self.handle_packet(data, src)
            if result is None:
                continue
            reply, dest = result
            try:
                self.sock.sendto(reply, dest)
            except OSError as e:
                self.log("could not send reply to {}: {}".format(dest, e))


# --------------------------------------------------------------------------- #
# --serve directlink entry point
# --------------------------------------------------------------------------- #
def _startup_adapter_check(adapter_name, if_index, server_ip):
    """Re-verify the refusals in the responder process itself.

    The parent checked, the elevated configure pass checked; this third check
    covers the daily case -- the responder auto-starting on a later launch,
    long after those passes ran, onto whatever the port is plugged into NOW.
    """
    adapter = adapter_state(if_index, adapter_name or None)
    if adapter is None:
        raise DirectLinkRefused(
            "the direct-link network port is no longer present")
    # allow_down: the console being off at PC boot is normal; open_socket
    # waits for the link. Gateway/lease stay hard refusals.
    ok, reason = classify_adapter(adapter, allow_down=True)
    if not ok:
        raise DirectLinkRefused(
            "refusing to answer DHCP on '{}': {}".format(
                adapter["name"], reason))
    if not any(i["ip"] == server_ip for i in adapter["ipv4"]):
        raise DirectLinkRefused(
            "'{}' no longer has the direct-link address {} -- tick the "
            "direct link box again to set it up".format(
                adapter["name"], server_ip))


def run_responder(argv):
    """`--serve directlink` target. Long flags only (Nuitka self-exec guard)."""
    import argparse
    parser = argparse.ArgumentParser(prog="directlink", add_help=False)
    parser.add_argument("--server-ip", required=True)
    parser.add_argument("--client-ip", required=True)
    parser.add_argument("--prefix", type=int, default=PREFIX_LENGTH)
    parser.add_argument("--adapter", default="")
    parser.add_argument("--if-index", type=int, default=0)
    parser.add_argument("--skip-adapter-check", action="store_true",
                        help="tests only: no PowerShell at startup")
    args = parser.parse_args(argv)

    def log(msg):
        print("[direct link] {}".format(msg), flush=True)

    try:
        if is_windows() and not args.skip_adapter_check:
            _startup_adapter_check(args.adapter, args.if_index, args.server_ip)
        responder = DhcpResponder(args.server_ip, args.client_ip, args.prefix,
                                  adapter_name=args.adapter, log=log)
        responder.open_socket()
        responder.serve_forever()
    except DirectLinkRefused as e:
        log("REFUSED: {}".format(e))
        return 3
    except WindowsSetupError as e:
        log("cannot start: {}".format(e))
        return 2
    except KeyboardInterrupt:
        return 0
    return 0
