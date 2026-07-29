"""Every Edge UDPFS flag must be reachable from OpenWrt and described in the docs.

Adding a flag to the Go CLI is the easy half. The flag is useless on the
hardware Edge exists for until the OpenWrt package can set it, and undiscoverable
until a document mentions it -- and neither of those is enforced by anything the
compiler or the Go tests can see. A feature can therefore be finished, merged,
released, and still be unreachable by every user who has one.

That is not hypothetical. --block-device, --sector-size, --no-compression and
--compression-cache-size shipped complete, with tests, and then sat invisible:
no mention in docs/EDGE.md or the Edge README, and no UCI option, so on a router
-- the entire reason Edge exists -- there was no supported way to turn any of
them on. --tx-delay-ms had been in the same state for longer.

So the rule is enforced here instead of remembered. A new flag fails this test
until it is either wired into the OpenWrt package and documented, or listed in
UCI_EXEMPT with a reason. Deciding to exempt a flag is fine; forgetting that the
decision exists is what this prevents.

Run:  python -m unittest tests.test_edge_flag_exposure -v
"""

import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN_GO = os.path.join(
    ROOT, "native", "ps2servers-edge", "cmd", "ps2servers-edge", "main.go")
UCI_CONFIG = os.path.join(
    ROOT, "packaging", "openwrt", "files", "ps2servers-edge.config")
UCI_INIT = os.path.join(
    ROOT, "packaging", "openwrt", "files", "ps2servers-edge.init")
EDGE_DOC = os.path.join(ROOT, "docs", "EDGE.md")
OPENWRT_DOC = os.path.join(ROOT, "docs", "OPENWRT.md")

# Flag -> UCI option name. Mostly mechanical, but --protocol-mode is spelled
# `protocol` in UCI, so the mapping is written out rather than derived: a
# name-mangling rule would have to grow an exception for that anyway, and an
# explicit table is what a reader needs when adding the next flag.
FLAG_TO_UCI = {
    "--root": "root",
    "--bind": "bind",
    "--port": "port",
    "--data-port": "data_port",
    "--protocol-mode": "protocol",
    "--single-port": "single_port",
    "--peer-timeout": "peer_timeout",
    "--tx-delay-ms": "tx_delay_ms",
    "--read-only": "read_only",
    "--block-device": "block_device",
    "--sector-size": "sector_size",
    "--no-compression": "no_compression",
    "--compression-cache-size": "compression_cache_size",
    "--metrics": "metrics",
    "--metrics-period": "metrics_period",
    "--log-format": "log_format",
    "--verbose": "verbose",
}

# Deliberately not UCI options. The reason is carried in the value so that
# removing an exemption is a decision someone has to read, not a line they can
# delete without noticing what it said.
UCI_EXEMPT = {
    "--modulo-mode": "deprecated alias for --protocol-mode modulo; `protocol` covers it",
    "--quiet": "procd forwards stdout to syslog; `verbose` is the level knob",
    "--version": "a diagnostic, not a service setting",
}

# Flags a user never needs told about in the Edge guide.
DOC_EXEMPT = {"--modulo-mode"}


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _udpfs_flags():
    """Flag names registered on the udpfs FlagSet, not the udpbd one.

    main.go builds two flag sets. Slicing between them keeps this test honest
    about which CLI it is describing -- udpbd is a separate subcommand with its
    own much smaller surface, and it is not driven by this UCI file at all.
    """
    text = _read(MAIN_GO)
    start = text.index('flag.NewFlagSet("udpfs"')
    end = text.index('flag.NewFlagSet("udpbd"')
    if not start < end:
        raise AssertionError("expected the udpfs flag set to be defined first")
    region = text[start:end]
    found = re.findall(r'fs\.(?:String|Bool|Int|Float64)\(\s*"([a-z0-9-]+)"', region)
    return {"--" + name for name in found}


def _section(text, name):
    """One `config <type> '<name>'` block, without the sections after it.

    These checks are about the udpfs service specifically -- FLAG_TO_UCI maps
    udpfs flags -- so they must not scan the whole file. Edge later gained smb
    and udpbd sections whose options map to those subcommands' flags instead;
    scanning globally reported every one of them as an orphan.
    tests/test_edge_service_modes.py covers those.
    """
    lines = text.splitlines()
    out, inside = [], False
    for line in lines:
        if line.startswith("config "):
            inside = line.rstrip().endswith("'%s'" % name)
            continue
        if inside:
            out.append(line)
    return "\n".join(out)


class UdpfsFlagsAreReachableFromOpenWrt(unittest.TestCase):
    def setUp(self):
        self.flags = _udpfs_flags()
        self.config = _read(UCI_CONFIG)
        self.init = _read(UCI_INIT)

    def test_flag_extraction_actually_found_the_flags(self):
        """Guard the guard: a parsing change must not silently empty this test."""
        self.assertGreaterEqual(len(self.flags), 15, self.flags)
        for expected in ("--root", "--block-device", "--compression-cache-size"):
            self.assertIn(expected, self.flags)

    def test_every_flag_is_either_mapped_or_exempt(self):
        classified = set(FLAG_TO_UCI) | set(UCI_EXEMPT)
        unclassified = sorted(self.flags - classified)
        self.assertEqual(unclassified, [], (
            "these Edge flags have no UCI option and no exemption, so an "
            "OpenWrt operator cannot use them: %s. Add a UCI option, or add an "
            "entry to UCI_EXEMPT saying why not." % ", ".join(unclassified)))

    def test_no_stale_entries(self):
        """A removed flag must not leave a mapping behind claiming it exists."""
        stale = sorted((set(FLAG_TO_UCI) | set(UCI_EXEMPT)) - self.flags)
        self.assertEqual(stale, [], (
            "these are mapped or exempted but no longer exist in main.go: %s"
            % ", ".join(stale)))

    def test_mapped_options_are_in_the_shipped_config(self):
        declared = set(re.findall(
            r"(?m)^\s*option\s+([a-z0-9_]+)\s+'", _section(self.config, "main")))
        missing = sorted(
            "%s (for %s)" % (option, flag)
            for flag, option in FLAG_TO_UCI.items() if option not in declared)
        self.assertEqual(missing, [], (
            "the shipped ps2servers-edge.config does not define these options, "
            "so they are absent from a fresh install: %s" % ", ".join(missing)))

    def _reads_option(self, option):
        """True if the init script actually consults this UCI option.

        Two spellings count. Scalars are read directly with config_get; booleans
        go through the append_bool_flag helper, which takes the option name as an
        argument. Matching only the first form would report the helper-driven
        options as unread, which is the opposite of the truth.
        """
        direct = r"config_get(?:_bool)?\s+\w+\s+main\s+%s\b" % re.escape(option)
        helper = r"append_bool_flag\s+main\s+%s\b" % re.escape(option)
        return bool(re.search(direct, self.init) or re.search(helper, self.init))

    def test_init_script_reads_every_mapped_option(self):
        unread = sorted(o for o in FLAG_TO_UCI.values() if not self._reads_option(o))
        self.assertEqual(unread, [], (
            "the init script never reads these UCI options, so setting them "
            "would be decorative -- exactly the bug that made read_only do "
            "nothing: %s" % ", ".join(unread)))

    def test_init_script_passes_every_mapped_flag(self):
        # Word-boundary matched: a bare substring check would let --metrics be
        # satisfied by --metrics-period, hiding a genuinely missing flag.
        missing = sorted(
            flag for flag in FLAG_TO_UCI
            if not re.search(r"%s(?![a-z-])" % re.escape(flag), self.init))
        self.assertEqual(missing, [], (
            "the init script reads the UCI option for these flags but never "
            "passes the flag to the binary: %s" % ", ".join(missing)))

    def test_config_has_no_orphan_options(self):
        """Every shipped option must correspond to a flag the binary accepts."""
        declared = set(re.findall(
            r"(?m)^\s*option\s+([a-z0-9_]+)\s+'", _section(self.config, "main")))
        known = set(FLAG_TO_UCI.values()) | {"enabled"}
        orphans = sorted(declared - known)
        self.assertEqual(orphans, [], (
            "these UCI options do not map to any Edge flag, so setting them "
            "does nothing: %s" % ", ".join(orphans)))


class UdpfsFlagsAreDocumented(unittest.TestCase):
    def setUp(self):
        self.flags = _udpfs_flags()

    def test_every_flag_appears_in_the_edge_guide(self):
        doc = _read(EDGE_DOC)
        missing = sorted(f for f in self.flags - DOC_EXEMPT if f not in doc)
        self.assertEqual(missing, [], (
            "docs/EDGE.md never mentions %s, so a user reading the "
            "documentation cannot discover the feature exists" % ", ".join(missing)))

    def test_openwrt_doc_lists_every_uci_option(self):
        doc = _read(OPENWRT_DOC)
        missing = sorted(o for o in FLAG_TO_UCI.values() if o not in doc)
        self.assertEqual(missing, [], (
            "docs/OPENWRT.md does not mention these UCI options, so an operator "
            "has no way to learn they exist: %s" % ", ".join(missing)))

    def test_openwrt_doc_config_block_matches_the_shipped_file(self):
        """The doc quotes the config verbatim; a drifted quote teaches a wrong key."""
        shipped = set(re.findall(
            r"(?m)^\s*option\s+([a-z0-9_]+)\s+'", _section(_read(UCI_CONFIG), "main")))
        documented = set(re.findall(
            r"(?m)^\s*option\s+([a-z0-9_]+)\s+'", _section(_read(OPENWRT_DOC), "main")))
        self.assertEqual(shipped, documented, (
            "the config block in docs/OPENWRT.md has drifted from the file the "
            "package actually installs: only in the file %s; only in the doc %s"
            % (sorted(shipped - documented), sorted(documented - shipped))))


if __name__ == "__main__":
    unittest.main()
