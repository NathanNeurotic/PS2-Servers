"""Killable identification boundary using Windows-compatible spawn semantics.

Resolvers must be importable module-level callables with picklable state.
The application must enable multiprocessing.freeze_support in its entry point
before selecting this adapter in a frozen build. No credentials go on argv.
"""
import multiprocessing
import time


def _run(resolve, job, cancel, connection):
    try:
        from launcher.ra_owned_service import IdentificationResult
        result = resolve(job, cancel)
        # Bound IPC before serializing; a callback cannot send arbitrary objects
        # or an unbounded response into the receiving supervisor thread.
        if not isinstance(result, IdentificationResult):
            return
        if result.watch_file is not None and (type(result.watch_file) is not bytes or
                                              len(result.watch_file) > 17432):
            return
        if (type(result.title) is not str or len(result.title) > 4096 or
                (result.serial is not None and
                 (type(result.serial) is not str or len(result.serial) > 64)) or
                type(result.achievements) is not int or
                not 0 <= result.achievements <= 10000000 or
                type(result.unsupported) is not bool):
            return
        connection.send(result)
    except Exception:
        # Failures remain retryable; do not serialize exception/credential text.
        pass
    finally:
        connection.close()


class ProcessResolver:
    """Run each lookup in one reaped child with a hard wall-clock deadline.

    Cancellation and timeout terminate the child, then kill if necessary.
    A surviving child is an error and prevents further work on this adapter.
    Intended for bounded identification, never unlock/submission side effects.
    """

    def __init__(self, resolve, timeout=10):
        if timeout <= 0:
            raise ValueError("Resolver deadline must be positive")
        self.resolve = resolve
        self.timeout = timeout
        self._child = None

    def __call__(self, job, cancel):
        if self._child is not None and self._child.is_alive():
            raise RuntimeError("Previous resolver process remains alive")
        context = multiprocessing.get_context("spawn")
        reader, writer = context.Pipe(duplex=False)
        child_cancel = context.Event()
        child = context.Process(target=_run, args=(self.resolve, job, child_cancel, writer),
                                daemon=True, name="RA identification")
        self._child = child
        started = False
        try:
            child.start()
            started = True
            writer.close()
            deadline = time.monotonic() + self.timeout
            while not cancel.is_set() and time.monotonic() < deadline:
                if reader.poll(0.02):
                    try:
                        return reader.recv()
                    except EOFError:
                        return None
                if not child.is_alive():
                    return None
            return None
        finally:
            reader.close()
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
