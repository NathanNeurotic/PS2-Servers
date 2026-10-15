"""Opt-in account/set worker and single-owner credential commit boundary.

Children only read profiles and call read/login APIs. They never persist
credentials or submit unlocks. The owner commits fresh login results after
generation checks, so a cancelled child cannot sign an account back in.
"""
from dataclasses import dataclass, field
import threading
import time

from launcher.ra_account import AccountClient
from launcher.ra_credentials import Credentials, CredentialStore
from launcher.ra_http import RateLimited
from launcher.ra_resolver_process import ProcessResolver
from launcher.ra_set_client import RASetClient
from launcher.ra_sets import SetContext, QualifiedSet


@dataclass(frozen=True)
class AccountTask:
    """Private process input; login secrets are absent from repr and argv."""
    task_id: int
    epoch: int
    operation: str
    username: str = ""
    password: str = field(default=None, repr=False)
    credentials: Credentials = field(default=None, repr=False)
    context: SetContext = None
    include_unofficial: bool = False
    consent: bool = False

    def __post_init__(self):
        for value in (self.task_id, self.epoch):
            if type(value) is not int or not 1 <= value <= 0xFFFFFFFF:
                raise ValueError("Invalid account task identity")
        if type(self.operation) is not str or self.operation not in ("login", "resume", "import", "set"):
            raise ValueError("Invalid account operation")
        if (type(self.username) is not str or len(self.username) > 64 or
                any(c in self.username for c in "\0\r\n") or
                type(self.include_unofficial) is not bool or type(self.consent) is not bool):
            raise ValueError("Invalid account task options")
        if self.operation == "login":
            if (not self.username or type(self.password) is not str or
                    not 1 <= len(self.password) <= 4096 or "\0" in self.password):
                raise ValueError("Invalid login input")
        elif self.password is not None:
            raise ValueError("Unexpected password")
        if self.operation == "set":
            if type(self.credentials) is not Credentials or type(self.context) is not SetContext:
                raise ValueError("Invalid set input")
            self.credentials.__post_init__()
            self.context.__post_init__()
            if self.context.account != self.credentials.username or self.context.epoch != self.epoch:
                raise ValueError("Set account context mismatch")
        elif self.credentials is not None or self.context is not None:
            raise ValueError("Unexpected set input")


@dataclass(frozen=True)
class AccountResult:
    """Bounded private result; failure text and passwords never cross back."""
    task_id: int
    epoch: int
    status: str
    credentials: Credentials = field(default=None, repr=False)
    qualified: QualifiedSet = None
    retry_after: int = 0


def valid_account_result(result):
    """Validate a plain bounded object graph before private IPC serialization."""
    if (type(result) is not AccountResult or type(result.task_id) is not int or result.task_id < 1 or
            type(result.epoch) is not int or result.epoch < 1 or
            result.status not in ("ok", "failed", "rate_limited") or
            type(result.retry_after) is not int or not 0 <= result.retry_after <= 300):
        return False
    if result.status != "ok":
        return (result.credentials is None and result.qualified is None and
                (result.retry_after > 0 if result.status == "rate_limited" else result.retry_after == 0))
    if result.retry_after or (result.credentials is None) == (result.qualified is None):
        return False
    if result.credentials is not None:
        if type(result.credentials) is not Credentials:
            return False
        try:
            result.credentials.__post_init__()
        except ValueError:
            return False
        return True
    qualified = result.qualified
    if type(qualified) is not QualifiedSet or type(qualified.context) is not SetContext:
        return False
    try:
        qualified.context.__post_init__()
    except ValueError:
        return False
    if (type(qualified.title) is not str or len(qualified.title) > 4096 or
            type(qualified.definitions) is not tuple or len(qualified.definitions) > 4096):
        return False
    total = 0
    seen = set()
    for pair in qualified.definitions:
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
    for values, limit in ((qualified.selected_ids, 4096), (qualified.unofficial_ids, 4096),
                          (qualified.prior_unlocks, 100000)):
        if type(values) is not tuple or len(values) > limit or any(
                type(value) is not int or not 1 <= value <= 0xFFFFFFFF for value in values):
            return False
    return (total <= 4 * 1024 * 1024 and seen <= set(qualified.selected_ids) and
            not seen.intersection(qualified.prior_unlocks) and
            set(qualified.unofficial_ids) <= set(qualified.selected_ids))


@dataclass(frozen=True)
class _Lookup:
    profile: str

    def __call__(self, job, cancel):
        try:
            store = CredentialStore(self.profile)
            if job.operation == "set":
                if type(job.credentials) is not Credentials or job.context.epoch != job.epoch:
                    raise ValueError("Invalid set job")
                qualified = RASetClient(job.credentials.username, job.credentials.token).fetch(
                    job.context, include_unofficial=job.include_unofficial, cancel=cancel)
                return AccountResult(job.task_id, job.epoch, "ok", qualified=qualified)
            client = AccountClient()
            if job.operation == "login":
                credentials = client.login(job.username, password=job.password, cancel=cancel)
            elif job.operation in ("resume", "import"):
                old = store.load() if job.operation == "resume" else store.read_managed(consent=job.consent)
                if old is None:
                    raise ValueError("No stored account")
                credentials = client.login(old.username, token=old.token, cancel=cancel)
            else:
                raise ValueError("Invalid account operation")
            return AccountResult(job.task_id, job.epoch, "ok", credentials=credentials)
        except RateLimited as error:
            return AccountResult(job.task_id, job.epoch, "rate_limited", retry_after=error.retry_after)
        except Exception:
            return AccountResult(job.task_id, job.epoch, "failed")


class AccountWorker:
    """One reaped child at a time; pacing/backoff survives child replacement.

    Call on a supervisor thread. A busy worker rejects another caller rather
    than accumulating processes. None means cancellation, deadline or cooldown;
    it never declares that a game has no support.
    """
    def __init__(self, profile, *, timeout=30, minimum_interval=1, resolve=None):
        import math
        if (type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 60 or
                type(minimum_interval) not in (int, float) or not math.isfinite(minimum_interval) or
                not 0 < minimum_interval <= 30):
            raise ValueError("Invalid account worker timing")
        self._resolver = ProcessResolver(resolve or _Lookup(str(profile)), timeout,
                                         validate=valid_account_result)
        self._lock = threading.Lock()
        self._not_before = 0
        self.minimum_interval = minimum_interval

    def __call__(self, job, cancel):
        if type(job) is not AccountTask:
            raise ValueError("Invalid account task")
        job.__post_init__()  # validate bounded input before spawn serialization
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("Account worker is already busy")
        try:
            if cancel.is_set() or time.monotonic() < self._not_before:
                return None
            result = self._resolver(job, cancel)
            self._not_before = time.monotonic() + self.minimum_interval
            if result is None or result.task_id != job.task_id or result.epoch != job.epoch:
                return None
            if result.status == "rate_limited":
                self._not_before = max(self._not_before, time.monotonic() + result.retry_after)
            return result
        finally:
            self._lock.release()


class AccountSession:
    """Single service-owner thread commits only current login or set results."""
    def __init__(self, receiver, store):
        self.receiver, self.store = receiver, store
        self._epoch = self._serial = 0
        self._credentials = self._pending = self._identification = None
        self.cancel = threading.Event()

    @property
    def username(self):
        return self._credentials.username if self._credentials is not None else None

    def _new_task(self, operation, **options):
        candidate = AccountTask(self._serial + 1, self._epoch, operation, **options)
        self.cancel.set()
        self.cancel = threading.Event()
        self._serial += 1
        self._pending = candidate
        return self._pending

    def begin_login(self, username, password):
        AccountTask(1, 1, "login", username=username, password=password)
        self.logout()
        return self._new_task("login", username=username, password=password)

    def begin_resume(self):
        self.logout()
        return self._new_task("resume")

    def begin_import(self, *, consent=False):
        if consent is not True:
            raise ValueError("Explicit managed-profile import consent required")
        self.logout()
        return self._new_task("import", consent=True)

    def begin_set(self, identification, game_id, console_id, *, include_unofficial=False):
        if self._credentials is None:
            raise ValueError("Account sign-in required")
        context = SetContext(self._credentials.username, self._epoch, identification.image_hash, game_id, console_id)
        task = self._new_task("set", credentials=self._credentials, context=context,
                              include_unofficial=include_unofficial)
        self._identification = identification
        return task

    def accept(self, job, result, *, remember=False, profile="opl"):
        """Persist/activate only after matching the current task and epoch."""
        if (type(job) is not AccountTask or self._pending != job or self.cancel.is_set() or not valid_account_result(result) or
                result.task_id != job.task_id or result.epoch != self._epoch):
            return False
        try:
            if result.status != "ok":
                return False
            if job.operation == "set":
                if result.qualified is None or result.qualified.context != job.context:
                    return False
                return self.receiver.complete_set(self._identification, result.qualified, profile=profile)
            if result.credentials is None or type(remember) is not bool:
                return False
            if remember:
                self.store.save(result.credentials)
            self.receiver.bind_account(result.credentials.username, self._epoch)
            self._credentials = result.credentials
            return True
        finally:
            self.cancel.set()
            self._pending = self._identification = None

    def logout(self, *, forget=False):
        """Revoke in-flight results and live state before optional owned-token removal."""
        if type(forget) is not bool:
            raise ValueError("Invalid credential removal choice")
        self.cancel.set()
        self._epoch += 1
        self._credentials = self._pending = self._identification = None
        self.receiver.clear_account()
        if forget:
            self.store.clear()
