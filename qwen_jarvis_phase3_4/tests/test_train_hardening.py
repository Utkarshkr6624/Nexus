"""Audit-driven hardening of the training engine: precision choice, manifest handling, loader fallback, non-finite guards,
fresh-run-directory / pruning rules, production config validation, tokenizer fingerprint, throughput logging, adapter-changed check.
Nothing here loads or trains Qwen3-8B; production code paths are exercised only with model loaders monkeypatched to raise."""
import hashlib
import importlib
import json
import sys

import pytest
import torch

from test_train_common import PROD_CFG, SMOKE_CFG, WORKSPACE, C, make_trainer, smoke_cfg, T

K = T.K
D = T.D
FROZEN = WORKSPACE / "dataset" / "v2_final"
OVER = {"data.max_train_examples": 40, "data.max_eval_examples": 10, "train.max_steps": 6, "train.save_every": 2, "train.eval_every": 2}


# ------------------------------------------------------------------------------------------- (1) precision
@pytest.mark.parametrize("cap,expected", [((7, 5), "fp16"), ((8, 0), "bf16"), ((6, 0), "fp16"), ((8, 6), "bf16"), ((9, 0), "bf16")])
def test_auto_precision_uses_compute_capability_not_emulated_bf16(monkeypatch, cap, expected):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "get_device_capability", lambda *a, **k: cap)
    monkeypatch.setattr(torch.cuda, "is_bf16_supported", lambda *a, **k: True)       # what a T4 reports through emulation
    cfg = C.load_config(PROD_CFG)
    assert cfg.train.precision == "auto"
    assert T.resolve_precision(cfg) == expected
    assert T.repro.bf16_native() == (expected == "bf16")


def test_precision_without_cuda_and_explicit_override(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    assert T.resolve_precision(C.load_config(PROD_CFG)) == "fp32"
    assert T.resolve_precision(C.load_config(PROD_CFG, ["train.precision=fp16"])) == "fp16"


# ------------------------------------------------------------------------------------------- (2) manifest
def _mini_dataset(tmp_path, manifest_name="MANIFEST.json", with_manifest=True, n=12):
    d = tmp_path / "ds"
    d.mkdir(parents=True, exist_ok=True)
    version = json.loads((FROZEN / "MANIFEST.json").read_text(encoding="utf-8"))["version"]      # read, never hard-coded
    files = {}
    for name in ("train.jsonl", "validation.jsonl"):
        lines = (FROZEN / name).read_text(encoding="utf-8").splitlines()[:n]
        text = "\n".join(lines) + "\n"
        (d / name).write_bytes(text.encode("utf-8"))      # bytes: write_text would turn LF into CRLF on Windows
        files[name] = {"records": len(lines), "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}
    if with_manifest:
        (d / manifest_name).write_text(json.dumps({"version": version, "files": files, "status": "FROZEN"}), encoding="utf-8")
    return d, version


def _prod_cfg(d, version):
    return C.load_config(PROD_CFG, [f"data.train_path={d / 'train.jsonl'}", f"data.validation_path={d / 'validation.jsonl'}", f"data.dataset_version={version}"])


def _info(d):
    return {"train_sha256": D.file_sha256(d / "train.jsonl"), "validation_sha256": D.file_sha256(d / "validation.jsonl")}


@pytest.mark.parametrize("name", ["MANIFEST.json", "manifest.json", "Manifest.JSON"])
def test_manifest_found_case_insensitively(tmp_path, name):
    d, version = _mini_dataset(tmp_path, manifest_name=name)
    cfg = _prod_cfg(d, version)
    out = T.check_manifest(cfg, d / "train.jsonl", d / "validation.jsonl", _info(d))
    assert out["manifest"]["version"] == version and len(out["manifest_sha256"]) == 64
    assert T.find_manifest(d).name == name


def test_missing_manifest_is_an_error_in_production_only(tmp_path):
    d, version = _mini_dataset(tmp_path, with_manifest=False)
    with pytest.raises(ValueError, match="no dataset manifest"):
        T.check_manifest(_prod_cfg(d, version), d / "train.jsonl", d / "validation.jsonl", _info(d))
    smoke = smoke_cfg(**{"data.train_path": str(d / "train.jsonl"), "data.validation_path": str(d / "validation.jsonl")})
    assert T.check_manifest(smoke, d / "train.jsonl", d / "validation.jsonl", _info(d)) == {"manifest": None, "manifest_sha256": None}


def test_dry_run_on_production_config_fails_without_manifest(tmp_path, capsys):
    d, version = _mini_dataset(tmp_path, with_manifest=False)
    rc = T.main(["--config", str(PROD_CFG), "--dry-run", "--set", f"data.train_path={d / 'train.jsonl'}",
                 "--set", f"data.validation_path={d / 'validation.jsonl'}", "--set", f"data.dataset_version={version}"])
    assert rc == T.EXIT["error"] and "no dataset manifest" in capsys.readouterr().err


def test_hash_mismatch_and_version_mismatch(tmp_path):
    d, version = _mini_dataset(tmp_path, manifest_name="manifest.json")
    (d / "train.jsonl").write_text((d / "train.jsonl").read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="does not match"):
        T.check_manifest(_prod_cfg(d, version), d / "train.jsonl", d / "validation.jsonl", _info(d))
    d2, version = _mini_dataset(tmp_path / "b", manifest_name="MANIFEST.json")
    with pytest.raises(ValueError, match="version mismatch"):
        T.check_manifest(_prod_cfg(d2, "another_version"), d2 / "train.jsonl", d2 / "validation.jsonl", _info(d2))


def test_dry_run_numbers_come_from_the_manifest_not_constants(capsys):
    man = json.loads((FROZEN / "MANIFEST.json").read_text(encoding="utf-8"))
    out = T.cmd_dry_run(C.load_config(PROD_CFG), sample=24)
    ds = out["dataset"]
    assert ds["train_records_in_file"] == man["files"]["train.jsonl"]["records"]
    assert ds["validation_records_in_file"] == man["files"]["validation.jsonl"]["records"]
    assert ds["train_sha256"] == man["files"]["train.jsonl"]["sha256"] and ds["manifest"]["version"] == man["version"]
    assert out["tokenizer_check"]["match"] is True and out["model_loaded"] is False


# ------------------------------------------------------------------------------------------- (3) loader fallback
@pytest.fixture
def broken_loader(monkeypatch):
    monkeypatch.setitem(sys.modules, "pipeline.data.dataset", None)          # makes `import pipeline.data.dataset` raise ImportError
    importlib.reload(D)
    yield
    monkeypatch.undo()
    importlib.reload(D)


def test_import_failure_is_recorded_and_production_refuses(broken_loader, capsys):
    assert D.SOURCE == "fallback" and D.IMPORT_ERROR
    with pytest.raises(RuntimeError, match="verified data loader"):
        T.require_primary_loader(C.load_config(PROD_CFG))
    with pytest.raises(RuntimeError, match="fallback renderer"):
        T.load_data(C.load_config(PROD_CFG), tok=None)
    rc = T.main(["--config", str(PROD_CFG), "--dry-run"])
    assert rc == T.EXIT["error"] and "pipeline.data" in capsys.readouterr().err
    T.require_primary_loader(smoke_cfg())                                     # smoke mode may still use it


def test_loader_is_restored_after_the_simulation():
    assert D.SOURCE == "pipeline.data" and D.IMPORT_ERROR is None


# ------------------------------------------------------------------------------------------- (5) non-finite guards
class FakeScaler:
    """Stands in for torch.amp.GradScaler (CUDA only) so the skip logic runs on CPU."""

    def scale(self, loss):
        return loss

    def unscale_(self, opt):
        pass

    def step(self, opt):
        opt.step()

    def update(self):
        pass

    def state_dict(self):
        return {}

    def load_state_dict(self, d):
        pass


def test_nan_loss_without_scaler_aborts_before_backward(tmp_path, monkeypatch):
    tr = make_trainer(smoke_cfg(**OVER), tmp_path / "r")
    monkeypatch.setattr(T, "masked_lm_loss", lambda m, b, i: torch.tensor(float("nan"), requires_grad=True))
    with pytest.raises(T.NonFiniteError, match="non-finite loss.*no GradScaler"):
        tr.fit()
    assert tr.step == 0 and tr.lora_b_norm() == 0.0                            # adapter untouched


def test_nan_gradient_without_scaler_aborts(tmp_path, monkeypatch):
    tr = make_trainer(smoke_cfg(**OVER), tmp_path / "r")
    monkeypatch.setattr(torch.nn.utils, "clip_grad_norm_", lambda *a, **k: torch.tensor(float("nan")))
    with pytest.raises(T.NonFiniteError, match="non-finite gradient"):
        tr.fit()


def test_consecutive_skipped_steps_abort(tmp_path, monkeypatch):
    tr = make_trainer(smoke_cfg(**OVER, **{"train.max_consecutive_skipped_steps": 3}), tmp_path / "r")
    tr.scaler = FakeScaler()
    monkeypatch.setattr(torch.nn.utils, "clip_grad_norm_", lambda *a, **k: torch.tensor(float("inf")))
    with pytest.raises(T.NonFiniteError, match="3 consecutive optimizer steps"):
        tr.fit()
    assert tr.skipped_steps == 3 and tr.step == 2                              # third skip raises before the step counter moves


def test_nan_loss_with_scaler_counts_as_skipped_and_aborts(tmp_path, monkeypatch):
    tr = make_trainer(smoke_cfg(**OVER, **{"train.max_consecutive_skipped_steps": 2}), tmp_path / "r")
    tr.scaler = FakeScaler()
    monkeypatch.setattr(T, "masked_lm_loss", lambda m, b, i: torch.tensor(float("nan"), requires_grad=True))
    with pytest.raises(T.NonFiniteError, match="2 consecutive.*loss"):
        tr.fit()


def test_isolated_skips_are_tolerated_and_recorded(tmp_path, monkeypatch):
    real = torch.nn.utils.clip_grad_norm_
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        return torch.tensor(float("nan")) if calls["n"] in (1, 3) else real(*a, **k)

    cfg = smoke_cfg(**OVER, **{"train.max_consecutive_skipped_steps": 3})
    tr = make_trainer(cfg, tmp_path / "r")
    tr.scaler = FakeScaler()
    monkeypatch.setattr(torch.nn.utils, "clip_grad_norm_", flaky)
    res = tr.fit()
    assert res["skipped_steps"] == 2 and res["step"] == 6 and res["adapter_updated"] and res["lora_B_norm"] > 0
    meta = json.loads((tmp_path / "r" / "checkpoint-6" / "checkpoint_meta.json").read_text())
    assert meta["metrics"]["skipped_steps"] == 2 and meta["metrics"]["lora_B_norm"] > 0
    log = [json.loads(l) for l in (tmp_path / "r" / "metrics.jsonl").read_text().splitlines()]
    assert sum(1 for r in log if r.get("event") == "step_skipped") == 2
    # the skip counters survive a resume
    tr2 = make_trainer(cfg, tmp_path / "r")
    tr2.resume(tmp_path / "r" / "checkpoint-6")
    assert tr2.skipped_steps == 2 and tr2.consecutive_skipped == 0


def test_adapter_unchanged_after_training_is_not_success(tmp_path, monkeypatch):
    tr = make_trainer(smoke_cfg(**OVER), tmp_path / "r")
    monkeypatch.setattr(tr.opt, "step", lambda *a, **k: None)                  # optimizer silently does nothing
    with pytest.raises(T.NonFiniteError, match="did not change"):
        tr.fit()


def test_config_validates_skip_limit():
    assert any("max_consecutive_skipped_steps" in e for e in C.validate_config(C.load_config(SMOKE_CFG, ["train.max_consecutive_skipped_steps=0"])))
    assert C.load_config(PROD_CFG).train.max_consecutive_skipped_steps == 20


# ------------------------------------------------------------------------------------------- (6) fresh run dir + pruning
def test_fresh_run_refused_when_checkpoints_exist(tmp_path):
    K.ensure_fresh_run_dir(tmp_path / "nothing_here")
    run = tmp_path / "run"
    (run / "checkpoint-1000").mkdir(parents=True)
    with pytest.raises(K.CheckpointError, match="already contains 1 checkpoint"):
        K.ensure_fresh_run_dir(run)


def test_production_start_without_resume_refuses_nonempty_run_dir(tmp_path, monkeypatch, capsys):
    run = tmp_path / "runs_root" / "qwen3_8b_jarvis_qlora_v1"
    (run / "checkpoint-1000").mkdir(parents=True)

    def never(*a, **k):
        raise AssertionError("must not get this far")

    monkeypatch.setattr(T, "load_tokenizer", never)                              # safety net: even a bug here cannot start a model load
    monkeypatch.setattr(T, "load_production_model", never)
    monkeypatch.setenv(T.UNLOCK_ENV, T.UNLOCK_VALUE)
    args = ["--config", str(PROD_CFG), "--confirm-production", "--set", "production_locked=false", "--set", f"output.output_dir={tmp_path / 'runs_root'}"]
    assert T.main(args) == T.EXIT["checkpoint"] and "already contains" in capsys.readouterr().err
    with pytest.raises(AssertionError, match="must not get this far"):          # with --resume the guard passes and the next step is reached
        T.main(args + ["--resume"])


def test_pruning_never_drops_current_session_for_older_higher_steps(tmp_path):
    cfg = smoke_cfg(**{**OVER, "train.keep_last": 2})
    run = tmp_path / "run"
    old = make_trainer(cfg, run)
    old.step = 1000                                                              # a checkpoint restored from a previous session
    old.save(0, {"val_loss": 9.0})
    new = make_trainer(cfg, run)
    new.fit()                                                                    # saves 2, 4, 6 in this session
    names = {p.name for _, p in K.list_checkpoints(run)}
    assert {"checkpoint-4", "checkpoint-6"} <= names, names
    assert "checkpoint-1000" not in names
    assert K.resolve_resume(run, "latest").name == "checkpoint-6"
    assert json.loads((run / "latest.json").read_text())["step"] == 6


# ------------------------------------------------------------------------------------------- (12) config + tokenizer + throughput
def test_production_rejects_unsafe_settings():
    errs = lambda *o: C.validate_config(C.load_config(PROD_CFG, list(o)), check_paths=False)
    assert any("quant.enabled" in e for e in errs("quant.enabled=false"))
    assert any("flash_attention_2" in e for e in errs("model.attn_implementation=flash_attention_2"))
    assert any("tokenizer" in e for e in errs("model.tokenizer_path=someone/else-tokenizer"))
    assert errs("model.attn_implementation=eager") == [] and errs("model.attn_implementation=sdpa") == []
    assert errs(f"model.tokenizer_path={WORKSPACE / '_hf_meta'}") == []
    assert errs() == []


def test_tokenizer_fingerprint_matches_reference_and_detects_change(monkeypatch):
    cfg = C.load_config(PROD_CFG)
    tok = T.load_tokenizer(C.load_config(SMOKE_CFG))                             # _hf_meta tokenizer
    out = T.check_tokenizer(cfg, tok)
    assert out["match"] is True and out["reference_source"] == "_hf_meta"
    ref_file = json.loads(T.FINGERPRINT_FILE.read_text(encoding="utf-8"))
    for k in ("chat_template_sha256", "vocab_sha256", "special_tokens_sha256", "vocab_size"):
        assert ref_file[k] == out["fingerprint"][k]                               # the shipped reference equals the _hf_meta files
    tok2 = T.load_tokenizer(C.load_config(SMOKE_CFG))
    tok2.chat_template = tok2.chat_template.replace("<|im_start|>", "<|im_begin|>")
    with pytest.raises(RuntimeError, match="chat_template_sha256"):
        T.check_tokenizer(cfg, tok2)
    assert T.check_tokenizer(C.load_config(SMOKE_CFG), tok2)["match"] is False   # smoke records the difference without failing


def test_reproducibility_snapshot_records_tokenizer_and_precision(tmp_path):
    cfg = C.load_config(SMOKE_CFG)
    tok = T.load_tokenizer(cfg)
    chk = T.check_tokenizer(cfg, tok)
    snap = T.repro.snapshot(cfg, {"version": "x"}, "fp16", extra={"tokenizer_check": chk, "data_loader": D.SOURCE})
    assert snap["precision"] == "fp16" and snap["tokenizer_check"]["match"] is True and snap["data_loader"] == "pipeline.data"
    assert "bf16_native" in json.dumps(snap["hardware"]) or snap["hardware"]["cuda"] is False


def test_throughput_is_logged_and_returned(tmp_path):
    tr = make_trainer(smoke_cfg(**OVER, **{"train.log_every": 2}), tmp_path / "r")
    res = tr.fit()
    assert res["first_steps_tokens_per_sec"] > 0 and res["tokens_per_sec_session"] > 0
    log = [json.loads(l) for l in (tmp_path / "r" / "metrics.jsonl").read_text().splitlines()]
    assert any(r.get("event") == "first_steps_throughput" and r["tokens_per_sec"] > 0 for r in log)
    assert any("tok_per_s" in r and r["tok_per_s"] > 0 for r in log)
    assert res["lora_B_norm"] > 0 and res["adapter_updated"] is True
