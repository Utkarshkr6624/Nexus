"""Recommendation endpoints: the actions a risk has raised, and what the user did.

Where the rules live
--------------------
Raising a suggestion is :class:`~app.services.risk.recommendation.RecommendationService`
and answering one is the same class: ``accept``, ``reject``, ``complete`` and
``view`` each write the lifecycle column, stamp ``responded_at`` where it applies
and append the matching ``ActivityEvent``. This router resolves the caller's
id, picks a status code and hands the row back.

The engine proposes and never performs
--------------------------------------
Every member of :class:`~app.models.enums.RecommendationType` names something a
person does, and this router adds no operation that is not one of the four
decisions. Accepting a suggestion does not reschedule the task, block the time or
move the deadline it names — it records that the user said they would, which is a
different fact from the one the word "accept" suggests, and is why the endpoints
are named for the answer rather than for the effect.

Tenancy
-------
**No route here accepts a user id**, and a foreign id is a **404, not 403** —
identical to an id nobody has ever issued, so the endpoint cannot be used to learn
which recommendation ids are real.

404 for a foreign id and 409 for a lifecycle the row cannot reach are told apart
by the service, which resolves the row through an owner-scoped lookup before it
writes. The router does not repeat either check: doing so would mean the row is
read twice and that the two answers could disagree.

``GET /recommendations`` carries no summary sentence, unlike the risk list
--------------------------------------------------------------------------
:class:`~app.schemas.recommendation.RecommendationListRead` is frozen at five
fields and the client renders no such line, so there is nothing to put here. The
shared sentence builder exists in the schema module if a future screen wants one,
rather than a field no consumer asked for.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import inspect

from app.api.deps import AuthenticatedUser, RecommendationServiceDep, RiskRepositoryDep
from app.core.deps import require_permission
from app.core.exceptions import NotFoundError
from app.core.permissions import Permission
from app.models.enums import RecommendationStatus, RecommendationType
from app.models.risk import Recommendation
from app.schemas.recommendation import RecommendationListRead, RecommendationRead

__all__ = ["router"]

router = APIRouter(prefix="/recommendations", tags=["recommendations"])

#: The page size a caller gets when it does not ask for one.
DEFAULT_PAGE_SIZE = 20

#: The largest page any caller may ask for. A ceiling rather than a default, and a
#: **rejection rather than a silent truncation** for the reason
#: ``docs/api-conventions.md`` §Pagination gives: a caller that asked for 500 and
#: received 100 cannot tell a truncated page from a short one.
MAX_PAGE_SIZE = 100

#: Applied to every route, including the four lifecycle writes, on the same
#: reasoning as :mod:`app.api.v1.risks`: answering a suggestion is a fact about
#: the caller's own work, and a second ``recommendations.write`` would be granted
#: to exactly the roles that already hold this one.
_ANALYTICS_READ = [Depends(require_permission(Permission.ANALYTICS_READ))]

#: One message for an id that is not the caller's and for an id that was never
#: issued, for the reason :mod:`app.api.v1.risks` gives: the two must not be
#: separable by a caller probing for ids.
_RECOMMENDATION_NOT_FOUND = "That recommendation does not exist."


def _columns(row: Any) -> dict[str, Any]:
    """Project a stored row onto the mapping its wire model validates from.

    ``model_validate(row, from_attributes=True)`` is not used for
    :class:`~app.schemas.recommendation.RecommendationRead`: the model declares
    ``metadata`` as ``AliasChoices("metadata_", "metadata")`` to cope with the
    name SQLAlchemy reserves on a declarative class, and under
    ``from_attributes`` pydantic resolves the first choice with ``getattr`` —
    which finds the column attribute today, but would find SQLAlchemy's own
    ``MetaData`` object if the alias order were ever the other way round.
    Reading the mapper's column attributes by name produces the stored mapping
    under the column's real attribute regardless of the alias order, and does so
    without this function needing to know the column list. The same reasoning
    and the same helper shape as :func:`app.api.v1.risks._columns`.
    """
    return {column.key: getattr(row, column.key) for column in inspect(type(row)).column_attrs}


def _recommendation_read(row: Recommendation) -> RecommendationRead:
    """Build the wire model for one suggestion.

    ``metadata`` is passed under the wire name explicitly, for the reason
    :func:`_columns` gives. ``reason`` is not defaulted here:
    :class:`RecommendationRead` refuses a blank one at validation, which is the
    only enforcement that survives a rule written next year — an imperative with
    nothing behind it renders perfectly well on a card.
    """
    return RecommendationRead.model_validate({**_columns(row), "metadata": row.metadata_})


@router.get(
    "",
    response_model=RecommendationListRead,
    summary="The caller's suggestions, most urgent first",
    dependencies=_ANALYTICS_READ,
)
async def list_recommendations(
    current_user: AuthenticatedUser,
    risks: RiskRepositoryDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
    recommendation_status: Annotated[RecommendationStatus | None, Query(alias="status")] = None,
    recommendation_type: Annotated[RecommendationType | None, Query()] = None,
) -> RecommendationListRead:
    """One page of suggestions, ordered by priority and then by age.

    **Ordering is the repository's.** ``critical`` does not sort alphabetically
    above ``high`` in any useful sense — the priority words are ranked through a
    ``CASE`` built from the enum — so a client sorting the page itself would have
    to reimplement that ladder.

    ``status`` and ``recommendation_type`` are single-valued and validated against
    the enums, so an unknown word is a 422 rather than a filter that quietly
    matches nothing. The screen most callers ask for passes ``status=new``: the
    open set, because a suggestion the user has already answered is history and
    listing it beside the unanswered ones is how a to-do list stops being a to-do
    list.

    ``by_priority`` counts across every matching row rather than this page, and
    always carries all four bands, so the response shape does not change as the
    last open suggestion is closed. It is filtered by exactly the ``status`` and
    ``recommendation_type`` the items were read with, so the tally beside
    ``total`` always describes the set the caller is looking at rather than a
    wider one.
    """
    statuses = [recommendation_status.value] if recommendation_status is not None else None
    types = [recommendation_type.value] if recommendation_type is not None else None
    rows, total = await risks.list_recommendations(
        current_user.id, statuses=statuses, types=types, limit=limit, offset=offset
    )
    by_priority = await risks.count_by_priority(current_user.id, statuses=statuses, types=types)
    return RecommendationListRead(
        items=[_recommendation_read(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
        by_priority=by_priority,
    )


@router.get(
    "/{recommendation_id}",
    response_model=RecommendationRead,
    summary="One suggestion, with the reason behind it",
    dependencies=_ANALYTICS_READ,
)
async def get_recommendation(
    current_user: AuthenticatedUser,
    risks: RiskRepositoryDep,
    recommendation_id: uuid.UUID,
) -> RecommendationRead:
    """Return a single suggestion.

    **404 for another account's suggestion, never 403** — the row is resolved
    through an owner-scoped lookup and never loaded if it is not the caller's.

    The ``reason`` is always present, and the schema refuses to serialise a blank
    one. That is the whole point of the rule table's "WHY" column: the imperative
    on its own is the failure the brief rules out, and it renders well enough that
    only an enforced field catches it.
    """
    row = await risks.get_recommendation(current_user.id, recommendation_id)
    if row is None:
        raise NotFoundError(_RECOMMENDATION_NOT_FOUND)
    return _recommendation_read(row)


@router.post(
    "/{recommendation_id}/accept",
    response_model=RecommendationRead,
    summary="'I will do this'",
    dependencies=_ANALYTICS_READ,
)
async def accept_recommendation(
    current_user: AuthenticatedUser,
    recommendations: RecommendationServiceDep,
    recommendation_id: uuid.UUID,
) -> RecommendationRead:
    """Record that the user accepted a suggestion, and return the updated row.

    **Accepting changes nothing about the work.** NEXUS does not reschedule the
    task, block the time or move the deadline the suggestion names; the endpoint
    records the decision and stamps ``responded_at``, and the user acts. The
    distinction is structural — every ``RecommendationType`` names something a
    person does and none of them is an operation this application performs.

    Accepting is not completing. "I will do this" and "I did this" are two
    events, and a recommendation may be completed only after it has been
    accepted.

    Errors: 404 for an id that is not the caller's, 409 for one already rejected
    or expired.
    """
    row = await recommendations.accept(owner=current_user, recommendation_id=recommendation_id)
    return _recommendation_read(row)


@router.post(
    "/{recommendation_id}/reject",
    response_model=RecommendationRead,
    summary="'Not for me'",
    dependencies=_ANALYTICS_READ,
)
async def reject_recommendation(
    current_user: AuthenticatedUser,
    recommendations: RecommendationServiceDep,
    recommendation_id: uuid.UUID,
) -> RecommendationRead:
    """Record that the user declined a suggestion, and return the updated row.

    **A rejection is a legitimate answer, not a dismissal of the risk.** The
    underlying condition may still be true and is still tracked; what changed is
    that this suggestion was not wanted. Rejection is terminal for the suggestion
    but not for the risk, and an identical suggestion can be raised again later
    if the situation still calls for it — which is the difference between a
    rejected suggestion and one the user simply never opened, and why the
    deduplicating lookup treats ``new``/``viewed`` as the open set rather than
    every non-terminal status.

    Errors: 404 for an id that is not the caller's, 409 for one already rejected
    or expired.
    """
    row = await recommendations.reject(owner=current_user, recommendation_id=recommendation_id)
    return _recommendation_read(row)


@router.post(
    "/{recommendation_id}/complete",
    response_model=RecommendationRead,
    summary="'Done'",
    dependencies=_ANALYTICS_READ,
)
async def complete_recommendation(
    current_user: AuthenticatedUser,
    recommendations: RecommendationServiceDep,
    recommendation_id: uuid.UUID,
) -> RecommendationRead:
    """Record that the user carried a suggestion out, and return the updated row.

    The terminal state of a suggestion the user acted on, and the training label
    Phase 10 is described as wanting alongside the rejection. As with accepting,
    nothing about the underlying work is changed here — completing a suggestion
    does not complete a task, and the pairing a client would want is a second
    request of its own.

    Completing without having accepted first is refused by the lifecycle: "done"
    for something that was never agreed is not a state the engine will record.

    Errors: 404 for an id that is not the caller's, 409 for one not accepted, or
    already rejected or expired.
    """
    row = await recommendations.complete(owner=current_user, recommendation_id=recommendation_id)
    return _recommendation_read(row)


@router.post(
    "/{recommendation_id}/view",
    response_model=RecommendationRead,
    summary="'I have seen this'",
    dependencies=_ANALYTICS_READ,
)
async def view_recommendation(
    current_user: AuthenticatedUser,
    recommendations: RecommendationServiceDep,
    recommendation_id: uuid.UUID,
) -> RecommendationRead:
    """Record that the user opened a suggestion, and return the updated row.

    **Viewing is not answering.** It is kept as its own route because "the user
    has seen this" and "the user has decided about this" are different
    observations, and collapsing them would make "how many suggestions were never
    answered" unanswerable — which is the number a model trained on acceptance
    would want most.

    It does not stamp ``responded_at``: a suggestion being opened is not a
    decision about it, and the column is the evidence that there was one.

    Errors: 404 for an id that is not the caller's, 409 for one already rejected
    or expired.
    """
    row = await recommendations.view(owner=current_user, recommendation_id=recommendation_id)
    return _recommendation_read(row)
