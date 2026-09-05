"""The fetched-client runner must fail closed, especially after build failures."""
import importlib.util
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


PATH = (Path(__file__).resolve().parents[1] / "conformance" / "integration" /
        "opl_http" / "run.py")
SPEC = importlib.util.spec_from_file_location("opl_http_runner", PATH)
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class RunnerFailureTests(unittest.TestCase):
    def test_compilation_failure_never_reaches_exercise(self):
        failure = subprocess.CalledProcessError(1, ["gcc"])
        with mock.patch.object(runner.sys, "argv", [str(PATH)]), \
                mock.patch.object(runner.shutil, "which", return_value="gcc"), \
                mock.patch.object(runner, "generate"), \
                mock.patch.object(runner.subprocess, "run", side_effect=failure) as run, \
                mock.patch.object(runner, "exercise") as exercise:
            with self.assertRaises(subprocess.CalledProcessError):
                runner.main()
            self.assertTrue(run.call_args.kwargs["check"])
            self.assertEqual(run.call_count, 1)
            exercise.assert_not_called()

    def test_changed_upstream_bytes_are_rejected_before_source_generation(self):
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(runner.urllib.request, "urlopen",
                                  return_value=io.BytesIO(b"unexpected source")):
            work = Path(directory)
            with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
                runner.generate(work)
            self.assertEqual(list(work.iterdir()), [])
