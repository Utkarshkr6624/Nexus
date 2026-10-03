"""Phase 11 — the trained intent classifier, serving.

Phase 10 produced exactly one artifact: a ``microsoft/deberta-v3-base``
classifier over the fourteen Nexo intents, trained on CPU and evaluated at
0.9738 accuracy / 0.9737 macro F1 on a 420-row held-out split. Phase 10 stopped
there, deliberately. This package is the other half — the boundary that puts that
checkpoint inside the running application.

The pipeline it serves is::

    user text
        → validation
        → tokenizer            (raw text, exactly as Phase 10 trained it)
        → model                (eval mode, inference_mode, no gradients)
        → softmax
        → intent + confidence
        → router               (threshold, destination, existing service)
        → response

Four modules, four jobs, and no module doing another's:

:mod:`app.ml.model_loader`
    Resolves the checkpoint, validates it against the taxonomy, picks a device and
    loads weights. No business logic, and **no module-level torch import** — that
    is what lets the application boot, and every non-ML route work, on a machine
    where torch is not installed.
:mod:`app.ml.classifier`
    ``predict(text) -> IntentPrediction``. Owns the tensor work and turns it into
    a plain dataclass before returning, so nothing downstream of it knows what a
    logit is.
:mod:`app.ml.router`
    Turns a prediction into a :class:`~app.ml.schemas.RoutingDecision`: the
    existing NEXUS service behind the intent, or an explicit refusal. Two classes
    that need a generative model answer ``large-model:unavailable`` rather than
    pretending; a prediction the model is not confident enough to stand behind
    answers ``uncertain`` rather than naming a service it might have guessed.
:mod:`app.ml.runtime`
    The process-wide owner. Loads once, reuses forever, and degrades explicitly:
    a missing checkpoint or a missing torch produces a *reason* a caller and an
    operator can both read, not a crash and not a fabricated result.

**One model, no fallback.** NEXO runs a classifier and nothing else. There is no
second model, no generative LLM, no cloud inference, and no path by which a
request that needs generation is answered by anything other than an honest
``large-model:unavailable``. The ``ml`` package under ``backend/`` remains the
training pipeline and is still stdlib-only, but :mod:`ml.datasets.taxonomy` is
now read at serving time, because the label set it defines is a contract rather
than a training detail: the taxonomy and the checkpoint's ``id2label`` are
cross-validated against each other on every load.
"""

from __future__ import annotations

from app.ml.classifier import IntentClassifier
from app.ml.exceptions import (
    InferenceError,
    InvalidUtteranceError,
    MLUnavailableError,
    ModelCheckpointError,
    ModelRuntimeError,
)
from app.ml.model_loader import LoadedModel, load_model, resolve_device
from app.ml.router import SERVICE_TARGETS, IntentRouter, resolve_service, routing_taxonomy
from app.ml.runtime import MLRuntime, MLRuntimeStatus, get_ml_runtime, reset_ml_runtime
from app.ml.schemas import (
    IntentPrediction,
    ModelIdentity,
    RoutingDecision,
    RoutingStatus,
    ServiceTarget,
)

__all__ = [
    "SERVICE_TARGETS",
    "InferenceError",
    "IntentClassifier",
    "IntentPrediction",
    "IntentRouter",
    "InvalidUtteranceError",
    "LoadedModel",
    "MLRuntime",
    "MLRuntimeStatus",
    "MLUnavailableError",
    "ModelCheckpointError",
    "ModelIdentity",
    "ModelRuntimeError",
    "RoutingDecision",
    "RoutingStatus",
    "ServiceTarget",
    "get_ml_runtime",
    "load_model",
    "reset_ml_runtime",
    "resolve_device",
    "resolve_service",
    "routing_taxonomy",
]
