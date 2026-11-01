#!/usr/bin/env python3
"""Build Neutrino's SMB client (usmb2) and run it against smb2_server.

Neutrino's SMB support (ps2max32/neutrino PR #236, by Ronnie Sahlberg) reads
games with usmb2, a deliberately tiny SMB client: dialect 3.0 only, bare
NTLMSSP rather than SPNEGO, NTLMv2, no signing, one request in flight, and
replies that must fit fixed buffers on the IOP. Every one of those is a way a
general-purpose server can be correct and still not work for it, so this runs
the real client rather than a description of it.

No upstream code is vendored. The pinned files are downloaded into a fresh
temporary directory, checked by SHA256, patched at exact anchors (fail closed
if an anchor moves), compiled with the host C compiler, and removed on exit.

What this proves: the pinned client logs in, connects the share, opens, sizes
and reads a file byte-exact against this server on the host, inside the PS2
build's buffer limits. What it does not prove: anything about the PS2's own
TCP stack (ministack/smap), timing, or a real console. POSIX only (BSD sockets).
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

USMB2_COMMIT = "f10c94bf0c523076cf12dbaed74fc6af673b713e"
USMB2_BASE = ("https://raw.githubusercontent.com/sahlberg/usmb2/"
              + USMB2_COMMIT + "/")
USMB2_HASHES = {
    "usmb2.c": "02c848d833a877910eb9023452e96dd8572e89c1b6000b3af490e26d2ac91376",
    "usmb2.h": "81c6632a885c0225d97f97904fec48fa692ff947b4522da2acc49a9c8e5ddb32",
    "ntlm.c": "5536eeb263db9e4bc587694217153758b092dc486651429213a7370d2c71ce0f",
    "ntlm.h": "97822c531e8b31b5f5f6cd5569ea204621636c36b2a3fe9220548e77b945b1c9",
    "md4c.c": "44211c046e2a47d907675a25332344848cb491073e41338c24ddc16377bafe9c",
    "md4.h": "bf8b13f1b40b9dc00dc4d85d0b5105713af2f983fe1a46f95b5fe9be8a0cdce8",
    "md5.c": "fac9ea114171ee9ec7ce18cfdda85e177015c18b2a268c0ba3244ed922d76206",
    "md5.h": "eadba56f77c7dbaaedce677dd5f6c7c7d13d81e0cb80b6ead1c9631ab8eacef4",
    "hmac-md5.c": "a35a32659be090b03da156f24bfe8928d419988b99cfa1a2f2583c0575c66119",
    "hmac-md5.h": "ed1f0a07f541019ccbbf343bcca3a4823bebb4e496cc86929ac7cb89db06f7f2",
}
# Merged PR #236, including its final path-length and initialization fixes.
NEUTRINO_COMMIT = "1bff25bcfa02e192f5b35cadbe53648786813974"
NEUTRINO_BASE = ("https://raw.githubusercontent.com/ps2max32/neutrino/"
                 + NEUTRINO_COMMIT + "/")
NEUTRINO_HASHES = {
    "iop/smb/src/smb_conn.c":
        "715d89b8c34cffacbb9e0e382cc2ba69fba6f1aafec0fadc627e582c86afe89d",
}
# Neutrino's iop/smb/Makefile: -DUSMB2_FEATURE_TINY_TCP -DUSMB2_FEATURE_REASSEMBLY
# -DUSMB2_FEATURE_NTLM. The first two select the PS2 TCP stack, which the host
# replaces with sockets; their limits are enforced by the splices below.
USMB2_FLAGS = ["-DUSMB2_FEATURE_NTLM"]
# USMB2_REASSEMBLY_SIZE in usmb2.c: SPL + SMB2 header + READ reply + 2 KiB.
REASSEMBLY_SIZE = 4 + 64 + 16 + 2048

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


class UpstreamUnavailable(Exception):
    """The pinned upstream source could not be retrieved at all.

    Same rule as conformance/integration/opl_http: not reaching a repository
    nobody here controls skips loudly; anything after the bytes arrive (hash,
    anchors, build, assertions) fails hard.
    """


def fetch(base, hashes, work):
    sources = {}
    for path, digest in hashes.items():
        try:
            with urllib.request.urlopen(base + path, timeout=30) as response:
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


def between(source, start, end=None):
    if source.count(start) != 1 or (end is not None and source.count(end) != 1):
        raise ValueError("Upstream extraction anchor changed: " + start)
    a = source.index(start)
    b = len(source) if end is None else source.index(end)
    if a >= b:
        raise ValueError("Upstream extraction anchors are out of order")
    return source[a:b]


def generate(work):
    usmb2 = fetch(USMB2_BASE, USMB2_HASHES, work)
    neutrino = fetch(NEUTRINO_BASE, NEUTRINO_HASHES, work)

    src = usmb2["usmb2.c"]
    # Port: the client hardcodes 445, which an unprivileged test cannot bind.
    src = splice(src, "        sin.sin_port = htons(445);\n",
                 "        sin.sin_port = htons(usmb2_test_port);\n", "usmb2.c")
    src = "#include <stdio.h>\n#include <stdlib.h>\nextern int usmb2_test_port;\n" + src
    # The PS2 build drops any reply larger than its reassembly buffer.
    src = splice(
        src,
        "        read_from_socket(usmb2, (uint8_t *)&spl, 4);\n"
        "        spl = ntohl(spl);\n",
        "        read_from_socket(usmb2, (uint8_t *)&spl, 4);\n"
        "        spl = ntohl(spl);\n"
        "        if (spl > %d - 4) {\n"
        "                printf(\"REPLY_TOO_BIG cmd=%%d spl=%%u\\n\", command, (unsigned)spl);\n"
        "                exit(70);\n"
        "        }\n" % REASSEMBLY_SIZE,
        "usmb2.c")
    # Everything after the command header lands in usmb2->buf, 512 bytes.
    src = splice(
        src,
        "        read_from_socket(usmb2, &usmb2->buf[commandincount], (int)spl);\n",
        "        if ((int)spl < 0 || commandincount + (int)spl > USMB2_SIZE) {\n"
        "                printf(\"BUF_OVERFLOW cmd=%d need=%d\\n\", command, commandincount + (int)spl);\n"
        "                exit(71);\n"
        "        }\n"
        "        read_from_socket(usmb2, &usmb2->buf[commandincount], (int)spl);\n",
        "usmb2.c")
    (work / "usmb2.c").write_text(src, encoding="utf-8", newline="\n")

    ntlm = splice(
        usmb2["ntlm.c"],
        "        out_pdu_size = offset;\n",
        "        out_pdu_size = offset;\n"
        "        if (out_pdu_size > USMB2_SIZE) {\n"
        "                printf(\"AUTH_OVERFLOW size=%d\\n\", out_pdu_size);\n"
        "                exit(72);\n"
        "        }\n",
        "ntlm.c")
    # The splice calls printf and exit. GCC 14 and clang 16+ make an implicit
    # declaration an error even with -w, so declare them rather than rely on
    # ntlm.c (`--cc clang` reproduces the CI failure with an older GCC).
    ntlm = "#include <stdio.h>\n#include <stdlib.h>\n" + ntlm
    (work / "ntlm.c").write_text(ntlm, encoding="utf-8", newline="\n")
    for name in USMB2_HASHES:
        if name not in ("usmb2.c", "ntlm.c"):
            (work / name).write_text(usmb2[name], encoding="utf-8", newline="\n")

    conn = neutrino["iop/smb/src/smb_conn.c"]
    io_code = "#define SMB_NAME_MAX 127\n" + between(conn, "uint8_t *smb_open(const char *path)\n{")
    template = (HERE / "probe.c.in").read_text(encoding="utf-8")
    if template.count("@UPSTREAM_SMB_IO@") != 1:
        raise ValueError("probe.c.in must contain the marker exactly once")
    (work / "probe.c").write_text(template.replace("@UPSTREAM_SMB_IO@", io_code),
                                  encoding="utf-8", newline="\n")


def build(work, compiler):
    exe = work / "probe"
    sources = [str(work / n) for n in
               ("probe.c", "usmb2.c", "ntlm.c", "md4c.c", "md5.c", "hmac-md5.c")]
    base = [compiler, "-std=gnu99", "-O0", "-g", "-w", "-I", str(work)] + USMB2_FLAGS
    # AddressSanitizer catches a write past the 512-byte buffer as it happens
    # (the splices only catch it at the next check). Use it where the compiler
    # can, and say so when it cannot.
    asan = base + ["-fsanitize=address", "-fno-omit-frame-pointer"]
    result = subprocess.run(asan + sources + ["-o", str(exe)],
                            capture_output=True, text=True, timeout=120)
    if result.returncode == 0:
        print("INFO  built with AddressSanitizer", flush=True)
        return exe
    print("INFO  AddressSanitizer unavailable; relying on the spliced bounds checks",
          flush=True)
    subprocess.run(base + sources + ["-o", str(exe)], check=True, timeout=120)
    return exe


def exercise(work, exe):
    sys.path[:0] = [str(ROOT / "smb2_server")]
    import smb2_wire as wire
    from smb2_auth import Authenticator
    from smb2_paths import Share
    from smb2_server import Smb2Server

    root = work / "games"
    (root / "DVD").mkdir(parents=True)
    # Not a multiple of 2048, so the last read is a short one.
    payload = os.urandom(3 * 1024 * 1024 + 1234)
    game = "SLUS_200.00.Neutrino Probe.iso"
    (root / "DVD" / game).write_bytes(payload)

    def run(ceiling, auth, user, password, share, path):
        server = Smb2Server({"games": Share("games", str(root))}, auth,
                            ceiling=ceiling, read_only=True)
        port = server.listen("127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        out = work / "out.bin"
        if out.exists():
            out.unlink()
        try:
            proc = subprocess.run([str(exe), str(port), user, password, share, path,
                                   str(out)], capture_output=True, text=True,
                                  timeout=120)
        finally:
            server.stop()
            thread.join(timeout=10)
        word = (proc.stdout.strip().splitlines() or [""])[-1]
        data = out.read_bytes() if out.exists() else b""
        return proc.returncode, word, proc.stderr, data

    def expect_ok(label, *args):
        code, word, err, data = run(*args)
        if code != 0 or data != payload:
            raise AssertionError("{}: exit {} {!r} ({} of {} bytes)\n{}".format(
                label, code, word, len(data), len(payload), err[-2000:]))
        print("PASS  " + label, flush=True)

    def expect_refused(label, want, *args):
        code, word, err, _ = run(*args)
        if word != want:
            raise AssertionError("{}: wanted {}, got exit {} {!r}\n{}".format(
                label, want, code, word, err[-2000:]))
        print("PASS  " + label, flush=True)

    smb3, smb2 = wire.CEILING_SMB3, wire.CEILING_SMB2
    users = Authenticator({"ps2": "a long PS2 password"})
    dvd = "DVD/" + game                    # as -dvd=smb:DVD/<file> passes it

    expect_ok("password login, SMB3 mode: open, size and byte-exact read",
              smb3, users, "ps2", "a long PS2 password", "games", dvd)
    expect_ok("share name matched without regard to case",
              smb3, users, "PS2", "a long PS2 password", "GAMES", dvd)
    expect_ok("open share accepts bsd-smb.toml's default user=guest pass=",
              smb3, Authenticator.open(), "guest", "", "games", dvd)
    long_user = "u" * 31                   # smb_args() keeps at most 31
    expect_ok("31-character user name fits the client's 512-byte buffer",
              smb3, Authenticator({long_user: "pw"}), long_user, "pw", "games", dvd)
    expect_refused("wrong password is refused", "LOGIN_FAILED",
                   smb3, users, "ps2", "wrong", "games", dvd)
    expect_refused("SMBv2 mode refuses it: usmb2 offers only SMB 3.0", "LOGIN_FAILED",
                   smb2, users, "ps2", "a long PS2 password", "games", dvd)
    expect_refused("unknown share is refused", "TREE_FAILED",
                   smb3, users, "ps2", "a long PS2 password", "nope", dvd)
    expect_refused("missing game is refused", "OPEN_FAILED",
                   smb3, users, "ps2", "a long PS2 password", "games", "DVD/missing.iso")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cc", default="gcc", help="C compiler executable (default: gcc)")
    parser.add_argument("--require-upstream", action="store_true",
                        help="Fail instead of skipping when the pinned upstream "
                             "source cannot be fetched.")
    args = parser.parse_args()
    if not sys.platform.startswith("linux"):
        # The pinned usmb2 sources include <endian.h>, which only glibc/musl
        # ship; macOS and Windows hosts cannot build them unpatched.
        print("SKIP  the pinned usmb2 host build needs Linux (<endian.h>); run this on Linux.")
        return 0
    compiler = shutil.which(args.cc)
    if not compiler:
        parser.error("C compiler not found: " + args.cc)
    print("Upstream usmb2 commit: " + USMB2_COMMIT, flush=True)
    print("Upstream Neutrino PR #236 commit: " + NEUTRINO_COMMIT, flush=True)
    with tempfile.TemporaryDirectory(prefix="ps2-neutrino-smb-") as directory:
        work = Path(directory)
        try:
            generate(work)
        except UpstreamUnavailable as exc:
            if args.require_upstream:
                print("FAIL  pinned upstream source unreachable: " + str(exc))
                return 1
            print("SKIP  pinned upstream source unreachable: " + str(exc))
            print("SKIP  the Neutrino SMB client check did NOT run. This says nothing "
                  "about whether the server is correct.")
            return 0
        exe = build(work, compiler)
        exercise(work, exe)
    print("PASS  all PC-hosted Neutrino SMB client checks (not PS2 hardware validation)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
