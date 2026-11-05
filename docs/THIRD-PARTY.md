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

No fork relationship or upstream Git history was imported. The Edge source
tree, CLI, documentation, packaging, and session
implementation were not copied wholesale from `udpfsd`.

The optional CHD backend and its minimal C declarations are specifically
adapted from `pcm720/udpfsd` at `ee94414f9f45ed33b8dd4ca4e03847440df946e9`.
The CD/DVD CHD and reference ISO fixtures under Edge's compression `testdata`
come from the same revision. The adapted files identify their origin, and the
full BSD license is included in
[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md). No upstream Git history or
wholesale server implementation was imported. The optional Linux UDP batch
writer in `internal/udpfs/udpwrite_linux.go` is a bounded adaptation from the
same reference, with cached socket state and Edge's own fallback semantics.

The Linux batch path uses `golang.org/x/net` (and `golang.org/x/sys`
transitively), whose Go Authors license is reproduced in
[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).

The CSO implementation uses Go's standard-library zlib support. The ZSO decoder
implements the published raw LZ4 block format with original bounded code; no
external LZ4 source is vendored. Go standard-library notices are supplied by the
Go distribution and release provenance tooling.
