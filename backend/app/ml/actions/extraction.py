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

**Every write in the app is expressible, and none of them is safe by accident.**
The verb set is no longer "create or complete": it covers creation, editing,
status transitions, scheduling, tagging, logging, archiving, publishing and
**deletion**, across tasks, projects, knowledge, learning and the planner. A
destruction verb used to end the extraction here, with a refusal explaining why
this layer would not do it. It does not any more, because that refusal was
safety in the wrong place. This layer reads text and can keep nothing: the thing
it produces is a proposal, the proposal is not a write, the confirm route
re-resolves every id through an owner-scoped service before calling anything, and
a delete names **one** row rather than a collection. What this layer still
refuses is everything it would have to *guess* — a bulk request, a reference
matching no row or several, a subject it cannot recover. Those refusals are about
certainty, not about danger, and that is the whole difference between a layer
that can only add and a layer that can be trusted with a delete.

**Every field this module emits carries its provenance.** Nothing is returned
bare. An :class:`Argument` names the span that produced it and the rule that
consumed it, which is what lets a confirm dialog say *"due Friday — matched 'due
friday'"* instead of asking the user to trust a number.

**An edit names two things, and only one of them is the value.** *"rename the API
task to Auth contract"* contains a reference and a new name, and an update
payload built from the whole phrase would rename the row to itself. So
:attr:`Extraction.title` is **the thing being acted on** — the reference for every
verb except a creation — and :attr:`Extraction.values` holds only the fields the
user actually stated. The connector decides which is which: the span after *to*,
*called*, *into* or a dash is the new value, the span before it is the row. A
payload is never built from a schema default, and never from a clause NEXUS had to
supply.

**A reference is not a title, and a collection is not a row.** *"make it high
priority"* names no name at all, and *"delete every task"* names every one. Both
are carried to the layer above rather than resolved here: the first as a weak
reference NEXUS keeps so the verb and the value survive, the second as
:attr:`Extraction.bulk`, which exists to be refused. The rule the refusal rests on
is the one this module can state exactly: **a delete names one row**, because one
row is a thing the user read, confirmed and can undo, and a collection is not.

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

**A filesystem path is a value, and a missing one is a refusal.** *"add repo name
xyz from path E:/op"* carries two arguments, and one of them is the only field
:class:`~app.schemas.developer.RepositoryCreate` cannot be written without: a
repository registered against no folder is a row that looks fine until the next
scan, when it cannot be read. So a path is recognised in four shapes — quoted, a
Windows drive, a POSIX absolute path, a ``~``-prefixed one — in that order,
because a quoted path is the only shape that can hold a space and is therefore
the only one that says where it ends. It is cut out of the title exactly as a
date is, because a title ending in half a path describes nothing, and it is
recorded in :attr:`Extraction.values` under ``"path"`` with the span that produced
it, so the confirm dialog can say *read from ``E:/op``*.

**And a path is never guessed.** Every other rule in this file refuses rather
than resolving — no nearest-match date, no ambiguous row, no bulk action — and a
folder is no different: NEXUS has no working directory of its own to offer, and
the server's own ``cwd`` is the one place a guess would be least defensible. So a
request naming no folder simply has no ``"path"`` key, and the layer above
answers it with a sentence telling the user what to say. The one thing supplied
on the user's behalf is the **label**, because *"register the repo E:/op"* names no
label at all — and there the folder's own final segment is the answer, since
that is what the server would default to anyway. It is recorded as a note and
shown beside the path, never in place of it.

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
    "SUPPORTED_INTENTS",
    "Argument",
    "ExtractedVerb",
    "Extraction",
    "PathMatch",
    "RowCandidate",
    "RowMatch",
    "RowMatchFailure",
    "TaskCandidate",
    "TaskMatch",
    "TaskMatchFailure",
    "day_start_utc",
    "extract_arguments",
    "extract_entity",
    "extract_title",
    "extract_verb",
    "find_date",
    "find_path",
    "local_day",
    "match_reference",
    "match_task_reference",
    "resolve_weekday",
    "strip_command_prefix",
]

#: The intents this module can pull arguments out of. Everything else has no
#: argument shape here, and :mod:`app.ml.actions.proposals` refuses it with an
#: explicit reason rather than trying.
#:
#: ``knowledge_lookup`` is in the set even though it is the *read* half of the
#: knowledge router, because the user says the same sentence either way: "find
#: the note where I wrote down the migration plan" and "archive the note where I
#: wrote down the migration plan" are the same noun phrase, and only the verb
#: tells them apart. Refusing the lookup class would refuse half the phrasings of
#: every write it does support.
SUPPORTED_INTENTS: frozenset[str] = frozenset(
    {
        str(Intent.TASK_MANAGE),
        str(Intent.PROJECT_MANAGE),
        str(Intent.SCHEDULE_PLAN),
        str(Intent.KNOWLEDGE_CAPTURE),
        str(Intent.KNOWLEDGE_LOOKUP),
        str(Intent.LEARNING_TRACK),
        str(Intent.ACCOUNT_ADMIN),
        str(Intent.DEVELOPER_INTEL),
    }
)

#: ``LearningGoalWrite.title`` declares ``max_length=200`` inline rather than
#: through a module constant, so it is repeated here with the field it mirrors.
#: ``RepositoryCreate.name`` declares its bound the same way, for the same reason.
#: Every bound below is the *schema's* bound, not a number chosen here: a title
#: clipped to it always validates against the payload it is written to.
_MAX_LEARNING_GOAL_TITLE_LENGTH = 200
_MAX_REPOSITORY_NAME_LENGTH = 200

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
# The closed verb set
# --------------------------------------------------------------------------- #


class ExtractedVerb:
    """What the deterministic layer makes of the request's verb.

    Deliberately **not** an :class:`~ml.datasets.taxonomy.Intent` member — verb is
    not one of the fourteen classes, which is the entire reason this module
    exists. These strings are the closed set the text itself can justify, and the
    set is deliberately wider than the app's write vocabulary: a verb with no
    proposal kind behind it costs a refusal here, while a missing verb costs a
    request the user cannot express at all.

    Declared before the vocabulary tables below because those tables are keyed by
    these members, and a table of string literals next to a class of constants is
    a place for the two to drift.
    """

    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    COMPLETE = "complete"
    STATUS = "status"
    ARCHIVE = "archive"
    PUBLISH = "publish"
    SCHEDULE = "schedule"
    UNSCHEDULE = "unschedule"
    TAG = "tag"
    UNTAG = "untag"
    LOG = "log"
    UNKNOWN = "unknown"


def _phrase_pattern(phrase: str) -> str:
    r"""Compile one vocabulary phrase, tolerating a hyphen between its words.

    Used for every table in this module that is written as words rather than as a
    regex — "high priority" and "work session" and "to-do" alike — so a user who
    types "work-session" and a user who types "work session" hit the same rule.
    Kept above the tables that call it, because three of them are built from it.

    The un-escape-then-re-escape round trip is because :func:`re.escape` also
    escapes the space (as ``\ ``), and replacing the space directly would have
    left the stray backslash behind — turning ``[`` into an escaped literal
    bracket and silently matching nothing.
    """
    spaced = re.escape(phrase).replace("\\ ", " ")
    return rf"\b{spaced.replace(' ', r'[-\s]+')}\b"


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

#: Verbs whose only plausible reading is destruction. ``remove`` is here because
#: NEXUS can now carry a delete, and the ambiguity that kept it out — "remove the
#: priority" is an edit — is resolved by :data:`_REMOVE_A_ROW_RE` below, which
#: reads it as a deletion only when what follows it is not a field. ``clear`` is
#: only a deletion when a determiner and a scope follow it, which is what
#: separates "clear all my tasks" from "clear the due date".
_DELETE_PHRASES: tuple[str, ...] = (
    "delete",
    "remove",
    "erase",
    "wipe",
    "purge",
    "nuke",
    "destroy",
    "drop",
    "get rid of",
    "throw away",
)

#: ``remove`` naming a **field** is an edit, not a deletion: there is nothing to
#: remove but a value, and the row the user cares about stays exactly where it
#: was. Read before the delete rule for the same reason the untag rule is — the
#: narrower reading is the one that cannot destroy anything.
_REMOVE_A_ROW_RE = re.compile(
    r"\b(?:remove|clear|drop)\b(?!\s+(?:the\s+|its\s+|their\s+|my\s+|our\s+)?"
    r"(?:priority|tags?|labels?|due\s+date|deadline|dates?|description|content|"
    r"summary|details?|title|name|status|assignee|estimate|project|link|url|"
    r"from\s+the)\b)",
    re.IGNORECASE,
)

_DELETE_WORD_RE = re.compile(
    r"\b(?:"
    + "|".join(re.escape(phrase) for phrase in _DELETE_PHRASES if phrase != "remove")
    + r")\b"
    r"|\bclear\s+(?:all|out|my\s+board|those|these|the\s+whole)\b",
    re.IGNORECASE,
)

#: Both deletion readings in one pattern, for the one place that needs to ask
#: "where in this sentence is the deletion?" rather than "is this one?".
_DELETE_RE = re.compile(f"{_DELETE_WORD_RE.pattern}|{_REMOVE_A_ROW_RE.pattern}", re.IGNORECASE)

#: Removing a *label* is not removing the row. Matched before :data:`_DELETE_RE`
#: because every phrase here contains one of its words, and the shorter match is
#: the honest reading: "remove the tag urgent from the API task" is a write to
#: the task's labels and a no-op on the task.
_UNTAG_RE = re.compile(
    r"\b(?:un\s?tag\w*|remove\s+(?:the\s+|its\s+|their\s+)?tags?|"
    r"drop\s+(?:the\s+|its\s+)?tags?|take\s+(?:the\s+|its\s+)?tags?\s+off)\b",
    re.IGNORECASE,
)

_TAG_RE = re.compile(
    r"\b(?:tag\w*|labell?ed)\b|\badd\s+(?:the\s+)?tags?\b|\bwith\s+the\s+tag\b",
    re.IGNORECASE,
)

_ARCHIVE_RE = re.compile(r"\barchiv\w*", re.IGNORECASE)
_PUBLISH_RE = re.compile(r"\b(?:publish\w*|unpublish\w*)", re.IGNORECASE)
_UNSCHEDULE_RE = re.compile(
    r"\bunschedul\w*|\bunblock\s+(?:the\s+|my\s+|that\s+)?(?:time|slot|block|calendar)\b"
    r"|\bfree\s+(?:up\s+)?(?:the\s+|my\s+|that\s+)?(?:slot|block|time|calendar)\b"
    r"|\bclear\s+(?:the\s+|my\s+)?(?:slot|block|entry|meeting)\b"
    r"|\btake\s+(?:it|that|this|them)\s+off\s+(?:my\s+|the\s+)?(?:calendar|schedule|planner)\b",
    re.IGNORECASE,
)
_SCHEDULE_RE = re.compile(
    r"\b(?:reschedul\w*|schedul\w*|book\w*|postpon\w*|defer\w*|slot\s+it\s+in)\b"
    r"|\bpush\b[^.?!]{0,40}?\b(?:out|back|off)\b"
    r"|\bpush\s+(?:it|that|this|them)\b"
    r"|\bmove\b"
    r"|\bblocks?\s+(?:out\s+)?(?:\d|an?\b|one\b|two\b|three\b|half)"
    r"|\bblocks?\s+(?:time\s+)?(?:for|on)\b",
    re.IGNORECASE,
)
_LOG_RE = re.compile(r"\blog\b|\btrack\b|\brecord\s+a\b", re.IGNORECASE)

#: "block" and "open" are the two words that most need the context around them.
#: "block the API contract task" is a transition; "block two hours on Friday" is
#: a time block. Same for a bare "set", which is an edit verb but also the
#: ordinary word in "set up a task" — hence the negative lookahead.
_STATUS_RE = re.compile(
    r"\b(?:start\w*|begin\w*|block\w*|unblock\w*|cancel\w*|reopen\w*|re-?open\w*|"
    r"undone\w*|paus\w*|hold|resume\w*|reactivat\w*|activat\w*)\b"
    r"|\bset\b(?!\s+up\b)[^.?!]{0,80}?\bto\s+(?:be\s+)?"
    r"(?:done|complete|completed|in[\s-]progress|blocked|cancelled|canceled|"
    r"todo|open|reopened|archived|active|on[\s-]hold|paused)\b",
    re.IGNORECASE,
)
_UPDATE_RE = re.compile(
    r"\b(?:rename\w*|chang\w*|updat\w*|edit\w*|modif\w*|adjust\w*|set\b(?!\s+up\b)|"
    r"clear\w*\b(?!\s+(?:all|out)\b)|"
    r"make\s+(?:it|this|that|them|these|those))\b",
    re.IGNORECASE,
)

#: Every verb rule, in the order :func:`extract_verb` applies them. The order is
#: the argument, so it is written out rather than derived from a dict: a rule that
#: matches first wins even when a later rule also matches, and the pairs that can
#: both match ("remove the tag", "block the time", "make a task") are exactly the
#: ones whose relative order is a decision rather than an accident.
#:
#: 1. ``untag`` and ``tag`` — before ``delete``, because every phrase that reads as
#:    a tag operation also contains a word the delete rule matches, and the
#:    narrower reading is the honest one.
#: 2. ``delete`` — anywhere. "delete that task I created" and "I want to delete"
#:    are both ``task_manage``, and the destruction verb must never be answered
#:    with a creation.
#: 3. the creation and completion families, which keep their own rules: leading
#:    position only, and **both in one sentence is unknown, not a guess**.
#: 4. the remaining single-word verbs, then the two context-sensitive ones.
#: 5. ``status`` before ``update``, so "set the task to completed" is the
#:    transition it plainly is rather than a rename with a status word in it.
_VERB_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (_UNTAG_RE, ExtractedVerb.UNTAG),
    (_TAG_RE, ExtractedVerb.TAG),
    (_REMOVE_A_ROW_RE, ExtractedVerb.DELETE),
    (_DELETE_WORD_RE, ExtractedVerb.DELETE),
)
_VERB_RULES_TAIL: tuple[tuple[re.Pattern[str], str], ...] = (
    (_ARCHIVE_RE, ExtractedVerb.ARCHIVE),
    (_PUBLISH_RE, ExtractedVerb.PUBLISH),
    (_UNSCHEDULE_RE, ExtractedVerb.UNSCHEDULE),
    (_SCHEDULE_RE, ExtractedVerb.SCHEDULE),
    (_LOG_RE, ExtractedVerb.LOG),
    (_STATUS_RE, ExtractedVerb.STATUS),
    (_UPDATE_RE, ExtractedVerb.UPDATE),
)

#: The leading words each verb family may be written with, so the phrase can be
#: cut out of the text before the subject is recovered. Read at the start only,
#: for the reason the completion prefixes are: "hold" is an ordinary English word
#: and "plan" appears inside titles more often than it introduces one.
_VERB_LEADING_WORDS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        ExtractedVerb.CREATE: tuple(_CREATE_PREFIXES),
        ExtractedVerb.COMPLETE: tuple(_COMPLETE_PREFIXES),
        ExtractedVerb.UPDATE: (
            "rename",
            "change",
            "update",
            "edit",
            "modify",
            "adjust",
            "set",
            "clear",
            "make",
        ),
        ExtractedVerb.DELETE: _DELETE_PHRASES,
        ExtractedVerb.ARCHIVE: ("archive",),
        ExtractedVerb.PUBLISH: ("publish", "unpublish"),
        ExtractedVerb.SCHEDULE: (
            "schedule",
            "reschedule",
            "book",
            "move",
            "block",
            "push",
            "postpone",
            "plan",
        ),
        ExtractedVerb.UNSCHEDULE: ("unschedule", "unblock", "free", "clear", "take"),
        ExtractedVerb.TAG: ("tag", "label", "add the tag", "add a tag"),
        ExtractedVerb.UNTAG: ("untag", "remove the tag", "remove a tag", "remove tags"),
        ExtractedVerb.LOG: ("log", "track", "record"),
        ExtractedVerb.STATUS: (
            "start",
            "begin",
            "block",
            "unblock",
            "cancel",
            "reopen",
            "re-open",
            "pause",
            "hold",
            "put",
            "resume",
            "activate",
            "set",
        ),
        ExtractedVerb.UNKNOWN: (),
    }
)

#: The status a transition verb *is*, when the verb is the only evidence there
#: is: "block the API contract task" strips to a bare reference and leaves no
#: status word behind. These are readings, not guesses — each is the state the
#: word names in every other context it appears in — and ``unblock`` reads as
#: ``in_progress`` rather than ``todo`` because clearing a block says work may
#: resume, not that it has not started.
_VERB_STATUS: Mapping[str, str] = MappingProxyType(
    {
        "start": "in_progress",
        "begin": "in_progress",
        "resume": "in_progress",
        "unblock": "in_progress",
        "reactivate": "active",
        "activate": "active",
        "block": "blocked",
        "cancel": "cancelled",
        "pause": "on_hold",
        "hold": "on_hold",
        "reopen": "todo",
        "re-open": "todo",
    }
)

#: Every status word a user might say, mapped to the stored value. Matched
#: longest-first, because "back to open" contains "open" and "not done" contains
#: "done": the longer phrase is the instruction and the shorter one is what it
#: corrects.
_STATUS_WORDS: tuple[tuple[str, str], ...] = tuple(
    sorted(
        (
            ("back to open", "todo"),
            ("back to the backlog", "todo"),
            ("back to todo", "todo"),
            ("move it back", "todo"),
            ("mark as not done", "todo"),
            ("not done", "todo"),
            ("in progress", "in_progress"),
            ("in-progress", "in_progress"),
            ("on hold", "on_hold"),
            ("as done", "completed"),
            ("as complete", "completed"),
            ("as completed", "completed"),
            ("as finished", "completed"),
            ("is done", "completed"),
            ("done", "completed"),
            ("complete", "completed"),
            ("completed", "completed"),
            ("finish", "completed"),
            ("finished", "completed"),
            ("closed", "completed"),
            ("blocked", "blocked"),
            ("block it", "blocked"),
            ("cancelled", "cancelled"),
            ("canceled", "cancelled"),
            ("cancel", "cancelled"),
            ("reopened", "todo"),
            ("reopen", "todo"),
            ("undone", "todo"),
            ("undo", "todo"),
            ("archived", "archived"),
            ("archive", "archived"),
            ("active", "active"),
            ("activated", "active"),
            ("paused", "on_hold"),
            ("pause", "on_hold"),
            ("todo", "todo"),
            ("started", "in_progress"),
            ("start", "in_progress"),
            ("begin", "in_progress"),
            ("began", "in_progress"),
            ("resumed", "in_progress"),
            ("unblocked", "in_progress"),
        ),
        key=lambda pair: len(pair[0]),
        reverse=True,
    )
)
_STATUS_RES: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(rf"\b{re.escape(phrase)}\b", re.IGNORECASE), value)
    for phrase, value in _STATUS_WORDS
)

#: Whole-sentence scope. "delete every task", "delete everything", "clear all".
#: Recorded rather than acted on: the proposal layer turns this into a refusal,
#: because a collection is never deleted from one sentence. Any of these words
#: turns a one-row request into a scope the layer will not act on from a phrase.
_BULK_RE = re.compile(
    r"\b(?:all|every|everything|the\s+whole|both|entire)\b"
    r"|\b(?:in\s+bulk)\b"
    r"|\bclear\s+(?:all|out|everything|my\s+board)\b",
    re.IGNORECASE,
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
        str(Intent.SCHEDULE_PLAN): (
            "event",
            "meeting",
            "calendar entry",
            "time block",
            "block",
            "work session",
            "session",
        ),
        str(Intent.KNOWLEDGE_CAPTURE): (
            "note",
            "notes",
            "memo",
            "thought",
            "journal entry",
            "bookmark",
            "link",
            "source",
            "concept",
        ),
        str(Intent.KNOWLEDGE_LOOKUP): (
            "note",
            "notes",
            "memo",
            "bookmark",
            "link",
            "source",
            "concept",
            "document",
            "article",
        ),
        str(Intent.LEARNING_TRACK): (
            "learning goal",
            "goal",
            "study goal",
            "study plan",
            "skill",
        ),
        str(Intent.ACCOUNT_ADMIN): ("profile", "account", "timezone", "avatar", "settings"),
        str(Intent.DEVELOPER_INTEL): ("repository", "repositories", "repo", "repos"),
    }
)

#: Every entity the assistant can act on, and the words a user reaches for. This
#: is the vocabulary for :func:`extract_entity`, which answers a different
#: question from the one :data:`_ENTITY_NOUNS` answers: not *"which noun is this
#: phrase using as a type?"* but *"which row of which table is the user talking
#: about?"*. The two overlap heavily and neither derives from the other, because
#: a lookup can name a bookmark while the prefix stripper for its intent has no
#: reason to strip the word.
_ENTITY_WORDS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "task": (
            "task",
            "tasks",
            "todo",
            "todos",
            "to-do",
            "to-dos",
            "reminder",
            "reminders",
            "chore",
            "chores",
            "item",
            "items",
            "subtask",
            "subtasks",
            "action item",
            "action items",
        ),
        "project": ("project", "projects", "initiative", "initiatives", "epic", "epics"),
        "note": (
            "note",
            "notes",
            "memo",
            "memos",
            "thought",
            "thoughts",
            "journal entry",
            "journal entries",
        ),
        "bookmark": ("bookmark", "bookmarks", "saved link", "saved links"),
        "concept": ("concept", "concepts"),
        "link": (
            "link",
            "links",
            "url",
            "urls",
            "source",
            "sources",
            "article",
            "articles",
            "reference",
            "references",
            "document",
            "documents",
        ),
        "goal": (
            "learning goal",
            "learning goals",
            "goal",
            "goals",
            "study goal",
            "study goals",
            "study plan",
        ),
        "skill": ("skill", "skills"),
        "event": (
            "event",
            "events",
            "meeting",
            "meetings",
            "appointment",
            "appointments",
            "calendar entry",
            "calendar entries",
            "time block",
            "focus block",
            "block of time",
        ),
        "session": (
            "work session",
            "work sessions",
            "focus session",
            "session",
            "sessions",
            "time log",
            "time logs",
        ),
        "profile": (
            "profile",
            "display name",
            "avatar",
            "profile picture",
            "timezone",
            "time zone",
            "account",
            "settings",
            "preferences",
            "notification preferences",
        ),
        "repository": ("repository", "repositories", "repo", "repos"),
    }
)

#: What a request is about when the utterance names no row type at all. Derived
#: from where each intent lands rather than from the taxonomy description:
#: "delete my account data" names no noun, and the surface it lands on is the
#: user's own profile.
_DEFAULT_ENTITY: Mapping[str, str] = MappingProxyType(
    {
        str(Intent.TASK_MANAGE): "task",
        str(Intent.PROJECT_MANAGE): "project",
        str(Intent.SCHEDULE_PLAN): "event",
        str(Intent.KNOWLEDGE_CAPTURE): "note",
        str(Intent.KNOWLEDGE_LOOKUP): "note",
        str(Intent.LEARNING_TRACK): "goal",
        str(Intent.ACCOUNT_ADMIN): "profile",
        str(Intent.DEVELOPER_INTEL): "repository",
    }
)

_ENTITY_WORD_VALUES: frozenset[str] = frozenset(_ENTITY_WORDS)

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

#: The connectors that **name** a subject rather than merely precede one.
#:
#: "create a task called finish the rollout task" and "the API contract task"
#: both end in a task noun, and only one of them means the noun to be stripped.
#: The second is a noun phrase naming something that exists — the noun is its
#: type. The first ends in a connector that says the following words *are* the
#: name, so a noun there is part of the name and stripping it loses the user's
#: own word: "finish the rollout task" became "finish the rollout".
#:
#: A prepositional connector cannot do this job. "add a task to review the
#: rollout task" is genuinely ambiguous, and a rule that guessed there would be
#: wrong as often as it was right.
#:
#: The failure direction is deliberate. When this matches on a phrase that did
#: not really name a subject, the cost is a title that keeps its final noun —
#: still a usable title, and one the user sees before confirming. The opposite
#: error silently deletes a word the user typed, which is the error worth
#: spending the weaker failure on.
_NAMING_CONNECTOR_RE = re.compile(r"\b(?:called|named|titled|entitled)\b", re.IGNORECASE)


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

#: The same rule for every table, used when the subject is a reference rather
#: than a new name. "the auth token rotation note" and "the retro meeting" are
#: written the same way as "the API contract task", and a matcher that only knew
#: about tasks could not resolve either. Built from :data:`_ENTITY_NOUNS` so a new
#: entity is covered by adding its noun to one table.
_REFERENCE_TRAILING_ENTITY_RE: re.Pattern[str] = re.compile(
    r"\s+(?:"
    + "|".join(
        _phrase_pattern(noun).removeprefix(r"\b")
        for noun in sorted(
            {noun for nouns in _ENTITY_NOUNS.values() for noun in nouns},
            key=len,
            reverse=True,
        )
    )
    + r")\s*$",
    re.IGNORECASE,
)

#: Subjects that name a row without naming it *specifically*. Kept as a reference
#: rather than refused, because "make it high priority" is a request NEXUS can
#: carry most of the way — the verb, the value and the row type are all known,
#: and only the id is missing, which the caller supplies or the matcher refuses.
_WEAK_REFERENCE_TITLES: frozenset[str] = _FILLER_TITLES | {
    "event",
    "events",
    "meeting",
    "meetings",
    "skill",
    "skills",
    "bookmark",
    "bookmarks",
    "link",
    "links",
    "concept",
    "concepts",
    "session",
    "sessions",
    "profile",
    "account",
}

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


_PRIORITY_RES: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(_phrase_pattern(phrase), re.IGNORECASE), value)
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

#: The payload field a stated date belongs in, per intent. A project's date is a
#: *target* and its column is ``target_date``; calling it a due date in the values
#: mapping would hand the proposal layer a field the schema does not have, and the
#: right answer to a date for a note or a calendar event — neither of which has one
#: — is a question asked upstream, not a key invented here.
_DATE_FIELD_BY_INTENT: Mapping[str, str] = MappingProxyType(
    {
        str(Intent.TASK_MANAGE): "due_date",
        str(Intent.PROJECT_MANAGE): "target_date",
        str(Intent.LEARNING_TRACK): "target_date",
    }
)

#: "starting tomorrow" and "due tomorrow" are the same resolved day written into
#: two columns. Only the user's own word decides which, so the marker is read
#: before the date rather than guessed at afterwards.
_START_DATE_RE = re.compile(
    r"\bstart\s+date\b|\bstarting\b|\bstarts?\s+on\b|\bbeginning\s+on\b|\bbegin\s+on\b",
    re.IGNORECASE,
)

#: Title length bounds, keyed by intent, read from the schema that will validate
#: the payload rather than invented here.
_TITLE_BOUNDS: Mapping[str, tuple[int, int]] = MappingProxyType(
    {
        str(Intent.TASK_MANAGE): (_MIN_TITLE_CHARACTERS, MAX_TASK_TITLE_LENGTH),
        str(Intent.PROJECT_MANAGE): (_MIN_TITLE_CHARACTERS, MAX_PROJECT_NAME_LENGTH),
        str(Intent.KNOWLEDGE_CAPTURE): (_MIN_TITLE_CHARACTERS, MAX_NOTE_TITLE_LENGTH),
        str(Intent.LEARNING_TRACK): (_MIN_TITLE_CHARACTERS, _MAX_LEARNING_GOAL_TITLE_LENGTH),
        # The lower bound is one character rather than three, because this
        # surface's label is allowed to come from a *folder name* — "E:/op" is
        # two characters and it is exactly the name the user meant. Every other
        # bound here exists to reject a word rather than a name; rejecting "op"
        # would reject the only label that request carries.
        str(Intent.DEVELOPER_INTEL): (1, _MAX_REPOSITORY_NAME_LENGTH),
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


#: The empty mapping :attr:`Extraction.values` falls back to. A module constant
#: rather than a ``MappingProxyType({})`` call in the field default: the default
#: is shared by every extraction that stated no field, and a fresh object per
#: instance would be a per-instance allocation for a value that is the same
#: empty mapping every time.
_NO_VALUES: Mapping[str, str] = MappingProxyType({})


# --------------------------------------------------------------------------- #
# Value objects
# --------------------------------------------------------------------------- #


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

    ``title`` is **the thing being acted on**, and which thing that is depends on
    the verb: for a creation it is the name of the row that does not exist yet,
    and for every other verb it is the *reference* to the row that does. Keeping
    one field for both is deliberate — it is the field the confirm dialog puts in
    quotes and the field the reference matcher is given, and having two of them
    would let a proposal quote a new name while matching a reference and never
    notice the difference.

    ``values`` is the other half of an edit: only the fields the user actually
    stated, keyed by the payload field they belong in. It is empty for a request
    that stated nothing new, and it is never populated from a schema default —
    ``medium`` is a default, and a confirm sentence that prints it claims a choice
    nobody made.

    ``target_status`` is the state a transition asks for, ``entity`` the kind of
    row it is about, and ``bulk`` whether the sentence asked for a whole
    collection rather than one row. The last is not acted on here: a bulk delete
    is refused further up, with a reason the user reads, because deleting many rows
    from one sentence is not a thing this layer will do even though deleting one
    is.

    ``notes`` are the softer observations — a date phrase recognised but not
    resolved, a title clipped to the schema bound — which belong on the proposal,
    so the user hears about them rather than discovering them after confirming.
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
    entity: str = ""
    target_status: str | None = None
    values: Mapping[str, str] = _NO_VALUES
    bulk: bool = False
    text: str = field(default="", repr=False)

    @property
    def usable(self) -> bool:
        """Whether a proposal can be built from this extraction.

        A bulk request is **not** usable even though it may have recovered a
        title, because the one thing the proposal layer must do with it is refuse
        it. Reporting otherwise would mean a caller that checks this property
        before reading :attr:`bulk` can build a proposal over a collection.
        """
        return self.title is not None and self.reason is None and not self.bulk


@dataclass(frozen=True, slots=True)
class RowCandidate:
    """A row the caller is willing to have NEXUS act on.

    Supplied by the caller rather than looked up here: resolving a phrase to a row
    is an owner-scoped database read, and this module stays free of the ORM. The
    caller has already scoped the read to the signed-in user, which is what makes
    a match here safe to act on — this layer decides *which* of the rows the
    caller offered, never *whether* the caller may see it.

    ``label`` is the human name of the row, whichever table it came from: a
    project has a name, a task a title, a work session neither. The alias exists
    so one matcher can serve every table.
    """

    id: UUID
    label: str

    @property
    def title(self) -> str:
        """Alias of :attr:`label`, so a task reads the same either way."""
        return self.label


@dataclass(frozen=True, slots=True)
class TaskCandidate:
    """A task the caller is willing to have NEXUS act on.

    The task-shaped spelling of :class:`RowCandidate`, kept because a task's name
    is a *title* and every caller that has one says so. It is not a subclass and
    not a second definition: the two carry the same two facts and each can read
    the other's field name, so a list of either can be handed to
    :func:`match_reference` and :func:`match_task_reference` interchangeably.
    """

    id: UUID
    title: str

    @property
    def label(self) -> str:
        """Alias of :attr:`title`, so a row reads the same either way."""
        return self.title


@dataclass(frozen=True, slots=True)
class RowMatch:
    """One candidate, named unambiguously by the utterance.

    ``rule`` names the pass that found it, for the same reason every
    :class:`Argument` carries one: the confirm dialog can show *why* NEXUS
    believed it had found the row the user meant.
    """

    candidate: RowCandidate | TaskCandidate
    rule: str
    matched_text: str


@dataclass(frozen=True, slots=True)
class TaskMatch(RowMatch):
    """One task, named unambiguously by the utterance.

    A distinct class so a caller reading a task outcome can say so, and a
    subclass so ``isinstance(outcome, RowMatch)`` still holds for every row type —
    the caller that handles the general case never has to know which one it got.
    """


@dataclass(frozen=True, slots=True)
class RowMatchFailure:
    """No candidate, or more than one, and which of those happened.

    ``reason_code`` is ``"not_found"`` or ``"ambiguous"``. The two are kept apart
    because they are different conversations with the user: one means "which row
    did you mean?", the other means "NEXUS found several and will not pick". A
    matcher that conflated them would answer "I could not find it" about a row the
    user can see.
    """

    reason_code: str
    reason: str


@dataclass(frozen=True, slots=True)
class TaskMatchFailure(RowMatchFailure):
    """No task, or more than one, and which of those happened."""


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

    The comparison form used by :func:`match_reference`. Case and punctuation
    are noise for matching and load-bearing for display, which is why the title a
    user reads is never normalised.
    """
    return _WHITESPACE_RE.sub(" ", _NORMALISE_RE.sub(" ", text.lower())).strip()


#: Every entity noun this module strips, in the normalised form the reference
#: rules compare in, with a naive plural for each. Derived rather than listed: a
#: noun added to :data:`_ENTITY_NOUNS` is then known to the reference rules for
#: free, which is the difference between "the API task" being recognised as a
#: reference and being read as a title.
def _noun_variants(noun: str) -> tuple[str, ...]:
    """A normalised noun and the plural a user is likely to have typed."""
    word = _normalise(noun)
    if not word:
        return ()
    if word.endswith("s"):
        return (word,)
    return (word, f"{word}s")


_ENTITY_NOUN_WORDS: frozenset[str] = frozenset(
    variant
    for nouns in _ENTITY_NOUNS.values()
    for noun in nouns
    for variant in _noun_variants(noun)
)

#: Words that stand in for a row the user has already pointed at. They name a
#: reference and never a new title, which is why an edit may be phrased entirely
#: in them — "make it high priority" is a complete request with no title in it.
_PRONOUNS: frozenset[str] = frozenset(
    {"it", "its", "this", "that", "these", "those", "them", "one", "ones", "me"}
)

#: A determiner or demonstrative in front of a reference. Stripped only from
#: references, never from a creation: "the API contract task" names a row that
#: exists, while a note the user is naming "This is the decision" keeps the word
#: they wrote.
_REFERENCE_LEAD_RE = re.compile(
    r"\A(?:an?|the|my|our|your|that|this|those|these|its|their|of)\s+", re.IGNORECASE
)


def _reference_lead(text: str) -> str:
    """Drop the determiner(s) in front of a reference, repeatedly.

    "the API contract task" reads as three determiners once the article, the
    possessive and the noun phrase have been normalised, and one pass over the
    pattern is not always enough — hence the loop, which terminates because each
    pass shortens the string.
    """
    previous = None
    while previous != text:
        previous = text
        text = _REFERENCE_LEAD_RE.sub("", text)
    return text


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
# References, values and scope
# --------------------------------------------------------------------------- #


def _find_status(text: str) -> tuple[str, str, int, int] | None:
    """Locate the longest status word in ``text``.

    Returns:
        ``(status, matched_text, start, end)``, or ``None`` when the text asks for
        no state at all. Longest-first, because "back to open" contains "open" and
        "not done" contains "done": in both, the shorter phrase is what the longer
        one is correcting.
    """
    best: tuple[str, str, int, int] | None = None
    for pattern, value in _STATUS_RES:
        match = pattern.search(text)
        if match is None:
            continue
        start, end = match.span()
        if best is None or (start, -(end - start)) < (best[2], -(best[3] - best[2])):
            best = (value, match.group(0), start, end)
    return best


def _remove_status_wording(text: str) -> tuple[str, str | None, str | None]:
    """Remove the state the request asks for, and report it.

    Returns:
        ``(remaining_text, status, matched_text)``.
    """
    found = _find_status(text)
    if found is None:
        return text, None, None
    status, matched, start, end = found
    return _cut(text, start, end), status, matched


def _strip_value_wording(text: str) -> str:
    """Remove every phrase that states a value rather than names a row.

    Priority and status wording are what an *edit* consists of, so a subject with
    them removed is the reference on its own. Dates are left alone: resolving one
    needs a clock, and :func:`extract_verb` deliberately has none — it reads the
    shape of the request, not its content.
    """
    stripped, _, _ = _remove_priority(text)
    stripped, _, _ = _remove_status_wording(stripped)
    return stripped


def _status_from_leading_verb(text: str, verb: str) -> str | None:
    """The state a transition verb asks for when the verb is the only evidence.

    "block the API contract task" strips to a bare reference and leaves no status
    word behind — the word *was* the status. The reading is not invented: each
    entry is the state that word names in every other context it appears in.

    Args:
        text: The utterance, before any prefix was stripped.
        verb: The verb :func:`extract_verb` settled on.

    Returns:
        The status, or ``None`` when the verb does not name one.
    """
    if verb not in (ExtractedVerb.STATUS, ExtractedVerb.COMPLETE):
        return None
    bare = strip_command_prefix(text, _POLITENESS_PREFIXES).lower()
    for word, status in _VERB_STATUS.items():
        if bare.startswith(word):
            return status
    if verb is ExtractedVerb.COMPLETE:
        return "completed"
    return None


#: A shift relative to a date NEXUS was never given — "out by two days", "back a
#: week". Matched before the date rules so "push the deadline out by two days" is
#: reported as unresolvable rather than resolved into whichever day happens to be
#: in the sentence.
_RELATIVE_SHIFT_RE = re.compile(
    r"\b(?:by|for|another)\s+(?:an?\s+|one\s+|two\s+|three\s+|four\s+|five\s+)?"
    r"\d*\s*(?:more\s+|fewer\s+|less\s+)?"
    r"(?:days?|weeks?|months?|years?|hours?|minutes?)\b"
    r"|\banother\s+(?:day|week|month|year|hour)\b",
    re.IGNORECASE,
)


def _remove_relative_shift(text: str) -> tuple[str, str | None]:
    """Remove a relative shift — "out by two days", "back a week".

    Returns:
        ``(remaining_text, matched_text)``.

    These are cut out for the same reason an unresolvable date is: NEXUS cannot
    turn "by two days" into a column value without knowing what the column holds
    today, and an invented date on a deadline is the one wrong answer a user does
    not re-read. The phrase is removed and reported rather than guessed at.
    """
    match = _RELATIVE_SHIFT_RE.search(text)
    if match is None:
        return text, None
    return _cut(text, match.start(), match.end()), match.group(0)


#: "change the due date on the auth task", "update the status of the retro". The
#: field being changed is named before the row is, and leaving it in the reference
#: makes a phrase that matched nothing match nothing twice over.
_EDIT_TARGET_RE = re.compile(
    r"\A\s*(?:an?|the|my|our)?\s*"
    r"(?:due\s+date|deadline|target\s+date|start\s+date|end\s+date|status|state|"
    r"priority|title|name|description|content|summary|details?)\s*"
    r"(?:\s+(?:on|of|for|in|to|of\s+the))?\s*",
    re.IGNORECASE,
)

#: Where an edit puts the row's name: "rename the API task **to** Auth contract".
#: The connector is what separates the reference from the new value, and without
#: it the two would be concatenated into a title that names neither.
_NAMING_CONNECTOR_FOR_VALUE_RE = re.compile(
    r"\b(?:to|called|into|named|renamed\s+to|changed\s+to)\b|:\s+|\s-\s", re.IGNORECASE
)

#: The same connectors :data:`_NAMING_CONNECTOR_RE` uses for a creation, where a
#: noun after the connector is part of the name rather than a type.
_REFERENCE_NAMING_CONNECTOR_RE = re.compile(
    r"\b(?:called|named|titled|entitled|renamed\s+to|changed\s+to)\b|:\s+", re.IGNORECASE
)

#: A tail that explains where a row came from rather than naming it: "that
#: duplicate reminder I created", "the old note I added yesterday".
_ATTRIBUTION_TAIL_RE = re.compile(
    r"\s+(?:that\s+)?(?:I|we)\s+(?:just\s+)?(?:created|added|made|saved|wrote)\b.*$",
    re.IGNORECASE,
)

#: A second clause. "delete the old task and delete the new one" is two requests;
#: this layer proposes one, and the tail is the other.
_SECOND_CLAUSE_RE = re.compile(r"\s+\b(?:and|then|also|plus)\b\s+.*$", re.IGNORECASE)

#: "out" and "back" left over from "push the deadline out" once the shift itself
#: has been cut.
_SHIFT_TAIL_RE = re.compile(r"\s+(?:out|back|off)\s*$", re.IGNORECASE)

#: How long a block is. "block **two hours** tomorrow morning for the proposal"
#: is a scheduling request whose *duration* is two hours and whose subject is the
#: proposal; a duration is a number in the payload, not a word in the title.
_DURATION_RE = re.compile(
    r"\b\d+(?:[.,]\d+)?\s*(?:h|hr|hrs|hour|hours|min|mins|minute|minutes)\b"
    r"|\b(?:an?|one|two|three|half|several)\s+(?:h|hr|hrs|hour|hours)\b"
    r"|\b(?:for|of)\s+an?\s+hour\b",
    re.IGNORECASE,
)

#: The part of a day, which is a time on the day the date already resolved and
#: never part of what the row is called.
_TIME_OF_DAY_RES = re.compile(r"\b(?:morning|afternoon|evening|night|lunchtime)\b", re.IGNORECASE)
_TIME_OF_DAY_RE = re.compile(
    r"\s+\b(?:morning|afternoon|evening|night|lunchtime|first thing|end of the day)\b\s*$",
    re.IGNORECASE,
)

#: A connector in front of a reference. "… for the proposal" names the proposal;
#: the word "for" is the grammar of the sentence rather than the name of the row.
_LEADING_CONNECTOR_RE = re.compile(r"\A\s*(?:for|about|on|to|of|with|from)\s+", re.IGNORECASE)

#: A field named after the row rather than before it: "update the API contract
#: **description**". Cut from the tail so the row is left in the reference.
_TRAILING_FIELD_RE = re.compile(
    r"\s+(?:description|content|summary|details?|body)\s*$", re.IGNORECASE
)

#: "clear the due date", "remove the tags" — a request to *unset* a field. It is
#: an edit, and it is the one edit a payload cannot express: every field in
#: NEXUS's update schemas is ``Optional`` with ``None`` meaning *leave it alone*,
#: so "no due date" has no representation to write. Reporting the phrase rather
#: than quietly setting the field to the old value is the honest answer.
_CLEARED_FIELD_RE = re.compile(
    r"\b(?:clear|remove|drop|forget)\s+(?:the\s+|its\s+|their\s+|my\s+)?"
    r"(?:priority|tags?|labels?|due\s+date|deadline|description|content|summary|"
    r"details?|status|assignee|estimate)\b",
    re.IGNORECASE,
)

#: The tag in "tag the API task **as** urgent" or "remove the tag **urgent**
#: from the API task".
_TAG_VALUE_RE = re.compile(
    r"\b(?:as|with|tag(?:ged)?|labell?ed)\s+(?P<tag>[^,.;]+?)\s*$", re.IGNORECASE
)
_TAG_LEAD_RE = re.compile(
    r"\A\s*(?P<tag>[^,.;]+?)\s+(?:to|on|from|for)\s+(?P<reference>.+)$", re.IGNORECASE
)

#: The profile fields a user names in an edit, and the payload key each belongs
#: in. "Update my timezone to Europe/Lisbon" is an edit whose *name* is a
#: timezone, and reading it as a rename would propose renaming the profile.
_PROFILE_FIELDS: Mapping[str, str] = MappingProxyType(
    {
        "timezone": "timezone",
        "time zone": "timezone",
        "display name": "display_name",
        "name": "display_name",
        "avatar": "avatar_url",
        "avatar url": "avatar_url",
        "profile picture": "avatar_url",
        "username": "username",
        "email": "email",
        "email address": "email",
        "preferences": "preferences",
        "notification preferences": "preferences",
        "settings": "settings",
    }
)


def _clean_reference(text: str) -> str:
    """Trim a recovered reference down to the words that name the row.

    Four cuts, in the order they can each make the next one match: an
    attribution tail ("… I created"), a second clause ("… and delete the rest"),
    a field named after the row ("… description"), and the word a shift left
    behind ("… out"). None of them can invent a name, and all four otherwise leave
    a phrase that matches no row the user owns.
    """
    cleaned = _ATTRIBUTION_TAIL_RE.sub("", text)
    cleaned = _SECOND_CLAUSE_RE.sub("", cleaned)
    cleaned = _TRAILING_FIELD_RE.sub("", cleaned)
    cleaned = _SHIFT_TAIL_RE.sub("", cleaned)
    return _collapse(cleaned)


def _split_on_naming_connector(text: str) -> tuple[str, str] | None:
    """Split *"the API task to Auth contract"* into reference and new value.

    The connector decides which side is which, and only the last one counts: an
    update names its row before the connector and its new name after it. Returns
    ``None`` when there is no connector, or when the span after it is empty or is
    nothing but value wording — "set the task **to** blocked" has a connector and
    a value on the far side, but the value is a status, not a name.

    Args:
        text: The utterance with its command phrase and value wording removed.

    Returns:
        ``(reference, new_value)``, or ``None`` when the text names one thing.
    """
    matches = list(_NAMING_CONNECTOR_FOR_VALUE_RE.finditer(text))
    if not matches:
        return None
    match = matches[-1]
    reference = text[: match.start()]
    value = text[match.end() :]
    reference = _collapse(reference)
    value = _trim_title_edges(_collapse(value))
    if not reference or not value:
        return None
    if _find_status(value) is not None or _find_priority(value) is not None:
        return None
    return reference, value


def _extract_tag(text: str) -> tuple[str, str | None, str | None]:
    """Split a tagging request into the row and the tag to put on it.

    Two shapes, both common: the tag after a marker ("tag the API task **as**
    urgent") and the tag before the row ("add the tag **urgent** to the API
    task", "remove the tag **urgent** from the API task"). The second also hands
    back the reference, because there the tag and the row are the same phrase
    read left to right.

    Returns:
        ``(remaining_text, tag, matched_text)``. ``tag`` is ``None`` when the
        request named a row but not a label, which is a proposal the confirm
        dialog can still show — with the label left out, rather than invented.
    """
    marker = _TAG_VALUE_RE.search(text)
    if marker is not None:
        tag = _collapse(marker.group("tag"))
        return _cut(text, marker.start(), marker.end()), tag or None, marker.group(0)

    lead = _TAG_LEAD_RE.match(text)
    if lead is not None:
        tag = _collapse(lead.group("tag"))
        reference = _collapse(lead.group("reference"))
        if tag and reference:
            return reference, tag, lead.group(0).strip()

    return text, None, None


def _is_bulk(text: str) -> bool:
    """Whether the sentence asked for a whole collection rather than one row.

    Recorded on :attr:`Extraction.bulk` and refused by the layer above, because
    "delete every task" is a different *kind* of request from "delete this task"
    and not a version of it with a longer phrase. The refusal names the rule —
    one row at a time — so the user is not left thinking NEXUS is incapable.
    """
    return _BULK_RE.search(text) is not None


# --------------------------------------------------------------------------- #
# Entity
# --------------------------------------------------------------------------- #


def _entity_patterns() -> tuple[tuple[re.Pattern[str], str], ...]:
    """Every entity word as a pattern, longest phrase first.

    Sorted by length rather than kept in table order because "learning goal" and
    "work session" are each two words that begin with a one-word entity, and
    matching "goal" first would report a learning goal as a bare goal.
    """
    pairs = [(noun, entity) for entity, nouns in _ENTITY_WORDS.items() for noun in nouns]
    return tuple(
        (re.compile(_phrase_pattern(noun), re.IGNORECASE), entity)
        for noun, entity in sorted(pairs, key=lambda pair: len(pair[0]), reverse=True)
    )


_ENTITY_RES: tuple[tuple[re.Pattern[str], str], ...] = _entity_patterns()

#: A bare "block" is a time block only in a scheduling sentence. "block the API
#: contract task" is a transition, and the word that tells them apart is the one
#: in front of it.
_TIME_BLOCK_RE = re.compile(
    r"\bblocks?\s+(?:of\s+time|out\s+)?(?:\d|an?\b|one\b|two\b|three\b|half\b|"
    r"(?:for|on|in)\b|the\s+(?:morning|afternoon|evening|week|day|weekend|slot))"
    r"|\bblocks?\b(?=[^.?!]{0,40}\b(?:calendar|schedule|planner|week|day|morning))",
    re.IGNORECASE,
)

#: A trailing "block"/"blocks" that never matched the time-block rule is a plain
#: English word and says nothing about what the request is about.
_BLOCK_AS_ENTITY = "event"


def extract_entity(text: str, intent: str) -> str:
    """Decide which kind of row the utterance is about.

    The noun is the entity — "delete that **note**" is about a note — and the
    intent is the fallback for the sentences that name none: "delete everything on
    my board" is about whatever the classifier said it was about, and the surface
    it lands on is the answer.

    Args:
        text: The raw utterance, as the classifier saw it.
        intent: The predicted intent, used only for the fallback.

    Returns:
        One of the twelve entity names, or ``""`` when the intent has no default
        and the sentence named nothing — which is a refusal further up, not a
        guess down here.
    """
    lowered = text.lower()
    best: tuple[int, int, str] | None = None
    for pattern, entity in _ENTITY_RES:
        match = pattern.search(lowered)
        if match is None:
            continue
        start, end = match.span()
        candidate = (start, -(end - start), entity)
        if best is None or candidate < best:
            best = candidate
    if best is not None:
        return best[2]
    if _TIME_BLOCK_RE.search(lowered):
        return _BLOCK_AS_ENTITY
    return _DEFAULT_ENTITY.get(intent, "")


# --------------------------------------------------------------------------- #
# Verb
# --------------------------------------------------------------------------- #


def _names_a_reference(text: str) -> bool:
    """Whether ``text``'s subject is a row that exists rather than one being named.

    The one genuinely hard distinction in this module: *"make a task to finish
    DSA"* and *"make the API task high priority"* both start with a creation
    word, and the second is an edit. The discriminator is the article, not the
    vocabulary: an **indefinite** article introduces a thing that does not exist
    yet ("a task", "a note about X"), while a **definite** one, a possessive or a
    demonstrative points at a row that does ("the API task", "my retro", "that
    note"). A bare noun phrase with neither is a reference too — there is nothing
    else it could be introducing.

    A creation whose subject is a pronoun — "make it high priority" — is an edit
    as well, and this is the rule that says so instead of creating a task named
    "it".

    Args:
        text: What is left of the utterance once its command phrase has been
            stripped.

    Returns:
        ``True`` when what remains names an existing row rather than a new one.
    """
    normalised = _normalise(text)
    if not normalised:
        return False
    opener = normalised.partition(" ")[0]
    if opener in _INDEFINITE_ARTICLES:
        return False
    if opener in _PRONOUNS or opener in _DEFINITE_LEADS:
        return True
    words = _reference_lead(normalised).split()
    if not words:
        return False
    if words[-1] in _ENTITY_NOUN_WORDS:
        return len(words) == 1
    return all(word in _ENTITY_NOUN_WORDS for word in words)


#: The determiners that introduce a row the user has already named. Read from the
#: front of the phrase, because that is where English puts the article and a
#: trailing one belongs to the row's name instead.
_DEFINITE_LEADS: frozenset[str] = frozenset(
    {"the", "my", "our", "your", "that", "this", "those", "these", "its", "their"}
)
_INDEFINITE_ARTICLES: frozenset[str] = frozenset({"a", "an"})


def _first_verb(rules: tuple[tuple[re.Pattern[str], str], ...], text: str) -> str | None:
    """The first rule in ``rules`` that matches anywhere in ``text``, or ``None``.

    The rules arrive as an ordered tuple rather than a mapping because the order
    is the decision — "remove the tag urgent" and "remove the task" are both
    caught by rules that match, and only the sequence says which reading wins.

    Args:
        rules: ``(pattern, verb)`` pairs, most decisive first.
        text: The utterance to search.

    Returns:
        The verb of the first matching rule, or ``None`` when none matched.
    """
    for pattern, verb in rules:
        if pattern.search(text):
            return verb
    return None


def extract_verb(text: str) -> str:
    """Decide what the utterance *asks to be done*, from the text alone.

    Ordered by how much damage getting it wrong does:

    1. A destruction verb anywhere wins over everything. ``"delete that
       duplicate reminder"`` and ``"delete that task I created"`` are both
       ``task_manage``, and the first must never be answered with a creation. It
       is a verb now rather than a refusal, because NEXUS carries a delete — and
       what keeps that from being the live delete this rule once feared lives
       further up: the confirm route, the explicit destructive flag, the one-row
       rule, and the owner-scoped re-resolution of every id.
    2. Removing a *tag* is not removing the row, so the tag family is read before
       the delete family: every phrase that reads as one also contains a word the
       delete rule matches.
    3. A completion marker — a leading ``mark``/``complete``/``finish``, or a
       trailing ``as done`` — means complete. The verbs count only in *leading*
       position, because ``"finish"`` inside a title is ordinary English.
    4. A creation prefix at the start means create, **unless** the subject it
       introduces is a reference: "make the API task high priority" is an edit.
    5. Both 3 and 4 mean **unknown**, not a guess. "add a task and mark the
       migration one done" is two requests in one sentence, and picking either
       one is picking wrong.
    6. Then the single-word families — archive, publish, unschedule, schedule,
       log — and last the two that need context: ``status`` before ``update``, so
       "set the task to completed" is the transition it plainly is.

    Args:
        text: The raw utterance, as the classifier saw it.

    Returns:
        One of the :class:`ExtractedVerb` values.
    """
    lowered = text.lower()
    for pattern, verb in _VERB_RULES:
        if pattern.search(lowered):
            return verb

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
        if _names_a_reference(strip_command_prefix(bare, _CREATE_PREFIXES)):
            return ExtractedVerb.UPDATE
        return ExtractedVerb.CREATE
    return _first_verb(_VERB_RULES_TAIL, text) or ExtractedVerb.UNKNOWN


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
        # ``match`` is anchored on the slice, so the span it reports is relative
        # to ``end``; the lead side needs no such fix because ``search`` is given
        # ``text[:start]`` and its offsets already line up with ``text``.
        end = end + tail.end()

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
# Paths
# --------------------------------------------------------------------------- #

#: A quoted run of text. The quote pair is captured rather than listed twice so
#: that ``'C:\Users\me\nexo'`` and ``"E:/op work"`` are one rule, and so that a
#: quote of the other kind inside the path cannot end it early.
_PATH_QUOTED_RE = re.compile(r"(?P<quote>[\"'\u201c\u2018])(?P<path>[^\"'\u201c\u2018]+)(?P=quote)")

#: A Windows drive path. The lookbehind keeps it from starting inside a word, and
#: the letter before the colon is what separates ``E:/op`` from the "10:30" that
#: would otherwise have a colon and something after it.
_WINDOWS_PATH_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/][^\s\"']+")

#: A POSIX absolute path. The lookbehind is what makes this safe to run over an
#: ordinary English sentence: a slash in "and/or" or "2/3" is preceded by a word
#: character and is not a path, while "at /home/me" is. ``(?!/)`` keeps the
#: second slash of a URL out of it — "https://github.com" is an address, not a
#: folder, and a path the service cannot read is a worse answer than none. The
#: ``~`` is excluded for the same reason it is looked for: in "~/code" the slash
#: belongs to a home-relative path, and reading it as an absolute one would drop
#: the tilde and hand the service a path relative to *its* working directory.
_POSIX_PATH_RE = re.compile(r"(?<![A-Za-z0-9._~-])/(?!/)[^\s\"']+")

#: A ``~``-prefixed path. The only shape that may begin with a character that is
#: also an ordinary word character, hence its own anchor.
_TILDE_PATH_RE = re.compile(r"(?<![A-Za-z0-9_])~[\\/][^\s\"']+")

#: The unquoted shapes, in the order they are tried: a drive path, then a POSIX
#: one, then a ``~``. The order is a decision rather than an accident because
#: the shapes overlap at the edges — a ``~`` path contains a POSIX path, and
#: :data:`_POSIX_PATH_RE` is the one that declines it, so the precedence never
#: has to be resolved by length. A quoted path is not in this tuple because it
#: is matched first and separately: it is the only shape that can hold a space,
#: and therefore the only one that says where it stops.
_PATH_SHAPES: tuple[tuple[re.Pattern[str], str], ...] = (
    (_WINDOWS_PATH_RE, "a Windows drive path"),
    (_POSIX_PATH_RE, "a POSIX absolute path"),
    (_TILDE_PATH_RE, "a ~-prefixed path"),
)

#: Punctuation that ends a sentence rather than a path. Cut from the right of a
#: match so "…from E:/op." records ``E:/op`` — a path ending in a full stop is a
#: path the service would refuse, and it would refuse it for the punctuation.
_PATH_TRAILING_PUNCTUATION = ".,;:!?)]}>\"'"

#: The framing that introduces a folder: "from path", "at", "in the local
#: directory". Cut with the path so the label is what remains, and looped
#: because the phrase is two or three words long as often as one. Every word
#: here is a preposition or a noun that only ever appears in front of a value,
#: and the loop terminates because each pass strictly shortens the string.
_PATH_LEAD_RE = re.compile(
    r"(?:^|(?<=\s))(?:from|at|in|into|under|inside|within|via|on|of|located|based|"
    r"path|paths|folder|folders|directory|directories|dir|local)?"
    r"\s+(?:the\s+)?\Z",
    re.IGNORECASE,
)

#: How many framing words one path may give up before the phrase is considered
#: pathological. Four covers "in the local path"; a longer chain means the text
#: is not a path sentence at all.
_MAX_PATH_LEAD_STRIPS = 4


@dataclass(frozen=True, slots=True)
class PathMatch:
    """A filesystem path found in the utterance.

    The counterpart of :class:`DateMatch` for the one field this module will not
    report as unreadable: a date phrase that does not resolve becomes a note, but
    there is no such thing as a path phrase that does not resolve — the text said
    where the folder is or it said nothing. ``value`` is the path with its quotes
    stripped and its sentence punctuation cut, ``matched_text`` is the span the
    user actually wrote (so a quoted path keeps its quotes on screen), and
    ``rule`` names the shape it was read as.
    """

    matched_text: str
    start: int
    end: int
    value: str
    rule: str


def find_path(text: str) -> PathMatch | None:
    """Find the folder the utterance names, if it names one.

    Four shapes, quoted first, because a quoted path is the only shape that can
    hold a space and is therefore the only one that says where it ends. A quoted
    string is **not** read as a path on the strength of being quoted: it has to
    look like one, since a label is quoted as often as a folder is and reading
    *"add a repository called 'my service'"* as a folder would register the
    repository against a directory that does not exist.

    Args:
        text: The text still being reduced to a subject.

    Returns:
        The :class:`PathMatch` for the first shape that matched, or ``None`` when
        the text names no folder. There is no third answer: this function either
        reads a path off the words the user wrote or it reports that there was
        none, because a folder is the one value in NEXUS whose wrong value
        cannot be noticed until a scan fails.
    """
    for quoted in _PATH_QUOTED_RE.finditer(text):
        value = quoted.group("path").strip()
        if any(shape.match(value) is not None for shape, _wording in _PATH_SHAPES):
            return PathMatch(
                matched_text=quoted.group(0),
                start=quoted.start(),
                end=quoted.end(),
                value=value,
                rule=f"the quoted path {quoted.group(0)}",
            )

    for shape, wording in _PATH_SHAPES:
        match = shape.search(text)
        if match is None:
            continue
        value = match.group(0).rstrip(_PATH_TRAILING_PUNCTUATION)
        return PathMatch(
            matched_text=value,
            start=match.start(),
            end=match.start() + len(value),
            value=value,
            rule=f"{wording} '{value}'",
        )
    return None


def _remove_path(text: str) -> tuple[str, PathMatch | None]:
    """Cut the folder and the words that introduce it out of the text.

    The path is cut *before* the priority and date rules are read, and not
    merely because the title would otherwise hold half of it: ``E:/code/urgent``
    contains a priority word and ``C:/Users/monday`` contains a weekday, and a
    rule that read either out of a folder would write a deadline or a grade the
    user never said. The folder is the field; the words around it are grammar.

    Returns:
        ``(remaining_text, match)``.
    """
    match = find_path(text)
    if match is None:
        return text, None

    start = match.start
    for _ in range(_MAX_PATH_LEAD_STRIPS):
        lead = _PATH_LEAD_RE.search(text[:start])
        if lead is None:
            break
        start = lead.start()
    return _cut(text, start, match.end), match


def _path_tail(value: str) -> str:
    """The folder's own final segment — the label a request left unstated.

    The one value in this module supplied on the user's behalf, and only where
    nothing was supplied at all. It is what the service would default to
    anyway (:attr:`RepositoryCreate.name` falls back to the directory name), so
    reading it here cannot register a repository under a name the user did not
    mean — and the caller records it as a note and shows the path beside it,
    because a label NEXUS chose is a label the user has to be able to see.

    Args:
        value: The path as the utterance wrote it.

    Returns:
        The last segment, or ``""`` for a bare drive root, where there is no
        name to take.
    """
    segments = [segment for segment in re.split(r"[\\/]+", value) if segment]
    if not segments:
        return ""
    return segments[-1].rstrip(":")


#: The words that say the words after them **are** the name. "name" joins
#: :data:`_SUBJECT_CONNECTORS` on purpose and only here: it is how "add repo name
#: xyz" is written, and no other surface in the app has a request phrased that
#: way — adding it to the shared table would silently change what every other
#: intent recovers from "add a task name …", which is a phrase that should stay
#: exactly as wrong as the user typed it.
_REPOSITORY_LABEL_CONNECTORS: tuple[str, ...] = (
    "named",
    "called",
    "titled",
    "entitled",
    "label",
    "name",
    "as",
)


def _repository_noun_alternation() -> str:
    """The repository nouns as one regex alternation, longest first.

    Built once and shared by the two patterns that read them, so a noun added to
    :data:`_ENTITY_NOUNS` is a one-word edit rather than a second place to keep
    in step — the same argument :func:`_entity_prefix_pattern` makes for the
    per-intent table.
    """
    return "|".join(
        re.escape(noun)
        for noun in sorted(_ENTITY_NOUNS[str(Intent.DEVELOPER_INTEL)], key=len, reverse=True)
    )


def _repository_subject_pattern() -> re.Pattern[str]:
    """Compile the ``subject prefix`` regex for a repository request.

    Built from :data:`_ENTITY_NOUNS` for the same reason
    :func:`_entity_prefix_pattern` is: adding a noun is then a one-word edit
    rather than a second place to keep in step.

    Two differences from that function, both about a request that names **no**
    label. The naming connector is optional, because "register the repo" is
    nothing but a noun; and a noun may end the text rather than be followed by a
    space, because after the folder has been cut out, "add repo from E:/op"
    leaves the word "repo" standing on its own at the end of the sentence. Both
    are what lets the label be recovered rather than refused.
    """
    alias = _repository_noun_alternation()
    connectors = "|".join(sorted(_REPOSITORY_LABEL_CONNECTORS, key=len, reverse=True))
    return re.compile(
        r"\A\s*(?:(?:an?|the|my|our|this)\s+)?(?:(?:new|another|extra)\s+)?"
        rf"(?:(?:{alias})(?:\s+|\Z))*(?:(?:{connectors})\s+)?",
        re.IGNORECASE,
    )


_REPOSITORY_SUBJECT_RE: re.Pattern[str] = _repository_subject_pattern()

#: The same nouns, found anywhere rather than at the front. Read only when the
#: sentence does not open with one — which is what "register the repo E:/op"
#: does, since "register" is not one of this module's creation prefixes and the
#: definite article makes the subject read as a reference. The noun is what
#: establishes the request as being about a repository, so it is the boundary:
#: everything up to it is framing, whatever it was phrased with.
_REPOSITORY_NOUN_RE: re.Pattern[str] = re.compile(
    r"(?:^|(?<=\s))(?:" + _repository_noun_alternation() + r")(?=\s|\Z)",
    re.IGNORECASE,
)


def _strip_repository_subject(text: str) -> tuple[str, list[str]]:
    """Cut the entity noun and the naming connector, leaving the label.

    "repo name xyz" is three words that describe one thing, and only the last is
    the name; the other two are cut here so that the ordinary title rules can
    then do what they do for every other surface. The spans cut are returned
    rather than discarded, because they are what the confirm dialog shows as the
    reasoning behind the name it is about to write.

    Args:
        text: What is left of the utterance once the path has been removed.

    Returns:
        ``(remaining_text, removed)``. ``removed`` is empty when the text held no
        framing to cut, which is the answer for a subject that is already bare.
    """
    match = _REPOSITORY_SUBJECT_RE.match(text)
    if match is not None and match.end() > 0:
        consumed = match.group(0).strip()
        return text[match.end() :], [consumed] if consumed else []

    noun = _REPOSITORY_NOUN_RE.search(text)
    if noun is None:
        return text, []
    consumed = text[: noun.end()].strip()
    return text[noun.end() :], [consumed] if consumed else []


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


def extract_title(
    text: str, *, intent: str, reference: bool = False
) -> tuple[str | None, str | None, list[str], list[str]]:
    """Recover the subject of the request from what the other rules leave behind.

    Args:
        text: The utterance with its command phrase already stripped.
        intent: The predicted intent, which chooses the entity nouns to strip.
        reference: Whether the subject is a **reference to a row that already
            exists** rather than the name of one being created. The two are read
            differently in three ways, and all three differences are about not
            deleting a word the user typed: a reference may be a bare pronoun
            ("make it high priority"), a reference is introduced by a
            demonstrative or a possessive ("that duplicate reminder"), and its
            trailing noun is a *type* for every table and not only for tasks
            ("the auth token rotation note").

    Returns:
        ``(title, reason, removed, notes)``. ``title`` is ``None`` — with
        ``reason`` populated — when nothing confident survives. ``removed`` lists
        every span cut along the way, and becomes the provenance rule on the
        title's :class:`Argument`.
    """
    removed: list[str] = []
    notes: list[str] = []
    remaining = _collapse(text)

    if reference:
        lead = _REFERENCE_LEAD_RE.match(remaining)
        if lead is not None:
            removed.append(lead.group(0).strip())
            remaining = remaining[lead.end() :]
        field = _EDIT_TARGET_RE.match(remaining)
        if field is not None and field.end() > 0:
            removed.append(field.group(0).strip())
            remaining = remaining[field.end() :]
        # A reference usually ends with its type ("the API contract task") and
        # often with a connector after it ("set the API contract task to
        # blocked"). The connector has to go before the type can be read as one,
        # or the trailing-noun rule below sees "task to" and matches nothing.
        remaining = _TRAILING_FILLER_RE.sub("", remaining)

    # Checked before the prefix match, because the match is not guaranteed to run
    # and consume the connector: "create a high priority task called X" puts an
    # adjective between the determiner and the noun, which the prefix pattern
    # does not cross, so by the time it would have matched the connector is the
    # only surviving evidence that the subject was named.
    named = _NAMING_CONNECTOR_RE.search(remaining) is not None

    entity_pattern = _ENTITY_PREFIX_RES.get(intent)
    if entity_pattern is not None:
        match = entity_pattern.match(remaining)
        if match is not None and match.end() > 0:
            consumed = match.group(0).strip()
            removed.append(consumed)
            remaining = remaining[match.end() :]

    trailing_pattern = _REFERENCE_TRAILING_ENTITY_RE if reference else _TRAILING_ENTITY_RE
    if not named:
        trailing = trailing_pattern.search(remaining)
        if trailing is not None:
            removed.append(trailing.group(0).strip())
            remaining = remaining[: trailing.start()]

    title = _trim_title_edges(remaining)
    minimum, maximum = _TITLE_BOUNDS.get(intent, (_MIN_TITLE_CHARACTERS, MAX_TASK_TITLE_LENGTH))

    # A pronoun or a bare noun is a *weak but real* reference: it cannot be
    # matched against a candidate set here, and the caller will be told so rather
    # than given a title invented from it. Refusing it at this level would throw
    # away the part of the request that was understood — the verb and the value —
    # because the row it names is the one thing this layer cannot see.
    if reference and title.lower() in _WEAK_REFERENCE_TITLES:
        return title, None, removed, notes

    # A determiner is the opposite: it names *something* and identifies nothing.
    # Left in, "delete the" becomes a fragment that matches every row whose title
    # happens to contain the word "the".
    if reference and _normalise(title) in _DEFINITE_LEADS | _INDEFINITE_ARTICLES:
        title = ""

    if len(title) < minimum or title.lower() in _FILLER_TITLES:
        return (
            None,
            (
                "NEXUS could not tell what this request is about: after removing the "
                "command phrase there was nothing left to name. Ask in one line — "
                "'add a task to <what>'."
                if not reference
                else "NEXUS could not tell which row this request is about. Ask in one "
                "line — 'delete the task called <what>'."
            ),
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
# Row reference matching
# --------------------------------------------------------------------------- #


def match_reference(
    fragment: str,
    candidates: Sequence[RowCandidate | TaskCandidate],
    *,
    noun: str = "row",
) -> RowMatch | RowMatchFailure:
    """Match a spoken fragment to exactly one of the caller's own rows.

    Three deterministic passes, strictest first: exact equality of the normalised
    strings, containment in either direction, then every significant word of the
    fragment appearing in the candidate's label. The first pass yielding
    **exactly one** candidate wins.

    This is the same question for every table, and it is asked the same way for
    all of them: *"the user said a phrase, exactly one row of theirs reads that
    way, here it is"*. It is deliberately **not** a similarity score with a
    threshold. A threshold on "how close is this" is a guess with a number
    attached, and every kind this layer can now carry is a write to somebody's own
    data: completing the wrong task loses history, renaming the wrong project
    loses a name, deleting the wrong note loses a document.

    Args:
        fragment: The reference recovered from the request.
        candidates: The rows the caller is willing to have NEXUS act on. They
            arrive already owner-scoped by the caller, so a match here can only
            ever name a row the signed-in user can already see.
        noun: What to call the rows in the refusal text — ``"note"``, ``"project"``
            — so the sentence names the thing the user asked about instead of the
            abstraction.

    Returns:
        A :class:`RowMatch`, or a :class:`RowMatchFailure` whose reason code is
        ``"not_found"`` or ``"ambiguous"``. Both are refusals: **this function never
        returns a second-best match**.
    """
    return _match_rows(fragment, candidates, noun=noun, verb="change")


def match_task_reference(
    fragment: str, candidates: Sequence[TaskCandidate | RowCandidate]
) -> TaskMatch | TaskMatchFailure:
    """Match a spoken fragment to exactly one of the caller's own tasks.

    The task-shaped spelling of :func:`match_reference`, kept because a task is
    the one row this layer matches most often and because the completion summary
    reads in its terms. It accepts either candidate type and answers with the task
    result types, so every existing caller keeps its own vocabulary.

    Args:
        fragment: The subject recovered from a completion request.
        candidates: The tasks the caller is willing to have acted on.

    Returns:
        A :class:`TaskMatch`, or a :class:`TaskMatchFailure` whose reason code is
        ``"not_found"`` or ``"ambiguous"``. Both are refusals: **this function
        never returns a second-best match**, because completing the wrong task is
        a write the user has to notice and undo.
    """
    outcome = _match_rows(fragment, candidates, noun="task", verb="complete")
    if isinstance(outcome, RowMatchFailure):
        return TaskMatchFailure(outcome.reason_code, outcome.reason)
    return TaskMatch(outcome.candidate, outcome.rule, outcome.matched_text)


def _match_rows(
    fragment: str,
    candidates: Sequence[RowCandidate | TaskCandidate],
    *,
    noun: str,
    verb: str,
) -> RowMatch | RowMatchFailure:
    """The matcher both public spellings share, parameterised by its own wording.

    The words differ and the behaviour must not: a task match says "complete" and
    a note match says "change", but "NEXUS will not guess which one" is the same
    promise either way, and two implementations of the same three passes would be
    two things to keep identical.

    Args:
        fragment: The reference recovered from the request.
        candidates: The rows the caller offered.
        noun: What to call the rows in the refusal text.
        verb: What NEXUS was being asked to do to them.

    Returns:
        A :class:`RowMatch` or a :class:`RowMatchFailure`.
    """
    needle = _normalise(fragment)
    if not needle:
        return RowMatchFailure(
            reason_code="not_found",
            reason=f"The request did not name a {noun}, so there is nothing to {verb}.",
        )

    normalised = [(candidate, _normalise(candidate.label)) for candidate in candidates]
    # A label of nothing but punctuation normalises to "", and "" is contained in
    # every needle, so such a candidate would match every fragment and turn these
    # passes into a coin flip. It has no words to be matched on; drop it.
    normalised = [pair for pair in normalised if pair[1]]

    exact = [pair for pair in normalised if pair[1] == needle]
    if len(exact) == 1:
        return RowMatch(exact[0][0], "exact title match", fragment)
    if len(exact) > 1:
        return RowMatchFailure(
            reason_code="ambiguous",
            reason=f"{len(exact)} {noun}s are titled '{fragment}'; NEXUS will not pick between them.",
        )

    contained = [pair for pair in normalised if needle in pair[1] or pair[1] in needle]
    if len(contained) == 1:
        return RowMatch(contained[0][0], "the named phrase appears in exactly one title", fragment)
    if len(contained) > 1:
        return RowMatchFailure(
            reason_code="ambiguous", reason=_ambiguous_reason(fragment, contained, noun, verb)
        )

    words = [word for word in needle.split() if len(word) > 2]
    if words:
        hits = [pair for pair in normalised if all(word in pair[1].split() for word in words)]
        if len(hits) == 1:
            return RowMatch(hits[0][0], "every word of the named phrase is in one title", fragment)
        if len(hits) > 1:
            return RowMatchFailure(
                reason_code="ambiguous", reason=_ambiguous_reason(fragment, hits, noun, verb)
            )

    return RowMatchFailure(
        reason_code="not_found",
        reason=(
            f"No open {noun} matches '{fragment}'. NEXUS will not {verb} a {noun} it "
            "would have to guess at."
        ),
    )


def _ambiguous_reason(
    fragment: str, pairs: Sequence[tuple[RowCandidate | TaskCandidate, str]], noun: str, verb: str
) -> str:
    """The refusal text for a fragment that matched more than one row."""
    labels = ", ".join(f"'{pair[0].label}'" for pair in pairs)
    return (
        f"'{fragment}' matches {len(pairs)} {noun}s ({labels}); NEXUS will not guess "
        f"which one to {verb}."
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
    first, because it decides whether the subject is a **new name** or a
    **reference to a row that exists**, and every rule after it is read
    differently depending on which; then priority, the relative shift and the
    date, because all three are cut *out* of the text and what is left is the
    row; then the transition wording, the tag, and finally the split between the
    reference and the new value.

    The one value read ahead of all of them is the **folder**, and only for the
    one intent that has one. It is first because a path contains words the other
    tables match — ``C:/Users/monday`` is a weekday and ``E:/code/urgent`` is a
    priority — and because the alternative is a repository row whose every
    future scan fails.

    A destructive verb is **not** a refusal here any more. It used to be, and the
    refusal was honest about a real limit — the classifier cannot see the verb, so
    NEXUS could not tell "add a task" from "delete everything". The verb is now
    read off the text instead, and what keeps a delete from being the destructive
    proposal that rule feared is not in this layer: the confirm route is
    mandatory, a destructive kind must be confirmed *destructively*, a delete names
    exactly one row (:attr:`Extraction.bulk` is how "delete every task" is
    reported so it can be refused), and the id is re-resolved through an
    owner-scoped service before anything is written.

    Args:
        text: The raw utterance, as the classifier saw it.
        prediction: The classifier's output. Only ``intent`` and ``confidence``
            are read; this function never re-classifies anything.
        tz: The caller's IANA zone. Dates resolve against it, and the resulting
            instant is converted to UTC through it.
        now: The reference instant, defaulting to :func:`datetime.now`. A naive
            value is read as UTC.

    Returns:
        An :class:`Extraction`: the row being acted on, the values stated for it,
        and the reasons for whatever could not be recovered.
    """
    intent = prediction.intent
    confidence = float(prediction.confidence)
    today = local_day(now or datetime.now(UTC), tz)
    verb = extract_verb(text)
    entity = extract_entity(text, intent)

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
            entity=entity,
            text=text,
        )

    arguments: list[Argument] = []
    notes: list[str] = []
    values: dict[str, str] = {}
    bulk = _is_bulk(text)

    # Everything except a creation is about a row that already exists, and that
    # changes three things: the subject may legitimately be a pronoun, its
    # determiner is part of the reference rather than part of a name, and the
    # words after a connector are the *new* value rather than the title.
    reference = verb is not ExtractedVerb.CREATE

    remaining = _strip_verb_prefix(text, verb)

    # The folder is read before every other value rule, for the reason
    # :func:`_remove_path` gives: a path is the one field that holds words the
    # priority and date vocabularies also match, and a repository registered
    # against no folder is a scan that fails on every future run. A sentence that
    # names no path simply gets no ``"path"`` key — the refusal above answers
    # that, and this layer never supplies a directory of its own.
    path: PathMatch | None = None
    if intent == str(Intent.DEVELOPER_INTEL):
        remaining, path = _remove_path(remaining)
        if path is not None:
            values["path"] = path.value
            arguments.append(
                Argument(
                    field="path",
                    value=path.value,
                    matched_text=path.matched_text,
                    rule=f"the folder to register, read from {path.rule}",
                )
            )

    # A tag is read before the priority table is, and the order is the point:
    # "urgent" is both a priority synonym and the most common tag anybody writes,
    # so whichever rule sees it first decides whether the request is a priority
    # change or a labelling one. For a tag request it can only be the label.
    tag = None
    if verb in (ExtractedVerb.TAG, ExtractedVerb.UNTAG):
        remaining, tag, tag_text = _extract_tag(remaining)
        if tag is not None:
            values["tag"] = tag
            arguments.append(
                Argument(
                    field="tag",
                    value=tag,
                    matched_text=tag_text or tag,
                    rule=f"the tag to put on the row, taken from '{tag_text}'",
                )
            )
        else:
            notes.append(
                "No tag name was found in the request, so the proposal names the row "
                "only; nothing will be labelled."
            )

    remaining, priority, priority_text = _remove_priority(remaining)
    if priority is not None:
        values["priority"] = priority
        arguments.append(
            Argument(
                field="priority",
                value=priority,
                matched_text=priority_text or priority,
                rule=f"priority phrase '{priority_text}' maps to {priority}",
            )
        )

    remaining, shift_text = _remove_relative_shift(remaining)
    if shift_text is not None:
        notes.append(
            f"No new date was set: '{shift_text}' is a shift from a date NEXUS was "
            "never given, and the stored value is an absolute day, so NEXUS asks "
            "rather than inventing one."
        )

    if reference:
        remaining = _clean_reference(remaining)
        cleared = _CLEARED_FIELD_RE.search(remaining)
        if cleared is not None:
            notes.append(
                f"'{cleared.group(0)}' asks for a field to be emptied, and an update "
                "cannot say 'leave it empty' — a missing field means unchanged. NEXUS "
                "is asking rather than writing a value nobody chose."
            )

    starts = bool(_START_DATE_RE.search(text))
    due_date: date | None = None
    due_at: datetime | None = None
    if verb in (ExtractedVerb.SCHEDULE, ExtractedVerb.UNSCHEDULE, ExtractedVerb.CREATE):
        # A duration and a part of the day are scheduling *parameters*. They are
        # cut before the subject is recovered, and reported rather than dropped,
        # because the confirm sentence has to say what the user asked for and a
        # two-hour block that silently became an untimed one is a different write.
        duration = _DURATION_RE.search(remaining)
        if duration is not None:
            notes.append(
                f"'{duration.group(0)}' was read as a length of time rather than as "
                "part of the name."
            )
            remaining = _cut(remaining, duration.start(), duration.end())
        # Everywhere for a scheduling verb, trailing only for a creation: "block
        # two hours tomorrow **morning** for the proposal" is a part of a day,
        # while a task *called* "the morning report" is a name the user typed.
        remaining = (
            _TIME_OF_DAY_RES.sub("", remaining)
            if verb is not ExtractedVerb.CREATE
            else _TIME_OF_DAY_RE.sub("", remaining)
        )

    remaining, date_match = _remove_date(remaining, today)
    if date_match is not None:
        if date_match.value is not None:
            due_date = date_match.value
            due_at = day_start_utc(due_date, tz)
            # "starting tomorrow" and "due tomorrow" are the same resolved day
            # written into two different columns, and which column the user meant
            # is the word they used rather than the day they got. Which of them it
            # is depends on the intent: a project dates itself by a target date and
            # a task by a due date, and a key the schema does not have is a
            # question to ask rather than a value to send.
            date_field = "start_date" if starts else _DATE_FIELD_BY_INTENT.get(intent, "due_date")
            values[date_field] = due_date.isoformat()
            arguments.append(
                Argument(
                    field=date_field,
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

    target_status, remaining = _extract_transition(verb, text, remaining, arguments, values)

    if reference:
        # A connector in front of a reference is the grammar of the sentence, not
        # the name of the row: "… for the proposal" is about the proposal.
        remaining = _LEADING_CONNECTOR_RE.sub("", remaining)
        split = _split_on_naming_connector(remaining)
        if split is not None:
            named, new_value = split
            profile_field = _profile_field(named) if entity == "profile" else None
            if profile_field is not None:
                values[profile_field] = new_value
                arguments.append(
                    Argument(
                        field=profile_field,
                        value=new_value,
                        matched_text=new_value,
                        rule=(
                            f"the new {profile_field.replace('_', ' ')}, taken from '{new_value}'"
                        ),
                    )
                )
                remaining = "profile"
            else:
                values["title"] = new_value
                arguments.append(
                    Argument(
                        field="title",
                        value=new_value,
                        matched_text=new_value,
                        rule=(
                            "the span after the naming connector, which is the new "
                            "name and not the row being renamed"
                        ),
                    )
                )
                remaining = named
        elif entity == "profile":
            profile_field = _profile_field(remaining)
            if profile_field is not None:
                notes.append(
                    f"The request names the {profile_field.replace('_', ' ')} but not "
                    "its new value, so NEXUS is asking rather than writing a field it "
                    "would have to clear."
                )
                remaining = "profile"

    framing: list[str] = []
    if intent == str(Intent.DEVELOPER_INTEL):
        remaining, framing = _strip_repository_subject(remaining)
        if not remaining.strip() and path is not None:
            # "register the repo E:/op" names no label at all, and the folder's
            # own name is the only one in the sentence. It is recorded as a note
            # because it is the one value here NEXUS supplied, and the note is
            # what keeps the proposal honest about it.
            label = _path_tail(path.value)
            if label:
                remaining = label
                notes.append(
                    "The request named no label for the repository, so NEXUS read "
                    f"'{label}' off the folder's own name — say 'called {label}' to "
                    "choose another."
                )

    title, reason, removed, title_notes = extract_title(
        remaining, intent=intent, reference=reference
    )
    removed = [*framing, *removed]
    notes.extend(title_notes)
    notes.extend(_unresolved_date_notes(remaining))
    if title is not None:
        if verb is ExtractedVerb.CREATE:
            values["title"] = title
        arguments.append(
            Argument(
                field="title",
                value=title,
                matched_text=text,
                rule=("the row being acted on, with " if reference else "the utterance with ")
                + (", ".join(repr(span) for span in removed) if removed else "nothing")
                + " removed",
            )
        )

    if bulk:
        # The wording is the refusal the proposal layer owes the user, written
        # here because this is the layer that can see the difference between "this
        # task" and "every task". A caller that needs the sentence takes the note.
        notes.append(
            "The request asks for a whole collection rather than a single row, and "
            "NEXUS will not delete a collection from one sentence — it works one row "
            "at a time. Name the one row and it will act on exactly that one."
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
        entity=entity,
        target_status=target_status,
        values=MappingProxyType(dict(values)),
        bulk=bulk,
        text=text,
    )


def _strip_verb_prefix(text: str, verb: str) -> str:
    """Cut the command phrase that opened the request, using the verb's own words.

    Args:
        text: The raw utterance.
        verb: The verb :func:`extract_verb` settled on, which chooses the phrase
            family. Reading the verb's own table rather than a single combined one
            matters: "set" is an edit and "set up" is a creation, and the two
            phrases are only distinguishable before the verb is known.

    Returns:
        What is left of the utterance once its framing is gone.
    """
    prefixes = (*_POLITENESS_PREFIXES, *_VERB_LEADING_WORDS.get(verb, ()))
    remaining = strip_command_prefix(text, prefixes)
    if verb == ExtractedVerb.COMPLETE:
        remaining = _remove_completion_marker(remaining)
    if verb == ExtractedVerb.DELETE and remaining == text.strip():
        # "add a task to clean the board and delete the old ones" is two requests
        # and the deletion is the second one. Cutting to the verb is what keeps
        # the proposal from being about the task the first clause asked to create.
        late = _DELETE_RE.search(remaining)
        if late is not None:
            remaining = remaining[late.end() :]
    return _collapse(remaining)


def _extract_transition(
    verb: str,
    text: str,
    remaining: str,
    arguments: list[Argument],
    values: dict[str, str],
) -> tuple[str | None, str]:
    """Read the state a transition asks for, and cut it out of the reference.

    Only a transition carries one. "add a task to finish DSA" contains the word
    "finish", and reading a status out of it would create a task that is already
    done — so the check is on the verb, not on the words.

    Args:
        verb: The verb :func:`extract_verb` settled on.
        text: The raw utterance, needed for the case where the verb *is* the
            status word and nothing is left once it is stripped.
        remaining: The utterance with its command phrase removed.
        arguments: Provenance sink; the status is appended as an
            :class:`Argument`.
        values: The stated-values mapping, updated in place.

    Returns:
        ``(status, remaining)``. ``remaining`` has the status wording cut out of
        it when there was any, because "set the API task to blocked" names the row
        before the state and a state left in place would be matched against it.
    """
    if verb not in (ExtractedVerb.STATUS, ExtractedVerb.COMPLETE):
        return None, remaining

    cut, status, matched = _remove_status_wording(remaining)
    if status is None:
        status = _status_from_leading_verb(text, verb)
    if status is None:
        return None, remaining

    values["status"] = status
    arguments.append(
        Argument(
            field="status",
            value=status,
            matched_text=matched or status,
            rule=(f"'{matched}' names the state {status}" if matched else f"the {status} verb"),
        )
    )
    return status, cut


def _profile_field(fragment: str) -> str | None:
    """The payload field a profile edit is about, when the fragment names one.

    "Update my timezone to Europe/Lisbon" is an edit whose subject is a field, not
    a row: the row is always the caller's own profile, and the field is what the
    sentence is about. Without this the connector rule would read "timezone" as a
    reference and "Europe/Lisbon" as a new name, and would propose renaming the
    profile.

    The field is found *inside* the fragment rather than as the whole of it,
    because a user says "the email address on my account" where they mean one
    field, and insisting the phrase be exactly the field name would make every
    sentence of that shape unreadable.

    Args:
        fragment: The span before the connector.

    Returns:
        The payload field name, or ``None`` when the fragment names no field.
    """
    normalised = _reference_lead(_normalise(fragment))
    for phrase in sorted(_PROFILE_FIELDS, key=len, reverse=True):
        if re.search(_phrase_pattern(phrase), normalised):
            return _PROFILE_FIELDS[phrase]
    return None


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
