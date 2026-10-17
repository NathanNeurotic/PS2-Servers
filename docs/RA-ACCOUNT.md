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
Native account UI/consent prompts, active desktop service selection, real-account
acceptance, leaderboards and console acceptance remain open. Passing offline login
fixtures does not establish real RetroAchievements authentication or unlock acceptance.
