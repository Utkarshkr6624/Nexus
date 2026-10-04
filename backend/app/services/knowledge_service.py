"""Knowledge-base business logic: notes, the objects they link to, and the views over the lot.

Routers translate the exceptions raised here into HTTP responses — this module
never imports FastAPI.

Tenant isolation
----------------
**Every read and write is scoped by ``owner.id`` in the query.** That is what
makes another account's id answer **404 and not 403**, identically to an id that
never existed, and it is why no method here accepts an owner from the request.

**The polymorphic link table is where that stops being automatic.**
``knowledge_links.source_id`` and ``target_id`` carry no foreign key — the
``*_type`` column decides which table each id points at, so a FK could only ever
name one of them — and ``GET /knowledge/links`` takes *neither id in the path*, so
nothing in the request mentions an owner at all. :meth:`KnowledgeService.create_link`
and :meth:`KnowledgeService.list_links` therefore both begin by resolving the
named endpoint through :meth:`_resolve_endpoint`, an **owner-scoped** query
against the table the type names, and both refuse before anything is written. A
guard applied after the insert would be a guard applied to a row that already
exists; a "list then filter" would be a cross-tenant read. That resolution step
*is* the security model of this table, and it is why
:meth:`KnowledgeService._resolve_endpoint` refuses with the same ``NotFoundError``
as a missing id.

One deliberate omission: **``tag_ids`` on the note and concept payloads is
accepted and not applied.** The repositories can write ``note_tags`` and
``concept_tags``, but nothing here holds a scoped tag lookup, so there is no way
to establish that a supplied tag id belongs to the caller before writing it — and
``note_tags.tag_id`` is a foreign key to *any* tag, so an unchecked write is a
way to attach somebody else's label to your own note. Wiring :class:`TagRepository`
into this service is the fix; until then the fields are inert rather than a hole.

Null is a value, not an absence
-------------------------------
An **absent** key and a key sent as ``null`` are different requests, and
``model_dump(exclude_unset=True)`` is what keeps them apart. On a nullable
column — ``summary``, ``description``, ``parent_id``, a resource's ``url`` —
``null`` is the documented way to clear the value, and it is honoured.

On a ``NOT NULL`` column it cannot mean anything, and the update models cannot
express the difference: they type every one of their fields ``str | None`` so
that *omitting* a key stays legal, which also makes ``{"title": null}`` a valid
payload. Left alone that reaches the database and comes back as a
``NotNullViolation`` — a 500 for a missing quote — and, worse for the
``except IntegrityError`` handlers below, as a **409 claiming a name conflict the
caller never had**. :func:`_refuse_null` answers those requests with the 422 they
were written for, before anything is written.

Revisions
---------
A revision is a **point-in-time copy** of title, content and summary, not a diff.
Restoring one is therefore an assignment rather than a replay of every
intermediate edit — the only version of "undo yesterday" that is correct without
the diff format having to be perfect. The trade is storage, and it is bounded:
:data:`~app.models.knowledge.MAX_REVISIONS_PER_NOTE` newest copies are kept per
note and the rest are pruned, so a note edited daily for years holds a fixed
number of full copies rather than a thousand.

**"Meaningful" edit means the *text* changed.** A revision is written when
``title``, ``content`` or ``summary`` actually differ from the stored row. A
status flip, a ``document_id`` re-point and a no-op PATCH are not revisions: they
change no text, and a history that recorded them would fill with entries
distinguishing nothing. :meth:`KnowledgeService.update_note` compares against the
*persisted* values rather than merely against which keys the payload named,
because a client that sends the same title back is not an edit. When one is
written it is written **unconditionally** — including for a note whose body is
still empty, because ``title`` is always present and a rename of an unwritten
note is an edit whose previous state would otherwise be unrecoverable.

**Restoring writes a new revision** of the current state before it overwrites the
note, so the history is append-only and a restore is itself undoable. Consuming
the revision would make "I changed my mind" unrecoverable, which is the one thing
a history feature exists to prevent.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from sqlalchemy.exc import IntegrityError

from app.core.config import Settings, get_settings
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.enums import (
    ActivityEvent,
    KnowledgeEntityType,
    KnowledgeLinkType,
    NoteStatus,
    ResourceType,
    validate_knowledge_entity_type,
    validate_knowledge_link_type,
    validate_note_status,
    validate_resource_type,
)
from app.models.knowledge import (
    MAX_REVISIONS_PER_NOTE,
    Bookmark,
    Category,
    Concept,
    Document,
    KnowledgeLink,
    Note,
    Resource,
)
from app.models.tag import Tag
from app.models.user import User
from app.schemas.common import Page, PageMeta
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
    KnowledgeGraphEdge,
    KnowledgeGraphNode,
    KnowledgeLinkCreate,
    KnowledgeLinkRead,
    KnowledgeSearchResult,
    NoteCreate,
    NoteRead,
    NoteRevisionRead,
    NoteUpdate,
    ResourceCreate,
    ResourceRead,
    ResourceUpdate,
)

if TYPE_CHECKING:  # pragma: no cover - import cycle avoidance
    from app.models.knowledge import NoteRevision
    from app.repositories.knowledge import (
        BookmarkRepository,
        CategoryRepository,
        ConceptRepository,
        DocumentRepository,
        KnowledgeLinkRepository,
        NoteRepository,
        ResourceRepository,
    )
    from app.services.activity_service import ActivityService
    from app.services.audit_service import AuditService

__all__ = ["KnowledgeService"]

#: The default page size when a caller does not name one.
DEFAULT_PAGE_SIZE = 20

#: The hard ceiling on graph nodes, enforced here as well as in the router.
#:
#: **The cap is the feature, not a limitation of it.** The spec names "a graph
#: that looks impressive but becomes unusable" as the failure mode, and a
#: force-directed layout is where that shows up: a few hundred nodes is already a
#: hairball no single edge can be followed through. The router's ``Query(le=...)``
#: refuses a larger request with a 422 before it reaches this service, which got
#: to refuse rather than truncate; the constant is repeated so a non-HTTP caller
#: is bounded by the same rule rather than by the router's absence.
MAX_GRAPH_NODES = 500

#: The longest search term this service will run. A ``q`` longer than this is a
#: pasted document rather than a query, and it matches everything — so it is both
#: useless and the most expensive request the search endpoint accepts.
MAX_SEARCH_LENGTH = 200

#: The most rows the search returns **per entity type**. The result is grouped, so
#: this is not a total: each type is bounded by its own statement.
MAX_SEARCH_ROWS = 20

#: The only URL schemes accepted for a resource or a bookmark.
#:
#: ``javascript:`` and ``data:`` are refused because a bookmark list is rendered
#: as links, and a stored ``javascript:`` URL is a stored script the next person to
#: open the list executes. ``mailto:`` is refused for the same reason it is not
#: helpful here: it is a scheme this product has no use for and no way to render
#: safely, and an allowlist of two is a rule rather than a denylist that has to be
#: extended every time a new dangerous scheme is invented.
_ALLOWED_URL_SCHEMES = frozenset({"http", "https"})

#: Sort keys each listing accepts, validated here and again in the repository.
#:
#: ``ORDER BY`` takes an expression rather than a bound parameter, so an
#: unvalidated sort name reaching the repository would be SQL injection behind a
#: query parameter. The two layers fail closed for different audiences: this one
#: raises the 422 a *request* deserves, the repository raises the ``ValueError`` a
#: *programming* error deserves.
_SORT_KEYS: dict[str, frozenset[str]] = {
    "note": frozenset({"created_at", "status", "title", "updated_at"}),
    "concept": frozenset({"created_at", "name", "updated_at"}),
    "resource": frozenset({"created_at", "resource_type", "title", "updated_at"}),
    "bookmark": frozenset({"archived_at", "created_at", "domain", "updated_at"}),
    "document": frozenset({"created_at", "filename", "title", "updated_at"}),
    "category": frozenset({"created_at", "name", "updated_at"}),
}
_SORT_ORDERS = frozenset({"asc", "desc"})

#: Which entity types the **search** iterates over, named once so the "no filter"
#: branch is a list rather than a hand-written OR chain. Categories are absent on
#: purpose: they have no free text to search.
#:
#: **Bookmarks belong here and did not used to.** This list used to be the
#: *graph's* list of node types, and a bookmark is rightly not a node — a graph
#: node is ``(id, type, label)`` and a bookmark has no label and no edge — so the
#: search silently inherited the omission and answered every unfiltered query
#: with an empty ``bookmarks`` list. The spec names bookmarks as one of the
#: things knowledge search covers, the response model carries the field, and the
#: branch below it was unreachable code, so "no bookmark matched" and "no bookmark
#: was ever looked for" were indistinguishable to every caller. The graph keeps
#: its own, narrower set — see :func:`app.repositories.knowledge._entity_types`.
_SEARCH_TABLES = ("note", "concept", "resource", "bookmark")

#: The fields a PATCH may not set to JSON ``null``, per entity.
#:
#: Every ``*Update`` model types these as ``str | None`` — Pydantic has no way to
#: say "optional but not nullable", and a field with no default has to accept
#: ``None`` to stay optional — while the columns behind them are ``NOT NULL``. So
#: ``{"title": null}`` validates, reaches the repository, and is a 500 from the
#: database rather than the 422 the caller wrote it for. The nullable columns
#: (``summary``, ``description``, ``parent_id``, ``resource_type``, a note's
#: ``document_id``) are absent from this map on purpose: clearing one of those is
#: a real request, and ``"summary": null`` is the documented way to say so.
_NOT_CLEARABLE_FIELDS: Mapping[str, frozenset[str]] = {
    "note": frozenset({"content", "title"}),
    "concept": frozenset({"name"}),
    "resource": frozenset({"title"}),
    "bookmark": frozenset({"url"}),
    "document": frozenset({"filename"}),
    "category": frozenset({"name"}),
}

#: Fields whose change makes an edit *meaningful* and therefore worth a revision.
#: A status flip or a ``document_id`` re-point changes no text, and a history that
#: recorded those would fill with entries distinguishing nothing.
_REVISION_FIELDS = ("title", "content", "summary")

_NOTE_NOT_FOUND = "Note not found."
_CONCEPT_NOT_FOUND = "Concept not found."
_RESOURCE_NOT_FOUND = "Resource not found."
_BOOKMARK_NOT_FOUND = "Bookmark not found."
_DOCUMENT_NOT_FOUND = "Document not found."
_CATEGORY_NOT_FOUND = "Category not found."
_LINK_NOT_FOUND = "Knowledge link not found."
_ENDPOINT_NOT_FOUND = "That knowledge object does not exist."
_REVISION_NOT_FOUND = "Revision not found."


class KnowledgeService:
    """The rules of the knowledge base: lifecycle, history, edges and cycles."""

    def __init__(
        self,
        notes: NoteRepository,
        concepts: ConceptRepository,
        resources: ResourceRepository,
        bookmarks: BookmarkRepository,
        documents: DocumentRepository,
        categories: CategoryRepository,
        links: KnowledgeLinkRepository,
        activity: ActivityService | None = None,
        audit: AuditService | None = None,
        settings: Settings | None = None,
    ) -> None:
        """Wire the service.

        **Seven repositories, not one, and the schema is the reason.** The seven
        tables have seven different uniqueness rules and there is no join that
        would collapse them. ``links`` is the one that cannot be folded in at all:
        ``knowledge_links`` is polymorphic, so only a service holding the other
        repositories can resolve an endpoint through the right owner-scoped query
        before writing an edge.

        ``activity`` records the Phase 5 events beside the write they describe. It
        is optional so the rules can be exercised without a history sink, and
        wired by the API layer because the Phase 6 analytics that read this feed
        are downstream of it being true.

        ``audit`` is accepted for symmetry with the other services and used by
        neither, on ``ProjectService``'s reasoning: writing a note is a fact about
        the work, not a fact about the account, and ``audit_logs`` is the security
        trail.
        """
        self.notes = notes
        self.concepts = concepts
        self.resources = resources
        self.bookmarks = bookmarks
        self.documents = documents
        self.categories = categories
        self.links = links
        self.activity = activity
        self.audit = audit
        self.settings = settings or get_settings()

    # -- Notes --------------------------------------------------------------

    async def list_notes(
        self,
        *,
        owner: User,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
        status: NoteStatus | str | None = None,
        search: str | None = None,
        sort: str = "updated_at",
        order: str = "desc",
    ) -> Page[NoteRead]:
        """List the caller's notes as one page.

        Args:
            owner: The authenticated caller. The scope is the caller's own.
            limit: Maximum rows in the page.
            offset: Rows to skip.
            status: Restrict to one lifecycle state, validated here so a typo is
                a 422 naming the field rather than a filter that matches nothing.
            search: Case-insensitive substring over title, content or summary.
            sort: One of ``_SORT_KEYS["note"]``.
            order: ``"asc"`` or ``"desc"``.

        Returns:
            The page and its metadata, the total being the size of the *filtered*
            set rather than of the slice.

        Raises:
            ValidationError: For an unknown status, sort key, sort direction, or a
                page window outside 1-100.
        """
        _check_window(limit=limit, offset=offset)
        status_value = _status_or_none(status)
        sort = _check_sort("note", sort, order)
        rows, total = await self.notes.list_for_user(
            owner.id,
            limit=limit,
            offset=offset,
            status=status_value,
            search=search,
            sort=sort,
            order=order,
        )
        return await self._note_page(rows, total, limit, offset)

    async def create_note(self, *, owner: User, data: NoteCreate) -> Note:
        """Create a note, always ``draft``.

        ``owner_id`` comes from the caller's session and never from the payload.
        A note carrying a ``document_id`` resolves it through the *scoped* document
        lookup first, so a note can never cite a record belonging to somebody
        else — and refuses **before** the row exists rather than leaving a note
        pointing at a document id the caller cannot read.

        No revision is written: there is no previous text to preserve, and a
        revision holding a note's own creation would make the history begin with
        a state the note was never in.
        """
        document_id = await self._own_document(data.document_id, owner)
        note = await self.notes.create(
            owner_id=owner.id,
            title=data.title,
            content=data.content,
            summary=data.summary,
            document_id=document_id,
        )
        await self._record(ActivityEvent.NOTE_CREATED, owner=owner, metadata={"title": note.title})
        return note

    async def get_note(self, *, note_id: uuid.UUID, owner: User) -> Note:
        """Return one of the caller's notes, with its history and tag counts on it.

        The returned row carries :attr:`NoteRead.revision_count` and
        ``tag_ids`` — see :meth:`_annotate` for why they are put on the row
        rather than left for the response model to default.

        Raises:
            NotFoundError: If the caller owns no note with this id. Another
                user's note and a nonexistent one are the same answer on purpose:
                a different error would turn this endpoint into a probe for which
                note ids are real.
        """
        note = await self.notes.get_by_id_for_user(note_id, owner.id)
        if note is None:
            raise NotFoundError(_NOTE_NOT_FOUND)
        return await self._annotate(note)

    async def update_note(self, *, note: Note, data: NoteUpdate, owner: User) -> Note:
        """Apply a partial update to a note the caller owns.

        **A field is written if the client named it** — ``"summary": null`` clears
        the summary, while an absent key leaves the column be — so
        ``model_dump(exclude_unset=True)`` is used, because Pydantic collapses
        "absent" and "sent as null" into the same ``None``.

        **A revision is written only when the text actually changed.** The test is
        against the persisted row rather than against which keys the payload
        named, so a client that PATCHes the same title back is not recorded as an
        edit and a status-only change leaves the history alone. The revision holds
        the *previous* values, so history is preserved by the write that displaced
        it rather than by a save button the user has to remember.

        **A field the column cannot hold is a 422 before the snapshot.** The
        update models type ``title`` and ``content`` as ``str | None`` while the
        columns are ``NOT NULL``; see :func:`_refuse_null` for why that is
        answered here rather than left to become a 500 — and why it has to happen
        before the revision, so a refused edit leaves no history behind.
        """
        self._own_note(note, owner)
        fields = {
            key: value
            for key, value in data.model_dump(exclude_unset=True).items()
            if key != "document_id"
        }
        # Before the snapshot, not after it: a refused edit must leave no trace,
        # and a revision written for an edit that then failed would be a history
        # entry describing a state the note was never in.
        _refuse_null(fields, entity="note")
        if "document_id" in data.model_fields_set:
            fields["document_id"] = await self._own_document(data.document_id, owner)
        if not fields:
            return await self._annotate(note)

        meaningful = any(
            name in fields and fields[name] != getattr(note, name) for name in _REVISION_FIELDS
        )
        if meaningful:
            await self._snapshot(note)
        updated = await self.notes.update_fields(note, **fields)
        await self._record(
            ActivityEvent.NOTE_UPDATED,
            owner=owner,
            metadata={"fields": sorted(fields)},
        )
        return await self._annotate(updated)

    async def delete_note(self, *, note: Note, owner: User) -> None:
        """Delete a note, its revisions and its edges.

        The edges go with it **in the service** because ``knowledge_links`` has no
        foreign key to cascade from: leaving them would make the graph keep drawing
        into a node that no longer exists. Activity rows do not cascade — the
        trail outlives its subject.
        """
        self._own_note(note, owner)
        await self.links.delete_for_entity(entity_type="note", entity_id=note.id, owner_id=owner.id)
        await self.notes.delete(note)

    async def publish_note(self, *, note: Note, owner: User) -> Note:
        """Assert that a note is real now.

        Idempotent: publishing an already-published note returns it unchanged, so
        a retried click gets the note it asked for rather than a 422 about a
        transition it has already made.
        """
        self._own_note(note, owner)
        if note.status == NoteStatus.PUBLISHED.value:
            return await self._annotate(note)
        updated = await self.notes.update_fields(note, status=NoteStatus.PUBLISHED.value)
        await self._record(
            ActivityEvent.NOTE_PUBLISHED, owner=owner, metadata={"title": note.title}
        )
        return await self._annotate(updated)

    async def archive_note(self, *, note: Note, owner: User) -> Note:
        """Set a note aside, keeping it, its revisions and its edges."""
        self._own_note(note, owner)
        if note.status == NoteStatus.ARCHIVED.value:
            return await self._annotate(note)
        updated = await self.notes.update_fields(note, status=NoteStatus.ARCHIVED.value)
        await self._record(ActivityEvent.NOTE_ARCHIVED, owner=owner, metadata={"title": note.title})
        return await self._annotate(updated)

    async def restore_note(self, *, note: Note, owner: User) -> Note:
        """Return an archived note to the working set as a **draft**.

        ``draft`` and not the status it held before archiving, because the schema
        has no column recording that one and re-asserting ``published`` on the
        user's behalf would claim an act they did not perform. Publishing it back
        is one click away.
        """
        self._own_note(note, owner)
        if note.status != NoteStatus.ARCHIVED.value:
            return await self._annotate(note)
        updated = await self.notes.update_fields(note, status=NoteStatus.DRAFT.value)
        await self._record(ActivityEvent.NOTE_RESTORED, owner=owner, metadata={"title": note.title})
        return await self._annotate(updated)

    # -- Revisions ----------------------------------------------------------

    async def list_revisions(
        self, *, note: Note, limit: int = DEFAULT_PAGE_SIZE, offset: int = 0
    ) -> Page[NoteRevisionRead]:
        """Return a note's revisions, newest first."""
        self._own_note(note, owner=None)
        _check_window(limit=limit, offset=offset)
        rows, total = await self.notes.list_revisions(
            note.id, note.owner_id, limit=limit, offset=offset
        )
        return _page(rows, total, limit, offset, NoteRevisionRead)

    async def get_revision(
        self, *, note: Note, revision_id: uuid.UUID, owner: User
    ) -> NoteRevision:
        """Return one revision of one of the caller's notes.

        All three of ``note_id``, ``revision_id`` and ``owner_id`` are in the
        lookup, so a caller cannot pair their own note with somebody else's
        revision id and have it confirmed.

        Raises:
            NotFoundError: If the revision is not one of this note's.
        """
        self._own_note(note, owner)
        revision = await self.notes.get_revision(note.id, revision_id, owner.id)
        if revision is None:
            raise NotFoundError(_REVISION_NOT_FOUND)
        return revision

    async def restore_revision(self, *, note: Note, revision_id: uuid.UUID, owner: User) -> Note:
        """Write a revision's text back onto the note, recording a new revision first.

        **The history is append-only.** The note's current text is snapshotted
        before the overwrite, so the restore is itself undoable; consuming the
        revision instead would make "I changed my mind" permanent.

        **Status is not restored.** A revision is a copy of the *text*, and
        reverting the lifecycle with it would un-publish a note on the strength of
        an edit made after publishing — a claim the user never made. Status moves
        only through ``publish_note``, ``archive_note`` and ``restore_note``.
        """
        self._own_note(note, owner)
        revision = await self.notes.get_revision(note.id, revision_id, owner.id)
        if revision is None:
            raise NotFoundError(_REVISION_NOT_FOUND)
        await self._snapshot(note)
        updated = await self.notes.update_fields(
            note, title=revision.title, content=revision.content, summary=revision.summary
        )
        await self._record(
            ActivityEvent.NOTE_REVISION_RESTORED,
            owner=owner,
            metadata={"revision_id": str(revision.id)},
        )
        return await self._annotate(updated)

    # -- Concepts -----------------------------------------------------------

    async def list_concepts(
        self,
        *,
        owner: User,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
        search: str | None = None,
        sort: str = "name",
        order: str = "asc",
    ) -> Page[ConceptRead]:
        """List the caller's concepts alphabetically by default.

        ``name`` ascending rather than ``created_at`` descending: a concept list
        is an index a person scans alphabetically, not a feed.
        """
        _check_window(limit=limit, offset=offset)
        sort = _check_sort("concept", sort, order)
        rows, total = await self.concepts.list_for_user(
            owner.id,
            limit=limit,
            offset=offset,
            sort=sort,
            order=order,
            search=search,
        )
        return await self._concept_page(rows, total, limit, offset)

    async def create_concept(self, *, owner: User, data: ConceptCreate) -> Concept:
        """Create a concept.

        **The owner is the caller and cannot be named.** A create payload that
        could choose its owner would let any authenticated user file a concept
        into another account.

        Raises:
            ConflictError: If this user already holds a concept with that exact
                name. The constraint is ``(owner_id, name)``, so two accounts may
                both hold "AVL Tree" without colliding — the conflict is only ever
                *within* one account.
        """
        if await self.concepts.get_by_name(owner.id, data.name) is not None:
            raise ConflictError("You already have a concept with that name.")
        try:
            concept = await self.concepts.create(
                owner_id=owner.id, name=data.name, description=data.description
            )
        except IntegrityError as exc:
            raise ConflictError("You already have a concept with that name.") from exc
        await self._record(
            ActivityEvent.CONCEPT_CREATED, owner=owner, metadata={"name": concept.name}
        )
        return concept

    async def get_concept(self, *, concept_id: uuid.UUID, owner: User) -> Concept:
        """Return one of the caller's concepts, or 404."""
        concept = await self.concepts.get_by_id_for_user(concept_id, owner.id)
        if concept is None:
            raise NotFoundError(_CONCEPT_NOT_FOUND)
        return concept

    async def update_concept(
        self, *, concept: Concept, data: ConceptUpdate, owner: User
    ) -> Concept:
        """Apply a partial update to a concept, refusing a name the user holds.

        Renaming to the name the concept *already* has is a no-op rather than a
        conflict: it collides with itself, and answering a retried rename with a
        409 would make the idempotent path the one that fails.

        **A ``name`` sent as ``null`` is a 422, not a 409.** The model types it
        ``str | None``, the column is ``NOT NULL``, and without this check the
        write raises ``IntegrityError`` which the handler below turns into "you
        already have a concept with that name" — a confident statement about a
        conflict the caller did not have. See :func:`_refuse_null`.
        """
        self._own_row(concept, owner, _CONCEPT_NOT_FOUND)
        fields = data.model_dump(exclude_unset=True)
        _refuse_null(fields, entity="concept")
        if "name" in fields and fields["name"] == concept.name:
            fields.pop("name")
        if "name" in fields:
            clash = await self.concepts.get_by_name(owner.id, fields["name"])
            if clash is not None:
                raise ConflictError("You already have a concept with that name.")
        if not fields:
            return concept
        try:
            return await self.concepts.update_fields(concept, **fields)
        except IntegrityError as exc:
            raise ConflictError("You already have a concept with that name.") from exc

    async def delete_concept(self, *, concept: Concept, owner: User) -> None:
        """Delete a concept and the edges touching it.

        The edges are removed explicitly for the reason given on
        :meth:`delete_note`: there is no foreign key on the link table to cascade
        from, so nothing else would stop the graph drawing into a dead node.
        """
        self._own_row(concept, owner, _CONCEPT_NOT_FOUND)
        await self.links.delete_for_entity(
            entity_type="concept", entity_id=concept.id, owner_id=owner.id
        )
        await self.concepts.delete(concept)

    # -- Resources ----------------------------------------------------------

    async def list_resources(
        self,
        *,
        owner: User,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
        search: str | None = None,
        sort: str = "updated_at",
        order: str = "desc",
    ) -> Page[ResourceRead]:
        """List the external things the caller has filed."""
        _check_window(limit=limit, offset=offset)
        sort = _check_sort("resource", sort, order)
        rows, total = await self.resources.list_for_user(
            owner.id,
            limit=limit,
            offset=offset,
            sort=sort,
            order=order,
            search=search,
        )
        return _page(rows, total, limit, offset, ResourceRead)

    async def create_resource(self, *, owner: User, data: ResourceCreate) -> Resource:
        """Create a resource, refusing a URL that is not http(s).

        The scheme check is here rather than in the schema because the same rule
        has to hold for a URL arriving in a PATCH, and a schema-level guard on the
        create payload alone would be a decoration.
        """
        url = _check_url(data.url, required=False)
        resource = await self.resources.create(
            owner_id=owner.id,
            title=data.title,
            description=data.description,
            url=url,
            resource_type=data.resource_type.value,
        )
        await self._record(
            ActivityEvent.RESOURCE_CREATED, owner=owner, metadata={"title": resource.title}
        )
        return resource

    async def get_resource(self, *, resource_id: uuid.UUID, owner: User) -> Resource:
        """Return one of the caller's resources, or 404."""
        resource = await self.resources.get_by_id_for_user(resource_id, owner.id)
        if resource is None:
            raise NotFoundError(_RESOURCE_NOT_FOUND)
        return resource

    async def update_resource(
        self, *, resource: Resource, data: ResourceUpdate, owner: User
    ) -> Resource:
        """Apply a partial update to a resource, re-validating a supplied URL.

        ``description`` and ``url`` are nullable columns, so ``null`` clears them
        and is honoured. ``title`` is not nullable, so a ``null`` title is a 422
        rather than a ``NotNullViolation`` from the database — see
        :func:`_refuse_null`.
        """
        self._own_row(resource, owner, _RESOURCE_NOT_FOUND)
        fields = data.model_dump(exclude_unset=True)
        _refuse_null(fields, entity="resource")
        if "url" in fields:
            fields["url"] = _check_url(fields["url"], required=False)
        if "resource_type" in fields and fields["resource_type"] is not None:
            fields["resource_type"] = _resource_type_or_raise(fields["resource_type"]).value
        if not fields:
            return resource
        return await self.resources.update_fields(resource, **fields)

    async def delete_resource(self, *, resource: Resource, owner: User) -> None:
        """Delete a resource and the edges touching it."""
        self._own_row(resource, owner, _RESOURCE_NOT_FOUND)
        await self.links.delete_for_entity(
            entity_type="resource", entity_id=resource.id, owner_id=owner.id
        )
        await self.resources.delete(resource)

    # -- Bookmarks ----------------------------------------------------------

    async def list_bookmarks(
        self,
        *,
        owner: User,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
        search: str | None = None,
        sort: str = "created_at",
        order: str = "desc",
    ) -> Page[BookmarkRead]:
        """List saved URLs, newest first.

        ``domain`` is deliberately not a filter: it is a *derived* value whose
        derivation the client cannot see, so filtering on it would mean filtering
        on something the caller cannot reproduce. The ``search`` over url and title
        answers the same question honestly.
        """
        _check_window(limit=limit, offset=offset)
        sort = _check_sort("bookmark", sort, order)
        rows, total = await self.bookmarks.list_for_user(
            owner.id,
            limit=limit,
            offset=offset,
            sort=sort,
            order=order,
            search=search,
        )
        return _page(rows, total, limit, offset, BookmarkRead)

    async def create_bookmark(self, *, owner: User, data: BookmarkCreate) -> Bookmark:
        """Save a URL, deriving ``domain`` from it.

        **``domain`` is never read from the payload.** It is computed here on
        every write, because a client-supplied domain is a client-supplied *label*
        — "saved from github.com" rendered beside a URL the user chose, which is a
        phishing surface wearing a metadata field.

        Raises:
            ConflictError: If this user already saved this exact URL. Uniqueness is
                ``(owner_id, url)``, so two accounts may each save the same URL and
                the conflict is only ever within one account.
        """
        url = _check_url(data.url, required=True)
        if await self.bookmarks.get_by_url(owner.id, url) is not None:
            raise ConflictError("You have already saved that URL.")
        try:
            bookmark = await self.bookmarks.create(
                owner_id=owner.id,
                url=url,
                title=data.title,
                description=data.description,
            )
        except IntegrityError as exc:
            raise ConflictError("You have already saved that URL.") from exc
        await self._record(
            ActivityEvent.BOOKMARK_CREATED, owner=owner, metadata={"domain": bookmark.domain}
        )
        return bookmark

    async def get_bookmark(self, *, bookmark_id: uuid.UUID, owner: User) -> Bookmark:
        """Return one of the caller's bookmarks, or 404."""
        bookmark = await self.bookmarks.get_by_id_for_user(bookmark_id, owner.id)
        if bookmark is None:
            raise NotFoundError(_BOOKMARK_NOT_FOUND)
        return bookmark

    async def update_bookmark(
        self, *, bookmark: Bookmark, data: BookmarkUpdate, owner: User
    ) -> Bookmark:
        """Apply a partial update, re-deriving ``domain`` when the URL changes.

        Deriving only on insert would leave the column describing a URL the
        bookmark no longer points at — worse than a null, because it looks
        correct and is not. A PATCH that does not name ``url`` leaves ``domain``
        alone, because the URL it was derived from is unchanged.

        ``title`` and ``description`` are nullable and a ``null`` clears them. A
        ``url`` sent as ``null`` is refused with a 422 rather than ignored: a
        bookmark is exactly its URL, there is no empty one, and silently dropping
        the field would answer a request to change it with a success.

        Raises:
            ConflictError: If the new URL is one this user already saved on a
                *different* bookmark.
            ValidationError: If ``url`` is sent as ``null``.
        """
        self._own_row(bookmark, owner, _BOOKMARK_NOT_FOUND)
        fields = data.model_dump(exclude_unset=True)
        _refuse_null(fields, entity="bookmark")
        if "url" in fields and fields["url"] is not None:
            url = _check_url(fields["url"], required=True)
            if url != bookmark.url:
                clash = await self.bookmarks.get_by_url(owner.id, url)
                if clash is not None and clash.id != bookmark.id:
                    raise ConflictError("You have already saved that URL.")
                fields["url"] = url
        if not fields:
            return bookmark
        try:
            return await self.bookmarks.update_fields(bookmark, **fields)
        except IntegrityError as exc:
            raise ConflictError("You have already saved that URL.") from exc

    async def delete_bookmark(self, *, bookmark: Bookmark, owner: User) -> None:
        """Delete a bookmark. The intended product answer is ``archive_bookmark``."""
        self._own_row(bookmark, owner, _BOOKMARK_NOT_FOUND)
        await self.bookmarks.delete(bookmark)

    async def archive_bookmark(self, *, bookmark: Bookmark, owner: User) -> Bookmark:
        """Set a bookmark aside, stamping ``archived_at``.

        The stamp and the state are written together, because both describe the
        same moment and a bookmark that is archived with a null ``archived_at``
        cannot answer "archived when?". Idempotent.
        """
        self._own_row(bookmark, owner, _BOOKMARK_NOT_FOUND)
        if bookmark.archived_at is not None:
            return bookmark
        return await self.bookmarks.archive(bookmark, archived_at=datetime.now(UTC))

    # -- Documents ----------------------------------------------------------

    async def list_documents(
        self,
        *,
        owner: User,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
        search: str | None = None,
        sort: str = "updated_at",
        order: str = "desc",
    ) -> Page[DocumentRead]:
        """List the caller's document records.

        **These rows are metadata, not files.** Nothing is uploaded, parsed or
        extracted in this phase; ``filename`` and ``title`` describe something a
        note may later attach, and ``notes.document_id`` is the seam an ingestion
        pipeline hooks into.
        """
        _check_window(limit=limit, offset=offset)
        sort = _check_sort("document", sort, order)
        rows, total = await self.documents.list_for_user(
            owner.id,
            limit=limit,
            offset=offset,
            sort=sort,
            order=order,
            search=search,
        )
        return _page(rows, total, limit, offset, DocumentRead)

    async def create_document(self, *, owner: User, data: DocumentCreate) -> Document:
        """Create a document record. Metadata only — see :meth:`list_documents`."""
        return await self.documents.create(
            owner_id=owner.id,
            filename=data.filename,
            title=data.title,
            description=data.description,
            document_type=data.document_type,
        )

    async def get_document(self, *, document_id: uuid.UUID, owner: User) -> Document:
        """Return one of the caller's document records, or 404."""
        document = await self.documents.get_by_id_for_user(document_id, owner.id)
        if document is None:
            raise NotFoundError(_DOCUMENT_NOT_FOUND)
        return document

    async def update_document(
        self, *, document: Document, data: DocumentUpdate, owner: User
    ) -> Document:
        """Apply a partial update to a document record.

        Notes citing this document keep pointing at it: ``notes.document_id`` is
        ``ON DELETE SET NULL`` rather than ``CASCADE``, so losing a bibliography
        entry detaches the notes rather than deleting them with it.

        Every other field here is nullable, so ``null`` clears it. ``filename``
        is the exception and is refused with a 422 — see :func:`_refuse_null`.
        """
        self._own_row(document, owner, _DOCUMENT_NOT_FOUND)
        fields = data.model_dump(exclude_unset=True)
        _refuse_null(fields, entity="document")
        if not fields:
            return document
        return await self.documents.update_fields(document, **fields)

    async def delete_document(self, *, document: Document, owner: User) -> None:
        """Delete a document record; the notes citing it survive with a null reference."""
        self._own_row(document, owner, _DOCUMENT_NOT_FOUND)
        await self.documents.delete(document)

    # -- Categories ---------------------------------------------------------

    async def list_categories(
        self,
        *,
        owner: User,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
        parent_id: uuid.UUID | None = None,
        sort: str = "name",
        order: str = "asc",
    ) -> Page[CategoryRead]:
        """List categories, one flat page.

        ``parent_id`` filters to the **direct children** of a node and nothing
        more: the tree is assembled by the client, and there is no recursive query
        here — a single ``COUNT`` and a single ``LIMIT`` do not survive a deep tree,
        and an endpoint the service did not implement would answer a filtered
        request with an unfiltered page.

        ``parent_id`` is *not* owner-checked here. Passing another account's id is
        a filter that matches nothing and returns an empty page, which is the
        correct answer: a filter cannot widen the scope, so there is nothing to
        refuse.
        """
        _check_window(limit=limit, offset=offset)
        sort = _check_sort("category", sort, order)
        rows, total = await self.categories.list_for_user(
            owner.id,
            limit=limit,
            offset=offset,
            sort=sort,
            order=order,
            parent_id=parent_id,
        )
        return _page(rows, total, limit, offset, CategoryRead)

    async def create_category(self, *, owner: User, data: CategoryCreate) -> Category:
        """Create a category, refusing a parent that is not the caller's.

        A ``parent_id`` pointing at somebody else's category is a 404 rather than a
        403, exactly like every other id on this surface.

        Raises:
            ConflictError: If this user already has a category with that name.
            NotFoundError: If ``parent_id`` is not one of the caller's categories.
        """
        parent = await self._own_category(data.parent_id, owner, what="parent category")
        if await self.categories.get_by_name(owner.id, data.name) is not None:
            raise ConflictError("You already have a category with that name.")
        try:
            return await self.categories.create(
                owner_id=owner.id, name=data.name, parent_id=parent.id if parent else None
            )
        except IntegrityError as exc:
            raise ConflictError("You already have a category with that name.") from exc

    async def get_category(self, *, category_id: uuid.UUID, owner: User) -> Category:
        """Return one of the caller's categories, or 404."""
        category = await self.categories.get_by_id_for_user(category_id, owner.id)
        if category is None:
            raise NotFoundError(_CATEGORY_NOT_FOUND)
        return category

    async def update_category(
        self, *, category: Category, data: CategoryUpdate, owner: User
    ) -> Category:
        """Apply a partial update to a category, refusing a parent cycle.

        **A cycle is refused and nothing is written.** The database can stop a
        category being its own parent with a check constraint, but ``A -> B -> C ->
        A`` is legal for a foreign key and impossible for a tree, and every client
        that renders the tree walks parents — so the ancestor chain is walked here
        and the move is rejected if it reaches the node being moved.

        Raises:
            NotFoundError: If ``parent_id`` is not one of the caller's categories.
            ConflictError: If the new name is one this user already holds on a
                different category.
            ValidationError: If the new parent would create a cycle.
        """
        self._own_row(category, owner, _CATEGORY_NOT_FOUND)
        fields = data.model_dump(exclude_unset=True)
        _refuse_null(fields, entity="category")
        if "parent_id" in fields:
            parent = await self._own_category(fields["parent_id"], owner, what="parent category")
            # The walk stops when it revisits a node, so a pre-existing cycle in
            # somebody's data cannot make this loop forever.
            ancestors = (
                await self.categories.list_ancestors(parent, owner.id) if parent is not None else []
            )
            if parent is not None and (
                parent.id == category.id or any(node.id == category.id for node in ancestors)
            ):
                raise ValidationError("A category cannot be its own ancestor.")
            fields["parent_id"] = parent.id if parent is not None else None
        if "name" in fields and fields["name"] == category.name:
            fields.pop("name")
        if "name" in fields:
            clash = await self.categories.get_by_name(owner.id, fields["name"])
            if clash is not None and clash.id != category.id:
                raise ConflictError("You already have a category with that name.")
        if not fields:
            return category
        try:
            return await self.categories.update_fields(category, **fields)
        except IntegrityError as exc:
            raise ConflictError("You already have a category with that name.") from exc

    async def delete_category(self, *, category: Category, owner: User) -> None:
        """Delete a category. Its children are kept, not deleted.

        ``parent_id`` is ``ON DELETE SET NULL``, not ``CASCADE``: deleting
        "Programming" must not silently take React, Backend and Databases with it.
        The user asked to remove one label, not three of their children. Refusing
        the delete while children exist would instead make the tree impossible to
        prune without emptying it from the leaves up — a folder system's workflow,
        and exactly the complexity the spec says not to build.
        """
        self._own_row(category, owner, _CATEGORY_NOT_FOUND)
        await self.categories.delete(category)

    # -- Links --------------------------------------------------------------

    async def list_links(
        self,
        *,
        owner: User,
        source_type: KnowledgeEntityType | None = None,
        source_id: uuid.UUID | None = None,
        target_type: KnowledgeEntityType | None = None,
        target_id: uuid.UUID | None = None,
        link_type: KnowledgeLinkType | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> Page[KnowledgeLinkRead]:
        """List a node's outbound edges, or the edges arriving at it (backlinks).

        **One route, two directions.** ``source_*`` answers "what does this point
        at?"; ``target_*`` answers "what points at this?", and the second is the
        backlinks view the spec calls out as what separates this from a notes app.
        They are the same two columns read in a different order, so splitting them
        would duplicate the pagination contract and the ownership check for no
        difference in the query shape.

        **Exactly one complete pair must be supplied** — naming neither, half a
        pair, or both is a 422. A request that specifies no endpoint has no
        bounded set of edges to return, and returning every link the caller owns
        would be an unbounded response dressed as a filtered one.

        **The named endpoint is resolved through an owner-scoped query first.**
        Neither id is in the path, so nothing in the request mentions ``owner_id``:
        without this step, ``?source_type=note&source_id=<someone else's note>``
        would return their edges. See this module's docstring.

        Raises:
            ValidationError: For a missing, partial or doubled endpoint pair, or an
                unknown ``link_type``.
            NotFoundError: If the named endpoint is not the caller's.
        """
        _check_window(limit=limit, offset=offset)
        outbound = source_type is not None or source_id is not None
        inbound = target_type is not None or target_id is not None
        if outbound and inbound:
            raise ValidationError(
                "Supply either source_type with source_id, or target_type with target_id — not both."
            )
        if not outbound and not inbound:
            raise ValidationError(
                "Supply source_type with source_id, or target_type with target_id."
            )
        if outbound and (source_type is None or source_id is None):
            raise ValidationError("source_type and source_id must be supplied together.")
        if inbound and (target_type is None or target_id is None):
            raise ValidationError("target_type and target_id must be supplied together.")

        link_type_value = _link_type_or_none(link_type)
        if outbound:
            await self._resolve_endpoint(
                _entity_or_raise(source_type, "source_type"), source_id, owner
            )
            rows, total = await self.links.list_outbound(
                owner.id,
                source_type=source_type.value,
                source_id=source_id,
                link_type=link_type_value,
                limit=limit,
                offset=offset,
            )
        else:
            await self._resolve_endpoint(
                _entity_or_raise(target_type, "target_type"), target_id, owner
            )
            rows, total = await self.links.list_inbound(
                owner.id,
                target_type=target_type.value,
                target_id=target_id,
                link_type=link_type_value,
                limit=limit,
                offset=offset,
            )
        return _page(rows, total, limit, offset, KnowledgeLinkRead)

    async def create_link(self, *, owner: User, data: KnowledgeLinkCreate) -> KnowledgeLink:
        """Record an edge between two of the caller's knowledge objects.

        **Both endpoints are resolved through an owner-scoped query before
        anything is written.** This is the security model of the polymorphic table
        and it cannot live in the database — ``source_id`` and ``target_id`` carry
        no foreign key because ``source_type`` is what decides which table they
        point at. A client supplying somebody else's note id therefore gets a 404
        and **the transaction writes nothing**: no edge, no activity event, nothing
        to roll back.

        Raises:
            NotFoundError: If either endpoint is not the caller's or does not exist.
            ConflictError: If this exact edge — same five columns — already exists.
            ValidationError: For a self-link or an unknown entity/link type.
        """
        source = _entity_or_raise(data.source_type, "source_type")
        target = _entity_or_raise(data.target_type, "target_type")
        link_type = _link_type_or_raise(data.link_type)
        if source is target and data.source_id == data.target_id:
            raise ValidationError("An object cannot link to itself.")
        await self._resolve_endpoint(source, data.source_id, owner)
        await self._resolve_endpoint(target, data.target_id, owner)
        # No pre-flight existence check: ``uq_knowledge_links_edge`` is the check,
        # and a read-then-write would lose the race it is meant to close.
        try:
            link = await self.links.create(
                owner_id=owner.id,
                source_type=source.value,
                source_id=data.source_id,
                target_type=target.value,
                target_id=data.target_id,
                link_type=link_type.value,
            )
        except IntegrityError as exc:
            raise ConflictError("That link already exists.") from exc
        await self._record(
            ActivityEvent.KNOWLEDGE_LINK_CREATED,
            owner=owner,
            metadata={"source_type": source.value, "link_type": link_type.value},
        )
        return link

    async def get_link(self, *, link_id: uuid.UUID, owner: User) -> KnowledgeLink:
        """Return one of the caller's edges, or 404.

        The ``owner_id`` in the query matters more here than on any other route:
        the edge's own row names no endpoint anywhere a foreign key could enforce
        ownership for it.
        """
        link = await self.links.get_by_id_for_user(link_id, owner.id)
        if link is None:
            raise NotFoundError(_LINK_NOT_FOUND)
        return link

    async def delete_link(self, *, link: KnowledgeLink, owner: User) -> None:
        """Remove one edge. Both endpoints survive — the edge was wrong, not them."""
        if link.owner_id != owner.id:
            raise NotFoundError(_LINK_NOT_FOUND)
        await self.links.delete(link)
        await self._record(
            ActivityEvent.KNOWLEDGE_LINK_REMOVED,
            owner=owner,
            metadata={"link_type": link.link_type},
        )

    # -- Graph and search ---------------------------------------------------

    async def graph(
        self,
        *,
        owner: User,
        limit: int = MAX_GRAPH_NODES,
        entity_type: KnowledgeEntityType | None = None,
    ) -> KnowledgeGraph:
        """Return the caller's graph as ``{nodes, edges}``, within a hard cap.

        **The cap is the feature.** The spec names "a graph that looks impressive
        but becomes unusable" as the failure mode, and a force-directed layout is
        where it shows up: a few hundred nodes is already a hairball no edge can
        be followed through. The router refuses a larger request with a 422 before
        it arrives; :data:`MAX_GRAPH_NODES` is enforced here too so a non-HTTP
        caller is bounded by the same rule rather than by the router's absence.

        **The whole graph is assembled in SQL** — one bounded query per
        participating table, then one edge query narrowed to the ids that
        survived — so nothing is read in full and filtered in Python.

        ``entity_type`` narrows the nodes and keeps only the edges whose *both*
        endpoints survive: an edge to a filtered-out node is dropped rather than
        drawn to nothing.
        """
        entity = _entity_or_none(entity_type)
        cap = min(limit, MAX_GRAPH_NODES)
        if cap < 1:
            raise ValidationError("limit must be at least 1.")
        payload = await self.links.graph_payload(
            owner.id, limit=cap, entity_type=entity.value if entity is not None else None
        )
        return KnowledgeGraph(
            nodes=[KnowledgeGraphNode(**node) for node in payload.nodes],
            edges=[KnowledgeGraphEdge(**edge) for edge in payload.edges],
            limit=cap,
            truncated=payload.truncated,
        )

    async def search(
        self,
        *,
        owner: User,
        query: str,
        entity_type: KnowledgeEntityType | None = None,
        limit: int = MAX_SEARCH_ROWS,
    ) -> KnowledgeSearchResult:
        """Search notes, concepts, resources and bookmarks in one call.

        **Grouped, not merged** — one list per entity type, so a client can render
        "3 notes, 1 concept" as two sections. This is deliberately not the unified
        cross-type search a later phase owns: ranking heterogeneous columns against
        each other needs a relevance model, and a fabricated one (concatenating the
        types and sorting by ``created_at``) would answer a different question from
        the one asked.

        **All four kinds are searched when no ``entity_type`` is given** —
        notes, concepts, resources *and* bookmarks. Bookmarks were previously
        unreachable here, because this method shared the graph's list of node
        types and a bookmark is not a node; the result was a permanently empty
        ``bookmarks`` group that a caller could not tell apart from a bookmark
        that matched nothing. See :data:`_SEARCH_TABLES`.

        **One bounded query per entity type**, never a ``SELECT *`` filtered in
        Python. ``ILIKE`` and not ``pg_trgm``: this is a portable build where the
        extension may not be installed, so relying on it would make search the
        first thing that breaks elsewhere. The cost is a scan that slows as a
        user's notes grow; a real deployment adds GIN trigram indexes on the same
        columns, which needs no column change and so no migration here.

        Raises:
            ValidationError: For an empty term, a term longer than
                :data:`MAX_SEARCH_LENGTH`, or a ``limit`` outside 1-20.
        """
        term = query.strip()
        if not term:
            raise ValidationError("q must not be blank.")
        if len(term) > MAX_SEARCH_LENGTH:
            raise ValidationError(f"q must be at most {MAX_SEARCH_LENGTH} characters.")
        if limit < 1 or limit > MAX_SEARCH_ROWS:
            raise ValidationError(f"limit must be between 1 and {MAX_SEARCH_ROWS}.")
        entity = _entity_or_none(entity_type)
        wanted = {entity.value} if entity is not None else set(_SEARCH_TABLES)

        result = KnowledgeSearchResult(query=term, limit=limit)
        if "note" in wanted:
            rows = await self.notes.search(owner.id, term, limit=limit)
            # Two statements for the whole matched page, not two per row: this
            # route is the one that matches up to MAX_SEARCH_ROWS of each kind in
            # a single call, so the per-row form cost 64 statements on a full
            # result set. Measured before and after in
            # tests/test_knowledge_query_efficiency.py.
            tags, counts = await self._note_joins(rows)
            result.notes = [
                _note_read_from(row, tags.get(row.id, []), counts.get(row.id, 0)) for row in rows
            ]
        if "concept" in wanted:
            rows = await self.concepts.search(owner.id, term, limit=limit)
            tags = await self._concept_joins(rows)
            result.concepts = [_concept_read_from(row, tags.get(row.id, [])) for row in rows]
        if "resource" in wanted:
            rows = await self.resources.search(owner.id, term, limit=limit)
            result.resources = [ResourceRead.model_validate(row) for row in rows]
        if "bookmark" in wanted:
            rows = await self.bookmarks.search(owner.id, term, limit=limit)
            result.bookmarks = [BookmarkRead.model_validate(row) for row in rows]
        return result

    # -- Internals ----------------------------------------------------------

    async def _note_page(
        self, rows: list[Note], total: int, limit: int, offset: int
    ) -> Page[NoteRead]:
        """Build a page of notes, filling the two join columns for the whole page.

        **Two queries for the page, not two per row.** ``tag_ids`` and
        ``revision_count`` live in other tables; asking for them one row at a time
        is the N+1 that makes a listing unusable at fifty rows.
        """
        ids = [row.id for row in rows]
        tags = await self.notes.list_tags_for_notes(ids)
        counts = await self.notes.revision_counts(ids)
        return Page[NoteRead](
            items=[
                _note_read_from(row, tags.get(row.id, []), counts.get(row.id, 0)) for row in rows
            ],
            meta=PageMeta(total=total, limit=limit, offset=offset),
        )

    async def _concept_page(
        self, rows: list[Concept], total: int, limit: int, offset: int
    ) -> Page[ConceptRead]:
        """Build a page of concepts, filling ``tag_ids`` for the whole page."""
        tags = await self.concepts.list_tags_for_concepts([row.id for row in rows])
        return Page[ConceptRead](
            items=[_concept_read_from(row, tags.get(row.id, [])) for row in rows],
            meta=PageMeta(total=total, limit=limit, offset=offset),
        )

    async def _note_joins(
        self, rows: Sequence[Note]
    ) -> tuple[dict[uuid.UUID, list[Tag]], dict[uuid.UUID, int]]:
        """The two join columns for a whole batch of notes, in two statements.

        **The N+1 this exists to prevent.** ``tag_ids`` and ``revision_count`` live
        in ``note_tags`` and ``note_revisions``, so a note cannot be serialised
        without two further reads. Asking for them one row at a time cost two
        statements per matched note, and the search route — which matches up to
        :data:`MAX_SEARCH_ROWS` rows of each kind in one call — is where that was
        paid: 1 note and 1 concept cost 7 statements, 20 and 20 cost 64.

        Two statements for any number of rows, and none at all for an empty batch —
        both repositories short-circuit on an empty id list rather than emitting
        the pointless ``IN ()``. A row with no tags and no revisions is simply
        absent from both maps, which is the truth about it.
        """
        ids = [row.id for row in rows]
        return await self.notes.list_tags_for_notes(ids), await self.notes.revision_counts(ids)

    async def _concept_joins(self, rows: Sequence[Concept]) -> dict[uuid.UUID, list[Tag]]:
        """``tag_ids`` for a whole batch of concepts, in one statement."""
        return await self.concepts.list_tags_for_concepts([row.id for row in rows])

    async def _annotate(self, note: Note) -> Note:
        """Put the two answers that live in other tables onto the row itself.

        **``revision_count`` was a false zero on every single-note route.**
        :attr:`NoteRead.revision_count` and ``tag_ids`` are not columns on
        ``notes``, and Pydantic fills a field from its default when the object it
        is validating does not carry it. The list and search paths build
        :class:`NoteRead` explicitly through :func:`_note_read_from`, so they were
        right; the routes that hand a note straight back — the read, the PATCH,
        ``/publish``, ``/archive``, ``/restore`` and ``/restore-revision`` —
        returned the bare row, and every one of them answered ``revision_count:
        0`` for a note holding fifty revisions. Zero is the answer for a note
        that has *never* been edited, and it is not the answer for one that has,
        which is exactly the confusion this repository's own rule forbids: an
        absence of measurement must not be rendered as a measured zero.

        Pydantic reads an attribute off the source object before it falls back to
        the field default, so setting the two names here is what the response
        model picks up — two queries, the same pair :meth:`_note_joins` runs.
        They are not mapped columns and are never written: nothing flushes after
        this runs, and :meth:`NoteRepository.update_fields` allowlists every
        column a write may touch.

        :meth:`create_note` is the one note-returning method that does not call
        this, and it is not an oversight: a note that has just been created has
        no revisions and no tags, which is precisely what the defaults say.

        Args:
            note: The row about to be serialised.

        Returns:
            The same row, carrying ``revision_count`` and ``tag_ids``.
        """
        tags = await self.notes.list_tags_for_notes([note.id])
        counts = await self.notes.revision_counts([note.id])
        note.tag_ids = [tag.id for tag in tags.get(note.id, [])]
        note.revision_count = counts.get(note.id, 0)
        return note

    async def _note_read(self, note: Note) -> NoteRead:
        """Build a :class:`NoteRead` from a row plus the two joins it needs.

        ``tag_ids`` and ``revision_count`` are answers from other tables, which is
        why :class:`NoteRead` cannot be produced by ``model_validate`` on the row
        alone — the counts come from one query each for the whole batch rather than
        one per row. :meth:`_annotate` is the same pair of queries for the one-row
        case, and exists because the single-note routes return the row rather than
        a built model.

        **One row only.** A caller holding several has to ask for them together —
        :meth:`_note_joins` once, then :func:`_note_read_from` per row — which is
        what :meth:`_note_page` and :meth:`search` do. Reaching for this in a loop
        is the N+1 it used to be, and it is the reason it is not the module's
        serialiser.
        """
        tags, counts = await self._note_joins([note])
        return _note_read_from(note, tags.get(note.id, []), counts.get(note.id, 0))

    async def _concept_read(self, concept: Concept) -> ConceptRead:
        """Build a :class:`ConceptRead`, whose ``tag_ids`` live in ``concept_tags``.

        One row only, for the reason :meth:`_note_read` gives.
        """
        tags = await self._concept_joins([concept])
        return _concept_read_from(concept, tags.get(concept.id, []))

    async def _resolve_endpoint(
        self, entity: KnowledgeEntityType, entity_id: uuid.UUID | None, owner: User
    ) -> Note | Concept | Resource:
        """Resolve one polymorphic endpoint through an owner-scoped query.

        **This is the security model of ``knowledge_links``.** The endpoint columns
        carry no foreign key and the request carries no owner, so the only thing
        standing between a caller and another account's edges is that this lookup
        is scoped by ``owner.id``. It raises the *same* ``NotFoundError`` as a
        nonexistent id, so it cannot become an existence oracle either.
        """
        if entity_id is None:
            raise ValidationError("An endpoint id is required.")
        if entity is KnowledgeEntityType.NOTE:
            row: Note | Concept | Resource | None = await self.notes.get_by_id_for_user(
                entity_id, owner.id
            )
        elif entity is KnowledgeEntityType.CONCEPT:
            row = await self.concepts.get_by_id_for_user(entity_id, owner.id)
        else:
            row = await self.resources.get_by_id_for_user(entity_id, owner.id)
        if row is None:
            raise NotFoundError(_ENDPOINT_NOT_FOUND)
        return row

    async def _own_document(self, document_id: uuid.UUID | None, owner: User) -> uuid.UUID | None:
        """Resolve a ``document_id`` through the scoped lookup, or ``None``."""
        if document_id is None:
            return None
        document = await self.documents.get_by_id_for_user(document_id, owner.id)
        if document is None:
            raise NotFoundError(_DOCUMENT_NOT_FOUND)
        return document.id

    async def _own_category(
        self, parent_id: uuid.UUID | None, owner: User, *, what: str
    ) -> Category | None:
        """Resolve a ``parent_id`` through the scoped lookup, returning the row.

        Returns the row rather than the id because the cycle check has to walk
        *from* the proposed parent, not merely compare two uuids.

        Raises:
            NotFoundError: If the parent is not the caller's. 404 and not 403, like
                every other id on this surface.
        """
        if parent_id is None:
            return None
        parent = await self.categories.get_by_id_for_user(parent_id, owner.id)
        if parent is None:
            raise NotFoundError(f"That {what} was not found.")
        return parent

    async def _snapshot(self, note: Note) -> None:
        """Record the note's current text as a revision and prune the overflow.

        **A copy of the state being displaced**, which is what makes an edit
        reversible: the write that overwrites the text is the same write that
        preserved it.

        **Every call snapshots, whatever the note currently holds.** An earlier
        version skipped this for a note with no body and no summary, on the
        reasoning that "a revision of an empty note describes nothing". But
        :attr:`Note.title` is ``NOT NULL`` and is one of the three columns a
        revision stores, so the note that guard spared was not empty — it was a
        titled note whose body had not been written yet, and the edit it skipped
        was a *rename*. The title a user typed before the first keystroke of the
        body was therefore destroyed by the rename with nothing to restore it
        from, while the caller had been told the change was meaningful. The
        creation state is exactly what the first edit wants back, so it is
        written; the history is bounded at
        :data:`~app.models.knowledge.MAX_REVISIONS_PER_NOTE` regardless.
        """
        await self.notes.create_revision(
            note_id=note.id,
            owner_id=note.owner_id,
            title=note.title,
            content=note.content,
            summary=note.summary,
        )
        await self.notes.prune_revisions(note.id, keep=MAX_REVISIONS_PER_NOTE)

    def _own_note(self, note: Note, owner: User | None) -> None:
        """Refuse a note that is not the caller's, when the caller is known.

        A tripwire, not the authorisation: every note reaching a mutating method
        was resolved through :meth:`get_note`, whose query is scoped by
        ``owner_id``. It raises the identical ``NotFoundError`` so it cannot become
        an oracle either.
        """
        if owner is not None and note.owner_id != owner.id:
            raise NotFoundError(_NOTE_NOT_FOUND)

    def _own_row(self, row: Any, owner: User, message: str) -> None:
        """Refuse an already-loaded row that is not the caller's. See :meth:`_own_note`."""
        if row.owner_id != owner.id:
            raise NotFoundError(message)

    async def _record(
        self, event: ActivityEvent, *, owner: User, metadata: Mapping[str, object] | None = None
    ) -> None:
        """Write one activity event, or do nothing when no feed is configured.

        Never raises. History is observability of the work, not a precondition for
        doing it, and a history table that is unavailable must not stop a user
        publishing a note. Field *names* are recorded rather than values, for the
        reason ``ProjectService`` records them: a note's content is user prose and
        an activity feed is not the place to copy it a second time.
        """
        if self.activity is None:
            return
        await self.activity.record(event.value, user_id=owner.id, metadata=metadata)


def _note_read_from(note: Note, tags: Sequence[Tag], revision_count: int) -> NoteRead:
    """One :class:`NoteRead` from a row and the two answers read about it.

    **Pure, and shared by every path that serialises more than one note.** The
    page builder, the search route and the single-note helper all reach this, so
    there is one definition of "a note with its joins filled in" and no route
    that quietly forgets a field. The joins arrive as arguments rather than being
    read here — that is the whole point, since reading them is what used to cost
    two statements per row.
    """
    return NoteRead(
        id=note.id,
        owner_id=note.owner_id,
        title=note.title,
        content=note.content,
        summary=note.summary,
        status=note.status,
        document_id=note.document_id,
        created_at=note.created_at,
        updated_at=note.updated_at,
        tag_ids=[tag.id for tag in tags],
        revision_count=revision_count,
        is_archived=note.status == NoteStatus.ARCHIVED.value,
    )


def _concept_read_from(concept: Concept, tags: Sequence[Tag]) -> ConceptRead:
    """One :class:`ConceptRead` from a row and its tags. See :func:`_note_read_from`."""
    return ConceptRead(
        id=concept.id,
        owner_id=concept.owner_id,
        name=concept.name,
        description=concept.description,
        created_at=concept.created_at,
        updated_at=concept.updated_at,
        tag_ids=[tag.id for tag in tags],
    )


def _refuse_null(fields: Mapping[str, object], *, entity: str) -> None:
    """Refuse a PATCH that sets a ``NOT NULL`` column to JSON ``null``.

    **This is the 422 a client wrote the request for, and it is the service's
    job rather than the schema's.** Every ``*Update`` model types its required
    text fields as ``str | None`` — an absent key and a ``None`` are the same
    value to Pydantic unless every field is given a default, and giving
    ``title`` a default would make it optional rather than required on the
    create path — so ``{"title": null}`` passes validation and reaches
    :meth:`NoteRepository.update_fields`. There it sets a ``NOT NULL`` column to
    ``None`` and the database answers with a ``NotNullViolation`` the service
    does not catch: a 500 with a traceback, for a request whose only fault is a
    missing quote.

    The alternative — coercing ``None`` to the column's default — invents a
    state the client did not ask for. ``content`` would become ``""``, which
    reads as "the note has no body" and silently destroys the prose the note
    had; and for ``title`` there is no default at all, only a violation. Refusing
    is the fail-closed answer, and it is the same one :meth:`_check_sort` gives a
    sort key it cannot resolve.

    Only the columns that cannot hold ``None`` are listed in
    :data:`_NOT_CLEARABLE_FIELDS`. A nullable one — ``summary``, ``description``,
    ``parent_id`` — is cleared by ``null`` on purpose, and that request reaches
    the repository untouched.

    Args:
        fields: The deconstructed update payload, before anything is written.
        entity: The kind of row being updated, naming the entry in
            :data:`_NOT_CLEARABLE_FIELDS`.

    Raises:
        ValidationError: If any named field is present and ``None``.
    """
    required = _NOT_CLEARABLE_FIELDS[entity]
    cleared = sorted(name for name in required if name in fields and fields[name] is None)
    if cleared:
        raise ValidationError(
            f"{entity} {', '.join(cleared)} cannot be null.",
            details={"fields": cleared},
        )


def _page(rows: list, total: int, limit: int, offset: int, model):
    """Wrap a result set and its unpaginated total in a :class:`Page`."""
    return Page[model](
        items=[model.model_validate(row) for row in rows],
        meta=PageMeta(total=total, limit=limit, offset=offset),
    )


def _check_window(*, limit: int, offset: int) -> None:
    """Reject a page window that is not one.

    A negative offset is a silent wrong answer in PostgreSQL, and a zero limit
    returns an empty page whose ``total`` matches nothing anybody asked for.
    """
    if limit < 1:
        raise ValidationError("limit must be at least 1.")
    if offset < 0:
        raise ValidationError("offset must be zero or greater.")


def _check_sort(entity: str, sort: str, order: str) -> str:
    """Validate a sort key and direction against the entity's allowlist.

    ``ORDER BY`` takes an expression rather than a bound parameter, so an
    unvalidated name reaching the repository would be SQL injection behind a query
    parameter. This layer raises the 422 a *request* deserves; the repository
    raises the ``ValueError`` a *programming* error deserves. Neither falls back to
    a default column, because a silent fallback answers a request for one ordering
    with a plausible ordering of another.
    """
    allowed = _SORT_KEYS[entity]
    if sort not in allowed:
        raise ValidationError(
            f"Cannot sort {entity} by {sort!r}.", details={"allowed": sorted(allowed)}
        )
    if order not in _SORT_ORDERS:
        raise ValidationError(
            f"Cannot sort {entity} {order!r}.", details={"allowed": sorted(_SORT_ORDERS)}
        )
    return sort


def _status_or_none(value: NoteStatus | str | None) -> str | None:
    """Validate a note status filter, or return ``None`` for no filter.

    The enum helper raises ``ValueError``, which the API layer would turn into a
    500. A filter naming a status that does not exist is the caller's mistake, so
    it is translated into the 422 that answers it.
    """
    if value is None:
        return None
    try:
        return validate_note_status(value).value
    except ValueError:
        raise ValidationError(f"Unknown status: {value!r}.") from None


def _entity_or_none(value: KnowledgeEntityType | str | None) -> KnowledgeEntityType | None:
    """Coerce an entity type, or pass ``None`` through as "no filter"."""
    if value is None:
        return None
    try:
        return validate_knowledge_entity_type(value)
    except ValueError:
        raise ValidationError(f"Unknown entity type: {value!r}.") from None


def _entity_or_raise(value: KnowledgeEntityType | str, field: str) -> KnowledgeEntityType:
    """Coerce a required entity type or raise the 422 that answers it."""
    try:
        return validate_knowledge_entity_type(value)
    except ValueError:
        raise ValidationError(f"Unknown {field}: {value!r}.") from None


def _link_type_or_none(value: KnowledgeLinkType | str | None) -> str | None:
    """Coerce an optional link-type filter, or ``None``."""
    if value is None:
        return None
    return _link_type_or_raise(value).value


def _link_type_or_raise(value: KnowledgeLinkType | str) -> KnowledgeLinkType:
    """Coerce a required link type or raise the 422 that answers it."""
    try:
        return validate_knowledge_link_type(value)
    except ValueError:
        raise ValidationError(f"Unknown link type: {value!r}.") from None


def _resource_type_or_raise(value: ResourceType | str) -> ResourceType:
    """Coerce a resource type or raise the 422 that answers it."""
    try:
        return validate_resource_type(value)
    except ValueError:
        raise ValidationError(f"Unknown resource type: {value!r}.") from None


def _check_url(value: str | None, *, required: bool) -> str | None:
    """Validate a URL's scheme and shape, or refuse it.

    **An allowlist of two, not a denylist.** ``javascript:`` is the reason this
    check exists — a bookmark list renders links, so a stored ``javascript:`` URL
    is a stored script the next person to open the list runs. A denylist would
    have to be extended every time a new dangerous scheme is invented; ``http``
    and ``https`` are the only two this product renders, and everything else is
    refused until there is a reason to render it.

    Raises:
        ValidationError: For a missing required URL, a scheme outside the
            allowlist, or a URL with no host.
    """
    if value is None or not value.strip():
        if required:
            raise ValidationError("A url is required.")
        return None
    url = value.strip()
    parts = urlsplit(url)
    if parts.scheme.lower() not in _ALLOWED_URL_SCHEMES:
        raise ValidationError(
            "url must be an http or https address.",
            details={"allowed": sorted(_ALLOWED_URL_SCHEMES)},
        )
    if not parts.netloc:
        raise ValidationError("url must include a host.")
    return url
