"""Read-only virtual CUE/BIN views for supported CHD v5 CD images.

The track-layout behavior is informed by Okeanos86's UDPFS/Neutrino prototype
at https://github.com/Okeanos86/neutrino/tree/6aed059a289c000753f40b04ed1ded0f7e503830/pc.
This module uses the repository's existing libchdr bindings and does not copy
that prototype's filesystem integration.
"""

import bisect
import ctypes
import io
import os
import stat
import struct
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from . import chd as _chd


CD_FRAME_BYTES = 2448       # 2352 sector bytes and 96 subchannel bytes
CD_SECTOR_BYTES = 2352      # raw sector bytes exposed by the virtual BIN
HEADER_BYTES = 124
MAX_METADATA_ENTRIES = 4096
MAX_TRACK_METADATA_BYTES = 4096
MAX_TRACKS = 99
MAX_HUNK_BYTES = 16 * 1024 * 1024
MAX_CACHE_BYTES = 4 * 1024 * 1024
DEFAULT_CACHE_BLOCKS = 32
_PADDING_FRAMES = 4
_CD_TRACK_TAGS = {b"CHT2", b"CHTR"}
_UNSUPPORTED_CD_TAGS = {b"CHCD", b"CHGD", b"CHGT"}
_RAW_TRACK_TYPES = {"MODE1_RAW", "MODE2_RAW", "AUDIO"}
_AVAILABLE = _chd.LIBCHDR_AVAILABLE


class ChdCueError(ValueError):
    """The CHD cannot be represented safely as a CUE/BIN pair."""


class NotCdImage(ChdCueError):
    """The CHD contains no supported CD track table."""


@dataclass(frozen=True)
class Track:
    number: int
    kind: str
    frames: int
    pregap: int = 0
    pregap_kind: str = ""
    postgap: int = 0

    @property
    def audio(self) -> bool:
        return self.kind == "AUDIO"

    @property
    def stored_pregap(self) -> bool:
        return self.pregap_kind.startswith("V")

    @property
    def cue_mode(self) -> str:
        return {
            "MODE1_RAW": "MODE1/2352",
            "MODE2_RAW": "MODE2/2352",
            "AUDIO": "AUDIO",
        }[self.kind]


@dataclass(frozen=True)
class TrackTable:
    hunk_bytes: int
    logical_bytes: int
    tracks: Tuple[Track, ...]

    def layout(self) -> "Layout":
        if self.logical_bytes <= 0 or self.logical_bytes % CD_FRAME_BYTES != 0:
            raise ChdCueError("invalid CD logical size")
        spans: List[Span] = []
        indexes: List[TrackIndex] = []
        virtual_frame = 0
        physical_frame = 0
        total_frames = self.logical_bytes // CD_FRAME_BYTES

        for track in self.tracks:
            if track.kind not in _RAW_TRACK_TYPES:
                raise ChdCueError(
                    f"track {track.number} uses unsupported type {track.kind!r}")
            if track.frames <= 0 or track.pregap < 0 or track.postgap < 0:
                raise ChdCueError(f"track {track.number} has invalid frame counts")
            if track.pregap > track.frames:
                raise ChdCueError(
                    f"track {track.number} pregap is longer than the track")
            if track.pregap_kind and not track.pregap_kind.isascii():
                raise ChdCueError(f"track {track.number} has invalid pregap metadata")

            if track.pregap and track.stored_pregap:
                if track.pregap >= track.frames:
                    raise ChdCueError(
                        f"track {track.number} stored pregap consumes the track")
                index0 = virtual_frame
                index1 = virtual_frame + track.pregap
            elif track.pregap:
                spans.append(Span(virtual_frame, track.pregap, -1, track.audio))
                index0 = virtual_frame
                virtual_frame += track.pregap
                index1 = virtual_frame
            else:
                index0 = -1
                index1 = virtual_frame

            if physical_frame + track.frames > total_frames:
                raise ChdCueError(
                    f"track {track.number} extends beyond the CHD frame range")
            spans.append(Span(virtual_frame, track.frames, physical_frame, track.audio))
            indexes.append(TrackIndex(track, index0, index1))
            virtual_frame += track.frames
            physical_frame += track.frames + (-track.frames) % _PADDING_FRAMES

            # A non-stored postgap is materialized as silence in the raw BIN.
            # The next track's INDEX 00/01 locations therefore include it.
            if track.postgap:
                spans.append(Span(virtual_frame, track.postgap, -1, track.audio))
                virtual_frame += track.postgap

        if virtual_frame * CD_SECTOR_BYTES > (1 << 63) - 1:
            raise ChdCueError("virtual BIN is too large")
        return Layout(tuple(spans), tuple(indexes), virtual_frame)


@dataclass(frozen=True)
class Span:
    start: int
    frames: int
    physical_start: int
    audio: bool


@dataclass(frozen=True)
class TrackIndex:
    track: Track
    index0: int
    index1: int


@dataclass(frozen=True)
class Layout:
    spans: Tuple[Span, ...]
    tracks: Tuple[TrackIndex, ...]
    total_frames: int
    _starts: Tuple[int, ...] = field(init=False, repr=False)

    def __post_init__(self):
        object.__setattr__(self, "_starts", tuple(span.start for span in self.spans))

    @property
    def size(self) -> int:
        return self.total_frames * CD_SECTOR_BYTES

    def span_at(self, frame: int) -> Optional[Span]:
        i = bisect.bisect_right(self._starts, frame) - 1
        if i < 0:
            return None
        span = self.spans[i]
        return span if frame < span.start + span.frames else None

    def cue_sheet(self, bin_name: str) -> bytes:
        lines = [f'FILE "{bin_name}" BINARY\r\n']
        for item in self.tracks:
            lines.append(f"  TRACK {item.track.number:02d} {item.track.cue_mode}\r\n")
            if item.index0 >= 0:
                lines.append(f"    INDEX 00 {_msf(item.index0)}\r\n")
            lines.append(f"    INDEX 01 {_msf(item.index1)}\r\n")
        return "".join(lines).encode("utf-8")


def _msf(frame: int) -> str:
    minutes, remainder = divmod(frame, 75 * 60)
    seconds, frames = divmod(remainder, 75)
    return f"{minutes:02d}:{seconds:02d}:{frames:02d}"


def _number(fields: dict, name: str, default: Optional[int] = None) -> int:
    value = fields.get(name)
    if value is None:
        if default is not None:
            return default
        raise ChdCueError(f"missing {name} in CD track metadata")
    try:
        result = int(value, 10)
    except (TypeError, ValueError) as exc:
        raise ChdCueError(f"invalid {name} in CD track metadata") from exc
    if result < 0:
        raise ChdCueError(f"negative {name} in CD track metadata")
    return result


def _parse_track(raw: bytes) -> Track:
    try:
        text = raw.rstrip(b"\0").decode("ascii")
    except UnicodeDecodeError as exc:
        raise ChdCueError("CD track metadata is not ASCII") from exc
    fields = {}
    for token in text.split():
        key, separator, value = token.partition(":")
        if separator:
            fields[key] = value
    number = _number(fields, "TRACK")
    frames = _number(fields, "FRAMES")
    pregap = _number(fields, "PREGAP", 0)
    postgap = _number(fields, "POSTGAP", 0)
    kind = fields.get("TYPE", "")
    pregap_kind = fields.get("PGTYPE", "")
    if not 1 <= number <= MAX_TRACKS or frames <= 0 or not kind:
        raise ChdCueError("invalid CD track number, type or frame count")
    return Track(number, kind, frames, pregap, pregap_kind, postgap)


def read_track_table(path: str) -> TrackTable:
    """Read and validate the bounded CD metadata chain from a CHD v5 file."""
    try:
        st = os.stat(path, follow_symlinks=False)
        if not stat.S_ISREG(st.st_mode):
            raise ChdCueError("CHD source is not a regular file")
        with open(path, "rb") as image:
            file_size = os.fstat(image.fileno()).st_size
            header = image.read(HEADER_BYTES)
            if len(header) != HEADER_BYTES or header[:8] != b"MComprHD":
                raise ChdCueError("not a complete CHD v5 header")
            header_length, version = struct.unpack(">II", header[8:16])
            if header_length != HEADER_BYTES or version != 5:
                raise ChdCueError("only CHD v5 headers are supported")

            logical_bytes = struct.unpack(">Q", header[32:40])[0]
            metadata_offset = struct.unpack(">Q", header[48:56])[0]
            hunk_bytes, unit_bytes = struct.unpack(">II", header[56:64])
            if (not logical_bytes or logical_bytes > (1 << 63) - 1 or
                    logical_bytes % CD_FRAME_BYTES):
                raise ChdCueError("invalid CD logical size")
            if (unit_bytes != CD_FRAME_BYTES or hunk_bytes == 0 or
                    hunk_bytes > MAX_HUNK_BYTES or hunk_bytes % CD_FRAME_BYTES):
                raise ChdCueError("unsupported CHD CD hunk geometry")
            hunk_count = (logical_bytes + hunk_bytes - 1) // hunk_bytes
            if hunk_count > (1 << 32) - 1:
                raise ChdCueError("CHD has too many hunks")
            if metadata_offset and metadata_offset < header_length:
                raise ChdCueError("CD metadata overlaps the CHD header")

            tracks: List[Track] = []
            visited = set()
            offset = metadata_offset
            while offset:
                if (len(visited) >= MAX_METADATA_ENTRIES or offset in visited or
                        offset < header_length or offset > file_size - 16):
                    raise ChdCueError("invalid or cyclic CHD metadata chain")
                visited.add(offset)
                image.seek(offset)
                meta_header = image.read(16)
                if len(meta_header) != 16:
                    raise ChdCueError("truncated CHD metadata header")
                tag = meta_header[:4]
                meta_length = int.from_bytes(meta_header[5:8], "big")
                next_offset = struct.unpack(">Q", meta_header[8:16])[0]
                if next_offset and (next_offset < header_length or next_offset > file_size - 16):
                    raise ChdCueError("CHD metadata points outside the file")
                if tag in _UNSUPPORTED_CD_TAGS:
                    raise ChdCueError(f"unsupported CD metadata tag {tag!r}")
                if tag in _CD_TRACK_TAGS:
                    if not 1 <= meta_length <= MAX_TRACK_METADATA_BYTES:
                        raise ChdCueError("invalid CD track metadata length")
                    if offset + 16 + meta_length > file_size:
                        raise ChdCueError("CD track metadata extends beyond the file")
                    data = image.read(meta_length)
                    if len(data) != meta_length:
                        raise ChdCueError("truncated CD track metadata")
                    tracks.append(_parse_track(data))
                    if len(tracks) > MAX_TRACKS:
                        raise ChdCueError("too many CD tracks")
                offset = next_offset
    except OSError as exc:
        raise ChdCueError(str(exc)) from exc

    if not tracks:
        raise NotCdImage("CHD has no supported CD track table")
    tracks.sort(key=lambda item: item.number)
    if any(track.number != i + 1 for i, track in enumerate(tracks)):
        raise ChdCueError("CD track numbers must be contiguous starting at 1")
    table = TrackTable(hunk_bytes, logical_bytes, tuple(tracks))
    table.layout()
    return table


class MemoryFile:
    """A seekable read-only in-memory file for generated CUE text."""

    def __init__(self, data: bytes):
        self._stream = io.BytesIO(data)
        self.uncompressed_size = len(data)

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence not in (0, 1, 2):
            raise ValueError("invalid whence")
        return self._stream.seek(offset, whence)

    def tell(self) -> int:
        return self._stream.tell()

    def close(self):
        self._stream.close()


class ChdBinFile:
    """Read-only raw 2352-byte sector stream over a CD CHD."""

    def __init__(self, path: str, table: TrackTable, layout: Layout,
                 cache_size: Optional[int] = None):
        if not _chd.LIBCHDR_AVAILABLE:
            raise ImportError(_chd._libchdr_error)
        st = os.stat(path, follow_symlinks=False)
        if not stat.S_ISREG(st.st_mode):
            raise ChdCueError("CHD source is not a regular file")

        self._path = path
        self._layout = layout
        self.uncompressed_size = layout.size
        self._frames_per_hunk = table.hunk_bytes // CD_FRAME_BYTES
        self._hunk_bytes = table.hunk_bytes
        self._num_hunks = (table.logical_bytes + table.hunk_bytes - 1) // table.hunk_bytes
        self._cache_size = (
            DEFAULT_CACHE_BLOCKS if cache_size is None else max(0, int(cache_size))
        )
        self._lib = _chd._lib
        self._buffer = ctypes.create_string_buffer(self._hunk_bytes)
        self._cache: OrderedDict[int, bytes] = OrderedDict()
        self._cache_bytes = 0
        self._pos = 0
        self._handle = None

        handle = self._lib._chd_file_p()
        error = self._lib.chd_open(os.fsencode(path), _chd.CHD_OPEN_READ,
                                   None, ctypes.byref(handle))
        if error != 0:
            self._buffer = None
            raise OSError(f"chd_open failed with error {error}")
        self._handle = handle

    def _hunk(self, index: int) -> bytes:
        if not 0 <= index < self._num_hunks:
            raise OSError("CHD hunk index is outside the image")
        if self._cache_size > 0:
            cached = self._cache.get(index)
            if cached is not None:
                self._cache.move_to_end(index)
                return cached
        error = self._lib.chd_read(self._handle, index, self._buffer)
        if error != 0:
            raise OSError(f"chd_read failed for hunk {index} with error {error}")
        decoded = self._buffer.raw
        if len(decoded) != self._hunk_bytes:
            raise OSError("libchdr returned an invalid hunk length")
        if self._cache_size > 0 and len(decoded) <= MAX_CACHE_BYTES:
            while self._cache and (
                len(self._cache) >= self._cache_size or
                self._cache_bytes + len(decoded) > MAX_CACHE_BYTES
            ):
                _, evicted = self._cache.popitem(last=False)
                self._cache_bytes -= len(evicted)
            self._cache[index] = decoded
            self._cache_bytes += len(decoded)
        return decoded

    def read(self, size: int = -1) -> bytes:
        if self._handle is None:
            raise ValueError("I/O operation on closed CHD BIN")
        remaining = self.uncompressed_size - self._pos
        if size is None or size < 0:
            size = remaining
        else:
            size = min(size, remaining)
        if size <= 0:
            return b""

        output = bytearray()
        position = self._pos
        while len(output) < size:
            virtual_frame, sector_offset = divmod(position, CD_SECTOR_BYTES)
            span = self._layout.span_at(virtual_frame)
            if span is None:
                raise OSError("virtual BIN track layout has a hole")
            amount = min(CD_SECTOR_BYTES - sector_offset, size - len(output))
            if span.physical_start < 0:
                output.extend(bytes(amount))
            else:
                physical_frame = span.physical_start + virtual_frame - span.start
                hunk_index, hunk_frame = divmod(physical_frame, self._frames_per_hunk)
                decoded = self._hunk(hunk_index)
                byte_offset = hunk_frame * CD_FRAME_BYTES
                sector = decoded[byte_offset:byte_offset + CD_SECTOR_BYTES]
                if len(sector) != CD_SECTOR_BYTES:
                    raise OSError("CHD sector is truncated")
                if span.audio:
                    sector = _swap_audio_bytes(sector)
                output.extend(sector[sector_offset:sector_offset + amount])
            position += amount
        self._pos = position
        return bytes(output)

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence == 0:
            position = offset
        elif whence == 1:
            position = self._pos + offset
        elif whence == 2:
            position = self.uncompressed_size + offset
        else:
            raise ValueError("invalid whence")
        if position < 0:
            raise ValueError("negative seek")
        self._pos = position
        return self._pos

    def tell(self) -> int:
        return self._pos

    def close(self):
        if self._handle is not None:
            self._lib.chd_close(self._handle)
            self._handle = None
        self._cache.clear()
        self._cache_bytes = 0
        self._buffer = None


def _swap_audio_bytes(sector: bytes) -> bytes:
    swapped = bytearray(len(sector))
    swapped[0::2] = sector[1::2]
    swapped[1::2] = sector[0::2]
    return bytes(swapped)


def _stem_key(stem: str) -> str:
    return stem.casefold() if os.name == "nt" else stem


def _name_key(name: str) -> Tuple[str, str]:
    stem, extension = os.path.splitext(name)
    return _stem_key(stem), extension.lower()


@dataclass(frozen=True)
class VirtualView:
    kind: str
    path: str
    chd_path: str
    table: TrackTable
    layout: Layout
    cue_data: bytes = b""

    @property
    def size(self) -> int:
        return len(self.cue_data) if self.kind == "cue" else self.layout.size

    def open(self, cache_size: Optional[int] = None):
        if self.kind == "cue":
            return MemoryFile(self.cue_data)
        return ChdBinFile(self.chd_path, self.table, self.layout, cache_size)


def _safe_cue_name(name: str) -> bool:
    try:
        if len(name.encode("utf-8")) > 255:
            return False
    except UnicodeEncodeError:
        return False
    return ("\"" not in name and "\\" not in name and
            all(ord(char) >= 32 and ord(char) != 127 for char in name))


def _virtual_views(chd_path: str, virtual_stem: str, directory: str,
                   table: TrackTable, layout: Layout) -> Tuple[VirtualView, VirtualView]:
    bin_name = virtual_stem + ".bin"
    cue_data = layout.cue_sheet(bin_name)
    return (
        VirtualView("cue", os.path.join(directory, virtual_stem + ".cue"),
                    chd_path, table, layout, cue_data),
        VirtualView("bin", os.path.join(directory, bin_name),
                    chd_path, table, layout),
    )


def list_virtual_entries(directory: str, entries) -> List[Tuple[str, VirtualView]]:
    """Build complete virtual pairs for a directory snapshot.

    If either alias name collides with a real entry, or multiple CHDs would
    generate the same pair, neither generated alias is advertised.
    """
    if not _AVAILABLE:
        return []
    real_keys = {_name_key(entry.name) for entry in entries}
    sources = {}
    ambiguous = set()
    for entry in entries:
        stem, extension = os.path.splitext(entry.name)
        if extension.lower() != ".chd":
            continue
        key = _stem_key(stem)
        if key in sources:
            ambiguous.add(key)
        else:
            sources[key] = entry

    virtual = []
    for key, entry in sources.items():
        if key in ambiguous:
            continue
        stem, _ = os.path.splitext(entry.name)
        cue_name, bin_name = stem + ".cue", stem + ".bin"
        if ((key, ".cue") in real_keys or (key, ".bin") in real_keys or
                not _safe_cue_name(cue_name) or not _safe_cue_name(bin_name)):
            continue
        try:
            table = read_track_table(entry.path)
            pair = _virtual_views(entry.path, stem, directory, table, table.layout())
        except (ChdCueError, OSError, UnicodeError, struct.error):
            continue
        virtual.extend(((cue_name, pair[0]), (bin_name, pair[1])))
    return virtual


def find_virtual_view(path: str) -> Optional[VirtualView]:
    """Resolve a missing .cue/.bin name to a complete, collision-free CHD pair."""
    if not _AVAILABLE:
        return None
    directory, basename = os.path.split(path)
    stem, extension = os.path.splitext(basename)
    kind = {".cue": "cue", ".bin": "bin"}.get(extension.lower())
    if kind is None or not directory or not _safe_cue_name(stem + ".bin"):
        return None
    try:
        with os.scandir(directory) as scan:
            entries = list(scan)
    except OSError:
        return None
    key = _stem_key(stem)
    real_keys = {_name_key(entry.name) for entry in entries}
    if (key, ".cue") in real_keys or (key, ".bin") in real_keys:
        return None
    candidates = [entry for entry in entries if _name_key(entry.name) == (key, ".chd")]
    if len(candidates) != 1:
        return None
    try:
        table = read_track_table(candidates[0].path)
        source_stem, _ = os.path.splitext(candidates[0].name)
        views = _virtual_views(candidates[0].path, source_stem, directory, table, table.layout())
    except (ChdCueError, OSError, UnicodeError, struct.error):
        return None
    return views[0] if kind == "cue" else views[1]
