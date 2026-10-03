"""Deterministic, leakage-aware train/validation/test splitting.

**What leakage is.** A model scored on rows that leaked into its training set
has been graded on recall rather than generalisation. NEXUS has two structural
sources of it, and both are worse than the usual one because they are baked into
how the datasets are built. The routing set is expanded from templates, so
*"add a task"* and *"create a new task"* recur as dozens of paraphrases sharing
one bag of words; the feature sets are derived per subject, so the same
developer's rows land in every split unless the subject is held out. Splitting
record-by-record hides both — near-identical text crosses the boundary and the
test set quietly describes people the model was fitted on.

**Why grouping precedes stratification.** The two objectives fight, and the
order is not a preference. Stratification balances the label histogram by
dealing records into splits; grouping forbids a duplicate family from being
split across them. If stratification ran first, grouping would have to undo its
deals and the outcome would depend on which pass ran last. Grouping first
collapses each family into one indivisible unit, and stratification then
balances *units* — the only granularity at which the choice still exists. The
consequence is stated rather than hidden: two paraphrases of one intent form a
single unit and therefore land whole in a single split, so the validation and
test sets are thinner than the raw class counts suggest. That is the price of
a number nobody can accuse of being flattered.

**Why determinism is the feature.** A reported accuracy is worth only what it
costs to re-measure. Everything here derives from ``random.Random(seed)`` over
an input order the caller fixes: no global random state, no dependence on
dictionary or set iteration order, no wall clock. Re-running the split next
month on the same manifest and a different machine returns the same assignment,
so a dropped score is a regression in the model rather than in the partition.
Changing how splits are computed therefore means bumping
:data:`SPLIT_CONFIG_VERSION` and re-measuring the baseline, never quietly
restating it.

**Nothing is dropped.** Every record that enters receives exactly one split,
and the result is checked for that before it is returned — a partial
assignment raises :class:`~ml.datasets.schema.DataValidationError` rather than
handing the trainer a quietly shorter dataset. Degenerate inputs (a single
record, a class smaller than the number of splits, a class collapsed into one
group by grouping) are answered with the rule that keeps every row and states
its compromise in ``counts``, not with an exception that would strand a dataset
the rest of the pipeline could have used.

Splits are named by their canonical keys — ``train``, ``validation``, ``test``
— and appear in ``counts`` even when empty, so a zero can be read as "this
split is empty" instead of as a key that was forgotten.
"""

from __future__ import annotations

import math
import random
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ml.datasets.schema import DataValidationError

#: Version of the splitting rule itself, stamped into manifests alongside the
#: dataset schema version. A v2 partition is not comparable to a v1 one even
#: when both are 70/15/15, so the two must never be read as one baseline.
SPLIT_CONFIG_VERSION = "nexo_splits.v1"

#: Canonical split order. Every tie-break in this module resolves towards the
#: earliest name here, which is what makes the result reproducible rather than
#: merely seeded.
_SPLIT_ORDER: tuple[str, str, str] = ("train", "validation", "test")

#: Pseudo-label for an unstratified split. A single class means the histogram
#: balances trivially and the proportions alone drive the deals.
_UNLABELLED = "\x00unlabelled"


@dataclass(frozen=True, slots=True)
class SplitConfig:
    """How a dataset is divided, and with what repeatability guarantee.

    The three fractions are checked rather than normalised. Silently rescaling
    ``0.6/0.2/0.2`` into ``0.7/0.15/0.15`` would be friendlier, but it would
    also mean the configuration in the manifest is not the configuration that
    ran, and a manifest that misreports its own split is worse than a rejected
    one.

    ``group_by`` is the leakage control and defaults to on. Turning it off is
    legitimate for an ablation whose entire purpose is to measure what grouping
    is worth, and illegitimate anywhere else: near-duplicates then straddle
    splits and the resulting score overstates generalisation by an unknown
    amount.
    """

    train: float = 0.7
    validation: float = 0.15
    test: float = 0.15
    seed: int = 20260101
    group_by: bool = True

    def __post_init__(self) -> None:
        """Reject a partition that is not a partition.

        Raises:
            ValueError: A fraction is negative, or the three do not sum to 1.0
                within 1e-9.
        """
        for name in _SPLIT_ORDER:
            value = getattr(self, name)
            if value < 0.0:
                raise ValueError(f"{name} fraction must be non-negative, got {value!r}")
        total = self.train + self.validation + self.test
        if abs(total - 1.0) > 1e-9:
            raise ValueError(
                "train + validation + test must sum to 1.0 within 1e-9, got "
                f"{total!r} from ({self.train!r}, {self.validation!r}, {self.test!r})"
            )


@dataclass(frozen=True, slots=True)
class SplitResult:
    """Where every record went.

    ``assignments`` is keyed by the caller's own record key, so downstream code
    re-derives its own partition from a stored split without holding the
    original list order. ``counts`` covers every canonical split name including
    the empty ones — an absent key and a count of zero are different claims, and
    only one of them is true here.
    """

    assignments: Mapping[str, str]
    counts: Mapping[str, int]
    seed: int


@dataclass(frozen=True, slots=True)
class _Unit:
    """One indivisible block of records.

    A unit is a single record when grouping is off, and a whole duplicate
    family when it is on. Stratification operates on units for exactly that
    reason: it is the coarsest granularity at which a split is still allowed to
    make a decision.
    """

    label: str
    keys: tuple[str, ...]


def split_records(
    records: Sequence[Mapping[str, Any]],
    *,
    key_field: str,
    label_field: str | None,
    config: SplitConfig,
) -> SplitResult:
    """Split record by record, stratified by label.

    Use this only for feature rows that are already independent — the per-subject
    vectors, where one row is one user and no two rows describe the same text.
    For anything drawn from a template expansion, use
    :func:`assign_leakage_free_splits`: this function will happily place two
    paraphrases of one intent on opposite sides of the boundary, which is the
    failure this module exists to prevent.

    Args:
        records: The records to divide. Order is part of the determinism
            contract — the same records in the same order always split the same
            way.
        key_field: The field naming each record. Must be unique across
            ``records``.
        label_field: The field to stratify on, or None to divide on the
            proportions alone.
        config: The partition and the seed.

    Returns:
        The assignment for every record.

    Raises:
        DataValidationError: A record has no usable key, two records share a key,
            a record has no usable label, or the assignment is incomplete.
    """
    return _split(records, key_field=key_field, label_field=label_field, config=config)


def assign_leakage_free_splits(
    records: Sequence[Mapping[str, Any]],
    *,
    key_field: str,
    label_field: str | None,
    duplicate_key_fn: Callable[[Mapping[str, Any]], str],
    config: SplitConfig,
) -> SplitResult:
    """Split records after collapsing duplicate families into single units.

    Args:
        records: The records to divide. Order is part of the determinism
            contract.
        key_field: The field naming each record. Must be unique across
            ``records``.
        label_field: The field to stratify on, or None to divide on the
            proportions alone.
        duplicate_key_fn: Maps a record to its duplicate-family key. Wrap
            :func:`ml.preprocessing.normalize.near_duplicate_key` with the
            field it should read, for example
            ``lambda row: near_duplicate_key(row["text"])`` — the function
            takes the *record*, so the text field is named here rather than
            guessed here. Its collisions are the module's definition of
            leakage: two records sharing a key always share a split.
        config: The partition and the seed. With ``group_by=False`` the
            duplicate key is ignored and the call degrades to
            :func:`split_records`; that is the ablation switch, not a default.

    Returns:
        The assignment for every record. No record is dropped and no duplicate
        family straddles a split.

    Raises:
        DataValidationError: A record has no usable key, two records share a key,
            a record has no usable label, a duplicate key is unusable, or the
            assignment is incomplete.
    """
    grouper: Callable[[Mapping[str, Any]], str] | None
    grouper = duplicate_key_fn if config.group_by else None
    return _split(
        records,
        key_field=key_field,
        label_field=label_field,
        config=config,
        grouper=grouper,
    )


def _split(
    records: Sequence[Mapping[str, Any]],
    *,
    key_field: str,
    label_field: str | None,
    config: SplitConfig,
    grouper: Callable[[Mapping[str, Any]], str] | None = None,
) -> SplitResult:
    """The one implementation both public entry points share.

    Args:
        records: The records to divide.
        key_field: The field naming each record.
        label_field: The field to stratify on, or None.
        config: The partition and the seed.
        grouper: Maps a record to its duplicate-family key, or None to treat
            every record as its own family.

    Returns:
        The assignment for every record.

    Raises:
        DataValidationError: A record is unusable, keys collide, or the assignment
            is incomplete.
    """
    fractions = _fractions(config)
    units = _build_units(records, key_field=key_field, label_field=label_field, grouper=grouper)
    placement = _place_units(units, fractions, seed=config.seed)
    _fill_empty_splits(placement, fractions)
    return _finalise(records, units, placement, config)


def _fractions(config: SplitConfig) -> tuple[tuple[str, float], ...]:
    """The splits that may receive records, in canonical order.

    A configured fraction of zero is excluded entirely, so a 0.8/0.2/0.0 run
    never has a row pushed into ``test`` by the empty-split repair.

    Args:
        config: The partition.

    Returns:
        ``(name, fraction)`` pairs for the splits with a non-zero share.
    """
    pairs = [(name, getattr(config, name)) for name in _SPLIT_ORDER]
    return tuple((name, fraction) for name, fraction in pairs if fraction > 0.0)


def _build_units(
    records: Sequence[Mapping[str, Any]],
    *,
    key_field: str,
    label_field: str | None,
    grouper: Callable[[Mapping[str, Any]], str] | None,
) -> list[_Unit]:
    """Collapse records into indivisible units, preserving first-appearance order.

    A family's label is the majority label among its members, ties broken by the
    smallest label. A family that mixes intents is a contradiction the dataset
    builder should have caught; picking one side deterministically keeps the
    stratification accounting honest instead of dropping the family.

    Args:
        records: The records to divide.
        key_field: The field naming each record.
        label_field: The field to stratify on, or None.
        grouper: Maps a record to its duplicate-family key, or None.

    Returns:
        The units, in the order their families first appeared.

    Raises:
        DataValidationError: A key is missing or reused, a label is unusable, or
            a duplicate key is not a non-empty string.
    """
    seen: set[str] = set()
    families: dict[str, tuple[list[str], list[str]]] = {}
    for record in records:
        key = _record_key(record, key_field)
        if key in seen:
            raise DataValidationError(
                f"key {key!r} from {key_field!r} appears more than once; every record "
                "needs a distinct key so exactly one split can be recorded for it"
            )
        seen.add(key)
        family = key if grouper is None else grouper(record)
        if not isinstance(family, str) or not family.strip():
            raise DataValidationError(
                f"duplicate_key_fn must return a non-empty string for record {key!r}, "
                f"got {family!r}"
            )
        keys, labels = families.setdefault(family, ([], []))
        keys.append(key)
        labels.append(_record_label(record, label_field, key))

    return [
        _Unit(label=_majority_label(labels), keys=tuple(keys)) for keys, labels in families.values()
    ]


def _record_key(record: Mapping[str, Any], key_field: str) -> str:
    """Read a record's identity, or refuse to guess it.

    Args:
        record: The decoded record.
        key_field: The field naming the record.

    Returns:
        The record key.

    Raises:
        DataValidationError: The field is absent, null, blank or not a string.
    """
    value = record.get(key_field)
    if not isinstance(value, str) or not value.strip():
        raise DataValidationError(
            f"{key_field!r} must be a non-empty string on every record, got {value!r}"
        )
    return value


def _record_label(record: Mapping[str, Any], label_field: str | None, key: str) -> str:
    """Read a record's label for stratification.

    Args:
        record: The decoded record.
        label_field: The field carrying the label, or None.
        key: The record key, used only to make a failure message findable.

    Returns:
        The label, or the unlabelled pseudo-label when no field was requested.

    Raises:
        DataValidationError: The label field is absent, null or blank. An
            unlabelled row cannot be stratified *or* trained on, and admitting
            one here would move the failure to a point where it is harder to
            trace.
    """
    if label_field is None:
        return _UNLABELLED
    value = record.get(label_field)
    if not isinstance(value, str) or not value.strip():
        raise DataValidationError(
            f"record {key!r} has no usable {label_field!r} label (got {value!r}); "
            "an unlabelled row must not reach a split"
        )
    return value


def _majority_label(labels: Sequence[str]) -> str:
    """Pick the label a mixed family is counted under.

    Args:
        labels: The labels of the family's members.

    Returns:
        The most common label, smallest label winning a tie.
    """
    counts = Counter(labels)
    highest = max(counts.values())
    return min(name for name, seen in counts.items() if seen == highest)


def _place_units(
    units: Sequence[_Unit],
    fractions: tuple[tuple[str, float], ...],
    *,
    seed: int,
) -> list[str]:
    """Deal units into splits, one label class at a time.

    Each class gets its exact quota by largest remainder, the resulting deal
    order is shuffled so a class's earliest rows do not all land in ``train``,
    and the shuffled deal is handed out over the class's shuffled members. Two
    classes therefore interleave into the splits for the same reason two
    independent draws should, and no class is systematically earlier.

    Largest remainder is also where the small-class cases are answered. A class
    of two with a 0.7/0.15/0.15 split has floors of 1/0/0 and one seat left
    over, which goes to the largest remainder — ``train``. A class of one lands
    whole as well. Spreading a class of two across validation and test would put
    a row in a split whose only content is a near-twin of a training row: a
    per-class score manufactured entirely by leakage.

    Args:
        units: The indivisible blocks.
        fractions: ``(name, fraction)`` pairs for the active splits.
        seed: The only source of randomness.

    Returns:
        A split name per unit, positionally aligned with ``units``.
    """
    rng = random.Random(seed)  # noqa: S311 — a shuffle, never a secret; the seed is the point
    order = list(range(len(units)))
    rng.shuffle(order)

    by_label: dict[str, list[int]] = {}
    for position in order:
        by_label.setdefault(units[position].label, []).append(position)

    placement = [""] * len(units)
    for label in sorted(by_label):
        members = by_label[label]
        deal: list[str] = []
        for name, quota in _largest_remainder(len(members), fractions).items():
            deal.extend([name] * quota)
        rng.shuffle(deal)
        for position, split_name in zip(members, deal, strict=True):
            placement[position] = split_name
    return placement


def _largest_remainder(
    total: int,
    fractions: tuple[tuple[str, float], ...],
) -> dict[str, int]:
    """Split an integer quota across splits without losing or inventing seats.

    Args:
        total: Seats to hand out.
        fractions: ``(name, fraction)`` pairs for the active splits.

    Returns:
        Seat count per split name, summing exactly to ``total``.
    """
    exact = {name: fraction * total for name, fraction in fractions}
    seats = {name: math.floor(value) for name, value in exact.items()}
    leftover = total - sum(seats.values())
    ranked = sorted(
        exact,
        key=lambda name: (-(exact[name] - seats[name]), _SPLIT_ORDER.index(name)),
    )
    for name in ranked[:leftover]:
        seats[name] += 1
    return seats


def _fill_empty_splits(
    placement: list[str],
    fractions: tuple[tuple[str, float], ...],
) -> None:
    """Move one unit into any active split that stratification left empty.

    An empty split is worse than a badly balanced one: there is no curve to
    read, and an early-stopping rule keyed on validation loss has nothing to
    key on. The donor is the split holding the most units and gives up its
    last-dealt unit, which is the smallest edit available. A split is left empty
    when no donor can spare one — fewer units than splits, or every unit in one
    place — and ``counts`` reports the zero rather than the repair inventing a
    row.

    Args:
        placement: Split name per unit, mutated in place.
        fractions: ``(name, fraction)`` pairs for the active splits.
    """
    held = Counter(placement)
    for name, _ in fractions:
        if name in placement:
            continue
        candidates = [other for other, _ in fractions if held[other] > 1]
        if not candidates:
            continue
        donor = max(candidates, key=lambda other: (held[other], -_SPLIT_ORDER.index(other)))
        placement[len(placement) - 1 - placement[::-1].index(donor)] = name
        held[donor] -= 1
        held[name] += 1


def _finalise(
    records: Sequence[Mapping[str, Any]],
    units: Sequence[_Unit],
    placement: Sequence[str],
    config: SplitConfig,
) -> SplitResult:
    """Expand unit placements to record keys and prove the partition is total.

    Args:
        records: The records that entered the split.
        units: The units the placements belong to.
        placement: Split name per unit.
        config: The configuration, for the seed echoed into the result.

    Returns:
        The complete assignment and per-split counts.

    Raises:
        DataValidationError: An internal invariant failed — a unit was never
            placed, a record key was emitted twice or missed, or a placement
            names a split that does not exist. The check is written as a real
            raise rather than an ``assert`` because ``-O`` strips asserts, and
            the case this catches is precisely one where the process is already
            misconfigured.
    """
    assignments: dict[str, str] = {}
    for unit, split_name in zip(units, placement, strict=True):
        if not split_name:
            raise DataValidationError(
                f"unit {unit.keys!r} was never placed; the split is incomplete"
            )
        for key in unit.keys:
            if key in assignments:
                raise DataValidationError(f"record {key!r} was assigned to two splits")
            assignments[key] = split_name

    if len(assignments) != len(records):
        raise DataValidationError(
            f"split lost records: {len(records)} in, {len(assignments)} assigned"
        )

    counts = dict.fromkeys(_SPLIT_ORDER, 0)
    for split_name in assignments.values():
        if split_name not in counts:
            raise DataValidationError(f"placement named an unknown split {split_name!r}")
        counts[split_name] += 1
    return SplitResult(assignments=assignments, counts=counts, seed=config.seed)


__all__ = [
    "SPLIT_CONFIG_VERSION",
    "SplitConfig",
    "SplitResult",
    "assign_leakage_free_splits",
    "split_records",
]
