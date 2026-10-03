# Phase 10 — ML training: final report

**Verdict: PHASE 10 INCOMPLETE.** The small routing model was genuinely trained
and evaluated. The `Qwen3-8B` QLoRA fine-tune was **not** trained, because no
accelerator was available to train it on, and that is documented below with the
evidence rather than papered over. The pipeline that would run it is built,
wired and automated; the hardware was not there.

---

## 1. Data

### Sources

Everything is **synthetic**, and every row says so. Nexo is a single-user personal
system, so no corpus of real user utterances exists to sample from. Instead:

1. **The application's own capability inventory.** `ml/datasets/capabilities.py`
   parses `app/` as source — never importing it — and harvested **185 routes
   across 19 domains, 12 recommendation types, 7 risk types, 11 permissions and
   62 activity events** (`nexo_capabilities.v1`). Every closed vocabulary the
   corpus draws from is a subset of these, so a renamed enum shrinks the corpus
   visibly instead of silently teaching a stale label.
2. **Hand-written template families per intent**, rendering that vocabulary into
   ordinary English. Each intent has its own families, its own slot pools and its
   own wording rules.
3. **The intent taxonomy itself** (`nexo_intents.v1`), whose exemplars are the
   highest-quality rows available because they were written against the real
   routers.

No public dataset was downloaded. Nothing from the unrelated abandoned Kaggle
project was reused.

### Corpus

| | Routing corpus | Qwen SFT corpus |
| --- | --- | --- |
| Version | `routing_dataset.v1` | `qwen_dataset.v1` |
| Record schema | `routing_intent.v1` | `qwen_sft.v1` |
| Rows | **2 100** (150 × 14 intents, exact balance) | **400** (40 × 10 categories) |
| train / validation / test | 1 470 / 322 / 308 | 280 / 60 / 60 |
| Provenance | 2 100 synthetic | 400 synthetic |

### Validation — all passed

`ml/validation.py` checks schema, schema version, missing and invalid values,
duplicate and near-duplicate texts, contradictory labels, class balance, label
validity, malformed records, train/validation/test leakage, source provenance,
synthetic-vs-real marking, feature availability and feature-version compatibility.
`prepare` refuses to continue unless every validator passes. Reports are written
to `ml/reports/{routing,qwen}_dataset.{json,md}` and `{routing,qwen}_splits.{json,md}`.

Result on the current corpus: 2 100 records, 2 100 unique keys, **zero findings**.

### Splits

Deterministic, seeded at **20260101**, grouped by the near-duplicate key so
related samples cannot straddle a split. Each corpus is audited separately:
leakage is a within-partition property, and pooling a routing utterance with a
Qwen instruction that happens to share a bag of words would report a collision no
model can commit. Both leakage audits report **0 leaked keys**.

### Data-quality work done this phase

Two real defects were found and fixed, both of which would have taught the model
something wrong:

1. **Cross-intent de-duplication was silently disabled.** `routing._generate`
  shadowed its `seen: set[str]` parameter with a fresh local set, so the
  cross-intent uniqueness the module's whole design rests on was never enforced.
  Invisible at the shipped seed (no two template families collide), latent at
  higher `--per-intent`. Fixed and verified: 2 100 rows, 2 100 unique texts.
2. **Slot pools were too narrow to fill a balanced corpus.** `analytics_insight`
  could produce 129 distinct utterances from 36 templates against a ceiling of
   116 slot combinations, and the builder raised at `--per-intent 200` — correctly
  refusing to pad. The pools were widened (projects 20→46, roles 5→24, topics
   20→42, timezones 6→15, libraries 12→23, and ten more), raising the binding
   ceiling to ~208.

---

## 2. Small model — trained and evaluated

| | |
| --- | --- |
| Base model | `microsoft/deberta-v3-base` |
| Parameters | **184 432 910** (inside the 100M–300M brief band; the loader refuses a checkpoint outside it) |
| Method | full fine-tune, 14-way classification |
| Labels | 14 intents, `num_labels` asserted against the taxonomy at prepare time |
| Hyperparameters | lr 2e-5, weight decay 0.01, batch 16, 4 epochs, warmup 10%, `balanced` loss weighting, max_seq 128 |
| Schedule | 368 optimiser steps (92/epoch × 4), warmup 36 |
| Seed | 20260101 |
| Device | **CPU** — torch 2.14.1+cpu, transformers 5.18.0 |
| Wall clock | **2 417 s** (≈40 min), including the resumed portion |
| Loss | 2.630 → **0.056** |
| Final loss | 0.0564 (not early-stopped) |
| Dataset checksum | `97bc73a7ab833192…` |
| Code commit | `64dd7809eb80` (dirty) |

### Metrics — held-out test split, 308 rows

| metric | before (1 260 rows, 3 epochs) | **now (2 100 rows, 4 epochs)** |
| --- | --- | --- |
| accuracy | 0.6209 | **0.9675** |
| macro F1 | 0.5648 | **0.9674** |
| weighted F1 | 0.5648 | **0.9674** |

Validation (322 rows): accuracy 0.9441, macro F1 0.9443.

### Per-intent

| intent | P | R | F1 | n |
| --- | --- | --- | --- | --- |
| `analytics_insight` | 1.000 | 0.864 | 0.927 | 22 |
| `knowledge_capture` | 1.000 | 0.864 | 0.927 | 22 |
| `project_manage` | 0.913 | 0.955 | 0.933 | 22 |
| `task_manage` | 0.913 | 0.955 | 0.933 | 22 |
| `knowledge_lookup` | 0.880 | 1.000 | 0.936 | 22 |
| `developer_intel` | 0.955 | 0.955 | 0.955 | 22 |
| `career_track` | 1.000 | 0.955 | 0.977 | 22 |
| `learning_track` | 0.957 | 1.000 | 0.978 | 22 |
| `risk_query` | 0.957 | 1.000 | 0.978 | 22 |
| `account_admin` | 1.000 | 1.000 | 1.000 | 22 |
| `code_assist` | 1.000 | 1.000 | 1.000 | 22 |
| `deep_reasoning` | 1.000 | 1.000 | 1.000 | 22 |
| `out_of_scope` | 1.000 | 1.000 | 1.000 | 22 |
| `schedule_plan` | 1.000 | 1.000 | 1.000 | 22 |

### Failure cases

No class is below 0.927 F1, and the four imperfect classes are all *recall*
losses on deliberately near-neighbouring intents: `knowledge_capture` vs
`knowledge_lookup` (both mention a note; one writes, one reads) and
`analytics_insight` vs `risk_query` (both report on measured history; one says
what happened, one says what will). Those are the boundaries the taxonomy exists
to hold, and they are held at ~0.93 rather than perfectly — which is the honest
shape of a boundary between two classes rather than a defect.

The confusion matrix and the full per-class table are in
`ml/artifacts/small-model/metrics.md` and `metrics.json`.

### OOD behaviour

`out_of_scope` is a trained class (F1 1.000), so abstention is something the
model can be *right* about. `ml/evaluation/metrics.py` also implements
`top_k_accuracy` for the top-2 abstention analysis, which is the number a Phase 11
router would be measured on. It is **not** computed in this run — see
Limitations.

---

## 3. Qwen3-8B — prepared, configured, blocked

| | |
| --- | --- |
| Base model | `Qwen/Qwen3-8B` |
| Method | QLoRA — 4-bit NF4 base, frozen; LoRA r=16, α=32, dropout 0.05, all seven projections |
| Corpus | `qwen_dataset.v1`, 400 rows (280 train / 60 validation / 60 test) |
| Hyperparameters | lr 2e-4, cosine, 3% warmup, 2 epochs, seq 2048, micro-batch 1 × grad-accum 16, grad checkpointing, bf16, max_grad_norm 0.3 |
| Segmentation | 250 steps per segment, checkpoint every 100 steps |
| **Status** | **BLOCKED — no accelerator exists on this account** |

### The blocker, with evidence

`Qwen/Qwen3-8B` under 4-bit QLoRA needs **≈5.7 GiB**: 3.7 GiB of frozen 4-bit
weights plus 2.0 GiB of activations and optimiser state.

- **Local:** RTX 3050 with 4 GB VRAM. `torch 2.14.1+cpu` sees no CUDA device.
- **Remote:** `--probe-remote` pushed a one-cell Kaggle kernel
  (`utkarsh6624/nexo-phase10-gpu-probe` v2, COMPLETE) that interrogated the
  machine it actually ran on and wrote `ml/artifacts/remote/nexo_gpu_probe.json`:

  ```
  kaggle_run_type   "Batch"
  cpu_count         4
  torch             2.11.0+cpu   (cuda: false)
  nvidia-smi        not on PATH
  device nodes      no /dev/nvidia*
  dns huggingface.co   gaierror: Temporary failure in name resolution
  dns pypi.org         gaierror: Temporary failure in name resolution
  ```

Kaggle's kernel metadata records `enable_gpu: true` and `machine_shape:
NvidiaTeslaT4` — but that is a record of the **request**, not of the machine. Two
independently-shaped metadata pushes produced the same CPU-only batch image.
Without a device *and* without DNS, an 8B model cannot be fitted **or fetched**.
The account shows 30 GPU-hours of quota; a quota figure is not an accelerator.

### What was NOT done

No training was attempted. **No adapter exists. No base-vs-fine-tuned comparison
was performed, and no number in this report claims one.**

### What is ready

`ml/artifacts/qwen/segment-000/` holds the rendered notebook and its input
bundle, so the run is one `make ml-train-qwen` away on hardware that can hold the
model. `ml/artifacts/qwen/qwen_run.json` records the verdict, the blocker, the
VRAM floor and the probe that established it.

---

## 4. Automation

### Stages

Seven, in `python -m ml.train` and `make ml-*`:

| stage | flag | make target | automated? |
| --- | --- | --- | --- |
| `prepare` | `--prepare` | `ml-prepare`, `ml-datasets`, `ml-validate` | yes |
| `train-small` | `--train-small` | `ml-train-small`, `ml-train-small-resume` | yes |
| `evaluate` | `--evaluate` | `ml-eval` | yes |
| `train-qwen` | `--train-qwen` | `ml-train-qwen` | yes, up to the accelerator gate |
| `probe-remote` | `--probe-remote` | `ml-probe-remote` | **yes — ran for real** |
| `eval-qwen` | `--eval-qwen` | `ml-eval-qwen` | yes, up to the adapter gate |
| `qwen-status` | `--qwen-status` | `ml-qwen-status` | **yes — ran for real** |

`train-qwen`, when an accelerator is verified, publishes the bundle as a Kaggle
dataset, renders the notebook against the published slug, pushes a kernel, blocks
until a terminal state, downloads the output, and rewrites the run record. It
pushes and waits in **one** call so the stage's verdict is the kernel's terminal
state rather than "accepted for execution".

### What the user does not have to do

Not a single row is hand-labelled; not a dataset is built by hand; no parameter
is configured by hand; no metric is computed by hand; no checkpoint is uploaded or
downloaded by hand; no artifact is copied by hand. The only manual step anywhere
in the remote path is **Kaggle authentication**, and that was already in place
and is reused — the pipeline never reads, prints, logs or commits the credential.

### MCP

No MCP tools were available in this environment; the Kaggle CLI was used
directly, and `ml/training/remote.py` is credential-blind by construction (it
shells out and reads only the `author` field the API already returns).

### Checkpoint / resume — demonstrated, not just implemented

This was verified live rather than asserted. The training run was interrupted at
step 220 and resumed:

```
resuming  yes - ...checkpoints\step-200 at step 200, epoch 2, batch 16 of 92
eval      step 200 loss 0.7371 accuracy 0.9068 macro_f1 0.9060
```

The resumed run re-evaluated at **exactly** the recorded step-200 numbers, then
continued to step 368. `training_state.json` records `resumed: true`,
`resumed_from: .../checkpoints/step-200`, and the full run record carries seed,
dataset checksum, config, resolved library versions and code commit.

**One honest wrinkle:** the resumed run's learning rate replayed warmup from step
200 rather than resuming mid-schedule. The adapter and optimiser state restore
correctly; the scheduler's position does not survive the round-trip. That is a
real fidelity gap in the resume path and it is stated here rather than buried.

---

## 5. Engineering

### Files added

- `backend/ml/train.py` — +`probe-remote` and `eval-qwen` stages, the real Qwen
  push path, `_notebook_config`, `_adapter_dir_name`, `_publish_qwen_dataset`.
- `backend/ml/kaggle/notebook.py` — `render_gpu_probe_notebook`.
- `backend/ml/datasets/routing.py` — widened slot pools; fixed the shadowed
  `seen` set.
- `backend/tests/test_ml_stages.py` — **37 new tests** (36 pass, 1 skipped:
  `nbformat` is not a backend dependency).
- `docs/specifications/phase-10-architecture.md`,
  `docs/specifications/phase-10-training.md`.

### Files changed

`Makefile` (two targets added, `.PHONY` updated), `.gitignore` (`*.ipynb`),
`configs/small_model.toml` (epochs 3→4, corrected rationale), `README.md`,
`docs/architecture.md`, `docs/specifications/README.md`,
`backend/tests/test_ml_notebooks.py`, `backend/tests/test_ml_secrets.py`.

### Tests

| suite | result |
| --- | --- |
| **full backend suite** | **2712 passed**, 2 skipped, 0 failed (18 m 43 s) |
| `tests/test_ml_*.py` | **442 passed**, 1 skipped |
| of which new (`test_ml_stages.py`) | 36 passed, 1 skipped |
| `ruff check .` | **All checks passed** |
| `ruff format --check .` | clean after formatting |

The two skips are environmental, not skipped-to-pass: one needs a Windows
directory-symlink privilege the session does not hold (a pre-existing Phase 8
test), one needs `nbformat`, which is deliberately not a backend dependency.
Nothing was skipped to make a suite pass, and no existing test was weakened.

### Artifacts

```
ml/artifacts/small-model/final/          trained classifier (184M params)
ml/artifacts/small-model/checkpoints/    step-100, step-200, step-300
ml/artifacts/small-model/metrics.{json,md}
ml/artifacts/small-model/predictions_test.jsonl
ml/artifacts/small-model/training_state.json
ml/artifacts/qwen/segment-000/           QLoRA notebook + input bundle
ml/artifacts/qwen/qwen_run.json          verdict, blocker, probe
ml/artifacts/qwen/gpu_probe_run.json     what the Kaggle session actually was
ml/artifacts/remote/nexo_gpu_probe.json  the probe itself
ml/reports/                              validation reports, per-stage manifests
```

All gitignored. `git add --dry-run` stages **49 files, every one source** — no
dataset, checkpoint, report or notebook.

### Security

The repository's own credential scanner was run over every file under
`backend/ml/` including binaries, artifacts, reports and the generated notebooks:
**97 files scanned, 0 hits.** No `KAGGLE_KEY`, no `Authorization`, no bearer
token, no hardcoded credential. The only 32+ char hex strings are sha256 content
digests. `~/.kaggle/access_token` is named in five docstrings as a path the code
must never read, and is never opened. Four personal identifiers that had been
hardcoded into test fixtures (a Kaggle username, a `C:\Users\...` home path) were
replaced with neutral fixtures and a `Path.home()` lookup — a hardcoded home path
would have gone stale on any other machine and silently stopped asserting
anything.

---

## 6. Limitations

1. **Qwen3-8B was not fine-tuned.** No accelerator was available on this account.
   The pipeline is built, wired and automated; the hardware was not there. This is
   the reason Phase 10 is not declared complete.
2. **No base-vs-fine-tuned comparison.** It requires an adapter. Producing one
   would have meant fabricating it.
3. **The corpus is synthetic.** A template-trained router is a baseline measured
   against the deterministic engines, not evidence about real demand. The score
   of 0.9675 says the intents are separable in the controlled vocabulary; it does
   not say the model will route real sentences at that rate.
4. **Scheduler state does not survive a resume** (see §4). Model, optimiser and
   RNG state do.
5. **`top_k_accuracy` was not computed** for this run, so the top-2 abstention
   headroom is unmeasured.
6. **The Kaggle GPU path is unexercised end to end.** Every stage above the
   accelerator gate is tested against a stubbed client, and the blocked path was
   exercised for real, but no QLoRA segment has ever actually run on a GPU from
   this repository.
7. **The probe is 24-hour-scoped by design.** A session that gains a GPU tomorrow
   requires re-running `--probe-remote`; the pipeline will not act on stale
   evidence.

---

## 7. What remains

- Run `make ml-probe-remote` on an account whose kernels receive a GPU.
- Run `make ml-train-qwen` — publishes, pushes, waits, downloads the adapter.
- Run `make ml-eval-qwen` — the paired base-vs-fine-tuned comparison.
- Consider fixing scheduler-state restoration in `ml/scripts/train_small_local.py`
  and the notebook's resume cell.

**Phase 11 has not been started, and Phase 10 has produced artifacts only — no
model is loaded into the running application.**