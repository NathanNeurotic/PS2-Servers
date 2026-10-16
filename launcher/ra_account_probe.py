"""Offline packaged account/profile/worker gate using disposable credentials."""
from pathlib import Path
import tempfile
import threading

from launcher.ra_account_worker import AccountTask, AccountResult, AccountWorker
from launcher.ra_credentials import Credentials, CredentialStore


def _synthetic(job, cancel):
    return AccountResult(job.task_id, job.epoch, "ok", credentials=Credentials("Synthetic", "synthetic-runtime-token"))


def package_selfcheck():
    """Exercise real platform protection and spawned private delivery, no network."""
    with tempfile.TemporaryDirectory(prefix="ra-owned-account-") as directory:
        store = CredentialStore(Path(directory) / "profile")
        credentials = Credentials("Synthetic", "synthetic-runtime-token")
        store.save(credentials)
        if store.load() != credentials:
            raise RuntimeError("Owned credential roundtrip failed")
        worker = AccountWorker(store.profile, resolve=_synthetic)
        result = worker(AccountTask(1, 1, "resume"), threading.Event())
        if result is None or result.credentials != credentials:
            raise RuntimeError("Owned private credential delivery failed")
        if worker._resolver._child is not None or worker._resolver._reader is not None:
            raise RuntimeError("Owned account worker did not release its resources")
        store.clear()
        if store.load() is not None:
            raise RuntimeError("Owned credential removal failed")
    return {"private_profile": True, "private_delivery": True, "child_reaped": True}
