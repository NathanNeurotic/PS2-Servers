"""Stable release identity and transparency metadata for packaged builds."""

import sys

APP_NAME = "PS2 Servers"
EXECUTABLE_BASENAME = "PS2Servers"
WINDOWS_EXE_NAME = EXECUTABLE_BASENAME + ".exe"
WINDOWS_PACKAGE_NAME = EXECUTABLE_BASENAME + "-windows-x64.zip"
COMPANY_NAME = "NathanNeurotic"
PRODUCT_NAME = APP_NAME
# Numeric only -- both feed Nuitka's --product-version/--file-version, which
# reject anything that is not a dotted number. A pre-release qualifier like
# "-rc1" lives in VERSION_QUALIFIER instead and is shown to the user via
# DISPLAY_VERSION, never handed to the build.
PRODUCT_VERSION = "0.7.0"
FILE_VERSION = PRODUCT_VERSION
# "" for a normal release; "-rc1" / "-beta1" while a version is under test.
VERSION_QUALIFIER = ""
# What the GUI shows and what a tester should quote in a report.
DISPLAY_VERSION = PRODUCT_VERSION + VERSION_QUALIFIER
FILE_DESCRIPTION = "PS2 Servers launcher for Open PS2 Loader local network servers"
COPYRIGHT = "Copyright (c) NathanNeurotic and PS2 Servers contributors"


def _baked_commit():
    """The hash build/build.py compiled in as launcher/_build_id.py, or ""."""
    try:
        from . import _build_id
        return str(getattr(_build_id, "COMMIT", "") or "").strip()
    except ImportError:
        return ""


def _git_commit():
    """The checkout's live HEAD, or "" outside a git worktree."""
    try:
        import os
        import subprocess
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=root, stderr=subprocess.DEVNULL)
        return out.decode("ascii", "replace").strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def build_commit():
    """Short hash of the commit this build was made from, or "" when unknown.

    A packaged build answers from the bake alone: it has no checkout to ask,
    and asking git from whatever directory the exe happens to sit in could
    answer for an unrelated surrounding repository. A source checkout asks git
    first -- build/build.py leaves _build_id.py in the tree, and a hash baked
    by an earlier build must not override the checkout's real, moved HEAD.
    Anything else, like a zipped source download, shows the version without
    one.
    """
    baked = _baked_commit()
    # Same packaged-build test as servers.is_frozen, inlined so this module
    # stays a leaf the build tooling can import on its own.
    if bool(getattr(sys, "frozen", False)) or ("__compiled__" in globals()):
        return baked
    return _git_commit() or baked


def version_label():
    """What the About tab shows: 'v0.5.3', plus the commit when known."""
    label = "v" + DISPLAY_VERSION
    commit = build_commit()
    return label + " (" + commit + ")" if commit else label

AVAST_GEN_PUBLIC_NOTE = """PS2 Servers has been submitted to Avast/Gen Threat Labs for false-positive review.

The application is open source and built from the public GitHub repository. It is a PS2 homebrew utility for user-controlled local server setup and does not contain malware, credential collection, persistence, adware, browser modification, or crypto-mining behavior.

Repository:
https://github.com/NathanNeurotic/PS2-Servers
"""
