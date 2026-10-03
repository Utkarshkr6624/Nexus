# Phase 10 — ML training: architecture

Phase 10 produces **artifacts**. It trains two models, evaluates them, checkpoints
them, and writes down everything needed to reproduce the run. It does not load a
model into the running application, and nothing in `backend/ml/` sits on a request
path. Serving what this phase produces is Phase 11's job, and until then the
deterministic engines in `app/services/` remain the fallback that any learned
component is measured against.

---

## 1. Two models, two jobs, one rule about which is used

| | Model A — routing classifier | Model B — `Qwen3-8B` |
| --- | --- | --- |
| Checkpoint | `microsoft/deberta-v3-base` (≈184M params) | `Qwen/Qwen3-8B` + LoRA adapter |
| Method | full fine-tune, 14-way classification | QLoRA (4-bit NF4 base + LoRA) |
| Answers | *which* capability should handle this | *what* is the right answer |
| Runs on | CPU; the exact wall clock of the current run is recorded in `ml/artifacts/small-model/training_state.json` as `duration_seconds` | a remote GPU, ≥6 GiB VRAM |
| Trained on | `routing_dataset.v1`, 2 100 rows | `qwen_dataset.v1`, 400 rows |
| Where it goes | `ml/artifacts/small-model/` | `ml/artifacts/qwen/` |

The rule that decides between them is not a quality comparison — it is a latency
one. Twelve of the fourteen intents are answered by deterministic Nexo services,
and those services are *faster and more reliable* than any language model.
Sending "add a task called ship the release notes" through an 8B model to reach a
database write would be strictly worse on every axis. The classifier exists to
avoid that: it recognises the cheap intents cheaply and escalates only the two
classes that genuinely need generation.

`code_assist` and `deep_reasoning` are the only intents whose destination is the
large model, and they are correspondingly heavy in the corpus — multi-step,
technical, design-argument utterances. `out_of_scope` is the mirror: weather,
sport, travel, small talk and near-miss non-Nexo asks, trained explicitly so that
abstention is a class the model can be *right* about rather than a row that was
quietly dropped.

---

## 2. The intent taxonomy is Nexo's, not a generic chatbot's

`nexo_intents.v1` (`ml/datasets/taxonomy.py`) holds 14 intents. Each carries a
description, keywords, exemplars, and the destination it routes to. The
destinations are harvested from the application's own routers by
`ml/datasets/capabilities.py`, which **parses `app/` as source and never imports
it** — so the taxonomy cannot drift away from what the product actually does
without the corpus shrinking visibly.

| # | Intent | Routes to |
| --- | --- | --- |
| 0 | `task_manage` | `api/v1/tasks` |
| 1 | `project_manage` | `api/v1/projects` |
| 2 | `schedule_plan` | `api/v1/planner` |
| 3 | `knowledge_capture` | `api/v1/knowledge` |
| 4 | `knowledge_lookup` | `api/v1/knowledge` |
| 5 | `analytics_insight` | `api/v1/analytics` |
| 6 | `risk_query` | `api/v1/risks` |
| 7 | `developer_intel` | `api/v1/developer` |
| 8 | `learning_track` | `api/v1/learning` |
| 9 | `career_track` | `api/v1/career` |
| 10 | `account_admin` | `api/v1/users` |
| 11 | `code_assist` | **`qwen3-8b`** |
| 12 | `deep_reasoning` | **`qwen3-8b`** |
| 13 | `out_of_scope` | `abstain` (deterministic fallback) |

`prepare` refuses to build anything if `small_model.toml`'s `num_labels`
disagrees with `len(INTENT_NAMES)`. A head sized for the wrong count does not
fail loudly — it just never predicts the missing class.

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

---

## 4. Package layout

```
backend/ml/
├── train.py                  the entry point; seven selectable stages
├── validation.py             every dataset validator, and assert_clean()
├── configs/
│   ├── small_model.toml      the classifier: architecture and hyperparameters
│   └── qwen_qlora.toml       the QLoRA run: quantisation, LoRA, segmentation
├── datasets/
│   ├── capabilities.py       parses app/ for routes, entities, closed vocabularies
│   ├── taxonomy.py           the 14 intents, their specs and routing destinations
│   ├── schema.py             record schemas, Provenance, canonical JSON/JSONL IO
│   ├── routing.py            the routing corpus generator
│   ├── qwen_sft.py           the Nexo-specific instruction corpus generator
│   └── features.py           Phase 9 feature-vector semantics (null ≠ zero)
├── preprocessing/
│   ├── normalize.py          text normalisation, near-duplicate keys, credential scan
│   └── splits.py             deterministic, leakage-free, stratified splitting
├── training/
│   ├── config.py             pydantic schema over the two TOMLs
│   ├── checkpoint.py         save/load/resume with provenance checks
│   ├── manifest.py           the run manifest every stage emits
│   └── remote.py             the Kaggle CLI driver (credential-blind)
├── kaggle/
│   └── notebook.py           renders the four notebooks this phase can push
├── evaluation/
│   ├── metrics.py            stdlib confusion matrix, precision/recall/F1
│   └── qwen_eval.py          the deterministic base-vs-fine-tuned rubric
├── scripts/
│   └── train_small_local.py  the classifier training loop (torch lives here)
├── artifacts/                models, checkpoints, adapters, downloaded kernel output
└── reports/                  validation reports, run manifests, pipeline summary
```

`backend/ml/datasets/`, `preprocessing/`, `evaluation/metrics.py`,
`validation.py`, `training/checkpoint.py`, `training/remote.py` and
`kaggle/notebook.py` are **standard library only**. Nothing in that set imports
torch, and that is not tidiness: it means a contributor can build a dataset,
validate it, split it and score a stored checkpoint without a multi-gigabyte
wheelhouse standing between them and a test run. The torch half is exactly two
files — the classifier training loop and the notebook cell sources that run
remotely.

---

## 5. The seven stages

`python -m ml.train [--flag]…`, or `make ml-*`. Stage flags select a **set**;
the stages always run in pipeline order.

| Stage | Flag | What it does |
| --- | --- | --- |
| `prepare` | `--prepare` | Harvest the inventory, build both corpora, validate, split, write the manifest |
| `train-small` | `--train-small` | Fine-tune the classifier, in a subprocess under the ML interpreter |
| `evaluate` | `--evaluate` | Score the classifier on the held-out test split |
| `train-qwen` | `--train-qwen` | Push one QLoRA segment to a verified accelerator |
| `probe-remote` | `--probe-remote` | Push a one-cell kernel that records what the session actually was |
| `eval-qwen` | `--eval-qwen` | Compare the base model against the adapter, paired |
| `qwen-status` | `--qwen-status` | Report account, accelerator verdict, kernel state, Phase 10 kernels |

The other flags are not stages: `--all` (the default, and what a bare invocation gets),
`--resume` (a modifier that only means anything alongside `--train-small`), `--seed`,
`--per-intent`, `--per-category`, `--config-dir`, `--datasets-dir`, `--artifacts-dir`,
`--reports-dir`, `--dry-run` and `--verbose`.

`make ml-*` wraps the same seven flags in twelve targets: `ml-help`, `ml-prepare`,
`ml-datasets`, `ml-validate`, `ml-train-small`, `ml-train-small-resume`, `ml-eval`,
`ml-train-qwen`, `ml-probe-remote`, `ml-qwen-status`, `ml-all` and `ml-test`. Seven map
one-for-one onto a stage flag; the rest are aliases, composites or helpers. `--eval-qwen`
is the one stage flag with no target of its own and is run as
`python -m ml.train --eval-qwen`, and `ml-all` is `ml-prepare` → `ml-train-small` →
`ml-eval` — deliberately without the Qwen stages, which cannot run on this machine.

`prepare` cross-checks the label count against the taxonomy, refuses to continue
unless every validator passes, splits without leakage, and writes a
`dataset_manifest.json` carrying a sha256 for every file it emitted.

`train-small` does **not** reimplement the training loop. It delegates to
`ml.scripts.train_small_local` in a subprocess, because torch belongs to exactly
one environment in this repository and the orchestrator does not have it. Two
copies of the same optimiser in two files is two chances to drift.

### BLOCKED is a real outcome

`StageStatus` has three members, not two. `BLOCKED` means "this cannot run here,
and here is exactly why, with the evidence attached". It is distinct from
`FAILED` (something is broken and needs fixing) and it is what the Qwen stages
record on hardware that cannot hold an 8B model. The blocked path still writes
the notebook and its input bundle, so a blocked run is one push away on hardware
that can hold the model rather than a run that has to be rebuilt.

---

## 6. A GPU in the metadata is not a GPU

Kaggle records `enable_gpu` and a `machine_shape` for the accelerator a kernel
*requested*, and echoes both back whether or not a device was attached. Reporting
"GPU enabled" on the strength of kernel metadata is reporting the request.

`--probe-remote` exists to make that checkable. It pushes a one-cell notebook that
asks the machine four questions — is there a `/dev/nvidia*` device node, does
`nvidia-smi` answer, does `torch.cuda.is_available()` return true, does DNS
resolve — waits for the kernel to finish, downloads its answer, and stores it in
`ml/artifacts/remote/nexo_gpu_probe.json`.

`ml.training.remote.verify_gpu_available` will return `True` **only** with a probe
behind it, and refuses a probe older than 24 hours because accelerator availability
is a property of the machine rather than of the account. Kernel metadata is not
admissible evidence.

---

## 7. What this phase deliberately does not do

Phase 10 ends at artifacts. Specifically, out of scope here and belonging to
later phases:

- **Phase 11** — loading any of this into the running application, a model
  registry, or a serving path. Nothing in `backend/ml/` imports `app`, and
  nothing there is importable from a request handler.
- **Phase 12** — Ollama, voice, speech-to-text, text-to-speech.
- **Phase 13** — the Command Center, web/internet tools, browser execution.
- **Phase 14** — final polish.

The deterministic engines in `app/services/` are unchanged and remain the
fallback. A classifier that cannot be trusted is worse than the rule that always
runs.

---

## 8. Where to read next

- [`phase-10-training.md`](./phase-10-training.md) — corpora, validation, splits,
  every hyperparameter, checkpoint/resume, the Kaggle workflow, troubleshooting.
- `ml/artifacts/small-model/metrics.md` — the classifier scorecard for the
  current run, including the confusion matrix.
- `ml/artifacts/qwen/qwen_run.json` — the Qwen run record: status, blocker, and
  the accelerator probe behind the verdict.
- `ml/reports/manifests/` — one manifest per stage per run.
