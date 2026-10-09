"""Optional local library catalogue for PS2-Servers Desktop.

The database is metadata only; opening it never alters game images or saves.
No game-serving hot path imports this module.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import time

from .config import config_dir

IMAGE_EXTENSIONS = frozenset({".iso", ".zso", ".cso", ".chd"})
MEDIA = ("DVD", "CD")
ART_TYPES = frozenset({"COV", "ICO", "LAB", "COV3"})
KEY_PATTERN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_. ()\[\]-]{0,95}$")
SERIAL_PREFIX = re.compile(r"^[A-Z]{4}[_-]\d{3}\.\d{2}[. _-]+", re.I)


def default_database():
    return Path(config_dir()) / "library" / "catalog.sqlite3"


def require_directory(root):
    path = Path(root).expanduser().resolve(strict=True)
    if not path.is_dir():
        raise ValueError("Choose an existing games folder.")
    return path


def visible_title(path):
    return SERIAL_PREFIX.sub("", Path(path).stem).replace("_", " ").strip() or Path(path).stem


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
        self.db.commit()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.db.close()

    def list_games(self, search=""):
        value = "%" + search.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        return [dict(r) for r in self.db.execute(
            """SELECT * FROM games
               WHERE lower(title) LIKE ? ESCAPE '\\' OR lower(disc_id) LIKE ? ESCAPE '\\'
               ORDER BY title COLLATE NOCASE, id LIMIT 10000""", (value, value))]

    def get_game(self, row_id):
        record = self.db.execute("SELECT * FROM games WHERE id=?", (int(row_id),)).fetchone()
        return dict(record) if record else None

    def upsert(self, filename, media="DVD"):
        if media not in MEDIA:
            raise ValueError("Choose CD or DVD.")
        source = Path(filename).resolve(strict=True)
        if source.is_symlink() or not source.is_file() or source.suffix.lower() not in IMAGE_EXTENSIONS:
            raise ValueError("Choose a supported PS2 ISO/CSO/ZSO/CHD file.")
        size = source.stat().st_size
        if size <= 0:
            raise ValueError("An empty image cannot be installed.")
        self.db.execute(
            """INSERT INTO games(image_path,title,media,image_bytes,updated)
               VALUES(?,?,?,?,?)
               ON CONFLICT(image_path) DO UPDATE SET
                   image_bytes=excluded.image_bytes, media=excluded.media,
                   updated=excluded.updated""",
            (str(source), visible_title(source), media, size, int(time.time())))
        self.db.commit()
        return self.db.execute("SELECT id FROM games WHERE image_path=?", (str(source),)).fetchone()[0]

    def scan(self, root):
        """Top-level CD/DVD folders only; no symlinks or unexpected folder trees."""
        base = require_directory(root)
        count = 0
        for media in MEDIA:
            directory = base / media
            if directory.is_symlink() or not directory.is_dir():
                continue
            for entry in directory.iterdir():
                if entry.is_symlink() or not entry.is_file() or entry.suffix.lower() not in IMAGE_EXTENSIONS:
                    continue
                try:
                    self.upsert(entry, media)
                    count += 1
                except (OSError, ValueError):
                    continue
        return count

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
        matched = re.compile(r"^(.+)_(COV|ICO|LAB|COV3)\\.(jpe?g|webp|bmp)$", re.I)
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
                img = ImageOps.exif_transpose(src).convert("RGB")
                # 8-bit palette PNG, as required by the RiptOPL artwork pipeline.
                img = img.quantize(colors=256, method=Image.Quantize.MEDIANCUT)
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
                if not isinstance(image, str) or len(image) > 4096 or not isinstance(title, str) or not title.strip() or len(title) > 160 or media not in MEDIA:
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
            with sqlite3.connect(str(temporary)) as output:
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
