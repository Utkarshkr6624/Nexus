# Qwen3-8B JARVIS — Checkpoint Recovery Finalization Report

Generated: 2026-10-10. Workspace: `E:\Nexo\Jarvis`.
Nothing was trained, resumed, evaluated, run for inference, or integrated. No files outside `E:\Nexo\Jarvis` were modified.
The training code in `E:\Nexo\qwen_jarvis_phase3_4` was **read only**, to find the file schemas and how they are written. No Kaggle credentials were printed or logged.

## 1. Executive summary

- **All 7 checkpoints are complete**: 800, 1100, 1200, 1300, 1400, 1500 and 1596 each contain exactly the 5 required files and nothing else.
- **All 28 payload files PASS.** Each one's size and SHA-256 match its own `checkpoint_meta.json`. These were re-verified after cleanup.
- **The 7 `checkpoint_meta.json` files are still UNVERIFIED as authentic.** No external trusted hash exists for them.
- **All 7 `trainer_state.pt` files were inspected statically** (zip listing plus `pickletools` opcode scan; nothing was deserialized or executed).
  - Every file contains only 4 allow-listed pickle globals.
  - Every file contains all the state the writer saves: optimizer, scheduler, grad scaler, RNG, and loop state.
- **The two run segments are continuous.** Strong evidence shows 800–1200 and 1300–1596 belong to one continuous training run that was resumed from checkpoint-1200 (§4.4).
- **`best.json` and `latest.json` were corrected** after their schema and meaning were established from the code that writes them, and the originals were backed up byte-for-byte:
  - `best.json` changed from checkpoint-800 to checkpoint-1300.
  - `latest.json` changed from checkpoint-1200 to checkpoint-1596.
- **`training_results.json` was downloaded and matches all historical records.** It has no hash of its own to check against.
- **21 verified duplicate files were deleted, freeing 2271797114 bytes (~2.12 GiB).** No unique artifact was deleted.
- **Exact training resumption has NOT been demonstrated.** It is untested (§12).

## 2. Final checkpoint inventory

Each checkpoint contains `checkpoint_meta.json`, `trainer_state.pt` (349622034 bytes), `adapter/adapter_config.json` (1146 bytes), `adapter/adapter_model.safetensors` (174655536 bytes) and `adapter/README.md` (5180 bytes).
`checkpoint_meta.json` sizes: 800 = 1496; 1100, 1200, 1500 and 1596 = 1499; 1300 and 1400 = 1498.
No unexpected files exist inside any checkpoint folder.

Other workspace files:
- Reports: `RECOVERY_INVENTORY.txt`, `PHASE_B_HASH_VERIFICATION.md`, `PHASE_C_RECOVERY_REPORT.md`, `FINAL_CLEANUP_PLAN.md`, `FINALIZATION_REPORT.md`, `CHECKPOINT_SHA256SUMS.txt`
- Manifest: `recovery_manifest.json`
- Pointer files: `best.json`, `latest.json`
- `_finalization_backups/` (original pointer files)
- `_finalization_downloads/` (`training_results.json`, plus evidence copies of the remote checkpoint-1200 metadata and the older pointer files)
- `_phase_c_downloads/` (5 download logs only)

## 3. Integrity verification (final, after cleanup)

| Checkpoint | Meta | trainer_state.pt | adapter_config.json | adapter_model.safetensors | README.md | Step / config_hash / dataset in meta |
|---|---|---|---|---|---|---|
| 800 | UNVERIFIED (self-describing) | PASS | PASS | PASS | PASS | 800 / 9312597956f22164 / qwen_jarvis_dataset_v2_final |
| 1100 | UNVERIFIED | PASS | PASS | PASS | PASS | 1100 / same / same |
| 1200 | UNVERIFIED | PASS | PASS | PASS | PASS | 1200 / same / same |
| 1300 | UNVERIFIED | PASS | PASS | PASS | PASS | 1300 / same / same |
| 1400 | UNVERIFIED | PASS | PASS | PASS | PASS | 1400 / same / same |
| 1500 | UNVERIFIED | PASS | PASS | PASS | PASS | 1500 / same / same |
| 1596 | UNVERIFIED | PASS | PASS | PASS | PASS | 1596 / same / same |

Full hashes for all 35 files are in `CHECKPOINT_SHA256SUMS.txt` (also checked with `sha256sum -c`).

`model_id` is `Qwen/Qwen3-8B` in all 7 metadata files. The recorded library versions are identical in all 7: torch 2.11.0+cu128, transformers 5.16.1, peft 0.20.0, accelerate 1.14.0, bitsandbytes 0.50.2, datasets 4.8.5, tokenizers 0.23.1, numpy 2.1.3, yaml 6.0.3. TRL was not importable, so it was not used.

The adapter config exists in two byte-identical groups: 800–1200 and 1300–1596. The only difference between them is the order of the `target_modules` list. The set is the same 7 modules, and r=16, alpha=32, dropout=0.05, LORA, CAUSAL_LM, Qwen/Qwen3-8B are identical. Each checkpoint's config matches its own metadata.

## 4. Training-state inspection and compatibility

### 4.1 Method (safe)
- Each `trainer_state.pt` was opened with `zipfile`, and `trainer_state/data.pkl` was disassembled with `pickletools.genops`. This reads opcodes only: no unpickling, no `torch.load`, nothing executed.
- Every file has 1521 zip entries: 1515 tensor blobs plus `data.pkl`, `.format_version`, `.storage_alignment`, `byteorder`, `version` and `.data/serialization_id`.
- Pickle globals referenced, identical in all 7: `collections.OrderedDict`, `torch.FloatStorage`, `torch.ByteStorage`, `torch._utils._rebuild_tensor_v2`. **None fall outside the allow-list.** That is consistent with the project's own loader, which uses `torch.load(..., weights_only=True)`.

### 4.2 Trainer implementation (from `qwen_jarvis_phase3_4/pipeline/train/{train.py,checkpoint.py}`, config `configs/qlora_sft.yaml`)
- A custom PyTorch training loop. It is not the HF `Trainer` and not TRL.
- Optimizer: `torch.optim.AdamW`, applied to the LoRA parameters only.
- Scheduler: `transformers.get_scheduler("cosine")` with warmup_ratio 0.03.
- `torch.amp.GradScaler` is active (fp16 on the T4). The saved scale is 32768.0.
- Config: lr 1e-4, per_device_batch 4 × grad_accum 4, max_seq_len 1024, seed 42, eval_every 100, save_every 100, keep_last 2, early_stopping_patience 4.
- Tokenizer: checked at run time against `configs/tokenizer_fingerprint.json` and the `_hf_meta` reference. No tokenizer files are stored in the checkpoints.
- **All state is embedded in `trainer_state.pt`**:
  - `optimizer` (`state` and `param_groups`)
  - `scheduler`
  - `scaler`
  - `rng` (`python`, `numpy`, `torch_cpu` and `torch_cuda`)
  - `loop` (step, epoch, tokens_seen, stopper, total_steps, skipped_steps, consecutive_skipped)
- No separate RNG, optimizer or scheduler files are expected. The adapter is loaded separately from `adapter/`.

### 4.3 Static values read from the opcode stream (not deserialized)

| Ckpt | sched last_epoch / _step_count | lr | loop tokens_seen | stopper best / best_step / bad | total_steps | skipped |
|---|---|---|---|---|---|---|
| 800 | 799 / 800 | 5.2333e-05 | 1870131 | 0.6658911464513418 / 800 / 0 | 1596 | 1 |
| 1100 | 1099 / 1100 | 2.3349e-05 | 2592628 | 0.6658911464513418 / 800 / 3 | 1596 | 1 |
| 1200 | 1199 / 1200 | 1.5369e-05 | 2820859 | 0.6658911464513418 / 800 / 4 | 1596 | 1 |
| 1300 | 1299 / 1300 | 8.8109e-06 | 3045120 | 0.6599428926175388 / 1300 / 0 | 1596 | 1 |
| 1400 | 1399 / 1400 | 3.9431e-06 | 3273147 | 0.6599428926175388 / 1300 / 1 | 1596 | 1 |
| 1500 | 1499 / 1500 | 9.6569e-07 | 3495243 | 0.6599428926175388 / 1300 / 2 | 1596 | 1 |
| 1596 | 1595 / 1596 | 1.0297e-10 | 3731843 | 0.6599428926175388 / 1300 / 3 | 1596 | 1 |

Optimizer tensors were saved on `cuda:0`. The project loader passes `map_location="cpu"`.
The tokens_seen value at step 1596 (3731843) equals the historical record and `training_results.json`.

### 4.4 Run continuity (800–1200 vs 1300–1596)
The evidence for one continuous run resumed from checkpoint-1200:
1. The primary dataset (the final run) contains a `checkpoint-1200/checkpoint_meta.json` that is **byte-identical** to the local one, which came from the older dataset. Both have SHA-256 `edc1737c…34fbd`. That checkpoint's meta also lists the same trainer_state and adapter hashes.
2. The early-stopping state carries over. At 1200 it was best = 0.66589 @ 800, bad = 4 (one short of patience 4 triggering on the next non-improvement). At 1300 a new best was recorded and bad reset to 0, which is exactly what restoring the 1200 stopper and then improving would produce.
3. Every 100-step interval is consistent: scheduler step counts, the cosine LR decay, and tokens_seen increments (about 224–231k per 100 steps).
4. The identity in all 7 metas is the same config_hash, dataset hash and manifest, and the library versions are identical.
5. Saved times show a gap: 10:35Z at 1200, then 15:02Z at 1300. That fits a new Kaggle session doing `--resume`. The different `target_modules` order in 1300+ fits a new process re-serializing a set.

**Conclusion: continuous — strongly supported.** It is not cryptographically proven, because the RNG state and the optimizer tensors were not compared across the resume boundary.

### 4.5 Readiness per checkpoint

| Ckpt | A. Inference (adapter load) | B. Resume training | C. Audit / reproduce |
|---|---|---|---|
| 800–1596 (all 7) | PASS for file completeness and integrity (adapter + config + base `Qwen/Qwen3-8B` reference). Actual loading: UNVERIFIED, not run. | UNVERIFIED. All state the writer saves is present and hash-valid. A real resume additionally needs: the same code (`pipeline/train`), config `qlora_sft.yaml` (config_hash 9312597956f22164), the identical dataset (`qwen_jarvis_dataset_v2_final`, manifest sha `1cab001c…`), the base model and tokenizer, a CUDA GPU, and compatible library versions. No test resume was run (not authorized). | PASS for the per-step metrics trail and identity hashes. Exact bit-for-bit reproduction: UNVERIFIED. It depends on the same library and CUDA stack, and on the earlier segments' logs and `reproducibility.json`, which are not present in this workspace. |

## 5. best.json / latest.json

- **Schema** (from `checkpoint.py save_checkpoint` and `repro.write_json_atomic`):
  - `latest.json = {"checkpoint", "step"}` is written at **every** save.
  - `best.json = {"checkpoint", "step", "metrics"}` is written only when `is_best`. Its `metrics` equals the checkpoint's meta `metrics`.
  - Format: `json.dumps(indent=1, sort_keys=True)`, no trailing newline.
- **Consumers**: `resolve_resume("latest"|"best")` and `prune()` in the training code. There are no consumers inside `E:\Nexo\Jarvis` (0 scripts).
- **Origin of the old files**: byte-identical to `qwen3_8b_jarvis_full_v3/best.json` and `latest.json` in the older dataset. In other words, the pointer state of the first segment, frozen at step 1200.
- **Correct values**:
  - latest = the most recent save (by `saved_ns`), which is checkpoint-1596.
  - best = the most recent `is_best` save, which is checkpoint-1300 (val_loss 0.6599428926175388).
  - Both agree with `training_results.json` (best_step 1300, step 1596).

| File | Original SHA-256 (bytes) | Backup | New SHA-256 (bytes) | Points to |
|---|---|---|---|---|
| best.json | 9fe8b74e7e85bff8c5f39067a2fc70d08829d0d7f143eb707c4eceae56573c2f (274) | `_finalization_backups/best.json`, identical | e4f51740aef1356ccfdfc5caf8dbfa8f64465c3433182346731dad4909b3d1d9 (289) | checkpoint-1300, step 1300, metrics = checkpoint-1300 meta metrics |
| latest.json | df3a78c380be8949bd61d537f6a511eea4f5f9df506874127257fc36b9eff205 (51) | `_finalization_backups/latest.json`, identical | 89a82037cc34cb72123cda710a24b6415e14f0d3afe5436db054b0aa82b1c5e3 (54) | checkpoint-1596, step 1596 |

Each new file was written to a `.tmp` file, parsed and checked, and then moved into place with `os.replace` (atomic). Both re-parse correctly, and each referenced directory exists.

## 6. training_results.json

- Downloaded individually from `utkarsh6624/jarvis-phase5-secure-checkpoint-backup` into `_finalization_downloads/primary/training_results.json`. Exit code 0.
- Size 348 bytes, which matches the remote listing. SHA-256 `c0ca5647a1b09b09d66f6bca9bcc06045c112023d8ba074fc1c0ba5ab825e929`.
- Contents: step 1596/1596, best_step 1300, best_val_loss 0.6599428926175388, final_val_loss 0.6626523670572841, final_val_ppl 1.9399309255549122, tokens_seen 3731843, skipped_steps 1, adapter_updated true, training_exit_code 0.
- **All values match the historical records**, the checkpoint-1596 meta (val_ppl), and the 1596 trainer-state loop values.
- Its integrity is **UNVERIFIED**: no hash is recorded for it anywhere. Its `note` field says it was "recorded from the completed training output", so it was written by the notebook, not by `train.py`.

## 7. Duplicate cleanup findings

See `FINAL_CLEANUP_PLAN.md`, which was written before any deletion.
- 21 candidates. Each was re-hashed together with its canonical file immediately before deletion.
- All 21 matched, so 0 were skipped.
- Kept as unique or evidence:
  - `_phase_c_downloads/*.log`
  - `_finalization_downloads/*`
  - `_finalization_backups/*`
- The staging folder `C:\Users\utkar\Downloads\JARVIS_Backup` is outside the workspace and was not touched.

## 8. Files deleted (21)

1. `checkpoint-1300/adapter_model.safetensors` (174655536, d71f9a97…26ef570c)
2. `checkpoint-1400/adapter_model.safetensors` (174655536, 8ea48e39…eb7e34fe)
3. `checkpoint-1500/adapter_model.safetensors` (174655536, b9d16c93…d48c865e9a)
4. `checkpoint-1596/adapter_model.safetensors` (174655536, 162b97f6…1bd4564dc8)
5. `adapter_model.safetensors` at the workspace root (174655536, 8c5881d4…6243ea2b, a duplicate of checkpoint-1200)
6–21. `_phase_c_downloads/checkpoint-{1300,1400,1500,1596}/{checkpoint_meta.json, trainer_state.pt, adapter/adapter_config.json, adapter/README.md}`, each byte-identical to its canonical file. Full hashes are in `FINAL_CLEANUP_PLAN.md`.

## 9. Disk space recovered

2271797114 bytes (~2.12 GiB). E: free space went from 165917650944 to 168189493248 bytes.

## 10. Final SHA-256 verification

After cleanup, all 35 canonical files were re-hashed:
- 28 payload files: PASS against their metadata.
- 7 metadata files: hashed and recorded; authenticity UNVERIFIED.

`CHECKPOINT_SHA256SUMS.txt` was checked with `sha256sum -c`.

## 11. Remaining risks and limitations

1. **Metadata authenticity has no external check.** Every payload matches its metadata, but the metadata itself has no external trusted hash. Trust rests on the private Kaggle dataset.
2. **`training_results.json` has no hash to check against.** Its values are consistent everywhere.
3. **The trainer states were not deserialized.** Static inspection only. The opcode-level values are reliable, but whether the tensors load correctly is unverified.
4. **Resume needs things this workspace does not contain**: the training code, the config, the dataset, the base model, a GPU, and matching library versions (§4.5).
5. **The checkpoints are a selection, not the full run.** Since keep_last is 2, the original run would have pruned intermediate checkpoints. The 7 here are what the backups kept; 900 and 1000 are absent by design, not missing.
6. **Outside the workspace:** the staging folder still holds the partial 1300 `trainer_state.pt` (176160768 bytes) and duplicate adapters. It was left untouched as instructed.

## 12. Training resumption status

**Exact training resumption has NOT been demonstrated. It remains untested.**
All files the writer needs for a resume are present and hash-verified for every checkpoint. No resume test was authorized or run, and no environment compatibility check was executed.
