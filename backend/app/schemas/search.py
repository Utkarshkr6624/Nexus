"""Wire contract for the global search surface.

Phase 13 adds one cross-entity search over tables that already exist. This module
is everything the HTTP layer knows about it — the entity kinds, the caps, and
the response shape — and it deliberately imports no ORM model, so the response
contract can be read, reviewed and asserted on without a database anywhere in
sight. Which table an entity kind *is* is
:mod:`app.repositories.search`'s statement; this file names the kinds and
nothing else.

Two things are decided here rather than in the router, and both are load-bearing.

**The matched region is reported as offsets, not as markup.** A search snippet
carries ``match_start``/``match_end`` character offsets into ``snippet`` rather
than wrapping the hit in sentinel characters. Sentinel markup would be one more
string that has to be escaped on the way out and parsed on the way in, and the
text being highlighted is *whatever the user wrote* — a note body containing the
sentinels verbatim would render as a broken highlight, or, worse, as a false
one. Offsets are coordinates, not content: there is nothing in them to escape
and nothing a caller can inject into.

**The caps are named and exported, not buried.** ``MAX_QUERY_CHARS`` and
``MAX_TYPE_FILTERS`` are read by
:mod:`app.api.v1.search` to build the ``Query`` constraints *and* by
:mod:`app.services.search_service` to enforce the same bound for a non-HTTP
caller. A bound that lives only in the router is a bound a service caller walks
straight past, and a bound that lives only in the service is one the OpenAPI
document cannot state.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import PageMeta

__all__ = [
    "MAX_QUERY_CHARS",
    "MAX_TYPE_FILTERS",
    "MIN_QUERY_CHARS",
    "SearchEntityKind",
    "SearchGroupRead",
    "SearchHitRead",
    "SearchResponse",
]


class SearchEntityKind(StrEnum):
    """The tables one search reads.

    A closed set, and derived by hand from the models rather than introspected:
    a search that silently gained a kind when a table was added would change the
    meaning of an existing query without anyone deciding it. Adding a kind is a
    change to this enum **and** to ``SEARCH_TARGETS`` in
    :mod:`app.repositories.search`, and :func:`tests.test_search_api` asserts the
    two agree.

    The declaration order is the tie-break order for groups that would otherwise
    rank equally, and it runs from the most-worked surface to the least, so an
    unranked palette reads projects before risks.
    """

    PROJECT = "project"
    TASK = "task"
    NOTE = "note"
    RESOURCE = "resource"
    BOOKMARK = "bookmark"
    DOCUMENT = "document"
    CONCEPT = "concept"
    REPOSITORY = "repository"
    GOAL = "goal"
    SKILL = "skill"
    EVENT = "event"
    RISK = "risk"
    RECOMMENDATION = "recommendation"


#: The shortest term accepted. One character, and stated here rather than left to
#: "well, ``min_length=1``": a zero-length query matches every row of every
#: table, which is a table dump wearing a search endpoint's clothes.
MIN_QUERY_CHARS: int = 1

#: The longest term accepted, in characters.
#:
#: Two hundred is a phrase, not a document. It is long enough for a pasted
#: sentence fragment and short enough that the ``ILIKE '%term%'`` pattern stays
#: a cheap literal to hand to PostgreSQL, and it is bounded independently of
#: ``MAX_PAGE_SIZE`` because the two costs are unrelated: a short term over a
#: large table is a slow scan, and a long term is a large pattern.
MAX_QUERY_CHARS: int = 200

#: How many values ``?types=`` may carry.
#:
#: Set to the size of :class:`SearchEntityKind`. It exists so the router can
#: answer "you asked for more entity kinds than exist" as a 422 naming the
#: parameter, instead of accepting a list that happens to be longer than the
#: vocabulary and silently narrowing it.
MAX_TYPE_FILTERS: int = 13


class SearchHitRead(BaseModel):
    """One matching row, as a palette row renders it.

    ``project_id``/``project_name`` are null for a hit that is not *inside* a
    project — including a ``PROJECT`` hit, which is the project rather than
    something filed under one. The rule is deliberately narrow so the frontend
    has no special case: it shows "in &lt;project&gt;" when the name is present
    and nothing when it is not.
    """

    model_config = ConfigDict(extra="forbid")

    kind: SearchEntityKind = Field(description="Which table this row came from.")
    id: UUID = Field(description="The row's own primary key.")
    title: str = Field(description="The row's own label — name, or title.")
    snippet: str = Field(
        description=(
            "Text from the matched column, trimmed around the hit with an ellipsis. "
            "Rendered as plain text: nothing here is HTML and no endpoint emits any."
        )
    )
    match_start: int = Field(
        ge=0,
        description="Start of the matched region, as a character offset into `snippet`.",
    )
    match_end: int = Field(
        ge=0, description="End of the matched region, as an offset into `snippet`."
    )
    matched_field: str = Field(
        description=(
            "The column that produced the snippet and the ranking, by its model "
            "attribute name — `name`, `title`, `content`, `description`, `url`, "
            "`filename`, `local_path`, `category`, `reason` or `target_topic`. "
            "This is the column *order* a hit was found in, not an arbitrary "
            "pick. A `bookmark` leads with `url` and a `document` with `filename` "
            "because both titles are nullable and the label must not be."
        )
    )
    project_id: UUID | None = Field(
        default=None, description="The project this row is filed under, when it has one."
    )
    project_name: str | None = Field(
        default=None, description="That project's name, resolved in the caller's scope."
    )
    relative_date: str | None = Field(
        default=None,
        description=(
            "How recently the row was touched — `today`, `yesterday`, `4 days ago`, "
            "`2 months ago`, or an ISO date once it is old enough that counting days "
            "stops helping. Always present for the kinds searched here, because every "
            "table in the union carries `updated_at`."
        ),
    )
    updated_at: datetime = Field(description="The row's `updated_at`, unrounded.")


class SearchGroupRead(BaseModel):
    """One entity kind's share of the page, for the grouped rendering."""

    model_config = ConfigDict(extra="forbid")

    kind: SearchEntityKind
    hits: list[SearchHitRead]


class SearchResponse(BaseModel):
    """The whole answer: one ranked list, and the same hits grouped by kind.

    Both views describe the **same page**. ``hits`` is the flat list a command
    palette walks top to bottom; ``groups`` partitions exactly those hits and
    adds nothing, so a client may render either and never disagree with itself.
    """

    model_config = ConfigDict(extra="forbid")

    query: str = Field(description="The term as it was actually searched for, after trimming.")
    hits: list[SearchHitRead] = Field(
        description="Every hit on this page, in rank order. Deterministic: identical "
        "queries over identical data return this list in this order."
    )
    groups: list[SearchGroupRead] = Field(
        description="The same hits partitioned by kind, groups in first-appearance order."
    )
    meta: PageMeta = Field(
        description=(
            "Pagination counters. `total` counts the hits *discovered* within the "
            "per-kind scan caps, not every matching row in the database — see the "
            "endpoint's pagination notes."
        )
    )
