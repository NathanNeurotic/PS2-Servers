"""Aligned UDPBD raw storage; writes require explicit exclusive access."""

import ctypes
import errno
import json
import os
import platform
import re
import stat
import struct
import subprocess


class RawDevice:
    """Present aligned device reads as the byte stream UDPBD packetization needs."""

    read_only = True
    is_raw = True

    def __init__(self, path, backend, writable=False):
        self.path = path
        self._backend = backend
        self.read_only = not writable
        self.size = backend.size
        self._position = 0
        self._request_end = None
        self._cached_start = 0
        self._cached_data = b""
        if self.size <= 0 or self.size % 512 or self.size // 512 > 0xFFFFFFFF:
            backend.close()
            raise ValueError("Device capacity must be positive, 512-byte aligned, "
                             "and fit UDPBD's 32-bit sector count (less than 2 TiB).")

    def sector_count(self):
        return self.size // 512

    def seek(self, sector, sector_count=None):
        position = sector * 512
        if not 0 <= position <= self.size:
            raise OSError(errno.EINVAL, "Sector is outside this device")
        self._position = position
        self._request_end = None if sector_count is None else position + sector_count * 512
        if self._request_end is not None and not position <= self._request_end <= self.size:
            raise OSError(errno.EINVAL, "Read request exceeds raw-device capacity")
        self._cached_data = b""  # Every UDPBD request seeks; never retain across requests.

    def read(self, count):
        if count < 0:
            raise ValueError("Negative read size")
        limit = self.size if self._request_end is None else self._request_end
        count = min(count, limit - self._position)
        if not count:
            return b""
        alignment = self._backend.sector_size
        pieces = []
        position = self._position
        while position < self._position + count:
            cached_end = self._cached_start + len(self._cached_data)
            if not self._cached_start <= position < cached_end:
                self._cached_start = position // alignment * alignment
                # Read ahead only within this request, bounded to 64 KiB.
                request_end = self._request_end or (self._cached_start + alignment)
                aligned_end = min(self.size, ((request_end + alignment - 1) // alignment) * alignment)
                size = min(65536, aligned_end - self._cached_start)
                self._cached_data = self._backend.read_at(self._cached_start, size)
                if len(self._cached_data) != size:
                    self._cached_data = b""
                    raise OSError(errno.EIO, "Short raw-device read; drive may be disconnected")
                cached_end = self._cached_start + len(self._cached_data)
            size = min(self._position + count - position, cached_end - position)
            offset = position - self._cached_start
            pieces.append(self._cached_data[offset:offset + size])
            position += size
        result = b"".join(pieces)
        self._position += len(result)
        return result

    def write(self, data):
        if self.read_only:
            raise PermissionError(errno.EROFS, "Raw devices are served read-only")
        end = self._position + len(data)
        limit = self.size if self._request_end is None else self._request_end
        if end > limit:
            raise OSError(errno.EINVAL, "Write exceeds raw-device request/capacity")
        self._cached_data = b""
        alignment = self._backend.sector_size
        # UDPBD packet sizes need not match the native sector size. Preserve
        # surrounding bytes under the exclusive device claim.
        while self._position < end:
            start = self._position // alignment * alignment
            offset = self._position - start
            size = min(end - self._position, alignment - offset)
            if offset or size != alignment:
                block = bytearray(self._backend.read_at(start, alignment))
                if len(block) != alignment:
                    raise OSError(errno.EIO, "Short native read during write")
                block[offset:offset + size] = data[:size]
                block = bytes(block)
            else:
                block = data[:size]
            if self._backend.write_at(start, block) != len(block):
                raise OSError(errno.EIO, "Short native write")
            self._position += size
            data = data[size:]

    def flush(self):
        if not self.read_only:
            self._backend.flush()

    def close(self):
        self._backend.close()


class _LinuxDevice:
    def __init__(self, path, writable=False, exclusive=False):
        import fcntl
        # O_NONBLOCK prevents a mistaken FIFO target from hanging startup.
        flags = os.O_RDWR if writable else os.O_RDONLY
        self.fd = os.open(path, flags | os.O_NONBLOCK | (os.O_EXCL if exclusive else 0))
        try:
            if not stat.S_ISBLK(os.fstat(self.fd).st_mode):
                raise ValueError("Select a Linux block disk or partition, not a file")
            # linux/fs.h: BLKGETSIZE64 is _IOR(0x12, 114, size_t).
            command = 0x80000000 | (struct.calcsize("P") << 16) | 0x1272
            self.size = struct.unpack("=Q", fcntl.ioctl(self.fd, command, b"\0" * 8))[0]
            self.sector_size = struct.unpack(
                "=I", fcntl.ioctl(self.fd, 0x1268, b"\0" * 4))[0]  # BLKSSZGET
            if (self.sector_size < 512 or self.sector_size > 65536
                    or self.sector_size & (self.sector_size - 1)
                    or self.size % self.sector_size):
                raise ValueError("Unsupported raw-device sector size/capacity")
        except BaseException:
            os.close(self.fd)
            raise

    def read_at(self, offset, count):
        return os.pread(self.fd, count, offset)

    def write_at(self, offset, data):
        return os.pwrite(self.fd, data, offset)

    def flush(self):
        os.fsync(self.fd)

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


class _WindowsDevice:
    def __init__(self, path, writable=False, exclusive=False, volumes=()):
        from ctypes import wintypes
        if not re.fullmatch(r"\\\\\.\\(?:PhysicalDrive\d+|[A-Z]:)", path,
                            re.IGNORECASE):
            raise ValueError(r"Use \\.\PhysicalDriveN or \\.\X: (without a trailing slash)")
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api = api
        self._volume_handles = []
        api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                   ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                   wintypes.HANDLE]
        api.CreateFileW.restype = wintypes.HANDLE
        api.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p,
                                       wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                       ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        api.DeviceIoControl.restype = wintypes.BOOL
        api.SetFilePointerEx.argtypes = [wintypes.HANDLE, ctypes.c_longlong,
                                        ctypes.c_void_p, wintypes.DWORD]
        api.SetFilePointerEx.restype = wintypes.BOOL
        api.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        api.ReadFile.restype = wintypes.BOOL
        api.WriteFile.argtypes = api.ReadFile.argtypes
        api.WriteFile.restype = wintypes.BOOL
        api.FlushFileBuffers.argtypes = [wintypes.HANDLE]
        api.FlushFileBuffers.restype = wintypes.BOOL
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        api.CloseHandle.restype = wintypes.BOOL
        api.VirtualAlloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t,
                                     wintypes.DWORD, wintypes.DWORD]
        api.VirtualAlloc.restype = ctypes.c_void_p
        api.VirtualFree.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD]
        api.VirtualFree.restype = wintypes.BOOL
        self.handle = None
        try:
            # For a physical disk, hold every filesystem volume lock for the
            # entire server lifetime. The disk itself is not a volume handle.
            if exclusive and re.search(r"PhysicalDrive\d+$", path, re.IGNORECASE):
                for volume in volumes:
                    handle = api.CreateFileW(volume, 0xC0000000, 3, None, 3, 0, None)
                    if handle == ctypes.c_void_p(-1).value:
                        raise ctypes.WinError(ctypes.get_last_error())
                    self._volume_handles.append(handle)
                    self._lock_volume(handle)
            access = 0xC0000000 if writable or exclusive else 0x80000000
            self.handle = api.CreateFileW(path, access, 3, None, 3, 0x20000000, None)
            if self.handle == ctypes.c_void_p(-1).value:
                self.handle = None
                raise ctypes.WinError(ctypes.get_last_error())
            if exclusive and re.fullmatch(r"\\\\\.\\[A-Z]:", path, re.IGNORECASE):
                self._lock_volume(self.handle)
        except BaseException:
            self.close()
            raise
        try:
            self.size = struct.unpack("<q", self._ioctl(0x7405C, 8))[0]
            geometry = self._ioctl(0x70000, 24)
            self.sector_size = struct.unpack_from("<I", geometry, 20)[0]
            if (self.sector_size < 512 or self.sector_size > 65536
                    or self.sector_size & (self.sector_size - 1)
                    or self.size % self.sector_size):
                raise ValueError("Unsupported raw-device sector size/capacity")
            if re.fullmatch(r"\\\\\.\\[A-Z]:", path, re.IGNORECASE):
                # Permit reads of the final sectors without filesystem bounds checks.
                self._ioctl(0x90083, 0)  # FSCTL_ALLOW_EXTENDED_DASD_IO
        except BaseException:
            self.close()
            raise

    def _ioctl(self, command, size):
        output = ctypes.create_string_buffer(size) if size else None
        returned = ctypes.c_ulong()
        if not self.api.DeviceIoControl(self.handle, command, None, 0, output,
                                        size, ctypes.byref(returned), None):
            raise ctypes.WinError(ctypes.get_last_error())
        if size and returned.value < size:
            raise OSError(errno.EIO, "Incomplete device capacity/geometry response")
        return output.raw if output is not None else b""

    def _lock_volume(self, handle):
        returned = ctypes.c_ulong()
        for command in (0x90018, 0x90020):  # LOCK, then DISMOUNT, never force.
            if not self.api.DeviceIoControl(handle, command, None, 0, None, 0,
                                            ctypes.byref(returned), None):
                raise ctypes.WinError(ctypes.get_last_error())

    def write_at(self, offset, data):
        buffer = self.api.VirtualAlloc(None, len(data), 0x3000, 4)
        if not buffer:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            ctypes.memmove(buffer, data, len(data))
            if not self.api.SetFilePointerEx(self.handle, offset, None, 0):
                raise ctypes.WinError(ctypes.get_last_error())
            written = ctypes.c_ulong()
            if not self.api.WriteFile(self.handle, buffer, len(data),
                                      ctypes.byref(written), None):
                raise ctypes.WinError(ctypes.get_last_error())
            return written.value
        finally:
            self.api.VirtualFree(buffer, 0, 0x8000)

    def flush(self):
        if not self.api.FlushFileBuffers(self.handle):
            raise ctypes.WinError(ctypes.get_last_error())

    def read_at(self, offset, count):
        # VirtualAlloc aligns the buffer to the allocation granularity (64 KiB),
        # unlike a Python bytes buffer. Offset/count are sector-aligned upstream.
        buffer = self.api.VirtualAlloc(None, count, 0x3000, 4)
        if not buffer:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not self.api.SetFilePointerEx(self.handle, offset, None, 0):
                raise ctypes.WinError(ctypes.get_last_error())
            received = ctypes.c_ulong()
            if not self.api.ReadFile(self.handle, buffer, count,
                                     ctypes.byref(received), None):
                raise ctypes.WinError(ctypes.get_last_error())
            return ctypes.string_at(buffer, received.value)
        finally:
            self.api.VirtualFree(buffer, 0, 0x8000)

    def close(self):
        if self.handle is not None:
            self.api.CloseHandle(self.handle)
            self.handle = None
        for handle in getattr(self, "_volume_handles", []):
            self.api.CloseHandle(handle)
        self._volume_handles = []


def open_raw_device(path, writable=False, exclusive=False):
    """Open raw storage; exclusive mode unmounts/locks before serving."""
    if writable and not exclusive:
        raise ValueError("Raw writes require exclusive access")
    system = platform.system()
    record = _exclusive_target(path) if exclusive else None
    if system == "Linux":
        if record:
            for mount in sorted(record.get("mounts") or [], key=len, reverse=True):
                try:
                    subprocess.run(["umount", "--", mount], check=True, capture_output=True, timeout=30)
                except subprocess.CalledProcessError as error:
                    detail = error.stderr.decode("utf-8", "replace").strip()
                    raise OSError(errno.EBUSY, f"Cannot unmount {mount}: {detail}. "
                                  "Previously unmounted filesystems remain unmounted.") from error
            if _exclusive_target(path).get("mounts"):
                raise OSError(errno.EBUSY, "Target still has mounted filesystems")
        backend = _LinuxDevice(path, writable=writable, exclusive=exclusive)
    elif system == "Windows":
        if record and record.get("kind") == "Disk" and record.get("mounts") and not record.get("volumes"):
            raise ValueError("Cannot identify mounted Windows volumes; exclusive access refused")
        backend = _WindowsDevice(path, writable=writable, exclusive=exclusive,
                                 volumes=record.get("volumes", ()) if record else ())
    else:
        raise ValueError("Raw-device serving is supported on Linux and Windows only")
    device = RawDevice(path, backend, writable=writable)
    device.mount_warning = mount_warning(path)
    return device


def _exclusive_target(path):
    target = os.path.realpath(path) if platform.system() == "Linux" else path.casefold()
    for item in _metadata():
        candidate = os.path.realpath(item["path"]) if platform.system() == "Linux" else item["path"].casefold()
        if candidate == target:
            if item.get("system") or any(m == "/" or m.startswith("/boot")
                                         for m in item.get("mounts") or []):
                raise ValueError("Exclusive access to a boot/system device is forbidden")
            return item
    raise ValueError("Cannot establish target identity and mount state; exclusive access refused")


def _metadata():
    """Read OS metadata only; include hidden system targets for mount checks."""
    if platform.system() == "Windows":
        command = r"""[Console]::OutputEncoding = [Text.UTF8Encoding]::new();
$items = @(Get-Disk | ForEach-Object {
    $disk = $_
    try {
        $parts = @(Get-Partition -DiskNumber $disk.Number -ErrorAction Stop)
    } catch {
        if ($_.CategoryInfo.Category -eq 'ObjectNotFound' -and
            $_.FullyQualifiedErrorId -like 'CmdletizationQuery_NotFound*,Get-Partition') {
            $parts = @()
        } else {
            throw
        }
    }
    $mounts = @($parts | ForEach-Object { $_.AccessPaths } | Where-Object { $_ -and $_ -notmatch 'Volume\{' })
    $volumes = @($parts | ForEach-Object { $_.AccessPaths } | Where-Object { $_ -match '^\\\\\?\\Volume\{' } | ForEach-Object { $_.TrimEnd('\') })
    foreach ($part in $parts) {
        if ($part.DriveLetter -and -not @($part.AccessPaths | Where-Object { $_ -match '^\\\\\?\\Volume\{' }).Count) {
            $volumes += ('\\.\'+$part.DriveLetter+':')
        }
    }
    [pscustomobject]@{path=('\\.\PhysicalDrive'+$disk.Number); kind='Disk'; model=$disk.FriendlyName; size=$disk.Size; mounts=$mounts; volumes=$volumes; fs=''; system=($disk.IsBoot -or $disk.IsSystem)}
    foreach ($part in $parts) {
        if ($part.DriveLetter) {
            $volume = $part | Get-Volume -ErrorAction Stop
            [pscustomobject]@{path=('\\.\'+$part.DriveLetter+':'); kind='Volume'; model=($disk.FriendlyName+' / '+$volume.FileSystemLabel); size=$part.Size; mounts=@($part.AccessPaths | Where-Object { $_ -and $_ -notmatch 'Volume\{' }); fs=$volume.FileSystemType; system=($disk.IsBoot -or $disk.IsSystem)}
        }
    }
})
ConvertTo-Json -InputObject $items -Depth 4 -Compress"""
        argv = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                "$ErrorActionPreference='Stop'; " + command]
        flags = 0x08000000
    elif platform.system() == "Linux":
        argv = ["lsblk", "--json", "--tree", "--bytes", "--output",
                "PATH,TYPE,SIZE,MODEL,FSTYPE,MOUNTPOINTS"]
        flags = 0
    else:
        raise ValueError("Raw-device metadata requires Linux or Windows")
    completed = subprocess.run(argv, capture_output=True, timeout=20, creationflags=flags)
    if completed.returncode:
        raise OSError(completed.stderr.decode("utf-8", "replace").strip())
    data = json.loads(completed.stdout.decode("utf-8-sig") or "[]")
    if platform.system() == "Windows":
        return data if isinstance(data, list) else [data]
    records = []

    def visit(item, model=""):
        model = item.get("model") or model
        mounts = [m for m in item.get("mountpoints", []) if m]
        for child in item.get("children", []):
            mounts.extend(visit(child, model))
        if item.get("type") in ("disk", "part", "loop") and int(item.get("size") or 0):
            records.append(dict(path=item["path"], kind="Partition" if item["type"] == "part"
                                else "Disk", model=model, size=int(item["size"]),
                                fs=item.get("fstype") or "", mounts=sorted(set(mounts)),
                                system=False))
        return mounts

    for item in data.get("blockdevices", []):
        visit(item)
    return records


def list_devices():
    """List selectable identity, layout, filesystem and mount metadata."""
    devices = []
    for item in _metadata():
        if item.get("system"):
            continue
        mounts = item.get("mounts") or []
        if isinstance(mounts, str):
            mounts = [mounts]
        description = (f"{item['kind']} / {item.get('model') or 'Unknown model'} / "
                       f"{int(item['size']) // (1024**3)} GiB / "
                       f"{item.get('fs') or 'filesystem unknown'} / "
                       + ("MOUNTED: " + ", ".join(mounts) if mounts else "unmounted"))
        devices.append((item["path"], description))
    return devices


def mount_warning(path):
    """Warn for a selected mounted target, including child partitions of a disk."""
    try:
        target = os.path.realpath(path) if platform.system() == "Linux" else path.casefold()
        for item in _metadata():
            candidate = os.path.realpath(item["path"]) if platform.system() == "Linux" else item["path"].casefold()
            if candidate == target:
                mounts = item.get("mounts") or []
                if isinstance(mounts, str):
                    mounts = [mounts]
                if mounts:
                    return ("WARNING: Target or a child partition is mounted at "
                            + ", ".join(mounts) + ". Unmount before serving where possible; "
                            "stop host applications from modifying it. Read-only access "
                            "does not provide a filesystem snapshot.")
                return ""
        return "WARNING: Mount state is unknown for this target. Verify it is quiescent before serving."
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        return f"WARNING: Could not check mount state ({error}). Verify the target is quiescent before serving."
