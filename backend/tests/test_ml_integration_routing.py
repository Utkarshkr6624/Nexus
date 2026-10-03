"""The routing policy Phase 11 added between a prediction and an existing service.

The classifier in :mod:`app.ml.classifier` turns text into fourteen probabilities and
nothing else. Everything a caller actually acts on is decided by
:class:`app.ml.router.IntentRouter`, and this module pins that decision layer on its
own terms: no checkpoint, no torch, no database session, no HTTP. Every test here
builds an :class:`~app.ml.schemas.IntentPrediction` by hand, because the router's job
is a *policy* over a number the model produced, and a policy over a number is exactly
the thing you cannot test honestly through the number's producer — a real forward pass
makes it impossible to place a prediction on either side of the threshold boundary and
therefore impossible to prove where the boundary is.

What is protected, and what breaks if it moves:

* **Every router intent reaches a service that already exists.** ``SERVICE_TARGETS`` is
  the only hand-written bridge in Phase 11, and a rename or move on the service side
  would otherwise turn into an ``ACCEPTED`` decision whose target cannot be imported —
  the one failure a router exists to prevent. Hence :func:`app.ml.router.resolve_service`
  is actually called and the returned object checked for being a real class with a real
  method, rather than the routing table being compared against itself.
* **Nothing is invented.** The table's key set is compared to the taxonomy's own
  :data:`~ml.datasets.taxonomy.ROUTER_INTENTS`. A key the taxonomy does not define is a
  class the model can never predict, i.e. dead routing; a router intent with no entry is
  an ``ACCEPTED`` decision with no service behind it.
* **Every negative answer is explicit and honest.** ``code_assist`` and
  ``deep_reasoning`` are trained classes that reach no service, ``out_of_scope`` abstains,
  and anything below threshold is ``uncertain`` with the runner-up intents named so the
  caller can offer a choice. Each of these is a promise made in an API response, and a
  promise that starts claiming to answer something NEXUS cannot answer is the failure
  this module watches for.
* **The threshold is a deployment decision, not a constant.** The same prediction must
  be accepted at one threshold and refused at the next, and the boundary must be exact:
  at ``threshold`` the decision is made, at ``threshold - 1e-6`` it is not.
* **There is no second model.** Phase 11's acceptance criterion is that NEXUS runs a
  classifier and nothing else, so both the serialised decision and the source of
  ``app/ml/`` are scanned for any reference to a generative backend. The scan carries a
  positive control, because a sweep that finds nothing because it searches for the wrong
  thing is indistinguishable from a clean bill of health.

One test at the end drives the real checkpoint into the real router and is marked
``ml_model``; it is the only test in the file that needs ``torch``, and it skips cleanly
with a reason on a checkout where the gitignored Phase 10 artifacts are absent.
"""

from __future__ import annotations

import inspect
import json
import math
import re
from importlib import import_module
from pathlib import Path

import pytest

from app.ml.exceptions import InferenceError, ModelCheckpointError, ModelRuntimeError
from app.ml.router import (
    SERVICE_TARGETS,
    IntentRouter,
    resolve_service,
    routing_taxonomy,
)
from app.ml.schemas import (
    IntentPrediction,
    RoutingDecision,
    RoutingStatus,
    ServiceTarget,
)
from ml.datasets.routing import label_map
from ml.datasets.taxonomy import (
    INTENT_NAMES,
    INTENT_SPECS,
    LARGE_MODEL_INTENTS,
    ROUTER_INTENTS,
    DestinationKind,
    Intent,
    intent_spec,
)

#: The confidence a deployment uses unless it configures otherwise. Pinned because the
#: threshold argument to :class:`~app.ml.router.IntentRouter` is keyword-only with no
#: default, so this number is the contract between ``app.core.config`` and the router —
#: change it and every "accepted" in the Phase 11 acceptance run means something else.
DEPLOYMENT_THRESHOLD = 0.90

#: The eleven router intents and the existing NEXUS call each one must land on.
#: ``entrypoint`` is the *first* call a caller makes on that service, not a category, so
#: a service rename or a reorganisation that moves ``list`` off ``TaskService`` fails
#: here rather than at the first request in production.
EXPECTED_ROUTING = (
    ("task_manage", "api/v1/tasks", "TaskService", "list"),
    ("project_manage", "api/v1/projects", "ProjectService", "list"),
    ("schedule_plan", "api/v1/planner", "PlannerService", "week"),
    ("knowledge_capture", "api/v1/knowledge", "KnowledgeService", "create_note"),
    ("knowledge_lookup", "api/v1/knowledge", "KnowledgeService", "search"),
    ("analytics_insight", "api/v1/analytics", "AnalyticsService", "overview"),
    ("risk_query", "api/v1/risks", "RiskDetectionService", "evaluate"),
    ("developer_intel", "api/v1/developer", "DeveloperIntelligenceService", "summary"),
    ("learning_track", "api/v1/learning", "LearningIntelligenceService", "summary"),
    ("career_track", "api/v1/career", "CareerIntelligenceService", "summary"),
    ("account_admin", "api/v1/users", "UserService", "get_active_by_id"),
)

ROUTER_INTENT_NAMES = tuple(name for name, _destination, _service, _entry in EXPECTED_ROUTING)
GENERATION_INTENT_NAMES = tuple(str(intent) for intent in LARGE_MODEL_INTENTS)

#: Intents whose accepted target *writes* to a user's data. Called out separately
#: because the asymmetric cost is what the threshold exists for: a wrong confident task
#: write lands in somebody's calendar, while a refusal costs one clarifying turn.
MUTATING_INTENT_NAMES = (
    "task_manage",
    "project_manage",
    "schedule_plan",
    "knowledge_capture",
    "account_admin",
)

#: Any mention of one of these in the serving package would mean NEXUS had acquired a
#: generative backend of some kind. The list is deliberately a brand list rather than a
#: word list: "generative model" appears throughout ``app/ml``'s prose, because saying so
#: is how the router explains ``generation_unavailable``, and that sentence must not be
#: mistaken for an integration.
FORBIDDEN_BACKENDS = (
    "qwen",
    "ollama",
    "qlora",
    "openai",
    "anthropic",
    "google.generativeai",
    "groq",
    "mistral",
    "cohere",
    "litellm",
    "vllm",
    "llama",
)

#: ``app/ml/``. Every module of the serving package, walked for the sweep below.
APP_ML_ROOT = Path(__file__).resolve().parents[1] / "app" / "ml"

#: The serving modules the sweep must cover. If a Phase 12 module is added and this
#: list is not updated, the sweep silently stops reading it — so it is pinned.
EXPECTED_ML_MODULES = frozenset(
    {
        "__init__.py",
        "classifier.py",
        "exceptions.py",
        "model_loader.py",
        "router.py",
        "runtime.py",
        "schemas.py",
    }
)


def _prediction(intent: str, confidence: float, **kwargs) -> IntentPrediction:
    """A prediction with no model behind it."""
    return IntentPrediction(intent=intent, confidence=confidence, **kwargs)


def _ml_source_files() -> list[Path]:
    """Every ``.py`` file in ``app/ml/``, excluding compiled bytecode directories."""
    return sorted(
        path
        for path in APP_ML_ROOT.rglob("*.py")
        if "__pycache__" not in path.parts and path.parent == APP_ML_ROOT
    )


#: One alternation over every forbidden backend, longest name first. Order matters:
#: ``ollama`` contains ``llama``, so a plain substring test per name reports a mention of
#: ``ollama`` as two findings and a file that only ever says ``ollama`` looks like it
#: also reached for a Meta checkpoint. Alternation resolves each position once, and the
#: longest spelling is tried first, so the finding names the backend actually written.
_FORBIDDEN_PATTERN = re.compile(
    "|".join(re.escape(name) for name in sorted(FORBIDDEN_BACKENDS, key=len, reverse=True)),
    re.IGNORECASE,
)


def _forbidden_backends_in(text: str) -> list[str]:
    """The forbidden backends named by ``text``, case-insensitively, without duplicates."""
    return list(
        dict.fromkeys(match.group(0).lower() for match in _FORBIDDEN_PATTERN.finditer(text))
    )


# ---------------------------------------------------------------------------
# Every router intent reaches a service that already exists.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("intent_name", "destination", "service_name", "entrypoint"),
    EXPECTED_ROUTING,
    ids=[f"{name}->{service}.{entry}" for name, _d, service, entry in EXPECTED_ROUTING],
)
def test_a_confident_router_intent_is_accepted_and_names_its_existing_service(
    intent_name, destination, service_name, entrypoint
):
    """The eleven router intents are the whole point of the phase; each must land."""
    router = IntentRouter(threshold=DEPLOYMENT_THRESHOLD)

    decision = router.route(_prediction(intent_name, 0.99))

    assert decision.status == RoutingStatus.ACCEPTED, decision.reason
    assert decision.accepted is True
    assert decision.intent == intent_name
    assert decision.destination == destination
    assert decision.destination == intent_spec(intent_name).destination
    assert decision.destination_kind == str(DestinationKind.ROUTER) == "router"
    assert decision.target is not None, decision.reason
    assert decision.service == service_name
    assert decision.target.service == service_name
    assert decision.target.entrypoint == entrypoint
    assert decision.reason


@pytest.mark.parametrize(
    ("intent_name", "_destination", "service_name", "entrypoint"),
    EXPECTED_ROUTING,
    ids=[f"{name}->{service}.{entry}" for name, _d, service, entry in EXPECTED_ROUTING],
)
def test_resolving_a_routing_target_yields_the_service_class_the_table_names(
    intent_name, _destination, service_name, entrypoint
):
    """The proof that the table points at a real class, not at a plausible name."""
    target = SERVICE_TARGETS[intent_name]

    resolved = resolve_service(target)

    assert inspect.isclass(resolved), f"{target.qualified} did not resolve to a class"
    assert resolved.__name__ == service_name
    # The table names the package for the service sub-packages (``app.services.analytics``
    # re-exports from ``app.services.analytics.service``); both forms are the module the
    # import path refers to.
    assert resolved.__module__ == target.module or resolved.__module__.startswith(
        f"{target.module}."
    ), f"{resolved.__module__!r} is not inside {target.module!r}"
    assert hasattr(resolved, entrypoint) is True, (
        f"{service_name} has no {entrypoint}() for {intent_name}"
    )
    assert callable(getattr(resolved, entrypoint))


@pytest.mark.parametrize(
    ("intent_name", "_destination", "service_name", "entrypoint"),
    EXPECTED_ROUTING,
    ids=[f"{name}->{service}.{entry}" for name, _d, service, entry in EXPECTED_ROUTING],
)
def test_a_routing_entrypoint_is_the_same_call_before_and_after_resolution(
    intent_name, _destination, service_name, entrypoint
):
    """Resolution must be an identity, not a lookup that could return a namesake."""
    resolved = resolve_service(SERVICE_TARGETS[intent_name])
    declared = getattr(import_module(SERVICE_TARGETS[intent_name].module), service_name)

    assert resolved is declared
    assert getattr(resolved, entrypoint) is getattr(declared, entrypoint)


def test_a_routing_target_naming_a_missing_service_class_is_reported():
    """A renamed service must fail loudly at resolve time, not return something."""
    target = ServiceTarget(
        service="NoSuchService",
        module="app.services.task_service",
        entrypoint="list",
    )

    with pytest.raises(ModelRuntimeError, match="runtime"):
        resolve_service(target)


def test_a_routing_target_naming_a_missing_module_is_reported():
    """A moved service must fail loudly too, not degrade into a silently absent class."""
    target = ServiceTarget(
        service="TaskService",
        module="app.services.no_such_module",
        entrypoint="list",
    )

    with pytest.raises(ModelRuntimeError, match="runtime"):
        resolve_service(target)


# ---------------------------------------------------------------------------
# Nothing is invented.
# ---------------------------------------------------------------------------


def test_the_service_targets_are_exactly_the_taxonomys_router_intents():
    """One entry per router intent: no more, no fewer."""
    assert set(SERVICE_TARGETS) == {str(intent) for intent in ROUTER_INTENTS}
    assert len(SERVICE_TARGETS) == 11
    assert set(SERVICE_TARGETS) == set(ROUTER_INTENT_NAMES)


def test_no_service_target_names_an_intent_the_taxonomy_does_not_define():
    """A key the model can never predict is dead routing, not a feature."""
    unknown = sorted(set(SERVICE_TARGETS) - set(INTENT_NAMES))

    assert unknown == []


def test_every_taxonomy_router_intent_has_a_routing_table_entry():
    """The inverse: a trained router class with nowhere to go cannot be served."""
    unrouted = sorted(
        str(intent) for intent in ROUTER_INTENTS if str(intent) not in SERVICE_TARGETS
    )

    assert unrouted == []


def test_the_eleven_router_intents_occupy_the_first_eleven_class_ids():
    """Class order is the taxonomy order; the router classes must stay ahead of the rest.

    A checkpoint whose head put ``out_of_scope`` at index 0 would still be a 14-class
    model that passes a count check, so the ordering is pinned rather than inferred.
    """
    classes = label_map()

    indices = sorted(classes[name] for name in SERVICE_TARGETS)
    assert indices == list(range(11))
    assert classes["code_assist"] == 11
    assert classes["deep_reasoning"] == 12
    assert classes["out_of_scope"] == 13


# ---------------------------------------------------------------------------
# The two classes NEXUS trains but cannot serve.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("intent_name", GENERATION_INTENT_NAMES, ids=GENERATION_INTENT_NAMES)
def test_a_confident_generation_intent_is_declined_rather_than_served(intent_name):
    """Recognising the request is useful; answering it by improvising is not."""
    router = IntentRouter(threshold=DEPLOYMENT_THRESHOLD)

    decision = router.route(_prediction(intent_name, 0.99))

    assert decision.status == RoutingStatus.GENERATION_UNAVAILABLE
    assert decision.accepted is False
    assert decision.destination == "large-model:unavailable"
    assert decision.destination_kind == str(DestinationKind.LARGE_MODEL) == "large_model"
    assert decision.target is None
    assert decision.service is None


@pytest.mark.parametrize("intent_name", GENERATION_INTENT_NAMES, ids=GENERATION_INTENT_NAMES)
def test_the_generation_refusal_is_explicit_and_promises_nothing(intent_name):
    """The reason reaches a client; it must not read as a partial answer."""
    router = IntentRouter(threshold=DEPLOYMENT_THRESHOLD)

    decision = router.route(_prediction(intent_name, 0.99))

    assert decision.reason, "a refusal with no reason is a silent drop"
    assert intent_name in decision.reason
    assert "no generative model" in decision.reason
    for promise in ("here is", "here's", "i wrote", "i generated", "use this code"):
        assert promise not in decision.reason.lower(), decision.reason


# ---------------------------------------------------------------------------
# Abstention.
# ---------------------------------------------------------------------------


def test_a_confident_out_of_scope_prediction_abstains_without_a_target():
    """Abstention is a prediction NEXUS can be right about, so it must be explicit."""
    router = IntentRouter(threshold=DEPLOYMENT_THRESHOLD)

    decision = router.route(_prediction("out_of_scope", 0.99))

    assert decision.status == RoutingStatus.OUT_OF_SCOPE
    assert decision.accepted is False
    assert decision.intent == "out_of_scope"
    assert decision.destination == "abstain"
    assert decision.destination_kind == str(DestinationKind.FALLBACK) == "fallback"
    assert decision.target is None
    assert decision.service is None


def test_the_abstention_names_the_surfaces_nexus_does_have():
    """Derived from the taxonomy, so it cannot list a router that has been removed."""
    router = IntentRouter(threshold=DEPLOYMENT_THRESHOLD)

    decision = router.route(_prediction("out_of_scope", 0.99))

    router_destinations = {
        spec.destination for spec in INTENT_SPECS if spec.destination_kind is DestinationKind.ROUTER
    }
    assert router_destinations
    for destination in router_destinations:
        assert destination in decision.reason, decision.reason
    assert "large-model:unavailable" not in decision.reason


# ---------------------------------------------------------------------------
# Low confidence never reaches a service.
# ---------------------------------------------------------------------------

SUB_THRESHOLD_CONFIDENCES = (0.0, 0.1, 0.5, DEPLOYMENT_THRESHOLD - 1e-6)


@pytest.mark.parametrize("intent_name", INTENT_NAMES, ids=INTENT_NAMES)
@pytest.mark.parametrize(
    "confidence", SUB_THRESHOLD_CONFIDENCES, ids=[str(value) for value in SUB_THRESHOLD_CONFIDENCES]
)
def test_every_intent_below_the_threshold_is_uncertain_and_calls_nothing(intent_name, confidence):
    """Confidence is checked first, so even a trained unservable class is refused here."""
    router = IntentRouter(threshold=DEPLOYMENT_THRESHOLD)

    decision = router.route(_prediction(intent_name, confidence))

    assert decision.status == RoutingStatus.UNCERTAIN, decision.reason
    assert decision.accepted is False
    assert decision.intent == intent_name
    assert decision.target is None
    assert decision.service is None


@pytest.mark.parametrize("intent_name", MUTATING_INTENT_NAMES, ids=MUTATING_INTENT_NAMES)
def test_a_low_confidence_write_intent_never_reaches_a_mutating_service(intent_name):
    """The asymmetry the threshold exists for: a wrong write costs more than a question."""
    router = IntentRouter(threshold=DEPLOYMENT_THRESHOLD)

    decision = router.route(_prediction(intent_name, DEPLOYMENT_THRESHOLD - 1e-6))

    assert decision.status == RoutingStatus.UNCERTAIN
    assert decision.target is None
    assert decision.destination == intent_spec(intent_name).destination
    assert SERVICE_TARGETS[intent_name].service not in json.dumps(decision.to_dict())


def test_a_prediction_exactly_at_the_threshold_is_accepted():
    """The boundary is inclusive: at the configured confidence the decision is made."""
    router = IntentRouter(threshold=DEPLOYMENT_THRESHOLD)

    decision = router.route(_prediction("task_manage", DEPLOYMENT_THRESHOLD))

    assert decision.status == RoutingStatus.ACCEPTED
    assert decision.target is not None


def test_a_prediction_one_step_below_the_threshold_is_not_accepted():
    """And exclusive on the other side, so the boundary cannot be smeared by rounding."""
    router = IntentRouter(threshold=DEPLOYMENT_THRESHOLD)

    decision = router.route(_prediction("task_manage", DEPLOYMENT_THRESHOLD - 1e-6))

    assert decision.status == RoutingStatus.UNCERTAIN
    assert decision.target is None


def test_a_not_a_number_confidence_is_uncertain_rather_than_confident():
    """NaN compares false against every bound, so a bare ``<`` would accept it."""
    router = IntentRouter(threshold=DEPLOYMENT_THRESHOLD)

    decision = router.route(_prediction("task_manage", math.nan))

    assert math.isnan(decision.confidence)
    assert decision.status == RoutingStatus.UNCERTAIN
    assert decision.target is None


# ---------------------------------------------------------------------------
# The threshold is configuration, never a constant.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("threshold", "expected_status"),
    [
        (0.50, RoutingStatus.ACCEPTED),
        (DEPLOYMENT_THRESHOLD, RoutingStatus.UNCERTAIN),
        (0.99, RoutingStatus.UNCERTAIN),
    ],
    ids=["0.5", "0.9", "0.99"],
)
def test_the_same_prediction_is_accepted_or_refused_as_the_threshold_moves(
    threshold, expected_status
):
    """One prediction, three deployments, two different answers — none of them in code."""
    prediction = _prediction("task_manage", 0.7)

    decision = IntentRouter(threshold=threshold).route(prediction)

    assert decision.status == expected_status
    assert (decision.target is not None) is (expected_status == RoutingStatus.ACCEPTED)


@pytest.mark.parametrize("threshold", [0.5, DEPLOYMENT_THRESHOLD, 0.99], ids=["0.5", "0.9", "0.99"])
@pytest.mark.parametrize("intent_name", INTENT_NAMES, ids=INTENT_NAMES)
def test_every_decision_echoes_the_threshold_of_the_router_that_made_it(intent_name, threshold):
    """A client rendering the reason needs the number that produced the verdict."""
    router = IntentRouter(threshold=threshold)

    decision = router.route(_prediction(intent_name, 0.95))

    assert router.threshold == threshold
    assert decision.threshold == threshold
    assert decision.to_dict()["threshold"] == threshold


@pytest.mark.parametrize("threshold", [0.0, -0.1, 1.01, 2.0], ids=["0", "-0.1", "1.01", "2"])
def test_a_threshold_outside_the_unit_interval_is_refused_at_construction(threshold):
    """Zero would accept everything and above one would refuse everything; both look working."""
    with pytest.raises(ValueError, match=r"threshold must lie in \(0, 1\]"):
        IntentRouter(threshold=threshold)


def test_a_threshold_of_exactly_one_is_permitted_and_accepts_a_certain_prediction():
    """The upper bound is included, and a certain prediction still names its service."""
    router = IntentRouter(threshold=1.0)

    decision = router.route(_prediction("task_manage", 1.0))

    assert router.threshold == 1.0
    assert decision.status == RoutingStatus.ACCEPTED
    assert decision.target is not None


# ---------------------------------------------------------------------------
# Uncertainty names the alternatives.
# ---------------------------------------------------------------------------


def test_an_uncertain_decision_names_the_runner_up_intents():
    """A refusal a caller can act on offers a choice rather than shrugging."""
    router = IntentRouter(threshold=DEPLOYMENT_THRESHOLD)
    prediction = _prediction(
        "analytics_insight",
        0.4,
        alternatives=(("task_manage", 0.3), ("knowledge_lookup", 0.2), ("risk_query", 0.1)),
    )

    decision = router.route(prediction)

    assert decision.status == RoutingStatus.UNCERTAIN
    assert "Did you mean" in decision.reason
    assert "task_manage (30%)" in decision.reason
    assert "knowledge_lookup (20%)" in decision.reason
    assert "risk_query (10%)" in decision.reason


def test_an_uncertain_decision_never_offers_the_winning_intent_as_its_own_alternative():
    """`"did you mean task_manage?"` when task_manage just won is a non-answer."""
    router = IntentRouter(threshold=DEPLOYMENT_THRESHOLD)
    prediction = _prediction(
        "task_manage",
        0.4,
        alternatives=(("task_manage", 0.4), ("risk_query", 0.2), ("career_track", 0.1)),
    )

    decision = router.route(prediction)

    assert "Did you mean task_manage" not in decision.reason
    assert "Did you mean risk_query (20%)" in decision.reason
    assert "career_track (10%)" in decision.reason


def test_an_uncertain_prediction_with_no_alternatives_asks_for_a_rephrasing():
    """Some callers construct predictions without runner-ups; the reason must still read."""
    router = IntentRouter(threshold=DEPLOYMENT_THRESHOLD)

    decision = router.route(_prediction("task_manage", 0.2, alternatives=()))

    assert decision.status == RoutingStatus.UNCERTAIN
    assert "Rephrasing with a surface name would help." in decision.reason
    assert "Did you mean" not in decision.reason


def test_an_uncertain_reason_names_the_confidence_and_the_threshold_it_was_refused_against():
    """The caller is told what it missed, not merely that it was refused."""
    router = IntentRouter(threshold=0.75)

    decision = router.route(_prediction("task_manage", 0.5))

    assert "50%" in decision.reason
    assert "75%" in decision.reason


# ---------------------------------------------------------------------------
# The reported taxonomy is the real taxonomy.
# ---------------------------------------------------------------------------


def test_the_routing_taxonomy_has_one_entry_per_taxonomy_intent():
    """A class added to training must not be able to appear in one report and not another."""
    entries = routing_taxonomy()

    assert len(entries) == 14
    assert set(entries) == set(INTENT_NAMES)


@pytest.mark.parametrize("intent_name", INTENT_NAMES, ids=INTENT_NAMES)
def test_the_routing_taxonomy_agrees_with_the_taxonomy_it_claims_to_report(intent_name):
    """A helper derived from the taxonomy can still drift if it re-derives anything."""
    spec = intent_spec(intent_name)
    entry = routing_taxonomy()[intent_name]

    assert entry["destination"] == spec.destination
    assert entry["destination_kind"] == str(spec.destination_kind)
    assert entry["description"] == spec.description


def test_the_routing_taxonomy_names_a_service_only_where_one_exists():
    """Three classes have no service and must report ``None`` rather than guess at one."""
    entries = routing_taxonomy()
    with_service = {name for name, entry in entries.items() if entry["service"] is not None}
    with_entrypoint = {name for name, entry in entries.items() if entry["entrypoint"] is not None}

    assert with_service == set(SERVICE_TARGETS)
    assert with_entrypoint == set(SERVICE_TARGETS)
    for name in (*GENERATION_INTENT_NAMES, str(Intent.OUT_OF_SCOPE)):
        assert entries[name]["service"] is None
        assert entries[name]["entrypoint"] is None


def test_the_routing_taxonomy_and_the_router_agree_on_destination_for_every_intent():
    """The report and the router must not describe two different systems."""
    entries = routing_taxonomy()
    router = IntentRouter(threshold=DEPLOYMENT_THRESHOLD)

    for intent_name in INTENT_NAMES:
        decision = router.route(_prediction(intent_name, 0.99))

        assert decision.destination == entries[intent_name]["destination"]
        assert decision.destination_kind == entries[intent_name]["destination_kind"]


# ---------------------------------------------------------------------------
# No second model.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("intent_name", INTENT_NAMES, ids=INTENT_NAMES)
def test_no_serialised_routing_decision_references_a_generative_backend(intent_name):
    """What the client receives must not name an engine that NEXUS does not run."""
    router = IntentRouter(threshold=DEPLOYMENT_THRESHOLD)

    payload = json.dumps(router.route(_prediction(intent_name, 0.99)).to_dict())

    assert _forbidden_backends_in(payload) == []


def test_the_serving_package_names_no_generative_backend_anywhere():
    """The stronger form: a fact about the source, so a future import cannot sneak one in."""
    offenders: list[str] = []
    for path in _ml_source_files():
        found = _forbidden_backends_in(path.read_text(encoding="utf-8", errors="replace"))
        if found:
            offenders.append(f"{path.name}: {found}")

    assert offenders == []


def test_the_generative_backend_sweep_actually_reads_the_whole_serving_package():
    """A sweep over the wrong directory is the most dangerous kind of no result."""
    files = _ml_source_files()

    assert {path.name for path in files} == EXPECTED_ML_MODULES
    assert APP_ML_ROOT.is_dir()
    assert sum(len(path.read_text(encoding="utf-8")) for path in files) > 10_000


def test_the_generative_backend_sweep_would_find_one_if_it_were_there():
    """Positive control: the needle is planted in a real file's real text."""
    clean = (APP_ML_ROOT / "router.py").read_text(encoding="utf-8")
    planted = f"{clean}\n# client = SomeClient(api_key='x')  # ollama\n"

    assert _forbidden_backends_in(clean) == []
    assert _forbidden_backends_in(planted) == ["ollama"]


@pytest.mark.parametrize("backend", FORBIDDEN_BACKENDS, ids=FORBIDDEN_BACKENDS)
def test_each_forbidden_backend_name_is_recognised_by_the_sweep(backend):
    """Every entry of the list is load-bearing; an unread one is a hole in the gate."""
    assert _forbidden_backends_in(f"use {backend.upper()} to answer") == [backend]


def test_the_routing_table_names_only_nexus_services():
    """Whatever the sweep finds, every target must be an ``app.services`` module."""
    assert SERVICE_TARGETS
    for intent_name, target in SERVICE_TARGETS.items():
        assert intent_name in INTENT_NAMES
        assert target.module.startswith("app.services."), target.module
        assert target.service and target.entrypoint
        assert target.qualified == f"{target.module}.{target.service}"


# ---------------------------------------------------------------------------
# Robustness.
# ---------------------------------------------------------------------------


def test_an_intent_outside_the_taxonomy_is_refused_rather_than_routed():
    """A checkpoint disagreeing with the taxonomy is a server fault, so it raises."""
    router = IntentRouter(threshold=DEPLOYMENT_THRESHOLD)
    prediction = IntentPrediction(intent="task_managment", confidence=0.99)

    with pytest.raises(InferenceError, match="Intent classification failed"):
        router.route(prediction)


def test_an_unknown_intent_is_refused_even_when_it_would_be_confidently_refused_as_uncertain():
    """The spec lookup happens before the confidence check, so no branch can skip it."""
    router = IntentRouter(threshold=DEPLOYMENT_THRESHOLD)

    with pytest.raises(InferenceError):
        router.route(IntentPrediction(intent="", confidence=0.01))

    with pytest.raises(InferenceError):
        router.route(IntentPrediction(intent="TASK_MANAGE", confidence=0.99))


def test_a_router_intent_with_no_routing_table_entry_is_a_named_failure(monkeypatch):
    """Silently dropping the target would hand back ``ACCEPTED`` that cannot be acted on."""
    monkeypatch.setattr("app.ml.router.SERVICE_TARGETS", {})
    router = IntentRouter(threshold=DEPLOYMENT_THRESHOLD)

    with pytest.raises(ModelRuntimeError, match="runtime"):
        router.route(_prediction("task_manage", 0.99))


def test_a_prediction_is_carried_through_onto_the_decision_unchanged():
    """The caller needs the original prediction beside the verdict, not a copy of it."""
    router = IntentRouter(threshold=DEPLOYMENT_THRESHOLD)
    prediction = _prediction("task_manage", 0.99, latency_ms=12.5, truncated=True)

    decision = router.route(prediction)

    assert decision.prediction is prediction
    assert decision.confidence == prediction.confidence
    assert decision.to_dict()["threshold"] == decision.threshold
    assert isinstance(decision, RoutingDecision)


# ---------------------------------------------------------------------------
# End to end, once, with the real checkpoint.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def real_classifier():
    """The real 14-class checkpoint, loaded once for the whole session.

    Loading costs roughly five seconds and 703 MiB, so it is session-scoped rather than
    per-test. Both preconditions skip with a reason instead of erroring: ``torch`` is a
    large optional install, and ``backend/ml/artifacts`` is gitignored, so a clean
    checkout has neither.
    """
    pytest.importorskip("torch", reason="torch is not installed")
    pytest.importorskip("transformers", reason="transformers is not installed")

    from app.ml.classifier import IntentClassifier
    from app.ml.model_loader import load_model, resolve_checkpoint_dir

    try:
        checkpoint = resolve_checkpoint_dir()
    except ModelCheckpointError as exc:
        pytest.skip(f"the Phase 10 checkpoint is not present: {exc}")

    classifier = IntentClassifier(
        load_model(checkpoint, device="cpu"), threshold=DEPLOYMENT_THRESHOLD
    )
    try:
        yield classifier
    finally:
        classifier.close()


@pytest.mark.ml_model
@pytest.mark.parametrize(
    ("utterance", "intent_name", "service_name", "entrypoint"),
    [
        ("Mark the API contract task as done", "task_manage", "TaskService", "list"),
        ("What does my calendar look like on Thursday?", "schedule_plan", "PlannerService", "week"),
        ("Write a pytest fixture that hands out a database session", "code_assist", "", ""),
        ("What's the weather in Porto tomorrow?", "out_of_scope", "", ""),
    ],
    ids=["task_manage", "schedule_plan", "code_assist", "out_of_scope"],
)
def test_the_real_classifier_routes_real_utterances_onto_the_pinned_policy(
    real_classifier, utterance, intent_name, service_name, entrypoint
):
    """One pass through the whole boundary, to prove the policy meets the real numbers.

    The unit tests above place predictions by hand; this one asks the model instead. The
    policies it pins are the ones the phase was accepted on: two router intents reach
    their existing services, and the two refusal classes refuse.
    """
    router = IntentRouter(threshold=DEPLOYMENT_THRESHOLD)

    prediction = real_classifier.predict(utterance)
    decision = router.route(prediction)

    assert prediction.intent == intent_name, prediction.to_dict()
    assert prediction.confidence >= DEPLOYMENT_THRESHOLD, prediction.to_dict()
    assert decision.intent == intent_name
    if service_name:
        assert decision.status == RoutingStatus.ACCEPTED
        assert decision.target is not None
        assert decision.target.service == service_name
        assert decision.target.entrypoint == entrypoint
        resolved = resolve_service(decision.target)
        assert resolved.__name__ == service_name
    else:
        assert decision.status in {
            RoutingStatus.GENERATION_UNAVAILABLE,
            RoutingStatus.OUT_OF_SCOPE,
        }
        assert decision.target is None
