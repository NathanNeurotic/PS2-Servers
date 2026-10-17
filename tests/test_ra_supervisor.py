"""Tests for RetroAchievements supervisor opt-in and owned runtime orchestration."""
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from launcher.achievements import main, run_owned_runtime
from launcher.servers import _achievements_argv, RETROACHIEVEMENTS


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.profile = Path(self.temp_dir.name) / "profile"
        self.profile.mkdir(parents=True, exist_ok=True)

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
