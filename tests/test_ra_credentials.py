"""Disposable profile tests with real Windows DPAPI or POSIX private files."""
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

from launcher.ra_credentials import Credentials, CredentialStore, MAGIC, _dpapi


class CredentialTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = CredentialStore(Path(self.directory.name) / "profile")
        self.credentials = Credentials("Synthetic", "synthetic-runtime-token")

    def test_real_platform_roundtrip_and_owned_only_removal(self):
        self.assertIsNone(self.store.load())
        self.store.save(self.credentials)
        self.assertEqual(self.store.load(), self.credentials)
        self.assertNotIn(self.credentials.token, repr(self.credentials))
        if os.name == "nt":
            self.assertNotIn(self.credentials.token.encode(), self.store.path.read_bytes())
        else:
            self.assertEqual(stat.S_IMODE(self.store.path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(self.store.directory.stat().st_mode), 0o700)
        managed = self.store.profile / "credentials"
        managed.write_bytes(b"untouched")
        self.store.clear()
        self.assertIsNone(self.store.load())
        self.assertEqual(managed.read_bytes(), b"untouched")

    def test_failed_atomic_replace_preserves_old_token_and_removes_temporary(self):
        self.store.save(self.credentials)
        previous = self.store.path.read_bytes()
        with mock.patch("launcher.ra_credentials.os.replace", side_effect=PermissionError("synthetic")):
            with self.assertRaises(PermissionError):
                self.store.save(Credentials("Other", "replacement-token"))
        self.assertEqual(self.store.path.read_bytes(), previous)
        self.assertEqual(list(self.store.directory.glob(".credentials-*")), [])

    def test_managed_import_requires_consent_and_never_changes_source(self):
        self.store._prepare()
        original = b"Synthetic\nlegacy-runtime-token\0"
        if os.name == "nt":
            original = _dpapi(original)
        source = self.store.profile / "credentials"
        source.write_bytes(original)
        source.chmod(0o600)
        with self.assertRaises(ValueError):
            self.store.read_managed()
        self.assertEqual(self.store.read_managed(consent=True), Credentials("Synthetic", "legacy-runtime-token"))
        self.assertEqual(source.read_bytes(), original)
        self.assertIsNone(self.store.load())

    def test_corrupt_owned_file_does_not_fall_back_to_managed(self):
        self.store.save(self.credentials)
        self.store.path.write_bytes(MAGIC + b"corrupt")
        with self.assertRaises(ValueError):
            self.store.load()

    def test_directory_link_is_rejected(self):
        self.store.profile.mkdir(mode=0o700)
        target = Path(self.directory.name) / "target"
        target.mkdir(mode=0o700)
        try:
            self.store.directory.symlink_to(target, target_is_directory=True)
        except OSError:
            self.skipTest("Creating symlinks requires host privileges")
        with self.assertRaises(ValueError):
            self.store.save(self.credentials)
        self.assertEqual(list(target.iterdir()), [])

    def test_posix_insecure_permissions_are_rejected(self):
        if os.name == "nt":
            return  # the platform roundtrip above exercises real DPAPI instead
        self.store.save(self.credentials)
        self.store.path.chmod(0o644)
        with self.assertRaises(ValueError):
            self.store.load()

    def test_invalid_credentials_never_reach_disk(self):
        for username, token in (("Synthetic", ""), ("Synthetic", "x\0y"), ("bad\nname", "token")):
            with self.assertRaises(ValueError):
                Credentials(username, token)
        self.assertFalse(self.store.directory.exists())
