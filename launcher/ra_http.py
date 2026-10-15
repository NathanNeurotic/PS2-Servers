"""Verified, bounded RA HTTP transport with per-client request pacing."""
import email.utils
import math
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from launcher.ra_sets import MAX_RESPONSE


class RateLimited(ValueError):
    """Sanitized server backoff that the owning worker retains across children."""
    def __init__(self, retry_after):
        super().__init__("RA service rate limited")
        self.retry_after = retry_after


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        raise ValueError("RA redirects are not permitted")


class Transport:
    """Fixed HTTPS endpoint, POST-only fields and no automatic retries."""
    ENDPOINT = "https://retroachievements.org/dorequest.php"

    def __init__(self, *, timeout=5, opener=None, minimum_interval=1):
        for value, maximum in ((timeout, 30), (minimum_interval, 30)):
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= maximum:
                raise ValueError("Invalid request timing")
        self.timeout, self.minimum_interval = timeout, minimum_interval
        self._next_request = 0
        self._lock = threading.Lock()
        self._opener = opener or urllib.request.build_opener(
            _NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))

    def post(self, fields, *, cancel=None):
        """Send one paced request; cancellation never causes an automatic retry."""
        with self._lock:
            wait = max(0, self._next_request - time.monotonic())
            if cancel is not None:
                if cancel.wait(wait):
                    raise ValueError("RA request cancelled")
            elif wait:
                time.sleep(wait)
            request = urllib.request.Request(self.ENDPOINT,
                data=urllib.parse.urlencode(fields).encode("ascii"), method="POST", headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": "PS2-Servers owned RA client"})
            try:
                with self._opener.open(request, timeout=self.timeout) as response:
                    if response.status != 200:
                        raise ValueError("Unexpected RA HTTP status")
                    data = response.read(MAX_RESPONSE + 1)
            except urllib.error.HTTPError as error:
                if error.code == 429:
                    raw = error.headers.get("Retry-After", "60") if error.headers else "60"
                    try:
                        delay = float(raw)
                    except (ValueError, TypeError):
                        try:
                            delay = email.utils.parsedate_to_datetime(raw).timestamp() - time.time()
                        except (ValueError, TypeError, OverflowError):
                            delay = 60
                    if not math.isfinite(delay):
                        delay = 60
                    error.close()
                    raise RateLimited(max(1, min(300, math.ceil(delay)))) from None
                error.close()
                raise ValueError("RA account request failed") from None
            except Exception:
                raise ValueError("RA account request failed") from None
            finally:
                self._next_request = time.monotonic() + self.minimum_interval
            if not data or len(data) > MAX_RESPONSE:
                raise ValueError("Invalid RA response size")
            return data
