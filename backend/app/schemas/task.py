"""Task request/response models.

A task is the unit of work: a title inside a project, with an optional window,
an estimate, an optional parent for subtasks, and dependencies declared in a
separate table.

Two decisions shape this module and are worth stating once here rather than in
each class.

**``is_overdue`` is computed by the backend, not left to the client.** The
frontend may also compute it — it has the date in the same payload — but a
number only one of the two sides is willing to defend is a number they will
eventually disagree about, and the list endpoint's answer has to be the same
whether it was rendered by the web client, a script or the API docs. It is
derived here from the row, so there is one definition. The comparison is
**date-based, not timestamp-based**: a task due today is not overdue at 09:00
and not overdue at 23:00 either. "Overdue" means the due date is *behind* today.
And "today" is the **UTC** day, because every stored timestamp is UTC —
see :func:`utc_today` for why the host's local calendar must never decide it,
and :meth:`TaskRead.build` for how the service hands in the database's clock.

**``status`` is not settable through :class:`TaskUpdate`.** Every transition
carries rules a blanket PATCH would bypass — completing stamps ``completed_at``,
reopening clears it, blocking records who is waiting on what. Those have their
own endpoints (:class:`TaskStatusChange` is the payload) so the rules always run.
:class:`TaskUpdate` answers 422 for ``status`` rather than dropping it, for the
reason spelled out on :class:`~app.schemas.user.UserUpdate`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated, Any, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import TaskPriority, TaskStatus

__all__ = [
    "MAX_ESTIMATED_MINUTES",
    "MAX_TASK_DESCRIPTION_LENGTH",
    "MAX_TASK_TITLE_LENGTH",
    "EstimatedMinutes",
    "TaskCreate",
    "TaskPriorityChange",
    "TaskRead",
    "TaskStats",
    "TaskStatusChange",
    "TaskSummary",
    "TaskUpdate",
    "is_past_due",
    "utc_today",
]

#: Must match ``tasks.title`` in ``app.models.task``.
MAX_TASK_TITLE_LENGTH = 300
#: Long enough for a specification, short enough that a payload is not a file
#: store in disguise.
MAX_TASK_DESCRIPTION_LENGTH = 8000
#: Upper bound on an estimate. 100 000 minutes is roughly 69 days, past which
#: the number is a typo rather than a piece of work, and an absurd estimate is
#: worse than a missing one: it is believed. Zero is allowed and means
#: "deliberately not estimated", which is not the same claim as a negative one.
MAX_ESTIMATED_MINUTES = 100_000

EstimatedMinutes = Annotated[
    int,
    Field(ge=0, le=MAX_ESTIMATED_MINUTES, examples=[90]),
]

#: A task is done or cancelled, or it is not. Neither state is behind its due
#: date in any sense worth showing red: the work was either finished or stopped
#: being asked for, and flagging either as overdue is noise that trains people to
#: ignore the flag.
_NOT_OVERDUE_STATUSES = (TaskStatus.COMPLETED, TaskStatus.CANCELLED)


def utc_today() -> date:
    """Return the current day **in UTC**.

    **Never ``date.today()``.** Every stored timestamp in this schema is
    timezone-aware UTC, so "today" has to be the UTC day too. ``date.today()``
    reads the *host's* local calendar, and a host ahead of UTC — the development
    box runs at +05:30 — disagrees with the database clock for five and a half
    hours a day. In that window a task due today is reported overdue and a task
    due tomorrow is not, while ``created_at`` on the very same row says
    yesterday. The two cannot both be right, and the one that is wrong is the
    badge users learn to ignore.

    This is the *fallback*, for a row validated straight from the ORM with
    nobody to ask. Every path this repository owns passes the database's own
    ``now()`` instead — see :meth:`TaskRead.build` and
    :meth:`~app.services.task_service.TaskService.stats`, which is what keeps
    ``is_overdue`` and ``TaskStats.overdue`` describing the same day. What is
    forbidden either way is the host's local calendar.
    """
    return datetime.now(UTC).date()


def is_past_due(*, due_date: date | None, status: str, today: date) -> bool:
    """Report whether one task's due date is strictly behind ``today``.

    **Date-based, not timestamp-based.** A task due today is not overdue at
    09:00 and not overdue at 23:00 either: "overdue" means the due date is
    *behind* today, so a deadline is never flagged before the day has passed.
    Comparing against an instant would flag it from midnight, which is the
    single most common way an "overdue" badge becomes something users learn to
    ignore.

    A task that is done or cancelled is never overdue — see
    :data:`_NOT_OVERDUE_STATUSES` for why.

    Args:
        due_date: The stored due date, or ``None`` for an un-deadlined task.
        status: The stored status string.
        today: The day to measure against, in UTC.

    Returns:
        Whether the task is late and still open.
    """
    return due_date is not None and due_date < today and status not in _NOT_OVERDUE_STATUSES


class _TaskTextNormaliser:
    """Shared before-validator trimming the task's free-text fields.

    Whitespace is stripped rather than rejected because it is invisible in the
    UI that produced it. A blank ``description`` becomes ``None`` so a client
    clearing the field can send ``""`` and mean "unset". ``title`` is stripped
    but not mapped to ``None``: an empty title is a mistake, and leaving it as
    ``""`` lets ``min_length`` reject it as one instead of silently emptying it.
    """

    @field_validator("title", mode="before", check_fields=False)
    @classmethod
    def _strip_title(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("description", mode="before", check_fields=False)
    @classmethod
    def _blank_description_is_unset(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        return stripped or None


class TaskCreate(_TaskTextNormaliser, BaseModel):
    """Creation payload for a task.

    ``status`` is accepted here — and only here — because a task can legitimately
    be filed as already under way or already blocked by somebody importing a
    backlog. After creation, every further change goes through a transition.
    """

    title: str = Field(
        min_length=1,
        max_length=MAX_TASK_TITLE_LENGTH,
        examples=["Rotate the staging database credentials"],
    )
    description: str | None = Field(
        default=None,
        max_length=MAX_TASK_DESCRIPTION_LENGTH,
        examples=["The shared credentials have to stop being shared."],
    )
    project_id: UUID = Field(description="Project the task belongs to.")
    priority: TaskPriority = Field(default=TaskPriority.MEDIUM)
    status: TaskStatus = Field(
        default=TaskStatus.TODO,
        description="Initial state; later changes go through a transition.",
    )
    start_date: date | None = Field(default=None, description="First day of the planned window.")
    due_date: date | None = Field(default=None, description="Date the work is wanted by.")
    estimated_minutes: EstimatedMinutes | None = Field(
        default=None,
        description="Effort estimate in whole minutes; null when not estimated.",
    )
    parent_id: UUID | None = Field(
        default=None,
        description="Parent task, for a subtask. Must not be the task itself.",
    )

    @model_validator(mode="after")
    def _check_window(self) -> Self:
        """Reject a window that ends before it starts.

        Caught here so it becomes a 422 instead of a row that renders as an
        impossible schedule. A PATCH that moves only one of the two dates is
        checked against the persisted row by the service, for the same reason
        :class:`~app.schemas.project.ProjectUpdate` checks only what it is sent.
        """
        if (
            self.start_date is not None
            and self.due_date is not None
            and self.due_date < self.start_date
        ):
            raise ValueError("due_date must not be earlier than start_date.")
        return self


class TaskUpdate(_TaskTextNormaliser, BaseModel):
    """Partial update of a task's details.

    ``status`` is deliberately absent — see this module's docstring. ``position``
    is absent too: board ordering is rewritten by a dedicated reorder endpoint
    which validates the whole set at once, and accepting one index here would
    let two concurrent drags each believe they moved a card.

    ``extra="forbid"`` is what turns "that field is not updatable here" into an
    answer the client can act on. Pydantic's default is ``"ignore"``, under
    which a client sending ``{"status": "completed"}`` gets a cheerful 200 with
    the field dropped, and a caller who reads that as "the task is done" has
    believed something false about their own work. Rejecting names the
    offending field in a 422 instead.

    ``project_id`` *is* here: a task has to live somewhere, and moving one is an
    ordinary edit. The service must verify the caller owns the destination
    project before applying it — ownership is a property of the row being
    written, not of the payload that proposed it.
    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=MAX_TASK_TITLE_LENGTH)
    description: str | None = Field(default=None, max_length=MAX_TASK_DESCRIPTION_LENGTH)
    project_id: UUID | None = None
    priority: TaskPriority | None = None
    start_date: date | None = None
    due_date: date | None = None
    estimated_minutes: EstimatedMinutes | None = None
    parent_id: UUID | None = None

    @model_validator(mode="after")
    def _check_window(self) -> Self:
        """Reject a window that ends before it starts, when both dates are sent."""
        if (
            "due_date" in self.model_fields_set
            and "start_date" in self.model_fields_set
            and self.start_date is not None
            and self.due_date is not None
            and self.due_date < self.start_date
        ):
            raise ValueError("due_date must not be earlier than start_date.")
        return self


class TaskStatusChange(BaseModel):
    """Payload for a lifecycle transition (``complete``, ``reopen``, ``block``).

    ``note`` is free text attached to the event the transition writes, so the
    reason a task was blocked or reopened survives in the activity feed rather
    than living only in someone's head.
    """

    status: TaskStatus = Field(description="State being moved to; the endpoint decides the rules.")
    note: str | None = Field(
        default=None,
        max_length=MAX_TASK_DESCRIPTION_LENGTH,
        description="Optional context recorded on the resulting activity event.",
    )


class TaskPriorityChange(BaseModel):
    """Payload for re-prioritising a task.

    Separate from :class:`TaskUpdate` because a priority change is an event the
    feed reports on its own — with the old and the new grade. The detail PATCH
    carries a ``priority`` too, and a priority sent there is reported as the same
    ``task_priority_changed`` event; this payload is the route that asks for one
    thing, not a gate that keeps the other one from working.
    """

    priority: TaskPriority


class TaskRead(BaseModel):
    """A task as returned by the task endpoints.

    ``is_overdue`` is derived from ``due_date`` and ``status`` rather than read
    from a column, and ``has_blocked_dependencies`` is supplied by the service
    from the dependency table. Neither is a property of the row alone — the
    second needs a join, the first needs to know what "today" is to the server —
    so both are set by whoever assembles the response, and both mean the same
    thing to every client that asks this endpoint.

    **The defaults are a fallback, not an answer.** ``tag_ids=[]`` and
    ``has_blocked_dependencies=False`` are what a row reads as when nobody ran
    the join for it. That is the right value for a task that genuinely has no
    tags and is genuinely unblocked, and it is a lie for a task that has three
    tags or is waiting on unfinished work — and it used to be printed as one.
    A single-entity read that skipped the join answered ``false`` while the list
    containing the same row answered ``true``, which is the quiet disagreement
    this whole surface is built to avoid. Every route that returns a
    ``TaskRead`` therefore goes through :meth:`build` with the join actually
    run; the default survives only for a caller that has run nothing and knows
    it.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    owner_id: UUID
    parent_id: UUID | None
    title: str
    description: str | None
    status: str = Field(description="One of the ``TaskStatus`` values.")
    priority: str = Field(description="One of the ``TaskPriority`` values.")
    start_date: date | None
    due_date: date | None
    estimated_minutes: int | None
    actual_minutes: int
    completed_at: datetime | None
    position: int = Field(description="Board ordering key; lower sorts first.")
    created_at: datetime
    updated_at: datetime
    tag_ids: list[UUID] = Field(
        default_factory=list,
        description="Tags on this task. Empty when the response was not joined to them.",
    )
    is_overdue: bool = Field(
        default=False,
        description="Past due and still open. Backend-computed; clients may mirror it.",
    )
    has_blocked_dependencies: bool = Field(
        default=False,
        description="True when at least one prerequisite task is unfinished.",
    )

    @model_validator(mode="after")
    def _derive_is_overdue(self) -> Self:
        # An explicit value wins, and the guard is what makes that true twice.
        # FastAPI validates the response model *again* on the way out, so a
        # builder that only patched the instance after the fact would have its
        # answer overwritten by the validator on the way through the response.
        # ``model_fields_set`` records that the value was supplied rather than
        # derived, and survives both ``model_copy`` and re-validation, so the
        # clock the caller handed to :meth:`build` is the clock that answers.
        #
        # What remains here is the fallback, for a row nobody built: the UTC
        # day, never the host's local one — see :func:`utc_today`.
        if "is_overdue" in self.model_fields_set:
            return self
        self.is_overdue = is_past_due(due_date=self.due_date, status=self.status, today=utc_today())
        return self

    @classmethod
    def build(
        cls,
        row: Any,
        *,
        tag_ids: list[UUID] | None = None,
        has_blocked_dependencies: bool = False,
        today: date | None = None,
    ) -> TaskRead:
        """Build a task from a row plus the data that needs extra queries.

        Args:
            row: The persisted task, read through ``from_attributes``.
            tag_ids: Ids of the task's tags, from the ``task_tags`` join.
            has_blocked_dependencies: Whether any task this one depends on is
                unfinished. Computed by the service from ``task_dependencies``;
                defaulting to ``False`` is the honest answer for a row read
                without that join.
            today: The UTC day to measure ``is_overdue`` against. Supply it
                whenever a clock is available — the service reads the
                database's, which is the same clock
                :meth:`~app.services.task_service.TaskService.stats` cuts its
                overdue window on, so the badge on a card and the count in the
                totals cannot describe two different days. Supplying it makes
                the value *explicit*, which is what stops the response model's
                re-validation on the way out from re-deriving it. Left
                ``None``, the row's own validator falls back to
                :func:`utc_today`.

        Returns:
            The task, with ``is_overdue`` derived and the two supplied values set.
        """
        update: dict[str, Any] = {
            "tag_ids": tag_ids or [],
            "has_blocked_dependencies": has_blocked_dependencies,
        }
        if today is not None:
            update["is_overdue"] = is_past_due(
                due_date=getattr(row, "due_date", None),
                status=getattr(row, "status", ""),
                today=today,
            )
        return cls.model_validate(row).model_copy(update=update)


class TaskSummary(BaseModel):
    """The lean shape a task list or board column renders.

    A card shows a title, where it sits, how urgent it is and whether it is
    late — not the full description, the timestamps or the actual minutes.
    Returning the full :class:`TaskRead` for a column of twenty cards would make
    the payload roughly four times the size for fields nobody renders.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    parent_id: UUID | None
    title: str
    status: str
    priority: str
    start_date: date | None
    due_date: date | None
    position: int
    completed_at: datetime | None
    tag_ids: list[UUID] = Field(default_factory=list)
    is_overdue: bool = False

    @model_validator(mode="after")
    def _derive_is_overdue(self) -> Self:
        """Same rule as :class:`TaskRead`, kept here so the two cannot disagree.

        The same guard, so an ``is_overdue`` supplied by a caller survives the
        response model being validated again on the way out. Where there is no
        caller — the subtask, dependency and dependency-response routes serve
        these straight from the ORM row — the flag is measured against
        :func:`utc_today`, the UTC day. That is the same day
        :class:`TaskRead` is measured against on every route that goes through
        :meth:`TaskRead.build`, so a card and its summary cannot disagree; it is
        simply the process clock rather than the database's, and the two agree
        because both are UTC.
        """
        if "is_overdue" in self.model_fields_set:
            return self
        self.is_overdue = is_past_due(due_date=self.due_date, status=self.status, today=utc_today())
        return self


class TaskStats(BaseModel):
    """Task counts for a project or for the owner's board.

    ``overdue`` is counted across every bucket rather than inside one: it is a
    different question from the status breakdown — "how much of this is late?" —
    and folding it into ``todo`` or ``in_progress`` would make the buckets stop
    being a partition.
    """

    total: int = Field(ge=0)
    todo: int = Field(ge=0)
    in_progress: int = Field(ge=0)
    blocked: int = Field(ge=0)
    completed: int = Field(ge=0)
    cancelled: int = Field(ge=0)
    overdue: int = Field(ge=0, description="Open tasks whose due date is behind today.")
