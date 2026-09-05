#!/usr/bin/env python3
"""Build and exercise pinned OPL client logic on loopback (Python 3 + GCC).

No upstream implementation is vendored. Downloads, generated C, executables and
fixtures live in a fresh temporary directory and are removed on exit.
"""
import argparse
import hashlib
import http.client
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

COMMIT = "6fced11a6afafe20c52b8d1a090067e3e1889b99"
BASE = "https://raw.githubusercontent.com/Docmine17/Open-PS2-Loader-HTTP/" + COMMIT + "/"
HASHES = {
    "modules/iopcore/cdvdman/http.c": "59e52f5456ff86ce024d7af647286f29ff0b6303a9db13c4560b97923e5925e8",
    "src/ethsupport.c": "a882cab07b646a1a978628bfe96bf2459b56e99ed598e00980613aa54bb0c881",
}
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def between(source, start, end):
    """Fail closed if pinned extraction anchors are missing or ambiguous."""
    if source.count(start) != 1 or source.count(end) != 1:
        raise ValueError("Upstream extraction anchor changed: " + start)
    a, b = source.index(start), source.index(end)
    if a >= b:
        raise ValueError("Upstream extraction anchors are out of order")
    return source[a:b]


class UpstreamUnavailable(Exception):
    """The pinned upstream source could not be retrieved at all.

    Distinct from every other failure here on purpose. This check gates CI on a
    repository nobody in this project controls -- three days old and described
    by its own author as a proof of concept. If it is deleted, renamed or made
    private, every pull request would go red for a reason unrelated to the
    change being tested. Not being able to REACH the source is an availability
    problem and skips loudly; anything after the bytes arrive (a hash mismatch,
    drifted extraction anchors, a build failure, a failed assertion) is a real
    signal and still fails hard.
    """


def generate(work):
    sources = {}
    for path, digest in HASHES.items():
        try:
            with urllib.request.urlopen(BASE + path, timeout=30) as response:
                data = response.read()
        except OSError as exc:
            # urllib's HTTPError and URLError are both OSError subclasses, so
            # this covers a deleted or private repo (404/403) as well as DNS,
            # TLS and timeout failures. A pinned commit's bytes cannot change
            # underneath us, so a 404 here means the source is gone, not that
            # it was edited -- that case is the hash check below.
            raise UpstreamUnavailable("{}: {}".format(path, exc)) from exc
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("Upstream SHA256 mismatch: " + path)
        (work / Path(path).name).write_bytes(data)
        sources[path] = data.decode("utf-8")
    http = sources["modules/iopcore/cdvdman/http.c"]
    eth = between(sources["src/ethsupport.c"], "static int ethUpdateGameListHTTP(void)\n{",
                  "static int ethUpdateGameList(item_list_t *itemList)\n{")
    replacements = {
        "@UPSTREAM_IO@": between(http, "static int SendData(", "int http_Connect("),
        "@UPSTREAM_RANGE@": between(http, "static void url_encode(", "int http_ReadFile("),
        "@UPSTREAM_CSV@": between(eth, "    int count = 0;",
                                 "    free(csv_buf);\n    return ethGameCount;"),
    }
    for name in ("range", "csv"):
        template = (HERE / (name + ".c.in")).read_text(encoding="utf-8")
        for marker, code in replacements.items():
            if marker in template:
                if template.count(marker) != 1:
                    raise ValueError("Duplicate template marker: " + marker)
                template = template.replace(marker, code)
        if "@UPSTREAM_" in template:
            raise ValueError("Unexpanded upstream template marker")
        (work / (name + ".c")).write_text(template, encoding="utf-8", newline="\n")


def build(work, compiler):
    binaries = {}
    for name in ("range", "csv"):
        exe = work / (name + (".exe" if os.name == "nt" else ""))
        # A unique temporary build directory plus check=True prevents stale runs.
        command = [compiler, "-std=c99", "-O0", "-Wall", "-Wextra",
                   str(work / (name + ".c")), "-o", str(exe)]
        if os.name == "nt" and name == "range":
            command.append("-lws2_32")
        subprocess.run(command, check=True, timeout=60)
        binaries[name] = exe
    return binaries


def exercise(work, binaries):
    sys.path[:0] = [str(ROOT / "http_server"), str(ROOT / "udpfs_server")]
    import http_server as hs
    from compression_selftest import make_cso

    root = work / "games"
    (root / "DVD").mkdir(parents=True)
    (root / "CD").mkdir()
    plain = bytes(range(256)) * 512
    iso = "SLUS_201.74.Rumble Racing.iso"
    amp = "SCUS_971.24.Ratchet & Clank.iso"
    cso = "SLUS_209.99.Compressed Game.iso"
    (root / "DVD" / iso).write_bytes(plain)
    (root / "CD" / amp).write_bytes(plain)
    make_cso(str(root / "DVD" / cso.replace(".iso", ".cso")), plain)
    index = hs.GameIndex(str(root), enable_compression=True)
    server = hs.Ps2HTTPServer(("127.0.0.1", 0), index)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]

    def get(path, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        try:
            connection.request("GET", path, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    try:
        status, _, csv = get("/games.csv")
        if status != 200:
            raise AssertionError("games.csv status: " + str(status))
        parsed = subprocess.run([str(binaries["csv"])], input=csv,
                                capture_output=True, check=True, timeout=30)
        rows = [line.split("\t")[1:] for line in parsed.stdout.decode().splitlines()
                if line.startswith("ROW\t")]
        expected = sorted([["SLUS_201.74", iso, "20"], ["SCUS_971.24", amp, "18"],
                           ["SLUS_209.99", cso, "20"]])
        if sorted(rows) != expected:
            raise AssertionError("OPL parsed rows differ: " + repr(rows))
        print("PASS  upstream CSV parser: exact startup, filename and media", flush=True)
        for _, filename, _ in rows:
            subprocess.run([str(binaries["range"]), "127.0.0.1", str(port), filename],
                           check=True, timeout=30)
        # Exercise the committed stale-size fix through its public list-fetch path.
        (root / "DVD" / iso).write_bytes(plain[:32768])
        time.sleep(index.RESCAN_INTERVAL + 0.1)
        get("/games.csv")
        status, headers, body = get("/" + iso.replace(" ", "%20"),
                                    {"Range": "bytes=0-8191"})
        if (status != 206 or headers.get("Content-Range") != "bytes 0-8191/32768"
                or body != plain[:8192]):
            raise AssertionError("In-place replacement retained stale size or bytes")
        print("PASS  in-place replacement noticed after game-list refresh", flush=True)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cc", default="gcc", help="C compiler executable (default: gcc)")
    parser.add_argument("--require-upstream", action="store_true",
                        help="Fail instead of skipping when the pinned upstream "
                             "source cannot be fetched. For a release gate, "
                             "where 'we could not check' is not good enough.")
    args = parser.parse_args()
    compiler = shutil.which(args.cc)
    if not compiler:
        parser.error("C compiler not found: " + args.cc)
    print("Upstream OPL commit: " + COMMIT, flush=True)
    with tempfile.TemporaryDirectory(prefix="ps2-http-conformance-") as directory:
        work = Path(directory)
        try:
            generate(work)
        except UpstreamUnavailable as exc:
            if args.require_upstream:
                print("FAIL  pinned upstream source unreachable: " + str(exc))
                return 1
            print("SKIP  pinned upstream source unreachable: " + str(exc))
            print("SKIP  the OPL client conformance check did NOT run. This says "
                  "nothing about whether the server is correct -- only that the "
                  "upstream repository could not be reached.")
            return 0
        binaries = build(work, compiler)
        exercise(work, binaries)
    print("PASS  all PC-hosted upstream client checks (not PS2 hardware validation)")
    return 0


if __name__ == "__main__":
    # main() returns the exit code now that a skip is distinct from a pass;
    # dropping it would make --require-upstream silently succeed.
    sys.exit(main())
