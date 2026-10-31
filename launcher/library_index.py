"""Read-only, rooted metadata descriptors shared by Desktop library consumers.

This module never creates/migrates a database or reads game payloads. Source IDs
identify canonical file paths, not game content, disc serials or RA game IDs.
"""
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import sqlite3
import stat
import unicodedata

from . import disc_identity
from .library_catalog import Catalog, IMAGE_EXTENSIONS, INDEX_MEDIA, _scan_path, require_directory, visible_title


MAX_INDEX_ENTRIES = 10_000


@dataclass(frozen=True)
class IndexedGame:
    source_id: str
    image_path: str
    root: str
    relative_path: str
    size: int
    mtime_ns: int
    stamp: str
    image_format: str
    media: str
    title: str
    display_title_override: str
    manual_id: str
    boot_serial: str
    identity_status: str
    identity_detail: str
    artwork_identity: str
    layout: str
    provenance: str = "Desktop catalogue; current filesystem observation"


@dataclass(frozen=True)
class IndexConflict:
    kind: str
    key: str
    paths: tuple


@dataclass
class IndexSnapshot:
    root: str = ""
    games: tuple = ()
    conflicts: tuple = ()
    examined: int = 0
    skipped: int = 0
    cancelled: bool = False
    limit_reached: bool = False
    details: list = field(default_factory=list)

    def __str__(self):
        state = "Index review cancelled" if self.cancelled else (
            "Index review limit reached" if self.limit_reached else "Index review complete")
        return (f"{state}: {len(self.games)} available indexed images, {self.skipped} skipped; "
                f"{len(self.conflicts)} duplicate/collision groups.")

    def report(self):
        lines = [str(self), "Root: " + (self.root or "(cancelled before opening root)"), "",
                 "Source IDs identify paths; moving a file changes its ID.",
                 "Layout hints do not establish console launch support."]
        for conflict in self.conflicts[:20]:
            lines.extend(("", f"{conflict.kind}: {conflict.key}"))
            lines.extend("  " + path for path in conflict.paths[:10])
            if len(conflict.paths) > 10:
                lines.append(f"  ... {len(conflict.paths) - 10} more paths")
        if len(self.conflicts) > 20:
            lines.append(f"\n... {len(self.conflicts) - 20} more groups (available through the API)")
        if self.details:
            lines.extend(("", "Skipped entries (first 20):", *self.details))
        lines.extend(("", "Available indexed paths (first 200):"))
        for game in self.games[:200]:
            lines.append(f"{game.relative_path} | {game.image_format} | {game.media} | "
                         f"{game.boot_serial or '(unknown ID)'} | {game.identity_status} | {game.layout}")
        return "\n".join(lines)


def source_id(path):
    canonical = os.path.normcase(str(path))
    return "path-v1:" + hashlib.sha256(canonical.encode("utf-8", errors="surrogatepass")).hexdigest()


def _fold(value):
    return unicodedata.normalize("NFC", value).casefold()


def _conflicts(games):
    groups = {kind: defaultdict(list) for kind in ("duplicate basename", "duplicate boot serial", "case/Unicode collision")}
    for game in games:
        groups["duplicate basename"][_fold(Path(game.image_path).name)].append(game.relative_path)
        groups["case/Unicode collision"][_fold(game.relative_path)].append(game.relative_path)
        if game.boot_serial:
            groups["duplicate boot serial"][game.boot_serial].append(game.relative_path)
    return tuple(IndexConflict(kind, key, tuple(sorted(paths)))
                 for kind, values in groups.items() for key, paths in sorted(values.items()) if len(paths) > 1)


def read_index(database, root, *, max_entries=MAX_INDEX_ENTRIES, cancel=None):
    """Snapshot indexed PS2 images under root without touching either database.

    Files are statted again: stale inspected IDs are withheld until inspection
    refreshes them. Missing files and rejected links are reported, never opened.
    The entry limit counts every catalogue row in scope, including skipped rows.
    """
    if type(max_entries) is not int or not 1 <= max_entries <= MAX_INDEX_ENTRIES:
        raise ValueError(f"Index limit must be 1 through {MAX_INDEX_ENTRIES}.")
    if cancel is not None and cancel.is_set():
        return IndexSnapshot(cancelled=True)
    base = require_directory(root)
    path = Path(database).expanduser().resolve(strict=True)
    prefix = str(base)
    if not prefix.endswith(os.sep):
        prefix += os.sep
    collation = "COLLATE NOCASE" if os.name == "nt" else ""
    result = IndexSnapshot(root=str(base))
    games = []
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            db.set_progress_handler(lambda: int(cancel is not None and cancel.is_set()), 1000)
            # Older catalogues have no identity cache. They remain readable;
            # opening this adapter must never create that table on their behalf.
            has_identity = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='disc_identity'").fetchone()
            fields = ("d.status AS inspected_status,d.detected_disc_id,d.boot_path,"
                      "d.detail AS identity_detail,d.checked AS identity_checked,d.fingerprint") if has_identity else (
                      "NULL AS inspected_status,NULL AS detected_disc_id,NULL AS boot_path,"
                      "NULL AS identity_detail,NULL AS identity_checked,NULL AS fingerprint")
            join = ("LEFT JOIN disc_identity d ON g.image_path=d.image_path AND d.inspector_version=?"
                    if has_identity else "")
            args = ((disc_identity.INSPECTOR_VERSION,) if has_identity else ()) + (len(prefix), prefix, max_entries + 1)
            rows = db.execute(
                f"""SELECT g.*, {fields} FROM games g {join}
                    WHERE substr(g.image_path,1,?)=? {collation} ORDER BY g.id LIMIT ?""", args)
            for record in rows:
                if cancel is not None and cancel.is_set():
                    result.cancelled = True
                    break
                if result.examined >= max_entries:
                    result.limit_reached = True
                    break
                result.examined += 1
                filename = Path(record["image_path"])
                try:
                    filename = _scan_path(base, filename)
                    if str(filename) != record["image_path"]:
                        raise ValueError("Noncanonical catalogue path; rescan this image before sharing it.")
                    info = filename.stat()
                    if not stat.S_ISREG(info.st_mode) or filename.suffix.lower() not in IMAGE_EXTENSIONS or info.st_size <= 0:
                        raise ValueError("Not an available supported PS2 image.")
                    row = Catalog._identity_record(record)
                    if row["fingerprint"] and row["fingerprint"] != disc_identity.fingerprint(info):
                        row.update(identity_status="stale", detected_disc_id="",
                                   identity_detail="Image changed since inspection; inspect disc IDs again.")
                    relative = filename.relative_to(base)
                    layout = ("CD/DVD layout" if len(relative.parts) == 2 and relative.parts[0] in ("CD", "DVD") else
                              "root image" if len(relative.parts) == 1 else "nested path")
                    boot_serial = row["detected_disc_id"] if row["identity_status"] in ("verified", "conflict") else ""
                    games.append(IndexedGame(
                        source_id(filename), str(filename), str(base), relative.as_posix(), info.st_size,
                        info.st_mtime_ns, f"{info.st_size}:{info.st_mtime_ns}:{info.st_ctime_ns}",
                        filename.suffix[1:].upper(), row["media"] if row["media"] in INDEX_MEDIA else "UNKNOWN",
                        row["title"], row["title"] if row["title"] != visible_title(filename) else "",
                        row["disc_id"], boot_serial, row["identity_status"], row["identity_detail"],
                        boot_serial if row["identity_status"] == "verified" else "", layout))
                except (OSError, ValueError) as exc:
                    result.skipped += 1
                    if len(result.details) < 20:
                        result.details.append(f"{record['image_path']}: {exc}")
    except sqlite3.Error as exc:
        if cancel is not None and cancel.is_set():
            result.cancelled = True
        else:
            raise ValueError("Cannot read the Desktop library index; finish active catalogue operations, "
                             "then scan it in LIBRARY or restore its backup.") from exc
    result.games = tuple(sorted(games, key=lambda game: (_fold(game.title), game.relative_path)))
    result.conflicts = _conflicts(result.games)
    return result
