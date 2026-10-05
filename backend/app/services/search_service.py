"""Cross-entity search: ranking, snippets, grouping, pagination.

Where the work happens
----------------------
:mod:`app.repositories.search` runs one bounded statement per table and returns
rows. Everything a caller actually reads — which column a hit matched, where in
the snippet the term landed, which of two equally recent rows comes first,
which project a task is in — is decided here, in plain deterministic code.

**No model is involved.** NEXUS runs one classifier, it is not a language model,
and nothing in this module consults it. A term is matched with ``ILIKE``, ranked
by column position and timestamp, and trimmed with ``re``. There is no semantic
similarity, no stemming, no "did you mean" and no synonym expansion: the ranking
is reproducible from the data, which is the property a search palette needs far
more than a cleverer one.

The ranking, and why it is a total order
----------------------------------------
A hit is ordered by **(matched-column position, recency, id)**:

1. **Matched-column position.** A term in a task's title is a stronger signal
   than the same term in its description, and a term in the *first* body column
   a stronger one still. The columns are listed in priority order on
   :class:`~app.repositories.search.SearchTarget` and the index of the first one
   that matched *is* the rank — so the ranking falls out of the same
   description the query is built from rather than being a second list that has
   to be kept in step with the first.
2. **Recency**, newest first. Ties on identical microseconds are common in a
   fixture and rare in production, which is exactly the kind of thing that makes
   an ordering look stable until it is not.
3. **Id**, ascending. Unique, so the order is *total*: two identical calls over
   identical rows return the same list in the same order, every time. This is a
   requirement of the endpoint rather than a nicety — a palette that reorders
   itself between keystrokes reads as a bug in the product.

Pagination: across the union, once
----------------------------------
``limit``/``offset`` slice the **flat ranked list**, and ``groups`` partition
the resulting page. Not per entity kind: a per-kind page of 50 would be 550 hits
for one request, would make ``meta.total`` depend on how many kinds were asked
for, and would mean "page 2" shows something entirely different from page 1
whenever a new kind is added. One list, one page, one ``total``.

**``meta.total`` counts hits *discovered*, not rows matching.** With
:data:`PER_ENTITY_SCAN` bounding each kind, the union cannot exceed
``kinds * PER_ENTITY_SCAN``. That bound is a feature — see the cap's own
discussion below — and it is stated on the response rather than left for a
caller to infer from a ``total`` that stopped growing.

The per-kind cap, and why it exists
-----------------------------------
:data:`PER_ENTITY_SCAN` is fifty rows per kind per request. This build has no
``pg_trgm`` and no full-text index, so ``ILIKE '%term%'`` is a sequential scan
of one table; without a cap, a common term would pull every note the user has
ever written into the process to rank most of them out of sight. Fifty is more
than any palette in the product shows — the brief's own grouped example renders
a handful of blocks — so the cap costs a scroll nobody takes and buys a bounded
statement.

Two other bounds are enforced here rather than only at the router, because a
bound enforced in one place is a bound a service caller walks past:
:data:`MAX_QUERY_CHARS` on the term and :data:`MAX_TYPE_FILTERS` on the kinds.

Filters that no searched kind can honour are refused, not ignored
----------------------------------------------------------------
``tag_ids`` against kinds that carry no joinable tags, ``project_id`` against
kinds with no project column, a ``status`` or ``priority`` outside the searched
kinds' vocabularies. Silently dropping any of them would return an unfiltered
page that *looks* filtered — the caller asks for something, gets everything, and
has no way to tell. Each is a :class:`~app.core.exceptions.ValidationError`
naming what was asked for, and the router renders it through the shared envelope
as a 422. The test is deliberately **no searched kind**, not *every* searched
kind: a filter one kind can honour and another cannot is a normal request.

``status`` and ``priority`` additionally differ *between* the kinds that have
them, so a value is validated against the union over the kinds being searched and
then applied to each of them. ``status=completed`` therefore narrows tasks,
projects, goals and recommendations together — which is what a caller who typed
one status into one box meant — and matches no notes, because ``completed`` is
not in :class:`~app.models.enums.NoteStatus`.

Scope filters and state filters are two different rules
------------------------------------------------------
``project_id`` and ``tag_ids`` are **scope**: they answer "inside this", and a
kind that cannot be inside it contributes nothing at all. Asking for a project
and getting a user's notes back would be answering a question nobody asked.

``status``, ``priority``, ``from`` and ``to`` are **state**: they answer "in this
condition", and they narrow only the kinds that carry the column. A note is not
``todo``, so ``status=todo`` must not hide it — dropping every note out of a
result set on the strength of a word about tasks would be a filter wider than the
one the caller typed.

The distinction is the whole reason ``project_id`` is not validated the same way
``status`` is, and it is the reason a filter applied to one kind is *refused*
only when **no** searched kind could have honoured it.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime

from app.core.exceptions import ValidationError
from app.core.logging import get_logger, log_event
from app.models.enums import (
    LearningGoalStatus,
    NoteStatus,
    ProjectPriority,
    ProjectStatus,
    RecommendationPriority,
    RecommendationStatus,
    RiskStatus,
    TaskPriority,
    TaskStatus,
)
from app.repositories.search import SEARCH_TARGETS, SearchRepository, SearchRow
from app.schemas.common import PageMeta
from app.schemas.search import (
    MAX_QUERY_CHARS,
    MAX_TYPE_FILTERS,
    MIN_QUERY_CHARS,
    SearchEntityKind,
    SearchGroupRead,
    SearchHitRead,
    SearchResponse,
)

__all__ = ["SearchService"]

logger = get_logger(__name__)


def _values(enum: type) -> frozenset[str]:
    """The members of ``enum`` as their wire strings."""
    return frozenset(member.value for member in enum)


def _normalise(value: str | None) -> str | None:
    """Fold a status/priority filter to the form the columns store.

    The columns are plain lowercase strings validated on every write path, so
    folding here means the filter compares the same way the rows do. An empty or
    whitespace-only value folds to ``None`` — a filter the caller did not
    meaningfully supply is not a filter that matches nothing.
    """
    if value is None:
        return None
    folded = value.strip().lower()
    return folded or None


#: The status vocabulary of each kind that has a ``status`` column. Kinds absent
#: from this mapping have no status column, so a status filter does not narrow
#: them rather than being validated against them.
_STATUSES_BY_KIND: Mapping[str, frozenset[str]] = {
    "project": _values(ProjectStatus),
    "task": _values(TaskStatus),
    "note": _values(NoteStatus),
    "goal": _values(LearningGoalStatus),
    "risk": _values(RiskStatus),
    "recommendation": _values(RecommendationStatus),
}

#: The priority vocabulary of each kind that has a ``priority`` column. Absent
#: for ``risks``, whose ``severity`` is a different column with a different job.
_PRIORITIES_BY_KIND: Mapping[str, frozenset[str]] = {
    "project": _values(ProjectPriority),
    "task": _values(TaskPriority),
    # A learning goal stores a ProjectPriority grade against the same four
    # values, which `app/models/learning.py` states on the column.
    "goal": _values(ProjectPriority),
    "recommendation": _values(RecommendationPriority),
}

#: Kinds whose project column can be filtered on.
_PROJECT_FILTERABLE: frozenset[str] = frozenset(
    kind for kind, target in SEARCH_TARGETS.items() if target.project_column is not None
)

#: Kinds whose tags are actually joinable. Only tasks: `note_tags` exists as a
#: table but no service in the codebase ever writes a row to it, so honouring it
#: here would advertise a filter that cannot narrow anything.
_TAG_FILTERABLE: frozenset[str] = frozenset(
    kind for kind, target in SEARCH_TARGETS.items() if target.tag_table is not None
)


def _target_of(row: SearchRow):
    """The :class:`~app.repositories.search.SearchTarget` a row came from.

    Rows are only ever built from a target, so the lookup cannot miss in
    practice; it raises rather than returning ``None`` because a hit with no
    target could not be rendered, and a ``None`` here would surface much later
    as an ``AttributeError`` on a column list.
    """
    target = SEARCH_TARGETS.get(row.kind)
    if target is None:  # pragma: no cover - unreachable by construction
        raise ValidationError("Unknown search entity kind.", details={"kind": row.kind})
    return target


def _relative_date(moment: datetime, *, now: datetime) -> str:
    """How long ago ``moment`` was, in words.

    A palette row has room for one short phrase, not an ISO timestamp a caller
    has to format itself; and "4 days ago" is the answer a person wants, where
    ``2026-01-01T09:00:00Z`` is the answer a log wants. Counting stops at a year
    because "437 days ago" is not a phrase anyone reads.

    The comparison is against an explicit ``now`` rather than a clock read here,
    so the function is testable at a fixed instant and so two calls in one
    request cannot straddle midnight and disagree.
    """
    moment = moment.astimezone(UTC)
    now = now.astimezone(UTC)
    days = (now.date() - moment.date()).days
    if days < 0:
        return moment.date().isoformat()
    if days == 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 7:
        return f"{days} days ago"
    if days < 31:
        weeks = days // 7
        return f"{weeks} week{'' if weeks == 1 else 's'} ago"
    if days < 365:
        months = days // 30
        return f"{months} month{'' if months == 1 else 's'} ago"
    years = days // 365
    return f"{years} year{'' if years == 1 else 's'} ago"


def _snippet(text: str, match: re.Match[str], *, radius: int, limit: int) -> tuple[str, int, int]:
    """Trim ``text`` to at most ``limit`` characters around ``match``.

    Returns the trimmed text and the match's offsets *within the trimmed text*,
    so a client can highlight the region without re-finding it and without the
    highlight being markup that could collide with the user's own characters.

    The window snaps outward to a space when there is one, which is what keeps a
    trim from cutting a word in half; where there is no space — a URL, a path —
    it cuts mid-token rather than failing, because a snippet is a preview and a
    truncated word still identifies the row.
    """
    start, end = match.span()
    if len(text) <= limit:
        return text, start, end

    window_start = max(0, start - radius)
    window_end = min(len(text), end + radius)

    if window_start > 0:
        space = text.find(" ", window_start, start)
        if space != -1:
            window_start = space + 1
    if window_end < len(text):
        space = text.rfind(" ", end, window_end)
        if space != -1:
            window_end = space

    prefix = "…" if window_start > 0 else ""
    suffix = "…" if window_end < len(text) else ""
    trimmed = prefix + text[window_start:window_end] + suffix
    return trimmed, len(prefix) + start - window_start, len(prefix) + end - window_start


class SearchService:
    """Rank, group and paginate one cross-entity search.

    Constructed per request from a single repository. The service holds no state
    between calls: two searches for the same term at two different moments must
    be able to differ (a row was edited in between), and a service that cached
    the first answer would make that impossible to express.
    """

    __slots__ = ("repository",)

    #: How many rows one entity kind may contribute to one request. See the
    #: module docstring for why the scan is capped at all.
    PER_ENTITY_SCAN: int = 50

    #: Characters kept either side of a match before the snippet is trimmed.
    SNIPPET_RADIUS: int = 60

    #: Hard ceiling on a rendered snippet, however long the source column is.
    #: Without it a note body of a hundred thousand characters would be returned
    #: whole on the strength of one matched word.
    MAX_SNIPPET_CHARS: int = 200

    def __init__(self, repository: SearchRepository) -> None:
        self.repository = repository

    async def search(
        self,
        *,
        owner: uuid.UUID,
        term: str,
        kinds: Sequence[SearchEntityKind] | None = None,
        project_id: uuid.UUID | None = None,
        status: str | None = None,
        priority: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        tag_ids: Sequence[uuid.UUID] = (),
        limit: int,
        offset: int,
        max_page_size: int,
        now: datetime | None = None,
    ) -> SearchResponse:
        """Run one search over the caller's records and return the ranked page.

        Args:
            owner: The caller's account. Every statement carries it, so nothing
                belonging to another account can enter the result set at any point
                — not by an id the caller supplied, and not by a filter.
            term: Free text, trimmed here. Whitespace-only is rejected.
            kinds: The entity kinds to read. ``None`` means all thirteen.
            project_id: Scope filter — only rows filed under one project, and
                kinds with no project column contribute nothing. Refused when no
                searched kind has one.
            status: One status value, validated against the union over ``kinds``
                and applied to each kind that has a status column.
            priority: Same, for ``priority``. Ignored by ``risks``, which carry a
                severity rather than a priority.
            date_from: Inclusive lower bound on each kind's own date column.
            date_to: Inclusive upper bound, widened to the end of the day for
                kinds whose date column is a timestamp.
            tag_ids: Scope filter — tasks carrying **every** listed tag, and kinds
                with no joinable tag table contribute nothing. Refused when no
                searched kind has one.
            limit: Page size, capped at ``max_page_size``.
            offset: Rows to skip from the ranked union.
            max_page_size: The caller's own page ceiling. Passed in rather than
                read from settings so a deployment's cap is the one that applies.
            now: The instant relative dates are measured against. Defaults to the
                current UTC time; tests pass a fixed one.

        Returns:
            A :class:`~app.schemas.search.SearchResponse` whose ``hits`` and
            ``groups`` describe the same page. An empty result is a 200 with two
            empty lists, never an error: "nothing matched" is the answer to a
            search, and a caller must not have to distinguish it from a fault.

        Raises:
            ValidationError: For a blank or over-long term, a limit outside
                ``1..max_page_size``, a negative offset, a reversed date range,
                a status or priority outside the searched kinds' vocabularies, or
                a filter naming a capability the searched kinds do not have.
        """
        query = self._validate_term(term)
        self._validate_page(limit=limit, offset=offset, max_page_size=max_page_size)
        self._validate_range(date_from=date_from, date_to=date_to)

        selected = self._resolve_kinds(kinds)
        self._validate_filters(
            selected,
            project_id=project_id,
            status=status,
            priority=priority,
            tag_ids=tag_ids,
        )

        status = _normalise(status)
        priority = _normalise(priority)

        rows: list[tuple[int, SearchRow]] = []
        for kind in selected:
            target = SEARCH_TARGETS[str(kind)]
            if project_id is not None and target.project_column is None:
                continue
            if tag_ids and target.tag_table is None:
                continue
            found = await self.repository.search(
                target,
                owner_id=owner,
                term=query,
                limit=self.PER_ENTITY_SCAN,
                status=status,
                priority=priority,
                project_id=project_id,
                date_from=date_from,
                date_to=date_to,
                tag_ids=tag_ids,
            )
            for row in found:
                rows.append((self._match_rank(target, row, query), row))

        rows.sort(key=lambda pair: self._sort_key(pair[0], pair[1]))

        moment = now or datetime.now(UTC)
        names = await self.repository.project_names(
            owner, [row.project_id for _, row in rows if row.project_id is not None]
        )

        hits = [
            self._to_hit(_target_of(row), rank, row, query=query, names=names, now=moment)
            for rank, row in rows
        ]
        page = hits[offset : offset + limit]
        response = SearchResponse(
            query=query,
            hits=page,
            groups=self._group(page),
            meta=PageMeta(total=len(hits), limit=limit, offset=offset),
        )

        log_event(
            logger,
            logging.INFO,
            "search_served",
            kinds=[str(kind) for kind in selected],
            text_chars=len(query),
            hits=len(page),
            total=response.meta.total,
            limit=limit,
            offset=offset,
        )
        return response

    # -- validation ----------------------------------------------------------

    @staticmethod
    def _validate_term(term: str) -> str:
        """Trim the term and refuse one that is blank or over-long.

        A blank term is refused rather than answered with everything: matching no
        text at all is not a search, and a caller who sent one has a bug.
        """
        query = term.strip()
        if len(query) < MIN_QUERY_CHARS:
            raise ValidationError(
                "The search term must contain at least one non-whitespace character.",
                details={"field": "q"},
            )
        if len(query) > MAX_QUERY_CHARS:
            raise ValidationError(
                f"The search term may be at most {MAX_QUERY_CHARS} characters.",
                details={"field": "q", "max_length": MAX_QUERY_CHARS},
            )
        return query

    @staticmethod
    def _validate_page(*, limit: int, offset: int, max_page_size: int) -> None:
        """Refuse a page the caller could not be served.

        A rejection rather than a clamp: a client that asked for 5000 and got 200
        cannot tell a capped page from a short one.
        """
        if limit < 1 or limit > max_page_size:
            raise ValidationError(
                f"The page size must be between 1 and {max_page_size}.",
                details={"field": "limit", "max_limit": max_page_size},
            )
        if offset < 0:
            raise ValidationError(
                "The page offset must not be negative.", details={"field": "offset"}
            )

    @staticmethod
    def _validate_range(*, date_from: date | None, date_to: date | None) -> None:
        """Refuse a range that cannot match anything, rather than returning empty."""
        if date_from is not None and date_to is not None and date_from > date_to:
            raise ValidationError(
                "`from` must not be later than `to`.",
                details={"field": "from"},
            )

    @staticmethod
    def _resolve_kinds(kinds: Sequence[SearchEntityKind] | None) -> list[SearchEntityKind]:
        """The kinds to read, de-duplicated and in declaration order.

        Declaration order rather than the order the caller listed them, so two
        requests naming the same set in a different order produce byte-identical
        responses — the same reason
        :func:`app.ml.router.routing_taxonomy` walks ``INTENT_SPECS`` instead of
        a frozenset.
        """
        if not kinds:
            return list(SearchEntityKind)
        if len(kinds) > MAX_TYPE_FILTERS:
            raise ValidationError(
                f"At most {MAX_TYPE_FILTERS} entity kinds may be searched at once.",
                details={"field": "types", "max_types": MAX_TYPE_FILTERS},
            )
        wanted = {str(kind) for kind in kinds}
        unknown = sorted(wanted - set(SEARCH_TARGETS))
        if unknown:
            raise ValidationError(
                "Unknown search entity kind.", details={"field": "types", "unknown": unknown}
            )
        return [kind for kind in SearchEntityKind if str(kind) in wanted]

    @staticmethod
    def _validate_filters(
        kinds: Sequence[SearchEntityKind],
        *,
        project_id: uuid.UUID | None,
        status: str | None,
        priority: str | None,
        tag_ids: Sequence[uuid.UUID],
    ) -> None:
        """Refuse a filter no searched kind can honour.

        The rule is uniform and the reason is stated once: a filter that is
        silently dropped produces a response that *looks* filtered and is not,
        which is worse than an error the caller can act on.
        """
        names = [str(kind) for kind in kinds]

        if project_id is not None and not any(name in _PROJECT_FILTERABLE for name in names):
            raise ValidationError(
                "None of the requested entity kinds is filed under a project.",
                details={
                    "field": "project_id",
                    "filterable_kinds": sorted(_PROJECT_FILTERABLE),
                    "requested": names,
                },
            )

        if tag_ids and not any(name in _TAG_FILTERABLE for name in names):
            raise ValidationError(
                "None of the requested entity kinds carries tags.",
                details={
                    "field": "tag_ids",
                    "filterable_kinds": sorted(_TAG_FILTERABLE),
                    "requested": names,
                },
            )

        if status is not None:
            vocabulary = set().union(*(_STATUSES_BY_KIND.get(name, frozenset()) for name in names))
            if status.strip().lower() not in vocabulary:
                raise ValidationError(
                    "No searched entity kind has that status.",
                    details={
                        "field": "status",
                        "accepted": sorted(vocabulary),
                        "requested": names,
                    },
                )

        if priority is not None:
            vocabulary = set().union(
                *(_PRIORITIES_BY_KIND.get(name, frozenset()) for name in names)
            )
            if priority.strip().lower() not in vocabulary:
                raise ValidationError(
                    "No searched entity kind has that priority.",
                    details={
                        "field": "priority",
                        "accepted": sorted(vocabulary),
                        "requested": names,
                    },
                )

    # -- ranking -------------------------------------------------------------

    @staticmethod
    def _match_rank(target, row: SearchRow, query: str) -> int:
        """Which column priority the row matched at.

        The index of the first column whose text contains the term, or the
        column count when the row matched without Python finding it — which can
        happen for a case-folding difference between ``ILIKE`` and
        :mod:`re`. Such a row ranks last rather than being dropped: it matched
        in the database, and this service does not get to overrule that.
        """
        needle = re.compile(re.escape(query), re.IGNORECASE)
        for index, value in enumerate(row.values):
            if needle.search(value):
                return index
        return len(row.values)

    @staticmethod
    def _sort_key(rank: int, row: SearchRow) -> tuple[int, float, str]:
        """The total order: column position, then recency, then id.

        ``id`` is unique, so this key has no ties and two identical calls over
        identical rows cannot produce two different lists. The id is compared as
        its canonical string because a UUID is orderable but its ordering is not
        the one anybody would guess from the displayed value.
        """
        return (rank, -row.recency.timestamp(), str(row.id))

    def _snippet_for(self, text: str, query: str) -> tuple[str, int, int]:
        """Locate the term and trim around it. See :func:`_snippet`."""
        found = re.search(re.escape(query), text, re.IGNORECASE)
        if found is None:
            return text[: self.MAX_SNIPPET_CHARS], 0, 0
        return _snippet(text, found, radius=self.SNIPPET_RADIUS, limit=self.MAX_SNIPPET_CHARS)

    def _to_hit(
        self, target, rank: int, row: SearchRow, *, query: str, names: Mapping, now: datetime
    ) -> SearchHitRead:
        """Turn one ranked row into the hit the caller reads.

        The snippet comes from the column at ``rank`` — the first column that
        matched — which is why ``matched_field`` is reported alongside it: the
        client is told *what* it is looking at, not only where the term landed.
        """
        index = min(rank, len(row.values) - 1)
        source = row.values[index] or row.values[0]
        snippet, start, end = self._snippet_for(source, query)
        return SearchHitRead(
            kind=target.kind,
            id=row.id,
            title=row.values[0][:300],
            snippet=snippet,
            match_start=start,
            match_end=end,
            matched_field=target.columns[index].key,
            project_id=row.project_id,
            project_name=names.get(row.project_id) if row.project_id is not None else None,
            relative_date=_relative_date(row.recency, now=now),
            updated_at=row.recency,
        )

    @staticmethod
    def _group(hits: Sequence[SearchHitRead]) -> list[SearchGroupRead]:
        """Partition the page by kind, groups in first-appearance order.

        Built from the page rather than from the full result set, so ``groups``
        and ``hits`` can never describe different things — the whole reason both
        are returned is that a client may render either.
        """
        ordered: dict[str, list[SearchHitRead]] = {}
        for hit in hits:
            ordered.setdefault(str(hit.kind), []).append(hit)
        return [SearchGroupRead(kind=kind, hits=group) for kind, group in ordered.items()]
