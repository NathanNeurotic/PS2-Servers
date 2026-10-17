"""Opt-in UDP transport and single-owner evaluation/account orchestration.

The resolver supervisor runs on one daemon worker. Use ProcessResolver or
AccountWorker for external identification: they enforce cancellation and a hard
process deadline. Direct callbacks are only suitable for bounded/cooperative
in-process work. stop() reports outstanding work rather than accumulating
replacement workers. Not selected by the active managed service yet.
"""
from dataclasses import dataclass
import queue
import socket
import threading

from launcher.ra_evaluated_receiver import EvaluatedReceiver
from launcher.ra_receiver import Receiver
from launcher.ra_sets import QualifiedSet


@dataclass(frozen=True)
class IdentificationResult:
    watch_file: bytes = None
    unsupported: bool = False
    serial: str = None
    title: str = ""
    achievements: int = 0
    definitions: tuple = None
    qualified: QualifiedSet = None
    profile: str = "opl"


class OwnedService:
    """One UDP owner, one resolver and bounded FIFOs of frames and evaluations.

    resolve(job, cancel_event) returns IdentificationResult (or QualifiedSet /
    AccountResult when library is configured). Exceptions and invalid results
    are transient failures, never declarations of no support. Lifecycle calls
    are serialized; frame/evaluation reads and account/reset commands may run
    concurrently from callers while the UDP owner thread commits all state.
    """

    def __init__(self, resolve, address=("127.0.0.1", 0), frame_capacity=256,
                 library=None, profile=None, **receiver_options):
        if type(frame_capacity) is not int or not 1 <= frame_capacity <= 1024:
            raise ValueError("Invalid frame queue capacity")
        if profile is not None and library is None:
            raise ValueError("Account profile requires an evaluator library")
        self.resolve = resolve
        self.address = address
        self.library = library
        self.profile = profile
        self.receiver_options = receiver_options
        self._loop = self._worker = None
        self._frames = queue.Queue(maxsize=frame_capacity)
        self._evaluations = queue.Queue(maxsize=frame_capacity)
        self.dropped_frames = 0
        self.dropped_evaluations = 0
        self._reset = threading.Event()
        self._frame_lock = threading.Lock()
        self._frame_generation = None
        self._active_job = None
        self._account = None
        self._account_epoch = 0
        self._commands = queue.Queue()

    @property
    def account(self):
        with self._frame_lock:
            return self._account

    @property
    def account_epoch(self):
        with self._frame_lock:
            return self._account_epoch

    def _build_receiver(self):
        if self.library is not None:
            return EvaluatedReceiver(self.library, **self.receiver_options)
        return Receiver(**self.receiver_options)

    def start(self):
        if any(t is not None and t.is_alive() for t in (self._loop, self._worker)):
            raise RuntimeError("Previous owned service has not stopped")
        # Validate before binding or starting either thread. The receiver and
        # optional AccountSession are subsequently owned exclusively by the UDP loop.
        receiver = self._build_receiver()
        session = None
        if self.profile is not None:
            from launcher.ra_account_worker import AccountSession
            from launcher.ra_credentials import CredentialStore
            session = AccountSession(receiver, CredentialStore(self.profile))
        listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            listener.bind(self.address)
            listener.settimeout(0.02)
        except BaseException:
            if hasattr(receiver, "close"):
                receiver.close()
            listener.close()
            raise
        self.bound_address = listener.getsockname()
        self._stop = threading.Event()
        self._reset.clear()
        self._jobs = queue.Queue(maxsize=1)
        self._results = queue.Queue(maxsize=1)
        with self._frame_lock:
            self._discard_frame()
            self.dropped_frames = 0
            self.dropped_evaluations = 0
        self._worker = threading.Thread(target=self._resolve, daemon=True,
                                        name="RA owned resolver")
        self._loop = threading.Thread(target=self._serve,
                                      args=(listener, receiver, session),
                                      daemon=True, name="RA owned UDP")
        self._worker.start()
        self._loop.start()
        return self.bound_address

    def _discard_frame(self):
        self._frame_generation = None
        for fifo in (self._frames, self._evaluations):
            while True:
                try:
                    fifo.get_nowait()
                except queue.Empty:
                    break

    def reset(self):
        """Immediately hide queued frames, then revoke state on the UDP owner."""
        with self._frame_lock:
            if self._active_job is not None:
                self._active_job[1].set()
            self._reset.set()
            self._discard_frame()

    def bind_account(self, account, epoch):
        """Bind a non-secret account identity and epoch on the UDP owner thread."""
        if self.library is None:
            raise RuntimeError("Account binding requires an evaluator library")
        if (type(account) is not str or not 1 <= len(account) <= 64 or
                any(c in account for c in "\0\r\n") or
                type(epoch) is not int or not 1 <= epoch <= 0xFFFFFFFF):
            raise ValueError("Invalid account identity or epoch")
        with self._frame_lock:
            if (account, epoch) != (self._account, self._account_epoch):
                if self._active_job is not None:
                    self._active_job[1].set()
                self._reset.set()
                self._discard_frame()
            self._account = account
            self._account_epoch = epoch
            self._commands.put_nowait(("bind_account", account, epoch))

    def clear_account(self, *, forget=False):
        """Log out and revoke active evaluation/account state on the UDP owner."""
        if type(forget) is not bool:
            raise ValueError("Invalid credential removal choice")
        if forget and self.profile is None:
            raise RuntimeError("Credential removal requires an account profile")
        with self._frame_lock:
            if self._active_job is not None:
                self._active_job[1].set()
            self._reset.set()
            self._discard_frame()
            self._account = None
            self._account_epoch += 1
            self._commands.put_nowait(("clear_account", forget))

    def run_account(self, callback, timeout=2):
        """Execute callback(session) on the UDP owner thread and return its result."""
        if self.profile is None:
            raise RuntimeError("Account session requires an account profile")
        if self._loop is None or not self._loop.is_alive():
            raise RuntimeError("Owned service is not running")
        done = threading.Event()
        box = []
        with self._frame_lock:
            self._commands.put_nowait(("session", callback, done, box))
        if not done.wait(timeout):
            raise TimeoutError("Timed out waiting for UDP owner thread")
        ok, payload = box[0]
        if not ok:
            raise payload
        return payload

    def take_frame(self):
        """Return the oldest queued complete frame, or None; never block.

        On overflow the backlog is discarded and the new frame retained.
        Its sequence exposes the discontinuity to the evaluator, which must
        reset temporal history. dropped_frames makes this loss observable.
        """
        with self._frame_lock:
            if self._reset.is_set():
                return None
            try:
                return self._frames.get_nowait()
            except queue.Empty:
                return None

    def take_evaluation(self):
        """Return the oldest queued EvaluatedOutput, or None; never block."""
        with self._frame_lock:
            if self._reset.is_set():
                return None
            try:
                return self._evaluations.get_nowait()
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

    @staticmethod
    def _normalize_result(result):
        if isinstance(result, IdentificationResult):
            return result
        if isinstance(result, QualifiedSet):
            return IdentificationResult(qualified=result)
        from launcher.ra_account_worker import AccountResult, valid_account_result
        if isinstance(result, AccountResult):
            if valid_account_result(result) and result.status == "ok" and result.qualified is not None:
                return IdentificationResult(qualified=result.qualified)
            return IdentificationResult()
        return IdentificationResult()

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
            cancel = threading.Event()
            with self._frame_lock:
                self._active_job = (job.token, cancel)
                if stop.is_set():
                    cancel.set()
            try:
                result = self._normalize_result(self.resolve(job, cancel))
            except Exception:
                result = IdentificationResult()
            finally:
                with self._frame_lock:
                    self._active_job = None
            while not stop.is_set():
                try:
                    results.put((job, result), timeout=0.02)
                    break
                except queue.Full:
                    continue

    @staticmethod
    def _raw_receiver(receiver):
        return receiver._receiver if isinstance(receiver, EvaluatedReceiver) else receiver

    @staticmethod
    def _complete_result(receiver, job, result):
        if isinstance(receiver, EvaluatedReceiver):
            if result.qualified is not None:
                if result.watch_file is not None or result.unsupported or result.definitions is not None:
                    raise ValueError("Conflicting qualified identification result")
                receiver.complete_set(job, result.qualified, profile=result.profile)
                return
            definitions = dict(result.definitions) if result.definitions is not None else None
            receiver.complete(job, result.watch_file, definitions,
                              unsupported=result.unsupported, serial=result.serial,
                              title=result.title, achievements=result.achievements)
            return
        if result.qualified is not None or result.definitions is not None:
            raise ValueError("Evaluated identification result requires an evaluator library")
        receiver.complete(job, result.watch_file,
                          unsupported=result.unsupported,
                          serial=result.serial, title=result.title,
                          achievements=result.achievements)

    def _drain_commands(self, receiver, session):
        while True:
            try:
                command = self._commands.get_nowait()
            except queue.Empty:
                break
            kind = command[0]
            if kind == "bind_account":
                _, account, epoch = command
                if isinstance(receiver, EvaluatedReceiver):
                    receiver.bind_account(account, epoch)
                self._account = account
                self._account_epoch = epoch
            elif kind == "clear_account":
                _, forget = command
                if session is not None:
                    session.logout(forget=forget)
                    self._account = session.username
                    self._account_epoch = session._epoch
                elif isinstance(receiver, EvaluatedReceiver):
                    receiver.clear_account()
                    self._account = None
                else:
                    receiver.clear()
                    self._account = None
            elif kind == "session":
                _, callback, done, box = command
                try:
                    value = callback(session)
                    box.append((True, value))
                except Exception as exc:
                    box.append((False, exc))
                finally:
                    self._account = session.username
                    self._account_epoch = session._epoch
                    if self._active_job is not None and (
                            self._active_job[0] != self._raw_receiver(receiver).token or
                            self._account is None):
                        self._active_job[1].set()
                    if self._frame_generation != self._raw_receiver(receiver).session.generation:
                        self._discard_frame()
                    done.set()

    def _serve(self, listener, receiver, session):
        raw = self._raw_receiver(receiver)
        try:
            while not self._stop.is_set():
                with self._frame_lock:
                    if self._reset.is_set():
                        if isinstance(receiver, EvaluatedReceiver):
                            receiver.reset()
                        else:
                            receiver.clear()
                        self._discard_frame()
                        self._reset.clear()
                    self._drain_commands(receiver, session)
                try:
                    job, result = self._results.get_nowait()
                except queue.Empty:
                    pass
                else:
                    try:
                        self._complete_result(receiver, job, result)
                    except (ValueError, TypeError):
                        receiver.complete(job)  # invalid set remains retryable
                # Leave the receiver's newest job queued until capacity exists.
                if not self._jobs.full():
                    for job in receiver.take_jobs():
                        self._jobs.put_nowait(job)
                else:
                    receiver.tick()
                with self._frame_lock:
                    if self._active_job is not None and self._active_job[0] != raw.token:
                        self._active_job[1].set()
                    if self._frame_generation != raw.session.generation:
                        self._discard_frame()
                try:
                    packet, peer = listener.recvfrom(1473)
                except (socket.timeout, ConnectionResetError, ConnectionRefusedError):
                    # Windows can report an earlier UDP send's ICMP rejection
                    # on recvfrom. A departed reply endpoint is not fatal to
                    # the listener or a new console's discovery.
                    continue
                if len(packet) > 1472:
                    continue
                received = receiver.receive(packet, peer)
                if isinstance(receiver, EvaluatedReceiver):
                    output, evaluated = received.wire, received
                else:
                    output, evaluated = received, None
                with self._frame_lock:
                    # A game switch revokes even a completed, unread old frame.
                    if self._frame_generation != raw.session.generation:
                        self._discard_frame()
                if output.reply:
                    try:
                        listener.sendto(output.reply, output.destination)
                    except OSError:
                        pass  # a failed datagram does not kill discovery
                if output.values:
                    with self._frame_lock:
                        if not self._reset.is_set():
                            if self._frames.full():
                                self.dropped_frames += self._frames.qsize()
                                while True:
                                    try:
                                        self._frames.get_nowait()
                                    except queue.Empty:
                                        break
                            self._frames.put_nowait(output)
                            if evaluated is not None:
                                if self._evaluations.full():
                                    self.dropped_evaluations += self._evaluations.qsize()
                                    while True:
                                        try:
                                            self._evaluations.get_nowait()
                                        except queue.Empty:
                                            break
                                self._evaluations.put_nowait(evaluated)
                            self._frame_generation = output.generation
        finally:
            with self._frame_lock:
                self._drain_commands(receiver, session)
                if session is not None:
                    session.logout()
                if isinstance(receiver, EvaluatedReceiver):
                    receiver.close()
                else:
                    receiver.clear()
                self._account = None
                self._account_epoch = 0
                if self._active_job is not None:
                    self._active_job[1].set()
                self._discard_frame()
            listener.close()
            self._stop.set()

