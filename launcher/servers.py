"""Registry of the PS2 servers the launcher can run.

Each server is described declaratively: the input fields the GUI should show,
how those values map to command-line arguments, how the server is launched
(an in-bundle Python module via re-exec, or a native binary), and on which
operating systems it is available.

Adding a server, or switching one between Python and native, is a local change
here -- nothing else in the engine needs to know the difference.
"""

import os
import platform
import sys
from dataclasses import dataclass, field
from typing import Callable, Optional

PKG_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(PKG_DIR)


def _repo(*parts):
    return os.path.join(REPO_ROOT, *parts)


# --------------------------------------------------------------------------- #
# Re-exec dispatch
# --------------------------------------------------------------------------- #
def is_frozen():
    """True when running inside a Nuitka/PyInstaller single-file build."""
    return bool(getattr(sys, "frozen", False)) or ("__compiled__" in globals())


def serve_command(key, args):
    """Command that runs the Python server `key` in its own process.

    When bundled, we re-exec *this* executable with a hidden --serve flag, so the
    embedded Python runs the server with no system Python installed. From source,
    we re-run the package the same way.
    """
    if is_frozen():
        return [sys.executable, "--serve", key, *args]
    return [sys.executable, "-m", "launcher", "--serve", key, *args]


# --------------------------------------------------------------------------- #
# Declarative descriptions
# --------------------------------------------------------------------------- #
@dataclass
class Field:
    key: str
    label: str
    kind: str  # folder | file | bool | port | text
    required: bool = False
    default: object = None
    help: str = ""
    advanced: bool = False  # GUI hides advanced fields until expanded


@dataclass
class ServerDef:
    key: str
    label: str
    blurb: str
    runtime: str  # 'python' | 'native'
    fields: list
    _build_argv: Callable
    default_port: Optional[int] = None
    port_is_hex: bool = False  # UDP servers are conventionally written in hex
    share_hint: str = ""  # what the user types into OPL's "Share" field, if any
    module_file: Optional[str] = None  # python: file to import
    module_dir: Optional[str] = None  # python: dir added to sys.path (sibling imports)
    binary_rel: dict = field(default_factory=dict)  # native: {system: 'rel/path'}
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
        return bool(self.module_file) and os.path.exists(self.module_file)

    def launch_command(self, values):
        """Full command (argv list) to start this server with the given values."""
        if self.runtime == "python":
            return serve_command(self.key, self.build_argv(values))
        binary = self.resolve_binary()
        if not binary:
            raise RuntimeError(f"No {self.key} binary available for this platform")
        return [binary, *self.build_argv(values)]


# --------------------------------------------------------------------------- #
# Argument builders (values dict -> server CLI args)
# --------------------------------------------------------------------------- #
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
    args = []
    if v.get("root_dir"):
        args += ["-d", v["root_dir"]]
    if v.get("block_device"):
        args += ["-b", v["block_device"]]
    if v.get("port"):
        args += ["-p", str(v["port"])]
    if v.get("bind"):
        args += ["-i", str(v["bind"])]
    if v.get("read_only"):
        args.append("-r")
    if v.get("enable_compression"):
        args.append("-c")
    if v.get("verbose"):
        args.append("-v")
    return args


def _udpbd_argv(v):
    return [v["image_file"]]


# --------------------------------------------------------------------------- #
# The registry
# --------------------------------------------------------------------------- #
SMBV1 = ServerDef(
    key="smbv1",
    label="SMBv1 server (RiptOPL)",
    blurb="Share a games folder over SMB. Works even on Windows 11 where the OS "
    "removed SMB1.",
    runtime="python",
    default_port=1445,
    share_hint="games",
    module_file=_repo("smbv1_server", "smbserver_opl.py"),
    module_dir=_repo("smbv1_server"),
    fields=[
        Field("games_folder", "Games folder", "folder", required=True,
              help="Folder of PS2 games/apps to share."),
        Field("port", "Port", "port", default=1445, advanced=True,
              help="TCP port (default 1445)."),
        Field("read_only", "Read-only", "bool", default=False, advanced=True,
              help="No saves / no VMC writes."),
        Field("take_445", "Take port 445 (admin)", "bool", default=False, advanced=True,
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
    blurb="Serve a folder and/or a disk image over UDP. Newer protocol; can "
    "transparently decompress CHD/CSO/ZSO.",
    runtime="python",
    default_port=0xF5F6,
    port_is_hex=True,
    module_file=_repo("udpfs_server", "udpfs_server.py"),
    module_dir=_repo("udpfs_server"),
    fields=[
        Field("root_dir", "Games folder", "folder", required=False,
              help="Folder to serve files from (folder and/or image required)."),
        Field("block_device", "Disk image", "file", required=False,
              help="A single disk image to serve as a block device."),
        Field("enable_compression", "Decompress CHD/CSO/ZSO", "bool", default=False,
              help="Compressed images appear as .iso (needs lz4 for ZSO, libchdr for CHD)."),
        Field("read_only", "Read-only", "bool", default=False, advanced=True),
        Field("port", "Port", "port", default=0xF5F6, advanced=True,
              help="UDP port (default 0xF5F6)."),
        Field("bind", "Bind address", "text", default="", advanced=True),
        Field("verbose", "Verbose logging", "bool", default=False, advanced=True),
    ],
    _build_argv=_udpfs_argv,
)

# NOTE: UDPBD is currently the interim native .exe (Windows only). It will be
# replaced by a pure-Python port -- at which point this entry becomes
# runtime="python" with module_file/module_dir set and available_os = all three,
# and the rest of the engine is unaffected.
UDPBD = ServerDef(
    key="udpbd",
    label="UDPBD server",
    blurb="Serve a single disk image as a block device over UDP. The PS2 finds "
    "the server automatically (broadcast).",
    runtime="native",
    default_port=0xBDBD,
    port_is_hex=True,
    binary_rel={"Windows": "udpbd_server/udpbd-server.exe"},
    available_os=("Windows",),  # interim: native exe only exists for Windows
    fields=[
        Field("image_file", "Disk image", "file", required=True,
              help="A single disk image / block device to serve."),
    ],
    _build_argv=_udpbd_argv,
)

REGISTRY = {s.key: s for s in (SMBV1, UDPFS, UDPBD)}
