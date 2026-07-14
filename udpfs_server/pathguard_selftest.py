#!/usr/bin/env python3
"""UDPFS path-containment guard self-test.

_resolve_path() is the single security gate between client-supplied paths and
the host filesystem: everything the PS2 opens, lists, or stats goes through
it. This test pins two properties:

  * CONTAINMENT -- traversal (../, absolute paths, backslash variants) can
                   never resolve outside the served root; normal relative
                   paths resolve inside it.
  * UNC ROOTS   -- a Windows NAS root (\\\\server\\share\\...) works. The guard
                   once ran .replace('\\\\\\\\', '\\\\') on its prefix, which ate the
                   leading double-backslash of UNC roots and made EVERY
                   request fail with EACCES ("path traversal or no root_dir"),
                   so remote/NAS libraries could not be served at all.

The UNC section is Windows-only: it exercises ntpath semantics through
os.path, which cannot be faithfully simulated on POSIX. It needs no live
share -- realpath() on an unreachable UNC path keeps the string as-is, so the
guard's pure string logic is fully exercised.

Run:  python udpfs_server/pathguard_selftest.py
"""

import os
import shutil
import sys
import tempfile

import udpfs_server as srv  # sys.path[0] is this dir when run as a script

# Path shape from the field report, but on loopback: resolving a UNC path
# makes Windows attempt a real SMB connection, and an unreachable LAN IP
# stalls ~21s on the first call per boot (measured) -- or, worse, contacts
# whatever actually lives at that address on the developer's network.
# 127.0.0.1 fails/answers instantly and realpath still preserves the UNC
# lead, which is all the guard's string logic needs.
UNC_ROOT = r"\\127.0.0.1\Data\[ROMs&ISOs]\PS2"


def check(cond, what):
    if cond:
        print("  ok: %s" % what)
    else:
        print("  FAIL: %s" % what)
        raise AssertionError(what)


def make_server(root):
    server = srv.UdpfsServer(root_dir=root, port=0, enable_compression=False)
    return server


def test_local_containment():
    print("[containment] local root")
    root = tempfile.mkdtemp(prefix="udpfs_guard_")
    server = make_server(root)
    try:
        os.makedirs(os.path.join(root, "CD"))
        with open(os.path.join(root, "ul.cfg"), "wb") as f:
            f.write(b"x")

        r = server._resolve_path("ul.cfg")
        check(r == os.path.join(server.root_dir, "ul.cfg"),
              "relative file resolves inside root")
        r = server._resolve_path("/CD")
        check(r == os.path.join(server.root_dir, "CD"),
              "leading-slash path resolves inside root")
        r = server._resolve_path("CD/../CD")
        check(r == os.path.join(server.root_dir, "CD"),
              "internal .. stays inside root")
        check(server._resolve_path("") == server.root_dir,
              "empty path resolves to the root itself")

        for evil in ["../evil", "..", "CD/../../evil",
                     "..\\evil", "CD\\..\\..\\evil"]:
            check(server._resolve_path(evil) is None,
                  "traversal blocked: %r" % evil)
    finally:
        server.sock.close()
        server.dsock.close()
        shutil.rmtree(root, ignore_errors=True)


def test_separator_terminated_root():
    """Roots whose realpath already ENDS with a separator -- a bare drive
    root ('C:\\') on Windows, '/' on POSIX. These are the one realpath output
    with a trailing separator; a guard that blindly appends os.sep builds a
    doubled-separator prefix no path matches, EACCES-ing every request (a
    dedicated games drive is a canonical OPL layout)."""
    print("[sep-root] separator-terminated root (drive root / '/')")
    tmp = tempfile.mkdtemp(prefix="udpfs_guard_")
    server = make_server(tmp)
    try:
        if os.name == "nt":
            sep_root = os.path.realpath("C:\\")
            check(sep_root.endswith(os.sep),
                  "realpath('C:\\\\') keeps the trailing separator")
        else:
            sep_root = "/"
        server.root_dir = sep_root

        r = server._resolve_path("PS2/CD/game.iso")
        expected = os.path.join(sep_root, "PS2", "CD", "game.iso")
        check(r == expected, "drive/'/'-root serves nested path: %r" % r)
        r = server._resolve_path("ul.cfg")
        check(r == os.path.join(sep_root, "ul.cfg"),
              "drive/'/'-root serves top-level file")
        check(server._resolve_path("") == sep_root,
              "the root itself resolves")
        # '..' cannot ascend above a drive root / filesystem root: it
        # resolves back to the root, which is still inside the share.
        check(server._resolve_path("..") == sep_root,
              "'..' clamps to the root, never above it")
    finally:
        server.sock.close()
        server.dsock.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_unc_root():
    if os.name != "nt":
        print("[unc] skipped (Windows ntpath semantics only)")
        return
    print("[unc] NAS/UNC root")
    root = tempfile.mkdtemp(prefix="udpfs_guard_")
    server = make_server(root)
    try:
        # Swap in the field-report UNC root. realpath() keeps an unreachable
        # UNC string as-is, matching what __init__ stores for a live share.
        server.root_dir = os.path.realpath(UNC_ROOT)
        check(server.root_dir.startswith("\\\\"),
              "realpath preserves the UNC double-backslash lead")

        # The exact requests from the failing report -- all must resolve.
        for p in ["ul.cfg", "CD/games.bin", "CD", "DVD/games.bin", "DVD"]:
            r = server._resolve_path(p)
            check(r is not None and r.startswith(server.root_dir + os.sep),
                  "UNC root serves %r" % p)

        # Containment must still hold from a UNC root: escaping the served
        # folder, the share, or onto another share is blocked.
        for evil in ["../escape", "../../OtherShare/steal",
                     "..\\..\\..\\..\\OtherShare"]:
            check(server._resolve_path(evil) is None,
                  "UNC traversal blocked: %r" % evil)
        check(server._resolve_path("") == server.root_dir,
              "UNC root itself resolves")

        # A trailing-separator UNC root (realpath keeps it when the share is
        # unreachable at startup) must serve too -- same separator-terminated
        # class as a bare drive root.
        server.root_dir = os.path.realpath(UNC_ROOT) + os.sep
        r = server._resolve_path("CD/games.bin")
        check(r is not None and r.startswith(os.path.realpath(UNC_ROOT)),
              "trailing-separator UNC root serves subpaths")
    finally:
        server.sock.close()
        server.dsock.close()
        shutil.rmtree(root, ignore_errors=True)


def main():
    test_local_containment()
    test_separator_terminated_root()
    test_unc_root()
    print()
    print("ALL UDPFS PATH-GUARD TESTS PASSED")


if __name__ == "__main__":
    main()
