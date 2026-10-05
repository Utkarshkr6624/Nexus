"""The analytics engine's honesty invariants, asserted where they are produced.

``test_analytics_scoring.py`` pins the pure formulas and the other
``test_analytics_*`` files pin the counts. Neither can see the six places where
this phase's own rules were broken on the way out of the service, because the
rules are about **what a figure says about the data** rather than about an
arithmetic result:

* a training row that cannot be attributed to the extraction that produced it
  (:func:`AnalyticsService.feature_snapshot` carries no ``schema_version``);
* a ``day_of_week`` column reporting a deadline weekday for a task that has no
  deadline, in the same row where ``deadline_distance_days`` correctly says
  ``None``;
* a measured ``0.0`` error rewritten to ``None`` by an alias copy written with
  ``or``;
* a figure the service itself says is not computable reported as ``0``;
* a drill-down list silently emptied by closed tasks that sorted ahead of the
  open ones, with nothing in the response saying the page ran out;
* a real project labelled ``Unassigned`` because the name lookup asked for 100
  rows and stopped.

**Every expected figure below is derived in the docstring**, from the weekday of
the anchor, from the count of rows the fixture writes, or from the constant the
cap is defined by — never copied from a run. A regression then shows up as a
wrong number rather than as a moved baseline.

``AnalyticsService`` is wired here directly rather than reached over HTTP,
because two of these are service-level facts (the snapshot's key set, the
project-name page) that the wire can obscure, and because the service is the
seam Phase 10 calls — ``models/analytics.py`` says so.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models.enums import ActivityEvent, CalendarEventType, TaskStatus
from app.models.planner import CalendarEvent
from app.repositories.analytics import AnalyticsRepository
from app.repositories.knowledge import NoteRepository
from app.repositories.planner import (
    AvailabilityRuleRepository,
    CalendarEventRepository,
    WorkSessionRepository,
)
from app.repositories.project import ProjectRepository
from app.repositories.task import TaskRepository
from app.schemas.analytics import (
    ANALYTICS_FEATURE_SCHEMA_VERSION,
    EstimationAccuracyRead,
    LearningAnalyticsRead,
)
from app.services.analytics.service import (
    MAX_OVERDUE_ROWS,
    PROJECT_NAME_PAGE_SIZE,
    AnalyticsService,
    _project_label,
)
from tests.analytics_fixtures import DAY, AnalyticsSeed, at, register_user

pytestmark = pytest.mark.integration

#: The window every read below is asked for: the anchor Monday and the six days
#: after it, so a fixture anchored at ``DAY`` is always inside it and no expected
#: value depends on today's date.
START = DAY
END = DAY + timedelta(days=6)


# -- Locals ------------------------------------------------------------------


async def db_today(session: AsyncSession) -> date:
    """Today on the *database* clock, normalised to UTC.

    ``feature_snapshot`` and the overdue drill-down both read "now" from
    ``SELECT now()`` so a deadline agrees with the rest of the system, which
    means the expected figures have to be derived from the same clock rather than
    from ``date.today()`` on the test host.

    Normalised to UTC because ``now()`` is a ``timestamptz`` returned **labelled
    with the connection's** ``TimeZone`` — ``Asia/Calcutta`` on this server — so a
    bare ``.date()`` on it is the server-local day, while the service converts to
    UTC before taking the date. The two differ for five and a half hours out of
    every twenty-four, and every figure derived from ``today`` would then be off
    by one day for exactly that stretch of the evening.
    """
    return (await session.scalar(select(func.now()))).astimezone(UTC).date()


async def study_block(seed: AnalyticsSeed, *, day: date, minutes: int = 45) -> CalendarEvent:
    """One calendar entry typed ``study`` — the only deliberate-learning signal.

    The shared seed deliberately does not carry an ``event_type``, so this writes
    the row directly; ``analytics.study_totals_in_range`` filters on exactly this
    value.
    """
    start = at(day)
    event = CalendarEvent(
        owner_id=seed.owner.id,
        title="study block",
        event_type=CalendarEventType.STUDY.value,
        starts_at=start,
        ends_at=start + timedelta(minutes=minutes),
        created_at=start,
    )
    seed.session.add(event)
    await seed.flush()
    return event


async def seeded(db_session: AsyncSession) -> tuple[AnalyticsSeed, AnalyticsService, object]:
    """An account, a seed bound to it, and the service wired against the session.

    Wired exactly as ``app.api.deps.get_analytics_service`` wires it, so a
    service-level assertion is about the same object an HTTP request would have
    reached.
    """
    owner = await register_user(db_session)
    service = AnalyticsService(
        AnalyticsRepository(db_session),
        TaskRepository(db_session),
        ProjectRepository(db_session),
        WorkSessionRepository(db_session),
        CalendarEventRepository(db_session),
        NoteRepository(db_session),
        availability=AvailabilityRuleRepository(db_session),
    )
    return AnalyticsSeed(db_session, owner), service, owner


# -- (a) THE SNAPSHOT MUST SAY WHICH EXTRACTION PRODUCED IT -----------------


async def test_the_feature_snapshot_carries_the_schema_version_its_columns_mean(db_session):
    """Every training row says ``analytics_features.v1``, whatever the data says.

    The audit found ``feature_snapshot`` returning a bare mapping of columns, so
    a Phase 10 training row could not be attributed to the extraction that made
    it: rename a column, and rows extracted before the rename are
    indistinguishable from rows extracted after it. Phase 8 and Phase 9 already
    stamp their vectors with ``developer_features.v1`` and its siblings, and
    this is that same contract applied here.

    The version is asserted as the **first** key because a trainer that builds a
    column order from the mapping needs it fixed, and as a ``str`` because it is
    a contract label rather than a model input.
    """
    seed, service, owner = await seeded(db_session)
    project = await seed.project()
    task = await seed.task(project_id=project.id, created_at=at(DAY))

    snapshot = await service.feature_snapshot(owner=owner, task_id=task.id)

    assert ANALYTICS_FEATURE_SCHEMA_VERSION == "analytics_features.v1"
    assert snapshot["schema_version"] == "analytics_features.v1"
    assert next(iter(snapshot)) == "schema_version"
    assert isinstance(snapshot["schema_version"], str)


async def test_a_bare_row_and_a_fully_measured_row_carry_the_same_version(db_session):
    """The stamp does not vary with how much there was to measure.

    Two tasks: one created and left alone, one created, estimated, given a due
    date and given a work session. Their feature rows differ everywhere — the
    bare one is ``estimated_minutes=None``, ``deadline_distance_days=None``,
    ``time_of_day=None``, ``actual_minutes=None``, ``recent_work_minutes=None``
    and ``work_session_count=0``; the measured one carries 60 estimated minutes,
    45 actual and 45 recent, one session, ``time_of_day=14``, a deadline
    ``deadline_distance_days`` days out and ``reschedule_count=0``. The version
    must be identical for both, because it describes the extractor and not the
    task.
    """
    seed, service, owner = await seeded(db_session)
    project = await seed.project()
    bare = await seed.task(project_id=project.id, title="bare", created_at=at(DAY))
    measured = await seed.task(
        project_id=project.id,
        title="measured",
        created_at=at(DAY),
        due_date=DAY + timedelta(days=3),
        estimated_minutes=60,
        actual_minutes=45,
    )
    await seed.work_session(day=DAY, minutes=45, task_id=measured.id, start_hour=14)

    bare_snapshot = await service.feature_snapshot(owner=owner, task_id=bare.id)
    measured_snapshot = await service.feature_snapshot(owner=owner, task_id=measured.id)

    assert (
        bare_snapshot["schema_version"]
        == measured_snapshot["schema_version"]
        == ANALYTICS_FEATURE_SCHEMA_VERSION
    )
    # The *matrix* is what a feature row is built from, so its keys are what must
    # be stable. The wrapper's metadata is compared separately below.
    bare_row = bare_snapshot["features"]
    measured_row = measured_snapshot["features"]
    assert list(bare_row) == list(measured_row)
    assert bare_snapshot["task_id"] == str(bare.id)
    assert measured_snapshot["task_id"] == str(measured.id)
    assert bare_snapshot["generated_at"] == measured_snapshot["generated_at"]
    # The rows really are different, so the assertion above is not vacuous.
    assert bare_row["estimated_minutes"] is None
    assert measured_row["estimated_minutes"] == 60
    assert measured_row["time_of_day"] == 14


# -- (b) A TASK WITH NO DEADLINE HAS NO DEADLINE WEEKDAY --------------------


async def test_a_task_with_no_due_date_reports_no_deadline_weekday(db_session):
    """``day_of_week`` is ``None``, not the weekday the task was created on.

    ``DAY`` is a Monday, so the task below is created four days later, on
    **Friday**, and ``(DAY + 4 days).weekday() == 4``. The defect computed
    ``(due_date or task.created_at.date()).weekday()``, so this row reported
    ``day_of_week == 4`` — a deadline weekday, "the deadline is a Friday" — in
    the same mapping where ``deadline_distance_days`` correctly said ``None``.
    A model reading that row sees deadline pressure that does not exist, and
    "no deadline" is exactly the signal the zero-or-null distinction exists to
    protect. The creation weekday is a real fact about a different thing and has
    no business under this key.
    """
    seed, service, owner = await seeded(db_session)
    project = await seed.project()
    created = DAY + timedelta(days=4)
    assert created.weekday() == 4, "the fixture must land on a Friday for this to prove anything"
    task = await seed.task(project_id=project.id, created_at=at(created))

    snapshot = (await service.feature_snapshot(owner=owner, task_id=task.id))["features"]

    assert snapshot["day_of_week"] is None
    # ...and the absence is a matched pair, not a hole in one column only.
    assert snapshot["deadline_distance_days"] is None
    assert "day_of_week" in snapshot


async def test_a_task_with_a_due_date_reports_the_weekday_of_that_deadline(db_session):
    """A deadline three days after the Monday anchor is a Wednesday: ``weekday() == 2``.

    The task is *created* on the anchor, so a figure still taken from
    ``created_at`` would be ``0``. Asserting ``2`` is what rules that out, and it
    is also the guard on the fix: the weekday must still be reported where a
    deadline genuinely exists, rather than the column simply always going null.
    """
    seed, service, owner = await seeded(db_session)
    project = await seed.project()
    due = DAY + timedelta(days=2)
    assert due.weekday() == 2, "the fixture must land on a Wednesday"
    assert DAY.weekday() == 0, "the anchor must be a Monday, or the two are indistinguishable"
    task = await seed.task(project_id=project.id, created_at=at(DAY), due_date=due)

    snapshot = (await service.feature_snapshot(owner=owner, task_id=task.id))["features"]

    assert snapshot["day_of_week"] == 2
    assert snapshot["deadline_distance_days"] == (due - await db_today(db_session)).days


# -- (c) A MEASURED ZERO IS NOT AN ABSENCE ----------------------------------


def test_a_measured_zero_error_is_not_rewritten_to_null():
    """Two estimates that were exactly right: 0.0 minutes of error, kept.

    Built as the audit described the shape — ``available=True``,
    ``sample_count=2``, ``bias=0.0``, ``median_error=0.0``, ``absolute_error=0.0``
    — because ``0.0`` is falsy in Python. The alias validator was written as
    ``self.mean_absolute_error = self.absolute_error or self.mean_absolute_error``,
    so the measured zero fell through to the alias, which was ``None``, and the
    response claimed beside ``median_error=0.0`` that the error had never been
    measured at all. "Every estimate was exactly right" is one of the most
    informative rows in this dataset and it was being deleted.
    """
    read = EstimationAccuracyRead(
        available=True,
        sample_count=2,
        bias=0.0,
        median_error=0.0,
        absolute_error=0.0,
    )

    assert read.absolute_error == 0.0
    assert read.mean_absolute_error == 0.0
    assert read.bias == 0.0
    assert read.median_error == 0.0


def test_the_alias_copy_still_fills_the_spelling_that_was_left_out():
    """``mean_absolute_error=0.0`` alone must reach ``absolute_error`` as ``0.0``.

    The two spellings exist so consumers written against either contract read
    the same figure, so filling the missing twin is the whole point of the
    validator — including when the missing twin's value happens to be falsy.
    """
    read = EstimationAccuracyRead(
        available=True, sample_count=2, bias=0.0, median_error=0.0, mean_absolute_error=0.0
    )

    assert read.absolute_error == 0.0
    assert read.mean_absolute_error == 0.0


def test_a_measured_zero_percentage_error_is_not_rewritten_to_null():
    """The percentage pair has the same defect and the same fix.

    Two completed tasks whose estimates and actuals were identical give a mean
    percentage error of ``0.0`` — a measurement, not an absence — on whichever
    spelling the caller supplies.
    """
    read = EstimationAccuracyRead(
        available=True,
        sample_count=2,
        bias=0.0,
        median_error=0.0,
        absolute_error=0.0,
        percentage_error=0.0,
    )

    assert read.percentage_error == 0.0
    assert read.mean_percentage_error == 0.0


def test_an_empty_comparison_is_still_null_on_both_spellings():
    """Nothing measured is ``None`` on both spellings, and the counts stay zero.

    With no completed pair to compare, every error figure has to stay ``None`` —
    "the estimates were exactly right" and "nobody has finished anything" are
    different claims and only one of them is true here. ``sample_count=0`` is a
    measured zero and must survive the alias copy as ``0``, not become the
    default it happens to equal.
    """
    read = EstimationAccuracyRead(available=False, sample_count=0, bias=None, median_error=None)

    assert read.absolute_error is None
    assert read.mean_absolute_error is None
    assert read.percentage_error is None
    assert read.mean_percentage_error is None
    assert read.under_estimation_rate is None
    assert read.sample_count == 0
    assert read.pairs_compared == 0


# -- (d) A FIGURE THAT IS NOT COMPUTABLE IS NOT ZERO ------------------------


def test_the_learning_schema_admits_a_null_knowledge_linked_tasks():
    """The field is ``int | None``, which is what makes ``null`` correct for it.

    The service carried a comment claiming "the schema types this field as a
    plain int, so 0 is what is returned". It does not: the declared annotation is
    ``int | None`` and the description says "Null rather than zero, because zero
    would claim the measurement was made and came back empty". The annotation is
    pinned here so the false comment cannot be reintroduced on the same evidence.
    """
    field = LearningAnalyticsRead.model_fields["knowledge_linked_tasks"]

    assert field.annotation == int | None
    assert "Null rather than zero" in (field.description or "")


async def test_the_learning_read_reports_knowledge_linked_tasks_as_null(db_session):
    """``knowledge_linked_tasks`` is ``None`` beside figures that were measured.

    Phase 5 writes its knowledge events with no ``task_id`` and no
    ``project_id``, so "tasks completed in projects the user was also writing
    about" cannot be computed from the rows that exist at all. The fixture proves
    the surrounding figures are real and computable: one ``study`` calendar block
    of 45 minutes is ``study_events=1`` and ``study_minutes=45``, and one
    ``note_created`` activity event is ``knowledge_interactions=1``. Only the
    uncomputable column is null, and the ``basis`` string still says in as many
    words that it is ``NOT measurable``.
    """
    seed, service, owner = await seeded(db_session)
    project = await seed.project()
    await seed.completed_task(day=DAY, project_id=project.id)
    await study_block(seed, day=DAY, minutes=45)
    await seed.activity(ActivityEvent.NOTE_CREATED, day=DAY)

    read = await service.learning(owner=owner, start=START, end=END)

    assert read.knowledge_linked_tasks is None
    assert "NOT measurable" in read.basis
    assert read.study_events == 1
    assert read.study_minutes == 45
    assert read.knowledge_interactions == 1


# -- (e) THE OVERDUE LIST MUST NOT BE EMPTIED BY CLOSED TASKS ---------------


async def test_the_overdue_list_is_not_emptied_by_closed_tasks_that_sort_first(db_session):
    """120 finished tasks, 3 open ones, all overdue: the drill-down keeps the 3.

    Every filler is *completed* and due 200 days ago, so it sorts ahead of the
    three open tasks, which are due 3 days ago. The old read asked the repository
    for the first 100 rows due before today **in any status** and dropped the
    closed ones in Python, so the page filled with finished work and the
    response answered ``overdue_tasks: 3`` beside ``top_overdue: []`` — a drill
    -down that says "nothing is overdue" while the headline above it says three
    things are. Asking the query for open statuses is what fixes it; with only
    three open tasks nothing is truncated and the flag says so.
    """
    seed, service, owner = await seeded(db_session)
    project = await seed.project()
    today = await db_today(db_session)
    for index in range(120):
        await seed.task(
            project_id=project.id,
            title=f"finished {index}",
            status=TaskStatus.COMPLETED.value,
            created_at=at(DAY),
            due_date=today - timedelta(days=200),
            completed_at=at(DAY),
        )
    open_titles = []
    for index in range(3):
        task = await seed.task(
            project_id=project.id,
            title=f"still open {index}",
            status=TaskStatus.TODO.value,
            created_at=at(DAY),
            due_date=today - timedelta(days=3),
        )
        open_titles.append(task.title)

    read = await service.task_analytics(owner=owner, start=START, end=END)

    # The premise, asserted rather than assumed: the unfiltered page the old read
    # asked for is 100 rows of finished work, so the drill-down it built from
    # that page was empty for the right reason and not by accident of the fixture.
    any_status, _ = await TaskRepository(db_session).list_for_user(
        owner.id, limit=100, offset=0, due_before=today, sort="due_date", order="asc"
    )
    assert len(any_status) == 100
    assert {task.status for task in any_status} == {TaskStatus.COMPLETED.value}

    assert read.overdue_tasks == 3
    # Compared as a set: all three are equally overdue, so the order among them
    # is the repository's and carries no claim the test should make.
    assert sorted(row.title for row in read.top_overdue) == sorted(open_titles)
    assert read.top_overdue_truncated is False
    assert all(row.days_overdue == 3 for row in read.top_overdue)


async def test_the_overdue_list_says_when_it_cannot_hold_every_overdue_task(db_session):
    """``MAX_OVERDUE_ROWS`` open overdue tasks is not truncated; one more is.

    The drill-down is a top-20 list by design, but a capped list that says
    nothing reads as "these are all of them". Twenty open overdue tasks exactly
    fits, so the flag is false; twenty-one does not fit, so the list is capped at
    :data:`MAX_OVERDUE_ROWS` and ``top_overdue_truncated`` is true — a statement
    the caller can render instead of quietly losing a row.
    """
    seed, service, owner = await seeded(db_session)
    project = await seed.project()
    today = await db_today(db_session)
    for index in range(MAX_OVERDUE_ROWS):
        await seed.task(
            project_id=project.id,
            title=f"overdue {index}",
            created_at=at(DAY),
            due_date=today - timedelta(days=2),
        )

    exact = await service.task_analytics(owner=owner, start=START, end=END)
    assert len(exact.top_overdue) == MAX_OVERDUE_ROWS
    assert exact.overdue_tasks == MAX_OVERDUE_ROWS
    assert exact.top_overdue_truncated is False

    await seed.task(
        project_id=project.id,
        title="one too many",
        created_at=at(DAY),
        due_date=today - timedelta(days=2),
    )
    capped = await service.task_analytics(owner=owner, start=START, end=END)

    assert capped.overdue_tasks == MAX_OVERDUE_ROWS + 1
    assert len(capped.top_overdue) == MAX_OVERDUE_ROWS
    assert capped.top_overdue_truncated is True


# -- (f) A REAL PROJECT IS NEVER "UNASSIGNED" -------------------------------


def test_an_unresolved_project_id_is_labelled_by_its_id_and_never_unassigned():
    """Past the name-lookup ceiling the label degrades to the id, not to a lie.

    ``Unassigned`` is a claim about a **session**: this time was recorded against
    no project, and it is what ``unassigned_minutes`` counts. A project id the
    name lookup did not reach still belongs to a project, so calling it
    "Unassigned" asserts in one row what the neighbouring counter denies. The id
    is ugly; it is not false.
    """
    project_id = uuid.UUID("11111111-2222-3333-4444-555555555555")

    assert _project_label(None, {}) == "Unassigned"
    assert _project_label(project_id, {project_id: "Roof repair"}) == "Roof repair"
    assert _project_label(project_id, {}) == "Project 11111111"


async def test_a_project_beyond_the_first_page_of_names_is_still_named(db_session):
    """101 projects, one work session against the oldest: it is named, not "Unassigned".

    ``time_distribution`` asked the project repository for one page of 100 rows
    and built a ``{id: name}`` map from whatever came back, so the 101st project
    was simply missing from the map and its minutes were labelled "Unassigned" —
    in the same response as ``unassigned_minutes: 0``, which says none of this
    time was recorded without a project. The hundred fillers are created an hour
    *after* the subject project, so the repository's default ``created_at DESC``
    ordering puts all 100 of them on the first page and the subject project last.
    One session of 45 minutes is ``total_minutes=45``, all of it attributed to
    the subject project.
    """
    seed, service, owner = await seeded(db_session)
    for index in range(PROJECT_NAME_PAGE_SIZE):
        await seed.project(name=f"filler {index}", created_at=at(DAY, 10))
    subject = await seed.project(name="Roof repair", created_at=at(DAY))
    await seed.work_session(day=DAY, minutes=45, project_id=subject.id)

    read = await service.time_distribution(owner=owner, start=START, end=END)

    # The premise, asserted rather than assumed: the subject project really is
    # past the page the old read stopped at.
    first_page, _ = await ProjectRepository(db_session).list_for_user(
        owner.id, limit=PROJECT_NAME_PAGE_SIZE, offset=0
    )
    assert len(first_page) == PROJECT_NAME_PAGE_SIZE
    assert subject.id not in {row.id for row in first_page}

    assert read.total_minutes == 45
    assert read.unassigned_minutes == 0
    buckets = {bucket.key: bucket for bucket in read.by_project}
    assert buckets[str(subject.id)].label == "Roof repair"
    assert buckets[str(subject.id)].minutes == 45
    assert [bucket.label for bucket in read.by_project] == ["Roof repair"]


async def test_a_session_with_no_project_is_still_reported_as_unassigned(db_session):
    """The label the fix protects is still produced where it is true.

    One session carrying no project at all is ``unassigned_minutes=45`` and a
    bucket labelled ``Unassigned``, and no project is consulted to earn that
    label. Pinning the honest case matters as much as pinning the dishonest one:
    a fix that made the label unreachable would have been a different defect.
    """
    seed, service, owner = await seeded(db_session)
    await seed.project(name="Roof repair", created_at=at(DAY))
    await seed.work_session(day=DAY, minutes=45)

    read = await service.time_distribution(owner=owner, start=START, end=END)

    assert read.unassigned_minutes == 45
    assert [bucket.label for bucket in read.by_project] == ["Unassigned"]
    assert read.by_project[0].key == "unassigned"
    assert read.by_project[0].share == 100.0


# -- The snapshot is still owner-scoped, after all of the above --------------


async def test_another_accounts_task_is_still_not_found_after_the_changes(db_session):
    """The 404-not-403 rule is unchanged by any of this: one owner's row only.

    The seed versions the column meanings; it must not weaken the tenancy. Ada's
    task is described for Ada and is **not found** for Grace, with no forbidden
    status code that would confirm the id exists.
    """
    seed, service, owner = await seeded(db_session)
    project = await seed.project()
    task = await seed.task(project_id=project.id, created_at=at(DAY))
    grace = await register_user(db_session, username="grace", email="grace@nexus.test")

    snapshot = await service.feature_snapshot(owner=owner, task_id=task.id)
    assert snapshot["schema_version"] == ANALYTICS_FEATURE_SCHEMA_VERSION

    with pytest.raises(NotFoundError):
        await service.feature_snapshot(owner=grace, task_id=task.id)
