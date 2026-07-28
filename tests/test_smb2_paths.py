"""The SMB2/SMB3 share boundary must be the real filesystem boundary.

This is the one piece of the new servers where a mistake hands a client a file
the operator never meant to share, so it is tested on its own, without a socket
or a protocol in the way.

The symlink cases are here because the existing SMBv1 resolver does not survive
them. That one uses os.path.abspath, which normalises ".." textually but never
follows a link, so a symlink inside the share produces a path that still starts
with the share root while opening a file outside it. Confirmed against the real
smbv1_server code before this module was written:

    resolve("elsewhere/secret.txt") -> .../share/elsewhere/secret.txt   accepted
    os.path.realpath of that        -> .../outside/secret.txt           outside

Not remotely exploitable there, since SMBv1 implements nothing that creates a
symlink, but it means the declared root is not the real boundary. Edge and the
UDPFS path guard both refuse symlink escapes; these tests hold the new servers
to that rule instead of the older one.

Run:  python -m unittest tests.test_smb2_paths -v
"""

import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

sys.path.insert(0, os.path.join(ROOT, "smb2_server"))
from smb2_paths import PathError, Share  # noqa: E402


def _can_symlink(directory):
    """Windows needs Developer Mode or admin to create symlinks."""
    probe = os.path.join(directory, "_symlink_probe")
    try:
        os.symlink(directory, probe, target_is_directory=True)
    except (OSError, NotImplementedError, AttributeError):
        return False
    os.remove(probe)
    return True


class ShareBoundary(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = os.path.join(self.tmp, "games")
        os.makedirs(os.path.join(self.root, "sub"))
        with open(os.path.join(self.root, "game.iso"), "wb") as handle:
            handle.write(b"iso")
        with open(os.path.join(self.root, "sub", "inner.bin"), "wb") as handle:
            handle.write(b"inner")

        self.outside = os.path.join(self.tmp, "outside")
        os.makedirs(self.outside)
        self.secret = os.path.join(self.outside, "secret.txt")
        with open(self.secret, "w", encoding="utf-8") as handle:
            handle.write("NOT SHAREABLE")

        self.share = Share("games", self.root)

    # ---- paths that must work -------------------------------------------------

    def test_ordinary_paths_resolve(self):
        for probe in ("game.iso", "\\game.iso", "/game.iso", "sub\\inner.bin", "sub/inner.bin"):
            with self.subTest(probe=probe):
                self.assertTrue(os.path.exists(self.share.resolve(probe)))

    def test_empty_path_is_the_share_root(self):
        for probe in ("", "\\", "/", "."):
            with self.subTest(probe=probe):
                self.assertEqual(self.share.resolve(probe), self.share.root)

    def test_a_path_that_does_not_exist_yet_resolves(self):
        """A file being created has no real path of its own; its parent must still be checked."""
        created = self.share.resolve("sub\\new-save.bin")
        self.assertTrue(created.startswith(self.share.root))
        with self.assertRaises(PathError):
            self.share.resolve("sub\\new-save.bin", must_exist=True)

    def test_relative_round_trips(self):
        real = self.share.resolve("sub\\inner.bin")
        self.assertEqual(self.share.relative(real), "sub\\inner.bin")

    # ---- paths that must not ---------------------------------------------------

    def test_parent_traversal_is_refused(self):
        for probe in ("..\\outside\\secret.txt", "../outside/secret.txt",
                      "sub\\..\\..\\outside\\secret.txt", "..", "sub/../..",
                      "\\..\\..\\outside\\secret.txt"):
            with self.subTest(probe=probe):
                with self.assertRaises(PathError):
                    self.share.resolve(probe)

    def test_traversal_that_lands_back_inside_is_still_refused(self):
        """"sub/../game.iso" is harmless arithmetic, and still not served.

        Accepting it would make the guard depend on where the counting happens
        to land rather than on a rule, and the difference between the two is
        invisible right up until a case is found where it lands elsewhere.
        """
        with self.assertRaises(PathError):
            self.share.resolve("sub\\..\\game.iso")

    def test_absolute_and_drive_qualified_paths_are_refused(self):
        for probe in ("C:\\Windows\\win.ini", "\\\\server\\share\\x", "D:/x"):
            with self.subTest(probe=probe):
                with self.assertRaises(PathError):
                    self.share.resolve(probe)

    def test_alternate_data_streams_are_refused(self):
        """"game.iso:hidden" names a stream Windows will happily open."""
        with self.assertRaises(PathError):
            self.share.resolve("game.iso:hidden")

    def test_illegal_characters_are_refused(self):
        for probe in ("bad<name", "bad>name", 'bad"name', "bad|name", "bad?name", "bad*name"):
            with self.subTest(probe=probe):
                with self.assertRaises(PathError):
                    self.share.resolve(probe)

    def test_a_sibling_directory_with_the_same_prefix_is_outside(self):
        """A root of .../games must not admit .../games-private.

        This is why containment is commonpath and not startswith(root + sep):
        the string test passes for any sibling whose name merely begins with the
        root's.
        """
        sibling = self.root + "-private"
        os.makedirs(sibling)
        with open(os.path.join(sibling, "other.txt"), "w", encoding="utf-8") as handle:
            handle.write("nope")
        self.assertFalse(self.share._inside(os.path.join(sibling, "other.txt")))


class SymlinkEscape(unittest.TestCase):
    """The case the older resolver lets through."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = os.path.join(self.tmp, "games")
        os.makedirs(self.root)
        self.outside = os.path.join(self.tmp, "outside")
        os.makedirs(self.outside)
        with open(os.path.join(self.outside, "secret.txt"), "w", encoding="utf-8") as handle:
            handle.write("NOT SHAREABLE")
        if not _can_symlink(self.tmp):
            self.skipTest("this platform/account cannot create symlinks")
        self.share = Share("games", self.root)

    def test_symlinked_directory_out_of_the_share_is_refused(self):
        os.symlink(self.outside, os.path.join(self.root, "elsewhere"),
                   target_is_directory=True)
        with self.assertRaises(PathError) as caught:
            self.share.resolve("elsewhere/secret.txt")
        self.assertEqual(caught.exception.status, "STATUS_ACCESS_DENIED")

    def test_symlinked_file_out_of_the_share_is_refused(self):
        os.symlink(os.path.join(self.outside, "secret.txt"),
                   os.path.join(self.root, "leak.txt"))
        with self.assertRaises(PathError):
            self.share.resolve("leak.txt")

    def test_symlink_that_stays_inside_the_share_is_served(self):
        """Refusing every symlink would break a legitimate layout.

        Pointing one folder of games at another folder of games, inside the same
        share, is an ordinary thing to do and there is nothing to refuse: the
        target is already shared.
        """
        inner = os.path.join(self.root, "real")
        os.makedirs(inner)
        with open(os.path.join(inner, "a.iso"), "wb") as handle:
            handle.write(b"a")
        os.symlink(inner, os.path.join(self.root, "link"), target_is_directory=True)
        resolved = self.share.resolve("link/a.iso")
        self.assertTrue(os.path.exists(resolved))

    def test_a_share_root_reached_through_a_symlink_still_works(self):
        """/home/user/games -> /mnt/disk2/games is an ordinary NAS layout.

        If the root were stored unresolved, every containment test would compare
        a resolved path against an unresolved root and refuse everything.
        """
        elsewhere = os.path.join(self.tmp, "real-games")
        os.makedirs(elsewhere)
        with open(os.path.join(elsewhere, "g.iso"), "wb") as handle:
            handle.write(b"g")
        linked_root = os.path.join(self.tmp, "linked-games")
        os.symlink(elsewhere, linked_root, target_is_directory=True)
        share = Share("games", linked_root)
        self.assertTrue(os.path.exists(share.resolve("g.iso")))


if __name__ == "__main__":
    unittest.main()
