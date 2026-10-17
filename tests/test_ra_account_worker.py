"""Real spawn/reaping and generation-safe commits using synthetic credentials."""
import multiprocessing
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest import mock

from launcher.ra_account_worker import (AccountTask, AccountResult, AccountWorker,
                                        AccountSession, valid_account_result, _Lookup)
from launcher.ra_credentials import Credentials, CredentialStore
from launcher.ra_session_client import SessionInfo, AwardResult
from launcher.ra_sets import QualifiedSet, SetContext


def synthetic_login(job, cancel):
    return AccountResult(job.task_id, job.epoch, "ok", credentials=Credentials("Synthetic", "synthetic-token"))


def rate_limited(job, cancel):
    return AccountResult(job.task_id, job.epoch, "rate_limited", retry_after=120)


def synthetic_set(job, cancel):
    qualified = QualifiedSet(job.context, "Synthetic game", ((1, "0xH080000=1"),),
                             (1,), (), ())
    return AccountResult(job.task_id, job.epoch, "ok", qualified=qualified)


def stalled(job, cancel):
    while True:
        time.sleep(1)


def oversized_credentials(job, cancel):
    credentials = Credentials("Synthetic", "token")
    object.__setattr__(credentials, "token", "x" * 20000)
    return AccountResult(job.task_id, job.epoch, "ok", credentials=credentials)


class AccountWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.profile = Path(self.temporary.name) / "profile"
        self.task = AccountTask(1, 1, "login", username="Synthetic", password="transient-password")
        self.before = {child.pid for child in multiprocessing.active_children()}

    def tearDown(self):
        self.assertEqual({child.pid for child in multiprocessing.active_children()}, self.before)

    def test_private_credential_delivery_reaping_and_no_child_persistence(self):
        worker = AccountWorker(self.profile, resolve=synthetic_login)
        result = worker(self.task, threading.Event())
        self.assertEqual(result.credentials.token, "synthetic-token")
        self.assertIsNone(worker._resolver._child)
        self.assertIsNone(worker._resolver._reader)
        self.assertNotIn("synthetic-token", repr(result))
        self.assertNotIn("transient-password", repr(self.task))
        self.assertFalse(self.profile.exists())

    def test_qualified_set_crosses_real_process_boundary_with_context_intact(self):
        context = SetContext("Synthetic", 1, "a" * 32, 1, 21)
        task = AccountTask(2, 1, "set", credentials=Credentials("Synthetic", "token"), context=context)
        worker = AccountWorker(self.profile, resolve=synthetic_set)
        result = worker(task, threading.Event())
        self.assertEqual(result.qualified.context, context)
        self.assertEqual(result.qualified.definitions, ((1, "0xH080000=1"),))
        self.assertIsNone(worker._resolver._child)
        self.assertIsNone(worker._resolver._reader)
        self.assertFalse(self.profile.exists())

    def test_mutated_oversized_input_is_rejected_before_spawning(self):
        worker = AccountWorker(self.profile, resolve=synthetic_login)
        object.__setattr__(self.task, "password", "x" * 4097)
        with mock.patch.object(worker, "_resolver") as resolver:
            with self.assertRaises(ValueError):
                worker(self.task, threading.Event())
            resolver.assert_not_called()

    def test_deadline_and_oversized_results_leave_no_process_or_reader(self):
        worker = AccountWorker(self.profile, resolve=stalled, timeout=0.3)
        started = time.monotonic()
        self.assertIsNone(worker(self.task, threading.Event()))
        self.assertLess(time.monotonic() - started, 3)
        self.assertIsNone(worker._resolver._reader)
        invalid = AccountWorker(self.profile, resolve=oversized_credentials)
        self.assertIsNone(invalid(self.task, threading.Event()))

    def test_parent_retains_backoff_without_spawning_again(self):
        worker = AccountWorker(self.profile, resolve=rate_limited)
        result = worker(self.task, threading.Event())
        self.assertEqual(result.status, "rate_limited")
        self.assertGreater(worker._not_before - time.monotonic(), 119)
        worker._resolver = mock.Mock(side_effect=AssertionError("Cooldown must not spawn"))
        self.assertIsNone(worker(self.task, threading.Event()))
        worker._resolver.assert_not_called()

    def test_cancellation_and_busy_worker_do_not_accumulate_children(self):
        worker = AccountWorker(self.profile, resolve=synthetic_login)
        cancel = threading.Event()
        cancel.set()
        self.assertIsNone(worker(self.task, cancel))
        worker._lock.acquire()
        try:
            with self.assertRaises(RuntimeError):
                worker(self.task, threading.Event())
        finally:
            worker._lock.release()

    def test_import_reads_only_after_consent_and_revalidates_without_writing(self):
        credentials = Credentials("Synthetic", "legacy-token")
        login = mock.Mock(return_value=credentials)
        with mock.patch("launcher.ra_account_worker.CredentialStore") as store_type, \
                mock.patch("launcher.ra_account_worker.AccountClient") as client_type:
            store_type.return_value.read_managed.return_value = credentials
            client_type.return_value.login = login
            task = AccountTask(1, 1, "import", consent=True)
            result = _Lookup(str(self.profile))(task, threading.Event())
            store_type.return_value.read_managed.assert_called_once_with(consent=True)
            login.assert_called_once_with("Synthetic", token="legacy-token", cancel=mock.ANY)
            store_type.return_value.save.assert_not_called()
            self.assertEqual(result.credentials, credentials)

    def test_stale_login_result_cannot_persist_after_logout(self):
        receiver, store = mock.Mock(), mock.Mock()
        session = AccountSession(receiver, store)
        task = session.begin_login("Synthetic", "transient-password")
        result = synthetic_login(task, threading.Event())
        session.logout(forget=True)
        self.assertFalse(session.accept(task, result, remember=True))
        store.save.assert_not_called()
        store.clear.assert_called_once()
        self.assertIsNone(session.username)

    def test_new_login_supersedes_old_before_commit_and_failed_save_never_binds(self):
        receiver, store = mock.Mock(), mock.Mock()
        session = AccountSession(receiver, store)
        old = session.begin_login("Synthetic", "old-password")
        current = session.begin_login("Synthetic", "new-password")
        self.assertFalse(session.accept(old, synthetic_login(old, None), remember=True))
        store.save.assert_not_called()
        store.save.side_effect = PermissionError("synthetic")
        with self.assertRaises(PermissionError):
            session.accept(current, synthetic_login(current, None), remember=True)
        receiver.bind_account.assert_not_called()
        self.assertIsNone(session.username)

    def test_current_login_can_persist_without_password_and_import_is_explicit(self):
        receiver = mock.Mock()
        store = CredentialStore(self.profile)
        session = AccountSession(receiver, store)
        with self.assertRaises(ValueError):
            session.begin_import()
        task = session.begin_login("Synthetic", "transient-password")
        self.assertTrue(session.accept(task, synthetic_login(task, None), remember=True))
        self.assertEqual(store.load(), Credentials("Synthetic", "synthetic-token"))
        self.assertNotIn(b"transient-password", store.path.read_bytes())
        self.assertEqual(session.username, "Synthetic")
        receiver.bind_account.assert_called_once_with("Synthetic", task.epoch)

    def test_invalid_private_result_shape_is_rejected(self):
        self.assertFalse(valid_account_result(AccountResult(1, 1, "ok")))
        self.assertFalse(valid_account_result(AccountResult(1, 1, "failed", credentials=Credentials("Synthetic", "token"))))
        self.assertFalse(valid_account_result(AccountResult(True, 1, "failed")))

    def test_session_award_ping_task_validation(self):
        context = SetContext("Synthetic", 1, "a" * 32, 100, 21)
        creds = Credentials("Synthetic", "token")
        task_session = AccountTask(1, 1, "start_session", credentials=creds, context=context)
        self.assertEqual(task_session.operation, "start_session")

        task_award = AccountTask(2, 1, "award", credentials=creds, context=context, achievement_id=42)
        self.assertEqual(task_award.achievement_id, 42)

        task_ping = AccountTask(3, 1, "ping", credentials=creds, context=context, rich_presence="Playing")
        self.assertEqual(task_ping.rich_presence, "Playing")

        with self.assertRaises(ValueError):
            AccountTask(4, 1, "award", credentials=creds, context=context, achievement_id=0)
        with self.assertRaises(ValueError):
            AccountTask(5, 1, "start_session", credentials=creds, context=context, achievement_id=42)

    def test_valid_account_result_session_award_ping(self):
        info = SessionInfo(100, 1600000000, (10,), ())
        self.assertTrue(valid_account_result(AccountResult(1, 1, "ok", session_info=info)))
        award = AwardResult(42, True, score=10)
        self.assertTrue(valid_account_result(AccountResult(1, 1, "ok", award=award)))
        self.assertTrue(valid_account_result(AccountResult(1, 1, "ok", ping=True)))
        self.assertFalse(valid_account_result(AccountResult(1, 1, "ok", ping=False)))
        self.assertFalse(valid_account_result(AccountResult(1, 1, "ok", session_info=info, award=award)))

    def test_account_session_start_session_award_and_ping_lifecycle(self):
        receiver, store = mock.Mock(), mock.Mock()
        receiver.complete_set.return_value = True
        session = AccountSession(receiver, store)
        login_task = session.begin_login("Synthetic", "pwd")
        self.assertTrue(session.accept(login_task, synthetic_login(login_task, None)))

        ident = mock.Mock(image_hash="a" * 32)
        set_task = session.begin_set(ident, 100, 21)
        qualified = QualifiedSet(set_task.context, "Synth", (), (), (), ())
        self.assertTrue(session.accept(set_task, AccountResult(set_task.task_id, 1, "ok", qualified=qualified)))
        self.assertEqual(session.context, set_task.context)

        # start_session
        sess_task = session.begin_start_session()
        self.assertEqual(sess_task.operation, "start_session")
        info = SessionInfo(100, 1600000000, (), ())
        self.assertTrue(session.accept(sess_task, AccountResult(sess_task.task_id, 1, "ok", session_info=info)))
        self.assertEqual(session.session_info, info)

        # award
        award_task = session.begin_award(42)
        self.assertEqual(award_task.operation, "award")
        self.assertEqual(award_task.achievement_id, 42)
        award = AwardResult(42, True, score=5)
        self.assertTrue(session.accept(award_task, AccountResult(award_task.task_id, 1, "ok", award=award)))

        # ping
        ping_task = session.begin_ping(rich_presence="Boss Fight")
        self.assertEqual(ping_task.operation, "ping")
        self.assertEqual(ping_task.rich_presence, "Boss Fight")
        self.assertTrue(session.accept(ping_task, AccountResult(ping_task.task_id, 1, "ok", ping=True)))

        # logout clears context and session_info
        session.logout()
        self.assertIsNone(session.context)
        self.assertIsNone(session.session_info)

    def test_lookup_dispatches_session_award_and_ping(self):
        context = SetContext("Synthetic", 1, "a" * 32, 100, 21)
        creds = Credentials("Synthetic", "token")
        with mock.patch("launcher.ra_account_worker.RASessionClient") as client_cls:
            client = client_cls.return_value
            info = SessionInfo(100, 12345, (), ())
            client.start_session.return_value = info
            award = AwardResult(99, True)
            client.award_achievement.return_value = award
            client.ping.return_value = True

            lookup = _Lookup(str(self.profile))

            task1 = AccountTask(1, 1, "start_session", credentials=creds, context=context)
            res1 = lookup(task1, threading.Event())
            self.assertEqual(res1.session_info, info)
            client.start_session.assert_called_once_with(context, cancel=mock.ANY)

            task2 = AccountTask(2, 1, "award", credentials=creds, context=context, achievement_id=99)
            res2 = lookup(task2, threading.Event())
            self.assertEqual(res2.award, award)
            client.award_achievement.assert_called_once_with(context, 99, cancel=mock.ANY)

            task3 = AccountTask(3, 1, "ping", credentials=creds, context=context, rich_presence="In Game")
            res3 = lookup(task3, threading.Event())
            self.assertTrue(res3.ping)
            client.ping.assert_called_once_with(context, rich_presence="In Game", cancel=mock.ANY)

