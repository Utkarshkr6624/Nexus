"""Config loading / validation / overrides (C5, C11). No model is loaded."""
import pytest

from test_train_common import PROD_CFG, SMOKE_CFG, C


def _load(path, sets=()):
    return C.load_config(path, list(sets))


def test_good_configs_validate():
    assert C.validate_config(_load(SMOKE_CFG)) == []
    # production data files appear when the dataset is frozen; the structure itself must always be valid
    assert C.validate_config(_load(PROD_CFG), check_paths=False) == []


def test_production_config_is_locked_and_official_model():
    cfg = _load(PROD_CFG)
    assert cfg.production_locked is True and cfg.mode == "production"
    assert cfg.model.model_id == "Qwen/Qwen3-8B" and cfg.quant.enabled and cfg.quant.quant_type == "nf4" and cfg.quant.double_quant
    assert set(cfg.lora.target_modules) == {"q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"}
    assert cfg.train.per_device_batch * cfg.train.grad_accum == 16
    assert cfg.data.packing is False


def test_malformed_yaml(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("mode: [unclosed\n  train: {", encoding="utf-8")
    with pytest.raises(C.ConfigError, match="malformed YAML"):
        _load(p)


def test_not_a_mapping(tmp_path):
    p = tmp_path / "list.yaml"
    p.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(C.ConfigError, match="mapping"):
        _load(p)


def test_missing_config_file(tmp_path):
    with pytest.raises(C.ConfigError, match="not found"):
        _load(tmp_path / "nope.yaml")


def test_unsupported_key_rejected(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(SMOKE_CFG.read_text(encoding="utf-8").replace("train:\n", "train:\n  flash_magic: true\n", 1), encoding="utf-8")
    with pytest.raises(C.ConfigError, match="unsupported config key.*flash_magic"):
        _load(p)


def test_unsupported_override_key():
    with pytest.raises(C.ConfigError, match="unsupported --set key"):
        _load(SMOKE_CFG, ["train.does_not_exist=1"])
    with pytest.raises(C.ConfigError, match="unsupported --set key"):
        _load(SMOKE_CFG, ["nosection.x=1"])
    with pytest.raises(C.ConfigError, match="expected key=value"):
        _load(SMOKE_CFG, ["train.seed"])


def test_overrides_are_typed():
    cfg = _load(SMOKE_CFG, ["train.learning_rate=2e-4", "train.seed=99", "lora.target_modules=[q_proj,v_proj]", "data.max_train_examples=null"])
    assert cfg.train.learning_rate == pytest.approx(2e-4) and cfg.train.seed == 99
    assert cfg.lora.target_modules == ["q_proj", "v_proj"] and cfg.data.max_train_examples is None


@pytest.mark.parametrize("override,needle", [
    ("train.learning_rate=-1", "learning_rate"),
    ("train.learning_rate=5", "learning_rate"),
    ("train.per_device_batch=0", "per_device_batch"),
    ("train.grad_accum=1.5", "grad_accum"),
    ("train.precision=int4", "precision"),
    ("train.lr_scheduler=magic", "lr_scheduler"),
    ("train.optimizer=sgd", "optimizer"),
    ("train.max_steps=0", "max_steps"),
    ("train.seed=abc", "seed"),
    ("lora.r=0", "lora.r"),
    ("lora.dropout=2", "dropout"),
    ("lora.target_modules=[]", "target_modules"),
    ("data.packing=true", "packing"),
    ("data.max_seq_len=8", "max_seq_len"),
    ("data.train_on=everything", "train_on"),
    ("data.validate=maybe", "validate"),
    ("data.dataset_version=''", "dataset_version"),
    ("output.run_name=a/b", "run_name"),
    ("mode=turbo", "mode"),
])
def test_invalid_values_rejected(override, needle):
    cfg = _load(SMOKE_CFG, [override])
    errs = C.validate_config(cfg)
    assert any(needle in e for e in errs), errs
    with pytest.raises(C.ConfigError):
        C.require_valid(cfg)


def test_single_model_rule_in_production():
    cfg = _load(PROD_CFG, ["model.model_id=meta-llama/Llama-3-8B"])
    assert any("single generative model" in e for e in C.validate_config(cfg, check_paths=False))
    cfg = _load(PROD_CFG, ["model.local_tiny=true"])
    assert any("local_tiny" in e for e in C.validate_config(cfg, check_paths=False))


def test_smoke_mode_cannot_load_real_model_or_quantise():
    assert any("local_tiny" in e for e in C.validate_config(_load(SMOKE_CFG, ["model.local_tiny=false"])))
    assert any("quant" in e for e in C.validate_config(_load(SMOKE_CFG, ["quant.enabled=true"])))


def test_missing_data_path_and_tokenizer(tmp_path):
    errs = C.validate_config(_load(SMOKE_CFG, [f"data.train_path={tmp_path / 'missing.jsonl'}"]))
    assert any("train_path not found" in e for e in errs)
    errs = C.validate_config(_load(SMOKE_CFG, [f"model.tokenizer_path={tmp_path}"]))
    assert any("tokenizer files not found" in e for e in errs)


def test_snapshot_hash_semantics():
    base = _load(SMOKE_CFG)
    assert base.snapshot_hash() == _load(SMOKE_CFG, ["train.save_every=50", "train.log_every=7", "output.run_name=other"]).snapshot_hash()
    assert base.snapshot_hash() != _load(SMOKE_CFG, ["train.learning_rate=1e-3"]).snapshot_hash()
    assert base.snapshot_hash() != _load(SMOKE_CFG, ["lora.r=4"]).snapshot_hash()


def test_trust_remote_code_is_rejected():
    cfg = _load(PROD_CFG, ["model.trust_remote_code=true"])
    errs = C.validate_config(cfg, check_paths=False)
    assert any("trust_remote_code" in e for e in errs)
