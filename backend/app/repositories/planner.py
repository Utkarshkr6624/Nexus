"""Data access for the planner: calendar events, work sessions and availability.

The repository owns SQL only. It never raises domain errors — the two guards
against a *programming* error are the field allowlist in each ``update_fields``
and the sort allowlists in each ``list_for_user``, both of which reject a bad
name with :class:`ValueError` because the name arrives from code, not from a
request body.

Three ideas carry the whole file.

**Ownership is a predicate, not a filter.** ``owner_id`` is in the ``WHERE``
clause of every method that takes one. A row the caller may not see is never
loaded at all, and another user's id is answered with ``None`` — identically to
an id that does not exist, so this cannot be used to probe which ids are real.

**Every range is half-open and always bounded.** The overlap predicate is
``starts_at < end AND ends_at > start``, never ``BETWEEN``: with ``BETWEEN``, an
event ending at 10:00 and one starting at 10:00 are "overlapping", which is
exactly the back-to-back pair a calendar is built out of. And because the
predicate has to be expressed as a range at all, every list method takes
``start``/``end`` as required keyword arguments — there is no code path that
runs the unbounded ``SELECT * FROM calendar_events WHERE owner_id = ?``, which
on a table that never shrinks is the query that eventually takes the process
down. The one listing without a range, ``AvailabilityRuleRepository.list_for_user``,
is bounded by a unique constraint instead: ``(owner_id, weekday, starts_at)``
caps it at 24 x 7 = 168 rows.

**Minutes come from one grouped query.** ``scheduled_minutes_for_user`` and
``actual_minutes_for_user`` are a single ``GROUP BY day`` each rather than a
loop of per-day counts, so a month view is two round trips and its buckets
cannot disagree with each other mid-write.

**The collision check is one indexed read, not a scan.** ``overlapping_for_user``
on each of the two tables answers "does this window touch something already
reserved", which every session write has to ask before it persists. It is the
same half-open predicate the listings use, so a session cannot overlap a
neighbouring one by the boundary rule (``10:00`` end against ``10:00`` start is
back-to-back, not a clash), and it is served by
``ix_work_sessions_owner_scheduled_start`` because ``owner_id`` is in the
predicate.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.models.planner import AvailabilityRule, CalendarEvent, WorkSession, WorkSessionStatus

__all__ = [
    "AvailabilityRuleRepository",
    "CalendarEventRepository",
    "WorkSessionRepository",
]

#: ``(owner_id, starts_at)`` is the index both calendar ranges are served by.
#: There is deliberately no standalone index on ``project_id``/``task_id``:
#: every query that filters on those also filters on ``owner_id`` first, so
#: PostgreSQL reaches them through this index and a second one would only add
#: write cost. It would pay off only if "every event for this project ever"
#: became a real question, which no route in this phase asks.
_EVENT_SORT_COLUMNS: dict[str, InstrumentedAttribute] = {
    "all_day": CalendarEvent.all_day,
    "created_at": CalendarEvent.created_at,
    "ends_at": CalendarEvent.ends_at,
    "event_type": CalendarEvent.event_type,
    "starts_at": CalendarEvent.starts_at,
    "title": CalendarEvent.title,
    "updated_at": CalendarEvent.updated_at,
}

_SESSION_SORT_COLUMNS: dict[str, InstrumentedAttribute] = {
    "actual_end": WorkSession.actual_end,
    "actual_minutes": WorkSession.actual_minutes,
    "actual_start": WorkSession.actual_start,
    "created_at": WorkSession.created_at,
    "scheduled_end": WorkSession.scheduled_end,
    "scheduled_start": WorkSession.scheduled_start,
    "status": WorkSession.status,
    "updated_at": WorkSession.updated_at,
}

#: The columns each ``update_fields`` will write. ``id``, ``created_at`` and
#: ``updated_at`` are identity and timeline; ``owner_id`` is the authorisation
#: anchor and the two foreign keys are which workspace the row belongs to — all
#: structural, and a partial update that can reach one is a partial update that
#: can move work between accounts.
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
        "actual_end",
        "actual_minutes",
        "actual_start",
        "estimated_minutes",
        "project_id",
        "scheduled_end",
        "scheduled_start",
        "status",
        "task_id",
    }
)

#: A cancelled session holds no time: it was called off, so counting it against
#: the day's capacity would report an overload the user does not have.
_LIVE_SESSION_STATUSES = ("planned", "active", "completed")


def _order_by_clauses(
    sort: str, order: str, allowed: Mapping[str, InstrumentedAttribute], table: str
) -> tuple[Any, ...]:
    """Resolve a public ``sort``/``order`` pair into ORDER BY expressions.

    ``ORDER BY`` takes an expression rather than a bound parameter, so a sort
    name interpolated into it would be SQL injection behind a ``sort`` query
    parameter. The name is resolved against an allowlist instead and an unknown
    one is a :class:`ValueError` — failing closed, never falling back to a
    default column, because a silent fallback would return a plausible ordering
    for a request that asked for a different one.

    The primary key is appended as a tiebreak: ``created_at`` is a one-second
    ``server_default``, so two rows created in the same tick can come back in
    either order, and a listing whose order varies between calls cannot be
    paginated or diffed.
    """
    column = allowed.get(sort)
    if column is None:
        raise ValueError(
            f"Cannot sort {table} by {sort!r}; "
            f"list_for_user accepts only {', '.join(sorted(allowed))}."
        )
    if order == "asc":
        return column.asc(), table.id.asc()
    if order == "desc":
        return column.desc(), table.id.desc()
    raise ValueError(f"Cannot sort {table} {order!r}; expected 'asc' or 'desc'.")


def _overlapping(
    filters: list, start: datetime, end: datetime, starts_at: Any, ends_at: Any
) -> None:
    """Append the half-open overlap predicate to ``filters``.

    ``starts_at < end AND ends_at > start``: two rows that merely touch are not
    an overlap, which is what a calendar of back-to-back meetings requires.
    Both bounds are also what keeps the statement bounded.
    """
    filters.append(starts_at < end)
    filters.append(ends_at > start)


def _zone(tz: str | None) -> ZoneInfo | None:
    """Resolve an IANA zone name, with ``None`` meaning UTC days.

    Raises:
        ValueError: If the zone name is not one this host knows. Resolved here
            rather than left to the caller, because a name PostgreSQL does not
            recognise surfaces as an opaque database error the caller cannot act
            on.
    """
    if tz is None:
        return None
    try:
        return ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f"Unknown IANA time zone: {tz!r}") from None


class CalendarEventRepository:
    """Calendar-event persistence bound to a single request-scoped session."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        owner_id: uuid.UUID,
        title: str,
        starts_at: datetime,
        ends_at: datetime,
        event_type: str = "other",
        description: str | None = None,
        project_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
        all_day: bool = False,
        location: str | None = None,
    ) -> CalendarEvent:
        """Insert an event and return it with server defaults populated.

        The window is not re-checked here: the schema layer rejects
        ``ends_at <= starts_at`` as a 422, and a repository that re-derived the
        rule would give the same failure two different shapes.
        """
        event = CalendarEvent(
            owner_id=owner_id,
            title=title.strip(),
            starts_at=starts_at,
            ends_at=ends_at,
            event_type=event_type,
            description=description,
            project_id=project_id,
            task_id=task_id,
            all_day=all_day,
            location=location,
        )
        self.session.add(event)
        await self.session.commit()
        await self.session.refresh(event)
        return event

    async def get_by_id_for_user(
        self, event_id: uuid.UUID, owner_id: uuid.UUID
    ) -> CalendarEvent | None:
        """Return the event only when it also belongs to this user.

        Ownership is part of the lookup rather than a check afterwards: knowing
        an id is not authorisation.
        """
        result = await self.session.execute(
            select(CalendarEvent).where(
                CalendarEvent.id == event_id, CalendarEvent.owner_id == owner_id
            )
        )
        return result.scalar_one_or_none()

    async def list_for_user(
        self,
        owner_id: uuid.UUID,
        *,
        start: datetime,
        end: datetime,
        project_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
        event_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
        sort: str = "starts_at",
        order: str = "asc",
    ) -> tuple[list[CalendarEvent], int]:
        """List one owner's events in a half-open window, with the unpaginated total.

        Returns:
            The page of rows and the number of rows the filters match — counted
            in SQL, so it is the size of the result set rather than of the slice.
        """
        filters = [CalendarEvent.owner_id == owner_id]
        _overlapping(filters, start, end, CalendarEvent.starts_at, CalendarEvent.ends_at)
        if project_id is not None:
            filters.append(CalendarEvent.project_id == project_id)
        if task_id is not None:
            filters.append(CalendarEvent.task_id == task_id)
        if event_type is not None:
            filters.append(CalendarEvent.event_type == event_type)

        page = (
            select(CalendarEvent)
            .where(*filters)
            .order_by(*_order_by_clauses(sort, order, _EVENT_SORT_COLUMNS, CalendarEvent))
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(page)
        rows = list(result.scalars().all())

        total = int(
            await self.session.scalar(
                select(func.count()).select_from(CalendarEvent).where(*filters)
            )
        )
        return rows, total

    async def count_for_user(self, owner_id: uuid.UUID, *, start: datetime, end: datetime) -> int:
        """Count the owner's events that overlap the window."""
        filters = [CalendarEvent.owner_id == owner_id]
        _overlapping(filters, start, end, CalendarEvent.starts_at, CalendarEvent.ends_at)
        result = await self.session.execute(
            select(func.count()).select_from(CalendarEvent).where(*filters)
        )
        return int(result.scalar_one())

    async def overlapping_for_user(
        self,
        owner_id: uuid.UUID,
        *,
        start: datetime,
        end: datetime,
        limit: int = 1,
    ) -> tuple[list[CalendarEvent], int]:
        """Return the owner's events that overlap ``[start, end)``, earliest first.

        The write-side collision check, and separate from
        :meth:`list_for_user` because it answers a different question: not "show
        me this window" but "does this window touch something". ``limit``
        defaults to one because the caller only ever needs the *first* clash to
        refuse a write and name it — asking for a thousand rows to report one
        conflict is how a collision check becomes the slow part of a POST.

        Ordering by ``starts_at`` (with the id tiebreak) is what makes
        ``limit=1`` deterministic: two events overlapping the same write would
        otherwise be reported in whichever order the plan chose, and the refusal
        would name a different event on each attempt.

        Returns:
            The rows, and the number of rows the predicate matches — so a caller
            can report "3 events are in the way" without reading them all.
        """
        filters = [CalendarEvent.owner_id == owner_id]
        _overlapping(filters, start, end, CalendarEvent.starts_at, CalendarEvent.ends_at)
        page = (
            select(CalendarEvent)
            .where(*filters)
            .order_by(CalendarEvent.starts_at.asc(), CalendarEvent.id.asc())
            .limit(limit)
        )
        result = await self.session.execute(page)
        rows = list(result.scalars().all())
        total = int(
            await self.session.scalar(
                select(func.count()).select_from(CalendarEvent).where(*filters)
            )
        )
        return rows, total

    async def update_fields(self, event: CalendarEvent, **fields: object) -> CalendarEvent:
        """Apply a partial update and persist it.

        Raises:
            ValueError: If a field is not on the allowlist — a programming error
                rather than user input, so it fails at the call site instead of
                writing a column nobody meant to write.
        """
        rejected = sorted(set(fields) - _EVENT_UPDATABLE_FIELDS)
        if rejected:
            raise ValueError(
                f"Cannot write {', '.join(rejected)} on a calendar event; "
                f"update_fields accepts only {', '.join(sorted(_EVENT_UPDATABLE_FIELDS))}."
            )
        for key, value in fields.items():
            setattr(event, key, value)
        self.session.add(event)
        await self.session.commit()
        await self.session.refresh(event)
        return event

    async def delete(self, event: CalendarEvent) -> None:
        """Hard-delete the event row.

        ``work_sessions`` that hang off it cascade; nothing else does. An event
        is a statement about time, not a record of work, so losing it loses no
        history worth keeping.
        """
        await self.session.delete(event)
        await self.session.commit()


class WorkSessionRepository:
    """Work-session persistence bound to a single request-scoped session."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        owner_id: uuid.UUID,
        scheduled_start: datetime,
        scheduled_end: datetime,
        task_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
        estimated_minutes: int | None = None,
        status: str = "planned",
        actual_start: datetime | None = None,
        actual_end: datetime | None = None,
        actual_minutes: int = 0,
    ) -> WorkSession:
        """Insert a session and return it with server defaults populated.

        ``estimated_minutes`` is nullable for the same reason
        ``tasks.estimated_minutes`` is: "not estimated" and "estimated at zero"
        are different answers.
        """
        session_row = WorkSession(
            owner_id=owner_id,
            scheduled_start=scheduled_start,
            scheduled_end=scheduled_end,
            task_id=task_id,
            project_id=project_id,
            estimated_minutes=estimated_minutes,
            status=status,
            actual_start=actual_start,
            actual_end=actual_end,
            actual_minutes=actual_minutes,
        )
        self.session.add(session_row)
        await self.session.commit()
        await self.session.refresh(session_row)
        return session_row

    async def get_by_id_for_user(
        self, session_id: uuid.UUID, owner_id: uuid.UUID
    ) -> WorkSession | None:
        """Return the session only when it also belongs to this user."""
        result = await self.session.execute(
            select(WorkSession).where(
                WorkSession.id == session_id, WorkSession.owner_id == owner_id
            )
        )
        return result.scalar_one_or_none()

    async def list_for_user(
        self,
        owner_id: uuid.UUID,
        *,
        start: datetime,
        end: datetime,
        task_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
        sort: str = "scheduled_start",
        order: str = "asc",
    ) -> tuple[list[WorkSession], int]:
        """List one owner's sessions in a half-open window, with the total."""
        filters = [WorkSession.owner_id == owner_id]
        _overlapping(filters, start, end, WorkSession.scheduled_start, WorkSession.scheduled_end)
        if task_id is not None:
            filters.append(WorkSession.task_id == task_id)
        if project_id is not None:
            filters.append(WorkSession.project_id == project_id)
        if status is not None:
            filters.append(WorkSession.status == status)

        page = (
            select(WorkSession)
            .where(*filters)
            .order_by(*_order_by_clauses(sort, order, _SESSION_SORT_COLUMNS, WorkSession))
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(page)
        rows = list(result.scalars().all())

        total = int(
            await self.session.scalar(select(func.count()).select_from(WorkSession).where(*filters))
        )
        return rows, total

    async def overlapping_for_user(
        self,
        owner_id: uuid.UUID,
        *,
        start: datetime,
        end: datetime,
        exclude_id: uuid.UUID | None = None,
        limit: int = 1,
    ) -> tuple[list[WorkSession], int]:
        """Return the owner's sessions that overlap ``[start, end)``, earliest first.

        The write-side collision check. Two things distinguish it from
        :meth:`list_for_user`:

        * **Cancelled sessions are excluded.** A cancelled block holds no time,
          so refusing a write because of one would stop the user re-using a slot
          they deliberately released.
        * ``exclude_id`` is the session being moved. A PATCH that shifts a
          session by one minute overlaps *itself* on every timestamp it is
          compared against, and a check that cannot exclude the row under edit
          would refuse every reschedule.

        ``limit`` defaults to one for the same reason as the event equivalent:
        the caller needs the first clash to refuse a write and name it.

        Returns:
            The rows, and the number of live rows the predicate matches.
        """
        filters = [
            WorkSession.owner_id == owner_id,
            WorkSession.status != WorkSessionStatus.CANCELLED.value,
        ]
        _overlapping(filters, start, end, WorkSession.scheduled_start, WorkSession.scheduled_end)
        if exclude_id is not None:
            filters.append(WorkSession.id != exclude_id)
        page = (
            select(WorkSession)
            .where(*filters)
            .order_by(WorkSession.scheduled_start.asc(), WorkSession.id.asc())
            .limit(limit)
        )
        result = await self.session.execute(page)
        rows = list(result.scalars().all())
        total = int(
            await self.session.scalar(select(func.count()).select_from(WorkSession).where(*filters))
        )
        return rows, total

    async def update_fields(self, session_row: WorkSession, **fields: object) -> WorkSession:
        """Apply a partial update and persist it.

        ``actual_minutes`` is on the allowlist but is never written as a running
        sum computed in Python: see the note on
        :attr:`app.models.task.Task.actual_minutes` — a read-modify-write
        increment loses time the moment two tabs write at once.
        """
        rejected = sorted(set(fields) - _SESSION_UPDATABLE_FIELDS)
        if rejected:
            raise ValueError(
                f"Cannot write {', '.join(rejected)} on a work session; "
                f"update_fields accepts only {', '.join(sorted(_SESSION_UPDATABLE_FIELDS))}."
            )
        for key, value in fields.items():
            setattr(session_row, key, value)
        self.session.add(session_row)
        await self.session.commit()
        await self.session.refresh(session_row)
        return session_row

    async def delete(self, session_row: WorkSession) -> None:
        """Hard-delete the session row."""
        await self.session.delete(session_row)
        await self.session.commit()

    async def scheduled_minutes_for_user(
        self, owner_id: uuid.UUID, *, start: datetime, end: datetime, tz: str | None = None
    ) -> list[tuple[date, int]]:
        """Return ``(local_date, scheduled_minutes)`` per day, in one query.

        Computed from the session's own window rather than from
        ``estimated_minutes``: the estimate is what the task *thought* the work
        would take, and an overload view has to be about the time the calendar
        is actually committed to.

        A session spanning midnight contributes its whole duration to the day
        it starts on. Splitting it would be more accurate and would also mean
        the per-day numbers no longer sum to anything the caller can check
        against a session's own duration.

        Cancelled sessions are excluded — see :data:`_LIVE_SESSION_STATUSES`.

        Args:
            owner_id: The user the sessions belong to.
            start: Inclusive start of the window, as an aware instant.
            end: Exclusive end of the window, as an aware instant.
            tz: IANA zone the days are cut on; ``None`` for UTC days.
        """
        return await self._minutes_per_day(
            owner_id,
            instant=WorkSession.scheduled_start,
            start=start,
            end=end,
            tz=tz,
            minutes=func.extract("epoch", WorkSession.scheduled_end - WorkSession.scheduled_start)
            / 60.0,
        )

    async def actual_minutes_for_user(
        self,
        owner_id: uuid.UUID,
        *,
        start: datetime,
        end: datetime,
        tz: str | None = None,
    ) -> list[tuple[date, int]]:
        """Return ``(local_date, actual_minutes)`` per day, in one query.

        Summed from the ``actual_minutes`` column rather than measured from
        ``actual_start``/``actual_end``: the column is the already-rounded
        figure every other surface reports, so this view cannot disagree with
        the task card by a rounding rule the user has never seen. Days are cut
        on ``actual_start``, and sessions that never started contribute to no
        day at all.
        """
        return await self._minutes_per_day(
            owner_id,
            instant=WorkSession.actual_start,
            start=start,
            end=end,
            tz=tz,
            minutes=WorkSession.actual_minutes,
            require_instant=True,
        )

    async def _minutes_per_day(
        self,
        owner_id: uuid.UUID,
        *,
        instant: InstrumentedAttribute,
        start: datetime,
        end: datetime,
        tz: str | None,
        minutes: Any,
        require_instant: bool = False,
    ) -> list[tuple[date, int]]:
        """Sum a minutes expression per local day over a bounded window.

        One query: a range scan on the owner and the instant column, answered
        from ``ix_work_sessions_owner_scheduled_start``, and never the user's
        whole history. The per-day ``GROUP BY`` is applied in Python rather than
        by ``date_trunc(..., AT TIME ZONE ...)`` for two reasons, both of which
        bit:

        * The grouping zone is resolved by the **same** :mod:`zoneinfo` the
          planner service uses to compute the day's boundaries. PostgreSQL has
          its own tzdata; where the two disagree — and a portable Windows
          PostgreSQL install can ship with **no** zone directory at all, so
          ``timezone('Europe/Berlin', ...)`` simply errors — a SQL-side day and
          a service-side day can land on different dates, and the per-day view
          would then contradict the day list rendered beside it.
        * Grouping a bounded window in Python costs one pass over a few hundred
          rows and removes that whole class of disagreement.

        Args:
            owner_id: The user the sessions belong to.
            instant: The column the days are cut on.
            start: Inclusive start of the window, as an aware instant.
            end: Exclusive end of the window, as an aware instant.
            tz: IANA zone the days are cut on; ``None`` for UTC days.
            minutes: The expression summed within each day.
            require_instant: Whether rows whose instant is NULL are dropped
                rather than being attributed to the start of the window.
        """
        zone = _zone(tz)
        statement = select(instant, minutes).where(
            WorkSession.owner_id == owner_id,
            instant >= start,
            instant < end,
            WorkSession.status.in_(_LIVE_SESSION_STATUSES),
        )
        if require_instant:
            statement = statement.where(instant.is_not(None))
        result = await self.session.execute(statement)

        totals: dict[date, float] = {}
        for row_instant, row_minutes in result.all():
            if row_instant is None:
                continue
            day = row_instant.astimezone(zone).date() if zone is not None else row_instant.date()
            totals[day] = totals.get(day, 0.0) + float(row_minutes)
        return [(day, round(minutes)) for day, minutes in sorted(totals.items())]


class AvailabilityRuleRepository:
    """Availability-rule persistence bound to a single request-scoped session."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_for_user(self, owner_id: uuid.UUID) -> list[AvailabilityRule]:
        """Return the owner's rules in weekday-then-time order.

        The one listing in this module without a range, and deliberately so:
        the table is a weekly pattern, ``(owner_id, weekday, starts_at)`` is
        unique, and that caps a user at 24 x 7 = 168 rows. A range query here
        would be a filter with nothing to filter on.
        """
        result = await self.session.execute(
            select(AvailabilityRule)
            .where(AvailabilityRule.owner_id == owner_id)
            .order_by(
                AvailabilityRule.weekday.asc(),
                AvailabilityRule.starts_at.asc(),
                AvailabilityRule.id.asc(),
            )
        )
        return list(result.scalars().all())

    async def replace_for_user(
        self, owner_id: uuid.UUID, rules: Sequence[Any]
    ) -> list[AvailabilityRule]:
        """Replace the owner's whole weekly pattern and return the new rows.

        A replace rather than a diff because ``PUT /availability`` states the
        whole week: a client that removed a rule means "I do not work then",
        which a diff-based endpoint has to distinguish from "I did not send
        this one". Replacing makes the resource's identity the week, not the
        rule row.

        The delete and the inserts share one SAVEPOINT
        -----------------------------------------------
        ``delete_for_user`` commits before this method used to insert, so a
        payload rejected by the unique constraint or by a check constraint —
        two rules sharing a Monday 09:00 start, a window that ends before it
        starts — left the user's week deleted and answered 409 for a change that
        never happened. The data was worse than the error: the client was told
        "conflict" while its calendar quietly lost every window it had.

        Atomicity is bought with an explicit savepoint rather than with a single
        statement. One statement would need the DELETE's ``RETURNING`` rows
        paired positionally with the submitted rules, and a data-modifying CTE
        sees its own DELETE through the *pre-statement* snapshot, so the
        insertion would have to be rebuilt out of the RETURNING set — the one
        place where a reordering silently turns a user's 09:00 window into a
        17:00 one. The savepoint keeps the submitted values paired with the
        submitted rules and lets the whole pair fail together, which is the only
        property the caller actually needs: either the week is exactly the week
        that was sent, or it is exactly the week that was already there.

        The commit stays *outside* the savepoint because the repository methods
        here each commit individually and ``replace_for_user`` has always
        committed — the savepoint release is what makes the statements durable
        together, and a commit inside the block would end the savepoint's life
        anyway. ``delete_for_user`` is untouched: the service calls it for a
        delete-only request and its single-statement transaction is already
        atomic on its own.

        Args:
            owner_id: The user whose pattern is being replaced.
            rules: The new rules, as objects or mappings carrying ``weekday``,
                ``starts_at``, ``ends_at`` and an optional ``label``.

        Returns:
            The new rules, refreshed from the database so their ids and
            timestamps are the stored ones rather than the ones the ORM was
            holding when the savepoint opened.

        Raises:
            IntegrityError: A payload the table refuses — a duplicate
                ``(weekday, starts_at)``, a weekday out of range, a window that
                does not advance. Raised with the owner's previous rules
                untouched, so the caller's existing calendar is still there.
        """
        rows = [AvailabilityRule(owner_id=owner_id, **_rule_fields(rule)) for rule in rules]
        async with self.session.begin_nested():
            await self.session.execute(
                delete(AvailabilityRule).where(AvailabilityRule.owner_id == owner_id)
            )
            for row in rows:
                self.session.add(row)
            await self.session.flush()
        await self.session.commit()
        # One re-read, not one per row: a full-week replace is up to 168 rules,
        # so the per-row ``refresh`` this replaces was 168 round trips to fetch
        # values the same statement already returns. Re-keyed by id because the
        # rows must come back in the order they were submitted, not in whatever
        # order the ``WHERE owner_id`` scan yields.
        stored = {row.id: row for row in await self.list_for_user(owner_id)}
        return [stored[row.id] for row in rows]

    async def delete_for_user(self, owner_id: uuid.UUID) -> None:
        """Delete every rule belonging to this user.

        A set-based ``DELETE`` so a concurrent PUT is a no-op rather than a
        failure. Called by the service for a delete-only request, which commits
        its own transaction; :meth:`replace_for_user` issues its own ``DELETE``
        inside a savepoint rather than calling this, because a committed delete
        is the one thing a failed replace cannot undo.
        """
        await self.session.execute(
            delete(AvailabilityRule).where(AvailabilityRule.owner_id == owner_id)
        )
        await self.session.commit()


def _rule_fields(rule: Any) -> dict[str, Any]:
    """Normalise one submitted rule to column kwargs.

    Accepts a mapping or an object with the same attributes so the service can
    hand over its validated schema objects directly, without this layer having
    to know which of the two Pydantic produced.
    """

    def field(name: str) -> Any:
        if isinstance(rule, Mapping):
            return rule.get(name)
        return getattr(rule, name, None)

    label = field("label")
    return {
        "weekday": field("weekday"),
        "starts_at": field("starts_at"),
        "ends_at": field("ends_at"),
        "label": label if label is None or isinstance(label, str) else str(label),
    }
