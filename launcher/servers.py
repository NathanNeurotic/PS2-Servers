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


def frozen_self_exe():
    """Path of the executable to re-launch when frozen.

    In a Nuitka *onefile* build, sys.executable is the temporary EXTRACTED inner
    binary, which cannot be relaunched -- re-running the app means launching the
    ORIGINAL onefile exe the user started. Nuitka exposes it via the
    NUITKA_ONEFILE_BINARY env var; sys.argv[0] is the next-best source.
    """
    argv = getattr(sys, "argv", None)
    argv0 = argv[0] if argv else None
    for candidate in (os.environ.get("NUITKA_ONEFILE_BINARY"), argv0):
        if candidate:
            path = os.path.abspath(candidate)
            if os.path.exists(path):
                return path
    return sys.executable


def serve_command(key, args):
    """Command that runs the Python server `key` in its own process.

    When bundled, we re-exec the original onefile exe with a hidden --serve flag,
    so the embedded Python runs the server with no system Python installed. From
    source, we re-run the package the same way.
    """
    if is_frozen():
        return [frozen_self_exe(), "--serve", key, *args]
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
        if is_frozen():
            return True  # python servers are bundled into the executable
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
def _parse_seconds(raw):
    """A whole-second count from a text field, or None to leave it to the server.

    The server clamps the range and owns the default; this only rejects what is
    not a number at all, so a typo can't become an argv the server must guess at.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        value = int(float(text))
    except ValueError:
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
    # Long flags only: the packaged app re-executes ITSELF to run a server,
    # and Nuitka's self-execution guard aborts (exit 2) when a compiled binary
    # is invoked with a bare '-c' (or '-m') followed by another argument --
    # exactly what '-c -v' produced once compression became the default.
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
    # Blank/garbage means "leave it alone" -- the server owns the default and the
    # clamp, so don't pass a flag we'd only have to second-guess here.
    timeout = _parse_seconds(v.get("peer_timeout"))
    if timeout is not None:
        args += ["--peer-timeout", str(timeout)]
    if v.get("read_only"):
        args.append("--read-only")
    if v.get("enable_compression"):
        args.append("--enable-compression")
    if v.get("single_port"):
        args.append("--single-port")
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


# --------------------------------------------------------------------------- #
# The registry
# --------------------------------------------------------------------------- #
SMBV1 = ServerDef(
    key="smbv1",
    label="SMBv1 server (RiptOPL)",
    blurb="Share a games folder over SMB. Works even on Windows 11 where the OS "
    "removed SMB1.",
    runtime="python",
    default_port=1111,
    share_hint="games",
    module_file=_repo("smbv1_server", "smbserver_opl.py"),
    module_dir=_repo("smbv1_server"),
    fields=[
        Field("games_folder", "Games folder", "folder", required=True,
              help="Folder of PS2 games/apps to share."),
        Field("port", "Port", "port", default=1111, advanced=True,
              help="TCP port (default 1111). Avoid ports below 1033 -- Windows can "
              "reserve or block low ports."),
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
        Field("enable_compression", "Decompress CHD/CSO/ZSO", "bool", default=True,
              help="On by default so CHD/CSO/ZSO images appear as playable .iso "
              "(needs lz4 for ZSO, libchdr for CHD; formats without their library "
              "are simply left as-is). Untick to serve files without decompression."),
        # Deliberately NOT advanced: the users who need this are the least likely
        # to go looking under a disclosure triangle for it.
        Field("single_port", "Check this if you are using Modulo", "bool",
              default=False,
              help="Only tick this for Modulo. Modulo uses improper UDPFS protocol "
                   "— it can't switch to the server's data port — so everything runs "
                   "on one port (0xF5F6 by default, set under Advanced). Modulo's "
                   "bug: NHDDL, RiptOPL, POPSTARTER, POPSLOADER and wLaunchELF-R3Z "
                   "all work without it."),
        Field("read_only", "Read-only", "bool", default=False, advanced=True),
        Field("port", "Port", "port", default=0xF5F6, advanced=True,
              help="UDP port (default 0xF5F6). In Modulo UDPFS mode this single "
                   "port carries everything."),
        Field("data_port", "Data port", "port", default=0, advanced=True,
              help="Leave 0 (auto) unless the data port must be predictable — a "
                   "manual firewall rule, port forwarding, or a strict NAT can't "
                   "follow the auto port, which changes every launch. Setting it "
                   "also adds a matching firewall rule. Ignored in Modulo mode."),
        Field("bind", "Bind address", "text", default="", advanced=True),
        Field("peer_timeout", "Idle timeout (seconds)", "text", default="3600",
              advanced=True,
              help="Drop a console after this long with no traffic, closing the "
                   "files it had open (60-86400, default 3600 = 1 hour). UDPFS has "
                   "no disconnect, so a paused game and an unplugged PS2 look "
                   "identical — set it too low and a long pause loses its game. "
                   "Lower it only to clear stale consoles faster."),
        Field("verbose", "Verbose logging", "bool", default=False, advanced=True),
    ],
    _build_argv=_udpfs_argv,
)

# Pure-Python port (udpbd_server/udpbd_server.py). Cross-platform; this is the
# only UDPBD implementation the launcher uses. A legacy Windows udpbd-server.exe
# was never wired in and is no longer vendored -- see udpbd_server/SOURCE.md.
UDPBD = ServerDef(
    key="udpbd",
    label="UDPBD server",
    blurb="Serve a single disk image as a block device over UDP. The PS2 finds "
    "the server automatically (broadcast).",
    runtime="python",
    default_port=0xBDBD,
    port_is_hex=True,
    module_file=_repo("udpbd_server", "udpbd_server.py"),
    module_dir=_repo("udpbd_server"),
    fields=[
        Field("image_file", "Disk image", "file", required=True,
              help="A single disk image to serve as a block device."),
        Field("read_only", "Read-only", "bool", default=False, advanced=True,
              help="No saves / no VMC writes."),
        Field("verbose", "Verbose logging", "bool", default=False, advanced=True,
              help="Log every read/write command."),
    ],
    _build_argv=_udpbd_argv,
)

REGISTRY = {s.key: s for s in (SMBV1, UDPFS, UDPBD)}
