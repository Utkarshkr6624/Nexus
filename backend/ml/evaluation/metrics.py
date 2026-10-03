"""Classification metrics for the Phase 10 models, in pure standard library.

**Why this exists instead of ``sklearn.metrics``.** The backend's pinned
``requirements.txt`` carries no scientific stack, and Phase 10 does not add one:
the local half of this pipeline has to stay importable on a bare interpreter so
that a contributor can build a dataset, validate it, split it and score a
checkpoint without a 2 GB wheelhouse standing between them and a test run. The
heavy half — training ``deberta-v3-base`` and the QLoRA fine-tune of Qwen3-8B —
runs on remote GPUs and never imports this module. Every metric below is a
handful of divisions over a confusion matrix, so pulling in a dependency to
compute them would cost the pipeline more than it would save.

**These are the numbers a Phase 11 router ships.** The classifier routes a
request to one of the intent labels drawn from Nexo's real capability
inventory; the router's offline scorecard is exactly what
:class:`ClassificationMetrics` produces. That makes two conventions
non-negotiable here.

**A degenerate class scores 0.0, not NaN and not an exception.** Precision is
``TP/(TP+FP)`` and recall is ``TP/(TP+FN)``. When a denominator is zero — a
class the model never predicts, or one absent from the split — the numerator is
zero too and the ratio is undefined. Returning ``0.0`` keeps a finite number
in the report; returning NaN would propagate silently into every average and
poison macro-F1 with a comparison that is always false. The cost is that a
never-predicted class drags macro-F1 down, which is the honest reading: the
model does not handle it.

**Labels are declared, never inferred.** Every function takes the label set
explicitly and orders the matrix by it, so a metric computed today is
comparable with one computed tomorrow against a different ordering of the same
intents, and a label the caller forgot is an error rather than a silently
dropped column. The same reasoning governs :func:`top_k_accuracy`: the softmax
head is read against the declared labels, not against whatever keys happen to be
present, because an undeclared key means the head and the taxonomy have drifted
apart.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

#: Rounded to this many decimal places when rendering for humans. Four is enough
#: to distinguish two runs and short enough to keep a markdown table readable.
#: The raw floats are left untouched in :meth:`ClassificationMetrics.to_dict`,
#: which is what a downstream comparison or a regression gate should read.
_REPORT_PRECISION = 4


def _validate_labels(labels: Sequence[str]) -> tuple[str, ...]:
    """Resolve and sanity-check a declared label set.

    Args:
        labels: The label universe, in the order the caller wants the matrix
            indexed by.

    Returns:
        The labels as an immutable tuple.

    Raises:
        ValueError: The set is empty, holds a non-string or blank label, or
            repeats a label — a duplicate would make the matrix rows ambiguous.
    """
    resolved = tuple(labels)
    if not resolved:
        raise ValueError("labels must not be empty")
    seen: set[str] = set()
    for label in resolved:
        if not isinstance(label, str) or not label:
            raise ValueError(f"every label must be a non-empty string, got {label!r}")
        if label in seen:
            raise ValueError(f"duplicate label {label!r}")
        seen.add(label)
    return resolved


def _validate_square(cm: Sequence[Sequence[int]]) -> tuple[tuple[int, ...], ...]:
    """Check that a matrix is square and holds non-negative integer counts.

    Args:
        cm: The matrix to check.

    Returns:
        The matrix as an immutable tuple of immutable rows.

    Raises:
        ValueError: The matrix is ragged, or a cell is not a non-negative
            integer. A float count means the matrix was built by averaging
            rather than counting, and every figure derived from it would be
            quietly wrong.
    """
    rows = tuple(tuple(row) for row in cm)
    if any(len(row) != len(rows) for row in rows):
        raise ValueError(
            f"confusion matrix must be square, got {len(rows)}x{[len(r) for r in rows]}"
        )
    for row in rows:
        for value in row:
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"confusion matrix cells must be non-negative ints, got {value!r}")
    return rows


def _validate_matrix(
    cm: Sequence[Sequence[int]], labels: Sequence[str]
) -> tuple[tuple[int, ...], ...]:
    """Check that a confusion matrix is square and matches the declared labels.

    Args:
        cm: The matrix to check.
        labels: The declared label universe.

    Returns:
        The matrix as an immutable tuple of immutable rows.

    Raises:
        ValueError: The matrix is ragged, has the wrong shape, or holds a
            negative or non-integer count.
    """
    resolved = tuple(labels)
    rows = _validate_square(cm)
    if len(rows) != len(resolved):
        raise ValueError(
            f"confusion matrix must be {len(resolved)}x{len(resolved)} to match the "
            f"labels, got {len(rows)}x{len(rows)}"
        )
    return rows


def _safe_ratio(numerator: int | float, denominator: int | float) -> float:
    """Divide, mapping a zero denominator to 0.0 instead of raising.

    A class the model never predicts makes the ratio undefined rather than
    wrong, and an undefined score has no place in an average.

    Args:
        numerator: The dividend.
        denominator: The divisor.

    Returns:
        The quotient, or 0.0 when the divisor is zero.
    """
    if not denominator:
        return 0.0
    return numerator / denominator


@dataclass(frozen=True, slots=True)
class PerClassMetrics:
    """Precision, recall, F1 and support for one label.

    ``support`` is the number of true instances of the label, which is what
    makes it usable as the weight in :func:`weighted_f1`. A label with support
    ``0`` contributes nothing to the weighted average no matter how good its
    scores look.
    """

    label: str
    precision: float
    recall: float
    f1: float
    support: int

    def to_dict(self) -> dict[str, Any]:
        """Serialise deterministically.

        Returns:
            A JSON-ready mapping with sorted keys.
        """
        return {
            "f1": self.f1,
            "label": self.label,
            "precision": self.precision,
            "recall": self.recall,
            "support": self.support,
        }


@dataclass(frozen=True, slots=True)
class ClassificationMetrics:
    """A complete offline scorecard for one classifier run.

    Everything is derived from the confusion matrix, so the dataclass can be
    rebuilt from a stored matrix without re-running inference. ``per_class``
    and ``support`` are keyed by the declared label order, and are exposed as
    mappings because callers read them by name rather than by position.
    """

    labels: tuple[str, ...]
    confusion_matrix: tuple[tuple[int, ...], ...]
    per_class: Mapping[str, PerClassMetrics]
    accuracy: float
    macro_f1: float
    weighted_f1: float
    support: Mapping[str, int]

    def to_dict(self) -> dict[str, Any]:
        """Serialise deterministically.

        Rows become lists and the per-class mapping becomes a plain object, so
        the result survives ``json.dumps(sort_keys=True)`` unchanged.

        Returns:
            A JSON-ready mapping with sorted keys.
        """
        return {
            "accuracy": self.accuracy,
            "confusion_matrix": [list(row) for row in self.confusion_matrix],
            "labels": list(self.labels),
            "macro_f1": self.macro_f1,
            "per_class": {label: metrics.to_dict() for label, metrics in self.per_class.items()},
            "support": dict(self.support),
            "weighted_f1": self.weighted_f1,
        }

    def to_markdown(self) -> str:
        """Render the scorecard for a training report.

        Returns:
            Markdown with the headline scores, a per-class table and the
            confusion matrix, rows actual and columns predicted.
        """
        lines = [
            "# Classification metrics",
            "",
            f"- accuracy: {self.accuracy:.{_REPORT_PRECISION}f}",
            f"- macro f1: {self.macro_f1:.{_REPORT_PRECISION}f}",
            f"- weighted f1: {self.weighted_f1:.{_REPORT_PRECISION}f}",
            "",
            "## Per class",
            "",
            "| label | precision | recall | f1 | support |",
            "| --- | --- | --- | --- | --- |",
        ]
        for label in self.labels:
            metrics = self.per_class[label]
            lines.append(
                f"| {label} "
                f"| {metrics.precision:.{_REPORT_PRECISION}f} "
                f"| {metrics.recall:.{_REPORT_PRECISION}f} "
                f"| {metrics.f1:.{_REPORT_PRECISION}f} "
                f"| {metrics.support} |"
            )
        lines.extend(_markdown_confusion_matrix(self.confusion_matrix, self.labels))
        return "\n".join(lines)


def _markdown_confusion_matrix(cm: Sequence[Sequence[int]], labels: Sequence[str]) -> list[str]:
    """Render a confusion matrix as markdown table rows.

    Args:
        cm: The matrix, already square.
        labels: The declared label order.

    Returns:
        The markdown lines, including the heading and the table body.
    """
    lines = [
        "",
        "## Confusion matrix (rows actual, columns predicted)",
        "",
        "| actual \\ predicted | " + " | ".join(labels) + " | support |",
        "| --- | " + " | ".join("---" for _ in labels) + " | --- |",
    ]
    for index, label in enumerate(labels):
        row = cm[index]
        cells = " | ".join(str(value) for value in row)
        lines.append(f"| {label} | {cells} | {sum(row)} |")
    return lines


def confusion_matrix(
    y_true: Sequence[str], y_pred: Sequence[str], labels: Sequence[str]
) -> tuple[tuple[int, ...], ...]:
    """Build a confusion matrix indexed by the declared label order.

    Rows are actual classes, columns are predicted ones. The label set is
    supplied by the caller rather than inferred from the data so that a label
    the split happens not to contain still gets a row and a column — dropping it
    would silently change the denominator of every accuracy figure.

    Args:
        y_true: The gold labels, one per example.
        y_pred: The predicted labels, aligned positionally with ``y_true``.
        labels: The label universe, in matrix order.

    Returns:
        A square matrix of counts, as an immutable tuple of rows.

    Raises:
        ValueError: The label set is empty, malformed or duplicated; the two
            sequences differ in length; or a value is not in ``labels``. A
            label outside the declared universe is a taxonomy drift the caller
            needs to see, not a row to discard.
    """
    resolved = _validate_labels(labels)
    index = {label: position for position, label in enumerate(resolved)}
    actual = tuple(y_true)
    predicted = tuple(y_pred)
    if len(actual) != len(predicted):
        raise ValueError(
            f"y_true and y_pred must be the same length, got {len(actual)} and {len(predicted)}"
        )
    matrix = [[0] * len(resolved) for _ in resolved]
    for truth, guess in zip(actual, predicted, strict=True):
        if truth not in index:
            raise ValueError(f"y_true contains {truth!r}, which is not in the declared labels")
        if guess not in index:
            raise ValueError(f"y_pred contains {guess!r}, which is not in the declared labels")
        matrix[index[truth]][index[guess]] += 1
    return tuple(tuple(row) for row in matrix)


def per_class_metrics(
    cm: Sequence[Sequence[int]], labels: Sequence[str]
) -> dict[str, PerClassMetrics]:
    """Derive per-class precision, recall and F1 from a confusion matrix.

    Precision is ``TP/(TP+FP)`` — of everything predicted as this label, how
    much was right. Recall is ``TP/(TP+FN)`` — of everything that really was
    this label, how much was found. F1 is their harmonic mean. A zero
    denominator yields ``0.0`` rather than an error or a NaN, because a class
    the model never emits, or never appears in the split, would otherwise
    produce an undefined score that quietly poisons every average it enters.

    Args:
        cm: A square confusion matrix, rows actual and columns predicted.
        labels: The label universe in matrix order.

    Returns:
        One :class:`PerClassMetrics` per label, keyed by label name.

    Raises:
        ValueError: The matrix shape does not match ``labels``, or a cell is
            not a non-negative integer.
    """
    resolved = _validate_labels(labels)
    matrix = _validate_matrix(cm, resolved)
    size = len(resolved)
    column_totals = [sum(matrix[row][column] for row in range(size)) for column in range(size)]
    results: dict[str, PerClassMetrics] = {}
    for position, label in enumerate(resolved):
        true_positive = matrix[position][position]
        support = sum(matrix[position])
        false_positive = column_totals[position] - true_positive
        false_negative = support - true_positive
        precision = _safe_ratio(true_positive, true_positive + false_positive)
        recall = _safe_ratio(true_positive, true_positive + false_negative)
        f1 = _safe_ratio(2.0 * precision * recall, precision + recall)
        results[label] = PerClassMetrics(
            label=label,
            precision=precision,
            recall=recall,
            f1=f1,
            support=support,
        )
    return results


def accuracy(cm: Sequence[Sequence[int]]) -> float:
    """Fraction of predictions that were correct.

    Args:
        cm: A square confusion matrix.

    Returns:
        The trace over the total, or 0.0 for an empty matrix.

    Raises:
        ValueError: The matrix is ragged or holds a negative or non-integer
            count.
    """
    matrix = _validate_square(cm)
    total = sum(sum(row) for row in matrix)
    if not total:
        return 0.0
    return sum(matrix[i][i] for i in range(len(matrix))) / total


def macro_f1(per_class: Mapping[str, PerClassMetrics]) -> float:
    """Unweighted mean F1 across classes.

    Every class counts the same however rare it is, which is the right lens for
    a router: a 12-intent taxonomy where the model collapses onto the four most
    common intents can score well on accuracy and badly here.

    Args:
        per_class: The per-class metrics, keyed by label.

    Returns:
        The mean F1, or 0.0 when there are no classes to average.
    """
    scores = [metrics.f1 for metrics in per_class.values()]
    if not scores:
        return 0.0
    return sum(scores) / len(scores)


def weighted_f1(per_class: Mapping[str, PerClassMetrics]) -> float:
    """Support-weighted mean F1 across classes.

    Args:
        per_class: The per-class metrics, keyed by label.

    Returns:
        The weighted mean F1, or 0.0 when the split holds no examples at all.
    """
    total_support = sum(metrics.support for metrics in per_class.values())
    if not total_support:
        return 0.0
    weighted = sum(metrics.f1 * metrics.support for metrics in per_class.values())
    return weighted / total_support


def evaluate(
    y_true: Sequence[str], y_pred: Sequence[str], labels: Sequence[str]
) -> ClassificationMetrics:
    """Score one classifier run end to end.

    Args:
        y_true: The gold labels, one per example.
        y_pred: The predicted labels, aligned positionally with ``y_true``.
        labels: The label universe, in report order.

    Returns:
        The complete scorecard, ready to serialise into a run manifest.

    Raises:
        ValueError: Anything :func:`confusion_matrix` raises.
    """
    resolved = _validate_labels(labels)
    matrix = confusion_matrix(y_true, y_pred, resolved)
    per_class = per_class_metrics(matrix, resolved)
    support = {label: per_class[label].support for label in resolved}
    return ClassificationMetrics(
        labels=resolved,
        confusion_matrix=matrix,
        per_class=per_class,
        accuracy=accuracy(matrix),
        macro_f1=macro_f1(per_class),
        weighted_f1=weighted_f1(per_class),
        support=support,
    )


def top_k_accuracy(
    y_true: Sequence[str],
    y_pred_proba: Sequence[Mapping[str, float]],
    labels: Sequence[str],
    k: int = 2,
) -> float:
    """Fraction of examples whose true label is in the softmax head's top ``k``.

    This is the first half of the abstention and out-of-distribution analysis.
    A router only has to be right about *which* of the ``k`` intents it offers,
    not right on the first attempt, so top-2 is the number the Phase 11 router
    is measured on; the gap between top-1 accuracy and top-2 accuracy is the
    headroom a re-ranker or a clarifying question would recover. When the top-1
    probability is low *and* the true label is not in the top ``k``, the input
    resembles nothing the router has been trained on and should be handed to
    the deterministic engine instead.

    Probabilities are ranked by descending value and ties break on the declared
    label order, so the metric is reproducible across runs rather than dependent
    on dictionary insertion order.

    Args:
        y_true: The gold labels, one per example.
        y_pred_proba: Per-example mappings of label to softmax probability, in
            the same order as ``y_true``.
        labels: The declared label universe the head is expected to cover.
        k: How many of the top labels count as a hit. Must be positive; values
            at or beyond the label count simply cover everything the head
            reported.

    Returns:
        The hit rate, or 0.0 when there are no examples.

    Raises:
        ValueError: ``k`` is not positive, the sequences differ in length, a
            key is outside ``labels``, or a probability is negative or
            non-finite.
    """
    resolved = _validate_labels(labels)
    if k < 1:
        raise ValueError(f"k must be positive, got {k}")
    actual = tuple(y_true)
    if len(actual) != len(y_pred_proba):
        raise ValueError(
            f"y_true and y_pred_proba must be the same length, "
            f"got {len(actual)} and {len(y_pred_proba)}"
        )
    if not actual:
        return 0.0
    hits = 0
    for truth, row in zip(actual, y_pred_proba, strict=True):
        if truth not in resolved:
            raise ValueError(f"y_true contains {truth!r}, which is not in the declared labels")
        ranked = []
        for label, probability in row.items():
            if label not in resolved:
                raise ValueError(
                    f"the head reported {label!r}, which is not in the declared labels"
                )
            if not math.isfinite(probability) or probability < 0.0:
                raise ValueError(f"probability for {label!r} is not a valid score: {probability!r}")
            ranked.append((probability, resolved.index(label), label))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        if any(item[2] == truth for item in ranked[:k]):
            hits += 1
    return hits / len(actual)


def format_confusion_matrix(cm: Sequence[Sequence[int]], labels: Sequence[str]) -> str:
    """Render a confusion matrix as an aligned text table.

    Rows are actual classes, columns predicted; the trailing column is the true
    count of each row. The table is what goes in a terminal log line and in a
    training report, so it is plain text with no markdown and no colour — it
    has to survive being piped into a file and read months later.

    Args:
        cm: A square confusion matrix.
        labels: The label universe in matrix order.

    Returns:
        The rendered table, newline-terminated.

    Raises:
        ValueError: The matrix shape does not match ``labels``, or a cell is
            not a non-negative integer.
    """
    resolved = _validate_labels(labels)
    matrix = _validate_matrix(cm, resolved)
    width = max(5, *(len(label) for label in resolved))
    header = "actual \\ predicted".ljust(width)
    header += "".join(label.rjust(width + 2) for label in resolved)
    header += "support".rjust(width + 2)
    rule = "-" * len(header)
    lines = [header, rule]
    for position, label in enumerate(resolved):
        row = matrix[position]
        cells = label.ljust(width) + "".join(str(value).rjust(width + 2) for value in row)
        lines.append(cells + str(sum(row)).rjust(width + 2))
    return "\n".join(lines) + "\n"


__all__ = [
    "ClassificationMetrics",
    "PerClassMetrics",
    "accuracy",
    "confusion_matrix",
    "evaluate",
    "format_confusion_matrix",
    "macro_f1",
    "per_class_metrics",
    "top_k_accuracy",
    "weighted_f1",
]
