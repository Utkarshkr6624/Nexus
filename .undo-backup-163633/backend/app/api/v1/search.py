"""The global search endpoint: one query across every table the caller owns.

One route
---------
``GET /api/v1/search`` is the only declaration in this file, and the count is the
design. Nine surfaces already have their own ``?search=`` — projects, tasks,
notes, concepts, resources — and each of those answers "filter *this* list".
This one answers "find the thing I am thinking of", which none of them can: the
thing a user remembers is usually not filed under the surface they would go
looking for it on. Splitting it per kind would recreate the problem it exists to
solve, and would make "the hit I saw has gone" a per-surface bug report.

Where the work happens
----------------------
Not here. This router resolves the caller, validates the query string, hands it to
:class:`~app.services.search_service.SearchService` and returns the answer. The
service ranks, trims and groups; :class:`~app.repositories.search.SearchRepository`
runs one bounded statement per table. The only judgement this file makes is
which HTTP answer to give, and it is the one the whole surface rests on: **the
caller is never taken from the request.**

Tenancy
-------
There is no ``user_id`` parameter, and there is nothing in the body to put one
in. The owner comes from the bearer token, is passed to the service as
``owner=``, and lands in the ``WHERE`` clause of every statement the repository
builds — including the one that resolves project names. A caller who knows
another account's task id therefore cannot make it appear, because the predicate
is on the query rather than on the filter, and no filter here narrows *past* it.

Permission
----------
``analytics.read``, and the same reuse ``app/api/v1/ml.py`` documents. Phase 13
adds a surface that spans projects, tasks, the knowledge base, the calendar, the
developer and learning stores, and analytics — there is no existing capability
that is exactly "read across all of them". Coining one would mean editing
``Permission``, and ``tests/test_permissions.py`` pins the member set as a
literal, so a new member is a permission *grant* decision as much as a code
change and would name a capability granted to exactly the roles
``analytics.read`` already names.

Pagination
----------
``limit``/``offset`` slice the **flat ranked union**, and ``groups`` partition
that same page — not each kind independently. The reasoning is in
:mod:`app.services.search_service`: per-kind pages would make ``meta.total``
depend on how many kinds were asked for, and would make "page 2" mean something
different from page 1 whenever the union's shape changed.

``meta.total`` is the number of hits *discovered* within the per-kind scan caps
(:data:`~app.services.search_service.SearchService.PER_ENTITY_SCAN`), not the
number of rows in the database that match. That is stated on the response model
rather than left to be inferred from a total that stops growing.

Route order
-----------
Nothing here is parameterised — there is no ``/search/{anything}`` — so no
literal route can be shadowed by one, and the single declaration is ordered
against the rest of the v1 aggregate for consistency with the load-bearing rule
``app/api/v1/risks.py`` spells out.

What it covers, and what it does not
------------------------------------
**Everything a caller owns that carries text they might remember.** Thirteen
kinds, listed in :class:`app.schemas.search.SearchEntityKind`. ``bookmarks``
and ``documents`` are in it for the reason their own surfaces' ``?search=``
made the alternative indefensible: each of them was findable from the screen
that listed it and unfindable from the screen that exists to find things, and a
saved link a user cannot search for is a link they have lost.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.deps import AuthenticatedUser
from app.core.deps import DbSession, require_permission
from app.core.permissions import Permission
from app.repositories.search import SearchRepository
from app.schemas.search import (
    MAX_QUERY_CHARS,
    MAX_TYPE_FILTERS,
    MIN_QUERY_CHARS,
    SearchEntityKind,
    SearchResponse,
)
from app.services.search_service import SearchService

router = APIRouter(prefix="/search", tags=["search"])

#: The page size a caller gets when it does not ask for one. The house default
#: from ``app/schemas/common``'s neighbours: a search result is something a
#: person reads, and fifty is more rows than any palette in the product renders.
DEFAULT_PAGE_SIZE = 50

#: The largest page any caller may ask for. A rejection rather than a silent
#: clamp, for the reason every other list in this API gives: a client that asked
#: for 5000 and received 200 cannot tell a capped page from a short one.
MAX_PAGE_SIZE = 200


def get_search_service(session: DbSession) -> SearchService:
    """Build the request-scoped search service.

    **One repository, because one repository is enough.** Every other service in
    :mod:`app.api.deps` takes a pile of them because its rules genuinely read
    several tables; this one reads many tables but touches no business rule, and
    the repository that knows how to scan them is the same object for all of
    them. Wiring eleven repositories here would be eleven collaborators that are
    passed straight through and used to answer nothing.

    The dependency lives in this module rather than in :mod:`app.api.deps`
    because it is the only service in the application that no other router needs;
    putting it in the shared module would make every future consumer import a
    wiring it has no reason to know about.
    """
    return SearchService(SearchRepository(session))


SearchServiceDep = Annotated[SearchService, Depends(get_search_service)]


@router.get(
    "",
    response_model=SearchResponse,
    summary="Search every record the caller owns",
    description=(
        "One cross-entity substring search over projects, tasks, notes, resources, "
        "bookmarks, documents, concepts, repositories, learning goals, skills, "
        "calendar events, risks and recommendations. Returns a flat ranked list and "
        "the same hits grouped by kind. Results are always the caller's own."
    ),
    dependencies=[Depends(require_permission(Permission.ANALYTICS_READ))],
)
async def search_everything(
    current_user: AuthenticatedUser,
    service: SearchServiceDep,
    q: Annotated[
        str,
        Query(
            min_length=MIN_QUERY_CHARS,
            max_length=MAX_QUERY_CHARS,
            description="Free text, matched case-insensitively as a substring.",
        ),
    ],
    types: Annotated[
        list[SearchEntityKind] | None,
        Query(
            max_length=MAX_TYPE_FILTERS,
            description="Restrict the search to these entity kinds. All of them by default.",
        ),
    ] = None,
    project_id: Annotated[
        UUID | None,
        Query(description="Only rows filed under this project. 422 for kinds that have none."),
    ] = None,
    status: Annotated[
        str | None,
        Query(description="One status value, applied to every searched kind that has one."),
    ] = None,
    priority: Annotated[
        str | None,
        Query(description="One priority value. `risks` carry a severity, not a priority."),
    ] = None,
    date_from: Annotated[
        date | None,
        Query(alias="from", description="Inclusive lower bound on each kind's own date column."),
    ] = None,
    date_to: Annotated[
        date | None,
        Query(alias="to", description="Inclusive upper bound; covers the whole named day."),
    ] = None,
    tag_ids: Annotated[
        list[UUID] | None,
        Query(description="Tasks carrying **every** listed tag. 422 for other kinds."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SearchResponse:
    """Search the caller's records and return the ranked page.

    **The scope is the caller's and only the caller's.** There is no ``user_id``
    parameter, and every statement carries the caller's own id in its ``WHERE``
    clause — so a search can never surface another account's row even for a
    caller who knows its exact id. That is a 404-shaped answer in a read: the
    row is not filtered out after the fact, it is never loaded.

    **No result is a 200, not an error.** A query matching nothing returns two
    empty lists and a ``total`` of zero, because "nothing matched" is a complete
    and common answer to a search and a caller must not have to tell it apart
    from a fault.

    **Ordering is deterministic.** Hits are ordered by which column matched (a
    title before a body), then most recently updated, then id. The same query
    over the same data returns the same list in the same order every time, so a
    palette that re-reads on a keystroke does not reshuffle under the cursor.

    **Filters narrow; they never widen, and never silently do nothing.**
    ``project_id`` against a kind with no project column is a 422 rather than an
    ignored parameter, because an ignored filter returns a page that looks
    filtered and is not.

    Errors: 401 unauthenticated, 403 without ``analytics.read``, 422 for an empty
    or over-long ``q``, an unknown entity kind in ``types``, a ``limit`` outside
    1-200, a negative ``offset``, a reversed ``from``/``to`` range, a status or
    priority outside the searched kinds' vocabularies, or a filter naming a
    capability the searched kinds do not have.
    """
    return await service.search(
        owner=current_user.id,
        term=q,
        kinds=types,
        project_id=project_id,
        status=status,
        priority=priority,
        date_from=date_from,
        date_to=date_to,
        tag_ids=tag_ids or (),
        limit=limit,
        offset=offset,
        max_page_size=MAX_PAGE_SIZE,
    )


#: The router only; the handlers are reached through it, not imported directly.
__all__ = ["router"]
