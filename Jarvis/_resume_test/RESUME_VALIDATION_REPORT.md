# JARVIS Phase 5 — Checkpoint Resume Validation Report

Generated: 2026-10-11. Test directory: `E:\Nexo\Jarvis\_resume_test\`.

## FINAL STATUS: **C. PARTIALLY VERIFIED**

The original code loaded and restored the training state of checkpoint-1596 (a copy):
- optimizer
- scheduler
- CPU RNG
- loop state
- early-stopping state
- the adapter's structural fit to Qwen3-8B

Three components remain untested here:
1. The real CUDA grad-scaler and CUDA RNG restore. This machine's torch has no CUDA.
2. Loading the adapter into the real quantized base model. The Qwen3-8B weights are not available locally.
3. Any training step. None was executed, and Phase D was not approved.

**No training step was run. Resumption is NOT demonstrated.**

## 1. Environment and dependency availability

| Item | Recorded on Kaggle (meta) | Available locally | Status |
|---|---|---|---|
| Training code | Kaggle package `qwen_jarvis_kaggle_package.zip` | `qwen_jarvis_phase3_4/kaggle/dist/` (sha256 944eab01…ed1ff3, matches `package_sha256.txt`). `pipeline/` is identical to the working copy (`diff -rq` empty). | AVAILABLE |
| Config | `configs/qlora_sft.yaml`, config_hash 9312597956f22164 | Packaged config reproduces **9312597956f22164** | AVAILABLE, MATCH |
| Dataset | qwen_jarvis_dataset_v2_final, manifest sha 1cab001c… | Packaged `dataset/v2_final`; dataset_hash and manifest sha reproduce exactly | AVAILABLE, MATCH |
| Tokenizer | Qwen/Qwen3-8B | `_hf_meta` + HF cache (tokenizer/config only); fingerprint check PASS | AVAILABLE |
| Base model weights | Qwen/Qwen3-8B (~16 GB) | **Not present.** HF cache holds 16 MB (config + tokenizer only) | **MISSING**. Not downloaded, as instructed. |
| GPU | Tesla T4 ×2 (the saved `torch_cuda` RNG has 2 device states) | RTX 3050 Laptop, **4 GB**, below the ~12.5 GiB QLoRA estimate in `docs/MEMORY_PLAN.md` | **INSUFFICIENT** |
| torch | 2.11.0+cu128 | 2.14.1**+cpu** (`.venv`), CUDA unavailable | VERSION + BUILD MISMATCH |
| transformers / peft / accelerate | 5.16.1 / 0.20.0 / 1.14.0 | 5.19.0 / 0.21.2 / 1.15.0 | MINOR MISMATCH |
| bitsandbytes | 0.50.2 | **Not installed** (CUDA-only) | MISSING |

Nothing was installed, uninstalled, upgraded or downloaded.

## 2. Files and checkpoints tested

- **The original checkpoint was never used directly.** `checkpoint-1596` was copied to `_resume_test/run/checkpoint-1596/`. The copy's hashes equal the originals before and after the test (`trainer_state.pt` 29c19b9c…178b, adapter 162b97f6…4dc8, meta a8bef0e2…7478).
- The Kaggle package was extracted to `_resume_test/code/`. Its 64 entries were checked for path traversal first, and none was found.
- The test script `_resume_test/load_test.py` was run with the original venv, `-B` (no bytecode written), and HF offline mode on. It writes:
  - logs: `load_test.log`
  - results: `load_test_results.json`

## 3. Loading implementation inspected (original code)

- `pipeline/train/checkpoint.py`:
  - `save_checkpoint` writes `adapter/` (PEFT `save_pretrained`), `trainer_state.pt` = `{optimizer, scheduler, scaler, rng, loop}` (`torch.save`) and `checkpoint_meta.json`.
  - `latest.json` / `best.json`.
  - `resolve_resume("latest"|"best"|path)`.
  - `verify_checkpoint`: schema 1, required fields, every file's size and SHA-256.
  - `check_compatibility`: identity equality unless `--force-resume`.
  - `load_checkpoint`: `torch.load(..., map_location="cpu", weights_only=True)`, the **safe loader**. No unrestricted pickle.
  - `load_adapter_into`: `peft.load_peft_weights` + `set_peft_model_state_dict`; unexpected keys raise.
  - `restore_rng`: Python `random.setstate`, `np.random.set_state`, `torch.set_rng_state`, and `torch.cuda.set_rng_state_all` **only if CUDA is available**.
- `pipeline/train/train.py`:
  - `Trainer.__init__` builds AdamW (LoRA params), `get_scheduler(cosine, warmup=ceil(0.03·total))`, `GradScaler("cuda")` (fp16 + CUDA only) and `EarlyStopping`.
  - `Trainer.resume` runs `load_checkpoint`, then `load_adapter_into`, then `opt.load_state_dict`, `sched.load_state_dict`, `scaler.load_state_dict` (when both exist), `restore_rng`, and restores step / tokens_seen / skipped counters / `stopper.load`.
  - Entry point: `python -m pipeline.train.train --config configs/qlora_sft.yaml --confirm-production --resume [latest|best|PATH]` (via `cmd_production`).
- **Unsafe deserialization required? No.** The original loader already uses `weights_only=True`, and it succeeded on torch 2.14.1.

## 4. Tests executed (checkpoint-1596 copy; results from `load_test_results.json`)

| # | Check (original function used) | Result | Evidence |
|---|---|---|---|
| 1 | Identity rebuilt from packaged config + dataset (`load_config`, `snapshot_hash`, `check_manifest`) equals the meta identity | PASS | config_hash 9312597956f22164; dataset_hash 8b066c83b8adaac7:add305738bf469c6; manifest 1cab001c…; model Qwen/Qwen3-8B |
| 2 | `K.load_checkpoint(copy, expected=identity)`: integrity + compatibility + `torch.load(weights_only=True)` | PASS | 0 mismatches; keys loop/optimizer/rng/scaler/scheduler |
| 3 | Tokenizer fingerprint (`check_tokenizer`) | PASS | reference `configs/tokenizer_fingerprint.json` |
| 4 | total_steps re-derived (`load_examples`, `steps_per_epoch`) | PASS | 12777 samples, 798 steps/epoch × 2 epochs = 1596; warmup 48 |
| 5 | Adapter fits the Qwen3-8B architecture: `attach_lora` on a `meta`-device skeleton + original `load_adapter_into` | PASS (structural) | 504 tensors; 0 missing / 0 unexpected keys; identical shapes; all finite. The weights are not actually applied (meta device). |
| 6 | Adapter lora_B norm vs meta | PASS | 14.370671255698555 vs 14.370671164968513 (difference 9e-8; meta was computed in a different precision) |
| 7 | Optimizer `load_state_dict` with the real trainable-param order and shapes | PASS | 504 params; exp_avg / exp_avg_sq shapes match each param; 0 non-finite; betas (0.9, 0.999), eps 1e-8, wd 0.01, initial_lr 1e-4, lr 1.0296691294042759e-10 |
| 8 | Adam step count vs global step | PASS | Adam step 1595 = 1596 − 1 skipped step |
| 9 | Scheduler `load_state_dict` (built before resume, as `Trainer` does) | PASS | last_epoch 1595; last_lr 1.0296691294042759e-10 = cosine formula = optimizer lr |
| 10 | Grad-scaler state | PARTIAL | State format loads; scale 32768.0, growth_tracker 1030. Tested with the **CPU** `GradScaler`; the original `GradScaler("cuda")` needs CUDA. |
| 11 | RNG restore: Python, NumPy, torch CPU (`restore_rng`, then compared to saved) | PASS | Exact equality |
| 12 | RNG restore: torch CUDA | UNVERIFIED | 2 saved device states; the original code skips them without CUDA |
| 13 | Loop + early stopping (`EarlyStopping.load`) | PASS | step 1596, epoch 1, tokens_seen 3731843, total_steps 1596, skipped 1, consecutive 0; stopper best 0.6599428926175388 @ 1300, bad 3. All match meta and historical records. |
| — | Restored global step = 1596 | PASS | |

**Run history (reported honestly):** the first run hit two harness errors, both mine, not the checkpoint's. Logs are kept as `load_test_run1_order_bug.*`.
- (a) I first built LoRA from the saved `adapter_config.json`, which has `inference_mode` on, so there were no trainable params. Fixed by using the original `attach_lora`.
- (b) After that fix, the scheduler check FAILED with optimizer lr 0.0, because I built the scheduler *after* `opt.load_state_dict` and its constructor reset the lr. `Trainer` builds it first. With the order fixed to match `Trainer.__init__` + `resume`, it passes.

No checkpoint data was altered to make a check pass.

## 5. Tests NOT executed, and why

- **Loading the adapter into real Qwen3-8B weights (inference load):** base weights missing (~16 GB), no CUDA, and bitsandbytes missing.
- **Full `Trainer.resume` through `cmd_production`:** it needs the 4-bit model on `cuda:0`.
- **CUDA grad scaler and CUDA RNG restore:** CPU-only torch.
- **One-step resume (Phase D):** not approved, and not feasible on this machine (see §7).

## 6. Answers

- Model weights loaded successfully? **Adapter tensors: yes** (safetensors, structure verified against the Qwen3-8B architecture). **Into the real base model: UNVERIFIED.**
- Training state loaded successfully? **Yes**, via the original `load_checkpoint` (`weights_only=True`) on torch 2.14.1.
- Optimizer: PASS. Scheduler: PASS. Scaler: PARTIAL (CPU substitute). RNG: PASS for CPU / Python / NumPy, UNVERIFIED for CUDA.
- Real resume step tested? **No.**

## 7. Phase D: one-step resume. NOT executed; awaiting approval

Important: **checkpoint-1596 cannot be used for a resume step.** Its loop step is 1596 = total_steps, so `Trainer.fit()` would do 0 steps. Raising `max_steps` changes config_hash (`max_steps` is not in `RESUME_IGNORE`), which would force `--force-resume`. Use **checkpoint-1500** (or 1300) instead: those are mid-run and identity-compatible.

**Required resources:**
- A CUDA GPU with ≥ ~13 GiB free (Kaggle T4 16 GB, as in the original run).
- CUDA torch + bitsandbytes 0.50.x.
- The Qwen3-8B base weights (~16 GB download from the Hub).
- The packaged dataset (already in `code/`).

This local machine (4 GB RTX 3050, CPU torch) **cannot** run it. Realistic place: a Kaggle session.

**What would run** (proposal; nothing executed): a small harness calling the original functions, in this order:
- `load_config`
- `load_tokenizer` / `check_tokenizer`
- `load_data`
- `attach_lora(load_production_model(cfg, unlocked=True), cfg)`
- `Trainer(..., run_dir=<isolated test dir>, device="cuda:0", precision=resolve_precision(cfg))`
- `trainer.resume(<COPY of checkpoint-1500>)`
- `trainer.fit(stop_at_step=1501)`

Then record:
- step before and after
- lr before and after
- scheduler last_epoch
- scaler scale
- loss / grad-norm finiteness
- skipped flag

**Side effects to expect:**
- On stopping, `fit` runs a full validation pass (~36k tokens, minutes on a T4).
- It saves `checkpoint-1501` + `latest.json` into the isolated run dir.
- `prune(keep_last=2)` acts **only inside that isolated dir**, which must contain only the copied checkpoint.

**Isolation:**
- Copy checkpoint-1500 into a fresh test run dir.
- Never point `run_dir` at `E:\Nexo\Jarvis`.
- Hash the originals before and after.

**Determinism:** exact continuation is not claimed. Bit-exact equality with the original step-1501 trajectory cannot be checked, because no step-1501 artifacts exist.

## 8. Final checkpoint integrity (Phase E)

- `sha256sum -c CHECKPOINT_SHA256SUMS.txt` (Git Bash coreutils): **35/35 OK**.
- Pre-test vs post-test snapshot of **every file in E:\Nexo\Jarvis outside `_resume_test`** (55 files, SHA-256 + size; `pre_test_*.txt` vs `post_test_*.txt`): **byte-identical**. That covers all checkpoints, `best.json`, `latest.json`, `recovery_manifest.json`, all reports, `_finalization_downloads/primary/training_results.json` and the backups.
- Files modified outside `E:\Nexo\Jarvis` since the pre-test snapshot (`qwen_jarvis_phase3_4`, HF cache): **0**. No `__pycache__` was written to the code copy.
- No credentials were read, printed or logged. No uploads.

## 9. Limitations and next actions

1. Get a CUDA environment matching the recorded stack (Kaggle T4: torch 2.11.0+cu128, transformers 5.16.1, peft 0.20.0, bitsandbytes 0.50.2) plus the base weights. Then approve Phase D on a **copy of checkpoint-1500**.
2. Local versions differ (torch 2.14.1 CPU, transformers 5.19.0, peft 0.21.2). The state *formats* load fine with them, but training numerics under those versions are untested.
3. The adapter was verified only structurally (meta device). An inference load on real weights is still pending.
4. `_resume_test/` holds about 525 MB: the checkpoint copy plus the extracted package. Delete it when no longer needed. It contains no original data.
