"""The generated routing corpus: intents, balance, determinism and validation.

Both builders are pure template generators seeded from one integer, and the
properties that matter are therefore not "the sentences read well" — they are
**determinism, balance, provenance and clean validation**. A dataset that
cannot be rebuilt byte-for-byte cannot be re-measured, and a reported accuracy
is worth only what it costs to reproduce.

Balance and provenance are asserted per row rather than in aggregate. Balance is
what the splitter's stratification depends on, and `Provenance.SYNTHETIC` on
every row is the declaration that keeps a template-trained baseline from being
read later as a model of real demand. Nothing here is a transcript, and a reader
who cannot tell which is which will read a confidence the data does not contain.

The corpora are built small (`per_intent=3`, `per_category=2`) so the suite stays
fast; the properties under test are seed-invariant, and the same builders at
full size are what the pipeline runs.
"""

from __future__ import annotations

import json
from collections import Counter

import pytest

from ml.datasets.routing import (
    ROUTING_DATASET_VERSION,
    build_routing_dataset,
    build_routing_records,
    label_map,
)
from ml.datasets.schema import (
    SCHEMA_VERSION_ROUTING,
    Provenance,
    stable_json_dumps,
)
from ml.datasets.taxonomy import INTENT_NAMES
from ml.preprocessing.normalize import normalize_text
from ml.validation import assert_clean, validate_routing_dataset

SEED = 20260101
PER_INTENT = 3
MAX_CLASS_RATIO = 3.0


@pytest.fixture(scope="module")
def routing_records() -> list[dict]:
    return build_routing_records(seed=SEED, per_intent=PER_INTENT)


@pytest.fixture(scope="module")
def test_the_routing_corpus_is_one_row_per_intent_placeholder(routing_records):
    assert len(routing_records) == PER_INTENT * len(INTENT_NAMES)
    assert len(routing_records) == 42


def test_the_routing_corpus_is_exactly_balanced(routing_records):
    histogram = Counter(row["intent"] for row in routing_records)

    assert set(histogram) == set(INTENT_NAMES)
    assert set(histogram.values()) == {PER_INTENT}


def test_every_intent_in_the_taxonomy_is_represented(routing_records):
    assert {row["intent"] for row in routing_records} == set(INTENT_NAMES)


def test_every_routing_row_declares_its_provenance(routing_records):
    for row in routing_records:
        assert row["provenance"] == Provenance.SYNTHETIC


def test_every_routing_row_carries_the_declared_schema_and_a_template(routing_records):
    for row in routing_records:
        assert row["schema_version"] == SCHEMA_VERSION_ROUTING
        assert row["template_id"]
        assert row["source"]
        assert row["text"].strip()
        assert row["intent"] in INTENT_NAMES


def test_no_two_routing_rows_share_a_normalised_text(routing_records):
    texts = [row["text"] for row in routing_records]

    assert len(set(texts)) == len(texts)
    normalised = [normalize_text(text) for text in texts]
    assert len(set(normalised)) == len(normalised)


def test_the_routing_corpus_is_byte_identical_on_a_rebuild():
    first = build_routing_records(seed=SEED, per_intent=PER_INTENT)
    second = build_routing_records(seed=SEED, per_intent=PER_INTENT)

    assert stable_json_dumps(first) == stable_json_dumps(second)
    assert json.loads(json.dumps(first)) == first


def test_a_different_seed_produces_a_different_corpus():
    first = build_routing_records(seed=SEED, per_intent=PER_INTENT)
    second = build_routing_records(seed=SEED + 1, per_intent=PER_INTENT)

    assert stable_json_dumps(first) != stable_json_dumps(second)


def test_build_stats_report_the_balance_and_the_version():
    examples, stats = build_routing_dataset(seed=SEED, per_intent=PER_INTENT)

    assert len(examples) == PER_INTENT * len(INTENT_NAMES)
    assert stats.total == len(examples)
    assert stats.seed == SEED
    assert set(stats.per_intent) == set(INTENT_NAMES)
    assert set(stats.per_intent.values()) == {PER_INTENT}

    payload = stats.to_dict()
    assert payload["dataset_version"] == ROUTING_DATASET_VERSION
    assert payload["balanced"] is True


def test_a_non_positive_per_intent_is_refused():
    from ml.datasets.schema import DataValidationError

    with pytest.raises(DataValidationError, match="per_intent must be positive"):
        build_routing_dataset(seed=SEED, per_intent=0)


def test_the_label_map_covers_the_taxonomy_in_taxonomy_order():
    """Class indices follow the taxonomy, not alphabetical order, and are dense."""
    mapping = label_map()

    assert set(mapping) == set(INTENT_NAMES)
    assert sorted(mapping.values()) == list(range(len(INTENT_NAMES)))
    assert mapping["task_manage"] == 0
    assert mapping["out_of_scope"] == len(INTENT_NAMES) - 1
    assert mapping == {name: index for index, name in enumerate(INTENT_NAMES)}


def test_the_routing_corpus_validates_clean(routing_records):
    report = validate_routing_dataset(
        routing_records, known_intents=INTENT_NAMES, max_class_ratio=MAX_CLASS_RATIO
    )

    assert report.passed, report.codes()
    assert report.errors() == ()
    assert report.counts["usable"] == len(routing_records)
    assert report.counts["contradictions"] == 0
    assert report.counts["invalid_labels"] == 0
    assert_clean(report)


def test_the_routing_corpus_splits_and_validates_without_leakage(routing_records):
    """The whole chain: generate -> split -> audit, at a size a test can afford."""
    from collections import defaultdict

    from ml.preprocessing.normalize import near_duplicate_key
    from ml.preprocessing.splits import SplitConfig, assign_leakage_free_splits
    from ml.validation import validate_splits

    result = assign_leakage_free_splits(
        routing_records,
        key_field="text",
        label_field="intent",
        duplicate_key_fn=lambda row: near_duplicate_key(row["text"]),
        config=SplitConfig(seed=SEED),
    )

    assert len(result.assignments) == len(routing_records)
    grouped: dict[str, list[str]] = defaultdict(list)
    for key, split_name in result.assignments.items():
        grouped[split_name].append(key)

    report = validate_splits(grouped, duplicate_key_fn=near_duplicate_key)

    assert report.passed, report.codes()
    assert report.counts["leaked_keys"] == 0
    assert report.counts["total_items"] == len(routing_records)
