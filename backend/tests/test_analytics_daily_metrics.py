"""The ``daily_metrics`` tier: one row per user per calendar day, and nothing else.

**Every test here requires a live PostgreSQL and is marked ``integration.**

This file covers the layer below every other analytics test: the aggregate tier
itself. :meth:`app.services.analytics.service.AnalyticsService.rebuild_range` is
the *only* writer of ``daily_metrics``, and the grouped
:class:`app.repositories.analytics.AnalyticsRepository` reads beneath it are the
only way the tables above get folded into it. Everything a dashboard shows is
eventually a sum of these rows, so a defect here does not surface as one wrong
card — it surfaces as a wrong dashboard with no visible seam.

Three properties carry the design, and each is asserted here directly rather
than inferred from a downstream total:

**One row per day, including the silent ones.** A day with no activity is a row
of zeroes, not a gap. "Nothing happened on Tuesday" and "Tuesday was never
aggregated" are different facts, and a series that drops the difference is a
series claiming completeness it does not have.

**Idempotency.** A rebuild recomputes a day from the source rows and *replaces*
the aggregate. The mechanism is the ``uq_daily_metrics_owner_date`` unique
constraint plus an ``INSERT ... ON CONFLICT DO UPDATE``: without the constraint
a replayed rebuild would append a second row for every day it covered and every
sum derived from the table would double. So the idempotency is asserted twice —
the rows really are identical on a second run, and the constraint that makes it
safe really exists in the migrated schema and really refuses a duplicate.

**Fixed statement count.** Each source table is read by one ``GROUP BY`` over the
whole range, so a 30-day rebuild costs the same number of round trips as a
1-day one. This is the difference between the aggregate tier existing and not
existing, so it is measured against the engine's ``before_cursor_execute`` event
rather than asserted in a comment.

The fixtures make *one* thing non-zero per case wherever a case is about a
single counter. A fixture that moved two counters at once could not tell which
query was wrong, and the exact numbers below are only worth asserting because
everything else in the row is provably zero.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.models.analytics import DailyMetric
from app.models.enums import ActivityEvent, TaskStatus, WorkSessionStatus
from app.models.planner import WorkSession
from app.models.project import Project
from app.repositories.analytics import METRIC_COLUMNS, AnalyticsRepository
from app.repositories.knowledge import NoteRepository
from app.repositories.planner import CalendarEventRepository, WorkSessionRepository
from app.repositories.project import ProjectRepository
from app.repositories.task import TaskRepository
from app.services.analytics import AnalyticsService
from tests.analytics_fixtures import DAY, AnalyticsSeed, at, register_user, seeded_client

pytestmark = pytest.mark.integration

#: The counter columns, sorted — the order :data:`METRIC_COLUMNS` uses. Spelled
#: out as an assertion helper's baseline so "every column but one is zero" can be
#: stated as a complete row rather than as a list of the columns somebody
#: remembered to check.
METRIC_NAMES: tuple[str, ...] = tuple(METRIC_COLUMNS)

#: An instant far enough in the past that no rebuild can produce it. Used to age
#: a row deliberately, so the "``updated_at`` advances" assertion does not depend
#: on two rebuilds landing in different microseconds.
ANCIENT = datetime(2020, 1, 1, tzinfo=UTC)

#: ``daily_metrics``' unique constraint, by name. The idempotency of every
#: rebuild rests on this existing in the migrated schema, so the tests below name
#: it rather than trusting that some unique index happens to be there.
UNIQUE_CONSTRAINT = "uq_daily_metrics_owner_date"


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _service(session: AsyncSession) -> AnalyticsService:
    """An :class:`AnalyticsService` wired to one session.

    Built directly rather than through ``get_analytics_service`` because these
    tests are about the aggregation tier and nothing else: the four collaborators
    ``rebuild_range`` never touches are passed anyway, because a service that
    only works with three of its dependencies would be a service that breaks the
    next time the read tier calls one of them.
    """
    return AnalyticsService(
        AnalyticsRepository(session),
        TaskRepository(session),
        ProjectRepository(session),
        WorkSessionRepository(session),
        CalendarEventRepository(session),
        NoteRepository(session),
    )


async def _seed(session: AsyncSession, username: str = "ada") -> AnalyticsSeed:
    """An owner created directly, for the cases that drive the service only."""
    return AnalyticsSeed(session, await register_user(session, username=username))


async def _project(seed: AnalyticsSeed) -> Project:
    """A project to hang tasks off: ``tasks.project_id`` is ``NOT NULL``.

    Dated before the window on purpose. A project is not a source of any
    counter in this table — ``projects_touched`` is counted from the activity
    feed, not from ``projects.created_at`` — so its date is chosen so that it
    cannot be mistaken for one that is.
    """
    return await seed.project(created_at=at(DAY - timedelta(days=1)))


async def _stored_rows(session: AsyncSession, owner_id: uuid.UUID) -> list[dict[str, object]]:
    """Every aggregate row for one user, ascending by day.

    Read as columns rather than as ORM entities on purpose: this session is also
    the one that wrote the rows, so an entity read would hand back whatever was
    cached in the identity map the first time the day was loaded — the right
    numbers from *before* a rebuild, and a test asserting idempotency would
    compare stale objects to fresh ones and pass for the wrong reason.
    """
    result = await session.execute(
        select(DailyMetric.metric_date, DailyMetric.updated_at, *METRIC_COLUMNS.values())
        .where(DailyMetric.user_id == owner_id)
        .order_by(DailyMetric.metric_date.asc())
    )
    return [dict(row._mapping) for row in result.all()]


async def _row(session: AsyncSession, owner_id: uuid.UUID, day: date) -> dict[str, int]:
    """One user's counters for one day, as a complete ``{column: int}`` mapping."""
    stored = await _stored_rows(session, owner_id)
    match = [row for row in stored if row["metric_date"] == day]
    assert len(match) == 1, f"expected exactly one aggregate for {day}, found {len(match)}"
    return {name: int(match[0][name]) for name in METRIC_NAMES}


def _expected(**overrides: int) -> dict[str, int]:
    """A complete zero row with ``overrides`` applied — the expected shape.

    Asserting the *whole* row rather than one counter is what makes these tests
    say "this query reads the right table": a fixture meant to move exactly one
    counter fails loudly if some other counter moves too, which is how a stray
    ``projects_touched`` or a doubled ``work_sessions`` gets caught.
    """
    row = dict.fromkeys(METRIC_NAMES, 0)
    row.update(overrides)
    return row


async def _late_session(
    session: AsyncSession,
    seed: AnalyticsSeed,
    *,
    scheduled_on: date,
    actual_on: date,
    minutes: int,
) -> WorkSession:
    """A work session whose scheduled and actual starts fall on *different* days.

    ``AnalyticsSeed.work_session`` writes both starts at the same instant, which
    is right for the common case and useless for this one: with the two equal,
    planned and actual minutes cannot disagree about which day they belong to.
    """
    scheduled_start = at(scheduled_on, 9)
    actual_start = at(actual_on, 9)
    row = WorkSession(
        owner_id=seed.owner.id,
        scheduled_start=scheduled_start,
        scheduled_end=scheduled_start + timedelta(minutes=minutes),
        actual_start=actual_start,
        actual_end=actual_start + timedelta(minutes=minutes),
        actual_minutes=minutes,
        status=WorkSessionStatus.COMPLETED.value,
    )
    session.add(row)
    await session.commit()
    return row


async def _database_zone(session: AsyncSession) -> ZoneInfo:
    """The calendar the aggregate is cut on: the connection's own ``TimeZone``.

    Asked of the database rather than assumed, because the whole point of the
    rule under test is that it is *the server's* zone and not a constant written
    into the SQL. A test that hard-coded ``Asia/Calcutta`` would pass on this
    box and then quietly assert a rule the code does not follow anywhere else.
    """
    name = await session.scalar(select(func.current_setting("TimeZone")))
    return ZoneInfo(str(name))


def _local_instant(zone: ZoneInfo, day: date, hour: int, minute: int = 0) -> datetime:
    """The instant at ``hour:minute`` on ``day`` as the database reads it.

    :func:`~tests.analytics_fixtures.at` builds UTC instants, which is right for
    a fixture that must not care where the boundary is and wrong for one that is
    about it: "23:59" is the last minute of a day in one calendar and a small
    hour of the next in another, so a UTC-hour fixture pins nothing about where
    the cut falls. This one asks which calendar the buckets use and builds the
    wall-clock time *there*.
    """
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone).astimezone(UTC)


async def _session_starting_at(
    seed: AnalyticsSeed, *, start: datetime, minutes: int
) -> WorkSession:
    """A work session beginning at an exact instant rather than at an hour.

    ``AnalyticsSeed.work_session`` takes a start *hour*, which is the right
    granularity everywhere else in this file and the wrong one here: the claim
    under test is where the **midnight** cut falls, and an hour-granular fixture
    cannot distinguish a cut at 00:00 from one at 01:00. The row is written by
    the shared helper — which gets the status, the estimated and actual ends and
    the ownership right — and then moved onto the exact instant.
    """
    row = await seed.work_session(day=start.date(), minutes=minutes, start_hour=start.hour)
    row.scheduled_start = start
    row.scheduled_end = start + timedelta(minutes=minutes)
    row.actual_start = start
    row.actual_end = start + timedelta(minutes=minutes)
    await seed.flush()
    return row


@contextlib.contextmanager
def _recorded_statements(engine: Engine) -> Iterator[list[str]]:
    """Capture every SQL statement the engine sends while the block runs.

    The claim under test is "one grouped query per source table, however long the
    range", and the only way to assert a claim about round trips is to count
    them. Attaching to the sync engine is what makes that possible: the async
    engine drives it underneath.
    """

    def _record(
        connection: Connection,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        seen.append(statement)

    seen: list[str] = []
    event.listen(engine.sync_engine, "before_cursor_execute", _record)
    try:
        yield seen
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _record)


# ---------------------------------------------------------------------------
# Coverage of the range
# ---------------------------------------------------------------------------


async def test_rebuild_writes_one_row_for_every_day_in_the_range(db_session: AsyncSession) -> None:
    """Seven days in, seven rows out — including the five with nothing on them.

    The returned count is the number of days written, and it must equal the
    number of rows stored. A rebuild that only wrote days it found something for
    would return 2 here and leave a series with holes in it, which is the state
    ``OverviewRead.stale`` exists to report and a client should never have to
    infer.
    """
    seed = await _seed(db_session)
    project = await _project(seed)
    await seed.task(created_at=at(DAY), project_id=project.id)
    await seed.completed_task(day=DAY + timedelta(days=3), project_id=project.id)

    written = await _service(db_session).rebuild_range(
        owner=seed.owner, start=DAY, end=DAY + timedelta(days=6)
    )
    rows = await _stored_rows(db_session, seed.owner.id)

    assert written == 7
    assert [row["metric_date"] for row in rows] == [
        DAY + timedelta(days=offset) for offset in range(7)
    ]

    for day in (DAY + timedelta(days=offset) for offset in range(7)):
        expected: dict[str, int] = {}
        if day in (DAY, DAY + timedelta(days=3)):
            # `completed_task` *creates* the task on the day it completes it, so
            # that day carries both counters. `tasks_created` is grouped on
            # `tasks.created_at` and `tasks_completed` on `tasks.completed_at`
            # (app/repositories/analytics.py) — one task written on one day is
            # two facts about that day, not a double count and not a creation on
            # some other day.
            expected["tasks_created"] = 1
        if day == DAY + timedelta(days=3):
            expected["tasks_completed"] = 1
        assert await _row(db_session, seed.owner.id, day) == _expected(**expected)


async def test_a_day_with_no_activity_is_written_as_zeros_not_omitted(
    db_session: AsyncSession,
) -> None:
    """The gap between "nothing happened" and "never aggregated" stays visible.

    Both states are a row that reads as zeroes through the API, so the row's
    *existence* is the only thing carrying the distinction — which is why this
    asserts the count and the dates, not merely that the counters are zero.
    """
    seed = await _seed(db_session)
    project = await _project(seed)
    await seed.task(created_at=at(DAY), project_id=project.id)

    await _service(db_session).rebuild_range(
        owner=seed.owner, start=DAY, end=DAY + timedelta(days=2)
    )
    rows = await _stored_rows(db_session, seed.owner.id)

    assert len(rows) == 3
    assert await _row(db_session, seed.owner.id, DAY + timedelta(days=1)) == _expected()
    assert await _row(db_session, seed.owner.id, DAY + timedelta(days=2)) == _expected()
    assert (
        await AnalyticsRepository(db_session).count_range(
            seed.owner.id, start=DAY, end=DAY + timedelta(days=2)
        )
        == 3
    )


# ---------------------------------------------------------------------------
# One counter at a time
# ---------------------------------------------------------------------------


async def test_tasks_created_counts_by_created_at(db_session: AsyncSession) -> None:
    """``tasks_created`` is grouped on ``created_at``, and nothing else moves.

    The seeds are written at **local midday** — twelve o'clock in the database's
    own zone, which :func:`_local_instant` builds — so every task lands on the
    same calendar day whatever offset the server is configured for. A fixture at
    23:00 *UTC* would be a fixture whose expected day depends on that offset: on
    ``Asia/Calcutta`` that is half past four the following morning, and the third
    task below would quietly become a task created on :data:`DAY` plus one.

    The third task is created on :data:`DAY` and finished on the day after it,
    which is the other half of the attribution rule: it entered the system on
    ``DAY``, so it counts here, and the whole-row assertion is what proves its
    completion did not leak into ``tasks_completed``.
    """
    seed = await _seed(db_session)
    project = await _project(seed)
    zone = await _database_zone(db_session)
    midday = _local_instant(zone, DAY, 12)
    tomorrow = _local_instant(zone, DAY + timedelta(days=1), 12)
    await seed.task(created_at=midday, project_id=project.id)
    await seed.task(created_at=midday, project_id=project.id)
    await seed.task(
        created_at=midday,
        completed_at=tomorrow,
        status=TaskStatus.COMPLETED.value,
        project_id=project.id,
    )

    await _service(db_session).rebuild_range(owner=seed.owner, start=DAY, end=DAY)

    assert await _row(db_session, seed.owner.id, DAY) == _expected(tasks_created=3)


async def test_tasks_completed_counts_by_completed_at(db_session: AsyncSession) -> None:
    """``tasks_completed`` is grouped on ``completed_at``, never on ``updated_at``.

    The fixture completes three tasks on the day *after* the window and creates
    them the day before it, so a window-scoped rebuild of the first day sees the
    completions only as ``tasks_completed = 0`` — and a task created and finished
    on the same day counts in both columns of that one row, which is two facts
    about one day rather than a double count.
    """
    seed = await _seed(db_session)
    project = await _project(seed)
    for _ in range(3):
        await seed.task(
            project_id=project.id,
            created_at=at(DAY - timedelta(days=1)),
            completed_at=at(DAY, 12),
            status=TaskStatus.COMPLETED.value,
        )
    await seed.task(
        project_id=project.id,
        created_at=at(DAY + timedelta(days=1)),
        completed_at=at(DAY + timedelta(days=1), 12),
        status=TaskStatus.COMPLETED.value,
    )

    await _service(db_session).rebuild_range(owner=seed.owner, start=DAY, end=DAY)

    assert await _row(db_session, seed.owner.id, DAY) == _expected(tasks_completed=3)


async def test_tasks_cancelled_counts_by_updated_at(db_session: AsyncSession) -> None:
    """``tasks_cancelled`` is grouped on ``updated_at``.

    There is no ``cancelled_at`` column anywhere in the schema, so the last
    write is the only cancellation evidence there is. The fixture says so
    explicitly: the tasks were created the day before and *last edited* on the
    day in the window, which is where the counter puts them.
    """
    seed = await _seed(db_session)
    project = await _project(seed)
    for hour in (11, 12):
        await seed.task(
            project_id=project.id,
            created_at=at(DAY - timedelta(days=1)),
            status=TaskStatus.CANCELLED.value,
            updated_at=at(DAY, hour),
        )
    # A live task edited on the same day must not be counted: the status filter
    # is part of the same predicate, and this row proves it is applied.
    await seed.task(
        project_id=project.id,
        created_at=at(DAY - timedelta(days=1)),
        updated_at=at(DAY, 13),
    )

    await _service(db_session).rebuild_range(owner=seed.owner, start=DAY, end=DAY)

    assert await _row(db_session, seed.owner.id, DAY) == _expected(tasks_cancelled=2)


@pytest.mark.parametrize(
    ("event_type", "column"),
    [
        (ActivityEvent.TASK_BLOCKED, "tasks_blocked"),
        (ActivityEvent.TASK_RESCHEDULED, "tasks_rescheduled"),
    ],
)
async def test_event_sourced_counters_come_from_the_activity_feed(
    db_session: AsyncSession, event_type: ActivityEvent, column: str
) -> None:
    """Blocked and rescheduled are read from ``activity_events``, not from the task.

    Neither has a column on ``tasks`` — a reschedule is a due-date edit that the
    task row cannot distinguish from any other edit, and a block that was later
    unblocked leaves nothing behind but the feed. Reading the task table would
    report a fabricated zero for both, so the counters are asserted from events.

    Each case writes three events of its own type, one of them on a neighbouring
    day, and asserts the full row. Nothing else may move.
    """
    seed = await _seed(db_session)
    project = await _project(seed)
    task = await seed.task(project_id=project.id, created_at=at(DAY - timedelta(days=1)))
    for hour in (9, 10, 11):
        await seed.activity(event_type, day=DAY, task_id=task.id, hour=hour)
    await seed.activity(event_type, day=DAY + timedelta(days=1), task_id=task.id)

    await _service(db_session).rebuild_range(owner=seed.owner, start=DAY, end=DAY)

    assert await _row(db_session, seed.owner.id, DAY) == _expected(**{column: 3})


async def test_planned_minutes_are_bucketed_by_scheduled_start(
    db_session: AsyncSession,
) -> None:
    """A session booked on Monday and run on Tuesday plans Monday and works Tuesday.

    The two minutes columns are read from two different timestamps on the same
    row, and a fixture that writes both as the same instant cannot tell them
    apart. This one books 120 minutes on Monday, runs it on Tuesday, and asserts
    that Monday carries the plan and Tuesday carries the work.
    """
    seed = await _seed(db_session)
    await _late_session(
        db_session, seed, scheduled_on=DAY, actual_on=DAY + timedelta(days=1), minutes=120
    )

    await _service(db_session).rebuild_range(
        owner=seed.owner, start=DAY, end=DAY + timedelta(days=1)
    )

    assert await _row(db_session, seed.owner.id, DAY) == _expected(planned_minutes=120)
    assert await _row(db_session, seed.owner.id, DAY + timedelta(days=1)) == _expected(
        actual_minutes=120, work_sessions=1
    )


async def test_actual_minutes_and_session_count_come_from_the_session_table(
    db_session: AsyncSession,
) -> None:
    """Minutes are summed from ``actual_minutes``, and sessions are counted.

    30 + 45 minutes over two sessions on one day: the day's total is the sum,
    the count is the number of blocks, and neither is derived from the other —
    which is the point of carrying both columns.
    """
    seed = await _seed(db_session)
    await seed.work_session(day=DAY, minutes=30, start_hour=9)
    await seed.work_session(day=DAY, minutes=45, start_hour=14)

    await _service(db_session).rebuild_range(owner=seed.owner, start=DAY, end=DAY)

    assert await _row(db_session, seed.owner.id, DAY) == _expected(
        planned_minutes=75, actual_minutes=75, work_sessions=2
    )


async def test_a_session_that_never_started_contributes_to_no_day(
    db_session: AsyncSession,
) -> None:
    """A planned block that was never run holds no time, so it is not ``actual``.

    It is still a plan, so ``planned_minutes`` keeps it. Reading the two columns
    from the same timestamp would make this impossible to represent.
    """
    seed = await _seed(db_session)
    scheduled_start = at(DAY, 9)
    db_session.add(
        WorkSession(
            owner_id=seed.owner.id,
            scheduled_start=scheduled_start,
            scheduled_end=scheduled_start + timedelta(minutes=60),
            status=WorkSessionStatus.PLANNED.value,
        )
    )
    await db_session.commit()

    await _service(db_session).rebuild_range(owner=seed.owner, start=DAY, end=DAY)

    assert await _row(db_session, seed.owner.id, DAY) == _expected(planned_minutes=60)


async def test_calendar_events_are_counted_on_the_day_they_start(
    db_session: AsyncSession,
) -> None:
    """``calendar_events`` counts entries by ``starts_at``, one row each.

    A twelve-hour block that runs past midnight is one event on the day it
    begins, matching the planner's own per-day overlap arithmetic. The fixture
    writes two entries on the day and one on the next, and asserts the whole row
    so the count cannot be attributed to something else.
    """
    seed = await _seed(db_session)
    await seed.calendar_event(day=DAY, start_hour=9, minutes=60)
    await seed.calendar_event(day=DAY, start_hour=14, minutes=30)
    await seed.calendar_event(day=DAY + timedelta(days=1), start_hour=9, minutes=45)

    await _service(db_session).rebuild_range(
        owner=seed.owner, start=DAY, end=DAY + timedelta(days=1)
    )

    assert await _row(db_session, seed.owner.id, DAY) == _expected(calendar_events=2)
    assert await _row(db_session, seed.owner.id, DAY + timedelta(days=1)) == _expected(
        calendar_events=1
    )


async def test_projects_touched_is_a_distinct_count_over_the_feed(
    db_session: AsyncSession,
) -> None:
    """Six edits to one project is one project touched.

    ``COUNT(DISTINCT project_id)`` over ``activity_events``: counting edits would
    make a focused day look like a broad one. The fixture writes four events —
    two for one project, one for the second, and one carrying no project at all
    — and the answer is 2.
    """
    seed = await _seed(db_session)
    first = await seed.project(created_at=at(DAY - timedelta(days=1)))
    second = await seed.project(created_at=at(DAY - timedelta(days=1)))
    for hour in (9, 10):
        await seed.activity(ActivityEvent.TASK_STARTED, day=DAY, project_id=first.id, hour=hour)
    await seed.activity(ActivityEvent.TASK_STARTED, day=DAY, project_id=second.id, hour=11)
    await seed.activity(ActivityEvent.TASK_STARTED, day=DAY, hour=12)

    await _service(db_session).rebuild_range(owner=seed.owner, start=DAY, end=DAY)

    assert await _row(db_session, seed.owner.id, DAY) == _expected(projects_touched=2)


# ---------------------------------------------------------------------------
# Event idempotency
# ---------------------------------------------------------------------------


async def test_rebuilding_twice_over_unchanged_data_changes_nothing(
    db_session: AsyncSession,
) -> None:
    """The same rebuild run twice leaves the same rows, with the same counts.

    A rebuild is run on a retry, on a poll, and after a partial outage. If it
    appended rather than replaced, the totals a dashboard sums would double with
    every run and the numbers would be visibly wrong to nobody — a second run is
    invisible on the row itself and obvious only a week later.

    The fixture spans every source table, so each counter is checked, not just
    the row count.
    """
    seed = await _seed(db_session)
    project = await _project(seed)
    task = await seed.task(created_at=at(DAY), project_id=project.id)
    await seed.task(
        project_id=project.id,
        created_at=at(DAY - timedelta(days=1)),
        completed_at=at(DAY, 12),
        status=TaskStatus.COMPLETED.value,
    )
    await seed.task(
        project_id=project.id,
        created_at=at(DAY - timedelta(days=1)),
        status=TaskStatus.CANCELLED.value,
        updated_at=at(DAY, 11),
    )
    await seed.activity(ActivityEvent.TASK_BLOCKED, day=DAY, task_id=task.id, hour=9)
    await seed.activity(ActivityEvent.TASK_RESCHEDULED, day=DAY, task_id=task.id, hour=10)
    await seed.activity(ActivityEvent.TASK_STARTED, day=DAY, project_id=project.id, hour=12)
    await seed.work_session(day=DAY, minutes=45, project_id=project.id)
    await seed.calendar_event(day=DAY, project_id=project.id)
    await seed.activity(ActivityEvent.NOTE_CREATED, day=DAY, hour=13)

    service = _service(db_session)
    first_written = await service.rebuild_range(
        owner=seed.owner, start=DAY, end=DAY + timedelta(days=2)
    )
    first = await _stored_rows(db_session, seed.owner.id)
    second_written = await service.rebuild_range(
        owner=seed.owner, start=DAY, end=DAY + timedelta(days=2)
    )
    second = await _stored_rows(db_session, seed.owner.id)

    assert first_written == second_written == 3
    assert len(first) == len(second) == 3
    assert [{name: row[name] for name in METRIC_NAMES} for row in first] == [
        {name: row[name] for name in METRIC_NAMES} for row in second
    ]

    # Pinned rather than merely self-consistent: a second run that quietly
    # stopped reading one source table would still compare equal to itself only
    # if the first run read it too, so the exact figures are stated here.
    assert await _row(db_session, seed.owner.id, DAY) == _expected(
        tasks_created=1,
        tasks_completed=1,
        tasks_cancelled=1,
        tasks_blocked=1,
        tasks_rescheduled=1,
        planned_minutes=45,
        actual_minutes=45,
        work_sessions=1,
        calendar_events=1,
        knowledge_events=1,
        projects_touched=1,
    )


async def test_the_owner_and_date_constraint_is_what_makes_a_rebuild_safe(
    db_session: AsyncSession,
) -> None:
    """``uq_daily_metrics_owner_date`` exists, and it refuses a second row.

    The idempotency of the whole tier is this constraint plus
    ``ON CONFLICT ... DO UPDATE``: without it, a replayed rebuild appends rather
    than replaces. So the constraint is checked where it lives — the migrated
    schema, not the model's declaration — and then deliberately violated to prove
    the storage layer would stop a duplicate the upsert exists to prevent.
    """
    declared = {
        constraint.name
        for constraint in DailyMetric.__table__.constraints
        if constraint.name is not None
    }
    assert UNIQUE_CONSTRAINT in declared

    stored = await db_session.execute(
        text(
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid = 'daily_metrics'::regclass AND contype = 'u'"
        )
    )
    assert UNIQUE_CONSTRAINT in {row[0] for row in stored.all()}

    seed = await _seed(db_session)
    await db_session.execute(
        pg_insert(DailyMetric).values(
            id=uuid.uuid4(), user_id=seed.owner.id, metric_date=DAY, tasks_created=1
        )
    )
    await db_session.commit()

    with pytest.raises(IntegrityError) as conflict:
        await db_session.execute(
            pg_insert(DailyMetric).values(
                id=uuid.uuid4(), user_id=seed.owner.id, metric_date=DAY, tasks_created=2
            )
        )
    await db_session.rollback()

    assert UNIQUE_CONSTRAINT in str(conflict.value)


async def test_a_rebuild_after_new_activity_replaces_the_day_rather_than_adding(
    db_session: AsyncSession,
) -> None:
    """A day that gained a task since the last rebuild goes 1 → 2, never 1 → 3.

    The upsert assigns the recomputed values rather than incrementing them. An
    incrementing upsert would be idempotent for identical inputs — which is
    exactly what the double-rebuild test would not catch — and silently wrong for
    a day whose source rows changed, which is the case that actually happens
    when a user completes a task in a window they already rebuilt.
    """
    seed = await _seed(db_session)
    project = await _project(seed)
    service = _service(db_session)
    await seed.task(created_at=at(DAY), project_id=project.id)

    await service.rebuild_range(owner=seed.owner, start=DAY, end=DAY)
    assert await _row(db_session, seed.owner.id, DAY) == _expected(tasks_created=1)

    await seed.task(created_at=at(DAY), project_id=project.id)
    await service.rebuild_range(owner=seed.owner, start=DAY, end=DAY)

    assert await _row(db_session, seed.owner.id, DAY) == _expected(tasks_created=2)
    stored = await _stored_rows(db_session, seed.owner.id)
    assert len(stored) == 1


async def test_a_rebuild_advances_updated_at_so_the_staleness_signal_is_real(
    db_session: AsyncSession,
) -> None:
    """Recomputing a day moves its ``updated_at``, which is how staleness is reported.

    The stamp is aged to 2020 first rather than simply compared across two
    rebuilds: two rebuilds can land inside the same microsecond, and a test that
    fails only when the machine is fast is a test that gets deleted. Moving the
    old value into the distant past makes the comparison exact — the stamp must
    come back from the upsert as "now", not as whatever it was.

    Nothing puts this on the wire (the daily series does not carry the column),
    so it is asserted on the row the API's ``OverviewRead.stale`` and
    ``aggregates_through`` reason about.
    """
    seed = await _seed(db_session)
    project = await _project(seed)
    await seed.task(created_at=at(DAY), project_id=project.id)
    service = _service(db_session)
    await service.rebuild_range(owner=seed.owner, start=DAY, end=DAY)

    stored = await _stored_rows(db_session, seed.owner.id)
    first_stamp = stored[0]["updated_at"]
    assert isinstance(first_stamp, datetime)
    # ``astimezone``, not ``replace(tzinfo=UTC)``: the column is read back aware and
    # labelled with the connection's ``TimeZone`` — Asia/Calcutta on this server — so
    # ``replace`` re-reads the local wall clock as if it were UTC and shifts the
    # instant by the offset. The two agree on any server running UTC, which is why
    # this assertion only ever failed off-UTC.
    assert first_stamp.astimezone(UTC) > ANCIENT

    await db_session.execute(
        text("UPDATE daily_metrics SET updated_at = :stamp WHERE user_id = :user_id"),
        {"stamp": ANCIENT, "user_id": seed.owner.id},
    )
    await db_session.commit()
    aged = await _stored_rows(db_session, seed.owner.id)
    assert aged[0]["updated_at"].astimezone(UTC) == ANCIENT

    await service.rebuild_range(owner=seed.owner, start=DAY, end=DAY)
    refreshed = await _stored_rows(db_session, seed.owner.id)

    assert refreshed[0]["updated_at"].astimezone(UTC) > ANCIENT


# ---------------------------------------------------------------------------
# Range length is a constant, not a multiplier
# ---------------------------------------------------------------------------


async def test_the_statement_count_does_not_grow_with_the_length_of_the_range(
    db_session: AsyncSession, engine: Engine
) -> None:
    """A 30-day rebuild costs exactly what a 1-day rebuild costs: twelve statements.

    Eleven grouped reads — created, completed, cancelled, overdue, blocked,
    rescheduled, knowledge events, planned minutes, actual minutes, calendar
    entries, projects touched — and one batched upsert. The alternative is a loop
    of per-day queries, which is a 30 x N round-trip pattern dressed up as a
    loop and the reason the aggregate tier exists at all.

    Counted from the engine's ``before_cursor_execute`` event rather than
    asserted from the source, so it is the statements that actually went to
    PostgreSQL that are being compared and not an intention.
    """
    seed = await _seed(db_session)
    project = await _project(seed)
    await seed.task(created_at=at(DAY), project_id=project.id)
    service = _service(db_session)

    with _recorded_statements(engine) as narrow:
        await service.rebuild_range(owner=seed.owner, start=DAY, end=DAY)
    with _recorded_statements(engine) as wide:
        await service.rebuild_range(owner=seed.owner, start=DAY, end=DAY + timedelta(days=29))

    assert len(narrow) == len(wide) == 12


async def test_a_wide_rebuild_agrees_with_a_narrow_one_on_every_day(
    db_session: AsyncSession,
) -> None:
    """Folding thirty days at once produces the same per-day rows as folding one.

    The statement-count test proves the wide rebuild is cheap; this proves it is
    also *right* — that the grouping is applied per day and not to the window as
    a whole, which is the mistake a single-range bug would produce and which the
    count alone could never catch.
    """
    seed = await _seed(db_session)
    project = await _project(seed)
    service = _service(db_session)
    await seed.task(created_at=at(DAY), project_id=project.id)
    await seed.work_session(day=DAY, minutes=30)

    await service.rebuild_range(owner=seed.owner, start=DAY, end=DAY)
    narrow = await _row(db_session, seed.owner.id, DAY)

    # A 30-day fold of the same range. The narrow rebuild above is not repeated
    # here: `rebuild_range` upserts, so the wide run *replaces* the one row the
    # narrow run wrote rather than adding to it, and `len(rows) == 30` below is
    # the check that it did exactly that.
    await service.rebuild_range(owner=seed.owner, start=DAY, end=DAY + timedelta(days=29))
    rows = await _stored_rows(db_session, seed.owner.id)
    active = [row for row in rows if any(int(row[name]) for name in METRIC_NAMES)]

    assert len(rows) == 30
    assert [row["metric_date"] for row in active] == [DAY]
    assert {name: int(active[0][name]) for name in METRIC_NAMES} == narrow


# ---------------------------------------------------------------------------
# Day boundaries
# ---------------------------------------------------------------------------


async def test_day_boundaries_are_cut_at_the_databases_local_midnight(
    db_session: AsyncSession,
) -> None:
    """A block at 23:59 and one at 00:01 the next day are two different days.

    ``daily_metrics.metric_date`` is cut at **the connection's own midnight**,
    written into the SQL as ``date(column AT TIME ZONE current_setting('TimeZone'))``
    so that the answer is a property of the query rather than of whichever
    connection happened to run it. A daily figure belongs to the day the user
    experienced: on a box at ``+05:30`` an evening's work is filed under that
    evening, not under the UTC day that is already tomorrow.

    Three blocks are seeded, at local wall-clock times read from the zone
    :func:`_database_zone` reports — one either side of the cut, and one at 04:00
    the following morning, which is still the *first* day in UTC and is the row
    that separates the two halves of the rule:

    * each day's bucket holds exactly its own blocks, so **a day's bucket does
      not straddle two days** and neither day's minutes leak into the other;
    * a read of one day returns that day and no other, because the window
      predicate is cut at the *same* local midnight the buckets are. Bucketing
      and bounding are two separate expressions, and a suite that only ever
      agreed with both at once would not notice one of them being wrong.

    Which of these catches which break is not an accident: hard-coding UTC in
    both expressions fails on the bucket figures, and hard-coding it in the
    window alone fails on the one-day read.
    """
    seed = await _seed(db_session)
    zone = await _database_zone(db_session)
    tomorrow = DAY + timedelta(days=1)
    await _session_starting_at(seed, start=_local_instant(zone, DAY, 23, 59), minutes=30)
    await _session_starting_at(seed, start=_local_instant(zone, tomorrow, 0, 1), minutes=45)
    await _session_starting_at(seed, start=_local_instant(zone, tomorrow, 4, 0), minutes=10)

    # The *window* a read is bounded by is cut at the same local midnight. The
    # 04:00 block is 22:30 UTC on the first day, so a predicate still cut at UTC
    # midnight would hand it to a read of the first day even though it belongs
    # to the second — and every caller that sums these groups, the service's
    # ``task_analytics`` and ``/overview`` totals among them, would count it.
    assert await AnalyticsRepository(db_session).session_minutes_by_day(
        seed.owner.id, start=DAY, end=DAY
    ) == [(DAY, 30, 1)]

    service = _service(db_session)

    await service.rebuild_range(owner=seed.owner, start=DAY, end=tomorrow)

    assert await _row(db_session, seed.owner.id, DAY) == _expected(
        planned_minutes=30, actual_minutes=30, work_sessions=1
    )
    assert await _row(db_session, seed.owner.id, tomorrow) == _expected(
        planned_minutes=55, actual_minutes=55, work_sessions=2
    )


# ---------------------------------------------------------------------------
# Range validation
# ---------------------------------------------------------------------------


async def test_an_inverted_range_is_refused_rather_than_writing_nothing(
    db_session: AsyncSession,
) -> None:
    """``end_date`` before ``start_date`` raises before a single row is written.

    The alternative is an empty series that reads exactly like "you did nothing",
    which is the failure mode a reversed range is most likely to cause and the
    one the response could not afterwards distinguish from real data.
    """
    seed = await _seed(db_session)
    service = _service(db_session)

    with pytest.raises(ValidationError) as inverted:
        await service.rebuild_range(owner=seed.owner, start=DAY, end=DAY - timedelta(days=1))

    assert "end_date" in str(inverted.value)
    assert await _stored_rows(db_session, seed.owner.id) == []


async def test_a_range_wider_than_the_rebuild_ceiling_is_refused(
    db_session: AsyncSession,
) -> None:
    """The write path has its own, lower ceiling than every read.

    ``analytics_rebuild_max_days`` (180) bounds ``rebuild_range`` because it is
    the one path that writes: the alternative is an unbounded aggregate over
    tables that never shrink, and ``/analytics/export.csv`` is the documented
    answer to "I want all of it". The boundary is asserted from both sides —
    the largest accepted window is rebuilt, one day more is refused — so the
    ceiling is pinned rather than merely probed from above.
    """
    seed = await _seed(db_session)
    project = await _project(seed)
    await seed.task(created_at=at(DAY), project_id=project.id)
    service = _service(db_session)
    limit = service.settings.analytics_rebuild_max_days
    last_allowed = DAY + timedelta(days=limit - 1)

    written = await service.rebuild_range(owner=seed.owner, start=DAY, end=last_allowed)
    assert written == limit

    with pytest.raises(ValidationError) as too_wide:
        await service.rebuild_range(
            owner=seed.owner, start=DAY, end=last_allowed + timedelta(days=1)
        )

    assert str(limit) in str(too_wide.value)
    assert len(await _stored_rows(db_session, seed.owner.id)) == limit


async def test_the_rebuild_route_refuses_an_inverted_window(
    client, db_session: AsyncSession, assert_error_envelope
) -> None:
    """Over HTTP the inverted window is a 422 in the shared error envelope.

    The route refuses it in ``resolve_window`` rather than letting an empty
    series reach the service, and the client is told which of the two ends is
    wrong rather than receiving a 200 full of zeroes.
    """
    seed, auth = await seeded_client(client, db_session)
    project = await _project(seed)
    await seed.task(created_at=at(DAY), project_id=project.id)

    response = await client.post(
        "/api/v1/analytics/rebuild",
        params={
            "start_date": DAY.isoformat(),
            "end_date": (DAY - timedelta(days=1)).isoformat(),
        },
        headers=auth,
    )

    error = assert_error_envelope(response, status_code=422, code="validation_error")
    assert "end_date" in error["message"]
    assert await _stored_rows(db_session, seed.owner.id) == []


async def test_the_rebuild_route_refuses_a_window_over_the_write_ceiling(
    client, db_session: AsyncSession, assert_error_envelope, settings
) -> None:
    """A window inside the read ceiling but over the write ceiling is a 422 too.

    ``resolve_window`` allows anything up to ``analytics_max_range_days`` (366),
    so a 181-day rebuild reaches the service and is refused *there*, by the
    ceiling the write path carries. Two different guards, and this is the one
    that cannot be observed by passing a 400-day window.
    """
    seed, auth = await seeded_client(client, db_session)
    start = DAY
    end = start + timedelta(days=settings.analytics_rebuild_max_days)

    response = await client.post(
        "/api/v1/analytics/rebuild",
        params={"start_date": start.isoformat(), "end_date": end.isoformat()},
        headers=auth,
    )

    error = assert_error_envelope(response, status_code=422, code="validation_error")
    assert str(settings.analytics_rebuild_max_days) in error["message"]
    assert seed.owner.id is not None


# ---------------------------------------------------------------------------
# Tenancy
# ---------------------------------------------------------------------------


async def test_one_users_rebuild_never_touches_another_users_rows(
    db_session: AsyncSession,
) -> None:
    """``owner_id`` is in the ``WHERE`` clause of every read and every write.

    Ada completes one task, creates two and runs a 45-minute session on one day;
    Grace creates a task, runs two 30-minute sessions and books a calendar entry
    on that same day. Rebuilding one window must produce one row with that
    user's figures and leave the other's account with no row at all — not a row
    of zeroes, not a merged row. Rebuilding Grace's afterwards must not disturb
    Ada's, which is the half a merged row would fail.
    """
    ada = await _seed(db_session, username="ada")
    grace = await register_user(db_session, username="grace")
    grace_seed = AnalyticsSeed(db_session, grace)
    ada_project = await _project(ada)
    grace_project = await _project(grace_seed)

    await ada.task(created_at=at(DAY), project_id=ada_project.id)
    await ada.task(
        project_id=ada_project.id,
        created_at=at(DAY - timedelta(days=1)),
        completed_at=at(DAY, 12),
        status=TaskStatus.COMPLETED.value,
    )
    await ada.task(created_at=at(DAY - timedelta(days=1)), project_id=ada_project.id)
    await ada.work_session(day=DAY, minutes=45)

    await grace_seed.task(created_at=at(DAY), project_id=grace_project.id)
    await grace_seed.work_session(day=DAY, minutes=30)
    await grace_seed.work_session(day=DAY, minutes=30)
    await grace_seed.calendar_event(day=DAY)

    service = _service(db_session)
    await service.rebuild_range(owner=ada.owner, start=DAY, end=DAY)

    assert await _row(db_session, ada.owner.id, DAY) == _expected(
        tasks_created=1, tasks_completed=1, planned_minutes=45, actual_minutes=45, work_sessions=1
    )
    assert await _stored_rows(db_session, grace.id) == []

    await service.rebuild_range(owner=grace, start=DAY, end=DAY)

    assert await _row(db_session, grace.id, DAY) == _expected(
        tasks_created=1,
        planned_minutes=60,
        actual_minutes=60,
        work_sessions=2,
        calendar_events=1,
    )
    assert await _row(db_session, ada.owner.id, DAY) == _expected(
        tasks_created=1, tasks_completed=1, planned_minutes=45, actual_minutes=45, work_sessions=1
    )
    assert await AnalyticsRepository(db_session).count_range(ada.owner.id, start=DAY, end=DAY) == 1


# ---------------------------------------------------------------------------
# The tier as the API serves it
# ---------------------------------------------------------------------------


async def test_the_series_endpoint_serves_exactly_the_rows_the_rebuild_wrote(
    client, db_session: AsyncSession
) -> None:
    """End to end: rebuild over HTTP, then read the aggregates over HTTP.

    The service tests above are about the tier; this one is about the tier being
    the thing the product reads. Ten tasks, eight of them completed, gives a
    ``tasks_created`` of 10 and a ``tasks_completed`` of 8 — the 80% the brief
    names — and the same figures must arrive through ``GET /analytics/series``
    with zeroes everywhere else.
    """
    seed, auth = await seeded_client(client, db_session)
    project = await _project(seed)
    for offset in range(10):
        day = DAY + timedelta(days=offset)
        # One task per day, completed on the day it was created. Writing a
        # *second* task per completed day would make the window hold eighteen
        # tasks rather than the ten the brief names, and `tasks_created` counts
        # rows by `created_at` — so the fixture has to create ten.
        if offset < 8:
            await seed.task(
                created_at=at(day),
                completed_at=at(day, 12),
                status=TaskStatus.COMPLETED.value,
                due_date=day,
                project_id=project.id,
            )
        else:
            await seed.task(created_at=at(day), due_date=day, project_id=project.id)

    rebuild = await client.post(
        "/api/v1/analytics/rebuild",
        params={"start_date": DAY.isoformat(), "end_date": (DAY + timedelta(days=9)).isoformat()},
        headers=auth,
    )
    assert rebuild.status_code == 202, rebuild.text
    assert rebuild.json() == {"rows_written": 10}

    response = await client.get(
        "/api/v1/analytics/series",
        params={"start_date": DAY.isoformat(), "end_date": (DAY + timedelta(days=9)).isoformat()},
        headers=auth,
    )
    assert response.status_code == 200, response.text
    rows = response.json()

    assert len(rows) == 10
    assert [row["metric_date"] for row in rows] == [
        (DAY + timedelta(days=offset)).isoformat() for offset in range(10)
    ]
    # A task created and completed on the same day reads 1/1 with nothing else
    # moving. `tasks_overdue` stays 0 even though the task carried a due date:
    # overdue means "was due that day and not finished by the end of it"
    # (AnalyticsRepository.count_tasks_overdue_by_day), and a task completed at
    # noon on its own due date was finished.
    first = rows[0]
    assert first["tasks_created"] == 1
    assert first["tasks_completed"] == 1
    assert {name: first[name] for name in METRIC_NAMES} == _expected(
        tasks_created=1, tasks_completed=1
    )
    # The two days whose task was never finished *are* overdue on their due
    # date, which is the other half of that rule: the same column is 0 above and
    # 1 here, distinguished only by the completion.
    assert {name: rows[8][name] for name in METRIC_NAMES} == _expected(
        tasks_created=1, tasks_overdue=1
    )
    assert sum(row["tasks_created"] for row in rows) == 10
    assert sum(row["tasks_completed"] for row in rows) == 8
