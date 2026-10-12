# Owned RetroAchievements runtime implementation

The compatibility contract starts in [the matrix](RA-OPL-COMPATIBILITY.md).
Issue [#223](https://github.com/NathanNeurotic/PS2-Servers/issues/223) remains
open. The active service still supervises the xeRAbora-derived engine.

## First implementation slice: passive protocol primitives

`launcher/ra_protocol.py` is an independent Python implementation of bounded
base request parsing, RAC1 watch-list chunk encoding and RA15 snapshot assembly.
It does not open sockets or access an account, does not submit unlocks, and is
not selected by the supervisor. Reference behavior comes from the pinned
console sender at `531aad4584d65d1779b744739b81402150c7669c` and the vendored
protocol headers/client; existing upstream notices remain intact.

Implemented behavior:

- Decode full RAP1, RAQ1 and RAG1 requests, plus abbreviated RAQ1/RAG1 forms;
  use a supplied source endpoint when the advertised address is unusable.
- Encode 896-byte binary watch chunks with base-protocol space padding,
  minimum 128-byte replies and 64-byte alignment. Caduceus NUL padding remains
  a separate contract.
- Parse fixed-width RA15 fields without searching binary values for fields;
  bound datagrams, counts, parts and assembled values.
- Retain at most one frame, keyed to an explicit serial/watch-count session;
  accept reordered fragments, suppress repeated completed frames, reject
  conflicting duplicates and inconsistent dimensions, and discard delayed
  older frames without replacing a newer in-flight frame.
- Accept configured direct-only or pointer-tail value sizes, use six-digit
  sequence wrap semantics from the sender's decimal formatter, and recover
  from an idle console restart. Duplicate replay does not extend the timeout.
- Validate immutable RAWL v1 files and RANL tails: read sizes, entry totals,
  node dependencies, exact lengths and snapshot capacity. Preserve legal
  out-of-RAM addresses, which the console reads as zero.
- Bind validated lists to image hash/serial generations with `WatchSession`;
  reinstalling a list always resets assembly, and explicit clearing revokes
  both transfer and evaluation. Failed replacement validation is atomic and
  retains the previous session; callers must clear first on a game/account
  transition. These primitives are still not routed into the active engine.

These are reference-derived/adversarial host tests, not complete differential
execution against the native engine or physical-console proof. In particular,
joining reordered parts after discovering their real sizes improves on a
reference assembler path that initially assumes a default chunk size; real
loader acceptance still needs to confirm recovery under packet loss.

## Required next slices

The transport-independent `launcher/ra_receiver.py` now owns a single-console
source-IP lease, explicit advertised/source-only reply policy, one pending
identification job with generation tokens, WAIT/NO/OK states, watch transfer
gating, timeout/retry and explicit account/reset revocation. Control requests
are limited separately from a 256-datagram/second total budget so normal
telemetry is not capped at 40 packets/second. Tests use an injected clock and
synthetic resolution results; no account or socket is used.

An external event loop must serialize receiver calls and marshal worker results
back to the owner. The opt-in `launcher/ra_owned_service.py` now supplies a UDP
owner thread, one resolver worker, one queued job/result and one latest-frame
slot. Loopback tests cover reception during a blocked lookup, stale game
results, transient failures, reset, bind conflicts and listener release.
`stop()` reports an outstanding callback and restart refuses to accumulate
workers. External identification uses `ProcessResolver`: one spawned child
per lookup, a hard wall-clock deadline, bounded result IPC, cancellation on
account reset/game switch/shutdown, and terminate/kill followed by reaping.
Spawn fixtures deliberately ignore cancellation and prove child cleanup and
subsequent lookup/restart. A child that cannot be reaped blocks further work
on that adapter. Direct callbacks remain available for bounded host fixtures;
they cannot be forcibly stopped. Production resolvers must be module-level
importable callables with picklable state and must not perform unlock side
effects. Both application entry points call `freeze_support` before launcher
initialization. Windows standalone build `3f1e7e7` passed the actual EXE's
`--selfcheck-ra-resolver` probe (result delivery, hard timeout, child reaping),
both existing packaged RA-mode smoke checks, and fresh-profile desktop startup.
Use `OwnedService(ProcessResolver(resolve_set, timeout=10), ...)` for external
identification; passing `resolve_set` directly selects the cooperative thread
path and does not supply hard cancellation. The resolver integration must
retain the process adapter. No owned account/evaluator route is activated yet.
Real account resolution, evaluator integration and active-service selection
remain unimplemented. Source-IP
ownership is an unauthenticated lease, not a security identity, and cannot
distinguish multiple consoles behind the same translated source address.
Foreign-source traffic is rejected before charging the owner's packet budget;
same-source traffic can still contend. Measure and refine scheduling before
enabling the runtime. Completing transfer is a
local gate, not proof the console installed the list or began a new launch.

1. Build differential native/reference fixtures and complete historical loader
   variant coverage. Abbreviated requests and endpoint fallback now have host
   tests; verify routing against reference behavior before claiming parity.
2. Own the watch-list lifecycle: derive/validate direct entries and pointer
   nodes through rcheevos, bind lists to image hashes/sets, and invalidate them
   across title, account and connection changes. Structural validation and
   generation binding exist; rcheevos derivation and receiver wiring remain.
3. Add a receiver with explicit peer ownership, NAT reply policy, bounded
   queues/rates, discovery, asynchronous identify WAIT/NO/OK responses, cleanup
   and reconnect. The assembler must be reset on peer or watch-list changes;
   serial and count alone cannot distinguish all list changes.
   RA15 has no generation identifier, so delayed pre-transition telemetry
   requires a handshake/launch boundary as well as clearing local fragments.
4. Integrate rcheevos evaluation and legitimate account/API/session handling,
   retaining secure private storage, without real unlock submissions in CI.
5. Integrate native UI, exports and compatibility adapters; compare outputs and
   packaged lifetime behavior before enabling the replacement service.
6. Complete exact-artifact console acceptance from the matrix. Do not remove
   the working runtime until the replacement meets those gates.
