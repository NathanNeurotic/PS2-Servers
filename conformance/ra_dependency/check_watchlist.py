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
    sources_ref = sorted((rc / "src/rcheevos").glob("*.c"))
    sources_ref += [rc / "src/rc_util.c", rc / "src/rc_compat.c", rc / "src/rhash/md5.c",
                    client / "watchlist.c", client / "snapshot.c", HERE / "watchlist.c"]
    compiler = shlex.split(os.environ.get("RA_CC", "gcc" if os.name == "nt" else "cc"))
    command = compiler + ["-O1", "-std=gnu99"]
    include_dirs = [rc / "include", rc / "src", rc / "src/rcheevos"]
    command_ref = list(command)
    for directory in include_dirs + [client, ROOT / "vendor/retroachievements/xerabora/protocol"]:
        command_ref += ["-I", str(directory)]
    expected = (HERE / "watchlist-baseline.txt").read_text()

    with tempfile.TemporaryDirectory(prefix="ra-watchlist-") as temporary:
        binary_ref = Path(temporary) / ("check_ref.exe" if os.name == "nt" else "check_ref")
        subprocess.run(command_ref + list(map(str, sources_ref)) + ["-o", str(binary_ref), "-lm"], check=True)
        actual_ref = subprocess.check_output([str(binary_ref)], text=True)

    if actual_ref.splitlines() != expected.splitlines():
        raise AssertionError("Reference watch-list bytes differ from the pinned baseline:\n" + actual_ref)
    print("Six native watch-list cases match the pre-upgrade baseline byte-for-byte")

    sources_owned = sorted((rc / "src/rcheevos").glob("*.c"))
    sources_owned += [rc / "src/rc_util.c", rc / "src/rc_compat.c", rc / "src/rhash/md5.c",
                      ROOT / "native/ra_watch_probe.c", HERE / "watchlist_owned.c"]
    command_owned = list(command)
    for directory in include_dirs:
        command_owned += ["-I", str(directory)]

    with tempfile.TemporaryDirectory(prefix="ra-watchlist-owned-") as temporary:
        binary_owned = Path(temporary) / ("check_owned.exe" if os.name == "nt" else "check_owned")
        subprocess.run(command_owned + list(map(str, sources_owned)) + ["-o", str(binary_owned), "-lm"], check=True)
        actual_owned = subprocess.check_output([str(binary_owned)], text=True)

    if actual_owned.splitlines() != expected.splitlines():
        raise AssertionError("Owned watch-list bytes differ from the pinned baseline:\n" + actual_owned)
    print("Six owned watch-list cases match the pre-upgrade baseline byte-for-byte")

    # Captured from tagged xeRAbora v2.0.0 using the same rcheevos revision.
    # Keep legacy goldens separately: matching the two current compilers alone
    # would not detect a shared regression.
    expected_v2 = (HERE / "watchlist-v2-baseline.txt").read_text().splitlines()
    with tempfile.TemporaryDirectory(prefix="ra-watchlist-v2-") as temporary:
        for name, command, sources in (("managed", command_ref, sources_ref),
                                        ("owned", command_owned, sources_owned)):
            binary = Path(temporary) / (name + (".exe" if os.name == "nt" else ""))
            subprocess.run(command + ["-DV2_WATCHLIST"] + list(map(str, sources))
                           + ["-o", str(binary), "-lm"], check=True)
            actual = subprocess.check_output([str(binary)], text=True)
            if actual.splitlines() != expected_v2:
                raise AssertionError(name + " v2 watch-list bytes differ from tagged baseline:\n" + actual)
    print("Ten managed and owned v2 cases match tagged upstream plans byte-for-byte")


if __name__ == "__main__":
    check()
