"""Bounded account/set qualification and native direct-watch derivation.

Response fields follow pinned rcheevos src/rapi/rc_api_runtime.c and
rc_api_user.c. No unlocks or sessions are submitted by this module.
"""
import ctypes
from dataclasses import dataclass
import json
import re
import struct

from launcher.ra_watchlist import validate_watchlist, validate_orbit_watchlist

MAX_RESPONSE = 4 * 1024 * 1024


def _uint(value, name):
    if type(value) is not int or not 1 <= value <= 0xFFFFFFFF:
        raise ValueError("Invalid " + name)
    return value


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate response field")
        result[key] = value
    return result


def decode_response(data):
    """Reject malformed, oversized and failed responses without echoing secrets."""
    if type(data) is not bytes or not 1 <= len(data) <= MAX_RESPONSE:
        raise ValueError("Invalid RA response size")
    try:
        result = json.loads(data, object_pairs_hook=_unique_object)
    except (ValueError, RecursionError):
        raise ValueError("Invalid RA response") from None
    if type(result) is not dict or result.get("Success") is not True:
        raise ValueError("RA request failed")
    return result


@dataclass(frozen=True)
class SetContext:
    """Non-secret identity captured before asynchronous account/set loading."""
    account: str
    epoch: int
    image_hash: str
    game_id: int
    console_id: int

    def __post_init__(self):
        if type(self.account) is not str or not 1 <= len(self.account) <= 64:
            raise ValueError("Invalid account identity")
        _uint(self.epoch, "account epoch")
        _uint(self.game_id, "game ID")
        if type(self.console_id) is not int or self.console_id not in (12, 21):
            raise ValueError("Only PS1 and PS2 sets are supported")
        if type(self.image_hash) is not str or not re.fullmatch("[a-f0-9]{32}", self.image_hash):
            raise ValueError("Invalid image hash")


@dataclass(frozen=True)
class QualifiedSet:
    """Immutable eligible definitions with explicit account/game provenance."""
    context: SetContext
    title: str
    definitions: tuple
    selected_ids: tuple
    unofficial_ids: tuple
    prior_unlocks: tuple

    @property
    def remaining(self):
        """An empty set means nothing remains, not that the game is unsupported."""
        return len(self.definitions)


def qualify_set(context, patch_response, unlock_response, *, include_unofficial=False):
    """Select softcore eligibility only after both account requests succeed.

    The transport must request these responses for the captured context. The
    API's unlock response need not echo a user or game, so this function cannot
    authenticate arbitrary caller-supplied JSON on its own.
    """
    if not isinstance(context, SetContext) or type(include_unofficial) is not bool:
        raise ValueError("Invalid set context or selection")
    patch = decode_response(patch_response).get("PatchData")
    unlocks = decode_response(unlock_response)
    if type(patch) is not dict:
        raise ValueError("Missing game data")
    if (type(patch.get("ID")) is not int or patch["ID"] != context.game_id or
            type(patch.get("ConsoleID")) is not int or patch["ConsoleID"] != context.console_id):
        raise ValueError("Game/console identity mismatch")
    # Optional echoes are checked when supplied; absence follows rcheevos.
    if "GameID" in unlocks and (type(unlocks["GameID"]) is not int or unlocks["GameID"] != context.game_id):
        raise ValueError("Unlock game identity mismatch")
    if "HardcoreMode" in unlocks and (type(unlocks["HardcoreMode"]) not in (bool, int) or unlocks["HardcoreMode"] != 0):
        raise ValueError("Softcore unlock response required")
    title, achievements, unlocked = patch.get("Title"), patch.get("Achievements"), unlocks.get("UserUnlocks")
    if type(title) is not str or len(title) > 4096:
        raise ValueError("Invalid game title")
    if type(achievements) is not list or len(achievements) > 4096:
        raise ValueError("Invalid achievement collection")
    if type(unlocked) is not list or len(unlocked) > 100000:
        raise ValueError("Invalid prior unlock collection")
    prior = {_uint(value, "prior unlock ID") for value in unlocked}
    definitions, selected, unofficial, seen = [], [], [], set()
    total_bytes = 0
    for achievement in achievements:
        if type(achievement) is not dict:
            raise ValueError("Invalid achievement")
        achievement_id = _uint(achievement.get("ID"), "achievement ID")
        if achievement_id in seen:
            raise ValueError("Duplicate achievement ID")
        seen.add(achievement_id)
        category = achievement.get("Flags")
        if type(category) is not int:
            raise ValueError("Invalid achievement category")
        if category != 3 and not (include_unofficial and category == 5):
            continue
        selected.append(achievement_id)
        if category == 5:
            unofficial.append(achievement_id)
        if achievement_id in prior:
            continue
        expression = achievement.get("MemAddr")
        if type(expression) is not str or not 1 <= len(expression) <= 16384 or "\0" in expression:
            raise ValueError("Invalid achievement expression")
        try:
            total_bytes += len(expression.encode("ascii"))
        except UnicodeError:
            raise ValueError("Non-ASCII achievement expression") from None
        if total_bytes > MAX_RESPONSE:
            raise ValueError("Achievement definitions exceed limit")
        definitions.append((achievement_id, expression))
    return QualifiedSet(context, title, tuple(definitions), tuple(selected),
                        tuple(unofficial), tuple(sorted(prior)))


def derive_direct_watch(library, qualified, *, profile="opl"):
    """Derive complete direct reads using native rcheevos, never text matching.

    Pointer-chain sets are rejected as a whole until RANL derivation exists.
    ORBIT v1.0.2 additionally enforces its 1216-byte snapshot ceiling.
    """
    if not isinstance(qualified, QualifiedSet) or not qualified.remaining:
        raise ValueError("No remaining achievement definitions")
    if profile not in ("opl", "orbit-v1.0.2"):
        raise ValueError("Invalid watch profile")
    lib = ctypes.CDLL(str(library))
    probe = lib.ps2servers_probe_watch
    probe.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_uint32), ctypes.c_uint32]
    probe.restype = ctypes.c_int
    entries = set()
    memory_limit = 2 * 1024 * 1024 if qualified.context.console_id == 12 else 32 * 1024 * 1024
    for _, expression in qualified.definitions:
        output = (ctypes.c_uint32 * 4096)()
        count = probe(expression.encode("ascii"), output, len(output))
        if count < 0:
            raise ValueError("Native watch derivation rejected the complete set (code {})".format(count))
        for word in output[:count]:
            if (word & 0x0FFFFFFF) + (word >> 28) > memory_limit:
                raise ValueError("Watch read exceeds console RAM")
            entries.add(word)
        if len(entries) > 4096:
            raise ValueError("Watch entry limit exceeded")
    if not entries:
        raise ValueError("Set has no direct memory reads")
    words = sorted(entries, key=lambda word: (word & 0x0FFFFFFF, word >> 28))
    data = struct.pack("<4I", 0x4C574152, 1, len(words), sum(word >> 28 for word in words))
    data += struct.pack("<{}I".format(len(words)), *words)
    (validate_orbit_watchlist if profile == "orbit-v1.0.2" else validate_watchlist)(data)
    return data
