"""Read-only raw storage for UDPBD; never opens a device for writing."""

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

    def __init__(self, path, backend):
        self.path = path
        self._backend = backend
        self.size = backend.size
        self._position = 0
        if self.size <= 0 or self.size % 512 or self.size // 512 > 0xFFFFFFFF:
            backend.close()
            raise ValueError("Device capacity must be positive, 512-byte aligned, "
                             "and fit UDPBD's 32-bit sector count (less than 2 TiB).")

    def sector_count(self):
        return self.size // 512

    def seek(self, sector):
        position = sector * 512
        if not 0 <= position <= self.size:
            raise OSError(errno.EINVAL, "Sector is outside this device")
        self._position = position

    def read(self, count):
        if count < 0:
            raise ValueError("Negative read size")
        count = min(count, self.size - self._position)
        if not count:
            return b""
        alignment = self._backend.sector_size
        start = self._position // alignment * alignment
        end = min(self.size, ((self._position + count + alignment - 1)
                              // alignment) * alignment)
        data = self._backend.read_at(start, end - start)
        if len(data) != end - start:
            raise OSError(errno.EIO, "Short raw-device read; drive may be disconnected")
        offset = self._position - start
        result = data[offset:offset + count]
        self._position += len(result)
        return result

    def write(self, data):
        raise PermissionError(errno.EROFS, "Raw devices are served read-only")

    def close(self):
        self._backend.close()


class _LinuxDevice:
    def __init__(self, path):
        import fcntl
        # O_NONBLOCK prevents a mistaken FIFO target from hanging startup.
        self.fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
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

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


class _WindowsDevice:
    def __init__(self, path):
        from ctypes import wintypes
        if not re.fullmatch(r"\\\\\.\\(?:PhysicalDrive\d+|[A-Z]:)", path,
                            re.IGNORECASE):
            raise ValueError(r"Use \\.\PhysicalDriveN or \\.\X: (without a trailing slash)")
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api = api
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
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        api.CloseHandle.restype = wintypes.BOOL
        api.VirtualAlloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t,
                                     wintypes.DWORD, wintypes.DWORD]
        api.VirtualAlloc.restype = ctypes.c_void_p
        api.VirtualFree.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD]
        api.VirtualFree.restype = wintypes.BOOL
        # GENERIC_READ only; shared with the host, explicitly noncached.
        self.handle = api.CreateFileW(path, 0x80000000, 3, None, 3, 0x20000000, None)
        if self.handle == ctypes.c_void_p(-1).value:
            self.handle = None
            raise ctypes.WinError(ctypes.get_last_error())
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


def open_raw_device(path):
    """Open a disk/partition for read-only serving, with native capacity queries."""
    system = platform.system()
    if system == "Linux":
        backend = _LinuxDevice(path)
    elif system == "Windows":
        backend = _WindowsDevice(path)
    else:
        raise ValueError("Raw-device serving is supported on Linux and Windows only")
    return RawDevice(path, backend)


def list_devices():
    """List device identity/capacity metadata without reading device contents."""
    if platform.system() == "Windows":
        command = ("[Console]::OutputEncoding = [Text.UTF8Encoding]::new(); "
                   "Get-Disk | Select-Object Number,FriendlyName,Size,IsBoot,IsSystem "
                   "| ConvertTo-Json -Compress")
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, timeout=20, creationflags=0x08000000)
        if completed.returncode:
            raise OSError(completed.stderr.decode("utf-8", "replace").strip())
        disks = json.loads(completed.stdout.decode("utf-8-sig") or "[]") or []
        if isinstance(disks, dict):
            disks = [disks]
        return [(rf"\\.\PhysicalDrive{disk['Number']}",
                 f"{disk['FriendlyName']} — {int(disk['Size']) // (1024**3)} GiB")
                for disk in disks if not disk["IsBoot"] and not disk["IsSystem"]]
    if platform.system() == "Linux":
        devices = []
        for name in sorted(os.listdir("/sys/class/block")):
            root = os.path.join("/sys/class/block", name)
            if name.startswith(("loop", "ram", "zram", "sr")):
                continue
            try:
                with open(os.path.join(root, "size")) as source:
                    size = int(source.read().strip()) * 512
                if not size:
                    continue
                partition = os.path.exists(os.path.join(root, "partition"))
                devices.append(("/dev/" + name,
                                f"{'Partition' if partition else 'Disk'} — "
                                f"{size // (1024**3)} GiB"))
            except (OSError, ValueError):
                continue
        return devices
    return []
