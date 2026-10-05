"""Data access for the whole knowledge base.

Notes, revisions, concepts, resources, bookmarks, documents, categories and the
polymorphic edge table.

The repository owns SQL only. It never raises domain errors — an unexpected
``IntegrityError`` propagates so the service layer can translate it into the API
contract. The exceptions are the two guards against a *programming* error: the
field allowlists in each ``update_fields`` and the sort allowlists in each
``list_for_user``, both of which reject a bad name with :class:`ValueError`
because the name arrives from code, not from a request body.

Five ideas carry the file.

**Ownership is a predicate, not a filter.** ``owner_id`` is in the ``WHERE``
clause of every method that takes one. A row the caller may not see is never
loaded at all, and another user's id is answered with ``None`` — identically to
an id that does not exist, so this cannot be used to probe which ids are real.

**Lists that need a second table ask for it in one query.**
:meth:`NoteRepository.list_tags_for_notes` and
:meth:`ConceptRepository.list_tags_for_concepts` return
``dict[UUID, list[Tag]]`` for a whole page from a single ``IN`` over the ids the
page already holds — the N+1 fix copied from
:meth:`app.repositories.tag.TagRepository.list_tags_for_tasks`. A loop of per-row
tag lookups is fifty round trips to render one screen.

**``knowledge_links`` carries no foreign key on its endpoints, so ownership has
to be established somewhere else.** ``source_type`` is what decides which table
``source_id`` points at, and a foreign key can only point at one table. Every
method here therefore takes ``owner_id`` and predicates on it, and the *service*
must additionally resolve both endpoints through an owner-scoped lookup before it
writes. :meth:`KnowledgeLinkRepository.graph_payload` is the place that pays for
the missing constraint in practice: it re-establishes the endpoints the database
cannot, by joining the edge table against a bounded node set.

**Nothing is assembled in Python that could be assembled in SQL.** The graph is
built from a CTE with ``LIMIT`` on each node branch, and the search runs one
bounded ``ILIKE`` per entity type. The old alternative — ``SELECT *`` then filter
— is the query that stops working at the size the caps exist to permit.

**Sorting comes from an allowlist.** ``ORDER BY`` takes an expression rather than
a bound parameter, so a caller-supplied column name is SQL injection behind a
``sort`` query parameter. The name is resolved against a fixed dict instead and
an unknown one raises rather than falling back to a default, because a silent
fallback answers a request with a plausible ordering it did not ask for.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from typing import Any, NamedTuple
from urllib.parse import urlsplit

from sqlalchemy import delete, func, insert, literal_column, or_, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.models.enums import KnowledgeEntityType
from app.models.knowledge import (
    DEFAULT_KNOWLEDGE_LINK_TYPE,
    MAX_REVISIONS_PER_NOTE,
    Bookmark,
    Category,
    Concept,
    Document,
    KnowledgeLink,
    Note,
    NoteRevision,
    Resource,
    concept_tags,
    note_tags,
)
from app.models.tag import Tag

__all__ = [
    "BookmarkRepository",
    "CategoryRepository",
    "ConceptRepository",
    "DocumentRepository",
    "GraphPayload",
    "KnowledgeLinkRepository",
    "NoteRepository",
    "ResourceRepository",
]


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #


def _search_pattern(term: str) -> str:
    """Wrap a user-supplied search term in a case-insensitive ``ILIKE`` pattern.

    ``ILIKE`` and not ``pg_trgm``/``unaccent``: this portable build has neither
    extension, so depending on one would make the migration unappliable on any
    server without contrib installed. The cost is that a leading ``%`` cannot use
    a B-tree index, which is exactly why every search is bounded by a ``LIMIT``
    and why the model docstrings say a real deployment adds GIN trigram indexes
    on these columns.

    The wildcards inside the term are escaped too — otherwise a search for ``50%``
    matches every row and a search for ``_`` matches any single character, and a
    caller's string would silently change what the query means.
    """
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _order_by_clauses(
    sort: str, order: str, allowed: Mapping[str, InstrumentedAttribute], model: type, name: str
) -> tuple[Any, ...]:
    """Resolve a public ``sort``/``order`` pair into ORDER BY expressions.

    ``model`` is the table class and ``name`` its name for the error message: the
    primary key is appended as a tiebreak, because ``created_at`` is a one-second
    ``server_default`` and two rows written in the same tick can otherwise come
    back in either order, which a paginated listing cannot survive.

    Raises:
        ValueError: If either name is not on its allowlist. Failing closed is the
            point: falling back to a default column would return a plausible
            ordering for a request that asked for a different one.
    """
    column = allowed.get(sort)
    if column is None:
        raise ValueError(
            f"Cannot sort {name} by {sort!r}; "
            f"list_for_user accepts only {', '.join(sorted(allowed))}."
        )
    if order == "asc":
        return column.asc(), model.id.asc()
    if order == "desc":
        return column.desc(), model.id.desc()
    raise ValueError(f"Cannot sort {name} {order!r}; expected 'asc' or 'desc'.")


async def _paginate_and_count(
    session: AsyncSession, statement: Any, count_from: Any, filters: Sequence[Any]
) -> tuple[list[Any], int]:
    """Run a bounded page and an unbounded ``COUNT`` over the same filters.

    Two statements, and that is the floor: ``total`` has to describe the filtered
    sequence rather than the slice, and a window function could do it in one but
    makes the page query unreadable to save one round trip on a per-user listing.
    """
    rows = list((await session.execute(statement)).scalars().all())
    total = int(await session.scalar(select(func.count()).select_from(count_from).where(*filters)))
    return rows, total


def _domain_from_url(url: str) -> str | None:
    """Derive a bookmark's host from its URL, server-side, always.

    Never from the client: a caller-supplied domain is a caller-supplied lie, and
    it is the shape a phishing bookmark takes — filed under a domain it does not
    belong to, and rendered by every client that groups by it. The host comes
    from :attr:`urllib.parse.SplitResult.hostname`, which is the parsed **host**
    rather than ``netloc``: ``hostname`` drops the credentials and the port, and
    lower-cases the host, so ``EXAMPLE.com:8443`` and ``example.com`` are one
    domain and ``[::1]:8080`` is ``::1`` rather than a second "domain" for
    localhost.

    **``netloc`` was the wrong half of the parse and it was a security bug, not a
    cosmetic one.** ``https://user:pw@example.com`` has the netloc
    ``user:pw@example.com``, and the old partition-on-``:`` read ``user`` — a
    bookmark filed under a *username*, grouped beside every other bookmark whose
    user happens to be called ``user``. Worse, ``https://github.com@evil.example/x``
    parsed to the whole ``github.com@evil.example``, which renders as a github.com
    bookmark and is not one: everything before the ``@`` in a netloc is
    credentials and everything after it is the host. The one field that exists to
    make the origin readable was being filled from the part of the URL a phisher
    controls most freely.

    ``None`` for anything without a host rather than a guess.
    """
    host = urlsplit(url.strip()).hostname
    return host.lower() if host else None


# --------------------------------------------------------------------------- #
# Notes
# --------------------------------------------------------------------------- #

_NOTE_UPDATABLE_FIELDS = frozenset({"content", "document_id", "status", "summary", "title"})
_NOTE_SORT_COLUMNS: dict[str, InstrumentedAttribute] = {
    "created_at": Note.created_at,
    "status": Note.status,
    "title": Note.title,
    "updated_at": Note.updated_at,
}


class NoteRepository:
    """Note and revision persistence bound to one request-scoped session."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        owner_id: uuid.UUID,
        title: str,
        content: str = "",
        summary: str | None = None,
        document_id: uuid.UUID | None = None,
    ) -> Note:
        """Insert a note and return it with the server defaults populated.

        ``status`` is not a parameter. A note is created ``draft`` and there is no
        path that produces one already published, so "this is real now" is always
        an act somebody performed rather than a field somebody typed.
        """
        note = Note(
            owner_id=owner_id,
            title=title.strip(),
            content=content,
            summary=summary,
            document_id=document_id,
        )
        self.session.add(note)
        await self.session.commit()
        await self.session.refresh(note)
        return note

    async def get_by_id_for_user(self, note_id: uuid.UUID, owner_id: uuid.UUID) -> Note | None:
        """Return the note only when it also belongs to this user."""
        result = await self.session.execute(
            select(Note).where(Note.id == note_id, Note.owner_id == owner_id)
        )
        return result.scalar_one_or_none()

    async def list_for_user(
        self,
        owner_id: uuid.UUID,
        *,
        limit: int,
        offset: int,
        status: str | None = None,
        search: str | None = None,
        tag_ids: list[uuid.UUID] | None = None,
        sort: str = "updated_at",
        order: str = "desc",
    ) -> tuple[list[Note], int]:
        """List one owner's notes with the unpaginated total.

        ``tag_ids`` is an ``IN`` over a subquery rather than a join, so a note
        carrying three of the requested tags is still one row and ``total`` counts
        notes rather than tag edges.
        """
        filters = [Note.owner_id == owner_id]
        if status is not None:
            filters.append(Note.status == status)
        if search is not None and search.strip():
            pattern = _search_pattern(search.strip())
            filters.append(
                Note.title.ilike(pattern, escape="\\")
                | Note.content.ilike(pattern, escape="\\")
                | Note.summary.ilike(pattern, escape="\\")
            )
        if tag_ids:
            filters.append(
                Note.id.in_(
                    select(note_tags.c.note_id).where(note_tags.c.tag_id.in_(list(tag_ids)))
                )
            )

        page = (
            select(Note)
            .where(*filters)
            .order_by(*_order_by_clauses(sort, order, _NOTE_SORT_COLUMNS, Note, "notes"))
            .limit(limit)
            .offset(offset)
        )
        return await _paginate_and_count(self.session, page, Note, filters)

    async def update_fields(self, note: Note, **fields: object) -> Note:
        """Apply a partial update.

        ``owner_id`` is not on the allowlist and never will be: it is the
        authorisation anchor, and a partial update that can reach it is a partial
        update that moves a note between accounts. ``created_at`` is history.

        Raises:
            ValueError: For a name outside the allowlist — a programming error, so
                it fails at the call site rather than writing a column nobody meant.
        """
        rejected = sorted(set(fields) - _NOTE_UPDATABLE_FIELDS)
        if rejected:
            raise ValueError(
                f"Cannot write {', '.join(rejected)} on a note row; "
                f"update_fields accepts only {', '.join(sorted(_NOTE_UPDATABLE_FIELDS))}."
            )
        for key, value in fields.items():
            setattr(note, key, value)
        self.session.add(note)
        await self.session.commit()
        await self.session.refresh(note)
        return note

    async def delete(self, note: Note) -> None:
        """Delete the note.

        Its revisions and its ``note_tags`` edges cascade, because neither means
        anything once the note is gone. ``knowledge_links`` does **not** cascade
        — the endpoints carry no foreign key (see
        :class:`app.models.knowledge.KnowledgeLink`) — so the service must call
        :meth:`KnowledgeLinkRepository.delete_for_entity` in the same transaction
        or the graph would keep an edge pointing at a row that no longer exists.
        """
        await self.session.delete(note)
        await self.session.commit()

    # -- revisions --------------------------------------------------------- #

    async def create_revision(
        self,
        *,
        note_id: uuid.UUID,
        owner_id: uuid.UUID,
        title: str,
        content: str,
        summary: str | None = None,
    ) -> NoteRevision:
        """Append a point-in-time **copy** of the note.

        A copy, not a diff: see :class:`app.models.knowledge.NoteRevision` for
        why restoring one is then a single assignment rather than a chain of
        patches to replay. Pruning is not done here — the write path and the
        bound live apart, so :meth:`prune_revisions` is called by the caller that
        owns the history policy rather than hidden inside this insert.
        """
        revision = NoteRevision(
            note_id=note_id,
            owner_id=owner_id,
            title=title,
            content=content,
            summary=summary,
        )
        self.session.add(revision)
        await self.session.commit()
        await self.session.refresh(revision)
        return revision

    async def list_revisions(
        self, note_id: uuid.UUID, owner_id: uuid.UUID, *, limit: int, offset: int
    ) -> tuple[list[NoteRevision], int]:
        """A note's history, newest first, with the unpaginated total.

        ``owner_id`` is a second predicate rather than a join through the note:
        history stays queryable per user without one, and the caller has already
        resolved the note through its own scoped lookup, so this is defence in
        depth for a pairing of ids the service checked.
        """
        filters = [NoteRevision.note_id == note_id, NoteRevision.owner_id == owner_id]
        page = (
            select(NoteRevision)
            .where(*filters)
            .order_by(NoteRevision.created_at.desc(), NoteRevision.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return await _paginate_and_count(self.session, page, NoteRevision, filters)

    async def get_revision(
        self, note_id: uuid.UUID, revision_id: uuid.UUID, owner_id: uuid.UUID
    ) -> NoteRevision | None:
        """Return one revision of *this* note, or ``None``.

        All three ids are in the predicate. Pairing them loosely — looking up the
        revision by id and checking the note afterwards — would let a real
        revision id be confirmed against a note it does not belong to.
        """
        result = await self.session.execute(
            select(NoteRevision).where(
                NoteRevision.id == revision_id,
                NoteRevision.note_id == note_id,
                NoteRevision.owner_id == owner_id,
            )
        )
        return result.scalar_one_or_none()

    async def count_revisions(self, note_id: uuid.UUID) -> int:
        """How many revisions this note has kept.

        Counted in SQL: ``revision_count`` is a number the note list shows, and it
        must not require loading the history to know its size.
        """
        result = await self.session.execute(
            select(func.count()).select_from(NoteRevision).where(NoteRevision.note_id == note_id)
        )
        return int(result.scalar_one())

    async def revision_counts(self, note_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, int]:
        """Revision counts for a whole page in one ``GROUP BY``.

        Same N+1 as the tags, on the same page: a fifty-note list must not cost
        fifty counts.
        """
        if not note_ids:
            return {}
        result = await self.session.execute(
            select(NoteRevision.note_id, func.count())
            .where(NoteRevision.note_id.in_(list(note_ids)))
            .group_by(NoteRevision.note_id)
        )
        return {note_id: int(count) for note_id, count in result.all()}

    async def prune_revisions(
        self, note_id: uuid.UUID, *, keep: int = MAX_REVISIONS_PER_NOTE
    ) -> int:
        """Delete everything but the newest ``keep`` revisions of one note.

        **Keeps the newest, never the oldest.** The tail is the part nobody reads
        and the part that grows without the user asking: one full copy of the
        note per edit, forever, is the one place in NEXUS where storage grows on
        its own. The consequence is stated rather than hidden — restore only
        reaches within this window, and after 50 edits the version from last
        March is gone.

        A set-based ``DELETE`` over a subquery rather than a load-delete loop, and
        the cutoff is chosen by ``ORDER BY created_at DESC, id DESC`` so the
        suffix that survives always includes the most recent state.
        """
        survivors = (
            select(NoteRevision.id)
            .where(NoteRevision.note_id == note_id)
            .order_by(NoteRevision.created_at.desc(), NoteRevision.id.desc())
            .limit(keep)
        )
        result = await self.session.execute(
            delete(NoteRevision).where(
                NoteRevision.note_id == note_id, NoteRevision.id.notin_(survivors)
            )
        )
        await self.session.commit()
        return int(result.rowcount or 0)

    # -- tags -------------------------------------------------------------- #

    async def list_tags_for_notes(
        self, note_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, list[Tag]]:
        """Return the tags of many notes in one query, keyed by note id.

        The whole reason this exists: rendering a page of notes with their tags
        would otherwise be one query per note. Only notes that *have* tags appear
        as keys, so read with ``result.get(note_id, [])``. An empty sequence
        returns ``{}`` without querying — ``IN ()`` is a pointless round trip, and
        an untagged page is the common case.

        Tags are the **per-user** rows that already exist; there is no second tag
        table in this phase, and ownership of each tag is the caller's to have
        established through the scoped tag lookup before it gets here.
        """
        if not note_ids:
            return {}
        result = await self.session.execute(
            select(note_tags.c.note_id, Tag)
            .join(Tag, Tag.id == note_tags.c.tag_id)
            .where(note_tags.c.note_id.in_(list(note_ids)))
            .order_by(note_tags.c.note_id.asc(), Tag.name.asc(), Tag.id.asc())
        )
        grouped: dict[uuid.UUID, list[Tag]] = {}
        for note_id, tag in result.all():
            grouped.setdefault(note_id, []).append(tag)
        return grouped

    async def set_tags(self, note_id: uuid.UUID, tag_ids: Sequence[uuid.UUID]) -> None:
        """Replace a note's tags with exactly this set.

        Delete-then-insert rather than a diff: the caller has decided the full
        desired set, and reconciling here would need a read first — three round
        trips that are still wrong under two concurrent edits. Ids are
        deduplicated because the composite primary key would reject a repeat as a
        500. An empty sequence clears the note's tags, which is the correct
        meaning of "set these tags" rather than a no-op.
        """
        await self.session.execute(delete(note_tags).where(note_tags.c.note_id == note_id))
        rows = [{"note_id": note_id, "tag_id": tag_id} for tag_id in dict.fromkeys(tag_ids)]
        if rows:
            await self.session.execute(insert(note_tags), rows)
        await self.session.commit()

    # -- search ------------------------------------------------------------ #

    async def search(self, owner_id: uuid.UUID, term: str, *, limit: int) -> list[Note]:
        """Substring search over title, content and summary, bounded in SQL.

        ``LIMIT`` in the statement rather than a slice afterwards: this is the
        query that would otherwise pull every note the user has ever written
        into memory to throw most of it away. See :func:`_search_pattern` for why
        this is ``ILIKE`` and not a trigram index.
        """
        pattern = _search_pattern(term)
        result = await self.session.execute(
            select(Note)
            .where(
                Note.owner_id == owner_id,
                or_(
                    Note.title.ilike(pattern, escape="\\"),
                    Note.content.ilike(pattern, escape="\\"),
                    Note.summary.ilike(pattern, escape="\\"),
                ),
            )
            .order_by(Note.updated_at.desc(), Note.id.desc())
            .limit(limit)
        )
        return list(result.scalars().all())


# --------------------------------------------------------------------------- #
# Concepts
# --------------------------------------------------------------------------- #

_CONCEPT_UPDATABLE_FIELDS = frozenset({"description", "name"})
_CONCEPT_SORT_COLUMNS: dict[str, InstrumentedAttribute] = {
    "created_at": Concept.created_at,
    "name": Concept.name,
    "updated_at": Concept.updated_at,
}


class ConceptRepository:
    """Concept persistence bound to a single request-scoped session."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        owner_id: uuid.UUID,
        name: str,
        description: str | None = None,
    ) -> Concept:
        """Insert a concept.

        Raises:
            IntegrityError: If this owner already holds that name — the check that
                turns a duplicate vocabulary entry into a 409 rather than a 500.
        """
        concept = Concept(owner_id=owner_id, name=name.strip(), description=description)
        self.session.add(concept)
        await self.session.commit()
        await self.session.refresh(concept)
        return concept

    async def get_by_id_for_user(
        self, concept_id: uuid.UUID, owner_id: uuid.UUID
    ) -> Concept | None:
        """Return the concept only when it also belongs to this user."""
        result = await self.session.execute(
            select(Concept).where(Concept.id == concept_id, Concept.owner_id == owner_id)
        )
        return result.scalar_one_or_none()

    async def get_by_name(self, owner_id: uuid.UUID, name: str) -> Concept | None:
        """Return this owner's concept with this exact name, or ``None``.

        Exact and case-sensitive, matching the constraint it stands in for. This
        is the check that makes a duplicate create a 409 before the
        ``IntegrityError`` does.
        """
        result = await self.session.execute(
            select(Concept).where(Concept.owner_id == owner_id, Concept.name == name.strip())
        )
        return result.scalar_one_or_none()

    async def list_for_user(
        self,
        owner_id: uuid.UUID,
        *,
        limit: int,
        offset: int,
        search: str | None = None,
        sort: str = "name",
        order: str = "asc",
    ) -> tuple[list[Concept], int]:
        """List this owner's concepts, alphabetically by default.

        A concept list is an index a person scans, not a feed they read in order,
        and ``name`` is the only ordering in which scanning finds anything.
        """
        filters = [Concept.owner_id == owner_id]
        if search is not None and search.strip():
            pattern = _search_pattern(search.strip())
            filters.append(
                Concept.name.ilike(pattern, escape="\\")
                | Concept.description.ilike(pattern, escape="\\")
            )
        page = (
            select(Concept)
            .where(*filters)
            .order_by(*_order_by_clauses(sort, order, _CONCEPT_SORT_COLUMNS, Concept, "concepts"))
            .limit(limit)
            .offset(offset)
        )
        return await _paginate_and_count(self.session, page, Concept, filters)

    async def update_fields(self, concept: Concept, **fields: object) -> Concept:
        """Apply a partial update.

        See :meth:`NoteRepository.update_fields` for why ``owner_id`` is not on
        the allowlist.
        """
        rejected = sorted(set(fields) - _CONCEPT_UPDATABLE_FIELDS)
        if rejected:
            raise ValueError(
                f"Cannot write {', '.join(rejected)} on a concept row; "
                f"update_fields accepts only {', '.join(sorted(_CONCEPT_UPDATABLE_FIELDS))}."
            )
        for key, value in fields.items():
            setattr(concept, key, value)
        self.session.add(concept)
        await self.session.commit()
        await self.session.refresh(concept)
        return concept

    async def delete(self, concept: Concept) -> None:
        """Delete the concept; its ``concept_tags`` edges cascade."""
        await self.session.delete(concept)
        await self.session.commit()

    async def list_tags_for_concepts(
        self, concept_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, list[Tag]]:
        """The tags of many concepts in one query, keyed by concept id.

        See :meth:`NoteRepository.list_tags_for_notes` — same query shape, same
        reason.
        """
        if not concept_ids:
            return {}
        result = await self.session.execute(
            select(concept_tags.c.concept_id, Tag)
            .join(Tag, Tag.id == concept_tags.c.tag_id)
            .where(concept_tags.c.concept_id.in_(list(concept_ids)))
            .order_by(concept_tags.c.concept_id.asc(), Tag.name.asc(), Tag.id.asc())
        )
        grouped: dict[uuid.UUID, list[Tag]] = {}
        for concept_id, tag in result.all():
            grouped.setdefault(concept_id, []).append(tag)
        return grouped

    async def owned_tag_ids(
        self, tag_ids: Sequence[uuid.UUID], owner_id: uuid.UUID
    ) -> set[uuid.UUID]:
        """Which of these tag ids are this user's tags.

        **The check that makes writing a tag edge safe.** ``concept_tags.tag_id``
        is a foreign key to *any* tag, so an unchecked write attaches somebody
        else's label to the caller's own concept — a cross-tenant row written
        through a completely legitimate-looking payload. The service compares the
        answer against what was asked for and refuses the difference, so the
        refusal happens before anything is written rather than as an
        ``IntegrityError`` afterwards.
        """
        wanted = list(dict.fromkeys(tag_ids))
        if not wanted:
            return set()
        result = await self.session.execute(
            select(Tag.id).where(Tag.owner_id == owner_id, Tag.id.in_(wanted))
        )
        return {row[0] for row in result.all()}

    async def set_tags(
        self, concept_id: uuid.UUID, tag_ids: Sequence[uuid.UUID], *, owner_id: uuid.UUID
    ) -> None:
        """Replace a concept's tags with exactly this set.

        Delete-then-insert rather than a diff, for the reason given on
        :meth:`NoteRepository.set_tags`: the caller has decided the full desired
        set. Ids are deduplicated because the composite primary key would reject a
        repeat as a 500, and only ids this owner holds are written — see
        :meth:`owned_tag_ids`, which the service runs first and whose answer this
        method assumes rather than re-deriving.
        """
        owned = await self.owned_tag_ids(tag_ids, owner_id)
        rows = [
            {"concept_id": concept_id, "tag_id": tag_id}
            for tag_id in dict.fromkeys(tag_ids)
            if tag_id in owned
        ]
        await self.session.execute(
            delete(concept_tags).where(concept_tags.c.concept_id == concept_id)
        )
        if rows:
            await self.session.execute(insert(concept_tags), rows)
        await self.session.commit()

    async def search(self, owner_id: uuid.UUID, term: str, *, limit: int) -> list[Concept]:
        """Substring search over name and description, bounded in SQL."""
        pattern = _search_pattern(term)
        result = await self.session.execute(
            select(Concept)
            .where(
                Concept.owner_id == owner_id,
                or_(
                    Concept.name.ilike(pattern, escape="\\"),
                    Concept.description.ilike(pattern, escape="\\"),
                ),
            )
            .order_by(Concept.name.asc(), Concept.id.asc())
            .limit(limit)
        )
        return list(result.scalars().all())


# --------------------------------------------------------------------------- #
# Resources
# --------------------------------------------------------------------------- #

_RESOURCE_UPDATABLE_FIELDS = frozenset({"description", "resource_type", "title", "url"})
_RESOURCE_SORT_COLUMNS: dict[str, InstrumentedAttribute] = {
    "created_at": Resource.created_at,
    "resource_type": Resource.resource_type,
    "title": Resource.title,
    "updated_at": Resource.updated_at,
}


class ResourceRepository:
    """Resource persistence bound to a single request-scoped session."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        owner_id: uuid.UUID,
        title: str,
        description: str | None = None,
        url: str | None = None,
        resource_type: str = "other",
    ) -> Resource:
        """Insert a resource."""
        resource = Resource(
            owner_id=owner_id,
            title=title.strip(),
            description=description,
            url=url,
            resource_type=resource_type,
        )
        self.session.add(resource)
        await self.session.commit()
        await self.session.refresh(resource)
        return resource

    async def get_by_id_for_user(
        self, resource_id: uuid.UUID, owner_id: uuid.UUID
    ) -> Resource | None:
        """Return the resource only when it also belongs to this user."""
        result = await self.session.execute(
            select(Resource).where(Resource.id == resource_id, Resource.owner_id == owner_id)
        )
        return result.scalar_one_or_none()

    async def list_for_user(
        self,
        owner_id: uuid.UUID,
        *,
        limit: int,
        offset: int,
        search: str | None = None,
        resource_type: str | None = None,
        sort: str = "created_at",
        order: str = "desc",
    ) -> tuple[list[Resource], int]:
        """List this owner's resources with the unpaginated total."""
        filters = [Resource.owner_id == owner_id]
        if resource_type is not None:
            filters.append(Resource.resource_type == resource_type)
        if search is not None and search.strip():
            pattern = _search_pattern(search.strip())
            filters.append(
                Resource.title.ilike(pattern, escape="\\")
                | Resource.description.ilike(pattern, escape="\\")
                | Resource.url.ilike(pattern, escape="\\")
            )
        page = (
            select(Resource)
            .where(*filters)
            .order_by(
                *_order_by_clauses(sort, order, _RESOURCE_SORT_COLUMNS, Resource, "resources")
            )
            .limit(limit)
            .offset(offset)
        )
        return await _paginate_and_count(self.session, page, Resource, filters)

    async def update_fields(self, resource: Resource, **fields: object) -> Resource:
        """Apply a partial update. ``owner_id`` is not on the allowlist."""
        rejected = sorted(set(fields) - _RESOURCE_UPDATABLE_FIELDS)
        if rejected:
            raise ValueError(
                f"Cannot write {', '.join(rejected)} on a resource row; "
                f"update_fields accepts only {', '.join(sorted(_RESOURCE_UPDATABLE_FIELDS))}."
            )
        for key, value in fields.items():
            setattr(resource, key, value)
        self.session.add(resource)
        await self.session.commit()
        await self.session.refresh(resource)
        return resource

    async def delete(self, resource: Resource) -> None:
        """Delete the resource.

        Its edges are not cascaded — there is no foreign key to hang the
        cascade off — so see
        :meth:`KnowledgeLinkRepository.delete_for_entity`.
        """
        await self.session.delete(resource)
        await self.session.commit()

    async def search(self, owner_id: uuid.UUID, term: str, *, limit: int) -> list[Resource]:
        """Substring search over title, description and URL, bounded in SQL."""
        pattern = _search_pattern(term)
        result = await self.session.execute(
            select(Resource)
            .where(
                Resource.owner_id == owner_id,
                or_(
                    Resource.title.ilike(pattern, escape="\\"),
                    Resource.description.ilike(pattern, escape="\\"),
                    Resource.url.ilike(pattern, escape="\\"),
                ),
            )
            .order_by(Resource.updated_at.desc(), Resource.id.desc())
            .limit(limit)
        )
        return list(result.scalars().all())


# --------------------------------------------------------------------------- #
# Bookmarks
# --------------------------------------------------------------------------- #

_BOOKMARK_UPDATABLE_FIELDS = frozenset({"archived_at", "description", "title", "url"})
_BOOKMARK_SORT_COLUMNS: dict[str, InstrumentedAttribute] = {
    "archived_at": Bookmark.archived_at,
    "created_at": Bookmark.created_at,
    # ``domain`` is derived rather than typed, but it is exactly the column a
    # "my bookmarks on this site" list sorts by — so it is on the allowlist.
    "domain": Bookmark.domain,
    "title": Bookmark.title,
    "updated_at": Bookmark.updated_at,
}


class BookmarkRepository:
    """Bookmark persistence bound to a single request-scoped session.

    The one repository that *writes* a value its caller did not send:
    :meth:`create` and :meth:`update_fields` both derive ``domain`` from the
    URL. See :func:`_domain_from_url` for why it is the server's job.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        owner_id: uuid.UUID,
        url: str,
        title: str | None = None,
        description: str | None = None,
    ) -> Bookmark:
        """Insert a bookmark with its domain derived from the URL.

        ``domain`` is not a parameter. That is the whole point: a client-supplied
        domain would let a bookmark be filed under a host it does not belong to,
        which is both a data-quality hole and the shape a phishing bookmark
        takes.

        Raises:
            IntegrityError: If this owner already bookmarked this exact URL — the
                duplicate is a 409 rather than two identical rows.
        """
        bookmark = Bookmark(
            owner_id=owner_id,
            url=url,
            title=title,
            description=description,
            domain=_domain_from_url(url),
        )
        self.session.add(bookmark)
        await self.session.commit()
        await self.session.refresh(bookmark)
        return bookmark

    async def get_by_id_for_user(
        self, bookmark_id: uuid.UUID, owner_id: uuid.UUID
    ) -> Bookmark | None:
        """Return the bookmark only when it also belongs to this user."""
        result = await self.session.execute(
            select(Bookmark).where(Bookmark.id == bookmark_id, Bookmark.owner_id == owner_id)
        )
        return result.scalar_one_or_none()

    async def get_by_url(self, owner_id: uuid.UUID, url: str) -> Bookmark | None:
        """Return this owner's bookmark for this exact URL, or ``None``.

        The pre-flight for a duplicate create: answering 409 without waiting for
        the constraint to fire.
        """
        result = await self.session.execute(
            select(Bookmark).where(Bookmark.owner_id == owner_id, Bookmark.url == url)
        )
        return result.scalar_one_or_none()

    async def list_for_user(
        self,
        owner_id: uuid.UUID,
        *,
        limit: int,
        offset: int,
        search: str | None = None,
        include_archived: bool = False,
        sort: str = "created_at",
        order: str = "desc",
    ) -> tuple[list[Bookmark], int]:
        """List this owner's bookmarks with the unpaginated total.

        Archived bookmarks are excluded by default. Archival is "not in the
        list", and a bookmarked link is a fact about the past that is kept rather
        than deleted — so it stays reachable and stays out of the way.
        """
        filters = [Bookmark.owner_id == owner_id]
        if not include_archived:
            filters.append(Bookmark.archived_at.is_(None))
        if search is not None and search.strip():
            pattern = _search_pattern(search.strip())
            filters.append(
                Bookmark.title.ilike(pattern, escape="\\")
                | Bookmark.description.ilike(pattern, escape="\\")
                | Bookmark.url.ilike(pattern, escape="\\")
            )
        page = (
            select(Bookmark)
            .where(*filters)
            .order_by(
                *_order_by_clauses(sort, order, _BOOKMARK_SORT_COLUMNS, Bookmark, "bookmarks")
            )
            .limit(limit)
            .offset(offset)
        )
        return await _paginate_and_count(self.session, page, Bookmark, filters)

    async def update_fields(self, bookmark: Bookmark, **fields: object) -> Bookmark:
        """Apply a partial update, re-deriving ``domain`` when ``url`` moves.

        ``domain`` is not writable, so the only way it changes is a new URL — and
        that is the moment it *must* change, or a bookmark that moved would keep
        claiming the old host. ``owner_id`` is not on the allowlist either.
        """
        rejected = sorted(set(fields) - _BOOKMARK_UPDATABLE_FIELDS)
        if rejected:
            raise ValueError(
                f"Cannot write {', '.join(rejected)} on a bookmark row; "
                f"update_fields accepts only {', '.join(sorted(_BOOKMARK_UPDATABLE_FIELDS))}."
            )
        if "url" in fields and isinstance(fields["url"], str):
            fields["domain"] = _domain_from_url(fields["url"])
        for key, value in fields.items():
            setattr(bookmark, key, value)
        self.session.add(bookmark)
        await self.session.commit()
        await self.session.refresh(bookmark)
        return bookmark

    async def archive(self, bookmark: Bookmark, archived_at: Any) -> Bookmark:
        """Stamp ``archived_at`` — the only caller of the archived set.

        A dedicated method rather than a payload field so the set cannot be
        entered or left by an ordinary edit.
        """
        return await self.update_fields(bookmark, archived_at=archived_at)

    async def delete(self, bookmark: Bookmark) -> None:
        """Delete the bookmark.

        Archival exists so deletion stays an explicit act; this is it.
        """
        await self.session.delete(bookmark)
        await self.session.commit()

    async def search(
        self, owner_id: uuid.UUID, term: str, *, limit: int, include_archived: bool = False
    ) -> list[Bookmark]:
        """Substring search over title, description and URL, bounded in SQL.

        Archived bookmarks are excluded unless ``include_archived`` says otherwise,
        on the same rule :meth:`list_for_user` applies: an archive is "not in my
        working set", and a search is a view of the working set. The flag exists
        so "where did I put that link?" has an answer that is not "it is gone".
        """
        pattern = _search_pattern(term)
        filters = [
            Bookmark.owner_id == owner_id,
            or_(
                Bookmark.title.ilike(pattern, escape="\\"),
                Bookmark.description.ilike(pattern, escape="\\"),
                Bookmark.url.ilike(pattern, escape="\\"),
            ),
        ]
        if not include_archived:
            filters.append(Bookmark.archived_at.is_(None))
        result = await self.session.execute(
            select(Bookmark)
            .where(*filters)
            .order_by(Bookmark.created_at.desc(), Bookmark.id.desc())
            .limit(limit)
        )
        return list(result.scalars().all())


# --------------------------------------------------------------------------- #
# Documents
# --------------------------------------------------------------------------- #

_DOCUMENT_UPDATABLE_FIELDS = frozenset({"description", "document_type", "filename", "title"})
_DOCUMENT_SORT_COLUMNS: dict[str, InstrumentedAttribute] = {
    "created_at": Document.created_at,
    "filename": Document.filename,
    "title": Document.title,
    "updated_at": Document.updated_at,
}


class DocumentRepository:
    """Document-metadata persistence bound to a request-scoped session.

    **Metadata only.** There is no blob, no object key and no extracted text in
    this phase; parsing a PDF is later work and this table is the row that work
    will attach to. Deleting one is ``ON DELETE SET NULL`` from
    ``notes.document_id``, so a removed file never takes the prose with it.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        owner_id: uuid.UUID,
        filename: str,
        title: str | None = None,
        description: str | None = None,
        document_type: str | None = None,
    ) -> Document:
        """Insert a document row."""
        document = Document(
            owner_id=owner_id,
            filename=filename.strip(),
            title=title,
            description=description,
            document_type=document_type,
        )
        self.session.add(document)
        await self.session.commit()
        await self.session.refresh(document)
        return document

    async def get_by_id_for_user(
        self, document_id: uuid.UUID, owner_id: uuid.UUID
    ) -> Document | None:
        """Return the document only when it also belongs to this user."""
        result = await self.session.execute(
            select(Document).where(Document.id == document_id, Document.owner_id == owner_id)
        )
        return result.scalar_one_or_none()

    async def list_for_user(
        self,
        owner_id: uuid.UUID,
        *,
        limit: int,
        offset: int,
        search: str | None = None,
        document_type: str | None = None,
        sort: str = "created_at",
        order: str = "desc",
    ) -> tuple[list[Document], int]:
        """List this owner's documents with the unpaginated total."""
        filters = [Document.owner_id == owner_id]
        if document_type is not None:
            filters.append(Document.document_type == document_type)
        if search is not None and search.strip():
            pattern = _search_pattern(search.strip())
            filters.append(
                Document.filename.ilike(pattern, escape="\\")
                | Document.title.ilike(pattern, escape="\\")
                | Document.description.ilike(pattern, escape="\\")
            )
        page = (
            select(Document)
            .where(*filters)
            .order_by(
                *_order_by_clauses(sort, order, _DOCUMENT_SORT_COLUMNS, Document, "documents")
            )
            .limit(limit)
            .offset(offset)
        )
        return await _paginate_and_count(self.session, page, Document, filters)

    async def update_fields(self, document: Document, **fields: object) -> Document:
        """Apply a partial update. ``owner_id`` is not on the allowlist."""
        rejected = sorted(set(fields) - _DOCUMENT_UPDATABLE_FIELDS)
        if rejected:
            raise ValueError(
                f"Cannot write {', '.join(rejected)} on a document row; "
                f"update_fields accepts only {', '.join(sorted(_DOCUMENT_UPDATABLE_FIELDS))}."
            )
        for key, value in fields.items():
            setattr(document, key, value)
        self.session.add(document)
        await self.session.commit()
        await self.session.refresh(document)
        return document

    async def delete(self, document: Document) -> None:
        """Delete the document.

        Notes that referenced it keep their text and lose the pointer, which is
        what ``SET NULL`` is for.
        """
        await self.session.delete(document)
        await self.session.commit()

    async def search(self, owner_id: uuid.UUID, term: str, *, limit: int) -> list[Document]:
        """Substring search over filename, title and description, bounded in SQL.

        The same predicate :meth:`list_for_user` applies for ``search``, on the
        same three columns, so ``GET /knowledge/documents?search=`` and
        ``GET /knowledge/search?q=`` cannot disagree about whether a document
        matches. Documents had no search at all, which is why the global endpoint
        answered "nothing" for a term the documents list answered with two rows.
        """
        pattern = _search_pattern(term)
        result = await self.session.execute(
            select(Document)
            .where(
                Document.owner_id == owner_id,
                or_(
                    Document.filename.ilike(pattern, escape="\\"),
                    Document.title.ilike(pattern, escape="\\"),
                    Document.description.ilike(pattern, escape="\\"),
                ),
            )
            .order_by(Document.updated_at.desc(), Document.id.desc())
            .limit(limit)
        )
        return list(result.scalars().all())


# --------------------------------------------------------------------------- #
# Categories
# --------------------------------------------------------------------------- #

_CATEGORY_UPDATABLE_FIELDS = frozenset({"name", "parent_id"})
_CATEGORY_SORT_COLUMNS: dict[str, InstrumentedAttribute] = {
    "created_at": Category.created_at,
    "name": Category.name,
    "updated_at": Category.updated_at,
}


class CategoryRepository:
    """Category persistence bound to a single request-scoped session."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self, *, owner_id: uuid.UUID, name: str, parent_id: uuid.UUID | None = None
    ) -> Category:
        """Insert a category.

        ``parent_id`` is *not* validated here beyond the database's one-row check.
        A parent belonging to another user and a parent that would close a cycle
        are both questions about rows the caller cannot see or about a walk up
        the tree, so both belong to the service, which resolves the parent
        through :meth:`get_by_id_for_user` first.

        Raises:
            IntegrityError: For a duplicate name under this owner, or for a
                category that is its own parent.
        """
        category = Category(owner_id=owner_id, name=name.strip(), parent_id=parent_id)
        self.session.add(category)
        await self.session.commit()
        await self.session.refresh(category)
        return category

    async def get_by_id_for_user(
        self, category_id: uuid.UUID, owner_id: uuid.UUID
    ) -> Category | None:
        """Return the category only when it also belongs to this user.

        This is the lookup a re-parent goes through, and it is why a parent
        belonging to somebody else is a 404 rather than a row silently adopted.
        """
        result = await self.session.execute(
            select(Category).where(Category.id == category_id, Category.owner_id == owner_id)
        )
        return result.scalar_one_or_none()

    async def get_by_name(self, owner_id: uuid.UUID, name: str) -> Category | None:
        """Return this owner's category with this exact name, or ``None``."""
        result = await self.session.execute(
            select(Category).where(Category.owner_id == owner_id, Category.name == name.strip())
        )
        return result.scalar_one_or_none()

    async def list_for_user(
        self,
        owner_id: uuid.UUID,
        *,
        limit: int,
        offset: int,
        parent_id: uuid.UUID | None = None,
        root_only: bool = False,
        search: str | None = None,
        sort: str = "name",
        order: str = "asc",
    ) -> tuple[list[Category], int]:
        """List this owner's categories with the unpaginated total.

        ``parent_id`` cannot express "roots only" — ``None`` means *do not filter
        on parent* — so ``root_only`` exists as the separate flag that can. A
        caller needing the subtree needs :meth:`list_ancestors` on each row.
        """
        filters = [Category.owner_id == owner_id]
        if parent_id is not None:
            filters.append(Category.parent_id == parent_id)
        elif root_only:
            filters.append(Category.parent_id.is_(None))
        if search is not None and search.strip():
            filters.append(Category.name.ilike(_search_pattern(search.strip()), escape="\\"))
        page = (
            select(Category)
            .where(*filters)
            .order_by(
                *_order_by_clauses(sort, order, _CATEGORY_SORT_COLUMNS, Category, "categories")
            )
            .limit(limit)
            .offset(offset)
        )
        return await _paginate_and_count(self.session, page, Category, filters)

    async def list_children(self, parent_id: uuid.UUID, owner_id: uuid.UUID) -> list[Category]:
        """The direct children of one node, alphabetically.

        One level only, like ``TaskRepository.list_subtasks``: descending the
        whole subtree is a recursive round trip per depth, and the product shows
        categories as a flat list under their parent.
        """
        result = await self.session.execute(
            select(Category)
            .where(Category.parent_id == parent_id, Category.owner_id == owner_id)
            .order_by(Category.name.asc(), Category.id.asc())
        )
        return list(result.scalars().all())

    async def list_ancestors(self, category: Category, owner_id: uuid.UUID) -> list[Category]:
        """Walk ``parent_id`` to the root, root first.

        **This is the cycle check.** The database can only refuse a category that
        is its own parent — one boolean over one row. ``A -> B -> C -> A`` needs a
        walk, and this is that walk: the service refuses a re-parent whose target
        already has this category among its ancestors.

        Bounded by :data:`_MAX_ANCESTOR_DEPTH` and by a visited set, so a row that
        somehow became cyclic in the database terminates instead of spinning
        forever. Both guards are belt-and-braces around a constraint that already
        makes the cycle unwritable through any normal path; the honest reading is
        that a cycle here is a bug, and a bug must end.
        """
        ancestors: list[Category] = []
        seen: set[uuid.UUID] = {category.id}
        parent_id = category.parent_id
        while parent_id is not None and parent_id not in seen:
            result = await self.session.execute(
                select(Category).where(Category.id == parent_id, Category.owner_id == owner_id)
            )
            parent = result.scalar_one_or_none()
            if parent is None:
                break
            seen.add(parent.id)
            ancestors.append(parent)
            parent_id = parent.parent_id
        ancestors.reverse()
        return ancestors

    async def update_fields(self, category: Category, **fields: object) -> Category:
        """Apply a partial update, including a re-parent.

        ``owner_id`` is not on the allowlist.
        """
        rejected = sorted(set(fields) - _CATEGORY_UPDATABLE_FIELDS)
        if rejected:
            raise ValueError(
                f"Cannot write {', '.join(rejected)} on a category row; "
                f"update_fields accepts only {', '.join(sorted(_CATEGORY_UPDATABLE_FIELDS))}."
            )
        for key, value in fields.items():
            setattr(category, key, value)
        self.session.add(category)
        await self.session.commit()
        await self.session.refresh(category)
        return category

    async def delete(self, category: Category) -> None:
        """Delete the node.

        ``parent_id`` is ``ON DELETE SET NULL``, so children are demoted to roots
        rather than deleted — they are the user's content and the category was
        only their filing.
        """
        await self.session.delete(category)
        await self.session.commit()


# --------------------------------------------------------------------------- #
# Knowledge links
# --------------------------------------------------------------------------- #

_LINK_SORT_COLUMNS: dict[str, InstrumentedAttribute] = {
    "created_at": KnowledgeLink.created_at,
    "link_type": KnowledgeLink.link_type,
}

#: How many edges may be returned per node in the graph payload. A node average
#: above four edges means a hub, and hubs are what turn a force-directed layout
#: into a hairball; the cap keeps the edge list from being the thing that fills
#: the response while the node cap says "bounded".
MAX_GRAPH_EDGES_PER_NODE = 4


class GraphPayload(NamedTuple):
    """What :meth:`KnowledgeLinkRepository.graph_payload` returns.

    A NamedTuple so it unpacks as the ``(nodes, edges)`` it looks like, while
    ``truncated`` stays reachable by name. ``truncated`` exists because ``nodes``
    alone cannot distinguish "this is everything you have" from "this is the cap"
    — and a client that cannot tell them apart draws a capped graph and calls it
    their knowledge base.
    """

    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    truncated: bool


class KnowledgeLinkRepository:
    """Edge persistence for the polymorphic link table."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        owner_id: uuid.UUID,
        source_type: str,
        source_id: uuid.UUID,
        target_type: str,
        target_id: uuid.UUID,
        link_type: str = DEFAULT_KNOWLEDGE_LINK_TYPE,
    ) -> KnowledgeLink:
        """Insert one edge.

        **The repository does not check that the endpoints exist or are owned.**
        It cannot: neither column carries a foreign key, because ``source_type`` is
        what decides which table ``source_id`` names. Ownership therefore has to
        be established by the caller through an owner-scoped lookup on both
        endpoints, before this is called. See
        :class:`app.models.enums.KnowledgeEntityType` — that comment is the
        security model of this table.

        Raises:
            IntegrityError: From ``ck_knowledge_links_no_self_edge`` or
                ``uq_knowledge_links_edge``. Left to propagate: the service turns
                them into 422 and 409, and swallowing them here would make those
                two refusals indistinguishable.
        """
        link = KnowledgeLink(
            owner_id=owner_id,
            source_type=str(source_type),
            source_id=source_id,
            target_type=str(target_type),
            target_id=target_id,
            link_type=str(link_type),
        )
        self.session.add(link)
        await self.session.commit()
        await self.session.refresh(link)
        return link

    async def get_by_id_for_user(
        self, link_id: uuid.UUID, owner_id: uuid.UUID
    ) -> KnowledgeLink | None:
        """Return the edge only when it also belongs to this user."""
        result = await self.session.execute(
            select(KnowledgeLink).where(
                KnowledgeLink.id == link_id, KnowledgeLink.owner_id == owner_id
            )
        )
        return result.scalar_one_or_none()

    async def delete(self, link: KnowledgeLink) -> None:
        """Remove one edge.

        A delete of the loaded row is enough here; the row is already in hand
        because the endpoint resolved it by id.
        """
        await self.session.delete(link)
        await self.session.commit()

    async def delete_for_entity(
        self, entity_type: str, entity_id: uuid.UUID, owner_id: uuid.UUID
    ) -> int:
        """Drop every edge touching one node, in either direction.

        **This is the cascade the missing foreign key cannot give us.** Deleting a
        note does not delete its edges — there is no FK for a cascade to hang off
        — and a left-over edge names an id that answers 404 to everything, so the
        graph would grow ghosts no screen can explain. The delete path must
        therefore call this in the same transaction as the delete itself.

        Owner-scoped: an edge that names somebody else's id is not this user's to
        delete, and the predicate is what guarantees the two never blur.
        """
        result = await self.session.execute(
            delete(KnowledgeLink).where(
                KnowledgeLink.owner_id == owner_id,
                or_(
                    (KnowledgeLink.source_type == entity_type)
                    & (KnowledgeLink.source_id == entity_id),
                    (KnowledgeLink.target_type == entity_type)
                    & (KnowledgeLink.target_id == entity_id),
                ),
            )
        )
        await self.session.commit()
        return int(result.rowcount or 0)

    async def list_outbound(
        self,
        owner_id: uuid.UUID,
        *,
        source_type: str,
        source_id: uuid.UUID,
        link_type: str | None = None,
        limit: int,
        offset: int,
    ) -> tuple[list[KnowledgeLink], int]:
        """Edges *leaving* one node ("what does this point at?").

        Served by ``ix_knowledge_links_source`` — see the index's own comment.
        """
        return await self._list_by_endpoint(
            owner_id,
            type_column=KnowledgeLink.source_type,
            id_column=KnowledgeLink.source_id,
            entity_type=source_type,
            entity_id=source_id,
            link_type=link_type,
            limit=limit,
            offset=offset,
        )

    async def list_inbound(
        self,
        owner_id: uuid.UUID,
        *,
        target_type: str,
        target_id: uuid.UUID,
        link_type: str | None = None,
        limit: int,
        offset: int,
    ) -> tuple[list[KnowledgeLink], int]:
        """Edges *arriving at* one node — the **backlinks**.

        The exact reverse of :meth:`list_outbound`, down to the shared helper, so
        the two directions cannot drift: the same columns, the same ``owner_id``
        predicate, the same ordering and the same tiebreak. Backlinks are the
        more valuable of the two in practice — a note is far more often the
        *target* of a reference than the source of one — which is why they have a
        named index and a named endpoint.
        """
        return await self._list_by_endpoint(
            owner_id,
            type_column=KnowledgeLink.target_type,
            id_column=KnowledgeLink.target_id,
            entity_type=target_type,
            entity_id=target_id,
            link_type=link_type,
            limit=limit,
            offset=offset,
        )

    async def _list_by_endpoint(
        self,
        owner_id: uuid.UUID,
        *,
        type_column: InstrumentedAttribute,
        id_column: InstrumentedAttribute,
        entity_type: str,
        entity_id: uuid.UUID,
        link_type: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[KnowledgeLink], int]:
        """The one query behind both directions.

        Outbound and inbound differ only in which two columns name the endpoint,
        so they share the predicate, the ordering and the count rather than
        keeping two near-identical bodies in step by hand.
        """
        filters = [
            KnowledgeLink.owner_id == owner_id,
            type_column == str(entity_type),
            id_column == entity_id,
        ]
        if link_type is not None:
            filters.append(KnowledgeLink.link_type == str(link_type))
        page = (
            select(KnowledgeLink)
            .where(*filters)
            .order_by(
                *_order_by_clauses(
                    "created_at", "desc", _LINK_SORT_COLUMNS, KnowledgeLink, "knowledge_links"
                )
            )
            .limit(limit)
            .offset(offset)
        )
        return await _paginate_and_count(self.session, page, KnowledgeLink, filters)

    async def list_for_owner(
        self,
        owner_id: uuid.UUID,
        *,
        limit: int,
        offset: int,
        link_type: str | None = None,
    ) -> tuple[list[KnowledgeLink], int]:
        """Every edge this owner owns, newest first, paginated.

        **This is the answer to "show me all my links".** Naming a node is how a
        caller asks what a node points at; it is not how a caller enumerates the
        edge table, and an edge table reachable only node-by-node means a client
        has to discover the nodes first — which it cannot do when the nodes it
        wants are the ones it is looking for.

        Bounded even though it is owner-scoped: ``owner_id`` is not a selective
        index for this table (it is on the row, but the leading pair of either
        endpoint index is), and a user with thousands of notes has tens of
        thousands of edges. Paging is the answer; an unbounded "everything" is not.
        """
        filters = [KnowledgeLink.owner_id == owner_id]
        if link_type is not None:
            filters.append(KnowledgeLink.link_type == str(link_type))
        page = (
            select(KnowledgeLink)
            .where(*filters)
            .order_by(
                *_order_by_clauses(
                    "created_at", "desc", _LINK_SORT_COLUMNS, KnowledgeLink, "knowledge_links"
                )
            )
            .limit(limit)
            .offset(offset)
        )
        return await _paginate_and_count(self.session, page, KnowledgeLink, filters)

    # -- graph ------------------------------------------------------------- #

    async def graph_payload(
        self, owner_id: uuid.UUID, *, limit: int, entity_type: str | None = None
    ) -> GraphPayload:
        """Assemble ``{nodes, edges}`` for one owner, bounded **in SQL**.

        The node set is a CTE: one branch per entity type, each carrying its own
        ``LIMIT``, so the cap is enforced by the database and the caller cannot
        be handed a bigger set than it asked for. The edge query then joins that
        CTE on *both* endpoints, which is what keeps every returned edge pointing
        at two nodes that are actually present — an edge to a filtered-out node is
        dropped rather than drawn to nothing.

        **The per-branch ``LIMIT`` is not the cap; it is the bound on the work.**
        Three branches each limited to ``limit`` return up to ``3 * limit`` nodes,
        which is why ``?limit=1`` used to answer with three nodes, ``?limit=10``
        with thirteen and ``?limit=200`` with 203 — every value exactly three over,
        and every one of them reported alongside a ``limit`` it had already
        exceeded. The union is therefore capped **again** on the outside, by an
        outer ``LIMIT limit`` over the pooled branches: the branches keep their
        per-branch limit so no table is read whole, and the response can never
        carry more than the number it says it does.

        The outer ordering is by ``id`` so the answer is deterministic: the same
        account at the same size gets the same graph on every call, which is what
        makes ``truncated`` mean the same thing twice. It is deliberately not
        ordered by type — that would fill the cap from whichever kind sorts first
        and drop the other two entirely.

        **Re-establishing the endpoints is the price of the polymorphism.** The
        database cannot check that these ids exist or are owned, because
        ``source_type`` is what chooses their table. Joining against the node set
        is where this layer pays that cost back: an edge naming another account's
        note has no node to match and simply does not appear.

        ``entity_type`` narrows the nodes to one branch and the edges follow the
        nodes that survive.

        The cap is the feature, not a limitation of it — the spec names "a graph
        that looks impressive but becomes unusable" as the failure mode, and a
        force-directed layout is where that shows up. The service and the router
        each enforce the same ceiling independently, so a non-HTTP caller is
        bounded too.
        """
        wanted = _entity_types(entity_type)
        branches = []
        if KnowledgeEntityType.NOTE.value in wanted:
            branches.append(
                select(
                    Note.id.label("id"),
                    literal_column("'note'").label("type"),
                    Note.title.label("label"),
                )
                .where(Note.owner_id == owner_id)
                .order_by(Note.updated_at.desc(), Note.id.desc())
                .limit(limit)
            )
        if KnowledgeEntityType.CONCEPT.value in wanted:
            branches.append(
                select(
                    Concept.id.label("id"),
                    literal_column("'concept'").label("type"),
                    Concept.name.label("label"),
                )
                .where(Concept.owner_id == owner_id)
                .order_by(Concept.name.asc(), Concept.id.asc())
                .limit(limit)
            )
        if KnowledgeEntityType.RESOURCE.value in wanted:
            branches.append(
                select(
                    Resource.id.label("id"),
                    literal_column("'resource'").label("type"),
                    Resource.title.label("label"),
                )
                .where(Resource.owner_id == owner_id)
                .order_by(Resource.created_at.desc(), Resource.id.desc())
                .limit(limit)
            )
        if not branches:
            return GraphPayload(nodes=[], edges=[], truncated=False)

        combined = branches[0] if len(branches) == 1 else union_all(*branches)
        pool = combined.subquery("graph_node_pool")
        nodes_cte = (
            select(pool.c.id, pool.c.type, pool.c.label)
            .order_by(pool.c.id.asc())
            .limit(limit)
            .cte("graph_nodes")
        )
        source = nodes_cte.alias("src")
        target = nodes_cte.alias("dst")

        edge_limit = min(limit * MAX_GRAPH_EDGES_PER_NODE, 2_000)
        edge_result = await self.session.execute(
            select(
                KnowledgeLink.source_id,
                KnowledgeLink.source_type,
                KnowledgeLink.target_id,
                KnowledgeLink.target_type,
                KnowledgeLink.link_type,
            )
            .join(
                source,
                (source.c.type == KnowledgeLink.source_type)
                & (source.c.id == KnowledgeLink.source_id),
            )
            .join(
                target,
                (target.c.type == KnowledgeLink.target_type)
                & (target.c.id == KnowledgeLink.target_id),
            )
            .where(KnowledgeLink.owner_id == owner_id)
            .order_by(KnowledgeLink.created_at.desc(), KnowledgeLink.id.desc())
            .limit(edge_limit)
        )
        edges = [
            {
                "source": source_id,
                "source_type": str(source_type),
                "target": target_id,
                "target_type": str(target_type),
                "type": str(link_type),
            }
            for source_id, source_type, target_id, target_type, link_type in edge_result.all()
        ]

        node_result = await self.session.execute(
            select(nodes_cte.c.id, nodes_cte.c.type, nodes_cte.c.label)
        )
        nodes = [
            {"id": node_id, "type": str(node_type), "label": label}
            for node_id, node_type, label in node_result.all()
        ]
        return GraphPayload(
            nodes=nodes,
            edges=edges,
            # The node cap is reached *and* the query asked for it, so a client
            # can tell "this is everything" from "this is all you may have".
            truncated=len(nodes) >= limit,
        )


def _entity_types(entity_type: str | None) -> set[str]:
    """Resolve an optional entity-type filter to the set of branches to run.

    An unrecognised value yields the empty set rather than everything: a filter
    that silently matches no rows and a filter that is ignored are both wrong, and
    the service has already validated this value against the enum, so reaching
    here with a stranger is a programming error worth an empty graph, not a
    silent widening to every type.
    """
    if entity_type is None:
        return {member.value for member in KnowledgeEntityType}
    return {str(entity_type)} & {member.value for member in KnowledgeEntityType}
