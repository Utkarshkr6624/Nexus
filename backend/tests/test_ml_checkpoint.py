"""Resumable checkpoints as plain files, so this half needs no torch.

A checkpoint is a directory: payload files plus one `checkpoint.json` describing
them. The machine that resumes a run is not the machine that trains it, and this
module is what the laptop without torch polls to answer *"which checkpoint is the
furthest I got, and is it whole?"*.

The crash contract is the whole design: `checkpoint.json` is written last, so a
directory that exists without its metadata is an interrupted write rather than a
checkpoint. Resuming from a half-written one corrupts a run in a way that only
surfaces hours later as a nonsense loss curve, so every reader refuses it —
`is_resumable` silently, `latest_checkpoint` with a warning naming the reason,
and `load_checkpoint` loudly, because the caller named that directory
explicitly and there is no other one to fall back to.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from ml.training.checkpoint import (
    CHECKPOINT_FORMAT_VERSION,
    CheckpointError,
    CheckpointMetadata,
    is_resumable,
    latest_checkpoint,
    list_checkpoints,
    load_checkpoint,
    save_checkpoint,
)

SEED = 20260101


def _metadata(global_step: int, **overrides) -> CheckpointMetadata:
    defaults = {
        "format_version": CHECKPOINT_FORMAT_VERSION,
        "run_id": "small-20260101T000000Z-abcd1234",
        "model_name": "microsoft/deberta-v3-base",
        "global_step": global_step,
        "epoch": 1,
        "segment_index": 0,
        "segments_completed": 1,
        "dataset_version": "routing_dataset.v1",
        "dataset_checksum": "0" * 64,
        "code_commit": "a" * 40,
        "config": {"max_seq_length": 128},
        "hyperparameters": {"learning_rate": 2e-5},
        "seed": SEED,
        "created_at": "2026-01-01T00:00:00+00:00",
        "elapsed_seconds": 12.5,
        "files": {},
    }
    return CheckpointMetadata(**{**defaults, **overrides})


def _save(directory: Path, step: int, *, tensors=None, **overrides):
    return save_checkpoint(
        directory,
        metadata=_metadata(step, **overrides),
        tensors={"adapter.safetensors": b"payload"} if tensors is None else tensors,
    )


def test_a_saved_checkpoint_round_trips(tmp_path):
    state = _save(tmp_path / "step-100", 100)

    loaded = load_checkpoint(state.directory)

    assert loaded.directory == state.directory
    assert loaded.metadata == state.metadata
    assert loaded.metadata.global_step == 100
    assert (state.directory / "adapter.safetensors").read_bytes() == b"payload"


def test_the_metadata_file_is_the_commit_point(tmp_path):
    """Its presence is what makes the surrounding files a checkpoint."""
    directory = tmp_path / "step-100"
    _save(directory, 100)

    assert (directory / "checkpoint.json").is_file()
    payload = json.loads((directory / "checkpoint.json").read_text(encoding="utf-8"))
    assert payload["format_version"] == CHECKPOINT_FORMAT_VERSION
    assert payload["global_step"] == 100
    assert payload["files"] == {"adapter.safetensors": "adapter.safetensors"}


def test_a_name_without_an_extension_gets_a_bin_suffix(tmp_path):
    state = _save(tmp_path / "step-1", 1, tensors={"optimizer": b"state"})

    assert state.metadata.files["optimizer"] == "optimizer.bin"
    assert (state.directory / "optimizer.bin").read_bytes() == b"state"


def test_extra_state_is_registered_under_the_extra_key(tmp_path):
    state = save_checkpoint(
        tmp_path / "step-1",
        metadata=_metadata(1),
        tensors={"adapter.safetensors": b"payload"},
        extra={"optimizer_step": 3},
    )

    assert state.metadata.files["extra"] == "extra.json"
    payload = json.loads((state.directory / "extra.json").read_text(encoding="utf-8"))
    assert payload == {"optimizer_step": 3}
    assert is_resumable(state.directory)


def test_the_sidecar_hook_writes_into_the_same_directory_before_the_commit(tmp_path):
    written: list[str] = []

    state = save_checkpoint(
        tmp_path / "step-1",
        metadata=_metadata(1),
        tensors={"adapter.safetensors": b"payload"},
        save_sidecar=lambda directory: written.append(sorted(p.name for p in directory.iterdir())),
    )

    assert written, "the sidecar must run before the metadata commits the directory"
    assert "adapter.safetensors" in written[0]
    assert "checkpoint.json" not in written[0]
    assert is_resumable(state.directory)


def test_a_directory_with_no_metadata_is_not_a_checkpoint(tmp_path):
    """Debris from an interrupted save, not a state anybody can enumerate."""
    directory = tmp_path / "step-200"
    directory.mkdir()
    (directory / "adapter.safetensors").write_bytes(b"payload")

    assert not is_resumable(directory)
    with pytest.raises(CheckpointError, match=r"no checkpoint\.json"):
        load_checkpoint(directory)


def test_latest_checkpoint_ignores_an_incomplete_directory_and_warns(tmp_path):
    root = tmp_path / "run"
    _save(root / "step-100", 100)
    debris = root / "step-200"
    debris.mkdir(parents=True)
    (debris / "adapter.safetensors").write_bytes(b"payload")

    with pytest.warns(RuntimeWarning, match="ignoring checkpoint directory"):
        found = latest_checkpoint(root)

    assert found is not None
    assert found.metadata.global_step == 100


def test_latest_checkpoint_picks_the_highest_step(tmp_path):
    root = tmp_path / "run"
    for step in (50, 300, 100, 200):
        _save(root / f"step-{step}", step)

    found = latest_checkpoint(root)

    assert found is not None
    assert found.metadata.global_step == 300


def test_latest_checkpoint_breaks_a_step_tie_by_directory_name(tmp_path):
    root = tmp_path / "run"
    _save(root / "aaa", 100)
    _save(root / "zzz", 100)

    found = latest_checkpoint(root)

    assert found is not None
    assert found.directory.name == "zzz"


def test_latest_checkpoint_returns_none_for_an_absent_or_empty_root(tmp_path):
    assert latest_checkpoint(tmp_path / "never-created") is None

    empty = tmp_path / "empty"
    empty.mkdir()
    assert latest_checkpoint(empty) is None


def test_list_checkpoints_is_ordered_by_global_step_and_skips_debris(tmp_path):
    root = tmp_path / "run"
    for step in (300, 50, 200, 100):
        _save(root / f"step-{step}", step)
    debris = root / "step-400"
    debris.mkdir(parents=True)
    (debris / "adapter.safetensors").write_bytes(b"payload")

    found = list_checkpoints(root)

    assert [entry.global_step for entry in found] == [50, 100, 200, 300]


def test_a_directory_declaring_a_missing_payload_is_refused(tmp_path):
    state = _save(tmp_path / "step-1", 1)
    (state.directory / "adapter.safetensors").unlink()

    assert not is_resumable(state.directory)
    with pytest.raises(CheckpointError, match=r"payload .* is missing"):
        load_checkpoint(state.directory)


def test_a_directory_declaring_an_empty_payload_is_refused(tmp_path):
    state = _save(tmp_path / "step-1", 1)
    (state.directory / "adapter.safetensors").write_bytes(b"")

    assert not is_resumable(state.directory)


def test_an_unreadable_metadata_file_is_refused(tmp_path):
    directory = tmp_path / "step-1"
    directory.mkdir()
    (directory / "checkpoint.json").write_text("{not json", encoding="utf-8")

    assert not is_resumable(directory)
    with pytest.raises(CheckpointError, match="unreadable"):
        load_checkpoint(directory)


def test_a_foreign_format_version_is_refused(tmp_path):
    directory = tmp_path / "step-1"
    directory.mkdir()
    (directory / "checkpoint.json").write_text(
        json.dumps({"format_version": "nexo_checkpoint.v2", "files": {}}), encoding="utf-8"
    )

    assert not is_resumable(directory)
    with pytest.raises(CheckpointError, match="is not a resumable checkpoint: format version"):
        load_checkpoint(directory)


def test_a_directory_that_is_not_a_directory_is_refused(tmp_path):
    target = tmp_path / "step-1"
    target.write_text("not a directory", encoding="utf-8")

    assert not is_resumable(target)


def test_an_unusable_tensor_name_cannot_address_anything_else(tmp_path):
    for name in ("../escape", "sub/dir", ".hidden", ""):
        with pytest.raises(CheckpointError, match="unusable tensor name"):
            _save(tmp_path / "step-1", 1, tensors={name: b"payload"})


def test_repeated_saves_of_the_same_run_are_byte_identical(tmp_path):
    """A run id is derived from its inputs; identical inputs must hash identically."""
    first = tmp_path / "a"
    second = tmp_path / "b"
    _save(first, 100)
    _save(second, 100)

    assert (first / "checkpoint.json").read_bytes() == (second / "checkpoint.json").read_bytes()
    assert (first / "adapter.safetensors").read_bytes() == (
        second / "adapter.safetensors"
    ).read_bytes()


def test_saving_over_an_existing_checkpoint_replaces_it(tmp_path):
    directory = tmp_path / "step-100"
    _save(directory, 100)
    _save(directory, 200)

    loaded = load_checkpoint(directory)

    assert loaded.metadata.global_step == 200
    assert [entry.global_step for entry in list_checkpoints(directory.parent)] == [200]


def test_metadata_round_trips_through_its_serialised_form():
    original = _metadata(42)

    restored = CheckpointMetadata.from_dict(json.loads(json.dumps(original.to_dict())))

    assert restored == original
    assert restored.to_dict() == original.to_dict()


def test_metadata_detaches_the_caller_sequences():
    config = {"max_seq_length": 128}
    metadata = _metadata(1, config=config)

    config["max_seq_length"] = 999

    assert metadata.config["max_seq_length"] == 128


def test_metadata_refuses_a_foreign_format_version():
    with pytest.raises(CheckpointError, match="unsupported checkpoint format"):
        CheckpointMetadata.from_dict({**_metadata(1).to_dict(), "format_version": "v9"})


def test_metadata_refuses_a_boolean_step():
    """`True` is an int in Python; resuming at step 1 from a bool is a silent bug."""
    with pytest.raises(CheckpointError, match="global_step"):
        CheckpointMetadata.from_dict(replace(_metadata(1), global_step=True).to_dict())


def test_metadata_refuses_a_missing_field():
    payload = _metadata(1).to_dict()
    del payload["dataset_checksum"]

    with pytest.raises(CheckpointError, match="dataset_checksum"):
        CheckpointMetadata.from_dict(payload)


def test_a_resumed_checkpoint_carries_the_fields_a_resume_needs(tmp_path):
    """A resume that cannot tell which segment died restarts the epoch wrong."""
    state = _save(tmp_path / "step-1", 400, segment_index=2, segments_completed=3)
    loaded = load_checkpoint(state.directory)

    assert loaded.metadata.global_step == 400
    assert loaded.metadata.segment_index == 2
    assert loaded.metadata.segments_completed == 3
    assert loaded.metadata.dataset_version == "routing_dataset.v1"
    assert loaded.metadata.dataset_checksum == "0" * 64
    assert loaded.metadata.seed == SEED
