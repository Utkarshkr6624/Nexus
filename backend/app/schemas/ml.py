"""Request/response models for the intent-routing surface.

Phase 11 wires a trained classifier into the running application, and this module
is the wire contract for the two endpoints that expose it. Three decisions shape
it.

**The response mirrors a decision, not a model output.** :class:`RoutingDecisionRead`
carries an intent, a confidence, a threshold and a verdict — never logits, a
tensor, a tokenizer id or a stack frame. The classifier's internals stop at
:mod:`app.ml.schemas`; a client that could see them would be coupled to the
checkpoint, and a checkpoint can be retrained without a client changing.

**A negative answer is a first-class response, not an error.** ``target`` is
``None`` for a declined or uncertain request and the ``status`` string says which
of the three it was. :class:`RoutingDecisionRead` therefore keeps
``destination`` and ``destination_kind`` populated even when no service is named:
"recognised, and there is nothing to call" is the answer the router gives for
``code_assist``, ``deep_reasoning``, ``out_of_scope`` and every below-threshold
prediction, and collapsing it to a 404 would throw away the only part of the
answer the caller can act on.

**Every field is described.** These two endpoints are the entire public face of
the ML surface and the first thing a caller reads in Swagger, so the docs carry
what the values *mean* — that ``confidence`` is this utterance's softmax
probability rather than a test-set accuracy, that ``threshold`` is a deployment
decision, that ``checkpoint`` names a path the caller may read but not choose.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import get_settings
from app.ml.schemas import RoutingDecision

__all__ = [
    "MAX_ALTERNATIVES",
    "MAX_INPUT_CHARS",
    "IntentAlternativeRead",
    "IntentRouteRead",
    "MLStatusRead",
    "ModelIdentityRead",
    "RouteRequest",
    "RoutingDecisionRead",
    "ServiceTargetRead",
]

#: The longest utterance accepted on the routing endpoint. Read once at import
#: because a Pydantic constraint has to be a static bound: ``max_length`` is
#: baked into the validator when the model class is built, so a request that
#: arrives under a different deployment's setting is still measured against this
#: process's configuration. It is the deployment's number rather than a constant
#: written here, because the length that makes sense is a function of the trained
#: context window, and that is a property of the checkpoint rather than of this
#: schema.
MAX_INPUT_CHARS: int = get_settings().ml_max_input_chars

#: How many runner-up intents a decision reports. Three is enough for a client to
#: offer a real choice without turning the response into the full fourteen-class
#: softmax, which would be a second, less legible copy of the same prediction.
MAX_ALTERNATIVES: int = 3


class RouteRequest(BaseModel):
    """The one thing a caller submits: the text to classify.

    **Extra keys are refused rather than dropped.** Pydantic's default is to
    ignore an unknown field, which would answer ``{"text": ..., "intent": ...}``
    with a cheerful 200 and no indication that the client had tried to steer the
    classifier by naming the class it wanted.
    """

    model_config = ConfigDict(extra="forbid")

    text: str = Field(
        min_length=1,
        max_length=MAX_INPUT_CHARS,
        description=(
            "The utterance to classify, sent verbatim and untransformed. Normalising, "
            "lowercasing or stripping it before it reaches the tokenizer would be a "
            "distribution shift the model was never trained on."
        ),
        examples=["add a task to draft the migration plan for friday"],
    )


class IntentAlternativeRead(BaseModel):
    """One runner-up class and the probability behind it."""

    model_config = ConfigDict(from_attributes=True)

    intent: str = Field(description="The runner-up intent name from the 14-class taxonomy.")
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Softmax probability of this class for this utterance.",
    )


class ServiceTargetRead(BaseModel):
    """The existing NEXO service an accepted decision lands on.

    A *pointer*, not an invocation: naming ``TaskService.list`` tells a caller
    which surface they reached and which call to make next, while the call itself
    stays with them so it runs through the same authenticated, owner-scoped route
    as one the user typed by hand.
    """

    model_config = ConfigDict(from_attributes=True)

    service: str = Field(description="Class name of the existing service, e.g. ``TaskService``.")
    module: str = Field(description="Import path of that service's module.")
    entrypoint: str = Field(description="The first call to make on it, e.g. ``list``.")


class RoutingDecisionRead(BaseModel):
    """What NEXUS decided to do about one utterance.

    ``status`` is the field a client branches on, and it is a closed set of four:
    ``accepted`` (a service is named and ``target`` is set), ``uncertain`` (the
    model answered but not strongly enough to act), ``out_of_scope`` (NEXUS has no
    surface for this), ``generation_unavailable`` (recognised as a generative
    class, which NEXUS does not serve). Every one of them is a 200 — the router
    worked and the answer is the answer.
    """

    model_config = ConfigDict(from_attributes=True)

    intent: str = Field(
        description="Winning intent name; always a member of the 14-class taxonomy."
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="The model's confidence in this utterance, not its test-set accuracy.",
    )
    threshold: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence below which NEXUS declines to name a service.",
    )
    status: str = Field(
        description="One of ``accepted``, ``uncertain``, ``out_of_scope``, ``generation_unavailable``."
    )
    destination: str = Field(
        description=(
            "Where the intent goes: an existing ``api/v1/...`` prefix, "
            "``large-model:unavailable`` for the generative classes, or ``abstain``."
        )
    )
    destination_kind: str = Field(description="One of ``router``, ``large_model``, ``fallback``.")
    target: ServiceTargetRead | None = Field(
        default=None,
        description="The service to call, or null when the decision named none.",
    )
    reason: str = Field(
        default="",
        description="Human-readable explanation, safe to render to a user.",
    )
    alternatives: list[IntentAlternativeRead] = Field(
        default_factory=list,
        max_length=MAX_ALTERNATIVES,
        description="Runner-up intents, highest first; empty when the model returned none.",
    )

    @classmethod
    def from_decision(cls, decision: RoutingDecision) -> RoutingDecisionRead:
        """Build the response from an :class:`app.ml.schemas.RoutingDecision`.

        ``from_attributes`` does the bulk of it, because the dataclass fields and
        this model are deliberately the same names. The alternatives are the one
        part that is not: they live on the decision's nested
        :class:`~app.ml.schemas.IntentPrediction` rather than on the decision
        itself, which keeps the dataclass honest about the fact that a decision
        can exist with no prediction attached.

        Args:
            decision: The router's verdict, prediction included when the
                classifier actually ran.

        Returns:
            The response model for this decision.
        """
        read = cls.model_validate(decision, from_attributes=True)
        alternatives = decision.prediction.alternatives if decision.prediction else ()
        read.alternatives = [
            IntentAlternativeRead(intent=str(name), confidence=float(score))
            for name, score in alternatives[:MAX_ALTERNATIVES]
        ]
        return read


class ModelIdentityRead(BaseModel):
    """Which checkpoint answered, from where, and what it cost to load.

    Diagnostics rather than routing information: a caller branches on
    ``device`` and ``load_seconds`` to understand latency, and on the rest to
    answer "is this the model I think it is" after a deployment changed.
    ``checkpoint`` is a path NEXUS resolved. It is reported, never accepted — a
    caller able to choose it would be choosing which weights answer them.
    """

    model_config = ConfigDict(from_attributes=True)

    base_model: str = Field(description="Architecture family the checkpoint was trained from.")
    architecture: str = Field(description="The checkpoint's own architecture entry.")
    device: str = Field(description="``cpu``, ``cuda`` or whatever the resolved device reports.")
    label_count: int = Field(ge=1, description="Number of classes the model predicts.")
    max_sequence_length: int = Field(ge=1, description="Trained context length, in subword tokens.")
    parameter_count: int = Field(ge=0, description="Trainable parameters in the loaded model.")
    checkpoint: str = Field(description="Resolved checkpoint directory. Reported, never accepted.")
    load_seconds: float = Field(ge=0.0, description="Wall-clock time spent loading the checkpoint.")


class IntentRouteRead(BaseModel):
    """One entry of the label set, as the router understands it.

    Published so a client can render the surface NEXUS offers without
    hard-coding it: the fourteen intents, where each one lands and which service
    answers it. It is the same table the router routes with, so a page built from
    this response cannot describe a capability the router no longer has.
    """

    model_config = ConfigDict(from_attributes=True)

    intent: str = Field(description="Intent name, e.g. ``task_manage``.")
    description: str = Field(description="What the class means, in prose.")
    destination: str = Field(description="Where the intent goes when it wins.")
    destination_kind: str = Field(description="One of ``router``, ``large_model``, ``fallback``.")
    service: str | None = Field(
        default=None,
        description="Service behind this intent, or null for a class with no router behind it.",
    )
    entrypoint: str | None = Field(
        default=None,
        description="First call to make on that service, or null when there is none.",
    )


class MLStatusRead(BaseModel):
    """Diagnostics for the intent classifier.

    **A degraded runtime is still a 200.** This endpoint exists to say that the
    classifier is off, unavailable or failed to load; answering 503 would make the
    one route that could explain the outage part of the outage. ``available`` and
    ``unavailable_reason`` carry the condition, and ``model`` is null when no
    weights are loaded — an absent identity is a fact, not a hole in the response.
    """

    enabled: bool = Field(description="Whether ML is switched on for this deployment.")
    available: bool = Field(description="Whether a working classifier can serve a prediction.")
    unavailable_reason: str | None = Field(
        default=None,
        description="Why it cannot, in machine-readable terms. Null when it can.",
    )
    model: ModelIdentityRead | None = Field(
        default=None,
        description="The loaded checkpoint's identity, or null when nothing is loaded.",
    )
    threshold: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence below which the router declines to name a service.",
    )
    taxonomy_version: str = Field(description="Version of the intent label set in use.")
    intents: list[IntentRouteRead] = Field(
        default_factory=list,
        description="Every class the classifier predicts, with its destination and service.",
    )
