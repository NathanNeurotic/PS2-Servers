"""Identify the local game root for Caduceus companion pairing.

No I/O, side effects, saved-configuration changes or path guesses. A running
server's frozen launch configuration wins over edited-but-not-applied fields.
"""
import os

ROOT_FIELDS = {"smbv1": "games_folder", "smbv2": "games_folder",
               "smbv3": "games_folder", "udpfs": "root_dir",
               "http": "root_dir", "udpbd": "virtual_folder"}


def candidate_roots(configured, active=None):
    """Return distinct (absolute_folder, source_modes); prefer live shares.

    For multiple distinct live roots return all of them, so the caller can
    require an explicit choice instead of pairing with the wrong disk.
    """
    active = active or {}

    def collect(sources):
        result = {}
        for mode, field in ROOT_FIELDS.items():
            values = sources.get(mode)
            if not isinstance(values, dict):
                continue
            raw = values.get(field)
            if not isinstance(raw, str) or not raw.strip():
                continue
            # Path normalization only; never stat arbitrary devices/network
            # paths on the GUI thread, and never expose other fields/secrets.
            folder = os.path.abspath(os.path.expanduser(raw.strip()))
            key = os.path.normcase(os.path.normpath(folder))
            if key not in result:
                result[key] = (folder, [mode])
            else:
                result[key][1].append(mode)
        return [(path, tuple(modes)) for path, modes in result.values()]

    return collect(active) or collect(configured)
