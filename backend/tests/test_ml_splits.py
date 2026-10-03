"""Deterministic, leakage-free train/validation/test splitting.

A reported accuracy is worth only what it costs to re-measure, so determinism
is not a convenience here — it is the property the whole scorecard rests on.
Two runs of the same split on different machines must return the same partition,
otherwise a dropped metric cannot be attributed to the model rather than to the
partition.

The other property is that nothing is quietly lost. Every record that enters
receives exactly one split, near-duplicates never straddle a boundary, and the
degenerate inputs that would ordinarily tempt an implementation into dropping
rows — a single record, a class with fewer members than there are splits, a
class collapsed into one group — are answered by keeping every row and stating
the compromise in `counts`.
"""

from __future__ import annotations

from collections import Counter, defaultdict

import pytest

from ml.datasets.routing import build_routing_records
from ml.datasets.schema import DataValidationError
from ml.preprocessing.normalize import near_duplicate_key
from ml.preprocessing.splits import (
    SPLIT_CONFIG_VERSION,
    SplitConfig,
    assign_leakage_free_splits,
    split_records,
)
from ml.validation import validate_splits

SEED = 20260101
LABELS = ("alpha", "beta", "gamma", "delta")


def _records(per_label: int = 30) -> list[dict[str, str]]:
    """A synthetic corpus whose near-duplicate keys are all distinct.

    Each row gets a unique unshared token, so grouping cannot collapse anything
    and the proportions a caller asked for are the proportions they get.
    """
    rows: list[dict[str, str]] = []
    for index, label in enumerate(LABELS):
        for position in range(per_label):
            rows.append(
                {
                    "text": f"{label} request {index}-{position} about subject {position:03d}",
                    "intent": label,
                }
            )
    return rows


def _split(records, *, config=None, grouped=True):
    return assign_leakage_free_splits(
        records,
        key_field="text",
        label_field="intent",
        duplicate_key_fn=lambda row: near_duplicate_key(row["text"]),
        config=config or SplitConfig(seed=SEED, group_by=grouped),
    )


def test_split_config_version_is_declared():
    assert SPLIT_CONFIG_VERSION == "nexo_splits.v1"


@pytest.mark.parametrize(
    "fractions",
    [
        (0.7, 0.15, 0.15),
        (0.6, 0.2, 0.2),
        (0.8, 0.2, 0.0),
        (1.0, 0.0, 0.0),
    ],
)
def test_split_config_accepts_partitions_that_sum_to_one(fractions):
    train, validation, test = fractions

    config = SplitConfig(train=train, validation=validation, test=test)

    assert (config.train, config.validation, config.test) == fractions
    assert config.seed == SEED
    assert config.group_by is True


@pytest.mark.parametrize(
    "fractions",
    [(-0.1, 0.55, 0.55), (0.7, 0.15, 0.16), (0.5, 0.2, 0.2), (0.7, -0.1, 0.4)],
)
def test_split_config_refuses_a_partition_that_is_not_a_partition(fractions):
    """Rescaling the fractions would mean the manifest misreports what ran."""
    train, validation, test = fractions

    with pytest.raises(ValueError, match=r"sum to 1.0|non-negative"):
        SplitConfig(train=train, validation=validation, test=test)


def test_the_same_seed_produces_the_same_assignment():
    records = _records()

    first = _split(records)
    second = _split(records)

    assert first.assignments == second.assignments
    assert first.counts == second.counts
    assert first.seed == second.seed


def test_a_different_seed_can_produce_a_different_assignment():
    records = _records(per_label=40)

    first = _split(records, config=SplitConfig(seed=1))
    second = _split(records, config=SplitConfig(seed=2))

    assert first.assignments != second.assignments


def test_input_order_is_part_of_the_determinism_contract():
    """Re-ordering the records is a different input, not a silent reshuffle."""
    records = _records()
    reversed_records = list(reversed(records))

    forward = _split(records)
    backward = _split(reversed_records)

    assert set(forward.assignments) == set(backward.assignments) == {row["text"] for row in records}


def test_every_record_is_assigned_exactly_once():
    records = _records()

    result = _split(records)

    assert set(result.assignments) == {row["text"] for row in records}
    assert len(result.assignments) == len(records)
    assert sum(result.counts.values()) == len(records)
    assert set(result.counts) == {"train", "validation", "test"}


def test_proportions_are_respected_within_tolerance():
    records = _records(per_label=50)

    result = _split(records)

    total = len(records)
    assert result.counts["train"] / total == pytest.approx(0.70, abs=0.02)
    assert result.counts["validation"] / total == pytest.approx(0.15, abs=0.02)
    assert result.counts["test"] / total == pytest.approx(0.15, abs=0.02)


def test_stratification_is_preserved_in_every_split():
    records = _records(per_label=30)

    result = _split(records)

    per_split_labels: dict[str, Counter[str]] = defaultdict(Counter)
    label_of = {row["text"]: row["intent"] for row in records}
    for key, split_name in result.assignments.items():
        per_split_labels[split_name][label_of[key]] += 1

    for split_name, histogram in per_split_labels.items():
        assert set(histogram) == set(LABELS), split_name
        # Each split holds a share of every class, not the leftovers of one.
        for label, count in histogram.items():
            assert count / result.counts[split_name] == pytest.approx(0.25, abs=0.12), (
                split_name,
                label,
            )


def test_near_duplicates_never_straddle_a_split():
    families = 40
    records = [
        {"text": f"Add the migration task for release {index}", "intent": "alpha"}
        for index in range(families)
    ]
    # A paraphrase of every first record: same bag of words, different order.
    records += [
        {"text": f"for release {index} add the task migration", "intent": "alpha"}
        for index in range(families)
    ]

    result = _split(records)

    family_split: dict[str, set[str]] = defaultdict(set)
    for row in records:
        family_split[near_duplicate_key(row["text"])].add(result.assignments[row["text"]])
    for family, splits in family_split.items():
        assert len(splits) == 1, family
    assert sum(result.counts.values()) == len(records)


def test_the_leakage_audit_passes_on_a_correct_split():
    records = [
        {"text": f"Add the migration task for release {index}", "intent": "alpha"}
        for index in range(40)
    ] + [
        {"text": f"for release {index} add the task migration", "intent": "alpha"}
        for index in range(40)
    ]

    result = _split(records)
    grouped: dict[str, list[str]] = defaultdict(list)
    for key, split_name in result.assignments.items():
        grouped[split_name].append(key)

    report = validate_splits(grouped, duplicate_key_fn=near_duplicate_key)

    assert report.passed
    assert report.counts["leaked_keys"] == 0


def test_turning_grouping_off_is_visible_in_the_audit():
    """The ablation switch is legitimate; the leakage it causes is documented."""
    records = [
        {"text": f"Add the migration task for release {index}", "intent": "alpha"}
        for index in range(40)
    ] + [
        {"text": f"for release {index} add the task migration", "intent": "alpha"}
        for index in range(40)
    ]

    result = _split(records, config=SplitConfig(seed=SEED, group_by=False))
    family_split: dict[str, set[str]] = defaultdict(set)
    for row in records:
        family_split[near_duplicate_key(row["text"])].add(result.assignments[row["text"]])

    straddling = [family for family, splits in family_split.items() if len(splits) > 1]
    assert straddling, "the ablation must actually leak, or it is not measuring anything"


def test_a_single_record_is_assigned_and_nothing_is_dropped():
    records = [{"text": "Mark the API contract task as done", "intent": "alpha"}]

    result = _split(records)

    assert sum(result.counts.values()) == 1
    assert set(result.assignments) == {"Mark the API contract task as done"}
    assert result.counts["train"] == 1


def test_a_class_smaller_than_the_split_count_still_keeps_every_row():
    """A class of one lands whole; spreading it would put a near-twin on both sides."""
    records = [{"text": f"alpha request {index}", "intent": "alpha"} for index in range(30)]
    records.append({"text": "gamma request 0", "intent": "gamma"})

    result = _split(records)

    assert len(result.assignments) == len(records)
    assert result.assignments["gamma request 0"] == "train"
    assert sum(result.counts.values()) == len(records)


def test_a_two_record_class_lands_whole_rather_than_straddling():
    records = [{"text": f"alpha request {index}", "intent": "alpha"} for index in range(30)]
    records += [
        {"text": "beta request 0", "intent": "beta"},
        {"text": "beta request 1", "intent": "beta"},
    ]

    result = _split(records)

    assert result.assignments["beta request 0"] == result.assignments["beta request 1"]
    assert len(result.assignments) == len(records)


def test_a_class_collapsed_into_one_group_still_lands_whole():
    """Every row of a class sharing a duplicate family is one indivisible unit."""
    records = [{"text": f"alpha request {index}", "intent": "alpha"} for index in range(30)]
    records += [
        {"text": "Review the deadline for beta", "intent": "beta"},
        {"text": "for beta review deadline the", "intent": "beta"},
    ]

    result = _split(records)

    assert (
        result.assignments["Review the deadline for beta"]
        == (result.assignments["for beta review deadline the"])
    )
    assert len(result.assignments) == len(records)


def test_a_zero_fraction_split_never_receives_a_row():
    records = _records(per_label=30)

    result = _split(records, config=SplitConfig(train=0.8, validation=0.2, test=0.0))

    assert result.counts["test"] == 0
    assert sum(result.counts.values()) == len(records)


def test_a_duplicate_record_key_is_refused():
    """Two rows cannot share one key, or exactly one split cannot be recorded for them."""
    records = [
        {"text": "same text", "intent": "alpha"},
        {"text": "same text", "intent": "beta"},
    ]

    with pytest.raises(DataValidationError, match="appears more than once"):
        _split(records)


def test_a_record_with_no_usable_key_is_refused():
    records = [{"text": "   ", "intent": "alpha"}]

    with pytest.raises(DataValidationError, match="non-empty string"):
        _split(records)


def test_a_record_with_no_usable_label_is_refused():
    records = [{"text": "an unlabelled request", "intent": ""}]

    with pytest.raises(DataValidationError, match="no usable 'intent' label"):
        _split(records)


def test_an_unusable_duplicate_key_is_refused():
    records = [{"text": "a request", "intent": "alpha"}]

    with pytest.raises(DataValidationError, match="duplicate_key_fn must return"):
        assign_leakage_free_splits(
            records,
            key_field="text",
            label_field="intent",
            duplicate_key_fn=lambda row: "",
            config=SplitConfig(seed=SEED),
        )


def test_split_records_degrades_to_a_plain_partition_without_a_label():
    records = [{"text": f"row {index}", "intent": "alpha"} for index in range(60)]

    result = split_records(
        records, key_field="text", label_field=None, config=SplitConfig(seed=SEED)
    )

    assert len(result.assignments) == 60
    assert result.counts["train"] == 42


def test_splitting_a_mixed_intent_family_keeps_the_family_and_records_one_label():
    """A contradiction is caught upstream; the splitter still keeps every row."""
    records = [
        {"text": "review the deadline", "intent": "alpha"},
        {"text": "deadline the review", "intent": "beta"},
        *[{"text": f"alpha request {index}", "intent": "alpha"} for index in range(20)],
    ]

    result = _split(records)

    assert result.assignments["review the deadline"] == (result.assignments["deadline the review"])
    assert len(result.assignments) == len(records)


def test_the_real_routing_corpus_splits_without_leakage():
    """The generated corpus is template-expanded, so this is the case that matters."""
    records = build_routing_records(per_intent=6)

    result = _split(records)

    assert len(result.assignments) == len(records)
    grouped: dict[str, list[str]] = defaultdict(list)
    for key, split_name in result.assignments.items():
        grouped[split_name].append(key)
    report = validate_splits(grouped, duplicate_key_fn=near_duplicate_key)

    assert report.passed, report.codes()
    assert report.counts["leaked_keys"] == 0
