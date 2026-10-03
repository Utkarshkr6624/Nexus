"""Phase 11 over HTTP: what the intent-routing surface promises a caller.

Phase 10 produced a checkpoint and stopped there; Phase 11's second half put it
inside the running FastAPI application behind two routes — ``GET /api/v1/ml/status``
and ``POST /api/v1/ml/route``. This file is the only place any of that is
exercised **through HTTP**, which is the only place the phase's real promise
lives. Everything the classifier and the router do can be unit-tested against
hand-built dataclasses, and none of that would prove a request reaches them: an
unexecuted classifier behind an unexercised router is two untested layers, and
the failure this phase exists to prevent is precisely an HTTP 200 carrying an
intent nobody asked the model for.

What the file protects, and why each one is load-bearing
----------------------------------------------------------

**A degraded classifier answers 503, never a fabricated 200.** This is the single
behaviour the whole phase turns on. ``app/api/v1/ml.py`` states it: the caller's
next move on a routing decision is to call ``TaskService``, so an invented intent
is worse than a refusal — NEXUS would be inventing a user's instruction and then
acting on it. :func:`test_a_degraded_classifier_answers_503_rather_than_fabricating_a_decision`
is the test; :func:`test_the_same_request_is_served_once_a_classifier_is_available`
is its positive control, because "the endpoint refused" and "the endpoint refuses
everything" are indistinguishable without one.

**The reason is machine-readable and is never prose.** ``MLRuntimeStatus.reason``
is a closed vocabulary a caller and an operator both branch on, so
:func:`test_every_degraded_reason_reaches_the_caller_verbatim` pins each member
this file can produce through the public API.

**A caller cannot choose which weights answer.** ``ModelIdentity.checkpoint`` is
reported and never accepted, and ``RouteRequest`` refuses unknown keys outright.
A caller who could name a path would be choosing the model, which is the one
control the deployment owns.

**Nothing about the inside escapes.** A response carries an intent, a number and
a service name — never a traceback, a torch frame, a filesystem path or a tensor.
:func:`test_no_ml_response_carries_an_internal` scans the success, 422 and 503
bodies, and :func:`test_the_internal_scanner_would_notice_one` proves the scanner
is not vacuous.

**A refusal is a first-class answer.** ``uncertain``, ``out_of_scope`` and
``generation_unavailable`` are 200s the router produced on purpose. Collapsing
them into a 404 would throw away the only part of the answer a caller can act on.

The seam this file uses
----------------------
``app.api.deps.get_ml_runtime`` reads ``request.app.state.ml_runtime`` and falls
back to the process singleton when the lifespan has not run. ``ASGITransport``
never runs a lifespan, so **``app.state.ml_runtime`` is the seam every degraded
and stubbed case below installs into** — see :func:`installed_runtime`, which is
the very attribute ``app/main.py`` assigns once ``_lifespan`` has loaded the
weights. Setting it exercises the real provider rather than bypassing it.

Authentication without a database
---------------------------------
The two routes need a caller and ``analytics.read``. Identity arrives twice — once
through :func:`app.api.deps.get_authenticated_user` and once through a
``require_permission(...)`` closure over :func:`app.core.deps.get_current_user` —
so :func:`authorised_client` overrides those two callables and leaves the
*permission gate itself* real: the 403 below is produced by
:func:`app.core.permissions.has_permission`, not by an assertion. The 401 tests
override nothing at all and therefore run the genuine unauthenticated path.

What needs the checkpoint
-------------------------
Everything marked ``ml_model`` needs the 703 MiB Phase 10 artifact, which is
gitignored and therefore absent on a clean checkout; those tests skip with a
reason naming what is missing. Everything else runs with no checkpoint, and none
of it needs a database.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from app.api.deps import get_authenticated_user
from app.core.config import get_settings
from app.core.deps import get_current_user
from app.ml.classifier import IntentClassifier
from app.ml.runtime import MLRuntime
from app.ml.schemas import IntentPrediction, ModelIdentity
from app.models.user import User
from app.schemas.ml import MAX_INPUT_CHARS
from ml.datasets.taxonomy import ROUTER_INTENTS, TAXONOMY_VERSION
from tests.test_errors import FORBIDDEN_FRAGMENTS

#: Applied to every test that needs the trained checkpoint.
ml_model = pytest.mark.ml_model

#: The fourteen intents in checkpoint class-id order. ``routing.label_map()`` is
#: the authority for that order and the taxonomy has no notion of it, so this is
#: the one place that has to be re-read if the checkpoint is ever retrained: a
#: status response whose ``intents`` stops matching this tuple is one a client
#: can no longer index by class id.
EXPECTED_INTENTS = (
    "task_manage",
    "project_manage",
    "schedule_plan",
    "knowledge_capture",
    "knowledge_lookup",
    "analytics_insight",
    "risk_query",
    "developer_intel",
    "learning_track",
    "career_track",
    "account_admin",
    "code_assist",
    "deep_reasoning",
    "out_of_scope",
)

#: The classes no service answers. A decision naming one of these must carry no
#: ``target``; a decision that did would be an ``accepted`` status with nothing to
#: call, which reads to a caller exactly like success.
UNSERVED_INTENTS = ("code_assist", "deep_reasoning", "out_of_scope")

#: The exact field set of ``RoutingDecisionRead``. Adding a field is a wire change
#: every client sees; this constant is what makes that a deliberate act rather
#: than a consequence of editing a Pydantic model.
DECISION_FIELDS = frozenset(
    {
        "intent",
        "confidence",
        "threshold",
        "status",
        "destination",
        "destination_kind",
        "target",
        "reason",
        "alternatives",
    }
)

#: The documented default from ``app/core/config.py``. Pinned literally because it
#: is a deployment decision recorded in that file's own precision/coverage table —
#: moving it is a deliberate act, and this is what makes it deliberate.
DOCUMENTED_DEFAULT_THRESHOLD = 0.90

#: Fragments that would mean the ML surface leaked its implementation. The first
#: group is inherited from ``tests/test_errors.py`` rather than restated, so the
#: two surfaces cannot drift apart on what counts as a leak. ``app.services`` and
#: ``app.repositories`` are dropped here because ``ServiceTargetRead.module``
#: publishes the service's import path as a documented field, which makes it the
#: contract rather than a leak; :func:`test_a_routing_decision_names_the_services_import_path`
#: is the other half of that trade, asserting the path is the *expected* one.
ML_INTERNALS = (
    "torch",
    "transformers",
    "safetensors",
    "safetensor",
    "site-packages",
    "site_packages",
    "huggingface",
    "logits",
    "tensor",
    "softmax",
    "token_type_ids",
    "input_ids",
    "inference_mode",
)
FORBIDDEN_IN_ML_BODY = tuple(
    fragment
    for fragment in (*FORBIDDEN_FRAGMENTS, *ML_INTERNALS)
    if fragment not in {"app.services", "app.repositories"}
)

#: A Windows drive letter or a POSIX root in the raw body. A leaked path arrives
#: JSON-escaped, but the character immediately after the colon is not, so the
#: class still matches.
_ABSOLUTE_PATH = re.compile(r"[A-Za-z]:[\\/]")

#: A token that appears nowhere else in NEXUS, so finding it in a response is
#: unambiguous evidence that the submitted text was echoed.
_CANARY = "zqxjvmark"

_TASK_UTTERANCE = "add a task to draft the migration plan for friday"
_CODE_UTTERANCE = "write a python function that reverses a linked list"
_REASONING_UTTERANCE = "think step by step about why the production migration keeps failing"
_OUT_OF_SCOPE_UTTERANCE = "what is the weather in reykjavik tomorrow"
#: Scores 0.24 on ``schedule_plan`` against this checkpoint: recognised, and not
#: nearly strongly enough to name a service.
_AMBIGUOUS_UTTERANCE = "and then"


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


class _StubClassifier:
    """Stands in for a loaded checkpoint, recording what it was asked.

    The phase's *policy* is deterministic and belongs to the router, so a double
    is the right instrument for the HTTP contract: response shape, refusal
    status, whether the checkpoint is or is not caller-controllable. It is
    deliberately not used for anything whose point is that the real model behaves
    a certain way — that is the ``ml_model`` half of this file.
    """

    def __init__(self, prediction: IntentPrediction, identity: ModelIdentity | None = None) -> None:
        self._prediction = prediction
        self._identity = identity
        self.seen: list[str] = []

    @property
    def identity(self) -> ModelIdentity | None:
        """The identity the status endpoint publishes for this model."""
        return self._identity

    def predict(self, text: str) -> IntentPrediction:
        self.seen.append(text)
        return self._prediction


class _InertWeights:
    """A ``LoadedModel`` stand-in whose forward pass must never be reached.

    ``IntentClassifier.__init__`` reads ``max_sequence_length`` and nothing else,
    and ``predict`` validates before it touches the model, so this lets the real
    validation code run while making any actual inference an immediate, loud
    failure. It is how the blank-utterance test tells "refused at validation" from
    "refused by everything".

    ``id2label`` is present because :meth:`IntentClassifier.predict` reads it
    while *logging* an inference failure; without it the double would raise out of
    the error path instead, and the test would see an escaping exception rather
    than the 500 the route is supposed to render.
    """

    max_sequence_length = 128
    id2label = ("unreachable",)

    def __getattr__(self, name: str):
        raise RuntimeError(f"inference must not reach the model (wanted {name!r})")


def _identity_for(settings) -> ModelIdentity:
    """A model identity as the loader would report it, for the server's checkpoint."""
    return ModelIdentity(
        base_model="microsoft/deberta-v3-base",
        architecture="DebertaV2ForSequenceClassification",
        device="cpu",
        label_count=len(EXPECTED_INTENTS),
        max_sequence_length=128,
        parameter_count=184_432_910,
        checkpoint=str(settings.ml_resolved_model_path),
        load_seconds=5.25,
    )


def _confident_task_prediction() -> IntentPrediction:
    """A confident, unambiguous ``task_manage`` prediction with runner-ups.

    The confidence is deliberately not round: a response that rounded or reshaped
    it would still look right on a smoke test.
    """
    return IntentPrediction(
        intent="task_manage",
        confidence=0.987_654_321,
        alternatives=(("project_manage", 0.006), ("schedule_plan", 0.002)),
        truncated=False,
        latency_ms=41.5,
    )


@contextmanager
def installed_runtime(app, runtime: MLRuntime | None) -> Iterator[None]:
    """Put ``runtime`` where ``app.api.deps.get_ml_runtime`` looks for it.

    Restores whatever was there, *including nothing*: the attribute is absent in
    any process whose lifespan has not run, and leaving a double behind would
    silently re-point every later test in the session at it.
    """
    missing = object()
    previous = getattr(app.state, "ml_runtime", missing)
    app.state.ml_runtime = runtime
    try:
        yield
    finally:
        app.state.ml_runtime = None if previous is missing else previous


def _caller(role: str = "user") -> User:
    """An in-memory account, never persisted, so no database is involved."""
    return User(username="ada", email="ada@nexus.dev", role=role, is_active=True)


def _runtime_reporting(reason: str, make_settings, tmp_path) -> MLRuntime:
    """Build an unavailable runtime whose status carries ``reason``.

    Reached through :meth:`MLRuntime.load` and :meth:`MLRuntime.shutdown` rather
    than by writing to private state, so the test exercises the same transitions
    the lifespan does.

    Args:
        reason: One of the ``MLRuntime.REASON_*`` members that is not ``available``.
        make_settings: The ``make_settings`` factory from ``tests/conftest.py``.
        tmp_path: A directory to point ``ML_MODEL_PATH`` at for the missing case.

    Returns:
        A runtime with no classifier and the requested status reason.

    Raises:
        AssertionError: For ``runtime_missing`` and ``load_failed``, which need
            torch to be absent or the weights to be corrupt and so cannot be
            produced without breaking the environment.
    """
    if reason == MLRuntime.REASON_UNLOADED:
        return MLRuntime(make_settings())
    if reason == MLRuntime.REASON_STOPPED:
        runtime = MLRuntime(
            make_settings(), classifier=_StubClassifier(_confident_task_prediction())
        )
        runtime.shutdown()
        return runtime
    if reason == MLRuntime.REASON_DISABLED:
        runtime = MLRuntime(make_settings(ML_ENABLED="false"))
        runtime.load()
        return runtime
    if reason == MLRuntime.REASON_CHECKPOINT_MISSING:
        runtime = MLRuntime(make_settings(ML_MODEL_PATH=str(tmp_path / "never-trained")))
        runtime.load()
        return runtime
    raise AssertionError(f"no public path produces the {reason!r} status here")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def authorised_client(app, offline_client) -> Iterator:
    """An offline client whose identity resolves, with the permission gate real.

    ``require_permission(...)`` builds a fresh closure per call, so it cannot be
    keyed in ``dependency_overrides``. Overriding ``get_current_user`` — the
    dependency that closure itself reads — reaches the gate without replacing it.
    """
    reader = _caller()
    app.dependency_overrides[get_current_user] = lambda: reader
    app.dependency_overrides[get_authenticated_user] = lambda: reader
    try:
        yield offline_client
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_authenticated_user, None)


@pytest.fixture
def serving_runtime(app, settings) -> Iterator[tuple[MLRuntime, _StubClassifier]]:
    """A runtime that answers, backed by :class:`_StubClassifier`."""
    classifier = _StubClassifier(_confident_task_prediction(), _identity_for(settings))
    runtime = MLRuntime(settings, classifier=classifier)
    with installed_runtime(app, runtime):
        yield runtime, classifier


@pytest.fixture
def degraded_runtime(app, settings) -> Iterator[MLRuntime]:
    """A runtime that has never loaded: ``not_loaded``, ``classifier is None``."""
    runtime = MLRuntime(settings)
    with installed_runtime(app, runtime):
        yield runtime


@pytest.fixture
def blank_text_runtime(app, settings) -> Iterator[MLRuntime]:
    """An available runtime whose model raises the moment inference is attempted."""
    classifier = IntentClassifier(_InertWeights(), threshold=0.9)  # type: ignore[arg-type]
    runtime = MLRuntime(settings, classifier=classifier)
    with installed_runtime(app, runtime):
        yield runtime


@pytest.fixture
def uncertain_runtime(app, settings) -> Iterator[_StubClassifier]:
    """A runtime whose prediction is a real one, but too weak to act on."""
    classifier = _StubClassifier(
        IntentPrediction(
            intent="task_manage",
            confidence=0.4123,
            alternatives=(
                ("project_manage", 0.31),
                ("schedule_plan", 0.19),
                ("out_of_scope", 0.05),
            ),
        )
    )
    runtime = MLRuntime(settings, classifier=classifier)
    with installed_runtime(app, runtime):
        yield classifier


@pytest.fixture(scope="session")
def live_runtime():
    """The real Phase 10 checkpoint, loaded once for the whole session.

    703 MiB and about five seconds; a function-scoped load would dominate the
    file. Skips — with a reason, never an error — on a clean checkout, because
    ``backend/ml/artifacts`` is gitignored and a fresh clone has never trained.
    """
    settings = get_settings()
    if not settings.ml_checkpoint_exists:
        pytest.skip(f"no Phase 10 checkpoint at {settings.ml_resolved_model_path}")
    pytest.importorskip("torch", reason="torch is not installed")
    pytest.importorskip("transformers", reason="transformers is not installed")

    from app.main import app as fastapi_app

    runtime = MLRuntime(settings)
    status = runtime.load()
    if not status.available:
        runtime.shutdown()
        pytest.skip(f"the classifier would not load here: {status.reason} ({status.detail})")

    with installed_runtime(fastapi_app, runtime):
        yield runtime
    runtime.shutdown()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _leaked(response) -> list[str]:
    """Every internal fragment a response body carries. Empty means it leaked none."""
    body = response.text.lower()
    found = [fragment for fragment in FORBIDDEN_IN_ML_BODY if fragment in body]
    if _ABSOLUTE_PATH.search(response.text):
        found.append("an absolute filesystem path")
    escaped_checkpoint = json.dumps(str(get_settings().ml_resolved_model_path))[1:-1]
    if escaped_checkpoint and escaped_checkpoint in response.text:
        found.append("the configured checkpoint path")
    return found


def _assert_no_internals(response) -> None:
    leaked = _leaked(response)
    assert not leaked, f"response body leaked {leaked}: {response.text}"


# ---------------------------------------------------------------------------
# Authentication and authorisation
# ---------------------------------------------------------------------------


async def test_the_status_endpoint_refuses_an_unauthenticated_caller(
    offline_client, assert_error_envelope
):
    """An anonymous caller cannot ask what NEXUS can classify into.

    ``/ml/route`` accepts free text and answers "which of our surfaces does this
    mean"; left anonymous that is a free oracle over the taxonomy, which is the
    shape of a model-extraction probe. Nothing is overridden here, so the 401 is
    produced by the real resolver rather than by a stub.
    """
    response = await offline_client.get("/api/v1/ml/status")

    error = assert_error_envelope(response, status_code=401, code="unauthorized")
    assert error["details"] is None
    assert response.headers["WWW-Authenticate"] == "Bearer"
    _assert_no_internals(response)


async def test_the_route_endpoint_refuses_an_unauthenticated_caller(
    offline_client, assert_error_envelope
):
    """A well-formed body does not make an anonymous classification request legal."""
    response = await offline_client.post("/api/v1/ml/route", json={"text": _TASK_UTTERANCE})

    assert_error_envelope(response, status_code=401, code="unauthorized")
    _assert_no_internals(response)


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        pytest.param("GET", "/api/v1/ml/status", None, id="status"),
        pytest.param("POST", "/api/v1/ml/route", {"text": _TASK_UTTERANCE}, id="route"),
    ],
)
async def test_a_caller_without_analytics_read_is_forbidden_on_both_routes(
    app, offline_client, assert_error_envelope, method, path, payload
):
    """Both routes refuse a role that holds no grant, and say so as a permission.

    ``analytics.read`` is a deliberate reuse of an existing capability rather
    than a missing ``ml.*``: Phases 7-9 gate new surfaces the same way, and
    ``tests/test_permissions.py`` pins the ``Permission`` member set literally, so
    a new member would be a test edit as well as a grant decision. The role below
    is one the map has never heard of, which is the fail-closed branch of
    :func:`app.core.permissions.permissions_for`.
    """
    caller = _caller(role="read-only-observer")
    app.dependency_overrides[get_current_user] = lambda: caller
    app.dependency_overrides[get_authenticated_user] = lambda: caller
    try:
        response = await offline_client.request(method, path, json=payload)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_authenticated_user, None)

    error = assert_error_envelope(response, status_code=403, code="forbidden")
    assert "permission" in error["message"].lower()
    _assert_no_internals(response)


async def test_the_permission_gate_admits_the_default_role(authorised_client, serving_runtime):
    """Non-vacuity for the 403 above: ``user`` is admitted by the same gate.

    If ``has_permission`` stopped consulting the role, or the grant map had
    accidentally learned ``read-only-observer``, the 403 would still pass while the
    gate had quietly stopped gating anything.
    """
    response = await authorised_client.get("/api/v1/ml/status")

    assert response.status_code == 200, response.text
    assert response.json()["taxonomy_version"] == TAXONOMY_VERSION


# ---------------------------------------------------------------------------
# Degradation
# ---------------------------------------------------------------------------


async def test_a_degraded_classifier_answers_503_rather_than_fabricating_a_decision(
    authorised_client, degraded_runtime, assert_error_envelope
):
    """The central promise of the phase: an unavailable classifier refuses.

    The refusal has to be *total*. A body answering 200 with an intent and a
    confidence would be read as a classification, and the caller's next move is to
    call ``TaskService`` on the strength of it — NEXUS would be inventing a user's
    instruction and acting on it. The assertions therefore check the shape of the
    error object field by field, not merely that the status code is not 200.
    """
    response = await authorised_client.post("/api/v1/ml/route", json={"text": _TASK_UTTERANCE})

    error = assert_error_envelope(response, status_code=503, code="ml_unavailable")
    assert error["details"] == {"reason": degraded_runtime.status.reason}
    body = response.json()
    assert set(body) == {"error"}
    assert not DECISION_FIELDS.intersection(body["error"])
    _assert_no_internals(response)


async def test_the_same_request_is_served_once_a_classifier_is_available(
    authorised_client, serving_runtime, assert_error_envelope
):
    """Positive control for the 503 above: the identical body answers 200.

    Without this, "the endpoint refused" and "the endpoint refuses everything" are
    the same observation, and reverting the ``raise MLUnavailableError`` branch
    would leave the 503 test green.
    """
    del serving_runtime, assert_error_envelope  # installed/available; the request is the point

    response = await authorised_client.post("/api/v1/ml/route", json={"text": _TASK_UTTERANCE})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["intent"] == "task_manage"
    assert body["status"] == "accepted"
    assert body["target"]["service"] == "TaskService"


@pytest.mark.parametrize(
    "reason",
    [
        pytest.param(MLRuntime.REASON_UNLOADED, id="not_loaded"),
        pytest.param(MLRuntime.REASON_CHECKPOINT_MISSING, id="checkpoint_missing"),
        pytest.param(MLRuntime.REASON_DISABLED, id="disabled"),
        pytest.param(MLRuntime.REASON_STOPPED, id="stopped"),
    ],
)
async def test_every_degraded_reason_reaches_the_caller_verbatim(
    app, authorised_client, make_settings, tmp_path, assert_error_envelope, reason
):
    """``details.reason`` is the runtime's closed vocabulary, not prose.

    A client's retry logic and an operator's runbook both branch on this string, so
    "something went wrong" would be a regression even though the status code would
    not move. ``runtime_missing`` and ``load_failed`` are absent because producing
    them needs torch uninstalled or the weights corrupt.
    """
    runtime = _runtime_reporting(reason, make_settings, tmp_path)
    assert runtime.status.reason == reason
    assert runtime.classifier is None

    with installed_runtime(app, runtime):
        response = await authorised_client.post("/api/v1/ml/route", json={"text": _TASK_UTTERANCE})

    error = assert_error_envelope(response, status_code=503, code="ml_unavailable")
    assert error["details"] == {"reason": reason}
    _assert_no_internals(response)


async def test_a_deployed_runtime_reports_its_reason_on_the_status_endpoint(
    app, authorised_client, make_settings, tmp_path
):
    """A caller can ask *why* ML is out without provoking the 503.

    That is what makes the degraded answer actionable rather than merely correct:
    the 503 names the reason, and the diagnostics route reports the same one, at a
    200, beside the threshold and the taxonomy.
    """
    runtime = _runtime_reporting(MLRuntime.REASON_DISABLED, make_settings, tmp_path)

    with installed_runtime(app, runtime):
        response = await authorised_client.get("/api/v1/ml/status")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["enabled"] is False
    assert body["available"] is False
    assert body["unavailable_reason"] == MLRuntime.REASON_DISABLED
    assert body["model"] is None
    assert len(body["intents"]) == 14


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("payload", "fields"),
    [
        pytest.param({}, {"text"}, id="missing_text"),
        pytest.param({"text": ""}, {"text"}, id="empty_text"),
        pytest.param({"text": None}, {"text"}, id="null_text"),
        pytest.param({"text": 42}, {"text"}, id="numeric_text"),
        pytest.param({"text": ["add a task"]}, {"text"}, id="list_text"),
        pytest.param({"text": {"body": "add a task"}}, {"text"}, id="object_text"),
        # A field dropped silently would answer 200 having classified nothing the
        # caller asked about, so a misspelling must be reported rather than
        # ignored. Both halves are named: the unknown key, and the required one
        # that was therefore never supplied.
        pytest.param({"utterance": "add a task"}, {"utterance", "text"}, id="misspelled_field"),
    ],
)
async def test_every_malformed_body_is_a_validation_envelope(
    authorised_client, serving_runtime, assert_error_envelope, payload, fields
):
    """A body the schema cannot accept is a 422 naming every offending field.

    ``extra="forbid"`` is what turns the last case from a silent success into a
    rejection, so the error must name the key the caller actually sent.
    """
    response = await authorised_client.post("/api/v1/ml/route", json=payload)

    error = assert_error_envelope(response, status_code=422, code="validation_error")
    assert {entry["field"] for entry in error["details"]["errors"]} == fields
    _assert_no_internals(response)


async def test_text_longer_than_the_configured_limit_is_refused(
    authorised_client, serving_runtime, assert_error_envelope
):
    """One character past ``ml_max_input_chars`` is a 422, not a silent truncation.

    The trained context is 128 subwords, so past a couple of thousand characters
    extra text cannot change the prediction — it is only a place to park a payload
    the classifier will never read.
    """
    response = await authorised_client.post(
        "/api/v1/ml/route", json={"text": "a" * (MAX_INPUT_CHARS + 1)}
    )

    assert_error_envelope(response, status_code=422, code="validation_error")
    _assert_no_internals(response)


async def test_text_of_exactly_the_configured_limit_is_still_served(
    authorised_client, serving_runtime
):
    """Non-vacuity for the bound above: the limit is a limit, not a blanket refusal.

    If the schema compared against anything other than ``ml_max_input_chars`` — an
    off-by-one, or the classifier's own larger character bound — this would fail
    while the 422 test stayed green.
    """
    assert get_settings().ml_max_input_chars == MAX_INPUT_CHARS

    response = await authorised_client.post(
        "/api/v1/ml/route", json={"text": "a" * MAX_INPUT_CHARS}
    )

    assert response.status_code == 200, response.text
    assert response.json()["intent"] == "task_manage"


@pytest.mark.parametrize(
    "blank",
    [pytest.param(" ", id="single_space"), pytest.param("   \t\n  ", id="whitespace_only")],
)
async def test_a_blank_utterance_is_refused_by_the_classifier(
    authorised_client, blank_text_runtime, assert_error_envelope, blank
):
    """Whitespace clears the schema and is stopped by the classifier's own rule.

    ``RouteRequest`` only bounds length; ``IntentClassifier`` owns "not a string,
    empty, or blank". The rule is applied in the domain rather than at the edge so
    that every caller — this route, the batch path, a script — is held to it, which
    is only observable here if it is exercised through the route.
    """
    response = await authorised_client.post("/api/v1/ml/route", json={"text": blank})

    error = assert_error_envelope(response, status_code=422, code="validation_error")
    assert "blank" in json.dumps(error["details"])
    _assert_no_internals(response)


async def test_a_non_blank_utterance_is_past_validation_on_the_same_runtime(
    authorised_client, blank_text_runtime, assert_error_envelope
):
    """Non-vacuity for the blank case: this runtime only refuses *blanks*.

    The model behind the fixture raises the moment inference is attempted, so the
    500 here is the proof that a well-formed utterance got all the way through
    validation to the forward pass — and therefore that the 422 above was the
    validation rule rather than a runtime that refuses everything.
    """
    response = await authorised_client.post("/api/v1/ml/route", json={"text": _TASK_UTTERANCE})

    assert_error_envelope(response, status_code=500, code="internal_error")
    _assert_no_internals(response)


# ---------------------------------------------------------------------------
# The checkpoint is the deployment's to choose
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("model_path", "C:/attacker/weights", id="model_path"),
        pytest.param("checkpoint", "/srv/attacker/final", id="checkpoint"),
        pytest.param("model", "microsoft/deberta-v3-base", id="model"),
        pytest.param("device", "cuda", id="device"),
        pytest.param("threshold", 0.01, id="threshold"),
        pytest.param("intent", "task_manage", id="intent"),
        pytest.param("weights", "model.safetensors", id="weights"),
        pytest.param("id2label", {"0": "out_of_scope"}, id="id2label"),
    ],
)
async def test_no_request_body_can_choose_which_weights_answer(
    authorised_client, serving_runtime, field, value
):
    """Steering the classifier is either refused outright or has no effect.

    ``RouteRequest`` sets ``extra="forbid"``, so the answer today is a 422; the
    test accepts a 200 too, because what is pinned is the *property* — a body key
    cannot move the answer — rather than the particular spelling of the refusal.
    Either way the caller's value must not come back out of the response.
    """
    plain = await authorised_client.post("/api/v1/ml/route", json={"text": _TASK_UTTERANCE})
    assert plain.status_code == 200, plain.text

    steered = await authorised_client.post(
        "/api/v1/ml/route", json={"text": _TASK_UTTERANCE, field: value}
    )

    if steered.status_code == 200:
        assert steered.json() == plain.json()
    else:
        assert steered.status_code == 422, steered.text
        assert steered.json()["error"]["code"] == "validation_error"
    assert json.dumps(value) not in steered.text, steered.text
    _assert_no_internals(steered)


async def test_the_status_endpoint_reports_the_server_checkpoint_not_the_callers(
    authorised_client, serving_runtime, settings
):
    """The published checkpoint is the one this deployment resolved.

    A caller who could steer the answer would also be able to fingerprint which
    weights are loaded. ``ModelIdentity.checkpoint`` is documented as reported,
    never accepted, and this is the half of that claim a client can observe.
    """
    await authorised_client.post(
        "/api/v1/ml/route",
        json={"text": _TASK_UTTERANCE, "checkpoint": "C:/attacker/final"},
    )

    response = await authorised_client.get("/api/v1/ml/status")

    assert response.status_code == 200, response.text
    assert response.json()["model"]["checkpoint"] == str(settings.ml_resolved_model_path)
    assert "attacker" not in response.text


# ---------------------------------------------------------------------------
# Nothing internal escapes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("scenario", "payload", "expected_status"),
    [
        pytest.param("accepted", {"text": _TASK_UTTERANCE}, 200, id="success"),
        pytest.param("rejected", {"text": "a" * (MAX_INPUT_CHARS + 1)}, 422, id="validation_422"),
        pytest.param("unavailable", {"text": _TASK_UTTERANCE}, 503, id="unavailable_503"),
    ],
)
async def test_no_ml_response_carries_an_internal(
    app,
    authorised_client,
    settings,
    make_settings,
    tmp_path,
    assert_error_envelope,
    scenario,
    payload,
    expected_status,
):
    """No traceback, frame, path or tensor wording in any of the three answers.

    An inference failure is the case that matters: the underlying exception is a
    torch or safetensors object whose ``str()`` names a file, a module and a
    device. ``InferenceError`` gives it a fixed message for exactly that reason,
    and this is the test that would notice if it stopped doing so.
    """
    if scenario == "unavailable":
        runtime = _runtime_reporting(MLRuntime.REASON_CHECKPOINT_MISSING, make_settings, tmp_path)
    else:
        runtime = MLRuntime(
            settings,
            classifier=_StubClassifier(_confident_task_prediction(), _identity_for(settings)),
        )

    with installed_runtime(app, runtime):
        response = await authorised_client.post("/api/v1/ml/route", json=payload)

    assert response.status_code == expected_status, response.text
    if expected_status != 200:
        assert_error_envelope(
            response,
            status_code=expected_status,
            code={422: "validation_error", 503: "ml_unavailable"}[expected_status],
        )
    _assert_no_internals(response)


def test_the_internal_scanner_would_notice_one():
    """Non-vacuity for :func:`_assert_no_internals`: the scanner detects a leak.

    A scan that finds nothing is only evidence when it could have found something,
    so this feeds it bodies carrying the classes of fragment the scanner watches
    for. Two samples because they arrive differently: ``response.text`` is a raw
    body, so a frame inside a JSON *string* arrives with its quotes escaped and a
    frame outside one does not.
    """

    class _Leaky:
        """A body shaped the way a leaked torch error would arrive in JSON."""

        text = (
            '{"error": {"code": "internal_error", "details": '
            '"Traceback (most recent call last): File \\"app/ml/classifier.py\\", '
            "line 210, in predict; torch softmax over logits at "
            'C:\\\\nexus\\\\.venv\\\\site-packages\\\\torch\\\\nn\\\\linear.py"}}'
        )

    class _LeakyFrame:
        """A body carrying an unescaped source line, as a non-JSON body would."""

        text = 'file "/app/ml/model_loader.py" could not read model.safetensors'

    escaped_leaks = _leaked(_Leaky())
    plain_leaks = _leaked(_LeakyFrame())

    assert "traceback" in escaped_leaks
    assert "torch" in escaped_leaks
    assert "logits" in escaped_leaks
    assert "site-packages" in escaped_leaks
    assert "an absolute filesystem path" in escaped_leaks
    assert 'file "' in plain_leaks
    assert "safetensors" in plain_leaks
    assert _leaked(_LeakyFrame()) == plain_leaks


# ---------------------------------------------------------------------------
# The response contract
# ---------------------------------------------------------------------------


async def test_a_routing_decision_has_exactly_the_documented_fields(
    authorised_client, serving_runtime
):
    """The wire shape is the whole contract; an extra key is a client-breaking act.

    There is deliberately no ``logits``, no ``token_ids``, no ``latency_ms`` and no
    echoed ``text``. Each of those is something a client could then couple to a
    checkpoint that can be retrained without the client changing.
    """
    response = await authorised_client.post("/api/v1/ml/route", json={"text": _TASK_UTTERANCE})

    body = response.json()
    assert set(body) == DECISION_FIELDS
    assert set(body["target"]) == {"service", "module", "entrypoint"}
    assert body["alternatives"] == [
        {"intent": "project_manage", "confidence": 0.006},
        {"intent": "schedule_plan", "confidence": 0.002},
    ]


async def test_the_confidence_is_the_classifiers_own_number_not_a_rounded_one(
    authorised_client, serving_runtime
):
    """``confidence`` travels at full precision, and as a float.

    It is this utterance's softmax probability rather than the test-set accuracy, so
    rounding it would misrepresent a number the caller may threshold on themselves.
    The stub returns nine significant digits precisely so that a ``round(x, 4)``
    anywhere on the path would be visible here and nowhere else.
    """
    _, classifier = serving_runtime
    prediction = classifier.predict("recorded out of band")

    response = await authorised_client.post("/api/v1/ml/route", json={"text": _TASK_UTTERANCE})

    confidence = response.json()["confidence"]
    assert type(confidence) is float
    assert confidence == prediction.confidence
    assert 0.0 <= confidence <= 1.0
    assert str(confidence) == "0.987654321"


async def test_a_low_confidence_prediction_is_refused_and_names_no_service(
    authorised_client, uncertain_runtime
):
    """Below the threshold the answer is a question, not a pointer at a service.

    ``target is None`` is the load-bearing part: an ``uncertain`` decision that
    still named ``TaskService`` would be indistinguishable from an accepted one to
    a client that only checked the target.
    """
    response = await authorised_client.post("/api/v1/ml/route", json={"text": _TASK_UTTERANCE})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "uncertain"
    assert body["intent"] == "task_manage"
    assert body["target"] is None
    assert body["destination"] == "api/v1/tasks"
    assert body["destination_kind"] == "router"
    assert body["confidence"] == 0.4123
    assert len(body["alternatives"]) == 3
    assert "task_manage" not in {entry["intent"] for entry in body["alternatives"]}
    assert "Did you mean" in body["reason"]


async def test_a_routing_decision_names_the_services_import_path(
    authorised_client, serving_runtime
):
    """``target.module`` is the documented import path, so publishing it is not a leak.

    This is the positive half of the trade ``FORBIDDEN_IN_ML_BODY`` makes by
    exempting ``app.services``: the field tells the caller which call to make next,
    and it is ``app.services.task_service`` rather than anything else.
    """
    response = await authorised_client.post("/api/v1/ml/route", json={"text": _TASK_UTTERANCE})

    body = response.json()
    assert body["target"] == {
        "service": "TaskService",
        "module": "app.services.task_service",
        "entrypoint": "list",
    }
    assert body["threshold"] == get_settings().ml_confidence_threshold


async def test_the_submitted_text_reaches_the_classifier_untransformed(
    authorised_client, serving_runtime
):
    """Not lower-cased, not trimmed, not stripped of punctuation.

    Phase 10 consumed the raw dataset strings; a tidy-up at the edge would be a
    distribution shift the model was never fitted on. The stub records exactly what
    it was asked, so the assertion is about the wire rather than about the route.
    """
    _, classifier = serving_runtime
    utterance = "  Add a Task, to draft the migration plan!  "

    response = await authorised_client.post("/api/v1/ml/route", json={"text": utterance})

    assert response.status_code == 200, response.text
    assert classifier.seen == [utterance]


# ---------------------------------------------------------------------------
# The status endpoint
# ---------------------------------------------------------------------------


async def test_the_status_endpoint_reports_the_fourteen_intent_taxonomy(
    authorised_client, serving_runtime
):
    """The published label set is the one the router routes with, in class order.

    It is joined from the same table a routing decision reads, so a client that
    renders "surfaces NEXUS offers" from this response cannot advertise a
    capability the router would refuse to route to.
    """
    response = await authorised_client.get("/api/v1/ml/status")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["taxonomy_version"] == TAXONOMY_VERSION
    assert len(body["intents"]) == 14
    assert tuple(entry["intent"] for entry in body["intents"]) == EXPECTED_INTENTS
    assert tuple(entry["intent"] for entry in body["intents"] if entry["service"] is None) == (
        UNSERVED_INTENTS
    )
    router_intents = {str(intent) for intent in ROUTER_INTENTS}
    served = [entry for entry in body["intents"] if entry["intent"] in router_intents]
    assert len(served) == 11
    assert all(entry["service"] and entry["entrypoint"] for entry in served)


async def test_the_status_endpoint_reports_the_threshold_it_routes_with(
    authorised_client, serving_runtime, settings
):
    """The number published is the number a decision is judged against.

    One settings object, two requests: a deployment that raised the threshold would
    get more refusals, and a status endpoint still advertising the old number would
    send a client tuning itself the wrong way.
    """
    response = await authorised_client.get("/api/v1/ml/status")
    threshold = response.json()["threshold"]

    assert threshold == settings.ml_confidence_threshold
    assert threshold == DOCUMENTED_DEFAULT_THRESHOLD

    decision = await authorised_client.post("/api/v1/ml/route", json={"text": _TASK_UTTERANCE})
    assert decision.json()["threshold"] == threshold


async def test_a_degraded_runtime_is_reported_as_a_200_not_an_error(
    authorised_client, degraded_runtime
):
    """The endpoint reporting the degradation is itself healthy.

    This is the ``/api/v1/health`` precedent applied verbatim: a 503 here would
    make the one route that could explain an outage part of it, and every caller
    asking *what is wrong* would get *something is wrong* instead of the reason.
    """
    response = await authorised_client.get("/api/v1/ml/status")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["enabled"] is True
    assert body["available"] is False
    assert body["unavailable_reason"] == MLRuntime.REASON_UNLOADED
    assert body["model"] is None
    # The taxonomy is reported with nothing loaded: it is deployment
    # configuration, not a property of the weights.
    assert len(body["intents"]) == 14
    assert body["threshold"] == DOCUMENTED_DEFAULT_THRESHOLD


async def test_a_loaded_classifier_is_reported_with_no_unavailable_reason(
    authorised_client, serving_runtime, settings
):
    """The inverse: an available runtime carries a model and a null reason.

    ``unavailable_reason`` must be null whenever ``available`` is true — a caller
    branching on the reason string would otherwise be told ``not_loaded`` on a
    perfectly healthy server.
    """
    response = await authorised_client.get("/api/v1/ml/status")

    body = response.json()
    assert body["available"] is True
    assert body["unavailable_reason"] is None
    assert set(body["model"]) == {
        "base_model",
        "architecture",
        "device",
        "label_count",
        "max_sequence_length",
        "parameter_count",
        "checkpoint",
        "load_seconds",
    }
    assert body["model"]["base_model"] == "microsoft/deberta-v3-base"
    assert body["model"]["architecture"] == "DebertaV2ForSequenceClassification"
    assert body["model"]["label_count"] == 14
    assert body["model"]["max_sequence_length"] == 128
    assert body["model"]["parameter_count"] == 184_432_910
    assert body["model"]["checkpoint"] == str(settings.ml_resolved_model_path)


# ---------------------------------------------------------------------------
# Live: real user input -> NEXO -> the Phase 10 checkpoint -> an existing service
# ---------------------------------------------------------------------------


@ml_model
async def test_a_task_utterance_routes_to_the_task_service(
    authorised_client, live_runtime, settings
):
    """The whole phase, end to end, on the strongest case in the taxonomy.

    Real user text through the real application into the real 183M-parameter
    checkpoint, out as an intent and an existing service. Nothing about the model's
    behaviour is stubbed; if the checkpoint, the tokenisation contract or the
    router's table were wrong, this is where it would stop being right.
    """
    response = await authorised_client.post("/api/v1/ml/route", json={"text": _TASK_UTTERANCE})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["intent"] == "task_manage"
    assert body["status"] == "accepted"
    assert body["destination"] == "api/v1/tasks"
    assert body["destination_kind"] == "router"
    assert body["target"]["service"] == "TaskService"
    assert body["threshold"] == settings.ml_confidence_threshold
    assert body["confidence"] >= body["threshold"]


@ml_model
async def test_a_code_assist_utterance_is_recognised_and_declined(authorised_client, live_runtime):
    """NEXUS recognises the request it will not serve, and answers without one.

    ``code_assist`` is a trained class, so the model *will* return it, and it
    reaches no service. The response must contain no generated code or prose answer
    at all — not a stub, not a refusal that carries a suggestion.
    """
    response = await authorised_client.post("/api/v1/ml/route", json={"text": _CODE_UTTERANCE})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["intent"] == "code_assist"
    assert body["status"] == "generation_unavailable"
    assert body["destination"] == "large-model:unavailable"
    assert body["destination_kind"] == "large_model"
    assert body["target"] is None
    assert set(body) == DECISION_FIELDS
    assert "```" not in response.text
    assert "def " not in response.text


@ml_model
async def test_a_deep_reasoning_utterance_is_recognised_and_declined(
    authorised_client, live_runtime
):
    """The other generative class gets the same explicit refusal as ``code_assist``.

    Both are trained classes NEXUS runs no model for. Answering either with an
    invented result would be the worst outcome available, so the test pins the
    same three facts: recognised, refused, and carrying no answer.
    """
    response = await authorised_client.post("/api/v1/ml/route", json={"text": _REASONING_UTTERANCE})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["intent"] == "deep_reasoning"
    assert body["status"] == "generation_unavailable"
    assert body["destination"] == "large-model:unavailable"
    assert body["target"] is None
    assert set(body) == DECISION_FIELDS
    assert "```" not in response.text


@ml_model
async def test_an_out_of_scope_utterance_abstains_and_lists_what_exists(
    authorised_client, live_runtime
):
    """Abstention names the surfaces the user may have meant.

    The list is derived from the router intents' own taxonomy entries rather than
    written out, so it cannot advertise a route that has since been deleted — and
    it is the only actionable part of a refusal.
    """
    response = await authorised_client.post(
        "/api/v1/ml/route", json={"text": _OUT_OF_SCOPE_UTTERANCE}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["intent"] == "out_of_scope"
    assert body["status"] == "out_of_scope"
    assert body["destination"] == "abstain"
    assert body["destination_kind"] == "fallback"
    assert body["target"] is None
    assert "api/v1/tasks" in body["reason"]
    assert "api/v1/knowledge" in body["reason"]


@ml_model
async def test_an_ambiguous_utterance_is_uncertain_and_names_no_service(
    authorised_client, live_runtime
):
    """A weak prediction refuses and offers its runner-ups instead of a service.

    Below the threshold a wrong confident answer writes to the user's calendar,
    while a refusal costs one clarifying turn. ``target is None`` is the assertion
    that matters; the alternatives are what makes the refusal useful.
    """
    response = await authorised_client.post("/api/v1/ml/route", json={"text": _AMBIGUOUS_UTTERANCE})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "uncertain"
    assert body["target"] is None
    assert body["confidence"] < body["threshold"]
    assert 1 <= len(body["alternatives"]) <= 3
    assert body["intent"] not in {entry["intent"] for entry in body["alternatives"]}


@ml_model
async def test_a_live_decision_never_echoes_the_submitted_text(authorised_client, live_runtime):
    """An utterance is untrusted free text and is never copied into the response.

    It may carry a credential. The route logs the intent, the confidence and the
    character count; nothing else in the response is derived from what was said, and
    this is the test that says so against the real model rather than a double.
    """
    response = await authorised_client.post(
        "/api/v1/ml/route",
        json={"text": f"add a task to call the {_CANARY} team about the migration"},
    )

    assert response.status_code == 200, response.text
    assert _CANARY not in response.text
    assert _CANARY not in json.dumps(response.json())


@ml_model
async def test_the_status_endpoint_reports_the_live_checkpoint_on_this_machine(
    authorised_client, live_runtime, settings
):
    """With weights loaded, the status route names the ones this process read.

    The path is deployment configuration: reported so an operator can confirm which
    checkpoint answered, never accepted so a caller could choose one. This is the
    client-observable half of that claim, against the real loader.
    """
    response = await authorised_client.get("/api/v1/ml/status")

    body = response.json()
    assert body["available"] is True
    assert body["unavailable_reason"] is None
    assert body["model"]["checkpoint"] == str(settings.ml_resolved_model_path)
    assert body["model"]["checkpoint"] == live_runtime.status.checkpoint
    assert body["model"]["device"] == live_runtime.status.device
    assert body["model"]["label_count"] == 14
    assert body["threshold"] == settings.ml_confidence_threshold


@ml_model
@pytest.mark.parametrize(
    ("utterance", "intent", "service"),
    [
        pytest.param(
            "add a task to draft the migration plan for friday",
            "task_manage",
            "TaskService",
            id="tasks",
        ),
        pytest.param(
            "show me my analytics overview for this week",
            "analytics_insight",
            "AnalyticsService",
            id="analytics",
        ),
        pytest.param(
            "search my notes about the migration",
            "knowledge_lookup",
            "KnowledgeService",
            id="knowledge",
        ),
    ],
)
async def test_a_plainly_worded_request_reaches_a_service_on_three_surfaces(
    authorised_client, live_runtime, utterance, intent, service
):
    """Positive control over the live half: the endpoint does not refuse everything.

    Three surfaces, three services, three forward passes through the real
    checkpoint. If the loader, the threadpool hop or the permission gate were
    broken in a way that made every live request fail, the individual assertions
    above might still pass while a route-level refusal crept in.
    """
    response = await authorised_client.post("/api/v1/ml/route", json={"text": utterance})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "accepted", utterance
    assert body["intent"] == intent, utterance
    assert body["target"]["service"] == service, utterance


# ---------------------------------------------------------------------------
# Found and not fixed
# ---------------------------------------------------------------------------


@pytest.mark.ml_model
async def test_a_credential_shaped_utterance_is_refused_before_classification(
    authorised_client, live_runtime, assert_error_envelope
):
    """An utterance carrying a secret should never reach the model.

    ``ml_reject_credentials`` says so in as many words, and a user utterance is
    exactly the untrusted free text :func:`ml.preprocessing.normalize.find_credential`
    was written to scan.

    This runs against the **real** checkpoint rather than one of the stubs above,
    which is the whole point of the test: a stub is handed a fixed prediction, so
    it can never show that the screening happens on the way *into* the classifier.
    The refusal has to be observed on a call that would otherwise have classified.
    """
    response = await authorised_client.post(
        "/api/v1/ml/route",
        json={"text": "add a task to rotate the api key ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"},
    )

    error = assert_error_envelope(response, status_code=422, code="validation_error")
    assert error["details"] is not None
    assert error["details"].get("reason") == "credential_shaped"
    # The refusal names the kind of credential, never the value it matched.
    assert "ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8" not in response.text
