"""Invariants the repositories owe their callers, pinned against the real database.

Each test here corresponds to a defect that shipped because nothing below the
route layer asserted the property:

* a day bound handed to :func:`app.repositories.search._date_bound` is a plain
  ``date``, not a ``datetime``, and reading ``.tzinfo`` off it raised for every
  kind whose date column is a timestamp;
* ``GET /tasks?tag_ids=`` is documented as all-of and was implemented as any-of,
  so it disagreed with ``GET /search?types=task`` for the same filter;
* the overdue series has to cut its day boundary at the connection's own
  midnight, which a bare ``CAST(timestamptz AS DATE)`` silently defers to
  whichever connection ran it;
* ``by_priority`` sits beside ``total`` in the recommendations response, so it has
  to count the rows ``total`` counts;
* replacing a whole weekly availability pattern is bounded at 168 rules, so it
  cannot afford one round trip per rule;
* ``committed_at`` is git's own fact about a commit and must not move when a
  second scan reports a different instant for the same object.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy import event, text

from app.models.knowledge import Note
from app.models.project import Project
from app.models.risk import Recommendation
from app.models.tag import Tag, task_tags
from app.models.task import Task
from app.repositories.analytics import AnalyticsRepository
from app.repositories.developer import DeveloperRepository
from app.repositories.planner import AvailabilityRuleRepository
from app.repositories.risk import RiskRepository
from app.repositories.search import SEARCH_TARGETS, SearchRepository, _date_bound
from app.repositories.task import TaskRepository
from tests.analytics_fixtures import register_user

pytestmark = pytest.mark.integration

#: A Monday well clear of any month boundary, so the day arithmetic here is
#: arithmetic and not a calendar surprise.
ANCHOR_DATE = date(2026, 3, 2)
ANCHOR = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)


@pytest.fixture
async def owner(db_session):
    return await register_user(db_session, username="ada", email="ada@nexus.test")


@pytest.fixture
async def project(db_session, owner):
    """A workspace, because ``tasks.project_id`` is ``NOT NULL``."""
    row = Project(owner_id=owner.id, name="Nexus")
    db_session.add(row)
    await db_session.commit()
    return row


# ----------------------------------------------------------------------
# search: a day bound is a date, not a datetime
# ----------------------------------------------------------------------


TIMESTAMP_KINDS = sorted(
    kind for kind, target in SEARCH_TARGETS.items() if target.filter_date_is_timestamp
)


def test_every_timestamp_kind_accepts_a_plain_date_bound():
    """The route's ``from``/``to`` are ``date``; the widening must survive one.

    A ``date`` has no ``tzinfo``, so reading the attribute directly raised
    ``AttributeError`` inside ``GET /search`` for every kind whose date column is
    a timestamp — the whole endpoint except the two ``Date`` kinds.
    """
    assert TIMESTAMP_KINDS, "the timestamp kinds are the point of the assertion"

    for kind in TIMESTAMP_KINDS:
        start = _date_bound(ANCHOR_DATE, is_timestamp=True, end_of_day=False)
        end = _date_bound(ANCHOR_DATE, is_timestamp=True, end_of_day=True)

        assert (start.year, start.month, start.day, start.hour, start.minute) == (
            ANCHOR_DATE.year,
            ANCHOR_DATE.month,
            ANCHOR_DATE.day,
            0,
            0,
        ), kind
        # ``to`` is the last microsecond of the day, not midnight: midnight would
        # silently drop everything that happened on it.
        assert (end.hour, end.minute, end.second, end.microsecond) == (23, 59, 59, 999_999), kind
        # A naive bound is the honest spelling for a ``date``; PostgreSQL resolves
        # it in the session zone rather than failing.
        assert start.tzinfo is None and end.tzinfo is None


def test_a_date_bound_is_returned_unchanged_for_a_date_column():
    """A ``Date`` column needs no widening, so the argument comes back as-is."""
    assert _date_bound(ANCHOR_DATE, is_timestamp=False, end_of_day=False) is ANCHOR_DATE
    assert _date_bound(None, is_timestamp=True, end_of_day=True) is None


async def test_search_applies_a_day_range_to_a_timestamp_kind(db_session, owner):
    """End to end over one timestamp kind: the bound reaches the statement."""
    inside = Note(owner_id=owner.id, title="Range probe inside", content="body")
    outside = Note(owner_id=owner.id, title="Range probe outside", content="body")
    db_session.add_all([inside, outside])
    await db_session.commit()

    # ``notes.updated_at`` is the kind's filter column, so it is the column the
    # range has to be written onto.
    for note, stamp in ((inside, ANCHOR), (outside, ANCHOR + timedelta(days=5))):
        await db_session.execute(
            text("UPDATE notes SET updated_at = :stamp WHERE id = :id"),
            {"stamp": stamp, "id": note.id},
        )
    await db_session.commit()

    repository = SearchRepository(db_session)
    hits = await repository.search(
        SEARCH_TARGETS["note"],
        owner_id=owner.id,
        term="Range probe",
        limit=50,
        date_from=ANCHOR_DATE,
        date_to=ANCHOR_DATE,
    )

    assert [hit.id for hit in hits] == [inside.id]


# ----------------------------------------------------------------------
# tasks: tag_ids is all-of
# ----------------------------------------------------------------------


async def test_tag_ids_requires_every_listed_tag(db_session, owner, project):
    """Two tags in, two tags out — a task carrying only one is not a match.

    The documented and implemented behaviour used to differ: the filter was an
    ``IN`` over ``task_tags`` and therefore any-of, so this returned ``both`` and
    ``first_only`` for the pair while the docs promised ``both``.
    """
    first = Tag(user_id=owner.id, name="alpha")
    second = Tag(user_id=owner.id, name="beta")
    db_session.add_all([first, second])
    await db_session.commit()
    await db_session.refresh(first)
    await db_session.refresh(second)

    both = Task(owner_id=owner.id, project_id=project.id, title="Carries both")
    first_only = Task(owner_id=owner.id, project_id=project.id, title="Carries alpha")
    second_only = Task(owner_id=owner.id, project_id=project.id, title="Carries beta")
    db_session.add_all([both, first_only, second_only])
    await db_session.commit()
    await db_session.refresh(both)
    await db_session.refresh(first_only)
    await db_session.refresh(second_only)

    for task, tag in ((both, first), (both, second), (first_only, first), (second_only, second)):
        await db_session.execute(task_tags.insert().values(task_id=task.id, tag_id=tag.id))
    await db_session.commit()

    repository = TaskRepository(db_session)

    pair_rows, pair_total = await repository.list_for_user(
        owner.id, limit=50, offset=0, tag_ids=[first.id, second.id]
    )
    assert [row.id for row in pair_rows] == [both.id]
    # The total is counted from the same predicate, so the header and the page
    # cannot disagree.
    assert pair_total == 1

    single_rows, single_total = await repository.list_for_user(
        owner.id, limit=50, offset=0, tag_ids=[first.id]
    )
    assert {row.id for row in single_rows} == {both.id, first_only.id}
    assert single_total == 2

    # The same tag listed twice is one tag, not a demand for two matching edges.
    duplicated_rows, duplicated_total = await repository.list_for_user(
        owner.id, limit=50, offset=0, tag_ids=[first.id, first.id]
    )
    assert {row.id for row in duplicated_rows} == {both.id, first_only.id}
    assert duplicated_total == 2


# ----------------------------------------------------------------------
# analytics: the overdue series is cut at the connection's own midnight
# ----------------------------------------------------------------------


async def test_the_overdue_series_cuts_its_boundary_at_the_connection_midnight(
    db_session, owner, project
):
    """A completion after the connection's midnight is the next day, and overdue.

    ``count_tasks_overdue_by_day`` decides "was it finished by the end of its due
    day?" by comparing ``due_date`` against
    ``date(completed_at AT TIME ZONE current_setting('TimeZone'))`` — the
    **connection's** calendar, named in the SQL rather than left to a bare
    ``CAST(timestamptz AS DATE)``, which would defer to whichever connection ran
    it and be invisible in the query. Setting the session zone is what makes the
    two spellings distinguishable at all; every sibling ``*_by_day`` method
    names the same zone and has to stay in step with them.

    Both directions are asserted, because one of them passes by accident under
    either rule and the other cannot:

    * in ``Asia/Tokyo`` the completion below is 08:30 the next day, so the task
      **was** late on its due date and the series reports it;
    * in ``UTC`` the same instant is 23:30 on the due date itself, so it was on
      time and the series is empty.

    A query that hard-coded either zone fails one of the two. A query that fell
    back to the implicit cast still passes both — it happens to agree — but it
    would then answer from whatever connection it was handed, which is the bug
    this test was written for.
    """
    await db_session.execute(text("SET TIME ZONE 'Asia/Tokyo'"))

    task = Task(
        owner_id=owner.id,
        project_id=project.id,
        title="Finished at 23:30 UTC on its due date",
        due_date=ANCHOR_DATE,
        # On time in UTC, and the next calendar day in Tokyo.
        completed_at=datetime(2026, 3, 2, 23, 30, tzinfo=UTC),
        status="completed",
    )
    db_session.add(task)
    await db_session.commit()

    repository = AnalyticsRepository(db_session)

    rows = await repository.count_tasks_overdue_by_day(owner.id, start=ANCHOR_DATE, end=ANCHOR_DATE)
    assert rows == [(ANCHOR_DATE, 1)], (
        "the overdue series cut its boundary at UTC midnight while the connection "
        "was on Asia/Tokyo, so a task finished the morning after it was due "
        "read as on time"
    )

    await db_session.execute(text("SET TIME ZONE 'UTC'"))

    rows = await repository.count_tasks_overdue_by_day(owner.id, start=ANCHOR_DATE, end=ANCHOR_DATE)
    assert rows == [], (
        "the overdue series called a task late under a zone where its completion "
        "falls on the due date itself"
    )


# ----------------------------------------------------------------------
# recommendations: by_priority counts what total counts
# ----------------------------------------------------------------------


async def test_by_priority_honours_the_type_filter(db_session, owner):
    """The tally sums to the same rows the filtered list returns.

    ``RecommendationListRead`` carries ``by_priority`` beside ``total``; a tally
    that ignored ``recommendation_type`` would report a header the filtered list
    contradicted.
    """
    db_session.add_all(
        [
            Recommendation(
                user_id=owner.id,
                recommendation_type="reschedule_task",
                priority="high",
                title="Reschedule",
                description="Move it",
                reason="It slipped",
                entity_type="task",
            ),
            Recommendation(
                user_id=owner.id,
                recommendation_type="reschedule_task",
                priority="high",
                title="Reschedule again",
                description="Move it twice",
                reason="It slipped again",
                entity_type="task",
            ),
            Recommendation(
                user_id=owner.id,
                recommendation_type="break_down_task",
                priority="low",
                title="Break it down",
                description="Split it",
                reason="Too big",
                entity_type="task",
            ),
        ]
    )
    await db_session.commit()

    repository = RiskRepository(db_session)

    _, total = await repository.list_recommendations(
        owner.id, types=["reschedule_task"], limit=50, offset=0
    )
    counts = await repository.count_by_priority(owner.id, types=["reschedule_task"])

    assert total == 2
    assert sum(counts.values()) == total
    assert counts["high"] == 2
    assert counts["low"] == 0

    unfiltered = await repository.count_by_priority(owner.id)
    assert sum(unfiltered.values()) == 3


# ----------------------------------------------------------------------
# availability: replacing a whole week is one round trip, not one per rule
# ----------------------------------------------------------------------


async def test_replacing_a_whole_week_costs_one_re_read(db_session, engine, owner):
    """A full week is up to 168 rules, so the re-read cannot be per row."""
    repository = AvailabilityRuleRepository(db_session)
    rules = [
        {
            "weekday": weekday,
            "starts_at": time(9 + weekday, 0),
            "ends_at": time(17 + weekday, 0),
            "label": f"Window {weekday}",
        }
        for weekday in range(6)
    ]

    statements: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _record)
    try:
        stored = await repository.replace_for_user(owner.id, rules)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _record)

    # Submitted order is the caller's order; the re-read is keyed by id so the
    # weekday ordering of the listing query cannot reorder the response.
    assert [row.label for row in stored] == [rule["label"] for rule in rules]
    assert all(row.id is not None and row.created_at is not None for row in stored)

    reads = [
        statement
        for statement in statements
        if statement.lstrip().upper().startswith("SELECT") and "availability_rules" in statement
    ]
    assert len(reads) == 1, statements

    listed = await repository.list_for_user(owner.id)
    assert {row.id for row in listed} == {row.id for row in stored}


# ----------------------------------------------------------------------
# developer: a commit's author date is not rewritten by a re-scan
# ----------------------------------------------------------------------


async def test_a_rescan_never_moves_a_committed_instant(db_session, owner):
    """The second scan's ``committed_at`` is discarded, not written.

    ``committed_at`` is a fact about the object rather than an observation about
    it. Whichever scan first saw the commit keeps the value; a re-scan that
    reported a different instant would mean two scans parsed the same repository
    differently, and the stored row would silently move under every aggregate
    that buckets by day.
    """
    repository = DeveloperRepository(db_session)
    repository_row = await repository.create_repository(
        owner.id, name="nexus", local_path="/repos/nexus"
    )
    commit_hash = "b" * 40

    await repository.upsert_commits(
        owner.id,
        repository_row.id,
        [
            {
                "commit_hash": commit_hash,
                "short_hash": "bbbbbbbbbbbb",
                "committed_at": ANCHOR,
                "message": "Add the reader",
                "branch": None,
            }
        ],
    )
    await repository.upsert_commits(
        owner.id,
        repository_row.id,
        [
            {
                "commit_hash": commit_hash,
                "short_hash": "bbbbbbbbbbbb",
                "committed_at": ANCHOR + timedelta(days=30),
                "message": "Add the reader",
                "branch": "main",
            }
        ],
    )

    stored = (
        await db_session.execute(
            text(
                "SELECT committed_at, branch FROM git_commits "
                "WHERE repository_id = :repository_id AND commit_hash = :commit_hash"
            ),
            {"repository_id": repository_row.id, "commit_hash": commit_hash},
        )
    ).one()
    # Branch attribution is refreshed — it is best-effort. The instant is not.
    assert stored.branch == "main"
    assert stored.committed_at.astimezone(UTC) == ANCHOR

    # The aggregate reads the same value back: the commit stays inside the
    # window that contains the instant it was first seen at.
    totals = await repository.commit_totals(owner.id, until=ANCHOR + timedelta(days=1))
    assert totals.commits == 1
