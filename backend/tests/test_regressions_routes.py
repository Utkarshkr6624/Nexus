"""Regression tests for six contract bugs fixed in one pass, at the HTTP boundary.

Every defect here is one a client reports as "the API is wrong", not as a crash,
so each is locked down where it is observable: over HTTP, against the real
application and a real PostgreSQL, in one block per defect.

1. ``GET /search?from=&to=`` answered **500 for every timestamp-column kind**.
   ``_date_bound`` read ``value.tzinfo`` off a :class:`datetime.date`, which has
   no such attribute, so any range filter bounded on a ``DateTime`` raised
   ``AttributeError``. Only ``task`` (``due_date``) and ``goal``
   (``target_date``) are ``Date`` columns, and the one existing range test used
   a task — which is how half the filter surface could be broken while the suite
   was green.
2. ``GET /users/`` and ``GET /analytics/export`` resolved the caller through
   ``get_current_user``, which never consults ``sessions``. A bearer from a
   revoked device therefore kept working on both for the whole access-token
   lifetime, including on the listing of every account's email address.
3. ``GET /recommendations?recommendation_type=…`` filtered the page but not the
   ``by_priority`` tally beside it, so the header claimed a total that the list
   next to it had excluded.
4. ``GET /planner/conflicts`` accepted a reversed span and answered 200 with an
   empty list — indistinguishable from "nothing is wrong" — and a ``window``
   echoing the two dates back in the wrong order.
5. ``GET /tasks?tag_ids=`` was **any-of** while the OpenAPI description, the
   repository docstring and ``GET /search?types=task`` all document all-of.
6. ``X-Nexus-Row-Count`` counted CRLFs, so a task title pasted out of a Windows
   editor reported a row that is not in the file.

Every test here is written to fail against the pre-fix code, and each block's
docstring names what the old code did, so a reader who reverts a fix can see
which assertion lights up and why.

House style follows ``tests/test_search_api.py`` and
``tests/test_analytics_export_api.py``: ``pytestmark = pytest.mark.integration``
because every test needs the live PostgreSQL the suite truncates, fixtures come
from ``tests/conftest.py`` and ``tests/analytics_fixtures.py`` rather than
being reinvented, and the test names are full English sentences.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import update

from app.core.config import get_settings
from app.core.security import decode_token
from app.models.planner import CalendarEvent
from app.models.risk import Recommendation
from app.models.tag import Tag, task_tags
from app.models.user import User
from app.repositories.search import _date_bound
from tests.analytics_fixtures import (
    DAY,
    PASSWORD,
    at,
    bearer,
    register_via_api,
    seeded_client,
    sign_in,
)

pytestmark = pytest.mark.integration

#: A term no other fixture in the suite contains, so a hit can only be a row this
#: file wrote. Matches the convention ``tests/test_search_api.py`` sets.
TERM = "zephyr"

#: A password the policy accepts, for the change-password block. Distinct from
#: ``PASSWORD`` so "the old device's session is gone" cannot be confused with
#: "the sign-in failed".
CHANGED_PASSWORD = "Rotated-Fixture-8"

#: The account the two-device block signs in twice.
ADA_EMAIL = "ada@nexus.test"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bearer(pair: dict[str, Any]) -> dict[str, str]:
    """The auth header for a token pair."""
    return bearer(pair["access_token"])


def _subject(pair: dict[str, Any]) -> uuid.UUID:
    """The account id a token pair was minted for."""
    claims = decode_token(pair["access_token"], settings=get_settings())
    return uuid.UUID(claims.subject)


async def _promote(db_session, user_id: uuid.UUID, role: str = "admin") -> None:
    """Set an account's role directly, as an operator or a fixture would."""
    await db_session.execute(update(User).where(User.id == user_id).values(role=role))
    await db_session.commit()


async def _two_devices(client, email: str = ADA_EMAIL) -> tuple[dict, dict]:
    """Register one account and sign it in twice: two real, distinct sessions.

    Explicit rather than implied because the whole point of the block is that a
    revocation is scoped to a device. With only one session there is nothing to
    revoke without also signing the caller out, and every assertion afterwards
    would pass for the wrong reason — or fail, because the one surviving caller
    would have been revoked too.
    """
    await register_via_api(client, username="ada", email=email)
    kept = await sign_in(client, email=email)
    dropped = await sign_in(client, email=email)
    assert kept["session_id"] != dropped["session_id"]
    assert kept["access_token"] != dropped["access_token"]
    return kept, dropped


def _rows_of(body: str) -> list[list[str]]:
    """Parse a CSV document into rows of raw cells.

    ``newline=""`` is load-bearing for the reason
    :func:`tests.test_analytics_export_api.rows_of` gives: the default
    universal-newline translation would rewrite the CRLF record separators and
    the embedded CRLF the count under test is about, and the comparison would
    then be against a document that no longer exists.
    """
    return list(csv.reader(io.StringIO(body, newline="")))


async def _recommendation(
    db_session,
    owner_id: uuid.UUID,
    *,
    recommendation_type: str,
    priority: str,
    title: str,
    status: str = "new",
) -> Recommendation:
    """One suggestion row, written directly and read back.

    The detection engine is not under test here — only what the list route does
    with the rows it reads — so the rows are seeded through the ORM rather than
    by driving the pass that would raise them. That also puts the
    ``(type, priority, status)`` shape of the fixture in one place, which is the
    entire subject of this block.

    Returned rather than looked up afterwards so a caller can change a column
    through the same identity the fixture wrote it under.
    """
    row = Recommendation(
        user_id=owner_id,
        recommendation_type=recommendation_type,
        priority=priority,
        title=title,
        description=f"{recommendation_type} advice",
        reason="the risk score rose",
        status=status,
    )
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)
    return row


async def _tagged_tasks(db_session, seed) -> tuple[Tag, Tag, list[uuid.UUID], list[uuid.UUID]]:
    """Two tags; one task carrying both, one carrying only the first.

    The split is the whole fixture. With a task carrying only one of the two
    tags, an any-of filter returns both and an all-of filter returns one, so the
    two implementations cannot both pass — and the task carrying both is what
    keeps the filter's positive case non-empty.

    Returns the two tags and, in that order, the ids of the both-tags task and
    the one-tag task.
    """
    owner = seed.owner.id
    first = Tag(user_id=owner, name=f"{TERM}-one")
    second = Tag(user_id=owner, name=f"{TERM}-two")
    db_session.add_all([first, second])
    await db_session.commit()
    await db_session.refresh(first)
    await db_session.refresh(second)

    project = await seed.project()
    both = await seed.task(project_id=project.id, title=f"{TERM} carries both tags")
    one = await seed.task(project_id=project.id, title=f"{TERM} carries one tag")
    for task, tag in ((both, first), (both, second), (one, first)):
        await db_session.execute(task_tags.insert().values(task_id=task.id, tag_id=tag.id))
    await db_session.commit()
    return first, second, both.id, one.id


# ---------------------------------------------------------------------------
# 1. A date range over a timestamp column
# ---------------------------------------------------------------------------


async def test_a_date_range_search_succeeds_for_a_kind_whose_date_column_is_a_timestamp(
    non_raising_client, truncated_database, db_session
):
    """``GET /search?from=&to=`` works for ``note``, not only for ``task``.

    ``notes`` is bounded on ``updated_at``, a ``DateTime``, so the day bounds have
    to be widened to instants before they can be compared — and that widening is
    the step that raised ``AttributeError`` and answered 500. ``tasks`` is bounded
    on ``due_date``, a ``Date``, which compares against the bound directly; that
    is the only kind the existing range test exercised.

    ``non_raising_client`` is deliberate. The pre-fix failure rendered a 500
    ``internal_error``, and the default client re-raises the escaping exception
    instead of letting the test read the response a user would have received.

    **The window is three days wide on purpose.** A day bound is written as a
    *naive* midnight — a caller names a calendar day, not an instant — so
    PostgreSQL resolves it in the connection's time zone, which is not UTC
    everywhere. A one-day window would make the assertion below a claim about
    this host's zone setting; three days with the rows well away from either
    edge makes it a claim about the filter.
    """
    seed, auth = await seeded_client(non_raising_client, db_session)

    inside_first = await seed.note(
        day=DAY, title=f"{TERM} inside the window", updated_at=at(DAY, 18)
    )
    inside_last = await seed.note(
        day=DAY + timedelta(days=1),
        title=f"{TERM} also inside the window",
        updated_at=at(DAY + timedelta(days=1), 6),
    )
    earlier = DAY - timedelta(days=5)
    later = DAY + timedelta(days=9)
    before = await seed.note(
        day=earlier, title=f"{TERM} before the window", updated_at=at(earlier, 12)
    )
    after = await seed.note(day=later, title=f"{TERM} after the window", updated_at=at(later, 12))

    response = await non_raising_client.get(
        "/api/v1/search",
        params={
            "q": TERM,
            "types": ["note"],
            "from": (DAY - timedelta(days=1)).isoformat(),
            "to": (DAY + timedelta(days=1)).isoformat(),
        },
        headers=auth,
    )

    assert response.status_code == 200, response.text
    found = {hit["id"] for hit in response.json()["hits"]}
    assert found == {str(inside_first.id), str(inside_last.id)}
    assert str(before.id) not in found
    assert str(after.id) not in found


async def test_a_date_range_search_succeeds_for_a_kind_bounded_on_its_start_time(
    client, db_session
):
    """The same fix for a kind whose bound column is the row's own ``starts_at``.

    ``calendar_events`` is bounded on ``starts_at`` rather than ``updated_at``, so
    it exercises the other half of the routing table — a column the repository
    reads for the filter and *not* for recency. One test per column would be
    repetitive; one test for each shape is enough to say the fix is in the bound
    and not in one particular column.

    The rows are written through the ORM rather than through
    ``AnalyticsSeed.calendar_event`` because the search matches on the title and
    the fixture names events ``event-1``, ``event-2`` — there is nothing to put
    the term in.
    """
    seed, auth = await seeded_client(client, db_session)
    owner = seed.owner.id
    inside = CalendarEvent(
        owner_id=owner,
        title=f"{TERM} inside the window",
        starts_at=at(DAY + timedelta(days=1), 10),
        ends_at=at(DAY + timedelta(days=1), 10, 30),
    )
    outside = CalendarEvent(
        owner_id=owner,
        title=f"{TERM} after the window",
        starts_at=at(DAY + timedelta(days=40), 10),
        ends_at=at(DAY + timedelta(days=40), 10, 30),
    )
    db_session.add_all([inside, outside])
    await db_session.commit()
    await db_session.refresh(inside)
    await db_session.refresh(outside)

    response = await client.get(
        "/api/v1/search",
        params={
            "q": TERM,
            "types": ["event"],
            "from": (DAY - timedelta(days=1)).isoformat(),
            "to": (DAY + timedelta(days=3)).isoformat(),
        },
        headers=auth,
    )

    assert response.status_code == 200, response.text
    found = {hit["id"] for hit in response.json()["hits"]}
    assert found == {str(inside.id)}
    assert str(outside.id) not in found


def test_a_day_bound_widens_to_instants_for_every_timestamp_column():
    """``_date_bound`` accepts the plain ``date`` the route actually hands it.

    The route parses ``?from=``/``?to=`` into :class:`datetime.date`, so this is
    the only value the helper ever sees in production — a ``datetime``, which
    *does* carry ``tzinfo``, would have hidden the defect from any unit test that
    passed one in. Reading the attribute directly raised ``AttributeError` for
    every timestamp-bounded kind.

    Asserted at both ends because they are not symmetric: ``from`` widens to
    midnight and ``to`` to the last microsecond of the day. Writing ``to`` as
    midnight drops everything that happened on that day, which is the kind of
    off-by-one only whoever lost the row notices.
    """
    assert _date_bound(DAY, is_timestamp=True, end_of_day=False) == datetime(2026, 1, 5)
    assert _date_bound(DAY, is_timestamp=True, end_of_day=True) == datetime(
        2026, 1, 5, 23, 59, 59, 999_999
    )

    # A ``Date`` column takes the bound as it arrived — no widening, and so no
    # attribute to read.
    assert _date_bound(DAY, is_timestamp=False, end_of_day=True) == DAY
    assert _date_bound(None, is_timestamp=True, end_of_day=True) is None

    # A timezone-aware bound keeps its zone rather than being flattened to naive,
    # and is normalised to the midnight the bound names.
    aware = datetime(2026, 1, 5, 6, 0, tzinfo=UTC)
    widened = _date_bound(aware, is_timestamp=True, end_of_day=False)
    assert widened == datetime(2026, 1, 5, tzinfo=UTC)
    assert widened.tzinfo is not None
    assert _date_bound(DAY, is_timestamp=True, end_of_day=False).tzinfo is None


# ---------------------------------------------------------------------------
# 2. Revocation reaches the routes that resolved identity without the table
# ---------------------------------------------------------------------------


async def test_the_account_listing_answers_401_once_the_device_is_revoked(
    client, db_session, assert_error_envelope
):
    """A revoked device cannot read the administrative listing of every account.

    ``GET /users/`` resolves the caller through ``get_current_user``, which
    checks the signature, the subject and the account and reads nothing else — it
    never consults ``sessions``. Revoking a device therefore ended only that
    device's *refresh* credential: every access token already minted from it went
    on authorising this listing, and every account's email address with it, for
    the whole ``ACCESS_TOKEN_EXPIRE_MINUTES``. Before the fix this answered 200.

    The account is promoted to ``admin`` first so the two permission gates pass
    and the 401 can only come from the session check; otherwise the route would
    answer 403 for an unrelated reason and prove nothing.

    The surviving device is asserted still working, so a route that refused
    everybody could not satisfy this.
    """
    kept, dropped = await _two_devices(client)
    await _promote(db_session, _subject(kept), "admin")

    # The dropped device's token is genuinely valid before the revocation: a 401
    # afterwards caused by anything else would be indistinguishable from the fix.
    assert (await client.get("/api/v1/users/", headers=_bearer(dropped))).status_code == 200

    revoked = await client.delete(
        f"/api/v1/auth/sessions/{dropped['session_id']}", headers=_bearer(kept)
    )
    assert revoked.status_code == 204, revoked.text

    error = assert_error_envelope(
        await client.get("/api/v1/users/", headers=_bearer(dropped)),
        status_code=401,
        code="unauthorized",
    )
    assert error["details"] is None
    assert (await client.get("/api/v1/users/", headers=_bearer(kept))).status_code == 200


async def test_the_export_manifest_answers_401_after_signing_out_every_other_device(
    client, db_session, assert_error_envelope
):
    """``POST /auth/logout-all`` ends these bearers too, not just their refreshes.

    The manifest is the second route that resolved identity through
    ``get_current_user`` alone, so a signed-out device went on being told the
    shape of every export for another hour.

    The comparison with ``/auth/me`` is the load-bearing part. ``/auth/me`` has
    always consulted the session table, so it already answered 401 in exactly
    this situation; the two routes now return the same answer instead of each
    deciding for itself, which is what "revocation means what the UI says it
    means" actually requires.
    """
    kept, dropped = await _two_devices(client)

    signed_out = await client.post("/api/v1/auth/logout-all", headers=_bearer(kept))
    assert signed_out.status_code == 204, signed_out.text

    assert_error_envelope(
        await client.get("/api/v1/analytics/export", headers=_bearer(dropped)),
        status_code=401,
        code="unauthorized",
    )
    me = await client.get("/api/v1/auth/me", headers=_bearer(dropped))
    assert me.status_code == 401, "the manifest and /auth/me must now agree"

    # The device that issued the sign-out keeps working, and so does the export
    # itself — a route that refused every caller would satisfy the lines above.
    assert (await client.get("/api/v1/auth/me", headers=_bearer(kept))).status_code == 200
    manifest = await client.get("/api/v1/analytics/export", headers=_bearer(kept))
    assert manifest.status_code == 200, manifest.text
    assert manifest.json()["datasets"] == ["daily_metrics", "task_performance", "work_sessions"]


async def test_a_password_changed_on_another_device_ends_the_bearer_there_too(
    client, db_session, assert_error_envelope
):
    """Changing the password on one device signs the other device's bearer out.

    The third way a session stops being live, and the one a user reaches for
    after a suspected compromise. It calls the same revocation the other two do,
    so before the fix these routes kept answering 200 with a token the account
    owner had already locked down.
    """
    kept, dropped = await _two_devices(client)
    await _promote(db_session, _subject(kept), "admin")

    changed = await client.patch(
        "/api/v1/auth/password",
        json={"current_password": PASSWORD, "new_password": CHANGED_PASSWORD},
        headers=_bearer(kept),
    )
    assert changed.status_code == 204, changed.text

    assert_error_envelope(
        await client.get("/api/v1/analytics/export", headers=_bearer(dropped)),
        status_code=401,
        code="unauthorized",
    )
    assert_error_envelope(
        await client.get("/api/v1/users/", headers=_bearer(dropped)),
        status_code=401,
        code="unauthorized",
    )
    assert (await client.get("/api/v1/analytics/export", headers=_bearer(kept))).status_code == 200


# ---------------------------------------------------------------------------
# 3. ``by_priority`` describes the filtered set
# ---------------------------------------------------------------------------


async def test_by_priority_sums_to_total_when_the_page_is_filtered_by_type(client, db_session):
    """``sum(by_priority.values()) == total`` under a ``recommendation_type`` filter.

    ``RecommendationListRead`` carries the tally beside ``total`` in one
    envelope, and that is the whole reason to keep them in step: a client
    rendering "12 suggestions — 4 critical, 3 high, …" is making a claim about
    the list it just fetched. ``count_by_priority`` ignored the ``types`` filter,
    so the claim was about a wider set than the page, and the header counted rows
    the list had excluded.

    The fixture is shaped so the two numbers cannot coincide by accident: five
    rows, two of them of the filtered-out type, and one of those two sitting in
    a band (``critical``) that the filtered type never uses.
    """
    seed, auth = await seeded_client(client, db_session)
    owner = seed.owner.id

    wanted = [
        await _recommendation(
            db_session,
            owner,
            recommendation_type="review_deadline",
            priority=priority,
            title=f"{TERM} wanted {priority}",
        )
        for priority in ("high", "medium", "low")
    ]
    unwanted = [
        await _recommendation(
            db_session,
            owner,
            recommendation_type="start_task",
            priority=priority,
            title=f"{TERM} unwanted {priority}",
        )
        for priority in ("critical", "high")
    ]

    response = await client.get(
        "/api/v1/recommendations",
        params={"recommendation_type": "review_deadline"},
        headers=auth,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == len(wanted)
    assert sum(body["by_priority"].values()) == body["total"], body["by_priority"]
    # Every band is present whatever the filter returned, so the response shape
    # does not change as the last open suggestion is closed.
    assert body["by_priority"] == {"critical": 0, "high": 1, "medium": 1, "low": 1}
    assert {row["id"] for row in body["items"]} == {str(row.id) for row in wanted}
    assert not {str(row.id) for row in unwanted} & {row["id"] for row in body["items"]}


async def test_by_priority_sums_to_total_under_a_status_filter_too(client, db_session):
    """The invariant is a property of the envelope, not of one filter.

    ``status`` was already honoured by ``count_by_priority``, so this passes
    before and after. It is here as the control: a regression that broke both
    filters would fail here first, which is what makes this a control rather than
    a duplicate of the test above.
    """
    seed, auth = await seeded_client(client, db_session)
    owner = seed.owner.id
    await _recommendation(
        db_session,
        owner,
        recommendation_type="review_deadline",
        priority="high",
        title=f"{TERM} still open",
    )
    accepted = await _recommendation(
        db_session,
        owner,
        recommendation_type="review_deadline",
        priority="low",
        title=f"{TERM} accepted",
        status="accepted",
    )
    assert accepted.status == "accepted"

    response = await client.get(
        "/api/v1/recommendations", params={"status": "accepted"}, headers=auth
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert sum(body["by_priority"].values()) == body["total"], body["by_priority"]
    assert body["by_priority"] == {"critical": 0, "high": 0, "medium": 0, "low": 1}
    assert [row["id"] for row in body["items"]] == [str(accepted.id)]


# ---------------------------------------------------------------------------
# 4. A reversed span is refused, not scanned
# ---------------------------------------------------------------------------


async def test_planner_conflicts_refuses_a_reversed_span(client, db_session, assert_error_envelope):
    """``start`` after ``end`` is a 422, like the routes that already refused it.

    The route used to scan it and answer 200 with an empty ``conflicts`` list — a
    clean-looking "nothing is wrong" for a window that was never inspected. An
    empty list is a real answer elsewhere on this route, so the two could not be
    told apart from the response.

    The sibling is asked in the same state and must agree on the status and the
    error code. It is a sibling rather than a copy because it refuses at a
    different layer, so asserting both pins "every route refuses a reversed
    window" rather than "this one does"; the messages differ because the two
    routes name different parameters, which is why the message is compared only
    within the conflicts route.
    """
    _, auth = await seeded_client(client, db_session)
    start = DAY + timedelta(days=5)
    end = DAY

    error = assert_error_envelope(
        await client.get(
            "/api/v1/planner/conflicts",
            params={"start": start.isoformat(), "end": end.isoformat()},
            headers=auth,
        ),
        status_code=422,
        code="validation_error",
    )
    assert "earlier" in error["message"], error["message"]

    sibling = await client.get(
        "/api/v1/calendar/events",
        params={
            "from": f"{start.isoformat()}T09:00:00+00:00",
            "to": f"{end.isoformat()}T09:00:00+00:00",
        },
        headers=auth,
    )
    assert sibling.status_code == 422, sibling.text
    assert sibling.json()["error"]["code"] == error["code"]


async def test_planner_conflicts_still_scans_a_span_that_is_the_right_way_round(client, db_session):
    """The refusal is the reversal, not the route.

    A guard that rejected every span would satisfy the test above. A two-day span
    is scanned and comes back 200 with the window it was asked about, and the
    same two days with their order swapped are refused, so the boundary is
    exactly ``end == start`` — a one-day span cannot show that, because
    swapping it changes nothing.
    """
    _, auth = await seeded_client(client, db_session)
    span = {
        "start": DAY.isoformat(),
        "end": (DAY + timedelta(days=2)).isoformat(),
    }

    response = await client.get("/api/v1/planner/conflicts", params=span, headers=auth)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["conflicts"] == []
    assert body["window"]["start_date"] == DAY.isoformat()
    assert body["window"]["end_date"] == (DAY + timedelta(days=2)).isoformat()

    swapped = {"start": span["end"], "end": span["start"]}
    assert (
        await client.get("/api/v1/planner/conflicts", params=swapped, headers=auth)
    ).status_code == 422


# ---------------------------------------------------------------------------
# 5. ``tag_ids`` means all-of on ``/tasks`` too
# ---------------------------------------------------------------------------


async def test_tasks_tag_ids_requires_every_tag_listed(client, db_session):
    """A task carrying only one of the two tags is excluded.

    The OpenAPI description on the route says "A task must carry every tag
    listed", the repository docstring says the same, and
    ``GET /search?types=task`` behaved that way — but the filter was an ``IN``
    over ``task_tags``, which is any-of. So "in these two projects" and "in any
    of these two projects" were the same question on this route and different
    questions everywhere else.

    ``meta.total`` is asserted alongside the ids because a filter that matched
    nothing would return an empty page and satisfy an ids-only assertion.
    """
    seed, auth = await seeded_client(client, db_session)
    first, second, both_id, one_id = await _tagged_tasks(db_session, seed)

    response = await client.get(
        "/api/v1/tasks", params={"tag_ids": [str(first.id), str(second.id)]}, headers=auth
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["id"] for row in body["items"]] == [str(both_id)]
    assert body["meta"]["total"] == 1
    assert str(one_id) not in {row["id"] for row in body["items"]}


async def test_the_task_list_and_the_search_now_agree_on_the_same_tag_filter(client, db_session):
    """The identical filter returns the identical set from both surfaces.

    The two routes are separate projections over the same ``task_tags`` table, so
    "tasks in these two tags" must not mean two different things depending on
    which one the client happened to open. Before the fix this compared one task
    against two.
    """
    seed, auth = await seeded_client(client, db_session)
    first, second, both_id, one_id = await _tagged_tasks(db_session, seed)

    listed = await client.get(
        "/api/v1/tasks", params={"tag_ids": [str(first.id), str(second.id)]}, headers=auth
    )
    searched = await client.get(
        "/api/v1/search",
        params={"q": TERM, "types": ["task"], "tag_ids": [str(first.id), str(second.id)]},
        headers=auth,
    )

    assert listed.status_code == searched.status_code == 200, searched.text
    from_list = {row["id"] for row in listed.json()["items"]}
    from_search = {hit["id"] for hit in searched.json()["hits"]}
    assert from_list == from_search == {str(both_id)}
    assert str(one_id) not in from_list | from_search


async def test_one_tag_still_matches_every_task_that_carries_it(client, db_session):
    """All-of over a single tag is the ordinary question, and it still works.

    The control for the two above: a filter that matched nothing at every arity
    would satisfy both. With one tag, the two seeded tasks both carry it and
    both must come back, and the second tag on its own is a different question
    with a different answer.
    """
    seed, auth = await seeded_client(client, db_session)
    first, second, both_id, one_id = await _tagged_tasks(db_session, seed)

    response = await client.get("/api/v1/tasks", params={"tag_ids": [str(first.id)]}, headers=auth)

    assert response.status_code == 200, response.text
    body = response.json()
    assert {row["id"] for row in body["items"]} == {str(both_id), str(one_id)}
    assert body["meta"]["total"] == 2

    only_second = await client.get(
        "/api/v1/tasks", params={"tag_ids": [str(second.id)]}, headers=auth
    )
    assert only_second.status_code == 200, only_second.text
    assert [row["id"] for row in only_second.json()["items"]] == [str(both_id)]


# ---------------------------------------------------------------------------
# 6. The row count is a count of records
# ---------------------------------------------------------------------------


async def test_the_row_count_ignores_a_crlf_inside_an_exported_field(client, db_session):
    r"""``X-Nexus-Row-Count`` equals the number of data rows, newline or not.

    The count was ``body.count("\r\n") - 1``. RFC 4180 quoting means a field
    containing CRLF keeps its newline inside the quotes rather than escaping it,
    so a task title pasted out of a Windows editor put an extra CRLF into the
    document and the header reported a row the file does not contain — from the
    one figure a client uses to say "exported N rows" without parsing the body.

    Two tasks, one of them carrying a CRLF, so the answer is 2 rather than 1.
    That distinguishes "counted records" from an off-by-one on the header, which
    a single-task version could not.
    """
    seed, auth = await seeded_client(client, db_session)
    project = await seed.project()
    awkward = "first line\r\nsecond line"
    await seed.task(project_id=project.id, title=awkward, created_at=at(DAY, 8))
    await seed.task(project_id=project.id, title=f"{TERM} ordinary", created_at=at(DAY, 9))

    response = await client.get(
        "/api/v1/analytics/export.csv",
        params={
            "dataset": "task_performance",
            "start_date": DAY.isoformat(),
            "end_date": DAY.isoformat(),
        },
        headers=auth,
    )

    assert response.status_code == 200, response.text
    header, *data = _rows_of(response.text)
    assert len(data) == 2, data
    assert response.headers["X-Nexus-Row-Count"] == str(len(data))
    # The embedded newline survived the round trip, which is the reason the naive
    # count was wrong rather than merely imprecise.
    assert sorted(row[header.index("title")] for row in data) == sorted(
        [awkward, f"{TERM} ordinary"]
    )


async def test_the_row_count_is_zero_for_a_window_with_no_rows(client, db_session):
    """The boundary of the fix: an empty export is still zero, not minus one.

    The count drops the header row, so a reader that counted records without
    excluding the header would report ``-1`` here. This is what says the header
    is excluded by design rather than by accident of the arithmetic.
    """
    _, auth = await seeded_client(client, db_session)

    response = await client.get(
        "/api/v1/analytics/export.csv",
        params={
            "dataset": "task_performance",
            "start_date": DAY.isoformat(),
            "end_date": DAY.isoformat(),
        },
        headers=auth,
    )

    assert response.status_code == 200, response.text
    assert response.headers["X-Nexus-Row-Count"] == "0"
    assert len(_rows_of(response.text)) == 1


# ---------------------------------------------------------------------------
# The fixtures themselves
# ---------------------------------------------------------------------------


def test_the_anchor_day_is_a_date_the_export_window_can_express():
    """``DAY`` is a ``date``, and a three-day window around it is well ordered.

    Stated once, here, rather than asserted inside every test that uses it: the
    export block's window arithmetic and the search block's slack depend on it,
    and a silent change to the anchor would make both fail for a reason that
    has nothing to do with the behaviour under test.
    """
    assert isinstance(DAY, date)
    assert DAY + timedelta(days=1) == DAY.replace(day=6)
    assert DAY - timedelta(days=1) < DAY < DAY + timedelta(days=1)
