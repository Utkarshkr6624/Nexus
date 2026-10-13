# Phase C — Checkpoint File Recovery Report

Generated: 2026-10-10
Source: private Kaggle dataset `utkarsh6624/jarvis-phase5-secure-checkpoint-backup`, downloaded one file at a time with Kaggle CLI 2.2.4 (`kaggle datasets download <ds> -f <path> -p <dir> --force -q`).
The older dataset `utkarsh6624/qwen3-8b-jarvis-full-v3-checkpoints` was listed only. Nothing was needed from it.
Temporary download folder: `E:\Nexo\Jarvis\_phase_c_downloads\` (kept for audit, along with `dl_*.log` and `trainer_downloads.log`).
No credentials were printed or logged. TLS and authentication were left as they were.
Nothing was deleted. Nothing was overwritten (every destination was absent before its copy). `best.json` and `latest.json` were not touched. `trainer_state.pt` files were never deserialized.
Disk: E: free went from 169413459968 to 165917687808 bytes.

## Remote path confirmation

`kaggle datasets files <ds> --page-size 200 -v` listed exactly one entry for each needed path, so no file was ambiguous:
`checkpoint-{1300,1400,1500,1596}/{checkpoint_meta.json, trainer_state.pt, adapter/adapter_config.json, adapter/README.md, adapter/adapter_model.safetensors}` plus `training_results.json` (348 bytes, not downloaded).

## Kaggle errors

None. All 16 downloads exited with code 0 and produced complete files (no `.kaggle-partial` markers left behind). There were no 404s and no retries.

## Downloads (16 files)

| Checkpoint | File | Expected bytes (meta) | Actual bytes | SHA-256 | Original-hash comparison |
|---|---|---|---|---|---|
| 1300 | checkpoint_meta.json | 1498 (listing) | 1498 | 3e2739a301f6b580d2c015f18781af2473217e96401f4b0a68c9e468bee4ccec | UNVERIFIED (has no hash of its own); byte-identical to the staging copy |
| 1300 | adapter/adapter_config.json | 1146 | 1146 | f5f0c671d8840894ceb384bdaa765c09adbc235678e57433954498477acb86f0 | PASS |
| 1300 | adapter/README.md | 5180 | 5180 | 85e2b3a3fae33eab459538a9f1ce2d9ad6bd9fb7c6b97f9ee97e07923ad3e57a | PASS |
| 1300 | trainer_state.pt | 349622034 | 349622034 | 3de2eafb922bf2eb3f268e1c44617da3314666872e6bb3a1d4630d10500faeb5 | PASS |
| 1400 | checkpoint_meta.json | 1498 (listing) | 1498 | eaaee64f044ad947d6ed22f21d676c4c416f5f79de2f74357b0abc22b996186b | UNVERIFIED (has no hash of its own) |
| 1400 | adapter/adapter_config.json | 1146 | 1146 | f5f0c671d8840894ceb384bdaa765c09adbc235678e57433954498477acb86f0 | PASS |
| 1400 | adapter/README.md | 5180 | 5180 | 85e2b3a3fae33eab459538a9f1ce2d9ad6bd9fb7c6b97f9ee97e07923ad3e57a | PASS |
| 1400 | trainer_state.pt | 349622034 | 349622034 | 81d314ae6f3a193fee1f484e79d9d4122fbc294545aae9458017dd8df980384d | PASS |
| 1500 | checkpoint_meta.json | 1499 (listing) | 1499 | c4354c5ab816daaf319fffb08649b8a2309e28d8890aa626b137569ac5d07ec3 | UNVERIFIED (has no hash of its own) |
| 1500 | adapter/adapter_config.json | 1146 | 1146 | f5f0c671d8840894ceb384bdaa765c09adbc235678e57433954498477acb86f0 | PASS |
| 1500 | adapter/README.md | 5180 | 5180 | 85e2b3a3fae33eab459538a9f1ce2d9ad6bd9fb7c6b97f9ee97e07923ad3e57a | PASS |
| 1500 | trainer_state.pt | 349622034 | 349622034 | 81f36e901bdde386ac12df440e6f27749b1bd093ed5ac0ab141f2b882fe861dc | PASS |
| 1596 | checkpoint_meta.json | 1499 (listing) | 1499 | a8bef0e232fe367d308ff9c551115a29a8370af01411916beea6ca715a8a7478 | UNVERIFIED (has no hash of its own) |
| 1596 | adapter/adapter_config.json | 1146 | 1146 | f5f0c671d8840894ceb384bdaa765c09adbc235678e57433954498477acb86f0 | PASS |
| 1596 | adapter/README.md | 5180 | 5180 | 85e2b3a3fae33eab459538a9f1ce2d9ad6bd9fb7c6b97f9ee97e07923ad3e57a | PASS |
| 1596 | trainer_state.pt | 349622034 | 349622034 | 29c19b9c8cf9143167e4df44b25a929192a2448ade14bd6aa8c4c6fbbb38178b | PASS |

## Files reused (not downloaded)

| Checkpoint | Source | Placed at | SHA-256 | vs meta |
|---|---|---|---|---|
| 1300 | checkpoint-1300\adapter_model.safetensors (local root) | checkpoint-1300\adapter\adapter_model.safetensors | d71f9a974837cdbaf4ade94e779ed15bfeee67bb215b77c14db9333926ef570c | PASS |
| 1400 | checkpoint-1400\adapter_model.safetensors (local root) | checkpoint-1400\adapter\adapter_model.safetensors | 8ea48e39cfc975fdad23ae8dc2bfc2ba19af9b574be0600b633460f4eb7e34fe | PASS |
| 1500 | checkpoint-1500\adapter_model.safetensors (local root) | checkpoint-1500\adapter\adapter_model.safetensors | b9d16c934d689952da179c4b8b3dbdfb104172ce2e95a46ecad685d48c865e9a | PASS |
| 1596 | checkpoint-1596\adapter_model.safetensors (local root) | checkpoint-1596\adapter\adapter_model.safetensors | 162b97f6007e76a147833c126b11f5146034c1315c08024414464b1bd4564dc8 | PASS |

The staging `checkpoint-1300\checkpoint_meta.json` was compared only. It is byte-identical to the Kaggle download, and the Kaggle copy is the one placed in the checkpoint.
The partial staging `checkpoint-1300\trainer_state.pt` (176160768 bytes) was not used.

## Placement

Each file was copied to its final location and then re-hashed there. All 24 placements PASS (4 checkpoints × meta, trainer_state, and 3 adapter files).
The original root adapters (`checkpoint-N\adapter_model.safetensors`) and the two extra copies of the checkpoint-1200 adapter are still in place and were not deleted.

## Final in-place verification: every file checked against its own checkpoint_meta.json

| Checkpoint | Files present | Adapter SHA-256 | Adapter | Config | README | Trainer state | config_hash | dataset_version | step | val_loss (meta) | is_best (meta) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 800 | 5/5 | 41ee6d98…4eeed1fc | PASS | PASS | PASS | PASS | 9312597956f22164 | qwen_jarvis_dataset_v2_final | 800 | 0.6658911464513418 | true* |
| 1100 | 5/5 | 35b8707b…b62beebe | PASS | PASS | PASS | PASS | 9312597956f22164 | qwen_jarvis_dataset_v2_final | 1100 | 0.6743624176073134 | false |
| 1200 | 5/5 | 8c5881d4…6243ea2b | PASS | PASS | PASS | PASS | 9312597956f22164 | qwen_jarvis_dataset_v2_final | 1200 | 0.6673446315759556 | false |
| 1300 | 5/5 | d71f9a97…26ef570c | PASS | PASS | PASS | PASS | 9312597956f22164 | qwen_jarvis_dataset_v2_final | 1300 | 0.6599428926175388 | true |
| 1400 | 5/5 | 8ea48e39…eb7e34fe | PASS | PASS | PASS | PASS | 9312597956f22164 | qwen_jarvis_dataset_v2_final | 1400 | 0.6648752473479987 | false |
| 1500 | 5/5 | b9d16c93…d48c865e9a | PASS | PASS | PASS | PASS | 9312597956f22164 | qwen_jarvis_dataset_v2_final | 1500 | 0.6627904218147832 | false |
| 1596 | 5/5 | 162b97f6…1bd4564dc8 | PASS | PASS | PASS | PASS | 9312597956f22164 | qwen_jarvis_dataset_v2_final | 1596 | 0.6626523670572841 | false |

Full hashes are in the tables above and in Phase B.
All seven `model_id` values are `Qwen/Qwen3-8B`.
The val_loss values in the metadata for 1200–1596 match the historical records exactly. The step-1300 metadata confirms it is the best checkpoint (lowest val_loss, is_best=true).
\* The checkpoint-800 metadata says is_best=true. That is relative to the earlier run segment, which ended at 1200. 800's val_loss is higher than 1300's.

## Configuration hash consistency

- `config_hash` = 9312597956f22164 in all 7 metadata files. PASS.
- `adapter_config.json` has two byte variants: c9a0eda9… (800/1100/1200) and f5f0c671… (1300–1596). Each checkpoint's file matches its own metadata. No file was substituted from another checkpoint.
- The only parsed difference is the **order** of `target_modules`. The set is the same: {q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj}. All other key fields are identical: peft_type LORA, r 16, lora_alpha 32, lora_dropout 0.05, bias none, task_type CAUSAL_LM, base Qwen/Qwen3-8B. PEFT saves `target_modules` from a set, so the order can change between processes. The two variants are equivalent in effect.

## Remaining risks

1. **The metadata files have no outside hash.** They are self-consistent: every payload hash they list matches. That includes the 1300–1596 adapters, which were downloaded in an earlier session and are now independently confirmed by the newly downloaded metadata. Their authenticity still rests on trusting the private Kaggle dataset.
2. **Two run segments.** 800–1200 were saved 09:29–10:35Z. 1300–1596 were saved 15:02–15:55Z, and their config variant suggests a different process. That fits a resume from checkpoint-1200, but continuity is unconfirmed without inspecting the trainer states. Phase G covers that.
3. **Training resumption not confirmed.** All required files are present and hash-verified, but contents and environment compatibility (torch 2.11.0+cu128, peft 0.20.0, transformers 5.16.1, bitsandbytes 0.50.2) are untested. This is Phase G.
4. **`best.json` / `latest.json` are stale.** They still point to 800 and 1200, not 1300 and 1596. They were left untouched as instructed. Phase H.
5. **Clutter kept on purpose.** The root-level adapter copies in 1300–1596, `E:\Nexo\Jarvis\adapter_model.safetensors`, and `_phase_c_downloads\` (about 1.4 GB) are duplicates. Removing them needs explicit approval.
