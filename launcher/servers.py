"""Declarative registry for the server processes launched by PS2 Servers."""

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


def _smbv1_argv(v):
    args = ["--share", "games={}".format(v["games_folder"])]
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
    timeout = _parse_seconds(v.get("peer_timeout"))
    if timeout is not None:
        args += ["--peer-timeout", str(timeout)]
    if v.get("read_only"):
        args.append("--read-only")
    if not v.get("enable_compression", True):
        args.append("--no-compression")
    # Existing saved launcher state may still contain modulo_mode. Preserve it as
    # a strict diagnostic selection, but new GUI sessions use automatic mode and
    # no longer expose the misleading global checkbox.
    protocol_mode = v.get("protocol_mode")
    if v.get("modulo_mode"):
        protocol_mode = "modulo"
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
    default_port=1111,
    share_hint="games",
    module_file=_repo("smbv1_server", "smbserver_opl.py"),
    module_dir=_repo("smbv1_server"),
    fields=[
        Field("games_folder", "Games folder", "folder", required=True,
              help="Folder of PS2 games/apps to share."),
        Field("port", "Port", "port", default=1111, advanced=True,
              help="TCP port (default 1111). Avoid ports below 1033."),
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
        Field("read_only", "Read-only", "bool", default=False, advanced=True),
        Field("port", "Port", "port", default=0xF5F6, advanced=True,
              help="UDP discovery port (default 0xF5F6)."),
        Field("data_port", "Data port", "port", default=0, advanced=True,
              help="Leave 0 (auto) unless a firewall/NAT requires a predictable data port."),
        Field("bind", "Bind address", "text", default="", advanced=True,
              help="Leave blank. Discovery already listens on every interface; this only pins the data source address."),
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

REGISTRY = {s.key: s for s in (SMBV1, UDPFS, UDPBD)}
