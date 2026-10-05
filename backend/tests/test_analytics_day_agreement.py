"""Two surfaces, one day: what ``/overview`` says about late work must add up.

**Every test here requires a live PostgreSQL and is marked ``integration.**

``app/repositories/analytics.py`` cuts a calendar day at the *database's* local
midnight — ``date(col AT TIME ZONE current_setting('TimeZone'))`` — so a row is
filed under the day its owner experienced. The service that reads those rows sat
on the older UTC cut in three places, and every one of those places feeds a
number this file pins:

* :meth:`AnalyticsService._deadline_result` decides on-time vs late, which is
  ``OverviewRead.deadlines.late``;
* ``feature_snapshot`` reports ``task_age_days`` and ``time_of_day``, which are
  Phase 10 training inputs;
* the same snapshot's project-velocity window opens with a UTC midnight the
  surrounding local ``today`` then had to be subtracted from.

None of them are visible in isolation, because each disagrees with the repository
only inside the handful of hours a day when the two calendars name different
days. Every fixture below therefore lands a task inside that window on purpose:
a UTC-shaped instant on a ``+05:30`` server is on the *previous* UTC day and on
the current local one, and that single difference is what the old code got wrong.

The assertions are written as agreements rather than as constants. "``late == 1``"
would keep passing if both sides moved together; "the number of tasks the
dashboard calls overdue is the number it calls late" would not, which is the
property a user actually sees — one response, two cards, opposite verdicts.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import TaskStatus
from app.repositories.analytics import AnalyticsRepository
from app.repositories.knowledge import NoteRepository
from app.repositories.planner import CalendarEventRepository, WorkSessionRepository
from app.repositories.project import ProjectRepository
from app.repositories.task import TaskRepository
from app.services.analytics import AnalyticsService
from tests.analytics_fixtures import DAY, AnalyticsSeed, at, register_user

pytestmark = pytest.mark.integration

#: An hour inside the disagreement window, as a local wall-clock hour. On a
#: ``+05:30`` server an instant at local 02:00 is on the *previous* UTC day; the
#: fixtures below all land inside ``00:00``-``05:30`` for that reason. The window
#: is five and a half hours wide there and zero wide on a UTC host, so every test
#: here states what it expects in terms of the *server's* zone rather than
#: assuming ``Asia/Calcutta`` — a suite that passed here would otherwise prove
#: nothing on a box configured as UTC.
EARLY_MORNING = 2


def _service(session: AsyncSession) -> AnalyticsService:
    """The service wired to one session, as the aggregation tests build it."""
    return AnalyticsService(
        AnalyticsRepository(session),
        TaskRepository(session),
        ProjectRepository(session),
        WorkSessionRepository(session),
        CalendarEventRepository(session),
        NoteRepository(session),
    )


async def _seed(session: AsyncSession, username: str = "ada") -> AnalyticsSeed:
    return AnalyticsSeed(session, await register_user(session, username=username))


async def _database_zone(session: AsyncSession) -> ZoneInfo:
    """The calendar every day cut in this file is measured against."""
    name = await session.scalar(select(func.current_setting("TimeZone")))
    return ZoneInfo(str(name))


def _local_instant(zone: ZoneInfo, day: date, hour: int, minute: int = 0) -> datetime:
    """The instant at ``hour:minute`` on ``day`` *in the server's own zone*.

    Built from the zone rather than with
    :func:`~tests.analytics_fixtures.at`, which returns UTC instants. ``at`` is
    right for a fixture that must not care where the boundary is and wrong for
    one that is entirely about it: "02:00" is a small hour of the day in one
    calendar and the evening before in another.
    """
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone).astimezone(UTC)


def _assert_zone_straddles_utc(zone: ZoneInfo, day: date, hour: int) -> None:
    """Fail loudly unless the fixture is actually inside the disagreement window.

    Every test below is a no-op on a UTC-configured server, and a suite that
    silently stops testing anything when the server zone changes is worse than no
    suite. Skipping states that plainly; it does not quietly pass.
    """
    instant = _local_instant(zone, day, hour)
    if instant.astimezone(UTC).date() == instant.astimezone(zone).date():
        pytest.skip(f"server zone {zone} does not straddle UTC at {day} {hour:02d}:00")


async def _session_starting_at(
    seed: AnalyticsSeed, *, start: datetime, minutes: int, task_id=None
) -> None:
    """A work session beginning at an exact instant rather than at an hour.

    ``AnalyticsSeed.work_session`` takes a start *hour* and builds it in UTC,
    which cannot express "02:00 in the server's calendar" at all.
    """
    row = await seed.work_session(day=start.date(), minutes=minutes, task_id=task_id)
    row.scheduled_start = start
    row.scheduled_end = start + timedelta(minutes=minutes)
    row.actual_start = start
    row.actual_end = start + timedelta(minutes=minutes)
    await seed.flush()


# ---------------------------------------------------------------------------
# `/overview`: tasks_overdue and deadlines.late are the same fact
# ---------------------------------------------------------------------------


async def test_overview_never_calls_the_same_task_overdue_and_on_time(
    db_session: AsyncSession,
) -> None:
    """One response must not report a task as both overdue and on time.

    The two figures are computed from the same ``tasks`` rows by different
    expressions. ``tasks_overdue`` counts a completion as late when
    ``local_day(completed_at) > due_date``; ``deadlines.late`` used to decide the
    same question with ``completed_at.astimezone(UTC).date()``. For five and a
    half hours of every day on a ``+05:30`` server those two answers differ, and
    ``/overview`` serves both — so the dashboard told the user their task was
    overdue on one card and punctual on another, at the same moment, from the
    same row.

    The fixture is the disagreement exactly: a task due on :data:`DAY` and
    finished at :data:`EARLY_MORNING` on the *following* local day, which is still
    :data:`DAY` in UTC. The window holds both its due date and its completion, so
    the two totals are comparable without any allowance for rows belonging to one
    side of the window only.

    The assertion is the agreement, not the literal ``1``: a dashboard whose two
    cards disagree is broken whichever way the numbers happen to fall, and a test
    that only pinned ``late == 1`` would keep passing if both cards were moved to
    the same wrong rule together.
    """
    seed = await _seed(db_session)
    zone = await _database_zone(db_session)
    tomorrow = DAY + timedelta(days=1)
    _assert_zone_straddles_utc(zone, tomorrow, EARLY_MORNING)

    project = await seed.project(created_at=at(DAY - timedelta(days=1)))
    late = await seed.task(
        project_id=project.id,
        status=TaskStatus.COMPLETED.value,
        created_at=_local_instant(zone, DAY, 12),
        completed_at=_local_instant(zone, tomorrow, EARLY_MORNING),
        due_date=DAY,
    )
    # A second task finished inside the same early-morning block but *on* its due
    # date, so the window holds one on-time completion as well as one late one.
    # Without it the pair of totals could agree at 1 while disagreeing about
    # which task was which.
    on_time = await seed.task(
        project_id=project.id,
        status=TaskStatus.COMPLETED.value,
        created_at=_local_instant(zone, DAY, 12),
        completed_at=_local_instant(zone, tomorrow, EARLY_MORNING),
        due_date=tomorrow,
    )
    assert late.id != on_time.id

    service = _service(db_session)
    await service.rebuild_range(owner=seed.owner, start=DAY, end=tomorrow)
    read = await service.overview(owner=seed.owner, start=DAY, end=tomorrow)

    overdue = next(point.current for point in read.totals if point.label == "tasks_overdue")

    # The repository files the late task on the day it was *due* and the on-time
    # task on the day it was due too — one overdue, zero the other — so the
    # window's overdue total is exactly the number of late completions.
    assert overdue == read.deadlines.late
    assert (overdue, read.deadlines.late, read.deadlines.on_time) == (1, 1, 1)
    assert read.deadlines.still_overdue == 0


async def test_overview_agrees_about_a_task_completed_in_the_early_morning_of_its_due_day(
    db_session: AsyncSession,
) -> None:
    """The other half of the window: due *after* the early-morning completion.

    Here the local calendar puts the completion and the deadline on the same day,
    so the task is on time — and a UTC cut that read the completion as the day
    before would also call it on time, by arithmetic that happens to land the
    right way round. It is asserted anyway because it is the case that fails if
    the fix is made by shifting the comparison rather than by cutting the day
    where the rest of the module cuts it, and because it pins the direction of
    the rule: nothing here is allowed to report a task as *worse* than it was.
    """
    seed = await _seed(db_session)
    zone = await _database_zone(db_session)
    _assert_zone_straddles_utc(zone, DAY, EARLY_MORNING)

    project = await seed.project(created_at=at(DAY - timedelta(days=1)))
    await seed.task(
        project_id=project.id,
        status=TaskStatus.COMPLETED.value,
        created_at=_local_instant(zone, DAY - timedelta(days=1), 12),
        completed_at=_local_instant(zone, DAY, EARLY_MORNING),
        due_date=DAY,
    )

    service = _service(db_session)
    await service.rebuild_range(owner=seed.owner, start=DAY, end=DAY)
    read = await service.overview(owner=seed.owner, start=DAY, end=DAY)

    overdue = next(point.current for point in read.totals if point.label == "tasks_overdue")

    assert overdue == 0
    assert (read.deadlines.on_time, read.deadlines.late) == (1, 0)


# ---------------------------------------------------------------------------
# `/analytics/feature-snapshot`: the ML inputs cut in the same calendar
# ---------------------------------------------------------------------------


async def test_task_age_days_counts_from_the_day_the_row_is_filed_under(
    db_session: AsyncSession,
) -> None:
    """``task_age_days`` must equal the distance to the day's own bucket.

    The row is bucketed at the database's local midnight, so "how old is this
    task" has to be measured from the same day the bucket says it was created
    on. It used to be ``(today - created_at.astimezone(UTC).date()).days`` — a
    UTC date subtracted from a *local* ``today`` — which reports a task created
    in the early hours of this morning as two days old.

    Stated as an identity rather than as a constant: ``task_age_days`` is exactly
    the number of days between the bucket that holds the creation and today. The
    literal here pins the same thing from the other side, so a fix that moved the
    bucket and not the feature would break one of the two.
    """
    seed = await _seed(db_session)
    metrics = AnalyticsRepository(db_session)
    zone = await _database_zone(db_session)
    today = await metrics.today()
    created_day = today - timedelta(days=1)
    _assert_zone_straddles_utc(zone, created_day, EARLY_MORNING)

    project = await seed.project(created_at=at(DAY))
    task = await seed.task(
        project_id=project.id,
        created_at=_local_instant(zone, created_day, EARLY_MORNING),
    )

    service = _service(db_session)
    await service.rebuild_range(owner=seed.owner, start=created_day, end=created_day)
    buckets = await metrics.count_tasks_by_day(seed.owner.id, start=created_day, end=created_day)

    # The creation really is filed under `created_day`, and not under the UTC day
    # before it — otherwise the identity below would be asserting agreement
    # between two rows that are not the same row.
    assert buckets == [(created_day, 1)]

    snapshot = await service.feature_snapshot(owner=seed.owner, task_id=task.id)
    features = snapshot["features"]

    assert features["task_age_days"] == 1
    assert features["task_age_days"] == (today - buckets[0][0]).days


async def test_time_of_day_reports_the_hour_the_work_actually_happened(
    db_session: AsyncSession,
) -> None:
    """``time_of_day`` is an hour in the user's calendar, not in Greenwich's.

    It is a training input, so the zone it is expressed in is part of the label:
    "somebody works at 23:30" and "somebody works at 18:00" are different
    populations. The feature used to take ``astimezone(UTC).hour``, so on a
    ``+05:30`` server a late-evening session was labelled five and a half hours
    early and every night worker was folded into the evening peak.

    Asserted against the zone's own offset rather than a hard-coded ``23`` — the
    number is only as meaningful as the calendar it was read in, and a test that
    hard-coded it would pass on this box and assert a rule the code does not
    follow on any other.
    """
    seed = await _seed(db_session)
    zone = await _database_zone(db_session)
    today = await AnalyticsRepository(db_session).today()
    day = today - timedelta(days=1)

    project = await seed.project(created_at=at(DAY))
    task = await seed.task(project_id=project.id, created_at=at(DAY))
    start = datetime(day.year, day.month, day.day, 23, 30, tzinfo=zone).astimezone(UTC)
    await _session_starting_at(seed, start=start, minutes=30, task_id=task.id)

    features = (await _service(db_session).feature_snapshot(owner=seed.owner, task_id=task.id))[
        "features"
    ]

    assert features["time_of_day"] == 23
    assert features["time_of_day"] == start.astimezone(zone).hour


async def test_the_velocity_window_opens_at_the_local_midnight_thirty_days_back(
    db_session: AsyncSession,
) -> None:
    """``project_velocity`` counts from local midnight, not from a UTC one.

    The window opens at ``datetime.combine(today - 30d, time.min, tzinfo=UTC)``
    while ``today`` itself is local. On a ``+05:30`` server that start instant is
    half past five in the morning of the thirtieth day — so the first five and a
    half hours of the window were excluded and the feature was quietly a 29.8-day
    count, disagreeing with the ``task_age_days`` beside it in the same row.

    Two completions straddle the boundary the two rules draw. One is finished
    three hours into the thirtieth *local* day: inside the window under the rule,
    outside it under the UTC spelling. The other is finished on the evening of
    the twenty-ninth, which is outside both. Exactly one may be counted.
    """
    seed = await _seed(db_session)
    metrics = AnalyticsRepository(db_session)
    zone = await _database_zone(db_session)
    today = await metrics.today()
    boundary_day = today - timedelta(days=30)
    _assert_zone_straddles_utc(zone, boundary_day, EARLY_MORNING)

    project = await seed.project(created_at=at(DAY))
    inside = await seed.task(
        project_id=project.id,
        status=TaskStatus.COMPLETED.value,
        created_at=_local_instant(zone, boundary_day, EARLY_MORNING + 1),
        completed_at=_local_instant(zone, boundary_day, EARLY_MORNING + 1),
    )
    outside = await seed.task(
        project_id=project.id,
        status=TaskStatus.COMPLETED.value,
        created_at=_local_instant(zone, boundary_day - timedelta(days=1), 23),
        completed_at=_local_instant(zone, boundary_day - timedelta(days=1), 23),
    )
    assert inside.id != outside.id

    features = (await _service(db_session).feature_snapshot(owner=seed.owner, task_id=inside.id))[
        "features"
    ]

    # Three hours into the thirtieth day is inside a thirty-day window; the
    # evening before it is not. Counting both would make the feature a 31-day
    # count, which is the error in the other direction.
    assert features["project_velocity"] == 1
