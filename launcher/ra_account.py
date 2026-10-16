"""Owned runtime-token login using pinned rcheevos login2 response fields."""
from launcher.ra_credentials import Credentials
from launcher.ra_http import Transport
from launcher.ra_sets import decode_response


class AccountClient:
    """Authenticate with a transient password or resume an existing runtime token."""
    def __init__(self, *, timeout=5, opener=None):
        self._transport = Transport(timeout=timeout, opener=opener)

    def login(self, username, *, password=None, token=None, cancel=None):
        """Return canonical credentials; never persist or return the password."""
        if type(username) is not str or not 1 <= len(username) <= 64 or any(c in username for c in "\0\r\n"):
            raise ValueError("Invalid account identity")
        if (password is None) == (token is None):
            raise ValueError("Exactly one login secret is required")
        secret = password if password is not None else token
        if type(secret) is not str or not 1 <= len(secret) <= 4096 or "\0" in secret:
            raise ValueError("Invalid login secret")
        fields = {"r": "login2", "u": username, "p" if password is not None else "t": secret}
        response = decode_response(self._transport.post(fields, cancel=cancel))
        return Credentials(response.get("User"), response.get("Token"))
