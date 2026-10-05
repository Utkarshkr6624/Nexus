"""Knowledge-base endpoints: the caller's notes, concepts and the rest, plus the links and graph.

Where the rules live
--------------------
A translation layer, exactly as :mod:`app.api.v1.projects` and
:mod:`app.api.v1.tags` are. Every question this file could answer — *is that
note the caller's, may this link be written, does that category cycle, is that
URL safe* — is answered by
:class:`~app.services.knowledge_service.KnowledgeService`. This router imports no
repository, raises no domain error, and lets
:func:`app.core.exceptions.install_exception_handlers` turn the service's
``NotFoundError`` / ``ConflictError`` / ``ValidationError`` into the shared
envelope. A second error body built here would be a second, drifting
implementation of the same contract.

Tenancy, and the one route where it is harder
---------------------------------------------
**No route here accepts a user id from the request.** The caller comes from the
bearer token, is passed to the service as ``owner=``, and every ``{id}`` is
resolved through a lookup scoped by ``owner_id`` before it is used, so another
account's id answers **404 and not 403** — identical to an id that never
existed.

The link routes are the hard case, and they are the reason that sentence needs
its own paragraph. ``knowledge_links`` is polymorphic: ``source_type`` chooses
which table ``source_id`` points at, so neither endpoint column can carry a
foreign key, and ``GET /knowledge/links`` takes **neither id in the path** — they
are query parameters. Nothing about the request therefore forces the service to
look at ``owner_id`` at all, and the *only* thing standing between
``GET /knowledge/links?source_type=note&source_id=<someone else's note>`` and a
cross-tenant read is that ``KnowledgeService`` resolves the endpoint through an
owner-scoped query before it lists. That is the security model of this table and
it lives in the service, not here; :attr:`app.models.enums.KnowledgeEntityType`
says so at length. This router's part of the job is to pass the parameters
through unfiltered rather than to pre-empt it.

Lifecycle as endpoints, not a field
-----------------------------------
``status`` is not on any ``*Update`` payload and every schema here is
``extra="forbid"``, so posting one is a 422 naming the field. That mirrors
``/projects``: reaching ``published`` writes an event, and a blanket PATCH able
to set the column would let a client assert a state without performing the act
that earns it. ``/publish``, ``/archive`` and ``/restore`` are the only doors, and
they are the only places the legal transitions live.

Revisions are copies, not diffs
-------------------------------
``note_revisions`` stores a point-in-time **copy** of title, content and
summary, so restoring one is an assignment rather than a three-way merge. The
trade is storage — a note edited often keeps several full copies — bought for
correctness: a diff format would make "restore what I had yesterday" depend on
replaying every intermediate edit correctly. ``POST
/knowledge/notes/{id}/restore-revision/{revision_id}`` writes a *new* revision
before it overwrites the note, so a restore is itself undoable and history grows
rather than being consumed.

One route for both link directions
----------------------------------
``GET /knowledge/links`` serves outbound edges (``source_type``/``source_id``) and
backlinks (``target_type``/``target_id``) rather than being split into two routes.
Both are *the same two columns read in a different order* — one route over
``knowledge_links`` is one query shape, one pagination contract and one place to
enforce that the endpoint is the caller's, and a caller asking for a node's
"related" view wants both halves from the same code path. Exactly one complete
pair must be supplied; a request naming neither, one half, or both is a 422
raised by the service.

The graph is capped, and the cap is the feature
-----------------------------------------------
``GET /knowledge/graph`` takes a ``limit`` and rejects anything above
:data:`MAX_GRAPH_LIMIT` rather than truncating. The spec calls out "a graph that
looks impressive but becomes unusable" as the failure mode, and a force-directed
layout is exactly where that shows up: a few hundred nodes is already a hairball
the user cannot follow, so returning more is not generosity, it is returning
something nobody can read. The service enforces the same ceiling independently
(:data:`~app.services.knowledge_service.KnowledgeService.MAX_GRAPH_NODES`) so a
non-HTTP caller is bounded too — **for an HTTP request the router's 422 wins**,
because a client that asked for 5000 and got 500 back could not tell the
difference between a cap and a small graph. The client-side answer to a large
graph is ``entity_type`` and paging, not a bigger limit.

Search is bounded in SQL, and portable
--------------------------------------
One ``ILIKE`` query per entity type, each with its own ``LIMIT``; no
``SELECT *`` is pulled into memory and filtered in Python. This PostgreSQL build
has **no ``pg_trgm`` and no ``unaccent``**, so neither is used — ``ILIKE`` is
portable across the two, which is what a local-first product that may be pointed
at an arbitrary server needs. The cost is that the scan is a sequential one and
gets slower as a user's notes grow; a real deployment adds GIN trigram indexes on
the same columns, which this schema leaves room for without a migration to the
columns themselves.

Sorting
-------
Every list takes ``sort`` and ``order``, and **neither is interpolated here**.
``ORDER BY`` takes an expression rather than a bound parameter, so a
caller-supplied column name is an injection point; the service resolves the name
against an allowlist and raises ``ValidationError`` before a statement is built,
and the repository resolves it a second time before interpolating. The
authoritative set is ``KnowledgeService._SORT_KEYS`` per entity. A bogus ``sort``
is a 422, never a 500.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status

from app.api.deps import AuthenticatedUser, KnowledgeServiceDep
from app.core.deps import require_permission
from app.core.exceptions import ValidationError
from app.core.permissions import Permission
from app.models.enums import KnowledgeEntityType, KnowledgeLinkType, NoteStatus, ResourceType
from app.models.knowledge import (
    Bookmark,
    Category,
    Concept,
    Document,
    KnowledgeLink,
    Note,
    Resource,
)
from app.schemas.common import Page
from app.schemas.knowledge import (
    BookmarkCreate,
    BookmarkRead,
    BookmarkUpdate,
    CategoryCreate,
    CategoryRead,
    CategoryUpdate,
    ConceptCreate,
    ConceptRead,
    ConceptUpdate,
    DocumentCreate,
    DocumentRead,
    DocumentUpdate,
    KnowledgeGraph,
    KnowledgeLinkCreate,
    KnowledgeLinkRead,
    KnowledgeSearchKind,
    KnowledgeSearchResult,
    NoteCreate,
    NoteRead,
    NoteRevisionRead,
    NoteUpdate,
    ResourceCreate,
    ResourceRead,
    ResourceUpdate,
)

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

#: The page size a caller gets when it does not ask for one. Matches the
#: project/task pages: a knowledge list is a scannable index, not a feed, and the
#: client pages the rest.
DEFAULT_PAGE_SIZE = 20

#: The largest page any collection here may ask for. 100 is a ceiling, not a
#: default: it is more than any knowledge screen in the product renders, so a
#: caller reaching it is a script exporting data, and such a caller is served by
#: walking ``offset``.
MAX_PAGE_SIZE = 100

#: The longest search term accepted. A ``q`` longer than this is a paste of a
#: document, not a query, and every row matches it — which makes the request
#: both useless and the most expensive one the endpoint accepts. Rejected with a
#: 422 rather than silently truncated, for the same reason the page cap is.
MAX_SEARCH_LENGTH = 200

#: How many rows **per entity type** a search returns. The result is grouped, so
#: this is not a total: asking for 20 asks for up to 20 notes *and* 20 concepts,
#: each from its own bounded query. Capped like every other collection, and a
#: rejection rather than a clamp.
MAX_SEARCH_LIMIT = 20

#: How many nodes the graph returns when the caller does not say. 200 is already
#: at the edge of what a force layout stays readable at; it is a default, not a
#: promise that 200 renders.
DEFAULT_GRAPH_LIMIT = 200

#: The hard ceiling on graph nodes. **The cap is the feature**, not a limitation
#: of it — see this module's docstring. Past this the layout stops carrying
#: information, so a request above it is a 422 telling the client to narrow by
#: ``entity_type`` rather than a truncated graph wearing a full one.
MAX_GRAPH_LIMIT = 500

#: The sort names each list advertises. Not enforced here: the service owns the
#: allowlist and the repository re-checks it before interpolating, so duplicating
#: the set would add a third place to keep in step for no security gained.
_SORT_DESCRIPTION = "Sort key; one of the names the service allowlists."


def _only(*allowed: str) -> Any:
    """Build a dependency refusing any query parameter this route does not have.

    **A filter that does nothing is worse than no filter, and FastAPI answers an
    unknown query parameter with a cheerful 200.** Every ``list_*`` route below
    declares its parameters explicitly, so ``GET /knowledge/bookmarks?archived=true``
    used to come back 200 with the ordinary unarchived page and ``meta.total: 4``:
    the caller had asked for a view that does not exist, been given the default
    one, and had no way to tell that the request they made and the answer they got
    were about different things. On the Resources tab the same shape produced a
    "Clear filters" chip over an unfiltered list, which is a filter that reports
    itself as active while doing nothing.

    So an unrecognised parameter is a **422 naming it**, the same way a body field
    this router does not accept is a 422 naming that. The refusal is the shared
    domain error the exception handlers already translate, so the envelope, the
    error code and the request id are the ones every other 422 on this surface
    carries — this is the one place the router raises rather than translating, and
    it is listed in :func:`_only` rather than added to the module's "raises no
    domain error" list because the alternative was an HTTPException with a second,
    drifting error body.

    The allowlist is written out per route because a route's parameters are its
    own; a derived list would have to be introspected out of the signature at
    runtime, which is cleverer than a list that a reader can check in one glance.

    Args:
        *allowed: The query parameter names the route advertises.

    Returns:
        A dependency callable for a route's ``dependencies=[...]``.
    """
    permitted = frozenset(allowed)

    async def _guard(request: Request) -> None:
        unknown = sorted(set(request.query_params) - permitted)
        if unknown:
            raise ValidationError(
                f"Unknown query parameter{'s' if len(unknown) > 1 else ''}: {', '.join(unknown)}.",
                details={"fields": unknown, "allowed": sorted(permitted)},
            )

    return _guard


# --------------------------------------------------------------------------- #
# Notes
# --------------------------------------------------------------------------- #


@router.get(
    "/notes",
    response_model=Page[NoteRead],
    summary="List the caller's notes",
    dependencies=[
        Depends(require_permission(Permission.KNOWLEDGE_READ)),
        Depends(_only("limit", "offset", "status", "search", "sort", "order")),
    ],
)
async def list_notes(
    current_user: AuthenticatedUser,
    knowledge: KnowledgeServiceDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
    note_status: Annotated[
        NoteStatus | None,
        Query(alias="status", description="Restrict to one lifecycle state."),
    ] = None,
    search: Annotated[
        str | None,
        Query(description="Case-insensitive substring of the title, content or summary."),
    ] = None,
    sort: Annotated[str, Query(description=_SORT_DESCRIPTION)] = "updated_at",
    order: Annotated[str, Query(description="'asc' or 'desc'.")] = "desc",
) -> Page[NoteRead]:
    """List notes as one page of a filtered, sorted, owner-scoped sequence.

    **The scope is the caller's and only the caller's.** There is no ``owner_id``
    parameter and no way to ask for somebody else's page: a filter narrows the
    caller's own notes, it cannot widen them. ``meta.total`` is the size of the
    filtered set, from a ``COUNT`` over the same statement the rows were drawn
    from, so it describes the sequence rather than the slice.

    **Unknown ``sort`` or ``order`` is a 422**, raised by the service against its
    allowlist before any SQL is built. A sort name is not sanitised, it is
    resolved against a fixed set of columns — which is what stops ``?sort=`` from
    being an injection point into ``ORDER BY``.

    Errors: 422 for an unknown status, sort key or sort direction, or a ``limit``
    outside 1-100.
    """
    return await knowledge.list_notes(
        owner=current_user,
        limit=limit,
        offset=offset,
        status=note_status,
        search=search,
        sort=sort,
        order=order,
    )


@router.post(
    "/notes",
    response_model=NoteRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a note",
    dependencies=[Depends(require_permission(Permission.KNOWLEDGE_WRITE))],
)
async def create_note(
    payload: NoteCreate,
    current_user: AuthenticatedUser,
    knowledge: KnowledgeServiceDep,
) -> Note:
    """Open a new note, always ``draft``.

    **The owner is the caller and cannot be named.** ``owner_id`` comes from the
    bearer token; a create endpoint that let the body choose its owner would hand
    any authenticated user the ability to file knowledge into another account.

    A note is always created ``draft``. There is no path through which a note
    comes into existence already published, so "this is real now" is always an
    act somebody performed and the ``NOTE_PUBLISHED`` event always describes it.

    Errors: 422 for a blank or over-long title, or a ``document_id`` that is not
    the caller's.
    """
    return await knowledge.create_note(owner=current_user, data=payload)


@router.get(
    "/notes/{note_id}",
    response_model=NoteRead,
    summary="Fetch one note",
    dependencies=[Depends(require_permission(Permission.KNOWLEDGE_READ))],
)
async def get_note(
    note_id: UUID,
    current_user: AuthenticatedUser,
    knowledge: KnowledgeServiceDep,
) -> Note:
    """Return one of the caller's notes.

    **404 for another account's note, never 403** — the id is resolved through a
    lookup scoped by ``owner_id``, so the row is never loaded and the refusal is
    the one a nonexistent id gets. See this module's docstring for why that
    distinction is load-bearing.

    Errors: 404 when the caller owns no note with this id.
    """
    return await knowledge.get_note(note_id=note_id, owner=current_user)


@router.patch(
    "/notes/{note_id}",
    response_model=NoteRead,
    summary="Edit a note",
    dependencies=[Depends(require_permission(Permission.KNOWLEDGE_WRITE))],
)
async def update_note(
    note_id: UUID,
    payload: NoteUpdate,
    current_user: AuthenticatedUser,
    knowledge: KnowledgeServiceDep,
) -> Note:
    """Apply a partial update to a note the caller owns.

    **A field is written if the client named it** — clearing a ``summary`` is
    ``"summary": null``, not an absent key.

    ``status`` is not on this payload and ``extra="forbid"`` makes sending it a
    422 naming the field: the lifecycle has its own endpoints because every edge
    of it carries a rule and records an event. ``owner_id`` is likewise not
    writable — a PATCH able to set it would be a way to hand a note to another
    account, and the repository's ``update_fields`` allowlist refuses it a second
    time below this layer.

    **An edit that changes title, content or summary writes a revision** holding
    the *previous* values, so history is preserved by the write that displaced it
    rather than by a separate save button the user has to remember.

    Errors: 404 for a note that is not the caller's; 422 for a blank or over-long
    title, or a field the payload does not accept.
    """
    note = await knowledge.get_note(note_id=note_id, owner=current_user)
    return await knowledge.update_note(note=note, data=payload, owner=current_user)


@router.delete(
    "/notes/{note_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete a note",
    dependencies=[Depends(require_permission(Permission.KNOWLEDGE_WRITE))],
)
async def delete_note(
    note_id: UUID,
    current_user: AuthenticatedUser,
    knowledge: KnowledgeServiceDep,
) -> Response:
    """Delete one of the caller's notes.

    **A hard delete, and the intended product answer is still ``/archive``.**
    Archiving is the terminal state a note is meant to reach; this is the
    explicit, destructive, user-initiated path for one that should not have been
    created. Both exist because a local-first product that cannot delete its own
    data is lying to its user.

    Revisions cascade with the note — a revision whose note is gone describes
    nothing. Activity rows do **not**: ``activity_events`` uses
    ``ON DELETE SET NULL`` precisely so the record of what happened outlives its
    subject with a null reference.

    Edges in ``knowledge_links`` are not cascaded, because the table has no
    foreign key to cascade from (see this module's docstring): the service
    removes the caller's edges touching the note in the same transaction, so a
    deleted note cannot leave a dangling edge the graph would still try to draw.

    Errors: 404 for a note that is not the caller's, or that does not exist.
    """
    note = await knowledge.get_note(note_id=note_id, owner=current_user)
    await knowledge.delete_note(note=note, owner=current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/notes/{note_id}/publish",
    response_model=NoteRead,
    summary="Publish a note",
    dependencies=[Depends(require_permission(Permission.KNOWLEDGE_WRITE))],
)
async def publish_note(
    note_id: UUID,
    current_user: AuthenticatedUser,
    knowledge: KnowledgeServiceDep,
) -> Note:
    """Assert that a note is real now.

    ``published`` is the only state a link target is *meant* to point at, and
    the UI filters to it by default. Publishing an already-published note is a
    no-op rather than an error, so a retried click gets the note it asked for.

    Errors: 404 for a note that is not the caller's.
    """
    note = await knowledge.get_note(note_id=note_id, owner=current_user)
    return await knowledge.publish_note(note=note, owner=current_user)


@router.post(
    "/notes/{note_id}/archive",
    response_model=NoteRead,
    summary="Archive a note",
    dependencies=[Depends(require_permission(Permission.KNOWLEDGE_WRITE))],
)
async def archive_note(
    note_id: UUID,
    current_user: AuthenticatedUser,
    knowledge: KnowledgeServiceDep,
) -> Note:
    """Set a note aside, keeping it, its revisions and its edges.

    Reachable only through this route, never a PATCH, so the archived set cannot
    be left by a generic edit. Archiving an archived note is a no-op.

    Errors: 404 for a note that is not the caller's.
    """
    note = await knowledge.get_note(note_id=note_id, owner=current_user)
    return await knowledge.archive_note(note=note, owner=current_user)


@router.post(
    "/notes/{note_id}/restore",
    response_model=NoteRead,
    summary="Restore an archived note",
    dependencies=[Depends(require_permission(Permission.KNOWLEDGE_WRITE))],
)
async def restore_note(
    note_id: UUID,
    current_user: AuthenticatedUser,
    knowledge: KnowledgeServiceDep,
) -> Note:
    """Return an archived note to the working set as a ``draft``.

    **It comes back ``draft``, and that is a deliberate answer rather than a
    lossy one.** The note was archived at some point after it may have been
    published, and re-asserting "this is real now" on the user's behalf would
    claim an act they did not perform; ``draft`` is the honest state for content
    that has been set aside and is being looked at again. Publishing it is one
    click away and is the only way to get back there.

    Restoring anything that is not archived is a no-op.

    Errors: 404 for a note that is not the caller's.
    """
    note = await knowledge.get_note(note_id=note_id, owner=current_user)
    return await knowledge.restore_note(note=note, owner=current_user)


@router.get(
    "/notes/{note_id}/revisions",
    response_model=Page[NoteRevisionRead],
    summary="A note's revision history",
    dependencies=[
        Depends(require_permission(Permission.KNOWLEDGE_READ)),
        Depends(_only("limit", "offset")),
    ],
)
async def list_note_revisions(
    note_id: UUID,
    current_user: AuthenticatedUser,
    knowledge: KnowledgeServiceDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[NoteRevisionRead]:
    """Return what a note looked like at earlier points, newest first.

    **The note is resolved before its history is read**, so asking for the
    revisions of a note that is not the caller's is a 404 rather than an empty
    page that quietly confirms the note exists. Each revision is scoped to the
    note in the query, so a caller cannot pair their note with somebody else's
    revision id and get a row.

    Revisions are **point-in-time copies, not diffs** — see this module's
    docstring for why, and for what that means for storage.

    Errors: 404 for a note that is not the caller's; 422 for a ``limit`` outside
    1-100.
    """
    note = await knowledge.get_note(note_id=note_id, owner=current_user)
    return await knowledge.list_revisions(note=note, limit=limit, offset=offset)


@router.get(
    "/notes/{note_id}/revisions/{revision_id}",
    response_model=NoteRevisionRead,
    summary="Fetch one revision",
    dependencies=[Depends(require_permission(Permission.KNOWLEDGE_READ))],
)
async def get_note_revision(
    note_id: UUID,
    revision_id: UUID,
    current_user: AuthenticatedUser,
    knowledge: KnowledgeServiceDep,
) -> Note:
    """Return one revision of one of the caller's notes.

    Both ids are resolved, not one. A caller who owns a note but supplies a
    revision belonging to a different note — or to another user's note — gets a
    404, because the lookup is ``note_id AND revision_id AND owner_id``. Pairing
    the ids loosely would let a real revision id be confirmed against a note it
    does not belong to.

    Errors: 404 when the note is not the caller's, or the revision is not one of
    that note's.
    """
    note = await knowledge.get_note(note_id=note_id, owner=current_user)
    return await knowledge.get_revision(note=note, revision_id=revision_id, owner=current_user)


@router.post(
    "/notes/{note_id}/restore-revision/{revision_id}",
    response_model=NoteRead,
    summary="Roll a note back to an earlier revision",
    dependencies=[Depends(require_permission(Permission.KNOWLEDGE_WRITE))],
)
async def restore_note_revision(
    note_id: UUID,
    revision_id: UUID,
    current_user: AuthenticatedUser,
    knowledge: KnowledgeServiceDep,
) -> Note:
    """Put a note's title, content and summary back to what a revision holds.

    **The current state is written as a new revision first**, so a restore is
    itself undoable and the history only ever grows. Consuming the revision
    instead would make "I changed my mind" unrecoverable, which is the one thing
    a history feature exists to prevent.

    The note is **not** restored to the revision's ``status``. A revision is a
    copy of the *text*; reverting the lifecycle along with it would un-publish a
    note on the strength of an edit the user made after publishing, which is not
    a claim they ever made. Status moves only through ``/publish``,
    ``/archive`` and ``/restore``.

    Errors: 404 when the note is not the caller's or the revision is not one of
    that note's.
    """
    note = await knowledge.get_note(note_id=note_id, owner=current_user)
    return await knowledge.restore_revision(note=note, revision_id=revision_id, owner=current_user)


# --------------------------------------------------------------------------- #
# Concepts
# --------------------------------------------------------------------------- #


@router.get(
    "/concepts",
    response_model=Page[ConceptRead],
    summary="List the caller's concepts",
    dependencies=[
        Depends(require_permission(Permission.KNOWLEDGE_READ)),
        Depends(_only("limit", "offset", "search", "sort", "order")),
    ],
)
async def list_concepts(
    current_user: AuthenticatedUser,
    knowledge: KnowledgeServiceDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
    search: Annotated[
        str | None,
        Query(description="Case-insensitive substring of the name or description."),
    ] = None,
    sort: Annotated[str, Query(description=_SORT_DESCRIPTION)] = "name",
    order: Annotated[str, Query(description="'asc' or 'desc'.")] = "asc",
) -> Page[ConceptRead]:
    """List concepts, the vocabulary the graph's middle nodes hang off.

    **The scope is the caller's and only the caller's**; ``owner_id`` is in the
    query and there is no parameter that widens it. Names are unique *per user*,
    not globally, so two accounts may both hold "AVL Tree" without colliding.

    Defaults to ``name`` ascending rather than ``created_at`` descending: a
    concept list is an index a person scans alphabetically, not a feed.

    Errors: 422 for an unknown sort key or sort direction, or a ``limit``
    outside 1-100.
    """
    return await knowledge.list_concepts(
        owner=current_user,
        limit=limit,
        offset=offset,
        search=search,
        sort=sort,
        order=order,
    )


@router.post(
    "/concepts",
    response_model=ConceptRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a concept",
    dependencies=[Depends(require_permission(Permission.KNOWLEDGE_WRITE))],
)
async def create_concept(
    payload: ConceptCreate,
    current_user: AuthenticatedUser,
    knowledge: KnowledgeServiceDep,
) -> Concept:
    """Name a concept the caller owns.

    **The owner is the caller and cannot be named.** ``owner_id`` comes from the
    bearer token, and the uniqueness constraint is ``(owner_id, name)`` — so two
    accounts may both define "AVL Tree", which is the point of a shared vocabulary
    being personal.

    **A name this user already holds is a 409**, not a 500 and not a silent
    reuse of the existing row.

    Errors: 409 for a name this user already has; 422 for a blank or over-long
    name.
    """
    return await knowledge.create_concept(owner=current_user, data=payload)


@router.get(
    "/concepts/{concept_id}",
    response_model=ConceptRead,
    summary="Fetch one concept",
    dependencies=[Depends(require_permission(Permission.KNOWLEDGE_READ))],
)
async def get_concept(
    concept_id: UUID,
    current_user: AuthenticatedUser,
    knowledge: KnowledgeServiceDep,
) -> Concept:
    """Return one of the caller's concepts.

    **This route did not exist and its absence was a 405.** Notes, resources,
    bookmarks and documents could each be read by id; a concept could not, so a
    client holding a concept id — from a link, from a search result, from a note
    that points at one — had no way to ask for it and ``GET /concepts/{id}``
    answered *Method Not Allowed* rather than 404. Every other entity on this
    surface has the read, and a list is not a substitute for it: a client that has
    an id should not have to page the whole set to resolve it.

    **404 for another account's concept, never 403** — see :func:`get_note`.

    Errors: 404 when the caller owns no concept with this id.
    """
    return await knowledge.get_concept(concept_id=concept_id, owner=current_user)


@router.patch(
    "/concepts/{concept_id}",
    response_model=ConceptRead,
    summary="Edit a concept",
    dependencies=[Depends(require_permission(Permission.KNOWLEDGE_WRITE))],
)
async def update_concept(
    concept_id: UUID,
    payload: ConceptUpdate,
    current_user: AuthenticatedUser,
    knowledge: KnowledgeServiceDep,
) -> Concept:
    """Apply a partial update to a concept the caller owns.

    **404 for another account's concept, never 403** — the scoped lookup runs
    before the write, and the repository's ``update_fields`` allowlist refuses
    ``owner_id`` below this layer.

    Renaming to a name this user already holds is a 409; renaming to the name it
    already has is a no-op, so a retried rename is not a conflict with itself.

    ``tag_ids`` replaces the concept's tag set and every id in it must be a tag the
    caller owns; ``tag_ids: []`` clears them. It used to be a **500** — the key was
    handed to a column update that does not own it, and the concept rename dialog
    submits the field every time, so renaming a concept was impossible and the
    name just typed went with the dialog.

    Errors: 404 for a concept that is not the caller's; 409 for a name this user
    already has on a different concept; 422 for a blank or over-long name, a
    ``tag_ids`` entry the caller does not own, or a field the payload does not
    accept.
    """
    concept = await knowledge.get_concept(concept_id=concept_id, owner=current_user)
    return await knowledge.update_concept(concept=concept, data=payload, owner=current_user)


@router.delete(
    "/concepts/{concept_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete a concept",
    dependencies=[Depends(require_permission(Permission.KNOWLEDGE_WRITE))],
)
async def delete_concept(
    concept_id: UUID,
    current_user: AuthenticatedUser,
    knowledge: KnowledgeServiceDep,
) -> Response:
    """Delete one of the caller's concepts and the edges touching it.

    Edges are removed with it. ``knowledge_links`` has no foreign key to cascade
    from, so the service deletes the caller's edges where this concept is either
    endpoint in the same transaction — otherwise the graph would keep drawing
    edges into a node that no longer exists.

    Errors: 404 for a concept that is not the caller's, or that does not exist.
    """
    concept = await knowledge.get_concept(concept_id=concept_id, owner=current_user)
    await knowledge.delete_concept(concept=concept, owner=current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- #
# Resources
# --------------------------------------------------------------------------- #


@router.get(
    "/resources",
    response_model=Page[ResourceRead],
    summary="List the caller's resources",
    dependencies=[
        Depends(require_permission(Permission.KNOWLEDGE_READ)),
        Depends(_only("limit", "offset", "search", "sort", "order", "resource_type")),
    ],
)
async def list_resources(
    current_user: AuthenticatedUser,
    knowledge: KnowledgeServiceDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
    search: Annotated[
        str | None,
        Query(description="Case-insensitive substring of the title, description or url."),
    ] = None,
    resource_type: Annotated[
        ResourceType | None,
        Query(description="Restrict to one kind of external thing."),
    ] = None,
    sort: Annotated[str, Query(description=_SORT_DESCRIPTION)] = "updated_at",
    order: Annotated[str, Query(description="'asc' or 'desc'.")] = "desc",
) -> Page[ResourceRead]:
    """List the external things the caller has filed, newest activity first.

    **The scope is the caller's and only the caller's.** A resource is a pointer
    at something outside NEXUS, but the pointer itself is per-user and carries
    the owner's notes on it, so it is scoped exactly like a note.

    ``resource_type`` is a real filter and it is the one the Resources tab's Type
    control has always been asking for: the parameter was not on the route, so the
    control changed the URL and fired no request, and a request that did arrive
    with it (``?resource_type=documentation``) was answered with the whole
    unfiltered page — including the rows typed ``other`` — under a filter chip
    saying otherwise. An unknown value is a 422 naming the vocabulary, like the
    unknown ``sort`` above it.

    Errors: 422 for an unknown sort key, sort direction or ``resource_type``, or a
    ``limit`` outside 1-100.
    """
    return await knowledge.list_resources(
        owner=current_user,
        limit=limit,
        offset=offset,
        search=search,
        resource_type=resource_type,
        sort=sort,
        order=order,
    )


@router.post(
    "/resources",
    response_model=ResourceRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a resource",
    dependencies=[Depends(require_permission(Permission.KNOWLEDGE_WRITE))],
)
async def create_resource(
    payload: ResourceCreate,
    current_user: AuthenticatedUser,
    knowledge: KnowledgeServiceDep,
) -> Resource:
    """File a reference to an article, video, course, paper or repository.

    **Nothing is fetched.** The spec forbids scraping infrastructure and this
    route honours that: ``url`` is stored as given and ``title``/``description``
    are whatever the user typed. A resource pointing at something that has since
    rotted is a real cost, and it is a smaller one than a metadata extractor
    that silently guesses wrong.

    **The owner is the caller and cannot be named.** ``owner_id`` comes from the
    bearer token.

    ``resource_type`` defaults to ``other`` rather than being required, because a
    user saving a link to something that fits none of the eight kinds should not
    have to lie about it to get it stored.

    Errors: 422 for a blank or over-long title, or a ``url`` that is not http(s).
    """
    return await knowledge.create_resource(owner=current_user, data=payload)


@router.patch(
    "/resources/{resource_id}",
    response_model=ResourceRead,
    summary="Edit a resource",
    dependencies=[Depends(require_permission(Permission.KNOWLEDGE_WRITE))],
)
async def update_resource(
    resource_id: UUID,
    payload: ResourceUpdate,
    current_user: AuthenticatedUser,
    knowledge: KnowledgeServiceDep,
) -> Resource:
    """Apply a partial update to a resource the caller owns.

    **404 for another account's resource, never 403** — the scoped lookup runs
    before the write, and ``owner_id`` is not writable.

    A supplied ``url`` is re-validated on update, not only on create: the same
    scheme check that refused ``javascript:`` at insert has to hold for a URL
    that arrives in a PATCH, or the guard is a create-time-only decoration.

    Errors: 404 for a resource that is not the caller's; 422 for a blank or
    over-long title, or a ``url`` that is not http(s).
    """
    resource = await knowledge.get_resource(resource_id=resource_id, owner=current_user)
    return await knowledge.update_resource(resource=resource, data=payload, owner=current_user)


@router.delete(
    "/resources/{resource_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete a resource",
    dependencies=[Depends(require_permission(Permission.KNOWLEDGE_WRITE))],
)
async def delete_resource(
    resource_id: UUID,
    current_user: AuthenticatedUser,
    knowledge: KnowledgeServiceDep,
) -> Response:
    """Delete one of the caller's resources and the edges touching it.

    Edges are removed in the same transaction for the reason given on
    ``/concepts/{id}``: there is no foreign key on the link table to cascade
    from.

    Errors: 404 for a resource that is not the caller's, or that does not exist.
    """
    resource = await knowledge.get_resource(resource_id=resource_id, owner=current_user)
    await knowledge.delete_resource(resource=resource, owner=current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- #
# Bookmarks
# --------------------------------------------------------------------------- #


@router.get(
    "/bookmarks",
    response_model=Page[BookmarkRead],
    summary="List the caller's bookmarks",
    dependencies=[
        Depends(require_permission(Permission.KNOWLEDGE_READ)),
        Depends(_only("limit", "offset", "search", "sort", "order", "include_archived")),
    ],
)
async def list_bookmarks(
    current_user: AuthenticatedUser,
    knowledge: KnowledgeServiceDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
    search: Annotated[
        str | None,
        Query(description="Case-insensitive substring of the url, title or description."),
    ] = None,
    sort: Annotated[str, Query(description=_SORT_DESCRIPTION)] = "created_at",
    order: Annotated[str, Query(description="'asc' or 'desc'.")] = "desc",
) -> Page[BookmarkRead]:
    """List saved URLs, newest first.

    **``domain`` is not a filter here and is not client-supplied.** It is derived
    server-side from ``url`` on every write, because a client-sent domain is a
    client-sent *label* — "saved from github.com" rendered for a URL the user
    chose, which is a phishing surface wearing a metadata field. Filtering on it
    would also mean filtering on a derived value whose derivation the client
    cannot see; a ``search`` over the url and title answers the same question.

    Errors: 422 for an unknown sort key or sort direction, or a ``limit``
    outside 1-100.
    """
    return await knowledge.list_bookmarks(
        owner=current_user,
        limit=limit,
        offset=offset,
        search=search,
        sort=sort,
        order=order,
    )


@router.post(
    "/bookmarks",
    response_model=BookmarkRead,
    status_code=status.HTTP_201_CREATED,
    summary="Save a bookmark",
    dependencies=[Depends(require_permission(Permission.KNOWLEDGE_WRITE))],
)
async def create_bookmark(
    payload: BookmarkCreate,
    current_user: AuthenticatedUser,
    knowledge: KnowledgeServiceDep,
) -> Bookmark:
    """Save a URL, deriving its domain server-side.

    **The owner is the caller and cannot be named.** ``owner_id`` comes from the
    bearer token.

    **A URL this user already saved is a 409**, not a 500 and not a silent
    return of the existing row — uniqueness is ``(owner_id, url)``, so two
    accounts may bookmark the same link (that is just two people bookmarking it)
    while one account may not hold it twice.

    **``domain`` is computed from the URL, never accepted from the body.** The
    payload has no such field and ``extra="forbid"`` makes sending one a 422
    naming it.

    Errors: 409 for a URL this user already saved; 422 for a blank url, a url
    that is not http(s), or an over-long title.
    """
    return await knowledge.create_bookmark(owner=current_user, data=payload)


@router.patch(
    "/bookmarks/{bookmark_id}",
    response_model=BookmarkRead,
    summary="Edit a bookmark",
    dependencies=[Depends(require_permission(Permission.KNOWLEDGE_WRITE))],
)
async def update_bookmark(
    bookmark_id: UUID,
    payload: BookmarkUpdate,
    current_user: AuthenticatedUser,
    knowledge: KnowledgeServiceDep,
) -> Bookmark:
    """Apply a partial update to a bookmark the caller owns.

    **404 for another account's bookmark, never 403** — the scoped lookup runs
    before the write, and ``owner_id`` is not writable.

    A changed ``url`` **re-derives ``domain``**. Deriving it only on insert would
    leave the column describing a URL the bookmark no longer points at, which is
    worse than a null: it looks correct and is not. A PATCH that does not name
    ``url`` leaves ``domain`` alone, because the URL it was derived from is
    unchanged.

    **``archived_at`` is not on this payload.** Archiving has its own route for
    the same reason notes do, and it is not a status column: it is a timestamp
    that answers "set aside when?".

    Errors: 404 for a bookmark that is not the caller's; 409 for a url this user
    already saved on a different bookmark; 422 for a url that is not http(s).
    """
    bookmark = await knowledge.get_bookmark(bookmark_id=bookmark_id, owner=current_user)
    return await knowledge.update_bookmark(bookmark=bookmark, data=payload, owner=current_user)


@router.delete(
    "/bookmarks/{bookmark_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete a bookmark",
    dependencies=[Depends(require_permission(Permission.KNOWLEDGE_WRITE))],
)
async def delete_bookmark(
    bookmark_id: UUID,
    current_user: AuthenticatedUser,
    knowledge: KnowledgeServiceDep,
) -> Response:
    """Delete one of the caller's bookmarks.

    **The intended product answer is ``/archive``** — a bookmark you set aside is
    a bookmark you might want back, and it keeps its ``archived_at`` so you can
    find it again. This is the explicit, destructive path.

    Errors: 404 for a bookmark that is not the caller's, or that does not exist.
    """
    bookmark = await knowledge.get_bookmark(bookmark_id=bookmark_id, owner=current_user)
    await knowledge.delete_bookmark(bookmark=bookmark, owner=current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/bookmarks/{bookmark_id}/archive",
    response_model=BookmarkRead,
    summary="Archive a bookmark",
    dependencies=[Depends(require_permission(Permission.KNOWLEDGE_WRITE))],
)
async def archive_bookmark(
    bookmark_id: UUID,
    current_user: AuthenticatedUser,
    knowledge: KnowledgeServiceDep,
) -> Bookmark:
    """Set a bookmark aside, stamping ``archived_at``.

    The stamp and the state are written together, because both describe the same
    moment and a bookmark that is archived with a null ``archived_at`` cannot
    answer "archived when?". Archiving an archived bookmark is a no-op, and
    un-archiving goes through the plain PATCH — the flag is a timestamp rather
    than a status, so there is no second lifecycle endpoint to keep in step.

    Errors: 404 for a bookmark that is not the caller's.
    """
    bookmark = await knowledge.get_bookmark(bookmark_id=bookmark_id, owner=current_user)
    return await knowledge.archive_bookmark(bookmark=bookmark, owner=current_user)


# --------------------------------------------------------------------------- #
# Documents
# --------------------------------------------------------------------------- #


@router.get(
    "/documents",
    response_model=Page[DocumentRead],
    summary="List the caller's documents",
    dependencies=[
        Depends(require_permission(Permission.KNOWLEDGE_READ)),
        Depends(_only("limit", "offset", "search", "sort", "order")),
    ],
)
async def list_documents(
    current_user: AuthenticatedUser,
    knowledge: KnowledgeServiceDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
    search: Annotated[
        str | None,
        Query(description="Case-insensitive substring of the filename or title."),
    ] = None,
    sort: Annotated[str, Query(description=_SORT_DESCRIPTION)] = "updated_at",
    order: Annotated[str, Query(description="'asc' or 'desc'.")] = "desc",
) -> Page[DocumentRead]:
    """List the caller's document metadata, most recently touched first.

    **These rows are metadata, not files.** Nothing is uploaded, parsed or
    extracted by this phase — the spec asks for a document *metadata layer* and
    explicitly not a PDF pipeline. ``filename`` and ``title`` describe something
    a note may later attach; the ingestion pipeline that produces them is a
    later phase's work, and the ``notes.document_id`` foreign key is the seam
    this surface exists to leave.

    Errors: 422 for an unknown sort key or sort direction, or a ``limit``
    outside 1-100.
    """
    return await knowledge.list_documents(
        owner=current_user,
        limit=limit,
        offset=offset,
        search=search,
        sort=sort,
        order=order,
    )


@router.post(
    "/documents",
    response_model=DocumentRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a document record",
    dependencies=[Depends(require_permission(Permission.KNOWLEDGE_WRITE))],
)
async def create_document(
    payload: DocumentCreate,
    current_user: AuthenticatedUser,
    knowledge: KnowledgeServiceDep,
) -> Document:
    """Record metadata for a document the caller holds.

    **The owner is the caller and cannot be named.** ``owner_id`` comes from the
    bearer token.

    ``title`` is optional because a document may be known only by its filename
    until it is opened; ``document_type`` is optional and unconstrained for the
    same reason — inventing a taxonomy the spec did not ask for would make the
    column a guess rather than a fact.

    Errors: 422 for a blank or over-long filename.
    """
    return await knowledge.create_document(owner=current_user, data=payload)


@router.patch(
    "/documents/{document_id}",
    response_model=DocumentRead,
    summary="Edit a document record",
    dependencies=[Depends(require_permission(Permission.KNOWLEDGE_WRITE))],
)
async def update_document(
    document_id: UUID,
    payload: DocumentUpdate,
    current_user: AuthenticatedUser,
    knowledge: KnowledgeServiceDep,
) -> Document:
    """Apply a partial update to a document record the caller owns.

    **404 for another account's document, never 403** — the scoped lookup runs
    before the write, and ``owner_id`` is not writable.

    Notes referencing this document keep pointing at it: ``notes.document_id`` is
    ``ON DELETE SET NULL`` rather than ``CASCADE`` so a document record that
    goes away detaches the notes rather than deleting them with it. Losing a
    bibliography entry is recoverable; losing the notes that cite it is not.

    Errors: 404 for a document that is not the caller's; 422 for a blank or
    over-long filename.
    """
    document = await knowledge.get_document(document_id=document_id, owner=current_user)
    return await knowledge.update_document(document=document, data=payload, owner=current_user)


@router.delete(
    "/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete a document record",
    dependencies=[Depends(require_permission(Permission.KNOWLEDGE_WRITE))],
)
async def delete_document(
    document_id: UUID,
    current_user: AuthenticatedUser,
    knowledge: KnowledgeServiceDep,
) -> Response:
    """Delete one of the caller's document records.

    **Notes that cite it survive with a null ``document_id``** — see this
    module's ``/documents/{document_id}`` PATCH for why the reference is
    ``SET NULL`` rather than ``CASCADE``.

    Errors: 404 for a document that is not the caller's, or that does not exist.
    """
    document = await knowledge.get_document(document_id=document_id, owner=current_user)
    await knowledge.delete_document(document=document, owner=current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- #
# Categories
# --------------------------------------------------------------------------- #


@router.get(
    "/categories",
    response_model=Page[CategoryRead],
    summary="List the caller's categories",
    dependencies=[
        Depends(require_permission(Permission.KNOWLEDGE_READ)),
        Depends(_only("limit", "offset", "parent_id", "sort", "order")),
    ],
)
async def list_categories(
    current_user: AuthenticatedUser,
    knowledge: KnowledgeServiceDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
    parent_id: Annotated[
        UUID | None,
        Query(description="Restrict to the direct children of this category."),
    ] = None,
    sort: Annotated[str, Query(description=_SORT_DESCRIPTION)] = "name",
    order: Annotated[str, Query(description="'asc' or 'desc'.")] = "asc",
) -> Page[CategoryRead]:
    """List categories, alphabetically by default.

    **One level is not implied by the page.** ``parent_id`` is a filter over the
    *direct children* of a node and nothing more: this returns a flat page, and
    the tree is assembled by the client walking parents or asking for each
    node's children. The spec asks for a simple category tree and explicitly not
    a folder system, so there is no recursive query here — a single ``COUNT`` and
    a single ``LIMIT``d statement would not survive a deep tree anyway, and
    :func:`app.api.v1.projects.list_projects`'s reasoning about unadvertised
    filters applies with more force: a recursive endpoint the service does not
    implement would answer a filtered request with an unfiltered page.

    ``parent_id`` is **not** owner-checked by this route. Passing another
    account's id is a filter that matches nothing and returns an empty page, and
    that is the correct answer: a filter cannot widen the scope, so there is
    nothing to refuse.

    Errors: 422 for an unknown sort key or sort direction, or a ``limit``
    outside 1-100.
    """
    return await knowledge.list_categories(
        owner=current_user,
        limit=limit,
        offset=offset,
        parent_id=parent_id,
        sort=sort,
        order=order,
    )


@router.post(
    "/categories",
    response_model=CategoryRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a category",
    dependencies=[Depends(require_permission(Permission.KNOWLEDGE_WRITE))],
)
async def create_category(
    payload: CategoryCreate,
    current_user: AuthenticatedUser,
    knowledge: KnowledgeServiceDep,
) -> Category:
    """Create a category, optionally under a parent the caller owns.

    **The owner is the caller and cannot be named.** ``owner_id`` comes from the
    bearer token; uniqueness is ``(owner_id, name)``, so two accounts may both
    have a "Databases" category without colliding.

    **A ``parent_id`` owned by another account is a 404, not a 403**, because it
    is resolved through the same scoped lookup as every other id in this module —
    and the category is not created, so the refusal writes nothing. Nesting under
    a stranger's category would be a cross-tenant read of its name and, more to
    the point, a tree with a parent in it that will not survive to be rendered.

    A category may not be its own parent; the self-reference is rejected on the
    way in rather than producing a one-node cycle.

    Errors: 404 for a parent that is not the caller's; 409 for a name this user
    already has; 422 for a blank or over-long name, or a self-parent.
    """
    return await knowledge.create_category(owner=current_user, data=payload)


@router.patch(
    "/categories/{category_id}",
    response_model=CategoryRead,
    summary="Edit a category",
    dependencies=[Depends(require_permission(Permission.KNOWLEDGE_WRITE))],
)
async def update_category(
    category_id: UUID,
    payload: CategoryUpdate,
    current_user: AuthenticatedUser,
    knowledge: KnowledgeServiceDep,
) -> Category:
    """Apply a partial update to a category the caller owns.

    **404 for another account's category, never 403**, on both the category and
    any ``parent_id`` in the payload, and neither write happens if either lookup
    fails.

    **A cycle is a 422 and nothing is written.** ``parent_id`` is self-
    referencing, and the database cannot stop ``A -> B -> A`` with a foreign key,
    so the service walks the proposed ancestor chain and refuses when it reaches
    the node being moved. A category tree that contains a cycle is not rendered
    by anything, including the client, and the damage would be discovered only
    after the write.

    Naming: 404 for a category or parent that is not the caller's; 409 for a
    name this user already has on a different category; 422 for a blank or
    over-long name, a self-parent, or a parent that would create a cycle.
    """
    category = await knowledge.get_category(category_id=category_id, owner=current_user)
    return await knowledge.update_category(category=category, data=payload, owner=current_user)


@router.delete(
    "/categories/{category_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete a category",
    dependencies=[Depends(require_permission(Permission.KNOWLEDGE_WRITE))],
)
async def delete_category(
    category_id: UUID,
    current_user: AuthenticatedUser,
    knowledge: KnowledgeServiceDep,
) -> Response:
    """Delete one of the caller's categories. Its children are kept, not deleted.

    **``parent_id`` is ``ON DELETE SET NULL``, not ``CASCADE``.** Deleting
    "Programming" must not silently take React, Backend and Databases with it —
    the user asked to remove one label, not three of their children — so the
    children are promoted to top level. The alternative, refusing the delete
    while children exist, would make a tree impossible to prune without first
    emptying it from the leaves up, which is a folder system's workflow and
    exactly the complexity the spec says not to build.

    Errors: 404 for a category that is not the caller's, or that does not exist.
    """
    category = await knowledge.get_category(category_id=category_id, owner=current_user)
    await knowledge.delete_category(category=category, owner=current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- #
# Links, graph and search
# --------------------------------------------------------------------------- #


@router.get(
    "/links",
    response_model=Page[KnowledgeLinkRead],
    summary="Edges leaving a node, or edges arriving at it",
    dependencies=[
        Depends(require_permission(Permission.KNOWLEDGE_READ)),
        Depends(
            _only(
                "source_type",
                "source_id",
                "target_type",
                "target_id",
                "link_type",
                "limit",
                "offset",
            )
        ),
    ],
)
async def list_links(
    current_user: AuthenticatedUser,
    knowledge: KnowledgeServiceDep,
    source_type: Annotated[
        KnowledgeEntityType | None,
        Query(description="Endpoint kind for an outbound query. Pair with source_id."),
    ] = None,
    source_id: Annotated[
        UUID | None,
        Query(description="Outbound endpoint. Pair with source_type."),
    ] = None,
    target_type: Annotated[
        KnowledgeEntityType | None,
        Query(description="Endpoint kind for a backlink query. Pair with target_id."),
    ] = None,
    target_id: Annotated[
        UUID | None,
        Query(description="Backlink endpoint. Pair with target_type."),
    ] = None,
    link_type: Annotated[
        KnowledgeLinkType | None,
        Query(description="Restrict to one relationship kind."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[KnowledgeLinkRead]:
    """List a node's outbound edges or the edges pointing back at it.

    **One route, two directions.** ``source_type``/``source_id`` answers "what
    does this point at?"; ``target_type``/``target_id`` answers "what points at
    this?" — the second is the **backlinks** view, which the spec calls one of
    the features that separates this from a notes app. They are the same two
    columns read in a different order, so splitting them would duplicate the
    pagination contract and the ownership check for no difference in the query
    shape. A node page wants both halves anyway.

    **Exactly one complete pair must be supplied.** Naming neither, naming half
    a pair, or naming both is a 422 raised by the service — a request that
    specifies no endpoint has no bounded set of edges to return, and returning
    every link the caller owns would be an unbounded response dressed as a
    filtered one.

    **Neither id is in the path and nothing in the request mentions ``owner_id``,
    which is exactly why this route is the hard one.** The service resolves the
    named endpoint through an owner-scoped query *before* it lists; without that
    step, asking for another account's node would return their edges. See this
    module's docstring and :class:`app.models.enums.KnowledgeEntityType`.

    ``link_type`` is a vocabulary filter, not a hardcoded branch: the spec
    forbids hardcoding relationship types through the application, and an unknown
    value is a 422 rather than a filter that silently matches nothing.

    Errors: 422 for a partial, missing or doubled endpoint pair, an unknown
    ``link_type``, or a ``limit`` outside 1-100; 404 when the named endpoint is
    not the caller's.
    """
    return await knowledge.list_links(
        owner=current_user,
        source_type=source_type,
        source_id=source_id,
        target_type=target_type,
        target_id=target_id,
        link_type=link_type,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/links",
    response_model=KnowledgeLinkRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a knowledge link",
    dependencies=[Depends(require_permission(Permission.KNOWLEDGE_WRITE))],
)
async def create_link(
    payload: KnowledgeLinkCreate,
    current_user: AuthenticatedUser,
    knowledge: KnowledgeServiceDep,
) -> KnowledgeLink:
    """Record an edge between two of the caller's knowledge objects.

    **Both endpoints are resolved through an owner-scoped query before anything
    is written.** This is the security model of the whole polymorphic table, and
    it cannot live in the database: ``source_id`` and ``target_id`` carry no
    foreign key because ``source_type`` is what decides which table they point
    at. So a client that supplies somebody else's note id gets a 404 and **the
    transaction writes nothing** — no edge, no activity event, nothing to roll
    back. A guard applied after the insert would be a guard applied to a row
    that already exists.

    **A self-link is a 422.** ``source`` equal to ``target`` is a node
    referencing itself, which no renderer draws and no query can make sense of.

    **The same edge twice is a 409, not a 500.** Uniqueness is
    ``(source_type, source_id, target_type, target_id, link_type)``, so the same
    pair under a *different* ``link_type`` is a different edge and is allowed —
    a note may both reference and be referenced by nothing in particular, and
    "related_to" and "supports" between the same pair are two real statements.

    Errors: 404 when either endpoint is not the caller's, or names an id that
    does not exist; 409 for a duplicate edge; 422 for a self-link or an unknown
    entity/link type.
    """
    return await knowledge.create_link(owner=current_user, data=payload)


@router.delete(
    "/links/{link_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete a knowledge link",
    dependencies=[Depends(require_permission(Permission.KNOWLEDGE_WRITE))],
)
async def delete_link(
    link_id: UUID,
    current_user: AuthenticatedUser,
    knowledge: KnowledgeServiceDep,
) -> Response:
    """Remove one of the caller's edges.

    **404 for another account's link, never 403** — the id is resolved with
    ``owner_id`` in the query, which matters more here than on any other route
    because the edge's own row does not name an owner anywhere a foreign key
    could enforce it.

    Deleting an edge is a statement that the relationship was wrong, not that
    either endpoint was: both notes, both concepts and both resources survive.

    Errors: 404 for a link that is not the caller's, or that does not exist.
    """
    link = await knowledge.get_link(link_id=link_id, owner=current_user)
    await knowledge.delete_link(link=link, owner=current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/graph",
    response_model=KnowledgeGraph,
    summary="The caller's knowledge graph, bounded",
    dependencies=[
        Depends(require_permission(Permission.KNOWLEDGE_READ)),
        Depends(_only("limit", "entity_type")),
    ],
)
async def get_graph(
    current_user: AuthenticatedUser,
    knowledge: KnowledgeServiceDep,
    limit: Annotated[
        int,
        Query(
            ge=1,
            le=MAX_GRAPH_LIMIT,
            description=f"Maximum nodes, 1-{MAX_GRAPH_LIMIT}. A 422, never a clamp.",
        ),
    ] = DEFAULT_GRAPH_LIMIT,
    entity_type: Annotated[
        KnowledgeEntityType | None,
        Query(description="Restrict nodes to one kind; edges follow the nodes kept."),
    ] = None,
) -> KnowledgeGraph:
    """Return the caller's graph as ``{nodes, edges}``, within a hard cap.

    **The cap is the feature.** The spec names "a graph that looks impressive but
    becomes unusable" as the failure mode, and a force-directed layout is
    precisely where that shows up: a few hundred nodes is already a hairball the
    user cannot follow a single edge through. ``?limit=501`` is a 422, not a
    500-node graph wearing a 200-node graph's label — a client that asked for
    501 and got 500 could not tell a cap from a small account. The service
    enforces the same ceiling independently
    (:data:`~app.services.knowledge_service.KnowledgeService.MAX_GRAPH_NODES`) so
    non-HTTP callers are bounded too, but **for a request that reached this
    router the 422 above wins**, because this layer got to refuse rather than
    truncate.

    **``truncated`` is what makes the remaining cap legible.** A caller that got
    exactly ``limit`` nodes cannot otherwise tell a capped graph from a complete
    one, and will draw a hairball and call it their knowledge base. The field
    answers it directly, which is why the two mechanisms are complementary: the
    422 stops a *request* that overshoots, and ``truncated`` labels a *response*
    that did.

    **The whole graph is assembled in SQL**, node selection and edge fetch
    alike, with every statement carrying ``owner_id``. Nothing is read in full
    and filtered in Python, which is what would make the endpoint unusable at the
    sizes this cap still permits.

    ``entity_type`` narrows the nodes and keeps only the edges whose **both**
    endpoints survive. An edge to a node that was filtered out is dropped rather
    than drawn to nothing.

    Errors: 422 for a ``limit`` above :data:`MAX_GRAPH_LIMIT` or below 1, or an
    unknown ``entity_type``.
    """
    return await knowledge.graph(owner=current_user, limit=limit, entity_type=entity_type)


@router.get(
    "/search",
    response_model=KnowledgeSearchResult,
    summary="Search the caller's knowledge",
    dependencies=[
        Depends(require_permission(Permission.KNOWLEDGE_READ)),
        Depends(_only("q", "type", "limit", "include_archived")),
    ],
)
async def search_knowledge(
    current_user: AuthenticatedUser,
    knowledge: KnowledgeServiceDep,
    q: Annotated[
        str,
        Query(
            min_length=1,
            max_length=MAX_SEARCH_LENGTH,
            description="Case-insensitive substring. Required, and length-bounded.",
        ),
    ],
    type_: Annotated[
        KnowledgeSearchKind | None,
        Query(
            alias="type",
            description="Restrict the search to one kind of knowledge object. Wider "
            "than the graph's entity types: a bookmark and a document are both "
            "searchable even though neither is a node an edge may point at.",
        ),
    ] = None,
    limit: Annotated[
        int,
        Query(ge=1, le=MAX_SEARCH_LIMIT, description="Maximum rows *per kind*."),
    ] = MAX_SEARCH_LIMIT,
) -> KnowledgeSearchResult:
    """Search notes, concepts, resources and bookmarks in one call.

    **The result is grouped, not merged.** The response carries one list per
    entity type rather than one ranked list, so a client can render "3 notes, 1
    concept" as two sections. This is deliberately *not* the unified global search
    the spec defers to a later phase: a cross-type ranking needs a relevance model
    over heterogeneous columns, and a fabricated one — concatenating the types and
    sorting by ``created_at`` — would be a ranking that answers a different
    question from the one asked.

    ``limit`` bounds **each** group's query, not the total, and is capped at
    :data:`MAX_SEARCH_LIMIT` with a 422 rather than a silent clamp.

    **Matching is PostgreSQL ``ILIKE`` against the caller's own rows only**, one
    bounded query per entity type, and never a ``SELECT *`` filtered in Python.
    ``pg_trgm`` and ``unaccent`` are deliberately unused: this is a portable
    build and those are extensions that may not be installed, so relying on them
    would make the search the first thing that breaks on another server. The cost
    is a sequential scan that slows as a user's notes grow; a real deployment adds
    GIN trigram indexes on the same columns, which needs no column change and so
    no migration here.

    ``type`` narrows to one kind rather than enabling one; both readings are the
    same filter, and an *enable* flag is the one shape that invites a caller to
    ask for a category of result this endpoint does not have.

    Errors: 422 for a ``q`` that is empty or over
    :data:`MAX_SEARCH_LENGTH`, an unknown ``type``, or a ``limit`` outside
    1-20.
    """
    return await knowledge.search(owner=current_user, query=q, entity_type=type_, limit=limit)


#: The router only; the handlers are reached through it, not imported directly.
__all__ = ["router"]
