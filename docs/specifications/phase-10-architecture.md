# Phase 10 — ML training: architecture

Phase 10 produces **artifacts**. It trains one model, evaluates it, checkpoints it,
and writes down everything needed to reproduce the run. It does not load a model into
the running application, and nothing in `backend/ml/` sits on a request path. Serving
what this phase produces is Phase 11's job, and until then the deterministic engines
in `app/services/` remain the fallback that the learned router is measured against.

---

## 1. One model, one job

| | The routing classifier |
| --- | --- |
| Checkpoint | `microsoft/deberta-v3-base`, fine-tuned in full |
| Size | `parameter_count = 183000000` in the TOML, **184 432 910** as counted by the last completed run, inside the enforced band `[100M, 300M]` |
| Method | full fine-tune, 14-way sequence classification |
| Reads | **one utterance** |
| Answers | *which* of Nexo's fourteen surfaces handles it — **not** the answer itself |
| Runs on | CPU. The last completed run took `duration_seconds = 3976.359` (≈66 min) under torch 2.14.1+cpu; `make ml-all` covers the whole pipeline |
| Trained on | `routing_dataset.v1` |
| Where it goes | `ml/artifacts/small-model/` |

There is no second model. NEXUS runs **no language model of any kind**, and the
taxonomy's `DestinationKind.LARGE_MODEL` names a destination string, not a
checkpoint: `DestinationKind.LARGE_MODEL` is documented as *"NEXUS runs no LLM; these
classes are still trained and predicted so the router can recognise a request it
cannot serve rather than being blind to it."*

### Why the router exists at all

The argument for it is a latency argument, not a quality one. **Twelve of the
fourteen intents are answered by deterministic Nexo services** — eleven carry
`DestinationKind.ROUTER` and name an API router that already exists, and the
twelfth is `out_of_scope`, which is answered by abstaining. Those services are
*faster and more reliable* than any language model. Sending *"add a task called ship
the release notes"* through a generation model to reach a database write would be
strictly worse on every axis.

So the classifier is not trying to be clever. It is a **cheap front door**: it
recognises the cheap intents cheaply, and its real job is to refuse to force-fit a
request onto a service that cannot serve it. That is also why `out_of_scope` is a
trained class rather than a dropped row — abstention is something the model can be
*right* about, and the evaluation can score it.

### What was removed, and why it was removed rather than disabled

An earlier draft of this phase carried a second model: an 8B Qwen checkpoint
fine-tuned with QLoRA on remote GPU kernels pushed through a Kaggle CLI driver,
with its own notebook renderers, evaluation rubric, four extra pipeline stages and
four extra Make targets. **All of it was deleted** — the corpus generator, the
config, the driver, the renderers, the evaluator, the test modules and the stages.

The reason is in `ml/train.py`'s own docstring: *dead code that looks live is worse
than its absence.* A stage that could only exist for a model this project does not
run reports progress without producing anything, and a `BLOCKED` verdict naming a
GPU this machine does not have is a status line about someone else's hardware, not
about NEXUS. The stages were removed, not left disabled, and the report below
describes only what is left.

---

## 2. The intent taxonomy is Nexo's, not a generic chatbot's

`nexo_intents.v1` (`ml/datasets/taxonomy.py`) holds 14 intents. Each carries a
description, keywords, exemplars, and the destination it routes to. The
destinations are harvested from the application's own routers by
`ml/datasets/capabilities.py`, which **parses `app/` as source and never imports
it** — so the taxonomy cannot drift away from what the product actually does
without the corpus shrinking visibly. The last `prepare` harvested **185 routes
across 19 domains**, plus the 12 recommendation types, 7 risk types, 11 permissions
and 62 activity events that fill the templates' slot pools.

| # | Intent | Routes to | Kind |
| --- | --- | --- | --- |
| 0 | `task_manage` | `api/v1/tasks` | router |
| 1 | `project_manage` | `api/v1/projects` | router |
| 2 | `schedule_plan` | `api/v1/planner` | router |
| 3 | `knowledge_capture` | `api/v1/knowledge` | router |
| 4 | `knowledge_lookup` | `api/v1/knowledge` | router |
| 5 | `analytics_insight` | `api/v1/analytics` | router |
| 6 | `risk_query` | `api/v1/risks` | router |
| 7 | `developer_intel` | `api/v1/developer` | router |
| 8 | `learning_track` | `api/v1/learning` | router |
| 9 | `career_track` | `api/v1/career` | router |
| 10 | `account_admin` | `api/v1/users` | router |
| 11 | `code_assist` | `large-model:unavailable` | `LARGE_MODEL` |
| 12 | `deep_reasoning` | `large-model:unavailable` | `LARGE_MODEL` |
| 13 | `out_of_scope` | `abstain` (deterministic fallback) | `FALLBACK` |

### The two classes with no server behind them

`code_assist` and `deep_reasoning` are the only classes carrying
`DestinationKind.LARGE_MODEL`, and their destination string is
`large-model:unavailable`. **NEXUS runs no LLM**, so nothing will ever be waiting
at that address — the string says so at the point of use rather than in a footnote.

They are kept in the label set anyway, and that is a deliberate decision rather than
a leftover:

- **A router that is blind to them is a router that guesses.** A code question or a
  design argument lands nearest to some router class, and a wrong write into the
  user's calendar is worse than an admitted gap. Naming the class lets the runtime
  say plainly that NEXUS cannot serve it.
- **The label set stays closed and stable.** Deleting two of fourteen classes would
  renumber the head, change the taxonomy version, and invalidate every checkpoint
  and manifest already written. They are also what a Phase 11 router would escalate
  to *if* generation were ever adopted — the taxonomy is data, and data can be
  changed on purpose later.
- **They keep their own corpus share.** They stay heavy in the corpus —
  multi-step, technical, design-argument utterances — because widening them to
  include trivia is exactly the failure the deterministic-first rule exists to
  prevent.

`prepare` refuses to build anything if `small_model.toml`'s `num_labels` (14)
disagrees with `len(INTENT_NAMES)`, and refuses again if the label map and the
taxonomy name different sets. A head sized for the wrong count does not fail loudly
— it just never predicts the missing class.

---

## 3. Adjacent intents are kept genuinely separable

This is where a template corpus usually cheats, so the generator is built around
the separations rather than around volume:

- `task_manage` changes the task; `schedule_plan` places work in time;
  `project_manage` changes the container both live in.
- `knowledge_capture` writes into the knowledge base; `knowledge_lookup` reads
  back out of it.
- `analytics_insight` reports measured history; `risk_query` reports what is about
  to go wrong.
- `learning_track` is study against a goal; `career_track` is standing.

Each intent gets its own template families, and every rendered candidate is
checked against everything emitted so far via `normalize_text`, so **no utterance
can carry two labels**. The rules that make this hold: no shared slot pool between
intents that must not bleed into each other (the activity-feed vocabulary is split
per intent for exactly this reason), no word in a template that would move it into
a neighbouring class, and a refusal to pad — a template/slot combination may be
drawn once only, so the corpus cannot fill its quota with one sentence in
different decorations.

When a family cannot produce enough distinct utterances to satisfy the requested
balance, the builder **raises** rather than returning a lopsided corpus. That
failure is how the `project` slot pool was found to be too small to support
`--per-intent 200`, and expanding the pools is the only acceptable fix.

The corpus is **synthetic, and says so on every row**. NEXUS is a single-user
personal system, so there is no transcript to sample; what the generator produces
is controlled linguistic coverage over the real capability vocabulary. Each row
carries `provenance = SYNTHETIC`, and the manifest aggregates that breakdown
explicitly — the day a hand-written row enters a corpus, it will show up.

---

## 4. Package layout, as it is

```
backend/ml/
├── train.py                  the entry point; three selectable stages
├── validation.py             every dataset validator, and assert_clean()
├── configs/
│   └── small_model.toml      the classifier: architecture and hyperparameters
├── datasets/
│   ├── capabilities.py       parses app/ for routes, entities, closed vocabularies
│   ├── taxonomy.py           the 14 intents, their specs and routing destinations
│   ├── schema.py             record schemas, Provenance, canonical JSON/JSONL IO
│   ├── routing.py            the routing corpus generator
│   └── features.py           Phase 9 feature-vector semantics (null ≠ zero)
├── preprocessing/
│   ├── normalize.py          text normalisation, near-duplicate keys, credential scan
│   └── splits.py             deterministic, leakage-free, stratified splitting
├── training/
│   ├── config.py             pydantic schema over the TOML
│   ├── checkpoint.py         save/load/resume with provenance checks
│   └── manifest.py           the run manifest every stage emits
├── evaluation/
│   └── metrics.py            stdlib confusion matrix, precision/recall/F1
├── scripts/
│   ├── train_small_local.py  the classifier training loop (torch lives here)
│   └── run_official_training.sh   the three-stage CPU run, scripted end to end
├── datasets/*.jsonl          the prepared routing_train/validation/test splits
├── artifacts/                small-model/ — checkpoints, final weights, metrics
└── reports/                  validation reports, run manifests, pipeline summary
```

### The stdlib half and the torch half

`datasets/`, `preprocessing/`, `evaluation/metrics.py`, `validation.py`,
`training/checkpoint.py` and `training/manifest.py` are **standard library only**,
and so is `train.py` itself — the orchestrator never imports torch. The torch
footprint is **exactly one file**: `ml/scripts/train_small_local.py`, and even there
every `import torch` / `import transformers` sits *inside a function*, because the
test suite collects the whole `ml` package under the backend interpreter and a
module-scope import would break collection on an interpreter that cannot have
torch.

That split is not tidiness:

- **A contributor can build, validate and split a dataset on a bare interpreter.**
  No wheelhouse stands between a new template family and a test run.
- **Metrics are hand-written for the same reason.** `evaluation/metrics.py`
  computes its confusion matrix, accuracy, macro/weighted F1 and per-class table
  without `sklearn`, because the backend's pinned requirements carry no scientific
  stack and Phase 10 does not add one.
- **torch lives in exactly one environment** (`backend/ml/.venv`), and the trainer
  runs there as a subprocess under the backend interpreter's orchestration. Which
  interpreter you are in is answered by the command line, not by reading code.

---

## 5. The three stages

`python -m ml.train [--flag]…`, or `make ml-*`. Stage flags select a **set**; the
stages always run in pipeline order.

| Stage | Flag | What it does |
| --- | --- | --- |
| `prepare` | `--prepare` | Harvest the inventory, cross-check the label count, build the routing corpus, validate, split without leakage, write the manifest |
| `train-small` | `--train-small` | Fine-tune the classifier, in a subprocess under the ML interpreter |
| `evaluate` | `--evaluate` | Score the classifier on the held-out test split |

The other flags are not stages: `--all` (the default, and what a bare invocation
gets), `--resume` (a modifier that only means anything alongside `--train-small`),
`--seed` (default `20260101`), `--per-intent` (default 200),
`--config-dir`, `--datasets-dir`, `--artifacts-dir`, `--reports-dir`, `--dry-run`
and `--verbose`.

`make ml-*` wraps the same three stages in nine targets: `ml-help`, `ml-prepare`,
`ml-datasets`, `ml-validate`, `ml-train-small`, `ml-train-small-resume`, `ml-eval`,
`ml-all` and `ml-test`. The first three map one-for-one onto a stage flag; the rest
are an alias, a report printer, a resume wrapper, a composite and a test runner.
`ml-all` is `ml-prepare` → `ml-train-small` → `ml-eval`, which is now the whole
pipeline rather than a subset of one.

`prepare` cross-checks the label count against the taxonomy, refuses to continue
unless every validator passes, splits without leakage, and writes a
`dataset_manifest.json` carrying a sha256 for every file it emitted. It is
**stdlib-only end to end** and runs on the backend interpreter with no torch in
sight.

`train-small` does **not** reimplement the training loop. It delegates to
`ml.scripts.train_small_local` in a subprocess, because torch belongs to exactly
one environment in this repository and the orchestrator does not have it. Two
copies of the same optimiser in two files is two chances to drift.

`--resume` restores the furthest checkpoint under
`artifacts/small-model/checkpoints/` after checking it belongs to the current data
(same checksum, same dataset version). Without it, a stage that finds a resumable
checkpoint **says so and starts fresh**, so the run's provenance is exactly what was
asked for. Nothing is deleted on either path.

### BLOCKED is still a real outcome

`StageStatus` has three members, not two. `BLOCKED` means "this cannot run here,
and here is exactly why". With one model the case is now a simple and honest one:
`evaluate` returns `BLOCKED` when there is no trained model at
`artifacts/small-model/final/` or no prepared test split, so a first `--evaluate`
on a fresh clone explains what to run instead and exits 0. It is distinct from
`FAILED`, which means something is broken and needs fixing.

---

## 6. What the last completed run looked like

The last completed run is what the repository's artifacts record, and it is quoted
here so the numbers below are comparable rather than aspirational:

| | |
| --- | --- |
| Corpus | 2 800 rows — 200 per intent × 14 |
| Schedule | 5 epochs, 615 optimiser steps, 123 per epoch |
| Splits | 1 960 train / 420 validation, 420 held-out test |
| Device | CPU; `duration_seconds = 3976.359` |
| Training loss | 2.6196 → 0.0264 |
| Validation | accuracy 0.9810, macro F1 0.9809 (420 rows) |
| Test accuracy | **0.9738** |
| Test macro F1 | **0.9737** (weighted F1 0.9737) |
| Worst per-intent F1 | **0.9153** — `project_manage`; then `analytics_insight` 0.9333, `developer_intel` 0.9474 and `risk_query` 0.9524, 30 rows of support each |

The confusion matrix in `ml/artifacts/small-model/metrics.md` shows where the remaining
eleven errors sit across the 420 test rows: `project_manage` losing two rows to
`risk_query` and one to `career_track`, `developer_intel` losing two to
`analytics_insight` and one to `project_manage`, `analytics_insight` losing one each to
`learning_track` and `career_track`, and one row each going `task_manage` → `risk_query`,
`knowledge_lookup` → `project_manage` and `out_of_scope` → `code_assist`.

That answers the question the `small_model.toml` comment framed when it moved to five
epochs. The pair it named as the open boundary — `knowledge_capture` and
`knowledge_lookup` — now scores **1.0000** and **0.9831**, and the single
`knowledge_lookup` miss goes to `project_manage`, not to its neighbour. The ceiling was
the corpus and the schedule, not a property of the taxonomy. What is left is spread
across the adjacency that §3 describes, which is where template vocabulary is the lever.

**The run above is the retrain.** The 2 800-row corpus and the fifth epoch the config
asks for have been executed and recorded: `ml/artifacts/small-model/training_state.json`
carries `run_id small-20261003T193530Z-855c5eb8`, `epochs 5`, `steps 615`,
`steps_per_epoch 123`, `train_rows 1960`, `validation_rows 420`, `device cpu`,
`first_loss 2.6196`, `final_loss 0.0264`, and `ml/reports/pipeline_summary.json` records
all three stages `PASSED` under a verdict of `PASS`.

The run it replaced — a 2 100-row corpus, 4 epochs, 368 steps, scoring 0.9675 accuracy
and 0.9674 macro F1 on 308 rows — is **superseded**. It is quoted here only as the
history of how the corpus size and the epoch count were chosen; it is not the current
model's performance.

---

## 7. What this phase deliberately does not do

Phase 10 ends at artifacts. Specifically, out of scope here and belonging to
later phases:

- **Phase 11** — loading any of this into the running application, a model
  registry, or a serving path. Nothing in `backend/ml/` imports `app`, and nothing
  there is importable from a request handler.
- **Phase 12** — Ollama, voice, speech-to-text, text-to-speech.
- **Phase 13** — the Command Center, web/internet tools, browser execution.
- **Phase 14** — final polish.

The deterministic engines in `app/services/` are unchanged and remain the
fallback. A classifier that cannot be trusted is worse than the rule that always
runs.

---

## 8. Where to read next

- [`phase-10-training.md`](./phase-10-training.md) — corpora, validation, splits,
  every hyperparameter, checkpoint/resume, troubleshooting.
- [`phase-10-report.md`](./phase-10-report.md) — what shipped, and what was found.
- `ml/artifacts/small-model/metrics.md` — the classifier scorecard for the last
  completed run, including the per-class table and the confusion matrix.
- `ml/artifacts/small-model/training_state.json` — the run record: steps, loss
  curve, per-split checksums, environment, wall clock.
- `ml/reports/manifests/` — one manifest per stage per run, JSON and Markdown.

> `ml/reports/qwen_splits.*` are outputs of the removed corpus generator. Nothing
> regenerates them and nothing reads them; they are historical files, not part of
> this pipeline.