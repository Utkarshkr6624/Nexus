"""Action *proposals*: the confirm layer between an utterance and a write.

This module answers the question ``app/api/v1/ml.py`` deferred. That router's
docstring said slot filling "needs either a second model or hand-written
per-utterance parsers"; the project brief allows exactly one of those, and
:mod:`app.ml.actions.extraction` is the hand-written one. What this module adds
is the thing that makes the parser safe to wire to a database: **nothing here
executes anything.** A proposal is a fully populated, validated payload plus the
sentence a user reads before agreeing to it. The caller shows the sentence, the
user confirms, and *then* the caller calls the service it names.

**The assistant may do anything, and one sentence still cannot do it.**
:data:`ActionKind` covers every write in the app — create, update, re-status,
schedule, tag, publish, archive and delete, across tasks, projects, knowledge,
learning, the planner, the developer surface's repositories and the account — and
:attr:`ActionProposal.destructive` is a field that is ``True`` for the deleting
kinds rather than a property that cannot be set. What has *not* changed is the
shape of the safety, and that is the part worth stating plainly, because it is
four separate properties rather than one refusal:

*Nothing runs without a confirm.* :attr:`ActionProposal.requires_confirmation` is
a property returning ``True``; it is not a constructor argument, not a field, and
not something a caller can pass. A bare utterance is a description.

*The classifier cannot see the verb, so every action names one row.*
:data:`ml.datasets.taxonomy.Intent.TASK_MANAGE` covers create, complete, block,
cancel, reorder **and delete**, because the taxonomy asks *which surface a request
lands on*, not *what to do to it*. Verb is not one of the fourteen classes. That
is why a proposal is never "the action this sentence implies": it is "the action
on **this** task", resolved by :func:`~app.ml.actions.extraction.match_reference`
against rows the caller already owns. A reference that matches zero rows or
several is a refusal (:data:`ProposalReason.TARGET_NOT_FOUND`,
:data:`~ProposalReason.TARGET_AMBIGUOUS`) and never a best guess.

*One sentence, one row.* "delete all my tasks", "clear the board" and "delete
everything" are refused with :data:`ProposalReason.DESTRUCTIVE_REQUEST`. This is
the sharpest of the four, because it is the one a bulk request would otherwise
pass by asking nicely: an unbounded delete needs a preview of what it would
remove and a count the user has checked, and neither is something a single
utterance can carry. ``DESTRUCTIVE_REQUEST`` now means exactly this and nothing
else — a single-row delete is an ordinary proposal with an ordinary sentence.

*A delete has to be asked for twice.* :attr:`ActionSpec.destructive` is published
so a client can render the confirm button as a warning, and the confirm route
refuses a destructive kind unless the client sends ``confirm_destructive=True``.
The double press is the whole of it; it is not a speed bump on a decision NEXUS
is entitled to make for the user, because it is the user's data.

*Nothing is believed at confirm time.* Every id is re-resolved through an
owner-scoped service method before the write, so a forged ``target_id`` is a 404
rather than a delete of somebody else's row. This module never touches the
database — :class:`ProposalContext` carries the candidate rows, already scoped by
whoever read them — so the scoping is the caller's, and the caller's scoping is
what the confirm route repeats.

**Only what was actually said appears in the summary.** "Create a high-priority
task" is not written when the user said nothing about priority: ``medium`` is a
*default*, and printing it would make the sentence claim a choice nobody made.
The same rule governs updates: a rename says what it renames *from* and *to*, and
an update that states one field says only that field. A missing clause means "not
stated", which is also why an unresolvable date produces a note rather than a
guess.

**What a caller has to supply.** None of this is guessable from an utterance. A
task belongs to a project, and every action that is not a creation names a row
the user already owns, so :class:`ProposalContext` carries both. Without them the
answer is a refusal with a reason, never a payload with a made-up id: a task
attached to a guessed project lands in somebody else's board, and a delete named
by a guessed id is worse.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, ValidationError

from app.core.permissions import Permission
from app.ml.actions.extraction import (
    Argument,
    ExtractedVerb,
    Extraction,
    RowCandidate,
    RowMatch,
    RowMatchFailure,
    extract_arguments,
    local_day,
    match_reference,
)
from app.ml.schemas import IntentPrediction
from app.models.enums import KnowledgeEntityType, ProjectStatus, TaskStatus
from app.schemas.developer import RepositoryCreate
from app.schemas.knowledge import (
    BookmarkCreate,
    ConceptCreate,
    KnowledgeLinkCreate,
    NoteCreate,
    NoteUpdate,
)
from app.schemas.learning import LearningGoalWrite, SkillWrite
from app.schemas.planner import CalendarEventCreate, CalendarEventUpdate, WorkSessionCreate
from app.schemas.project import ProjectCreate, ProjectUpdate
from app.schemas.task import TaskCreate, TaskStatusChange, TaskUpdate
from app.schemas.user import UserUpdate
from ml.datasets.taxonomy import Intent

__all__ = [
    "ACTION_SPECS",
    "ActionAck",
    "ActionKind",
    "ActionProposal",
    "ActionSpec",
    "ProjectStatusWrite",
    "ProposalContext",
    "ProposalReason",
    "ProposalRefusal",
    "TaskScheduleWrite",
    "is_proposal",
    "propose_action",
    "render_summary",
]

#: How long an event or a work session lasts when the utterance named a day but
#: no window. Stated in the confirm sentence rather than hidden: the user reads
#: "from Monday 09:00 to 10:00" and can reject it. A schema that required a
#: window would otherwise turn every "block friday for the design review" into a
#: refusal, and a refusal is a worse answer than a stated assumption.
_ASSUMED_WINDOW_MINUTES = 60

#: Bound on the free text :class:`ActionAck` carries. Matches the description
#: bound the services already use for a transition note, so a refusal here would
#: never be a surprise the service had to produce instead.
_MAX_ACK_TEXT_LENGTH = 2000


class ActionKind(StrEnum):
    """The closed set of things this layer can propose.

    Every write the app offers, grouped by the surface that owns it: tasks,
    projects, knowledge, learning, the planner, the repositories on the developer
    surface and the caller's own account.
    Creating is a minority of them, because after "add a task" the requests that
    actually accumulate are the second ones — rename, reschedule, re-status, and
    remove the thing that was created in error.

    Membership is not a judgement about whether the effect is reversible. That
    judgement is :attr:`ActionSpec.destructive`, and it lives in the table next
    to the entry point rather than in the name, because what a kind *does* is a
    property of the service call and not of the vocabulary.
    """

    # Tasks
    CREATE_TASK = "create_task"
    UPDATE_TASK = "update_task"
    DELETE_TASK = "delete_task"
    COMPLETE_TASK = "complete_task"
    SET_TASK_STATUS = "set_task_status"
    SCHEDULE_TASK = "schedule_task"
    UNSCHEDULE_TASK = "unschedule_task"
    TAG_TASK = "tag_task"
    UNTAG_TASK = "untag_task"

    # Projects
    CREATE_PROJECT = "create_project"
    UPDATE_PROJECT = "update_project"
    DELETE_PROJECT = "delete_project"
    SET_PROJECT_STATUS = "set_project_status"

    # Knowledge
    CREATE_NOTE = "create_note"
    UPDATE_NOTE = "update_note"
    DELETE_NOTE = "delete_note"
    ARCHIVE_NOTE = "archive_note"
    PUBLISH_NOTE = "publish_note"
    CREATE_BOOKMARK = "create_bookmark"
    DELETE_BOOKMARK = "delete_bookmark"
    CREATE_CONCEPT = "create_concept"
    DELETE_CONCEPT = "delete_concept"
    CREATE_LINK = "create_link"
    DELETE_LINK = "delete_link"

    # Learning
    CREATE_LEARNING_GOAL = "create_learning_goal"
    UPDATE_LEARNING_GOAL = "update_learning_goal"
    COMPLETE_LEARNING_GOAL = "complete_learning_goal"
    DELETE_LEARNING_GOAL = "delete_learning_goal"
    CREATE_SKILL = "create_skill"
    DELETE_SKILL = "delete_skill"

    # Planner
    CREATE_EVENT = "create_event"
    UPDATE_EVENT = "update_event"
    DELETE_EVENT = "delete_event"
    CREATE_SESSION = "create_session"
    DELETE_SESSION = "delete_session"

    # Developer intelligence
    CREATE_REPOSITORY = "create_repository"
    DELETE_REPOSITORY = "delete_repository"

    # Account
    UPDATE_PROFILE = "update_profile"


class ProposalReason:
    """The closed vocabulary a refusal can report.

    A client branches on these, and so does this package's own test suite — a
    refusal whose reason is prose can be asserted on but not acted on.

    ``TARGET_NOT_FOUND`` and ``TARGET_AMBIGUOUS`` are one decision seen from two
    sides: a reference that matches no row and a reference that matches several
    are both refusals, and both are the answer to a question the app can only ask
    the user. The two ``TASK_REFERENCE_*`` codes are the original pair from when
    tasks were the only rows a proposal could name; they are kept because clients
    branch on them and because nothing about them became untrue, but the generic
    pair is what new kinds report.
    """

    UNSUPPORTED_INTENT = "unsupported_intent"
    DESTRUCTIVE_REQUEST = "destructive_request"
    VERB_NOT_RECOVERED = "verb_not_recovered"
    TITLE_NOT_RECOVERABLE = "title_not_recoverable"
    TASK_REFERENCE_AMBIGUOUS = "task_reference_ambiguous"
    TASK_REFERENCE_NOT_FOUND = "task_reference_not_found"
    CONTEXT_MISSING = "context_missing"
    PAYLOAD_INVALID = "payload_invalid"
    TARGET_AMBIGUOUS = "target_ambiguous"
    TARGET_NOT_FOUND = "target_not_found"
    FIELD_NOT_RECOVERABLE = "field_not_recoverable"
    ENTITY_NOT_RECOGNISED = "entity_not_recognised"


# --------------------------------------------------------------------------- #
# Payloads this layer owns
# --------------------------------------------------------------------------- #


class ActionAck(BaseModel):
    """The payload of an action whose whole effect is a verb.

    A delete, an archive, a publish, a schedule transition and a tag change carry
    no fields of their own: what they change is the row the caller named, and
    everything the row holds is already there. So this model exists to be
    *empty* — every member is optional, and ``{}`` is a valid payload.

    ``extra="forbid"`` is kept even here. The confirm route validates an untrusted
    body against this schema, and under Pydantic's default a client sending
    ``{"task_id": "..."}`` would get a cheerful 200 with the field dropped — which
    would read as "the row you named is the row that was deleted", and that is
    exactly the belief this layer refuses to let a caller hold.

    The optional members are the things such a request can genuinely carry: a
    ``reason`` or ``note`` recorded on the resulting activity event, the ``tag``
    a tag or untag names, and a ``status`` word for a transition whose schema
    does not constrain it.
    """

    model_config = {"extra": "forbid"}

    note: str | None = Field(
        default=None, max_length=_MAX_ACK_TEXT_LENGTH, description="Context on the resulting event."
    )
    tag: str | None = Field(
        default=None, max_length=48, description="The tag a tag or untag request names."
    )
    reason: str | None = Field(
        default=None, max_length=_MAX_ACK_TEXT_LENGTH, description="Why, recorded on the event."
    )
    status: str | None = Field(
        default=None, max_length=64, description="The state word, for transitions that take one."
    )


class TaskScheduleWrite(BaseModel):
    """The window a :data:`ActionKind.SCHEDULE_TASK` writes.

    ``start_date`` is required and ``due_date`` is not, which mirrors
    :class:`~app.schemas.task.TaskCreate`: a task may be scheduled from a day
    without being given an end. "Schedule it from Monday" is a real request and
    the schema does not make the user invent a deadline to express it.

    ``extra="forbid"`` for the reason :class:`ActionAck` gives.
    """

    model_config = {"extra": "forbid"}

    start_date: date = Field(description="First day of the planned window.")
    due_date: date | None = Field(default=None, description="Date the work is wanted by.")


class ProjectStatusWrite(BaseModel):
    """The state a :data:`ActionKind.SET_PROJECT_STATUS` moves a project to.

    A project's lifecycle has its own transition endpoints rather than an
    ordinary edit, so this exists to keep the status out of
    :class:`~app.schemas.project.ProjectUpdate` — where the app has deliberately
    forbidden it — while still letting one sentence move a project.

    ``extra="forbid"`` for the reason :class:`ActionAck` gives.
    """

    model_config = {"extra": "forbid"}

    status: ProjectStatus = Field(description="State being moved to; the service owns the rules.")
    note: str | None = Field(
        default=None, max_length=_MAX_ACK_TEXT_LENGTH, description="Context on the resulting event."
    )


@dataclass(frozen=True, slots=True)
class ActionSpec:
    """What one kind of proposal is: the payload, the permission, the call.

    ``schema`` is the Pydantic model the payload is validated against, held as a
    reference rather than re-declared, so a proposal is by construction the shape
    the API route already accepts — there is no second definition of what
    ``TaskCreate`` means to drift out of step with the first.

    ``permission`` is the capability the *route* enforces, so a caller can check
    authorisation at the moment it shows the confirm dialog instead of after the
    user has already pressed the button.

    ``destructive`` is a **field**, because the table below now says which
    actions delete and the client has to be able to warn before the button is
    pressed. It is a field rather than a keyword argument on the proposal because
    the answer belongs to the *kind*: the user cannot ask for a delete and receive
    a create, and a caller cannot soften one into the other on the way to the
    dialog.

    ``also_intents`` are the other trained intents the same request may be
    classified as. ``schedule_plan`` and ``knowledge_lookup`` both cover requests
    this table answers, and a confirm carries the intent the classifier actually
    produced — so the confirm route accepts the primary intent *or* any of these.
    """

    kind: ActionKind
    intent: Intent
    schema: type[BaseModel]
    permission: Permission
    service: str
    module: str
    entrypoint: str
    title_field: str | None = "title"
    date_field: str | None = None
    also_intents: frozenset[Intent] = frozenset()
    destructive: bool = False

    @property
    def qualified(self) -> str:
        """``module.ClassName``, for logs and for resolving the import."""
        return f"{self.module}.{self.service}"


#: One entry per :class:`ActionKind`. Frozen, because a table a request could
#: edit is a table that no longer describes the deployment.
ACTION_SPECS: Mapping[ActionKind, ActionSpec] = MappingProxyType(
    {
        # --- tasks ------------------------------------------------------- #
        ActionKind.CREATE_TASK: ActionSpec(
            kind=ActionKind.CREATE_TASK,
            intent=Intent.TASK_MANAGE,
            schema=TaskCreate,
            permission=Permission.TASKS_WRITE,
            service="TaskService",
            module="app.services.task_service",
            entrypoint="create",
            title_field="title",
            date_field="due_date",
        ),
        ActionKind.UPDATE_TASK: ActionSpec(
            kind=ActionKind.UPDATE_TASK,
            intent=Intent.TASK_MANAGE,
            schema=TaskUpdate,
            permission=Permission.TASKS_WRITE,
            service="TaskService",
            module="app.services.task_service",
            entrypoint="update",
            title_field="title",
            date_field="due_date",
        ),
        ActionKind.DELETE_TASK: ActionSpec(
            kind=ActionKind.DELETE_TASK,
            intent=Intent.TASK_MANAGE,
            schema=ActionAck,
            permission=Permission.TASKS_WRITE,
            service="TaskService",
            module="app.services.task_service",
            entrypoint="delete",
            destructive=True,
        ),
        ActionKind.COMPLETE_TASK: ActionSpec(
            kind=ActionKind.COMPLETE_TASK,
            intent=Intent.TASK_MANAGE,
            schema=TaskStatusChange,
            permission=Permission.TASKS_WRITE,
            service="TaskService",
            module="app.services.task_service",
            entrypoint="set_status",
        ),
        ActionKind.SET_TASK_STATUS: ActionSpec(
            kind=ActionKind.SET_TASK_STATUS,
            intent=Intent.TASK_MANAGE,
            schema=TaskStatusChange,
            permission=Permission.TASKS_WRITE,
            service="TaskService",
            module="app.services.task_service",
            entrypoint="set_status",
        ),
        ActionKind.SCHEDULE_TASK: ActionSpec(
            kind=ActionKind.SCHEDULE_TASK,
            intent=Intent.TASK_MANAGE,
            schema=TaskScheduleWrite,
            permission=Permission.TASKS_WRITE,
            service="TaskService",
            module="app.services.task_service",
            entrypoint="schedule",
            also_intents=frozenset({Intent.SCHEDULE_PLAN}),
        ),
        ActionKind.UNSCHEDULE_TASK: ActionSpec(
            kind=ActionKind.UNSCHEDULE_TASK,
            intent=Intent.TASK_MANAGE,
            schema=ActionAck,
            permission=Permission.TASKS_WRITE,
            service="TaskService",
            module="app.services.task_service",
            entrypoint="unschedule",
            also_intents=frozenset({Intent.SCHEDULE_PLAN}),
        ),
        ActionKind.TAG_TASK: ActionSpec(
            kind=ActionKind.TAG_TASK,
            intent=Intent.TASK_MANAGE,
            schema=ActionAck,
            permission=Permission.TASKS_WRITE,
            service="TaskService",
            module="app.services.task_service",
            entrypoint="add_tag",
        ),
        ActionKind.UNTAG_TASK: ActionSpec(
            kind=ActionKind.UNTAG_TASK,
            intent=Intent.TASK_MANAGE,
            schema=ActionAck,
            permission=Permission.TASKS_WRITE,
            service="TaskService",
            module="app.services.task_service",
            entrypoint="remove_tag",
        ),
        # --- projects ---------------------------------------------------- #
        ActionKind.CREATE_PROJECT: ActionSpec(
            kind=ActionKind.CREATE_PROJECT,
            intent=Intent.PROJECT_MANAGE,
            schema=ProjectCreate,
            permission=Permission.PROJECTS_WRITE,
            service="ProjectService",
            module="app.services.project_service",
            entrypoint="create",
            title_field="name",
            date_field="target_date",
        ),
        ActionKind.UPDATE_PROJECT: ActionSpec(
            kind=ActionKind.UPDATE_PROJECT,
            intent=Intent.PROJECT_MANAGE,
            schema=ProjectUpdate,
            permission=Permission.PROJECTS_WRITE,
            service="ProjectService",
            module="app.services.project_service",
            entrypoint="update",
            title_field="name",
            date_field="target_date",
        ),
        ActionKind.DELETE_PROJECT: ActionSpec(
            kind=ActionKind.DELETE_PROJECT,
            intent=Intent.PROJECT_MANAGE,
            schema=ActionAck,
            permission=Permission.PROJECTS_WRITE,
            service="ProjectService",
            module="app.services.project_service",
            entrypoint="delete",
            destructive=True,
        ),
        ActionKind.SET_PROJECT_STATUS: ActionSpec(
            kind=ActionKind.SET_PROJECT_STATUS,
            intent=Intent.PROJECT_MANAGE,
            schema=ProjectStatusWrite,
            permission=Permission.PROJECTS_WRITE,
            service="ProjectService",
            module="app.services.project_service",
            entrypoint="set_status",
        ),
        # --- knowledge --------------------------------------------------- #
        ActionKind.CREATE_NOTE: ActionSpec(
            kind=ActionKind.CREATE_NOTE,
            intent=Intent.KNOWLEDGE_CAPTURE,
            schema=NoteCreate,
            permission=Permission.KNOWLEDGE_WRITE,
            service="KnowledgeService",
            module="app.services.knowledge_service",
            entrypoint="create_note",
            title_field="title",
            also_intents=frozenset({Intent.KNOWLEDGE_LOOKUP}),
        ),
        ActionKind.UPDATE_NOTE: ActionSpec(
            kind=ActionKind.UPDATE_NOTE,
            intent=Intent.KNOWLEDGE_CAPTURE,
            schema=NoteUpdate,
            permission=Permission.KNOWLEDGE_WRITE,
            service="KnowledgeService",
            module="app.services.knowledge_service",
            entrypoint="update_note",
            title_field="title",
            also_intents=frozenset({Intent.KNOWLEDGE_LOOKUP}),
        ),
        ActionKind.DELETE_NOTE: ActionSpec(
            kind=ActionKind.DELETE_NOTE,
            intent=Intent.KNOWLEDGE_CAPTURE,
            schema=ActionAck,
            permission=Permission.KNOWLEDGE_WRITE,
            service="KnowledgeService",
            module="app.services.knowledge_service",
            entrypoint="delete_note",
            also_intents=frozenset({Intent.KNOWLEDGE_LOOKUP}),
            destructive=True,
        ),
        ActionKind.ARCHIVE_NOTE: ActionSpec(
            kind=ActionKind.ARCHIVE_NOTE,
            intent=Intent.KNOWLEDGE_CAPTURE,
            schema=ActionAck,
            permission=Permission.KNOWLEDGE_WRITE,
            service="KnowledgeService",
            module="app.services.knowledge_service",
            entrypoint="archive_note",
            also_intents=frozenset({Intent.KNOWLEDGE_LOOKUP}),
        ),
        ActionKind.PUBLISH_NOTE: ActionSpec(
            kind=ActionKind.PUBLISH_NOTE,
            intent=Intent.KNOWLEDGE_CAPTURE,
            schema=ActionAck,
            permission=Permission.KNOWLEDGE_WRITE,
            service="KnowledgeService",
            module="app.services.knowledge_service",
            entrypoint="publish_note",
            also_intents=frozenset({Intent.KNOWLEDGE_LOOKUP}),
        ),
        ActionKind.CREATE_BOOKMARK: ActionSpec(
            kind=ActionKind.CREATE_BOOKMARK,
            intent=Intent.KNOWLEDGE_CAPTURE,
            schema=BookmarkCreate,
            permission=Permission.KNOWLEDGE_WRITE,
            service="KnowledgeService",
            module="app.services.knowledge_service",
            entrypoint="create_bookmark",
            title_field="url",
            also_intents=frozenset({Intent.KNOWLEDGE_LOOKUP}),
        ),
        ActionKind.DELETE_BOOKMARK: ActionSpec(
            kind=ActionKind.DELETE_BOOKMARK,
            intent=Intent.KNOWLEDGE_CAPTURE,
            schema=ActionAck,
            permission=Permission.KNOWLEDGE_WRITE,
            service="KnowledgeService",
            module="app.services.knowledge_service",
            entrypoint="delete_bookmark",
            also_intents=frozenset({Intent.KNOWLEDGE_LOOKUP}),
            destructive=True,
        ),
        ActionKind.CREATE_CONCEPT: ActionSpec(
            kind=ActionKind.CREATE_CONCEPT,
            intent=Intent.KNOWLEDGE_CAPTURE,
            schema=ConceptCreate,
            permission=Permission.KNOWLEDGE_WRITE,
            service="KnowledgeService",
            module="app.services.knowledge_service",
            entrypoint="create_concept",
            title_field="name",
            also_intents=frozenset({Intent.KNOWLEDGE_LOOKUP}),
        ),
        ActionKind.DELETE_CONCEPT: ActionSpec(
            kind=ActionKind.DELETE_CONCEPT,
            intent=Intent.KNOWLEDGE_CAPTURE,
            schema=ActionAck,
            permission=Permission.KNOWLEDGE_WRITE,
            service="KnowledgeService",
            module="app.services.knowledge_service",
            entrypoint="delete_concept",
            also_intents=frozenset({Intent.KNOWLEDGE_LOOKUP}),
            destructive=True,
        ),
        ActionKind.CREATE_LINK: ActionSpec(
            kind=ActionKind.CREATE_LINK,
            intent=Intent.KNOWLEDGE_CAPTURE,
            schema=KnowledgeLinkCreate,
            permission=Permission.KNOWLEDGE_WRITE,
            service="KnowledgeService",
            module="app.services.knowledge_service",
            entrypoint="create_link",
            title_field=None,
            also_intents=frozenset({Intent.KNOWLEDGE_LOOKUP}),
        ),
        ActionKind.DELETE_LINK: ActionSpec(
            kind=ActionKind.DELETE_LINK,
            intent=Intent.KNOWLEDGE_CAPTURE,
            schema=ActionAck,
            permission=Permission.KNOWLEDGE_WRITE,
            service="KnowledgeService",
            module="app.services.knowledge_service",
            entrypoint="delete_link",
            also_intents=frozenset({Intent.KNOWLEDGE_LOOKUP}),
            destructive=True,
        ),
        # --- learning ---------------------------------------------------- #
        ActionKind.CREATE_LEARNING_GOAL: ActionSpec(
            kind=ActionKind.CREATE_LEARNING_GOAL,
            intent=Intent.LEARNING_TRACK,
            schema=LearningGoalWrite,
            permission=Permission.ANALYTICS_READ,
            service="LearningIntelligenceService",
            module="app.services.learning",
            entrypoint="create_goal",
            title_field="title",
            date_field="target_date",
        ),
        ActionKind.UPDATE_LEARNING_GOAL: ActionSpec(
            kind=ActionKind.UPDATE_LEARNING_GOAL,
            intent=Intent.LEARNING_TRACK,
            schema=LearningGoalWrite,
            permission=Permission.ANALYTICS_READ,
            service="LearningIntelligenceService",
            module="app.services.learning",
            entrypoint="update_goal",
            title_field="title",
            date_field="target_date",
        ),
        ActionKind.COMPLETE_LEARNING_GOAL: ActionSpec(
            kind=ActionKind.COMPLETE_LEARNING_GOAL,
            intent=Intent.LEARNING_TRACK,
            schema=ActionAck,
            permission=Permission.ANALYTICS_READ,
            service="LearningIntelligenceService",
            module="app.services.learning",
            entrypoint="complete_goal",
        ),
        ActionKind.DELETE_LEARNING_GOAL: ActionSpec(
            kind=ActionKind.DELETE_LEARNING_GOAL,
            intent=Intent.LEARNING_TRACK,
            schema=ActionAck,
            permission=Permission.ANALYTICS_READ,
            service="LearningIntelligenceService",
            module="app.services.learning",
            entrypoint="delete_goal",
            destructive=True,
        ),
        ActionKind.CREATE_SKILL: ActionSpec(
            kind=ActionKind.CREATE_SKILL,
            intent=Intent.LEARNING_TRACK,
            schema=SkillWrite,
            permission=Permission.ANALYTICS_READ,
            service="LearningIntelligenceService",
            module="app.services.learning",
            entrypoint="create_skill",
            title_field="name",
        ),
        ActionKind.DELETE_SKILL: ActionSpec(
            kind=ActionKind.DELETE_SKILL,
            intent=Intent.LEARNING_TRACK,
            schema=ActionAck,
            permission=Permission.ANALYTICS_READ,
            service="LearningIntelligenceService",
            module="app.services.learning",
            entrypoint="delete_skill",
            destructive=True,
        ),
        # --- planner ---------------------------------------------------- #
        ActionKind.CREATE_EVENT: ActionSpec(
            kind=ActionKind.CREATE_EVENT,
            intent=Intent.SCHEDULE_PLAN,
            schema=CalendarEventCreate,
            permission=Permission.CALENDAR_WRITE,
            service="PlannerService",
            module="app.services.planner_service",
            entrypoint="create_event",
            title_field="title",
        ),
        ActionKind.UPDATE_EVENT: ActionSpec(
            kind=ActionKind.UPDATE_EVENT,
            intent=Intent.SCHEDULE_PLAN,
            schema=CalendarEventUpdate,
            permission=Permission.CALENDAR_WRITE,
            service="PlannerService",
            module="app.services.planner_service",
            entrypoint="update_event",
            title_field="title",
        ),
        ActionKind.DELETE_EVENT: ActionSpec(
            kind=ActionKind.DELETE_EVENT,
            intent=Intent.SCHEDULE_PLAN,
            schema=ActionAck,
            permission=Permission.CALENDAR_WRITE,
            service="PlannerService",
            module="app.services.planner_service",
            entrypoint="delete_event",
            destructive=True,
        ),
        ActionKind.CREATE_SESSION: ActionSpec(
            kind=ActionKind.CREATE_SESSION,
            intent=Intent.SCHEDULE_PLAN,
            schema=WorkSessionCreate,
            permission=Permission.CALENDAR_WRITE,
            service="PlannerService",
            module="app.services.planner_service",
            entrypoint="create_session",
            title_field=None,
        ),
        ActionKind.DELETE_SESSION: ActionSpec(
            kind=ActionKind.DELETE_SESSION,
            intent=Intent.SCHEDULE_PLAN,
            schema=ActionAck,
            permission=Permission.CALENDAR_WRITE,
            service="PlannerService",
            module="app.services.planner_service",
            entrypoint="delete_session",
            destructive=True,
        ),
        # --- developer intelligence --------------------------------- #
        # A repository is named by a **path**, not by a title, which is the one
        # kind here whose payload cannot be built from :attr:`Extraction.title`
        # alone. ``Permission.ANALYTICS_READ`` is the capability
        # ``POST /developer/repositories`` is already gated with, and
        # ``register_repository`` is the service method that proves the folder is
        # a git work tree **before** storing a row — the reason a path is never
        # guessed here either.
        ActionKind.CREATE_REPOSITORY: ActionSpec(
            kind=ActionKind.CREATE_REPOSITORY,
            intent=Intent.DEVELOPER_INTEL,
            schema=RepositoryCreate,
            permission=Permission.ANALYTICS_READ,
            service="DeveloperIntelligenceService",
            module="app.services.developer",
            entrypoint="register_repository",
            title_field="name",
            date_field=None,
        ),
        # The counterpart, and the widest cascade on the developer surface:
        # ``delete_repository`` drops the row **and** its commits, branches and
        # scan runs through the schema's ``ON DELETE CASCADE``, so the row it
        # leaves behind is not a quieter record of the same repository — it is no
        # record at all. The same ``Permission.ANALYTICS_READ`` is used because
        # this is the capability ``DELETE /developer/repositories/{id}`` is already
        # gated on; the destructive flag and the second press, not a wider one, are
        # what this kind adds.
        ActionKind.DELETE_REPOSITORY: ActionSpec(
            kind=ActionKind.DELETE_REPOSITORY,
            intent=Intent.DEVELOPER_INTEL,
            schema=ActionAck,
            permission=Permission.ANALYTICS_READ,
            service="DeveloperIntelligenceService",
            module="app.services.developer",
            entrypoint="delete_repository",
            title_field=None,
            date_field=None,
            destructive=True,
        ),
        # --- account ---------------------------------------------------- #
        ActionKind.UPDATE_PROFILE: ActionSpec(
            kind=ActionKind.UPDATE_PROFILE,
            intent=Intent.ACCOUNT_ADMIN,
            schema=UserUpdate,
            permission=Permission.USERS_WRITE,
            service="UserService",
            module="app.services.user_service",
            entrypoint="update",
            title_field=None,
        ),
    }
)

# --------------------------------------------------------------------------- #
# Routing: (intent, verb, entity) → spec
# --------------------------------------------------------------------------- #

#: Which spec answers a given ``(intent, verb, entity)``. The three keys are all
#: required, and each one rules out a different guess:
#:
#: *the intent* is what the classifier gave — it names the surface, never the verb;
#: *the verb* is what :func:`~app.ml.actions.extraction.extract_verb` could read
#:   off the text, so "delete" and "add" are distinguishable even though the
#:   classifier cannot tell them apart;
#: *the entity* is the noun the user actually named, because "delete the bookmark"
#:   and "delete the note" are both ``knowledge_capture`` and only one of them is
#:   about the row the user has open.
#:
#: Read left to right, this is also the reason there is no key without an entity:
#: an entity-less action is exactly the guess this layer exists to refuse.
_ROUTES: tuple[tuple[ActionKind, str, str], ...] = (
    (ActionKind.CREATE_TASK, ExtractedVerb.CREATE, "task"),
    (ActionKind.UPDATE_TASK, ExtractedVerb.UPDATE, "task"),
    (ActionKind.DELETE_TASK, ExtractedVerb.DELETE, "task"),
    (ActionKind.COMPLETE_TASK, ExtractedVerb.COMPLETE, "task"),
    (ActionKind.SET_TASK_STATUS, ExtractedVerb.STATUS, "task"),
    (ActionKind.SCHEDULE_TASK, ExtractedVerb.SCHEDULE, "task"),
    (ActionKind.UNSCHEDULE_TASK, ExtractedVerb.UNSCHEDULE, "task"),
    (ActionKind.TAG_TASK, ExtractedVerb.TAG, "task"),
    (ActionKind.UNTAG_TASK, ExtractedVerb.UNTAG, "task"),
    (ActionKind.CREATE_PROJECT, ExtractedVerb.CREATE, "project"),
    (ActionKind.UPDATE_PROJECT, ExtractedVerb.UPDATE, "project"),
    (ActionKind.DELETE_PROJECT, ExtractedVerb.DELETE, "project"),
    (ActionKind.SET_PROJECT_STATUS, ExtractedVerb.STATUS, "project"),
    (ActionKind.CREATE_NOTE, ExtractedVerb.CREATE, "note"),
    (ActionKind.UPDATE_NOTE, ExtractedVerb.UPDATE, "note"),
    (ActionKind.DELETE_NOTE, ExtractedVerb.DELETE, "note"),
    (ActionKind.ARCHIVE_NOTE, ExtractedVerb.ARCHIVE, "note"),
    (ActionKind.PUBLISH_NOTE, ExtractedVerb.PUBLISH, "note"),
    (ActionKind.CREATE_BOOKMARK, ExtractedVerb.CREATE, "bookmark"),
    (ActionKind.DELETE_BOOKMARK, ExtractedVerb.DELETE, "bookmark"),
    (ActionKind.CREATE_CONCEPT, ExtractedVerb.CREATE, "concept"),
    (ActionKind.DELETE_CONCEPT, ExtractedVerb.DELETE, "concept"),
    (ActionKind.CREATE_LINK, ExtractedVerb.CREATE, "link"),
    (ActionKind.DELETE_LINK, ExtractedVerb.DELETE, "link"),
    (ActionKind.CREATE_LEARNING_GOAL, ExtractedVerb.CREATE, "goal"),
    (ActionKind.UPDATE_LEARNING_GOAL, ExtractedVerb.UPDATE, "goal"),
    (ActionKind.COMPLETE_LEARNING_GOAL, ExtractedVerb.COMPLETE, "goal"),
    (ActionKind.DELETE_LEARNING_GOAL, ExtractedVerb.DELETE, "goal"),
    (ActionKind.CREATE_SKILL, ExtractedVerb.CREATE, "skill"),
    (ActionKind.DELETE_SKILL, ExtractedVerb.DELETE, "skill"),
    (ActionKind.CREATE_EVENT, ExtractedVerb.CREATE, "event"),
    (ActionKind.UPDATE_EVENT, ExtractedVerb.UPDATE, "event"),
    (ActionKind.DELETE_EVENT, ExtractedVerb.DELETE, "event"),
    (ActionKind.CREATE_SESSION, ExtractedVerb.LOG, "session"),
    (ActionKind.DELETE_SESSION, ExtractedVerb.DELETE, "session"),
    (ActionKind.CREATE_REPOSITORY, ExtractedVerb.CREATE, "repository"),
    (ActionKind.DELETE_REPOSITORY, ExtractedVerb.DELETE, "repository"),
    (ActionKind.UPDATE_PROFILE, ExtractedVerb.UPDATE, "profile"),
)


def _routing_table() -> Mapping[tuple[str, str, str], ActionSpec]:
    """Index :data:`ACTION_SPECS` by every intent a request of this kind may carry.

    Each entry is registered under its primary intent **and** under every
    :attr:`ActionSpec.also_intents` member, because the classifier's label is the
    one the confirm route has to re-check and the one that may legitimately differ
    from the spec's. A collision would mean two kinds claiming the same utterance,
    so it is raised rather than resolved: a duplicate key is a defect in this
    table, not a thing to pick a winner for.
    """
    table: dict[tuple[str, str, str], ActionSpec] = {}
    for kind, verb, entity in _ROUTES:
        spec = ACTION_SPECS[kind]
        for intent in (spec.intent, *sorted(spec.also_intents)):
            key = (str(intent), verb, entity)
            if key in table:
                raise ValueError(f"Two action kinds claim {key}: {table[key].kind} and {kind}.")
            table[key] = spec
    return MappingProxyType(table)


_SPEC_BY_INTENT_VERB_ENTITY: Mapping[tuple[str, str, str], ActionSpec] = _routing_table()

#: The verbs each intent can justify at all. Read when a lookup misses, to tell
#: "the text does not say what to do" from "it does, but not to that row type".
_VERBS_BY_INTENT: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        intent: frozenset(
            verb for candidate, verb, _entity in _SPEC_BY_INTENT_VERB_ENTITY if candidate == intent
        )
        for intent in {key[0] for key in _SPEC_BY_INTENT_VERB_ENTITY}
    }
)

#: The row types one ``(intent, verb)`` pair acts on, for the same distinction.
_ENTITIES_BY_INTENT_VERB: Mapping[tuple[str, str], frozenset[str]] = MappingProxyType(
    {
        key: frozenset(
            entity
            for candidate, verb, entity in _SPEC_BY_INTENT_VERB_ENTITY
            if candidate == key[0] and verb == key[1]
        )
        for key in _SPEC_BY_INTENT_VERB_ENTITY
    }
)

#: What a request is about when it does not say. ``knowledge_capture`` defaults
#: to a note because that is what "write this down" has meant in this app since
#: Phase 5; ``schedule_plan`` defaults to an event because a plan without a noun
#: is a meeting far more often than it is a logged session;
#: ``developer_intel`` defaults to a repository because the only thing this layer
#: writes on that surface is one, and a developer request that names no noun is
#: still about the folder on the user's disk.
_DEFAULT_ENTITY_BY_INTENT: Mapping[str, str] = MappingProxyType(
    {
        str(Intent.TASK_MANAGE): "task",
        str(Intent.PROJECT_MANAGE): "project",
        str(Intent.KNOWLEDGE_CAPTURE): "note",
        str(Intent.KNOWLEDGE_LOOKUP): "note",
        str(Intent.LEARNING_TRACK): "goal",
        str(Intent.SCHEDULE_PLAN): "event",
        str(Intent.DEVELOPER_INTEL): "repository",
        str(Intent.ACCOUNT_ADMIN): "profile",
    }
)

#: Every entity string the routes know. A request naming anything else cannot be
#: routed, and the refusal says so rather than falling back to the default.
_KNOWN_ENTITIES: frozenset[str] = frozenset(entity for _kind, _verb, entity in _ROUTES)

#: The intents that have at least one proposal kind behind them, derived from the
#: table rather than listed — so a kind added without an intent, or an intent
#: added without a kind, shows up here as a mismatch rather than as a silent
#: ``unsupported_intent`` for a surface that does have actions.
_PROPOSABLE_INTENTS: frozenset[str] = frozenset(
    str(intent) for spec in ACTION_SPECS.values() for intent in (spec.intent, *spec.also_intents)
)

#: Which kinds invent a row and which act on one the caller already owns. A
#: creation has nothing to resolve; every other kind has to name exactly one row
#: before a payload exists, which is where :func:`match_reference` enters.
_CREATE_KINDS: frozenset[ActionKind] = frozenset(
    kind for kind, verb, _entity in _ROUTES if verb in (ExtractedVerb.CREATE, ExtractedVerb.LOG)
)

#: The kinds that write a *new* value onto a named row. Split out because the
#: title they carry is the row's new name, while on every other kind the title is
#: the reference the matcher consumes — the same string means opposite things
#: depending on the verb, and conflating them would rename a row the user only
#: asked to delete.
_UPDATE_KINDS: frozenset[ActionKind] = frozenset(
    kind for kind, verb, _entity in _ROUTES if verb == ExtractedVerb.UPDATE
)

#: The kinds that name a row the caller already owns. Every non-creation does,
#: an update included: it acts on a stored row, so its id has to be resolved and
#: re-resolved at confirm time exactly like a delete's. ``update_profile`` is the
#: one exclusion, because the row is the caller and the caller is already known.
_TARGET_KINDS: frozenset[ActionKind] = (
    frozenset(ACTION_SPECS) - _CREATE_KINDS - {ActionKind.UPDATE_PROFILE}
)

#: The kinds whose effect cannot be walked back from the activity feed. Derived
#: from the table rather than listed, so the sentence appended to a delete and the
#: flag the confirm route refuses on cannot disagree.
_DESTRUCTIVE_KINDS: frozenset[ActionKind] = frozenset(
    spec.kind for spec in ACTION_SPECS.values() if spec.destructive
)

_PRIORITY_ADJECTIVES: Mapping[str, str] = MappingProxyType(
    {
        "high": "high-priority",
        "medium": "medium-priority",
        "low": "low-priority",
        "critical": "critical-priority",
    }
)

_WEEKDAY_NAMES: tuple[str, ...] = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)

#: The default zone. A module constant rather than a ``ZoneInfo("UTC")`` call in
#: the dataclass default: ``ZoneInfo`` is an immutable cached singleton, so the
#: same object is safe to share and ``ruff``'s RUF009 is right that calling it
#: per instantiation buys nothing.
_UTC = ZoneInfo("UTC")


# --------------------------------------------------------------------------- #
# Callers' rows
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ProposalContext:
    """What only the caller knows: the zone, the clock, the project, the rows.

    None of this is guessable from an utterance. A task belongs to a project, and
    every action that is not a creation names a row the user already owns; both
    arrive here, already owner-scoped by whoever read them, so this module never
    touches the database.

    One candidate list per row type, all the same shape. They are separate
    members rather than one tagged list because a request names *one* row: giving
    a note's title to the task matcher would produce matches that are correct
    against the wrong table, and a delete that lands on a row the user did not
    name is the failure this layer is built to avoid.
    """

    tz: ZoneInfo = _UTC
    now: datetime | None = None
    project_id: UUID | None = None
    project_label: str | None = None
    task_candidates: tuple[RowCandidate, ...] = ()
    project_candidates: tuple[RowCandidate, ...] = ()
    note_candidates: tuple[RowCandidate, ...] = ()
    bookmark_candidates: tuple[RowCandidate, ...] = ()
    concept_candidates: tuple[RowCandidate, ...] = ()
    link_candidates: tuple[RowCandidate, ...] = ()
    goal_candidates: tuple[RowCandidate, ...] = ()
    skill_candidates: tuple[RowCandidate, ...] = ()
    event_candidates: tuple[RowCandidate, ...] = ()
    session_candidates: tuple[RowCandidate, ...] = ()
    repository_candidates: tuple[RowCandidate, ...] = ()


#: Which candidate list belongs to which entity. The value is the attribute name
#: on :class:`ProposalContext`, read rather than hard-coded at each call site so
#: that adding a row type is one entry here and one member on the context.
_CANDIDATE_FIELD_BY_ENTITY: Mapping[str, str] = MappingProxyType(
    {
        "task": "task_candidates",
        "project": "project_candidates",
        "note": "note_candidates",
        "bookmark": "bookmark_candidates",
        "concept": "concept_candidates",
        "link": "link_candidates",
        "goal": "goal_candidates",
        "skill": "skill_candidates",
        "event": "event_candidates",
        "session": "session_candidates",
        "repository": "repository_candidates",
    }
)

#: How a row type is named in the confirm sentence. Separate from the key above
#: because the sentence wants English ("work session") while the lookup wants the
#: attribute ("session_candidates").
_ENTITY_WORDS: Mapping[str, str] = MappingProxyType(
    {
        "task": "task",
        "project": "project",
        "note": "note",
        "bookmark": "bookmark",
        "concept": "concept",
        "link": "link",
        "goal": "learning goal",
        "skill": "skill",
        "event": "calendar event",
        "session": "work session",
        "repository": "repository",
        "profile": "profile",
    }
)

#: The endpoint pools a ``create_link`` may draw its two ends from.
#: :class:`~app.models.enums.KnowledgeEntityType` has exactly three members and
#: the third — a resource — has no candidate list in this contract, so an edge to
#: one is a refusal rather than a guessed endpoint.
_LINK_POOLS: tuple[tuple[KnowledgeEntityType, str], ...] = (
    (KnowledgeEntityType.NOTE, "note_candidates"),
    (KnowledgeEntityType.CONCEPT, "concept_candidates"),
)


@dataclass(frozen=True, slots=True)
class ActionProposal:
    """One fully populated action the user is being asked to confirm.

    Carries the payload (:attr:`payload`), the provenance of every extracted
    field (:attr:`arguments`), the capability it needs (:attr:`permission`), the
    row it acts on or belongs to (:attr:`target_id`), and the one sentence the
    user checks (:attr:`summary`). It carries **no** handle to call: a proposal
    names the service, module and entry point, and the caller — which owns the
    session, the transaction and the user's decision — makes that call.

    :attr:`destructive` is copied from the spec rather than derived here, so the
    flag the dialog renders and the flag the confirm route refuses are the same
    value read from the same table row.
    """

    kind: ActionKind
    intent: str
    confidence: float
    payload: BaseModel
    arguments: tuple[Argument, ...]
    permission: Permission
    summary: str
    spec: ActionSpec
    notes: tuple[str, ...] = ()
    target_id: UUID | None = None
    target_label: str | None = None
    destructive: bool = False

    @property
    def requires_confirmation(self) -> bool:
        """Always ``True``, and not settable — see this module's docstring."""
        return True

    def to_dict(self) -> dict[str, Any]:
        """The proposal as a JSON-ready mapping, dates and ids rendered as text."""
        return {
            "kind": str(self.kind),
            "intent": self.intent,
            "confidence": round(self.confidence, 6),
            "summary": self.summary,
            "requires_confirmation": self.requires_confirmation,
            "destructive": self.destructive,
            "permission": str(self.permission),
            "service": self.spec.service,
            "module": self.spec.module,
            "entrypoint": self.spec.entrypoint,
            "schema": self.spec.schema.__name__,
            "payload": _payload_to_dict(self.payload),
            "target_id": str(self.target_id) if self.target_id else None,
            "target_label": self.target_label,
            "arguments": [argument.to_dict() for argument in self.arguments],
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class ProposalRefusal:
    """No proposal, and exactly why.

    A refusal is a first-class answer with its own reason code, not an absence.
    The alternative — returning ``None`` and letting the caller guess between
    "NEXUS did not understand", "NEXUS refused on purpose" and "NEXUS has a bug" —
    is exactly the flat refusal :mod:`app.ml.router` was written to stop doing.
    """

    intent: str
    confidence: float
    reason_code: str
    reason: str
    kind: ActionKind | None = None
    arguments: tuple[Argument, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def requires_confirmation(self) -> bool:
        """Always ``True``.

        A refusal has nothing to confirm, and is never something a caller may
        quietly act on.
        """
        return True

    @property
    def destructive(self) -> bool:
        """Always ``False``. Nothing was proposed, so nothing can be destructive."""
        return False

    def to_dict(self) -> dict[str, Any]:
        """The refusal as a JSON-ready mapping."""
        return {
            "kind": str(self.kind) if self.kind else None,
            "intent": self.intent,
            "confidence": round(self.confidence, 6),
            "reason_code": self.reason_code,
            "reason": self.reason,
            "requires_confirmation": self.requires_confirmation,
            "destructive": self.destructive,
            "arguments": [argument.to_dict() for argument in self.arguments],
            "notes": list(self.notes),
        }


def is_proposal(outcome: ActionProposal | ProposalRefusal) -> bool:
    """Whether an outcome is actionable.

    The caller's one branch, so that "did NEXUS propose something?" is a single
    predicate rather than an ``isinstance`` scattered across route handlers.
    """
    return isinstance(outcome, ActionProposal)


def render_summary(proposal: ActionProposal) -> str:
    """The one sentence a user checks before agreeing.

    The sentence must contain the **whole** effect: what will be created or
    changed, what it will be called, its priority, its date, and where it lives.
    A confirm dialog the user has to cross-reference against a form is a confirm
    dialog nobody reads properly. A delete adds the one clause it cannot borrow
    from the others — that it cannot be undone — because that clause is the whole
    reason it takes a second press.

    Returns:
        :attr:`ActionProposal.summary`, which is built once at proposal time so
        the sentence a caller stores is the sentence the user saw.
    """
    return proposal.summary


# --------------------------------------------------------------------------- #
# Summary rendering
# --------------------------------------------------------------------------- #


#: How each kind names its date field in prose. A project's date is a *target*,
#: not a *due* date — the column is ``target_date`` and calling it "due" in the
#: sentence would describe an obligation the user never accepted.
_DATE_WORDING: Mapping[ActionKind, str] = MappingProxyType(
    {
        ActionKind.CREATE_TASK: "due",
        ActionKind.UPDATE_TASK: "due",
        ActionKind.CREATE_PROJECT: "targeting",
        ActionKind.UPDATE_PROJECT: "targeting",
        ActionKind.CREATE_LEARNING_GOAL: "targeting",
        ActionKind.UPDATE_LEARNING_GOAL: "targeting",
    }
)

#: How a payload field is named in the sentence for a multi-field update.
#: Anything absent falls back to the field name with underscores spaced out, which
#: is the right answer for every field this table has not needed to rename.
_FIELD_WORDS: Mapping[str, str] = MappingProxyType(
    {
        "title": "title",
        "name": "name",
        "priority": "priority",
        "due_date": "due date",
        "start_date": "start date",
        "target_date": "target date",
        "description": "description",
        "location": "location",
        "event_type": "type",
        "display_name": "display name",
        "avatar_url": "avatar",
        "username": "username",
    }
)

#: The clause appended to every delete. One sentence's worth of honesty that the
#: payload cannot carry and the service cannot undo.
_IRREVERSIBLE_CLAUSE = " This cannot be undone."

#: The field names a reference can arrive under. On a delete or a transition the
#: "title" is the fragment :func:`match_reference` just consumed, not a value the
#: payload is being asked to write, so these are read out of
#: :attr:`Extraction.values` rather than carried into it.
_TITLE_KEYS: frozenset[str] = frozenset({"title", "name", "url"})

#: The stated keys a payload places somewhere its own schema does not have a
#: column for. A link is written as four typed ids resolved from two spoken
#: references, so ``source`` and ``target`` are consumed rather than refused as
#: fields nothing can hold. An event and a session take a day, which the extractor
#: reports as ``due_date`` because that is the column it belongs to everywhere
#: else; here it is the window's start. A repository is spoken as ``path`` and
#: stored as ``local_path``, which is the one rename between an utterance and a
#: column anywhere in this table.
_EXTRA_VALUE_KEYS: Mapping[ActionKind, frozenset[str]] = MappingProxyType(
    {
        ActionKind.CREATE_LINK: frozenset({"source", "target", "link_type"}),
        ActionKind.CREATE_EVENT: frozenset({"due_date"}),
        ActionKind.CREATE_SESSION: frozenset({"due_date"}),
        ActionKind.CREATE_REPOSITORY: frozenset({"path"}),
    }
)

#: Words a :class:`ActionAck` payload cannot hold and does not need to. An
#: :class:`ActionAck` kind's whole effect is its verb and the row it names, so a
#: date on "delete the overdue task", a grade on "archive the low-priority note"
#: and a state on "mark the draft as archived" are context rather than a change
#: being dropped — the sentence already reports the verb, and the verb is the
#: entire effect. Refusing these would teach the user the assistant cannot delete
#: anything it was also given a date for.
_ACTION_ACK_CONTEXT_KEYS: frozenset[str] = frozenset(
    {"due_date", "start_date", "target_date", "priority", "status", "note", "reason"}
)


def _format_due(due_date: date, today: date, wording: str = "due") -> str:
    """Render a date the way a person would say it, relative when it is near.

    Within the coming week the weekday name is what the user said and what they
    will recognise; further out an ISO date is unambiguous where "next Friday"
    would not be. Deliberately **not** a relative phrase ("in three days"): the
    summary is read at a moment that may not be the moment the proposal is
    confirmed, and a sentence whose dates move while it is on screen is a
    sentence nobody can check.
    """
    delta = (due_date - today).days
    if 0 <= delta <= 6:
        return f"{wording} {_WEEKDAY_NAMES[due_date.weekday()]}"
    return f"{wording} {due_date.isoformat()}"


def _format_window(starts_at: datetime, ends_at: datetime, tz: ZoneInfo) -> str:
    """Render a clock window in the caller's zone.

    Both ends are named, including an end this layer assumed, because a calendar
    entry whose duration was invented in silence is a row the user will believe
    they chose.
    """
    local_start = starts_at.astimezone(tz)
    local_end = ends_at.astimezone(tz)
    if local_start.date() == local_end.date():
        return (
            f"from {_WEEKDAY_NAMES[local_start.weekday()]} {local_start:%H:%M} to {local_end:%H:%M}"
        )
    return f"from {local_start.isoformat()} to {local_end.isoformat()}"


def _describe_target(label: str | None, identifier: UUID | None) -> str:
    """The "in project …" clause, preferring the caller's own name for the row."""
    if label:
        return f"in project '{label}'"
    if identifier is not None:
        return f"in project {identifier}"
    return ""


def _field_word(name: str) -> str:
    """The prose name of a payload field."""
    return _FIELD_WORDS.get(name, name.replace("_", " "))


def _render_value(value: Any) -> str:
    """Render one payload value the way the confirm sentence should read it.

    Enums come out in the words a person uses (``in progress``, not
    ``in_progress``) and dates as ISO, because a date the sentence shows is a
    date the user can check against the one they said.
    """
    if isinstance(value, StrEnum):
        return str(value).replace("_", " ")
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _changed_fields(payload: BaseModel) -> tuple[str, ...]:
    """The payload's explicitly-set fields, in the schema's own declaration order.

    ``model_fields_set`` is a set, and a sentence listing a user's changes in a
    different order each time reads as a different sentence. Iterating the schema
    fixes the order without having to sort anything.
    """
    return tuple(name for name in payload.model_fields if name in payload.model_fields_set)


def _update_sentence(subject: str, label: str | None, payload: BaseModel) -> str:
    """One sentence for an update, naming the row and only what changed.

    A rename is called a rename — "rename the task 'API contract' to 'Auth
    contract'" — because that is the request, and a sentence that said "set title
    to 'Auth contract'" would describe the mechanism rather than the effect. Every
    other update lists the fields the user actually stated, because a PATCH that
    sends three of a model's eleven fields and a confirm sentence that describes
    eleven is the failure this module's whole design is about.

    Args:
        subject: The noun phrase the sentence is about, e.g. ``the task 'X'`` or
            ``your profile``.
        label: The row's stored name, used only to recognise a rename.
        payload: The validated payload, whose ``model_fields_set`` is the change.
    """
    changed = _changed_fields(payload)
    if changed in (("title",), ("name",)):
        new_name = _render_value(getattr(payload, changed[0]))
        if label and new_name != label:
            return f"Rename {subject} to '{new_name}'."
    parts = ", ".join(
        f"{_field_word(name)} to {_render_value(getattr(payload, name))}" for name in changed
    )
    return f"Change {subject}: set {parts}."


def _summarise(
    spec: ActionSpec,
    *,
    entity: str,
    label: str | None,
    payload: BaseModel,
    priority: str | None,
    due_date: date | None,
    today: date,
    tz: ZoneInfo,
    target_label: str | None,
    target_id: UUID | None,
    links: _LinkEndpoints | None,
) -> str:
    """Compose the confirm sentence for one proposal.

    Clauses are joined in the order a person states them — what, how urgent,
    when, where — and **absent fields are omitted rather than defaulted**: the
    sentence reports the request that was made, not the request plus the schema's
    defaults. ``label`` is the row's own stored name for anything that acts on an
    existing row and what was extracted for anything that creates one.
    """
    kind = spec.kind
    word = _ENTITY_WORDS.get(entity, "row")
    subject = f"the {word} '{label}'"

    # Every delete says the one thing the payload cannot: it is final. The clause
    # is identical for all of them on purpose — "this cannot be undone" is a
    # property of the table, not of which table it was.
    #
    # A repository is the one exception, and it is an exception of *scope* rather
    # than of kind. Deleting a task loses a card and deleting a project loses a
    # board, but this row owns its commits, its branches and every scan run ever
    # taken of it: the schema cascades, and there is no archive of the history
    # because there is no row left to hang it on. A sentence that said only
    # "this cannot be undone" would be true and would still understate what the
    # press does, so the cascade is named here rather than discovered afterwards
    # from a dashboard that no longer has the numbers.
    if kind is ActionKind.DELETE_REPOSITORY:
        return (
            f"Delete {subject}, along with its commits, branches and scan runs."
            f"{_IRREVERSIBLE_CLAUSE}"
        )
    if kind in _DESTRUCTIVE_KINDS:
        return f"Delete {subject}.{_IRREVERSIBLE_CLAUSE}"

    if kind is ActionKind.COMPLETE_TASK:
        return f"Mark {subject} as completed."
    if kind is ActionKind.SET_TASK_STATUS:
        return f"Mark {subject} as {_render_value(payload.status)}."
    if kind is ActionKind.SET_PROJECT_STATUS:
        return f"Mark {subject} as {_render_value(payload.status)}."
    if kind is ActionKind.COMPLETE_LEARNING_GOAL:
        return f"Complete {subject}."
    if kind is ActionKind.ARCHIVE_NOTE:
        return f"Archive {subject}."
    if kind is ActionKind.PUBLISH_NOTE:
        return f"Publish {subject}."
    if kind is ActionKind.UNSCHEDULE_TASK:
        return f"Unschedule {subject}."
    if kind is ActionKind.TAG_TASK:
        return f"Add the tag '{payload.tag}' to {subject}."
    if kind is ActionKind.UNTAG_TASK:
        return f"Remove the tag '{payload.tag}' from {subject}."
    if kind is ActionKind.SCHEDULE_TASK:
        clauses = [f"Schedule {subject} starting {payload.start_date.isoformat()}"]
        if payload.due_date is not None and payload.due_date != payload.start_date:
            clauses.append(f"and ending {payload.due_date.isoformat()}")
        return ", ".join(clauses) + "."

    if kind in _UPDATE_KINDS:
        if kind is ActionKind.UPDATE_PROFILE:
            return _update_sentence("your profile", None, payload)
        return _update_sentence(subject, label, payload)

    # --- creations ------------------------------------------------------- #
    if kind is ActionKind.CREATE_LINK:
        if links is None:  # pragma: no cover — the caller resolves both ends first
            return "Link two rows in the knowledge base."
        return (
            f"Link the {_ENTITY_WORDS[str(links.source.entity)]} '{links.source.label}' "
            f"to the {_ENTITY_WORDS[str(links.target.entity)]} '{links.target.label}'."
        )
    if kind is ActionKind.CREATE_EVENT:
        return (
            f"Add the calendar event '{label}', "
            f"{_format_window(payload.starts_at, payload.ends_at, tz)}."
        )
    if kind is ActionKind.CREATE_SESSION:
        return (
            "Log a work session, "
            f"{_format_window(payload.scheduled_start, payload.scheduled_end, tz)}."
        )
    if kind is ActionKind.CREATE_BOOKMARK:
        return f"Save the bookmark '{label}'."
    if kind is ActionKind.CREATE_CONCEPT:
        return f"Create the concept '{label}'."
    if kind is ActionKind.CREATE_SKILL:
        return f"Track the skill '{label}'."
    if kind is ActionKind.CREATE_REPOSITORY:
        # The path is in the sentence rather than implied, because it is the part
        # the user cannot guess NEXUS chose — and the second clause is the one
        # thing the payload cannot state: the folder is resolved, expanded and
        # checked as a git work tree **before** a row is written, and the user is
        # agreeing to exactly that, not to a scan.
        return (
            f"Register the repository '{label}' from {payload.local_path}. "
            "The folder is resolved and checked on the server before anything "
            "is stored, and no scan runs yet."
        )

    # A note has no priority field and so has no urgency to describe; every other
    # creation kind has one, and the payload is about to carry the value the user
    # asked for — a sentence that left it out would be checking a form the user
    # has to read the payload to complete.
    adjective = ""
    if "priority" in spec.schema.model_fields:
        adjective = _PRIORITY_ADJECTIVES.get(priority or "", "")
    prefix = f"{adjective} " if adjective else ""

    if kind is ActionKind.CREATE_NOTE:
        subject_text = f"Save a note titled '{label}'"
    elif kind is ActionKind.CREATE_PROJECT:
        subject_text = f"Create a {prefix}project named '{label}'"
    elif kind is ActionKind.CREATE_LEARNING_GOAL:
        subject_text = f"Create a {prefix}learning goal titled '{label}'"
    else:
        subject_text = f"Create a {prefix}task titled '{label}'"

    clauses = [subject_text]
    # Only a kind that *has* a date field may be described as having a date. A
    # note has none, so "save this note tomorrow" produces no date clause at all
    # — a sentence describing an effect that the payload would not perform is
    # worse than no sentence, because the user would be confirming a deadline
    # NEXUS is going to drop.
    if due_date is not None and spec.date_field is not None:
        clauses.append(_format_due(due_date, today, _DATE_WORDING.get(spec.kind, "due")))
    if kind is ActionKind.CREATE_TASK:
        where = _describe_target(target_label, target_id)
        if where:
            clauses.append(where)
    return ", ".join(clauses) + "."


# --------------------------------------------------------------------------- #
# The proposal itself
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class _Refused:
    """A refusal raised below the routing table, carried back as a value.

    The payload builder has three early exits that are refusals rather than
    errors — an unstorable field, an unrecoverable status word, a window with no
    start — and threading a ``ProposalRefusal`` through each one would mean
    constructing half of one before the caller knows the intent. This is the two
    fields that actually matter, and :func:`propose_action` completes the answer.
    """

    reason_code: str
    reason: str


@dataclass(frozen=True, slots=True)
class _Endpoint:
    """One end of a proposed knowledge edge."""

    entity: KnowledgeEntityType
    identifier: UUID
    label: str


@dataclass(frozen=True, slots=True)
class _LinkEndpoints:
    """Both ends of a proposed knowledge edge, resolved to real rows."""

    source: _Endpoint
    target: _Endpoint


def _verb_for(extraction: Extraction) -> str:
    """The verb to route on, which is the extracted one unless it contradicts a state.

    ``"mark the API contract task as blocked"`` carries a completion verb *and* an
    explicit state, because ``mark`` is how people open a completion and
    ``blocked`` is what they then said. Routing on the verb alone would complete
    the task — the opposite of the request — and the confirm sentence would go on
    to say "as completed", which is a sentence about something the user did not
    ask for. A stated state that is not ``completed`` is therefore read as the
    transition it names, which is not a guess: the user wrote the state down.
    """
    verb = extraction.verb
    if verb != ExtractedVerb.COMPLETE:
        return verb
    stated = extraction.target_status or extraction.values.get("status")
    return verb if stated in (None, TaskStatus.COMPLETED) else ExtractedVerb.STATUS


def _unroutable(verb: str, intent: str, entity: str) -> _Refused:
    """Why this ``(intent, verb, entity)`` triple has no spec, in the user's terms.

    The two answers are kept apart because they are two different conversations.
    "I could not tell what you wanted done" sends the user back to a shorter
    sentence. "I understood that, but not on that kind of row" sends them to a
    different noun, and telling them the first would be true and useless.
    """
    if verb not in _VERBS_BY_INTENT.get(intent, frozenset()):
        return _Refused(
            ProposalReason.VERB_NOT_RECOVERED,
            "NEXUS could not tell what this request asks to be done. The classifier "
            "does not see verbs — 'add a task' and 'delete a task' are the same "
            "intent — so it asks rather than picking one.",
        )
    known = _ENTITIES_BY_INTENT_VERB.get((intent, verb), frozenset())
    if entity not in known:
        offered = " or ".join(f"'{name}'" for name in sorted(known)) or "nothing"
        named = f"a {entity}" if entity in _KNOWN_ENTITIES else f"'{entity}'"
        return _Refused(
            ProposalReason.ENTITY_NOT_RECOGNISED,
            f"NEXUS read '{verb}' and {named}, but '{intent}' does not "
            f"{verb} {offered}. Say which kind of row you meant.",
        )
    return _Refused(
        ProposalReason.VERB_NOT_RECOVERED,
        f"NEXUS could not tell what to do about the {entity}.",
    )


def _match_target(
    fragment: str, entity: str, context: ProposalContext
) -> RowMatch | RowMatchFailure:
    """Resolve the reference against the candidate list for ``entity``.

    Returns:
        Whatever :func:`~app.ml.actions.extraction.match_reference` returns — a
        match, or a :class:`~app.ml.actions.extraction.RowMatchFailure` whose
        reason code distinguishes "no such row" from "several such rows". An
        entity with no candidate list at all is a not-found rather than a crash,
        because an empty list is a legitimate answer from a caller that fetched
        nothing for this intent.
    """
    field = _CANDIDATE_FIELD_BY_ENTITY.get(entity)
    if field is None:
        return RowMatchFailure(
            reason_code="not_found",
            reason=f"NEXUS has no {entity} rows to look the reference up in.",
        )
    return match_reference(fragment, getattr(context, field))


def _resolve_link_endpoints(
    values: Mapping[str, str], context: ProposalContext
) -> _LinkEndpoints | _Refused:
    """Resolve both ends of a proposed edge to rows the caller already owns.

    ``knowledge_links`` is polymorphic: the pair of endpoints carries its own
    type, and nothing in the table can check it. So each end is matched against
    every pool a knowledge edge may point at, and the pool that produced a unique
    match supplies both the id *and* the type. Zero matches and several matches
    are refusals, which is the same rule every other row obeys — an edge to a
    guessed endpoint is a dangling row nobody will ever see again.
    """
    resolved: list[_Endpoint] = []
    for which in ("source", "target"):
        reference = values.get(which)
        if not reference:
            return _Refused(
                ProposalReason.FIELD_NOT_RECOVERABLE,
                f"A link needs both ends named, and NEXUS could not tell which row is the {which}.",
            )
        hits = []
        for entity, field in _LINK_POOLS:
            match = match_reference(reference, getattr(context, field))
            if not isinstance(match, RowMatchFailure):
                hits.append(_Endpoint(entity, match.candidate.id, match.candidate.label))
        if not hits:
            return _Refused(
                ProposalReason.TARGET_NOT_FOUND,
                f"Nothing in your notes or concepts matches the {which} '{reference}', "
                "so NEXUS will not link a row it would have to guess at.",
            )
        if len(hits) > 1:
            return _Refused(
                ProposalReason.TARGET_AMBIGUOUS,
                f"'{reference}' matches more than one row as the {which}, and NEXUS "
                "will not pick between them.",
            )
        resolved.append(hits[0])
    source, target = resolved
    if source.identifier == target.identifier:
        return _Refused(
            ProposalReason.PAYLOAD_INVALID,
            "That link would start and end at the same row, which the database "
            "refuses as a self edge.",
        )
    return _LinkEndpoints(source, target)


def _as_instant(value: Any) -> datetime | None:
    """Read a value as an aware instant, or ``None`` when it is not one."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _window_fields(
    extraction: Extraction,
    raw: dict[str, Any],
    start_key: str,
    end_key: str,
) -> dict[str, Any] | _Refused:
    """The two ends of a calendar or session window, or a refusal.

    A window that starts and ends in the same instant is not a window the user
    meant, and the schemas say so; the assumption made when only a day was named
    is a named constant, and both ends of it appear in the confirm sentence.
    """
    starts = _as_instant(raw.get(start_key)) or extraction.due_at
    if starts is None:
        return _Refused(
            ProposalReason.FIELD_NOT_RECOVERABLE,
            "That request has to name a day, and NEXUS could not recover one from "
            "it. Say which day.",
        )
    ends = _as_instant(raw.get(end_key)) or (starts + timedelta(minutes=_ASSUMED_WINDOW_MINUTES))
    if starts.tzinfo is None or ends.tzinfo is None:
        return _Refused(
            ProposalReason.FIELD_NOT_RECOVERABLE,
            "NEXUS read a time with no time zone, which it will not assume one for.",
        )
    if ends <= starts:
        return _Refused(
            ProposalReason.PAYLOAD_INVALID,
            f"That request ends {_window_word(ends)} before it starts, so there is no "
            "window to write.",
        )
    return {start_key: starts, end_key: ends}


def _window_word(instant: datetime) -> str:
    """A short human phrase for one end of a window, used in a refusal."""
    return f"at {instant.astimezone(UTC):%H:%M}"


def propose_action(
    text: str,
    prediction: IntentPrediction,
    *,
    context: ProposalContext | None = None,
) -> ActionProposal | ProposalRefusal:
    """Propose — never perform — the action one utterance asks for.

    The whole contract is in the return type: an :class:`ActionProposal` the
    user must confirm, or a :class:`ProposalRefusal` saying why there is none.
    There is no path through this function that writes anything, and no argument
    that asks it to.

    **The order of the refusals is the design.** A request is checked for scope
    (does it name one row or a collection), then for routing (does this intent
    write this row with this verb), then for a target (does the reference name
    exactly one of the caller's rows), and only then for a payload. Each of those
    is a question the next one depends on, and a request that fails the first
    three is not answered with a half-built payload that would read as "there was
    only this much I could do".

    Args:
        text: The utterance, as the classifier saw it.
        prediction: The classifier's output. Nothing is re-classified here; the
            intent is taken as given, which is why this layer can be tested
            without a checkpoint.
        context: The caller's zone, clock, project and candidate rows. Defaults
            to UTC and the current instant, which is enough for a request that
            needs none of them.

    Returns:
        An :class:`ActionProposal` or a :class:`ProposalRefusal`.
    """
    context = context or ProposalContext()
    extraction = extract_arguments(
        text, prediction, tz=context.tz, now=context.now or datetime.now(UTC)
    )
    intent = extraction.intent
    confidence = extraction.confidence

    def refuse(code: str, why: str, kind: ActionKind | None = None) -> ProposalRefusal:
        return ProposalRefusal(
            intent=intent,
            confidence=confidence,
            reason_code=code,
            reason=why,
            kind=kind,
            arguments=extraction.arguments,
            notes=extraction.notes,
        )

    if intent not in _PROPOSABLE_INTENTS:
        return refuse(
            ProposalReason.UNSUPPORTED_INTENT,
            extraction.reason
            or (
                f"'{intent}' names a surface to look at rather than something to "
                "write, so there is no action to propose."
            ),
        )

    # One sentence, one row. This is checked before anything else can be built
    # from the text, because "delete everything" and "delete the duplicate
    # reminder I made yesterday" are the same verb to the extractor and are not
    # the same request at all. A collection delete needs a count and a preview,
    # and neither travels inside an utterance.
    if extraction.bulk:
        return refuse(
            ProposalReason.DESTRUCTIVE_REQUEST,
            "NEXUS will not remove a whole collection from one sentence. Name the "
            "row you mean and it will propose that one — which you then confirm, "
            "and which for a delete takes a second press.",
        )

    entity = extraction.entity or _DEFAULT_ENTITY_BY_INTENT.get(intent, "")
    verb = _verb_for(extraction)
    spec = _SPEC_BY_INTENT_VERB_ENTITY.get((intent, verb, entity))
    if spec is None:
        unroutable = _unroutable(verb, intent, entity)
        return refuse(unroutable.reason_code, unroutable.reason)

    if extraction.title is None:
        return refuse(
            ProposalReason.TITLE_NOT_RECOVERABLE,
            extraction.reason or "NEXUS could not recover a title.",
            kind=spec.kind,
        )

    target_id: UUID | None = None
    target_label: str | None = None

    if spec.kind in _TARGET_KINDS:
        matched = _match_target(extraction.title, entity, context)
        if isinstance(matched, RowMatchFailure):
            return refuse(
                ProposalReason.TARGET_AMBIGUOUS
                if matched.reason_code == "ambiguous"
                else ProposalReason.TARGET_NOT_FOUND,
                matched.reason,
                kind=spec.kind,
            )
        target_id = matched.candidate.id
        target_label = matched.candidate.label
    elif spec.kind is ActionKind.CREATE_TASK:
        if context.project_id is None:
            return refuse(
                ProposalReason.CONTEXT_MISSING,
                "Creating a task needs the project it belongs to, and no project was "
                "supplied with the request. Open the project's board and add it "
                "there.",
                kind=spec.kind,
            )
        target_id = context.project_id
        target_label = context.project_label

    links: _LinkEndpoints | None = None
    if spec.kind is ActionKind.CREATE_LINK:
        endpoints = _resolve_link_endpoints(extraction.values, context)
        if isinstance(endpoints, _Refused):
            return refuse(endpoints.reason_code, endpoints.reason, kind=spec.kind)
        links = endpoints

    today = local_day(context.now or datetime.now(UTC), context.tz)
    payload = _build_payload(spec, extraction=extraction, context=context, links=links)
    if isinstance(payload, _Refused):
        return refuse(payload.reason_code, payload.reason, kind=spec.kind)

    # An action that acts on a row quotes the **stored** title, because the user's
    # phrase is a fragment of it and the row's own name is what the confirm dialog
    # has to show. A creation has no such row yet, so it quotes what was
    # extracted; ``target_label`` there is the *project*, and putting that in the
    # title would have named the task after its parent.
    summary = _summarise(
        spec,
        entity=entity,
        label=target_label if spec.kind in _TARGET_KINDS else extraction.title,
        payload=payload,
        priority=extraction.priority,
        due_date=extraction.due_date,
        today=today,
        tz=context.tz,
        target_label=target_label,
        target_id=target_id,
        links=links,
    )
    return ActionProposal(
        kind=spec.kind,
        intent=intent,
        confidence=confidence,
        payload=payload,
        arguments=extraction.arguments,
        permission=spec.permission,
        summary=summary,
        spec=spec,
        notes=extraction.notes,
        target_id=target_id,
        target_label=target_label,
        destructive=spec.destructive,
    )


def _build_payload(
    spec: ActionSpec,
    *,
    extraction: Extraction,
    context: ProposalContext,
    links: _LinkEndpoints | None = None,
) -> BaseModel | _Refused:
    """Validate the extracted arguments into the spec's schema.

    Only fields that were actually extracted are passed, so ``model_fields_set``
    on the resulting payload is exactly the set of things the user said. That is
    what keeps a schema default out of the confirm sentence: a field nobody
    mentioned was never set, and a caller that wants to know what was asserted can
    read ``model_fields_set`` instead of guessing.

    A field the user *did* mention but this payload cannot carry is not dropped
    and is not defaulted either — it is a refusal
    (:data:`ProposalReason.FIELD_NOT_RECOVERABLE`), because silently ignoring a
    stated change is the one behaviour every ``extra="forbid"`` in this codebase
    exists to prevent.

    Returns:
        The validated payload, or a :class:`_Refused` naming the reason. The
        schema's own error is not propagated: a validation failure here means the
        extraction produced something the route would reject, and the right answer
        to that is a refusal with a reason, not a 422 to a caller who asked a
        question rather than submitted a payload. A payload that *does* validate
        here is then put through :func:`_publishable` before it leaves, so what
        this returns is always something the confirm route will accept back.
    """
    fields = set(spec.schema.model_fields)
    values = extraction.values
    raw: dict[str, Any] = {}
    consumed: set[str] = set()

    # The title means opposite things depending on the verb, so it is read
    # differently rather than uniformly: on a creation it is the name being given,
    # on an update it is the name being written, and on everything else it is the
    # fragment the matcher already consumed and the payload must not repeat.
    # An update reads the generic ``title`` too, because the noun a person says
    # ("rename the project to …") is not the column the row stores it in.
    #
    # ``consumed`` records which of the stated keys have found a home, so the
    # check below can tell a value this payload placed apart from one it dropped.
    if spec.kind in _CREATE_KINDS and spec.title_field in fields:
        raw[str(spec.title_field)] = extraction.title
        # A creation's title-ish keys all name the row being created; a project
        # has no ``title`` column but "create a project called Q4 Migration" is
        # not a request to change a field nothing holds.
        consumed |= set(values) & _TITLE_KEYS
    elif spec.kind in _UPDATE_KINDS and spec.title_field in fields:
        # An update writes a name only when one was stated. Filling it from
        # ``extraction.title`` would put the row's own current title back into a
        # PATCH and then print "set title to <what it already was>", which is a
        # sentence about a change nobody asked to make.
        stated = values.get(str(spec.title_field)) or values.get("title")
        if stated is not None:
            raw[str(spec.title_field)] = stated
            consumed |= {key for key in (str(spec.title_field), "title") if key in values}
    else:
        # A delete, a transition or a tag: the name in the utterance is the
        # reference the matcher just resolved, not a value being written back.
        consumed |= set(values) & _TITLE_KEYS
    consumed |= _EXTRA_VALUE_KEYS.get(spec.kind, frozenset())
    if spec.schema is ActionAck:
        consumed |= _ACTION_ACK_CONTEXT_KEYS

    unstorable = sorted(name for name in values if name not in fields and name not in consumed)
    if unstorable:
        return _Refused(
            ProposalReason.FIELD_NOT_RECOVERABLE,
            f"NEXUS read a change to '{unstorable[0]}', which this action cannot "
            "carry, so it is asking rather than dropping something you asked for.",
        )

    for name, value in values.items():
        if name in fields:
            raw.setdefault(name, value)

    if spec.date_field is not None and extraction.due_date is not None:
        raw.setdefault(spec.date_field, extraction.due_date)
    # Not every kind has somewhere to put a priority — :class:`NoteCreate` has no
    # such field and forbids extras, so sending one would fail validation for a
    # field the note cannot hold anyway. The schema, not the kind, is the
    # authority on which fields exist.
    if extraction.priority is not None and "priority" in fields:
        raw.setdefault("priority", extraction.priority)

    # A task is the one row that cannot exist on its own. The id comes from the
    # context rather than the utterance, which is why :func:`propose_action`
    # answers ``context_missing`` before it ever gets here: a payload with a
    # guessed project would land on somebody else's board.
    if spec.kind is ActionKind.CREATE_TASK:
        if context.project_id is None:
            return _Refused(
                ProposalReason.CONTEXT_MISSING,
                "Creating a task needs the project it belongs to, and no project was "
                "supplied with the request.",
            )
        raw["project_id"] = context.project_id

    # A repository may be linked to a project and does not have to be, so this is
    # the one id on this layer that is passed **through** rather than demanded: the
    # caller supplies one when the request arrived from inside a project and gets
    # a repository either way. Nothing here resolves it — the service proves
    # ownership of whatever arrives, which is why another account's project is a
    # 404 at confirm time rather than a 403 here.
    if spec.kind is ActionKind.CREATE_REPOSITORY and context.project_id is not None:
        raw["project_id"] = context.project_id

    special = _specialise(spec, extraction=extraction, raw=raw, links=links)
    if isinstance(special, _Refused):
        return special
    raw.update(special)

    try:
        payload = spec.schema(**raw)
    except ValidationError:
        return _Refused(
            ProposalReason.PAYLOAD_INVALID,
            "What NEXUS extracted did not satisfy the payload schema, so it is "
            "asking rather than writing something malformed.",
        )
    return _publishable(spec, payload)


def _publishable(spec: ActionSpec, payload: BaseModel) -> BaseModel | _Refused:
    """The payload, but only once it is proven the confirm route will take it back.

    **This is the round-trip contract, enforced at the end that publishes.**
    :meth:`ActionProposal.to_dict` and
    :meth:`app.schemas.actions.ActionProposalRead.from_proposal` both hand the
    client ``payload.model_dump(mode="json")``, and the confirm endpoint
    re-validates exactly that rendering against exactly this schema before it
    calls anything. A payload that is legal as an object but not as its own JSON
    rendering is therefore a proposal the backend itself made unconfirmable: the
    user reads a sentence, presses Confirm, and gets a 422 for a field they never
    typed. That is a defect on this side of the wire, not a mistake on theirs, and
    the only place it can be caught without shipping it is here.

    The two ways it happens are both silent at construction time:

    * Pydantic does not validate a field's **default** unless the field says
      ``validate_default=True``. A member declared ``x: str = Field(default=None)``
      constructs happily, dumps as ``null``, and is then refused by
      :meth:`BaseModel.model_validate` — which is the failure in one sentence.
    * An ``AfterValidator`` that only accepts a *native* type — a naive datetime
      raised to an aware one by the constructor, say — is handed a JSON string by
      the confirm route rather than the object it was written against.

    So this re-runs the two rejections the confirm route runs first — unknown
    keys, then the schema itself — against the published rendering. Both are
    unreachable for a payload that came out of this function, and that is the
    point: they are checked here so that a future schema edit cannot quietly turn
    one of them back into a 422 the user is asked to explain.

    Refusing rather than publishing is the right answer on the rare occasion it
    fires. A ``payload_invalid`` refusal is an honest 200 the caller can render
    and the user can rephrase; an unconfirmable proposal is a dialog that lies
    about being able to do something.
    """
    published = payload.model_dump(mode="json")
    unknown = sorted(set(published) - set(spec.schema.model_fields))
    if unknown:  # pragma: no cover — model_dump emits exactly the declared fields
        return _Refused(
            ProposalReason.PAYLOAD_INVALID,
            f"NEXUS read that request, but the details it would have to send carry a "
            f"field this action has nowhere to put ('{unknown[0]}'), so it is asking "
            "rather than offering an action it could not carry out.",
        )
    try:
        spec.schema.model_validate(published)
    except ValidationError:
        return _Refused(
            ProposalReason.PAYLOAD_INVALID,
            "NEXUS read that request, but the details it would have to send cannot be "
            "written back in the form NEXUS publishes them, so it is asking rather "
            "than offering an action that could not be carried out. Rephrase it and "
            "NEXUS will read it again.",
        )
    return payload


def _specialise(
    spec: ActionSpec,
    *,
    extraction: Extraction,
    raw: dict[str, Any],
    links: _LinkEndpoints | None,
) -> dict[str, Any] | _Refused:
    """The fields a kind has to work out for itself, on top of the stated ones.

    Split out of :func:`_build_payload` because these are the handful of places
    where the schema alone cannot say what to write: a transition's target state
    comes from the verb rather than from a field the user spelled out, a schedule
    needs a start day, and an event's two ends may have to be assumed. Every one
    of them either produces something the confirm sentence shows in full, or
    refuses.
    """
    kind = spec.kind

    # An update with nothing in it is not an update. It reaches here as a schema
    # the utterance satisfied vacuously, and confirming it would be a PATCH that
    # changes no field and reports success — the one write whose sentence cannot
    # be written, because there is nothing to say.
    if kind in _UPDATE_KINDS and not raw:
        return _Refused(
            ProposalReason.FIELD_NOT_RECOVERABLE,
            "NEXUS read that as a change but could not find what to change. Name "
            "the field and its new value.",
        )

    if kind is ActionKind.COMPLETE_TASK:
        note = raw.get("note")
        return {"status": str(TaskStatus.COMPLETED), **({"note": note} if note else {})}

    if kind in (ActionKind.SET_TASK_STATUS, ActionKind.SET_PROJECT_STATUS):
        word = extraction.target_status or raw.get("status")
        if not word:
            return _Refused(
                ProposalReason.FIELD_NOT_RECOVERABLE,
                "NEXUS could not tell which state that is. Name the state — "
                "'in progress', 'blocked', 'cancelled', 'active', 'on hold'.",
            )
        try:
            raw["status"] = (
                TaskStatus(word) if kind is ActionKind.SET_TASK_STATUS else ProjectStatus(word)
            )
        except ValueError:
            return _Refused(
                ProposalReason.FIELD_NOT_RECOVERABLE,
                f"'{word}' is not a state anything in NEXUS can be moved to, so "
                "there is no change to propose.",
            )
        return {}

    if kind in (ActionKind.TAG_TASK, ActionKind.UNTAG_TASK):
        # "tag the API contract urgent" carries no ``tag`` key, because the
        # priority vocabulary got there first and read "urgent" as a grade. The
        # sentence plainly named a tag, so it is read as one — from the span the
        # priority rule actually consumed, so the tag is called "urgent" and not
        # the grade that word maps to. The confirm line prints the tag it found,
        # which makes this a shown reading rather than a hidden one, and refusing
        # a request that clearly named a tag would teach the user that the
        # assistant cannot be asked for one.
        tag = (
            raw.get("tag")
            or extraction.values.get("tag")
            or _matched_phrase(extraction, "priority")
        )
        if not tag:
            return _Refused(
                ProposalReason.FIELD_NOT_RECOVERABLE,
                "NEXUS could not tell which tag that was. Say the tag's name.",
            )
        return {"tag": tag}

    if kind is ActionKind.SCHEDULE_TASK:
        start = raw.pop("start_date", None) or extraction.due_date
        if start is None:
            return _Refused(
                ProposalReason.FIELD_NOT_RECOVERABLE,
                "Scheduling a task needs a day to start on, and NEXUS could not "
                "recover one from that sentence.",
            )
        return {"start_date": start}

    if kind is ActionKind.CREATE_LINK:
        if links is None:  # pragma: no cover — propose_action resolves both ends first
            return _Refused(
                ProposalReason.FIELD_NOT_RECOVERABLE,
                "A link needs both of its ends named.",
            )
        endpoint_fields: dict[str, Any] = {
            "source_type": links.source.entity,
            "source_id": links.source.identifier,
            "target_type": links.target.entity,
            "target_id": links.target.identifier,
        }
        link_type = extraction.values.get("link_type")
        if link_type:
            endpoint_fields["link_type"] = link_type
        return endpoint_fields

    if kind is ActionKind.CREATE_EVENT:
        window = _window_fields(extraction, raw, "starts_at", "ends_at")
        return {} if isinstance(window, _Refused) else window

    if kind is ActionKind.CREATE_SESSION:
        window = _window_fields(extraction, raw, "scheduled_start", "scheduled_end")
        return {} if isinstance(window, _Refused) else window

    if kind is ActionKind.CREATE_REPOSITORY:
        path = extraction.values.get("path")
        if not path:
            # The one field here that cannot be inferred at all. Every other rule
            # in this package refuses rather than guesses — no nearest-match date,
            # no ambiguous row, no bulk action — and a path is no different: a
            # repository registered against a guessed folder is a row whose every
            # future scan fails, and the sentence would have named a directory the
            # user never said. The refusal carries the shape of the request that
            # works instead of an error.
            return _Refused(
                ProposalReason.FIELD_NOT_RECOVERABLE,
                "NEXUS needs the folder itself to register a repository, and no "
                "path was in that request. Say where it lives — 'add repo name xyz "
                "from path E:/op'.",
            )
        return {"local_path": path}

    return {}


def _matched_phrase(extraction: Extraction, field: str) -> str | None:
    """The text the extractor actually consumed for ``field``, not its mapping.

    Needed wherever a *word* is wanted rather than the value it normalises to:
    ``"urgent"`` maps to the grade ``high``, and a tag the user called "urgent"
    is not called "high". Reads the provenance the extraction layer already
    carries, so this layer does not keep a second vocabulary.
    """
    for argument in reversed(extraction.arguments):
        if argument.field == field and argument.matched_text:
            return argument.matched_text
    return None


def _payload_to_dict(payload: BaseModel) -> dict[str, Any]:
    """Render a payload's fields as JSON-safe values, UUIDs as strings."""
    rendered: dict[str, Any] = {}
    for name, value in payload.model_dump(mode="json").items():
        rendered[name] = value
    return rendered
