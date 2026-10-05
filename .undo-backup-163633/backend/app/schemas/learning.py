"""Wire shapes for Phase 9 learning intelligence.

Goals, skills, the evidence between them, and the four read-time figures the
dashboard is built from.

One rule governs every number on this surface
---------------------------------------------
**A level on this surface is always somebody's claim, and the response says whose.**
:attr:`SkillRead.current_level` is a number and :attr:`SkillRead.level_source`
says whether the person typed it or NEXUS derived it. They are required
together for that reason: a response model that let ``current_level`` travel
without ``level_source`` would let a client render ``2/5`` with no indication of
where it came from, and that single omission is the difference between "your
self-assessed level is 2" and "you are not good at Machine Learning". There is no
judgement field anywhere in this module, and none may be added — the phase holds
a claim and its provenance, and that is the whole of it.

Nullability: the two rules, applied to every field below
--------------------------------------------------------
**Every nullable field is required and typed ``T | None``, never
``Optional[T] = None``.** The key is *always present* in the JSON. A client that
reads ``gap.days_since_last_activity`` and finds the key missing cannot tell a
skill nobody has touched from a payload built by an older version of the server,
and the two want different sentences on screen. :class:`RiskRead` does the same
thing and for the same reason.

**A figure that could not be computed is ``None``, never ``0``.** The three
places this bites hardest:

* :attr:`LearningActivityBucketRead.minutes` and
  :attr:`LearningActivitySeriesRead.total_minutes` are null for a bucket in
  which nobody logged a duration. ``0`` would claim that time was measured and
  found to be nothing, which is a different — and much more confident — claim
  than "nobody said how long it took".
* :attr:`LearningFeatureValues.goal_progress` is null when the account has no
  goals at all, because an account with no goals does not have goals that are
  zero percent complete.
* :attr:`LearningFeatureValues.completion_rate` is null over an empty
  denominator rather than ``0.0``, which would read as "you complete nothing".

A genuine zero stays a plain number. ``gap=0`` against a stated target is a
*measurement* — the target is met — and :attr:`SkillGapRead` carries it with
``available: true``, which is what keeps it apart from the unmeasured case. That
distinction is the reason :attr:`SkillGapRead` has an ``available`` flag at all
rather than simply nulling the gap: a null gap and a zero gap are two different
answers and a client must be able to tell which one it has.

Explanations have to carry their figures
----------------------------------------
:attr:`SkillGapRead.explanation` is validated to contain a digit, the same way
:class:`~app.schemas.recommendation.RecommendationRead` rejects a blank reason.
A gap sentence with no number in it — "you have a gap in Machine Learning" — is
exactly the judgement this phase refuses to make, and it renders well enough that
a screenshot review would not catch it. A field validator is the only thing that
still catches it in a service written next year.

Enum columns arrive as ``str``
------------------------------
:attr:`SkillRead.level_source`, :attr:`LearningGoalRead.status` and the rest are
typed ``str`` even though the backend holds a ``StrEnum``. The vocabulary is a
closed set and each field enumerates it in its description, but a response model
that *declared* it would make the wire a second place the vocabulary lives — and
the one that falls out of step with the enum. The service returns ``.value``; the
client checks it against the same closed union it already imports.

List shapes are flat, and tallies sit beside the rows
-----------------------------------------------------
Every list here carries ``items``/``total``/``limit``/``offset`` directly rather
than inside a ``meta`` envelope, matching :class:`app.schemas.risk.RiskListRead`.
The totals are read on screen next to the rows; burying them under ``meta``
invites a header to be written against a page slice and then quoted as though it
described the whole set. The band tallies — ``by_status``, ``by_type``,
``by_level_source`` — are **complete**, zeroed where nothing was found, because a
count of zero is a real measurement (the user has no completed goals, which is
the good news the page exists to deliver) and needs no null escape. That is also
what keeps a response's *shape* from changing as the last completed goal is
archived: a client indexing ``by_status.completed`` never meets a hole where a
number belongs.

Nothing here is a model
-----------------------
:class:`LearningFeatureVectorRead` carries a ``schema_version`` and named numbers,
and that is all. Phase 9 extracts features; it does not train, load, serve or
register anything, and nothing in this file may be joined with ``features`` and
rendered as a prediction.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import (
    LearningActivityType,
    LearningGoalStatus,
    ProjectPriority,
    SkillLevelSource,
)
from app.schemas.recommendation import band_count_sentence

__all__ = [
    "LEARNING_FEATURE_SCHEMA_VERSION",
    "LearningActivityBucketRead",
    "LearningActivityListRead",
    "LearningActivityRead",
    "LearningActivitySeriesRead",
    "LearningActivityWrite",
    "LearningFeatureValues",
    "LearningFeatureVectorRead",
    "LearningGoalListRead",
    "LearningGoalRead",
    "LearningGoalUpdate",
    "LearningGoalWrite",
    "LearningGoalWriteBase",
    "LearningMetricRead",
    "LearningSummaryRead",
    "SkillGapListRead",
    "SkillGapRead",
    "SkillListRead",
    "SkillRead",
    "SkillUpdate",
    "SkillWrite",
]

#: The version stamped on a feature vector. A single string rather than an enum
#: because this is the *contract* with whatever trains on it: a v2 must not be
#: able to typecheck against v1 column meanings, and the frontend carries the
#: closed union that makes that a compile error there.
LEARNING_FEATURE_SCHEMA_VERSION = "learning_features.v1"

#: Goal states, in the order the tally is filled — the lifecycle order, so a
#: header reads "3 active, 1 completed" rather than an alphabetical shuffle.
_GOAL_STATUS_ORDER: tuple[str, ...] = tuple(status.value for status in LearningGoalStatus)
#: Activity types, weakest first. The order is the weighting order, so a list
#: header that walks it cannot put a page view beside a finished project.
_ACTIVITY_TYPE_ORDER: tuple[str, ...] = tuple(kind.value for kind in LearningActivityType)
#: Both members, so ``by_level_source`` has a key for each and a client never has
#: to check which side of the honesty control it is on.
_LEVEL_SOURCE_ORDER: tuple[str, ...] = tuple(source.value for source in SkillLevelSource)
#: The plural nouns the list headers count. "Goals" and "activities" rather than
#: anything about progress: this surface counts what was recorded, never how it
#: is going.
_GOAL_LIST_SUBJECT = "learning goals"
_ACTIVITY_LIST_SUBJECT = "learning activities"

#: The one sentence every unavailable metric carries. A shared constant so a
#: client can recognise the cold-start message without string-matching each
#: metric's own wording, and so it is written once.
NOT_ENOUGH_DATA = "Not enough data to assess this yet."

#: The largest number of minutes any one row may carry, on a goal's estimate or
#: on a single recorded activity. One year of every minute of the day: past it,
#: the figure is not a figure anybody estimated or measured but a value typed to
#: see what would happen. The ceiling is declared here rather than left to the
#: column because ``estimated_effort_minutes`` is a 32-bit ``Integer``, so an
#: unbounded value reaches storage as a ``DataError`` and leaves the client with
#: a 500 and no field to fix — and because ``minutes_in_window`` is a *sum* over
#: whatever was accepted, one absurd row poisons every window it lands in.
MAX_LEARNING_MINUTES = 365 * 24 * 60

#: The band :attr:`SkillListRead.by_category` counts the user's uncategorised
#: skills under. The category vocabulary is deliberately open, so this is the
#: **only** key NEXUS invents here, and it exists for one reason: the tally has
#: to add up to ``total``. A breakdown that silently drops the skills nobody
#: grouped sums to less than the number printed beside it, and a client summing
#: the bands gets a number that is not the total with nothing saying why.
UNCATEGORISED_CATEGORY = "uncategorised"


def _trimmed(value: Any) -> Any:
    """Strip the whitespace around a caller-supplied name before it is checked.

    ``"  FastAPI  "`` and ``FastAPI`` are the same name written twice, and only
    the trimmed form collides with the row already in the table — so the
    trimming has to happen before ``min_length`` and before the duplicate
    lookup, not after the row is written. A name that is *only* whitespace then
    fails ``min_length`` as the empty string it is, rather than being accepted as
    a row whose name renders as nothing.
    """
    return value.strip() if isinstance(value, str) else value


def _complete_bands(provided: Mapping[str, int], order: tuple[str, ...]) -> dict[str, int]:
    """Zero-fill a band tally with every member of ``order``, keeping extras.

    Args:
        provided: The counts the caller supplied. May be partial, may be empty,
            and may carry a key outside ``order`` if a future enum member
            reached a persisted row first.
        order: Every band that must be present in the result, in display order.

    Returns:
        A **new** dictionary holding every band from ``order`` and every extra
        key the caller passed. A new object rather than an in-place fill, so a
        tally the caller still holds is not rewritten underneath them.
    """
    filled = {key: int(provided.get(key, 0)) for key in order}
    for key, value in provided.items():
        if key not in filled:
            filled[key] = int(value)
    return filled


# ---------------------------------------------------------------------------
# Goals
# ---------------------------------------------------------------------------


class LearningGoalRead(BaseModel):
    """One thing the user said they meant to learn, and how it is going.

    ``progress`` is the user's own percentage, or the one they were shown and
    accepted. It is **not** a tally of activities underneath it, and no field
    here implies otherwise: a study session and a goal are different units, and
    dividing one by the other is the first step towards NEXUS claiming to know
    whether somebody learned something.

    :attr:`estimated_effort_minutes` is likewise the user's estimate. Null means
    not estimated, which is not the same as zero — a goal estimated at zero would
    render as a deadline that is already missed.
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID = Field(description="Identifier of the goal.")
    title: str = Field(
        description="The user's own name for it. Never rewritten by NEXUS and never "
        "inferred from the skill it points at."
    )
    description: str | None = Field(
        description="A longer note in the user's words. Null means none was written, "
        "which is not an empty string and not an unfinished field."
    )
    target_skill_id: uuid.UUID | None = Field(
        description="The skill this goal is about, when one already exists. Null "
        "while the topic has no skill row yet — a goal can name a subject before "
        "the account is tracking it, and the foreign key is `ON DELETE SET NULL`, "
        "so the intention outlives the skill."
    )
    target_topic: str | None = Field(
        description="The same idea in free text, for the goal whose subject is not a "
        "tracked skill. Both this and `target_skill_id` may be set; neither has to be."
    )
    target_date: date | None = Field(
        description="The date the user set for themselves. Null means no deadline "
        "was stated — NEXUS never supplies one, and a goal with no date is not a "
        "goal that is overdue."
    )
    priority: str = Field(
        description="How much this competes for attention: one of the "
        f"{tuple(item.value for item in ProjectPriority)} `ProjectPriority` values. "
        "Deliberately the same four grades projects and tasks use, so one control "
        "sorts a goal next to the work it sits beside."
    )
    status: str = Field(
        description="Where the goal sits in its own life: one of the "
        f"{_GOAL_STATUS_ORDER} `LearningGoalStatus` values. `archived` is separate "
        "from `completed` — a finished goal the user wants kept and a goal they "
        "have dismissed are different facts, and an archived goal must not count "
        "as incomplete work anywhere."
    )
    progress: int = Field(
        ge=0,
        le=100,
        description="0-100 as asserted by the user, or by NEXUS restating a figure "
        "they accepted. Never derived from the activities below it, and never a "
        "judgement about progress.",
    )
    estimated_effort_minutes: int | None = Field(
        description="The user's own estimate of the work involved, in minutes. "
        "Deliberately not NEXUS's: deriving it from recorded activity durations "
        "would be the first place this phase could imply that NEXUS knows what a "
        "task takes.",
    )

    project_id: uuid.UUID | None = Field(
        description="The project this goal is working towards. `ON DELETE SET NULL`, "
        "so deleting a project never deletes the intention."
    )
    note_id: uuid.UUID | None = Field(
        description="The knowledge note this goal belongs to, when it was filed "
        "against one. Null when the goal was never linked to a note."
    )
    completed_at: datetime | None = Field(
        description="When the goal reached `completed`. Null while it has not — and "
        "the database holds the two together, so a stamp without a terminal status "
        "cannot exist."
    )
    created_at: datetime = Field(description="When the goal was recorded.")
    updated_at: datetime = Field(
        description="When the row was last revised. A goal *is* revised — a level of "
        "progress is re-asserted — unlike the activities beneath it."
    )


class LearningGoalListRead(BaseModel):
    """One page of goals, with the state tally beside the rows.

    ``by_status`` always carries all five states, zeroed where nothing was found.
    A client reading ``by_status.completed`` therefore never needs a fallback
    default that would quietly turn a missing key into the same number as an
    empty band — which is the failure that lets a list header quietly stop
    reporting completed goals at all.

    ``summary`` is composed here rather than left to each page, so the header has
    one owner and two headers cannot quote different totals for the same query.
    """

    items: list[LearningGoalRead] = Field(
        description="The goals on this page. Empty when the filters match nothing, "
        "which is a measurement and not an error."
    )
    total: int = Field(
        ge=0, description="How many goals match the filters, not the length of this page."
    )
    limit: int = Field(ge=0, description="Maximum rows the page may hold.")
    offset: int = Field(ge=0, description="How many matching rows were skipped.")
    by_status: dict[str, int] = Field(
        description="Counts across every matching goal, not just this page. Always "
        "carries all five statuses, zeroed where nothing was found, so the "
        "response shape does not change as the last completed goal is archived."
    )
    summary: str = Field(
        description="One factual sentence describing the counts, for the list header. "
        "Never claims anything about the user's ability; it counts goals."
    )

    @model_validator(mode="after")
    def _fill_bands_and_summary(self) -> Self:
        """Complete the tally and compose the header sentence.

        The counts are copied rather than mutated, so a dictionary the caller
        still holds is not rewritten underneath them.
        """
        self.by_status = _complete_bands(self.by_status, _GOAL_STATUS_ORDER)
        if not self.summary:
            self.summary = band_count_sentence(
                self.by_status, self.total, _GOAL_LIST_SUBJECT, _GOAL_STATUS_ORDER
            )
        return self


class LearningGoalWriteBase(BaseModel):
    """The fields a create and a PATCH share, every one of them optional.

    The split exists so ``POST`` can require a ``title`` while ``PATCH`` does
    not, without the two drifting into two copies of the same field list. Every
    member defaults to null, and the service applies the payload with
    ``exclude_unset`` — so **an omitted key leaves the column alone and an
    explicit ``null`` clears it**. That is the only reading under which a PATCH
    can both leave a description alone and blank it, and it is why this model
    does not pretend the two are the same thing.
    """

    model_config = ConfigDict(populate_by_name=True)

    description: str | None = Field(
        default=None,
        max_length=2000,
        description="A longer note about what this goal is. Omit to leave it alone; "
        "send an explicit null to clear it.",
    )
    target_skill_id: uuid.UUID | None = Field(
        default=None,
        description="A skill in this account to point the goal at. Must belong to the "
        "caller; another account's skill is a 404, never a 403. Null clears the link "
        "without touching the recorded activities.",
    )
    target_topic: str | None = Field(
        default=None,
        max_length=200,
        description="The subject in free text, for a goal whose subject is not a "
        "tracked skill. NEXUS never fills it in from the linked skill.",
    )
    target_date: date | None = Field(
        default=None,
        description="A self-imposed deadline. NEXUS never supplies one and never "
        "warns about a goal that has none.",
    )
    priority: str | None = Field(
        default=None,
        description=f"One of the {tuple(item.value for item in ProjectPriority)} "
        "`ProjectPriority` values. Defaults server-side to `medium`; validated "
        "against the enum before the write, never trusted from the column.",
    )
    status: str | None = Field(
        default=None,
        description=f"One of the {_GOAL_STATUS_ORDER} `LearningGoalStatus` values. "
        "Completion goes through the dedicated complete endpoint, which stamps "
        "`completed_at` and emits the event; setting it here does neither.",
    )
    progress: int | None = Field(
        default=None,
        ge=0,
        le=100,
        description="0-100. The user's assertion, never computed from activities.",
    )
    estimated_effort_minutes: int | None = Field(
        default=None,
        ge=0,
        le=MAX_LEARNING_MINUTES,
        description="The user's own estimate of the work, in minutes. Null is not "
        "estimated; it is not zero. Capped at "
        f"{MAX_LEARNING_MINUTES:,} — a year of every minute of the day.",
    )
    project_id: uuid.UUID | None = Field(
        default=None,
        description="A project in this account the goal works towards. Null clears "
        "the link; the goal itself is `ON DELETE SET NULL` from the project, so "
        "deleting the project never deletes the intention.",
    )
    note_id: uuid.UUID | None = Field(
        default=None,
        description="A note in this account to file the goal under.",
    )


class LearningGoalWrite(LearningGoalWriteBase):
    """Create one learning goal.

    ``title`` is the only required field. A goal with nothing else on it is a
    legitimate first record — "I want to learn Rust" — and forcing a target date
    or an estimate up front would put a form in front of an intention.

    ``status`` defaults server-side to ``not_started``, the lowest member of the
    lifecycle rather than ``in_progress``: a goal created during planning has
    demonstrably not started, and defaulting it forward would claim an activity
    nobody recorded.
    """

    title: str = Field(
        min_length=1,
        max_length=200,
        description="The user's name for it. Required, and never rewritten by NEXUS. "
        "Surrounding whitespace is trimmed, so a title typed with a stray space is "
        "the goal the user meant rather than a second row beside it.",
    )

    @field_validator("title", mode="before")
    @classmethod
    def _trim_title(cls, value: Any) -> Any:
        """Trim a title before its length is checked.

        ``min_length=1`` is the whole defence against a whitespace-only title, and
        it can only see the empty string — so the trimming has to happen first.
        """
        return _trimmed(value)


class LearningGoalUpdate(LearningGoalWriteBase):
    """Patch a goal.

    ``extra="forbid"`` is what turns "that field is not editable here" into an
    answer the client can act on. Under Pydantic's default a client sending a
    field nobody owns gets a cheerful 200 with it silently dropped, and a caller
    reading that as "the change was applied" has believed something false about
    their own goal. Rejecting names the offending field in a 422.

    :attr:`LearningGoalWriteBase.title` is inherited as optional, so omitting it
    leaves it alone; ``progress`` and ``status`` are the two a PATCH owns, and
    ``completed_at`` is owned by the complete endpoint rather than by any field
    a client can send.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    title: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description="A new name. Omit to leave it alone. Surrounding whitespace is "
        "trimmed; a title cannot be nulled, because a goal without a name is not a "
        "goal the user can recognise on the page.",
    )

    @field_validator("title", mode="before")
    @classmethod
    def _trim_title(cls, value: Any) -> Any:
        """Trim a renamed goal's title before its length is checked.

        ``None`` passes through untouched so the field's ``exclude_unset`` rule
        still decides between "not sent" and "sent as null"; the service refuses
        the null outright, since ``learning_goals.title`` is ``NOT NULL``.
        """
        return _trimmed(value)


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------


class SkillRead(BaseModel):
    """One thing the user is learning, and where they say they are with it.

    :attr:`current_level` and :attr:`level_source` are required together and are
    the reason this model validates its own source: a response that carried the
    number without the provenance would let a card render ``2/5`` with nothing
    saying who claimed it, and that is the exact failure the whole
    :class:`~app.models.enums.SkillLevelSource` column exists to prevent.

    :attr:`confidence` is 0-100 and means *how much recorded evidence backs an
    estimate*, so ``0`` is the honest value for a level the user typed — there
    was nothing to estimate from, which is not the same as a low-confidence
    estimate existing.

    :attr:`last_activity_at` is null when nothing has ever been recorded against
    this skill. That null is a different fact from "recorded, long ago", and the
    dormancy rule the recommendation engine reads treats the two differently.
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID = Field(description="Identifier of the skill.")
    name: str = Field(
        description="The user's own name for it — 'Python', 'technical writing'. "
        "Unique within this account and not globally: two accounts may each track "
        "a thing by the same name without contending for anything."
    )
    category: str | None = Field(
        description="A grouping word such as `language`, `framework`, `domain` or "
        "`practice`. Those four are suggestions the UI offers, **not** a closed "
        "set: a user who calls something 'embedded' is not wrong, so an "
        "unrecognised value is inert rather than invalid."
    )
    description: str | None = Field(
        description="A note about what this skill means to them. Null means none was written."
    )
    current_level: int = Field(
        ge=1,
        le=5,
        description="1-5, as claimed by the user or derived by NEXUS. Five is enough "
        "to be useful and few enough that 3 and 4 mean different things. Always read "
        "together with `level_source`; on its own this number says nothing about who "
        "is making the claim.",
    )
    target_level: int = Field(
        ge=1,
        le=5,
        description="1-5: the level the user is aiming at. Also theirs — NEXUS never "
        "raises a target, because raising it would be an opinion about what they "
        "should want.",
    )
    level_source: str = Field(
        description="Who is allowed to claim the number above: one of the "
        f"{_LEVEL_SOURCE_ORDER} `SkillLevelSource` values. `user_defined` means the "
        "person set it and NEXUS merely records it; `system_estimate` means NEXUS "
        "inferred it from recorded activity and can show its working. **Never omit "
        "or guess this.**"
    )
    confidence: int = Field(
        ge=0,
        le=100,
        description="0-100: how much recorded evidence backs the level. Zero is the "
        "normal value for a user-defined level — there was nothing to estimate from, "
        "which is not the same as a weak estimate existing.",
    )
    evidence_count: int = Field(
        ge=0,
        description="How many learning activities point at this skill, whole history. "
        "A cached counter over a simple count; the gap that depends on it is "
        "computed on read so no second answer can go stale. Zero is a real count.",
    )
    last_activity_at: datetime | None = Field(
        description="When anything was last recorded against this skill, or null when "
        "nothing ever has been. Null is not 'long ago'."
    )
    created_at: datetime = Field(description="When the skill was added.")
    updated_at: datetime = Field(
        description="When the row was last revised — a level re-asserted, a category "
        "changed, or an activity bumping the counters."
    )

    @field_validator("level_source", mode="after")
    @classmethod
    def _level_source_must_be_a_known_claim(cls, value: str) -> str:
        """Reject a level source nothing recognises.

        The column is a string, so an unrecognised value is storable. The only
        safe rendering of one is the cautious one, and a cautious renderer is not
        a guarantee — so the write is refused at the edge of the response instead.
        A level can therefore never reach a screen without a stated provenance.

        Raises:
            ValueError: If the value is not one of the two `SkillLevelSource`
                members.
        """
        if value not in _LEVEL_SOURCE_ORDER:
            raise ValueError(
                f"level_source must be one of {_LEVEL_SOURCE_ORDER}, got {value!r}. A "
                "skill level cannot reach a screen without saying who set it."
            )
        return value


class SkillListRead(BaseModel):
    """One page of skills, with the tallies the skills header shows.

    ``by_level_source`` is complete over both members. That is the tally worth
    having beside the rows: it is the one that answers "how much of this page is
    NEXUS's opinion" — which is a question the design is obliged to make
    answerable rather than to make go away, and which a count of rows alone
    cannot.

    ``by_category`` cannot be completed the same way, because the category
    vocabulary is deliberately open. A key only appears where the user has used
    one, and an unrecognised category is inert rather than an error.

    The evidence split is the second honest tally: how many tracked skills have
    anything recorded against them, and how many are a name and nothing else yet.
    """

    items: list[SkillRead] = Field(
        description="The skills on this page, by name. Empty when the filters match nothing."
    )
    total: int = Field(
        ge=0, description="How many skills match the filters, not the length of this page."
    )
    limit: int = Field(ge=0, description="Maximum rows the page may hold.")
    offset: int = Field(ge=0, description="How many matching rows were skipped.")
    by_level_source: dict[str, int] = Field(
        description="Counts across every matching skill by who claimed the level. "
        "Always carries both sources, zeroed where nothing was found."
    )
    by_category: dict[str, int] = Field(
        description="Counts by the user's own grouping word, across every matching "
        "skill. Only keys the account has actually used appear — the vocabulary is "
        "open by design — and the skills the user never grouped are counted under "
        f"`{UNCATEGORISED_CATEGORY}`, so the bands always add up to `total`. The "
        f"`{UNCATEGORISED_CATEGORY}` band itself is omitted when it is zero."
    )
    skills_with_evidence: int = Field(
        ge=0,
        description="Matching skills with at least one recorded activity, across "
        "every matching row. The complement of `skills_without_evidence`; together "
        "they sum to `total`.",
    )
    skills_without_evidence: int = Field(
        ge=0,
        description="Matching skills that are a name and a level and nothing more. A "
        "real count, not a gap in the data.",
    )

    @model_validator(mode="after")
    def _fill_level_sources(self) -> Self:
        """Complete the source tally, copying rather than mutating the caller's dict.

        ``by_category`` is left exactly as supplied: its vocabulary is open, so
        zero-filling it would invent categories nobody used.
        """
        self.by_level_source = _complete_bands(self.by_level_source, _LEVEL_SOURCE_ORDER)
        return self


class SkillWrite(BaseModel):
    """Track one skill.

    ``name`` is required and ``current_level`` is **not**: a new skill is a name
    the user typed and nothing else, so its level defaults to the floor with the
    aim of reaching three — and the source is recorded as ``user_defined``,
    because defaulting a fresh row to ``system_estimate`` would be claiming an
    inference that has not happened.

    A caller who wants a level NEXUS derived does not set one here. It is
    written by the read path once ``learning_min_evidence_for_estimate``
    activities exist, and refused before that.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(
        min_length=1,
        max_length=120,
        description="The user's own name for the skill. A single technology or "
        "discipline; a paragraph about it belongs in `description`. Trimmed, so "
        "`\"  FastAPI  \"` is the skill `FastAPI` rather than a second row beside "
        "it. Unique within this account, and a duplicate is a 409 rather than a "
        "silently merged row.",
    )
    category: str | None = Field(
        default=None,
        max_length=64,
        description="A grouping word such as `language`, `framework`, `domain` or "
        "`practice`. A suggestion, not a closed set.",
    )
    description: str | None = Field(
        default=None,
        max_length=2000,
        description="A note about what this skill means to them.",
    )
    current_level: int | None = Field(
        default=None,
        ge=1,
        le=5,
        description="The user's own 1-5 assessment. Omit for the floor. Whatever is "
        "sent is recorded as `user_defined`, because a level a client supplied is a "
        "claim the client is making.",
    )
    target_level: int | None = Field(
        default=None,
        ge=1,
        le=5,
        description="The 1-5 level the user is aiming at. Defaults to 3; NEXUS never raises it.",
    )

    @field_validator("name", mode="before")
    @classmethod
    def _trim_name(cls, value: Any) -> Any:
        """Trim the name before its length is checked.

        A name that is only whitespace becomes the empty string it is, and
        ``min_length=1`` refuses it — where before it was accepted and stored as a
        row whose name renders as nothing anywhere on the page.
        """
        return _trimmed(value)


class SkillUpdate(BaseModel):
    """Patch a skill.

    ``level_source`` and ``confidence`` are **deliberately absent**: they are
    NEXUS's to write, and a client that could set them would be able to file its
    own inference as a self-assessment. ``extra="forbid"`` turns that into a 422
    naming the field rather than a cheerful 200 that dropped it.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=120,
        description="A new name. Omit to leave it alone. Trimmed; a name cannot be "
        "nulled, because `skills.name` is NOT NULL and the identity of the row "
        "rests on it.",
    )

    @field_validator("name", mode="before")
    @classmethod
    def _trim_name(cls, value: Any) -> Any:
        """Trim a renamed skill's name before its length is checked.

        ``None`` passes through so ``exclude_unset`` still decides between "not
        sent" and "sent as null"; the service refuses the null, because the
        column is ``NOT NULL`` and the duplicate lookup keys on it.
        """
        return _trimmed(value)
    category: str | None = Field(
        default=None,
        max_length=64,
        description="A new grouping word. An explicit null clears it.",
    )
    description: str | None = Field(
        default=None,
        max_length=2000,
        description="A new note. An explicit null clears it.",
    )
    current_level: int | None = Field(
        default=None,
        ge=1,
        le=5,
        description="A re-asserted 1-5 level. Sending it re-records the source as "
        "`user_defined`, because the person is the one making the claim now.",
    )
    target_level: int | None = Field(
        default=None,
        ge=1,
        le=5,
        description="A new 1-5 target.",
    )


# ---------------------------------------------------------------------------
# Gaps — computed on read, never stored
# ---------------------------------------------------------------------------


class SkillGapRead(BaseModel):
    """How far one skill is from its target, and what that is based on.

    **A gap of zero and no gap at all are different answers, and this model is
    shaped to keep them apart.** A skill whose target is already met reports
    ``gap=0`` with ``available=True`` — that is a measurement, and the most
    reassuring thing the page can say. A skill with nothing recorded reports
    ``available=False`` with a reason, because "no evidence" is not "no gap"; it
    is the absence of the question's answer. Collapsing the second into a null
    ``gap`` would render an unmeasured skill as a skill at zero distance from its
    target, which is a claim nobody can support.

    :attr:`explanation` is validated to carry a digit. A sentence like "you have a
    gap in Machine Learning" is the judgement this phase exists to refuse, and it
    renders well enough that a screenshot review would never catch it; a validator
    is the only thing that still catches it in a service written next year. The
    digit forces the sentence to name the levels and the evidence count, which is
    what makes it checkable against the rows beneath it.
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    skill_id: uuid.UUID | None = Field(
        description="The skill this gap is about. Null for a gap on a subject with "
        "no skill row yet — a goal can name a topic before the account tracks it, "
        "and the gap is still computable against a stated target."
    )
    skill_name: str = Field(
        description="The skill's name, or the free-text topic when there is no row. "
        "Present so the row reads as a subject rather than as a number."
    )
    target_level: int = Field(
        ge=1, le=5, description="The 1-5 level being aimed at. The user's, always."
    )
    current_level: int = Field(
        ge=1,
        le=5,
        description="The 1-5 level being measured from. **Read with "
        "`level_source`**: the same 2/5 is a self-assessment in one row and an "
        "inference in another, and the sentence above is built to say which.",
    )
    level_source: str = Field(
        description="Who claimed `current_level`: one of the "
        f"{_LEVEL_SOURCE_ORDER} `SkillLevelSource` values. This is what decides "
        "whether the row may be described as *self-assessed* or as an *estimate*, "
        "and the two sentences are not interchangeable."
    )
    gap: int = Field(
        ge=0,
        description="`max(0, target - current)`. Zero is a real measurement — the "
        "target is met — and is carried with `available: true`. Never null: an "
        "unmeasurable gap is reported through `available`, not by erasing the "
        "number, so the two cases cannot be confused.",
    )
    evidence_count: int = Field(
        ge=0,
        description="Recorded activities against this skill, whole history. The base "
        "an estimate would rest on.",
    )
    evidence_last_30d: int = Field(
        ge=0,
        description="Recorded activities against this skill inside the last 30 days. "
        "Zero here beside a non-zero `evidence_count` means the skill is dormant, "
        "which is a finding; it is not an absence.",
    )
    days_since_last_activity: int | None = Field(
        description="Days since anything was recorded against this skill, or null "
        "when nothing ever has been. Null is not zero days — a skill touched "
        "today is 0, and that is a different row."
    )
    available: bool = Field(
        description="Whether this gap is measured at all. False with a reason "
        "attached, never a number wearing a measurement's clothes."
    )
    reason_if_unavailable: str | None = Field(
        description="Why there is no measurement, usually the shared cold-start "
        "sentence. Set whenever `available` is false; null whenever it is true. The "
        "pairing is documented rather than enforced here, because a response model "
        "that raised on a service's missing reason would turn one bad row into a "
        "500 for the whole page."
    )
    explanation: str = Field(
        min_length=1,
        description="The sentence shown to the user, naming both levels and the "
        "evidence count — 'Target 4/5, current self-assessed 2/5. NEXUS recorded 6 "
        "related learning activities in the last 30 days.' **Must contain a digit**; "
        "a digitless sentence is a judgement, and this model refuses one.",
    )

    @field_validator("explanation", mode="after")
    @classmethod
    def _explanation_must_carry_its_figures(cls, value: str) -> str:
        """Reject an explanation with no figure in it.

        Stripped before the check rather than after: a whitespace-only sentence is
        the same defect as an empty one and would otherwise survive ``min_length``.

        Raises:
            ValueError: If no digit appears anywhere in the explanation.
        """
        stripped = value.strip()
        if not any(character.isdigit() for character in stripped):
            raise ValueError(
                "A skill gap explanation must name its figures — the two levels and "
                "the evidence count. A sentence with no number in it is a judgement "
                "about the user, which is the claim this phase refuses to make."
            )
        return stripped


class SkillGapListRead(BaseModel):
    """One page of skill gaps, and how many of them were measured.

    ``available_count`` and ``unavailable_count`` are the honest split. A list
    that only reported the measurable gaps would quietly delete the untracked
    skills from the page, and the user would see a shorter list every week with no
    indication that anything had been dropped. Carrying both counts means the
    header can say "5 gaps, 1 not measured yet" — which is true, checkable, and
    makes no claim about anybody.
    """

    items: list[SkillGapRead] = Field(
        description="The gaps on this page, largest first. Empty when the filters match nothing."
    )
    total: int = Field(
        ge=0, description="How many gaps match the filters, not the length of this page."
    )
    limit: int = Field(ge=0, description="Maximum rows the page may hold.")
    offset: int = Field(ge=0, description="How many matching rows were skipped.")
    available_count: int = Field(
        ge=0,
        description="Matching gaps that carry a measurement, across every matching "
        "row. Includes measured zeros: a met target *is* a measurement.",
    )
    unavailable_count: int = Field(
        ge=0,
        description="Matching gaps NEXUS declined to compute, each of which carries "
        "its own reason. `available_count + unavailable_count == total`.",
    )
    by_level_source: dict[str, int] = Field(
        description="Counts across every matching gap by who claimed the level it is "
        "measured from. Always carries both sources, so a page of estimates can be "
        "recognised as one before a single sentence is read."
    )

    @model_validator(mode="after")
    def _fill_level_sources(self) -> Self:
        """Complete the source tally, copying rather than mutating the caller's dict."""
        self.by_level_source = _complete_bands(self.by_level_source, _LEVEL_SOURCE_ORDER)
        return self


# ---------------------------------------------------------------------------
# Activities — the evidence, append-only
# ---------------------------------------------------------------------------


class LearningActivityRead(BaseModel):
    """One recorded thing the user did that counts as evidence of learning.

    :attr:`duration_minutes` is null for an **event** ("I finished the chapter")
    as against a **span** ("I spent forty minutes on it"). Both are honest rows
    and the column tells them apart; zero would claim a measured zero-length
    session, which is a different and false fact.

    :attr:`source_type` and :attr:`source_id` are a labelled polymorphic pair —
    the same shape ``risks.entity_type``/``entity_id`` already uses. Both null for
    a hand-entered activity, which is a legitimate row and not an incomplete one.
    The label is what stops "6 commits touched Python files" being read back as
    "6 Python tasks completed", so it must never be reconstructed client-side.
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID = Field(description="Identifier of the recorded activity.")
    skill_id: uuid.UUID | None = Field(
        description="The skill this is evidence for, or null when the user named "
        "none. A study session legitimately precedes having a skill row, and it also "
        "outlives a deleted one: the foreign key is `ON DELETE SET NULL`, so the row "
        "survives as an append-only fact with an unattributed subject — the same "
        "state it is in when the user never named a skill at all. It still counts "
        "towards every account-wide figure on this surface; only the per-skill "
        "evidence count and gap lose it, which is the cost of not destroying a "
        "record the user typed."
    )
    goal_id: uuid.UUID | None = Field(
        description="The goal this was recorded towards, or null for a standalone "
        "one. `ON DELETE SET NULL` — the trail outlives the goal, so abandoning a "
        "goal never erases the record that the user once worked on it."
    )
    activity_type: str = Field(
        description="What kind of event this is: one of the "
        f"{_ACTIVITY_TYPE_ORDER} `LearningActivityType` values. None of them implies "
        "understanding; `resource_viewed` records that a page was opened and is the "
        "weakest of the seven, which is why a weighted count is never the same as "
        "a raw one."
    )
    title: str = Field(
        description="The user's one-line name for it. Never generated from the linked "
        "record — a derived title would be a sentence about work nobody described."
    )
    description: str | None = Field(
        description="A longer note in the user's words. Null means none was written."
    )
    occurred_at: datetime = Field(
        description="When it happened. Never null: an activity with no instant is not "
        "evidence of anything, and every read on this surface is windowed. "
        "Server-defaults to now only when the caller supplied none."
    )
    duration_minutes: int | None = Field(
        description="How long it took, or null when it is an event rather than a "
        "span. Null is not zero — see the class docstring."
    )
    source_type: str | None = Field(
        description="Which subsystem this came from — `manual`, `task`, `note`, "
        "`project` or `repository`. The vocabulary is deliberately **open**: a new "
        "subsystem must be able to record an activity without a migration, and an "
        "unrecognised value is inert rather than invalid."
    )
    source_id: uuid.UUID | None = Field(
        description="The row it was derived from. Meaningful only alongside "
        "`source_type`, which is what says which table it points into — the two are "
        "kept together rather than keyed on alone because a task id and a project id "
        "are drawn from the same uuid space."
    )
    created_at: datetime = Field(
        description="When NEXUS recorded it, which is not `occurred_at` on an activity "
        "logged days after the fact. This table has no `updated_at`: a recorded "
        "activity is a fact about a moment, and a revision stamp would assert the "
        "moment is still being rewritten."
    )


class LearningActivityWrite(BaseModel):
    """Record one learning activity.

    ``activity_type`` is required and is validated against the enum before the
    write: it is what separates a weighted study session from an unweighted page
    view, so an unrecognised value would land in whichever bucket the consumer
    forgot to exclude and the evidence count behind a skill level would then
    overstate itself.

    Recording an activity bumps the named skill's ``evidence_count`` and
    ``last_activity_at`` and emits ``LEARNING_SESSION_RECORDED``. Nothing here
    updates a skill *level* — evidence is collected, and the level is asserted
    separately and labelled.
    """

    model_config = ConfigDict(populate_by_name=True)

    activity_type: str = Field(
        description="What kind of event this is: one of the "
        f"{_ACTIVITY_TYPE_ORDER} `LearningActivityType` values, validated against "
        "the enum rather than trusted as a bare string."
    )
    title: str = Field(
        min_length=1,
        max_length=200,
        description="The user's one-line name for it. Required: NEXUS will not invent "
        "a description of learning nobody described.",
    )
    description: str | None = Field(
        default=None,
        max_length=2000,
        description="A longer note about what was done.",
    )
    skill_id: uuid.UUID | None = Field(
        default=None,
        description="A skill in this account to record this against. Another "
        "account's skill is a 404, never a 403.",
    )
    goal_id: uuid.UUID | None = Field(
        default=None,
        description="A goal in this account this counts towards.",
    )
    occurred_at: datetime | None = Field(
        default=None,
        description="When it happened. Omit and the server stamps the current "
        "instant — the only case in which NEXUS supplies the time, and only when "
        "the caller gave none.",
    )
    duration_minutes: int | None = Field(
        default=None,
        ge=0,
        le=MAX_LEARNING_MINUTES,
        description="How long it took. Omit for an event that has no length; a "
        "spanned session and an instantaneous event are different facts. Capped at "
        f"{MAX_LEARNING_MINUTES:,} minutes — a year of every minute of the day — "
        "because a duration is summed into every window figure on this surface.",
    )
    source_type: str | None = Field(
        default=None,
        max_length=32,
        description="Which subsystem this came from. Leave null for something typed "
        "in by hand, which is the common case.",
    )
    source_id: uuid.UUID | None = Field(
        default=None,
        description="The row it was derived from. Meaningful only with `source_type`.",
    )


class LearningActivityListRead(BaseModel):
    """One page of activities, with the type tally beside the rows.

    ``by_type`` carries all seven types in weighting order, zeroed where nothing
    was found. Completing the tally is what lets a header say "6 activities: 4
    study sessions, 2 page views" without a client having to tally a page slice
    and quote it as the whole history.
    """

    items: list[LearningActivityRead] = Field(
        description="The activities on this page, newest first. Empty when the "
        "window matches nothing, which is a measurement and not an error."
    )
    total: int = Field(
        ge=0, description="How many activities match the filters, not the length of this page."
    )
    limit: int = Field(ge=0, description="Maximum rows the page may hold.")
    offset: int = Field(ge=0, description="How many matching rows were skipped.")
    by_type: dict[str, int] = Field(
        description="Counts across every matching activity, not just this page. "
        "Always carries all seven types, zeroed where nothing was found, in "
        "weighting order rather than alphabetically."
    )
    summary: str = Field(
        description="One factual sentence describing the counts, for the list header. "
        "Counts events; never characterises what they show."
    )

    @model_validator(mode="after")
    def _fill_bands_and_summary(self) -> Self:
        """Complete the tally and compose the header sentence.

        The counts are copied rather than mutated, so a dictionary the caller
        still holds is not rewritten underneath them.
        """
        self.by_type = _complete_bands(self.by_type, _ACTIVITY_TYPE_ORDER)
        if not self.summary:
            self.summary = band_count_sentence(
                self.by_type, self.total, _ACTIVITY_LIST_SUBJECT, _ACTIVITY_TYPE_ORDER
            )
        return self


class LearningActivityBucketRead(BaseModel):
    """One bucket of the activity series: a period and what happened inside it.

    Buckets are **zero-filled**: a quiet Tuesday arrives with ``activities: 0``
    rather than being skipped. A series that omitted empty buckets would silently
    compress the timeline and make a sparse fortnight read as dense as a busy one
    — a misreading of the data rather than a presentational choice.

    ``minutes`` is null, not 0, for a bucket in which nobody logged a duration.
    Zero would claim time was measured and found to be nothing, which is a far
    more confident claim than "nobody said how long it took".
    """

    bucket_start: datetime = Field(
        description="The bucket's first instant, floored to UTC midnight, to the "
        "Monday starting its week, or to the first of its month. Consecutive buckets "
        "are exactly adjacent."
    )
    bucket_end: datetime = Field(
        description="When this bucket ends: the next bucket's start, or the window's "
        "end for the final one, which is therefore possibly narrower than its "
        "siblings."
    )
    activities: int = Field(
        ge=0,
        description="Activities recorded in this bucket, all types. A real zero for a "
        "period nobody recorded anything in.",
    )
    sessions: int = Field(
        ge=0,
        description="Of those, the ones typed `study_session` — the weighted kind. "
        "Carried beside the raw count because the two do not move together and a "
        "chart that showed only the raw one would overstate a fortnight of page "
        "views.",
    )
    minutes: int | None = Field(
        description="Summed `duration_minutes` for the activities that carried one, or "
        "null when none in this bucket did. Null rather than 0: no duration recorded "
        "is not a measured absence of time."
    )


class LearningActivitySeriesRead(BaseModel):
    """The activity series over a window, with the window that produced it.

    ``skill_id`` echoes what the series was narrowed to, or null for the whole
    account, so a chart can say what it is showing without re-reading the query
    that asked for it — and without a caption that outlives its filter.
    """

    granularity: str = Field(
        description="How wide one bucket is: one of the `day`, `week` or `month` "
        "values the series builder uses."
    )
    window_days: int = Field(ge=1, description="The length of the window the series covers.")
    window_start: datetime = Field(description="Inclusive start of that window.")
    window_end: datetime = Field(description="Exclusive end of that window.")
    skill_id: uuid.UUID | None = Field(
        description="The skill the series was narrowed to, or null when it covers "
        "every activity the account has recorded."
    )
    buckets: list[LearningActivityBucketRead] = Field(
        description="Dense and ascending, gaps included. Never empty for a non-empty "
        "range: a range with nothing recorded is all zeroes."
    )
    total_activities: int = Field(
        ge=0,
        description="Activities across every bucket. Carried so a chart's axis and its "
        "caption cannot quote different sums.",
    )
    total_minutes: int | None = Field(
        description="Summed durations across every bucket that carried one, or null "
        "when none did. Null rather than 0 — the same reasoning as a bucket's "
        "`minutes`, applied to the series."
    )

    @model_validator(mode="after")
    def _total_minutes_is_null_without_any_duration(self) -> Self:
        """Make the total null when no bucket measured any time.

        A sum of empty buckets is arithmetically ``0`` and factually wrong: it
        says "no time was spent", where the truth is "no time was recorded".
        Deriving the null here rather than trusting the caller is what keeps a
        service that forgot the rule from publishing a confident zero.
        """
        if self.total_minutes is None and any(
            bucket.minutes is not None for bucket in self.buckets
        ):
            self.total_minutes = sum(
                bucket.minutes or 0 for bucket in self.buckets if bucket.minutes is not None
            )
        return self


# ---------------------------------------------------------------------------
# The dashboard's three readings
# ---------------------------------------------------------------------------


class LearningSummaryRead(BaseModel):
    """The account-wide headline figures for the learning page.

    Counts only. There is no score here and no verdict: ``activities_in_window`` is
    "how many activities were recorded", and the window it covers is carried
    beside it so the sentence printed underneath can be true.

    ``minutes_in_window`` is null when nothing in the window carried a duration,
    for the reason every other duration figure on this surface is nullable.

    ``has_data`` is the cold-start flag. False means every count below is
    legitimately zero **and** the page must explain that nothing has been recorded
    yet, rather than rendering a dashboard of zeroes as though that were a finding
    about the account.
    """

    goal_count: int = Field(
        ge=0,
        description="Goals this account holds, archived ones included — an archived "
        "goal is a record the user kept, and deleting it to shrink a count would "
        "delete what they kept it for.",
    )
    active_goal_count: int = Field(
        ge=0,
        description="Of those, the ones still live: `not_started`, `in_progress` or "
        "`paused`. This is the figure a header shows, because a completed goal is "
        "history and a count that kept rising as goals were finished would never let "
        "anyone see that they had.",
    )
    completed_goal_count: int = Field(ge=0, description="Goals that reached `completed`.")
    skill_count: int = Field(
        ge=0,
        description="Skills this account tracks. Bounded by `learning_max_skills`, "
        "because the skills list is the input to every gap computation.",
    )
    skills_with_evidence: int = Field(
        ge=0,
        description="Of those, the ones with at least one recorded activity. The "
        "difference from `skill_count` is how much of the page is a name rather than "
        "a history, and it is worth stating rather than hiding.",
    )
    activity_count: int = Field(
        ge=0,
        description="Activities recorded across the whole history. A real zero when "
        "nothing has ever been recorded.",
    )
    activities_in_window: int = Field(
        ge=0, description="Activities recorded inside the window below."
    )
    minutes_in_window: int | None = Field(
        description="Summed durations for activities in the window that carried one, or "
        "null when none did. Null rather than 0 — no duration recorded is not a "
        "measured absence of time."
    )
    window_days: int = Field(
        ge=1,
        description="The length of the window the in-window figures cover. Carried so "
        "no sentence about them can omit the range it describes.",
    )
    window_start: datetime = Field(description="Inclusive start of that window.")
    window_end: datetime = Field(description="Exclusive end of that window.")
    latest_activity_at: datetime | None = Field(
        description="The most recent activity recorded across the account, or null "
        "when none has ever been."
    )
    has_data: bool = Field(
        description="False when there is nothing recorded to summarise, so the counts "
        "read as an absence rather than as a finding."
    )
    summary: str = Field(
        description="One factual sentence describing the counts, composed server-side "
        "so the header has one owner rather than one per page. Counts goals, skills "
        "and recorded events; never says what they show about the user."
    )


class LearningMetricRead(BaseModel):
    """One metric, fully explained.

    The shape of :class:`app.services.learning.metrics.LearningMetric`, carried
    through unchanged. Four fields do real work:

    * ``value`` is ``float | None``, and null means *not measured*. A rate over
      an empty denominator produces null rather than an invented zero, and a total
      over an unmeasured window produces null rather than zero.
    * ``available`` with ``reason_if_unavailable`` is the positive form of the
      same fact: "we looked, and there was nothing to look at". A zero with
      ``available: true`` is a *different* answer — the arithmetic came out at
      zero — and must never be rendered with the reason attached.
    * ``definition`` says how it is computed and ``explanation`` says it again
      with the figures in it. An explanation with no digit in it is a backend bug.
    * ``unit`` is data rather than decoration, so a duration is not formatted as a
      count.
    """

    key: str = Field(
        description="The metric's stable identity: `sessions_last_7d`, "
        "`sessions_last_30d`, `learning_minutes`, `goal_progress`, "
        "`goal_deadline_distance_days`, `completion_rate`, `learning_consistency` "
        "or `skill_activity_frequency`. A closed set of exactly eight, always all "
        "eight, so a client that indexes by key never meets a hole where a card "
        "belongs. The same eight names, in the same order, are the columns of "
        "`learning_features.v1`."
    )
    label: str = Field(description="Short human name for the card.")
    value: float | None = Field(
        description="The measured figure, or null when the metric could not be "
        "computed. Never 0 for that reason: a real zero is a measurement and is "
        "carried with `available: true`."
    )
    unit: str = Field(
        description="What kind of figure this is: one of the `count`, `minutes`, "
        "`percent`, `days` or `ratio` units. Note what is not in that list — "
        "nothing on this surface measures how well anybody learned anything, so "
        "there is no `score` to format and no curve to plot."
    )
    definition: str = Field(
        description="One sentence naming the inputs and the arithmetic, so the method "
        "can be shown above the result."
    )
    window_days: int | None = Field(
        description="The window this instance measured, or null for a whole-history figure."
    )
    source: str = Field(
        description="Which recorded facts the computation read, named so a reader can "
        "find the rows behind the number — for example `learning_activities`."
    )
    explanation: str = Field(
        description="The sentence shown to the user, carrying the figures it was "
        "built from. Never characterises the user: a recorded activity is evidence "
        "that something was recorded, and nothing on this surface converts that into "
        "a claim about a person."
    )
    available: bool = Field(
        description="Whether there is a measurement at all. False with a reason "
        "attached, never a zero wearing a value."
    )
    reason_if_unavailable: str | None = Field(
        description="Why the metric could not be computed, usually the shared "
        f"`{NOT_ENOUGH_DATA}` sentence. Null whenever `available` is true."
    )

    @model_validator(mode="after")
    def _value_tracks_availability(self) -> Self:
        """Keep ``value`` and ``available`` from telling different stories.

        Two normalisations, and deliberately not a rejection. ``available=False``
        with a non-null ``value`` would publish a number beside a sentence saying
        there is none, so the number is dropped. The other way round — a measured
        ``0.0`` with ``available=False`` — is the exact conflation this phase
        exists to prevent, so it is repaired rather than accepted.

        Nothing raises. A response model that turned a service's bookkeeping slip
        into a 500 would take the whole learning page down for one card, and the
        cost of the failure is a missing figure rather than a wrong claim.
        """
        if not self.available:
            if self.value is not None:
                self.value = None
            if not self.reason_if_unavailable:
                self.reason_if_unavailable = NOT_ENOUGH_DATA
        else:
            self.reason_if_unavailable = None
        return self


# ---------------------------------------------------------------------------
# Features — extracted, never modelled
# ---------------------------------------------------------------------------


class LearningFeatureValues(BaseModel):
    """The feature names, and only the feature names.

    An **extractor**, not a model: named numbers under a schema version so a later
    phase knows what each column meant. Nothing here is a prediction, a
    probability or a fitted parameter, and ``learning_consistency`` is a rate of
    days carrying a recorded event — not a statement about a person's discipline.

    The four nullable figures are the contract's own example of the null-not-zero
    rule. ``goal_progress`` is null for an account with no goals, because an
    account with no goals does not have goals that are zero percent complete.
    ``completion_rate`` is null over an empty denominator rather than ``0.0``,
    which would read as "you complete nothing". ``goal_deadline_distance_days`` is
    null when no goal carries a self-imposed date. ``learning_minutes`` is null
    when nothing in the window recorded a duration, because zero minutes would
    claim time was measured and found to be nothing.
    """

    sessions_last_7d: int = Field(ge=0, description="Activities recorded in the last 7 days.")
    sessions_last_30d: int = Field(ge=0, description="Activities recorded in the last 30 days.")
    learning_minutes: int | None = Field(
        description="Minutes summed from activities in the window that carried a "
        "duration, or null when none did. Null rather than 0 — see the class "
        "docstring."
    )
    goal_progress: float | None = Field(
        description="Mean progress across the account's live goals, 0-100, or null "
        "when there are none. The user's own asserted progress, averaged — never a "
        "derived competence score."
    )
    goal_deadline_distance_days: int | None = Field(
        description="Signed mean number of days from now to the deadlines the account's "
        "open, dated goals carry — negative when the average is already past. Null "
        "when no goal carries a date, which is not the same as a deadline today. This "
        "is the same figure `/learning/metrics` reports, so the card and the feature "
        "export cannot disagree about how much time is left."
    )
    completion_rate: float | None = Field(
        description="Completed goals as a fraction of goals that have reached a "
        "terminal state, or null over an empty denominator rather than 0.0."
    )
    learning_consistency: float | None = Field(
        description="Distinct days carrying a recorded activity as a fraction of the "
        "window, or null when nothing was recorded. A rate of events per day, never "
        "a measure of a habit or a person."
    )
    skill_activity_frequency: float | None = Field(
        description="Activities per tracked skill per week across the window, or null "
        "when no skills are tracked. A rate over an empty set of skills, not a "
        "statement about how fast anyone learns."
    )


class LearningFeatureVectorRead(BaseModel):
    """``GET /learning/features``: the account-level feature row.

    ``schema_version`` is what makes the vector usable later: a trainer that sees
    ``learning_features.v1`` knows the column meanings without having to trust
    that the client did not reorder them. There is no model, no inference and no
    registry behind this shape — Phase 10 does that.
    """

    schema_version: str = Field(
        default=LEARNING_FEATURE_SCHEMA_VERSION,
        description="The version of the column meanings below. A v2 must not "
        "typecheck against v1, which is why the frontend carries this as a closed "
        "union rather than a bare string.",
    )
    generated_at: datetime = Field(
        description="When the vector was extracted, from the database clock rather "
        "than the host's, so it belongs on the same timeline as the rows it reads."
    )
    window_days: int = Field(
        ge=1, description="The window the date-bounded features above were computed over."
    )
    features: LearningFeatureValues = Field(
        description="The account-level row: the same features aggregated across every "
        "goal, skill and activity this account owns."
    )
