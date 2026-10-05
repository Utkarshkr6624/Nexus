"""Data access for the daily aggregates, and the grouped reads that feed them.

One row per user per day lives in
:class:`~app.models.analytics.DailyMetric`; everything below it exists to fill
that row and to read it back.

The repository owns SQL only. It never raises domain errors. The two guards
against a *programming* error are the field allowlist in
:meth:`AnalyticsRepository.upsert_daily` and the granularity allowlist in
:meth:`AnalyticsRepository.list_range`; both reject a bad name with
:class:`ValueError`, because the name arrives from code, not from a request body.

Four ideas carry the whole file.

**Ownership is a predicate, not a filter.** ``user_id``/``owner_id`` is in the
``WHERE`` clause of every method that takes one. A row the caller may not see is
never loaded at all, and another user's id is answered with an empty result —
identically to an id that does not exist, so this cannot be used to probe which
ids are real.

**The upsert is the idempotency anchor.** :meth:`AnalyticsRepository.upsert_daily`
is a real ``INSERT ... ON CONFLICT (user_id, metric_date) DO UPDATE`` against the
unique constraint, so recomputing a day converges on one row rather than appending
a second. This is what stops the double-counting the spec warns about: a rebuild
over a window that was already built produces identical rows, so a retried request
or an overlapping refresh cannot inflate a total. The method also reports *which*
of the two happened, because "inserted" and "corrected" are different facts for a
caller deciding whether the number it is about to show has ever been computed.

**Every aggregate is one grouped query, never a loop.** Each ``*_by_day`` method
is a single ``SELECT ... GROUP BY`` over a bounded window. A thirty-day rebuild
therefore issues a fixed number of statements regardless of how many days the
window holds — the alternative, a per-day loop, is the ``30 x N`` pattern that
turns one dashboard refresh into hundreds of round trips.

**Day boundaries are cut in one zone, named explicitly.** ``tasks.created_at``,
``work_sessions.scheduled_start`` and every other instant in this schema is
``timestamptz``. Calling ``func.date()`` on such a column does *not* return a
date in any particular zone — it converts through the connection's ``TimeZone``
setting first. Every day-bucketing expression in **this module** is therefore
spelled ``func.date(<ts> AT TIME ZONE current_setting('TimeZone'))``, and every
date-range predicate is that same zone's midnight expressed as an instant.

That zone is **the database server's own** — the zone ``now()`` is already
labelled with, and the zone the application host runs at. It used to be a hard
``'UTC'``, and the two were not interchangeable: the router resolved "today" from
``now()`` (server-local) while this module bucketed days in UTC, so for five and a
half hours of every day the window a user was looking at as *today* was a window
this module had filed under *yesterday*. "8 tasks completed today" and "8 tasks
completed yesterday" were both true of the same eight rows, and the dashboard
rendered whichever one it happened to ask.

The zone is named in the SQL rather than left implicit because a bare
``func.date(<ts>)`` follows whichever connection happened to run it, so the same
question could get two answers. Spelled out, the answer is a property of the
*query*; and :meth:`AnalyticsRepository.today` — the one place "today" is defined
for this surface, read by the router's window resolution and by the service
alike — asks the same clock, so it cuts the calendar the way the rows are cut.

:func:`utc_day` is kept beside :func:`local_day` for the two repositories that
import it (developer commits, learning activities). They are not this module's
surface, they were written against the UTC cut, and quietly changing the
arithmetic under their numbers fixes nothing anybody reported.

The repository holds no aggregation *rules* — which day does a task belong to? is
a business decision, and it lives in
:mod:`app.services.analytics.service`. What lives here is the bounded reading and
the upsert, because the conflict target is a constraint storage has to own.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import (
    Date,
    DateTime,
    cast,
    delete,
    func,
    literal,
    literal_column,
    select,
    union_all,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import ActivityLog
from app.models.analytics import DailyMetric
from app.models.enums import (
    ActivityEvent,
    CalendarEventType,
    NoteStatus,
    TaskPriority,
    TaskStatus,
    WorkSessionStatus,
)
from app.models.knowledge import (
    Bookmark,
    Concept,
    Document,
    KnowledgeLink,
    Note,
    Resource,
    note_tags,
)
from app.models.planner import AvailabilityRule, CalendarEvent, WorkSession
from app.models.project import Project
from app.models.tag import Tag
from app.models.task import Task

__all__ = ["METRIC_COLUMNS", "AnalyticsRepository", "local_midnight"]

#: The counter columns the upsert will write.
#:
#: ``user_id`` and ``metric_date`` are excluded because they are the conflict
#: target, not a value being set — letting a caller overwrite either would defeat
#: the very constraint that makes the upsert idempotent. ``id`` and ``updated_at``
#: are identity and bookkeeping.
_DAILY_METRIC_FIELDS = frozenset(
    {
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
)

#: Public ``metric`` names accepted by the trend reader, mapped to their column.
#:
#: ``SELECT`` takes an expression rather than a bound parameter, so a metric name
#: interpolated into it would be SQL injection behind a query parameter. The name
#: is resolved against this allowlist instead and an unknown one is a
#: :class:`ValueError` — failing closed, never falling back to a default column,
#: because a silent fallback would plot a plausible line for a request that asked
#: for a different one.
METRIC_COLUMNS: dict[str, Any] = {
    name: getattr(DailyMetric, name) for name in sorted(_DAILY_METRIC_FIELDS)
}

#: Granularities :meth:`AnalyticsRepository.list_range` will bucket on.
#:
#: ``date_trunc`` is applied to ``metric_date``, a bare ``DATE`` column, so the
#: truncation is pure calendar arithmetic with no timezone attached — which is the
#: reason SQL bucketing is *safe here* where it was not in Phase 4. See the module
#: docstring: there the column was a ``timestamptz`` whose truncation silently
#: depended on the session zone, and the answer had to be cut in Python by
#: :mod:`zoneinfo` so it could not disagree with the day view beside it.
#: Truncating a ``DATE`` has no such ambiguity, so the group-by moves into SQL and
#: a 90-day monthly series is one round trip rather than one per bucket.
GRANULARITIES = ("day", "week", "month")

#: A session that was called off holds no time. Counting it would report effort the
#: user never spent and — because the same figure feeds a focus score — would
#: reward cancelling as much as working.
_LIVE_SESSION_STATUSES = (
    WorkSessionStatus.PLANNED.value,
    WorkSessionStatus.ACTIVE.value,
    WorkSessionStatus.COMPLETED.value,
)

#: A task that is not finished and not abandoned. Anything else is neither open
#: workload nor overdue.
_OPEN_STATUSES = (TaskStatus.TODO.value, TaskStatus.IN_PROGRESS.value, TaskStatus.BLOCKED.value)

#: Rescheduling has no status column — moving a due date is not a state transition,
#: and the task row cannot tell a reschedule from any other edit — so the feed is
#: the only place the fact exists. ``TaskService`` emits ``TASK_RESCHEDULED`` for
#: exactly that reason.
RESCHEDULE_EVENT = ActivityEvent.TASK_RESCHEDULED.value
BLOCKED_EVENT = ActivityEvent.TASK_BLOCKED.value

#: Knowledge activity, for ``knowledge_events``. These are **exactly** the Phase 5
#: members of :class:`~app.models.enums.ActivityEvent` — no ``concept_updated``, no
#: ``category_*``, no ``document_*``, no ``note_deleted``, because Phase 5 never
#: emits them. Listing names no writer can produce would make the vocabulary look
#: richer than the feed is and would leave every phantom bucket at a permanent 0.
#:
#: Two absences are load-bearing rather than accidental. **``note_viewed`` does not
#: exist** — the enum's own docstring says so — so "knowledge viewed" is not
#: computable from anything Phases 1-5 store and no proxy is invented for it.
#: **``note_revision_created`` does not exist either**: a revision is recorded by
#: the edit that caused it, which already emits ``NOTE_UPDATED``.
_KNOWLEDGE_EVENTS = (
    ActivityEvent.NOTE_CREATED.value,
    ActivityEvent.NOTE_UPDATED.value,
    ActivityEvent.NOTE_ARCHIVED.value,
    ActivityEvent.NOTE_PUBLISHED.value,
    ActivityEvent.NOTE_RESTORED.value,
    ActivityEvent.NOTE_REVISION_RESTORED.value,
    ActivityEvent.CONCEPT_CREATED.value,
    ActivityEvent.RESOURCE_CREATED.value,
    ActivityEvent.BOOKMARK_CREATED.value,
    ActivityEvent.KNOWLEDGE_LINK_CREATED.value,
    ActivityEvent.KNOWLEDGE_LINK_REMOVED.value,
)

#: Phases 1-5 have no explicit "study session" concept, so learning analytics can
#: only read what the calendar actually records. This is the one event type that
#: means deliberate study time; every other learning figure comes from the
#: knowledge event stream.
_STUDY_EVENT_TYPE = CalendarEventType.STUDY.value


def local_midnight(day: date) -> Any:
    """The instant ``day`` begins on, in the server's own zone.

    ``CAST(:day AS TIMESTAMP) AT TIME ZONE current_setting('TimeZone')`` converts
    a bare calendar date into the ``timestamptz`` that date's local midnight is,
    which is the only instant a ``date`` range and a ``timestamptz`` column can be
    compared on. Built in SQL rather than in Python so the zone is the one the
    server applies everywhere else on this connection — the zone :func:`local_day`
    buckets with, the zone ``now()`` is labelled with, and the zone
    :meth:`AnalyticsRepository.today` cuts the day at. Three readings of "a day"
    is exactly the disagreement this module is written to prevent.

    Public because it is also the answer to "where does this calendar range
    start" for callers *above* this module. The service has to open a rolling
    window ("completions in the last thirty days") as a predicate on a
    ``timestamptz`` column; building that bound in Python with a hard-coded
    ``tzinfo=UTC`` is the fourth reading of "a day", and it was the one that made
    a thirty-day window 29.8 days long on a ``+05:30`` host. Imported rather than
    re-derived, so the bound is the same expression the buckets use.
    """
    return func.cast(day, DateTime).op("AT TIME ZONE")(func.current_setting("TimeZone"))


def _day_window(start: date, end: date) -> tuple[Any, Any]:
    """Convert an inclusive date range into the half-open local-midnight window.

    Half-open (``< end_exclusive``) rather than ``<= end`` so a row stamped at
    exactly midnight on the day after the window is excluded by arithmetic instead
    of by a comparison a future edit could flip. The instants are expressions
    rather than Python datetimes so no offset is baked in here: a ``+05:30`` host
    and a UTC host running the same query must bucket the same rows.
    """
    return local_midnight(start), local_midnight(end + timedelta(days=1))


def utc_day(instant_column: Any) -> Any:
    """Bucket a ``timestamptz`` column to its **UTC** calendar day.

    Retained for the repositories outside this surface that import it — developer
    commits and learning activities — which were built against a fixed UTC cut.
    Analytics' own rows use :func:`local_day`; see the module docstring for why
    the two are not the same calendar.
    """
    return func.date(instant_column.op("AT TIME ZONE")("UTC"))


def local_day(instant_column: Any) -> Any:
    """Bucket a ``timestamptz`` column to its calendar day **in the server's zone**.

    ``func.date(col)`` alone would convert through the connection's ``TimeZone``
    without saying so in the query; naming the zone inside the expression makes the
    answer a property of the query and lines it up with
    :meth:`AnalyticsRepository.today` and with the predicates built by
    :func:`_day_window`.

    Spelled ``AT TIME ZONE`` through :meth:`~sqlalchemy.sql.operators.Operators.op`
    rather than through SQLAlchemy's ``at_time_zone`` helper because that helper is
    not exposed on an ORM ``InstrumentedAttribute``; the emitted SQL is
    ``date(col AT TIME ZONE current_setting('TimeZone'))``.
    """
    return func.date(instant_column.op("AT TIME ZONE")(func.current_setting("TimeZone")))


class AnalyticsRepository:
    """Daily-aggregate persistence and the grouped reads that feed it."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # The daily-aggregate table
    # ------------------------------------------------------------------

    async def upsert_daily(
        self, *, user_id: uuid.UUID, metric_date: date, values: Mapping[str, int]
    ) -> bool:
        """Write one user-day aggregate, inserting or updating as needed.

        **This method is the idempotency anchor of the whole analytics engine.**
        ``ON CONFLICT (user_id, metric_date) DO UPDATE`` resolves against the
        table's unique constraint, so calling it twice for the same day with the
        same numbers leaves exactly one row with exactly those numbers. Without it,
        every rebuild would append a second row for every day it covered and every
        total derived from the table would double.

        The update assigns the full supplied set rather than incrementing: a day is
        *recomputed*, never accumulated onto. An incrementing upsert would be
        idempotent for identical inputs and silently wrong for a day whose
        underlying rows changed — which is the case that actually happens, when a
        user completes a task in a window they already rebuilt.

        Args:
            user_id: Whose aggregate this is.
            metric_date: The day being written.
            values: Counter names from :data:`_DAILY_METRIC_FIELDS`. Absent names
                keep whatever the row already holds; present names overwrite,
                including with ``0``.

        Returns:
            ``True`` when a row was inserted, ``False`` when an existing row was
            updated. The two are distinguished by PostgreSQL's ``xmax`` system
            column, which is zero on a freshly inserted tuple and non-zero once the
            row has been rewritten.

        Raises:
            ValueError: If a counter name is not on the allowlist — a programming
                error rather than user input, so it fails at the call site instead
                of writing a column nobody meant to write.
        """
        rejected = sorted(set(values) - _DAILY_METRIC_FIELDS)
        if rejected:
            raise ValueError(
                f"Cannot write {', '.join(rejected)} on a daily metric; "
                f"upsert_daily accepts only {', '.join(sorted(_DAILY_METRIC_FIELDS))}."
            )
        if not values:
            return False

        payload: dict[str, Any] = {
            "id": uuid.uuid4(),
            "user_id": user_id,
            "metric_date": metric_date,
            **{key: int(value) for key, value in values.items()},
        }
        statement = (
            pg_insert(DailyMetric)
            .values(**payload)
            .on_conflict_do_update(
                index_elements=[DailyMetric.user_id, DailyMetric.metric_date],
                set_={
                    **{key: payload[key] for key in values},
                    # `updated_at` is written by the statement rather than left to
                    # the model. `TimestampMixin.updated_at` carries an `onupdate`
                    # of `now()`, but that is a Core construct SQLAlchemy applies
                    # to UPDATE statements it generates itself; a Core
                    # `insert().on_conflict_do_update()` bypasses it entirely, so
                    # without this the column would keep the timestamp of the
                    # *first* rebuild forever. That timestamp is the only signal
                    # the analytics API has for "these numbers are stale" and the
                    # one the staleness banner renders, so a rebuild that did not
                    # move it would leave the dashboard claiming to be current
                    # while serving figures from hours or days ago.
                    "updated_at": func.now(),
                },
            )
            # `xmax = 0` distinguishes the two outcomes of the upsert: a tuple
            # inserted into a fresh slot carries the zero system version, while one
            # rewritten by DO UPDATE carries the updater's.
            .returning(literal_column("xmax = 0").label("was_inserted"))
        )
        result = await self.session.execute(statement)
        was_inserted = bool(result.scalar_one())
        await self.session.commit()
        return was_inserted

    async def upsert_many(self, owner_id: uuid.UUID, rows: Sequence[Mapping[str, Any]]) -> int:
        """Write one row per day in a single statement, replacing any that are there.

        The batch form of :meth:`upsert_daily`, for a caller that has already
        computed every day of a window. Same guarantee for the same reason, and one
        statement for a 366-day rebuild rather than 366 round trips.

        ``set_`` is built from :data:`_DAILY_METRIC_FIELDS` rather than from the
        supplied keys, so a column added to the model later cannot be written on
        insert and then silently left stale on update — the failure mode that makes
        a recompute *look* like it worked while the dashboard keeps serving last
        week's number.

        Returns:
            The number of rows written, which is ``len(rows)`` whether each one
            inserted or updated.
        """
        if not rows:
            return 0
        payload = []
        for row in rows:
            rejected = sorted((set(row) - _DAILY_METRIC_FIELDS) - {"metric_date"})
            if rejected:
                raise ValueError(
                    f"Cannot write {', '.join(rejected)} on a daily metric; "
                    f"upsert_many accepts only {', '.join(sorted(_DAILY_METRIC_FIELDS))}."
                )
            payload.append(
                {
                    "user_id": owner_id,
                    "metric_date": row["metric_date"],
                    **{column: row.get(column, 0) for column in sorted(_DAILY_METRIC_FIELDS)},
                }
            )
        statement = pg_insert(DailyMetric).values(payload)
        statement = statement.on_conflict_do_update(
            index_elements=[DailyMetric.user_id, DailyMetric.metric_date],
            set_={
                **{column: getattr(statement.excluded, column) for column in _DAILY_METRIC_FIELDS},
                # See `upsert_daily`: the batch form needs the same explicit write
                # for the same reason, or a recompute leaves the row looking
                # untouched to every freshness check that reads `updated_at`.
                "updated_at": func.now(),
            },
        )
        await self.session.execute(statement)
        await self.session.commit()
        return len(payload)

    async def covered_dates(self, owner_id: uuid.UUID, *, start: date, end: date) -> set[date]:
        """Which days in the inclusive range already have an aggregate row.

        Lets a caller fill only the gaps instead of recomputing a window it already
        has, which is the difference between a background catch-up and a full
        rebuild on every poll.
        """
        result = await self.session.execute(
            select(DailyMetric.metric_date).where(
                DailyMetric.user_id == owner_id,
                DailyMetric.metric_date >= start,
                DailyMetric.metric_date <= end,
            )
        )
        return set(result.scalars().all())

    async def list_range(
        self, user_id: uuid.UUID, *, start: date, end: date, granularity: str = "day"
    ) -> list[DailyMetric]:
        """Return the user's aggregates for a window, bucketed by granularity.

        At ``day`` the rows are the stored objects. At ``week``/``month`` the
        stored rows are summed into freshly constructed, **unattached** instances
        whose ``metric_date`` is the bucket's first day — an object the caller may
        read attributes from but must not expect to have been persisted. That is
        stated rather than hidden because the return type is the model: a caller
        that called ``session.add()`` on a bucketed row would try to write a second
        aggregate, and only the absence of a flush would stop it.

        **No weekly or monthly table exists, and that is deliberate.** Every weekly
        figure in the product is this function summing seven stored days, so
        rebuilding a day corrects every roll-up that reads it and there is no second
        copy to fall out of step. See migration ``0006`` for the full argument.

        Raises:
            ValueError: If ``granularity`` is not one of :data:`GRANULARITIES` — a
                programming error, since the name reaches here from a ``Literal``
                the request layer has already narrowed.
        """
        if granularity not in GRANULARITIES:
            raise ValueError(
                f"Cannot bucket analytics by {granularity!r}; "
                f"list_range accepts only {', '.join(GRANULARITIES)}."
            )

        filters = [
            DailyMetric.user_id == user_id,
            DailyMetric.metric_date >= start,
            DailyMetric.metric_date <= end,
        ]
        if granularity == "day":
            result = await self.session.execute(
                select(DailyMetric).where(*filters).order_by(DailyMetric.metric_date.asc())
            )
            return list(result.scalars().all())

        bucket = cast(func.date_trunc(granularity, DailyMetric.metric_date), Date).label("bucket")
        summed = [
            func.coalesce(func.sum(column), 0).label(name)
            for name, column in METRIC_COLUMNS.items()
        ]
        result = await self.session.execute(
            select(bucket, *summed).where(*filters).group_by(bucket).order_by(bucket.asc())
        )
        names = list(METRIC_COLUMNS)
        buckets: list[DailyMetric] = []
        for row in result.all():
            buckets.append(
                DailyMetric(
                    id=uuid.UUID(int=0),
                    user_id=user_id,
                    metric_date=row[0],
                    **dict(zip(names, (int(value) for value in row[1:]), strict=True)),
                )
            )
        return buckets

    async def latest_date(self, user_id: uuid.UUID) -> date | None:
        """The most recent day this user has an aggregate for, or ``None``.

        The staleness probe: ``None`` means nothing has ever been computed, and a
        date behind today means the numbers on screen are older than the user
        thinks. Either way the caller reports it rather than presenting a stale
        total as current.
        """
        result = await self.session.execute(
            select(func.max(DailyMetric.metric_date)).where(DailyMetric.user_id == user_id)
        )
        return result.scalar_one()

    async def latest_metric_date(self, user_id: uuid.UUID) -> date | None:
        """Alias of :meth:`latest_date` under the name the service layer uses."""
        return await self.latest_date(user_id)

    async def today(self) -> date:
        """Today's calendar day, according to the **database** clock.

        The one definition of "today" for this surface. The router resolves a
        window's missing ends from it, the service reads its ``overdue`` and
        ``still_overdue`` figures from it, and :func:`local_day` cuts every day
        bucket in the same zone — so "today" here means the same thing as the day
        a row was filed under.

        Not the host's calendar: on a machine ahead of UTC (the development box
        runs at +05:30) those disagree for five and a half hours a day, and in
        that window the dashboard showed an empty *today* beside yesterday's
        eight completed tasks — two true statements about the same eight rows.

        Read as ``now()::date`` so PostgreSQL applies the very ``TimeZone`` the
        day buckets use, rather than converting the instant a second time in
        Python and risking a different answer.
        """
        value = await self.session.scalar(select(func.now().cast(Date)))
        return value if isinstance(value, date) else datetime.now().date()

    async def as_local_time(self, instant: datetime) -> datetime:
        """``instant`` restated in the server's own zone.

        The Python-side counterpart of :func:`local_day`, for the instants a
        caller already holds rather than a column it can bucket in SQL. Its
        ``.date()`` is the day that column would have been filed under and its
        ``.hour`` is the hour the user actually lived through, both because the
        same expression is applied by the server rather than by a
        ``ZoneInfo`` looked up on the application host.

        That distinction is the whole point. ``instant.astimezone(UTC).hour`` was
        how the feature snapshot read ``time_of_day``, and on a ``+05:30`` server
        it labelled an evening's work as the early afternoon — a training input
        shifted by half a day for a quarter of the population, and a number that
        looks like a plain hour of the day and is not one.
        """
        return await self.session.scalar(
            select(
                literal(instant, DateTime(timezone=True)).op("AT TIME ZONE")(
                    func.current_setting("TimeZone")
                )
            )
        )

    async def aggregate_watermarks(
        self, user_id: uuid.UUID, *, start: date, end: date
    ) -> tuple[datetime | None, datetime | None]:
        """``(oldest aggregate written_at, newest source instant)`` for a window.

        The two halves of ``OverviewRead.stale``. "Every day in this window has a
        row" says the window was *measured*; it does not say the rows describe
        what is in the database now. A task completed after the last rebuild
        leaves a complete, fully covered window whose totals are one edit out of
        date, and ``stale: false`` for it is a claim nothing supports.

        Comparing the newest recorded activity against the oldest row computed
        from it answers that without storing anything extra: an aggregate is
        current exactly when nothing inside the window is newer than it. The
        caller compares them strictly, so a rebuild and the write it is
        recomputing from — which can share a timestamp — do not read as stale.

        The source side is one ``UNION ALL`` of per-table maxima rather than a
        join: the tables share ``owner_id`` and nothing else, so a join would
        multiply every row by every other table's rows to compute five
        independent numbers. Both statements together are two round trips.
        """
        oldest = (
            await self.session.execute(
                select(func.min(DailyMetric.updated_at)).where(
                    DailyMetric.user_id == user_id,
                    DailyMetric.metric_date >= start,
                    DailyMetric.metric_date <= end,
                )
            )
        ).scalar_one()
        start_local, end_local = _day_window(start, end)
        sources = union_all(
            *(
                select(func.max(column).label("at")).where(
                    owner_column == user_id, column >= start_local, column < end_local
                )
                for column, owner_column in (
                    (Task.created_at, Task.owner_id),
                    (Task.completed_at, Task.owner_id),
                    (Task.updated_at, Task.owner_id),
                    (WorkSession.actual_start, WorkSession.owner_id),
                    (ActivityLog.created_at, ActivityLog.user_id),
                )
            )
        ).subquery()
        newest = (
            await self.session.execute(select(func.max(sources.c.at)).select_from(sources))
        ).scalar_one()
        return oldest, newest

    async def count_range(self, user_id: uuid.UUID, *, start: date, end: date) -> int:
        """Count the user's daily aggregates in a window."""
        result = await self.session.execute(
            select(func.count())
            .select_from(DailyMetric)
            .where(
                DailyMetric.user_id == user_id,
                DailyMetric.metric_date >= start,
                DailyMetric.metric_date <= end,
            )
        )
        return int(result.scalar_one())

    async def delete_range(self, owner_id: uuid.UUID, *, start: date, end: date) -> int:
        """Delete the owner's rows in a window, reporting how many went.

        Set-based so a concurrent rebuild is a no-op rather than a failure. Nothing
        in the application calls this — a recompute overwrites rather than deleting
        — and it exists for the operational case of a window whose aggregation rules
        changed, where recomputing in place would blend two definitions of the same
        day.
        """
        result = await self.session.execute(
            delete(DailyMetric).where(
                DailyMetric.user_id == owner_id,
                DailyMetric.metric_date >= start,
                DailyMetric.metric_date <= end,
            )
        )
        await self.session.commit()
        return int(result.rowcount or 0)

    # ------------------------------------------------------------------
    # Source rows for the aggregation tier — one grouped query each
    # ------------------------------------------------------------------

    async def count_tasks_by_day(
        self, owner_id: uuid.UUID, *, start: date, end: date
    ) -> list[tuple[date, int]]:
        """``(local_day, tasks_created)`` for the window, in one query.

        Grouped on ``created_at``: that is the instant the row came into being, and
        it is the one a "tasks created" chart means. The window predicate is on the
        raw ``timestamptz`` column rather than on ``func.date(...)``, which is what
        lets it be an index range at all — wrapping the column in a function makes
        it un-indexable whatever indexes exist. Which index is a separate question,
        and this method used to answer it wrongly in its own docstring: it claimed
        the scan "stays inside ``ix_tasks_owner_status_due``", which is
        ``(owner_id, status, due_date)`` and mentions neither of this query's two
        range columns, so ``EXPLAIN`` showed a ``Seq Scan on tasks`` over the
        account's whole history. ``0011`` added ``ix_tasks_owner_created`` for
        this shape, and the plan is now a ``Bitmap Heap Scan``.
        """
        start_local, end_local = _day_window(start, end)
        day = local_day(Task.created_at)
        result = await self.session.execute(
            select(day, func.count())
            .where(
                Task.owner_id == owner_id,
                Task.created_at >= start_local,
                Task.created_at < end_local,
            )
            .group_by(day)
            .order_by(day.asc())
        )
        return [(row[0], int(row[1])) for row in result.all()]

    async def count_tasks_completed_by_day(
        self, owner_id: uuid.UUID, *, start: date, end: date
    ) -> list[tuple[date, int]]:
        """``(local_day, tasks_completed)`` for the window, in one query.

        Grouped on ``completed_at`` rather than ``updated_at``: the latter is
        rewritten by every later edit, so a task completed last week and renamed
        today would move to today. ``completed_at`` is stamped once, at the
        transition.
        """
        start_local, end_local = _day_window(start, end)
        day = local_day(Task.completed_at)
        result = await self.session.execute(
            select(day, func.count())
            .where(
                Task.owner_id == owner_id,
                Task.completed_at.is_not(None),
                Task.completed_at >= start_local,
                Task.completed_at < end_local,
            )
            .group_by(day)
            .order_by(day.asc())
        )
        return [(row[0], int(row[1])) for row in result.all()]

    async def count_tasks_overdue_by_day(
        self, owner_id: uuid.UUID, *, start: date, end: date
    ) -> list[tuple[date, int]]:
        """``(local_day, tasks_overdue)`` for the window, in one query.

        **"Overdue" here means "was due on this day and was not finished by the end
        of it."** The definition is stated because the word is overloaded: a task
        sitting overdue today is not overdue every day since its due date, it became
        overdue once. Counting it on the day it was actually due is what makes the
        daily series sum back into the open-overdue count instead of growing without
        bound.

        A task whose ``due_date`` falls on the day but which was completed that same
        day is not counted: ``due_date`` is a date and ``completed_at`` an instant,
        so a task completed at 09:00 on its due date is on time. The comparison
        cuts the instant with :func:`local_day`, so it lands on the same day the
        row was bucketed on and the same day
        :meth:`app.services.analytics.service.AnalyticsService._deadline_result`
        files the completion under — which is the reason it is not a
        ``timestamptz::date`` cast here and a UTC date there.

        **A cancelled task is not counted**, and that is the same rule
        :meth:`overdue_count_as_of` applies rather than a second opinion about
        what overdue means. Dropping a task says "I decided not to do this", not
        "I failed to do this in time", so a cancelled task is not an overdue
        commitment — on the day it was due or any other. Leaving it out here
        while :meth:`overdue_count_as_of` excluded it made the same task overdue
        in the daily series and not overdue in the workload figure, which is
        worse than either answer on its own.

        Note what the exclusion is **not**: this count is not restricted to
        currently-open statuses. A task completed *after* its due date was
        genuinely overdue on that due date, and removing it from history would
        rewrite the past to flatter the present — the very thing a daily series
        exists to record. So only ``CANCELLED`` is filtered out; a completed task
        still counts on the day it was late, which is what the completion
        comparison elsewhere reports as a late completion.
        """
        day = Task.due_date
        # ``local_day`` rather than a plain ``CAST(... AS DATE)``: the bare cast
        # resolves ``timestamptz::date`` through the session's ``TimeZone``, which
        # happens to be the right zone but by accident rather than by statement —
        # see this module's docstring.
        completed_day = local_day(Task.completed_at)
        result = await self.session.execute(
            select(day, func.count())
            .where(
                Task.owner_id == owner_id,
                Task.status != TaskStatus.CANCELLED.value,
                Task.due_date >= start,
                Task.due_date <= end,
                (Task.completed_at.is_(None)) | (completed_day > Task.due_date),
            )
            .group_by(day)
            .order_by(day.asc())
        )
        return [(row[0], int(row[1])) for row in result.all()]

    async def count_tasks_cancelled_by_day(
        self, owner_id: uuid.UUID, *, start: date, end: date
    ) -> list[tuple[date, int]]:
        """``(local_day, tasks_cancelled)`` for the window, in one query.

        A limitation stated rather than hidden: cancellation has no timestamp of its
        own, so this buckets on ``updated_at`` for tasks whose status is *now*
        ``cancelled``. A cancelled task edited afterwards moves to the later day. The
        total over a window is exact — it is the number of cancelled tasks whose
        last write fell inside it — but one day's figure can move when the task is
        touched again. A dedicated ``cancelled_at`` column is the fix, and it is
        deliberately not added here: a migration for one metric is a change every
        write path has to honour, and this is the honest approximation until that
        cost is worth paying.
        """
        start_local, end_local = _day_window(start, end)
        day = local_day(Task.updated_at)
        result = await self.session.execute(
            select(day, func.count())
            .where(
                Task.owner_id == owner_id,
                Task.status == TaskStatus.CANCELLED.value,
                Task.updated_at >= start_local,
                Task.updated_at < end_local,
            )
            .group_by(day)
            .order_by(day.asc())
        )
        return [(row[0], int(row[1])) for row in result.all()]

    async def event_counts_by_day(
        self,
        owner_id: uuid.UUID,
        *,
        start: date,
        end: date,
        event_types: Sequence[str],
    ) -> list[tuple[date, int]]:
        """``(local_day, count)`` for a set of activity event types, in one query.

        The only honest source for "rescheduled" and "blocked by hand". Neither has
        a column: a reschedule is a due-date edit, indistinguishable from any other
        edit on the task row, and a block that was later unblocked leaves nothing but
        the feed. Reading the task table instead would report a fabricated zero for
        both.
        """
        start_local, end_local = _day_window(start, end)
        day = local_day(ActivityLog.created_at)
        result = await self.session.execute(
            select(day, func.count())
            .where(
                ActivityLog.user_id == owner_id,
                ActivityLog.event_type.in_(list(event_types)),
                ActivityLog.created_at >= start_local,
                ActivityLog.created_at < end_local,
            )
            .group_by(day)
            .order_by(day.asc())
        )
        return [(row[0], int(row[1])) for row in result.all()]

    async def completed_pairs_in_range(
        self, owner_id: uuid.UUID, *, start: date, end: date
    ) -> list[tuple[uuid.UUID, int | None, int | None, date | None, date]]:
        """``(task_id, estimated, actual, due_date, completed_day)`` for completions.

        One query, and the single source for three figures at once — estimation
        accuracy (estimated vs actual), deadline adherence (due_date vs the
        completion's day) and cycle time. Pulling them from one read is what stops
        those three from disagreeing: they are literally the same rows.

        **The fifth element is a ``date``, not an instant: the completion's day
        as :func:`local_day` cuts it.** The window predicate is already the same
        local midnight, so the bucket and the bound are one calendar; returning
        the raw ``timestamptz`` instead left the caller to re-derive that day, and
        every caller that did so reached for ``.astimezone(UTC).date()``. On a
        ``+05:30`` server that is a different day from the one this query
        bucketed on for five and a half hours a day, which made
        ``/overview`` report ``deadlines.late: 0`` beside a ``tasks_overdue`` total
        counting the same task as late — one response, two verdicts, one row.

        **``actual`` is ``None`` when no time was ever recorded against the task.**
        It used to be ``int(row[2] or 0)`` — the ``tasks.actual_minutes`` column,
        which is ``NOT NULL DEFAULT 0`` and which the session write path does not
        maintain. Every completed task therefore reported an *actual* of zero, and
        the estimation score read that as an observation: a user with real work
        sessions behind those tasks was told they over-estimate by the full
        estimate, 100% of the time, forever. The honest answer for "how long did
        this take" when nothing timed it is that it is not known.

        The minutes that *were* recorded — the ``work_sessions`` rows, which the
        planner writes on every stop — are summed here instead, because they are
        the observation and the task column is a cache of it. A session cancelled
        before it ran holds no time and is excluded, as everywhere else.

        ``estimated_minutes`` may be ``None`` and is returned as such. The estimation
        score drops those pairs rather than treating "never estimated" as "estimated
        at zero", which would add a spurious zero-error row to the training data
        Phase 10 will consume.

        Only completions inside the window appear; an open task has no
        ``completed_at`` and so cannot be here at all.
        """
        start_local, end_local = _day_window(start, end)
        recorded = (
            select(
                WorkSession.task_id.label("task_id"),
                func.coalesce(func.sum(WorkSession.actual_minutes), 0).label("minutes"),
            )
            .where(
                WorkSession.owner_id == owner_id,
                WorkSession.task_id.is_not(None),
                WorkSession.status.not_in((WorkSessionStatus.CANCELLED.value,)),
            )
            .group_by(WorkSession.task_id)
            .subquery()
        )
        result = await self.session.execute(
            select(
                Task.id,
                Task.estimated_minutes,
                func.coalesce(recorded.c.minutes, Task.actual_minutes).label("actual"),
                Task.due_date,
                local_day(Task.completed_at).label("completed_day"),
            )
            .outerjoin(recorded, recorded.c.task_id == Task.id)
            .where(
                Task.owner_id == owner_id,
                Task.completed_at.is_not(None),
                Task.completed_at >= start_local,
                Task.completed_at < end_local,
            )
        )
        return [
            (row[0], row[1], None if int(row[2] or 0) <= 0 else int(row[2]), row[3], row[4])
            for row in result.all()
        ]

    async def avg_cycle_minutes_in_range(
        self, owner_id: uuid.UUID, *, start: date, end: date
    ) -> float | None:
        """Mean minutes from creation to completion, or ``None`` with no sample.

        Averaged in SQL so a busy month does not require transferring every
        ``created_at`` to count them, and ``None`` — not ``0`` — when nothing was
        completed in the window.
        """
        start_local, end_local = _day_window(start, end)
        statement = select(
            func.avg(func.extract("epoch", Task.completed_at - Task.created_at) / 60.0)
        ).where(
            Task.owner_id == owner_id,
            Task.completed_at.is_not(None),
            Task.completed_at >= start_local,
            Task.completed_at < end_local,
        )
        value = (await self.session.execute(statement)).scalar_one()
        return None if value is None else round(float(value), 1)

    async def overdue_count_as_of(
        self,
        owner_id: uuid.UUID,
        as_of: date,
        *,
        start: date | None = None,
        end: date | None = None,
    ) -> int:
        """Open tasks whose due date is strictly before ``as_of``.

        "Open" excludes completed *and* cancelled work: a task the user deliberately
        dropped is not an overdue commitment, and counting it would make a cleaned
        backlog look worse than a neglected one.

        The caller passes the date rather than this method reading a clock, so the
        figure is a function of its arguments and can be re-derived exactly later.
        ``as_of`` is normally the database's own ``func.now()`` date.

        ``start``/``end`` narrow the count to the tasks **due inside a window**,
        which is a different question from "how much is overdue right now" and the
        one a windowed endpoint has to answer. Without them a request for a
        December window returned today's open backlog, so every window ever asked
        for reported the same number — an answer that moved with the clock while
        carrying the caller's dates in the same response.
        """
        filters = [
            Task.owner_id == owner_id,
            Task.status.in_(_OPEN_STATUSES),
            Task.due_date.is_not(None),
            Task.due_date < as_of,
        ]
        if start is not None:
            filters.append(Task.due_date >= start)
        if end is not None:
            filters.append(Task.due_date <= end)
        result = await self.session.execute(select(func.count()).select_from(Task).where(*filters))
        return int(result.scalar_one())

    async def status_counts(self, owner_id: uuid.UUID) -> dict[str, int]:
        """Every task status bucket plus ``total``, in one ``GROUP BY``.

        Every member of :class:`~app.models.enums.TaskStatus` is present even when
        empty, so a caller can read ``counts["completed"]`` without a ``.get()``
        default and the response shape does not change when the last completed task
        is deleted. ``total`` is the sum of the buckets rather than a second query,
        so it cannot disagree with them mid-write.
        """
        result = await self.session.execute(
            select(Task.status, func.count()).where(Task.owner_id == owner_id).group_by(Task.status)
        )
        counts: dict[str, int] = {status.value: 0 for status in TaskStatus}
        for status_value, bucket in result.all():
            counts[str(status_value)] = int(bucket)
        counts["total"] = sum(value for key, value in counts.items() if key != "total")
        return counts

    async def priority_counts(self, owner_id: uuid.UUID) -> dict[str, int]:
        """Every priority bucket plus ``total``. Same shape rule as :meth:`status_counts`."""
        result = await self.session.execute(
            select(Task.priority, func.count())
            .where(Task.owner_id == owner_id)
            .group_by(Task.priority)
        )
        counts: dict[str, int] = {priority.value: 0 for priority in TaskPriority}
        for priority_value, bucket in result.all():
            counts[str(priority_value)] = int(bucket)
        counts["total"] = sum(value for key, value in counts.items() if key != "total")
        return counts

    async def open_task_counts_by_project(
        self, owner_id: uuid.UUID, as_of: date | None = None
    ) -> dict[uuid.UUID, int]:
        """``{project_id: open_tasks}``, one grouped query.

        ``as_of`` narrows the count to open tasks that are also overdue when it is
        given, and is otherwise the plain open count. Used by the workload surface
        and by the ML feature snapshot's ``project_open_task_count``.

        Keyed by project rather than joined to names, so a caller that only needs
        the counts does not pay for the join.
        """
        filters = [Task.owner_id == owner_id, Task.status.in_(_OPEN_STATUSES)]
        if as_of is not None:
            filters.extend([Task.due_date.is_not(None), Task.due_date < as_of])
        result = await self.session.execute(
            select(Task.project_id, func.count()).where(*filters).group_by(Task.project_id)
        )
        return {row[0]: int(row[1]) for row in result.all()}

    async def session_minutes_by_day(
        self, owner_id: uuid.UUID, *, start: date, end: date
    ) -> list[tuple[date, int, int]]:
        """``(local_day, actual_minutes, session_count)`` for the window, in one query.

        Summed from ``actual_minutes`` rather than measured from
        ``actual_start``/``actual_end``, so the aggregate cannot disagree with the
        task card by a rounding rule the user has never seen. Days are cut on
        ``actual_start``; a session that was planned and never started contributes
        to no day at all, which is correct — no time was spent.

        **Cancelled sessions are excluded**, exactly as
        :data:`app.repositories.planner._LIVE_SESSION_STATUSES` excludes them from
        the planner's own per-day minutes. A cancelled session holds no time, and
        including it would inflate both the day total and the focus score.
        """
        start_local, end_local = _day_window(start, end)
        day = local_day(WorkSession.actual_start)
        result = await self.session.execute(
            select(
                day,
                func.coalesce(func.sum(WorkSession.actual_minutes), 0),
                func.count(),
            )
            .where(
                WorkSession.owner_id == owner_id,
                WorkSession.actual_start.is_not(None),
                WorkSession.actual_start >= start_local,
                WorkSession.actual_start < end_local,
                WorkSession.status.not_in((WorkSessionStatus.CANCELLED.value,)),
            )
            .group_by(day)
            .order_by(day.asc())
        )
        return [(row[0], int(row[1]), int(row[2])) for row in result.all()]

    async def planned_minutes_by_day(
        self, owner_id: uuid.UUID, *, start: date, end: date
    ) -> list[tuple[date, int]]:
        """``(local_day, scheduled_minutes)`` for the window, in one query.

        The *committed* span rather than the copied ``estimated_minutes``: what a day
        was going to cost is a property of the calendar, and the task's estimate is
        a separate figure that belongs to the estimation-accuracy comparison.
        Cancelled sessions are excluded, as everywhere else.
        """
        start_local, end_local = _day_window(start, end)
        day = local_day(WorkSession.scheduled_start)
        minutes = (
            func.extract("epoch", WorkSession.scheduled_end - WorkSession.scheduled_start) / 60.0
        )
        result = await self.session.execute(
            select(day, func.coalesce(func.sum(minutes), 0))
            .where(
                WorkSession.owner_id == owner_id,
                WorkSession.scheduled_start >= start_local,
                WorkSession.scheduled_start < end_local,
                WorkSession.status.in_(_LIVE_SESSION_STATUSES),
            )
            .group_by(day)
            .order_by(day.asc())
        )
        return [(row[0], round(float(row[1]))) for row in result.all()]

    async def session_summary_in_range(
        self, owner_id: uuid.UUID, *, start: date, end: date
    ) -> dict[str, float | int | None]:
        """Focus inputs for the whole window in a single row.

        One statement with conditional aggregates (``FILTER``) rather than five
        counts: the focus score needs the same population of sessions five different
        ways, and five statements could each see a different snapshot if a timer
        stopped between them. ``avg_minutes`` is ``None`` when nothing ran — an
        absent average, never a zero one.

        ``interruptions`` counts sessions in the window that did not reach
        ``completed``. It is a proxy for interruption, not a measurement of human
        attention, and the focus score's own explanation string says so where a user
        reads it.
        """
        start_local, end_local = _day_window(start, end)
        completed = WorkSession.status == WorkSessionStatus.COMPLETED.value
        statement = select(
            func.count().label("sessions"),
            func.coalesce(func.sum(WorkSession.actual_minutes), 0).label("minutes"),
            func.avg(WorkSession.actual_minutes).label("avg_minutes"),
            func.count().filter(completed).label("completed_sessions"),
            func.count().filter(~completed).label("interruptions"),
            func.coalesce(func.sum(WorkSession.actual_minutes).filter(completed), 0).label(
                "completed_minutes"
            ),
        ).where(
            WorkSession.owner_id == owner_id,
            WorkSession.actual_start.is_not(None),
            WorkSession.actual_start >= start_local,
            WorkSession.actual_start < end_local,
            WorkSession.status.not_in((WorkSessionStatus.CANCELLED.value,)),
        )
        row = (await self.session.execute(statement)).one()
        average = row.avg_minutes
        return {
            "sessions": int(row.sessions),
            "minutes": int(row.minutes),
            "avg_minutes": None if average is None else round(float(average), 1),
            "completed_sessions": int(row.completed_sessions),
            "interruptions": int(row.interruptions),
            "completed_minutes": int(row.completed_minutes),
        }

    async def work_minutes_by_project(
        self, owner_id: uuid.UUID, *, start: date, end: date
    ) -> dict[uuid.UUID, int]:
        """``{project_id: tracked_minutes}`` from sessions that ran, in one query.

        Read from ``work_sessions`` rather than summed from ``tasks.actual_minutes``
        because the session is what actually happened: it is per-block, carries a
        start instant to attribute to a day, and excludes cancelled work. A task's
        running total has none of those and would put every minute a task ever
        accumulated into one arbitrary day.

        Sessions with no project are absent from the mapping; the caller reports them
        separately as unassigned rather than folding them into a real project.
        """
        start_local, end_local = _day_window(start, end)
        result = await self.session.execute(
            select(
                WorkSession.project_id,
                func.coalesce(func.sum(WorkSession.actual_minutes), 0),
            )
            .where(
                WorkSession.owner_id == owner_id,
                WorkSession.project_id.is_not(None),
                WorkSession.actual_start.is_not(None),
                WorkSession.actual_start >= start_local,
                WorkSession.actual_start < end_local,
                WorkSession.status.not_in((WorkSessionStatus.CANCELLED.value,)),
            )
            .group_by(WorkSession.project_id)
        )
        return {row[0]: int(row[1]) for row in result.all()}

    async def calendar_events_by_day(
        self, owner_id: uuid.UUID, *, start: date, end: date
    ) -> list[tuple[date, int]]:
        """``(local_day, calendar_events)`` for the window, in one query.

        Counted on ``starts_at``. An event that ends tomorrow and started today is
        counted today, matching the planner's own per-day overlap arithmetic — a day
        with a twelve-hour block is one event on the day it begins.
        """
        start_local, end_local = _day_window(start, end)
        day = local_day(CalendarEvent.starts_at)
        result = await self.session.execute(
            select(day, func.count())
            .where(
                CalendarEvent.owner_id == owner_id,
                CalendarEvent.starts_at >= start_local,
                CalendarEvent.starts_at < end_local,
            )
            .group_by(day)
            .order_by(day.asc())
        )
        return [(row[0], int(row[1])) for row in result.all()]

    async def projects_touched_by_day(
        self, owner_id: uuid.UUID, *, start: date, end: date
    ) -> list[tuple[date, int]]:
        """``(local_day, distinct projects with activity that day)``, in one query.

        ``COUNT(DISTINCT project_id)`` over the feed. "Projects worked on" is a
        distinct count on purpose: six edits to one task are one project touched, and
        counting six would make a focused day look like a broad one.
        """
        start_local, end_local = _day_window(start, end)
        day = local_day(ActivityLog.created_at)
        result = await self.session.execute(
            select(day, func.count(func.distinct(ActivityLog.project_id)))
            .where(
                ActivityLog.user_id == owner_id,
                ActivityLog.project_id.is_not(None),
                ActivityLog.created_at >= start_local,
                ActivityLog.created_at < end_local,
            )
            .group_by(day)
            .order_by(day.asc())
        )
        return [(row[0], int(row[1])) for row in result.all()]

    async def study_totals_in_range(
        self, owner_id: uuid.UUID, *, start: date, end: date
    ) -> tuple[int, int]:
        """``(study_events, study_minutes)`` in one query.

        The honest basis for the learning figure: Phases 1-5 record no dedicated
        study session, so the only deliberate-study signal that exists is a calendar
        entry typed ``study``. It is counted and summed from the event's own span,
        not from an estimate.

        Returns ``(0, 0)`` — not ``None`` — because unlike a *rate* this is a count,
        and "the user booked zero study blocks" is a true statement. The learning
        read pairs it with an ``available`` flag derived from whether anything was
        ever recorded at all.
        """
        start_local, end_local = _day_window(start, end)
        minutes = func.extract("epoch", CalendarEvent.ends_at - CalendarEvent.starts_at) / 60.0
        result = await self.session.execute(
            select(func.count(), func.coalesce(func.sum(minutes), 0)).where(
                CalendarEvent.owner_id == owner_id,
                CalendarEvent.event_type == _STUDY_EVENT_TYPE,
                CalendarEvent.starts_at >= start_local,
                CalendarEvent.starts_at < end_local,
            )
        )
        row = result.one()
        return int(row[0]), round(float(row[1]))

    async def activity_days_in_range(
        self, owner_id: uuid.UUID, *, start: date, end: date
    ) -> list[date]:
        """Local days carrying at least one activity event, ascending.

        The consistency denominator's other half. It reads the *feed* rather than the
        task or session tables on purpose: "did the user show up" is about touching
        the product at all, and a user who rescheduled something without completing
        anything was still present. Days with no event simply do not appear, which is
        how a gap stays visible instead of becoming a zero.
        """
        start_local, end_local = _day_window(start, end)
        day = local_day(ActivityLog.created_at)
        result = await self.session.execute(
            select(day)
            .where(
                ActivityLog.user_id == owner_id,
                ActivityLog.created_at >= start_local,
                ActivityLog.created_at < end_local,
            )
            .group_by(day)
            .order_by(day.asc())
        )
        return [row[0] for row in result.all()]

    # ------------------------------------------------------------------
    # Knowledge
    # ------------------------------------------------------------------

    async def knowledge_counts(self, owner_id: uuid.UUID) -> dict[str, int]:
        """How much of each knowledge kind this user has, from six tables.

        Six ``COUNT``s rather than one join because the tables share nothing but
        ``owner_id``; a join would be a cartesian product multiplied by the size of
        the largest table to obtain six independent totals.

        Counted as *totals the user owns*, not as rows created in a window — this is
        the "how big is my knowledge base" figure. The per-window created/updated
        split comes from :meth:`knowledge_write_counts_in_range`.
        """
        statements = {
            "notes": select(func.count()).select_from(Note).where(Note.owner_id == owner_id),
            "concepts": select(func.count())
            .select_from(Concept)
            .where(Concept.owner_id == owner_id),
            "resources": select(func.count())
            .select_from(Resource)
            .where(Resource.owner_id == owner_id),
            "bookmarks": select(func.count())
            .select_from(Bookmark)
            .where(Bookmark.owner_id == owner_id),
            "documents": select(func.count())
            .select_from(Document)
            .where(Document.owner_id == owner_id),
            "links": select(func.count())
            .select_from(KnowledgeLink)
            .where(KnowledgeLink.owner_id == owner_id),
        }
        counts: dict[str, int] = {}
        for name, statement in statements.items():
            counts[name] = int((await self.session.execute(statement)).scalar_one())
        return counts

    async def knowledge_events_by_day(
        self, owner_id: uuid.UUID, *, start: date, end: date
    ) -> list[tuple[date, int]]:
        """``(local_day, knowledge_events)`` for the window, in one query.

        **What this counts, precisely:** every knowledge event in the feed, which is
        a record of *writes* — see :data:`_KNOWLEDGE_EVENTS`.

        It is **not** a view count, and Phase 5 does not record views: there is no
        ``note_viewed`` member in :class:`~app.models.enums.ActivityEvent`, so there
        is no row to count. The knowledge read therefore reports interactions as
        writes and says so in its ``basis`` field; "knowledge viewed" is not
        computable from what Phases 1-5 store, and inventing a proxy for it would be
        exactly the fabrication this phase forbids.
        """
        start_local, end_local = _day_window(start, end)
        day = local_day(ActivityLog.created_at)
        result = await self.session.execute(
            select(day, func.count())
            .where(
                ActivityLog.user_id == owner_id,
                ActivityLog.event_type.in_(list(_KNOWLEDGE_EVENTS)),
                ActivityLog.created_at >= start_local,
                ActivityLog.created_at < end_local,
            )
            .group_by(day)
            .order_by(day.asc())
        )
        return [(row[0], int(row[1])) for row in result.all()]

    async def knowledge_write_counts_in_range(
        self, owner_id: uuid.UUID, *, start: date, end: date
    ) -> dict[str, int]:
        """Write-side knowledge counters for the window, in one grouped query.

        Notes are split into created/updated from the feed's event names rather than
        from ``notes.created_at``/``updated_at``: ``updated_at`` is rewritten by every
        autosave, so a note created in January and saved today would be counted as
        *created* today. The feed records what happened, which is the question being
        asked.
        """
        start_local, end_local = _day_window(start, end)
        result = await self.session.execute(
            select(ActivityLog.event_type, func.count())
            .where(
                ActivityLog.user_id == owner_id,
                ActivityLog.event_type.in_(list(_KNOWLEDGE_EVENTS)),
                ActivityLog.created_at >= start_local,
                ActivityLog.created_at < end_local,
            )
            .group_by(ActivityLog.event_type)
        )
        counts: dict[str, int] = dict.fromkeys(_KNOWLEDGE_EVENTS, 0)
        for event_type, bucket in result.all():
            counts[str(event_type)] = int(bucket)
        interactions = sum(counts.values())
        counts["notes_created"] = counts.get(ActivityEvent.NOTE_CREATED.value, 0)
        counts["notes_updated"] = sum(
            counts[name]
            for name in (
                ActivityEvent.NOTE_UPDATED.value,
                ActivityEvent.NOTE_PUBLISHED.value,
                ActivityEvent.NOTE_ARCHIVED.value,
                ActivityEvent.NOTE_RESTORED.value,
            )
        )
        counts["concepts_created"] = counts.get(ActivityEvent.CONCEPT_CREATED.value, 0)
        counts["resources_added"] = counts.get(ActivityEvent.RESOURCE_CREATED.value, 0)
        counts["bookmarks_added"] = counts.get(ActivityEvent.BOOKMARK_CREATED.value, 0)
        counts["links_created"] = counts.get(ActivityEvent.KNOWLEDGE_LINK_CREATED.value, 0)
        # Every knowledge event in the window, writes only. See
        # `knowledge_events_by_day` for why a view count is not among them.
        counts["interactions"] = interactions
        return counts

    async def notes_by_status(self, owner_id: uuid.UUID) -> dict[str, int]:
        """Every note-status bucket, present even when empty. See :meth:`status_counts`."""
        result = await self.session.execute(
            select(Note.status, func.count()).where(Note.owner_id == owner_id).group_by(Note.status)
        )
        counts: dict[str, int] = {status.value: 0 for status in NoteStatus}
        for status_value, bucket in result.all():
            counts[str(status_value)] = int(bucket)
        counts["total"] = sum(value for key, value in counts.items() if key != "total")
        return counts

    async def knowledge_tag_counts(
        self, owner_id: uuid.UUID, *, start: date, end: date
    ) -> list[tuple[uuid.UUID, str, int]]:
        """``(tag_id, name, note_count)`` for tags used by notes touched in the window.

        **A limitation stated rather than hidden.** Phase 5 records its activity
        events with no ``task_id`` and no ``project_id`` — ``KnowledgeService`` calls
        ``activity.record(event, user_id=...)`` with only a title in the metadata — so
        the feed cannot be joined back to a note row, and the note table is the only
        available linkage. The window is therefore applied to ``notes.updated_at``,
        which an autosaving editor rewrites continuously: a note opened in January
        and merely re-saved this week can appear here.

        The alternative — using ``created_at`` — would under-report every tag on any
        note more than a few days old, which is worse for a "most active knowledge
        areas" list. The true fix is Phase 5 recording the note id on its events;
        this returns the best figure the stored data supports and says so.

        Tags with no qualifying note are not returned. Order is by count then name, so
        two identical calls return the same order.
        """
        day = local_day(Note.updated_at)
        statement = (
            select(Tag.id, Tag.name, func.count(func.distinct(Note.id)))
            .select_from(note_tags.join(Note, Note.id == note_tags.c.note_id))
            .join(Tag, Tag.id == note_tags.c.tag_id)
            .where(
                Tag.user_id == owner_id,
                Note.owner_id == owner_id,
                day >= start,
                day <= end,
            )
            .group_by(Tag.id, Tag.name)
            .order_by(func.count(func.distinct(Note.id)).desc(), Tag.name.asc())
        )
        result = await self.session.execute(statement)
        return [(row[0], row[1], int(row[2])) for row in result.all()]

    # ------------------------------------------------------------------
    # Per-project roll-ups
    # ------------------------------------------------------------------

    async def project_task_counts(
        self, owner_id: uuid.UUID, *, as_of: date
    ) -> list[tuple[uuid.UUID, str, str, int, int, int, int, int]]:
        """``(project_id, name, status, total, completed, remaining, overdue, open)``.

        One grouped query over ``projects LEFT JOIN tasks``, joined from the project
        side so a project with no tasks still appears — a new project with zero tasks
        is a real answer, not a missing row.

        ``completed`` counts rows whose status is ``completed``; ``remaining`` is
        everything that is neither completed nor cancelled; ``overdue`` is the open
        subset whose due date has passed as of ``as_of``. All of them are conditional
        aggregates over the same rows, so they cannot disagree.

        Ordered by name with ``id`` as a tiebreaker rather than by name alone. The
        id is there because the caller pages this list: names are not unique, so a
        name-only order lets two identically named projects swap places between two
        requests and a row the client already holds reappear on the next page.
        """
        unfinished = Task.status.not_in((TaskStatus.COMPLETED.value, TaskStatus.CANCELLED.value))
        result = await self.session.execute(
            select(
                Project.id,
                Project.name,
                Project.status,
                func.count(Task.id),
                func.count(Task.id).filter(Task.status == TaskStatus.COMPLETED.value),
                func.count(Task.id).filter(unfinished),
                func.count(Task.id).filter(
                    Task.status.in_(_OPEN_STATUSES),
                    Task.due_date.is_not(None),
                    Task.due_date < as_of,
                ),
                func.count(Task.id).filter(Task.status.in_(_OPEN_STATUSES)),
            )
            .select_from(Project)
            .outerjoin(Task, Task.project_id == Project.id)
            .where(Project.owner_id == owner_id)
            .group_by(Project.id, Project.name, Project.status)
            .order_by(Project.name.asc(), Project.id.asc())
        )
        return [
            (
                row[0],
                row[1],
                row[2],
                int(row[3]),
                int(row[4]),
                int(row[5]),
                int(row[6]),
                int(row[7]),
            )
            for row in result.all()
        ]

    async def project_minutes_in_range(
        self, owner_id: uuid.UUID, *, start: date, end: date
    ) -> dict[uuid.UUID, tuple[int, int, int]]:
        """``{project_id: (session_minutes, task_estimated, task_actual)}``.

        Two independent sources, because the two figures answer different questions
        and must not be conflated: *session* minutes are time demonstrably spent,
        while the task columns are what the tasks themselves accumulated. Reported
        side by side they also make a discrepancy visible instead of hiding it inside
        a single number.
        """
        session_totals = await self.work_minutes_by_project(owner_id, start=start, end=end)
        task_result = await self.session.execute(
            select(
                Task.project_id,
                func.coalesce(func.sum(Task.estimated_minutes), 0),
                func.coalesce(func.sum(Task.actual_minutes), 0),
            )
            .where(Task.owner_id == owner_id)
            .group_by(Task.project_id)
        )
        task_map = {row[0]: (int(row[1]), int(row[2])) for row in task_result.all()}
        return {
            project_id: (session_totals.get(project_id, 0), *task_map.get(project_id, (0, 0)))
            for project_id in set(session_totals) | set(task_map)
        }

    async def project_activity_counts(
        self, owner_id: uuid.UUID, *, start: date, end: date
    ) -> dict[uuid.UUID, int]:
        """``{project_id: activity_events}`` in the window, in one grouped query."""
        start_local, end_local = _day_window(start, end)
        result = await self.session.execute(
            select(ActivityLog.project_id, func.count())
            .where(
                ActivityLog.user_id == owner_id,
                ActivityLog.project_id.is_not(None),
                ActivityLog.created_at >= start_local,
                ActivityLog.created_at < end_local,
            )
            .group_by(ActivityLog.project_id)
        )
        return {row[0]: int(row[1]) for row in result.all()}

    async def project_velocity_by_week(
        self, owner_id: uuid.UUID, project_id: uuid.UUID, *, start: date, end: date
    ) -> list[tuple[date, int]]:
        """``(week_start, tasks_completed)`` for one project, in one query.

        **NEXUS's definition of velocity, in full: tasks completed per calendar week,
        counted by the week their ``completed_at`` falls in.** It is not an Agile
        story-point velocity, and no claim about team throughput is made from it —
        the ``velocity_tasks_per_week`` description in :mod:`app.schemas.analytics`
        repeats this so the limitation travels with the number.

        Bucketed in SQL with ``date_trunc`` over an already-pinned day expression,
        so the week start is a real Monday rather than whatever the session zone
        would have produced.

        One project at a time. The roll-up asks for every project on the page in
        one call instead — see :meth:`project_completion_by_week` — so this stays
        for the single-project callers and does not become an N+1.
        """
        start_local, end_local = _day_window(start, end)
        week = cast(func.date_trunc("week", local_day(Task.completed_at)), Date)
        result = await self.session.execute(
            select(week, func.count())
            .where(
                Task.owner_id == owner_id,
                Task.project_id == project_id,
                Task.completed_at.is_not(None),
                Task.completed_at >= start_local,
                Task.completed_at < end_local,
            )
            .group_by(week)
            .order_by(week.asc())
        )
        return [(row[0], int(row[1])) for row in result.all()]

    async def project_completion_by_week(
        self,
        owner_id: uuid.UUID,
        *,
        project_ids: Sequence[uuid.UUID],
        start: date,
        end: date,
    ) -> dict[uuid.UUID, list[tuple[date, int]]]:
        """``{project_id: [(week_start, tasks_completed), ...]}`` for many projects.

        The batched form of :meth:`project_velocity_by_week`, and the one the
        per-project roll-up reads. Asking per project was the ``N + 1`` that made
        a page of twenty roll-ups issue twenty more statements; one grouped query
        over the page's ids produces the same series for every project at once.

        A project with no completions in the window is **absent** from the
        mapping rather than mapped to an empty list, matching every other batched
        read here: the caller cannot tell the difference and would only have to
        handle one.
        """
        if not project_ids:
            return {}
        start_local, end_local = _day_window(start, end)
        week = cast(func.date_trunc("week", local_day(Task.completed_at)), Date)
        result = await self.session.execute(
            select(Task.project_id, week, func.count())
            .where(
                Task.owner_id == owner_id,
                Task.project_id.in_(list(project_ids)),
                Task.completed_at.is_not(None),
                Task.completed_at >= start_local,
                Task.completed_at < end_local,
            )
            .group_by(Task.project_id, week)
            .order_by(Task.project_id.asc(), week.asc())
        )
        series: dict[uuid.UUID, list[tuple[date, int]]] = {}
        for project_id, week_start, count in result.all():
            series.setdefault(project_id, []).append((week_start, int(count)))
        return series

    async def project_completion_counts_in_range(
        self,
        owner_id: uuid.UUID,
        *,
        project_ids: Sequence[uuid.UUID],
        start: date,
        end: date,
    ) -> dict[uuid.UUID, tuple[int, int]]:
        """``{project_id: (created, completed)}`` for many projects in one query.

        The historical completion rate's numerator and denominator, for **every
        named project at once**. Both halves cover the same window, so the rate
        describes that period rather than "everything ever" divided by "this week".

        **This is the batched form, and the batching is the point.** Asking per
        project is the ``4 x N`` pattern: the roll-up over ``N`` projects issued four
        statements each, because two counts per project were read twice — once for
        the numerator and once for the denominator — so 60 projects cost 240 round
        trips on one page load, and every routine risk evaluation paid it again
        through :meth:`AnalyticsService.project_analytics`. Two conditional
        aggregates over one ``GROUP BY project_id`` produce the same numbers for
        every project at once, and the statement count stops depending on how many
        projects the caller owns.

        ``project_ids`` narrows the scan to the page being rendered, so a paged
        roll-up does not count completions for projects it is about to leave out.
        An empty sequence returns an empty mapping without touching the database:
        there is nothing to count, and an ``IN ()`` would be a round trip spent to
        learn that.
        """
        if not project_ids:
            return {}
        start_local, end_local = _day_window(start, end)
        result = await self.session.execute(
            select(
                Task.project_id,
                func.count().filter(Task.created_at >= start_local, Task.created_at < end_local),
                func.count().filter(
                    Task.completed_at.is_not(None),
                    Task.completed_at >= start_local,
                    Task.completed_at < end_local,
                ),
            )
            .where(Task.owner_id == owner_id, Task.project_id.in_(list(project_ids)))
            .group_by(Task.project_id)
        )
        return {row[0]: (int(row[1]), int(row[2])) for row in result.all()}

    # ------------------------------------------------------------------
    # Availability, workload capacity, ML features
    # ------------------------------------------------------------------

    async def availability_windows(self, owner_id: uuid.UUID) -> list[tuple[int, time, time]]:
        """``(weekday, start, end)`` for the user's recurring free windows.

        Read here rather than from :class:`AvailabilityRuleRepository` because the
        only analytics consumer needs availability *as minutes*, and that repository
        returns rules as rows shaped for the planner's own response. Bounded by the
        table's unique constraint to 24 x 7 = 168 rows, which is why it needs no
        range.

        Naive ``time`` values are returned on purpose: "09:00 to 17:00" is a wall-clock
        reading with no offset, and attaching an instant here would freeze the window
        at whatever offset happened to be in effect.
        """
        result = await self.session.execute(
            select(
                AvailabilityRule.weekday,
                AvailabilityRule.starts_at,
                AvailabilityRule.ends_at,
            )
            .where(AvailabilityRule.owner_id == owner_id)
            .order_by(AvailabilityRule.weekday.asc(), AvailabilityRule.starts_at.asc())
        )
        return [(int(row[0]), row[1], row[2]) for row in result.all()]

    async def task_event_count(
        self, owner_id: uuid.UUID, task_id: uuid.UUID, event_type: str
    ) -> int:
        """How many times this one task recorded ``event_type``.

        The ``reschedule_count`` feature. Scoped by owner as well as by task, so the
        count cannot be obtained for another user's task even if an id leaks.
        """
        result = await self.session.execute(
            select(func.count())
            .select_from(ActivityLog)
            .where(
                ActivityLog.user_id == owner_id,
                ActivityLog.task_id == task_id,
                ActivityLog.event_type == event_type,
            )
        )
        return int(result.scalar_one())

    async def task_session_totals(self, owner_id: uuid.UUID, task_id: uuid.UUID) -> tuple[int, int]:
        """``(session_count, actual_minutes)`` for one task, in one query.

        Cancelled sessions are excluded, as everywhere else, so a task's tracked
        minutes here match the minutes its sessions actually contributed.
        """
        result = await self.session.execute(
            select(
                func.count(),
                func.coalesce(func.sum(WorkSession.actual_minutes), 0),
            ).where(
                WorkSession.owner_id == owner_id,
                WorkSession.task_id == task_id,
                WorkSession.status.not_in((WorkSessionStatus.CANCELLED.value,)),
            )
        )
        row = result.one()
        return int(row[0]), int(row[1])

    async def recent_work_minutes(self, owner_id: uuid.UUID, *, days: int = 30) -> int:
        """Minutes actually worked in the ``days`` local days ending *now*.

        "Now" is the database clock: the predicate is ``func.now() - interval`` and
        nothing is computed on the application host, so the window is the server's
        answer and does not drift with a client whose clock is wrong. A rolling
        ``interval`` rather than a set of day boundaries, so it is deliberately
        *not* cut with :func:`local_midnight` — it is the same length either way
        and lands on the same instants, but it costs no expression to write.
        ``days`` is clamped to at least 1 — zero would be an empty window and a
        negative one a range whose ends are swapped.
        """
        span = max(int(days), 1)
        statement = select(func.coalesce(func.sum(WorkSession.actual_minutes), 0)).where(
            WorkSession.owner_id == owner_id,
            WorkSession.actual_start.is_not(None),
            WorkSession.actual_start >= func.now() - func.make_interval(0, 0, 0, span),
            WorkSession.status.not_in((WorkSessionStatus.CANCELLED.value,)),
        )
        return int((await self.session.execute(statement)).scalar_one())

    async def task_created_day(self, owner_id: uuid.UUID, task_id: uuid.UUID) -> date | None:
        """The calendar day a task was created on, owner-scoped.

        A ``date`` and not an instant, cut by :func:`local_day`: the feature
        snapshot needs the task's age, and an age is a subtraction between two
        calendar days. Returning the instant only to have the caller reduce it
        again is how ``task_age_days`` came to be measured from
        ``created_at.astimezone(UTC).date()`` against a *local* ``today`` — a day
        bucket and an age that disagreed for five and a half hours every day.
        Bucketed here, the two are the same calendar by construction.

        An age measured from a row the caller may not see would be a cross-tenant
        leak, so this stays owner-scoped: it returns ``None`` for another user's
        task exactly as for one that does not exist.
        """
        result = await self.session.execute(
            select(local_day(Task.created_at)).where(Task.owner_id == owner_id, Task.id == task_id)
        )
        return result.scalar_one_or_none()

    async def task_first_work_instant(
        self, owner_id: uuid.UUID, task_id: uuid.UUID
    ) -> datetime | None:
        """When work first started on one task, or ``None``.

        ``time_of_day`` in the feature snapshot is derived from this rather than from
        ``created_at``, because "when was this picked up" is the moment the behaviour
        is actually about. When no session ever ran the answer is ``None`` and the
        snapshot reports ``None`` rather than guessing from the creation time.

        Returned as an instant on purpose: an hour-of-day is not a *day*, so there
        is no day bucket to fold it into. The zone it is read in is the caller's to
        choose, and the caller that chose UTC was reporting an evening's work as
        the early afternoon — see :meth:`as_local_time`.
        """
        result = await self.session.execute(
            select(func.min(WorkSession.actual_start)).where(
                WorkSession.owner_id == owner_id,
                WorkSession.task_id == task_id,
                WorkSession.actual_start.is_not(None),
            )
        )
        return result.scalar_one_or_none()

    # ------------------------------------------------------------------
    # CSV export sources
    # ------------------------------------------------------------------

    async def task_performance_rows(
        self, owner_id: uuid.UUID, *, start: date, end: date
    ) -> list[tuple[Any, ...]]:
        """Flat ``task_performance`` rows for the export, in one query.

        One statement over ``tasks LEFT JOIN projects`` because an export is a single
        read: a loop of per-task lookups to fetch the same project name repeatedly is
        the N+1 this method exists to avoid. The title is selected but never the
        description or content — an export is a file the user keeps, and nothing
        private is copied into it by default.
        """
        start_local, end_local = _day_window(start, end)
        result = await self.session.execute(
            select(
                Task.id,
                Project.name,
                Task.title,
                Task.status,
                Task.priority,
                Task.estimated_minutes,
                Task.actual_minutes,
                Task.due_date,
                Task.created_at,
                Task.completed_at,
            )
            .select_from(Task)
            .outerjoin(Project, Project.id == Task.project_id)
            .where(
                Task.owner_id == owner_id,
                Task.created_at >= start_local,
                Task.created_at < end_local,
            )
            .order_by(Task.created_at.asc(), Task.id.asc())
        )
        return [tuple(row) for row in result.all()]

    async def work_session_rows(
        self, owner_id: uuid.UUID, *, start: date, end: date, basis: str = "scheduled"
    ) -> list[tuple[Any, ...]]:
        """Flat ``work_sessions`` rows for a window, in one query.

        ``basis`` says **which column places a row in the window** — the only thing
        that distinguishes this read from itself one line apart.

        ``"scheduled"`` (the default) selects by ``scheduled_start``: the session
        was booked for that day. That is the right question for the CSV export,
        which is a record of what the user planned, and it is why a session booked
        and never run still belongs in the file.

        ``"actual"`` selects by ``actual_start``: the work happened on that day.
        That is the basis every *measure* of tracked time in the product uses —
        :meth:`session_minutes_by_day`, :meth:`session_summary_in_range`,
        :meth:`work_minutes_by_project` — and this read used ``scheduled_start``
        unconditionally, so ``/analytics/time`` filed a session run at 02:20 under
        the day it had been *booked* for. The same window then reported two
        different work-time totals on one page: the Overview card read the
        actual-basis aggregate, the Time tab read this one. A user asked "where
        did my time go on Tuesday" and the answer changed with the tab.

        Cancelled sessions are **kept** here, unlike every aggregate above, because
        an export is a record of what the user did rather than a measure of effort:
        a session that was booked and then cancelled is exactly the kind of row a
        user wants to see when checking their own export. A caller reading on the
        ``"actual"`` basis filters them itself — it has to filter
        ``actual_start IS NULL`` anyway — so cancelled work contributes no minutes
        to a "where did my time go" figure.
        """
        if basis not in ("scheduled", "actual"):
            raise ValueError(
                f"Cannot select work sessions by {basis!r}; "
                "work_session_rows accepts only 'scheduled' and 'actual'."
            )
        column = WorkSession.scheduled_start if basis == "scheduled" else WorkSession.actual_start
        start_local, end_local = _day_window(start, end)
        result = await self.session.execute(
            select(
                WorkSession.id,
                WorkSession.task_id,
                WorkSession.project_id,
                WorkSession.scheduled_start,
                WorkSession.scheduled_end,
                WorkSession.actual_start,
                WorkSession.actual_end,
                WorkSession.estimated_minutes,
                WorkSession.actual_minutes,
                WorkSession.status,
            )
            .where(
                WorkSession.owner_id == owner_id,
                column >= start_local,
                column < end_local,
            )
            .order_by(column.asc(), WorkSession.id.asc())
        )
        return [tuple(row) for row in result.all()]

    async def daily_metric_rows(
        self, owner_id: uuid.UUID, *, start: date, end: date
    ) -> list[tuple[Any, ...]]:
        """Flat ``daily_metrics`` rows for the export, in one query.

        The aggregate table read raw rather than through :meth:`list_range`, so the
        exported CSV has one row per day regardless of the granularity the caller was
        browsing at.
        """
        result = await self.session.execute(
            select(DailyMetric.metric_date, *METRIC_COLUMNS.values(), DailyMetric.updated_at)
            .where(
                DailyMetric.user_id == owner_id,
                DailyMetric.metric_date >= start,
                DailyMetric.metric_date <= end,
            )
            .order_by(DailyMetric.metric_date.asc())
        )
        return [tuple(row) for row in result.all()]
