# Compression fixture provenance

`test_cd.chd`, `test_dvd.chd`, and `test.iso` come from
`pcm720/udpfsd/internal/fs/compression/test/` at commit
`ee94414f9f45ed33b8dd4ca4e03847440df946e9`.

They are unmodified test data used to verify DVD and CD CHD decoding against the
reference ISO. `test_cd.chd` is stored as 544 CD frames, while `test.iso` contains the first 542.
The final two frames are zero-filled hunk padding that chdman added. The pinned
reference exposes the hunk-rounded 544-sector view. Core and Edge deliberately
differ: they read the track table (FRAMES:542) and end the ISO where the disc
ends, so the view is exactly `test.iso`. The tagged tests check every decoded
byte and the EOF.

The upstream project is Copyright 2026 Ivan V (pcm720), under the BSD license
reproduced in the repository-root `THIRD_PARTY_NOTICES.md`.
