"""Phase 13 — the intent → argument → confirm layer.

Two modules, and the boundary between them is the one that matters:

:mod:`app.ml.actions.extraction`
    Deterministic argument extraction from an utterance, given the classifier's
    prediction. Stdlib only. No model, no network, no service import.
:mod:`app.ml.actions.proposals`
    The closed set of things those arguments may be turned into — a validated
    payload, the permission it needs, the sentence the user checks — and the
    refusals for everything else.

**Nothing here executes.** A proposal is a description of a write, not a write.
Between them the two modules cover every write in the app — create, update,
re-status, schedule, tag, publish, archive and delete, across tasks, projects,
knowledge, learning, the planner and the caller's own account — so the limits
are no longer *which* actions exist but **how many rows one may name**. One
utterance names one row, the user reads a sentence about that row and presses
Confirm, and only then does anything change;
:attr:`ActionProposal.requires_confirmation` is a property returning ``True`` and
cannot be set. A request for a whole collection is refused rather than answered,
and a destructive proposal carries an explicit ``destructive`` flag so the client
can warn before the button and the confirm route can demand a second, deliberate
press. Both modules' docstrings say the whole of it; the short version is that
``Intent.TASK_MANAGE`` covers "add a task" and "delete everything" identically,
so a layer that chose between them would be guessing — and the guessing is now
confined to the one row an action names, in a sentence the user is about to read.
"""

from __future__ import annotations

from app.ml.actions.extraction import (
    Argument,
    ExtractedVerb,
    Extraction,
    RowCandidate,
    RowMatch,
    RowMatchFailure,
    TaskCandidate,
    TaskMatch,
    TaskMatchFailure,
    day_start_utc,
    extract_arguments,
    extract_verb,
    find_date,
    local_day,
    match_reference,
    match_task_reference,
    resolve_weekday,
    strip_command_prefix,
)
from app.ml.actions.proposals import (
    ACTION_SPECS,
    ActionAck,
    ActionKind,
    ActionProposal,
    ActionSpec,
    ProjectStatusWrite,
    ProposalContext,
    ProposalReason,
    ProposalRefusal,
    TaskScheduleWrite,
    is_proposal,
    propose_action,
    render_summary,
)

__all__ = [
    "ACTION_SPECS",
    "ActionAck",
    "ActionKind",
    "ActionProposal",
    "ActionSpec",
    "Argument",
    "ExtractedVerb",
    "Extraction",
    "ProjectStatusWrite",
    "ProposalContext",
    "ProposalReason",
    "ProposalRefusal",
    "RowCandidate",
    "RowMatch",
    "RowMatchFailure",
    "TaskCandidate",
    "TaskMatch",
    "TaskMatchFailure",
    "TaskScheduleWrite",
    "day_start_utc",
    "extract_arguments",
    "extract_verb",
    "find_date",
    "is_proposal",
    "local_day",
    "match_reference",
    "match_task_reference",
    "propose_action",
    "render_summary",
    "resolve_weekday",
    "strip_command_prefix",
]
