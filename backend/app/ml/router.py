"""Turn a classifier prediction into a decision about an existing NEXUS service.

Phase 10 trained a fourteen-class intent classifier and stopped there. Phase 11's
first job is not to build a new subsystem around it — it is to let the number the
model produces *mean* something. This module is that meaning: given an
:class:`~app.ml.schemas.IntentPrediction`, it answers three questions and nothing
else — is NEXUS confident enough to act, which of the services that already exist
does this land on, and if there is no service, why not.

**The router decides; it does not act.** Turning *"add a task to draft the
migration plan for Friday"* into ``TaskService.create_task(title=..., due_date=...)``
is slot filling, and it needs either a second model or hand-written per-utterance
parsers, neither of which Phase 11 owns. Everything below is the deterministic half
the classifier is allowed to influence: a class name in, a named existing service
out. A caller that wants the service call still makes it, still owns the
transaction, and still decides what to do with a rejection from it.

**Every negative answer is explicit.** ``code_assist`` and ``deep_reasoning`` are
trained classes, so the model *will* return them, and they reach no service. They
get :data:`~app.ml.schemas.RoutingStatus.GENERATION_UNAVAILABLE` and the taxonomy's
``large-model:unavailable`` destination — NEXUS runs no generative model and would
rather say so than answer a code question with a confident non-answer. A
below-threshold prediction gets :data:`~app.ml.schemas.RoutingStatus.UNCERTAIN` and
the runner-up intents, so the caller can offer *"did you mean…"* instead of a flat
refusal. An ``out_of_scope`` prediction gets the list of surfaces NEXUS *does* have,
derived from the taxonomy rather than written out, so the hint cannot drift from the
routers that actually exist.

**The taxonomy decides, this module only maps.** Destination and destination kind
come from :func:`ml.datasets.taxonomy.intent_spec` at call time; the eleven
:data:`SERVICE_TARGETS` entries are the only thing here hand-written, and they are
the bridge the classifier does not know how to make. A :data:`DestinationKind.ROUTER`
intent with no entry in :data:`SERVICE_TARGETS` is therefore a bug in this file, not a
user error, and it raises rather than returning a decision with no service — silently
dropping the target would hand the caller an ``ACCEPTED`` decision that cannot be
acted on, which is the exact failure mode a router exists to prevent.

**Services are resolved lazily, never imported here.** Every module under
``app.services`` imports the ORM session machinery, and importing them at module
scope would make the classifier half of ``app.ml`` unimportable without a database —
turning a routing table into a test that needs the full stack to run. The import
paths are therefore strings, and :func:`resolve_service` performs the import on
demand. That also gives the routing table something to be wrong about loudly: a
renamed module fails at resolve time with a named exception instead of quietly
pointing at nothing.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from importlib import import_module
from types import MappingProxyType
from typing import Any

from app.core.logging import get_logger, log_event
from app.ml.exceptions import InferenceError, ModelRuntimeError
from app.ml.schemas import (
    IntentPrediction,
    RoutingDecision,
    RoutingStatus,
    ServiceTarget,
)
from ml.datasets.schema import DataValidationError
from ml.datasets.taxonomy import (
    INTENT_SPECS,
    DestinationKind,
    IntentSpec,
    intent_spec,
)

__all__ = [
    "SERVICE_TARGETS",
    "IntentRouter",
    "resolve_service",
    "routing_taxonomy",
]

logger = get_logger(__name__)

#: How many runner-up intents a low-confidence decision names back to the caller.
#: Three is enough to offer a real choice without turning the reason into a table.
_ALTERNATIVES_NAMED = 3

#: The existing NEXUS service behind each :data:`DestinationKind.ROUTER` intent.
#:
#: Keys are exactly the eleven router intents, and ``entrypoint`` is the call a
#: caller makes first rather than a category: most of these intents can end at
#: several routes, and the first question is always "what exists" before it is
#: "change it". Frozen because a routing table that a request could edit is a
#: routing table that no longer describes the deployment.
SERVICE_TARGETS: Mapping[str, ServiceTarget] = MappingProxyType(
    {
        "task_manage": ServiceTarget(
            service="TaskService",
            module="app.services.task_service",
            entrypoint="list",
        ),
        "project_manage": ServiceTarget(
            service="ProjectService",
            module="app.services.project_service",
            entrypoint="list",
        ),
        "schedule_plan": ServiceTarget(
            service="PlannerService",
            module="app.services.planner_service",
            entrypoint="week",
        ),
        "knowledge_capture": ServiceTarget(
            service="KnowledgeService",
            module="app.services.knowledge_service",
            entrypoint="create_note",
        ),
        "knowledge_lookup": ServiceTarget(
            service="KnowledgeService",
            module="app.services.knowledge_service",
            entrypoint="search",
        ),
        "analytics_insight": ServiceTarget(
            service="AnalyticsService",
            module="app.services.analytics",
            entrypoint="overview",
        ),
        "risk_query": ServiceTarget(
            service="RiskDetectionService",
            module="app.services.risk",
            entrypoint="evaluate",
        ),
        "developer_intel": ServiceTarget(
            service="DeveloperIntelligenceService",
            module="app.services.developer",
            entrypoint="summary",
        ),
        "learning_track": ServiceTarget(
            service="LearningIntelligenceService",
            module="app.services.learning",
            entrypoint="summary",
        ),
        "career_track": ServiceTarget(
            service="CareerIntelligenceService",
            module="app.services.career",
            entrypoint="summary",
        ),
        "account_admin": ServiceTarget(
            service="UserService",
            module="app.services.user_service",
            entrypoint="get_active_by_id",
        ),
    }
)


def _first_sentence(text: str) -> str:
    """The opening sentence of a taxonomy description.

    Reason strings travel to a client and to a log line, and the specs are
    written for a reader of the taxonomy rather than for an API response; the
    first sentence is the part that says what the class covers, which is all a
    caller needs to be told why NEXUS declined.
    """
    head, separator, _ = text.partition(". ")
    return f"{head}." if separator else text


def resolve_service(target: ServiceTarget) -> type:
    """Import a :class:`ServiceTarget` and return the class it names.

    Lazy on purpose — see the module docstring — which also makes this the one
    place a wrong :data:`SERVICE_TARGETS` entry surfaces. Importing
    ``app.services`` costs a SQLAlchemy import, so it happens on a routing
    decision, not on ``import app.ml.router``.

    Args:
        target: The service, module path and entrypoint recorded in
            :data:`SERVICE_TARGETS`.

    Returns:
        The service class, ready for the caller to instantiate with its own
        session.

    Raises:
        ModelRuntimeError: The module could not be imported, or the named class
            is not present in it. A renamed or moved service is a deployment
            fault rather than anything the caller did, and it surfaces as a
            :class:`~app.core.exceptions.NexusError` so it renders through the
            standard envelope instead of escaping as a bare ``ImportError`` from
            inside the ML package.
    """
    try:
        module = import_module(target.module)
    except ImportError as exc:
        raise ModelRuntimeError(
            details={"service": target.service, "module": target.module}
        ) from exc

    resolved = getattr(module, target.service, None)
    if resolved is None:
        raise ModelRuntimeError(details={"service": target.service, "module": target.module})
    return resolved


def routing_taxonomy() -> dict[str, dict[str, Any]]:
    """The whole label set as an API-ready mapping, derived from the taxonomy.

    Built by walking :data:`ml.datasets.taxonomy.INTENT_SPECS` rather than by
    listing the fourteen intents here, so a class added to the training
    taxonomy cannot appear in one report and be missing from another. The
    service name is joined from :data:`SERVICE_TARGETS`, which means this view
    shows the same routing table the router actually uses instead of a second
    description of it.

    Returns:
        A mapping from intent name to its ``destination``,
        ``destination_kind``, ``description``, and — for the router-backed
        intents only — ``service`` and ``entrypoint``.
    """
    entries: dict[str, dict[str, Any]] = {}
    for spec in INTENT_SPECS:
        target = SERVICE_TARGETS.get(str(spec.intent))
        entries[str(spec.intent)] = {
            "description": spec.description,
            "destination": spec.destination,
            "destination_kind": str(spec.destination_kind),
            "service": target.service if target else None,
            "entrypoint": target.entrypoint if target else None,
        }
    return entries


class IntentRouter:
    """Policy for turning a prediction into a :class:`RoutingDecision`.

    Stateless and cheap to construct; one instance per request path is fine, and
    a module-level one held by the classifier's caller is fine too. The
    threshold is a constructor argument with no default because it is a
    deployment decision, not a property of the code: the number that separates
    *"confident enough to touch a service"* from *"ask the user"* belongs to
    the configuration that loads the model, and hard-coding it here would make
    that choice invisible at every call site.

    A below-threshold prediction never reaches a service. That is the whole
    point of the threshold: a wrong confident answer writes to the user's
    calendar, while a refusal costs one clarifying turn.

    Args:
        threshold: The minimum softmax probability at which a prediction is
            acted on. Must lie in ``(0, 1]``.

    Raises:
        ValueError: ``threshold`` is outside ``(0, 1]``. Rejected at
            construction because a threshold of zero would accept everything and
            a threshold above one would refuse everything, and both would look
            like working code.
    """

    __slots__ = ("_threshold",)

    def __init__(self, *, threshold: float) -> None:
        if not 0.0 < threshold <= 1.0:
            raise ValueError(f"threshold must lie in (0, 1], got {threshold!r}")
        self._threshold = float(threshold)

    @property
    def threshold(self) -> float:
        """The confidence a prediction must reach to name a service."""
        return self._threshold

    def route(self, prediction: IntentPrediction) -> RoutingDecision:
        """Decide what NEXUS will do about one utterance.

        The branches are ordered by how much they cost to get wrong: confidence
        is checked first so a weak prediction can never reach a service, then
        the two trained-but-unservable classes, then abstention, then the
        ordinary router-backed intents.

        Args:
            prediction: The classifier's output for one utterance.

        Returns:
            A :class:`RoutingDecision` whose ``threshold`` is this router's,
            and whose ``target`` is set only when ``status`` is
            :data:`~app.ml.schemas.RoutingStatus.ACCEPTED`.

        Raises:
            InferenceError: The prediction carries an intent name outside the
                taxonomy. The classifier and this module share one label set, so
                this means the loaded checkpoint disagrees with
                ``ml.datasets.taxonomy`` — a server fault, not a user one.
            ModelRuntimeError: A :data:`DestinationKind.ROUTER` intent has no
                entry in :data:`SERVICE_TARGETS`. Also a bug in this file: the
                decision would otherwise be ``ACCEPTED`` with no service to
                call, which reads to a caller exactly like success.
        """
        spec = self._spec_for(prediction)
        confidence = float(prediction.confidence)

        # NaN is tested explicitly rather than left to ``<``: a NaN compares
        # false against every bound, so it would sail through a bare less-than
        # check and be treated as a confident prediction.
        if math.isnan(confidence) or confidence < self._threshold:
            decision = self._uncertain(prediction, spec, confidence)
        elif spec.destination_kind is DestinationKind.LARGE_MODEL:
            decision = self._generation_unavailable(prediction, spec, confidence)
        elif spec.destination_kind is DestinationKind.FALLBACK:
            decision = self._out_of_scope(prediction, spec, confidence)
        else:
            decision = self._accepted(prediction, spec, confidence)

        log_event(
            logger,
            logging.INFO,
            "intent_routed",
            intent=decision.intent,
            status=decision.status,
            confidence=round(decision.confidence, 4),
            threshold=decision.threshold,
            destination=decision.destination,
            destination_kind=decision.destination_kind,
            service=decision.service,
            latency_ms=round(prediction.latency_ms, 3),
            truncated=prediction.truncated,
        )
        return decision

    def _spec_for(self, prediction: IntentPrediction) -> IntentSpec:
        """Resolve the prediction's taxonomy spec.

        Args:
            prediction: The classifier's output.

        Returns:
            The matching :class:`~ml.datasets.taxonomy.IntentSpec`.

        Raises:
            InferenceError: The intent name is not a member of the taxonomy.
        """
        try:
            return intent_spec(prediction.intent)
        except DataValidationError as exc:
            raise InferenceError() from exc

    def _uncertain(
        self, prediction: IntentPrediction, spec: IntentSpec, confidence: float
    ) -> RoutingDecision:
        """Build the low-confidence decision: name the runners-up, call nothing."""
        alternatives = [
            f"{name} ({score:.0%})"
            for name, score in prediction.alternatives[:_ALTERNATIVES_NAMED]
            if name != prediction.intent
        ]
        hint = (
            f" Did you mean {', '.join(alternatives)}?"
            if alternatives
            else " Rephrasing with a surface name would help."
        )
        reason = (
            f"NEXUS is not confident enough to act on this request "
            f"({confidence:.0%} against a {self._threshold:.0%} threshold)."
            f"{hint}"
        )
        return RoutingDecision(
            intent=str(spec.intent),
            confidence=confidence,
            threshold=self._threshold,
            status=RoutingStatus.UNCERTAIN,
            destination=spec.destination,
            destination_kind=str(spec.destination_kind),
            target=None,
            reason=reason,
            prediction=prediction,
        )

    def _generation_unavailable(
        self, prediction: IntentPrediction, spec: IntentSpec, confidence: float
    ) -> RoutingDecision:
        """Build the decision for a class NEXUS trains but cannot serve.

        The destination is taken from the taxonomy, which pins both generative
        classes to ``large-model:unavailable`` — the exact string the Phase 10
        contract was designed around, so it is read rather than restated here.
        """
        reason = (
            f"NEXUS recognised this as {spec.intent} and declines it: it runs no "
            f"generative model, so it will not answer by improvising one. "
            f"{_first_sentence(spec.description)}"
        )
        return RoutingDecision(
            intent=str(spec.intent),
            confidence=confidence,
            threshold=self._threshold,
            status=RoutingStatus.GENERATION_UNAVAILABLE,
            destination=spec.destination,
            destination_kind=str(spec.destination_kind),
            target=None,
            reason=reason,
            prediction=prediction,
        )

    def _out_of_scope(
        self, prediction: IntentPrediction, spec: IntentSpec, confidence: float
    ) -> RoutingDecision:
        """Build the abstention, naming the surfaces the user may have meant.

        The surfaces come from the router intents' own specs, so the list of
        suggestions is whatever NEXUS currently supports — a hard-coded list
        would keep answering for routers that had since been deleted.
        Iteration is over :data:`ml.datasets.taxonomy.INTENT_SPECS` rather than
        over the ``ROUTER_INTENTS`` frozenset, because frozenset order follows
        string hashing and this string reaches an API response where the same
        request must not produce two different sentences.
        """
        destinations: list[str] = []
        for candidate in INTENT_SPECS:
            if candidate.destination_kind is not DestinationKind.ROUTER:
                continue
            if candidate.destination not in destinations:
                destinations.append(candidate.destination)
        reason = (
            f"NEXUS has no surface for this and will not guess one. Surfaces "
            f"available: {', '.join(destinations)}."
        )
        return RoutingDecision(
            intent=str(spec.intent),
            confidence=confidence,
            threshold=self._threshold,
            status=RoutingStatus.OUT_OF_SCOPE,
            destination=spec.destination,
            destination_kind=str(spec.destination_kind),
            target=None,
            reason=reason,
            prediction=prediction,
        )

    def _accepted(
        self, prediction: IntentPrediction, spec: IntentSpec, confidence: float
    ) -> RoutingDecision:
        """Build the decision that names an existing service.

        Raises:
            ModelRuntimeError: No :data:`SERVICE_TARGETS` entry for this router
                intent, which is a defect in this module rather than anything
                about the request.
        """
        target = SERVICE_TARGETS.get(str(spec.intent))
        if target is None:
            raise ModelRuntimeError(
                details={"intent": str(spec.intent), "destination": spec.destination}
            )
        reason = (
            f"Routed to {target.service}.{target.entrypoint} "
            f"({spec.destination}); the classifier chose the surface, the caller "
            f"makes the call."
        )
        return RoutingDecision(
            intent=str(spec.intent),
            confidence=confidence,
            threshold=self._threshold,
            status=RoutingStatus.ACCEPTED,
            destination=spec.destination,
            destination_kind=str(spec.destination_kind),
            target=target,
            reason=reason,
            prediction=prediction,
        )
