"""Native watch-list stability and Web API parser conformance."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
COMPILER = "gcc" if os.name == "nt" else "cc"


class NativeWatchlistDependencyTests(unittest.TestCase):
    def test_progress_dates_preserve_independent_hardcore_status(self):
        if not shutil.which(COMPILER):
            if os.environ.get("GITHUB_ACTIONS") == "true" and sys.platform.startswith("linux"):
                self.fail("Linux CI requires a native C compiler for progress-date conformance")
            self.skipTest("native C compiler required")
        environment = os.environ.copy()
        environment["RA_CC"] = COMPILER
        subprocess.run([sys.executable, str(ROOT / "conformance/ra_dependency/check_progress_dates.py")],
                       cwd=ROOT, env=environment, check=True, timeout=90)

    def test_watchlist_matches_previous_dependency_bytes(self):
        if not shutil.which(COMPILER):
            if os.environ.get("GITHUB_ACTIONS") == "true" and sys.platform.startswith("linux"):
                self.fail("Linux CI requires a native C compiler for watch-list conformance")
            self.skipTest("native C compiler required")
        environment = os.environ.copy()
        environment["RA_CC"] = COMPILER
        subprocess.run([sys.executable, str(ROOT / "conformance/ra_dependency/check_watchlist.py")],
                       cwd=ROOT, env=environment, check=True, timeout=180)
