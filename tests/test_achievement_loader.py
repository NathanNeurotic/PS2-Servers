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

    def test_existing_files_are_preserved(self):
        existing = self.directory / "OPL-RA-xerabora.ELF"
        existing.write_bytes(b"keep")
        with mock.patch("launcher.achievement_loader.LOADERS", {"xerabora": self.spec}), \
             mock.patch("launcher.achievement_loader.fetch", side_effect=[self.elf, self.license]):
            with self.assertRaises(FileExistsError):
                export_loader("xerabora", self.directory)
        self.assertEqual(existing.read_bytes(), b"keep")
        self.assertEqual(len(list(self.directory.iterdir())), 1)
