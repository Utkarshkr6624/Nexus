"""Errors raised by the Phase 11 intent-classification boundary.

Every exception here derives from :class:`app.core.exceptions.NexusError`, so a
failure inside the classifier renders through the same envelope as every other
domain error and never leaks a stack trace, a filesystem path or a tensor shape
to a client.

The split is between *the deployment cannot classify* and *this request cannot be
classified*:

* :class:`MLUnavailableError` and its subclasses are 503. They describe a server
  that has no working classifier — no checkpoint on disk, no ``torch``, a
  corrupted file — which an operator can fix. ``details`` carries the kind only,
  never the path, because the path is local filesystem information.
* :class:`InferenceError` is a 500. The model loaded and then failed on a
  request it should have been able to answer.
* :class:`InvalidUtteranceError` is a 422 and behaves like any other validation
  rejection.
"""

from __future__ import annotations

from collections.abc import Mapping
from http import HTTPStatus
from typing import Any

from app.core.exceptions import ErrorCode, NexusError, ValidationError

__all__ = [
    "InferenceError",
    "InvalidUtteranceError",
    "MLUnavailableError",
    "ModelCheckpointError",
    "ModelRuntimeError",
]


class MLUnavailableError(NexusError):
    """The intent classifier cannot serve a prediction.

    A deployment condition rather than a request condition: NEXUS is running
    with ML switched off, without the trained checkpoint, without ``torch``, or
    with a checkpoint that failed to load. 503, because nothing about the
    caller's request is wrong.
    """

    code = ErrorCode.ML_UNAVAILABLE
    status_code = HTTPStatus.SERVICE_UNAVAILABLE
    default_message = (
        "Intent classification is not available on this server. The trained "
        "checkpoint could not be loaded."
    )


class ModelCheckpointError(MLUnavailableError):
    """The Phase 10 checkpoint is missing, incomplete or unreadable.

    Raised for an absent directory, a missing member file, unparsable JSON, a
    label map that disagrees with the taxonomy, or weights that do not decode.
    The point of naming the *kind* rather than the failure is that a missing
    checkpoint must read as "the checkpoint is missing", not as an obscure
    tokenizer or tensor error three frames deeper.
    """

    default_message = "The trained intent classifier checkpoint is unavailable."


class ModelRuntimeError(MLUnavailableError):
    """The classifier's runtime could not be initialised.

    ``torch`` or ``transformers`` is not importable, or the requested device
    cannot be used — a ``cuda`` request on a machine with no CUDA build, for
    instance. Distinct from :class:`ModelCheckpointError` because the weights may
    be perfectly fine and only the machine is not.
    """

    default_message = "The intent classifier runtime could not be initialised."


class InferenceError(NexusError):
    """Prediction failed after the model had already loaded.

    A 500 with a fixed message: the underlying text belongs in the log, where it
    is recorded as an exception type rather than as the user's utterance.
    """

    code = ErrorCode.INTERNAL_ERROR
    status_code = HTTPStatus.INTERNAL_SERVER_ERROR
    default_message = "Intent classification failed."


class InvalidUtteranceError(ValidationError):
    """The submitted text is not something the classifier may be asked about.

    Empty, whitespace-only, or over the configured character bound. Raised in
    the domain rather than left to the schema so the same rule applies to every
    caller of :meth:`app.ml.classifier.IntentClassifier.predict`, not only to the
    one route that happens to validate at the edge.
    """

    default_message = "The submitted text cannot be classified."

    def __init__(
        self,
        message: str | None = None,
        *,
        details: Mapping[str, Any] | None = None,
        status_code: int | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(
            message,
            details=details,
            status_code=status_code,
            code=code,
        )
