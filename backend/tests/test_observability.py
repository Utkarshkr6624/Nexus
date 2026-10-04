"""§14.16 — every operation NEXUS can fail at has to be findable in the log.

An operator debugging a running NEXUS has exactly one tool: the log. This file is
the contract that the categories below each leave a record, and it is written as
*drive the operation and search the captured records* rather than *read the source
and check a string is present* — because the question is never "was a call
written", it is "would someone grepping for this find out what happened".

The categories
--------------

===============================  ==============================================
authentication failures          ``auth_login_failed``
API errors                       ``domain_error`` / ``http_exception`` /
                                 ``unhandled_exception``
database errors                  ``database_probe`` and ``unhandled_exception``
ML inference failures            ``ml.inference_failed``
assistant action refusals        ``ml_action_proposed`` with ``proposed=false``
Git scan failures                ``git_scan_failed``
background jobs                  ``analytics_rebuild_*`` and ``risk_detection_*``
===============================  ==============================================

Four of these were silent before this file existed. The analytics rebuild wrote
no line at all, so a window that was never recomputed and a window that was
recomputed cleanly looked identical. The risk detection pass wrote no line either,
and that one *resolves* risks — so a half-finished pass is one that closed live
rows and was never recorded as having run. A failed git scan stored a row the user
could see on one repository but emitted nothing an operator could correlate, and a
refused sign-in left only an audit row that a per-account history cannot show
arriving in a hundred at once. Those four gained records; the two background jobs
gained a ``_started`` / ``_completed`` / ``_failed`` triplet each.

The negative half
-----------------

Every one of these lines must also be *safe*. The final group asserts that a real
sign-in and a real password-change attempt leave no plaintext secret in any
record, using a canary that appears nowhere else in the system, so finding it in a
record is evidence rather than coincidence.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

import pytest
from httpx import AsyncClient

from app.api.deps import get_authenticated_user
from app.core.config import BACKEND_ROOT, get_settings
from app.core.deps import get_current_user
from app.ml.classifier import IntentClassifier
from app.ml.runtime import MLRuntime
from app.ml.schemas import IntentPrediction, ModelIdentity
from app.models.user import User
from app.services.developer.git import run_git
from ml.datasets.routing import label_map
from tests.analytics_fixtures import bearer, register_via_api, sign_in, user_by_email

#: The events this file asserts. Kept as data so a rename in the application is a
#: failing assertion here rather than a silently unobservable event.
EXPECTED_EVENTS = frozenset(
    {
        "auth_login_failed",
        "domain_error",
        "unhandled_exception",
        "database_probe",
        "ml.inference_failed",
        "ml_action_proposed",
        "git_scan_failed",
        "analytics_rebuild_started",
        "analytics_rebuild_completed",
        "analytics_rebuild_failed",
        "risk_detection_started",
        "risk_detection_completed",
        "risk_detection_failed",
    }
)

#: Appears only as a password in the requests below.
SECRET_CANARY = "correcthorsebatterystaple"

#: A day inside the analytics ceiling, so a rebuild window is a one-day one.
_REBUILD_DAY = date(2026, 3, 16)

#: The training window a stand-in identity reports, matching the checkpoint's.
_TRAINED_WINDOW = 128


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _events(caplog, name: str) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.getMessage() == name]


def _one(caplog, name: str) -> logging.LogRecord:
    matches = _events(caplog, name)
    assert len(matches) == 1, (
        f"expected one {name!r}, saw {sorted({r.getMessage() for r in caplog.records})}"
    )
    return matches[0]


def _rendered(record: logging.LogRecord) -> str:
    return " ".join(f"{key}={value}" for key, value in record.__dict__.items())


def _structured(record: logging.LogRecord) -> str:
    """The record's own fields, without the traceback it carries.

    ``exc_info`` is a tuple whose repr contains the whole formatted traceback,
    exception message included — which is the point: the detail *is* kept
    server-side. So an assertion about "what a log reader sees as a field" has to
    exclude it, or it is asserting the opposite of the contract.
    """
    return " ".join(
        f"{key}={value}"
        for key, value in record.__dict__.items()
        if key not in ("exc_info", "exc_text", "stack_info")
    )


def _everything(caplog) -> str:
    return "".join(_rendered(record) for record in caplog.records)


async def _account(client, *, username: str = "ada") -> tuple[dict[str, str], str]:
    """Register one account and return its bearer headers and email."""
    email = f"{username}@nexus.test"
    await register_via_api(client, username=username, email=email)
    tokens = await sign_in(client, email=email)
    return bearer(tokens["access_token"]), email


class _ExplodingWeights:
    """A ``LoadedModel`` stand-in whose forward pass is a hard failure."""

    max_sequence_length = _TRAINED_WINDOW
    id2label = ("unreachable",)

    def __getattr__(self, name: str):
        raise RuntimeError(f"inference reached the model (wanted {name!r})")


class _StubClassifier:
    """A classifier stand-in for the proposal route's happy and refusal paths."""

    def __init__(self, prediction: IntentPrediction, identity: ModelIdentity | None) -> None:
        self._prediction = prediction
        self._identity = identity

    @property
    def identity(self) -> ModelIdentity | None:
        return self._identity

    def predict(self, text: str) -> IntentPrediction:
        return self._prediction


def _identity() -> ModelIdentity:
    return ModelIdentity(
        base_model="microsoft/deberta-v3-base",
        architecture="DebertaV2ForSequenceClassification",
        device="cpu",
        label_count=len(label_map()),
        max_sequence_length=_TRAINED_WINDOW,
        parameter_count=184_432_910,
        checkpoint="<stand-in>",
        load_seconds=5.25,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def authorised_ml_client(app, offline_client) -> Iterator[AsyncClient]:
    """An offline client whose identity resolves and whose permission gate is real."""
    caller = User(username="ada", email="ada@nexus.dev", role="user", is_active=True)
    app.dependency_overrides[get_current_user] = lambda: caller
    app.dependency_overrides[get_authenticated_user] = lambda: caller
    try:
        yield offline_client
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_authenticated_user, None)


@pytest.fixture
def installed_runtime(app, settings):
    """Install a runtime on ``app.state`` and hand back the installer."""

    def _install(classifier) -> MLRuntime:
        runtime = MLRuntime(settings, classifier=classifier)
        app.state.ml_runtime = runtime
        return runtime

    yield _install
    app.state.ml_runtime = None


@pytest.fixture
def serving_ml_runtime(installed_runtime) -> Iterator[MLRuntime]:
    """A runtime that answers, backed by a fixed prediction."""
    yield installed_runtime(
        _StubClassifier(
            IntentPrediction(
                intent="task_manage",
                confidence=0.987_654,
                alternatives=(("project_manage", 0.006),),
                truncated=False,
                latency_ms=41.5,
            ),
            _identity(),
        )
    )


@pytest.fixture
def exploding_ml_runtime(installed_runtime) -> Iterator[MLRuntime]:
    """A runtime whose model raises the instant inference is attempted."""
    yield installed_runtime(IntentClassifier(_ExplodingWeights()))  # type: ignore[arg-type]


@pytest.fixture
async def git_repository(tmp_path) -> Path:
    """An empty ``main``-branched repository in ``tmp_path``.

    ``git init`` with no commits is a valid repository and registers fine, which
    is what makes deleting it afterwards the cleanest way to produce a scan
    failure that is a real filesystem condition rather than a mocked one.
    """
    root = tmp_path / "worktree"
    await run_git(tmp_path, "init", "-b", "main", str(root))
    await run_git(root, "config", "user.email", "test@example.invalid")
    await run_git(root, "config", "user.name", "Test Person")
    return root


# ---------------------------------------------------------------------------
# The contract table is real
# ---------------------------------------------------------------------------


def test_every_expected_event_is_emitted_somewhere_in_the_application():
    """The names above are not aspirational.

    The behavioural tests below prove a subset actually *runs*; this one proves
    none of the names was quietly renamed out from under them.
    """
    found: set[str] = set()
    for path in (BACKEND_ROOT / "app").rglob("*.py"):
        for line in path.read_text(encoding="utf-8").splitlines():
            for name in EXPECTED_EVENTS:
                if f'"{name}"' in line:
                    found.add(name)

    assert found >= EXPECTED_EVENTS, sorted(EXPECTED_EVENTS - found)


# ---------------------------------------------------------------------------
# Authentication failures
# ---------------------------------------------------------------------------


async def test_a_failed_sign_in_is_logged_with_its_reason(client, db_session, caplog):
    """Two refusals, one response, two distinguishable log records.

    The responses must stay byte-identical — probing cannot tell "unknown email"
    from "wrong password" — so the log is the only place the two are separable,
    which is exactly why both are written there.
    """
    caplog.set_level(logging.INFO)
    _headers, email = await _account(client)

    wrong = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "not-the-password"}
    )
    unknown = await client.post(
        "/api/v1/auth/login", json={"email": "nobody@nexus.test", "password": "not-the-password"}
    )

    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["error"]["message"] == unknown.json()["error"]["message"]

    failures = _events(caplog, "auth_login_failed")
    assert len(failures) == 2
    assert {record.reason for record in failures} == {"invalid_credentials"}


async def test_an_inactive_account_is_logged_under_its_own_reason(client, db_session, caplog):
    """The second reason is reachable, not just declared."""
    caplog.set_level(logging.INFO)
    _headers, email = await _account(client)
    from tests.analytics_fixtures import PASSWORD

    owner = await user_by_email(db_session, email)
    owner.is_active = False
    await db_session.commit()

    response = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})

    assert response.status_code == 401
    assert _one(caplog, "auth_login_failed").reason == "inactive"


async def test_a_failed_sign_in_never_writes_the_submitted_password(client, db_session, caplog):
    caplog.set_level(logging.INFO)
    _headers, email = await _account(client)

    await client.post(
        "/api/v1/auth/login", json={"email": email, "password": f"wrong-{SECRET_CANARY}"}
    )

    assert SECRET_CANARY not in _everything(caplog)


# ---------------------------------------------------------------------------
# API errors
# ---------------------------------------------------------------------------


async def test_a_domain_error_is_logged_with_its_code_and_path(offline_client, caplog):
    caplog.set_level(logging.INFO)

    await offline_client.get("/api/v1/tasks", headers={"Authorization": "Bearer nonsense"})

    record = _one(caplog, "domain_error")
    assert record.status_code == 401
    assert record.path == "/api/v1/tasks"


async def test_a_request_validation_failure_is_logged(non_raising_client, caplog):
    caplog.set_level(logging.INFO)

    await non_raising_client.post("/api/v1/auth/login", json={"email": "not-an-email"})

    record = _one(caplog, "request_validation_failed")
    assert record.status_code == 422
    assert record.path == "/api/v1/auth/login"


async def test_an_unhandled_exception_is_logged_with_its_type_not_its_message(
    non_raising_client, caplog, monkeypatch
):
    """The catch-all handler is the last line of observability; it must fire.

    The message stays in the traceback, reachable by ``request_id``, and out of
    the structured fields — a 500 whose detail is application text about a
    failure can carry a query fragment or a path.
    """
    caplog.set_level(logging.INFO)

    from app.api.v1 import health

    async def explode() -> None:
        raise RuntimeError("a deliberate failure for the observability test")

    # Patched on the module the handler *calls*, not on the handler: a route is
    # bound to its function at registration, so replacing the module attribute
    # would leave the registered handler untouched and this would pass vacuously.
    monkeypatch.setattr(health, "check_database_connection", explode)

    response = await non_raising_client.get("/api/v1/health")

    assert response.status_code == 500
    # The message is in the traceback, server-side, and in neither the body nor
    # any structured field a reader would parse out of the line.
    assert "a deliberate failure" not in response.text
    record = _one(caplog, "unhandled_exception")
    assert record.exception_type == "RuntimeError"
    assert record.exc_info is not None
    assert "a deliberate failure" not in _structured(record)


# ---------------------------------------------------------------------------
# Database errors
# ---------------------------------------------------------------------------


async def test_a_database_failure_inside_a_request_is_recorded_with_its_type(
    engine, truncated_database, non_raising_client, caplog, monkeypatch
):
    """The same failure, from an authenticated request that reaches the database."""
    caplog.set_level(logging.INFO)
    headers, _email = await _account(non_raising_client)

    from sqlalchemy.exc import OperationalError

    from app.repositories.task import TaskRepository

    async def unreachable(*args, **kwargs):
        raise OperationalError("SELECT tasks", {}, Exception("could not connect to server"))

    monkeypatch.setattr(TaskRepository, "list_for_user", unreachable, raising=False)

    response = await non_raising_client.get("/api/v1/tasks", headers=headers)

    assert response.status_code == 500, response.text
    assert "could not connect to server" not in response.text
    record = _one(caplog, "unhandled_exception")
    assert record.exception_type == "OperationalError"
    assert record.exc_info is not None
    assert "could not connect to server" not in _structured(record)


def test_the_startup_database_probe_reports_a_verdict():
    """``database_probe`` is emitted by the lifespan from a boolean.

    ``check_database_connection`` swallows every exception on purpose — the
    boolean must not raise — so the log line is the only place the boot-time
    verdict reaches an operator. Asserting the call shape keeps the two halves
    from drifting apart. This is the one assertion in the file that reads source
    rather than driving the operation, and the reason is stated rather than
    hidden: ``ASGITransport`` never runs a lifespan, so there is no cheap way to
    observe a startup line from a request.
    """
    import app.main as main

    source = Path(main.__file__).read_text(encoding="utf-8")
    assert '"database_probe"' in source
    assert "check_database_connection()" in source


async def test_the_database_probe_answers_a_verdict_and_never_raises():
    """The behaviour behind that line, driven for real.

    A probe that raised would take the whole boot with it; a probe that hung
    would stall startup until the OS TCP timeout. Both are the reason the
    function swallows and bounds its own work, and both are what make the
    ``database_probe`` log line worth reading.
    """
    from app.db.session import check_database_connection

    assert await check_database_connection() in (True, False)


# ---------------------------------------------------------------------------
# ML inference failures
# ---------------------------------------------------------------------------


async def test_an_ml_inference_failure_is_logged_with_its_exception_type(
    authorised_ml_client, exploding_ml_runtime, caplog
):
    caplog.set_level(logging.INFO)

    response = await authorised_ml_client.post("/api/v1/ml/route", json={"text": "add a task"})

    assert response.status_code == 500
    record = _one(caplog, "ml.inference_failed")
    assert record.error == "RuntimeError"
    assert record.characters == len("add a task")
    assert record.label_count == 1


async def test_an_ml_runtime_that_will_not_load_is_logged(caplog, make_settings, tmp_path):
    """The deployment-side ML failure, as the runtime records it."""
    from app.ml.runtime import MLRuntime as Runtime

    runtime = Runtime(make_settings(ML_MODEL_PATH=str(tmp_path / "never-trained")))
    try:
        status = runtime.load()
    finally:
        runtime.shutdown()

    assert status.available is False
    records = _events(caplog, "ml_runtime_unavailable")
    assert records, sorted({record.getMessage() for record in caplog.records})
    assert records[-1].reason == Runtime.REASON_CHECKPOINT_MISSING


# ---------------------------------------------------------------------------
# Assistant action refusals
# ---------------------------------------------------------------------------


async def test_a_refused_action_is_logged_with_its_reason_code(
    authorised_ml_client, serving_ml_runtime, caplog
):
    """A refusal is a 200 with ``proposed: false``, so the log is where it is visible.

    Without a line, "NEXUS declined" and "the request never arrived" are the same
    absence, which is the flat refusal the proposal surface was written to end.
    """
    caplog.set_level(logging.INFO)

    response = await authorised_ml_client.post(
        "/api/v1/ml/action/propose",
        json={"text": "delete the whole project and everything in it"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["proposed"] is False
    record = _one(caplog, "ml_action_proposed")
    assert record.proposed is False
    assert record.reason_code


async def test_a_proposed_action_is_logged_as_a_proposal(app, client, db_session, caplog):
    """The positive control: the event distinguishes the two outcomes.

    Needs a real account and a real project, because ``create_task`` refuses
    without one — ``context_missing`` is a legitimate refusal, not a bug, so a
    control that omitted it would be asserting against the refusal path.
    """
    caplog.set_level(logging.INFO)
    headers, _email = await _account(client)
    project = await client.post("/api/v1/projects", json={"name": "Atlas"}, headers=headers)
    assert project.status_code in (200, 201), project.text

    runtime = _StubClassifier(
        IntentPrediction(
            intent="task_manage",
            confidence=0.987_654,
            alternatives=(("project_manage", 0.006),),
            truncated=False,
            latency_ms=41.5,
        ),
        _identity(),
    )
    installed = MLRuntime(get_settings(), classifier=runtime)
    app.state.ml_runtime = installed
    try:
        response = await client.post(
            "/api/v1/ml/action/propose",
            json={
                "text": "add a task to draft the migration plan",
                "project_id": project.json()["id"],
            },
            headers=headers,
        )
    finally:
        app.state.ml_runtime = None

    assert response.status_code == 200, response.text
    assert response.json()["proposed"] is True
    record = _one(caplog, "ml_action_proposed")
    assert record.proposed is True
    assert record.reason_code is None


# ---------------------------------------------------------------------------
# Git scan failures
# ---------------------------------------------------------------------------


async def _register_repository(client, headers, path: Path) -> str:
    """Register a local repository through the real endpoint.

    ``/api/v1/developer/repositories`` rather than a nested project route: the
    project is a payload field, and a registration that needs a project to
    exist first would be testing a different surface.
    """
    repository = await client.post(
        "/api/v1/developer/repositories",
        json={"name": "local", "local_path": str(path)},
        headers=headers,
    )
    assert repository.status_code == 201, repository.text
    return repository.json()["id"]


async def test_a_git_scan_that_cannot_run_is_logged(client, db_session, caplog, git_repository):
    """Registered while it was a repository, scanned after it stopped being one.

    That is the ordinary failure — the directory was moved, the volume was
    unmounted, the path was a symlink that now resolves elsewhere — and it is the
    one a stored path produces with no warning.
    """
    caplog.set_level(logging.INFO)
    headers, _email = await _account(client)
    repository_id = await _register_repository(client, headers, git_repository)

    import shutil

    shutil.rmtree(git_repository)

    scan = await client.post(
        f"/api/v1/developer/repositories/{repository_id}/scan", headers=headers
    )

    assert scan.status_code == 200, scan.text
    assert scan.json()["status"] == "error"
    record = _one(caplog, "git_scan_failed")
    assert record.repository_id == repository_id
    assert record.error
    assert record.duration_ms >= 0


async def test_a_successful_scan_does_not_report_a_failure(
    client, db_session, caplog, git_repository
):
    """The positive control: the line is emitted on failure only."""
    caplog.set_level(logging.INFO)
    headers, _email = await _account(client)
    repository_id = await _register_repository(client, headers, git_repository)

    await client.post(f"/api/v1/developer/repositories/{repository_id}/scan", headers=headers)

    assert not _events(caplog, "git_scan_failed")


async def test_a_git_scan_failure_does_not_log_the_local_path(
    client, db_session, caplog, git_repository
):
    caplog.set_level(logging.INFO)
    headers, _email = await _account(client)
    repository_id = await _register_repository(client, headers, git_repository)

    import shutil

    shutil.rmtree(git_repository)
    await client.post(f"/api/v1/developer/repositories/{repository_id}/scan", headers=headers)

    assert git_repository.name not in _everything(caplog)


# ---------------------------------------------------------------------------
# Background job: the analytics rebuild
# ---------------------------------------------------------------------------


async def test_the_analytics_rebuild_brackets_its_run(client, db_session, caplog):
    caplog.set_level(logging.INFO)
    headers, _email = await _account(client)

    response = await client.post(
        "/api/v1/analytics/rebuild",
        params={"start_date": _REBUILD_DAY.isoformat(), "end_date": _REBUILD_DAY.isoformat()},
        headers=headers,
    )

    assert response.status_code == 202, response.text
    started = _one(caplog, "analytics_rebuild_started")
    completed = _one(caplog, "analytics_rebuild_completed")
    assert started.start == _REBUILD_DAY.isoformat()
    assert started.end == _REBUILD_DAY.isoformat()
    assert started.days == 1
    assert completed.rows_written == 1
    assert completed.elapsed_ms >= 0
    assert not _events(caplog, "analytics_rebuild_failed")


async def test_a_rebuild_that_fails_is_logged_as_a_failure(
    engine, truncated_database, non_raising_client, caplog, monkeypatch
):
    """The 500 path, with the traceback kept server-side.

    ``non_raising_client`` because ``ASGITransport`` re-raises by default, which
    would hide the very response whose body the assertion checks. ``engine`` is in
    the signature so the database the route reads is the configured one.
    """
    caplog.set_level(logging.INFO)
    headers, _email = await _account(non_raising_client)

    from app.repositories.analytics import AnalyticsRepository

    async def explode(*args, **kwargs):
        raise RuntimeError("a deliberate rebuild failure")

    monkeypatch.setattr(AnalyticsRepository, "upsert_many", explode)

    response = await non_raising_client.post(
        "/api/v1/analytics/rebuild",
        params={"start_date": _REBUILD_DAY.isoformat(), "end_date": _REBUILD_DAY.isoformat()},
        headers=headers,
    )

    assert response.status_code == 500, response.text
    failed = _one(caplog, "analytics_rebuild_failed")
    assert failed.exc_info
    assert failed.start == _REBUILD_DAY.isoformat()
    assert "a deliberate rebuild failure" not in response.text
    assert "a deliberate rebuild failure" not in _structured(failed)


async def test_a_rebuild_refused_for_a_wide_window_is_not_a_failed_job(client, db_session, caplog):
    """A caller's 422 is not a job that failed.

    ``_check_range`` sits outside the logging wrapper deliberately; this is the
    test that goes red if it were moved inside, which would report every
    mistyped window as a background-job incident.
    """
    caplog.set_level(logging.INFO)
    headers, _email = await _account(client)

    response = await client.post(
        "/api/v1/analytics/rebuild",
        params={
            "start_date": (_REBUILD_DAY - timedelta(days=5000)).isoformat(),
            "end_date": _REBUILD_DAY.isoformat(),
        },
        headers=headers,
    )

    assert response.status_code == 422, response.text
    assert not _events(caplog, "analytics_rebuild_failed")
    assert not _events(caplog, "analytics_rebuild_started")


# ---------------------------------------------------------------------------
# Background job: the risk detection pass
# ---------------------------------------------------------------------------


async def test_the_risk_detection_pass_brackets_its_run(client, db_session, caplog):
    caplog.set_level(logging.INFO)
    headers, _email = await _account(client)

    response = await client.post("/api/v1/intelligence/evaluate", headers=headers)

    assert response.status_code == 200, response.text
    started = _one(caplog, "risk_detection_started")
    completed = _one(caplog, "risk_detection_completed")
    assert started.window_days == 14
    assert completed.risks_found >= 0
    assert completed.risks_created >= 0
    assert completed.duration_ms >= 0
    assert not _events(caplog, "risk_detection_failed")


async def test_a_detection_pass_that_fails_is_logged_as_a_failure(
    engine, truncated_database, non_raising_client, caplog, monkeypatch
):
    caplog.set_level(logging.INFO)
    headers, _email = await _account(non_raising_client)

    from app.repositories.analytics import AnalyticsRepository

    async def explode(*args, **kwargs):
        raise RuntimeError("a deliberate detection failure")

    monkeypatch.setattr(AnalyticsRepository, "activity_days_in_range", explode)

    response = await non_raising_client.post("/api/v1/intelligence/evaluate", headers=headers)

    assert response.status_code == 500, response.text
    failed = _one(caplog, "risk_detection_failed")
    assert failed.exc_info
    assert failed.window_days == 14
    assert "a deliberate detection failure" not in response.text
    assert "a deliberate detection failure" not in _structured(failed)


async def test_a_detection_pass_refused_for_a_wide_window_is_not_a_failed_job(
    client, db_session, caplog
):
    caplog.set_level(logging.INFO)
    headers, _email = await _account(client)

    response = await client.post("/api/v1/intelligence/evaluate?window_days=5000", headers=headers)

    assert response.status_code == 422, response.text
    assert not _events(caplog, "risk_detection_failed")
    assert not _events(caplog, "risk_detection_started")


# ---------------------------------------------------------------------------
# Secrets never reach a log line
# ---------------------------------------------------------------------------


async def test_a_successful_sign_in_writes_no_password_to_any_record(client, db_session, caplog):
    caplog.set_level(logging.INFO)
    _headers, email = await _account(client)

    from tests.analytics_fixtures import PASSWORD

    response = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})

    assert response.status_code == 200
    assert PASSWORD not in _everything(caplog)


async def test_a_password_change_attempt_leaves_no_plaintext_secret_in_any_record(
    client, db_session, caplog
):
    """``REDACTED_KEYS`` exists; this is it exercised on real traffic.

    The payload carries ``current_password`` and ``new_password`` — the two keys
    most likely to reach a log line through a well-meaning extra field on a
    ``log_event`` call — plus a canary so a leak would be unambiguous.
    """
    caplog.set_level(logging.INFO)
    _headers, email = await _account(client)
    tokens = await sign_in(client, email=email)

    response = await client.patch(
        "/api/v1/auth/password",
        json={
            "current_password": f"wrong-{SECRET_CANARY}",
            "new_password": f"Nyx-{SECRET_CANARY}!9",
        },
        headers=bearer(tokens["access_token"]),
    )

    assert response.status_code in (400, 401, 422), response.text
    assert SECRET_CANARY not in _everything(caplog)


def test_the_redacted_key_set_covers_the_keys_the_auth_payloads_actually_use():
    """A key nobody logs is a key nobody had to redact.

    Read out of ``app/schemas/user.py`` and ``app/schemas/security.py`` rather
    than restated, so renaming a field there without adding it here is a failing
    assertion rather than a silent leak.
    """
    import app.schemas.security as security
    import app.schemas.user as user
    from app.core.logging import REDACTED_KEYS

    sources = Path(security.__file__).read_text(encoding="utf-8") + Path(user.__file__).read_text(
        encoding="utf-8"
    )
    for field in ("password", "current_password", "new_password"):
        assert field in sources, field
        assert field in REDACTED_KEYS, field


def test_redaction_actually_replaces_the_value():
    """The function, not the list.

    Without this, a regression that turned ``redact`` into a pass-through would
    leave every secret test above green while writing every secret to disk.
    """
    from app.core.logging import redact

    assert redact({"password": SECRET_CANARY})["password"] != SECRET_CANARY
    assert redact({"nested": {"api_key": SECRET_CANARY}})["nested"]["api_key"] != SECRET_CANARY
    assert redact([{"token": SECRET_CANARY}])[0]["token"] != SECRET_CANARY
    # A field that is not a secret is untouched, or the redaction is useless.
    assert redact({"project_name": "atlas"})["project_name"] == "atlas"


def test_the_redacted_marker_is_what_a_redacted_value_becomes():
    from app.core.logging import _REDACTED, redact

    assert redact({"password": SECRET_CANARY})["password"] == _REDACTED
