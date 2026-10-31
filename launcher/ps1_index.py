"""Read-only PS1 descriptors for a games root: POPSTARTER VCDs and Ember folders.

PS1 games are kept apart from the PS2 catalogue on purpose. A PS1 serial is not a
PS2 boot serial, an Ember folder name is not a VCD filename, and neither is ever
put in a PS2 identity field. Nothing here opens a game payload, writes a file or
touches the Desktop catalogue database.

Layouts follow RiptOPL (``rebuild/main`` at 4ec6c68):

* POPSTARTER: ``<root>/POPS/<name>.VCD`` at the top level of ``POPS``.
* Ember (``include/cuesupport.h``, ``src/sources/cuesupport.cpp``):
  ``<root>/EMBER/games/<name>/`` holding a ``.cue``, ``.exe`` or ``.bin`` at its
  own top level (Ember's ``io_find_disc`` does not recurse). The name must not
  start with ``..``, must not contain ``/``, ``:`` or ``\\``, and must be at most
  180 bytes (``CUE_NAME_LAUNCH_MAX``), or Ember refuses the launch argument.
"""

from dataclasses import dataclass
import os
from pathlib import Path
import re
import stat

POPS_FOLDER = "POPS"
EMBER_FOLDER = "EMBER"
EMBER_GAMES = "games"
EMBER_DISC_EXTENSIONS = (".cue", ".exe", ".bin")  # Ember's own priority order
EMBER_NAME_LAUNCH_MAX = 180
MAX_ENTRIES = 10_000
_PS1_NAME_ID = re.compile(r"^([A-Z]{4})[_-](\d{3})\.?(\d{2})", re.I)


@dataclass(frozen=True)
class Ps1Game:
    core: str             # "popstarter" or "ember"
    name: str             # the VCD filename or the Ember folder name, as listed
    relative_path: str    # POSIX path from the root
    disc_image: str       # the file the core would use ("" for a VCD: the VCD itself)
    filename_id: str      # PS1 serial-looking prefix of the name, provisional; never a PS2 serial
    launchable: bool
    problem: str = ""

    # PS2-only fields are present and always empty, so a consumer that merges
    # descriptors cannot mistake a PS1 row for a verified PS2 disc.
    ps2_boot_serial: str = ""
    identity_status: str = "not applicable (PS1)"


def ember_name_problem(name):
    """Why Ember would refuse this folder name as its launch argument, or ''."""
    if not name or name == ".":
        return "empty name"
    if name.startswith(".."):
        return "starts with '..'"
    if any(c in name for c in "/:\\"):
        return "contains '/', ':' or '\\'"
    if len(os.fsencode(name)) > EMBER_NAME_LAUNCH_MAX:
        return "longer than {} bytes".format(EMBER_NAME_LAUNCH_MAX)
    return ""


def _filename_id(name):
    match = _PS1_NAME_ID.match(name)
    return "{}_{}.{}".format(match.group(1).upper(), match.group(2), match.group(3)) if match else ""


def _plain_directory(path):
    try:
        info = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        return False  # symlink or Windows reparse point: not followed
    return stat.S_ISDIR(info.st_mode)


def _child(directory, wanted):
    """The real entry name matching ``wanted`` case-insensitively, or None."""
    try:
        for entry in os.listdir(directory):
            if entry.lower() == wanted.lower():
                return entry
    except OSError:
        return None
    return None


def scan(root, *, max_entries=MAX_ENTRIES):
    """Every PS1 game under root, PS1 fields only. Returns (games, problems)."""
    root = Path(root)
    games, problems = [], []
    pops = _child(root, POPS_FOLDER)
    if pops and _plain_directory(root / pops):
        try:
            names = sorted(os.listdir(root / pops))
        except OSError as exc:
            problems.append("cannot read {}: {}".format(pops, exc))
            names = []
        for name in names:
            if len(games) >= max_entries:
                problems.append("stopped at {} entries".format(max_entries))
                return games, problems
            path = root / pops / name
            if not name.lower().endswith(".vcd"):
                continue
            try:
                info = os.lstat(path)
            except OSError:
                continue
            if not stat.S_ISREG(info.st_mode):
                problems.append("skipped (not a regular file): {}/{}".format(pops, name))
                continue
            games.append(Ps1Game("popstarter", name, "{}/{}".format(pops, name), "",
                                 _filename_id(name), True))
    ember = _child(root, EMBER_FOLDER)
    games_dir_name = _child(root / ember, EMBER_GAMES) if ember and _plain_directory(root / ember) else None
    if games_dir_name and _plain_directory(root / ember / games_dir_name):
        base = root / ember / games_dir_name
        try:
            names = sorted(os.listdir(base))
        except OSError as exc:
            problems.append("cannot read {}/{}: {}".format(ember, games_dir_name, exc))
            names = []
        for name in names:
            if len(games) >= max_entries:
                problems.append("stopped at {} entries".format(max_entries))
                return games, problems
            folder = base / name
            if not _plain_directory(folder):
                continue
            try:
                inside = os.listdir(folder)
            except OSError as exc:
                problems.append("cannot read {}/{}/{}: {}".format(ember, games_dir_name, name, exc))
                continue
            disc = ""
            for ext in EMBER_DISC_EXTENSIONS:
                hits = sorted(f for f in inside if f.lower().endswith(ext))
                if hits:
                    disc = hits[0]
                    break
            if not disc:
                continue  # an empty folder or a group-VMC folder: RiptOPL does not list it
            problem = ember_name_problem(name)
            games.append(Ps1Game("ember", name, "{}/{}/{}".format(ember, games_dir_name, name), disc,
                                 _filename_id(name), not problem, problem))
    return games, problems
