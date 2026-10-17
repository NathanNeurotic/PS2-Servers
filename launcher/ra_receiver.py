"""Owned, transport-independent RA receiver state machine.

One event-loop owner calls receive/take_jobs/complete/clear. Workers return
identification results through that owner; they never mutate this object.
No sockets, account requests, memory evaluation or unlock submissions occur
here. The existing native service remains active until integration acceptance.
"""
from dataclasses import dataclass
import ipaddress
import time
import unicodedata

from launcher.ra_protocol import parse_request, pad_reply
from launcher.ra_watchlist import WatchSession, validate_watchlist


@dataclass(frozen=True)
class IdentificationJob:
    """Generation token carried through asynchronous account/set resolution."""

    token: int
    image_hash: str
    serial: str


@dataclass(frozen=True)
class ReceiverOutput:
    """At most one reply or one complete memory frame per input datagram."""

    reply: bytes = b""
    destination: tuple = ()
    values: bytes = b""
    generation: int = 0
    sequence: int = None


class Receiver:
    """Single-console lease, bounded identification work and watch transfer.

    Advertised reply endpoints are preserved for the pinned NAT contract.
    Hosts needing source-only routing may explicitly disable that policy.
    An unauthenticated discovery lease is not cryptographic authentication.
    """

    def __init__(self, clock=time.monotonic, lease_seconds=15,
                 identify_seconds=30, requests_per_second=40,
                 advertised_replies=True):
        if lease_seconds <= 0 or identify_seconds <= 0 or requests_per_second < 1:
            raise ValueError("Invalid receiver limits")
        self.clock = clock
        self.lease_seconds = lease_seconds
        self.identify_seconds = identify_seconds
        self.requests_per_second = requests_per_second
        self.advertised_replies = advertised_replies
        self.token = 0
        self.window = clock()
        self.requests = 0
        self.datagrams = 0
        self.destination = None
        self.acknowledged = set()
        self.session = WatchSession(clock=clock, timeout=lease_seconds)
        self.clear()

    def clear(self):
        """Revoke ownership and invalidate pending work on account/reset/stop."""
        self.token += 1
        self.owner = None
        self.destination = None
        self.acknowledged.clear()
        self.last_activity = None
        self.pending = None
        self.pending_since = None
        self.queued = None
        self.identified = None
        self.unsupported = False
        self.title = ""
        self.achievements = 0
        self.sent_chunks = set()
        self.session.clear()

    def _expire(self, now):
        if self.last_activity is not None and now - self.last_activity >= self.lease_seconds:
            self.clear()
        elif self.pending is not None and now - self.pending_since >= self.identify_seconds:
            self.token += 1
            self.pending = self.queued = None

    def take_jobs(self):
        """Drain at most one identification job; repeated requests never queue more."""
        self._expire(self.clock())
        job, self.queued = self.queued, None
        return (job,) if job is not None else ()

    def tick(self):
        """Expire idle ownership and identification without draining queued work."""
        self._expire(self.clock())

    def complete(self, job, watch_file=None, *, unsupported=False,
                 serial=None, title="", achievements=0):
        """Publish a current worker result; stale/account-switched results are ignored.

        No file and unsupported=False denotes a transient failure. A later
        request can retry it; a network failure must never become RAA1 NO.
        """
        self._expire(self.clock())
        if not isinstance(job, IdentificationJob) or self.pending != job or job.token != self.token:
            return False
        if watch_file is not None:
            if unsupported:
                raise ValueError("A supported set cannot also be unsupported")
            validate_watchlist(watch_file)
            if type(achievements) is not int or not 0 <= achievements <= 10000000:
                raise ValueError("Invalid achievement count")
            self.session.install(job.image_hash, serial or job.serial, watch_file)
            text = unicodedata.normalize("NFKD", str(title))
            self.title = "".join(c if " " <= c <= "~" else " "
                                 for c in text if not unicodedata.combining(c))[:63]
            self.achievements = achievements
        self.pending = self.queued = None
        self.identified = (job.image_hash, job.serial) if watch_file is not None or unsupported else None
        self.unsupported = unsupported
        return True

    def receive(self, packet, peer):
        """Process one input and return bounded effects for an external I/O loop."""
        now = self.clock()
        self._expire(now)
        if now - self.window >= 1:
            self.window, self.requests, self.datagrams = now, 0, 0
        if not isinstance(packet, bytes):
            return ReceiverOutput()
        try:
            source = str(ipaddress.IPv4Address(peer[0]))
            if type(peer[1]) is not int or not 1 <= peer[1] <= 65535:
                return ReceiverOutput()
        except (ValueError, TypeError, IndexError):
            return ReceiverOutput()
        if self.owner is not None and source != self.owner:
            return ReceiverOutput()
        self.datagrams += 1
        if self.datagrams > 256:
            return ReceiverOutput()
        if packet.startswith(b"RA15 "):
            if self.owner is None or self.session.watch is None:
                return ReceiverOutput()
            total = (len(self.session.watch.data) + 895) // 896
            if len(self.sent_chunks) != total:
                return ReceiverOutput()  # no telemetry before this list's transfer
            try:
                values = self.session.feed(packet)
            except ValueError:
                return ReceiverOutput()
            if values is not None:
                self.last_activity = now
                destination = self.destination or (source, peer[1])
                return ReceiverOutput(destination=destination, values=values, generation=self.session.generation,
                                      sequence=self.session.assembler.sequence)
            return ReceiverOutput()
        self.requests += 1
        if self.requests > self.requests_per_second:
            return ReceiverOutput()
        try:
            request = parse_request(packet, peer)
        except ValueError:
            return ReceiverOutput()
        if self.owner is None:
            if request.kind == "RAG1":
                return ReceiverOutput()
            self.owner = source
        self.last_activity = now
        destination = ((request.address, request.port) if self.advertised_replies
                       else (source, peer[1]))
        self.destination = destination
        if request.kind in ("RAH1", "RAK1", "RAK2"):
            if request.kind in ("RAK1", "RAK2"):
                for item in request.extra:
                    try:
                        self.acknowledged.add(int(item))
                    except ValueError:
                        pass
            return ReceiverOutput(destination=destination)
        if request.kind == "RAP1":
            return ReceiverOutput(pad_reply(b"RAO1 OK PS2-Servers/owned-protocol"), destination)
        if request.kind == "RAG1":
            try:
                reply = self.session.chunk(request.image_hash, request.index)
            except ValueError:
                return ReceiverOutput()
            if reply:
                self.sent_chunks.add(request.index)
                return ReceiverOutput(reply, destination)
            return ReceiverOutput()
        identity = (request.image_hash, request.serial)
        if self.identified == identity:
            if self.unsupported:
                reply = b"RAA1 NO"
            else:
                length = len(self.session.watch.data)
                reply = (f"RAA1 OK {length} {(length + 895) // 896} "
                         f"{self.achievements} 0 0 {self.title}").encode("ascii")
        else:
            if self.pending is None or (self.pending.image_hash, self.pending.serial) != identity:
                self.token += 1
                self.session.clear()
                self.sent_chunks.clear()
                self.identified = None
                self.unsupported = False
                self.pending = IdentificationJob(self.token, *identity)
                self.pending_since = now
                self.queued = self.pending
            reply = b"RAA1 WAIT"
        return ReceiverOutput(pad_reply(reply), destination)
