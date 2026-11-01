# Companion capability matrix (#249)

This records what PS2-Servers does today for each companion workflow in
[#249](https://github.com/NathanNeurotic/PS2-Servers/issues/249). For each one
it gives the status, the code and tests behind it, and the strongest evidence
we have. It is the P0 baseline the issue asks for, kept in the repository so it
changes in the same commit as the code.

**Evidence levels.** *Advertised* means the docs or a reference describe it.
*Host-tested* means an offline test or conformance run on a PC passes.
*Hardware* means someone checked it on a PS2. Nothing below is at the hardware
level yet. **Every row is NOT TESTED on a PS2.**

## Pinned sources

| Source | Commit | Used for |
|---|---|---|
| PS2-Servers `main` | `d862ffa` (#261 merge) | Everything in this file |
| [RiptOPL](https://github.com/NathanNeurotic/RiptOPL) `rebuild/main` | `4ec6c68` (2026-10-31) | Ember layout (`include/cuesupport.h`, `src/sources/cuesupport.cpp`), PNG/CLUT alpha (`src/gfx/textures.cpp`), network keys (`include/config.h`, `include/opl.h`, `src/app/settingsio.cpp`) |
| [Open-PS2-Loader](https://github.com/NathanNeurotic/Open-PS2-Loader) | `bfebafdd` | HTTP list parser `src/httpcatalog.c`, compiled by `conformance/integration/riptopl_http/run.py` |
| [PS2TVCRT / FastLoader](https://github.com/NathanNeurotic/PS2TVCRT) README | `1844bd1` | Feature targets only (its claims are not hardware results) |

## Shared fixture library

[`tests/library_fixtures.py`](../tests/library_fixtures.py) builds one synthetic
games root covering every case #249 lists. [`tests/test_companion_fixtures.py`](../tests/test_companion_fixtures.py)
runs each consumer over that same tree: the catalogue scan and inspection, the
shared index, both HTTP list profiles, the achievements library and the PS1
descriptors. The cases are:

- standard `CD/` and `DVD/`;
- a root-level ISO;
- a game four folders deep, and one five deep that the depth limit leaves out;
- two images with the same basename in different folders;
- two images with the same inspected serial;
- a UTF-8 name with a space and a comma;
- CHD, CSO and ZSO containers;
- a truncated image;
- a PS1 VCD and an Ember folder, plus a folder Ember would not list;
- an unreadable folder, which is checked only when the test is not run as root;
- a link that points out of the root;
- 1,000 images, to check the entry and size budgets.

## Workflows

| Workflow | Status | Code | Tests | Evidence |
|---|---|---|---|---|
| Recursive discovery, opt-in, to 4 levels, no link following | present | `launcher/library_catalog.py` `scan_detailed` | `test_library_recursive_scan.py`, `test_companion_fixtures.py` | host-tested |
| PS2 boot serial from `SYSTEM.CNF` (ISO/CSO/ZSO/CHD), unknown when unreadable | present | `launcher/disc_identity.py`, `Catalog.inspect_identities` | `test_disc_identity.py`, `test_library_identity.py`, `test_companion_fixtures.py` | host-tested |
| Stale serial withheld after an image changes | present | `Catalog._current_identity` | `test_library_identity.py` | host-tested |
| Shared read-only index for the Desktop and achievements libraries | present | `launcher/library_index.py`, `Catalog.indexed_games` | `test_library_index.py`, `test_game_library.py` | host-tested |
| Duplicate basename, serial, case and Unicode collisions reported, not collapsed | present | `launcher/library_index.py` | `test_library_index.py`, `test_companion_fixtures.py` | host-tested |
| PS1 VCD and Ember descriptors kept apart from PS2 identity | partial | `launcher/ps1_index.py` (read-only; not shown in the Library tab yet) | `test_companion_fixtures.py` | host-tested |
| HTTP legacy flat list, the default and unchanged | present | `http_server/http_server.py` | `test_http_server.py`, `conformance/integration/opl_http` | host-tested |
| HTTP RiptOPL path-aware list (`--catalog-profile riptopl-path-aware`) | present | `http_server/http_server.py` | `test_http_path_aware.py`, `conformance/integration/riptopl_http` | host-tested against the client's own C parser |
| Artwork import and Repair ART into 8-bit palette PNG | present | `Catalog.write_art`, `migrate_legacy_art` | `test_library_catalog.py` | host-tested |
| Artwork transparency kept (tRNS) | present | `_artwork_palette_image` | `test_library_catalog.py` | host-tested; RiptOPL reads tRNS as CLUT alpha per `textures.cpp` |
| Missing-art download from a URL the user supplies | partial | `launcher/art_fetch.py`, Library **Download missing art** | `test_art_fetch.py`, `test_library_panel.py` | host-tested; one art type per run, no per-image preview |
| Built-in artwork provider | intentionally missing | none | none | No provider with clear rights to retail box art was found |
| Library root bound to server profiles (opt-in, with preview and rollback) | missing | none | none | none |
| Guided setup | present | `launcher/setup_guide.py` (read-only text guide) | `test_setup_guide.py` | host-tested |
| RiptOPL settings export (versioned, read-only) | present | `launcher/riptopl_profile.py`, `launcher/riptopl_export.py`, ABOUT **RiptOPL settings…** | `test_riptopl_profile.py` | host-tested; keys checked against RiptOPL `settingsio.cpp`; SMB2/SMB3 refused |
| Cross-service session telemetry | partial | Shared `SESSION` line format (`launcher/session_stats.py`), opt-in (`--session-summary`) on HTTP, SMBv1, UDPFS (Desktop/Core) and UDPBD; final lines also on Windows Stop; UDPFS stall report | `test_session_summary.py`, `test_session_view.py`, `test_cooperative_stop.py`, `test_udpfs_stall_report.py` | host-tested; the launcher's SESSIONS tab shows the lines from every service; SMBv2/SMBv3 and Edge in review |
| Offline VMC creation | intentionally missing for now | none | none | RiptOPL already creates per-game VMCs; HTTP and virtual exFAT stay read-only |

## Known limits found by the fixture

- **One row per STARTUP on the path-aware list.** RiptOPL keeps the first row
  for a STARTUP and drops the rest. STARTUP still comes from the filename, so
  two images with the **same basename** in different folders get the same
  STARTUP, and only the first one is listed. The second is logged and not
  served. Fixing this needs the client and server to agree to key a row on
  something other than the filename (OPL #987). The server cannot fix it alone.
- **Media for nested images is a size guess.** Folders outside `CD/` and `DVD/`
  carry no media, so the list falls back to the size rule. The inspected disc
  type is not used yet.
- **The legacy flat list and the achievements library stay top-level.** This is
  on purpose: their clients request flat paths.

## Per-loader transport needs

| Loader | What it needs from PS2-Servers | Status |
|---|---|---|
| RiptOPL (native OPL modes) | SMBv1 share, UDPFS, UDPBD, or the HTTP path-aware list | Desktop/Core serve all of them; Edge has no HTTP game serving |
| Neutrino | Its own SMB/UDPBD support; SMB is being added in a separate thread | out of scope here |
| POPSTARTER (PS1 VCD) | `POPS/<name>.VCD` on a device POPSTARTER can read | VCDs are indexed by `ps1_index.py`; serving is unchanged |
| Ember (PS1 `.cue`/`.bin`) | `EMBER/games/<name>/` with a disc at its top level | Indexed by `ps1_index.py`; serving is unchanged |
| Docmine17 OPL-HTTP (legacy) | Flat `STARTUP,FILENAME,MEDIA` list, 8192 bytes | Unchanged default |
