# Consolidated desktop test candidate

This candidate combines the desktop startup repair, the general Library tab,
and the RetroAchievements feature branch. Keep an existing working download
and back up launcher settings before testing. It remains a prerelease; host
checks do not prove console compatibility or legitimate achievement unlocks.

## Desktop and server regression

- Extract the complete package into a new folder. Launch from Explorer, finish
  or dismiss the setup guide, close, and reopen. The app must stay open.
- Confirm existing settings remain intact. Reopen the setup guide manually.
- Test each server mode you use: SMBv1/v2/v3, UDPFS, HTTP and UDPBD. Confirm
  discovery/game listing, a known-good game launch, sustained gameplay, Stop,
  restart and exit. Record the loader version and storage/transport used.
- For UDPBD, first test ordinary image or virtual-exFAT operation. Physical-drive
  write/exclusive options are separate opt-ins; never use a valuable drive as a
  disposable test target. Confirm reads, reconnects and expected save behavior
  only in configurations you already intend to exercise.

## Library and achievement catalogue

- Open the general LIBRARY tab and the achievement catalogue through
  RetroAchievements > Manage game library. Both must open with the same profile.
  Their metadata databases are separate; image folders can be shared.
- Scan an existing CD/DVD layout, search, edit metadata, back up and restore/import
  catalogue metadata. Confirm original images and existing custom covers survive.
- Import a disposable image; try the same destination twice. The second operation
  must refuse overwrite. Cancel a transfer; no incomplete image should be listed.
- Exercise a direct image download and cancel it. HTML/error pages must not be
  installed as game images.
- Import a Caduceus JSON or SQLite catalogue, scan ISO achievement compatibility,
  select recognized/unmatched filters, and reopen to verify saved preferences.
- Repair a selected cover and verify the indexed PNG on the console. Existing
  custom artwork must remain intact. Catalogue backups contain metadata, not ISOs
  or memory-card saves.

## RetroAchievements: test each mode separately

- Select xeRAbora or Caduceus and export the matching console loader. Preserve the
  exported license/source manifest. Do not assume one ELF supports both modes.
- Start RetroAchievements, open its local account page, sign in and configure
  the required account/API credentials. Confirm a wrong credential reports an
  error without terminating game serving.
- Test console discovery, Caduceus pairing where applicable, game identification,
  live progress, a legitimate softcore unlock, and console notifications.
- Check library/game-set/leaderboard/follow pages. Record unsupported-loader or
  transport limitations separately from desktop/server failures.
- Stop and restart the achievement service; verify its UDP ports are released.
  Exit PS2-Servers and verify it leaves no achievement helper running.
- Hardcore mode is not enabled by this integration. Sound currently has a mute
  toggle; volume/custom-sound controls remain outside this candidate's completed
  parity claims.

## Optional integrations

- Discord is disabled by default. Enable it under Desktop settings using the
  official default Application ID 1558114313619898409. Start/stop server modes and
  verify activity updates, optional uptime, reconnect after closing Discord, and
  activity clearing on application exit. No file paths, IPs or credentials may
  appear. No bot token or OAuth login is required.
- Choose an OBS export folder, including a path with spaces. Verify text/JSON
  updates during a real session and inspect them in OBS.
- Explicitly enable the LAN viewer and open it on a phone. Verify live state;
  account-changing requests must remain unavailable remotely. Disable/stop and
  confirm the viewer closes.

## Report evidence

For a failure, record the candidate's About/build identifier, OS, package type,
loader/ELF version, server mode and storage layout, exact action, expected versus
observed behavior, and the relevant Terminal output. Redact credentials and
private paths before sharing. A passing desktop test does not replace the PS2
session checks above.
