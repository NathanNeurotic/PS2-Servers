# ORBIT Neutrino compatibility audit

Reference: Daniel (`danielnuld`)'s [ORBIT v1.0.2](https://github.com/danielnuld/ps2-launcher/releases/tag/v1.0.2),
tag commit `7a88a6a5fdc935001d977497460716762edef404`. Audited default-branch
head `28a23666fbc8b11b5edabcfff0f780b2b2161219` is one merge commit ahead with
no changed files. This is an additional console family, not an OPL revision.

## Release identity and provenance

The release ZIP was downloaded and its SHA-256 matched GitHub asset metadata:
`a5cf88e2f27d3745b01c93d6d691b162972749b35452a38461046a02b3f178a5`.
Size: 2,087,748 bytes. It contains `neutrino/neutrino.elf`,
`neutrino/modules/ee_core.elf`, `neutrino/modules/raagent.irx`, the Neutrino
license and the ORBIT launcher license. This verifies published binary identity,
not a reproducible build or PS2-Servers console acceptance. No ORBIT binary or
launcher source is bundled by this change.

ORBIT's README names xeRAbora **alpha.9** as the tested companion. Its author
reports a physical SCPH-75001 test with Black over UDPFS, approximately 64
snapshots per second and concurrent game reads. These are upstream-reported
results for that configuration, not validation of our alpha.16-derived engine,
RiptOPL, Caduceus mode, every game or other storage paths.

Sources: [README](https://github.com/danielnuld/ps2-launcher/blob/v1.0.2/README.md),
[console results](https://github.com/danielnuld/ps2-launcher/blob/v1.0.2/docs/phase17-results.md).

## Confirmed source contracts

| Surface | ORBIT v1.0.2 behavior | PS2-Servers evidence / action |
| --- | --- | --- |
| Identification / list transfer | Launcher uses RAP1, RAQ1 and RAG1 to UDP 18194, advertises its own port 18194, receives 896-byte RAC1 chunks | Owned parser/receiver fixtures pass; native account-backed transfer remains an acceptance gate |
| In-game discovery | Agent advertises UDP 18195 from the same console IP after the launcher transfers the list | Fixture confirms owned receiver preserves the installed session and replies to the agent port when handoff completes within the lease |
| Telemetry | Fixed-width RA15, timing fields after pt/np, final 15-character tilde-padded serial; pt=0, np=1 | Source-derived header and maximum supported value payload parse successfully |
| Snapshot ceiling | Agent explicitly skips values above `1472 - 256 = 1216` bytes; no multipart send path | Explicit `validate_orbit_watchlist` rejects larger complete sets; never truncate or change the general OPL ceiling |
| Pointer chains | EE reader explicitly implements direct addresses only; optional RANL tail is not resolved | Explicit profile rejects any chain-bearing set rather than promise full-set compatibility |
| Notices / resets | Agent receive handler recognizes RAO1 only; RAH1 carries diagnostic fields | No source-backed in-game unlock card, badge or remote-reset support; do not infer them from shared packet names |
| Achievement list browser | ORBIT requests `/game?id=<id>` over HTTP at companion port 18280 | Missing compatible LAN endpoint; our account interface remains loopback-only. Add a separately scoped read-only adapter before claiming browser parity |
| Launcher handoff | Fetch list before teardown, write `.wl` and `config/ra.toml`, pass `-cfg=ra` and `-ra=<path>` | This belongs in the console launcher adapter; desktop support alone cannot activate the agent |
| UDPFS module order | Reuse the game's SMAP/ministack, load agent at order 30 after the network stack | Preserve shared network ownership; do not reload a competing stack |
| Non-quick boot | Watch list read before IOP reset | Validate the actual launcher/Neutrino path; a path string surviving reset does not prove accessible storage |

Sources: [achievements.c](https://github.com/danielnuld/ps2-launcher/blob/v1.0.2/achievements.c),
[launcher.c](https://github.com/danielnuld/ps2-launcher/blob/v1.0.2/launcher.c),
[agent sender](https://github.com/danielnuld/ps2-launcher/blob/v1.0.2/third_party/neutrino-igr/raagent/src/main.c),
[EE reader](https://github.com/danielnuld/ps2-launcher/blob/v1.0.2/third_party/neutrino-igr/ra.c).

The generic RAP1 handshake contains no loader capability/version identifier.
Selecting the ORBIT limits must therefore be explicit; do not guess the family
from a source port or apply its limits to every OPL client. The owned profile
validator is not yet wired into active native runtime routing or an exporter.
The receiver fixture uses synthetic worker results, no account and no unlock.

## Broader feature inventory and next work

Track outside features as separate contracts with implementation, host,
package and console evidence. Keep a working runtime while filling gaps:

1. Validate ORBIT wire fixtures against our native runtime and an actual
   account-backed set, without synthetic unlock submissions. Cover launch
   delay beyond the ownership lease, reconnect and stale-game rejection.
2. Design the read-only LAN `/game` adapter separately from account mutation,
   credentials and the loopback UI. ORBIT's existing request carries no pairing
   token; define explicit activation and access scope before service routing.
3. Coordinate an explicit ORBIT-Neutrino launcher profile with RiptOPL. Pin and
   credit the existing runtime; retain normal Neutrino paths. Do not create a
   new fork merely to reproduce this implementation. Console acceptance must
   include UDPFS stack sharing and list lifetime across IOP reset.
4. Investigate ORBIT's catalog-assisted hashing/listing and remote VMC workflow
   as additional server features. Audit `server/orbit_catalog.py` and its
   consumer before promising endpoint or persistence compatibility. They are
   distinct from RA transport and remain unaudited here.
5. Extend the existing OPL/Caduceus/PS1/POPStarter matrix with exact runtime
   versions and capability limits. General game loading, hashing, account
   browsing, achievement evaluation and on-console notices are separate rows.

Preserve Daniel/ORBIT, Maximus32/Neutrino and hacan359/xeRAbora attribution.
ORBIT identifies its launcher as GPL-3.0 and its Neutrino fork as AFL-3.0;
its copied protocol headers retain their MIT provenance. Review distribution
requirements against exact files if a later change bundles binaries or code.
