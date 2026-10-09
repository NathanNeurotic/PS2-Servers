"""Caduceus OPL's CADQ/CADA companion protocols, implemented from wire contracts.

The console receives account data and a share-scoped pairing capability, never
an RA password, login token, or Web API key. RA evaluation stays in rcheevos.
"""
import concurrent.futures
import ctypes
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import re
import secrets
import selectors
import socket
import threading
import time
import unicodedata
import urllib.parse
import urllib.request


def clean(value, limit):
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return "".join(c if " " <= c <= "~" else " " for c in text)[:limit]


def number(value):
    try:
        return max(0, min(10000000, int(value or 0)))
    except (TypeError, ValueError, OverflowError):
        return 0


def pad(body):
    data = body.encode("ascii")
    if len(data) >= 960:
        raise ValueError("Caduceus response exceeds the console receive buffer")
    return data + bytes(max(128, ((len(data) + 64) // 64) * 64) - len(data))


def read_secret(path):
    try:
        data = Path(path).read_bytes()
    except OSError:
        return ""
    if not data or len(data) > 4096:
        return ""
    if os.name == "nt":
        from ctypes import wintypes

        class Blob(ctypes.Structure):
            _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

        buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
        source, result = Blob(len(data), buffer), Blob()
        crypt = ctypes.WinDLL("crypt32", use_last_error=True)
        crypt.CryptUnprotectData.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p,
                                            ctypes.c_void_p, ctypes.c_void_p,
                                            ctypes.c_void_p, wintypes.DWORD,
                                            ctypes.POINTER(Blob)]
        crypt.CryptUnprotectData.restype = wintypes.BOOL
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        if not crypt.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
            return ""
        try:
            data = ctypes.string_at(result.data, result.size)
        finally:
            ctypes.memset(result.data, 0, result.size)
            kernel.LocalFree(result.data)
    try:
        return data.rstrip(b"\0\r\n").decode("utf-8")
    except UnicodeError:
        return ""


def pairing_key(art):
    art.mkdir(parents=True, exist_ok=True)
    target = art / "CADUCEUS.KEY"
    if target.exists():
        key = target.read_text("ascii").strip()
        if re.fullmatch("[a-f0-9]{64}", key):
            return key
        raise ValueError("ART/CADUCEUS.KEY is invalid; repair or remove it before pairing")
    key = secrets.token_hex(32)
    with open(target, "x", encoding="ascii", opener=lambda p, flags: os.open(p, flags, 0o600)) as output:
        output.write(key)
    return key


class Account:
    def __init__(self, profile, state):
        self.profile, self.state = profile, state

    def user(self):
        login = self.state().get("login", {})
        if not login.get("ok"):
            return ""
        credentials = read_secret(self.profile / "credentials").split("\n", 1)
        return credentials[0] if len(credentials) == 2 else ""

    def request(self, endpoint, **params):
        user, key = self.user(), read_secret(self.profile / "apikey")
        if not user or not key:
            raise ValueError("Sign in and enter a RetroAchievements Web API key on the account page")
        params.update(y=key)
        url = "https://retroachievements.org/API/" + endpoint + ".php?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, headers={"User-Agent": "PS2-Servers RetroAchievements"})
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            raise ValueError("RetroAchievements response exceeds the configured limit")
        result = json.loads(raw)
        if isinstance(result, dict) and (result.get("Success") is False or result.get("Error")):
            raise ValueError("RetroAchievements request failed")
        return result

    def resolve(self, image_hash):
        body = urllib.parse.urlencode({"r": "gameid", "m": image_hash}).encode("ascii")
        request = urllib.request.Request("https://retroachievements.org/dorequest.php", data=body)
        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.loads(response.read(65536))
        if not data.get("Success"):
            raise ValueError("Hash lookup failed")
        return number(data.get("GameID"))

    def game(self, game_id):
        return self.request("API_GetGameInfoAndUserProgress", u=self.user(), g=game_id)


class Bridge:
    def __init__(self, profile, folder):
        self.profile, self.art = Path(profile), Path(folder) / "ART"
        self.key = pairing_key(self.art)
        self.stop = threading.Event()
        self.state = {}
        self.lock = threading.RLock()
        self.account = Account(self.profile, self.get_state)
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="Caduceus API")
        self.slots = threading.BoundedSemaphore(32)
        self.pending = {}
        self.catalog = {}
        self.selector = selectors.DefaultSelector()
        self.sockets = []
        self.peer = ""

    def get_state(self):
        with self.lock:
            return self.state

    def start(self):
        try:
            for port in (18197, 18198):
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self.sockets.append(sock)
                if os.name == "nt":
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                sock.bind(("0.0.0.0", port))
                sock.setblocking(False)
                self.selector.register(sock, selectors.EVENT_READ, port)
        except BaseException:
            self.close()
            raise
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.events = threading.Thread(target=self.read_events, daemon=True)
        self.thread.start()
        self.events.start()

    def close(self):
        self.stop.set()
        for thread in (getattr(self, "thread", None), getattr(self, "events", None)):
            if thread:
                thread.join(timeout=2)
        for sock in self.sockets:
            sock.close()
        self.selector.close()
        self.pool.shutdown(wait=False, cancel_futures=True)

    def send(self, sock, peer, body):
        if not self.stop.is_set():
            try:
                sock.sendto(pad(body), peer)
            except OSError:
                pass

    def read_events(self):
        # Only events produced in this session can generate console notices.
        seen = set()
        while not self.stop.is_set():
            try:
                with urllib.request.urlopen("http://127.0.0.1:18196/events", timeout=3) as stream:
                    for line in stream:
                        if self.stop.is_set():
                            return
                        if not line.startswith(b"data:"):
                            continue
                        value = json.loads(line[5:])
                        if value.get("delta"):
                            continue
                        with self.lock:
                            self.state = value
                        for event in value.get("unlocks", []):
                            event_id = number(event.get("id"))
                            if event_id in seen:
                                continue
                            seen.add(event_id)
                            peer = self.peer or value.get("console", {}).get("ip", "")
                            if peer and event.get("ago", 999) <= 2:
                                body = "RAU1 {} {} {}".format(event_id, min(999999, number(event.get("points"))), clean(event.get("title"), 63))
                                self.send(self.sockets[0], (peer, 18195), body)
            except (OSError, ValueError):
                with self.lock:
                    self.state = {}
                self.stop.wait(0.5)

    def run(self):
        window, requests = time.monotonic(), 0
        while not self.stop.is_set():
            for selected, _ in self.selector.select(0.1):
                sock, port = selected.fileobj, selected.data
                try:
                    packet, peer = sock.recvfrom(1024)
                except OSError:
                    continue
                now = time.monotonic()
                if now - window >= 1:
                    window, requests = now, 0
                requests += 1
                if requests > 40:
                    continue
                with self.lock:
                    self.receive(sock, port, packet, peer, now)

    def receive(self, sock, port, packet, peer, now):
        for key, record in list(self.pending.items()):
            if now - record[0] > 30:
                del self.pending[key]
        if port == 18197:
            match = re.fullmatch(rb"CADQ([12]) ([a-f0-9]{32})", packet)
            if not match:
                return
            version, image_hash = (x.decode("ascii") for x in match.groups())
            self.peer = peer[0]
            ready = bool(self.get_state().get("login", {}).get("ok"))
            prefix = "CADR{} {} ".format(version, image_hash)
            if version == "2":
                prefix += "READY " if ready else "OFFLINE "
            cache = self.catalog.get(image_hash)
            if cache and now - cache[0] < 3600:
                self.send(sock, peer, prefix + cache[1])
                return
            self.send(sock, peer, prefix + "UNKNOWN")
            job_key = ("hash", image_hash)
            if job_key not in self.pending and len(self.pending) < 32:
                self.pending[job_key] = (now, None)
                self.submit(self.lookup, image_hash)
            return
        match = re.fullmatch(rb"CADA1 ([0-9]{1,10}) ([GA]) ([0-9]{1,6}) ([0-3]) ([0-9]{1,8}|[a-f0-9]{32}) ([a-f0-9]{64})", packet)
        if not match or not hmac.compare_digest(match[6].decode("ascii"), self.key):
            return
        nonce, kind, page, filter_id, target, _ = [x.decode("ascii") for x in match.groups()]
        prefix = "CADB1 {} ".format(nonce)
        user = self.account.user()
        if not user:
            self.send(sock, peer, prefix + "OFFLINE")
            return
        key = (peer, packet, user)
        record = self.pending.get(key)
        if record:
            self.send(sock, peer, record[1] or prefix + "WAIT")
            return
        if len(self.pending) >= 32:
            self.send(sock, peer, prefix + "BUSY")
            return
        self.pending[key] = (now, None)
        self.send(sock, peer, prefix + "WAIT")
        if not self.submit(self.progress, key, sock, peer, prefix, kind, int(page), int(filter_id), target, user):
            self.pending.pop(key, None)
            self.send(sock, peer, prefix + "BUSY")

    def submit(self, function, *args):
        if not self.slots.acquire(blocking=False):
            return False
        try:
            task = self.pool.submit(function, *args)
            task.add_done_callback(lambda _: self.slots.release())
        except RuntimeError:
            self.slots.release()
            return False
        return True

    def lookup(self, image_hash):
        try:
            game_id = self.account.resolve(image_hash)
            if not game_id:
                answer = "NO"
            else:
                game = self.account.game(game_id)
                count = number(game.get("NumAchievements"))
                answer = "OK {} {}".format(count, clean(game.get("Title"), 90)) if count else "NO"
                if count:
                    self.artwork(image_hash, game.get("ImageIcon"))
            with self.lock:
                if len(self.catalog) >= 4096:
                    self.catalog.clear()
                self.catalog[image_hash] = (time.monotonic(), answer)
        except (OSError, ValueError):
            pass  # UNKNOWN remains retryable; API failure is never NO.

    def progress(self, key, sock, peer, prefix, kind, page, filter_id, target, user):
        try:
            game_id, earned, maximum = 0, 0, 0
            if kind == "G":
                data = self.account.request("API_GetUserCompletionProgress", u=user, o=page * 3, c=3)
                total, title = number(data.get("Total")), "My library"
                rows = [{"id": x.get("GameID"), "total": x.get("MaxPossible"),
                         "earned": x.get("NumAwarded"), "hardcore": x.get("NumAwardedHardcore"),
                         "title": x.get("Title"), "description": x.get("ConsoleName"),
                         "image": x.get("ImageIcon"), "date": x.get("MostRecentAwardedDate")}
                        for x in data.get("Results", [])[:3]]
            else:
                game_id = self.account.resolve(target) if len(target) == 32 else number(target)
                if not game_id:
                    self.finish(key, sock, peer, prefix + "UNSUPPORTED", user)
                    return
                data = self.account.game(game_id)
                title = data.get("Title")
                rows = [{"id": x.get("ID"), "title": x.get("Title"), "description": x.get("Description"),
                         "points": x.get("Points"), "earned": bool(x.get("DateEarned") or x.get("DateEarnedHardcore")),
                         "hardcore": bool(x.get("DateEarnedHardcore")),
                         "date": x.get("DateEarned") or x.get("DateEarnedHardcore"),
                         "image": "https://media.retroachievements.org/Badge/{}.png".format(x.get("BadgeName"))}
                        for x in data.get("Achievements", {}).values()]
                maximum, earned = len(rows), sum(bool(x["earned"]) for x in rows)
                rows = [x for x in rows if not filter_id or
                        (bool(x["earned"]) if filter_id == 1 else not x["earned"] if filter_id == 2 else x["hardcore"])]
                total, rows = len(rows), rows[page * 3:page * 3 + 3]
            lines = []
            for entry in rows:
                image = entry.get("image")
                image = "https://media.retroachievements.org" + image if image and image.startswith("/") else image
                icon = hashlib.sha256(image.encode()).hexdigest()[:32] if image else "-"
                if image:
                    self.artwork(icon, image)
                lines.append("\t".join([str(number(entry.get(k))) for k in ("id", "total", "earned", "hardcore", "points")] +
                                       [icon, clean(entry.get("title"), 56), clean(entry.get("description"), 100), clean(entry.get("date"), 19)]))
            body = prefix + "OK\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\n".format(kind, page, total, game_id, earned, maximum, clean(user, 24), clean(title, 56)) + "\n".join(lines)
            self.finish(key, sock, peer, body, user)
        except (OSError, ValueError, TypeError, AttributeError):
            self.finish(key, sock, peer, prefix + "ERROR", user)

    def finish(self, key, sock, peer, body, user):
        if self.account.user() != user:
            body = "CADB1 {} OFFLINE".format(key[1].split()[1].decode("ascii"))
        with self.lock:
            self.pending[key] = (time.monotonic(), body)
            self.send(sock, peer, body)

    def artwork(self, key, image):
        if not image:
            return
        if image.startswith("/"):
            image = "https://media.retroachievements.org" + image
        url = urllib.parse.urlparse(image)
        if url.scheme != "https" or url.netloc != "media.retroachievements.org":
            return
        target = self.art / (key + "_RA.png")
        if target.exists():
            return
        try:
            from PIL import Image
            with urllib.request.urlopen(image, timeout=3.5) as response:
                if urllib.parse.urlparse(response.url).netloc != url.netloc:
                    return
                data = response.read(512 * 1024 + 1)
            if len(data) > 512 * 1024:
                return
            with Image.open(io.BytesIO(data)) as icon:
                if icon.width * icon.height > 4000000:
                    return
                icon = icon.convert("RGBA").resize((64, 64))
                temporary = target.with_suffix(".tmp")
                icon.save(temporary, format="PNG")
                os.replace(temporary, target)
        except (ImportError, OSError, ValueError):
            pass  # Artwork is optional; account/progress data remains usable.
