"""Declarative registry for the server processes launched by PS2 Servers."""

import math
import os
import platform
import sys
from dataclasses import dataclass, field
from typing import Callable, Optional

PKG_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(PKG_DIR)


def _repo(*parts):
    return os.path.join(REPO_ROOT, *parts)


def is_frozen():
    return bool(getattr(sys, "frozen", False)) or ("__compiled__" in globals())


def frozen_self_exe():
    argv = getattr(sys, "argv", None)
    argv0 = argv[0] if argv else None
    for candidate in (os.environ.get("NUITKA_ONEFILE_BINARY"), argv0):
        if candidate:
            path = os.path.abspath(candidate)
            if os.path.exists(path):
                return path
    return sys.executable


def serve_command(key, args):
    if is_frozen():
        return [frozen_self_exe(), "--serve", key, *args]
    return [sys.executable, "-m", "launcher", "--serve", key, *args]


@dataclass
class Field:
    key: str
    label: str
    kind: str
    required: bool = False
    default: object = None
    help: str = ""
    advanced: bool = False
    windows_only: bool = False
    # For kind="choice": the ordered options as (label shown, value sent). The
    # two differ so the interface can say something a user understands while the
    # wire keeps the word the servers actually accept.
    choices: tuple = ()


@dataclass
class ServerDef:
    key: str
    label: str
    blurb: str
    runtime: str
    fields: list
    _build_argv: Callable
    default_port: Optional[int] = None
    port_is_hex: bool = False
    recommendation: str = ""
    recommendation_kind: str = ""
    share_hint: str = ""
    module_file: Optional[str] = None
    module_dir: Optional[str] = None
    binary_rel: dict = field(default_factory=dict)
    available_os: tuple = ("Windows", "Linux", "Darwin")

    def build_argv(self, values):
        return self._build_argv(values)

    def port_display(self):
        if self.default_port is None:
            return "-"
        return ("0x%04X" % self.default_port) if self.port_is_hex else str(self.default_port)

    def resolve_binary(self, system=None):
        system = system or platform.system()
        rel = self.binary_rel.get(system)
        if not rel:
            return None
        path = _repo(*rel.split("/"))
        return path if os.path.exists(path) else None

    def is_available(self, system=None):
        system = system or platform.system()
        if system not in self.available_os:
            return False
        if self.runtime == "native":
            return self.resolve_binary(system) is not None
        if is_frozen():
            return True
        return bool(self.module_file) and os.path.exists(self.module_file)

    def launch_command(self, values):
        if self.runtime == "python":
            return serve_command(self.key, self.build_argv(values))
        binary = self.resolve_binary()
        if not binary:
            raise RuntimeError(f"No {self.key} binary available for this platform")
        return [binary, *self.build_argv(values)]


def _parse_seconds(raw):
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        value = int(float(text))
    except (ValueError, OverflowError):
        return None
    return value if value > 0 else None


def _smb_argv(v, smb_version="1"):
    args = ["--share", "games={}".format(v["games_folder"]), "--smb-version", str(smb_version)]
    if v.get("port"):
        args += ["--port", str(v["port"])]
    if v.get("bind"):
        args += ["--bind", str(v["bind"])]
    if v.get("read_only"):
        args.append("--read-only")
    if v.get("take_445"):
        args.append("--take-445")
    if v.get("verbose"):
        args.append("-v")
    return args


def _smbv1_argv(v):
    return _smb_argv(v, "1")


# SMBv2 and SMBv3 run smb2_server/, not the SMBv1 server with a flag. They were
# briefly offered on top of stub handlers that connected and then served nothing;
# tests/test_netinfo_and_smb.py now refuses to let a mode be offered before the
# server behind it can list a share, which is the rule that was missing.
#
# One implementation, two modes: SMB3 is the same protocol at a higher dialect,
# so the ceiling is a flag rather than a second server to keep in step.
def _smb2_argv(v, smb_version):
    args = ["--share", "games={}".format(v["games_folder"]),
            "--smb-version", str(smb_version)]
    if v.get("port"):
        args += ["--port", str(v["port"])]
    if v.get("bind"):
        args += ["--bind", str(v["bind"])]
    if v.get("read_only"):
        args.append("--read-only")
    if v.get("take_445"):
        args.append("--take-445")
    # A password is required unless the user deliberately ticks the open box.
    # The server refuses to start with neither, rather than defaulting to open.
    user = (v.get("username") or "").strip()
    password = v.get("password") or ""
    if v.get("open_share"):
        args.append("--open")
    elif user and password:
        args += ["--user", "{}:{}".format(user, password)]
    if v.get("verbose"):
        args.append("-v")
    return args


def _smbv2_argv(v):
    return _smb2_argv(v, "2")


def _smbv3_argv(v):
    return _smb2_argv(v, "3")


_SMB2_FIELDS = [
    Field("games_folder", "Games folder", "folder", required=True,
          help="Folder to share. Browsable from a modern PC, unlike SMBv1."),
    Field("port", "Port", "port", default=1445, advanced=False,
          help="TCP port (default 1445). Windows itself can only connect on "
               "445 -- use the advanced option below for that."),
    Field("username", "Username", "text", default="ps2",
          help="The name to enter on the client. SMB2/SMB3 clients require a "
               "login; there is no guest mode as in SMBv1."),
    Field("password", "Password", "text", default="",
          help="Required unless 'No password' is ticked below. Anyone who can "
               "reach the port can try to guess it, so make it a long one."),
    Field("open_share", "No password (anyone on the network can read it)",
          "bool", default=False, advanced=True,
          help="Serve without a login at all. Only for a network you trust "
               "completely -- every device on it gets the share."),
    Field("read_only", "Read-only", "bool", default=False, advanced=True,
          help="No saves / no VMC writes."),
    Field("take_445", "Take port 445 (admin)", "bool", default=False,
          advanced=True, windows_only=True,
          help="Bind standard port 445 by pausing Windows file sharing. Needs "
               "admin, and is what Windows Explorer needs to connect at all."),
    Field("bind", "Bind address", "text", default="", advanced=True,
          help="Interface to bind (blank = all)."),
    Field("verbose", "Verbose logging", "bool", default=False, advanced=True),
]


# How the protocol mode is offered, and what each choice puts on the wire.
#
# "Proper" rather than "Standard" because the choice a user is making is between
# the protocol as specified and a client that counts sequences differently --
# "standard" reads like a recommendation, and the recommended setting is Auto.
# The wire value stays "standard": it is what both the Python server and Edge
# accept, and renaming it would break every saved configuration and command line.
PROTOCOL_MODE_CHOICES = (
    ("Auto", "auto"),
    ("Proper", "standard"),
    ("Modulo", "modulo"),
)

# Accepts the label, the wire value, or any casing of either. A saved settings
# file predating the dropdown holds "standard"; a new one holds "Proper"; and a
# user editing the file by hand may write either. All three should work rather
# than silently falling back to auto.
_PROTOCOL_MODE_BY_NAME = {}
for _label, _value in PROTOCOL_MODE_CHOICES:
    _PROTOCOL_MODE_BY_NAME[_label.lower()] = _value
    _PROTOCOL_MODE_BY_NAME[_value.lower()] = _value


def migrate_saved(server_key, saved):
    """Translate retired settings keys into the controls that replaced them.

    Applied when a saved configuration is loaded into a card, because the card
    restores and collects values by walking its widgets: a key with no widget is
    dropped on load and can never appear again on save. So honouring a retired
    key inside build_argv is not enough on its own -- the GUI cannot put it
    there. It has to become a key that does have a widget, before the widgets are
    filled in.

    Translating rather than merely preserving is also the better outcome for the
    person upgrading. Their console keeps working AND the reason is now visible
    in the interface, where they can see they are on Modulo and change it, rather
    than being held in a key nothing displays.
    """
    if not saved:
        return saved
    if server_key != "udpfs":
        return saved
    if not saved.get("modulo_mode"):
        return saved
    migrated = dict(saved)
    # Only fills an empty selection. Someone who has since made an explicit
    # choice in the new control means it.
    if not protocol_mode_value(migrated.get("protocol_mode")):
        migrated["protocol_mode"] = "Modulo"
    migrated.pop("modulo_mode", None)
    return migrated


def protocol_mode_value(raw):
    """Map whatever is stored for protocol_mode to a value the servers accept.

    Returns None for anything unrecognised, which the caller treats as "say
    nothing and let the server default to auto" -- a bad string must not become
    a command-line argument the server exits on.
    """
    if raw is None:
        return None
    return _PROTOCOL_MODE_BY_NAME.get(str(raw).strip().lower())


def _udpfs_argv(v):
    if not v.get("root_dir") and not v.get("block_device"):
        raise ValueError("UDPFS needs a Games folder and/or a Disk image.")
    args = []
    if v.get("root_dir"):
        args += ["--root-dir", v["root_dir"]]
    if v.get("block_device"):
        args += ["--block-device", v["block_device"]]
    if v.get("port"):
        args += ["--port", str(v["port"])]
    if v.get("data_port"):
        args += ["--data-port", str(v["data_port"])]
    if v.get("bind"):
        args += ["--bind", str(v["bind"])]
    if v.get("tx_delay_ms") is not None:
        try:
            delay = float(v["tx_delay_ms"])
            if math.isfinite(delay) and delay >= 0.0:
                args += ["--tx-delay-ms", str(delay)]
        except (TypeError, ValueError):
            pass
    timeout = _parse_seconds(v.get("peer_timeout"))
    if timeout is not None:
        args += ["--peer-timeout", str(timeout)]
    if v.get("read_only"):
        args.append("--read-only")
    if not v.get("enable_compression", True):
        args.append("--no-compression")
    # The old global "Modulo mode" checkbox was removed because it applied one
    # client's quirk to every client at once. Auto classifies each session on its
    # own, so a Proper client and several Modulo clients transfer side by side.
    #
    # The selector is back as a visible three-way choice, not because Auto is
    # unreliable, but because when it does misjudge a client there has to be a
    # way to say so from the interface rather than by hand-editing a settings
    # file. Auto stays the default.
    #
    # modulo_mode is still read: a settings file written before the dropdown
    # existed carries it, and losing that on upgrade would silently move someone
    # off the setting that made their console work.
    protocol_mode = protocol_mode_value(v.get("protocol_mode"))
    if v.get("modulo_mode"):
        protocol_mode = "modulo"
    # "auto" is deliberately not passed. It is the server default, so omitting it
    # keeps the logged command line honest about what was actually chosen.
    if protocol_mode in ("standard", "modulo"):
        args += ["--protocol-mode", protocol_mode]
    if v.get("verbose"):
        args.append("--verbose")
    return args


def _udpbd_argv(v):
    args = [v["image_file"]]
    if v.get("read_only"):
        args.append("-r")
    if v.get("verbose"):
        args.append("-v")
    return args


SMBV1 = ServerDef(
    key="smbv1",
    label="SMBv1 server (RiptOPL)",
    blurb="Share a games folder over SMB. Works even on Windows 11 where the OS removed SMB1.",
    runtime="python",
    default_port=1025,
    share_hint="games",
    module_file=_repo("smbv1_server", "smbserver_opl.py"),
    module_dir=_repo("smbv1_server"),
    fields=[
        Field("games_folder", "Games folder", "folder", required=True,
              help="Folder of PS2 games/apps to share."),
        Field("port", "Port", "port", default=1025, advanced=False,
              help="TCP port (default 1025). Ports below 1025 require Administrator."),
        Field("read_only", "Read-only", "bool", default=False, advanced=True,
              help="No saves / no VMC writes."),
        Field("take_445", "Take port 445 (admin)", "bool", default=False,
              advanced=True, windows_only=True,
              help="Bind standard port 445 by pausing Windows file sharing. Needs admin."),
        Field("bind", "Bind address", "text", default="", advanced=True,
              help="Interface to bind (blank = all)."),
        Field("verbose", "Verbose logging", "bool", default=False, advanced=True),
    ],
    _build_argv=_smbv1_argv,
)

SMBV2 = ServerDef(
    key="smbv2",
    label="SMBv2 server",
    blurb="Share a games folder over SMB2, which modern Windows can browse "
          "without re-enabling SMB1. Needs a username and password.",
    runtime="python",
    default_port=1445,
    share_hint="games",
    module_file=_repo("smb2_server", "smb2_server.py"),
    module_dir=_repo("smb2_server"),
    fields=_SMB2_FIELDS,
    _build_argv=_smbv2_argv,
)

SMBV3 = ServerDef(
    key="smbv3",
    label="SMBv3 server",
    blurb="The same server negotiating up to SMB 3.0.2. Pick this unless a "
          "client refuses it and needs SMB2.",
    runtime="python",
    default_port=1445,
    share_hint="games",
    module_file=_repo("smb2_server", "smb2_server.py"),
    module_dir=_repo("smb2_server"),
    fields=_SMB2_FIELDS,
    _build_argv=_smbv3_argv,
)

UDPFS = ServerDef(
    key="udpfs",
    label="UDPFS server",
    blurb="Serve a folder and/or disk image over UDP. Automatic compatibility supports standards-compliant and Modulo clients at the same time; no compatibility checkbox is required.",
    recommendation="Recommended for most setups",
    recommendation_kind="good",
    runtime="python",
    default_port=0xF5F6,
    port_is_hex=True,
    module_file=_repo("udpfs_server", "ps2servers_core.py"),
    module_dir=_repo("udpfs_server"),
    fields=[
        Field("root_dir", "Games folder", "folder", required=False,
              help="Folder to serve files from (folder and/or image required)."),
        Field("block_device", "Disk image", "file", required=False,
              help="A single disk image to serve as a block device."),
        Field("enable_compression", "Decompress CHD/CSO/ZSO", "bool", default=True,
              help="On by default. Formats without their optional library remain unadvertised."),
        # Visible rather than advanced. It is the first thing to reach for when a
        # console will not connect, and a setting you have to know exists is no
        # use to the person who needs it.
        Field("protocol_mode", "Protocol mode", "choice", default="Auto",
              choices=PROTOCOL_MODE_CHOICES,
              help="Auto handles Proper and Modulo clients at the same time and "
                   "suits almost everyone. Pick one explicitly only if a console "
                   "will not connect on Auto."),
        Field("read_only", "Read-only", "bool", default=False, advanced=True),
        Field("port", "Port", "port", default=0xF5F6, advanced=True,
              help="UDP discovery port (default 0xF5F6)."),
        Field("data_port", "Data port", "port", default=0, advanced=True,
              help="Leave 0 (auto) unless a firewall/NAT requires a predictable data port."),
        Field("bind", "Bind address", "text", default="", advanced=True,
              help="Leave blank. Discovery already listens on every network interface; this only pins the data source address."),
        Field("tx_delay_ms", "TX delay (ms)", "text", default="0",
              advanced=True,
              help="Optional pacing delay between UDP transmissions in milliseconds."),
        Field("peer_timeout", "Idle timeout (seconds)", "text", default="3600",
              advanced=True,
              help="Drop an inactive console and close its handles after 60-86400 seconds."),
        Field("verbose", "Verbose logging", "bool", default=False, advanced=True),
    ],
    _build_argv=_udpfs_argv,
)

UDPBD = ServerDef(
    key="udpbd",
    label="UDPBD server",
    blurb="Serve a single disk image as a block device over UDP. Largely superseded by UDPFS.",
    recommendation="Legacy — prefer UDPFS",
    recommendation_kind="legacy",
    runtime="python",
    default_port=0xBDBD,
    port_is_hex=True,
    module_file=_repo("udpbd_server", "udpbd_server.py"),
    module_dir=_repo("udpbd_server"),
    fields=[
        Field("image_file", "Disk image", "file", required=True),
        Field("read_only", "Read-only", "bool", default=False, advanced=True),
        Field("verbose", "Verbose logging", "bool", default=False, advanced=True),
    ],
    _build_argv=_udpbd_argv,
)

REGISTRY = {s.key: s for s in (SMBV1, SMBV2, SMBV3, UDPFS, UDPBD)}
