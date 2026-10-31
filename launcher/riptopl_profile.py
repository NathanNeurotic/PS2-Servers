"""Export one server card's connection values as RiptOPL settings.

Only keys that RiptOPL itself reads are written, checked against RiptOPL
``rebuild/main`` at ``RIPTOPL_REF`` (``include/config.h``, ``include/opl.h``
and ``src/app/settingsio.cpp``). Nothing here touches the console, a memory card,
the network or a saved setting: the result is text the user copies across.

Never exported:

* the PS2's own address, netmask, gateway, DNS or DHCP choice. The server does
  not know them, and guessing one breaks a working console;
* a password. A card with a password gets a note to type it on the console;
* SMB2/SMB3 cards. The RiptOPL rebuild speaks SMBv1 only for now.
"""

from dataclasses import dataclass, field
import ipaddress

PROFILE_VERSION = 1
RIPTOPL_REF = "4ec6c68"

NETWORK_FILE = "conf_network.cfg"
OPL_FILE = "settings_riptopl.cfg"  # CONFIG_OPL_FILENAME (include/config.h)

# include/opl.h
NET_PROTO_SMB = 1
NET_PROTO_UDPFS = 2
NET_PROTO_UDPFSBD = 3
NET_PROTO_UDPBD = 4
NET_PROTO_HTTP = 5
START_MODE_MANUAL = 1  # include/iosupport.h

# Ports the RiptOPL client uses with no setting to change them.
UDPFS_CLIENT_PORT = 0xF5F6
UDPBD_CLIENT_PORT = 0xBDBD
RIPTOPL_DEFAULT_SMB_PORT = 1111  # src/app/opl.cpp, what an unconfigured install tries

VALUE_MAX = 255  # CONFIG_KEY_VALUE_LEN - 1
SMB_FIELD_MAX = 31  # gPCShareName / gPCUserName are char[32] (include/opl.h)


@dataclass
class Profile:
    server: str
    protocol_label: str
    network: list = field(default_factory=list)   # (key, value) for conf_network.cfg
    opl: list = field(default_factory=list)       # (key, value) for settings_riptopl.cfg
    menu: list = field(default_factory=list)      # (label, value) as the console menu shows it
    notes: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def _pc_ip(raw):
    text = str(raw or "").strip()
    try:
        address = ipaddress.IPv4Address(text)
    except ValueError:
        raise ValueError("Select the PC's LAN IP in SETUP first.") from None
    if address.is_unspecified or address.is_loopback or address.is_multicast or address.is_link_local:
        raise ValueError("{} is not an address a PS2 can reach. Select the PC's LAN IP in SETUP.".format(text))
    return str(address)


def _port(values, default):
    raw = values.get("port")
    if raw in (None, ""):
        return default
    try:
        number = int(str(raw).strip(), 0)
    except ValueError:
        raise ValueError("The card's port '{}' is not a number.".format(raw)) from None
    if not 0 < number < 65536:
        raise ValueError("The card's port {} is out of range.".format(number))
    return number


def _bind_host(values):
    bind = str(values.get("bind") or "").strip()
    host = bind.rpartition(":")[0].strip() if bind.count(":") == 1 else bind
    return "" if host in ("", "0.0.0.0") else host


def _check_value(key, value, limit=VALUE_MAX):
    text = str(value)
    if "\r" in text or "\n" in text or "\0" in text:
        raise ValueError("{} cannot contain a line break.".format(key))
    if len(text.encode("utf-8")) > limit:
        raise ValueError("{} is longer than RiptOPL's {} bytes.".format(key, limit))
    return text


def _address(values, pc_ip, profile):
    bind = _bind_host(values)
    if bind and bind != pc_ip:
        profile.warnings.append(
            "The card is bound to {} but SETUP selects {}. The export uses {}, the address the "
            "server actually answers on.".format(bind, pc_ip, bind))
        return _pc_ip(bind)
    return pc_ip


def _protocol_keys(profile, protocol):
    profile.opl += [("network_protocol", protocol), ("net_protocol_pick", protocol),
                    ("net_start_mode", START_MODE_MANUAL)]
    profile.notes.append("Network start is set to Manual. Change it to Auto on the console if you "
                         "want RiptOPL to connect at boot.")


def build(server_key, values, pc_ip):
    """The RiptOPL settings for this card. Raises ValueError with a reason."""
    values = dict(values or {})
    if server_key in ("smbv2", "smbv3"):
        raise ValueError("RiptOPL's network stack speaks SMBv1 only for now. Use the SMBv1 card "
                         "for RiptOPL.")
    if server_key == "smbv1":
        return _smbv1(values, _pc_ip(pc_ip))
    if server_key == "udpfs":
        return _udpfs(values)
    if server_key == "udpbd":
        return _udpbd(values)
    if server_key == "http":
        return _http(values, _pc_ip(pc_ip))
    raise ValueError("This card has no RiptOPL network settings to export.")


def _smbv1(values, pc_ip):
    profile = Profile("smbv1", "SMB")
    address = _address(values, pc_ip, profile)
    port = 445 if values.get("take_445") else _port(values, 1025)
    share = _check_value("Share", (values.get("share_name") or "games").strip() or "games",
                         SMB_FIELD_MAX)
    user = _check_value("User", (values.get("username") or "").strip() or "guest", SMB_FIELD_MAX)
    profile.network += [("smb_share_use_nbns", 0), ("smb_ip", address), ("smb_port", port),
                        ("smb_share", share), ("smb_user", user)]
    if values.get("password"):
        profile.notes.append("The card has a password. It is not exported: type it in RiptOPL's "
                             "Network settings on the console.")
        password = "(type it on the console)"
    else:
        profile.network.append(("smb_pass", ""))
        password = "(blank)"
    _protocol_keys(profile, NET_PROTO_SMB)
    profile.menu += [("Protocol", "SMB"), ("Address type", "IP"), ("Server IP", address),
                     ("Port", str(port)), ("Share", share), ("User", user), ("Password", password)]
    if port != RIPTOPL_DEFAULT_SMB_PORT:
        profile.notes.append("RiptOPL tries port {} until it is told otherwise; this card uses {}, "
                             "so the port must be set.".format(RIPTOPL_DEFAULT_SMB_PORT, port))
    return profile


def _udpfs(values):
    profile = Profile("udpfs", "UDPFS")
    port = _port(values, UDPFS_CLIENT_PORT)
    if port != UDPFS_CLIENT_PORT:
        profile.warnings.append("The card listens on 0x{:04X}, but RiptOPL always discovers UDPFS on "
                                "0x{:04X}. Set the card back to 0x{:04X} or the console will not find "
                                "it.".format(port, UDPFS_CLIENT_PORT, UDPFS_CLIENT_PORT))
    image_only = bool(values.get("block_device")) and not values.get("root_dir")
    protocol, access, label = ((NET_PROTO_UDPFSBD, 1, "Image") if image_only
                               else (NET_PROTO_UDPFS, 0, "Files"))
    if values.get("block_device") and values.get("root_dir"):
        profile.notes.append("The card serves a folder and an image. The export picks Files; switch "
                             "UDPFS Access to Image on the console for the image.")
    _protocol_keys(profile, protocol)
    profile.opl.append(("udpfs_access", access))
    profile.menu += [("Protocol", "UDPFS"), ("UDPFS Access", label),
                     ("Server IP", "none needed (found by broadcast)")]
    profile.notes.append("The PS2's own IP must be on the same network as the PC.")
    return profile


def _udpbd(values):
    profile = Profile("udpbd", "UDPBD")
    port = _port(values, UDPBD_CLIENT_PORT)
    if port != UDPBD_CLIENT_PORT:
        profile.warnings.append("The card uses 0x{:04X}, but RiptOPL's UDPBD client always uses "
                                "0x{:04X}.".format(port, UDPBD_CLIENT_PORT))
    _protocol_keys(profile, NET_PROTO_UDPBD)
    profile.menu += [("Protocol", "UDPBD"), ("Server IP", "none needed (found by broadcast)")]
    profile.notes.append("The PS2's own IP must be on the same network as the PC.")
    return profile


def _http(values, pc_ip):
    profile = Profile("http", "HTTP")
    address = _address(values, pc_ip, profile)
    port = _port(values, 1100)
    profile.network += [("http_ip", address), ("http_port", port), ("http_base_path", "/")]
    _protocol_keys(profile, NET_PROTO_HTTP)
    profile.menu += [("Protocol", "HTTP"), ("Server IP", address), ("Port", str(port)),
                     ("Base path", "/")]
    profile.notes.append("Set the port to {}, not 0.".format(port))
    if values.get("recursive") and "path-aware" not in str(values.get("catalog_profile") or "").lower():
        profile.notes.append("Nested folders are only listed with the RiptOPL path-aware game list.")
    return profile


def _legacy(pairs):
    return "".join("{}={}\n".format(k, v) for k, v in pairs)


def _libconfig(pairs):
    lines = []
    for key, value in pairs:
        text = str(value)
        bare = text.lstrip("-").isdigit()
        if not bare:
            text = '"{}"'.format(text.replace("\\", "\\\\").replace('"', '\\"'))
        lines.append("{} = {};\n".format(key, text))
    return "".join(lines)


def render(profile):
    """The full text a user reads and copies from."""
    out = ["PS2-Servers -> RiptOPL settings, profile version {} (RiptOPL rebuild/main {})".format(
        PROFILE_VERSION, RIPTOPL_REF), "Server: {}".format(profile.protocol_label), ""]
    if profile.warnings:
        out += ["FIX FIRST:"] + ["  - " + w for w in profile.warnings] + [""]
    out += ["Easiest: enter these in RiptOPL's Network settings on the console:"]
    out += ["  {:<14} {}".format(label + ":", value) for label, value in profile.menu]
    if profile.notes:
        out += ["", "Notes:"] + ["  - " + n for n in profile.notes]
    out += ["", "Editing the files on a PC instead:",
            "  1. Copy each file to a backup first (for example {0}.bak).".format(NETWORK_FILE),
            "  2. Replace the matching lines, or add any that are missing. Leave every other line as it is.",
            "  3. Keep the file's own style: lines like 'key=value' use block A, lines like "
            "'key = value;' use block B. Never mix them in one file.",
            "  The PS2's own IP, gateway and DNS are not here on purpose: keep the ones the console has."]
    for name, pairs in ((NETWORK_FILE, profile.network), (OPL_FILE, profile.opl)):
        if not pairs:
            continue
        out += ["", "{} (A: key=value)".format(name), _legacy(pairs).rstrip("\n"),
                "", "{} (B: key = value;)".format(name), _libconfig(pairs).rstrip("\n")]
    return "\n".join(out) + "\n"
