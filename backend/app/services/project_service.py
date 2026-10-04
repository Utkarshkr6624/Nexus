"""Project business logic: the lifecycle rules around a body of work.

Projects are the top of the Phase 3 tree — tasks hang off them, and the planner,
knowledge and analytics phases hang off those. This module owns the rules a
router must not re-derive for itself: which status transitions exist, what
archiving means, what a window is allowed to look like, and which writes leave a
trace.

Routers translate the exceptions raised here into HTTP responses — this module
never imports FastAPI.

**Audit is not activity, and this module does not conflate them.**
:class:`~app.services.audit_service.AuditService` writes ``audit_logs`` and
answers *"who tried to do what to their account, and from where"* — sign-ins,
credential changes, session revocations, account deletion. Everything on that
table is a **security** event, and it is what an operator reads when the
question is whether something was accessed that should not have been.
``ActivityService`` writes ``activity_events`` and answers *"what happened to my
work"* — a project created, a task completed, a status moved.

The two are not interchangeable and the difference is not cosmetic:

* Phase 6 analytics reads ``activity_events``. It must not read ``audit_logs``,
  which is retained under a different policy, is a much smaller table, and holds
  client addresses rather than domain history.
* A project being completed is not a security event. Writing it to the audit
  trail would bury the sign-ins that trail exists to explain, and would make
  "was this account accessed suspiciously?" a question about how many tasks were
  ticked.

So every mutation below writes an :class:`~app.models.enums.ActivityEvent` and
none writes an :class:`~app.models.audit.AuditEvent` — with one honest
exception, the deletion, which is recorded on the *activity* feed too (see
:meth:`ProjectService.delete`). The ``audit`` constructor parameter is
deliberately accepted and never used; it is there so this service is wired the
same way as the auth services and so the seam exists if a security-relevant
project event is ever defined. Recording a domain event on the audit trail
"because the parameter was there" is precisely the conflation this paragraph
exists to prevent.

Repository contract relied on by this module::

    ProjectRepository.create(*, owner_id, name, description=None, priority="medium")
        -> Project
    ProjectRepository.get_by_id_for_user(project_id, owner_id) -> Project | None
    ProjectRepository.list_for_user(owner_id, *, limit, offset, status=None,
                                    search=None, sort="created_at",
                                    order="desc") -> tuple[list[Project], int]
    ProjectRepository.update_fields(project, **fields) -> Project
    ProjectRepository.delete(project) -> None
    ProjectRepository.count_for_user(owner_id) -> int
    ProjectRepository.stats_for_user(owner_id) -> dict[str, int]

and, for :meth:`ProjectService.summary` only, from
:class:`~app.repositories.task.TaskRepository`::

    TaskRepository.list_for_project(project_id, owner_id, *, limit, offset,
                                    status=None) -> tuple[list[Task], int]

Tenant isolation
----------------
**Every read and write in this module is scoped by ``owner.id`` in the query.**
:meth:`~app.repositories.project.ProjectRepository.get_by_id_for_user` puts the
predicate in the ``WHERE`` clause rather than loading a row and comparing
``row.owner_id`` afterwards, so a project the caller may not see is never loaded
at all and "not yours" is indistinguishable from "does not exist". That is what
makes the ID neither an authorisation nor an enumeration oracle. The
:meth:`_owned` helper is a *second*, cheap tripwire for the paths that take an
already-loaded row; it is not the primary check, and it raises the identical
``NotFoundError`` so it cannot become one either.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.core.config import Settings, get_settings
from app.core.exceptions import NotFoundError, ValidationError
from app.models.enums import ActivityEvent, ProjectStatus, TaskStatus, validate_project_status
from app.models.project import Project
from app.models.user import User
from app.repositories.project import ProjectRepository
from app.schemas.common import Page, PageMeta
from app.schemas.project import (
    ProjectCreate,
    ProjectRead,
    ProjectStats,
    ProjectSummary,
    ProjectUpdate,
)
from app.services.audit_service import AuditService

if TYPE_CHECKING:  # pragma: no cover - import cycle avoidance
    from app.repositories.task import TaskRepository
    from app.services.activity_service import ActivityService

__all__ = ["ProjectService"]

#: The default page size when a caller does not name one.
DEFAULT_PAGE_SIZE = 50

#: The legal project transitions, written down once.
#:
#: A lifecycle is not a bag of strings: ``completed`` means something specific
#: happened (``completed_at`` was stamped) and ``archived`` means something
#: specific happened (``archived_at`` was stamped). Letting a client write the
#: status directly would let it set the *premise* of those two without either
#: side effect, leaving a project that claims to be finished and carries no
#: evidence of finishing. The table below is what :meth:`ProjectService.set_status`
#: enforces, and it is the only door to a status change.
#:
#: **Every edge below is reachable over HTTP, and that is a property of the
#: router rather than of the table.** An edge no endpoint can walk is a
#: description of an intent. ``ON_HOLD`` listed here since the first commit and
#: was reached by nothing: ``app/api/v1/projects.py`` called ``set_status``
#: only with ``COMPLETED``, so ``ProjectStats.on_hold`` read a hard zero for
#: every account in the repository — a number indistinguishable from a user who
#: never pauses a project. ``/activate`` and ``/hold`` and ``/resume`` are the
#: doors those edges needed; ``/activate`` additionally unblocks ``/complete``,
#: which is only legal *from* ``ACTIVE``.
#:
#: The rules it encodes:
#:
#: * ``PLANNED`` may start or be shelved, but may not be *completed* directly —
#:   work nobody started does not get to claim it finished. It goes through
#:   ``ACTIVE``.
#: * ``ON_HOLD`` may only resume or be archived. Completing a project straight
#:   out of a hold would skip the work, so it resumes first.
#: * ``COMPLETED`` may be reopened (``ACTIVE``) or archived. Reopening clears
#:   ``completed_at``; see :meth:`set_status`.
#: * ``ARCHIVED`` is terminal **through this method**. :meth:`archive` is the one
#:   way in and :meth:`restore` the one way out, so the archived state cannot be
#:   left by a generic transition and the restore rule stays in one place.
#:
#: A transition to the status a project already holds is deliberately absent from
#: every set and is *not* an error: :meth:`set_status` short-circuits it into a
#: no-op before consulting this table, so a retried request or a double-clicked
#: button is idempotent rather than a 422.
_LEGAL_TRANSITIONS: Mapping[ProjectStatus, frozenset[ProjectStatus]] = {
    ProjectStatus.PLANNED: frozenset(
        {ProjectStatus.ACTIVE, ProjectStatus.ON_HOLD, ProjectStatus.ARCHIVED}
    ),
    ProjectStatus.ACTIVE: frozenset(
        {ProjectStatus.ON_HOLD, ProjectStatus.COMPLETED, ProjectStatus.ARCHIVED}
    ),
    ProjectStatus.ON_HOLD: frozenset({ProjectStatus.ACTIVE, ProjectStatus.ARCHIVED}),
    ProjectStatus.COMPLETED: frozenset({ProjectStatus.ACTIVE, ProjectStatus.ARCHIVED}),
    ProjectStatus.ARCHIVED: frozenset(),
}

#: What a restore lands on.
#:
#: ARCHIVED is terminal in the table above, so :meth:`restore` has to choose a
#: status to return to, and the schema has no column recording the one the
#: project held before it was archived. ``ACTIVE`` is the choice because it is
#: the state a project is in whenever work is happening, and a project being
#: un-archived is by definition one the user wants back in view. The cost is
#: that un-archiving a *completed* project returns it to ``ACTIVE`` rather than
#: ``COMPLETED``; storing the pre-archive status would fix that and costs a
#: column Phase 3 does not have. Flagged for review: it is a real, if minor,
#: lossy behaviour rather than an oversight.
_RESTORED_STATUS = ProjectStatus.ACTIVE

#: The columns a plain detail update may write.
#:
#: ``status``, ``completed_at`` and ``archived_at`` are absent on purpose: they
#: are written only by the transition methods, which is what keeps the lifecycle
#: rules above from being bypassable by a PATCH. ``owner_id`` and ``id`` are
#: structural. :class:`~app.schemas.project.ProjectUpdate` already forbids all
#: of them at the schema layer; this set is the second door, and it is here so
#: that a future schema that grows a ``status`` field still cannot become a back
#: door into the transition rules.
_UPDATABLE_FIELDS = frozenset({"description", "name", "priority", "start_date", "target_date"})

#: Sort keys the listing accepts.
#:
#: Validated here as well as in the repository, because the two failures are
#: different errors: the repository's ``ValueError`` means the code is wrong
#: (a programming error), while this one means a *request* asked to sort by
#: something that does not exist, which is a 422 and never a 500. Both layers
#: fail closed — neither falls back to a default column, because a silent
#: fallback answers a request for one ordering with a plausible ordering of
#: another.
_SORT_KEYS = frozenset(
    {"created_at", "updated_at", "name", "status", "priority", "start_date", "target_date"}
)
_SORT_ORDERS = frozenset({"asc", "desc"})

_PROJECT_NOT_FOUND = "Project not found."


class ProjectService:
    """The rules of the project lifecycle, and nothing else."""

    def __init__(
        self,
        repository: ProjectRepository,
        activity: ActivityService | None = None,
        audit: AuditService | None = None,
        settings: Settings | None = None,
        *,
        task_repository: TaskRepository | None = None,
    ) -> None:
        """Wire the service.

        Args:
            repository: Project persistence for the request-scoped session.
            activity: Where domain events are written. Optional so the lifecycle
                rules can be exercised without a history sink; production wires
                one so a completed project leaves a trace.
            audit: Accepted for symmetry with the auth services and
                deliberately unused — see the module docstring for why a project
                completing is an activity event and not a security one.
            settings: Application settings, resolved from the environment when
                not supplied.
            task_repository: Needed only by :meth:`summary`, which reports a
                project's task counts. It is keyword-only and optional so the
                constructor call sites keep working; leaving it out and then
                asking for a summary is a wiring mistake, and
                :attr:`_tasks` names it rather than inventing zero counts.
        """
        self.repository = repository
        self.activity = activity
        self.audit = audit
        self.settings = settings or get_settings()
        self.task_repository = task_repository

    # -- Creation ------------------------------------------------------------

    async def create(self, *, owner: User, data: ProjectCreate) -> Project:
        """Create a project in its initial state.

        The project is always ``planned``: :class:`ProjectCreate` has no status
        field and the column's server default is ``planned``, so there is no
        path — API, import or fixture — through which a project comes into
        existence already finished.

        ``owner_id`` is taken from the caller's session and never from the
        payload. A create endpoint that let the body name its owner would hand
        any authenticated user the ability to file work into another account.

        The window (``start_date``/``target_date``) is written in a second
        statement rather than at insert because
        :meth:`ProjectRepository.create` takes no dates. Both statements are in
        the same request-scoped transaction scope and the row is not returned
        until both have landed, so a caller never observes a project with a name
        and no window.

        Args:
            owner: The authenticated caller.
            data: The creation payload.

        Returns:
            The persisted project.

        Raises:
            Nothing. The schema has already validated the window, and the
            columns are unconstrained beyond NOT NULL.
        """
        project = await self.repository.create(
            owner_id=owner.id,
            name=data.name,
            description=data.description,
            priority=data.priority.value,
        )
        window = {
            key: value
            for key, value in (("start_date", data.start_date), ("target_date", data.target_date))
            if value is not None
        }
        if window:
            project = await self.repository.update_fields(project, **window)
        await self._record(
            ActivityEvent.PROJECT_CREATED,
            owner=owner,
            project=project,
            metadata={"name": project.name, "priority": project.priority},
        )
        return project

    async def get(self, *, project_id: uuid.UUID, owner: User) -> Project:
        """Return one of the caller's projects.

        Args:
            project_id: The project to fetch.
            owner: The authenticated caller.

        Returns:
            The project.

        Raises:
            NotFoundError: If the caller owns no project with this id. Another
                user's project and a nonexistent one are the same answer, on
                purpose: a different error would turn this endpoint into a probe
                for which project ids are real.
        """
        project = await self.repository.get_by_id_for_user(project_id, owner.id)
        if project is None:
            raise NotFoundError(_PROJECT_NOT_FOUND)
        return project

    async def list(
        self,
        *,
        owner: User,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
        status: str | None = None,
        search: str | None = None,
        sort: str = "created_at",
        order: str = "desc",
    ) -> Page[ProjectRead]:
        """List the caller's projects as a page.

        Every row and the ``total`` come from the same filtered statement, so
        "page 4 of 12" describes one result set rather than two that can
        disagree.

        Args:
            owner: The authenticated caller. The scope is the caller's own.
            limit: Maximum rows in the page.
            offset: Rows to skip.
            status: Restrict to one status. Validated here rather than passed
                through, so a typo is a 422 naming the field instead of a filter
                that silently matches nothing.
            search: Case-insensitive substring over name or description.
            sort: One of :data:`_SORT_KEYS`.
            order: ``"asc"`` or ``"desc"``.

        Returns:
            The page of projects and its metadata.

        Raises:
            ValidationError: If the page window, status, sort key or sort order
                is not one this service accepts.
        """
        _check_window(limit=limit, offset=offset)
        status_value = _status_or_none(status)
        sort = _check_sort(sort, order)
        rows, total = await self.repository.list_for_user(
            owner.id,
            limit=limit,
            offset=offset,
            status=status_value,
            search=search,
            sort=sort,
            order=order,
        )
        return Page[ProjectRead](
            items=[ProjectRead.model_validate(row) for row in rows],
            meta=PageMeta(total=total, limit=limit, offset=offset),
        )

    # -- Detail edits --------------------------------------------------------

    async def update(self, *, project: Project, data: ProjectUpdate, owner: User) -> Project:
        """Apply a partial update to a project's details.

        **PATCH semantics: a field is written if the client named it.** ``None``
        is a value for ``description`` and for both dates — a client clearing the
        description sends ``null`` — while an absent key leaves the column be.
        ``model_dump(exclude_unset=True)`` is the only thing that tells those two
        apart, because Pydantic collapses "absent" and "sent as null" into the
        same ``None``.

        The window is re-checked against the *persisted* row, not just against
        the payload. :class:`ProjectUpdate` can only compare two dates the client
        sent together, so a PATCH that moves only ``start_date`` past a stored
        ``target_date`` would otherwise persist an impossible schedule that no
        schema ever saw in one request.

        Status is not writable here and not present on the schema; see
        :data:`_UPDATABLE_FIELDS`.

        Args:
            project: The project, already resolved for this owner.
            data: The fields to change.
            owner: The authenticated caller.

        Returns:
            The updated project, or the unchanged one when nothing writable was
            sent.

        Raises:
            NotFoundError: If the row does not belong to the caller.
            ValidationError: If the resulting window ends before it starts.
        """
        self._owned(project, owner)
        sent = data.model_dump(exclude_unset=True)
        fields = {key: value for key, value in sent.items() if key in _UPDATABLE_FIELDS}
        start_date = fields.get("start_date", project.start_date)
        target_date = fields.get("target_date", project.target_date)
        if start_date is not None and target_date is not None and target_date < start_date:
            raise ValidationError("target_date must not be earlier than start_date.")
        if not fields:
            return project
        updated = await self.repository.update_fields(project, **fields)
        await self._record(
            ActivityEvent.PROJECT_UPDATED,
            owner=owner,
            project=updated,
            # Field names only, for the same reason user_service records only
            # names: a project's description can carry anything the user typed,
            # and an activity feed is not the place to copy it a second time.
            metadata={"fields": sorted(fields)},
        )
        return updated

    # -- Lifecycle -----------------------------------------------------------

    async def set_status(self, *, project: Project, status: str, owner: User) -> Project:
        """Move a project to another status, or refuse.

        This is the **only** door to a status change. :meth:`update` cannot
        reach the column, the schema cannot carry it, and the legal edges are
        :data:`_LEGAL_TRANSITIONS`.

        The timestamps follow the status, in both directions, and that is the
        whole point of routing every change through here:

        * entering ``COMPLETED`` stamps ``completed_at``;
        * leaving ``COMPLETED`` clears it, so a reopened project cannot keep
          reporting the moment it was "delivered".

        ``archived_at`` is *not* touched here. It belongs to
        :meth:`archive`/:meth:`restore` alone, so that "archived when" is
        answerable without reading a history. Archiving is still reachable from
        :data:`_LEGAL_TRANSITIONS` for completeness, and doing it through this
        method would be refused as a transition out of the terminal state — the
        archive path is :meth:`archive`.

        Args:
            project: The project, already resolved for this owner.
            status: The target :class:`~app.models.enums.ProjectStatus` value.
            owner: The authenticated caller.

        Returns:
            The project in its new state, or unchanged when it was already in
            that state.

        Raises:
            NotFoundError: If the row does not belong to the caller.
            ValidationError: If the status is unknown, the row's own status has
                drifted to a value this service does not recognise, or the
                transition is not in :data:`_LEGAL_TRANSITIONS`.
        """
        self._owned(project, owner)
        target = _status_or_raise(status, "status")
        current = _current_status(project)
        if current == target:
            # Idempotent: a retried request or a double-clicked button gets the
            # project it asked for rather than a 422 for asking twice.
            return project
        if target not in _LEGAL_TRANSITIONS[current]:
            raise ValidationError(
                f"A project cannot move from {current.value!r} to {target.value!r}.",
                details={
                    "from": current.value,
                    "to": target.value,
                    "allowed": sorted(member.value for member in _LEGAL_TRANSITIONS[current]),
                },
            )
        fields: dict[str, object] = {"status": target.value}
        if target is ProjectStatus.COMPLETED:
            fields["completed_at"] = datetime.now(UTC)
        elif current is ProjectStatus.COMPLETED:
            fields["completed_at"] = None
        updated = await self.repository.update_fields(project, **fields)
        await self._record(
            _transition_event(current, target),
            owner=owner,
            project=updated,
            metadata={"from": current.value, "to": target.value},
        )
        return updated

    async def activate(self, *, project: Project, owner: User) -> Project:
        """Move a planned project into the working set.

        The ``PLANNED -> ACTIVE`` edge of :data:`_LEGAL_TRANSITIONS`, and the
        door it was missing. Without it a project could only ever become
        ``active`` as a side effect of :meth:`restore`, so "start this project"
        had no route — and ``COMPLETED`` is only reachable *from* ``ACTIVE``, so
        ``/complete`` was unreachable for every project the user had not
        archived and un-archived first. The edge existed, the rule was written
        down, and nothing could walk it.

        Args:
            project: The project, already resolved for this owner.
            owner: The authenticated caller.

        Returns:
            The active project, or the unchanged one when it already was.

        Raises:
            NotFoundError: If the row does not belong to the caller.
            ValidationError: If the status may not become ``active`` — an
                ``on_hold`` project resumes (:meth:`resume`), a ``completed`` one
                reopens through ``/complete``'s own edge, and ``archived`` is
                terminal through this method.
        """
        return await self.set_status(
            project=project, status=ProjectStatus.ACTIVE.value, owner=owner
        )

    async def hold(self, *, project: Project, owner: User) -> Project:
        """Shelve a project: stop working on it, keep it in the working set.

        **This is what makes ``ON_HOLD`` a state rather than an aspiration.**
        :data:`_LEGAL_TRANSITIONS` has allowed ``PLANNED -> ON_HOLD`` and
        ``ACTIVE -> ON_HOLD`` from the first commit, and until now no endpoint
        could take either edge: ``set_status`` was only ever called with
        ``COMPLETED``. ``ProjectStats.on_hold`` therefore read a hard zero for
        every account — indistinguishable from a user who never pauses a
        project, which is the same false signal ``/start`` used to send about
        task completion.

        A hold is not an archive. The project stays in the working set, keeps
        its tasks and its window, and comes back through :meth:`resume`.
        Completing straight out of a hold is refused by the table, because the
        point of shelving is that the work paused.

        Args:
            project: The project, already resolved for this owner.
            owner: The authenticated caller.

        Returns:
            The held project, or the unchanged one when it already was.

        Raises:
            NotFoundError: If the row does not belong to the caller.
            ValidationError: If the status may not become ``on_hold`` — a
                ``completed`` or ``archived`` project cannot be shelved.
        """
        return await self.set_status(
            project=project, status=ProjectStatus.ON_HOLD.value, owner=owner
        )

    async def resume(self, *, project: Project, owner: User) -> Project:
        """Take a held project back into the working set.

        The ``ON_HOLD -> ACTIVE`` edge, and the only way out of a hold that is
        not an archive. It lands on ``ACTIVE`` rather than on whatever the
        project held before — the schema records no pre-hold status, the same
        trade :data:`_RESTORED_STATUS` makes for :meth:`restore`.

        Resuming a project that is not on hold is a no-op rather than a 422,
        for the same reason every other idempotent write in this service is
        one: a retried click is a retry, not a mistake.

        Args:
            project: The project, already resolved for this owner.
            owner: The authenticated caller.

        Returns:
            The resumed project, or the unchanged one.

        Raises:
            NotFoundError: If the row does not belong to the caller.
            ValidationError: If the row's status has drifted to an unknown value.
        """
        return await self.set_status(
            project=project, status=ProjectStatus.ACTIVE.value, owner=owner
        )

    async def archive(self, *, project: Project, owner: User) -> Project:
        """Archive a project: hide it from the working set, keep its history.

        Archiving sets ``status`` and stamps ``archived_at`` together, because
        both describe the same moment and a project that is ``archived`` with a
        null ``archived_at`` cannot answer "archived when?".

        A project that was ``completed`` loses its ``completed_at`` on the way
        out, the same rule :meth:`set_status` applies: the stamp describes the
        status, and after this write the status is no longer ``completed``.

        Archiving an archived project is a no-op rather than an error.

        Args:
            project: The project, already resolved for this owner.
            owner: The authenticated caller.

        Returns:
            The archived project, or the unchanged one if it already was.

        Raises:
            NotFoundError: If the row does not belong to the caller.
            ValidationError: If the row's status has drifted to an unknown value.
        """
        self._owned(project, owner)
        current = _current_status(project)
        if current is ProjectStatus.ARCHIVED:
            return project
        fields: dict[str, object] = {
            "status": ProjectStatus.ARCHIVED.value,
            "archived_at": datetime.now(UTC),
        }
        if current is ProjectStatus.COMPLETED:
            fields["completed_at"] = None
        updated = await self.repository.update_fields(project, **fields)
        await self._record(
            ActivityEvent.PROJECT_ARCHIVED,
            owner=owner,
            project=updated,
            metadata={"from": current.value},
        )
        return updated

    async def restore(self, *, project: Project, owner: User) -> Project:
        """Return an archived project to the working set.

        The inverse of :meth:`archive`: ``archived_at`` is cleared, because the
        project is no longer archived and a lingering stamp would make
        "archived when?" answer with a moment it is not in.

        The status becomes ``ACTIVE`` — see :data:`_RESTORED_STATUS` for why,
        and for what that costs.

        Args:
            project: The project, already resolved for this owner.
            owner: The authenticated caller.

        Returns:
            The restored project, or the unchanged one if it was not archived.

        Raises:
            NotFoundError: If the row does not belong to the caller.
            ValidationError: If the row's status has drifted to an unknown value.
        """
        self._owned(project, owner)
        current = _current_status(project)
        if current is not ProjectStatus.ARCHIVED:
            return project
        updated = await self.repository.update_fields(
            project,
            status=_RESTORED_STATUS.value,
            archived_at=None,
        )
        await self._record(
            ActivityEvent.PROJECT_RESTORED,
            owner=owner,
            project=updated,
            metadata={"to": _RESTORED_STATUS.value},
        )
        return updated

    async def delete(self, *, project: Project, owner: User) -> None:
        """Delete a project and everything filed under it.

        **Note the tension with the model layer.** The docstring on
        :class:`~app.models.project.Project` states that a project "is never
        deleted in Phase 3 — archiving is the terminal state", and the enum
        agrees by making ``ARCHIVED`` terminal. This method exists because the
        Phase 3 service brief requires it, and because a local-first product
        that cannot delete its own data is lying to its user. The intended
        product answer is still :meth:`archive`: this is the explicit,
        destructive, user-initiated path, and a router should offer it as such.

        What the database does is not this module's decision but is worth
        stating: ``tasks`` and ``project_tags`` cascade with the project,
        because a task without its project has no meaning, while
        ``activity_events`` is set to ``NULL`` by its own ``ON DELETE SET NULL``,
        so deleting a project does not erase the record of what happened inside
        it. That is why the event below is written **after** the delete — the
        foreign key no longer has to resolve, and a failure of the delete leaves
        no event claiming it happened.

        **And that is where the cascade reaches further than it looks.** The
        tasks going with it are themselves the foreign key on the Phase 4 tables
        (``WorkSession.task_id`` and ``CalendarEvent.task_id``, both ``ON DELETE
        CASCADE`` at ``app/models/planner.py:149-151`` and ``:272-280``), and
        those tables also carry a direct ``project_id`` cascade of their own. So
        one ``DELETE /projects/{id}`` removes **every tracked minute and every
        calendar booking filed under that project**, transitively, with nothing
        warning the owner and no way back. Nothing about it is recoverable: the
        Phase 6 analytics read those rows, so a later window reports fewer hours
        worked, which is indistinguishable from the user having worked less.

        Same recommendation as :meth:`app.services.task_service.TaskService.delete`,
        one level up: **leave the cascade, make the destructive path opt-in.**
        A confirmation that names what is about to go — the task count, the
        minutes recorded — costs nothing and is honest; removing the cascade
        would orphan sessions pointing at a project nobody can name, which is
        the shape the planner model explicitly avoids. Archiving is the answer
        for everything except data that should not exist.

        Args:
            project: The project, already resolved for this owner.
            owner: The authenticated caller.

        Raises:
            NotFoundError: If the row does not belong to the caller.
        """
        self._owned(project, owner)
        project_id = project.id
        name = project.name
        await self.repository.delete(project)
        # ``project_id`` is deliberately ``None``: passing the id this call just
        # deleted raises ``ForeignKeyViolation`` inside the best-effort audit
        # write, which swallows it — so the deletion would go unrecorded while
        # the endpoint still answered 204. The id travels in ``metadata``
        # instead, which is where a deleted row's identity has to live now that
        # the join it supported no longer has a row on the other end.
        await self._record(
            ActivityEvent.PROJECT_DELETED,
            owner=owner,
            project=None,
            project_id=None,
            metadata={"project_id": str(project_id), "name": name},
        )

    # -- Aggregates ----------------------------------------------------------

    async def stats(self, *, owner: User) -> ProjectStats:
        """Return the caller's projects counted by status.

        One grouped query, so the buckets cannot disagree with each other the way
        five separate counts can when a write lands between them.

        Args:
            owner: The authenticated caller.

        Returns:
            The counts. Every bucket is present even when empty, so a dashboard
            does not lose a column when the first project in it is deleted.
        """
        counts = await self.repository.stats_for_user(owner.id)
        return ProjectStats(
            total=counts.get("total", 0),
            active=counts.get(ProjectStatus.ACTIVE.value, 0),
            completed=counts.get(ProjectStatus.COMPLETED.value, 0),
            planned=counts.get(ProjectStatus.PLANNED.value, 0),
            on_hold=counts.get(ProjectStatus.ON_HOLD.value, 0),
            archived=counts.get(ProjectStatus.ARCHIVED.value, 0),
        )

    async def summary(self, *, project: Project, owner: User) -> ProjectSummary:
        """Return a project plus its task counts and progress percentage.

        The counts come from the task repository's aggregate — two grouped
        statements, not a count per project — and the percentage is derived from
        them inside :class:`~app.schemas.project.ProjectSummary`, so the number
        on screen is always the number beside it.

        Subtasks count toward ``task_count``. They are real work the user filed
        under this project; excluding them would make a project's progress
        depend on how deeply someone nested their checklist, which is a
        presentation decision dressed up as a metric.

        Args:
            project: The project, already resolved for this owner.
            owner: The authenticated caller.

        Returns:
            The summary, with ``progress_percent`` already derived.

        Raises:
            NotFoundError: If the row does not belong to the caller.
            ValueError: If the service was built without a task repository. That
                is a wiring mistake rather than an outcome of a request, so it is
                raised as one rather than answered with invented zero counts.
        """
        self._owned(project, owner)
        tasks = self._tasks
        # ``list_for_project`` returns the *unpaginated* total alongside its
        # rows, so a limit of 1 costs the same round trip as a limit of 50 and
        # the count does not depend on the page size.
        _, task_count = await tasks.list_for_project(project.id, owner.id, limit=1, offset=0)
        _, completed_count = await tasks.list_for_project(
            project.id,
            owner.id,
            limit=1,
            offset=0,
            status=TaskStatus.COMPLETED.value,
        )
        return ProjectSummary.build(
            ProjectRead.model_validate(project),
            task_count=task_count,
            completed_task_count=completed_count,
        )

    # -- Internals -----------------------------------------------------------

    @property
    def _tasks(self) -> TaskRepository:
        """The task repository, or a loud failure when it was never wired.

        Only :meth:`summary` needs it. Returning ``None`` from here and letting
        the caller trip over ``NoneType`` would put the diagnosis three frames
        away; this names the misconfiguration where it happened.
        """
        if self.task_repository is None:
            raise ValueError(
                "ProjectService.summary needs a task repository; build the service "
                "with task_repository=... to use it."
            )
        return self.task_repository

    def _owned(self, project: Project, owner: User) -> None:
        """Refuse a row that is not the caller's.

        A tripwire, not the authorisation. Every project reaching a mutating
        method was resolved through :meth:`get`, whose query is scoped by
        ``owner_id``, so this comparison cannot be the thing standing between an
        IDOR and a write — the row was never loaded in the first place. It
        exists for the caller that assembles a ``Project`` some other way (a
        test fixture, a future import job), and it raises the *same*
        ``NotFoundError`` as :meth:`get` so it cannot be used to tell a
        forbidden project from a missing one.
        """
        if project.owner_id != owner.id:
            raise NotFoundError(_PROJECT_NOT_FOUND)

    async def _record(
        self,
        event: ActivityEvent,
        *,
        owner: User,
        project: Project | None = None,
        project_id: uuid.UUID | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> None:
        """Write one activity event, or do nothing when no feed is configured.

        Never raises. :meth:`ActivityService.record` is best-effort by design and
        this wrapper only adds the "no sink" case to the same rule: history is
        observability of the work, not a precondition for doing it, and a
        history table that is unavailable must not stop a user completing a
        task.
        """
        if self.activity is None:
            return
        await self.activity.record(
            event.value,
            user_id=owner.id,
            project_id=project_id if project_id is not None else (project.id if project else None),
            metadata=metadata,
        )


def _current_status(project: Project) -> ProjectStatus:
    """Return the project's status as a member, or raise.

    A row whose status is not one of the vocabulary members is a data problem,
    and it is raised rather than defaulted. Substituting ``PLANNED`` would put a
    corrupted project back into the working set with no visible sign that
    anything is wrong; raising says plainly that the row cannot be reasoned
    about. Validate on the way in with
    :func:`app.models.enums.validate_project_status` so this stays unreachable.
    """
    try:
        return validate_project_status(project.status)
    except ValueError as exc:
        raise ValidationError(
            f"This project's status is not a known value: {project.status!r}."
        ) from exc


def _transition_event(current: ProjectStatus, target: ProjectStatus) -> ActivityEvent:
    """Return the activity event that describes one legal transition.

    A project completing, archiving or being reopened is the moment a history
    exists to remember, so it gets its own event rather than a generic update;
    everything else that moves a project is just an update.
    """
    if target is ProjectStatus.COMPLETED:
        return ActivityEvent.PROJECT_COMPLETED
    if target is ProjectStatus.ARCHIVED:
        return ActivityEvent.PROJECT_ARCHIVED
    if current is ProjectStatus.ARCHIVED:
        return ActivityEvent.PROJECT_RESTORED
    return ActivityEvent.PROJECT_UPDATED


def _status_or_none(value: str | None) -> str | None:
    """Validate a status filter, or return ``None`` for no filter.

    The enum helpers raise :class:`ValueError`, which the API layer would turn
    into a 500. A filter naming a status that does not exist is the caller's
    mistake, so it is translated into the 422 that answers it.
    """
    if value is None:
        return None
    return _status_or_raise(value, "status").value


def _status_or_raise(value: str | ProjectStatus, field: str) -> ProjectStatus:
    """Coerce a status value into a member, or raise :class:`ValidationError`."""
    try:
        return validate_project_status(value)
    except ValueError:
        raise ValidationError(f"Unknown {field}: {value!r}.") from None


def _check_window(*, limit: int, offset: int) -> None:
    """Reject a page window that is not one.

    ``limit`` is at least one and ``offset`` at least zero. A negative offset is
    a silent wrong answer in PostgreSQL — ``OFFSET -1`` is an error there, but a
    caller computing one from a cursor can produce it — and a zero limit returns
    an empty page whose ``total`` does not match anything anybody asked for.
    """
    if limit < 1:
        raise ValidationError("limit must be at least 1.")
    if offset < 0:
        raise ValidationError("offset must be zero or greater.")


def _check_sort(sort: str, order: str) -> str:
    """Validate the sort key and direction, returning the key.

    ``ORDER BY`` takes an expression rather than a bound parameter, so an
    unvalidated sort name reaching the repository would be SQL injection behind
    a query parameter. Both layers reject an unknown name; this one raises the
    422 a request deserves, the repository raises the ``ValueError`` a
    programming error deserves.
    """
    if sort not in _SORT_KEYS:
        raise ValidationError(
            f"Cannot sort projects by {sort!r}.",
            details={"allowed": sorted(_SORT_KEYS)},
        )
    if order not in _SORT_ORDERS:
        raise ValidationError(
            f"Cannot sort projects {order!r}.",
            details={"allowed": sorted(_SORT_ORDERS)},
        )
    return sort
