"""Small anti-aliased status-dot images for the server tabs.

A ttk.Notebook tab is single-colour text, so a running/stopped state drawn as a
glyph (the old "* / o") inherits the tab's text colour -- there is no way to make
just the dot green. A per-tab *image* can carry its own colour, and Tk renders
PNG data with a real alpha channel, so we draw a crisp dot ourselves using only
the standard library: Pillow is not a dependency and is not bundled in releases.

dot_png_base64() returns a base64 PNG string suitable for tk.PhotoImage(data=...).
Sizes are tiny and results are cached, so calling it per rebuild is cheap.
"""

import base64
import struct
import zlib

_cache = {}


def _hex_rgb(color):
    if not isinstance(color, str):
        return tuple(color[:3])
    c = color.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))


def _png_rgba(size, pixels):
    """Encode a size x size RGBA buffer as a PNG (no filtering, one IDAT)."""
    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8-bit RGBA
    row = size * 4
    raw = bytearray()
    for y in range(size):
        raw.append(0)  # filter: none
        raw += pixels[y * row:(y + 1) * row]
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b""))


def dot_png_base64(diameter, color, filled=True, samples=3):
    """A base64 PNG of one status dot.

    filled=True -> a solid disc (running / online).
    filled=False -> a hollow ring (stopped / offline).
    Edges are anti-aliased by `samples`x`samples` supersampling; the alpha
    channel keeps the corners transparent so the dot sits cleanly on any tab
    background (selected or not).
    """
    d = max(6, int(diameter))
    key = (d, color, filled, samples)
    cached = _cache.get(key)
    if cached is not None:
        return cached

    r = d / 2.0
    ring = max(1.6, d * 0.22)
    cr, cg, cb = _hex_rgb(color)
    px = bytearray(d * d * 4)
    for y in range(d):
        for x in range(d):
            covered = 0
            for sy in range(samples):
                for sx in range(samples):
                    fx = x + (sx + 0.5) / samples
                    fy = y + (sy + 0.5) / samples
                    dist = ((fx - r) ** 2 + (fy - r) ** 2) ** 0.5
                    if filled:
                        covered += dist <= r - 0.6
                    else:
                        covered += (r - 0.6 - ring) <= dist <= (r - 0.6)
            i = (y * d + x) * 4
            px[i] = cr
            px[i + 1] = cg
            px[i + 2] = cb
            px[i + 3] = 255 * covered // (samples * samples)

    out = base64.b64encode(_png_rgba(d, bytes(px))).decode("ascii")
    _cache[key] = out
    return out
