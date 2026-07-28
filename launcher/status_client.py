"""Ask a running server what state it is actually in.

The launcher has always shown two states per server, derived from whether the
child process is alive: green disc for running, grey ring for stopped. That is
cheap and it is often wrong in the two ways that matter.

A process can be alive and not serving. Pull the USB stick out and the share
root vanishes; the process keeps running and the launcher keeps showing green,
so the user goes looking for a network fault that is not there.

And a process being alive says nothing about whether it is in the middle of
something. Stopping a server, or quitting the launcher, while a console is
writing a save truncates that save. Until now the GUI had no way to know, so it
could not even warn.

The status protocol answers both. See docs/ROUTER-STATUS.md.

Only the UDPFS server implements it today. Nothing here assumes otherwise: a
server that does not answer is reported as UNKNOWN, which the caller must treat
as "fall back to the process check", never as "down".
"""

import importlib.util
import os
import socket
import threading

# No reply. Not the same as stopped: the server may simply be a build that
# predates the protocol, or one that never implemented it. A caller must fall
# back to whatever it knew before rather than reporting a fault.
UNKNOWN = "unknown"

_protocol = None
_protocol_lock = threading.Lock()


def protocol():
    """The shared router_status module, loaded from the server tree.

    Loaded by path rather than imported, and deliberately not duplicated here.
    The launcher and the server must agree byte for byte on this format, and
    the surest way to guarantee that is for there to be exactly one copy of it.
    The path is derived from the registry entry, so it resolves the same way in
    a packaged build as it does from source.
    """
    global _protocol
    with _protocol_lock:
        if _protocol is not None:
            return _protocol
        from launcher.servers import REGISTRY

        entry = REGISTRY.get("udpfs")
        if entry is None or not entry.module_file:
            return None
        path = os.path.join(os.path.dirname(entry.module_file), "router_status.py")
        if not os.path.isfile(path):
            return None
        spec = importlib.util.spec_from_file_location(
            "ps2servers_router_status", path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _protocol = module
        return _protocol


def query(host, port, timeout=0.4):
    """Ask one server for its status. Returns the decoded reply, or None.

    None means "did not answer, or answered with something that is not ours".
    Every failure funnels here rather than raising, because this is called from
    a poll loop where an exception would be a worse outcome than a missing
    answer.
    """
    proto = protocol()
    if proto is None or not port:
        return None
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        sock.sendto(proto.build_status_query(), (host, int(port)))
        data, _ = sock.recvfrom(2048)
        return proto.parse_status_reply(data)
    except (OSError, ValueError):
        # Includes the timeout, a refused port, and a host that will not
        # resolve. All of them mean the same thing to a caller: no answer.
        return None
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


def is_busy(status):
    """True only when a server has positively said it is mid-transfer.

    Written so that UNKNOWN and None are not busy. A warning that fires when
    the launcher simply could not reach a server would be ignored within a day,
    and then it would not be there for the case that matters.
    """
    proto = protocol()
    if not status or proto is None:
        return False
    return status.get("state") == proto.STATE_BUSY


class Poller:
    """Polls the servers the launcher is running, off the GUI thread.

    Tk has one thread and a blocked socket in the middle of a periodic callback
    freezes the window. So the waiting happens here and the GUI only ever reads
    the last answer, which is at worst one interval stale -- fine for something
    a person is looking at, and never a stall.
    """

    def __init__(self, interval=1.0, timeout=0.4):
        self.interval = max(0.2, float(interval))
        self.timeout = max(0.05, float(timeout))
        self._targets = {}
        self._results = {}
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = None

    def set_target(self, key, host, port):
        """Start polling one server. Replaces any previous target for `key`."""
        with self._lock:
            self._targets[key] = (host, int(port))
        # Poll immediately rather than waiting out the interval: this is called
        # when a server starts, which is exactly when someone is watching.
        self._wake.set()

    def clear_target(self, key):
        with self._lock:
            self._targets.pop(key, None)
            self._results.pop(key, None)

    def snapshot(self):
        """The most recent answer per key. UNKNOWN where there was none."""
        with self._lock:
            return dict(self._results)

    def status(self, key):
        with self._lock:
            return self._results.get(key)

    def start(self):
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="status-poller", daemon=True)
        self._thread.start()

    def stop(self, join_timeout=1.0):
        self._stop.set()
        self._wake.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=join_timeout)

    def poll_once(self):
        """One sweep. Separated from the loop so tests need no threads."""
        with self._lock:
            targets = dict(self._targets)
        for key, (host, port) in targets.items():
            reply = query(host, port, timeout=self.timeout)
            with self._lock:
                # Only record for targets that still exist: a server stopped
                # mid-sweep must not have a stale answer reinstated behind
                # clear_target.
                if key in self._targets:
                    self._results[key] = reply if reply else {"state_name": UNKNOWN}

    def _run(self):
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:
                # A poller that dies takes the launcher's status display with
                # it, silently. Nothing here is important enough to be worth
                # that, so every sweep is allowed to fail on its own.
                pass
            self._wake.wait(self.interval)
            self._wake.clear()
