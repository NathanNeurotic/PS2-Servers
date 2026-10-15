# Owned rcheevos evaluator integration

The owned host now evaluates offline achievement conditions through the pinned
rcheevos **public C runtime API**. It does not compile or invoke the xeRAbora
desktop application for this path. This is an intermediate implementation for
#223; the existing managed achievement service remains selected.

## Native boundary

`build/build_ra_evaluator.py` builds `rcheevos-owned.dll`,
`librcheevos-owned.dylib` or `librcheevos-owned.so`. It shares the existing
pinned native toolchain selection and target architecture. Sources are only
rcheevos runtime, utility/compatibility and MD5 files. The build preserves the
MIT license and records the exact dependency pin in
`OWNED-EVALUATOR-SOURCES.json`. Normal package builds stage this library.

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

Account eligibility, legitimate set fetching, prior unlocks and core/unofficial
selection must be resolved before evaluator construction. Watch derivation
from the fetched set, live service/evaluator ownership, leaderboard and rich
presence integration, unlock/session API handling, UI adapters and console
acceptance remain open. The new evaluator performs no HTTP requests or unlock
submissions. Neither source tests nor a packaged probe establish hardware or
complete achievement-set compatibility.
