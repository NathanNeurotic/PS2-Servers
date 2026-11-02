"""MKDIR, REMOVE and RMDIR act on the entry the console named, never a link's target.

The three handlers resolved their path with _resolve_path(), which realpath()s
all of it, so a symlink in the last component was replaced by its target.
Measured on a share holding link.elf -> GAMES/real.elf: DREAD lists link.elf
from lstat (mode 0, 14 bytes), Launchstation shows it as a 14-byte file, and
deleting it from the PS2 deleted GAMES/real.elf, answered 0 and left the link.
RMDIR of a link to an empty folder removed that folder the same way, and MKDIR
over a dangling link created the directory wherever the link pointed.

REMOVE and RMDIR also went through a linked folder on the way: deleting
fulllink/keep.bin deleted FULL/keep.bin. A Windows junction is listed by DREAD
as a folder, so a console deleting a folder recursively would go into one and
empty the folder it points at; both kinds of link are now refused there.

These drive the real handlers of both server classes -- the standalone
UdpfsServer and ps2servers_core.AutoUdpfsServer, which the launcher runs -- on
a real filesystem, and check what is left on disk afterwards.

Run:  python -m unittest tests.test_udpfs_symlink_path_ops -v
"""

import errno
import os
import shutil
import struct
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UDPFS = os.path.join(_ROOT, "udpfs_server")
if _UDPFS not in sys.path:
    sys.path.insert(0, _UDPFS)

import ps2servers_core  # noqa: E402
import udpfs_server as srv  # noqa: E402

ADDR = ("192.168.0.245", 62966)

MKDIR, REMOVE, RMDIR = 0x20, 0x22, 0x24
O_WRONLY, O_CREAT, O_TRUNC = 0x0002, 0x0200, 0x0400
IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003  # winnt.h: a junction

ELF_BYTES = b"real elf bytes"


def _path_request(opcode, path, mode=0):
    """udpfs_msg_path_req_t plus the NUL-terminated path, padded to 4 bytes."""
    body = path.encode("utf-8") + b"\0"
    body += b"\0" * (-len(body) % 4)
    return struct.pack("<BBH", opcode, 0, mode) + body


def _open_request(path, flags, is_dir=False):
    body = path.encode("utf-8") + b"\0"
    body += b"\0" * (-len(body) % 4)
    return struct.pack("<BBHi", 0x10, 1 if is_dir else 0, flags, 0o644) + body


class _PathOpsMixin:
    """Every test runs once per server class; see the subclasses at the end."""

    server_class = None

    # -- fixture --------------------------------------------------------------
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="udpfs_links_")
        self.outside = tempfile.mkdtemp(prefix="udpfs_outside_")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.outside, ignore_errors=True)

        os.makedirs(self.at("GAMES"))
        self.write(self.at("GAMES", "real.elf"), ELF_BYTES)
        os.makedirs(self.at("EMPTY"))
        os.makedirs(self.at("FULL"))
        self.write(self.at("FULL", "keep.bin"), b"keep")
        self.write(os.path.join(self.outside, "secret.bin"), b"secret")
        os.makedirs(os.path.join(self.outside, "OUTDIR"))

        try:
            self.link("GAMES/real.elf", "link.elf")
            self.link("EMPTY", "dirlink", is_dir=True)
            self.link("FULL", "fulllink", is_dir=True)
            self.link(os.path.join(self.outside, "secret.bin"), "outlink")
            self.link(os.path.join(self.outside, "OUTDIR"), "outdirlink", is_dir=True)
            self.link("GAMES/ghost", "dangling", is_dir=True)
            self.link(os.path.join(self.outside, "ghostdir"), "dangling_out", is_dir=True)
        except (OSError, NotImplementedError) as exc:
            # Windows grants symlink creation only to administrators or in
            # developer mode; CI runs this module on Linux.
            self.skipTest(f"cannot create symlinks here: {exc}")

        self.server = self.make_server(self.root)

    def make_server(self, root, read_only=False):
        server = self.server_class(root_dir=root, port=0, read_only=read_only)
        self.addCleanup(server.sock.close)
        if server.dsock is not server.sock:
            self.addCleanup(server.dsock.close)
        server.replies = []
        server.events = []
        server._print_event = server.events.append
        server._send_result_reply = lambda addr, result: server.replies.append(result)
        server._send_open_reply = (
            lambda addr, handle, stat_info=None: server.replies.append(handle))
        server._send_dread_reply = (
            lambda addr, result, name=None, stat_info=None:
            server.replies.append((result, name, stat_info)))
        # handles/next_handle are per-client properties read from the worker
        # thread's session; this thread stands in for that worker.
        server._local = threading.local()
        server._local.session = types.SimpleNamespace(handles={}, next_handle=1)
        return server

    def at(self, *parts):
        return os.path.join(self.root, *parts)

    @staticmethod
    def write(path, data):
        with open(path, "wb") as f:
            f.write(data)

    @staticmethod
    def read(path):
        with open(path, "rb") as f:
            return f.read()

    def link(self, target, name, is_dir=False):
        os.symlink(target, self.at(name), target_is_directory=is_dir)

    def op(self, opcode, path, server=None):
        server = server or self.server
        handler = {MKDIR: server._handle_mkdir, REMOVE: server._handle_remove,
                   RMDIR: server._handle_rmdir}[opcode]
        handler(ADDR, _path_request(opcode, path, 0o755 if opcode == MKDIR else 0))
        return server.replies[-1]

    def open_reply(self, path, flags, server=None):
        server = server or self.server
        server._handle_open(ADDR, _open_request(path, flags))
        return server.replies[-1]

    def assertLink(self, name):
        self.assertTrue(os.path.islink(self.at(name)), f"{name} is no longer a symlink")

    def assertGone(self, name):
        self.assertFalse(os.path.lexists(self.at(name)), f"{name} still exists")

    # -- REMOVE -----------------------------------------------------------------
    def test_remove_symlink_removes_the_link_and_keeps_its_target(self):
        self.assertEqual(self.op(REMOVE, "link.elf"), 0)
        self.assertTrue(os.path.exists(self.at("GAMES", "real.elf")),
                        "REMOVE of a symlink deleted the file it points at")
        self.assertEqual(self.read(self.at("GAMES", "real.elf")), ELF_BYTES)
        self.assertGone("link.elf")

    def test_remove_symlink_to_a_file_outside_the_root_removes_only_the_link(self):
        # The link lives inside the share, so it is the console's to delete;
        # what it points at is not, and must not be reached.
        self.assertEqual(self.op(REMOVE, "outlink"), 0)
        self.assertGone("outlink")
        self.assertEqual(self.read(os.path.join(self.outside, "secret.bin")), b"secret")

    def test_remove_dangling_symlinks(self):
        self.assertEqual(self.op(REMOVE, "dangling"), 0)
        self.assertGone("dangling")
        self.assertEqual(self.op(REMOVE, "dangling_out"), 0)
        self.assertGone("dangling_out")

    def test_remove_symlink_to_a_directory_removes_only_the_link(self):
        self.assertEqual(self.op(REMOVE, "fulllink"), 0)
        self.assertGone("fulllink")
        self.assertEqual(self.read(self.at("FULL", "keep.bin")), b"keep")

    def test_remove_of_a_real_directory_is_eisdir(self):
        self.assertEqual(self.op(REMOVE, "FULL"), -errno.EISDIR)
        self.assertEqual(self.op(REMOVE, ""), -errno.EISDIR)
        self.assertEqual(self.read(self.at("FULL", "keep.bin")), b"keep")

    def test_remove_regular_file_and_missing_file(self):
        self.assertEqual(self.op(REMOVE, "GAMES/real.elf"), 0)
        self.assertFalse(os.path.exists(self.at("GAMES", "real.elf")))
        self.assertEqual(self.op(REMOVE, "GAMES/real.elf"), -errno.ENOENT)

    # -- RMDIR ------------------------------------------------------------------
    def test_rmdir_symlink_to_an_empty_directory_is_enotdir(self):
        result = self.op(RMDIR, "dirlink")
        self.assertTrue(os.path.isdir(self.at("EMPTY")),
                        "RMDIR of a symlink removed the directory it points at")
        self.assertEqual(result, -errno.ENOTDIR)
        self.assertLink("dirlink")

    def test_rmdir_symlink_to_a_full_directory_is_enotdir(self):
        self.assertEqual(self.op(RMDIR, "fulllink"), -errno.ENOTDIR)
        self.assertLink("fulllink")
        self.assertEqual(self.read(self.at("FULL", "keep.bin")), b"keep")

    def test_rmdir_symlink_to_a_directory_outside_the_root_is_enotdir(self):
        self.assertEqual(self.op(RMDIR, "outdirlink"), -errno.ENOTDIR)
        self.assertLink("outdirlink")
        self.assertTrue(os.path.isdir(os.path.join(self.outside, "OUTDIR")))

    def test_rmdir_of_files_and_file_links_is_enotdir(self):
        self.assertEqual(self.op(RMDIR, "link.elf"), -errno.ENOTDIR)
        self.assertEqual(self.op(RMDIR, "GAMES/real.elf"), -errno.ENOTDIR)
        self.assertEqual(self.op(RMDIR, "dangling"), -errno.ENOTDIR)
        self.assertLink("link.elf")
        self.assertLink("dangling")
        self.assertEqual(self.read(self.at("GAMES", "real.elf")), ELF_BYTES)

    def test_rmdir_real_directories(self):
        self.assertEqual(self.op(RMDIR, "FULL"), -errno.ENOTEMPTY)
        self.assertEqual(self.read(self.at("FULL", "keep.bin")), b"keep")
        self.assertEqual(self.op(RMDIR, "EMPTY"), 0)
        self.assertFalse(os.path.exists(self.at("EMPTY")))
        self.assertEqual(self.op(RMDIR, "EMPTY"), -errno.ENOENT)

    def test_rmdir_of_the_share_root_is_refused(self):
        # An empty share is the case that matters: rmdir() of it succeeds.
        empty_root = tempfile.mkdtemp(prefix="udpfs_empty_root_")
        self.addCleanup(shutil.rmtree, empty_root, ignore_errors=True)
        server = self.make_server(empty_root)
        for path in ("", "/", ".", "./"):
            with self.subTest(path=path):
                result = self.op(RMDIR, path, server)
                self.assertTrue(os.path.isdir(empty_root), "RMDIR removed the share root")
                self.assertEqual(result, -errno.EACCES)

    # -- MKDIR ------------------------------------------------------------------
    def test_mkdir_over_a_dangling_symlink_is_eexist(self):
        result = self.op(MKDIR, "dangling")
        self.assertFalse(os.path.lexists(self.at("GAMES", "ghost")),
                         "MKDIR created a directory at a dangling link's target")
        self.assertEqual(result, -errno.EEXIST)
        self.assertLink("dangling")

    def test_mkdir_over_a_dangling_symlink_out_of_the_root_is_eexist(self):
        self.assertEqual(self.op(MKDIR, "dangling_out"), -errno.EEXIST)
        self.assertLink("dangling_out")
        self.assertFalse(os.path.lexists(os.path.join(self.outside, "ghostdir")))

    def test_mkdir_creates_and_reports_existing(self):
        self.assertEqual(self.op(MKDIR, "NEW/"), 0)
        self.assertTrue(os.path.isdir(self.at("NEW")))
        self.assertEqual(self.op(MKDIR, "NEW"), -errno.EEXIST)
        self.assertEqual(self.op(MKDIR, "GAMES"), -errno.EEXIST)
        self.assertEqual(self.op(MKDIR, "dirlink"), -errno.EEXIST)
        # A walker creating every component from the top must still be told
        # the root exists, as it always was.
        self.assertEqual(self.op(MKDIR, ""), -errno.EEXIST)
        self.assertEqual(self.op(MKDIR, "NOPARENT/child"), -errno.ENOENT)

    # -- containment --------------------------------------------------------------
    def test_paths_that_leave_the_root_are_refused(self):
        for opcode in (MKDIR, REMOVE, RMDIR):
            for path in ("../x", "GAMES/../../x", "..", "GAMES/..",
                         "outdirlink/x", "outdirlink/sub/x"):
                with self.subTest(opcode=hex(opcode), path=path):
                    self.assertEqual(self.op(opcode, path), -errno.EACCES)
        self.assertEqual(self.op(REMOVE, "outdirlink/../secret.bin"), -errno.EACCES)
        self.assertEqual(self.read(os.path.join(self.outside, "secret.bin")), b"secret")
        self.assertEqual(os.listdir(os.path.join(self.outside, "OUTDIR")), [])
        self.assertTrue(os.path.isdir(self.root))

    def test_mkdir_through_a_linked_folder_stays_inside_the_root(self):
        # Creating through a link only has to stay inside the root.
        self.assertEqual(self.op(MKDIR, "fulllink/SUB"), 0)
        self.assertTrue(os.path.isdir(self.at("FULL", "SUB")))

    # -- deleting through a linked folder -----------------------------------------
    def test_deletes_do_not_go_through_a_linked_folder(self):
        # Deleting through fulllink -> FULL deletes what is in FULL, a folder
        # the console never named. Edge lists any link to a folder as a
        # folder, and a console deleting a folder recursively goes into it.
        os.makedirs(self.at("FULL", "SUB"))
        for opcode, path in ((REMOVE, "fulllink/keep.bin"), (RMDIR, "fulllink/SUB"),
                             (REMOVE, "fulllink\\keep.bin"), (REMOVE, "./fulllink/keep.bin")):
            with self.subTest(opcode=hex(opcode), path=path):
                self.assertEqual(self.op(opcode, path), -errno.EACCES)
        self.assertEqual(self.read(self.at("FULL", "keep.bin")), b"keep")
        self.assertTrue(os.path.isdir(self.at("FULL", "SUB")))
        # By its own path the folder is the console's to delete, and so is
        # the link itself, which REMOVE deletes without touching FULL.
        self.assertEqual(self.op(RMDIR, "FULL/SUB"), 0)
        self.assertEqual(self.op(REMOVE, "fulllink"), 0)
        self.assertGone("fulllink")
        self.assertEqual(self.read(self.at("FULL", "keep.bin")), b"keep")

    def test_deletes_do_not_go_through_a_linked_folder_further_down(self):
        # The walk checks every folder on the way, not only the first.
        os.makedirs(self.at("FULL", "SUB"))
        os.makedirs(self.at("DEEP"))
        self.link(os.path.join("..", "FULL"), os.path.join("DEEP", "link"), is_dir=True)
        for opcode, path in ((REMOVE, "DEEP/link/keep.bin"), (RMDIR, "DEEP/link/SUB")):
            with self.subTest(opcode=hex(opcode), path=path):
                self.assertEqual(self.op(opcode, path), -errno.EACCES)
        self.assertEqual(self.read(self.at("FULL", "keep.bin")), b"keep")
        self.assertTrue(os.path.isdir(self.at("FULL", "SUB")))

    def test_a_dotdot_does_not_get_a_delete_past_the_linked_folder_check(self):
        # realpath() steps over a folder that does not exist, or a file, when
        # a '..' follows it, and goes on through the link. The walk stopped at
        # the first component it could not lstat() and called the path clean,
        # so these deleted FULL/keep.bin and FULL/SUB through fulllink.
        for opcode, path in ((REMOVE, "nonexistent/../fulllink/keep.bin"),
                             (RMDIR, "nonexistent/../fulllink/SUB"),
                             (REMOVE, "GAMES/real.elf/../../fulllink/keep.bin"),
                             (RMDIR, "GAMES/real.elf/../../fulllink/SUB")):
            with self.subTest(opcode=hex(opcode), path=path):
                self.write(self.at("FULL", "keep.bin"), b"keep")
                os.makedirs(self.at("FULL", "SUB"), exist_ok=True)
                self.assertLess(self.op(opcode, path), 0)
                self.assertTrue(os.path.isfile(self.at("FULL", "keep.bin")))
                self.assertTrue(os.path.isdir(self.at("FULL", "SUB")))

    def test_deletes_do_not_go_through_a_junction(self):
        # lstat() reports a Windows junction as a directory, so DREAD lists
        # one as a folder; only st_reparse_tag gives it away. Junctions exist
        # only on Windows, so a real folder stands in for one here.
        os.makedirs(self.at("JUNC", "SUB"))
        self.write(self.at("JUNC", "keep.bin"), b"keep")
        junction = os.path.join(self.server.root_dir, "JUNC")
        real_lstat = os.lstat

        def lstat(path, *args, **kwargs):
            st = real_lstat(path, *args, **kwargs)
            if os.fspath(path) != junction:
                return st
            return types.SimpleNamespace(
                st_mode=st.st_mode, st_reparse_tag=IO_REPARSE_TAG_MOUNT_POINT)

        with mock.patch.object(srv.os, "lstat", lstat):
            removed = self.op(REMOVE, "JUNC/keep.bin")
            rmdir = self.op(RMDIR, "JUNC/SUB")
        self.assertEqual(removed, -errno.EACCES)
        self.assertEqual(rmdir, -errno.EACCES)
        self.assertEqual(self.read(self.at("JUNC", "keep.bin")), b"keep")
        self.assertTrue(os.path.isdir(self.at("JUNC", "SUB")))

    def test_a_file_used_as_a_folder_is_not_a_link(self):
        # Only links are refused; anything else is the operation's to answer.
        self.assertEqual(self.op(REMOVE, "FULL/keep.bin/x"), -errno.ENOTDIR)
        self.assertEqual(self.op(RMDIR, "FULL/keep.bin/x"), -errno.ENOTDIR)
        self.assertEqual(self.op(REMOVE, "FULL/NOPE/x"), -errno.ENOENT)
        self.assertEqual(self.read(self.at("FULL", "keep.bin")), b"keep")

    def test_open_for_create_through_a_link_out_of_the_root_stays_refused(self):
        flags = O_WRONLY | O_CREAT | O_TRUNC
        self.assertEqual(self.open_reply("outlink", flags), -errno.EACCES)
        self.assertEqual(self.read(os.path.join(self.outside, "secret.bin")), b"secret")
        self.assertEqual(self.open_reply("dangling_out", flags), -errno.EACCES)
        self.assertFalse(os.path.lexists(os.path.join(self.outside, "ghostdir")))
        self.assertEqual(self.open_reply("outdirlink/new.bin", flags), -errno.EACCES)
        self.assertEqual(os.listdir(os.path.join(self.outside, "OUTDIR")), [])

    # -- read-only ----------------------------------------------------------------
    def test_read_only_refuses_everything_and_changes_nothing(self):
        server = self.make_server(self.root, read_only=True)
        for opcode, path in ((MKDIR, "NEW"), (MKDIR, "dangling"),
                             (REMOVE, "link.elf"), (REMOVE, "outlink"),
                             (REMOVE, "GAMES/real.elf"), (RMDIR, "EMPTY"),
                             (RMDIR, "dirlink"), (RMDIR, "")):
            with self.subTest(opcode=hex(opcode), path=path):
                self.assertEqual(self.op(opcode, path, server), -errno.EACCES)
        self.assertFalse(os.path.exists(self.at("NEW")))
        self.assertLink("link.elf")
        self.assertLink("outlink")
        self.assertLink("dirlink")
        self.assertTrue(os.path.isdir(self.at("EMPTY")))
        self.assertEqual(self.read(self.at("GAMES", "real.elf")), ELF_BYTES)

    # -- DREAD --------------------------------------------------------------------
    def test_dread_still_lists_links_from_lstat(self):
        # Following links here would show dirlink as a folder, and a delete
        # from the PS2 would then recurse into the folder it points at.
        self.server._handle_open(ADDR, _open_request("", 0x0001, is_dir=True))
        handle = self.server.replies[-1]
        self.assertGreater(handle, 0)
        listed = {}
        while True:
            self.server._handle_dread(ADDR, struct.pack("<BBBBi", 0x1C, 0, 0, 0, handle))
            result, name, stat_info = self.server.replies[-1]
            if result <= 0:
                break
            listed[name] = stat_info
        self.assertEqual(listed["link.elf"]["mode"], 0)
        self.assertEqual(listed["link.elf"]["size"], len("GAMES/real.elf"))
        self.assertEqual(listed["dirlink"]["mode"], 0)
        self.assertEqual(listed["GAMES"]["mode"], srv.FIO_S_IFDIR)


class StandaloneServerPathOpsTests(_PathOpsMixin, unittest.TestCase):
    server_class = srv.UdpfsServer


class LauncherServerPathOpsTests(_PathOpsMixin, unittest.TestCase):
    server_class = ps2servers_core.AutoUdpfsServer


if __name__ == "__main__":
    unittest.main()
