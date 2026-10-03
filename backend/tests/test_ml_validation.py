"""The data-integrity gate between building a dataset and training on it.

Every builder in `ml.datasets` can produce records; none of them can produce
*trustworthy* records. A template generator can emit the same sentence twice, a
generator can attach two intents to one utterance, and someone pasting a real
task list into a prompt can carry a key along with it. None of those announce
themselves at training time — the run converges and reports a number that is
quietly wrong.

The ERROR/WARNING asymmetry is the design, and the tests below pin it in both
directions: a contradictory label or a credential must stop the run, while a
lopsided class histogram or a near-duplicate must not, because real user
requests are lopsided and repetitive and refusing to train on reality would
refuse to train at all.

Two properties are asserted repeatedly because they are the ones that make the
gate trustworthy: a report never reproduces the secret it rejected, and
`assert_clean` raises rather than returning a boolean a caller can ignore.
"""

from __future__ import annotations

import json
from collections import defaultdict

import pytest

from ml.datasets.schema import (
    SCHEMA_VERSION_FEATURES,
    SCHEMA_VERSION_ROUTING,
    DataValidationError,
)
from ml.datasets.taxonomy import INTENT_NAMES
from ml.preprocessing.normalize import near_duplicate_key
from ml.validation import (
    VALIDATION_REPORT_VERSION,
    Finding,
    Severity,
    assert_clean,
    validate_feature_dataset,
    validate_no_credentials,
    validate_routing_dataset,
    validate_splits,
)

# Fabricated, never a real token. Built from repeated characters so it cannot
# collide with anything in the repository or in anybody's environment.
FAKE_KAGGLE = "KAGGLE_" + "z" * 32


def _routing(text: str, intent: str, **extra) -> dict:
    return {
        "schema_version": SCHEMA_VERSION_ROUTING,
        "text": text,
        "intent": intent,
        "provenance": "synthetic",
        "template_id": extra.get("template_id", "t-1"),
        "source": extra.get("source", "unit-test"),
    }


def _validate_routing(records, *, max_class_ratio: float = 3.0):
    return validate_routing_dataset(
        records, known_intents=INTENT_NAMES, max_class_ratio=max_class_ratio
    )


def test_a_clean_routing_dataset_passes_with_a_class_histogram():
    records = [_routing(f"request number {index}", "task_manage") for index in range(6)]

    report = _validate_routing(records)

    assert report.passed
    assert report.findings == ()
    assert report.counts["records"] == 6
    assert report.counts["usable"] == 6
    assert report.counts["class:task_manage"] == 6
    assert report.counts["known_intents"] == len(INTENT_NAMES)


def test_a_non_positive_max_class_ratio_is_refused():
    with pytest.raises(ValueError, match="max_class_ratio must be positive"):
        _validate_routing([_routing("a request", "task_manage")], max_class_ratio=0.0)


def test_a_label_outside_the_taxonomy_is_an_error():
    """The router can only act on an intent the application knows how to execute."""
    records = [
        _routing("Mark the API contract task as done", "task_manage"),
        _routing("Reschedule the failing request", "complete_blocked_task"),
    ]

    report = _validate_routing(records)

    assert not report.passed
    assert "invalid_label" in report.codes()
    assert report.counts["invalid_labels"] == 1
    assert any(finding.severity is Severity.ERROR for finding in report.errors())


def test_a_text_repeated_verbatim_is_an_error():
    records = [
        _routing("Mark the API contract task as done", "task_manage"),
        _routing("Mark the API contract task as done", "task_manage", template_id="t-2"),
    ]

    report = _validate_routing(records)

    assert not report.passed
    assert "duplicate_text" in report.codes()


def test_one_text_carrying_two_intents_is_a_contradiction_and_an_error():
    """Redundancy is a warning; a contradiction is unrecoverable by any procedure."""
    records = [
        _routing("Add the task", "task_manage"),
        _routing("add the task!", "project_manage"),
    ]

    report = _validate_routing(records)

    assert not report.passed
    assert "contradictory_label" in report.codes()
    assert report.counts["contradictions"] == 1
    # The contradicting group is reported once, at ERROR, and not also as a warning.
    assert "near_duplicate_text" not in report.codes()


def test_class_imbalance_is_a_warning_and_the_run_proceeds():
    records = [_routing(f"request number {index}", "task_manage") for index in range(9)]
    records.append(_routing("one rare request", "career_track"))

    report = _validate_routing(records, max_class_ratio=2.0)

    assert report.passed
    assert report.codes() == ("class_imbalance",)
    assert report.warnings()[0].severity is Severity.WARNING
    assert report.errors() == ()


def test_a_balanced_dataset_raises_no_imbalance_warning():
    records = [_routing(f"a number {index}", "task_manage") for index in range(5)]
    records += [_routing(f"b number {index}", "career_track") for index in range(5)]

    report = _validate_routing(records, max_class_ratio=2.0)

    assert "class_imbalance" not in report.codes()
    assert report.passed


def test_an_unknown_schema_version_is_refused_rather_than_coerced():
    records = [
        {
            "schema_version": "routing_intent.v9",
            "text": "Mark the API contract task as done",
            "intent": "task_manage",
        }
    ]

    report = _validate_routing(records)

    assert not report.passed
    assert "unknown_schema_version" in report.codes()


def test_a_record_with_no_schema_version_is_refused():
    records = [{"text": "Mark the API contract task as done", "intent": "task_manage"}]

    report = _validate_routing(records)

    assert not report.passed
    assert "missing_schema_version" in report.codes()


def test_a_record_that_is_not_an_object_is_a_malformed_record():
    report = _validate_routing(["not a record at all"])

    assert not report.passed
    assert "malformed_record" in report.codes()


def test_a_record_missing_the_text_the_model_reads_is_refused():
    records = [
        {
            "schema_version": SCHEMA_VERSION_ROUTING,
            "text": "",
            "intent": "task_manage",
        }
    ]

    report = _validate_routing(records)

    assert not report.passed
    assert "missing_field" in report.codes()


def test_a_credential_in_a_record_is_an_error_and_is_never_reproduced():
    """The finding names the kind and quotes redacted text. Never the value."""
    records = [
        _routing("Mark the API contract task as done", "task_manage"),
        _routing(f"my token is {FAKE_KAGGLE}", "task_manage", template_id="t-2"),
    ]

    report = _validate_routing(records)

    assert not report.passed
    assert "credential_detected" in report.codes()

    serialised = json.dumps(report.to_dict(), sort_keys=True)
    markdown = report.to_markdown()
    assert FAKE_KAGGLE not in serialised
    assert FAKE_KAGGLE not in markdown
    assert "kaggle_token" in serialised
    assert "[REDACTED]" in serialised


def test_near_duplicates_are_a_warning_rather_than_an_error():
    records = [
        _routing("Review deadline for migration plan", "task_manage"),
        _routing("migration plan review deadline for", "task_manage", template_id="t-2"),
    ]

    report = _validate_routing(records)

    assert report.passed
    assert report.codes() == ("near_duplicate_text",)


def test_a_feature_row_dataset_reports_incompleteness_as_a_warning():
    records = [
        {
            "schema_version": SCHEMA_VERSION_FEATURES,
            "source_schema_version": "developer_features.v1",
            "subject": "nexo/backend",
            "values": {"commits_last_7d": 0, "repository_age_days": None},
            "available": {"commits_last_7d": True, "repository_age_days": False},
            "provenance": "derived",
            "source": "developer.feature_snapshot",
        }
    ]

    report = validate_feature_dataset(records)

    assert report.passed
    assert report.codes() == ("incomplete_row",)
    assert report.counts["complete_rows"] == 0
    assert report.counts["incomplete_rows"] == 1


def test_a_feature_row_with_a_value_behind_a_false_flag_is_refused():
    records = [
        {
            "schema_version": SCHEMA_VERSION_FEATURES,
            "source_schema_version": "developer_features.v1",
            "subject": "nexo/backend",
            "values": {"repository_age_days": 0},
            "available": {"repository_age_days": False},
        }
    ]

    report = validate_feature_dataset(records)

    assert not report.passed
    assert "malformed_record" in report.codes()


def test_a_repeated_feature_row_is_an_error():
    row = {
        "schema_version": SCHEMA_VERSION_FEATURES,
        "source_schema_version": "career_features.v1",
        "subject": "utkar",
        "values": {"projects_completed": 2},
        "available": {"projects_completed": True},
    }

    report = validate_feature_dataset([row, dict(row)])

    assert not report.passed
    assert "duplicate_row" in report.codes()


def test_validate_splits_passes_on_a_correct_partition():
    """The positive control. Without it, the leakage test below proves nothing."""
    items = {
        "train": ["add the migration task", "review the open risks"],
        "validation": ["schedule a focus block", "show my open projects"],
        "test": ["save a note about the rotation", "compare my throughput"],
    }

    report = validate_splits(items, duplicate_key_fn=near_duplicate_key)

    assert report.passed
    assert report.counts["leaked_keys"] == 0
    assert report.counts["total_items"] == 6
    assert report.counts["unique_keys"] == 6


def test_validate_splits_fails_when_leakage_is_injected():
    """Moving one paraphrase across the boundary must turn the report red."""
    items = {
        "train": ["add the migration task", "review the open risks"],
        "validation": ["task migration add the", "show my open projects"],
        "test": ["save a note about the rotation", "compare my throughput"],
    }

    report = validate_splits(items, duplicate_key_fn=near_duplicate_key)

    assert not report.passed
    assert "split_leakage" in report.codes()
    assert report.counts["leaked_keys"] == 1


def test_validate_splits_warns_about_an_empty_split():
    report = validate_splits(
        {"train": ["one", "two"], "validation": [], "test": ["three"]},
        duplicate_key_fn=near_duplicate_key,
    )

    assert "empty_split" in report.codes()
    assert report.passed, "an empty evaluation split is a warning, not corruption"


def test_validate_no_credentials_flags_kinds_and_withholds_the_value():
    texts = ["a clean line", f"token {FAKE_KAGGLE}", "hf_" + "q" * 24]

    report = validate_no_credentials(texts)

    assert not report.passed
    assert report.codes() == ("credential_detected", "credential_detected")
    assert report.counts["texts"] == 3
    assert report.counts["flagged"] == 2
    assert FAKE_KAGGLE not in json.dumps(report.to_dict())
    assert "kaggle_token" in json.dumps(report.to_dict())


def test_validate_no_credentials_passes_on_clean_text():
    report = validate_no_credentials(["a clean line", "another clean line"])

    assert report.passed
    assert report.counts["flagged"] == 0


def test_assert_clean_passes_a_clean_report():
    report = _validate_routing([_routing("Mark the task done", "task_manage")])

    assert_clean(report)


def test_assert_clean_raises_on_an_error_report():
    records = [_routing("Add the task", "task_manage"), _routing("add the task!", "project_manage")]
    report = _validate_routing(records)

    with pytest.raises(DataValidationError, match="contradictory_label"):
        assert_clean(report)


def test_assert_clean_does_not_raise_on_warnings_only():
    """A lopsided corpus is imperfect, not corrupt; refusing it would refuse reality."""
    records = [_routing(f"request number {index}", "task_manage") for index in range(9)]
    records.append(_routing("one rare request", "career_track"))
    report = _validate_routing(records, max_class_ratio=2.0)

    assert report.warnings()
    assert report.passed
    assert_clean(report)


def test_assert_clean_names_every_failing_report():
    clean = _validate_routing([_routing("Mark the task done", "task_manage")])
    broken = _validate_routing(
        [_routing("Add the task", "task_manage"), _routing("add the task!", "project_manage")]
    )

    with pytest.raises(DataValidationError) as excinfo:
        assert_clean(clean, broken)

    assert "routing_intent.v1" in str(excinfo.value)
    assert "contradictory_label" in str(excinfo.value)


def test_a_report_serialises_deterministically_with_its_version():
    records = [_routing(f"request number {index}", "task_manage") for index in range(4)]
    report = _validate_routing(records)

    payload = report.to_dict()

    assert payload["report_version"] == VALIDATION_REPORT_VERSION
    assert payload["name"] == SCHEMA_VERSION_ROUTING
    assert payload["schema_version"] == SCHEMA_VERSION_ROUTING
    assert payload["passed"] is True
    assert json.loads(json.dumps(payload)) == payload
    assert report.to_dict() == payload


def test_report_markdown_leads_with_the_verdict():
    records = [_routing(f"request number {index}", "task_manage") for index in range(4)]
    report = _validate_routing(records)

    markdown = report.to_markdown()

    assert "## Validation report: routing_intent.v1" in markdown
    assert "**PASS** - training may proceed." in markdown
    assert VALIDATION_REPORT_VERSION in markdown


def test_failing_report_markdown_says_the_run_must_stop():
    records = [_routing("Add the task", "task_manage"), _routing("add the task!", "project_manage")]

    markdown = _validate_routing(records).to_markdown()

    assert "**FAIL** - training must stop" in markdown


def test_finding_serialises_its_severity_and_sample():
    finding = Finding("code", Severity.WARNING, "a message", ("sample",))

    assert finding.to_dict() == {
        "code": "code",
        "severity": "warning",
        "message": "a message",
        "sample": ["sample"],
    }


def test_a_report_with_no_findings_still_renders():
    report = _validate_routing([_routing("Mark the task done", "task_manage")])

    assert "_No findings._" in report.to_markdown()


def test_validation_is_deterministic_for_the_same_input():
    records = [_routing(f"request number {index}", "task_manage") for index in range(4)]

    first = _validate_routing(records)
    second = _validate_routing(records)

    assert first.to_dict() == second.to_dict()
    assert first.to_markdown() == second.to_markdown()


def test_a_leaked_split_over_a_real_corpus_is_detected_when_injected():
    """End-to-end positive control over generated records, not a fixture."""
    from ml.datasets.routing import build_routing_records
    from ml.preprocessing.splits import SplitConfig, assign_leakage_free_splits

    records = build_routing_records(per_intent=4)
    result = assign_leakage_free_splits(
        records,
        key_field="text",
        label_field="intent",
        duplicate_key_fn=lambda row: near_duplicate_key(row["text"]),
        config=SplitConfig(seed=20260101),
    )
    grouped: dict[str, list[str]] = defaultdict(list)
    for key, split_name in result.assignments.items():
        grouped[split_name].append(key)

    assert validate_splits(grouped, duplicate_key_fn=near_duplicate_key).passed

    # Inject leakage: a reworded twin of a training row is dropped into the
    # held-out split. The key is order-insensitive, so the twin collides.
    train_text = grouped["train"][0]
    twin = " ".join(reversed(train_text.split()))

    grouped["test"].append(twin)

    leaked = validate_splits(grouped, duplicate_key_fn=near_duplicate_key)

    assert not leaked.passed
    assert "split_leakage" in leaked.codes()
    assert leaked.counts["leaked_keys"] == 1
