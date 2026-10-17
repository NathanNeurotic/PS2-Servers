"""Single-owner receiver/evaluator lifecycle; no account or submission I/O.

The caller must qualify definitions and prior unlocks for its current account
before complete(). Account changes must call reset(), revoking pending jobs.
This adapter is opt-in and is not selected by the managed UDP service.
"""
from dataclasses import dataclass

from launcher.ra_evaluator import Evaluation, Evaluator
from launcher.ra_receiver import Receiver, ReceiverOutput


@dataclass(frozen=True)
class EvaluatedOutput:
    """Wire effects plus optional local evaluation, never a submitted unlock."""
    wire: ReceiverOutput
    evaluation: Evaluation = None


class EvaluatedReceiver:
    """Own one receiver and at most one generation-bound native evaluator.

    All methods belong to one event-loop thread. Native evaluation is
    synchronous; callers must keep this owner off the desktop/UI thread.
    Workers return results to this owner rather than calling complete().
    """

    def __init__(self, library, **receiver_options):
        self._receiver = Receiver(**receiver_options)
        self._library = library
        self._evaluator = None
        self._closed = False
        self._account = None

    def bind_account(self, account, epoch):
        """Revoke jobs/native state before accepting a new non-secret account epoch."""
        self._check_open()
        if type(account) is not str or not 1 <= len(account) <= 64 or type(epoch) is not int or epoch < 1:
            raise ValueError("Invalid account identity or epoch")
        identity = (account, epoch)
        if identity != self._account:
            self.reset()
            self._account = identity

    def clear_account(self):
        """Log out of the owned set boundary without retaining evaluable state."""
        self.reset()
        self._account = None

    def complete_set(self, job, qualified, *, profile="opl"):
        """Accept a qualified current-account/current-image set and derive its watches.

        Zero remaining definitions and unsupported pointer derivation are
        explicit errors, never a claim that the game is unsupported.
        """
        from launcher.ra_sets import QualifiedSet, derive_direct_watch
        self.tick()
        if not isinstance(qualified, QualifiedSet):
            raise ValueError("Qualified set required")
        context = qualified.context
        if (self._account != (context.account, context.epoch) or
                self._receiver.pending != job or job is None or
                job.image_hash != context.image_hash):
            self._receiver.complete(job)
            self._reconcile()
            return False
        try:
            watch = derive_direct_watch(self._library, qualified, profile=profile)
        except Exception:
            self._receiver.clear()
            self._reconcile()
            raise
        return self.complete(job, watch, dict(qualified.definitions),
                             title=qualified.title, achievements=len(qualified.selected_ids))

    def _reconcile(self):
        runtime = self._evaluator
        if runtime is not None and (
                self._receiver.session.watch is None or
                runtime.generation != self._receiver.session.generation):
            runtime.close()
            self._evaluator = None

    def _check_open(self):
        if self._closed:
            raise RuntimeError("Evaluated receiver is closed")

    def tick(self):
        """Expire leases and immediately release any revoked native state."""
        self._check_open()
        self._receiver.tick()
        self._reconcile()

    def take_jobs(self):
        """Return current identification work after expiry/revocation checks."""
        self.tick()
        return self._receiver.take_jobs()

    def complete(self, job, watch_file=None, definitions=None, **metadata):
        """Publish only a current set whose native conditions activate fully.

        Invalid conditions or metadata leave identification retryable; they
        never advertise a usable watch list or become an unsupported result.
        Stale results are ignored before loading/allocating native state.
        """
        self.tick()
        receiver = self._receiver
        if receiver.pending != job or job is None or job.token != receiver.token:
            return False
        if watch_file is None:
            accepted = receiver.complete(job, **metadata)
            self._reconcile()
            return accepted
        candidate = None
        try:
            candidate = Evaluator(self._library, definitions, watch_file,
                                  receiver.session.generation + 1)
            accepted = receiver.complete(job, watch_file, **metadata)
        except Exception:
            if candidate is not None:
                candidate.close()
            # complete() may have installed a list before metadata conversion
            # failed. Revoke everything instead of leaving an advertised list
            # with no corresponding evaluator.
            receiver.clear()
            self._reconcile()
            raise
        if not accepted:
            candidate.close()
            return False
        self._reconcile()
        self._evaluator = candidate
        return True

    def receive(self, packet, peer):
        """Process wire input and evaluate only its current complete frame."""
        self._check_open()
        output = self._receiver.receive(packet, peer)
        self._reconcile()
        evaluation = None
        if output.values and self._evaluator is not None:
            evaluation = self._evaluator.evaluate_frame(output)
        return EvaluatedOutput(output, evaluation)

    def reset(self):
        """Revoke account/game jobs, wire ownership and native history together."""
        self._check_open()
        self._receiver.clear()
        self._reconcile()

    def close(self):
        """Release native state and prevent reuse; safe to call repeatedly."""
        if not self._closed:
            self.reset()
            self._closed = True
