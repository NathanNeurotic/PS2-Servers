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

    def test_unreachable_upstream_skips_rather_than_failing_ci(self):
        """A deleted or unreachable upstream must not redden every pull request.

        This check gates CI on a repository nobody here controls. Losing the
        ability to REACH it is an availability problem, not a defect in this
        server, so it exits 0 -- loudly, and without pretending the check ran.
        """
        with mock.patch.object(runner.sys, "argv", [str(PATH)]), \
                mock.patch.object(runner.shutil, "which", return_value="gcc"), \
                mock.patch.object(runner, "generate",
                                  side_effect=runner.UpstreamUnavailable("404")), \
                mock.patch.object(runner, "build") as build, \
                mock.patch.object(runner, "exercise") as exercise, \
                mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(runner.main(), 0)
        build.assert_not_called()
        exercise.assert_not_called()
        self.assertIn("SKIP", out.getvalue())
        self.assertNotIn("PASS", out.getvalue())

    def test_require_upstream_turns_the_skip_back_into_a_failure(self):
        """A release gate cannot accept 'we could not check' as a pass."""
        with mock.patch.object(runner.sys, "argv", [str(PATH), "--require-upstream"]), \
                mock.patch.object(runner.shutil, "which", return_value="gcc"), \
                mock.patch.object(runner, "generate",
                                  side_effect=runner.UpstreamUnavailable("404")), \
                mock.patch.object(runner, "exercise") as exercise, \
                mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(runner.main(), 1)
        exercise.assert_not_called()
        self.assertIn("FAIL", out.getvalue())

    def test_a_hash_mismatch_still_fails_hard_and_is_not_a_skip(self):
        """Only unreachability is tolerated. Changed content is a real signal."""
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(runner.urllib.request, "urlopen",
                                  return_value=io.BytesIO(b"unexpected source")):
            with self.assertRaises(ValueError):
                runner.generate(Path(directory))

    def test_changed_upstream_bytes_are_rejected_before_source_generation(self):
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(runner.urllib.request, "urlopen",
                                  return_value=io.BytesIO(b"unexpected source")):
            work = Path(directory)
            with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
                runner.generate(work)
            self.assertEqual(list(work.iterdir()), [])
