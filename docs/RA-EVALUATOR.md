# Owned rcheevos evaluator integration

The owned host now evaluates offline achievement conditions through the pinned
rcheevos **public C runtime API**. It does not compile or invoke the xeRAbora
desktop application for this path. This is an intermediate implementation for
#223; the existing managed achievement service remains selected.

## Native boundary

`build/build_ra_evaluator.py` builds `rcheevos-owned.dll`,
`librcheevos-owned.dylib` or `librcheevos-owned.so`. It shares the existing
pinned native toolchain selection and target architecture. Sources are only
rcheevos runtime, utility/compatibility and MD5 files plus our small public-API
watch probe in `native/ra_watch_probe.c`. The build preserves the
MIT license and records the exact dependency pin in
`OWNED-EVALUATOR-SOURCES.json`. Normal package builds stage this library.
The provenance file also records the watch probe's source hash.

`launcher/ra_evaluator.py` keeps `rc_runtime_t` opaque: it calls allocation,
achievement activation, frame evaluation and destruction through public
functions. It does not mirror private runtime structures or reimplement the
condition interpreter. Event IDs and the public event structure follow the
pinned `rc_runtime.h`; native fixtures exercise the actual callback ABI.

## Snapshot and recovery contract

- One evaluator belongs to one qualified set/watch generation. Construct a new
  instance after account, game or watch-list replacement. Explicit `close()`
  or context-manager exit releases its native runtime.
- Direct values and pointer `(address, value)` pairs are decoded from validated
  RAWL/RANL data. Reads must fit inside one watched entry; direct reads take
  precedence over pointer pairs. A zero pointer-pair address is absent memory.
- Missing reads reject the entire frame, suppress its events and rebuild the
  runtime. This conservative behavior may defer otherwise valid achievements;
  it avoids interpreting unavailable pointer values as legitimate zeroes.
- Duplicates, old frames and another generation never advance conditions.
  Sequence wrap uses the existing six-digit sender modulus. A gap rebuilds the
  runtime before evaluating the new frame, including delta/prior memory
  history. `rc_runtime_reset` alone retains that history and is insufficient.
- Already-triggered IDs survive local recovery and are omitted from rebuilt
  runtimes. A condition event is local evidence, not a submitted RA unlock.
- The UDP service retains a FIFO of up to 256 frames by default, configurable
  from 1 to 1024. Overflow discards its backlog, counts dropped frames and
  retains the newest complete frame. Receiver outputs carry sequence and
  generation so consumers detect the discontinuity. Reset/expiry clears the
  whole backlog. Overflow can miss a legitimate achievement; it is not a claim
  of lossless evaluation under arbitrary load.

## Host verification

`python -m unittest tests.test_ra_evaluator tests.test_ra_owned_service` compiles
and loads the real pinned library, without an account or external network.
Coverage includes native condition arming/triggering, hit counts, replay and
generation rejection, gap recovery, delta history, pointer pairs, missing
memory, sequence wrap, invalid expressions, disposal, receiver handoff, FIFO
order and overflow/reset behavior. Missing compilers fail Linux GitHub Actions
coverage and remain explicit skips in local environments without a toolchain.

`python build/build_ra_evaluator.py` builds the source-install library.
`python ps2servers.py --selfcheck-ra-evaluator` exercises real native conditions
and history recovery. The same flag on the packaged executable verifies its
staged library and callback ABI, independently of source tests.

## Remaining integration

The opt-in set path first verifies `gameid` for the image hash, without sending
credentials, then reads the pinned runtime API's `patch` and softcore
`unlocks` responses using an existing runtime login token. It does not implement
the active login UI. The opt-in [owned account boundary](RA-ACCOUNT.md) now adds
login/resume, explicit import, private token storage and a killable paced worker.
Requests use verified HTTPS, POST-only credentials,
no redirects, bounded response sizes and sanitized failure messages. The socket
timeout is not a total lookup deadline; the owned account worker supplies a
process deadline, with active service orchestration still pending. No session or unlock
submission is performed and tests never contact the real service.

`ra_sets.qualify_set` validates game/console identity, applies core/unofficial
selection and prior unlocks, and preserves the captured account epoch and image
hash. Empty remaining definitions are distinct from a failed/unsupported game.
`EvaluatedReceiver.bind_account/clear_account/complete_set` revoke previous
state and reject mismatched account epochs/images before native derivation.
The lower-level `complete` interface remains for already-qualified callers.

Direct watch derivation uses native rcheevos memory callbacks and public parsed
condition sets; it does not scan expression strings or mirror private layouts.
It includes alternate and paused groups, deduplicates reads, validates PS1/PS2
RAM limits and enforces the explicit ORBIT ceiling. The PS2 lower bound is
0x80000, matching the current EE reader; its kernel/low RAM and scratchpad
must not be treated as authentic zero-valued watched memory. Pointer-chain sets are
rejected in full until owned RANL derivation is implemented. Constant-only sets
and sets with no remaining conditions also cannot currently be installed on
this opt-in receiver path. These are remaining gaps, not unsupported-game
declarations, and the managed service remains selected.

`launcher/ra_evaluated_receiver.py` now provides an opt-in, single-owner adapter
between the receiver and evaluator. It activates a complete set before
advertising the watch list, ignores stale worker results before native
allocation, evaluates only complete transferred-list frames, and closes native
state on game change, lease expiry, reset and shutdown. A failed activation or
metadata conversion revokes any partial installation and remains retryable.
Account changes must call `reset()`; its generation tokens invalidate even
pending results for the same image. Local events are returned to the owner,
never submitted automatically. This synchronous adapter belongs off the UI
thread and has not replaced the managed service or been wired into OwnedService.

Native login UI, authenticated host/real-account acceptance and
active set-fetch orchestration remain open. Pointer watch derivation,
live service/evaluator transport ownership, leaderboard and rich
presence integration, unlock/session API handling, UI adapters and console
acceptance remain open. The new evaluator performs no HTTP requests or unlock
submissions. Neither source tests nor a packaged probe establish hardware or
complete achievement-set compatibility.
