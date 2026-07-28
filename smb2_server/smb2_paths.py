"""Rooted path resolution for the SMB2/SMB3 server.

Kept in its own module because it is the one piece where a mistake hands a
client a file the operator never meant to share, and it should be readable and
testable without starting a server.

This deliberately does NOT copy smbv1_server's resolver. That one resolves with
os.path.abspath, which normalises ".." textually but does not follow symbolic
links, so a symlink inside the share resolves to a path that still begins with
the share root while opening a file outside it. Verified against the real code:

    share/elsewhere -> /tmp/outside
    resolve("elsewhere/secret.txt")
      -> .../share/elsewhere/secret.txt   (accepted: starts with the root)
      real path .../outside/secret.txt    (outside the share)

That is not remotely exploitable on its own -- the SMBv1 server implements no
command that creates a symlink, so a client cannot plant one -- but it means the
declared root is not the actual boundary, and both the Edge server and the UDPFS
path guard already refuse symlink escapes. The newer servers should not inherit
the weaker rule.

Resolution here is by real path: symlinks are followed first, then the result is
required to lie under the real root. A symlink pointing outside is refused even
though the operator created it, which is the same answer the rest of the project
gives.
"""

import os


class PathError(Exception):
    """Raised when a client path cannot be served. Carries an NT status name.

    The status travels with the error so a handler does not have to guess which
    of ACCESS_DENIED / OBJECT_NAME_NOT_FOUND / OBJECT_PATH_SYNTAX_BAD to send
    back, and so the reason is recorded once, where it was decided.
    """

    def __init__(self, status, detail):
        super().__init__(detail)
        self.status = status
        self.detail = detail


# Characters Windows forbids in a filename. A client that sends one is either
# confused or probing; either way there is nothing on disk it can legitimately
# name, so refusing is both correct and cheap.
_ILLEGAL = set('<>:"|?*') | {chr(c) for c in range(32)}

# NTFS alternate data streams. "file.txt:hidden" names a stream, not a file, and
# on Windows opening it succeeds -- so leaving this to the filesystem would let a
# client read or write bytes that never appear in a directory listing.
_STREAM_SEPARATOR = ":"

MAX_COMPONENT = 255
MAX_PATH_CHARS = 4096


class Share:
    """One shared directory, and the only way a client path becomes a real one."""

    def __init__(self, name, root, read_only=False):
        self.name = name
        # realpath, not abspath: the root itself may be reached through a
        # symlink (/home/user/games -> /mnt/disk2/games is ordinary), and if the
        # root is not already resolved then every containment test below is
        # comparing against the wrong string.
        self.root = os.path.realpath(root)
        self.read_only = read_only

    def __repr__(self):
        return "Share(%r, %r, read_only=%r)" % (self.name, self.root, self.read_only)

    def resolve(self, client_path, must_exist=False):
        """Map a client path to a real local path inside this share.

        Raises PathError rather than returning None, so a caller cannot forget
        to check and end up passing None to open().
        """
        if client_path is None:
            client_path = ""
        if len(client_path) > MAX_PATH_CHARS:
            raise PathError("STATUS_OBJECT_NAME_INVALID", "path too long")

        # SMB uses backslashes; accept forward slashes too because clients and
        # test harnesses are inconsistent about it.
        normalised = client_path.replace("\\", "/")

        # A UNC path names a different server, so it is refused rather than
        # served. Without this it is not rejected but quietly *reinterpreted*:
        # the empty leading components fall out of the loop below and
        # \\server\share\x becomes the share-relative server\share\x. That stays
        # inside the root, so nothing leaks -- but answering a question the
        # client did not ask is how a client ends up trusting the wrong file.
        if normalised.startswith("//"):
            raise PathError("STATUS_OBJECT_PATH_SYNTAX_BAD",
                            "UNC paths name another server and are not served")

        components = []
        for part in normalised.split("/"):
            if part in ("", "."):
                continue
            if part == "..":
                # Refused outright rather than popped. Popping would silently
                # accept "a/../../b" as "b" whenever the arithmetic happened to
                # land back inside the root, which makes the guard depend on
                # counting rather than on a rule.
                raise PathError("STATUS_OBJECT_PATH_SYNTAX_BAD",
                                "parent traversal is not served")
            if len(part) > MAX_COMPONENT:
                raise PathError("STATUS_OBJECT_NAME_INVALID", "name component too long")
            if _STREAM_SEPARATOR in part:
                raise PathError("STATUS_OBJECT_NAME_INVALID",
                                "alternate data streams are not served")
            if any(ch in _ILLEGAL for ch in part):
                raise PathError("STATUS_OBJECT_NAME_INVALID",
                                "illegal character in name")
            components.append(part)

        candidate = os.path.join(self.root, *components) if components else self.root

        # The containment test is done on the REAL path. For a path that does
        # not exist yet -- a file about to be created -- realpath still resolves
        # the directories above it, which is the part that could be a symlink
        # out of the share.
        real = os.path.realpath(candidate)
        if not self._inside(real):
            raise PathError("STATUS_ACCESS_DENIED",
                            "path resolves outside the share")

        if must_exist and not os.path.exists(real):
            raise PathError("STATUS_OBJECT_NAME_NOT_FOUND", "no such file or directory")
        return real

    def _inside(self, real):
        """True if a resolved path is the share root or beneath it.

        os.path.commonpath rather than a startswith on root + sep, because
        startswith accepts a sibling whose name merely begins with the root's:
        with a root of /srv/games, the string test lets /srv/games-private
        through. commonpath compares whole components.
        """
        if real == self.root:
            return True
        try:
            return os.path.commonpath([real, self.root]) == self.root
        except ValueError:
            # Raised when the paths are on different drives on Windows, which
            # is conclusively outside the share.
            return False

    def relative(self, real):
        """The share-relative path for a resolved local path, in SMB form."""
        rel = os.path.relpath(real, self.root)
        if rel == os.curdir:
            return ""
        return rel.replace(os.sep, "\\")
