#!/usr/bin/env python3
"""Build the libsmb2 the PS2 SMB2 browse drivers use and run it against smb2_server.

Two PS2 programs list and copy files over SMB2 with Ronnie Sahlberg's libsmb2:

  * RiptOPL's smb2man.irx (the game list, the share picker, VMC and config
    files) vendors libsmb2 c6e4997 and offers SMB 2.0.2/2.1 only.
  * Launchstation ships smb2man.irx built from libsmb2's wLaunchELF branch,
    d61b781, and offers every dialect libsmb2 knows.

libsmb2 asks a server for things smaller clients never do -- a directory class
(FileIdFullDirectoryInformation), an attribute-tag query beside every stat,
three-request related compounds that need more than one credit, and the share
list over IPC$/srvsvc -- and each one a server lacks fails a whole feature
rather than a single call. So this runs the real library, at both pins, through
the operations those drivers perform, in both SMBv2 and SMBv3 server modes.

No upstream code is vendored. Each pin is fetched by commit id (git verifies
the content against the id), built with CMake and the host compiler in a fresh
temporary directory, and removed on exit. The checks use libsmb2's own example
programs, unmodified.

What this proves: those libsmb2 versions log in with NTLMv2, list, stat, read
byte-exact, write, truncate and enumerate shares against this server on the
host. What it does not prove: anything about the IOP, SMAP or a real console.
RiptOPL's in-game reader (smb2_cdvdman) is separate code and is not run here.
POSIX only.
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading

UPSTREAM = "https://github.com/sahlberg/libsmb2"
PINS = {
    # name: (commit, extra URL arguments matching what that driver sets)
    "riptopl": ("c6e4997fcbdbb0e2f1fe7a0c74483b4533859b52", "?vers=2"),
    "launchstation": ("d61b78126e77f731ce85a3cf663eb6c780d4f371", ""),
}
EXAMPLES = ["smb2-ls-async", "smb2-stat-sync", "smb2-cat-sync", "smb2-put-sync",
            "smb2-truncate-sync", "smb2-share-enum-sync"]

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "smb2_server"))

import smb2_wire as wire                    # noqa: E402
from smb2_auth import Authenticator         # noqa: E402
from smb2_paths import Share                # noqa: E402
from smb2_server import Smb2Server          # noqa: E402

USER, PASSWORD = "ps2", "secret"
GAME_SIZE = 3 * 1024 * 1024 + 77            # several reads, an odd tail


class UpstreamUnavailable(Exception):
    """The pinned upstream source could not be retrieved at all.

    Same rule as the other conformance runners: not reaching a repository
    nobody here controls skips loudly; anything after the source arrives
    (build, assertions) fails hard.
    """


def fetch(commit, dest):
    dest.mkdir(parents=True)
    git = ["git", "-C", str(dest)]
    subprocess.run(git + ["init", "-q"], check=True)
    subprocess.run(git + ["remote", "add", "origin", UPSTREAM], check=True)
    try:
        subprocess.run(git + ["fetch", "-q", "--depth", "1", "origin", commit],
                       check=True, timeout=180)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise UpstreamUnavailable("could not fetch libsmb2 %s: %s" % (commit, exc))
    subprocess.run(git + ["checkout", "-q", "FETCH_HEAD"], check=True)
    head = subprocess.run(git + ["rev-parse", "HEAD"], check=True,
                          capture_output=True, text=True).stdout.strip()
    if head != commit:
        raise SystemExit("fetched %s, wanted %s" % (head, commit))


def build(src):
    out = src / "build"
    subprocess.run(["cmake", "-S", str(src), "-B", str(out), "-DENABLE_EXAMPLES=ON",
                    "-DENABLE_LIBKRB5=OFF", "-DENABLE_GSSAPI=OFF",
                    "-DCMAKE_BUILD_TYPE=Debug"],
                   check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["cmake", "--build", str(out), "-j", str(os.cpu_count() or 2),
                    "--target"] + EXAMPLES, check=True, stdout=subprocess.DEVNULL)
    return out / "examples"


class Case:
    def __init__(self, label):
        self.label = label
        self.failed = None


def run_checks(name, examples, url_args, ceiling, work):
    share_dir = work / ("share-%s-%d" % (name, ceiling))
    (share_dir / "DVD").mkdir(parents=True)
    game = bytes((i * 7 + 3) & 0xFF for i in range(GAME_SIZE))
    (share_dir / "DVD" / "TEST.iso").write_bytes(game)
    creds = work / "ntlm_user"
    creds.write_text(":%s:%s\n" % (USER, PASSWORD))

    server = Smb2Server({"PS2SMB": Share("PS2SMB", str(share_dir))},
                        Authenticator({USER: PASSWORD}), ceiling=ceiling)
    port = server.listen("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env = dict(os.environ, NTLM_USER_FILE=str(creds))
    base = "smb://%s@127.0.0.1:%d" % (USER, port)
    mode = "SMBv%d" % (3 if ceiling == wire.CEILING_SMB3 else 2)
    failures = []

    def run(label, args, check):
        try:
            p = subprocess.run([str(examples / args[0])] + args[1:], env=env,
                               cwd=str(work), capture_output=True, timeout=60)
            why = check(p)
        except subprocess.TimeoutExpired:
            why = "timed out (a request the client never sent, or no reply)"
        tag = "%s %s: %s" % (name, mode, label)
        if why:
            failures.append(tag)
            print("FAIL", tag, "--", why)
        else:
            print("ok  ", tag)

    def ok_and(pred, msg):
        def check(p):
            if p.returncode != 0:
                return "exit %d: %s" % (p.returncode,
                                       (p.stdout + p.stderr).decode("utf-8", "replace")[-300:])
            return None if pred(p) else msg
        return check

    def url(path):
        return base + "/PS2SMB/" + path + url_args

    run("list the share root", ["smb2-ls-async", url("")],
        ok_and(lambda p: b"DVD" in p.stdout, "DVD not listed"))
    run("list a folder", ["smb2-ls-async", url("DVD")],
        ok_and(lambda p: b"TEST.iso" in p.stdout, "TEST.iso not listed"))
    run("stat a game", ["smb2-stat-sync", url("DVD/TEST.iso")],
        ok_and(lambda p: str(GAME_SIZE).encode() in p.stdout, "wrong size"))
    run("read a game byte-exact", ["smb2-cat-sync", url("DVD/TEST.iso")],
        ok_and(lambda p: p.stdout == game, "bytes differ"))
    payload = os.urandom(200000)
    (work / "up.bin").write_bytes(payload)
    run("write a file", ["smb2-put-sync", str(work / "up.bin"), url("new.bin")],
        ok_and(lambda p: (share_dir / "new.bin").read_bytes() == payload,
               "written bytes differ"))
    run("truncate a file", ["smb2-truncate-sync", url("new.bin"), "1000"],
        ok_and(lambda p: (share_dir / "new.bin").stat().st_size == 1000,
               "size not 1000"))
    run("list the shares (IPC$ srvsvc)",
        ["smb2-share-enum-sync", base + "/IPC$" + url_args],
        ok_and(lambda p: b"PS2SMB" in p.stdout, "PS2SMB not in the share list"))

    server.stop()
    return failures


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--require-upstream", action="store_true",
                    help="fail instead of skipping when upstream is unreachable")
    ap.add_argument("--keep", action="store_true", help="keep the build directory")
    args = ap.parse_args()

    for tool in ("git", "cmake", "cc"):
        if shutil.which(tool) is None:
            print("SKIP: %s not installed" % tool)
            return 2 if args.require_upstream else 0

    work = Path(tempfile.mkdtemp(prefix="libsmb2-conf-"))
    failures = []
    try:
        for name, (commit, url_args) in PINS.items():
            src = work / ("libsmb2-" + name)
            try:
                fetch(commit, src)
            except UpstreamUnavailable as exc:
                print("SKIP:", exc)
                if args.require_upstream:
                    return 2
                continue
            examples = build(src)
            for ceiling in (wire.CEILING_SMB2, wire.CEILING_SMB3):
                failures += run_checks(name, examples, url_args, ceiling, work)
    finally:
        if args.keep:
            print("kept", work)
        else:
            shutil.rmtree(work, ignore_errors=True)

    if failures:
        print("%d libsmb2 check(s) failed" % len(failures))
        return 1
    print("libsmb2 conformance: all passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
