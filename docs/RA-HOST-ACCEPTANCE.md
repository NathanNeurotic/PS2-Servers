# RetroAchievements integration host acceptance

This records PR #222 host evidence. It is not physical-console acceptance and
it does not complete the independent-runtime work in issue #223.

## Package provenance

Manual workflow run 38010605442 built commit
`d5649c3e45a5790d5308f2dced2c52f00a38c488` without publishing a release.
All five platform build jobs passed. Windows x86 and x64 onefile packages
passed the fresh-profile desktop startup gate. The downloaded Windows x64
portable and Windows x86 onefile ZIP checksums matched their adjacent SHA256
files. Both packages passed `tools/check_achievements_package.py` for xeRAbora
and Caduceus modes: startup, UDP discovery, offline paired response, duplicate
rejection, supervisor shutdown, socket release and profile-lock release.

Later commits isolate GUI invitations and kernel mutex tests, extract the
unchanged default mutex name into a constant, and add decoder/integration
regression coverage and documentation. They are not included in that package
run. Final-head CI remains a separate gate.

## Real native dependencies and integrations

- Built libchdr at the source pin in `build/build_libchdr.py`.
- Generated a synthetic DVD CHD with checksum-verified official MAME 0.289
  chdman. The real libchdr decoded all 49,152 bytes to the original ISO fixture.
  Independent BOOT2+ELF MD5, native rcheevos ISO hash and compressed CHD hash
  matched: `4c3d78a0558915d533693a8283fab3a2`. The fixture and provenance are in
  `tests/fixtures/ra_hash/`; CI now verifies decoding after building libchdr.
- Built the pinned native RA engine. The native host suite exercised restart,
  mode changes, discovery, private credentials, pairing and shutdown without
  logging into RA or submitting achievements.
- Native OBS text/JSON exports updated between two **synthetic** telemetry
  serials using a folder containing spaces. This proves export plumbing, not
  game identification, achievement evaluation or a legitimate unlock.
- The read-only viewer served actual native-engine state over loopback and
  rejected POST logout. Separate wire tests cover foreign origins and hosts,
  unlisted routes and connection bounds. A phone over LAN remains untested.
- The actual Discord desktop returned READY, acknowledged a generic
  PS2-Servers desktop activity and acknowledged immediate activity clearing.
  No game title, account, address, path or credential was published. A real
  packet-verified game's presentation remains an acceptance check.

## Remaining acceptance

- Interactive packaged Library, native overview, sound picker/reset and
  setup-guide behavior beyond automated startup/headless package checks.
- Commercial CHD variants and representative large/multi-track images;
  actual compressed LZ4 ZSO blocks beyond current raw-block fixtures.
- Real-account sign-in, progress, leaderboards and a legitimate softcore unlock.
- Matching console/loader PS2 and PS1 POPStarter behavior, Caduceus notices,
  pairing, disconnect/reconnect/IGR, and gameplay unaffected.
- Independent PS2-Servers runtime and full native account/UI remain #223;
  this integration still supervises the xeRAbora-derived engine.
