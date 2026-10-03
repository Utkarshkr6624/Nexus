"""Rubric evaluation of a Qwen generation: base model against the fine-tune.

**What this establishes, stated first because it is the honest part.** Every
dimension here is a **mechanical proxy**. It reads the text of a response with
regexes and ``json.loads`` and asks questions a language model could not argue
with: did it name a capability that exists, did it claim to have performed an
irreversible action, does its action block parse. None of that is reasoning
quality, helpfulness, tone, correctness of the advice itself, or whether the
user would have preferred the answer. Nothing here can rank two responses that
are both fluent and both wrong in different ways. **A rubric that says the
fine-tune is better means the fine-tune broke fewer of the rules below, and
nothing more.** Any claim about how helpful or sensible the answers are needs
human evaluation, which Phase 10 cannot substitute with arithmetic. The reports
say so in their own text: :meth:`EvalReport.to_markdown` leads with the limits
and states the counting rule on the aggregate score.

The one thing this module is genuinely good at is the failure mode Nexo cares
most about, and it is worth being precise about why that failure mode is
mechanically checkable. Every member of ``RecommendationType`` names an action
a **person** takes — *reschedule_task*, *break_down_task*, *block_time*,
*review_deadline* — and none of them names something the system performs. So
*"I have rescheduled your task"* is not a stylistic imprecision; it is the model
describing an execution the product does not have, into a calendar the user
keeps. :class:`RubricDimension.NO_FALSE_EXECUTION` is therefore a hard 0.0, not a
penalty, and it is the one dimension a base 8B model reliably fails and the one
the fine-tune is expected to fix.

**The dimensions, and the rule behind each one.** Every score in ``[0.0, 1.0]``
comes from an inspectable predicate over the response text — no model, no
embedding, no rubric classifier. The full rule is in each scoring function's
docstring; the summary is:

* :attr:`~RubricDimension.CAPABILITY_GROUNDING` — capitalised terms the response
  uses must exist in the capability inventory harvested off ``backend/app``;
  invented product names cost the full 1.0.
* :attr:`~RubricDimension.STRUCTURED_ACTION` — a fenced block or delimited
  action list exists, and if it claims to be JSON it actually parses. Unparseable
  JSON is deliberately harsher than no block at all: a machine-readable block
  that does not parse fails at the consumer, whereas prose that was never meant
  for one cannot.
* :attr:`~RubricDimension.NO_FALSE_EXECUTION` — no past-tense completion of an
  action NEXUS proposes but never performs.
* :attr:`~RubricDimension.CLARIFICATION` — an ambiguous instruction gets a
  question. Score is **not** an automatic failure for a non-ambiguous one: the
  SFT data generates plain answers for clear prompts, so demanding a question
  everywhere would punish the behaviour the fine-tune was trained for.
* :attr:`~RubricDimension.ESCALATION` — an instruction no Nexo surface can serve
  is refused or named as out of scope; a clear one is **not** answered at length.
  Asymmetric on purpose: a refusal is cheap to write by accident, so an
  unsupported one is caught by the unknown-intent sweep instead.
* :attr:`~RubricDimension.UNKNOWN_INTENT` — sweeping every taxonomy keyword
  against the response. Only meaningful when ``expected_intent`` is None, since a
  routed answer is *supposed* to use the vocabulary of its own class.
* :attr:`~RubricDimension.CONTRADICTION_FREE` — it never asserts it both cannot
  and did, and never quotes back a value it contradicts itself on.
* :attr:`~RubricDimension.CONCISION` — words per instruction word against a
  length ladder calibrated on this application's 185 routes and 14 intents.

**Grounding is a vocabulary, not a knowledge base.** Two honest limits. Only
multi-character capitalised runs are checked, so ``Jira`` is caught but ``JQL``
is not, and a capability the inventory does not name (``FastAPI``, ``Docker``,
``TimescaleDB``) counts as invented. Both are deliberate: the report is a
*product*-grounding check against a real harvest, and the README's honesty rule
makes a false accusation worse than a missed one. The evidence string names the
offending terms so a reader can tell which kind of finding they are looking at.

**Counting rules.** ``score_generation`` yields the eight dimensions in enum
declaration order, every example carries all eight, and ``per_dimension`` is the
mean over those eight. That is why the scores in ``examples`` average to
``overall`` for every row: the aggregation is the plain mean, not a weighted
scorecard. A harder weighting is a policy decision, not a measurement, so it does
not live in the code that takes the measurements.

Everything here is stdlib-only and deterministic — ``ml`` never gains a
dependency, and two runs over the same generations produce byte-identical JSON.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from ml.datasets.schema import stable_json_dumps

#: Version of the report shape. Bump on any rename of a field or a dimension,
#: so a base-vs-tuned comparison can never pair a v1 report with a v2 one and
#: read the difference as a change in the model.
QWEN_EVAL_VERSION = "qwen_eval.v1"

#: Decimal places used when rendering scores for humans. Four is enough to show a
#: delta and short enough to keep a markdown table scannable; the raw floats are
#: untouched in ``to_dict``.
_REPORT_PRECISION = 4

#: ``math.exp`` overflows just past 709; a mean NLL at or above this is reported
#: as ``inf``. A diverged fine-tune really does produce one, and ``inf`` says
#: so where a plausible-looking number would hide it.
_NLL_CEILING = 700.0

#: A per-dimension delta at or below this is "unchanged". A rubric delta of
#: 0.0007 is the same number twice at the precision anyone reads one at, and
#: calling it a move is how a comparison starts telling a story the data does
#: not contain.
_DELTA_EPSILON = 1e-6

#: Response words per instruction word a routeable request may cost before
#: escalation is scored as failed. Deliberately generous: this application has
#: 185 routes across 19 domains, and naming the surface, the permission it needs
#: and the action to take genuinely costs more words than the question asked.
_ESCALATION_WORD_BUDGET = 8.0

#: Prepositions that mark a demonstrative as standing in for something. A
#: demonstrative before one of these is an object — "reschedule *that* for me" —
#: while the same word before a noun heads a relative clause and specifies
#: nothing.
_AMBIGUITY_PREPOSITIONS = frozenset(
    {"for", "to", "into", "onto", "with", "from", "before", "after", "by", "in"}
)

#: Word-to-word length ladder, in response words per instruction word. The top
#: rung is the realistic one: this application has 185 routes across 19 domains
#: and 14 intents, and naming the right surface, the permission it needs and the
#: action to take genuinely costs more than four words per word asked. Past that
#: rung the penalty is 0.5 per doubling, so a runaway generation loses the point
#: decisively instead of being squeezed towards it.
_CONCISION_RATIOS = (3.0, 8.0, 16.0)

#: Words of length 0 that begin a run which may continue as one term. Keeps the
#: single initial capitalisation in "I have rescheduled your task" out of the
#: grounding check.
_JOINING_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "as",
        "at",
        "by",
        "for",
        "from",
        "in",
        "is",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
    }
)

#: Phrases that ask the user something. "?" alone is not enough — an exclamation
#: is not a question and a hedged aside is not one either.
_CLARIFYING_PHRASES = (
    "which one",
    "what do you mean",
    "could you clarify",
    "can you clarify",
    "can you confirm",
    "could you confirm",
    "which project",
    "which task",
    "which do you",
    "did you mean",
    "would you like me to",
    "do you want me to",
    "can you be more specific",
    "be more specific",
)

#: What the model says when the request is one NEXUS cannot serve.
_ABSTENTION_MARKERS = (
    "don't have",
    "do not have",
    "cannot",
    "can't",
    "not able to",
    "out of scope",
    "outside what i",
    "no surface",
    "not something i",
    "not something nexo",
    "i don't",
    "unable",
)

#: What a refusal says when it names the surfaces the user might have meant.
_SURFACE_REDIRECT = (
    "you might have meant",
    "did you mean",
    "you may have meant",
    "instead of",
    "rather than",
)

#: Claimed completions of an action NEXUS proposes but never performs. Every
#: pattern pairs a completion or perfect auxiliary with an action verb, which is
#: what separates "I have rescheduled your task" from "You may want to
#: reschedule it". Deliberately not included: *I found*, *I see*,
#: *I checked*, *I reviewed* — reading a risk or running an analytics query is
#: something NEXUS does do, and flagging those would make the false-positive rate
#: of the dimension so high that the signal is worthless.
_FALSE_EXECUTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:i|we|nexus|nexo|northstar)\s+(?:have\s+|has\s+|already\s+)*"
        r"(?:rescheduled|deleted|removed|archived|cancelled|canceled|created|created?)"
        r"(?:\s+\w+){0,3}\s+(?:your|the|my|this|that)\b"
    ),
    re.compile(r"\bi(?:'ve|\s+have)\s+(?:now\s+)?(?:deleted|removed|archived|created)\b"),
    re.compile(
        r"\b(?:i|we|nexus|nexo|northstar)\s+(?:have\s+|has\s+|already\s+)*"
        r"(?:marked|flagged|assigned|booked|scheduled|blocked|moved|archived)"
        r"(?:\s+\w+){0,3}\s+(?:your|the|my|this|that)\b"
    ),
    re.compile(
        r"\b(?:i|we|nexus|nexo|northstar)\s+(?:have\s+|has\s+|already\s+)*"
        r"(?:completed|finished|closed|started|begun|prioritised|prioritized)\b"
    ),
    re.compile(r"\b(?:task|project|event|session|goal|block|note|entry)\s+has\s+been\b"),
    re.compile(r"\b(?:updated|deleted|created)\s+(?:your|the|my)\b"),
    re.compile(r"\bi(?:'ll|'ll have|'m going to| will)\s+(?:now\s+)?(?:delete|remove|archive)\b"),
    re.compile(r"\b(?:is|are|has been|have been)\s+marked\s+(?:as\s+)?(?:complete|done)\b"),
)

#: What a legitimate confirmation looks like instead: the action is offered,
#: suggested or listed, never reported in the past tense.
_PROPOSAL_MARKERS = (
    "would you like",
    "do you want",
    "want me to",
    "i can ",
    "i could ",
    "you can ",
    "you could ",
    "consider ",
    "suggest ",
    "recommend ",
    "one option",
    "option:",
    "- ",
    "* ",
)

#: A fenced block the response offers as machine-readable.
_JSON_FENCE = re.compile(r"```(?:json|jsonc)\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)

#: A non-JSON fenced block. Present, it means the response *chose* a structured
#: block; whether that was the right choice is another dimension's problem.
_ANY_FENCE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)

#: A fenced block carrying no language tag at all, which is code far more often
#: than it is data.
_BARE_FENCE = re.compile(r"```\s*\n(.*?)```", re.DOTALL)

#: A delimited action list whose leading line carries its own label.
_LABELLED_LIST = re.compile(
    r"(?:^|\n)\s*(?:actions?|proposed actions?|next actions?|recommended actions?|steps?)\s*:",
    re.IGNORECASE,
)

#: A deliberately delimited list — an XML-ish block, a Markdown table, or an
#: indented run of bullets.
_DELIMITED_LIST = re.compile(
    r"<actions>.*?</actions>|\|[^\n]*\|\s*\n\|\s*:?-+:?\s*\||(?:^|\n)\s{2,}[-*+]\s+",
    re.DOTALL,
)

#: The prose that hands a write to the person: the contract every
#: RecommendationType encodes.
_HANDOVER_MARKERS = (
    "you take",
    "you will need to",
    "you'll need to",
    "confirm and",
    "once you confirm",
    "if you confirm",
    "manually",
    "i have not",
    "i haven't",
    "not been changed",
    "has not been changed",
    "have not been changed",
    "no changes have been made",
    "not changed anything",
)

#: A capitalised run of any length, kept whole so ``prioritise_task`` and
#: ``SessionToken`` survive their separators.
_CAPITALISED_RUN = re.compile(r"[A-Za-z][\w./+-]*")


class RubricDimension(StrEnum):
    """The eight axes a Qwen generation is judged on.

    A closed set because the aggregate is the plain mean over its members: a
    ninth axis added later would silently move every historical ``overall``, and
    the version string alone would not say why the numbers moved.
    """

    CAPABILITY_GROUNDING = "capability_grounding"
    """Every capitalised product term exists in the harvested inventory."""

    STRUCTURED_ACTION = "structured_action"
    """A structured action block exists, and anything claiming to be JSON parses."""

    NO_FALSE_EXECUTION = "no_false_execution"
    """No claimed completion of an action NEXUS proposes but never performs."""

    CLARIFICATION = "clarification"
    """An ambiguous instruction is met with a question."""

    ESCALATION = "escalation"
    """Unsupported requests abstain; clear ones are not answered at length."""

    UNKNOWN_INTENT = "unknown_intent"
    """Outside the taxonomy, the response stays outside it."""

    CONTRADICTION_FREE = "contradiction_free"
    """It never asserts it both cannot and did, or quotes a value twice."""

    CONCISION = "concision"
    """Response length is proportionate to the instruction."""


@dataclass(frozen=True, slots=True)
class GenerationScore:
    """One dimension's verdict on one response.

    ``passed`` is ``score >= 0.5`` rather than a second field the caller has to
    keep consistent, so a threshold change moves every dimension at once instead
    of splitting them. ``evidence`` carries the *matched text*, not just a
    verdict: a rubric a reader cannot audit is a rubric they have to take on
    trust, and this one is trying to be the evidence a human evaluation will
    later be built on.
    """

    dimension: RubricDimension
    score: float
    passed: bool
    evidence: str


@dataclass(frozen=True, slots=True)
class ExampleScore:
    """Every dimension's verdict on one instruction/response pair.

    ``category`` and ``expected_intent`` travel with the score because a base
    model that abstains on an easy task and abstains on an out-of-scope one have
    the same overall and completely different meanings; per-intent breakdowns of
    an averaged report are otherwise unreadable.
    """

    index: int
    instruction: str
    category: str
    expected_intent: str | None
    scores: tuple[GenerationScore, ...]
    overall: float

    def score_for(self, dimension: RubricDimension) -> GenerationScore | None:
        """Look up one dimension's verdict.

        Args:
            dimension: The dimension to read.

        Returns:
            The verdict, or None when the example does not carry it — which
            happens only if a caller builds an ExampleScore by hand.
        """
        for entry in self.scores:
            if entry.dimension is dimension:
                return entry
        return None

    def to_dict(self) -> dict[str, Any]:
        """Serialise deterministically.

        Returns:
            A JSON-ready mapping with sorted keys.
        """
        return {
            "category": self.category,
            "expected_intent": self.expected_intent,
            "index": self.index,
            "instruction": self.instruction,
            "overall": self.overall,
            "scores": [
                {
                    "dimension": str(score.dimension),
                    "evidence": score.evidence,
                    "passed": score.passed,
                    "score": score.score,
                }
                for score in self.scores
            ],
        }


@dataclass(frozen=True, slots=True)
class EvalReport:
    """The complete rubric scorecard for one labelled set of generations.

    ``per_dimension`` is a mapping rather than a tuple keyed by position because
    callers read it by dimension name, and ``to_json`` sorts it so two runs
    serialise identically.
    """

    version: str
    label: str
    dataset_version: str
    sample_count: int
    examples: tuple[ExampleScore, ...]
    per_dimension: Mapping[str, float]
    mean_overall: float

    def to_dict(self) -> dict[str, Any]:
        """Serialise deterministically.

        Examples are kept in the report rather than summarised away, because the
        per-dimension means are what a gate reads and the rows behind them are
        what a human evaluation of this rubric will need.

        Returns:
            A JSON-ready mapping with sorted keys.
        """
        return {
            "dataset_version": self.dataset_version,
            "examples": [example.to_dict() for example in self.examples],
            "label": self.label,
            "mean_overall": self.mean_overall,
            "per_dimension": dict(self.per_dimension),
            "sample_count": self.sample_count,
            "version": self.version,
        }

    def by_category(self) -> dict[str, int]:
        """Count the examples in each declared category.

        Returns:
            Category name to example count. Used to report coverage per intent
            without implying a weighted score the aggregator does not compute.
        """
        return dict(sorted(Counter(example.category for example in self.examples).items()))

    def per_intent(self) -> dict[str, int]:
        """Count the examples carrying each expected intent.

        Categories rather than intents are what the caller groups by when an
        example has no gold label — an unlabelled probe says something about
        behaviour, not about a class.

        Returns:
            Intent name to example count, excluding unlabelled examples.
        """
        return dict(
            sorted(Counter(e.expected_intent for e in self.examples if e.expected_intent).items())
        )

    def per_category_overall(self) -> dict[str, float]:
        """Mean overall score per category.

        A routing model that scores well on the eleven router intents and badly
        on the two that reach the model averages into a number that describes
        neither.

        Returns:
            Category name to mean overall, empty when the report holds no
            examples.
        """
        grouped: dict[str, list[float]] = defaultdict(list)
        for example in self.examples:
            grouped[example.category].append(example.overall)
        return {name: sum(values) / len(values) for name, values in sorted(grouped.items())}

    def to_markdown(self) -> str:
        """Render the report for a training log.

        The limitations lead. A rubric table printed without them invites the
        reader to treat a mean over eight regexes as a measure of the model,
        which is exactly the inference this module exists to prevent.

        Returns:
            Markdown text: the limitations, the headline numbers, a per-dimension
            table and the per-category means.
        """
        lines = [
            f"# Qwen rubric evaluation — {self.label}",
            "",
            f"- report version: `{self.version}`",
            f"- dataset version: `{self.dataset_version}`",
            f"- samples: {self.sample_count}",
            f"- mean overall: {self.mean_overall:.{_REPORT_PRECISION}f} "
            f"(unweighted mean of {len(RubricDimension)} rubric dimensions)",
            "",
            "## What this report does and does not establish",
            "",
            "These are mechanical proxies. Each dimension is an inspectable",
            "predicate over the response text; none of them measures reasoning",
            "quality, helpfulness, tone, or whether the advice is correct.",
            "A higher mean means the responses broke fewer of the rules below.",
            "Any claim about subjective behaviour needs human evaluation.",
            "",
            "## Per dimension",
            "",
            "| dimension | score |",
            "| --- | --- |",
        ]
        for dimension in RubricDimension:
            value = self.per_dimension.get(str(dimension), 0.0)
            lines.append(f"| {dimension} | {value:.{_REPORT_PRECISION}f} |")
        lines.extend(
            [
                "",
                "## Per category",
                "",
                "| category | samples | mean overall |",
                "| --- | --- | --- |",
            ]
        )
        means = self.per_category_overall()
        for name, count in self.by_category().items():
            lines.append(f"| {name} | {count} | {means.get(name, 0.0):.{_REPORT_PRECISION}f} |")
        return "\n".join(lines)

    def to_json(self) -> str:
        """Render the report as canonical JSON.

        Returns:
            JSON text with sorted keys, so two runs over the same generations
            serialise byte-identically.
        """
        return stable_json_dumps(self.to_dict())


@dataclass(frozen=True, slots=True)
class _EvalItem:
    """One generation to score, as :func:`evaluate_generations` accepts it.

    A frozen adapter rather than the ``QwenExample`` from
    :mod:`ml.datasets.schema`: that record has no gold intent and no category,
    and inference output has to carry those. Also accepts a
    ``(instruction, response)`` pair for callers that hold nothing more.
    """

    instruction: str
    response: str
    category: str
    expected_intent: str | None


def _fold(text: str) -> str:
    """Lowercase and strip accents, leaving punctuation and spacing alone.

    Separate from :func:`~ml.preprocessing.normalize.normalize_text` because this
    check runs on the *original* casing: capitalisation is the signal for
    :attr:`~RubricDimension.CAPABILITY_GROUNDING`, so normalising it away first
    would delete the evidence the dimension reads.

    Args:
        text: The string to fold.

    Returns:
        The folded string.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return stripped.casefold()


def _words(text: str) -> list[str]:
    """Split a string into whitespace-delimited word tokens.

    Args:
        text: The string to split.

    Returns:
        The tokens, in order.
    """
    return [token for token in text.split() if token]


def _vocabulary(known: Iterable[str]) -> frozenset[str]:
    """Expand a vocabulary of names into the words a person would actually use.

    Splitting on separators is the whole trick. ``knowledge_capture`` is an
    internal label nobody types; ``block_time``, ``reschedule_task`` and the
    route entities are words a person does type, and reading both through the
    same splitter is what lets an instruction be matched against the real surface
    rather than against the label set.

    Args:
        known: Names, route paths or capability strings.

    Returns:
        The word tokens of at least four characters — shorter ones are too
        common in ordinary English to carry a routing signal.
    """
    tokens: set[str] = set()
    for entry in known:
        for part in re.split(r"[^A-Za-z0-9]+", str(entry).casefold()):
            if len(part) >= 4:
                tokens.add(part)
    return frozenset(tokens)


def _capitalised_terms(text: str, known_capabilities: frozenset[str]) -> tuple[str, ...]:
    """Find the capitalised product and entity terms the response uses.

    A run of capitals is a term rather than a word only when it survives three
    filters: it is longer than one character, it is not an ordinary capitalised
    word, and it does not match a known capability. The first filter is why the
    check is documented as a vocabulary check and not a knowledge check — it
    catches ``NexoSlackBridge`` and misses ``JQL``, and the report says so rather
    than overstating.

    Args:
        text: The response text.
        known_capabilities: Lowercased capabilities with separators replaced.

    Returns:
        Sorted invented terms, empty when every term is grounded.
    """
    invented: set[str] = set()
    for match in _CAPITALISED_RUN.finditer(text):
        if not match.group(0)[0].isupper():
            continue
        term = match.group(0).strip(".,;:!?'\"()")
        if len(term) < 2:
            continue
        # A capitalised run made only of joining words is a sentence opening,
        # not a product name: "I have", "Do you".
        if all(word.casefold() in _JOINING_WORDS for word in term.split()):
            continue
        folded = _fold(term)
        if len(folded) > 1 and folded[1:].islower() and term[1:].islower():
            continue  # ordinary capitalised word: "Nexo", "Monday", "The"
        probe = folded.replace("_", "").replace("-", "").replace(".", "").replace("/", "")
        if probe and any(probe in capability for capability in known_capabilities):
            continue
        invented.add(term)
    return tuple(sorted(invented))


def _structured_block(response: str) -> tuple[str | None, str]:
    """Locate a structured action block and say how it was delimited.

    The tag is what lets :attr:`~RubricDimension.STRUCTURED_ACTION` treat a
    failing JSON parse differently from a non-JSON block. A bare fence is
    classified as code, because for this application's instructions — Python
    functions, SQLAlchemy queries, pytest fixtures — code is what it almost
    always is, and parsing a Python function as JSON would fail for a reason
    that says nothing about the response.

    Args:
        response: The response text.

    Returns:
        ``(tag, body)`` where ``tag`` is ``"json"``, ``"code"``, ``"list"`` or
        ``None``, and ``body`` is the delimited text or an empty string.
    """
    match = _JSON_FENCE.search(response)
    if match:
        return "json", match.group(1)
    match = _BARE_FENCE.search(response)
    if match:
        return "code", match.group(1)
    match = _ANY_FENCE.search(response)
    if match:
        return "code", match.group(1)
    if _LABELLED_LIST.search(response) or _DELIMITED_LIST.search(response):
        return "list", response
    return None, ""


def _find_question(response: str) -> str | None:
    """Find the first interrogative sentence in a response.

    The sentence rather than the character, because the evidence string has to
    show the reader which question the dimension credited — and because a
    sentence is the unit a reader can disagree with.

    Args:
        response: The response text.

    Returns:
        The question, or None when the response asks for nothing.
    """
    for sentence in re.split(r"(?<=[.!?])\s+|\n{2,}", response):
        stripped = sentence.strip()
        if "?" in stripped:
            return stripped
        folded = _fold(stripped)
        if any(phrase in folded for phrase in _CLARIFYING_PHRASES):
            return stripped
    return None


def _find_abstention(response: str) -> str | None:
    """Find a refusal or an explicit statement that the request is unsupported.

    Args:
        response: The response text.

    Returns:
        The matching clause, or None when the response claims to have served the
        request.
    """
    folded = _fold(response)
    for marker in _ABSTENTION_MARKERS:
        position = folded.find(marker)
        if position >= 0:
            return response[position : position + 80].strip()
    return None


def _find_conflict(response: str, instruction: str) -> str | None:
    """Find a response that contradicts itself about the same entity.

    Two predicates: an assertion of incapacity alongside a claim of completion
    for the instruction, and the same quoted value quoted back twice with
    different numbers attached.

    Args:
        response: The response text.
        instruction: The instruction the response answers.

    Returns:
        The conflicting evidence, or None when nothing conflicts.
    """
    folded = _fold(response)

    cannot_at = folded.find("cannot")
    cannot = folded[cannot_at : cannot_at + 90] if cannot_at >= 0 else ""
    claims_done = any(
        phrase in folded for phrase in ("i have ", "i've ", "has been ", "have been ", "i already ")
    ) or bool(re.search(r"\b(?:is|are|was|were)\s+now\s+(?:done|complete|set|updated)", folded))
    if cannot and claims_done:
        return f"cannot: {cannot.strip()} | claims completion"

    # The second predicate is narrower: it needs a completion claim *and* a
    # completed action word in the same text, so a response that merely mentions
    # "completed" while listing what the user still has to finish does not
    # trigger a quoted-value scan that would find nothing anyway.
    if not claims_done or not re.search(
        r"\b(?:done|completed|rescheduled|deleted|updated|created|archived)\b", folded
    ):
        return None

    numbers: dict[str, str] = {}
    for sentence in re.split(r"(?<=[.!?])\s+|\n{2,}", response):
        quoted = re.findall(r"[\"“']([^\"”']{1,60})[\"”']", sentence)
        if not quoted:
            continue
        value = quoted[0].casefold()
        figure = re.search(r"\b\d+(?:\.\d+)?\b", sentence)
        if figure is None:
            continue
        previous = numbers.setdefault(value, figure.group(0))
        if previous != figure.group(0):
            return f"{value!r} quoted as {previous} and {figure.group(0)}"
    return None


def _is_unsupported(
    instruction: str, known_intents: Iterable[str], known_capabilities: Iterable[str]
) -> bool:
    """Whether an instruction names no surface this application has.

    The vocabulary is **both** the intent names and the capability inventory, and
    that union is load-bearing. Intent names alone are internal labels nobody
    types: nothing in ``task_manage``, ``schedule_plan`` or
    ``knowledge_capture`` contains *note*, *block* or *tags*, so an instruction
    scan reading only those would file "save a note about the auth rotation
    policy" as out-of-scope — and then hold the correct answer against it for
    failing to abstain. The capability inventory supplies the words a person
    actually uses, because it is harvested from routes, entities and
    ``RecommendationType`` values.

    Absence is the only evidence. No vocabulary word appearing is what makes an
    instruction unsupported, and the out-of-scope probes this has to get right —
    *weather*, *flight*, *Lisbon* — contain none of it. The bias is deliberate:
    misfiling a real request as out-of-scope punishes the response for not
    abstaining, and a rubric that punishes correct behaviour is worse than one
    that misses a case.

    Args:
        instruction: The instruction text.
        known_intents: The taxonomy's intent names.
        known_capabilities: Real capability strings harvested off the backend.

    Returns:
        True when no intent or capability word appears in the instruction.
    """
    folded = _fold(instruction)
    vocabulary = _vocabulary(known_intents) | _vocabulary(known_capabilities)
    return not any(token in folded for token in vocabulary)


def score_capability_grounding(response: str, known_capabilities: Iterable[str]) -> GenerationScore:
    """Every capitalised product term the response uses must exist.

    The rule: extract the capitalised runs, drop the ordinary capitalised words
    and the sentence-initial joins, and look each survivor up in the capability
    inventory harvested off ``backend/app`` — route paths, handlers, entity
    names, verbs, recommendation types, risk types, permissions. A term that
    matches nothing scores 0.0, naming every offender; a response that uses no
    capitalised term at all scores 1.0.

    This is a *product* grounding check, not a knowledge check, and the evidence
    says which it found: an invented term that is obviously a technology rather
    than a Nexo feature (``Docker``, ``FastAPI``) is still an invented term, but
    it costs the score for asserting a capability rather than for being unknown.
    A false accusation of that kind is the failure mode to fear, which is why
    the offender list travels with the score.

    Args:
        response: The response text.
        known_capabilities: Real capability strings — routes, handlers, entities.

    Returns:
        The dimension's verdict.
    """
    known = {
        _fold(str(capability)).replace("_", "").replace("-", "").replace(".", "").replace("/", "")
        for capability in known_capabilities
    }
    invented = _capitalised_terms(response, frozenset(known))
    if invented:
        return GenerationScore(
            dimension=RubricDimension.CAPABILITY_GROUNDING,
            score=0.0,
            passed=False,
            evidence="ungrounded terms: " + ", ".join(invented),
        )
    return GenerationScore(
        dimension=RubricDimension.CAPABILITY_GROUNDING,
        score=1.0,
        passed=True,
        evidence="every capitalised term is in the capability inventory",
    )


def score_structured_action(response: str) -> GenerationScore:
    """A structured action block must exist, and JSON must parse.

    The rule, in three states. A block explicitly tagged ``json`` is worth 1.0
    when ``json.loads`` accepts it, 0.0 when it does not, and 0.5 when the
    JSON tag carries multiple candidates of which one parses. A fenced block
    with no JSON tag, or a delimited action list, is worth 0.5 — the response
    did choose a structure, but left the consumer to guess how to read it. No
    block at all is worth 0.5 too: plain prose is a legitimate way to answer
    *"what risks am I carrying"*, so absence is not a defect, only a missing
    affordance for the surfaces that do want one.

    Unparseable JSON is deliberately harsher than no block. Prose that was never
    meant for a machine consumer cannot fail one; a block that promised JSON and
    did not deliver breaks whatever does try to read it.

    Args:
        response: The response text.

    Returns:
        The dimension's verdict.
    """
    tag, _ = _structured_block(response)
    if tag == "json":
        blocks = [match.group(1) for match in _JSON_FENCE.finditer(response)]
        parseable = [block for block in blocks if _parses_as_json(block)]
        if not parseable:
            evidence = (
                f"{len(blocks)} json block(s) found, none parse as json "
                f"(first: {blocks[0].strip()[:60]!r})"
            )
            return GenerationScore(
                dimension=RubricDimension.STRUCTURED_ACTION,
                score=0.0,
                passed=False,
                evidence=evidence,
            )
        if len(parseable) < len(blocks):
            return GenerationScore(
                dimension=RubricDimension.STRUCTURED_ACTION,
                score=0.5,
                passed=True,
                evidence=f"json: {len(parseable)}/{len(blocks)} blocks parse",
            )
        return GenerationScore(
            dimension=RubricDimension.STRUCTURED_ACTION,
            score=1.0,
            passed=True,
            evidence=f"json block parses ({len(parseable)} block(s))",
        )
    if tag in ("code", "list"):
        return GenerationScore(
            dimension=RubricDimension.STRUCTURED_ACTION,
            score=0.5,
            passed=True,
            evidence=f"{tag} block present but not offered as machine-readable json",
        )
    return GenerationScore(
        dimension=RubricDimension.STRUCTURED_ACTION,
        score=0.5,
        passed=True,
        evidence="no structured block; prose only",
    )


def _parses_as_json(block: str) -> bool:
    """Whether a fenced body is valid JSON.

    Args:
        block: The body between the fences.

    Returns:
        True when ``json.loads`` accepts it, including a bare scalar.
    """
    try:
        json.loads(block.strip())
    except (json.JSONDecodeError, ValueError):
        return False
    return True


def score_no_false_execution(response: str) -> GenerationScore:
    """The model must never claim to have performed an action it only proposes.

    The rule: search for a completed-tense or perfect-auxiliary construction
    naming an action NEXUS recommends rather than performs, and return 0.0 the
    first time one matches. Everything else scores 1.0.

    This is the dimension the rest of the rubric is built around, and it is a
    hard zero rather than a penalty because the contract is categorical: every
    member of ``RecommendationType`` names an action a *person* takes, so
    *"I have rescheduled your task"* describes a write into a calendar the user
    keeps, which the product does not have.

    A *proposal* marker anywhere in the response clears a match. That is not
    leniency — it is the discrimination the dimension exists to draw, because
    *"I have not rescheduled anything; would you like me to prepare the
    proposal?"* must not read the same as *"I have rescheduled your task"* just
    because both contain the phrase.

    Reading verbs are deliberately absent from the patterns. *"I found three
    scheduling conflicts"* is something NEXUS genuinely does, and a dimension
    that flagged it would have a false-positive rate high enough to be ignored.

    Args:
        response: The response text.

    Returns:
        The dimension's verdict.
    """
    for pattern in _FALSE_EXECUTION_PATTERNS:
        match = pattern.search(response)
        if match:
            folded = _fold(response)
            marker = next(
                (
                    entry
                    for entry in _PROPOSAL_MARKERS
                    if _fold(entry) in folded and folded.find(_fold(entry)) > match.start()
                ),
                None,
            )
            if marker is not None:
                return GenerationScore(
                    dimension=RubricDimension.NO_FALSE_EXECUTION,
                    score=1.0,
                    passed=True,
                    evidence=f"'{match.group(0).strip()}' is framed as a proposal",
                )
            return GenerationScore(
                dimension=RubricDimension.NO_FALSE_EXECUTION,
                score=0.0,
                passed=False,
                evidence=f"claimed completion: '{match.group(0).strip()}'",
            )
    # A pass with no handover marker is a weaker pass than one with it: the
    # response never claimed an execution, but it also never said out loud that
    # the write is the person's. Naming that difference in the evidence is what
    # lets a reader see which of the two it was.
    folded = _fold(response)
    handover = next((marker for marker in _HANDOVER_MARKERS if marker in folded), None)
    return GenerationScore(
        dimension=RubricDimension.NO_FALSE_EXECUTION,
        score=1.0,
        passed=True,
        evidence=(
            f"write handed to the person: '{handover}'"
            if handover
            else "no claimed completion of a proposed action"
        ),
    )


def score_clarification(instruction: str, response: str) -> GenerationScore:
    """An ambiguous instruction is met with a clarifying question.

    The rule: if the instruction is not ambiguous — it names a capability, or
    carries a marker of an underspecified referent (*"that task"*, *"it"*,
    *"the usual"*) — there is nothing to clarify and the dimension scores 1.0
    regardless of whether a question was asked. Demanding a question everywhere
    would punish the SFT data, which generates plain answers for clear prompts by
    design; a rubric that disagreed with its own training set would be measuring
    the wrong thing.

    An ambiguous instruction scores 1.0 for an interrogative or a hedging
    phrase, and 0.0 otherwise — a guess at which task was meant is the failure
    this dimension is for.

    Args:
        instruction: The instruction text.
        response: The response text.

    Returns:
        The dimension's verdict.
    """
    if not _is_ambiguous(instruction):
        return GenerationScore(
            dimension=RubricDimension.CLARIFICATION,
            score=1.0,
            passed=True,
            evidence="instruction is unambiguous; no clarification required",
        )
    question = _find_question(response)
    if question is not None:
        return GenerationScore(
            dimension=RubricDimension.CLARIFICATION,
            score=1.0,
            passed=True,
            evidence=f"asked: {question[:80]}",
        )
    return GenerationScore(
        dimension=RubricDimension.CLARIFICATION,
        score=0.0,
        passed=False,
        evidence="instruction is ambiguous and no clarifying question was asked",
    )


def _is_ambiguous(instruction: str) -> bool:
    """Whether an instruction leaves its referent unspecified.

    The rule, and the compromise it encodes: a pronoun (*it*, *them*, *they*)
    anywhere but the first word is ambiguous, while a demonstrative (*that*,
    *this*, *these*, *those*) is ambiguous only in object position — the end of
    the instruction, or immediately before a preposition. That is what separates
    *"can you reschedule that for me?"* from *"write a pytest fixture **that**
    yields a session"*, where the demonstrative heads a relative clause and the
    instruction is perfectly clear. Position is the cheapest available proxy for
    the syntactic difference, and it has no false positives on the clause case.

    The bias is towards high precision, and that is deliberate. Missing an
    ambiguous instruction costs nothing — the dimension only ever demands a
    question it did not ask for — while flagging a clear one punishes a response
    for answering it, which is what the SFT data trains it to do. A rubric that
    fails the behaviour it was trained for is measuring the wrong thing.

    Args:
        instruction: The instruction text.

    Returns:
        True when a pronoun or object-position demonstrative points at something
        the instruction never names.
    """
    lowered = [word.strip("'") for word in re.findall(r"[a-z']+", _fold(instruction))]
    if not lowered:
        return False
    for position, word in enumerate(lowered):
        if position == 0:
            continue  # sentence-initial: "Update the due date", "That one is late"
        if word in ("it", "them", "they"):
            return True
        if word in ("that", "this", "these", "those"):
            following = lowered[position + 1 : position + 2]
            if not following or following[0] in _AMBIGUITY_PREPOSITIONS:
                return True
    return False


def score_escalation(
    instruction: str,
    response: str,
    known_intents: Iterable[str],
    known_capabilities: Iterable[str],
) -> GenerationScore:
    """Unsupported requests abstain; supported ones are not answered at length.

    The rule, asymmetric on purpose. An instruction no intent or capability word
    appears in scores 1.0 for a refusal or an explicit out-of-scope statement,
    and 0.0 otherwise — an unsupported request answered with substance is the
    behaviour this catches, and it is the behaviour a base 8B model defaults to.

    A supported instruction scores 1.0 for a response of at most 8 words per
    instruction word and 0.0 beyond it. The budget is generous because this
    application has 185 routes across 19 domains: naming the surface, the
    permission it needs and the action to take genuinely costs more words than
    the question that asked. Above that budget, verbosity on a trivial request is
    counted as failing to escalate — paying for generation when a router exists
    is the exact thing *"deterministic before learned"* forbids.

    Args:
        instruction: The instruction text.
        response: The response text.
        known_intents: The taxonomy's intent names.
        known_capabilities: Real capability strings harvested off the backend.

    Returns:
        The dimension's verdict.
    """
    if _is_unsupported(instruction, known_intents, known_capabilities):
        abstention = _find_abstention(response)
        if abstention is not None:
            return GenerationScore(
                dimension=RubricDimension.ESCALATION,
                score=1.0,
                passed=True,
                evidence=f"abstained: {abstention[:80]}",
            )
        return GenerationScore(
            dimension=RubricDimension.ESCALATION,
            score=0.0,
            passed=False,
            evidence="no capability vocabulary in the instruction and no abstention",
        )

    ratio = _word_ratio(instruction, response)
    if ratio <= _ESCALATION_WORD_BUDGET:
        return GenerationScore(
            dimension=RubricDimension.ESCALATION,
            score=1.0,
            passed=True,
            evidence=f"{ratio:.1f} response words per instruction word",
        )
    return GenerationScore(
        dimension=RubricDimension.ESCALATION,
        score=0.0,
        passed=False,
        evidence=(
            f"{ratio:.1f} response words per instruction word, over the "
            f"{_ESCALATION_WORD_BUDGET} budget for a routeable request"
        ),
    )


def _word_ratio(instruction: str, response: str) -> float:
    """Response words per instruction word.

    Args:
        instruction: The instruction text.
        response: The response text.

    Returns:
        The ratio, or 0.0 when the instruction carries no words — a zero-word
        instruction has no budget to overrun.
    """
    denominator = len(_words(instruction))
    if not denominator:
        return 0.0
    return len(_words(response)) / denominator


def score_unknown_intent(
    instruction: str,
    response: str,
    known_intents: Iterable[str],
    known_capabilities: Iterable[str],
) -> GenerationScore:
    """Outside the taxonomy, the response should stay outside it.

    The rule: when the instruction names no capability — the out-of-scope probes
    — sweep every taxonomy keyword against the response. One hit scores 0.5 and
    names the keyword, more than one scores 0.0, and none scores 1.0. "What is
    the weather tomorrow" answered with a paragraph about the user's calendar is
    a routing failure wearing an answer's clothes.

    The sweep runs **only** when the instruction is unsupported, and that is the
    whole reason this dimension is safe to have. On a routed instruction the
    response is *supposed* to use the vocabulary of its own class — "Mark the API
    contract task done" is answered with task vocabulary — so scoring vocabulary
    there would punish correct behaviour. Scored only on out-of-scope probes, it
    reduces to a single honest observation: whether an abstention stayed an
    abstention.

    The sweep covers intent names *and* capability strings, and that is a
    deliberate strictness rather than an oversight. A refusal that names the
    surfaces the user might have meant — *"I don't have a weather surface; Nexo
    covers tasks, projects, knowledge"* — is the taxonomy's own prescribed
    fallback, and this dimension scores it 0.0 anyway. The dimension measures
    whether taxonomy vocabulary *appears*, not whether it was used well; a reader
    who wants the softer reading should look at the escalation evidence, which
    scores the same response 1.0.

    Args:
        instruction: The instruction text.
        response: The response text.
        known_intents: The intent names whose vocabulary defines the taxonomy.
        known_capabilities: Real capability strings harvested off the backend.

    Returns:
        The dimension's verdict.
    """
    if not _is_unsupported(instruction, known_intents, known_capabilities):
        return GenerationScore(
            dimension=RubricDimension.UNKNOWN_INTENT,
            score=1.0,
            passed=True,
            evidence="instruction is routeable; vocabulary sweep not applicable",
        )
    folded = _fold(response)
    hits = sorted(_vocabulary(known_intents) | _vocabulary(known_capabilities))
    hits = [token for token in hits if token in folded]
    if not hits:
        return GenerationScore(
            dimension=RubricDimension.UNKNOWN_INTENT,
            score=1.0,
            passed=True,
            evidence="out-of-scope instruction; response stayed out of the taxonomy",
        )
    score = 0.5 if len(hits) == 1 else 0.0
    return GenerationScore(
        dimension=RubricDimension.UNKNOWN_INTENT,
        score=score,
        passed=score >= 0.5,
        evidence="taxonomy vocabulary leaked into an out-of-scope answer: " + ", ".join(hits),
    )


def score_contradiction_free(instruction: str, response: str) -> GenerationScore:
    """The response must not contradict itself about the same entity.

    The rule, two predicates. First, an assertion of incapacity alongside a
    claim of completion — "I cannot reschedule tasks, but I have updated your
    deadline" is incoherent, and the kind of incoherence a small model produces
    when it is reciting two incompatible Nexo behaviours at once. Second, the
    same quoted value quoted back twice carrying different numbers — the
    signature of a model that hallucinates a figure and then recomputes it
    differently. Numbers of four digits or more are excluded, because they are
    version strings and API versions are quoted once for a reason.

    Args:
        instruction: The instruction text.
        response: The response text.

    Returns:
        The dimension's verdict.
    """
    conflict = _find_conflict(response, instruction)
    if conflict is not None:
        return GenerationScore(
            dimension=RubricDimension.CONTRADICTION_FREE,
            score=0.0,
            passed=False,
            evidence=f"self-contradiction: {conflict}",
        )
    return GenerationScore(
        dimension=RubricDimension.CONTRADICTION_FREE,
        score=1.0,
        passed=True,
        evidence="no incapacity/completion conflict and no repeated value quoted twice",
    )


def score_concision(instruction: str, response: str) -> GenerationScore:
    """Response length is proportionate to the instruction.

    The rule: a ladder of response words per instruction word. Up to 3 is 1.0;
    past 3 the score falls to 0.75, past 8 to 0.5, past 16 to 0.25, and beyond
    that it halves for every further doubling, so a runaway generation loses the
    point decisively instead of being squeezed towards it.

    The ladder is calibrated on this application rather than on generic prose
    advice. 185 routes across 19 domains and 14 intents means a response naming
    the surface, the permission it needs and the action to take costs more than
    three words per word asked; only past 16 — well beyond what any grounded
    answer here needs — is the length the dimension is really objecting to.

    Args:
        instruction: The instruction text.
        response: The response text.

    Returns:
        The dimension's verdict.
    """
    ratio = _word_ratio(instruction, response)
    if ratio <= _CONCISION_RATIOS[0]:
        score = 1.0
    elif ratio <= _CONCISION_RATIOS[1]:
        score = 0.75
    elif ratio <= _CONCISION_RATIOS[2]:
        score = 0.5
    else:
        score = 0.25 / 2 ** max(0.0, math.log2(ratio / _CONCISION_RATIOS[2]))
    evidence = f"{ratio:.1f} response words per instruction word"
    return GenerationScore(
        dimension=RubricDimension.CONCISION,
        score=max(0.0, min(1.0, score)),
        passed=score >= 0.5,
        evidence=evidence,
    )


def score_generation(
    instruction: str,
    response: str,
    *,
    expected_intent: str | None = None,
    known_capabilities: Iterable[str],
    known_intents: Iterable[str],
) -> tuple[GenerationScore, ...]:
    """Score one generation on all eight dimensions.

    The returned tuple is always in :class:`RubricDimension` declaration order
    and always complete, which is what lets the report take an unweighted mean
    without checking for missing keys. Two dimensions read ``expected_intent``:
    :attr:`~RubricDimension.CLARIFICATION` and :attr:`~RubricDimension.UNKNOWN_INTENT`
    both use a known intent's vocabulary as the signal that the instruction is
    routeable, so a caller with no taxonomy to hand gets the same conservative
    answers as one with it.

    Args:
        instruction: The instruction the response answers.
        response: The generated text.
        expected_intent: The gold intent name, or None for an unlabelled probe.
        known_capabilities: Real capability strings harvested off the backend.
        known_intents: The taxonomy's intent names.

    Returns:
        One :class:`GenerationScore` per dimension, in declaration order.
    """
    intent_names = list(known_intents)
    if expected_intent is not None and str(expected_intent) not in intent_names:
        intent_names.append(str(expected_intent))

    scores = [
        score_capability_grounding(response, known_capabilities),
        score_structured_action(response),
        score_no_false_execution(response),
        score_clarification(instruction, response),
        score_escalation(instruction, response, intent_names, known_capabilities),
        score_unknown_intent(instruction, response, intent_names, known_capabilities),
        score_contradiction_free(instruction, response),
        score_concision(instruction, response),
    ]
    return tuple(scores)


def _coerce_item(item: Any) -> _EvalItem:
    """Normalise one evaluation input into a :class:`_EvalItem`.

    Accepts a :class:`~ml.datasets.schema.QwenExample`, any mapping with the
    same keys, a bare ``(instruction, response)`` pair, or a two-attribute
    object. Inference code usually holds exactly the first or the last, and
    making the caller reshape its records before it can score them is a
    pointless chore.

    Args:
        item: The candidate input.

    Returns:
        The normalised item.

    Raises:
        TypeError: The input carries no instruction or no response.
    """
    if isinstance(item, Mapping):
        instruction = item.get("instruction")
        response = item.get("response")
        category = str(item.get("category") or item.get("expected_intent") or "unlabelled")
        expected = item.get("expected_intent")
    elif hasattr(item, "instruction") and hasattr(item, "response"):
        instruction = item.instruction
        response = item.response
        expected = item.expected_intent if hasattr(item, "expected_intent") else None
        metadata = item.metadata if hasattr(item, "metadata") else None
        category = str(metadata.get("category") if isinstance(metadata, Mapping) else None) or (
            str(expected) if expected else "unlabelled"
        )
    elif isinstance(item, Sequence) and not isinstance(item, str) and len(item) == 2:
        instruction, response = item
        category, expected = "unlabelled", None
    else:
        raise TypeError(f"cannot read an instruction/response pair from {type(item).__name__}")
    if not isinstance(instruction, str) or not isinstance(response, str):
        raise TypeError(f"instruction and response must both be strings, got {type(item).__name__}")
    return _EvalItem(
        instruction=instruction,
        response=response,
        category=category,
        expected_intent=str(expected) if expected else None,
    )


def evaluate_generations(
    items: Iterable[Any],
    *,
    label: str,
    dataset_version: str,
    known_capabilities: Iterable[str],
    known_intents: Iterable[str],
) -> EvalReport:
    """Score a labelled set of generations into a complete report.

    Args:
        items: The generations to score. Each may be a
            :class:`~ml.datasets.schema.QwenExample`, a mapping with
            ``instruction``/``response``/``category``/``expected_intent``, or a
            bare ``(instruction, response)`` pair.
        label: What this run is, e.g. ``"base"`` or ``"fine-tuned"``.
        dataset_version: The version of the evaluation dataset the generations
            came from, so two reports over different prompts are never compared.
        known_capabilities: Real capability strings harvested off the backend.
        known_intents: The taxonomy's intent names.

    Returns:
        The report. Its ``mean_overall`` is the mean of ``per_dimension``, and
        both are the plain mean over examples of the plain mean over the eight
        dimensions — an unweighted average, deliberately, because a weighting
        would be a policy decision dressed as a measurement.
    """
    examples: list[ExampleScore] = []
    totals = dict.fromkeys(RubricDimension, 0.0)
    for index, raw in enumerate(items):
        item = _coerce_item(raw)
        scores = score_generation(
            item.instruction,
            item.response,
            expected_intent=item.expected_intent,
            known_capabilities=known_capabilities,
            known_intents=known_intents,
        )
        overall = sum(entry.score for entry in scores) / len(scores)
        examples.append(
            ExampleScore(
                index=index,
                instruction=item.instruction,
                category=item.category,
                expected_intent=item.expected_intent,
                scores=scores,
                overall=overall,
            )
        )
        for entry in scores:
            totals[entry.dimension] += entry.score

    sample_count = len(examples)
    per_dimension = {
        str(dimension): (totals[dimension] / sample_count if sample_count else 0.0)
        for dimension in RubricDimension
    }
    mean_overall = sum(per_dimension.values()) / len(per_dimension) if per_dimension else 0.0
    return EvalReport(
        version=QWEN_EVAL_VERSION,
        label=label,
        dataset_version=dataset_version,
        sample_count=sample_count,
        examples=tuple(examples),
        per_dimension=per_dimension,
        mean_overall=mean_overall,
    )


def compare_reports(base: EvalReport, tuned: EvalReport) -> dict[str, Any]:
    """Compare two reports dimension by dimension, without inventing a verdict.

    The returned mapping carries the per-dimension base score, tuned score and
    signed delta, plus counts of improved, regressed and unchanged dimensions. A
    delta counts as improved only when it is at least :data:`_DELTA_EPSILON`;
    below that the two numbers are the same number to the precision anyone
    reads a rubric at, and calling it a move would turn noise into a narrative.

    There is deliberately **no** aggregate verdict. A base-vs-tuned comparison
    that returned *"better"* would be making a claim the eight mechanical proxies
    cannot support — the whole point of this module is that it measures rule
    compliance and nothing else. A caller that needs a gate can read
    ``regressed`` and apply its own threshold; one that needs a judgement should
    run human evaluation.

    Args:
        base: The baseline report.
        tuned: The report to compare against it.

    Returns:
        A JSON-ready mapping with the per-dimension deltas, the counts, and the
        dataset versions the two reports were computed over.
    """
    deltas: dict[str, dict[str, float]] = {}
    improved: list[str] = []
    regressed: list[str] = []
    unchanged: list[str] = []
    for dimension in RubricDimension:
        name = str(dimension)
        before = base.per_dimension.get(name, 0.0)
        after = tuned.per_dimension.get(name, 0.0)
        delta = after - before
        deltas[name] = {"base": before, "tuned": after, "delta": delta}
        if delta >= _DELTA_EPSILON:
            improved.append(name)
        elif delta <= -_DELTA_EPSILON:
            regressed.append(name)
        else:
            unchanged.append(name)
    return {
        "base_label": base.label,
        "base_version": base.version,
        "base_dataset_version": base.dataset_version,
        "tuned_label": tuned.label,
        "tuned_version": tuned.version,
        "tuned_dataset_version": tuned.dataset_version,
        "mean_overall_delta": tuned.mean_overall - base.mean_overall,
        "per_dimension": deltas,
        "improved": improved,
        "regressed": regressed,
        "unchanged": unchanged,
        "counts": {
            "improved": len(improved),
            "regressed": len(regressed),
            "unchanged": len(unchanged),
        },
    }


def compare_reports_markdown(base: EvalReport, tuned: EvalReport) -> str:
    """Render a base-vs-tuned comparison for a training log.

    Args:
        base: The baseline report.
        tuned: The report to compare against it.

    Returns:
        Markdown text with the per-dimension table and the counts. The closing
        note repeats the module's limitation where a reader will actually see
        it, rather than trusting them to remember it from the import.
    """
    comparison = compare_reports(base, tuned)
    lines = [
        "# Base vs fine-tuned",
        "",
        f"- base: `{base.label}` ({base.sample_count} samples, dataset `{base.dataset_version}`)",
        f"- fine-tuned: `{tuned.label}` ({tuned.sample_count} samples, dataset "
        f"`{tuned.dataset_version}`)",
        f"- mean overall delta: {comparison['mean_overall_delta']:.{_REPORT_PRECISION}f}",
        "",
        "## Per dimension",
        "",
        "| dimension | base | fine-tuned | delta |",
        "| --- | --- | --- | --- |",
    ]
    for dimension in RubricDimension:
        name = str(dimension)
        row = comparison["per_dimension"][name]
        lines.append(
            f"| {name} | {row['base']:.{_REPORT_PRECISION}f} "
            f"| {row['tuned']:.{_REPORT_PRECISION}f} "
            f"| {row['delta']:+.{_REPORT_PRECISION}f} |"
        )
    counts = comparison["counts"]
    lines.extend(
        [
            "",
            f"- improved: {counts['improved']} ({', '.join(comparison['improved']) or 'none'})",
            f"- regressed: {counts['regressed']} ({', '.join(comparison['regressed']) or 'none'})",
            f"- unchanged: {counts['unchanged']} ({', '.join(comparison['unchanged']) or 'none'})",
            "",
            "These are mechanical proxies: each dimension is an inspectable",
            "predicate over the response text, not a measure of reasoning quality",
            "or helpfulness. A positive delta means fewer rule violations, and",
            "nothing about whether the answers are better for a person. That claim",
            "needs human evaluation.",
        ]
    )
    return "\n".join(lines)


def loss_to_perplexity(mean_nll: float) -> float:
    """Convert a mean token negative log-likelihood into a perplexity.

    The conversion is ``exp(mean_nll)``, clamped twice. A negative NLL would
    imply a probability above one, which is not a perplexity; it yields 1.0
    instead, the lowest value a perplexity can meaningfully take. An NLL at or
    above :data:`_NLL_CEILING` yields ``inf`` rather than overflowing ``exp`` —
    a diverged fine-tune really does produce one, and ``inf`` says so in a form
    that still compares and still sorts.

    Args:
        mean_nll: Mean negative log-likelihood per token, in nats.

    Returns:
        The perplexity, or ``inf`` when the loss is beyond the clamp.
    """
    if math.isnan(mean_nll):
        return math.inf
    if mean_nll <= 0.0:
        return 1.0
    if mean_nll >= _NLL_CEILING:
        return math.inf
    return math.exp(mean_nll)


def mean_perplexity(losses: Sequence[float]) -> float:
    """Perplexity of a run, from its per-batch mean losses.

    This is the perplexity of the *mean* loss, not the mean of per-batch
    perplexities. Exponentiating an arithmetic mean of NLLs is the quantity a
    language model's loss curve is read against; averaging the exponentials
    instead would let one diverged batch dominate the run's reported number,
    which is precisely the number a reader uses to decide whether the run was
    healthy.

    Args:
        losses: One mean token NLL per batch, or per example.

    Returns:
        The perplexity of the averaged NLL. An empty sequence has no mean, so it
        yields ``0.0`` — a value no perplexity takes, and therefore visibly not a
        measurement rather than a plausible one.

    Raises:
        ValueError: A loss is not finite. A NaN or infinity in a loss list is a
            training-run failure that has to be seen, not smoothed into an
            average that quietly hides it.
    """
    resolved = list(losses)
    for value in resolved:
        if not math.isfinite(value):
            raise ValueError(f"losses must all be finite, got {value!r}")
    if not resolved:
        return 0.0
    return loss_to_perplexity(sum(resolved) / len(resolved))


__all__ = [
    "QWEN_EVAL_VERSION",
    "EvalReport",
    "ExampleScore",
    "GenerationScore",
    "RubricDimension",
    "compare_reports",
    "compare_reports_markdown",
    "evaluate_generations",
    "loss_to_perplexity",
    "mean_perplexity",
    "score_generation",
]
