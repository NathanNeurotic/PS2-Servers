"""Read-only RA patch/unlock requests using an existing runtime login token.

No login, credential persistence, session start or unlock submission occurs
here. Run off the UI thread behind a process deadline for hard cancellation;
the socket timeout alone is not a total wall-clock deadline.
"""
from launcher.ra_http import Transport, _NoRedirect
from launcher.ra_sets import SetContext, decode_response, qualify_set


class RASetClient:
    """Fetch a set and softcore prior unlocks for one captured account context."""
    ENDPOINT = Transport.ENDPOINT

    def __init__(self, username, api_token, *, timeout=5, opener=None):
        if type(username) is not str or not 1 <= len(username) <= 64:
            raise ValueError("Invalid account identity")
        if type(api_token) is not str or not 1 <= len(api_token) <= 4096 or "\0" in api_token:
            raise ValueError("Invalid runtime login token")
        self.username = username
        self._token = api_token
        self.timeout = timeout
        self._transport = Transport(timeout=timeout, opener=opener)

    def _request(self, operation, game_id, *, cancel=None):
        if operation not in ("gameid", "patch", "unlocks"):
            raise ValueError("Only read-only set requests are permitted")
        # Requests mirror pinned rcheevos rc_api_runtime.c / rc_api_user.c.
        fields = ({"r": "gameid", "m": game_id} if operation == "gameid" else
                  {"r": operation, "u": self.username, "t": self._token, "g": game_id})
        if operation == "unlocks":
            fields["h"] = 0  # only softcore is supported by our console engine
        return self._transport.post(fields, cancel=cancel)

    def fetch(self, context, *, include_unofficial=False, cancel=None):
        """Return only a fully qualified pair of successful account responses."""
        if not isinstance(context, SetContext) or context.account != self.username:
            raise ValueError("Account context mismatch")
        if type(include_unofficial) is not bool:
            raise ValueError("Invalid achievement selection")
        def check_cancelled():
            if cancel is not None and cancel.is_set():
                raise ValueError("Set lookup cancelled")
        check_cancelled()
        mapping = decode_response(self._request("gameid", context.image_hash, cancel=cancel))
        if type(mapping.get("GameID")) is not int or mapping["GameID"] != context.game_id:
            raise ValueError("Image hash/game identity mismatch or unknown image")
        check_cancelled()
        patch = self._request("patch", context.game_id, cancel=cancel)
        check_cancelled()
        unlocks = self._request("unlocks", context.game_id, cancel=cancel)
        check_cancelled()
        return qualify_set(context, patch, unlocks, include_unofficial=include_unofficial)
