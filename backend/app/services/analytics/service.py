"""The Analytics & Intelligence data engine: aggregates in, honest numbers out.

The one rule this module exists to keep
---------------------------------------
**Every number here is computed from rows that exist.** There is no LLM, no
sampled estimate, no "typical value" and no default standing in for missing
data. Where the underlying rows cannot support a figure the response carries
``available=False`` and a ``reason_if_unavailable`` — it never emits ``0``. A user
with two completed tasks is told "Not enough activity yet", never that their
deadline adherence is 0%: the first is true, and the second is an invented number
that a model trained on this data would later read as a real observation.

Two tiers, one direction of travel
----------------------------------
:meth:`AnalyticsService.rebuild_range` is the only writer. It folds the source
rows into one ``daily_metrics`` row per day and upserts on
``(user_id, metric_date)``. Every read below then reads *those rows* rather than
re-scanning events per request, which is what makes a dashboard cheap. Reads go
back to the underlying tables only for the figures that are point-in-time by
nature — the current backlog, the current deadline exposure — which an aggregate
dated in the past genuinely cannot answer.

``POST /analytics/rebuild`` is a **maintenance action, not a user action**, and
it records nothing: no ``activity_events`` row, no ``audit_logs`` row.
Recomputing somebody's dashboard from their own data is not something the user
did, and a feed full of "analytics recomputed" entries would bury the work events
the feed exists for. ``activity`` and ``audit`` are accepted for signature
symmetry and used by neither.

**Rebuild is idempotent and bounded.** The upsert key is
``(user_id, metric_date)`` and every column is recomputed from scratch, so a
second run overwrites rather than appends. Each repository query buckets the
*whole range* in one statement, so the round-trip count does not grow with the
length of the range.

Where ``formula`` appears
------------------------
``ProductivityRead`` carries a ``formula`` string the UI renders verbatim, and it
names the four weights *and* what they were applied to. The sub-scores carry the
same information where a reader actually meets it: every component row carries an
``explanation`` of what was measured and, where a component was not counted, why.

Two things Phase 1-5 cannot record, said out loud
-------------------------------------------------
* **A cancellation has no date.** ``tasks`` carries ``status = 'cancelled'`` but no
  ``cancelled_at``, so a cancellation is dated by ``updated_at`` — the last edit,
  which for a cancelled task is usually the cancellation and occasionally a later
  touch. It is a real column read a real way, and the only one there is.
* **"Overdue" means "was due that day and was not finished by the end of it"**,
  not "was past due on that day". The repository states the definition and this
  module inherits it deliberately: a task sitting overdue today did not become
  overdue again every day since, so the daily series sums back into the open
  backlog instead of growing without bound. "How much is overdue *right now*" is a
  different and separately-answered question — it is
  :meth:`AnalyticsService.task_analytics`'s ``overdue_tasks``, read against the
  database clock.
* **Learning is a conjunction, not a flag, and knowledge events carry no project.**
  No Phase 1-5 table marks anything as "learning", and ``KnowledgeService``
  records its events with no ``task_id`` and no ``project_id`` — so "tasks
  completed in projects the user was also writing about" is **not computable**.
  :meth:`AnalyticsService.learning` reports the figure as ``null`` and says so in its
  ``basis`` string, rather than substituting a weaker correlation that would read
  as the same number.

Feature extraction and why ``None`` is not optional
--------------------------------------------------
:meth:`AnalyticsService.feature_snapshot` is the ML hook Phase 10 trains on. It
returns ``None`` for a feature it cannot compute and **never a fabricated
zero**. That is not tidiness: inside a training matrix a zero is a *signal* —
"no deadline pressure", "no open work in this project" — and a fabricated one is
indistinguishable from a real observation once it is in there. ``None`` is the
absence of an observation, which an imputer can handle deliberately; ``0`` is a
claim.
"""

from __future__ import annotations

import csv
import io
import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from time import perf_counter
from typing import Any

from sqlalchemy import func, select

from app.core.config import Settings, get_settings
from app.core.exceptions import NotFoundError, ValidationError
from app.core.logging import get_logger, log_event
from app.models.analytics import DailyMetric
from app.models.enums import ActivityEvent, TaskPriority
from app.models.task import Task
from app.models.user import User
from app.repositories.analytics import METRIC_COLUMNS, AnalyticsRepository, local_midnight
from app.repositories.knowledge import NoteRepository
from app.repositories.planner import (
    AvailabilityRuleRepository,
    CalendarEventRepository,
    WorkSessionRepository,
)
from app.repositories.project import ProjectRepository
from app.repositories.task import TaskRepository
from app.schemas.analytics import (
    ANALYTICS_FEATURE_SCHEMA_VERSION,
    ComparisonPoint,
    ConsistencyRead,
    CsvExportManifestRead,
    CsvExportRead,
    DailyMetricRead,
    DeadlineAdherenceRead,
    EstimationAccuracyRead,
    FocusRead,
    KnowledgeAnalyticsRead,
    LearningAnalyticsRead,
    MetricRange,
    OverdueTaskRead,
    OverviewRead,
    ProductivityRead,
    ProjectAnalyticsRead,
    ScoreComponentRead,
    TagCountRead,
    TaskAnalyticsRead,
    TimeBucketRead,
    TimeDistributionRead,
    TrendPoint,
    VelocityRead,
    WorkloadRead,
)
from app.schemas.common import Page, PageMeta
from app.services.activity_service import ActivityService
from app.services.analytics.scoring import (
    NOT_ENOUGH_ACTIVITY,
    ScoreResult,
    absolute_change,
    consistency_score,
    deadline_adherence,
    estimation_accuracy,
    focus_score,
    percent_change,
    productivity_score,
    rate,
)
from app.services.audit_service import AuditService

__all__ = ["CSV_DATASETS", "GRANULARITIES", "TRENDABLE_METRICS", "AnalyticsService"]

logger = get_logger(__name__)

#: Granularities the series and trend endpoints accept, matching the repository's.
GRANULARITIES = ("day", "week", "month")

#: The daily columns, in the order the repository stores and exports them.
#: Re-exported from the repository rather than re-typed here so the CSV header and
#: the aggregate can never drift apart.
TRENDABLE_METRICS: tuple[str, ...] = tuple(METRIC_COLUMNS)

#: The CSV datasets and the columns each carries. The column list is the contract
#: an importer is written against, so it is stated once and used by both the file
#: and :meth:`AnalyticsService.export_manifest`.
CSV_DATASETS: dict[str, tuple[str, ...]] = {
    "daily_metrics": ("metric_date", *TRENDABLE_METRICS, "updated_at"),
    "task_performance": (
        "task_id",
        "project_name",
        "title",
        "status",
        "priority",
        "estimated_minutes",
        "actual_minutes",
        "due_date",
        "created_at",
        "completed_at",
        "estimate_error_minutes",
    ),
    "work_sessions": (
        "session_id",
        "task_id",
        "project_id",
        "scheduled_start",
        "scheduled_end",
        "actual_start",
        "actual_end",
        "estimated_minutes",
        "actual_minutes",
        "status",
    ),
}

#: How many per-task rows the time distribution returns. The list is a "where did
#: my time go" drill-down, not an export; ``/export.csv`` is the complete answer.
MAX_TIME_BUCKETS = 20

#: How many rows the overdue drill-down carries. A drill-down, not an export.
MAX_OVERDUE_ROWS = 20

#: How many projects the paginated roll-up serves when the caller names none. A
#: project list is a picker a person scans, and the ceiling a route may ask for is
#: the route's own business — this is only the fallback for a direct caller.
_DEFAULT_PROJECT_PAGE_SIZE = 20

#: The most project rows a read will page through to turn project ids into
#: project names. A bound rather than a single page: the previous read asked for
#: 100 rows and no more, so an account with more than 100 projects had the rest
#: labelled "Unassigned" — which is a claim about where the work was *recorded*,
#: and every one of those projects is recorded against a real project row. The
#: bound exists only to keep an unbounded owner from paging forever; past it the
#: label degrades to the project's own id, which is ugly but not false.
PROJECT_NAME_PAGE_SIZE = 100
PROJECT_NAME_LOOKUP_CEILING = 1000

_TASK_NOT_FOUND = "That task does not exist."
_PROJECT_NOT_FOUND = "That project does not exist."

#: ``TaskPriority`` as an ordinal. The feature keys are numbers so a model can
#: read them as one — ``schema_version`` is the single exception, and it is a
#: contract string rather than a column — and this is the only mapping that does
#: not invent an ordering the enum does not have.
_PRIORITY_RANK = {
    TaskPriority.LOW: 1,
    TaskPriority.MEDIUM: 2,
    TaskPriority.HIGH: 3,
    TaskPriority.CRITICAL: 4,
}

_OPEN_STATUSES = ("todo", "in_progress", "blocked")


def _previous_window(start: date, end: date) -> tuple[date, date]:
    """The equal-length window immediately before ``[start, end]``."""
    days = (end - start).days + 1
    return start - timedelta(days=days), start - timedelta(days=1)


def _days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def _bucket(day: date, granularity: str) -> date:
    """The first day of the bucket ``day`` belongs to."""
    if granularity == "day":
        return day
    if granularity == "week":
        return day - timedelta(days=day.weekday())
    if granularity == "month":
        return day.replace(day=1)
    raise ValidationError(
        f"Unsupported granularity {granularity!r}; expected one of {', '.join(GRANULARITIES)}."
    )


def _bucket_end(start: date, granularity: str) -> date:
    """The last day of the bucket beginning at ``start``."""
    if granularity == "day":
        return start
    if granularity == "week":
        return start + timedelta(days=6)
    return (start + timedelta(days=32)).replace(day=1) - timedelta(days=1)


def _bucket_label(start: date, granularity: str) -> str:
    """A stable, human-readable label for a bucket."""
    if granularity == "day":
        return start.isoformat()
    if granularity == "week":
        return f"{start.isoformat()}/W{start.isocalendar().week:02d}"
    return start.strftime("%Y-%m")


def _iso(value: datetime | date | None) -> str:
    """An ISO-8601 CSV cell, empty for an absent value."""
    return value.isoformat() if value is not None else ""


def _cell(value: int | None) -> int | str:
    """A CSV cell that is empty — not ``0`` — for a value nobody recorded."""
    return "" if value is None else value


#: Characters that make a spreadsheet treat a cell as a formula rather than as
#: text. Excel, LibreOffice and Sheets all evaluate a cell whose first character
#: is one of these.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value: str | None) -> str:
    """Neutralise a leading formula character in user-authored text.

    An export is the one analytics surface that leaves the database as a file the
    user then double-clicks. A task titled ``=1+1`` or ``@SUM(A1:A9)`` is written
    verbatim into the cell, and when the file is opened those cells are evaluated
    by the spreadsheet — turning a row of someone's task titles into live
    formulas in the user's session. Prefixing with an apostrophe forces text and
    leaves the visible value unchanged, which is the conventional mitigation.

    Only *leading* characters are affected. A task legitimately named ``C++`` or
    ``A-B testing`` is untouched, and a title that merely contains ``=`` in the
    middle is not a formula to any spreadsheet and is not rewritten.
    """
    if not value:
        return ""
    if value.startswith(_FORMULA_PREFIXES):
        return f"'{value}"
    return value


def _components(components: Sequence[Any]) -> list[ScoreComponentRead]:
    """Map scoring's frozen dataclasses onto the wire schema.

    Explicit rather than a pydantic coercion, so a rename in either module is a
    name error here instead of a silently empty breakdown inside a response that
    still returns 200.
    """
    return [
        ScoreComponentRead(
            name=component.name,
            points=float(component.points),
            max_points=float(component.max_points),
            explanation=component.explanation,
        )
        for component in components
    ]


def _estimation_read(result: Any, start: date, end: date) -> EstimationAccuracyRead:
    """Scoring's :class:`EstimationResult` as the wire schema."""
    return EstimationAccuracyRead(
        available=result.available,
        reason_if_unavailable=result.reason_if_unavailable,
        sample_count=result.sample_count,
        pairs_compared=result.sample_count,
        absolute_error=result.absolute_error,
        mean_absolute_error=result.absolute_error,
        percentage_error=result.percentage_error,
        mean_percentage_error=result.percentage_error,
        bias=result.bias,
        median_error=result.median_error,
        under_estimation_rate=result.under_estimation_rate,
        over_estimation_rate=result.over_estimation_rate,
        underestimation_rate=result.under_estimation_rate,
        overestimation_rate=result.over_estimation_rate,
        range=MetricRange(start_date=start, end_date=end),
    )


def _minutes_between(start: time, end: time) -> int:
    """Minutes from ``start`` to ``end`` on the same wall-clock day."""
    return max(0, (end.hour * 60 + end.minute) - (start.hour * 60 + start.minute))


def _priority_rank(value: str) -> int | None:
    """The ordinal for a stored priority, or ``None`` for a drifted value.

    ``None`` rather than a guess: a drifted ``priority`` is a data problem, and
    mapping it to "medium" would hand the model a confident number about a row
    nobody can read.
    """
    try:
        return _PRIORITY_RANK.get(TaskPriority(value))
    except ValueError:
        return None


class AnalyticsService:
    """Aggregation, scoring and export for one user's work.

    Read methods share a shape: fetch the daily aggregates for the window, fetch
    the previous equal-length window for comparison, hand plain numbers to the
    pure scoring functions, and assemble a response carrying the score, its
    components and its ``available`` flag. Nothing here reads a clock of its own
    except the one feature-extraction path, which asks the database.
    """

    def __init__(
        self,
        metrics: AnalyticsRepository,
        tasks: TaskRepository,
        projects: ProjectRepository,
        sessions: WorkSessionRepository,
        events: CalendarEventRepository,
        notes: NoteRepository,
        activity: ActivityService | None = None,
        audit: AuditService | None = None,
        settings: Settings | None = None,
        availability: AvailabilityRuleRepository | None = None,
    ) -> None:
        """Wire the service.

        Args:
            metrics: ``daily_metrics`` persistence and every grouped read-side
                aggregate. The rebuild tier and most read figures come from here.
            tasks: Task persistence. Used for the owner-scoped single-row lookups,
                which is where the 404-not-403 rule lives.
            projects: Project persistence, same purpose.
            sessions: Work-session persistence.
            events: Calendar-event persistence.
            notes: Note persistence.
            activity: Accepted for signature symmetry and deliberately **unused**;
                see the module docstring on why a rebuild records nothing.
            audit: Accepted and deliberately **unused**, on the reasoning the other
                services give: ``audit_logs`` is the security trail, and recomputing
                somebody's dashboard is not a security event.
            settings: Resolved from the environment when not supplied.
            availability: The user's weekly free-time pattern, for the one
                workload figure that cannot be derived from recorded work.
        """
        self.metrics = metrics
        self.tasks = tasks
        self.projects = projects
        self.sessions = sessions
        self.events = events
        self.notes = notes
        self.activity = activity
        self.audit = audit
        self.settings = settings or get_settings()
        self.availability = availability

    # -- Aggregation tier ----------------------------------------------------

    async def rebuild_range(self, *, owner: User, start: date, end: date) -> int:
        """Recompute ``daily_metrics`` for every day in ``[start, end]``.

        One grouped query per source — task creations, completions,
        cancellations, overdue, blocked, rescheduled, knowledge events, planned
        minutes, actual minutes, calendar entries, projects touched — plus one
        upsert. Every query buckets the **whole range** in a single statement, so
        the round-trip count does not grow with the length of the range: a 30-day
        rebuild and a 1-day rebuild issue the same twelve statements. That is the
        point of the tier, and the alternative — a loop of per-day queries — is
        exactly the 30 x N round trip this design removes.

        A day with no activity is written as a row of zeros rather than omitted,
        because "nothing happened on Tuesday" is a real observation and a series
        with a hole in it is one the client has to re-fill. It is a zero in the
        *aggregate*; it never becomes a zero in a *score*, because availability is
        decided from what was recorded, not from the presence of this row.

        **The pass is observable.** ``analytics_rebuild_started`` and
        ``analytics_rebuild_completed`` bracket every run with the window and the
        row count, and ``analytics_rebuild_failed`` carries the traceback if a
        source query or the upsert raises. A rebuild is the one write path in the
        dashboard and it produces no activity rows of its own, so without these
        three lines there is nothing in the log distinguishing a window that was
        recomputed from one that was never touched.

        Args:
            owner: The user whose days are rebuilt. Scoped in every statement.
            start: Inclusive first day.
            end: Inclusive last day.

        Returns:
            The number of daily rows written — one per day in the range, whether
            or not that day had any activity.

        Raises:
            ValidationError: If the range is inverted, or wider than the ceiling
                ``Settings.analytics_rebuild_max_days`` sets for this write path.
        """
        self._check_range(start, end, ceiling=self.settings.analytics_rebuild_max_days)
        log_event(
            logger,
            logging.INFO,
            "analytics_rebuild_started",
            owner_id=str(owner.id),
            start=start.isoformat(),
            end=end.isoformat(),
            days=(end - start).days + 1,
        )
        started = perf_counter()
        try:
            written = await self._write_range(owner=owner, start=start, end=end)
        except Exception:
            # The owner id, the window and the exception *type* — never the rows
            # being recomputed, which are one user's working day and must not be
            # copied into a log. The traceback carries the statement that failed.
            log_event(
                logger,
                logging.ERROR,
                "analytics_rebuild_failed",
                exc_info=True,
                owner_id=str(owner.id),
                start=start.isoformat(),
                end=end.isoformat(),
                elapsed_ms=round((perf_counter() - started) * 1000.0, 3),
            )
            raise
        log_event(
            logger,
            logging.INFO,
            "analytics_rebuild_completed",
            owner_id=str(owner.id),
            start=start.isoformat(),
            end=end.isoformat(),
            rows_written=written,
            elapsed_ms=round((perf_counter() - started) * 1000.0, 3),
        )
        return written

    async def _write_range(self, *, owner: User, start: date, end: date) -> int:
        """Run every grouped source query for the range and upsert the result.

        Split from :meth:`rebuild_range` so that the start/finish/failure lines
        wrap exactly the work and nothing else. :meth:`AnalyticsService._check_range`
        stays outside it, because an inverted or over-wide range is a caller's
        422 and not a rebuild that failed.

        Args:
            owner: The user whose days are rebuilt. Scoped in every statement.
            start: Inclusive first day.
            end: Inclusive last day.

        Returns:
            The number of daily rows written.
        """
        owner_id = owner.id

        created = _by_day(await self.metrics.count_tasks_by_day(owner_id, start=start, end=end))
        completed = _by_day(
            await self.metrics.count_tasks_completed_by_day(owner_id, start=start, end=end)
        )
        cancelled = _by_day(
            await self.metrics.count_tasks_cancelled_by_day(owner_id, start=start, end=end)
        )
        overdue = _by_day(
            await self.metrics.count_tasks_overdue_by_day(owner_id, start=start, end=end)
        )
        blocked = _by_day(
            await self.metrics.event_counts_by_day(
                owner_id, start=start, end=end, event_types=[ActivityEvent.TASK_BLOCKED.value]
            )
        )
        rescheduled = _by_day(
            await self.metrics.event_counts_by_day(
                owner_id,
                start=start,
                end=end,
                event_types=[ActivityEvent.TASK_RESCHEDULED.value],
            )
        )
        knowledge = _by_day(
            await self.metrics.knowledge_events_by_day(owner_id, start=start, end=end)
        )
        planned = _by_day(await self.metrics.planned_minutes_by_day(owner_id, start=start, end=end))
        actual = {
            day: (minutes, _count)
            for day, minutes, _count in await self.metrics.session_minutes_by_day(
                owner_id, start=start, end=end
            )
        }
        events = _by_day(await self.metrics.calendar_events_by_day(owner_id, start=start, end=end))
        touched = _by_day(
            await self.metrics.projects_touched_by_day(owner_id, start=start, end=end)
        )

        rows = [
            {
                "metric_date": day,
                "tasks_created": created.get(day, 0),
                "tasks_completed": completed.get(day, 0),
                "tasks_overdue": overdue.get(day, 0),
                "tasks_cancelled": cancelled.get(day, 0),
                "tasks_blocked": blocked.get(day, 0),
                "tasks_rescheduled": rescheduled.get(day, 0),
                "planned_minutes": planned.get(day, 0),
                "actual_minutes": actual.get(day, (0, 0))[0],
                "work_sessions": actual.get(day, (0, 0))[1],
                "calendar_events": events.get(day, 0),
                "knowledge_events": knowledge.get(day, 0),
                "projects_touched": touched.get(day, 0),
            }
            for day in _days(start, end)
        ]
        return await self.metrics.upsert_many(owner_id, rows)

    async def daily_series(
        self, *, owner: User, start: date, end: date, granularity: str = "day"
    ) -> list[DailyMetricRead]:
        """Return the stored daily aggregates, optionally rolled up in time.

        Reads ``daily_metrics`` and nothing else. A window that has never been
        rebuilt comes back empty rather than being recomputed on the fly: this is a
        *read*, and a read that re-aggregates undoes the reason the table exists.
        ``OverviewRead.stale`` is what tells a client which of the two it is
        looking at.
        """
        self._check_range(start, end)
        self._check_granularity(granularity)
        rows = await self.metrics.list_range(
            owner.id, start=start, end=end, granularity=granularity
        )
        return [self._as_daily(row) for row in rows]

    # -- Scores --------------------------------------------------------------

    async def productivity(self, *, owner: User, start: date, end: date) -> ProductivityRead:
        """The transparent 0-100 productivity score, or why there isn't one."""
        self._check_range(start, end)
        window = await self._totals(owner, start, end)
        today = await self._today()

        deadline_result, _buckets = await self._deadline_result(owner.id, start, end, today)
        activity_days = await self.metrics.activity_days_in_range(owner.id, start=start, end=end)
        sessions = await self.metrics.session_summary_in_range(owner.id, start=start, end=end)
        completion_result = _completion_result(window)
        consistency_result = consistency_score(
            active_days=len(activity_days),
            window_days=window["window_days"],
            session_count=int(sessions.get("sessions") or 0),
        )
        focus_result = focus_score(
            avg_session_minutes=(
                None if sessions.get("avg_minutes") is None else float(sessions["avg_minutes"])
            ),
            completed_planned_sessions=int(sessions.get("completed_sessions") or 0),
            interruptions=int(sessions.get("interruptions") or 0),
        )
        result = productivity_score(
            completion_rate=completion_result,
            deadline_adherence=deadline_result,
            consistency=consistency_result,
            focus=focus_result,
            weights=self.settings.analytics_productivity_weights,
        )
        return ProductivityRead(
            score=result.score,
            available=result.available,
            reason_if_unavailable=result.reason_if_unavailable,
            components=_components(result.components),
            formula=self._productivity_formula(),
            label="NEXUS Productivity Score",
            disclaimer=(
                "A transparent, reproducible blend of four things this system "
                "recorded. It is not a validated measure of human performance."
            ),
            range=self._metric_range(start, end),
            weight_total=sum(self.settings.analytics_productivity_weights.values()),
        )

    async def consistency(self, *, owner: User, start: date, end: date) -> ConsistencyRead:
        """How regularly the user showed up over the window."""
        self._check_range(start, end)
        window_days = (end - start).days + 1
        activity_days = await self.metrics.activity_days_in_range(owner.id, start=start, end=end)
        sessions = await self.metrics.session_summary_in_range(owner.id, start=start, end=end)
        result = consistency_score(
            active_days=len(activity_days),
            window_days=window_days,
            session_count=int(sessions.get("sessions") or 0),
        )
        ordered = sorted(activity_days)
        return ConsistencyRead(
            score=result.score,
            available=result.available,
            reason_if_unavailable=result.reason_if_unavailable,
            active_days=len(ordered),
            window_days=window_days,
            work_sessions=int(sessions.get("sessions") or 0),
            session_count=int(sessions.get("sessions") or 0),
            active_day_ratio=rate(len(ordered), window_days),
            longest_streak=_longest_streak(ordered),
            current_streak=_current_streak(ordered),
            components=_components(result.components),
            label="NEXUS Consistency Score",
            formula=(
                "consistency = round(100 * active_days / window_days), where an "
                "active day is one carrying at least one recorded activity event. "
                "Unavailable — not 0 — when no work session was started, because "
                "there is then no record of which days the user was present."
            ),
            disclaimer=(
                "Measures presence, not output. A day with a one-minute session "
                "counts as a day like any other."
            ),
            range=self._metric_range(start, end),
        )

    async def focus(self, *, owner: User, start: date, end: date) -> FocusRead:
        """How long, and how uninterrupted, the user's work blocks were."""
        self._check_range(start, end)
        window = await self._totals(owner, start, end)
        sessions = await self.metrics.session_summary_in_range(owner.id, start=start, end=end)
        result = focus_score(
            avg_session_minutes=(
                None if sessions.get("avg_minutes") is None else float(sessions["avg_minutes"])
            ),
            completed_planned_sessions=int(sessions.get("completed_sessions") or 0),
            interruptions=int(sessions.get("interruptions") or 0),
        )
        return FocusRead(
            score=result.score,
            available=result.available,
            reason_if_unavailable=result.reason_if_unavailable,
            avg_session_minutes=(
                None if sessions.get("avg_minutes") is None else float(sessions["avg_minutes"])
            ),
            completed_planned_sessions=int(sessions.get("completed_sessions") or 0),
            interruptions=int(sessions.get("interruptions") or 0),
            reschedules=window["tasks_rescheduled"],
            focused_minutes=int(sessions.get("completed_minutes") or 0),
            total_minutes=int(sessions.get("minutes") or 0),
            components=_components(result.components),
            label="NEXUS Focus Score",
            formula=(
                "focus = round(100 * (0.6 * min(avg_session_minutes / 45, 1) + 0.4 * "
                "completed_sessions / (completed_sessions + interruptions))). "
                "'Interruptions' are sessions that did not reach `completed` — a "
                "proxy read off the session table, not a measurement of attention."
            ),
            disclaimer=(
                "Derived from recorded work-session behaviour. It does not measure "
                "human attention or concentration."
            ),
            range=self._metric_range(start, end),
        )

    async def deadlines(self, *, owner: User, start: date, end: date) -> DeadlineAdherenceRead:
        """On-time vs late vs still-overdue, for tasks completed in the window."""
        self._check_range(start, end)
        today = await self._today()
        result, (on_time, late, still_overdue) = await self._deadline_result(
            owner.id, start, end, today
        )
        decided = on_time + late
        return DeadlineAdherenceRead(
            available=result.available,
            reason_if_unavailable=result.reason_if_unavailable,
            on_time=on_time,
            late=late,
            still_overdue=still_overdue,
            adherence_rate=None if not result.available else round(on_time / decided * 100.0, 4),
            rate=None if not result.available else round(on_time / decided * 100.0, 4),
            overdue_open=still_overdue,
            total_considered=decided + still_overdue,
            components=_components(result.components),
            range=self._metric_range(start, end),
        )

    async def estimation(self, *, owner: User, start: date, end: date) -> EstimationAccuracyRead:
        """How far estimates land from reality, and in which direction."""
        self._check_range(start, end)
        pairs = await self._estimation_pairs(owner.id, start, end)
        return _estimation_read(estimation_accuracy(pairs=pairs), start, end)

    # -- Work and time -------------------------------------------------------

    async def workload(self, *, owner: User, start: date, end: date) -> WorkloadRead:
        """What is open now, and how much of the declared free time it has claimed.

        ``available_minutes`` comes from the user's own availability rules and is
        ``None`` when they have declared none. That is unconfigured, not zero: a
        user with no working-hours rule has not said they have no working hours,
        and a workload ratio computed against an invented denominator would be a
        number about nothing.
        """
        self._check_range(start, end)
        today = await self._today()
        status = await self.metrics.status_counts(owner.id)
        priority = await self.metrics.priority_counts(owner.id)
        overdue_open = await self.metrics.overdue_count_as_of(owner.id, today)
        planned = _by_day(await self.metrics.planned_minutes_by_day(owner.id, start=start, end=end))
        actual = {
            day: minutes
            for day, minutes, _count in await self.metrics.session_minutes_by_day(
                owner.id, start=start, end=end
            )
        }
        available_minutes = await self._available_minutes(owner, start, end)

        window_days = (end - start).days + 1
        scheduled = sum(planned.values())
        open_tasks = sum(
            int(count) for status_value, count in status.items() if status_value in _OPEN_STATUSES
        )
        high_priority_open = sum(
            int(count)
            for name, count in priority.items()
            if name in ("high", "critical") and name != "total"
        )
        return WorkloadRead(
            open_tasks=open_tasks,
            high_priority_open=high_priority_open,
            overdue_open=overdue_open,
            scheduled_minutes=scheduled,
            available_minutes=available_minutes,
            workload_ratio=rate(scheduled, available_minutes) if available_minutes else None,
            average_daily_scheduled_minutes=round(scheduled / window_days, 2)
            if scheduled
            else None,
            high_priority_tasks=high_priority_open,
            overdue_tasks=overdue_open,
            actual_minutes=sum(actual.values()),
            available=bool(scheduled or actual),
            reason_if_unavailable=None
            if (scheduled or actual)
            else f"{NOT_ENOUGH_ACTIVITY}: no work sessions were planned or run in this range.",
            comparison=[
                ComparisonPoint(
                    label=day.isoformat(),
                    current=float(actual.get(day, 0)),
                    previous=float(planned.get(day, 0)),
                    absolute_change=absolute_change(actual.get(day, 0), planned.get(day, 0)),
                    percent_change=percent_change(actual.get(day, 0), planned.get(day, 0)),
                )
                for day in _days(start, end)
            ],
            status_counts=status,
            priority_counts=priority,
            range=self._metric_range(start, end),
        )

    async def time_distribution(
        self, *, owner: User, start: date, end: date, project_id: uuid.UUID | None = None
    ) -> TimeDistributionRead:
        """Where the recorded work time went, by project and by task.

        Both breakdowns come from one read of the session rows and are filled
        from the same loop, so they partition the **same minutes**: every minute
        that reaches ``total_minutes`` reaches both slices. That includes the
        unattributable ones — ``work_sessions.task_id`` is nullable, so a session
        can belong to a project without belonging to a task — which are reported
        under the reserved ``unassigned`` key in both lists rather than counted
        once and dropped from the other, the arrangement that left the per-task
        shares summing to well under 100% while ``total_minutes`` said the window
        was fully accounted for.

        Past :data:`MAX_TIME_BUCKETS` tasks the by-task list is a drill-down and
        not a partition; ``/export.csv`` is the complete answer. Per-task rows are
        labelled by task id rather than by title: the title would cost a lookup per
        task, and a drill-down list is not worth an N+1 — ``key`` carries the full
        id for a client that wants to link.
        """
        self._check_range(start, end)
        project_id = await self._owned_project(project_id, owner)
        rows = await self.metrics.work_session_rows(owner.id, start=start, end=end)

        by_project: dict[uuid.UUID | None, int] = {}
        by_task: dict[uuid.UUID | None, int] = {}
        for (
            _session_id,
            task_id,
            row_project_id,
            _scheduled_start,
            _scheduled_end,
            actual_start,
            _actual_end,
            _estimated,
            actual_minutes,
            status,
        ) in rows:
            if actual_start is None or status == "cancelled":
                continue
            if project_id is not None and row_project_id != project_id:
                continue
            minutes = int(actual_minutes)
            by_project[row_project_id] = by_project.get(row_project_id, 0) + minutes
            by_task[task_id] = by_task.get(task_id, 0) + minutes

        total = sum(by_project.values())
        names = await self._project_names(owner.id)
        return TimeDistributionRead(
            total_minutes=total,
            available=bool(by_project),
            reason_if_unavailable=None
            if by_project
            else f"{NOT_ENOUGH_ACTIVITY}: no completed work sessions were recorded in this range.",
            unassigned_minutes=by_project.get(None, 0) if None in by_project else 0,
            # Stringified for the wire — see `TimeDistributionRead.project_id`,
            # which is a report field rather than a lookup key.
            project_id=str(project_id) if project_id else None,
            by_project=[
                TimeBucketRead(
                    key=str(key) if key is not None else "unassigned",
                    label=_project_label(key, names),
                    minutes=minutes,
                    share=rate(minutes, total),
                )
                for key, minutes in sorted(
                    by_project.items(), key=lambda item: (-item[1], str(item[0]))
                )
            ],
            by_task=[
                TimeBucketRead(
                    # The reserved key and label, not a task id: a client that
                    # links a row has nothing to link a session recorded against
                    # no task to, and inventing one would be worse than saying so.
                    key=str(key) if key is not None else "unassigned",
                    label=f"Task {str(key)[:8]}" if key is not None else "Unassigned",
                    minutes=minutes,
                    share=rate(minutes, total),
                )
                for key, minutes in sorted(by_task.items(), key=lambda item: -item[1])[
                    :MAX_TIME_BUCKETS
                ]
            ],
            range=self._metric_range(start, end),
        )

    # -- Work-management analytics ------------------------------------------

    async def task_analytics(self, *, owner: User, start: date, end: date) -> TaskAnalyticsRead:
        """Creation, completion, cancellation and backlog over the window."""
        self._check_range(start, end)
        today = await self._today()
        window = await self._totals(owner, start, end)
        status = await self.metrics.status_counts(owner.id)
        priority = await self.metrics.priority_counts(owner.id)
        overdue = await self.metrics.overdue_count_as_of(owner.id, today)
        pairs = await self._estimation_pairs(owner.id, start, end)
        cycle_minutes = await self.metrics.avg_cycle_minutes_in_range(
            owner.id, start=start, end=end
        )
        estimation = estimation_accuracy(pairs=pairs)

        created = window["tasks_created"]
        completed = window["tasks_completed"]
        denominator = created or completed
        top_overdue, top_overdue_truncated = await self._top_overdue(owner.id, today)
        return TaskAnalyticsRead(
            total_tasks=int(status.get("total", 0)),
            completed_tasks=int(status.get("completed", 0)),
            open_tasks=sum(int(count) for name, count in status.items() if name in _OPEN_STATUSES),
            overdue_tasks=overdue,
            cancelled_tasks=int(status.get("cancelled", 0)),
            blocked_tasks=int(status.get("blocked", 0)),
            completion_rate=rate(completed, denominator),
            overdue_rate=rate(overdue, denominator),
            avg_completion_days=None if cycle_minutes is None else round(cycle_minutes / 1440.0, 4),
            avg_cycle_minutes=cycle_minutes,
            avg_estimate_error_minutes=estimation.absolute_error,
            tasks_created=created,
            tasks_completed=completed,
            tasks_cancelled=window["tasks_cancelled"],
            tasks_blocked=window["tasks_blocked"],
            tasks_overdue=window["tasks_overdue"],
            tasks_rescheduled=window["tasks_rescheduled"],
            available=bool(denominator or status.get("total")),
            reason_if_unavailable=None
            if (denominator or status.get("total"))
            else f"{NOT_ENOUGH_ACTIVITY}: no tasks exist for this account.",
            estimation=_estimation_read(estimation, start, end),
            top_overdue=top_overdue,
            top_overdue_truncated=top_overdue_truncated,
            by_status=status,
            by_priority=priority,
            range=self._metric_range(start, end),
        )

    async def project_analytics(
        self,
        *,
        owner: User,
        start: date,
        end: date,
        project_id: uuid.UUID | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[ProjectAnalyticsRead]:
        """Per-project roll-up, computed from the tasks themselves.

        Not stored: a ``project_metrics`` table would be a second copy of every one
        of these numbers, kept in step by a job that can be late, and reading it
        would tell the user how current their figures were only if they also knew
        when the job last ran. The migration module docstring carries the argument.

        **Every per-project figure is grouped, so the statement count does not
        depend on how many projects the account has.** The window-scoped completion
        pair used to be read per project, twice — once for the numerator and once
        for the denominator — which made this method ``4 x N + 5`` statements:
        9 at one project, 245 at sixty. It is now six whatever ``N`` is, because
        :meth:`AnalyticsRepository.project_completion_counts_in_range` returns the
        pair for every project at once. The count matters beyond this route:
        :mod:`app.services.risk.detection` reaches this method on every evaluation
        pass, so a detector run was multiplying the same N+1 by the number of
        detectors that read a project row.

        ``limit`` and ``offset`` page the roll-up; ``limit=None`` means every
        project, which is what the internal callers want and what the HTTP route
        deliberately does not offer. Use :meth:`project_analytics_page` for the
        paginated form, which is the one carrying the total.

        Args:
            owner: The account being described.
            start: First day of the window, inclusive.
            end: Last day of the window, inclusive.
            project_id: Narrow to one project. Another account's project is a 404,
                resolved through the owner-scoped lookup before any aggregate runs.
            limit: Projects to return, or ``None`` for all of them.
            offset: Projects to skip, in the repository's own ``name`` order.

        Returns:
            The roll-up rows, in project-name order.
        """
        rows, _total = await self._project_rollups(
            owner=owner, start=start, end=end, project_id=project_id, limit=limit, offset=offset
        )
        return rows

    async def project_analytics_page(
        self,
        *,
        owner: User,
        start: date,
        end: date,
        project_id: uuid.UUID | None = None,
        limit: int = _DEFAULT_PROJECT_PAGE_SIZE,
        offset: int = 0,
    ) -> Page[ProjectAnalyticsRead]:
        """One bounded page of the per-project roll-up, and the size of the whole set.

        ``meta.total`` counts every project the filters match rather than the rows in
        ``items``. Without it a client cannot tell a complete answer from a truncated
        one: a bare array of twenty roll-ups is indistinguishable from an account
        that happens to own twenty projects, and a dashboard would render "your
        projects" from whichever of the two it happened to be looking at.

        Paging the read rather than slicing the answer is what keeps the cost bounded
        — the grouped reads are asked only about the projects on this page, so
        neither the statement count nor the rows examined grows with the size of the
        account.
        """
        rows, total = await self._project_rollups(
            owner=owner, start=start, end=end, project_id=project_id, limit=limit, offset=offset
        )
        return Page[ProjectAnalyticsRead](
            items=rows,
            meta=PageMeta(total=total, limit=max(1, limit), offset=max(0, offset)),
        )

    async def _project_rollups(
        self,
        *,
        owner: User,
        start: date,
        end: date,
        project_id: uuid.UUID | None,
        limit: int | None,
        offset: int,
    ) -> tuple[list[ProjectAnalyticsRead], int]:
        """Build the roll-up rows for one page, with the size of the whole set.

        The single place both public methods read through, so the paged and unpaged
        forms cannot answer differently for the same rows — the failure mode every
        list envelope in this codebase is written to rule out.

        The page is taken **before** the grouped reads run, not after: the window
        completion counts are asked about the projects on this page alone, so a
        twenty-row page over an account with six hundred projects still scans
        twenty projects' tasks.

        Returns:
            The rows for the requested slice, and how many projects the filters
            match in total.
        """
        self._check_range(start, end)
        owned = await self._owned_project(project_id, owner)
        today = await self._today()
        counts = await self.metrics.project_task_counts(owner.id, as_of=today)
        if owned is not None:
            counts = [row for row in counts if row[0] == owned]
        total = len(counts)

        skip = max(0, offset)
        page = counts[skip:] if limit is None else counts[skip : skip + max(0, limit)]

        minutes = await self.metrics.project_minutes_in_range(owner.id, start=start, end=end)
        activity = await self.metrics.project_activity_counts(owner.id, start=start, end=end)
        window_counts = await self.metrics.project_completion_counts_in_range(
            owner.id,
            project_ids=[row[0] for row in page],
            start=start,
            end=end,
        )

        weeks = max(1.0, round((end - start).days / 7, 4))
        results: list[ProjectAnalyticsRead] = []
        for (
            pid,
            name,
            status,
            total_tasks,
            completed,
            remaining,
            overdue_tasks,
            _open_tasks,
        ) in page:
            session_minutes, task_estimated, task_actual = minutes.get(pid, (0, 0, 0))
            created_in_window, completed_in_window = window_counts.get(pid, (0, 0))
            results.append(
                ProjectAnalyticsRead(
                    project_id=pid,
                    name=name,
                    status=status,
                    total_tasks=total_tasks,
                    completed_tasks=completed,
                    remaining_tasks=remaining,
                    overdue_tasks=overdue_tasks,
                    completion_rate=rate(completed_in_window, created_in_window),
                    total_work_minutes=session_minutes,
                    avg_task_actual_minutes=round(session_minutes / completed, 2)
                    if completed
                    else None,
                    velocity=VelocityRead(
                        tasks_per_week=round(completed_in_window / weeks, 4)
                        if completed_in_window
                        else None,
                        estimated_minutes_per_week=round(task_estimated / weeks, 4)
                        if task_estimated
                        else None,
                        weeks_measured=weeks,
                    ),
                    velocity_tasks_per_week=round(completed_in_window / weeks, 4)
                    if completed_in_window
                    else None,
                    work_minutes=session_minutes,
                    estimated_minutes=task_estimated,
                    actual_minutes=task_actual,
                    avg_task_minutes=round(task_actual / total_tasks, 2) if total_tasks else None,
                    activity_events=int(activity.get(pid, 0)),
                    available=bool(total_tasks),
                    reason_if_unavailable=None
                    if total_tasks
                    else f"{NOT_ENOUGH_ACTIVITY}: this project has no tasks yet.",
                    range=self._metric_range(start, end),
                )
            )
        return results, total

    async def learning(self, *, owner: User, start: date, end: date) -> LearningAnalyticsRead:
        """Recorded study and knowledge-adjacent activity — no inference of mastery."""
        self._check_range(start, end)
        study_events, study_minutes = await self.metrics.study_totals_in_range(
            owner.id, start=start, end=end
        )
        knowledge = await self.metrics.knowledge_write_counts_in_range(
            owner.id, start=start, end=end
        )
        interactions = int(knowledge.get("interactions", 0))
        available = bool(study_events or interactions)
        return LearningAnalyticsRead(
            available=available,
            reason_if_unavailable=None
            if available
            else f"{NOT_ENOUGH_ACTIVITY}: no study events and no knowledge activity "
            "were recorded in this range.",
            study_events=study_events,
            study_minutes=study_minutes,
            # Phase 5 records knowledge events with no `task_id` and no
            # `project_id` — `KnowledgeService.record` passes only a title in the
            # metadata — so "tasks completed in projects the user was also writing
            # about" is **not computable** from the rows that exist. `None`, not
            # `0`: a zero would say the correlation was measured and came back
            # empty, and inside a training matrix that is a fabricated
            # observation. The field is `int | None` and its description says so;
            # the `basis` string below repeats it for the reader.
            knowledge_linked_tasks=None,
            knowledge_interactions=interactions,
            notes_created=int(knowledge.get("notes_created", 0)),
            notes_updated=int(knowledge.get("notes_updated", 0)),
            basis=(
                "calendar_events typed `study`, plus Phase 5 knowledge activity "
                "events. `knowledge_linked_tasks` is null because it is NOT "
                "measurable: Phase 5 writes its events with no task_id and no "
                "project_id, so no query can join knowledge to a project."
            ),
            definition=(
                "NEXUS stores no learning flag on any table. These figures are the "
                "two deliberate signals that do exist: calendar entries the user "
                "typed `study`, and the Phase 5 knowledge events written in the "
                "same window. Nothing is inferred beyond those recorded facts."
            ),
            range=self._metric_range(start, end),
        )

    async def knowledge(self, *, owner: User, start: date, end: date) -> KnowledgeAnalyticsRead:
        """Recorded Phase 5 knowledge activity for the window."""
        self._check_range(start, end)
        writes = await self.metrics.knowledge_write_counts_in_range(owner.id, start=start, end=end)
        by_status = await self.metrics.notes_by_status(owner.id)
        tags = await self.metrics.knowledge_tag_counts(owner.id, start=start, end=end)
        totals = await self.metrics.knowledge_counts(owner.id)
        interactions = int(writes.get("interactions", 0))
        available = bool(interactions or tags)
        return KnowledgeAnalyticsRead(
            available=available,
            reason_if_unavailable=None
            if available
            else f"{NOT_ENOUGH_ACTIVITY}: no knowledge activity was recorded in this range.",
            notes_created=int(writes.get("notes_created", 0)),
            notes_updated=int(writes.get("notes_updated", 0)),
            concepts_created=int(writes.get("concepts_created", 0)),
            resources_added=int(writes.get("resources_added", 0)),
            bookmarks_added=int(writes.get("bookmarks_added", 0)),
            links_created=int(writes.get("links_created", 0)),
            notes_published=int(by_status.get("published", 0)),
            documents_added=int(totals.get("documents", 0)),
            interactions=interactions,
            most_used_tags=[
                TagCountRead(key=str(tag_id), label=name, count=count)
                for tag_id, name, count in tags
            ],
            notes_by_status=by_status,
            range=self._metric_range(start, end),
        )

    # -- Overview and trends -------------------------------------------------

    async def overview(self, *, owner: User, start: date, end: date) -> OverviewRead:
        """The dashboard read: totals against the previous period, plus the scores.

        This is the endpoint the UI opens, so it carries the aggregate counters,
        their comparison and the headline scores together rather than making the
        client make four requests and reconcile them.

        **A read never writes.** Any day the aggregates do not already cover is
        left alone, so the totals and the daily series describe what has actually
        been measured rather than silently becoming measured by the act of asking.
        See :meth:`_totals` for what removing the on-the-fly fill was worth.

        ``stale`` reports whether the stored aggregates still describe the window:
        ``true`` means a day of the window has never been aggregated, or something
        inside it was recorded after the aggregates were computed. The second half
        is the one that was missing — a fully covered window whose tasks have been
        completed since reported ``stale: false`` beside totals that were one edit
        out of date, which is worse than no flag at all because a client trusts it.

        ``aggregates_through`` says how far the stored aggregates reach, which is
        what a client renders as "updated N minutes ago" — and ``None`` when
        nothing has ever been aggregated, rather than the end of the window that
        was merely asked about. ``POST /analytics/rebuild`` is the answer to a
        ``true`` flag; this read never recomputes anything.
        """
        self._check_range(start, end)
        previous_start, previous_end = _previous_window(start, end)
        covered = await self.metrics.covered_dates(owner.id, start=start, end=end)
        rows = await self.metrics.list_range(owner.id, start=start, end=end)
        previous_rows = await self.metrics.list_range(
            owner.id, start=previous_start, end=previous_end
        )
        window = _totals_from(rows, window_days=(end - start).days + 1)
        previous = _totals_from(previous_rows, window_days=(previous_end - previous_start).days + 1)
        totals = [
            ComparisonPoint(
                label=column,
                current=float(window.get(column, 0)),
                previous=float(previous.get(column, 0)),
                absolute_change=absolute_change(window.get(column, 0), previous.get(column, 0)),
                percent_change=percent_change(window.get(column, 0), previous.get(column, 0)),
            )
            for column in TRENDABLE_METRICS
        ]
        # A window with no aggregate row at all is the case this flag exists for:
        # it is maximally incomplete, so `stale` is true, not false. The old
        # `bool(covered) and covered != ...` guard read "nothing computed yet" as
        # "nothing to be stale about", which is exactly backwards.
        stale = covered != set(_days(start, end))
        if not stale:
            stale = await self._outpaced_by_source(owner.id, start, end)
        active = any(point.current for point in totals)
        return OverviewRead(
            range=self._metric_range(start, end),
            previous_range=self._metric_range(previous_start, previous_end),
            stale=stale,
            is_stale=stale,
            aggregates_through=await self.metrics.latest_metric_date(owner.id),
            totals=totals,
            productivity=await self.productivity(owner=owner, start=start, end=end),
            deadlines=await self.deadlines(owner=owner, start=start, end=end),
            consistency=await self.consistency(owner=owner, start=start, end=end),
            focus=await self.focus(owner=owner, start=start, end=end),
            estimation=await self.estimation(owner=owner, start=start, end=end),
            daily=[self._as_daily(row) for row in rows],
            reason_if_empty=None
            if active
            else f"{NOT_ENOUGH_ACTIVITY}: no activity has been recorded in this range. "
            "POST /analytics/rebuild computes whatever rows exist.",
        )

    async def trends(
        self, *, owner: User, metric: str, start: date, end: date, granularity: str = "day"
    ) -> list[TrendPoint]:
        """One metric over time, each bucket against the same bucket last period.

        Args:
            owner: The caller. Every query is scoped to them.
            metric: A key of :data:`TRENDABLE_METRICS`. Anything else is a
                :class:`ValidationError` — the column is resolved from an
                allowlist, never from the caller's string.
            start: Inclusive first day.
            end: Inclusive last day.
            granularity: ``day``, ``week`` or ``month``.

        Returns:
            One :class:`TrendPoint` per non-empty bucket, ascending. Empty buckets
            are omitted: a trend line through a day nothing happened is a claim
            about that day, and a client draws a gap more honestly.
        """
        self._check_range(start, end)
        self._check_granularity(granularity)
        if metric not in TRENDABLE_METRICS:
            raise ValidationError(
                f"Cannot trend {metric!r}; trends accept only "
                f"{', '.join(sorted(TRENDABLE_METRICS))}."
            )
        previous_start, previous_end = _previous_window(start, end)
        rows = await self.metrics.list_range(
            owner.id, start=start, end=end, granularity=granularity
        )
        previous_rows = await self.metrics.list_range(
            owner.id, start=previous_start, end=previous_end, granularity=granularity
        )

        current = _bucket_series(rows, metric, granularity)
        earlier = _bucket_series(previous_rows, metric, granularity)
        # Paired by **position over the emitted points**, not by date key. The two
        # windows are the same length and cut the same way, but their *dates* are a
        # full window apart, so keying the earlier series by its own bucket dates
        # meant no current bucket ever matched a previous one: `previous`,
        # `absolute_change` and `percent_change` were null on every point at every
        # granularity, which is the comparison the brief asks for and the reason
        # the request was made.
        #
        # Empty buckets are skipped on **both** sides before pairing. A rebuild
        # writes a row of zeroes for every day in the window, so the earlier
        # series is full of legitimate zeros; pairing positionally over those
        # would compare this week's first active day against last week's *first
        # day of the calendar* and read a real decline that never happened. Both
        # series omit empties, so pairing the points a client actually sees is the
        # only alignment that means anything.
        #
        # If the earlier window runs out of points the tail is left unpaired and
        # reports `previous: null`, rather than being compared against a bucket
        # from an unrelated week.
        earlier_ordered = [earlier[key] for key in sorted(earlier) if earlier[key]]
        earlier_by_position = iter(earlier_ordered)
        points: list[TrendPoint] = []
        for bucket_start in sorted(current):
            value = current[bucket_start]
            if not value:
                continue
            before = next(earlier_by_position, None)
            points.append(
                TrendPoint(
                    bucket=bucket_start,
                    label=_bucket_label(bucket_start, granularity),
                    value=float(value),
                    previous=None if before is None else float(before),
                    absolute_change=None if before is None else absolute_change(value, before),
                    percent_change=None if before is None else percent_change(value, before),
                    period_start=bucket_start,
                    period_end=_bucket_end(bucket_start, granularity),
                )
            )
        return points

    # -- Export --------------------------------------------------------------

    async def export_csv(self, *, owner: User, dataset: str, start: date, end: date) -> str:
        """Render one dataset as CSV text.

        Built with :mod:`csv` into a :class:`io.StringIO` rather than by joining
        on commas: task titles, project names and note titles routinely contain
        commas, and a hand-rolled join silently shifts every later column of every
        such row — a file that opens, renders, and is wrong.

        An empty range is **not** an error: the header row is returned with no data
        rows, so a client can always download, open and diff a file.

        Args:
            owner: The caller. Every row is scoped to them.
            dataset: A key of :data:`CSV_DATASETS`.
            start: Inclusive first day.
            end: Inclusive last day.

        Returns:
            The CSV document, newline-terminated.

        Raises:
            ValidationError: For an unknown dataset or an inverted/oversized range.
        """
        self._check_range(start, end)
        if dataset not in CSV_DATASETS:
            raise ValidationError(
                f"Cannot export {dataset!r}; export_csv accepts only "
                f"{', '.join(sorted(CSV_DATASETS))}."
            )
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\r\n")
        writer.writerow(CSV_DATASETS[dataset])

        if dataset == "daily_metrics":
            for row in await self.metrics.daily_metric_rows(owner.id, start=start, end=end):
                writer.writerow([_iso(row[0]), *row[1:]])
            return buffer.getvalue()

        if dataset == "task_performance":
            rows = await self.metrics.task_performance_rows(owner.id, start=start, end=end)
            for (
                task_id,
                project_name,
                title,
                status,
                priority,
                estimated,
                actual_minutes,
                due_date,
                created_at,
                completed_at,
            ) in rows:
                writer.writerow(
                    [
                        str(task_id),
                        # The two free-text columns the user typed. Both go
                        # through `_csv_safe`; every other cell below is either an
                        # ISO instant, an int or a closed enum the user cannot
                        # make a spreadsheet evaluate.
                        _csv_safe(project_name),
                        _csv_safe(title),
                        str(status),
                        str(priority),
                        _cell(estimated),
                        actual_minutes,
                        _iso(due_date),
                        _iso(created_at),
                        _iso(completed_at),
                        # Empty rather than 0 when there is no estimate: "not
                        # estimated" is not "estimated correctly", and a CSV that
                        # cannot tell them apart is a CSV nobody can trust.
                        "" if estimated is None else int(actual_minutes or 0) - estimated,
                    ]
                )
            return buffer.getvalue()

        for (
            session_id,
            task_id,
            project_id,
            scheduled_start,
            scheduled_end,
            actual_start,
            actual_end,
            estimated,
            actual_minutes,
            status,
        ) in await self.metrics.work_session_rows(owner.id, start=start, end=end):
            writer.writerow(
                [
                    str(session_id),
                    str(task_id or ""),
                    str(project_id or ""),
                    _iso(scheduled_start),
                    _iso(scheduled_end),
                    _iso(actual_start),
                    _iso(actual_end),
                    _cell(estimated),
                    actual_minutes,
                    str(status),
                ]
            )
        return buffer.getvalue()

    async def csv_export(
        self, *, owner: User, dataset: str, start: date, end: date
    ) -> CsvExportRead:
        """:meth:`export_csv` plus the envelope the HTTP route returns.

        Split from :meth:`export_csv` because the CSV itself is contractually a
        plain string — a caller writing it to a file should not have to strip a
        wrapper off first — while the route wants a filename and a row count so a
        cap on the export is visible to the client instead of silent.

        The count is a count of **records**, read back through the ``csv``
        reader rather than by counting newlines: a field containing CRLF — a task
        title pasted out of a Windows editor, say — is quoted rather than escaped,
        so its newline survives into the document and counting CRLF would report a
        row that is not there.
        """
        body = await self.export_csv(owner=owner, dataset=dataset, start=start, end=end)
        return CsvExportRead(
            dataset=dataset,
            filename=f"nexus-{dataset}-{start.isoformat()}-{end.isoformat()}.csv",
            content_type="text/csv; charset=utf-8",
            # ``- 1`` drops the header row, which is always written.
            row_count=max(0, sum(1 for _ in csv.reader(io.StringIO(body, newline=""))) - 1),
            columns=list(CSV_DATASETS[dataset]),
            truncated=False,
            csv=body,
            content=body,
        )

    def export_manifest(self) -> CsvExportManifestRead:
        """Which datasets exist and what columns each carries.

        Static, so it is a method rather than a query: it exists so a client can
        build an importer without hardcoding a column list in TypeScript, and a
        column list that changed without the importer noticing is the failure this
        prevents. It is a separate response model from :class:`CsvExportRead`
        because this one maps a dataset to *its* columns, where a rendered export
        carries one dataset's columns as a flat list.
        """
        return CsvExportManifestRead(
            datasets=sorted(CSV_DATASETS),
            columns={name: list(columns) for name, columns in CSV_DATASETS.items()},
        )

    # -- ML feature extraction ----------------------------------------------

    async def feature_snapshot(
        self, *, owner: User, task_id: uuid.UUID
    ) -> dict[str, float | int | str | None]:
        """The ML feature vector for one task.

        **``None`` means "not observable"; it never means zero.** A zero here is a
        training signal — "no deadline pressure", "no open work in this project" —
        and a fabricated one is indistinguishable from a real observation once it
        is in a feature matrix. A model fitted on that cannot tell them apart and
        will reproduce the fabrication. Where the schema cannot support a feature —
        no deadline, no estimate, no session ever run against the task — the key is
        present and its value is ``None``, which an imputer can handle
        deliberately rather than by accident.

        **Every row carries ``schema_version``**, the same
        ``analytics_features.v1`` contract the Phase 8/9 vectors stamp themselves
        with. Without it a training row cannot be attributed to the extraction
        that produced it, so a column whose meaning changed between two runs is
        indistinguishable from a feature that moved. It sits **beside**
            ``features``, not inside it, because ``features`` is a feature matrix:
            a positional row of numbers whose every column must be a feature. A
            string in that mapping is not a feature, and a version key smuggled
            into it becomes a column a model is then asked to fit.

        Args:
            owner: The caller. A task belonging to somebody else is *not found*,
                so this cannot be used to probe which task ids exist.
            task_id: The task to describe.

        Returns:
            A mapping carrying ``schema_version``, ``generated_at``, ``task_id``
            and a ``features`` object whose keys are stable snake_case — the same
            keys, all present, on every call whatever the data says.

        Raises:
            NotFoundError: If the task does not exist **or** is not the caller's.
        """
        task = await self.tasks.get_by_id_for_user(task_id, owner.id)
        if task is None:
            raise NotFoundError(_TASK_NOT_FOUND)
        today = await self._today()

        open_by_project = await self.metrics.open_task_counts_by_project(owner.id, None)
        overdue_by_project = await self.metrics.open_task_counts_by_project(owner.id, today)
        session_count, session_minutes = await self.metrics.task_session_totals(owner.id, task.id)
        first_work = await self.metrics.task_first_work_instant(owner.id, task.id)
        created_day = await self.metrics.task_created_day(owner.id, task.id)
        # `local_midnight`, not `datetime.combine(..., time.min, tzinfo=UTC)`: the
        # window opens at the same instant a day bucket does, in the server's own
        # zone. The UTC spelling subtracted five and a half hours from the window
        # on a `+05:30` host, so a feature named "completions in the last 30 days"
        # was a 29.8-day count — and it disagreed with `task_age_days` below, which
        # was cut in yet another calendar.
        window_start = local_midnight(today - timedelta(days=30))
        reschedules = await self.metrics.task_event_count(
            owner.id, task.id, ActivityEvent.TASK_RESCHEDULED.value
        )
        project_stats = (
            (
                await self.metrics.session.execute(
                    select(
                        func.count(Task.id),
                        func.count(Task.id).filter(Task.status == "completed"),
                        func.count(Task.id).filter(Task.completed_at >= window_start),
                    ).where(Task.project_id == task.project_id, Task.owner_id == owner.id)
                )
            ).one()
            if await self.projects.get_by_id_for_user(task.project_id, owner.id)
            else None
        )

        due_date = task.due_date
        if created_day is None:  # pragma: no cover - the task was just read owner-scoped
            created_day = (await self.metrics.as_local_time(task.created_at)).date()
        total_tasks, completed, velocity = (
            (int(project_stats[0]), int(project_stats[1]), int(project_stats[2]))
            if project_stats is not None
            else (None, None, None)
        )
        first_work_local = (
            None if first_work is None else await self.metrics.as_local_time(first_work)
        )
        return {
            "schema_version": ANALYTICS_FEATURE_SCHEMA_VERSION,
            "generated_at": today,
            "task_id": str(task.id),
            "features": {
                "priority": _priority_rank(task.priority),
                # Measured from the day the row was *filed under*, which is the day
                # this creation appears in `tasks_created`. It was a UTC date
                # subtracted from a local `today`, so a task created in the early
                # hours of this morning read as a day older than the aggregate it
                # is counted in.
                "task_age_days": max(0, (today - created_day).days),
                "estimated_minutes": task.estimated_minutes,
                # `tasks.actual_minutes` is NOT NULL with a zero default, so it cannot
                # distinguish "never tracked" from "tracked as zero" — the ambiguity
                # app/models/task.py documents. With no session behind it, the honest
                # answer is that the figure was never observed.
                "actual_minutes": task.actual_minutes if session_count else None,
                "deadline_distance_days": (due_date - today).days if due_date is not None else None,
                "reschedule_count": reschedules,
                "project_open_task_count": open_by_project.get(task.project_id, 0),
                "historical_completion_rate": rate(completed, total_tasks),
                "recent_work_minutes": session_minutes if session_count else None,
                "work_session_count": session_count,
                # The hour the user actually worked, in the server's own calendar.
                # It is a training input, so the zone is part of the label: it used
                # to be `first_work.astimezone(UTC).hour`, which on a `+05:30` host
                # filed an evening's session under the early afternoon and moved
                # every night worker into the wrong part of the distribution.
                "time_of_day": first_work_local.hour if first_work_local is not None else None,
                # The weekday of the *deadline*, or `None` when there is no deadline. It was
                # once `(due_date or task.created_at.date()).weekday()`, which reported
                # the creation weekday under the name of a deadline weekday: beside
                # `deadline_distance_days=None` in the same row, that claims a deadline
                # the task does not have, and "no deadline pressure" is exactly the
                # signal a fabricated weekday would destroy. The creation weekday is a
                # real fact about a different thing; if it is wanted, it has to be
                # asked for under its own name.
                "day_of_week": due_date.weekday() if due_date is not None else None,
                "project_velocity": velocity,
                "overdue_count": (
                    max(0, (today - due_date).days)
                    if due_date is not None and due_date < today and task.completed_at is None
                    else 0
                ),
                # Reported alongside the other features rather than inside it: the
                # project-level backlog pressure the task sits in, which a model needs
                # and which the per-task figures alone do not carry.
                "project_overdue_task_count": overdue_by_project.get(task.project_id, 0),
            },
        }

    # -- Internals -----------------------------------------------------------

    def _productivity_formula(self) -> str:
        """The formula string for the composite score, weights included."""
        weights = self.settings.analytics_productivity_weights
        return (
            f"productivity = completion x {weights['completion']:g} + "
            f"deadline x {weights['deadline']:g} + consistency x {weights['consistency']:g} "
            f"+ focus x {weights['focus']:g}, where each component is a 0-100 rate and "
            "the four weights total 100 (Settings refuses to start if they do not). "
            "A component with no data contributes zero points and says so in its "
            "explanation — it is never scored as a low result, and the remaining "
            "components keep their full weight."
        )

    @staticmethod
    def _metric_range(start: date, end: date, granularity: str = "day") -> MetricRange:
        return MetricRange(start_date=start, end_date=end, granularity=granularity)

    def _check_range(self, start: date, end: date, ceiling: int | None = None) -> None:
        """Reject an inverted or oversized window.

        Bounded because the alternative is an unbounded aggregate over tables that
        never shrink, and ``/export.csv`` is the documented answer to "I want all
        of it". The ceiling defaults to the read ceiling and is passed lower for the
        rebuild, which is the one write path.
        """
        if end < start:
            raise ValidationError("end_date must not be earlier than start_date.")
        limit = ceiling if ceiling is not None else self.settings.analytics_max_range_days
        if (end - start).days + 1 > limit:
            raise ValidationError(f"A range may span at most {limit} days.")

    @staticmethod
    def _check_granularity(granularity: str) -> None:
        if granularity not in GRANULARITIES:
            raise ValidationError(
                f"Unsupported granularity {granularity!r}; "
                f"expected one of {', '.join(GRANULARITIES)}."
            )

    async def _today(self) -> date:
        """Today's date, from the database clock, in the database's own zone.

        Never ``date.today()``: a host whose clock drifts from the server's would
        file a deadline in the wrong day, and "still overdue" is exactly the figure
        that has to agree with the rest of the system.

        Delegated to :meth:`AnalyticsRepository.today` rather than re-derived here,
        because this method used to normalise ``now()`` to **UTC** while the
        router's window resolution read ``now()``.date() in the server's zone and
        the SQL bucketed days in UTC. Three readings of "a day" inside one module:
        on a host at +05:30 they disagreed for five and a half hours a day, and a
        user opening the dashboard after midnight saw an empty *today* beside
        yesterday's eight completed tasks. Now there is one method, and the day
        buckets are cut in the same zone it returns.
        """
        return await self.metrics.today()

    async def _outpaced_by_source(self, owner_id: uuid.UUID, start: date, end: date) -> bool:
        """Whether anything recorded inside the window is newer than its aggregates.

        Coverage alone answers "was this window measured?"; it does not answer "do
        the stored rows still describe it?". A task completed after the last
        rebuild leaves a fully covered window whose totals exclude it, and the flag
        said the figures were current.

        ``None`` on either side means the comparison cannot be made — no aggregates
        at all, or nothing recorded in the window — and that is not staleness. The
        coverage check owns the "never measured" case; this only refines the "was
        measured" one.
        """
        oldest_row, newest_source = await self.metrics.aggregate_watermarks(
            owner_id, start=start, end=end
        )
        if oldest_row is None or newest_source is None:
            return False
        # Both sides are timestamps being *ordered*, not days being cut, so UTC is
        # the right label here: it is a total order over instants and it cancels
        # out of the comparison. The zone would matter only if these were dates.
        if newest_source.tzinfo is None:  # pragma: no cover - asyncpg returns aware
            newest_source = newest_source.replace(tzinfo=UTC)
        if oldest_row.tzinfo is None:  # pragma: no cover - asyncpg returns aware
            oldest_row = oldest_row.replace(tzinfo=UTC)
        return newest_source > oldest_row

    async def _owned_project(self, project_id: uuid.UUID | None, owner: User) -> uuid.UUID | None:
        """Resolve ``project_id`` through the *scoped* lookup, or 404.

        Another user's project is not a permission error here; it is a row that does
        not exist, identically to an id nobody has ever issued.
        """
        if project_id is None:
            return None
        if await self.projects.get_by_id_for_user(project_id, owner.id) is None:
            raise NotFoundError(_PROJECT_NOT_FOUND)
        return project_id

    async def _available_minutes(self, owner: User, start: date, end: date) -> int | None:
        """Declared free minutes in the window, or ``None`` with no rules.

        ``None`` rather than zero: a user who has not configured working hours has
        not said they work zero hours, and a workload ratio computed against an
        invented denominator is a number about nothing.
        """
        windows = await self.metrics.availability_windows(owner.id)
        if not windows:
            return None
        total = 0
        for day in _days(start, end):
            for weekday, window_start, window_end in windows:
                if int(weekday) == day.weekday():
                    total += _minutes_between(window_start, window_end)
        return total or None

    async def _totals(self, owner: User, start: date, end: date) -> dict[str, Any]:
        """Summed counters over the window, from the stored daily aggregates.

        **A read never writes.** This used to fill a gap: a day with no row was
        recomputed through :meth:`rebuild_range` before the totals were summed.
        That made every ``GET`` a writer — ``GET /analytics/tasks`` over a December
        window materialised five aggregate rows nobody asked for and pushed
        ``aggregates_through`` to the end of that window, so a later
        ``/overview`` reported the account as measured through 2030 when it had
        never been rebuilt once. It also made the read paths quietly *capable of a
        different window from their siblings*: the fill is bounded by
        ``analytics_rebuild_max_days`` (180) while every read is bounded by
        ``analytics_max_range_days`` (366), so ``/productivity`` and ``/overview``
        refused a 200-day window with "A range may span at most 180 days." while
        ``/deadlines``, ``/consistency`` and ``/learning`` answered it, each over
        the same dates.

        A window nobody has aggregated now sums to nothing and says so:
        ``OverviewRead.stale`` is ``true``, ``reason_if_empty`` names the reason,
        and ``POST /analytics/rebuild`` is the documented way to measure it. That
        is the same contract :meth:`daily_series` has always had.
        """
        rows = await self.metrics.list_range(owner.id, start=start, end=end)
        return _totals_from(rows, window_days=(end - start).days + 1)

    async def _deadline_result(
        self, owner_id: uuid.UUID, start: date, end: date, today: date
    ) -> tuple[Any, tuple[int, int, int]]:
        """``(result, (on_time, late, still_overdue))`` for completions in the window.

        On-time and late are decided from the same rows estimation accuracy reads —
        a task completed on its due date is on time, one completed after it is
        late, at the day granularity the data has. ``still_overdue`` is "today"
        against the **database** clock.

        ``completed_day`` arrives from the repository already cut in the database's
        own zone, which is what makes this figure agree with the ``tasks_overdue``
        total ``/overview`` serves beside it. It used to be
        ``completed_at.astimezone(UTC).date()`` computed here, and for five and a
        half hours of every day on a ``+05:30`` server that named a different day
        from the one :meth:`count_tasks_overdue_by_day` buckets on: the dashboard
        counted a task as overdue in one card and punctual in another, from one
        row, in one response.
        """
        pairs = await self.metrics.completed_pairs_in_range(owner_id, start=start, end=end)
        on_time = late = 0
        for _task_id, _estimated, _actual, due_date, completed_day in pairs:
            if due_date is None:
                continue
            if completed_day <= due_date:
                on_time += 1
            else:
                late += 1
        still_overdue = await self.metrics.overdue_count_as_of(owner_id, today)
        return (
            deadline_adherence(on_time=on_time, late=late, still_overdue=still_overdue),
            (on_time, late, still_overdue),
        )

    async def _estimation_pairs(
        self, owner_id: uuid.UUID, start: date, end: date
    ) -> list[tuple[int, int]]:
        """``(estimated, actual)`` for every task completed in the window.

        Only tasks carrying both numbers qualify. A task with no estimate is not a
        zero estimate, and admitting one would drag the bias toward "everybody
        over-estimates" on the strength of a value nobody wrote.
        """
        pairs = await self.metrics.completed_pairs_in_range(owner_id, start=start, end=end)
        return [
            (int(estimated), int(actual))
            for _task_id, estimated, actual, _due, _completed in pairs
            if estimated
        ]

    async def _project_names(self, owner_id: uuid.UUID) -> dict[uuid.UUID, str]:
        """Every project name the owner has, up to the documented ceiling.

        Paged rather than asked for once. The single page of 100 this replaced
        was not a bound, it was a guess with no failure mode: the projects past it
        were simply absent from the map, and the caller could only label them by
        whatever it substituted. Paging until the owner runs out means the bound
        is the only thing that can truncate a name lookup, and it is a stated one.

        Args:
            owner_id: The account whose projects are named.

        Returns:
            The resolved ``{project_id: name}`` map. An id missing from it belongs
            to a project this lookup did not reach, **not** to no project at all.
        """
        names: dict[uuid.UUID, str] = {}
        offset = 0
        while offset < PROJECT_NAME_LOOKUP_CEILING:
            rows, _total = await self.projects.list_for_user(
                owner_id, limit=PROJECT_NAME_PAGE_SIZE, offset=offset
            )
            if not rows:
                break
            names.update({project.id: project.name for project in rows})
            offset += len(rows)
            if len(rows) < PROJECT_NAME_PAGE_SIZE:
                break
        return names

    async def _top_overdue(
        self, owner_id: uuid.UUID, today: date
    ) -> tuple[list[OverdueTaskRead], bool]:
        """The most overdue open tasks, for the drill-down list, and whether it is short.

        Read through the task repository rather than the analytics one: it is an
        ordinary owner-scoped task listing, and asking the analytics repository to
        re-state a listing it does not own would be a second code path for the
        same question.

        The open-status predicate is asked of the **query**, once per member of
        :data:`_OPEN_STATUSES`, and the page is not narrowed afterwards. It used to
        fetch the first 100 rows due before today in either status and drop the
        closed ones in Python, so an account whose 100 earliest-due rows were all
        finished answered ``overdue_tasks: 5`` beside an empty drill-down list, and
        nothing in the response said the list had been cut. ``status`` is a single
        value on the repository call rather than a set, which is why this is a
        small loop instead of one statement; the cap is per status, and the union
        of the per-status top-N by due date contains the overall top-N by due
        date, so the merge below cannot drop a row that belonged in the result.

        Args:
            owner_id: The account whose tasks are listed.
            today: The date "overdue" is measured against, from the database clock.

        Returns:
            At most :data:`MAX_OVERDUE_ROWS` reads, most overdue first, and a flag
            saying whether more open overdue tasks existed than the list holds.
        """
        overdue: list[OverdueTaskRead] = []
        matched = 0
        for status in _OPEN_STATUSES:
            rows, total = await self.tasks.list_for_user(
                owner_id,
                limit=MAX_OVERDUE_ROWS,
                offset=0,
                status=status,
                due_before=today,
                sort="due_date",
                order="asc",
            )
            for task in rows:
                # `due_before` already excludes a null due date in SQL; the guard
                # states the invariant that `days_overdue` may not be 0 for one.
                if task.due_date is None:
                    continue
                overdue.append(
                    OverdueTaskRead(
                        task_id=task.id,
                        title=task.title,
                        due_date=task.due_date,
                        # None, not 0: a task with no due date is not "zero days
                        # late", the question does not apply to it.
                        days_overdue=(today - task.due_date).days,
                        priority=task.priority,
                    )
                )
            matched += total
        overdue.sort(key=lambda read: read.days_overdue or 0, reverse=True)
        # `matched` is the unpaginated count the repository counted in SQL, so the
        # flag is true whenever an open overdue task exists that the list does not
        # carry — including the case where a single status filled its own page.
        return overdue[:MAX_OVERDUE_ROWS], matched > MAX_OVERDUE_ROWS

    @staticmethod
    def _as_daily(row: DailyMetric) -> DailyMetricRead:
        return DailyMetricRead(
            metric_date=row.metric_date,
            **{column: int(getattr(row, column, 0) or 0) for column in TRENDABLE_METRICS},
            # Carried separately from `TRENDABLE_METRICS` because it is not a
            # trendable counter — it is the freshness stamp the client reads to
            # say "updated 5 minutes ago". Leaving it unset serialises as null on
            # every row, and the staleness banner renders null as "never
            # updated", which is the one thing the brief forbids: a dashboard
            # claiming it has no idea when its numbers were computed when in fact
            # the database knows precisely.
            updated_at=row.updated_at,
        )


def _project_label(project_id: uuid.UUID | None, names: dict[uuid.UUID, str]) -> str:
    """The project's own name, or its id. Never "Unassigned" for a real project.

    ``Unassigned`` is a claim about a **session**: this time was recorded against
    no project, and it is counted in ``unassigned_minutes`` beside this list. A
    project id that :meth:`AnalyticsService._project_names` did not resolve still
    belongs to a project, so labelling it "Unassigned" asserts that the minutes
    were unrecorded against any project while the same response counts them under
    a project key. The id is ugly; it is not a lie.
    """
    if project_id is None:
        return "Unassigned"
    return names.get(project_id) or f"Project {str(project_id)[:8]}"


def _by_day(rows: Sequence[tuple[date, int]]) -> dict[date, int]:
    """``[(day, value), ...]`` to ``{day: value}``, dropping absent keys."""
    return {day: int(value) for day, value in rows if day is not None}


def _totals_from(rows: Sequence[Any], *, window_days: int) -> dict[str, Any]:
    """Summed counters over a set of stored aggregate rows."""
    totals: dict[str, Any] = dict.fromkeys(TRENDABLE_METRICS, 0)
    for row in rows:
        for column in TRENDABLE_METRICS:
            totals[column] += int(getattr(row, column, 0) or 0)
    totals["window_days"] = window_days
    return totals


def _bucket_series(rows: Sequence[Any], metric: str, granularity: str) -> dict[date, int]:
    """``{bucket_start: value}`` for one metric over stored aggregate rows."""
    buckets: dict[date, int] = {}
    for row in rows:
        start = _bucket(row.metric_date, granularity)
        buckets[start] = buckets.get(start, 0) + int(getattr(row, metric, 0) or 0)
    return buckets


def _completion_result(window: dict[str, Any]) -> ScoreResult:
    """The completion component, as a ``ScoreResult`` the blend can consume.

    ``tasks_created`` is the denominator because it is the figure that says how much
    work entered the system in the window. Falling back to ``tasks_completed`` would
    let a window that created nothing but finished old work report 100%.
    """
    created = window["tasks_created"]
    completed = window["tasks_completed"]
    if not created:
        return ScoreResult(
            score=None,
            available=False,
            reason_if_unavailable=(
                f"{NOT_ENOUGH_ACTIVITY}: no tasks were created in this period, so there "
                "is nothing to measure completion against."
            ),
        )
    return ScoreResult(
        score=round(rate(completed, created) or 0.0, 4),
        components=[],
        available=True,
    )


def _longest_streak(ordered: Sequence[date]) -> int:
    """The longest run of consecutive days in ``ordered`` (ascending)."""
    best = run = 0
    previous: date | None = None
    for day in ordered:
        run = run + 1 if previous is not None and day == previous + timedelta(days=1) else 1
        best = max(best, run)
        previous = day
    return best


def _current_streak(ordered: Sequence[date], *, today: date | None = None) -> int:
    """The run of consecutive days ending on the most recent active day.

    ``today`` is accepted so the caller can pass the database's date; it is not
    read from a clock here, because a streak computed against a host clock is a
    streak that can disagree with every other figure in the response.
    """
    if not ordered:
        return 0
    streak = 1
    for index in range(len(ordered) - 1, 0, -1):
        if ordered[index] == ordered[index - 1] + timedelta(days=1):
            streak += 1
        else:
            break
    return streak
