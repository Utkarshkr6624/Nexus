"""The process-wide owner of the Phase 10 intent classifier.

**One model, one owner, one place that can fail.** Every other module in the ML
boundary assumes a classifier exists and says nothing about where it came from;
this module is the only one that knows a checkpoint lives on a filesystem, that
``torch`` may not be installed, and that the weights take seconds to read. That
concentration is the point: a runtime with no owner ends up loading 703 MiB once
per route that touches it, or once per worker reload, and the failure surfaces
as a 500 in an unrelated endpoint.

**Loading is tolerant by default and strict on request.** A missing checkpoint
is a supported state — ``backend/ml/artifacts`` is gitignored, so a fresh clone
has never run Phase 10 — and it must not take the rest of the API down with it.
So :meth:`MLRuntime.load` records a machine-readable ``reason`` and leaves the
runtime unavailable; the ML endpoints then answer 503 with that reason and every
deterministic router keeps working. A deployment that cannot serve its own
contract at all sets ``ML_FAIL_FAST`` and gets the failure at boot instead of a
feature that quietly refuses every request.

**Nothing here imports torch.** ``app.ml.classifier`` reaches for it inside its
own functions, which is what lets :mod:`app.api.deps` import this module — and
therefore this package — without a 300 MiB import on a server that has ML
switched off.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.core.config import Settings, get_settings
from app.core.logging import get_logger, log_event
from app.ml.exceptions import (
    MLUnavailableError,
    ModelCheckpointError,
    ModelRuntimeError,
)

if TYPE_CHECKING:  # pragma: no cover - typing only, never executed at import
    from app.ml.classifier import IntentClassifier

__all__ = ["MLRuntime", "MLRuntimeStatus", "get_ml_runtime", "reset_ml_runtime"]

logger = get_logger("app.ml.runtime")


@dataclass(frozen=True, slots=True)
class MLRuntimeStatus:
    """Why the classifier is or is not answering, in a form a caller may read.

    ``reason`` is a closed vocabulary rather than prose, because it is what a
    health check branches on and what a caller is told when it gets a 503:
    "the checkpoint is missing" is an operator-actionable fact and "something
    went wrong" is not. ``detail`` carries the underlying message for the log
    and for a developer reading the response body in development; it names the
    failure, never anything about a user's text.
    """

    available: bool
    reason: str
    detail: str = ""
    checkpoint: str = ""
    device: str = ""
    load_seconds: float = 0.0

    def to_dict(self) -> dict[str, object]:
        """A JSON-ready view, for a health response or a startup log."""
        return {
            "available": self.available,
            "reason": self.reason,
            "detail": self.detail,
            "checkpoint": self.checkpoint,
            "device": self.device,
            "load_seconds": round(self.load_seconds, 3),
        }


class MLRuntime:
    """Holds the loaded classifier and the reason it is not there.

    **Constructed with settings, loaded once, released once.** The state a
    runtime owns is the model and nothing else: no request state, no per-user
    data, no cache that would need invalidating, so one instance per process is
    correct and a second one would only duplicate the memory.

    The lifecycle methods are guarded by a lock because loading runs in a worker
    thread (:meth:`load` blocks for seconds reading 703 MiB) while a request
    arriving on the event loop may read :attr:`status` at the same moment.
    """

    #: Every reason :meth:`load` can record. A caller may compare against these,
    #: so they are part of the module's contract rather than log wording.
    REASON_UNLOADED = "not_loaded"
    REASON_AVAILABLE = "available"
    REASON_DISABLED = "disabled"
    REASON_CHECKPOINT_MISSING = "checkpoint_missing"
    REASON_RUNTIME_MISSING = "runtime_missing"
    REASON_LOAD_FAILED = "load_failed"
    REASON_STOPPED = "stopped"

    def __init__(
        self,
        settings: Settings | None = None,
        classifier: IntentClassifier | None = None,
    ) -> None:
        """Create an unloaded runtime.

        Args:
            settings: The configuration to resolve the checkpoint and device
                from. Defaults to the process-wide singleton.
            classifier: An already-constructed classifier to adopt instead of
                loading one. :meth:`load` accepts it as-is and never touches
                ``torch``, which is how a caller that has built the classifier
                some other way — or a test standing in for one — exercises the
                lifecycle without a 703 MiB read.
        """
        self._settings = settings or get_settings()
        self._classifier: IntentClassifier | None = classifier
        self._status = MLRuntimeStatus(
            available=classifier is not None,
            reason=self.REASON_AVAILABLE if classifier is not None else self.REASON_UNLOADED,
            checkpoint=str(self._settings.ml_resolved_model_path),
            device=self._settings.ml_device,
        )
        self._attempted = classifier is not None
        self._lock = threading.Lock()

    # -- Accessors -----------------------------------------------------------

    @property
    def settings(self) -> Settings:
        """The settings this runtime was built from."""
        return self._settings

    @property
    def classifier(self) -> IntentClassifier | None:
        """The loaded classifier, or ``None`` when the runtime is degraded.

        ``None`` is a normal value here rather than a failure: it is what a
        caller must handle by answering 503, and it is the state the process is
        in on any machine that has not run Phase 10.
        """
        return self._classifier

    @property
    def status(self) -> MLRuntimeStatus:
        """Why the runtime is or is not answering. Safe to read from any thread."""
        return self._status

    @property
    def is_available(self) -> bool:
        """Whether a prediction can be made right now."""
        return self._classifier is not None

    # -- Lifecycle -----------------------------------------------------------

    def load(self) -> MLRuntimeStatus:
        """Load the classifier, once, degrading rather than crashing.

        Idempotent by design: the lifespan calls this at startup and a route
        that is reached without one may call it again, and neither ordering may
        produce two copies of 703 MiB of weights. A second call after a
        successful load is a no-op returning the existing status; a second call
        after a *failed* load is also a no-op, so a route hitting a 703 does
        not re-read a checkpoint that was just found to be missing on every
        request.

        Returns:
            The status after the attempt, degraded or not.

        Raises:
            ModelCheckpointError: ``ML_FAIL_FAST`` is set and the checkpoint is
                absent.
            MLUnavailableError: ``ML_FAIL_FAST`` is set and the classifier could
                not be built — ``torch`` missing, or the load itself failed.
        """
        with self._lock:
            if self._attempted:
                return self._status

            if not self._settings.ml_enabled:
                # A deliberate off switch is a decision, not a failure: refusing
                # to boot because an operator asked for no model would make the
                # switch useless in exactly the deployments that need it, so
                # this reason never raises even under ML_FAIL_FAST.
                return self._degrade(
                    self.REASON_DISABLED,
                    "ML integration is switched off (ML_ENABLED=false).",
                    level=logging.INFO,
                )

            if not self._settings.ml_checkpoint_exists:
                # Checked before the import rather than by catching: a machine
                # with no torch and no checkpoint should learn which one is
                # missing immediately, not after an import attempt, and the
                # loader would raise the same class with the same meaning.
                return self._handle_failure(
                    self.REASON_CHECKPOINT_MISSING,
                    ModelCheckpointError(
                        f"no checkpoint directory at {self._settings.ml_resolved_model_path}"
                    ),
                    ModelCheckpointError(
                        "The trained intent classifier checkpoint is unavailable."
                    ),
                    exc_info=False,
                    level=logging.WARNING,
                )

            started = time.monotonic()
            try:
                classifier = self._build_classifier()
            except ModelCheckpointError as exc:
                # Distinguished from the runtime case because the two send an
                # operator to different places: a checkpoint error means re-run
                # Phase 10 or fix ML_MODEL_PATH, a runtime error means install
                # torch or fix ML_DEVICE. Both are the caller's, never the
                # request's, so both degrade rather than crash.
                return self._handle_failure(
                    self.REASON_CHECKPOINT_MISSING,
                    exc,
                    exc.__class__,
                    level=logging.WARNING,
                )
            except ModelRuntimeError as exc:
                return self._handle_failure(
                    self.REASON_RUNTIME_MISSING,
                    exc,
                    exc.__class__,
                    level=logging.WARNING,
                )
            except MLUnavailableError as exc:
                return self._handle_failure(
                    self.REASON_LOAD_FAILED, exc, exc.__class__, exc_info=True
                )
            except Exception as exc:
                # Anything else is a bug rather than a deployment condition, so
                # it degrades like the rest — the traceback goes to the log, not
                # to the caller — but it is logged at ERROR because "the
                # checkpoint is missing" and "our wiring is wrong" are different
                # pages of the same runbook.
                return self._handle_failure(
                    self.REASON_LOAD_FAILED,
                    exc,
                    MLUnavailableError("The intent classifier checkpoint could not be loaded."),
                    exc_info=True,
                )

            self._classifier = classifier
            self._attempted = True
            self._status = MLRuntimeStatus(
                available=True,
                reason=self.REASON_AVAILABLE,
                checkpoint=str(self._settings.ml_resolved_model_path),
                device=getattr(
                    getattr(classifier, "identity", None),
                    "device",
                    self._settings.ml_device,
                ),
                load_seconds=time.monotonic() - started,
            )
            log_event(
                logger,
                logging.INFO,
                "ml_runtime_loaded",
                checkpoint=self._status.checkpoint,
                device=self._status.device,
                load_seconds=round(self._status.load_seconds, 3),
            )
            return self._status

    def shutdown(self) -> None:
        """Release the classifier and leave the runtime reloadable.

        Called from the lifespan's ``finally`` alongside the engine dispose, so
        the process gives back a gigabyte of resident memory it no longer needs.
        The attempt flag is cleared so a runtime that is loaded again — a test
        that restarted the app, a lifespan that ran twice in one process — goes
        back to the filesystem rather than reporting a stale status forever.
        """
        with self._lock:
            classifier = self._classifier
            self._classifier = None
            self._attempted = False
            self._status = MLRuntimeStatus(
                available=False,
                reason=self.REASON_STOPPED,
                checkpoint=str(self._settings.ml_resolved_model_path),
                device=self._settings.ml_device,
            )
        if classifier is None:
            return
        try:
            classifier.close()
        except Exception:
            # Shutdown must not fail: this runs in the lifespan's ``finally``,
            # and an exception here would replace a clean stop with a traceback
            # about memory the process is about to exit with anyway.
            log_event(
                logger,
                logging.WARNING,
                "ml_runtime_close_failed",
                exc_info=True,
            )
        else:
            log_event(logger, logging.INFO, "ml_runtime_stopped")

    # -- Internals -----------------------------------------------------------

    def _build_classifier(self) -> IntentClassifier:
        """Load the checkpoint and wrap it in a classifier.

        The imports are inside the function because that is the whole reason
        this module can be imported by a process with no ML stack installed: at
        module scope a single ``from app.ml.classifier import IntentClassifier``
        would make ``import app.api.deps`` fail on a machine where torch was
        never installed, turning an absent optional feature into a broken API.

        Loading is two steps rather than one — :func:`load_model` returns the
        weights on a device, :class:`IntentClassifier` turns them into a
        prediction — so that the classifier half of the package stays testable
        against a hand-built :class:`LoadedModel` with no weights in it.

        Returns:
            A classifier carrying the configured threshold, so the number the
            router compares against is the one an operator set.

        Raises:
            ModelCheckpointError: The checkpoint is missing or unusable.
            ModelRuntimeError: torch or transformers is unavailable, or the
                requested device cannot be provided.
        """
        from app.ml.classifier import IntentClassifier
        from app.ml.model_loader import load_model

        loaded = load_model(
            self._settings.ml_resolved_model_path,
            device=self._settings.ml_device,
        )
        return IntentClassifier(
            loaded,
            threshold=self._settings.ml_confidence_threshold,
            reject_credentials=self._settings.ml_reject_credentials,
        )

    def _handle_failure(
        self,
        reason: str,
        exc: Exception,
        error: MLUnavailableError,
        *,
        exc_info: bool = True,
        level: int = logging.ERROR,
    ) -> MLRuntimeStatus:
        """Degrade with ``reason``, or re-raise under ``ML_FAIL_FAST``.

        One place decides between the two modes so every failure in
        :meth:`load` degrades identically and no branch can accidentally
        swallow an error the operator asked to hear about.

        Args:
            reason: One of the ``REASON_*`` constants.
            exc: The exception the load raised.
            error: What to raise instead when the deployment is strict.
            exc_info: Whether to attach the traceback to the log record.
            level: Log level. A missing checkpoint is a WARNING because it is
                the documented state of a fresh clone; anything else is an ERROR
                because it is not.

        Returns:
            The degraded status. Never returned in strict mode: the raise happens
            on the way out.

        Raises:
            MLUnavailableError: ``ML_FAIL_FAST`` is set.
        """
        detail = str(exc) or exc.__class__.__name__
        if self._settings.ml_fail_fast:
            log_event(
                logger,
                level,
                "ml_runtime_failed",
                exc_info=exc_info,
                reason=reason,
                detail=detail,
                checkpoint=str(self._settings.ml_resolved_model_path),
            )
            raise error from exc
        return self._degrade(reason, detail, level=level, exc_info=exc_info)

    def _degrade(
        self,
        reason: str,
        detail: str,
        *,
        level: int,
        exc_info: bool = False,
    ) -> MLRuntimeStatus:
        """Record an unavailable status, log it, and return it."""
        self._attempted = True
        self._classifier = None
        self._status = MLRuntimeStatus(
            available=False,
            reason=reason,
            detail=detail,
            checkpoint=str(self._settings.ml_resolved_model_path),
            device=self._settings.ml_device,
        )
        log_event(
            logger,
            level,
            "ml_runtime_unavailable",
            exc_info=exc_info,
            reason=reason,
            detail=detail,
            checkpoint=self._status.checkpoint,
        )
        return self._status


_runtime: MLRuntime | None = None
_runtime_lock = threading.Lock()


def get_ml_runtime() -> MLRuntime:
    """Return the process-wide runtime, creating it on first use.

    A module-level singleton rather than a FastAPI dependency graph, because the
    model is process state: two runtimes would mean two copies of the weights and
    two answers to "is ML up" that could disagree. The provider in
    :mod:`app.api.deps` reads this when the lifespan has not run, which is the
    normal case in the test suite — clients built on ``ASGITransport`` never
    execute a lifespan.
    """
    global _runtime
    if _runtime is None:
        with _runtime_lock:
            if _runtime is None:
                _runtime = MLRuntime(get_settings())
    return _runtime


def reset_ml_runtime() -> None:
    """Tear down the singleton so the next call builds a fresh runtime.

    Exists for the test suite, and for the lifespan's shutdown: a process that
    re-entered ``get_ml_runtime()`` after a lifespan shutdown must load again
    rather than be handed a runtime whose classifier has been released. Tests
    that swap ``ML_ENABLED`` or the checkpoint path between cases call this
    first, since the singleton would otherwise hold the previous case's settings.
    """
    global _runtime
    with _runtime_lock:
        if _runtime is not None:
            _runtime.shutdown()
        _runtime = None
