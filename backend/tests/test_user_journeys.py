"""§14.23 — the seven user journeys, end to end.

These are not unit tests with extra steps. Each walks a story through the real
HTTP API, against the live database and — where the story needs it — the real
Phase 10 checkpoint, and asserts on **values** rather than status codes. A
journey that only checks for ``200`` proves a router is mounted, which was known
before this file existed.

**Why the stories are worth pinning.** NEXUS is thirteen features that have to
agree with each other: a task completed on the Tasks page has to be the same
task the Analytics page scores, the risk engine has to see the deadline that
created it, and the Command Center has to show the consequences of both. Every
one of those is a seam between two features that each have their own tests, and
a seam is exactly where a unit-test-per-feature suite stops seeing anything.

**Two limits, stated rather than hidden.**

The browser half of the voice journey is not here. Speech recognition and speech
synthesis are browser APIs, exercised in the frontend suite against fakes under
jsdom; there is no microphone in this environment, and faking one at the HTTP
boundary would test the fake. What *is* pinnable server-side is the other half —
that a transcript, shaped exactly as a recogniser delivers one, reaches the single
classifier and comes back naming a real service — and that is what Journey 6
checks.

The developer journey needs a real Git work tree. Where one cannot be built the
test skips with the reason rather than asserting against a mock repository.
"""

from __future__ import annotations

import subprocess
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def loaded_classifier():
    """Load the real checkpoint for the duration of a test.

    ``ASGITransport`` does not execute the lifespan, so nothing has loaded the
    model and the ML routes answer 503 — correct for a server that was never
    started, useless for a test about what the model decides. Loading here is the
    honest middle: the same checkpoint the application loads, loaded the way the
    application loads it.
    """
    from app.core.config import get_settings
    from app.ml.runtime import get_ml_runtime, reset_ml_runtime

    settings = get_settings()
    if not settings.ml_checkpoint_exists:
        pytest.skip(f"no Phase 10 checkpoint at {settings.ml_resolved_model_path}")
    pytest.importorskip("torch", reason="torch is not installed")
    pytest.importorskip("transformers", reason="transformers is not installed")

    reset_ml_runtime()
    runtime = get_ml_runtime()
    status = runtime.load()
    if not status.available:
        pytest.skip(f"the classifier would not load here: {status.reason}")
    try:
        yield runtime
    finally:
        runtime.shutdown()
        reset_ml_runtime()


async def register(http: Any, *, username: str) -> str:
    """Create an account and return its access token."""
    email = f"{username}-{uuid.uuid4().hex[:8]}@example.com"
    response = await http.post(
        "/api/v1/auth/register",
        json={
            "username": username,
            "email": email,
            "password": "Journeys!2026",
            "display_name": username.title(),
        },
    )
    assert response.status_code == 201, response.text
    login = await http.post(
        "/api/v1/auth/login", json={"email": email, "password": "Journeys!2026"}
    )
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


def authed(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def window() -> dict[str, str]:
    """A 31-day window ending today, which every analytics route requires."""
    today = date.today()
    return {"start": (today - timedelta(days=30)).isoformat(), "end": today.isoformat()}


# `git` is invoked with a fixed argument list, no shell, and no path from a test
# interpolated into a command string — the only caller is this module and the only
# variable is the temp directory it just created. The bandit rules flag subprocess
# use and a bare executable name regardless, so they are silenced here rather than
# at every call site, with the reasoning recorded once.
def init_repo(path: Path) -> bool:
    """Build a real Git work tree with one commit. False when git is absent."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        env = {
            "GIT_AUTHOR_NAME": "Journey",
            "GIT_AUTHOR_EMAIL": "journey@example.com",
            "GIT_COMMITTER_NAME": "Journey",
            "GIT_COMMITTER_EMAIL": "journey@example.com",
        }
        for args in (
            ["git", "init", "-q"],
            ["git", "config", "user.email", "journey@example.com"],
            ["git", "config", "user.name", "Journey"],
            ["git", "commit", "--allow-empty", "-q", "-m", "journey commit"],
        ):
            result = subprocess.run(  # noqa: S603 - fixed argv, no shell
                args, cwd=path, capture_output=True, env=env, timeout=30, check=False
            )
            if result.returncode != 0:
                return False
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def git_available() -> bool:
    # `git` is a fixed program with a fixed argument, no shell, and no test
    # input reaching the command line — the same trust the application's own Git
    # surface already requires. Bandit flags any partial executable path, so the
    # exemption is stated once here rather than reasoned about at every call.
    return (
        subprocess.run(
            # ruff reports the partial-executable-path rule against the argument,
            # so that is where its exemption has to sit.
            ["git", "--version"],  # noqa: S607
            capture_output=True,
            check=False,
        ).returncode
        == 0
    )


# ---------------------------------------------------------------------------
# Journey 1 — Work
# ---------------------------------------------------------------------------


async def test_journey_work_carries_a_task_from_creation_to_analytics(client):
    """Create a project, a task, work a session against it, complete it, see it.

    The last step is the one that matters. Everything before it is Phase 3 and 4
    working correctly; the assertion is that the Analytics surface counts a task
    completed through the ordinary API, which is the seam between the execution
    layer and the intelligence layer.
    """
    token = await register(client, username="work")
    headers = authed(token)

    response = await client.post(
        "/api/v1/projects",
        json={"name": "Nebula", "description": "the rewrite"},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    project_id = response.json()["id"]

    response = await client.post(
        "/api/v1/tasks",
        json={
            "project_id": project_id,
            "title": "Ship the rollout plan",
            "priority": "high",
            "due_date": date.today().isoformat(),
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    task = response.json()
    assert task["status"] == "todo"
    assert task["title"] == "Ship the rollout plan"

    response = await client.post(f"/api/v1/tasks/{task['id']}/start", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "in_progress"

    now = datetime.now(UTC)
    response = await client.post(
        "/api/v1/work-sessions",
        json={
            "task_id": task["id"],
            "project_id": project_id,
            "scheduled_start": now.isoformat(),
            "scheduled_end": (now + timedelta(hours=2)).isoformat(),
            "event_type": "focus",
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    session_id = response.json()["id"]

    # A session is created `planned`. Stopping one that was never started is a
    # 422 by design — recorded time is time actually worked, not time allocated.
    response = await client.post(f"/api/v1/work-sessions/{session_id}/start", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "active", response.text

    response = await client.post(f"/api/v1/work-sessions/{session_id}/stop", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed", "a stopped session must be completed"

    response = await client.post(f"/api/v1/tasks/{task['id']}/complete", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed"

    # Task analytics is its own route; `overview` folds in the score and
    # deadlines rather than the task counts.
    response = await client.get("/api/v1/analytics/tasks", params=window(), headers=headers)
    assert response.status_code == 200, response.text
    measured = response.json()
    assert measured["total_tasks"] == 1, measured
    assert measured["tasks_completed"] == 1, (
        "a task completed through the API is invisible to Analytics — the two "
        "surfaces disagree about the same rows"
    )


async def test_journey_work_is_invisible_to_another_account(client):
    """The tenancy half of every journey.

    Journey 1 creates a project and a task. A second account with an
    identically-named project must not see either, through any of the reads the
    journey used.
    """
    first = authed(await register(client, username="tenant-a"))

    response = await client.post("/api/v1/projects", json={"name": "Shared Name"}, headers=first)
    project_id = response.json()["id"]
    await client.post(
        "/api/v1/tasks",
        json={"project_id": project_id, "title": "Private work"},
        headers=first,
    )

    second = authed(await register(client, username="tenant-b"))

    response = await client.get("/api/v1/projects", params={"limit": 100}, headers=second)
    assert all(row["name"] != "Shared Name" for row in response.json()["items"])

    # Knowing the id is not a capability.
    response = await client.get(f"/api/v1/projects/{project_id}", headers=second)
    assert response.status_code == 404, "ownership is 404, never 403 — a 403 confirms the id exists"

    response = await client.get("/api/v1/tasks", params={"limit": 100}, headers=second)
    assert all(row["title"] != "Private work" for row in response.json()["items"])


# ---------------------------------------------------------------------------
# Journey 2 — Intelligence
# ---------------------------------------------------------------------------


async def test_journey_intelligence_runs_detection_and_reads_its_output(client):
    """Activity → analytics → risk detection → recommendations.

    The risk pass is the interesting step: it is the first thing NEXUS computes
    from records rather than recording them, so it is where a cross-feature seam
    would break.
    """
    headers = authed(await register(client, username="intel"))

    response = await client.post("/api/v1/projects", json={"name": "Risk fixture"}, headers=headers)
    project_id = response.json()["id"]
    await client.post(
        "/api/v1/tasks",
        json={
            "project_id": project_id,
            "title": "Overdue by a fortnight",
            "priority": "critical",
            "due_date": (date.today() - timedelta(days=14)).isoformat(),
        },
        headers=headers,
    )

    response = await client.get("/api/v1/analytics/tasks", params=window(), headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["total_tasks"] == 1

    response = await client.post(
        "/api/v1/intelligence/evaluate",
        json={"today": date.today().isoformat(), "window_days": 30},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    evaluated = response.json()
    assert evaluated["evaluated"] is True, evaluated
    assert evaluated["evaluated_at"], "an evaluated pass must say when it ran"
    # `reason_if_not_evaluated` names which detectors declined and why — a user
    # with no availability rules is told that, rather than shown a silent empty
    # result. The absence of risks here is the engine being honest about
    # capacity, not a failure to run.
    assert "reason_if_not_evaluated" in evaluated, evaluated

    response = await client.get("/api/v1/risks", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["items"], (
        "detection found risks the register does not list — the engine and the "
        "register disagree about the same rows"
    )

    response = await client.get("/api/v1/recommendations", headers=headers)
    assert response.status_code == 200, response.text


async def test_journey_intelligence_risk_register_is_tenant_scoped(client):
    """A second account's risk pass must not surface the first account's rows."""
    first = authed(await register(client, username="risk-a"))

    response = await client.post("/api/v1/projects", json={"name": "Risk isolation"}, headers=first)
    project_id = response.json()["id"]
    await client.post(
        "/api/v1/tasks",
        json={
            "project_id": project_id,
            "title": "Far overdue",
            "priority": "critical",
            "due_date": (date.today() - timedelta(days=30)).isoformat(),
        },
        headers=first,
    )
    await client.post(
        "/api/v1/intelligence/evaluate",
        json={"today": date.today().isoformat(), "window_days": 30},
        headers=first,
    )

    second = authed(await register(client, username="risk-b"))
    response = await client.get("/api/v1/risks", headers=second)
    assert response.json()["items"] == [], "one account's risk pass leaked into another's register"


# ---------------------------------------------------------------------------
# Journey 3 — Learning
# ---------------------------------------------------------------------------


async def test_journey_learning_records_study_and_reports_it(client):
    """Goal → activity → the record is readable → career evidence follows.

    Goal *progress* is deliberately NOT derived from study time:
    ``goal_progress`` is the mean of figures the user set by hand, and the module
    says so in as many words — an account that sets its progress generously and
    one that sets it conservatively produce different numbers for identical
    histories. Asserting that studying does not move it is asserting that
    decision rather than fighting it.
    """
    headers = authed(await register(client, username="learn"))

    response = await client.post(
        "/api/v1/learning/skills", json={"name": "Distributed systems"}, headers=headers
    )
    assert response.status_code == 201, response.text
    skill_id = response.json()["id"]

    response = await client.post(
        "/api/v1/learning/goals",
        json={
            "title": "Finish the Raft paper",
            "target_skill_id": skill_id,
            "target_date": (date.today() + timedelta(days=30)).isoformat(),
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    goal_id = response.json()["id"]

    response = await client.get("/api/v1/learning/goals", headers=headers)
    before = next(row for row in response.json()["items"] if row["id"] == goal_id)
    assert before["progress"] == 0

    response = await client.post(
        "/api/v1/learning/activities",
        json={
            "title": "Read the consensus chapter",
            "activity_type": "study_session",
            "skill_id": skill_id,
            "goal_id": goal_id,
            "duration_minutes": 90,
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    activity_id = response.json()["id"]

    response = await client.get(
        "/api/v1/learning/activities",
        params={"skill_id": skill_id, "limit": 50},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    recorded = [row for row in response.json()["items"] if row["id"] == activity_id]
    assert recorded, "the recorded activity is not readable through the ordinary list"
    assert recorded[0]["duration_minutes"] == 90, recorded[0]

    response = await client.get("/api/v1/learning/goals", headers=headers)
    after = next(row for row in response.json()["items"] if row["id"] == goal_id)
    assert after["progress"] == before["progress"], (
        "goal progress moved without the user setting it — it is a user-set "
        "field, not a measurement of study time"
    )

    response = await client.post(
        "/api/v1/career/evidence",
        json={
            "evidence_type": "skill_activity",
            "title": "Raft consensus, from the reading",
            "occurred_on": date.today().isoformat(),
            "skill_id": skill_id,
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text

    response = await client.get("/api/v1/career/summary", headers=headers)
    assert response.status_code == 200, response.text


# ---------------------------------------------------------------------------
# Journey 4 — Developer
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not git_available(), reason="git is not on PATH")
async def test_journey_developer_registers_a_repository_and_scans_it(client, tmp_path: Path):
    """A real work tree → registered → scanned → its commits are readable.

    The repository is a genuine ``git init`` in a temp directory rather than a
    fixture row, because the whole feature is "read the Git history on this
    machine" and a synthetic repository would test the fake.
    """
    work_tree = tmp_path / "journey-repo"
    if not init_repo(work_tree):
        pytest.skip("git could not build a work tree in this environment")

    headers = authed(await register(client, username="devjourney"))

    response = await client.post(
        "/api/v1/developer/repositories",
        json={"local_path": str(work_tree), "name": "journey-repo"},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    repository_id = response.json()["id"]

    response = await client.post(
        f"/api/v1/developer/repositories/{repository_id}/scan", json={}, headers=headers
    )
    assert response.status_code == 200, response.text

    response = await client.get(
        f"/api/v1/developer/repositories/{repository_id}/commits",
        params={"limit": 10},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    rows = response.json()["items"]
    assert rows, "a scanned repository with one commit reported none"
    assert any("journey commit" in row["message"] for row in rows), rows

    response = await client.get("/api/v1/developer/summary", headers=headers)
    assert response.status_code == 200, response.text


async def test_journey_developer_rejects_a_path_outside_any_allowlist(client):
    """Registering a repository is a filesystem capability; it must be bounded.

    This is the security half of Journey 4, asserted here rather than in a
    security suite because it is the same journey a user walks.
    """
    headers = authed(await register(client, username="devpathjourney"))

    response = await client.post(
        "/api/v1/developer/repositories",
        json={"local_path": "../../etc", "name": "escape"},
        headers=headers,
    )
    assert response.status_code in (400, 404, 422), (
        f"a relative traversal was accepted ({response.status_code}); the path "
        "must be validated before any filesystem access"
    )


# ---------------------------------------------------------------------------
# Journey 5 — AI
# ---------------------------------------------------------------------------


@pytest.mark.ml_model
async def test_journey_ai_proposes_writes_and_reads_back_through_the_ordinary_api(
    client, loaded_classifier
):
    """Transcript → classifier → proposal → confirm → and the row is real.

    The last step is what makes this a journey rather than a unit test: the task
    the assistant created must come back through ``GET /tasks``, the same read
    the Tasks page uses. An assistant that reported success without writing, or
    wrote somewhere the app cannot see, would pass every other assertion here.

    **A known narrowing, not tested away.** The deterministic extractor strips a
    trailing type-noun from a recovered title, so "create a task called finish
    the rollout plan" yields "finish the rollout plan" — correct — while a title
    that itself ends in that noun, "create a task called finish the rollout task",
    yields "finish the rollout". The extractor's edge cases belong to
    ``tests/test_ml_actions.py``, which pins them deliberately; this journey uses
    an ordinary title because what it is pinning is the propose→confirm→read-back
    seam, and a contrived title would make it fail for a reason unrelated to
    that seam.
    """
    headers = authed(await register(client, username="aijourney"))

    response = await client.post(
        "/api/v1/projects", json={"name": "Assistant project"}, headers=headers
    )
    project_id = response.json()["id"]

    response = await client.post(
        "/api/v1/ml/route", json={"text": "show me my open tasks"}, headers=headers
    )
    assert response.status_code == 200, response.text
    decision = response.json()
    assert decision["intent"] == "task_manage", decision
    assert decision["target"]["service"] == "TaskService", (
        "the decision named a service that does not exist"
    )

    response = await client.post(
        "/api/v1/ml/action/propose",
        json={
            "text": "create a high priority task called finish the rollout plan",
            "project_id": project_id,
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["proposed"] is True, body
    proposal = body["proposal"]
    assert proposal["kind"] == "create_task"
    assert proposal["destructive"] is False
    assert proposal["requires_confirmation"] is True

    # Proposing must not have written anything.
    response = await client.get("/api/v1/tasks", params={"limit": 100}, headers=headers)
    assert all("rollout plan" not in row["title"].lower() for row in response.json()["items"]), (
        "proposing an action created the row — the whole safety property of "
        "this layer is that it does not"
    )

    response = await client.post(
        "/api/v1/ml/action/confirm",
        json={
            "kind": proposal["kind"],
            "intent": proposal["intent"],
            "payload": proposal["payload"],
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["outcome"] == "created", result
    assert result["applied"] is True

    # Matched on the id the assistant reported, which is authoritative, rather
    # than on the title — the deterministic extractor recovers the title from the
    # utterance and its exact casing is the extractor's business, not this
    # journey's.
    response = await client.get("/api/v1/tasks", params={"limit": 100}, headers=headers)
    created_row = next(
        (row for row in response.json()["items"] if row["id"] == result["entity_id"]), None
    )
    assert created_row is not None, (
        "the assistant reported a created task and the ordinary task list does "
        "not contain it — the write and the read disagree"
    )
    assert created_row["project_id"] == project_id, created_row
    assert "rollout plan" in created_row["title"].lower(), created_row


@pytest.mark.ml_model
async def test_journey_ai_refuses_a_destructive_request(client, loaded_classifier):
    """A request to delete everything must produce a refusal, not a proposal.

    The classifier cannot tell this from "add a task" — both are ``task_manage``
    — which is precisely why the action layer refuses on the verb rather than
    trusting the intent.
    """
    headers = authed(await register(client, username="aideljourney"))

    response = await client.post(
        "/api/v1/ml/action/propose",
        json={"text": "delete every project in my workspace"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["proposed"] is False, (
        "a destructive request produced an executable proposal; the classifier "
        "cannot distinguish it from a creation and the layer must refuse on the verb"
    )
    assert body["refusal"]["reason_code"] == "destructive_request", body


# ---------------------------------------------------------------------------
# Journey 6 — Voice
# ---------------------------------------------------------------------------


@pytest.mark.ml_model
async def test_journey_voice_transcript_reaches_the_one_classifier(client, loaded_classifier):
    """The server half of the voice journey.

    Speech recognition and synthesis are browser APIs with their own frontend
    coverage; this asserts the half the backend owns — that a transcript, shaped
    exactly as a recogniser delivers one (no terminal punctuation, lower case,
    the phrasing a person actually speaks), reaches the single model and comes
    back naming a real NEXUS service.
    """
    headers = authed(await register(client, username="voicejourney"))

    transcripts = {
        "mark the api contract task as done": ("task_manage", "TaskService"),
        "how much did i actually get done this week": ("analytics_insight", "AnalyticsService"),
        "what is at risk right now": ("risk_query", "RiskDetectionService"),
    }
    for transcript, (intent, service) in transcripts.items():
        response = await client.post("/api/v1/ml/route", json={"text": transcript}, headers=headers)
        assert response.status_code == 200, response.text
        decision = response.json()
        assert decision["intent"] == intent, f"{transcript!r} -> {decision}"
        assert decision["target"]["service"] == service, decision

    response = await client.get("/api/v1/ml/status", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["available"] is True
    assert body["model"]["base_model"] == "microsoft/deberta-v3-base"
    assert body["model"]["label_count"] == 14, "the model is not the trained one"


# ---------------------------------------------------------------------------
# Journey 7 — Command Center
# ---------------------------------------------------------------------------


async def test_journey_command_center_reads_are_consistent_and_tenant_scoped(client):
    """Every surface the Command Center composes, for one account, and no other's.

    The page is frontend code; what it needs from the backend is that all of its
    reads agree about the same user. A panel reading another account's rows is
    the failure this would catch.
    """
    headers = authed(await register(client, username="centre"))

    response = await client.post(
        "/api/v1/projects", json={"name": "Command Center fixture"}, headers=headers
    )
    project_id = response.json()["id"]
    await client.post(
        "/api/v1/tasks",
        json={
            "project_id": project_id,
            "title": "Overdue and visible",
            "priority": "critical",
            "due_date": (date.today() - timedelta(days=7)).isoformat(),
        },
        headers=headers,
    )
    await client.post(
        "/api/v1/intelligence/evaluate",
        json={"today": date.today().isoformat(), "window_days": 30},
        headers=headers,
    )
    await client.post(
        "/api/v1/learning/goals", json={"title": "Goal for the centre"}, headers=headers
    )

    reads = {
        "tasks": ("/api/v1/tasks", {"limit": 100}),
        "risks": ("/api/v1/risks", {}),
        "recommendations": ("/api/v1/recommendations", {}),
        "learning goals": ("/api/v1/learning/goals", {}),
        "analytics": ("/api/v1/analytics/overview", window()),
        "projects": ("/api/v1/projects", {"limit": 100}),
    }
    for label, (path, params) in reads.items():
        response = await client.get(path, params=params, headers=headers)
        assert response.status_code == 200, f"{label}: {response.text}"

    other = authed(await register(client, username="centre-other"))
    for label, (path, params) in reads.items():
        response = await client.get(path, params=params, headers=other)
        assert response.status_code == 200, f"{label}: {response.text}"
        raw = response.text
        assert "Command Center fixture" not in raw, f"{label} leaked another account's data"
        assert "Overdue and visible" not in raw, f"{label} leaked another account's data"


async def test_journey_command_center_search_finds_what_the_page_shows(client):
    """Global search must return the same rows the Command Center renders.

    Two surfaces reading the same tables and disagreeing is the seam this pins: a
    row visible in a panel but unfindable in search reads as data loss.
    """
    headers = authed(await register(client, username="ccsearch"))

    response = await client.post(
        "/api/v1/projects", json={"name": "Quasar telemetry"}, headers=headers
    )
    project_id = response.json()["id"]
    await client.post(
        "/api/v1/tasks",
        json={"project_id": project_id, "title": "Chart the quasar drift"},
        headers=headers,
    )

    response = await client.get("/api/v1/search", params={"q": "quasar"}, headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    titles = {hit["title"] for hit in body["hits"]}
    assert "Chart the quasar drift" in titles, body
    assert any(hit["kind"] == "project" for hit in body["hits"]), body

    other = authed(await register(client, username="ccsearch-other"))
    response = await client.get("/api/v1/search", params={"q": "quasar"}, headers=other)
    assert response.json()["hits"] == [], "search leaked another account's rows"
