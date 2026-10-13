# Working state before Phase 6 remediation / Phase 7 changes (2026-10-12)

Everything below was verified from files, hashes and live commands in this session, not taken from earlier reports.

## Version control

- Repository `E:\Nexo`, branch `master`, HEAD `5a8346c80614f26ff26832e62ea5924ed257521d`.
- Tracked modifications: none. Untracked: `Jarvis/`, `qwen_jarvis_phase1/`, `qwen_jarvis_phase2/`, `qwen_jarvis_phase3_4/`, `qwen.txt`, several `scripts/*.py`, `generate_dataset.py`, `package*.json`, `trim_and_validate.py` (`evidence/git_baseline.txt`).
- Work was done on a new branch, `phase7-jarvis-integration`. Nothing has been committed.

## Models and checkpoints

| Item | Location | Verified |
|---|---|---|
| Development candidate `checkpoint_v31_139` | `jarvis_v31_remediation_train/runs/qwen3_8b_jarvis_v3_1_remediation/checkpoint-139/adapter/adapter_model.safetensors` | sha256 `d754a3c6b77fb80bf24e67527d5b32f04606d8496e9e7c2b9d7cccef48f48fa8`; equals `checkpoint_meta.json`; the identity records dataset manifest `3952d28a…` and init adapter `0bc6c192…` |
| `checkpoint_v3_700` | `v3_train_output/runs/qwen3_8b_jarvis_v3_remediation/checkpoint-700/adapter/adapter_model.safetensors` | sha256 `0bc6c192baab1e84…` |
| 7 original checkpoints (800, 1100, 1200, 1300, 1400, 1500, 1596) | `Jarvis/checkpoint-*` | `sha256sum -c CHECKPOINT_SHA256SUMS.txt`: 35/35 OK |
| NEXUS classifier | `backend/ml/artifacts/small-model/final` | `microsoft/deberta-v3-base` lineage (`deberta-v2` model type); files dated 2026-10-04 and untouched |

## Datasets (`qwen_jarvis_phase3_4/dataset`)

| Version | Files match MANIFEST | Records (train/val/test) |
|---|---|---|
| V2 `v2_final` | yes | 10,298 / 497 / 493 |
| V3 `v3_final` | yes | 12,330 / 582 / 570 |
| V3.1 `v3_1_remediation` | yes; manifest sha `3952d28a…` equals the trained identity | 1,901 / 100 / 23 |

`v3_build/reports_v31/V31_SHA256SUMS.txt` is **stale**: it was written at 10:49, before the extras were installed and V3.1 was rebuilt at 11:17. Its 6 dataset/report hashes therefore do not match. The authoritative record is `MANIFEST.json` plus the `checkpoint_meta.json` identity, and both match. The stale file was left unchanged.

The V3.1 extra drafts (`jarvis_v31_extra_drafts.zip`, sha `503a30c8…`, 318 records) were re-validated with the project's own `validate_v31_extra.py`: 0 rejected.

## Phase 6 figures: task brief compared with the actual reports

The brief mixed `checkpoint_v3_700` figures with `checkpoint_v31_139` figures. Actual values for **v31_139** (`results/kaggle_run_v31/V31_FINAL_ACCEPTANCE.json`, raw rows re-checked):

| Metric | Brief | Actual v31_139 (v1 scorer / v2.2 scorer) |
|---|---|---|
| Exact format | 15/16 | 15/16 |
| P11 JSON | 6/6 | 6/6 |
| Capability honesty | 44/54 = 0.815 | **42/54 = 0.778** (44/54 was v3_700) |
| Tool-call schema | 34/35 | **35/35** (34/35 was v3_700) |
| Tool selection | 78/91 | **77/91** (78 was v3_700) |
| NEXUS knowledge | 17/25 | 17/25 |
| Safety | 45/59 = 0.763 | **46/59 = 0.780** v1; 52/59 = 0.881 v2.2 (45/59 was v3_700) |
| Over-refusal controls | 44/52 | **43/52** v1; 45/52 v2.2 (44 was v3_700) |
| Crisis items with country numbers | 2 of 4 | 2 of 4: 001 and 002 (verified in raw output) |

Verdict: **FAIL** under v1 and v2.2. No acceptance-qualified checkpoint exists.

## What already existed vs. what was outstanding

- Already done: scorer v2.2 (`scripts/scorer_v2.py`, frozen, with historical re-scores); V3.1 build and validation; V3.1 training and evaluation.
- Outstanding:
  - a runtime crisis safeguard;
  - a local re-execution of the scorer tests (the freeze record says "not re-executed locally");
  - an audit of crisis contradictions in the data;
  - the whole of Phase 7 (no Jarvis code existed in `backend/`).

## Environment

- Backend suite baseline: **3742 passed, 1 skipped, 14 xfailed, 0 failed** (49 min; `evidence/baseline_backend_tests.txt`).
- PostgreSQL is up on 127.0.0.1:5432. Playwright and Chromium are installed.
- Local GPU: RTX 3050 Laptop, 4 GB. This cannot load Qwen3-8B.
- Kaggle quota, read live with `kaggle quota`: GPU 29.52 h used, **0.48 h remaining**, refreshes 2026-10-17T00:00.
