"""Compare native watch-list output with the pre-upgrade rcheevos baseline.

Run with a native GCC/Clang compiler: python conformance/ra_dependency/check_watchlist.py
RA_CC may select another compiler. No account, network or console is used.
"""
import os
from pathlib import Path
import shlex
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def check():
    rc = ROOT / "vendor/retroachievements/rcheevos"
    client = ROOT / "vendor/retroachievements/xerabora/client/src"
    sources = sorted((rc / "src/rcheevos").glob("*.c"))
    sources += [rc / "src/rc_util.c", rc / "src/rc_compat.c", rc / "src/rhash/md5.c",
                client / "watchlist.c", HERE / "watchlist.c"]
    compiler = shlex.split(os.environ.get("RA_CC", "gcc" if os.name == "nt" else "cc"))
    command = compiler + ["-O1", "-std=gnu99"]
    for directory in [rc / "include", rc / "src", rc / "src/rcheevos", client,
                      ROOT / "vendor/retroachievements/xerabora/protocol"]:
        command += ["-I", str(directory)]
    with tempfile.TemporaryDirectory(prefix="ra-watchlist-") as temporary:
        binary = Path(temporary) / ("check.exe" if os.name == "nt" else "check")
        subprocess.run(command + list(map(str, sources)) + ["-o", str(binary), "-lm"], check=True)
        actual = subprocess.check_output([str(binary)], text=True)
    expected = (HERE / "watchlist-baseline.txt").read_text()
    if actual.splitlines() != expected.splitlines():
        raise AssertionError("Native watch-list bytes differ from the pinned baseline:\n" + actual)
    print("Six native watch-list cases match the pre-upgrade baseline byte-for-byte")


if __name__ == "__main__":
    check()
