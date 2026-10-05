"""The data-quality contract: what analytics says when the data is thin, wrong-shaped or gone.

**Every test here requires a live PostgreSQL and is marked ``integration``.**

The Phase 6 brief devotes three short sections to the conditions under which a
number must *not* be produced, and they are the ones a happy-path suite never
reaches::

    DATA QUALITY
    Analytics must handle: no data, partial data, missing work sessions,
    deleted entities, cancelled tasks, incomplete tasks, timezone boundaries.
    Never crash because there is no activity.

        "Not enough activity yet"     <- correct
        "0% productivity"             <- wrong

    COMPARISON
    Allow comparison with previous periods. Show absolute change and percentage
    change. Handle zero/empty previous periods safely. Never display `NaN`,
    `Infinity` or `undefined%`.

    ANALYTICS REFRESH
    Provide a clear mechanism to calculate metrics, refresh aggregates, detect
    stale analytics. ... Do not silently show stale numbers without indication.

This file turns those paragraphs into assertions. The score arithmetic itself
belongs to ``test_analytics_scoring.py`` and the metric values to the per-surface
API suites, so what is asserted here is the *shape of the answer when the inputs
are missing*: which fields go ``None``, which figures stay ``available=False``
with a reason, and which counters remain true because they are counts rather than
rates.

Why a separate file rather than more cases in the per-surface suites
--------------------------------------------------------------------
A "no data" case and a "how many tasks did I finish" case read nothing alike, and
mixing them buries the first inside the second. More importantly the conditions
here are *cross-cutting*: a cancelled task changes the daily aggregate, the task
roll-up, the deadline figure, the overdue drill-down and the feature vector at
once. A suite organised per endpoint would assert each of those separately and
would never notice that they had started to disagree with each other — which is
the failure that actually happens.

The method
----------
* **Every route is swept, not sampled.** The "never crash because there is no
  activity" rule is a statement about the whole surface, so the empty-account
  sweep enumerates the routes rather than picking the interesting ones. A route
  added later and not added here is a hole in the guarantee, which is why the
  sweep is driven from one module-level tuple.
* **Expected values are derived by hand, not read back from the service.** The
  fixtures make exactly one thing non-zero per case wherever a case is about a
  single condition, and the numbers in the assertions are the ones the rows add
  up to. Every window is stated absolutely (around ``DAY``) rather than relative
  to the clock, so the suite does not start failing on a particular date.
* **Every figure with a denominator in the code is accounted for.** The
  zero-division section below first lists *which* divisions exist and which guard
  each one, and the assertions follow that list rather than a guess.
* **The recursive walks report a path.** A response is checked by descending into
  it, so a non-finite float or a missing reason string names the field that
  produced it instead of failing on "something, somewhere".

What this file deliberately does not do
---------------------------------------
It does not assert the value of any score. Where a score's arithmetic is the
subject, ``test_analytics_scores_api.py`` owns it; here the only claim is about
``available`` and about whether a missing component is excluded-and-said-so or
scored-as-zero.
"""

from __future__ import annotations

import math
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select, text

from app.models.analytics import DailyMetric
from app.models.enums import ActivityEvent, TaskStatus
from app.services.analytics.scoring import NOT_ENOUGH_ACTIVITY
from tests.analytics_fixtures import DAY, AnalyticsSeed, at, seeded_client

pytestmark = pytest.mark.integration

#: The one window every test asks for unless it is about the window itself.
#:
#: Six days: long enough that ``window_days`` is a real divisor, short enough
#: that ``consistency``'s ``active_days / window_days`` does not round to an
#: unrepresentable share. The ends are stated absolutely because the seeded rows
#: sit on :data:`~tests.analytics_fixtures.DAY` and a relative window would drift
#: off them as the clock moves.
WINDOW_START = DAY - timedelta(days=1)
WINDOW_END = DAY + timedelta(days=4)
WINDOW_DAYS = (WINDOW_END - WINDOW_START).days + 1
WINDOW = {"start_date": WINDOW_START.isoformat(), "end_date": WINDOW_END.isoformat()}

#: A due date no clock will reach. Used wherever a fixture needs an open task
#: that is emphatically *not* overdue, so the overdue figures in the assertion
#: are facts about the fixture rather than about today's date.
FAR_DUE = date(2099, 12, 31)

#: A due date no clock will have passed. The counterpart to :data:`FAR_DUE`, for
#: the "already late" half of every overdue assertion.
LONG_PAST = date(2020, 1, 1)

#: An id nobody has ever been issued. ``/analytics/feature-snapshot`` is the one
#: route that needs a ``task_id``, so the empty sweep passes this and asserts the
#: 404 rather than inventing a task to describe.
UNISSUED_TASK_ID = uuid.UUID("00000000-0000-4000-8000-0000000000ff")

#: The routes that read the source tables or the stored aggregates without
#: writing anything. Split out because ``/overview``, ``/productivity``,
#: ``/focus`` and ``/tasks`` each *fill a gap* in ``daily_metrics`` before reading
#: it, which would leave this account's own zero rows behind and change what
#: ``/series`` returns for the rest of the sweep. That distinction is a property
#: of the service, and a suite that did not know about it would assert the wrong
#: empty shape purely from call order.
READ_ONLY_ROUTES: tuple[tuple[str, str], ...] = (
    ("GET", "/api/v1/analytics/series"),
    ("GET", "/api/v1/analytics/trends"),
    ("GET", "/api/v1/analytics/deadlines"),
    ("GET", "/api/v1/analytics/consistency"),
    ("GET", "/api/v1/analytics/estimation"),
    ("GET", "/api/v1/analytics/workload"),
    ("GET", "/api/v1/analytics/time"),
    ("GET", "/api/v1/analytics/projects"),
    ("GET", "/api/v1/analytics/learning"),
    ("GET", "/api/v1/analytics/knowledge"),
    ("GET", "/api/v1/analytics/export"),
    ("GET", "/api/v1/analytics/export.csv"),
)

#: The four that recompute a gap before reading. ``/overview`` is here and not in
#: the list above because it nests ``/productivity``, which is what fills the
#: gap — a read that appears pure but is not.
GAP_FILLING_ROUTES: tuple[tuple[str, str], ...] = (
    ("GET", "/api/v1/analytics/overview"),
    ("GET", "/api/v1/analytics/productivity"),
    ("GET", "/api/v1/analytics/focus"),
    ("GET", "/api/v1/analytics/tasks"),
)

#: Every route that takes a window, for the range-validation sweep. Sixteen of
#: the eighteen: ``/export`` describes the schema rather than a period, and
#: ``/feature-snapshot`` describes one task rather than a period.
WINDOW_ROUTES: tuple[tuple[str, str], ...] = (
    ("GET", "/api/v1/analytics/overview"),
    ("GET", "/api/v1/analytics/productivity"),
    ("GET", "/api/v1/analytics/deadlines"),
    ("GET", "/api/v1/analytics/consistency"),
    ("GET", "/api/v1/analytics/focus"),
    ("GET", "/api/v1/analytics/estimation"),
    ("GET", "/api/v1/analytics/workload"),
    ("GET", "/api/v1/analytics/time"),
    ("GET", "/api/v1/analytics/projects"),
    ("GET", "/api/v1/analytics/tasks"),
    ("GET", "/api/v1/analytics/learning"),
    ("GET", "/api/v1/analytics/knowledge"),
    ("GET", "/api/v1/analytics/trends"),
    ("GET", "/api/v1/analytics/series"),
    ("GET", "/api/v1/analytics/export.csv"),
    ("POST", "/api/v1/analytics/rebuild"),
)

#: All eighteen, for the "nothing crashes" sweep. Read from this module rather
#: than from the OpenAPI document, which ``test_analytics_privacy.py`` already
#: checks against the router; what is new here is the assertion applied to each.
ALL_ROUTES: tuple[tuple[str, str], ...] = (
    *WINDOW_ROUTES,
    ("GET", "/api/v1/analytics/export"),
    ("GET", "/api/v1/analytics/feature-snapshot"),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _rebuild(
    client: Any, headers: dict[str, str], *, start: date = WINDOW_START, end: date = WINDOW_END
) -> dict[str, Any]:
    """Rebuild the window and return the reported body.

    The rebuild is made explicit wherever a test asserts on a stored aggregate.
    Three of the read routes recompute an uncovered window on their own, so
    relying on that would make "was this table populated?" an accident of which
    endpoint the test happened to call first.
    """
    response = await client.post(
        "/api/v1/analytics/rebuild",
        params={"start_date": start.isoformat(), "end_date": end.isoformat()},
        headers=headers,
    )
    assert response.status_code == 202, response.text
    return response.json()


async def _get(client: Any, headers: dict[str, str], path: str, **params: Any) -> dict[str, Any]:
    """``GET`` an analytics route inside the standard window and return its body."""
    response = await client.get(path, params={**WINDOW, **params}, headers=headers)
    assert response.status_code == 200, f"{path}: {response.text}"
    return response.json()


async def _series(
    client: Any, headers: dict[str, str], *, start: date, end: date
) -> list[dict[str, Any]]:
    """The stored daily aggregates for an arbitrary window, as plain dictionaries."""
    response = await client.get(
        "/api/v1/analytics/series",
        params={"start_date": start.isoformat(), "end_date": end.isoformat()},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _by_day(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """``/series`` rows keyed by their ``metric_date``."""
    return {row["metric_date"]: row for row in rows}


async def _snapshot(
    session: Any, client: Any, headers: dict[str, str], task_id: uuid.UUID
) -> dict[str, Any]:
    """The 15 numeric features of one task's snapshot, wrapper asserted.

    ``/analytics/feature-snapshot`` answers a **wrapper** around the feature
    matrix — ``schema_version``, ``generated_at``, ``task_id`` and a ``features``
    object holding the numbers — rather than the bare mapping it used to return.
    A Phase-10 training row has to be attributable to the extraction that
    produced it, and a version string smuggled *inside* the matrix becomes a
    column a model is then asked to fit, so the provenance travels beside the
    numbers rather than among them.

    The three wrapper assertions live here rather than in each caller because
    every caller here reads the matrix and none of them is about provenance;
    this is the one place that says what the wrapper must contain. It also pins
    ``generated_at`` to the **database** clock, since this host is not on UTC and
    a wrapper stamped from ``date.today()`` would be a day ahead of the clock
    every feature in it was derived from. Normalised to UTC for the same reason
    the service normalises: ``now()`` comes back labelled with the *connection's*
    ``TimeZone``, and on this server that is ``Asia/Calcutta``, so a bare
    ``.date()`` on it would be the server-local day rather than the one every
    feature beside it was derived from. The two would then disagree for five and
    a half hours out of every twenty-four.

    Args:
        session: The test's session, used to read the database's ``now()``.
        client: The authenticated HTTP client.
        headers: The caller's authorization headers.
        task_id: The task to describe.

    Returns:
        The ``features`` mapping: the same 15 keys, in the same order, whatever
        the data says.
    """
    response = await client.get(
        "/api/v1/analytics/feature-snapshot", params={"task_id": str(task_id)}, headers=headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"schema_version", "generated_at", "task_id", "features"}
    assert body["schema_version"] == "analytics_features.v1"
    assert body["task_id"] == str(task_id)
    today = (await session.scalar(select(func.now()))).astimezone(UTC).date()
    assert body["generated_at"] == today.isoformat()
    return body["features"]


def _counters(row: dict[str, Any]) -> dict[str, int]:
    """One aggregate row's counters, with the two bookkeeping fields removed.

    ``metric_date`` and ``updated_at`` are stripped so the comparison is about
    the twelve numbers the engine computed. Asserting the whole row is what makes
    a fixture that accidentally moves two counters fail here rather than
    producing a plausible-looking number somewhere downstream.
    """
    return {key: value for key, value in row.items() if key not in ("metric_date", "updated_at")}


def _non_finite_paths(node: Any, path: str = "body") -> list[str]:
    """Every path through ``node`` whose value is a float with no finite value.

    ``NaN`` and ``Infinity`` are the three things the brief forbids a comparison
    from displaying, and they are the three a naive ``current / previous`` in a
    response model produces silently: they serialise as bare tokens that a JSON
    reader in another language will reject outright. Walked rather than
    string-searched so the failure names the field.
    """
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            found.extend(_non_finite_paths(value, f"{path}[{key!r}]"))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.extend(_non_finite_paths(value, f"{path}[{index}]"))
    elif isinstance(node, float) and not math.isfinite(node):
        found.append(f"{path} = {node!r}")
    return found


def _unavailable_paths(node: Any, path: str = "body") -> list[str]:
    """Every object that says ``available=False`` and breaks the contract.

    Three rules, checked together because each is a way the "Not enough activity
    yet" promise can be kept in the wording and broken in the number:

    * the reason has to be present and has to *be* the shared phrase, so a
      client needs one string to match rather than a family of near-synonyms;
    * ``score`` has to be ``None`` — a zero next to ``available=False`` is the
      exact "0% productivity" the brief calls wrong, because a client that
      renders the number without reading the flag will render it;
    * nothing deeper may contradict it.
    """
    found: list[str] = []
    if isinstance(node, dict):
        if node.get("available") is False:
            reason = node.get("reason_if_unavailable")
            if not isinstance(reason, str) or not reason.startswith(NOT_ENOUGH_ACTIVITY):
                found.append(f"{path}: available=False but reason is {reason!r}")
            if node.get("score") is not None:
                found.append(f"{path}: available=False but score is {node['score']!r}")
        for key, value in node.items():
            found.extend(_unavailable_paths(value, f"{path}[{key!r}]"))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.extend(_unavailable_paths(value, f"{path}[{index}]"))
    return found


def _assert_honest(response: Any, *, where: str) -> None:
    """Assert a response carries no non-finite number and no broken unavailability.

    Applied to the raw text as well as the parsed body: a ``NaN`` that reached
    the serialiser would be visible in the text even in a field this walk does
    not descend into, and the brief names the *rendered* value, not the model.
    """
    assert "NaN" not in response.text, f"{where} serialised NaN"
    assert "Infinity" not in response.text, f"{where} serialised Infinity"
    if response.headers.get("content-type", "").startswith("application/json"):
        payload = response.json()
        assert _non_finite_paths(payload) == [], where
        assert _unavailable_paths(payload) == [], where


async def _database_zone(session: Any) -> ZoneInfo:
    """The calendar ``daily_metrics`` is cut on: the connection's ``TimeZone``.

    Asked of the database rather than assumed. The rule under test is that the
    boundary is the *server's* midnight and not a constant compiled into the
    SQL, so a fixture that hard-coded an offset would pin a deployment rather
    than the rule.
    """
    name = await session.scalar(select(func.current_setting("TimeZone")))
    return ZoneInfo(str(name))


def _local_instant(zone: ZoneInfo, day: date, hour: int, minute: int = 0) -> datetime:
    """The instant at ``hour:minute`` on ``day`` as the database reads it.

    :func:`~tests.analytics_fixtures.at` builds UTC instants, which is right for
    a fixture that must not care where the boundary is and wrong for one that is
    about it. ``23:59 UTC`` is the last minute of a day in London and half past
    five in the morning of the *next* day in Calcutta, so a UTC-hour fixture
    cannot say anything about where the cut falls.
    """
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone).astimezone(UTC)


async def _session_at(
    seed: AnalyticsSeed, *, start: datetime, minutes: int, project_id: uuid.UUID
) -> None:
    """A work session starting at an exact minute-precision instant.

    ``AnalyticsSeed.work_session`` takes a start *hour*, which is the right
    granularity for every other case in the suite and the wrong one here: the
    claim under test is where the **midnight** cut falls, and an hour-granular
    fixture cannot distinguish a cut at 00:00 from one at 01:00. The row is
    therefore written by the shared helper — which gets the status, the estimated
    and actual ends, and the ownership right — and then moved onto the exact
    instant. The helper has no ``minute`` parameter and this file does not edit a
    shared fixture to add one for a single caller. Pair it with
    :func:`_local_instant` to place the block on the calendar the buckets are
    actually cut on.
    """
    row = await seed.work_session(
        day=start.date(), minutes=minutes, project_id=project_id, start_hour=start.hour
    )
    row.scheduled_start = start
    row.scheduled_end = start + timedelta(minutes=minutes)
    row.actual_start = start
    row.actual_end = start + timedelta(minutes=minutes)
    await seed.flush()


async def _stored_stamps(
    session: Any, user_id: uuid.UUID, *, start: date, end: date
) -> list[tuple[date, datetime]]:
    """``(metric_date, updated_at)`` for one account's stored aggregates.

    Read as columns rather than as ORM entities on purpose: the session that
    wrote the rows is the one reading them back, so an entity read would return
    whatever the identity map cached on first load and an "the stamp advanced"
    assertion would compare a stale object against itself and pass.
    """
    result = await session.execute(
        select(DailyMetric.metric_date, DailyMetric.updated_at)
        .where(
            DailyMetric.user_id == user_id,
            DailyMetric.metric_date >= start,
            DailyMetric.metric_date <= end,
        )
        .order_by(DailyMetric.metric_date.asc())
    )
    return [(row[0], row[1]) for row in result.all()]


# ---------------------------------------------------------------------------
# No data at all
# ---------------------------------------------------------------------------


async def test_every_endpoint_answers_a_well_formed_response_for_an_empty_account(
    client, db_session
):
    """All eighteen routes, for an account that has never recorded anything.

    "Never crash because there is no activity" is the brief's data-quality rule
    stated outright. A 500 on a brand-new account is a dashboard that is unusable
    at exactly the moment a new user first opens it, which is the only moment
    every one of them is a new user.

    ``status_code < 500`` is the headline; the per-route value-level claims are
    in the three tests below rather than here, because a single assertion over a
    loop says nothing about *which* field was wrong when it fails. The sweep's
    job is the status and the absence of ``NaN``/``Infinity`` — the two failures
    that would take the whole surface down rather than one card.
    """
    _seed, auth = await seeded_client(client, db_session)
    statuses: dict[str, int] = {}

    for method, path in ALL_ROUTES:
        params = {**WINDOW, "task_id": str(UNISSUED_TASK_ID)}
        response = await client.request(method, path, params=params, headers=auth)

        assert response.status_code < 500, f"{path}: {response.text}"
        assert "NaN" not in response.text, path
        assert "Infinity" not in response.text, path
        statuses[path] = response.status_code

    # Seventeen of the eighteen answer 200 or 202; the eighteenth is a 404 for
    # an id that does not exist, which is the correct well-formed answer for a
    # route that describes one specific task. Pinned so that a change in either
    # direction — a new 500, or a 200 that answered for a task it never found —
    # fails here rather than hiding inside a sweep.
    assert statuses["/api/v1/analytics/feature-snapshot"] == 404
    assert {
        code for path, code in statuses.items() if path != "/api/v1/analytics/feature-snapshot"
    } == {200, 202}


async def test_the_read_only_routes_report_the_empty_shape_not_a_zero_score(client, db_session):
    """The twelve routes that never write, on an account with no rows at all.

    The important assertion is a shape that is easy to get subtly wrong:
    ``/series`` and ``/trends`` come back **empty** rather than zero-filled,
    because a window nobody has rebuilt is a window nobody has measured, and
    zero-filling it would claim a completeness the table does not have. That is
    only observable on routes that read the stored aggregates without recomputing
    them, so they are asked **first** — the moment any gap-filling route runs on
    this account, the honest empty answer is gone and the sweep would be
    asserting a later state than the one it was written for.
    """
    _seed, auth = await seeded_client(client, db_session)

    series = await _get(client, auth, "/api/v1/analytics/series")
    trends = await _get(client, auth, "/api/v1/analytics/trends")
    projects = (await _get(client, auth, "/api/v1/analytics/projects"))["items"]

    # "Never been aggregated" is not "aggregated and found nothing".
    assert series == []
    assert trends == []
    assert projects == []

    for method, path in READ_ONLY_ROUTES:
        response = await client.request(method, path, params=WINDOW, headers=auth)
        assert response.status_code == 200, f"{path}: {response.text}"
        _assert_honest(response, where=path)

    # Counts of nothing are zero; rates over nothing are absent.
    deadlines = await _get(client, auth, "/api/v1/analytics/deadlines")
    assert deadlines["on_time"] == 0
    assert deadlines["late"] == 0
    assert deadlines["still_overdue"] == 0
    assert deadlines["total_considered"] == 0
    assert deadlines["adherence_rate"] is None
    assert deadlines["rate"] is None

    workload = await _get(client, auth, "/api/v1/analytics/workload")
    assert workload["open_tasks"] == 0
    assert workload["high_priority_open"] == 0
    assert workload["overdue_open"] == 0
    assert workload["scheduled_minutes"] == 0
    assert workload["actual_minutes"] == 0
    assert workload["status_counts"]["total"] == 0
    assert workload["priority_counts"]["total"] == 0

    time_view = await _get(client, auth, "/api/v1/analytics/time")
    assert time_view["total_minutes"] == 0
    assert time_view["unassigned_minutes"] == 0
    assert time_view["project_id"] is None
    assert time_view["by_project"] == []
    assert time_view["by_task"] == []


async def test_the_gap_filling_routes_answer_unavailable_for_an_empty_account(client, db_session):
    """``/overview``, ``/productivity``, ``/focus`` and ``/tasks`` on nothing.

    These four are separated from the sweep above because they used to fill an
    uncovered window in ``daily_metrics`` before reading it. Sharing one account
    with the read-only routes would make ``/series`` there return this account's
    own freshly-written zero rows rather than the empty list the rule is about,
    and the two questions would silently become one.

    Each body is taken from **the first and only** time its route is called. That
    still matters, and for the opposite reason to the one it used to: these
    routes no longer write, so nothing a later call sees could differ from this
    one — but pinning "first and only" keeps that true if a write ever comes back.

    ``by_status`` is the one counter that legitimately reads zero, and
    ``completion_rate`` is the one that must not: no work entered the system, so
    there is nothing to be a percentage *of*.
    """
    _seed, auth = await seeded_client(client, db_session)
    bodies: dict[str, dict[str, Any]] = {}

    for method, path in GAP_FILLING_ROUTES:
        response = await client.request(method, path, params=WINDOW, headers=auth)
        assert response.status_code == 200, f"{path}: {response.text}"
        _assert_honest(response, where=path)
        bodies[path.rsplit("/", 1)[-1]] = response.json()

    overview = bodies["overview"]
    productivity = bodies["productivity"]
    focus = bodies["focus"]
    tasks = bodies["tasks"]

    assert overview["reason_if_empty"].startswith(NOT_ENOUGH_ACTIVITY)
    assert {point["current"] for point in overview["totals"]} == {0.0}
    assert {point["label"] for point in overview["totals"]} == {
        "actual_minutes",
        "calendar_events",
        "knowledge_events",
        "planned_minutes",
        "projects_touched",
        "tasks_blocked",
        "tasks_cancelled",
        "tasks_completed",
        "tasks_created",
        "tasks_overdue",
        "tasks_rescheduled",
        "work_sessions",
    }
    # Nothing had ever been aggregated, and asking did not aggregate it either.
    # `stale` says exactly that: these figures describe no measurement at all, so
    # they are maximally stale rather than not stale at all — the old
    # `bool(covered) and ...` guard read "nothing computed yet" as "nothing to be
    # stale about", which is backwards. And `aggregates_through` is `None`, not the
    # end of the window: these routes used to fill the gap and report themselves
    # measured through it, which is how an account that had never been rebuilt
    # once came to claim it was measured through 2030.
    assert overview["stale"] is True
    assert overview["aggregates_through"] is None
    assert overview["data_as_of"] is None

    assert productivity["available"] is False
    assert productivity["score"] is None
    # The four components are still listed so a client can render the breakdown
    # without special-casing the whole object, and each says it was not counted.
    assert [component["name"] for component in productivity["components"]] == [
        "completion",
        "deadline",
        "consistency",
        "focus",
    ]
    assert [component["points"] for component in productivity["components"]] == [0.0] * 4
    assert all(
        component["explanation"].startswith("Not counted:")
        for component in productivity["components"]
    )
    assert productivity["weight_total"] == 100.0

    assert focus["score"] is None
    assert focus["avg_session_minutes"] is None
    assert focus["total_minutes"] == 0
    assert focus["focused_minutes"] == 0
    assert focus["completed_planned_sessions"] == 0
    assert focus["interruptions"] == 0
    assert focus["reschedules"] == 0

    assert tasks["total_tasks"] == 0
    assert tasks["completed_tasks"] == 0
    assert tasks["open_tasks"] == 0
    assert tasks["cancelled_tasks"] == 0
    assert tasks["blocked_tasks"] == 0
    assert tasks["completion_rate"] is None
    assert tasks["overdue_rate"] is None
    assert tasks["avg_completion_days"] is None
    assert tasks["avg_cycle_minutes"] is None
    assert tasks["avg_estimate_error_minutes"] is None
    assert tasks["top_overdue"] == []
    assert tasks["by_status"]["total"] == 0
    assert tasks["by_priority"]["total"] == 0
    assert tasks["estimation"]["available"] is False


async def test_an_unavailable_figure_says_not_enough_activity_and_never_reports_zero(
    client, db_session
):
    """The brief's own example, checked in the form it is written in.

    ``"Not enough activity yet"`` is correct and ``"0% productivity"`` is wrong.
    Two ways that goes wrong in an implementation, both checked here: the
    unavailable figure carries a zero in the numeric field where a client that
    renders the number without reading the flag will render it, and the rendered
    page shows the literal forbidden string. The recursive walk covers the first
    across every nested object in every response; the text check covers the
    second, including inside a CSV where nothing is JSON at all.
    """
    _seed, auth = await seeded_client(client, db_session)

    for method, path in ALL_ROUTES:
        response = await client.request(
            method,
            path,
            params={**WINDOW, "task_id": str(UNISSUED_TASK_ID)},
            headers=auth,
        )
        assert "0% productivity" not in response.text, path
        assert "undefined" not in response.text, path
        if response.status_code == 200 and response.headers.get("content-type", "").startswith(
            "application/json"
        ):
            assert _unavailable_paths(response.json()) == [], path

    # The same rule stated positively, so the walk above is not the only thing
    # keeping it honest: the phrase itself is present and carries the reason.
    productivity = await _get(client, auth, "/api/v1/analytics/productivity")
    assert productivity["reason_if_unavailable"].startswith(NOT_ENOUGH_ACTIVITY)
    assert productivity["available"] is False


# ---------------------------------------------------------------------------
# Partial data
# ---------------------------------------------------------------------------


async def test_tasks_with_no_work_sessions_measure_what_exists_and_refuse_the_rest(
    client, db_session
):
    """Four finished tasks and a fifth still open, with no timer ever started.

    This is the shape the "partial data" rule is about: three of the four
    productivity components are measurable and one family of metrics has nothing
    to work from at all. The score is **not** unavailable and it is **not** 0 —
    it is 49, and the two components that could not be measured contribute zero
    points out of their own weight with an explanation saying so. That is the
    documented rule: a missing component is excluded and named, never scored as
    a low result.

    The arithmetic, by hand, from the rows:

    * four tasks created and four completed on :data:`DAY`, one more created
      and still ``todo`` → ``tasks_created = 5``, ``tasks_completed = 4``;
    * completion = ``4 / 5`` = 80% of a 30-point weight → **24.0 points**;
    * all four were completed on their due date, so adherence is 100% of a
      25-point weight → **25.0 points**;
    * no work session was ever started, so the consistency score is
      *unavailable* (its documented rule: a user who never started a timer has
      no record of which days they were present) → **0.0 of 20**;
    * the same absence makes the focus score unavailable → **0.0 of 25**.

    ``24 + 25 = 49``. The consistency route still reports ``active_day_ratio``
    as ``0.0`` rather than ``None``: the denominator (``window_days``) exists and
    is a real measurement of nothing happening, which is a different statement
    from "the ratio is undefined".
    """
    seed, auth = await seeded_client(client, db_session)
    project = await seed.project()
    for _index in range(4):
        await seed.completed_task(day=DAY, project_id=project.id)
    await seed.task(project_id=project.id, created_at=at(DAY), due_date=FAR_DUE)
    await _rebuild(client, auth)

    overview = await _get(client, auth, "/api/v1/analytics/overview")
    totals = {point["label"]: point["current"] for point in overview["totals"]}
    assert totals["tasks_created"] == 5.0
    assert totals["tasks_completed"] == 4.0
    # No timer was ever started, so both minute columns stay at zero.
    assert totals["planned_minutes"] == 0.0
    assert totals["actual_minutes"] == 0.0
    assert totals["work_sessions"] == 0.0

    productivity = overview["productivity"]
    assert productivity["available"] is True
    assert productivity["score"] == 49
    assert [
        (component["name"], component["points"], component["max_points"])
        for component in productivity["components"]
    ] == [
        ("completion", 24.0, 30.0),
        ("deadline", 25.0, 25.0),
        ("consistency", 0.0, 20.0),
        ("focus", 0.0, 25.0),
    ]
    assert productivity["components"][2]["explanation"].startswith("Not counted:")
    assert productivity["components"][3]["explanation"].startswith("Not counted:")

    # Measured, because the rows support it.
    deadlines = await _get(client, auth, "/api/v1/analytics/deadlines")
    assert deadlines["available"] is True
    assert deadlines["on_time"] == 4
    assert deadlines["late"] == 0
    assert deadlines["adherence_rate"] == 100.0

    tasks = await _get(client, auth, "/api/v1/analytics/tasks")
    assert tasks["total_tasks"] == 5
    assert tasks["completed_tasks"] == 4
    assert tasks["open_tasks"] == 1
    assert tasks["completion_rate"] == 80.0
    assert tasks["overdue_tasks"] == 0
    # Every completion took 09:00 → 12:00, so three hours of cycle time.
    assert tasks["avg_cycle_minutes"] == 180.0
    assert tasks["avg_completion_days"] == 0.125

    # Refused, because no session row exists to measure.
    for path in ("consistency", "focus", "time", "estimation"):
        body = await _get(client, auth, f"/api/v1/analytics/{path}")
        assert body["available"] is False, path
        assert body["reason_if_unavailable"].startswith(NOT_ENOUGH_ACTIVITY), path

    consistency = await _get(client, auth, "/api/v1/analytics/consistency")
    assert consistency["score"] is None
    assert consistency["active_days"] == 0
    assert consistency["window_days"] == WINDOW_DAYS
    assert consistency["work_sessions"] == 0
    assert consistency["longest_streak"] == 0
    assert consistency["active_day_ratio"] == 0.0

    focus = await _get(client, auth, "/api/v1/analytics/focus")
    assert focus["score"] is None
    assert focus["total_minutes"] == 0
    assert focus["avg_session_minutes"] is None

    time_view = await _get(client, auth, "/api/v1/analytics/time")
    assert time_view["total_minutes"] == 0
    assert time_view["by_project"] == []
    assert time_view["by_task"] == []

    # No task carried an estimate, so accuracy is not a perfect zero — it is
    # unavailable. A task with no estimate is not a task estimated at zero.
    estimation = await _get(client, auth, "/api/v1/analytics/estimation")
    assert estimation["sample_count"] == 0
    assert estimation["absolute_error"] is None
    assert estimation["bias"] is None
    assert tasks["estimation"]["sample_count"] == 0
    assert tasks["avg_estimate_error_minutes"] is None

    # A workload of no planned and no actual minutes, while five tasks exist.
    # ``available`` follows the minutes, because that is what the panel is
    # about; the task counts beside it are still true and still reported.
    workload = await _get(client, auth, "/api/v1/analytics/workload")
    assert workload["available"] is False
    assert workload["scheduled_minutes"] == 0
    assert workload["actual_minutes"] == 0
    assert workload["workload_ratio"] is None
    assert workload["average_daily_scheduled_minutes"] is None
    assert workload["open_tasks"] == 1
    assert workload["status_counts"]["total"] == 5


async def test_work_sessions_with_no_estimates_measure_time_and_refuse_the_accuracy(
    client, db_session
):
    """Time recorded, estimates never written: the time is measured, the accuracy is not.

    The mirror image of the case above, and the one that catches an
    implementation that *shares* the "is there any data" decision between
    unrelated metrics. A 50-minute session and two finished tasks make focus,
    time distribution, workload and consistency all measurable; the same two
    tasks, carrying no ``estimated_minutes``, make estimation accuracy
    unavailable. Nothing here is zero, because every figure has rows behind it.

    One activity event is seeded on :data:`DAY` so the consistency score has an
    active day to be a score *of*: 1 active day out of 6 is ``16.6667%``, which
    rounds to **17**. Without it, ``active_days`` would be 0 and the formula
    would legitimately produce 0 — which is a true measurement, but a poor
    illustration of a metric that *can* be measured.
    """
    seed, auth = await seeded_client(client, db_session)
    project = await seed.project()
    first = await seed.completed_task(day=DAY, project_id=project.id, actual_minutes=75)
    await seed.completed_task(day=DAY, project_id=project.id, actual_minutes=75)
    await seed.work_session(day=DAY, minutes=50, project_id=project.id, task_id=first.id)
    await seed.activity(
        ActivityEvent.TASK_STARTED, day=DAY, project_id=project.id, task_id=first.id
    )
    await _rebuild(client, auth)

    estimation = await _get(client, auth, "/api/v1/analytics/estimation")
    assert estimation["available"] is False
    assert estimation["sample_count"] == 0
    assert estimation["pairs_compared"] == 0
    assert estimation["absolute_error"] is None
    assert estimation["percentage_error"] is None
    assert estimation["bias"] is None
    assert estimation["median_error"] is None
    assert estimation["under_estimation_rate"] is None
    assert estimation["over_estimation_rate"] is None

    tasks = await _get(client, auth, "/api/v1/analytics/tasks")
    assert tasks["estimation"]["available"] is False
    assert tasks["estimation"]["sample_count"] == 0
    assert tasks["avg_estimate_error_minutes"] is None
    # The task figures are unaffected: the tasks exist and were finished.
    assert tasks["total_tasks"] == 2
    assert tasks["completed_tasks"] == 2
    assert tasks["completion_rate"] == 100.0

    # The session is measurable everywhere time is.
    focus = await _get(client, auth, "/api/v1/analytics/focus")
    assert focus["available"] is True
    assert focus["avg_session_minutes"] == 50.0
    assert focus["total_minutes"] == 50
    assert focus["focused_minutes"] == 50
    assert focus["completed_planned_sessions"] == 1
    assert focus["interruptions"] == 0
    # 50 minutes is past the 45-minute uninterrupted-block target, so the
    # length term saturates at 60 points, and the one session was followed
    # through, so the follow-through term is a further 40.
    assert focus["score"] == 100

    time_view = await _get(client, auth, "/api/v1/analytics/time")
    assert time_view["available"] is True
    assert time_view["total_minutes"] == 50
    assert [
        (bucket["key"], bucket["minutes"], bucket["share"]) for bucket in time_view["by_project"]
    ] == [(str(project.id), 50, 100.0)]
    assert [bucket["key"] for bucket in time_view["by_task"]] == [str(first.id)]

    consistency = await _get(client, auth, "/api/v1/analytics/consistency")
    assert consistency["available"] is True
    assert consistency["active_days"] == 1
    assert consistency["window_days"] == WINDOW_DAYS
    assert consistency["work_sessions"] == 1
    assert consistency["longest_streak"] == 1
    assert consistency["current_streak"] == 1
    assert consistency["active_day_ratio"] == 16.6667
    assert consistency["score"] == 17

    workload = await _get(client, auth, "/api/v1/analytics/workload")
    assert workload["available"] is True
    assert workload["scheduled_minutes"] == 50
    assert workload["actual_minutes"] == 50
    # No availability rule has been declared, so there is no denominator for a
    # workload ratio. Unconfigured is not zero.
    assert workload["available_minutes"] is None
    assert workload["workload_ratio"] is None
    # 50 planned minutes over the 6-day window.
    assert workload["average_daily_scheduled_minutes"] == 8.33


async def test_a_project_with_no_tasks_leaves_the_whole_surface_unavailable(client, db_session):
    """A project exists; nothing has ever been filed under it.

    ``test_analytics_projects_api.py`` already pins what ``/projects`` says about
    an empty project. This asks the other half of the question: does the presence
    of that one project — and of nothing else — make any *other* figure look
    measured? It must not. An account holding a single empty project has done
    nothing, and every rate on the surface has no denominator.
    """
    seed, auth = await seeded_client(client, db_session)
    project = await seed.project(name="Vacant")
    await _rebuild(client, auth)

    projects = (await _get(client, auth, "/api/v1/analytics/projects"))["items"]
    assert [entry["project_id"] for entry in projects] == [str(project.id)]
    vacant = projects[0]
    assert vacant["total_tasks"] == 0
    assert vacant["available"] is False
    assert vacant["reason_if_unavailable"].startswith(NOT_ENOUGH_ACTIVITY)
    assert vacant["completion_rate"] is None
    assert vacant["avg_task_actual_minutes"] is None
    assert vacant["avg_task_minutes"] is None
    assert vacant["velocity"]["tasks_per_week"] is None
    assert vacant["velocity"]["estimated_minutes_per_week"] is None

    tasks = await _get(client, auth, "/api/v1/analytics/tasks")
    assert tasks["available"] is False
    assert tasks["total_tasks"] == 0
    assert tasks["completion_rate"] is None
    assert tasks["overdue_rate"] is None

    overview = await _get(client, auth, "/api/v1/analytics/overview")
    assert overview["reason_if_empty"].startswith(NOT_ENOUGH_ACTIVITY)
    assert {point["current"] for point in overview["totals"]} == {0.0}
    assert overview["productivity"]["available"] is False
    assert overview["productivity"]["score"] is None

    workload = await _get(client, auth, "/api/v1/analytics/workload")
    assert workload["available"] is False
    assert workload["open_tasks"] == 0
    assert workload["workload_ratio"] is None

    time_view = await _get(client, auth, "/api/v1/analytics/time")
    assert time_view["available"] is False
    assert time_view["by_project"] == []

    export = await client.get(
        "/api/v1/analytics/export.csv",
        params={**WINDOW, "dataset": "task_performance"},
        headers=auth,
    )
    assert export.status_code == 200, export.text
    assert export.headers["X-Nexus-Row-Count"] == "0"
    # A header and no data rows: a file the user can always open and diff.
    assert export.text.count("\r\n") == 1


async def test_a_note_with_no_tag_and_no_event_leaves_knowledge_unavailable(client, db_session):
    """A note exists in the knowledge base; nothing was ever recorded *about* it.

    The two knowledge figures are read from different tables and the difference
    is the point. ``notes_by_status`` counts the rows the user **owns** — "how
    big is my knowledge base" — and correctly reports one. The windowed counters
    are read from the **event feed**, because a note's ``updated_at`` is rewritten
    by every autosave, and an account that has never written an event has
    recorded no knowledge activity in this window.

    Reporting "0 notes created" as a measurement would be defensible, but
    reporting the window as *available* on the strength of a row nobody created
    an event for would not be. Tags are the same case in miniature: ``seed.note``
    writes no tag, so the "most active knowledge areas" list is empty rather than
    padded with something.
    """
    seed, auth = await seeded_client(client, db_session)
    await seed.note(day=DAY)
    await _rebuild(client, auth)

    knowledge = await _get(client, auth, "/api/v1/analytics/knowledge")
    assert knowledge["available"] is False
    assert knowledge["reason_if_unavailable"].startswith(NOT_ENOUGH_ACTIVITY)
    assert knowledge["interactions"] == 0
    assert knowledge["notes_created"] == 0
    assert knowledge["notes_updated"] == 0
    assert knowledge["most_used_tags"] == []
    assert knowledge["top_tags"] == []
    # The note really is there — this is not an empty fixture passing for an
    # empty answer.
    assert knowledge["notes_by_status"]["total"] == 1
    assert knowledge["notes_by_status"]["draft"] == 1
    assert knowledge["documents_added"] == 0

    learning = await _get(client, auth, "/api/v1/analytics/learning")
    assert learning["available"] is False
    assert learning["knowledge_interactions"] == 0
    assert learning["notes_created"] == 0
    assert learning["study_events"] == 0
    assert learning["study_minutes"] == 0

    overview = await _get(client, auth, "/api/v1/analytics/overview")
    totals = {point["label"]: point["current"] for point in overview["totals"]}
    assert totals["knowledge_events"] == 0.0


# ---------------------------------------------------------------------------
# Zero division
# ---------------------------------------------------------------------------


async def test_no_empty_denominator_anywhere_on_the_surface_produces_a_number(client, db_session):
    """Every division in the engine, exercised with an empty denominator.

    The divisions that exist, and what guards each — read off
    ``app/services/analytics/scoring.py`` and the service, not guessed:

    * ``rate(numerator, denominator)`` → ``None``. It backs every rate on the
      surface: completion, adherence, overdue, time-distribution shares,
      ``active_day_ratio``, the workload ratio, the feature vector's historical
      completion rate.
    * ``percent_change(current, previous)`` → ``None`` when ``previous`` is zero
      or negative, because there is no honest percentage change from nothing to
      something.
    * ``consistency_score`` refuses before dividing: ``session_count <= 0`` and
      ``window_days <= 0`` are both checked.
    * ``focus_score`` refuses before dividing: ``completed_planned_sessions <= 0``
      is checked, so ``completed / (completed + interruptions)`` never sees a
      zero denominator.
    * ``deadline_adherence`` refuses before dividing: ``on_time + late <= 0``.
    * ``estimation_accuracy`` drops every pair whose estimate is not positive,
      so ``|actual - estimated| / estimated`` never sees a zero.
    * ``service.workload`` guards ``scheduled / window_days`` with
      ``if scheduled`` and ``project_analytics`` guards each of its three
      divisions the same way.

    With nothing recorded, every one of those denominators is zero at once, so
    one account exercises the whole list. The recursive walk proves none of them
    leaked a non-finite float anywhere in any response, and the field-level
    assertions prove the guarded ones are ``None`` rather than ``0``.
    """
    _seed, auth = await seeded_client(client, db_session)
    await _rebuild(client, auth)

    for method, path in (*WINDOW_ROUTES, ("GET", "/api/v1/analytics/export")):
        response = await client.request(method, path, params=WINDOW, headers=auth)
        assert response.status_code in (200, 202), f"{path}: {response.text}"
        _assert_honest(response, where=path)

    # ``percent_change`` from a previous period of zero: 0/0 is the case that
    # serialises as ``NaN`` and 1/0 as ``Infinity``, and the brief names both.
    overview = await _get(client, auth, "/api/v1/analytics/overview")
    assert len(overview["totals"]) == 12
    for point in overview["totals"]:
        assert point["current"] == 0.0, point["label"]
        assert point["previous"] == 0.0, point["label"]
        assert point["absolute_change"] == 0.0, point["label"]
        assert point["percent_change"] is None, point["label"]

    # One comparison point per day of the window, whether or not anything
    # happened on it, and every one of them the 0/0 case.
    workload = await _get(client, auth, "/api/v1/analytics/workload")
    assert [point["label"] for point in workload["comparison"]] == [
        (WINDOW_START + timedelta(days=offset)).isoformat() for offset in range(WINDOW_DAYS)
    ]
    for point in workload["comparison"]:
        assert point["current"] == 0.0
        assert point["previous"] == 0.0
        assert point["absolute_change"] == 0.0
        assert point["percent_change"] is None
    assert workload["workload_ratio"] is None
    assert workload["average_daily_scheduled_minutes"] is None
    assert workload["available_minutes"] is None

    # The stored series is zero rows' worth of measurements, not NaN.
    rows = await _series(client, auth, start=WINDOW_START, end=WINDOW_END)
    assert len(rows) == WINDOW_DAYS
    for row in rows:
        assert set(_counters(row).values()) == {0}, row["metric_date"]


async def test_an_estimate_of_zero_is_never_used_as_a_denominator(client, db_session):
    """One task estimated at 60 and taken 90, one estimated at 0: one sample, not two.

    The dangerous division on this surface is
    ``abs(actual - estimated) / estimated * 100``, because an ``estimated_minutes``
    of zero is a perfectly valid column value and dividing by it is undefined
    rather than large. The documented rule drops such pairs and counts them out
    of ``sample_count``, so the sample describes what was actually measured.

    The arithmetic over the surviving pair ``(60, 90)``: absolute error **30.0**,
    signed error ``60 - 90 = -30`` so a bias of **-30.0** (estimates ran below
    the time taken), percentage error ``30 / 60`` = **50.0%**, a
    under-estimation rate of **100.0%** and an over-estimation rate of **0.0%**.
    A single pair, so the median equals the mean.
    """
    seed, auth = await seeded_client(client, db_session)
    project = await seed.project()
    await seed.completed_task(
        day=DAY, project_id=project.id, estimated_minutes=60, actual_minutes=90
    )
    await seed.completed_task(
        day=DAY, project_id=project.id, estimated_minutes=0, actual_minutes=45
    )

    estimation = await _get(client, auth, "/api/v1/analytics/estimation")
    assert estimation["available"] is True
    assert estimation["sample_count"] == 1
    assert estimation["pairs_compared"] == 1
    assert estimation["absolute_error"] == 30.0
    assert estimation["bias"] == -30.0
    assert estimation["percentage_error"] == 50.0
    assert estimation["median_error"] == 30.0
    assert estimation["under_estimation_rate"] == 100.0
    assert estimation["over_estimation_rate"] == 0.0

    response = await client.get("/api/v1/analytics/estimation", params=WINDOW, headers=auth)
    assert "NaN" not in response.text
    assert "Infinity" not in response.text


async def test_a_previous_period_of_zero_yields_null_never_infinity(client, db_session):
    """Activity now, none in the equal-length window before it.

    The comparison half of the brief: "handle zero/empty previous periods
    safely. Never display ``NaN``, ``Infinity`` or ``undefined%``." Two
    different shapes of the same guard, and both are checked on real rows.

    * **Trend points.** A bucket with no counterpart last period carries
      ``previous``, ``absolute_change`` and ``percent_change`` as ``None``. The
      absolute change is ``None`` too, not ``2.0``: there is nothing to subtract
      from, and a difference from an absence is a number about nothing.
    * **Overview totals and the daily workload comparison.** These are always
      present, so they show ``absolute_change = 2.0`` (a real difference against
      a real zero) and ``percent_change = None`` (no honest percentage change
      from nothing to something).
    """
    seed, auth = await seeded_client(client, db_session)
    project = await seed.project()
    for _index in range(2):
        await seed.completed_task(day=DAY, project_id=project.id)
    await seed.work_session(day=DAY, minutes=45, project_id=project.id)
    await _rebuild(client, auth)

    start, end = DAY, DAY + timedelta(days=2)
    window = {"start_date": start.isoformat(), "end_date": end.isoformat()}

    trends = await client.get(
        "/api/v1/analytics/trends",
        params={**window, "metric": "tasks_completed"},
        headers=auth,
    )
    assert trends.status_code == 200, trends.text
    points = trends.json()
    # One point only: the two days with no completions are omitted, not zeroed.
    assert [point["bucket"] for point in points] == [DAY.isoformat()]
    assert points[0]["value"] == 2.0
    assert points[0]["previous"] is None
    assert points[0]["absolute_change"] is None
    assert points[0]["percent_change"] is None

    overview = await client.get("/api/v1/analytics/overview", params=window, headers=auth)
    assert overview.status_code == 200, overview.text
    totals = {point["label"]: point for point in overview.json()["totals"]}
    assert totals["tasks_completed"]["current"] == 2.0
    assert totals["tasks_completed"]["previous"] == 0.0
    assert totals["tasks_completed"]["absolute_change"] == 2.0
    assert totals["tasks_completed"]["percent_change"] is None

    workload = await client.get("/api/v1/analytics/workload", params=window, headers=auth)
    assert workload.status_code == 200, workload.text
    comparison = {point["label"]: point for point in workload.json()["comparison"]}
    # The day that had a session: planned and actual are both 45, so a real
    # zero percent change is a true measurement and is reported.
    assert comparison[DAY.isoformat()]["percent_change"] == 0.0
    # The two days with nothing on them: 0 over 0, which has no percentage.
    for day in (DAY + timedelta(days=1), DAY + timedelta(days=2)):
        point = comparison[day.isoformat()]
        assert point["current"] == 0.0
        assert point["previous"] == 0.0
        assert point["absolute_change"] == 0.0
        assert point["percent_change"] is None


# ---------------------------------------------------------------------------
# Timezone boundaries
# ---------------------------------------------------------------------------


async def test_a_block_at_2359_local_and_one_at_0001_local_the_next_day_are_two_days(
    client, db_session
):
    """The database's day boundary, at minute precision, from both sides.

    ``daily_metrics.metric_date`` is cut at the connection's own midnight,
    written into the SQL as ``date(column AT TIME ZONE current_setting('TimeZone'))``
    rather than left to an implicit cast. Two consequences are asserted here, and
    neither is observable at hour granularity:

    * a block starting at **23:59** belongs to the first day, and one starting
      at **00:01** the next morning belongs to the second — so the cut is at
      midnight and not at some hour boundary;
    * a window that straddles the boundary includes both, and a window that ends
      on the first day excludes the second one entirely.

    Both minutes are **local to the server's zone**, read from
    ``current_setting('TimeZone')`` rather than assumed, so the pair sits either
    side of the cut the code actually cuts. Spelled in UTC it would prove
    nothing: 23:59 UTC is half past five the following morning in
    ``Asia/Calcutta``, and both blocks would land on the same day.

    The two minutes columns are read from two different timestamps on the same
    row (``scheduled_start`` and ``actual_start``), so a boundary bug in either
    would show up as a disagreement between them rather than as a shifted day.
    """
    seed, auth = await seeded_client(client, db_session)
    zone = await _database_zone(db_session)
    project = await seed.project()
    await _session_at(
        seed, start=_local_instant(zone, DAY, 23, 59), minutes=30, project_id=project.id
    )
    await _session_at(
        seed,
        start=_local_instant(zone, DAY + timedelta(days=1), 0, 1),
        minutes=45,
        project_id=project.id,
    )

    await _rebuild(client, auth, start=DAY, end=DAY + timedelta(days=1))

    # A window that straddles the boundary holds both days, each with its own
    # block: 23:59 is a minute inside the first day and 00:01 a minute inside
    # the second, so the cut is at midnight rather than at an hour.
    straddling = _by_day(await _series(client, auth, start=DAY, end=DAY + timedelta(days=1)))
    assert list(straddling) == [DAY.isoformat(), (DAY + timedelta(days=1)).isoformat()]
    assert _counters(straddling[DAY.isoformat()]) == {
        "tasks_created": 0,
        "tasks_completed": 0,
        "tasks_overdue": 0,
        "tasks_cancelled": 0,
        "tasks_blocked": 0,
        "tasks_rescheduled": 0,
        "planned_minutes": 30,
        "actual_minutes": 30,
        "work_sessions": 1,
        "calendar_events": 0,
        "knowledge_events": 0,
        "projects_touched": 0,
    }
    assert _counters(straddling[(DAY + timedelta(days=1)).isoformat()]) == {
        "tasks_created": 0,
        "tasks_completed": 0,
        "tasks_overdue": 0,
        "tasks_cancelled": 0,
        "tasks_blocked": 0,
        "tasks_rescheduled": 0,
        "planned_minutes": 45,
        "actual_minutes": 45,
        "work_sessions": 1,
        "calendar_events": 0,
        "knowledge_events": 0,
        "projects_touched": 0,
    }

    # A window that ends before the boundary sees only the first block.
    first_only = await _series(client, auth, start=DAY, end=DAY)
    assert [row["metric_date"] for row in first_only] == [DAY.isoformat()]
    assert first_only[0]["actual_minutes"] == 30
    assert first_only[0]["work_sessions"] == 1

    # A window that starts after it sees only the second.
    second_only = await _series(
        client, auth, start=DAY + timedelta(days=1), end=DAY + timedelta(days=1)
    )
    assert second_only[0]["actual_minutes"] == 45
    assert second_only[0]["work_sessions"] == 1

    overview = await client.get(
        "/api/v1/analytics/overview",
        params={"start_date": DAY.isoformat(), "end_date": (DAY + timedelta(days=1)).isoformat()},
        headers=auth,
    )
    assert overview.status_code == 200, overview.text
    totals = {point["label"]: point["current"] for point in overview.json()["totals"]}
    assert totals["actual_minutes"] == 75.0
    assert totals["planned_minutes"] == 75.0
    assert totals["work_sessions"] == 2.0

    trends = await client.get(
        "/api/v1/analytics/trends",
        params={
            "start_date": DAY.isoformat(),
            "end_date": (DAY + timedelta(days=1)).isoformat(),
            "metric": "actual_minutes",
        },
        headers=auth,
    )
    assert [(point["bucket"], point["value"]) for point in trends.json()] == [
        (DAY.isoformat(), 30.0),
        ((DAY + timedelta(days=1)).isoformat(), 45.0),
    ]


async def test_a_block_that_runs_past_midnight_is_counted_whole_on_the_day_it_started(
    client, db_session
):
    """A 23:00 block that ends at 00:30 is ninety minutes, all of them on the first day.

    The one place a day cut could plausibly be expected to split a figure, and
    it deliberately does not. ``session_minutes_by_day`` buckets on
    ``actual_start`` and sums the row's own ``actual_minutes``, so a block is
    attributed to the day it began and is never halved across two rows. Splitting
    it would also make the daily series stop summing back into the totals
    ``/overview`` reports, which is the reconciliation the aggregate tier exists
    to preserve.

    The block starts at 23:00 **in the server's own zone**, so it genuinely runs
    past that calendar's midnight. Seeded at 23:00 UTC it would not: in
    ``Asia/Calcutta`` that is half past four the next morning, and the block
    would already have started on the following day.
    """
    seed, auth = await seeded_client(client, db_session)
    zone = await _database_zone(db_session)
    project = await seed.project()
    await _session_at(
        seed, start=_local_instant(zone, DAY, 23, 0), minutes=90, project_id=project.id
    )
    await _rebuild(client, auth, start=DAY, end=DAY + timedelta(days=1))

    rows = _by_day(await _series(client, auth, start=DAY, end=DAY + timedelta(days=1)))
    assert rows[DAY.isoformat()]["actual_minutes"] == 90
    assert rows[DAY.isoformat()]["work_sessions"] == 1
    assert rows[(DAY + timedelta(days=1)).isoformat()]["actual_minutes"] == 0
    assert rows[(DAY + timedelta(days=1)).isoformat()]["work_sessions"] == 0

    time_view = await client.get(
        "/api/v1/analytics/time",
        params={"start_date": DAY.isoformat(), "end_date": (DAY + timedelta(days=1)).isoformat()},
        headers=auth,
    )
    assert time_view.json()["total_minutes"] == 90


# ---------------------------------------------------------------------------
# Cancelled and incomplete work
# ---------------------------------------------------------------------------


async def test_a_cancelled_task_is_never_counted_as_completed(client, db_session):
    """One finished, one open, one cancelled — and only one of them is a completion.

    ``cancelled`` is a real status and is neither completed nor open. Getting
    that wrong is not a rounding error: a user who abandoned a fifth of their
    backlog would be told they finished it, and every rate derived from the
    distinction — completion rate, open workload, the overdue backlog — would be
    wrong with them.

    The counters, from the rows: on :data:`DAY` three tasks were created, one was
    completed, one was cancelled (dated by ``updated_at``, the only cancellation
    evidence the schema has) and one is still ``todo``. Nothing is overdue,
    because the four dated tasks either finished on their due date or have no
    due date at all.
    """
    seed, auth = await seeded_client(client, db_session)
    project = await seed.project()
    await seed.task(
        project_id=project.id,
        status=TaskStatus.CANCELLED.value,
        created_at=at(DAY),
        updated_at=at(DAY, 11),
    )
    await seed.completed_task(day=DAY, project_id=project.id)
    await seed.task(project_id=project.id, created_at=at(DAY), due_date=FAR_DUE)
    await _rebuild(client, auth)

    rows = _by_day(await _series(client, auth, start=WINDOW_START, end=WINDOW_END))
    assert _counters(rows[DAY.isoformat()]) == {
        "tasks_created": 3,
        "tasks_completed": 1,
        "tasks_overdue": 0,
        "tasks_cancelled": 1,
        "tasks_blocked": 0,
        "tasks_rescheduled": 0,
        "planned_minutes": 0,
        "actual_minutes": 0,
        "work_sessions": 0,
        "calendar_events": 0,
        "knowledge_events": 0,
        "projects_touched": 0,
    }

    tasks = await _get(client, auth, "/api/v1/analytics/tasks")
    assert tasks["total_tasks"] == 3
    assert tasks["completed_tasks"] == 1
    # The cancelled task is not open either, so it is in neither bucket.
    assert tasks["open_tasks"] == 1
    assert tasks["cancelled_tasks"] == 1
    assert tasks["completion_rate"] == 33.3333
    assert tasks["by_status"]["cancelled"] == 1
    assert tasks["by_status"]["completed"] == 1
    assert tasks["by_status"]["todo"] == 1
    assert tasks["by_status"]["total"] == 3

    deadlines = await _get(client, auth, "/api/v1/analytics/deadlines")
    assert deadlines["available"] is True
    assert deadlines["on_time"] == 1
    assert deadlines["late"] == 0
    assert deadlines["adherence_rate"] == 100.0

    workload = await _get(client, auth, "/api/v1/analytics/workload")
    assert workload["open_tasks"] == 1
    assert workload["status_counts"]["cancelled"] == 1


async def test_a_cancelled_task_is_not_an_overdue_commitment(client, db_session):
    """A task the user deliberately dropped does not sit in the overdue backlog.

    This is the rule the repository states about ``overdue_count_as_of`` and the
    one the "cancelled tasks" item in the brief's data-quality list is about: a
    cancelled task is not work that was missed, and counting it would make a
    cleaned-up backlog look worse than a neglected one.

    Both halves are asserted, because "the cancelled task is absent" on its own
    is satisfied by a fixture that put nothing overdue in the first place. Two
    tasks with the same long-past due date are created — one cancelled, one left
    ``todo`` — and exactly one of them is counted.
    """
    seed, auth = await seeded_client(client, db_session)
    project = await seed.project()
    await seed.task(
        project_id=project.id,
        status=TaskStatus.CANCELLED.value,
        created_at=at(DAY - timedelta(days=10)),
        updated_at=at(DAY - timedelta(days=5)),
        due_date=LONG_PAST,
    )
    forgotten = await seed.task(
        project_id=project.id, created_at=at(DAY - timedelta(days=10)), due_date=LONG_PAST
    )
    await _rebuild(client, auth)

    tasks = await _get(client, auth, "/api/v1/analytics/tasks")
    # The control: one open, long-overdue task really is in the count.
    assert tasks["overdue_tasks"] == 1
    assert [row["task_id"] for row in tasks["top_overdue"]] == [str(forgotten.id)]
    assert tasks["top_overdue"][0]["due_date"] == LONG_PAST.isoformat()

    workload = await _get(client, auth, "/api/v1/analytics/workload")
    assert workload["overdue_open"] == 1
    assert workload["overdue_tasks"] == 1
    assert workload["open_tasks"] == 1

    deadlines = await _get(client, auth, "/api/v1/analytics/deadlines")
    # Still overdue is reported beside adherence and deliberately does not enter
    # its denominator, so a task due next month is not a missed deadline.
    assert deadlines["still_overdue"] == 1
    assert deadlines["on_time"] == 0
    assert deadlines["late"] == 0
    assert deadlines["available"] is False
    assert deadlines["adherence_rate"] is None
    assert deadlines["total_considered"] == 1

    # One open task out of two created, the cancelled one in neither bucket.
    assert tasks["open_tasks"] == 1
    assert tasks["total_tasks"] == 2
    assert tasks["cancelled_tasks"] == 1


async def test_an_in_progress_task_with_tracked_time_still_counts_that_time(client, db_session):
    """Unfinished is not unworked: 90 recorded minutes on a task still in progress.

    Incompleteness is a state, not a stopwatch. A task that is still
    ``in_progress`` has work against it, and every figure derived from work
    sessions must include it — otherwise stopping a task half-way would erase the
    half-hour the user actually spent, which is precisely the "incomplete tasks"
    case the brief lists under data quality.

    Seeded: one ``in_progress`` task, due far in the future so it is not overdue,
    carrying ``actual_minutes = 90``, with one 90-minute completed session run
    against it on :data:`DAY`. The same 90 has to come out of the daily
    aggregate, the overview total, the time distribution, the workload, the
    per-project roll-up and the feature vector, or the six views of one afternoon
    disagree with each other.
    """
    seed, auth = await seeded_client(client, db_session)
    project = await seed.project()
    task = await seed.task(
        project_id=project.id,
        status=TaskStatus.IN_PROGRESS.value,
        created_at=at(DAY),
        due_date=FAR_DUE,
        estimated_minutes=60,
        actual_minutes=90,
    )
    await seed.work_session(day=DAY, minutes=90, project_id=project.id, task_id=task.id)
    await _rebuild(client, auth)

    rows = _by_day(await _series(client, auth, start=DAY, end=DAY + timedelta(days=1)))
    assert rows[DAY.isoformat()]["actual_minutes"] == 90
    assert rows[DAY.isoformat()]["planned_minutes"] == 90
    assert rows[DAY.isoformat()]["work_sessions"] == 1
    assert rows[DAY.isoformat()]["tasks_created"] == 1
    assert rows[DAY.isoformat()]["tasks_completed"] == 0

    overview = await _get(client, auth, "/api/v1/analytics/overview")
    totals = {point["label"]: point["current"] for point in overview["totals"]}
    assert totals["actual_minutes"] == 90.0
    assert totals["planned_minutes"] == 90.0
    assert totals["work_sessions"] == 1.0
    assert totals["tasks_created"] == 1.0
    assert totals["tasks_completed"] == 0.0

    time_view = await _get(client, auth, "/api/v1/analytics/time")
    assert time_view["total_minutes"] == 90
    assert [
        (bucket["key"], bucket["minutes"], bucket["share"]) for bucket in time_view["by_project"]
    ] == [(str(project.id), 90, 100.0)]
    assert [bucket["key"] for bucket in time_view["by_task"]] == [str(task.id)]

    workload = await _get(client, auth, "/api/v1/analytics/workload")
    assert workload["actual_minutes"] == 90
    assert workload["scheduled_minutes"] == 90
    assert workload["open_tasks"] == 1
    assert workload["overdue_open"] == 0

    tasks = await _get(client, auth, "/api/v1/analytics/tasks")
    assert tasks["open_tasks"] == 1
    assert tasks["completed_tasks"] == 0
    # Work entered the system and none of it was finished, so the rate is a
    # real 0% rather than an undefined one.
    assert tasks["completion_rate"] == 0.0
    assert tasks["overdue_rate"] == 0.0

    projects = (await _get(client, auth, "/api/v1/analytics/projects"))["items"]
    entry = projects[0]
    assert entry["total_tasks"] == 1
    assert entry["completed_tasks"] == 0
    assert entry["remaining_tasks"] == 1
    assert entry["available"] is True
    assert entry["total_work_minutes"] == 90
    assert entry["work_minutes"] == 90
    # Nothing has been finished, so the per-completion average has no
    # denominator; the per-task average is over the one task and is 90.
    assert entry["avg_task_actual_minutes"] is None
    assert entry["avg_task_minutes"] == 90.0

    vector = await _snapshot(db_session, client, auth, task.id)
    assert vector["actual_minutes"] == 90
    assert vector["work_session_count"] == 1
    assert vector["recent_work_minutes"] == 90
    assert vector["project_open_task_count"] == 1
    assert vector["estimated_minutes"] == 60


async def test_task_minutes_with_no_session_are_never_counted_as_worked_time(client, db_session):
    """The other side of the case above: a running total nobody ever timed.

    ``tasks.actual_minutes`` is ``NOT NULL`` with a zero default, so it cannot
    distinguish "never tracked" from "tracked as zero" — an ambiguity
    ``app/models/task.py`` documents. The service resolves it in one direction:
    time *worked* is read from ``work_sessions``, never from the task's running
    total, so a task carrying 90 minutes with no session behind it contributes
    nothing to any worked-time figure.

    Two figures report the column anyway, and both are pinned here because the
    distinction is the whole point:

    * ``/projects`` reports ``actual_minutes`` (summed from the task rows) beside
      ``work_minutes`` (summed from the sessions) precisely so a discrepancy
      between what a task accumulated and what was demonstrably spent stays
      visible instead of hiding inside one number;
    * the ML feature vector reports ``actual_minutes`` as ``None``, because a
      zero there is a training signal ("this task took no time") and a
      fabricated one is indistinguishable from an observation once it is inside
      a feature matrix. ``None`` is the absence of an observation, which an
      imputer can handle deliberately.
    """
    seed, auth = await seeded_client(client, db_session)
    project = await seed.project()
    task = await seed.task(
        project_id=project.id,
        status=TaskStatus.IN_PROGRESS.value,
        created_at=at(DAY),
        due_date=FAR_DUE,
        actual_minutes=90,
    )
    await _rebuild(client, auth)

    overview = await _get(client, auth, "/api/v1/analytics/overview")
    totals = {point["label"]: point["current"] for point in overview["totals"]}
    assert totals["actual_minutes"] == 0.0
    assert totals["planned_minutes"] == 0.0
    assert totals["work_sessions"] == 0.0

    time_view = await _get(client, auth, "/api/v1/analytics/time")
    assert time_view["available"] is False
    assert time_view["total_minutes"] == 0

    workload = await _get(client, auth, "/api/v1/analytics/workload")
    assert workload["available"] is False
    assert workload["actual_minutes"] == 0
    assert workload["scheduled_minutes"] == 0

    focus = await _get(client, auth, "/api/v1/analytics/focus")
    assert focus["available"] is False
    assert focus["total_minutes"] == 0

    projects = (await _get(client, auth, "/api/v1/analytics/projects"))["items"]
    entry = projects[0]
    # The task's own total, and the time demonstrably spent, side by side.
    assert entry["actual_minutes"] == 90
    assert entry["work_minutes"] == 0
    assert entry["total_work_minutes"] == 0

    vector = await _snapshot(db_session, client, auth, task.id)
    assert vector["actual_minutes"] is None
    assert vector["recent_work_minutes"] is None
    assert vector["work_session_count"] == 0


# ---------------------------------------------------------------------------
# Deleted entities
# ---------------------------------------------------------------------------


async def test_deleting_a_project_mid_window_leaves_no_orphans_and_no_server_error(
    client, db_session
):
    """A project, its tasks and its sessions deleted mid-window; the rest still adds up.

    Deleting a project is a hard delete that cascades to its tasks and their work
    sessions, while ``activity_events`` keeps the record of what happened inside
    it with a null reference. That mixture is exactly where an analytics surface
    breaks: a figure summed from a table whose rows vanished, or a bucket keyed on
    a project id that no longer resolves to a name.

    Both halves are asserted. **No server error**: every route is called after the
    delete and must still answer. **No orphan counts**: the routes that read the
    source tables directly report only the surviving project, and after an
    explicit rebuild the stored aggregates drop to the survivor's figures.

    The activity event is the one row that outlives the project, and the
    consistency score still counts its day — the record of the work survives the
    container, which is what ``DELETE /projects/{id}`` documents. What does *not*
    survive is the project's reference on it, so ``projects_touched`` falls to
    zero: the counter is a distinct count of project ids over the feed, and a
    null id is not a project.
    """
    seed, auth = await seeded_client(client, db_session)
    doomed = await seed.project(name="Doomed")
    kept = await seed.project(name="Kept")

    finished = await seed.completed_task(day=DAY, project_id=doomed.id)
    await seed.task(project_id=doomed.id, created_at=at(DAY), due_date=FAR_DUE)
    await seed.task(project_id=kept.id, created_at=at(DAY), due_date=FAR_DUE)
    await seed.work_session(day=DAY, minutes=60, project_id=doomed.id, task_id=finished.id)
    await seed.work_session(day=DAY, minutes=30, project_id=kept.id)
    await seed.activity(
        ActivityEvent.TASK_STARTED, day=DAY, project_id=doomed.id, task_id=finished.id
    )

    window = {"start_date": DAY.isoformat(), "end_date": (DAY + timedelta(days=1)).isoformat()}
    await _rebuild(client, auth, start=DAY, end=DAY + timedelta(days=1))

    # Before the delete the window really does carry both projects, so the
    # assertions after it cannot pass because the fixture was empty.
    before = await client.get("/api/v1/analytics/projects", params=window, headers=auth)
    assert {entry["name"] for entry in before.json()["items"]} == {"Doomed", "Kept"}
    before_totals = await client.get("/api/v1/analytics/overview", params=window, headers=auth)
    before_actual = {point["label"]: point["current"] for point in before_totals.json()["totals"]}
    assert before_actual["actual_minutes"] == 90.0
    assert before_actual["work_sessions"] == 2.0

    deleted = await client.delete(f"/api/v1/projects/{doomed.id}", headers=auth)
    assert deleted.status_code == 204, deleted.text

    # Nothing on the surface falls over, on a dangling reference.
    for method, path in WINDOW_ROUTES:
        response = await client.request(method, path, params=window, headers=auth)
        assert response.status_code in (200, 202), f"{path}: {response.text}"

    projects = await client.get("/api/v1/analytics/projects", params=window, headers=auth)
    assert [entry["project_id"] for entry in projects.json()["items"]] == [str(kept.id)]
    assert projects.json()["items"][0]["name"] == "Kept"
    assert projects.json()["items"][0]["total_tasks"] == 1

    tasks = await client.get("/api/v1/analytics/tasks", params=window, headers=auth)
    assert tasks.json()["total_tasks"] == 1
    assert tasks.json()["by_status"]["total"] == 1
    assert tasks.json()["completed_tasks"] == 0
    assert tasks.json()["top_overdue"] == []

    time_view = await client.get("/api/v1/analytics/time", params=window, headers=auth)
    assert time_view.json()["total_minutes"] == 30
    assert [bucket["key"] for bucket in time_view.json()["by_project"]] == [str(kept.id)]

    export = await client.get(
        "/api/v1/analytics/export.csv",
        params={**window, "dataset": "task_performance"},
        headers=auth,
    )
    assert export.status_code == 200, export.text
    assert export.headers["X-Nexus-Row-Count"] == "1"
    assert "Doomed" not in export.text
    assert str(finished.id) not in export.text

    sessions_csv = await client.get(
        "/api/v1/analytics/export.csv",
        params={**window, "dataset": "work_sessions"},
        headers=auth,
    )
    assert sessions_csv.status_code == 200, sessions_csv.text
    assert sessions_csv.headers["X-Nexus-Row-Count"] == "1"
    assert str(doomed.id) not in sessions_csv.text

    # The stored aggregates are a snapshot of what was true when they were
    # written, so a recompute is what brings them back in step with the rows.
    await _rebuild(client, auth, start=DAY, end=DAY + timedelta(days=1))

    after = await client.get("/api/v1/analytics/overview", params=window, headers=auth)
    assert after.status_code == 200, after.text
    totals = {point["label"]: point["current"] for point in after.json()["totals"]}
    assert totals["actual_minutes"] == 30.0
    assert totals["work_sessions"] == 1.0
    assert totals["tasks_created"] == 1.0
    assert totals["tasks_completed"] == 0.0
    assert totals["projects_touched"] == 0.0
    assert after.json()["stale"] is False

    # The activity row the delete route promised to keep is still there, so the
    # day is still an active day — the record outlived the project.
    consistency = await client.get("/api/v1/analytics/consistency", params=window, headers=auth)
    assert consistency.json()["available"] is True
    assert consistency.json()["active_days"] == 1
    assert consistency.json()["work_sessions"] == 1


async def test_deleting_a_task_removes_it_from_every_figure_without_a_server_error(
    client, db_session
):
    """A finished task with an estimate deleted; every figure re-derives around it.

    The narrower half of "deleted entities". Deleting a task cascades to the work
    sessions run against it and nulls the reference on its activity rows, so the
    estimation sample, the tracked minutes, the task counts and the feature
    vector all have to lose it together — and the feature vector for the id that
    was deleted has to be a 404 rather than a vector computed against a row that
    no longer exists.

    Seeded: one task completed on :data:`DAY` estimated at 60 and taken 90 (so
    the single estimation pair is an absolute error of 30.0 and a bias of -30.0),
    one open task, and a 45-minute session against the first.

    The **actual** half of the pair is the 45 recorded session minutes, not the
    ``tasks.actual_minutes`` column's 90. ``tasks.actual_minutes`` is a cache of
    the observation that the session write path does not maintain, and
    ``completed_pairs_in_range`` reads the observation: an estimate of 60 against
    45 minutes actually spent is an absolute error of **15.0** and — signed
    ``estimated - actual`` — a bias of **+15.0**, i.e. an over-estimation rate
    of 100% and an under-estimation rate of 0%. The distinction is the point:
    a figure that read the stale column would be measuring something no user ever
    recorded, and would keep doing so on every read.
    """
    seed, auth = await seeded_client(client, db_session)
    project = await seed.project(name="Kept")
    finished = await seed.completed_task(
        day=DAY, project_id=project.id, estimated_minutes=60, actual_minutes=90
    )
    await seed.task(project_id=project.id, created_at=at(DAY), due_date=FAR_DUE)
    await seed.work_session(day=DAY, minutes=45, project_id=project.id, task_id=finished.id)
    await _rebuild(client, auth)

    before = await _get(client, auth, "/api/v1/analytics/estimation")
    assert before["available"] is True
    assert before["sample_count"] == 1
    assert before["pairs_compared"] == 1
    # (estimated 60, recorded 45): signed ``estimated - actual`` = +15.
    assert before["absolute_error"] == 15.0
    assert before["bias"] == 15.0
    # The sign convention is pinned beside the figure it explains: a positive
    # bias next to a 0% under-estimation rate, never beside a 100%.
    assert before["under_estimation_rate"] == 0.0
    assert before["over_estimation_rate"] == 100.0

    deleted = await client.delete(f"/api/v1/tasks/{finished.id}", headers=auth)
    assert deleted.status_code == 204, deleted.text

    after = await _get(client, auth, "/api/v1/analytics/estimation")
    assert after["available"] is False
    assert after["sample_count"] == 0
    assert after["absolute_error"] is None
    assert after["bias"] is None
    assert after["reason_if_unavailable"].startswith(NOT_ENOUGH_ACTIVITY)

    tasks = await _get(client, auth, "/api/v1/analytics/tasks")
    assert tasks["total_tasks"] == 1
    assert tasks["completed_tasks"] == 0
    assert tasks["avg_cycle_minutes"] is None
    assert tasks["avg_estimate_error_minutes"] is None

    time_view = await _get(client, auth, "/api/v1/analytics/time")
    assert time_view["available"] is False
    assert time_view["total_minutes"] == 0

    projects = (await _get(client, auth, "/api/v1/analytics/projects"))["items"]
    assert projects[0]["total_tasks"] == 1
    assert projects[0]["work_minutes"] == 0

    for method, path in WINDOW_ROUTES:
        response = await client.request(method, path, params=WINDOW, headers=auth)
        assert response.status_code in (200, 202), f"{path}: {response.text}"

    snapshot = await client.get(
        "/api/v1/analytics/feature-snapshot", params={"task_id": str(finished.id)}, headers=auth
    )
    assert snapshot.status_code == 404, snapshot.text
    assert snapshot.json()["error"]["code"] == "not_found"
    assert snapshot.json()["error"]["message"] == "That task does not exist."


# ---------------------------------------------------------------------------
# Date range validation
# ---------------------------------------------------------------------------


async def test_every_window_taking_endpoint_refuses_an_inverted_window(client, db_session):
    """All sixteen, with ``end_date`` before ``start_date``.

    A reversed range produces an empty series that reads exactly like "you did
    nothing" — which is the failure the window resolver exists to prevent, and the
    one a response could not afterwards distinguish from real data. So the guard
    is asserted at the edge, on every route that takes a window, and the *message*
    is pinned: a client that has to parse prose to tell which end of the window is
    wrong is a client that will get it wrong.

    ``/export`` and ``/feature-snapshot`` are the two routes that take no window
    and so cannot refuse one; the other sixteen all can, and all do.
    """
    _seed, auth = await seeded_client(client, db_session)
    inverted = {
        "start_date": WINDOW_END.isoformat(),
        "end_date": WINDOW_START.isoformat(),
    }

    for method, path in WINDOW_ROUTES:
        response = await client.request(method, path, params=inverted, headers=auth)
        assert response.status_code == 422, f"{path}: {response.text}"
        error = response.json()["error"]
        assert error["code"] == "validation_error", path
        assert error["message"] == "end_date must not be earlier than start_date.", path


async def test_every_window_taking_endpoint_refuses_a_window_over_the_ceiling(
    client, db_session, settings, assert_error_envelope
):
    """All sixteen, with a window wider than ``ANALYTICS_MAX_RANGE_DAYS``.

    An unbounded analytics read is the one shape this system's indexes cannot
    serve: every aggregate beneath it scans the owner's whole history. The limit
    is read from the settings rather than spelled out, so the refusal and the
    configuration cannot drift apart, and the message names the number so a
    client can show it to the user rather than "invalid range".

    The window used is six years, which is over both ceilings (366 days to read,
    180 to write), so ``/rebuild`` is caught by the same guard and nothing is
    written.
    """
    _seed, auth = await seeded_client(client, db_session)
    oversized = {"start_date": "2020-01-01", "end_date": "2026-01-01"}

    for method, path in WINDOW_ROUTES:
        response = await client.request(method, path, params=oversized, headers=auth)
        error = assert_error_envelope(response, status_code=422, code="validation_error")
        assert error["message"] == (
            f"The analytics window may span at most {settings.analytics_max_range_days} days."
        ), path


async def test_a_malformed_date_is_refused_rather_than_defaulted(
    client, db_session, assert_error_envelope
):
    """``?start_date=not-a-date`` and an impossible calendar date, on all sixteen.

    The dangerous outcome here is not a 500. It is answering over the *default*
    window: the caller would be shown a real number computed over a period they
    never asked for, and the only clue would be the ``range`` echoed back inside a
    body they did not think to check. So the refusal is pinned down to the
    offending parameter — ``query.start_date``, ``query.end_date`` — on every
    route that takes one.

    ``2026-13-45`` is used for the unparseable-shaped end date because it looks
    like a date, is well-formed as a string, and is neither a month nor a day.
    """
    _seed, auth = await seeded_client(client, db_session)

    for method, path in WINDOW_ROUTES:
        response = await client.request(
            method, path, params={**WINDOW, "start_date": "not-a-date"}, headers=auth
        )
        error = assert_error_envelope(response, status_code=422, code="validation_error")
        assert [entry["field"] for entry in error["details"]["errors"]] == ["query.start_date"], (
            path
        )

        response = await client.request(
            method, path, params={**WINDOW, "end_date": "2026-13-45"}, headers=auth
        )
        error = assert_error_envelope(response, status_code=422, code="validation_error")
        assert [entry["field"] for entry in error["details"]["errors"]] == ["query.end_date"], path


async def test_an_unknown_granularity_is_refused_on_both_time_series_routes(
    client, db_session, assert_error_envelope
):
    """``fortnight`` is a 422 on ``/series`` and on ``/trends``, and names the allowlist.

    A granularity that fell back to a default would plot a plausible line for a
    question the caller did not ask, and the caller would have no way to tell.
    There are exactly three bucket sizes — the aggregate table stores one row per
    day, so a week or a month is a *bucket* of those rows and not a second table —
    and the message says so.

    Both routes are checked because they validate the parameter separately: each
    calls ``_check_granularity`` itself rather than sharing a dependency, so a
    guard added to one would not reach the other.
    """
    _seed, auth = await seeded_client(client, db_session)
    expected = "Unsupported granularity 'fortnight'; expected one of day, week, month."

    for path in ("/api/v1/analytics/series", "/api/v1/analytics/trends"):
        response = await client.get(
            path, params={**WINDOW, "granularity": "fortnight"}, headers=auth
        )
        error = assert_error_envelope(response, status_code=422, code="validation_error")
        assert error["message"] == expected, path

        # The control: the three documented values are accepted, so the
        # assertion above is a refusal and not a route that rejects everything.
        for granularity in ("day", "week", "month"):
            allowed = await client.get(
                path, params={**WINDOW, "granularity": granularity}, headers=auth
            )
            assert allowed.status_code == 200, f"{path}?granularity={granularity}"


# ---------------------------------------------------------------------------
# Analytics refresh
# ---------------------------------------------------------------------------


async def test_a_rebuild_reports_the_days_it_wrote_and_repeating_it_changes_nothing(
    client, db_session
):
    """``rows_written`` is the inclusive day count, and a second run is a no-op.

    ``POST /rebuild`` is 202 rather than 200 because it is a bounded write a
    client reports on ("rebuilt 7 days"), and the number it reports is the number
    of daily rows written — **one per day in the window, whether or not that day
    had anything on it**. A rebuild that only wrote the days it found something
    for would return 2 here and leave a series with holes, which is a state a
    client has to infer rather than be told.

    The idempotency half is asserted on the stored rows rather than on the
    response, because a rebuild that appended a second row for every day it
    covered would report exactly the same number either time. The identity of
    each row's primary key is checked as well as its values: ``ON CONFLICT DO
    UPDATE`` replaces the existing row, so the ids are stable, whereas an
    insert-then-append would mint a second id per day per run.
    """
    seed, auth = await seeded_client(client, db_session)
    project = await seed.project()
    await seed.task(project_id=project.id, created_at=at(DAY))
    await seed.completed_task(day=DAY + timedelta(days=2), project_id=project.id)

    first = await _rebuild(client, auth)
    assert first == {"rows_written": WINDOW_DAYS}

    result = await db_session.execute(
        select(DailyMetric.id, DailyMetric.metric_date, DailyMetric.tasks_created)
        .where(DailyMetric.user_id == seed.owner.id)
        .order_by(DailyMetric.metric_date.asc())
    )
    stored_first = [(row[0], row[1], row[2]) for row in result.all()]
    assert len(stored_first) == WINDOW_DAYS
    assert [row[1] for row in stored_first] == [
        WINDOW_START + timedelta(days=offset) for offset in range(WINDOW_DAYS)
    ]
    created_by_day = {row[1]: row[2] for row in stored_first}
    assert created_by_day[DAY] == 1
    assert created_by_day[DAY + timedelta(days=2)] == 1

    second = await _rebuild(client, auth)
    assert second == first

    result = await db_session.execute(
        select(DailyMetric.id, DailyMetric.metric_date, DailyMetric.tasks_created)
        .where(DailyMetric.user_id == seed.owner.id)
        .order_by(DailyMetric.metric_date.asc())
    )
    assert [(row[0], row[1], row[2]) for row in result.all()] == stored_first

    rows = await _series(client, auth, start=WINDOW_START, end=WINDOW_END)
    assert len(rows) == WINDOW_DAYS
    assert [row["metric_date"] for row in rows] == [
        (WINDOW_START + timedelta(days=offset)).isoformat() for offset in range(WINDOW_DAYS)
    ]
    assert sum(row["tasks_created"] for row in rows) == 2
    assert sum(row["tasks_completed"] for row in rows) == 1

    overview = await _get(client, auth, "/api/v1/analytics/overview")
    assert overview["stale"] is False
    assert overview["is_stale"] is False
    assert overview["aggregates_through"] == WINDOW_END.isoformat()


async def test_a_rebuild_advances_the_freshness_stamp_it_stores(client, db_session):
    """Recomputing a day moves its ``updated_at`` — the signal the banner renders.

    The brief's refresh requirement is "do not silently show stale numbers without
    indication", and the indicator on this surface is the timestamp of the last
    recompute. Two things have to be true for it to mean anything, and both are
    asserted here:

    * **the stamp moves on a recompute.** ``upsert_many`` writes
      ``updated_at = now()`` into the ``set_`` mapping of the ``ON CONFLICT DO
      UPDATE``; without that the model's ``onupdate=func.now()`` — a Core
      construct SQLAlchemy applies only to statements it generates itself — would
      never fire for a Core upsert, and every day would carry the timestamp of
      the first rebuild it ever had;
    * **the recompute is a *re*write, not a no-op.** The stamp is moved into the
      distant past first rather than simply compared across two rebuilds, because
      two rebuilds can land inside the same clock tick and a test that fails only
      on a fast machine is a test that gets deleted.

    Read as columns, not as ORM entities: this session wrote the rows, so an
    entity read would return the copy cached in the identity map and the
    comparison would be a stale object against itself.
    """
    seed, auth = await seeded_client(client, db_session)
    project = await seed.project()
    await seed.task(project_id=project.id, created_at=at(DAY))
    await _rebuild(client, auth, start=DAY, end=DAY)

    first = await _stored_stamps(db_session, seed.owner.id, start=DAY, end=DAY)
    assert len(first) == 1
    assert first[0][0] == DAY
    # ``astimezone``, not ``replace(tzinfo=UTC)``: the stamp comes back aware and
    # labelled with the connection's ``TimeZone`` (Asia/Calcutta on this server),
    # so ``replace`` would re-read the local wall clock as if it were UTC and shift
    # the instant by the offset. ``replace`` and ``astimezone`` agree on any server
    # running UTC, which is why this only ever failed here.
    assert first[0][1].astimezone(UTC) > datetime(2020, 1, 1, tzinfo=UTC)

    aged = datetime(2020, 1, 1, tzinfo=UTC)
    await db_session.execute(
        text("UPDATE daily_metrics SET updated_at = :stamp WHERE user_id = :user_id"),
        {"stamp": aged, "user_id": seed.owner.id},
    )
    await db_session.commit()
    assert (await _stored_stamps(db_session, seed.owner.id, start=DAY, end=DAY))[0][1].astimezone(
        UTC
    ) == aged

    await _rebuild(client, auth, start=DAY, end=DAY)

    refreshed = await _stored_stamps(db_session, seed.owner.id, start=DAY, end=DAY)
    assert refreshed[0][1].astimezone(UTC) > aged


async def test_a_partially_rebuilt_window_reports_itself_as_stale(client, db_session):
    """Three days rebuilt out of six is reported as incomplete, not rendered as a whole.

    ``OverviewRead.stale`` is the mechanism behind "do not silently show stale
    numbers without indication". A window is stale when it holds *some* aggregate
    rows but not one per day, and the flag is cleared the moment the gap is
    filled — so the two states a client has to tell apart, "partly measured" and
    "fully measured", are both reachable and both asserted here.

    ``aggregates_through`` is asserted alongside it because it is the other half
    of the same answer: the newest day the table holds for this account, which is
    what a client renders as "updated through Monday".
    """
    seed, auth = await seeded_client(client, db_session)
    project = await seed.project()
    await seed.task(project_id=project.id, created_at=at(DAY))

    await _rebuild(client, auth, start=WINDOW_START, end=WINDOW_START + timedelta(days=2))

    partial = await client.get("/api/v1/analytics/overview", params=WINDOW, headers=auth)
    assert partial.status_code == 200, partial.text
    assert partial.json()["stale"] is True
    assert partial.json()["is_stale"] is True
    # `aggregates_through` reads the *stored* table, so it stops where the rebuild
    # stopped: three days in, not the six the request asked about. That is what
    # makes it the other half of the answer — a client can say "measured through
    # Monday" and mean it. It used to read `WINDOW_END` here, because this route
    # filled the remaining three days on its way to answering; that a `GET` wrote
    # three rows nobody asked for was the behaviour, not a rounding of it.
    assert partial.json()["aggregates_through"] == (WINDOW_START + timedelta(days=2)).isoformat()

    await _rebuild(client, auth)

    complete = await client.get("/api/v1/analytics/overview", params=WINDOW, headers=auth)
    assert complete.status_code == 200, complete.text
    assert complete.json()["stale"] is False
    assert complete.json()["is_stale"] is False
    assert complete.json()["aggregates_through"] == WINDOW_END.isoformat()

    # The aggregates themselves are unchanged by being completed: filling a gap
    # recomputes the same rows rather than adding to them.
    rows = await _series(client, auth, start=WINDOW_START, end=WINDOW_END)
    assert len(rows) == WINDOW_DAYS
    assert sum(row["tasks_created"] for row in rows) == 1


async def test_a_window_that_was_never_rebuilt_serves_no_series_rather_than_recomputing(
    client, db_session
):
    """``/series`` is a read of the aggregate table and nothing else.

    A route that re-aggregated on the fly would undo the reason ``daily_metrics``
    exists: a bounded write, done on a schedule, that every dashboard read is
    cheap because of. So the empty answer is asserted here, against the stored
    table as well as through the API — a route that recomputed would report the
    same figures whether or not the rows had ever been written, and would pass a
    test that is meant to catch exactly that.

    The two halves are therefore a *before* and an *after*: before anything is
    aggregated the route serves nothing, and after an explicit rebuild it serves
    exactly the rows the rebuild wrote. Nothing here depends on which endpoint
    happened to be called first, which is the failure mode of asserting a
    "read-only" claim through a set of endpoints that quietly recompute.
    """
    seed, auth = await seeded_client(client, db_session)
    project = await seed.project()
    await seed.task(project_id=project.id, created_at=at(DAY))

    # Read the stored table first: nothing has been aggregated, so there is
    # nothing to serve and the route must not invent any.
    series = await _get(client, auth, "/api/v1/analytics/series")
    trends = await _get(client, auth, "/api/v1/analytics/trends")
    assert series == []
    assert trends == []
    assert await _stored_stamps(db_session, seed.owner.id, start=WINDOW_START, end=WINDOW_END) == []

    export = await client.get(
        "/api/v1/analytics/export.csv",
        params={**WINDOW, "dataset": "daily_metrics"},
        headers=auth,
    )
    assert export.status_code == 200, export.text
    assert export.headers["X-Nexus-Row-Count"] == "0"

    await _rebuild(client, auth)

    rows = await _series(client, auth, start=WINDOW_START, end=WINDOW_END)
    assert len(rows) == WINDOW_DAYS
    assert sum(row["tasks_created"] for row in rows) == 1

    overview = await _get(client, auth, "/api/v1/analytics/overview")
    totals = {point["label"]: point["current"] for point in overview["totals"]}
    assert totals["tasks_created"] == 1.0
    assert len(overview["daily"]) == WINDOW_DAYS
    assert overview["stale"] is False
