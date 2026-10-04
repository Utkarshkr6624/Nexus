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
``verb_not_recovered``, ``title_not_recoverable``, the two task-reference
failures, ``context_missing`` and ``payload_invalid`` are answers NEXUS gave on
purpose. Returning 500 for "could not understand" would make a routine outcome
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
* **The candidate tasks.** Fetched **only** for a ``task_manage`` prediction,
  owner-scoped, capped, and filtered to the statuses a completion can legally
  leave. They are the rows :func:`~app.ml.actions.extraction.match_task_reference`
  may resolve a reference against; without them "mark the API contract as done"
  could only ever be a refusal.

Why confirm is the security-critical half
-----------------------------------------
A proposal is a description. Confirm is where it becomes a write, and the client
is the untrusted party from that moment on: the user may have edited the title in
the confirm dialog, and a body is just bytes. So **nothing in the request is
believed.** In order:

1. ``kind`` is parsed as an :class:`~app.ml.actions.proposals.ActionKind` by the
   request model, so a name outside the closed set is a 422 before the handler
   runs. There is no delete member to reach, and
   :attr:`~app.ml.actions.proposals.ActionSpec.destructive` is a property
   returning ``False`` — but the handler checks it anyway, so a spec that ever
   disagreed with its kind would be refused here rather than executed.
2. The :class:`~app.ml.actions.proposals.ActionSpec` is looked up in the frozen
   table, which supplies the payload schema and the permission. Nothing about the
   body can name a different service.
3. The permission is re-checked server-side against
   :attr:`app.core.permissions.Permission` for the caller's role. The route gate
   is ``analytics.read``; the *action's* capability — ``tasks.write``,
   ``projects.write``, ``knowledge.write`` — is a second, separate check, so a
   client cannot propose under one capability and execute under another.
4. The ``intent`` must match the intent the spec says this kind can only have
   come from.
5. Unknown payload keys are rejected, then the payload is validated against the
   spec's own Pydantic model. An empty or malformed payload therefore never
   reaches a service.
6. Every id is re-resolved through an owner-scoped service method. A forged
   ``project_id`` is refused by ``TaskService.create``'s own scoped lookup and a
   forged ``target_id`` by ``TaskService.get`` — both a 404, both before the row
   is written, both indistinguishable from an id nobody has issued.

**The activity trail is not optional here.** Every service below arrives through
:mod:`app.api.deps`, which wires ``activity=`` for all of them, so a task created
through this endpoint writes the same ``TASK_CREATED`` row a hand-typed
``POST /tasks`` writes. The brief's phrase "call the real service" is the whole of
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

A completion is idempotent for free: ``TaskService.set_status`` returns the row
unchanged when it is already in the target state, and the endpoint reports that as
a no-op by comparing the status it read with the status it got back.

What this module cannot claim
-----------------------------
There is no server-side record of a proposal, so confirm cannot prove one
happened; the binding it has is *shape* — a registered kind, an intent that kind
can only come from, and a payload that validates against the schema that kind
produces. Anything else is a 422. ``tests/test_ml_actions_api.py`` pins that
inventory directly: this module registers exactly two routes, both ``POST``, and
``confirm`` is the only one that reaches a service.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError
from starlette.concurrency import run_in_threadpool

from app.api.deps import (
    AuthenticatedUser,
    KnowledgeServiceDep,
    LearningIntelligenceServiceDep,
    MLRuntimeDep,
    ProjectRepositoryDep,
    ProjectServiceDep,
    SettingsDep,
    TaskServiceDep,
)
from app.core.deps import require_permission
from app.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.core.logging import get_logger, log_event
from app.core.permissions import Permission, has_permission
from app.ml.actions import (
    ACTION_SPECS,
    ActionKind,
    ActionProposal,
    ActionSpec,
    ProposalContext,
    ProposalRefusal,
    TaskCandidate,
    is_proposal,
    propose_action,
)
from app.ml.exceptions import MLUnavailableError
from app.models.enums import TaskStatus
from app.models.user import User
from app.schemas.actions import (
    ConfirmActionRead,
    ConfirmActionRequest,
    ProposeActionRead,
    ProposeActionRequest,
)
from app.schemas.knowledge import NoteCreate
from app.schemas.learning import LearningGoalWrite
from app.schemas.project import ProjectCreate
from app.schemas.task import TaskCreate, TaskStatusChange
from app.services.knowledge_service import KnowledgeService
from app.services.learning import LearningIntelligenceService
from app.services.planner_service import resolve_timezone
from app.services.project_service import ProjectService
from app.services.task_service import TaskService
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

#: How many open tasks a completion may be matched against. A cap rather than the
#: caller's whole backlog: :func:`~app.ml.actions.extraction.match_task_reference`
#: refuses an ambiguous reference outright, so a larger candidate set does not
#: produce more matches — it produces more refusals and a slower request.
_CANDIDATE_LIMIT = 50

#: The statuses a task can be completed *from*, and therefore the ones worth
#: offering as completion candidates. A completed or cancelled row is not a
#: candidate: completing it is either a no-op or an illegal edge, and neither is
#: a proposal worth showing a confirm button for.
_OPEN_STATUSES = frozenset(
    {TaskStatus.TODO.value, TaskStatus.IN_PROGRESS.value, TaskStatus.BLOCKED.value}
)

_TZ_DESCRIPTION = (
    "The caller's IANA zone, e.g. `Europe/Berlin`. Omitted uses the deployment "
    "default. Resolved exactly as the planner resolves it, so a date extracted "
    "here and a day planned there cut on the same instant."
)

#: The row each kind writes, for the ``entity`` field of the confirm response.
_ENTITY_BY_KIND: dict[ActionKind, str] = {
    ActionKind.CREATE_TASK: "task",
    ActionKind.COMPLETE_TASK: "task",
    ActionKind.CREATE_PROJECT: "project",
    ActionKind.CREATE_NOTE: "note",
    ActionKind.CREATE_LEARNING_GOAL: "learning_goal",
}


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
    tasks: TaskServiceDep,
    project_repository: ProjectRepositoryDep,
    settings: SettingsDep,
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
    candidates = await _task_candidates(tasks, current_user, prediction.intent)

    outcome = propose_action(
        payload.text,
        prediction,
        context=ProposalContext(
            tz=zone,
            now=datetime.now(UTC),
            project_id=project_id,
            project_label=project_label,
            task_candidates=candidates,
        ),
    )

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
        candidates=len(candidates),
    )

    if isinstance(outcome, ActionProposal):
        return ProposeActionRead.from_proposal(outcome)
    assert isinstance(outcome, ProposalRefusal)  # noqa: S101 — narrowing, not a runtime check
    return ProposeActionRead.from_refusal(outcome)


@router.post(
    "/action/confirm",
    response_model=ConfirmActionRead,
    summary="Carry out a proposal the user has confirmed",
    description=(
        "Re-derives the action from `kind`, re-checks the permission, re-validates "
        "the payload against the schema that kind names and re-resolves every id "
        "through an owner-scoped lookup, then calls the same service a typed "
        "request would have. A repeated confirm reports the existing row rather "
        "than creating a second one."
    ),
    dependencies=_ANALYTICS_READ,
)
async def confirm_action(
    body: ConfirmActionRequest,
    current_user: AuthenticatedUser,
    tasks: TaskServiceDep,
    projects: ProjectServiceDep,
    knowledge: KnowledgeServiceDep,
    learning: LearningIntelligenceServiceDep,
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

    **There is no delete path here and there cannot be.** No
    :class:`~app.ml.actions.proposals.ActionKind` is destructive, so there is no
    kind to name; the ``spec.destructive`` guard below is the belt to that
    braces, and it refuses rather than executing if a spec ever disagreed with its
    own kind.

    **The activity trail is written by the service, not by this router.**
    ``TaskServiceDep`` and its siblings arrive from :mod:`app.api.deps`, which
    wires ``activity=`` for all of them, so a task created here writes the same
    ``TASK_CREATED`` row a hand-typed ``POST /tasks`` writes.

    Errors: 401 unauthenticated, 403 without ``analytics.read`` or without the
    permission this particular action needs, 404 for a target the caller does not
    own, 422 for a kind outside the set, an ``intent`` that kind cannot have come
    from, an empty or malformed payload, or a completion with no ``target_id``.
    """
    spec = ACTION_SPECS[body.kind]
    if spec.destructive:
        # Unreachable while every ActionSpec.destructive is a property returning
        # False, and that is the point: the refusal does not consult the kind to
        # decide, it asks the table and refuses what the table says is destructive.
        raise ValidationError(
            "This action cannot be confirmed: NEXO does not carry out destructive actions."
        )
    if not has_permission(current_user.role, spec.permission):
        raise ForbiddenError("You do not have permission to perform this action.")
    if body.intent != str(spec.intent):
        raise ValidationError(
            "This payload was not proposed from that intent.",
            details={"expected_intent": str(spec.intent), "supplied_intent": body.intent},
        )

    data = _validated_payload(spec, body.payload)
    if body.kind is ActionKind.COMPLETE_TASK:
        result = await _complete_task(tasks, current_user, body, data)
    elif body.kind is ActionKind.CREATE_TASK:
        result = await _create_task(tasks, current_user, data)
    elif body.kind is ActionKind.CREATE_PROJECT:
        result = await _create_project(projects, current_user, data)
    elif body.kind is ActionKind.CREATE_NOTE:
        result = await _create_note(knowledge, current_user, data)
    else:
        result = await _create_learning_goal(learning, current_user, data)

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


async def _task_candidates(
    tasks: TaskService, owner: User, intent: str
) -> tuple[TaskCandidate, ...]:
    """The caller's open tasks, for a reference like "the API contract" to land on.

    Fetched **only** for a ``task_manage`` prediction, because that is the only
    intent with a kind that names a row. The list is owner-scoped inside
    ``TaskService.list`` — the caller is the ``WHERE`` clause — and filtered to
    the statuses a completion can legally leave, so a cancelled card is never
    offered as something to complete.

    Args:
        tasks: The request-scoped task service.
        owner: The authenticated caller.
        intent: The classifier's winning intent, as a string.

    Returns:
        The candidates, or an empty tuple for every other intent.
    """
    if intent != str(Intent.TASK_MANAGE):
        return ()
    page = await tasks.list(
        owner=owner,
        limit=_CANDIDATE_LIMIT,
        sort="created_at",
        order="desc",
    )
    return tuple(
        TaskCandidate(id=row.id, title=row.title)
        for row in page.items
        if row.status in _OPEN_STATUSES
    )


# --------------------------------------------------------------------------- #
# Confirm: re-derivation
# --------------------------------------------------------------------------- #


def _validated_payload(spec: ActionSpec, raw: dict[str, Any]) -> BaseModel:
    """Validate an untrusted payload against the schema the spec names.

    Two rejections happen before Pydantic's, and both matter more than the ones
    it does itself:

    * **Unknown keys.** ``TaskCreate`` and ``LearningGoalWriteBase`` do not set
      ``extra="forbid"``, so Pydantic's default would drop a key the client sent
      and answer 200 — leaving a client that read the 200 as "the change was
      applied" believing something false about their own work.
    * **An empty payload.** ``{}`` is a valid dictionary that validates against
      nothing, and the resulting "confirmed" action would be a write with no
      content. It is named as a 422 here rather than surfacing as a confusing
      per-field error.

    Raises:
        ValidationError: 422 for an unknown key, an empty payload, or any failure
            of the spec's own model.
    """
    unknown = sorted(set(raw) - set(spec.schema.model_fields))
    if unknown:
        raise ValidationError(
            f"This action's payload has no field named {unknown[0]!r}.",
            details={"unknown_fields": unknown, "expected": sorted(spec.schema.model_fields)},
        )
    if not raw:
        raise ValidationError("This action's payload is empty.")
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


# --------------------------------------------------------------------------- #
# Confirm: one dispatcher per kind
# --------------------------------------------------------------------------- #


async def _complete_task(
    tasks: TaskService, owner: User, body: ConfirmActionRequest, data: BaseModel
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
    if body.target_id is None:
        raise ValidationError(
            "Completing a task needs the task the proposal named.",
            details={"field": "target_id"},
        )
    change = _expect(data, TaskStatusChange)
    task = await tasks.get(task_id=body.target_id, owner=owner)
    before = task.status
    updated = await tasks.set_status(
        task=task,
        status=change.status,
        owner=owner,
        note=change.note,
    )
    if updated.status == before:
        return ConfirmActionRead(
            kind=ActionKind.COMPLETE_TASK,
            entity=_ENTITY_BY_KIND[ActionKind.COMPLETE_TASK],
            entity_id=updated.id,
            outcome="no_op",
            applied=False,
            message=(
                f"The task '{task.title}' is already {str(change.status).replace('_', ' ')}; "
                "nothing changed."
            ),
        )
    return ConfirmActionRead(
        kind=ActionKind.COMPLETE_TASK,
        entity=_ENTITY_BY_KIND[ActionKind.COMPLETE_TASK],
        entity_id=updated.id,
        outcome="updated",
        applied=True,
        message=f"Marked the task '{task.title}' as {str(change.status).replace('_', ' ')}.",
    )


async def _create_task(tasks: TaskService, owner: User, data: BaseModel) -> ConfirmActionRead:
    """Create one task, or name the one already on the board.

    ``TaskService.create`` re-checks the project through its own scoped lookup, so
    a forged ``project_id`` is a 404 with no row written. The service also writes
    ``TASK_CREATED`` beside the insert; this router writes nothing itself.
    """
    create = _expect(data, TaskCreate)
    existing = await _existing(
        tasks.list(
            owner=owner,
            project_id=create.project_id,
            search=create.title,
            limit=_DUPLICATE_SCAN,
        ),
        create.title,
        "title",
    )
    if existing is not None:
        return ConfirmActionRead(
            kind=ActionKind.CREATE_TASK,
            entity=_ENTITY_BY_KIND[ActionKind.CREATE_TASK],
            entity_id=existing.id,
            outcome="no_op",
            applied=False,
            message=(
                f"A task titled '{create.title}' is already on that board; "
                "NEXO did not create a second one."
            ),
        )
    task = await tasks.create(owner=owner, data=create)
    return ConfirmActionRead(
        kind=ActionKind.CREATE_TASK,
        entity=_ENTITY_BY_KIND[ActionKind.CREATE_TASK],
        entity_id=task.id,
        outcome="created",
        applied=True,
        message=f"Created the task '{task.title}'.",
    )


async def _create_project(
    projects: ProjectService, owner: User, data: BaseModel
) -> ConfirmActionRead:
    """Create one project, or name the one that already exists."""
    create = _expect(data, ProjectCreate)
    existing = await _existing(
        projects.list(owner=owner, search=create.name, limit=_DUPLICATE_SCAN),
        create.name,
        "name",
    )
    if existing is not None:
        return ConfirmActionRead(
            kind=ActionKind.CREATE_PROJECT,
            entity=_ENTITY_BY_KIND[ActionKind.CREATE_PROJECT],
            entity_id=existing.id,
            outcome="no_op",
            applied=False,
            message=(
                f"A project named '{create.name}' already exists; NEXO did not create a second one."
            ),
        )
    project = await projects.create(owner=owner, data=create)
    return ConfirmActionRead(
        kind=ActionKind.CREATE_PROJECT,
        entity=_ENTITY_BY_KIND[ActionKind.CREATE_PROJECT],
        entity_id=project.id,
        outcome="created",
        applied=True,
        message=f"Created the project '{project.name}'.",
    )


async def _create_note(
    knowledge: KnowledgeService, owner: User, data: BaseModel
) -> ConfirmActionRead:
    """Create one note, or name the one already in the knowledge base."""
    create = _expect(data, NoteCreate)
    existing = await _existing(
        knowledge.list_notes(owner=owner, search=create.title, limit=_DUPLICATE_SCAN),
        create.title,
        "title",
    )
    if existing is not None:
        return ConfirmActionRead(
            kind=ActionKind.CREATE_NOTE,
            entity=_ENTITY_BY_KIND[ActionKind.CREATE_NOTE],
            entity_id=existing.id,
            outcome="no_op",
            applied=False,
            message=(
                f"A note titled '{create.title}' already exists; NEXO did not create a second one."
            ),
        )
    note = await knowledge.create_note(owner=owner, data=create)
    return ConfirmActionRead(
        kind=ActionKind.CREATE_NOTE,
        entity=_ENTITY_BY_KIND[ActionKind.CREATE_NOTE],
        entity_id=note.id,
        outcome="created",
        applied=True,
        message=f"Created the note '{note.title}'.",
    )


async def _create_learning_goal(
    learning: LearningIntelligenceService, owner: User, data: BaseModel
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
    page = await learning.list_goals(owner=owner, limit=_DUPLICATE_SCAN)
    existing = _exact_title(page.items, goal.title)
    if existing is not None:
        return ConfirmActionRead(
            kind=ActionKind.CREATE_LEARNING_GOAL,
            entity=_ENTITY_BY_KIND[ActionKind.CREATE_LEARNING_GOAL],
            entity_id=existing.id,
            outcome="no_op",
            applied=False,
            message=(
                f"A learning goal titled '{goal.title}' is already recorded; "
                "NEXO did not create a second one."
            ),
        )
    fields = goal.model_dump(exclude_unset=True, exclude_none=True)
    created = await learning.create_goal(owner=owner, **fields)
    return ConfirmActionRead(
        kind=ActionKind.CREATE_LEARNING_GOAL,
        entity=_ENTITY_BY_KIND[ActionKind.CREATE_LEARNING_GOAL],
        entity_id=created.id,
        outcome="created",
        applied=True,
        message=f"Created the learning goal '{created.title}'.",
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


__all__ = ["router"]
