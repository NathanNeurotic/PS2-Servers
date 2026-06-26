"""Application icon helpers for PS2 Servers.

The icon is generated with the standard library so the source tree does not need
binary assets just to give the Tk window, Windows taskbar, tray icon and packaged
EXE a recognizable identity.
"""

import os
import platform
import struct
import tempfile

APP_ID = "NathanNeurotic.PS2Servers.Launcher"
APP_NAME = "PS2 Servers"


_GLYPHS = {
    "P": (
        "11110",
        "10001",
        "10001",
        "11110",
        "10000",
        "10000",
        "10000",
    ),
    "S": (
        "01111",
        "10000",
        "10000",
        "01110",
        "00001",
        "00001",
        "11110",
    ),
    "2": (
        "11110",
        "00001",
        "00001",
        "01110",
        "10000",
        "10000",
        "11111",
    ),
}


def set_windows_app_id():
    """Give Windows a stable identity for taskbar grouping/icon lookup."""
    if platform.system() != "Windows":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass


def icon_dir():
    path = os.path.join(tempfile.gettempdir(), "PS2Servers")
    os.makedirs(path, exist_ok=True)
    return path


def ico_path():
    return write_ico(os.path.join(icon_dir(), "PS2Servers.ico"))


def write_ico(path):
    with open(path, "wb") as f:
        f.write(ico_bytes())
    return path


def apply_to_tk_root(root, tk_module):
    """Apply the generated app icon to a Tk root window."""
    set_windows_app_id()
    if platform.system() == "Windows":
        try:
            root.iconbitmap(ico_path())
        except Exception:
            pass
    try:
        photo = tk_photo(tk_module, 64)
        root.iconphoto(True, photo)
        root._ps2servers_iconphoto = photo  # keep the Tcl image alive
    except Exception:
        pass


def tk_photo(tk_module, size=64):
    image = tk_module.PhotoImage(width=size, height=size)
    pixels = _rgba_pixels(size)
    rows = []
    for y in range(size):
        row = []
        for x in range(size):
            r, g, b, a = pixels[y * size + x]
            if a < 16:
                row.append("#000000")
            else:
                row.append("#{0:02x}{1:02x}{2:02x}".format(r, g, b))
        rows.append("{" + " ".join(row) + "}")
    image.put(" ".join(rows), to=(0, 0, size, size))
    try:
        for y in range(size):
            for x in range(size):
                if pixels[y * size + x][3] < 16:
                    image.transparency_set(x, y, True)
    except Exception:
        pass
    return image


def ico_bytes():
    sizes = (16, 32, 48, 64)
    images = [_ico_image(s) for s in sizes]
    header = [struct.pack("<HHH", 0, 1, len(images))]
    offset = 6 + 16 * len(images)
    entries = []
    for size, data in zip(sizes, images):
        entries.append(struct.pack(
            "<BBBBHHII",
            size, size, 0, 0, 1, 32, len(data), offset))
        offset += len(data)
    return b"".join(header + entries + images)


def _ico_image(size):
    pixels = _rgba_pixels(size)
    pixel_rows = []
    for y in range(size - 1, -1, -1):  # DIB is bottom-up
        row = bytearray()
        for x in range(size):
            r, g, b, a = pixels[y * size + x]
            row.extend((b, g, r, a))
        pixel_rows.append(bytes(row))
    xor_bitmap = b"".join(pixel_rows)

    mask_stride = ((size + 31) // 32) * 4
    and_mask = bytearray(mask_stride * size)
    for y in range(size):
        src_y = size - 1 - y
        for x in range(size):
            if pixels[src_y * size + x][3] < 128:
                and_mask[y * mask_stride + (x // 8)] |= 0x80 >> (x % 8)

    header = struct.pack(
        "<IIIHHIIIIII",
        40,
        size,
        size * 2,
        1,
        32,
        0,
        len(xor_bitmap) + len(and_mask),
        0,
        0,
        0,
        0,
    )
    return header + xor_bitmap + bytes(and_mask)


def _rgba_pixels(size):
    pixels = [(0, 0, 0, 0)] * (size * size)

    def set_px(x, y, color):
        if 0 <= x < size and 0 <= y < size:
            pixels[y * size + x] = color

    def inside_round_rect(x, y, margin, radius):
        left = margin
        top = margin
        right = size - margin - 1
        bottom = size - margin - 1
        if left + radius <= x <= right - radius and top <= y <= bottom:
            return True
        if left <= x <= right and top + radius <= y <= bottom - radius:
            return True
        for cx, cy in (
            (left + radius, top + radius),
            (right - radius, top + radius),
            (left + radius, bottom - radius),
            (right - radius, bottom - radius),
        ):
            if (x - cx) * (x - cx) + (y - cy) * (y - cy) <= radius * radius:
                return True
        return False

    margin = max(2, size // 12)
    radius = max(3, size // 5)
    border = max(1, size // 18)

    for y in range(size):
        for x in range(size):
            if inside_round_rect(x, y, margin, radius):
                color = (7, 12, 35, 255)
                if not inside_round_rect(x, y, margin + border, max(1, radius - border)):
                    color = (41, 116, 255, 255)
                if abs((x + y) - size) <= max(1, size // 18):
                    color = (67, 170, 255, 255)
                set_px(x, y, color)

    _draw_square(pixels, size, size // 5, size // 5, max(3, size // 10), (58, 132, 255, 255))
    _draw_square(pixels, size, size * 3 // 4, size // 5, max(2, size // 12), (116, 194, 255, 230))
    _draw_square(pixels, size, size // 5, size * 3 // 4, max(2, size // 12), (116, 194, 255, 230))

    _draw_text(pixels, size, "PS2")

    return pixels


def _draw_square(pixels, size, x, y, side, color):
    for yy in range(y, y + side):
        for xx in range(x, x + side):
            if 0 <= xx < size and 0 <= yy < size:
                pixels[yy * size + xx] = color


def _draw_text(pixels, size, text):
    scale = max(1, size // 20)
    glyph_w = 5 * scale
    glyph_h = 7 * scale
    gap = max(1, scale)
    total_w = len(text) * glyph_w + (len(text) - 1) * gap
    x0 = (size - total_w) // 2
    y0 = (size - glyph_h) // 2 + max(0, size // 32)

    def rect(x, y, w, h, color):
        for yy in range(y, y + h):
            for xx in range(x, x + w):
                if 0 <= xx < size and 0 <= yy < size:
                    pixels[yy * size + xx] = color

    for dx, dy, color in ((max(1, scale), max(1, scale), (0, 0, 0, 150)),
                          (0, 0, (245, 248, 255, 255))):
        x = x0 + dx
        for ch in text:
            glyph = _GLYPHS[ch]
            for gy, row in enumerate(glyph):
                for gx, bit in enumerate(row):
                    if bit == "1":
                        rect(x + gx * scale, y0 + dy + gy * scale,
                             scale, scale, color)
            x += glyph_w + gap
