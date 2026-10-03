"""The record of what a training run *was*.

A fine-tune that cannot be described cannot be repeated, and one that cannot be
repeated cannot be believed. This module writes the single document that makes a
run reproducible, and every field in it exists because of the failure it
prevents: the commit and the dirty flag answer *"was this the code I think it
was?"* together (a clean sha from a dirty tree is a lie), the dataset checksum
answers *"the same rows?"*, the seed answers *"the same split?"*, and the
environment answers *"the same library versions?"*.

Two properties are asserted here rather than assumed. `collect_environment`
must answer whether torch is present **without importing it** — the module whose
entire job is to describe the machine must not cost a gigabyte of resident
memory doing it, and must behave identically on the torch-less laptop where the
manifest is written and the GPU box where it is not. And `to_markdown` must run
its output through redaction, because `config` and `environment` are free-form
and an operator note is a perfectly ordinary place for a token to end up in a
document people paste into tickets.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from ml.datasets.schema import sha256_file, sha256_text
from ml.preprocessing.normalize import find_credential
from ml.training.manifest import (
    GIT_TIMEOUT_SECONDS,
    MANIFEST_VERSION,
    UNKNOWN_COMMIT,
    RunManifest,
    checksum_file,
    collect_environment,
    git_revision,
    new_run_id,
)

#: ``backend/`` -> ``backend/ml/`` -> ``backend/`` -> the repository root.
REPO_ROOT = Path(__file__).resolve().parents[2]

WHEN = datetime(2026, 1, 1, 13, 14, 5, tzinfo=UTC)

# Fabricated, never a real token.
FAKE_ASSIGNED = "api_key = 4f8a2c1e9b7d6035a8c2e"


def _manifest(**overrides) -> RunManifest:
    defaults = {
        "run_id": new_run_id(
            "small", when=WHEN, dataset_version="routing_dataset.v1", seed=20260101
        ),
        "model_name": "microsoft/deberta-v3-base",
        "base_model": "microsoft/deberta-v3-base",
        "model_version": "routing_intent.v1",
        "dataset_version": "routing_dataset.v1",
        "dataset_source": "ml/datasets/routing",
        "schema_version": "routing_intent.v1",
        "preprocessing_version": "nexo_splits.v1",
        "code_commit": "a" * 40,
        "code_dirty": False,
        "config": {"max_seq_length": 128},
        "hyperparameters": {"learning_rate": 2e-5, "num_train_epochs": 3},
        "seed": 20260101,
        "environment": collect_environment(),
        "started_at": "2026-01-01T13:14:05+00:00",
    }
    return RunManifest(**{**defaults, **overrides})


def test_a_run_id_sorts_chronologically_and_is_deterministic():
    run_id = new_run_id("small", when=WHEN, dataset_version="routing_dataset.v1", seed=20260101)

    assert run_id == new_run_id(
        "small", when=WHEN, dataset_version="routing_dataset.v1", seed=20260101
    )
    assert run_id.startswith("small-20260101T131405Z-")
    assert re.fullmatch(r"[a-z]+-\d{8}T\d{6}Z-[0-9a-f]{8}", run_id), run_id


def test_a_run_id_changes_with_the_seed_the_dataset_or_the_clock():
    base = new_run_id("small", when=WHEN, dataset_version="v1", seed=1)

    assert base != new_run_id("small", when=WHEN, dataset_version="v1", seed=2)
    assert base != new_run_id("small", when=WHEN, dataset_version="v2", seed=1)
    assert base != new_run_id(
        "small", when=datetime(2026, 1, 1, 13, 14, 6, tzinfo=UTC), dataset_version="v1", seed=1
    )


def test_run_ids_sort_in_the_order_the_runs_happened():
    earlier = new_run_id("qwen", when=datetime(2026, 1, 1, 9, 0, 0, tzinfo=UTC))
    later = new_run_id("qwen", when=datetime(2026, 1, 2, 9, 0, 0, tzinfo=UTC))

    assert sorted([later, earlier]) == [earlier, later]


def test_a_naive_timestamp_is_read_as_local_and_converted_to_utc():
    naive = datetime(2026, 1, 1, 13, 14, 5)
    aware = new_run_id("small", when=naive)

    assert re.fullmatch(r"small-\d{8}T\d{6}Z-[0-9a-f]{8}", aware)


def test_git_revision_reports_a_full_sha_for_this_repository():
    """The real repository, not a fixture: a 40-char sha is the deliverable."""
    commit, dirty = git_revision(REPO_ROOT)

    assert commit != UNKNOWN_COMMIT
    assert re.fullmatch(r"[0-9a-f]{40}", commit), commit
    assert isinstance(dirty, bool)


def test_git_revision_degrades_rather_than_failing_outside_a_repository(tmp_path):
    """A manifest must still be written from a tarball with no ``.git`` in it."""
    commit, dirty = git_revision(tmp_path)

    assert commit == UNKNOWN_COMMIT
    assert dirty is False, "asserting 'the tree matched' about a commit nobody can name is the lie"
    assert GIT_TIMEOUT_SECONDS > 0


def test_collect_environment_describes_the_machine_without_importing_torch():
    import sys

    environment = collect_environment()

    assert set(environment) == {
        "python_version",
        "platform",
        "torch_available",
        "torch_version",
    }
    assert environment["torch_available"] == ("torch" in sys.modules or _torch_importable())
    assert "torch" not in sys.modules, "collect_environment must not import torch to describe it"
    assert isinstance(environment["platform"], str)
    assert environment["python_version"].count(".") == 2
    assert (environment["torch_version"] is None) != environment["torch_available"]


def _torch_importable() -> bool:
    from importlib.util import find_spec

    return find_spec("torch") is not None


def test_collect_environment_is_json_serialisable_and_stable():
    environment = collect_environment()

    assert json.loads(json.dumps(environment)) == environment


def test_a_manifest_serialises_to_json_with_its_version():
    payload = _manifest().to_dict()

    assert payload["manifest_version"] == MANIFEST_VERSION
    assert json.loads(json.dumps(payload)) == payload
    assert payload["run_id"].startswith("small-")
    assert payload["code_commit"] == "a" * 40
    assert payload["code_dirty"] is False


def test_a_manifest_records_what_a_run_produced():
    manifest = _manifest(
        finished_at="2026-01-01T14:00:00+00:00",
        duration_seconds=2755.0,
        checkpoints=({"global_step": 100}, {"global_step": 200}),
        evaluation={"accuracy": 0.8214, "macro_f1": 0.8197},
        artifacts={"adapter": "ml/artifacts/small-model/final"},
        checksums={"adapter": "b" * 64},
    )

    payload = manifest.to_dict()

    assert payload["finished_at"] == "2026-01-01T14:00:00+00:00"
    assert payload["duration_seconds"] == 2755.0
    assert len(payload["checkpoints"]) == 2
    assert payload["evaluation"]["accuracy"] == 0.8214
    assert set(payload["artifacts"]) == set(payload["checksums"])


def test_an_unfinished_run_leaves_the_completion_fields_empty():
    """A crashed run must still leave evidence of which data and commit it was on."""
    payload = _manifest().to_dict()

    assert payload["finished_at"] is None
    assert payload["duration_seconds"] is None
    assert payload["checkpoints"] == []
    assert payload["evaluation"] == {}


def test_a_manifest_detaches_the_mappings_the_caller_handed_in():
    """A manifest a caller can still edit after the fact documents nothing."""
    config = {"max_seq_length": 128}
    manifest = _manifest(config=config)

    config["max_seq_length"] = 999

    assert manifest.config["max_seq_length"] == 128


def test_manifest_markdown_leads_with_the_run_and_lists_every_artifact():
    manifest = _manifest(
        artifacts={"adapter": "ml/artifacts/small-model/final"},
        checksums={"adapter": "b" * 64},
        checkpoints=({"global_step": 100},),
    )

    markdown = manifest.to_markdown()

    assert markdown.startswith("# Run manifest `small-")
    assert manifest.code_commit in markdown
    assert "ml/artifacts/small-model/final" in markdown
    assert "b" * 64 in markdown
    assert "code_dirty" in markdown


def test_manifest_markdown_redacts_a_token_pasted_into_a_free_form_field():
    """`config` is exactly where an operator note with a key would end up."""
    manifest = _manifest(config={"operator_note": FAKE_ASSIGNED})

    markdown = manifest.to_markdown()

    assert "4f8a2c1e9b7d6035a8c2e" not in markdown
    assert find_credential(markdown) is None
    assert "[REDACTED]" in markdown


def test_manifest_markdown_says_an_unfinished_run_is_unfinished():
    markdown = _manifest().to_markdown()

    assert "not finished" in markdown


def test_checksum_file_matches_the_schema_helper(tmp_path):
    target = tmp_path / "adapter.safetensors"
    target.write_bytes(b"payload")

    assert checksum_file(target) == sha256_file(target)
    assert checksum_file(target) == sha256_text("payload")
    assert len(checksum_file(target)) == 64


def test_a_manifest_over_the_real_environment_still_serialises():
    """End-to-end shape: what the pipeline actually writes on this machine."""
    commit, dirty = git_revision(REPO_ROOT)
    manifest = RunManifest(
        run_id=new_run_id("qwen", when=WHEN, dataset_version="qwen_dataset.v1", seed=20260101),
        model_name="Qwen/Qwen3-8B",
        base_model="Qwen/Qwen3-8B",
        model_version="qwen_sft.v1",
        dataset_version="qwen_dataset.v1",
        dataset_source="ml/datasets/qwen_sft",
        schema_version="qwen_sft.v1",
        preprocessing_version="nexo_splits.v1",
        code_commit=commit,
        code_dirty=dirty,
        config={"method": "qlora"},
        hyperparameters={"lora_r": 16},
        seed=20260101,
        environment=collect_environment(),
        started_at="2026-01-01T13:14:05+00:00",
    )

    payload = manifest.to_dict()

    assert json.loads(json.dumps(payload, sort_keys=True)) == payload
    assert find_credential(manifest.to_markdown()) is None
    assert find_credential(json.dumps(payload, sort_keys=True)) is None
