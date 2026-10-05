"""The learning, knowledge and ML-feature reads, end to end over HTTP.

These are the three Phase 6 surfaces that report on what a user *did* rather
than on how productive they were: ``/analytics/learning``, ``/analytics/knowledge``
and ``/analytics/feature-snapshot``. The scoring formulas have a pure unit suite
in ``test_analytics_scoring.py``; what is missing is the layer above it, where
the figures have to be right *as observed through the API* — a count that is
correct against the rows and wrong against the response is still a wrong number.

Three properties are pinned here, and each has a failure mode the brief calls
out by name:

* **Exact counts from fixed data.** The brief's rule — "if: 10 tasks, 8
  completed / then: completion rate must equal 80%" — only works if every
  expected value can be derived by hand from the fixture. So each category below
  is seeded in isolation: a fixture whose only knowledge event is
  ``resource_created`` must report ``resources_added == 1`` and every other
  counter exactly zero, which is a much stronger statement than "the number went
  up by one somewhere".
* **No inference.** Phases 1-5 record no learning flag, no mastery field and no
  note views. The endpoints therefore have to report what was recorded and say
  which rows they read, and the tests assert the *absence* of the claims the
  brief forbids: no mastery number, no proficiency score, and no view count
  invented for an event type that does not exist.
* **Owner scoping on the ML features.** ``feature-snapshot`` is the Phase 10
  training set. A feature vector for another account's task would be both a
  tenancy leak and a poisoned label, so the route must answer 404 — never 403 —
  for an id that is not the caller's.

Window parameters are always explicit. The routes default to a window ending on
the database's current date, and a fixture anchored at :data:`DAY` in January
would fall outside that default and every assertion below would read zero for a
reason that has nothing to do with the code under test.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import Date, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import ActivityEvent, CalendarEventType, NoteStatus, TaskPriority
from app.models.knowledge import note_tags
from app.models.planner import CalendarEvent
from app.models.tag import Tag
from tests.analytics_fixtures import DAY, AnalyticsSeed, at, seeded_client

pytestmark = pytest.mark.integration

#: The window every read below is asked for: the anchor Monday and the six days
#: after it, so the whole fixture is in range and no test depends on today's date.
START = DAY
END = DAY + timedelta(days=6)

WINDOW = {"start_date": START.isoformat(), "end_date": END.isoformat()}


# -- Locals the shared seed deliberately does not provide --------------------


async def study_event(
    seed: AnalyticsSeed,
    *,
    day: date,
    minutes: int,
    hour: int = 9,
    event_type: str = CalendarEventType.STUDY.value,
) -> CalendarEvent:
    """A calendar entry of a named ``event_type``.

    ``AnalyticsSeed.calendar_event`` leaves ``event_type`` at the schema default,
    which is ``other``. Learning analytics counts ``event_type = 'study'`` and
    nothing else, so the typed row has to be built here rather than reached
    through the helper.
    """
    start = at(day, hour)
    event = CalendarEvent(
        owner_id=seed.owner.id,
        title="study block",
        event_type=event_type,
        starts_at=start,
        ends_at=start + timedelta(minutes=minutes),
        created_at=start,
    )
    seed.session.add(event)
    await seed.flush()
    return event


async def tag_note(seed: AnalyticsSeed, *, note_id: uuid.UUID, name: str) -> Tag:
    """Apply a tag to one of the owner's notes, minting the tag if it is new.

    ``most_used_tags`` is a join over ``note_tags``, and nothing in the shared
    seed writes either table — the Phase 5 service would have to be driven
    through HTTP to do it, which would put event rows in the window as a side
    effect. Writing the two rows directly keeps the tag fixture from also
    recording knowledge activity, so the tag counts and the event counts stay
    independent measurements.
    """
    existing = await seed.session.scalar(
        select(Tag).where(Tag.user_id == seed.owner.id, Tag.name == name)
    )
    tag = existing or Tag(user_id=seed.owner.id, name=name)
    seed.session.add(tag)
    await seed.flush()
    await seed.session.execute(note_tags.insert().values(note_id=note_id, tag_id=tag.id))
    await seed.flush()
    return tag


async def db_today(session: AsyncSession) -> date:
    """The current date on the database clock, in the database's own calendar.

    ``feature-snapshot`` has no window parameter and reads "now" from
    ``SELECT now()``, so the exact expected values for ``task_age_days``,
    ``deadline_distance_days`` and ``overdue_count`` have to be derived from the
    same clock rather than from ``date.today()`` on the test host.

    **Not** normalised to UTC, which is what this once did. ``now()`` is a
    ``timestamptz`` returned labelled with the connection's ``TimeZone``, so a
    bare ``.date()`` on it is the server-local day — which is now exactly what
    :meth:`AnalyticsRepository.today` returns. Converting first put the two a
    whole day apart for five and a half hours a day, and each of those figures is
    a whole-day difference when they do.
    """
    return await session.scalar(select(func.now().cast(Date)))


async def db_zone(session: AsyncSession) -> ZoneInfo:
    """The calendar ``time_of_day`` is read in.

    Asked of the database rather than assumed: the feature is an hour in the
    server's own zone, so a fixture that means "the user started at 14:00" has
    to mean it in *that* calendar.
    """
    return ZoneInfo(str(await session.scalar(select(func.current_setting("TimeZone")))))


async def snapshot_for(
    session: AsyncSession,
    client: Any,
    headers: dict[str, str],
    *,
    task_id: uuid.UUID,
    today: date | None = None,
) -> dict[str, Any]:
    """One task's feature snapshot, its wrapper asserted, for the matrix's sake.

    ``/analytics/feature-snapshot`` answers a wrapper — ``schema_version``,
    ``generated_at``, ``task_id`` and a ``features`` object holding the fifteen
    numbers — rather than the bare mapping it used to return. A Phase-10 training
    row has to be attributable to the extraction that produced it, and a version
    string smuggled *inside* the matrix becomes a column a model is asked to
    fit, so the provenance travels beside the numbers and never among them. The
    matrix itself is untouched: the same fifteen keys, in the same order, on
    every call whatever the data says.

    Args:
        session: The test's session, used to read the database's ``now()``.
        client: The authenticated HTTP client.
        headers: The caller's authorization headers.
        task_id: The task to describe.
        today: The database's current date, when the caller already has it.

    Returns:
        The whole body, wrapper included. Callers read ``["features"]`` for the
        matrix; the three other keys are this helper's business.
    """
    response = await client.get(
        "/api/v1/analytics/feature-snapshot", params={"task_id": str(task_id)}, headers=headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"schema_version", "generated_at", "task_id", "features"}
    assert body["schema_version"] == "analytics_features.v1"
    assert body["task_id"] == str(task_id)
    assert body["generated_at"] == (today or await db_today(session)).isoformat()
    return body


# -- LEARNING ANALYTICS FOUNDATION -------------------------------------------


async def test_learning_reports_the_study_sessions_and_minutes_it_can_count(client, db_session):
    """45 + 30 minutes of ``study`` blocks is 75 minutes over 2 sessions.

    The 90-minute ``other`` event on the same day is the point of the fixture:
    the figure is a count of *typed* study blocks, not of time spent on the
    calendar, and a fixture where every event were a study block could not tell
    the two apart.
    """
    seed, auth = await seeded_client(client, db_session)
    await study_event(seed, day=DAY, minutes=45, hour=9)
    await study_event(seed, day=DAY, minutes=30, hour=11)
    await study_event(seed, day=DAY, minutes=90, hour=14, event_type=CalendarEventType.OTHER.value)

    response = await client.get("/api/v1/analytics/learning", params=WINDOW, headers=auth)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["study_events"] == 2
    assert body["study_minutes"] == 75


async def test_learning_counts_only_study_blocks_inside_the_requested_window(client, db_session):
    """A study block the day before the window is not study time in it.

    Without this the count would be 3 events and 135 minutes, which is the
    number a date-range bug produces and which no assertion of "there is some
    study activity" would ever catch.
    """
    seed, auth = await seeded_client(client, db_session)
    await study_event(seed, day=DAY, minutes=45)
    await study_event(seed, day=DAY - timedelta(days=1), minutes=60)
    await study_event(seed, day=END + timedelta(days=1), minutes=60)

    response = await client.get("/api/v1/analytics/learning", params=WINDOW, headers=auth)

    body = response.json()
    assert response.status_code == 200, response.text
    assert body["study_events"] == 1
    assert body["study_minutes"] == 45


async def test_learning_counts_knowledge_interactions_and_notes_from_the_event_feed(
    client, db_session
):
    """2 notes created and 1 updated is 3 interactions, split 2/1.

    The split is read from ``activity_events`` rather than from the notes table
    because ``notes.updated_at`` is rewritten by every autosave: a note written
    in January and re-saved this week would otherwise be counted as *created*
    this week. The feed records what happened.
    """
    seed, auth = await seeded_client(client, db_session)
    for _ in range(2):
        await seed.activity(ActivityEvent.NOTE_CREATED, day=DAY)
    await seed.activity(ActivityEvent.NOTE_UPDATED, day=DAY)

    response = await client.get("/api/v1/analytics/learning", params=WINDOW, headers=auth)

    body = response.json()
    assert response.status_code == 200, response.text
    assert body["knowledge_interactions"] == 3
    assert body["notes_created"] == 2
    assert body["notes_updated"] == 1
    assert body["available"] is True
    assert body["reason_if_unavailable"] is None


async def test_learning_reports_no_learning_related_tasks_and_says_why(client, db_session):
    """``knowledge_linked_tasks`` is null, and the response says it is not measured.

    NEXUS records no learning flag and Phase 5 writes its knowledge events with
    no ``task_id`` and no ``project_id``, so "tasks completed in projects the
    user was also writing about" cannot be computed from the rows that exist.
    The figure is therefore **null, not zero**, with the reason in ``basis``.

    The zero this replaces was the dishonest half of that pair. The fixture has
    a completed task and a note in the same window, so a reader cannot tell from
    the numbers whether ``0`` means "the correlation was measured and came back
    empty" — which would be a claim about the user's work — or "nobody measured
    it". The fixture makes that concrete: the one figure it *can* measure,
    ``knowledge_interactions``, is 1 from the note event, so the window is not
    silent and the null really is about the correlation rather than the data.
    Absence of measurement is null; a measured zero would be ``0``.
    """
    seed, auth = await seeded_client(client, db_session)
    project = await seed.project()
    await seed.completed_task(day=DAY, project_id=project.id)
    await seed.activity(ActivityEvent.NOTE_CREATED, day=DAY)

    response = await client.get("/api/v1/analytics/learning", params=WINDOW, headers=auth)

    body = response.json()
    assert response.status_code == 200, response.text
    assert body["knowledge_linked_tasks"] is None
    # The window was not silent, so the null is about the correlation and not
    # about there being nothing recorded.
    assert body["knowledge_interactions"] == 1
    assert "NOT measurable" in body["basis"]


async def test_learning_states_the_window_it_measured_over(client, db_session):
    """The range travels with the numbers, so a client cannot misread them.

    A reader who asks for six days and is shown 75 minutes has to know *which*
    six days; without the echoed window the only way to find out is to remember
    what was sent.
    """
    seed, auth = await seeded_client(client, db_session)
    await study_event(seed, day=DAY, minutes=45)

    response = await client.get("/api/v1/analytics/learning", params=WINDOW, headers=auth)

    body = response.json()
    assert response.status_code == 200, response.text
    assert body["range"]["start_date"] == START.isoformat()
    assert body["range"]["end_date"] == END.isoformat()


async def test_learning_makes_no_claim_about_mastery_or_intelligence(client, db_session):
    """The brief forbids inferring mastery; the response must not imply one.

    Two things are asserted. The field set is exactly the eight recorded
    figures plus the three prose fields, so a new key cannot appear without this
    test noticing — the failure mode being a ``mastery_level`` or
    ``skill_score`` arriving with no row behind it. And the word "mastery"
    appears nowhere in the payload, not even in the prose, so the endpoint
    cannot claim mastery in a footnote either.
    """
    seed, auth = await seeded_client(client, db_session)
    await study_event(seed, day=DAY, minutes=45)
    await seed.activity(ActivityEvent.NOTE_CREATED, day=DAY)

    response = await client.get("/api/v1/analytics/learning", params=WINDOW, headers=auth)

    body = response.json()
    assert response.status_code == 200, response.text
    assert set(body) == {
        "available",
        "reason_if_unavailable",
        "study_events",
        "study_minutes",
        "knowledge_linked_tasks",
        "knowledge_interactions",
        "notes_created",
        "notes_updated",
        "projects_touched",
        "basis",
        "definition",
        "range",
    }
    assert "mastery" not in response.text.lower()
    assert "proficien" not in response.text.lower()
    assert "skill" not in response.text.lower()


# -- KNOWLEDGE ANALYTICS -----------------------------------------------------


async def test_knowledge_counts_notes_created_when_that_is_the_only_event(client, db_session):
    """Three ``note_created`` events: 3 created, and nothing else.

    Every other counter is asserted at exactly zero. A fixture that recorded all
    five kinds at once could not tell a working counter from one that is stuck
    on a default, which is the failure this isolation exists to catch.
    """
    seed, auth = await seeded_client(client, db_session)
    for _ in range(3):
        await seed.activity(ActivityEvent.NOTE_CREATED, day=DAY)

    response = await client.get("/api/v1/analytics/knowledge", params=WINDOW, headers=auth)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["notes_created"] == 3
    assert body["notes_updated"] == 0
    assert body["concepts_created"] == 0
    assert body["resources_added"] == 0
    assert body["bookmarks_added"] == 0
    assert body["links_created"] == 0
    assert body["interactions"] == 3


async def test_knowledge_counts_notes_updated_when_that_is_the_only_event(client, db_session):
    """Updates are counted separately from creations, from the event name."""
    seed, auth = await seeded_client(client, db_session)
    for _ in range(4):
        await seed.activity(ActivityEvent.NOTE_UPDATED, day=DAY)

    response = await client.get("/api/v1/analytics/knowledge", params=WINDOW, headers=auth)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["notes_updated"] == 4
    assert body["notes_created"] == 0
    assert body["concepts_created"] == 0
    assert body["resources_added"] == 0
    assert body["bookmarks_added"] == 0
    assert body["links_created"] == 0
    assert body["interactions"] == 4


async def test_knowledge_counts_concepts_created_when_that_is_the_only_event(client, db_session):
    seed, auth = await seeded_client(client, db_session)
    for _ in range(2):
        await seed.activity(ActivityEvent.CONCEPT_CREATED, day=DAY)

    response = await client.get("/api/v1/analytics/knowledge", params=WINDOW, headers=auth)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["concepts_created"] == 2
    assert body["notes_created"] == 0
    assert body["notes_updated"] == 0
    assert body["resources_added"] == 0
    assert body["bookmarks_added"] == 0
    assert body["links_created"] == 0
    assert body["interactions"] == 2


async def test_knowledge_counts_resources_added_when_that_is_the_only_event(client, db_session):
    seed, auth = await seeded_client(client, db_session)
    await seed.activity(ActivityEvent.RESOURCE_CREATED, day=DAY)

    response = await client.get("/api/v1/analytics/knowledge", params=WINDOW, headers=auth)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["resources_added"] == 1
    assert body["notes_created"] == 0
    assert body["notes_updated"] == 0
    assert body["concepts_created"] == 0
    assert body["bookmarks_added"] == 0
    assert body["links_created"] == 0
    assert body["interactions"] == 1


async def test_knowledge_counts_bookmarks_added_when_that_is_the_only_event(client, db_session):
    seed, auth = await seeded_client(client, db_session)
    for _ in range(5):
        await seed.activity(ActivityEvent.BOOKMARK_CREATED, day=DAY)

    response = await client.get("/api/v1/analytics/knowledge", params=WINDOW, headers=auth)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["bookmarks_added"] == 5
    assert body["notes_created"] == 0
    assert body["notes_updated"] == 0
    assert body["concepts_created"] == 0
    assert body["resources_added"] == 0
    assert body["links_created"] == 0
    assert body["interactions"] == 5


async def test_knowledge_counts_links_created_when_that_is_the_only_event(client, db_session):
    """``knowledge_link_created`` counts as a link; its removal does not.

    A removed link is still an interaction worth recording, so it is in the
    total, but ``links_created`` answers a different question — how many edges
    were added — and folding removals into it would make the figure fall when
    the user tidies up after themselves.
    """
    seed, auth = await seeded_client(client, db_session)
    for _ in range(3):
        await seed.activity(ActivityEvent.KNOWLEDGE_LINK_CREATED, day=DAY)
    await seed.activity(ActivityEvent.KNOWLEDGE_LINK_REMOVED, day=DAY)

    response = await client.get("/api/v1/analytics/knowledge", params=WINDOW, headers=auth)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["links_created"] == 3
    assert body["notes_created"] == 0
    assert body["notes_updated"] == 0
    assert body["concepts_created"] == 0
    assert body["resources_added"] == 0
    assert body["bookmarks_added"] == 0
    assert body["interactions"] == 4


async def test_knowledge_reports_no_view_count_because_none_is_recorded(client, db_session):
    """Knowledge *views* are not computable, so no view figure is invented.

    :class:`app.models.enums.ActivityEvent` has no ``note_viewed`` member —
    Phase 5 records writes only — so a views counter would have to be a proxy
    for something else. The test asserts the endpoint does not answer a question
    the stored rows cannot support: the payload never mentions a view, and its
    interaction total is exactly the number of writes.
    """
    seed, auth = await seeded_client(client, db_session)
    for _ in range(2):
        await seed.activity(ActivityEvent.NOTE_CREATED, day=DAY)
    await seed.activity(ActivityEvent.NOTE_PUBLISHED, day=DAY)

    response = await client.get("/api/v1/analytics/knowledge", params=WINDOW, headers=auth)

    body = response.json()
    assert response.status_code == 200, response.text
    assert "view" not in response.text.lower()
    # Publishing is a write, and it is counted as one.
    assert body["interactions"] == 3
    assert body["notes_updated"] == 1


async def test_knowledge_counts_the_most_used_tags_ordered_by_use(client, db_session):
    """Python on 3 notes, sql on 2, dsa on 1 — ordered 3, 2, 1.

    The window is applied to ``notes.updated_at``, which is the only link from a
    tag to a day that Phase 5's rows support. The fourth note carries the tag
    "rust" but was last saved the day before the window, so it must not appear:
    a "most used tags" list that ignores the requested range is a list of the
    whole knowledge base wearing a date filter.
    """
    seed, auth = await seeded_client(client, db_session)
    first = await seed.note(day=DAY, updated_at=at(DAY, 15))
    second = await seed.note(day=DAY, updated_at=at(DAY, 16))
    third = await seed.note(day=DAY, updated_at=at(DAY, 17))
    stale = await seed.note(day=DAY - timedelta(days=1), updated_at=at(DAY - timedelta(days=1), 9))

    for note in (first, second, third):
        await tag_note(seed, note_id=note.id, name="python")
    for note in (first, second):
        await tag_note(seed, note_id=note.id, name="sql")
    await tag_note(seed, note_id=first.id, name="dsa")
    await tag_note(seed, note_id=stale.id, name="rust")

    response = await client.get("/api/v1/analytics/knowledge", params=WINDOW, headers=auth)

    assert response.status_code == 200, response.text
    body = response.json()
    assert [(tag["label"], tag["count"]) for tag in body["most_used_tags"]] == [
        ("python", 3),
        ("sql", 2),
        ("dsa", 1),
    ]
    assert "rust" not in [tag["label"] for tag in body["most_used_tags"]]


async def test_knowledge_reports_the_note_status_split_of_the_whole_base(client, db_session):
    """``notes_by_status`` is a state count, not a windowed event count.

    Two drafts and one published note exist; the response reports 2 / 1 / 0 and
    a total of 3, every status present even though nothing is archived. The
    shape does not change when the last archived note is deleted, so no client
    needs a ``.get()`` default for a bucket that happens to be empty.
    """
    seed, auth = await seeded_client(client, db_session)
    await seed.note(day=DAY, status=NoteStatus.DRAFT.value)
    await seed.note(day=DAY, status=NoteStatus.DRAFT.value)
    await seed.note(day=DAY, status=NoteStatus.PUBLISHED.value)

    response = await client.get("/api/v1/analytics/knowledge", params=WINDOW, headers=auth)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["notes_by_status"] == {"draft": 2, "published": 1, "archived": 0, "total": 3}
    assert body["notes_published"] == 1
    assert body["documents_added"] == 0


async def test_knowledge_returns_no_most_active_concepts_because_none_are_computed(
    client, db_session
):
    """``most_active_concepts`` is empty for every fixture in this suite.

    The field is part of the response model and the analytics page renders it
    as "most active knowledge areas", but ``AnalyticsService.knowledge`` never
    populates it — there is no query behind it yet. Asserted as a
    characterisation so that when a concept query is added, this test is the one
    that has to change. See the handoff note on
    ``app/services/analytics/service.py:948``.
    """
    seed, auth = await seeded_client(client, db_session)
    await seed.activity(ActivityEvent.CONCEPT_CREATED, day=DAY)
    await seed.activity(ActivityEvent.NOTE_CREATED, day=DAY)

    response = await client.get("/api/v1/analytics/knowledge", params=WINDOW, headers=auth)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["concepts_created"] == 1
    assert body["most_active_concepts"] == []
    assert body["top_tags"] == []


async def test_knowledge_reports_only_the_callers_own_activity(client, db_session):
    """Another account's knowledge events are invisible, and a tag too.

    Every statement is scoped by ``owner_id``. A user with three notes and a
    ``sql`` tag who sees "0 notes, no tags" from someone else's activity has
    been told nothing about them; a cross-tenant knowledge dashboard would be
    both the privacy failure the brief names and a leak of what somebody else is
    working on.
    """
    seed, _auth = await seeded_client(client, db_session)
    for _ in range(3):
        await seed.activity(ActivityEvent.NOTE_CREATED, day=DAY)
    mine = await seed.note(day=DAY, updated_at=at(DAY, 15))
    await tag_note(seed, note_id=mine.id, name="mine")

    _, other_auth = await seeded_client(
        client, db_session, username="grace", email="grace@nexus.test"
    )
    for _ in range(5):
        await seed.activity(ActivityEvent.NOTE_CREATED, day=DAY, metadata={"by": "grace"})
    theirs = await seed.note(day=DAY, updated_at=at(DAY, 15))
    await tag_note(seed, note_id=theirs.id, name="theirs")

    response = await client.get("/api/v1/analytics/knowledge", params=WINDOW, headers=other_auth)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["notes_created"] == 0
    assert body["interactions"] == 0
    assert body["available"] is False
    assert body["most_used_tags"] == []
    assert body["notes_by_status"] == {"draft": 0, "published": 0, "archived": 0, "total": 0}


# -- ML DATA PREPARATION / FEATURE SNAPSHOTS --------------------------------


async def _feature_fixture(seed: AnalyticsSeed) -> tuple[uuid.UUID, date]:
    """Seed the fixed feature-extraction fixture and return the task and today.

    One project, four tasks and one work session, laid out so every feature is
    determined by hand:

    * ``subject`` — high priority, estimated at 60 minutes, tracked at 45, due
      on the anchor day, rescheduled twice, with a 45-minute session starting at
      14:00 on that day.
    * ``open_undated`` — open, no due date, so it is neither in the overdue
      counts nor a deadline feature.
    * two completed tasks finished five days ago, which is inside the 30-day
      velocity window and outside it is not.

    Hence: 4 tasks in the project, 2 of them completed (50%), 2 open, 1 of the
    open ones overdue, 1 session totalling 45 minutes.
    """
    today = await db_today(seed.session)
    project = await seed.project()
    recent = today - timedelta(days=5)

    subject = await seed.task(
        project_id=project.id,
        title="feature subject",
        priority=TaskPriority.HIGH.value,
        estimated_minutes=60,
        actual_minutes=45,
        due_date=DAY,
        created_at=at(DAY),
    )
    await seed.task(project_id=project.id, title="open undated", created_at=at(DAY))
    for index in (1, 2):
        await seed.task(
            project_id=project.id,
            title=f"finished {index}",
            status="completed",
            created_at=at(recent),
            completed_at=at(recent, 17),
            actual_minutes=30,
        )
    # Placed at 14:00 in the *server's* calendar rather than 14:00 UTC.
    # ``time_of_day`` is read from that calendar, and ``AnalyticsSeed.work_session``
    # builds its ``start_hour`` as a UTC instant — 14:00 UTC is 19:30 in
    # ``Asia/Calcutta``, so leaving it there would make the asserted 14 a
    # statement about this server's offset rather than about the fixture.
    zone = await db_zone(seed.session)
    started = datetime(DAY.year, DAY.month, DAY.day, 14, tzinfo=zone).astimezone(UTC)
    tracked = await seed.work_session(day=DAY, minutes=45, task_id=subject.id)
    tracked.scheduled_start = started
    tracked.scheduled_end = started + timedelta(minutes=45)
    tracked.actual_start = started
    tracked.actual_end = started + timedelta(minutes=45)
    await seed.flush()
    for _ in range(2):
        await seed.activity(
            ActivityEvent.TASK_RESCHEDULED, day=DAY, task_id=subject.id, project_id=project.id
        )
    return subject.id, today


async def test_the_feature_snapshot_is_an_exactly_derived_row_for_one_task(client, db_session):
    """Every feature value, stated.

    This is the Phase 10 training set, so "the key exists" is not a useful
    assertion: a feature carrying a plausible but unverified number is worse
    than a missing one, because a model cannot tell it apart from an
    observation. Every figure below is derived from the fixture above:

    ``priority`` 3 (high is the third ordinal), ``estimated_minutes`` 60,
    ``actual_minutes`` 45 because a session backs it, ``reschedule_count`` 2,
    ``project_open_task_count`` 2, ``historical_completion_rate`` 50.0 (2 of 4),
    ``recent_work_minutes`` 45, ``work_session_count`` 1, ``time_of_day`` 14
    from the session's start rather than the task's creation, ``day_of_week`` 0
    because the anchor is a Monday, ``project_velocity`` 2 completed inside the
    30-day window, and ``overdue_count`` / ``task_age_days`` /
    ``deadline_distance_days`` from the database clock against the due date.

    The matrix is compared against exactly the fifteen keys below, and the
    wrapper is asserted around it by :func:`snapshot_for` — so this test remains
    the *values* claim it was written as, rather than a second statement about
    the shape.
    """
    seed, auth = await seeded_client(client, db_session)
    task_id, today = await _feature_fixture(seed)

    body = await snapshot_for(db_session, client, auth, task_id=task_id, today=today)
    age = (today - DAY).days
    assert body["features"] == {
        "priority": 3,
        "task_age_days": age,
        "estimated_minutes": 60,
        "actual_minutes": 45,
        "deadline_distance_days": -age,
        "reschedule_count": 2,
        "project_open_task_count": 2,
        "historical_completion_rate": 50.0,
        "recent_work_minutes": 45,
        "work_session_count": 1,
        "time_of_day": 14,
        "day_of_week": 0,
        "project_velocity": 2,
        "overdue_count": age,
        "project_overdue_task_count": 1,
    }


async def test_the_feature_snapshot_keys_are_stable_across_two_calls(client, db_session):
    """Same task, two calls: identical keys in identical order, identical values.

    Phase 10 builds a column from this object. A key that appeared on one call
    and not the next — or drifted to a different position — would produce a
    feature matrix whose columns mean different things per row, which is the
    failure this endpoint exists to make impossible.

    The guarantee is about the **matrix**, so it is asserted against
    ``features``: the fifteen keys in one order on both calls, with identical
    values. The wrapper is a fixed four-key envelope around that and cannot
    drift per row, and the whole-body comparison is kept as well so the two
    extractions are shown to be identical objects — ``generated_at`` being the
    database's current date, which two calls in one test resolve alike.
    """
    seed, auth = await seeded_client(client, db_session)
    task_id, _ = await _feature_fixture(seed)

    first = await client.get(
        "/api/v1/analytics/feature-snapshot", params={"task_id": str(task_id)}, headers=auth
    )
    second = await client.get(
        "/api/v1/analytics/feature-snapshot", params={"task_id": str(task_id)}, headers=auth
    )

    assert first.status_code == second.status_code == 200, first.text
    first_body, second_body = first.json(), second.json()
    assert len(first_body["features"]) == 15
    assert list(first_body["features"]) == list(second_body["features"])
    assert first_body["features"] == second_body["features"]
    assert first_body["schema_version"] == second_body["schema_version"] == "analytics_features.v1"
    assert first_body["task_id"] == second_body["task_id"] == str(task_id)
    assert first_body["generated_at"] == second_body["generated_at"]
    assert list(first_body) == list(second_body)
    assert first_body == second_body


async def test_the_feature_snapshot_is_null_where_nothing_was_observed(client, db_session):
    """No estimate, no sessions, no deadline: nulls, never manufactured zeros.

    A zero in a feature matrix means "observed, and it was zero" — no deadline
    pressure, no backlog. A fabricated one is indistinguishable from an
    observation once the rows are in a matrix, so every feature the fixture
    cannot support comes back as ``null`` with the key still present.

    **The task still belongs to a project.** ``tasks.project_id`` is
    ``NOT NULL``, so "no project" is not a state this schema can hold and the
    fixture is not allowed to pretend it is. What that costs is exactly two
    features: ``historical_completion_rate`` and ``project_velocity`` are the
    only ones the service resolves through an owner-scoped project lookup, so
    for a task in a real project they are always observable and here they are
    honest observations rather than gaps — 0 of 1 completed is 0.0%, and 0
    completions in the last 30 days is 0. The rest of the row is unobservable
    and says so.
    """
    seed, auth = await seeded_client(client, db_session)
    project = await seed.project()
    task = await seed.task(project_id=project.id, title="bare task", created_at=at(DAY))

    today = await db_today(db_session)
    body = await snapshot_for(db_session, client, auth, task_id=task.id, today=today)
    vector = body["features"]
    assert vector["estimated_minutes"] is None
    assert vector["actual_minutes"] is None
    assert vector["recent_work_minutes"] is None
    assert vector["time_of_day"] is None
    assert vector["deadline_distance_days"] is None
    # ...while the ones the row really does carry stay present and measured.
    assert vector["priority"] == 2
    assert vector["task_age_days"] == (today - DAY).days
    assert vector["reschedule_count"] == 0
    assert vector["work_session_count"] == 0
    assert vector["overdue_count"] == 0
    # Project-derived, so observable even for a brand-new project: the only task
    # in it is this one, nothing has been completed, and it is the sole open task.
    assert vector["historical_completion_rate"] == 0.0
    assert vector["project_velocity"] == 0
    assert vector["project_open_task_count"] == 1
    assert vector["project_overdue_task_count"] == 0


async def test_the_feature_snapshot_is_owner_scoped(client, db_session, assert_error_envelope):
    """Another account's task is **404**, exactly as a nonexistent id is.

    Not 403: a permission error says "that task exists and is not yours", which
    is enough to enumerate ids across the tenancy. The two answers are
    identical, so the route cannot be used to learn which ids are real — and a
    training set is not the place to be leaking somebody else's work into.
    """
    seed, _auth = await seeded_client(client, db_session)
    task_id, _ = await _feature_fixture(seed)
    _, other_auth = await seeded_client(
        client, db_session, username="grace", email="grace@nexus.test"
    )

    borrowed = await client.get(
        "/api/v1/analytics/feature-snapshot", params={"task_id": str(task_id)}, headers=other_auth
    )
    missing = await client.get(
        "/api/v1/analytics/feature-snapshot",
        params={"task_id": str(uuid.uuid4())},
        headers=other_auth,
    )

    borrowed_error = assert_error_envelope(borrowed, status_code=404, code="not_found")
    missing_error = assert_error_envelope(missing, status_code=404, code="not_found")
    assert borrowed_error["message"] == missing_error["message"]


# -- A USER WITH NO KNOWLEDGE ACTIVITY --------------------------------------


async def test_every_read_answers_two_hundred_for_a_user_with_no_activity(client, db_session):
    """Three 200s and a reason, for an account that has signed up and stopped.

    "Never crash because there is no activity" is the brief's data-quality
    rule. Both aggregate reads answer ``available=False`` with the shared
    "Not enough activity yet" wording rather than a zero, and the feature
    snapshot answers a matrix of nulls for a task that was only ever created —
    wrapped, as it always is, in the provenance keys that say which extraction
    and which task the row belongs to.
    """
    seed, auth = await seeded_client(client, db_session)
    # A task is always in a project — `tasks.project_id` is NOT NULL — and an
    # empty project records no activity, so this stays the "signed up and
    # stopped" account the test is about.
    project = await seed.project()
    task = await seed.task(project_id=project.id, title="created and left", created_at=at(DAY))

    learning = await client.get("/api/v1/analytics/learning", params=WINDOW, headers=auth)
    knowledge = await client.get("/api/v1/analytics/knowledge", params=WINDOW, headers=auth)
    snapshot = await client.get(
        "/api/v1/analytics/feature-snapshot", params={"task_id": str(task.id)}, headers=auth
    )

    assert learning.status_code == 200, learning.text
    assert learning.json()["available"] is False
    assert learning.json()["reason_if_unavailable"].startswith("Not enough activity yet")
    assert learning.json()["study_events"] == 0
    assert learning.json()["study_minutes"] == 0
    assert learning.json()["knowledge_interactions"] == 0

    assert knowledge.status_code == 200, knowledge.text
    assert knowledge.json()["available"] is False
    assert knowledge.json()["reason_if_unavailable"].startswith("Not enough activity yet")
    assert knowledge.json()["interactions"] == 0
    assert knowledge.json()["most_used_tags"] == []

    assert snapshot.status_code == 200, snapshot.text
    assert snapshot.json()["features"]["estimated_minutes"] is None
    assert snapshot.json()["features"]["actual_minutes"] is None


async def test_a_study_block_alone_makes_learning_available(client, db_session):
    """One calendar block is enough: ``available`` follows what was recorded.

    With no knowledge events at all the interaction counters are legitimately
    zero — they are counts, and zero of them is a true statement — but the read
    must not present the window as empty when a 45-minute study block was
    booked in it.
    """
    seed, auth = await seeded_client(client, db_session)
    await study_event(seed, day=DAY, minutes=45)

    response = await client.get("/api/v1/analytics/learning", params=WINDOW, headers=auth)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["available"] is True
    assert body["reason_if_unavailable"] is None
    assert body["study_events"] == 1
    assert body["knowledge_interactions"] == 0


async def test_a_knowledge_event_alone_makes_learning_available(client, db_session):
    """The same rule from the other side: writes count even with no study block."""
    seed, auth = await seeded_client(client, db_session)
    await seed.activity(ActivityEvent.NOTE_CREATED, day=DAY)

    response = await client.get("/api/v1/analytics/learning", params=WINDOW, headers=auth)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["available"] is True
    assert body["study_events"] == 0
    assert body["study_minutes"] == 0
    assert body["knowledge_interactions"] == 1


async def test_knowledge_events_outside_the_window_do_not_make_it_available(client, db_session):
    """A January event does not make an October window look active.

    The default window ends on the database's current date, so a fixture
    anchored at :data:`DAY` is months behind it. Requesting the explicit window
    must therefore report "not enough activity yet" rather than counting a year
    of stale rows.
    """
    seed, auth = await seeded_client(client, db_session)
    await seed.activity(ActivityEvent.NOTE_CREATED, day=DAY)
    recent = await db_today(db_session)
    window = {
        "start_date": recent.isoformat(),
        "end_date": (recent + timedelta(days=2)).isoformat(),
    }

    response = await client.get("/api/v1/analytics/knowledge", params=window, headers=auth)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["available"] is False
    assert body["notes_created"] == 0
    assert body["interactions"] == 0
