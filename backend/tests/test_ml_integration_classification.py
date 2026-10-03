"""What the trained checkpoint actually says when you hand it real sentences.

Phase 10 measured 0.9738 on 420 rows of its own corpus, and said plainly in the
same breath that the corpus is synthetic and template-generated. This module is
the part of Phase 11 that holds the serving path to *that* caveat. Everything
else in the phase — the loader's file checks, the router's threshold policy, the
503 envelope — is testable with fakes. Whether the integrated classifier answers
correctly is not: it can only be answered by the real weights, so this module
loads the real checkpoint and asks it real questions.

Two groups, deliberately not the same thing:

``TestPredictionForEveryIntent``
    At least two hand-written phrasings for each of the fourteen classes, chosen
    to be *representative* — the sentence a competent author would write if asked
    for one. This is the coverage floor. Every class must be reachable through
    the serving path, and a class that no natural sentence reaches is a class the
    router will silently never name. Non-vacuity is enforced structurally:
    ``test_the_example_set_covers_every_intent_at_least_twice`` fails if the
    parametrisation is edited down to one lucky phrasing per class.

``TestGeneralizationToHandWrittenNaturalLanguage``
    The same fourteen classes, phrased the way people actually type: lowercase,
    ALL CAPS, no question mark on a question, "hey so" openers, run-on commas,
    everyday vocabulary instead of the domain nouns the templates lean on. This
    group is allowed to fail, and the ones that do are marked ``xfail`` with the
    measured prediction in the reason rather than edited until they pass —
    rewriting a failing case until it agrees with the model is how a genuine
    integration weakness disappears from the report. The count is the Phase 11
    generalization number and it is not flattering.

Two invariants get dedicated tests because they are the cheapest ways to break
the integration without breaking anything that looks wrong:

* **Raw text reaches the tokenizer.** Phase 10 trained on the untouched ``text``
  column. A ``.lower()`` or a ``strip("?!.")`` added during integration would
  still produce a perfectly well-formed prediction, just scored on a string the
  model has never seen. :class:`_RecordingTokenizer` stands in for the real
  tokenizer and asserts character-for-character equality with the input.
* **The confidence is a probability, not a habit.** One prediction's
  ``confidence`` is recomputed from the model's own logits and compared, so
  "somebody hard-coded 0.97" is answerable by a test rather than by inspection.

The checkpoint under ``backend/ml/artifacts/`` is gitignored and torch is a large
optional install, so the whole module carries the ``ml_model`` marker and skips
with a reason when either is absent.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest

from app.ml.classifier import (
    DEFAULT_ALTERNATIVE_COUNT,
    MAX_UTTERANCE_CHARACTERS,
    IntentClassifier,
)
from app.ml.exceptions import InvalidUtteranceError
from app.ml.model_loader import (
    MAX_SEQUENCE_LENGTH,
    REQUIRED_CHECKPOINT_FILES,
    LoadedModel,
    default_checkpoint_dir,
    load_model,
)
from ml.datasets.taxonomy import INTENT_SPECS, Intent

pytestmark = pytest.mark.ml_model

#: Every intent name the trained head may emit. Asserted rather than assumed: the
#: loader cross-validates the checkpoint's label map against the taxonomy, but
#: that happens at load time and a test that re-derives it is what catches a
#: prediction that escaped the taxonomy entirely.
INTENT_NAMES = frozenset(str(intent) for intent in Intent)

#: The trained context length, restated because the truncation test compares the
#: classifier's flag against the checkpoint's own token count, and a silent change
#: to either would leave the flag permanently False (or permanently True).
EXPECTED_MAX_SEQUENCE_LENGTH = 128

#: How many runner-up classes each prediction carries. Asserted as a length, not
#: just an ordering: "the alternatives are sorted descending and exclude the
#: winner" is trivially true of an empty tuple, so the length is what stops that
#: assertion from being a test that cannot fail.
EXPECTED_ALTERNATIVE_COUNT = DEFAULT_ALTERNATIVE_COUNT

#: Everything this module feeds the model, as ``(intent, utterance)``. Used both
#: to run predictions and to assert none of it was lifted from the Phase 10
#: corpus.
REPRESENTATIVE_EXAMPLES: tuple[tuple[Intent, str], ...] = (
    (Intent.TASK_MANAGE, "Could you strike out the broken printer from my to-do list?"),
    (Intent.TASK_MANAGE, "I finished picking up the dry cleaning, take that off my list"),
    (Intent.PROJECT_MANAGE, "Spin up a separate project just for the garage renovation"),
    (Intent.PROJECT_MANAGE, "Where does the garage renovation stand at the moment?"),
    (Intent.PROJECT_MANAGE, "Who else is working with me on the garage renovation"),
    (Intent.SCHEDULE_PLAN, "I'm free from four o'clock onwards tomorrow, slot the dentist in"),
    (Intent.SCHEDULE_PLAN, "Could you hold Friday afternoon open for the house viewing?"),
    (Intent.SCHEDULE_PLAN, "Move my Thursday dentist appointment to next Wednesday"),
    (Intent.KNOWLEDGE_CAPTURE, "Save a note saying the boiler needs servicing before winter"),
    (Intent.KNOWLEDGE_CAPTURE, "Please write a note that we agreed to go with the smaller room"),
    (Intent.KNOWLEDGE_CAPTURE, "Keep this webpage for me, it's about repairing an old bicycle"),
    (Intent.KNOWLEDGE_LOOKUP, "Can you look up what we saved about the holiday dates?"),
    (Intent.KNOWLEDGE_LOOKUP, "Search my notes for anything about the holiday dates"),
    (Intent.KNOWLEDGE_LOOKUP, "Any idea where I put that note about the bicycle repair?"),
    (Intent.ANALYTICS_INSIGHT, "How many things actually got finished last month?"),
    (Intent.ANALYTICS_INSIGHT, "Am I getting through more work than I was a month ago?"),
    (Intent.RISK_QUERY, "Anything going to bite me before the weekend?"),
    (Intent.RISK_QUERY, "What have I promised too much of?"),
    (Intent.DEVELOPER_INTEL, "Which repositories have I been committing code to lately?"),
    (Intent.DEVELOPER_INTEL, "How many commits did I push this week?"),
    (Intent.DEVELOPER_INTEL, "Summarise the commit activity on the payments repo"),
    (Intent.LEARNING_TRACK, "I keep meaning to practise the violin, how do I get back into it?"),
    (Intent.LEARNING_TRACK, "How much time have I put into the Portuguese course so far?"),
    (Intent.CAREER_TRACK, "Am I building the track record that gets you the senior job?"),
    (Intent.CAREER_TRACK, "What would I still need on my CV to be taken seriously for lead?"),
    (Intent.ACCOUNT_ADMIN, "My emails keep landing at 3am, the clock on my account is wrong"),
    (Intent.ACCOUNT_ADMIN, "Who else is my company letting into my stuff?"),
    (Intent.CODE_ASSIST, "Show me how to write a loop in Ruby that reverses a string"),
    (
        Intent.CODE_ASSIST,
        "This Python program says 'IndexError: list index out of range', what does it mean?",
    ),
    (Intent.DEEP_REASONING, "Argue whether the deterministic engine stays the fallback"),
    (
        Intent.DEEP_REASONING,
        "Weigh up whether to buy or lease the extra floor and justify whichever you pick",
    ),
    (Intent.DEEP_REASONING, "Think through making the scheduler learn every rule"),
    (Intent.OUT_OF_SCOPE, "How do I bake a decent sourdough loaf?"),
    (Intent.OUT_OF_SCOPE, "Did the tortoise actually win the marathon?"),
)

#: Hand-written natural language, four per intent, varying casing, punctuation,
#: sentence structure and register on purpose. Every utterance is either a hit or
#: one of the recorded misses in :data:`GENERALIZATION_MISSES`; none of them has
#: been rewritten since the measurement.
GENERALIZATION_EXAMPLES: tuple[tuple[Intent, str], ...] = (
    (Intent.TASK_MANAGE, "ok mark that done"),
    (Intent.TASK_MANAGE, "MARK IT COMPLETE!!!"),
    (
        Intent.TASK_MANAGE,
        "hey so I finally got round to that dentist appointment, strike it off my list",
    ),
    (Intent.TASK_MANAGE, "the laundry's been hung up and put away so tick it off would you"),
    (Intent.PROJECT_MANAGE, "can you spin up something for the kitchen extension"),
    (Intent.PROJECT_MANAGE, "the bathroom project - where's that at now?"),
    (Intent.PROJECT_MANAGE, "i'd like the old website shut down and put in storage"),
    (Intent.PROJECT_MANAGE, "who else is on the kitchen extension with me"),
    (Intent.SCHEDULE_PLAN, "free after 3 thursday, book the vet in"),
    (Intent.SCHEDULE_PLAN, "could you shove the standup onto tomorrow morning instead"),
    (Intent.SCHEDULE_PLAN, "what did my day look like on the 14th"),
    (Intent.SCHEDULE_PLAN, "put three hours aside on friday for writing up the results"),
    (Intent.KNOWLEDGE_CAPTURE, "write this down for me - the boiler service is due in october"),
    (Intent.KNOWLEDGE_CAPTURE, "SAVE THE LINK TO THAT ARTICLE ABOUT URBAN GARDENING"),
    (Intent.KNOWLEDGE_CAPTURE, "quick one - jot down that we agreed on the smaller room"),
    (Intent.KNOWLEDGE_CAPTURE, "keep this page about compost bins so i can find it again"),
    (Intent.KNOWLEDGE_LOOKUP, "what was it we agreed about the boiler"),
    (Intent.KNOWLEDGE_LOOKUP, "where did i write down that thing about the room dimensions"),
    (Intent.KNOWLEDGE_LOOKUP, "can you dig up the page I saved on sourdough starters"),
    (Intent.KNOWLEDGE_LOOKUP, "i can't find my note about the party seating - any luck?"),
    (Intent.ANALYTICS_INSIGHT, "did i actually get more done this month than last"),
    (Intent.ANALYTICS_INSIGHT, "how many things did i finish last month?"),
    (Intent.ANALYTICS_INSIGHT, "am i getting slower at this or is it just me"),
    (Intent.ANALYTICS_INSIGHT, "SHOW ME HOW MANY ITEMS I CLOSED OFF IN JULY"),
    (Intent.RISK_QUERY, "am i in trouble for the 5th"),
    (Intent.RISK_QUERY, "whats going to bite me"),
    (Intent.RISK_QUERY, "i think i've said yes to too much, is that a problem"),
    (Intent.RISK_QUERY, "which of my commitments are about to blow up"),
    (Intent.DEVELOPER_INTEL, "how much code have i actually shipped this week"),
    (Intent.DEVELOPER_INTEL, "i havent opened vscode in days lol whats my streak looking like"),
    (Intent.DEVELOPER_INTEL, "which of my projects have i been committing to lately"),
    (Intent.DEVELOPER_INTEL, "WHEN DID I LAST PUSH ANYTHING"),
    (Intent.LEARNING_TRACK, "ive been meaning to pick the guitar back up for months"),
    (Intent.LEARNING_TRACK, "how many hours have i put into spanish"),
    (Intent.LEARNING_TRACK, "give me something to practise tonight, ive lost the plot"),
    (Intent.LEARNING_TRACK, "should i keep at the piano or just quit"),
    (Intent.CAREER_TRACK, "am i actually going to get that senior job or am i deluding myself"),
    (Intent.CAREER_TRACK, "what would i need on my cv to go for tech lead"),
    (Intent.CAREER_TRACK, "be honest - am i ready for a management role"),
    (Intent.CAREER_TRACK, "where do i stand against the bar for a staff engineer"),
    (Intent.ACCOUNT_ADMIN, "my notifications are driving me nuts can you turn them down"),
    (Intent.ACCOUNT_ADMIN, "the clock on here says the wrong time, fix it"),
    (Intent.ACCOUNT_ADMIN, "who has permission to look at my stuff"),
    (Intent.ACCOUNT_ADMIN, "CHANGE THE EMAIL ON THIS ACCOUNT"),
    (Intent.CODE_ASSIST, "why is my script throwing KeyError on the third line"),
    (Intent.CODE_ASSIST, "show me a one-liner in bash that lists every folder in here"),
    (Intent.CODE_ASSIST, "this regex is eating my whole string, whats wrong with it"),
    (Intent.CODE_ASSIST, "what does 'ECONNREFUSED' mean when node complains about it"),
    (Intent.DEEP_REASONING, "should we move offices or rent the extra floor, talk me through"),
    (Intent.DEEP_REASONING, "is remote work actually better or is that what everyone says"),
    (Intent.DEEP_REASONING, "argue me into or out of hiring a second designer"),
    (Intent.DEEP_REASONING, "if we killed the caching layer tomorrow what would actually break"),
    (Intent.OUT_OF_SCOPE, "how do i make sourdough starter from scratch"),
    (Intent.OUT_OF_SCOPE, "did the tortoise actually win the marathon"),
    (Intent.OUT_OF_SCOPE, "whats the weather like in glasgow"),
    (Intent.OUT_OF_SCOPE, "can you book me a taxi to the airport"),
)

#: The measured generalization misses, keyed by the exact utterance. Every value
#: is what the checkpoint actually returned when Phase 11 was verified, recorded
#: so the xfail is a *finding* rather than a shrug. Strict xfail: a future
#: checkpoint that fixes one of these fails the suite until the marker is removed
#: on purpose, which is the moment the report should be rewritten.
GENERALIZATION_MISSES: dict[str, tuple[str, float]] = {
    "who else is on the kitchen extension with me": ("account_admin", 0.9512),
    "what did my day look like on the 14th": ("analytics_insight", 0.6399),
    "write this down for me - the boiler service is due in october": ("schedule_plan", 0.4673),
    "am i getting slower at this or is it just me": ("risk_query", 0.9558),
    "how much code have i actually shipped this week": ("analytics_insight", 0.9924),
    "i havent opened vscode in days lol whats my streak looking like": (
        "analytics_insight",
        0.9586,
    ),
    "WHEN DID I LAST PUSH ANYTHING": ("knowledge_lookup", 0.8396),
    "give me something to practise tonight, ive lost the plot": ("risk_query", 0.8173),
    "should i keep at the piano or just quit": ("risk_query", 0.3684),
    "am i actually going to get that senior job or am i deluding myself": ("risk_query", 0.5610),
    "should we move offices or rent the extra floor, talk me through": ("risk_query", 0.3393),
    "is remote work actually better or is that what everyone says": ("risk_query", 0.9004),
    "argue me into or out of hiring a second designer": ("risk_query", 0.8495),
    "if we killed the caching layer tomorrow what would actually break": ("project_manage", 0.4177),
}

#: How many of the fifty-six hand-written utterances the checkpoint gets right,
#: and how many it does not. Pinned because this is the one number in the file
#: that Phase 11 quotes, and a suite that silently grew a passing example would
#: move the headline without moving a word of the report.
MEASURED_GENERALIZATION_HITS = 42
MEASURED_GENERALIZATION_TOTAL = 56

#: A marker word the rejection tests look for. A rejection that quotes the
#: submitted text has to echo *something* recognisable; an all-``x`` over-length
#: string would make the "it was not echoed" assertion impossible to falsify.
REJECTION_MARKER = "CONFIDENTIAL-UTTERANCE"

#: Every row of the Phase 10 corpus lives here, one JSON object per line.
#: Gitignored like the weights, so the overlap check skips when it is absent.
CORPUS_DIR = Path(__file__).resolve().parent.parent / "ml" / "datasets"
CORPUS_FILES = ("routing_train.jsonl", "routing_validation.jsonl", "routing_test.jsonl")

#: Floor on the corpus size the overlap check will accept before it trusts
#: itself. Without it, reading an empty or truncated file would make the scan find
#: nothing and pass — the classic way a "nothing matched" test cannot fail.
MINIMUM_CORPUS_ROWS = 1000


def _example_ids(cases: tuple[tuple[Intent, str], ...]) -> list[str]:
    """Unique, readable ids so a failure names the class, not a row index."""
    seen: dict[Intent, int] = {}
    ids: list[str] = []
    for intent, _ in cases:
        seen[intent] = seen.get(intent, 0) + 1
        ids.append(f"{intent}-{seen[intent]}")
    return ids


def _xfail_reason(expected: Intent, text: str) -> str | None:
    """The recorded misclassification for a known generalization miss, else ``None``."""
    predicted = GENERALIZATION_MISSES.get(text)
    if predicted is None:
        return None
    name, confidence = predicted
    return f"measured generalization miss: classified {name} at {confidence:.4f}, not {expected}"


def _generalization_params() -> list[Any]:
    """The generalization cases, each already carrying its own xfail mark if it misses."""
    params: list[Any] = []
    ids = _example_ids(GENERALIZATION_EXAMPLES)
    for (intent, text), case_id in zip(GENERALIZATION_EXAMPLES, ids, strict=True):
        reason = _xfail_reason(intent, text)
        marks = (pytest.mark.xfail(strict=True, reason=reason),) if reason else ()
        params.append(pytest.param(intent, text, id=case_id, marks=marks))
    return params


REPRESENTATIVE_IDS = _example_ids(REPRESENTATIVE_EXAMPLES)
GENERALIZATION_IDS = _example_ids(GENERALIZATION_EXAMPLES)


class _RecordingTokenizer:
    """A pass-through tokenizer that remembers exactly what it was handed.

    :class:`~app.ml.model_loader.LoadedModel` is a frozen dataclass, so the spy is
    installed with :func:`dataclasses.replace` rather than by assignment: the
    classifier is handed a new loaded model that shares the same 703 MiB of
    weights and the same label order, and only the tokenizer is swapped.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    @property
    def texts_seen(self) -> list[Any]:
        """The first positional argument of every call, in call order."""
        return [args[0] for args, _ in self.calls if args]

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((args, kwargs))
        return self._inner(*args, **kwargs)


@pytest.fixture(scope="session")
def loaded_model() -> LoadedModel:
    """The real Phase 10 checkpoint, loaded once for the whole session.

    Loading costs about five seconds and 703 MiB, so this is session-scoped and
    every test shares it. Both preconditions are checked before the expensive
    work: torch first, because a machine without it cannot load anything, and then
    the checkpoint's member files, so the skip reason names what is actually
    missing rather than an ``ImportError`` three frames into transformers.
    """
    for package in ("torch", "transformers"):
        try:
            __import__(package)
        except ImportError as exc:
            pytest.skip(f"{package} is not installed in this interpreter ({exc})")

    checkpoint = default_checkpoint_dir()
    missing = [name for name in REQUIRED_CHECKPOINT_FILES if not (checkpoint / name).is_file()]
    if missing:
        pytest.skip(
            f"the Phase 10 checkpoint is not on disk under {CORPUS_DIR.parent.name} "
            f"(missing: {', '.join(missing)}); run ml/train.py to produce it"
        )
    return load_model(checkpoint, device="cpu")


@pytest.fixture(scope="session")
def classifier(loaded_model: LoadedModel) -> IntentClassifier:
    """A classifier over the shared checkpoint.

    The classifier's own ``threshold`` is a convenience default; the 0.90 the
    router applies is configured at the router. Nothing in this module routes, so
    no settings are read — which is what lets these tests run with no environment
    configuration at all.
    """
    return IntentClassifier(loaded_model)


def test_the_classifier_under_test_is_the_fourteen_class_checkpoint(
    classifier: IntentClassifier,
):
    """Positive control: the fixture loaded what this module claims it is testing.

    Without this, every other test would still pass against a stub that happened
    to load a model with the wrong context length or a truncated label set.
    """
    identity = classifier.identity

    assert identity is not None
    assert classifier.label_count == 14
    assert len(Intent) == 14
    assert classifier.max_sequence_length == EXPECTED_MAX_SEQUENCE_LENGTH
    assert MAX_SEQUENCE_LENGTH == EXPECTED_MAX_SEQUENCE_LENGTH
    assert identity.label_count == classifier.label_count
    assert identity.max_sequence_length == EXPECTED_MAX_SEQUENCE_LENGTH
    assert identity.parameter_count > 100_000_000


def test_the_example_set_covers_every_intent_at_least_twice():
    """Fourteen classes in, and never one lucky phrasing per class.

    A parametrisation that silently loses a class — a typo in an intent name, an
    intent edited out of the data — is the failure this guards against, and it
    would otherwise hide behind green rows.
    """
    expected = [intent for intent, _ in REPRESENTATIVE_EXAMPLES]

    assert len(REPRESENTATIVE_EXAMPLES) >= 2 * len(Intent)
    assert set(expected) == set(Intent)
    assert len(set(expected)) == len(Intent) == 14
    assert len(set(REPRESENTATIVE_IDS)) == len(REPRESENTATIVE_IDS)
    assert len(set(GENERALIZATION_IDS)) == len(GENERALIZATION_IDS)
    for intent in Intent:
        assert expected.count(intent) >= 2, f"{intent} has fewer than two phrasings"


def test_every_utterance_in_this_module_is_absent_from_the_phase_10_corpus():
    """The whole point of the module: this text was never in training.

    The Phase 10 rows are template-generated and the curated
    :data:`~ml.datasets.taxonomy.INTENT_SPECS` exemplars are folded into that
    same corpus, so "we wrote our own examples" is a claim to be checked rather
    than asserted in a docstring. A row lifted verbatim from the corpus would turn
    every accuracy number in this file into a training-set score.
    """
    available = [CORPUS_DIR / name for name in CORPUS_FILES if (CORPUS_DIR / name).is_file()]
    if not available:
        pytest.skip(f"the Phase 10 corpus is not on disk under {CORPUS_DIR.name}")

    corpus_texts: set[str] = set()
    for path in available:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                corpus_texts.add(json.loads(line)["text"])
    assert len(corpus_texts) >= MINIMUM_CORPUS_ROWS, (
        f"only {len(corpus_texts)} corpus rows read from {len(available)} file(s); an "
        "overlap scan over that would pass without checking anything"
    )

    curated = {example for spec in INTENT_SPECS for example in spec.examples}
    for intent, text in (*REPRESENTATIVE_EXAMPLES, *GENERALIZATION_EXAMPLES):
        assert text not in corpus_texts, f"{intent}: {text!r} is a Phase 10 training row"
        assert text not in curated, f"{intent}: {text!r} is a curated taxonomy exemplar"


class TestPredictionForEveryIntent:
    """The coverage floor: a natural sentence for each of the fourteen classes.

    Representative rather than adversarial. If one of these fails, the class has
    no ordinary sentence that reaches it and the router's mapping for that intent
    is only reachable by text shaped like the templates.
    """

    @pytest.mark.parametrize(
        ("intent", "text"),
        REPRESENTATIVE_EXAMPLES,
        ids=REPRESENTATIVE_IDS,
    )
    def test_a_representative_sentence_is_classified_as_its_intent(
        self,
        classifier: IntentClassifier,
        intent: Intent,
        text: str,
    ):
        """The sentence a person would actually write reaches the right service."""
        prediction = classifier.predict(text)

        assert prediction.intent == str(intent), (
            f"{text!r} was classified {prediction.intent} at {prediction.confidence:.4f}"
        )

    @pytest.mark.parametrize(
        ("intent", "text"),
        REPRESENTATIVE_EXAMPLES,
        ids=REPRESENTATIVE_IDS,
    )
    def test_every_confidence_is_a_probability_and_every_alternative_is_ranked(
        self,
        classifier: IntentClassifier,
        intent: Intent,
        text: str,
    ):
        """The shape contract, asserted for every class rather than for one.

        ``alternatives`` is checked against a fixed length as well as against its
        ordering, because "sorted descending and excludes the winner" is trivially
        true of an empty tuple. The winner is additionally required to be the
        strict maximum of the reported distribution, which is the property that
        makes the tuple *the* ranking of this prediction.
        """
        prediction = classifier.predict(text)
        scores = [score for _, score in prediction.alternatives]
        names = [name for name, _ in prediction.alternatives]

        assert 0.0 <= prediction.confidence <= 1.0
        assert prediction.intent in INTENT_NAMES
        assert str(intent) in INTENT_NAMES
        assert len(prediction.alternatives) == EXPECTED_ALTERNATIVE_COUNT
        assert scores == sorted(scores, reverse=True)
        assert prediction.intent not in names
        assert all(name in INTENT_NAMES for name in names)
        assert len(set(names)) == EXPECTED_ALTERNATIVE_COUNT
        assert all(score <= prediction.confidence for score in scores)
        assert prediction.truncated is False


class TestGeneralizationToHandWrittenNaturalLanguage:
    """Hand-written natural language: casing, punctuation, register, everyday words.

    This group is a measurement, not a target. Fourteen of its fifty-six utterances
    are classified as something other than their intent, and the pattern is not
    noise: ``deep_reasoning`` loses four of four — three to ``risk_query`` and one
    to ``project_manage`` — because those classes share the "should I worry about
    this decision" surface in the training templates. The misses are recorded in
    :data:`GENERALIZATION_MISSES` and marked ``xfail(strict=True)``, so they are
    reported rather than hidden, and so a model that fixes one of them fails the
    suite until the marker is removed on purpose.

    **The measured result is 42 of 56 correct.**
    """

    @pytest.mark.parametrize(
        ("intent", "text"),
        _generalization_params(),
    )
    def test_natural_language_is_classified_as_its_intent(
        self,
        classifier: IntentClassifier,
        intent: Intent,
        text: str,
    ):
        """The honest generalization measurement, measured rather than engineered."""
        prediction = classifier.predict(text)

        assert prediction.intent == str(intent), (
            f"{text!r} was classified {prediction.intent} at {prediction.confidence:.4f}"
        )

    @pytest.mark.parametrize(
        ("intent", "text"),
        GENERALIZATION_EXAMPLES,
        ids=GENERALIZATION_IDS,
    )
    def test_a_natural_language_miss_is_still_a_well_formed_prediction(
        self,
        classifier: IntentClassifier,
        intent: Intent,
        text: str,
    ):
        """A wrong intent is a routing error, not a crash or a malformed result.

        Deliberately *not* xfailed, and paired with the classification assertion
        above: a case that is expected to be misrouted must still produce a
        prediction that satisfies the shape contract, or a regression that made
        the classifier throw on lowercase input would hide behind the xfail.
        """
        prediction = classifier.predict(text)

        assert prediction.intent in INTENT_NAMES
        assert 0.0 <= prediction.confidence <= 1.0
        assert len(prediction.alternatives) == EXPECTED_ALTERNATIVE_COUNT
        assert prediction.intent not in [name for name, _ in prediction.alternatives]
        assert prediction.truncated is False
        assert prediction.latency_ms > 0.0

    def test_the_measured_generalization_score_is_still_the_one_phase_11_reports(self):
        """The scoreboard, pinned.

        The miss table and the data cannot drift apart, and the number cannot move
        without an explicit edit here. This is the assertion that makes the
        generalization result a measurement rather than a memory.
        """
        texts = {text for _, text in GENERALIZATION_EXAMPLES}

        assert len(GENERALIZATION_EXAMPLES) == MEASURED_GENERALIZATION_TOTAL
        assert set(GENERALIZATION_MISSES) <= texts
        assert len(GENERALIZATION_MISSES) == (
            MEASURED_GENERALIZATION_TOTAL - MEASURED_GENERALIZATION_HITS
        )
        assert len(GENERALIZATION_EXAMPLES) - len(GENERALIZATION_MISSES) == (
            MEASURED_GENERALIZATION_HITS
        )
        assert {
            _xfail_reason(intent, text) is not None for intent, text in GENERALIZATION_EXAMPLES
        } == {True, False}
        assert {
            text
            for intent, text in GENERALIZATION_EXAMPLES
            if _xfail_reason(intent, text) is not None
        } == set(GENERALIZATION_MISSES)

    def test_the_deep_reasoning_class_is_the_one_that_breaks_most_often(self):
        """The measured failure mode, kept as a claim rather than as an impression.

        All four ``deep_reasoning`` utterances here are misrouted — three to
        ``risk_query`` and one to ``project_manage``. That is a real weakness of
        the integration: ``deep_reasoning`` and ``risk_query`` share the "should I
        worry about this decision" surface in the training templates, and once the
        domain vocabulary is dropped the classes stop being separable. It is
        recorded as an assertion so it is re-measured rather than remembered the
        next time the checkpoint changes.
        """
        confused = [
            text for intent, text in GENERALIZATION_EXAMPLES if intent is Intent.DEEP_REASONING
        ]

        assert len(confused) == 4
        assert all(text in GENERALIZATION_MISSES for text in confused)
        assert [GENERALIZATION_MISSES[text][0] for text in confused] == [
            "risk_query",
            "risk_query",
            "risk_query",
            "project_manage",
        ]


class TestPreprocessingParity:
    """The classifier must score exactly the string Phase 10 trained on."""

    def test_the_tokenizer_is_handed_the_raw_utterance_unchanged(
        self,
        loaded_model: LoadedModel,
    ):
        """No lowercasing, no punctuation stripping, no whitespace collapsing.

        Any normalisation added during integration would still return a
        well-formed prediction while scoring a string the weights have never seen.
        This is the single easiest thing in the phase to break and the hardest to
        notice.
        """
        text = "  Move  my Dentist APPOINTMENT!!! to Thursday??  "
        spy = _RecordingTokenizer(loaded_model.tokenizer)
        spied = IntentClassifier(dataclasses.replace(loaded_model, tokenizer=spy))

        spied.predict(text)

        assert spy.calls, "the classifier did not tokenise through the tokenizer it was given"
        assert spy.texts_seen, "the tokenizer was called without a positional text argument"
        for seen in spy.texts_seen:
            assert seen == text
            assert len(seen) == len(text)

    def test_the_tokenizer_is_called_with_the_trained_tokenisation_contract(
        self,
        loaded_model: LoadedModel,
    ):
        """Truncation at 128, no padding, tensors out — as ``_encode_all`` did.

        A change here is invisible in the response: the model still answers, just
        about a different number of tokens. The training reference is
        ``ml/scripts/train_small_local.py::_encode_all``.
        """
        spy = _RecordingTokenizer(loaded_model.tokenizer)
        spied = IntentClassifier(dataclasses.replace(loaded_model, tokenizer=spy))

        spied.predict("Anything at all")

        truncating = [kwargs for _, kwargs in spy.calls if kwargs.get("truncation") is True]
        assert len(truncating) == 1, "expected exactly one truncating tokenisation per predict"
        assert truncating[0]["max_length"] == EXPECTED_MAX_SEQUENCE_LENGTH
        assert truncating[0]["padding"] is False
        assert truncating[0]["return_tensors"] == "pt"

    def test_token_type_ids_are_dropped_before_the_forward_pass(
        self,
        loaded_model: LoadedModel,
    ):
        """DeBERTa-v3 declares ``type_vocab_size: 0``; the training code popped the key.

        Observed at the model's own ``forward``, not reconstructed from the
        tokenizer, so this fails if the key is ever handed through.
        """
        captured: dict[str, Any] = {}
        original_forward = loaded_model.model.forward

        def _capturing_forward(**inputs: Any) -> Any:
            captured.update(inputs)
            return original_forward(**inputs)

        loaded_model.model.forward = _capturing_forward
        try:
            IntentClassifier(loaded_model).predict("Anything at all")
        finally:
            del loaded_model.model.forward

        assert captured, "the model was never called, so its inputs were never observed"
        assert "input_ids" in captured
        assert "attention_mask" in captured
        assert "token_type_ids" not in captured


class TestTruncationAndConfidence:
    """Long inputs degrade by flagging, and the probability is a real probability."""

    def test_an_utterance_far_over_the_context_length_is_still_classified(
        self,
        classifier: IntentClassifier,
        loaded_model: LoadedModel,
    ):
        """Truncation is reported, not crashed on, and the flag is real.

        The token count comes from the tokenizer with truncation disabled, so the
        assertion is about a genuinely over-length input; without that check a
        ``truncated`` flag hard-wired to ``True`` would pass here.
        """
        long_text = "The team agreed that the migration would happen in the spring. " * 60
        assert len(long_text) < MAX_UTTERANCE_CHARACTERS
        token_count = len(
            loaded_model.tokenizer(long_text, truncation=False, add_special_tokens=True)[
                "input_ids"
            ]
        )
        assert token_count > EXPECTED_MAX_SEQUENCE_LENGTH

        prediction = classifier.predict(long_text)

        assert prediction.truncated is True
        assert prediction.intent in INTENT_NAMES
        assert 0.0 <= prediction.confidence <= 1.0

    def test_an_utterance_inside_the_context_length_is_not_flagged_as_truncated(
        self,
        classifier: IntentClassifier,
        loaded_model: LoadedModel,
    ):
        """The control for the flag above: a short request must not be truncated."""
        short_text = "Move my dentist appointment to Thursday"
        token_count = len(
            loaded_model.tokenizer(short_text, truncation=False, add_special_tokens=True)[
                "input_ids"
            ]
        )
        assert token_count <= EXPECTED_MAX_SEQUENCE_LENGTH

        assert classifier.predict(short_text).truncated is False

    def test_the_confidence_is_the_softmax_probability_of_the_winning_class(
        self,
        classifier: IntentClassifier,
        loaded_model: LoadedModel,
    ):
        """Recomputed from the logits, so "did anyone hard-code 0.97?" has an answer."""
        torch_module = loaded_model.torch_module
        text = "Add a task to buy stamps before Friday"
        encoded = loaded_model.tokenizer(
            text,
            truncation=True,
            max_length=EXPECTED_MAX_SEQUENCE_LENGTH,
            padding=False,
            return_tensors="pt",
        )
        encoded.pop("token_type_ids", None)

        prediction = classifier.predict(text)
        with torch_module.inference_mode():
            logits = loaded_model.model(
                **{name: tensor.to(loaded_model.device) for name, tensor in encoded.items()}
            ).logits
        probabilities = torch_module.softmax(logits, dim=-1)[0]

        assert prediction.intent == loaded_model.id2label[int(probabilities.argmax())]
        assert prediction.confidence == pytest.approx(float(probabilities.max()), abs=1e-6)
        assert sum(float(value) for value in probabilities) == pytest.approx(1.0, abs=1e-5)

    def test_the_confidence_varies_with_the_utterance_rather_than_being_a_constant(
        self,
        classifier: IntentClassifier,
    ):
        """The cheap control that catches a rounded, clamped or invented number."""
        decisive = classifier.predict("Add a task to buy stamps before Friday")
        murky = classifier.predict("hm")

        assert decisive.intent == "task_manage"
        assert abs(decisive.confidence - murky.confidence) > 1e-3


class TestUtteranceValidation:
    """Rejection happens before inference, and never quotes what was submitted."""

    @pytest.mark.parametrize(
        ("text", "fragment"),
        [
            ("", "empty"),
            ("   \t\n  ", "empty"),
            ("x" * (MAX_UTTERANCE_CHARACTERS + 1), "longer than"),
        ],
        ids=["empty", "whitespace-only", "over-length"],
    )
    def test_an_unusable_utterance_is_rejected(
        self,
        classifier: IntentClassifier,
        text: str,
        fragment: str,
    ):
        """A request the classifier may not answer never reaches the forward pass."""
        with pytest.raises(InvalidUtteranceError, match=fragment):
            classifier.predict(text)

    def test_a_blank_utterance_is_rejected_with_no_echo_of_what_was_sent(
        self,
        classifier: IntentClassifier,
    ):
        """A blank rejection carries only the bound, so there is nothing to echo."""
        for blank in ("", "   \t\n  "):
            with pytest.raises(InvalidUtteranceError, match="empty") as caught:
                classifier.predict(blank)
            details = caught.value.details

            assert set(details) == {"reason", "max_characters"}
            assert details["reason"] == "blank"
            assert details["max_characters"] == MAX_UTTERANCE_CHARACTERS
            assert all(blank != value for value in details.values())
        # Only the whitespace-only case can be searched for as a substring: the
        # empty string is contained in every message, so the check would pass
        # vacuously against it.
        with pytest.raises(InvalidUtteranceError, match="empty") as caught:
            classifier.predict("   \t\n  ")

        assert "   \t\n  " not in caught.value.message

    def test_an_over_length_rejection_never_quotes_the_submitted_text(
        self,
        classifier: IntentClassifier,
    ):
        """The submitted text is the one thing that must not be copied anywhere."""
        over_length = (f"{REJECTION_MARKER} " * 300)[: MAX_UTTERANCE_CHARACTERS + 1]
        assert len(over_length) > MAX_UTTERANCE_CHARACTERS
        assert REJECTION_MARKER in over_length

        with pytest.raises(InvalidUtteranceError, match="longer than") as caught:
            classifier.predict(over_length)
        details = caught.value.details

        assert details["max_characters"] == MAX_UTTERANCE_CHARACTERS
        assert details["characters"] == len(over_length)
        assert REJECTION_MARKER not in json.dumps(details)
        assert REJECTION_MARKER not in caught.value.message
        assert over_length not in json.dumps(details)

    def test_a_non_string_utterance_is_rejected(self, classifier: IntentClassifier):
        """A validation domain that assumed a string would raise ``AttributeError``."""
        with pytest.raises(InvalidUtteranceError, match="must be a string") as caught:
            classifier.predict(None)  # type: ignore[arg-type]

        assert caught.value.details == {"type": "NoneType"}


def test_a_prediction_serialises_to_the_rounded_shape_the_api_returns(
    classifier: IntentClassifier,
):
    """``to_dict`` is what the route hands the client, so it is part of the contract."""
    prediction = classifier.predict("Add a task to buy stamps before Friday")

    payload = prediction.to_dict()

    assert set(payload) == {"intent", "confidence", "alternatives", "truncated", "latency_ms"}
    assert payload["intent"] == prediction.intent
    assert payload["confidence"] == pytest.approx(prediction.confidence, abs=1e-6)
    assert payload["truncated"] is False
    assert len(payload["alternatives"]) == EXPECTED_ALTERNATIVE_COUNT
    assert all(set(entry) == {"intent", "confidence"} for entry in payload["alternatives"])
    assert [entry["confidence"] for entry in payload["alternatives"]] == [
        round(score, 6) for _, score in prediction.alternatives
    ]
