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

    def test_display_version_matches_an_independent_read(self):
        """Cross-check the ast parser against a different way of reading the file.

        Deliberately not the same mechanism: a parser validated only by itself
        would agree with its own bug. The regex is the naive read, so if the two
        disagree one of them is wrong.

        Both quote styles are accepted, and a missing match fails with a sentence
        rather than an AttributeError on .group() -- the assertion should say what
        is wrong, not crash on the way to finding out.
        """
        with open(os.path.join(ROOT, "launcher", "release_metadata.py"),
                  encoding="utf-8") as handle:
            text = handle.read()
        product = re.search(r"""PRODUCT_VERSION\s*=\s*["']([^"']+)["']""", text)
        self.assertIsNotNone(
            product, "PRODUCT_VERSION is no longer a plain string literal in "
                     "launcher/release_metadata.py")
        qualifier = re.search(r"""VERSION_QUALIFIER\s*=\s*["']([^"']*)["']""", text)
        # A missing VERSION_QUALIFIER is legitimate; version_source defaults it
        # to "", so the independent read has to as well.
        suffix = qualifier.group(1) if qualifier else ""
        self.assertEqual(vs.display_version(), product.group(1) + suffix)


class NoWorkflowCarriesItsOwnVersion(unittest.TestCase):
    def test_check_sources_passes(self):
        """The guard that would have caught the drift before it shipped."""
        try:
            vs.check_sources()
        except SystemExit as exc:
            self.fail(str(exc))

    def _edge_version_step(self):
        """The `run:` body of edge-build.yml's version step, from parsed YAML."""
        import yaml
        with open(vs.EDGE_WORKFLOW, encoding="utf-8") as handle:
            workflow = yaml.safe_load(handle)
        for step in workflow["jobs"]["build"]["steps"]:
            if step.get("id") == "version":
                return step["run"]
        self.fail("edge-build.yml has no step with id: version")

    def test_edge_workflow_derives_its_base_version(self):
        """The step that computes the version must call the shared tool.

        Checked against that step's parsed `run:` body rather than against the
        whole file. A substring search over the file would be satisfied by the
        path appearing in a comment or an unrelated step while the fallback went
        back to a hardcoded literal -- and this file now contains exactly such a
        comment, explaining the drift, so that weaker check would be self-
        satisfying.
        """
        run = self._edge_version_step()
        self.assertTrue(
            "version_source.py" in run,
            "edge-build.yml's version step no longer calls version_source.py, "
            "so its base version can drift from the launcher again")

    def test_edge_workflow_validates_a_tag_itself(self):
        """release.yml's needs-graph cannot gate edge.yml.

        edge.yml triggers on v* tags independently and attaches straight to the
        release, so a mismatched tag would otherwise publish Edge binaries named
        for the tag while the launcher half correctly refused to build -- a
        release holding Edge alone, labelled a version the source denies.
        """
        run = self._edge_version_step()
        self.assertTrue(
            "--check-tag" in run,
            "edge-build.yml does not validate the tag against the source, and "
            "nothing else can validate it on this workflow's behalf")


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

    def test_qualifier_mismatch_is_refused(self):
        """The qualifier half must be enforced whichever way it is wrong.

        Both directions ship a GUI that contradicts the release it came from:
        tagging v0.5.0-rc1 with an empty qualifier gives binaries calling
        themselves 0.5.0, and tagging a bare v0.5.0 while the qualifier still
        says -rc1 gives binaries calling themselves 0.5.0-rc1.

        Skipping whichever case does not currently apply would mean this test
        quietly stops checking anything the moment a qualifier is set -- which is
        precisely when a release is under test and the check matters most.
        """
        product = vs.product_version()
        if vs.version_qualifier():
            wrong = "v" + product          # bare tag, qualifier still set
        else:
            wrong = "v" + product + "-rc1"  # qualified tag, no qualifier set
        with self.assertRaises(SystemExit) as caught:
            vs.check_tag(wrong)
        self.assertIn("mismatch", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
