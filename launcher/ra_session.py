"""Passive, bounded RA session polling; never runs network I/O on the Tk thread.

A running achievement process is not proof that the console is connected.
Only advancing console packet counters keep a game session in 'playing'.
No file scanning and no calls into SMB/UDPFS hot paths are needed.
"""
import json
import threading
import time
import urllib.error
import urllib.request

from .ra_notifications import UnlockTracker


def safe_title(text):
    return " ".join(str(text or "").split())[:96]


class SessionTracker:
    """Classify the xeRAbora-compatible /state snapshot without false activity."""

    def __init__(self, stale_seconds=15):
        self.stale_seconds = stale_seconds
        self.last_packets = None
        self.last_advance = None
        self.session_number = 0
        self.active_identity = None

    def reset(self):
        self.last_packets = None
        self.last_advance = None
        self.active_identity = None

    def observe(self, state, now=None):
        now = time.monotonic() if now is None else now
        if not isinstance(state, dict):
            self.reset()
            return {"state": "unreachable", "text": "RA engine unavailable"}
        login = state.get("login") if isinstance(state.get("login"), dict) else {}
        console = state.get("console") if isinstance(state.get("console"), dict) else {}
        game = state.get("game") if isinstance(state.get("game"), dict) else {}
        connected = console.get("connected") is True
        packets = console.get("packets")
        if not isinstance(packets, int) or isinstance(packets, bool) or packets < 0:
            packets = None
        if not connected:
            self.reset()
            return {"state": "listening", "text": (
                "RA signed in · Waiting for PS2" if login.get("ok")
                else "RA sign-in required · Waiting for PS2")}
        advanced = (packets is not None and self.last_packets is not None
                    and packets > self.last_packets)
        if packets is not None:
            if advanced:
                self.last_advance = now
            elif self.last_packets is not None and packets < self.last_packets:
                # An engine restart resets the counter; wait for new packets.
                self.last_advance = None
            self.last_packets = packets
        if (packets is not None and self.last_advance is not None and
                now - self.last_advance >= self.stale_seconds):
            self.active_identity = None
            return {"state": "stalled", "text": "PS2 telemetry stalled · Check connection"}
        if packets is None:
            self.active_identity = None
            return {"state": "connected", "text": "PS2 connected · Telemetry unverified"}
        if self.last_advance is None:
            self.active_identity = None
            # The first poll may read a stale connected flag and old game name.
            # Do not publish a game until at least two counters advance.
            return {"state": "connected", "text": "PS2 connected · Awaiting fresh telemetry"}
        # The upstream /state may show the last *checked* game even while a
        # different image is streaming. It only fills game.serial when the
        # active console hash matches the loaded achievement set.
        serial = safe_title(game.get("serial"))
        if not serial:
            self.active_identity = None
            return {"state": "connected", "text": "PS2 connected · No verified tracked game"}
        title = safe_title(game.get("title"))
        if not title:
            self.active_identity = None
            return {"state": "connected", "text": "PS2 connected · Awaiting tracked game"}
        identity = (serial, str(game.get("hash") or ""), title)
        if identity != self.active_identity:
            # The engine can change the displayed/checked set without sending
            # memory from it. Demand a fresh packet on every identity change.
            if not advanced:
                self.active_identity = None
                return {"state": "connected", "text": "PS2 connected · Awaiting new-game telemetry"}
            self.session_number += 1
            self.active_identity = identity
        return {"state": "playing", "title": title, "packets": packets,
                "session": self.session_number,
                "text": "Now playing: {} · {} packets".format(title, packets)}


class SessionPoller:
    def __init__(self, url=None, fetch=None, period=1.5):
        self.url = url
        self.fetch = fetch or self._fetch
        self.period = period
        self.tracker = SessionTracker()
        self.unlock_tracker = UnlockTracker()
        self._unlocks = []
        self._stop = threading.Event()
        self._enabled = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self._result = {"state": "stopped", "text": "RA stopped"}

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True,
                                            name="PS2-Servers RA session")
            self._thread.start()

    def set_running(self, running):
        if running:
            self._enabled.set()
        else:
            self._enabled.clear()
            with self._lock:
                self._unlocks.clear()

    def snapshot(self):
        with self._lock:
            return dict(self._result)

    def take_unlocks(self):
        """Drain the capped worker queue on Tk's thread without I/O."""
        with self._lock:
            events = self._unlocks[:]
            self._unlocks.clear()
            return events

    def _fetch(self):
        if self.url is None:
            from launcher.achievements import account_url
            url = account_url() + "state"
        else:
            url = self.url
        with urllib.request.urlopen(url, timeout=2) as response:
            data = response.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024:
            raise ValueError("RA status reply exceeded limit.")
        return json.loads(data)

    def _run(self):
        was_enabled = False
        while not self._stop.is_set():
            if not self._enabled.is_set():
                if was_enabled:
                    self.tracker.reset()
                    self.unlock_tracker.reset()
                    with self._lock:
                        self._unlocks.clear()
                        self._result = {"state": "stopped", "text": "RA stopped"}
                was_enabled = False
                self._stop.wait(0.2)
                continue
            was_enabled = True
            try:
                engine_state = self.fetch()
                snapshot = self.tracker.observe(engine_state)
                events = self.unlock_tracker.observe(engine_state, snapshot)
            except (OSError, ValueError, TypeError, urllib.error.URLError):
                snapshot = self.tracker.observe(None)
                self.unlock_tracker.reset()
                events = []
            with self._lock:
                # A stop during an in-flight HTTP read must never publish
                # stale unlocks into the subsequent service session.
                if self._enabled.is_set():
                    self._result = snapshot
                    self._unlocks.extend(events)
                    del self._unlocks[:-8]
            self._stop.wait(self.period)

    def close(self):
        self._stop.set()
        self._enabled.clear()
        with self._lock:
            self._unlocks.clear()
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None
