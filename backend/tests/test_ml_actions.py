"""The intent → argument → confirm layer, and the promises it makes.

``app/api/v1/ml.py`` deferred slot filling to a later phase and said why: turning
"add a task to draft the migration plan for Friday" into a service call "needs
either a second model or hand-written per-utterance parsers". The project brief
allowed exactly one of those. :mod:`app.ml.actions.extraction` is the hand-written
one and :mod:`app.ml.actions.proposals` decides what those arguments are allowed
to become. Neither imports ``torch`` or a checkpoint: every prediction here is
hand-built, because the classifier's output is an input to this layer, not a
subject of it.

What is protected, and what breaks if it moves:

* **A destructive request produces no proposal at all.** This is the security
  test of the file. :data:`ml.datasets.taxonomy.Intent.TASK_MANAGE` covers
  create, complete, block, cancel, reorder **and delete**, so "delete every task"
  arrives as the same class, with the same confidence, as "add a task". A layer
  that turned that into a write would delete a board on the strength of a
  sentence nobody read. The protection is structural — no :class:`ActionKind`
  member is destructive and :attr:`ActionProposal.destructive` is a property that
  returns ``False`` — and it is pinned from three directions: the utterances, the
  kind set, and the fact that the attribute cannot be set.
* **Confirmation is not configurable.** ``requires_confirmation`` is a property
  returning ``True``, so no caller can construct a proposal that skips it. The
  value a caller would pass is the one that would make the layer safe to delete.
* **An ambiguous date yields no date.** "on friday" said on a Friday, a bare
  "next week", ``2026-13-45``: each produces no date plus a note saying why. A
  plausible wrong deadline is worse than an absent one, because a deadline is the
  field a user trusts without re-reading.
* **A title is recovered or refused, never invented.** What the command phrase
  and the entity noun leave behind is either a title with its provenance or a
  refusal with a reason. A confidently wrong title creates a confidently wrong
  task.
* **Everything extracted says where it came from.** Each field carries the span it
  matched and the rule that consumed it, so the confirm dialog can show its own
  reasoning and a test can pin it.
* **There is no second model.** NEXUS runs one classifier; this package runs no
  inference at all. The scan at the end carries a positive control, because a
  sweep that finds nothing for the wrong reason is indistinguishable from a
  clean bill of health.

The other thing this suite protects is the *absence* of writes: there is no
endpoint, no service import and no callable on a proposal. A proposal names the
service, module and entry point as strings and carries a Pydantic payload — the
caller, which owns the session and the user's decision, makes the call.
"""

from __future__ import annotations

import inspect
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from pydantic import BaseModel

from app.core.permissions import Permission
from app.ml.actions import extraction as extraction_module
from app.ml.actions import proposals as proposals_module
from app.ml.actions.extraction import (
    DESTRUCTIVE_VERBS,
    ExtractedVerb,
    Extraction,
    TaskCandidate,
    TaskMatch,
    day_start_utc,
    extract_arguments,
    extract_title,
    extract_verb,
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
from app.ml.schemas import IntentPrediction
from app.models.enums import TaskPriority, TaskStatus
from app.schemas.knowledge import NoteCreate
from app.schemas.learning import LearningGoalWrite
from app.schemas.project import ProjectCreate
from app.schemas.task import TaskCreate, TaskStatusChange
from ml.datasets.taxonomy import Intent

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

#: Wednesday. Chosen because it is not a Friday and not a Monday, so both the
#: "next occurrence" and the "next ISO week" readings of a weekday are
#: distinguishable from each other in the tests below.
LISBON = ZoneInfo("Europe/Lisbon")
NOW = datetime(2026, 3, 11, 9, 0, tzinfo=LISBON)
TODAY = date(2026, 3, 11)

PROJECT_ID = UUID("11111111-1111-4111-8111-111111111111")
CONTRACT_TASK_ID = UUID("22222222-2222-4222-8222-222222222222")
RETRO_TASK_ID = UUID("33333333-3333-4333-8333-333333333333")
THIRD_TASK_ID = UUID("44444444-4444-4444-8444-444444444444")


def prediction(intent: str = str(Intent.TASK_MANAGE)) -> IntentPrediction:
    """A hand-built prediction.

    Confidence is fixed and high so a test that fails does so on the extraction
    rather than on a threshold nobody is exercising here — that decision belongs
    to :class:`app.ml.router.IntentRouter`, tested in ``test_ml_integration_routing``.
    """
    return IntentPrediction(intent=intent, confidence=0.93)


def context(**overrides) -> ProposalContext:
    """A context carrying everything the supported proposals need."""
    base = {
        "tz": LISBON,
        "now": NOW,
        "project_id": PROJECT_ID,
        "project_label": "Nexo rewrite",
        "task_candidates": (
            TaskCandidate(CONTRACT_TASK_ID, "Draft the API contract"),
            TaskCandidate(RETRO_TASK_ID, "Run the retro"),
        ),
    }
    base.update(overrides)
    return ProposalContext(**base)


def extract(text: str, intent: str = str(Intent.TASK_MANAGE)) -> Extraction:
    """Run the extractor with a fixed clock and zone."""
    return extract_arguments(text, prediction(intent), tz=LISBON, now=NOW)


def propose(text: str, intent: str = str(Intent.TASK_MANAGE), ctx=None) -> ActionProposal:
    """Propose and assert that a proposal — not a refusal — came back."""
    outcome = propose_action(text, prediction(intent), context=ctx or context())
    assert isinstance(outcome, ActionProposal), (
        f"expected a proposal for {text!r}, got a refusal: {outcome.reason}"
    )
    return outcome


def refuse(text: str, intent: str = str(Intent.TASK_MANAGE), ctx=None) -> ProposalRefusal:
    """Propose and assert that a refusal — not a proposal — came back."""
    outcome = propose_action(text, prediction(intent), context=ctx or context())
    assert isinstance(outcome, ProposalRefusal), (
        f"expected a refusal for {text!r}, got a proposal: {outcome.summary}"
    )
    return outcome


# --------------------------------------------------------------------------- #
# Command phrases
# --------------------------------------------------------------------------- #

#: One utterance per family the brief names, each written so that the recovered
#: title is only right if that family's prefix was actually consumed.
PHRASE_FAMILIES: tuple[tuple[str, str, str], ...] = (
    ("add", "Add a task to finish DSA", "finish DSA"),
    ("create", "Create a task to finish DSA", "finish DSA"),
    ("make", "Make a task to finish DSA", "finish DSA"),
    ("new", "New task to finish DSA", "finish DSA"),
    ("remind me to", "Remind me to finish DSA", "finish DSA"),
    ("i need to", "I need to finish DSA", "finish DSA"),
    ("can you", "Can you add a task to finish DSA", "finish DSA"),
    ("please", "Please add a task to finish DSA", "finish DSA"),
    ("set up", "Set up a task to finish DSA", "finish DSA"),
    ("polite stack", "Can you please add a task to finish DSA", "finish DSA"),
    ("no phrase", "Finish the DSA task", "DSA"),
)


@pytest.mark.parametrize(
    ("family", "utterance", "expected_title"),
    PHRASE_FAMILIES,
    ids=[family for family, _, _ in PHRASE_FAMILIES],
)
def test_command_phrase_families_leave_the_subject(
    family: str, utterance: str, expected_title: str
) -> None:
    """Every command phrase the brief lists is consumed, and only that.

    "Finish the DSA task" is the control: the *completion* family, where the verb
    is "finish" and the trailing entity noun has to come off instead. If the
    prefix stripper ever became positional-only or greedy, this is where it
    shows.
    """
    assert extract(utterance).title == expected_title, family


def test_prefix_stripping_requires_a_word_boundary() -> None:
    """The prefix "new" must not eat the first two letters of "newsletter"."""
    assert strip_command_prefix("newsletters for the board") == "newsletters for the board"
    assert strip_command_prefix("new task for the launch") == "task for the launch"


def test_completion_verb_is_only_read_in_leading_position() -> None:
    """The word "finish" inside a title is English, not a verb.

    "add a task to finish DSA" is a creation whose *title* happens to start with
    the word "finish". Matching the word anywhere would propose completing a task
    the user never finished — the worst kind of wrong, because completing is a
    write to somebody's own history.
    """
    assert extract_verb("Add a task to finish DSA") == ExtractedVerb.CREATE
    assert extract_verb("Finish the DSA task") == ExtractedVerb.COMPLETE
    assert extract("Add a task to finish DSA").title == "finish DSA"


def test_politeness_is_not_read_as_a_creation_verb() -> None:
    """The phrase "please mark … as done" is one completion, not a creation plus one.

    "please" is framing. Counting it as a creation verb made this ambiguous and
    it was refused; the split into politeness and creation prefixes is the fix,
    and this test is what keeps it from regressing.
    """
    assert extract_verb("Please mark the API contract task as done") == ExtractedVerb.COMPLETE
    assert extract_verb("Can you please finish the retro") == ExtractedVerb.COMPLETE


def test_two_requests_in_one_sentence_are_refused_rather_than_guessed() -> None:
    """A creation and a completion together resolve to no verb at all."""
    assert (
        extract_verb("add a task to finish DSA and mark the retro task as done")
        == ExtractedVerb.UNKNOWN
    )
    proposal_refusal = refuse("add a task to finish DSA and mark the retro task as done")
    assert proposal_refusal.reason_code == ProposalReason.VERB_NOT_RECOVERED
    assert not is_proposal(proposal_refusal)


def test_an_intent_with_no_verb_is_refused_rather_than_guessed() -> None:
    """The utterance "what are my tasks" names a surface; there is nothing to write."""
    refusal = refuse("What tasks are still open on the Nexo rewrite?")
    assert refusal.reason_code == ProposalReason.VERB_NOT_RECOVERED
    assert "verb" in refusal.reason


def test_an_unsupported_intent_is_refused_with_its_own_reason() -> None:
    """A read-only class never becomes a write, and says why."""
    refusal = refuse(
        "What did we decide about the risk scoring thresholds?", str(Intent.KNOWLEDGE_LOOKUP)
    )
    assert refusal.reason_code == ProposalReason.UNSUPPORTED_INTENT
    assert refusal.kind is None


# --------------------------------------------------------------------------- #
# Priority
# --------------------------------------------------------------------------- #

PRIORITY_PHRASES: tuple[tuple[str, str], ...] = (
    ("high priority", "high"),
    ("high-priority", "high"),
    ("urgent", "high"),
    ("asap", "high"),
    ("top priority", "high"),
    ("critical", "critical"),
    ("p0", "critical"),
    ("blocker", "critical"),
    ("medium priority", "medium"),
    ("normal priority", "medium"),
    ("normal", "medium"),
    ("low priority", "low"),
    ("lowest priority", "low"),
    ("whenever", "low"),
    ("no rush", "low"),
    ("not urgent", "low"),
    ("someday", "low"),
)


@pytest.mark.parametrize(
    ("phrase", "expected"),
    PRIORITY_PHRASES,
    ids=[phrase for phrase, _ in PRIORITY_PHRASES],
)
def test_priority_synonyms(phrase: str, expected: str) -> None:
    """Every synonym maps to a grade the schema actually persists.

    "urgent" maps to ``high`` rather than ``critical``: urgency is a statement
    about this week, ``critical`` is a grade on the scale, and collapsing the two
    would put a word into a column that means something else.
    """
    result = extract(f"add a {phrase} task to finish DSA")
    assert result.priority == expected, phrase
    assert result.title == "finish DSA", phrase


def test_priority_is_cut_out_of_the_title() -> None:
    """The title is the subject, not the sentence minus the verb."""
    proposal = propose("Add a high priority task to finish DSA due Friday")
    assert proposal.payload.title == "finish DSA"
    assert proposal.payload.priority is TaskPriority.HIGH
    assert proposal.summary == (
        "Create a high-priority task titled 'finish DSA', due Friday, in project 'Nexo rewrite'."
    )


def test_an_unmentioned_priority_is_absent_from_the_payload_and_the_summary() -> None:
    """The sentence reports the request, not the request plus the schema defaults.

    ``medium`` is a default. Printing it would make the confirm dialog claim the
    user chose something they never said.
    """
    proposal = propose("Add a task to finish DSA")
    assert "priority" not in proposal.payload.model_fields_set
    assert "priority" not in proposal.summary
    assert proposal.summary == "Create a task titled 'finish DSA', in project 'Nexo rewrite'."


def test_critical_is_not_high() -> None:
    """The two grades are distinct on both ``TaskPriority`` and ``ProjectPriority``."""
    assert propose("Add a critical task to rotate the signing keys").payload.priority is (
        TaskPriority.CRITICAL
    )


# --------------------------------------------------------------------------- #
# Dates
# --------------------------------------------------------------------------- #

RELATIVE_DATES: tuple[tuple[str, date], ...] = (
    ("today", date(2026, 3, 11)),
    ("tomorrow", date(2026, 3, 12)),
    ("the day after tomorrow", date(2026, 3, 13)),
    ("on friday", date(2026, 3, 13)),
    ("for friday", date(2026, 3, 13)),
    ("this friday", date(2026, 3, 13)),
    ("on monday", date(2026, 3, 16)),
    ("next monday", date(2026, 3, 16)),
    ("next friday", date(2026, 3, 20)),
    ("on 2026-04-02", date(2026, 4, 2)),
    ("by 2026-04-02", date(2026, 4, 2)),
    ("due sunday", date(2026, 3, 15)),
)


@pytest.mark.parametrize(
    ("phrase", "expected"),
    RELATIVE_DATES,
    ids=[phrase for phrase, _ in RELATIVE_DATES],
)
def test_relative_and_iso_dates(phrase: str, expected: date) -> None:
    """Relative and ISO forms both resolve, against the caller's day."""
    result = extract(f"add a task to finish DSA {phrase}")
    assert result.due_date == expected, phrase
    assert result.title == "finish DSA", phrase


def test_next_monday_is_the_following_week_even_from_a_monday() -> None:
    """The phrase "next monday", said on a Monday, is a week away and not today.

    The alternative reading — today — is what makes a bare "monday" ambiguous,
    and the ``next`` qualifier is exactly the word that removes the ambiguity.
    """
    monday = datetime(2026, 3, 9, 9, 0, tzinfo=LISBON)
    assert resolve_weekday(0, monday.date(), qualifier="next") == date(2026, 3, 16)
    assert resolve_weekday(0, monday.date(), qualifier=None) is None
    assert resolve_weekday(4, monday.date(), qualifier="this") == date(2026, 3, 13)


#: Friday. Chosen only for the same-weekday ambiguity: "on friday" said *on a
#: Friday* has two honest readings, and it is the only utterance in this file that
#: the extractor must refuse rather than resolve.
FRIDAY_NOW = datetime(2026, 3, 13, 9, 0, tzinfo=LISBON)


@pytest.mark.parametrize(
    ("utterance", "clock", "expected_absent"),
    (
        ("add a task to finish DSA on friday", FRIDAY_NOW, "friday"),
        ("add a task to finish DSA on 2026-13-45", NOW, "2026-13-45"),
        ("add a task to finish DSA next week", NOW, "next week"),
        ("add a task to finish DSA sometime soon", NOW, "sometime"),
    ),
    ids=("same-weekday", "impossible-date", "no-day-named", "vague"),
)
def test_an_ambiguous_date_yields_no_date_rather_than_a_guess(
    utterance: str, clock: datetime, expected_absent: str
) -> None:
    """The central safety rule: an unresolvable date is absent, not invented.

    "on friday" said on a Friday has two honest readings, a bare "next week"
    names no day at all, and ``2026-13-45`` is not a date. Each produces no date
    plus a note naming what was dropped. A nearest-match guess would be a
    *worse* outcome than an absent field, because a deadline is the one thing a
    user files without re-reading. The proposal still exists — the subject was
    recovered — and the absence is on it.
    """
    result = extract_arguments(utterance, prediction(), tz=LISBON, now=clock)
    assert result.due_date is None, utterance
    assert result.due_at is None, utterance
    assert any(expected_absent in note for note in result.notes), result.notes

    proposal = propose_action(utterance, prediction(), context=context(now=clock))
    assert is_proposal(proposal), proposal.reason
    assert "due_date" not in proposal.payload.model_fields_set
    assert "due" not in proposal.summary
    assert proposal.notes, "an absent date must be reported, not silently dropped"


def test_a_weekday_mentioned_on_another_day_is_not_ambiguous() -> None:
    """The control: the same words on a Wednesday resolve to the next Friday.

    Without this, "ambiguous" could mean "the rule always refuses a weekday" —
    which would be safe and useless, and would be passing the test above.
    """
    result = extract("add a task to finish DSA on friday")
    assert result.due_date == date(2026, 3, 13)
    assert not result.notes


def test_an_unresolvable_date_is_still_cut_from_the_title() -> None:
    """Otherwise the summary would quote a title the user never wrote."""
    result = extract("add a task to draft the plan for 2026-13-45")
    assert result.title == "draft the plan"
    assert result.due_date is None


def test_the_earliest_date_in_the_utterance_wins() -> None:
    """Two dates is an ambiguous request; the first one is the natural reading."""
    result = extract("add a task to ship the release on 2026-04-02 by friday")
    assert result.due_date == date(2026, 4, 2)


# --------------------------------------------------------------------------- #
# Timezone conversion
# --------------------------------------------------------------------------- #

#: (zone, local day, the UTC instant that day's local midnight falls on). Three
#: zones chosen for the three interesting offsets: behind UTC, a half-hour ahead,
#: and far enough ahead that local midnight is the *previous* UTC day.
ZONE_CASES: tuple[tuple[str, date, datetime], ...] = (
    # 09:00 local on the 11th, "tomorrow" in every zone. The stored date is the
    # caller's day; the instant is that day's local midnight in UTC. Four zones,
    # because a single one proves nothing: UTC has an offset of zero, New York
    # is behind, Kolkata is a half hour ahead, and Tokyo's local midnight is on
    # the *previous* UTC day.
    ("Europe/Lisbon", date(2026, 3, 12), datetime(2026, 3, 12, 0, 0, tzinfo=UTC)),
    ("America/New_York", date(2026, 3, 12), datetime(2026, 3, 12, 4, 0, tzinfo=UTC)),
    ("Asia/Kolkata", date(2026, 3, 12), datetime(2026, 3, 11, 18, 30, tzinfo=UTC)),
    ("Asia/Tokyo", date(2026, 3, 12), datetime(2026, 3, 11, 15, 0, tzinfo=UTC)),
)


@pytest.mark.parametrize(
    ("zone", "local_day_value", "expected_instant"),
    ZONE_CASES,
    ids=[zone for zone, _, _ in ZONE_CASES],
)
def test_dates_resolve_in_the_callers_zone_and_convert_to_aware_utc(
    zone: str, local_day_value: date, expected_instant: datetime
) -> None:
    """The stored date is the caller's day; the instant is that day's local midnight.

    This is the conversion the planner cuts its day window on — see
    :func:`app.ml.actions.extraction.day_start_utc`. The Tokyo case is the one
    that matters: local midnight on the 12th is 15:00 UTC on the **11th**, so a
    naive ``date -> midnight UTC`` conversion would file the task on the wrong
    side of the date line.
    """
    tz = ZoneInfo(zone)
    now = datetime(2026, 3, 11, 9, 0, tzinfo=tz)
    result = extract_arguments("add a task to finish DSA tomorrow", prediction(), tz=tz, now=now)
    assert result.due_date == local_day_value, zone
    assert result.due_at == expected_instant, zone
    assert result.due_at is not None and result.due_at.tzinfo is not None, zone
    assert result.due_at.utcoffset() is not None and result.due_at.utcoffset().total_seconds() == 0


def test_the_same_instant_lands_on_two_utc_days() -> None:
    """The reason the conversion exists, stated as a test.

    09:00 in Tokyo on the 12th is 00:00 UTC on the 12th, so "tomorrow" means two
    different days depending on which clock asked.
    """
    tokyo = datetime(2026, 3, 12, 9, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
    lisbon = tokyo.astimezone(LISBON)
    assert local_day(tokyo, ZoneInfo("Asia/Tokyo")) == date(2026, 3, 12)
    assert local_day(lisbon, LISBON) == date(2026, 3, 12)
    assert day_start_utc(date(2026, 3, 12), ZoneInfo("Asia/Tokyo")).date() == date(2026, 3, 11)


def test_the_day_window_mirrors_the_planners_own_helper() -> None:
    """The conversion is the planner's, reimplemented — so it must agree with it.

    ``app.services.planner_service.day_bounds`` is imported *here* on purpose:
    the whole argument for mirroring rather than importing is that the package
    stays importable without the ORM, and that argument holds only while the two
    implementations actually produce the same instant. Checking them together is
    what stops the mirror from drifting into a second, subtly wrong definition of
    a day.
    """
    from app.services.planner_service import day_bounds

    for zone in ("Europe/Lisbon", "America/New_York", "Asia/Kolkata", "Australia/Sydney"):
        tz = ZoneInfo(zone)
        for day in (date(2026, 3, 8), date(2026, 6, 1), date(2026, 10, 25)):
            assert day_start_utc(day, tz) == day_bounds(day, tz)[0], (zone, day)


# --------------------------------------------------------------------------- #
# Titles
# --------------------------------------------------------------------------- #

ENTITY_FAMILIES: tuple[tuple[str, str, str, str], ...] = (
    (str(Intent.TASK_MANAGE), "Add a task to finish DSA", "finish DSA", "create_task"),
    (str(Intent.TASK_MANAGE), "Add a new todo for buying stamps", "buying stamps", "create_task"),
    (
        str(Intent.TASK_MANAGE),
        "Remind me to call the bank tomorrow",
        "call the bank",
        "create_task",
    ),
    (
        str(Intent.PROJECT_MANAGE),
        "Create a project for the mobile redesign",
        "mobile redesign",
        "create_project",
    ),
    (
        str(Intent.KNOWLEDGE_CAPTURE),
        "Save a note about the auth token rotation policy",
        "auth token rotation policy",
        "create_note",
    ),
    (
        str(Intent.KNOWLEDGE_CAPTURE),
        "Write a note called why the prune keeps a suffix",
        "why the prune keeps a suffix",
        "create_note",
    ),
    (
        str(Intent.LEARNING_TRACK),
        "Add a learning goal for distributed systems",
        "distributed systems",
        "create_learning_goal",
    ),
)


@pytest.mark.parametrize(
    ("intent", "utterance", "expected_title", "expected_kind"),
    ENTITY_FAMILIES,
    ids=[family[1] for family in ENTITY_FAMILIES],
)
def test_every_supported_entity_family(
    intent: str, utterance: str, expected_title: str, expected_kind: str
) -> None:
    """One utterance per entity noun family, across all four proposal kinds.

    The entity noun is keyed by intent, so "a note about X" is stripped for
    ``knowledge_capture`` and left alone for ``task_manage`` — where those same
    words are usually part of the title.
    """
    assert extract(utterance, intent).title == expected_title
    assert str(propose(utterance, intent).kind) == expected_kind


UNEEXTRACTABLE = (
    "add",
    "add a task",
    "add a task to",
    "create",
    "new",
)


@pytest.mark.parametrize("utterance", UNEEXTRACTABLE)
def test_an_unextractable_title_yields_no_proposal_and_a_reason(utterance: str) -> None:
    """Nothing confident survives → no proposal, and an explicit reason.

    The wrong title is worse than no title: it creates a real task the user then
    has to find and delete, and the delete is the one operation this layer will
    not help them with.
    """
    result = extract(utterance)
    assert result.title is None
    assert result.reason, "a refusal must say why"
    assert not result.usable

    refusal = refuse(utterance)
    assert refusal.reason_code == ProposalReason.TITLE_NOT_RECOVERABLE
    assert refusal.kind is ActionKind.CREATE_TASK
    assert "add a task to" in refusal.reason


def test_an_over_long_title_is_clipped_and_the_clipping_is_reported() -> None:
    """A title longer than the column is bounded — and the bound is said out loud."""
    long_subject = " ".join(["word"] * 120)
    result = extract(f"add a task to {long_subject}")
    assert result.title is not None
    assert len(result.title) <= 300
    assert any("clipped" in note for note in result.notes), result.notes


# --------------------------------------------------------------------------- #
# Completions
# --------------------------------------------------------------------------- #

COMPLETION_UTTERANCES: tuple[tuple[str, str], ...] = (
    ("Mark the API contract task as done", "Draft the API contract"),
    ("mark the api contract task as done", "Draft the API contract"),
    ("Finish the retro", "Run the retro"),
    ("close out the retro task", "Run the retro"),
    ("please mark the API contract as complete", "Draft the API contract"),
)


@pytest.mark.parametrize(
    ("utterance", "expected_title"),
    COMPLETION_UTTERANCES,
    ids=[u for u, _ in COMPLETION_UTTERANCES],
)
def test_completion_requires_naming_one_task(utterance: str, expected_title: str) -> None:
    """A completion resolves to exactly one of the caller's own rows.

    The proposal quotes the *stored* title, not the fragment: the confirm dialog
    has to show the row the write will touch.
    """
    proposal = propose(utterance)
    assert proposal.kind is ActionKind.COMPLETE_TASK
    assert proposal.target_id in (CONTRACT_TASK_ID, RETRO_TASK_ID)
    assert proposal.payload == TaskStatusChange(status=TaskStatus.COMPLETED)
    assert expected_title in proposal.summary


def test_a_completion_naming_nothing_produces_no_proposal() -> None:
    """The phrase "mark it as done" names no task, so there is nothing safe to write."""
    refusal = refuse("Mark it as done")
    assert refusal.reason_code in (
        ProposalReason.TITLE_NOT_RECOVERABLE,
        ProposalReason.TASK_REFERENCE_NOT_FOUND,
    )


def test_a_completion_matching_several_tasks_is_refused() -> None:
    """Two rows contain the phrase; picking either would be a coin toss on a write."""
    ctx = context(
        task_candidates=(
            TaskCandidate(CONTRACT_TASK_ID, "Draft the API contract"),
            TaskCandidate(THIRD_TASK_ID, "Review the API contract"),
        )
    )
    refusal = refuse("mark the API contract task as done", ctx=ctx)
    assert refusal.reason_code == ProposalReason.TASK_REFERENCE_AMBIGUOUS
    assert "will not guess" in refusal.reason


def test_a_completion_matching_nothing_is_refused() -> None:
    refusal = refuse("mark the quarterly retrospective task as done")
    assert refusal.reason_code == ProposalReason.TASK_REFERENCE_NOT_FOUND


def test_completion_without_candidates_is_refused_rather_than_matched() -> None:
    """An empty candidate set is "not found", never "the only one is right"."""
    refusal = refuse("mark the API contract task as done", ctx=context(task_candidates=()))
    assert refusal.reason_code == ProposalReason.TASK_REFERENCE_NOT_FOUND


def test_matching_prefers_an_exact_title_over_a_containment_match() -> None:
    """A row named exactly what was said beats a row that merely contains it."""
    ctx = context(
        task_candidates=(
            TaskCandidate(CONTRACT_TASK_ID, "API contract"),
            TaskCandidate(THIRD_TASK_ID, "Draft the API contract v2"),
        )
    )
    proposal = propose("mark the API contract task as done", ctx=ctx)
    assert proposal.target_id == CONTRACT_TASK_ID


def test_match_task_reference_returns_the_match_shape_not_a_bare_object() -> None:
    """The matcher names the rule it used, for the same reason every other field does."""
    match = match_task_reference(
        "API contract", (TaskCandidate(CONTRACT_TASK_ID, "Draft the API contract"),)
    )
    assert isinstance(match, TaskMatch)
    assert match.candidate.id == CONTRACT_TASK_ID
    assert match.rule


# --------------------------------------------------------------------------- #
# The safety rule
# --------------------------------------------------------------------------- #

DESTRUCTIVE_UTTERANCES: tuple[tuple[str, str], ...] = (
    ("Delete that duplicate reminder I created", str(Intent.TASK_MANAGE)),
    ("delete every task on the board", str(Intent.TASK_MANAGE)),
    ("please delete the migration plan task", str(Intent.TASK_MANAGE)),
    ("Delete the old marketing site project", str(Intent.PROJECT_MANAGE)),
    ("delete that note about the token rotation policy", str(Intent.KNOWLEDGE_CAPTURE)),
    ("erase the Q1 retrospective goal", str(Intent.LEARNING_TRACK)),
    ("get rid of that task", str(Intent.TASK_MANAGE)),
    ("nuke the old project", str(Intent.PROJECT_MANAGE)),
    ("wipe the stale tasks", str(Intent.TASK_MANAGE)),
    ("purge the archived notes", str(Intent.KNOWLEDGE_CAPTURE)),
    ("destroy the learning goal for Rust", str(Intent.LEARNING_TRACK)),
    ("throw away the duplicate reminder", str(Intent.TASK_MANAGE)),
)


@pytest.mark.parametrize(
    ("utterance", "intent"),
    DESTRUCTIVE_UTTERANCES,
    ids=[utterance for utterance, _ in DESTRUCTIVE_UTTERANCES],
)
def test_a_destructive_request_produces_no_proposal_at_all(utterance: str, intent: str) -> None:
    """The security test of this slice, pinned from the utterance side.

    Every one of these is classified ``task_manage``, ``project_manage``,
    ``knowledge_capture`` or ``learning_track`` — the same classes a creation
    arrives on, at the same confidence. If the verb were read off the intent and
    then used to choose create-or-delete, every line here would be a live delete
    on somebody's board. There is no delete, so every line here is a refusal with
    a reason instead, and the user is told why.
    """
    refusal = refuse(utterance, intent)
    assert refusal.reason_code == ProposalReason.DESTRUCTIVE_REQUEST
    assert refusal.kind is None
    assert refusal.destructive is False
    assert not is_proposal(refusal)
    assert "classifier" in refusal.reason


def test_the_destructive_check_wins_over_the_command_prefix() -> None:
    """An "add … then delete" request must not be answered by creating anything.

    The refusal is unconditional: once a destruction verb is present the layer
    stops, rather than extracting from a sentence that also asks for a write.
    """
    result = extract("add a task to clean up the board and delete the old ones")
    assert result.verb == ExtractedVerb.DESTRUCTIVE
    assert result.title is None
    assert result.arguments == ()


def test_no_proposal_kind_is_destructive() -> None:
    """The kind set itself. A ``delete`` member here would be a live delete path."""
    names = {str(kind) for kind in ActionKind}
    for forbidden in ("delete", "remove", "destroy", "cancel", "archive", "block", "purge"):
        assert forbidden not in names, f"a destructive kind slipped into {names}"


def test_every_spec_reports_non_destructive() -> None:
    assert ACTION_SPECS
    for kind, spec in ACTION_SPECS.items():
        assert spec.kind is kind
        assert spec.destructive is False, kind


def test_destructive_is_a_property_so_it_cannot_be_set_true() -> None:
    """The protection is structural, not a value someone chose.

    ``destructive`` and ``requires_confirmation`` are properties returning
    ``False`` and ``True``. A field would have been a value a future table entry
    could carry; a property is not.
    """
    assert not isinstance(ActionSpec.__dict__.get("destructive"), object.__class__)
    assert isinstance(inspect.getattr_static(ActionSpec, "destructive"), property)
    assert isinstance(inspect.getattr_static(ActionProposal, "destructive"), property)
    assert isinstance(inspect.getattr_static(ActionProposal, "requires_confirmation"), property)

    proposal = propose("Add a task to finish DSA")
    with pytest.raises((AttributeError, TypeError)):
        proposal.destructive = True  # type: ignore[misc]


def test_requires_confirmation_cannot_be_switched_off() -> None:
    """No caller may construct a proposal that skips the user.

    ``requires_confirmation`` is a property, not a constructor argument, so
    passing it is a ``TypeError`` rather than a silently ignored keyword.
    """
    proposal = propose("Add a task to finish DSA")
    assert proposal.requires_confirmation is True
    assert "requires_confirmation" not in ActionProposal.__dataclass_fields__

    with pytest.raises(TypeError):
        ActionProposal(
            kind=ActionKind.CREATE_TASK,
            intent="task_manage",
            confidence=0.9,
            payload=TaskCreate(title="x", project_id=PROJECT_ID),
            arguments=(),
            permission=Permission.TASKS_WRITE,
            summary="x",
            spec=ACTION_SPECS[ActionKind.CREATE_TASK],
            requires_confirmation=False,  # type: ignore[call-arg]
        )


def test_every_proposal_and_refusal_is_non_destructive_and_confirmed() -> None:
    """The blanket assertion over both outcome types.

    Whatever comes back, it is not destructive and it is not something to act on
    without the user. A refusal that reported ``destructive=False`` would still be
    misleading, which is why the refusal type carries the same two properties.
    """
    outcomes = [
        propose("Add a task to finish DSA"),
        propose("Mark the API contract task as done"),
        propose("Save a note about the prune suffix"),
        refuse("Delete that task"),
        refuse("Show me my tasks"),
        refuse("Add a task"),
    ]
    for outcome in outcomes:
        assert outcome.destructive is False, outcome
        assert outcome.requires_confirmation is True, outcome
        assert outcome.to_dict()["destructive"] is False, outcome
        assert outcome.to_dict()["requires_confirmation"] is True, outcome


def test_cancel_is_not_treated_as_delete() -> None:
    """``cancel`` is absent from the destructive vocabulary on purpose.

    It is refused too — no ``cancel`` kind exists either — but for the honest
    reason: the verb is not one NEXUS can resolve from the text with confidence.
    Conflating "NEXUS declined to delete" with "NEXUS cannot tell what cancel
    means" would be a lie in the user's face about why they got nothing.
    """
    assert "cancel" not in DESTRUCTIVE_VERBS
    refusal = refuse("cancel the migration plan task")
    assert refusal.reason_code != ProposalReason.DESTRUCTIVE_REQUEST
    assert not is_proposal(refusal)


def test_remove_and_clear_are_not_destructive_markers() -> None:
    """The phrases "remove the priority" and "clear the due date" are ordinary edits.

    A marker list that refused those would refuse correct requests in order to
    guard against a word it cannot disambiguate — which is the guessing this
    layer exists to avoid, pointed the other way.
    """
    for verb in ("remove", "clear"):
        assert verb not in DESTRUCTIVE_VERBS
        assert extract_verb(f"{verb} the priority from the migration task") != (
            ExtractedVerb.DESTRUCTIVE
        )


# --------------------------------------------------------------------------- #
# Payloads, permissions, context
# --------------------------------------------------------------------------- #


def test_every_spec_names_a_service_call_that_actually_exists() -> None:
    """The service, module and entry point are resolved and called-checked.

    They are strings so that ``app.ml.actions`` stays importable without the
    ORM, which makes them exactly the thing that can silently go stale: a
    service that is renamed leaves a proposal naming a module nothing can import
    — the one failure a proposal exists to prevent, because the caller would
    build a confirm dialog around a call it cannot make.
    """
    from app.ml.router import resolve_service
    from app.ml.schemas import ServiceTarget

    for kind, spec in ACTION_SPECS.items():
        service = resolve_service(
            ServiceTarget(service=spec.service, module=spec.module, entrypoint=spec.entrypoint)
        )
        assert inspect.isclass(service), kind
        assert hasattr(service, spec.entrypoint), f"{kind}.{spec.entrypoint}"
        assert callable(getattr(service, spec.entrypoint)), kind


def test_each_payload_validates_against_the_schema_its_spec_names() -> None:
    """No second definition of what a create means.

    The spec holds a reference to the Pydantic model, so a proposal is by
    construction the shape the API route already accepts — and a renamed field
    cannot pass here and 422 there.
    """
    cases = (
        ("Add a task to finish DSA", str(Intent.TASK_MANAGE), ActionKind.CREATE_TASK, TaskCreate),
        (
            "Create a project for the mobile redesign",
            str(Intent.PROJECT_MANAGE),
            ActionKind.CREATE_PROJECT,
            ProjectCreate,
        ),
        (
            "Save a note about the prune suffix",
            str(Intent.KNOWLEDGE_CAPTURE),
            ActionKind.CREATE_NOTE,
            NoteCreate,
        ),
        (
            "Add a learning goal for distributed systems",
            str(Intent.LEARNING_TRACK),
            ActionKind.CREATE_LEARNING_GOAL,
            LearningGoalWrite,
        ),
        (
            "Mark the API contract task as done",
            str(Intent.TASK_MANAGE),
            ActionKind.COMPLETE_TASK,
            TaskStatusChange,
        ),
    )
    for utterance, intent, kind, schema in cases:
        proposal = propose(utterance, intent)
        assert proposal.kind is kind, utterance
        assert proposal.spec.schema is schema, utterance
        assert isinstance(proposal.payload, schema), utterance
        assert proposal.spec.schema(**proposal.payload.model_dump()) is not None


def test_each_kind_names_the_permission_its_route_enforces() -> None:
    assert ACTION_SPECS[ActionKind.CREATE_TASK].permission is Permission.TASKS_WRITE
    assert ACTION_SPECS[ActionKind.COMPLETE_TASK].permission is Permission.TASKS_WRITE
    assert ACTION_SPECS[ActionKind.CREATE_PROJECT].permission is Permission.PROJECTS_WRITE
    assert ACTION_SPECS[ActionKind.CREATE_NOTE].permission is Permission.KNOWLEDGE_WRITE
    # The learning router reuses ``analytics.read`` (see ``app/api/v1/learning.py``);
    # a proposal that named a capability the route does not check would let the
    # confirm dialog promise something the write would then refuse.
    assert ACTION_SPECS[ActionKind.CREATE_LEARNING_GOAL].permission is Permission.ANALYTICS_READ

    for utterance, intent in (
        ("Add a task to finish DSA", str(Intent.TASK_MANAGE)),
        ("Create a project for the mobile redesign", str(Intent.PROJECT_MANAGE)),
        ("Save a note about the prune suffix", str(Intent.KNOWLEDGE_CAPTURE)),
        ("Add a learning goal for Rust", str(Intent.LEARNING_TRACK)),
    ):
        proposal = propose(utterance, intent)
        assert proposal.permission is proposal.spec.permission


def test_a_task_without_a_project_is_refused_not_guessed() -> None:
    """``TaskCreate.project_id`` is required, and no utterance contains a UUID.

    Inventing one would file the task on a board the user did not choose. The
    refusal says what is missing and where to go instead.
    """
    refusal = refuse("Add a task to finish DSA", ctx=context(project_id=None))
    assert refusal.reason_code == ProposalReason.CONTEXT_MISSING
    assert "project" in refusal.reason


def test_the_project_is_named_in_the_summary_from_the_callers_own_label() -> None:
    """The whole effect has to be in the sentence, including where the task lands."""
    with_label = propose("Add a task to finish DSA")
    assert "in project 'Nexo rewrite'" in with_label.summary

    unlabelled = propose("Add a task to finish DSA", ctx=context(project_label=None))
    assert str(PROJECT_ID) in unlabelled.summary
    assert unlabelled.target_id == PROJECT_ID


def test_a_proposal_carries_no_callable_and_no_service_instance() -> None:
    """Nothing here can execute.

    A proposal holds a Pydantic payload and three strings naming a service — not
    the service, not a session, not a coroutine. This is asserted structurally
    because it is the property the whole layer exists to have.
    """
    proposal = propose("Add a task to finish DSA")
    rendered = proposal.to_dict()
    assert isinstance(rendered["service"], str)
    assert isinstance(rendered["entrypoint"], str)
    assert not callable(rendered["service"])

    spec = proposal.spec
    assert isinstance(spec.service, str)
    assert isinstance(spec.module, str)
    assert isinstance(spec.entrypoint, str)
    assert isinstance(spec.schema, type) and issubclass(spec.schema, BaseModel)

    # Nothing hanging off a proposal is callable: no service object, no session,
    # no coroutine function. Enumerated through the dataclass fields rather than
    # ``vars()``, which raises on a ``slots=True`` dataclass and would make the
    # assertion vacuous.
    for name in ActionProposal.__dataclass_fields__:
        value = getattr(proposal, name)
        assert not callable(value), name


def test_a_payload_that_does_not_validate_is_a_refusal_not_an_exception() -> None:
    """The failure mode is a question back, not a 422 at a caller who asked one.

    ``_build_payload`` is reached directly here because the extraction cannot
    produce an invalid value through the public API — which is the point. If a
    future rule ever can, the contract under test is what happens next.
    """
    broken = Extraction(
        intent=str(Intent.TASK_MANAGE),
        verb=ExtractedVerb.CREATE,
        title="finish DSA",
        priority="urgent",
        arguments=(),
        confidence=0.9,
    )
    payload = proposals_module._build_payload(
        ACTION_SPECS[ActionKind.CREATE_TASK], extraction=broken, project_id=PROJECT_ID
    )
    assert payload is None
    assert not isinstance(payload, BaseModel)


# --------------------------------------------------------------------------- #
# Provenance
# --------------------------------------------------------------------------- #


def test_every_extracted_field_records_where_it_came_from() -> None:
    """No field is returned bare.

    A confirm dialog that cannot say *"due Friday — matched 'due friday'"* is
    asking the user to trust a number produced by a string rule they have never
    seen.
    """
    proposal = propose("Add a high priority task to finish DSA due Friday")
    fields = {argument.field for argument in proposal.arguments}
    assert fields == {"title", "priority", "due_date", "due_at"}
    for argument in proposal.arguments:
        assert argument.matched_text, argument
        assert argument.rule, argument
        assert argument.value, argument
        assert argument.to_dict()["field"] == argument.field

    by_field = {argument.field: argument for argument in proposal.arguments}
    assert "high priority" in by_field["priority"].matched_text
    assert "friday" in by_field["due_date"].matched_text.lower()
    assert by_field["due_date"].value == "2026-03-13"
    assert by_field["due_at"].value == "2026-03-13T00:00:00+00:00"
    assert LISBON.key in by_field["due_at"].rule


def test_the_proposal_serialises_the_provenance_for_the_dialog() -> None:
    proposal = propose("Add a high priority task to finish DSA due Friday")
    rendered = proposal.to_dict()
    assert {entry["field"] for entry in rendered["arguments"]} == {
        "title",
        "priority",
        "due_date",
        "due_at",
    }
    assert rendered["summary"]
    assert rendered["permission"] == "tasks.write"
    assert rendered["schema"] == "TaskCreate"
    assert rendered["target_id"] == str(PROJECT_ID)
    assert rendered["target_label"] == "Nexo rewrite"


def test_the_refusal_carries_the_extraction_it_refused_on() -> None:
    """A user told "ask again" should at least see what NEXUS did understand."""
    refusal = refuse("add a task to finish DSA and then delete the rest")
    assert refusal.notes == () or all(isinstance(note, str) for note in refusal.notes)
    assert refusal.to_dict()["reason_code"] == ProposalReason.DESTRUCTIVE_REQUEST


# --------------------------------------------------------------------------- #
# The summary sentence
# --------------------------------------------------------------------------- #


def test_the_summary_states_the_whole_effect() -> None:
    """One sentence the user can check without cross-referencing anything.

    The brief's own example, end to end: title, priority, date and destination,
    with nothing invented and nothing omitted.
    """
    proposal = propose("Add a high priority task to finish DSA due Friday")
    assert render_summary(proposal) == (
        "Create a high-priority task titled 'finish DSA', due Friday, in project 'Nexo rewrite'."
    )
    assert render_summary(proposal).endswith(".")


@pytest.mark.parametrize(
    ("utterance", "intent", "expected"),
    (
        (
            "Add a task to finish DSA",
            str(Intent.TASK_MANAGE),
            "Create a task titled 'finish DSA', in project 'Nexo rewrite'.",
        ),
        (
            "Create a project for the mobile redesign targeting 2026-05-01",
            str(Intent.PROJECT_MANAGE),
            "Create a project named 'mobile redesign', targeting 2026-05-01.",
        ),
        (
            "Save a note about the auth token rotation policy",
            str(Intent.KNOWLEDGE_CAPTURE),
            "Save a note titled 'auth token rotation policy'.",
        ),
        (
            "Add a learning goal for distributed systems by 2026-06-01",
            str(Intent.LEARNING_TRACK),
            "Create a learning goal titled 'distributed systems', targeting 2026-06-01.",
        ),
        (
            "Mark the API contract task as done",
            str(Intent.TASK_MANAGE),
            "Mark the task 'Draft the API contract' as completed.",
        ),
    ),
)
def test_each_kind_renders_its_own_sentence(utterance: str, intent: str, expected: str) -> None:
    """A project has a *target* date and a task has a *due* date.

    They are different columns with different meanings, and a sentence that
    called a project's target "due" would describe an obligation the user never
    accepted.
    """
    assert render_summary(propose(utterance, intent)) == expected


def test_a_kind_with_no_date_field_is_never_described_as_having_one() -> None:
    """A note has no date column, so the sentence must not claim one.

    "Save a note about the retro tomorrow" resolves the date — the extractor
    cannot know the entity has nowhere to put it — and the sentence would then
    describe a deadline the payload does not carry. Confirming that is worse than
    saying nothing, so the clause is dropped for a kind with no date field.
    """
    proposal = propose("Save a note about the retro tomorrow", str(Intent.KNOWLEDGE_CAPTURE))
    assert proposal.spec.date_field is None
    assert proposal.summary == "Save a note titled 'retro'."
    assert "due" not in proposal.summary
    assert proposal.payload.model_dump()["title"] == "retro"


def test_a_distant_date_is_rendered_as_a_date_not_a_weekday() -> None:
    """A weekday name is fine for the coming week and wrong for a date in June."""
    proposal = propose("Add a task to write the retrospective due 2026-09-15")
    assert proposal.summary == (
        "Create a task titled 'write the retrospective', due 2026-09-15, in project 'Nexo rewrite'."
    )


def test_the_summary_never_names_a_field_the_user_did_not_say() -> None:
    """A missing clause means "not stated" — never the schema's default."""
    plain = propose("Add a task to finish DSA")
    assert "priority" not in plain.summary
    assert "due" not in plain.summary


# --------------------------------------------------------------------------- #
# No second model, no execution
# --------------------------------------------------------------------------- #

_ACTIONS_DIR = Path(extraction_module.__file__).parent

#: Substrings that would indicate a second inference backend. Matched against the
#: source of the whole package.
_FORBIDDEN_BACKENDS = (
    "ollama",
    "llama",
    "qwen",
    "whisper",
    "openai",
    "anthropic",
    "transformers",
    "torch",
    "langchain",
    "litellm",
    "huggingface",
    "from_pretrained",
    "AutoModel",
)


def test_there_is_no_second_model_in_this_package() -> None:
    """Phase 13's acceptance criterion: NEXUS runs a classifier and nothing else.

    The extraction is a regex parser by design — the brief permits a deterministic
    fallback and forbids another AI model — so the whole package's source is
    swept for a reference to an inference backend. The scan carries a **positive
    control**: a sweep that finds nothing because it searched for the wrong thing
    is indistinguishable from a clean bill of health.
    """
    sources = {
        path.name: path.read_text(encoding="utf-8") for path in sorted(_ACTIONS_DIR.glob("*.py"))
    }
    assert sources, "the package must have sources to scan"

    haystack = "\n".join(sources.values())
    for backend in _FORBIDDEN_BACKENDS:
        assert backend.lower() not in haystack.lower(), f"{backend} appears in app/ml/actions"

    positive_control = haystack.lower().count("import re")
    assert positive_control >= 1, "the scan is not matching the text it is meant to match"


def test_this_package_imports_no_service_and_no_orm() -> None:
    """``app.ml`` stays importable without a database, as the router established."""
    sources = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(_ACTIONS_DIR.glob("*.py"))
    )
    code = "\n".join(line for line in sources.splitlines() if not line.lstrip().startswith("#"))
    for forbidden in ("app.services", "app.db", "sqlalchemy", "app.models"):
        assert f"import {forbidden}" not in code, forbidden

    # ``app.schemas`` is allowed — it holds the payload contracts — but it must be
    # imported by module, never by pulling a row out of a session.
    assert "app.schemas.task import" in code


def test_importing_actions_does_not_import_torch_in_a_fresh_interpreter() -> None:
    """The classifier's runtime stays out of this slice.

    :mod:`app.ml.router` established that the classifier half of ``app.ml`` must
    stay importable on a machine with no torch; a subpackage that pulled it in
    would break that for every caller of the routing table. Checked in a
    subprocess because this one may well have torch loaded already, through
    another test.
    """
    import subprocess
    import sys

    script = (
        "import sys;"
        "import app.ml.actions;"
        "assert 'torch' not in sys.modules, 'torch was imported';"
        "assert 'transformers' not in sys.modules, 'transformers was imported';"
        "print('ok')"
    )
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_no_module_level_side_effects() -> None:
    """Importing the package reads no clock, opens nothing and writes nothing."""
    import app.ml.actions as actions

    for name in actions.__all__:
        assert hasattr(actions, name), name


# --------------------------------------------------------------------------- #
# Vocabulary integrity
# --------------------------------------------------------------------------- #


def test_the_supported_intent_set_matches_the_specs_that_have_kinds() -> None:
    """Extraction and proposal must agree about which intents have arguments.

    An intent with a proposal kind but no extraction rules would produce a
    ``TITLE_NOT_RECOVERABLE`` for every utterance, which reads like a bug and is
    really a missing entry.
    """
    intents_with_kinds = {str(spec.intent) for spec in ACTION_SPECS.values()}
    assert intents_with_kinds == extraction_module.SUPPORTED_INTENTS


def test_every_intent_with_a_kind_reaches_it_from_a_plain_utterance() -> None:
    """A positive control for the test above, at the behaviour level.

    Every spec must be reachable, or the table is describing a surface that does
    not exist.
    """
    reached = {
        str(propose(utterance, intent).kind)
        for utterance, intent in (
            ("Add a task to finish DSA", str(Intent.TASK_MANAGE)),
            ("Mark the API contract task as done", str(Intent.TASK_MANAGE)),
            ("Create a project for the mobile redesign", str(Intent.PROJECT_MANAGE)),
            ("Save a note about the prune suffix", str(Intent.KNOWLEDGE_CAPTURE)),
            ("Add a learning goal for Rust", str(Intent.LEARNING_TRACK)),
        )
    }
    assert reached == {str(kind) for kind in ActionKind}


def test_the_priority_vocabulary_only_produces_persisted_grades() -> None:
    """Every synonym maps onto a value ``TaskPriority`` actually has."""
    grades = {str(grade) for grade in TaskPriority}
    for phrase in PRIORITY_PHRASES:
        result = extract(f"add a {phrase[0]} task to finish DSA")
        assert result.priority in grades, phrase


def test_the_extractor_leaves_the_users_capitalisation_alone() -> None:
    """Case is noise for matching and load-bearing for display.

    The title a user reads is the one they typed; the *matching* normalisation is
    applied to a copy, which is why "Mark the API contract task as done" matches
    a row stored as "Draft the API contract".
    """
    proposal = propose("Add a task to Review the ADR")
    assert proposal.payload.title == "Review the ADR"


def test_whitespace_and_punctuation_do_not_change_the_answer() -> None:
    noisy = extract("  add   a  task  to   finish   DSA   tomorrow  ")
    tidy = extract("add a task to finish DSA tomorrow")
    assert noisy.title == tidy.title == "finish DSA"
    assert noisy.due_date == tidy.due_date


def test_uuid_and_date_rendering_in_to_dict_is_json_safe() -> None:
    import json

    proposal = propose("Add a high priority task to finish DSA due Friday")
    encoded = json.dumps(proposal.to_dict())
    assert "2026-03-13" in encoded
    assert str(PROJECT_ID) in encoded
    assert json.loads(encoded)["kind"] == "create_task"


def test_a_candidate_uuid_is_echoed_in_a_completion_proposal() -> None:
    proposal = propose("Mark the API contract task as done")
    assert isinstance(proposal.target_id, UUID)
    assert proposal.to_dict()["target_id"] == str(proposal.target_id)


def test_the_filler_title_list_rejects_an_empty_subject() -> None:
    """A word that survives every strip rule but names nothing is still refused."""
    assert extract("add a task to it").title is None


def test_a_named_subject_keeps_a_trailing_entity_noun_that_is_part_of_its_name() -> None:
    """ "create a task called finish the rollout task" must not lose a word.

    The trailing-noun strip exists so "the API contract task" resolves to the
    row called "API contract" — the noun there is a type. But when the user
    introduces the subject *by name*, a trailing noun is part of the name they
    typed, and stripping it silently edits their request. The user sees the
    title in the confirmation dialog, so a title that kept a redundant noun is
    a cosmetic annoyance, while one that lost a word is a wrong row created.
    """
    assert extract("create a high priority task called finish the rollout task").title == (
        "finish the rollout task"
    )
    assert extract("create a task named review the API contract task").title == (
        "review the API contract task"
    )


def test_a_type_noun_is_still_stripped_when_no_name_was_given() -> None:
    """The other half of the pair: without a naming connector the strip stands.

    Without this the fix would read as "never strip a trailing noun", which
    would break every completion reference — "mark the API contract task as
    done" is matched against real titles, and the row is called "API contract".
    """
    assert extract_title("the API contract task", intent="task_manage")[0] == "API contract"
    assert extract_title("my q4 reporting task", intent="task_manage")[0] == "q4 reporting"


def test_a_named_subject_is_unaffected_when_it_ends_in_something_else() -> None:
    """The rule must not fire on titles that never had a noun to protect."""
    assert extract("create a task called finish the rollout plan").title == (
        "finish the rollout plan"
    )
    assert (
        extract("create a project called nebula", intent=str(Intent.PROJECT_MANAGE)).title
        == "nebula"
    )
    assert extract("add a task to write the migration plan").title == "write the migration plan"
