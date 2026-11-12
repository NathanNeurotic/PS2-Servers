<!--
Shared changelog for published releases. Keep this short: the release page is a
changelog and a download table, not another copy of the operator manual.
-->

## Changelog

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
