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
There is no delete, no cancel and no block, and :attr:`ActionProposal.destructive`
is a property that returns ``False`` rather than a field that could be set to
``True``. Both modules' docstrings say why in full; the short version is that
``Intent.TASK_MANAGE`` covers "add a task" and "delete everything" identically,
so a layer that chose between them would be guessing, and deletion is not a
guess worth making with somebody's work history.
"""

from __future__ import annotations

from app.ml.actions.extraction import (
    Argument,
    ExtractedVerb,
    Extraction,
    TaskCandidate,
    TaskMatch,
    TaskMatchFailure,
    day_start_utc,
    extract_arguments,
    extract_verb,
    find_date,
    local_day,
    match_task_reference,
    resolve_weekday,
    strip_command_prefix,
)
from app.ml.actions.proposals import (
    ACTION_SPECS,
    ActionKind,
    ActionProposal,
    ActionSpec,
    ProposalContext,
    ProposalReason,
    ProposalRefusal,
    is_proposal,
    propose_action,
    render_summary,
)

__all__ = [
    "ACTION_SPECS",
    "ActionKind",
    "ActionProposal",
    "ActionSpec",
    "Argument",
    "ExtractedVerb",
    "Extraction",
    "ProposalContext",
    "ProposalReason",
    "ProposalRefusal",
    "TaskCandidate",
    "TaskMatch",
    "TaskMatchFailure",
    "day_start_utc",
    "extract_arguments",
    "extract_verb",
    "find_date",
    "is_proposal",
    "local_day",
    "match_task_reference",
    "propose_action",
    "render_summary",
    "resolve_weekday",
    "strip_command_prefix",
]
