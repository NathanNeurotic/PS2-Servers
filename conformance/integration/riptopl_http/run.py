#!/usr/bin/env python3
"""Check the riptopl-path-aware games.csv against RiptOPL's own C code.

Fetches RiptOPL's pinned catalogue parser and request-path builder, compiles
them on the host, parses this server's generated games.csv with them, and
requests every accepted row at the exact URI the console would build. Nothing
upstream is vendored; downloads and binaries live in a temporary directory.

This is PC-hosted evidence only. It does not show that a PS2 lists, launches
or plays anything.
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
import urllib.request

# NathanNeurotic/Open-PS2-Loader rebuild/main. RiptOPL's src/platform/
# httpcatalog.cpp carries the same parser logic (comment paths and one
# constexpr differ).
COMMIT = "bfebafdda10fd7c090c4906e4c8930bc5000cceb"
BASE = "https://raw.githubusercontent.com/NathanNeurotic/Open-PS2-Loader/" + COMMIT + "/"
HASHES = {
    "src/httpcatalog.c": "551bc8ac74085b0f67dede66a6005908e48b13ccb352b683714a3c962b100ab0",
    "include/httpcatalog.h": "0f0b341aca5e8843eecde691bb54b945b651ed4104f252d8044312a4ba178471",
    "src/httpsupport.c": "f01088d9dbd4c6d9c0d3faa0e81b32f4c6c8484c8816c8231f15318c4267f4ad",
    "include/httpsupport.h": "8ea8d51049a3f451616122e6eb39400eb30067bfa1058e1dc475314bee71a70d",
}
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
MEDIA = {"CD": "18", "DVD": "20"}


class UpstreamUnavailable(Exception):
    """The pinned source could not be retrieved; skip loudly, never pass."""


def between(source, start, end):
    """Fail closed if pinned extraction anchors are missing or ambiguous."""
    if source.count(start) != 1 or source.count(end) != 1:
        raise ValueError("Upstream extraction anchor changed: " + start)
    a, b = source.index(start), source.index(end)
    if a >= b:
        raise ValueError("Upstream extraction anchors are out of order")
    return source[a:b]


def generate(work):
    sources = {}
    for path, digest in HASHES.items():
        try:
            with urllib.request.urlopen(BASE + path, timeout=30) as response:
                data = response.read()
        except OSError as exc:
            raise UpstreamUnavailable("{}: {}".format(path, exc)) from exc
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("Upstream SHA256 mismatch: " + path)
        sources[path] = data.decode("utf-8")
    (work / "include").mkdir()
    (work / "include" / "httpcatalog.h").write_text(sources["include/httpcatalog.h"],
                                                    encoding="utf-8", newline="\n")
    (work / "httpcatalog.c").write_text(sources["src/httpcatalog.c"],
                                        encoding="utf-8", newline="\n")
    build_path = between(sources["src/httpsupport.c"],
                         "void httpNormalizeBasePath(char *path, size_t size)",
                         "static void httpServerIpString(")
    template = (HERE / "catalog.c.in").read_text(encoding="utf-8")
    if template.count("@UPSTREAM_BUILD_PATH@") != 1:
        raise ValueError("Template marker missing or duplicated")
    (work / "catalog.c").write_text(template.replace("@UPSTREAM_BUILD_PATH@", build_path),
                                    encoding="utf-8", newline="\n")
    return sources


def check_limits(sources):
    """The server's row/byte caps must equal the client's, or rows vanish."""
    sys.path[:0] = [str(ROOT / "http_server"), str(ROOT / "udpfs_server")]
    import http_server as hs
    header = sources["include/httpsupport.h"]
    for name, value in (("HTTP_CATALOG_BYTES_MAX", hs.RIPTOPL_CATALOG_BYTES_MAX),
                        ("HTTP_CATALOG_ROWS_MAX", hs.RIPTOPL_CATALOG_ROWS_MAX)):
        line = [l for l in header.splitlines() if l.startswith("#define " + name + " ")]
        if len(line) != 1 or int(line[0].split()[2]) != value:
            raise AssertionError("{} differs from the server's limit {}".format(name, value))
    catalog_h = sources["include/httpcatalog.h"]
    for name, value in (("HTTP_CAT_STARTUP_MAX", hs.RIPTOPL_STARTUP_MAX),
                        ("HTTP_CAT_TITLE_MAX", hs.RIPTOPL_TITLE_MAX),
                        ("HTTP_CAT_PATH_MAX", hs.RIPTOPL_PATH_MAX)):
        line = [l for l in catalog_h.splitlines() if l.startswith("#define " + name + " ")]
        if len(line) != 1 or int(line[0].split()[2]) != value:
            raise AssertionError("{} differs from the server's limit {}".format(name, value))
    print("PASS  server limits equal the pinned client limits", flush=True)


def build(work, compiler):
    exe = work / ("catalog" + (".exe" if os.name == "nt" else ""))
    subprocess.run([compiler, "-std=c99", "-O0", "-Wall", "-Wextra", "-I", str(work),
                    str(work / "catalog.c"), str(work / "httpcatalog.c"), "-o", str(exe)],
                   check=True, timeout=60)
    return exe


def exercise(work, exe):
    import http_server as hs
    from compression_selftest import make_cso

    root = work / "games"
    plain = bytes(range(256)) * 512
    files = {
        "DVD/SLUS_201.74.Rumble Racing.iso": plain,
        "CD/SCUS_971.24.Ratchet & Clank, The.iso": plain[::-1],
        "Games/RPG/Deep/SLES_500.01.Nested #1 100%.iso": plain[1:] + b"\0",
        "SLPM_123.45.Ünicode \"Quoted\".iso": plain[2:] + b"\0\0",
        # Same STARTUP as the first row: the client keeps only the first.
        "DVD/SLUS_201.74.Rumble Racing.v2.iso": b"\x55" * 4096,
    }
    for rel, payload in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(payload)
    make_cso(str(root / "DVD" / "SLUS_209.99.Compressed Game.cso"), plain)
    expected = {
        "DVD/SLUS_201.74.Rumble Racing.iso": ("SLUS_201.74", "Rumble Racing", "DVD", plain),
        "CD/SCUS_971.24.Ratchet & Clank, The.iso":
            ("SCUS_971.24", "Ratchet & Clank, The", "CD", plain[::-1]),
        "Games/RPG/Deep/SLES_500.01.Nested #1 100%.iso":
            ("SLES_500.01", "Nested #1 100%", "CD", plain[1:] + b"\0"),
        "SLPM_123.45.Ünicode \"Quoted\".iso":
            ("SLPM_123.45", "Ünicode \"Quoted\"", "CD", plain[2:] + b"\0\0"),
        "DVD/SLUS_209.99.Compressed Game.iso": ("SLUS_209.99", "Compressed Game", "DVD", plain),
    }

    index = hs.GameIndex(str(root), enable_compression=True,
                         profile=hs.PROFILE_RIPTOPL_PATH_AWARE, recursive=True)
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
        for base in ("/", "/PS2/games"):
            status, _, csv = get(base.rstrip("/") + "/games.csv")
            if status != 200:
                raise AssertionError("games.csv status: " + str(status))
            parsed = subprocess.run([str(exe), base], input=csv, capture_output=True,
                                    check=True, timeout=30).stdout.decode("utf-8")
            lines = parsed.splitlines()
            rejects = [l for l in lines if not l.startswith("ROW\t")]
            if rejects:
                raise AssertionError("RiptOPL refused rows: " + repr(rejects))
            rows = {}
            for line in lines:
                _, startup, title, media, path, supported, uri = line.split("\t")
                rows[path] = (startup, title, media, supported, uri)
            if set(rows) != set(expected):
                raise AssertionError("RiptOPL parsed paths differ: " + repr(sorted(rows)))
            for path, (startup, title, media, payload) in expected.items():
                got = rows[path]
                if got[:4] != (startup, title, MEDIA[media], "1"):
                    raise AssertionError("Row differs for {}: {!r}".format(path, got))
                status, headers, body = get(got[4], {"Range": "bytes=0-8191"})
                if status != 206 or body != payload[:8192] or \
                        headers.get("Content-Range") != "bytes 0-8191/{}".format(len(payload)):
                    raise AssertionError("Wrong response for {} at {}".format(path, got[4]))
            print("PASS  base {}: RiptOPL parses every row and each URI serves its own bytes"
                  .format(base), flush=True)
        status, _, _ = get("/SLUS_201.74.Rumble%20Racing.iso", {"Range": "bytes=0-8191"})
        if status != 404:
            raise AssertionError("A bare filename must not resolve in the path-aware profile")
        print("PASS  no bare-filename fallback", flush=True)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cc", default="gcc", help="C compiler executable (default: gcc)")
    parser.add_argument("--require-upstream", action="store_true",
                        help="Fail instead of skipping when the pinned source cannot be fetched.")
    args = parser.parse_args()
    compiler = shutil.which(args.cc)
    if not compiler:
        parser.error("C compiler not found: " + args.cc)
    print("Pinned RiptOPL/Open-PS2-Loader commit: " + COMMIT, flush=True)
    with tempfile.TemporaryDirectory(prefix="ps2-riptopl-http-") as directory:
        work = Path(directory)
        try:
            sources = generate(work)
        except UpstreamUnavailable as exc:
            if args.require_upstream:
                print("FAIL  pinned source unreachable: " + str(exc))
                return 1
            print("SKIP  pinned source unreachable: " + str(exc))
            print("SKIP  the RiptOPL catalogue check did NOT run.")
            return 0
        check_limits(sources)
        exe = build(work, compiler)
        exercise(work, exe)
    print("PASS  all PC-hosted RiptOPL client checks (not PS2 hardware validation)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
