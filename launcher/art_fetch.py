"""Opt-in artwork download from a URL template the user supplies.

Nothing here runs on its own: no provider is built in and nothing is fetched
at startup. The user enters a template such as
``https://example.org/covers/{serial}.jpg`` and confirms a preview first. Each
download is bounded (HTTPS only, redirects, bytes, time, one retry) and
cancellable, and every image still goes through ``Catalog.write_art``, which
refuses to replace an existing file.

Whether a source lets you use its artwork is between you and that source;
supplying a URL here does not grant any rights.
"""

import io
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from . import library_catalog

MAX_BYTES = 8 * 1024 * 1024
TIMEOUT = 15
MAX_REDIRECTS = 3
MAX_RETRY_WAIT = 10
MAX_BATCH = 500
USER_AGENT = "PS2-Servers artwork import"
PLACEHOLDERS = ("{serial}", "{opl_id}")
_OPL_ID = re.compile(r"^([A-Z]{4})_(\d{3})\.(\d{2})$")


class FetchError(Exception):
    """A download that was refused or failed; the message is shown to the user."""


class Cancelled(Exception):
    pass


def validate_template(template):
    """Return the stripped template or raise ValueError with a plain reason."""
    template = (template or "").strip()
    if not any(p in template for p in PLACEHOLDERS):
        raise ValueError("The URL must contain {serial} (SLUS-20174) or {opl_id} (SLUS_201.74).")
    probe = urllib.parse.urlsplit(template.replace("{serial}", "x").replace("{opl_id}", "x"))
    if probe.scheme != "https" or not probe.hostname:
        raise ValueError("Only https:// URLs are accepted.")
    if probe.username or probe.password:
        raise ValueError("Do not put a user name or password in the URL.")
    return template


def serial_forms(opl_id):
    """(serial, opl_id) for a PS2 disc ID, e.g. ('SLUS-20174', 'SLUS_201.74')."""
    match = _OPL_ID.fullmatch((opl_id or "").upper().replace("-", "_"))
    if not match:
        raise ValueError("Not a PS2 disc ID: {!r}".format(opl_id))
    prefix, a, b = match.groups()
    return "{}-{}{}".format(prefix, a, b), "{}_{}.{}".format(prefix, a, b)


def build_url(template, opl_id):
    serial, opl = serial_forms(opl_id)
    quote = lambda s: urllib.parse.quote(s, safe="")
    return template.replace("{serial}", quote(serial)).replace("{opl_id}", quote(opl))


class _HttpsOnlyRedirects(urllib.request.HTTPRedirectHandler):
    max_redirections = MAX_REDIRECTS

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme != "https":
            raise FetchError("Refused a redirect away from HTTPS: {}".format(newurl))
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _opener():
    return urllib.request.build_opener(_HttpsOnlyRedirects())


def fetch(url, *, cancel=None, opener=None, max_bytes=MAX_BYTES, timeout=TIMEOUT, sleep=None):
    """Bytes of one image, or FetchError. 404 returns None (not on the source)."""
    opener = opener or _opener()
    cancel = cancel or threading.Event()
    sleep = sleep or cancel.wait
    for attempt in (1, 2):
        if cancel.is_set():
            raise Cancelled()
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with opener.open(request, timeout=timeout) as response:
                kind = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if not kind.startswith("image/"):
                    raise FetchError("Not an image ({}): {}".format(kind or "no type", url))
                length = response.headers.get("Content-Length")
                if length and length.isdigit() and int(length) > max_bytes:
                    raise FetchError("Image larger than {} MB: {}".format(max_bytes // 2**20, url))
                body = bytearray()
                while True:
                    if cancel.is_set():
                        raise Cancelled()
                    chunk = response.read(65536)
                    if not chunk:
                        return bytes(body)
                    body += chunk
                    if len(body) > max_bytes:
                        raise FetchError("Image larger than {} MB: {}".format(max_bytes // 2**20, url))
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 410):
                return None
            if exc.code in (429, 500, 502, 503, 504) and attempt == 1:
                wait = exc.headers.get("Retry-After", "") if exc.headers else ""
                sleep(min(int(wait), MAX_RETRY_WAIT) if wait.isdigit() else 2)
                continue
            raise FetchError("HTTP {} from {}".format(exc.code, url)) from None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            reason = getattr(exc, "reason", exc)
            raise FetchError("Could not download {}: {}".format(url, reason)) from None
    raise FetchError("No answer from {}".format(url))


@dataclass
class Plan:
    """What a batch would do, shown to the user before anything is fetched."""
    fetch: list = field(default_factory=list)       # (row, opl_id, source_of_id, url)
    have_art: list = field(default_factory=list)    # rows that already have the file
    skipped: list = field(default_factory=list)     # (row, reason)


def plan(rows, root, template, art_type="COV"):
    """Pick an identity for each row, verified BOOT2 serial first."""
    template = validate_template(template)
    library_catalog.artwork_filename("SLUS_000.00", art_type)  # validates art_type
    art = library_catalog.require_directory(root) / "ART"
    result = Plan()
    seen = set()
    for row in rows:
        status = row.get("identity_status", "")
        manual = (row.get("disc_id") or "").upper().replace("-", "_")
        if status == "verified":
            opl_id, source = row["detected_disc_id"], "inspected"
        elif status in ("conflict", "stale"):
            result.skipped.append((row, "identity is {}; inspect it again or fix the ID".format(status)))
            continue
        elif manual:
            opl_id, source = manual, "manual"
        else:
            result.skipped.append((row, "no disc ID; run Inspect IDs or enter one"))
            continue
        try:
            serial_forms(opl_id)
        except ValueError:
            result.skipped.append((row, "{} is not a PS2 disc ID".format(opl_id)))
            continue
        if opl_id in seen:
            result.skipped.append((row, "{} is already in this batch".format(opl_id)))
            continue
        seen.add(opl_id)
        if (art / library_catalog.artwork_filename(opl_id, art_type)).exists():
            result.have_art.append(row)
            continue
        if len(result.fetch) >= MAX_BATCH:
            result.skipped.append((row, "batch limit of {} reached".format(MAX_BATCH)))
            continue
        result.fetch.append((row, opl_id, source, build_url(template, opl_id)))
    return result


def run(catalog, the_plan, root, art_type="COV", *, cancel=None, progress=None, fetcher=fetch):
    """Download and install a confirmed plan. Returns a list of (opl_id, outcome)."""
    cancel = cancel or threading.Event()
    report = []
    total = len(the_plan.fetch)
    for done, (_row, opl_id, source, url) in enumerate(the_plan.fetch):
        if cancel.is_set():
            report.append((opl_id, "cancelled"))
            continue
        if progress:
            progress(done, total, opl_id)
        try:
            data = fetcher(url, cancel=cancel)
            if data is None:
                report.append((opl_id, "not on the source"))
                continue
            target = catalog.write_art(io.BytesIO(data), root, opl_id, art_type)
            report.append((opl_id, "saved {} ({} ID) from {}".format(target.name, source, url)))
        except Cancelled:
            report.append((opl_id, "cancelled"))
        except FileExistsError:
            report.append((opl_id, "kept the existing file"))
        except Exception as exc:  # one bad image must not stop the batch
            report.append((opl_id, "failed: {}".format(exc)))
    if progress:
        progress(total, total, "")
    return report
