"""Checkpoint system (C6): creation, metadata, pointers, retention, atomicity, reload, integrity and compatibility failures."""
import json
import shutil

import pytest
import torch

from test_train_common import make_trainer, smoke_cfg, T

K = T.K
OVER = {"data.max_train_examples": 40, "data.max_eval_examples": 10, "train.max_steps": 6, "train.save_every": 2, "train.eval_every": 2, "train.keep_last": 2}


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    """A finished 6-step smoke run with checkpoints at 2/4/6."""
    d = tmp_path_factory.mktemp("ckrun") / "run"
    cfg = smoke_cfg(**OVER)
    tr = make_trainer(cfg, d)
    tr.fit()
    return d, tr, cfg


def test_checkpoint_layout_and_metadata(run):
    d, tr, cfg = run
    ck = d / "checkpoint-6"
    assert (ck / "adapter" / "adapter_config.json").exists() and (ck / "adapter" / "adapter_model.safetensors").exists()
    assert (ck / "trainer_state.pt").exists()
    meta = json.loads((ck / "checkpoint_meta.json").read_text())
    assert meta["step"] == 6 and meta["schema"] == K.SCHEMA
    ident = meta["identity"]
    assert ident["model_id"] == cfg.model.model_id and ident["dataset_version"] == "qwen_jarvis_dataset_v2_final" and len(ident["dataset_manifest_sha256"]) == 64 and ident["config_hash"] == cfg.snapshot_hash()
    assert ":" in ident["dataset_hash"]
    assert "torch" in meta["libraries"] and "transformers" in meta["libraries"] and "peft" in meta["libraries"]
    assert "val_loss" in meta["metrics"] and meta["saved_utc"].endswith("Z")
    assert "adapter/adapter_model.safetensors" in meta["files"] and all(len(v["sha256"]) == 64 for v in meta["files"].values())


def test_latest_and_best_pointers(run):
    d, tr, _ = run
    assert json.loads((d / "latest.json").read_text())["step"] == 6
    best = json.loads((d / "best.json").read_text())
    assert best["step"] == tr.stopper.best_step and (d / best["checkpoint"]).is_dir()
    assert K.resolve_resume(d, "latest").name == "checkpoint-6"
    assert K.resolve_resume(d, "best").name == best["checkpoint"]


def test_retention_keeps_last_k_and_best(run):
    d, tr, _ = run
    names = {p.name for _, p in K.list_checkpoints(d)}
    best = json.loads((d / "best.json").read_text())["checkpoint"]
    assert best in names and "checkpoint-6" in names
    assert len(names) <= 3                                                   # keep_last=2 plus possibly the (older) best
    assert not any(p.name.startswith(".tmp") for p in d.iterdir())           # no leftovers


def test_reload_restores_adapter_and_loop_state(run, tmp_path):
    d, tr, cfg = run
    other = make_trainer(cfg, tmp_path / "other")
    for p in other.trainable:
        torch.nn.init.normal_(p, std=0.5)                                    # scramble so a successful load is observable
    meta = other.resume(d / "checkpoint-6")
    assert meta["step"] == 6 and other.step == 6 and other.tokens_seen == tr.tokens_seen
    for (n1, p1), (n2, p2) in zip(tr.model.named_parameters(), other.model.named_parameters()):
        if p1.requires_grad:
            assert torch.equal(p1, p2), n1


def test_missing_meta_is_reported(run, tmp_path):
    d, _, _ = run
    bad = tmp_path / "checkpoint-9"
    shutil.copytree(d / "checkpoint-6", bad)
    (bad / "checkpoint_meta.json").unlink()
    with pytest.raises(K.CheckpointError, match="not a complete checkpoint"):
        K.verify_checkpoint(bad)


def test_corrupt_meta_json(run, tmp_path):
    d, _, _ = run
    bad = tmp_path / "checkpoint-9"
    shutil.copytree(d / "checkpoint-6", bad)
    (bad / "checkpoint_meta.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(K.CheckpointError, match="corrupt JSON"):
        K.verify_checkpoint(bad)


def test_missing_adapter_file(run, tmp_path):
    d, _, _ = run
    bad = tmp_path / "checkpoint-9"
    shutil.copytree(d / "checkpoint-6", bad)
    (bad / "adapter" / "adapter_model.safetensors").unlink()
    with pytest.raises(K.CheckpointError, match="missing file"):
        K.verify_checkpoint(bad)


def test_truncated_or_modified_file(run, tmp_path):
    d, _, _ = run
    bad = tmp_path / "checkpoint-9"
    shutil.copytree(d / "checkpoint-6", bad)
    f = bad / "trainer_state.pt"
    f.write_bytes(f.read_bytes()[:-50])
    with pytest.raises(K.CheckpointError, match="does not match its recorded hash"):
        K.verify_checkpoint(bad)


def test_required_meta_fields_and_schema(run, tmp_path):
    d, _, _ = run
    bad = tmp_path / "checkpoint-9"
    shutil.copytree(d / "checkpoint-6", bad)
    meta = json.loads((bad / "checkpoint_meta.json").read_text())
    meta2 = dict(meta)
    meta2.pop("identity")
    (bad / "checkpoint_meta.json").write_text(json.dumps(meta2))
    with pytest.raises(K.CheckpointError, match="missing required field"):
        K.verify_checkpoint(bad)
    meta["schema"] = 999
    (bad / "checkpoint_meta.json").write_text(json.dumps(meta))
    with pytest.raises(K.CheckpointError, match="unsupported checkpoint schema"):
        K.verify_checkpoint(bad)


@pytest.mark.parametrize("field,value", [("model_id", "Qwen/Qwen3-14B"), ("dataset_version", "other_dataset"), ("config_hash", "deadbeef"),
                                         ("dataset_hash", "x:y")])
def test_incompatible_identity_refused_unless_forced(run, tmp_path, field, value):
    d, tr, cfg = run
    other = make_trainer(cfg, tmp_path / "x")
    other.identity = dict(other.identity, **{field: value})
    with pytest.raises(K.CheckpointError, match=field):
        other.resume(d / "checkpoint-6")
    meta = other.resume(d / "checkpoint-6", force=True)                      # --force-resume overrides, with a warning
    assert meta["step"] == 6


def test_changing_training_hyperparameter_invalidates_resume(run, tmp_path):
    d, _, _ = run
    other_cfg = smoke_cfg(**{**OVER, "train.learning_rate": 0.001})
    other = make_trainer(other_cfg, tmp_path / "y")
    with pytest.raises(K.CheckpointError, match="config_hash"):
        other.resume(d / "checkpoint-6")


def test_changing_save_interval_does_not_invalidate_resume(run, tmp_path):
    d, _, _ = run
    other = make_trainer(smoke_cfg(**{**OVER, "train.save_every": 5, "train.log_every": 3}), tmp_path / "z")
    assert other.resume(d / "checkpoint-6")["step"] == 6


def test_resolve_resume_errors(tmp_path):
    with pytest.raises(K.CheckpointError, match="nothing to resume"):
        K.resolve_resume(tmp_path, "latest")
    with pytest.raises(K.CheckpointError, match="nothing to resume"):
        K.resolve_resume(tmp_path, "best")
    with pytest.raises(K.CheckpointError, match="does not exist"):
        K.resolve_resume(tmp_path, str(tmp_path / "nope"))


def test_latest_falls_back_when_pointer_is_corrupt(run):
    d, _, _ = run
    ptr = d / "latest.json"
    saved = ptr.read_text()
    try:
        ptr.write_text("garbage")
        assert K.resolve_resume(d, "latest").name == "checkpoint-6"
    finally:
        ptr.write_text(saved)


def test_save_is_atomic_when_writing_fails(run, tmp_path, monkeypatch):
    d, tr, cfg = run
    work = make_trainer(cfg, tmp_path / "atomic")
    work.step = 2
    work.save(0, {"val_loss": 1.0})
    before = (tmp_path / "atomic" / "latest.json").read_text()
    orig = work.model.save_pretrained

    def fail(*a, **k):
        orig(*a, **k)
        raise OSError("disk full (simulated)")

    monkeypatch.setattr(work.model, "save_pretrained", fail)
    work.step = 4
    with pytest.raises(OSError, match="disk full"):
        work.save(0, {"val_loss": 0.9})
    runp = tmp_path / "atomic"
    assert not (runp / "checkpoint-4").exists() and not any(p.name.startswith(".tmp") for p in runp.iterdir())
    assert (runp / "latest.json").read_text() == before                      # pointer still names the last good checkpoint
    K.verify_checkpoint(runp / "checkpoint-2")


def test_trainer_state_is_loaded_without_pickle_execution(run):
    d, _, _ = run
    # weights_only=True: the loader refuses arbitrary pickled objects
    import pickle
    import os
    import tempfile

    class Evil:
        def __reduce__(self):
            return (os.system, ("echo pwned",))

    f = tempfile.mktemp(suffix=".pt")
    with open(f, "wb") as fh:
        pickle.dump({"x": Evil()}, fh)
    with pytest.raises(Exception):
        torch.load(f, weights_only=True)
    os.unlink(f)
    ck = K.load_checkpoint(d / "checkpoint-6")
    assert set(ck["state"]) >= {"optimizer", "scheduler", "rng", "loop"}


def test_cli_exit_code_for_checkpoint_errors(tmp_path):
    from pipeline.train import checkpoint as ck_mod

    assert T.EXIT["checkpoint"] == 4
    with pytest.raises(ck_mod.CheckpointError):
        K.resolve_resume(tmp_path, "latest")
