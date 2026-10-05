"""One detection pass: what it writes, what it refuses to write, and what it closes.

:meth:`app.services.risk.detection.RiskDetectionService.evaluate` is the only
public surface of Phase 7's risk engine, and it is where every decision that is
*not* arithmetic becomes visible. ``app/services/risk/scoring.py`` has its own
tests over the pure formulas; this file covers the three things the formulas
cannot decide:

**What becomes a row.** A detector's answer only reaches the Risk Center after
being partitioned by two rules the module states at length — an unavailable
detector writes nothing and says why, and a detector that measures zero writes
nothing and says that too. Each is asserted from both sides, because a partition
that dropped the *available* results instead of the unavailable ones would look
identical from the empty side.

**What happens on the second pass.** The brief's hardest requirement is "the
same underlying risk should not generate hundreds of identical records", and it
is only true if re-detection updates in place. So the deduplication, the
in-place refresh, and the resolution sweep are each driven by two consecutive
passes over a fixture and a hand-checked fixture in between — never by reading
the source of the upsert and asserting against it.

**What a stored risk is obliged to carry.** Evidence and metadata are what make
a row auditable after the analytics behind it have been rebuilt, and the
null-identity upsert path once stored ``{}`` for every account-level risk while
its row-level sibling stored correctly. The metadata assertions below are
therefore aimed at exactly the account-level risks, not at the task-level one
that would have passed anyway.

**The seventh detector.** ``RiskType.TASK`` is the one vocabulary member the
original six detectors could not reach, and section (h) now covers it: a blocked
task and a task that has been moved three or more times are conditions of a
single row, which the project roll-up can count but cannot express. Two
``recommendation_rules`` entries were mapped onto that type from the start and
had no path to fire, so the block below asserts them end to end rather than
asserting the risk alone.

Reading the rows back is done through a column projection rather than through the
ORM, for the same reason ``test_analytics_daily_metrics.py`` does it: this
session is also the one that wrote the rows, so an entity read would hand back
whatever the identity map cached, and an idempotency assertion would compare
stale objects to fresh ones and pass for the wrong reason.

On the project detector's remaining-work floor
-----------------------------------------------
The project sub-signal that counts unfinished tasks now starts above zero:
:data:`app.services.risk.scoring.PROJECT_REMAINING_FLOOR` is ten, so two or three
open tasks are the normal state of a healthy project rather than a fraction of a
signal. Four expectations below changed shape because of it — a project holding
one or two open tasks and nothing else used to measure 1 or 2 points and be stored
as a ``low`` risk saying "N task(s) are unfinished", and now measures a genuine
zero and is measured rather than stored.

There is a second, older reason the same fixtures find no project row: at one
unfinished task the score is ``0.10 x (1 / 20) x 100``, exactly ``0.5``, and
:func:`app.services.risk.scoring._result` snaps to six decimals before rounding to
an integer so that Python's banker's rounding sends the exact half to ``0``. That
is the *right* answer for half a point — the detection service's "a measured zero
writes nothing" rule cannot discard a score that rounds up to 1 — and the fixtures
that genuinely need a surviving project risk give it a signal worth a point
(:func:`_project_survivor`).

On the caps a pass writes
-------------------------
Section (k) covers the rule that a bound on what a pass writes is not a
measurement of what has gone away. Every fixture there is two passes on purpose:
a risk that was never written cannot be falsely closed, so the regression needs a
row that already exists and a second pass that skips it.

On the window
-------------
Every fixture is anchored on **the database's** date rather than on
``date.today()``, because ``evaluate`` reads its own instant from ``func.now()``
and the two must describe the same day: a task's deadline is measured in hours
from the pass's instant to the end of its due day
(:func:`app.services.risk.detection._hours_until`), so a ``today`` from the host
clock and a ``now`` from the server would put every deadline in the wrong band.
The residual risk is the two reads straddling UTC midnight, a window of
milliseconds in twenty-four hours.

``_hours_until`` measuring to the *end* of the due day is also why the fixtures
place deadlines two or three days out rather than "tomorrow". A due date of
``today + 1`` sits between 24 and 48 hours away depending on the hour of the run
and a due date of ``today + 3`` sits between 72 and 96 — bands that do not
straddle an urgency threshold, so the exact scores below hold at any hour.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import ActivityLog
from app.models.enums import (
    ActivityEvent,
    RecommendationType,
    RiskStatus,
    RiskType,
    TaskStatus,
)
from app.models.planner import AvailabilityRule, WorkSession
from app.models.risk import Recommendation, Risk, RiskEvaluation
from app.models.task import Task
from app.repositories.activity import ActivityRepository
from app.repositories.analytics import AnalyticsRepository
from app.repositories.knowledge import NoteRepository
from app.repositories.planner import CalendarEventRepository, WorkSessionRepository
from app.repositories.project import ProjectRepository
from app.repositories.risk import RiskRepository
from app.repositories.task import TaskRepository
from app.services.activity_service import ActivityService
from app.services.analytics.service import AnalyticsService
from app.services.risk import detection as detection_module
from app.services.risk.detection import RiskDetectionService
from app.services.risk.recommendation import RecommendationService
from tests.analytics_fixtures import DAY, AnalyticsSeed, at, register_user

pytestmark = pytest.mark.integration

#: The evaluation window every fixture uses. Seven days rather than the
#: fourteen-day default because it is the smallest window in which the
#: consistency detector has a same-length window to compare against, and because
#: it makes the declared-availability arithmetic below an exact integer: one rule
#: per weekday of a seven-day window contributes exactly seven spans.
WINDOW = 7

#: The columns every stored risk is read back through. Listed in full rather than
#: read as ORM entities so a column added to the model shows up here as a failing
#: import rather than as a silently unasserted field.
_RISK_COLUMNS = (
    Risk.id,
    Risk.risk_type,
    Risk.severity,
    Risk.score,
    Risk.title,
    Risk.description,
    Risk.evidence,
    Risk.evidence_strength,
    Risk.entity_type,
    Risk.entity_id,
    Risk.status,
    Risk.detected_at,
    Risk.resolved_at,
    Risk.metadata_,
)

_EVALUATION_COLUMNS = (
    RiskEvaluation.risks_found,
    RiskEvaluation.risks_created,
    RiskEvaluation.risks_updated,
    RiskEvaluation.risks_resolved,
    RiskEvaluation.by_severity,
    RiskEvaluation.by_type,
    RiskEvaluation.recommendations_created,
    RiskEvaluation.duration_ms,
)


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def _service(session: AsyncSession) -> RiskDetectionService:
    """A detection service wired the way :func:`app.api.deps.get_risk_service` wires it.

    Every collaborator is the real one, including the activity sink and the
    recommendation generator. Passing ``activity=None`` would make the lifecycle
    assertions below pass vacuously, and passing ``recommendations=None`` would
    drop the one collaborator whose absence is a documented deployment choice
    rather than a test fixture.
    """
    metrics = AnalyticsRepository(session)
    activity = ActivityService(ActivityRepository(session))
    risks = RiskRepository(session)
    tasks = TaskRepository(session)
    projects = ProjectRepository(session)
    analytics = AnalyticsService(
        metrics,
        tasks,
        projects,
        WorkSessionRepository(session),
        CalendarEventRepository(session),
        NoteRepository(session),
    )
    return RiskDetectionService(
        metrics,
        analytics,
        tasks,
        projects,
        risks,
        activity=activity,
        recommendations=RecommendationService(risks, tasks, projects, activity),
    )


async def _seed(session: AsyncSession, username: str = "ada") -> AnalyticsSeed:
    """An owner created directly.

    Direct rather than through the API because these tests drive the service,
    not a route, and a registered account would carry login rows this file has
    no reason to reason about. ``register_user`` writes no ``activity_events``,
    which matters: ``activity_days_in_range`` reads that feed, so a registration
    event would move the consistency detector's inputs behind the test's back.
    """
    return AnalyticsSeed(session, await register_user(session, username=username))


async def _db_today(session: AsyncSession) -> date:
    """The database's date, read the same way ``evaluate`` reads its instant.

    Including the normalisation. ``func.now()`` is a ``timestamptz`` returned
    labelled with the *connection's* ``TimeZone`` — ``Asia/Calcutta`` on this
    server — so a bare ``.date()`` is the server-local day, while the detector
    converts to UTC before deriving the deadline distances it reports. Every
    fixture here is positioned relative to this date, so a fixture anchored on one
    calendar and asserted against the other is a whole day of drift for the five
    and a half hours a day the two disagree.
    """
    value = await session.scalar(select(func.now()))
    if not isinstance(value, datetime):
        return datetime.now(UTC).date()
    if value.tzinfo is None:  # pragma: no cover - psycopg returns aware values
        return value.date()
    return value.astimezone(UTC).date()


async def _availability(
    session: AsyncSession,
    owner_id: uuid.UUID,
    *,
    weekdays: tuple[int, ...] = (0, 1, 2, 3, 4, 5, 6),
    starts_at: time = time(9, 0),
    ends_at: time = time(10, 0),
) -> None:
    """Declare the same wall-clock window on each listed weekday.

    All seven weekdays rather than "the days the fixture uses", so the declared
    capacity is a function of the window length alone: a seven-day window over
    seven rules contributes exactly ``7 x span`` minutes whatever day it starts
    on, and the workload score derived from it is therefore exact rather than
    dependent on which weekday the suite happened to run.
    """
    for weekday in weekdays:
        session.add(
            AvailabilityRule(
                owner_id=owner_id, weekday=weekday, starts_at=starts_at, ends_at=ends_at
            )
        )
    await session.commit()


# ---------------------------------------------------------------------------
# Reading the pass's output back
# ---------------------------------------------------------------------------


async def _risks(session: AsyncSession, owner_id: uuid.UUID) -> list[dict[str, object]]:
    """Every stored risk for one account, as plain mappings.

    Ordered by type then id so a diff between two passes is readable. Columns
    rather than entities because this session wrote the rows; see the module
    docstring.
    """
    result = await session.execute(
        select(*_RISK_COLUMNS)
        .where(Risk.user_id == owner_id)
        .order_by(Risk.risk_type.asc(), Risk.id.asc())
    )
    return [dict(row._mapping) for row in result.all()]


def _by_type(rows: list[dict[str, object]]) -> dict[str, int]:
    """``{risk_type: count}`` for a pass's stored risks."""
    counts: dict[str, int] = {}
    for row in rows:
        counts[str(row["risk_type"])] = counts.get(str(row["risk_type"]), 0) + 1
    return counts


def _only(rows: list[dict[str, object]], risk_type: str) -> dict[str, object]:
    """The single stored risk of one type, asserting there is exactly one."""
    matching = [row for row in rows if row["risk_type"] == risk_type]
    assert len(matching) == 1, (
        f"expected exactly one {risk_type} risk, found {len(matching)} in {_by_type(rows)}"
    )
    return matching[0]


async def _events(session: AsyncSession, user_id: uuid.UUID) -> list[dict[str, object]]:
    """The owner's activity feed as ``{event_type, metadata_}``, oldest first."""
    result = await session.execute(
        select(ActivityLog.event_type, ActivityLog.metadata_)
        .where(ActivityLog.user_id == user_id)
        .order_by(ActivityLog.created_at.asc(), ActivityLog.id.asc())
    )
    return [{"event_type": row[0], "metadata": row[1]} for row in result.all()]


def _event_types(events: list[dict[str, object]]) -> list[str]:
    return [str(event["event_type"]) for event in events]


#: The three lifecycle events one pass writes. Named rather than filtered on a
#: prefix so that an event type added to the reconciliation shows up in this list
#: and fails the assertion rather than being filtered past it.
_RISK_LIFECYCLE_EVENTS = (
    ActivityEvent.RISK_DETECTED.value,
    ActivityEvent.RISK_UPDATED.value,
    ActivityEvent.RISK_RESOLVED.value,
)


def _risk_event_types(events: list[dict[str, object]]) -> list[str]:
    """The reconciliation events only, in order.

    A wired pass also raises recommendations and writes
    ``RECOMMENDATION_CREATED`` for each of them, interleaved with the risk
    events; those belong to the suggestion rules and are not what these
    assertions are about.
    """
    return [
        event_type for event_type in _event_types(events) if event_type in _RISK_LIFECYCLE_EVENTS
    ]


async def _evaluations(session: AsyncSession, owner_id: uuid.UUID) -> list[dict[str, object]]:
    """The account's evaluation history, oldest first.

    Ordered by ``evaluated_at`` then id rather than through
    :meth:`RiskRepository.list_evaluations`, whose newest-first ordering is the
    right one for the intelligence screen and the wrong one for asserting "the
    *second* pass reported these counts".
    """
    result = await session.execute(
        select(*_EVALUATION_COLUMNS)
        .where(RiskEvaluation.user_id == owner_id)
        .order_by(RiskEvaluation.evaluated_at.asc(), RiskEvaluation.id.asc())
    )
    return [dict(row._mapping) for row in result.all()]


async def _complete(session: AsyncSession, task: Task, *, on: date) -> Task:
    """Finish an open task, dated, so the next pass sees the condition disappear.

    Written through the ORM rather than through the task service because the
    point of the auto-resolution tests is the *state* the pass reads, not the
    validation that produces it — and because a service call would also write a
    ``TASK_COMPLETED`` activity event, which moves the consistency detector's
    inputs in the same change being tested.
    """
    task.status = TaskStatus.COMPLETED.value
    task.completed_at = at(on, 12)
    task.updated_at = at(on, 12)
    session.add(task)
    await session.commit()
    return task


async def _project_survivor(seed: AnalyticsSeed, project_id: uuid.UUID, *, today: date) -> Task:
    """A second open task that keeps its project's risk worth a point.

    Two jobs in one fixture, and both are needed. The auto-resolution tests have
    to hold a project risk that survives the change under test, so the untouched
    sibling has to carry a signal of its own rather than depend on the *count* of
    remaining tasks: one unfinished task is half a point, which
    :func:`app.services.risk.scoring._result` rounds to nothing (see the module
    docstring), so a sibling that was merely open would silently stop existing.
    An overdue due date is the cheapest signal that is unambiguously a point:
    ``0.30 x 1/10 = 3``.

    Deliberately carries **no estimate**, so it never enters the deadline scan:
    the tests that use it are about which rows survive a sweep, and a second
    deadline risk would put a third row in play.
    """
    return await seed.task(
        project_id=project_id,
        status=TaskStatus.IN_PROGRESS.value,
        due_date=today - timedelta(days=3),
    )


async def _recommendations(session: AsyncSession, owner_id: uuid.UUID) -> list[dict[str, object]]:
    """Every stored recommendation for one account, as plain mappings.

    The same column-projection rule as :func:`_risks`, for the same reason: this
    session wrote the rows the assertion reads.
    """
    result = await session.execute(
        select(
            Recommendation.recommendation_type,
            Recommendation.priority,
            Recommendation.title,
            Recommendation.description,
            Recommendation.reason,
            Recommendation.entity_type,
            Recommendation.entity_id,
            Recommendation.risk_id,
            Recommendation.status,
        )
        .where(Recommendation.user_id == owner_id)
        .order_by(Recommendation.recommendation_type.asc(), Recommendation.id.asc())
    )
    return [dict(row._mapping) for row in result.all()]


def _recommendation_types(rows: list[dict[str, object]]) -> dict[str, int]:
    """``{recommendation_type: count}`` for a pass's suggestions."""
    counts: dict[str, int] = {}
    for row in rows:
        key = str(row["recommendation_type"])
        counts[key] = counts.get(key, 0) + 1
    return counts


async def _reschedules(seed: AnalyticsSeed, task_id: uuid.UUID, count: int, *, day: date) -> None:
    """``count`` ``TASK_RESCHEDULED`` events against one task, oldest first.

    Written directly rather than through ``TaskService.reschedule`` because the
    detectors read the event feed, not the due date it would have moved — and
    because a service call would also rewrite the task row, which is the thing
    under test in every other respect. Spread across hours on one day so the
    consistency detector's active-day count moves by exactly one, which keeps
    these fixtures from raising an account-level risk of their own.
    """
    for index in range(count):
        await seed.activity(
            ActivityEvent.TASK_RESCHEDULED, day=day, task_id=task_id, hour=8 + index
        )


# ---------------------------------------------------------------------------
# (a) One pass produces the right risk for a known fixture
# ---------------------------------------------------------------------------


async def test_a_task_due_tomorrow_with_unbooked_work_raises_exactly_that_deadline_risk(
    db_session: AsyncSession,
) -> None:
    """Five hours of work, two hours booked, due tomorrow — score 42, ``medium``.

    Hand-derived from :func:`~app.services.risk.scoring.deadline_risk`:
    ``gap_ratio = (300 - 120) / 300 = 0.6``; ``deadline_in_hours`` is between
    24 and 48 because :func:`~app.services.risk.detection._hours_until` measures
    to the **end** of the due day, which puts tomorrow in the 72-hour band at
    ``urgency = 0.7``; ``priority = medium`` leaves the multiplier at 1.0; and
    ``historical_completion_rate`` is ``None`` because no task with a due date
    has been completed, so the completion multiplier stays neutral at 1.0.
    ``round(100 x 0.6 x 0.7 x 1.0 x 1.0) = 42``, which bands ``medium``.

    The brief's worked example — 300/120 at 24 hours — scores 60 and bands
    ``high``, and that figure is *not* reachable from a due date of "tomorrow":
    24 hours is a duration, and the schema stores a date.
    """
    seed = await _seed(db_session)
    today = await _db_today(db_session)
    project = await seed.project()
    task = await seed.task(
        project_id=project.id,
        title="Ship the quarterly report",
        due_date=today + timedelta(days=1),
        estimated_minutes=300,
        actual_minutes=0,
    )
    await seed.work_session(day=today, minutes=120, task_id=task.id, project_id=project.id)

    summary = await _service(db_session).evaluate(owner=seed.owner, today=today, window_days=WINDOW)

    rows = await _risks(db_session, seed.owner.id)
    deadline = _only(rows, RiskType.DEADLINE.value)
    assert deadline["score"] == 42
    assert deadline["severity"] == "medium"
    assert deadline["status"] == RiskStatus.ACTIVE.value
    assert deadline["entity_type"] == "task"
    assert deadline["entity_id"] == task.id
    assert deadline["metadata_"] == {
        "remaining_minutes": 300,
        "available_minutes": 120,
        # Read back as a float: the column is JSONB and the detector stored
        # whatever `_hours_until` returned, which is not an integer.
        "deadline_in_hours": deadline["metadata_"]["deadline_in_hours"],
        "priority": "medium",
        "historical_completion_rate": None,
        "title": "Ship the quarterly report",
    }
    assert 24 < float(deadline["metadata_"]["deadline_in_hours"]) <= 48

    # No project risk alongside it, and that is arithmetic rather than an
    # omission: the fixture's single open task leaves the project roll-up with
    # one remaining task, worth `0.10 * 1/20 * 100 = 0.5`. Half a point is half a
    # point — `_result` snaps to six decimals and banker's rounding sends it to
    # zero — and the module's "a measured zero writes nothing" rule then discards
    # it. See the module docstring; the project detector's own test below covers
    # the same boundary from the accepting side.
    assert _by_type(rows) == {"deadline": 1}
    assert summary.by_type == {"deadline": 1}
    assert summary.by_severity == {"medium": 1}


# ---------------------------------------------------------------------------
# (b) Deduplication
# ---------------------------------------------------------------------------


async def test_a_second_pass_over_unchanged_data_creates_nothing(db_session: AsyncSession) -> None:
    """Two passes, one row per condition, and the second reports ``created == 0``.

    The brief's "the same underlying risk should not generate hundreds of
    identical records" is only true if the *second* pass updates in place. Both
    halves are asserted: the row count and the row ids are unchanged, and the
    summary's own counters say ``created == 0`` and ``updated == 1``. A run that
    deduplicated but reported the refresh as a creation would leave the event
    feed and the trend history disagreeing with each other.
    """
    seed = await _seed(db_session)
    today = await _db_today(db_session)
    project = await seed.project()
    task = await seed.task(
        project_id=project.id,
        due_date=today + timedelta(days=1),
        estimated_minutes=300,
        actual_minutes=0,
    )
    await seed.work_session(day=today, minutes=120, task_id=task.id, project_id=project.id)
    service = _service(db_session)

    first = await service.evaluate(owner=seed.owner, today=today, window_days=WINDOW)
    first_rows = await _risks(db_session, seed.owner.id)

    second = await service.evaluate(owner=seed.owner, today=today, window_days=WINDOW)
    second_rows = await _risks(db_session, seed.owner.id)

    assert first.risks_found == 1
    assert first.risks_created == 1
    assert first.risks_updated == 0
    assert second.risks_found == 1
    assert second.risks_created == 0
    assert second.risks_updated == 1
    assert second.risks_resolved == 0

    assert len(second_rows) == len(first_rows) == 1
    assert [row["id"] for row in second_rows] == [row["id"] for row in first_rows]

    history = await _evaluations(db_session, seed.owner.id)
    assert [
        (row["risks_found"], row["risks_created"], row["risks_updated"]) for row in history
    ] == [
        (1, 1, 0),
        (1, 0, 1),
    ]

    # The event feed tells the same story as the counters: one detection, then one
    # update. `RISK_UPDATED` on a re-detection is the assertion that the
    # `created` flag really did come from the storage layer and not from the
    # service's own arithmetic. The risk events are filtered out of a feed that
    # also carries the pass's suggestion events, because the assertion is about
    # the reconciliation and not about the recommendation rules.
    assert _risk_event_types(await _events(db_session, seed.owner.id)) == [
        ActivityEvent.RISK_DETECTED.value,
        ActivityEvent.RISK_UPDATED.value,
    ]


# ---------------------------------------------------------------------------
# (c) A changed score updates in place
# ---------------------------------------------------------------------------


async def test_a_growing_estimate_moves_the_score_on_the_same_row(db_session: AsyncSession) -> None:
    """300 estimated becomes 600: 42/``medium`` becomes 56/``high``, same id.

    ``gap_ratio = (600 - 120) / 600 = 0.8`` against the same ``urgency = 0.7``,
    so ``round(100 x 0.8 x 0.7) = 56``. A second row would leave the Risk Center
    showing both the old score and the new one for the same task, which is the
    duplicate-record failure the partial unique index exists to prevent — so the
    assertion is on the row count *and* on the identity of the row that moved.
    The count is one rather than two because the project's own risk is half a
    point and is therefore not stored; see the module docstring.
    """
    seed = await _seed(db_session)
    today = await _db_today(db_session)
    project = await seed.project()
    task = await seed.task(
        project_id=project.id,
        due_date=today + timedelta(days=1),
        estimated_minutes=300,
        actual_minutes=0,
    )
    await seed.work_session(day=today, minutes=120, task_id=task.id, project_id=project.id)
    service = _service(db_session)
    await service.evaluate(owner=seed.owner, today=today, window_days=WINDOW)
    before = _only(await _risks(db_session, seed.owner.id), RiskType.DEADLINE.value)
    assert (before["score"], before["severity"]) == (42, "medium")

    task.estimated_minutes = 600
    db_session.add(task)
    await db_session.commit()
    summary = await service.evaluate(owner=seed.owner, today=today, window_days=WINDOW)
    after = _only(await _risks(db_session, seed.owner.id), RiskType.DEADLINE.value)

    assert after["id"] == before["id"]
    assert (after["score"], after["severity"]) == (56, "high")
    assert len(await _risks(db_session, seed.owner.id)) == 1
    assert summary.risks_updated == 1
    assert summary.risks_created == 0
    assert summary.by_severity == {"high": 1}


# ---------------------------------------------------------------------------
# (d) Auto-resolution
# ---------------------------------------------------------------------------


async def test_completing_the_task_resolves_its_risk_and_says_so(db_session: AsyncSession) -> None:
    """The condition goes away, the row goes to ``resolved``, and an event says so.

    This is the step that stops the Risk Center only ever growing, and it is the
    one a detector cannot perform on its own: nothing re-detecting a finished
    task will ever *mention* that the old deadline risk stopped being true. The
    only evidence of it is the risk's absence from this run's output, which is
    what :meth:`RiskDetectionService._resolve_stale` reads.

    The fixture holds a second, untouched open task so the project risk survives
    the change and is *updated* rather than swept — which is the sharper test: a
    sweep that closed everything the run did not newly create would resolve 2
    rows here and the count would prove it. That sibling has to carry a signal of
    its own (:func:`_project_survivor`), because a project holding one unfinished
    task is half a point and is not stored at all.
    """
    seed = await _seed(db_session)
    today = await _db_today(db_session)
    project = await seed.project()
    task = await seed.task(
        project_id=project.id,
        due_date=today + timedelta(days=1),
        estimated_minutes=300,
        actual_minutes=0,
    )
    await _project_survivor(seed, project.id, today=today)
    await seed.work_session(day=today, minutes=120, task_id=task.id, project_id=project.id)
    service = _service(db_session)
    await service.evaluate(owner=seed.owner, today=today, window_days=WINDOW)
    opened = _only(await _risks(db_session, seed.owner.id), RiskType.DEADLINE.value)
    assert opened["status"] == RiskStatus.ACTIVE.value
    assert opened["resolved_at"] is None

    await _complete(db_session, task, on=today)
    summary = await service.evaluate(owner=seed.owner, today=today, window_days=WINDOW)
    closed = _only(await _risks(db_session, seed.owner.id), RiskType.DEADLINE.value)

    assert closed["id"] == opened["id"]
    assert closed["status"] == RiskStatus.RESOLVED.value
    assert isinstance(closed["resolved_at"], datetime)
    assert closed["resolved_at"] > opened["detected_at"]
    assert summary.risks_resolved == 1
    assert summary.risks_updated == 1
    assert summary.risks_found == 1

    resolved_events = [
        event
        for event in await _events(db_session, seed.owner.id)
        if event["event_type"] == ActivityEvent.RISK_RESOLVED.value
    ]
    assert len(resolved_events) == 1
    assert resolved_events[0]["metadata"] == {
        "risk_id": str(opened["id"]),
        "risk_type": RiskType.DEADLINE.value,
        "severity": "medium",
        "reason": "the condition behind it was not detected in this evaluation",
    }

    # The resolution sweep is owner-scoped and terminal: the same pass cannot
    # then re-detect the resolved risk, or the row would flip back.
    assert summary.by_type == {"project": 1}
    assert _by_type(await _risks(db_session, seed.owner.id)) == {
        RiskType.DEADLINE.value: 1,
        RiskType.PROJECT.value: 1,
    }


# ---------------------------------------------------------------------------
# (e) A measurement of zero is measured and not stored
# ---------------------------------------------------------------------------


async def test_a_busy_quiet_account_is_measured_and_stores_nothing(
    db_session: AsyncSession,
) -> None:
    """An empty Risk Center from a busy, unpressured account — and a summary that says why.

    This is the second of the module's two rules about not writing a row, and it
    is a judgement call rather than a law — which is exactly why it is tested
    from both sides. A pass that stored its zeros would fill the Risk Center with
    "No scheduling conflicts detected" rows the user has to dismiss by hand every
    run; a pass that dropped them *silently* would leave an empty list
    indistinguishable from a broken engine. So the table is asserted empty **and**
    the summary carries the sentence naming the detectors that measured nothing.

    Every detector that *can* answer is made to answer rather than decline,
    which is the harder half: availability is declared wide enough that the 180
    booked minutes sit far inside the 420 declared, and the three completions
    carry equal estimates so the mean overrun is exactly zero. The two that
    still decline — consistency, which has no earlier window to compare against,
    and the deadline scan, which has no open task carrying both a due date and
    an estimate — say so on the same summary, which is the whole point: an empty
    Risk Center is always explained.
    """
    seed = await _seed(db_session)
    today = await _db_today(db_session)
    await _availability(db_session, seed.owner.id)
    project = await seed.project()
    for offset, (estimated, actual) in enumerate(((60, 60), (90, 90), (120, 120))):
        day = today - timedelta(days=4 - offset)
        await seed.completed_task(
            day=day, project_id=project.id, estimated_minutes=estimated, actual_minutes=actual
        )
        await seed.work_session(day=day, minutes=60, start_hour=9)

    summary = await _service(db_session).evaluate(owner=seed.owner, today=today, window_days=WINDOW)

    assert await _risks(db_session, seed.owner.id) == []
    assert summary.risks_found == 0
    assert summary.risks_created == 0
    assert summary.risks_updated == 0
    assert summary.risks_resolved == 0
    assert summary.by_severity == {}
    assert summary.by_type == {}
    assert summary.evaluated is True
    assert (
        summary.reason_if_not_evaluated
        == "Consistency: There is no earlier period with recorded activity to compare "
        "against.; "
        "Deadline: Not assessed: no open task carries both a due date inside this window and "
        "an estimate.; "
        "Task: Not assessed: no open task is recorded as blocked or has 3 or more reschedules "
        "in its history.; "
        "Estimation, Project, Scheduling, Workload: measured no risk in this window."
    )


# ---------------------------------------------------------------------------
# (f) Cold start
# ---------------------------------------------------------------------------


async def test_a_brand_new_account_is_told_it_has_not_enough_data(
    db_session: AsyncSession,
) -> None:
    """An empty account produces no rows, no events and every reason.

    The sentence is asserted in full rather than by substring because the whole
    point is that each detector says *its own* reason: "no availability is
    configured" and "no earlier period to compare against" are different facts
    with different remediations, and a summary that collapsed them into one
    generic sentence would have thrown away the only actionable part.

    ``evaluated`` is **false** here, and this assertion was ``True`` before the
    scheduling detector learned to decline. The old expectation was wrong rather
    than merely out of date, and the reason is worth recording: the flag means
    "at least one detector could judge something", and on an account with no
    projects, no tasks, no sessions and no availability rules, not one could.
    The flag stayed true only because the scheduling detector was fed four zero
    counts and had nowhere to notice that there was no plan behind them. So the
    pass reported that it had evaluated this account and had measured nothing
    at all — a confident "you are fine" from an engine whose entire design
    argument is that it would rather decline, and ``evaluated: false`` — the
    answer the contracts describe and the Risk Center needs to tell "nothing to
    report yet" from "nothing wrong" — was unreachable through the API.

    The two halves are asserted together because they are one claim: the account
    produces nothing **and** says why. A pass that wrote a row here would be
    fabricating one, and a pass that wrote nothing silently would have left an
    empty Risk Center indistinguishable from a clean one.
    """
    seed = await _seed(db_session)
    today = await _db_today(db_session)

    summary = await _service(db_session).evaluate(owner=seed.owner, today=today, window_days=WINDOW)

    assert await _risks(db_session, seed.owner.id) == []
    assert _risk_event_types(await _events(db_session, seed.owner.id)) == []
    assert await _recommendations(db_session, seed.owner.id) == []
    assert summary.risks_found == 0
    assert summary.risks_created == 0
    assert summary.risks_updated == 0
    assert summary.risks_resolved == 0
    assert summary.recommendations_created == 0
    assert summary.evaluated is False
    assert summary.reason_if_not_evaluated == (
        "Workload: No availability is configured, so there is no capacity to compare "
        "scheduled work against.; "
        "Estimation: Not enough historical data to estimate your typical task duration.; "
        "Consistency: There is no earlier period with recorded activity to compare against.; "
        "Scheduling: No work sessions are recorded in this window, so there is no schedule "
        "to inspect.; "
        "Deadline: Not assessed: no open task carries both a due date inside this window "
        "and an estimate.; "
        "Task: Not assessed: no open task is recorded as blocked or has 3 or more reschedules "
        "in its history."
    )
    history = await _evaluations(db_session, seed.owner.id)
    assert len(history) == 1
    assert history[0]["risks_found"] == 0


async def test_one_scheduled_session_is_enough_for_the_pass_to_have_judged_something(
    db_session: AsyncSession,
) -> None:
    """The other side of the boundary: a plan of one is still a plan.

    The cold-start test above asserts that an account with nothing at all
    answers ``evaluated: false``, and the obvious way to over-correct is to make
    the scheduling detector decline whenever it measured zero — which would turn
    every clean week into "not enough data" and quietly invert the flag into
    "no risk was found". This is the test that pins the boundary where it
    belongs: **one** session with no conflicts in it is a measurement of a real
    schedule, so the detector measures, scores zero, stores nothing, and the flag
    is true.

    Everything else about the account is deliberately left empty — no project, no
    task, no availability — so the scheduling detector is the *only* detector
    that can judge, and ``evaluated: true`` here rests on one answer rather than
    on a crowd. The reason sentence is asserted in full because its shape is the
    contract a client renders: the three unavailable detectors first, then the
    two that had no rows to look at, then the single zero.
    """
    seed = await _seed(db_session)
    today = await _db_today(db_session)
    await seed.work_session(day=today, minutes=60, start_hour=9)

    summary = await _service(db_session).evaluate(owner=seed.owner, today=today, window_days=WINDOW)

    assert await _risks(db_session, seed.owner.id) == []
    assert _risk_event_types(await _events(db_session, seed.owner.id)) == []
    assert summary.risks_found == 0
    assert summary.evaluated is True
    assert summary.reason_if_not_evaluated == (
        "Workload: No availability is configured, so there is no capacity to compare "
        "scheduled work against.; "
        "Estimation: Not enough historical data to estimate your typical task duration.; "
        "Consistency: There is no earlier period with recorded activity to compare against.; "
        "Deadline: Not assessed: no open task carries both a due date inside this window "
        "and an estimate.; "
        "Task: Not assessed: no open task is recorded as blocked or has 3 or more reschedules "
        "in its history.; "
        "Scheduling: measured no risk in this window."
    )


async def test_cancelled_sessions_are_not_a_plan_to_inspect(db_session: AsyncSession) -> None:
    """Rows the detector walks past are not rows it judged.

    A cancelled session was booked and then withdrawn, so it is not part of the
    plan — which is why :func:`~app.services.risk.detection._booked_minutes_by_task`
    already excludes it from a task's booked time. Counting it here would make
    ``sessions_considered`` disagree with the counts beside it: an account whose
    only session was cancelled would report a plan of one and a clean week,
    which is the same fabricated zero the cold-start case exists to prevent.

    The stored row is counted first, so the test cannot pass vacuously — if the
    fixture's session were not actually in the table, "no sessions to inspect"
    would be true for the wrong reason.
    """
    seed = await _seed(db_session)
    today = await _db_today(db_session)
    await seed.work_session(day=today, minutes=60, start_hour=9, status="cancelled")
    stored = await db_session.scalar(
        select(func.count()).select_from(WorkSession).where(WorkSession.owner_id == seed.owner.id)
    )
    assert stored == 1

    summary = await _service(db_session).evaluate(owner=seed.owner, today=today, window_days=WINDOW)

    assert await _risks(db_session, seed.owner.id) == []
    assert _risk_event_types(await _events(db_session, seed.owner.id)) == []
    assert summary.evaluated is False
    assert summary.reason_if_not_evaluated == (
        "Workload: No availability is configured, so there is no capacity to compare "
        "scheduled work against.; "
        "Estimation: Not enough historical data to estimate your typical task duration.; "
        "Consistency: There is no earlier period with recorded activity to compare against.; "
        "Scheduling: No work sessions are recorded in this window, so there is no schedule "
        "to inspect.; "
        "Deadline: Not assessed: no open task carries both a due date inside this window "
        "and an estimate.; "
        "Task: Not assessed: no open task is recorded as blocked or has 3 or more reschedules "
        "in its history."
    )


# ---------------------------------------------------------------------------
# (g) Tenancy
# ---------------------------------------------------------------------------


async def test_one_accounts_pass_never_sees_or_writes_anothers(db_session: AsyncSession) -> None:
    """Ada's risks are invisible to Grace, and Grace's pass cannot close them.

    Ownership is a predicate in every statement rather than a filter over a
    loaded page, and the resolution sweep is the reason that matters: a sweep
    that queried without the owner would close the *other* account's risks on the
    strength of this account's empty findings. So the ordering is the proof —
    Grace is evaluated over nothing before and after Ada's risks exist, and
    Ada's rows are compared by id and status, not merely counted.
    """
    ada = await _seed(db_session, username="ada")
    grace = await _seed(db_session, username="grace")
    today = await _db_today(db_session)
    ada_project = await ada.project()
    ada_task = await ada.task(
        project_id=ada_project.id,
        due_date=today + timedelta(days=1),
        estimated_minutes=300,
        actual_minutes=0,
    )
    await ada.work_session(day=today, minutes=120, task_id=ada_task.id)
    service = _service(db_session)

    empty_grace = await service.evaluate(owner=grace.owner, today=today, window_days=WINDOW)
    await service.evaluate(owner=ada.owner, today=today, window_days=WINDOW)
    ada_rows = await _risks(db_session, ada.owner.id)

    after = await service.evaluate(owner=grace.owner, today=today, window_days=WINDOW)

    assert empty_grace.risks_found == 0
    assert after.risks_found == 0
    assert await _risks(db_session, grace.owner.id) == []
    assert [row["id"] for row in await _risks(db_session, ada.owner.id)] == [
        row["id"] for row in ada_rows
    ]
    assert {row["status"] for row in ada_rows} == {RiskStatus.ACTIVE.value}

    # A second account's *own* passes are its own: three of them, none of which
    # raises anything or touches Ada's rows, which is also why the two evaluation
    # histories have different lengths.
    grace_third = await service.evaluate(owner=grace.owner, today=today, window_days=WINDOW)

    assert grace_third.risks_found == 0
    assert grace_third.by_type == {}
    assert await _risks(db_session, grace.owner.id) == []
    assert [row["id"] for row in await _risks(db_session, ada.owner.id)] == [
        row["id"] for row in ada_rows
    ]
    assert len(await _evaluations(db_session, ada.owner.id)) == 1
    assert len(await _evaluations(db_session, grace.owner.id)) == 3


# ---------------------------------------------------------------------------
# (h) Every detector, one fixture at a time
# ---------------------------------------------------------------------------


async def test_the_workload_detector_fires_on_an_overcommitted_plan(
    db_session: AsyncSession,
) -> None:
    """540 minutes booked against 420 declared: ``round(100 x 0.2857 / 0.5) = 57``.

    The declared capacity is exact by construction: seven availability rules of
    one hour each over a seven-day window is 420 minutes whatever day the window
    starts on. Three 180-minute sessions on three different days give 540
    scheduled minutes, a ratio of ``9/7``, an excess of ``2/7``, and a score of
    ``round(100 x 2/7 / 0.5) = round(57.1428...) = 57`` — ``high``.

    The sessions also have to leave every *other* detector at zero for the
    assertion to be about the workload detector alone: they start at 09:00
    inside the declared 09:00 to 10:00 window, on different days, so there is no
    overlap, nothing outside availability and no unbroken run.
    """
    seed = await _seed(db_session)
    today = await _db_today(db_session)
    await _availability(db_session, seed.owner.id)
    for offset in (3, 2, 1):
        await seed.work_session(day=today - timedelta(days=offset), minutes=180, start_hour=9)

    summary = await _service(db_session).evaluate(owner=seed.owner, today=today, window_days=WINDOW)

    rows = await _risks(db_session, seed.owner.id)
    workload = _only(rows, RiskType.WORKLOAD.value)
    assert workload["score"] == 57
    assert workload["severity"] == "high"
    # An account-level risk: about the whole plan, so there is no row to point at.
    assert workload["entity_type"] == "account"
    assert workload["entity_id"] is None
    assert workload["metadata_"]["scheduled_minutes"] == 540
    assert workload["metadata_"]["available_minutes"] == 420
    assert workload["metadata_"]["window_label"] == f"the 7 days to {today:%d %b %Y}"
    assert summary.by_type == {"workload": 1}


async def test_the_estimation_detector_fires_on_a_run_of_overruns(
    db_session: AsyncSession,
) -> None:
    """The brief's three pairs — 60/110, 90/150, 120/180 — score 83, ``critical``.

    ``mean_overrun = (0.8333 + 0.6667 + 0.5) / 3 = 0.6667``, and
    ``round(100 x 0.6667 / 0.8) = round(83.333) = 83``. Three is exactly the
    floor at which the detector stops declining, so the fixture also pins the
    boundary from the accepting side.

    All three complete on their own due date, which is what makes the deadline
    adherence figure read 100% — and why the project roll-up has nothing
    remaining to grade, leaving the estimation risk the only one stored.
    """
    seed = await _seed(db_session)
    today = await _db_today(db_session)
    project = await seed.project()
    for offset, (estimated, actual) in enumerate(((60, 110), (90, 150), (120, 180))):
        await seed.completed_task(
            day=today - timedelta(days=4 - offset),
            project_id=project.id,
            estimated_minutes=estimated,
            actual_minutes=actual,
        )

    summary = await _service(db_session).evaluate(owner=seed.owner, today=today, window_days=WINDOW)

    rows = await _risks(db_session, seed.owner.id)
    estimation = _only(rows, RiskType.ESTIMATION.value)
    assert estimation["score"] == 83
    assert estimation["severity"] == "critical"
    assert estimation["evidence_strength"] == "low"
    assert estimation["entity_type"] == "account"
    assert summary.by_type == {"estimation": 1}
    assert estimation["title"] == "Completed tasks ran 67% longer than estimated"


async def test_the_consistency_detector_fires_on_a_fall_against_the_earlier_window(
    db_session: AsyncSession,
) -> None:
    """One active day this week against seven last week: ``round(100 x 6/7) = 86``.

    ``current_rate = 1/7``, ``previous_rate = 7/7``, so the drop is
    ``(1 - 1/7) / 1 = 0.8571`` and the score is 86 — ``critical``. The two
    windows are the same length, which is what makes the two rates comparable at
    all.

    The earlier window needs a *started work session* before its consistency
    read is available; activity days alone come from the feed. Both are supplied,
    and the session is placed before the trailing window so it reaches neither
    the workload figure nor the planning signals.
    """
    seed = await _seed(db_session)
    today = await _db_today(db_session)
    await seed.work_session(day=today - timedelta(days=9), minutes=60, start_hour=9)
    for offset in range(WINDOW):
        await seed.activity(ActivityEvent.TASK_STARTED, day=today - timedelta(days=WINDOW + offset))
    await seed.activity(ActivityEvent.TASK_STARTED, day=today - timedelta(days=2))

    summary = await _service(db_session).evaluate(owner=seed.owner, today=today, window_days=WINDOW)

    rows = await _risks(db_session, seed.owner.id)
    consistency = _only(rows, RiskType.CONSISTENCY.value)
    assert consistency["score"] == 86
    assert consistency["severity"] == "critical"
    # Fourteen days of window on both sides is above the ten-sample floor for
    # medium evidence and below the thirty-sample floor for high.
    assert consistency["evidence_strength"] == "medium"
    assert consistency["metadata_"] == {
        "active_days": 1,
        "window_days": WINDOW,
        "previous_active_days": WINDOW,
        "previous_window_days": WINDOW,
        "current_rate": 0.1429,
        "previous_rate": 1.0,
    }
    assert summary.by_type == {"consistency": 1}


async def test_the_project_detector_fires_on_overdue_blocked_and_remaining_work(
    db_session: AsyncSession,
) -> None:
    """Ten overdue, five blocked, twenty unfinished: ``65``, ``high``.

    Each sub-signal saturates, so the weighted sum is exactly
    ``0.30 + 0.25 + 0.10 = 0.65`` and the score is 65. No target date is
    declared and nothing was completed, so the deadline and velocity
    sub-signals contribute nothing and the arithmetic has no float in it.

    The twenty open tasks deliberately carry **no estimates**, which is the
    other thing this fixture pins: ``_open_due_tasks`` excludes an unestimated
    task from the deadline scan and *reports* the exclusion instead, because
    scoring one against a remaining of zero would claim a task nobody estimated
    has no work outstanding. Only the ten overdue tasks carry a due date, so ten
    is the number reported — the other ten are skipped before the estimate is
    ever looked at.

    The five blocked tasks are measured twice, which is the whole point of the
    seventh detector: once as a count against the project and once as five
    separate rows pointing at the tasks themselves, each scoring ``0.60 x 100 =
    60``. A project roll-up can say *how many* of a project's tasks are blocked;
    it cannot say anything about the one a suggestion has to name.
    """
    seed = await _seed(db_session)
    today = await _db_today(db_session)
    project = await seed.project(name="Atlas")
    for _ in range(10):
        await seed.task(
            project_id=project.id, status=TaskStatus.TODO.value, due_date=today - timedelta(days=3)
        )
    for _ in range(5):
        await seed.task(project_id=project.id, status=TaskStatus.BLOCKED.value)
    for _ in range(5):
        await seed.task(project_id=project.id, status=TaskStatus.TODO.value)

    summary = await _service(db_session).evaluate(owner=seed.owner, today=today, window_days=WINDOW)

    rows = await _risks(db_session, seed.owner.id)
    project_risk = _only(rows, RiskType.PROJECT.value)
    assert project_risk["score"] == 65
    assert project_risk["severity"] == "high"
    assert project_risk["entity_type"] == "project"
    assert project_risk["entity_id"] == project.id
    assert project_risk["metadata_"]["overdue_tasks"] == 10
    assert project_risk["metadata_"]["blocked_tasks"] == 5
    assert project_risk["metadata_"]["remaining_tasks"] == 20
    assert summary.by_type == {"project": 1, "task": 5}
    assert summary.by_severity == {"high": 6}
    assert (
        "10 open task(s) have a due date inside this window but no estimate, and were not "
        "assessed" in (summary.reason_if_not_evaluated or "")
    )


async def test_the_scheduling_detector_fires_on_overlaps_and_after_deadline_bookings(
    db_session: AsyncSession,
) -> None:
    """Five overlapping sessions, all booked past the deadline: ``35 + 25 = 60``.

    ``overlap`` saturates at five, and the fixture writes six sessions at the
    same hour on the same day because the counter measures *sessions that start
    before the furthest end seen so far* — the first session has nothing to
    overlap, so five of the six are counted. At ``min(1, 5/5)`` the overlap
    sub-signal is worth its full ``0.35``, and ``after_deadline`` is a boolean
    worth ``0.25`` however many sessions are late, so the two sum to exactly
    ``60``. Nothing was declared available to fall outside and the run of six
    back-to-back sessions sits exactly on the consecutive baseline, contributing
    nothing — which is itself worth pinning, because that signal is a count and
    a run of exactly six is explicitly "a long run, not a signal".

    The deadline risk that comes alongside it is asserted too, because it is the
    clearest statement of the interaction between the two detectors: the sessions
    that are booked too late are also *not* booked in time, so the task scores
    ``round(100 x 1.0 x 0.4) = 40`` with nothing at all scheduled before its due
    date three days out.
    """
    seed = await _seed(db_session)
    today = await _db_today(db_session)
    project = await seed.project()
    task = await seed.task(
        project_id=project.id,
        due_date=today + timedelta(days=3),
        estimated_minutes=300,
        actual_minutes=0,
    )
    late = today + timedelta(days=4)
    for _ in range(6):
        await seed.work_session(day=late, minutes=60, task_id=task.id, start_hour=9)

    summary = await _service(db_session).evaluate(owner=seed.owner, today=today, window_days=WINDOW)

    rows = await _risks(db_session, seed.owner.id)
    scheduling = _only(rows, RiskType.SCHEDULING.value)
    assert scheduling["score"] == 60
    assert scheduling["severity"] == "high"
    assert scheduling["metadata_"]["overlapping_sessions"] == 5
    assert scheduling["metadata_"]["sessions_after_deadline"] == 6
    assert scheduling["metadata_"]["outside_availability_sessions"] == 0
    assert scheduling["metadata_"]["longest_consecutive_run"] == 6
    deadline = _only(rows, RiskType.DEADLINE.value)
    assert (deadline["score"], deadline["severity"]) == (40, "medium")
    assert deadline["metadata_"]["available_minutes"] == 0
    # No project risk: the fixture's single task is not blocked and not overdue,
    # so the project's own signals are half a point of remaining work and nothing
    # else — which is not a measurement worth storing. See the module docstring.
    assert summary.by_type == {"deadline": 1, "scheduling": 1}


async def test_a_blocked_task_is_a_task_risk_as_well_as_a_project_risk(
    db_session: AsyncSession,
) -> None:
    """Both readings of the same fact, from the two detectors that own each.

    This test used to assert the opposite — that no pass ever writes a
    ``task``-typed row — because ``RiskType.TASK`` was in the vocabulary and
    :data:`app.services.risk.recommendation.recommendation_rules` mapped two
    rules onto it while no detector could produce one. It is written as a positive
    assertion of the new behaviour: the same two blocked tasks are counted
    against their project **and** named one by one.

    The project side keeps its arithmetic: ``0.25 x 2/5`` blocked is worth 10 and
    the two unfinished tasks are below :data:`PROJECT_REMAINING_FLOOR`, so the
    remaining-work sub-signal contributes nothing at all — ``low``. The task side
    is new, and it is per row rather than per project — one ``task`` risk per
    blocked task, each ``0.60 x 100 = 60``, each pointing at the task the
    suggestion has to name. Asserting both halves in one fixture is the point: a
    detector that replaced the project roll-up rather than sitting beside it would
    show up here as a project row that had lost its blocked count.
    """
    seed = await _seed(db_session)
    today = await _db_today(db_session)
    project = await seed.project()
    blocked = [
        await seed.task(project_id=project.id, status=TaskStatus.BLOCKED.value) for _ in range(2)
    ]

    summary = await _service(db_session).evaluate(owner=seed.owner, today=today, window_days=WINDOW)

    rows = await _risks(db_session, seed.owner.id)
    project_risk = _only(rows, RiskType.PROJECT.value)
    assert project_risk["score"] == 10
    assert project_risk["severity"] == "low"
    assert project_risk["entity_type"] == "project"
    assert project_risk["metadata_"]["blocked_tasks"] == 2

    task_rows = [row for row in rows if row["risk_type"] == RiskType.TASK.value]
    assert sorted(row["entity_id"] for row in task_rows) == sorted(task.id for task in blocked)
    assert {(row["score"], row["severity"]) for row in task_rows} == {(60, "high")}
    assert summary.by_type == {"project": 1, "task": 2}
    assert summary.by_severity == {"low": 1, "high": 2}


async def test_a_blocked_task_carries_its_own_score_evidence_and_wording(
    db_session: AsyncSession,
) -> None:
    """One blocked task: ``0.60 x 100 = 60``, ``high``, and every input stored.

    The exact row, because this is the first time the ``task`` type reaches the
    Risk Center and everything about it is new: the score and its band, the
    evidence line that states the condition, the metadata the wording and the
    suggestions are later built from, and the title and description themselves.

    Evidence is asserted as the exact structure the API reads back rather than
    as prose: ``contribution`` is the points the line accounts for, and the two
    halves of this module's contract are that the lines add up to the score and
    that the stored metadata is what the recommendation rules re-read months
    later.

    The project risk is asserted alongside rather than ignored — one blocked task
    is ``0.25 x 1/5`` of the project's blocked signal and nothing else, because a
    single unfinished task is below the remaining-work floor, so the two detectors
    are looking at the same row from different sides and neither is a duplicate of
    the other.
    """
    seed = await _seed(db_session)
    today = await _db_today(db_session)
    project = await seed.project()
    task = await seed.task(
        project_id=project.id,
        title="Ship the quarterly report",
        status=TaskStatus.BLOCKED.value,
    )

    summary = await _service(db_session).evaluate(owner=seed.owner, today=today, window_days=WINDOW)

    rows = await _risks(db_session, seed.owner.id)
    stored = _only(rows, RiskType.TASK.value)
    assert stored["score"] == 60
    assert stored["severity"] == "high"
    assert stored["status"] == RiskStatus.ACTIVE.value
    assert stored["entity_type"] == "task"
    assert stored["entity_id"] == task.id
    # One authoritative row and no history behind it: the evidence strength is
    # `low` because the sample count is 1, which is what it is.
    assert stored["evidence_strength"] == "low"
    assert stored["metadata_"] == {
        "blocked": True,
        "reschedules": 0,
        "reschedule_threshold": 3,
        "title": "Ship the quarterly report",
        "signals": {"blocked": 1.0},
    }
    assert stored["evidence"] == [
        {
            "label": "Task is blocked",
            "detail": "recorded in the blocked status, so its remaining work cannot be placed",
            "contribution": 60.0,
        }
    ]
    assert stored["title"] == "Ship the quarterly report is blocked"
    assert stored["description"] == (
        "The task is recorded in the blocked status, so the work remaining on it cannot be "
        "placed until the block is cleared."
    )

    assert _by_type(rows) == {"project": 1, "task": 1}
    assert _only(rows, RiskType.PROJECT.value)["score"] == 5
    assert summary.by_type == {"project": 1, "task": 1}
    assert summary.by_severity == {"low": 1, "high": 1}


async def test_a_repeatedly_rescheduled_task_raises_the_break_it_down_rule(
    db_session: AsyncSession,
) -> None:
    """Four moves is the brief's own worked example, and it now reaches a rule.

    "IF task repeatedly rescheduled THEN recommend breaking task into subtasks"
    was unreachable before the task detector existed: the two rules mapped onto
    :data:`~app.models.enums.RiskType.TASK` had no risk of that type to fire on.
    So this test asserts the whole path rather than the risk alone — score 40
    (``0.40 x 100``, ``medium``), the evidence line carrying the count, and a
    ``BREAK_DOWN_TASK`` suggestion whose own reason quotes the same four.

    The negative half matters as much: ``COMPLETE_BLOCKED_TASK`` is registered on
    the same risk type, and this task is not blocked, so exactly one suggestion
    is written. A detector that passed a blocked flag it had not measured would
    produce two rows, one of them claiming work is blocked that is not.

    The project roll-up is the negative case for the *other* direction: the task
    is neither blocked nor overdue, so its project's only signal is half a point
    of remaining work and no project row is stored.
    """
    seed = await _seed(db_session)
    today = await _db_today(db_session)
    project = await seed.project()
    task = await seed.task(project_id=project.id, title="Rewrite the ingestion pipeline")
    await _reschedules(seed, task.id, 4, day=today - timedelta(days=2))

    summary = await _service(db_session).evaluate(owner=seed.owner, today=today, window_days=WINDOW)

    rows = await _risks(db_session, seed.owner.id)
    stored = _only(rows, RiskType.TASK.value)
    assert stored["score"] == 40
    assert stored["severity"] == "medium"
    assert stored["entity_id"] == task.id
    assert stored["metadata_"] == {
        "blocked": False,
        "reschedules": 4,
        "reschedule_threshold": 3,
        "title": "Rewrite the ingestion pipeline",
        "signals": {"rescheduled": 1.0},
    }
    assert stored["evidence"] == [
        {
            "label": "Repeatedly rescheduled",
            "detail": "4 reschedules recorded against this task, against a threshold of 3",
            "contribution": 40.0,
        }
    ]
    assert stored["title"] == "Rewrite the ingestion pipeline has been rescheduled 4 times"
    assert _by_type(rows) == {"task": 1}

    suggestions = await _recommendations(db_session, seed.owner.id)
    assert _recommendation_types(suggestions) == {RecommendationType.BREAK_DOWN_TASK.value: 1}
    assert summary.recommendations_created == 1
    break_down = suggestions[0]
    assert break_down["title"] == "Break Rewrite the ingestion pipeline into smaller pieces"
    assert break_down["priority"] == "medium"
    assert break_down["entity_type"] == "task"
    assert break_down["entity_id"] == task.id
    assert break_down["reason"] == (
        "4 reschedules of Rewrite the ingestion pipeline are recorded in its history. "
        "Recorded risk score 40 of 100 (medium severity, low evidence strength)."
    )


async def test_a_blocked_task_that_was_also_rescheduled_reaches_both_task_rules(
    db_session: AsyncSession,
) -> None:
    """The two conditions are not exclusive, and a task carrying both is one risk.

    ``0.60 + 0.40`` saturates the scale at 100 — ``critical`` — and both
    ``recommendation_rules`` entries for the ``task`` type fire against the same
    row: one suggestion to clear the block and one to break the task up. Two
    suggestions off one risk is the shape the generator's in-pass dedup exists to
    allow, because they are different types about the same task; the same type
    twice would have been a duplicate and would have been suppressed.

    The counts are pinned exactly rather than as "at least two", because the
    failure this test guards is a suggestion that never appears: both rules
    reaching a risk of the type they are registered for is the entire reason the
    detector exists.
    """
    seed = await _seed(db_session)
    today = await _db_today(db_session)
    project = await seed.project(name="Atlas")
    task = await seed.task(
        project_id=project.id,
        title="Rewrite the ingestion pipeline",
        status=TaskStatus.BLOCKED.value,
        estimated_minutes=240,
    )
    await _reschedules(seed, task.id, 3, day=today - timedelta(days=2))

    summary = await _service(db_session).evaluate(owner=seed.owner, today=today, window_days=WINDOW)

    rows = await _risks(db_session, seed.owner.id)
    stored = _only(rows, RiskType.TASK.value)
    assert stored["score"] == 100
    assert stored["severity"] == "critical"
    assert stored["title"] == (
        "Rewrite the ingestion pipeline is blocked and has been rescheduled 3 times"
    )
    assert stored["metadata_"]["signals"] == {"blocked": 1.0, "rescheduled": 1.0}
    assert [(line["label"], line["contribution"]) for line in stored["evidence"]] == [
        ("Task is blocked", 60.0),
        ("Repeatedly rescheduled", 40.0),
    ]
    assert summary.by_severity == {"low": 1, "critical": 1}

    suggestions = await _recommendations(db_session, seed.owner.id)
    # Three suggestions, not two: the project risk raises its own pair as well —
    # one for the project's blocked work and one to review its open signals —
    # and they are filed against the project rather than the task. Asserting the
    # full set is what keeps the two detectors distinguishable; a run that
    # collapsed them would show up here as one of these rows missing.
    assert _recommendation_types(suggestions) == {
        RecommendationType.BREAK_DOWN_TASK.value: 1,
        RecommendationType.COMPLETE_BLOCKED_TASK.value: 2,
        RecommendationType.REVIEW_PROJECT.value: 1,
    }
    assert summary.recommendations_created == 4
    # The two raised by the *task* risk, which is the pair this detector exists
    # to reach. Both file against the task itself, so a reader who follows
    # either one lands on the row it is about rather than on the project that
    # happens to hold it.
    on_the_task = [row for row in suggestions if row["entity_id"] == task.id]
    assert _recommendation_types(on_the_task) == {
        RecommendationType.BREAK_DOWN_TASK.value: 1,
        RecommendationType.COMPLETE_BLOCKED_TASK.value: 1,
    }
    by_type = {str(row["recommendation_type"]): row for row in on_the_task}
    assert by_type[RecommendationType.BREAK_DOWN_TASK.value]["priority"] == "critical"
    assert by_type[RecommendationType.COMPLETE_BLOCKED_TASK.value]["title"] == (
        "Resolve the blocked work in Atlas"
    )


async def test_a_task_moved_twice_is_not_a_task_risk(db_session: AsyncSession) -> None:
    """Below the threshold there is no candidate, no row, and a stated reason.

    Two moves is an adjustment; three is the number the contracts name and the
    number the break-it-down rule gates on. Asserting the boundary from the
    accepting side — and from the *rejecting* side in the same fixture, with a
    second task carrying no events at all — is what makes the threshold a fact
    rather than a hope: a detector that fired on any recorded move would produce
    a row for both tasks, and one that fired on three would produce a row for
    neither.

    The pass says why as well. An empty Risk Center that cannot explain itself
    reads as "nothing is wrong", which is the failure the coverage notes exist
    to prevent.

    The Risk Center is now empty rather than holding one ``project`` row, and
    that is the same fix seen from the other end: two unfinished tasks in an
    otherwise healthy project are the normal state of a project, and
    ``0.10 x 2/20 = 1`` point of "pressure" from them was a false positive built
    out of arithmetic. Two open tasks used to be worth a stored ``low`` risk and
    a "review the open signals on this project" suggestion; below
    :data:`PROJECT_REMAINING_FLOOR` they are worth neither, and what the project
    actually holds — no overdue work, no blocked work, no target date — is
    correctly nothing at all.
    """
    seed = await _seed(db_session)
    today = await _db_today(db_session)
    project = await seed.project()
    twice = await seed.task(project_id=project.id, title="Draft the release notes")
    never = await seed.task(project_id=project.id, title="Review the access list")
    await _reschedules(seed, twice.id, 2, day=today - timedelta(days=2))
    # The two are distinct rows in the same project, which is the fixture's own
    # claim: one task has a history and one does not, and the fact that two rows
    # are open in the project at all is what makes the project roll-up's own
    # arithmetic — and the absence of a risk because of it — worth asserting.
    assert never.id != twice.id

    summary = await _service(db_session).evaluate(owner=seed.owner, today=today, window_days=WINDOW)

    rows = await _risks(db_session, seed.owner.id)
    assert rows == []
    assert RiskType.TASK.value not in summary.by_type
    assert summary.by_type == {}
    assert "measured no risk in this window" in (summary.reason_if_not_evaluated or "")
    suggestions = await _recommendations(db_session, seed.owner.id)
    # Nothing at all, and in particular nothing filed against either task: a
    # detector that read Grace-style cross-account counts or dropped the
    # reschedule threshold would file a "break this task up" suggestion here.
    assert suggestions == []
    assert summary.recommendations_created == 0
    assert (
        "Task: Not assessed: no open task is recorded as blocked or has 3 or more "
        "reschedules in its history."
    ) in (summary.reason_if_not_evaluated or "")


async def test_task_risks_and_their_suggestions_stay_with_their_owner(
    db_session: AsyncSession,
) -> None:
    """Ada's blocked task is never counted, listed or closed on Grace's pass.

    The reschedule read is the one this detector assembles rather than delegates,
    and it is the place ownership is easiest to get wrong: it joins the event
    feed to the task table, and a query that scoped only one of the two would
    count another account's events. So the fixture gives Grace a task of her own
    with two recorded moves — enough to raise a risk if the threshold were read
    from anywhere but her own feed, not enough to raise one here — and Ada a
    blocked, thrice-moved task.

    The sweep is the other half. A pass that queried without the owner would
    close the *other* account's task risk on the strength of this account's empty
    findings, so Ada's rows are compared by id and status after Grace's passes
    rather than merely counted.
    """
    ada = await _seed(db_session, username="ada")
    grace = await _seed(db_session, username="grace")
    today = await _db_today(db_session)
    ada_project = await ada.project()
    ada_task = await ada.task(project_id=ada_project.id, status=TaskStatus.BLOCKED.value)
    await _reschedules(ada, ada_task.id, 3, day=today - timedelta(days=2))
    grace_project = await grace.project()
    grace_task = await grace.task(project_id=grace_project.id)
    await _reschedules(grace, grace_task.id, 2, day=today - timedelta(days=2))
    service = _service(db_session)

    await service.evaluate(owner=grace.owner, today=today, window_days=WINDOW)
    ada_summary = await service.evaluate(owner=ada.owner, today=today, window_days=WINDOW)
    ada_rows = await _risks(db_session, ada.owner.id)

    assert RiskType.TASK.value in ada_summary.by_type
    assert [row["entity_id"] for row in ada_rows if row["risk_type"] == RiskType.TASK.value] == [
        ada_task.id
    ]

    grace_again = await service.evaluate(owner=grace.owner, today=today, window_days=WINDOW)

    assert RiskType.TASK.value not in grace_again.by_type
    assert await _recommendations(db_session, grace.owner.id) == []
    assert [row["id"] for row in await _risks(db_session, ada.owner.id)] == [
        row["id"] for row in ada_rows
    ]
    assert {row["status"] for row in ada_rows} == {RiskStatus.ACTIVE.value}
    assert grace_task.id != ada_task.id


async def test_the_account_level_detectors_are_unaffected_by_the_task_detector(
    db_session: AsyncSession,
) -> None:
    """The workload row is byte-for-byte what it is with no blocked task present.

    A seventh detector can change the account-level figures in two ways that
    neither shows up in a count: by altering a shared read, or by displacing an
    account-level risk from a capped list. So this is the workload detector's own
    fixture — 540 minutes booked against 420 declared — with a blocked,
    thrice-moved task added, and the workload row is asserted in full: score, band,
    both inputs, and the entity columns that make it account-level.

    The three detectors that measured zero here are asserted absent by name. They
    are the ones a pass would be most tempted to fill in — a blocked task is a
    condition, and a condition is exactly what those detectors' zero rows would
    otherwise say.
    """
    seed = await _seed(db_session)
    today = await _db_today(db_session)
    await _availability(db_session, seed.owner.id)
    for offset in (3, 2, 1):
        await seed.work_session(day=today - timedelta(days=offset), minutes=180, start_hour=9)
    project = await seed.project()
    task = await seed.task(project_id=project.id, status=TaskStatus.BLOCKED.value)
    await _reschedules(seed, task.id, 3, day=today - timedelta(days=2))

    summary = await _service(db_session).evaluate(owner=seed.owner, today=today, window_days=WINDOW)

    rows = await _risks(db_session, seed.owner.id)
    workload = _only(rows, RiskType.WORKLOAD.value)
    assert workload["score"] == 57
    assert workload["severity"] == "high"
    assert workload["entity_type"] == "account"
    assert workload["entity_id"] is None
    assert workload["metadata_"]["scheduled_minutes"] == 540
    assert workload["metadata_"]["available_minutes"] == 420
    assert workload["metadata_"]["window_label"] == f"the 7 days to {today:%d %b %Y}"
    for absent in (RiskType.SCHEDULING, RiskType.ESTIMATION, RiskType.CONSISTENCY):
        assert absent.value not in summary.by_type
    # Every account-level row the pass wrote points at no row, which is what
    # makes "task-level" a distinction the two halves of the pass really keep.
    assert all(
        row["entity_type"] == "account" and row["entity_id"] is None
        for row in rows
        if row["risk_type"] == RiskType.WORKLOAD.value
    )
    assert _only(rows, RiskType.TASK.value)["entity_id"] == task.id
    assert summary.by_type == {"project": 1, "task": 1, "workload": 1}
    assert summary.by_severity == {"low": 1, "critical": 1, "high": 1}


async def test_unblocking_the_task_resolves_its_risk_and_makes_its_suggestion_moot(
    db_session: AsyncSession,
) -> None:
    """The brief's "the condition disappears" applied to the new detector.

    A blocked task is a row whose status can change between two passes, and
    nothing re-detecting an unblocked task will ever *mention* that the old risk
    stopped being true — the only evidence is its absence from this run's
    findings, which is what the sweep reads. The suggestion is expired in the
    same step: an open suggestion attached to a risk that no longer exists has
    nothing left to be about, and ``expired`` records that it was not declined,
    which is a different fact and a different label.
    """
    seed = await _seed(db_session)
    today = await _db_today(db_session)
    project = await seed.project(name="Atlas")
    task = await seed.task(project_id=project.id, status=TaskStatus.BLOCKED.value)
    # A second open task carrying a signal of its own, so the project's risk is
    # still a measurement after the block clears and `risks_resolved == 1` is
    # about the task risk rather than about the project going quiet as well.
    await _project_survivor(seed, project.id, today=today)
    service = _service(db_session)

    await service.evaluate(owner=seed.owner, today=today, window_days=WINDOW)
    opened = _only(await _risks(db_session, seed.owner.id), RiskType.TASK.value)
    assert opened["status"] == RiskStatus.ACTIVE.value

    task.status = TaskStatus.TODO.value
    task.updated_at = at(today, 14)
    db_session.add(task)
    await db_session.commit()
    summary = await service.evaluate(owner=seed.owner, today=today, window_days=WINDOW)

    closed = _only(await _risks(db_session, seed.owner.id), RiskType.TASK.value)
    assert closed["id"] == opened["id"]
    assert closed["status"] == RiskStatus.RESOLVED.value
    assert isinstance(closed["resolved_at"], datetime)
    assert summary.risks_resolved == 1
    assert RiskType.TASK.value not in summary.by_type
    # The suggestion filed against the task; the project's own is a different
    # identity and survives, because the project still has open signals.
    on_the_task = [
        row
        for row in await _recommendations(db_session, seed.owner.id)
        if row["entity_id"] == task.id
    ]
    assert _recommendation_types(on_the_task) == {RecommendationType.COMPLETE_BLOCKED_TASK.value: 1}
    assert on_the_task[0]["status"] == "expired"


# ---------------------------------------------------------------------------
# (i) Evidence and metadata
# ---------------------------------------------------------------------------


async def test_every_stored_risk_carries_its_evidence_and_its_inputs(
    db_session: AsyncSession,
) -> None:
    """Both are non-empty on every row — and asserted on the *account-level* ones.

    This is a regression guard for a defect that lived on exactly one path.
    ``risks.metadata`` is the column name and ``metadata_`` is the ORM attribute,
    because ``metadata`` is reserved on a declarative class; the null-identity
    upsert wrote the column name straight into ``Risk(**values)``, which SQLAlchemy
    accepted as a stray instance attribute and never wrote. Every account-level
    risk — workload, consistency and estimation, three of the six detectors, and
    every risk whose ``entity_id`` is null — silently stored ``{}`` while its
    row-level sibling stored correctly, so a workload risk could not explain its
    own score months later.

    The assertion is therefore aimed at the estimation risk alone: it is an
    account-level risk written through the null-identity path, and its metadata
    is the detector's own four keys, pinned exactly rather than checked for
    emptiness. The row-level assertion is included too, so a future change that
    breaks both paths cannot hide behind the one that was broken before.
    """
    seed = await _seed(db_session)
    today = await _db_today(db_session)
    await _availability(db_session, seed.owner.id)
    project = await seed.project()
    for offset, (estimated, actual) in enumerate(((60, 110), (90, 150), (120, 180))):
        day = today - timedelta(days=4 - offset)
        await seed.completed_task(
            day=day, project_id=project.id, estimated_minutes=estimated, actual_minutes=actual
        )
        await seed.work_session(day=day, minutes=60, start_hour=9)
    # Days 5 and 6 back rather than 3 and 2: the three completions above already
    # put a session on each of those days, and two sessions on one day overlap by
    # construction — which would raise a scheduling risk and make the pass about
    # three detectors rather than the two account-level ones under test.
    for offset in (6, 5):
        await seed.work_session(day=today - timedelta(days=offset), minutes=180, start_hour=9)

    await _service(db_session).evaluate(owner=seed.owner, today=today, window_days=WINDOW)

    rows = await _risks(db_session, seed.owner.id)
    assert _by_type(rows) == {"estimation": 1, "workload": 1}
    for row in rows:
        assert row["evidence"], f"{row['risk_type']} stored with no evidence lines"
        assert row["metadata_"], f"{row['risk_type']} stored with empty metadata"

    estimation = _only(rows, RiskType.ESTIMATION.value)
    assert estimation["metadata_"] == {
        "sample_count": 3,
        "mean_overrun": 0.6667,
        "under_estimation_count": 3,
        "over_estimation_count": 0,
    }
    # `contribution` is the score *before* `_result` rounds it, so this line
    # reads 83.33 where the stored `score` reads 83. Both are asserted as they
    # are rather than reconciled into one another: the scoring module rounds the
    # score once, at the end, and the evidence payload rounds each contribution
    # to two places, so the two can differ by up to half a point and there is
    # nothing a test can do about it that would not be hiding it.
    assert estimation["evidence"] == [
        {
            "label": "Average overrun on recent tasks",
            "detail": "67% longer than estimated across 3 completed task(s)",
            "contribution": 83.33,
        },
        {
            "label": "Direction of the error",
            "detail": "3 ran long, 0 ran short",
            "contribution": 0.0,
        },
    ]
    assert (
        estimation["description"]
        == "Across 3 completed task(s) in this window, the recorded duration ran about 67% "
        "longer than the estimate it was given."
    )


# ---------------------------------------------------------------------------
# (j) The summary's own arithmetic
# ---------------------------------------------------------------------------


async def test_the_summary_counts_add_up_and_the_run_is_timed(
    db_session: AsyncSession,
) -> None:
    """``risks_found == risks_created + risks_updated`` on every pass, timed.

    ``risks_resolved`` is deliberately **not** part of that identity, and this is
    the test that says so rather than leaving it to be inferred: a resolved risk
    is one the run did *not* re-detect, so it was never in ``risks_found``. The
    schema's own description of ``risks_found`` states the same thing
    (``app/schemas/risk.py``: "Not simply created plus updated"), and the two
    halves are asserted together here — a run that found two risks, refreshed one
    and closed one is the case where a reader is most tempted to add them up.

    ``duration_ms`` is asserted on both the returned summary and the stored row,
    because they are two answers to the same question and a divergence between
    them would make the trend history lie about what evaluation costs.
    """
    seed = await _seed(db_session)
    today = await _db_today(db_session)
    project = await seed.project()
    first_task = await seed.task(
        project_id=project.id,
        due_date=today + timedelta(days=1),
        estimated_minutes=300,
        actual_minutes=0,
    )
    await seed.task(
        project_id=project.id,
        due_date=today + timedelta(days=1),
        estimated_minutes=600,
        actual_minutes=0,
    )
    await seed.work_session(day=today, minutes=120, task_id=first_task.id)
    service = _service(db_session)

    opening = await service.evaluate(owner=seed.owner, today=today, window_days=WINDOW)
    # Two deadline risks, in two different bands. The 600-minute task has nothing
    # booked against it at all: gap_ratio 1.0 x urgency 0.7 = 70, which is `high`
    # (the ladder's `critical` floor is 75). The 300-minute one has 120 booked:
    # gap_ratio 0.6 x 0.7 = 42, `medium`. The project contributes nothing: two
    # unfinished tasks and no other signal is below the remaining-work floor, so
    # it measures a genuine zero and is measured rather than stored.
    assert opening.risks_found == 2
    assert opening.risks_created == 2
    assert opening.risks_updated == 0
    assert opening.by_severity == {"medium": 1, "high": 1}
    assert opening.duration_ms >= 0

    # One of the two deadlines becomes an ordinary open task with no due date,
    # which takes one deadline risk out of the run and leaves the sweep to close
    # it — the one case where the three counters are all non-zero, and the only
    # one where `risks_found` and `risks_resolved` describe disjoint sets.
    first_task.due_date = None
    db_session.add(first_task)
    await db_session.commit()
    mixed = await service.evaluate(owner=seed.owner, today=today, window_days=WINDOW)

    assert mixed.risks_found == 1
    assert mixed.risks_created == 0
    assert mixed.risks_updated == 1
    assert mixed.risks_resolved == 1
    assert mixed.risks_found == mixed.risks_created + mixed.risks_updated
    assert mixed.by_severity == {"high": 1}
    assert mixed.duration_ms >= 0

    # `risks_found` and `risks_resolved` are disjoint *sets*, not merely
    # different numbers — asserted on ids, because the two counters are equal
    # here (one row refreshed, one row closed) and a reader would otherwise take
    # the coincidence for the identity this test is about. The closed row is the
    # 300-minute task whose due date was removed; the live one is the 600-minute
    # task the pass re-detected.
    mixed_rows = await _risks(db_session, seed.owner.id)
    closed_rows = [row for row in mixed_rows if row["status"] == RiskStatus.RESOLVED.value]
    live_rows = [row for row in mixed_rows if row["status"] == RiskStatus.ACTIVE.value]
    assert len(closed_rows) == len(live_rows) == 1
    assert closed_rows[0]["id"] != live_rows[0]["id"]
    assert closed_rows[0]["entity_id"] == first_task.id
    assert live_rows[0]["entity_id"] != first_task.id

    history = await _evaluations(db_session, seed.owner.id)
    assert len(history) == 2
    for row, summary in zip(history, (opening, mixed), strict=True):
        assert row["risks_found"] == summary.risks_found
        assert row["risks_created"] == summary.risks_created
        assert row["risks_updated"] == summary.risks_updated
        assert row["risks_resolved"] == summary.risks_resolved
        assert row["by_severity"] == summary.by_severity
        assert row["by_type"] == summary.by_type
        assert row["duration_ms"] == summary.duration_ms
        assert row["duration_ms"] >= 0


# ---------------------------------------------------------------------------
# (k) A cap on what is written is not a claim that the rest has gone away
# ---------------------------------------------------------------------------


async def test_a_project_risk_beyond_the_write_cap_is_left_open_rather_than_closed(
    db_session: AsyncSession,
) -> None:
    """Twenty-six projects at risk, twenty-five rows, and the other one still live.

    One pass writes at most :data:`MAX_PROJECT_RISKS` project risks, keeping the
    highest scores. The defect this pins is what happened to the rest: they were
    dropped from the pass's output entirely, so the resolution sweep read their
    absence as "the condition went away" and closed them with the recorded reason
    *"the condition behind it was not detected in this evaluation"* — a sentence
    about a condition NEXUS had just scored and simply had no room for. A
    truncation is a limit on what is written, not a measurement of what is gone.

    Two passes, because a risk that was never written cannot be falsely closed:
    the regression needs a row that already exists. The first pass writes
    twenty-five of the twenty-six; the project it left out is identified **from
    the data** (the one project with no risk row) and then given a second overdue
    task. Its score rises above the rest, so the cap now keeps it and displaces a
    project that does have a row — which has to come out of the second pass
    active, untouched, and unresolved.

    Each project carries a single overdue task and nothing else, so every one of
    them scores exactly ``0.30 x 1/10 x 100 = 3``: no blocked work, no target
    date, no velocity, and one open task is below the remaining-work floor.
    """
    seed = await _seed(db_session)
    today = await _db_today(db_session)
    projects = []
    for index in range(26):
        project = await seed.project(name=f"Atlas {index:02d}")
        await seed.task(project_id=project.id, due_date=today - timedelta(days=3))
        projects.append(project)

    service = _service(db_session)
    first = await service.evaluate(owner=seed.owner, today=today, window_days=WINDOW)
    stored = [
        row for row in await _risks(db_session, seed.owner.id) if row["risk_type"] == "project"
    ]

    assert first.risks_found == first.risks_created == 25
    assert len(stored) == 25
    assert {row["score"] for row in stored} == {3}
    held_back = [
        project for project in projects if project.id not in {row["entity_id"] for row in stored}
    ]
    assert len(held_back) == 1

    # A second overdue task lifts that project to 6, which puts it inside the cap
    # and pushes one of the twenty-five out of it.
    await seed.task(project_id=held_back[0].id, due_date=today - timedelta(days=5))
    second = await service.evaluate(owner=seed.owner, today=today, window_days=WINDOW)
    after = [
        row for row in await _risks(db_session, seed.owner.id) if row["risk_type"] == "project"
    ]

    assert second.risks_created == 1
    assert second.risks_updated == 24
    assert second.risks_resolved == 0
    assert len(after) == 26
    assert {row["status"] for row in after} == {RiskStatus.ACTIVE.value}
    assert "were beyond the cap this pass writes" in (second.reason_if_not_evaluated or "")
    # The promoted project is the new row, and the displaced one is the row the
    # second pass did not touch: still there, still `active`, and still carrying
    # the score the first pass measured rather than one from a pass that skipped
    # it. `created == 1` and `updated == 24` against 26 rows is what says that.
    promoted = [row for row in after if row["entity_id"] == held_back[0].id]
    assert len(promoted) == 1
    assert promoted[0]["score"] == 6


async def test_a_blocked_task_beyond_the_write_cap_is_left_open_rather_than_closed(
    db_session: AsyncSession,
) -> None:
    """Twenty-six blocked tasks, twenty-five rows, and the twenty-sixth still live.

    The same defect on the task detector's own cap, and the case the audit names:
    with thirty blocked tasks, five of them never appeared on any later pass.
    Twenty-six blocked tasks all score ``0.60 x 100 = 60``, so the cap keeps
    twenty-five of them and the one it drops is reported as no longer blocked —
    every pass, forever, because a resolved risk is terminal until the condition
    returns.

    The promotion is three recorded reschedules rather than a second overdue
    task, because it is what makes the displaced row *writable at all*: the task
    that was left out now scores ``0.60 + 0.40 = 100`` and takes the cap's last
    slot, so one of the twenty-five that does have a row falls off it. Its row is
    the one the first pass wrote, and it must survive the second pass untouched.
    """
    seed = await _seed(db_session)
    today = await _db_today(db_session)
    project = await seed.project(name="Atlas")
    blocked = [
        await seed.task(project_id=project.id, status=TaskStatus.BLOCKED.value) for _ in range(26)
    ]

    service = _service(db_session)
    await service.evaluate(owner=seed.owner, today=today, window_days=WINDOW)
    stored = [row for row in await _risks(db_session, seed.owner.id) if row["risk_type"] == "task"]

    assert len(stored) == 25
    assert {row["score"] for row in stored} == {60}
    held_back = [task for task in blocked if task.id not in {row["entity_id"] for row in stored}]
    assert len(held_back) == 1

    await _reschedules(seed, held_back[0].id, 3, day=today - timedelta(days=2))
    second = await service.evaluate(owner=seed.owner, today=today, window_days=WINDOW)
    after = [row for row in await _risks(db_session, seed.owner.id) if row["risk_type"] == "task"]

    # 25 updates rather than 24: the twenty-four task risks the cap kept, plus
    # the project's own risk, which is refreshed on every pass — which is also
    # why this fixture's project is asserted through the task rows and not
    # through the summary's counters alone.
    assert second.risks_created == 1
    assert second.risks_updated == 25
    assert second.risks_resolved == 0
    assert len(after) == 26
    assert {row["status"] for row in after} == {RiskStatus.ACTIVE.value}
    assert {row["score"] for row in after} == {60, 100}
    assert "were beyond the cap this pass writes" in (second.reason_if_not_evaluated or "")


async def test_a_deadline_risk_beyond_the_write_cap_is_left_open_rather_than_closed(
    db_session: AsyncSession,
) -> None:
    """The nearest twenty-five deadlines are written; the twenty-sixth is not closed.

    The deadline scan is ordered by due date and the write cap keeps the nearest
    :data:`MAX_DEADLINE_TASKS`, so the task pushed out is normally the furthest one
    — which the scoring function grades lowest anyway. That is a good reason to
    keep the *nearest*, not a reason to close the rest: a task whose due date is
    four weeks out today can be the closest one tomorrow, and a row closed for
    not being in today's page cannot come back.

    Twenty-six tasks in twenty-six projects keeps the fixture to one detector:
    each project holds a single open task, which is below the remaining-work
    floor, so the run writes deadline risks and nothing else and the summary's
    counters are attributable to the cap alone. Every task scores
    ``1.0 x 0.7 x 1.0 = 70`` — gap ratio 1.0 with nothing booked, against the
    72-hour urgency rung, which a due date two days out always falls into
    whatever hour of the day the suite happens to run at.
    """
    seed = await _seed(db_session)
    today = await _db_today(db_session)
    tasks = []
    for index in range(26):
        project = await seed.project(name=f"Atlas {index:02d}")
        tasks.append(
            await seed.task(
                project_id=project.id,
                due_date=today + timedelta(days=2),
                estimated_minutes=300,
            )
        )

    service = _service(db_session)
    await service.evaluate(owner=seed.owner, today=today, window_days=WINDOW)
    stored = [
        row for row in await _risks(db_session, seed.owner.id) if row["risk_type"] == "deadline"
    ]

    assert len(stored) == 25
    assert {row["score"] for row in stored} == {70}
    held_back = [task for task in tasks if task.id not in {row["entity_id"] for row in stored}]
    assert len(held_back) == 1

    # Pulling the held-back task's date forward puts it at the head of the
    # nearest-first ordering, so the cap keeps it and displaces another row.
    held_back[0].due_date = today
    db_session.add(held_back[0])
    await db_session.commit()
    second = await service.evaluate(owner=seed.owner, today=today, window_days=WINDOW)
    after = [
        row for row in await _risks(db_session, seed.owner.id) if row["risk_type"] == "deadline"
    ]

    assert second.risks_created == 1
    assert second.risks_updated == 24
    assert second.risks_resolved == 0
    assert len(after) == 26
    assert {row["status"] for row in after} == {RiskStatus.ACTIVE.value}
    assert "were beyond the cap this pass writes" in (second.reason_if_not_evaluated or "")


async def test_a_candidate_scan_that_cannot_reach_its_end_leaves_that_type_alone(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A task that leaves the scanned page is not a task that stopped needing one.

    The write caps are only half of it: the *reads* are bounded too, and a
    candidate beyond the page is one this pass never judged. The deadline scan
    reads :data:`DEADLINE_SCAN_LIMIT` open, estimated tasks, so an account with
    more than that would have had every deadline risk behind the page reported as
    "the condition behind it was not detected" — for a task whose due date had
    not changed at all.

    The limit is lowered to one so the fixture needs three tasks rather than two
    hundred; the defect is the page boundary and not the number. Three tasks are
    written on the first pass, one of them then loses its due date, and the
    second pass — reading a page of one out of two candidates — must leave all
    three rows alone and say why. Closing the cleared task would be right by
    accident here and wrong by design in general: with a page of one and two
    candidates, NEXUS cannot tell which condition went away.
    """
    seed = await _seed(db_session)
    today = await _db_today(db_session)
    project = await seed.project(name="Atlas")
    tasks = [
        await seed.task(
            project_id=project.id,
            title=title,
            due_date=today + timedelta(days=2),
            estimated_minutes=300,
        )
        for title in ("First", "Second", "Third")
    ]

    service = _service(db_session)
    await service.evaluate(owner=seed.owner, today=today, window_days=WINDOW)
    stored = [
        row for row in await _risks(db_session, seed.owner.id) if row["risk_type"] == "deadline"
    ]
    assert len(stored) == 3

    monkeypatch.setattr(detection_module, "DEADLINE_SCAN_LIMIT", 1)
    tasks[2].due_date = None
    db_session.add(tasks[2])
    await db_session.commit()
    summary = await service.evaluate(owner=seed.owner, today=today, window_days=WINDOW)
    after = [
        row for row in await _risks(db_session, seed.owner.id) if row["risk_type"] == "deadline"
    ]

    assert summary.risks_resolved == 0
    assert len(after) == 3
    assert {row["status"] for row in after} == {RiskStatus.ACTIVE.value}
    assert (
        "Deadline: 1 open task(s) due inside this window were beyond the scan limit of 1 "
        "and were not assessed, so open deadline risks were left as they are."
    ) in (summary.reason_if_not_evaluated or "")


async def test_a_project_past_the_target_date_page_is_still_scored_against_its_target_date(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A project created before the page boundary keeps its ``days_to_deadline``.

    The project roll-up is unbounded — it carries a row for every project the
    owner has, including one with no tasks — while the target-date read paged once
    and stopped. So past the first hundred projects every one of them was graded
    as though it had declared no target date at all: ``days_to_deadline`` ``None``,
    the deadline sub-signal absent, twenty of its points gone. The same project
    measured 20 with a target date and 0 without one, decided purely by how early
    it happened to be created — a score determined by insertion order rather than
    by the project.

    The page is lowered to two rather than raising a hundred and one projects,
    because the defect is the page and not the number: the read now walks to a
    short page, so the third project is picked up by the second query. The
    projects carry ``created_at`` values a day apart because the page is ordered
    newest-first and a tie would make "which project was past the page" arbitrary
    rather than reproducible.

    The score is 20: five days to the target with work outstanding is the top
    deadline rung at ``0.20 x 1.0 x 100``, and one open task is below the
    remaining-work floor.
    """
    seed = await _seed(db_session)
    today = await _db_today(db_session)
    projects = []
    for index in range(3):
        project = await seed.project(
            name=f"Atlas {index:02d}", created_at=at(DAY + timedelta(days=index))
        )
        project.target_date = today + timedelta(days=5)
        db_session.add(project)
        projects.append(project)
    await db_session.commit()
    # The oldest project is the one the first page of two cannot reach, so it is
    # the one whose target date used to disappear.
    oldest = projects[0]
    await seed.task(project_id=oldest.id)

    monkeypatch.setattr(detection_module, "PROJECT_TARGET_PAGE_SIZE", 2)
    summary = await _service(db_session).evaluate(owner=seed.owner, today=today, window_days=WINDOW)

    stored = _only(await _risks(db_session, seed.owner.id), RiskType.PROJECT.value)
    assert stored["entity_id"] == oldest.id
    assert stored["score"] == 20
    assert stored["metadata_"]["days_to_deadline"] == 5
    assert summary.risks_found == 1
    # Nothing was left unread, so nothing about the boundary is reported: the
    # walk reached the end of a set of three.
    assert "target-date scan limit" not in (summary.reason_if_not_evaluated or "")


async def test_a_project_whose_target_date_the_scan_never_read_is_not_closed_as_resolved(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A project measured without its target date is a gap, not a resolution.

    The consequence of the page boundary, and the one that reaches the training
    data. A project past the scan limit measures a *lower* score — no deadline
    sub-signal at all — so a project whose only pressure was its approaching
    target date scores zero when its column is missing, and the sweep reads that
    zero as the condition having gone away and closes a live risk.

    So a truncated read marks ``RiskType.PROJECT`` unexamined for the pass: the
    project risk from the first pass survives the second one, and the run summary
    says that open project risks were left as they are and why. A stale row costs
    the user one extra card for one pass; a resolved risk says something false
    about their project, permanently.

    Both the page and the ceiling are lowered to two rather than raising a
    thousand and one projects: the ceiling is checked between pages, so a page of
    a hundred would still have read the whole set. The defect is the boundary and
    not its value.
    """
    seed = await _seed(db_session)
    today = await _db_today(db_session)
    projects = []
    for index in range(3):
        project = await seed.project(
            name=f"Atlas {index:02d}", created_at=at(DAY + timedelta(days=index))
        )
        project.target_date = today + timedelta(days=5)
        db_session.add(project)
        projects.append(project)
    await db_session.commit()
    oldest = projects[0]
    await seed.task(project_id=oldest.id)

    service = _service(db_session)
    await service.evaluate(owner=seed.owner, today=today, window_days=WINDOW)
    opened = _only(await _risks(db_session, seed.owner.id), RiskType.PROJECT.value)
    assert (opened["score"], opened["status"]) == (20, RiskStatus.ACTIVE.value)

    monkeypatch.setattr(detection_module, "PROJECT_TARGET_PAGE_SIZE", 2)
    monkeypatch.setattr(detection_module, "PROJECT_TARGET_SCAN_LIMIT", 2)
    summary = await service.evaluate(owner=seed.owner, today=today, window_days=WINDOW)
    after = _only(await _risks(db_session, seed.owner.id), RiskType.PROJECT.value)

    assert summary.risks_resolved == 0
    assert after["id"] == opened["id"]
    assert after["status"] == RiskStatus.ACTIVE.value
    assert after["score"] == 20
    assert (
        "Project: 1 project(s) were beyond the target-date scan limit of 2 and are scored "
        "without their target date, so open project risks were left as they are."
    ) in (summary.reason_if_not_evaluated or "")


async def test_a_brand_new_project_with_a_handful_of_open_tasks_is_not_at_risk(
    db_session: AsyncSession,
) -> None:
    """Three open tasks, nothing overdue, nothing blocked, no target date: no risk.

    The false positive the project detector raised from its remaining-work
    sub-signal. A project created this morning holds three tasks, they are all on
    time, and the count of them is evidence of nothing — but ``0.10 x 3/20 x 100
    = 1.5`` rounds to a stored score of 2, ``low``, whose entire description was
    "3 task(s) are unfinished". Inside a training matrix that is the worst kind of
    row there is: it attaches "this account is at risk" to a healthy account,
    which is a label rather than an observation.

    Below :data:`PROJECT_REMAINING_FLOOR` the sub-signal is absent rather than
    small, so the project measures a genuine zero: nothing is written, and the
    run summary still names the detectors that measured nothing, which is what
    separates an empty Risk Center that is an answer from one that is a silence.
    """
    seed = await _seed(db_session)
    today = await _db_today(db_session)
    project = await seed.project(name="Atlas")
    for _ in range(3):
        await seed.task(project_id=project.id)

    summary = await _service(db_session).evaluate(owner=seed.owner, today=today, window_days=WINDOW)

    assert await _risks(db_session, seed.owner.id) == []
    assert summary.risks_found == 0
    assert summary.by_type == {}
    assert summary.by_severity == {}
    assert "measured no risk in this window" in (summary.reason_if_not_evaluated or "")
    assert await _recommendations(db_session, seed.owner.id) == []


async def test_a_task_due_today_tomorrow_and_in_three_days_names_its_own_due_date(
    db_session: AsyncSession,
) -> None:
    """Three due dates, three suggestions, each naming the right one — first pass.

    The ``BLOCK_TIME`` title and reason are built from the date on the **task
    row**. They used to be rebuilt from ``deadline_in_hours`` — a gap measured at
    the start of the pass — added to ``detected_at``, the instant the row was
    written afterwards. Those are two different clocks, and adding a length taken
    from one to a timestamp taken from the other gives ``due_date + (written_at -
    measured_at)``: correct on an ordinary pass and a day out on one that began
    before midnight and wrote its rows after it. The wrong day was not merely
    displayed — the reason is persisted, so it was stored.

    Three tasks with nothing booked against them, so each fires the rule: 300
    minutes remaining, 0 booked, a gap of 5h. The dates are derived in the
    assertions from the database's own ``today`` rather than read back from the
    stored string, because the point is that the stored string *is* that date.
    """
    seed = await _seed(db_session)
    today = await _db_today(db_session)
    project = await seed.project(name="Atlas")
    offsets = {"Due today": 0, "Due tomorrow": 1, "Due in three days": 3}
    for title, offset in offsets.items():
        await seed.task(
            project_id=project.id,
            title=title,
            due_date=today + timedelta(days=offset),
            estimated_minutes=300,
        )

    summary = await _service(db_session).evaluate(owner=seed.owner, today=today, window_days=WINDOW)

    suggestions = await _recommendations(db_session, seed.owner.id)
    assert _recommendation_types(suggestions) == {RecommendationType.BLOCK_TIME.value: 3}
    assert summary.recommendations_created == 3
    by_title = {str(row["title"]): row for row in suggestions}

    for title, offset in offsets.items():
        due = f"{today + timedelta(days=offset):%d %b %Y}"
        row = by_title[f"Schedule another 5h for {title} before {due}"]
        assert due in str(row["description"])
        assert due in str(row["reason"])
        # One pass only. The date is read from the task's own column, so it is
        # right the first time and stays right after the user moves the date.
        assert row["status"] == "new"
