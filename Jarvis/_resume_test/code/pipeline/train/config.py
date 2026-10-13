"""Training configuration: dataclasses, YAML loading, CLI overrides (--set a.b=c) and validation.

Strict by design: unknown keys, wrong types and impossible values are errors with a clear message (never silently ignored).
Relative data/tokenizer paths resolve against the config file's directory; output_dir resolves against the workspace root.
"""
from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

WORKSPACE = Path(__file__).resolve().parents[2]
OFFICIAL_MODEL_ID = "Qwen/Qwen3-8B"
MIN_TRANSFORMERS = (4, 51, 0)          # VERIFIED: Qwen3 needs transformers >= 4.51.0 (model card)
ALLOWED_MODES = ("production", "smoke")
# TEST-ONLY escape hatch: production mode rejects data.max_train_examples / max_eval_examples (a silent prefix-only run) unless this
# environment variable is set. It exists for unit tests of the production code path; never set it for real training (docs/TRAINING_DESIGN.md).
TEST_LIMITS_ENV = "QWEN_TEST_ONLY_ALLOW_EXAMPLE_LIMITS"
TEST_LIMITS_VALUE = "I_AM_TESTING_NOT_TRAINING"
ALLOWED_PRECISION = ("auto", "fp32", "fp16", "bf16")
ALLOWED_SCHEDULERS = ("cosine", "linear", "constant", "constant_with_warmup")
ALLOWED_OPTIMIZERS = ("adamw_torch",)
ALLOWED_TRAIN_ON = ("per_turn", "all_assistant", "last_assistant")
ALLOWED_VALIDATE = ("strict", "phase2", "skip", "off")
ALLOWED_KBIT = ("manual", "peft")
# config fields that may change between a run and its resume without invalidating the checkpoint
RESUME_IGNORE = ("max_consecutive_skipped_steps", "eval_every", "save_every", "log_every", "keep_last", "early_stopping_patience", "early_stopping_min_delta", "assumed_tokens_per_sec")


class ConfigError(ValueError):
    """Raised for any invalid / unsupported / malformed configuration."""


@dataclass
class TinyCfg:
    hidden_size: int = 64
    intermediate_size: int = 128
    num_hidden_layers: int = 2
    num_attention_heads: int = 4
    num_key_value_heads: int = 2
    head_dim: int = 16
    max_position_embeddings: int = 2048


@dataclass
class ModelCfg:
    model_id: str = OFFICIAL_MODEL_ID
    tokenizer_path: str = OFFICIAL_MODEL_ID
    local_tiny: bool = False
    trust_remote_code: bool = False
    attn_implementation: str = "sdpa"
    tiny: TinyCfg = field(default_factory=TinyCfg)


@dataclass
class QuantCfg:
    enabled: bool = True
    quant_type: str = "nf4"
    double_quant: bool = True
    compute_dtype: str = "auto"
    kbit_prepare: str = "manual"


@dataclass
class LoraCfg:
    r: int = 16
    alpha: int = 32
    dropout: float = 0.05
    bias: str = "none"
    task_type: str = "CAUSAL_LM"
    use_rslora: bool = False
    target_modules: list = field(default_factory=lambda: ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"])


@dataclass
class DataCfg:
    train_path: str = ""
    validation_path: str = ""
    test_path: str = ""
    dataset_version: str = ""
    max_seq_len: int = 2048
    train_on: str = "per_turn"
    validate: str = "strict"
    group_by_length: bool = True
    packing: bool = False
    max_train_examples: Any = None
    max_eval_examples: Any = None


@dataclass
class TrainCfg:
    epochs: int = 2
    max_steps: int = -1
    per_device_batch: int = 2
    grad_accum: int = 8
    learning_rate: float = 1e-4
    lr_scheduler: str = "cosine"
    warmup_ratio: float = 0.03
    weight_decay: float = 0.01
    adam_beta1: float = 0.9
    adam_beta2: float = 0.999
    adam_eps: float = 1e-8
    max_grad_norm: float = 1.0
    optimizer: str = "adamw_torch"
    precision: str = "auto"
    gradient_checkpointing: bool = True
    seed: int = 42
    eval_every: int = 100
    save_every: int = 100
    keep_last: int = 2
    log_every: int = 10
    early_stopping_patience: int = 4
    early_stopping_min_delta: float = 0.0
    assumed_tokens_per_sec: float = 250.0
    max_consecutive_skipped_steps: int = 20       # abort after this many consecutive non-finite (skipped) optimizer steps


@dataclass
class OutputCfg:
    output_dir: str = "runs"
    run_name: str = "run"


@dataclass
class Config:
    mode: str = "production"
    banner: str = ""
    production_locked: bool = True
    model: ModelCfg = field(default_factory=ModelCfg)
    quant: QuantCfg = field(default_factory=QuantCfg)
    lora: LoraCfg = field(default_factory=LoraCfg)
    data: DataCfg = field(default_factory=DataCfg)
    train: TrainCfg = field(default_factory=TrainCfg)
    output: OutputCfg = field(default_factory=OutputCfg)
    # resolved (not from YAML)
    config_dir: str = ""
    config_path: str = ""

    # ---- derived helpers -------------------------------------------------------------------
    def data_path(self, which: str) -> Path:
        p = getattr(self.data, f"{which}_path")
        return _resolve(p, self.config_dir)

    def tokenizer_location(self) -> str:
        t = self.model.tokenizer_path
        if self.model.local_tiny or t.startswith((".", "/")) or (len(t) > 1 and t[1] == ":"):
            return str(_resolve(t, self.config_dir))
        return t

    def run_dir(self) -> Path:
        out = Path(self.output.output_dir)
        out = out if out.is_absolute() else WORKSPACE / out
        return out / self.output.run_name

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d.pop("config_dir", None)
        d.pop("config_path", None)
        return d

    def snapshot_hash(self) -> str:
        """Hash of every field that changes the training trajectory (resume compatibility)."""
        d = self.to_dict()
        for k in RESUME_IGNORE:
            d["train"].pop(k, None)
        for k in ("train_path", "validation_path", "test_path"):
            d["data"].pop(k, None)          # data identity is checked through the dataset hash instead
        d["output"] = {}
        d.pop("banner", None)
        d.pop("production_locked", None)
        return hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()[:16]


def _resolve(p: str, base: str) -> Path:
    q = Path(p)
    return q if q.is_absolute() else (Path(base) / q).resolve()


# ---------------------------------------------------------------------------------------------
def _build(cls, data: dict, path: str):
    if not isinstance(data, dict):
        raise ConfigError(f"'{path}' must be a mapping, got {type(data).__name__}")
    known = {f.name: f for f in dataclasses.fields(cls) if f.name not in ("config_dir", "config_path")}
    unknown = sorted(set(data) - set(known))
    if unknown:
        raise ConfigError(f"unsupported config key(s) under '{path or '<root>'}': {', '.join(unknown)}")
    kwargs = {}
    for name, val in data.items():
        f = known[name]
        sub = f.default_factory() if f.default_factory is not dataclasses.MISSING else None  # type: ignore[misc]
        if dataclasses.is_dataclass(sub):
            kwargs[name] = _build(type(sub), val, f"{path}.{name}" if path else name)
        else:
            kwargs[name] = val
    return cls(**kwargs)


def load_config(path: str | Path, overrides: list[str] | None = None) -> Config:
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"config file not found: {p}")
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigError(f"malformed YAML in {p}: {e}") from e
    if not isinstance(raw, dict):
        raise ConfigError(f"{p} must contain a YAML mapping at top level")
    for ov in overrides or []:
        apply_override(raw, ov)
    cfg = _build(Config, raw, "")
    cfg.config_dir = str(p.resolve().parent)
    cfg.config_path = str(p.resolve())
    return cfg


def apply_override(raw: dict, spec: str) -> None:
    """--set train.learning_rate=2e-4   (value parsed as YAML so numbers/bools/lists/null work)."""
    if "=" not in spec:
        raise ConfigError(f"bad --set '{spec}': expected key=value")
    key, _, val = spec.partition("=")
    parts = key.strip().split(".")
    try:
        parsed = yaml.safe_load(val)
    except yaml.YAMLError as e:
        raise ConfigError(f"bad --set value for '{key}': {e}") from e
    if isinstance(parsed, str):
        try:                      # PyYAML (YAML 1.1) reads 2e-4 / 1e5 as strings; accept them as numbers on the command line
            parsed = float(parsed) if any(c in parsed for c in "eE.") and parsed.strip() == parsed else parsed
        except ValueError:
            pass
    cur = raw
    for part in parts[:-1]:
        if part not in cur or not isinstance(cur[part], dict):
            raise ConfigError(f"unsupported --set key '{key}' ('{part}' is not a section)")
        cur = cur[part]
    if parts[-1] not in cur:
        raise ConfigError(f"unsupported --set key '{key}': no such parameter")
    cur[parts[-1]] = parsed


# ---------------------------------------------------------------------------------------------
def _is_num(x, lo=None, hi=None, integer=False):
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return False
    if integer and int(x) != x:
        return False
    return (lo is None or x >= lo) and (hi is None or x <= hi)


def validate_config(cfg: Config, *, check_paths: bool = True) -> list[str]:
    """Returns a list of problems (empty = valid)."""
    e: list[str] = []
    if cfg.mode not in ALLOWED_MODES:
        e.append(f"mode must be one of {ALLOWED_MODES}, got {cfg.mode!r}")
    if not isinstance(cfg.production_locked, bool):
        e.append("production_locked must be true/false")
    m, q, lo, d, t, o = cfg.model, cfg.quant, cfg.lora, cfg.data, cfg.train, cfg.output
    if cfg.mode == "production":
        if m.local_tiny:
            e.append("production mode must not use model.local_tiny")
        if m.model_id != OFFICIAL_MODEL_ID:
            e.append(f"production model_id must be {OFFICIAL_MODEL_ID!r} (single generative model rule), got {m.model_id!r}")
    else:
        if not m.local_tiny:
            e.append("smoke mode requires model.local_tiny: true (it must never load Qwen3-8B)")
        if q.enabled:
            e.append("smoke mode cannot use quant.enabled (bitsandbytes is not available/testable locally)")
    if m.trust_remote_code:
        e.append("model.trust_remote_code must be false: Qwen3 is natively supported (transformers >= 4.51) and remote code would execute downloaded Python")
    if m.attn_implementation not in ("sdpa", "eager", "flash_attention_2"):
        e.append(f"unsupported model.attn_implementation {m.attn_implementation!r}")
    if cfg.mode == "production":
        if not q.enabled:
            e.append("production requires quant.enabled: true (an unquantised 8B model does not fit a 16 GB Kaggle GPU)")
        if m.attn_implementation == "flash_attention_2":
            e.append("model.attn_implementation 'flash_attention_2' is unsupported on Kaggle T4/P100 GPUs; use sdpa or eager")
        tk = cfg.tokenizer_location()
        if tk != OFFICIAL_MODEL_ID and Path(tk).resolve() != (WORKSPACE / "_hf_meta").resolve():
            e.append(f"production tokenizer must be {OFFICIAL_MODEL_ID!r} or the verified local _hf_meta directory, got {tk!r}")
    if q.enabled:
        if q.quant_type not in ("nf4", "fp4"):
            e.append("quant.quant_type must be nf4 or fp4")
        if q.compute_dtype not in ("auto", "fp16", "bf16", "fp32"):
            e.append("quant.compute_dtype must be auto/fp16/bf16/fp32")
        if q.kbit_prepare not in ALLOWED_KBIT:
            e.append(f"quant.kbit_prepare must be one of {ALLOWED_KBIT}")
    if not _is_num(lo.r, 1, 256, True):
        e.append("lora.r must be an integer in [1, 256]")
    if not _is_num(lo.alpha, 1, 1024, True):
        e.append("lora.alpha must be an integer in [1, 1024]")
    if not _is_num(lo.dropout, 0, 0.9):
        e.append("lora.dropout must be in [0, 0.9]")
    if lo.bias not in ("none", "all", "lora_only"):
        e.append("lora.bias must be none/all/lora_only")
    if lo.task_type != "CAUSAL_LM":
        e.append("lora.task_type must be CAUSAL_LM")
    if not isinstance(lo.target_modules, list) or not lo.target_modules or not all(isinstance(x, str) for x in lo.target_modules):
        e.append("lora.target_modules must be a non-empty list of module names")
    if not _is_num(d.max_seq_len, 32, 40960, True):
        e.append("data.max_seq_len must be an integer in [32, 40960] (Qwen3 max_position_embeddings is 40960)")
    if d.validate not in ALLOWED_VALIDATE:
        e.append(f"data.validate must be one of {ALLOWED_VALIDATE}")
    if d.train_on not in ALLOWED_TRAIN_ON:
        e.append(f"data.train_on must be one of {ALLOWED_TRAIN_ON}")
    if d.packing:
        e.append("data.packing is not supported (breaks per-example loss masks); set packing: false")
    for k in ("max_train_examples", "max_eval_examples"):
        v = getattr(d, k)
        if v is not None and not _is_num(v, 1, None, True):
            e.append(f"data.{k} must be null or a positive integer")
        elif v is not None and cfg.mode == "production" and os.environ.get(TEST_LIMITS_ENV) != TEST_LIMITS_VALUE:
            e.append(f"data.{k}={v} would train/evaluate on only a prefix of the frozen dataset; production requires null (the full dataset). "
                     f"TEST-ONLY override (never for real training): set environment variable {TEST_LIMITS_ENV}={TEST_LIMITS_VALUE}")
    if not d.dataset_version:
        e.append("data.dataset_version must be set (recorded in every checkpoint)")
    if not _is_num(t.epochs, 1, 100, True):
        e.append("train.epochs must be an integer >= 1")
    if not _is_num(t.max_steps, -1, None, True) or t.max_steps == 0:
        e.append("train.max_steps must be -1 (use epochs) or a positive integer")
    for k in ("per_device_batch", "grad_accum", "eval_every", "save_every", "keep_last", "log_every"):
        if not _is_num(getattr(t, k), 1, None, True):
            e.append(f"train.{k} must be a positive integer")
    if not _is_num(t.learning_rate, 1e-9, 1.0):
        e.append("train.learning_rate must be in (0, 1]")
    if t.learning_rate > 1e-2 and cfg.mode == "production":
        e.append("train.learning_rate > 1e-2 is unreasonable for production LoRA SFT")
    if t.lr_scheduler not in ALLOWED_SCHEDULERS:
        e.append(f"train.lr_scheduler must be one of {ALLOWED_SCHEDULERS}")
    if not _is_num(t.warmup_ratio, 0, 0.5):
        e.append("train.warmup_ratio must be in [0, 0.5]")
    if not _is_num(t.weight_decay, 0, 1):
        e.append("train.weight_decay must be in [0, 1]")
    if not _is_num(t.max_grad_norm, 0):
        e.append("train.max_grad_norm must be >= 0")
    if t.optimizer not in ALLOWED_OPTIMIZERS:
        e.append(f"train.optimizer must be one of {ALLOWED_OPTIMIZERS}")
    if t.precision not in ALLOWED_PRECISION:
        e.append(f"train.precision must be one of {ALLOWED_PRECISION}")
    if not isinstance(t.seed, int) or isinstance(t.seed, bool):
        e.append("train.seed must be an integer")
    if not _is_num(t.max_consecutive_skipped_steps, 1, None, True):
        e.append("train.max_consecutive_skipped_steps must be an integer >= 1")
    if not _is_num(t.early_stopping_patience, 0, None, True):
        e.append("train.early_stopping_patience must be an integer >= 0 (0 disables)")
    if not o.run_name or any(c in o.run_name for c in '\\/:*?"<>|'):
        e.append("output.run_name must be a simple directory name")
    if check_paths:
        for which in ("train", "validation"):
            p = cfg.data_path(which)
            if not p.exists():
                e.append(f"data.{which}_path not found: {p}")
        tk = cfg.tokenizer_location()
        if (m.local_tiny or tk.startswith(("/", "."))) and not (Path(tk) / "tokenizer.json").exists():
            e.append(f"tokenizer files not found in {tk}")
    return e


def require_valid(cfg: Config, *, check_paths: bool = True) -> None:
    errs = validate_config(cfg, check_paths=check_paths)
    if errs:
        raise ConfigError("invalid configuration:\n  - " + "\n  - ".join(errs))


def clone(cfg: Config) -> Config:
    return copy.deepcopy(cfg)
