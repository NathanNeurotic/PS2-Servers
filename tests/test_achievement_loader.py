import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from launcher.achievement_loader import blob_id, export_loader


class LoaderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.elf = b"\x7fELFfixture"
        self.license = b"Fixture license"
        self.spec = {"url": "https://example.com/loader", "license": "https://example.com/license",
                     "source": "https://example.com/source", "size": len(self.elf),
                     "sha256": hashlib.sha256(self.elf).hexdigest(), "license_blob": blob_id(self.license)}

    def tearDown(self):
        self.temporary.cleanup()

    def test_verified_loader_exports_license_and_provenance(self):
        with mock.patch("launcher.achievement_loader.LOADERS", {"xerabora": self.spec}), \
             mock.patch("launcher.achievement_loader.fetch", side_effect=[self.elf, self.license]):
            output = Path(export_loader("xerabora", self.directory))
        self.assertEqual(output.read_bytes(), self.elf)
        self.assertEqual(output.with_suffix(".LICENSE.txt").read_bytes(), self.license)
        manifest = json.loads(output.with_suffix(".SOURCE.json").read_text())
        self.assertEqual(manifest["downloaded_sha256"], self.spec["sha256"])

    def test_bad_checksum_never_publishes_loader(self):
        with mock.patch("launcher.achievement_loader.LOADERS", {"xerabora": self.spec}), \
             mock.patch("launcher.achievement_loader.fetch", return_value=b"\x7fELFaltered"):
            with self.assertRaises(ValueError):
                export_loader("xerabora", self.directory)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_v2_is_explicit_and_cannot_overwrite_legacy_export(self):
        legacy = self.directory / "OPL-RA-xerabora.ELF"
        legacy.write_bytes(b"preserve legacy")
        with mock.patch("launcher.achievement_loader.LOADERS", {"xerabora-v2": self.spec}), \
             mock.patch("launcher.achievement_loader.fetch", side_effect=[self.elf, self.license]):
            result = Path(export_loader("xerabora", self.directory, watch_format="ran2"))
        self.assertEqual(result.name, "OPL-RA-xerabora-v2.ELF")
        self.assertEqual(legacy.read_bytes(), b"preserve legacy")
        manifest = json.loads(result.with_suffix(".SOURCE.json").read_text())
        self.assertEqual(manifest["watch_format"], "ran2")
        with mock.patch("launcher.achievement_loader.fetch") as fetch:
            for mode, fmt in (("caduceus", "ran2"), ("xerabora", "bad")):
                with self.assertRaises(ValueError):
                    export_loader(mode, self.directory, watch_format=fmt)
            fetch.assert_not_called()

    def test_export_command_exits_before_runtime_or_account_setup(self):
        from launcher.achievements import main
        with mock.patch("launcher.achievement_loader.export_loader", return_value="candidate.ELF") as export, \
             mock.patch("launcher.achievements.engine_path") as engine, \
             mock.patch("launcher.achievements.claim_service") as service:
            self.assertEqual(main(["--watch-format", "ran2", "--export-loader", str(self.directory)]), 0)
            export.assert_called_once_with("xerabora", self.directory, watch_format="ran2")
            engine.assert_not_called()
            service.assert_not_called()

    def test_existing_files_are_preserved(self):
        existing = self.directory / "OPL-RA-xerabora.ELF"
        existing.write_bytes(b"keep")
        with mock.patch("launcher.achievement_loader.LOADERS", {"xerabora": self.spec}), \
             mock.patch("launcher.achievement_loader.fetch", side_effect=[self.elf, self.license]):
            with self.assertRaises(FileExistsError):
                export_loader("xerabora", self.directory)
        self.assertEqual(existing.read_bytes(), b"keep")
        self.assertEqual(len(list(self.directory.iterdir())), 1)
