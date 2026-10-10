"""One Windows Desktop launcher per login session; server children are exempt."""

import ctypes
from ctypes import wintypes
import platform


_MUTEX_NAME = "Local\\PS2Servers.Desktop"


def restart_args():
    return ["--desktop-restart"] if platform.system() == "Windows" else []


class DesktopInstance:
    def __init__(self, restarting=False):
        self.restarting = restarting
        self.handle = None
        self.acquired = False

    def __enter__(self):
        if platform.system() != "Windows":
            self.acquired = True
            return self
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        self.kernel.CreateMutexW.restype = wintypes.HANDLE
        self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.kernel.WaitForSingleObject.restype = wintypes.DWORD
        self.kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
        self.kernel.ReleaseMutex.restype = wintypes.BOOL
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.handle = self.kernel.CreateMutexW(None, False, _MUTEX_NAME)
        if self.handle:
            # Restart/UAC children start before the old GUI finishes stopping
            # its servers. Ordinary second launches report immediately.
            result = self.kernel.WaitForSingleObject(self.handle, 30000 if self.restarting else 0)
            self.acquired = result in (0, 0x80)  # acquired or abandoned owner
        if not self.acquired:
            if self.handle:
                self.kernel.CloseHandle(self.handle)
                self.handle = None
            user = ctypes.WinDLL("user32", use_last_error=True)
            user.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
            user.MessageBoxW.restype = ctypes.c_int
            user.MessageBoxW(None,
                             "PS2 Servers is already running, or its Desktop lock could not be acquired. "
                             "Open the existing window or its tray icon. One window can run multiple servers.",
                             "PS2 Servers", 0x40)
        return self

    def __exit__(self, *_exc):
        if self.handle:
            if self.acquired:
                self.kernel.ReleaseMutex(self.handle)
            self.kernel.CloseHandle(self.handle)
            self.handle = None
