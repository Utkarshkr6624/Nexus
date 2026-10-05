"""Phase 6 derived analytics: the daily aggregate tier.

One table, one row per user per day, and every number in it is a **count of
rows that exist in the tables above it**. Nothing here is estimated, guessed or
sampled; ``AnalyticsService.rebuild_range`` recomputes a day from the underlying
rows and writes the result.

Why ``daily_metrics`` is the *only* aggregate table
----------------------------------------------------
The brief offers ``weekly_metrics``, ``monthly_metrics``, ``project_metrics`` and
``task_metrics`` as possibilities and then says, explicitly, *do not create
redundant tables for every possible metric*. That is a decision, not an omission,
and it is worth stating what was given up:

* **Weekly and monthly are sums, not new facts.** A week's "tasks completed" is
  the sum of the seven ``daily_metrics.tasks_completed`` values. Materialising it
  buys a narrower index scan and costs a second thing that can disagree with the
  first; :meth:`app.services.analytics.service.AnalyticsService.daily_series`
  buckets in Python instead, and the API still answers "per week" in one query.
* **Per-project and per-task figures are not day-bucketed at all.** They are
  grouped from ``tasks``/``work_sessions`` directly in the same read, because a
  project has no daily shape to pre-aggregate — the interesting axis is the
  project, and this table has no column for it.
* **A snapshot table would multiply.** A ``productivity_snapshots`` row per task
  per day is exactly the "thousands of redundant snapshots" the brief warns
  against. Phase 10 will call :meth:`feature_snapshot` to build its training set
  on demand, so no stored copy is needed to prepare it.

What ``daily_metrics`` genuinely buys is that a dashboard reads one small,
indexed, one-row-per-day table instead of re-scanning ``activity_events``,
``work_sessions``, ``tasks`` and ``calendar_events`` on every render.

The uniqueness constraint is the idempotency anchor
--------------------------------------------------
``(user_id, metric_date)`` is unique, and :meth:`rebuild_range` upserts on it.
Recomputing a day therefore *replaces* it: replaying a rebuild over the same
range with the same inputs writes the same rows and leaves no duplicates. That is
what makes the operation safe to run from a worker that may retry, and it is why
this table needs no "first seen / last seen" bookkeeping.

``metric_date`` is the **database's** day
---------------------------------------
The column is a bare ``Date`` with no zone. It is cut at the **database server's**
local midnight, written into every read as
``date(column AT TIME ZONE current_setting('TimeZone'))`` and into every window
bound as ``CAST(:day AS TIMESTAMP) AT TIME ZONE current_setting('TimeZone')`` —
see :mod:`app.repositories.analytics`. Naming the zone in the SQL rather than
leaving it to the connection is what makes the answer a property of the query, and
naming *that* zone is what makes it the day the user experienced: on a ``+05:30``
server an evening's work belongs to that evening, not to the UTC day already
tomorrow.

It used to be cut at UTC midnight. That was a defensible choice when every
instant in the schema was UTC and nothing else in the system had an opinion, and
it stopped being one the moment :meth:`app.repositories.analytics.
AnalyticsRepository.today` began resolving "today" from the server's clock: for
five and a half hours a day the router resolved the dashboard's window as
*today* while this module filed the rows under *yesterday*.

The planner's local-day views are still a separate cut, and deliberately so: they
take an explicit ``tz``. An analytics day does not, because no analytics route
accepts one — a metric whose day boundary moved with a query parameter could not
be compared against the previous period it is being compared to.
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Date, ForeignKey, Index, Integer, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

__all__ = ["DailyMetric"]


class DailyMetric(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One user's activity for one calendar day, as counts and minutes.

    Every column is ``NOT NULL`` with a zero default, so a day with no activity
    is a row of zeroes rather than a missing row. That distinction is the whole
    point of the table: "nothing happened on Tuesday" and "Tuesday has not been
    aggregated yet" are different states, and a chart that renders them the same
    way is claiming a completeness it does not have. The service distinguishes
    them via :attr:`updated_at`.

    ``updated_at`` is therefore load-bearing rather than incidental — it is how
    the API answers "is this number stale?" — which is why this model uses
    :class:`~app.db.base.TimestampMixin` even though a row is never edited
    except by the upsert that recomputes it.
    """

    __tablename__ = "daily_metrics"

    __table_args__ = (
        # The idempotency anchor. See the module docstring: recomputing a day
        # must replace it, or a replayed rebuild double-counts the month.
        UniqueConstraint("user_id", "metric_date", name="uq_daily_metrics_owner_date"),
        # The unique constraint already indexes `(user_id, metric_date)` as a
        # btree and would serve every read this table has. This narrower index
        # exists for the same reason as `ix_availability_rules_owner_id`: it is
        # what PostgreSQL picks for the plain "all my metrics" probe, and it
        # costs one index entry per row on a table that is bounded by
        # (days x users) rather than growing with the work.
        Index("ix_daily_metrics_user_id", "user_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    metric_date: Mapped[date] = mapped_column(Date, nullable=False)

    # -- Task counts --------------------------------------------------------
    tasks_created: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    tasks_completed: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    #: Tasks past their ``due_date`` and unfinished on that day. A point-in-time
    #: count, not "became overdue today" — see the service, which buckets it per
    #: day from ``due_date``/``completed_at``.
    tasks_overdue: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    #: Tasks sitting in ``cancelled`` whose ``updated_at`` falls on this day.
    #: There is no ``cancelled_at`` column anywhere in Phase 3, so this is the
    #: only cancellation evidence the schema records; the service says so in
    #: its docstring rather than presenting it as a transition date.
    tasks_cancelled: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    #: ``TASK_BLOCKED`` events recorded that day, from ``activity_events``.
    tasks_blocked: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    #: ``TASK_RESCHEDULED`` events recorded that day.
    tasks_rescheduled: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)

    # -- Minutes ------------------------------------------------------------
    #: Minutes *committed* by work sessions whose scheduled start is on this day.
    planned_minutes: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    #: Minutes *spent* by work sessions whose actual start is on this day.
    actual_minutes: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)

    # -- Event counts -------------------------------------------------------
    work_sessions: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    calendar_events: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    #: Phase 5 knowledge events (notes, concepts, resources, bookmarks, links).
    knowledge_events: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    projects_touched: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<DailyMetric user={self.user_id} date={self.metric_date}>"
