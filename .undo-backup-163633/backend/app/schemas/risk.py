"""Wire shapes for the Phase 7 Risk Center.

The risk, its evidence, the tallies around it, and the record of what one
detection run did.

Phase 6's rule — "a number that could not be computed is ``None``, never ``0``" —
is inherited here and then *deliberately broken in one place*, because a stored
risk and a computed score are not the same kind of fact.

**A persisted risk always has a score.** A ``RiskResult`` with no score is one
the detector declined to judge, and the detection service never persists those:
there is no row to hang "not enough data to assess this" on, so the sentence goes
onto the evaluation summary (:class:`EvaluationRead`) where it can be shown
without inventing a risk. What reaches this module is a row that exists *because*
something was measured, and a risk nobody measured is a thing that was never
found. Typing ``score`` as ``int`` with its 0-100 bounds is therefore not
optimism, it is the statement that a zero here is a measurement — the arithmetic
came out at zero, and the evidence beside it says why.

Four nullability decisions, and what each one is for
----------------------------------------------------
:attr:`RiskRead.recommendations` is always a list and empty when there is nothing
to suggest. "No suggested action yet" is a real state with its own rendering in
the Risk Center, and a null would force every client to branch on the difference
between "none" and "not looked". The emptiness is the information.

:attr:`RiskRead.resolved_at` is null for a live risk, and that null is the whole
point of the column. "How long was this open" is a subtraction from it, and the
alternative — a zero timestamp, or a boolean — is a second answer that could
disagree with the first.

:attr:`RiskRead.entity_type` and :attr:`RiskRead.entity_id` are null together,
for an account-level condition such as workload or consistency, which is about
the user's whole plan rather than a row. They are not defaulted to a sentinel
entity, because a synthetic row id would then be a join target that resolves to
nothing.

:attr:`RiskRead.metadata` is an empty dict rather than null when a detector
recorded nothing extra. The difference between "no extra inputs" and "not
recorded" is not one the engine can observe after the fact, and pretending
otherwise would put a second meaning on the same absence.

Why the counts are never missing a band
--------------------------------------
:attr:`RiskListRead.by_severity` always carries all four severities, zeroed where
the count is nothing, and :class:`RiskSummaryRead` states the same four counts as
fields rather than as a map. A count of zero is a real measurement — the user has
no critical risks, which is the good news the page exists to deliver — so unlike a
score it needs no ``None`` escape hatch, and a client can read ``by_severity.high``
without a fallback default that would quietly turn a missing key into the same
number as an empty one.

Where the models live
---------------------
:class:`EvaluationRead` is here rather than in the recommendation module because
a detection run is a fact about risks; the only reason it mentions recommendations
is the one count of how many actions that run produced. ``RecommendationSummaryRead``
lives in :mod:`app.schemas.recommendation` and is imported the one way round, so
neither module has to know about the other at import time.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Self

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from app.models.enums import RiskSeverity
from app.schemas.recommendation import RecommendationSummaryRead, band_count_sentence

__all__ = [
    "EvaluationRead",
    "RiskEvidenceRead",
    "RiskListRead",
    "RiskRead",
    "RiskSummaryRead",
    "SeverityBandRead",
    "count_sentence",
]

#: Severity bands, most severe first. Used to fill the count dictionaries in a
#: fixed order so two responses that carry the same numbers serialise identically
#: — a header that reorders itself between renders is a diff nobody can read.
_SEVERITY_ORDER: tuple[str, ...] = tuple(severity.value for severity in RiskSeverity)

#: The plural noun the risk header counts. "Live" because every risk the engine
#: stores is a condition a detector re-derives while it holds; a resolved one is
#: history, and a header that kept counting it would never let a user see that
#: clearing a backlog changed anything.
_LIST_SUBJECT = "live risks"


def count_sentence(by_severity: Mapping[str, int], total: int) -> str:
    """One factual sentence describing a set of risk counts.

    Delegates to :func:`app.schemas.recommendation.band_count_sentence` rather than
    reimplementing the sentence, so this header and any recommendation header the
    service composes are written by one implementation and cannot drift apart in
    tone.

    Args:
        by_severity: Counts keyed by severity word. Bands with no entries are
            left out rather than printed as a zero.
        total: The number of rows the counts describe. Used for the leading count
            when the two disagree, so a stale breakdown never silently changes
            the headline.

    Returns:
        A sentence such as ``"5 live risks: 1 critical, 2 high, 2 low."`` An
        empty set returns ``"No live risks."``
    """
    return band_count_sentence(by_severity, total, _LIST_SUBJECT, _SEVERITY_ORDER)


class RiskEvidenceRead(BaseModel):
    """One line of a risk's "why": an input, and the points it contributed.

    These are the same three fields the pure scoring module produces
    (:class:`app.services.risk.scoring.RiskEvidence`), carried through to the
    client unchanged. The whole point of persisting them is that a stored risk
    must still be able to explain itself months later, after the aggregates it
    was computed from have been rebuilt — so nothing here is re-derived at read
    time, and a line that contributed no points carries ``0.0`` rather than being
    dropped, because "the deadline is 24 hours away" is part of the explanation
    even though it is not part of the arithmetic.
    """

    model_config = ConfigDict(from_attributes=True)

    label: str = Field(description="The input, named: 'Work not scheduled before the deadline'.")
    detail: str = Field(
        description="That input with its numbers, in words a person can check "
        "against the rows they recorded."
    )
    contribution: float = Field(
        description="Points of the risk's 0-100 score this line accounts for. The "
        "moving lines account for the score to within rounding — the score is a "
        "whole number and each share is carried at two decimals, so they need not "
        "sum to it exactly. Lines that describe an input without moving the "
        "number carry 0.0."
    )


class RiskRead(BaseModel):
    """One stored risk: what it is, how loudly it speaks, and what to do about it.

    Validates straight from a ``Risk`` row, so the service can hand a repository
    result to the model without unpacking it field by field. The
    recommendations are the one part that is not on the row — they are joined in,
    because a risk and the actions proposed for it are stored in two tables that
    can be read independently, and because deleting a risk must not delete the
    record of having acted on it.
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID = Field(description="Identifier of the stored risk.")
    risk_type: str = Field(
        description="Which detector produced this: one of the ``RiskType`` values "
        "— `deadline`, `workload`, `estimation`, `consistency`, `project`, "
        "`scheduling`, `task`. A closed set, so every stored risk names a "
        "condition some detector knows how to re-derive or resolve."
    )
    severity: str = Field(
        description="How loudly this speaks, as one of the ``RiskSeverity`` values. "
        "Derived from `score` and never set independently, so a risk cannot claim "
        "91 and read `medium`."
    )
    score: int = Field(
        ge=0,
        le=100,
        description="0-100, and never null. A row only exists because a detector "
        "produced a scored result, so a zero here is a measurement — the "
        "arithmetic came out at zero and the evidence says why — rather than the "
        "absence of one. NEXUS-derived, not a probability that anything will happen.",
    )
    title: str = Field(
        description="A few words naming the condition: 'Deadline pressure on the "
        "Q3 report'. Neutral and factual, never a judgement of the person."
    )
    description: str = Field(
        description="The condition stated in full, with the numbers that produced "
        "it. Says what is true about the recorded work and nothing about what it "
        "implies about the user."
    )
    evidence: list[RiskEvidenceRead] = Field(
        default_factory=list,
        description="The ordered 'why'. Never empty on a stored risk: a score with "
        "nothing behind it is the exact thing the brief forbids.",
    )
    evidence_strength: str = Field(
        default="low",
        description="How much data the score was computed from, as one of the "
        "``EvidenceStrength`` values. This is a banded sample count and not model "
        "confidence; a thin sample is still reported, just never as though it were firm.",
    )
    entity_type: str | None = Field(
        description="What the risk is about — `task`, `project` or `account`. Null "
        "together with `entity_id` for an account-level condition, which has no row "
        "to point at."
    )
    entity_id: uuid.UUID | None = Field(
        description="The row this risk is about. Null together with `entity_type`. "
        "Kept beside `entity_type` rather than keyed on alone because a task id and "
        "a project id are drawn from the same uuid space and one would swallow the other."
    )
    status: str = Field(
        description="Where the risk sits in its lifecycle; one of the "
        "``RiskStatus`` values. `acknowledged` means 'still true, no longer needs "
        "my attention' — it is not `resolved`."
    )
    detected_at: datetime = Field(
        description="When this condition was first detected, and it has to survive a "
        "re-detection: the repository updates a live row in place, so if the refresh "
        "reset this clock then 'how long has this been going on' would always answer "
        "with the length of the last evaluation rather than the age of the condition."
    )
    resolved_at: datetime | None = Field(
        description="When the status last became terminal — resolved by the "
        "condition disappearing, or dismissed by the user. Null while the risk is "
        "live, and that null is what makes 'how long was this open' answerable."
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("metadata_", "metadata"),
        description="The raw inputs the score was computed from. Kept beside the "
        "evidence so a stored risk stays auditable after the analytics it was "
        "built from have been rebuilt. Empty when a detector recorded nothing "
        "extra.",
    )
    recommendations: list[RecommendationSummaryRead] = Field(
        default_factory=list,
        description="Actions proposed for this risk, each with its reason. Empty is "
        "a real state the UI renders as 'No suggested action yet' — a rule may "
        "have nothing to propose for this condition, and null would have meant "
        "'not looked'.",
    )

    @model_validator(mode="before")
    @classmethod
    def _accept_stored_evidence_lines(cls, value: Any) -> Any:
        """Read evidence stored as bare strings as lines with no points.

        The column is an untyped JSONB list, and a detector that records a line
        as a single string has still recorded evidence — only with no
        contribution attributed to it. Mapping it to a zero-contribution line
        keeps such a risk explainable instead of failing validation at the edge
        of the response; the alternative is a 500 on a row whose only fault is
        being terser than the schema expects. Structured entries and the scoring
        module's own dataclass pass through untouched.
        """
        if not isinstance(value, dict) or "evidence" not in value:
            return value
        stored = value["evidence"]
        if not isinstance(stored, list):
            return value
        value = dict(value)
        value["evidence"] = [
            {"label": line, "detail": line, "contribution": 0.0} if isinstance(line, str) else line
            for line in stored
        ]
        return value


class RiskListRead(BaseModel):
    """One page of risks, with the tallies the Risk Center header shows.

    Flat rather than wrapped in the shared :class:`app.schemas.common.Page`
    envelope, for the same reason the recommendation list is: `by_severity` and
    `summary` belong on screen next to the rows, not buried inside a `meta`
    object whose other members are pagination bookkeeping.
    """

    items: list[RiskRead] = Field(
        default_factory=list,
        description="The risks on this page, most severe first and then most "
        "recently detected. Empty when the filters match nothing.",
    )
    total: int = Field(default=0, ge=0, description="How many risks match the filters.")
    limit: int = Field(default=0, ge=0, description="Maximum rows the page may hold.")
    offset: int = Field(default=0, ge=0, description="How many matching rows were skipped.")
    by_severity: dict[str, int] = Field(
        default_factory=dict,
        description="Counts across every matching risk, not just this page. Always "
        "carries all four severities, zeroed where nothing was found, so the "
        "response shape does not change as the last critical risk is resolved.",
    )
    summary: str = Field(
        default="",
        description="One factual sentence describing the counts, for the header. A "
        "validated response always carries one; a service is free to supply its "
        "own sentence instead.",
    )

    @model_validator(mode="after")
    def _fill_severities_and_summary(self) -> Self:
        """Complete the tally and compose the header sentence.

        The counts are copied rather than mutated so a dictionary the caller
        still holds is not rewritten underneath them.
        """
        filled = {key: int(self.by_severity.get(key, 0)) for key in _SEVERITY_ORDER}
        for key, value in self.by_severity.items():
            if key not in filled:
                filled[key] = value
        self.by_severity = filled
        if not self.summary:
            self.summary = count_sentence(self.by_severity, self.total)
        return self


class SeverityBandRead(BaseModel):
    """One severity band, with the scores that fall into it.

    The four words ``critical`` / ``high`` / ``medium`` / ``low`` are a four-way
    split of a 0-100 number, and a number whose bands the reader cannot see is a
    number they cannot place. This model is the definition travelling with the
    counts, so a client states the ladder from the deployment that produced it
    rather than from a second copy of 75/50/25 it hardcoded itself.
    """

    severity: str = Field(description="The band this entry defines, most severe first.")
    minimum_score: int = Field(ge=0, le=100, description="Lowest score in this band, inclusive.")
    maximum_score: int | None = Field(
        default=None,
        ge=0,
        le=100,
        description="Highest score in this band, inclusive. Null for the top band, "
        "which has no ceiling.",
    )
    description: str = Field(
        description="One sentence saying what the band is worth, in words rather "
        "than as a number to be interpreted."
    )


class RiskSummaryRead(BaseModel):
    """The compact tallies the dashboard shows, the bands they are counted in,
    and nothing else.

    A whole page of risks behind five numbers is the wrong shape for a dashboard
    tile, and it is the wrong shape for a screen reader announcement too. The
    counts are of *live* risks — ``active`` and ``acknowledged`` — because a
    resolved one is history, and a dashboard that kept counting it would never
    let a user see that clearing a backlog changed anything.

    :attr:`severity_bands` is what the counts are counted *in*, and it is here
    rather than left to each client to hardcode: the four band names carry no
    meaning on their own, so a screen that wants to say why a risk is critical
    has nothing to say until somebody states the ladder.
    """

    critical: int = Field(default=0, ge=0, description="Live risks in the critical band.")
    high: int = Field(default=0, ge=0, description="Live risks in the high band.")
    medium: int = Field(default=0, ge=0, description="Live risks in the medium band.")
    low: int = Field(default=0, ge=0, description="Live risks in the low band.")
    total: int = Field(
        default=0,
        ge=0,
        description="The four bands added together. Carried rather than derived at "
        "the call site so the dashboard and the Risk Center header cannot quote "
        "different totals for the same query.",
    )
    needs_attention: bool = Field(
        default=False,
        description="True when at least one live risk is `high` or `critical`. "
        "Medium and low are reported but do not raise the flag: a dashboard that "
        "raises an alarm over an amber band teaches users to ignore it, which is "
        "the behaviour the brief's neutral register rules out.",
    )
    severity_bands: list[SeverityBandRead] = Field(
        default_factory=list,
        description="What each severity band means, most severe first: the score "
        "range it covers and one sentence defining it. Always all four, in a "
        "fixed order, so the response shape does not depend on the data.",
    )

    @model_validator(mode="after")
    def _derive_needs_attention(self) -> Self:
        """Set the flag from the counts.

        Derived rather than supplied because its definition is fixed — 'high or
        worse' — and a second opinion about it could only ever disagree with the
        counts printed beside it.
        """
        self.needs_attention = self.critical > 0 or self.high > 0
        return self


class EvaluationRead(BaseModel):
    """What one detection run did, in one row.

    The result of ``POST /intelligence/evaluate`` and each entry of
    ``GET /intelligence/evaluations``. It is a *response* shape rather than a
    projection of the ``risk_evaluations`` row, because it carries two things the
    row cannot: whether the pass ran at all, and the reasons the detectors
    declined to judge.

    That second part matters more than it looks. A detector with too little data
    returns no score and no row, so a run that assessed nothing looks identical
    to a run that found nothing unless the reasons are carried. ``evaluated``
    and ``reason_if_not_evaluated`` are how "not enough data" reaches the screen
    as a sentence instead of as an absence.

    The counts are a snapshot of a moment and deliberately not a history: the
    risks themselves are the durable record, and one row per risk per run is the
    multiplication the brief warns against. Per-risk timing is recoverable from
    ``RiskRead.detected_at`` and ``RiskRead.resolved_at``, and storing it twice
    would create a second answer that could disagree with the first.
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    evaluated: bool = Field(
        default=True,
        description="Whether the pass actually ran. False means the counts below "
        "describe nothing: they are zero because nothing was measured, not "
        "because nothing was found.",
    )
    reason_if_not_evaluated: str | None = Field(
        default=None,
        description="Why the pass did not run, or null when it did. Shown to the "
        "user in place of the counts; it is set whenever `evaluated` is false, "
        "though the schema does not enforce the pairing, because a response "
        "model that raised here would surface a service mistake as a 500.",
    )
    evaluated_at: datetime = Field(
        description="When the run started. The identity of the run, together with "
        "the row's run token; two runs in the same microsecond are told apart by "
        "the token, not by this field."
    )
    window_start: datetime = Field(
        description="Start of the window the run reasoned over, so the snapshot is "
        "interpretable without also reconstructing which range produced it."
    )
    window_end: datetime = Field(description="End of that window.")
    risks_found: int = Field(
        default=0,
        ge=0,
        description="Live risks after the pass. Not simply created plus updated: "
        "the run also resolves live risks it did not re-detect, so the three "
        "counts below do not add up to this one.",
    )
    risks_created: int = Field(
        default=0,
        ge=0,
        description="New rows written, each of which emitted a RISK_DETECTED event. "
        "This is the number that answers 'is the engine finding new things, or "
        "only re-reporting the same ones'.",
    )
    risks_updated: int = Field(
        default=0,
        ge=0,
        description="Existing live rows refreshed in place, which is the "
        "deduplication working — the same condition found again is updated, not "
        "duplicated.",
    )
    risks_resolved: int = Field(
        default=0,
        ge=0,
        description="Live risks that were not re-detected this run and were "
        "transitioned to resolved, because the condition behind them is gone.",
    )
    by_severity: dict[str, int] = Field(
        default_factory=dict,
        description="Counts by severity across the risks this run touched. Empty "
        "when the run touched none.",
    )
    by_type: dict[str, int] = Field(
        default_factory=dict,
        description="Counts by risk type — the 'what kind of trouble' breakdown. "
        "Empty when the run touched none.",
    )
    recommendations_created: int = Field(
        default=0,
        ge=0,
        description="Actions this run proposed on the back of the risks it found. "
        "Zero is a normal result: a rule fires on at most one recommendation per "
        "entity, and most runs re-find a risk whose suggestion already exists.",
    )
    duration_ms: int = Field(
        default=0,
        ge=0,
        description="Milliseconds the pass took. The only way to notice that a "
        "change made evaluation expensive, which is the performance failure a "
        "functional test never catches.",
    )
