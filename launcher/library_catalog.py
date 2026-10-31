"""Optional local library catalogue for PS2-Servers Desktop.

The database is metadata only; opening it never alters game images or saves.
No game-serving hot path imports this module.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass, field, replace
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import tempfile
import time

from .config import config_dir
from . import disc_identity

IMAGE_EXTENSIONS = frozenset({".iso", ".zso", ".cso", ".chd"})
MEDIA = ("DVD", "CD")
INDEX_MEDIA = (*MEDIA, "UNKNOWN")
MAX_SCAN_DEPTH = 8
MAX_SCAN_ENTRIES = 100_000
ART_TYPES = frozenset({"COV", "ICO", "LAB", "COV3"})
KEY_PATTERN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_. ()\[\]-]{0,95}$")
SERIAL_PREFIX = re.compile(r"^[A-Z]{4}[_-]\d{3}\.\d{2}[. _-]+", re.I)


@dataclass
class ScanResult:
    """Bounded scan outcome; indexed rows survive cancellation for safe rescans."""
    indexed: int = 0
    visited: int = 0
    skipped_links: int = 0
    skipped_depth: int = 0
    skipped_unsupported: int = 0
    skipped_invalid: int = 0
    errors: int = 0
    cancelled: bool = False
    limit_reached: bool = False
    details: list = field(default_factory=list)

    def __str__(self):
        state = "Cancelled" if self.cancelled else (
            "Entry limit reached" if self.limit_reached else "Scan complete")
        text = (f"{state}: {self.indexed} indexed, {self.visited} inspected; "
                f"{self.skipped_links} links, {self.skipped_depth} depth-limited folders, "
                f"{self.skipped_unsupported} unsupported, {self.skipped_invalid} invalid, "
                f"{self.errors} errors.")
        if self.details:
            text += " First issue: " + self.details[0]
        return text


@dataclass
class IdentityScanResult:
    visited: int = 0
    inspected: int = 0
    cached: int = 0
    verified: int = 0
    conflicts: int = 0
    unknown: int = 0
    invalid: int = 0
    unsupported: int = 0
    errors: int = 0
    cancelled: bool = False
    limit_reached: bool = False
    details: list = field(default_factory=list)

    def __str__(self):
        state = "Inspection cancelled" if self.cancelled else (
            "Inspection limit reached" if self.limit_reached else "Inspection complete")
        text = (f"{state}: {self.visited} entries, {self.inspected} inspected, {self.cached} cached; "
                f"{self.verified} verified, {self.conflicts} conflicts, {self.unknown} unknown, "
                f"{self.invalid} invalid, {self.unsupported} unsupported, {self.errors} errors.")
        if self.details:
            text += " First issue: " + self.details[0]
        return text


def _linked(info):
    """Reject Unix links and Windows junctions/other reparse points."""
    return (stat.S_ISLNK(info.st_mode) or
            bool(getattr(info, "st_file_attributes", 0) &
                 getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)))


def _scan_path(base, path):
    """Recheck containment and every descendant component before indexing."""
    relative = path.relative_to(base)
    current = base
    for part in relative.parts:
        current = current / part
        if _linked(current.lstat()):
            raise ValueError("Symlink or reparse point skipped")
    resolved = path.resolve(strict=True)
    resolved.relative_to(base)
    return resolved


def default_database():
    return Path(config_dir()) / "library" / "catalog.sqlite3"


def require_directory(root):
    path = Path(root).expanduser().resolve(strict=True)
    if not path.is_dir():
        raise ValueError("Choose an existing games folder.")
    return path


def visible_title(path):
    return SERIAL_PREFIX.sub("", Path(path).stem).replace("_", " ").strip() or Path(path).stem


def _artwork_palette_image(Image, img):
    """8-bit palette image for RiptOPL artwork, keeping transparency.

    RiptOPL loads an 8-bit palette PNG as a T8 texture whose CLUT alpha comes
    from the tRNS chunk (src/gfx/textures.cpp), so a logo or disc with a
    transparent background keeps it. Opaque art takes the same path as before.
    """
    if img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info:
        rgba = img.convert("RGBA")
        if rgba.getchannel("A").getextrema()[0] < 255:
            # Median cut cannot quantize RGBA; fast octree keeps the alpha
            # in the palette and Pillow writes it as tRNS.
            return rgba.quantize(colors=256, method=Image.Quantize.FASTOCTREE)
    return img.convert("RGB").quantize(colors=256, method=Image.Quantize.MEDIANCUT)


def artwork_filename(identity, art_type="COV"):
    """Explicit identity; PS2 disc-ID, VCD basename and ELF filename are different keys."""
    if art_type not in ART_TYPES or not KEY_PATTERN.fullmatch(identity):
        raise ValueError("Invalid artwork name/type; use the game's exact RiptOPL identity.")
    if identity in (".", "..") or "/" in identity or "\\" in identity:
        raise ValueError("Invalid artwork identity.")
    return "{}_{}.png".format(identity, art_type)


class Catalog:
    def __init__(self, database=None):
        self.path = Path(database) if database is not None else default_database()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path), timeout=15)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA busy_timeout=15000")
        self.db.execute("""CREATE TABLE IF NOT EXISTS games (
            id INTEGER PRIMARY KEY,
            image_path TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            disc_id TEXT NOT NULL DEFAULT '',
            media TEXT NOT NULL,
            image_bytes INTEGER NOT NULL,
            notes TEXT NOT NULL DEFAULT '',
            updated INTEGER NOT NULL
        )""")
        # A separate, disposable cache preserves the existing games schema and
        # manually edited IDs. Older builds can still open the same catalogue.
        self.db.execute("""CREATE TABLE IF NOT EXISTS disc_identity (
            image_path TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
            inspector_version INTEGER NOT NULL, status TEXT NOT NULL,
            detected_disc_id TEXT NOT NULL, boot_path TEXT NOT NULL,
            detail TEXT NOT NULL, checked INTEGER NOT NULL
        )""")
        self.db.commit()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.db.close()

    def list_games(self, search=""):
        value = "%" + search.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        needle = search.lower()
        rows = [self._current_identity(r) for r in self.db.execute(
            """SELECT g.*, d.status AS inspected_status, d.detected_disc_id,
                      d.boot_path, d.detail AS identity_detail, d.checked AS identity_checked,
                      d.fingerprint AS identity_fingerprint
               FROM games g LEFT JOIN disc_identity d ON g.image_path=d.image_path
                    AND d.inspector_version=?
               WHERE lower(g.title) LIKE ? ESCAPE '\\' OR lower(g.disc_id) LIKE ? ESCAPE '\\'
                  OR lower(d.detected_disc_id) LIKE ? ESCAPE '\\'
               ORDER BY g.title COLLATE NOCASE, g.id LIMIT 10000""",
            (disc_identity.INSPECTOR_VERSION, value, value, value))]
        # A row found only by an inspected ID that is now stale no longer matches.
        return [r for r in rows if not needle or r["identity_status"] != "stale"
                or needle in (r["title"] or "").lower() or needle in (r["disc_id"] or "").lower()]

    def get_game(self, row_id):
        record = self.db.execute(
            """SELECT g.*, d.status AS inspected_status, d.detected_disc_id,
                      d.boot_path, d.detail AS identity_detail, d.checked AS identity_checked,
                      d.fingerprint AS identity_fingerprint
               FROM games g LEFT JOIN disc_identity d ON g.image_path=d.image_path
                    AND d.inspector_version=?
               WHERE g.id=?""", (disc_identity.INSPECTOR_VERSION, int(row_id))).fetchone()
        return self._current_identity(record) if record else None

    @classmethod
    def _current_identity(cls, record):
        """Withhold a cached BOOT2 serial once the image no longer matches it."""
        row = cls._identity_record(record)
        stamp = row.pop("identity_fingerprint", None)
        if stamp and row["identity_status"] in ("verified", "conflict"):
            try:
                current = disc_identity.fingerprint(Path(row["image_path"]).stat())
            except OSError:
                current = ""
            if current != stamp:
                row.update(identity_status="stale", detected_disc_id="", boot_path="",
                           identity_detail="Image changed since inspection; inspect disc IDs again.")
        return row

    @staticmethod
    def _identity_record(record):
        row = dict(record)
        row["identity_status"] = row.pop("inspected_status") or "not inspected"
        for key in ("detected_disc_id", "boot_path", "identity_detail"):
            row[key] = row[key] or ""
        if row["identity_status"] == "verified":
            conflicts = []
            manual = row["disc_id"].upper().replace("-", "_")
            if manual and manual != row["detected_disc_id"]:
                conflicts.append("Manual ID differs from the inspected BOOT2 serial.")
            named = disc_identity.filename_serial(row["image_path"])
            if named and named != row["detected_disc_id"]:
                conflicts.append(f"Filename ID {named} differs from the inspected BOOT2 serial.")
            if conflicts:
                row["identity_status"] = "conflict"
                row["identity_detail"] = " ".join(conflicts) + " " + row["identity_detail"]
        return row

    def upsert(self, filename, media="DVD"):
        if media not in INDEX_MEDIA:
            raise ValueError("Choose CD, DVD or UNKNOWN media.")
        source = Path(filename).resolve(strict=True)
        if source.is_symlink() or not source.is_file() or source.suffix.lower() not in IMAGE_EXTENSIONS:
            raise ValueError("Choose a supported PS2 ISO/CSO/ZSO/CHD file.")
        info = source.stat()
        size = info.st_size
        if size <= 0:
            raise ValueError("An empty image cannot be installed.")
        self.db.execute(
            """INSERT INTO games(image_path,title,media,image_bytes,updated)
               VALUES(?,?,?,?,?)
               ON CONFLICT(image_path) DO UPDATE SET
                   image_bytes=excluded.image_bytes,
                   media=CASE WHEN excluded.media='UNKNOWN' THEN games.media ELSE excluded.media END,
                   updated=excluded.updated""",
            (str(source), visible_title(source), media, size, int(time.time())))
        self.db.execute("DELETE FROM disc_identity WHERE image_path=? AND fingerprint<>?",
                        (str(source), disc_identity.fingerprint(info)))
        self.db.commit()
        return self.db.execute("SELECT id FROM games WHERE image_path=?", (str(source),)).fetchone()[0]

    def scan(self, root):
        """Keep the existing top-level CD/DVD scan API and integer result."""
        return self.scan_detailed(root).indexed

    def scan_detailed(self, root, *, recursive=False, max_depth=4,
                      max_entries=10_000, cancel=None, progress=None):
        """Index metadata with bounded traversal; never infer launch capability.

        Recursive mode includes root images and at most max_depth directory
        levels below the selected root. Only a top-level CD/DVD ancestor supplies
        media; arbitrary-root images remain UNKNOWN (no size/identity inference).
        Progress receives snapshots at most once per 128 entries, plus completion.
        The scanner does not open image payloads, follow links, or delete rows.
        """
        if type(max_depth) is not int or not 0 <= max_depth <= MAX_SCAN_DEPTH:
            raise ValueError(f"Scan depth must be 0 through {MAX_SCAN_DEPTH}.")
        if type(max_entries) is not int or not 1 <= max_entries <= MAX_SCAN_ENTRIES:
            raise ValueError(f"Entry limit must be 1 through {MAX_SCAN_ENTRIES}.")
        base = require_directory(root)
        result = ScanResult()
        stack = []

        def issue(path, exc):
            result.errors += 1
            if len(result.details) < 20:
                result.details.append(f"{path.relative_to(base)}: {exc}")

        def notify():
            if progress:
                progress(replace(result, details=list(result.details)))

        def open_directory(path, depth, media):
            try:
                _scan_path(base, path)
                stack.append((os.scandir(path), depth, media, path))
            except ValueError:
                result.skipped_links += 1
            except OSError as exc:
                issue(path, exc)

        # Iterators keep memory bounded even for a directory with many entries.
        if recursive:
            open_directory(base, 0, "UNKNOWN")
        else:
            for media in reversed(MEDIA):
                path = base / media
                try:
                    info = path.lstat()
                    if _linked(info):
                        result.skipped_links += 1
                    elif stat.S_ISDIR(info.st_mode):
                        open_directory(path, max_depth, media)
                except FileNotFoundError:
                    continue
                except OSError as exc:
                    issue(path, exc)
        try:
            while stack:
                if cancel is not None and cancel.is_set():
                    result.cancelled = True
                    break
                iterator, depth, media, directory = stack[-1]
                try:
                    entry = next(iterator)
                except StopIteration:
                    iterator.close()
                    stack.pop()
                    continue
                except OSError as exc:
                    issue(directory, exc)
                    iterator.close()
                    stack.pop()
                    continue
                if result.visited >= max_entries:
                    result.limit_reached = True
                    break
                result.visited += 1
                path = Path(entry.path)
                try:
                    info = entry.stat(follow_symlinks=False)
                    if _linked(info):
                        result.skipped_links += 1
                    elif stat.S_ISDIR(info.st_mode):
                        if recursive and depth < max_depth:
                            child_media = entry.name.upper() if depth == 0 and entry.name.upper() in MEDIA else media
                            open_directory(path, depth + 1, child_media)
                        else:
                            result.skipped_depth += 1
                    elif stat.S_ISREG(info.st_mode) and path.suffix.lower() in IMAGE_EXTENSIONS:
                        source = _scan_path(base, path)
                        if info.st_size <= 0:
                            result.skipped_invalid += 1
                        else:
                            self.upsert(source, media)
                            result.indexed += 1
                    else:
                        result.skipped_unsupported += 1
                except ValueError:
                    result.skipped_invalid += 1
                except OSError as exc:
                    issue(path, exc)
                if result.visited % 128 == 0:
                    notify()
        finally:
            for iterator, _, _, _ in stack:
                iterator.close()
        notify()
        return result

    def inspect_identities(self, root, *, max_entries=1000, cancel=None, progress=None):
        """Opt-in sparse inspection of indexed entries under one selected root.

        No image is installed or renamed, and no manually entered ID is changed.
        A matching filesystem fingerprint reuses a completed inspection. Errors
        and files changed during inspection are retried on the next invocation.
        """
        if type(max_entries) is not int or not 1 <= max_entries <= 10_000:
            raise ValueError("Inspection limit must be 1 through 10000.")
        base = require_directory(root)
        prefix = str(base)
        if not prefix.endswith(os.sep):
            prefix += os.sep
        collation = "COLLATE NOCASE" if os.name == "nt" else ""
        entries = self.db.execute(
            f"""SELECT id, image_path FROM games
                WHERE substr(image_path,1,?)=? {collation} ORDER BY id LIMIT ?""",
            (len(prefix), prefix, max_entries + 1)).fetchall()
        result = IdentityScanResult()

        def notify():
            if progress:
                progress(replace(result, details=list(result.details)))

        for record in entries:
            if cancel is not None and cancel.is_set():
                result.cancelled = True
                break
            if result.visited >= max_entries:
                result.limit_reached = True
                break
            result.visited += 1
            source = Path(record["image_path"])
            stamp = ""
            try:
                source = _scan_path(base, source)
                info = source.stat()
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError("Image path is no longer a regular file.")
                stamp = disc_identity.fingerprint(info)
                cached = self.db.execute(
                    """SELECT status FROM disc_identity WHERE image_path=?
                       AND fingerprint=? AND inspector_version=?""",
                    (record["image_path"], stamp, disc_identity.INSPECTOR_VERSION)).fetchone()
                if cached and cached["status"] in ("verified", "unknown", "invalid", "unsupported"):
                    result.cached += 1
                else:
                    identity = disc_identity.inspect_disc(source, cancel=cancel)
                    result.inspected += 1
                    # Include the interval between our cache lookup and the
                    # inspector opening the file in the mutation check.
                    if disc_identity.fingerprint(_scan_path(base, source).stat()) != stamp:
                        identity = disc_identity.DiscIdentity("changed", detail="Image changed; retry inspection.")
                    self._save_identity(record["image_path"], stamp, identity)
            except InterruptedError:
                result.cancelled = True
                break
            except (OSError, ValueError) as exc:
                self._save_identity(record["image_path"], stamp,
                                    disc_identity.DiscIdentity("unavailable", detail=str(exc)))
            row = self.get_game(record["id"])
            status = row["identity_status"]
            counter = {"conflict": "conflicts", "unavailable": "errors", "changed": "errors", "stale": "errors"}.get(status, status)
            setattr(result, counter, getattr(result, counter) + 1)
            if status != "verified" and len(result.details) < 20:
                result.details.append(f"{source.relative_to(base)}: {row['identity_detail']}")
            if result.visited % 16 == 0:
                notify()
        notify()
        return result

    def _save_identity(self, path, stamp, identity):
        self.db.execute(
            """INSERT OR REPLACE INTO disc_identity
               (image_path,fingerprint,inspector_version,status,detected_disc_id,boot_path,detail,checked)
               VALUES(?,?,?,?,?,?,?,?)""",
            (path, stamp, disc_identity.INSPECTOR_VERSION, identity.status,
             identity.disc_id, identity.boot_path, identity.detail, int(time.time())))
        self.db.commit()

    def indexed_games(self, root, *, max_entries=10_000, cancel=None):
        """Expose the same read-only descriptors as the optional RA adapter."""
        from .library_index import read_index
        return read_index(self.path, root, max_entries=max_entries, cancel=cancel)

    def edit(self, row_id, title, disc_id="", notes=""):
        title = str(title).strip()
        if not title or len(title) > 160 or len(disc_id) > 64 or len(notes) > 2000:
            raise ValueError("Title is required (up to 160 characters); ID/notes are bounded.")
        cur = self.db.execute(
            "UPDATE games SET title=?, disc_id=?, notes=?, updated=? WHERE id=?",
            (title, str(disc_id).strip(), str(notes).strip(), int(time.time()), int(row_id)))
        self.db.commit()
        if cur.rowcount != 1:
            raise ValueError("The selected game no longer exists.")

    def import_image(self, source, root, media="DVD"):
        """Non-destructive copy. Never overwrite an existing ISO or mutate an original."""
        base = require_directory(root)
        if media not in MEDIA:
            raise ValueError("Choose CD or DVD.")
        image = Path(source).expanduser()
        if image.is_symlink() or not image.is_file() or image.suffix.lower() not in IMAGE_EXTENSIONS:
            raise ValueError("Select a local ISO/CSO/ZSO/CHD image.")
        destdir = base / media
        if destdir.is_symlink():
            raise ValueError("Refusing to install into a symlinked games folder.")
        destdir.mkdir(parents=True, exist_ok=True)
        target = destdir / image.name
        if image.resolve() == target.resolve() or target.exists():
            raise FileExistsError("Image already exists in the library: {}".format(target))
        # x+b uses O_EXCL: no old file can be clobbered, including on exFAT/FAT.
        # This operation is separate from game serving; stop transfers before importing.
        created = False
        try:
            with image.open("rb") as inp, target.open("xb") as out:
                created = True
                shutil.copyfileobj(inp, out, 1024 * 1024)
                out.flush()
                os.fsync(out.fileno())
            if target.stat().st_size != image.stat().st_size:
                raise OSError("Transferred size differs from source.")
        except BaseException:
            if created:
                target.unlink(missing_ok=True)
            raise
        self.upsert(target, media)
        return target

    def import_images(self, sources, root, media="DVD"):
        """Queue selected local images one by one; failures never clobber valid files."""
        chosen = list(sources)
        if not chosen or len(chosen) > 1000:
            raise ValueError("Select between 1 and 1000 local disc images.")
        completed, skipped = [], []
        for image in chosen:
            try:
                completed.append(str(self.import_image(image, root, media)))
            except (OSError, ValueError) as exc:
                skipped.append((str(image), str(exc)))
        return {"imported": len(completed), "skipped": len(skipped), "items": completed,
                "errors": skipped[:12]}

    def migrate_legacy_art(self, root):
        """Convert existing ART JPEG/WebP/BMP entries to 8-bit PNG without deleting originals.

        Exact RiptOPL identity is preserved by stripping only the final _TYPE
        suffix. Existing valid PNG files always win; invalid or linked input
        files are ignored rather than replacing the user's artwork.
        """
        folder = require_directory(root) / "ART"
        if folder.is_symlink() or not folder.is_dir():
            return {"converted": 0, "skipped": 0, "errors": 0}
        matched = re.compile(r"^(.+)_(COV|ICO|LAB|COV3)\.(jpe?g|webp|bmp)$", re.I)
        converted = skipped = errors = 0
        for entry in sorted(folder.iterdir()):
            if entry.is_symlink() or not entry.is_file():
                continue
            match = matched.fullmatch(entry.name)
            if not match:
                continue
            name, art_type = match.group(1), match.group(2).upper()
            try:
                output = folder / artwork_filename(name, art_type)
            except ValueError:
                skipped += 1
                continue
            if output.exists() or output.is_symlink():
                skipped += 1
                continue
            try:
                self.write_art(entry, root, name, art_type)
                converted += 1
            except (OSError, ValueError, RuntimeError):
                errors += 1
        return {"converted": converted, "skipped": skipped, "errors": errors}

    def write_art(self, source, root, identity, art_type="COV"):
        base = require_directory(root)
        destination = base / "ART"
        if destination.is_symlink():
            raise ValueError("Refusing to install artwork into a symlinked ART folder.")
        name = artwork_filename(identity, art_type)
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / name
        if target.exists():
            raise FileExistsError("Existing cover preserved: {}".format(target))
        try:
            from PIL import Image, ImageOps, UnidentifiedImageError
        except ImportError as exc:
            raise RuntimeError("Cover conversion requires Pillow; install Pillow to use artwork import.") from exc
        created = False
        try:
            with Image.open(source) as src:
                if src.width > 10000 or src.height > 10000 or src.width * src.height > 50_000_000:
                    raise ValueError("Artwork dimensions exceed safety limits.")
                src.load()
                img = _artwork_palette_image(Image, ImageOps.exif_transpose(src))
                with target.open("xb") as out:
                    created = True
                    img.save(out, format="PNG", optimize=True)
                    out.flush()
                    os.fsync(out.fileno())
        except BaseException:
            if created:
                target.unlink(missing_ok=True)
            raise
        return target

    def export_json(self, destination):
        path = Path(destination)
        # Never put a backup on top of the live SQLite database or existing files.
        with path.open("x", encoding="utf-8") as output:
            json.dump({"format": "PS2ServersLibrary/1", "games": self.list_games()},
                      output, ensure_ascii=False, indent=2)
        return path

    def import_json(self, source):
        doc = json.loads(Path(source).read_text(encoding="utf-8"))
        if not isinstance(doc, dict) or doc.get("format") != "PS2ServersLibrary/1":
            raise ValueError("Unsupported catalogue backup format.")
        games = doc.get("games")
        if not isinstance(games, list) or len(games) > 100_000:
            raise ValueError("Malformed or oversized catalogue.")
        accepted = 0
        with self.db:
            for item in games:
                if not isinstance(item, dict):
                    raise ValueError("Invalid catalogue entry.")
                image = item.get("image_path")
                title = item.get("title")
                media = item.get("media")
                if not isinstance(image, str) or len(image) > 4096 or not isinstance(title, str) or not title.strip() or len(title) > 160 or media not in INDEX_MEDIA:
                    raise ValueError("Invalid catalogue entry.")
                disc_id = str(item.get("disc_id", ""))[:64]
                notes = str(item.get("notes", ""))[:2000]
                # Metadata import never copies games or touches any installed image.
                self.db.execute(
                    """INSERT INTO games(image_path,title,disc_id,media,image_bytes,notes,updated)
                       VALUES(?,?,?,?,?,?,?)
                       ON CONFLICT(image_path) DO UPDATE SET
                         title=excluded.title,disc_id=excluded.disc_id,
                         notes=excluded.notes,updated=excluded.updated""",
                    (image, title.strip(), disc_id, media,
                     max(0, int(item.get("image_bytes", 0))), notes, int(time.time())))
                accepted += 1
        return accepted

    def backup_sqlite(self, destination):
        """Consistent online snapshot; protect source DB and refuse overwrites."""
        target = Path(destination)
        if target.resolve() == self.path.resolve() or target.exists():
            raise FileExistsError("Choose a new backup file; existing files are never replaced.")
        fd, temp_name = tempfile.mkstemp(prefix=".ps2library-", suffix=".sqlite", dir=target.parent)
        os.close(fd)
        temporary = Path(temp_name)
        created = False
        try:
            with closing(sqlite3.connect(str(temporary))) as output:
                self.db.backup(output)
            with temporary.open("rb") as inp, target.open("xb") as out:
                created = True
                shutil.copyfileobj(inp, out)
                out.flush()
                os.fsync(out.fileno())
        except BaseException:
            if created:
                target.unlink(missing_ok=True)
            raise
        finally:
            temporary.unlink(missing_ok=True)
        return target
