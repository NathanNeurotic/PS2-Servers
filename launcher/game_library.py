"""Local PS2 catalogue and atomic image installation, independent of Electron."""
import json
import io
from contextlib import closing, contextmanager
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
import warnings

from launcher.config import config_dir

IMAGE_TYPES = {".iso", ".chd", ".cso", ".zso"}
PS1_TYPES = {".vcd"}
MEDIA_TYPES = {"CD": IMAGE_TYPES, "DVD": IMAGE_TYPES, "POPS": PS1_TYPES}
RA_CONSOLES = (21, 12)  # PlayStation 2, PlayStation 1


def web_url(value):
    value = str(value or "").strip()
    if not value:
        return ""
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Use an HTTP or HTTPS URL without embedded credentials.")
    return value


def safe_name(value):
    name = Path(str(value)).name
    if name != value or any(c in name for c in '<>:"/\\|?*') or any(ord(c) < 32 for c in name):
        raise ValueError("Choose a plain file name without path separators.")
    if not name or name.endswith((".", " ")) or name.split(".", 1)[0].upper() in {
            "CON", "PRN", "AUX", "NUL", *["COM" + str(i) for i in range(1, 10)],
            *["LPT" + str(i) for i in range(1, 10)]}:
        raise ValueError("This file name cannot be used safely on all supported systems.")
    return name


def publish_new(temporary, destination):
    """Publish a finished file without ever replacing an existing image."""
    try:
        os.link(temporary, destination)
    except OSError:
        if os.name != "nt":
            raise
        # Windows rename refuses an existing destination and works on exFAT.
        os.rename(temporary, destination)
    else:
        os.unlink(temporary)


class Library:
    def __init__(self, directory=None):
        self.directory = Path(directory or Path(config_dir()) / "library")
        self.directory.mkdir(parents=True, exist_ok=True)
        self.database = self.directory / "achievement-catalog.sqlite3"
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS games (
                id INTEGER PRIMARY KEY, mongo_id TEXT, title TEXT NOT NULL,
                console TEXT NOT NULL DEFAULT 'PS2', icon TEXT, original_name TEXT,
                source TEXT, game_id TEXT, download_url TEXT, downloads_json TEXT DEFAULT '[]',
                created_at TEXT, updated_at TEXT)""")
            db.execute("""CREATE TABLE IF NOT EXISTS image_status (
                path TEXT PRIMARY KEY, stamp TEXT, hash TEXT, status TEXT,
                game_id INTEGER, title TEXT, count INTEGER, checked_at REAL)""")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.database, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def games(self, query="", limit=100000):
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM games WHERE instr(lower(title),lower(?))>0 ORDER BY title COLLATE NOCASE LIMIT ?", (query, int(limit)))]

    @staticmethod
    def normalize(row):
        if not isinstance(row, dict) or not isinstance(row.get("title"), str) or not row["title"].strip():
            raise ValueError("Every catalogue entry needs a title.")
        if str(row.get("console") or "PS2").upper() != "PS2":
            raise ValueError("Only PS2 catalogue entries are supported.")
        downloads = row["downloads"] if "downloads" in row else json.loads(row.get("downloads_json") or "[]")
        if not isinstance(downloads, list):
            raise ValueError("Downloads must be a list.")
        for item in downloads:
            if not isinstance(item, dict) or not web_url(item.get("url")):
                raise ValueError("Each download needs an HTTP or HTTPS URL.")
        mongo = row.get("mongo_id") or row.get("_id") or ""
        if isinstance(mongo, dict):
            mongo = mongo.get("$oid", "")
        return (str(mongo), row["title"].strip()[:500], "PS2", web_url(row.get("icon")),
                str(row.get("original_name") or row.get("originalName") or "")[:500],
                str(row.get("source") or "")[:500], str(row.get("game_id") or row.get("gameId") or "")[:100],
                web_url(row.get("download_url") or row.get("downloadUrl") or
                        (downloads[0]["url"] if downloads else "")), json.dumps(downloads))

    def save_game(self, row, game_id=None):
        values = self.normalize(row)
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with self.connect() as db:
            if game_id is None:
                cursor = db.execute("""INSERT INTO games
                    (mongo_id,title,console,icon,original_name,source,game_id,download_url,downloads_json,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""", values + (now, now))
                return cursor.lastrowid
            db.execute("""UPDATE games SET mongo_id=?,title=?,console=?,icon=?,original_name=?,source=?,
                game_id=?,download_url=?,downloads_json=?,updated_at=? WHERE id=?""", values + (now, int(game_id)))
            return game_id

    def remove_game(self, game_id):
        # Catalogue deletion never removes an ISO, cover or save.
        with self.connect() as db:
            db.execute("DELETE FROM games WHERE id=?", (int(game_id),))

    def cover_path(self, game_id):
        return self.directory / "covers" / (str(int(game_id)) + ".png")

    def repair_cover(self, row, folder=None):
        from PIL import Image
        url = web_url(row.get("icon"))
        if not url:
            raise ValueError("This catalogue entry has no cover URL.")
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "PS2-Servers"}), timeout=15) as response:
            data = response.read(16 * 1024 * 1024 + 1)
        if len(data) > 16 * 1024 * 1024:
            raise ValueError("The cover image is too large.")
        target = self.cover_path(row["id"])
        target.parent.mkdir(parents=True, exist_ok=True)
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.width * image.height > 16000000:
                    raise ValueError("The cover exceeds the 16-megapixel image limit.")
                image.load()
                image = image.convert("RGBA")
                image.thumbnail((512, 512))
                # RiptOPL/OPL artwork uses indexed 8-bit PNG; RGBA output
                # otherwise looks fine on desktop but fails on console.
                # FASTOCTREE supports RGBA inputs and retains transparency.
                image = image.quantize(colors=256, method=Image.Quantize.FASTOCTREE)
                with tempfile.NamedTemporaryFile(dir=target.parent, suffix=".png", delete=False) as output:
                    temporary = Path(output.name)
                try:
                    image.save(temporary, "PNG")
                    os.replace(temporary, target)
                finally:
                    temporary.unlink(missing_ok=True)
        serial = str(row.get("game_id") or "").upper()
        if folder and re.fullmatch(r"[A-Z]{4}_\d{3}\.\d{2}", serial):
            art = Path(folder) / "ART"
            art.mkdir(parents=True, exist_ok=True)
            cover = art / (serial + "_COV.png")
            # Existing console artwork may be a user's custom cover.
            if not cover.exists():
                with tempfile.NamedTemporaryFile(dir=art, suffix=".part", delete=False) as output:
                    temporary = Path(output.name)
                try:
                    shutil.copyfile(target, temporary)
                    publish_new(temporary, cover)
                finally:
                    temporary.unlink(missing_ok=True)
        return str(target)

    def backup(self, destination):
        destination = Path(destination).resolve()
        active = self.database.resolve()
        if destination in (active, Path(str(active) + "-wal"), Path(str(active) + "-shm"), Path(str(active) + "-journal")):
            raise ValueError("Choose a destination outside the active database files.")
        if destination.exists() and os.path.samefile(destination, active):
            raise ValueError("A backup cannot replace the active database.")
        with tempfile.NamedTemporaryFile(dir=destination.parent, suffix=".sqlite3", delete=False) as file:
            temporary = Path(file.name)
        try:
            with self.connect() as source, closing(sqlite3.connect(temporary)) as target:
                source.backup(target)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return str(destination)

    def import_catalog(self, source):
        source = Path(source)
        if source.stat().st_size > 100 * 1024 * 1024:
            raise ValueError("Catalogue imports are limited to 100 MB.")
        if source.suffix.lower() == ".json":
            rows = json.loads(source.read_text("utf-8-sig"))
            if isinstance(rows, dict):
                rows = rows.get("games")
        else:
            with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as db:
                db.row_factory = sqlite3.Row
                rows = [dict(row) for row in db.execute("SELECT * FROM games LIMIT 100001")]
        if not isinstance(rows, list) or not 1 <= len(rows) <= 100000:
            raise ValueError("A catalogue must contain 1 to 100,000 games.")
        normalized = [self.normalize(row) for row in rows]
        backup = self.directory / ("before-import-" + str(time.time_ns()) + ".sqlite3")
        self.backup(backup)
        added = skipped = 0
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with self.connect() as db:
            for row in normalized:
                if db.execute("SELECT 1 FROM games WHERE title=? COLLATE NOCASE OR (mongo_id<>'' AND mongo_id=?)",
                              (row[1], row[0])).fetchone():
                    skipped += 1
                    continue
                db.execute("""INSERT INTO games (mongo_id,title,console,icon,original_name,source,
                    game_id,download_url,downloads_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                           row + (now, now))
                added += 1
        return added, skipped, str(backup)

    @staticmethod
    def installed(folder):
        root = Path(folder)
        result = []
        for kind, extensions in MEDIA_TYPES.items():
            directory = root / kind
            if directory.is_dir():
                for path in directory.iterdir():
                    if path.is_file() and path.suffix.lower() in extensions:
                        stat = path.stat()
                        result.append({"path": str(path), "name": path.name, "kind": kind, "size": stat.st_size,
                                       "stamp": "{}:{}:{}".format(stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)})
        return sorted(result, key=lambda row: row["name"].casefold())

    @staticmethod
    def install(source, folder, kind="DVD", name=None, cancel=None, progress=None):
        source = Path(source)
        if source.suffix.lower() not in MEDIA_TYPES.get(kind, set()) or not source.is_file():
            raise ValueError("Choose a PS2 image for CD/DVD or a PS1 VCD for POPS.")
        before = source.stat()
        return Library.transfer(source.open("rb"), folder, kind, name or source.name,
                                before.st_size, cancel, progress, source=source, stamp=before, validate_image=True)

    @staticmethod
    def download(url, folder, kind, name, cancel=None, progress=None):
        request = urllib.request.Request(web_url(url), headers={"User-Agent": "PS2-Servers"})
        response = urllib.request.urlopen(request, timeout=15)
        length = response.headers.get("Content-Length")
        return Library.transfer(response, folder, kind, name, int(length) if length else None,
                                cancel, progress, validate_image=True)

    @staticmethod
    def transfer(stream, folder, kind, name, total=None, cancel=None, progress=None, source=None, stamp=None, validate_image=False):
        temporary = None
        try:
            if kind not in MEDIA_TYPES:
                raise ValueError("Select CD, DVD or POPS.")
            name = safe_name(name)
            if Path(name).suffix.lower() not in MEDIA_TYPES[kind]:
                raise ValueError("CD/DVD require a PS2 image; POPS requires a PS1 VCD.")
            directory = Path(folder) / kind
            directory.mkdir(parents=True, exist_ok=True)
            destination = directory / name
            if destination.exists():
                raise FileExistsError("This image already exists; it will not be overwritten.")
            if total is not None and (total < 0 or shutil.disk_usage(directory).free < total):
                raise ValueError("There is not enough free space for this image.")
            with tempfile.NamedTemporaryFile(dir=directory, prefix=".ps2-import-", suffix=".part", delete=False) as output:
                temporary = Path(output.name)
                done = 0
                while True:
                    if cancel and cancel.is_set():
                        raise InterruptedError("Transfer cancelled; no image was installed.")
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
                output.flush()
                os.fsync(output.fileno())
            if total is not None and done != total:
                raise ValueError("The transfer was incomplete; no image was installed.")
            if not done:
                raise ValueError("The image is empty.")
            if validate_image:
                with temporary.open("rb") as image:
                    suffix = Path(name).suffix.lower()
                    if suffix == ".iso":
                        image.seek(16 * 2048 + 1)
                        valid = image.read(5) == b"CD001"
                    elif suffix == ".vcd":
                        image.seek(0x100000 + 16 * 2352 + 24 + 1)
                        valid = image.read(5) == b"CD001"
                    else:
                        magic = {".chd": b"MComprHD", ".cso": b"CISO", ".zso": b"ZISO"}[suffix]
                        valid = image.read(len(magic)) == magic
                if not valid:
                    raise ValueError("The downloaded/imported file is not a valid image of the selected type.")
            if source:
                after = source.stat()
                if (after.st_size, after.st_mtime_ns) != (stamp.st_size, stamp.st_mtime_ns):
                    raise ValueError("The source image changed during import.")
            if cancel and cancel.is_set():
                raise InterruptedError("Transfer cancelled; no image was installed.")
            publish_new(temporary, destination)
            return str(destination)
        finally:
            stream.close()
            if temporary:
                temporary.unlink(missing_ok=True)


def hash_image(path):
    from launcher.achievements import engine_path
    path = Path(path)
    if path.suffix.lower() == ".vcd":
        from launcher.ps1_vcd import hash_vcd
        before = path.stat()
        result = hash_vcd(path)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError("The VCD changed during hashing; retry the scan.")
        return result
    if path.suffix.lower() in {".chd", ".cso", ".zso"}:
        from launcher.ra_compressed_hash import hash_compressed_ps2
        before = path.stat()
        digest = hash_compressed_ps2(path)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError("The compressed disc changed during hashing; retry the scan.")
        return digest
    if path.suffix.lower() != ".iso":
        raise ValueError("Achievement compatibility scanning requires a PS2 ISO/CHD/CSO/ZSO or PS1 VCD.")
    before = path.stat()
    result = subprocess.run([str(engine_path()), "--hash-file", str(path.resolve())],
                            capture_output=True, timeout=120,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("The image changed during hashing; try again.")
    image_hash = result.stdout.decode("ascii", errors="replace").strip()
    if result.returncode or not re.fullmatch("[a-f0-9]{32}", image_hash):
        raise ValueError("Could not identify the PS2 executable in this ISO.")
    return image_hash


class Compatibility:
    def __init__(self, library, account=None):
        self.library = library
        self.index_file = library.directory / "ra-hashes.json"
        self.index = None
        self.hashes = {}
        if account is None:
            from launcher.achievements import account_url, profile_dir
            from launcher.caduceus import Account

            def state():
                with urllib.request.urlopen(account_url() + "state", timeout=3) as response:
                    return json.load(response)

            account = Account(profile_dir(), state)
        self.account = account

    def refresh(self, force=False):
        if self.index is None:
            try:
                self.load_index(json.loads(self.index_file.read_text("utf-8")))
            except (OSError, ValueError, TypeError, KeyError):
                pass
        if not force and self.index and set(self.index.get("consoles", ())) == set(RA_CONSOLES) \
                and time.time() - self.index["at"] < 86400:
            return
        # Both systems must load successfully before publishing a combined
        # index. Old PS2-only caches are refreshed, not used to label PS1 NO.
        games = []
        for console in RA_CONSOLES:
            entries = self.account.request("API_GetGameList", i=console, h=1, f=1)
            if not isinstance(entries, list):
                raise ValueError("Invalid RetroAchievements console catalogue.")
            games.extend(dict(entry, console_id=console) for entry in entries)
        index = {"at": time.time(), "consoles": list(RA_CONSOLES), "games": games}
        self.load_index(index)
        with tempfile.NamedTemporaryFile(dir=self.library.directory, suffix=".part", mode="w", encoding="utf-8", delete=False) as file:
            temporary = Path(file.name)
            json.dump(index, file)
        try:
            os.replace(temporary, self.index_file)
        finally:
            temporary.unlink(missing_ok=True)

    def load_index(self, index):
        if not isinstance(index, dict) or not isinstance(index.get("at"), (int, float)) or not isinstance(index.get("games"), list):
            raise ValueError("Invalid achievement hash catalogue.")
        hashes = {}
        for game in index["games"]:
            if not isinstance(game, dict) or not isinstance(game.get("Hashes"), list):
                raise ValueError("Invalid achievement hash catalogue.")
            game_id, count = int(game["ID"]), int(game["NumAchievements"])
            if game_id <= 0 or count < 0:
                raise ValueError("Invalid achievement hash catalogue.")
            for image_hash in game["Hashes"]:
                if not isinstance(image_hash, str) or not re.fullmatch("[a-fA-F0-9]{32}", image_hash):
                    raise ValueError("Invalid image hash in the achievement catalogue.")
                hashes[image_hash.lower()] = {"game_id": game_id, "title": str(game.get("Title") or ""), "count": count}
        self.index, self.hashes = index, hashes

    def check(self, path):
        path = Path(path).resolve()
        if path.suffix.lower() not in {".iso", ".chd", ".cso", ".zso", ".vcd"}:
            return {"status": "unsupported", "message": "Compatibility scanning requires PS2 ISO/CHD/CSO/ZSO or PS1 VCD."}
        stat = path.stat()
        stamp = "{}:{}:{}".format(stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        with self.library.connect() as db:
            cached = db.execute("SELECT * FROM image_status WHERE path=? AND stamp=?", (str(path), stamp)).fetchone()
        try:
            self.refresh()
        except (OSError, ValueError):
            # Only reuse a previously checked, unchanged PS2 ISO offline.
            if path.suffix.lower() != ".iso" or cached is None:
                raise
            return {key: cached[key] for key in
                    ("status", "hash", "game_id", "title", "count", "checked_at")}
        image_hash = cached["hash"] if cached else hash_image(path)
        game = self.hashes.get(image_hash)
        status = "compatible" if game and game["count"] else "unmatched"
        result = dict(game or {"game_id": 0, "title": "", "count": 0}, status=status, hash=image_hash,
                      checked_at=self.index["at"])
        with self.library.connect() as db:
            db.execute("""INSERT OR REPLACE INTO image_status
                (path,stamp,hash,status,game_id,title,count,checked_at) VALUES (?,?,?,?,?,?,?,?)""",
                       (str(path), stamp, image_hash, status, result["game_id"], result["title"], result["count"], result["checked_at"]))
        return result
