#!/usr/bin/env python3
"""PS2 Servers HTTP entry point.

Serves an OPL games folder over HTTP for Open PS2 Loader's HTTP mode
(Docmine17/Open-PS2-Loader-HTTP): a generated ``games.csv`` game list, plus
Range-request ISO streaming for the in-game ``http_cdvdman.irx`` driver.

The console is far stricter than "any web server that supports Range" -- the
full wire contract is in docs/HTTP.md. The four rules that shape this file:

  * A range read MUST be answered with 206. A 200 is a hard failure (-5) in
    the IOP driver, so a Range header is never ignored here.
  * The body MUST be EXACTLY the requested byte count. The driver ignores
    Content-Length and Content-Range and reads precisely what it asked for, so
    a single extra byte desynchronises the keep-alive socket for every later
    read -- which shows up as a game that loads and then corrupts, not as an
    obvious network error.
  * The whole response header block must fit the driver's 512-byte buffer.
    That is why no Server banner is sent and Content-Type is a fixed string
    rather than a mimetypes lookup.
  * games.csv must carry a Content-Length and must never be chunked (the
    client's chunked-transfer branch is commented out), and it must fit 8192
    bytes or the console silently truncates the tail mid-line.

ISOs are requested from the FLAT web root -- the driver hardcodes its base
path to "/" -- while games.csv is fetched from "/<share>/games.csv". That
asymmetry is why this server serves from a filename index built across the
root, DVD/ and CD/ rather than translating a URL into a path.
"""

import argparse
import os
import posixpath
import re
import socket
import sys
import threading
import time
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

# Compressed-image support is shared with UDPFS rather than reimplemented. The
# extension table there carries a standing warning against second copies: a
# hardcoded list at a call site is exactly how CHD files silently vanished from
# UDPFS listings once before. Reuse it, or inherit that bug.
_HERE = os.path.dirname(os.path.abspath(__file__))
_UDPFS_DIR = os.path.join(os.path.dirname(_HERE), "udpfs_server")

COMPRESSION_AVAILABLE = False
_COMPRESSED_EXTENSIONS = ()
_open_compressed = None
_get_compressed_info = None
try:
    if os.path.isdir(_UDPFS_DIR) and _UDPFS_DIR not in sys.path:
        sys.path.insert(0, _UDPFS_DIR)
    from udpfs_server import COMPRESSED_EXTENSIONS as _COMPRESSED_EXTENSIONS
    from udpfs_server import get_compressed_info as _get_compressed_info
    from udpfs_server import open_compressed as _open_compressed
    COMPRESSION_AVAILABLE = True
except Exception:  # pragma: no cover - exercised only in a broken tree
    pass


DEFAULT_PORT = 1100

#: Extensions the console reads itself, streamed byte-for-byte. ZSO decoding
#: lives in cdvdman.c above the device layer (ProbeZSO / ziso_read_sector), so
#: it applies to http_cdvdman.irx too -- the PS2 decompresses these, which
#: means ZSO works here even when lz4 is absent on the PC.
RAW_EXTENSIONS = (".iso", ".zso", ".ziso")

#: '.ziso' is a ZSO spelling several conversion tools emit, but OPL's games.csv
#: parser only recognises the literal '.iso' and '.zso'. Advertising the real
#: name would make the console request "Game.ziso.iso", so it is renamed.
_ADVERTISED_RAW_EXTENSION = {".iso": ".iso", ".zso": ".zso", ".ziso": ".zso"}

#: OPL's on-disc naming convention, e.g. "SLUS_201.74.Rumble Racing.iso".
_STARTUP_RE = re.compile(r"^([A-Za-z]{4}_\d{3}\.\d{2})\.(.+)$")

#: base_game_info_t.startup is char[GAME_STARTUP_MAX + 1] with
#: GAME_STARTUP_MAX 12 (include/supportbase.h), and settings_http->filename is
#: char[160]. Exceeding either truncates on the console, and a truncated
#: filename means the ISO request 404s with no clue why.
STARTUP_MAX = 12
FILENAME_MAX = 159

#: The console's games.csv receive buffer (src/ethsupport.c: out_len = 8192).
GAMES_CSV_MAX = 8192

#: Anything at or below this is a CD-media title when the folder does not say.
#: A pressed PS2 CD-ROM tops out around 700 MB.
CD_SIZE_LIMIT = 800 * 1024 * 1024

MEDIA_CD = "CD"
MEDIA_DVD = "DVD"

#: Catalogue profiles. The flat profile is the default and is unchanged: it is
#: what the pinned Docmine17 client parses. The path-aware profile is for
#: RiptOPL only, whose parser (src/httpcatalog.c) reads a quoted four-field
#: STARTUP,TITLE,MEDIA,RELATIVE_PATH row and requests <base>/<path>. Neither
#: client can be told apart on the wire, so the choice is explicit.
PROFILE_LEGACY_FLAT = "legacy-flat"
PROFILE_RIPTOPL_PATH_AWARE = "riptopl-path-aware"
PROFILES = (PROFILE_LEGACY_FLAT, PROFILE_RIPTOPL_PATH_AWARE)

#: RiptOPL's catalogue contract, from NathanNeurotic/Open-PS2-Loader at
#: 4cfde79ca604af11fe8b610c12514f818c99b689 (include/httpcatalog.h and
#: include/httpsupport.h). Limits are UTF-8 bytes, as the C side measures
#: them with strlen. Exceeding the row or byte limit fails the client's WHOLE
#: catalogue rather than truncating it, so this server drops rows and says so.
RIPTOPL_STARTUP_MAX = 12
RIPTOPL_TITLE_MAX = 160
RIPTOPL_PATH_MAX = 255
RIPTOPL_CATALOG_BYTES_MAX = 262144
RIPTOPL_CATALOG_ROWS_MAX = 2048
#: modules/network/common/httpclient.h HTTP_CLIENT_STREAM_URI_MAX and
#: include/opl.h HTTP_BASE_PATH_MAX at the same commit. The client percent-
#: encodes the path into a URI buffer after its base path; a path that cannot
#: fit is refused at launch, after it has already listed.
RIPTOPL_URI_MAX = 768
RIPTOPL_BASE_PATH_MAX = 64
_RIPTOPL_UNRESERVED = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.~/")

#: How far below the games folder --recursive looks. Matches the four levels
#: the library indexer offers.
RECURSIVE_MAX_DEPTH = 4
RECURSIVE_MAX_DIRS = 2000

#: Fixed rather than a mimetypes lookup: one less per-request call on the hot
#: path, and a deterministic contribution to the 512-byte header budget.
CONTENT_TYPE = "application/octet-stream"


def _log(message):
    print(message, flush=True)


def parse_startup(stem):
    """(startup, title) for a filename stem, per OPL's naming convention.

    Returns the startup id uppercased and the remaining title. When the stem
    does not follow the convention there is no id to recover -- STARTUP is
    derived from the stem instead so the game still appears, because a missing
    entry is worse than an imperfect one. The caller reports those so the user
    can see what to rename: art, per-game CFG and cheats all key off STARTUP,
    so a wrong id degrades quietly rather than failing loudly.
    """
    match = _STARTUP_RE.match(stem)
    if match:
        return match.group(1).upper(), match.group(2)
    return stem[:STARTUP_MAX], stem


def _utf8_prefix(text, limit):
    """The longest prefix of text that encodes to at most limit UTF-8 bytes."""
    data = text.encode("utf-8")
    if len(data) <= limit:
        return text
    return data[:limit].decode("utf-8", "ignore")


def _csv_quote(text):
    """RFC 4180 quoting, which RiptOPL's parser accepts for every field.

    Always quoting is what keeps a title or path starting with '#' from
    reading as a comment line, and a comma or quote from splitting a field.
    """
    return '"' + text.replace('"', '""') + '"'


def _has_control(text):
    return any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text)


def riptopl_encoded_path(rel_path):
    """rel_path percent-encoded exactly as RiptOPL's httpBuildPath does it."""
    return "".join(ch if ch in _RIPTOPL_UNRESERVED else "%{:02X}".format(byte)
                   for ch in rel_path for byte in ch.encode("utf-8"))


def riptopl_row_problem(startup, title, rel_path):
    """Why RiptOPL's httpCatalogParseLine would refuse this row, or None.

    Mirrors the pinned parser: fields are trimmed of blanks and tabs (even
    inside quotes), control bytes are refused, and the path must be a
    relative .iso/.zso path with no empty, '.' or '..' segment.
    """
    for label, value, limit in (("startup", startup, RIPTOPL_STARTUP_MAX),
                                ("title", title, RIPTOPL_TITLE_MAX),
                                ("path", rel_path, RIPTOPL_PATH_MAX)):
        if value != value.strip(" \t"):
            return "{} starts or ends with a blank, which the console trims".format(label)
        if not value:
            return "empty " + label
        if _has_control(value):
            return "control character in " + label
        try:
            size = len(value.encode("utf-8"))
        except UnicodeEncodeError:
            return "{} is not valid UTF-8".format(label)
        if size > limit:
            return "{} over {} bytes".format(label, limit)
    if "://" in rel_path:
        return "path looks like a URL"
    if rel_path.startswith("/"):
        return "absolute path"
    if "\\" in rel_path:
        return "backslash in path"
    for segment in rel_path.split("/"):
        if segment in ("", "."):
            return "empty path segment"
        if segment == "..":
            return "parent path segment"
    if not rel_path.lower().endswith((".iso", ".zso")) or len(rel_path) <= 4:
        return "path does not end in .iso or .zso"
    if len(riptopl_encoded_path(rel_path)) + RIPTOPL_BASE_PATH_MAX >= RIPTOPL_URI_MAX:
        return "path too long once URL-encoded"
    return None


class GameEntry(object):
    """One row of games.csv and the host file behind it."""

    __slots__ = ("startup", "advertised", "path", "media", "size",
                 "compressed", "conventional", "rel_path", "title")

    def __init__(self, startup, advertised, path, media, size, compressed,
                 conventional, rel_path=None, title=None):
        self.startup = startup
        self.advertised = advertised   # the name in games.csv and in the URL
        self.path = path               # the real file on disk
        self.media = media
        self.size = size               # uncompressed size, i.e. what we serve
        self.compressed = compressed   # decompressed by us, not by the console
        self.conventional = conventional
        # Path-aware profile only: the advertised path relative to the games
        # folder ("DVD/Game.iso", a CHD renamed to .iso) and the display title.
        self.rel_path = rel_path if rel_path is not None else advertised
        self.title = title if title is not None else advertised

    def csv_line(self):
        return "{},{},{}".format(self.startup, self.advertised, self.media)

    def riptopl_csv_line(self):
        return "{},{},{},{}".format(_csv_quote(self.startup), _csv_quote(self.title),
                                    self.media, _csv_quote(self.rel_path))


class GameIndex(object):
    """Flat filename -> file map over an OPL folder, plus its games.csv.

    Flat because the in-game driver requests every ISO from the web root while
    the games we serve live in DVD/ and CD/. Building an explicit whitelist
    also means a URL is never translated into a path, so there is no traversal
    surface and no accidental directory listing of the user's whole library --
    both of which the reference server has.
    """

    #: Subdirectories scanned in addition to the root, and the media each
    #: implies. OPL creates these itself on first run.
    SCAN_DIRS = ((".", None), ("DVD", MEDIA_DVD), ("CD", MEDIA_CD))

    #: Seconds between directory rescans. Long enough that streaming a game
    #: never pays for one, short enough that a game copied in while the user is
    #: still browsing shows up without a restart.
    RESCAN_INTERVAL = 5.0

    def __init__(self, root_dir, enable_compression=True,
                 profile=PROFILE_LEGACY_FLAT, recursive=False):
        if profile not in PROFILES:
            raise ValueError("unknown catalogue profile: {}".format(profile))
        if recursive and profile != PROFILE_RIPTOPL_PATH_AWARE:
            # A flat client requests bare filenames, so nested games it cannot
            # tell apart would list and then launch the wrong disc or none.
            raise ValueError("recursive scanning needs the {} profile"
                             .format(PROFILE_RIPTOPL_PATH_AWARE))
        self.root_dir = os.path.realpath(root_dir)
        self.enable_compression = enable_compression and COMPRESSION_AVAILABLE
        self.profile = profile
        self.path_aware = profile == PROFILE_RIPTOPL_PATH_AWARE
        self.recursive = recursive
        self._lock = threading.Lock()
        self._entries = {}
        self._path_entries = {}
        self._exact_paths = {}
        self._ambiguous_paths = set()
        self._ambiguous_names = set()
        self._csv = b""
        self._signature = None
        self._last_scan = 0.0
        self.refresh(force=True)

    # -- scanning ---------------------------------------------------------- #

    def _decompressible(self):
        """Extensions we decompress into a virtual .iso, in probe order.

        Derived from the UDPFS table so an alias added there is picked up here
        too. ZSO spellings are excluded deliberately: the console decodes those
        itself, so passing them through raw saves both bandwidth and our CPU.
        """
        if not self.enable_compression:
            return ()
        if self.path_aware:
            # RiptOPL lists a .zso but refuses to launch it (httpsupport.c
            # streams raw sectors only), so decode it here when lz4 is present.
            return tuple(_COMPRESSED_EXTENSIONS)
        return tuple(ext for ext in _COMPRESSED_EXTENSIONS
                     if ext not in (".zso", ".ziso"))

    def _scan_dirs(self):
        """(absolute directory, implied media) for each directory scanned.

        The DVD/CD lookup is case-insensitive because the folders are created
        by OPL on one platform and read by us on another.
        """
        if self.recursive:
            return self._walk_dirs()
        try:
            listing = os.listdir(self.root_dir)
        except OSError:
            listing = []
        by_lower = {name.lower(): name for name in listing}
        result = []
        for name, media in self.SCAN_DIRS:
            if name == ".":
                result.append((self.root_dir, None))
                continue
            actual = by_lower.get(name.lower())
            if not actual:
                continue
            path = os.path.join(self.root_dir, actual)
            if os.path.isdir(path):
                result.append((path, media))
        return result

    def _walk_dirs(self):
        """Every directory up to RECURSIVE_MAX_DEPTH below the root, bounded.

        Symlinked and junctioned directories are not entered, so a link cannot
        pull a tree outside the games folder into the catalogue or form a
        cycle. Media comes from a top-level CD/ or DVD/ ancestor only; anything
        else falls back to the size heuristic, exactly as a root image does.
        """
        result = [(self.root_dir, None)]
        pending = [(self.root_dir, None, 0)]
        while pending and len(result) < RECURSIVE_MAX_DIRS:
            directory, media, depth = pending.pop(0)
            if depth >= RECURSIVE_MAX_DEPTH:
                continue
            try:
                with os.scandir(directory) as it:
                    children = sorted((e for e in it if self._is_real_dir(e)),
                                      key=lambda e: e.name)
            except OSError as exc:
                _log("cannot read {}: {}".format(directory, exc))
                continue
            for child in children:
                if len(result) >= RECURSIVE_MAX_DIRS:
                    _log("WARNING: stopped after {} folders; deeper games are not listed"
                         .format(RECURSIVE_MAX_DIRS))
                    break
                implied = media
                if depth == 0 and child.name.upper() in (MEDIA_CD, MEDIA_DVD):
                    implied = child.name.upper()
                result.append((child.path, implied))
                pending.append((child.path, implied, depth + 1))
        return result

    @staticmethod
    def _is_real_dir(entry):
        try:
            if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                return False
            # A Windows junction is not a symlink to os.scandir.
            is_junction = getattr(entry, "is_junction", None)
            return not (is_junction and is_junction())
        except OSError:
            return False

    def _contained(self, path):
        """The real file is inside the games folder (no link escapes it)."""
        real = os.path.realpath(path)
        return real == self.root_dir or real.startswith(self.root_dir.rstrip(os.sep) + os.sep)

    def _disk_csv_path(self):
        p = os.path.join(self.root_dir, "games.csv")
        return p if os.path.isfile(p) else None

    def _signature_of(self, scan_dirs):
        """Change token covering each scanned file's identity, size and mtime.

        Directory mtime and entry count are NOT enough. Replacing a game's
        contents in place -- re-dumping an ISO under the same name, which people
        do -- changes neither on Windows, so the index kept serving the old size
        forever: Content-Range advertised a total that no longer existed, and
        every read past it came back 416 with the game simply refusing to load.

        scandir carries the stat data from the directory read, so this costs
        little, and it only runs on a games.csv fetch (throttled) -- never on the
        read path.
        """
        parts = []
        for path, _media in scan_dirs:
            entries = []
            try:
                with os.scandir(path) as it:
                    for entry in it:
                        try:
                            st = entry.stat()
                        except OSError:
                            continue
                        entries.append((entry.name, st.st_mtime_ns, st.st_size))
            except OSError:
                continue
            parts.append((path, tuple(sorted(entries))))
        disk_csv = self._disk_csv_path()
        if disk_csv:
            try:
                st = os.stat(disk_csv)
                parts.append(("__disk_games_csv__", st.st_mtime_ns, st.st_size))
            except OSError:
                pass
        return tuple(parts)

    def _media_for(self, implied, size):
        if implied:
            return implied
        return MEDIA_CD if size <= CD_SIZE_LIMIT else MEDIA_DVD

    def _entry_for(self, directory, name, implied_media, decompressible):
        """A GameEntry for one directory item, or None if it is not a game."""
        path = os.path.join(directory, name)
        stem, ext = os.path.splitext(name)
        lower = ext.lower()

        if lower in decompressible:
            advertised = stem + ".iso"
            # The uncompressed size is what Content-Range must describe, and
            # what decides CD vs DVD. A header we cannot parse means we cannot
            # serve the file correctly, so skip it rather than advertise a
            # game that fails at boot.
            info = _get_compressed_info(path)
            if not info:
                _log("  skipped (unreadable {} header): {}".format(lower, name))
                return None
            size = info[0]
            compressed = True
        elif lower in RAW_EXTENSIONS:
            advertised = stem + _ADVERTISED_RAW_EXTENSION[lower]
            try:
                size = os.path.getsize(path)
            except OSError:
                return None
            compressed = False
        else:
            return None

        if not os.path.isfile(path):
            return None

        if self.path_aware:
            return self._path_aware_entry(directory, name, stem, advertised, path,
                                          implied_media, size, compressed)

        if len(advertised) > FILENAME_MAX:
            _log("  skipped (name over {} chars, the console truncates it): "
                 "{}".format(FILENAME_MAX, name))
            return None

        # games.csv has no quoting or escaping -- OPL splits on commas and
        # newlines full stop. A comma would cut the filename field short, so the
        # console would request a name the index does not hold and get a 404 at
        # boot with nothing to explain it; a newline (legal on Linux) would
        # inject a whole extra row. Neither can be fixed server-side without
        # renaming the user's file, so say which file and why.
        bad = [label for ch, label in ((",", "comma"),
                                       (chr(10), "newline"),
                                       (chr(13), "carriage return"))
               if ch in advertised]
        if bad:
            _log("  skipped ({} in name -- games.csv has no escaping, so the "
                 "console would look for the wrong file; rename it): {}"
                 .format(" and ".join(bad), name))
            return None

        startup, _title = parse_startup(stem)
        conventional = bool(_STARTUP_RE.match(stem))
        return GameEntry(startup, advertised, path,
                         self._media_for(implied_media, size), size,
                         compressed, conventional)

    def _path_aware_entry(self, directory, name, stem, advertised, path,
                          implied_media, size, compressed):
        """A GameEntry addressed by its real relative path, for RiptOPL.

        Row validity (lengths, control bytes, blank edges) is checked when the
        catalogue is built, so every refusal is reported in one place.
        """
        if not self._contained(path):
            _log("  skipped (links outside the games folder): {}".format(name))
            return None
        rel_dir = os.path.relpath(directory, self.root_dir).replace(os.sep, "/")
        rel_path = advertised if rel_dir == "." else rel_dir + "/" + advertised
        try:
            rel_path.encode("utf-8")
        except UnicodeEncodeError:
            # An undecodable POSIX name arrives as lone surrogates; RiptOPL
            # could not request it, and encoding it later would abort the scan.
            _log("  skipped (name is not valid UTF-8; rename it): {!a}".format(
                os.path.relpath(path, self.root_dir).replace(os.sep, "/")))
            return None
        startup, title = parse_startup(stem)
        conventional = bool(_STARTUP_RE.match(stem))
        if not conventional:
            startup = _utf8_prefix(stem.strip(" \t"), RIPTOPL_STARTUP_MAX).strip(" \t")
        title = _utf8_prefix(title.strip(" \t"), RIPTOPL_TITLE_MAX).strip(" \t")
        return GameEntry(startup, advertised, path,
                         self._media_for(implied_media, size), size,
                         compressed, conventional, rel_path=rel_path, title=title)

    def refresh(self, force=False):
        """Rebuild the index if the scanned directories changed."""
        scan_dirs = self._scan_dirs()
        signature = self._signature_of(scan_dirs)
        with self._lock:
            if not force and signature == self._signature:
                return
            if self.path_aware:
                report = self._rebuild_path_aware(scan_dirs, signature)
            else:
                report = self._rebuild_flat(scan_dirs, signature)
        self._report(*report)

    def _rebuild_flat(self, scan_dirs, signature):
        """The legacy flat index, unchanged. Caller holds the lock."""
        entries = {}
        path_entries = {}
        seen_names = {}
        ambiguous_names = set()
        unconventional = []
        for directory, implied_media in scan_dirs:
            try:
                names = sorted(os.listdir(directory))
            except OSError as exc:
                _log("cannot read {}: {}".format(directory, exc))
                continue
            rel_dir = os.path.relpath(directory, self.root_dir).replace("\\", "/")
            for name in names:
                entry = self._entry_for(directory, name, implied_media,
                                        self._decompressible())
                if entry is None:
                    continue
                if rel_dir != ".":
                    rel_path_key = posixpath.normpath(posixpath.join(rel_dir, name)).lstrip("/").lower()
                    path_entries[rel_path_key] = entry

                key = entry.advertised.lower()
                existing = seen_names.get(key)
                if existing is not None:
                    ambiguous_names.add(key)
                    _log("  duplicate name '{}': serving {}, ignoring {}"
                         .format(entry.advertised, existing.path,
                                 entry.path))
                    continue
                seen_names[key] = entry
                entries[key] = entry
                if not entry.conventional:
                    unconventional.append(entry.advertised)

        self._entries = entries
        self._path_entries = path_entries
        self._ambiguous_names = ambiguous_names
        disk_csv = self._disk_csv_path()
        if disk_csv:
            try:
                with open(disk_csv, "rb") as f:
                    disk_data = f.read()
                if len(disk_data) > GAMES_CSV_MAX:
                    _log("WARNING: disk-resident games.csv exceeds {} bytes ({} bytes); "
                         "falling back to auto-generated catalog"
                         .format(GAMES_CSV_MAX, len(disk_data)))
                    self._csv, dropped = self._build_csv(entries)
                else:
                    self._csv = disk_data
                    dropped = 0
                    _log("Using disk-resident games.csv from {}".format(disk_csv))
                self._signature = signature
            except OSError as exc:
                self._signature = None
                _log("Error reading disk games.csv: {}, falling back to auto-generation"
                     .format(exc))
                self._csv, dropped = self._build_csv(entries)
        else:
            self._csv, dropped = self._build_csv(entries)
            self._signature = signature
        return entries, unconventional, dropped, "{} bytes".format(GAMES_CSV_MAX)

    def _rebuild_path_aware(self, scan_dirs, signature):
        """Index every game by its own relative path. Caller holds the lock.

        Nothing is flattened, so two games with the same filename in different
        folders both list and both launch. Matching is exact first; a
        case-insensitive match is used only when it names one file, because a
        case-sensitive filesystem can hold Game.iso and GAME.iso side by side.
        """
        found = []
        for directory, implied_media in scan_dirs:
            try:
                names = sorted(os.listdir(directory))
            except OSError as exc:
                _log("cannot read {}: {}".format(directory, exc))
                continue
            for name in names:
                entry = self._entry_for(directory, name, implied_media,
                                        self._decompressible())
                if entry is not None:
                    found.append(entry)

        candidates = {}
        for entry in found:
            if entry.rel_path in candidates:
                # Game.chd and Game.iso side by side both advertise Game.iso.
                _log("  duplicate path '{}': serving {}, ignoring {}"
                     .format(entry.rel_path, candidates[entry.rel_path].path, entry.path))
                continue
            candidates[entry.rel_path] = entry

        disk_csv = self._disk_csv_path()
        if disk_csv:
            # A games.csv on disk is almost always a legacy three-field list,
            # which RiptOPL would reject row by row; this profile always
            # generates its own.
            _log("Ignoring {} in the RiptOPL path-aware profile; the list is generated."
                 .format(disk_csv))
        retained = []
        self._csv, dropped = self._build_riptopl_csv(candidates.values(), retained)

        # Only what the list advertises is served: a row the list left out
        # (invalid, a repeated STARTUP, past the caps) answers 404.
        exact = {}
        folded = {}
        ambiguous = set()
        for entry in retained:
            exact[entry.rel_path] = entry
            key = entry.rel_path.lower()
            if key in folded:
                ambiguous.add(key)
            else:
                folded[key] = entry
        for key in ambiguous:
            folded.pop(key, None)

        self._entries = {}
        self._exact_paths = exact
        self._path_entries = folded
        self._ambiguous_paths = ambiguous
        self._ambiguous_names = set()
        unconventional = [e.rel_path for e in retained if not e.conventional]
        self._signature = signature
        return exact, unconventional, dropped, "{} bytes and {} rows".format(
            RIPTOPL_CATALOG_BYTES_MAX, RIPTOPL_CATALOG_ROWS_MAX)

    def _build_riptopl_csv(self, entries, retained=None):
        """(bytes, dropped_count) for a four-field RiptOPL games.csv.

        Every row is checked against the client's own parser rules first, so a
        refusal names the file here instead of becoming a silently missing row
        on the console. The client keeps the first row for a STARTUP and drops
        the rest, so later duplicates are reported and left out. Its row and
        byte limits fail the whole list when exceeded, so rows past them are
        dropped and counted.
        """
        body = bytearray()
        dropped = 0
        rows = 0
        startups = {}
        for entry in sorted(entries, key=lambda e: (e.rel_path.lower(), e.rel_path)):
            problem = riptopl_row_problem(entry.startup, entry.title, entry.rel_path)
            if problem:
                _log("  skipped ({}): {}".format(problem, entry.path))
                continue
            first = startups.get(entry.startup)
            if first is not None:
                _log("  skipped (STARTUP {} is already used by {}; the console keeps "
                     "only the first row for an id): {}".format(entry.startup, first, entry.rel_path))
                continue
            line = (entry.riptopl_csv_line() + "\n").encode("utf-8")
            if rows >= RIPTOPL_CATALOG_ROWS_MAX or len(body) + len(line) > RIPTOPL_CATALOG_BYTES_MAX:
                dropped += 1
                continue
            startups[entry.startup] = entry.rel_path
            body += line
            rows += 1
            if retained is not None:
                retained.append(entry)
        return bytes(body), dropped

    def _report(self, entries, unconventional, dropped, cap="8192 bytes"):
        _log("Indexed {} game(s) from {}".format(len(entries), self.root_dir))
        if unconventional:
            _log("{} file(s) do not follow OPL's SLUS_201.74.Title.iso naming, "
                 "so their STARTUP id is a guess -- art, per-game settings and "
                 "cheats key off that id:".format(len(unconventional)))
            for name in unconventional[:10]:
                _log("  {}".format(name))
            if len(unconventional) > 10:
                _log("  ... and {} more".format(len(unconventional) - 10))
        if dropped:
            _log("WARNING: games.csv is capped at {} by the console, so "
                 "{} game(s) were left out. Split the library or shorten "
                 "filenames.".format(cap, dropped))

    # -- games.csv --------------------------------------------------------- #

    def _build_csv(self, entries):
        """(bytes, dropped_count) for the generated games.csv.

        Truncation is done here, deliberately: the console clamps an oversized
        body to its 8192-byte buffer and cuts the final line mid-field, which
        produces one corrupt entry and no error. Dropping whole lines and
        saying so is the honest failure.
        """
        body = bytearray()
        dropped = 0
        for entry in sorted(entries.values(), key=lambda e: e.advertised.lower()):
            line = (entry.csv_line() + "\n").encode("utf-8", "replace")
            if len(body) + len(line) > GAMES_CSV_MAX:
                dropped += 1
                continue
            body += line
        return bytes(body), dropped

    def _refresh_if_stale(self):
        """Rescan at most once per RESCAN_INTERVAL seconds.

        Scanning costs an os.stat and an os.listdir per watched directory. Doing
        that per request made this server measurably SLOWER than the plain
        SimpleHTTPRequestHandler it is meant to beat, because the console issues
        thousands of 8 KB reads and every one of them paid for a directory walk.
        """
        now = time.monotonic()
        if now - self._last_scan < self.RESCAN_INTERVAL:
            return
        self._last_scan = now
        self.refresh()

    def games_csv(self):
        # Only the list is worth a freshness check: it is fetched when the user
        # browses, which is exactly when a newly added game should show up.
        self._refresh_if_stale()
        with self._lock:
            return self._csv

    def lookup(self, name):
        """The GameEntry for a requested filename, or None.

        Deliberately does NOT rescan. This is the hot path -- one call per 8 KB
        of a game being streamed -- and a game appearing mid-stream is of no use
        to a console that is already reading one. New games are picked up by the
        next games.csv fetch.

        Case-insensitive: matches normalized relative path (e.g. 'DVD/Game.iso')
        first, then falls back to bare filename ('Game.iso') if unambiguous.
        """
        if self.path_aware:
            return self._lookup_path(name)
        clean = posixpath.normpath(name).lstrip("/").lower()
        with self._lock:
            entry = self._path_entries.get(clean)
            if entry is not None:
                return entry
            base = posixpath.basename(name).lower()
            if base in self._ambiguous_names:
                return None
            return self._entries.get(base)

    def _lookup_path(self, name):
        """Path-aware lookup: <base>/<relative path>, never a bare filename.

        RiptOPL requests its configured base path followed by the catalogue
        path, and the base is not visible to the server, so leading segments
        are dropped one at a time until a catalogue path matches. There is no
        basename fallback: a four-field row's path is authoritative, and
        guessing from a filename is how a missing image becomes a different
        disc.
        """
        parts = posixpath.normpath(name).lstrip("/").split("/")
        with self._lock:
            for start in range(len(parts)):
                candidate = "/".join(parts[start:])
                entry = self._exact_paths.get(candidate)
                if entry is not None:
                    return entry
                key = candidate.lower()
                if key in self._ambiguous_paths:
                    return None
                entry = self._path_entries.get(key)
                if entry is not None:
                    return entry
        return None


class _RangeError(Exception):
    """Requested range cannot be satisfied; answer 416."""


class Ps2HTTPRequestHandler(BaseHTTPRequestHandler):
    """Range handler written against the console's expectations, not the RFC.

    Keep-alive is the point: the driver opens one socket and issues thousands
    of 8 KB reads down it. Everything per-request is therefore kept cheap --
    the open file handle is cached on the connection rather than reopened the
    way the reference server does, and no mimetypes or stat call happens on the
    hot path.
    """

    protocol_version = "HTTP/1.1"
    # No Server banner: it buys nothing and spends the 512-byte header budget.
    server_version = ""
    sys_version = ""

    # -- lifecycle --------------------------------------------------------- #

    def setup(self):
        BaseHTTPRequestHandler.setup(self)
        self._cached_path = None
        self._cached_handle = None
        try:
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass

    def finish(self):
        self._close_cached()
        try:
            BaseHTTPRequestHandler.finish(self)
        except (ConnectionResetError, BrokenPipeError, OSError):
            # The driver drops and reopens its socket on any read error, so a
            # reset here is routine rather than a fault worth a traceback.
            pass

    def _close_cached(self):
        if self._cached_handle is not None:
            try:
                self._cached_handle.close()
            except Exception:
                pass
        self._cached_path = None
        self._cached_handle = None

    def _handle_for(self, entry):
        """An open, seekable handle for this entry, cached per connection."""
        if self._cached_path == entry.path and self._cached_handle is not None:
            return self._cached_handle
        self._close_cached()
        if entry.compressed:
            handle = _open_compressed(entry.path)
            if handle is None:
                raise OSError("no decoder for {}".format(entry.path))
        else:
            handle = open(entry.path, "rb")
        self._cached_path = entry.path
        self._cached_handle = handle
        return handle

    def log_message(self, fmt, *args):
        if self.server.verbose:
            _log("[{}] {}".format(self.client_address[0], fmt % args))

    def log_error(self, fmt, *args):
        _log("[{}] {}".format(self.client_address[0], fmt % args))

    # -- routing ----------------------------------------------------------- #

    def do_GET(self):
        self._dispatch(body=True)

    def do_HEAD(self):
        self._dispatch(body=False)

    def _dispatch(self, body):
        try:
            path = self._normalised_path()
            if path is None:
                self._send_short(400, "Bad Request")
                return
            if self._is_games_csv(path):
                self._serve_games_csv(body)
                return
            entry = self.server.index.lookup(path)
            if entry is None:
                self.log_error("404 %s", path)
                self._send_short(404, "Not Found")
                return
            if self.server.verbose:
                # send_response_only() skips BaseHTTPRequestHandler.log_request,
                # so the request log has to be written here or --verbose is
                # silent on exactly the traffic worth watching.
                self.log_message("%s %s", self.command,
                                 self.headers.get("Range") or path)
            self._serve_game(entry, body)
        except (ConnectionResetError, BrokenPipeError):
            self.close_connection = True
        except _RangeError:
            self._send_short(416, "Requested Range Not Satisfiable")
        except OSError as exc:
            self.log_error("%s", exc)
            self._send_short(500, "Internal Server Error")

    def _normalised_path(self):
        """The requested name with its leading path stripped, or None.

        The driver percent-encodes only spaces and sends the rest raw, so a
        real name can arrive containing '#' or '%'. Unquoting is still correct
        for both, and because lookup goes through the index whitelist rather
        than the filesystem, a crafted path resolves to nothing rather than to
        a file outside the root.
        """
        raw = self.path.split("?", 1)[0].split("#", 1)[0]
        try:
            decoded = unquote(raw, errors="strict")
        except (UnicodeDecodeError, ValueError):
            return None
        if "\x00" in decoded:
            return None
        return posixpath.normpath(decoded).lstrip("/")

    def _is_games_csv(self, path):
        """games.csv is fetched from '/games.csv' or '/<share>/games.csv'.

        Which one depends on OPL's Share field, which we do not see, so both
        are answered -- and any share name is accepted rather than only the
        configured one, because a mismatch there is otherwise an empty game
        list with nothing on the wire to explain it.
        """
        return posixpath.basename(path).lower() == "games.csv"

    # -- responses --------------------------------------------------------- #

    def _send_short(self, code, message):
        payload = (message + "\n").encode("ascii", "replace")
        self.send_response_only(code, message)
        self.send_header("Date", formatdate(usegmt=True))
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def _serve_games_csv(self, body):
        payload = self.server.index.games_csv()
        # Content-Length always, never chunked: the client's chunked branch is
        # commented out, so a chunked list is an empty game list.
        self.send_response_only(200, "OK")
        self.send_header("Date", formatdate(usegmt=True))
        self.send_header("Content-Type", "text/csv")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if body:
            self.wfile.write(payload)

    def _parse_range(self, total):
        """(start, end) inclusive from the Range header, or None if absent.

        Out-of-range is reported rather than clamped. Clamping would return
        fewer bytes than the driver asked for, and since it reads a fixed count
        and ignores Content-Length, it would block on the missing tail -- a
        hung console instead of a failed read it can retry.
        """
        header = self.headers.get("Range")
        if not header:
            return None
        match = re.match(r"^bytes=(\d*)-(\d*)$", header.strip())
        if not match or not match.group(1):
            raise _RangeError(header)
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else total - 1
        if start > end or start >= total or end >= total:
            self.log_error("range %s outside 0-%d", header.strip(), total - 1)
            raise _RangeError(header)
        return start, end

    def _serve_game(self, entry, body):
        total = entry.size
        span = self._parse_range(total)

        if span is None:
            # No Range header. The console always sends one; this path exists
            # for curl and browser checks, and is a plain 200.
            self.send_response_only(200, "OK")
            self.send_header("Date", formatdate(usegmt=True))
            self.send_header("Content-Type", CONTENT_TYPE)
            self.send_header("Content-Length", str(total))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            if body:
                self._send_bytes(entry, 0, total)
            return

        start, end = span
        length = end - start + 1
        self.send_response_only(206, "Partial Content")
        self.send_header("Date", formatdate(usegmt=True))
        self.send_header("Content-Type", CONTENT_TYPE)
        self.send_header("Content-Range",
                         "bytes {}-{}/{}".format(start, end, total))
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        if body:
            self._send_bytes(entry, start, length)

    def _send_bytes(self, entry, offset, length):
        """Write exactly `length` bytes starting at `offset`.

        Exactly, because the driver reads a fixed count off the socket and
        ignores our framing headers: a short write leaves it blocking and a
        long one shifts every subsequent response on this keep-alive socket.
        """
        handle = self._handle_for(entry)

        if not entry.compressed:
            # sendfile keeps the payload out of user space entirely on Linux;
            # elsewhere Python falls back to a read/send loop that is still
            # cheaper than rebuilding the response per request.
            self.wfile.flush()
            try:
                sent = self.connection.sendfile(handle, offset, length)
            except (AttributeError, ValueError):
                # sendfile is unavailable or refused the arguments before
                # sending anything, so the manual path below is safe.
                sent = 0
            except OSError:
                # An OSError can surface AFTER a partial send, and the byte
                # count is lost with it -- CPython's _sendfile_use_send lets the
                # exception escape without returning total_sent. Resending from
                # the original offset would put those bytes on the wire twice
                # and desynchronise every later read, so the only safe move is
                # to drop the connection. The driver reconnects and retries,
                # which is recoverable; a duplicated range is not.
                self.close_connection = True
                return
            if sent == length:
                return
            # Short but not failed: finish by hand from where it stopped.
            offset += sent
            length -= sent

        handle.seek(offset)
        remaining = length
        while remaining > 0:
            chunk = handle.read(min(remaining, 256 * 1024))
            if not chunk:
                # The file shrank under us. Nothing valid can be sent now, so
                # drop the connection: the driver reconnects and retries, which
                # is recoverable, whereas a short body is not.
                self.close_connection = True
                raise OSError("short read on {} at {}".format(entry.path, offset))
            self.wfile.write(chunk)
            remaining -= len(chunk)
        self.wfile.flush()


class Ps2HTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, index, verbose=False):
        self.index = index
        self.verbose = verbose
        ThreadingHTTPServer.__init__(self, address, Ps2HTTPRequestHandler)

    def handle_error(self, request, client_address):
        """Connection resets are the console reconnecting, not a fault.

        The driver closes and reopens its socket after any failed read, and
        Windows surfaces that as ConnectionResetError. Letting the default
        handler print a traceback per reconnect buries real errors.
        """
        exc_type = sys.exc_info()[0]
        if exc_type in (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
            return
        ThreadingHTTPServer.handle_error(self, request, client_address)


def _port_arg(value):
    try:
        port = int(str(value), 0)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("invalid port: {}".format(value))
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port out of range: {}".format(value))
    return port


def build_parser():
    parser = argparse.ArgumentParser(
        prog="ps2servers-http",
        description="Serve an OPL games folder over HTTP for Open PS2 Loader's "
                    "HTTP mode.")
    # A long flag rather than a positional: the packaged launcher re-executes
    # itself as `--serve http ...`, and Nuitka's self-exec guard only tolerates
    # long options on that path (see PR #75).
    parser.add_argument("--root-dir", dest="root", required=True,
                        help="Games folder (the one holding DVD/ and CD/)")
    parser.add_argument("--port", type=_port_arg, default=DEFAULT_PORT,
                        help="TCP port (default {})".format(DEFAULT_PORT))
    parser.add_argument("--bind", default="",
                        help="Interface to bind (blank = all)")
    parser.add_argument("--no-compression", action="store_true",
                        help="Do not decompress CHD/CSO into virtual .iso files")
    parser.add_argument("--catalog-profile", choices=PROFILES,
                        default=PROFILE_LEGACY_FLAT,
                        help="games.csv format: legacy-flat (default, Docmine17 "
                             "OPL-HTTP) or riptopl-path-aware (RiptOPL only)")
    parser.add_argument("--recursive", action="store_true",
                        help="riptopl-path-aware only: also list games up to {} "
                             "folders deep".format(RECURSIVE_MAX_DEPTH))
    parser.add_argument("--verbose", action="store_true",
                        help="Log every request")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)

    root = os.path.realpath(args.root)
    if not os.path.isdir(root):
        _log("Games folder not found: {}".format(root))
        return 1

    enable_compression = not args.no_compression
    if enable_compression and not COMPRESSION_AVAILABLE:
        _log("Compressed-image support unavailable; CHD/CSO will not be served.")

    if args.recursive and args.catalog_profile != PROFILE_RIPTOPL_PATH_AWARE:
        _log("--recursive needs --catalog-profile {}: a flat client requests "
             "bare filenames and cannot launch nested games."
             .format(PROFILE_RIPTOPL_PATH_AWARE))
        return 2

    index = GameIndex(root, enable_compression=enable_compression,
                      profile=args.catalog_profile, recursive=args.recursive)

    try:
        server = Ps2HTTPServer((args.bind, args.port), index,
                               verbose=args.verbose)
    except OSError as exc:
        _log("Cannot bind {}:{} -- {}".format(args.bind or "0.0.0.0",
                                              args.port, exc))
        return 1

    _log("HTTP server listening on {}:{}".format(args.bind or "0.0.0.0",
                                                 args.port))
    if index.path_aware:
        _log("Catalogue profile: {} (RiptOPL only; a Docmine17 OPL-HTTP build "
             "cannot read this games.csv).".format(args.catalog_profile))
        _log("In RiptOPL: Network -> Protocol HTTP, Port {}, base path / ."
             .format(args.port))
    else:
        _log("In OPL: Network -> Protocol HTTP, Port {}. Set the Port explicitly; "
             "OPL's game-list and in-game defaults disagree when it is left at 0."
             .format(args.port))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        _log("Stopping.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
