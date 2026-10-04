"""Risk Center endpoints: what is currently true about the caller's plan.

Where the rules live
--------------------
Every question this router could answer — *is this risk still live, may it be
acknowledged, may it be resolved* — is answered by
:class:`~app.repositories.risk.RiskRepository`, which owns the lifecycle and puts
the owner id in the ``WHERE`` clause of every statement. This file picks a status
code, assembles the response and writes the one activity event the repository
deliberately does not.

Tenancy
-------
**No route here accepts a user id.** The caller comes from the bearer token and
is passed down as ``owner_id``, and a row that belongs to somebody else answers
**404, not 403** — identically to an id that was never issued, so the route
cannot be used to learn which risk ids are real. The repository never loads the
row in the first place; the scoping is in the query, not a filter applied
afterwards.

Route order is load-bearing here
--------------------------------
**``GET /risks/summary`` is declared before ``GET /risks/{risk_id}``.** Starlette
matches routes in the order they were added and does not prefer the literal
segment over the parameter, so with ``/{risk_id}`` registered first the literal
string ``summary`` is bound to the path parameter, the detail handler runs, and
the dashboard tile 404s on an id that never existed. Declaring the summary route
above the parameterised one is the entire fix; there is no path-conversion
trick and no ``Path`` annotation that makes it unnecessary. If a future edit
reorders these two decorators, the summary tile breaks silently — the route
still exists, it just answers a different question.

Where the three lifecycle transitions run, and why they are here at all
---------------------------------------------------------------------
:meth:`RiskDetectionService.evaluate` owns every transition the engine performs
by itself. The three a *user* performs — acknowledge, dismiss, resolve — have no
service method, and the contract puts them on this router, so the router performs
them: an owner-scoped read to decide 404 against 409, one
``transition_risk`` statement, and one ``activity_events`` row. That is a
lifecycle write with no judgement in it, not risk calculation — the interesting
question, "may this risk move here", is the repository's transition table rather
than anything this module decides.

Why 409 and not 200 for an already-terminal risk
------------------------------------------------
``transition_risk`` answers ``None`` for two different situations: a row that is
not the caller's, and a row whose current status cannot reach the requested one.
The first is a 404 and the second is a 409, and telling them apart needs the
row — so the router reads it first. A silent 200 would be worse than either: a
client that dismissed a risk it had already dismissed would render "dismissed"
and believe it had done something, when what it actually wanted to say is that
the risk is gone. Acknowledging is not resolving — it means "still true, no
longer needs my attention" — and the row keeps being re-detected either way.

Why the suggestions are joined here
-----------------------------------
:class:`~app.schemas.risk.RiskRead` carries ``recommendations``, and the risk and
its actions live in two tables that are read independently — deleting a risk must
not delete the record of having acted on it. The repository has no "these
recommendations belong to these risks" lookup, so this router issues one
owner-scoped query for the whole page rather than a lookup per row, and says so
in :func:`_recommendations_by_risk`. The join is bounded by the page: at most
:data:`MAX_PAGE_SIZE` risk ids go into it.

Pagination
----------
``limit`` is capped at :data:`MAX_PAGE_SIZE`, and the cap is a **rejection rather
than a silent truncation** — ``?limit=500`` is a 422, because a caller that
asked for 500 and received 100 cannot tell a truncated page from a page that was
always 100 rows long. See ``docs/api-conventions.md`` §Pagination.

Every filter here narrows ``total``, so a caller narrows and then still pages.
That is the whole argument for the severity filter being answered here rather
than in the browser: a client-side band filter can only see the rows it already
holds, so it can neither count a band nor offer a pager for one.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Sequence
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    ActivityServiceDep,
    AuthenticatedUser,
    DbSession,
    RiskRepositoryDep,
)
from app.core.deps import require_permission
from app.core.exceptions import ConflictError, NotFoundError
from app.core.permissions import Permission
from app.models.enums import ActivityEvent, RiskSeverity, RiskStatus, RiskType
from app.models.risk import LIVE_RISK_STATUSES, Recommendation, Risk
from app.models.user import User
from app.repositories.risk import RiskRepository
from app.schemas.risk import RiskListRead, RiskRead, RiskSummaryRead
from app.services.activity_service import ActivityService
from app.services.risk.detection import ENTITY_PROJECT, ENTITY_TASK

__all__ = ["router"]

router = APIRouter(prefix="/risks", tags=["risks"])

#: The page size a caller gets when it does not ask for one. Smaller than the
#: task board's, because a risk card carries its evidence and its suggestions and
#: is a taller row than a task card.
DEFAULT_PAGE_SIZE = 20

#: The largest page any caller may ask for. A ceiling, not a default: past this a
#: caller is a script exporting data rather than a screen, and such a caller is
#: served by walking ``offset``.
MAX_PAGE_SIZE = 100

#: Applied to every route, including the three lifecycle writes. Phase 7 reuses
#: ``analytics.read`` rather than coining a ``risks.write``: everything these
#: routes write is the caller answering a question about rows derived from their
#: own recorded work, so the capability that admits the reading already admits the
#: answering — and inventing a second permission here would grant it to exactly
#: the same roles, since the role table has no entry for either.
_ANALYTICS_READ = [Depends(require_permission(Permission.ANALYTICS_READ))]

#: One message for an id that is not the caller's and for an id that was never
#: issued. They must be indistinguishable, because the second is the answer a
#: probe for other accounts' risk ids would be hoping to separate.
_RISK_NOT_FOUND = "That risk does not exist."

#: One message for a risk whose current status cannot reach the requested one.
#: Deliberately distinct from the 404 message *because it is answered from a row
#: the caller owns*: the difference between "that is not yours" and "that is
#: already closed" is one the caller is entitled to, and it is only safe to give
#: because the 404 case was settled first.
_RISK_NOT_TRANSITIONABLE = (
    "That risk is not in a state this action can apply to. A risk that has already "
    "been resolved or dismissed cannot be acknowledged, dismissed or resolved again."
)

#: The statuses from which no further move is possible. Read by the transition
#: helper to answer a repeat of a risk's *own* terminal status with a 409, which
#: the repository's ladder deliberately permits for the detection sweep.
_TERMINAL_RISK_STATUSES = frozenset({RiskStatus.RESOLVED.value, RiskStatus.DISMISSED.value})


async def _recommendations_by_risk(
    session: AsyncSession,
    owner_id: uuid.UUID,
    risk_ids: Sequence[uuid.UUID],
) -> dict[uuid.UUID, list[Recommendation]]:
    """The suggestions raised for each of ``risk_ids``, keyed by risk.

    One owner-scoped statement for the whole page. The alternative — resolving
    each row's suggestions individually — is a round trip per card, which is the
    N+1 that makes a list endpoint feel slow, and ``RiskRepository`` has no bulk
    lookup that would avoid it. Ownership is in the ``WHERE`` clause rather than
    applied afterwards, for the reason every read in this codebase is scoped that
    way: a row the caller may not see is never loaded.

    Ordering is by ``created_at`` descending with ``id`` as a tiebreaker. The
    primary key is not decoration — two suggestions raised in the same pass share
    a second-resolution timestamp, and without a total order a caller paging
    through a Risk Center would see one twice and miss another.

    Args:
        session: The request-scoped session, used directly rather than through a
            repository because no repository method answers this question.
        owner_id: Whose suggestions these may be.
        risk_ids: The risks on the current page. An empty sequence skips the
            query entirely rather than sending ``IN ()``.

    Returns:
        A mapping from risk id to its suggestions, most urgent first. Risks with
        none are simply absent, and the caller reads that as the empty list the
        schema documents as a real state.
    """
    if not risk_ids:
        return {}
    result = await session.execute(
        select(Recommendation)
        .where(
            Recommendation.user_id == owner_id,
            Recommendation.risk_id.in_(list(risk_ids)),
        )
        .order_by(Recommendation.created_at.desc(), Recommendation.id.desc())
    )
    grouped: dict[uuid.UUID, list[Recommendation]] = defaultdict(list)
    for row in result.scalars().all():
        grouped[row.risk_id].append(row)
    return dict(grouped)


def _columns(row: Any) -> dict[str, Any]:
    """Project a stored row onto the mapping its wire model validates from.

    ``model_validate(row, from_attributes=True)`` is not used for the read
    models in this phase, and the reason is worth recording rather than working
    around silently: both declare ``metadata`` as
    ``AliasChoices("metadata_", "metadata")`` to cope with the name SQLAlchemy
    reserves on a declarative class, and under ``from_attributes`` pydantic
    resolves the first choice with ``getattr``. Today that first choice is the
    column attribute and happens to work, but ``Risk.metadata`` — the name
    pydantic would reach for if the alias order were ever the other way round —
    is SQLAlchemy's own ``MetaData`` object, and validation then fails with
    "input should be a valid dictionary" on a row that is perfectly good.
    Reading the mapper's column attributes by name produces the stored mapping
    under the column's real attribute regardless of the alias order, and picks
    up a column added later without this function being touched.
    """
    return {column.key: getattr(row, column.key) for column in inspect(type(row)).column_attrs}


def _risk_read(row: Risk, recommendations: Sequence[Recommendation]) -> RiskRead:
    """Build the wire model for one risk, with its suggestions attached.

    ``metadata`` is passed under the wire name explicitly for the reason
    :func:`_columns` gives. ``recommendations`` is not a column and so is added
    separately; an empty sequence is the state the schema documents as "no
    suggested action yet" and is not a missing value.
    """
    return RiskRead.model_validate(
        {**_columns(row), "metadata": row.metadata_, "recommendations": list(recommendations)}
    )


async def _transition(
    *,
    current_user: User,
    risks: RiskRepository,
    activity: ActivityService,
    session: AsyncSession,
    risk_id: uuid.UUID,
    target: RiskStatus,
    event: ActivityEvent,
) -> RiskRead:
    """Move one risk along its lifecycle and record why it moved.

    The read comes first and is what settles the two refusals apart: a row that is
    not the caller's is a **404**, and a row that is already terminal is a
    **409**. ``transition_risk`` answers ``None`` for both and deliberately does
    not raise for the second, because a detection run re-resolving a row another
    run has already closed should find it settled rather than fail.

    ``responded=True`` is what distinguishes "the user closed this" from "the
    condition went away", which the repository stores in ``metadata`` — and that
    is the difference a future model would be trained on.

    The activity event carries ids and numbers only. The risk's title is stored on
    the row itself, and duplicating a user's own words into a feed nobody asked
    for is how a summary ends up quoting text the user has since changed.

    Args:
        current_user: The caller, resolved from the bearer token.
        risks: The owner-scoped repository the transition is written through.
        activity: The history sink the event is appended to.
        session: The request-scoped session, for the suggestions join.
        risk_id: The risk to move.
        target: The status to move it to.
        event: The lifecycle event that transition is.

    Returns:
        The updated risk, suggestions attached, as the transition endpoints
        return the row rather than a status alone — a client that acknowledged
        something needs to redraw the card from the server's answer rather than
        from its own guess about what happened.

    Raises:
        NotFoundError: 404 when the risk is not the caller's or does not exist.
        ConflictError: 409 when the risk is already in a terminal state.
    """
    row = await risks.get_risk(current_user.id, risk_id)
    if row is None:
        raise NotFoundError(_RISK_NOT_FOUND)
    # The repository's ladder permits a self-transition on a terminal row
    # (`resolved -> resolved`), because the detection sweep needs to re-resolve a
    # row another run already closed without failing. That is right for the
    # sweep and wrong for this endpoint: a user who clicks "dismiss" on a risk
    # that is already dismissed should be told so, not handed a cheerful 200
    # and a second dismissal event. Answered here rather than in the repository
    # because the two callers genuinely want different answers.
    if row.status == target.value and row.status in _TERMINAL_RISK_STATUSES:
        raise ConflictError(_RISK_NOT_TRANSITIONABLE)
    updated = await risks.transition_risk(
        current_user.id, risk_id, status=target.value, responded=True
    )
    if updated is None:
        raise ConflictError(_RISK_NOT_TRANSITIONABLE)
    await activity.record(
        event,
        user_id=current_user.id,
        project_id=updated.entity_id if updated.entity_type == ENTITY_PROJECT else None,
        task_id=updated.entity_id if updated.entity_type == ENTITY_TASK else None,
        metadata={
            "risk_id": str(updated.id),
            "risk_type": updated.risk_type,
            "severity": updated.severity,
            "score": int(updated.score),
            "status": updated.status,
        },
    )
    suggestions = await _recommendations_by_risk(session, current_user.id, [updated.id])
    return _risk_read(updated, suggestions.get(updated.id, []))


@router.get(
    "",
    response_model=RiskListRead,
    summary="The caller's risks, worst first",
    dependencies=_ANALYTICS_READ,
)
async def list_risks(
    current_user: AuthenticatedUser,
    risks: RiskRepositoryDep,
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
    risk_status: Annotated[RiskStatus | None, Query(alias="status")] = None,
    risk_type: Annotated[RiskType | None, Query()] = None,
    severity: Annotated[RiskSeverity | None, Query()] = None,
) -> RiskListRead:
    """One page of risks, ordered by severity then by how recently each was seen.

    **Ordering is the repository's, not the client's.** Severity words do not sort
    alphabetically into their own severity order — ``medium`` sorts above ``low``
    and both above ``high`` — so the ranking goes through a ``CASE`` built from
    the enum. A client that sorted the page itself would have to reimplement that
    and would eventually get it wrong.

    ``status``, ``risk_type`` and ``severity`` are single-valued and are checked
    against the enums by FastAPI, so ``?status=nonsense`` is a 422 rather than a
    filter that quietly matches nothing. The band filter is the server's for a
    reason a client cannot fix: narrowing a page in the browser can only count
    the rows that page happened to carry, so the tiles would understate a band
    that continued onto page two and the pager would have to be withdrawn for
    want of a total. Here the same word narrows ``items``, ``total`` and the
    tally together, and paging keeps working.

    ``by_severity`` counts **the same set the items come from** — all three
    filters, applied by the repository's one filter builder, so the header tiles
    and the rows underneath them cannot describe different questions. That makes
    ``sum(by_severity.values()) == total`` an invariant a client may rely on, and
    it is why the band filter leaves the other three bands at zero rather than
    reporting what they would have said.

    **The suggestions are joined, so a card shows its actions.** A risk with none
    carries an empty list, which the Risk Center renders as "No suggested action
    yet" — a rule had nothing to propose, which is not the same as not having
    looked.
    """
    statuses = [risk_status.value] if risk_status is not None else None
    types = [risk_type.value] if risk_type is not None else None
    bands = [severity.value] if severity is not None else None
    rows, total = await risks.list_risks(
        current_user.id,
        statuses=statuses,
        risk_types=types,
        severities=bands,
        limit=limit,
        offset=offset,
    )
    by_severity = await risks.count_by_severity(
        current_user.id, statuses=statuses, risk_types=types, severities=bands
    )
    suggestions = await _recommendations_by_risk(session, current_user.id, [row.id for row in rows])
    return RiskListRead(
        items=[_risk_read(row, suggestions.get(row.id, [])) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
        by_severity=by_severity,
    )


@router.get(
    "/summary",
    response_model=RiskSummaryRead,
    summary="Compact counts for the dashboard",
    dependencies=_ANALYTICS_READ,
)
async def risk_summary(
    current_user: AuthenticatedUser,
    risks: RiskRepositoryDep,
) -> RiskSummaryRead:
    """Return the live severity tallies, and nothing else.

    **Declared before ``GET /risks/{risk_id}`` on purpose** — see this module's
    docstring. Starlette matches in declaration order, so the reverse order binds
    the literal string ``summary`` to the path parameter and this tile answers
    with a 404 about an id that never existed.

    The counts are of **live** risks — ``active`` and ``acknowledged`` — because
    a resolved one is history, and a dashboard that kept counting it would never
    let a user see that clearing a backlog changed anything.

    An account with nothing flagged gets ``200`` with five zeroes and
    ``needs_attention: false``. That is a real answer rather than an error, and
    it is the good news the widget exists to deliver.
    """
    counts = await risks.count_by_severity(current_user.id, statuses=list(LIVE_RISK_STATUSES))
    return RiskSummaryRead(**counts, total=sum(counts.values()))


@router.get(
    "/{risk_id}",
    response_model=RiskRead,
    summary="One risk, with its evidence and its suggestions",
    dependencies=_ANALYTICS_READ,
)
async def get_risk(
    current_user: AuthenticatedUser,
    risks: RiskRepositoryDep,
    session: DbSession,
    risk_id: uuid.UUID,
) -> RiskRead:
    """Return a single risk, explained.

    **404 for another account's risk, never 403.** The row is resolved through an
    owner-scoped lookup and simply is not loaded if it is not the caller's, so
    the endpoint cannot be used to discover which risk ids exist.

    The evidence lines are the ones the detection pass stored, not a re-derivation
    at read time — a stored risk has to still explain itself after the aggregates
    it was computed from have been rebuilt.
    """
    row = await risks.get_risk(current_user.id, risk_id)
    if row is None:
        raise NotFoundError(_RISK_NOT_FOUND)
    suggestions = await _recommendations_by_risk(session, current_user.id, [row.id])
    return _risk_read(row, suggestions.get(row.id, []))


@router.post(
    "/{risk_id}/acknowledge",
    response_model=RiskRead,
    summary="'Still true, no longer needs my attention'",
    dependencies=_ANALYTICS_READ,
)
async def acknowledge_risk(
    current_user: AuthenticatedUser,
    risks: RiskRepositoryDep,
    activity: ActivityServiceDep,
    session: DbSession,
    risk_id: uuid.UUID,
) -> RiskRead:
    """Move a risk to ``acknowledged`` and return the updated row.

    **Acknowledging is not resolving.** The risk stays live and keeps being
    re-detected, so a later pass refreshes its score and its evidence rather than
    opening a second row — which is what makes "I accept this is still true"
    survive the next evaluation instead of being undone by it.

    Errors: 404 for an id that is not the caller's, 409 for one already resolved
    or dismissed.
    """
    return await _transition(
        current_user=current_user,
        risks=risks,
        activity=activity,
        session=session,
        risk_id=risk_id,
        target=RiskStatus.ACKNOWLEDGED,
        event=ActivityEvent.RISK_ACKNOWLEDGED,
    )


@router.post(
    "/{risk_id}/dismiss",
    response_model=RiskRead,
    summary="'This does not apply to me'",
    dependencies=_ANALYTICS_READ,
)
async def dismiss_risk(
    current_user: AuthenticatedUser,
    risks: RiskRepositoryDep,
    activity: ActivityServiceDep,
    session: DbSession,
    risk_id: uuid.UUID,
) -> RiskRead:
    """Move a risk to ``dismissed`` and return the updated row.

    Dismissal is terminal, and it is kept distinct from ``resolved`` on purpose:
    a dismissed risk was judged not to apply, a resolved one had its condition go
    away. The two are different answers, and the brief's framing — a risk worth
    surfacing is a risk worth acting on, including by declining it — is only true
    if the decline is recorded as itself.

    A dismissed risk is not re-detected: the identity is free again, so if the
    condition genuinely returns a later pass writes a new row rather than
    reopening this one and overwriting the ``detected_at`` that ``resolved_at`` is
    measured against.

    Errors: 404 for an id that is not the caller's, 409 for one already resolved
    or dismissed.
    """
    return await _transition(
        current_user=current_user,
        risks=risks,
        activity=activity,
        session=session,
        risk_id=risk_id,
        target=RiskStatus.DISMISSED,
        event=ActivityEvent.RISK_DISMISSED,
    )


@router.post(
    "/{risk_id}/resolve",
    response_model=RiskRead,
    summary="'The condition is over'",
    dependencies=_ANALYTICS_READ,
)
async def resolve_risk(
    current_user: AuthenticatedUser,
    risks: RiskRepositoryDep,
    activity: ActivityServiceDep,
    session: DbSession,
    risk_id: uuid.UUID,
) -> RiskRead:
    """Move a risk to ``resolved`` and return the updated row.

    **Usually written by the detection pass**, which closes every live risk it no
    longer re-detects — that reconciliation is what stops the Risk Center from
    only ever growing. This route exists for the case where the user knows the
    condition is over before the next pass runs.

    Marking it resolved does **not** stop a future detection from raising a new
    risk for the same condition: the live-identity index is partial and a
    terminal row is outside it, so a condition that comes back is a new episode
    with its own ``detected_at``. What it does stop is the row counting as
    something to act on.

    Errors: 404 for an id that is not the caller's, 409 for one already resolved
    or dismissed.
    """
    return await _transition(
        current_user=current_user,
        risks=risks,
        activity=activity,
        session=session,
        risk_id=risk_id,
        target=RiskStatus.RESOLVED,
        event=ActivityEvent.RISK_RESOLVED,
    )
