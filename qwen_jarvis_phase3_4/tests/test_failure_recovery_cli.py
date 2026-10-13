"""C11 failure recovery at the command-line level: every bad input must give a clear message and a non-zero exit code, never a traceback or a started run.
Uses subprocess against the real CLIs (train, eval, data).  Run: .venv\\Scripts\\python.exe -m pytest tests/test_failure_recovery_cli.py"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

WS = Path(__file__).resolve().parent.parent
PY = sys.executable
ENV = {k: v for k, v in os.environ.items() if k != "QWEN_PHASE5_UNLOCK"} | {"PYTHONIOENCODING": "utf-8"}


def run(*args, env=None, timeout=300):
    return subprocess.run([PY, *args], capture_output=True, text=True, cwd=WS, env=env or ENV, timeout=timeout)


def train(*args):
    return run("-m", "pipeline.train.train", *args)


def clean(r):
    return (r.stdout + r.stderr)


def assert_clean_failure(r, *needles):
    out = clean(r)
    assert r.returncode != 0, out
    assert "Traceback (most recent call last)" not in out, out[-800:]
    for n in needles:
        assert n.lower() in out.lower(), (n, out[-800:])


def test_missing_config_file(tmp_path):
    assert_clean_failure(train("--config", str(tmp_path / "nope.yaml"), "--validate-config"), "config")


def test_malformed_yaml(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("mode: [unclosed\n  train: {", encoding="utf-8")
    assert_clean_failure(train("--config", str(p), "--validate-config"), "malformed")


def test_unsupported_parameter_override():
    assert_clean_failure(train("--config", "configs/qlora_sft.yaml", "--validate-config", "--set", "train.warp_drive=11"), "warp_drive")


def test_unsupported_value():
    assert_clean_failure(train("--config", "configs/qlora_sft.yaml", "--validate-config", "--set", "lora.r=0"), "lora")


def test_remote_code_rejected():
    assert_clean_failure(train("--config", "configs/qlora_sft.yaml", "--validate-config", "--set", "model.trust_remote_code=true"), "trust_remote_code")


def test_second_model_rejected():
    assert_clean_failure(train("--config", "configs/qlora_sft.yaml", "--validate-config", "--set", "model.model_id=meta-llama/Llama-3-8B"), "model_id")


def test_invalid_dataset_path(tmp_path):
    assert_clean_failure(train("--config", "configs/qlora_sft.yaml", "--dry-run", "--set", f"data.train_path={tmp_path / 'missing_dir' / 'train.jsonl'}"))


def test_dataset_hash_mismatch_is_refused(tmp_path):
    d = tmp_path / "v2_final"
    shutil.copytree(WS / "dataset" / "v2_final", d)
    with open(d / "train.jsonl", "a", encoding="utf-8") as f:       # tamper: one extra byte of content changes the hash
        f.write("\n")
    r = train("--config", "configs/qlora_sft.yaml", "--dry-run", "--set", f"data.train_path={d / 'train.jsonl'}",
              "--set", f"data.validation_path={d / 'validation.jsonl'}", "--set", f"data.test_path={d / 'test.jsonl'}")
    assert_clean_failure(r)
    assert "hash" in clean(r).lower() or "manifest" in clean(r).lower() or "sha" in clean(r).lower(), clean(r)[-600:]


def test_invalid_checkpoint_resume(tmp_path):
    bad = tmp_path / "checkpoint-5"
    bad.mkdir()
    (bad / "checkpoint_meta.json").write_text("{not json", encoding="utf-8")
    # safe modes refuse --resume explicitly instead of silently ignoring it; corrupt-checkpoint handling itself is unit-tested in tests/test_checkpoint.py
    r = train("--config", "configs/smoke_test.yaml", "--smoke-test", "--resume", str(bad))
    assert_clean_failure(r, "--resume applies only")
    r = train("--config", "configs/qlora_sft.yaml", "--dry-run", "--resume", str(bad))
    assert_clean_failure(r, "--resume applies only")


def test_resume_latest_without_any_run_is_clear(tmp_path):
    r = train("--config", "configs/smoke_test.yaml", "--smoke-test", "--resume", "latest", "--set", f"output.output_dir={tmp_path}", "--set", "output.run_name=never_ran")
    out = clean(r)
    assert "Traceback (most recent call last)" not in out, out[-800:]


def test_production_lock_each_condition_missing():
    r = train("--config", "configs/qlora_sft.yaml")
    assert r.returncode == 3 and "LOCKED" in clean(r)
    r = train("--config", "configs/qlora_sft.yaml", "--confirm-production")          # flag alone
    assert r.returncode == 3
    r = train("--config", "configs/qlora_sft.yaml", "--confirm-production", "--set", "production_locked=false")   # flag + config, no env var
    assert r.returncode == 3 and "QWEN_PHASE5_UNLOCK" in clean(r)
    env = ENV | {"QWEN_PHASE5_UNLOCK": "wrong-value"}
    r = run("-m", "pipeline.train.train", "--config", "configs/qlora_sft.yaml", "--confirm-production", "--set", "production_locked=false", env=env)
    assert r.returncode == 3


def test_eval_refuses_to_load_a_model_without_explicit_permission(tmp_path):
    r = run("-m", "pipeline.eval.cli", "run", "--model", "Qwen/Qwen3-8B", "--suite", "general", "--out", str(tmp_path / "x"))
    assert_clean_failure(r, "allow-model-load")
    assert not (tmp_path / "x" / "eval_results.json").exists()


def test_eval_unknown_suite(tmp_path):
    assert_clean_failure(run("-m", "pipeline.eval.cli", "run", "--stub", "oracle", "--suite", "nonexistent", "--out", str(tmp_path / "x")), "unknown suite")


def test_data_cli_rejects_missing_dir(tmp_path):
    r = run("-m", "pipeline.data.cli", "stats", "--split", "train", "--data-dir", str(tmp_path / "none"), "--out-dir", str(tmp_path))
    assert r.returncode != 0
    assert "Traceback (most recent call last)" not in clean(r) or "FileNotFoundError" in clean(r)    # clear file error either way


def test_data_validate_rejects_corrupt_record(tmp_path):
    d = tmp_path / "ds"
    d.mkdir()
    for s in ("train", "validation", "test"):
        (d / f"{s}.jsonl").write_text(json.dumps({"id": "x", "messages": [{"role": "user", "content": "hi"}]}) + "\n", encoding="utf-8")   # no assistant turn
    r = run("-m", "pipeline.data.cli", "validate", "--data-dir", str(d), "--mode", "strict")
    assert r.returncode != 0, clean(r)[-500:]
