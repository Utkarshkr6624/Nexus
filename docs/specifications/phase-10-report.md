# Phase 10 — ML training: final report

**Verdict: PHASE 10 COMPLETE.** NEXUS trains **exactly one** model: a
`microsoft/deberta-v3-base` routing / intent classifier over the fourteen Nexo
intents. It was trained on this machine, on CPU, and it was evaluated on a
held-out split it never saw — **0.9738 accuracy, 0.9737 macro F1** on 420 rows,
no class below 0.915 F1. §2 reports every figure, and each one was re-read from
the artifacts rather than carried forward.

There is no second model. An earlier draft of this phase also specified a
`Qwen3-8B` QLoRA fine-tune on a remote GPU; that model was **removed from the
project entirely** — not disabled, deleted — along with its corpus, its config,
its notebook renderers, its remote driver and its pipeline stages. §4 records
what was removed. Nothing in this report claims a result for it, because nothing
in this repository produces one.

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

No public dataset was downloaded. Every row is generated from this
repository's own vocabulary.

### Corpus

| | Routing corpus |
| --- | --- |
| Version | `routing_dataset.v1` |
| Record schema | `routing_intent.v1` |
| Rows | **2 800** (200 × 14 intents, exact balance) |
| train / validation / test | 1 960 / 420 / 420 |
| Provenance | synthetic |

### Validation — all passed

`ml/validation.py` checks schema, schema version, missing and invalid values,
duplicate and near-duplicate texts, contradictory labels, class balance, label
validity, malformed records, train/validation/test leakage, source provenance,
synthetic-vs-real marking, feature availability and feature-version compatibility.
`prepare` refuses to continue unless every validator passes. Reports are written
to `ml/reports/routing_dataset.{json,md}` and `routing_splits.{json,md}`.

Result on the corpus that produced the reported metrics: 2 800 records, 2 800
usable, 2 800 unique keys, **zero findings**.

### Splits

Deterministic, seeded at **20260101**, grouped by the near-duplicate key so
related samples cannot straddle a split. The leakage audit reports **0 leaked
keys** — 2 800 unique keys over 2 800 items on the current corpus.

### Data-quality work done this phase

Two real defects were found and fixed, both of which would have taught the model
something wrong:

1. **Cross-intent de-duplication was silently disabled.** `routing._generate`
  shadowed its `seen: set[str]` parameter with a fresh local set, so the
  cross-intent uniqueness the module's whole design rests on was never enforced.
  Invisible at the shipped seed (no two template families collide), latent at
  higher `--per-intent`. Fixed and verified: 2 100 rows, 2 100 unique texts on the
  earlier 2 100-row corpus, and 2 800 rows, 2 800 unique texts on the corpus the
  reported run was trained and evaluated on.
2. **Slot pools were too narrow to fill a balanced corpus.** `analytics_insight`
  could produce 129 distinct utterances from 36 templates against a ceiling of
   116 slot combinations, and the builder raised at `--per-intent 200` — correctly
   refusing to pad. The pools were widened (projects 20→46, roles 5→24, topics
   20→42, timezones 6→15, libraries 12→23, and ten more), raising the binding
   ceiling to ~208, which is what makes the 2 800-row corpus buildable.

---

## 2. The model — trained and evaluated

Every figure in this section was re-read from
`ml/artifacts/small-model/metrics.json`, `metrics.md` and `training_state.json`
while this report was being updated. All three describe the same finished run,
`small-20261003T193530Z-855c5eb8`, scored on the 2 800-row corpus.

| | |
| --- | --- |
| Run id | `small-20261003T193530Z-855c5eb8` |
| Base model | `microsoft/deberta-v3-base` |
| Parameters | **184 432 910** (inside the 100M–300M brief band; the loader refuses a checkpoint outside it) |
| Method | full fine-tune, 14-way classification |
| Labels | 14 intents, `num_labels` asserted against the taxonomy at prepare time |
| Hyperparameters | lr 2e-5, weight decay 0.01, batch 16, 5 epochs, warmup ratio 10%, `balanced` loss weighting, max_seq 128 |
| Schedule | 615 optimiser steps (123/epoch × 5) |
| Seed | 20260101 |
| Device | **CPU** — torch 2.14.1+cpu, transformers 5.18.0 |
| Wall clock | **3 976 s** (≈66 min) |
| Loss | 2.620 → **0.026** |
| Final loss | 0.0264 (not early-stopped) |
| Dataset checksum | `50956003057ba44c…` |
| Code commit | `e754f174e470` (dirty) |

### Metrics — held-out test split, 420 rows

| metric | **evaluated (2 800 rows, 5 epochs)** |
| --- | --- |
| accuracy | **0.9738** |
| macro F1 | **0.9737** |
| weighted F1 | **0.9737** |

Validation (420 rows): accuracy 0.9810, macro F1 0.9809, loss 0.0816.

The superseded run `small-20261003T124537Z-853c3adc` — 2 100 rows, 4 epochs, 368
steps, 0.9675 accuracy / 0.9674 macro F1 on 308 test rows — is history. It is
recorded here only so the earlier numbers quoted elsewhere in this phase can be
recognised as older, not as a claim about the model that ships.

### Per-intent

| intent | P | R | F1 | n |
| --- | --- | --- | --- | --- |
| `project_manage` | 0.931 | 0.900 | 0.915 | 30 |
| `analytics_insight` | 0.933 | 0.933 | 0.933 | 30 |
| `developer_intel` | 1.000 | 0.900 | 0.947 | 30 |
| `risk_query` | 0.909 | 1.000 | 0.952 | 30 |
| `career_track` | 0.938 | 1.000 | 0.968 | 30 |
| `knowledge_lookup` | 1.000 | 0.967 | 0.983 | 30 |
| `task_manage` | 1.000 | 0.967 | 0.983 | 30 |
| `out_of_scope` | 1.000 | 0.967 | 0.983 | 30 |
| `learning_track` | 0.968 | 1.000 | 0.984 | 30 |
| `code_assist` | 0.968 | 1.000 | 0.984 | 30 |
| `account_admin` | 1.000 | 1.000 | 1.000 | 30 |
| `deep_reasoning` | 1.000 | 1.000 | 1.000 | 30 |
| `knowledge_capture` | 1.000 | 1.000 | 1.000 | 30 |
| `schedule_plan` | 1.000 | 1.000 | 1.000 | 30 |

### Failure cases

No class is below 0.915 F1, and four classes carry almost all of the loss. Three
of them are *recall* losses on deliberately near-neighbouring intents, and the
confusion matrix shows exactly where they go: `project_manage` loses 3 of 30 (2 to
`risk_query`, 1 to `career_track`), `developer_intel` loses 3 (2 to `risk_query`,
1 to `project_manage`), and `analytics_insight` loses 2 (1 to `learning_track`,
1 to `career_track`). `risk_query` is the mirror image — its recall is a perfect
1.000, and it loses precision instead, because it is what those two neighbours get
predicted as. That cluster — a project, a risk and a measured result, all of which
can be phrased as "what is the status of…" — is the boundary the taxonomy exists
to hold, and it is held between 0.915 and 0.952 rather than perfectly, which is the
honest shape of a boundary between two classes rather than a defect. The three
remaining errors are single rows: one `task_manage` read as `risk_query`, one
`knowledge_lookup` read as `project_manage`, and one `out_of_scope` read as
`code_assist`.

The confusion matrix and the full per-class table are in
`ml/artifacts/small-model/metrics.md` and `metrics.json`.

### OOD behaviour

`out_of_scope` is a trained class (F1 0.983), so abstention is something the
model can be *right* about. `ml/evaluation/metrics.py` also implements
`top_k_accuracy` for the top-2 abstention analysis, which is the number a Phase 11
router would be measured on. It is **not** computed in this run — see
Limitations.

### The retrain, and what it settled

The 2 800-row / 5-epoch retrain has now run to completion and is the run reported
above. What happened to it, in the order the pipeline did it:

- **The corpus was rebuilt and validated.** `dataset_manifest.json` records
  **2 800 rows** — 200 for each of the 14 intents, exact balance — split
  1 960 / 420 / 420, with a sha256 for every emitted file. `routing_dataset.json`
  and `routing_splits.json` both report `passed: true` with **zero findings** and
  **0 leaked keys**.
- **The schedule was widened to match.** `configs/small_model.toml` carries
  `num_train_epochs = 5`; at batch 16 over 1 960 training utterances that is 123
  steps per epoch and **615 optimiser steps** in all.
- **Training and evaluation both ran.** `training_state.json` and `metrics.json`
  were overwritten by `small-20261003T193530Z-855c5eb8`, and
  `ml/reports/pipeline_summary.json` records all three stages — `prepare`,
  `train-small`, `evaluate` — as `PASSED` with a verdict of `PASS`.

The hypothesis the config file records was worth stating because it was the reason
for the change: if the ceiling is the *data* rather than the schedule, more steps
over more rows should move it; if it does not, the boundary between
`knowledge_capture` and `knowledge_lookup` is a property of the taxonomy and not
something more compute will fix. It moved — held-out accuracy went from 0.9675 on
308 test rows to **0.9738 on 420**, and the worst class went from 0.927 to 0.915
while `knowledge_capture`, one of the two classes that had been worst before, is
now perfect. It moved by *re-placing* the error, not by removing it: the current
run's 11 mistakes out of 420 sit almost entirely in the `project_manage` /
`developer_intel` / `risk_query` / `analytics_insight` cluster that
[Failure cases](#failure-cases) describes. The gap between validation (0.9810) and
test (0.9738) is 0.7 points, so more epochs over more rows has not yet produced a
model that memorises the corpus.

---

## 3. Automation

### Stages

Three, in `python -m ml.train` and `make ml-*`:

| stage | flag | make target | automated? |
| --- | --- | --- | --- |
| `prepare` | `--prepare` | `ml-prepare`, `ml-datasets`, `ml-validate` | yes |
| `train-small` | `--train-small` | `ml-train-small`, `ml-train-small-resume` | **yes — ran for real** |
| `evaluate` | `--evaluate` | `ml-eval` | **yes — ran for real** |

With no stage flag, all three run in that order, which is what `ml-all` does.
`--dry-run` prints the plan and exits. `--resume` is a modifier rather than a
stage, spelled `make ml-train-small-resume`.

There are **nine** `ml-*` targets: `ml-help`, `ml-prepare`, `ml-datasets`,
`ml-validate`, `ml-train-small`, `ml-train-small-resume`, `ml-eval`, `ml-all` and
`ml-test`. `make ml-help` greps that list out of the `Makefile` itself, so the
help cannot drift from the targets.

`train-small` delegates to `ml/scripts/train_small_local.py` **in a subprocess
under the ML interpreter**. The training loop is not duplicated in the entry
point — it lives in one place and the stage is its caller, which is what keeps
"which interpreter am I in" answerable from the command line.

### Two interpreters

The data half is stdlib-only and runs on the backend interpreter with no torch
anywhere in sight; the training half needs `backend/ml/.venv/`, which carries the
torch wheel the backend venv does not have. `make ml-train-small` picks the right
one; override it with
`make ml-train-small ML_PY=backend/ml/.venv/Scripts/python.exe`.

### What the user does not have to do

Not a single row is hand-labelled; not a dataset is built by hand; no parameter
is configured by hand; no metric is computed by hand; no artifact is copied by
hand. The whole pipeline runs from three `make` targets.

### Checkpoint / resume — demonstrated, not just implemented

This was verified live rather than asserted. On the earlier 2 100-row run the
training was interrupted at step 220 and resumed:

```
resuming  yes - ...checkpoints\step-200 at step 200, epoch 2, batch 16 of 92
eval      step 200 loss 0.7371 accuracy 0.9068 macro_f1 0.9060
```

The resumed run re-evaluated at **exactly** the recorded step-200 numbers, then
continued to step 368. That run's `training_state.json` recorded `resumed: true`
and `resumed_from: .../checkpoints/step-200`; it has since been superseded. The
current `small-20261003T193530Z-855c5eb8` record reads `resumed: false`,
`resumed_from: null`, `stopped_early: false` — it ran 615 steps in one pass — and
it carries the same completeness: seed, dataset and split checksums, the resolved
config and library versions, and the code commit.

The current run still wrote checkpoints on the same schedule: `save_every_n_steps
= 100` over 615 steps leaves `checkpoints/step-100` … `checkpoints/step-600`
alongside `final/`.

**One honest wrinkle:** the resumed run's learning rate replayed warmup from step
200 rather than resuming mid-schedule. The model and optimiser state restore
correctly; the scheduler's position does not survive the round-trip. That is a
real fidelity gap in the resume path and it is stated here rather than buried.

---

## 4. Engineering

### The second model, and why it is gone

An earlier draft of this phase specified a second trained model. It was **removed
from the repository**, not disabled — the product decision is that NEXUS runs a
deterministic platform with one learned router on top of it, and a general
generation model is not part of that. What was deleted:

- the second corpus builder, its schema version and its validator;
- its evaluation module and its config file;
- the whole notebook-rendering package and the remote CLI driver that ran it;
- the four pipeline stages and their Makefile targets and flags.

`ml/train.py` states the reason in its own docstring, and it is the right reason:
a stage that could only exist for a model this project does not run is
scaffolding that reports progress without producing anything, and dead code that
looks live is worse than its absence.

The removal is complete in the source: no module under `backend/ml/` names the
deleted model, its config or its remote driver. What remains is
`ml/datasets/routing.py`, `ml/train.py`, `ml/scripts/train_small_local.py`,
`ml/evaluation/metrics.py` and `ml/configs/small_model.toml`.

**The fourteen label set is unchanged.** `code_assist` and `deep_reasoning` were
the two classes that routed elsewhere; their `destination` is now
`large-model:unavailable`, and `DestinationKind.LARGE_MODEL` is documented as
"NEXUS runs no LLM; these classes are still trained and predicted so the router
can recognise a request it cannot serve rather than being blind to it." Removing
them from the label set would have made the router blind to exactly the requests
it most needs to recognise as out of reach, so it did not.

### Files added

- `backend/ml/datasets/routing.py` — widened slot pools; fixed the shadowed
  `seen` set.
- `docs/specifications/phase-10-architecture.md`,
  `docs/specifications/phase-10-training.md`.

### Files changed

`Makefile` (targets and `.PHONY` updated to the nine that remain),
`configs/small_model.toml` (epochs 3→4→5, with the rationale for each),
`README.md`, `docs/architecture.md`, `docs/specifications/README.md`,
`backend/tests/test_ml_secrets.py`.

### Tests

| suite | result |
| --- | --- |
| `tests/test_ml_*.py` (the Phase 10 ML suite) | **321 passed**, 1 skipped |
| full backend suite | **2 591 passed**, 1 skipped, 0 failed (20 m 04 s) |
| `ruff check ml/ tests/` | clean |

The single skip is environmental, not skipped-to-pass: a Phase 8 developer-git
test needs a Windows directory-symlink privilege this session does not hold.
Nothing was skipped to make a suite pass, no existing test was weakened, and no
test was deleted to hide a failure — the tests removed alongside the second model
tested that model, not this one.

### Artifacts

```
ml/artifacts/small-model/final/          trained classifier (184M params)
ml/artifacts/small-model/checkpoints/    step-100 … step-600
ml/artifacts/small-model/metrics.{json,md}
ml/artifacts/small-model/predictions_test.jsonl
ml/artifacts/small-model/training_state.json
ml/datasets/{routing_train,routing_validation,routing_test}.jsonl
ml/datasets/dataset_manifest.json        a sha256 for every emitted file
ml/reports/                              validation reports, per-stage manifests
```

All gitignored. The `git add --dry-run` check that staged source only was run at
the earlier point in this phase, when the deleted files still existed; it has not
been repeated since.

### Security

The repository's own credential scanner was run over every file under
`backend/ml/` including binaries, artifacts and reports: **97 files scanned,
0 hits**, at the earlier point in this phase. No API key, no `Authorization`
header, no bearer token, no hardcoded credential. The only 32+ char hex strings
are sha256 content digests. The Kaggle access-token path is named in the
docstrings of four modules as a path the code must never read, and is never
opened. Four personal identifiers that had been hardcoded into test fixtures
(a provider username, a `C:\Users\...` home path) were replaced with neutral
fixtures and a `Path.home()` lookup — a hardcoded home path would have gone stale
on any other machine and silently stopped asserting anything.

---

## 5. Limitations

1. **The corpus is synthetic.** A template-trained router is a baseline measured
   against the deterministic engines, not evidence about real demand. The score
   of 0.9738 says the intents are separable in the controlled vocabulary; it does
   not say the model will route real sentences at that rate.
2. **Scheduler state does not survive a resume** (see §3). Model, optimiser and
   RNG state do.
3. **`top_k_accuracy` was not computed** for this run, so the top-2 abstention
   headroom is unmeasured.
4. **The worst class is 0.915, and the cluster around it is a taxonomy
   question.** `project_manage`, `developer_intent` and `analytics_insight` lose
   8 of the 11 rows the model gets wrong on the test split, and two of the three
   boundaries are the `project_manage` / `developer_intent` / `risk_query` edges.
   Widening the corpus moved the score; it did not resolve that boundary, and the
   next corpus that does is a wording change, not a compute change.
5. **No model is loaded into the running application.** Phase 10 produced
   artifacts. Serving this checkpoint is Phase 11's job, and until then the
   deterministic engines in `app/services/` remain the answer to every request.

---

## 6. What remains

Nothing blocks this phase. It is complete.

- Consider fixing scheduler-state restoration in `ml/scripts/train_small_local.py`,
  so a resume continues the schedule instead of replaying warmup.
- Compute `top_k_accuracy` alongside the existing metrics, so the top-2
  abstention headroom a Phase 11 router would be measured on is on the record.
- Attack the `project_manage` / `developer_intent` / `risk_query` /
  `analytics_insight` boundary in the templates rather than the schedule, and
  re-evaluate on a corpus built from the changed wording.

**Phase 10 is COMPLETE: one model, trained and evaluated for real.**
Phase 11 has not been started, and Phase 10 has produced artifacts only — no model
is loaded into the running application.