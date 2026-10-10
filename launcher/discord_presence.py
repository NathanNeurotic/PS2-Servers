"""Optional Discord desktop IPC activity, using verified, explicitly shared RA titles."""
import ctypes
import json
import os
from pathlib import Path
import re
import select
import socket
import struct
import tempfile
import threading
import time
import uuid

DEFAULT_APPLICATION_ID = "1558114313619898409"
MODE_NAMES = {"smbv1": "SMBv1", "smbv2": "SMBv2", "smbv3": "SMBv3", "udpfs": "UDPFS",
              "http": "HTTP", "udpbd": "UDPBD", "retroachievements": "RetroAchievements"}


def public_game_title(value):
    """A verified display title only; never paths, URLs or host identity."""
    if not isinstance(value, str):
        return ""
    title = " ".join(value.split()).strip()[:96]
    if (not title or any(ord(ch) < 32 or ord(ch) == 127 for ch in title)
            or "/" in title or "\\" in title or "@" in title
            or re.search(r"(?i)https?:|(?:\b[0-9]{1,3}\.){3}[0-9]{1,3}\b", title)):
        return ""
    return title


class DesktopActivity:
    """Allowlisted server activity; title sharing requires explicit consent."""
    def __init__(self, show_uptime=False):
        self.lock = threading.Lock()
        self.modes = ()
        self.show_uptime = show_uptime
        self.started = int(time.time())
        self.game = ""
        self.game_identity = None
        self.game_started = None

    def update(self, modes, show_uptime=False, game=None, show_game=False):
        names = tuple(sorted({key for key in modes if key in MODE_NAMES}))
        verified = (show_game and "retroachievements" in names
                    and isinstance(game, dict) and game.get("state") == "playing"
                    and isinstance(game.get("session"), int)
                    and not isinstance(game.get("session"), bool)
                    and game["session"] > 0)
        title = public_game_title(game.get("title")) if verified else ""
        identity = (game["session"], title) if title else None
        with self.lock:
            self.modes = names
            self.show_uptime = bool(show_uptime)
            if identity != self.game_identity:
                self.game_identity = identity
                self.game = title
                self.game_started = int(time.time()) if title else None

    def snapshot(self):
        with self.lock:
            names = [MODE_NAMES[key] for key in self.modes]
            result = {"type": 0, "name": "PS2-Servers", "details": ", ".join(names) if names else "Ready to serve games",
                      "state": "Serving PlayStation 2 games" if names else "Desktop launcher", "instance": False}
            if self.game:
                result["details"] = self.game
                result["state"] = "Playing on PlayStation 2"
                if self.show_uptime:
                    result["timestamps"] = {"start": self.game_started}
            elif self.show_uptime:
                result["timestamps"] = {"start": self.started}
            return result


def frame(opcode, value):
    data = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return struct.pack("<II", opcode, len(data)) + data


class Transport:
    def __init__(self, index):
        self.handle = None
        self.socket = None
        if os.name == "nt":
            from ctypes import wintypes
            self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            self.kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
            self.kernel.CreateFileW.restype = wintypes.HANDLE
            self.kernel.PeekNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                                  ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
            self.kernel.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                             ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
            self.kernel.WriteFile.argtypes = self.kernel.ReadFile.argtypes
            self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            self.kernel.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
            handle = self.kernel.CreateFileW(r"\\.\pipe\discord-ipc-" + str(index), 0xC0000000, 0, None, 3, 0, None)
            if handle == ctypes.c_void_p(-1).value:
                raise OSError("Discord IPC is not available")
            self.handle = handle
        else:
            self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.socket.settimeout(0.3)
            base = os.environ.get("XDG_RUNTIME_DIR") or os.environ.get("TMPDIR") or tempfile.gettempdir()
            try:
                self.socket.connect(str(Path(base) / ("discord-ipc-" + str(index))))
            except BaseException:
                self.socket.close()
                raise

    def write(self, data):
        if len(data) > 4096:
            raise ValueError("Discord activity frame is too large")
        if self.socket is not None:
            self.socket.sendall(data)
        else:
            from ctypes import wintypes
            written = wintypes.DWORD()
            if not self.handle or not self.kernel.WriteFile(self.handle, data, len(data), ctypes.byref(written), None) or written.value != len(data):
                raise OSError("Discord IPC write failed")

    def read(self, size, deadline, stopped):
        data = bytearray()
        while len(data) < size:
            if stopped.is_set() or time.monotonic() >= deadline:
                raise TimeoutError("Discord IPC did not answer")
            if self.socket is not None:
                try:
                    chunk = self.socket.recv(size - len(data))
                except socket.timeout:
                    continue
            else:
                from ctypes import wintypes
                available = wintypes.DWORD()
                if not self.handle or not self.kernel.PeekNamedPipe(self.handle, None, 0, None, ctypes.byref(available), None):
                    raise OSError("Discord IPC disconnected")
                if not available.value:
                    stopped.wait(0.05)
                    continue
                length = min(size - len(data), available.value)
                buffer = ctypes.create_string_buffer(length)
                received = wintypes.DWORD()
                if not self.kernel.ReadFile(self.handle, buffer, length, ctypes.byref(received), None):
                    raise OSError("Discord IPC read failed")
                chunk = buffer.raw[:received.value]
            if not chunk:
                raise OSError("Discord IPC disconnected")
            data.extend(chunk)
        return bytes(data)

    def receive(self, deadline, stopped):
        opcode, length = struct.unpack("<II", self.read(8, deadline, stopped))
        if length > 1024 * 1024:
            raise ValueError("Discord IPC frame exceeded its limit")
        value = json.loads(self.read(length, deadline, stopped))
        if opcode != 3 and not isinstance(value, dict):
            raise ValueError("Invalid Discord IPC message")
        return opcode, value

    def readable(self):
        if self.socket is not None:
            return bool(select.select([self.socket], [], [], 0)[0])
        from ctypes import wintypes
        available = wintypes.DWORD()
        if not self.handle or not self.kernel.PeekNamedPipe(self.handle, None, 0, None, ctypes.byref(available), None):
            raise OSError("Discord IPC disconnected")
        return available.value >= 8

    def close(self):
        if self.socket is not None:
            self.socket.close()
            self.socket = None
        if self.handle:
            self.kernel.CancelIoEx(self.handle, None)
            self.kernel.CloseHandle(self.handle)
            self.handle = None


class Presence:
    def __init__(self, app_id, profile, transport=Transport, activity_provider=None, predecessor=None):
        if not re.fullmatch(r"[0-9]{17,20}", app_id):
            raise ValueError("Discord needs a valid public Application ID (17–20 digits).")
        self.app_id, self.profile = app_id, Path(profile)
        self.transport = transport
        self.activity_provider = activity_provider
        self.predecessor = predecessor
        self.message = "Connecting to Discord desktop…"
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.pipe = None

    def start(self):
        self.thread.start()

    def status(self, message):
        self.message = message
        data = {"message": message, "app_id": self.app_id}
        target = self.profile / "discord-status.json"
        try:
            temporary = target.with_name(target.name + "." + uuid.uuid4().hex + ".tmp")
            temporary.write_text(json.dumps(data), encoding="utf-8")
            os.replace(temporary, target)
        except OSError:
            pass

    def await_reply(self, nonce=None):
        deadline = time.monotonic() + 5
        while True:
            opcode, value = self.pipe.receive(deadline, self.stop)
            if opcode == 3:
                self.pipe.write(frame(4, value))
                continue
            if opcode == 2 or value.get("evt") == "ERROR":
                raise OSError("Discord rejected the application or activity")
            if opcode == 1 and ((nonce is None and value.get("evt") == "READY") or
                                (nonce is not None and value.get("nonce") == nonce)):
                return value

    def publish(self, activity):
        nonce = uuid.uuid4().hex
        self.pipe.write(frame(1, {"cmd": "SET_ACTIVITY", "args": {"pid": os.getpid(), "activity": activity}, "nonce": nonce}))
        self.await_reply(nonce)

    def run(self):
        if self.predecessor is not None:
            self.predecessor.close()
            self.predecessor = None
        while not self.stop.is_set():
            try:
                for index in range(10):
                    try:
                        self.pipe = self.transport(index)
                        break
                    except OSError:
                        continue
                if self.pipe is None:
                    raise OSError("Open Discord desktop to share activity")
                self.pipe.write(frame(0, {"v": 1, "client_id": self.app_id}))
                reply = self.await_reply()
                user = reply.get("data", {}).get("user", {})
                self.status("Connected to Discord as " + str(user.get("username") or "desktop user"))
                last, sent = object(), 0
                while not self.stop.wait(5):
                    if self.pipe.readable():
                        opcode, value = self.pipe.receive(time.monotonic() + 5, self.stop)
                        if opcode == 3:
                            self.pipe.write(frame(4, value))
                        elif opcode == 2 or value.get("evt") == "ERROR":
                            raise OSError("Discord IPC disconnected")
                    activity = self.activity_provider() if self.activity_provider else None
                    removing_game = (isinstance(last, dict) and
                        last.get("state") == "Playing on PlayStation 2" and
                        (not isinstance(activity, dict) or
                         activity.get("state") != "Playing on PlayStation 2"))
                    if activity != last and (activity is None or not sent or removing_game or
                                             time.monotonic() - sent >= 15):
                        self.publish(activity)
                        last, sent = activity, time.monotonic()
            except Exception:
                # Optional external IPC must never propagate into desktop/server control.
                if not self.stop.is_set():
                    self.status("Discord unavailable; retrying. Check the Application ID and open Discord desktop.")
            finally:
                if self.pipe:
                    if self.stop.is_set():
                        try:
                            self.pipe.write(frame(1, {"cmd": "SET_ACTIVITY", "args": {"pid": os.getpid(), "activity": None}, "nonce": uuid.uuid4().hex}))
                        except Exception:
                            pass
                    try:
                        self.pipe.close()
                    except Exception:
                        pass
                    self.pipe = None
            self.stop.wait(15)
        self.status("Discord activity stopped")

    def close(self):
        self.stop.set()
        if self.thread.is_alive():
            self.thread.join(timeout=5)
        if self.pipe:
            try:
                self.pipe.close()
            except Exception:
                pass
