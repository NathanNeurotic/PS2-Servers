"""A CD CHD's ISO size comes from its track table, not from the hunk count."""
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "udpfs_server"))

from compressed_iso.chd import ChdFileWrapper, LIBCHDR_AVAILABLE, exact_cd_iso_size  # noqa: E402
from udpfs_server import get_compressed_info  # noqa: E402

FIXTURES = os.path.join(_ROOT, "tests", "fixtures")
RAW = os.path.join(FIXTURES, "cd_chd", "synthetic-cd-1003.chd")
COOKED = os.path.join(FIXTURES, "cd_chd", "synthetic-cd-cooked-1003.chd")
DVD = os.path.join(FIXTURES, "ra_hash", "synthetic-dvd.chd")
SECTORS = 1003
EXPECTED = b"".join(i.to_bytes(4, "little") * 512 for i in range(SECTORS))


@unittest.skipUnless(LIBCHDR_AVAILABLE, "libchdr is not available on this machine")
class CdChdSizeTest(unittest.TestCase):
    def test_helper_gives_the_disc_length_for_raw_and_cooked(self):
        self.assertEqual(exact_cd_iso_size(RAW), SECTORS * 2048)
        self.assertEqual(exact_cd_iso_size(COOKED), SECTORS * 2048)

    def test_wrapper_and_listing_agree_and_match_the_source(self):
        for path in (RAW, COOKED):
            with self.subTest(path=os.path.basename(path)):
                self.assertEqual(get_compressed_info(path), (SECTORS * 2048, "CHD"))
                wrapper = ChdFileWrapper(path)
                try:
                    self.assertEqual(wrapper.uncompressed_size, SECTORS * 2048)
                    data = wrapper.read(1 << 30)
                    self.assertEqual(data, EXPECTED)
                    wrapper.seek(0, 2)
                    self.assertEqual(wrapper.tell(), SECTORS * 2048)
                finally:
                    wrapper.close()

    @unittest.skipIf(os.name == "nt", "symlinks need privileges on Windows")
    def test_a_symlinked_image_is_sized_like_its_target(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            link = os.path.join(tmp, "LINK.chd")
            os.symlink(RAW, link)
            self.assertEqual(exact_cd_iso_size(link), SECTORS * 2048)
            self.assertEqual(get_compressed_info(link), (SECTORS * 2048, "CHD"))

    def test_dvd_and_missing_files_are_left_alone(self):
        self.assertIsNone(exact_cd_iso_size(DVD))
        self.assertIsNone(exact_cd_iso_size(RAW + ".missing"))


if __name__ == "__main__":
    unittest.main()
