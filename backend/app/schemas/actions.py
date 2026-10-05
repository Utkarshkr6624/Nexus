"""Request/response models for the propose → confirm action surface.

Four models in, three pairs out, and the wire contract is deliberately the *narrow*
half of what the proposal layer knows. :mod:`app.ml.actions.proposals` carries
provenance, the service pointer and the argument spans; a client needs the
sentence to confirm and the payload to send back, and shipping the rest would
couple every consumer to a module whose internals are still moving.

Five decisions shape the file.

**A refusal is a response, not an error.** :class:`ProposeActionRead` is one model
with a ``proposed`` flag and exactly one of :attr:`proposal` /
:attr:`refusal` populated. A union type would be more precise and less usable: a
client would have to discriminate on the response itself to learn whether it may
show a confirm button, and "NEXUS read this and would not act" would become an
exception path rather than the ordinary answer it is. ``proposed: false`` with a
``reason`` is the 200.

**The proposal's payload is published verbatim, and the confirm body takes it
back as free-form JSON.** :class:`ConfirmActionRequest.payload` is a
``dict[str, Any]`` rather than a ``Union[TaskCreate, ProjectCreate, ...]``
because the schema it must satisfy depends on ``kind``, and a union would have
to be re-declared here and could drift from the table
:data:`app.ml.actions.proposals.ACTION_SPECS` already holds. The endpoint
resolves the schema from the table and validates against that, so there is still
exactly one definition of what each payload means.

**``requires_confirmation`` is echoed as the answer it already is.**
:attr:`ActionProposalRead.requires_confirmation` is a property returning ``True``
in the proposal layer, not a field, so a client cannot read it as a negotiable.
``destructive`` is the opposite case and is echoed for the other reason: it is a
**real field** derived from :data:`app.ml.actions.proposals.ACTION_SPECS`, and a
confirm dialog that has to ask its own table whether a kind discards a row will
eventually ask it differently. Publishing it keeps the warning the user sees and
the gate :meth:`app.api.v1.actions.confirm_action` applies reading from one
source.

**``intent`` is required on the confirm body.** It is the cheapest stateless
binding between the two calls: the endpoint refuses a confirm whose ``intent``
disagrees with the intent :data:`~app.ml.actions.proposals.ACTION_SPECS` says
this kind can only come from — or with any of the other intents that kind is
trained to also answer as, which is what a second reading of the same sentence
legitimately produces. It binds shape to origin; it is not, and does not pretend
to be, proof that a proposal happened.

**The three payload models the action surface adds are re-exported, not
declared.** :class:`ActionAck` (the kinds whose whole effect is a verb),
:class:`TaskScheduleWrite` and :class:`ProjectStatusWrite` are declared once, in
:mod:`app.ml.actions.proposals`, because that is the module that references them
from :data:`~app.ml.actions.proposals.ACTION_SPECS` and builds them into every
proposal. They are imported and listed in ``__all__`` here so a consumer of the
wire models can reach the whole contract from one module, but **this file does
not define them.** Two classes with one name and two field lists would be the
"second definition waiting to drift" the paragraph above is about, and the
endpoint would be validating a payload against whichever one the table happened
to hold.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.ml.actions.proposals import (
    ActionAck,
    ActionKind,
    ActionProposal,
    ProjectStatusWrite,
    ProposalRefusal,
    TaskScheduleWrite,
)

__all__ = [
    "ActionAck",
    "ActionProposalRead",
    "ConfirmActionRead",
    "ConfirmActionRequest",
    "ExtractArgumentRead",
    "ProjectStatusWrite",
    "ProposalRefusalRead",
    "ProposeActionRead",
    "ProposeActionRequest",
    "TaskScheduleWrite",
]


class ProposeActionRequest(BaseModel):
    """What the caller submits to be understood: the utterance, and where it lands.

    **``text`` declares no length bound of its own.** The classifier's own
    :meth:`app.ml.classifier.IntentClassifier.predict` validation is what
    rejects blank, over-long and credential-shaped text, and duplicating those
    bounds here would mean two rules that could disagree — a request refused at
    the edge for a different reason than the one the model would have given, and
    credential screening (which lives only in the classifier) silently absent for
    any caller that reached this endpoint with an empty string.
    """

    model_config = ConfigDict(extra="forbid")

    text: str = Field(
        description=(
            "The utterance, sent verbatim and untransformed. Normalising it before "
            "it reaches the tokenizer would be a distribution shift the model was "
            "never trained on."
        ),
        examples=["add a task to draft the migration plan for friday"],
    )
    project_id: UUID | None = Field(
        default=None,
        description=(
            "The project a created task should belong to. Required for a task "
            "creation; another account's project is a 404, never a 403. The caller's "
            "own zone is taken from `?tz=`, as it is on every planner route."
        ),
    )


class ExtractArgumentRead(BaseModel):
    """One extracted field and the span it came from.

    Published so a confirm dialog can show its own reasoning — *"due Friday,
    matched 'for friday'"* — rather than asking the user to trust a number. Every
    member carries a non-empty ``matched_text`` because
    :class:`app.ml.actions.extraction.Argument` guarantees it, and a row with an
    empty span would mean the extractor had invented something.
    """

    model_config = ConfigDict(from_attributes=True)

    field: str = Field(description="The payload field this argument fills.")
    value: str = Field(description="The value as text, which is what a dialog renders.")
    matched_text: str = Field(description="The span of the utterance it was read from.")
    rule: str = Field(description="The extraction rule that consumed that span.")


class ActionProposalRead(BaseModel):
    """One action the user is being asked to confirm, in wire form.

    ``summary`` is the whole effect in one sentence — what will change, what it
    will be called, when, and where — and it was built at proposal time, so the
    sentence a client stores is the sentence the user saw. The payload beside it
    is what a confirm sends back, and ``target_id`` is the row a completion acts
    on.
    """

    model_config = ConfigDict(from_attributes=True)

    kind: ActionKind = Field(description="Which action this is; a member of the closed set.")
    intent: str = Field(description="The classifier's winning intent for the utterance.")
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="The model's confidence in this intent, not its test-set accuracy.",
    )
    summary: str = Field(description="The one sentence the user checks before agreeing.")
    requires_confirmation: bool = Field(
        description="Always true. Not a request — there is no path that skips it."
    )
    destructive: bool = Field(
        description=(
            "True for actions that discard a row; the confirm dialog should say so. "
            "Derived from the spec table, and it is the flag the confirm endpoint "
            "checks before it will execute one."
        )
    )
    permission: str = Field(
        description="The capability the confirming call will be checked against."
    )
    service: str = Field(
        description="Class name of the service that will run, e.g. ``TaskService``."
    )
    module: str = Field(description="Import path of that service's module.")
    entrypoint: str = Field(description="The method that will be called on it.")
    payload_schema: str = Field(
        description="Pydantic model the payload validates against, e.g. ``TaskCreate``."
    )
    payload: dict[str, Any] = Field(description="The validated payload, JSON-ready.")
    target_id: UUID | None = Field(
        default=None,
        description=(
            "The row this acts on or belongs to. For a creation it is the parent "
            "the row will be filed under; for everything else it is the one existing "
            "row the action changes, and the confirm body must send it back."
        ),
    )
    target_label: str | None = Field(
        default=None, description="The caller's own name for that row, so the dialog can quote it."
    )
    arguments: list[ExtractArgumentRead] = Field(
        default_factory=list,
        description="Every extracted field with its provenance, in extraction order.",
    )
    notes: list[str] = Field(
        default_factory=list,
        description="Softer observations, e.g. a date phrase that was seen but not resolved.",
    )

    @classmethod
    def from_proposal(cls, proposal: ActionProposal) -> ActionProposalRead:
        """Render one :class:`ActionProposal` as this response model.

        Args:
            proposal: The proposal the user is about to be shown.

        Returns:
            The wire form. ``payload`` is dumped in JSON mode, so UUIDs and dates
            arrive as strings and the client does not have to know which of them
            are scalars.
        """
        return cls(
            kind=proposal.kind,
            intent=proposal.intent,
            confidence=round(float(proposal.confidence), 6),
            summary=proposal.summary,
            requires_confirmation=proposal.requires_confirmation,
            destructive=proposal.destructive,
            permission=str(proposal.permission),
            service=proposal.spec.service,
            module=proposal.spec.module,
            entrypoint=proposal.spec.entrypoint,
            payload_schema=proposal.spec.schema.__name__,
            payload=proposal.payload.model_dump(mode="json"),
            target_id=proposal.target_id,
            target_label=proposal.target_label,
            arguments=[ExtractArgumentRead.model_validate(arg) for arg in proposal.arguments],
            notes=list(proposal.notes),
        )


class ProposalRefusalRead(BaseModel):
    """No proposal, and the closed-vocabulary reason why.

    ``reason_code`` is what a client branches on and ``reason`` is what it shows.
    Prose alone could be asserted on but not acted on, so both ship.
    """

    model_config = ConfigDict(from_attributes=True)

    kind: ActionKind | None = Field(
        default=None,
        description="The kind NEXUS understood but would not propose, when it knows one.",
    )
    intent: str = Field(description="The classifier's winning intent for the utterance.")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence behind that intent.")
    reason_code: str = Field(
        description=(
            "One of ``app.ml.actions.proposals.ProposalReason``: unsupported_intent, "
            "destructive_request, verb_not_recovered, title_not_recoverable, "
            "task_reference_ambiguous, task_reference_not_found, target_ambiguous, "
            "target_not_found, field_not_recoverable, entity_not_recognised, "
            "context_missing, payload_invalid. ``destructive_request`` now means "
            "only 'that was a whole collection', never 'deletes are off'."
        )
    )
    reason: str = Field(description="Human-readable explanation, safe to render to a user.")
    arguments: list[ExtractArgumentRead] = Field(
        default_factory=list,
        description="What NEXUS did manage to read, so the dialog can show the near miss.",
    )
    notes: list[str] = Field(default_factory=list, description="Softer observations, if any.")


class ProposeActionRead(BaseModel):
    """The answer to "what would NEXUS do about this sentence".

    **One of :attr:`proposal` and :attr:`refusal` is set, never both and never
    neither.** ``proposed`` is the flag to branch on, and it is redundant with
    that on purpose: a client should not have to test two nullable fields to find
    out whether it may render a confirm button.
    """

    proposed: bool = Field(
        description="Whether there is an action to confirm. False is a normal answer, not an error."
    )
    intent: str = Field(description="The classifier's winning intent for the utterance.")
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Softmax probability of that intent for this utterance.",
    )
    proposal: ActionProposalRead | None = Field(
        default=None, description="The action to confirm. Null when nothing was proposed."
    )
    refusal: ProposalRefusalRead | None = Field(
        default=None, description="Why nothing was proposed. Null when a proposal came back."
    )

    @classmethod
    def from_proposal(cls, proposal: ActionProposal) -> ProposeActionRead:
        """Wrap a proposal as a ``proposed: true`` answer."""
        return cls(
            proposed=True,
            intent=proposal.intent,
            confidence=round(float(proposal.confidence), 6),
            proposal=ActionProposalRead.from_proposal(proposal),
            refusal=None,
        )

    @classmethod
    def from_refusal(cls, refusal: ProposalRefusal) -> ProposeActionRead:
        """Wrap a refusal as a ``proposed: false`` answer."""
        return cls(
            proposed=False,
            intent=refusal.intent,
            confidence=round(float(refusal.confidence), 6),
            proposal=None,
            refusal=ProposalRefusalRead(
                kind=refusal.kind,
                intent=refusal.intent,
                confidence=round(float(refusal.confidence), 6),
                reason_code=refusal.reason_code,
                reason=refusal.reason,
                arguments=[ExtractArgumentRead.model_validate(arg) for arg in refusal.arguments],
                notes=list(refusal.notes),
            ),
        )


class ConfirmActionRequest(BaseModel):
    """What the caller submits to carry out a proposal they have seen.

    **``payload`` is untrusted.** The user may have edited it in the confirm
    dialog, and the point of accepting an edit is that NEXUS checks it rather than
    trusting it: the endpoint resolves the real Pydantic model from ``kind``,
    rejects unknown keys, and re-validates against it. ``dict[str, Any]`` rather
    than a union, because the model to validate against is a property of ``kind``
    and re-declaring that mapping here is a second definition waiting to drift.
    """

    model_config = ConfigDict(extra="forbid")

    kind: ActionKind = Field(
        description="Which action to carry out. Anything outside this set is a 422."
    )
    intent: str = Field(
        description=(
            "The intent the proposal reported. Refused when it disagrees with the "
            "one this kind can only have come from, or with any of the other "
            "intents this kind is also trained to answer as."
        )
    )
    payload: dict[str, Any] = Field(
        description="The proposal's payload, possibly edited by the user. Validated as untrusted input."
    )
    target_id: UUID | None = Field(
        default=None,
        description=(
            "The row the action acts on — the task a completion marks done, the note a "
            "delete removes. Re-resolved through an owner-scoped lookup, so another "
            "account's id is a 404 before anything is written. Required by every "
            "kind that names an existing row."
        ),
    )
    confirm_destructive: bool = Field(
        default=False,
        description=(
            "Set only when the user pressed Confirm on a proposal marked destructive; "
            "the endpoint refuses a destructive kind without it. Defaults to false so "
            "an ordinary confirm cannot carry it by accident, and is checked against "
            "the spec table rather than against anything the client says the kind is."
        ),
    )


class ConfirmActionRead(BaseModel):
    """What actually happened, said by the service that did it.

    **``outcome`` is the field a client branches on** and it is one of
    ``created``, ``updated``, ``deleted`` or ``no_op``. ``no_op`` is a
    success-shaped answer, not an error: a replayed confirm reports the row that
    is already there and ``applied: false``, because the desired state was
    reached by the first call and telling the user their second press failed
    would be a lie about a system that worked. The same holds for a transition
    into a state the row is already in. ``applied`` says whether this request is
    what moved the row, and ``message`` is the sentence to show — written from
    the service's return value rather than from what the caller hoped for.

    ``deleted`` carries the id of the row that is **gone**. That is deliberate:
    a client refreshing a list needs the id to drop, and a "deleted" answer with
    no id would leave it guessing which of the rows it was holding.
    """

    kind: ActionKind = Field(description="The action that was confirmed.")
    entity: str = Field(
        description=(
            "What kind of row was written: task, project, note, bookmark, concept, "
            "link, learning_goal, skill, calendar_event, work_session, user, "
            "repository. The noun is derived from the kind's own spec row rather "
            "than written by the dispatcher, so it cannot drift from the kind."
        )
    )
    entity_id: UUID = Field(description="The row's identifier, as the service returned it.")
    outcome: Literal["created", "updated", "deleted", "no_op"] = Field(
        description="What this request did to the row."
    )
    applied: bool = Field(
        description="Whether this request changed anything. False on a replay or an already-satisfied transition."
    )
    message: str = Field(description="One truthful sentence describing what happened.")
