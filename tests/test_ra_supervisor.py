"""Tests for RetroAchievements supervisor opt-in and owned runtime orchestration."""
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from launcher.achievements import main, run_owned_runtime
from launcher.servers import _achievements_argv, RETROACHIEVEMENTS
from launcher.ra_account_worker import AccountResult
from launcher.ra_credentials import Credentials
from launcher.ra_receiver import IdentificationJob
from launcher.ra_sets import QualifiedSet


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.profile = Path(self.temp_dir.name) / "profile"
        self.profile.mkdir(parents=True, exist_ok=True)

    def resolve_fixture(self, mapping, response, *, cancel=None):
        """Exercise the actual supervisor resolver with all account I/O stubbed."""
        import json
        cancel = cancel or threading.Event()
        job = IdentificationJob(1, "a" * 32, "SLUS_000.01")
        captured, calls = [], []
        args = mock.Mock(mode="xerabora", login=None, import_credentials=False,
                         lan_viewer=False, games_folder=None)
        library = self.profile / "fixture.dll"
        library.write_bytes(b"fixture")

        def worker_call(task, event, **options):
            calls.append((task, options))
            return response(task, event, options)

        def service_factory(resolve, **options):
            service = mock.Mock()
            service.start.side_effect = lambda: captured.append(resolve(job, cancel))
            return service

        with mock.patch("launcher.achievements.library_path", return_value=library), \
             mock.patch("launcher.ra_owned_service.OwnedService", side_effect=service_factory), \
             mock.patch("launcher.ra_credentials.CredentialStore") as store_type, \
             mock.patch("launcher.ra_account_worker.AccountWorker") as worker_type, \
             mock.patch("launcher.ra_http.Transport") as transport_type, \
             mock.patch("launcher.ra_web.RAWebServer"):
            store_type.return_value.load.return_value = Credentials("Synthetic", "synthetic-token")
            transport_type.return_value.post.return_value = json.dumps({"Success": True, "GameID": mapping}).encode()
            worker_type.return_value.side_effect = worker_call
            self.assertEqual(run_owned_runtime(args, self.profile, mock.Mock(), threading.Event(),
                                               lambda: False, mock.Mock()), 0)
        return captured[0], calls

    def test_console_fallback_and_session_are_not_dropped_by_worker_cooldown(self):
        def reply(task, cancel, options):
            if task.operation == "set" and task.context.console_id == 12 and options.get("wait"):
                qualified = QualifiedSet(task.context, "Synthetic PS1", ((1, "0xH001000=1"),), (1,), (), ())
                return AccountResult(task.task_id, task.epoch, "ok", qualified=qualified)
            return AccountResult(task.task_id, task.epoch, "failed")
        result, calls = self.resolve_fixture(123, reply)
        self.assertIsNotNone(result.qualified)
        self.assertEqual(result.qualified.context.console_id, 12)
        self.assertEqual([task.operation for task, _ in calls], ["set", "set", "start_session"])
        self.assertTrue(all(options.get("wait") for _, options in calls))

    def test_failed_or_rate_limited_set_lookup_does_not_declare_no_support(self):
        for status in ("failed", "rate_limited"):
            with self.subTest(status=status):
                def reply(task, cancel, options):
                    return AccountResult(task.task_id, task.epoch, status,
                                         retry_after=1 if status == "rate_limited" else 0)
                result, _ = self.resolve_fixture(123, reply)
                self.assertFalse(result.unsupported)
                self.assertIsNone(result.qualified)

    def test_only_explicit_zero_mapping_declares_unknown_image(self):
        for mapping in (0, False, True, None, -1, 0x100000000, "123"):
            with self.subTest(mapping=mapping):
                result, calls = self.resolve_fixture(mapping, lambda *args: None)
                self.assertEqual(result.unsupported, type(mapping) is int and mapping == 0)
                self.assertEqual(calls, [])

    def test_cancelled_lookup_does_not_start_account_jobs(self):
        cancel = threading.Event()
        cancel.set()
        result, calls = self.resolve_fixture(123, lambda *args: None, cancel=cancel)
        self.assertFalse(result.unsupported)
        self.assertEqual(calls, [])

    def test_cancelled_success_cannot_publish_a_qualified_set_or_start_session(self):
        def reply(task, cancel, options):
            qualified = QualifiedSet(task.context, "Synthetic", ((1, "0xH080000=1"),), (1,), (), ())
            cancel.set()
            return AccountResult(task.task_id, task.epoch, "ok", qualified=qualified)
        result, calls = self.resolve_fixture(123, reply)
        self.assertIsNone(result.qualified)
        self.assertFalse(result.unsupported)
        self.assertEqual([task.operation for task, _ in calls], ["set"])

    def test_cancelled_session_start_does_not_return_a_qualified_result(self):
        def reply(task, cancel, options):
            if task.operation == "start_session":
                cancel.set()
                return None
            qualified = QualifiedSet(task.context, "Synthetic", ((1, "0xH080000=1"),), (1,), (), ())
            return AccountResult(task.task_id, task.epoch, "ok", qualified=qualified)
        result, calls = self.resolve_fixture(123, reply)
        self.assertIsNone(result.qualified)
        self.assertFalse(result.unsupported)
        self.assertEqual([task.operation for task, _ in calls], ["set", "start_session"])

    def test_achievements_argv_owned_runtime(self):
        args_default = _achievements_argv({"mode": "xerabora"})
        self.assertNotIn("--owned-runtime", args_default)

        args_owned = _achievements_argv({"mode": "xerabora", "owned_runtime": True})
        self.assertIn("--owned-runtime", args_owned)

        args_caduceus = _achievements_argv({
            "mode": "caduceus",
            "games_folder": str(self.profile),
            "owned_runtime": True,
        })
        self.assertIn("--owned-runtime", args_caduceus)
        self.assertIn("--mode", args_caduceus)
        self.assertIn("caduceus", args_caduceus)

    def test_retroachievements_field_registry(self):
        owned_field = next((f for f in RETROACHIEVEMENTS.fields if f.key == "owned_runtime"), None)
        self.assertIsNotNone(owned_field)
        self.assertTrue(owned_field.advanced)
        self.assertFalse(owned_field.default)

    def test_main_missing_library_errors(self):
        with mock.patch("launcher.achievements.profile_dir", return_value=self.profile), \
             mock.patch("launcher.achievements.library_path") as lib_mock:
            lib_mock.return_value = Path(self.temp_dir.name) / "nonexistent-rcheevos.dll"
            with self.assertRaises(SystemExit) as ctx:
                main(["--owned-runtime"])
            self.assertEqual(ctx.exception.code, 2)

    def test_run_owned_runtime_lifecycle(self):
        args = mock.Mock(
            mode="xerabora",
            owned_runtime=True,
            login=None,
            import_credentials=False,
            lan_viewer=False,
            games_folder=None,
        )
        fake_lib = Path(self.temp_dir.name) / "fake_rcheevos.dll"
        fake_lib.write_bytes(b"dummy")

        lease = mock.Mock()
        stopped = threading.Event()
        owner_alive = mock.Mock(side_effect=[True, False])
        close_owner = mock.Mock()

        with mock.patch("launcher.achievements.library_path", return_value=fake_lib), \
             mock.patch("launcher.ra_owned_service.OwnedService") as service_cls, \
             mock.patch("launcher.ra_credentials.CredentialStore") as store_cls, \
             mock.patch("launcher.ra_account_worker.AccountWorker") as worker_cls:

            service = service_cls.return_value
            service.account = "TestUser"
            service.take_evaluation.return_value = None

            store = store_cls.return_value
            store.load.return_value = mock.Mock(username="TestUser", token="dummy-token")

            ret = run_owned_runtime(args, self.profile, lease, stopped, owner_alive, close_owner)
            self.assertEqual(ret, 0)

            service.start.assert_called_once()
            service.bind_account.assert_called_once_with("TestUser", 1)
            service.stop.assert_called_once()
            lease.close.assert_called_once()
            close_owner.assert_called_once()

    def test_run_owned_runtime_evaluation_trigger_dispatches_award(self):
        args = mock.Mock(
            mode="xerabora",
            owned_runtime=True,
            login=None,
            import_credentials=False,
            lan_viewer=False,
            games_folder=None,
        )
        fake_lib = Path(self.temp_dir.name) / "fake_rcheevos.dll"
        fake_lib.write_bytes(b"dummy")

        lease = mock.Mock()
        stopped = threading.Event()
        close_owner = mock.Mock()

        with mock.patch("launcher.achievements.library_path", return_value=fake_lib), \
             mock.patch("launcher.ra_owned_service.OwnedService") as service_cls, \
             mock.patch("launcher.ra_credentials.CredentialStore") as store_cls, \
             mock.patch("launcher.ra_account_worker.AccountWorker") as worker_cls, \
             mock.patch("launcher.ra_session_client.RASessionClient") as client_cls:

            service = service_cls.return_value
            service.account = "TestUser"

            mock_eval = mock.Mock()
            mock_eval.evaluation.triggered = (42,)
            service.take_evaluation.side_effect = [mock_eval, None]

            store = store_cls.return_value
            creds = mock.Mock(username="TestUser", token="dummy-token")
            store.load.return_value = creds

            client = client_cls.return_value
            client.award_achievement.return_value = mock.Mock(awarded=True, already_unlocked=False, softcore_score=10)

            # Let the loop run once then stop
            owner_alive = mock.Mock(side_effect=[True, True, False])

            ret = run_owned_runtime(args, self.profile, lease, stopped, owner_alive, close_owner)
            self.assertEqual(ret, 0)
            service.stop.assert_called_once()

    def test_run_owned_runtime_serves_state_http(self):
        import urllib.request
        import json

        args = mock.Mock(
            mode="xerabora",
            owned_runtime=True,
            login=None,
            import_credentials=False,
            lan_viewer=False,
            games_folder=None,
        )
        fake_lib = Path(self.temp_dir.name) / "fake_rcheevos.dll"
        fake_lib.write_bytes(b"dummy")

        lease = mock.Mock()
        stopped = threading.Event()
        close_owner = mock.Mock()

        port_box = []

        def check_port_and_alive():
            port_file = self.profile / "account-port"
            if port_file.is_file() and not port_box:
                try:
                    port = int(port_file.read_text("ascii"))
                    req = urllib.request.Request(f"http://127.0.0.1:{port}/state")
                    with urllib.request.urlopen(req, timeout=3) as resp:
                        data = json.loads(resp.read().decode("utf-8"))
                        port_box.append((port, data))
                except Exception:
                    pass
                return True
            return False

        with mock.patch("launcher.achievements.library_path", return_value=fake_lib), \
             mock.patch("launcher.ra_owned_service.OwnedService") as service_cls, \
             mock.patch("launcher.ra_credentials.CredentialStore") as store_cls, \
             mock.patch("launcher.ra_account_worker.AccountWorker"):

            service = service_cls.return_value
            service.account = "WebUser"
            service.take_evaluation.return_value = None

            store = store_cls.return_value
            store.load.return_value = mock.Mock(username="WebUser", token="dummy-token")

            ret = run_owned_runtime(args, self.profile, lease, stopped, check_port_and_alive, close_owner)
            self.assertEqual(ret, 0)
            self.assertTrue(port_box)
            port, data = port_box[0]
            self.assertEqual(data["login"]["user"], "WebUser")
            self.assertTrue(data["login"]["ok"])
            # Verify account-port is unlinked after clean exit
            self.assertFalse((self.profile / "account-port").is_file())
