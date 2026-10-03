"""The four `*_features.v1` contracts, restated as training rows.

One rule binds this whole module: **a figure that could not be computed is
`None`, never `0`.** Phases 8 and 9 removed the fabricated zero at the API, and
the only thing that would bring it back is the moment a feature row reaches a
training matrix, where a column of numbers has no room for a `None` that says
*why*. `build_feature_row` therefore derives the availability mask rather than
trusting the caller, and the tests below pin the three states that mask has to
distinguish: measured-and-zero, measured-and-nonzero, and never-measured.

The other job here is protecting the contract table itself. `FEATURE_COLUMNS`
*is* the positional layout of the feature matrix — the eleventh column of
`developer_features.v1` is `project_association` forever — so a reordering or a
dropped column has to fail here rather than quietly reshuffle every model
trained against it.
"""

from __future__ import annotations

import json

import pytest

from ml.datasets.features import (
    FEATURE_COLUMNS,
    NULLABLE_COLUMNS,
    build_feature_row,
    describe_feature_contracts,
)
from ml.datasets.schema import DataValidationError, Provenance

#: The four contracts Phase 10 trains on, with the column counts the backend
#: schemas declare. A change here is a schema bump, not an edit.
EXPECTED_CONTRACTS = {
    "developer_features.v1": 11,
    "learning_features.v1": 8,
    "career_features.v1": 6,
    "analytics_features.v1": 15,
}

#: Which columns each contract may legitimately leave `None`, restated from the
#: backend schemas. Every other column is a count of something that exists, so
#: zero is always a computable answer for it.
EXPECTED_NULLABLE = {
    "developer_features.v1": frozenset({"repository_age_days", "inactivity_days"}),
    "learning_features.v1": frozenset(
        {
            "learning_minutes",
            "goal_progress",
            "goal_deadline_distance_days",
            "completion_rate",
            "learning_consistency",
            "skill_activity_frequency",
        }
    ),
    "career_features.v1": frozenset({"project_activity"}),
    "analytics_features.v1": frozenset(
        {
            "actual_minutes",
            "deadline_distance_days",
            "recent_work_minutes",
            "work_session_count",
            "time_of_day",
            "day_of_week",
            "project_velocity",
        }
    ),
}


def _counts(version: str, **overrides: float) -> dict[str, float]:
    """Every non-nullable column of ``version`` filled with a benign figure.

    Nullables are excluded so a caller can decide, per column, whether the
    subject had been observed at all.
    """
    return {
        column: 1.0
        for column in FEATURE_COLUMNS[version]
        if column not in NULLABLE_COLUMNS[version]
    } | overrides


def test_the_four_contracts_have_the_declared_column_counts():
    assert set(FEATURE_COLUMNS) == set(EXPECTED_CONTRACTS)
    for version, count in EXPECTED_CONTRACTS.items():
        assert len(FEATURE_COLUMNS[version]) == count, version
        assert len(set(FEATURE_COLUMNS[version])) == count, version


def test_the_nullable_set_matches_the_documented_contract():
    assert NULLABLE_COLUMNS == EXPECTED_NULLABLE
    for version, nullable in NULLABLE_COLUMNS.items():
        assert nullable <= set(FEATURE_COLUMNS[version]), version
        assert nullable, version


def test_a_null_on_a_nullable_column_is_derived_as_unavailable():
    row = build_feature_row(
        "developer_features.v1",
        "nexo/frontend",
        _counts("developer_features.v1", repository_age_days=None),
    )

    assert row.values["repository_age_days"] is None
    assert row.available["repository_age_days"] is False
    assert row.unavailable_columns() == ("inactivity_days", "repository_age_days")
    assert not row.is_complete()


def test_a_omitted_nullable_column_is_also_unavailable_and_invents_no_figure():
    values = _counts("developer_features.v1")
    assert "inactivity_days" not in values

    row = build_feature_row("developer_features.v1", "nexo/frontend", values)

    assert row.values["inactivity_days"] is None
    assert row.available["inactivity_days"] is False


def test_a_genuine_zero_on_a_nullable_column_stays_available():
    """`0` days of inactivity is an observation; `None` says nobody looked."""
    row = build_feature_row(
        "developer_features.v1",
        "nexo/frontend",
        _counts("developer_features.v1", repository_age_days=0, inactivity_days=0),
    )

    assert row.values["repository_age_days"] == 0
    assert row.available["repository_age_days"] is True
    assert row.available["inactivity_days"] is True
    assert row.is_complete()


def test_a_genuine_zero_on_a_count_column_stays_available():
    row = build_feature_row(
        "developer_features.v1",
        "nexo/frontend",
        _counts(
            "developer_features.v1",
            commits_last_7d=0,
            commits_last_30d=0,
            repository_age_days=0,
            inactivity_days=0,
        ),
    )

    assert row.values["commits_last_7d"] == 0
    assert row.available["commits_last_7d"] is True
    assert row.is_complete()


def test_a_null_on_a_non_nullable_column_is_refused():
    """`0` is always computable for a count, so a null claims the counter never ran."""
    with pytest.raises(DataValidationError, match="is not nullable"):
        build_feature_row(
            "developer_features.v1",
            "nexo/frontend",
            _counts("developer_features.v1", commits_last_7d=None),
        )


def test_a_figure_for_a_column_the_contract_does_not_have_is_refused():
    with pytest.raises(DataValidationError, match="columns the contract does not define"):
        build_feature_row(
            "career_features.v1",
            "utkar",
            _counts("career_features.v1", commits_last_7d=3),
        )


def test_an_unknown_contract_version_is_refused():
    with pytest.raises(DataValidationError, match="unknown feature contract"):
        build_feature_row("career_features.v2", "utkar", {})


def test_an_empty_subject_is_refused():
    with pytest.raises(DataValidationError, match="subject must be a non-empty string"):
        build_feature_row("career_features.v1", "   ", _counts("career_features.v1"))


def test_the_row_is_rectangular_and_in_contract_order():
    row = build_feature_row("career_features.v1", "utkar", _counts("career_features.v1"))

    assert tuple(row.values) == FEATURE_COLUMNS["career_features.v1"]
    assert tuple(row.available) == FEATURE_COLUMNS["career_features.v1"]
    assert set(row.values) == set(row.available)


def test_an_explicit_unavailable_flag_needs_no_figure():
    row = build_feature_row(
        "career_features.v1",
        "utkar",
        _counts("career_features.v1"),
        available={"project_activity": False},
        provenance=Provenance.REAL,
        source="career.service",
    )

    assert row.available["project_activity"] is False
    assert row.values["project_activity"] is None
    assert row.provenance is Provenance.REAL
    assert row.source == "career.service"


def test_an_explicit_unavailable_flag_may_not_carry_a_figure():
    with pytest.raises(DataValidationError, match="is marked unavailable but carries"):
        build_feature_row(
            "career_features.v1",
            "utkar",
            _counts("career_features.v1", project_activity=0.0),
            available={"project_activity": False},
        )


def test_an_explicit_available_flag_requires_a_figure():
    with pytest.raises(DataValidationError, match="is marked available but carries no figure"):
        build_feature_row(
            "career_features.v1",
            "utkar",
            _counts("career_features.v1"),
            available={"project_activity": True},
        )


def test_an_availability_flag_must_be_a_bool():
    with pytest.raises(DataValidationError, match="availability flags must be bool"):
        build_feature_row(
            "career_features.v1",
            "utkar",
            _counts("career_features.v1"),
            available={"project_activity": 0},
        )


def test_an_availability_flag_for_an_unknown_column_is_refused():
    with pytest.raises(DataValidationError, match="columns the contract does not define"):
        build_feature_row(
            "career_features.v1",
            "utkar",
            _counts("career_features.v1"),
            available={"commits_last_7d": True},
        )


def test_describe_feature_contracts_covers_all_four_versions():
    description = describe_feature_contracts()

    assert set(description) == set(EXPECTED_CONTRACTS)
    assert json.loads(json.dumps(description)) == description


@pytest.mark.parametrize("version", sorted(EXPECTED_CONTRACTS))
def test_each_described_contract_matches_the_column_table(version):
    entry = describe_feature_contracts()[version]

    assert entry["columns"] == list(FEATURE_COLUMNS[version])
    assert entry["column_count"] == len(FEATURE_COLUMNS[version])
    assert entry["nullable_columns"] == sorted(EXPECTED_NULLABLE[version])
    assert entry["required_columns"] == sorted(
        set(FEATURE_COLUMNS[version]) - EXPECTED_NULLABLE[version]
    )
    assert list(entry["column_meanings"]) == list(FEATURE_COLUMNS[version])


@pytest.mark.parametrize("version", sorted(EXPECTED_CONTRACTS))
def test_every_described_column_carries_a_meaning(version):
    entry = describe_feature_contracts()[version]

    for column, meaning in entry["column_meanings"].items():
        assert meaning.strip(), (version, column)
        assert len(meaning) > 20, (version, column)


def test_describe_feature_contracts_is_stable_across_calls():
    assert describe_feature_contracts() == describe_feature_contracts()


def test_the_career_null_contract_is_the_documented_worked_example():
    """`project_activity` is null until a repository has been scanned, never 0."""
    unscanned = build_feature_row("career_features.v1", "utkar", _counts("career_features.v1"))
    scanned = build_feature_row(
        "career_features.v1", "utkar", _counts("career_features.v1", project_activity=0.0)
    )

    assert unscanned.available["project_activity"] is False
    assert scanned.available["project_activity"] is True
    assert scanned.values["project_activity"] == 0.0
