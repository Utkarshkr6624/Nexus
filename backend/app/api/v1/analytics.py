"""Analytics endpoints: the Phase 6 data engine over HTTP.

A translation layer and nothing else. Every question — *what is the completion
rate*, *how many rows does a rebuild write*, *is this feature observable* — is
answered by :class:`~app.services.analytics.service.AnalyticsService` and
:mod:`app.services.analytics.scoring`. This router resolves the caller's window,
picks a status code and hands the result back.

Tenancy
-------
**No route here accepts a user id.** The caller comes from the bearer token and
is passed to the service as ``owner=``, which puts ``owner_id`` in the ``WHERE``
clause of every statement. ``GET /analytics/feature-snapshot?task_id=`` resolves
that id through the same owner-scoped lookup, so another account's task answers
**404 and never 403** — identical to an id that does not exist, so the route
cannot be used to learn which task ids are real.

The default window
------------------
``start_date``/``end_date`` default to the last
``ANALYTICS_DEFAULT_RANGE_DAYS`` days **ending on the database's current date**.
The date comes from ``SELECT now()`` rather than from ``datetime.now()`` for the
same reason every other "now" in this codebase does: a host whose clock has
drifted would otherwise bucket today's activity into yesterday.

Endpoint count — the decision the brief asked for
------------------------------------------------
The brief asks for fewer, cleaner endpoints over ten thin ones, and names
``/deadlines`` as the candidate to fold into ``/overview``. The fold is **already
made** in the response model: :class:`~app.schemas.analytics.OverviewRead`
carries ``productivity``, ``consistency``, ``deadlines``, ``focus``,
``estimation`` and ``workload`` alongside the totals, because a dashboard that
made two requests over the same window would render two numbers that can differ
by one task created in between.

``GET /analytics/deadlines`` is nevertheless kept, for one reason: it is the
route a drill-down panel calls, it returns exactly one nested object instead of
the whole overview, and it is the surface where the *unavailable* case is read
on its own — "Not enough activity yet" with no five other panels competing for
attention. Removing it would save one route and cost the dashboard its cheapest
way to explain itself. Consistency, focus and estimation have no such route:
they are reachable inside ``/overview`` and ``/productivity``, which is where
they are actually read.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import func, select

from app.api.deps import (
    AnalyticsServiceDep,
    AuthenticatedUser,
    DbSession,
    SettingsDep,
    get_authenticated_user,
)
from app.core.deps import require_permission
from app.core.permissions import Permission
from app.schemas.analytics import (
    ConsistencyRead,
    CsvExportManifestRead,
    DailyMetricRead,
    DeadlineAdherenceRead,
    EstimationAccuracyRead,
    FocusRead,
    KnowledgeAnalyticsRead,
    LearningAnalyticsRead,
    OverviewRead,
    ProductivityRead,
    ProjectAnalyticsRead,
    TaskAnalyticsRead,
    TimeDistributionRead,
    TrendPoint,
    WorkloadRead,
)
from app.schemas.common import Page

router = APIRouter(prefix="/analytics", tags=["analytics"])

#: Applied to every route. Analytics is the one surface that characterises a
#: person, so it is a capability in its own right rather than a byproduct of
#: holding ``tasks.read`` — and it is read-only, so there is no write
#: permission to pair it with.
_ANALYTICS_READ = [Depends(require_permission(Permission.ANALYTICS_READ))]

#: Granularity values the series and trend routes accept. An allowlist rather
#: than a free string, because the value selects a bucketing rule and an unknown
#: one has no rule to fall back to.
_GRANULARITY_DESCRIPTION = "Bucket size: day, week or month."

#: Rows a project roll-up page carries when the caller names none.
DEFAULT_PAGE_SIZE = 20

#: The largest project page any caller may ask for. A ceiling, not a default: a
#: caller reaching it is a script walking the whole set, and such a caller pages
#: through ``offset`` rather than being handed everything in one response.
MAX_PAGE_SIZE = 100


async def get_today(session: DbSession) -> date:
    """Return the current date according to the **database** clock.

    The only ``now`` on this surface, and it is a query rather than
    ``date.today()`` for the reason the planner gives: the application's host and
    the database server can disagree, and whichever one is wrong would decide
    which day the user is looking at.
    """
    value = await session.scalar(select(func.now()))
    return value.date() if value is not None else date.today()


_Today = Annotated[date, Depends(get_today)]


async def resolve_window(
    today: _Today,
    settings: SettingsDep,
    start_date: Annotated[
        date | None, Query(description="First day of the window, inclusive.")
    ] = None,
    end_date: Annotated[
        date | None, Query(description="Last day of the window, inclusive.")
    ] = None,
) -> tuple[date, date]:
    """Fill in the two missing ends of a window and reject an impossible one.

    ``end_date`` defaults to today and ``start_date`` to
    ``ANALYTICS_DEFAULT_RANGE_DAYS`` before it. An inverted window is refused
    here with a 422 rather than being passed down to an aggregation, because a
    reversed range would otherwise produce an empty series that reads exactly
    like "you did nothing".

    Raises:
        HTTPException: 422 for ``end_date`` before ``start_date`` or a window
            wider than ``ANALYTICS_MAX_RANGE_DAYS``.
    """
    from fastapi import HTTPException

    resolved_end = end_date or today
    resolved_start = start_date or (
        resolved_end - timedelta(days=settings.analytics_default_range_days - 1)
    )
    if resolved_end < resolved_start:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="end_date must not be earlier than start_date.",
        )
    if (resolved_end - resolved_start).days + 1 > settings.analytics_max_range_days:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"The analytics window may span at most {settings.analytics_max_range_days} days."
            ),
        )
    return resolved_start, resolved_end


_Window = Annotated[tuple[date, date], Depends(resolve_window)]


@router.get(
    "/overview",
    response_model=OverviewRead,
    summary="The dashboard's first paint",
    dependencies=_ANALYTICS_READ,
)
async def get_overview(
    current_user: AuthenticatedUser,
    analytics: AnalyticsServiceDep,
    window: _Window,
) -> OverviewRead:
    """Return totals with period-over-period comparisons plus every headline score.

    **One request, one window, one set of numbers.** Six of the other routes on
    this router return slices of this response; a dashboard that issued them all
    would render six views of the same period that could disagree by one task
    created mid-render.

    Anything the recorded rows cannot support comes back as
    ``available=False`` with a reason, never as a zero. A brand-new account
    returns **200** with six scores saying "Not enough activity yet" — that is a
    successful answer to the question asked, not an error.

    Errors: 422 for an inverted or oversized window.
    """
    start, end = window
    return await analytics.overview(owner=current_user, start=start, end=end)


@router.get(
    "/productivity",
    response_model=ProductivityRead,
    summary="The transparent 0-100 productivity score",
    dependencies=_ANALYTICS_READ,
)
async def get_productivity(
    current_user: AuthenticatedUser,
    analytics: AnalyticsServiceDep,
    window: _Window,
) -> ProductivityRead:
    """Return the weighted composite with **every term shown**.

    The response carries the four component scores, their ``points`` and
    ``max_points``, and a ``formula`` string naming the weights — so a user can
    read back exactly how the headline number was produced. A component with no
    data is *excluded* and named in ``reason_if_unavailable`` rather than scored
    as zero.

    This is a NEXUS-derived engineering metric computed from rows this
    application wrote. It is not a validated measure of a person, and a low score
    means little recorded activity rather than low productivity.

    Errors: 422 for an inverted or oversized window.
    """
    start, end = window
    return await analytics.productivity(owner=current_user, start=start, end=end)


@router.get(
    "/deadlines",
    response_model=DeadlineAdherenceRead,
    summary="On-time rate for work that had a deadline",
    dependencies=_ANALYTICS_READ,
)
async def get_deadlines(
    current_user: AuthenticatedUser,
    analytics: AnalyticsServiceDep,
    window: _Window,
) -> DeadlineAdherenceRead:
    """Return the on-time share of everything with a due date.

    **The drill-down for a figure that `/overview` also carries.** Kept as its
    own route for the reason this module's docstring gives: it is the panel that
    has to render "not enough activity yet" on its own, without five other
    cards competing for the reader's attention.

    A user with two completed tasks and no deadline pressure is told NEXUS has
    not observed enough to score them. They are not told 0%.

    Errors: 422 for an inverted or oversized window.
    """
    start, end = window
    return await analytics.deadlines(owner=current_user, start=start, end=end)


@router.get(
    "/consistency",
    response_model=ConsistencyRead,
    summary="How regularly work was recorded",
    dependencies=_ANALYTICS_READ,
)
async def get_consistency(
    current_user: AuthenticatedUser,
    analytics: AnalyticsServiceDep,
    window: _Window,
) -> ConsistencyRead:
    """Return the consistency score for the window.

    Also nested inside `/overview`; exposed here because a settings page renders
    this one figure with its streak counters and would otherwise have to pull
    the entire overview to do it.
    """
    start, end = window
    return await analytics.consistency(owner=current_user, start=start, end=end)


@router.get(
    "/focus",
    response_model=FocusRead,
    summary="Session depth and slot adherence",
    dependencies=_ANALYTICS_READ,
)
async def get_focus(
    current_user: AuthenticatedUser,
    analytics: AnalyticsServiceDep,
    window: _Window,
) -> FocusRead:
    """Return the focus score for the window, or why it cannot be computed.

    Derived from recorded work sessions only. It makes no claim about human
    attention, and it is unavailable rather than zero until at least one planned
    session has been run to completion.
    """
    start, end = window
    return await analytics.focus(owner=current_user, start=start, end=end)


@router.get(
    "/estimation",
    response_model=EstimationAccuracyRead,
    summary="How far estimates land from tracked time",
    dependencies=_ANALYTICS_READ,
)
async def get_estimation(
    current_user: AuthenticatedUser,
    analytics: AnalyticsServiceDep,
    window: _Window,
) -> EstimationAccuracyRead:
    """Return estimation accuracy over tasks that have both numbers.

    ``bias`` is mean(estimated - actual): **negative means the estimates ran
    below the time actually taken**, which is the same direction as the
    under-estimation rate beside it. Only tasks carrying both an estimate and
    tracked time are counted — a task with no estimate is not a zero estimate.
    """
    start, end = window
    return await analytics.estimation(owner=current_user, start=start, end=end)


@router.get(
    "/workload",
    response_model=WorkloadRead,
    summary="Planned against actual",
    dependencies=_ANALYTICS_READ,
)
async def get_workload(
    current_user: AuthenticatedUser,
    analytics: AnalyticsServiceDep,
    window: _Window,
) -> WorkloadRead:
    """Return planned minutes against tracked minutes, plus overloaded days."""
    start, end = window
    return await analytics.workload(owner=current_user, start=start, end=end)


@router.get(
    "/time",
    response_model=TimeDistributionRead,
    summary="Where the recorded time went",
    dependencies=_ANALYTICS_READ,
)
async def get_time_distribution(
    current_user: AuthenticatedUser,
    analytics: AnalyticsServiceDep,
    window: _Window,
    project_id: Annotated[UUID | None, Query(description="Restrict to one project.")] = None,
) -> TimeDistributionRead:
    """Return tracked minutes sliced by hour of day and day of week.

    Derived from ``work_sessions.actual_start`` in the user's own zone. A
    project that is not the caller's is a **404**, resolved through an
    owner-scoped lookup before the aggregate runs.
    """
    start, end = window
    return await analytics.time_distribution(
        owner=current_user, start=start, end=end, project_id=project_id
    )


@router.get(
    "/projects",
    response_model=Page[ProjectAnalyticsRead],
    summary="Per-project rollups for the window, one page at a time",
    dependencies=_ANALYTICS_READ,
)
async def get_project_analytics(
    current_user: AuthenticatedUser,
    analytics: AnalyticsServiceDep,
    window: _Window,
    project_id: Annotated[UUID | None, Query(description="Restrict to one project.")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE, description="Rows per page.")] = (
        DEFAULT_PAGE_SIZE
    ),
    offset: Annotated[int, Query(ge=0, description="Rows to skip.")] = 0,
) -> Page[ProjectAnalyticsRead]:
    """Return a bounded page of per-project figures computed from the source rows.

    **There is no ``project_metrics`` table.** A weekly or monthly figure is a
    bucket of ``daily_metrics``; a per-project figure is a ``GROUP BY`` over
    ``tasks`` and ``work_sessions``, which are indexed on the owner and already
    hold the truth. Materialising either would create a second answer to a
    question the source rows answer exactly, and a recompute job to keep the two
    in step.

    **The response is a page, not an array.** ``meta.total`` counts every project
    the filters match, so a client can tell twenty of two hundred from twenty of
    twenty; ``items`` is the window ``[offset, offset + limit)`` in project-name
    order. An unbounded array here said nothing about whether the set was
    complete, and the only way to find out was to ask for a page that did not
    exist.

    A ``project_id`` for another account is a **404** — the id is resolved through
    the owner-scoped lookup before any aggregate runs, so the route cannot be used
    to learn which project ids are real.

    Errors: 422 for an inverted or oversized window, or a ``limit`` outside 1-100.
    """
    start, end = window
    return await analytics.project_analytics_page(
        owner=current_user,
        start=start,
        end=end,
        project_id=project_id,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/tasks",
    response_model=TaskAnalyticsRead,
    summary="Task counts, throughput and estimation spread",
    dependencies=_ANALYTICS_READ,
)
async def get_task_analytics(
    current_user: AuthenticatedUser,
    analytics: AnalyticsServiceDep,
    window: _Window,
) -> TaskAnalyticsRead:
    """Return the task-level aggregate for the window."""
    start, end = window
    return await analytics.task_analytics(owner=current_user, start=start, end=end)


@router.get(
    "/learning",
    response_model=LearningAnalyticsRead,
    summary="Task throughput as a learning curve",
    dependencies=_ANALYTICS_READ,
)
async def get_learning(
    current_user: AuthenticatedUser,
    analytics: AnalyticsServiceDep,
    window: _Window,
) -> LearningAnalyticsRead:
    """Return throughput, completion rate and estimation accuracy together.

    Reported from recorded task and session rows only. Nothing in this response
    infers anything about how the user *learned*; the name is the product's
    vocabulary, not a claim.
    """
    start, end = window
    return await analytics.learning(owner=current_user, start=start, end=end)


@router.get(
    "/knowledge",
    response_model=KnowledgeAnalyticsRead,
    summary="Knowledge-base activity for the window",
    dependencies=_ANALYTICS_READ,
)
async def get_knowledge(
    current_user: AuthenticatedUser,
    analytics: AnalyticsServiceDep,
    window: _Window,
) -> KnowledgeAnalyticsRead:
    """Return notes, concepts, resources, links and bookmarks touched in the window.

    Scoped by ``owner_id`` in every statement, so a user sees only their own
    knowledge base — which, being private by construction, is what makes this
    surface safe at all.
    """
    start, end = window
    return await analytics.knowledge(owner=current_user, start=start, end=end)


@router.get(
    "/trends",
    response_model=list[TrendPoint],
    summary="One metric over time",
    dependencies=_ANALYTICS_READ,
)
async def get_trends(
    current_user: AuthenticatedUser,
    analytics: AnalyticsServiceDep,
    window: _Window,
    metric: Annotated[
        str, Query(description="A daily column name, e.g. tasks_completed.")
    ] = "tasks_completed",
    granularity: Annotated[str, Query(description=_GRANULARITY_DESCRIPTION)] = "day",
) -> list[TrendPoint]:
    """Return one metric bucketed over time, read from ``daily_metrics``.

    The metric is resolved against an allowlist, never from the caller's string
    into a ``getattr`` — ``?metric=owner_id`` must not be a column read the
    caller chose.

    Buckets with no recorded activity are **omitted**, not zero-filled: a trend
    line through a day nothing happened is a claim about the day, and the client
    draws a gap more honestly than a straight line.
    """
    start, end = window
    return await analytics.trends(
        owner=current_user, metric=metric, start=start, end=end, granularity=granularity
    )


@router.get(
    "/series",
    response_model=list[DailyMetricRead],
    summary="The stored daily aggregates",
    dependencies=_ANALYTICS_READ,
)
async def get_daily_series(
    current_user: AuthenticatedUser,
    analytics: AnalyticsServiceDep,
    window: _Window,
    granularity: Annotated[str, Query(description=_GRANULARITY_DESCRIPTION)] = "day",
) -> list[DailyMetricRead]:
    """Return the ``daily_metrics`` rows for the window, optionally re-bucketed.

    Reads the aggregate table and nothing else — a read that re-aggregated would
    undo the reason ``daily_metrics`` exists. A window that has never been
    rebuilt comes back empty rather than being recomputed on the fly, and
    ``is_stale`` on `/overview` is what tells a client the difference.
    """
    start, end = window
    return await analytics.daily_series(
        owner=current_user, start=start, end=end, granularity=granularity
    )


@router.post(
    "/rebuild",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Recompute the daily aggregates for a window",
    dependencies=_ANALYTICS_READ,
)
async def rebuild(
    current_user: AuthenticatedUser,
    analytics: AnalyticsServiceDep,
    window: _Window,
) -> Response:
    """Recompute ``daily_metrics`` for every day in the window.

    Returns **202**, not 200: the work is a write over a bounded range and the
    response body is the number of daily rows written, so a client can show
    "rebuilt N days" immediately. The statement count is a constant that does not
    grow with the length of the range — one grouped query per source table and
    one upsert — so this is a bounded amount of work whatever window it is given.

    **Idempotent.** Re-running over the same window upserts onto
    ``UNIQUE (user_id, metric_date)``: the same rows, the same count, no
    duplicates.
    """
    start, end = window
    rows = await analytics.rebuild_range(owner=current_user, start=start, end=end)
    return Response(
        status_code=status.HTTP_202_ACCEPTED,
        content=f'{{"rows_written": {rows}}}',
        media_type="application/json",
    )


@router.get(
    "/export",
    response_model=CsvExportManifestRead,
    summary="Which CSV datasets exist, and their columns",
    # The route takes no caller, so nothing here would consult the ``sessions``
    # table on its own. ``require_permission`` resolves the caller through
    # ``app.core.deps.get_current_user``, which never does — a revoked or
    # superseded bearer therefore kept reaching this manifest for the whole
    # ``ACCESS_TOKEN_EXPIRE_MINUTES``. Adding the check as a dependency rather
    # than a parameter is deliberate: it says "the token must still be live"
    # without implying the manifest is scoped to anyone.
    dependencies=[*_ANALYTICS_READ, Depends(get_authenticated_user)],
)
async def export_manifest(
    analytics: AnalyticsServiceDep,
) -> CsvExportManifestRead:
    """List the export datasets and the columns each one carries.

    **This is what keeps ``/export.csv`` a plain download.** A client that needs
    to know the shape of a file — to build an importer, or to inspect the Phase 10
    training set the brief mentions — asks here, instead of hardcoding a column
    list in TypeScript or parsing a sample download. The column list is a
    contract: changing it is a breaking change for importers, and this route is
    where that change becomes visible before it ships.
    """
    return analytics.export_manifest()


@router.get(
    "/export.csv",
    summary="Download a dataset as CSV",
    dependencies=_ANALYTICS_READ,
)
async def export_csv(
    current_user: AuthenticatedUser,
    analytics: AnalyticsServiceDep,
    window: _Window,
    dataset: Annotated[
        str, Query(description="One of daily_metrics, task_performance or work_sessions.")
    ] = "daily_metrics",
) -> Response:
    """Download one dataset as RFC 4180 ``text/csv``.

    **This is a file, not JSON.** The route is named ``.csv`` and a browser
    pointed at it should get a download with the right filename, which is what
    ``Content-Disposition`` and an explicit ``media_type="text/csv"`` buy. The
    row count travels in the ``X-Nexus-Row-Count`` header so a client can say
    "exported N rows" without parsing the body first — the concern a JSON
    envelope was there to solve, without turning a download into an API response.

    The column list lives on ``GET /analytics/export``, the manifest, so a client
    builds its importer against one source rather than a column list copied out
    of a sample file.

    **An empty window is not an error**: the header row is returned with no data
    rows, so a client can always download, open and diff a file.

    Errors: 422 for an unknown dataset, an inverted window, or one wider than
    ``ANALYTICS_MAX_RANGE_DAYS``.
    """
    start, end = window
    exported = await analytics.csv_export(owner=current_user, dataset=dataset, start=start, end=end)
    return Response(
        content=exported.csv,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{exported.filename}"',
            "X-Nexus-Row-Count": str(exported.row_count),
        },
    )


@router.get(
    "/feature-snapshot",
    summary="The ML feature vector for one task",
    dependencies=_ANALYTICS_READ,
)
async def feature_snapshot(
    current_user: AuthenticatedUser,
    analytics: AnalyticsServiceDep,
    task_id: Annotated[UUID, Query(description="The task to describe.")],
) -> dict[str, Any]:
    """Return the named feature vector for one of the caller's tasks.

    **404 for another account's task, never 403.** The id is resolved through an
    owner-scoped lookup, so the row is never loaded and the refusal is the one a
    nonexistent id gets — the route cannot be used to learn which task ids exist.

    **Every key is always present; a value may be ``None``, never a fabricated
    zero.** A zero in a feature matrix is a training signal ("no deadline
    pressure"), and a manufactured one is indistinguishable from an observation
    once it is in there. Where the schema cannot support a feature — no due
    date, no estimate, no sessions ever run against this task — the key is
    present with ``None``, which an imputer handles deliberately.
    """
    return await analytics.feature_snapshot(owner=current_user, task_id=task_id)


#: The router only; handlers are reached through it, not imported directly.
__all__ = ["router"]
