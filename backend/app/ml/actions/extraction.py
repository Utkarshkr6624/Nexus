"""Deterministic argument extraction: the half of an assistant a classifier cannot do.

Phase 11 answered the question its own router docstring posed — *"turning 'add a
task to draft the migration plan for Friday' into
``TaskService.create_task(title=..., due_date=...)`` is slot filling, and it needs
either a second model or hand-written per-utterance parsers"*. The project brief
settles which of those two NEXUS gets: **"any deterministic fallback is allowed
where technically necessary, but it must NOT be another AI model."** This module
is that fallback. It is the hand-written parser, and it is stdlib-only: ``re``,
``datetime``, ``zoneinfo``. No model, no network, no service import.

**Why hand-written is the right answer here rather than a compromise.** The
classifier is a 14-way softmax over whole utterances. It cannot tell *"add a
task"* from *"delete everything"* — both are ``task_manage``, because the
taxonomy asks *which surface a request lands on*, not *what to do to it*. Verb is
not one of the fourteen classes. A second model doing slot filling would have to
learn that taxonomy from scratch and would still be guessing on *"finish the DSA
task"*, where the verb and the subject share a span. A regex can also be wrong,
but it is wrong **visibly**: it records the phrase it matched (:class:`Argument`),
so a proposal the user is about to confirm can show its own reasoning, and the
test suite can pin the exact behaviour instead of a probability.

**Every field this module emits carries its provenance.** Nothing is returned
bare. An :class:`Argument` names the span that produced it and the rule that
consumed it, which is what lets a confirm dialog say *"due Friday — matched 'due
friday'"* instead of asking the user to trust a number.

**An ambiguous date yields no date.** This is the most important rule in the
file. *"on friday"* said on a Friday has two honest readings; a bare *"next
week"* names no day; ``2026-13-45`` is not a date. Each produces
``due_date=None`` plus a note saying what was seen and why it was not resolved —
**never** a nearest-match guess, because a deadline is the field a user trusts
without re-reading it, and a plausible wrong deadline is worse than an absent one
the confirm dialog can show plainly. The unresolvable phrase is still *cut from
the title*: leaving "for friday" inside the title of a task that has no date
would make the summary describe a title the user never wrote.

**Titles are recovered or refused, never invented.** After the command phrase
and the entity noun are removed, what is left is the subject. If nothing is left,
or what is left is a filler word, there is no title and :attr:`Extraction.reason`
says so. A confidently wrong title creates a confidently wrong task, and the
user approved a sentence they did not read.

**Timezones are the caller's, and the conversion mirrors the planner.** A date
resolves against the caller's IANA zone, and :attr:`Extraction.due_at` is the
day's local midnight converted to offset-aware UTC — built exactly as
:func:`app.services.planner_service.day_bounds` builds it
(``datetime.combine(day, time.min, tzinfo=zone).astimezone(UTC)``), so a day
planned here and a day planned by the planner cut their windows on the same
instant. The planner's function is **not** imported: everything under
``app.services`` pulls in the ORM session machinery, and
:mod:`app.ml.router` already established that the classifier half of the package
stays importable without a database. The mirror is one line and both docstrings
name the other.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from types import MappingProxyType
from uuid import UUID
from zoneinfo import ZoneInfo

from app.ml.schemas import IntentPrediction
from app.schemas.knowledge import MAX_NOTE_TITLE_LENGTH
from app.schemas.project import MAX_PROJECT_NAME_LENGTH
from app.schemas.task import MAX_TASK_TITLE_LENGTH
from ml.datasets.taxonomy import Intent

__all__ = [
    "DESTRUCTIVE_VERBS",
    "SUPPORTED_INTENTS",
    "Argument",
    "ExtractedVerb",
    "Extraction",
    "TaskCandidate",
    "TaskMatch",
    "TaskMatchFailure",
    "day_start_utc",
    "extract_arguments",
    "extract_verb",
    "find_date",
    "local_day",
    "match_task_reference",
    "resolve_weekday",
    "strip_command_prefix",
]

#: The intents this module can pull arguments out of. Everything else has no
#: argument shape here, and :mod:`app.ml.actions.proposals` refuses it with an
#: explicit reason rather than trying.
SUPPORTED_INTENTS: frozenset[str] = frozenset(
    {
        str(Intent.TASK_MANAGE),
        str(Intent.PROJECT_MANAGE),
        str(Intent.KNOWLEDGE_CAPTURE),
        str(Intent.LEARNING_TRACK),
    }
)

#: ``LearningGoalWrite.title`` declares ``max_length=200`` inline rather than
#: through a module constant, so it is repeated here with the field it mirrors.
#: Every bound below is the *schema's* bound, not a number chosen here: a title
#: clipped to it always validates against the payload it is written to.
_MAX_LEARNING_GOAL_TITLE_LENGTH = 200

#: How many prefixes one utterance may give up before the loop is considered
#: pathological. Three covers "can you please add"; a longer chain means the input
#: is not an instruction in this family.
_MAX_PREFIX_STRIPS = 3

#: A recovered subject shorter than this, or made only of these words, is not a
#: title.
_MIN_TITLE_CHARACTERS = 3
_FILLER_TITLES: frozenset[str] = frozenset(
    {
        "it",
        "that",
        "this",
        "one",
        "them",
        "thing",
        "stuff",
        "something",
        "anything",
        "task",
        "note",
        "project",
        "goal",
        "todo",
        "reminder",
        "new",
        "me",
        "done",
    }
)


# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #

#: Framing that carries no verb. Stripped before anything else and never counted
#: as evidence of a creation — "please mark it done" is a completion, and without
#: this separation it would read as "please" (create) plus "mark" (complete) and
#: be refused as ambiguous.
_POLITENESS_PREFIXES: tuple[str, ...] = tuple(
    sorted(
        (
            "can you please",
            "could you please",
            "would you please",
            "can you",
            "could you",
            "would you",
            "will you",
            "please",
        ),
        key=len,
        reverse=True,
    )
)

#: Leading phrases that mark a *creation*. "i need to" is in here because
#: "I need to learn Rust" is a request to record something; it is not a verb the
#: extractor can read off the sentence in isolation.
_CREATE_PREFIXES: tuple[str, ...] = tuple(
    sorted(
        (
            "remind me to",
            "remind me about",
            "i would like to",
            "i'd like to",
            "i need to",
            "i want to",
            "i have to",
            "i must",
            "note down",
            "jot down",
            "set up",
            "create",
            "capture",
            "save",
            "store",
            "file",
            "write",
            "jot",
            "add",
            "make",
            "new",
        ),
        key=len,
        reverse=True,
    )
)

#: Leading verbs for the completion family. Only *leading* position counts,
#: because "finish" is an ordinary word inside a title — "add a task to finish
#: DSA" is a creation, and matching the word anywhere would propose completing a
#: task that was never finished.
_COMPLETE_PREFIXES: tuple[str, ...] = (
    "mark off",
    "mark",
    "complete",
    "finish",
    "close out",
    "close",
    "tick off",
    "check off",
)

#: A trailing completion marker, which may stand anywhere: "mark the API contract
#: task as done", "the API contract task is done".
_COMPLETE_MARKER_RE = re.compile(
    r"\b(?:as\s+(?:done|complete|completed|finished)|is\s+done|are\s+done|"
    r"got\s+done|is\s+complete)\b",
    re.IGNORECASE,
)

#: Verbs whose only plausible reading is destruction. See the module docstring of
#: :mod:`app.ml.actions.proposals` for why these never become a proposal kind.
#: ``remove`` and ``clear`` are deliberately **absent**: "remove the priority" and
#: "clear the due date" are ordinary edits, and a marker list that refused those
#: would refuse correct proposals in order to guard against a word it cannot
#: disambiguate — which is the guessing this layer exists to avoid.
DESTRUCTIVE_VERBS: frozenset[str] = frozenset(
    {"delete", "erase", "wipe", "purge", "nuke", "destroy"}
)

_DESTRUCTIVE_PHRASES: tuple[str, ...] = tuple(
    sorted(
        ("get rid of", "throw away", *DESTRUCTIVE_VERBS),
        key=len,
        reverse=True,
    )
)

#: Nouns that may sit between the article and the subject. Keyed by intent, so
#: "a note about X" is stripped for ``knowledge_capture`` and not for
#: ``task_manage``, where the same words are usually part of the title.
_ENTITY_NOUNS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        str(Intent.TASK_MANAGE): (
            "task",
            "tasks",
            "todo",
            "to-do",
            "reminder",
            "chore",
            "item",
        ),
        str(Intent.PROJECT_MANAGE): ("project", "projects", "initiative", "epic"),
        str(Intent.KNOWLEDGE_CAPTURE): ("note", "notes", "memo", "thought", "journal entry"),
        str(Intent.LEARNING_TRACK): ("learning goal", "goal", "study goal", "study plan"),
    }
)

#: Connectors that introduce the subject after the entity noun.
_SUBJECT_CONNECTORS: tuple[str, ...] = (
    "called",
    "named",
    "titled",
    "entitled",
    "that says",
    "saying",
    "about",
    "for",
    "to",
    "on",
    "of",
    ":",
)


def _entity_prefix_pattern(nouns: Sequence[str]) -> re.Pattern[str]:
    """Compile the ``subject prefix`` regex for one intent's nouns.

    Built from the table rather than hand-written per intent, so adding a noun is
    a one-word edit and the four patterns cannot drift apart in shape.
    """
    alias = "|".join(re.escape(noun) for noun in nouns)
    connectors = "|".join(
        re.escape(part) for part in sorted(_SUBJECT_CONNECTORS, key=len, reverse=True)
    )
    return re.compile(
        r"\A\s*(?:(?:an?|the|my|our)\s+)?(?:(?:new|another|extra)\s+)?"
        rf"(?:(?:{alias})\s+)*"
        rf"(?:(?:{connectors})\s+|\s*:\s*)",
        re.IGNORECASE,
    )


_ENTITY_PREFIX_RES: Mapping[str, re.Pattern[str]] = MappingProxyType(
    {intent: _entity_prefix_pattern(nouns) for intent, nouns in _ENTITY_NOUNS.items()}
)

#: The task nouns matched at the **end** of a phrase. A completion reference is
#: usually written about the task rather than to it — "the API contract task" —
#: and without this the word "task" becomes part of the name being matched.
_TRAILING_ENTITY_RE = re.compile(
    r"\s+(?:tasks?|todos?|to-?dos?|reminders?|items?|chores?)\s*$",
    re.IGNORECASE,
)

#: Priority vocabulary. The four values are the ones
#: :class:`app.models.enums.TaskPriority` and
#: :class:`~app.models.enums.ProjectPriority` persist; ``critical`` is a real
#: grade on both scales and not a synonym of ``high``, so "critical" maps to it
#: and "urgent" — which describes *this week* rather than a grade — maps to
#: ``high``. Matched longest-phrase-first, so "high priority" beats the "high"
#: inside it.
_PRIORITY_SYNONYMS: tuple[tuple[str, str], ...] = (
    ("critical priority", "critical"),
    ("highest priority", "high"),
    ("high priority", "high"),
    ("medium priority", "medium"),
    ("normal priority", "medium"),
    ("standard priority", "medium"),
    ("low priority", "low"),
    ("lowest priority", "low"),
    ("top priority", "high"),
    ("p0", "critical"),
    ("blocker", "critical"),
    ("critical", "critical"),
    ("urgent", "high"),
    ("asap", "high"),
    ("normal", "medium"),
    ("medium", "medium"),
    ("no rush", "low"),
    ("not urgent", "low"),
    ("whenever", "low"),
    ("someday", "low"),
    ("high", "high"),
    ("low", "low"),
)


def _priority_pattern(phrase: str) -> str:
    r"""Compile one priority phrase, tolerating a hyphen between its words.

    The un-escape-then-re-escape round trip is because :func:`re.escape` also
    escapes the space (as ``\ ``), and replacing the space directly would have
    left the stray backslash behind — turning ``[`` into an escaped literal
    bracket and silently matching nothing.
    """
    spaced = re.escape(phrase).replace("\\ ", " ")
    return rf"\b{spaced.replace(' ', r'[-\s]+')}\b"


_PRIORITY_RES: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(_priority_pattern(phrase), re.IGNORECASE), value)
    for phrase, value in sorted(_PRIORITY_SYNONYMS, key=lambda pair: len(pair[0]), reverse=True)
)

#: Words that sit next to the priority phrase and belong to it: "a task **with**
#: high priority", "a task - high priority".
_PRIORITY_LEAD_RE = re.compile(
    r"\s*(?:,\s*|\s+-\s+|\s*:\s*|\s+(?:with|at)\s+)\s*$",
    re.IGNORECASE,
)
_PRIORITY_TRAIL_RE = re.compile(
    r"^\s*(?:,\s*|\s+-\s+|\s*:\s*|\s+(?:and|with|priority)\s+)",
    re.IGNORECASE,
)

_WEEKDAY_OFFSETS: Mapping[str, int] = MappingProxyType(
    {
        "monday": 0,
        "mon": 0,
        "tuesday": 1,
        "tue": 1,
        "tues": 1,
        "wednesday": 2,
        "wed": 2,
        "weds": 2,
        "thursday": 3,
        "thu": 3,
        "thurs": 3,
        "thur": 3,
        "friday": 4,
        "fri": 4,
        "saturday": 5,
        "sat": 5,
        "sunday": 6,
        "sun": 6,
    }
)

#: A named day, with its optional connectors ("due", "by", "on", "for", "before",
#: "until", "starting") and optional week qualifier ("next", "this") folded in so
#: the connector is cut out of the title along with the date.
_WEEKDAY_RE = re.compile(
    r"\b(?:(?:due|by|on|for|before|until|starting|next|this)\s+){0,2}"
    r"(mon|tues?|wednes|thurs?|fri|satur|sun)day\b",
    re.IGNORECASE,
)

_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")

_KEYWORD_DATES: Mapping[str, int] = MappingProxyType(
    {"today": 0, "tomorrow": 1, "day after tomorrow": 2}
)

_KEYWORD_DATE_RE = re.compile(
    r"\b(?:(?:due|by|on|for|before|until|starting)\s+)?(?:the\s+)?"
    r"(day after tomorrow|today|tomorrow)\b",
    re.IGNORECASE,
)

#: Date-shaped wording this module recognises as *not* resolving. Listed rather
#: than detected structurally because a structural rule for "looks like a date
#: but is not" is itself a guess. Each is reported as a note so the confirm dialog
#: can say "no date — the request said 'sometime'".
_UNRESOLVED_DATE_MARKERS: tuple[str, ...] = (
    "next week",
    "next month",
    "next quarter",
    "this week",
    "this month",
    "end of week",
    "end of month",
    "sometime",
    "soon",
    "later",
    "asap",
    "whenever",
    "anytime",
    "no rush",
)

#: Title length bounds, keyed by intent, read from the schema that will validate
#: the payload rather than invented here.
_TITLE_BOUNDS: Mapping[str, tuple[int, int]] = MappingProxyType(
    {
        str(Intent.TASK_MANAGE): (_MIN_TITLE_CHARACTERS, MAX_TASK_TITLE_LENGTH),
        str(Intent.PROJECT_MANAGE): (_MIN_TITLE_CHARACTERS, MAX_PROJECT_NAME_LENGTH),
        str(Intent.KNOWLEDGE_CAPTURE): (_MIN_TITLE_CHARACTERS, MAX_NOTE_TITLE_LENGTH),
        str(Intent.LEARNING_TRACK): (_MIN_TITLE_CHARACTERS, _MAX_LEARNING_GOAL_TITLE_LENGTH),
    }
)

_LEADING_ARTICLE_RE = re.compile(r"\A(?:an?|the|my|our)\s+", re.IGNORECASE)
_TRAILING_FILLER_RE = re.compile(
    r"(?:\s+(?:to|for|about|on|of|by|that|saying|with|and|in|please|"
    r"targeting|due|starting|beginning|ending|scheduled))+$",
    re.IGNORECASE,
)
_PUNCTUATION_EDGES_RE = re.compile(
    r"^[\s\"'\u201c\u201d\u2018\u2019(\[]+|[\s\"'\u201c\u201d\u2018\u2019)\].,;:!?]+$"
)

_NORMALISE_RE = re.compile(r"[^a-z0-9\s]+")
_WHITESPACE_RE = re.compile(r"\s+")


# --------------------------------------------------------------------------- #
# Value objects
# --------------------------------------------------------------------------- #


class ExtractedVerb:
    """What the deterministic layer makes of the request's verb.

    Deliberately **not** an :class:`~ml.datasets.taxonomy.Intent` member — verb is
    not one of the fourteen classes, which is the entire reason this module
    exists. These four strings are the closed set the text itself can justify.
    """

    CREATE = "create"
    COMPLETE = "complete"
    DESTRUCTIVE = "destructive"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Argument:
    """One extracted field, and where it came from.

    ``matched_text`` is the span consumed from the utterance and ``rule`` names
    the rule that consumed it, so a confirm dialog can show the user *why* the
    proposal says what it says rather than asking them to trust it. An
    :class:`Argument` with an empty ``matched_text`` is a defect in this module:
    every field here is extracted from the text by some rule.

    ``value`` is always a string, because these reach a response the frontend
    renders; the typed value lives on :class:`Extraction`.
    """

    field: str
    value: str
    matched_text: str
    rule: str

    def to_dict(self) -> dict[str, str]:
        """The argument as a JSON-ready mapping."""
        return {
            "field": self.field,
            "value": self.value,
            "matched_text": self.matched_text,
            "rule": self.rule,
        }


@dataclass(frozen=True, slots=True)
class Extraction:
    """Everything one utterance yielded, plus the reasons for what it did not.

    ``title`` is ``None`` when nothing confident was recoverable and ``reason``
    then says why in words meant for the user. ``notes`` are the softer
    observations — a date phrase recognised but not resolved, a title clipped to
    the schema bound — which belong on the proposal, so the user hears about them
    rather than discovering them after confirming.
    """

    intent: str
    verb: str
    title: str | None
    arguments: tuple[Argument, ...] = ()
    notes: tuple[str, ...] = ()
    priority: str | None = None
    due_date: date | None = None
    due_at: datetime | None = None
    confidence: float = 0.0
    reason: str | None = None
    text: str = field(default="", repr=False)

    @property
    def usable(self) -> bool:
        """Whether a proposal can be built from this extraction."""
        return self.title is not None and self.reason is None


@dataclass(frozen=True, slots=True)
class TaskCandidate:
    """A task the caller is willing to be talked into completing.

    Supplied by the caller rather than looked up here: resolving a title to a row
    is an owner-scoped database read, and this module stays free of the ORM.
    """

    id: UUID
    title: str


@dataclass(frozen=True, slots=True)
class TaskMatch:
    """One candidate, named unambiguously by the utterance."""

    candidate: TaskCandidate
    rule: str
    matched_text: str


@dataclass(frozen=True, slots=True)
class TaskMatchFailure:
    """No candidate, or more than one, and which of those happened.

    ``reason_code`` is ``"not_found"`` or ``"ambiguous"``. The two are kept apart
    because they are different conversations with the user: one means "which task
    did you mean?", the other means "NEXUS found several and will not pick".
    """

    reason_code: str
    reason: str


# --------------------------------------------------------------------------- #
# Small text helpers
# --------------------------------------------------------------------------- #


def _collapse(text: str) -> str:
    """Collapse whitespace runs and detach punctuation from the words before it.

    Cutting a phrase out of the middle of a sentence leaves holes — ``"a  task
    to"`` with two spaces, or ``"the plan , on"`` with a floating comma. Both look
    like corruption in a confirm dialog, so they are repaired once here rather
    than in every caller.
    """
    repaired = _WHITESPACE_RE.sub(" ", text)
    repaired = re.sub(r"\s+([,.;:!?])", r"\1", repaired)
    return repaired.strip()


def _normalise(text: str) -> str:
    """Lower-case, drop punctuation, collapse whitespace.

    The comparison form used by :func:`match_task_reference`. Case and punctuation
    are noise for matching and load-bearing for display, which is why the title a
    user reads is never normalised.
    """
    return _WHITESPACE_RE.sub(" ", _NORMALISE_RE.sub(" ", text.lower())).strip()


def strip_command_prefix(text: str, prefixes: Sequence[str] = _CREATE_PREFIXES) -> str:
    """Strip leading command phrases until none of them matches.

    Args:
        text: The utterance, already trimmed.
        prefixes: Candidate phrases. Longest first, which is how the callers'
            tables are sorted: "remind me to" is considered before "remind".

    Returns:
        What remains. Matching is anchored at the start, case-insensitive, and
        requires a word boundary after the phrase, so ``"new"`` does not eat the
        first two letters of ``"newsletter"``.

    Notes:
        ``"make"`` and ``"new"`` are ambiguous words that happen to be command
        phrases — "make dinner" loses two letters it would rather keep. That is
        the cost of a fixed vocabulary, and it is a cost the confirm dialog
        *shows* rather than hides: the user reads the resulting title before
        anything is written.
    """
    remaining = text.strip()
    for _ in range(_MAX_PREFIX_STRIPS):
        for prefix in prefixes:
            if not remaining.lower().startswith(prefix):
                continue
            tail = remaining[len(prefix) :]
            if tail and not tail[0].isspace():
                continue
            remaining = tail.strip()
            break
        else:
            break
    return remaining


def _cut(text: str, start: int, end: int) -> str:
    """Remove ``text[start:end]``, repaired with :func:`_collapse`."""
    return _collapse(f"{text[:start]} {text[end:]}")


# --------------------------------------------------------------------------- #
# Verb
# --------------------------------------------------------------------------- #


def extract_verb(text: str) -> str:
    """Decide what the utterance *asks to be done*, from the text alone.

    Ordered by how much damage getting it wrong does:

    1. A destruction verb anywhere wins over everything. ``"delete that
       duplicate reminder"`` and ``"delete that task I created"`` are both
       ``task_manage``, and the first must never be answered with a creation.
    2. A completion marker — a leading ``mark``/``complete``/``finish``, or a
       trailing ``as done`` — means complete. The verbs count only in *leading*
       position, because ``"finish"`` inside a title is ordinary English.
    3. A creation prefix at the start means create.
    4. Both 2 and 3 mean **unknown**, not a guess. "add a task and mark the
       migration one done" is two requests in one sentence, and picking either
       one is picking wrong.

    Returns:
        One of the :class:`ExtractedVerb` values.
    """
    lowered = text.lower()
    if any(phrase in lowered for phrase in _DESTRUCTIVE_PHRASES):
        return ExtractedVerb.DESTRUCTIVE

    bare = strip_command_prefix(text, _POLITENESS_PREFIXES)
    completes = any(bare.lower().startswith(prefix) for prefix in _COMPLETE_PREFIXES) or bool(
        _COMPLETE_MARKER_RE.search(text)
    )
    creates = strip_command_prefix(bare, _CREATE_PREFIXES) != bare

    if completes and creates:
        return ExtractedVerb.UNKNOWN
    if completes:
        return ExtractedVerb.COMPLETE
    if creates:
        return ExtractedVerb.CREATE
    return ExtractedVerb.UNKNOWN


# --------------------------------------------------------------------------- #
# Priority
# --------------------------------------------------------------------------- #


def _find_priority(text: str) -> tuple[str, str, int, int] | None:
    """Locate the earliest priority phrase in ``text``.

    Returns:
        ``(value, matched_text, start, end)``, or ``None``.
    """
    best: tuple[str, str, int, int] | None = None
    for pattern, value in _PRIORITY_RES:
        match = pattern.search(text)
        if match is None:
            continue
        start, end = match.span()
        if best is None or (start, -(end - start)) < (best[2], -(best[3] - best[2])):
            best = (value, match.group(0), start, end)
    return best


def _remove_priority(text: str) -> tuple[str, str | None, str | None]:
    """Remove the priority phrase and its punctuation, keeping the phrase text.

    Returns:
        ``(remaining_text, value, matched_text)``.
    """
    found = _find_priority(text)
    if found is None:
        return text, None, None
    value, matched, start, end = found

    lead = _PRIORITY_LEAD_RE.search(text[:start])
    if lead is not None and lead.end() > 0:
        start = lead.start()
    tail = _PRIORITY_TRAIL_RE.match(text[end:])
    if tail is not None:
        end = tail.end()

    return _cut(text, start, end), value, matched


# --------------------------------------------------------------------------- #
# Dates
# --------------------------------------------------------------------------- #


def local_day(instant: datetime, tz: ZoneInfo) -> date:
    """The calendar day ``instant`` falls on in ``tz``.

    Mirrors :func:`app.services.planner_service.local_day`, including its rule that
    a naive instant is read as UTC — the same fallback that stops an operator's
    half-finished debug call from raising instead of answering.
    """
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
    return instant.astimezone(tz).date()


def day_start_utc(day: date, tz: ZoneInfo) -> datetime:
    """``day``'s local midnight in ``tz``, as an offset-aware UTC instant.

    The start of the half-open window the planner uses — see
    :func:`app.services.planner_service.day_bounds`, which this mirrors rather
    than imports. *Combining* the local wall clock with the zone and converting
    once is what makes the DST days come out right: a zone east of Greenwich has
    a local midnight on the previous UTC day, and a spring-forward day is 23
    hours long.
    """
    return datetime.combine(day, time.min, tzinfo=tz).astimezone(UTC)


def _next_weekday(today: date, weekday: int) -> date:
    """The next occurrence of ``weekday`` strictly after ``today``."""
    return today + timedelta(days=(weekday - today.weekday()) % 7 or 7)


def resolve_weekday(weekday: int, today: date, *, qualifier: str | None = None) -> date | None:
    """Resolve a named weekday, or ``None`` when the reading is ambiguous.

    Three readings, and the third is why this returns an optional:

    * ``"next monday"`` — the Monday of the *following* ISO week, which stays
      unambiguous even when today is Monday.
    * ``"this friday"`` — this week's Friday, today when today is Friday.
    * bare ``"friday"`` — the next occurrence strictly after today. When today
      **is** that weekday the utterance has two honest readings, today or in
      seven days, and this returns ``None`` rather than choosing one.

    Args:
        weekday: ISO weekday, 0 for Monday.
        today: The reference day, already in the caller's zone.
        qualifier: ``"next"``, ``"this"`` or ``None`` for a bare mention.

    Returns:
        The resolved date, or ``None`` when the reading is ambiguous.
    """
    if qualifier == "next":
        start_of_next_week = today - timedelta(days=today.weekday()) + timedelta(days=7)
        return start_of_next_week + timedelta(days=weekday)
    if qualifier == "this":
        return today if today.weekday() == weekday else _next_weekday(today, weekday)
    if today.weekday() == weekday:
        return None
    return _next_weekday(today, weekday)


@dataclass(frozen=True, slots=True)
class DateMatch:
    """A date-shaped span found in the utterance, resolved or not."""

    matched_text: str
    start: int
    end: int
    value: date | None
    unresolved: str | None = None


def find_date(text: str, today: date) -> DateMatch | None:
    """Find and resolve the earliest date-shaped span in ``text``.

    All three families are searched — ISO, weekday, keyword — and the earliest
    match wins, so "draft the 2026-04-01 plan for friday" resolves to the ISO date
    the user put first.

    Args:
        text: The text still being reduced to a subject.
        today: The reference day in the caller's zone.

    Returns:
        The earliest :class:`DateMatch`, whose ``value`` is ``None`` when the
        phrase is unresolvable and whose ``unresolved`` sentence then says which
        failure it was, or ``None`` when the text contains no date at all.
    """
    candidates: list[DateMatch] = []

    for match in _ISO_DATE_RE.finditer(text):
        raw = match.group(0)
        try:
            resolved = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            candidates.append(
                DateMatch(
                    matched_text=raw,
                    start=match.start(),
                    end=match.end(),
                    value=None,
                    unresolved=f"'{raw}' is not a calendar date.",
                )
            )
            continue
        candidates.append(
            DateMatch(matched_text=raw, start=match.start(), end=match.end(), value=resolved)
        )

    for match in _WEEKDAY_RE.finditer(text):
        raw = match.group(0)
        lowered = raw.lower()
        weekday = _WEEKDAY_OFFSETS.get(match.group(1).lower() + "day")
        if weekday is None:
            continue
        qualifier = "next" if "next" in lowered else ("this" if "this" in lowered else None)
        resolved = resolve_weekday(weekday, today, qualifier=qualifier)
        candidates.append(
            DateMatch(
                matched_text=raw,
                start=match.start(),
                end=match.end(),
                value=resolved,
                unresolved=(
                    None
                    if resolved is not None
                    else f"'{raw.strip()}' could mean today or a week away, so no date was chosen."
                ),
            )
        )

    for match in _KEYWORD_DATE_RE.finditer(text):
        offset = _KEYWORD_DATES[match.group(1).lower()]
        candidates.append(
            DateMatch(
                matched_text=match.group(0),
                start=match.start(),
                end=match.end(),
                value=today + timedelta(days=offset),
            )
        )

    if not candidates:
        return None
    return min(candidates, key=lambda match: (match.start, -len(match.matched_text)))


def _remove_date(text: str, today: date) -> tuple[str, DateMatch | None]:
    """Remove the earliest date phrase, resolved or not."""
    match = find_date(text, today)
    if match is None:
        return text, None
    return _cut(text, match.start, match.end), match


# --------------------------------------------------------------------------- #
# Title
# --------------------------------------------------------------------------- #


def _trim_title_edges(text: str) -> str:
    """Drop the leading article, the trailing connector and stray punctuation."""
    trimmed = _collapse(text)
    for _ in range(2):
        stripped = _PUNCTUATION_EDGES_RE.sub("", _LEADING_ARTICLE_RE.sub("", trimmed))
        stripped = _TRAILING_FILLER_RE.sub("", stripped)
        if stripped == trimmed:
            break
        trimmed = stripped
    return _PUNCTUATION_EDGES_RE.sub("", trimmed).strip()


def extract_title(text: str, *, intent: str) -> tuple[str | None, str | None, list[str], list[str]]:
    """Recover the subject of the request from what the other rules leave behind.

    Args:
        text: The utterance with its command phrase already stripped.
        intent: The predicted intent, which chooses the entity nouns to strip.

    Returns:
        ``(title, reason, removed, notes)``. ``title`` is ``None`` — with
        ``reason`` populated — when nothing confident survives. ``removed`` lists
        every span cut along the way, and becomes the provenance rule on the
        title's :class:`Argument`.
    """
    removed: list[str] = []
    notes: list[str] = []
    remaining = _collapse(text)

    entity_pattern = _ENTITY_PREFIX_RES.get(intent)
    if entity_pattern is not None:
        match = entity_pattern.match(remaining)
        if match is not None and match.end() > 0:
            removed.append(match.group(0).strip())
            remaining = remaining[match.end() :]

    trailing = _TRAILING_ENTITY_RE.search(remaining)
    if trailing is not None:
        removed.append(trailing.group(0).strip())
        remaining = remaining[: trailing.start()]

    title = _trim_title_edges(remaining)
    minimum, maximum = _TITLE_BOUNDS.get(intent, (_MIN_TITLE_CHARACTERS, MAX_TASK_TITLE_LENGTH))

    if len(title) < minimum or title.lower() in _FILLER_TITLES:
        return (
            None,
            "NEXUS could not tell what this request is about: after removing the "
            "command phrase there was nothing left to name. Ask in one line — "
            "'add a task to <what>'.",
            removed,
            notes,
        )

    if len(title) > maximum:
        clipped = title[:maximum].rsplit(" ", 1)[0].strip()
        notes.append(
            f"The subject is {len(title)} characters, so it was clipped to the first "
            f"{len(clipped)} to fit the field it is written to."
        )
        title = clipped
    return title, None, removed, notes


# --------------------------------------------------------------------------- #
# Task reference matching
# --------------------------------------------------------------------------- #


def match_task_reference(
    fragment: str, candidates: Sequence[TaskCandidate]
) -> TaskMatch | TaskMatchFailure:
    """Match a spoken fragment to exactly one of the caller's own tasks.

    Three deterministic passes, strictest first: exact equality of the normalised
    strings, containment in either direction, then every significant word of the
    fragment appearing in the candidate's title. The first pass yielding
    **exactly one** candidate wins.

    Args:
        fragment: The subject recovered from a completion request.
        candidates: The tasks the caller is willing to have completed.

    Returns:
        A :class:`TaskMatch`, or a :class:`TaskMatchFailure` whose reason code is
        ``"not_found"`` or ``"ambiguous"``. Both are refusals: **this function
        never returns a second-best match**, because completing the wrong task is
        a write the user has to notice and undo.
    """
    needle = _normalise(fragment)
    if not needle:
        return TaskMatchFailure(
            reason_code="not_found",
            reason="The request did not name a task, so there is nothing to complete.",
        )

    normalised = [(candidate, _normalise(candidate.title)) for candidate in candidates]

    exact = [pair for pair in normalised if pair[1] == needle]
    if len(exact) == 1:
        return TaskMatch(exact[0][0], "exact title match", fragment)
    if len(exact) > 1:
        return TaskMatchFailure(
            reason_code="ambiguous",
            reason=(
                f"{len(exact)} tasks are titled '{fragment}'; NEXUS will not pick between them."
            ),
        )

    contained = [pair for pair in normalised if needle in pair[1] or pair[1] in needle]
    if len(contained) == 1:
        return TaskMatch(contained[0][0], "the named phrase appears in exactly one title", fragment)
    if len(contained) > 1:
        return TaskMatchFailure(
            reason_code="ambiguous", reason=_ambiguous_reason(fragment, contained)
        )

    words = [word for word in needle.split() if len(word) > 2]
    if words:
        hits = [pair for pair in normalised if all(word in pair[1].split() for word in words)]
        if len(hits) == 1:
            return TaskMatch(hits[0][0], "every word of the named phrase is in one title", fragment)
        if len(hits) > 1:
            return TaskMatchFailure(
                reason_code="ambiguous", reason=_ambiguous_reason(fragment, hits)
            )

    return TaskMatchFailure(
        reason_code="not_found",
        reason=(
            f"No open task matches '{fragment}'. NEXUS will not complete a task it "
            "would have to guess at."
        ),
    )


def _ambiguous_reason(fragment: str, pairs: Sequence[tuple[TaskCandidate, str]]) -> str:
    """The refusal text for a fragment that matched more than one task."""
    titles = ", ".join(f"'{pair[0].title}'" for pair in pairs)
    return (
        f"'{fragment}' matches {len(pairs)} tasks ({titles}); NEXUS will not guess "
        "which one to complete."
    )


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


def extract_arguments(
    text: str,
    prediction: IntentPrediction,
    *,
    tz: ZoneInfo,
    now: datetime | None = None,
) -> Extraction:
    """Turn one utterance into arguments, deterministically, given its intent.

    The order is load-bearing and follows the cost of being wrong: the verb
    first, because a destructive request must stop everything; then priority and
    the date, because both are cut *out* of the text and the subject is what is
    left when they have been.

    Args:
        text: The raw utterance, as the classifier saw it.
        prediction: The classifier's output. Only ``intent`` and ``confidence``
            are read; this function never re-decides what the utterance is about.
        tz: The caller's IANA zone. Dates resolve against it, and the resulting
            instant is converted to UTC through it.
        now: The reference instant, defaulting to :func:`datetime.now`. A naive
            value is read as UTC.

    Returns:
        An :class:`Extraction`: either a title with its provenance, or a
        ``reason`` saying why there is none.
    """
    intent = prediction.intent
    confidence = float(prediction.confidence)
    today = local_day(now or datetime.now(UTC), tz)
    verb = extract_verb(text)

    if intent not in SUPPORTED_INTENTS:
        return Extraction(
            intent=intent,
            verb=verb,
            title=None,
            reason=(
                f"NEXUS cannot build an action from '{intent}': that class names a "
                "surface to look at, not something to write."
            ),
            confidence=confidence,
            text=text,
        )

    if verb == ExtractedVerb.DESTRUCTIVE:
        return Extraction(
            intent=intent,
            verb=verb,
            title=None,
            reason=(
                "The request uses a destruction verb. NEXUS runs one classifier, and "
                "it cannot tell 'add a task' from 'delete everything' — both are "
                "task_manage — so a destructive request is never turned into an "
                "action. Archive or cancel the row yourself if that is what you "
                "meant."
            ),
            confidence=confidence,
            text=text,
        )

    arguments: list[Argument] = []
    notes: list[str] = []
    priority: str | None = None
    due_date: date | None = None
    due_at: datetime | None = None

    if verb == ExtractedVerb.CREATE:
        remaining = strip_command_prefix(text, (*_POLITENESS_PREFIXES, *_CREATE_PREFIXES))
    else:
        remaining = strip_command_prefix(text, (*_POLITENESS_PREFIXES, *_COMPLETE_PREFIXES))
        remaining = _remove_completion_marker(remaining)

    remaining, priority, priority_text = _remove_priority(remaining)
    if priority is not None:
        arguments.append(
            Argument(
                field="priority",
                value=priority,
                matched_text=priority_text or priority,
                rule=f"priority phrase '{priority_text}' maps to {priority}",
            )
        )

    remaining, date_match = _remove_date(remaining, today)
    if date_match is not None:
        if date_match.value is not None:
            due_date = date_match.value
            due_at = day_start_utc(due_date, tz)
            arguments.append(
                Argument(
                    field="due_date",
                    value=due_date.isoformat(),
                    matched_text=date_match.matched_text,
                    rule=(f"date phrase '{date_match.matched_text}' resolved against {tz.key}"),
                )
            )
            arguments.append(
                Argument(
                    field="due_at",
                    value=due_at.isoformat(),
                    matched_text=date_match.matched_text,
                    rule=(
                        f"local midnight on {due_date.isoformat()} in {tz.key} "
                        "converted to UTC, the instant the planner cuts a day on"
                    ),
                )
            )
        else:
            notes.append(f"No date was set: {date_match.unresolved} NEXUS does not guess a date.")

    title, reason, removed, title_notes = extract_title(remaining, intent=intent)
    notes.extend(title_notes)
    notes.extend(_unresolved_date_notes(remaining))
    if title is not None:
        arguments.append(
            Argument(
                field="title",
                value=title,
                matched_text=text,
                rule="the utterance with "
                + (", ".join(repr(span) for span in removed) if removed else "nothing")
                + " removed",
            )
        )

    return Extraction(
        intent=intent,
        verb=verb,
        title=title,
        arguments=tuple(arguments),
        notes=tuple(notes),
        priority=priority,
        due_date=due_date,
        due_at=due_at,
        confidence=confidence,
        reason=reason,
        text=text,
    )


def _remove_completion_marker(text: str) -> str:
    """Cut a trailing completion marker out of a reference fragment.

    "mark the API contract task as done" names the task; "as done" is how it was
    phrased. Left in, the fragment stops matching the row it refers to.
    """
    marker = _COMPLETE_MARKER_RE.search(text)
    if marker is not None:
        return _cut(text, marker.start(), marker.end())
    trimmed = text.rstrip(" .")
    if trimmed.lower().endswith("done"):
        return _collapse(trimmed[: -len("done")])
    return text


def _unresolved_date_notes(text: str) -> list[str]:
    """Notes for date-shaped wording this module deliberately does not resolve."""
    lowered = text.lower()
    return [
        f"No date was set: '{marker}' does not name a day, and NEXUS does not guess one."
        for marker in _UNRESOLVED_DATE_MARKERS
        if marker in lowered
    ]
