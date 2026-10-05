"""Data access for :class:`~app.models.task.Task` and its dependency edges.

The repository owns SQL only. It never raises domain errors — an unexpected
``IntegrityError`` propagates so the service layer can translate it into the API
contract. The exceptions are the two guards against a *programming* error: the
field allowlist in :meth:`TaskRepository.update_fields` and the sort allowlist in
:meth:`TaskRepository.list_for_user`, both of which reject a bad name with
:class:`ValueError` because the name arrives from code, not from a request body.

Four ideas run through the file.

**Ownership is a predicate, not a filter.** ``owner_id`` is in the ``WHERE``
clause of every method that takes one. ``tasks`` carries it as its own column
rather than being reached through the project, so this lookup is one indexed
comparison with no join — and, more to the point, a row the caller may not see is
never loaded at all. Presenting another user's task id returns ``None``,
identically to presenting an id that does not exist.

**Filters AND together.** ``list_for_user`` builds one statement and every
filter narrows it, so the ``total`` it returns counts the same rows the page was
drawn from. ``tag_ids`` is the one that needs saying out loud: a task must
carry *every* listed tag, not any of them, because "in these three projects" and
"in any of these three projects" are different questions and only one of them is
usually meant. It is expressed as an ``IN`` over a subquery rather than a join so
that a task with three matching tags is still one row and the count stays right.

**Counts come from the database.** ``total`` is a ``COUNT`` over the filtered
query, and :meth:`TaskRepository.stats_for_user` gets every bucket from a single
``GROUP BY`` rather than a loop of ``COUNT``s. :meth:`TaskRepository.blocked_task_ids`
is the same shape asked a different question — *which* of the ids a page already
holds are waiting on unfinished work — because the alternative is one dependency
lookup per card, fifty of them on a full page.

**A task cannot wait for itself.** :meth:`TaskRepository.add_dependency` does not
check ``task_id != depends_on_id``, because the ``task_dependencies`` table
carries a ``CHECK`` constraint that does. A Python check is one code path; the
constraint is every path into the table, including the one somebody writes in a
script at midnight. The ``IntegrityError`` it raises is the repository's normal
"never raises domain errors" behaviour — the service layer translates it.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.models.enums import TaskStatus
from app.models.tag import task_tags
from app.models.task import Task, TaskDependency

__all__ = ["TaskRepository"]

#: The columns :meth:`TaskRepository.update_fields` will write.
#:
#: ``id``, ``created_at`` and ``updated_at`` are identity and timeline.
#: ``owner_id`` is the authorisation anchor and ``project_id`` is which workspace
#: the row belongs to: both are structural, and a partial update that can reach
#: either is a partial update that can move work between accounts. Everything
#: here is state a service has already decided to change. Note ``completed_at``
#: *is* writable — it is the timestamp of a transition the service performs, and
#: refusing it would mean the transition had no place to record itself.
#:
#: An absent field raises rather than being dropped, so a caller who expected a
#: column to be written finds out at the call site instead of discovering a field
#: that never saved.
_UPDATABLE_FIELDS = frozenset(
    {
        "actual_minutes",
        "completed_at",
        "description",
        "due_date",
        "estimated_minutes",
        "parent_id",
        "position",
        "priority",
        "project_id",
        "start_date",
        "status",
        "title",
    }
)

#: Public sort names mapped to column attributes.
#:
#: ``ORDER BY`` takes an expression rather than a bound parameter, so a sort name
#: interpolated into it would be SQL injection behind a ``sort`` query parameter.
#: The name is resolved against this table instead and an unknown one is a
#: :class:`ValueError` — failing closed, never falling back to a default column,
#: because a silent fallback would return a plausible ordering for a request that
#: asked for a different one.
_SORT_COLUMNS: dict[str, InstrumentedAttribute] = {
    "completed_at": Task.completed_at,
    "created_at": Task.created_at,
    "due_date": Task.due_date,
    "position": Task.position,
    "start_date": Task.start_date,
    "status": Task.status,
    "updated_at": Task.updated_at,
    "title": Task.title,
    "priority": Task.priority,
}

#: Every value ``tasks.status`` is supposed to hold, as plain strings.
#:
#: A statement needs the *persisted* form, and ``tasks.status`` is a ``String(16)``
#: rather than a native enum — the board's vocabulary grew after the table was
#: written, and the column was left alone. That leaves a drifted value
#: reachable, which is why this list exists at all: it is the predicate that finds
#: the rows nothing can interpret, rather than the one that quietly counts them as
#: ordinary work. :attr:`app.models.task.Task.status_enum` is the Python half of
#: the same question.
_KNOWN_TASK_STATUSES: tuple[str, ...] = tuple(status.value for status in TaskStatus)


def _search_pattern(term: str) -> str:
    """Wrap a user-supplied search term in a case-insensitive ``ILIKE`` pattern.

    ``ILIKE`` and not ``pg_trgm``: this build of PostgreSQL has no contrib
    modules, so ``CREATE EXTENSION pg_trgm`` fails with "extension is not
    available" and the trigram indexes that would make substring search fast
    cannot be created at all. Depending on an extension the target cannot install
    would make the migration unappliable; the portable operator is the right
    choice here, and the cost — a leading ``%`` cannot use a B-tree index — is
    modest for a per-user listing.

    The wildcards inside the term are escaped too. Without this a search for
    ``50%`` matches every row and a search for ``_`` matches any single character:
    a caller's string would silently change what the query means.
    """
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _order_by_clauses(sort: str, order: str) -> tuple[InstrumentedAttribute, ...]:
    """Resolve a public ``sort``/``order`` pair into ORDER BY expressions.

    Args:
        sort: A key of :data:`_SORT_COLUMNS`.
        order: ``"asc"`` or ``"desc"``.

    Returns:
        The chosen column, and the primary key as a tiebreak. The tiebreak is not
        decoration: ``created_at`` is a one-second ``server_default``, so two
        tasks created in the same tick can come back in either order, and a
        listing whose order varies between calls cannot be paginated or diffed.

    Raises:
        ValueError: If either name is not on its allowlist.
    """
    column = _SORT_COLUMNS.get(sort)
    if column is None:
        raise ValueError(
            f"Cannot sort tasks by {sort!r}; "
            f"list_for_user accepts only {', '.join(sorted(_SORT_COLUMNS))}."
        )
    if order == "asc":
        return column.asc(), Task.id.asc()
    if order == "desc":
        return column.desc(), Task.id.desc()
    raise ValueError(f"Cannot sort tasks {order!r}; expected 'asc' or 'desc'.")


def _subtask_filters(project_id: uuid.UUID, parent_id: uuid.UUID | None) -> list:
    """Build the board-column predicate shared by position and listing queries.

    ``parent_id IS NOT DISTINCT FROM ?`` is the one comparison that means the
    same thing for a NULL argument and a set one. The readable alternative —
    branching to ``parent_id.is_(None)`` or ``parent_id == parent_id`` — has a
    fourth variant nobody remembers to write: a caller filtering for "top-level
    tasks" and getting subtasks instead.
    """
    return [Task.project_id == project_id, Task.parent_id.is_not_distinct_from(parent_id)]


class TaskRepository:
    """Task persistence bound to a single request-scoped session."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        project_id: uuid.UUID,
        owner_id: uuid.UUID,
        title: str,
        description: str | None = None,
        priority: str = "medium",
        status: str = "todo",
        parent_id: uuid.UUID | None = None,
        start_date: date | None = None,
        due_date: date | None = None,
        estimated_minutes: int | None = None,
        position: int = 0,
    ) -> Task:
        """Insert a new task and return it with server defaults populated.

        ``owner_id`` is written here even though the project already has one. The
        denormalisation is what lets every read on this table be a single indexed
        predicate on the task row rather than a join through ``projects``, and it
        is what makes the ownership check in
        :meth:`get_by_id_for_user` a comparison rather than a subquery. The cost
        is that a caller can in principle insert a task whose ``owner_id`` differs
        from its project's; that is a service-layer invariant, and the FK to
        ``projects`` is what keeps the row reachable at all.
        """
        task = Task(
            project_id=project_id,
            owner_id=owner_id,
            title=title.strip(),
            description=description,
            priority=priority,
            status=status,
            parent_id=parent_id,
            start_date=start_date,
            due_date=due_date,
            estimated_minutes=estimated_minutes,
            position=position,
        )
        self.session.add(task)
        await self.session.commit()
        # created_at/updated_at/actual_minutes come from server defaults, so
        # re-read them rather than handing back an instance whose timestamps are
        # still unset.
        await self.session.refresh(task)
        return task

    async def get_by_id_for_user(self, task_id: uuid.UUID, owner_id: uuid.UUID) -> Task | None:
        """Return the task only when it also belongs to this user.

        Ownership is part of the lookup rather than a check afterwards: knowing an
        id is not authorisation. Another user's id returns ``None``, identically
        to an id that does not exist, so this cannot be used to probe which ids
        are real.
        """
        result = await self.session.execute(
            select(Task).where(Task.id == task_id, Task.owner_id == owner_id)
        )
        return result.scalar_one_or_none()

    async def list_for_user(
        self,
        owner_id: uuid.UUID,
        *,
        limit: int,
        offset: int,
        project_id: uuid.UUID | None = None,
        parent_id: uuid.UUID | None = None,
        status: str | None = None,
        priority: str | None = None,
        due_before: date | None = None,
        due_after: date | None = None,
        search: str | None = None,
        tag_ids: list[uuid.UUID] | None = None,
        sort: str = "created_at",
        order: str = "desc",
    ) -> tuple[list[Task], int]:
        """List one owner's tasks, newest by default, with the unpaginated total.

        ``parent_id`` cannot express "top-level tasks only": ``None`` means *do
        not filter on parent*, because a filter that treats ``None`` as a value
        cannot also be an absent optional argument. A caller that needs root-level
        tasks filters them out itself, or asks for them by project.

        ``tag_ids`` requires a task to carry every listed tag. It is an ``IN`` over
        a grouped subquery rather than a join to ``task_tags``, so a task matching
        three of the requested tags is still one candidate — one that the
        ``HAVING`` then rejects — and the ``total`` is the number of distinct
        tasks rather than the number of matching tag edges.

        Returns:
            The page of rows and the total number of rows the filters match —
            counted in SQL, not as ``len(rows)``.
        """
        filters = [Task.owner_id == owner_id]
        if project_id is not None:
            filters.append(Task.project_id == project_id)
        if parent_id is not None:
            filters.append(Task.parent_id == parent_id)
        if status is not None:
            filters.append(Task.status == status)
        if priority is not None:
            filters.append(Task.priority == priority)
        if due_before is not None:
            filters.append(Task.due_date <= due_before)
        if due_after is not None:
            filters.append(Task.due_date >= due_after)
        if search is not None and search.strip():
            pattern = _search_pattern(search.strip())
            filters.append(
                Task.title.ilike(pattern, escape="\\")
                | Task.description.ilike(pattern, escape="\\")
            )
        if tag_ids:
            # All-of, enforced by counting the distinct tags each candidate task
            # carries: a bare ``IN`` over the edge table would be any-of and would
            # hand back a larger page than the docs promise.
            wanted = list(dict.fromkeys(tag_ids))
            matching = (
                select(task_tags.c.task_id)
                .where(task_tags.c.tag_id.in_(wanted))
                .group_by(task_tags.c.task_id)
                .having(func.count(func.distinct(task_tags.c.tag_id)) == len(wanted))
            )
            filters.append(Task.id.in_(matching))

        page = (
            select(Task)
            .where(*filters)
            .order_by(*_order_by_clauses(sort, order))
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(page)
        rows = list(result.scalars().all())

        total = int(
            await self.session.scalar(select(func.count()).select_from(Task).where(*filters))
        )
        return rows, total

    async def list_for_project(
        self,
        project_id: uuid.UUID,
        owner_id: uuid.UUID,
        *,
        limit: int,
        offset: int,
        status: str | None = None,
    ) -> tuple[list[Task], int]:
        """List one project's board column, scoped to its owner.

        Both predicates are present even though ``project_id`` would be enough to
        find the row: a project id is exactly as guessable as a task id, and the
        service above has already decided what it is allowed to show. Defence in
        depth costs one indexed comparison.
        """
        filters = [Task.project_id == project_id, Task.owner_id == owner_id]
        if status is not None:
            filters.append(Task.status == status)

        page = (
            select(Task)
            .where(*filters)
            .order_by(Task.position.asc(), Task.created_at.asc(), Task.id.asc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(page)
        rows = list(result.scalars().all())

        total = int(
            await self.session.scalar(select(func.count()).select_from(Task).where(*filters))
        )
        return rows, total

    async def count_for_user(self, owner_id: uuid.UUID) -> int:
        """Count the owner's tasks."""
        result = await self.session.execute(
            select(func.count()).select_from(Task).where(Task.owner_id == owner_id)
        )
        return int(result.scalar_one())

    async def stats_for_user(self, owner_id: uuid.UUID) -> dict[str, int]:
        """Return every task's status bucket plus the total, in one query.

        One ``GROUP BY status`` rather than a loop of ``COUNT``s, so a dashboard
        render is a single round trip whose buckets cannot disagree with each
        other mid-write. Every value of :class:`~app.models.enums.TaskStatus` is
        present even when empty, so a caller can read ``stats["completed"]``
        without a ``.get()`` default and the response shape does not change when
        the last completed task is deleted. ``total`` is the sum of the buckets —
        the same rows, partitioned — rather than a second query that could
        disagree with the first.
        """
        result = await self.session.execute(
            select(Task.status, func.count()).where(Task.owner_id == owner_id).group_by(Task.status)
        )
        stats: dict[str, int] = {status.value: 0 for status in TaskStatus}
        for status_value, bucket in result.all():
            stats[str(status_value)] = int(bucket)
        stats["total"] = sum(value for key, value in stats.items() if key != "total")
        return stats

    async def update_fields(self, task: Task, **fields: object) -> Task:
        """Apply a partial update and persist it.

        Args:
            task: The row to update.
            **fields: The columns to write. Only the names in
                :data:`_UPDATABLE_FIELDS` are accepted. Absent keys are left
                untouched, so ``None`` is a value to write (clear the column) and
                not a way to skip one.

        Returns:
            The updated, refreshed row.

        Raises:
            ValueError: If a field is not on the allowlist — a programming error
                rather than user input, so it fails at the call site instead of
                writing a column nobody meant to write.
        """
        rejected = sorted(set(fields) - _UPDATABLE_FIELDS)
        if rejected:
            raise ValueError(
                f"Cannot write {', '.join(rejected)} on a task row; "
                f"update_fields accepts only {', '.join(sorted(_UPDATABLE_FIELDS))}."
            )
        for key, value in fields.items():
            setattr(task, key, value)
        self.session.add(task)
        await self.session.commit()
        await self.session.refresh(task)
        return task

    async def delete(self, task: Task) -> None:
        """Hard-delete the task row.

        What the database does around it is worth stating: ``task_dependencies``,
        ``task_tags`` and any subtasks cascade, because none of those mean
        anything once the task is gone, while ``activity_events`` is set to
        ``NULL`` by its own ``ON DELETE SET NULL``, so deleting a task does not
        erase the record of what happened to it.
        """
        await self.session.delete(task)
        await self.session.commit()

    async def next_position(self, project_id: uuid.UUID, parent_id: uuid.UUID | None) -> int:
        """Return the next free board position in a column.

        ``MAX(position) + 1``, with ``COALESCE`` so an empty column starts at 0
        rather than NULL. This is a read followed by a write and so it is racy
        under concurrency — two cards dropped at once can land on the same
        position. That is accepted deliberately: the position is presentation
        order within a column, and the tie is broken by ``created_at`` in
        :meth:`list_for_project`, so the worst outcome is two cards whose order
        settles at the next read. Taking a row lock to prevent it would serialise
        every drag-and-drop in the product to protect a cosmetic ordering.
        """
        result = await self.session.execute(
            select(func.coalesce(func.max(Task.position) + 1, 0)).where(
                *_subtask_filters(project_id, parent_id)
            )
        )
        return int(result.scalar_one())

    async def list_subtasks(self, parent_id: uuid.UUID) -> list[Task]:
        """Return a task's direct children in board order.

        One level only, by design. Descending the whole subtree is what turns a
        five-line method into a recursive round trip per depth, and the product
        shows subtasks as a flat list under their parent.
        """
        result = await self.session.execute(
            select(Task)
            .where(Task.parent_id == parent_id)
            .order_by(Task.position.asc(), Task.created_at.asc(), Task.id.asc())
        )
        return list(result.scalars().all())

    async def count_subtasks(self, parent_id: uuid.UUID) -> int:
        """Count a task's direct children, for the "3 subtasks" affordance.

        Counted in SQL so the affordance does not have to load the children to
        decide whether to show them.
        """
        result = await self.session.execute(
            select(func.count()).select_from(Task).where(Task.parent_id == parent_id)
        )
        return int(result.scalar_one())

    async def count_dependency_edges(self, task_id: uuid.UUID) -> int:
        """Count the dependency edges touching this task **in either direction**.

        Both directions, because both break the same invariant: an edge whose two
        ends sit in different projects is refused by
        :meth:`~app.services.task_service.TaskService.add_dependency`, and the two
        ways a task can be the one that moves out from under an edge are being the
        blocked card (``task_id``) and being the blocker (``depends_on_id``). One
        ``OR`` of two counts rather than a join, because the caller only asks
        "are there any?" and the answer is the same either way.

        Args:
            task_id: The card whose edges are being counted.

        Returns:
            How many edges name this task, as either end.
        """
        result = await self.session.execute(
            select(func.count())
            .select_from(TaskDependency)
            .where(
                or_(
                    TaskDependency.task_id == task_id,
                    TaskDependency.depends_on_id == task_id,
                )
            )
        )
        return int(result.scalar_one())

    async def add_dependency(
        self, *, task_id: uuid.UUID, depends_on_id: uuid.UUID
    ) -> TaskDependency:
        """Record that ``task_id`` cannot finish before ``depends_on_id`` does.

        Deliberately unchecked in Python: ``CHECK (task_id <> depends_on_id)`` on
        ``task_dependencies`` makes a self-dependency impossible at the database
        level, for every writer rather than for this one. A task waiting on
        itself can never be completed, and a validation that lives only in a
        service method is one refactoring away from not existing.

        The ``(task_id, depends_on_id)`` unique constraint is the other half: a
        duplicate edge is a second ``IntegrityError`` rather than a duplicate row
        that renders twice in the UI.

        Raises:
            IntegrityError: From the ``CHECK`` or the unique constraint. Left to
                propagate — the service layer translates it, and swallowing it
                here would mean a service could not tell a self-dependency from a
                duplicate edge from a missing task.
        """
        dependency = TaskDependency(task_id=task_id, depends_on_id=depends_on_id)
        self.session.add(dependency)
        await self.session.commit()
        await self.session.refresh(dependency)
        return dependency

    async def list_dependencies(self, task_id: uuid.UUID) -> list[Task]:
        """Return the tasks this one is waiting on.

        Blocked-on is the direction that matters to the user, and it is the one
        that is easy to invert: ``depends_on_id`` on the row is the *other* task,
        so the join here is on ``depends_on_id`` while the row is found by
        ``task_id``. Swapping them returns "things waiting on me" from a method
        whose name promises the opposite, and nothing would fail.
        """
        result = await self.session.execute(
            select(Task)
            .join(TaskDependency, TaskDependency.depends_on_id == Task.id)
            .where(TaskDependency.task_id == task_id)
            .order_by(Task.position.asc(), Task.created_at.asc(), Task.id.asc())
        )
        return list(result.scalars().all())

    async def list_dependents(self, task_id: uuid.UUID) -> list[Task]:
        """Return the tasks waiting on this one — the mirror of the above.

        Used by the completion path to refuse finishing a task whose
        dependencies are still open, which is the question this direction
        answers.
        """
        result = await self.session.execute(
            select(Task)
            .join(TaskDependency, TaskDependency.task_id == Task.id)
            .where(TaskDependency.depends_on_id == task_id)
            .order_by(Task.position.asc(), Task.created_at.asc(), Task.id.asc())
        )
        return list(result.scalars().all())

    async def blocked_task_ids(
        self, task_ids: Sequence[uuid.UUID], owner_id: uuid.UUID
    ) -> set[uuid.UUID]:
        """Ids from ``task_ids`` that have at least one unfinished prerequisite.

        One owner-scoped statement: ``task_dependencies`` joined to the
        depended-upon ``tasks`` row, filtered to ``tasks.owner_id == owner_id``
        and a status that is not ``completed``.

        The join direction is the one :meth:`list_dependencies` gets right and
        this one has to get right too: the edge's ``depends_on_id`` is the
        *blocker*, so the ``tasks`` row carrying ``owner_id`` and the status is
        the prerequisite, not the card that is waiting. Ownership is a predicate
        on the prerequisite, which is the same predicate the row-by-row path
        applied after loading the edges — an edge into somebody else's card never
        blocks anything of the caller's.

        ``COMPLETED`` is the only status that clears a card, matching
        :data:`~app.services.task_service._SATISFIES_DEPENDENCY`: a ``cancelled``
        prerequisite is still waiting, and the user unblocks it by removing the
        edge. ``DISTINCT`` because a card waiting on three unfinished tasks is
        blocked once, and the caller only ever asks ``task_id in blocked``.

        The *waiting* card's own ownership is deliberately not a predicate here.
        The per-row path this replaces did not filter on it either, and
        :meth:`~app.services.task_service.TaskService.list` only ever passes ids
        it has just loaded under ``owner_id`` — the scope belongs to the caller
        that assembles the page, not to this query, which is asked a narrower
        question: of these cards, which are waiting on my unfinished work.

        A prerequisite whose status is outside
        :class:`~app.models.enums.TaskStatus` counts as unfinished here, because
        it is not ``completed`` and nothing else is. That is not the same as
        treating it as ordinary work: :meth:`list_drifted_prerequisites` turns
        such a row into the ``ValidationError`` the service raises for it, and
        this answer is never used once that has happened.

        An empty sequence returns ``set()`` without querying, for the reason
        :meth:`~app.repositories.tag.TagRepository.list_tags_for_tasks` does the
        same: an ``IN ()`` is a pointless round trip, and a page that matched
        nothing is the ordinary last page of any filtered listing.

        Args:
            task_ids: The cards the caller is rendering, in any order.
            owner_id: The caller's account, applied to the prerequisite.

        Returns:
            The subset of ``task_ids`` with at least one unfinished
            prerequisite, as a set for the membership test the caller makes.
        """
        if not task_ids:
            return set()
        result = await self.session.execute(
            select(TaskDependency.task_id)
            .join(Task, Task.id == TaskDependency.depends_on_id)
            .where(
                TaskDependency.task_id.in_(list(task_ids)),
                Task.owner_id == owner_id,
                Task.status != TaskStatus.COMPLETED.value,
            )
            .distinct()
        )
        return set(result.scalars().all())

    async def list_drifted_prerequisites(
        self, task_ids: Sequence[uuid.UUID], owner_id: uuid.UUID
    ) -> list[Task]:
        """Return the prerequisites of ``task_ids`` whose status is not a known one.

        The same join as :meth:`blocked_task_ids`, with the membership test
        inverted: instead of "unfinished" it asks which rows of
        ``tasks.status`` no member of :class:`~app.models.enums.TaskStatus` names.
        ``tasks.status`` carries no ``CHECK`` constraint and is not a native enum,
        so a drifted value is reachable by any writer that skips
        :func:`~app.models.enums.validate_task_status` — an import, a script, a
        later version of the enum written by hand. This query is what finds it.

        It is a second statement rather than a branch inside the first because a
        set of ids cannot carry a row's status out of SQL, and because the caller
        needs the row itself to name the value in its error. The service raises
        on what comes back; nothing here interprets it, and the answer is not a
        flag the caller may fold into the blocked one.

        Returns the rows in board order so the row that gets named is a
        deterministic one, and ``[]`` without querying for an empty sequence.
        """
        if not task_ids:
            return []
        result = await self.session.execute(
            select(Task)
            .join(TaskDependency, TaskDependency.depends_on_id == Task.id)
            .where(
                TaskDependency.task_id.in_(list(task_ids)),
                Task.owner_id == owner_id,
                Task.status.notin_(_KNOWN_TASK_STATUSES),
            )
            .order_by(Task.position.asc(), Task.created_at.asc(), Task.id.asc())
        )
        return list(result.scalars().all())

    async def remove_dependency(self, *, task_id: uuid.UUID, depends_on_id: uuid.UUID) -> bool:
        """Drop one dependency edge, reporting whether it was there to drop.

        A set-based ``DELETE`` rather than a load-modify-delete, so removing an
        edge that a concurrent request already removed is a no-op rather than a
        failure. The return value distinguishes "removed" from "was not there",
        which is what lets the endpoint answer 204 and 404 correctly.
        """
        result = await self.session.execute(
            delete(TaskDependency).where(
                TaskDependency.task_id == task_id,
                TaskDependency.depends_on_id == depends_on_id,
            )
        )
        await self.session.commit()
        return bool(result.rowcount)

    async def dependency_exists(self, task_id: uuid.UUID, depends_on_id: uuid.UUID) -> bool:
        """Report whether this exact edge already exists."""
        result = await self.session.execute(
            select(func.count())
            .select_from(TaskDependency)
            .where(
                TaskDependency.task_id == task_id,
                TaskDependency.depends_on_id == depends_on_id,
            )
        )
        return bool(result.scalar_one())
