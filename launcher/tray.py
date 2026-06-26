"""Windows notification-area (system tray) support — pure ctypes, no deps.

Lets the launcher hide to the tray instead of quitting, so the servers keep
running without a taskbar window. Right-click the tray icon for Open / Quit.

Windows-only: ``AVAILABLE`` is False elsewhere and the GUI keeps its normal
close-to-quit behaviour. ``start()`` returns False if the icon can't be created,
so the app can fall back gracefully and never trap the user with a hidden window.
"""

import platform
import threading

from . import app_icon

AVAILABLE = platform.system() == "Windows"

if AVAILABLE:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    shell32 = ctypes.windll.shell32
    kernel32 = ctypes.windll.kernel32

    LRESULT = ctypes.c_ssize_t
    WNDPROCTYPE = ctypes.WINFUNCTYPE(
        LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

    WM_DESTROY = 0x0002
    WM_CLOSE = 0x0010
    WM_NULL = 0x0000
    WM_APP = 0x8000
    WM_TRAY = WM_APP + 1
    WM_LBUTTONUP = 0x0202
    WM_LBUTTONDBLCLK = 0x0203
    WM_RBUTTONUP = 0x0205
    WM_CONTEXTMENU = 0x007B

    NIM_ADD = 0
    NIM_DELETE = 2
    NIF_MESSAGE = 0x01
    NIF_ICON = 0x02
    NIF_TIP = 0x04

    IDI_APPLICATION = 32512
    IMAGE_ICON = 1
    LR_LOADFROMFILE = 0x0010
    LR_DEFAULTSIZE = 0x0040

    MF_STRING = 0x0000
    TPM_RIGHTBUTTON = 0x0002
    TPM_RETURNCMD = 0x0100

    ID_OPEN = 1001
    ID_QUIT = 1002

    class NOTIFYICONDATA(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("hWnd", wintypes.HWND),
            ("uID", wintypes.UINT),
            ("uFlags", wintypes.UINT),
            ("uCallbackMessage", wintypes.UINT),
            ("hIcon", wintypes.HICON),
            ("szTip", wintypes.WCHAR * 128),
            ("dwState", wintypes.DWORD),
            ("dwStateMask", wintypes.DWORD),
            ("szInfo", wintypes.WCHAR * 256),
            ("uVersion", wintypes.UINT),
            ("szInfoTitle", wintypes.WCHAR * 64),
            ("dwInfoFlags", wintypes.DWORD),
        ]

    class WNDCLASS(ctypes.Structure):
        _fields_ = [
            ("style", wintypes.UINT),
            ("lpfnWndProc", WNDPROCTYPE),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HANDLE),
            ("hbrBackground", wintypes.HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
        ]

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    # Set restype/argtypes so 64-bit handles aren't truncated to 32-bit ints.
    user32.DefWindowProcW.restype = LRESULT
    user32.DefWindowProcW.argtypes = [
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
    user32.RegisterClassW.restype = wintypes.ATOM
    user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASS)]
    user32.LoadIconW.restype = wintypes.HICON
    user32.LoadIconW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
    user32.LoadImageW.restype = wintypes.HICON
    user32.LoadImageW.argtypes = [
        wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
        ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.CreatePopupMenu.restype = wintypes.HMENU
    user32.AppendMenuW.argtypes = [
        wintypes.HMENU, wintypes.UINT, ctypes.c_void_p, wintypes.LPCWSTR]
    user32.TrackPopupMenu.restype = ctypes.c_int
    user32.TrackPopupMenu.argtypes = [
        wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, wintypes.HWND, wintypes.LPVOID]
    user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.PostMessageW.argtypes = [
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.GetMessageW.argtypes = [
        ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    shell32.Shell_NotifyIconW.restype = wintypes.BOOL
    shell32.Shell_NotifyIconW.argtypes = [
        wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATA)]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]

    def load_app_icon():
        try:
            icon = user32.LoadImageW(
                None, app_icon.ico_path(), IMAGE_ICON, 0, 0,
                LR_LOADFROMFILE | LR_DEFAULTSIZE)
            if icon:
                return icon
        except Exception:
            pass
        return user32.LoadIconW(None, IDI_APPLICATION)


class SystemTray:
    """A single tray icon with an Open / Quit right-click menu.

    on_open / on_quit are called from the tray's own thread, so they should just
    hand off to the GUI thread (e.g. push to a queue the Tk loop drains).
    """

    _class_name = "PS2ServersTrayWindow"
    _class_registered = False

    def __init__(self, tooltip, on_open, on_quit):
        self.tooltip = tooltip[:127]
        self.on_open = on_open
        self.on_quit = on_quit
        self._hwnd = None
        self._nid = None
        self._icon_added = False
        self._wndproc = None  # keep a reference so it isn't garbage-collected
        self._ready = threading.Event()

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()
        self._ready.wait(timeout=3.0)
        return self._hwnd is not None and self._icon_added

    def _run(self):
        try:
            hinst = kernel32.GetModuleHandleW(None)
            self._wndproc = WNDPROCTYPE(self._wnd_proc)
            if not SystemTray._class_registered:
                wc = WNDCLASS()
                wc.lpfnWndProc = self._wndproc
                wc.hInstance = hinst
                wc.hIcon = load_app_icon()
                wc.lpszClassName = self._class_name
                if not user32.RegisterClassW(ctypes.byref(wc)):
                    if kernel32.GetLastError() != 1410:  # not ALREADY_EXISTS
                        return
                SystemTray._class_registered = True

            self._hwnd = user32.CreateWindowExW(
                0, self._class_name, "PS2 Servers", 0, 0, 0, 0, 0,
                None, None, hinst, None)
            if not self._hwnd:
                return

            nid = NOTIFYICONDATA()
            nid.cbSize = ctypes.sizeof(NOTIFYICONDATA)
            nid.hWnd = self._hwnd
            nid.uID = 1
            nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
            nid.uCallbackMessage = WM_TRAY
            nid.hIcon = load_app_icon()
            nid.szTip = self.tooltip
            self._nid = nid
            self._icon_added = bool(
                shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)))
        finally:
            self._ready.set()

        if not self._icon_added:
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        if msg == WM_TRAY:
            event = lparam & 0xFFFF
            if event in (WM_RBUTTONUP, WM_CONTEXTMENU):
                self._show_menu(hwnd)
            elif event in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                self._safe(self.on_open)
            return 0
        if msg == WM_CLOSE:
            if self._nid is not None:
                shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid))
            user32.DestroyWindow(hwnd)
            return 0
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _show_menu(self, hwnd):
        menu = user32.CreatePopupMenu()
        user32.AppendMenuW(menu, MF_STRING, ID_OPEN, "Open PS2 Servers")
        user32.AppendMenuW(menu, MF_STRING, ID_QUIT, "Quit")
        pt = POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        user32.SetForegroundWindow(hwnd)  # so the menu closes when clicked away
        cmd = user32.TrackPopupMenu(
            menu, TPM_RIGHTBUTTON | TPM_RETURNCMD, pt.x, pt.y, 0, hwnd, None)
        user32.PostMessageW(hwnd, WM_NULL, 0, 0)  # known menu-dismiss workaround
        user32.DestroyMenu(menu)
        if cmd == ID_OPEN:
            self._safe(self.on_open)
        elif cmd == ID_QUIT:
            self._safe(self.on_quit)

    @staticmethod
    def _safe(fn):
        try:
            fn()
        except Exception:
            pass

    def stop(self):
        if self._hwnd:
            user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)
            self._hwnd = None
