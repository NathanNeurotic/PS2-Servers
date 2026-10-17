"""Read-write RA session and unlock client using an existing runtime token.

Never contacts real RA in tests or CI. Requires captured SetContext.
"""
from dataclasses import dataclass
import hashlib
import json

from launcher.ra_http import Transport
from launcher.ra_sets import SetContext, decode_response, MAX_RESPONSE


@dataclass(frozen=True)
class SessionInfo:
    """Non-secret session response state from RA startsession."""
    game_id: int
    server_now: int = 0
    unlocks: tuple = ()
    hardcore_unlocks: tuple = ()

    def __post_init__(self):
        if type(self.game_id) is not int or not 1 <= self.game_id <= 0xFFFFFFFF:
            raise ValueError("Invalid game ID")
        if type(self.server_now) is not int or self.server_now < 0:
            raise ValueError("Invalid server timestamp")
        for collection in (self.unlocks, self.hardcore_unlocks):
            if type(collection) is not tuple or len(collection) > 100000:
                raise ValueError("Invalid session unlocks")
            for item in collection:
                if type(item) is not int or not 1 <= item <= 0xFFFFFFFF:
                    raise ValueError("Invalid unlock achievement ID")


@dataclass(frozen=True)
class AwardResult:
    """Non-secret result of awardachievement."""
    achievement_id: int
    awarded: bool
    score: int = 0
    softcore_score: int = 0
    remaining: int = 0
    already_unlocked: bool = False

    def __post_init__(self):
        if type(self.achievement_id) is not int or not 1 <= self.achievement_id <= 0xFFFFFFFF:
            raise ValueError("Invalid achievement ID")
        if type(self.awarded) is not bool or type(self.already_unlocked) is not bool:
            raise ValueError("Invalid award status")
        for value in (self.score, self.softcore_score, self.remaining):
            if type(value) is not int or value < 0:
                raise ValueError("Invalid award scores or remaining count")


class RASessionClient:
    """Read-write RA session and unlock client using an existing runtime login token."""
    ENDPOINT = Transport.ENDPOINT

    def __init__(self, username, api_token, *, timeout=5, opener=None):
        if type(username) is not str or not 1 <= len(username) <= 64 or any(c in username for c in "\0\r\n"):
            raise ValueError("Invalid account identity")
        if type(api_token) is not str or not 1 <= len(api_token) <= 4096 or "\0" in api_token:
            raise ValueError("Invalid runtime login token")
        self.username = username
        self._token = api_token
        self.timeout = timeout
        self._transport = Transport(timeout=timeout, opener=opener)

    def _check_context(self, context):
        if not isinstance(context, SetContext) or context.account != self.username:
            raise ValueError("Account context mismatch")

    def start_session(self, context, *, client_string="PS2-Servers/owned-runtime", cancel=None):
        """Send startsession for softcore game session tracking."""
        self._check_context(context)
        fields = {
            "r": "startsession",
            "u": self.username,
            "t": self._token,
            "g": context.game_id,
            "h": 0,
            "m": context.image_hash,
            "l": client_string,
        }
        response = decode_response(self._transport.post(fields, cancel=cancel))
        unlocks = tuple(item["ID"] for item in response.get("Unlocks", []) if isinstance(item, dict) and "ID" in item)
        hardcore = tuple(item["ID"] for item in response.get("HardcoreUnlocks", []) if isinstance(item, dict) and "ID" in item)
        server_now = response.get("ServerNow", 0)
        return SessionInfo(context.game_id, int(server_now) if type(server_now) in (int, float) else 0,
                           unlocks, hardcore)

    def award_achievement(self, context, achievement_id, *, seconds_since_unlock=0, cancel=None):
        """Submit softcore achievement unlock with MD5 signature validation."""
        self._check_context(context)
        if type(achievement_id) is not int or not 1 <= achievement_id <= 0xFFFFFFFF:
            raise ValueError("Invalid achievement ID")
        if type(seconds_since_unlock) is not int or seconds_since_unlock < 0:
            raise ValueError("Invalid unlock timing")

        sig_data = f"{achievement_id}{self.username}0"
        if seconds_since_unlock:
            sig_data += f"{achievement_id}{seconds_since_unlock}"
        signature = hashlib.md5(sig_data.encode("ascii")).hexdigest()

        fields = {
            "r": "awardachievement",
            "u": self.username,
            "t": self._token,
            "a": achievement_id,
            "h": 0,
            "m": context.image_hash,
            "v": signature,
        }
        if seconds_since_unlock:
            fields["o"] = seconds_since_unlock

        data = self._transport.post(fields, cancel=cancel)
        if type(data) is not bytes or not 1 <= len(data) <= MAX_RESPONSE:
            raise ValueError("Invalid RA response size")
        try:
            raw = json.loads(data)
        except Exception as error:
            raise ValueError("Invalid RA response") from error

        if not isinstance(raw, dict):
            raise ValueError("Invalid RA response")
        if raw.get("Success") is True:
            return AwardResult(achievement_id, True,
                               score=int(raw.get("Score") or 0),
                               softcore_score=int(raw.get("SoftcoreScore") or 0),
                               remaining=int(raw.get("AchievementsRemaining") or 0),
                               already_unlocked=False)
        error_msg = str(raw.get("Error") or "")
        if error_msg.startswith("User already has"):
            return AwardResult(achievement_id, True,
                               score=int(raw.get("Score") or 0),
                               softcore_score=int(raw.get("SoftcoreScore") or 0),
                               remaining=int(raw.get("AchievementsRemaining") or 0),
                               already_unlocked=True)
        raise ValueError("RA award achievement request failed")

    def ping(self, context, *, rich_presence=None, cancel=None):
        """Send periodic softcore session ping, optionally with rich presence text."""
        self._check_context(context)
        fields = {
            "r": "ping",
            "u": self.username,
            "t": self._token,
            "g": context.game_id,
            "h": 0,
            "x": context.image_hash,
        }
        if rich_presence is not None:
            fields["m"] = str(rich_presence)[:4096]
        response = decode_response(self._transport.post(fields, cancel=cancel))
        return response.get("Success") is True
