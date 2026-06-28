"""Embedded PS2 theme asset registry.

The server tab icons are optimized copies of the supplied PS2 Servers icon set.
They are embedded as base64 PNG strings so packaged builds keep the exact icons
without loose runtime files.
"""

ASSETS = {
    "ICON_SMB": "iVBORw0KGgoAAAANSUhEUgAAACYAAAATCAYAAAD8in+wAAAA+klEQVR42u2UMWoCURRFz3wkGYuZBKJgF7IAVyBDqgzuRHAPYmFlYRVIqqwjYIoUZgezgdilSEA0iKN//rcyiDhjBId5hbe+cM97//7nAOVK7fYSqCFA31/jMYADlF3XG1hsSwLYKl40jdUfCsCY5A4hUoo6gEKoCgPzgw5+0JEFlgVUKJiNnhyRYLPJjxXbMdFgWU9aCJh3fSOzY069bQ91Te1O8p9pTr29fZlKYr8ASsd+41Md101WWub5XGxvazrqHfSWAEyyfFNc5Ap1dd8Nt6GsMcN9PmOI/sB0op9J9GteUNWHfgMIgZd48Ru4rvceL+ePKfZPgDWQNFKYJIlrLQAAAABJRU5ErkJggg==",
    "ICON_UDPFS": "iVBORw0KGgoAAAANSUhEUgAAACYAAAATCAYAAAD8in+wAAAA+UlEQVR42u3UsWrCQBzH8e+lYg0BDQjN0sHiWp/AoVvoG3QXiuC7ODk4FPoahXbo0jfoCwRxcRC0GGqT3p1TpNSY4pDklP7mP/w/97//nQDspnd5DngYkPlsOgEQgF2r2kOss74JsCj6ulUqfrMAJFxhSIQlOgAWhqY0WK/eoldvmQXLApUKe/wIzL3Kf9hJwJyGK/7atVJgd9rVB03MabgiOU2R00vreRw7Fi4XOu+Gyeea7Fe4XOi0vjsTKwJn3Kv8Pa2sVACUki95o+7dtv8TJZV8TqvTSr9vYfI7Gkt4ygs1uLjuAj7wsF6vbmpV+zWOPkd7ygOADYBPSosSd1mMAAAAAElFTkSuQmCC",
    "ICON_UDPBD": "iVBORw0KGgoAAAANSUhEUgAAACYAAAATCAYAAAD8in+wAAABG0lEQVR42u2VsUoDQRRFz4xm4oYgBhLSCJoqlXZWKhZC8AvsrO3F1k/QxsLKH/AHBMUuX6CVnRFBJJEECRhn4xurlTVu2GqTEbzVFA/u4XLfGwUElYV6XoJBFQ/0+tx6BFBAYHKFYzWj9n0AC63dEQmbGgDlangirdUKgMZTzU7DNO8qem15TwCarZPk5KYBFkFFkN6A3b5c2Oj9odriDdjb4MmkLoGv5f8Hi2t+btGOds0LsNXqrknr2g+wYqmsiqWymiTkOM+/0bF+t+OyNtxYOiB+8fvdjkvy/ZXYJOC828rRtFI/cZHP66wJN2uHjdiJME7kKmlOxN19gw2H9gy4zApqu360DjSA83bvfsvkCjc2fD8dM/4A8AUhJlySGMujeAAAAABJRU5ErkJggg==",
}

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
