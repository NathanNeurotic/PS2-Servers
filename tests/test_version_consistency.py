"""The launcher and Edge must not be able to disagree about the version.

There were two version numbers. `launcher/release_metadata.py` held
`PRODUCT_VERSION`, which the maintainer edits and which reaches the GUI, the
Windows executable metadata and `--version`. `.github/workflows/edge-build.yml`
held a second literal for non-tag builds. Nothing compared them.

They drifted, and the drift shipped. Development build `main-130-3efbc27`
attached

    PS2ServersEdge-0.5.0-edge.3efbc27-arm64-pi3-pi4-pi5-nas.tar.gz

beside a launcher reporting `0.4.9`. Both came out of the same release, so a
tester quoting "the version" in a report would name a different number depending
on which binary they happened to open -- and the version is most of what a bug
report is for.

The tag case is worse, because Edge derives its version from the tag and the
launcher does not: tagging `v0.5.0` while the source still said `0.4.9` publishes
binaries labelled both at once. That check needs a tag, so it lives in
release.yml. Everything checkable without one lives here, where it runs on every
push instead of once per release.

Run:  python -m unittest tests.test_version_consistency -v
"""

import importlib.util
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _load_version_source():
    """Import tools/version_source.py by path; tools/ is not a package."""
    spec = importlib.util.spec_from_file_location(
        "_ps2_version_source", os.path.join(ROOT, "tools", "version_source.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


vs = _load_version_source()


class VersionIsReadableWithoutImportingTheLauncher(unittest.TestCase):
    """A release runner must not have to execute GUI code to read a string.

    launcher/__init__.py installs an import hook for the dependency panel, so
    importing the package to reach one constant runs machinery that has nothing
    to do with versioning and can fail where the GUI dependencies are absent.
    """

    def test_product_version_looks_like_a_version(self):
        self.assertRegex(vs.product_version(), r"^\d+\.\d+\.\d+$")

    def test_qualifier_is_empty_or_prefixed_with_a_dash(self):
        qualifier = vs.version_qualifier()
        if qualifier:
            self.assertTrue(
                qualifier.startswith("-"),
                "VERSION_QUALIFIER %r must start with '-' so DISPLAY_VERSION "
                "reads like 0.5.0-rc1" % qualifier)

    def test_display_version_matches_the_launcher_package(self):
        """launcher.__version__ is DISPLAY_VERSION; the parser must agree with it."""
        with open(os.path.join(ROOT, "launcher", "release_metadata.py"),
                  encoding="utf-8") as handle:
            text = handle.read()
        product = re.search(r'PRODUCT_VERSION = "([^"]+)"', text).group(1)
        qualifier = re.search(r'VERSION_QUALIFIER = "([^"]*)"', text).group(1)
        self.assertEqual(vs.display_version(), product + qualifier)


class NoWorkflowCarriesItsOwnVersion(unittest.TestCase):
    def test_check_sources_passes(self):
        """The guard that would have caught the drift before it shipped."""
        try:
            vs.check_sources()
        except SystemExit as exc:
            self.fail(str(exc))

    def test_edge_workflow_derives_its_base_version(self):
        """Belt and braces: the fallback must reference the shared tool."""
        with open(vs.EDGE_WORKFLOW, encoding="utf-8") as handle:
            text = handle.read()
        # Compared as a boolean rather than with assertIn, which would print the
        # entire workflow on failure and bury the one sentence that matters.
        self.assertTrue(
            "tools/version_source.py" in text,
            "edge-build.yml no longer derives its base version from the shared "
            "source of truth, so it can drift from the launcher again")


class TagCheckActuallyRejects(unittest.TestCase):
    """The guard is only worth having if it refuses the bad case."""

    def test_matching_tag_is_accepted(self):
        vs.check_tag("v" + vs.display_version())

    def test_mismatched_tag_is_refused(self):
        product = vs.product_version()
        bumped = product.rsplit(".", 1)[0] + ".999"
        with self.assertRaises(SystemExit) as caught:
            vs.check_tag("v" + bumped)
        self.assertIn("mismatch", str(caught.exception))

    def test_missing_qualifier_is_refused(self):
        """v0.5.0-rc1 against an empty qualifier ships a GUI calling itself 0.5.0."""
        if vs.version_qualifier():
            self.skipTest("a qualifier is set; this case cannot arise right now")
        with self.assertRaises(SystemExit):
            vs.check_tag("v" + vs.product_version() + "-rc1")


if __name__ == "__main__":
    unittest.main()
