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
    @unittest.skipUnless(shutil.which(COMPILER), "native C compiler required")
    def test_watchlist_matches_previous_dependency_bytes(self):
        environment = os.environ.copy()
        environment["RA_CC"] = COMPILER
        subprocess.run([sys.executable, str(ROOT / "conformance/ra_dependency/check_watchlist.py")],
                       cwd=ROOT, env=environment, check=True, timeout=180)
