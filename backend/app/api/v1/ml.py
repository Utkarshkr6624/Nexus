"""Intent-routing endpoints: the Phase 10 classifier, over HTTP.

Two endpoints, and the count is the design.

NEXUS had no chat and no natural-language surface before this phase, so this is
a genuinely new door rather than a fourth spelling of something existing. It is
kept to two because the second answers the first: ``POST /ml/route`` already
returns the intent *and* the confidence, so a separate ``/predict`` would be the
same payload under a second URL, and a second URL is a second thing to
authenticate, version, document and eventually deprecate.

Where the work happens
----------------------
Not here. This router resolves the caller, validates one bounded string, asks
:class:`~app.ml.runtime.MLRuntime` for a prediction and hands that prediction to
:class:`~app.ml.router.IntentRouter`. The inference is
:meth:`app.ml.classifier.IntentClassifier.predict` and the policy is the router's;
neither is re-implemented here. The router module's only judgement is which HTTP
answer to give, and it makes exactly one: a runtime that cannot classify gets a
**503**, never a fabricated prediction. Answering 200 with an invented intent
would be worse than refusing, because the caller's next move is to call a service
on the strength of it — NEXUS would be inventing a user's instruction and then
acting on it.

**Inference runs on a worker thread.** A 183M-parameter forward pass on CPU is
hundreds of milliseconds of solid compute, and the only thing worse than serving
it slowly is stalling the event loop while it happens: every other route in the
process would queue behind one classification. ``run_in_threadpool`` is used for
the same reason ``app.main`` uses it to load the weights.

Authentication, and why this is not an anonymous surface
--------------------------------------------------------
Both routes require :data:`~app.api.deps.AuthenticatedUser` rather than the
plain current-user alias. ``/ml/route`` accepts arbitrary free text and answers
"which of NEXUS's surfaces does this mean" — an oracle over the taxonomy, free for
an anonymous caller to enumerate, and exactly the shape of a model-extraction
probe. The session-aware alias is used rather than ``CurrentUser`` because it also
honours revocation: a user who signed a device out must not keep routing through
it for as long as the access token is still valid.

Permission
----------
Both routes are gated on ``analytics.read``, and that is a deliberate reuse
rather than a missing ``ml.*``. Phases 7, 8 and 9 all gate new surfaces on
existing capabilities — the Risk Center's lifecycle *writes* run on
``analytics.read``, for the same reason — and ``tests/test_permissions.py`` pins
the ``Permission`` member set as a literal, so a new member would be a test edit
as well as a grant decision. A capability that would be granted to exactly the
roles ``analytics.read`` already names is not a new capability.

Degradation is a first-class answer
-----------------------------------
``GET /ml/status`` reports a runtime that is disabled, unloaded or broken as a
**200**. This is the health-endpoint precedent verbatim: the endpoint reporting
that a dependency is degraded is itself healthy, and returning 503 would make the
one route that could explain an outage part of it. The flip side is that
``/ml/route`` is the route that *does* fail closed, and it does so through
:class:`~app.ml.exceptions.MLUnavailableError` so the shared envelope renders it
as ``ml_unavailable`` rather than as an anonymous 500.

Route order
-----------
Nothing here is parameterised — there is no ``/ml/{anything}`` — so no literal
route can be shadowed by a parameterised one. Both declarations are literals and
are kept in that order anyway, per the load-bearing rule ``app/api/v1/risks.py``
spells out.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool

from app.api.deps import AuthenticatedUser, MLRuntimeDep
from app.core.deps import require_permission
from app.core.logging import get_logger, log_event
from app.core.permissions import Permission
from app.ml.exceptions import MLUnavailableError
from app.ml.router import IntentRouter, routing_taxonomy
from app.schemas.ml import (
    IntentRouteRead,
    MLStatusRead,
    ModelIdentityRead,
    RouteRequest,
    RoutingDecisionRead,
)
from ml.datasets.taxonomy import TAXONOMY_VERSION

router = APIRouter(prefix="/ml", tags=["ml"])

logger = get_logger(__name__)

#: Applied to both routes. See this module's docstring: Phases 7-9 established
#: that a new surface reuses an existing capability rather than coining one, and
#: ``tests/test_permissions.py`` asserts the ``Permission`` member set literally,
#: so adding a member here would be a test-breaking change that bought nothing —
#: ``ml.*`` would be granted to exactly the roles ``analytics.read`` already
#: names.
_ANALYTICS_READ = [Depends(require_permission(Permission.ANALYTICS_READ))]


@router.get(
    "/status",
    response_model=MLStatusRead,
    summary="Diagnostics for the intent classifier",
    description=(
        "Reports whether the classifier is enabled and available, why it is not "
        "if it is not, which checkpoint is loaded and what every intent routes to."
    ),
    dependencies=_ANALYTICS_READ,
)
async def get_ml_status(
    current_user: AuthenticatedUser,
    runtime: MLRuntimeDep,
) -> MLStatusRead:
    """Report whether NEXUS can classify, and if so what it would classify into.

    **This route answers 200 even when the classifier cannot run.** A runtime that
    is switched off, has no checkpoint on disk or failed to load is reported
    through ``available`` and ``unavailable_reason``, with ``model`` null — the
    same contract ``/api/v1/health`` uses for a database it cannot reach. A 503
    here would be the outage describing itself, and every caller that asked *what
    is wrong* would get *something is wrong* instead of the reason.

    ``unavailable_reason`` is the runtime's own closed vocabulary — ``disabled``,
    ``checkpoint_missing``, ``runtime_missing``, ``load_failed``, ``not_loaded``,
    ``stopped`` — rather than prose, because it is the string an operator
    branches on. It is null whenever the runtime is available.

    ``intents`` is the taxonomy the router is actually using rather than a
    description of it, joined from the same table a routing decision reads, so a
    client that renders "surfaces NEXUS offers" from this response cannot
    advertise a capability the router would refuse to route to.

    Errors: 401 unauthenticated, 403 without ``analytics.read``. Nothing else — in
    particular a broken classifier is a 200 here, not an error.
    """
    status = runtime.status
    settings = runtime.settings
    classifier = runtime.classifier
    identity = classifier.identity if classifier is not None else None

    return MLStatusRead(
        enabled=settings.ml_enabled,
        available=status.available,
        unavailable_reason=status.reason if not status.available else None,
        model=(
            ModelIdentityRead.model_validate(identity, from_attributes=True)
            if identity is not None
            else None
        ),
        threshold=settings.ml_confidence_threshold,
        taxonomy_version=TAXONOMY_VERSION,
        intents=[
            IntentRouteRead.model_validate({"intent": name, **entry})
            for name, entry in routing_taxonomy().items()
        ],
    )


@router.post(
    "/route",
    response_model=RoutingDecisionRead,
    summary="Classify an utterance and say where it goes",
    description=(
        "Runs the trained intent classifier over the submitted text and returns "
        "the routing decision: the intent, the confidence behind it, the surface "
        "it maps to and, when no service applies, why."
    ),
    dependencies=_ANALYTICS_READ,
)
async def route_utterance(
    payload: RouteRequest,
    current_user: AuthenticatedUser,
    runtime: MLRuntimeDep,
) -> RoutingDecisionRead:
    """Classify one utterance and report what NEXUS would do about it.

    **The text is passed through byte for byte.** Training consumed the raw
    dataset strings, so trimming or lower-casing here would be a distribution
    shift the model has never seen; the classifier's own validation is the only
    length rule that runs.

    **A refusal is a 200.** ``uncertain``, ``out_of_scope`` and
    ``generation_unavailable`` are answers the classifier gave on purpose, and
    each carries a ``reason`` the caller can show. Only a runtime that cannot
    classify at all is an error, and it fails closed: a 503 ``ml_unavailable``
    rather than an invented intent.

    **The decision names a service; it does not call one.** Slot-filling an
    utterance into ``TaskService.create(...)`` would need a second model or
    hand-written per-utterance parsers, neither of which this phase owns. The
    caller makes the call, through the same authenticated, owner-scoped route it
    would have used had they typed the request themselves.

    The submitted text is never logged — only the intent, the confidence and the
    latency. An utterance is untrusted free text and may carry a credential.

    Errors: 401 unauthenticated, 403 without ``analytics.read``, 422 for an empty
    or over-long ``text``, 503 when the classifier is unavailable, 500 if
    inference fails on a request it should have been able to answer.
    """
    classifier = runtime.classifier
    if classifier is None:
        raise MLUnavailableError(details={"reason": runtime.status.reason})

    prediction = await run_in_threadpool(classifier.predict, payload.text)
    # The router is stateless and cheap; building it per request reads the
    # threshold from the settings this request resolved rather than from a
    # module-level singleton that a reconfigured deployment would never pick up.
    decision = IntentRouter(threshold=runtime.settings.ml_confidence_threshold).route(prediction)

    log_event(
        logger,
        logging.INFO,
        "ml_route_served",
        intent=decision.intent,
        status=decision.status,
        confidence=round(float(decision.confidence), 4),
        destination=decision.destination,
        service=decision.service,
        latency_ms=round(float(prediction.latency_ms), 3),
        truncated=prediction.truncated,
        text_chars=len(payload.text),
    )
    return RoutingDecisionRead.from_decision(decision)


#: The router only; the handlers are reached through it, not imported directly.
__all__ = ["router"]
