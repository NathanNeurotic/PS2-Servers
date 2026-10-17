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

    def test_owned_watchlist_matches_baseline_cases(self):
        if not shutil.which(COMPILER):
            if os.environ.get("GITHUB_ACTIONS") == "true" and sys.platform.startswith("linux"):
                self.fail("Linux CI requires a native C compiler for watch-list conformance")
            self.skipTest("native C compiler required")
        import json
        from build.build_ra_evaluator import build
        from launcher.ra_sets import SetContext, qualify_set, derive_direct_watch
        from tests.test_ra_sets import responses
        library = build()
        expected_lines = (ROOT / "conformance/ra_dependency/watchlist-baseline.txt").read_text().splitlines()
        cases = [
            "0xH001000=1_0x001002=2_0xX001004=3",
            "0xL001000=1_0xU001000=2_0xM001001=1",
            "d0xH001000=1_p0xH001001=2",
            "A:0xH001000_A:0xH001001_0xH001002=3",
            "I:0xX001000_0xH000004=1",
            "I:0xX001000_I:0xX000004_0xH000008=1"
        ]
        context = SetContext("Synthetic", 1, "a" * 32, 42, 12)
        for i, expression in enumerate(cases):
            patch, prior = responses([{"ID": 10, "Flags": 3, "MemAddr": expression}])
            data = json.loads(patch)
            data["PatchData"]["ConsoleID"] = 12
            qualified = qualify_set(context, json.dumps(data).encode(), prior)
            wire = derive_direct_watch(library, qualified)
            expected_hex = expected_lines[i].split()[-1]
            self.assertEqual(wire.hex(), expected_hex)

