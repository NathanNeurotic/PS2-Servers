"""File-backed PS2 theme asset registry and Tk image loader."""

import math
import os
import sys


THEME_ASSET_FILES = {
    "BANNER": "BANNER.png",
    "BACKGROUND": "BACKGROUND.png",
    "ACCENT": "ACCENT.png",
    "LINEBREAK": "LINEBREAK.png",
    "LOGO": "LOGO.png",
    "ICON_SMB": "ICON_SMB.png",
    "ICON_UDPFS": "ICON_UDPFS.png",
    "ICON_UDPBD": "ICON_UDPBD.png",
}


def asset_names():
    return tuple(THEME_ASSET_FILES.keys())


def _candidate_asset_dirs():
    package_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(package_dir, "assets", "theme"),
    ]

    # PyInstaller-style extraction, harmless for Nuitka/source mode.
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        candidates.append(os.path.join(frozen_root, "launcher", "assets", "theme"))

    # Nuitka standalone/onefile data files are extracted beside the packaged
    # package layout. Keep this relative to the executable as a fallback.
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    candidates.append(os.path.join(exe_dir, "launcher", "assets", "theme"))

    # Last resort for source checkouts launched from repo root.
    candidates.append(os.path.join(os.getcwd(), "launcher", "assets", "theme"))

    seen = set()
    out = []
    for path in candidates:
        norm = os.path.normcase(os.path.abspath(path))
        if norm not in seen:
            out.append(path)
            seen.add(norm)
    return out


def asset_path(name):
    filename = THEME_ASSET_FILES.get(name)
    if not filename:
        return None
    for directory in _candidate_asset_dirs():
        path = os.path.join(directory, filename)
        if os.path.exists(path):
            return path
    return None


def _keep(owner, image):
    if owner is None or image is None:
        return image
    photos = getattr(owner, "_ps2_theme_photos", [])
    photos.append(image)
    owner._ps2_theme_photos = photos
    return image


def photo(gui, name, owner=None, subsample=1):
    path = asset_path(name)
    if not path:
        return None
    image = gui.tk.PhotoImage(file=path)
    if subsample and subsample > 1:
        image = image.subsample(subsample, subsample)
    return _keep(owner, image)


def photo_fit(gui, name, owner=None, max_width=None, max_height=None):
    path = asset_path(name)
    if not path:
        return None
    image = gui.tk.PhotoImage(file=path)
    width = max(1, image.width())
    height = max(1, image.height())
    factor = 1
    if max_width:
        factor = max(factor, int(math.ceil(width / float(max_width))))
    if max_height:
        factor = max(factor, int(math.ceil(height / float(max_height))))
    if factor > 1:
        image = image.subsample(factor, factor)
    return _keep(owner, image)
