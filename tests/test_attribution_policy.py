import os
import shutil
import subprocess
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class AttributionWorkflowTests(unittest.TestCase):
    def test_tag_with_no_new_commits_does_not_fail_validation(self):
        bash = shutil.which("bash")
        if bash is None:
            self.skipTest("attribution workflow requires a POSIX shell")
        workflow = (ROOT / ".github" / "workflows" / "attribution-policy.yml").read_text(
            encoding="utf-8")
        step = workflow.split("      - name: Check pushed commit attribution\n", 1)[1]
        script = textwrap.dedent(step.split("        run: |\n", 1)[1])
        tag = f"attribution-empty-range-{os.getpid()}"
        try:
            subprocess.run(
                ["git", "tag", "-a", tag, "-m", "attribution test", "HEAD"],
                cwd=ROOT, check=True, capture_output=True, text=True)
            after = subprocess.check_output(
                ["git", "rev-parse", tag], cwd=ROOT, text=True).strip()
            before = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
            result = subprocess.run(
                [bash, "-c", script], cwd=ROOT,
                env=dict(os.environ, BEFORE=before, AFTER=after, DEFAULT_BRANCH="main"),
                capture_output=True, text=True, timeout=30)
        finally:
            subprocess.run(
                ["git", "tag", "-d", tag], cwd=ROOT,
                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("No new commit messages", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
