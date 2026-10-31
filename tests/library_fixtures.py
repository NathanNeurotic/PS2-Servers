"""One owned fixture library for every #249 case, shared by the consumer tests.

``build(root)`` writes a small games root; ``stress(root, count)`` adds many
images for budget tests. Everything is synthetic: no retail image, account,
network or PS2 is involved. The PS2 images that should inspect cleanly are
real minimal ISO9660 volumes with a SYSTEM.CNF (see tests/test_disc_identity.py).

Layout (paths relative to the root):

    DVD/SLUS_201.74.Standard DVD.iso   inspects as SLUS_201.74
    CD/SCUS_971.24.Standard CD.iso     inspects as SCUS_971.24
    SLES_500.01.Root Game.iso          root-level image
    Games/RPG/Deep/Four/SLPM_650.01.Nested.iso   four folders deep
    Games/RPG/Deep/Four/Five/TooDeep.iso         five deep, past the limit
    Games/A/Same Name.iso, Games/B/Same Name.iso same basename, two paths
    Games/Serial/Copy One.iso, Copy Two.iso      same inspected serial
    Games/Ünïcode, comma.iso                      UTF-8, space and comma
    Games/SLUS_555.55.Wrong Name.iso             filename serial != boot serial
    Games/Compressed/Game.chd|.cso|.zso          compressed containers
    Games/Broken/Truncated.iso                   truncated, no volume descriptor
    POPS/SLUS_007.08.PS1 Game.VCD                PS1 for POPSTARTER
    EMBER/games/SCUS_944.26 Ember Game/Game.cue  PS1 for Ember
    EMBER/games/Group VMCs/                       no disc: not an Ember game
    Games/Locked/                                 unreadable when POSIX allows
    Games/Outside -> <outside dir>                link that must not be followed
"""

import os
from pathlib import Path

from tests.test_disc_identity import iso_fixture

INSPECTED = {
    "DVD/SLUS_201.74.Standard DVD.iso": "SLUS_201.74",
    "CD/SCUS_971.24.Standard CD.iso": "SCUS_971.24",
    "SLES_500.01.Root Game.iso": "SLES_500.01",
    "Games/RPG/Deep/Four/SLPM_650.01.Nested.iso": "SLPM_650.01",
    "Games/A/Same Name.iso": "SLUS_211.11",
    "Games/B/Same Name.iso": "SLUS_222.22",
    "Games/Serial/Copy One.iso": "SLUS_233.33",
    "Games/Serial/Copy Two.iso": "SLUS_233.33",
    "Games/Ünïcode, comma.iso": "SLES_544.44",
    "Games/SLUS_555.55.Wrong Name.iso": "SLUS_566.66",
}
# The filename claims SLUS_555.55 but SYSTEM.CNF boots SLUS_566.66.
MISNAMED = "Games/SLUS_555.55.Wrong Name.iso"
TOO_DEEP = "Games/RPG/Deep/Four/Five/TooDeep.iso"
COMPRESSED = ("Games/Compressed/Game.chd", "Games/Compressed/Game.cso", "Games/Compressed/Game.zso")
TRUNCATED = "Games/Broken/Truncated.iso"
VCD = "POPS/SLUS_007.08.PS1 Game.VCD"
EMBER_GAME = "EMBER/games/SCUS_944.26 Ember Game"
EMBER_NOT_A_GAME = "EMBER/games/Group VMCs"
LOCKED = "Games/Locked"
LINK = "Games/Outside"


def _write(root, relative, data):
    path = Path(root, *relative.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(data))
    return path


def build(root, outside):
    """Write the fixture library into root; outside is a sibling the link points at."""
    root, outside = Path(root), Path(outside)
    for relative, serial in INSPECTED.items():
        _write(root, relative, iso_fixture(serial))
    _write(root, TOO_DEEP, iso_fixture("SLUS_299.99"))
    # Container magic only: indexable, but inspection must not invent a serial.
    _write(root, COMPRESSED[0], b"MComprHD" + b"\0" * 4088)
    _write(root, COMPRESSED[1], b"CISO" + b"\0" * 4092)
    _write(root, COMPRESSED[2], b"ZISO" + b"\0" * 4092)
    _write(root, TRUNCATED, b"\0" * 4096)
    _write(root, VCD, b"\0" * 4096)
    _write(root, EMBER_GAME + "/Game.cue", b'FILE "Game.bin" BINARY\n  TRACK 01 MODE2/2352\n')
    _write(root, EMBER_GAME + "/Game.bin", b"\0" * 4096)
    _write(root, EMBER_NOT_A_GAME + "/SLUS_007.08.mcd", b"\0" * 1024)
    _write(outside, "Escaped.iso", iso_fixture("SLUS_666.66"))
    link_made = False
    try:
        os.symlink(outside, root / Path(*LINK.split("/")), target_is_directory=True)
        link_made = True
    except (OSError, NotImplementedError):
        pass  # Windows without the symlink privilege
    locked = root / Path(*LOCKED.split("/"))
    _write(root, LOCKED + "/Hidden.iso", iso_fixture("SLUS_777.77"))
    locked_made = False
    if os.name == "posix" and os.geteuid() != 0:
        os.chmod(locked, 0)
        locked_made = True
    return {"link": link_made, "locked": locked_made}


def unlock(root):
    locked = Path(root, *LOCKED.split("/"))
    if locked.exists():
        os.chmod(locked, 0o755)


def stress(root, count):
    """count tiny images under Stress/; sizes only, they are never inspected."""
    for i in range(count):
        _write(root, "Stress/{:04d}/SLUS_{:03d}.{:02d}.Stress.iso".format(i // 100, i // 100, i % 100),
               b"\0" * 2048)
