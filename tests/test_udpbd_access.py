"""Access failures must remain visible when UDPBD can only serve reads."""

import contextlib
import importlib.util
import io
import pathlib
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "udpbd_access", ROOT / "udpbd_server" / "udpbd_server.py")
UDPBD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(UDPBD)


class AccessTests(unittest.TestCase):
    def test_write_denied_still_serves_reads_and_explains_save_failure(self):
        backing = io.BytesIO(b"x" * 512)
        errors = io.StringIO()
        with patch("builtins.open", side_effect=[
                PermissionError(13, "write denied"), backing]), \
                contextlib.redirect_stderr(errors):
            device = UDPBD.BlockDevice("disk.img")
        self.assertTrue(device.read_only)
        self.assertEqual(device.sector_count(), 1)
        self.assertEqual(device.read(512), b"x" * 512)
        self.assertIn("write denied", errors.getvalue())
        self.assertIn("saves / VMC writes will fail", errors.getvalue())
        device.close()

    def test_explicit_read_only_preserves_original_access_error(self):
        denied = PermissionError(13, "read denied")
        with patch("builtins.open", side_effect=denied) as opener:
            with self.assertRaises(PermissionError) as raised:
                UDPBD.BlockDevice("disk.img", read_only=True)
        self.assertIs(raised.exception, denied)
        opener.assert_called_once_with("disk.img", "rb", buffering=0)

    def test_cli_reports_denied_target_without_existence_precheck(self):
        output = io.StringIO()
        with patch.object(UDPBD, "BlockDevice", side_effect=PermissionError(
                13, "read denied")), contextlib.redirect_stdout(output):
            result = UDPBD.main(["/dev/test", "--read-only"])
        self.assertEqual(result, 1)
        self.assertIn("read denied", output.getvalue())
        self.assertIn("mount permissions", output.getvalue())
        self.assertIn("device access permissions", output.getvalue())


if __name__ == "__main__":
    unittest.main()
