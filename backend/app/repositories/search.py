"""Cross-entity search persistence: one bounded statement per table.

The whole file is a routing table and one method. :data:`SEARCH_TARGETS` maps an
entity kind to the model, the ownership column, the text columns in *priority
order*, the date column a range filter applies to, and the optional
project/status/priority/tag joins that kind actually has. :meth:`SearchRepository.search`
builds one statement from that description and returns rows; it does not rank,
trim or format anything.

Why a table rather than eleven methods
-------------------------------------
Eleven hand-written ``search_*`` methods would be eleven places to forget the
ownership predicate, and that is the one thing this table must never let a
refactor drop. Here the predicate is written once, in one ``where`` clause, for
every kind: **there is no code path through this file that emits a statement
without ``owner_id``**, because the method takes the owner as a required keyword
argument and has no default that would let a caller omit it.

Three facts about the search medium, stated once here rather than per method
------------------------------------------------------------------------------------
* **No trigram index exists.** This build has neither ``pg_trgm`` nor a
  Postgres full-text configuration, and Phase 13 adds neither — a migration is
  not part of a read-only projection. ``ILIKE '%term%'`` with a leading wildcard
  cannot use a B-tree, so every statement here is a scan of one table, bounded
  in SQL by ``LIMIT``. The limit is the only thing standing between a broad term
  and a full scan of a user's whole backlog; see
  :data:`~app.services.search_service.SearchService.PER_ENTITY_SCAN`.
* **Wildcards inside the term are escaped** by :func:`_search_pattern`, so a
  search for ``50%`` matches the two characters ``5`` and ``0`` and a search for
  ``_`` matches an underscore. Unescaped, either would silently rewrite the
  query's meaning on the caller's behalf.
* **Ordering is a total order.** ``(title-column match, updated_at DESC, id
  ASC)`` — the third term is unique, so two identical calls over identical rows
  cannot differ, and no caller sees an ordering that depends on the physical
  order PostgreSQL happened to return.

The kind vocabulary itself lives in :mod:`app.schemas.search`, and this module
keys :data:`SEARCH_TARGETS` by plain strings rather than importing it: a
repository that imported a Pydantic wire enum would be a repository that could
no longer be reasoned about without the API layer. The two are pinned together
by a test rather than by an import.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from types import MappingProxyType
from typing import Any

from sqlalchemy import ColumnElement, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.sql.elements import ColumnClause

from app.models.developer import GitRepository
from app.models.knowledge import Concept, Note, Resource
from app.models.learning import LearningGoal, Skill
from app.models.planner import CalendarEvent
from app.models.project import Project
from app.models.risk import Recommendation, Risk
from app.models.tag import task_tags
from app.models.task import Task

__all__ = [
    "SEARCH_TARGETS",
    "SearchRepository",
    "SearchRow",
    "SearchTarget",
]


def _search_pattern(term: str) -> str:
    """Wrap a search term in a case-insensitive ``ILIKE`` pattern.

    Deliberately identical to :func:`app.repositories.knowledge._search_pattern`,
    and for the same three reasons stated there: no ``pg_trgm`` to use, a
    leading wildcard is therefore a scan, and the wildcards *inside* the term
    have to be escaped or a caller's string silently becomes a pattern.
    """
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


#: Selected in place of a project column for kinds that have none, so a result
#: row's shape does not depend on which target produced it.
_NULL_COLUMN = literal(None)


@dataclass(frozen=True, slots=True)
class SearchTarget:
    """How one entity kind is read.

    Attributes:
        kind: The wire value, matching :class:`app.schemas.search.SearchEntityKind`.
        model: The ORM class whose table is scanned.
        owner_column: The ownership predicate — ``owner_id`` on most tables,
            ``user_id`` on the ones written before the column was renamed. Carried
            per target rather than assumed, because assuming it would be wrong on
            exactly the tables it matters most for.
        columns: The text columns searched, **in priority order**. The first is
            the row's label and is also what ``title`` is read from; the order
            doubles as the rank, so a match in column 0 outranks a match in
            column 2 at equal recency.
        recency_column: The timestamp used to rank, to order within a kind, and to
            render the relative date. ``updated_at`` on every kind here, because
            "recently touched" is what a search palette means and it is never
            null.
        filter_date_column: The column ``from``/``to`` bound. Not ``recency_column``
            in general: filtering tasks by when you *edited* them is a different
            question from filtering them by when they are *due*, and answering the
            second with the first would quietly return the wrong rows.
        filter_date_is_timestamp: Whether the filter column is a ``Date`` or a
            ``DateTime``, which decides whether a caller's day bound is widened to
            the whole day before it is bound as a parameter.
        project_column: The project this kind can be filed under, when it has one.
        status_column / priority_column: Present only where the table has such a
            column. ``risks`` has ``severity`` rather than ``priority``, so a
            ``priority`` filter deliberately does not narrow risks rather than
            mapping one onto the other.
        tag_table / tag_column: The association table and the entity-side column,
            for the kinds whose tags are actually joinable. Only tasks are; notes
            have a ``note_tags`` table that no service ever writes to.
    """

    kind: str
    model: type
    owner_column: InstrumentedAttribute
    columns: tuple[InstrumentedAttribute, ...]
    recency_column: InstrumentedAttribute
    filter_date_column: InstrumentedAttribute | None
    filter_date_is_timestamp: bool
    project_column: InstrumentedAttribute | None
    status_column: InstrumentedAttribute | None
    priority_column: InstrumentedAttribute | None
    tag_table: Any | None = None
    tag_column: Any | None = None


@dataclass(frozen=True, slots=True)
class SearchRow:
    """One matched row, before ranking or presentation.

    ``values`` is positional against the target's ``columns``, so a caller reads
    ``values[0]`` as the label and walks the rest in rank order without knowing
    which table produced it. Null text is normalised to ``""`` here rather than
    at every use site: ``notes.summary`` and most of the description columns are
    nullable, and a snippet builder that has to null-check is a snippet builder
    that will forget.
    """

    kind: str
    id: uuid.UUID
    values: tuple[str, ...]
    project_id: uuid.UUID | None
    recency: datetime


#: The eleven kinds, in the order :class:`~app.schemas.search.SearchEntityKind`
#: declares them. Frozen, and hand-maintained for the reason the enum is: a
#: search that gained a table by accident would change what an existing query
#: means.
#:
#: **What is deliberately not here.** Several other tables are per-user and do
#: carry text: ``bookmarks`` (``url``/``title``/``description``), ``documents``
#: (``filename``/``title``/``description``), ``categories`` (``name``) and
#: ``work_sessions`` (no text column of its own). They are left out because the
#: set above is the set the brief specified, and every kind added is another
#: sequential scan paid on every search for a row type the caller did not ask
#: for. ``bookmarks`` is the obvious next candidate — a saved link is exactly
#: the row a user forgets they saved — and adding it means one entry here and one
#: member in :class:`~app.schemas.search.SearchEntityKind`, nothing more. Tables
#: that are not user-scoped search targets at all — ``audit_logs``,
#: ``activity_events``, ``sessions``, the career tables — are excluded for a
#: stronger reason: they are records *about* the account rather than records the
#: account owns, and surfacing them in a search palette would answer a different
#: question than the one the endpoint is for.
SEARCH_TARGETS: Mapping[str, SearchTarget] = MappingProxyType(
    {
        "project": SearchTarget(
            kind="project",
            model=Project,
            owner_column=Project.owner_id,
            columns=(Project.name, Project.description),
            recency_column=Project.updated_at,
            filter_date_column=Project.target_date,
            filter_date_is_timestamp=False,
            project_column=None,
            status_column=Project.status,
            priority_column=Project.priority,
        ),
        "task": SearchTarget(
            kind="task",
            model=Task,
            owner_column=Task.owner_id,
            columns=(Task.title, Task.description),
            recency_column=Task.updated_at,
            filter_date_column=Task.due_date,
            filter_date_is_timestamp=False,
            project_column=Task.project_id,
            status_column=Task.status,
            priority_column=Task.priority,
            tag_table=task_tags,
            tag_column=task_tags.c.task_id,
        ),
        "note": SearchTarget(
            kind="note",
            model=Note,
            owner_column=Note.owner_id,
            columns=(Note.title, Note.content, Note.summary),
            recency_column=Note.updated_at,
            filter_date_column=Note.updated_at,
            filter_date_is_timestamp=True,
            project_column=None,
            status_column=Note.status,
            priority_column=None,
        ),
        "resource": SearchTarget(
            kind="resource",
            model=Resource,
            owner_column=Resource.owner_id,
            columns=(Resource.title, Resource.description, Resource.url),
            recency_column=Resource.updated_at,
            filter_date_column=Resource.updated_at,
            filter_date_is_timestamp=True,
            project_column=None,
            status_column=None,
            priority_column=None,
        ),
        "concept": SearchTarget(
            kind="concept",
            model=Concept,
            owner_column=Concept.owner_id,
            columns=(Concept.name, Concept.description),
            recency_column=Concept.updated_at,
            filter_date_column=Concept.updated_at,
            filter_date_is_timestamp=True,
            project_column=None,
            status_column=None,
            priority_column=None,
        ),
        "repository": SearchTarget(
            kind="repository",
            model=GitRepository,
            owner_column=GitRepository.user_id,
            columns=(GitRepository.name, GitRepository.description, GitRepository.local_path),
            recency_column=GitRepository.updated_at,
            filter_date_column=GitRepository.updated_at,
            filter_date_is_timestamp=True,
            project_column=GitRepository.project_id,
            status_column=None,
            priority_column=None,
        ),
        "goal": SearchTarget(
            kind="goal",
            model=LearningGoal,
            owner_column=LearningGoal.user_id,
            columns=(LearningGoal.title, LearningGoal.description, LearningGoal.target_topic),
            recency_column=LearningGoal.updated_at,
            filter_date_column=LearningGoal.target_date,
            filter_date_is_timestamp=False,
            project_column=LearningGoal.project_id,
            status_column=LearningGoal.status,
            priority_column=LearningGoal.priority,
        ),
        "skill": SearchTarget(
            kind="skill",
            model=Skill,
            owner_column=Skill.user_id,
            columns=(Skill.name, Skill.description, Skill.category),
            recency_column=Skill.updated_at,
            filter_date_column=Skill.updated_at,
            filter_date_is_timestamp=True,
            project_column=None,
            status_column=None,
            priority_column=None,
        ),
        "event": SearchTarget(
            kind="event",
            model=CalendarEvent,
            owner_column=CalendarEvent.owner_id,
            columns=(
                CalendarEvent.title,
                CalendarEvent.description,
                CalendarEvent.location,
            ),
            recency_column=CalendarEvent.updated_at,
            filter_date_column=CalendarEvent.starts_at,
            filter_date_is_timestamp=True,
            project_column=CalendarEvent.project_id,
            status_column=None,
            priority_column=None,
        ),
        "risk": SearchTarget(
            kind="risk",
            model=Risk,
            owner_column=Risk.user_id,
            columns=(Risk.title, Risk.description),
            recency_column=Risk.updated_at,
            filter_date_column=Risk.detected_at,
            filter_date_is_timestamp=True,
            project_column=None,
            status_column=Risk.status,
            # Deliberately None: `risks` carries `severity`, not `priority`, and
            # the two vocabularies are not interchangeable.
            priority_column=None,
        ),
        "recommendation": SearchTarget(
            kind="recommendation",
            model=Recommendation,
            owner_column=Recommendation.user_id,
            columns=(Recommendation.title, Recommendation.description, Recommendation.reason),
            recency_column=Recommendation.updated_at,
            filter_date_column=Recommendation.created_at,
            filter_date_is_timestamp=True,
            project_column=None,
            status_column=Recommendation.status,
            priority_column=Recommendation.priority,
        ),
    }
)


def _date_bound(
    value: date | datetime | None,
    *,
    is_timestamp: bool,
    end_of_day: bool,
) -> date | datetime | None:
    """Widen a caller-supplied day bound to the instant a timestamp column needs.

    A ``Date`` column compares happily against a ``date``, so ``from=2026-01-05``
    needs no help. A ``DateTime`` column needs the edges spelled out, and the
    two directions are not symmetric: ``from`` becomes midnight *inclusive* and
    ``to`` becomes the last microsecond of the day *inclusive*. Writing ``to`` as
    midnight would silently drop every event on that day, which is the kind of
    off-by-one that is only noticed by whoever lost the row.
    """
    if value is None or not is_timestamp:
        return value
    if end_of_day:
        return datetime(
            value.year, value.month, value.day, 23, 59, 59, 999_999, tzinfo=value.tzinfo
        )
    return datetime(value.year, value.month, value.day, tzinfo=value.tzinfo)


class SearchRepository:
    """Cross-entity search, bound to one request-scoped session.

    Stateless beyond the session, like every other repository here: one instance
    per request, and no caching between statements — a cache would hand a
    caller results from before somebody else's write, which is a stale-answer
    bug that looks like a cache working.
    """

    __slots__ = ("session",)

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def target_for(self, kind: str) -> SearchTarget | None:
        """The description for ``kind``, or ``None`` when there is no such kind."""
        return SEARCH_TARGETS.get(kind)

    async def search(
        self,
        target: SearchTarget,
        *,
        owner_id: uuid.UUID,
        term: str,
        limit: int,
        status: str | None = None,
        priority: str | None = None,
        project_id: uuid.UUID | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        tag_ids: Sequence[uuid.UUID] = (),
    ) -> list[SearchRow]:
        """Return up to ``limit`` of this owner's rows matching ``term``.

        One statement, one table, one ownership predicate. The filters are
        optional and every one of them narrows the same statement rather than
        being applied afterwards, so ``limit`` bounds the rows that actually
        matched rather than the rows that happened to be scanned first.

        Args:
            target: The kind's :class:`SearchTarget`, from :data:`SEARCH_TARGETS`.
            owner_id: The caller's account. Required, and the first term of every
                ``where`` clause — a foreign row is never loaded, so there is
                nothing downstream that could leak it.
            term: The already-trimmed search text. Escape the wildcard only; the
                term is never interpolated into the statement.
            limit: The per-kind cap, applied by ``LIMIT`` in SQL.
            status: Matched against this kind's ``status`` column where it has
                one, and ignored where it does not.
            priority: Same, for ``priority``. Ignored for ``risks``.
            project_id: Matched against this kind's project column where it has
                one.
            date_from: Inclusive lower bound on the kind's date column.
            date_to: Inclusive upper bound, widened to the end of the day for
                timestamp columns.
            tag_ids: For ``task`` only — the task must carry **every** listed
                tag, the same all-of semantics :class:`app.repositories.task.TaskRepository`
                uses. A kind with no joinable tag table contributes nothing to a
                tagged search; :mod:`app.services.search_service` skips those kinds
                and refuses the filter outright when no searched kind could have
                honoured it, so the caller is never handed unfiltered rows.
        """
        pattern = _search_pattern(term)
        clauses: list[ColumnElement[bool]] = [
            target.owner_column == owner_id,
            or_(*[column.ilike(pattern, escape="\\") for column in target.columns]),
        ]

        if target.status_column is not None and status is not None:
            clauses.append(target.status_column == status)
        if target.priority_column is not None and priority is not None:
            clauses.append(target.priority_column == priority)
        if target.project_column is not None and project_id is not None:
            clauses.append(target.project_column == project_id)

        if target.filter_date_column is not None and (date_from is not None or date_to is not None):
            lower = _date_bound(
                date_from, is_timestamp=target.filter_date_is_timestamp, end_of_day=False
            )
            upper = _date_bound(
                date_to, is_timestamp=target.filter_date_is_timestamp, end_of_day=True
            )
            if lower is not None:
                clauses.append(target.filter_date_column >= lower)
            if upper is not None:
                clauses.append(target.filter_date_column <= upper)

        if tag_ids and target.tag_table is not None and target.tag_column is not None:
            # One correlated EXISTS per tag rather than a join: "carries every one
            # of these tags" is an all-of test, and a join would both permit any-of
            # and let a task carrying three of the requested tags occupy three rows
            # — spending the per-kind cap three times on one task.
            for tag_id in tag_ids:
                clauses.append(
                    select(literal(1))
                    .select_from(target.tag_table)
                    .where(
                        target.tag_column == target.model.id,
                        target.tag_table.c.tag_id == tag_id,
                    )
                    .exists()
                )

        selected: list[ColumnClause[Any]] = [target.model.id]
        selected.extend(target.columns)
        selected.append(
            target.project_column if target.project_column is not None else _NULL_COLUMN
        )
        selected.append(target.recency_column)

        result = await self.session.execute(
            select(*selected)
            .where(*clauses)
            # Title matches first so the per-kind cap spends its budget on them:
            # a cap that kept the 50 most recently touched body matches would
            # report "nothing here" for a row the user can see the title of.
            .order_by(
                target.columns[0].ilike(pattern, escape="\\").desc(),
                target.recency_column.desc(),
                target.model.id.asc(),
            )
            .limit(limit)
        )

        return [
            SearchRow(
                kind=target.kind,
                id=row[0],
                values=tuple("" if value is None else str(value) for value in row[1:-2]),
                project_id=row[-2],
                recency=row[-1],
            )
            for row in result.all()
        ]

    async def project_names(
        self, owner_id: uuid.UUID, project_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, str]:
        """Resolve project names for hits, scoped to the owner.

        A second statement rather than a join onto every kind: the join would be
        paid once per table, and the answer is the same handful of names in every
        case. The ``owner_id`` predicate is here for the same reason it is
        everywhere else — a hit naming a project id the caller does not own gets
        no name back rather than a lookup that confirms the id exists.
        """
        if not project_ids:
            return {}
        unique = list(dict.fromkeys(project_ids))
        result = await self.session.execute(
            select(Project.id, Project.name).where(
                Project.owner_id == owner_id,
                Project.id.in_(unique),
            )
        )
        return {row[0]: row[1] for row in result.all()}
