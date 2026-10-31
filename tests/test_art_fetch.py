"""Opt-in artwork download: template checks, identity choice, bounds. Offline."""
import io
import tempfile
import threading
import unittest
import urllib.error
from email.message import Message
from pathlib import Path

from launcher import art_fetch
from launcher.library_catalog import Catalog

TEMPLATE = "https://covers.example/{serial}.jpg"


FAKE_IMAGE = b"\x89PNG\r\n\x1a\nnot decoded by fetch()"


def _png():
    try:
        from PIL import Image
    except ImportError:
        raise unittest.SkipTest("Pillow is optional for source checkout")
    out = io.BytesIO()
    Image.new("RGB", (8, 8), (1, 2, 3)).save(out, format="PNG")
    return out.getvalue()


class _Response(io.BytesIO):
    def __init__(self, body, kind="image/png", length=True):
        super().__init__(body)
        self.headers = Message()
        self.headers["Content-Type"] = kind
        if length:
            self.headers["Content-Length"] = str(len(body))


class _Opener:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.urls = []

    def open(self, request, timeout=None):
        self.urls.append(request.full_url)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def _http_error(code, retry_after=None):
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError("https://covers.example/x", code, "x", headers, None)


class TemplateTests(unittest.TestCase):
    def test_template_must_be_https_with_a_placeholder(self):
        self.assertEqual(art_fetch.validate_template(" " + TEMPLATE + " "), TEMPLATE)
        for bad in ("https://covers.example/cover.jpg", "http://covers.example/{serial}.jpg",
                    "https://user:pw@covers.example/{serial}.jpg", "file:///{serial}", ""):
            with self.assertRaises(ValueError, msg=bad):
                art_fetch.validate_template(bad)

    def test_both_serial_spellings(self):
        self.assertEqual(art_fetch.serial_forms("SLUS_201.74"), ("SLUS-20174", "SLUS_201.74"))
        self.assertEqual(art_fetch.build_url("https://a.example/{serial}/{opl_id}.png", "slus-201.74"),
                         "https://a.example/SLUS-20174/SLUS_201.74.png")
        with self.assertRaises(ValueError):
            art_fetch.serial_forms("Game.iso")


class FetchTests(unittest.TestCase):
    def test_returns_image_bytes(self):
        body = FAKE_IMAGE
        self.assertEqual(art_fetch.fetch("https://x", opener=_Opener(_Response(body))), body)

    def test_missing_cover_is_none(self):
        self.assertIsNone(art_fetch.fetch("https://x", opener=_Opener(_http_error(404))))

    def test_non_image_is_refused(self):
        with self.assertRaises(art_fetch.FetchError):
            art_fetch.fetch("https://x", opener=_Opener(_Response(b"<html>", "text/html")))

    def test_size_is_bounded_with_and_without_a_length(self):
        big = b"x" * 2048
        for length in (True, False):
            with self.assertRaises(art_fetch.FetchError):
                art_fetch.fetch("https://x", opener=_Opener(_Response(big, length=length)), max_bytes=1024)

    def test_one_bounded_retry_on_rate_limit(self):
        waits = []
        opener = _Opener(_http_error(429, "999"), _Response(FAKE_IMAGE))
        self.assertIsNotNone(art_fetch.fetch("https://x", opener=opener, sleep=waits.append))
        self.assertEqual(waits, [art_fetch.MAX_RETRY_WAIT])
        with self.assertRaises(art_fetch.FetchError):
            art_fetch.fetch("https://x", opener=_Opener(_http_error(503), _http_error(503)),
                            sleep=lambda _s: None)

    def test_whole_download_has_a_deadline(self):
        ticks = iter([0.0, 0.0, 100.0])

        class Slow(_Response):
            def read1(self, size):
                return b"x"

        with self.assertRaisesRegex(art_fetch.FetchError, "longer than"):
            art_fetch.fetch("https://x", opener=_Opener(Slow(b"")), timeout=15,
                            clock=lambda: next(ticks))

    def test_retry_wait_counts_against_the_same_deadline(self):
        ticks = iter([0.0, 0.0, 14.0])
        waits = []
        with self.assertRaisesRegex(art_fetch.FetchError, "limit"):
            art_fetch.fetch("https://x", opener=_Opener(_http_error(503), _Response(FAKE_IMAGE)),
                            sleep=waits.append, timeout=15, clock=lambda: next(ticks))
        self.assertEqual(waits, [])

    def test_cancel_before_start(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(art_fetch.Cancelled):
            art_fetch.fetch("https://x", opener=_Opener(), cancel=cancel)

    def test_redirect_away_from_https_is_refused(self):
        handler = art_fetch._HttpsOnlyRedirects()
        with self.assertRaises(art_fetch.FetchError):
            handler.redirect_request(None, None, 302, "Found", {}, "http://evil.example/x.png")
        self.assertEqual(handler.max_redirections, art_fetch.MAX_REDIRECTS)


class PlanAndRunTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "PS2"
        self.root.mkdir()
        self.catalog = Catalog(Path(tmp.name) / "library.sqlite3")
        self.addCleanup(self.catalog.__exit__)

    @staticmethod
    def row(status="not inspected", detected="", manual="", title="T"):
        return {"identity_status": status, "detected_disc_id": detected, "disc_id": manual,
                "title": title}

    def test_verified_serial_first_and_unsafe_ids_reported(self):
        (self.root / "ART").mkdir()
        (self.root / "ART" / "SLES_500.01_COV.png").write_bytes(b"keep")
        rows = [self.row("verified", "SLUS_201.74", manual="SLUS_999.99"),
                self.row(manual="sces-500.00"),
                self.row("conflict", "SLUS_111.11"),
                self.row("stale"),
                self.row(),
                self.row(manual="Homebrew.elf"),
                self.row(manual="SLUS_201.74"),
                self.row(manual="SLES_500.01")]
        result = art_fetch.plan(rows, self.root, TEMPLATE)
        self.assertEqual([(i, s) for _r, i, s, _u in result.fetch],
                         [("SLUS_201.74", "inspected"), ("SCES_500.00", "manual")])
        self.assertEqual(result.fetch[0][3], "https://covers.example/SLUS-20174.jpg")
        self.assertEqual(len(result.have_art), 1)
        self.assertEqual(len(result.skipped), 5)

    def test_run_installs_keeps_existing_and_survives_failures(self):
        _png()  # skips without Pillow
        rows = [self.row(manual="SLUS_201.74"), self.row(manual="SLUS_202.02"),
                self.row(manual="SLUS_203.03"), self.row(manual="SLUS_204.04")]
        the_plan = art_fetch.plan(rows, self.root, TEMPLATE)
        answers = {"SLUS-20174": _png(), "SLUS-20202": None, "SLUS-20303": b"not an image"}

        def fetcher(url, cancel=None):
            key = url.rsplit("/", 1)[1][:-4]
            if key not in answers:
                raise art_fetch.FetchError("HTTP 500 from " + url)
            return answers[key]

        report = dict(art_fetch.run(self.catalog, the_plan, self.root, fetcher=fetcher))
        self.assertTrue(report["SLUS_201.74"].startswith("saved SLUS_201.74_COV.png (manual ID)"))
        self.assertEqual(report["SLUS_202.02"], "not on the source")
        self.assertTrue(report["SLUS_203.03"].startswith("failed:"))
        self.assertTrue(report["SLUS_204.04"].startswith("failed: HTTP 500"))
        self.assertTrue((self.root / "ART" / "SLUS_201.74_COV.png").exists())
        # A second run keeps the file it already wrote.
        again = dict(art_fetch.run(self.catalog, the_plan, self.root, fetcher=fetcher))
        self.assertEqual(again["SLUS_201.74"], "kept the existing file")

    def test_missing_converter_stops_the_batch(self):
        rows = [self.row(manual="SLUS_201.74"), self.row(manual="SLUS_202.02")]
        the_plan = art_fetch.plan(rows, self.root, TEMPLATE)
        calls = []

        class NoPillow:
            def write_art(self, *_a, **_k):
                raise RuntimeError("Cover conversion requires Pillow")

        report = art_fetch.run(NoPillow(), the_plan, self.root,
                               fetcher=lambda url, cancel=None: calls.append(url) or FAKE_IMAGE)
        self.assertEqual(len(calls), 1)
        self.assertEqual([o for _i, o in report][1], "not attempted")

    def test_cancel_stops_the_rest(self):
        rows = [self.row(manual="SLUS_201.74"), self.row(manual="SLUS_202.02")]
        the_plan = art_fetch.plan(rows, self.root, TEMPLATE)
        cancel = threading.Event()

        def fetcher(url, cancel=None):
            cancel.set()
            return None

        report = art_fetch.run(self.catalog, the_plan, self.root, cancel=cancel, fetcher=fetcher)
        self.assertEqual([o for _i, o in report], ["not on the source", "cancelled"])


if __name__ == "__main__":
    unittest.main()
