"""Compile the production earned-date parser with an offline HTTP fixture."""
import os
from pathlib import Path
import shlex
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def check():
    client = ROOT / "vendor/retroachievements/xerabora/client/src"
    compiler = shlex.split(os.environ.get("RA_CC", "gcc" if os.name == "nt" else "cc"))
    with tempfile.TemporaryDirectory(prefix="ra-progress-dates-") as temporary:
        binary = Path(temporary) / ("check.exe" if os.name == "nt" else "check")
        subprocess.run(compiler + ["-O1", "-std=gnu99", "-I", str(client),
                       str(client / "raweb.c"), str(HERE / "progress_dates.c"),
                       "-o", str(binary)], check=True, timeout=60)
        subprocess.run([str(binary)], check=True, timeout=10)


if __name__ == "__main__":
    check()
