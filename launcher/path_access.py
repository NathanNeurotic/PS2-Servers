"""Check selected files/folders without changing their contents or permissions."""

import os
import platform
import stat


def _failure(path, error):
    message = f"{path}\n{type(error).__name__}: {error}"
    if isinstance(error, PermissionError):
        if platform.system() == "Windows":
            message += ("\nCheck the file/folder's Security permissions for your "
                        "Windows account and access to its parent folders.")
        else:
            message += ("\nCheck access to the parent directories and the drive's "
                        "mount ownership/permissions. NTFS/exFAT mount options "
                        "may control access instead of chmod/chown.")
    elif isinstance(error, FileNotFoundError):
        message += "\nCheck that the drive is connected and mounted at this path."
    return message


def check_path(path, kind, read_only=False):
    """Return (usable for reads, report); writable-open results are advisory.

    Folder checks enumerate entries without recursively reading game files.
    File checks read one byte and, when requested, attempt a non-truncating
    writable open. Neither test guarantees that subsequent reads/writes succeed.
    Raw devices are deliberately not opened by this file/folder check.
    """
    if not path:
        return False, "Select a file or folder first."
    try:
        info = os.stat(path)
        if kind == "folder":
            if not stat.S_ISDIR(info.st_mode):
                return False, f"{path}\nThis target is not a folder."
            with os.scandir(path) as entries:
                next(entries, None)
            return True, (f"{path}\nFolder listing succeeded. Individual game-file "
                          "reads and save/VMC writes have not been tested.")
        if not stat.S_ISREG(info.st_mode):
            return False, (f"{path}\nThis is not a regular image file. Raw disks "
                           "and partitions need separate device access and "
                           "capacity validation; this check does not open them.")
        with open(path, "rb", buffering=0) as target:
            target.read(1)
    except (OSError, ValueError) as error:
        return False, _failure(path, error)

    report = f"{path}\nFile read succeeded ({info.st_size} bytes)."
    if read_only:
        return True, report + " Read-only selected; writable access was not checked."
    try:
        with open(path, "r+b", buffering=0):
            pass
    except (OSError, ValueError) as error:
        return True, (report + "\nWritable open failed; saves/VMC writes require "
                      "writable access.\n" + _failure(path, error))
    return True, (report + "\nWritable open succeeded. No data was written; "
                  "actual saves/VMC writes have not been tested.")
