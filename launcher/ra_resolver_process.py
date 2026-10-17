"""Killable identification boundary using Windows-compatible spawn semantics.

Resolvers must be importable module-level callables with picklable state.
The application must enable multiprocessing.freeze_support in its entry point
before selecting this adapter in a frozen build. No credentials go on argv.
"""
import multiprocessing
import io
import pickle
import queue
import threading
import time

IPC_LIMIT = 8 * 1024 * 1024


def valid_identification_result(result):
    """Keep the original bounded identification IPC contract as the default."""
    from launcher.ra_owned_service import IdentificationResult
    from launcher.ra_account_worker import AccountResult, valid_account_result
    from launcher.ra_sets import QualifiedSet, SetContext
    if not isinstance(result, IdentificationResult):
        return False
    if not ((result.watch_file is None or (type(result.watch_file) is bytes and len(result.watch_file) <= 17432)) and
            type(result.title) is str and len(result.title) <= 4096 and
            (result.serial is None or (type(result.serial) is str and len(result.serial) <= 64)) and
            type(result.achievements) is int and 0 <= result.achievements <= 10000000 and
            type(result.unsupported) is bool and
            type(result.profile) is str and result.profile in ("opl", "orbit-v1.0.2")):
        return False
    if result.qualified is not None:
        if (result.watch_file is not None or result.unsupported or result.definitions is not None or
                type(result.qualified) is not QualifiedSet or
                type(result.qualified.context) is not SetContext or
                not valid_account_result(AccountResult(1, result.qualified.context.epoch, "ok",
                                                       qualified=result.qualified))):
            return False
    if result.definitions is not None:
        if (result.watch_file is None or result.unsupported or
                type(result.definitions) is not tuple or not 1 <= len(result.definitions) <= 4096):
            return False
        total = 0
        seen = set()
        for pair in result.definitions:
            if type(pair) is not tuple or len(pair) != 2:
                return False
            identifier, expression = pair
            if (type(identifier) is not int or not 1 <= identifier <= 0xFFFFFFFF or identifier in seen or
                    type(expression) is not str or not 1 <= len(expression) <= 16384 or "\0" in expression):
                return False
            try:
                total += len(expression.encode("ascii"))
            except UnicodeError:
                return False
            seen.add(identifier)
        if total > 4 * 1024 * 1024:
            return False
    return True


def _run(resolve, job, cancel, connection, validate):
    try:
        result = resolve(job, cancel)
        # Bound IPC before serializing; a callback cannot send arbitrary objects
        # or an unbounded response into the receiving supervisor thread.
        if not validate(result):
            return
        payload = pickle.dumps(result, protocol=pickle.HIGHEST_PROTOCOL)
        if len(payload) <= IPC_LIMIT:
            connection.send_bytes(payload)
    except Exception:
        # Failures remain retryable; do not serialize exception/credential text.
        pass
    finally:
        connection.close()


class _ResultUnpickler(pickle.Unpickler):
    """Only passive result dataclasses may cross the lookup child boundary."""
    def find_class(self, module, name):
        allowed = {
            "launcher.ra_owned_service": {"IdentificationResult"},
            "launcher.ra_account_worker": {"AccountResult"},
            "launcher.ra_credentials": {"Credentials"},
            "launcher.ra_sets": {"SetContext", "QualifiedSet"},
            "launcher.ra_session_client": {"SessionInfo", "AwardResult"},
        }
        if name not in allowed.get(module, set()):
            raise pickle.UnpicklingError("Invalid resolver result type")
        return super().find_class(module, name)


def _receive(connection, delivered):
    """Keep partial pipe writes from blocking the supervisor's deadline loop."""
    try:
        result = _ResultUnpickler(io.BytesIO(connection.recv_bytes(IPC_LIMIT))).load()
    except Exception:
        result = None
    delivered.put_nowait(result)


class ProcessResolver:
    """Run each lookup in one reaped child with a hard wall-clock deadline.

    Cancellation and timeout terminate the child, then kill if necessary.
    A surviving child is an error and prevents further work on this adapter.
    Intended for bounded identification, never unlock/submission side effects.
    """

    def __init__(self, resolve, timeout=10, validate=valid_identification_result):
        if timeout <= 0:
            raise ValueError("Resolver deadline must be positive")
        self.resolve = resolve
        self.validate = validate
        self.timeout = timeout
        self._child = None
        self._reader = None

    def __call__(self, job, cancel):
        if self._child is not None and self._child.is_alive():
            raise RuntimeError("Previous resolver process remains alive")
        if self._reader is not None and self._reader.is_alive():
            raise RuntimeError("Previous resolver reader remains alive")
        context = multiprocessing.get_context("spawn")
        reader, writer = context.Pipe(duplex=False)
        child_cancel = context.Event()
        child = context.Process(target=_run, args=(self.resolve, job, child_cancel, writer, self.validate),
                                daemon=True, name="RA identification")
        self._child = child
        started = False
        try:
            child.start()
            started = True
            writer.close()
            delivered = queue.Queue(maxsize=1)
            receiver = self._reader = threading.Thread(target=_receive, args=(reader, delivered), daemon=True)
            receiver.start()
            deadline = time.monotonic() + self.timeout
            while not cancel.is_set() and time.monotonic() < deadline:
                try:
                    result = delivered.get(timeout=0.02)
                except queue.Empty:
                    continue
                if cancel.is_set() or time.monotonic() >= deadline:
                    return None
                return result if self.validate(result) else None
            return None
        finally:
            writer.close()
            if started:
                child_cancel.set()
                child.join(0.05)
                if child.is_alive():
                    child.terminate()
                    child.join(0.2)
                if child.is_alive():
                    child.kill()
                    child.join(0.2)
                if child.is_alive():
                    raise RuntimeError("Cannot reap resolver process")
                child.close()
            self._child = None
            reader.close()
            if self._reader is not None:
                self._reader.join(0.5)
                if self._reader.is_alive():
                    raise RuntimeError("Cannot reap resolver reader")
                self._reader = None


def _probe_result(job, cancel):
    from launcher.ra_owned_service import IdentificationResult
    return IdentificationResult(unsupported=True)


def _probe_stuck(job, cancel):
    while True:
        time.sleep(1)


def package_selfcheck():
    """Offline packaged-process probe: result delivery, hard timeout and reap."""
    import threading
    from launcher.ra_receiver import IdentificationJob
    job = IdentificationJob(1, "a" * 32, "SLUS_210.65")
    result = ProcessResolver(_probe_result)(job, threading.Event())
    if result is None or not result.unsupported:
        raise RuntimeError("Spawned resolver did not return its result")
    before = {p.pid for p in multiprocessing.active_children()}
    started = time.monotonic()
    if ProcessResolver(_probe_stuck, timeout=1)(job, threading.Event()) is not None:
        raise RuntimeError("Stuck resolver unexpectedly returned")
    if time.monotonic() - started > 4:
        raise RuntimeError("Resolver termination exceeded deadline allowance")
    if {p.pid for p in multiprocessing.active_children()} != before:
        raise RuntimeError("Resolver left a child process behind")
    return {"result_delivery": True, "hard_timeout": True, "child_reaped": True}
