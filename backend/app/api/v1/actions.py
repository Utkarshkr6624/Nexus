"""The propose → confirm pair: the only two doors an utterance gets through.

Phase 11 classified sentences and stopped there. ``app/api/v1/ml.py`` said why in
its own docstring — turning *"add a task to draft the migration plan for Friday"*
into ``TaskService.create(...)`` is slot filling, "and it needs either a second
model or hand-written per-utterance parsers". :mod:`app.ml.actions.extraction` is
the hand-written one and :mod:`app.ml.actions.proposals` decides what those
arguments may become; this file is the thin layer that hands an utterance to them
and, on the user's say-so, makes the call they name.

Two routes
----------
``POST /api/v1/ml/action/propose`` and ``POST /api/v1/ml/action/confirm``. Neither
is optional and there is no third: ``propose`` is what the user reads, ``confirm``
is the only thing in NEXUS that executes a sentence. Adding a route that wrote
without a ``propose`` in front of it would be exactly the surface this phase
exists not to offer.

**The prefix is the existing ``/ml`` one, and that is safe.** ``app/api/v1/ml.py``
owns ``/ml/status`` and ``/ml/route``; two routers may share a prefix as long as
the paths below them do not collide, and ``/ml/action/...`` collides with neither
because neither takes a parameter. Nothing here is parameterised, so the
load-bearing rule ``app/api/v1/risks.py`` spells out cannot be violated by a
future ``/ml/{anything}``.

What propose does, and what it deliberately does not
----------------------------------------------------
It reuses :class:`~app.ml.runtime.MLRuntime` and
:meth:`app.ml.classifier.IntentClassifier.predict` verbatim — **one model, loaded
once.** The text is passed through byte for byte, because training consumed the
raw dataset strings and trimming it here would be a distribution shift the
checkpoint has never seen. The prediction goes to
:func:`app.ml.actions.proposals.propose_action`, which decides what may be
proposed and refuses everything else with a reason.

**A refusal is a 200.** ``unsupported_intent``, ``destructive_request``,
``verb_not_recovered``, ``title_not_recoverable``, ``target_ambiguous``,
``target_not_found``, ``field_not_recoverable``, ``entity_not_recognised``,
``context_missing`` and ``payload_invalid`` are answers NEXUS gave on purpose.
Returning 500 for "could not understand" would make a routine outcome
indistinguishable from a fault, and returning 4xx would make the client treat a
sentence it should have shown to the user as a broken request. Only a classifier
that cannot run at all is an error, and it fails closed with 503.

**A classifier that cannot run is a 503, never a fabricated proposal.** The
caller's next move on a proposal is a write, so an invented intent would be
NEXUS inventing a user's instruction and then acting on it.

Context, assembled from the caller's own rows
---------------------------------------------
Three things an utterance cannot supply, and each is resolved server-side:

* **The zone.** ``?tz=``, resolved through
  :func:`app.services.planner_service.resolve_timezone` — the same helper the
  planner routes use, so a date extracted here and a day planned there cut on the
  same instant. It raises rather than falling back to UTC, and a caller with no
  ``tz`` gets the deployment default.
* **The project.** Resolved through the owner-scoped project lookup, so another
  account's project is a 404 and its name never reaches a payload. A creation
  without a project is refused by the proposal layer with ``context_missing``
  rather than answered with a guessed id.
* **The candidate rows.** Fetched **only for the intent the classifier
  predicted**, owner-scoped, capped, and shaped to the row types that intent's
  kinds can act on — see :func:`_candidate_rows`. They are the rows
  :func:`~app.ml.actions.extraction.match_reference` may resolve a reference
  against; without them "delete the API contract task" could only ever be a
  refusal, and with them "delete every task" can be told apart from it only
  because a collection never matches one row.

Why confirm is the security-critical half
-----------------------------------------
A proposal is a description. Confirm is where it becomes a write, and the client
is the untrusted party from that moment on: the user may have edited the title in
the confirm dialog, and a body is just bytes. So **nothing in the request is
believed.** In order:

1. ``kind`` is parsed as an :class:`~app.ml.actions.proposals.ActionKind` by the
   request model, so a name outside the closed set is a 422 before the handler
   runs.
2. :attr:`~app.ml.actions.proposals.ActionSpec.destructive` is a **field**, read
   from the same frozen table row that produced the dialog's warning. A
   destructive kind is refused unless the request carries
   :attr:`~app.schemas.actions.ConfirmActionRequest.confirm_destructive`, so the
   proof that the user was warned and the proof that they went ahead are the same
   boolean, and a client cannot execute a delete it never showed anybody.
3. The :class:`~app.ml.actions.proposals.ActionSpec` is looked up in the frozen
   table, which supplies the payload schema and the permission. Nothing about the
   body can name a different service.
4. The permission is re-checked server-side against
   :attr:`app.core.permissions.Permission` for the caller's role. The route gate
   is ``analytics.read``; the *action's* capability — ``tasks.write``,
   ``projects.write``, ``knowledge.write`` — is a second, separate check, so a
   client cannot propose under one capability and execute under another.
5. The ``intent`` must match the intent the spec says this kind can only have
   come from, **or** one of the other trained intents the same request may be
   classified as (:attr:`~app.ml.actions.proposals.ActionSpec.also_intents`).
   ``schedule_plan`` and ``knowledge_lookup`` are legitimate second readings of a
   request whose kind lives under another intent, and refusing those would make
   the assistant fail on sentences it in fact understands.
6. Unknown payload keys are rejected, then the payload is validated against the
   spec's own Pydantic model. An empty payload is rejected **only** when that
   schema has required fields — the no-argument kinds legitimately send ``{}``.
7. Every id is re-resolved through an owner-scoped service method. A forged
   ``project_id`` is refused by ``TaskService.create``'s own scoped lookup, and a
   forged ``target_id`` by ``TaskService.get``, ``KnowledgeService.get_note``,
   ``PlannerService.get_event``, ``LearningIntelligenceService.get_goal`` or
   ``DeveloperIntelligenceService.get_repository`` — whichever the kind needs, all
   a 404, all before the row is written, all indistinguishable from an id nobody
   has issued.

**Deletions are possible here, and the safety is not in their absence.** The
earlier version of this module refused every destructive request outright, on the
reasoning that a surface where one sentence removes the evidence of what a user
did is a surface NEXUS should not offer. That reasoning was about *unbounded*
removal, and it was addressed in the wrong place: refusing "delete" as a word
also refused "delete the draft note I just wrote", which is the single most
ordinary correction a person makes. The properties that actually matter are kept,
and each of them is enforced below rather than asserted:

* nothing executes without a ``propose`` the user read first, and
  :attr:`~app.ml.actions.proposals.ActionProposal.requires_confirmation` is a
  property returning ``True`` rather than a field that could be set to ``False``;
* a destructive kind is refused unless the caller says the dialog said so;
* a delete names **exactly one row**, resolved owner-scoped at confirm time — the
  proposal layer refuses a whole-collection request with
  ``destructive_request`` and this endpoint has no code path that could act on one;
* a reference matching zero or several rows is a refusal, never a best guess.

**The activity trail is not optional here.** Every service below arrives through
:mod:`app.api.deps`, which wires ``activity=`` for all of them, so a task created
through this endpoint writes the same ``TASK_CREATED`` row a hand-typed
``POST /tasks`` writes, and a task deleted here writes the same
``TASK_DELETED`` row. The brief's phrase "call the real service" is the whole of
it: this router has no SQL, no session of its own and no second write path.

Idempotence: a replay is a no-op, not a duplicate and not an error
------------------------------------------------------------------
Confirm is not idempotent at the storage layer — ``tasks``, ``projects``, ``notes``
and ``learning_goals`` have no natural key a payload could hit — and there is no
proposal table to hang a nonce off, so an idempotency *key* would have to live in
process memory and would expire with the restart that most often accompanies a
retry. So the replay is detected instead, against the caller's own rows: before
a creation, the endpoint asks the same service for a page of the caller's work and
compares the identifying field **exactly** (title for a task, note and goal; name
for a project), scoped to the same parent. A match returns 200 with
``outcome: "no_op"``, ``applied: false`` and the id of the row that is already
there.

**Why no-op and not 409.** The realistic duplicate is a double-click or a retry
after a timeout, and in both cases the desired state — this task exists — *was*
achieved by the first call. A 409 would render a working system as a failure and
teach users to retry harder. A no-op names the existing row, so the dialog can
close on the real id. The cost is a bounded page read per creation, and the
failure mode it introduces is suppression rather than duplication: a caller who
genuinely wants a second identical card must make it non-identical, and the
response says in as many words that nothing was created rather than leaving them
to discover it.

A transition or an update is idempotent for free, and reports itself. Most of
these services return the row unchanged when the stored value already equals the
requested one — ``TaskService.set_status``, ``TaskService.unschedule``,
``TaskService.add_tag``, ``ProjectService.set_status``,
``KnowledgeService.archive_note``, ``PlanningService.update_event`` — and the
endpoint compares the fields it asked to change against what the service handed
back. That comparison is made against a **snapshot taken before the call**, not
against the row object: the repositories mutate the very instance they are
passed and refresh it, so ``task is updated`` on the success path and a
before/after comparison on the object would always report a no-op.

**The duplicate scan covers the five creation kinds that had one.** A bookmark, a
concept, a link, a skill, an event and a session are either not named by a
sentence in a way that collides (a link is identified by both of its endpoints; a
session by its window) or are refused with a 409 by their own service on a
duplicate, so adding a page read in front of them would be a cost with no
avoided error. A repository does have a page read, and for the mirror-image
reason: its identifying field is a **path**, which the service normalises before
it stores one, so the check has to normalise too — see :func:`_existing_path`.

What this module cannot claim
-----------------------------
There is no server-side record of a proposal, so confirm cannot prove one
happened; the binding it has is *shape* — a registered kind, an intent that kind
can only come from, and a payload that validates against the schema that kind
produces. Anything else is a 422. ``tests/test_ml_actions_api.py`` pins the route
inventory directly: this module registers exactly two routes, both ``POST``, and
``confirm`` is the only one that reaches a service.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError
from starlette.concurrency import run_in_threadpool

from app.api.deps import (
    AuthenticatedUser,
    DeveloperIntelligenceServiceDep,
    KnowledgeServiceDep,
    LearningIntelligenceServiceDep,
    MLRuntimeDep,
    PlannerServiceDep,
    ProjectRepositoryDep,
    ProjectServiceDep,
    SettingsDep,
    TagServiceDep,
    TaskServiceDep,
    UserServiceDep,
)
from app.core.deps import require_permission
from app.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.core.logging import get_logger, log_event
from app.core.permissions import Permission, has_permission
from app.ml.actions import (
    ACTION_SPECS,
    ActionAck,
    ActionKind,
    ActionProposal,
    ActionSpec,
    ExtractedVerb,
    Extraction,
    ProjectStatusWrite,
    ProposalContext,
    ProposalRefusal,
    RowCandidate,
    TaskScheduleWrite,
    extract_arguments,
    is_proposal,
    propose_action,
)
from app.ml.exceptions import MLUnavailableError
from app.models.enums import LearningGoalStatus, NoteStatus, TaskStatus
from app.models.user import User
from app.schemas.actions import (
    ConfirmActionRead,
    ConfirmActionRequest,
    ProposeActionRead,
    ProposeActionRequest,
)
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
from app.services.developer import DeveloperIntelligenceService
from app.services.knowledge_service import KnowledgeService
from app.services.learning import LearningIntelligenceService
from app.services.planner_service import PlannerService, resolve_timezone
from app.services.project_service import ProjectService
from app.services.tag_service import TagService
from app.services.task_service import TaskService
from app.services.user_service import UserService
from ml.datasets.taxonomy import Intent

router = APIRouter(prefix="/ml", tags=["ml-actions"])

logger = get_logger(__name__)

#: Applied to both routes, on the same reasoning ``app/api/v1/ml.py`` documents:
#: the Phase 11 ML surface reuses ``analytics.read`` rather than coining an
#: ``ml.*`` capability, and this is one more caller of that decision. It is a
#: *floor*, not the whole authorisation — the action's own capability is
#: re-checked per kind in :func:`confirm_action`.
_ANALYTICS_READ = [Depends(require_permission(Permission.ANALYTICS_READ))]

#: How many of the caller's own rows the replay check reads. A page, not a scan:
#: the duplicate this has to catch is one that was created seconds ago, so it is
#: within the first page of any ordering that puts new rows first. Kept at 50
#: because that is inside every service's own page ceiling, so the check can
#: never raise a validation error of its own.
_DUPLICATE_SCAN = 50

#: How many of the caller's own rows a reference may be matched against. A cap
#: rather than the caller's whole history:
#: :func:`~app.ml.actions.extraction.match_reference` refuses an ambiguous
#: reference outright, so a larger candidate set does not produce more matches —
#: it produces more refusals and a slower request. It also bounds the work one
#: sentence can cause on a large account.
_CANDIDATE_LIMIT = 50

#: The statuses a completion can legally leave, and therefore the only rows worth
#: offering as completion candidates. A completed or cancelled card is not a
#: candidate: completing it is either a no-op or an illegal edge, and neither is
#: a proposal worth showing a confirm button for. Applied only when the extracted
#: verb is a completion — see :func:`_task_candidates`.
_COMPLETABLE_STATUSES = frozenset(
    {TaskStatus.TODO.value, TaskStatus.IN_PROGRESS.value, TaskStatus.BLOCKED.value}
)

_TZ_DESCRIPTION = (
    "The caller's IANA zone, e.g. `Europe/Berlin`. Omitted uses the deployment "
    "default. Resolved exactly as the planner resolves it, so a date extracted "
    "here and a day planned there cut on the same instant."
)

#: The row each kind writes, for the ``entity`` field of the confirm response and
#: for the sentence that reports it. Derived from the kind's own service table
#: rather than kept as prose, so a kind added without an entity shows up here as
#: a gap the tests can see rather than as a response with an empty noun.
_ENTITY_BY_KIND: Mapping[ActionKind, str] = MappingProxyType(
    {
        ActionKind.CREATE_TASK: "task",
        ActionKind.UPDATE_TASK: "task",
        ActionKind.DELETE_TASK: "task",
        ActionKind.COMPLETE_TASK: "task",
        ActionKind.SET_TASK_STATUS: "task",
        ActionKind.SCHEDULE_TASK: "task",
        ActionKind.UNSCHEDULE_TASK: "task",
        ActionKind.TAG_TASK: "task",
        ActionKind.UNTAG_TASK: "task",
        ActionKind.CREATE_PROJECT: "project",
        ActionKind.UPDATE_PROJECT: "project",
        ActionKind.DELETE_PROJECT: "project",
        ActionKind.SET_PROJECT_STATUS: "project",
        ActionKind.CREATE_NOTE: "note",
        ActionKind.UPDATE_NOTE: "note",
        ActionKind.DELETE_NOTE: "note",
        ActionKind.ARCHIVE_NOTE: "note",
        ActionKind.PUBLISH_NOTE: "note",
        ActionKind.CREATE_BOOKMARK: "bookmark",
        ActionKind.DELETE_BOOKMARK: "bookmark",
        ActionKind.CREATE_CONCEPT: "concept",
        ActionKind.DELETE_CONCEPT: "concept",
        ActionKind.CREATE_LINK: "link",
        ActionKind.DELETE_LINK: "link",
        ActionKind.CREATE_LEARNING_GOAL: "learning_goal",
        ActionKind.UPDATE_LEARNING_GOAL: "learning_goal",
        ActionKind.COMPLETE_LEARNING_GOAL: "learning_goal",
        ActionKind.DELETE_LEARNING_GOAL: "learning_goal",
        ActionKind.CREATE_SKILL: "skill",
        ActionKind.DELETE_SKILL: "skill",
        ActionKind.CREATE_EVENT: "calendar_event",
        ActionKind.UPDATE_EVENT: "calendar_event",
        ActionKind.DELETE_EVENT: "calendar_event",
        ActionKind.CREATE_SESSION: "work_session",
        ActionKind.DELETE_SESSION: "work_session",
        ActionKind.UPDATE_PROFILE: "user",
        ActionKind.CREATE_REPOSITORY: "repository",
        ActionKind.DELETE_REPOSITORY: "repository",
    }
)

#: Which rows one predicted intent may act on, as the ``ProposalContext`` member
#: they arrive in. **Not every row type on every request**: a
#: ``knowledge_capture`` utterance can name a note, a bookmark, a concept or a
#: link, so it gets those four; an ``account_admin`` utterance can only name the
#: caller's own profile, which is already in hand, so it gets none. Fetching
#: everything would turn one sentence into eleven owner-scoped page reads and
#: widen the candidate pool for a kind that could never match a row from it.
_CANDIDATE_FIELD_BY_INTENT: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        str(Intent.TASK_MANAGE): ("task_candidates",),
        str(Intent.PROJECT_MANAGE): ("project_candidates",),
        str(Intent.SCHEDULE_PLAN): ("event_candidates", "session_candidates"),
        str(Intent.KNOWLEDGE_CAPTURE): (
            "note_candidates",
            "bookmark_candidates",
            "concept_candidates",
            "link_candidates",
        ),
        str(Intent.KNOWLEDGE_LOOKUP): (
            "note_candidates",
            "bookmark_candidates",
            "concept_candidates",
            "link_candidates",
        ),
        str(Intent.LEARNING_TRACK): ("goal_candidates", "skill_candidates"),
        str(Intent.DEVELOPER_INTEL): ("repository_candidates",),
        str(Intent.ACCOUNT_ADMIN): (),
    }
)


@dataclass(frozen=True, slots=True)
class _Services:
    """The request-scoped services :func:`confirm_action` is allowed to call.

    One object rather than eight parameters on every dispatcher: the dispatch
    table's signature is then exactly ``(services, owner, body, payload)``, which
    is what makes the table below readable as a list of what each kind does. The
    services arrive from :mod:`app.api.deps`, so each already carries the activity
    recorder and each re-resolves every id it is given.
    """

    tasks: TaskService
    projects: ProjectService
    knowledge: KnowledgeService
    learning: LearningIntelligenceService
    planner: PlannerService
    tags: TagService
    users: UserService
    developer: DeveloperIntelligenceService


async def _build_services(
    tasks: TaskServiceDep,
    projects: ProjectServiceDep,
    knowledge: KnowledgeServiceDep,
    learning: LearningIntelligenceServiceDep,
    planner: PlannerServiceDep,
    tags: TagServiceDep,
    users: UserServiceDep,
    developer: DeveloperIntelligenceServiceDep,
) -> _Services:
    """Resolve the eight services the action surface may call, as one value.

    A dependency rather than eight parameters on each route, so both handlers
    take the same single ``services`` argument and the dispatch table's signature
    stays ``(services, owner, body, payload)``. Every one of them arrives through
    :mod:`app.api.deps` and therefore already carries the activity recorder, which
    is what keeps this router free of a second write path.
    """
    return _Services(
        tasks=tasks,
        projects=projects,
        knowledge=knowledge,
        learning=learning,
        planner=planner,
        tags=tags,
        users=users,
        developer=developer,
    )


#: Injected into both routes. Resolved by FastAPI at import time, so it is
#: declared after :func:`_build_services` and before the handlers that name it.
_ServicesDep = Annotated[_Services, Depends(_build_services)]


@router.post(
    "/action/propose",
    response_model=ProposeActionRead,
    summary="Classify an utterance and propose the action it describes",
    description=(
        "Runs the trained intent classifier over the submitted text, extracts its "
        "arguments deterministically and returns either an action for the user to "
        "confirm or a refusal explaining why there is none. Nothing is written."
    ),
    dependencies=_ANALYTICS_READ,
)
async def propose_action_endpoint(
    payload: ProposeActionRequest,
    current_user: AuthenticatedUser,
    runtime: MLRuntimeDep,
    project_repository: ProjectRepositoryDep,
    settings: SettingsDep,
    services: _ServicesDep,
    tz: Annotated[str | None, Query(description=_TZ_DESCRIPTION)] = None,
) -> ProposeActionRead:
    """Propose — never perform — the action one utterance asks for.

    **The classifier is the only model, and it is the one already loaded.**
    ``runtime.classifier`` is the same object ``POST /ml/route`` uses, resolved by
    the same provider, so a second load would be a second copy of 703 MiB of
    weights. A runtime with no classifier raises
    :class:`~app.ml.exceptions.MLUnavailableError`, which the shared envelope
    renders as 503 ``ml_unavailable`` — never an invented intent, because the
    caller's next move on a proposal is a write.

    **Inference runs on a worker thread**, for the reason ``app/api/v1/ml.py``
    gives: a 183M-parameter forward pass is hundreds of milliseconds of solid
    compute and stalling the event loop for it would queue every other route
    behind one classification.

    **The submitted text is never logged** — only the intent, the confidence and
    the latency. An utterance is untrusted free text and may carry a credential.

    **Candidates are gathered for the predicted intent only.** The router knows
    the intent but not the verb, because the verb is read from the text by
    :mod:`app.ml.actions.extraction` *inside*
    :func:`~app.ml.actions.proposals.propose_action`; so the finest slice
    available here is the intent, and :func:`_candidate_rows` takes it. Fetching
    every row type on every request would be eleven owner-scoped page reads to
    answer one sentence, and would hand the matcher rows no kind in that intent
    could ever act on — which is how a reference ends up matching the wrong table.

    Errors: 401 unauthenticated, 403 without ``analytics.read``, 404 for a
    ``project_id`` that is not the caller's, 422 for blank, over-long or
    credential-shaped text, 503 when the classifier is unavailable, 500 if
    inference fails on a request it should have been able to answer. **A refusal
    is none of these** — it is a 200 with ``proposed: false``.
    """
    classifier = runtime.classifier
    if classifier is None:
        raise MLUnavailableError(details={"reason": runtime.status.reason})

    prediction = await run_in_threadpool(classifier.predict, payload.text)

    zone = resolve_timezone(tz, settings)
    project_id, project_label = await _resolve_project(
        project_repository, payload.project_id, current_user
    )
    now = datetime.now(UTC)
    # Extracted here as well as inside propose_action, purely so the candidate
    # read below can narrow by verb. Same text, same prediction, same clock, and
    # the function is pure — so the two calls cannot disagree, and the repeat
    # costs microseconds of regex beside a forward pass that has already run.
    extraction = extract_arguments(payload.text, prediction, tz=zone, now=now)
    candidates = await _candidate_rows(services, current_user, extraction)
    context = _proposal_context(
        tz=zone,
        now=now,
        project_id=project_id,
        project_label=project_label,
        candidates=candidates,
    )

    outcome = propose_action(payload.text, prediction, context=context)

    log_event(
        logger,
        logging.INFO,
        "ml_action_proposed",
        proposed=is_proposal(outcome),
        intent=prediction.intent,
        kind=str(outcome.kind) if outcome.kind else None,
        reason_code=None if is_proposal(outcome) else outcome.reason_code,
        confidence=round(float(prediction.confidence), 4),
        latency_ms=round(float(prediction.latency_ms), 3),
        truncated=prediction.truncated,
        text_chars=len(payload.text),
        candidates=sum(len(rows) for rows in candidates.values()),
    )

    if isinstance(outcome, ActionProposal):
        return ProposeActionRead.from_proposal(outcome)
    assert isinstance(outcome, ProposalRefusal)  # noqa: S101 — narrowing, not a runtime check
    return ProposeActionRead.from_refusal(outcome)


# --------------------------------------------------------------------------- #
# Propose: context assembled from the caller's own rows
# --------------------------------------------------------------------------- #


async def _resolve_project(
    repository: Any, project_id: UUID | None, owner: User
) -> tuple[UUID | None, str | None]:
    """Resolve the requested project through the owner-scoped lookup.

    A project belonging to somebody else is **not found**, never a permission
    error, and its name never reaches a payload or a summary: a 403 would confirm
    the id exists, which turns this endpoint into a directory of other people's
    boards.

    Returns:
        ``(id, name)`` when the request named a project the caller owns, and
        ``(None, None)`` when it named none. The proposal layer turns a
        ``create_task`` with no project into a ``context_missing`` refusal rather
        than a task filed under a guessed board.
    """
    if project_id is None:
        return None, None
    project = await repository.get_by_id_for_user(project_id, owner.id)
    if project is None:
        raise NotFoundError("That project does not exist.")
    return project.id, project.name


async def _candidate_rows(
    services: _Services, owner: User, extraction: Extraction
) -> Mapping[str, tuple[RowCandidate, ...]]:
    """The caller's own rows, for the row type this utterance can land on.

    **Gathered for the intent, and narrowed by the verb.** Every read here is
    owner-scoped inside the service that makes it — the caller is the ``WHERE``
    clause — so a candidate list can only ever contain rows the signed-in user
    can already see, which is what makes it safe for the proposal layer to match
    a phrase against one of them and hand the result to confirm.

    The intent chooses *which row types* to read and the verb chooses how far to
    narrow within one of them. Both are read from
    :func:`~app.ml.actions.extraction.extract_arguments` on the way in, so this
    does the extraction once more than :func:`propose_action` will — a pure
    function over the same text, the same prediction and the same clock, so the
    two calls cannot disagree, and the cost is microseconds of regex against a
    forward pass that has already run. What it buys is the difference between
    "here are the caller's fifty newest tasks" and "here are the rows this
    particular request could legally act on".

    Args:
        services: The request-scoped services.
        owner: The authenticated caller.
        extraction: What the utterance says, already extracted from the same
            text and prediction :func:`propose_action` will use.

    Returns:
        A mapping of :class:`~app.ml.actions.RowCandidate` tuples keyed by the
        :class:`~app.ml.actions.proposals.ProposalContext` member they belong in.
        Empty for an intent whose kinds can only act on rows already in hand —
        ``account_admin`` edits the caller's own profile and names no row.
    """
    wanted = _CANDIDATE_FIELD_BY_INTENT.get(str(extraction.intent), ())
    if not wanted:
        return {}
    rows: dict[str, tuple[RowCandidate, ...]] = {}
    if "task_candidates" in wanted:
        rows["task_candidates"] = await _task_candidates(
            services.tasks, owner, completable=extraction.verb is ExtractedVerb.COMPLETE
        )
    if "project_candidates" in wanted:
        rows["project_candidates"] = await _project_candidates(services.projects, owner)
    if "event_candidates" in wanted:
        rows["event_candidates"] = await _event_candidates(services.planner, owner)
    if "session_candidates" in wanted:
        rows["session_candidates"] = await _session_candidates(services.planner, owner)
    if "note_candidates" in wanted:
        rows["note_candidates"] = await _note_candidates(services.knowledge, owner)
    if "bookmark_candidates" in wanted:
        rows["bookmark_candidates"] = await _bookmark_candidates(services.knowledge, owner)
    if "concept_candidates" in wanted:
        rows["concept_candidates"] = await _concept_candidates(services.knowledge, owner)
    if "link_candidates" in wanted:
        rows["link_candidates"] = await _link_candidates(services.knowledge, owner)
    if "goal_candidates" in wanted:
        rows["goal_candidates"] = await _goal_candidates(services.learning, owner)
    if "skill_candidates" in wanted:
        rows["skill_candidates"] = await _skill_candidates(services.learning, owner)
    if "repository_candidates" in wanted:
        rows["repository_candidates"] = await _repository_candidates(services.developer, owner)
    return rows


async def _task_candidates(
    tasks: TaskService, owner: User, *, completable: bool
) -> tuple[RowCandidate, ...]:
    """The caller's most recent tasks, as reference targets.

    ``completable`` narrows the list to the statuses a completion can legally
    leave. It is the one status filter left in this module, and it is here
    because **the verb is the only thing that can justify it**: offering a
    completed card as something to complete produces a proposal whose
    confirmation is a guaranteed no-op, and a confirm dialog that is always a
    no-op is how a user learns not to read them. Narrowing unconditionally, as an
    earlier version of this function did, was wrong for the opposite reason — it
    made "delete the migration task I finished last week" unmatchable, because the
    row it names is exactly the row that is no longer open.

    So the two verbs get two lists: a delete, an update, a reschedule or a tag
    sees the caller's recent work whatever state it is in, and a completion sees
    only the rows there is still something to finish.
    """
    page = await tasks.list(
        owner=owner,
        limit=_CANDIDATE_LIMIT,
        sort="created_at",
        order="desc",
    )
    return tuple(
        RowCandidate(id=row.id, label=row.title)
        for row in page.items
        if not completable or row.status in _COMPLETABLE_STATUSES
    )


async def _project_candidates(projects: ProjectService, owner: User) -> tuple[RowCandidate, ...]:
    """The caller's most recent projects, as reference targets."""
    page = await projects.list(owner=owner, limit=_CANDIDATE_LIMIT)
    return tuple(RowCandidate(id=row.id, label=row.name) for row in page.items)


async def _note_candidates(knowledge: KnowledgeService, owner: User) -> tuple[RowCandidate, ...]:
    """The caller's most recently touched notes, as reference targets."""
    page = await knowledge.list_notes(owner=owner, limit=_CANDIDATE_LIMIT)
    return tuple(RowCandidate(id=row.id, label=row.title) for row in page.items)


async def _bookmark_candidates(
    knowledge: KnowledgeService, owner: User
) -> tuple[RowCandidate, ...]:
    """The caller's newest bookmarks, labelled by title or failing that by URL.

    A bookmark's title is optional, and a candidate with an empty label cannot be
    matched against anything — so the URL stands in. It is the name the user gave
    the row anyway, and the confirm dialog would show it either way.
    """
    page = await knowledge.list_bookmarks(owner=owner, limit=_CANDIDATE_LIMIT)
    return tuple(
        RowCandidate(id=row.id, label=(row.title or row.url).strip()) for row in page.items
    )


async def _concept_candidates(knowledge: KnowledgeService, owner: User) -> tuple[RowCandidate, ...]:
    """The caller's concepts, in the alphabetical order that list page uses."""
    page = await knowledge.list_concepts(owner=owner, limit=_CANDIDATE_LIMIT)
    return tuple(RowCandidate(id=row.id, label=row.name) for row in page.items)


async def _link_candidates(knowledge: KnowledgeService, owner: User) -> tuple[RowCandidate, ...]:
    """No link candidates, because the knowledge service has no read that yields them.

    **This is a gap in the read path, not a decision about links.** There is no
    owner-scoped read of the caller's edges that carries link *ids*, and a
    candidate without one cannot be confirmed against:

    * :meth:`KnowledgeService.list_links` takes a direction and a node, and
      refuses the call that asks for neither. There is no "all my links" page, so
      the alternatives are one call per note and per concept — up to a hundred
      owner-scoped queries on a single propose request, with no ordering to cap
      them honestly.
    * :meth:`KnowledgeService.graph` does return the whole graph in one bounded
      query, but :class:`~app.schemas.knowledge.KnowledgeGraphEdge` is endpoints
      and a link type: it has no ``id`` of its own. So even that read cannot
      produce a candidate confirm could act on.

    So a sentence that names a link is refused by the proposal layer with
    ``target_not_found``, which is the honest answer: NEXUS does not know which
    edge is meant, and a best guess here would delete or unlink the wrong one.
    :attr:`ActionKind.DELETE_LINK` and
    :attr:`ActionKind.CREATE_LINK` are fully implemented at confirm and work
    whenever the caller supplies the ``target_id`` itself.

    Returns:
        Always empty. Kept as a function rather than deleted so that the fix is
        one body when the knowledge service grows an owner-scoped edge read that
        carries ids, and so that the absence is documented next to the nine
        candidate lists that do work.
    """
    return ()


async def _goal_candidates(
    learning: LearningIntelligenceService, owner: User
) -> tuple[RowCandidate, ...]:
    """The caller's learning goals, as reference targets."""
    page = await learning.list_goals(owner=owner, limit=_CANDIDATE_LIMIT)
    return tuple(RowCandidate(id=row.id, label=row.title) for row in page.items)


async def _skill_candidates(
    learning: LearningIntelligenceService, owner: User
) -> tuple[RowCandidate, ...]:
    """The caller's tracked skills, as reference targets.

    Goals and skills are read into two separate lists rather than one, and the
    point is not tidiness. ``learning_track`` carries both a status vocabulary and
    a priority vocabulary, so a goal called "High availability" and a skill called
    "Rust" can both be named by a sentence that is really about a status or a
    grade — and the proposal layer picks one list to match against. Splitting
    them means the loser is a ``target_ambiguous`` refusal, which asks the user
    to be plainer, rather than a silent match against the wrong table.
    """
    goals = await learning.list_goals(owner=owner, limit=_CANDIDATE_LIMIT)
    skills = await learning.list_skills(owner=owner, limit=_CANDIDATE_LIMIT)
    goal_rows = [RowCandidate(id=row.id, label=row.title) for row in goals.items]
    taken = {row.label.strip().casefold() for row in goal_rows}
    skill_rows = (
        RowCandidate(id=row.id, label=row.name)
        for row in skills.items
        if row.name.strip().casefold() not in taken
    )
    return (*goal_rows, *skill_rows)


async def _repository_candidates(
    developer: DeveloperIntelligenceService, owner: User
) -> tuple[RowCandidate, ...]:
    """The caller's repositories, as reference targets.

    Fetched for every ``developer_intel`` utterance, which is one page read more
    than that surface used to cost — a registration is the only other kind on it,
    and it matches nothing by name. The read is what makes "remove Swift pdf from
    repo" act rather than route: without a candidate list the proposal layer has
    nothing to resolve the phrase against and the honest answer is
    ``target_not_found``, which is a refusal rather than a delete of whatever the
    folder nearest the phrase happened to be.

    The label is the repository's own name rather than its path, because the name
    is what a person says and what the confirm dialog quotes. The path is what
    the *registration* is keyed on, and reading it here would make every sentence
    carry a string nobody types out loud.
    """
    page = await developer.list_repositories(owner=owner, limit=_CANDIDATE_LIMIT)
    return tuple(
        RowCandidate(id=row.id, label=(row.name or _folder_name(row.local_path)))
        for row in page.items
    )


def _folder_name(local_path: str) -> str:
    """The last segment of a stored repository path, for a row named nothing.

    ``register_repository`` falls back to the folder's own name when the caller
    supplied no label, so a row without one is a row this endpoint wrote for a
    path the user gave. A candidate with an empty label matches nothing at all —
    :func:`~app.ml.actions.extraction.match_reference` drops it rather than
    letting it match everything — so the folder's own name stands in, and it is
    the same name that row already carries in the developer surface's own list.
    """
    segments = [segment for segment in local_path.replace("\\", "/").split("/") if segment]
    return segments[-1] if segments else local_path


async def _event_candidates(planner: PlannerService, owner: User) -> tuple[RowCandidate, ...]:
    """The caller's nearest events, as reference targets.

    ``list_events`` takes a half-open overlap window and defaults to one around
    the present, so this is the calendar a sentence about "today" or "tomorrow"
    could mean — and not the caller's whole history, which is deliberate: an event
    two years ago is not what "move the design review" refers to, and offering it
    would make almost every reference ambiguous.
    """
    page = await planner.list_events(owner=owner, limit=_CANDIDATE_LIMIT)
    return tuple(RowCandidate(id=row.id, label=row.title) for row in page.items)


async def _session_candidates(planner: PlannerService, owner: User) -> tuple[RowCandidate, ...]:
    """The caller's nearest work sessions, labelled by the window they hold.

    A session has no title, so the label is the thing a person recognises it by.
    Rendered without a zone suffix because the planner already renders its own
    times in the caller's zone, and a second offset here would disagree with it.
    """
    page = await planner.list_sessions(owner=owner, limit=_CANDIDATE_LIMIT)
    return tuple(
        RowCandidate(
            id=row.id,
            label=f"{row.scheduled_start:%Y-%m-%d %H:%M} to {row.scheduled_end:%H:%M}",
        )
        for row in page.items
    )


def _proposal_context(
    *,
    tz: Any,
    now: datetime,
    project_id: UUID | None,
    project_label: str | None,
    candidates: Mapping[str, tuple[RowCandidate, ...]],
) -> ProposalContext:
    """Assemble the proposal layer's view of the world.

    The candidate lists are attached **by name**, onto whichever members
    :class:`~app.ml.actions.proposals.ProposalContext` actually declares rather
    than onto a name this router assumes it declares. Passing a member the
    dataclass does not have is a ``TypeError`` — a 500 on an ordinary request,
    caused by a mismatch between two modules rather than by anything the user
    did — so the declared field list is read once and only names it recognises
    are attached.

    When a list this router gathered has nowhere to go, that is a defect in the
    mapping above rather than a condition a caller can act on. It is logged and
    not raised: the request still gets a truthful answer, and the answer is a
    ``target_not_found`` refusal for the references that list would have resolved.
    A proposal that names no row is safe; a proposal that names the wrong one is
    not, and that is the case worth being loud about.
    """
    declared = {item.name for item in fields(ProposalContext)}
    attached = {name: rows for name, rows in candidates.items() if name in declared}
    dropped = sorted(set(candidates) - set(attached))
    if dropped:
        log_event(
            logger,
            logging.WARNING,
            "ml_action_candidate_lists_dropped",
            fields=dropped,
            reason="ProposalContext declares no such member; the rows were read but not attached.",
        )
    return ProposalContext(
        tz=tz,
        now=now,
        project_id=project_id,
        project_label=project_label,
        **attached,
    )


# --------------------------------------------------------------------------- #
# Confirm
# --------------------------------------------------------------------------- #


@router.post(
    "/action/confirm",
    response_model=ConfirmActionRead,
    summary="Carry out a proposal the user has confirmed",
    description=(
        "Re-derives the action from `kind`, re-checks the permission, refuses a "
        "destructive kind that was not confirmed as destructive, re-validates the "
        "payload against the schema that kind names and re-resolves every id "
        "through an owner-scoped lookup, then calls the same service a typed "
        "request would have. A repeated confirm reports the existing row rather "
        "than creating a second one."
    ),
    dependencies=_ANALYTICS_READ,
)
async def confirm_action(
    body: ConfirmActionRequest,
    current_user: AuthenticatedUser,
    services: _ServicesDep,
) -> ConfirmActionRead:
    """Execute one confirmed proposal, and report only what the service confirmed.

    **Nothing in the body is believed.** ``kind`` names the
    :class:`~app.ml.actions.proposals.ActionSpec` — and therefore the payload
    schema, the permission and the entry point — from a frozen table the request
    cannot influence. The payload is validated against that schema as untrusted
    input, unknown keys included, so a field the user edited into the confirm
    dialog is checked rather than honoured. Every id is resolved through an
    owner-scoped service method, which is what turns a forged foreign id into a
    404 before any row is touched.

    **A destructive kind has to say so.** The check is against
    :attr:`~app.ml.actions.proposals.ActionSpec.destructive` — the same field the
    confirm dialog read to decide whether to warn — and not against the client's
    own claim about the kind, which would make the flag a thing the untrusted
    party sets. Deleting a row is a normal thing for a user to ask for and a
    normal thing for the assistant to do; what makes it safe is that the sentence
    was read first, the warning was shown, the user pressed the button that
    carries the warning, and the row it removes was re-resolved as the caller's
    own immediately before it went.

    **A delete removes one row.** There is no code path here that takes a
    collection: the id comes from the single ``target_id`` the proposal carried,
    and the proposal layer refuses a request that named more than one row with
    ``destructive_request``. One row is not always one *table* — deleting a
    project takes its tasks, deleting a repository takes its commits, branches and
    scan runs through the schema's cascade — and the confirm sentence is where
    that is stated, because it is the only place the user is told what the press
    will take with it.

    **The activity trail is written by the service, not by this router.** Every
    service arrives from :mod:`app.api.deps`, which wires ``activity=`` for all
    of them, so a task created here writes the same ``TASK_CREATED`` row a
    hand-typed ``POST /tasks`` writes.

    Errors: 401 unauthenticated, 403 without ``analytics.read`` or without the
    permission this particular action needs, 404 for a target the caller does not
    own, 422 for a kind outside the set, an ``intent`` that kind cannot have come
    from, a destructive kind without ``confirm_destructive``, a missing
    ``target_id``, or a payload the kind's schema does not accept.
    """
    spec = ACTION_SPECS[body.kind]
    if spec.destructive and not body.confirm_destructive:
        raise ValidationError(
            "This action discards a row. Send confirm_destructive=true to carry out "
            "a proposal the user was shown as destructive.",
            details={"kind": str(body.kind), "destructive": True},
        )
    if not has_permission(current_user.role, spec.permission):
        raise ForbiddenError("You do not have permission to perform this action.")
    allowed = {str(spec.intent), *(str(other) for other in spec.also_intents)}
    if body.intent not in allowed:
        raise ValidationError(
            "This payload was not proposed from that intent.",
            details={
                "expected_intent": str(spec.intent),
                "accepted_intents": sorted(allowed),
                "supplied_intent": body.intent,
            },
        )

    data = _validated_payload(spec, body.payload)
    handler = _HANDLERS.get(body.kind)
    if handler is None:  # pragma: no cover — the table and the enum are both closed
        raise ValidationError(
            "This action cannot be carried out.",
            details={"kind": str(body.kind), "reason": "no_dispatcher"},
        )
    result = await handler(services, current_user, body, data)

    log_event(
        logger,
        logging.INFO,
        "ml_action_confirmed",
        kind=str(body.kind),
        entity=result.entity,
        entity_id=str(result.entity_id),
        outcome=result.outcome,
        applied=result.applied,
    )
    return result


def _validated_payload(spec: ActionSpec, raw: dict[str, Any]) -> BaseModel:
    """Validate an untrusted payload against the schema the spec names.

    Two rejections happen before Pydantic's, and both matter more than the ones
    it does itself:

    * **Unknown keys.** ``TaskCreate`` and ``LearningGoalWriteBase`` do not set
      ``extra="forbid"``, so Pydantic's default would drop a key the client sent
      and answer 200 — leaving a client that read the 200 as "the change was
      applied" believing something false about their own work.
    * **An empty payload, but only where emptiness is meaningless.** The
      no-argument kinds — every delete, an archive, a publish, a completion — carry
      their whole instruction in ``target_id`` and the sentence the user read, so
      they legitimately send ``{}`` and refusing that would make the destructive
      half of the surface unusable. A kind whose schema *does* have required
      fields is a different case: ``{}`` there is a caller who has not said what
      to create, and naming it as a 422 is better than surfacing it as a
      confusing per-field error.

    Raises:
        ValidationError: 422 for an unknown key, an empty payload where the
            schema has required fields, or any failure of the spec's own model.
    """
    unknown = sorted(set(raw) - set(spec.schema.model_fields))
    if unknown:
        raise ValidationError(
            f"This action's payload has no field named {unknown[0]!r}.",
            details={"unknown_fields": unknown, "expected": sorted(spec.schema.model_fields)},
        )
    if not raw and _required_fields(spec.schema):
        raise ValidationError(
            "This action's payload is empty.",
            details={"required_fields": sorted(_required_fields(spec.schema))},
        )
    try:
        return spec.schema.model_validate(raw)
    except PydanticValidationError as exc:
        # ``exc.errors()`` entries carry a ``ctx`` holding the original exception
        # object for a ``model_validator``, which is not JSON-serialisable — so the
        # field, the message and the error type are lifted out explicitly. Passing
        # the raw structure would turn a client's bad payload into a 500 from the
        # error renderer, which is the opposite of the 422 it is supposed to be.
        raise ValidationError(
            "The confirmed payload is not valid for this action.",
            details={
                "errors": [
                    {
                        "field": ".".join(str(part) for part in error["loc"]) or "payload",
                        "message": error["msg"],
                        "type": error["type"],
                    }
                    for error in exc.errors(include_url=False)
                ]
            },
        ) from exc


def _required_fields(schema: type[BaseModel]) -> frozenset[str]:
    """The names a schema cannot be constructed without."""
    return frozenset(name for name, field in schema.model_fields.items() if field.is_required())


# --------------------------------------------------------------------------- #
# Confirm: creations
# --------------------------------------------------------------------------- #


async def _create_task(
    services: _Services, owner: User, body: ConfirmActionRequest, data: BaseModel
) -> ConfirmActionRead:
    """Create one task, or name the one already on the board.

    ``TaskService.create`` re-checks the project through its own scoped lookup, so
    a forged ``project_id`` is a 404 with no row written. The service also writes
    ``TASK_CREATED`` beside the insert; this router writes nothing itself.
    """
    create = _expect(data, TaskCreate)
    existing = await _existing(
        services.tasks.list(
            owner=owner,
            project_id=create.project_id,
            search=create.title,
            limit=_DUPLICATE_SCAN,
        ),
        create.title,
        "title",
    )
    if existing is not None:
        return _read(
            ActionKind.CREATE_TASK,
            existing,
            outcome="no_op",
            applied=False,
            message=(
                f"A task titled '{create.title}' is already on that board; "
                "NEXO did not create a second one."
            ),
        )
    task = await services.tasks.create(owner=owner, data=create)
    return _read(
        ActionKind.CREATE_TASK,
        task,
        outcome="created",
        applied=True,
        message=f"Created the task '{task.title}'.",
    )


async def _create_project(
    services: _Services, owner: User, body: ConfirmActionRequest, data: BaseModel
) -> ConfirmActionRead:
    """Create one project, or name the one that already exists."""
    create = _expect(data, ProjectCreate)
    existing = await _existing(
        services.projects.list(owner=owner, search=create.name, limit=_DUPLICATE_SCAN),
        create.name,
        "name",
    )
    if existing is not None:
        return _read(
            ActionKind.CREATE_PROJECT,
            existing,
            outcome="no_op",
            applied=False,
            message=(
                f"A project named '{create.name}' already exists; NEXO did not create a second one."
            ),
        )
    project = await services.projects.create(owner=owner, data=create)
    return _read(
        ActionKind.CREATE_PROJECT,
        project,
        outcome="created",
        applied=True,
        message=f"Created the project '{project.name}'.",
    )


async def _create_note(
    services: _Services, owner: User, body: ConfirmActionRequest, data: BaseModel
) -> ConfirmActionRead:
    """Create one note, or name the one already in the knowledge base."""
    create = _expect(data, NoteCreate)
    existing = await _existing(
        services.knowledge.list_notes(owner=owner, search=create.title, limit=_DUPLICATE_SCAN),
        create.title,
        "title",
    )
    if existing is not None:
        return _read(
            ActionKind.CREATE_NOTE,
            existing,
            outcome="no_op",
            applied=False,
            message=(
                f"A note titled '{create.title}' already exists; NEXO did not create a second one."
            ),
        )
    note = await services.knowledge.create_note(owner=owner, data=create)
    return _read(
        ActionKind.CREATE_NOTE,
        note,
        outcome="created",
        applied=True,
        message=f"Created the note '{note.title}'.",
    )


async def _create_bookmark(
    services: _Services, owner: User, body: ConfirmActionRequest, data: BaseModel
) -> ConfirmActionRead:
    """Save a URL the caller named.

    **No duplicate scan here, and the reason is that there is nothing to scan
    against.** A bookmark is identified by its URL, which the extractor reads out
    of the sentence itself: a second save of the same address is either the user
    doing it on purpose — the row carries a description and a title they may have
    changed — or a replay of the first, and the replay is caught by the user
    seeing the same bookmark twice rather than by a page read in front of every
    save. The other creation kinds kept their scan because a title is a name two
    different rows can legitimately share.
    """
    create = _expect(data, BookmarkCreate)
    bookmark = await services.knowledge.create_bookmark(owner=owner, data=create)
    return _read(
        ActionKind.CREATE_BOOKMARK,
        bookmark,
        outcome="created",
        applied=True,
        message=f"Saved the bookmark '{bookmark.title or bookmark.url}'.",
    )


async def _create_concept(
    services: _Services, owner: User, body: ConfirmActionRequest, data: BaseModel
) -> ConfirmActionRead:
    """Record a concept.

    The service refuses a second concept of the same name with a 409, which is
    the answer a duplicate should get here too — so there is no scan in front of
    it. A scan would have to decide that "Async" and "async" are the same concept,
    and that is a case-folding rule this router would be inventing.
    """
    create = _expect(data, ConceptCreate)
    concept = await services.knowledge.create_concept(owner=owner, data=create)
    return _read(
        ActionKind.CREATE_CONCEPT,
        concept,
        outcome="created",
        applied=True,
        message=f"Recorded the concept '{concept.name}'.",
    )


async def _create_link(
    services: _Services, owner: User, body: ConfirmActionRequest, data: BaseModel
) -> ConfirmActionRead:
    """Record one directed edge between two rows the caller owns.

    Both endpoints were resolved by the proposal layer against the candidate lists
    this router gathered, and ``KnowledgeService.create_link`` re-resolves them
    again before the write, so a client that swaps an endpoint for somebody else's
    id gets a 404 rather than an edge pointing out of the knowledge base.
    """
    create = _expect(data, KnowledgeLinkCreate)
    link = await services.knowledge.create_link(owner=owner, data=create)
    return _read(
        ActionKind.CREATE_LINK,
        link,
        outcome="created",
        applied=True,
        message=f"Linked {create.source_type} to {create.target_type}.",
    )


async def _create_learning_goal(
    services: _Services, owner: User, body: ConfirmActionRequest, data: BaseModel
) -> ConfirmActionRead:
    """Create one learning goal, or name the one already recorded.

    ``create_goal`` takes flat keyword arguments rather than a payload model, so
    the validated :class:`~app.schemas.learning.LearningGoalWrite` is spread into
    them. ``exclude_unset`` keeps the service's own defaults for fields the user
    never named, and ``exclude_none`` stops an explicit null from being written
    onto a column that was never meant to be cleared — ``progress=None`` in
    particular is not the same as ``progress=0``. Every pointer in the write
    (``project_id``, ``note_id``, ``target_skill_id``) is proved to belong to the
    caller inside the service, so a forged one is a 404 and the goal is not
    written.
    """
    goal = _expect(data, LearningGoalWrite)
    page = await services.learning.list_goals(owner=owner, limit=_DUPLICATE_SCAN)
    existing = _exact_title(page.items, goal.title)
    if existing is not None:
        return _read(
            ActionKind.CREATE_LEARNING_GOAL,
            existing,
            outcome="no_op",
            applied=False,
            message=(
                f"A learning goal titled '{goal.title}' is already recorded; "
                "NEXO did not create a second one."
            ),
        )
    created = await services.learning.create_goal(
        owner=owner, **_set_fields(goal, exclude_none=True)
    )
    return _read(
        ActionKind.CREATE_LEARNING_GOAL,
        created,
        outcome="created",
        applied=True,
        message=f"Created the learning goal '{created.title}'.",
    )


async def _create_skill(
    services: _Services, owner: User, body: ConfirmActionRequest, data: BaseModel
) -> ConfirmActionRead:
    """Track one skill, at the level the user says they are at.

    ``create_skill`` takes flat keywords, so the validated
    :class:`~app.schemas.learning.SkillWrite` is spread into them with the same
    ``exclude_unset``/``exclude_none`` pair the goal uses. The service refuses a
    name this account already tracks with a 409, which is the right answer to a
    duplicate here and the reason there is no scan in front of it.

    ``current_level`` is passed through as the caller sent it, and the service
    records the row as ``user_defined`` whatever it is: a level the client supplied
    is a claim the client is making, and NEXUS never files its own absence of an
    estimate as one.
    """
    skill = _expect(data, SkillWrite)
    created = await services.learning.create_skill(
        owner=owner, **_set_fields(skill, exclude_none=True)
    )
    return _read(
        ActionKind.CREATE_SKILL,
        created,
        outcome="created",
        applied=True,
        message=f"Started tracking the skill '{created.name}'.",
    )


async def _create_repository(
    services: _Services, owner: User, body: ConfirmActionRequest, data: BaseModel
) -> ConfirmActionRead:
    """Register one local repository from the folder the sentence named.

    **The path is the whole instruction, so nothing here may substitute for it.**
    :class:`~app.schemas.developer.RepositoryCreate` requires ``local_path`` and
    the proposal layer refuses a sentence that named no folder rather than
    inventing one, so by the time this runs the one field that matters was read
    out of the utterance rather than inferred from it.

    ``register_repository`` takes flat keywords, so the validated payload is
    spread into them with the same ``exclude_unset``/``exclude_none`` pair the
    goal and the skill use. A field the user never named is not a field this
    router fills in — which is how ``project_id`` reaches the service only when
    the caller actually supplied one, and how a ``is_active`` or a ``description``
    the user typed into the confirm dialog is forwarded rather than dropped.

    **The duplicate check compares normalised paths, not strings.** The service
    stores the *resolved* absolute path, which on Windows is backslashed and
    case-folded, while the payload still carries what the user said; an exact
    comparison would miss the very replay it exists to catch and let the service
    answer 409 instead. :func:`_existing_path` normalises both sides first.
    Missing it there is harmless — the service's own 409 is a correct answer — so
    this read improves the tone of a duplicate, not its correctness.

    **The project pointer is proved inside the service**, exactly as a forged
    ``project_id`` is on a task: ``register_repository`` looks the project up by
    id and owner before it writes anything, so another account's project is a 404
    and no row is created. Resolving it a second time here would be the same
    lookup twice.

    **No scan is triggered.** A registration stores a path; reading the work tree
    is ``POST /developer/repositories/{id}/scan`` and stays that call's job, so
    confirming this leaves every figure on the dashboard exactly as it was.
    """
    create = _expect(data, RepositoryCreate)
    existing = await _existing_path(
        services.developer.list_repositories(owner=owner, limit=_DUPLICATE_SCAN),
        create.local_path,
    )
    if existing is not None:
        return _read(
            ActionKind.CREATE_REPOSITORY,
            existing,
            outcome="no_op",
            applied=False,
            message=(
                f"The repository '{existing.name}' at {existing.local_path} is already "
                "registered; NEXO did not register it a second time."
            ),
        )
    repository = await services.developer.register_repository(
        owner=owner,
        **_set_fields(create, exclude_none=True),
    )
    return _read(
        ActionKind.CREATE_REPOSITORY,
        repository,
        outcome="created",
        applied=True,
        message=f"Registered the repository '{repository.name}' at {repository.local_path}.",
    )


async def _create_event(
    services: _Services, owner: User, body: ConfirmActionRequest, data: BaseModel
) -> ConfirmActionRead:
    """Book a window on the caller's calendar.

    ``PlannerService.create_event`` checks the window against what is already
    reserved before the row is written and raises a 409 naming the conflict, so an
    overlapping booking is refused by the same code that refuses it for a typed
    ``POST /calendar/events``.
    """
    create = _expect(data, CalendarEventCreate)
    event = await services.planner.create_event(owner=owner, data=create)
    return _read(
        ActionKind.CREATE_EVENT,
        event,
        outcome="created",
        applied=True,
        message=f"Booked '{event.title}' on your calendar.",
    )


async def _create_session(
    services: _Services, owner: User, body: ConfirmActionRequest, data: BaseModel
) -> ConfirmActionRead:
    """Log a block of work.

    ``WorkSessionCreate`` requires both ends of the window, so an utterance that
    named a day and no time cannot reach this handler with a payload that
    validates — the proposal layer assumes a stated window and prints it in the
    sentence the user confirmed, which is what makes assuming one safe. When the
    extractor could not recover even a day, it refuses instead and this handler is
    never reached.

    ``estimated_minutes`` is left exactly as the payload carried it. The service
    copies it rather than looking it up on the task, because a session records what
    was planned for this block and not the task's whole estimate; filling it in
    here would be exactly the overwrite that turns a 30-minute slice into a
    four-hour booking.
    """
    create = _expect(data, WorkSessionCreate)
    session = await services.planner.create_session(owner=owner, data=create)
    return _read(
        ActionKind.CREATE_SESSION,
        session,
        outcome="created",
        applied=True,
        message=(
            f"Logged a work session from {session.scheduled_start:%Y-%m-%d %H:%M} "
            f"to {session.scheduled_end:%H:%M}."
        ),
    )


# --------------------------------------------------------------------------- #
# Confirm: updates
# --------------------------------------------------------------------------- #


async def _update_task(
    services: _Services, owner: User, body: ConfirmActionRequest, data: BaseModel
) -> ConfirmActionRead:
    """Change the fields the sentence named on one task, and only those.

    ``TaskUpdate`` is a PATCH model and is handed to the service whole, so a field
    sent as an explicit ``null`` still clears its column — the service's
    ``exclude_unset`` is what distinguishes "clear it" from "leave it alone", and
    re-dumping the payload here would collapse the two.

    The snapshot is taken **before** the call and read off the caller's own
    ``title``, because ``update_fields`` mutates the very instance it is passed.
    """
    update = _expect(data, TaskUpdate)
    task = await services.tasks.get(task_id=_target(body, "task"), owner=owner)
    title = task.title
    updated = await services.tasks.update(task=task, data=update, owner=owner)
    changed = _differs(task, updated, update.model_fields_set)
    return _read(
        ActionKind.UPDATE_TASK,
        updated,
        outcome="updated" if changed else "no_op",
        applied=changed,
        message=(
            f"Updated the task '{title}'."
            if changed
            else f"The task '{title}' already says that; nothing changed."
        ),
    )


async def _update_project(
    services: _Services, owner: User, body: ConfirmActionRequest, data: BaseModel
) -> ConfirmActionRead:
    """Change the fields the sentence named on one project.

    ``ProjectUpdate`` has no ``status`` member — a transition is
    :class:`~app.ml.actions.proposals.ActionKind.SET_PROJECT_STATUS` — so this
    handler cannot write the column by accident even if a client tries.
    """
    update = _expect(data, ProjectUpdate)
    project = await services.projects.get(project_id=_target(body, "project"), owner=owner)
    name = project.name
    updated = await services.projects.update(project=project, data=update, owner=owner)
    changed = _differs(project, updated, update.model_fields_set)
    return _read(
        ActionKind.UPDATE_PROJECT,
        updated,
        outcome="updated" if changed else "no_op",
        applied=changed,
        message=(
            f"Updated the project '{name}'."
            if changed
            else f"The project '{name}' already says that; nothing changed."
        ),
    )


async def _update_note(
    services: _Services, owner: User, body: ConfirmActionRequest, data: BaseModel
) -> ConfirmActionRead:
    """Change a note's text, and nothing else.

    ``NoteUpdate`` carries no ``status`` and no ``document_id``, so the archived
    and published states stay behind the archive and publish actions and a note's
    provenance cannot be re-pointed by an edit.
    """
    update = _expect(data, NoteUpdate)
    note = await services.knowledge.get_note(note_id=_target(body, "note"), owner=owner)
    title = note.title
    updated = await services.knowledge.update_note(note=note, data=update, owner=owner)
    changed = _differs(note, updated, update.model_fields_set)
    return _read(
        ActionKind.UPDATE_NOTE,
        updated,
        outcome="updated" if changed else "no_op",
        applied=changed,
        message=(
            f"Updated the note '{title}'."
            if changed
            else f"The note '{title}' already says that; nothing changed."
        ),
    )


async def _update_event(
    services: _Services, owner: User, body: ConfirmActionRequest, data: BaseModel
) -> ConfirmActionRead:
    """Move or reword one calendar event.

    The service re-checks the window against the persisted row when a PATCH moves
    only one end of it, and re-resolves ``project_id`` and ``task_id`` through its
    own scoped lookups, so a forged pointer is a 404 with nothing written.
    """
    update = _expect(data, CalendarEventUpdate)
    event = await services.planner.get_event(event_id=_target(body, "event"), owner=owner)
    title = event.title
    updated = await services.planner.update_event(event=event, data=update, owner=owner)
    changed = _differs(event, updated, update.model_fields_set)
    return _read(
        ActionKind.UPDATE_EVENT,
        updated,
        outcome="updated" if changed else "no_op",
        applied=changed,
        message=(
            f"Updated the event '{title}'."
            if changed
            else f"The event '{title}' already says that; nothing changed."
        ),
    )


async def _update_learning_goal(
    services: _Services, owner: User, body: ConfirmActionRequest, data: BaseModel
) -> ConfirmActionRead:
    """Change the fields the sentence named on one learning goal.

    ``update_goal`` takes a mapping rather than a PATCH model and **refuses an
    empty one**, so the payload is dumped with ``exclude_unset`` — a field the user
    never mentioned is not in the mapping and cannot clear its column. ``None`` is
    *not* excluded here, because for this service an explicit null means "write
    SQL NULL" and is how a description is blanked; that is the opposite of the rule
    the goal *creation* follows, where a null is absent.

    The snapshot is the stored values, taken before the call, because the
    repository mutates the row it is handed.
    """
    goal = _expect(data, LearningGoalWrite)
    values = _set_fields(goal, exclude_none=False)
    identifier = _target(body, "learning goal")
    before = await services.learning.get_goal(owner=owner, goal_id=identifier)
    snapshot = {name: getattr(before, name, None) for name in values}
    title = before.title
    updated = await services.learning.update_goal(owner=owner, goal_id=identifier, values=values)
    changed = any(getattr(updated, name, None) != value for name, value in snapshot.items())
    return _read(
        ActionKind.UPDATE_LEARNING_GOAL,
        updated,
        outcome="updated" if changed else "no_op",
        applied=changed,
        message=(
            f"Updated the learning goal '{title}'."
            if changed
            else f"The learning goal '{title}' already says that; nothing changed."
        ),
    )


async def _update_profile(
    services: _Services, owner: User, body: ConfirmActionRequest, data: BaseModel
) -> ConfirmActionRead:
    """Change the caller's own profile.

    **The only kind here with no target to resolve**, and that is the whole reason
    it is safe: the row is the authenticated user, so there is no id in the body
    that could name somebody else's account. ``UserUpdate`` cannot carry an email
    or a password — both are identity transitions with their own endpoints and
    their own rules — and ``extra="forbid"`` means a client that tries is told so
    rather than answered with a cheerful 200 that dropped the field.

    The snapshot is the three writable columns read before the call. The service
    returns the unchanged account when nothing writable was sent, so without this
    a replay would otherwise be reported as an edit.
    """
    update = _expect(data, UserUpdate)
    sent = tuple(update.model_fields_set)
    before = {name: getattr(owner, name, None) for name in sent}
    account = await services.users.update(owner, update)
    changed = any(getattr(account, name, None) != value for name, value in before.items())
    shown = account.display_name or account.username or "your profile"
    return ConfirmActionRead(
        kind=ActionKind.UPDATE_PROFILE,
        entity=_ENTITY_BY_KIND[ActionKind.UPDATE_PROFILE],
        entity_id=account.id,
        outcome="updated" if changed else "no_op",
        applied=changed,
        message=(
            f"Updated the profile for '{shown}'."
            if changed
            else f"The profile for '{shown}' already says that; nothing changed."
        ),
    )


# --------------------------------------------------------------------------- #
# Confirm: deletions
# --------------------------------------------------------------------------- #
#
# Every handler below follows the same three steps, and the repetition is the
# point rather than an accident of style:
#
# 1. **Resolve owner-scoped.** ``tasks.get`` / ``knowledge.get_note`` and its
#    siblings carry ``owner_id`` in the query, so another account's id is a 404 and
#    the row is never loaded. A permission check on an id that already proved it
#    exists would be weaker, and would answer 403 — which confirms the id is real.
# 2. **Capture the name.** The label is read off the resolved row so the sentence
#    can name what went, from storage rather than from the request that asked.
# 3. **Discard through the service**, which cascades and writes its own activity
#    row. Nothing here touches a session or a table.
#
# ``confirm_destructive`` was already checked against the spec table by
# :func:`confirm_action` before any of this ran.


async def _delete_task(
    services: _Services, owner: User, body: ConfirmActionRequest, data: BaseModel
) -> ConfirmActionRead:
    """Delete one task, and the sessions and completions hanging off it.

    The cascade is the service's, not this router's: ``TaskService.delete`` drops
    the attached rows in the same transaction and records ``TASK_DELETED``, which
    is why the typed route and this one leave identical state behind.
    """
    task = await services.tasks.get(task_id=_target(body, "task"), owner=owner)
    await services.tasks.delete(task=task, owner=owner)
    return _read(
        ActionKind.DELETE_TASK,
        task,
        outcome="deleted",
        applied=True,
        message=_deleted("task", task.title),
    )


async def _delete_project(
    services: _Services, owner: User, body: ConfirmActionRequest, data: BaseModel
) -> ConfirmActionRead:
    """Delete one project.

    A project owns its tasks, so this is the widest single-row delete in the app
    and the sentence the user confirmed says so. The service performs the cascade;
    a task that is a subtask of another is re-parented or refused by the same rule
    that guards ``DELETE /projects/{id}``.
    """
    project = await services.projects.get(project_id=_target(body, "project"), owner=owner)
    await services.projects.delete(project=project, owner=owner)
    return _read(
        ActionKind.DELETE_PROJECT,
        project,
        outcome="deleted",
        applied=True,
        message=_deleted("project", project.name),
    )


async def _delete_note(
    services: _Services, owner: User, body: ConfirmActionRequest, data: BaseModel
) -> ConfirmActionRead:
    """Delete one note.

    The note's revisions are the record of how it was written, and the service's
    cascade takes them with it. That is a real loss, which is why ``archive_note``
    exists as a separate kind: a user who wants it out of the way can archive it
    and keep the history, and only the sentence that says *delete* gets here.
    """
    note = await services.knowledge.get_note(note_id=_target(body, "note"), owner=owner)
    await services.knowledge.delete_note(note=note, owner=owner)
    return _read(
        ActionKind.DELETE_NOTE,
        note,
        outcome="deleted",
        applied=True,
        message=_deleted("note", note.title),
    )


async def _delete_bookmark(
    services: _Services, owner: User, body: ConfirmActionRequest, data: BaseModel
) -> ConfirmActionRead:
    """Delete one saved URL."""
    bookmark = await services.knowledge.get_bookmark(
        bookmark_id=_target(body, "bookmark"), owner=owner
    )
    label = (bookmark.title or bookmark.url).strip()
    await services.knowledge.delete_bookmark(bookmark=bookmark, owner=owner)
    return _read(
        ActionKind.DELETE_BOOKMARK,
        bookmark,
        outcome="deleted",
        applied=True,
        message=_deleted("bookmark", label),
    )


async def _delete_concept(
    services: _Services, owner: User, body: ConfirmActionRequest, data: BaseModel
) -> ConfirmActionRead:
    """Delete one concept.

    The edges pointing at it are removed by the database's cascade rather than by
    this router, which is why there is no second read here to find and unlink them.
    """
    concept = await services.knowledge.get_concept(concept_id=_target(body, "concept"), owner=owner)
    await services.knowledge.delete_concept(concept=concept, owner=owner)
    return _read(
        ActionKind.DELETE_CONCEPT,
        concept,
        outcome="deleted",
        applied=True,
        message=_deleted("concept", concept.name),
    )


async def _delete_link(
    services: _Services, owner: User, body: ConfirmActionRequest, data: BaseModel
) -> ConfirmActionRead:
    """Delete one edge, leaving both of the rows it joined.

    A link is the cheapest thing in the knowledge base to lose and the most
    annoying to have dangling, so removing one is a delete rather than an archive:
    the two endpoints keep whatever else pointed at them.
    """
    link = await services.knowledge.get_link(link_id=_target(body, "link"), owner=owner)
    await services.knowledge.delete_link(link=link, owner=owner)
    return _read(
        ActionKind.DELETE_LINK,
        link,
        outcome="deleted",
        applied=True,
        message=_deleted("link", f"{link.source_type} → {link.target_type}"),
    )


async def _delete_learning_goal(
    services: _Services, owner: User, body: ConfirmActionRequest, data: BaseModel
) -> ConfirmActionRead:
    """Delete one learning goal, and keep the record that they worked on it.

    The activities recorded towards the goal survive with their pointer dropped.
    That is the service's design and the reason this delete is offered at all: a
    user who has abandoned a goal has not unlearned anything, and the evidence is
    a history rather than a row.
    """
    identifier = _target(body, "learning goal")
    goal = await services.learning.get_goal(owner=owner, goal_id=identifier)
    await services.learning.delete_goal(owner=owner, goal_id=identifier)
    return _read(
        ActionKind.DELETE_LEARNING_GOAL,
        goal,
        outcome="deleted",
        applied=True,
        message=_deleted("learning goal", goal.title),
    )


async def _delete_skill(
    services: _Services, owner: User, body: ConfirmActionRequest, data: BaseModel
) -> ConfirmActionRead:
    """Delete one skill and everything recorded against it.

    **The one delete in NEXUS that takes the user's own history with it.** The
    activities cascade, and the levels a skill carried went into the gap read as
    evidence. That is why the confirm sentence names the skill and the dialog
    warns: it is not recoverable, and no archive route stands in for it. The
    service does the cascade and drops the career-evidence pointer itself.
    """
    identifier = _target(body, "skill")
    skill = await services.learning.get_skill(owner=owner, skill_id=identifier)
    await services.learning.delete_skill(owner=owner, skill_id=identifier)
    return _read(
        ActionKind.DELETE_SKILL,
        skill,
        outcome="deleted",
        applied=True,
        message=_deleted("skill", skill.name),
    )


async def _delete_event(
    services: _Services, owner: User, body: ConfirmActionRequest, data: BaseModel
) -> ConfirmActionRead:
    """Delete one calendar event.

    The work sessions reserved inside the window are the service's to decide on:
    it refuses the delete rather than dropping them, and the refusal is the same
    one a typed ``DELETE /calendar/events/{id}`` gets.
    """
    event = await services.planner.get_event(event_id=_target(body, "event"), owner=owner)
    await services.planner.delete_event(event=event, owner=owner)
    return _read(
        ActionKind.DELETE_EVENT,
        event,
        outcome="deleted",
        applied=True,
        message=_deleted("event", event.title),
    )


async def _delete_session(
    services: _Services, owner: User, body: ConfirmActionRequest, data: BaseModel
) -> ConfirmActionRead:
    """Delete one work session."""
    session = await services.planner.get_session(
        session_id=_target(body, "work session"), owner=owner
    )
    await services.planner.delete_session(session=session, owner=owner)
    return _read(
        ActionKind.DELETE_SESSION,
        session,
        outcome="deleted",
        applied=True,
        message=(
            f"Deleted the work session from {session.scheduled_start:%Y-%m-%d %H:%M} "
            f"to {session.scheduled_end:%H:%M}."
        ),
    )


async def _delete_repository(
    services: _Services, owner: User, body: ConfirmActionRequest, data: BaseModel
) -> ConfirmActionRead:
    """Delete one repository, and everything that was ever observed under it.

    **The widest cascade on this surface, and the confirm sentence says so.**
    ``DeveloperIntelligenceService.delete_repository`` resolves the row through
    its own owner-scoped lookup and lets the schema's ``ON DELETE CASCADE`` take
    the commits, the branches and every scan run with it — deliberately not a
    deactivation, because a row that kept its history while claiming the
    repository was gone would leave the account's metrics reading commits from a
    work tree the user has explicitly removed. The row is read first so the
    message names what went from storage rather than from the phrase that asked.

    The folder on the user's disk is **not** touched: this removes a registration
    and what NEXUS recorded about it, and the confirm sentence is careful to say
    "delete the repository" rather than anything that reads as "delete the code".
    """
    identifier = _target(body, "repository")
    repository = await services.developer.get_repository(owner=owner, repository_id=identifier)
    await services.developer.delete_repository(owner=owner, repository_id=identifier)
    return _read(
        ActionKind.DELETE_REPOSITORY,
        repository,
        outcome="deleted",
        applied=True,
        message=(
            f"Deleted the repository '{repository.name}'; its commits, branches and "
            "scan runs went with it."
        ),
    )


# --------------------------------------------------------------------------- #
# Confirm: transitions, scheduling and tags
# --------------------------------------------------------------------------- #


async def _complete_task(
    services: _Services, owner: User, body: ConfirmActionRequest, data: BaseModel
) -> ConfirmActionRead:
    """Mark one of the caller's own tasks complete, or report that it already is.

    The target is re-resolved here through ``TaskService.get``, whose query is
    scoped by ``owner_id`` — so a forged ``target_id`` is a 404 and the row is
    never loaded, which is stronger than a permission check on an id that already
    proved it exists.

    The status read before the call is compared with the status returned after it,
    because ``TaskService.set_status`` returns the row unchanged when it is
    already in the target state. Reporting that as ``no_op`` is what keeps a
    replayed confirm from claiming a transition that did not happen.
    """
    return await _transition_task(services, owner, body, data, ActionKind.COMPLETE_TASK)


async def _set_task_status(
    services: _Services, owner: User, body: ConfirmActionRequest, data: BaseModel
) -> ConfirmActionRead:
    """Move one task to the state the sentence named.

    The same door as a completion — :meth:`TaskService.set_status` is the only way
    the column moves — reached through a different kind so the confirm sentence
    can say "mark as blocked" rather than "mark complete", and so
    :attr:`~app.ml.actions.proposals.ActionSpec.destructive` and the dialog read
    the two apart.
    """
    return await _transition_task(services, owner, body, data, ActionKind.SET_TASK_STATUS)


async def _transition_task(
    services: _Services,
    owner: User,
    body: ConfirmActionRequest,
    data: BaseModel,
    kind: ActionKind,
) -> ConfirmActionRead:
    """Move one task to the state its payload names, through the one legal door.

    ``set_status`` owns the transition table, so an illegal edge is refused by the
    service in exactly the words a typed ``PATCH /tasks/{id}/status`` would use.
    ``note`` rides along onto the activity event and is never written onto the
    card — "why is this blocked?" belongs to the history.

    The comparison is against a scalar read before the call, not against the row
    object: ``update_fields`` mutates and refreshes the instance it is given, so
    the two are the same object afterwards.
    """
    change = _expect(data, TaskStatusChange)
    task = await services.tasks.get(task_id=_target(body, "task"), owner=owner)
    before = task.status
    updated = await services.tasks.set_status(
        task=task,
        status=change.status,
        owner=owner,
        note=change.note,
    )
    if updated.status == before:
        return _read(
            kind,
            updated,
            outcome="no_op",
            applied=False,
            message=(
                f"The task '{task.title}' is already {str(change.status).replace('_', ' ')}; "
                "nothing changed."
            ),
        )
    return _read(
        kind,
        updated,
        outcome="updated",
        applied=True,
        message=f"Marked the task '{task.title}' as {str(change.status).replace('_', ' ')}.",
    )


async def _set_project_status(
    services: _Services, owner: User, body: ConfirmActionRequest, data: BaseModel
) -> ConfirmActionRead:
    """Move one project to the state the sentence named.

    ``ProjectService.set_status`` takes a plain string and owns the transition
    table, so the enum is rendered to its value here rather than being handed over
    as an enum member — a service that takes ``str`` should be given the string the
    database stores, not an object that happens to compare equal to it.

    ``archived_at`` is deliberately not touched: the service refuses an out-of-
    terminal-state edge, and the archive timestamp stays the property of the
    archive route alone.
    """
    write = _expect(data, ProjectStatusWrite)
    project = await services.projects.get(project_id=_target(body, "project"), owner=owner)
    wanted = str(write.status)
    before = project.status
    updated = await services.projects.set_status(project=project, status=wanted, owner=owner)
    if updated.status == before:
        return _read(
            ActionKind.SET_PROJECT_STATUS,
            updated,
            outcome="no_op",
            applied=False,
            message=f"The project '{project.name}' is already {wanted.replace('_', ' ')}; nothing changed.",
        )
    return _read(
        ActionKind.SET_PROJECT_STATUS,
        updated,
        outcome="updated",
        applied=True,
        message=f"Marked the project '{project.name}' as {wanted.replace('_', ' ')}.",
    )


async def _archive_note(
    services: _Services, owner: User, body: ConfirmActionRequest, data: BaseModel
) -> ConfirmActionRead:
    """Set a note aside, keeping it and its history.

    The service returns the row unchanged when it is already archived, so a
    replayed confirm is a ``no_op`` rather than a second archive event.
    """
    note = await services.knowledge.get_note(note_id=_target(body, "note"), owner=owner)
    before = note.status
    archived = await services.knowledge.archive_note(note=note, owner=owner)
    if archived.status == before:
        return _read(
            ActionKind.ARCHIVE_NOTE,
            archived,
            outcome="no_op",
            applied=False,
            message=f"The note '{note.title}' is already archived; nothing changed.",
        )
    return _read(
        ActionKind.ARCHIVE_NOTE,
        archived,
        outcome="updated",
        applied=True,
        message=f"Archived the note '{note.title}'.",
    )


async def _publish_note(
    services: _Services, owner: User, body: ConfirmActionRequest, data: BaseModel
) -> ConfirmActionRead:
    """Mark a note as real, which is what a link target is meant to point at.

    **An archived note cannot be published.** The only way out of ``archived`` is
    :meth:`KnowledgeService.restore_note`, which is not one of this surface's
    kinds — so the proposal layer refuses the sentence and the user is told to
    restore the note first, rather than this handler reaching for a service method
    the confirm sentence never described.
    """
    note = await services.knowledge.get_note(note_id=_target(body, "note"), owner=owner)
    if note.status == NoteStatus.ARCHIVED.value:
        raise ValidationError(
            "That note is archived. Restore it before publishing it.",
            details={"note_status": note.status},
        )
    before = note.status
    published = await services.knowledge.publish_note(note=note, owner=owner)
    if published.status == before:
        return _read(
            ActionKind.PUBLISH_NOTE,
            published,
            outcome="no_op",
            applied=False,
            message=f"The note '{note.title}' is already published; nothing changed.",
        )
    return _read(
        ActionKind.PUBLISH_NOTE,
        published,
        outcome="updated",
        applied=True,
        message=f"Published the note '{note.title}'.",
    )


async def _complete_learning_goal(
    services: _Services, owner: User, body: ConfirmActionRequest, data: BaseModel
) -> ConfirmActionRead:
    """Mark one goal finished: the stamp, the status and the 100% happen together.

    ``complete_goal`` is deliberately the only producer of ``completed_at``, so a
    goal cannot end up claiming it was finished without the status that says so.
    Re-completing is not an error upstream — it re-stamps the row — so the state is
    read first and an already-completed goal reported as a no-op rather than as a
    fresh completion.
    """
    identifier = _target(body, "learning goal")
    goal = await services.learning.get_goal(owner=owner, goal_id=identifier)
    title = goal.title
    if goal.status == LearningGoalStatus.COMPLETED.value:
        return _read(
            ActionKind.COMPLETE_LEARNING_GOAL,
            goal,
            outcome="no_op",
            applied=False,
            message=f"The learning goal '{title}' is already completed; nothing changed.",
        )
    completed = await services.learning.complete_goal(owner=owner, goal_id=identifier)
    return _read(
        ActionKind.COMPLETE_LEARNING_GOAL,
        completed,
        outcome="updated",
        applied=True,
        message=f"Marked the learning goal '{title}' as completed.",
    )


async def _schedule_task(
    services: _Services, owner: User, body: ConfirmActionRequest, data: BaseModel
) -> ConfirmActionRead:
    """Put a planned window on a task.

    **Through :meth:`TaskService.schedule`, not through ``update``.** That is the
    whole reason this kind exists rather than being an ``update_task`` with a
    ``start_date``: the schedule door records *when* a date was given and emits
    ``TASK_SCHEDULED`` or ``TASK_RESCHEDULED``, where a blanket PATCH records
    neither, and "when did this slip?" is the question an activity feed exists to
    answer.

    ``due_date`` is passed only when the sentence named one. The service treats
    ``None`` as "leave the stored deadline alone", and clearing a deadline is
    ``unschedule``'s job rather than this one's.
    """
    write = _expect(data, TaskScheduleWrite)
    task = await services.tasks.get(task_id=_target(body, "task"), owner=owner)
    title = task.title
    before = (task.start_date, task.due_date)
    scheduled = await services.tasks.schedule(
        task=task,
        owner=owner,
        start_date=write.start_date,
        due_date=write.due_date,
    )
    if (scheduled.start_date, scheduled.due_date) == before:
        return _read(
            ActionKind.SCHEDULE_TASK,
            scheduled,
            outcome="no_op",
            applied=False,
            message=f"The task '{title}' is already planned from {write.start_date}; nothing changed.",
        )
    return _read(
        ActionKind.SCHEDULE_TASK,
        scheduled,
        outcome="updated",
        applied=True,
        message=f"Scheduled the task '{title}' from {write.start_date}.",
    )


async def _unschedule_task(
    services: _Services, owner: User, body: ConfirmActionRequest, data: BaseModel
) -> ConfirmActionRead:
    """Take the planned window off a task.

    ``unschedule`` returns the row unchanged when there was no window, so the state
    is read first and the retry reported as a no-op.
    """
    task = await services.tasks.get(task_id=_target(body, "task"), owner=owner)
    title = task.title
    if task.start_date is None:
        return _read(
            ActionKind.UNSCHEDULE_TASK,
            task,
            outcome="no_op",
            applied=False,
            message=f"The task '{title}' was not scheduled; nothing changed.",
        )
    unscheduled = await services.tasks.unschedule(task=task, owner=owner)
    return _read(
        ActionKind.UNSCHEDULE_TASK,
        unscheduled,
        outcome="updated",
        applied=True,
        message=f"Removed the planned window from the task '{title}'.",
    )


async def _tag_task(
    services: _Services, owner: User, body: ConfirmActionRequest, data: BaseModel
) -> ConfirmActionRead:
    """Attach one of the caller's own tags to a task.

    The tag is resolved **by name, against the caller's own tags, matched
    exactly** — the page read is a substring search over several columns and a row
    it turns up is only a candidate until the whole name is compared. A name that
    matches nothing is a refusal here rather than a new tag: "tag it urgent" must
    not quietly create a tag called urgent because the account had not got one.

    The task's tags are read through ``TaskService.read``, which runs the join
    ``TaskRead.tag_ids`` promises; ``add_tag`` is a no-op when the tag is already
    attached, so the before-set is what decides whether anything changed.
    """
    ack = _expect(data, ActionAck)
    task = await services.tasks.get(task_id=_target(body, "task"), owner=owner)
    title = task.title
    tag_id, tag_name = await _tag_id(services.tags, owner, ack.tag)
    before = (await services.tasks.read(task=task, owner=owner)).tag_ids
    after = await services.tasks.add_tag(task=task, tag_id=tag_id, owner=owner)
    if list(after) == list(before):
        return _read(
            ActionKind.TAG_TASK,
            task,
            outcome="no_op",
            applied=False,
            message=f"The task '{title}' already carries the tag '{tag_name}'; nothing changed.",
        )
    return _read(
        ActionKind.TAG_TASK,
        task,
        outcome="updated",
        applied=True,
        message=f"Tagged the task '{title}' with '{tag_name}'.",
    )


async def _untag_task(
    services: _Services, owner: User, body: ConfirmActionRequest, data: BaseModel
) -> ConfirmActionRead:
    """Detach one of the caller's own tags from a task.

    Detaching a tag the task does not carry is a no-op upstream, so the before-set
    decides what is reported.
    """
    ack = _expect(data, ActionAck)
    task = await services.tasks.get(task_id=_target(body, "task"), owner=owner)
    title = task.title
    tag_id, tag_name = await _tag_id(services.tags, owner, ack.tag)
    before = (await services.tasks.read(task=task, owner=owner)).tag_ids
    after = await services.tasks.remove_tag(task=task, tag_id=tag_id, owner=owner)
    if list(after) == list(before):
        return _read(
            ActionKind.UNTAG_TASK,
            task,
            outcome="no_op",
            applied=False,
            message=f"The task '{title}' does not carry the tag '{tag_name}'; nothing changed.",
        )
    return _read(
        ActionKind.UNTAG_TASK,
        task,
        outcome="updated",
        applied=True,
        message=f"Removed the tag '{tag_name}' from the task '{title}'.",
    )


# --------------------------------------------------------------------------- #
# Confirm: one dispatcher per kind
# --------------------------------------------------------------------------- #

#: What every dispatcher is handed, and what it must answer with. The arguments
#: are the same for all thirty-eight so the table below reads as a list of what
#: each kind does rather than as thirty-eight signatures.
_Handler = Callable[
    ["_Services", User, ConfirmActionRequest, BaseModel], Awaitable[ConfirmActionRead]
]

_HANDLERS: Mapping[ActionKind, _Handler] = MappingProxyType(
    {
        # --- tasks ------------------------------------------------------- #
        ActionKind.CREATE_TASK: _create_task,
        ActionKind.UPDATE_TASK: _update_task,
        ActionKind.DELETE_TASK: _delete_task,
        ActionKind.COMPLETE_TASK: _complete_task,
        ActionKind.SET_TASK_STATUS: _set_task_status,
        ActionKind.SCHEDULE_TASK: _schedule_task,
        ActionKind.UNSCHEDULE_TASK: _unschedule_task,
        ActionKind.TAG_TASK: _tag_task,
        ActionKind.UNTAG_TASK: _untag_task,
        # --- projects ---------------------------------------------------- #
        ActionKind.CREATE_PROJECT: _create_project,
        ActionKind.UPDATE_PROJECT: _update_project,
        ActionKind.DELETE_PROJECT: _delete_project,
        ActionKind.SET_PROJECT_STATUS: _set_project_status,
        # --- knowledge --------------------------------------------------- #
        ActionKind.CREATE_NOTE: _create_note,
        ActionKind.UPDATE_NOTE: _update_note,
        ActionKind.DELETE_NOTE: _delete_note,
        ActionKind.ARCHIVE_NOTE: _archive_note,
        ActionKind.PUBLISH_NOTE: _publish_note,
        ActionKind.CREATE_BOOKMARK: _create_bookmark,
        ActionKind.DELETE_BOOKMARK: _delete_bookmark,
        ActionKind.CREATE_CONCEPT: _create_concept,
        ActionKind.DELETE_CONCEPT: _delete_concept,
        ActionKind.CREATE_LINK: _create_link,
        ActionKind.DELETE_LINK: _delete_link,
        # --- learning ---------------------------------------------------- #
        ActionKind.CREATE_LEARNING_GOAL: _create_learning_goal,
        ActionKind.UPDATE_LEARNING_GOAL: _update_learning_goal,
        ActionKind.COMPLETE_LEARNING_GOAL: _complete_learning_goal,
        ActionKind.DELETE_LEARNING_GOAL: _delete_learning_goal,
        ActionKind.CREATE_SKILL: _create_skill,
        ActionKind.DELETE_SKILL: _delete_skill,
        # --- planner ----------------------------------------------------- #
        ActionKind.CREATE_EVENT: _create_event,
        ActionKind.UPDATE_EVENT: _update_event,
        ActionKind.DELETE_EVENT: _delete_event,
        ActionKind.CREATE_SESSION: _create_session,
        ActionKind.DELETE_SESSION: _delete_session,
        # --- account ----------------------------------------------------- #
        ActionKind.UPDATE_PROFILE: _update_profile,
        # --- developer intelligence -------------------------------------- #
        ActionKind.CREATE_REPOSITORY: _create_repository,
        ActionKind.DELETE_REPOSITORY: _delete_repository,
    }
)


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #


def _expect[BaseModelT: BaseModel](payload: BaseModel, model: type[BaseModelT]) -> BaseModelT:
    """Narrow a validated payload to the model its spec names.

    The validation in :func:`_validated_payload` is already against that exact
    class, so this is a typing assertion rather than a runtime check; it exists so
    each dispatcher reads its own fields without a cast.
    """
    assert isinstance(payload, model)  # noqa: S101 — the spec's schema produced it
    return payload


def _read(
    kind: ActionKind,
    row: Any,
    *,
    outcome: str,
    applied: bool,
    message: str,
) -> ConfirmActionRead:
    """Build a confirm answer from a row the service has just acted on.

    ``entity`` comes from the kind rather than from the call site, so the noun in
    the sentence and the noun in the response cannot drift apart.
    """
    return ConfirmActionRead(
        kind=kind,
        entity=_ENTITY_BY_KIND[kind],
        entity_id=row.id,
        outcome=outcome,
        applied=applied,
        message=message,
    )


def _deleted(entity: str, label: str) -> str:
    """The sentence a delete reports, given the row's own name."""
    return f"Deleted the {entity} '{label}'."


def _target(body: ConfirmActionRequest, entity: str) -> UUID:
    """The one row this action acts on, or a 422 naming the field that was absent.

    Every kind that changes or removes an existing row needs one, and there is no
    default: the proposal layer resolved the reference against the caller's own
    rows and put the resulting id here, so a confirm that arrives without it is a
    client that dropped it rather than a sentence NEXUS could have re-resolved.
    Re-deriving it from the body would mean matching a phrase on the write path,
    which is the guess the refusal rules exist to prevent.
    """
    if body.target_id is None:
        raise ValidationError(
            f"This action needs the {entity} the proposal named.",
            details={"field": "target_id", "entity": entity},
        )
    return body.target_id


def _set_fields(payload: BaseModel, *, exclude_none: bool) -> dict[str, Any]:
    """The fields a payload actually names, as a service's flat keywords.

    ``exclude_unset`` is the load-bearing half: it is the only way to tell "the
    user did not mention this" from "the user asked for this to be null", and
    several of these services write ``None`` as a value rather than skipping it.

    ``exclude_none`` is a per-caller decision and the two are opposites. A
    *creation* excludes nulls, because ``progress=None`` is not ``progress=0`` and
    a creation with a null column is a row claiming to have nothing recorded. An
    *edit* does not, because ``None`` is how a description is cleared.
    """
    return payload.model_dump(exclude_unset=True, exclude_none=exclude_none)


def _differs(before: Any, after: Any, names: Iterable[str]) -> bool:
    """Whether any of ``names`` differs between two reads of the same row.

    **Compare scalars, never the two row objects.** Every repository in this app
    mutates the instance it is handed and refreshes it, so on the success path
    ``before is after`` — a comparison of the objects would report every genuine
    update as a no-op. The values are therefore read attribute by attribute, and
    the caller chooses which attributes: the ones the payload said it was changing.
    """
    return any(getattr(after, name, None) != getattr(before, name, None) for name in names)


async def _existing(page: Awaitable[Any], wanted: str, field: str) -> Any | None:
    """The first row in ``page`` whose ``field`` equals ``wanted`` exactly.

    The service ``search`` parameters are substring matches over more than one
    column, so a row found by them is only a *candidate*; comparing the whole
    field is what makes the check a duplicate check rather than a fuzzy one. A
    task whose description happens to contain the title therefore does not
    suppress a real creation.
    """
    for row in (await page).items:
        if getattr(row, field).strip().casefold() == wanted.strip().casefold():
            return row
    return None


def _exact_title(rows: Any, wanted: str) -> Any | None:
    """The first row whose ``title`` equals ``wanted`` exactly.

    Split from :func:`_existing` only because
    ``LearningIntelligenceService.list_goals`` has no search parameter to narrow
    the page first; the comparison itself is the same one.
    """
    for row in rows:
        if row.title.strip().casefold() == wanted.strip().casefold():
            return row
    return None


async def _existing_path(page: Awaitable[Any], wanted: str) -> Any | None:
    """The first row in ``page`` already pointing at the folder ``wanted`` names.

    Split from :func:`_existing` because a **path is not a string**. The service
    stores ``str(Path(...).resolve())``, which on Windows is backslashed and
    lower-cased, while the payload still carries what the user said — ``E:/op``,
    ``~/code/nexo``, or a path relative to whatever the server's working directory
    happens to be. Comparing those two spellings with :func:`_existing` would miss
    the very replay it exists to catch and let the service answer 409 instead, so
    both sides go through :func:`_normalised_path` first.

    That normalisation is the *textual* half of what the service does: it never
    touches the filesystem, so it cannot raise on a path that does not exist and
    cannot succeed in proving a folder is a work tree. Proving that is the
    service's job, before the write, and it is why this is a cheap pre-check and
    not a second validation.
    """
    target = _normalised_path(wanted)
    for row in (await page).items:
        if _normalised_path(row.local_path) == target:
            return row
    return None


def _normalised_path(path: str) -> str:
    """One spelling per folder, for the duplicate check above.

    ``expanduser`` because a ``~``-prefixed path is how the user writes their own
    home and the stored row will never carry the tilde; ``abspath`` because a
    relative path is resolved against the process's working directory — which is
    the same directory the service resolves it against; and ``normcase`` because
    Windows folds both the case and the separator, so two spellings of one
    folder are otherwise two rows from this module's point of view.
    """
    return os.path.normcase(os.path.abspath(os.path.expanduser(path)))


async def _tag_id(tags: TagService, owner: User, name: str | None) -> tuple[UUID, str]:
    """Resolve one of the caller's own tags by its exact name.

    ``TaskService.add_tag`` takes a ``tag_id`` and checks it against the caller,
    so the name has to become an id first, and it has to become **the right** one:
    the search is a substring match, so ``search="work"`` turns up "work" and
    "network" alike and picking the first would file the task under a tag the user
    did not say. Comparing the whole name is what turns a page read into a
    resolution.

    Returns:
        The tag's id and the name as stored, so the message quotes what the
        database holds rather than the casing the caller sent.

    Raises:
        ValidationError: When the payload named no tag, or named one this account
            does not have. Neither creates a tag: an assistant that invents a tag
            to satisfy a sentence is doing the user's bookkeeping for them.
    """
    if not name or not name.strip():
        raise ValidationError(
            "This action needs the tag to use.",
            details={"field": "payload.tag"},
        )
    wanted = name.strip().casefold()
    page = await tags.list(owner=owner, search=name.strip(), limit=_DUPLICATE_SCAN)
    for row in page.items:
        if row.name.strip().casefold() == wanted:
            return row.id, row.name
    raise ValidationError(
        "That is not one of your tags.",
        details={"field": "payload.tag", "tag": name.strip()},
    )


__all__ = ["router"]
