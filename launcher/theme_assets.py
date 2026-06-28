"""Embedded PS2 theme asset registry.

Binary theme images live under launcher/assets/theme/ in the source tree and are
loaded by the GUI at runtime. This module remains as a small registry/helper so
older code that checks theme_assets.ASSETS continues to work.
"""

ASSETS = {}

THEME_ASSET_FILES = {
    "BANNER": "banner.png",
    "BACKGROUND": "background.png",
    "ACCENT": "accent.png",
    "LINEBREAK": "linebreak.png",
    "LOGO": "logo.png",
    "ICON_SMB": "icon_smb.png",
    "ICON_UDPFS": "icon_udpfs.png",
    "ICON_UDPBD": "icon_udpbd.png",
}


def asset_names():
    return tuple(THEME_ASSET_FILES.keys())
