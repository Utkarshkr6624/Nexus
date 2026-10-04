"""The propose → confirm endpoints over HTTP: the surface that turns a sentence into a write.

``tests/test_ml_actions.py`` proves the proposal layer is safe *as a type* — no
destructive member, no callable on a proposal, an ambiguous date yields no date.
None of that proves anything about the two routes that sit on top of it, and the
two routes are where the risk actually lives: ``confirm`` takes a body from a
browser, re-derives a service call from it, and writes a row. This file is the
only place that path is exercised **through HTTP**, and it is written so that
every test could still fail if the code were reverted.

What is pinned here, and why each one is load-bearing
-----------------------------------------------------

**A foreign id is never touched.** This is the file's security test.
``POST /api/v1/ml/action/confirm`` accepts an *edited* payload — that is the
whole point of a confirm dialog — so ``project_id`` and ``target_id`` are both
attacker-controlled. They are re-resolved through the owner-scoped service
methods, which 404 rather than load the row, and the tests assert the row's state
*after* the refused request as well as the status code.

**A kind outside the closed set is a 422, and there is no delete.** ``ActionKind``
has five members and none of them is destructive. The tests pin both halves:
that a bogus kind is refused at the edge, and that a spec claiming to be
destructive is refused by the handler rather than executed — the second is
reached by installing a stub into the router's own table, because a spec that
claims ``destructive=True`` is not representable and therefore cannot be built
for real.

**Nothing reaches a service until the payload has been re-derived.** The empty,
malformed and unknown-key payloads are tested with ``TaskService.create``
replaced by a callable that raises if called, so a regression in the guard is a
loud error rather than a quietly-created row.

**The activity trail is written by the service and is observable.** A task
created here must leave the same ``TASK_CREATED`` row a hand-typed
``POST /tasks`` leaves, and so on for each kind. This is what makes "call the real
service with ``activity=`` wired" checkable rather than aspirational.

**A replay does not duplicate.** The realistic duplicate is a double-click or a
retry after a timeout, in both of which the first call already achieved the
desired state; the second therefore reports ``no_op`` with the existing row's id
rather than a second row or a 409 the user would read as a failure.

**A refusal is a 200.** ``proposed: false`` with a reason code is the ordinary
answer to "could not understand", including for a destructive request. The
degraded-classifier case is the one that is *not* a 200, because a caller about
to act on a fabricated intent is exactly the failure this phase exists to prevent.

The seam this file uses
-----------------------
``app.api.deps.get_ml_runtime`` reads ``request.app.state.ml_runtime`` and falls
back to the process singleton when the lifespan has not run. ``ASGITransport``
never runs one, so that attribute is where a stub classifier is installed — the
very assignment ``app/main.py`` makes once the real weights are loaded.
Authentication is real: accounts are registered and signed in over HTTP, so the
403 and 404 answers below come from the actual permission and ownership checks
rather than from a fixture that replaced them.
"""

from __future__ import annotations

import inspect
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1 import actions as actions_module
from app.api.v1.actions import router
from app.core.config import Settings
from app.ml.classifier import MAX_UTTERANCE_CHARACTERS, IntentClassifier
from app.ml.runtime import MLRuntime
from app.ml.schemas import IntentPrediction
from app.models.activity import ActivityLog
from app.models.enums import ActivityEvent, TaskStatus
from ml.datasets.taxonomy import Intent
from tests.analytics_fixtures import bearer, register_via_api, sign_in, user_by_email

PROPOSE = "/api/v1/ml/action/propose"
CONFIRM = "/api/v1/ml/action/confirm"

TASK_INTENT = str(Intent.TASK_MANAGE)
PROJECT_INTENT = str(Intent.PROJECT_MANAGE)
NOTE_INTENT = str(Intent.KNOWLEDGE_CAPTURE)
GOAL_INTENT = str(Intent.LEARNING_TRACK)
OUT_OF_SCOPE_INTENT = str(Intent.OUT_OF_SCOPE)

CREATE_TASK_KIND = "create_task"
CREATE_TASK_TEXT = "add a task to draft the migration plan"
COMPLETE_TASK_TEXT = "mark the api contract task as done"
CONTRACT_TASK_TITLE = "Draft the API contract"


# --------------------------------------------------------------------------- #
# Doubles
# --------------------------------------------------------------------------- #


class _StubClassifier:
    """A loaded checkpoint that returns one chosen intent.

    The classifier's inference is the thing already proven elsewhere; what this
    file is about is everything downstream of it, so a double is the right
    instrument. It records the text it was handed, which is how the tests assert
    the endpoint passes the utterance through **byte for byte** rather than
    normalising it into a distribution shift the checkpoint never saw.
    """

    def __init__(self, intent: str, confidence: float = 0.97) -> None:
        self._prediction = IntentPrediction(intent=intent, confidence=confidence)
        self.seen: list[str] = []

    @property
    def identity(self) -> None:
        """No identity: this double is never published by any endpoint under test."""
        return None

    def predict(self, text: str) -> IntentPrediction:
        """Return the fixed prediction, recording what was asked."""
        self.seen.append(text)
        return self._prediction


class _InertWeights:
    """A ``LoadedModel`` stand-in whose forward pass must never be reached.

    ``IntentClassifier.predict`` validates the utterance *before* it touches the
    model, so installing this lets the real validation run — blank, over-long and
    credential-shaped text all rejected — while making any actual inference an
    immediate, loud failure. That is what distinguishes "refused at validation"
    from "refused by everything else" in the 422 tests.
    """

    max_sequence_length = 128
    id2label = ("unreachable",)

    def __getattr__(self, name: str) -> Any:
        raise RuntimeError(f"inference must not reach the model (wanted {name!r})")


@contextmanager
def _installed(app: Any, runtime: MLRuntime):
    """Put ``runtime`` where ``app.api.deps.get_ml_runtime`` looks for it.

    Restores whatever was there, *including nothing*: the attribute is absent in
    a process whose lifespan never ran, and leaving a double behind would
    silently re-point every later test in the session at it.
    """
    missing = object()
    previous = getattr(app.state, "ml_runtime", missing)
    app.state.ml_runtime = runtime
    try:
        yield
    finally:
        app.state.ml_runtime = None if previous is missing else previous


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def serving(app, settings: Settings):
    """Install a classifier that answers with a chosen intent."""

    @contextmanager
    def _install(intent: str):
        classifier = _StubClassifier(intent)
        runtime = MLRuntime(settings, classifier=classifier)
        with _installed(app, runtime):
            yield classifier

    return _install


@pytest.fixture
def validating(app, settings: Settings):
    """Install a classifier whose real validation runs and whose inference cannot.

    Used for the blank, over-long and credential-shaped cases: the 422 has to come
    from ``IntentClassifier._validate`` rather than from a schema, because the
    credential screen exists *only* there.
    """
    runtime = MLRuntime(settings, classifier=IntentClassifier(_InertWeights()))  # type: ignore[arg-type]
    with _installed(app, runtime):
        yield runtime


@pytest.fixture
def degraded(app, settings: Settings):
    """A runtime that has never loaded a model: ``classifier is None``."""
    with _installed(app, MLRuntime(settings)):
        yield


@pytest.fixture
async def ada(client, db_session: AsyncSession) -> dict[str, Any]:
    """A signed-in account, created through the real register and login routes."""
    return await _account(client, db_session, "ada")


@pytest.fixture
async def grace(client, db_session: AsyncSession) -> dict[str, Any]:
    """A second signed-in account, for the cross-tenant tests."""
    return await _account(client, db_session, "grace")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


async def _account(client: Any, db_session: AsyncSession, username: str) -> dict[str, Any]:
    """Register an account over HTTP and return its headers plus its ORM row.

    Deliberately the real round trip rather than a minted token: a token forged
    around the auth code would prove nothing about the ownership predicates this
    file exists to test.
    """
    address = f"{username}@nexus.test"
    await register_via_api(client, username=username, email=address)
    tokens = await sign_in(client, email=address)
    owner = await user_by_email(db_session, address)
    return {"headers": bearer(tokens["access_token"]), "user": owner}


async def _project(client: Any, auth: dict[str, Any], name: str = "Nexo rewrite") -> dict[str, Any]:
    """Create a project through its own route."""
    response = await client.post("/api/v1/projects", json={"name": name}, headers=auth["headers"])
    assert response.status_code == 201, response.text
    return response.json()


async def _task(
    client: Any,
    auth: dict[str, Any],
    project_id: str,
    title: str,
    **extra: Any,
) -> dict[str, Any]:
    """Create a task through its own route."""
    body = {"title": title, "project_id": project_id, **extra}
    response = await client.post("/api/v1/tasks", json=body, headers=auth["headers"])
    assert response.status_code == 201, response.text
    return response.json()


async def _event_types(db_session: AsyncSession, owner_id: UUID) -> list[str]:
    """Every activity event recorded for one account, oldest first."""
    rows = (
        (
            await db_session.execute(
                select(ActivityLog)
                .where(ActivityLog.user_id == owner_id)
                .order_by(ActivityLog.created_at)
            )
        )
        .scalars()
        .all()
    )
    return [row.event_type for row in rows]


async def _task_count(db_session: AsyncSession, owner_id: UUID) -> int:
    """How many tasks the account owns, counted straight from the table."""
    from app.models.task import Task

    rows = (await db_session.execute(select(Task).where(Task.owner_id == owner_id))).scalars().all()
    return len(rows)


async def _propose(client: Any, auth: dict[str, Any], **body: Any) -> Any:
    """Call the propose route and assert it answered 200.

    A refusal is a 200 by design, so asserting the status here and the shape in
    each test is what keeps "refused" and "broken" distinguishable everywhere
    downstream.
    """
    response = await client.post(PROPOSE, json=body, headers=auth["headers"])
    assert response.status_code == 200, response.text
    return response.json()


async def _confirm(client: Any, auth: dict[str, Any], **overrides: Any) -> Any:
    """Call the confirm route with the defaults a proposal produces, and assert 200.

    Merging through :func:`_confirm_body` keeps a test that only cares about the
    payload from restating ``kind`` and ``intent`` on every call, while a test that
    wants to vary those passes a full body and overrides all three.
    """
    response = await client.post(CONFIRM, json=_confirm_body(**overrides), headers=auth["headers"])
    assert response.status_code == 200, response.text
    return response.json()


def _confirm_body(**overrides: Any) -> dict[str, Any]:
    """A confirm body in the shape a proposal produces, with overrides applied."""
    body: dict[str, Any] = {
        "kind": CREATE_TASK_KIND,
        "intent": TASK_INTENT,
        "payload": {"title": "draft the migration plan", "project_id": str(UUID(int=0))},
    }
    body.update(overrides)
    return body


def _install_spec(monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> None:
    """Repoint the router's spec table at a hostile ``create_task`` entry.

    ``ActionSpec`` is a frozen, slotted dataclass, so the stand-in is built from
    the real spec's declared fields rather than copied. Two guards in the handler
    are unreachable through the shipped table — ``destructive`` is a property that
    cannot be set, and every role holds every write permission — and this is how
    a test reaches the half of each guard that would still be there if the type
    stopped defending it.
    """
    from app.ml.actions import ActionKind

    real = actions_module.ACTION_SPECS[ActionKind.CREATE_TASK]
    hostile = SimpleNamespace(
        **{name: getattr(real, name) for name in real.__dataclass_fields__},
        destructive=overrides.get("destructive", False),
    )
    for name, value in overrides.items():
        if name != "destructive":
            setattr(hostile, name, value)
    monkeypatch.setattr(
        actions_module,
        "ACTION_SPECS",
        {**actions_module.ACTION_SPECS, ActionKind.CREATE_TASK: hostile},
    )


# --------------------------------------------------------------------------- #
# The module's shape
# --------------------------------------------------------------------------- #


def test_this_module_registers_exactly_two_post_routes_and_nothing_else() -> None:
    """The inventory is the first guarantee, so it is asserted before anything else.

    A third route — or a ``DELETE`` — would be a surface that writes, or destroys,
    without a proposal in front of it. Pinning the set rather than counting it
    means a route added *and* a route removed both fail here.
    """
    declared = {
        (route.path, method) for route in router.routes for method in sorted(route.methods or ())
    }
    assert declared == {
        ("/ml/action/propose", "POST"),
        ("/ml/action/confirm", "POST"),
    }


def test_both_routes_land_under_the_existing_ml_prefix_in_the_served_schema(app) -> None:
    """The exact paths the frontend will call, read from the served OpenAPI document.

    Derived from ``app.openapi()`` rather than from the router objects, so it also
    proves ``app/api/v1/router.py`` actually included this router — a module that
    exists and is never mounted is the failure this catches.
    """
    paths = app.openapi()["paths"]
    assert sorted(path for path in paths if path.startswith("/api/v1/ml/")) == [
        "/api/v1/ml/action/confirm",
        "/api/v1/ml/action/propose",
        "/api/v1/ml/route",
        "/api/v1/ml/status",
    ]
    for path in (PROPOSE, CONFIRM):
        assert sorted(paths[path]) == ["post"], path


def test_no_route_in_this_module_deletes_anything() -> None:
    """Not one handler in this file reaches ``delete``, ``cancel`` or ``remove``.

    A source-level scan rather than a behavioural one, because the guarantee is
    structural: there is no code path here that could be made to drop a row even
    if a future edit tried. The positive control is what stops the scan from
    passing for the wrong reason.
    """
    from app.api.v1 import actions as module

    source = inspect.getsource(module)
    code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
    for forbidden in (".delete(", ".remove(", "TaskService.delete", "ProjectService.delete"):
        assert forbidden not in code, forbidden
    assert "await tasks.create(" in code, "the scanner is not matching the text it should match"


# --------------------------------------------------------------------------- #
# Propose: the refusal is an answer
# --------------------------------------------------------------------------- #


async def test_a_clear_request_produces_a_proposal_and_writes_nothing(
    client, ada, db_session, serving
) -> None:
    """The happy path: one sentence in, one proposal out, no row written.

    The row count is the point. ``propose`` describes a write; if it also made
    one, the confirm dialog would be asking the user to agree to something already
    done.
    """
    project = await _project(client, ada)
    with serving(TASK_INTENT):
        body = await _propose(client, ada, text=CREATE_TASK_TEXT, project_id=project["id"])

    assert body["proposed"] is True
    assert body["refusal"] is None
    proposal = body["proposal"]
    assert proposal["kind"] == CREATE_TASK_KIND
    assert proposal["intent"] == TASK_INTENT
    assert proposal["requires_confirmation"] is True
    assert proposal["destructive"] is False
    assert proposal["payload"] == {
        "title": "draft the migration plan",
        "description": None,
        "project_id": project["id"],
        "priority": "medium",
        "status": "todo",
        "start_date": None,
        "due_date": None,
        "estimated_minutes": None,
        "parent_id": None,
    }
    assert "draft the migration plan" in proposal["summary"]
    assert proposal["target_id"] == project["id"]
    assert all(argument["matched_text"] for argument in proposal["arguments"])
    assert await _task_count(db_session, ada["user"].id) == 0


async def test_the_utterance_reaches_the_classifier_byte_for_byte(client, ada, serving) -> None:
    """No trimming, no lower-casing, before the tokenizer sees it.

    Training consumed the raw dataset strings; normalising here would be a
    distribution shift the checkpoint has never seen, and it would be invisible
    in every other assertion in this file.
    """
    with serving(TASK_INTENT) as classifier:
        await _propose(client, ada, text=CREATE_TASK_TEXT)
    assert classifier.seen == [CREATE_TASK_TEXT]


async def test_an_out_of_scope_utterance_is_a_refusal_and_still_a_200(client, ada, serving) -> None:
    """A refusal is an answer, not a fault.

    A client that had to tell a refusal apart from a 500 would wrap this call in a
    try/catch and the user would see an error dialog for a sentence NEXUS simply
    declined.
    """
    with serving(OUT_OF_SCOPE_INTENT):
        body = await _propose(client, ada, text="what is the weather in reykjavik")

    assert body["proposed"] is False
    assert body["proposal"] is None
    assert body["refusal"]["reason_code"] == "unsupported_intent"
    assert body["refusal"]["reason"]
    assert body["intent"] == OUT_OF_SCOPE_INTENT


async def test_a_destructive_request_is_refused_with_a_reason(client, ada, serving) -> None:
    """The classifier cannot see the verb, so "delete everything" must not act.

    ``task_manage`` covers create, complete, block, cancel, reorder **and**
    delete — one class, one confidence. A surface that turned that sentence into a
    write would delete a board on the strength of a prompt nobody read.
    """
    with serving(TASK_INTENT):
        body = await _propose(client, ada, text="delete all my tasks")

    assert body["proposed"] is False
    assert body["refusal"]["reason_code"] == "destructive_request"


async def test_a_task_creation_with_no_project_is_refused_not_guessed(client, ada, serving) -> None:
    """No project means no proposal, because a guessed id lands on somebody's board.

    ``TaskCreate`` requires a ``project_id``; without one the alternative would be
    inventing a destination, and a task attached to another account's project is a
    worse outcome than asking.
    """
    with serving(TASK_INTENT):
        body = await _propose(client, ada, text=CREATE_TASK_TEXT)

    assert body["proposed"] is False
    assert body["refusal"]["reason_code"] == "context_missing"


async def test_a_completion_resolves_against_the_callers_real_open_tasks(
    client, ada, serving
) -> None:
    """The reference is matched against rows the caller actually owns.

    Nothing about "the API contract" is guessable from the sentence, so this is
    where the owner-scoped ``TaskService.list`` read pays off: the returned
    ``target_id`` is a real row id, and a second account's identically named task
    could not have produced it.
    """
    project = await _project(client, ada)
    task = await _task(client, ada, project["id"], CONTRACT_TASK_TITLE)
    with serving(TASK_INTENT):
        body = await _propose(client, ada, text=COMPLETE_TASK_TEXT, project_id=project["id"])

    proposal = body["proposal"]
    assert proposal["kind"] == "complete_task"
    assert proposal["target_id"] == task["id"]
    assert proposal["target_label"] == CONTRACT_TASK_TITLE
    assert proposal["payload"] == {"status": "completed", "note": None}


async def test_a_finished_task_is_not_offered_as_a_completion_candidate(
    client, ada, serving
) -> None:
    """A card already completed is not something to complete again.

    Offering it would produce a proposal whose confirmation is a guaranteed no-op,
    which is how a confirm dialog teaches users not to read it.
    """
    project = await _project(client, ada)
    task = await _task(client, ada, project["id"], CONTRACT_TASK_TITLE, status="in_progress")
    completed = await client.post(f"/api/v1/tasks/{task['id']}/complete", headers=ada["headers"])
    assert completed.status_code == 200, completed.text

    with serving(TASK_INTENT):
        body = await _propose(client, ada, text=COMPLETE_TASK_TEXT, project_id=project["id"])

    assert body["proposed"] is False
    assert body["refusal"]["reason_code"] == "task_reference_not_found"


async def test_the_candidate_list_is_read_only_for_the_intent_that_uses_it(
    client, ada, serving, monkeypatch
) -> None:
    """Both directions of one guard, asserted together so neither can be vacuous.

    A ``knowledge_capture`` proposal reads no task list at all, and a
    ``task_manage`` proposal reads exactly one. Pinning only the first would pass
    with the fetch deleted; pinning only the second would pass with it moved above
    the intent check — which is what this test is here to catch, since an
    owner-scoped query on every unrelated request is a cost paid for nothing.
    """
    from app.services.task_service import TaskService

    calls: list[dict[str, Any]] = []
    original = TaskService.list

    async def _counting(self: Any, **kwargs: Any) -> Any:
        calls.append(kwargs)
        return await original(self, **kwargs)

    monkeypatch.setattr(TaskService, "list", _counting)

    with serving(NOTE_INTENT):
        note = await _propose(client, ada, text="save a note called why the prune keeps a suffix")
    assert note["proposal"]["kind"] == "create_note"
    assert calls == [], "a note proposal read the caller's task list"

    project = await _project(client, ada)
    with serving(TASK_INTENT):
        task = await _propose(client, ada, text=CREATE_TASK_TEXT, project_id=project["id"])
    assert task["proposal"]["kind"] == CREATE_TASK_KIND
    assert len(calls) == 1, "a task_manage proposal did not read the candidate list once"
    assert calls[0]["owner"].id == ada["user"].id
    assert calls[0]["limit"] == 50


async def test_the_caller_zone_is_resolved_and_a_bad_one_is_refused(client, ada, serving) -> None:
    """The caller's zone goes through the planner's resolver, which refuses rather than guessing.

    A silent fallback to UTC would shift every date boundary by the zone's offset
    and answer a well-formed question with another question's data.
    """
    project = await _project(client, ada)
    with serving(TASK_INTENT):
        response = await client.post(
            f"{PROPOSE}?tz=Europe/Berlin",
            json={"text": CREATE_TASK_TEXT, "project_id": project["id"]},
            headers=ada["headers"],
        )
    assert response.status_code == 200, response.text
    assert response.json()["proposed"] is True

    with serving(TASK_INTENT):
        rejected = await client.post(
            f"{PROPOSE}?tz=Mars/Olympus_Mons",
            json={"text": CREATE_TASK_TEXT, "project_id": project["id"]},
            headers=ada["headers"],
        )
    assert rejected.status_code == 422, rejected.text


async def test_an_unknown_project_is_a_404_before_anything_is_proposed(
    client, ada, serving
) -> None:
    """Another account's project id is not found, and its name never leaks.

    A 403 would confirm the id exists, which turns this endpoint into a directory
    of other people's boards.
    """
    with serving(TASK_INTENT):
        response = await client.post(
            PROPOSE,
            json={"text": CREATE_TASK_TEXT, "project_id": str(UUID(int=4242))},
            headers=ada["headers"],
        )
    assert response.status_code == 404, response.text


async def test_a_degraded_classifier_answers_503_rather_than_fabricating(
    client, ada, degraded
) -> None:
    """The one failure that is not a 200, because the next move is a write.

    Answering with an invented intent would be NEXUS inventing a user's
    instruction and then acting on it.
    """
    response = await client.post(PROPOSE, json={"text": CREATE_TASK_TEXT}, headers=ada["headers"])
    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "ml_unavailable"


OVER_LONG_TEXT = "word " * (MAX_UTTERANCE_CHARACTERS // 5 + 2)

#: Long enough to trip :meth:`app.ml.classifier.IntentClassifier._validate` and
#: short enough that nothing else has to read it. Derived from the constant rather
#: than typed out, so tightening the deployment's limit does not quietly turn this
#: case into a test of the 500 path instead.
assert len(OVER_LONG_TEXT) > MAX_UTTERANCE_CHARACTERS, OVER_LONG_TEXT[:20]

CREDENTIAL_TEXT = "my password is hunter2 and my key is sk-abcdefghijklmnopqrst"


@pytest.mark.parametrize(
    ("label", "text"),
    [
        ("blank", "   "),
        ("over-long", OVER_LONG_TEXT),
        ("credential-shaped", CREDENTIAL_TEXT),
    ],
    ids=["blank", "over-long", "credential-shaped"],
)
async def test_text_the_classifier_will_not_classify_is_a_422(
    client, ada, validating, label: str, text: str
) -> None:
    """The classifier's own rules, reused rather than restated at the edge.

    The credential screen lives in ``IntentClassifier._validate`` and nowhere
    else, so a schema that duplicated only the length bound would accept the very
    input Phase 11 exists to refuse.
    """
    response = await client.post(PROPOSE, json={"text": text}, headers=ada["headers"])
    assert response.status_code == 422, f"{label}: {response.text}"


async def test_both_routes_require_a_caller(offline_client) -> None:
    """Neither door is anonymous, and neither is a classification oracle."""
    for path, body in (
        (PROPOSE, {"text": CREATE_TASK_TEXT}),
        (CONFIRM, _confirm_body()),
    ):
        response = await offline_client.post(path, json=body)
        assert response.status_code == 401, path


async def test_a_caller_without_the_capability_gets_a_403(offline_client, app) -> None:
    """The route-level gate is real, not decoration.

    ``require_permission`` builds a fresh closure per call, so overriding
    ``get_current_user`` — the dependency that closure reads — reaches the gate
    while leaving the gate itself in place. An unrecognised role is the
    fail-closed path through :func:`app.core.permissions.has_permission`.
    """
    from app.api.deps import get_authenticated_user
    from app.core.deps import get_current_user
    from app.models.user import User

    stranger = User(username="nobody", email="nobody@nexus.test", role="not-a-role")
    app.dependency_overrides[get_current_user] = lambda: stranger
    app.dependency_overrides[get_authenticated_user] = lambda: stranger
    try:
        for path, body in (
            (PROPOSE, {"text": CREATE_TASK_TEXT}),
            (CONFIRM, _confirm_body()),
        ):
            response = await offline_client.post(path, json=body)
            assert response.status_code == 403, f"{path}: {response.text}"
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_authenticated_user, None)


# --------------------------------------------------------------------------- #
# Confirm: the round trip
# --------------------------------------------------------------------------- #


async def test_the_round_trip_creates_a_task_and_records_the_activity_event(
    client, ada, db_session, serving
) -> None:
    """Propose, read the sentence, confirm, and find the row *and* the trail.

    The activity row is half the assertion. A task created here that recorded
    nothing would be a card whose history does not exist, which is precisely what
    wiring the services through ``app.api.deps`` — rather than calling the
    repositories — is for.
    """
    project = await _project(client, ada)
    with serving(TASK_INTENT):
        proposed = await _propose(client, ada, text=CREATE_TASK_TEXT, project_id=project["id"])

    proposal = proposed["proposal"]
    result = await _confirm(
        client,
        ada,
        kind=proposal["kind"],
        intent=proposal["intent"],
        payload=proposal["payload"],
    )

    assert result["kind"] == CREATE_TASK_KIND
    assert result["entity"] == "task"
    assert result["outcome"] == "created"
    assert result["applied"] is True
    assert "draft the migration plan" in result["message"]

    stored = await client.get(f"/api/v1/tasks/{result['entity_id']}", headers=ada["headers"])
    assert stored.status_code == 200, stored.text
    assert stored.json()["title"] == "draft the migration plan"
    assert stored.json()["project_id"] == project["id"]

    assert ActivityEvent.TASK_CREATED.value in await _event_types(db_session, ada["user"].id)


async def test_a_completion_round_trip_moves_the_task_and_records_the_transition(
    client, ada, db_session, serving
) -> None:
    """The second kind, and the only one that changes a row rather than adding one.

    The card is seeded ``in_progress`` because that is the state NEXUS's own state
    machine permits ``completed`` to be reached from — the endpoint goes through
    ``TaskService.set_status`` like every typed route, and inherits its rules
    rather than inventing its own.
    """
    project = await _project(client, ada)
    task = await _task(
        client, ada, project["id"], CONTRACT_TASK_TITLE, status=TaskStatus.IN_PROGRESS.value
    )
    with serving(TASK_INTENT):
        proposed = await _propose(client, ada, text=COMPLETE_TASK_TEXT, project_id=project["id"])

    proposal = proposed["proposal"]
    result = await _confirm(
        client,
        ada,
        kind=proposal["kind"],
        intent=proposal["intent"],
        payload=proposal["payload"],
        target_id=proposal["target_id"],
    )

    assert result["entity_id"] == task["id"]
    assert result["outcome"] == "updated"
    assert result["applied"] is True
    stored = await client.get(f"/api/v1/tasks/{task['id']}", headers=ada["headers"])
    assert stored.json()["status"] == TaskStatus.COMPLETED.value
    assert ActivityEvent.TASK_COMPLETED.value in await _event_types(db_session, ada["user"].id)


@pytest.mark.parametrize(
    ("intent", "utterance", "kind", "entity", "event", "verify"),
    [
        (
            PROJECT_INTENT,
            "create a project called the atlas rewrite",
            "create_project",
            "project",
            ActivityEvent.PROJECT_CREATED.value,
            "project",
        ),
        (
            NOTE_INTENT,
            "save a note called why the prune keeps a suffix",
            "create_note",
            "note",
            ActivityEvent.NOTE_CREATED.value,
            "note",
        ),
        (
            GOAL_INTENT,
            "i want to learn rust",
            "create_learning_goal",
            "learning_goal",
            ActivityEvent.LEARNING_GOAL_CREATED.value,
            "goal",
        ),
    ],
    ids=["project", "note", "learning-goal"],
)
async def test_every_creation_kind_round_trips_through_the_same_two_routes(
    client,
    ada,
    db_session,
    serving,
    intent: str,
    utterance: str,
    kind: str,
    entity: str,
    event: str,
    verify: str,
) -> None:
    """All four creations share one dispatcher path and each writes its own event.

    Parameterised rather than written out four times because the claim is that
    they are *the same* code path with a different spec, and a copy per kind would
    let them drift without anything here noticing.
    """
    with serving(intent):
        proposed = await _propose(client, ada, text=utterance)
    proposal = proposed["proposal"]
    assert proposal["kind"] == kind, proposed

    result = await _confirm(
        client,
        ada,
        kind=proposal["kind"],
        intent=proposal["intent"],
        payload=proposal["payload"],
    )
    assert result["outcome"] == "created", result
    assert result["entity"] == entity, result
    assert event in await _event_types(db_session, ada["user"].id), await _event_types(
        db_session, ada["user"].id
    )

    read_back = {
        "project": ("/api/v1/projects/{id}", ada["headers"]),
        "note": ("/api/v1/knowledge/notes/{id}", ada["headers"]),
        "goal": ("/api/v1/learning/goals/{id}", ada["headers"]),
    }[verify]
    stored = await client.get(read_back[0].format(id=result["entity_id"]), headers=read_back[1])
    assert stored.status_code == 200, stored.text
    assert stored.json()["id"] == result["entity_id"]


# --------------------------------------------------------------------------- #
# Confirm: what the client is not believed about
# --------------------------------------------------------------------------- #


async def test_a_forged_target_id_never_touches_another_accounts_task(
    client, ada, grace, db_session
) -> None:
    """The security test of the file: a payload naming somebody else's row is a no-op.

    Grace's task is left exactly as it was — still ``todo``, still one row, no
    event — because ``TaskService.get``'s query is scoped by ``owner_id`` and the
    row is never loaded. A permission check on an id that had already been loaded
    would have been the weaker control.
    """
    grace_project = await _project(client, grace)
    grace_task = await _task(client, grace, grace_project["id"], CONTRACT_TASK_TITLE)

    response = await client.post(
        CONFIRM,
        json=_confirm_body(
            kind="complete_task",
            payload={"status": "completed", "note": None},
            target_id=grace_task["id"],
        ),
        headers=ada["headers"],
    )
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "not_found"

    stored = await client.get(f"/api/v1/tasks/{grace_task['id']}", headers=grace["headers"])
    assert stored.json()["status"] == TaskStatus.TODO.value
    assert ActivityEvent.TASK_COMPLETED.value not in await _event_types(
        db_session, grace["user"].id
    )
    assert await _task_count(db_session, ada["user"].id) == 0
    assert await _task_count(db_session, grace["user"].id) == 1


async def test_a_forged_project_id_never_creates_a_task_on_another_board(
    client, ada, grace, db_session
) -> None:
    """The other half of the same test: a create naming a foreign project.

    ``TaskService.create`` re-checks the project through its own scoped lookup and
    writes nothing when it is missing, so the forged ``project_id`` produces a 404
    and not a card on Grace's board.
    """
    grace_project = await _project(client, grace)

    response = await client.post(
        CONFIRM,
        json=_confirm_body(payload={"title": "Injected", "project_id": grace_project["id"]}),
        headers=ada["headers"],
    )
    assert response.status_code == 404, response.text
    assert await _task_count(db_session, grace["user"].id) == 0
    assert await _task_count(db_session, ada["user"].id) == 0


@pytest.mark.parametrize(
    "kind",
    ["delete_task", "DELETE_TASK", "cancel_task", "", "create_task ", "delete everything"],
    ids=["snake-case", "screaming", "not-a-member", "empty", "padded", "prose"],
)
async def test_a_kind_outside_the_closed_set_is_a_422(client, ada, db_session, kind: str) -> None:
    """The edge rejects the name before the handler runs at all.

    Including the near misses — ``"create_task "`` with a trailing space, ``""``
    empty, prose — because a lenient coercion is how a name that was never a
    member starts reaching a dispatcher.
    """
    response = await client.post(
        CONFIRM,
        json=_confirm_body(kind=kind),
        headers=ada["headers"],
    )
    assert response.status_code == 422, f"{kind!r}: {response.text}"
    assert await _task_count(db_session, ada["user"].id) == 0


async def test_a_spec_that_claims_to_be_destructive_is_refused_not_executed(
    client, ada, db_session, monkeypatch
) -> None:
    """The handler asks the table whether the action is destructive, and refuses.

    Unreachable with the shipped table — ``ActionSpec.destructive`` is a property
    returning ``False``, so a destructive spec cannot be constructed — which is
    exactly why the guard is worth pinning: it is the half that would still hold
    if somebody replaced the property with a field.
    """
    _install_spec(monkeypatch, destructive=True)
    response = await client.post(CONFIRM, json=_confirm_body(), headers=ada["headers"])
    assert response.status_code == 422, response.text
    assert await _task_count(db_session, ada["user"].id) == 0


async def test_the_permission_a_kind_needs_is_re_checked_server_side(
    client, ada, db_session, monkeypatch
) -> None:
    """Proposing under one capability does not carry over to executing under another.

    The route gate is ``analytics.read``; the action's own capability is a second
    check inside the handler. A spec carrying a permission no role holds — here a
    name outside :class:`~app.core.permissions.Permission` entirely, which
    ``has_permission`` treats as not held rather than raising — must be refused.
    """
    _install_spec(monkeypatch, permission="actions.not_a_real_permission")
    response = await client.post(CONFIRM, json=_confirm_body(), headers=ada["headers"])
    assert response.status_code == 403, response.text
    assert await _task_count(db_session, ada["user"].id) == 0


async def test_an_intent_the_kind_cannot_have_came_from_is_a_422(client, ada, db_session) -> None:
    """The binding between the two calls.

    A client cannot confirm a ``create_task`` while claiming the classifier said
    ``knowledge_capture``: the table says this kind exists only behind one intent,
    and a payload that contradicts it did not come out of a propose.
    """
    response = await client.post(
        CONFIRM,
        json=_confirm_body(intent=NOTE_INTENT),
        headers=ada["headers"],
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["expected_intent"] == TASK_INTENT
    assert await _task_count(db_session, ada["user"].id) == 0


async def test_a_completion_with_no_target_id_is_a_422(client, ada, db_session) -> None:
    """The row is part of the payload as far as the dispatcher is concerned.

    ``TaskStatusChange`` carries only a status, so without ``target_id`` there is
    no task to act on and nothing may be guessed.
    """
    response = await client.post(
        CONFIRM,
        json=_confirm_body(kind="complete_task", payload={"status": "completed", "note": None}),
        headers=ada["headers"],
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["field"] == "target_id"


@pytest.mark.parametrize(
    ("label", "payload"),
    [
        ("empty", {}),
        ("blank title", {"title": "", "project_id": "3b1f5b1e-0000-4000-8000-000000000001"}),
        ("missing project", {"title": "Draft the migration plan"}),
        ("wrong type", {"title": 17, "project_id": "3b1f5b1e-0000-4000-8000-000000000001"}),
        (
            "reversed window",
            {
                "title": "Draft the migration plan",
                "project_id": "3b1f5b1e-0000-4000-8000-000000000001",
                "start_date": "2026-03-20",
                "due_date": "2026-03-10",
            },
        ),
        (
            "unknown key",
            {
                "title": "Draft the migration plan",
                "project_id": "3b1f5b1e-0000-4000-8000-000000000001",
                "owner_id": "3b1f5b1e-0000-4000-8000-000000000002",
            },
        ),
        ("not an object", ["draft the migration plan"]),
    ],
    ids=[
        "empty",
        "blank-title",
        "missing-project",
        "wrong-type",
        "reversed-window",
        "unknown-key",
        "not-an-object",
    ],
)
async def test_a_malformed_payload_is_rejected_before_any_service_is_called(
    client, ada, db_session, monkeypatch, label: str, payload: Any
) -> None:
    """The guard is proven by making the service unreachable.

    ``TaskService.create`` is replaced with something that raises if it is reached,
    so a regression that let a payload past the guard fails loudly here instead of
    quietly writing a row. The unknown-key case matters most: ``TaskCreate`` does
    not set ``extra="forbid"``, so Pydantic's default would have dropped
    ``owner_id`` and answered 200.
    """
    from app.services.task_service import TaskService

    def _unreachable(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a service was called before the payload was re-derived")

    monkeypatch.setattr(TaskService, "create", _unreachable)

    response = await client.post(
        CONFIRM, json=_confirm_body(payload=payload), headers=ada["headers"]
    )
    assert response.status_code == 422, f"{label}: {response.text}"
    assert await _task_count(db_session, ada["user"].id) == 0


async def test_a_payload_the_user_edited_is_honoured_and_still_checked(
    client, ada, db_session
) -> None:
    """An edit in the confirm dialog is the feature — and it is validated, not trusted.

    The title below is one the user typed into the dialog and NEXUS never
    extracted. It is written, because the row still belongs to the caller and the
    project is still theirs; and it would be refused had it named somebody else's
    board.
    """
    project = await _project(client, ada)
    response = await client.post(
        CONFIRM,
        json=_confirm_body(payload={"title": "renamed in the dialog", "project_id": project["id"]}),
        headers=ada["headers"],
    )
    assert response.status_code == 200, response.text
    assert response.json()["outcome"] == "created"

    stored = await client.get(
        f"/api/v1/tasks/{response.json()['entity_id']}", headers=ada["headers"]
    )
    assert stored.json()["title"] == "renamed in the dialog"
    assert ActivityEvent.TASK_CREATED.value in await _event_types(db_session, ada["user"].id)


# --------------------------------------------------------------------------- #
# Confirm: honest reporting
# --------------------------------------------------------------------------- #


async def test_an_illegal_transition_is_reported_rather_than_fabricated(
    client, ada, db_session
) -> None:
    """A transition the state machine forbids produces the service's error.

    ``todo`` cannot reach ``completed`` — the board is a state machine and the
    endpoint inherits it rather than inventing a shortcut. The 422 is the honest
    answer; a ``200`` with ``applied: true`` would be NEXUS claiming to have moved
    a card it did not move, and the row would still say otherwise.
    """
    project = await _project(client, ada)
    task = await _task(client, ada, project["id"], CONTRACT_TASK_TITLE)

    response = await client.post(
        CONFIRM,
        json=_confirm_body(
            kind="complete_task",
            payload={"status": "completed", "note": None},
            target_id=task["id"],
        ),
        headers=ada["headers"],
    )
    assert response.status_code == 422, response.text
    assert "cannot move from 'todo' to 'completed'" in response.json()["error"]["message"]

    stored = await client.get(f"/api/v1/tasks/{task['id']}", headers=ada["headers"])
    assert stored.json()["status"] == TaskStatus.TODO.value
    assert ActivityEvent.TASK_COMPLETED.value not in await _event_types(db_session, ada["user"].id)


async def test_a_completion_blocked_by_open_work_is_reported_rather_than_fabricated(
    client, ada, db_session
) -> None:
    """The second service refusal worth pinning: a prerequisite is still open.

    The card is legitimately in progress and the transition is legal, so nothing
    about the request is malformed — ``TaskService`` refuses because the edge the
    user declared is not satisfied. Reporting that as a failure is the only honest
    option; the alternative is a completed card resting on work nobody did.
    """
    project = await _project(client, ada)
    blocker = await _task(client, ada, project["id"], "Write the migration runbook")
    blocked = await _task(
        client, ada, project["id"], "Cut the release", status=TaskStatus.IN_PROGRESS.value
    )
    dependency = await client.post(
        f"/api/v1/tasks/{blocked['id']}/dependencies?depends_on_id={blocker['id']}",
        headers=ada["headers"],
    )
    assert dependency.status_code == 201, dependency.text

    response = await client.post(
        CONFIRM,
        json=_confirm_body(
            kind="complete_task",
            payload={"status": "completed", "note": None},
            target_id=blocked["id"],
        ),
        headers=ada["headers"],
    )
    assert response.status_code == 422, response.text
    assert "unfinished" in response.json()["error"]["message"]

    stored = await client.get(f"/api/v1/tasks/{blocked['id']}", headers=ada["headers"])
    assert stored.json()["status"] == TaskStatus.IN_PROGRESS.value
    assert ActivityEvent.TASK_COMPLETED.value not in await _event_types(db_session, ada["user"].id)


async def test_a_replayed_create_does_not_duplicate_the_row(client, ada, db_session) -> None:
    """The second press of the button is a no-op naming the row the first press made.

    The realistic duplicate is a double-click or a retry after a timeout, and in
    both the desired state was already reached. A 409 would render a working system
    as a failure; a second row would render it as data loss of the user's intent to
    make one thing. So the replay reports the truth: nothing was applied, and here
    is the id it was applied to.
    """
    project = await _project(client, ada)
    payload = {"title": "draft the migration plan", "project_id": project["id"]}

    first = await _confirm(client, ada, payload=payload)
    second = await _confirm(client, ada, payload=payload)

    assert first["outcome"] == "created"
    assert first["applied"] is True
    assert second["outcome"] == "no_op"
    assert second["applied"] is False
    assert second["entity_id"] == first["entity_id"]
    assert "did not create a second one" in second["message"]
    assert await _task_count(db_session, ada["user"].id) == 1


async def test_a_replayed_completion_reports_a_no_op_and_writes_one_event(
    client, ada, db_session
) -> None:
    """A completion is idempotent for free, and says so rather than claiming again.

    ``set_status`` returns the row unchanged when it is already in the target
    state, so the endpoint can tell the difference between "I moved it" and "it was
    already there" — and the second is not reported as a transition, which is what
    a fabricated ``applied: true`` would amount to.
    """
    project = await _project(client, ada)
    task = await _task(
        client, ada, project["id"], CONTRACT_TASK_TITLE, status=TaskStatus.IN_PROGRESS.value
    )
    body = _confirm_body(
        kind="complete_task",
        payload={"status": "completed", "note": None},
        target_id=task["id"],
    )

    first = await _confirm(client, ada, **body)
    second = await _confirm(client, ada, **body)

    assert (first["outcome"], first["applied"]) == ("updated", True)
    assert (second["outcome"], second["applied"]) == ("no_op", False)
    assert second["entity_id"] == task["id"]
    assert "already" in second["message"]
    assert (await _event_types(db_session, ada["user"].id)).count(
        ActivityEvent.TASK_COMPLETED.value
    ) == 1


async def test_a_row_that_only_looks_similar_is_not_mistaken_for_a_duplicate(
    client, ada, db_session
) -> None:
    """The replay check compares the whole field, not the substring the search found.

    Three of the services' ``search`` parameters match across more than one
    column, so a row found by a search is only a candidate. A card whose title is a
    prefix of the requested one must not suppress a real creation — the failure
    mode of a duplicate check is suppression, and it has to be pinned as tightly as
    the duplication it prevents.
    """
    project = await _project(client, ada)
    await _task(client, ada, project["id"], "draft the migration")

    result = await _confirm(
        client,
        ada,
        payload={"title": "draft the migration plan", "project_id": project["id"]},
    )
    assert result["outcome"] == "created", result
    assert await _task_count(db_session, ada["user"].id) == 2


#: The one kind the destructive and permission tests re-point the table at.
CREATE_TASK_KIND = "create_task"
