"""Planner endpoints: the day, the week, the month, and what to put in them.

Where the rules live
--------------------
This file is a translation layer, exactly as ``app/api/v1/projects.py`` is. The
windows, the bucketing, the overload arithmetic and the scheduling engine all
belong to :class:`~app.services.planner_service.PlannerService`; this router
validates a query, picks a status code and hands the result back. It imports no
repository, and the one domain error it raises is the impossible-span refusal
:func:`list_conflicts` documents — a query the shape of which no service could
answer, checked here so every route refuses it the same way.

Timezones — the rule this whole router exists to honour
--------------------------------------------------------
**Every stored instant is timezone-aware UTC, and nothing here silently shifts
one.** A *day* is not an instant: "Tuesday" means midnight-to-midnight in some
particular zone, and the only way to know which is for the client to say. So:

* ``?tz=`` names an **IANA** zone and defaults to
  ``settings.planner_default_timezone``.
* An **unknown zone is a 422, never a fallback to UTC.** A silent fallback would
  move every day boundary by that zone's offset and answer a well-formed
  question with a different question's data; the user would see a calendar in
  the wrong hours and no error explaining why.
* The window is computed as **local midnight to the next local midnight**, and
  only then converted to UTC for the query. Deriving it arithmetically in UTC
  instead would put a 23:30 local session on the previous day for anyone east of
  Greenwich — the single worst bug a calendar can have.
* Every returned instant keeps its offset, and every response carries the local
  day boundaries (``window``) that produced it, so a client never has to guess
  which days were considered or in whose time they were counted.

Where an event/session payload differs
--------------------------------------
``GET /api/v1/calendar/events`` has **no** ``tz``: an event's ``starts_at`` is an
instant and is returned as the instant that was stored. Converting it into the
reader's zone is a client decision made with the user's own zone, and a server
that re-expressed it would make the stored value unobservable. The planner
routes are asked *in a zone* precisely because a day is not an instant.

Why ``date``/``week_start``/``month`` are required rather than defaulting to today
-------------------------------------------------------------------------------
Every "now" in this phase is read from the **database** clock
(``func.now()``), because a host whose clock drifts from the server's would
otherwise put a session in the wrong day for a user whose work starts at
midnight. A default here would have to read that clock from the router, and a
router that owns a clock reading is a second source of "now" in the system. The
caller already knows what day it is looking at; making it say so keeps the
database the only clock, and a wrong default is a wrong *day of results*, which
is worse than a missing parameter.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.api.deps import AuthenticatedUser, PlannerServiceDep, SchedulingServiceDep
from app.core.deps import require_permission
from app.core.exceptions import ValidationError
from app.core.permissions import Permission
from app.schemas.common import PageMeta
from app.schemas.planner import (
    ConflictList,
    PlannerDay,
    PlannerWeek,
    PlannerWindow,
    SuggestionResponse,
)
from app.services.planner_service import resolve_timezone
from app.services.scheduling_service import MAX_CONFLICTS

router = APIRouter(prefix="/planner", tags=["planner"])

#: The conflict list's page metadata. Nothing here is paged — the span bounds
#: the answer — but :class:`~app.schemas.planner.ConflictList` carries ``meta``,
#: and a fabricated ``limit`` would be a claim the endpoint does not honour.
#: ``MAX_CONFLICTS`` is imported from the engine rather than re-declared, so
#: ``meta.limit`` always reports the cap that was actually applied.

_TZ_DESCRIPTION = (
    "IANA zone the day boundaries are computed in; defaults to "
    "settings.planner_default_timezone. An unknown zone is a 422, never a "
    "silent fallback to UTC."
)


class PlannerMonth(BaseModel):
    """Every day of one month, plus the window they were computed for.

    ``PlannerService.month`` returns the days; the wrapper is added here so the
    month view carries the same ``window`` the day and week views do. Without it
    a client rendering a month could not tell which zone the days were bucketed
    in, and would have to re-derive the answer it just asked for.
    """

    days: list[PlannerDay] = Field(default_factory=list)
    window: PlannerWindow


@router.get(
    "/day",
    response_model=PlannerDay,
    summary="One local calendar day",
    dependencies=[Depends(require_permission(Permission.CALENDAR_READ))],
)
async def get_day(
    current_user: AuthenticatedUser,
    planner: PlannerServiceDep,
    day: Annotated[
        date, Query(alias="date", description="The local day to return, as `YYYY-MM-DD`.")
    ],
    tz: Annotated[str | None, Query(description=_TZ_DESCRIPTION)] = None,
) -> PlannerDay:
    """Return one local day: its events, its sessions and its capacity.

    **The window is local midnight to the next local midnight in ``tz``,**
    converted to UTC only for the query. Everything in ``events``/``sessions``
    *overlaps* that window, so a block begun at 23:00 the night before and
    running past midnight is on this day's calendar.

    **``available_minutes`` is ``None`` when the user has declared no hours for
    that weekday**, not ``0``. "No hours declared" and "declared zero hours" are
    different answers, and only the second one means the day is overloaded; with
    ``None``, ``overload_minutes`` is ``None`` and ``overloaded`` is ``False``.
    ``ratio`` is likewise ``None`` rather than NaN or infinity for the same
    reason — a number that cannot be expressed is a ``None``, so no client has to
    read the field defensively.

    Errors: 422 for a malformed ``date`` or an unknown ``tz``.
    """
    return await planner.day(owner=current_user, day=day, tz=tz)


@router.get(
    "/week",
    response_model=PlannerWeek,
    summary="Seven days of the planner",
    dependencies=[Depends(require_permission(Permission.CALENDAR_READ))],
)
async def get_week(
    current_user: AuthenticatedUser,
    planner: PlannerServiceDep,
    week_start: Annotated[date, Query(description="First day of the week, as `YYYY-MM-DD`.")],
    tz: Annotated[str | None, Query(description=_TZ_DESCRIPTION)] = None,
) -> PlannerWeek:
    """Return seven consecutive days from ``week_start``, with totals.

    **``week_start`` is taken as given rather than snapped to Monday.** A client
    that wants a Sunday-start week gets one, and it already knows which day it
    asked for; silently re-anchoring would make the response disagree with the
    request the caller can still see.

    The span is ``week_start`` through ``week_start + 6 days`` inclusive of the
    seventh, each day computed in ``tz``'s local midnight.

    ``totals.available_minutes`` is ``None`` when *no* day in the week has
    declared hours — the same distinction as the day view, summed rather than
    lost.

    Errors: 422 for a malformed ``week_start`` or an unknown ``tz``.
    """
    return await planner.week(owner=current_user, week_start=week_start, tz=tz)


@router.get(
    "/month",
    response_model=PlannerMonth,
    summary="Every day of one month",
    dependencies=[Depends(require_permission(Permission.CALENDAR_READ))],
)
async def get_month(
    current_user: AuthenticatedUser,
    planner: PlannerServiceDep,
    month: Annotated[str, Query(description="Calendar month as `YYYY-MM`.")],
    tz: Annotated[str | None, Query(description=_TZ_DESCRIPTION)] = None,
) -> PlannerMonth:
    """Return every day of ``month`` (28-31 of them) as a planner day.

    **One range query per table for the whole month, then bucketed in Python.**
    A query per day would be sixty round trips, and the buckets would be free to
    disagree with each other mid-write — a month where one day showed an event
    that had already moved into the next.

    **Not paginated.** The row count is bounded by the calendar (28-31), so a
    ``limit`` here could only ever be reached by a caller that already has the
    whole answer in one response.

    ``month`` is validated against ``YYYY-MM`` by the service rather than being
    parsed leniently: a reader that guessed whether ``"2026-3"`` meant March or
    the third of some year would be a coin toss dressed as a filter.

    Errors: 422 for a malformed ``month`` or an unknown ``tz``.
    """
    days = await planner.month(owner=current_user, month=month, tz=tz)
    return PlannerMonth(
        days=days,
        window=PlannerWindow(
            start_date=days[0].date,
            end_date=days[-1].date,
            timezone=str(planner.resolve_timezone(tz, settings=planner.settings)),
        ),
    )


@router.post(
    "/suggestions",
    response_model=SuggestionResponse,
    summary="Propose slots for the caller's open tasks",
    dependencies=[Depends(require_permission(Permission.CALENDAR_READ))],
)
async def suggest_slots(
    current_user: AuthenticatedUser,
    scheduling: SchedulingServiceDep,
    tz: Annotated[str | None, Query(description=_TZ_DESCRIPTION)] = None,
    task_ids: Annotated[
        list[UUID] | None,
        Query(description="Restrict to these tasks. Repeatable."),
    ] = None,
) -> SuggestionResponse:
    """Propose placements for the caller's own open, estimated, dated tasks.

    **This is a deterministic engine, not a model.** Nothing here calls out:
    the same backlog, the same calendar and the same reference instant always
    produce the same list in the same order, which is what makes a suggestion
    arguable — a user can re-run the request and get the same answer, or hand
    two people the same backlog and get the same plan.

    **Every suggestion carries a `reason` and its `evidence`.** An engine that
    cannot say why it put work at 14:00 on Tuesday is indistinguishable from one
    that guessed, and a user who books time on the strength of an unexplained
    slot has been given a claim rather than an argument. The evidence carries the
    inputs the decision was made from — the due date, the estimate, the windows
    skipped — so the claim can be checked rather than believed.

    **Nothing is written.** A suggestion is a proposal; the row appears when the
    user acts on it through ``POST /work-sessions``. An endpoint that booked as
    it suggested would fill the calendar with guesses the user never reviewed.

    **An empty list is a real answer, and `reason_if_empty` says which
    precondition was missing** — no availability rules, no candidates, or no
    room before the deadlines. Fabricating a plausible slot instead would be
    worse than returning nothing: the user would book it.

    ``task_ids`` is resolved through the **scoped** task lookup, so naming
    somebody else's task is a 404 rather than a suggestion built from their
    backlog.

    Errors: 404 for a task that is not the caller's; 422 for an unknown ``tz``.
    """
    return await scheduling.suggest(owner=current_user, task_ids=task_ids, tz=tz)


@router.get(
    "/conflicts",
    response_model=ConflictList,
    summary="Conflicts over a span",
    dependencies=[Depends(require_permission(Permission.CALENDAR_READ))],
)
async def list_conflicts(
    current_user: AuthenticatedUser,
    scheduling: SchedulingServiceDep,
    start: Annotated[date, Query(description="First local day to inspect.")],
    end: Annotated[date, Query(description="Last local day to inspect, inclusive.")],
    tz: Annotated[str | None, Query(description=_TZ_DESCRIPTION)] = None,
) -> ConflictList:
    """Report what is wrong with the caller's schedule over a span.

    **Four kinds, and each names its evidence**: two overlapping events, two
    overlapping sessions, work booked outside a declared availability window, and
    a session that ends after the deadline of the task it is for. The last is the
    one worth having — nothing else in the system notices that the only time
    booked for a task is after it was due.

    The detection itself is a pure function
    (:func:`app.services.scheduling_service.detect_conflicts`); this route only
    reads the span and hands it over. That split is deliberate: the judgement is
    exercisable without a database or a clock, and a rule about a schedule is
    much easier to argue about when it takes lists and returns a list.

    **An empty list means nothing was wrong**, not that nothing was checked — a
    span with no events and no sessions has no conflicts, and saying so is the
    correct answer. A truncated span is never an empty one, so the two cannot be
    confused.

    **A reversed span is refused rather than scanned.** ``end`` before ``start``
    describes no days at all, and answering it with 200 and an empty list would
    be a clean-looking "no conflicts" for a window that was never inspected —
    ``window`` would even read back the two dates the caller sent, in the wrong
    order. :meth:`~app.services.planner_service.PlannerService.list_events`,
    ``list_sessions`` and :meth:`~app.services.analytics.service.AnalyticsService._check_range`
    all refuse it for the same reason, and so does this route.

    **The answer is bounded, and says so.** Overlap detection is pairwise, so a
    span holding 500 mutually overlapping sessions would name 124,750 pairs —
    roughly 63.5 MB of JSON. The scan stops at
    :data:`app.services.scheduling_service.MAX_CONFLICTS` and the response
    carries ``truncated: true`` with the reason, so a partial list is never
    presented as a complete one. ``meta.total`` is then a floor on the number of
    conflicts that exist rather than a count of them.

    Errors: 422 for a malformed date, a reversed span, or an unknown ``tz``.
    """
    if end < start:
        raise ValidationError("end_date must not be earlier than start_date.")
    zone = resolve_timezone(tz, settings=scheduling.settings)
    scan = await scheduling.conflicts(owner=current_user, start=start, end=end, tz=tz)
    return ConflictList(
        window=PlannerWindow(start_date=start, end_date=end, timezone=str(zone)),
        conflicts=scan.conflicts,
        truncated=scan.truncated,
        truncated_reasons=list(scan.reasons),
        meta=PageMeta(total=len(scan.conflicts), limit=MAX_CONFLICTS, offset=0),
    )


#: The router only; the handlers are reached through it, not imported directly.
__all__ = ["router"]
