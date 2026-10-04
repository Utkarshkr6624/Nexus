"""``compute_overload`` measures elapsed time, whatever zone the rows are labelled in.

What was wrong
--------------
The day's committed minutes were computed as::

    overlap = min(_aware(row.scheduled_end), end_at) - max(_aware(row.scheduled_start), start_at)

Python does not always subtract two aware datetimes by their instants. When both
carry the **same** ``tzinfo``, the shared zone is *ignored* and the wall-clock
fields are subtracted directly, so the offset change inside the span — and a
clock change inside it — is thrown away.

Rows read back from a ``timestamptz`` column arrive labelled UTC, where the two
rules agree, so the defect was invisible through the API. It was visible to a
caller of the pure function, which is a documented public entry point
(:func:`app.services.planner_service.compute_overload`, re-exported from
``app.services`` and reachable as ``PlannerService.overload_for_day``): hand it a
session carrying its own offset and the same two instants come back as a
different number of minutes.

The worked case, Berlin's spring-forward on 2026-03-29:

===========================  =====================  ====================
what                         ``scheduled_start``    ``scheduled_end``
===========================  =====================  ====================
wall clock the user booked   ``00:00+01:00``        ``04:00+02:00``
the same two instants in UTC ``2026-03-28T23:00Z``  ``2026-03-29T02:00Z``
===========================  =====================  ====================

02:00Z minus 23:00Z is **three** hours. The old code returned **four**, because
it subtracted ``04:00`` from ``00:00`` in Berlin and the missing hour between them
never entered the arithmetic. The user is told they have committed a quarter of
an hour more of their day than they did.

The fall-back day errs the same way in the other direction: a 00:00-04:00 booking
on 2026-10-25 is **five** real hours (00:00 CEST is 22:00Z, 04:00 CET is 03:00Z)
and the old code reported four.

What is asserted
----------------
Four hand-derived figures, each stated in its own test with the arithmetic that
produced it, plus the equality that is the actual point: the same two instants
cost the same minutes whether they are labelled in the owner's zone or in UTC.
Everything else about :func:`compute_overload` is exercised by
``tests/test_planner_api.py`` against the database.

House style, following ``tests/test_risk_scoring.py``: no database, no HTTP, no
``integration`` marker. The rows are hand-built doubles because the point is the
arithmetic, and the arithmetic does not care where the row came from.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.services.planner_service import compute_overload, day_bounds

BERLIN = ZoneInfo("Europe/Berlin")

#: The spring-forward day in Berlin: local clocks jump 02:00 -> 03:00, so the
#: local day is 23 real hours long.
SPRING_FORWARD = date(2026, 3, 29)
#: The fall-back day in Berlin: 03:00 -> 02:00, so the local day is 25 hours long.
FALL_BACK = date(2026, 10, 25)
#: An ordinary day, so the tests that are not about a clock change have somewhere
#: to be ordinary.
ORDINARY_DAY = date(2026, 7, 15)


class _Rule:
    """An availability rule, reduced to the three fields the function reads."""

    def __init__(self, weekday: int, starts_at: time, ends_at: time) -> None:
        self.weekday = weekday
        self.starts_at = starts_at
        self.ends_at = ends_at


class _Session:
    """A work session, reduced to the three fields the function reads."""

    def __init__(self, scheduled_start: datetime, scheduled_end: datetime, status: str) -> None:
        self.scheduled_start = scheduled_start
        self.scheduled_end = scheduled_end
        self.status = status


def _availability(day: date, start: time, end: time) -> list[_Rule]:
    """One window on ``day``, expressed in wall-clock time as the schema stores it."""
    return [_Rule(day.weekday(), start, end)]


def _session(start: datetime, end: datetime) -> list[_Session]:
    return [_Session(start, end, "planned")]


# ---------------------------------------------------------------------------
# The days themselves
# ---------------------------------------------------------------------------


def test_day_bounds_are_23_and_25_real_hours_on_the_two_transition_days() -> None:
    """The expected values below are only surprising without these numbers.

    ``day_bounds`` builds each window from the wall clock and converts once, which
    is the right way round: a UTC-midnight window would be two hours out for a
    Berlin reader in summer and would put a 23:30 local session on the wrong row.
    """
    start, end = day_bounds(SPRING_FORWARD, BERLIN)
    assert (start, end) == (
        datetime(2026, 3, 28, 23, 0, tzinfo=UTC),
        datetime(2026, 3, 29, 22, 0, tzinfo=UTC),
    )
    assert end - start == timedelta(hours=23)

    start, end = day_bounds(FALL_BACK, BERLIN)
    assert (start, end) == (
        datetime(2026, 10, 24, 22, 0, tzinfo=UTC),
        datetime(2026, 10, 25, 23, 0, tzinfo=UTC),
    )
    assert end - start == timedelta(hours=25)


# ---------------------------------------------------------------------------
# The four figures
# ---------------------------------------------------------------------------


def test_a_booking_across_the_spring_forward_costs_the_hours_that_elapsed() -> None:
    """00:00-04:00 local on the spring-forward day is three real hours.

    00:00 in Berlin is still CET (+01:00), so that instant is 2026-03-28T23:00Z.
    04:00 is already CEST (+02:00), so it is 2026-03-29T02:00Z. The elapsed
    distance is 23:00Z -> 02:00Z: three hours, 180 minutes.

    The wall clock says four hours, and the wall clock is not wrong about what the
    user asked for — but the day this lands in is 23 hours long, so charging it
    240 minutes reports an hour of commitment that did not happen.

    ``available`` is **240** rather than 180 because the availability rules are
    wall-clock ``TIME`` columns and the schema cannot say anything else; that
    mismatch is the two sides of the ratio answering in different units on a
    transition day, and it is stated here rather than papered over. It is bounded
    by the length of the transition and affects neither an ordinary day nor any
    number stored anywhere.

    Before the fix this returned 240.
    """
    load = compute_overload(
        day=SPRING_FORWARD,
        availability=_availability(SPRING_FORWARD, time(0), time(4)),
        sessions=_session(
            datetime(2026, 3, 29, 0, 0, tzinfo=BERLIN),
            datetime(2026, 3, 29, 4, 0, tzinfo=BERLIN),
        ),
        owner_tz="Europe/Berlin",
    )
    assert load.scheduled == 180
    assert load.available == 240
    assert load.ratio == 0.75
    assert load.overload_minutes == 0


def test_a_booking_across_the_fall_back_costs_the_hours_that_elapsed() -> None:
    """00:00-04:00 local on the fall-back day is five real hours.

    The transition runs the other way: 00:00 in Berlin is CEST (+02:00), so it is
    2026-10-24T22:00Z, while 04:00 is already CET (+01:00), so it is
    2026-10-25T03:00Z. 22:00Z -> 03:00Z is five hours, 300 minutes — the repeated
    hour is real time the user had, and the day is 25 hours long.

    Before the fix this returned 240.
    """
    load = compute_overload(
        day=FALL_BACK,
        availability=_availability(FALL_BACK, time(0), time(4)),
        sessions=_session(
            datetime(2026, 10, 25, 0, 0, tzinfo=BERLIN),
            datetime(2026, 10, 25, 4, 0, tzinfo=BERLIN),
        ),
        owner_tz="Europe/Berlin",
    )
    assert load.scheduled == 300
    assert load.available == 240
    assert load.ratio == 1.25
    assert load.overload_minutes == 60


def test_the_same_two_instants_cost_the_same_whatever_zone_they_are_labelled_in() -> None:
    """The spring-forward pair, written in UTC instead of in Berlin.

    This is the assertion the other three support. The function is pure, so the
    only thing that can differ between these two calls is how the operands are
    labelled — and the answer must not depend on that. Before the fix the Berlin
    call said 240 and this one said 180, which is the defect stated in the module
    docstring in one line.
    """
    labelled_in_berlin = compute_overload(
        day=SPRING_FORWARD,
        availability=_availability(SPRING_FORWARD, time(0), time(4)),
        sessions=_session(
            datetime(2026, 3, 29, 0, 0, tzinfo=BERLIN),
            datetime(2026, 3, 29, 4, 0, tzinfo=BERLIN),
        ),
        owner_tz="Europe/Berlin",
    )
    labelled_in_utc = compute_overload(
        day=SPRING_FORWARD,
        availability=_availability(SPRING_FORWARD, time(0), time(4)),
        sessions=_session(
            datetime(2026, 3, 28, 23, 0, tzinfo=UTC),
            datetime(2026, 3, 29, 2, 0, tzinfo=UTC),
        ),
        owner_tz="Europe/Berlin",
    )
    assert labelled_in_utc.scheduled == 180
    assert labelled_in_berlin.scheduled == labelled_in_utc.scheduled


def test_a_session_covering_a_whole_transition_day_costs_the_whole_day() -> None:
    """25 real hours on the fall-back day, not the 1440 the wall clock implies.

    The session starts at local midnight and ends at the next local midnight, so
    its share of the day is the whole day: 2026-10-24T22:00Z to
    2026-10-25T23:00Z, which is 25 hours, 1500 minutes.
    """
    load = compute_overload(
        day=FALL_BACK,
        availability=_availability(FALL_BACK, time(0), time(23, 59)),
        sessions=_session(
            datetime(2026, 10, 24, 22, 0, tzinfo=UTC),
            datetime(2026, 10, 25, 23, 0, tzinfo=UTC),
        ),
        owner_tz="Europe/Berlin",
    )
    assert load.scheduled == 1500


# ---------------------------------------------------------------------------
# The ordinary cases, so the fix is pinned as a narrowing and not a widening
# ---------------------------------------------------------------------------


def test_an_ordinary_day_is_unchanged() -> None:
    """Two hours booked against an eight-hour window: 120 of 480, ratio 0.25."""
    load = compute_overload(
        day=ORDINARY_DAY,
        availability=_availability(ORDINARY_DAY, time(9), time(17)),
        sessions=_session(
            datetime(2026, 7, 15, 9, 0, tzinfo=UTC),
            datetime(2026, 7, 15, 11, 0, tzinfo=UTC),
        ),
        owner_tz="UTC",
    )
    assert (load.available, load.scheduled, load.overload_minutes) == (480, 120, 0)
    assert load.ratio == 0.25


def test_a_session_labelled_in_another_zone_still_clips_to_the_local_day() -> None:
    """23:30-01:30 Berlin on the 15th is 21:30Z to 23:30Z.

    Only the half hour from 22:00Z to 23:30Z falls inside the 15th's window
    (which runs 2026-07-14T22:00Z to 2026-07-15T22:00Z), so the 15th is credited
    with 30 minutes and the 16th with the other 90.
    """
    availability: list[Any] = _availability(ORDINARY_DAY, time(0), time(23, 59))
    start = datetime(2026, 7, 15, 21, 30, tzinfo=UTC)
    end = datetime(2026, 7, 15, 23, 30, tzinfo=UTC)
    on_the_15th = compute_overload(
        day=ORDINARY_DAY,
        availability=availability,
        sessions=_session(start, end),
        owner_tz="Europe/Berlin",
    )
    on_the_16th = compute_overload(
        day=ORDINARY_DAY + timedelta(days=1),
        availability=_availability(ORDINARY_DAY + timedelta(days=1), time(0), time(23, 59)),
        sessions=_session(start, end),
        owner_tz="Europe/Berlin",
    )
    assert on_the_15th.scheduled == 30
    assert on_the_16th.scheduled == 90
