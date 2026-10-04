"""Tasks and the explicit dependencies between them.

A task is either a board card of its own or, when :attr:`Task.parent_id` is set,
a subtask of another task. Dependencies are a *separate* table rather than a
column because "blocks" is many-to-many and directed: A blocks B does not mean
B blocks A, and neither implies a parent/child relationship.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import TaskPriority, TaskStatus

__all__ = [
    "DEFAULT_TASK_PRIORITY",
    "DEFAULT_TASK_STATUS",
    "Task",
    "TaskDependency",
]

#: What a new task gets when the caller does not choose.
DEFAULT_TASK_STATUS = TaskStatus.TODO.value
DEFAULT_TASK_PRIORITY = TaskPriority.MEDIUM.value

#: Sized for a one-line task title. A task whose title needs more than this is a
#: description, which is what :attr:`Task.description` is for.
_MAX_TITLE_LENGTH = 300
#: Sized for the longest member of :class:`TaskStatus` (``in_progress``) and
#: :class:`TaskPriority` (``critical``), with room to spare.
_MAX_ENUM_LENGTH = 16


class Task(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One unit of work.

    Deleting a task is a real delete, not a status change: :attr:`status` carries
    a ``CANCELLED`` member for work that was abandoned, so a row only disappears
    when its user asked for it to. Its activity rows survive that, with
    :attr:`activity_events.ActivityEvent.task_id` blanked rather than cascaded.
    """

    __tablename__ = "tasks"

    __table_args__ = (
        # A task that is its own parent is a one-node cycle, and every tree walk
        # over subtasks — the roll-up of a parent's completion, the "expand to
        # show children" query on the board — then has to defend against it.
        # That defence cannot live in the model: the parent is chosen at write
        # time, and a bug anywhere between two screens can produce the value.
        # The invariant is cheap and total at the database level, so it is
        # enforced here and named so a violation names itself.
        CheckConstraint(
            "parent_id IS NULL OR parent_id <> id",
            name="ck_tasks_parent_not_self",
        ),
        # The dashboard's two hot queries, and the reason they need more than
        # `ix_tasks_owner_id`.
        #
        # 1. "my tasks in these statuses whose due date falls in this window,
        #    soonest first", and
        # 2. the overdue probe — `owner_id = ? AND status <> 'completed' AND
        #    due_date < today`.
        #
        # Both are an equality on `owner_id`, an equality (or a small IN list) on
        # `status` and a *range* on `due_date`. The single-column owner index can
        # only serve the first term, so PostgreSQL heap-fetches the user's entire
        # backlog — which never shrinks, because a completed task keeps its row —
        # and filters the other two columns afterwards. With this index the
        # range on `due_date` is resolved inside the index: one index scan per
        # status value, rows already in `due_date` order, no final sort, and only
        # qualifying rows reach the heap.
        #
        # This is not the `sessions` trade-off (see `app/models/session.py`),
        # where the composite would have been pointless: a user has tens of
        # sessions and hundreds to low-thousands of tasks, and only the task
        # backlog grows without bound. The duplicated `owner_id` prefix is kept
        # because the brief's column contract asks for a plain index there, and
        # because a leading-column index is still the right entry point for the
        # unqualified "all my tasks" query this composite does not serve
        # efficiently.
        Index("ix_tasks_owner_status_due", "owner_id", "status", "due_date"),
        # The analytics window reads, which the composite above does not serve at
        # all. Phase 6 buckets two series by an *instant* range and none of them
        # mentions `due_date`:
        #
        # 1. "tasks created per day" — `owner_id = ? AND created_at >= ? AND
        #    created_at < ?`, grouped by `date_trunc('day', created_at)`.
        # 2. "tasks completed per day" — the same shape on `completed_at`, and
        #    the same shape again behind `completed_pairs_in_range` and
        #    `avg_cycle_minutes_in_range`, which between them feed deadline
        #    adherence, estimation accuracy and cycle time.
        #
        # `ix_tasks_owner_status_due` can serve only its leading column here, so
        # every one of those reads was a sequential scan of the whole table with
        # the window applied afterwards — a cost that grows with the account's
        # entire history rather than with the window, and that is paid on *every*
        # analytics request because the daily rebuild runs them to decide whether
        # the stored aggregates are current.
        #
        # Measured on 20,000 rows for one account over a 31-day window: 1.23 ms to
        # 0.27 ms on the created-at bucket, 0.86 ms to 0.03 ms on the completed
        # pairs, 0.92 ms to 0.08 ms on the cycle time. Plan changed from
        # `Seq Scan on tasks` to a `Bitmap Heap Scan` and two `Index Scan`s.
        #
        # `owner_id` leads because every read is already scoped to one account —
        # that is the tenancy guarantee, not an optimisation — and a range cannot
        # use an index that has it as a later column.
        Index("ix_tasks_owner_created", "owner_id", "created_at"),
        Index("ix_tasks_owner_completed", "owner_id", "completed_at"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    # The subtask's parent. CASCADE rather than SET NULL: a subtask has no
    # meaning without the card it belongs to, exactly as a session has none
    # without its account.
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    title: Mapped[str] = mapped_column(String(_MAX_TITLE_LENGTH), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(_MAX_ENUM_LENGTH),
        server_default=DEFAULT_TASK_STATUS,
        nullable=False,
    )
    priority: Mapped[str] = mapped_column(
        String(_MAX_ENUM_LENGTH),
        server_default=DEFAULT_TASK_PRIORITY,
        nullable=False,
    )
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Minutes, nullable, because "we have not estimated this" and "we estimated
    # it at zero minutes" are different answers.
    estimated_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # NOTE FOR PHASE 4 (time tracking) — read before adding work sessions.
    #
    # This is a single integer minute counter, deliberately left as the spec
    # gives it, and it has three consequences a session table has to respect:
    #
    #   1. **Accumulate in SQL, never in Python.** A running timer that reads the
    #      row, adds the elapsed minutes in the application and writes the sum
    #      back loses time the moment two tabs or two devices write at once —
    #      last writer wins. The increment must be a single statement,
    #      `UPDATE tasks SET actual_minutes = actual_minutes + :elapsed`, so the
    #      row lock does the serialising.
    #   2. **One minute is the resolution.** A timer started at 14:03:37 either
    #      has to round each session or accumulate in seconds. Rounding per
    #      session loses up to 30 seconds per session, which compounds over a day
    #      of short sessions; the honest fix is to sum exact session durations and
    #      round once when writing here, so that choice has to be made on the
    #      session side, not here.
    #   3. **NOT NULL, server default 0** means "never tracked" and "tracked as
    #      zero" are indistinguishable, and there is no CHECK on the sign. That is
    #      a deliberate Phase 3 simplification, not an oversight: this column
    #      exists so Phase 4 can report a number even before sessions do. If
    #      Phase 4 wants "this task has never had a timer run", it needs a
    #      nullable column or a separate flag — a schema change, taken
    #      deliberately rather than discovered as a negative total in a report.
    actual_minutes: Mapped[int] = mapped_column(
        Integer,
        server_default="0",
        nullable=False,
    )
    # Stamped when the task reaches `completed`. Not derivable from `updated_at`,
    # which every subsequent edit rewrites.
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    # Board ordering within a status column. A plain integer so a drag can write
    # the new order for the affected rows in one statement; ties are expected and
    # must be broken deterministically by the reader (`position, created_at` or
    # `position, id` — pick one and use it in every board query), because nothing
    # here enforces uniqueness.
    position: Mapped[int] = mapped_column(
        Integer,
        server_default="0",
        nullable=False,
    )

    # Index note for the rest of the table:
    #
    # * `ix_tasks_project_id` serves the Kanban board, which reads a whole
    #   project's tasks in one query and groups by status on the client. No
    #   composite `(project_id, position)` is added: the board fetches every row
    #   for the project regardless, so an ordering index cannot reduce the row
    #   count — it would only save a `sort` on a result set the application has
    #   already decided to receive in full.
    # * `status`, `priority` and `due_date` deliberately carry **no** standalone
    #   index. Each has a handful of distinct values across the whole
    #   installation, so PostgreSQL will not choose them over a scan, and the
    #   task list's filters on them are always applied together with a selective
    #   term (`project_id`, `owner_id` or a tag join). They appear instead as the
    #   second and third columns of `ix_tasks_owner_status_due`, where they are
    #   reached *after* an equality on the leading column and so actually narrow
    #   something.

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Task id={self.id} title={self.title!r} status={self.status!r}>"

    @property
    def status_enum(self) -> TaskStatus | None:
        """The task's status as a member, or ``None`` if the row drifted.

        ``None`` rather than a fallback, because the fallback would put a task
        with an unreadable status into the ``todo`` column, where it looks like
        ordinary work instead of a data problem. Validate on write with
        :func:`app.models.enums.validate_task_status`.
        """
        try:
            return TaskStatus(self.status)
        except ValueError:
            return None

    @property
    def priority_enum(self) -> TaskPriority | None:
        """The task's priority as a member, or ``None`` if the row drifted."""
        try:
            return TaskPriority(self.priority)
        except ValueError:
            return None


class TaskDependency(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A directed edge: :attr:`task_id` cannot start until :attr:`depends_on_id` has.

    Edges are rows rather than columns because the relation is many-to-many and
    directional. The two timestamps the mixin adds are about the *edge*, not the
    work: ``created_at`` is when the dependency was declared (which is what an
    audit of "when did we decide B was blocked by A" needs) and ``updated_at``
    moves if the edge is ever re-pointed.
    """

    __tablename__ = "task_dependencies"

    __table_args__ = (
        # The same edge declared twice is a duplicate that every traversal walks
        # twice and that "what blocks this?" answers twice.
        UniqueConstraint("task_id", "depends_on_id", name="uq_task_dependencies_task_pair"),
        # A task that blocks itself is not a dependency, it is a deadlock: the
        # task is simultaneously incomplete and permanently unable to start, and
        # every readiness calculation that walks the graph has to special-case it
        # to avoid looping. It is also trivial to write by accident — a bug that
        # copies the "current task" id into the dependency picker produces exactly
        # this row, and the Python check that would have caught it is in a
        # different process from the one doing the write (an import, a bulk
        # script, a future service).
        #
        # So it is made impossible here. This constraint only rules out the
        # direct cycle; a longer cycle (A blocks B blocks C blocks A) is still
        # possible and still has to be detected in the service layer, because
        # ruling it out in the database would need a recursive trigger on a table
        # this size, paid on every insert, to prevent something a depth-first walk
        # catches for free.
        CheckConstraint(
            "task_id <> depends_on_id",
            name="ck_task_dependencies_no_self_dependency",
        ),
    )

    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    depends_on_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    # Index note: `ix_task_dependencies_task_id` serves "what is this task
    # waiting on" — the question asked on every board render — and
    # `ix_task_dependencies_depends_on_id` serves "what does this task block",
    # which is the reverse question the dependency panel asks. Neither column
    # alone serves both directions, which is why the pair is not a single index
    # on the composite primary key's prefix. The unique constraint above already
    # gives an index on `(task_id, depends_on_id)`; it is a constraint rather
    # than a lookup path, and it cannot answer a query keyed on `depends_on_id`.
