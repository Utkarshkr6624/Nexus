"""The Phase 3 API surface end to end: reachable states, agreeing reads, honest deletes.

Phases 3, 4 and 5 shipped with essentially no coverage of the work-management
surface — ``task_service``, ``project_service`` and ``tag_service`` were
imported by no test file at all. That is the largest single risk in the
repository before Phase 10 trains models on this data, because every one of the
defects below is a *silent* one: nothing errors, nothing 500s, and the only
symptom is a number that reads plausible and is wrong.

What this file pins
-------------------

**1. The states that must be reachable over HTTP.** A status listed in a
legality table that no route can walk is a description, not a rule. Both
``TaskStatus.CANCELLED`` and ``ProjectStatus.ON_HOLD`` were exactly that: their
edges existed in ``_LEGAL_TRANSITIONS`` from the first commit, ``set_status``
was called only with ``COMPLETED``, and the corresponding stats buckets read a
hard zero for every account in the repository. Zero there is indistinguishable
from a user who never abandons work or never pauses a project, which is the
most expensive kind of wrong a metrics table can carry into training. The tests
below drive the new ``/cancel``, ``/hold``, ``/resume`` and ``/activate`` doors
and then read the buckets back.

**2. One UTC day.** Every stored timestamp is timezone-aware UTC, so "today"
has to be the UTC day. It was ``date.today()`` — the *host's* calendar — in the
service's overdue window and in both response models. On this +05:30 host the
two disagree for five and a half hours a day: a task due today is reported
overdue while the ``created_at`` on the same row says yesterday, and the badge
on a card disagrees with the total beside it. The tests derive "today" from the
database's own ``now()``, and one of them replaces the schema module's fallback
clock with one thirty days out to prove the served answer cannot be moved by a
clock that is not the database's.

**3. A read model that says "did not measure" as a measured zero.**
``GET /tasks/{id}`` answered ``has_blocked_dependencies: false`` and
``tag_ids: []`` for a task that was blocked and tagged, while ``GET /tasks``
answered the truth for the identical row in the identical request. Likewise
``GET /tags/{id}`` answered ``task_count: 0`` for a tag used on a task.
A confident zero is worse than a null here: the client cannot tell it from
"this is unused", so it deletes the label and drops the card off the board.

**4. Cascade destruction, now documented at the boundary.** ``DELETE /tasks/{id}``
and ``DELETE /projects/{id}`` take ``work_sessions`` and ``calendar_events``
with them — every tracked minute — silently. The planner model is not this
file's to change, so the tests below *pin the consequence*, which makes the
new docstrings tested claims rather than prose, and the recommendation is
written up alongside the routers and the services.

House style, deliberately
-------------------------
Follows ``tests/test_developer_service.py`` and ``tests/test_task_integrity.py``:

* ``pytestmark = pytest.mark.integration`` — every test here needs the live
  PostgreSQL the suite truncates between tests.
* Every row that goes in through the wire goes in through the **real HTTP API**
  rather than straight into the tables, because three of the four defects are
  properties of *which route shape reaches the service*. Seeding a ``cancelled``
  row directly would test nothing at all.
* Rows that must be read back are read through an **explicit column projection**,
  never an ORM entity: this is the same session that wrote them, so an entity
  read would return whatever the identity map cached and a "the delete
  cascaded" assertion would compare a stale object with itself.
* The clock is read from the database through ``func.now()``, never from
  ``datetime.now()`` — the whole point of these tests is that the two are not
  the same thing.
* Every expected figure is derived in the test's own docstring rather than
  recorded from a run.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import app.schemas.task as task_schema
from app.models.activity import ActivityLog
from app.models.enums import ActivityEvent
from app.models.planner import CalendarEvent, WorkSession
from app.models.tag import Tag
from app.repositories.project import ProjectRepository
from app.repositories.tag import TagRepository
from app.repositories.task import TaskRepository
from app.services.activity_service import ActivityService
from app.services.task_service import TaskService
from tests.analytics_fixtures import seeded_client

pytestmark = pytest.mark.integration


def _service(session: AsyncSession) -> TaskService:
    """Mirror :func:`app.api.deps.get_task_service` over one session.

    Hand-wired rather than reached through the app so a test can ask the service
    a question the wire does not expose — specifically :meth:`TaskService._today`,
    the single seam every "today" on this service resolves through. The activity
    sink is the real one; a ``None`` sink would make any event assertion
    vacuous.
    """
    return TaskService(
        TaskRepository(session),
        ProjectRepository(session),
        TagRepository(session),
        activity=ActivityService(session),
    )


async def _db_today(session: AsyncSession) -> date:
    """The database's own UTC day — the truth every "today" must agree with."""
    now = await session.scalar(select(func.now()))
    return now.astimezone(UTC).date()


async def _new_project(client, auth, name: str = "Phase 3") -> str:
    """Create one project through the API and return its id."""
    response = await client.post("/api/v1/projects", json={"name": name}, headers=auth)
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _new_task(client, auth, project_id: str, title: str, **extra) -> str:
    """Create one task through the API and return its id."""
    response = await client.post(
        "/api/v1/tasks",
        json={"project_id": project_id, "title": title, **extra},
        headers=auth,
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _task_stats(client, auth) -> dict:
    """The ``tasks`` half of ``GET /activity/stats`` — every bucket, always present."""
    response = await client.get("/api/v1/activity/stats", headers=auth)
    assert response.status_code == 200, response.text
    return response.json()["tasks"]


async def _project_stats(client, auth) -> dict:
    """The ``projects`` half of ``GET /activity/stats``."""
    response = await client.get("/api/v1/activity/stats", headers=auth)
    assert response.status_code == 200, response.text
    return response.json()["projects"]


async def _row_count(session: AsyncSession, model: type) -> int:
    """Count every row of ``model``.

    An explicit column projection rather than ORM entities: this is the same
    session that wrote the rows, so an entity read would hand back whatever the
    identity map cached and a "the cascade fired" assertion would compare a
    stale object with itself and pass for the wrong reason.
    """
    return await session.scalar(select(func.count()).select_from(model))


# ---------------------------------------------------------------------------
# 1. Unreachable states
# ---------------------------------------------------------------------------


async def test_a_task_can_be_cancelled_over_http_and_the_cancelled_bucket_then_counts_it(
    client, db_session
):
    """``cancelled`` is reachable over HTTP and stops reading as a hard zero.

    Before the fix ``TaskStatus.CANCELLED`` had no route of any shape, so no
    account in the repository could hold one and ``TaskStats.cancelled`` was
    structurally ``0`` — the same figure a user who never abandons a task
    produces. One task, one cancel: the stats must move from
    ``cancelled=0, todo=1, total=1`` to ``cancelled=1, todo=0, total=1``, and the
    buckets must still be a partition, so the cancelled card is counted once and
    nowhere else.

    ``completed_at`` must stay ``None``. Cancelling is not finishing, and a row
    that claimed otherwise would let a dropped card pass as delivered work.
    """
    _seed, auth = await seeded_client(client, db_session)
    project = await _new_project(client, auth)
    task_id = await _new_task(client, auth, project, "Abandon the migration")

    before = await _task_stats(client, auth)
    assert (before["cancelled"], before["total"], before["todo"]) == (0, 1, 1), before

    cancelled = await client.post(f"/api/v1/tasks/{task_id}/cancel", headers=auth)
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["completed_at"] is None

    after = await _task_stats(client, auth)
    assert (after["cancelled"], after["total"]) == (1, 1), after
    assert (after["todo"], after["completed"]) == (0, 0), after

    reread = await client.get(f"/api/v1/tasks/{task_id}", headers=auth)
    assert reread.status_code == 200, reread.text
    assert reread.json()["status"] == "cancelled"
    assert reread.json()["completed_at"] is None

    again = await client.post(f"/api/v1/tasks/{task_id}/cancel", headers=auth)
    assert again.status_code == 200, again.text
    assert (await _task_stats(client, auth))["cancelled"] == 1


async def test_a_cancelled_task_cannot_be_reopened_because_cancelled_is_terminal(
    client, db_session, assert_error_envelope
):
    """The terminal edge of the lifecycle stays refused after cancel is wired.

    ``CANCELLED`` has an empty outgoing set in ``_LEGAL_TRANSITIONS`` for a
    stated reason: work deliberately abandoned is not work that failed, and
    letting it be re-opened would fold "dropped" back into the backlog the
    status was invented to keep separate. Opening the door *into* the state must
    not have opened one out of it.

    The refusal is a 422 naming the reason, and the card stays cancelled — the
    point of a terminal state is that the refusal cannot be routed around.
    """
    _seed, auth = await seeded_client(client, db_session)
    project = await _new_project(client, auth)
    task_id = await _new_task(client, auth, project, "Abandon the migration")
    assert (await client.post(f"/api/v1/tasks/{task_id}/cancel", headers=auth)).status_code == 200

    reopened = await client.post(f"/api/v1/tasks/{task_id}/reopen", headers=auth)
    error = assert_error_envelope(reopened, status_code=422, code="validation_error")
    assert "cancelled" in error["message"], error

    after = await client.get(f"/api/v1/tasks/{task_id}", headers=auth)
    assert after.json()["status"] == "cancelled"
    assert (await _task_stats(client, auth))["cancelled"] == 1


async def test_a_completed_task_cannot_be_cancelled_because_the_work_happened(
    client, db_session, assert_error_envelope
):
    """Cancelling a completed task is refused, not quietly accepted.

    ``COMPLETED`` does not list ``CANCELLED`` among its legal edges, and the
    reason is that the work happened. Relabelling a finished card as abandoned
    would erase a fact rather than record one; a completed task the user no
    longer wants is *deleted* — which is a decision the user makes by asking for
    destruction rather than by clicking "cancel".

    The error must name the illegal edge and the states that were available
    instead, so a client can render a reason rather than a bare status code.
    """
    _seed, auth = await seeded_client(client, db_session)
    project = await _new_project(client, auth)
    task_id = await _new_task(client, auth, project, "Ship the thing")
    assert (await client.post(f"/api/v1/tasks/{task_id}/start", headers=auth)).status_code == 200
    assert (await client.post(f"/api/v1/tasks/{task_id}/complete", headers=auth)).status_code == 200

    refused = await client.post(f"/api/v1/tasks/{task_id}/cancel", headers=auth)
    error = assert_error_envelope(refused, status_code=422, code="validation_error")
    assert error["details"] == {
        "from": "completed",
        "to": "cancelled",
        "allowed": ["in_progress", "todo"],
    }, error

    after = await client.get(f"/api/v1/tasks/{task_id}", headers=auth)
    assert after.json()["status"] == "completed"
    assert after.json()["completed_at"] is not None
    stats = await _task_stats(client, auth)
    assert (stats["cancelled"], stats["completed"]) == (0, 1), stats


async def test_a_blocked_task_can_be_cancelled_because_a_dropped_prerequisite_is_not_a_met_one(
    client, db_session
):
    """``BLOCKED -> CANCELLED`` works, and the cancelled card stops blocking.

    The third legal edge into ``cancelled``, and the one with a rule attached.
    A task waiting on unfinished work is exactly the work a user decides to
    stop waiting for — the other two edges cover stopping work that has not
    begun and work in hand.

    The card must land in the cancelled bucket and the board's blocked bucket
    must empty, because the two are a partition of the same rows. It still does
    not *satisfy* anything waiting on it — ``CANCELLED`` is not
    ``COMPLETED`` — which is why removing the dependency edge remains the
    documented way out of a stuck completion.
    """
    _seed, auth = await seeded_client(client, db_session)
    project = await _new_project(client, auth)
    prerequisite = await _new_task(client, auth, project, "Rotate the credentials")
    card = await _new_task(client, auth, project, "Publish the release")
    edge = await client.post(
        f"/api/v1/tasks/{card}/dependencies",
        params={"depends_on_id": prerequisite},
        headers=auth,
    )
    assert edge.status_code == 201, edge.text
    assert (
        await client.post(
            f"/api/v1/tasks/{card}/block", json={"note": "waiting on the vendor"}, headers=auth
        )
    ).status_code == 200
    assert (await _task_stats(client, auth))["blocked"] == 1

    cancelled = await client.post(f"/api/v1/tasks/{card}/cancel", headers=auth)
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"

    stats = await _task_stats(client, auth)
    assert (stats["cancelled"], stats["blocked"]) == (1, 0), stats
    assert (await client.get(f"/api/v1/tasks/{card}", headers=auth)).json()[
        "has_blocked_dependencies"
    ] is True


async def test_a_project_can_be_shelved_and_resumed_and_the_on_hold_bucket_then_counts_it(
    client, db_session
):
    """``on_hold`` is reachable over HTTP and stops reading as a hard zero.

    ``ProjectStatus.ON_HOLD`` has listed its legal edges since the first commit
    and ``set_status`` was called only with ``COMPLETED``, so
    ``ProjectStats.on_hold`` was structurally ``0`` for every account — a
    figure indistinguishable from a user who never pauses a project.

    A project created through the API is ``planned``, so the file pins both
    edges the table allows out of it: ``planned -> on_hold`` and
    ``on_hold -> active``. Resuming must land on ``active`` (the schema records
    no pre-hold status, the same lossy trade ``/restore`` makes), and a repeat
    of either call must be an idempotent no-op rather than a 422.

    A hold is not an archive: nothing is stamped, and the project stays in the
    working set with its window.
    """
    _seed, auth = await seeded_client(client, db_session)
    project = await _new_project(client, auth, "Phase 3")

    shelved = await client.post(f"/api/v1/projects/{project}/hold", headers=auth)
    assert shelved.status_code == 200, shelved.text
    assert shelved.json()["status"] == "on_hold"
    assert shelved.json()["archived_at"] is None

    held = await _project_stats(client, auth)
    assert (held["on_hold"], held["total"]) == (1, 1), held

    assert (await client.post(f"/api/v1/projects/{project}/hold", headers=auth)).status_code == 200

    resumed = await client.post(f"/api/v1/projects/{project}/resume", headers=auth)
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["status"] == "active"

    after = await _project_stats(client, auth)
    assert (after["on_hold"], after["active"]) == (0, 1), after

    twice = await client.post(f"/api/v1/projects/{project}/resume", headers=auth)
    assert twice.status_code == 200, twice.text
    assert twice.json()["status"] == "active"


async def test_a_planned_project_can_be_activated_and_only_then_completed(
    client, db_session, assert_error_envelope
):
    """``PLANNED -> ACTIVE`` has a door, which is what makes ``/complete`` reachable.

    ``COMPLETED`` is only legal *from* ``ACTIVE``, so while no route could take
    ``planned -> active`` a project that had not been archived and restored
    first could never be finished, and ``/complete`` answered 422 on every call
    — a project lifecycle with a completion state nothing could reach.

    Both halves are asserted: the refusal with the states that *were* available,
    and the completion after the door exists. ``completed_at`` must be stamped
    on the successful path, because a completed project carrying no evidence of
    finishing is exactly the row this design refuses to produce.
    """
    _seed, auth = await seeded_client(client, db_session)
    project = await _new_project(client, auth, "Phase 3")

    early = await client.post(f"/api/v1/projects/{project}/complete", headers=auth)
    error = assert_error_envelope(early, status_code=422, code="validation_error")
    assert error["details"]["from"] == "planned", error
    assert error["details"]["to"] == "completed", error
    assert error["details"]["allowed"] == ["active", "archived", "on_hold"], error

    activated = await client.post(f"/api/v1/projects/{project}/activate", headers=auth)
    assert activated.status_code == 200, activated.text
    assert activated.json()["status"] == "active"

    completed = await client.post(f"/api/v1/projects/{project}/complete", headers=auth)
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "completed"
    assert completed.json()["completed_at"] is not None

    stats = await _project_stats(client, auth)
    assert (stats["completed"], stats["active"]) == (1, 0), stats


async def test_a_completed_project_cannot_be_shelved_because_the_work_happened(
    client, db_session, assert_error_envelope
):
    """``COMPLETED -> ON_HOLD`` is refused, and the project stays completed.

    The table allows only ``ACTIVE`` and ``ARCHIVED`` out of ``COMPLETED``.
    Shelving a delivered project would be a claim that it was paused when it
    was not, and the stamp the completion left is exactly the evidence that
    must not be relabelled away.
    """
    _seed, auth = await seeded_client(client, db_session)
    project = await _new_project(client, auth, "Phase 3")
    assert (
        await client.post(f"/api/v1/projects/{project}/activate", headers=auth)
    ).status_code == 200
    assert (
        await client.post(f"/api/v1/projects/{project}/complete", headers=auth)
    ).status_code == 200

    refused = await client.post(f"/api/v1/projects/{project}/hold", headers=auth)
    error = assert_error_envelope(refused, status_code=422, code="validation_error")
    assert error["details"]["from"] == "completed", error
    assert "on_hold" not in error["details"]["allowed"], error

    after = await client.get(f"/api/v1/projects/{project}", headers=auth)
    assert after.json()["status"] == "completed"


async def test_another_accounts_task_answers_404_on_cancel_rather_than_403(
    client, db_session, assert_error_envelope
):
    """A foreign task id is 404 on the new door — identical to an id never issued.

    ``403`` would mean "this id exists and is not yours", which turns every
    route into a probe for which task ids are real. The new door must answer
    exactly what ``GET /tasks/{id}`` answers: the same code, the same message,
    and no mutation. ``ada`` owns one task and has already cancelled it, so
    ``grace``'s attempt cannot be told apart from ``grace`` guessing a uuid —
    and ``grace``'s own board must be untouched afterwards.
    """
    _seed, auth = await seeded_client(client, db_session)
    project = await _new_project(client, auth)
    task_id = await _new_task(client, auth, project, "Ada's card")
    assert (await client.post(f"/api/v1/tasks/{task_id}/cancel", headers=auth)).status_code == 200

    _grace_seed, other_auth = await seeded_client(
        client, db_session, username="grace", email="grace@nexus.test"
    )
    stranger_id = uuid.uuid4()

    foreign = await client.post(f"/api/v1/tasks/{task_id}/cancel", headers=other_auth)
    missing = await client.post(f"/api/v1/tasks/{stranger_id}/cancel", headers=other_auth)
    foreign_error = assert_error_envelope(foreign, status_code=404, code="not_found")
    missing_error = assert_error_envelope(missing, status_code=404, code="not_found")
    assert foreign_error["message"] == missing_error["message"] == "Task not found."

    grace_stats = await _task_stats(client, other_auth)
    assert (grace_stats["cancelled"], grace_stats["total"]) == (0, 0), grace_stats
    assert (await _task_stats(client, auth))["cancelled"] == 1


async def test_another_accounts_project_answers_404_on_every_lifecycle_door_rather_than_403(
    client, db_session, assert_error_envelope
):
    """The new project doors inherit the 404-not-403 rule, message for message.

    ``ada`` owns the project. ``grace`` calls ``/hold``, ``/resume`` and
    ``/activate`` on it, and on an id nobody issued. All six answers are 404
    with the same message, and none of them mutates anything — a refusal that
    half-applied would be worse than one that simply answered.
    """
    _seed, auth = await seeded_client(client, db_session)
    project = await _new_project(client, auth, "Phase 3")
    _grace_seed, other_auth = await seeded_client(
        client, db_session, username="grace", email="grace@nexus.test"
    )
    stranger_id = uuid.uuid4()

    for path in ("hold", "resume", "activate"):
        foreign = await client.post(f"/api/v1/projects/{project}/{path}", headers=other_auth)
        missing = await client.post(f"/api/v1/projects/{stranger_id}/{path}", headers=other_auth)
        foreign_error = assert_error_envelope(foreign, status_code=404, code="not_found")
        missing_error = assert_error_envelope(missing, status_code=404, code="not_found")
        assert foreign_error["message"] == missing_error["message"] == "Project not found.", path

    after = await client.get(f"/api/v1/projects/{project}", headers=auth)
    assert after.json()["status"] == "planned"
    assert (await _project_stats(client, auth))["on_hold"] == 0


# ---------------------------------------------------------------------------
# 2. One UTC day
# ---------------------------------------------------------------------------


async def test_the_overdue_badge_and_the_overdue_total_count_the_same_tasks(client, db_session):
    """A card's badge and the total beside it describe the same UTC day.

    Two tasks under one account: one due **today, in UTC** and one due three
    days behind. A task due today is not overdue — "overdue" means the due date
    is *behind* today — so the expected answer is exactly one overdue task, on
    both surfaces and on the single-card reads too.

    The two used to be computed from the *host's* calendar, which on a box at
    +05:30 runs five and a half hours ahead of every timestamp the database
    stores. In that window the total counted a task the badge called fine.
    Asserting both, from the same rows, is what makes that disagreement
    impossible to reintroduce.
    """
    _seed, auth = await seeded_client(client, db_session)
    today = await _db_today(db_session)
    project = await _new_project(client, auth)

    due_today = await _new_task(client, auth, project, "Due today", due_date=today.isoformat())
    three_days_back = (today - timedelta(days=3)).isoformat()
    due_long_ago = await _new_task(
        client, auth, project, "Due three days ago", due_date=three_days_back
    )

    listed = await client.get("/api/v1/tasks", headers=auth)
    assert listed.status_code == 200, listed.text
    badges = {item["id"]: item["is_overdue"] for item in listed.json()["items"]}
    assert badges[due_today] is False, badges
    assert badges[due_long_ago] is True, badges

    assert (await _task_stats(client, auth))["overdue"] == 1

    single_today = await client.get(f"/api/v1/tasks/{due_today}", headers=auth)
    assert single_today.json()["is_overdue"] is False, single_today.text
    single_old = await client.get(f"/api/v1/tasks/{due_long_ago}", headers=auth)
    assert single_old.json()["is_overdue"] is True, single_old.text


async def test_the_service_resolves_today_from_the_database_clock(client, db_session):
    """The service's "today" is the database's ``now()`` in UTC, not the host's.

    :meth:`TaskService._today` is the single seam every window on this service
    cuts on: the overdue total in :meth:`TaskService.stats`, the overdue badge
    in :meth:`TaskService._page` and in :meth:`TaskService.read`. Reading the
    database rather than the Python clock is the whole fix for the +05:30 host
    whose local day runs five and a half hours ahead of every timestamp it
    stores.

    It is checked against ``func.now()`` rather than against ``datetime.now()``
    on purpose: the assertion has to hold at *any* hour, including the hours
    where the two clocks happen to agree. A test that only failed between
    midnight and 05:30 would be a test that passes for most of the day for the
    wrong reason.
    """
    _seed, _auth = await seeded_client(client, db_session)
    expected = await _db_today(db_session)
    assert await _service(db_session)._today() == expected
    # The response models' own fallback agrees with it: UTC, not local.
    assert task_schema.utc_today() == expected


async def test_a_clock_thirty_days_ahead_cannot_move_the_overdue_badge(
    client, db_session, monkeypatch: pytest.MonkeyPatch
):
    """A clock thirty days out cannot make a task due today look overdue.

    The response models' *fallback* clock is all a bare ``model_validate`` has
    to use — there is no session in a schema. This test replaces that fallback
    with a clock deliberately, absurdly wrong and asks the API anyway. If the
    served badge moved, the answer would be coming from the module fallback
    rather than from the database, which is exactly the defect: a schema-level
    ``date.today()`` deciding what a card looks like to its owner.

    The task is due the database's UTC today, so the honest answer is
    ``is_overdue: false`` — a deadline is not late before the day has passed.
    """
    _seed, auth = await seeded_client(client, db_session)
    today = await _db_today(db_session)
    project = await _new_project(client, auth)
    task_id = await _new_task(client, auth, project, "Due today", due_date=today.isoformat())

    monkeypatch.setattr(task_schema, "utc_today", lambda: today + timedelta(days=30))

    # The fallback, for a caller with no clock at all, is now a liar.
    assert task_schema.utc_today() == today + timedelta(days=30)

    # The API does not believe it — on the single read or on the page.
    detail = await client.get(f"/api/v1/tasks/{task_id}", headers=auth)
    assert detail.status_code == 200, detail.text
    assert detail.json()["is_overdue"] is False, detail.text

    listed = await client.get("/api/v1/tasks", headers=auth)
    assert [item["is_overdue"] for item in listed.json()["items"]] == [False], listed.text
    assert (await _task_stats(client, auth))["overdue"] == 0


async def test_is_past_due_is_date_based_so_a_deadline_is_never_late_before_its_day(
    client, db_session
):
    """A task due today, tomorrow and yesterday: one overdue, and it is yesterday's.

    The comparison is on dates, not instants, so ``due_date == today`` is a task
    due today and not yet overdue — at 00:00 and at 23:59 alike. Comparing
    against a timestamp is the single most common way an "overdue" badge
    becomes something users learn to ignore, because it turns red at midnight.

    The count beside it must agree, which is the point of driving both surfaces
    from the same UTC day.
    """
    _seed, auth = await seeded_client(client, db_session)
    today = await _db_today(db_session)
    project = await _new_project(client, auth)

    yesterday = await _new_task(
        client, auth, project, "Yesterday", due_date=(today - timedelta(days=1)).isoformat()
    )
    tomorrow = await _new_task(
        client, auth, project, "Tomorrow", due_date=(today + timedelta(days=1)).isoformat()
    )

    listed = await client.get("/api/v1/tasks", headers=auth)
    badges = {item["id"]: item["is_overdue"] for item in listed.json()["items"]}
    assert badges[yesterday] is True, badges
    assert badges[tomorrow] is False, badges
    assert (await _task_stats(client, auth))["overdue"] == 1


# ---------------------------------------------------------------------------
# 3. Reads that must not confuse "not measured" with "zero"
# ---------------------------------------------------------------------------


async def test_the_single_task_read_reports_the_blocked_dependency_the_list_reports(
    client, db_session
):
    """``GET /tasks/{id}`` and ``GET /tasks`` must agree on the same row.

    ``card`` waits on ``prerequisite``, which is still ``todo``. The listing
    answered ``has_blocked_dependencies: true`` because
    :meth:`TaskService._page` runs the dependency join; the single-card fetch
    returned the ORM row, so the response model fell back to its ``false``
    default and told the client a blocked card was ready to start. Two answers
    to one question, in the same request, differing only by which endpoint was
    asked.

    ``tag_ids`` had the same shape and is pinned here for the same reason: the
    card carries one tag and the single read reported none.

    Completing the prerequisite must flip the flag to ``false`` on both — a flag
    that never clears is as wrong as one that never sets.
    """
    _seed, auth = await seeded_client(client, db_session)
    project = await _new_project(client, auth)
    prerequisite = await _new_task(client, auth, project, "Rotate the credentials")
    card = await _new_task(client, auth, project, "Publish the release")

    tag = await client.post("/api/v1/tags", json={"name": "security"}, headers=auth)
    assert tag.status_code == 201, tag.text
    tag_id = tag.json()["id"]
    tagged = await client.put(
        f"/api/v1/tasks/{card}/tags", json={"tag_ids": [tag_id]}, headers=auth
    )
    assert tagged.status_code == 200, tagged.text

    edge = await client.post(
        f"/api/v1/tasks/{card}/dependencies",
        params={"depends_on_id": prerequisite},
        headers=auth,
    )
    assert edge.status_code == 201, edge.text

    listed = await client.get("/api/v1/tasks", headers=auth)
    assert listed.status_code == 200, listed.text
    from_list = {item["id"]: item for item in listed.json()["items"]}
    assert from_list[card]["has_blocked_dependencies"] is True, from_list[card]
    assert from_list[card]["tag_ids"] == [tag_id], from_list[card]

    single = await client.get(f"/api/v1/tasks/{card}", headers=auth)
    assert single.status_code == 200, single.text
    assert single.json()["has_blocked_dependencies"] is True, single.text
    assert single.json()["tag_ids"] == [tag_id], single.text

    assert (
        await client.post(f"/api/v1/tasks/{prerequisite}/start", headers=auth)
    ).status_code == 200
    assert (
        await client.post(f"/api/v1/tasks/{prerequisite}/complete", headers=auth)
    ).status_code == 200

    cleared = await client.get(f"/api/v1/tasks/{card}", headers=auth)
    assert cleared.json()["has_blocked_dependencies"] is False, cleared.text
    relisted = await client.get("/api/v1/tasks", headers=auth)
    relisted_card = {item["id"]: item for item in relisted.json()["items"]}[card]
    assert relisted_card["has_blocked_dependencies"] is False, relisted_card


async def test_the_single_tag_read_reports_the_counts_the_tag_list_reports(client, db_session):
    """``GET /tags/{id}`` and ``GET /tags`` must agree on how far a tag reaches.

    ``reach`` is applied to one task and one project. The listing has always
    counted both from one aggregate over the page; the single-tag fetch returned
    the ORM row, so ``TagRead``'s ``0`` defaults answered ``task_count: 0`` for
    a tag sitting on a task — a confident zero the client cannot distinguish
    from "unused", and one it responds to by deleting the label.

    A tag applied to nothing must still answer ``0``/``0``. That is the case the
    defaults were *right* for, and it is asserted so the fix cannot degenerate
    into "every count is non-zero".

    The project edge is written through the repository because the Phase 3
    surface has no ``PUT /projects/{id}/tags`` route; it is the same
    :class:`~app.repositories.tag.TagRepository.set_project_tags` the service
    calls, and the count is read back through the API either way.
    """
    _seed, auth = await seeded_client(client, db_session)
    project = await _new_project(client, auth)
    task_id = await _new_task(client, auth, project, "Publish the release")

    created = await client.post("/api/v1/tags", json={"name": "reach"}, headers=auth)
    assert created.status_code == 201, created.text
    tag_id = created.json()["id"]
    unused = await client.post("/api/v1/tags", json={"name": "spare"}, headers=auth)
    assert unused.status_code == 201, unused.text
    unused_id = unused.json()["id"]

    applied = await client.put(
        f"/api/v1/tasks/{task_id}/tags", json={"tag_ids": [tag_id]}, headers=auth
    )
    assert applied.status_code == 200, applied.text
    await TagRepository(db_session).set_project_tags(uuid.UUID(project), [uuid.UUID(tag_id)])

    listed = await client.get("/api/v1/tags", headers=auth)
    assert listed.status_code == 200, listed.text
    from_list = {item["id"]: item for item in listed.json()["items"]}
    assert (from_list[tag_id]["task_count"], from_list[tag_id]["project_count"]) == (1, 1), (
        from_list[tag_id]
    )

    single = await client.get(f"/api/v1/tags/{tag_id}", headers=auth)
    assert single.status_code == 200, single.text
    assert (single.json()["task_count"], single.json()["project_count"]) == (1, 1), single.text

    empty = await client.get(f"/api/v1/tags/{unused_id}", headers=auth)
    assert empty.status_code == 200, empty.text
    assert (empty.json()["task_count"], empty.json()["project_count"]) == (0, 0), empty.text
    assert (from_list[unused_id]["task_count"], from_list[unused_id]["project_count"]) == (0, 0)

    renamed = await client.put(f"/api/v1/tags/{tag_id}", json={"name": "reach-two"}, headers=auth)
    assert renamed.status_code == 200, renamed.text
    assert (renamed.json()["task_count"], renamed.json()["project_count"]) == (1, 1), renamed.text


# ---------------------------------------------------------------------------
# 4. Cascade destruction, pinned at the boundary
# ---------------------------------------------------------------------------


async def test_deleting_a_task_destroys_the_work_sessions_and_calendar_events_filed_against_it(
    client, db_session
):
    """Deleting a task takes its tracked minutes and its bookings with it.

    This is the behaviour the ``DELETE /tasks/{id}`` docstring now states, and
    it is asserted rather than asserted-in-prose because a docstring claiming a
    cascade is only true until somebody changes a foreign key. The cascade
    lives in ``app/models/planner.py`` — ``work_sessions.task_id`` and
    ``calendar_events.task_id`` are both ``ON DELETE CASCADE`` — and this file
    cannot change it, so it pins it.

    The figures are derived from the fixture: one session and one event on the
    doomed task, one of each on a task that is being kept, and one of each on a
    task that is then *cancelled* rather than deleted. After the delete only
    the survivor's rows remain — one session, one event — and after the cancel
    the cancelled task still has its own session, which is the whole of the
    recommendation the docstring makes.
    """
    seed, auth = await seeded_client(client, db_session)
    project = uuid.UUID(await _new_project(client, auth, "Seed project"))
    doomed = await seed.task(project_id=project, title="Doomed")
    kept = await seed.task(project_id=project, title="Kept")

    day = datetime.now(UTC).date()
    await seed.work_session(day=day, minutes=45, task_id=doomed.id)
    await seed.work_session(day=day, minutes=30, task_id=kept.id)
    await seed.calendar_event(day=day, task_id=doomed.id)
    await seed.calendar_event(day=day, task_id=kept.id)
    assert (
        await _row_count(db_session, WorkSession),
        await _row_count(db_session, CalendarEvent),
    ) == (2, 2)

    deleted = await client.delete(f"/api/v1/tasks/{doomed.id}", headers=auth)
    assert deleted.status_code == 204, deleted.text

    assert (
        await _row_count(db_session, WorkSession),
        await _row_count(db_session, CalendarEvent),
    ) == (1, 1)
    assert await _rows_pointing_at(db_session, WorkSession, kept.id) == 1
    assert await _rows_pointing_at(db_session, CalendarEvent, kept.id) == 1

    survivor = await seed.task(project_id=project, title="Cancelled")
    await seed.work_session(day=day, minutes=20, task_id=survivor.id)
    cancelled = await client.post(f"/api/v1/tasks/{survivor.id}/cancel", headers=auth)
    assert cancelled.status_code == 200, cancelled.text
    assert await _rows_pointing_at(db_session, WorkSession, survivor.id) == 1
    assert await _row_count(db_session, WorkSession) == 2


async def test_deleting_a_project_destroys_the_work_sessions_and_calendar_events_under_it(
    client, db_session
):
    """Deleting a project takes every tracked minute filed under it, silently.

    The cascade is two hops: ``tasks.project_id`` is ``ON DELETE CASCADE``, and
    the tasks it takes with them are the foreign key on ``work_sessions`` and
    ``calendar_events``; both planner tables also cascade on ``project_id``
    directly, so a session booked against the project with no task at all goes
    too. The figures are derived from the fixture: two sessions and one event
    under the doomed project, one session and one event under the project that
    stays, and nothing left for the deleted one.

    This is why ``DELETE /projects/{id}`` says "archive, do not delete": an
    archived project keeps every minute, and the analytics that read them stop
    counting it as active work without the history vanishing.
    """
    seed, auth = await seeded_client(client, db_session)
    doomed = await seed.project(name="Doomed")
    kept = await seed.project(name="Kept")

    day = datetime.now(UTC).date()
    await seed.work_session(day=day, minutes=60, project_id=doomed.id)
    await seed.work_session(day=day, minutes=15, project_id=doomed.id)
    await seed.work_session(day=day, minutes=25, project_id=kept.id)
    await seed.calendar_event(day=day, project_id=doomed.id)
    await seed.calendar_event(day=day, project_id=kept.id)
    assert (
        await _row_count(db_session, WorkSession),
        await _row_count(db_session, CalendarEvent),
    ) == (3, 2)

    deleted = await client.delete(f"/api/v1/projects/{doomed.id}", headers=auth)
    assert deleted.status_code == 204, deleted.text

    assert (
        await _row_count(db_session, WorkSession),
        await _row_count(db_session, CalendarEvent),
    ) == (1, 1)
    assert await _project_rows(db_session, WorkSession, kept.id) == 1
    assert await _project_rows(db_session, CalendarEvent, kept.id) == 1

    archived = await client.post(f"/api/v1/projects/{kept.id}/archive", headers=auth)
    assert archived.status_code == 200, archived.text
    assert archived.json()["status"] == "archived"
    assert archived.json()["archived_at"] is not None
    # Archiving keeps everything the delete would have taken.
    assert await _project_rows(db_session, WorkSession, kept.id) == 1


async def test_a_deleted_tag_takes_its_edges_and_is_404_on_the_next_read(client, db_session):
    """Deleting a tag leaves no edge behind, and no way to probe that it existed.

    The tag row and its ``task_tags`` edge go together; the task that carried
    the label simply has one fewer. Reading the deleted id is a 404 — byte for
    byte the answer an id nobody issued gets — so the response cannot be used to
    find out which tag ids were real.
    """
    _seed, auth = await seeded_client(client, db_session)
    project = await _new_project(client, auth)
    task_id = await _new_task(client, auth, project, "Publish the release")

    created = await client.post("/api/v1/tags", json={"name": "temporary"}, headers=auth)
    assert created.status_code == 201, created.text
    tag_id = created.json()["id"]
    applied = await client.put(
        f"/api/v1/tasks/{task_id}/tags", json={"tag_ids": [tag_id]}, headers=auth
    )
    assert applied.status_code == 200, applied.text
    assert (await client.get(f"/api/v1/tasks/{task_id}", headers=auth)).json()["tag_ids"] == [
        tag_id
    ]

    stored = await db_session.execute(
        select(Tag.id, Tag.name, Tag.user_id).where(Tag.id == uuid.UUID(tag_id))
    )
    assert stored.one_or_none() is not None

    deleted = await client.delete(f"/api/v1/tags/{tag_id}", headers=auth)
    assert deleted.status_code == 204, deleted.text

    gone = await db_session.execute(select(Tag.id).where(Tag.id == uuid.UUID(tag_id)))
    assert gone.one_or_none() is None

    detail = await client.get(f"/api/v1/tasks/{task_id}", headers=auth)
    assert detail.status_code == 200, detail.text
    assert detail.json()["tag_ids"] == []
    missing = await client.get(f"/api/v1/tags/{tag_id}", headers=auth)
    stranger = await client.get(f"/api/v1/tags/{uuid.uuid4()}", headers=auth)
    assert missing.status_code == stranger.status_code == 404
    assert missing.json()["error"]["message"] == stranger.json()["error"]["message"]


async def _rows_pointing_at(session: AsyncSession, model: type, task_id: uuid.UUID) -> int:
    """Count the rows of ``model`` whose ``task_id`` is ``task_id``."""
    return await session.scalar(
        select(func.count()).select_from(model).where(model.task_id == task_id)
    )


async def _project_rows(session: AsyncSession, model: type, project_id: uuid.UUID) -> int:
    """Count the rows of ``model`` whose ``project_id`` is ``project_id``."""
    return await session.scalar(
        select(func.count()).select_from(model).where(model.project_id == project_id)
    )


async def _event_types(session: AsyncSession, user_id: uuid.UUID) -> list[str]:
    """This account's activity feed, in the order the rows were written."""
    rows = await session.execute(
        select(ActivityLog.event_type)
        .where(ActivityLog.user_id == user_id)
        .order_by(ActivityLog.created_at, ActivityLog.id)
    )
    return [row[0] for row in rows.all()]


async def test_deleting_a_project_is_recorded_as_a_deletion(client, db_session):
    """The feed says the project was deleted, and says it only once.

    A delete is a moment that happened, not a mutation of a row that is still
    there — and this service used to record it as ``PROJECT_UPDATED`` with a
    ``deleted`` flag in the metadata. Every consumer filtering the feed for
    deletions therefore saw no deletion at all, and every "what changed in this
    project" reader counted the removal as an edit. The event the task feed
    already had for the same moment is ``TASK_DELETED``; this is its project
    counterpart, not a new idea.

    The id travels in ``metadata`` and not on ``project_id``, because the row
    that column points at is gone and a best-effort write that violates the
    foreign key would be swallowed — losing the event while still answering 204.
    """
    seed, auth = await seeded_client(client, db_session)
    doomed = await seed.project(name="Doomed")

    before = await _event_types(db_session, seed.owner.id)
    deleted = await client.delete(f"/api/v1/projects/{doomed.id}", headers=auth)
    assert deleted.status_code == 204, deleted.text

    after = await _event_types(db_session, seed.owner.id)
    assert after[len(before) :] == [ActivityEvent.PROJECT_DELETED.value]
    assert ActivityEvent.PROJECT_UPDATED.value not in after[len(before) :]

    event = await db_session.execute(
        select(ActivityLog.metadata_).where(
            ActivityLog.user_id == seed.owner.id,
            ActivityLog.event_type == ActivityEvent.PROJECT_DELETED.value,
        )
    )
    assert event.scalar_one() == {"project_id": str(doomed.id), "name": "Doomed"}
