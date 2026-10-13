"""Resume-compatibility LOAD test (no training step). Runs the ORIGINAL training code (extracted Kaggle package in ./code)
against a COPY of checkpoint-1596 in ./run. trainer_state.pt is read only via the original K.load_checkpoint (torch.load weights_only=True).
The Qwen3-8B base weights are not available locally, so the model skeleton is built on the `meta` device (shapes only, no weights)."""
import json, math, os, random, sys, traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
CODE, RUN = HERE / "code", HERE / "run"
CK = RUN / "checkpoint-1596"
HF_META = Path(r"E:\Nexo\qwen_jarvis_phase3_4\_hf_meta")  # read-only: Qwen3-8B config + tokenizer files (no weights)
sys.path.insert(0, str(CODE))
os.chdir(CODE)

import numpy as np
import torch
from pipeline.train import checkpoint as K, config as C, train as T
from pipeline.train.callbacks import EarlyStopping

R = {"env": {"python": sys.version.split()[0], "torch": torch.__version__, "cuda_available": torch.cuda.is_available()}, "checks": {}}
def rec(name, ok, **kw):
    R["checks"][name] = {"status": "PASS" if ok is True else ("FAIL" if ok is False else ok), **kw}
    print(f"[{R['checks'][name]['status']}] {name} {json.dumps(kw, default=str)[:300]}")

try:
    import transformers, peft
    R["env"].update(transformers=transformers.__version__, peft=peft.__version__)
    meta = json.loads((CK / "checkpoint_meta.json").read_text(encoding="utf-8"))

    # 1. expected identity from the original config + packaged dataset (original functions)
    cfg = C.load_config(CODE / "configs" / "qlora_sft.yaml")
    tp, vp = cfg.data_path("train"), cfg.data_path("validation")
    from pipeline.train import data_adapter as D
    info = {"version": cfg.data.dataset_version, "train_sha256": D.file_sha256(tp), "validation_sha256": D.file_sha256(vp)}
    info.update(T.check_manifest(cfg, tp, vp, info))
    expected = {"model_id": cfg.model.model_id, "dataset_version": info["version"],
                "dataset_hash": info["train_sha256"][:16] + ":" + info["validation_sha256"][:16],
                "dataset_manifest_sha256": info.get("manifest_sha256"), "config_hash": cfg.snapshot_hash()}
    rec("identity_reproduced_from_code", expected == meta["identity"], expected=expected, recorded=meta["identity"])

    # 2. original loader: integrity + compatibility + torch.load(weights_only=True)
    ck = K.load_checkpoint(CK, expected=expected, force=False)
    st = ck["state"]
    rec("load_checkpoint_weights_only", True, mismatches=ck["mismatches"], keys=sorted(st))

    # 3. total_steps / warmup derivation (needs tokenizer: local _hf_meta files, offline)
    cfg.model.tokenizer_path = str(HF_META)
    tok = T.load_tokenizer(cfg)
    tk = T.check_tokenizer(cfg, tok)
    rec("tokenizer_fingerprint", bool(tk.get("match")), reference=tk.get("reference_source"))
    tr = D.load_examples(tp, tok, cfg.data.max_seq_len, cfg.data.train_on, cfg.data.max_train_examples, cfg.data.validate, False)
    spe = T.steps_per_epoch(len(tr), cfg.train.per_device_batch, cfg.train.grad_accum)
    total = min(cfg.train.max_steps, cfg.train.epochs * spe) if cfg.train.max_steps > 0 else cfg.train.epochs * spe
    warm = math.ceil(cfg.train.warmup_ratio * total)
    rec("total_steps_rederived", total == st["loop"]["total_steps"] == 1596, train_samples=len(tr), steps_per_epoch=spe, total=total, warmup=warm)

    # 4. model skeleton on meta + LoRA from the checkpoint's adapter_config + original load_adapter_into
    from transformers import AutoConfig, AutoModelForCausalLM
    from peft import LoraConfig, get_peft_model
    mcfg = AutoConfig.from_pretrained(str(HF_META))
    with torch.device("meta"):
        base = AutoModelForCausalLM.from_config(mcfg)
    model = T.attach_lora(base, cfg)  # original function: same LoraConfig + trainable-param order as the real run
    K.load_adapter_into(model, CK / "adapter")
    from peft import load_peft_weights, get_peft_model_state_dict
    w = load_peft_weights(str(CK / "adapter"), device="cpu")
    exp = {k: tuple(v.shape) for k, v in get_peft_model_state_dict(model).items()}
    got = {k: tuple(v.shape) for k, v in w.items()}
    finite = all(torch.isfinite(v.float()).all().item() for v in w.values())
    bnorm = math.sqrt(sum(float(v.float().pow(2).sum()) for k, v in w.items() if "lora_B" in k))
    rec("adapter_fits_qwen3_8b_architecture", exp == got and finite, tensors=len(got), finite=finite,
        lora_B_norm=bnorm, meta_lora_B_norm=meta["metrics"]["lora_B_norm"], missing=sorted(set(exp) - set(got))[:3], unexpected=sorted(set(got) - set(exp))[:3])
    rec("adapter_lora_B_norm_matches_meta", abs(bnorm - meta["metrics"]["lora_B_norm"]) < 1e-3 * meta["metrics"]["lora_B_norm"], computed=bnorm)

    # 5. optimizer: same param order/shapes as Trainer (trainable params of the PEFT model), original AdamW args
    shapes = [tuple(p.shape) for p in model.parameters() if p.requires_grad]
    params = [torch.zeros(s, requires_grad=True) for s in shapes]
    t = cfg.train
    opt = torch.optim.AdamW(params, lr=t.learning_rate, betas=(t.adam_beta1, t.adam_beta2), eps=t.adam_eps, weight_decay=t.weight_decay)
    from transformers import get_scheduler
    sched = get_scheduler(t.lr_scheduler, opt, num_warmup_steps=warm, num_training_steps=total)  # built in Trainer.__init__, before resume()
    opt.load_state_dict(st["optimizer"])
    os_ = opt.state_dict()["state"]
    shape_ok = len(os_) == len(params) and all(tuple(os_[i]["exp_avg"].shape) == shapes[i] == tuple(os_[i]["exp_avg_sq"].shape) for i in range(len(params)))
    steps = {float(os_[i]["step"]) for i in os_}
    pg = opt.param_groups[0]
    nonfinite = sum(int(not torch.isfinite(os_[i][k]).all()) for i in os_ for k in ("exp_avg", "exp_avg_sq"))
    rec("optimizer_state_restored", shape_ok and nonfinite == 0, n_params=len(params), adam_step_values=sorted(steps), lr=pg["lr"], initial_lr=pg.get("initial_lr"),
        betas=pg["betas"], eps=pg["eps"], weight_decay=pg["weight_decay"], nonfinite_tensors=nonfinite)
    rec("optimizer_step_count_vs_global_step", steps == {1595.0} or steps == {1596.0}, note="1 skipped step expected => adam step 1595",
        adam_step=sorted(steps))

    # 6. scheduler: original get_scheduler + load_state_dict
    sched.load_state_dict(st["scheduler"])
    lr_now = sched.get_last_lr()[0]
    prog = (sched.last_epoch - warm) / max(1, total - warm)
    lr_formula = t.learning_rate * 0.5 * (1 + math.cos(math.pi * prog))
    rec("scheduler_state_restored", sched.last_epoch == 1595 and math.isclose(lr_now, lr_formula, rel_tol=1e-6, abs_tol=1e-15) and math.isclose(lr_now, pg["lr"], rel_tol=1e-9),
        last_epoch=sched.last_epoch, last_lr=lr_now, cosine_formula_lr=lr_formula, optimizer_lr=pg["lr"])

    # 7. grad scaler: original uses GradScaler("cuda") (needs CUDA); here substituted with the CPU scaler to test the state format only
    sc_st = st["scaler"]
    if torch.cuda.is_available():
        sc = torch.amp.GradScaler("cuda")
    else:
        sc = torch.amp.GradScaler("cpu")
    sc.load_state_dict(sc_st)
    rec("grad_scaler_state_restored", "PARTIAL" if not torch.cuda.is_available() else sc.get_scale() == sc_st["scale"],
        scale=sc.get_scale(), saved=sc_st, device="cpu substitute" if not torch.cuda.is_available() else "cuda")

    # 8. RNG: original restore_rng, then compare live state with the saved state
    K.restore_rng(st["rng"])
    py = random.getstate(); npst = np.random.get_state()
    rng_ok = (list(py[1]) == list(st["rng"]["python"][1]) and npst[1].tolist() == st["rng"]["numpy"][1]
              and torch.equal(torch.get_rng_state(), st["rng"]["torch_cpu"]))
    rec("rng_python_numpy_torchcpu_restored", rng_ok)
    rec("rng_torch_cuda_restored", "UNVERIFIED (no CUDA; original code skips it when CUDA is absent)" if not torch.cuda.is_available() else True,
        saved_devices=len(st["rng"].get("torch_cuda", [])))

    # 9. loop + early stopping (original EarlyStopping.load)
    loop = st["loop"]
    es = EarlyStopping(t.early_stopping_patience, t.early_stopping_min_delta); es.load(loop.get("stopper", {}))
    want = {"step": 1596, "epoch": meta["epoch"], "tokens_seen": 3731843, "total_steps": 1596, "skipped_steps": 1, "consecutive_skipped": 0}
    rec("loop_state_restored", all(loop.get(k) == v for k, v in want.items()) and es.best == 0.6599428926175388 and es.best_step == 1300,
        loop={k: loop.get(k) for k in want}, stopper=es.state(), meta_step=meta["step"])
    rec("resume_would_continue", "INFO", note=f"step {loop['step']} >= total_steps {loop['total_steps']}: Trainer.fit() would perform 0 further steps from checkpoint-1596")
except Exception as e:
    R["fatal"] = f"{type(e).__name__}: {e}"
    R["traceback"] = traceback.format_exc()
    print("FATAL", R["fatal"])

(HERE / "load_test_results.json").write_text(json.dumps(R, indent=1, default=str), encoding="utf-8")
