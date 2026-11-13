"""exact_cd_iso_size: a CD CHD's length comes from its track table."""
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "udpfs_server"))

from compressed_iso.chd import ChdFileWrapper, LIBCHDR_AVAILABLE, exact_cd_iso_size  # noqa: E402

CD = os.path.join(_ROOT, "tests", "fixtures", "cd_chd", "synthetic-cd-1003.chd")
DVD = os.path.join(_ROOT, "tests", "fixtures", "ra_hash", "synthetic-dvd.chd")
SECTORS = 1003


@unittest.skipUnless(LIBCHDR_AVAILABLE, "libchdr is not available on this machine")
class ExactCdSizeTest(unittest.TestCase):
    def test_track_table_gives_the_disc_length(self):
        self.assertEqual(exact_cd_iso_size(CD), SECTORS * 2048)

    def test_wrapper_still_reports_the_hunk_rounded_size(self):
        # Deliberate: UDPFS matches udpfsd. Only callers that ask exact_cd_iso_size
        # get the shorter length.
        wrapper = ChdFileWrapper(CD)
        try:
            self.assertEqual(wrapper.uncompressed_size, 1008 * 2048)
        finally:
            wrapper.close()

    def test_non_cd_and_missing_files_give_none(self):
        self.assertIsNone(exact_cd_iso_size(DVD))
        self.assertIsNone(exact_cd_iso_size(CD + ".missing"))


if __name__ == "__main__":
    unittest.main()
