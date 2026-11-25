"""The 32-bit Windows package is the Windows 7 build (issue #330).

These run everywhere, on any Python: they check the import-table reader against
synthetic PE images, and that the release workflows still build the 32-bit
package with Python 3.8 and gate it on tools/check_win7_imports.py.
"""
import os
import re
import struct
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import check_win7_imports as win7  # noqa: E402

SECTION_RVA = 0x1000
SECTION_FILE = 0x200


def make_pe(imports, os_version=(6, 0), subsystem_version=(6, 0), pe32plus=False):
    """A minimal PE image whose only content is an import table.

    imports is a list of (dll, [function names]).
    """
    thunk = 8 if pe32plus else 4
    descriptors = 20 * (len(imports) + 1)
    blob = bytearray(descriptors)
    patches = []
    for index, (dll, functions) in enumerate(imports):
        table = len(blob)
        blob += bytes(thunk * (len(functions) + 1))
        names = []
        for function in functions:
            names.append(len(blob))
            blob += b"\0\0" + function.encode("ascii") + b"\0"
            if len(blob) % 2:
                blob += b"\0"
        dll_name = len(blob)
        blob += dll.encode("ascii") + b"\0"
        for slot, name in enumerate(names):
            patches.append((table + thunk * slot, SECTION_RVA + name))
        struct.pack_into("<IIIII", blob, 20 * index,
                         SECTION_RVA + table, 0, 0, SECTION_RVA + dll_name, SECTION_RVA + table)
    for offset, value in patches:
        struct.pack_into("<Q" if pe32plus else "<I", blob, offset, value)

    opt_size = 240 if pe32plus else 224
    header = bytearray(SECTION_FILE)
    header[0:2] = b"MZ"
    struct.pack_into("<I", header, 0x3C, 0x40)
    header[0x40:0x44] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", header, 0x44, 0x8664 if pe32plus else 0x14C,
                     1, 0, 0, 0, opt_size, 0x0102)
    opt = 0x58
    struct.pack_into("<H", header, opt, 0x20B if pe32plus else 0x10B)
    struct.pack_into("<HH", header, opt + 40, *os_version)
    struct.pack_into("<HH", header, opt + 48, *subsystem_version)
    dirs = opt + (112 if pe32plus else 96)
    struct.pack_into("<I", header, dirs - 4, 16)
    struct.pack_into("<II", header, dirs + 8, SECTION_RVA, descriptors)
    section = opt + opt_size
    struct.pack_into("<8sIIII", header, section, b".idata", len(blob), SECTION_RVA,
                     len(blob), SECTION_FILE)
    return bytes(header) + bytes(blob)


class CheckerTests(unittest.TestCase):
    def check(self, data, name="image.dll"):
        with tempfile.TemporaryDirectory() as temporary:
            path = os.path.join(temporary, name)
            with open(path, "wb") as handle:
                handle.write(data)
            return win7.check_image(path)

    def test_the_two_errors_from_issue_330_are_caught(self):
        problems = self.check(make_pe([
            ("api-ms-win-core-path-l1-1-0.dll", ["PathCchCanonicalizeEx"]),
            ("KERNEL32.dll", ["CreateFileW", "PssQuerySnapshot"]),
        ]))
        self.assertEqual(len(problems), 2, problems)
        self.assertIn("api-ms-win-core-path-l1-1-0.dll", problems[0])
        self.assertIn("KERNEL32.dll!PssQuerySnapshot", problems[1])

    def test_a_windows_7_image_passes(self):
        for pe32plus in (False, True):
            with self.subTest(pe32plus=pe32plus):
                self.assertEqual(self.check(make_pe([
                    ("KERNEL32.dll", ["CreateFileW", "GetTickCount64", "AddDllDirectory"]),
                    ("api-ms-win-crt-runtime-l1-1-0.dll", ["_initterm"]),
                    ("api-ms-win-core-synch-l1-1-0.dll", ["AcquireSRWLockExclusive"]),
                    ("USER32.dll", ["MessageBoxW"]),
                ], pe32plus=pe32plus)), [])

    def test_newer_api_set_revision_is_caught(self):
        problems = self.check(make_pe([
            ("api-ms-win-core-synch-l1-2-0.dll", ["WaitOnAddress"]),
        ]))
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("api-ms-win-core-synch-l1-2-0.dll", problems[0])

    def test_header_asking_for_windows_8_is_caught(self):
        problems = self.check(make_pe([], subsystem_version=(6, 2)))
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("subsystem version 6.2", problems[0])

    def test_not_a_pe_is_reported_not_passed(self):
        self.assertTrue(self.check(b"not a PE image"))

    def test_main_walks_folders_and_returns_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            nested = os.path.join(temporary, "lib")
            os.mkdir(nested)
            with open(os.path.join(nested, "good.pyd"), "wb") as handle:
                handle.write(make_pe([("KERNEL32.dll", ["CreateFileW"])]))
            with open(os.path.join(temporary, "readme.txt"), "w") as handle:
                handle.write("not an image")
            self.assertEqual(win7.main([temporary]), 0)
            with open(os.path.join(nested, "bad.dll"), "wb") as handle:
                handle.write(make_pe([("shcore.dll", ["GetDpiForMonitor"])]))
            self.assertEqual(win7.main([temporary]), 1)
        with tempfile.TemporaryDirectory() as empty:
            self.assertEqual(win7.main([empty]), 2)


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as handle:
        return handle.read()


def pins(text):
    return dict(re.findall(r"^([A-Za-z0-9_.-]+)==(\S+)", text, re.M))


class WorkflowTests(unittest.TestCase):
    def test_same_packages_as_the_main_build(self):
        # Versions differ on purpose: the Windows 7 build keeps the last
        # releases that support Python 3.8.
        main = pins(read("requirements-build.txt"))
        win7_pins = pins(read("build", "requirements-win7.txt"))
        self.assertEqual(set(win7_pins), set(main))

    def test_dependabot_never_bumps_the_windows_7_pins(self):
        import yaml
        config = yaml.safe_load(read(".github", "dependabot.yml"))
        entries = [entry for entry in config["updates"]
                   if entry["package-ecosystem"] == "pip"
                   and entry["directory"].rstrip("/") == "/build"]
        self.assertEqual(len(entries), 1, entries)
        self.assertIn({"dependency-name": "*"}, entries[0].get("ignore", []))
        root = [entry for entry in config["updates"]
                if entry["package-ecosystem"] == "pip"
                and entry["directory"] == "/"]
        self.assertEqual(len(root), 1, root)
        self.assertIn("build/**", root[0].get("exclude-paths", []))

    def test_release_workflows_build_x86_on_python_38_and_gate_it(self):
        for name in ("release.yml", "release-on-main.yml"):
            with self.subTest(workflow=name):
                text = read(".github", "workflows", name)
                x86 = text[text.index("- name: Set up Python (Windows x86)"):]
                x86 = x86[:x86.index("\n\n")]
                self.assertRegex(x86, r"python-version: [\"']3\.8\.10[\"']")
                self.assertIn("build/requirements-win7.txt", text)
                self.assertEqual(text.count("python tools/check_win7_imports.py"), 2)


if __name__ == "__main__":
    unittest.main()
