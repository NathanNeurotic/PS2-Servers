#!/usr/bin/env python3
"""Build the PS2 NFS client (unfs4) and run it against nfs4_server.

Neutrino's NFS backing store (ps2max32/neutrino PR 240) and Launchstation's
nfs: driver (PR 366) both read through unfs4, by Ronnie Sahlberg: NFS version
4.0 only, replies read at fixed offsets, and buffers on the IOP that a reply
must not outgrow. A server can be correct NFS and still not work for it, so
this runs the real client rather than a description of it.

No upstream code is vendored. The pinned files are downloaded into a fresh
temporary directory, checked by SHA256, patched at exact anchors (fail closed
if an anchor moves), compiled with the host C compiler, and removed on exit.

Two builds:
  * Neutrino's: UNFS4_SIZE=512 and no optional features, as iop/nfs/Makefile.
    probe.c mounts, opens, sizes and reads a game byte-exact, 2048 bytes per
    READ, and checks each reply fits the 2112-byte reassembly buffer.
  * The full client (every UNFS4_FEATURE_*, as Launchstation's nfsman): the
    upstream unfs4-test.c, which lists, creates, writes, reads back, resizes,
    renames and deletes. Run with writes on, it must pass; with writes off,
    every change must fail and the folder must be untouched.

What this proves: the pinned client reads and (with writes on) changes files
through this server on the host, inside the PS2 builds' buffer limits. What it
does not prove: anything about the PS2's own TCP stack, timing, or a real
console. POSIX only (BSD sockets).
"""
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.request

UNFS4_COMMIT = "3c673fc4c8f793b234ddbdb2b16978aa2844a32d"
UNFS4_BASE = "https://raw.githubusercontent.com/sahlberg/unfs4/" + UNFS4_COMMIT + "/"
UNFS4_HASHES = {
    "unfs4.c": "50d0aa3b60632fac3bcdc7629ac229fbc9469046eb14f36f662ad15340402ccc",
    "unfs4.h": "e250ad90c2b6ffec7a587520852eeedb3fcd7d846a6ca08bbaa3fecfa7bc528e",
    "unfs4-test.c": "4ee7e1b2882e5f4753e7562c61271473153e2876ec29d01c9a227228129886d8",
}
# Neutrino PR 240, iop/nfs/Makefile: -DUNFS4_FEATURE_TINY_TCP
# -DUNFS4_FEATURE_REASSEMBLY -DUNFS4_SIZE=512. The first two select the PS2 TCP
# stack, which the host replaces with sockets; its limit is the splice below.
NEUTRINO_FLAGS = ["-DUNFS4_SIZE=512",
                  # UNFS4_REASSEMBLY_SIZE (64 + 2048), less the record mark
                  "-DUNFS4_TEST_MAX_RECORD=2108"]
FULL_FLAGS = ["-DUNFS4_FEATURE_" + f for f in
              ("OPENDIR", "WRITE", "CREATE", "STAT", "MKDIR", "UNLINK", "RENAME",
               "TRUNCATE")]

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


class UpstreamUnavailable(Exception):
    """The pinned upstream source could not be retrieved at all.

    Same rule as the other conformance harnesses: not reaching a repository
    nobody here controls skips loudly; anything after the bytes arrive (hash,
    anchors, build, assertions) fails hard.
    """


def fetch(work):
    sources = {}
    for path, digest in UNFS4_HASHES.items():
        try:
            with urllib.request.urlopen(UNFS4_BASE + path, timeout=30) as response:
                data = response.read()
        except OSError as exc:
            raise UpstreamUnavailable("{}: {}".format(path, exc)) from exc
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("Upstream SHA256 mismatch: " + path)
        sources[path] = data.decode("utf-8")
    return sources


def splice(source, anchor, replacement, name):
    if source.count(anchor) != 1:
        raise ValueError("Upstream splice anchor changed in {}: {!r}".format(name, anchor))
    return source.replace(anchor, replacement)


def generate(work):
    upstream = fetch(work)
    src = upstream["unfs4.c"]
    # Port: the client hardcodes 2049, which a test must not require.
    src = splice(src, "        sin.sin_port = htons(2049);\n",
                 "        sin.sin_port = htons(unfs4_test_port());\n", "unfs4.c")
    # Nothing of a reply may be left once the client has read what it asked
    # for (bar XDR padding): the sockets build would quietly drop the rest,
    # and the PS2 build would overrun or refuse it. Neutrino's build also
    # refuses a record its reassembly buffer cannot hold.
    src = splice(
        src,
        "        read_reply(unfs4, indata, indatacount, &left);\n"
        "        while (left) {\n",
        "        read_reply(unfs4, indata, indatacount, &left);\n"
        "        if (left > 3 || (get32(b) & 0x7fffffff) > UNFS4_TEST_MAX_RECORD) {\n"
        "                printf(\"REPLY_TOO_BIG record=%u left=%u\\n\",\n"
        "                       (unsigned)(get32(b) & 0x7fffffff), (unsigned)left);\n"
        "                exit(70);\n"
        "        }\n"
        "        while (left) {\n",
        "unfs4.c")
    src = ("#include <stdio.h>\n#include <stdlib.h>\n"
           "#ifndef UNFS4_TEST_MAX_RECORD\n#define UNFS4_TEST_MAX_RECORD 0x7fffffff\n#endif\n"
           "static int unfs4_test_port(void)\n{\n"
           "        const char *p = getenv(\"UNFS4_TEST_PORT\");\n"
           "        return p ? atoi(p) : 2049;\n}\n" + src)
    (work / "unfs4.c").write_text(src, encoding="utf-8", newline="\n")
    for name in ("unfs4.h", "unfs4-test.c"):
        (work / name).write_text(upstream[name], encoding="utf-8", newline="\n")
    shutil.copy(HERE / "probe.c", work / "probe.c")


def build(work, compiler, name, sources, flags):
    exe = work / name
    sources = [str(work / s) for s in sources]
    base = [compiler, "-std=gnu99", "-O0", "-g", "-w", "-I", str(work)] + flags
    # AddressSanitizer catches a write past the client's buffer as it happens.
    asan = base + ["-fsanitize=address", "-fno-omit-frame-pointer"]
    result = subprocess.run(asan + sources + ["-o", str(exe)],
                            capture_output=True, text=True, timeout=120)
    if result.returncode == 0:
        print("INFO  built {} with AddressSanitizer".format(name), flush=True)
        return exe
    print("INFO  AddressSanitizer unavailable for {}; relying on the spliced "
          "bounds check".format(name), flush=True)
    subprocess.run(base + sources + ["-o", str(exe)], check=True, timeout=120)
    return exe


def snapshot(root):
    out = {}
    for top, dirs, files in os.walk(root):
        for name in dirs + files:
            path = os.path.join(top, name)
            data = None
            if os.path.isfile(path):
                st = os.stat(path)
                data = (st.st_size, hashlib.sha256(open(path, "rb").read()).hexdigest()
                        if st.st_size < 64 * 1024 * 1024 else st.st_mtime_ns)
            out[os.path.relpath(path, root)] = data
    return out


def exercise(work, probe, full_test):
    sys.path[:0] = [str(ROOT / "nfs4_server")]
    from nfs4_server import Nfs4Server

    root = work / "games"
    (root / "DVD").mkdir(parents=True)
    # Not a multiple of 2048, so the last read is a short one.
    payload = os.urandom(3 * 1024 * 1024 + 1234)
    game = "SLUS_200.00.Neutrino Probe.iso"
    (root / "DVD" / game).write_bytes(payload)
    (root / "README.txt").write_bytes(b"cat me through NFS\n")
    huge = root / "DVD" / "huge.iso"
    huge_size = 5 * 1024 ** 3 + 2048 * 3 + 517
    tail = os.urandom(10000)
    try:
        with open(huge, "wb") as f:
            f.seek(huge_size - len(tail))
            f.write(tail)
    except OSError:
        huge = None
    env = dict(os.environ, ASAN_OPTIONS="exitcode=99:detect_leaks=0")

    def run(allow_writes, argv):
        server = Nfs4Server(str(root), allow_writes=allow_writes, log=lambda m: None)
        port = server.listen("127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            return subprocess.run(argv, capture_output=True, text=True, timeout=600,
                                  env=dict(env, UNFS4_TEST_PORT=str(port)))
        finally:
            server.stop()
            thread.join(timeout=10)

    def read_game(label, export, path, want, tail_bytes=None):
        out = work / "out.bin"
        if out.exists():
            out.unlink()
        argv = [str(probe), export, path, str(out)]
        if tail_bytes:
            argv.append(str(tail_bytes))
        proc = run(False, argv)
        data = out.read_bytes() if out.exists() else b""
        if proc.returncode != 0 or data != want:
            raise AssertionError("{}: exit {} {!r} ({} of {} bytes)\n{}".format(
                label, proc.returncode, proc.stdout.strip()[-300:], len(data),
                len(want), proc.stderr[-3000:]))
        print("PASS  " + label, flush=True)

    read_game("Neutrino build: mount /, open, size and byte-exact read",
              "/", "DVD/" + game, payload)
    read_game("Neutrino build: a sub-folder as the export", "/DVD", game, payload)
    if huge is not None:
        read_game("Neutrino build: the tail of a 5 GiB game (64-bit offsets)",
                  "/", "DVD/huge.iso", tail, len(tail))
    else:
        print("SKIP  no sparse files here; the 5 GiB read did not run", flush=True)
    proc = run(False, [str(probe), "/", "DVD/missing.iso", str(work / "x")])
    if proc.stdout.strip() != "OPEN_FAILED status=2":
        raise AssertionError("missing game: {!r}\n{}".format(proc.stdout, proc.stderr[-2000:]))
    print("PASS  Neutrino build: a missing game is NOENT", flush=True)

    # Upstream's own test, as the full client. It works in a folder of its
    # own and removes it again, so the share must end exactly as it began.
    before = snapshot(root)
    proc = run(True, [str(full_test), "127.0.0.1", "/", "README.txt"])
    if proc.returncode != 0 or "PASSED: 0 failures" not in proc.stdout:
        raise AssertionError("unfs4-test, writes on: exit {}\n{}\n{}".format(
            proc.returncode, proc.stdout[-4000:], proc.stderr[-3000:]))
    if "cat me through NFS" not in proc.stdout:
        raise AssertionError("unfs4-test did not print README.txt:\n" + proc.stdout[-2000:])
    if snapshot(root) != before:
        raise AssertionError("unfs4-test, writes on, left the share changed")
    print("PASS  full client, writes on: upstream unfs4-test passes "
          "(list, create, write, read back, resize, rename, delete)", flush=True)

    proc = run(False, [str(full_test), "127.0.0.1", "/", "README.txt"])
    lines = proc.stdout.splitlines()
    if proc.returncode != 1 or "ok   opendir" not in lines:
        raise AssertionError("unfs4-test, writes off: exit {}\n{}\n{}".format(
            proc.returncode, proc.stdout[-4000:], proc.stderr[-3000:]))
    for change in ("mkdir", "create", "rename", "create 20 files"):
        if "FAIL " + change not in lines:
            raise AssertionError("writes off, but '{}' did not fail:\n{}".format(
                change, proc.stdout[-4000:]))
    if snapshot(root) != before:
        raise AssertionError("writes off, yet the share changed")
    print("PASS  full client, writes off: every change refused, share untouched",
          flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cc", default="gcc", help="C compiler executable (default: gcc)")
    parser.add_argument("--require-upstream", action="store_true",
                        help="Fail instead of skipping when the pinned upstream "
                             "source cannot be fetched.")
    args = parser.parse_args()
    if os.name != "posix":
        print("SKIP  the pinned unfs4 host build needs BSD sockets; run this on Linux or macOS.")
        return 0
    compiler = shutil.which(args.cc)
    if not compiler:
        parser.error("C compiler not found: " + args.cc)
    print("Upstream unfs4 commit: " + UNFS4_COMMIT, flush=True)
    with tempfile.TemporaryDirectory(prefix="ps2-neutrino-nfs-") as directory:
        work = Path(directory)
        try:
            generate(work)
        except UpstreamUnavailable as exc:
            if args.require_upstream:
                print("FAIL  pinned upstream source unreachable: " + str(exc))
                return 1
            print("SKIP  pinned upstream source unreachable: " + str(exc))
            print("SKIP  the unfs4 client check did NOT run. This says nothing "
                  "about whether the server is correct.")
            return 0
        probe = build(work, compiler, "probe", ["probe.c", "unfs4.c"], NEUTRINO_FLAGS)
        full_test = build(work, compiler, "unfs4-test", ["unfs4-test.c", "unfs4.c"],
                          FULL_FLAGS)
        exercise(work, probe, full_test)
    print("PASS  all PC-hosted unfs4 client checks (not PS2 hardware validation)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
