"""Bounded per-client session counters, summarised once per session.

Opt-in (``--session-summary``). Nothing is logged per request: each event is
one counter update under a lock, and a client's totals are printed as one
``SESSION`` line when it has been quiet for ``idle_seconds``, when it is pushed
out by a newer client past ``max_clients``, or when the server stops.

The line is a contract the launcher can read::

    SESSION <service> client=<ip> seconds=<n> connections=<n> list=<n> open=<n>
            read=<n> bytes=<n> write=<n> close=<n> errors=<n> ended=<idle|evicted|stopped>

Counts describe what the host saw. A read the host served does not prove that
the game ran, and a session with no errors does not prove gameplay worked.
"""

from collections import OrderedDict
import threading
import time

KINDS = ("connections", "list", "open", "read", "bytes", "write", "close", "errors")
MAX_CLIENTS = 64
IDLE_SECONDS = 60.0


class _Client:
    __slots__ = ("first", "last") + KINDS

    def __init__(self, now):
        self.first = self.last = now
        for kind in KINDS:
            setattr(self, kind, 0)


class SessionStats:
    def __init__(self, service, *, max_clients=MAX_CLIENTS, idle_seconds=IDLE_SECONDS,
                 clock=time.monotonic, emit=print):
        if max_clients < 1:
            raise ValueError("max_clients must be at least 1")
        self.service = service
        self.max_clients = max_clients
        self.idle_seconds = float(idle_seconds)
        self._clock = clock
        self._emit = emit
        self._lock = threading.Lock()
        self._clients = OrderedDict()  # least recently seen first

    def record(self, client, kind, count=1):
        """Add count to one counter for this client. Unknown kinds are a bug."""
        if kind not in KINDS:
            raise ValueError(kind)
        now = self._clock()
        evicted = None
        with self._lock:
            entry = self._clients.get(client)
            if entry is None:
                if len(self._clients) >= self.max_clients:
                    evicted = self._clients.popitem(last=False)
                entry = self._clients[client] = _Client(now)
            else:
                self._clients.move_to_end(client)
            entry.last = now
            setattr(entry, kind, getattr(entry, kind) + count)
        if evicted is not None:
            self._emit(self._line(evicted[0], evicted[1], "evicted"))

    def expire(self):
        """Summarise and forget every client idle for idle_seconds. Returns the lines."""
        now = self._clock()
        done = []
        with self._lock:
            for client in list(self._clients):
                entry = self._clients[client]
                if now - entry.last < self.idle_seconds:
                    break  # ordered by last seen, so the rest are newer
                done.append((client, self._clients.pop(client)))
        lines = [self._line(c, e, "idle") for c, e in done]
        for line in lines:
            self._emit(line)
        return lines

    def stop(self):
        """Summarise every remaining client. Returns the lines."""
        with self._lock:
            done = list(self._clients.items())
            self._clients.clear()
        lines = [self._line(c, e, "stopped") for c, e in done]
        for line in lines:
            self._emit(line)
        return lines

    def active(self):
        with self._lock:
            return len(self._clients)

    def _line(self, client, entry, ended):
        fields = " ".join("{}={}".format(k, getattr(entry, k)) for k in KINDS)
        return "SESSION {} client={} seconds={} {} ended={}".format(
            self.service, client, int(entry.last - entry.first), fields, ended)

    def run_expiry(self, stop_event, interval=10.0):
        """Call expire() every interval until stop_event is set (for a daemon thread)."""
        while not stop_event.wait(interval):
            self.expire()


def parse_line(line):
    """The fields of a SESSION line as a dict, or None if it is not one."""
    parts = str(line).strip().split()
    if len(parts) < 3 or parts[0] != "SESSION":
        return None
    out = {"service": parts[1]}
    for part in parts[2:]:
        key, sep, value = part.partition("=")
        if not sep:
            return None
        out[key] = int(value) if value.isdigit() else value
    return out
