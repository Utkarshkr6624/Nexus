"""Action *proposals*: the confirm layer between an utterance and a write.

This module answers the question ``app/api/v1/ml.py`` deferred. That router's
docstring said slot filling "needs either a second model or hand-written
per-utterance parsers"; the project brief allows exactly one of those, and
:mod:`app.ml.actions.extraction` is the hand-written one. What this module adds
is the thing that makes the parser safe to wire to a database: **nothing here
executes anything.** A proposal is a fully populated, validated payload plus the
sentence a user reads before agreeing to it. The caller shows the sentence, the
user confirms, and *then* the caller calls the service it names.

**There is no delete. There is no delete. There is no delete.**
:data:`ActionKind` has no destructive member and :meth:`ActionSpec.destructive`
is a property returning ``False``, so a destructive proposal is not a thing that
has been decided against in one place and could be re-enabled by adding an
entry — it is not representable. The reason is structural, and it is two facts:

*The classifier cannot see the verb.* :data:`ml.datasets.taxonomy.Intent.TASK_MANAGE`
covers create, complete, block, cancel, reorder **and delete**, because the
taxonomy asks *which surface a request lands on*, not *what to do to it*. Verb is
not one of the fourteen classes. "add a task" and "delete every task" are the same
class with the same confidence. Any layer that read the verb off the intent and
then chose create-or-delete would be guessing, and it would guess *silently* —
which is the failure mode this whole phase exists to remove.

*Deletion destroys recorded time.* :meth:`app.services.task_service.TaskService.delete`
and :meth:`app.services.project_service.ProjectService.delete` drop the work
sessions, completions and progress figures attached to the row; their own
docstrings recommend archive or cancel instead, precisely because the history is
worth more than the row. A surface where one sentence removes the evidence of
what a user actually did is not a surface NEXUS should offer at all.

So a destructive request is **refused with a reason**, not silently downgraded to
a creation and not quietly dropped: the user is told that NEXUS read the request,
declined it, and why. That refusal is the same shape as every other one here.

**Every proposal requires confirmation and that is not configurable.**
:attr:`ActionProposal.requires_confirmation` is a property returning ``True``; it
is not a constructor argument, not a field, and not something a caller can pass.
The same argument could not hold ``False``, because the value the caller would be
passing is the one thing that would make the layer safe to remove. The safety of
this layer is a property of its type.

**Only what was actually said appears in the summary.** "Create a high-priority
task" is not written when the user said nothing about priority: ``medium`` is a
*default*, and printing it would make the sentence claim a choice nobody made.
The summary states the extracted effect and stops; a missing clause means "not
stated", which is also why an unresolvable date produces a note rather than a
guess.

**What a caller has to supply.** Some of these payloads cannot be completed from
an utterance alone. :class:`TaskCreate` requires a ``project_id`` and a
completion requires naming *one* of the caller's own rows, so
:class:`ProposalContext` carries both. Without them the answer is a refusal with
a reason, never a payload with a made-up id: a task attached to a guessed project
lands in somebody else's board, and that is a worse outcome than asking.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ValidationError

from app.core.permissions import Permission
from app.ml.actions.extraction import (
    Argument,
    ExtractedVerb,
    Extraction,
    TaskCandidate,
    TaskMatchFailure,
    extract_arguments,
    local_day,
    match_task_reference,
)
from app.ml.schemas import IntentPrediction
from app.models.enums import TaskStatus
from app.schemas.knowledge import NoteCreate
from app.schemas.learning import LearningGoalWrite
from app.schemas.project import ProjectCreate
from app.schemas.task import TaskCreate, TaskStatusChange
from ml.datasets.taxonomy import Intent

__all__ = [
    "ACTION_SPECS",
    "ActionKind",
    "ActionProposal",
    "ActionSpec",
    "ProposalContext",
    "ProposalReason",
    "ProposalRefusal",
    "is_proposal",
    "propose_action",
    "render_summary",
]


class ActionKind(StrEnum):
    """The closed set of things this layer can propose.

    Four creations and one transition, chosen because they are the cases where
    the argument extraction is reliable and the effect is reversible. **There is
    no destructive member**, and adding one is not a small change: see this
    module's docstring for the two reasons.
    """

    CREATE_TASK = "create_task"
    CREATE_PROJECT = "create_project"
    CREATE_NOTE = "create_note"
    CREATE_LEARNING_GOAL = "create_learning_goal"
    COMPLETE_TASK = "complete_task"


class ProposalReason:
    """The closed vocabulary a refusal can report.

    A client branches on these, and so does this package's own test suite — a
    refusal whose reason is prose can be asserted on but not acted on.
    """

    UNSUPPORTED_INTENT = "unsupported_intent"
    DESTRUCTIVE_REQUEST = "destructive_request"
    VERB_NOT_RECOVERED = "verb_not_recovered"
    TITLE_NOT_RECOVERABLE = "title_not_recoverable"
    TASK_REFERENCE_AMBIGUOUS = "task_reference_ambiguous"
    TASK_REFERENCE_NOT_FOUND = "task_reference_not_found"
    CONTEXT_MISSING = "context_missing"
    PAYLOAD_INVALID = "payload_invalid"


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

    :attr:`destructive` is a **property**, not a field. Making it one would mean
    a table entry could say ``destructive=True`` and this layer would carry a
    proposal kind that deletes a user's data on the strength of a classifier that
    cannot tell the request from a creation.
    """

    kind: ActionKind
    intent: Intent
    schema: type[BaseModel]
    permission: Permission
    service: str
    module: str
    entrypoint: str
    title_field: str = "title"
    date_field: str | None = None

    @property
    def destructive(self) -> bool:
        """Always ``False``. See this module's docstring and :class:`ActionKind`."""
        return False

    @property
    def qualified(self) -> str:
        """``module.ClassName``, for logs and for resolving the import."""
        return f"{self.module}.{self.service}"


#: One entry per :class:`ActionKind`. Frozen, because a table a request could
#: edit is a table that no longer describes the deployment.
ACTION_SPECS: Mapping[ActionKind, ActionSpec] = MappingProxyType(
    {
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
        ActionKind.COMPLETE_TASK: ActionSpec(
            kind=ActionKind.COMPLETE_TASK,
            intent=Intent.TASK_MANAGE,
            schema=TaskStatusChange,
            permission=Permission.TASKS_WRITE,
            service="TaskService",
            module="app.services.task_service",
            entrypoint="set_status",
            title_field="title",
        ),
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
        ActionKind.CREATE_NOTE: ActionSpec(
            kind=ActionKind.CREATE_NOTE,
            intent=Intent.KNOWLEDGE_CAPTURE,
            schema=NoteCreate,
            permission=Permission.KNOWLEDGE_WRITE,
            service="KnowledgeService",
            module="app.services.knowledge_service",
            entrypoint="create_note",
            title_field="title",
        ),
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
    }
)

#: Which spec answers an intent *and* a verb. Both keys are required: the intent
#: is what the classifier gave, the verb is what the text supports, and a
#: proposal needs both to agree.
_SPEC_BY_INTENT_VERB: Mapping[tuple[str, str], ActionSpec] = MappingProxyType(
    {
        (str(Intent.TASK_MANAGE), ExtractedVerb.CREATE): ACTION_SPECS[ActionKind.CREATE_TASK],
        (str(Intent.TASK_MANAGE), ExtractedVerb.COMPLETE): ACTION_SPECS[ActionKind.COMPLETE_TASK],
        (str(Intent.PROJECT_MANAGE), ExtractedVerb.CREATE): ACTION_SPECS[ActionKind.CREATE_PROJECT],
        (str(Intent.KNOWLEDGE_CAPTURE), ExtractedVerb.CREATE): ACTION_SPECS[ActionKind.CREATE_NOTE],
        (str(Intent.LEARNING_TRACK), ExtractedVerb.CREATE): ACTION_SPECS[
            ActionKind.CREATE_LEARNING_GOAL
        ],
    }
)

#: The intents that have at least one proposal kind behind them, derived from the
#: table rather than listed — so a kind added without an intent, or an intent
#: added without a kind, shows up here as a mismatch rather than as a silent
#: ``unsupported_intent`` for a surface that does have actions.
_PROPOSABLE_INTENTS: frozenset[str] = frozenset(str(spec.intent) for spec in ACTION_SPECS.values())

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


@dataclass(frozen=True, slots=True)
class ProposalContext:
    """What only the caller knows: the zone, the clock, the project, the rows.

    None of this is guessable from an utterance. A task belongs to a project, and
    a completion names a row the user already owns; both arrive here, already
    owner-scoped by whoever read them, so this module never touches the database.
    """

    tz: ZoneInfo = _UTC
    now: datetime | None = None
    project_id: UUID | None = None
    project_label: str | None = None
    task_candidates: tuple[TaskCandidate, ...] = ()


@dataclass(frozen=True, slots=True)
class ActionProposal:
    """One fully populated action the user is being asked to confirm.

    Carries the payload (:attr:`payload`), the provenance of every extracted
    field (:attr:`arguments`), the capability it needs (:attr:`permission`), the
    row it acts on or belongs to (:attr:`target_id`), and the one sentence the
    user checks (:attr:`summary`). It carries **no** handle to call: a proposal
    names the service, module and entry point, and the caller — which owns the
    session, the transaction and the user's decision — makes that call.
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

    @property
    def requires_confirmation(self) -> bool:
        """Always ``True``, and not settable — see this module's docstring."""
        return True

    @property
    def destructive(self) -> bool:
        """Always ``False``. No :class:`ActionKind` is destructive."""
        return False

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
    dialog nobody reads properly.

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
        ActionKind.CREATE_PROJECT: "targeting",
        ActionKind.CREATE_LEARNING_GOAL: "targeting",
    }
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


def _describe_target(label: str | None, identifier: UUID | None) -> str:
    """The "in project …" clause, preferring the caller's own name for the row."""
    if label:
        return f"in project '{label}'"
    if identifier is not None:
        return f"in project {identifier}"
    return ""


def _summarise(
    spec: ActionSpec,
    *,
    title: str,
    priority: str | None,
    due_date: date | None,
    today: date,
    target_label: str | None,
    target_id: UUID | None,
) -> str:
    """Compose the confirm sentence for one proposal.

    Clauses are joined in the order a person states them — what, how urgent,
    when, where — and **absent fields are omitted rather than defaulted**: the
    sentence reports the request that was made, not the request plus the schema's
    defaults.
    """
    if spec.kind is ActionKind.COMPLETE_TASK:
        return f"Mark the task '{title}' as completed."

    if spec.kind is ActionKind.CREATE_NOTE:
        subject = f"Save a note titled '{title}'"
    elif spec.kind is ActionKind.CREATE_PROJECT:
        subject = f"Create a project named '{title}'"
    elif spec.kind is ActionKind.CREATE_LEARNING_GOAL:
        subject = f"Create a learning goal titled '{title}'"
    else:
        adjective = _PRIORITY_ADJECTIVES.get(priority or "", "")
        subject = f"Create a {adjective + ' ' if adjective else ''}task titled '{title}'"

    clauses = [subject]
    # Only a kind that *has* a date field may be described as having a date. A
    # note has none, so "save this note tomorrow" produces no date clause at all
    # — a sentence describing an effect that the payload would not perform is
    # worse than no sentence, because the user would be confirming a deadline
    # NEXUS is going to drop.
    if due_date is not None and spec.date_field is not None:
        clauses.append(_format_due(due_date, today, _DATE_WORDING.get(spec.kind, "due")))
    if spec.kind is ActionKind.CREATE_TASK:
        where = _describe_target(target_label, target_id)
        if where:
            clauses.append(where)
    return ", ".join(clauses) + "."


# --------------------------------------------------------------------------- #
# The proposal itself
# --------------------------------------------------------------------------- #


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

    Args:
        text: The utterance, as the classifier saw it.
        prediction: The classifier's output. Nothing is re-classified here; the
            intent is taken as given, which is why this layer can be tested
            without a checkpoint.
        context: The caller's zone, clock, project and candidate tasks. Defaults
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

    if extraction.verb == ExtractedVerb.DESTRUCTIVE:
        return refuse(
            ProposalReason.DESTRUCTIVE_REQUEST,
            extraction.reason or "NEXUS does not propose destructive actions.",
        )

    spec = _SPEC_BY_INTENT_VERB.get((intent, extraction.verb))
    if spec is None:
        if intent not in _PROPOSABLE_INTENTS:
            return refuse(
                ProposalReason.UNSUPPORTED_INTENT,
                extraction.reason
                or (
                    f"'{intent}' names a surface to look at rather than something to "
                    "write, so there is no action to propose."
                ),
            )
        # The intent has kinds behind it, but the verb is not one this text can
        # justify. Naming the kind is deliberately avoided: which of "create a
        # task" and "complete a task" was meant is the question being refused.
        return refuse(
            ProposalReason.VERB_NOT_RECOVERED,
            "NEXUS could not tell whether this was a creation or a completion. The "
            "classifier cannot see the verb — 'add a task' and 'delete every task' "
            "are the same intent — so it asks rather than picking one.",
        )

    if extraction.title is None:
        return refuse(
            ProposalReason.TITLE_NOT_RECOVERABLE,
            extraction.reason or "NEXUS could not recover a title.",
            kind=spec.kind,
        )

    target_id: UUID | None = None
    target_label: str | None = None

    if spec.kind is ActionKind.COMPLETE_TASK:
        matched = match_task_reference(extraction.title, context.task_candidates)
        if isinstance(matched, TaskMatchFailure):
            return refuse(
                ProposalReason.TASK_REFERENCE_AMBIGUOUS
                if matched.reason_code == "ambiguous"
                else ProposalReason.TASK_REFERENCE_NOT_FOUND,
                matched.reason,
                kind=spec.kind,
            )
        target_id = matched.candidate.id
        target_label = matched.candidate.title
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

    today = local_day(context.now or datetime.now(UTC), context.tz)
    payload = _build_payload(
        spec,
        extraction=extraction,
        project_id=context.project_id,
    )
    if payload is None:
        return refuse(
            ProposalReason.PAYLOAD_INVALID,
            "What NEXUS extracted did not satisfy the payload schema, so it is "
            "asking rather than writing something malformed.",
            kind=spec.kind,
        )

    # A completion names a row the caller already owns, so the summary quotes the
    # stored title — the user's phrase is a fragment of it and the row's own name
    # is what the confirm dialog must show. A creation has no such row yet, so it
    # quotes what was extracted; ``target_label`` there is the *project*, and
    # putting that in the title would have named the task after its parent.
    summary_title = target_label if spec.kind is ActionKind.COMPLETE_TASK else extraction.title

    summary = _summarise(
        spec,
        title=summary_title,
        priority=extraction.priority,
        due_date=extraction.due_date,
        today=today,
        target_label=target_label,
        target_id=target_id,
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
    )


def _build_payload(
    spec: ActionSpec, *, extraction: Extraction, project_id: UUID | None
) -> BaseModel | None:
    """Validate the extracted arguments into the spec's schema.

    Only fields that were actually extracted are passed, so
    ``model_fields_set`` on the resulting payload is exactly the set of things
    the user said. That is what keeps a schema default out of the confirm
    sentence: a field nobody mentioned was never set, and a caller that wants to
    know what was asserted can read ``model_fields_set`` instead of guessing.

    Returns:
        The validated payload, or ``None`` when it does not satisfy the schema.
        The schema's own error is not propagated: a validation failure here means
        the extraction produced something the route would reject, and the right
        answer to that is a refusal with a reason, not a 422 to a caller who
        asked a question rather than submitted a payload.
    """
    raw: dict[str, Any] = {}
    if spec.kind is ActionKind.COMPLETE_TASK:
        return TaskStatusChange(status=TaskStatus.COMPLETED)

    raw[spec.title_field] = extraction.title
    if extraction.priority is not None:
        raw["priority"] = extraction.priority
    if spec.date_field is not None and extraction.due_date is not None:
        raw[spec.date_field] = extraction.due_date
    if spec.kind is ActionKind.CREATE_TASK:
        if project_id is None:
            return None
        raw["project_id"] = project_id

    try:
        return spec.schema(**raw)
    except ValidationError:
        return None


def _payload_to_dict(payload: BaseModel) -> dict[str, Any]:
    """Render a payload's fields as JSON-safe values, UUIDs as strings."""
    rendered: dict[str, Any] = {}
    for name, value in payload.model_dump(mode="json").items():
        rendered[name] = value
    return rendered
