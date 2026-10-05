"""Task endpoints: the board, its subtasks, its dependencies and its tags.

Where the rules live
--------------------
This file is a translation layer and nothing else. Every question it could
answer — *may this task move to that status, is that dependency a cycle, is this
parent in the same project, is that tag yours* — is answered by
:class:`~app.services.task_service.TaskService`, and this router picks a status
code and hands the result back. It imports no repository, and it raises no
domain error: :func:`app.core.exceptions.install_exception_handlers` turns the
service's ``NotFoundError`` / ``ConflictError`` / ``ValidationError`` into the
shared envelope, and a router that built its own error body would be a second,
drifting implementation of the same contract.

Tenancy, in particular, is the service's. **No route here accepts a user id
from the request.** The caller comes from the bearer token, is passed to the
service as ``owner=``, and the service resolves every row through a
``get_by_id_for_user``-scoped lookup. That is what makes another account's id
answer **404 and not 403** — identical to an id that never existed — so the
route cannot be used to find out which task ids are real. A ``403`` here would
mean "this id exists and is not yours", which is the one answer this surface
must never give.

Two honest gaps in what the wire can carry today
------------------------------------------------
Both are properties of the service layer, not of this router, and both are
stated here because a response model that cannot tell a client is worse than one
that admits it:

* **``parent_id`` is not a filter on the listing.** ``TaskService.list`` does
  not accept it although ``TaskRepository.list_for_user`` supports the column,
  so the parameter is not declared here rather than declared and silently
  ignored. One service signature is all that is missing.

The gap that used to be second of the two — **single-task responses carrying no
tags and no dependency flag** — is closed. ``GET /tasks/{id}`` returned
``tag_ids: []`` and ``has_blocked_dependencies: false`` for a task that had
three tags and was waiting on unfinished work, while ``GET /tasks`` returned the
real values for the same row in the same request. Every single-task route here
now goes through ``TaskService.read``/``get_read``, which assembles a row the
way ``_page`` assembles a page, and the ``is_overdue`` flag is measured against
the database's own clock rather than the host's local calendar.

Every edge of the lifecycle is reachable here too. ``cancelled`` was listed in
``TaskService._LEGAL_TRANSITIONS`` from the first commit and no route could take
any edge into it, so ``TaskStats.cancelled`` read a hard zero for every account
in the repository — indistinguishable from a user who never abandons work.

Pagination
----------
The listing is a :class:`~app.schemas.common.Page`, never a bare array, and
``limit`` is capped at :data:`MAX_PAGE_SIZE`. The cap is a **rejection, not a
silent truncation**: ``?limit=500`` is a 422 rather than a 100-row page, because
a client that asked for 500 and received 100 cannot tell a truncated page from a
page that was always 100 rows long, and would paginate off the end of a
sequence it believes it has seen. See ``docs/api-conventions.md`` §Pagination.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.deps import AuthenticatedUser, TaskServiceDep
from app.core.deps import require_permission
from app.core.exceptions import ValidationError
from app.core.permissions import Permission
from app.models.enums import TaskPriority, TaskStatus
from app.models.task import Task
from app.schemas.common import Page
from app.schemas.tag import TagAssignment
from app.schemas.task import (
    TaskCreate,
    TaskPriorityChange,
    TaskRead,
    TaskStatusChange,
    TaskSummary,
    TaskUpdate,
)

router = APIRouter(prefix="/tasks", tags=["tasks"])

#: The page size a caller gets when it does not ask for one. Smaller than
#: :data:`~app.services.task_service.TaskService.DEFAULT_PAGE_SIZE` because a
#: task list is the densest screen in the product — a board column is a handful
#: of cards, not a feed — and the client pages through the rest.
DEFAULT_PAGE_SIZE = 20

#: The largest page any caller may ask for. 100 is a ceiling, not a default: it
#: is more than a board renders, so a caller reaching it is a script exporting
#: data rather than a UI, and such a caller is served by walking ``offset``.
MAX_PAGE_SIZE = 100

#: The sort names this endpoint advertises.
#:
#: The list is deliberately *not* enforced here even though it looks like a
#: validation concern. ``ORDER BY`` takes an expression rather than a bound
#: parameter, so an unvalidated sort name would be SQL injection behind a query
#: parameter — but the service already resolves the name against an allowlist
#: and raises ``ValidationError`` before a statement is built, and the
#: repository resolves it a second time before it is interpolated. Duplicating
#: the set here would add a third place to keep in step with no security gained.
#:
#: The authoritative set is ``TaskService._SORT_KEYS``: ``created_at``,
#: ``updated_at``, ``title``, ``status``, ``priority``, ``start_date``,
#: ``due_date``, ``position``, ``completed_at``. Anything else — including
#: ``estimated_minutes`` — is a 422 that never reaches the database.
_SORT_DESCRIPTION = "Sort key; one of the names the service allowlists."

#: The two names the dependency listing answers to for each end of an edge.
#:
#: Declared rather than left to the router's default of discarding an unknown
#: query parameter, because discarding one is what made ``?direction=reverse``
#: answer ``200 []`` for a card with three cards downstream of it.
_FORWARD_DIRECTION_NAMES = frozenset({"forward", "dependencies"})
_REVERSE_DIRECTION_NAMES = frozenset({"reverse", "dependents"})


class _BlockRequest(TaskStatusChange):
    """:class:`~app.schemas.task.TaskStatusChange`, narrowed to the one status this route produces.

    ``TaskStatusChange.status`` is required, and a client that posted
    ``{"status": "completed"}`` to ``/block`` would otherwise have the field
    silently ignored: the request would succeed and the card would come back
    ``blocked``. That is precisely the quiet disagreement between what was asked
    for and what happened that ``TaskUpdate`` refuses to allow through
    ``extra="forbid"``, and a client reading its own request body would be
    entitled to believe otherwise. Pinning the literal turns it into a 422 that
    names the field, and giving it a default makes ``{"note": "waiting on the
    vendor"}`` — the only thing this route actually reads — a complete body.
    """

    status: Literal[TaskStatus.BLOCKED] = TaskStatus.BLOCKED


@router.get(
    "",
    response_model=Page[TaskRead],
    summary="List the caller's tasks",
    dependencies=[Depends(require_permission(Permission.TASKS_READ))],
)
async def list_tasks(
    current_user: AuthenticatedUser,
    tasks: TaskServiceDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
    project_id: UUID | None = None,
    task_status: Annotated[TaskStatus | None, Query(alias="status")] = None,
    priority: Annotated[TaskPriority | None, Query()] = None,
    due_before: date | None = None,
    due_after: date | None = None,
    search: Annotated[
        str | None,
        Query(description="Case-insensitive substring of the title or description."),
    ] = None,
    tag_ids: Annotated[
        list[UUID] | None,
        Query(description="Repeatable. A task must carry every tag listed."),
    ] = None,
    sort: Annotated[str, Query(description=_SORT_DESCRIPTION)] = "created_at",
    order: Annotated[str, Query(description="'asc' or 'desc'.")] = "desc",
) -> Page[TaskRead]:
    """List tasks as one page of a filtered, sorted, owner-scoped sequence.

    **The scope is the caller's and only the caller's.** There is no ``user_id``
    parameter, and there is no way to ask for somebody else's page: a filter
    narrows the caller's own tasks, it cannot widen them. ``project_id`` is not
    re-checked for ownership here, and does not need to be — every row returned
    is already the caller's, so a project that is not theirs can only match
    nothing.

    **Every filter ANDs**, which is what makes ``meta.total`` the number the
    page was drawn from rather than the number of everything. ``tag_ids`` is the
    strictest of them: a task must carry *every* tag listed, not any of them,
    because "in these three projects" and "in any of these three projects" are
    different questions and only one is usually meant.

    **Unknown ``sort``/``order``/``status``/``priority`` is a 422**, raised by the
    service against its allowlist before any SQL is built — a sort name is not
    sanitised, it is resolved against a fixed set of columns.

    ``parent_id`` is **not** a filter on this route: :meth:`TaskService.list`
    does not accept it, and accepting a parameter the service ignores would
    return an unfiltered list under a name that promises a filtered one. The
    repository supports the column, so this is a one-line service change once
    someone owns it.
    """
    return await tasks.list(
        owner=current_user,
        limit=limit,
        offset=offset,
        project_id=project_id,
        status=task_status,
        priority=priority,
        due_before=due_before,
        due_after=due_after,
        search=search,
        tag_ids=tag_ids,
        sort=sort,
        order=order,
    )


@router.post(
    "",
    response_model=TaskRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a task",
    dependencies=[Depends(require_permission(Permission.TASKS_WRITE))],
)
async def create_task(
    payload: TaskCreate,
    current_user: AuthenticatedUser,
    tasks: TaskServiceDep,
) -> Task:
    """File a new card under a project of the caller's.

    The project is the caller's or the task is not filed: ``tasks.owner_id`` is
    denormalised from the project, so nothing in the database stops a row being
    created under somebody else's project id, and that is checked here — as a
    ``NotFoundError``, so a foreign project id is indistinguishable from one that
    was never issued. A ``parent_id`` is checked the same way and against the
    same project, and must be a root card: nesting is one level deep.

    ``status`` is accepted here and nowhere else after this, because an imported
    backlog may legitimately arrive already under way or already blocked.

    ``tag_ids`` and ``has_blocked_dependencies`` come back empty here because
    they are *measured* zeros rather than unmeasured ones: the row was created
    by this very request, so it carries no tag edges and no dependency edges. No
    join is worth running to confirm it.

    Errors: 404 for a project or parent that is not the caller's; 422 for a
    parent in another project or one that is itself a subtask.
    """
    return await tasks.create(owner=current_user, data=payload)


@router.get(
    "/{task_id}",
    response_model=TaskRead,
    summary="Fetch one task",
    dependencies=[Depends(require_permission(Permission.TASKS_READ))],
)
async def get_task(
    task_id: UUID,
    current_user: AuthenticatedUser,
    tasks: TaskServiceDep,
) -> TaskRead:
    """Return one of the caller's tasks.

    **404 for another account's task, never 403** — the id is resolved through
    a lookup scoped by ``owner_id``, so the row is never loaded and the refusal
    is byte-for-byte the one a nonexistent id gets. See this module's docstring
    for why that distinction is load-bearing.

    **``tag_ids`` and ``has_blocked_dependencies`` are real here**, joined for
    this one row the same way the listing joins a page. They used to be the
    schema's ``[]``/``false`` defaults on this route and only the listing's real
    values, so a client could fetch a blocked card and be told it was ready. An
    absent measurement is not a measured zero, and a card waiting on unfinished
    work reported as unblocked is the more expensive of the two mistakes.

    Errors: 404 when the caller owns no task with this id.
    """
    return await tasks.get_read(task_id=task_id, owner=current_user)


@router.patch(
    "/{task_id}",
    response_model=TaskRead,
    summary="Edit a task's details",
    dependencies=[Depends(require_permission(Permission.TASKS_WRITE))],
)
async def update_task(
    task_id: UUID,
    payload: TaskUpdate,
    current_user: AuthenticatedUser,
    tasks: TaskServiceDep,
) -> TaskRead:
    """Apply a partial update to a task the caller owns.

    **A field is written if the client named it**, and an omitted field is left
    alone — which is why clearing a description or a due date is a
    ``"field": null`` and not an absent key.

    ``status`` is not on this payload and ``extra="forbid"`` means sending it is
    a 422 naming the field, not a silent drop: completing stamps
    ``completed_at`` and blocking records a reason, and a blanket PATCH that
    could set the column would be a way around both. ``position`` is absent for
    the same reason — board ordering is rewritten as a set, so two concurrent
    drags cannot each believe they moved a card.

    A move to another project re-checks the destination's ownership, because
    ownership is a property of the row being written and not of the payload
    proposing it. It also refuses a move for a task that carries subtasks or
    dependency edges, which is the same linkage rule the create path enforces:
    a move must not produce a parent/child or a dependency edge that straddles
    two projects.

    **``priority`` is accepted here and reported as its own event.** The
    dedicated ``PATCH /tasks/{id}/priority`` route exists so a re-triage is a
    moment the feed can answer for, not so the column can be locked; a priority
    that arrives in this payload writes the same ``task_priority_changed`` event,
    with the old and the new grade. Every changed field is recorded with the value
    it held before the write, so an edit can be shown and reversed from the feed
    rather than only listed by name.

    Errors: 404 for the task, the destination project or the new parent not
    being the caller's; 422 for an inverted date window, a parent that is in
    another project or is itself a subtask, or a move that would separate a
    subtask, a child task or a dependency edge from the project it belongs to.
    """
    task = await tasks.get(task_id=task_id, owner=current_user)
    updated = await tasks.update(task=task, data=payload, owner=current_user)
    return await tasks.read(task=updated, owner=current_user)


@router.delete(
    "/{task_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete a task",
    dependencies=[Depends(require_permission(Permission.TASKS_WRITE))],
)
async def delete_task(
    task_id: UUID,
    current_user: AuthenticatedUser,
    tasks: TaskServiceDep,
) -> Response:
    """Delete one of the caller's tasks for good.

    A real delete, not a status change: ``cancelled`` already exists for work
    that was abandoned, so a row only disappears when its user asked for it to.
    Subtasks, dependency edges and tag edges cascade; the activity rows do not.

    **Tracked time goes with it, and that is the consequence worth reading.**
    ``work_sessions.task_id`` and ``calendar_events.task_id`` are ``ON DELETE
    CASCADE`` (``app/models/planner.py:149-151`` and ``:272-280``), so this one
    call removes every minute the user actually recorded against the task and
    every calendar entry reserved for it. Silently, and with no way back: the
    Phase 6 analytics that read those rows then report fewer hours worked, which
    is indistinguishable from the user having worked less.

    **So cancel rather than delete, wherever cancelling will do.** A cancelled
    task keeps its sessions, its bookings, its history and its place in the
    stats; it just stops being work anyone is waiting on. Recommend to the owner
    that this route be made opt-in — a confirmation naming the minutes and
    bookings about to be destroyed — rather than that the cascade be removed,
    which would leave sessions pointing at a task nobody can name.

    Errors: 404 for a task that is not the caller's, or that does not exist.
    """
    task = await tasks.get(task_id=task_id, owner=current_user)
    await tasks.delete(task=task, owner=current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{task_id}/complete",
    response_model=TaskRead,
    summary="Complete a task",
    dependencies=[Depends(require_permission(Permission.TASKS_WRITE))],
)
async def complete_task(
    task_id: UUID,
    current_user: AuthenticatedUser,
    tasks: TaskServiceDep,
) -> TaskRead:
    """Finish a task, stamping ``completed_at``.

    Two rules, and the second is the reason this is not an alias for setting the
    status. The task must be in a status that may be completed — work nobody
    started does not get to claim it was finished — and **anything it is waiting
    on must itself be complete**. A dependency that is not ``COMPLETED`` blocks,
    ``CANCELLED`` included: a prerequisite that was dropped is not a
    prerequisite that was met. The refusal names the open prerequisites, so the
    user is told what to finish rather than merely that something is in the way.

    Errors: 422 for an illegal transition or an open dependency; 404 for a task
    that is not the caller's.
    """
    task = await tasks.get(task_id=task_id, owner=current_user)
    return await tasks.read(
        task=await tasks.complete(task=task, owner=current_user), owner=current_user
    )


@router.post(
    "/{task_id}/reopen",
    response_model=TaskRead,
    summary="Reopen a completed task",
    dependencies=[Depends(require_permission(Permission.TASKS_WRITE))],
)
async def reopen_task(
    task_id: UUID,
    current_user: AuthenticatedUser,
    tasks: TaskServiceDep,
) -> TaskRead:
    """Take a completed task back, clearing ``completed_at``.

    Reopening returns the card to ``todo`` and **clears the stamp**, so a task
    that is open again cannot go on reporting the moment it was finished.
    Reopening something that is already open is a no-op rather than an error,
    for the same reason every status no-op is one: a retried click is a retry,
    not a mistake.

    ``cancelled`` is the one status this refuses. It is terminal — work that was
    deliberately abandoned is not work that failed, and letting it be re-opened
    would fold "dropped" back into the backlog the status was invented to keep
    separate. Such a task is re-created if it is genuinely needed again.

    Errors: 422 when the task was cancelled; 404 when it is not the caller's.
    """
    task = await tasks.get(task_id=task_id, owner=current_user)
    return await tasks.read(
        task=await tasks.reopen(task=task, owner=current_user), owner=current_user
    )


@router.post(
    "/{task_id}/start",
    response_model=TaskRead,
    summary="Move a task to in-progress",
    dependencies=[Depends(require_permission(Permission.TASKS_WRITE))],
)
async def start_task(
    task_id: UUID,
    current_user: AuthenticatedUser,
    tasks: TaskServiceDep,
) -> TaskRead:
    """Move a task into ``IN_PROGRESS``.

    The state machine only allows ``COMPLETED`` from ``IN_PROGRESS`` or
    ``BLOCKED``, so this is the route that makes completing a task reachable at
    all. It was missing until the analytics phase, which surfaced it: every
    completion metric read zero because no task could ever be completed, and
    that looked exactly like a user who completes nothing.
    """
    task = await tasks.get(task_id=task_id, owner=current_user)
    return await tasks.read(
        task=await tasks.start(task=task, owner=current_user), owner=current_user
    )


@router.post(
    "/{task_id}/block",
    response_model=TaskRead,
    summary="Mark a task blocked",
    dependencies=[Depends(require_permission(Permission.TASKS_WRITE))],
)
async def block_task(
    task_id: UUID,
    current_user: AuthenticatedUser,
    tasks: TaskServiceDep,
    payload: _BlockRequest | None = None,
) -> TaskRead:
    """Mark a task blocked, recording why.

    **The body is optional, and ``note`` is its only meaningful field.** It
    exists because the service takes the reason — :meth:`TaskService.block`
    accepts one and attaches it to the ``TASK_BLOCKED`` event — and because
    "why is this blocked?" is worth more than a boolean. It is deliberately *not*
    written onto the task row: the reason was true on Tuesday and is not true on
    Friday, and a card carrying a mutable "why" column would be read as a
    current fact by every client that renders it.

    The body being optional is not a convenience. "Mark this blocked" is a
    one-click action, and requiring a JSON object to say nothing would make the
    common case the one that needs the most typing.

    Errors: 422 when the status cannot move to ``blocked`` (a completed or
    cancelled task cannot be blocked) or when the body names a status other
    than ``blocked``; 404 for a task that is not the caller's.
    """
    task = await tasks.get(task_id=task_id, owner=current_user)
    return await tasks.read(
        task=await tasks.block(
            task=task, owner=current_user, reason=payload.note if payload else None
        ),
        owner=current_user,
    )


@router.post(
    "/{task_id}/cancel",
    response_model=TaskRead,
    summary="Cancel a task",
    dependencies=[Depends(require_permission(Permission.TASKS_WRITE))],
)
async def cancel_task(
    task_id: UUID,
    current_user: AuthenticatedUser,
    tasks: TaskServiceDep,
) -> TaskRead:
    """Cancel a task: work that is abandoned rather than finished.

    **The last open state in the lifecycle, and the one the wire could not
    reach.** ``TaskService._LEGAL_TRANSITIONS`` has allowed ``cancelled`` from
    ``todo``, ``in_progress`` and ``blocked`` since the first commit, and no
    route could take any of those edges — so ``TaskStats.cancelled`` was a hard
    zero for every account, which is indistinguishable from a user who never
    abandons work.

    **Cancelling is not completing.** ``completed_at`` is not stamped, the card
    does not count as done, and it does not start satisfying the things that
    wait on it: a cancelled prerequisite is a dropped prerequisite, which is why
    a task blocked behind one stays blocked until the edge is removed.

    **And it is not deleting.** ``cancelled`` keeps the row, its history and
    everything filed against it. A completed task cannot be cancelled — the
    work happened, and relabelling it would erase a fact rather than record one;
    deleting it is the path for that, and see this router's docstring for what
    that costs.

    ``reason``, when given, rides on the activity event and never on the card:
    "not doing this any more" is history, not a current fact every client would
    read.

    Errors: 422 when the status cannot move to ``cancelled`` (a completed task);
    404 for a task that is not the caller's.
    """
    task = await tasks.get(task_id=task_id, owner=current_user)
    return await tasks.read(
        task=await tasks.cancel(task=task, owner=current_user), owner=current_user
    )


@router.patch(
    "/{task_id}/priority",
    response_model=TaskRead,
    summary="Re-prioritise a task",
    dependencies=[Depends(require_permission(Permission.TASKS_WRITE))],
)
async def set_task_priority(
    task_id: UUID,
    payload: TaskPriorityChange,
    current_user: AuthenticatedUser,
    tasks: TaskServiceDep,
) -> TaskRead:
    """Move a task to a different grade on the priority scale.

    Separate from the detail PATCH because a re-triage is its own moment: the
    feed reports it as ``TASK_PRIORITY_CHANGED`` so "when was this de-prioritised
    and by whom" stays answerable, where folding it into a generic edit would
    leave it indistinguishable from a retyped title.

    **It is not the only way to reach the column.** ``PATCH /tasks/{id}`` also
    carries ``priority``, and a priority sent there writes the very same event
    with the same old and new values — so this route is the shorter path to the
    same record rather than a gate around it.

    Errors: 422 for a priority that is not a known value; 404 for a task that is
    not the caller's.
    """
    task = await tasks.get(task_id=task_id, owner=current_user)
    updated = await tasks.set_priority(task=task, priority=payload.priority, owner=current_user)
    return await tasks.read(task=updated, owner=current_user)


@router.get(
    "/{task_id}/subtasks",
    response_model=list[TaskSummary],
    summary="List a task's direct subtasks",
    dependencies=[Depends(require_permission(Permission.TASKS_READ))],
)
async def list_subtasks(
    task_id: UUID,
    current_user: AuthenticatedUser,
    tasks: TaskServiceDep,
) -> list[Task]:
    """Return the cards filed directly under this one, in board order.

    **One level, by construction** — the service refuses to create a subtask
    whose parent already has a parent, so this list is the whole tree and
    nothing here has to defend against a depth it cannot reach. A deeper tree
    would also be one the product cannot render: the board collapses children by
    exactly one level.

    A bare list, not a :class:`~app.schemas.common.Page`, and that is
    deliberate: the bound here is the caller's own subtask limit rather than a
    page size, so there is no "next page" to reach and a total that would
    always equal the row count. Tag ids on these summaries are empty — the
    service reads children in one query and does not join their tags; the board
    shows the parent's own labels.

    Errors: 404 for a task that is not the caller's.
    """
    task = await tasks.get(task_id=task_id, owner=current_user)
    return await tasks.list_subtasks(task=task, owner=current_user)


@router.get(
    "/{task_id}/dependencies",
    response_model=list[TaskSummary],
    summary="List the tasks this one is waiting on, or waiting on this one",
    dependencies=[Depends(require_permission(Permission.TASKS_READ))],
)
async def list_dependencies(
    task_id: UUID,
    current_user: AuthenticatedUser,
    tasks: TaskServiceDep,
    direction: Annotated[
        str,
        Query(
            description=(
                "'forward' (the default) lists what this task waits on; 'reverse' "
                "lists what waits on it."
            )
        ),
    ] = "forward",
) -> list[Task]:
    """Return the prerequisites of this task — the direction the user asks about.

    "What am I waiting on?" is the question the dependency panel exists to
    answer, and it is the easy one to get backwards: on the edge row
    ``depends_on_id`` is the *other* task, so an implementation that joins on the
    wrong column answers "what is waiting on me" from a method whose name
    promises the opposite, and nothing fails.

    **``direction`` is the other end of the same edge, and it is declared rather
    than assumed.** An undeclared query parameter is discarded by the router
    before the handler runs, so ``?direction=reverse`` used to answer ``200 []``
    for a card with three cards downstream of it: a request that named a
    direction the API did not have, answered as though the graph were empty. The
    two honest answers are a list or a 422, and this is now the first one.
    ``dependencies``/``dependents`` are accepted as names for the same two
    directions, because those are the words the model uses everywhere else.

    A bare list, for the reason given on the subtasks route. Completing a task
    walks the forward list, and ``COMPLETED`` is the only status that satisfies
    it.

    Errors: 404 for a task that is not the caller's; 422 for a direction that is
    neither forward nor reverse.
    """
    if direction in _REVERSE_DIRECTION_NAMES:
        reverse = True
    elif direction in _FORWARD_DIRECTION_NAMES:
        reverse = False
    else:
        raise ValidationError(
            f"Unknown dependency direction: {direction!r}.",
            details={
                "field": "direction",
                "allowed": sorted(_FORWARD_DIRECTION_NAMES | _REVERSE_DIRECTION_NAMES),
            },
        )
    task = await tasks.get(task_id=task_id, owner=current_user)
    if reverse:
        return await tasks.list_dependents(task=task, owner=current_user)
    return await tasks.list_dependencies(task=task, owner=current_user)


@router.post(
    "/{task_id}/dependencies",
    response_model=TaskSummary,
    status_code=status.HTTP_201_CREATED,
    summary="Declare that this task waits on another",
    dependencies=[Depends(require_permission(Permission.TASKS_WRITE))],
)
async def add_dependency(
    task_id: UUID,
    current_user: AuthenticatedUser,
    tasks: TaskServiceDep,
    depends_on_id: Annotated[
        UUID,
        Query(description="The task this one now waits on. Must be in the same project."),
    ],
) -> Task:
    """Record that ``task_id`` cannot finish before ``depends_on_id`` does.

    Three refusals, all of them the point of declaring the edge: a task may not
    wait on itself, may not wait on a card in another project, and may not close
    a cycle. The cycle rule is why this is 201-and-not-a-race rather than a
    cheap column check — ruling it out in the database would mean a recursive
    trigger paid on every insert into a table the product writes constantly, to
    prevent something a depth-first walk over a handful of prerequisites catches
    for free.

    The 201 body is the **blocker**, not the task that was blocked: the caller
    has just been told what it is waiting on, and that is the row it needs in
    order to render the new edge.

    **The identifier is a query parameter, not a body.** There is no
    ``TaskDependencyCreate`` in ``app/schemas/task.py``, and a router that
    invented one would be a second owner of the request shapes. It is a single
    field with nothing to extend, so the query parameter carries it until the
    schema lands — at which point this is a one-line change and nothing else
    about the route moves.

    Errors: 409 when the edge already exists; 422 for a self-dependency, a
    cross-project edge or a cycle; 404 when either task is not the caller's.
    """
    task = await tasks.get(task_id=task_id, owner=current_user)
    # Loaded through the scoped lookup rather than passed as a bare id, so the
    # task being returned to the caller is one the caller has just been shown
    # they own. The service re-checks ownership on the row it is handed anyway.
    blocker = await tasks.get(task_id=depends_on_id, owner=current_user)
    await tasks.add_dependency(task=task, depends_on=blocker, owner=current_user)
    return blocker


@router.delete(
    "/{task_id}/dependencies/{depends_on_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Drop a dependency edge",
    dependencies=[Depends(require_permission(Permission.TASKS_WRITE))],
)
async def remove_dependency(
    task_id: UUID,
    depends_on_id: UUID,
    current_user: AuthenticatedUser,
    tasks: TaskServiceDep,
) -> Response:
    """Remove one "waits on" edge.

    **This is also the documented way out of a stuck completion.** A
    prerequisite that was cancelled and is never going to be finished is
    unblocked by removing the edge, because ``CANCELLED`` deliberately does not
    satisfy a dependency. So the route answers 404 for an edge that is not there
    rather than succeeding quietly: a client that removed the wrong thing is
    told, instead of being left believing the blocker is gone when it is not.

    Errors: 404 for an edge that does not exist, or for either task not being
    the caller's.
    """
    task = await tasks.get(task_id=task_id, owner=current_user)
    await tasks.remove_dependency(task=task, depends_on=depends_on_id, owner=current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put(
    "/{task_id}/tags",
    response_model=list[UUID],
    summary="Replace a task's tags",
    dependencies=[Depends(require_permission(Permission.TASKS_WRITE))],
)
async def set_task_tags(
    task_id: UUID,
    payload: TagAssignment,
    current_user: AuthenticatedUser,
    tasks: TaskServiceDep,
) -> list[UUID]:
    """Replace this task's tags with exactly the set in the body.

    A **replacement, not an addition**: the client sends the tags the task should
    end up with. An additive endpoint would need a second call to undo a
    mistake, and the mistake here is a tag the client cannot see.

    An empty list clears the task's tags. That is the meaning of "set these
    tags", not a no-op.

    Every id is resolved against the caller **before** anything is written, so a
    request naming one of another account's tags changes nothing at all rather
    than half-applying — and the answer is a 404, so the response cannot be used
    to discover which tag ids are real.

    Errors: 404 for a task that is not the caller's, or for any tag that is not.
    """
    task = await tasks.get(task_id=task_id, owner=current_user)
    return await tasks.set_tags(task=task, tag_ids=payload.tag_ids, owner=current_user)


#: The router only; the handlers are reached through it, not imported directly.
__all__ = ["router"]
