"""Shared helpers for the training/checkpoint tests (no tests in this file)."""
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[1]
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))

from pipeline.train import config as C  # noqa: E402
from pipeline.train import train as T  # noqa: E402

SMOKE_CFG = WORKSPACE / "configs" / "smoke_test.yaml"
PROD_CFG = WORKSPACE / "configs" / "qlora_sft.yaml"

_CACHE = {}


def smoke_cfg(**over):
    """Smoke config with small data limits; over = {'train.max_steps': 4, ...}."""
    sets = [f"{k}={v}" for k, v in over.items()]
    return C.load_config(SMOKE_CFG, sets)


def tokenizer_and_data(cfg):
    key = (cfg.data.max_seq_len, cfg.data.max_train_examples, cfg.data.max_eval_examples)
    if key not in _CACHE:
        tok = T.load_tokenizer(cfg)
        tr, va, info = T.load_data(cfg, tok)
        _CACHE[key] = (tok, tr, va, info)
    return _CACHE[key]


def make_trainer(cfg, run_dir, echo=False):
    tok, tr, va, info = tokenizer_and_data(cfg)
    model = T.attach_lora(T.build_tiny_model(cfg), cfg)
    return T.Trainer(cfg, model, tok, tr, va, info, run_dir=run_dir, device="cpu", precision="fp32", echo=echo)


def adapter_tensors(trainer):
    return {n: p.detach().clone() for n, p in trainer.model.named_parameters() if p.requires_grad}
