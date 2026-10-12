"""Opt-in UDP transport for the owned receiver; not active service routing.

The resolver runs on one daemon worker and must honor its cancellation event
and impose timeouts on external I/O. Python cannot forcibly stop a callback.
stop() closes the listener even if a resolver misbehaves, reports that worker,
and start() refuses to accumulate replacement workers while it remains alive.
"""
from dataclasses import dataclass
import queue
import socket
import threading

from launcher.ra_receiver import Receiver


@dataclass(frozen=True)
class IdentificationResult:
    watch_file: bytes = None
    unsupported: bool = False
    serial: str = None
    title: str = ""
    achievements: int = 0


class OwnedService:
    """One UDP owner, one resolver and one latest-frame slot.

    resolve(job, cancel_event) returns IdentificationResult. Exceptions and
    invalid results are transient failures, never declarations of no support.
    Lifecycle calls are serialized; frame reads and reset may run concurrently.
    No account credentials or native evaluator are wired here yet.
    """

    def __init__(self, resolve, address=("127.0.0.1", 0), **receiver_options):
        self.resolve = resolve
        self.address = address
        self.receiver_options = receiver_options
        self._loop = self._worker = None
        self._frames = queue.Queue(maxsize=1)
        self._reset = threading.Event()
        self._frame_lock = threading.Lock()
        self._frame_generation = None

    def start(self):
        if any(t is not None and t.is_alive() for t in (self._loop, self._worker)):
            raise RuntimeError("Previous owned service has not stopped")
        listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            listener.bind(self.address)
            listener.settimeout(0.02)
        except BaseException:
            listener.close()
            raise
        self.bound_address = listener.getsockname()
        self._stop = threading.Event()
        self._reset.clear()
        self._jobs = queue.Queue(maxsize=1)
        self._results = queue.Queue(maxsize=1)
        with self._frame_lock:
            self._discard_frame()
        self._worker = threading.Thread(target=self._resolve, daemon=True,
                                        name="RA owned resolver")
        self._loop = threading.Thread(target=self._serve, args=(listener,), daemon=True,
                                      name="RA owned UDP")
        self._worker.start()
        self._loop.start()
        return self.bound_address

    def _discard_frame(self):
        self._frame_generation = None
        try:
            self._frames.get_nowait()
        except queue.Empty:
            pass

    def reset(self):
        """Immediately hide queued frames, then revoke state on the UDP owner."""
        with self._frame_lock:
            self._reset.set()
            self._discard_frame()

    def take_frame(self):
        """Return the latest complete ReceiverOutput, or None; never block."""
        with self._frame_lock:
            if self._reset.is_set():
                return None
            try:
                return self._frames.get_nowait()
            except queue.Empty:
                return None

    def stop(self, timeout=1):
        """Return whether BOTH threads stopped; listener closes independently."""
        if self._loop is None:
            return True
        self._stop.set()
        self.reset()
        self._loop.join(timeout)
        self._worker.join(timeout)
        return not self._loop.is_alive() and not self._worker.is_alive()

    def _resolve(self):
        # Capture this run's objects; restart cannot redirect an old worker.
        stop, jobs, results = self._stop, self._jobs, self._results
        while not stop.is_set():
            try:
                job = jobs.get(timeout=0.02)
            except queue.Empty:
                continue
            if stop.is_set():
                break
            try:
                result = self.resolve(job, stop)
                if not isinstance(result, IdentificationResult):
                    result = IdentificationResult()
            except Exception:
                result = IdentificationResult()
            while not stop.is_set():
                try:
                    results.put((job, result), timeout=0.02)
                    break
                except queue.Full:
                    continue

    def _serve(self, listener):
        receiver = Receiver(**self.receiver_options)
        try:
            while not self._stop.is_set():
                with self._frame_lock:
                    if self._reset.is_set():
                        receiver.clear()
                        self._discard_frame()
                        self._reset.clear()
                try:
                    job, result = self._results.get_nowait()
                except queue.Empty:
                    pass
                else:
                    try:
                        receiver.complete(job, result.watch_file,
                                          unsupported=result.unsupported,
                                          serial=result.serial, title=result.title,
                                          achievements=result.achievements)
                    except (ValueError, TypeError):
                        receiver.complete(job)  # invalid set remains retryable
                # Leave the receiver's newest job queued until capacity exists.
                if not self._jobs.full():
                    for job in receiver.take_jobs():
                        self._jobs.put_nowait(job)
                else:
                    receiver.tick()
                with self._frame_lock:
                    if self._frame_generation != receiver.session.generation:
                        self._discard_frame()
                try:
                    packet, peer = listener.recvfrom(1473)
                except socket.timeout:
                    continue
                if len(packet) > 1472:
                    continue
                output = receiver.receive(packet, peer)
                with self._frame_lock:
                    # A game switch revokes even a completed, unread old frame.
                    if self._frame_generation != receiver.session.generation:
                        self._discard_frame()
                if output.reply:
                    try:
                        listener.sendto(output.reply, output.destination)
                    except OSError:
                        pass  # a failed datagram does not kill discovery
                if output.values:
                    with self._frame_lock:
                        if not self._reset.is_set():
                            self._discard_frame()
                            self._frames.put_nowait(output)
                            self._frame_generation = output.generation
        finally:
            receiver.clear()
            listener.close()
            self._stop.set()
