# Third-party provenance

PS2 Servers Edge is a first-party PS2-Servers component with its own command
surface, package boundaries, session manager, tests, deployment files,
documentation, and release workflow.

The implementation was informed by:

- the existing PS2-Servers Python UDPFS implementation and its packet tests;
- the public Neutrino UDPFS/UDPRDMA protocol documentation and client behavior;
- observed Modulo compatibility behavior recorded in PS2-Servers history;
- `pcm720/udpfsd`, reviewed as a permissively licensed technical reference for
  protocol behavior, platform targets, and the practical CHD/CGO boundary.

No fork relationship or upstream Git history was imported. No issue, pull
request, discussion, comment, or other contact was made in any `pcm720`
repository. The Edge source tree, CLI, documentation, packaging, and session
implementation were not copied wholesale from `udpfsd`.

No source file from `pcm720/udpfsd` is currently copied into Edge. Its BSD
license is nevertheless included in `THIRD_PARTY_NOTICES.md` to preserve clear
provenance for reviewers and for any later, specifically documented adaptation.
If future work adapts code, the affected files must identify the adapted region
and preserve the applicable copyright and license notice.

The CSO implementation uses Go's standard-library zlib support. The ZSO decoder
implements the published raw LZ4 block format with original bounded code; no
external LZ4 source is vendored. Go standard-library notices are supplied by the
Go distribution and release provenance tooling.
