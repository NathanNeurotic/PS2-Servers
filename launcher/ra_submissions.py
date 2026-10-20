"""One bounded, cancellable consumer for explicit owned-runtime submissions."""
import queue
import threading

from launcher.ra_account_worker import AccountTask, AccountWorker


class Submissions:
    """Submit each admitted task once; never retry an ambiguous remote award."""
    def __init__(self, profile, cancel, *, capacity=64, worker=None):
        if type(capacity) is not int or not 1 <= capacity <= 1024:
            raise ValueError("Invalid submission capacity")
        self._cancel = cancel
        self._queue = queue.Queue(capacity)
        self._lock = threading.Lock()
        self._worker = worker if worker is not None else AccountWorker(str(profile))
        self._thread = threading.Thread(target=self._run, name="RA submissions", daemon=True)
        self._thread.start()

    def offer(self, task):
        if type(task) is not AccountTask or task.operation not in ("award", "ping"):
            raise ValueError("Invalid submission task")
        task.__post_init__()
        with self._lock:
            if self._cancel.is_set():
                return False
            try:
                self._queue.put_nowait(task)
                return True
            except queue.Full:
                return False

    def _run(self):
        while not self._cancel.is_set():
            try:
                task = self._queue.get(timeout=0.05)
            except queue.Empty:
                continue
            try:
                result = self._worker(task, self._cancel, wait=True)
                if not self._cancel.is_set() and (result is None or result.status != "ok"):
                    print(f"RetroAchievements: {task.operation} request did not complete; not retried.", flush=True)
                elif (not self._cancel.is_set() and task.operation == "award"
                      and result.award is not None and result.award.awarded):
                    print(f"RetroAchievements: achievement {task.achievement_id} accepted by server.", flush=True)
            except Exception:
                if not self._cancel.is_set():
                    print(f"RetroAchievements: {task.operation} request failed; not retried.", flush=True)

    def close(self, timeout=2):
        with self._lock:
            self._cancel.set()
            while True:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    break
        self._thread.join(timeout)
        return not self._thread.is_alive()
