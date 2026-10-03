"""Record schemas for the Phase 10 datasets.

These three record types are the contract between a builder and a trainer that
run on different machines at different times. If `to_dict`/`from_dict` drift
apart, a dataset written today still loads tomorrow — and quietly, the way a
`values` map silently loses the distinction between *"nobody looked"* and
*"we looked and found nothing"*, which is the exact defect the Phases 8/9
remediation removed at the API. The round-trip tests below are what keeps that
distinction from being reintroduced in the serialiser.

The failure paths matter at least as much as the happy one. A validator that
cannot say *why* a row was rejected produces a report nobody can act on, so
every rejection below asserts both the exception type and that the message
names the offending field or column.
"""

from __future__ import annotations

import json

import pytest

from ml.datasets.schema import (
    SCHEMA_VERSION_FEATURES,
    SCHEMA_VERSION_ROUTING,
    DatasetError,
    DataValidationError,
    FeatureRow,
    Provenance,
    RoutingExample,
    read_jsonl,
    sha256_file,
    sha256_text,
    stable_json_dumps,
    write_jsonl,
)

# sha256 of the empty string, fixed by the standard rather than by this code.
EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_routing_example_round_trips():
    original = RoutingExample(
        text="Mark the API contract task as done",
        intent="task_manage",
        provenance=Provenance.SYNTHETIC,
        template_id="task-manage-curated-3",
        source="ml.datasets.routing",
    )

    restored = RoutingExample.from_dict(original.to_dict())

    assert restored == original
    assert restored.to_dict() == original.to_dict()
    assert restored.to_dict()["schema_version"] == SCHEMA_VERSION_ROUTING


def test_routing_example_defaults_provenance_to_synthetic():
    """A row that says nothing about where it came from is a row nobody can rebuild."""
    restored = RoutingExample.from_dict({"text": "What tasks are open?", "intent": "task_manage"})

    assert restored.provenance is Provenance.SYNTHETIC
    assert restored.template_id == ""
    assert restored.source == ""


@pytest.mark.parametrize("missing", ["text", "intent"])
def test_routing_example_refuses_a_missing_required_field(missing):
    record = {"text": "Add a task", "intent": "task_manage"}
    del record[missing]

    with pytest.raises(DataValidationError, match=missing):
        RoutingExample.from_dict(record)


@pytest.mark.parametrize("blank", ["", "   "])
def test_routing_example_refuses_a_blank_required_field(blank):
    with pytest.raises(DataValidationError, match="text"):
        RoutingExample.from_dict({"text": blank, "intent": "task_manage"})


def test_routing_example_refuses_an_unknown_provenance():
    with pytest.raises(DataValidationError, match="provenance"):
        RoutingExample.from_dict(
            {"text": "Add a task", "intent": "task_manage", "provenance": "hallucinated"}
        )


def test_feature_row_reports_its_unavailable_columns():
    row = FeatureRow(
        source_schema_version="developer_features.v1",
        subject="nexo/backend",
        values={"commits_last_7d": 0, "repository_age_days": None},
        available={"commits_last_7d": True, "repository_age_days": False},
    )

    assert row.unavailable_columns() == ("repository_age_days",)
    assert not row.is_complete()


def test_feature_row_treats_a_genuine_zero_as_available():
    """``0`` commits is a measurement. Collapsing it into "unmeasured" is the defect."""
    row = FeatureRow(
        source_schema_version="developer_features.v1",
        subject="nexo/backend",
        values={"commits_last_7d": 0},
        available={"commits_last_7d": True},
    )

    assert row.available["commits_last_7d"] is True
    assert row.values["commits_last_7d"] == 0
    assert row.is_complete()


def test_feature_row_rejects_an_unavailable_column_that_carries_a_value():
    """The failure this whole mask exists to prevent: a figure behind a false flag."""
    with pytest.raises(DataValidationError, match="marked unavailable but carries"):
        FeatureRow.from_dict(
            {
                "source_schema_version": "developer_features.v1",
                "subject": "nexo/backend",
                "values": {"repository_age_days": 0},
                "available": {"repository_age_days": False},
            }
        )


def test_feature_row_rejects_a_value_column_with_no_availability_flag():
    with pytest.raises(DataValidationError, match="availability flag"):
        FeatureRow.from_dict(
            {
                "source_schema_version": "developer_features.v1",
                "subject": "nexo/backend",
                "values": {"commits_last_7d": 3},
                "available": {},
            }
        )


def test_feature_row_rejects_a_non_bool_availability_flag():
    with pytest.raises(DataValidationError, match="must be a bool"):
        FeatureRow.from_dict(
            {
                "source_schema_version": "developer_features.v1",
                "subject": "nexo/backend",
                "values": {"commits_last_7d": 3},
                "available": {"commits_last_7d": "yes"},
            }
        )


def test_feature_row_rejects_maps_that_are_not_objects():
    with pytest.raises(DataValidationError, match="must both be objects"):
        FeatureRow.from_dict(
            {
                "source_schema_version": "developer_features.v1",
                "subject": "nexo/backend",
                "values": ["commits_last_7d"],
                "available": {},
            }
        )


def test_feature_row_round_trips():
    original = FeatureRow(
        source_schema_version="career_features.v1",
        subject="utkar",
        values={"projects_completed": 2, "project_activity": None},
        available={"projects_completed": True, "project_activity": False},
        provenance=Provenance.DERIVED,
        source="career.feature_snapshot",
    )

    restored = FeatureRow.from_dict(original.to_dict())

    assert restored == original
    assert restored.to_dict() == original.to_dict()
    assert restored.to_dict()["schema_version"] == SCHEMA_VERSION_FEATURES


def test_write_jsonl_and_read_jsonl_round_trip(tmp_path):
    records = [
        {"schema_version": SCHEMA_VERSION_ROUTING, "text": "Add a task", "intent": "task_manage"},
        {
            "schema_version": SCHEMA_VERSION_ROUTING,
            "text": "Show projects",
            "intent": "project_manage",
        },
    ]
    destination = tmp_path / "nested" / "routing_train.jsonl"

    written = write_jsonl(destination, records)

    assert written == 2
    assert read_jsonl(destination) == records


def test_write_jsonl_creates_missing_parents_and_returns_zero_for_no_records(tmp_path):
    destination = tmp_path / "a" / "b" / "empty.jsonl"

    assert write_jsonl(destination, []) == 0
    assert read_jsonl(destination) == []


def test_read_jsonl_skips_blank_lines_without_dropping_real_rows(tmp_path):
    destination = tmp_path / "routing.jsonl"
    destination.write_text(
        '{"text": "one", "intent": "task_manage"}\n'
        "\n"
        "   \n"
        '{"text": "two", "intent": "project_manage"}\n',
        encoding="utf-8",
    )

    assert read_jsonl(destination) == [
        {"text": "one", "intent": "task_manage"},
        {"text": "two", "intent": "project_manage"},
    ]


def test_read_jsonl_names_the_line_of_a_malformed_record(tmp_path):
    """A dataset that quietly loses rows is worse than one that refuses to load."""
    destination = tmp_path / "broken.jsonl"
    destination.write_text(
        '{"text": "one", "intent": "task_manage"}\n{not json at all}\n',
        encoding="utf-8",
    )

    with pytest.raises(DatasetError, match=r"broken\.jsonl:2: malformed JSON"):
        read_jsonl(destination)


def test_read_jsonl_rejects_a_line_that_is_not_an_object(tmp_path):
    destination = tmp_path / "scalar.jsonl"
    destination.write_text('["not", "an", "object"]\n', encoding="utf-8")

    with pytest.raises(DatasetError, match="expected a JSON object"):
        read_jsonl(destination)


def test_read_jsonl_reports_a_missing_file(tmp_path):
    with pytest.raises(DatasetError, match="dataset not found"):
        read_jsonl(tmp_path / "absent.jsonl")


def test_write_jsonl_output_is_canonical_and_reparses_identically(tmp_path):
    records = [{"b": 2, "a": 1}]
    destination = tmp_path / "canonical.jsonl"

    write_jsonl(destination, records)

    line = destination.read_text(encoding="utf-8")
    assert line == '{"a":1,"b":2}\n'
    assert json.loads(line) == records[0]


def test_stable_json_dumps_sorts_keys_and_keeps_non_ascii():
    assert stable_json_dumps({"b": 1, "a": "café"}) == '{"a":"café","b":1}'


def test_sha256_text_is_stable_and_matches_the_known_empty_digest():
    assert sha256_text("") == EMPTY_SHA256
    assert sha256_text("nexo") == sha256_text("nexo")
    assert sha256_text("nexo") != sha256_text("nexo ")
    assert len(sha256_text("anything")) == 64


def test_sha256_file_matches_sha256_text_and_is_stable(tmp_path):
    destination = tmp_path / "payload.bin"
    destination.write_bytes(b"nexo phase 10")

    digest = sha256_file(destination)

    assert digest == sha256_text("nexo phase 10")
    assert digest == sha256_file(destination)
    assert len(digest) == 64


def test_sha256_file_reports_a_missing_file(tmp_path):
    with pytest.raises(DatasetError, match="cannot hash"):
        sha256_file(tmp_path / "absent.bin")
