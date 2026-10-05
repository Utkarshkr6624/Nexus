"""Request/response models for the knowledge base.

Notes, concepts, resources, bookmarks, documents, categories and the edges
between them.

Four decisions shape this module.

**Every ``*Update`` is ``extra="forbid"``**, for the reason
:class:`~app.schemas.task.TaskUpdate` gives: Pydantic's default is ``"ignore"``,
under which a client that sends a field this route does not own gets a cheerful
200 with the field dropped — and a caller who reads that as success has believed
something false about their own data. The fields with rules attached are the
ones that most need it: ``status`` moves only through ``/publish``,
``/archive`` and ``/restore``, ``domain`` is derived from the URL by the server,
and ``owner_id`` is the caller's and is never writable by anybody. Naming the
offending field in a 422 is the answer a client can act on.

The ``*Create`` models are ``extra="forbid"`` for the same reason, and one extra:
a create payload carrying ``owner_id`` must be *rejected*, not dropped, because
the alternative is a client that believes it filed its note somewhere it did not.

**A URL must be ``http`` or ``https``.** ``javascript:`` is rejected for the
reason :data:`~app.schemas.user.AVATAR_URL_SCHEMES` rejects it on ``avatar_url``:
a stored ``javascript:`` URL becomes a script injection the moment a client
renders it into an ``<a href>`` or an ``<img src>``. Every validator here
produces a 422 through Pydantic, never a 500 — an unparseable or hostile URL is
user input, and answering it with a traceback is a bug, not a policy.

**There are no ``datetime`` inputs on any model.** ``created_at``,
``archived_at`` and every ``updated_at`` are stamped by the server, so there is
no naive-vs-aware decision to get wrong here; the naive-datetime rejection other
phases need does not apply and is not faked.

Counts on the read models (``revision_count``, ``tag_ids``) are supplied by the
service from the association and history tables, because none of them can be
answered from the row itself. They default so a read without those joins is
still a valid response.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import (
    KnowledgeEntityType,
    KnowledgeLinkType,
    NoteStatus,
    ResourceType,
)
from app.models.knowledge import MAX_REVISIONS_PER_NOTE
from app.schemas.tag import MAX_TAGS_PER_OBJECT

__all__ = [
    "ALLOWED_URL_SCHEMES",
    "MAX_BOOKMARK_TITLE_LENGTH",
    "MAX_CATEGORY_NAME_LENGTH",
    "MAX_CONCEPT_NAME_LENGTH",
    "MAX_DOCUMENT_FILENAME_LENGTH",
    "MAX_DOCUMENT_TITLE_LENGTH",
    "MAX_DOCUMENT_TYPE_LENGTH",
    "MAX_KNOWLEDGE_URL_LENGTH",
    "MAX_NOTE_TITLE_LENGTH",
    "MAX_REVISIONS_PER_NOTE",
    "MAX_TAGS_PER_NOTE",
    "BacklinksResponse",
    "BookmarkCreate",
    "BookmarkRead",
    "BookmarkUpdate",
    "CategoryCreate",
    "CategoryRead",
    "CategoryUpdate",
    "ConceptCreate",
    "ConceptRead",
    "ConceptUpdate",
    "DocumentCreate",
    "DocumentRead",
    "DocumentUpdate",
    "GraphResponse",
    "KnowledgeEntityType",
    "KnowledgeGraph",
    "KnowledgeGraphEdge",
    "KnowledgeGraphNode",
    "KnowledgeLinkCreate",
    "KnowledgeLinkRead",
    "KnowledgeLinkType",
    "KnowledgeSearchKind",
    "KnowledgeSearchResult",
    "NoteCreate",
    "NoteRead",
    "NoteRevisionRead",
    "NoteStatus",
    "NoteUpdate",
    "ResourceCreate",
    "ResourceRead",
    "ResourceType",
    "ResourceUpdate",
    "SearchResponse",
]

#: Must match the column widths in ``app.models.knowledge``; they are two halves
#: of one contract and a schema that is looser than its column is a 500 waiting
#: for a user with a long enough title.
MAX_NOTE_TITLE_LENGTH = 300
MAX_CONCEPT_NAME_LENGTH = 200
MAX_RESOURCE_TITLE_LENGTH = 300
MAX_BOOKMARK_TITLE_LENGTH = 300
MAX_KNOWLEDGE_URL_LENGTH = 2048
MAX_DOCUMENT_FILENAME_LENGTH = 255
MAX_DOCUMENT_TITLE_LENGTH = 300
MAX_DOCUMENT_TYPE_LENGTH = 32
MAX_CATEGORY_NAME_LENGTH = 120

#: A note or concept carries at most this many tags — the same bound Phase 3 puts
#: on a task, so "too many tags" means one thing across the product. Re-exported
#: from :mod:`app.schemas.tag` rather than restated: a second constant that had to
#: be kept equal to the first is one that eventually is not.
MAX_TAGS_PER_NOTE = MAX_TAGS_PER_OBJECT

#: A note body, summary or description. Not a column width — ``Text`` is — but a
#: bound on what one request may carry, so a paste of a whole book is a 422
#: rather than a row nobody will read back.
MAX_NOTE_TEXT_LENGTH = 200_000

#: The only schemes a stored URL may use. ``javascript:`` is the important
#: exclusion; ``data:`` and ``file:`` are refused for the same reason. Mirrors
#: :data:`~app.schemas.user.AVATAR_URL_SCHEMES` deliberately rather than sharing
#: it — the two fields are allowed to diverge, and a knowledge URL is not a
#: profile picture.
ALLOWED_URL_SCHEMES = ("http", "https")


def _require_web_url(value: Any, field: str) -> Any:
    """Reject anything that is not an absolute ``http(s)`` URL.

    Shared by every URL field so the rule and its wording exist once. The scheme
    check is the security-relevant half and the ``netloc`` check is the
    correctness half: ``http:///path`` parses with no host, which renders as a
    broken link that can never be fetched.
    """
    if not isinstance(value, str):
        return value
    candidate = value.strip()
    parsed = urlsplit(candidate)
    if parsed.scheme.lower() not in ALLOWED_URL_SCHEMES or not parsed.netloc:
        raise ValueError(f"{field} must be an absolute http or https URL.")
    return candidate


class _StrippedText:
    """Shared before-validators for the knowledge text fields.

    Stripping rather than rejecting: the whitespace is invisible in whatever UI
    produced it. A blank ``description``/``summary`` becomes ``None`` so a client
    clearing a field can send ``""`` and mean it, and ``title``/``name`` are
    stripped but *not* mapped to ``None`` — an empty title is a mistake, and
    leaving it as ``""`` lets ``min_length`` report it as one.
    """

    @field_validator("title", "name", "filename", mode="before", check_fields=False)
    @classmethod
    def _strip_required_text(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("description", "summary", mode="before", check_fields=False)
    @classmethod
    def _blank_optional_text_is_unset(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        return value.strip() or None


# --------------------------------------------------------------------------- #
# Notes
# --------------------------------------------------------------------------- #


class NoteCreate(_StrippedText, BaseModel):
    """Creation payload for a note.

    **``status`` is absent and so is ``owner_id``.** A note comes into existence
    ``draft`` — there is no path that creates one already published, so "this is
    real now" is always an act somebody performed — and its owner is the bearer
    token, never the body. Sending either is a 422 naming the field.
    """

    model_config = ConfigDict(extra="forbid")

    title: str = Field(
        min_length=1,
        max_length=MAX_NOTE_TITLE_LENGTH,
        examples=["Why the prune keeps a suffix"],
    )
    content: str = Field(default="", max_length=MAX_NOTE_TEXT_LENGTH)
    summary: str | None = Field(default=None, max_length=2000)
    document_id: UUID | None = Field(
        default=None,
        description="Metadata row for the file this note was written from.",
    )
    tag_ids: list[UUID] = Field(default_factory=list, max_length=MAX_TAGS_PER_NOTE)

    @field_validator("tag_ids")
    @classmethod
    def _deduplicate(cls, value: list[UUID]) -> list[UUID]:
        """Drop repeats.

        The composite key would reject the same tag twice and surface as a 500;
        the client sent a list, not a multiset.
        """
        return list(dict.fromkeys(value))


class NoteUpdate(_StrippedText, BaseModel):
    """Partial update of a note's text.

    No ``status`` (lifecycle has endpoints), no ``document_id`` (which file a
    note came from is provenance, and re-pointing it silently rewrites where a
    note claims to have come from), no ``owner_id``.
    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=MAX_NOTE_TITLE_LENGTH)
    content: str | None = Field(default=None, max_length=MAX_NOTE_TEXT_LENGTH)
    summary: str | None = Field(default=None, max_length=2000)


class NoteRead(BaseModel):
    """A note as returned by the knowledge endpoints.

    ``tag_ids``, ``revision_count`` and ``is_archived`` are not columns. The
    first two come from the association and history tables, and the third is
    ``status == archived`` — a derived value so a client cannot be told a note
    is archived by one field while another says otherwise.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    owner_id: UUID
    title: str
    content: str
    summary: str | None
    status: str = Field(description="One of the ``NoteStatus`` values.")
    document_id: UUID | None
    created_at: datetime
    updated_at: datetime
    tag_ids: list[UUID] = Field(default_factory=list)
    revision_count: int = Field(default=0, ge=0)
    is_archived: bool = False

    @model_validator(mode="after")
    def _archive_is_a_status(self) -> NoteRead:
        """Derive ``is_archived`` from ``status`` rather than trusting a caller.

        Two fields describing one state is two fields that can disagree; deriving
        it means a client cannot render "archived" over a draft. A model validator
        rather than a field one because the two must be computed together and
        ``is_archived`` is usually absent from the input — a field validator does
        not run for a defaulted field.
        """
        self.is_archived = self.status == NoteStatus.ARCHIVED.value
        return self

    @classmethod
    def build(
        cls,
        row: Any,
        *,
        tag_ids: list[UUID] | None = None,
        revision_count: int = 0,
    ) -> NoteRead:
        """Build from a row plus the two answers that need other tables."""
        return cls.model_validate(row).model_copy(
            update={"tag_ids": tag_ids or [], "revision_count": revision_count}
        )


class NoteRevisionRead(BaseModel):
    """One point-in-time copy of a note.

    ``status`` is deliberately absent: a revision stores the *text* as it stood,
    so restoring one is an assignment of title/content/summary and nothing else.
    Reverting the lifecycle along with it would un-publish a note on the strength
    of an edit made after publishing.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    note_id: UUID
    owner_id: UUID
    title: str
    content: str
    summary: str | None
    created_at: datetime


# --------------------------------------------------------------------------- #
# Concepts
# --------------------------------------------------------------------------- #


class ConceptCreate(_StrippedText, BaseModel):
    """Creation payload for a concept.

    No ``status`` and no ``owner_id``; a concept is named and described, and that
    is the whole of it. The name is unique **per owner**, so two accounts may
    both hold "async" — the second create *by the same user* is a 409.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=MAX_CONCEPT_NAME_LENGTH, examples=["async"])
    description: str | None = Field(default=None, max_length=MAX_NOTE_TEXT_LENGTH)
    tag_ids: list[UUID] = Field(default_factory=list, max_length=MAX_TAGS_PER_NOTE)

    @field_validator("tag_ids")
    @classmethod
    def _deduplicate(cls, value: list[UUID]) -> list[UUID]:
        return list(dict.fromkeys(value))


class ConceptUpdate(_StrippedText, BaseModel):
    """Partial update of a concept.

    ``name`` is writable — renaming vocabulary is an ordinary edit, and the
    per-owner uniqueness check still applies.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=MAX_CONCEPT_NAME_LENGTH)
    description: str | None = Field(default=None, max_length=MAX_NOTE_TEXT_LENGTH)
    tag_ids: list[UUID] | None = Field(
        default=None, max_length=MAX_TAGS_PER_NOTE, description="Full replacement set."
    )

    @field_validator("tag_ids")
    @classmethod
    def _deduplicate(cls, value: list[UUID] | None) -> list[UUID] | None:
        return None if value is None else list(dict.fromkeys(value))


class ConceptRead(BaseModel):
    """A concept as returned by the endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    owner_id: UUID
    name: str
    description: str | None
    created_at: datetime
    updated_at: datetime
    tag_ids: list[UUID] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Resources
# --------------------------------------------------------------------------- #


class ResourceCreate(_StrippedText, BaseModel):
    """Creation payload for a resource: an external thing worth citing."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=MAX_RESOURCE_TITLE_LENGTH)
    description: str | None = Field(default=None, max_length=MAX_NOTE_TEXT_LENGTH)
    url: str | None = Field(default=None, max_length=MAX_KNOWLEDGE_URL_LENGTH)
    resource_type: ResourceType = Field(default=ResourceType.OTHER)

    @field_validator("url", mode="before")
    @classmethod
    def _check_url(cls, value: Any) -> Any:
        return _require_web_url(value, "url")


class ResourceUpdate(_StrippedText, BaseModel):
    """Partial update of a resource.

    ``resource_type`` is writable here — unlike a note's status it carries no
    rules beyond the vocabulary itself, so classifying an existing resource is an
    ordinary edit.
    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=MAX_RESOURCE_TITLE_LENGTH)
    description: str | None = Field(default=None, max_length=MAX_NOTE_TEXT_LENGTH)
    url: str | None = Field(default=None, max_length=MAX_KNOWLEDGE_URL_LENGTH)
    resource_type: ResourceType | None = None

    @field_validator("url", mode="before")
    @classmethod
    def _check_url(cls, value: Any) -> Any:
        return _require_web_url(value, "url")


class ResourceRead(BaseModel):
    """A resource as returned by the endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    owner_id: UUID
    title: str
    description: str | None
    url: str | None
    resource_type: str = Field(description="One of the ``ResourceType`` values.")
    created_at: datetime
    updated_at: datetime


# --------------------------------------------------------------------------- #
# Bookmarks
# --------------------------------------------------------------------------- #


class BookmarkCreate(_StrippedText, BaseModel):
    """Creation payload for a bookmark.

    **There is no ``domain`` and no ``archived_at`` here.** The domain is derived
    from the URL by the server: a client-supplied one is a client-supplied lie,
    and it is the shape a phishing bookmark would take — filed under a domain it
    does not belong to. Archival has its own endpoint so the archived set cannot
    be entered by an ordinary edit.
    """

    model_config = ConfigDict(extra="forbid")

    url: str = Field(
        min_length=1,
        max_length=MAX_KNOWLEDGE_URL_LENGTH,
        examples=["https://example.com/article"],
    )
    title: str | None = Field(default=None, max_length=MAX_BOOKMARK_TITLE_LENGTH)
    description: str | None = Field(default=None, max_length=MAX_NOTE_TEXT_LENGTH)

    @field_validator("url", mode="before")
    @classmethod
    def _check_url(cls, value: Any) -> Any:
        return _require_web_url(value, "url")


class BookmarkUpdate(_StrippedText, BaseModel):
    """Partial update of a bookmark.

    ``url`` *is* writable — re-saving a moved link is ordinary — and the
    derived ``domain`` is recomputed with it.
    """

    model_config = ConfigDict(extra="forbid")

    url: str | None = Field(default=None, min_length=1, max_length=MAX_KNOWLEDGE_URL_LENGTH)
    title: str | None = Field(default=None, max_length=MAX_BOOKMARK_TITLE_LENGTH)
    description: str | None = Field(default=None, max_length=MAX_NOTE_TEXT_LENGTH)

    @field_validator("url", mode="before")
    @classmethod
    def _check_url(cls, value: Any) -> Any:
        return _require_web_url(value, "url")


class BookmarkRead(BaseModel):
    """A bookmark as returned by the endpoints.

    ``domain`` is the server's derivation, never the client's.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    owner_id: UUID
    url: str
    title: str | None
    description: str | None
    domain: str | None
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


# --------------------------------------------------------------------------- #
# Documents
# --------------------------------------------------------------------------- #


class DocumentCreate(_StrippedText, BaseModel):
    """Creation payload for a document.

    **Metadata only.** There is no file, no upload and no extracted text in this
    phase — parsing a PDF is later work — so this names what a document *is* and
    gives the ingestion phase a row to attach to.
    """

    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1, max_length=MAX_DOCUMENT_FILENAME_LENGTH)
    title: str | None = Field(default=None, max_length=MAX_DOCUMENT_TITLE_LENGTH)
    description: str | None = Field(default=None, max_length=MAX_NOTE_TEXT_LENGTH)
    document_type: str | None = Field(default=None, max_length=MAX_DOCUMENT_TYPE_LENGTH)


class DocumentUpdate(_StrippedText, BaseModel):
    """Partial update of a document's metadata.

    ``filename`` is writable so a corrected name can be fixed; the row describes
    the same file throughout.
    """

    model_config = ConfigDict(extra="forbid")

    filename: str | None = Field(
        default=None, min_length=1, max_length=MAX_DOCUMENT_FILENAME_LENGTH
    )
    title: str | None = Field(default=None, max_length=MAX_DOCUMENT_TITLE_LENGTH)
    description: str | None = Field(default=None, max_length=MAX_NOTE_TEXT_LENGTH)
    document_type: str | None = Field(default=None, max_length=MAX_DOCUMENT_TYPE_LENGTH)


class DocumentRead(BaseModel):
    """A document as returned by the endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    owner_id: UUID
    filename: str
    title: str | None
    description: str | None
    document_type: str | None
    created_at: datetime
    updated_at: datetime


# --------------------------------------------------------------------------- #
# Categories
# --------------------------------------------------------------------------- #


class CategoryCreate(_StrippedText, BaseModel):
    """Creation payload for a category node.

    ``parent_id`` must be a category **the caller owns**, and the service checks
    that through an owner-scoped lookup before the write; a parent that would
    close a cycle is refused for the same reason a self-parent is refused in the
    database, but a deeper cycle needs a walk and is the service's job.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=MAX_CATEGORY_NAME_LENGTH, examples=["Databases"])
    parent_id: UUID | None = None


class CategoryUpdate(_StrippedText, BaseModel):
    """Partial update of a category.

    Re-parenting is an ordinary edit and the cycle check runs on it exactly as it
    does on create.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=MAX_CATEGORY_NAME_LENGTH)
    parent_id: UUID | None = None


class CategoryRead(BaseModel):
    """A category as returned by the endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    owner_id: UUID
    name: str
    parent_id: UUID | None
    created_at: datetime
    updated_at: datetime


# --------------------------------------------------------------------------- #
# Links
# --------------------------------------------------------------------------- #


class KnowledgeLinkCreate(BaseModel):
    """Payload for recording one directed edge.

    The endpoints are typed ids rather than a table name: ``source_type`` is what
    decides which table ``source_id`` points at, so the endpoint has to carry its
    own kind. ``owner_id`` is absent because the endpoints themselves are
    resolved through owner-scoped queries before anything is written — the
    polymorphism means the database cannot do it.

    **A self edge is refused**, by ``ck_knowledge_links_no_self_edge`` in the
    database, so the refusal is total rather than something this schema has to
    remember. Every *other* cycle (``A -> B`` alongside ``B -> A``) is legal and
    means what it says.
    """

    model_config = ConfigDict(extra="forbid")

    source_type: KnowledgeEntityType
    source_id: UUID
    target_type: KnowledgeEntityType
    target_id: UUID
    link_type: KnowledgeLinkType = Field(
        default=KnowledgeLinkType.RELATED_TO,
        description="Part of the edge's identity: the same pair may carry two types.",
    )


class KnowledgeLinkRead(BaseModel):
    """One edge as returned by the link endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    owner_id: UUID
    source_type: str = Field(description="One of the ``KnowledgeEntityType`` values.")
    source_id: UUID
    target_type: str
    target_id: UUID
    link_type: str = Field(description="One of the ``KnowledgeLinkType`` values.")
    created_at: datetime


class BacklinksResponse(BaseModel):
    """The edges pointing *at* one node — the second half of the link API.

    Separate from :class:`KnowledgeLinkRead` because a backlink panel is a
    different question from "everything this note links to": the caller already
    knows which node they are looking at, so the response names it and carries
    the edges rather than making the client re-derive the node from row 1.
    """

    model_config = ConfigDict(from_attributes=True)

    entity_type: str = Field(description="One of the ``KnowledgeEntityType`` values.")
    entity_id: UUID
    backlinks: list[KnowledgeLinkRead] = Field(default_factory=list)
    total: int = Field(default=0, ge=0)


# --------------------------------------------------------------------------- #
# Graph and search
# --------------------------------------------------------------------------- #


class KnowledgeSearchKind(StrEnum):
    """What ``GET /knowledge/search`` may be asked to look in.

    **This is deliberately a different set from**
    :class:`~app.models.enums.KnowledgeEntityType`. The entity type names the
    kinds an *edge* may point at, and a bookmark has no label and a document is
    metadata for a file nothing has uploaded yet, so neither is a graph node. The
    kinds a *search* may look in have nothing to do with either of those:
    bookmarks and documents both hold text a person typed and both are worth
    searching, and both used to be unfindable through ``?type=`` even though an
    unfiltered search returned them — ``?type=bookmark`` answered **422** for the
    very kind the same response was filling in.

    ``category`` is absent for the reason it is absent everywhere else: a category
    is a one-word label with no free text to match.
    """

    NOTE = "note"
    CONCEPT = "concept"
    RESOURCE = "resource"
    BOOKMARK = "bookmark"
    DOCUMENT = "document"


class KnowledgeGraphNode(BaseModel):
    """One node.

    ``id`` is the row's id and ``type`` says which table it is in, which together
    are the only key that identifies a node across the polymorphic edge table.
    """

    id: UUID
    type: str = Field(description="One of the ``KnowledgeEntityType`` values.")
    label: str


class KnowledgeGraphEdge(BaseModel):
    """One edge, referring to nodes by id and disambiguated by the node type."""

    source: UUID
    target: UUID
    source_type: str
    target_type: str
    type: str = Field(description="One of the ``KnowledgeLinkType`` values.")


class KnowledgeGraph(BaseModel):
    """The caller's graph, bounded.

    ``limit`` is echoed so a client can tell a *small graph* from a *capped*
    one — the two are indistinguishable from ``nodes`` alone, and a caller who
    cannot tell them apart will draw a hairball and call it their knowledge
    base. ``truncated`` answers it directly: the query returned exactly
    ``limit`` nodes and more exist.
    """

    nodes: list[KnowledgeGraphNode] = Field(default_factory=list)
    edges: list[KnowledgeGraphEdge] = Field(default_factory=list)
    limit: int = Field(ge=1)
    truncated: bool = False


class KnowledgeSearchResult(BaseModel):
    """Search results, **grouped by entity type rather than merged**.

    One list per kind so a client can render "3 notes, 1 concept" as two
    sections. Merging them would need a relevance model over heterogeneous
    columns; sorting the concatenation by ``created_at`` would be a ranking that
    answers a different question from the one asked.

    ``limit`` bounds **each** group — it is not a total — and every list is
    filled by its own bounded ``ILIKE`` query rather than by one unbounded read
    filtered in Python.

    **``documents`` is here because ``GET /knowledge/documents?search=`` already
    found them.** A caller that does not know which table a thing lives in asks
    the search endpoint; a caller that does know asks the list. Documents were in
    the second and not the first, so the same term answered differently depending
    on which route the client happened to know about — and the global search is
    the one that exists precisely to remove that question.
    """

    query: str
    limit: int = Field(ge=1)
    notes: list[NoteRead] = Field(default_factory=list)
    concepts: list[ConceptRead] = Field(default_factory=list)
    resources: list[ResourceRead] = Field(default_factory=list)
    bookmarks: list[BookmarkRead] = Field(default_factory=list)
    documents: list[DocumentRead] = Field(default_factory=list)

    @property
    def total(self) -> int:
        """Rows returned across all five groups."""
        return (
            len(self.notes)
            + len(self.concepts)
            + len(self.resources)
            + len(self.bookmarks)
            + len(self.documents)
        )


# The API surface names these ``KnowledgeGraph`` and ``KnowledgeSearchResult``.
# The brief calls them ``GraphResponse``/``SearchResponse``; both names are bound
# to the same classes so neither the router nor a caller reading the spec has to
# know which one won.
GraphResponse = KnowledgeGraph
SearchResponse = KnowledgeSearchResult
