"""Owned private runtime-token storage; never stores passwords or Web API keys.

Windows uses user-bound DPAPI. POSIX follows the existing managed profile's
owner-only file policy (not encryption). Managed-profile import is explicit,
read-only and limited to the PS2-Servers profile supplied by the caller.
"""
import ctypes
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import stat
import tempfile

MAGIC = b"PS2RA-OWNED-1\n"
LIMIT = 16384


@dataclass(frozen=True)
class Credentials:
    """Canonical account identity plus a runtime token excluded from repr."""
    username: str
    token: str = field(repr=False)

    def __post_init__(self):
        for value, maximum in ((self.username, 64), (self.token, 4096)):
            if type(value) is not str or not 1 <= len(value) <= maximum or any(c in value for c in "\r\n\0"):
                raise ValueError("Invalid runtime credentials")


def _dpapi(data, *, decrypt=False):
    """Protect/unprotect bytes with UI forbidden and release native buffers."""
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    source = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    incoming, outgoing = Blob(len(data), source), Blob()
    pointer = ctypes.POINTER(Blob)
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [pointer, ctypes.c_void_p if decrypt else wintypes.LPCWSTR,
                         pointer, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, pointer]
    function.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    try:
        if not function(ctypes.byref(incoming), None if decrypt else "PS2-Servers owned RA",
                        None, None, None, 1, ctypes.byref(outgoing)):
            raise ValueError("Credential protection failed")
        if not outgoing.size or outgoing.size > LIMIT:
            raise ValueError("Invalid protected credential size")
        return ctypes.string_at(outgoing.data, outgoing.size)
    finally:
        ctypes.memset(source, 0, len(source))
        if outgoing.data:
            ctypes.memset(outgoing.data, 0, outgoing.size)
            kernel.LocalFree(outgoing.data)


def _check_path(path, *, directory=False):
    info = path.lstat()
    if (stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) &
            getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)):
        raise ValueError("Credential paths must not be links")
    if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        raise ValueError("Invalid credential path type")
    if os.name != "nt" and (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077):
        raise ValueError("Credential paths must be private to their owner")


def _read_private(path):
    _check_path(path)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    with os.fdopen(descriptor, "rb") as source:
        data = source.read(LIMIT + 1)
    if not 1 <= len(data) <= LIMIT:
        raise ValueError("Invalid credential file size")
    return data


class CredentialStore:
    """Atomic owned profile with an explicit, non-destructive managed import."""
    def __init__(self, profile):
        self.profile = Path(profile)
        self.directory = self.profile / "owned"
        self.path = self.directory / "credentials"

    def _prepare(self):
        self.profile.mkdir(parents=True, exist_ok=True, mode=0o700)
        _check_path(self.profile, directory=True)
        self.directory.mkdir(exist_ok=True, mode=0o700)
        _check_path(self.directory, directory=True)

    def save(self, credentials):
        """Persist only a validated runtime token; atomic replacement preserves rollback."""
        if not isinstance(credentials, Credentials):
            raise ValueError("Runtime credentials required")
        self._prepare()
        if self.path.exists() or self.path.is_symlink():
            _check_path(self.path)
        data = json.dumps({"version": 1, "username": credentials.username,
                           "token": credentials.token}, ensure_ascii=True).encode()
        if os.name == "nt":
            data = _dpapi(data)
        payload = MAGIC + data
        if len(payload) > LIMIT:
            raise ValueError("Credential file exceeds limit")
        descriptor, temporary = tempfile.mkstemp(prefix=".credentials-", dir=self.directory)
        try:
            with os.fdopen(descriptor, "wb") as destination:
                destination.write(payload)
                destination.flush()
                os.fsync(destination.fileno())
            os.replace(temporary, self.path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def load(self):
        """Load only the owned file; missing credentials never trigger an import."""
        if not self.directory.exists():
            if self.directory.is_symlink():
                raise ValueError("Credential paths must not be links")
            return None
        self._prepare()
        if not self.path.exists() and not self.path.is_symlink():
            return None
        data = _read_private(self.path)
        if not data.startswith(MAGIC):
            raise ValueError("Invalid owned credential format")
        data = data[len(MAGIC):]
        if os.name == "nt":
            data = _dpapi(data, decrypt=True)
        try:
            value = json.loads(data)
            if type(value) is not dict or type(value.get("version")) is not int or value["version"] != 1:
                raise ValueError()
            return Credentials(value["username"], value["token"])
        except (ValueError, KeyError, TypeError, RecursionError):
            raise ValueError("Invalid owned credential format") from None

    def clear(self):
        """Remove only owned credentials; the managed profile remains a rollback path."""
        if self.directory.exists():
            self._prepare()
            if self.path.exists() or self.path.is_symlink():
                _check_path(self.path)
                self.path.unlink()

    def read_managed(self, *, consent=False):
        """Read the existing PS2-Servers credentials only after explicit consent.

        Import callers must revalidate this token with login2 before saving it.
        The original credentials and Web API key are never changed or copied.
        """
        if consent is not True:
            raise ValueError("Explicit managed-profile import consent required")
        _check_path(self.profile, directory=True)
        data = _read_private(self.profile / "credentials")
        if os.name == "nt":
            data = _dpapi(data, decrypt=True)
        if data.endswith(b"\0"):
            data = data[:-1]
        try:
            username, token = data.decode("utf-8").split("\n")
            return Credentials(username, token)
        except (ValueError, UnicodeError):
            raise ValueError("Invalid managed credential format") from None
