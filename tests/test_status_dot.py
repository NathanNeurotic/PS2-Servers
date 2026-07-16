"""Unit tests for the stdlib status-dot PNG generator.

Run:  python -m unittest tests.test_status_dot -v
"""

import base64
import struct
import unittest

from launcher import status_dot


def decode_png_header(b64):
    raw = base64.b64decode(b64)
    assert raw[:8] == b"\x89PNG\r\n\x1a\n", "bad PNG signature"
    # first chunk after the 8-byte signature is IHDR: len(4) type(4) then data
    assert raw[12:16] == b"IHDR", "IHDR not first"
    width, height, bit_depth, color_type = struct.unpack(">IIBB", raw[16:26])
    return width, height, bit_depth, color_type, raw


class StatusDotTests(unittest.TestCase):
    def test_filled_dot_is_valid_rgba_png(self):
        w, h, depth, ctype, raw = decode_png_header(
            status_dot.dot_png_base64(16, "#46f6b1", filled=True))
        self.assertEqual((w, h), (16, 16))
        self.assertEqual(depth, 8)
        self.assertEqual(ctype, 6)  # RGBA
        self.assertIn(b"IEND", raw)

    def test_ring_dot_is_valid(self):
        w, h, _d, ctype, _raw = decode_png_header(
            status_dot.dot_png_base64(14, "#8aa9d6", filled=False))
        self.assertEqual((w, h), (14, 14))
        self.assertEqual(ctype, 6)

    def test_minimum_diameter_clamped(self):
        w, h, _d, _c, _r = decode_png_header(
            status_dot.dot_png_base64(2, "#ffffff"))
        self.assertGreaterEqual(w, 6)
        self.assertEqual(w, h)

    def test_hex_forms_and_tuple_accepted(self):
        for color in ("#46f6b1", "46f6b1", "#4fa", (70, 246, 177)):
            self.assertTrue(status_dot.dot_png_base64(12, color))

    def test_result_is_cached(self):
        a = status_dot.dot_png_base64(20, "#123456", filled=True)
        b = status_dot.dot_png_base64(20, "#123456", filled=True)
        self.assertIs(a, b)  # same cached string object

    def test_filled_and_ring_differ(self):
        filled = status_dot.dot_png_base64(18, "#46f6b1", filled=True)
        ring = status_dot.dot_png_base64(18, "#46f6b1", filled=False)
        self.assertNotEqual(filled, ring)

    def test_center_filled_opaque_ring_hollow(self):
        # Un-filter the IDAT and read the centre pixel's alpha: a filled disc is
        # opaque in the middle, a ring is transparent there.
        def center_alpha(b64, size):
            import zlib
            raw = base64.b64decode(b64)
            i, idat = 8, b""
            while i < len(raw):
                length = struct.unpack(">I", raw[i:i + 4])[0]
                tag = raw[i + 4:i + 8]
                if tag == b"IDAT":
                    idat += raw[i + 8:i + 8 + length]
                i += 12 + length
            data = zlib.decompress(idat)
            stride = size * 4
            cy = size // 2
            # each row is prefixed with a 1-byte filter (0 == none, as we write)
            row = data[cy * (stride + 1) + 1:cy * (stride + 1) + 1 + stride]
            cx = size // 2
            return row[cx * 4 + 3]
        self.assertGreater(center_alpha(
            status_dot.dot_png_base64(18, "#46f6b1", filled=True), 18), 200)
        self.assertEqual(center_alpha(
            status_dot.dot_png_base64(18, "#46f6b1", filled=False), 18), 0)


if __name__ == "__main__":
    unittest.main()
