# Kaggle package - Qwen3-8B JARVIS QLoRA SFT

**Status: built and statically validated in Phase 3/4. Kaggle execution is PENDING PHASE 5.** Nothing in this directory has been run on Kaggle; no Qwen
weights were downloaded; no GPU time was used. Memory and time figures in `docs/MEMORY_PLAN.md` are ESTIMATES, not measurements.

## What is here

| file | purpose |
|---|---|
| `build_package.py` | builds `dist/qwen_jarvis_kaggle_package.zip` (deterministic; secret scan; per-file SHA-256 manifest) |
| `requirements_kaggle.txt` | Python dependencies installed by the notebook (torch is *not* reinstalled) |
| `notebook/build_notebook.py` | reproducibly generates the notebook |
| `notebook/qwen_jarvis_phase5_training.ipynb` | the Phase 5 notebook; production cells are gated and disabled by default |
| `validate_notebook.py` | static validation (C10) -> `notebook_validation.json` |
| `memory_calc.py` | regenerates `docs/MEMORY_PLAN.md` / `memory_estimates.json` from the real `config.json` |

## Build the package (local, safe, no network)

```text
cd qwen_jarvis_phase3_4
.venv\Scripts\python.exe kaggle\build_package.py          # writes kaggle\dist\qwen_jarvis_kaggle_package.zip + package_sha256.txt
.venv\Scripts\python.exe kaggle\notebook\build_notebook.py
.venv\Scripts\python.exe kaggle\validate_notebook.py
```
The package contains `pipeline/`, `configs/`, `tool_protocol/`, the frozen dataset (mounted as `dataset/v2_final/`), `kaggle/` (README, requirements, notebook), a few docs, and
`PACKAGE_MANIFEST.json`. It never contains `.venv`, `runs/`, model weights, `.env` files or tokens; the build fails if the secret scan finds anything.
The tokenizer/config files in `_hf_meta/` are not packaged: the tokenizer is fetched with the model in Phase 5.

## Phase 5 setup (do these only when Phase 5 starts)

1. **Create a Kaggle dataset** from `kaggle/dist/qwen_jarvis_kaggle_package.zip` (Datasets -> New dataset -> upload the zip; keep it **private**). Compare the SHA-256 in `package_sha256.txt`.
2. **Create a new Kaggle notebook** and upload/import `kaggle/notebook/qwen_jarvis_phase5_training.ipynb`. *Add data* -> your private dataset.
3. **Accelerator:** choose a **T4** (a single GPU is enough - see `docs/MEMORY_PLAN.md`). Prefer T4 over P100: P100 is compute capability sm_60 and its support in the current default Kaggle PyTorch wheels is **unverified**. T4/P100 have no native bf16, so the run must use fp16 (the environment cell decides from the compute capability, not from `torch.cuda.is_bf16_supported()`, which is True on a T4 only through slow emulation). **Internet:** turn it on only for the session that needs to download Qwen/Qwen3-8B (about 16 GB).
4. **Secrets:** Qwen3-8B is open-weights and normally needs no token. If one is ever required, add it under *Add-ons -> Secrets* as `HF_TOKEN`. The notebook reads it only inside the gated cell and never prints it.
   Never paste tokens into cells, files or the package.
5. Run the cells top to bottom. Cells 1-6 (environment, package check, install, config, dry run) are safe. Then open the **production gate** (cell 7): set `PHASE5_AUTHORIZED = True`, `CONFIRM_PRODUCTION = True`
   and set the environment variable `QWEN_PHASE5_UNLOCK=I_UNDERSTAND_THIS_STARTS_PRODUCTION_TRAINING` yourself. Set `MODE` in the same cell: `"train"` for a fresh run, `"resume"` after a killed session - only the matching cell acts and the other prints which MODE it needs, so *Run all* in a restarted session cannot start a fresh run over an existing one (the train cell also refuses when `runs/` already holds checkpoints). Only then do the train / resume / evaluate / loss / compare cells do anything.
6. **Checkpointing:** checkpoints go to `/kaggle/working/runs/<run>/checkpoint-<step>/` (LoRA adapter + optimiser state, `checkpoint_meta.json`, atomic writes, `latest.json`, `best.json`).
   If the session dies, save the run directory as a Kaggle dataset (*Save Version* output, or *Output -> New dataset*), add it to a new session and run the **resume** cell (`--resume latest`).
7. **Export:** the export cell copies the best LoRA adapter (a few hundred MB) to `/kaggle/working/export/`. Merging into the full bf16 model is optional and not done in the notebook.

## Library versions are UNVERIFIED on Kaggle
Every local test ran on a **CPU-only** venv: torch 2.14.1, transformers 5.19.0, peft 0.21.2, trl 1.15.0 (accelerate 1.15.0, datasets 5.1.0). `bitsandbytes` (CUDA-only) has never been run, and the `dtype=`/`torch_dtype=` fallback used for transformers 4.51-4.55 was never exercised. `requirements_kaggle.txt` therefore has hard lower bounds plus next-major guards only. The notebook prints the resolved versions next to the tested ones and runs `pip check`; after the first real install, pin the versions that worked before any long run.

## Throughput
Memory and time numbers in `docs/MEMORY_PLAN.md` are ESTIMATES. The notebook does not itself compute tokens/s: it streams the trainer's log, and the trainer's periodic log lines carry `tok_per_s` and a `first_steps_throughput` event is written after 10 steps (verified in `pipeline/train/train.py`); the real number only exists once Phase 5 has run.

## Local-only commands
`--smoke-test` is **workspace-only**: it needs `_hf_meta/`, which is deliberately not packaged. From the extracted package only `--validate-config`, `--dry-run` (which fetches the tokenizer files, so it needs internet on Kaggle) and the refusal of production without the unlock work.

## Reproducibility
`PACKAGE_MANIFEST.json` lists the SHA-256 of every file (generated validation/report outputs are excluded); the notebook verifies it after extraction and also checks that `dataset/v2_final/MANIFEST.json` is present with exactly that name (case-sensitive, Kaggle is Linux) and that the train/validation/test hashes match it. Checkpoints record dataset version, model id, library versions, seed and config hash.

## Safety notes
* Training data is data: no pipeline step executes dataset text.
* The gate has four independent conditions (see the notebook); the same lock exists in `configs/qlora_sft.yaml` (`production_locked: true`) and in `train.py` (`--confirm-production`).
* This workspace never touches the NEXUS application; the package contains none of its source.
