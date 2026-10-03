"""The value objects the ML boundary exchanges with the rest of NEXUS.

These are the only shapes that cross between the classifier and its callers, and
they are deliberately free of ``torch``. A router that has to know what a
``Logits`` is has been given the wrong boundary; a prediction that carries a
live tensor graph has leaked the implementation into everything downstream of it.

Three objects, three jobs:

:class:`IntentPrediction`
    What the model said — an intent name, the probability behind it, and the
    runner-up. Carries no policy: whether the confidence is *enough* is the
    router's question, not the classifier's.
:class:`ServiceTarget`
    Which existing NEXO service an intent lands on, and which call on it is the
    natural entry point.
:class:`RoutingDecision`
    The two above plus the threshold verdict and, where there is no service, the
    reason there is not one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "IntentPrediction",
    "ModelIdentity",
    "RoutingDecision",
    "RoutingStatus",
    "ServiceTarget",
]


class RoutingStatus:
    """The stable strings a routing decision can report.

    A closed set rather than free text, because these land in an API response
    the frontend branches on. ``ACCEPTED`` means "NEXUS is confident enough to
    name a service"; ``UNCERTAIN`` means the model returned something but not
    strongly enough to act on, which is a materially different answer from
    ``OUT_OF_SCOPE`` — one says NEXUS does not have a surface for this, the other
    says NEXUS could not tell which surface you meant.
    """

    ACCEPTED = "accepted"
    UNCERTAIN = "uncertain"
    OUT_OF_SCOPE = "out_of_scope"
    GENERATION_UNAVAILABLE = "generation_unavailable"


@dataclass(frozen=True, slots=True)
class ModelIdentity:
    """What loaded, from where, and at what cost.

    ``checkpoint`` is the resolved directory. It is logged and reported, and it
    is *not* accepted from a request: the path is deployment configuration, and a
    caller able to choose it would be choosing which weights answer them.
    """

    base_model: str
    architecture: str
    device: str
    label_count: int
    max_sequence_length: int
    parameter_count: int
    checkpoint: str
    load_seconds: float

    def to_dict(self) -> dict[str, Any]:
        """The identity as a JSON-ready mapping, load time rounded for display."""
        return {
            "base_model": self.base_model,
            "architecture": self.architecture,
            "device": self.device,
            "label_count": self.label_count,
            "max_sequence_length": self.max_sequence_length,
            "parameter_count": self.parameter_count,
            "checkpoint": self.checkpoint,
            "load_seconds": round(self.load_seconds, 3),
        }


@dataclass(frozen=True, slots=True)
class IntentPrediction:
    """One classifier decision about one utterance.

    Attributes:
        intent: The winning intent name, e.g. ``"task_manage"``. Always a member
            of :class:`ml.datasets.taxonomy.Intent`.
        confidence: The softmax probability of that class. This is the model's
            own estimate for *this utterance*; it is not the test accuracy, and
            0.97 accuracy does not make every prediction 0.97 confident.
        alternatives: The next best (intent, probability) pairs, highest first.
            Kept so a low-confidence prediction can be answered with "did you
            mean…?" instead of a flat refusal.
        truncated: Whether the utterance was cut to the trained context length.
            A prediction made from a prefix of the request is a weaker claim, and
            the caller is told rather than left to assume the whole string was
            read.
        latency_ms: Wall-clock time for this call, warm model included.
    """

    intent: str
    confidence: float
    alternatives: tuple[tuple[str, float], ...] = ()
    truncated: bool = False
    latency_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """The prediction as a JSON-ready mapping, probabilities rounded for display."""
        return {
            "intent": self.intent,
            "confidence": round(self.confidence, 6),
            "alternatives": [
                {"intent": name, "confidence": round(score, 6)} for name, score in self.alternatives
            ],
            "truncated": self.truncated,
            "latency_ms": round(self.latency_ms, 3),
        }


@dataclass(frozen=True, slots=True)
class ServiceTarget:
    """An existing NEXO service, and the call that starts the work.

    ``service`` is the class's name and ``module`` its import path, resolved
    lazily by the router rather than imported here. That keeps this module free of
    ``app.services`` — and therefore free of the SQLAlchemy session machinery
    every service imports — so the classifier half of the package can be reasoned
    about without a database anywhere in sight.

    ``entrypoint`` names the concrete first call, not a category. "TaskService"
    tells a caller which surface they reached; "TaskService.list" tells them the
    call to make.
    """

    service: str
    module: str
    entrypoint: str

    @property
    def qualified(self) -> str:
        """``module.ClassName``, for logs and for resolving the import."""
        return f"{self.module}.{self.service}"

    def to_dict(self) -> dict[str, Any]:
        """The target as a JSON-ready mapping for an API response."""
        return {
            "service": self.service,
            "module": self.module,
            "entrypoint": self.entrypoint,
        }


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    """A prediction plus what NEXUS will do about it.

    The point of this object is that the *negative* answers are as explicit as
    the positive one. A request for a code refactor does not fall through to a
    default branch: it carries ``status="generation_unavailable"``,
    ``destination="large-model:unavailable"`` and a reason, so the caller learns
    that NEXUS recognised the request and declined it rather than that the
    request vanished.
    """

    intent: str
    confidence: float
    threshold: float
    status: str
    destination: str
    destination_kind: str
    target: ServiceTarget | None = None
    reason: str = ""
    prediction: IntentPrediction | None = field(default=None, repr=False)

    @property
    def accepted(self) -> bool:
        """Whether NEXUS is confident enough to name a service for this turn."""
        return self.status == RoutingStatus.ACCEPTED

    @property
    def service(self) -> str | None:
        """The name of the existing service this decision lands on, if any."""
        return self.target.service if self.target else None

    def to_dict(self) -> dict[str, Any]:
        """The decision as a JSON-ready mapping, probabilities rounded for display."""
        return {
            "intent": self.intent,
            "confidence": round(self.confidence, 6),
            "threshold": self.threshold,
            "status": self.status,
            "destination": self.destination,
            "destination_kind": self.destination_kind,
            "target": self.target.to_dict() if self.target else None,
            "reason": self.reason,
        }
