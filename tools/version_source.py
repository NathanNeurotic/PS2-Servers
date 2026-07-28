"""One place that answers "what version is this?", and a guard that keeps it one.

The repository had two independent version numbers. `launcher/release_metadata.py`
carried `PRODUCT_VERSION`, which the maintainer edits and which feeds the GUI, the
Windows executable metadata and `--version`. `.github/workflows/edge-build.yml`
carried its own hardcoded literal for non-tag builds. Nothing compared them, so
they drifted, and the drift shipped: development build `main-130-3efbc27` attached
`PS2ServersEdge-0.5.0-edge.3efbc27-*` next to a launcher reporting `0.4.9`. A
tester quoting "the version" from that download would name a different number
depending on which binary they happened to look at, which is exactly the
information a bug report exists to carry.

On a tag the two could disagree in a worse way, because Edge derives its version
from the tag while the launcher does not: tagging `v0.5.0` while the source still
said `0.4.9` would publish binaries labelled both.

So: `launcher/release_metadata.py` is the source of truth. Edge asks this module
for the base version instead of hardcoding one, and `--check-tag` refuses to let a
tag be published that disagrees with the source.

Read with `ast` rather than by importing `launcher`, because importing that
package installs a GUI panel hook and pulls in machinery a release runner has no
reason to execute just to read a string.

Usage:
    python tools/version_source.py                  # print PRODUCT_VERSION
    python tools/version_source.py --print-display  # print DISPLAY_VERSION
    python tools/version_source.py --check-tag v0.5.0
    python tools/version_source.py --check-sources
"""

import argparse
import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
METADATA = os.path.join(ROOT, "launcher", "release_metadata.py")
EDGE_WORKFLOW = os.path.join(ROOT, ".github", "workflows", "edge-build.yml")

# Workflows that must not carry a version literal of their own. Anything here is
# checked by --check-sources and by tests/test_version_consistency.py.
VERSIONED_WORKFLOWS = (EDGE_WORKFLOW,)

# Any shell assignment of a dotted version literal, not just VERSION="...".
# The first version of this matched double quotes after the exact name VERSION,
# which a reintroduction as VERSION='0.5.0', VERSION=0.5.0 or BASE="0.5.0" would
# have walked straight past -- a guard narrower than the thing it guards.
_VERSION_LITERAL = re.compile(r"""\b([A-Z][A-Z0-9_]*)=["']?(\d+\.\d+\.\d+)""")


def _constants():
    """String constants from release_metadata.py, without importing it."""
    with open(METADATA, encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=METADATA)
    found = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, ast.Constant) or not isinstance(node.value.value, str):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                found[target.id] = node.value.value
    return found


def product_version():
    """The numeric version, e.g. "0.4.9". Never carries a qualifier."""
    values = _constants()
    try:
        return values["PRODUCT_VERSION"]
    except KeyError:
        raise SystemExit(
            "launcher/release_metadata.py no longer defines PRODUCT_VERSION as a "
            "plain string literal; version_source.py cannot read it")


def version_qualifier():
    """"" for a normal release, "-rc1"/"-beta1" while a version is under test."""
    return _constants().get("VERSION_QUALIFIER", "")


def display_version():
    """What the GUI shows and what a tester should quote in a report."""
    return product_version() + version_qualifier()


def expected_tag():
    return "v" + display_version()


def check_tag(tag):
    """Fail unless the tag being published matches the version in the source.

    Both halves matter. PRODUCT_VERSION feeds the executable metadata and Edge's
    filenames; VERSION_QUALIFIER feeds DISPLAY_VERSION, which is the string a
    tester reads off the GUI and puts in a report. Tagging v0.5.0-rc1 with an
    empty qualifier would ship binaries whose own GUI called them 0.5.0.
    """
    want = expected_tag()
    if tag == want:
        print("version check passed: tag %s matches launcher/release_metadata.py" % tag)
        return
    raise SystemExit(
        "tag/source version mismatch\n"
        "  tag being published : %s\n"
        "  source says         : %s  (PRODUCT_VERSION=%r, VERSION_QUALIFIER=%r)\n"
        "\n"
        "Publishing this would attach Edge binaries named for the tag next to a\n"
        "launcher reporting the source version, so a bug report could quote\n"
        "either. Edit launcher/release_metadata.py to match the tag, or tag %s."
        % (tag, want, product_version(), version_qualifier(), want))


def check_sources():
    """Fail if a workflow has grown a version literal of its own again."""
    problems = []
    for path in VERSIONED_WORKFLOWS:
        with open(path, encoding="utf-8") as handle:
            for number, line in enumerate(handle, 1):
                match = _VERSION_LITERAL.search(line)
                if match:
                    problems.append("%s:%d assigns %s=%s as a literal"
                                    % (os.path.relpath(path, ROOT), number,
                                       match.group(1), match.group(2)))
    if problems:
        raise SystemExit(
            "a workflow carries its own version number again:\n  %s\n\n"
            "That is how the launcher and Edge drifted apart. Derive it from\n"
            "launcher/release_metadata.py via tools/version_source.py instead."
            % "\n  ".join(problems))
    print("version check passed: no workflow carries its own version literal")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--print-display", action="store_true",
                        help="print DISPLAY_VERSION (with any qualifier)")
    parser.add_argument("--check-tag", metavar="TAG",
                        help="fail unless TAG matches the version in the source")
    parser.add_argument("--check-sources", action="store_true",
                        help="fail if a workflow hardcodes its own version")
    args = parser.parse_args(argv)

    if args.check_tag:
        check_tag(args.check_tag)
        return 0
    if args.check_sources:
        check_sources()
        return 0
    print(display_version() if args.print_display else product_version())
    return 0


if __name__ == "__main__":
    sys.exit(main())
