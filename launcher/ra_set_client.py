"""Read-only RA patch/unlock requests using an existing runtime login token.

No login, credential persistence, session start or unlock submission occurs
here. Run off the UI thread behind a process deadline for hard cancellation;
the socket timeout alone is not a total wall-clock deadline.
"""
import math
import ssl
import urllib.parse
import urllib.request

from launcher.ra_sets import MAX_RESPONSE, SetContext, decode_response, qualify_set


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        raise ValueError("RA redirects are not permitted")


class RASetClient:
    """Fetch a set and softcore prior unlocks for one captured account context."""
    ENDPOINT = "https://retroachievements.org/dorequest.php"

    def __init__(self, username, api_token, *, timeout=5, opener=None):
        if type(username) is not str or not 1 <= len(username) <= 64:
            raise ValueError("Invalid account identity")
        if type(api_token) is not str or not 1 <= len(api_token) <= 4096 or "\0" in api_token:
            raise ValueError("Invalid runtime login token")
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 30:
            raise ValueError("Invalid request timeout")
        self.username = username
        self._token = api_token
        self.timeout = timeout
        self._opener = opener or urllib.request.build_opener(
            _NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))

    def _request(self, operation, game_id):
        if operation not in ("gameid", "patch", "unlocks"):
            raise ValueError("Only read-only set requests are permitted")
        # Requests mirror pinned rcheevos rc_api_runtime.c / rc_api_user.c.
        fields = ({"r": "gameid", "m": game_id} if operation == "gameid" else
                  {"r": operation, "u": self.username, "t": self._token, "g": game_id})
        if operation == "unlocks":
            fields["h"] = 0  # only softcore is supported by our console engine
        body = urllib.parse.urlencode(fields).encode("ascii")
        request = urllib.request.Request(self.ENDPOINT, data=body, method="POST", headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "PS2-Servers owned RA client"})
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                if response.status != 200:
                    raise ValueError("Unexpected RA HTTP status")
                data = response.read(MAX_RESPONSE + 1)
        except Exception:
            # HTTP/server/proxy exception text can contain reflected credentials.
            raise ValueError("RA set request failed") from None
        if not data or len(data) > MAX_RESPONSE:
            raise ValueError("Invalid RA response size")
        return data

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
        mapping = decode_response(self._request("gameid", context.image_hash))
        if type(mapping.get("GameID")) is not int or mapping["GameID"] != context.game_id:
            raise ValueError("Image hash/game identity mismatch or unknown image")
        check_cancelled()
        patch = self._request("patch", context.game_id)
        check_cancelled()
        unlocks = self._request("unlocks", context.game_id)
        check_cancelled()
        return qualify_set(context, patch, unlocks, include_unofficial=include_unofficial)
