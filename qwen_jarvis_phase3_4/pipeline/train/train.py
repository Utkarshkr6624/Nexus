"""Qwen3-8B QLoRA SFT training engine (B6, B7).

Safe modes (Phase 3/4):  --validate-config | --dry-run | --smoke-test
Production training is LOCKED: it needs ALL of
    1. environment variable QWEN_PHASE5_UNLOCK=I_UNDERSTAND_THIS_STARTS_PRODUCTION_TRAINING
    2. the --confirm-production flag
    3. production_locked: false in the config
and a production config (mode: production). Phase 3/4 never satisfies these. Importing this module does nothing.

  python pipeline/train/train.py --config configs/qlora_sft.yaml --validate-config
  python pipeline/train/train.py --config configs/qlora_sft.yaml --dry-run
  python pipeline/train/train.py --smoke-test                       # tiny random Qwen3-architecture model on CPU
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[2]
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))

from pipeline.train import checkpoint as K  # noqa: E402
from pipeline.train import config as C  # noqa: E402
from pipeline.train import data_adapter as D  # noqa: E402
from pipeline.train import repro  # noqa: E402
from pipeline.train.callbacks import EarlyStopping, MetricsLogger  # noqa: E402

UNLOCK_ENV = "QWEN_PHASE5_UNLOCK"
UNLOCK_VALUE = "I_UNDERSTAND_THIS_STARTS_PRODUCTION_TRAINING"
SMOKE_BANNER = "PIPELINE SMOKE TEST - NOT QWEN PRODUCTION TRAINING"
EXIT = {"ok": 0, "error": 1, "config": 2, "locked": 3, "checkpoint": 4}


class ProductionLocked(RuntimeError):
    """Raised whenever production training is requested without the full Phase-5 unlock."""


def check_production_unlock(cfg: C.Config, confirm_flag: bool, env=None) -> None:
    env = os.environ if env is None else env
    missing = []
    if env.get(UNLOCK_ENV) != UNLOCK_VALUE:
        missing.append(f"environment variable {UNLOCK_ENV}={UNLOCK_VALUE}")
    if not confirm_flag:
        missing.append("the --confirm-production flag")
    if cfg.production_locked is not False:
        missing.append("production_locked: false in the config")
    if cfg.mode != "production":
        missing.append("a production config (mode: production)")
    if missing:
        raise ProductionLocked(
            "PRODUCTION TRAINING IS LOCKED. Qwen3-8B training is reserved for Phase 5 on Kaggle and is not allowed in Phase 3/4.\n"
            "Missing: " + "; ".join(missing) + ".\nSafe alternatives: --validate-config, --dry-run, --smoke-test.")


# ------------------------------------------------------------------------------------------------
def resolve_precision(cfg: C.Config) -> str:
    import torch

    p = cfg.train.precision
    if p != "auto":
        return p
    if torch.cuda.is_available():
        return "bf16" if repro.bf16_native() else "fp16"        # NOT is_bf16_supported(): it is True on a T4 through emulation
    return "fp32"


def load_tokenizer(cfg: C.Config):
    from transformers import AutoTokenizer

    tok = AutoTokenizer.from_pretrained(cfg.tokenizer_location(), trust_remote_code=cfg.model.trust_remote_code)
    if tok.pad_token is None:
        tok.pad_token = "<|endoftext|>"
    return tok


def build_tiny_model(cfg: C.Config):
    """Randomly-initialised Qwen3-architecture model (smoke test only). No weights are downloaded."""
    import torch
    from transformers import AutoModelForCausalLM, Qwen3Config

    if not cfg.model.local_tiny:
        raise C.ConfigError("build_tiny_model requires model.local_tiny: true")
    base = json.loads((Path(cfg.tokenizer_location()) / "config.json").read_text(encoding="utf-8"))
    t = cfg.model.tiny
    base.update(hidden_size=t.hidden_size, intermediate_size=t.intermediate_size, num_hidden_layers=t.num_hidden_layers,
                num_attention_heads=t.num_attention_heads, num_key_value_heads=t.num_key_value_heads, head_dim=t.head_dim,
                max_position_embeddings=t.max_position_embeddings, max_window_layers=t.num_hidden_layers)
    base.pop("torch_dtype", None)
    base.pop("dtype", None)
    conf = Qwen3Config(**{k: v for k, v in base.items() if k not in ("architectures", "model_type", "transformers_version")})
    torch.manual_seed(cfg.train.seed)
    model = AutoModelForCausalLM.from_config(conf, attn_implementation=cfg.model.attn_implementation)
    return model.float()


def load_production_model(cfg: C.Config, unlocked: bool):
    """Qwen3-8B 4-bit load. NOT TESTED LOCALLY (bitsandbytes/CUDA); reachable only after the production unlock."""
    if not unlocked:
        raise ProductionLocked("load_production_model called without the production unlock")
    import torch
    from transformers import AutoModelForCausalLM, BitsAndBytesConfig

    if not torch.cuda.is_available():
        raise RuntimeError("production training needs a CUDA GPU (Kaggle T4/P100)")
    prec = resolve_precision(cfg)
    cd = cfg.quant.compute_dtype
    cd = ("bf16" if prec == "bf16" else "fp16") if cd == "auto" else cd
    compute = {"bf16": torch.bfloat16, "fp16": torch.float16, "fp32": torch.float32}[cd]
    qcfg = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type=cfg.quant.quant_type, bnb_4bit_use_double_quant=cfg.quant.double_quant,
                              bnb_4bit_compute_dtype=compute)
    kw = dict(quantization_config=qcfg, device_map={"": 0}, attn_implementation=cfg.model.attn_implementation,
              trust_remote_code=cfg.model.trust_remote_code)
    try:
        model = AutoModelForCausalLM.from_pretrained(cfg.model.model_id, dtype=compute, **kw)
    except TypeError:    # transformers < 4.56 spells it torch_dtype
        model = AutoModelForCausalLM.from_pretrained(cfg.model.model_id, torch_dtype=compute, **kw)
    model.config.use_cache = False
    if cfg.quant.kbit_prepare == "peft":
        from peft import prepare_model_for_kbit_training

        model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=cfg.train.gradient_checkpointing,
                                                gradient_checkpointing_kwargs={"use_reentrant": False})
    elif cfg.train.gradient_checkpointing:
        model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
        model.enable_input_require_grads()
    return model


def attach_lora(model, cfg: C.Config):
    from peft import LoraConfig, get_peft_model

    lc = cfg.lora
    pc = LoraConfig(r=lc.r, lora_alpha=lc.alpha, lora_dropout=lc.dropout, bias=lc.bias, task_type=lc.task_type, use_rslora=lc.use_rslora,
                    target_modules=list(lc.target_modules))
    return get_peft_model(model, pc)


# ------------------------------------------------------------------------------------------------
class NonFiniteError(RuntimeError):
    """Training produced non-finite loss/gradients (fp16 overflow or divergence); the run is aborted instead of silently not learning."""


def require_primary_loader(cfg: C.Config) -> None:
    """Production (and --dry-run on a production config) must use the verified Data-agent loader. The fallback renderer supervises
    different tokens and is allowed in smoke mode only."""
    if D.SOURCE != "pipeline.data" and cfg.mode == "production":
        raise RuntimeError("the verified data loader pipeline.data could not be imported "
                           f"({D.IMPORT_ERROR}); refusing to use the fallback renderer in production mode. Fix the environment and retry.")


def find_manifest(data_dir: Path):
    """MANIFEST.json, matched case-insensitively (Linux file systems are case sensitive; packaged copies may be lower case)."""
    for q in sorted(data_dir.iterdir()) if data_dir.is_dir() else []:
        if q.is_file() and q.name.lower() == "manifest.json":
            return q
    return None


def load_data(cfg: C.Config, tok, *, limit_train=None, limit_eval=None, random_subset=False):
    """Returns (train_samples, val_samples, dataset_info). Integrity (manifest) is checked BEFORE any data is rendered."""
    lim_t = limit_train if limit_train is not None else cfg.data.max_train_examples
    lim_e = limit_eval if limit_eval is not None else cfg.data.max_eval_examples
    tp, vp = cfg.data_path("train"), cfg.data_path("validation")
    require_primary_loader(cfg)
    info = {"version": cfg.data.dataset_version, "train_file": tp.name, "validation_file": vp.name,
            "train_sha256": D.file_sha256(tp), "validation_sha256": D.file_sha256(vp), "train_records_in_file": D.count_lines(tp),
            "validation_records_in_file": D.count_lines(vp), "limits": {"train": lim_t, "validation": lim_e}, "loader": D.SOURCE}
    info.update(check_manifest(cfg, tp, vp, info))
    tr = D.load_examples(tp, tok, cfg.data.max_seq_len, cfg.data.train_on, lim_t, cfg.data.validate, random_subset)
    va = D.load_examples(vp, tok, cfg.data.max_seq_len, cfg.data.train_on, lim_e, cfg.data.validate, random_subset)
    info["train_samples_used"], info["validation_samples_used"] = len(tr), len(va)
    return tr, va, info


def check_manifest(cfg: C.Config, tp: Path, vp: Path, info: dict) -> dict:
    """A frozen dataset ships a manifest (any capitalisation) beside the data files. Its version must equal data.dataset_version and the
    train/validation SHA-256 values must match. In PRODUCTION mode a missing manifest is an error; its hash joins every checkpoint identity."""
    mp = find_manifest(tp.parent)
    if mp is None:
        if cfg.mode == "production":
            raise ValueError(f"no dataset manifest (MANIFEST.json, any capitalisation) found in {tp.parent}; production training requires the frozen "
                             "dataset's manifest so its integrity can be verified")
        return {"manifest": None, "manifest_sha256": None}
    try:
        man = json.loads(mp.read_text(encoding="utf-8"))
        files = man["files"]
    except (json.JSONDecodeError, KeyError) as e:
        raise ValueError(f"{mp} is not a valid dataset manifest ({type(e).__name__})") from e
    if man.get("version") != cfg.data.dataset_version:
        raise ValueError(f"dataset version mismatch: config says {cfg.data.dataset_version!r}, {mp.name} says {man.get('version')!r}")
    for name, got in ((tp.name, info["train_sha256"]), (vp.name, info["validation_sha256"])):
        want = files.get(name, {}).get("sha256")
        if want != got:
            raise ValueError(f"{name} does not match {mp.name} (sha256 {got[:12]}... vs manifest {str(want)[:12]}...): the frozen dataset was modified")
    return {"manifest": {"version": man["version"], "sha256": {k: v.get("sha256") for k, v in files.items()}, "status": man.get("status")},
            "manifest_sha256": D.file_sha256(mp)}


FINGERPRINT_FILE = WORKSPACE / "configs" / "tokenizer_fingerprint.json"


def check_tokenizer(cfg: C.Config, tok) -> dict:
    """Hash the tokenizer's chat template / vocabulary / special tokens and compare with the verified reference: the local _hf_meta files when
    present, else configs/tokenizer_fingerprint.json. Production: mismatch is an error. Returns the record stored in reproducibility.json."""
    fp = repro.tokenizer_fingerprint(tok)
    ref, ref_src = None, None
    meta = WORKSPACE / "_hf_meta"
    if (meta / "tokenizer.json").exists():
        from transformers import AutoTokenizer

        ref, ref_src = repro.tokenizer_fingerprint(AutoTokenizer.from_pretrained(str(meta))), "_hf_meta"
    elif FINGERPRINT_FILE.exists():
        ref, ref_src = json.loads(FINGERPRINT_FILE.read_text(encoding="utf-8")), FINGERPRINT_FILE.name
    keys = ("chat_template_sha256", "vocab_sha256", "special_tokens_sha256", "vocab_size")
    match = None if ref is None else all(fp.get(k) == ref.get(k) for k in keys)
    out = {"fingerprint": fp, "reference_source": ref_src, "reference": ref, "match": match}
    if match is False and cfg.mode == "production":
        diff = [k for k in keys if fp.get(k) != ref.get(k)]
        raise RuntimeError(f"tokenizer/chat-template differs from the verified Qwen3-8B reference ({ref_src}) in: {', '.join(diff)}; refusing to train")
    if match is None and cfg.mode == "production":
        print("WARNING: no reference tokenizer fingerprint available; recorded only.")
    return out


def plan_epoch(lengths: list[int], bs: int, group: bool, seed: int, epoch: int) -> list[list[int]]:
    """Deterministic micro-batch plan for one epoch: depends only on (lengths, bs, seed, epoch)."""
    rng = random.Random(seed * 1_000_003 + epoch)
    idx = list(range(len(lengths)))
    rng.shuffle(idx)
    if group:
        mega = max(bs, 50 * bs)
        chunks = [sorted(idx[i:i + mega], key=lambda j: (-lengths[j], j)) for i in range(0, len(idx), mega)]
        batches = [c[i:i + bs] for c in chunks for i in range(0, len(c), bs)]
        rng.shuffle(batches)
    else:
        batches = [idx[i:i + bs] for i in range(0, len(idx), bs)]
    return batches


def steps_per_epoch(n_samples: int, bs: int, accum: int) -> int:
    return max(1, math.ceil(n_samples / bs) // accum)


def n_items(samples) -> int:
    """Supervised (label != -100) tokens after the causal shift."""
    return sum(1 for s in samples for x in s["labels"][1:] if x != -100)


# ------------------------------------------------------------------------------------------------
LOSS_CHUNK = 1024      # supervised positions per lm_head/cross-entropy chunk (1024 x 151,936 x 4 B = 0.6 GB transient fp32 logits)


def causal_lm_of(model):
    """The Qwen3ForCausalLM inside an optional PEFT wrapper (LoRA layers are injected in place, so calling it applies the adapters)."""
    return model.get_base_model() if hasattr(model, "get_base_model") else model


def _ce_sum(head, hidden, targets):
    import torch.nn.functional as F

    return F.cross_entropy(head(hidden).float(), targets, reduction="sum")


def masked_lm_loss(model, batch: dict, items: int):
    """Token-weighted causal-LM loss computed ONLY at supervised positions (OOM guard for the 151,936-entry vocabulary).

    Equivalent to HF's shifted cross-entropy with `num_items_in_batch=items`, but the transformer body runs on all tokens and the
    expensive lm_head + fp32 logits are computed for the labelled positions only, in chunks that are recomputed in backward
    (activation checkpointing), so peak memory does not scale with seq_len x micro_batch x vocab.
    """
    import torch
    from torch.utils.checkpoint import checkpoint

    lm = causal_lm_of(model)
    hidden = lm.model(input_ids=batch["input_ids"], attention_mask=batch["attention_mask"]).last_hidden_state[:, :-1]
    targets = batch["labels"][:, 1:]
    keep = targets != -100
    hs, ys = hidden[keep], targets[keep]
    if ys.numel() == 0:
        return hidden.sum() * 0.0
    total = hidden.new_zeros((), dtype=torch.float32)
    use_ckpt = torch.is_grad_enabled() and hs.requires_grad
    for i in range(0, ys.numel(), LOSS_CHUNK):
        h, y = hs[i:i + LOSS_CHUNK], ys[i:i + LOSS_CHUNK]
        total = total + (checkpoint(_ce_sum, lm.lm_head, h, y, use_reentrant=False) if use_ckpt else _ce_sum(lm.lm_head, h, y))
    return total / items


class Trainer:
    def __init__(self, cfg, model, tok, train_samples, val_samples, dataset_info, *, run_dir=None, device="cpu", precision="fp32", echo=True):
        import torch
        from transformers import get_scheduler

        self.cfg, self.model, self.tok, self.tr, self.va, self.info = cfg, model, tok, train_samples, val_samples, dataset_info
        self.device, self.precision = device, precision
        self.run_dir = Path(run_dir) if run_dir else cfg.run_dir()
        t = cfg.train
        self.spe = steps_per_epoch(len(self.tr), t.per_device_batch, t.grad_accum)
        self.total_steps = min(t.max_steps, t.epochs * self.spe) if t.max_steps > 0 else t.epochs * self.spe
        params = [p for p in model.parameters() if p.requires_grad]
        if not params:
            raise RuntimeError("no trainable parameters: LoRA adapters are not attached")
        self.trainable = params
        self.opt = torch.optim.AdamW(params, lr=t.learning_rate, betas=(t.adam_beta1, t.adam_beta2), eps=t.adam_eps, weight_decay=t.weight_decay)
        warm = math.ceil(t.warmup_ratio * self.total_steps)
        self.sched = get_scheduler(t.lr_scheduler, self.opt, num_warmup_steps=warm, num_training_steps=self.total_steps)
        self.use_cuda = device.startswith("cuda")
        self.scaler = torch.amp.GradScaler("cuda") if (precision == "fp16" and self.use_cuda) else None
        self.stopper = EarlyStopping(t.early_stopping_patience, t.early_stopping_min_delta)
        self.step = 0
        self.tokens_seen = 0
        self.logger = MetricsLogger(self.run_dir / "metrics.jsonl", echo=echo)
        self.identity = {"model_id": cfg.model.model_id, "dataset_version": dataset_info["version"],
                         "dataset_hash": dataset_info["train_sha256"][:16] + ":" + dataset_info["validation_sha256"][:16],
                         "dataset_manifest_sha256": dataset_info.get("manifest_sha256"), "config_hash": cfg.snapshot_hash()}
        self.stop_reason = None
        self.skipped_steps = 0
        self.consecutive_skipped = 0

    # -- helpers -------------------------------------------------------------------------------
    def _autocast(self):
        import torch

        if self.precision == "bf16" and self.use_cuda:
            return torch.autocast("cuda", dtype=torch.bfloat16)
        if self.precision == "fp16" and self.use_cuda:
            return torch.autocast("cuda", dtype=torch.float16)
        return torch.autocast("cpu", enabled=False)

    def _batch(self, samples, idxs):
        b = D.collate([samples[i] for i in idxs], self.tok.pad_token_id)
        return {k: v.to(self.device) for k, v in b.items()}

    def _forward_loss(self, samples, idxs, items):
        batch = self._batch(samples, idxs)
        with self._autocast():
            return masked_lm_loss(self.model, batch, items)

    def evaluate(self) -> dict:
        import torch

        self.model.eval()
        lengths = [len(s["input_ids"]) for s in self.va]
        order = sorted(range(len(self.va)), key=lambda i: (-lengths[i], i))
        bs = self.cfg.train.per_device_batch
        tot, cnt = 0.0, 0
        with torch.no_grad():
            for i in range(0, len(order), bs):
                idxs = order[i:i + bs]
                items = n_items([self.va[j] for j in idxs])
                if items == 0:
                    continue
                loss = self._forward_loss(self.va, idxs, items)
                tot += float(loss) * items
                cnt += items
        self.model.train()
        if cnt == 0:
            raise RuntimeError("validation set has no supervised tokens")
        vl = tot / cnt
        return {"val_loss": vl, "val_ppl": math.exp(min(vl, 50)), "val_tokens": cnt}

    def _loop_state(self, epoch):
        return {"step": self.step, "epoch": epoch, "tokens_seen": self.tokens_seen, "stopper": self.stopper.state(), "total_steps": self.total_steps,
                "skipped_steps": self.skipped_steps, "consecutive_skipped": self.consecutive_skipped}

    def lora_b_norm(self) -> float:
        """L2 norm of all LoRA B matrices. B starts at exactly 0, so a norm > 0 proves the adapter was updated."""
        tot = 0.0
        for n, p in self.model.named_parameters():
            if "lora_B" in n:
                tot += float(p.detach().float().pow(2).sum())
        return math.sqrt(tot)

    def save(self, epoch, metrics, is_best=False):
        metrics = {**metrics, "skipped_steps": self.skipped_steps, "lora_B_norm": self.lora_b_norm()}
        return K.save_checkpoint(self.run_dir, step=self.step, model=self.model, optimizer=self.opt, scheduler=self.sched, scaler=self.scaler,
                                 loop_state=self._loop_state(epoch), identity=self.identity, metrics=metrics, keep_last=self.cfg.train.keep_last, is_best=is_best)

    def resume(self, path: Path, force=False):
        ck = K.load_checkpoint(path, expected=self.identity, force=force)
        K.load_adapter_into(self.model, ck["adapter_dir"])
        st = ck["state"]
        self.opt.load_state_dict(st["optimizer"])
        if st.get("scheduler") is not None:
            self.sched.load_state_dict(st["scheduler"])
        if st.get("scaler") is not None and self.scaler is not None:
            self.scaler.load_state_dict(st["scaler"])
        K.restore_rng(st["rng"])
        loop = st["loop"]
        self.step, self.tokens_seen = int(loop["step"]), int(loop.get("tokens_seen", 0))
        self.skipped_steps, self.consecutive_skipped = int(loop.get("skipped_steps", 0)), int(loop.get("consecutive_skipped", 0))
        self.stopper.load(loop.get("stopper", {}))
        if ck["mismatches"]:
            print("WARNING (forced resume) mismatches: " + "; ".join(ck["mismatches"]))
        return ck["meta"]

    def _register_skip(self, why: str, **kv) -> None:
        self.skipped_steps += 1
        self.consecutive_skipped += 1
        self.logger.log(step=self.step + 1, event="step_skipped", reason=why, skipped_steps=self.skipped_steps, consecutive=self.consecutive_skipped, **kv)
        limit = self.cfg.train.max_consecutive_skipped_steps
        if self.consecutive_skipped >= limit:
            raise NonFiniteError(f"{self.consecutive_skipped} consecutive optimizer steps were skipped because of non-finite {why} (limit {limit}, "
                                 f"{self.skipped_steps} skipped in total). The run is not learning (fp16 overflow or divergence). Aborting; try "
                                 "precision bf16 on an Ampere+ GPU, a lower learning rate, or a shorter max_seq_len.")

    # -- main loop -----------------------------------------------------------------------------
    def fit(self, *, stop_at_step: int | None = None) -> dict:
        import torch

        t = self.cfg.train
        lengths = [len(s["input_ids"]) for s in self.tr]
        self.model.train()
        accum = t.grad_accum
        last_eval_step = None
        last_metrics: dict = {}
        epoch_plans: dict[int, list] = {}
        start_step, tokens0, t0 = self.step, self.tokens_seen, time.perf_counter()
        first_tps = None

        def tps():
            return (self.tokens_seen - tokens0) / max(1e-9, time.perf_counter() - t0)

        while self.step < self.total_steps:
            epoch, offset = divmod(self.step, self.spe)
            if epoch not in epoch_plans:
                epoch_plans = {epoch: plan_epoch(lengths, t.per_device_batch, self.cfg.data.group_by_length, t.seed, epoch)}
            micro = epoch_plans[epoch][offset * accum:(offset + 1) * accum]
            items = sum(n_items([self.tr[j] for j in b]) for b in micro)
            if items == 0:
                raise RuntimeError(f"step {self.step}: no supervised tokens in the accumulation group")
            total_loss, group_bad = 0.0, False
            for b in micro:
                loss = self._forward_loss(self.tr, b, items)      # already divided by the group's token count
                lval = float(loss.detach())
                if not math.isfinite(lval):
                    if self.scaler is None:
                        raise NonFiniteError(f"non-finite loss ({lval}) at step {self.step + 1} with no GradScaler active; aborting before backward "
                                             "so the adapter is not corrupted")
                    group_bad = True
                    break
                total_loss += lval
                (self.scaler.scale(loss) if self.scaler else loss).backward()
                self.tokens_seen += sum(len(self.tr[j]["input_ids"]) for j in b)
            gn = float("nan")
            if group_bad:
                self.opt.zero_grad(set_to_none=True)
                total_loss = float("nan")
                self._register_skip("loss")
            else:
                if self.scaler:
                    self.scaler.unscale_(self.opt)
                gn = float(torch.nn.utils.clip_grad_norm_(self.trainable, t.max_grad_norm if t.max_grad_norm > 0 else float("inf")))
                if not math.isfinite(gn):
                    if self.scaler is None:
                        raise NonFiniteError(f"non-finite gradient norm ({gn}) at step {self.step + 1} (loss={total_loss}) with no GradScaler active; "
                                             "aborting rather than corrupting the adapter")
                    self.opt.zero_grad(set_to_none=True)
                    self.scaler.update()
                    self._register_skip("gradient", grad_norm=str(gn))
                else:
                    if self.scaler:
                        self.scaler.step(self.opt)
                        self.scaler.update()
                    else:
                        self.opt.step()
                    self.sched.step()
                    self.opt.zero_grad(set_to_none=True)
                    self.consecutive_skipped = 0
            self.step += 1
            steps_run = self.step - start_step
            if first_tps is None and (steps_run >= 10 or self.step >= self.total_steps):
                first_tps = tps()
                self.logger.log(step=self.step, event="first_steps_throughput", steps_measured=steps_run, tokens_per_sec=first_tps,
                                note="MEASURED on this machine; replaces assumed_tokens_per_sec")
            if self.step % t.log_every == 0 or self.step == 1:
                self.logger.log(step=self.step, epoch=round(self.step / self.spe, 3), loss=total_loss, grad_norm=gn, lr=self.sched.get_last_lr()[0],
                                tokens=self.tokens_seen, tok_per_s=round(tps(), 1), skipped=self.skipped_steps)
            done = self.step >= self.total_steps or (stop_at_step is not None and self.step >= stop_at_step)
            do_eval = self.step % t.eval_every == 0 or done
            improved = False
            if do_eval:
                last_metrics = self.evaluate()
                last_eval_step = self.step
                improved, stop = self.stopper.update(self.step, last_metrics["val_loss"])
                self.logger.log(step=self.step, **last_metrics, best_val_loss=self.stopper.best, bad_evals=self.stopper.bad)
                if stop:
                    self.stop_reason = f"early stopping: no validation improvement for {self.stopper.bad} evaluations"
                    done = True
            if improved or self.step % t.save_every == 0 or done:
                self.save(epoch, {"train_loss": total_loss, **last_metrics, "metrics_step": last_eval_step}, is_best=improved)
            if done:
                break
        norm = self.lora_b_norm()
        updated = math.isfinite(norm) and norm > 0.0
        res = {"step": self.step, "total_steps": self.total_steps, "best_val_loss": self.stopper.best, "best_step": self.stopper.best_step,
               "last": last_metrics, "stop_reason": self.stop_reason, "tokens_seen": self.tokens_seen, "skipped_steps": self.skipped_steps,
               "tokens_per_sec_session": round(tps(), 2), "first_steps_tokens_per_sec": None if first_tps is None else round(first_tps, 2),
               "lora_B_norm": norm, "adapter_updated": updated}
        if self.step > start_step and not updated:
            raise NonFiniteError(f"training finished but the LoRA adapter did not change (B-matrix norm = {norm}); refusing to report success")
        return res


# ------------------------------------------------------------------------------------------------
def summarize(cfg: C.Config) -> str:
    t, d = cfg.train, cfg.data
    return (f"{cfg.banner or cfg.mode}\n  mode={cfg.mode} locked={cfg.production_locked} model={cfg.model.model_id} quant={'nf4' if cfg.quant.enabled else 'off'}\n"
            f"  lora r={cfg.lora.r} alpha={cfg.lora.alpha} dropout={cfg.lora.dropout} targets={','.join(cfg.lora.target_modules)}\n"
            f"  data version={d.dataset_version} max_len={d.max_seq_len} train_on={d.train_on} validate={d.validate}\n"
            f"  train epochs={t.epochs} bs={t.per_device_batch}x{t.grad_accum} (effective {t.per_device_batch * t.grad_accum}) lr={t.learning_rate} "
            f"sched={t.lr_scheduler} precision={t.precision} grad_ckpt={t.gradient_checkpointing} seed={t.seed}")


def cmd_validate_config(cfg) -> int:
    C.require_valid(cfg)
    print(summarize(cfg))
    print("CONFIG VALID (no model loaded, no training performed)")
    return EXIT["ok"]


def cmd_dry_run(cfg, sample=256) -> dict:
    """Loads config + tokenizer + a data sample, builds batches, prints the plan. NEVER loads a model."""
    C.require_valid(cfg)
    require_primary_loader(cfg)
    local_meta = WORKSPACE / "_hf_meta"
    if cfg.tokenizer_location() == C.OFFICIAL_MODEL_ID and (local_meta / "tokenizer.json").exists():
        # offline dry run: the local _hf_meta files are byte-identical to the hub tokenizer (checked by the fingerprint comparison below)
        cfg = C.clone(cfg)
        cfg.model.tokenizer_path = str(local_meta)
        print(f"(dry-run uses the local verified tokenizer files in {local_meta.name}; no network access needed)")
    tok = load_tokenizer(cfg)
    tk_check = check_tokenizer(cfg, tok)
    tp, vp = cfg.data_path("train"), cfg.data_path("validation")
    # EXACT sample counts come from the WHOLE files (assistant-message count, no tokenising). Only the token LENGTH below is sampled
    # (a seeded random subset of records, not the file prefix) and therefore an estimate.
    n_train = D.count_samples(tp, cfg.data.train_on)
    n_val = D.count_samples(vp, cfg.data.train_on)
    tr, va, info = load_data(cfg, tok, limit_train=min(sample, D.count_lines(tp)), limit_eval=min(64, D.count_lines(vp)), random_subset=True)
    lengths = [len(s["input_ids"]) for s in tr]
    plan = plan_epoch(lengths, cfg.train.per_device_batch, cfg.data.group_by_length, cfg.train.seed, 0)
    first = D.collate([tr[i] for i in plan[0]], tok.pad_token_id)
    mean_len = sum(lengths) / max(1, len(lengths))
    spe = steps_per_epoch(n_train, cfg.train.per_device_batch, cfg.train.grad_accum)
    total = cfg.train.max_steps if cfg.train.max_steps > 0 else cfg.train.epochs * spe
    est_tokens = n_train * mean_len * cfg.train.epochs
    t = cfg.train
    out = {"banner": cfg.banner, "dataset": info, "batch_shape": list(first["input_ids"].shape),
           "EXACT_train_records": info["train_records_in_file"], "EXACT_train_samples": n_train,
           "EXACT_validation_records": info["validation_records_in_file"], "EXACT_validation_samples": n_val,
           "EXACT_samples_per_epoch_note": "samples = assistant messages in the whole file (before any max_seq_len drop; 0 dropped per reports/data_stats_*.json)",
           "limits_active": {"train": cfg.data.max_train_examples, "validation": cfg.data.max_eval_examples},
           "EXACT_steps_per_epoch": spe, "EXACT_total_optimizer_steps": total,
           "EXACT_evaluations_incl_final": total // t.eval_every + (1 if total % t.eval_every else 0),
           "EXACT_periodic_checkpoints_incl_final": total // t.save_every + (1 if total % t.save_every else 0),
           "checkpoint_note": "plus one extra checkpoint at every new best validation loss; retention keeps the last %d + best" % t.keep_last,
           "samples_left_out_per_epoch_by_step_rounding_(reshuffled_each_epoch)": n_train - spe * t.grad_accum * t.per_device_batch,
           "mean_tokens_per_sample_ESTIMATED_from_random_%d_records" % len(tr): round(mean_len, 1),
           "tokens_to_process_ESTIMATED": int(est_tokens),
           "wall_time_hours_ESTIMATED_not_measured": round(est_tokens / max(1.0, cfg.train.assumed_tokens_per_sec) / 3600, 2),
           "effective_batch": cfg.train.per_device_batch * cfg.train.grad_accum, "model_loaded": False,
           "tokenizer_check": {"reference_source": tk_check["reference_source"], "match": tk_check["match"],
                               "chat_template_sha256": tk_check["fingerprint"]["chat_template_sha256"]},
           "note": "ESTIMATED values use assumed_tokens_per_sec from the config; nothing here was measured on a GPU."}
    print(summarize(cfg))
    print(json.dumps(out, indent=1, default=str))
    print("DRY RUN OK (no model loaded, no training performed)")
    return out


def run_smoke(cfg: C.Config, run_dir: Path | None = None, echo=True) -> dict:
    """Tiny-model end-to-end pipeline check: train -> checkpoint -> interrupt -> resume -> evaluate. NOT Qwen training."""
    import shutil

    if cfg.mode != "smoke" or not cfg.model.local_tiny:
        raise C.ConfigError("--smoke-test needs a smoke config (mode: smoke, model.local_tiny: true); it never loads Qwen3-8B")
    C.require_valid(cfg)
    print(SMOKE_BANNER)
    repro.set_seed(cfg.train.seed)
    tok = load_tokenizer(cfg)
    tr, va, info = load_data(cfg, tok)
    rd = Path(run_dir) if run_dir else cfg.run_dir()
    if rd.exists():
        shutil.rmtree(rd)
    stop_at = max(cfg.train.save_every, (cfg.train.max_steps * 2 // 3) // cfg.train.save_every * cfg.train.save_every)

    def fresh():
        m = attach_lora(build_tiny_model(cfg), cfg)
        return Trainer(cfg, m, tok, tr, va, info, run_dir=rd, device="cpu", precision="fp32", echo=echo)

    a = fresh()
    ra = a.fit(stop_at_step=stop_at)
    b = fresh()
    ck = K.resolve_resume(rd, "latest")
    meta = b.resume(ck)
    rb = b.fit()
    result = {"banner": SMOKE_BANNER, "stopped_at": ra["step"], "resumed_from_step": meta["step"], "final_step": rb["step"], "final_val_loss": rb["last"].get("val_loss"), "lora_B_norm": rb["lora_B_norm"],
              "first_steps_tokens_per_sec": rb["first_steps_tokens_per_sec"],
              "trainable_params": sum(p.numel() for p in b.trainable), "checkpoints": [p.name for _, p in K.list_checkpoints(rd)],
              "dataset": {k: info[k] for k in ("version", "train_samples_used", "validation_samples_used", "loader")}}
    ok = (ra["step"] == stop_at and meta["step"] == stop_at and rb["step"] == b.total_steps and rb["last"].get("val_loss") is not None
          and math.isfinite(rb["last"]["val_loss"]) and (rd / "best.json").exists() and (rd / "latest.json").exists() and rb["adapter_updated"])
    result["ok"] = bool(ok)
    print(json.dumps(result, indent=1, default=str))
    print("SMOKE TEST " + ("PASSED" if ok else "FAILED") + " - " + SMOKE_BANNER)
    return result


def warm_start(model, adapter_dir, expected_sha256) -> dict:
    """Initialise the freshly attached LoRA from an existing adapter (new optimizer, scheduler and step counter: a new fine-tuning stage,
    not a resume). The adapter file must match the trusted sha256 given on the command line."""
    if not expected_sha256:
        raise ValueError("--init-adapter requires --init-adapter-sha256 (trusted hash of adapter_model.safetensors)")
    f = Path(adapter_dir) / "adapter_model.safetensors"
    got = repro.sha256_bytes(f.read_bytes())
    if got != expected_sha256:
        raise K.CheckpointError(f"init adapter {f} sha256 {got[:12]}... != trusted {expected_sha256[:12]}...; refusing to train")
    K.load_adapter_into(model, Path(adapter_dir))
    print(f"warm start: LoRA initialised from {adapter_dir} (sha256 {got[:12]}...)", flush=True)
    return {"path": str(adapter_dir), "sha256": got}


def cmd_production(cfg, args) -> int:
    check_production_unlock(cfg, args.confirm_production)        # raises ProductionLocked; Phase 3/4 never gets past this line
    C.require_valid(cfg)
    require_primary_loader(cfg)
    rd = cfg.run_dir()
    if not args.resume:
        K.ensure_fresh_run_dir(rd)                               # a fresh run never shares a directory with existing checkpoints
    repro.set_seed(cfg.train.seed)
    tok = load_tokenizer(cfg)
    tk_check = check_tokenizer(cfg, tok)                         # raises on mismatch with the verified Qwen3-8B tokenizer/chat template
    tr, va, info = load_data(cfg, tok)
    model = attach_lora(load_production_model(cfg, unlocked=True), cfg)
    init = warm_start(model, args.init_adapter, args.init_adapter_sha256) if args.init_adapter else None
    prec = resolve_precision(cfg)
    trainer = Trainer(cfg, model, tok, tr, va, info, device="cuda:0", precision=prec)
    if init:
        trainer.identity["init_adapter_sha256"] = init["sha256"]        # part of the identity, so a resume must use the same starting adapter
    rd.mkdir(parents=True, exist_ok=True)
    repro.write_json_atomic(rd / "reproducibility.json", repro.snapshot(cfg, info, prec, extra={"tokenizer_check": tk_check, "data_loader": D.SOURCE, "init_adapter": init}))
    if args.resume:
        trainer.resume(K.resolve_resume(rd, args.resume), force=args.force_resume)
    res = trainer.fit()
    print(json.dumps(res, indent=1, default=str))
    if not res.get("adapter_updated"):
        raise NonFiniteError("LoRA adapter unchanged after training; not reporting success")
    return EXIT["ok"]


def build_parser():
    ap = argparse.ArgumentParser(description="Qwen3-8B QLoRA SFT trainer (production locked until Phase 5)")
    ap.add_argument("--config", default=str(WORKSPACE / "configs" / "qlora_sft.yaml"))
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="override a config value, e.g. train.learning_rate=2e-4")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--validate-config", action="store_true")
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--smoke-test", action="store_true")
    ap.add_argument("--resume", nargs="?", const="latest", default=None, metavar="PATH|latest|best")
    ap.add_argument("--force-resume", action="store_true", help="resume despite config/dataset identity mismatch")
    ap.add_argument("--confirm-production", action="store_true", help="part of the Phase-5 unlock (see module docstring)")
    ap.add_argument("--init-adapter", default=None, metavar="DIR", help="start a new fine-tuning stage from this adapter's weights (fresh optimizer/schedule)")
    ap.add_argument("--init-adapter-sha256", default=None, metavar="HEX", help="trusted sha256 of DIR/adapter_model.safetensors")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg_path = args.config
        if args.smoke_test and "--config" not in (argv if argv is not None else sys.argv):
            cfg_path = str(WORKSPACE / "configs" / "smoke_test.yaml")
        if args.resume and (args.validate_config or args.dry_run or args.smoke_test):
            raise C.ConfigError("--resume applies only to a real (Phase 5) run; --validate-config/--dry-run/--smoke-test never resume a checkpoint "
                                "(the smoke test exercises save/interrupt/resume on its own)")
        cfg = C.load_config(cfg_path, args.set)
        if args.validate_config:
            return cmd_validate_config(cfg)
        if args.dry_run:
            cmd_dry_run(cfg)
            return EXIT["ok"]
        if args.smoke_test:
            return EXIT["ok"] if run_smoke(cfg)["ok"] else EXIT["error"]
        return cmd_production(cfg, args)
    except ProductionLocked as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return EXIT["locked"]
    except C.ConfigError as e:
        print(f"CONFIG ERROR: {e}", file=sys.stderr)
        return EXIT["config"]
    except K.CheckpointError as e:
        print(f"CHECKPOINT ERROR: {e}", file=sys.stderr)
        return EXIT["checkpoint"]
    except NonFiniteError as e:
        print(f"TRAINING ABORTED: {e}", file=sys.stderr)
        return EXIT["error"]
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return EXIT["error"]


if __name__ == "__main__":
    sys.exit(main())
