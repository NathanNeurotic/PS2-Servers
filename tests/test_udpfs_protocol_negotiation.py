import importlib.util
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
UDPFS_DIR = ROOT / "udpfs_server"
if str(UDPFS_DIR) not in sys.path:
    sys.path.insert(0, str(UDPFS_DIR))
SPEC = importlib.util.spec_from_file_location(
    "ps2servers_core", UDPFS_DIR / "ps2servers_core.py")
CORE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CORE)


class NegotiationTests(unittest.TestCase):
    def test_shared_handshake_fixtures(self):
        cases = json.loads((ROOT / "conformance" / "fixtures" / "handshake_cases.json").read_text())
        for case in cases:
            with self.subTest(case=case["name"]):
                self.assertEqual(
                    CORE.classify_profile(case["discovery"], case["first_data"]),
                    case["profile"],
                )

    def test_standard_sequence(self):
        self.assertEqual(CORE.classify_profile(0, 0), CORE.PROFILE_STANDARD)

    def test_modulo_fresh_sequence(self):
        self.assertEqual(CORE.classify_profile(0, 1), CORE.PROFILE_MODULO)

    def test_modulo_resumed_sequence(self):
        self.assertEqual(CORE.classify_profile(7, 8), CORE.PROFILE_MODULO)

    def test_modulo_wraparound(self):
        self.assertEqual(CORE.classify_profile(4095, 0), CORE.PROFILE_MODULO)

    def test_unrelated_sequence_falls_back_to_standard(self):
        self.assertEqual(CORE.classify_profile(7, 0), CORE.PROFILE_STANDARD)

    def test_default_protocol_mode_is_auto(self):
        parser = CORE.build_parser()
        args = parser.parse_args(["--root-dir", str(ROOT)])
        self.assertEqual(args.protocol_mode, "auto")


if __name__ == "__main__":
    unittest.main()
