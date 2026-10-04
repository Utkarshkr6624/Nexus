"""The Planner: calendar events, work sessions, availability, and day/week views.

This module owns the rules the planner surface cannot get from SQL, and nothing
else — it imports no FastAPI and raises :class:`~app.core.exceptions.NexusError`
subclasses that the installed exception handlers turn into the shared envelope.

Time
----
**Every stored instant is timezone-aware UTC. Every day boundary is computed in
the IANA zone the request asked for** (``?tz=``, falling back to
``settings.planner_default_timezone``), using :mod:`zoneinfo` from the standard
library — no third-party tz database, because the platform already ships the
IANA one and a second copy is a second thing to fall out of date.

The rule exists because the spec forbids silently shifting a user's events. A
calendar view for ``Europe/Berlin`` spans ``[local midnight, next local
midnight)`` converted to UTC, *not* a UTC midnight; those are different instants
and picking the wrong one puts a 23:30 local session on the previous day's row
for every user east of Greenwich. Day boundaries are converted to UTC before any
query is built, so every statement in this file compares against an instant the
database can index.

**An unknown zone name is rejected with a 422**, never silently replaced by
UTC. A fallback would answer a well-formed question with a different question's
data — events quietly landing on the wrong day — which is the precise failure
the spec forbids.

``availability_rules`` is the one exception to "everything is an instant": its
``starts_at``/``ends_at`` are naive ``TIME`` columns, because "I work from 09:00"
is a wall-clock statement that has no instant until it is combined with a date
and a zone. The combination happens in :func:`availability_windows_for`.

Reservations
------------
**A work session may not overlap another live work session or a calendar
event**, and the check lives in the service — :meth:`PlannerService._assert_window_free`
— so that the hand-created session and the accepted suggestion cannot disagree
about what "reserved" means. Overlap is half-open: 10:00 against 10:00 is
back-to-back, which is how a day is built out of blocks. Two *events* may still
overlap each other; see :meth:`PlannerService.update_event` for why that is a
deliberate difference and :func:`app.services.scheduling_service.detect_conflicts`
for where it is reported instead.

**A bounded read that does not fit is refused, never shortened.** Every range
read compares its page against the unpaginated total and raises rather than
returning a partial answer — a truncated busy list is a list with holes in it,
and the scheduler places work into holes.

Purity
------
:meth:`PlannerService.overload_for_day` is delegated to the module-level
:func:`compute_overload`, which touches neither the database nor the clock. That
is what makes the availability-vs-commitment comparison unit-testable, and it is
why the same function is reachable without constructing a service.

Repository contract relied on by this module::

    CalendarEventRepository.create(*, owner_id, title, starts_at, ends_at,
                                   event_type="other", description=None,
                                   project_id=None, task_id=None, all_day=False,
                                   location=None) -> CalendarEvent
    CalendarEventRepository.get_by_id_for_user(event_id, owner_id) -> CalendarEvent | None
    CalendarEventRepository.list_for_user(owner_id, *, start, end, project_id=None,
                                          task_id=None, event_type=None, limit=100,
                                          offset=0, sort="starts_at",
                                          order="asc") -> tuple[list[CalendarEvent], int]
    CalendarEventRepository.overlapping_for_user(owner_id, *, start, end,
                                                 limit=1) -> tuple[list[CalendarEvent], int]
    CalendarEventRepository.update_fields(event, **fields) -> CalendarEvent
    CalendarEventRepository.delete(event) -> None

    WorkSessionRepository.create(*, owner_id, scheduled_start, scheduled_end,
                                 task_id=None, project_id=None,
                                 estimated_minutes=None, status="planned",
                                 actual_start=None, actual_end=None,
                                 actual_minutes=0) -> WorkSession
    WorkSessionRepository.get_by_id_for_user(session_id, owner_id) -> WorkSession | None
    WorkSessionRepository.list_for_user(owner_id, *, start, end, task_id=None,
                                        project_id=None, status=None, limit=100,
                                        offset=0, sort="scheduled_start",
                                        order="asc") -> tuple[list[WorkSession], int]
    WorkSessionRepository.overlapping_for_user(owner_id, *, start, end,
                                               exclude_id=None,
                                               limit=1) -> tuple[list[WorkSession], int]
    WorkSessionRepository.update_fields(session, **fields) -> WorkSession
    WorkSessionRepository.delete(session) -> None

    AvailabilityRuleRepository.list_for_user(owner_id) -> list[AvailabilityRule]
    AvailabilityRuleRepository.replace_for_user(owner_id, rules) -> list[AvailabilityRule]

    ProjectRepository.get_by_id_for_user(project_id, owner_id) -> Project | None
    TaskRepository.get_by_id_for_user(task_id, owner_id) -> Task | None

Tenancy
-------
Every read is scoped by ``owner_id`` **in the query**, so another account's id
answers 404 and is indistinguishable from an id that does not exist. The same
holds for every reference this module writes: an event's ``project_id`` and
``task_id`` are resolved through the scoped lookups before they are stored, so a
caller cannot file a calendar row against somebody else's work.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings, get_settings
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.enums import (
    ActivityEvent,
    CalendarEventType,
    validate_calendar_event_type,
)
from app.models.planner import (
    AvailabilityRule,
    CalendarEvent,
    WorkSession,
    WorkSessionStatus,
    validate_work_session_status,
)
from app.models.user import User
from app.repositories.planner import (
    AvailabilityRuleRepository,
    CalendarEventRepository,
    WorkSessionRepository,
)
from app.repositories.project import ProjectRepository
from app.repositories.task import TaskRepository
from app.schemas.common import Page, PageMeta
from app.schemas.planner import (
    CalendarEventRead,
    DayLoad,
    PlannerDay,
    PlannerWeek,
    PlannerWindow,
    WeekTotals,
    WorkSessionRead,
)

if TYPE_CHECKING:  # pragma: no cover - import cycle avoidance
    from app.services.activity_service import ActivityService
    from app.services.audit_service import AuditService

__all__ = [
    "MAX_PAGE_SIZE",
    "PlannerService",
    "availability_windows_for",
    "compute_overload",
    "day_bounds",
    "local_day",
    "resolve_timezone",
]

#: The default page size when a caller does not ask for one. A planner listing is
#: a range query the client pages through, not a feed it reads end to end.
DEFAULT_PAGE_SIZE = 50

#: The largest page any caller may ask for. A **rejection, not a silent clamp**:
#: a caller that asked for 500 and got 100 cannot tell a truncated page from a
#: page that was always 100 rows, and would paginate off the end of a sequence it
#: believes it has seen.
MAX_PAGE_SIZE = 100

#: Ceiling on the rows a single internal range read (a week or month view) may
#: pull. The repository's range predicate already bounds the statement; this
#: bounds how much of the bounded set one view assembles, so a user with a
#: pathological calendar gets a view rather than an unbounded materialisation.
#:
#: **A read that hits it is refused, never silently shortened.** Every caller
#: compares the row count against the unpaginated total and refuses the request
#: (a 422) when the window holds more than this. Truncating instead would make
#: the scheduler place work into slots the truncated rows hid — an audit
#: reproduced exactly that with 1,100 events in a month and 1,000 shown, and the
#: remaining 100 were offered as free. A view that is short and says nothing is
#: indistinguishable from a view that is complete.
MAX_RANGE_ROWS = 1000

#: Ceiling on the rules one ``PUT /availability`` may carry. The unique
#: constraint ``(owner_id, weekday, starts_at)`` permits 24 per weekday, but a
#: weekly pattern with more than a handful of windows per day is not a schedule
#: anybody keeps, and the cap keeps the payload off the "upload a file" path.
MAX_RULES_PER_WEEKDAY = 24

#: How far back / forward ``GET /calendar/events`` looks when the caller names no
#: range. Bounded in both directions because the repository takes no unbounded
#: window, and a missing range is a request for "recent and upcoming" rather than
#: for the whole history.
DEFAULT_LOOKBACK_DAYS = 30
DEFAULT_LOOKAHEAD_DAYS = 365

#: Sort keys the listings accept. ``ORDER BY`` takes an expression rather than a
#: bound parameter, so an unvalidated sort name is SQL injection behind a query
#: parameter; the name is resolved against these sets and the repository resolves
#: it a second time before interpolating. Failing closed, never a default column.
_EVENT_SORT_KEYS = frozenset(
    {"all_day", "created_at", "ends_at", "event_type", "starts_at", "title", "updated_at"}
)
_SESSION_SORT_KEYS = frozenset(
    {
        "actual_end",
        "actual_minutes",
        "actual_start",
        "created_at",
        "scheduled_end",
        "scheduled_start",
        "status",
        "updated_at",
    }
)
_SORT_ORDERS = frozenset({"asc", "desc"})

_EVENT_NOT_FOUND = "Calendar event not found."
_SESSION_NOT_FOUND = "Work session not found."
_TASK_NOT_FOUND = "Task not found."
_PROJECT_NOT_FOUND = "Project not found."

#: Event columns a plain PATCH may write. ``owner_id`` is the authorisation
#: anchor and is absent; ``project_id``/``task_id`` *are* present because moving
#: an event onto another of the caller's own rows is an ordinary edit, and the
#: destination is ownership-checked like any other reference.
_EVENT_UPDATABLE_FIELDS = frozenset(
    {
        "all_day",
        "completed_at",
        "description",
        "ends_at",
        "event_type",
        "location",
        "project_id",
        "starts_at",
        "task_id",
        "title",
    }
)
_SESSION_UPDATABLE_FIELDS = frozenset(
    {
        "estimated_minutes",
        "project_id",
        "scheduled_end",
        "scheduled_start",
        "status",
        "task_id",
    }
)


# -- Timezone primitives (pure) ---------------------------------------------


def resolve_timezone(name: str | None, settings: Settings) -> ZoneInfo:
    """Resolve an IANA zone name, or raise.

    Args:
        name: The caller's ``?tz=``. Blank/absent falls back to
            ``settings.planner_default_timezone``.
        settings: Supplies that fallback.

    Returns:
        The zone.

    Raises:
        ValidationError: If the name is not one this host knows. **It is never
            replaced by UTC**: a silent fallback would shift every boundary by
            the zone's offset and answer a well-formed question with another
            question's data.
    """
    candidate = (name or "").strip() or settings.planner_default_timezone
    try:
        return ZoneInfo(candidate)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        raise ValidationError(
            f"Unknown IANA time zone: {candidate!r}.",
            details={"tz": candidate, "example": "Europe/Berlin"},
        ) from None


def day_bounds(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """Return ``day``'s half-open ``[start, end)`` window as UTC instants.

    The local midnight is *combined* with the zone rather than derived from a
    UTC instant, which is what makes the DST days come out right: a zone east of
    Greenwich has a local midnight that is the previous UTC day, and a zone with
    a spring-forward transition can have a day 23 hours long. Building the
    window from the wall clock and converting once handles both without a
    special case.
    """
    start = datetime.combine(day, time.min, tzinfo=tz)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz)
    return start.astimezone(UTC), end.astimezone(UTC)


def local_day(instant: datetime, tz: ZoneInfo) -> date:
    """The calendar day an instant falls on in ``tz``."""
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
    return instant.astimezone(tz).date()


def availability_windows_for(
    rules: Sequence[AvailabilityRule], day: date
) -> list[tuple[time, time]]:
    """The owner's availability windows on one calendar day, **merged**.

    ``weekday`` is ISO: 0 is Monday through 6 for Sunday, which is
    :meth:`datetime.date.weekday` exactly. Sorted by start so a caller walking
    the day does not have to re-sort, and merged wherever two rules *overlap or
    abut* so a 09:00-12:00 plus 12:00-15:00 rule reads as one window, and so
    does a 09:00-17:00 plus 11:00-13:00 one.

    Merging the overlapping case is not tidiness, it is correctness. Summing a
    day's windows as they were written double-counts every shared minute: a day
    declared as 09:00-17:00 (480 minutes) with a nested 11:00-13:00 rule (120)
    reported **600** available minutes for an eight-hour day — the audit
    measured 960 on its own eleven-hour fixture. Every downstream figure is a
    ratio of scheduled time to available time, so an inflated denominator makes
    an overloaded week look half empty. The unique constraint is
    ``(owner_id, weekday, starts_at)``, which permits overlapping windows
    freely: nothing but this function stops them being counted twice.

    Accepts any object carrying ``weekday``/``starts_at``/``ends_at`` - a
    persisted rule, a Pydantic payload, or a test double — because the same
    computation runs in the service, the scheduler and its unit tests.
    """
    windows = sorted(
        (rule.starts_at, rule.ends_at) for rule in rules if rule.weekday == day.weekday()
    )
    merged: list[tuple[time, time]] = []
    for start, end in windows:
        if merged and start <= merged[-1][1]:
            # `<=`, not `==`: abutting windows are continuous, and so are
            # windows that share minutes. Taking the later end rather than the
            # current one also covers a rule wholly nested inside another.
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _minutes_between(start: time, end: time) -> int:
    """Whole minutes from one wall-clock time to a later one."""
    base = datetime.combine(date(2000, 1, 1), start)
    end_dt = datetime.combine(date(2000, 1, 1), end)
    if end_dt <= base:
        # Overnight availability (a night shift) is not representable as one
        # window on this schema; the constraint refuses it at write time, so a
        # row that got here anyway contributes nothing rather than a negative.
        return 0
    return int((end_dt - base).total_seconds() // 60)


def compute_overload(
    *,
    day: date,
    availability: Sequence[Any],
    sessions: Sequence[WorkSession],
    owner_tz: str = "UTC",
) -> DayLoad:
    """Compare one day's committed time with the time the user said they have.

    **Pure: no database, no clock.** Every input is passed in, including the
    zone, so the same call with the same arguments always answers the same
    thing — which is what makes it unit-testable and what stops a view from
    flickering because two requests straddled midnight.

    ``scheduled`` is the part of each session that falls on ``day``, so a session
    running across midnight contributes only its own share; a session is counted
    when it *overlaps* the day rather than when it starts on it. Cancelled
    sessions are excluded — they hold no time, so counting them would report an
    overload the user does not have.

    ``ratio`` is ``None`` when the day has no availability at all rather than a
    division by zero. "You scheduled 90 minutes but declared no availability" is
    *unknown*, and the honest rendering of unknown is null; a 0.0 or an infinity
    would both be invented numbers that a chart would happily plot.

    **The overlap is measured in elapsed time, and is therefore independent of
    what zone the session rows are labelled with.** That is not automatic: Python
    subtracts two aware datetimes carrying the *same* ``tzinfo`` by their wall
    clock and ignores the offset, so a row read as ``04:00+02:00`` minus
    ``00:00+01:00`` is **four hours** even across a spring-forward, where the two
    instants are three hours apart. Normalising both ends to UTC first — see
    :func:`_aware` — makes the same pair of instants give the same answer whatever
    they are labelled, which is the property the rest of this module assumes when
    it converts a boundary to UTC "before any query is built".
    """
    tz = resolve_timezone(owner_tz, get_settings())
    available = sum(
        _minutes_between(start, end) for start, end in availability_windows_for(availability, day)
    )
    start_at, end_at = day_bounds(day, tz)
    scheduled = 0
    for row in sessions:
        if str(row.status) == WorkSessionStatus.CANCELLED.value:
            continue
        overlap = min(_aware(row.scheduled_end), end_at) - max(
            _aware(row.scheduled_start), start_at
        )
        if overlap > timedelta(0):
            scheduled += round(overlap.total_seconds() / 60)
    return DayLoad(
        date=day,
        available=available,
        scheduled=scheduled,
        overload_minutes=max(0, scheduled - available),
        ratio=round(scheduled / available, 4) if available > 0 else None,
    )


def _aware(value: datetime) -> datetime:
    """Normalise an instant to UTC: naive is read as UTC, aware is converted.

    **Converting rather than passing through is load-bearing for arithmetic.**
    Python's rule for subtracting two aware datetimes is that a *shared*
    ``tzinfo`` is ignored and the wall-clock fields are subtracted directly, so
    ``04:00+02:00 - 00:00+01:00`` is ``4:00:00`` — while the two instants are
    three hours apart whenever a clock change sits between them. Rows read back
    from ``timestamptz`` columns arrive labelled UTC, so the shared-``tzinfo``
    branch was only reachable when a caller of these pure functions handed in an
    instant carrying its own offset; it then silently changed a day's committed
    minutes by the length of the transition. Normalising here means the two
    operands are always ``timezone.utc``, which is the one case where the shared
    ``tzinfo`` *is* honoured and the subtraction is the elapsed time between the
    instants.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _overlaps(start: datetime, end: datetime, other_start: datetime, other_end: datetime) -> bool:
    """Half-open overlap: touching is not overlapping.

    An event ending at 10:00 and one starting at 10:00 are back-to-back, which is
    what a calendar is built out of. ``BETWEEN`` would call that a collision.
    """
    return start < other_end and end > other_start


def _week_totals(days: Sequence[PlannerDay]) -> WeekTotals:
    """Sum a week's days.

    ``available_minutes`` is ``None`` when *no* day declared hours — a week of
    unknowns must not be reported as a week of zeroes, which would render as a
    completely free week rather than an unconfigured one.
    """
    declared = [day.available_minutes for day in days if day.available_minutes is not None]
    return WeekTotals(
        available_minutes=sum(declared) if declared else None,
        scheduled_minutes=sum(day.scheduled_minutes for day in days),
        overload_minutes=sum(day.overload_minutes or 0 for day in days),
        overloaded_days=sum(1 for day in days if day.overloaded),
        event_count=sum(len(day.events) for day in days),
        session_count=sum(len(day.sessions) for day in days),
    )


def _parse_month(month: str) -> tuple[int, int]:
    """Parse a strict ``YYYY-MM``, or raise.

    Strict on purpose: ``"2026-3"`` and ``"2026-03"`` would otherwise be the same
    month reached two ways, and a reader that guessed which was meant would be a
    coin toss dressed up as a filter. Exactly four year digits and two month
    digits, nothing else.
    """
    if (
        not isinstance(month, str)
        or len(month) != 7
        or month[4] != "-"
        or not month[:4].isdigit()
        or not month[5:].isdigit()
    ):
        raise ValidationError("month must be formatted YYYY-MM.", details={"month": str(month)})
    year, month_number = int(month[:4]), int(month[5:])
    if not 1 <= month_number <= 12:
        raise ValidationError("month must name a month 01-12.", details={"month": month})
    return year, month_number


def _check_window(*, limit: int, offset: int, maximum: int = MAX_PAGE_SIZE) -> None:
    """Reject a page window that is not one, including over-large pages."""
    if limit < 1:
        raise ValidationError("limit must be at least 1.")
    if limit > maximum:
        raise ValidationError(
            f"limit must be at most {maximum}.",
            details={"limit": limit, "max_limit": maximum},
        )
    if offset < 0:
        raise ValidationError("offset must be zero or greater.")


def _check_sort(sort: str, order: str, allowed: frozenset[str], subject: str) -> None:
    """Validate a sort pair against an allowlist, failing closed."""
    if sort not in allowed:
        raise ValidationError(
            f"Cannot sort {subject} by {sort!r}.", details={"allowed": sorted(allowed)}
        )
    if order not in _SORT_ORDERS:
        raise ValidationError(
            f"Cannot sort {subject} {order!r}.", details={"allowed": sorted(_SORT_ORDERS)}
        )


def _reject_truncated_range(
    *, rows: Sequence[Any], total: int, table: str, start: datetime, end: datetime
) -> None:
    """Refuse a bounded read that did not fit in :data:`MAX_RANGE_ROWS`.

    The repository returns the unpaginated ``total`` alongside the page, so
    "we read a thousand of eleven hundred" is a fact rather than a suspicion.

    Refusing is the only honest option for a caller that is about to *decide*
    something from the rows — the scheduler walks them to find free slots, and a
    truncated busy list is a list with holes in it. The day, week and month views
    refuse for the same reason: a month that silently showed 1,000 of 1,100
    events looks complete, and every day after the cut looks free.
    """
    if total <= len(rows):
        return
    raise ValidationError(
        f"That span holds more than {MAX_RANGE_ROWS} {table}, so it cannot be read "
        "completely. Ask for a smaller range.",
        details={
            "table": table,
            "rows_matched": total,
            "rows_read": len(rows),
            "max_range_rows": MAX_RANGE_ROWS,
            "window_start": _aware(start).isoformat(),
            "window_end": _aware(end).isoformat(),
        },
    )


def _local_days_touched(start: datetime, end: datetime, zone: ZoneInfo) -> list[date]:
    """The local calendar days a half-open ``[start, end)`` interval touches.

    The half-microsecond on the end is what makes a block ending exactly at
    local midnight belong to the day before it: the instant ``00:00:00`` is the
    first moment of the *next* day, and a session that ran 23:00-00:00 has
    touched one day, not two.
    """
    first = local_day(_aware(start), zone)
    last = local_day(_aware(end - timedelta(microseconds=1)), zone)
    return [first + timedelta(days=offset) for offset in range((last - first).days + 1)]


def _event_type_or_none(value: str | CalendarEventType | None) -> str | None:
    if value is None:
        return None
    return _event_type_or_raise(value).value


def _event_type_or_raise(value: str | CalendarEventType) -> CalendarEventType:
    try:
        return validate_calendar_event_type(value)
    except ValueError:
        raise ValidationError(f"Unknown event type: {value!r}.") from None


def _session_status_or_none(value: str | WorkSessionStatus | None) -> str | None:
    if value is None:
        return None
    return _session_status_or_raise(value).value


def _session_status_or_raise(value: str | WorkSessionStatus) -> WorkSessionStatus:
    try:
        return validate_work_session_status(value)
    except ValueError:
        raise ValidationError(f"Unknown work session status: {value!r}.") from None


class PlannerService:
    """The rules of the calendar, the clock, and the time the user has."""

    def __init__(
        self,
        events: CalendarEventRepository,
        sessions: WorkSessionRepository,
        availability: AvailabilityRuleRepository,
        projects: ProjectRepository,
        tasks: TaskRepository,
        activity: ActivityService | None = None,
        audit: AuditService | None = None,
        settings: Settings | None = None,
    ) -> None:
        """Wire the service.

        Args:
            events: Calendar-event persistence.
            sessions: Work-session persistence.
            availability: The weekly pattern.
            projects: Needed because an event or session may point at a project,
                and that reference must be re-checked through the *scoped* lookup
                before it is written.
            tasks: The same, for a task reference — and for the scheduler's
                candidate backlog.
            activity: Where domain events are written. Optional so the rules can
                be exercised without a history sink.
            audit: Accepted for symmetry with the other services and deliberately
                unused — booking a meeting is a fact about the work, not a fact
                about the account, and ``audit_logs`` is the security trail.
            settings: Application settings, resolved from the environment when
                not supplied.
        """
        self.events = events
        self.sessions = sessions
        self.availability = availability
        self.projects = projects
        self.tasks = tasks
        self.activity = activity
        self.audit = audit
        self.settings = settings or get_settings()

    # -- Calendar events -----------------------------------------------------

    @staticmethod
    def resolve_timezone(name: str | None, settings: Settings | None = None) -> ZoneInfo:
        """Resolve an IANA zone name, or raise :class:`ValidationError`.

        Exposed on the instance so a router can name the zone it built a response
        window from using the same resolution — and therefore the same answer —
        the service used to pick the days inside it. A router that resolved it
        separately would silently disagree with the query on any machine whose
        tzdata differed.
        """
        return resolve_timezone(name, settings or get_settings())

    async def create_event(self, *, owner: User, data: Any) -> CalendarEvent:
        """Create a calendar event for the caller.

        Every reference in the payload is resolved through the scoped lookups
        **before** the row is written, so an id belonging to somebody else leaves
        no row behind at all rather than a half-applied one. The refusal is a
        ``NotFoundError`` — the caller cannot tell "not yours" from "does not
        exist", so this cannot be used to probe which ids are real.

        Raises:
            NotFoundError: If ``project_id`` or ``task_id`` is not the caller's.
            ValidationError: If the window ends before it starts.
        """
        project_id = await self._owned_project(data.project_id, owner)
        task_id = await self._owned_task(data.task_id, owner)
        _require_forward_window(data.starts_at, data.ends_at)
        event_type = _event_type_or_raise(data.event_type).value
        event = await self.events.create(
            owner_id=owner.id,
            title=data.title,
            starts_at=data.starts_at,
            ends_at=data.ends_at,
            event_type=event_type,
            description=data.description,
            project_id=project_id,
            task_id=task_id,
            all_day=data.all_day,
            location=data.location,
        )
        await self._record(
            ActivityEvent.CALENDAR_EVENT_CREATED,
            owner=owner,
            project_id=project_id,
            task_id=task_id,
            metadata={"title": event.title, "event_type": event.event_type},
        )
        return event

    async def get_event(self, *, event_id: uuid.UUID, owner: User) -> CalendarEvent:
        """Return one of the caller's events.

        404 for another account's event, never 403: the ``owner_id`` predicate is
        in the query, so the row is never loaded.
        """
        event = await self.events.get_by_id_for_user(event_id, owner.id)
        if event is None:
            raise NotFoundError(_EVENT_NOT_FOUND)
        return event

    async def list_events(
        self,
        *,
        owner: User,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
        starts_from: datetime | None = None,
        starts_to: datetime | None = None,
        project_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
        event_type: str | CalendarEventType | None = None,
        sort: str = "starts_at",
        order: str = "asc",
    ) -> Page[CalendarEventRead]:
        """List the caller's events overlapping a window, as a page.

        The window is **half-open overlap**, not ``starts_at BETWEEN``: an event
        that began yesterday and runs into this week is on this week's calendar.
        With no window named, a bounded default around now is used — the
        repository takes no unbounded range on purpose, so "no filter" has to
        mean *some* window rather than *every* row.

        Raises:
            ValidationError: For an impossible window, an unknown event type,
                an unknown sort, or a ``limit`` outside 1-100.
        """
        _check_window(limit=limit, offset=offset)
        _check_sort(sort, order, _EVENT_SORT_KEYS, "calendar events")
        if starts_from is not None and starts_to is not None and starts_to <= starts_from:
            raise ValidationError("`to` must be later than `from`.")
        now = await self._db_now()
        start = starts_from or now - timedelta(days=DEFAULT_LOOKBACK_DAYS)
        end = starts_to or now + timedelta(days=DEFAULT_LOOKAHEAD_DAYS)
        rows, total = await self.events.list_for_user(
            owner.id,
            start=start,
            end=end,
            project_id=project_id,
            task_id=task_id,
            event_type=_event_type_or_none(event_type),
            limit=limit,
            offset=offset,
            sort=sort,
            order=order,
        )
        return Page[CalendarEventRead](
            items=[CalendarEventRead.model_validate(row) for row in rows],
            meta=PageMeta(total=total, limit=limit, offset=offset),
        )

    async def update_event(self, *, event: CalendarEvent, data: Any, owner: User) -> CalendarEvent:
        """Apply a partial update to an event the caller owns.

        **PATCH semantics: a field is written if the client named it**, so a
        cleared location is ``"location": null`` rather than an absent key. That
        rule has one exception, and it is checked here rather than trusted to the
        schema: a ``null`` is a legitimate clear for a *nullable* column and is
        not for a window end. ``PATCH {"starts_at": null}`` used to reach
        ``_require_forward_window(None, ...)`` and raise ``AttributeError`` out
        of ``_aware`` — an unhandled 500 on a request the schema had already
        decided was well-formed. It is a 422 now, naming the field.

        The window is re-checked against the **persisted** row, not only against
        the payload: a PATCH that moves ``starts_at`` past a stored ``ends_at``
        carries nothing for the schema to compare against and would otherwise
        persist an impossible schedule no single request expressed.

        Two events may still overlap each other. That is deliberate and is not
        the same defect as two *sessions* overlapping: a meeting is a statement
        the user makes about their own day, and real calendars hold concurrent
        meetings, whereas a work session is a reservation other machinery places
        into. :func:`app.services.scheduling_service.detect_conflicts` reports
        ``overlapping_events`` as an error for exactly the users who want to be
        told; here the diagnostic surface is the right place and a 409 would not
        be.

        Raises:
            NotFoundError: If the event is not the caller's.
            ValidationError: For a null window end, an inverted effective window,
                an unknown event type, or a reference that is not the caller's
                (the reference's own ``NotFoundError``).
        """
        self._owned_event(event, owner)
        sent = data.model_dump(exclude_unset=True)
        _reject_null_window_fields(sent, ("starts_at", "ends_at"))
        fields = {key: value for key, value in sent.items() if key in _EVENT_UPDATABLE_FIELDS}
        if not fields:
            return event
        if "project_id" in fields:
            fields["project_id"] = await self._owned_project(fields["project_id"], owner)
        if "task_id" in fields:
            fields["task_id"] = await self._owned_task(fields["task_id"], owner)
        if "event_type" in fields:
            fields["event_type"] = _event_type_or_raise(fields["event_type"]).value
        _require_forward_window(
            fields.get("starts_at", event.starts_at),
            fields.get("ends_at", event.ends_at),
        )
        updated = await self.events.update_fields(event, **fields)
        await self._record(
            ActivityEvent.CALENDAR_EVENT_UPDATED,
            owner=owner,
            project_id=updated.project_id,
            task_id=updated.task_id,
            metadata={"fields": sorted(fields), "title": updated.title},
        )
        return updated

    async def delete_event(self, *, event: CalendarEvent, owner: User) -> None:
        """Delete one of the caller's events.

        Sessions hanging off it cascade: an event is a statement about time, not
        a record of work, so losing it loses no history worth keeping.
        """
        self._owned_event(event, owner)
        event_id, title = event.id, event.title
        project_id, task_id = event.project_id, event.task_id
        await self.events.delete(event)
        # The ids ride in the metadata rather than in the foreign keys: the
        # columns would point at rows that no longer exist.
        await self._record(
            ActivityEvent.CALENDAR_EVENT_DELETED,
            owner=owner,
            project_id=project_id,
            task_id=task_id,
            metadata={"event_id": str(event_id), "title": title},
        )

    # -- Work sessions -------------------------------------------------------

    async def create_session(self, *, owner: User, data: Any) -> WorkSession:
        """Create a work session for the caller.

        ``estimated_minutes`` is copied from the payload rather than looked up
        on the task: a session records what was *planned for this block*, which
        is not the same number as the task's whole estimate, and overwriting one
        with the other is how a 30-minute slice turns into a 4-hour booking.

        **The window is checked against what is already reserved before the row
        is written** — see :meth:`_assert_window_free`. This is the one place
        that check belongs: every route that books a block reaches it (the
        ``POST /work-sessions`` handler directly, and
        :meth:`app.services.scheduling_service.SchedulingService.accept` through
        this very method), so no caller can produce an overlap by going around
        it.

        Raises:
            NotFoundError: If ``project_id`` or ``task_id`` is not the caller's.
            ValidationError: If the window ends before it starts, or the status
                is not one of the four.
            ConflictError: If the window overlaps another live work session or a
                calendar event. 409, naming the conflicting row.
        """
        project_id = await self._owned_project(data.project_id, owner)
        task_id = await self._owned_task(data.task_id, owner)
        _require_forward_window(data.scheduled_start, data.scheduled_end)
        await self._assert_window_free(
            owner=owner, start=data.scheduled_start, end=data.scheduled_end
        )
        status = _session_status_or_raise(data.status).value
        row = await self.sessions.create(
            owner_id=owner.id,
            scheduled_start=data.scheduled_start,
            scheduled_end=data.scheduled_end,
            task_id=task_id,
            project_id=project_id,
            estimated_minutes=data.estimated_minutes,
            status=status,
        )
        return row

    async def get_session(self, *, session_id: uuid.UUID, owner: User) -> WorkSession:
        """Return one of the caller's sessions (404 for anyone else's)."""
        row = await self.sessions.get_by_id_for_user(session_id, owner.id)
        if row is None:
            raise NotFoundError(_SESSION_NOT_FOUND)
        return row

    async def list_sessions(
        self,
        *,
        owner: User,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
        starts_from: datetime | None = None,
        starts_to: datetime | None = None,
        task_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
        status: str | WorkSessionStatus | None = None,
        sort: str = "scheduled_start",
        order: str = "asc",
    ) -> Page[WorkSessionRead]:
        """List the caller's sessions overlapping a window, as a page.

        A session overlapping the window is in the window even when it started
        before it — an evening block begun at 23:00 belongs to the day it ran
        into.
        """
        _check_window(limit=limit, offset=offset)
        _check_sort(sort, order, _SESSION_SORT_KEYS, "work sessions")
        if starts_from is not None and starts_to is not None and starts_to <= starts_from:
            raise ValidationError("`to` must be later than `from`.")
        now = await self._db_now()
        start = starts_from or now - timedelta(days=DEFAULT_LOOKBACK_DAYS)
        end = starts_to or now + timedelta(days=DEFAULT_LOOKAHEAD_DAYS)
        rows, total = await self.sessions.list_for_user(
            owner.id,
            start=start,
            end=end,
            task_id=task_id,
            project_id=project_id,
            status=_session_status_or_none(status),
            limit=limit,
            offset=offset,
            sort=sort,
            order=order,
        )
        return Page[WorkSessionRead](
            items=[WorkSessionRead.model_validate(row) for row in rows],
            meta=PageMeta(total=total, limit=limit, offset=offset),
        )

    async def update_session(self, *, session: WorkSession, data: Any, owner: User) -> WorkSession:
        """Apply a partial update to a session the caller owns.

        **``actual_start``/``actual_end``/``actual_minutes`` are not in this
        method's vocabulary at all** — not filtered out of a payload that
        carried them, but refused by the schema before they arrive. They are the
        product of :meth:`start_session` and :meth:`stop_session`, which are the
        only doors onto them, and every surface that reports time to a user
        reads those columns. A PATCH that could assert ``actual_minutes`` would
        make the tracked total a function of what a client sent rather than of
        what a clock measured; the audit's `PATCH {"actual_minutes": 600}`
        returned 200 and changed nothing, which is worse than either
        persisting the claim or rejecting it.

        A move is re-checked for collisions exactly as a create is, excluding
        the row being moved — a session overlaps itself on every instant of its
        own window, so the exclusion is what makes "reschedule" possible at all.

        Raises:
            NotFoundError: If the session is not the caller's.
            ValidationError: For a null window end, an inverted effective window,
                or a status the model does not define.
            ConflictError: If the new window overlaps another live session or a
                calendar event.
        """
        self._owned_session(session, owner)
        sent = data.model_dump(exclude_unset=True)
        _reject_null_window_fields(sent, ("scheduled_start", "scheduled_end"))
        fields = {key: value for key, value in sent.items() if key in _SESSION_UPDATABLE_FIELDS}
        if not fields:
            return session
        if "project_id" in fields:
            fields["project_id"] = await self._owned_project(fields["project_id"], owner)
        if "task_id" in fields:
            fields["task_id"] = await self._owned_task(fields["task_id"], owner)
        if "status" in fields:
            fields["status"] = _session_status_or_raise(fields["status"]).value
        start = fields.get("scheduled_start", session.scheduled_start)
        end = fields.get("scheduled_end", session.scheduled_end)
        _require_forward_window(start, end)
        if _aware(start) != _aware(session.scheduled_start) or _aware(end) != _aware(
            session.scheduled_end
        ):
            await self._assert_window_free(owner=owner, start=start, end=end, exclude_id=session.id)
        return await self.sessions.update_fields(session, **fields)

    async def delete_session(self, *, session: WorkSession, owner: User) -> None:
        """Delete one of the caller's sessions."""
        self._owned_session(session, owner)
        await self.sessions.delete(session)

    async def start_session(self, *, session: WorkSession, owner: User) -> WorkSession:
        """Stamp ``actual_start`` and move the session to ``active``.

        ``actual_start`` comes from the **database** clock (``func.now()``) rather
        than the process clock: the value is a fact about this installation's
        time, and a host whose clock has drifted would otherwise report a
        session that started in the future and then compute a negative duration
        when it is stopped.

        Starting an already-active session is a no-op, so a double-clicked
        button does not discard the first stamp. A completed or cancelled
        session cannot be restarted: its ``actual_start`` is history.
        """
        self._owned_session(session, owner)
        status = _session_status_or_raise(session.status)
        if status is WorkSessionStatus.ACTIVE:
            return session
        if status in (WorkSessionStatus.COMPLETED, WorkSessionStatus.CANCELLED):
            raise ValidationError(
                f"A {status.value} work session cannot be started.",
                details={"status": status.value},
            )
        started = await self.sessions.update_fields(
            session,
            actual_start=await self._db_now(),
            status=WorkSessionStatus.ACTIVE.value,
        )
        await self._record(
            ActivityEvent.WORK_SESSION_STARTED,
            owner=owner,
            project_id=started.project_id,
            task_id=started.task_id,
            metadata={
                "session_id": str(started.id),
                "scheduled_start": started.scheduled_start.isoformat(),
            },
        )
        return started

    async def stop_session(self, *, session: WorkSession, owner: User) -> WorkSession:
        """Stamp ``actual_end``, compute ``actual_minutes`` and complete.

        **The duration is measured from ``actual_start``, never from
        ``scheduled_start``.** Those differ whenever the user did not begin on
        time, and reporting the scheduled figure as the actual one would make the
        time-tracking page agree with the plan by construction — which is the one
        number a time tracker exists to contradict.

        The elapsed seconds are rounded **once**, here, rather than per session:
        see the note on :attr:`app.models.task.Task.actual_minutes` — rounding at
        a finer granularity loses up to 30 seconds per session and the error
        compounds across a day of short ones.

        Stopping a completed session is a no-op. Stopping one that was never
        started is a 422, because there is no start to measure from and inventing
        one would put a session on the report that nobody worked on.
        """
        self._owned_session(session, owner)
        status = _session_status_or_raise(session.status)
        if status is WorkSessionStatus.COMPLETED:
            return session
        if status is not WorkSessionStatus.ACTIVE or session.actual_start is None:
            raise ValidationError(
                "Start the work session before stopping it.",
                details={"status": status.value},
            )
        ended_at = await self._db_now()
        started_at = _aware(session.actual_start)
        elapsed = max(0, round((ended_at - started_at).total_seconds() / 60))
        completed = await self.sessions.update_fields(
            session,
            actual_end=ended_at,
            actual_minutes=elapsed,
            status=WorkSessionStatus.COMPLETED.value,
        )
        await self._record(
            ActivityEvent.WORK_SESSION_COMPLETED,
            owner=owner,
            project_id=completed.project_id,
            task_id=completed.task_id,
            metadata={"session_id": str(completed.id), "actual_minutes": elapsed},
        )
        return completed

    # -- Availability --------------------------------------------------------

    async def get_availability(self, *, owner: User) -> list[AvailabilityRule]:
        """Return the caller's weekly availability pattern.

        The one listing in this module without a range, and the schema is why:
        ``(owner_id, weekday, starts_at)`` is unique, so the answer is at most
        24 x 7 = 168 rows.
        """
        return await self.availability.list_for_user(owner.id)

    async def replace_availability(
        self, *, owner: User, rules: Sequence[Any]
    ) -> list[AvailabilityRule]:
        """Replace the caller's whole week with ``rules``.

        A replace rather than a diff because ``PUT /availability`` states the
        week: "I removed a rule" and "I did not send it" mean the same thing here,
        and a diff-based endpoint would have to guess between them.

        Duplicates within one payload are rejected by the unique constraint; the
        ``IntegrityError`` is translated into a 409 here rather than escaping as a
        500, and the conflicting ``(weekday, starts_at)`` is named so the client
        can see which two rows collide.
        """
        try:
            return await self.availability.replace_for_user(owner.id, rules)
        except IntegrityError as exc:
            raise ConflictError(
                "Two of these availability rules share a weekday and start time.",
                details={"max_rules_per_weekday": MAX_RULES_PER_WEEKDAY},
            ) from exc

    # -- Planner views -------------------------------------------------------

    async def day(self, *, owner: User, day: date, tz: str | None = None) -> PlannerDay:
        """Return one local calendar day: its events, sessions and load.

        The window is ``day``'s local midnight to the next local midnight in the
        requested zone, converted to UTC before the query — see the module
        docstring for why a UTC window would put a 23:30 local session on the
        previous day.
        """
        zone = resolve_timezone(tz, self.settings)
        rules = await self.availability.list_for_user(owner.id)
        buckets = await self._range(owner, day, day, zone)
        return self._build_day(day, zone, buckets, rules)

    async def week(self, *, owner: User, week_start: date, tz: str | None = None) -> PlannerWeek:
        """Return seven days from ``week_start``.

        ``week_start`` is taken as given rather than snapped to Monday: a client
        that wants a Sunday-start week gets one, and the caller already knows
        which day it asked for.
        """
        zone = resolve_timezone(tz, self.settings)
        start = week_start
        rules = await self.availability.list_for_user(owner.id)
        buckets = await self._range(owner, start, start + timedelta(days=6), zone)
        days = [
            self._build_day(start + timedelta(days=offset), zone, buckets, rules)
            for offset in range(7)
        ]
        return PlannerWeek(
            week_start=start,
            week_end=start + timedelta(days=6),
            days=days,
            totals=_week_totals(days),
            window=PlannerWindow(
                start_date=start, end_date=start + timedelta(days=6), timezone=str(zone)
            ),
        )

    async def month(self, *, owner: User, month: str, tz: str | None = None) -> list[PlannerDay]:
        """Return every day of ``YYYY-MM`` as a planner day.

        Two range queries for the whole month — one for events, one for sessions
        — then bucketed in Python. One query per day would be 60 round trips for a
        month view, and the buckets would be free to disagree with each other
        mid-write.
        """
        zone = resolve_timezone(tz, self.settings)
        year, month_number = _parse_month(month)
        first = date(year, month_number, 1)
        last = date(year + (month_number == 12), (month_number % 12) + 1, 1) - timedelta(days=1)
        rules = await self.availability.list_for_user(owner.id)
        buckets = await self._range(owner, first, last, zone)
        span = (last - first).days
        return [
            self._build_day(first + timedelta(days=offset), zone, buckets, rules)
            for offset in range(span + 1)
        ]

    def overload_for_day(
        self,
        *,
        day: date,
        availability: Sequence[Any],
        sessions: Sequence[WorkSession],
        owner_tz: str = "UTC",
    ) -> DayLoad:
        """One day's availability versus committed time.

        Pure: no database, no clock. Delegates to :func:`compute_overload` so the
        same function is reachable without a service instance.
        """
        return compute_overload(
            day=day, availability=availability, sessions=sessions, owner_tz=owner_tz
        )

    # -- Internals -----------------------------------------------------------

    async def _range(
        self, owner: User, first: date, last: date, zone: ZoneInfo
    ) -> dict[date, tuple[list[CalendarEvent], list[WorkSession]]]:
        """Read every event and session touching a date span, bucketed by local day.

        Rows are filed under **every local day they touch**, not only the one
        they start on. That is the same rule ``/work-sessions`` and the
        repository use — an interval is in a window when it *overlaps* it — and
        the two surfaces used to disagree: a 90-minute session from 23:30 to
        01:00 was filed under the first day alone, so ``/planner/day`` credited
        30 minutes to it while ``/work-sessions`` listed it against both days
        and :func:`compute_overload` (which measures the overlap with each day)
        found 60 minutes of the same session with nowhere to go. Filing it twice
        is not double-counting: each day's load clamps the interval to its own
        bounds, so the shares are 30 and 60 and they sum to the session's 90.

        A read that does not fit in :data:`MAX_RANGE_ROWS` is refused rather
        than shortened — see :func:`_reject_truncated_range`.

        Raises:
            ValidationError: If either table holds more rows in the span than
                :data:`MAX_RANGE_ROWS` allows, so the buckets could not be
                complete.
        """
        window_start, window_end = day_bounds(first, zone)
        window_end = max(window_end, day_bounds(last, zone)[1])
        events, event_total = await self.events.list_for_user(
            owner.id,
            start=window_start,
            end=window_end,
            limit=MAX_RANGE_ROWS,
            sort="starts_at",
            order="asc",
        )
        _reject_truncated_range(
            rows=events,
            total=event_total,
            table="calendar events",
            start=window_start,
            end=window_end,
        )
        sessions, session_total = await self.sessions.list_for_user(
            owner.id,
            start=window_start,
            end=window_end,
            limit=MAX_RANGE_ROWS,
            sort="scheduled_start",
            order="asc",
        )
        _reject_truncated_range(
            rows=sessions,
            total=session_total,
            table="work sessions",
            start=window_start,
            end=window_end,
        )
        span = (last - first).days
        buckets: dict[date, tuple[list[CalendarEvent], list[WorkSession]]] = {
            first + timedelta(days=offset): ([], []) for offset in range(span + 1)
        }
        for event in events:
            for key in _local_days_touched(event.starts_at, event.ends_at, zone):
                if key in buckets:
                    buckets[key][0].append(event)
        for row in sessions:
            for key in _local_days_touched(row.scheduled_start, row.scheduled_end, zone):
                if key in buckets:
                    buckets[key][1].append(row)
        return buckets

    def _build_day(
        self,
        day: date,
        zone: ZoneInfo,
        buckets: Mapping[date, tuple[list[CalendarEvent], list[WorkSession]]],
        rules: Sequence[AvailabilityRule],
    ) -> PlannerDay:
        """Assemble one :class:`PlannerDay` from rows already in hand."""
        events, sessions = buckets.get(day, ([], []))
        load = compute_overload(day=day, availability=rules, sessions=sessions, owner_tz=str(zone))
        # A day the user has declared no hours for is *unknown*, not overloaded:
        # "no availability defined" and "zero minutes available" are different
        # answers, and only the second one makes a red badge honest. Hence
        # ``None`` rather than 0, and ``overloaded`` stays False.
        declared = bool(availability_windows_for(rules, day))
        return PlannerDay(
            date=day,
            events=[CalendarEventRead.model_validate(row) for row in events],
            sessions=[WorkSessionRead.model_validate(row) for row in sessions],
            available_minutes=load.available if declared else None,
            scheduled_minutes=load.scheduled,
            overload_minutes=load.overload_minutes if declared else None,
            overloaded=declared and load.overload_minutes > 0,
            ratio=load.ratio,
        )

    async def _db_now(self) -> datetime:
        """The database clock, read once.

        Used for every "now" the planner stamps or compares against. A value
        computed in Python is a second clock that can disagree with the server's,
        and the disagreement shows up as a session that started before it was
        created.
        """
        value = await self.sessions.session.scalar(select(func.now()))
        return _aware(value)

    async def _assert_window_free(
        self, *, owner: User, start: datetime, end: datetime, exclude_id: uuid.UUID | None = None
    ) -> None:
        """Refuse a work-session window that is already reserved.

        Two questions, in the order that makes the message useful, both scoped
        by ``owner_id`` in the query so one account's calendar can never decide
        another's write:

        1. Does it overlap another **live work session**? That is the
           double-booking the audit demonstrated: two 201s for the same hour.
        2. Does it overlap a **calendar event**? A session placed over a meeting
           is a block of time the user has already promised to somebody else, and
           every other surface in the system — the scheduler's busy list, the
           conflict detector — treats that interval as spoken for. Refusing the
           write is the only place the rule can be enforced; reporting it later
           leaves the double-booking already stored.

        **Half-open, like every other range here**: a session ending at 10:00
        and one starting at 10:00 are back-to-back and both are allowed. A
        calendar made of adjacent blocks is not a collision.

        Cancelled sessions are ignored — a cancelled block holds no time — and
        ``exclude_id`` is the row under edit, because a session overlaps itself
        on every instant of its own window.

        **The reads are serialised per owner** by a transaction-scoped advisory
        lock taken *before* them (:func:`_lock_owner_calendar`). The check and
        the insert that follows it are two statements, so two simultaneous POSTs
        for the same slot could both read an empty calendar and both write —
        which is the very defect this method exists to close, reached by a
        different road. The lock is released when the repository's own commit
        ends the transaction, so it covers the check and the write and nothing
        else. This is the application-level substitute for the database-level
        guard discussed in the report: a GiST exclusion constraint cannot be used
        here, because ``owner_id WITH =`` needs ``btree_gist`` and this build has
        no contrib extensions at all.

        Raises:
            ConflictError: 409, naming the conflicting row and the overlap. The
                details carry the conflicting id so a client can offer to move
                *that* block rather than to guess which one is in the way.
        """
        await _lock_owner_calendar(self.sessions.session, owner.id)
        sessions, session_total = await self.sessions.overlapping_for_user(
            owner.id, start=start, end=end, exclude_id=exclude_id, limit=1
        )
        if sessions:
            clash = sessions[0]
            raise ConflictError(
                "That time overlaps another work session.",
                details={
                    "conflict": "work_session",
                    "conflicting_id": str(clash.id),
                    "conflicting_start": _aware(clash.scheduled_start).isoformat(),
                    "conflicting_end": _aware(clash.scheduled_end).isoformat(),
                    "conflicting_sessions": session_total,
                    "requested_start": _aware(start).isoformat(),
                    "requested_end": _aware(end).isoformat(),
                },
            )
        events, event_total = await self.events.overlapping_for_user(
            owner.id, start=start, end=end, limit=1
        )
        if events:
            clash = events[0]
            raise ConflictError(
                "That time overlaps a calendar event.",
                details={
                    "conflict": "calendar_event",
                    "conflicting_id": str(clash.id),
                    "conflicting_title": clash.title,
                    "conflicting_start": _aware(clash.starts_at).isoformat(),
                    "conflicting_end": _aware(clash.ends_at).isoformat(),
                    "conflicting_events": event_total,
                    "requested_start": _aware(start).isoformat(),
                    "requested_end": _aware(end).isoformat(),
                },
            )

    async def _owned_project(self, project_id: uuid.UUID | None, owner: User) -> uuid.UUID | None:
        if project_id is None:
            return None
        if await self.projects.get_by_id_for_user(project_id, owner.id) is None:
            raise NotFoundError(_PROJECT_NOT_FOUND)
        return project_id

    async def _owned_task(self, task_id: uuid.UUID | None, owner: User) -> uuid.UUID | None:
        if task_id is None:
            return None
        if await self.tasks.get_by_id_for_user(task_id, owner.id) is None:
            raise NotFoundError(_TASK_NOT_FOUND)
        return task_id

    def _owned_event(self, event: CalendarEvent, owner: User) -> None:
        """Tripwire on a row assembled some other way; the query is the real check."""
        if event.owner_id != owner.id:
            raise NotFoundError(_EVENT_NOT_FOUND)

    def _owned_session(self, session: WorkSession, owner: User) -> None:
        if session.owner_id != owner.id:
            raise NotFoundError(_SESSION_NOT_FOUND)

    async def _record(
        self,
        event: ActivityEvent,
        *,
        owner: User,
        project_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> None:
        """Write one activity event, or do nothing when no feed is configured.

        Never raises: history is observability of the work, not a precondition
        for doing it.
        """
        if self.activity is None:
            return
        await self.activity.record(
            event.value,
            user_id=owner.id,
            project_id=project_id,
            task_id=task_id,
            metadata=metadata,
        )


def _lock_owner_calendar(session: Any, owner_id: uuid.UUID) -> Any:
    """Serialise one owner's collision checks against their own writes.

    ``pg_advisory_xact_lock`` on a key derived from ``owner_id``, so two
    simultaneous writes for one account queue behind each other while two
    accounts never wait for each other. **Transaction-scoped**, so the lock is
    taken by the first statement of the transaction and released by whatever
    commits it — here, the repository call that writes the row, which is exactly
    the span the check has to cover.

    The key is a BLAKE2b digest of the owner's uuid split into two ``int4``s, the
    same construction ``tests/conftest.py`` uses for the suite's own database
    lock, so there is one derivation of "an advisory key for this identifier"
    in the repository rather than two that could disagree.

    Returns:
        The awaited result of the statement, so the caller can ``await`` it
        without importing the driver.
    """
    digest = hashlib.blake2b(owner_id.bytes, digest_size=8).digest()
    first = int.from_bytes(digest[:4], "big", signed=True)
    second = int.from_bytes(digest[4:], "big", signed=True)
    return session.execute(select(func.pg_advisory_xact_lock(first, second)))


def _require_forward_window(start: datetime, end: datetime) -> None:
    """Reject an interval that ends before it starts.

    Caught here as well as in the schema because a PATCH that moves only one end
    of the window carries nothing for the schema to compare against, and the
    service sees the effective pair.
    """
    if _aware(end) <= _aware(start):
        raise ValidationError(
            "The end of the window must be later than its start.",
            details={"starts_at": _aware(start).isoformat(), "ends_at": _aware(end).isoformat()},
        )


def _reject_null_window_fields(sent: Mapping[str, Any], names: Sequence[str]) -> None:
    """Reject a PATCH that names a window end with ``null``.

    ``"location": null`` clears a nullable column and is the documented way to
    clear it. ``"starts_at": null`` does not clear anything — the column is
    ``NOT NULL`` — and before this check it reached ``_require_forward_window``,
    where ``_aware(None)`` raised ``AttributeError`` and the request left as an
    unhandled **500**. The schema had already decided the body was
    well-formed, so the service is where the promise has to be kept.

    Raises:
        ValidationError: 422, naming the field and saying what it means.
    """
    for name in names:
        if name in sent and sent[name] is None:
            raise ValidationError(
                f"{name} cannot be null; send the whole window instead of clearing one end of it.",
                details={"field": name},
            )
