"""Native watch-list stability across rcheevos dependency updates."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
COMPILER = "gcc" if os.name == "nt" else "cc"


class NativeWatchlistDependencyTests(unittest.TestCase):
    def test_watchlist_matches_previous_dependency_bytes(self):
        if not shutil.which(COMPILER):
            if os.environ.get("GITHUB_ACTIONS") == "true" and sys.platform.startswith("linux"):
                self.fail("Linux CI requires a native C compiler for watch-list conformance")
            self.skipTest("native C compiler required")
        environment = os.environ.copy()
        environment["RA_CC"] = COMPILER
        subprocess.run([sys.executable, str(ROOT / "conformance/ra_dependency/check_watchlist.py")],
                       cwd=ROOT, env=environment, check=True, timeout=180)
