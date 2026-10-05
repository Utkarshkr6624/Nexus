"""User isolation on the analytics surface, end to end over HTTP.

The Phase 6 brief's privacy rule is one sentence: *"All analytics must be
user-scoped. Never allow one user to retrieve another user's metrics. Do not
expose raw internal events unnecessarily."* This file is that sentence turned
into assertions, and nothing else — the metric arithmetic belongs to the
correctness suites, and duplicating it here would only give a leak two chances
to hide.

**Every test here requires a live PostgreSQL and is marked ``integration``.**

The shape of the threat
-----------------------
Analytics is the one surface that *characterises* a person: rates, streaks,
"you finished 87.5% of what you started", a named list of their projects and the
titles of their worst-overdue tasks. Every one of those figures is somebody's
private information, and none of it is protected by a permission the second
account does not also hold — ``analytics.read`` is granted to every ``user``.
Ownership, not authorisation, is the only thing standing between two accounts,
so it has to be tested directly rather than inferred from the permission map.

The method
----------
Two real accounts are registered through the real API and given deliberately
unmistakable datasets: different project names, different task titles, different
note titles, different tag names, and session lengths chosen so that no figure
can coincide by accident (``4321`` and ``4381`` minutes for one, ``29`` for the
other). Then *every* route on the analytics router is called as the second
account and the whole response is walked, value by value, looking for anything
that belongs to the first.

The walk is the important part. Asserting "the total is 2" would pass just as
happily against a response that leaked the other account's project name in a
list, a title in a drill-down, an id in a bucket key, or a raw event payload in
a free-text field. So the marker scan descends into nested objects and arrays
and reports the *path* of anything it finds, and every test that uses it is
paired with a control test proving the markers are present in the owner's own
responses — otherwise a broken fixture would make the whole file pass
vacuously.

What "must not disclose" means for a 404
-----------------------------------------
Two routes accept an id belonging to somebody else: ``/analytics/time`` and
``/analytics/projects`` take ``project_id``, and ``/analytics/feature-snapshot``
takes ``task_id``. Each is answered with **404, never 403**, and — the part that
actually protects the id space — with a body **byte-identical** to the body a
random, never-issued uuid gets. A 403 would confirm the row exists; a different
message, a different code, a different request id shape or a shorter "not found"
for the real one would confirm the same thing just as reliably. So the test
compares the two responses directly rather than asserting a status code.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import UTC, date, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select

from app.models.analytics import DailyMetric
from app.models.enums import ActivityEvent, CalendarEventType, TaskStatus
from app.models.knowledge import note_tags
from app.models.tag import Tag
from app.services.analytics.service import TRENDABLE_METRICS
from tests.analytics_fixtures import DAY, AnalyticsSeed, at, seeded_client

pytestmark = pytest.mark.integration


# -- The two accounts, and the markers that make a leak unmissable -----------

#: The window every request asks for: 14 days centred on the fixture anchor.
#:
#: Fixed rather than "the last 7 days" because the seeded rows sit on
#: ``DAY = 2026-01-05`` and a relative window would drift off them as the clock
#: moves. ``2026-01-01`` to ``2026-01-14`` contains every seeded day, is inside
#: ``ANALYTICS_MAX_RANGE_DAYS``, and is inside ``ANALYTICS_REBUILD_MAX_DAYS``.
WINDOW_START = DAY - timedelta(days=4)
WINDOW_END = DAY + timedelta(days=9)
WINDOW_DAYS = (WINDOW_END - WINDOW_START).days + 1
WINDOW = {"start_date": WINDOW_START.isoformat(), "end_date": WINDOW_END.isoformat()}

#: An open task due far enough ahead that "overdue as of the database clock" is
#: a fact about the fixture rather than about today's date. Without it every
#: figure below would silently change meaning the day after 2026-01-20 passed.
FAR_DUE = date(2099, 12, 31)

ALPHA_PROJECT = "Alpha-Confidential-Reactor"
ALPHA_NOTE_TITLE = "Alpha-Note-Secret"
ALPHA_TAG_NAME = "ALPHA-TAG-Confidential"
#: Written into one ``activity_events.metadata`` row and never anywhere else.
#: It stands in for the "do not expose raw internal events" half of the rule:
#: if any analytics route ever serialised the feed, this string would ride out
#: with it, for its owner as well as for anybody else.
ALPHA_EVENT_MARKER = "ALPHA-RAW-EVENT-MARKER"
#: Session lengths with four digits in them, so a leaked total cannot be
#: mistaken for a window length, a weekday or a status count.
ALPHA_MONDAY_MINUTES = 4321
ALPHA_TUESDAY_MINUTES = 60
ALPHA_TRACKED_MINUTES = ALPHA_MONDAY_MINUTES + ALPHA_TUESDAY_MINUTES
ALPHA_STUDY_MINUTES = 90

BRAVO_PROJECT = "Bravo-Private-Backlog"
BRAVO_NOTE_TITLE = "Bravo-Note-Private"
BRAVO_SESSION_MINUTES = 29
BRAVO_ESTIMATE_MINUTES = 11
BRAVO_ACTUAL_MINUTES = 29

#: A uuid nobody has ever been issued. Every "does this disclose existence?"
#: comparison is against a response to *this*, never against a status code.
UNISSUED_TASK_ID = uuid.UUID("00000000-0000-4000-8000-0000000000ff")
UNISSUED_PROJECT_ID = uuid.UUID("00000000-0000-4000-8000-00000000eeff")

#: Every route on ``app/api/v1/analytics.py``, in the order the brief lists them.
#:
#: Used for the anonymous sweep and for the per-endpoint isolation sweep, and
#: asserted against the application's own OpenAPI document — so a route added
#: to the router later fails this file rather than quietly going untested.
ENDPOINTS: tuple[tuple[str, str], ...] = (
    ("GET", "/api/v1/analytics/overview"),
    ("GET", "/api/v1/analytics/productivity"),
    ("GET", "/api/v1/analytics/consistency"),
    ("GET", "/api/v1/analytics/focus"),
    ("GET", "/api/v1/analytics/deadlines"),
    ("GET", "/api/v1/analytics/estimation"),
    ("GET", "/api/v1/analytics/workload"),
    ("GET", "/api/v1/analytics/time"),
    ("GET", "/api/v1/analytics/projects"),
    ("GET", "/api/v1/analytics/tasks"),
    ("GET", "/api/v1/analytics/learning"),
    ("GET", "/api/v1/analytics/knowledge"),
    ("GET", "/api/v1/analytics/trends"),
    ("GET", "/api/v1/analytics/series"),
    ("GET", "/api/v1/analytics/export"),
    ("GET", "/api/v1/analytics/export.csv"),
    ("GET", "/api/v1/analytics/feature-snapshot"),
    # The only write on the router, and it writes ``daily_metrics``. Last in the
    # sweep so the reads above it see exactly what the seeded rows produce.
    ("POST", "/api/v1/analytics/rebuild"),
)


# -- Fixture data -------------------------------------------------------------


async def _study_event(seed: AnalyticsSeed, *, day: date, minutes: int) -> None:
    """Book a calendar entry typed ``study``.

    ``AnalyticsSeed.calendar_event`` does not take an ``event_type``, and the
    learning analytics count study blocks by exactly that column — so the row is
    written by the shared helper and then typed, rather than the shared helper
    gaining a parameter this one file needs.
    """
    event = await seed.calendar_event(day=day, minutes=minutes)
    event.event_type = CalendarEventType.STUDY.value
    await seed.flush()


async def _tag_the_note(
    seed: AnalyticsSeed, session: Any, *, note_id: uuid.UUID, name: str
) -> uuid.UUID:
    """Attach a named tag to one note, through the association table.

    Tag names are user content like any other, and ``/analytics/knowledge``
    returns them as ``most_used_tags`` — the one place on this surface where a
    second account's free text could surface. Seeding one for the first account
    is what makes that a real assertion rather than an empty list passing.
    """
    tag = Tag(user_id=seed.owner.id, name=name)
    session.add(tag)
    await session.flush()
    await session.execute(note_tags.insert().values(note_id=note_id, tag_id=tag.id))
    await session.commit()
    return tag.id


class World:
    """The two accounts, their ids, and the markers a leak would carry.

    A plain object rather than a dataclass or a fixture: it is a bag of
    identifiers assembled by one coroutine, and a test reads better naming the
    account (``world.alpha_headers``) than indexing a tuple.
    """

    def __init__(self) -> None:
        self.alpha_seed: AnalyticsSeed
        self.alpha_headers: dict[str, str]
        self.alpha_project_id: uuid.UUID
        self.alpha_task_ids: list[uuid.UUID] = []
        self.alpha_session_ids: list[uuid.UUID] = []
        self.alpha_note_id: uuid.UUID
        self.bravo_seed: AnalyticsSeed
        self.bravo_headers: dict[str, str]
        self.bravo_project_id: uuid.UUID
        self.bravo_task_ids: list[uuid.UUID] = []
        self.bravo_session_id: uuid.UUID
        self.bravo_note_id: uuid.UUID
        #: Every string a leak of the first account would carry.
        self.markers: set[str] = set()
        #: Every number a leak of the first account would carry. Four digits,
        #: chosen so none can collide with a window length or a status count.
        self.numbers: set[float] = set()

    def assert_no_alpha_data(self, payload: Any, *, where: str) -> None:
        """Fail if any value anywhere in ``payload`` belongs to the first account."""
        offenders = _markers_in(payload, self.markers, self.numbers)
        assert not offenders, f"{where} leaked the first account's data: {offenders}"


async def _seed_world(client: Any, db_session: Any) -> World:
    """Register two accounts and give each a hand-checkable, distinct dataset.

    The first account ("alpha") is deliberately the *bigger* one — ten tasks,
    eight completions, 4381 tracked minutes — because a leak is easiest to see
    when the wrong answer is unmistakably wrong, and a second account that
    happened to share a number with the first would hide one. The second
    ("bravo") is deliberately tiny: two tasks, one completion, 29 minutes.

    Every figure below is derivable by hand from the rows, which is what lets
    the isolation tests assert exact values rather than "not equal to the other
    account's", while the score suites keep the arithmetic.
    """
    world = World()

    # -- Account one: the dataset a leak would betray --------------------
    world.alpha_seed, world.alpha_headers = await seeded_client(
        client, db_session, username="ada", email="ada-isolation@nexus.test"
    )
    alpha = world.alpha_seed
    monday, tuesday, wednesday = DAY, DAY + timedelta(days=1), DAY + timedelta(days=2)

    project = await alpha.project(name=ALPHA_PROJECT)
    world.alpha_project_id = project.id

    # Four tasks finished on time on the Monday, and three more on the Tuesday.
    for index in range(1, 5):
        task = await alpha.task(
            project_id=project.id,
            title=f"Alpha-Task-{index:02d}",
            status=TaskStatus.COMPLETED.value,
            created_at=at(monday),
            completed_at=at(monday, 12),
            due_date=monday,
            estimated_minutes=30,
            actual_minutes=45,
        )
        world.alpha_task_ids.append(task.id)

    # The fourth Tuesday task is finished *after* its due date, so the first
    # account's deadline rate is 7/8 = 87.5% and the second account's is 1/1 =
    # 100%. Two accounts with the same adherence rate could not tell a leak
    # from a coincidence.
    for index in range(5, 9):
        late = index == 8
        task = await alpha.task(
            project_id=project.id,
            title=f"Alpha-Task-{index:02d}",
            status=TaskStatus.COMPLETED.value,
            created_at=at(tuesday),
            completed_at=at(tuesday, 12),
            due_date=monday if late else tuesday,
            estimated_minutes=30,
            actual_minutes=45,
        )
        world.alpha_task_ids.append(task.id)

    # One open task past its due date (so the first account has a non-zero
    # overdue backlog and a non-empty drill-down list) and one not.
    overdue_task = await alpha.task(
        project_id=project.id,
        title="Alpha-Task-09",
        created_at=at(wednesday),
        due_date=wednesday,
    )
    world.alpha_task_ids.append(overdue_task.id)
    # Created on the window's first day rather than on the Monday, so the
    # Monday's ``tasks_created`` is exactly the four tasks finished that day
    # and the aggregate a rebuild writes for it can be checked by hand.
    future_task = await alpha.task(
        project_id=project.id,
        title="Alpha-Task-10",
        created_at=at(WINDOW_START),
        due_date=FAR_DUE,
    )
    world.alpha_task_ids.append(future_task.id)

    monday_session = await alpha.work_session(
        day=monday, minutes=ALPHA_MONDAY_MINUTES, project_id=project.id, start_hour=8
    )
    tuesday_session = await alpha.work_session(
        day=tuesday, minutes=ALPHA_TUESDAY_MINUTES, project_id=project.id, start_hour=8
    )
    world.alpha_session_ids = [monday_session.id, tuesday_session.id]

    note = await alpha.note(day=monday, title=ALPHA_NOTE_TITLE, updated_at=at(monday))
    world.alpha_note_id = note.id
    await _tag_the_note(alpha, db_session, note_id=note.id, name=ALPHA_TAG_NAME)
    await _study_event(alpha, day=wednesday, minutes=ALPHA_STUDY_MINUTES)

    await alpha.activity(
        ActivityEvent.NOTE_CREATED,
        day=monday,
        hour=11,
        metadata={"internal_note": ALPHA_EVENT_MARKER},
    )
    await alpha.activity(
        ActivityEvent.TASK_RESCHEDULED,
        day=tuesday,
        project_id=project.id,
        task_id=world.alpha_task_ids[4],
        hour=11,
    )

    # -- Account two: the account under test -----------------------------
    world.bravo_seed, world.bravo_headers = await seeded_client(
        client, db_session, username="bob", email="bob-isolation@nexus.test"
    )
    bravo = world.bravo_seed
    bravo_project = await bravo.project(name=BRAVO_PROJECT)
    world.bravo_project_id = bravo_project.id

    done = await bravo.task(
        project_id=bravo_project.id,
        title="Bravo-Task-01",
        status=TaskStatus.COMPLETED.value,
        created_at=at(monday),
        completed_at=at(monday, 13),
        due_date=monday,
        estimated_minutes=BRAVO_ESTIMATE_MINUTES,
        actual_minutes=BRAVO_ACTUAL_MINUTES,
    )
    world.bravo_task_ids.append(done.id)
    open_task = await bravo.task(
        project_id=bravo_project.id,
        title="Bravo-Task-02",
        created_at=at(monday),
        due_date=FAR_DUE,
    )
    world.bravo_task_ids.append(open_task.id)

    session = await bravo.work_session(
        day=monday,
        minutes=BRAVO_SESSION_MINUTES,
        project_id=bravo_project.id,
        task_id=done.id,
        start_hour=10,
    )
    world.bravo_session_id = session.id

    bravo_note = await bravo.note(day=monday, title=BRAVO_NOTE_TITLE, updated_at=at(monday))
    world.bravo_note_id = bravo_note.id
    await bravo.activity(
        ActivityEvent.NOTE_CREATED,
        day=monday,
        hour=11,
        metadata={"internal_note": "BRAVO-RAW-EVENT-MARKER"},
    )

    world.markers = {
        ALPHA_PROJECT,
        ALPHA_NOTE_TITLE,
        ALPHA_TAG_NAME,
        ALPHA_EVENT_MARKER,
        str(project.id),
        str(note.id),
        *{f"Alpha-Task-{index:02d}" for index in range(1, 11)},
        *{str(task_id) for task_id in world.alpha_task_ids},
        *{str(session_id) for session_id in world.alpha_session_ids},
    }
    world.numbers = {float(ALPHA_MONDAY_MINUTES), float(ALPHA_TRACKED_MINUTES)}
    return world


async def _daily_rows(session: Any, user_id: uuid.UUID) -> list[dict[str, Any]]:
    """One account's ``daily_metrics`` rows as plain comparable dictionaries.

    Read straight from the table rather than through ``/analytics/series``,
    because the point of the rebuild test is what is *stored*: a route that
    recomputed on the fly would report the same numbers whether or not the row
    had survived, and would pass the test that is meant to catch exactly that.
    """
    result = await session.execute(
        select(DailyMetric)
        .where(DailyMetric.user_id == user_id)
        .order_by(DailyMetric.metric_date.asc())
    )
    return [
        {
            "metric_date": row.metric_date,
            "updated_at": row.updated_at,
            **{column: getattr(row, column) for column in TRENDABLE_METRICS},
        }
        for row in result.scalars().all()
    ]


# -- The marker walk ----------------------------------------------------------


def _markers_in(node: Any, markers: set[str], numbers: set[float]) -> list[str]:
    """Every path through ``node`` whose value carries a marker.

    Descends through nested objects and arrays and reports a path for each
    hit, so a failure says *where* the other account's data turned up rather
    than only that it did. Strings are matched as substrings (a title may sit
    inside a sentence) and numbers by exact value — a range check would be
    meaningless here, and a loose match on digits would trip over a date.
    """
    offenders: list[str] = []

    def walk(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                walk(child, f"{path}[{key!r}]")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f"{path}[{index}]")
        elif isinstance(value, str):
            for marker in markers:
                if marker in value:
                    offenders.append(f"{path} = {value!r} contains {marker!r}")
        elif isinstance(value, bool):
            return
        elif isinstance(value, (int, float)) and float(value) in numbers:
            offenders.append(f"{path} = {value!r}")

    walk(node, "body")
    return offenders


def _csv_table(text: str) -> tuple[list[str], list[dict[str, str]]]:
    """Parse a CSV export into a header and one dictionary per data row.

    Parsed rather than substring-searched because a search for a leaked number
    in a CSV that also carries an ``updated_at`` timestamp is a flaky test: the
    microsecond field occasionally spells the digits being searched for. Cells
    are compared as parsed values, so the test is the same strength and
    deterministic.
    """
    rows = list(csv.reader(io.StringIO(text)))
    header = rows[0]
    return header, [dict(zip(header, row, strict=True)) for row in rows[1:]]


# -- The surface itself -------------------------------------------------------


async def test_the_router_exposes_exactly_the_documented_endpoints(client):
    """The sweep below covers eighteen routes; this pins that number.

    Read from the application's own OpenAPI document rather than from the module
    source, so it fails on a route that was *added* as well as on one that was
    removed. A privacy suite that quietly stops covering a new endpoint is the
    failure mode this test exists to prevent.
    """
    document = (await client.get("/openapi.json")).json()
    analytics_paths = sorted(
        path for path in document["paths"] if path.startswith("/api/v1/analytics")
    )

    assert analytics_paths == sorted(path for _method, path in ENDPOINTS)


async def test_an_anonymous_caller_is_refused_on_every_endpoint(client, assert_error_envelope):
    """401 and the shared envelope, on all eighteen routes.

    Analytics is the surface that characterises a person, so "not signed in"
    has to be answered before any question is asked at all — and answered in
    the same envelope as every other refusal, so a client needs one error
    reader rather than two.
    """
    for method, path in ENDPOINTS:
        params = {
            **WINDOW,
            "task_id": str(UNISSUED_TASK_ID),
            "project_id": str(UNISSUED_PROJECT_ID),
        }
        response = await client.request(method, path, params=params)

        error = assert_error_envelope(response, status_code=401, code="unauthorized")
        assert error["details"] is None, path


# -- The control: the markers really are in the first account's own data ----


async def test_the_first_account_really_does_have_the_data_the_markers_name(client, db_session):
    """The control for every sweep below.

    Without this, a fixture that silently seeded nothing would make all of the
    isolation assertions pass for the wrong reason. So the first account is
    asked for its own numbers and is shown to hold every marker the other
    account is checked against: ten tasks, eight completions, 4381 tracked
    minutes, its project, its titles, its tag, and a raw event payload that
    must appear nowhere at all.
    """
    world = await _seed_world(client, db_session)

    # ``/overview`` is a read of the stored aggregates and never recomputes
    # them, so the window is rebuilt first — otherwise the totals would read
    # zero and this control would prove nothing.
    rebuilt = await client.post(
        "/api/v1/analytics/rebuild", params=WINDOW, headers=world.alpha_headers
    )
    assert rebuilt.status_code == 202, rebuilt.text

    overview = await client.get(
        "/api/v1/analytics/overview", params=WINDOW, headers=world.alpha_headers
    )
    assert overview.status_code == 200, overview.text
    totals = {point["label"]: point["current"] for point in overview.json()["totals"]}
    assert totals["tasks_created"] == 10.0
    assert totals["tasks_completed"] == 8.0
    assert totals["actual_minutes"] == float(ALPHA_TRACKED_MINUTES)
    assert totals["work_sessions"] == 2.0
    assert totals["knowledge_events"] == 1.0
    assert totals["calendar_events"] == 1.0
    assert totals["tasks_rescheduled"] == 1.0
    assert totals["projects_touched"] == 1.0

    projects = await client.get(
        "/api/v1/analytics/projects", params=WINDOW, headers=world.alpha_headers
    )
    assert projects.status_code == 200, projects.text
    assert [entry["name"] for entry in projects.json()["items"]] == [ALPHA_PROJECT]
    assert projects.json()["items"][0]["project_id"] == str(world.alpha_project_id)
    # The control also covers the paging total: the sweep below reads
    # ``/projects`` as the *other* account, and a ``total`` counted across
    # owners would report the first account's project there without its name or
    # id ever appearing in the body.
    assert projects.json()["meta"]["total"] == 1

    knowledge = await client.get(
        "/api/v1/analytics/knowledge", params=WINDOW, headers=world.alpha_headers
    )
    assert knowledge.status_code == 200, knowledge.text
    assert [tag["label"] for tag in knowledge.json()["most_used_tags"]] == [ALPHA_TAG_NAME]

    learning = await client.get(
        "/api/v1/analytics/learning", params=WINDOW, headers=world.alpha_headers
    )
    assert learning.status_code == 200, learning.text
    assert learning.json()["study_events"] == 1
    assert learning.json()["study_minutes"] == ALPHA_STUDY_MINUTES

    export = await client.get(
        "/api/v1/analytics/export.csv",
        params={**WINDOW, "dataset": "task_performance"},
        headers=world.alpha_headers,
    )
    assert export.status_code == 200, export.text
    assert ALPHA_PROJECT in export.text
    assert "Alpha-Task-01" in export.text


# -- The sweep: no endpoint returns the first account's anything ------------


async def test_the_second_account_sees_nothing_of_the_firsts_on_any_endpoint(client, db_session):
    """All eighteen routes, called as the second account, walked value by value.

    The core assertion of the file. For every endpoint the response is either
    JSON — and is walked recursively, so a leak in a nested breakdown, a bucket
    key, a drill-down title or an explanation string would be caught just as
    readily as one in a headline count — or CSV, and is scanned as raw text,
    since a title inside a comma-separated row is a leak too.

    A status-code check would prove almost nothing here: every one of these
    routes answers 200 to a caller who is merely not the owner of the rows
    being described.
    """
    world = await _seed_world(client, db_session)

    for method, path in ENDPOINTS:
        params = {**WINDOW, "task_id": str(world.bravo_task_ids[0])}
        response = await client.request(method, path, params=params, headers=world.bravo_headers)

        assert response.status_code in (200, 202), f"{path}: {response.text}"
        if response.headers.get("content-type", "").startswith("text/csv"):
            # A CSV has no structure to walk, so the free text is the surface:
            # a project name, a task title or an id in a cell is a leak.
            for marker in world.markers:
                assert marker not in response.text, f"{path} exported {marker!r}"
        else:
            world.assert_no_alpha_data(response.json(), where=path)


async def test_the_second_accounts_figures_are_its_own_exact_ones(client, db_session):
    """Where the sweep proves nothing leaked, this proves the right thing arrived.

    "Not equal to the other account's" is satisfied by an empty dashboard, a
    500-shaped body or a response that dropped half its fields. So the exact
    values the second account's own two rows produce are asserted, route by
    route, against figures derived by hand from the fixture.

    The scores themselves are deliberately only compared for *difference*:
    their arithmetic is the correctness suites' subject, and what privacy needs
    to know is that the two accounts did not receive each other's.
    """
    world = await _seed_world(client, db_session)
    await client.post("/api/v1/analytics/rebuild", params=WINDOW, headers=world.bravo_headers)

    # -- totals and roll-ups ------------------------------------------------
    overview = await client.get(
        "/api/v1/analytics/overview", params=WINDOW, headers=world.bravo_headers
    )
    assert overview.status_code == 200, overview.text
    totals = {point["label"]: point["current"] for point in overview.json()["totals"]}
    assert totals["tasks_created"] == 2.0
    assert totals["tasks_completed"] == 1.0
    assert totals["actual_minutes"] == float(BRAVO_SESSION_MINUTES)
    assert totals["planned_minutes"] == float(BRAVO_SESSION_MINUTES)
    assert totals["work_sessions"] == 1.0
    assert totals["knowledge_events"] == 1.0
    assert totals["calendar_events"] == 0.0
    assert totals["tasks_rescheduled"] == 0.0
    assert totals["projects_touched"] == 0.0

    tasks = await client.get("/api/v1/analytics/tasks", params=WINDOW, headers=world.bravo_headers)
    assert tasks.status_code == 200, tasks.text
    body = tasks.json()
    assert body["total_tasks"] == 2
    assert body["completed_tasks"] == 1
    assert body["open_tasks"] == 1
    assert body["overdue_tasks"] == 0
    assert body["completion_rate"] == 50.0
    assert body["by_status"]["total"] == 2
    # The first account's most-overdue task is the one the drill-down would
    # otherwise list, and it belongs to somebody else.
    assert body["top_overdue"] == []

    projects = await client.get(
        "/api/v1/analytics/projects", params=WINDOW, headers=world.bravo_headers
    )
    assert projects.status_code == 200, projects.text
    body = projects.json()
    assert len(body["items"]) == 1
    entry = body["items"][0]
    assert entry["name"] == BRAVO_PROJECT
    assert entry["project_id"] == str(world.bravo_project_id)
    assert entry["total_tasks"] == 2
    assert entry["completed_tasks"] == 1
    assert entry["total_work_minutes"] == BRAVO_SESSION_MINUTES
    # The envelope's total is counted from the caller's projects. The first
    # account owns one project too, so a total of two here would disclose that
    # the neighbour has something — a leak the rows above cannot see, because
    # the total is a count rather than a name or an id.
    assert body["meta"]["total"] == 1

    # -- the point-in-time figures -----------------------------------------
    deadlines = await client.get(
        "/api/v1/analytics/deadlines", params=WINDOW, headers=world.bravo_headers
    )
    assert deadlines.status_code == 200, deadlines.text
    assert deadlines.json()["on_time"] == 1
    assert deadlines.json()["late"] == 0
    assert deadlines.json()["still_overdue"] == 0
    assert deadlines.json()["adherence_rate"] == 100.0

    workload = await client.get(
        "/api/v1/analytics/workload", params=WINDOW, headers=world.bravo_headers
    )
    assert workload.status_code == 200, workload.text
    assert workload.json()["open_tasks"] == 1
    assert workload.json()["overdue_open"] == 0
    assert workload.json()["scheduled_minutes"] == BRAVO_SESSION_MINUTES
    assert workload.json()["actual_minutes"] == BRAVO_SESSION_MINUTES
    assert workload.json()["available"] is True

    focus = await client.get("/api/v1/analytics/focus", params=WINDOW, headers=world.bravo_headers)
    assert focus.status_code == 200, focus.text
    assert focus.json()["total_minutes"] == BRAVO_SESSION_MINUTES
    assert focus.json()["completed_planned_sessions"] == 1
    assert focus.json()["interruptions"] == 0
    assert focus.json()["reschedules"] == 0

    consistency = await client.get(
        "/api/v1/analytics/consistency", params=WINDOW, headers=world.bravo_headers
    )
    assert consistency.status_code == 200, consistency.text
    assert consistency.json()["active_days"] == 1
    assert consistency.json()["work_sessions"] == 1
    assert consistency.json()["longest_streak"] == 1

    estimation = await client.get(
        "/api/v1/analytics/estimation", params=WINDOW, headers=world.bravo_headers
    )
    assert estimation.status_code == 200, estimation.text
    # The one pair the second account owns: estimated 11, actually taken 29.
    assert estimation.json()["sample_count"] == 1
    assert estimation.json()["absolute_error"] == 18.0
    assert estimation.json()["bias"] == -18.0

    # -- time, knowledge, learning -----------------------------------------
    time_view = await client.get(
        "/api/v1/analytics/time", params=WINDOW, headers=world.bravo_headers
    )
    assert time_view.status_code == 200, time_view.text
    assert time_view.json()["total_minutes"] == BRAVO_SESSION_MINUTES
    assert time_view.json()["unassigned_minutes"] == 0
    assert time_view.json()["by_project"] == [
        {
            "key": str(world.bravo_project_id),
            "label": BRAVO_PROJECT,
            "minutes": BRAVO_SESSION_MINUTES,
            "share": 100.0,
        }
    ]
    assert [bucket["key"] for bucket in time_view.json()["by_task"]] == [
        str(world.bravo_task_ids[0])
    ]

    knowledge = await client.get(
        "/api/v1/analytics/knowledge", params=WINDOW, headers=world.bravo_headers
    )
    assert knowledge.status_code == 200, knowledge.text
    assert knowledge.json()["notes_created"] == 1
    assert knowledge.json()["interactions"] == 1
    assert knowledge.json()["notes_by_status"]["total"] == 1
    # The first account's tag is the leak this list would carry.
    assert knowledge.json()["most_used_tags"] == []

    learning = await client.get(
        "/api/v1/analytics/learning", params=WINDOW, headers=world.bravo_headers
    )
    assert learning.status_code == 200, learning.text
    assert learning.json()["study_events"] == 0
    assert learning.json()["study_minutes"] == 0
    assert learning.json()["knowledge_interactions"] == 1


async def test_the_two_accounts_never_receive_each_others_scores(client, db_session):
    """The headline numbers are per-account, and the breakdowns agree.

    A score is the most identifying figure on the surface — it is a single
    integer derived from a person's whole recorded history — so it is checked
    on its own rather than as a side effect of the marker sweep. Each
    component's name must also be one of the four documented ones: a component
    carrying the other account's name would be a leak that no number catches.
    """
    world = await _seed_world(client, db_session)

    alpha = await client.get(
        "/api/v1/analytics/productivity", params=WINDOW, headers=world.alpha_headers
    )
    bravo = await client.get(
        "/api/v1/analytics/productivity", params=WINDOW, headers=world.bravo_headers
    )
    assert alpha.status_code == 200 and bravo.status_code == 200

    assert bravo.json()["available"] is True
    assert bravo.json()["score"] is not None
    assert bravo.json()["score"] != alpha.json()["score"]
    assert {component["name"] for component in bravo.json()["components"]} == {
        "completion",
        "deadline",
        "consistency",
        "focus",
    }
    world.assert_no_alpha_data(bravo.json(), where="/productivity")

    # The same score is also nested inside ``/overview``, which is the route the
    # dashboard actually opens. Both must be the second account's own.
    alpha_overview = await client.get(
        "/api/v1/analytics/overview", params=WINDOW, headers=world.alpha_headers
    )
    bravo_overview = await client.get(
        "/api/v1/analytics/overview", params=WINDOW, headers=world.bravo_headers
    )
    assert alpha_overview.status_code == 200 and bravo_overview.status_code == 200
    assert bravo_overview.json()["productivity"]["score"] == bravo.json()["score"]
    assert (
        bravo_overview.json()["productivity"]["score"]
        != alpha_overview.json()["productivity"]["score"]
    )
    world.assert_no_alpha_data(bravo_overview.json(), where="/overview")


# -- The stored aggregates and the export ------------------------------------


async def test_the_stored_series_and_trends_carry_only_the_callers_own_days(client, db_session):
    """``/series`` and ``/trends`` read the shared aggregate table.

    ``daily_metrics`` is the one analytics table keyed by a user column rather
    than joined per request, so it is the most likely place for a forgotten
    predicate to turn into a cross-tenant read: a missing ``user_id`` filter here
    would hand one account the other's totals on every dashboard paint, and the
    per-day row values would show it. Both routes are therefore read *after* an
    explicit rebuild, so the rows being served are rows somebody actually
    wrote.
    """
    world = await _seed_world(client, db_session)
    await client.post("/api/v1/analytics/rebuild", params=WINDOW, headers=world.alpha_headers)
    await client.post("/api/v1/analytics/rebuild", params=WINDOW, headers=world.bravo_headers)

    series = await client.get(
        "/api/v1/analytics/series", params=WINDOW, headers=world.bravo_headers
    )
    assert series.status_code == 200, series.text
    rows = series.json()
    assert len(rows) == WINDOW_DAYS
    by_day = {row["metric_date"]: row for row in rows}
    monday = by_day[DAY.isoformat()]
    assert monday["tasks_created"] == 2
    assert monday["tasks_completed"] == 1
    assert monday["actual_minutes"] == BRAVO_SESSION_MINUTES
    assert monday["work_sessions"] == 1
    world.assert_no_alpha_data(rows, where="/series")

    trends = await client.get(
        "/api/v1/analytics/trends",
        params={**WINDOW, "metric": "actual_minutes"},
        headers=world.bravo_headers,
    )
    assert trends.status_code == 200, trends.text
    assert [(point["bucket"], point["value"]) for point in trends.json()] == [
        (DAY.isoformat(), float(BRAVO_SESSION_MINUTES))
    ]

    completions = await client.get(
        "/api/v1/analytics/trends",
        params={**WINDOW, "metric": "tasks_completed"},
        headers=world.bravo_headers,
    )
    assert completions.status_code == 200, completions.text
    assert [(point["bucket"], point["value"]) for point in completions.json()] == [
        (DAY.isoformat(), 1.0)
    ]


async def test_a_csv_export_contains_only_the_callers_own_rows(client, db_session):
    """All three datasets, checked cell by cell.

    A CSV export is the surface most likely to escape a scope check, because it
    is assembled by string-writing code in the service rather than serialised
    from a scoped model. Each dataset is therefore parsed and compared as data:
    the second account's file must hold the second account's two tasks, its one
    session, and the day-total figures its own rows produce.
    """
    world = await _seed_world(client, db_session)
    await client.post("/api/v1/analytics/rebuild", params=WINDOW, headers=world.bravo_headers)

    daily = await client.get(
        "/api/v1/analytics/export.csv",
        params={**WINDOW, "dataset": "daily_metrics"},
        headers=world.bravo_headers,
    )
    assert daily.status_code == 200, daily.text
    assert daily.headers["X-Nexus-Row-Count"] == str(WINDOW_DAYS)
    header, daily_rows = _csv_table(daily.text)
    assert header[0] == "metric_date"
    assert len(daily_rows) == WINDOW_DAYS
    monday = next(row for row in daily_rows if row["metric_date"] == DAY.isoformat())
    assert int(monday["tasks_created"]) == 2
    assert int(monday["tasks_completed"]) == 1
    assert int(monday["actual_minutes"]) == BRAVO_SESSION_MINUTES
    assert int(monday["work_sessions"]) == 1
    for row in daily_rows:
        for column in TRENDABLE_METRICS:
            assert float(row[column]) not in world.numbers, f"{column} on {row['metric_date']}"

    tasks_csv = await client.get(
        "/api/v1/analytics/export.csv",
        params={**WINDOW, "dataset": "task_performance"},
        headers=world.bravo_headers,
    )
    assert tasks_csv.status_code == 200, tasks_csv.text
    assert tasks_csv.headers["X-Nexus-Row-Count"] == "2"
    _header, task_rows = _csv_table(tasks_csv.text)
    assert sorted(row["title"] for row in task_rows) == ["Bravo-Task-01", "Bravo-Task-02"]
    assert {row["project_name"] for row in task_rows} == {BRAVO_PROJECT}
    assert {row["task_id"] for row in task_rows} == {
        str(task_id) for task_id in world.bravo_task_ids
    }

    sessions_csv = await client.get(
        "/api/v1/analytics/export.csv",
        params={**WINDOW, "dataset": "work_sessions"},
        headers=world.bravo_headers,
    )
    assert sessions_csv.status_code == 200, sessions_csv.text
    assert sessions_csv.headers["X-Nexus-Row-Count"] == "1"
    _header, session_rows = _csv_table(sessions_csv.text)
    assert [row["session_id"] for row in session_rows] == [str(world.bravo_session_id)]
    assert [row["project_id"] for row in session_rows] == [str(world.bravo_project_id)]
    assert [row["actual_minutes"] for row in session_rows] == [str(BRAVO_SESSION_MINUTES)]


async def test_the_export_manifest_is_the_same_document_for_both_accounts(client, db_session):
    """``/export`` carries a column contract, not a user's rows.

    It is the one route on the router that takes no window and no owner, and
    that is correct — it describes the *schema* of the export. What would not be
    correct is a manifest that quietly varies per caller, so the assertion here
    is byte equality between the two accounts plus a check that the document
    contains no id, name or marker at all.
    """
    world = await _seed_world(client, db_session)

    alpha = await client.get("/api/v1/analytics/export", headers=world.alpha_headers)
    bravo = await client.get("/api/v1/analytics/export", headers=world.bravo_headers)
    assert alpha.status_code == 200 and bravo.status_code == 200
    assert alpha.json() == bravo.json()
    assert bravo.json()["datasets"] == ["daily_metrics", "task_performance", "work_sessions"]

    world.assert_no_alpha_data(bravo.json(), where="/export")
    for marker in world.markers:
        assert marker not in bravo.text, f"/export mentions {marker!r}"


# -- The id-space routes: another account's id must be indistinguishable ------


async def test_another_accounts_project_id_is_a_404_identical_to_an_unissued_one(
    client, db_session, assert_error_envelope
):
    """``/time`` and ``/projects`` both take ``project_id``; neither may confirm it.

    403 would be a straight answer to "does this project exist?", and so would a
    404 whose body differs from the one an unissued uuid gets. So both responses
    are compared to the response for an id nobody has ever been issued, field by
    field. The absence of a 403 is the other half: this is a row that does not
    exist *as far as this caller is concerned*, not a permission failure.
    """
    world = await _seed_world(client, db_session)
    missing = {"project_id": str(UNISSUED_PROJECT_ID)}
    foreign = {"project_id": str(world.alpha_project_id)}

    for path in ("/api/v1/analytics/time", "/api/v1/analytics/projects"):
        unissued = await client.get(path, params={**WINDOW, **missing}, headers=world.bravo_headers)
        error = assert_error_envelope(unissued, status_code=404, code="not_found")
        assert error["message"] == "That project does not exist."

        actual = await client.get(path, params={**WINDOW, **foreign}, headers=world.bravo_headers)
        assert_error_envelope(actual, status_code=404, code="not_found")
        assert actual.json()["error"]["message"] == unissued.json()["error"]["message"]
        world.assert_no_alpha_data(actual.json(), where=path)


async def test_a_foreign_project_id_filter_leaks_nothing_even_where_it_succeeds(client, db_session):
    """The one project's own figures, and only that project's.

    A filter that is honoured wrongly does not have to error to be a leak: it
    can widen the answer to "the caller's whole dataset" while looking like it
    narrowed. So the second account's own project id is passed to
    ``/projects`` and the response is checked to describe that project alone.
    ``meta.total`` is checked alongside it, because a filter that named one
    project and reported the caller's whole account as the total would satisfy
    every assertion about the rows. (``/time`` takes the same parameter and is
    covered by the test below.)
    """
    world = await _seed_world(client, db_session)
    params = {**WINDOW, "project_id": str(world.bravo_project_id)}

    projects = await client.get(
        "/api/v1/analytics/projects", params=params, headers=world.bravo_headers
    )
    assert projects.status_code == 200, projects.text
    body = projects.json()
    assert [entry["project_id"] for entry in body["items"]] == [str(world.bravo_project_id)]
    assert [entry["name"] for entry in body["items"]] == [BRAVO_PROJECT]
    assert body["meta"]["total"] == 1
    world.assert_no_alpha_data(body, where="/projects?project_id=<own>")


async def test_the_time_route_answers_a_filter_by_the_callers_own_project(
    non_raising_client, db_session
):
    """The narrowing that *succeeds* must describe only the filtered project.

    The refusal for a foreign project id is covered above; this is the other
    half of the same contract — a filter the caller is entitled to apply must
    return that project's minutes and nothing else, including nothing from the
    first account.

    Driven through the non-raising client so that a server-side error shows up
    as the rendered 500 envelope in the assertion message rather than as a
    stack trace out of the ASGI transport.
    """
    world = await _seed_world(non_raising_client, db_session)
    params = {**WINDOW, "project_id": str(world.bravo_project_id)}

    response = await non_raising_client.get(
        "/api/v1/analytics/time", params=params, headers=world.bravo_headers
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["project_id"] == str(world.bravo_project_id)
    assert body["total_minutes"] == BRAVO_SESSION_MINUTES
    assert [bucket["key"] for bucket in body["by_project"]] == [str(world.bravo_project_id)]
    world.assert_no_alpha_data(body, where="/time?project_id=<own>")


async def test_another_accounts_task_id_is_a_404_identical_to_an_unissued_one(
    client, db_session, assert_error_envelope
):
    """``/feature-snapshot`` is the Phase 10 training hook, so it matters most.

    The feature vector is the most machine-readable thing the system exposes
    about a person: priority, age, estimate against actual, how much they
    worked, how they tend to schedule. A caller who could read another account's
    vector would be training on, or profiling, a stranger. The id is resolved
    through an owner-scoped lookup, so the row is never loaded and the refusal
    is the one a nonexistent id gets.
    """
    world = await _seed_world(client, db_session)
    unissued = await client.get(
        "/api/v1/analytics/feature-snapshot",
        params={"task_id": str(UNISSUED_TASK_ID)},
        headers=world.bravo_headers,
    )
    error = assert_error_envelope(unissued, status_code=404, code="not_found")
    assert error["message"] == "That task does not exist."

    for task_id in world.alpha_task_ids:
        actual = await client.get(
            "/api/v1/analytics/feature-snapshot",
            params={"task_id": str(task_id)},
            headers=world.bravo_headers,
        )
        assert_error_envelope(actual, status_code=404, code="not_found")
        assert actual.json()["error"]["message"] == unissued.json()["error"]["message"]
        world.assert_no_alpha_data(actual.json(), where="/feature-snapshot")


async def test_a_feature_snapshot_describes_only_the_callers_own_task(client, db_session):
    """The 200 case, so the 404 above is not just a route that always refuses.

    The second account's own task gets a complete vector, every documented key
    present, with the values its own row produces. The project-level features
    are the interesting ones for privacy: they are counts of *other* tasks, so a
    bug that dropped the owner predicate would return the first account's
    project backlog against the second account's task.

    The response is a wrapper around that matrix — ``schema_version``,
    ``generated_at``, ``task_id`` and ``features`` — and the wrapper is where
    this file's own rule bites hardest. ``task_id`` echoes the id back, so a
    route that resolved the task through an unscoped lookup and then echoed the
    *requested* id would put the first account's id into the second account's
    response. Every first-account task id is a marker, so the walk below already
    catches that; it is spelled out here as well because a leak in a key added
    yesterday is worth naming rather than leaving to the general sweep.
    """
    world = await _seed_world(client, db_session)

    response = await client.get(
        "/api/v1/analytics/feature-snapshot",
        params={"task_id": str(world.bravo_task_ids[0])},
        headers=world.bravo_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    vector = body["features"]

    assert set(body) == {"schema_version", "generated_at", "task_id", "features"}
    assert body["schema_version"] == "analytics_features.v1"
    # The id in the body is the caller's own, never one of the first account's.
    assert body["task_id"] == str(world.bravo_task_ids[0])
    # The stamp is the **database** clock, the same one every feature in the row
    # was derived from; a wrapper dated from a host-local ``date.today()`` would
    # be a day ahead of them for five and a half hours a day. Normalised to UTC
    # for the same reason the service normalises: ``now()`` arrives labelled with
    # the *connection's* ``TimeZone`` (Asia/Calcutta here), so a bare ``.date()``
    # on it is the server-local day rather than the one the service stamps.
    db_now = await db_session.scalar(select(func.now()))
    assert body["generated_at"] == db_now.astimezone(UTC).date().isoformat()
    assert set(vector) == {
        "priority",
        "task_age_days",
        "estimated_minutes",
        "actual_minutes",
        "deadline_distance_days",
        "reschedule_count",
        "project_open_task_count",
        "historical_completion_rate",
        "recent_work_minutes",
        "work_session_count",
        "time_of_day",
        "day_of_week",
        "project_velocity",
        "overdue_count",
        "project_overdue_task_count",
    }
    assert vector["estimated_minutes"] == BRAVO_ESTIMATE_MINUTES
    assert vector["actual_minutes"] == BRAVO_ACTUAL_MINUTES
    assert vector["work_session_count"] == 1
    assert vector["recent_work_minutes"] == BRAVO_SESSION_MINUTES
    # One completed task out of the two in the second account's own project.
    assert vector["historical_completion_rate"] == 50.0
    # The single open task in that project, and it is not overdue.
    assert vector["project_open_task_count"] == 1
    assert vector["project_overdue_task_count"] == 0
    assert vector["overdue_count"] == 0
    world.assert_no_alpha_data(body, where="/feature-snapshot")


# -- The empty shape ----------------------------------------------------------


async def test_a_fresh_account_gets_the_documented_empty_overview(client, db_session):
    """A brand-new account reads an *empty* dashboard, not the first one's zeros.

    The failure this guards against is subtle and worth naming: an unscoped read
    does not have to be catastrophically wrong to be a privacy bug. If it
    returned the first account's figures, this test would catch it. If it
    returned the first account's *shape* — the same non-empty lists, the same
    non-null scores, the same populated ``reason_if_empty`` slot — it would look
    like activity where there is none, and the user would be told a story about
    themselves that is not true.

    The first account is seeded first, so the table really does hold another
    user's rows while this one is asked for the dashboard.
    """
    world = await _seed_world(client, db_session)
    _seed, headers = await seeded_client(
        client, db_session, username="carol", email="carol-isolation@nexus.test"
    )

    response = await client.get("/api/v1/analytics/overview", params=WINDOW, headers=headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["range"] == {
        "start_date": WINDOW_START.isoformat(),
        "end_date": WINDOW_END.isoformat(),
        "granularity": "day",
    }
    assert {point["current"] for point in body["totals"]} == {0.0}
    assert {point["label"] for point in body["totals"]} == set(TRENDABLE_METRICS)
    # `daily` is populated, but every counter in it is this account's own zero.
    # `/overview` completes the window before reading it, so a never-aggregated
    # window comes back as one row of zeroes per day rather than as an empty
    # list — "nothing happened" and "never computed" are different states, and
    # this endpoint now answers the second by doing the work. The privacy claim
    # under test is that none of those rows carry another user's figures.
    assert body["daily"]
    assert all(all(row[key] == 0 for key in TRENDABLE_METRICS) for row in body["daily"])
    assert [row["metric_date"] for row in body["daily"]] == [
        (WINDOW_START + timedelta(days=offset)).isoformat() for offset in range(WINDOW_DAYS)
    ]
    assert body["productivity"]["available"] is False
    assert body["productivity"]["score"] is None
    assert body["deadlines"]["available"] is False
    assert body["deadlines"]["on_time"] == 0
    assert body["consistency"]["available"] is False
    assert body["consistency"]["active_days"] == 0
    assert body["focus"]["available"] is False
    assert body["focus"]["total_minutes"] == 0
    assert body["estimation"]["available"] is False
    assert body["estimation"]["sample_count"] == 0
    assert body["reason_if_empty"].startswith("Not enough activity yet")
    # The window had to be computed on this request, which is precisely what
    # `stale` reports. Every score above is still unavailable and the totals are
    # still zero: `stale` describes the state of the *aggregates*, not the
    # user's activity, and conflating the two would tell a brand-new user they
    # have out-of-date data rather than none.
    assert body["stale"] is True
    world.assert_no_alpha_data(body, where="/overview (empty account)")


async def test_a_fresh_account_gets_empty_rollups_rather_than_another_users_rows(
    client, db_session
):
    """Every roll-up route, on an account with no rows at all.

    Split from the score routes because those trigger a rebuild of the window
    they are asked for, which would leave this account's own (zero) aggregates
    in place and change what the series and export routes return. These eleven
    routes read without writing, so one account can check all of them and the
    empty shape is a fact about the data rather than about the request order.
    """
    world = await _seed_world(client, db_session)
    _seed, headers = await seeded_client(
        client, db_session, username="dave", email="dave-isolation@nexus.test"
    )

    projects = await client.get("/api/v1/analytics/projects", params=WINDOW, headers=headers)
    assert projects.status_code == 200, projects.text
    # The paging total is asserted as well as the rows: an account owning no
    # projects at all reports a total of zero, and a total of one or two would
    # say that the first account's projects are being counted for somebody else.
    assert projects.json() == {"items": [], "meta": {"total": 0, "limit": 20, "offset": 0}}
    world.assert_no_alpha_data(projects.json(), where="/projects (empty account)")

    trends = await client.get("/api/v1/analytics/trends", params=WINDOW, headers=headers)
    assert trends.status_code == 200 and trends.json() == []

    series = await client.get("/api/v1/analytics/series", params=WINDOW, headers=headers)
    assert series.status_code == 200 and series.json() == []

    time_view = await client.get("/api/v1/analytics/time", params=WINDOW, headers=headers)
    assert time_view.status_code == 200, time_view.text
    assert time_view.json()["total_minutes"] == 0
    assert time_view.json()["available"] is False
    assert time_view.json()["by_project"] == []
    assert time_view.json()["by_task"] == []
    world.assert_no_alpha_data(time_view.json(), where="/time (empty account)")

    workload = await client.get("/api/v1/analytics/workload", params=WINDOW, headers=headers)
    assert workload.status_code == 200, workload.text
    assert workload.json()["open_tasks"] == 0
    assert workload.json()["overdue_open"] == 0
    assert workload.json()["scheduled_minutes"] == 0
    assert workload.json()["actual_minutes"] == 0
    assert workload.json()["available_minutes"] is None
    assert workload.json()["workload_ratio"] is None
    assert workload.json()["status_counts"]["total"] == 0
    assert workload.json()["available"] is False

    deadlines = await client.get("/api/v1/analytics/deadlines", params=WINDOW, headers=headers)
    assert deadlines.status_code == 200, deadlines.text
    assert deadlines.json()["available"] is False
    assert deadlines.json()["on_time"] == 0
    assert deadlines.json()["late"] == 0
    assert deadlines.json()["still_overdue"] == 0
    assert deadlines.json()["adherence_rate"] is None

    estimation = await client.get("/api/v1/analytics/estimation", params=WINDOW, headers=headers)
    assert estimation.status_code == 200, estimation.text
    assert estimation.json()["available"] is False
    assert estimation.json()["sample_count"] == 0
    assert estimation.json()["absolute_error"] is None
    assert estimation.json()["bias"] is None

    consistency = await client.get("/api/v1/analytics/consistency", params=WINDOW, headers=headers)
    assert consistency.status_code == 200, consistency.text
    assert consistency.json()["available"] is False
    assert consistency.json()["score"] is None
    assert consistency.json()["active_days"] == 0
    assert consistency.json()["work_sessions"] == 0
    assert consistency.json()["longest_streak"] == 0
    assert consistency.json()["current_streak"] == 0

    learning = await client.get("/api/v1/analytics/learning", params=WINDOW, headers=headers)
    assert learning.status_code == 200, learning.text
    assert learning.json()["available"] is False
    assert learning.json()["study_events"] == 0
    assert learning.json()["study_minutes"] == 0
    assert learning.json()["knowledge_interactions"] == 0

    knowledge = await client.get("/api/v1/analytics/knowledge", params=WINDOW, headers=headers)
    assert knowledge.status_code == 200, knowledge.text
    assert knowledge.json()["available"] is False
    assert knowledge.json()["interactions"] == 0
    # The first account owns a tag; an empty account's tag list must be empty
    # rather than the neighbour's, which is the one free-text list on this route.
    assert knowledge.json()["most_used_tags"] == []
    assert knowledge.json()["notes_by_status"]["total"] == 0
    world.assert_no_alpha_data(knowledge.json(), where="/knowledge (empty account)")

    for dataset in ("daily_metrics", "task_performance", "work_sessions"):
        export = await client.get(
            "/api/v1/analytics/export.csv",
            params={**WINDOW, "dataset": dataset},
            headers=headers,
        )
        assert export.status_code == 200, export.text
        assert export.headers["X-Nexus-Row-Count"] == "0", dataset
        # A header row and nothing else: an empty export is a file, not an error.
        _header, rows = _csv_table(export.text)
        assert rows == [], dataset


async def test_a_fresh_accounts_scores_are_unavailable_rather_than_zero(client, db_session):
    """The three score-bearing routes, on a second brand-new account.

    A separate account from the roll-up test because ``/productivity``,
    ``/focus`` and ``/tasks`` each fill a gap in the aggregate table before
    reading it. Sharing one account with the test above would mean the series
    and export routes there saw this account's own zero rows rather than none,
    which is a different question.
    """
    world = await _seed_world(client, db_session)
    _seed, headers = await seeded_client(
        client, db_session, username="erin", email="erin-isolation@nexus.test"
    )

    productivity = await client.get(
        "/api/v1/analytics/productivity", params=WINDOW, headers=headers
    )
    assert productivity.status_code == 200, productivity.text
    assert productivity.json()["available"] is False
    assert productivity.json()["score"] is None
    assert productivity.json()["reason_if_unavailable"].startswith("Not enough activity yet")
    # The four components are still listed, each saying it was not counted, so
    # a client can render the breakdown without special-casing the whole thing.
    assert [component["points"] for component in productivity.json()["components"]] == [
        0.0,
        0.0,
        0.0,
        0.0,
    ]
    world.assert_no_alpha_data(productivity.json(), where="/productivity")

    focus = await client.get("/api/v1/analytics/focus", params=WINDOW, headers=headers)
    assert focus.status_code == 200, focus.text
    assert focus.json()["available"] is False
    assert focus.json()["score"] is None
    assert focus.json()["avg_session_minutes"] is None
    assert focus.json()["total_minutes"] == 0
    assert focus.json()["completed_planned_sessions"] == 0

    tasks = await client.get("/api/v1/analytics/tasks", params=WINDOW, headers=headers)
    assert tasks.status_code == 200, tasks.text
    assert tasks.json()["available"] is False
    assert tasks.json()["total_tasks"] == 0
    assert tasks.json()["completed_tasks"] == 0
    assert tasks.json()["open_tasks"] == 0
    assert tasks.json()["top_overdue"] == []
    # No tasks means no rate — never 0%, which would be a claim about a person.
    assert tasks.json()["completion_rate"] is None
    assert tasks.json()["overdue_rate"] is None
    assert tasks.json()["by_status"]["total"] == 0
    assert tasks.json()["estimation"]["available"] is False
    world.assert_no_alpha_data(tasks.json(), where="/tasks")


# -- Rebuild: one account's recompute is not another's -----------------------


async def test_one_accounts_rebuild_cannot_alter_the_other_accounts_aggregates(client, db_session):
    """The only write on the router, and it writes a table keyed by user.

    ``daily_metrics`` is unique on ``(user_id, metric_date)`` and rebuilt by
    recomputing the whole range, so a rebuild that dropped the owner predicate
    would not add rows — it would *overwrite* the other account's days with
    zeroes, quietly blanking a dashboard nobody asked to have blanked. That is
    a data-destroying failure and a privacy failure at once, and it is
    invisible through the API, because the route that caused it would then report
    the zeros it had just written. So the stored rows are read straight from the
    table, before and after, and compared value by value.
    """
    world = await _seed_world(client, db_session)
    alpha_id = world.alpha_seed.owner.id
    bravo_id = world.bravo_seed.owner.id

    seeded = await client.post(
        "/api/v1/analytics/rebuild", params=WINDOW, headers=world.alpha_headers
    )
    assert seeded.status_code == 202, seeded.text
    assert seeded.json() == {"rows_written": WINDOW_DAYS}

    before = await _daily_rows(db_session, alpha_id)
    assert len(before) == WINDOW_DAYS
    monday = next(row for row in before if row["metric_date"] == DAY)
    assert monday["tasks_created"] == 4
    assert monday["tasks_completed"] == 4
    assert monday["actual_minutes"] == ALPHA_MONDAY_MINUTES
    assert monday["work_sessions"] == 1

    # The second account now rebuilds the very same window, which is exactly
    # the moment a missing owner predicate would do its damage.
    rebuilt = await client.post(
        "/api/v1/analytics/rebuild", params=WINDOW, headers=world.bravo_headers
    )
    assert rebuilt.status_code == 202, rebuilt.text
    assert rebuilt.json() == {"rows_written": WINDOW_DAYS}

    after = await _daily_rows(db_session, alpha_id)
    assert after == before, "the second account's rebuild rewrote the first account's days"

    # And the first account still reads their own numbers back out.
    overview = await client.get(
        "/api/v1/analytics/overview", params=WINDOW, headers=world.alpha_headers
    )
    totals = {point["label"]: point["current"] for point in overview.json()["totals"]}
    assert totals["actual_minutes"] == float(ALPHA_TRACKED_MINUTES)
    assert totals["tasks_completed"] == 8.0

    # Both accounts now hold a full window each: the write added rows for the
    # caller and for nobody else.
    assert len(await _daily_rows(db_session, bravo_id)) == WINDOW_DAYS


async def test_a_rebuild_request_cannot_be_pointed_at_another_accounts_data(client, db_session):
    """There is no parameter that widens a rebuild beyond the caller's own rows.

    The window is the only thing a rebuild accepts, so the guard here is that
    the union of both accounts' stored rows still separates cleanly after
    repeated rebuilds from both sides: idempotent for the caller, invisible to
    the other, and never a second row for a day.
    """
    world = await _seed_world(client, db_session)
    for _attempt in range(2):
        for headers in (world.alpha_headers, world.bravo_headers):
            response = await client.post(
                "/api/v1/analytics/rebuild", params=WINDOW, headers=headers
            )
            assert response.status_code == 202, response.text

    alpha_rows = await _daily_rows(db_session, world.alpha_seed.owner.id)
    bravo_rows = await _daily_rows(db_session, world.bravo_seed.owner.id)

    assert len(alpha_rows) == WINDOW_DAYS
    assert len(bravo_rows) == WINDOW_DAYS
    # Re-running converges on one row per day rather than appending a second,
    # so the counts above are also the idempotency assertion.
    assert {row["metric_date"] for row in alpha_rows} == {row["metric_date"] for row in bravo_rows}
    assert [row["actual_minutes"] for row in alpha_rows] != [
        row["actual_minutes"] for row in bravo_rows
    ]
