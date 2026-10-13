# Phase 6 Reproduction / Resume Instructions

## A. Local, CPU-only (everything that does not need the model): already executed

```text
cd E:\Nexo\Jarvis\jarvis_phase6_final
set PY=E:\Nexo\qwen_jarvis_phase3_4\.venv\Scripts\python.exe
%PY% -B scripts\dataset_evidence.py                 # -> results\dataset_evidence.json
%PY% -B scripts\integrity_check.py                  # -> manifests\checkpoint_integrity.json   (set HF_HUB_OFFLINE=1)
%PY% -B scripts\phase6_suites.py                    # -> prompts\phase6_*.jsonl (54 items; exits 1 if any oracle fails its own check)
%PY% -B scripts\selftest_phase6.py                  # checks, integrity gates, launcher/kernel definitions, stub pipeline, 2-run merge; must print SELFTEST PASSED
cd E:\Nexo\Jarvis\_resume_test\code && %PY% -B -m pipeline.eval.cli selftest   # original scorer self-test
```

## B. Kaggle GPU run. NOT YET EXECUTED; every step needs explicit approval

### B.0 Inputs (verified 2026-10-11 by listing; nothing uploaded yet)

| Kaggle dataset (private) | Holds | Used for |
|---|---|---|
| `utkarsh6624/qwen-jarvis-phase5-package` | the training package as **extracted files** (64 files incl. `pipeline/eval/` and its data). Its `PACKAGE_MANIFEST.json` sha256 is `888b18ba…005a0`, equal to the manifest inside the local zip `944eab01…`. | `--code` (training-package evaluation code + suites) |
| `utkarsh6624/jarvis-phase5-secure-checkpoint-backup` | checkpoint-1200, 1300, 1400, 1500, 1596 | adapters |
| `utkarsh6624/qwen3-8b-jarvis-full-v3-checkpoints` | `qwen3_8b_jarvis_full_v3/checkpoint-800, 1100, 1200` | adapters 800 and 1100 (and a second 1200 copy) |
| `utkarsh6624/jarvis-phase6-eval` | **to be created**: `scripts/`, `prompts/`, `kaggle/` from this folder | runner, suites, launcher, pinned requirements |

Do not attach `qwen-jarvis-phase-5-training-package`: it is an older duplicate, and the launcher aborts if it finds two packages.

Mount paths differ between Kaggle versions (`/kaggle/input/<slug>` or `/kaggle/input/datasets/<owner>/<slug>`). The launcher therefore **discovers** paths and never hard-codes them.

### B.1 Integrity gates (all run before any generation; any failure aborts)

The gates run in this order:

1. **Package gate** (`scripts/trusted_hashes.py: verify_package`). It runs before any package code is imported.
   - The `PACKAGE_MANIFEST.json` sha256 must equal the trusted constant `888b18ba…`. That constant was established from the locally verified zip, not from the Kaggle input.
   - Then every one of the 64 listed files must match its recorded sha256 and size.
2. **GPU gate** (launcher): every GPU must be a Tesla T4 with ≥ 14 GB.
3. **Library gate** (launcher). It installs `kaggle/requirements_phase6.txt` and asserts transformers 5.16.1, peft 0.20.0, accelerate 1.14.0 and bitsandbytes 0.50.2, the versions recorded in every `checkpoint_meta.json`. torch is Kaggle's own build and is recorded.
4. **Adapter gate** (`find_adapters`). It runs before the base model is downloaded.
   - For each of the 7 steps it searches every input for `checkpoint-<step>/adapter`.
   - It accepts only a copy whose `adapter_model.safetensors` **and** `adapter_config.json` equal the trusted hashes; a wrong-hash copy is never used.
   - A missing or mismatched adapter aborts the run.
   - All 7 adapters are verified in **both** runs.
5. **Adapter-control gate** (runner, after loading). Three conditions must hold:
   - the bare-base logit fingerprint is reproduced under `disable_adapter()`;
   - each adapter gives `active_adapters == [name]` with a fingerprint different from the base;
   - all 7 fingerprints are pairwise distinct.

### B.2 Workload split (Kaggle sessions end at 12 h)

| Run | Kernel | Configs | Generations | Estimate (not measured) |
|---|---|---|---|---|
| 1 | `utkarsh6624/jarvis-phase6-eval-run1` | base_model, checkpoint_800, checkpoint_1100, checkpoint_1200 | 4 × 422 = 1688 | ≈ 4.5–5 h |
| 2 | `utkarsh6624/jarvis-phase6-eval-run2` | base_model, checkpoint_1300, checkpoint_1400, checkpoint_1500, checkpoint_1596 | 5 × 422 = 2110 | ≈ 5.5–6.5 h |

- **Base model in both runs.** Every checkpoint is then compared with a base run from the same session, and the two base runs measure cross-session determinism (`determinism.json → cross_run`).
- **Time budget.** The runner does not *start* a configuration once elapsed time plus the slowest finished configuration would exceed 10.5 h. Finished configurations are kept, and `run_status.json` lists what was not started.
- **Resume.** Attach the previous run's output as a kernel source; the launcher copies `phase6_run<G>/raw/*.json` and the runner skips complete configurations.
- **Estimate basis.** Assumes ≈ 10 s per generation on a T4 with 4-bit loading. The first run's `run_status.json` gives the real figure.

### B.3 Commands (planned; none executed)

**Step 2: build the upload staging folder and create the private dataset.**

```text
cd E:\Nexo\Jarvis\jarvis_phase6_final
rmdir /s /q upload_staging\jarvis-phase6-eval 2>nul
mkdir upload_staging\jarvis-phase6-eval
xcopy /e /i scripts upload_staging\jarvis-phase6-eval\scripts
xcopy /e /i prompts upload_staging\jarvis-phase6-eval\prompts
mkdir upload_staging\jarvis-phase6-eval\kaggle
copy kaggle\phase6_launch.py upload_staging\jarvis-phase6-eval\kaggle\
copy kaggle\requirements_phase6.txt upload_staging\jarvis-phase6-eval\kaggle\
copy upload_staging\dataset-metadata.json.template upload_staging\jarvis-phase6-eval\dataset-metadata.json
kaggle datasets create -p upload_staging\jarvis-phase6-eval --dir-mode zip
kaggle datasets files utkarsh6624/jarvis-phase6-eval --page-size 200 -v
```

Then verify by download-back. Download every listed file with `kaggle datasets download utkarsh6624/jarvis-phase6-eval -f <path> -p artifacts\upload_check --force` and compare its sha256 with the staging copy. **Do not launch** if any path or hash differs (for example, if `--dir-mode zip` left the folders as zip files).

**Step 3: launch the runs** (each push starts a background, committed run whose `/kaggle/working` output persists).

```text
kaggle kernels push -p kaggle\kernels\run1
kaggle kernels status utkarsh6624/jarvis-phase6-eval-run1
kaggle kernels push -p kaggle\kernels\run2           # after run 1 has passed its gates (or concurrently, if quota allows)
```

- Confirm on the Kaggle page that the accelerator is **T4**. `machine_shape: "NvidiaTeslaT4"` in `kernel-metadata.json` is not yet verified against the CLI; the launcher aborts on any non-T4 GPU anyway.
- Kernels are private (`is_private: "true"`), have internet ON (pip and the base-model download; no token), and attach the 4 datasets above.

**Step 4: collect and score locally.**

```text
kaggle kernels output utkarsh6624/jarvis-phase6-eval-run1 -p results\kaggle_run\run1
kaggle kernels output utkarsh6624/jarvis-phase6-eval-run2 -p results\kaggle_run\run2
%PY% -B scripts\score_phase6.py results\kaggle_run\merged results\kaggle_run\run1\phase6_run1 results\kaggle_run\run2\phase6_run2
```

The merged scoring takes `base_model` from run 1, and the run 2 base results are compared item by item in `determinism.json`. Review `failed_tests.json`, then apply the rubric's selection rule.

### B.4 Safety notes

- Everything is private, and no tokens or secrets are needed or used. Nothing is ever written to `/kaggle/input`, so the originals stay read-only.
- Internet must be ON. While it is on, `python_exec` runs model-written code behind an AST allow-list (no `os`, `socket`, `subprocess`, `open`, dunder access or non-allow-listed imports), restricted builtins, `-I -S`, a temp working directory and a 5 s timeout. This is **not** an OS sandbox. To disable execution, add `--no-exec` to the runner command in `phase6_launch.py`; the coding metric then becomes static-only.
- No training or Phase 5 production code is run.

### B.5 The initial 96-response run

If its files still exist (`phase6_raw_results.json/.csv`, `phase6_manifest.json`, `phase6_initial_scores.csv`), copy them unchanged into `results\initial_96\`.

## C. Reload verification of a selected adapter (after selection)

Run the launcher's runner on GPU with:
- `--configs checkpoint_<step>`
- `--suites phase6_format,phase6_identity`

The adapter gate re-checks the trusted sha256 before loading.
