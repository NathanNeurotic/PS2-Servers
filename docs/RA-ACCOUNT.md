# Owned account/profile and worker boundary

The owned path implements runtime-token login/resume, explicit managed-profile
import, private owned storage, paced HTTPS and bounded account/set lookup. It
remains opt-in. The desktop's managed service and its account UI are still active;
these classes do not select a new service, start sessions or submit unlocks.

## Profile and import

`CredentialStore(profile)` writes only `profile/owned/credentials`. Windows
protects its payload with user-bound DPAPI and forbids protection UI. POSIX uses
owner-only directories/files (0700/0600), following the existing managed policy;
POSIX storage is not encrypted. Writes use a private temporary file, fsync and
atomic replacement. Invalid protection, formats, links/reparse points and unsafe
POSIX permissions fail instead of falling back to another credential source.
Passwords and Web API keys are never stored here. Runtime-token repr is redacted.

`read_managed(consent=True)` reads only `profile/credentials`, using the managed
engine's existing DPAPI or private-file format. It does not scan standalone
xeRAbora/Caduceus profiles or copy their Web API keys. Import is never automatic.
The worker revalidates an imported token with `login2`; only the owning session
may persist a current successful result when explicitly asked to remember it.
The source credentials remain untouched, preserving the managed rollback path.
No real user credentials are read by tests or selfchecks.

## Ownership, HTTP and cancellation

`AccountSession` runs on one service-owner thread. Starting login/resume/import
revokes the prior receiver/account generation. A worker returns a private
`AccountResult`; the owner checks task ID and epoch before saving or binding it.
Logout cancels outstanding work and clears native evaluation before optionally
removing only the owned token. A late child cannot persist a token or restore
an account after logout because children never write profile credentials.

`AccountWorker` serializes lookups through one spawned, reaped child. It rejects
concurrent callers, paces starts and retains HTTP 429 backoff across children.
The supervisor uses `wait=True` for console-profile fallback and session startup:
it waits cancellably for the existing cooldown instead of dropping those jobs.
The default worker call still returns immediately during cooldown. Only a
successful hash lookup with an integer `GameID` of zero declares an unknown
image; malformed mappings and failed/limited set qualification stay retryable.

The owned dashboard's logout revokes the live service account and removes only
its owned credential through the existing owner-thread account session. It also
cancels subsequent session requests and prevents pending lookup/evaluation output
from restoring the displayed game. The dashboard remains available signed out;
restart with `--login` or explicit import to sign in again. HTTP logout reports
failure if the owner cannot complete credential removal. Cancellation cannot undo
an unlock request already accepted remotely. Logout adds no PS2 protocol messages.

Awards and periodic pings share one bounded submission consumer (64 waiting tasks
plus one active task), replacing per-request threads. It reuses `AccountWorker`
for killable requests, retained pacing/backoff and cancellation. Each evaluation
carries the immutable account/game context of its accepted set, so a queued
unlock cannot be relabelled with a later game's hash. Logout and shutdown cancel
the worker and discard pending submissions. A full queue reports rejection; it
does not grow or silently overwrite an award. Failed or ambiguous requests are
attempted once, with no automatic retry or durable replay queue. Local unlock
display is not remote award acceptance. This bounds submissions separately from
identification; it does not claim one global HTTP scheduler for all account work.

The UDP owner retains unread unlock evaluations in a bounded FIFO and coalesces
ordinary evaluations into one latest-state slot. Routine telemetry cannot evict
an unlock before the supervisor sees it. The unlock FIFO uses `frame_capacity`
(256 by default); if it contains only unlock events and fills, the new event is
rejected with a diagnostic and `dropped_evaluations` increment. Existing events
are preserved. Account/game reset clears both paths. This is an in-memory bound,
not durable award delivery or proof of remote acceptance.
The HTTPS transport spaces requests by at least one second per client, uses a
fixed verified HTTPS endpoint and POST fields, rejects redirects, bounds bodies,
sanitizes errors and performs no automatic retries. Backoff is bounded to 1--300
seconds. Passwords/tokens cross only the private process boundary, never argv or
console packets. Only an already-authenticated set job carries a runtime token.

The process supervisor validates results before serialization, bounds private
IPC to 8 MiB and reads on one owned helper thread. Partial pipe payloads therefore
cannot block its deadline loop. Timeout/cancellation terminates and reaps the
child and releases its reader; a surviving process/reader prevents replacement.
This is a trusted local child channel, not a network pickle endpoint.
Deserialization permits only the passive result dataclasses; other pickle globals
are rejected before result validation. The managed POSIX engine also tightens an
existing application profile to 0700, rejecting links and foreign owners while
leaving the shared XDG/.config parent unchanged.

Qualified set results retain the account epoch, image hash and game/console ID.
The owner publishes them only through the existing generation-bound receiver.
No-support, transport failure, cooldown and no-remaining-achievement states must
remain distinct when the active UI/service adapter is added.

## Session tracking, unlock submission and ping

`RASessionClient` handles session tracking and unlock submission using an existing
runtime login token. It requires an active `SetContext` matching the account identity:

- `start_session(context)` submits `r=startsession` for softcore session registration
  and returns `SessionInfo` with server timestamp and existing unlock IDs.
- `award_achievement(context, achievement_id, seconds_since_unlock=0)` generates
  the required softcore MD5 signature `v=md5(f"{achievement_id}{username}0")`
  (including `seconds_since_unlock` when nonzero) and submits `r=awardachievement`.
  Responses with `"User already has"` are treated as idempotent successes with
  `already_unlocked=True`. Sanitized exceptions prevent leaking credentials or private errors.
- `ping(context, rich_presence=None)` submits periodic session keepalives (`r=ping`)
  with optional rich presence strings truncated to 4096 bytes.

`AccountSession` exposes `begin_start_session()`, `begin_award()`, and `begin_ping()`.
These operations execute through `AccountWorker` in isolated subprocesses. The owner
commits results only when task IDs and epochs match, saving `session_info` on the
session owner and verifying awards against active context.

## Verification and remaining gates

Source fixtures cover real platform token protection, atomic rollback, explicit
import, login field/response contracts, private result delivery, cancellation,
backoff, stale-account rejection and native set activation. POSIX additionally
injects a partial IPC payload to verify the byte-stream deadline boundary.

`--selfcheck-ra-account` uses a disposable synthetic profile and spawned callback
without networking. It checks platform storage, private credential delivery and
resource release. Existing resolver and evaluator selfchecks remain separate.

`OwnedService` now orchestrates `EvaluatedReceiver` and `AccountSession` on its
single UDP owner thread when configured with an evaluator library and profile.
The supervisor offers this service under `--owned-runtime`; the managed service
remains the default. Native account UI/consent prompts, real-account acceptance,
complete session/submission lifetime ownership, leaderboards and console acceptance
remain open. Passing offline login
fixtures does not establish real RetroAchievements authentication or unlock acceptance.
