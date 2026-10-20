"""Offline bounds, provenance and cancellation for the submission consumer."""
import threading
import unittest
from unittest import mock

from launcher.ra_account_worker import AccountTask, AccountResult
from launcher.ra_credentials import Credentials
from launcher.ra_sets import SetContext
from launcher.ra_submissions import Submissions


class SubmissionTests(unittest.TestCase):
    def task(self, number, game=123, operation="award"):
        context = SetContext("Synthetic", 1, ("a" if game == 123 else "b") * 32, game, 21)
        return AccountTask(number, 1, operation, credentials=Credentials("Synthetic", "fixture-token"),
                           context=context, achievement_id=42 if operation == "award" else 0)

    def test_bounded_queue_and_cancel_discard_pending_requests(self):
        cancel, entered = threading.Event(), threading.Event()
        def blocked(task, event, **options):
            entered.set()
            event.wait(3)
        worker = mock.Mock(side_effect=blocked)
        submissions = Submissions("unused", cancel, capacity=1, worker=worker)
        try:
            self.assertTrue(submissions.offer(self.task(1)))
            self.assertTrue(entered.wait(2))
            self.assertTrue(submissions.offer(self.task(2)))
            self.assertFalse(submissions.offer(self.task(3)))
            self.assertTrue(submissions.close())
            self.assertFalse(submissions.offer(self.task(4)))
            worker.assert_called_once_with(self.task(1), cancel, wait=True)
        finally:
            submissions.close()

    def test_each_request_preserves_origin_and_is_attempted_once(self):
        cancel, done = threading.Event(), threading.Event()
        calls = []
        def worker(task, event, **options):
            calls.append(task)
            if len(calls) == 3:
                done.set()
            # Explicit failures do not cause an automatic award retry.
            return AccountResult(task.task_id, task.epoch, "failed")
        submissions = Submissions("unused", cancel, worker=worker)
        tasks = [self.task(1), self.task(2, game=456), self.task(3, game=456, operation="ping")]
        try:
            for task in tasks:
                self.assertTrue(submissions.offer(task))
            self.assertTrue(done.wait(2))
            self.assertEqual(calls, tasks)
            self.assertTrue(submissions.close())
        finally:
            submissions.close()

    def test_invalid_operations_do_not_enter_the_queue(self):
        cancel = threading.Event()
        submissions = Submissions("unused", cancel, worker=mock.Mock())
        try:
            with self.assertRaises(ValueError):
                submissions.offer(AccountTask(1, 1, "resume"))
        finally:
            self.assertTrue(submissions.close())
