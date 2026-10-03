"""Classification metrics, in pure standard library.

These numbers are what the Phase 11 router ships against, so the conventions
below are part of the contract rather than an implementation detail:

* **A degenerate class scores 0.0**, not NaN and not an exception. Precision and
  recall are ratios whose denominators vanish when a class is never predicted;
  returning NaN would propagate silently into every average and poison macro-F1
  with a comparison that is always false. The cost — a never-predicted class
  drags macro-F1 down — is the honest reading.
* **Labels are declared, never inferred.** The caller supplies the label set and
  the matrix is ordered by it, so a label the split happens not to contain still
  gets a row and a column, and today's number is comparable with tomorrow's
  against a different ordering of the same intents.

The expected values below are computed by hand from the matrices in the tests,
and the arithmetic is written out in each docstring so a reader can check it
rather than trust it.
"""

from __future__ import annotations

import json

import pytest

from ml.evaluation.metrics import (
    ClassificationMetrics,
    accuracy,
    confusion_matrix,
    evaluate,
    format_confusion_matrix,
    macro_f1,
    per_class_metrics,
    top_k_accuracy,
    weighted_f1,
)

LABELS = ("alpha", "beta", "gamma")

#: Rows actual, columns predicted.
#:
#:            predicted
#:            alpha beta gamma
#: alpha        2     1     0
#: beta         0     3     1
#: gamma        1     0     4
#:
#: row sums 3, 4, 5 -> total 12. Diagonal 2+3+4 = 9 -> accuracy 9/12 = 3/4.
#: column sums 3, 4, 5.
CM = ((2, 1, 0), (0, 3, 1), (1, 0, 4))


def test_confusion_matrix_rows_are_actual_and_columns_are_predicted():
    """``cm[actual][predicted]``. Transposing it silently doubles the error rate."""
    y_true = ["alpha"] * 3 + ["beta"] * 4 + ["gamma"] * 5
    y_pred = [
        "alpha", "alpha", "beta",
        "beta", "beta", "beta", "gamma",
        "gamma", "alpha", "gamma", "gamma", "gamma",
    ]  # fmt: skip

    matrix = confusion_matrix(y_true, y_pred, LABELS)

    assert matrix == CM
    assert matrix[0][0] == 2
    assert matrix[0][1] == 1
    assert matrix[1][2] == 1
    assert matrix[2][0] == 1
    assert sum(sum(row) for row in matrix) == len(y_true)


def test_accuracy_is_the_trace_over_the_total():
    """Diagonal 2+3+4 = 9 over 3+4+5 = 12 -> 0.75."""
    assert accuracy(CM) == 9 / 12
    assert accuracy(CM) == 0.75


def test_per_class_scores_are_computed_from_the_diagonal_and_the_margins():
    """Per-class arithmetic from the diagonals and the margins.

    alpha: TP 2, support 3, column total 3 -> FP 1, FN 1. p = r = 2/3, F1 = 2/3.
    beta:  TP 3, support 4, column total 4 -> FP 1, FN 1. p = r = 3/4, F1 = 3/4.
    gamma: TP 4, support 5, column total 5 -> FP 1, FN 1. p = r = 4/5, F1 = 4/5.
    """
    scores = per_class_metrics(CM, LABELS)

    assert scores["alpha"].precision == pytest.approx(2 / 3)
    assert scores["alpha"].recall == pytest.approx(2 / 3)
    assert scores["alpha"].f1 == pytest.approx(2 / 3)
    assert scores["alpha"].support == 3

    assert scores["beta"].precision == pytest.approx(3 / 4)
    assert scores["beta"].f1 == pytest.approx(3 / 4)
    assert scores["beta"].support == 4

    assert scores["gamma"].precision == pytest.approx(4 / 5)
    assert scores["gamma"].f1 == pytest.approx(4 / 5)
    assert scores["gamma"].support == 5


def test_macro_f1_is_the_unweighted_mean_of_the_class_scores():
    """((2/3) + (3/4) + (4/5)) / 3."""
    scores = per_class_metrics(CM, LABELS)

    expected = ((2 / 3) + (3 / 4) + (4 / 5)) / 3

    assert macro_f1(scores) == pytest.approx(expected)
    assert macro_f1(scores) == pytest.approx(0.7388888, abs=1e-6)


def test_weighted_f1_weights_each_class_by_its_support():
    """(3*2/3 + 4*3/4 + 5*4/5) / (3+4+5) = (2 + 3 + 4) / 12 = 0.75."""
    scores = per_class_metrics(CM, LABELS)

    assert weighted_f1(scores) == pytest.approx((2 + 3 + 4) / 12)


def test_macro_and_weighted_f1_differ_on_an_imbalanced_split():
    """The lens a router is judged by. Nine alphas and one beta, all predicted alpha.

    Matrix: [[9, 0], [1, 0]].  alpha: TP 9, support 9, column total 10 -> FP 1,
    FN 0. p = 9/10, r = 1, F1 = 2*0.9/1.9 = 18/19.  beta: never predicted, and
    the F1 falls out 0.0 by the zero-denominator convention.
    macro   = (18/19 + 0) / 2 = 9/19      ~ 0.4737
    weighted = (9 * 18/19 + 1 * 0) / 10  ~ 0.8526
    """
    y_true = ["alpha"] * 9 + ["beta"]
    y_pred = ["alpha"] * 10

    metrics = evaluate(y_true, y_pred, ("alpha", "beta"))

    assert metrics.accuracy == pytest.approx(9 / 10)
    assert metrics.per_class["beta"].f1 == 0.0
    assert metrics.macro_f1 == pytest.approx(9 / 19)
    assert metrics.weighted_f1 == pytest.approx(9 * (18 / 19) / 10)
    assert metrics.weighted_f1 > metrics.macro_f1


def test_a_class_that_is_never_predicted_scores_zero_rather_than_raising():
    """0/0 is not a crash and not NaN: a never-predicted class does drag macro-F1."""
    y_true = ["alpha", "alpha", "beta"]
    y_pred = ["alpha", "alpha", "alpha"]

    metrics = evaluate(y_true, y_pred, ("alpha", "beta"))
    beta = metrics.per_class["beta"]

    assert beta.precision == 0.0
    assert beta.recall == 0.0
    assert beta.f1 == 0.0
    assert beta.support == 1
    assert metrics.macro_f1 == pytest.approx(metrics.per_class["alpha"].f1 / 2)


def test_an_empty_matrix_scores_zero_everywhere():
    empty = ((0, 0, 0), (0, 0, 0), (0, 0, 0))

    assert accuracy(empty) == 0.0
    scores = per_class_metrics(empty, LABELS)
    assert macro_f1(scores) == 0.0
    assert weighted_f1(scores) == 0.0


def test_evaluate_assembles_the_whole_scorecard():
    metrics = evaluate(
        ["alpha", "alpha", "beta", "beta", "beta", "beta", "gamma", "gamma"],
        ["alpha", "beta", "beta", "gamma", "beta", "beta", "alpha", "gamma"],
        LABELS,
    )

    assert isinstance(metrics, ClassificationMetrics)
    assert metrics.labels == LABELS
    assert metrics.confusion_matrix == ((1, 1, 0), (0, 3, 1), (1, 0, 1))
    assert metrics.support == {"alpha": 2, "beta": 4, "gamma": 2}
    assert metrics.accuracy == pytest.approx(5 / 8)


def test_evaluate_serialises_to_json_ready_data():
    metrics = evaluate(["alpha", "beta"], ["alpha", "alpha"], LABELS)

    payload = metrics.to_dict()

    assert json.loads(json.dumps(payload)) == payload
    assert payload["labels"] == list(LABELS)
    assert payload["confusion_matrix"] == [list(row) for row in metrics.confusion_matrix]
    assert set(payload["per_class"]) == set(LABELS)


def test_evaluate_renders_a_markdown_scorecard():
    metrics = evaluate(["alpha", "beta"], ["alpha", "alpha"], LABELS)

    markdown = metrics.to_markdown()

    assert "# Classification metrics" in markdown
    assert "rows actual, columns predicted" in markdown
    for label in LABELS:
        assert f"| {label} " in markdown


def test_format_confusion_matrix_contains_every_label_and_the_orientation_header():
    rendered = format_confusion_matrix(CM, LABELS)

    for label in LABELS:
        assert label in rendered
    assert "actual \\ predicted" in rendered
    assert "support" in rendered
    assert rendered.endswith("\n")
    lines = rendered.strip().splitlines()
    assert len(lines) == len(LABELS) + 2, "header, rule and one row per label"
    # Row totals appear in the trailing support column.
    assert "3" in lines[2]
    assert "4" in lines[3]
    assert "5" in lines[4]


def test_format_confusion_matrix_rejects_a_matrix_that_does_not_match_the_labels():
    with pytest.raises(ValueError, match="confusion matrix must be 3x3"):
        format_confusion_matrix(((0, 0), (0, 0)), LABELS)


@pytest.mark.parametrize(
    "labels",
    [(), ("alpha", "alpha"), ("", "beta"), ("alpha", 3)],
)
def test_malformed_label_sets_are_refused(labels):
    with pytest.raises(ValueError):
        confusion_matrix(["alpha"], ["alpha"], labels)


def test_a_label_outside_the_declared_set_is_refused():
    with pytest.raises(ValueError, match="not in the declared labels"):
        confusion_matrix(["alpha", "delta"], ["alpha", "beta"], LABELS)

    with pytest.raises(ValueError, match="not in the declared labels"):
        confusion_matrix(["alpha", "beta"], ["alpha", "delta"], LABELS)


def test_mismatched_lengths_are_refused():
    with pytest.raises(ValueError, match="must be the same length"):
        confusion_matrix(["alpha", "beta"], ["alpha"], LABELS)


def test_a_float_cell_is_refused_because_a_matrix_was_averaged_not_counted():
    with pytest.raises(ValueError, match="non-negative ints"):
        per_class_metrics(((1.5, 0.0), (0.0, 1.0)), ("alpha", "beta"))


def test_a_bool_cell_is_refused():
    with pytest.raises(ValueError, match="non-negative ints"):
        per_class_metrics(((True, 0), (0, 1)), ("alpha", "beta"))


def test_a_ragged_matrix_is_refused():
    with pytest.raises(ValueError, match="must be square"):
        per_class_metrics(((1, 0, 0), (0, 1)), LABELS)


def test_top_k_accuracy_measures_whether_the_truth_is_in_the_head():
    """The abstention analysis: a router only has to be right about *which* of k."""
    y_true = ["alpha", "beta", "gamma"]
    proba = [
        {"alpha": 0.6, "beta": 0.3, "gamma": 0.1},
        {"alpha": 0.5, "beta": 0.2, "gamma": 0.3},
        {"alpha": 0.7, "beta": 0.2, "gamma": 0.1},
    ]

    assert top_k_accuracy(y_true, proba, LABELS, k=1) == pytest.approx(1 / 3)
    assert top_k_accuracy(y_true, proba, LABELS, k=2) == pytest.approx(1 / 3)
    assert top_k_accuracy(y_true, proba, LABELS, k=3) == 1.0
    assert top_k_accuracy([], [], LABELS, k=2) == 0.0


def test_top_k_accuracy_breaks_ties_on_the_declared_label_order():
    y_true = ["beta"]
    proba = [{"alpha": 0.5, "beta": 0.5, "gamma": 0.0}]

    assert top_k_accuracy(y_true, proba, LABELS, k=1) == 0.0
    assert top_k_accuracy(y_true, proba, LABELS, k=2) == 1.0


@pytest.mark.parametrize(
    ("probabilities", "match"),
    [
        ([{"alpha": -0.1, "beta": 0.6, "gamma": 0.5}], "not a valid score"),
        ([{"alpha": 0.5, "beta": 0.6, "delta": 0.0}], "not in the declared labels"),
    ],
)
def test_top_k_accuracy_refuses_a_head_that_has_drifted(probabilities, match):
    with pytest.raises(ValueError, match=match):
        top_k_accuracy(["alpha"], probabilities, LABELS, k=2)


def test_top_k_accuracy_requires_a_positive_k():
    with pytest.raises(ValueError, match="k must be positive"):
        top_k_accuracy(["alpha"], [{"alpha": 1.0, "beta": 0.0, "gamma": 0.0}], LABELS, k=0)
