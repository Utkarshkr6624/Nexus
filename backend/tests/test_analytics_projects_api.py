"""Per-project and task-level analytics, end to end through the HTTP layer.

**Every test here requires a live PostgreSQL and has NOT been executed.** They
are marked ``integration`` and are excluded from
``pytest -m "not integration"``.

The Phase 6 brief is emphatic that analytics be *mathematically correct* and
verified against known fixed datasets ("If: 10 tasks, 8 completed / Then:
completion rate must equal 80%"), so every expectation in this file is an exact
number derived by hand from the rows
:mod:`tests.analytics_fixtures` writes, and each test's docstring carries that
arithmetic. Nothing here asserts a range, a truthiness, or "roughly".

Two projects are seeded because a single project cannot exercise the parts of
the roll-up that are per-project: :func:`_seed_atlas` has tracked work sessions,
estimates, actual durations and recorded activity, and :func:`_seed_borealis`
has a third of the work and no overdue anything, so the difference between the
two rows is visible in every field at once.

What the two endpoints actually promise, as read from the code
-------------------------------------------------------------
* ``GET /analytics/projects`` declares exactly one filter beyond the window,
  ``project_id``, plus the ``limit`` and ``offset`` every paged route takes. Its
  answer is the standard envelope — ``items`` and a ``meta`` naming ``total``,
  ``limit`` and ``offset`` — and the project tests below read through ``items``.
  It does **not** declare ``category``; the brief lists ``category`` among the
  query parameters for the analytics API, and no route on this router takes one.
  The envelope itself, the total and the paging order are covered by
  ``tests/test_analytics_pagination.py``; what is asserted here is that the
  *figures* in each row survive the move, and that neither the rows nor the total
  can carry another account's project.
* ``GET /analytics/tasks`` declares **no** filter beyond the window. A
  ``project_id`` sent to it is an unknown query parameter, which FastAPI
  discards, so the answer is the unfiltered one — asserted below, because a
  client that sends the parameter and gets a plausible-looking number back has
  no way to know the filter was ignored.
* Project totals (``total_tasks``, ``completed_tasks``, ``remaining_tasks``,
  ``overdue_tasks``) are read as of the **database clock**, not the end of the
  window, while ``completion_rate`` and every velocity figure are window-scoped.
  Both are asserted separately below rather than inferred from one another.

The velocity tests pin the *bucket*, because "velocity" is the one number on
this surface whose definition matters more than its value. The response says
"tasks completed per calendar week"; the service computes
``completions_in_window / (window_days / 7)``. Over a window that happens to be
a whole number of weeks those agree. Over the same seeded data, three windows of
7, 14 and 21 days are requested, and the three answers are what a per-calendar-
week bucketing could not produce — see
:func:`test_the_velocity_bucket_is_the_window_and_not_a_calendar_week`.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest

from app.models.enums import ActivityEvent, TaskPriority, TaskStatus
from tests.analytics_fixtures import DAY, AnalyticsSeed, at, seeded_client

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# The fixed dataset
# ---------------------------------------------------------------------------

#: The three-week window every project test asks for. ``DAY`` is a Monday, so
#: this is exactly three whole Monday-to-Sunday weeks and the window length
#: divides into a whole number of weeks without a remainder — which is what
#: makes the velocity arithmetic below readable rather than approximate.
WINDOW_START = DAY
WINDOW_END = DAY + timedelta(days=20)

#: The same window as query parameters. Sent on every project request so a test
#: never inherits the endpoint's own default window (which ends on the database's
#: current date and would silently drift away from the seeded days).
WINDOW = {
    "start_date": WINDOW_START.isoformat(),
    "end_date": WINDOW_END.isoformat(),
}

#: A due date far enough in the past that the task is overdue as of the database
#: clock whatever day the suite is run on. The engine compares a due date against
#: ``now()``, never against the window, so "overdue" here is a statement about
#: the clock and this constant is what makes it a fixed one.
ANCIENT_DUE_DATE = date(2020, 6, 1)

#: A window whose *span* — ``end_date - start_date`` — is a whole number of
#: weeks. ``project_analytics`` measures the window that way rather than by its
#: inclusive length, so these are the end dates that make the divisor exactly
#: 1.0, 2.0 and 3.0 and the velocity rates exact arithmetic instead of a ratio
#: with a repeating decimal in the denominator. The span/offset difference is
#: pinned on its own in
#: :func:`test_the_measured_week_count_excludes_the_last_day_of_the_window`.
VELOCITY_WINDOW = {
    "start_date": WINDOW_START.isoformat(),
    "end_date": (WINDOW_START + timedelta(days=21)).isoformat(),
}

#: ``(label, start, end, weeks_measured, tasks_per_week)``. Each row is one
#: request over the *same* seeded data, with the weekly rate derived by hand from
#: how many of Atlas' eight completions fall inside that window. The first row's
#: span is six days, which the service's ``max(1.0, ...)`` floor rounds up to a
#: single week; the other two are exact.
VELOCITY_CASES = (
    ("one week", "2026-01-05", "2026-01-11", 1.0, 2.0),
    ("two weeks", "2026-01-05", "2026-01-19", 2.0, 2.5),
    ("three weeks", "2026-01-05", "2026-01-26", 3.0, 2.6667),
)

#: Every estimated Atlas task: 60 minutes promised.
ATLAS_ESTIMATED = 60
#: ... and every one of them took 90. A constant, uniform over-estimate miss, so
#: the estimation figures below are arithmetic rather than an average of
#: hand-picked pairs.
ATLAS_ACTUAL = 90

#: ``(created day offset, completed day offset)`` for Atlas' eight completions,
#: in week order. Week one holds two, week two three, week three three — the
#: uneven per-week shape is deliberate: a flat 8/8/8 would hide the difference
#: between "per week" and "the average per week".
ATLAS_COMPLETIONS = ((0, 2), (0, 4), (7, 9), (7, 11), (7, 12), (14, 15), (14, 17), (14, 19))

#: Atlas' tracked work, in minutes, on the day offset given. 60 + 45 + 75 = 180,
#: which is deliberately *not* a multiple of the 90 minutes each task took: the
#: roll-up reports session minutes and task minutes side by side precisely so a
#: discrepancy between them stays visible instead of being averaged away.
ATLAS_SESSIONS = ((2, 60), (11, 45), (15, 75))

#: Atlas' open work: one task with no deadline, one long past its deadline.
ATLAS_OPEN_OFFSETS = (1, 1)

#: Borealis' three completions, all inside week one.
BOREALIS_COMPLETIONS = ((0, 1), (0, 3), (0, 5))
BOREALIS_ESTIMATED = 30
BOREALIS_ACTUAL = 45


async def _seed_atlas(seed: AnalyticsSeed):
    """Project **Atlas**: ten tasks, eight completed, tracked work in three sessions.

    The by-hand figures, all over ``WINDOW_START..WINDOW_END``:

    * ``total_tasks`` 10 — every seeded task belongs to the project.
    * ``completed_tasks`` 8 — the eight rows written with ``status='completed'``.
    * ``remaining_tasks`` 2 — anything neither completed nor cancelled.
    * ``overdue_tasks`` 1 — of the two open tasks, one has a due date before
      the database's today and one has none at all.
    * ``completion_rate`` 80.0 — 8 completions over 10 creations *inside the
      window*, which is the ratio the service builds; note it is not
      ``completed_tasks / total_tasks``, because those two counts are as-of-now
      while this rate is window-scoped. Here they happen to agree; the velocity
      section below is where they stop agreeing.
    * ``estimated_minutes`` 480 and ``actual_minutes`` 720 — 8 x 60 and 8 x 90
      summed over the project's task rows.
    * ``total_work_minutes`` 180 — the three sessions, and *not* the 720 task
      minutes: session minutes are time demonstrably spent, task minutes are
      what the task rows accumulated.
    * ``avg_task_actual_minutes`` 22.5 — 180 tracked minutes over 8 completions.
    * ``avg_task_minutes`` 72.0 — 720 task minutes over all 10 tasks.
    * ``activity_events`` 2 — the two in-window events carrying this project id.

    Returns the project. A session with no project is seeded too, and must not
    appear against any project: untracked time belongs to nobody rather than to
    the project that happened to be nearest.
    """
    project = await seed.project(name="Atlas")

    for created_offset, completed_offset in ATLAS_COMPLETIONS:
        completed_day = WINDOW_START + timedelta(days=completed_offset)
        await seed.task(
            project_id=project.id,
            status=TaskStatus.COMPLETED.value,
            created_at=at(WINDOW_START + timedelta(days=created_offset)),
            completed_at=at(completed_day),
            due_date=completed_day,
            estimated_minutes=ATLAS_ESTIMATED,
            actual_minutes=ATLAS_ACTUAL,
        )

    for index, created_offset in enumerate(ATLAS_OPEN_OFFSETS):
        await seed.task(
            project_id=project.id,
            status=TaskStatus.TODO.value,
            created_at=at(WINDOW_START + timedelta(days=created_offset)),
            # Only the second open task carries a deadline, so the overdue count
            # is 1 rather than 2 and the zero-due-date branch is covered too.
            due_date=ANCIENT_DUE_DATE if index else None,
        )

    for day_offset, minutes in ATLAS_SESSIONS:
        await seed.work_session(
            day=WINDOW_START + timedelta(days=day_offset),
            minutes=minutes,
            project_id=project.id,
        )

    # Tracked time that belongs to no project. It must not be folded into Atlas.
    await seed.work_session(day=WINDOW_START + timedelta(days=3), minutes=30)

    for day_offset, hour in ((2, 9), (2, 12)):
        await seed.activity(
            ActivityEvent.TASK_STARTED,
            day=WINDOW_START + timedelta(days=day_offset),
            hour=hour,
            project_id=project.id,
        )

    return project


async def _seed_borealis(seed: AnalyticsSeed):
    """Project **Borealis**: four tasks, three completed, one 30-minute session.

    The by-hand figures over the same window:

    * ``total_tasks`` 4, ``completed_tasks`` 3, ``remaining_tasks`` 1,
      ``overdue_tasks`` 0 — its one open task has no deadline.
    * ``completion_rate`` 75.0 — 3 completions over 4 creations in the window.
    * ``estimated_minutes`` 105 — 3 x 30 on the completions plus 15 on the open
      one, which carries an estimate even though it has no tracked time.
    * ``actual_minutes`` 135 — 3 x 45.
    * ``total_work_minutes`` 30, ``avg_task_actual_minutes`` 10.0 (30 / 3),
      ``avg_task_minutes`` 33.75 (135 / 4).
    * ``activity_events`` 0 — nothing was recorded against this project.
    """
    project = await seed.project(name="Borealis")

    for created_offset, completed_offset in BOREALIS_COMPLETIONS:
        completed_day = WINDOW_START + timedelta(days=completed_offset)
        await seed.task(
            project_id=project.id,
            status=TaskStatus.COMPLETED.value,
            created_at=at(WINDOW_START + timedelta(days=created_offset)),
            completed_at=at(completed_day),
            due_date=completed_day,
            estimated_minutes=BOREALIS_ESTIMATED,
            actual_minutes=BOREALIS_ACTUAL,
        )

    await seed.task(
        project_id=project.id,
        status=TaskStatus.TODO.value,
        created_at=at(WINDOW_START + timedelta(days=2)),
        estimated_minutes=15,
    )

    await seed.work_session(day=WINDOW_START + timedelta(days=3), minutes=30, project_id=project.id)
    return project


def _rows(response) -> list[dict]:
    """The project rows of a ``/analytics/projects`` response, which is a page.

    The route answers with the standard list envelope — ``items`` plus a ``meta``
    naming ``total``, ``limit`` and ``offset`` — rather than with a bare array.
    Every test in this file seeds fewer projects than one page holds and then
    reads them by name, which only means anything if the response really did
    carry all of them; ``len(items) == meta.total`` says so here rather than
    letting a truncated page fail later as a name that is mysteriously absent.

    ``test_an_account_with_no_projects_at_all_gets_an_empty_list`` and the
    filtering tests read ``meta`` directly, because counting the set is part of
    what they are about.
    """
    assert response.status_code == 200, response.text
    body = response.json()
    items = body["items"]
    assert len(items) == body["meta"]["total"], body["meta"]
    assert body["meta"]["offset"] == 0, body["meta"]
    return items


def _by_name(rows: list[dict], name: str) -> dict:
    """The one row for ``name``, failing loudly rather than returning ``None``."""
    matches = [row for row in rows if row["name"] == name]
    assert len(matches) == 1, [row["name"] for row in rows]
    return matches[0]


# ---------------------------------------------------------------------------
# GET /analytics/projects — the per-project roll-up
# ---------------------------------------------------------------------------


async def test_two_projects_report_their_own_hand_computed_totals(client, db_session):
    """Each project's totals are its own rows, never a blend of the two.

    The expected numbers are derived in :func:`_seed_atlas` and
    :func:`_seed_borealis`. If the roll-up ever joins or aggregates across
    projects, Atlas's 10 tasks and Borealis's 4 would stop being distinguishable
    here.
    """
    seed, auth = await seeded_client(client, db_session)
    await _seed_atlas(seed)
    await _seed_borealis(seed)

    response = await client.get("/api/v1/analytics/projects", params=WINDOW, headers=auth)

    rows = _rows(response)
    assert [row["name"] for row in rows] == ["Atlas", "Borealis"]

    atlas = _by_name(rows, "Atlas")
    assert atlas["project_id"] == str(atlas["project_id"])
    assert atlas["status"] == "active"
    assert atlas["total_tasks"] == 10
    assert atlas["completed_tasks"] == 8
    assert atlas["remaining_tasks"] == 2
    assert atlas["overdue_tasks"] == 1
    assert atlas["completion_rate"] == 80.0
    assert atlas["available"] is True
    assert atlas["reason_if_unavailable"] is None

    borealis = _by_name(rows, "Borealis")
    assert borealis["total_tasks"] == 4
    assert borealis["completed_tasks"] == 3
    assert borealis["remaining_tasks"] == 1
    assert borealis["overdue_tasks"] == 0
    assert borealis["completion_rate"] == 75.0


async def test_time_tracked_and_time_estimated_are_reported_separately(client, db_session):
    """Session minutes and task minutes are two figures, not one.

    Atlas tracked **180** minutes in work sessions while its eight tasks
    accumulated **720** minutes of actual duration and were estimated at **480**.
    The brief asks for "estimated vs actual" and "total work time" as distinct
    rows, and :func:`_seed_atlas` seeds the two sources far enough apart
    (180 against 720) that collapsing them into one number fails here.
    """
    seed, auth = await seeded_client(client, db_session)
    await _seed_atlas(seed)
    await _seed_borealis(seed)

    response = await client.get("/api/v1/analytics/projects", params=WINDOW, headers=auth)

    atlas = _by_name(_rows(response), "Atlas")

    # 60 + 45 + 75 minutes of work sessions. The 30-minute untracked session
    # seeded alongside them belongs to no project and is in none of these.
    assert atlas["total_work_minutes"] == 180
    assert atlas["work_minutes"] == 180
    # 8 tasks x 60 estimated, and 8 x 90 actually taken.
    assert atlas["estimated_minutes"] == 480
    assert atlas["actual_minutes"] == 720
    # 180 tracked minutes spread over the 8 tasks that were finished ...
    assert atlas["avg_task_actual_minutes"] == 22.5
    # ... and 720 accumulated minutes spread over all 10 tasks, finished or not.
    assert atlas["avg_task_minutes"] == 72.0

    borealis = _by_name(_rows(response), "Borealis")
    assert borealis["total_work_minutes"] == 30
    assert borealis["estimated_minutes"] == 105
    assert borealis["actual_minutes"] == 135
    assert borealis["avg_task_actual_minutes"] == 10.0
    assert borealis["avg_task_minutes"] == 33.75


async def test_velocity_is_eight_completions_over_three_measured_weeks(client, db_session):
    """NEXUS velocity over a window the service measures as exactly 3 weeks.

    The window is 2026-01-05 to **2026-01-26**: a *span* of 21 days, which is
    the arithmetic the service performs — see
    :func:`test_the_measured_week_count_excludes_the_last_day_of_the_window` for
    why the span and not the inclusive length is what counts. Eight completions
    over 3.0 weeks is 2.6667, and 480 estimated minutes over the same three
    weeks is 160.0.

    The window is chosen so the divisor is a whole number of weeks. Any other
    end date makes the rate a ratio of eight over a fraction, which is still
    exact arithmetic but no longer readable as a checked-by-hand figure.
    """
    seed, auth = await seeded_client(client, db_session)
    await _seed_atlas(seed)

    response = await client.get("/api/v1/analytics/projects", params=VELOCITY_WINDOW, headers=auth)

    atlas = _by_name(_rows(response), "Atlas")

    assert atlas["velocity"] is not None
    assert atlas["velocity"]["weeks_measured"] == 3.0
    assert atlas["velocity"]["tasks_per_week"] == 2.6667
    assert atlas["velocity_tasks_per_week"] == 2.6667
    # 8 x 60 estimated minutes over the same three weeks.
    assert atlas["velocity"]["estimated_minutes_per_week"] == 160.0
    assert "calendar week" in atlas["velocity"]["definition"]
    assert "not an Agile story-point velocity" in atlas["velocity"]["definition"]


async def test_the_per_week_completion_counts_are_not_returned(client, db_session):
    """The spec's ``Week 1 — 12, Week 2 — 18, Week 3 — 21`` timeline is absent.

    Atlas completes 2, 3 and 3 tasks in the three weeks of the window. The
    response schema declares a ``weekly_completed`` list that would carry exactly
    that series, and the repository already has
    ``AnalyticsRepository.project_velocity_by_week`` to compute it — but nothing
    calls either, so the field is always empty and the per-week shape is lost.

    This test pins the current behaviour rather than the desired one, because a
    test that asserted ``[2, 3, 3]`` would be asserting a feature that does not
    exist. The hand-derived counts are named here so the intent survives until
    the surface is built.
    """
    seed, auth = await seeded_client(client, db_session)
    await _seed_atlas(seed)

    response = await client.get("/api/v1/analytics/projects", params=VELOCITY_WINDOW, headers=auth)

    atlas = _by_name(_rows(response), "Atlas")

    # 2 completions in week one (Jan 5-11), 3 in week two (Jan 12-18), 3 in
    # week three (Jan 19-25). Reported as an average, never as a series.
    assert atlas["weekly_completed"] == []
    assert atlas["velocity"]["tasks_per_week"] == round(8 / 3, 4)


async def test_the_velocity_bucket_is_the_window_and_not_a_calendar_week(client, db_session):
    """Three windows over one dataset give three different weekly rates.

    The same eight Atlas completions, spread 2 / 3 / 3 across the three weeks of
    the full window:

    * 2026-01-05..2026-01-11 (span 6 days, measured as 1.0 weeks after the
      floor) contains the two week-one completions, so the rate is **2.0**.
    * 2026-01-05..2026-01-19 (span 14 days = 2.0 weeks) contains those two plus
      the three week-two ones, so the rate is 5 / 2 = **2.5**.
    * 2026-01-05..2026-01-26 (span 21 days = 3.0 weeks) contains all eight, so
      the rate is 8 / 3 = **2.6667**.

    A per-calendar-week bucketing could not produce that middle number: week one
    of the 14-day span really holds 2 and week two really holds 3, and neither
    is 2.5. What the service reports is one average over the requested window,
    which is a different claim from the one its ``definition`` string makes.
    """
    seed, auth = await seeded_client(client, db_session)
    await _seed_atlas(seed)

    for label, start, end, weeks_measured, expected in VELOCITY_CASES:
        response = await client.get(
            "/api/v1/analytics/projects",
            params={"start_date": start, "end_date": end},
            headers=auth,
        )

        assert response.status_code == 200, f"{label}: {response.text}"
        velocity = _by_name(_rows(response), "Atlas")["velocity"]
        assert velocity["weeks_measured"] == weeks_measured, label
        assert velocity["tasks_per_week"] == expected, label


async def test_the_measured_week_count_excludes_the_last_day_of_the_window(client, db_session):
    """``weeks_measured`` counts ``end - start``, not the days the window covers.

    2026-01-05..2026-01-25 is 21 days *inclusive*, and every other window
    measurement in the engine — ``MetricRange.days``, ``_check_range``'s ceiling,
    ``_totals``' ``window_days`` — counts it as 21. The velocity divisor is the
    one place that uses ``(end - start).days``, so it measures the same window
    as 20 days: 20 / 7 = **2.8571** weeks rather than 3.

    The effect is not a rounding curiosity. Eight completions over 2.8571 weeks
    is 2.8, so a window the user asked for as three weeks reports a rate 5%
    *above* the 2.6667 the same eight completions give when the window really is
    measured as three weeks. The one-week window in :data:`VELOCITY_CASES` hides
    the same thing behind the ``max(1.0, ...)`` floor, which is why the seven-day
    case reads 1.0 and this one does not.
    """
    seed, auth = await seeded_client(client, db_session)
    await _seed_atlas(seed)

    response = await client.get("/api/v1/analytics/projects", params=WINDOW, headers=auth)

    atlas = _by_name(_rows(response), "Atlas")

    # The window itself reports 21 inclusive days ...
    assert atlas["range"]["start_date"] == "2026-01-05"
    assert atlas["range"]["end_date"] == "2026-01-25"
    # ... while the velocity divisor sees a 20-day span.
    assert atlas["velocity"]["weeks_measured"] == round(20 / 7, 4)
    assert atlas["velocity"]["weeks_measured"] == 2.8571
    assert atlas["velocity"]["tasks_per_week"] == round(8 / round(20 / 7, 4), 4)
    assert atlas["velocity"]["tasks_per_week"] == 2.8
    # Measured as three weeks, the same eight completions are 2.6667.
    assert atlas["velocity"]["tasks_per_week"] - round(8 / 3, 4) == pytest.approx(0.1333)


async def test_estimated_minutes_per_week_does_not_move_with_the_window(client, db_session):
    """The estimated-minutes rate divides an all-time sum by the window's weeks.

    ``estimated_minutes`` is a sum over the project's task rows with no date
    filter, so it is 480 for Atlas whether the window is a week or three. The
    divisor is window-dependent, so the "per week" figure that results falls as
    the window lengthens: 480.0 over the one-week window and 160.0 over the
    three-week one, for the same 480 minutes.

    That is the opposite of what a throughput rate should do — a longer window
    covering more weeks should divide a longer window's worth of estimates — and
    it is asserted here because a client rendering the number has no way to see
    that it is not per-week at all.
    """
    seed, auth = await seeded_client(client, db_session)
    await _seed_atlas(seed)

    _short_label, short_start, short_end, _short_weeks, _short_rate = VELOCITY_CASES[0]
    short_response = await client.get(
        "/api/v1/analytics/projects",
        params={"start_date": short_start, "end_date": short_end},
        headers=auth,
    )
    long_response = await client.get(
        "/api/v1/analytics/projects", params=VELOCITY_WINDOW, headers=auth
    )

    assert short_response.status_code == 200, short_response.text
    assert long_response.status_code == 200, long_response.text
    short_atlas = _by_name(_rows(short_response), "Atlas")
    long_atlas = _by_name(_rows(long_response), "Atlas")

    # Identical all-time estimate sum over both windows ...
    assert short_atlas["estimated_minutes"] == 480
    assert long_atlas["estimated_minutes"] == 480
    # ... and therefore the "per week" figure only moved because the divisor did.
    assert short_atlas["velocity"]["estimated_minutes_per_week"] == 480.0
    assert long_atlas["velocity"]["estimated_minutes_per_week"] == 160.0


async def test_a_project_with_no_tasks_appears_with_zeros(client, db_session):
    """An empty project is a real answer, not a missing row and not a crash.

    ``project_task_counts`` joins from the project side with a ``LEFT JOIN``, so
    a project created seconds ago still has a row: zeros for the counts, ``None``
    for every rate that would otherwise divide by zero, and an explicit
    "Not enough activity yet" instead of a fabricated 0%.
    """
    seed, auth = await seeded_client(client, db_session)
    await _seed_atlas(seed)
    empty = await seed.project(name="Vacant")

    response = await client.get("/api/v1/analytics/projects", params=WINDOW, headers=auth)

    vacant = _by_name(_rows(response), "Vacant")

    assert vacant["project_id"] == str(empty.id)
    assert vacant["total_tasks"] == 0
    assert vacant["completed_tasks"] == 0
    assert vacant["remaining_tasks"] == 0
    assert vacant["overdue_tasks"] == 0
    assert vacant["total_work_minutes"] == 0
    assert vacant["estimated_minutes"] == 0
    assert vacant["actual_minutes"] == 0
    assert vacant["activity_events"] == 0

    # The three rates that need a denominator are absent rather than zero.
    assert vacant["completion_rate"] is None
    assert vacant["avg_task_actual_minutes"] is None
    assert vacant["avg_task_minutes"] is None
    assert vacant["velocity"]["tasks_per_week"] is None
    assert vacant["velocity"]["estimated_minutes_per_week"] is None

    assert vacant["available"] is False
    assert vacant["reason_if_unavailable"].startswith("Not enough activity yet")
    assert vacant["range"] == {
        "start_date": WINDOW_START.isoformat(),
        "end_date": WINDOW_END.isoformat(),
        "granularity": "day",
    }


async def test_an_account_with_no_projects_at_all_gets_an_empty_page(client, db_session):
    """No projects is an empty page and a 200, never a 404 or a 500.

    The "never crash because there is no activity" rule from the brief, at its
    simplest: the route is a list, and a list with nothing in it is the answer.
    The envelope carries the same news as the array it replaced — ``items`` empty
    and a ``total`` of **zero**, which is a real count of nothing rather than a
    count that could not be taken. The page size and offset ride along because a
    client paginating needs them echoed even on the first, empty request.
    """
    _seed, auth = await seeded_client(client, db_session)

    response = await client.get("/api/v1/analytics/projects", params=WINDOW, headers=auth)

    assert response.status_code == 200, response.text
    assert response.json() == {"items": [], "meta": {"total": 0, "limit": 20, "offset": 0}}


async def test_project_rows_carry_the_window_they_were_computed_over(client, db_session):
    """Every row states the window, so a client never re-derives it.

    The envelope's ``meta`` names the page, not the period — it says which slice
    of which set came back, and nothing about which days the figures describe.
    Each row still carries its own ``range`` for that reason, and a client that
    mixed rows from two requests could otherwise render them as one series.
    """
    seed, auth = await seeded_client(client, db_session)
    await _seed_atlas(seed)
    await _seed_borealis(seed)

    response = await client.get("/api/v1/analytics/projects", params=WINDOW, headers=auth)

    for row in _rows(response):
        assert row["range"] == {
            "start_date": WINDOW_START.isoformat(),
            "end_date": WINDOW_END.isoformat(),
            "granularity": "day",
        }, row["name"]


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


async def test_the_project_id_filter_returns_only_that_project(client, db_session):
    """``?project_id=`` narrows to one project, with its own unchanged numbers.

    The filter is applied after the grouped query rather than pushed into it, so
    a filtered row must still carry Atlas' full figures — a filter that
    recomputed anything would show a different number here than in the unfiltered
    response.
    """
    seed, auth = await seeded_client(client, db_session)
    atlas = await _seed_atlas(seed)
    await _seed_borealis(seed)

    unfiltered = await client.get("/api/v1/analytics/projects", params=WINDOW, headers=auth)
    assert unfiltered.status_code == 200, unfiltered.text

    response = await client.get(
        "/api/v1/analytics/projects",
        params={**WINDOW, "project_id": str(atlas.id)},
        headers=auth,
    )

    assert response.status_code == 200, response.text
    rows = response.json()["items"]
    assert len(rows) == 1
    assert rows[0] == _by_name(_rows(unfiltered), "Atlas")
    # The total describes the filtered set, so narrowing to one project of two
    # reports one — the envelope cannot claim the caller's whole account here.
    assert response.json()["meta"]["total"] == 1


async def test_an_unknown_project_id_filter_answers_404(client, db_session):
    """A filter for a project that does not exist is a 404, not an empty list.

    The project id is resolved through an owner-scoped lookup *before* the
    aggregation runs, so a caller cannot use this route to learn which project
    ids exist: an id nobody issued and an id belonging to somebody else produce
    the same answer, as :func:`test_another_users_project_is_404_not_403`
    pins for the ownership case.
    """
    seed, auth = await seeded_client(client, db_session)
    await _seed_atlas(seed)

    response = await client.get(
        "/api/v1/analytics/projects",
        params={**WINDOW, "project_id": str(uuid.uuid4())},
        headers=auth,
    )

    assert response.status_code == 404, response.text
    assert response.json() == {
        "error": {
            "code": "not_found",
            "message": "That project does not exist.",
            "details": None,
            "request_id": response.headers["X-Request-ID"],
        }
    }


# ---------------------------------------------------------------------------
# Ownership — the 404-not-403 rule
# ---------------------------------------------------------------------------


async def test_another_users_project_is_404_not_403(client, db_session, assert_error_envelope):
    """Asking about Grace's project answers 404 and returns none of her data.

    **The rule under test is 404, not 403.** A 403 would confirm the id is real
    and turn this route into a project-existence oracle, which is why
    ``AnalyticsService._owned_project`` resolves the id through
    ``ProjectRepository.get_by_id_for_user`` — ownership is part of the lookup
    rather than a check afterwards. The response body is asserted byte for byte
    against the one an id nobody has ever issued produces, so the two cases
    cannot drift apart into "403 for someone else's, 404 for a typo".
    """
    seed, auth = await seeded_client(client, db_session)
    await _seed_atlas(seed)
    await _seed_borealis(seed)

    grace_seed, _grace_auth = await seeded_client(
        client, db_session, username="grace", email="grace@nexus.test"
    )
    grace_project = await grace_seed.project(name="Grace-only")
    await grace_seed.task(
        project_id=grace_project.id,
        status=TaskStatus.COMPLETED.value,
        created_at=at(DAY),
        completed_at=at(DAY),
        due_date=DAY,
        estimated_minutes=10,
        actual_minutes=20,
    )
    await grace_seed.work_session(day=DAY, minutes=999, project_id=grace_project.id)

    response = await client.get(
        "/api/v1/analytics/projects",
        params={**WINDOW, "project_id": str(grace_project.id)},
        headers=auth,
    )

    assert response.status_code == 404, response.text
    error = assert_error_envelope(response, status_code=404, code="not_found")
    assert error["message"] == "That project does not exist."

    # Not 403, and not 500: the two failure modes this rule exists to avoid.
    assert response.status_code != 403
    assert response.status_code != 500
    # And nothing of Grace's leaked through the error path.
    assert "Grace-only" not in response.text
    assert "999" not in response.text


async def test_a_users_own_list_never_contains_another_accounts_projects(client, db_session):
    """The unfiltered list is owner-scoped, not merely the filtered one.

    Ownership being enforced only on the ``project_id`` path would be a real
    hole: a client that never filters would still see another account's project
    name, task counts and tracked minutes. Grace's project is given a distinctive
    999 tracked minutes so her row is recognisable if it ever appears.

    The paging envelope is asserted here too, because ``meta.total`` is a second
    place a cross-tenant row could surface: it counts the caller's projects, and
    a total of two on an account that owns one would disclose that Grace's
    project exists even with her name nowhere in the body. It reads **1**, and
    the count is not reachable from the items alone.
    """
    seed, auth = await seeded_client(client, db_session)
    await _seed_atlas(seed)

    grace_seed, _grace_auth = await seeded_client(
        client, db_session, username="grace", email="grace@nexus.test"
    )
    grace_project = await grace_seed.project(name="Grace-only")
    await grace_seed.work_session(day=DAY, minutes=999, project_id=grace_project.id)

    response = await client.get("/api/v1/analytics/projects", params=WINDOW, headers=auth)

    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["name"] for row in body["items"]] == ["Atlas"]
    assert body["meta"]["total"] == 1
    assert str(grace_project.id) not in response.text


# ---------------------------------------------------------------------------
# GET /analytics/tasks
# ---------------------------------------------------------------------------

#: The task-analytics window: one week, the smallest span in which a completion
#: rate is not decided by a single task.
TASK_WINDOW = {
    "start_date": DAY.isoformat(),
    "end_date": (DAY + timedelta(days=6)).isoformat(),
}

#: A due date inside the task window, so the task is both counted by the daily
#: ``tasks_overdue`` series and overdue as of the database clock. The window's
#: end is two weeks after the fixture anchor, so this is comfortably in the past
#: whenever the suite runs.
TASK_WINDOW_DUE_DATE = DAY + timedelta(days=2)

#: The task window as dates, for the callers that hand it to a route other than
#: a ``GET``.
TASK_WINDOW_DAYS = (DAY, DAY + timedelta(days=6))


async def _rebuild(client, headers: dict[str, str]) -> None:
    """Aggregate the task window, which is what ``GET /analytics/tasks`` reads.

    Several of that route's figures — ``tasks_created``, ``tasks_completed``,
    ``tasks_rescheduled``, ``tasks_overdue`` — are sums of the rows in
    ``daily_metrics``, and no read route writes there any more: a ``GET`` that
    filled the window it was asked about made every dashboard request a writer,
    and let an account that had never been rebuilt claim it was measured through
    a window it never had. A test that wants those counters therefore has to ask
    for the aggregation, exactly as a client does.
    """
    start, end = TASK_WINDOW_DAYS
    response = await client.post(
        "/api/v1/analytics/rebuild",
        params={"start_date": start.isoformat(), "end_date": end.isoformat()},
        headers=headers,
    )
    assert response.status_code == 202, response.text


async def _seed_task_board(seed: AnalyticsSeed):
    """Six tasks covering every status, with known events and tracked minutes.

    All six are created inside the one-week window:

    ==========================  =========  ===========  =========  =====  =====
    task                        status     created      completed  est.  actual
    ==========================  =========  ===========  =========  =====  =====
    ``slow``                    completed  Mon 09:00    Wed 12:00    60      90
    ``quick``                   completed  Mon 09:00    Mon 12:00    30      30
    ``late``                    todo       Tue 09:00    —            —       0
    ``running``                 in_prog.   Tue 09:00    —            —       0
    ``stuck``                   blocked    Wed 09:00    —            —       0
    ``dropped``                 cancelled  Wed 09:00    — (Jan 8)   —       0
    ==========================  =========  ===========  =========  =====  =====

    ``slow`` also carries the three ``TASK_RESCHEDULED`` events and the two
    work sessions (40 + 50 = 90 minutes) the assertions below count.

    Returns the mapping of role to task, so a test can name the ids it expects
    in a response.
    """
    project = await seed.project(name="Task board")
    tasks: dict[str, object] = {}

    tasks["slow"] = await seed.task(
        project_id=project.id,
        status=TaskStatus.COMPLETED.value,
        priority=TaskPriority.LOW.value,
        created_at=at(DAY, 9),
        completed_at=at(DAY + timedelta(days=2), 12),
        due_date=DAY + timedelta(days=2),
        estimated_minutes=60,
        actual_minutes=90,
    )
    tasks["quick"] = await seed.task(
        project_id=project.id,
        status=TaskStatus.COMPLETED.value,
        created_at=at(DAY, 9),
        completed_at=at(DAY, 12),
        due_date=DAY,
        estimated_minutes=30,
        actual_minutes=30,
    )
    tasks["late"] = await seed.task(
        project_id=project.id,
        status=TaskStatus.TODO.value,
        created_at=at(DAY + timedelta(days=1), 9),
        due_date=TASK_WINDOW_DUE_DATE,
        priority=TaskPriority.HIGH.value,
    )
    tasks["running"] = await seed.task(
        project_id=project.id,
        status=TaskStatus.IN_PROGRESS.value,
        created_at=at(DAY + timedelta(days=1), 9),
    )
    tasks["stuck"] = await seed.task(
        project_id=project.id,
        status=TaskStatus.BLOCKED.value,
        created_at=at(DAY + timedelta(days=2), 9),
    )
    tasks["dropped"] = await seed.task(
        project_id=project.id,
        status=TaskStatus.CANCELLED.value,
        created_at=at(DAY + timedelta(days=2), 9),
        # There is no cancelled_at column, so the daily aggregate dates a
        # cancellation by updated_at. The fixture sets it explicitly.
        updated_at=at(DAY + timedelta(days=3), 9),
        priority=TaskPriority.CRITICAL.value,
    )

    for day_offset, hour in ((1, 11), (1, 14), (2, 10)):
        await seed.activity(
            ActivityEvent.TASK_RESCHEDULED,
            day=DAY + timedelta(days=day_offset),
            hour=hour,
            task_id=tasks["slow"].id,
            project_id=project.id,
        )
    await seed.activity(
        ActivityEvent.TASK_BLOCKED,
        day=DAY + timedelta(days=2),
        hour=15,
        task_id=tasks["stuck"].id,
        project_id=project.id,
    )

    # 40 + 50 = 90 tracked minutes against ``slow`` alone.
    for day_offset, minutes in ((0, 40), (1, 50)):
        await seed.work_session(
            day=DAY + timedelta(days=day_offset),
            minutes=minutes,
            task_id=tasks["slow"].id,
            project_id=project.id,
        )

    return tasks


async def test_task_analytics_counts_creation_completion_and_the_backlog(client, db_session):
    """Six seeded tasks produce the counts in :func:`_seed_task_board`.

    The by-hand figures over ``2026-01-05..2026-01-11``:

    * ``total_tasks`` 6 and ``completed_tasks`` 2 — every status bucket present.
    * ``open_tasks`` 3 — todo, in_progress and blocked; completed and cancelled
      are the two states a task is *not* open in.
    * ``overdue_tasks`` 1 — only ``late`` has a due date, and it is before the
      database's today.
    * ``cancelled_tasks`` 1, ``blocked_tasks`` 1.
    * ``tasks_created`` 6, ``tasks_completed`` 2, ``tasks_cancelled`` 1 (dated
      by ``updated_at``, the only cancellation timestamp the schema has),
      ``tasks_blocked`` 1, ``tasks_overdue`` 1 (bucketed on the due date, so it
      is counted once rather than on every day it stayed late).
    * ``completion_rate`` 33.3333 — 2 completions over the 6 tasks created in
      the window.
    * ``overdue_rate`` 16.6667 — 1 over those same 6.

    Every "in the window" figure above is a sum of the stored daily aggregates,
    so the window is aggregated first: reading it without a rebuild would report
    zeroes from an empty table rather than the seeded board.
    """
    seed, auth = await seeded_client(client, db_session)
    await _seed_task_board(seed)
    await _rebuild(client, auth)

    response = await client.get("/api/v1/analytics/tasks", params=TASK_WINDOW, headers=auth)

    assert response.status_code == 200, response.text
    body = response.json()

    assert body["total_tasks"] == 6
    assert body["completed_tasks"] == 2
    assert body["open_tasks"] == 3
    assert body["overdue_tasks"] == 1
    assert body["cancelled_tasks"] == 1
    assert body["blocked_tasks"] == 1

    assert body["tasks_created"] == 6
    assert body["tasks_completed"] == 2
    assert body["tasks_cancelled"] == 1
    assert body["tasks_blocked"] == 1
    assert body["tasks_overdue"] == 1

    assert body["completion_rate"] == round(2 / 6 * 100, 4)
    assert body["completion_rate"] == 33.3333
    assert body["overdue_rate"] == round(1 / 6 * 100, 4)
    assert body["overdue_rate"] == 16.6667

    assert body["available"] is True
    assert body["reason_if_unavailable"] is None
    assert body["range"] == {
        "start_date": TASK_WINDOW["start_date"],
        "end_date": TASK_WINDOW["end_date"],
        "granularity": "day",
    }


async def test_task_analytics_reports_every_status_and_priority_bucket(client, db_session):
    """Both breakdowns carry every enum member, zeroed rather than absent.

    The seeded board holds one low (``slow``), three medium (``quick``,
    ``running``, ``stuck``), one high (``late``) and one critical (``dropped``)
    task, and exactly one task in each status except medium-by-status. Reading
    ``by_status["completed"]`` without a ``.get()`` default is the contract: a
    client must not need one that silently returns 0 for a status the user has
    never used.
    """
    seed, auth = await seeded_client(client, db_session)
    await _seed_task_board(seed)

    response = await client.get("/api/v1/analytics/tasks", params=TASK_WINDOW, headers=auth)

    assert response.status_code == 200, response.text
    body = response.json()

    assert body["by_status"] == {
        "todo": 1,
        "in_progress": 1,
        "blocked": 1,
        "completed": 2,
        "cancelled": 1,
        "total": 6,
    }
    assert body["by_priority"] == {
        "low": 1,
        "medium": 3,
        "high": 1,
        "critical": 1,
        "total": 6,
    }


async def test_one_tasks_recorded_events_are_counted_in_the_window(client, db_session):
    """A task's three reschedules and one block are read from the activity feed.

    Neither fact has a column on ``tasks`` — a reschedule is a due-date edit
    indistinguishable from any other, and a block later lifted leaves nothing
    behind — so the feed is the only honest source and the window total is the
    sum of the events that fall inside it. ``slow`` carries the three
    reschedules; ``stuck`` carries the block.

    The events are folded into ``daily_metrics`` by the rebuild, so a window that
    was never aggregated reports none of them — which is why the aggregation is
    explicit here rather than left to whichever route happened to be called.
    """
    seed, auth = await seeded_client(client, db_session)
    await _seed_task_board(seed)
    await _rebuild(client, auth)

    response = await client.get("/api/v1/analytics/tasks", params=TASK_WINDOW, headers=auth)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tasks_rescheduled"] == 3
    assert body["tasks_blocked"] == 1


async def test_task_analytics_reports_cycle_time_and_estimation_from_the_same_rows(
    client, db_session
):
    """Cycle time and estimation accuracy over the two completed tasks.

    By hand, from ``slow`` (created Mon 09:00, completed Wed 12:00 — 2 days and
    3 hours, or 3060 minutes) and ``quick`` (Mon 09:00 to Mon 12:00, or 180
    minutes):

    * ``avg_cycle_minutes`` (3060 + 180) / 2 = **1620.0**, and
      ``avg_completion_days`` is the same number over 1440 = **1.125**.
    * Estimation over the two pairs ``(60, 90)`` and ``(30, 30)``: signed errors
      ``-30`` and ``0``, so the bias is **-15.0** (the estimates ran *below* the
      time taken), the mean absolute error is **15.0**, the mean percentage
      error is (50 + 0) / 2 = **25.0**, the median absolute error is **15.0**,
      the under-estimation rate is **50.0** and the over-estimation rate
      **0.0**.

    The two figures come from one read of the same rows, which is why they can
    never disagree: a cycle time that implied 30 minutes of work and an
    estimation error computed from 90 would be a bug worth catching.
    """
    seed, auth = await seeded_client(client, db_session)
    await _seed_task_board(seed)

    response = await client.get("/api/v1/analytics/tasks", params=TASK_WINDOW, headers=auth)

    assert response.status_code == 200, response.text
    body = response.json()

    assert body["avg_cycle_minutes"] == 1620.0
    assert body["avg_completion_days"] == 1.125
    assert body["avg_estimate_error_minutes"] == 15.0

    estimation = body["estimation"]
    assert estimation["available"] is True
    assert estimation["sample_count"] == 2
    assert estimation["pairs_compared"] == 2
    assert estimation["absolute_error"] == 15.0
    assert estimation["percentage_error"] == 25.0
    assert estimation["bias"] == -15.0
    assert estimation["median_error"] == 15.0
    assert estimation["under_estimation_rate"] == 50.0
    assert estimation["over_estimation_rate"] == 0.0


async def test_the_overdue_drill_down_names_the_task_that_is_late(client, db_session):
    """``top_overdue`` lists the unfinished task past its due date, and only it.

    ``late`` is the one seeded task that is both open and past its due date.
    ``days_overdue`` is measured against the database clock rather than the
    window, so its value moves with the day the suite runs; the identity of the
    task, its deadline and its priority are the fixed parts.
    """
    seed, auth = await seeded_client(client, db_session)
    tasks = await _seed_task_board(seed)

    response = await client.get("/api/v1/analytics/tasks", params=TASK_WINDOW, headers=auth)

    assert response.status_code == 200, response.text
    overdue = response.json()["top_overdue"]

    assert len(overdue) == 1
    assert overdue[0]["task_id"] == str(tasks["late"].id)
    assert overdue[0]["due_date"] == TASK_WINDOW_DUE_DATE.isoformat()
    assert overdue[0]["priority"] == TaskPriority.HIGH.value
    # A positive integer, and never ``None``: the task *is* late, so "how many
    # days" is a question that has an answer.
    assert isinstance(overdue[0]["days_overdue"], int)
    assert overdue[0]["days_overdue"] > 0


async def test_task_analytics_ignores_a_project_id_it_does_not_declare(client, db_session):
    """``?project_id=`` on ``/analytics/tasks`` is discarded, not applied.

    The route declares no ``project_id``, so FastAPI drops the unknown query
    parameter and the response is the unfiltered account-wide total. The brief
    lists ``project_id`` among the analytics query parameters, and a client that
    sends it receives a plausible-looking number with no indication that the
    filter was ignored — which is exactly why this is pinned as a test rather
    than left to chance. Two projects' worth of tasks are seeded so that a filter
    that *was* applied could not accidentally produce the same answer.
    """
    seed, auth = await seeded_client(client, db_session)
    tasks = await _seed_task_board(seed)
    other = await seed.project(name="Other board")
    await seed.task(project_id=other.id, status=TaskStatus.TODO.value, created_at=at(DAY))

    unfiltered = await client.get("/api/v1/analytics/tasks", params=TASK_WINDOW, headers=auth)
    filtered = await client.get(
        "/api/v1/analytics/tasks",
        params={**TASK_WINDOW, "project_id": str(tasks["slow"].project_id)},
        headers=auth,
    )

    assert unfiltered.status_code == 200, unfiltered.text
    assert filtered.status_code == 200, filtered.text
    # Seven tasks exist; the parameter changes nothing.
    assert unfiltered.json()["total_tasks"] == 7
    assert filtered.json() == unfiltered.json()


async def test_an_unknown_category_is_ignored_by_both_analytics_routes(client, db_session):
    """No route on this router declares ``category``, so neither filters on it.

    The brief asks for ``category`` among the analytics query parameters. Neither
    ``/analytics/projects`` nor ``/analytics/tasks`` takes one, so a client
    sending it gets the unfiltered answer rather than a 422 that would tell it
    the parameter does not exist. Asserted for both routes so the behaviour is
    a documented contract rather than an accident of FastAPI's default.
    """
    seed, auth = await seeded_client(client, db_session)
    atlas = await _seed_atlas(seed)
    await _seed_task_board(seed)

    projects_plain = await client.get("/api/v1/analytics/projects", params=WINDOW, headers=auth)
    projects_categorised = await client.get(
        "/api/v1/analytics/projects", params={**WINDOW, "category": "writing"}, headers=auth
    )
    tasks_plain = await client.get("/api/v1/analytics/tasks", params=TASK_WINDOW, headers=auth)
    tasks_categorised = await client.get(
        "/api/v1/analytics/tasks", params={**TASK_WINDOW, "category": "writing"}, headers=auth
    )

    assert projects_plain.status_code == 200, projects_plain.text
    assert projects_categorised.status_code == 200, projects_categorised.text
    assert projects_categorised.json() == projects_plain.json()
    # Read through ``items``: the envelope dict itself has two keys, so counting
    # the response body would pass whatever the page held.
    assert [row["name"] for row in projects_categorised.json()["items"]] == [
        "Atlas",
        "Task board",
    ]
    assert str(atlas.id) in projects_categorised.text

    assert tasks_plain.status_code == 200, tasks_plain.text
    assert tasks_categorised.status_code == 200, tasks_categorised.text
    assert tasks_categorised.json() == tasks_plain.json()
    # Atlas' ten tasks plus the task board's six; the parameter changes nothing.
    assert tasks_categorised.json()["total_tasks"] == 16


async def test_task_analytics_for_an_account_with_no_tasks(client, db_session):
    """No tasks is 200 with zeroes and ``None`` rates, never a crash.

    Every rate here divides by something the account does not have, and the
    brief's rule is explicit: "Not enough activity yet", not "0%". The counts
    are genuine zeroes — an account with no tasks has none — while the two rates
    are ``None`` because a ratio over nothing has no value.
    """
    _seed, auth = await seeded_client(client, db_session)

    response = await client.get("/api/v1/analytics/tasks", params=TASK_WINDOW, headers=auth)

    assert response.status_code == 200, response.text
    body = response.json()

    assert body["total_tasks"] == 0
    assert body["completed_tasks"] == 0
    assert body["open_tasks"] == 0
    assert body["overdue_tasks"] == 0
    assert body["completion_rate"] is None
    assert body["overdue_rate"] is None
    assert body["avg_completion_days"] is None
    assert body["avg_cycle_minutes"] is None
    assert body["avg_estimate_error_minutes"] is None
    assert body["top_overdue"] == []
    assert body["by_status"] == {
        "todo": 0,
        "in_progress": 0,
        "blocked": 0,
        "completed": 0,
        "cancelled": 0,
        "total": 0,
    }
    assert body["estimation"]["available"] is False
    assert body["estimation"]["sample_count"] == 0
    assert body["estimation"]["bias"] is None

    assert body["available"] is False
    assert body["reason_if_unavailable"].startswith("Not enough activity yet")


async def test_task_analytics_is_owner_scoped(client, db_session):
    """One account's tasks never appear in another's task analytics.

    Grace's board is seeded with a distinctive shape — 5 tasks, 4 of them
    completed — so a leak would move Ada's totals from 6 to 11 and her
    completion rate from 33.3333 to something else entirely.
    """
    seed, auth = await seeded_client(client, db_session)
    await _seed_task_board(seed)

    grace_seed, _grace_auth = await seeded_client(
        client, db_session, username="grace", email="grace@nexus.test"
    )
    grace_project = await grace_seed.project(name="Grace-only")
    for index in range(4):
        await grace_seed.completed_task(
            day=DAY + timedelta(days=index),
            project_id=grace_project.id,
            estimated_minutes=10,
            actual_minutes=20,
        )
    await grace_seed.task(project_id=grace_project.id, created_at=at(DAY))

    response = await client.get("/api/v1/analytics/tasks", params=TASK_WINDOW, headers=auth)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total_tasks"] == 6
    assert body["completed_tasks"] == 2
    assert body["by_status"]["completed"] == 2
    assert "Grace-only" not in response.text
