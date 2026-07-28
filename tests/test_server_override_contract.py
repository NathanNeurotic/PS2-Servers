"""Pin which UdpfsServer methods ps2servers_core replaces.

There are two UDPFS server classes in this repository and only one of them runs:

* ``udpfs_server.UdpfsServer`` -- redistributed from Rick Gaiser's Neutrino host
  tools (see NOTICE.md) and still a documented standalone entry point in the
  README, so it stays as it is.
* ``ps2servers_core.AutoUdpfsServer`` -- subclasses it, replaces the global
  Modulo switch with per-session negotiation, and is what the launcher registry
  actually points at.

Editing an overridden method in the base class alone therefore changes nothing
on the shipped path. That is not hypothetical: a discovery log line was added to
``udpfs_server.py`` first, the server ran, the tests passed, and the line never
appeared, because ``AutoUdpfsServer._handle_discovery`` shadows it.

These tests do not forbid overriding. They make the set of overrides an explicit,
reviewed list, so adding a seventh is a deliberate act rather than something a
future reader discovers the hard way.

Run:  python -m unittest tests.test_server_override_contract -v
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UDPFS_DIR = os.path.join(ROOT, "udpfs_server")
for path in (ROOT, _UDPFS_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)

import ps2servers_core  # noqa: E402
import udpfs_server  # noqa: E402

from launcher.servers import REGISTRY  # noqa: E402


# Every method AutoUdpfsServer replaces. Changing this set is a real decision:
# whatever is listed here must be edited in ps2servers_core.py, because editing
# the udpfs_server.py copy will not affect the launcher, the packaged app, or
# anything a user runs through the GUI.
EXPECTED_OVERRIDES = {
    "__init__",
    "_get_or_create_session",
    "_handle_data",
    "_handle_discovery",
    "_sendto",
    "run",
}


def _own_methods(cls):
    """Methods defined directly on cls, ignoring anything inherited."""
    return {
        name for name, value in vars(cls).items()
        if callable(value) and not isinstance(value, (staticmethod, classmethod))
    }


class OverrideContractTests(unittest.TestCase):
    def test_override_set_is_exactly_what_is_documented(self):
        base = _own_methods(udpfs_server.UdpfsServer)
        core = _own_methods(ps2servers_core.AutoUdpfsServer)
        actual = base & core

        unexpected = actual - EXPECTED_OVERRIDES
        self.assertFalse(unexpected, (
            "ps2servers_core.AutoUdpfsServer now also overrides {}. That is fine, "
            "but add it to EXPECTED_OVERRIDES here so the next person editing "
            "udpfs_server.{} knows their change will not reach the launcher."
        ).format(sorted(unexpected), sorted(unexpected)[0] if unexpected else ""))

        missing = EXPECTED_OVERRIDES - actual
        self.assertFalse(missing, (
            "{} is no longer overridden in ps2servers_core. If that is "
            "intentional the base implementation is now live on the shipped "
            "path -- confirm it is the behaviour you want, then remove it here."
        ).format(sorted(missing)))

    def test_the_launcher_runs_the_core_entry_point(self):
        # The whole hazard rests on this: the registry decides which file is
        # loaded, and it is the core, not the base module.
        udpfs = REGISTRY["udpfs"]
        self.assertEqual(
            os.path.basename(udpfs.module_file), "ps2servers_core.py",
            "the UDPFS registry entry no longer points at ps2servers_core.py; "
            "the override contract in this file assumes it does")

    def test_core_subclasses_the_base_rather_than_reimplementing_it(self):
        # If this ever stops being true the two servers have genuinely forked
        # and this contract cannot express the relationship any more.
        self.assertTrue(
            issubclass(ps2servers_core.AutoUdpfsServer, udpfs_server.UdpfsServer),
            "AutoUdpfsServer no longer subclasses UdpfsServer")


if __name__ == "__main__":
    unittest.main()
