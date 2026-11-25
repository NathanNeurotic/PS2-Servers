<!--
Shared changelog for published releases. Keep this short: the release page is a
changelog and a download table, not another copy of the operator manual.
-->

## Changelog

- Windows 7 and 8: the 32-bit package (`PS2Servers-windows-x86`) starts there now; it is built with Python 3.8, the last Python that runs on Windows 7. Before, it stopped at launch asking for api-ms-win-core-path-l1-1-0.dll, then PssQuerySnapshot (#330). The 64-bit packages still need Windows 10 or 11.
- SMBv1: the console can delete files and empty folders and rename them (#334). Not on read-only shares, outside the share or on the share root; a rename never replaces an existing name, and a file or folder that is open answers "in use".
- SMBv1: folder listings send many entries per reply (up to 2 KiB) instead of one per round trip (#335), so big folders list faster. RiptOPL builds from PR 540 on use it for faster Ember lists. A client that asks for one entry at a time gets the same reply as before.
- SMBv1 testing: delete and rename are host-tested; batched listings were checked in PCSX2 with RiptOPL. Neither has been run with a real PS2 yet.

### Earlier, in v0.9.5

- RetroAchievements: caught up with xeRAbora 2.1 and 2.2. When RiptOPL says which game is running, the PC loads its set by itself, even if you never ran "check game support" on that PC.
- RetroAchievements: achievements that read memory through Remember/Recall now work, and big sets can use up to 512 pointer chains (256 for PS1) in RAN2 mode. RANL mode keeps its 128.
- RetroAchievements: the first check of a new game no longer times out while the console downloads its list.
- RetroAchievements: optional RAN2 watch lists (xeRAbora v2). RANL stays the default.
- RetroAchievements: test mode, where nothing is sent to RetroAchievements (no unlocks, leaderboards or sessions), and a local diagnostics export for bug reports.
- Discord: optional game details and RetroAchievements profile link.
- macOS: the release no longer hangs on the RetroAchievements check (the account page did a slow network name lookup at startup).

### Earlier, in v0.9.4

- SMBv1 security fix: when a password is set, shares are refused until a login succeeds. Before, a client could skip the login and reach the shares. SMBv2/SMBv3 already checked it. Servers started without a password behave as before.
- SMBv1: a read right after a write now sees the new bytes (it could return the old ones), which matters for memory-card images and other files the console writes then reads back.
- SMBv1: optional "Decompress CHD/CSO" serves Game.chd and Game.cso as Game.iso, read-only, using the UDPFS decoders. Off by default; not yet tested on a console. Idea and first version by [snapsapnap2](https://github.com/snapsapnap2) (#308).
- CD CHD images now report their true disc length instead of a hunk-padded size (for example 1003 sectors, not 1008) on UDPFS, HTTP, SMBv1 and Edge. DVD CHDs are unchanged.

### Earlier, in v0.9.3

- NFS: a new NFS card serves a games folder over NFS 4.0 (TCP 2049) for Neutrino's NFS backing store and the nfs: driver, both built on [Ronnie Sahlberg](https://github.com/sahlberg)'s unfs4. Writes are off unless "Allow writes" is on. See docs/NFS.md.
- NFS testing: checked against unfs4 (the client Neutrino uses) in CI only. Not yet run with a real PS2.
- SMBv2/SMBv3: the cards take a Share name like the SMBv1 card, so the console can open a share not called "games". A blank name still means "games".
- SMBv2/SMBv3: Start refuses a card with a username but no password, and says what to set, instead of starting and exiting.
- Portable builds keep settings, library data and the RetroAchievements profile in a data folder next to the program (settings from the per-user folder are read once).
- Desktop: "Auto-start servers on launch" works again; quitting no longer forgets which servers were running.
- Desktop: firewall removal stops servers only after the last confirm; the Library tab opens the shared setup guide and reports a missing Pillow instead of failing; dropdowns ignore the mouse wheel; the Terminal tab fits the window with one scroll bar.
- Conformance: the Neutrino SMB check runs against usmb2 bf61d1d.
- Credits: Ronnie Sahlberg for unfs4, usmb2 and libsmb2.

### Earlier, in v0.9.2

- Desktop: Verbose logging is now on by default, and turned on once for existing settings (turn it off and it stays off). Terminal lines carry an HH:MM:SS time.
- Desktop: the last open tab is remembered between runs; the Minimize option is labelled "Minimize to tray (off = taskbar)".
- UDPFS: virtual CD CUE/BIN images from CHD files.
- UDPFS: concurrent clients, root-relative paths, and handle-preserving restart/rediscovery.
- Compression: collision-free aliases, raw containers, and optional Edge CHD decoding.
- Edge: block-device-only service and optional Linux UDP batching.
- Conformance: three-server differential host validation and expanded fault coverage.
