"""Phase 5 pre-flight guards: exact dry-run sample counts, and production refusal of data limits (no model, no training)."""
import json

import pytest

from test_train_common import PROD_CFG, SMOKE_CFG, WORKSPACE, C, T
from pipeline.train import data_adapter as D

TRAIN = WORKSPACE / "dataset" / "v2_final" / "train.jsonl"
VAL = WORKSPACE / "dataset" / "v2_final" / "validation.jsonl"


def _assistant_messages(path):
    return sum(1 for line in open(path, encoding="utf-8") if line.strip() for m in json.loads(line)["messages"] if m["role"] == "assistant")


def test_count_samples_is_exact_for_the_frozen_files():
    assert D.count_samples(TRAIN, "per_turn") == _assistant_messages(TRAIN) == 12777      # data_stats_train.json
    assert D.count_samples(VAL, "per_turn") == _assistant_messages(VAL)
    assert D.count_samples(TRAIN, "last_assistant") == D.count_lines(TRAIN) == 10298


def test_count_samples_small_file(tmp_path):
    p = tmp_path / "x.jsonl"
    recs = [{"messages": [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}, {"role": "user", "content": "c"}, {"role": "assistant", "content": "d"}]},
            {"messages": [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]}]
    p.write_text("\n".join(json.dumps(r) for r in recs) + "\n\n", encoding="utf-8")
    assert D.count_samples(p, "per_turn") == 3 and D.count_samples(p, "all_assistant") == 2
    p.write_text("{bad", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid JSON"):
        D.count_samples(p)


def test_dry_run_reports_exact_whole_file_counts():
    cfg = C.load_config(PROD_CFG)
    out = T.cmd_dry_run(cfg, sample=24)
    assert out["EXACT_train_samples"] == 12777 and out["EXACT_train_records"] == 10298
    assert out["EXACT_validation_samples"] == D.count_samples(VAL, "per_turn")
    assert out["EXACT_steps_per_epoch"] == T.steps_per_epoch(12777, 4, 4) == 798
    assert out["EXACT_total_optimizer_steps"] == 1596 and out["effective_batch"] == 16
    assert "estimated_train_samples" not in out and out["limits_active"] == {"train": None, "validation": None}
    assert out["dataset"]["train_samples_used"] != out["EXACT_train_samples"]            # the SAMPLE is small; the counts are not extrapolated
    assert any(k.startswith("mean_tokens_per_sample_ESTIMATED") for k in out)


@pytest.mark.parametrize("key", ["data.max_train_examples", "data.max_eval_examples"])
def test_production_rejects_example_limits(key, monkeypatch):
    monkeypatch.delenv(C.TEST_LIMITS_ENV, raising=False)
    cfg = C.load_config(PROD_CFG, [f"{key}=256"])
    errs = C.validate_config(cfg)
    assert any(key.split(".")[1] in e and "production requires null" in e for e in errs), errs
    with pytest.raises(C.ConfigError, match="production requires null"):
        C.require_valid(cfg)
    assert T.main(["--config", str(PROD_CFG), "--validate-config", "--set", f"{key}=256"]) == T.EXIT["config"]
    assert T.main(["--config", str(PROD_CFG), "--dry-run", "--set", f"{key}=256"]) == T.EXIT["config"]


def test_production_limits_wrong_override_value_still_rejected(monkeypatch):
    monkeypatch.setenv(C.TEST_LIMITS_ENV, "1")
    assert C.validate_config(C.load_config(PROD_CFG, ["data.max_train_examples=256"]))


def test_test_only_override_is_explicit_and_does_not_unlock_production(monkeypatch):
    monkeypatch.setenv(C.TEST_LIMITS_ENV, C.TEST_LIMITS_VALUE)
    cfg = C.load_config(PROD_CFG, ["data.max_train_examples=256"])
    assert C.validate_config(cfg) == []                                                    # limits allowed for tests only...
    monkeypatch.delenv(T.UNLOCK_ENV, raising=False)
    assert T.main(["--config", str(PROD_CFG), "--set", "data.max_train_examples=256"]) == T.EXIT["locked"]   # ...and training is still locked


def test_production_default_config_uses_full_dataset(monkeypatch):
    monkeypatch.delenv(C.TEST_LIMITS_ENV, raising=False)
    cfg = C.load_config(PROD_CFG)
    assert cfg.data.max_train_examples is None and cfg.data.max_eval_examples is None
    assert C.validate_config(cfg) == []
    assert (cfg.data.dataset_version, cfg.model.model_id, cfg.train.epochs, cfg.quant.quant_type, cfg.quant.enabled, cfg.train.per_device_batch,
            cfg.train.grad_accum) == ("qwen_jarvis_dataset_v2_final", "Qwen/Qwen3-8B", 2, "nf4", True, 4, 4)


def test_smoke_config_limits_still_allowed(monkeypatch):
    monkeypatch.delenv(C.TEST_LIMITS_ENV, raising=False)
    assert C.validate_config(C.load_config(SMOKE_CFG)) == []
