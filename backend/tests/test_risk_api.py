"""The Phase 7 surface end to end over HTTP: risks, recommendations, detection.

Fourteen routes across three routers, and the properties worth pinning are mostly
about what must **not** happen. Three of them exist because a plausible edit
passes review and breaks the product quietly:

* ``GET /risks/summary`` is only reachable while it is declared *before*
  ``GET /risks/{risk_id}``. Starlette matches in declaration order and does not
  prefer a literal segment over a parameter, so reversing the two binds the
  string ``summary`` to the path parameter and the dashboard tile answers with a
  422 about a uuid that never was one. The test that catches it is
  :func:`test_the_summary_route_is_not_swallowed_by_the_id_route`, and it pins
  the presence of ``needs_attention`` — a field only the summary handler emits.
* A foreign id is **404, never 403**, on every read and on all seven
  transitions. A 403 confirms the row exists, which turns the endpoint into an
  existence oracle for other people's risk ids. The responses for a foreign id
  and for one nobody ever issued are asserted to carry the same message, so the
  two cases cannot drift apart.
* ``RiskRead.metadata`` on an account-level risk. ``metadata`` is the name
  SQLAlchemy reserves on a declarative class, so both Phase 7 models map the
  column as ``metadata_``; the account-level detectors — the three whose
  ``entity_id`` is null, which cannot be arbitrated by the partial unique index
  and so take the repository's advisory-locked select-then-write path — stored
  ``{}`` while every row-level risk stored correctly.

The counts in the detection tests are derived, not recorded from a run. One
project holding one open, estimated, overdue task produces exactly two risks —
the deadline (100, ``critical``; the scoring module returns 100 by construction
for a passed deadline) and the project roll-up (``low``: one overdue task is
``0.30 x 1/10`` and one remaining task is ``0.10 x 1/20``, so ``round(3.5)``) —
and exactly two suggestions, the deadline-review and the project-review. Every
other detector declines or measures zero, which is why the run summary carries
their reasons and their rows are not written.

**Every test here requires a live PostgreSQL and has NOT been executed against
production data.** They are marked ``integration``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import ActivityLog
from app.models.enums import (
    ActivityEvent,
    RecommendationStatus,
    RecommendationType,
    RiskStatus,
    RiskType,
)
from app.models.risk import Recommendation, Risk
from app.models.user import User
from app.repositories.risk import RiskRepository
from tests.analytics_fixtures import AnalyticsSeed, seeded_client

pytestmark = pytest.mark.integration

#: The fourteen Phase 7 routes as ``(method, path)``. Every gate test walks this
#: table rather than restating a list, so a route added to a router without being
#: added here shows up as a route nobody proved is protected.
ROUTES: tuple[tuple[str, str], ...] = (
    ("GET", "/api/v1/risks"),
    ("GET", "/api/v1/risks/summary"),
    ("GET", "/api/v1/risks/{risk_id}"),
    ("POST", "/api/v1/risks/{risk_id}/acknowledge"),
    ("POST", "/api/v1/risks/{risk_id}/dismiss"),
    ("POST", "/api/v1/risks/{risk_id}/resolve"),
    ("GET", "/api/v1/recommendations"),
    ("GET", "/api/v1/recommendations/{recommendation_id}"),
    ("POST", "/api/v1/recommendations/{recommendation_id}/accept"),
    ("POST", "/api/v1/recommendations/{recommendation_id}/reject"),
    ("POST", "/api/v1/recommendations/{recommendation_id}/complete"),
    ("POST", "/api/v1/recommendations/{recommendation_id}/view"),
    ("POST", "/api/v1/intelligence/evaluate"),
    ("GET", "/api/v1/intelligence/evaluations"),
)

#: ``(method, path)`` for the routes that must answer 404 on a foreign risk id:
#: the read and all three transitions.
RISK_ID_ROUTES: tuple[tuple[str, str], ...] = tuple(
    (method, path) for method, path in ROUTES if "/risks/{risk_id}" in path
)

#: The same for recommendations: the read and all four transitions.
RECOMMENDATION_ID_ROUTES: tuple[tuple[str, str], ...] = tuple(
    (method, path) for method, path in ROUTES if "/recommendations/{recommendation_id}" in path
)

#: The three risk transitions, the status each writes, and the event it appends.
RISK_TRANSITIONS: tuple[tuple[str, str, str], ...] = (
    ("acknowledge", RiskStatus.ACKNOWLEDGED.value, ActivityEvent.RISK_ACKNOWLEDGED.value),
    ("dismiss", RiskStatus.DISMISSED.value, ActivityEvent.RISK_DISMISSED.value),
    ("resolve", RiskStatus.RESOLVED.value, ActivityEvent.RISK_RESOLVED.value),
)

#: The four recommendation transitions, the status each writes, whether it
#: stamps ``responded_at``, and the event it appends. ``responded_at`` is the
#: column that answers "how many suggestions were never answered", so viewing
#: one — which is not an answer — must leave it null.
RECOMMENDATION_TRANSITIONS: tuple[tuple[str, str, bool, str], ...] = (
    (
        "accept",
        RecommendationStatus.ACCEPTED.value,
        True,
        ActivityEvent.RECOMMENDATION_ACCEPTED.value,
    ),
    (
        "reject",
        RecommendationStatus.REJECTED.value,
        True,
        ActivityEvent.RECOMMENDATION_REJECTED.value,
    ),
    (
        "complete",
        RecommendationStatus.COMPLETED.value,
        True,
        ActivityEvent.RECOMMENDATION_COMPLETED.value,
    ),
    ("view", RecommendationStatus.VIEWED.value, False, ActivityEvent.RECOMMENDATION_VIEWED.value),
)

#: The refusal a risk route answers with for an id that is not the caller's. One
#: string for a foreign id and for one nobody ever issued; asserting both are
#: identical is the point, because a route that could separate them is an
#: existence oracle.
RISK_NOT_FOUND_MESSAGE = "That risk does not exist."

#: The same for recommendations — on the read route.
RECOMMENDATION_NOT_FOUND_MESSAGE = "That recommendation does not exist."

#: ...and on the four transitions, which used to be a *different* string for the
#: same condition: the router defined one for its own ``GET`` while the
#: transitions delegated to :class:`RecommendationService`, which raised its own.
#: Neither leaked an existence oracle, but the same condition answering in two
#: sentences on two routes of one resource is a contract a client cannot hold
#: onto, and the router's docstring claimed otherwise. They are now one constant.
RECOMMENDATION_TRANSITION_NOT_FOUND_MESSAGE = RECOMMENDATION_NOT_FOUND_MESSAGE

#: A role the permission map has never heard of, used for the 403 case. An
#: ordinary account holds ``analytics.read``, so refusing a request has to be a
#: question about a role — and the map is fail-closed, so an unknown role is the
#: honest way to express "does not hold it".
ROLE_WITHOUT_ANALYTICS = "wizard"

#: How far in the past the seeded overdue task's due date sits, and how long ago
#: the row was created. Both are relative to the clock rather than to the
#: :mod:`tests.analytics_fixtures` anchor, and the reason is the window:
#: ``POST /intelligence/evaluate`` evaluates the **last fourteen days**, and the
#: detection service skips its deadline scan outright when the Phase 6 roll-up
#: reports no open tasks — a row created in January would sit outside any window
#: run later in the year and the pass would legitimately find nothing. Two days
#: is unambiguously in the past under any skew between the host clock and the
#: database clock, which is the instant the score is measured from.
OVERDUE_BY = timedelta(days=2)
CREATED_BY = timedelta(days=5)
PROJECT_CREATED_BY = timedelta(days=9)

#: The four bands every count dictionary carries, however the rows happen to
#: fall. The same four words serve ``by_severity`` and ``by_priority`` because
#: priority is derived from severity; spelled once so an "empty" assertion is
#: about the numbers rather than about a comparison that would also pass for a
#: wrong set of keys.
ZERO_BANDS = {"critical": 0, "high": 0, "medium": 0, "low": 0}

#: The ordering fixture, as ``(label, severity, days ago)``. Severity descending
#: first and ``detected_at`` descending within a band, which is only visible
#: because two rows share each of the two worst bands. A plain
#: ``ORDER BY severity DESC`` would put ``medium`` above ``high`` above
#: ``critical`` — and this ordering would catch it.
ORDERING_FIXTURE: tuple[tuple[str, str, float], ...] = (
    ("critical-old", "critical", 6.0),
    ("critical-new", "critical", 2.0),
    ("high-old", "high", 5.0),
    ("high-new", "high", 1.0),
    ("medium", "medium", 0.5),
    ("low", "low", 0.25),
)
ORDERING_EXPECTED = ("critical-new", "critical-old", "high-new", "high-old", "medium", "low")

#: What one detection pass over the seeded overdue task must produce. Derived in
#: the module docstring; stated here as the constants the tests assert.
DETECTION_RISKS_FOUND = 2
DETECTION_RECOMMENDATIONS = 2
DETECTION_BY_SEVERITY = {"critical": 1, "low": 1}
DETECTION_BY_TYPE = {"deadline": 1, "project": 1}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _request(
    client: Any,
    method: str,
    template: str,
    headers: dict[str, str] | None = None,
    *,
    risk_id: uuid.UUID | None = None,
    recommendation_id: uuid.UUID | None = None,
    **kwargs: Any,
):
    """Issue one request against a route template.

    The placeholders are filled with a fresh, never-issued uuid unless the
    caller names a real row, so the gate tests exercise the route the same way a
    caller with no such row would reach it.
    """
    path = template.format(
        risk_id=risk_id or uuid.uuid4(),
        recommendation_id=recommendation_id or uuid.uuid4(),
    )
    return client.request(method, path, headers=headers, **kwargs)


async def _events(
    db_session: AsyncSession, user_id: uuid.UUID, event_type: str
) -> list[ActivityLog]:
    """Every ``activity_events`` row of one kind belonging to one account."""
    result = await db_session.execute(
        select(ActivityLog).where(
            ActivityLog.user_id == user_id, ActivityLog.event_type == event_type
        )
    )
    return list(result.scalars().all())


def _recent(days_ago: float, hour: int = 9) -> datetime:
    """A UTC instant ``days_ago`` before now, on the hour."""
    return (datetime.now(UTC) - timedelta(days=days_ago)).replace(
        hour=hour, minute=0, second=0, microsecond=0
    )


def _default_evidence() -> list[dict[str, Any]]:
    """One evidence line, in the shape ``risks.evidence`` stores.

    The column is an untyped JSONB list, so this is a fixture decision rather
    than a schema constraint — but the wire model reads three keys per line, and
    a seed storing something else would be testing the schema's
    bare-string fallback instead of the route.
    """
    return [
        {
            "label": "Work not scheduled before the deadline",
            "detail": "4h of 4h (100%) has no time booked",
            "contribution": 60.0,
        }
    ]


async def _risk(
    db_session: AsyncSession,
    owner: User,
    *,
    risk_type: str = RiskType.DEADLINE.value,
    severity: str = "high",
    score: int = 60,
    title: str = "Deadline approaching for the Q3 report",
    description: str = "About 4h of the 4h of estimated work has no time booked.",
    status: str = RiskStatus.ACTIVE.value,
    detected_at: datetime | None = None,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    evidence: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
    resolved_at: datetime | None = None,
) -> Risk:
    """Write one ``risks`` row directly.

    Rows are written here rather than through ``POST /intelligence/evaluate``
    because most of this file is about what the API does with a risk that
    already exists — pagination, ordering, tenancy, lifecycle — and one detection
    pass can only ever produce the shapes the six detectors know how to produce.
    The instants are explicit for the reason
    :mod:`tests.analytics_fixtures` writes rows itself: an ordering assertion
    cannot be made against a clock.
    """
    row = Risk(
        id=uuid.uuid4(),
        user_id=owner.id,
        risk_type=risk_type,
        severity=severity,
        score=score,
        title=title,
        description=description,
        evidence=_default_evidence() if evidence is None else evidence,
        evidence_strength="low",
        entity_type=entity_type,
        entity_id=entity_id,
        status=status,
        detected_at=detected_at or _recent(1),
        resolved_at=resolved_at,
        metadata_={"remaining_minutes": 240} if metadata is None else metadata,
    )
    db_session.add(row)
    await db_session.commit()
    return row


async def _recommendation(
    db_session: AsyncSession,
    owner: User,
    *,
    recommendation_type: str = RecommendationType.BLOCK_TIME.value,
    priority: str = "high",
    title: str = "Schedule another 4h for the Q3 report",
    description: str = "Add 4h of unscheduled work before the due date.",
    reason: str = "4h of estimated work remains and 0m is booked, leaving 4h unscheduled.",
    risk_id: uuid.UUID | None = None,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    status: str = RecommendationStatus.NEW.value,
    created_at: datetime | None = None,
) -> Recommendation:
    """Write one ``recommendations`` row directly.

    ``reason`` is non-blank by construction because
    :class:`~app.schemas.recommendation.RecommendationRead` refuses a blank one
    at validation, which would turn a fixture mistake into a 500 on a read route.
    """
    row = Recommendation(
        id=uuid.uuid4(),
        user_id=owner.id,
        recommendation_type=recommendation_type,
        priority=priority,
        title=title,
        description=description,
        reason=reason,
        risk_id=risk_id,
        entity_type=entity_type,
        entity_id=entity_id,
        status=status,
        created_at=created_at or _recent(1),
        metadata_={"rule": recommendation_type},
    )
    db_session.add(row)
    await db_session.commit()
    return row


async def _two_accounts(
    client: Any, db_session: AsyncSession
) -> tuple[
    tuple[AnalyticsSeed, User, dict[str, str]],
    tuple[AnalyticsSeed, User, dict[str, str]],
]:
    """Two signed-in accounts as ``(seed, owner, headers)``, Ada's first and Grace's second.

    Two accounts rather than one plus a hand-written foreign row, because the
    isolation assertions are about what a *response* may contain: a foreign id
    that exists only in a fixture is a weaker adversary than one with its own
    risks, its own suggestions and its own detection runs.
    """
    ada_seed, ada_headers = await seeded_client(client, db_session)
    grace_seed, grace_headers = await seeded_client(
        client, db_session, username="grace", email="grace@nexus.test"
    )
    return (ada_seed, ada_seed.owner, ada_headers), (grace_seed, grace_seed.owner, grace_headers)


async def _overdue_task(seed: AnalyticsSeed) -> tuple[uuid.UUID, uuid.UUID]:
    """The one project and one overdue task the detection counts are derived from.

    Returned as ids only: the caller asserts on detection output, and a test that
    held the objects would be tempted to assert on them instead of on the wire.
    """
    project = await seed.project(name="atlas", created_at=_recent(PROJECT_CREATED_BY.days))
    task = await seed.task(
        project_id=project.id,
        title="Q3 report",
        created_at=_recent(CREATED_BY.days),
        due_date=(datetime.now(UTC) - OVERDUE_BY).date(),
        estimated_minutes=240,
    )
    return project.id, task.id


@pytest.fixture
async def account(client, db_session) -> tuple[AnalyticsSeed, dict[str, str]]:
    """One signed-in account with no recorded work, as ``(seed, headers)``.

    The seed rather than the bare ``User`` because most tests need both: rows
    are written for the owner, and ``seed.owner`` is the id those rows are filed
    under.
    """
    seed, headers = await seeded_client(client, db_session)
    return seed, headers


async def _seed_ordering(db_session: AsyncSession, owner: User) -> dict[str, uuid.UUID]:
    """The six rows :data:`ORDERING_FIXTURE` describes, by label."""
    rows = {}
    for label, severity, days_ago in ORDERING_FIXTURE:
        rows[label] = await _risk(
            db_session,
            owner,
            severity=severity,
            score={"critical": 90, "high": 60, "medium": 30, "low": 5}[severity],
            title=f"Risk {label}",
            detected_at=_recent(days_ago),
        )
    return rows


def _counts_only(summary: dict) -> dict:
    """The six tally keys of a summary response, with the band definitions split off.

    ``severity_bands`` describes the deployment's ladder rather than this
    account's work, so the "these are Ada's counts and nobody else's"
    assertions compare the counts alone and check the bands separately.
    """
    return {key: value for key, value in summary.items() if key != "severity_bands"}


def _severity_band_edges(summary: dict) -> list[tuple[str, int, int | None]]:
    """``(severity, minimum_score, maximum_score)`` per stated band, in order."""
    return [
        (band["severity"], band["minimum_score"], band["maximum_score"])
        for band in summary["severity_bands"]
    ]


# ---------------------------------------------------------------------------
# The gate: 401 anonymous, 403 without analytics.read, on all fourteen routes
# ---------------------------------------------------------------------------

_ROUTE_CASES = [pytest.param(method, path, id=f"{method} {path}") for method, path in ROUTES]


@pytest.mark.parametrize(("method", "template"), _ROUTE_CASES)
async def test_every_route_refuses_an_anonymous_caller(
    method, template, client, assert_error_envelope
):
    """No Phase 7 route answers a caller who has not signed in.

    Authentication runs before the permission check, so "you are not signed in"
    is never reported as "you may not" — they are different answers to different
    questions, and a client acts on them differently.
    """
    response = await _request(client, method, template)

    error = assert_error_envelope(response, status_code=401, code="unauthorized")
    assert error["details"] is None


@pytest.mark.parametrize(("method", "template"), _ROUTE_CASES)
async def test_every_route_refuses_a_caller_without_analytics_read(
    method, template, client, db_session, account, assert_error_envelope
):
    """A valid, live session whose role grants nothing is answered 403.

    Nothing here is about authentication: the token is real and the session row
    is live, so this is the permission gate refusing a capability the role map
    does not grant. Phase 7 reuses ``analytics.read`` rather than coining a
    ``risks.write``, and this is the test that says the reuse is enforced on the
    seven transition routes and not only on the five reads.
    """
    seed, headers = account
    owner = seed.owner
    await db_session.execute(
        update(User).where(User.id == owner.id).values(role=ROLE_WITHOUT_ANALYTICS)
    )
    await db_session.commit()

    response = await _request(client, method, template, headers=headers)

    assert_error_envelope(response, status_code=403, code="forbidden")


# ---------------------------------------------------------------------------
# The routing hazard
# ---------------------------------------------------------------------------


async def test_the_summary_route_is_not_swallowed_by_the_id_route(client, db_session, account):
    """``GET /risks/summary`` answers with counts, not with a uuid parse failure.

    The assertion is the presence of ``needs_attention``, which only
    :class:`~app.schemas.risk.RiskSummaryRead` carries. Were ``/{risk_id}``
    registered first, Starlette would bind the literal string ``summary`` to the
    path parameter, the uuid conversion would fail, and the dashboard tile would
    answer 422 about an id nobody ever issued — a route that still exists and
    still answers a different question, which is why only this test catches it.
    """
    _seed, headers = account

    response = await client.get("/api/v1/risks/summary", headers=headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert "needs_attention" in body, body
    assert set(body) == {
        "critical",
        "high",
        "medium",
        "low",
        "total",
        "needs_attention",
        "severity_bands",
    }


async def test_the_summary_counts_only_live_risks(client, db_session, account):
    """Five zeroes, then the live risks — and nothing that has been closed.

    A resolved or dismissed row is history, and a dashboard that kept counting it
    would never let a user see that clearing a backlog changed anything.
    ``needs_attention`` is raised by ``high`` and above only: a tile that alarms
    over an amber band teaches users to ignore it.
    """
    seed, headers = account
    owner = seed.owner
    empty = await client.get("/api/v1/risks/summary", headers=headers)
    assert empty.status_code == 200, empty.text
    assert _counts_only(empty.json()) == {
        "critical": 0,
        "high": 0,
        "medium": 0,
        "low": 0,
        "total": 0,
        "needs_attention": False,
    }

    await _risk(db_session, owner, severity="critical", score=91, status="active")
    await _risk(db_session, owner, severity="high", score=60, status="active")
    await _risk(db_session, owner, severity="high", score=55, status="acknowledged")
    await _risk(db_session, owner, severity="medium", score=30, status="active")
    await _risk(
        db_session,
        owner,
        severity="low",
        score=10,
        status=RiskStatus.DISMISSED.value,
        resolved_at=_recent(0),
    )

    response = await client.get("/api/v1/risks/summary", headers=headers)

    assert response.status_code == 200, response.text
    assert _counts_only(response.json()) == {
        "critical": 1,
        "high": 2,
        "medium": 1,
        "low": 0,
        "total": 4,
        "needs_attention": True,
    }
    # The bands are a property of the deployment, not of the account, so an empty
    # account and a flagged one state the same ladder. Asserting it here is what
    # stops the field silently becoming an empty list again.
    assert _severity_band_edges(response.json()) == [
        ("critical", 75, None),
        ("high", 50, 74),
        ("medium", 25, 49),
        ("low", 0, 24),
    ]


# ---------------------------------------------------------------------------
# Listing: pagination, filters, ordering
# ---------------------------------------------------------------------------


async def test_risks_are_ordered_by_severity_then_by_detection_time(client, db_session, account):
    """The list is worst-first, and within a band the most recently seen first.

    Severity words do not sort into their own order alphabetically, so the
    ranking has to be built from the enum; asserting the exact sequence of ids is
    what pins it, because a client that sorted the page itself would have to
    reimplement the ladder and would eventually get it wrong.
    """
    seed, headers = account
    owner = seed.owner
    rows = await _seed_ordering(db_session, owner)

    response = await client.get("/api/v1/risks", headers=headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["id"] for item in body["items"]] == [
        str(rows[label].id) for label in ORDERING_EXPECTED
    ]
    assert [item["title"] for item in body["items"]] == [
        f"Risk {label}" for label in ORDERING_EXPECTED
    ]
    # The words themselves are the assertion: `medium` sorts above `high` under a
    # plain `ORDER BY severity DESC`, so this sequence is unreachable without the
    # enum-built rank.
    assert [item["severity"] for item in body["items"]] == [
        "critical",
        "critical",
        "high",
        "high",
        "medium",
        "low",
    ]
    assert body["total"] == len(ORDERING_FIXTURE)
    assert body["by_severity"] == {"critical": 2, "high": 2, "medium": 1, "low": 1}


async def test_the_risk_list_pages_without_losing_or_repeating_a_row(client, db_session, account):
    """Two pages of two over six rows, plus the totals that describe the match.

    ``total`` is the number the *filters* match rather than the length of the
    page, and ``limit``/``offset`` are echoed so a client paging through can tell
    a short page from a filtered one.
    """
    seed, headers = account
    owner = seed.owner
    rows = await _seed_ordering(db_session, owner)
    expected = [str(rows[label].id) for label in ORDERING_EXPECTED]

    first = await client.get("/api/v1/risks", params={"limit": 2, "offset": 0}, headers=headers)
    second = await client.get("/api/v1/risks", params={"limit": 2, "offset": 2}, headers=headers)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert [item["id"] for item in first.json()["items"]] == expected[:2]
    assert [item["id"] for item in second.json()["items"]] == expected[2:4]
    assert first.json()["limit"] == 2
    assert first.json()["offset"] == 0
    assert second.json()["offset"] == 2
    for body in (first.json(), second.json()):
        assert body["total"] == len(ORDERING_FIXTURE)
        # The tally is across the whole match, not across the page.
        assert body["by_severity"] == {"critical": 2, "high": 2, "medium": 1, "low": 1}
    seen = [{item["id"] for item in page.json()["items"]} for page in (first, second)]
    assert seen[0].isdisjoint(seen[1])


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"offset": -1}])
async def test_a_page_size_outside_the_documented_bounds_is_422(
    params, client, db_session, account
):
    """``?limit=500`` is refused rather than quietly truncated to the ceiling.

    A caller that asked for 500 and received 100 cannot tell a truncated page
    from a page that was always 100 rows long, so the cap is a rejection.
    """
    _seed, headers = account

    response = await client.get("/api/v1/risks", params=params, headers=headers)

    assert response.status_code == 422, response.text


async def test_the_risk_list_filters_by_status_and_by_type(client, db_session, account):
    """Each filter narrows the rows, and the tally narrows with them.

    ``by_severity`` counts the **same set the items come from**, which is what
    makes ``sum(by_severity.values()) == total`` true rather than incidental.
    Asserting the type filter reaches the tally is what stops a future change
    from quietly describing the whole account over the top of a filtered page.
    """
    seed, headers = account
    owner = seed.owner
    deadline = await _risk(
        db_session, owner, risk_type=RiskType.DEADLINE.value, severity="critical", score=91
    )
    project_active = await _risk(
        db_session, owner, risk_type=RiskType.PROJECT.value, severity="high", score=60
    )
    project_acknowledged = await _risk(
        db_session,
        owner,
        risk_type=RiskType.PROJECT.value,
        severity="medium",
        score=30,
        status=RiskStatus.ACKNOWLEDGED.value,
    )

    by_type = await client.get("/api/v1/risks", params={"risk_type": "project"}, headers=headers)
    assert by_type.status_code == 200, by_type.text
    body = by_type.json()
    # Both project risks, worst first — the type filter narrows the rows without
    # touching their ordering.
    assert [item["id"] for item in body["items"]] == [
        str(project_active.id),
        str(project_acknowledged.id),
    ]
    assert body["total"] == 2
    # The type filter reaches the tally too, so the header describes the two rows
    # on screen and not the deadline risk the caller filtered away.
    assert body["by_severity"] == {"critical": 0, "high": 1, "medium": 1, "low": 0}
    assert sum(body["by_severity"].values()) == body["total"]

    by_status = await client.get(
        "/api/v1/risks", params={"status": "acknowledged"}, headers=headers
    )
    assert by_status.status_code == 200, by_status.text
    status_body = by_status.json()
    assert status_body["total"] == 1
    assert [item["id"] for item in status_body["items"]] == [str(project_acknowledged.id)]
    assert status_body["by_severity"] == {"critical": 0, "high": 0, "medium": 1, "low": 0}

    both = await client.get(
        "/api/v1/risks",
        params={"status": "active", "risk_type": "project"},
        headers=headers,
    )
    assert both.status_code == 200, both.text
    assert [item["id"] for item in both.json()["items"]] == [str(project_active.id)]
    assert deadline.id not in {item["id"] for item in both.json()["items"]}


async def test_the_risk_list_filters_by_severity_band(client, db_session, account):
    """One band, counted across every page rather than across the rows in hand.

    The band is the tile filter, so it is the one whose client-side version was
    visibly wrong: narrowing a page in the browser could only ever find the rows
    that page already carried. Answering it here is what lets ``total`` and the
    pager mean something while a band is in force.
    """
    seed, headers = account
    owner = seed.owner
    rows = await _seed_ordering(db_session, owner)

    response = await client.get("/api/v1/risks", params={"severity": "high"}, headers=headers)

    assert response.status_code == 200, response.text
    body = response.json()
    # Both `high` rows, and only them, still in the repository's ordering.
    assert [item["id"] for item in body["items"]] == [
        str(rows["high-new"].id),
        str(rows["high-old"].id),
    ]
    assert body["total"] == 2
    # The tally describes the filtered set, so the three bands the caller did not
    # ask for read zero rather than repeating numbers the list is not showing.
    assert body["by_severity"] == {"critical": 0, "high": 2, "medium": 0, "low": 0}


async def test_a_band_filter_pages_without_losing_a_row(client, db_session, account):
    """Two rows in a band, taken one at a time.

    This is the case the client-side band could not express at all: the pager was
    withdrawn while a band was active, because the narrowed page had no total to
    divide by. ``total`` here is the band's, so the second row is reachable.
    """
    seed, headers = account
    owner = seed.owner
    rows = await _seed_ordering(db_session, owner)

    first = await client.get(
        "/api/v1/risks", params={"severity": "high", "limit": 1, "offset": 0}, headers=headers
    )
    second = await client.get(
        "/api/v1/risks", params={"severity": "high", "limit": 1, "offset": 1}, headers=headers
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert [item["id"] for item in first.json()["items"]] == [str(rows["high-new"].id)]
    assert [item["id"] for item in second.json()["items"]] == [str(rows["high-old"].id)]
    # A short page is not a filtered one: both pages say the band holds two.
    for page in (first, second):
        assert page.json()["total"] == 2
        assert page.json()["by_severity"] == {"critical": 0, "high": 2, "medium": 0, "low": 0}


async def test_the_band_filter_narrows_with_status_and_type_together(client, db_session, account):
    """Three filters are an intersection, on the items and on the tally alike.

    The rows are seeded so that each filter alone would still leave more than
    one: dropping ``status`` widens the answer, which is what distinguishes an
    intersection from three filters where only the last one is doing the work.
    """
    seed, headers = account
    owner = seed.owner
    deadline = await _risk(
        db_session, owner, risk_type=RiskType.DEADLINE.value, severity="critical", score=91
    )
    project_active = await _risk(
        db_session,
        owner,
        risk_type=RiskType.PROJECT.value,
        severity="high",
        score=60,
        detected_at=_recent(1),
    )
    project_acknowledged = await _risk(
        db_session,
        owner,
        risk_type=RiskType.PROJECT.value,
        severity="high",
        score=58,
        status=RiskStatus.ACKNOWLEDGED.value,
        detected_at=_recent(2),
    )
    workload = await _risk(
        db_session,
        owner,
        risk_type=RiskType.WORKLOAD.value,
        severity="high",
        score=52,
        detected_at=_recent(3),
    )

    narrowed = await client.get(
        "/api/v1/risks",
        params={"severity": "high", "status": "active", "risk_type": "project"},
        headers=headers,
    )
    assert narrowed.status_code == 200, narrowed.text
    body = narrowed.json()
    assert [item["id"] for item in body["items"]] == [str(project_active.id)]
    assert body["total"] == 1
    assert body["by_severity"] == {"critical": 0, "high": 1, "medium": 0, "low": 0}

    # Dropping one filter widens it by exactly the rows that filter was holding.
    band_only = await client.get("/api/v1/risks", params={"severity": "high"}, headers=headers)
    assert band_only.status_code == 200, band_only.text
    assert [item["id"] for item in band_only.json()["items"]] == [
        str(project_active.id),
        str(project_acknowledged.id),
        str(workload.id),
    ]
    assert band_only.json()["total"] == 3
    assert str(deadline.id) not in {item["id"] for item in band_only.json()["items"]}


async def test_the_band_tally_and_the_total_describe_the_same_rows(client, db_session, account):
    """``sum(by_severity.values()) == total``, whatever band is in force.

    The two are produced by different statements — a window count for the rows
    and a grouped count for the tally — and a client is entitled to add one up
    and check it against the other. The assertion is made over every band rather
    than one because the interesting failure is a filter that reaches the grouped
    count and not the rows, which shows up as a mismatch rather than as a 422.

    The empty intersection is included because it is the one case the repository
    answers with a different statement: a window count only exists on a returned
    row, so a filter matching nothing falls back to a separate ``COUNT``. A band
    filter that narrowed the rows but not that fallback would report a total of
    zero for a band the tally says holds rows.
    """
    seed, headers = account
    owner = seed.owner
    await _seed_ordering(db_session, owner)

    requests = [{"severity": band} for band in ("critical", "high", "medium", "low")]
    requests.append({"severity": "critical", "status": "dismissed"})

    for params in requests:
        response = await client.get("/api/v1/risks", params=params, headers=headers)

        assert response.status_code == 200, (params, response.text)
        body = response.json()
        assert sum(body["by_severity"].values()) == body["total"], params
        assert set(body["by_severity"]) == {"critical", "high", "medium", "low"}, params
        if body["total"] == 0:
            assert body["items"] == [], params
            assert body["by_severity"] == ZERO_BANDS, params
        else:
            assert {item["severity"] for item in body["items"]} == {params["severity"]}, params
            assert body["by_severity"][params["severity"]] == body["total"], params


async def test_omitting_the_band_filter_describes_the_whole_set(client, db_session, account):
    """No ``severity`` parameter is no filter at all.

    The band parameter is optional rather than defaulting to a first band,
    because a default would silently hide every other band from a caller who
    never asked to be shown one.
    """
    seed, headers = account
    owner = seed.owner
    rows = await _seed_ordering(db_session, owner)

    response = await client.get("/api/v1/risks", headers=headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["id"] for item in body["items"]] == [
        str(rows[label].id) for label in ORDERING_EXPECTED
    ]
    assert body["total"] == len(ORDERING_FIXTURE)
    assert body["by_severity"] == {"critical": 2, "high": 2, "medium": 1, "low": 1}


@pytest.mark.parametrize(
    "params",
    [{"status": "nonsense"}, {"risk_type": "budget"}, {"severity": "catastrophic"}],
    ids=["status", "risk_type", "severity"],
)
async def test_an_unknown_filter_value_is_422_not_a_silently_empty_list(
    params, client, db_session, account
):
    """A misspelled filter is a mistake, and a 200 with zero rows reads as one.

    An empty list is the real answer for a value that matches nothing, and it
    must not also be the answer for a word the vocabulary has never heard of — or
    a client with a typo would render an empty Risk Center and believe there is
    nothing wrong with the plan.
    """
    seed, headers = account
    owner = seed.owner
    await _risk(db_session, owner)

    response = await client.get("/api/v1/risks", params=params, headers=headers)

    assert response.status_code == 422, response.text


# ---------------------------------------------------------------------------
# Ownership — 404, never 403
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "template"),
    [pytest.param(method, path, id=f"{method} {path}") for method, path in RISK_ID_ROUTES],
)
async def test_a_foreign_risk_is_404_not_403_on_every_route(
    method, template, client, db_session, assert_error_envelope
):
    """Grace's risk, read and transitioned, is refused exactly like a typo.

    The caller is authenticated and permitted — the token is Ada's, and Ada
    holds ``analytics.read`` — and still gets ``not_found``. The message for the
    foreign id is compared against the message for an id nobody ever issued, so
    the two cases cannot drift apart into "403 for someone else's, 404 for a
    typo", which is the drift this rule exists to prevent.
    """
    (
        (_ada_seed, _ada_owner, ada_headers),
        (_grace_seed, grace_owner, _grace_headers),
    ) = await _two_accounts(client, db_session)
    foreign = await _risk(db_session, grace_owner, title="Grace's own risk")

    response = await _request(client, method, template, headers=ada_headers, risk_id=foreign.id)
    error = assert_error_envelope(response, status_code=404, code="not_found")
    assert error["message"] == RISK_NOT_FOUND_MESSAGE
    assert "Grace's own risk" not in response.text

    unissued = await _request(client, method, template, headers=ada_headers)
    unissued_error = assert_error_envelope(unissued, status_code=404, code="not_found")
    assert unissued_error["message"] == error["message"]


@pytest.mark.parametrize(
    ("method", "template"),
    [
        pytest.param(method, path, id=f"{method} {path}")
        for method, path in RECOMMENDATION_ID_ROUTES
    ],
)
async def test_a_foreign_recommendation_is_404_not_403_on_every_route(
    method, template, client, db_session, assert_error_envelope
):
    """The same rule on the suggestion surface: the read and all four transitions.

    What matters for tenancy is the code and the status: every one of these is
    ``404 / not_found`` for an authenticated, permitted caller, and the message
    is identical whether the row is somebody else's or was never issued. The two
    *messages* differ between the read and the transitions — see
    :data:`RECOMMENDATION_TRANSITION_NOT_FOUND_MESSAGE` for why, and it is a
    reported defect rather than an expected difference.
    """
    (
        (_ada_seed, _ada_owner, ada_headers),
        (_grace_seed, grace_owner, _grace_headers),
    ) = await _two_accounts(client, db_session)
    foreign = await _recommendation(db_session, grace_owner, title="Grace's own suggestion")
    expected_message = (
        RECOMMENDATION_NOT_FOUND_MESSAGE
        if method == "GET"
        else RECOMMENDATION_TRANSITION_NOT_FOUND_MESSAGE
    )

    response = await _request(
        client, method, template, headers=ada_headers, recommendation_id=foreign.id
    )
    error = assert_error_envelope(response, status_code=404, code="not_found")
    assert error["message"] == expected_message
    assert "Grace's own suggestion" not in response.text

    unissued = await _request(client, method, template, headers=ada_headers)
    unissued_error = assert_error_envelope(unissued, status_code=404, code="not_found")
    assert unissued_error["message"] == error["message"]


# ---------------------------------------------------------------------------
# The risk lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("action", "expected", "event"),
    [pytest.param(*case, id=case[0]) for case in RISK_TRANSITIONS],
)
async def test_each_transition_moves_the_risk_and_records_why(
    action, expected, event, client, db_session, account
):
    """Acknowledge, dismiss and resolve each write their status and one event.

    Acknowledging is not resolving: it means "still true, no longer needs my
    attention", the row stays live and keeps being re-detected, and it stays out
    of a terminal state so nothing downstream treats it as history. The two
    terminal moves stamp ``resolved_at``, which is the only way "how long was this
    open" is answerable without reading the event log.
    """
    seed, headers = account
    owner = seed.owner
    risk = await _risk(db_session, owner)

    response = await _request(
        client, "POST", f"/api/v1/risks/{{risk_id}}/{action}", headers=headers, risk_id=risk.id
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == str(risk.id)
    assert body["status"] == expected
    terminal = expected in {RiskStatus.DISMISSED.value, RiskStatus.RESOLVED.value}
    assert (body["resolved_at"] is not None) is terminal
    # A re-detection refreshes a live row in place without moving this clock, and
    # a transition does not touch it either: it is the left-hand side of
    # ``resolved_at - detected_at``.
    assert datetime.fromisoformat(body["detected_at"]) == risk.detected_at

    recorded = await _events(db_session, owner.id, event)
    assert len(recorded) == 1, event
    assert recorded[0].metadata_["risk_id"] == str(risk.id)
    assert recorded[0].metadata_["status"] == expected
    assert recorded[0].metadata_["risk_type"] == risk.risk_type


async def test_a_terminal_risk_is_409_on_every_further_transition(
    client, db_session, assert_error_envelope, account
):
    """A closed risk cannot be acknowledged, resolved or dismissed again.

    A silent 200 would be worse than either refusal: a client that dismissed a
    risk it had already dismissed would render "dismissed" and believe it had
    done something, when what it actually wanted to say is that the risk is
    gone. The message is one fixed sentence rather than one naming the row's
    current status — a deliberate asymmetry against the recommendation router,
    which does name it, and the difference is worth seeing stated here.

    A repeat of the status the row *already holds* is a 409 too, and that is
    worth being explicit about because the repository deliberately permits it.
    ``_RISK_TRANSITIONS`` maps ``dismissed -> {dismissed}`` because the detection
    sweep must be able to re-resolve a row another run already closed without
    raising. That is right for the sweep and wrong for this endpoint: a user who
    clicks "dismiss" on a risk they already dismissed should be told it is
    closed, not handed a cheerful 200 and a second dismissal event. The two
    callers genuinely want different answers, so the distinction is made in the
    router — which already holds the row — rather than by loosening the
    repository's table.
    """
    seed, headers = account
    owner = seed.owner
    rows = [
        await _risk(
            db_session,
            owner,
            status=RiskStatus.DISMISSED.value,
            resolved_at=_recent(0),
        ),
        await _risk(
            db_session,
            owner,
            status=RiskStatus.RESOLVED.value,
            resolved_at=_recent(0),
        ),
    ]
    for row in rows:
        for action, _expected, _event in RISK_TRANSITIONS:
            response = await _request(
                client,
                "POST",
                f"/api/v1/risks/{{risk_id}}/{action}",
                headers=headers,
                risk_id=row.id,
            )
            # Every combination is a 409, including the repeat of the status the
            # row already holds. See the docstring: the repository permits that
            # move for the detection sweep, and the router refuses it for a
            # user, because the two callers want different answers.
            error = assert_error_envelope(response, status_code=409, code="conflict")
            assert "not in a state this action can apply to" in error["message"]

        # The row is untouched by the refusals: a refused transition is not a
        # silent write, and neither is the idempotent one.
        after = await client.get(f"/api/v1/risks/{row.id}", headers=headers)
        assert after.status_code == 200, after.text
        assert after.json()["status"] == row.status


async def test_a_user_transition_records_that_the_user_was_the_actor(client, db_session, account):
    """``metadata.resolution`` is ``user`` when a transition closed the row.

    The risk table has no ``responded_at`` column, so "they fixed it" and "the
    condition went away" are distinguished by this key — which is the difference
    a later model would be trained on, and a human reading a history would want
    told.
    """
    seed, headers = account
    owner = seed.owner
    risk = await _risk(db_session, owner)

    response = await _request(
        client, "POST", "/api/v1/risks/{risk_id}/resolve", headers=headers, risk_id=risk.id
    )

    assert response.status_code == 200, response.text
    assert response.json()["metadata"]["resolution"] == "user"


# ---------------------------------------------------------------------------
# The recommendation lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("action", "expected", "stamps", "event"),
    [pytest.param(*case, id=case[0]) for case in RECOMMENDATION_TRANSITIONS],
)
async def test_each_recommendation_transition_writes_its_own_status_and_event(
    action, expected, stamps, event, client, db_session, account
):
    """Four answers, four rows, and ``responded_at`` on exactly three of them.

    Viewing a suggestion is not answering it: the column is the evidence that
    there *was* a decision, and a system that counted a read as a response would
    train itself on a signal that says nothing. Each transition also appends its
    own event, because the status column says what a suggestion is now and the
    feed says when and in what order it got there.

    ``from_status`` is the status the row held *before* this call — which is the
    whole point of the field. "accepted from viewed" and "accepted without being
    read" are different rows in a future training set, and a ``from_status``
    that always equalled the target could not tell them apart. Capturing it
    takes reading it before the write: ``get_recommendation`` and
    ``transition_recommendation`` share one session, and the latter's ``UPDATE
    ... RETURNING`` runs with ``populate_existing=True``, so an attribute read
    after the transition returns the status the write just set. Asserted as
    ``new`` here because every case in this table starts from ``new``.
    """
    seed, headers = account
    owner = seed.owner
    suggestion = await _recommendation(db_session, owner)

    response = await _request(
        client,
        "POST",
        f"/api/v1/recommendations/{{recommendation_id}}/{action}",
        headers=headers,
        recommendation_id=suggestion.id,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == str(suggestion.id)
    assert body["status"] == expected
    assert (body["responded_at"] is not None) is stamps
    assert body["expires_at"] is None

    recorded = await _events(db_session, owner.id, event)
    assert len(recorded) == 1, event
    assert recorded[0].metadata_["recommendation_id"] == str(suggestion.id)
    assert recorded[0].metadata_["status"] == expected
    assert recorded[0].metadata_["from_status"] == "new"


async def test_an_answered_recommendation_is_409_naming_the_status_it_is_in(
    client, db_session, assert_error_envelope, account
):
    """A rejected suggestion cannot be accepted, and the 409 says it is rejected.

    The lifecycle is one-way and terminal, so the only useful thing a 409 can
    carry is which state the row is in: "this one is rejected" is what tells a
    client to stop offering the button, and a message that only said "no" would
    leave it guessing between a state it can recover from and one it cannot.
    """
    seed, headers = account
    owner = seed.owner
    rejected = await _recommendation(db_session, owner, status=RecommendationStatus.REJECTED.value)
    completed = await _recommendation(
        db_session, owner, status=RecommendationStatus.COMPLETED.value
    )

    response = await _request(
        client,
        "POST",
        "/api/v1/recommendations/{recommendation_id}/accept",
        headers=headers,
        recommendation_id=rejected.id,
    )
    error = assert_error_envelope(response, status_code=409, code="conflict")
    assert (
        error["message"] == "This recommendation was declined, so it cannot be marked as accepted."
    )

    second = await _request(
        client,
        "POST",
        "/api/v1/recommendations/{recommendation_id}/view",
        headers=headers,
        recommendation_id=completed.id,
    )
    second_error = assert_error_envelope(second, status_code=409, code="conflict")
    assert (
        second_error["message"]
        == "This recommendation was completed, so it cannot be marked as read."
    )


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


async def test_an_evaluation_runs_detection_and_reports_exact_counts(client, db_session, account):
    """One pass over one overdue task: two risks, two suggestions, both explained.

    The two risks are derived rather than recorded. The deadline scores 100 and
    bands ``critical`` because the scoring module returns 100 by construction for
    a deadline that has passed; the project roll-up contributes one overdue task
    (``0.30 x 1/10``) and one remaining task (``0.10 x 1/20``) for
    ``round(3.5) == 4``, which bands ``low``. The two suggestions are the
    deadline-review — the deadline-gap rule declines on a date that has gone —
    and the project-review.

    Everything else is *not* written, and the summary says why: no declared
    availability leaves the workload detector nothing to compare against, fewer
    than three completed pairs leaves the estimation detector no history, an
    earlier window with no activity in it leaves the consistency detector no
    baseline, and no work sessions at all leaves the scheduling detector no plan
    to inspect. That last one is a genuine absence rather than a measured zero —
    the fixture books no work — and it is why the pass is still ``evaluated``
    only because the deadline detector had something to judge.
    """
    seed, headers = account
    await _overdue_task(seed)

    response = await client.post("/api/v1/intelligence/evaluate", headers=headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["evaluated"] is True
    assert body["risks_found"] == DETECTION_RISKS_FOUND
    assert body["risks_created"] == DETECTION_RISKS_FOUND
    assert body["risks_updated"] == 0
    assert body["risks_resolved"] == 0
    assert body["recommendations_created"] == DETECTION_RECOMMENDATIONS
    assert body["by_severity"] == DETECTION_BY_SEVERITY
    assert body["by_type"] == DETECTION_BY_TYPE
    assert body["duration_ms"] >= 0
    reason = body["reason_if_not_evaluated"]
    assert reason.startswith("Workload: No availability is configured")
    assert "Estimation: Not enough historical data" in reason
    assert "Consistency: " in reason
    assert "Scheduling: No work sessions are recorded in this window" in reason
    assert reason.endswith(
        "Task: Not assessed: no open task is recorded as blocked or has 3 or more "
        "reschedules in its history."
    )

    # The two rows the summary describes are the two the Risk Center now holds.
    listed = await client.get("/api/v1/risks", headers=headers)
    assert listed.status_code == 200, listed.text
    items = listed.json()["items"]
    assert len(items) == DETECTION_RISKS_FOUND
    assert {item["risk_type"] for item in items} == {"deadline", "project"}
    assert items[0]["severity"] == "critical"
    assert items[0]["score"] == 100

    suggestions = await client.get("/api/v1/recommendations", headers=headers)
    assert suggestions.status_code == 200, suggestions.text
    assert len(suggestions.json()["items"]) == DETECTION_RECOMMENDATIONS


async def test_a_second_evaluation_refreshes_the_same_rows_rather_than_adding_more(
    client, db_session, account
):
    """Two presses of the button are one Risk Center and two run summaries.

    This is the brief's "the same underlying risk should not generate hundreds
    of identical records" demonstrated rather than asserted: the second pass
    finds the same two conditions, updates them in place through the partial
    unique index, and reports ``created=0 / updated=2``.
    """
    seed, headers = account
    await _overdue_task(seed)

    first = await client.post("/api/v1/intelligence/evaluate", headers=headers)
    assert first.status_code == 200, first.text
    second = await client.post("/api/v1/intelligence/evaluate", headers=headers)

    assert second.status_code == 200, second.text
    body = second.json()
    assert body["risks_found"] == DETECTION_RISKS_FOUND
    assert body["risks_created"] == 0
    assert body["risks_updated"] == DETECTION_RISKS_FOUND
    assert body["risks_resolved"] == 0
    # Both suggestions are already open and saying the same thing, so neither is
    # counted as created again.
    assert body["recommendations_created"] == 0

    listed = await client.get("/api/v1/risks", headers=headers)
    assert listed.json()["total"] == DETECTION_RISKS_FOUND
    suggestions = await client.get("/api/v1/recommendations", headers=headers)
    assert suggestions.json()["total"] == DETECTION_RECOMMENDATIONS


async def test_an_evaluation_closes_a_live_risk_the_detectors_no_longer_agree_with(
    client, db_session, account
):
    """Reconciliation is what stops the Risk Center only ever growing.

    A live risk whose condition is not re-detected is a condition that has
    stopped being news, and the only thing that can notice is a detector that ran
    and disagreed. Without this step the table is append-only and the user's
    only way to clear it is by hand.
    """
    seed, headers = account
    owner = seed.owner
    await _overdue_task(seed)
    stale = await _risk(
        db_session,
        owner,
        risk_type=RiskType.SCHEDULING.value,
        severity="low",
        score=12,
        title="Conflicts in the scheduled plan",
        entity_type="account",
        detected_at=_recent(2),
    )

    response = await client.post("/api/v1/intelligence/evaluate", headers=headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["risks_resolved"] == 1
    assert body["risks_found"] == DETECTION_RISKS_FOUND

    after = await client.get(f"/api/v1/risks/{stale.id}", headers=headers)
    assert after.status_code == 200, after.text
    assert after.json()["status"] == RiskStatus.RESOLVED.value
    assert after.json()["resolved_at"] is not None
    recorded = await _events(db_session, owner.id, ActivityEvent.RISK_RESOLVED.value)
    assert len(recorded) == 1
    assert recorded[0].metadata_["risk_id"] == str(stale.id)


@pytest.mark.parametrize("window_days", [0, 367], ids=["below-the-floor", "over-the-ceiling"])
async def test_an_out_of_range_window_is_422(window_days, client, db_session, account):
    """A window the analytics reads could not honour is refused at the boundary.

    Zero is caught by the route's own bound and 367 by the detection service,
    which checks the window against ``ANALYTICS_MAX_RANGE_DAYS`` so one mistake
    becomes one error rather than six identical ones from the middle of a pass.
    Both land in the shared 422 envelope, which is the point: the two callers
    learn the same thing from the same shape.
    """
    _seed, headers = account

    response = await client.post(
        "/api/v1/intelligence/evaluate", params={"window_days": window_days}, headers=headers
    )

    assert response.status_code == 422, response.text


async def test_the_run_history_is_newest_first(client, db_session, account):
    """One row per run, ordered by when it ran.

    Asserted on the *counters* rather than on the timestamps: the second run
    created nothing and updated both risks, so ``[0, 2]`` is a fact about the
    two passes and would reverse under any ordering that put the older run first,
    whatever the clocks said.
    """
    seed, headers = account
    await _overdue_task(seed)
    await client.post("/api/v1/intelligence/evaluate", headers=headers)
    await client.post("/api/v1/intelligence/evaluate", headers=headers)

    response = await client.get("/api/v1/intelligence/evaluations", headers=headers)

    assert response.status_code == 200, response.text
    rows = response.json()
    assert len(rows) == 2
    assert [row["risks_created"] for row in rows] == [0, DETECTION_RISKS_FOUND]
    assert [row["risks_updated"] for row in rows] == [DETECTION_RISKS_FOUND, 0]
    assert [row["evaluated_at"] for row in rows] == sorted(
        (row["evaluated_at"] for row in rows), reverse=True
    )
    # A stored row is always a run that ran; ``evaluated=False`` is a live answer
    # from POST /evaluate and is never persisted.
    assert all(row["evaluated"] is True for row in rows)


# ---------------------------------------------------------------------------
# The wire shape: evidence and metadata
# ---------------------------------------------------------------------------


async def test_an_account_level_risk_carries_its_evidence_and_metadata(client, db_session, account):
    """Both survive the round trip, on the exact path the defect lived on.

    ``metadata`` is the name SQLAlchemy reserves on a declarative class, so the
    column is mapped as ``metadata_`` and the wire name is ``metadata``. An
    account-level risk — ``entity_id`` null, which the partial unique index
    cannot arbitrate because two nulls do not collide, so the repository takes
    its advisory-locked select-then-write path — used to store ``{}`` while every
    row-level risk stored correctly. Seeding through the repository rather than
    inserting the row keeps the test on that path, and asserting equality with
    the dictionaries passed in is what makes a regression loud.
    """
    seed, headers = account
    owner = seed.owner
    metadata = {
        "scheduled_minutes": 2280,
        "available_minutes": 1800,
        "window_label": "the 14 days to 05 Jan 2026",
    }
    evidence = [
        {
            "label": "Scheduled against available time",
            "detail": "38h scheduled against 30h available (127%)",
            "contribution": 53.0,
        },
        {
            "label": "Time to move",
            "detail": "approximately 8h of planned work is beyond the declared capacity",
            "contribution": 0.0,
        },
    ]
    row, created = await RiskRepository(db_session).upsert_risk(
        owner.id,
        risk_type=RiskType.WORKLOAD.value,
        severity="high",
        score=53,
        title="Scheduled work is 127% of declared availability",
        description="38h of work is scheduled against 30h of declared availability.",
        evidence=evidence,
        evidence_strength="medium",
        entity_type="account",
        entity_id=None,
        metadata=metadata,
    )
    assert created is True

    response = await client.get(f"/api/v1/risks/{row.id}", headers=headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["metadata"] == metadata
    assert body["evidence"] == evidence
    assert body["entity_type"] == "account"
    assert body["entity_id"] is None
    assert body["evidence_strength"] == "medium"

    # The same two fields on the list route, which serialises through the same
    # helper: a fix applied to the detail handler alone would leave this one
    # serving `{}`.
    listed = await client.get("/api/v1/risks", headers=headers)
    assert listed.status_code == 200, listed.text
    assert listed.json()["items"][0]["metadata"] == metadata
    assert listed.json()["items"][0]["evidence"] == evidence


async def test_a_risk_carries_the_suggestions_raised_for_it(client, db_session, account):
    """The join exists so a risk card can show its actions.

    A risk with no suggestion carries an empty list — "No suggested action yet"
    is a real state with its own rendering, and a null would force every client
    to branch on the difference between "none" and "not looked". The projection
    keeps ``reason``, because the one thing a nested summary may not drop is the
    explanation.
    """
    seed, headers = account
    owner = seed.owner
    project = await _risk(
        db_session, owner, risk_type=RiskType.PROJECT.value, severity="high", score=60
    )
    other = await _risk(db_session, owner, title="Risk with no suggestion")
    suggestion = await _recommendation(
        db_session,
        owner,
        recommendation_type=RecommendationType.REVIEW_PROJECT.value,
        title="Review the open signals on Atlas",
        risk_id=project.id,
        entity_type="project",
        entity_id=project.id,
    )

    response = await client.get(f"/api/v1/risks/{project.id}", headers=headers)

    assert response.status_code == 200, response.text
    nested = response.json()["recommendations"]
    assert len(nested) == 1
    assert set(nested[0]) == {
        "id",
        "recommendation_type",
        "priority",
        "title",
        "reason",
        "status",
        "created_at",
    }
    assert nested[0]["id"] == str(suggestion.id)
    assert nested[0]["title"] == "Review the open signals on Atlas"
    assert nested[0]["reason"] == suggestion.reason
    assert nested[0]["status"] == RecommendationStatus.NEW.value

    empty = await client.get(f"/api/v1/risks/{other.id}", headers=headers)
    assert empty.status_code == 200, empty.text
    assert empty.json()["recommendations"] == []


async def test_a_recommendation_read_carries_the_whole_contract_shape(client, db_session, account):
    """Every documented field is on the wire, ``reason`` included and non-blank.

    The failure a missing ``reason`` produces is invisible in a screenshot: the
    imperative renders perfectly well on a card, so nobody notices the sentence
    justifying it is gone. The wire model is what catches it, and this asserts
    the model is actually on the path.
    """
    seed, headers = account
    owner = seed.owner
    suggestion = await _recommendation(db_session, owner, reason="4h remain and 0m is booked.")

    response = await client.get(f"/api/v1/recommendations/{suggestion.id}", headers=headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {
        "id",
        "recommendation_type",
        "priority",
        "title",
        "description",
        "reason",
        "entity_type",
        "entity_id",
        "risk_id",
        "status",
        "created_at",
        "responded_at",
        "expires_at",
        "metadata",
    }
    assert body["reason"] == "4h remain and 0m is booked."
    assert body["responded_at"] is None


# ---------------------------------------------------------------------------
# Isolation, end to end
# ---------------------------------------------------------------------------


async def test_no_response_to_one_account_ever_contains_another_accounts_data(client, db_session):
    """Two accounts, and Ada's responses are searched for anything of Grace's.

    Grace's rows carry deliberately distinctive markers — a title no realistic
    plan would use and the real ids of her project, her task and her suggestion —
    so a leak is unambiguous. The counts are checked as exact numbers rather
    than "looks about right", because that is precisely the assertion which would
    not notice another account's risk joining a page.
    """
    (
        (_ada_seed, ada_owner, ada_headers),
        (grace_seed, grace_owner, _grace_headers),
    ) = await _two_accounts(client, db_session)
    grace_project_id, grace_task_id = await _overdue_task(grace_seed)
    grace_risk = await _risk(
        db_session,
        grace_owner,
        risk_type=RiskType.PROJECT.value,
        title="GRACE-ONLY project risk",
        entity_type="project",
        entity_id=grace_project_id,
    )
    grace_suggestion = await _recommendation(
        db_session,
        grace_owner,
        title="GRACE-ONLY suggestion",
        risk_id=grace_risk.id,
        entity_type="project",
        entity_id=grace_project_id,
    )
    await _risk(db_session, ada_owner, title="Ada's own risk", entity_type="account")
    await _risk(db_session, ada_owner, title="Ada's second risk", severity="low", score=8)
    await _recommendation(db_session, ada_owner, title="Ada's own suggestion")

    leaked = (
        "GRACE-ONLY",
        str(grace_risk.id),
        str(grace_suggestion.id),
        str(grace_project_id),
        str(grace_task_id),
    )
    for template in (
        "/api/v1/risks",
        "/api/v1/risks/summary",
        "/api/v1/intelligence/evaluations",
        "/api/v1/recommendations",
    ):
        response = await _request(client, "GET", template, headers=ada_headers)
        assert response.status_code == 200, (template, response.text)
        for needle in leaked:
            assert needle not in response.text, (template, needle)

    detail = await client.get(f"/api/v1/risks/{grace_risk.id}", headers=ada_headers)
    assert detail.status_code == 404, detail.text
    for needle in leaked:
        assert needle not in detail.text, needle

    # And the counts are Ada's alone, not "at least Ada's".
    risks = (await _request(client, "GET", "/api/v1/risks", headers=ada_headers)).json()
    assert risks["total"] == 2
    assert {item["title"] for item in risks["items"]} == {"Ada's own risk", "Ada's second risk"}
    assert risks["by_severity"] == {"critical": 0, "high": 1, "medium": 0, "low": 1}
    summary = (await _request(client, "GET", "/api/v1/risks/summary", headers=ada_headers)).json()
    assert _counts_only(summary) == {
        "critical": 0,
        "high": 1,
        "medium": 0,
        "low": 1,
        "total": 2,
        "needs_attention": True,
    }
    suggestions = (
        await _request(client, "GET", "/api/v1/recommendations", headers=ada_headers)
    ).json()
    assert suggestions["total"] == 1
    assert [item["title"] for item in suggestions["items"]] == ["Ada's own suggestion"]
    assert suggestions["items"][0]["entity_id"] is None


# ---------------------------------------------------------------------------
# A brand-new account
# ---------------------------------------------------------------------------


async def test_a_fresh_account_gets_the_documented_empty_shape(client, db_session, account):
    """Both lists answer 200 with zeroes, never a 500.

    "Never crash because there is no activity" is a data-quality requirement
    stated outright, and a 500 here would be a Risk Center unusable precisely
    when a new user first opens it. The exact bodies are asserted rather than
    "items is empty" because the four severity and four priority bands are part
    of the contract: a client reading ``by_severity.high`` must not need a
    fallback default that turns a missing key into the same number as an empty
    one.
    """
    _seed, headers = account

    risks = await client.get("/api/v1/risks", headers=headers)
    recommendations = await client.get("/api/v1/recommendations", headers=headers)
    evaluations = await client.get("/api/v1/intelligence/evaluations", headers=headers)

    assert risks.status_code == 200, risks.text
    assert risks.json() == {
        "items": [],
        "total": 0,
        "limit": 20,
        "offset": 0,
        "by_severity": ZERO_BANDS,
        "summary": "No live risks.",
    }
    assert recommendations.status_code == 200, recommendations.text
    assert recommendations.json() == {
        "items": [],
        "total": 0,
        "limit": 20,
        "offset": 0,
        "by_priority": ZERO_BANDS,
    }
    assert evaluations.status_code == 200, evaluations.text
    assert evaluations.json() == []


async def test_a_fresh_account_can_evaluate_and_is_told_why_it_found_nothing(
    client, db_session, account
):
    """A pass with nothing to measure is a successful answer, not an error.

    The counts are real zeroes because nothing was measured, and the reasons
    travel with them: "not enough recorded activity" is the difference between a
    user in their first fortnight and a user with nothing wrong, and only the
    first can be told from the second if the reasons are on the response.

    ``evaluated`` is **false**, and this is the assertion that proves the flag
    is reachable. It used to be ``True`` here, and the test documented why as an
    accepted limitation: the scheduling detector always could judge, because its
    inputs are four counts and all four are zero on an empty account. That left
    the API permanently answering "I evaluated this account and there is nothing
    to report" for a user who has recorded nothing at all — the exact confusion
    the flag exists to prevent, and the reason ``evaluated: false`` had no path
    through the surface. The scheduling detector now declines when the plan it
    read holds no sessions, so every detector declines here and the answer is
    the honest one.
    """
    _seed, headers = account

    response = await client.post("/api/v1/intelligence/evaluate", headers=headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["evaluated"] is False
    assert body["risks_found"] == 0
    assert body["risks_created"] == 0
    assert body["risks_updated"] == 0
    assert body["risks_resolved"] == 0
    assert body["recommendations_created"] == 0
    assert body["by_severity"] == {}
    assert body["by_type"] == {}
    reason = body["reason_if_not_evaluated"]
    assert "Deadline: Not assessed: no open task carries both a due date" in reason
    assert "Scheduling: No work sessions are recorded in this window" in reason
