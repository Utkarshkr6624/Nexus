# Phase 10 — ML Training: the operational half

**Status:** documents the pipeline as it exists in `backend/ml/`. NEXUS runs **one** trained
model — a `deberta-v3-base` intent router over fourteen Nexo surfaces — and the pipeline has
**three** stages: `--prepare`, `--train-small`, `--evaluate`.
**Scope:** dataset construction, validation, splitting, the model configuration, the local
training loop, checkpointing and resume, reproducibility, troubleshooting, and the `ml-*`
Make targets.

This document deliberately covers the **operational** half of Phase 10 — the machinery
that produces and trains on data. It does not restate the product intent (why a router
exists, why two classes exist that NEXUS cannot serve) or the architecture (how a trained
checkpoint reaches a request); those are in [`../architecture.md`](../architecture.md).
Where this document disagrees with the code, the code is what runs — and the disagreement
is written down here rather than left for someone to discover.

> **What is not here.** An earlier draft of this pipeline carried a second, remotely trained
> model and the machinery around it: a second corpus generator, its configuration file, the
> remote CLI driver, the notebook renderers, the accelerator probe, and four further stages.
> All of that was **removed with the model, not disabled**, so this document no longer
> describes it and no command in it reaches for it. What remains is the single-model
> pipeline documented below.

> **AGENTS.md:** no `AGENTS.md` exists at the repository root or under `backend/` at the
> time of writing, so there is no project-supplied agent guidance to reconcile against.

---

## 1. The shape of the pipeline, and the seam inside it

Everything under `backend/ml/` is **stdlib-only** — no `torch`, no `transformers`, no
`pydantic` outside the config module — except the one place that genuinely needs them.
That split is not stylistic; it is what keeps the backend test suite runnable without a
multi-hundred-megabyte download.

| Interpreter | Lives at | What runs there |
| --- | --- | --- |
| Backend venv | `backend/.venv/Scripts/python.exe` | the orchestrator (`ml.train`), all of `ml.datasets`, `ml.validation`, `ml.preprocessing`, `ml.training.checkpoint`, the TOML loader |
| ML venv | `backend/ml/.venv/Scripts/python.exe` | `ml/scripts/train_small_local.py` — the only loop that calls `model.backward()` |

The entry point is `python -m ml.train`, run from `backend/`:

```bash
cd backend
../.venv/Scripts/python.exe -m ml.train --prepare          # stdlib interpreter is enough
../ml/.venv/Scripts/python.exe -m ml.scripts.train_small_local   # torch lives only here
```

The orchestrator bridges the seam itself. `--train-small` does not import the trainer —
it shells out to `ml.scripts.train_small_local` **in a subprocess under the ML
interpreter** (`ml/train.py`, `stage_train_small`). That is what makes "which
interpreter am I in?" answerable from the command line instead of from reading imports.
`--evaluate` crosses the same seam for inference, running a short inline script under the
ML interpreter and scoring its output here, so the metrics cannot disagree with the
trainer's own numbers.

There are **three** selectable stages — `prepare`, `train-small`, `evaluate`, each with a
flag of the same name. Stage flags select a **set, not a sequence**: asking for
`--evaluate --prepare` runs both, in pipeline order (`ml/train.py`, `STAGES`).
`train-small` precedes `evaluate` because the report is about a model that exists.

**Stage statuses** are `PASSED` / `FAILED` / `BLOCKED`. `BLOCKED` exists so that "this
cannot run here, and here is exactly why" is not forced to masquerade as either success or
failure: `--evaluate` on a clone with nothing trained reports `BLOCKED` and exits 0.

`ml/scripts/train_small_local.py` reinforces the boundary in the other direction: every
`torch` import is inside a function, because pytest collects the whole `ml` package
under the backend interpreter and a module-scope `import torch` would break collection
on an interpreter that cannot have torch.

### 1.1 What each stage does

**`prepare` (`stage_prepare`).** Harvest the capability inventory from `app/` by parsing
the source, cross-check `num_labels` against the taxonomy (a mismatch raises
`PipelineError` and refuses to write splits), build the routing corpus, validate it,
`assert_clean`, split it without leakage, audit the partition, and write the three JSONL
splits, `label_map.json`, `capability_inventory.json`, `splits.json` and
`dataset_manifest.json`. Stdlib-only end to end.

**`train-small` (`stage_train_small`).** Resolve the torch interpreter, delegate to
`ml.scripts.train_small_local` in a subprocess, and report what the trainer wrote. The
training loop is not duplicated here: it lives in one place and this stage is the caller.

**`evaluate` (`stage_evaluate`).** Score the held-out test split through
`ml.evaluation.metrics.evaluate`. Missing model → `BLOCKED` (*"evaluate scores a
checkpoint, it does not train one"*); missing test split → `BLOCKED`.

Every stage writes a timestamped manifest to `ml/reports/manifests/` carrying a sha256 for
every file it claims to have produced.

---

## 2. Dataset construction

### 2.1 Why every row is synthetic

There is no corpus of real user utterances to sample from, and this is not a
shortage that more effort would fix. NEXUS is a single-user, self-hosted personal
system: there is exactly one user, so there is no population from which to sample
phrasing. Scraping a chatbot benchmark would produce a label space that does not match
the 185 routes that have to serve it. Every row in the corpus is therefore generated
deterministically from a template plus a real capability vocabulary, and every row
carries `provenance=Provenance.SYNTHETIC` because that is exactly what it is.

Three consequences are load-bearing and are enforced in code, not asserted in prose:

1. **The manifest states it.** `stage_prepare` writes an explicit `provenance` histogram
   per corpus (`_provenance_breakdown`, `ml/train.py:537`). The day a hand-written row
   enters a corpus it shows up there as a second key. The current run reads
   `{"synthetic": 2800}`.
2. **A router fitted on this belongs in its manifest as a template-trained baseline.**
   Generated text proxies for the distribution of phrasing; it is not evidence about a
   user. `ml/datasets/routing.py`'s module docstring makes this a load-bearing claim
   about how the artifact may be described downstream.
3. **Entity nouns are plausible, not harvested.** Task titles, project names, the
   artifact someone saved — there is no seed copy of a real user's data in this
   repository, so inventing them is the only honest option. A "real" title lifted from a
   repository fixture would be a fiction with extra steps.

What synthetic generation *can* give you, and what the modules use it for: controlled
coverage of the real closed vocabularies (the fourteen intents, the twelve
`RecommendationType` members, the seven risk types, the eleven permissions, the activity
events), reproducible from a seed, and auditable — you can read the template that
produced any row and know exactly which words carried the label.

### 2.2 Grounding: the capability inventory

`ml/datasets/capabilities.py` harvests the real product surface by **parsing the
application source with `ast` — it never imports `app`**. `stage_prepare` runs it
against `backend/app/` and writes `ml/datasets/capability_inventory.json`. The current
inventory: **185 routes, 19 domains, 12 recommendation types, 7 risk types, 11
permissions, 62 activity events**, version `nexo_capabilities.v1`.

The inventory reaches the generator as a `CapabilityInventory` argument. `routing.py`
**filters its closed vocabularies against it**. Every field of `_Vocabulary` is therefore a
subset of what the application actually declares, and an **empty field is a signal, not a
fallback to invention** — a renamed enum stops generation rather than teaching a stale
label. When no inventory is supplied the module falls back to the literals declared in
`routing.py` itself, so it can be called without `app/` in front of you; the pipeline
always passes the live one.

### 2.3 The routing corpus — `ml/datasets/routing.py`

**Version:** `routing_dataset.v1` (versions the *generator*, distinct from the
`routing_intent.v1` record schema it produces).

**What a row is.** One utterance, one intent label, a `template_id`, a `source`, and
`provenance=Provenance.SYNTHETIC`.

**Where the fourteen labels come from.** `ml/datasets/taxonomy.py`'s `INTENT_NAMES` —
the routing destination a request can land on: eleven router families, the two classes
NEXUS cannot serve (`code_assist`, `deep_reasoning`) and the abstention class
(`out_of_scope`). These are **not** the twelve `RecommendationType` members; those name
the actions a person takes and are the *vocabulary the templates are written from*.
`label_map()` indexes intents in taxonomy order, not alphabetical, because the taxonomy
order is the order the classes were chosen in — routers first, then the two unservable
classes, then abstention — and a class index that preserves that is readable in a
confusion matrix, where a head sitting next to the two classes it cannot serve tells
you something at a glance.

**The two unservable classes.** `code_assist` and `deep_reasoning` carry
`destination_kind=DestinationKind.LARGE_MODEL` and `destination="large-model:unavailable"`.
NEXUS runs no LLM. The classes are still trained and still predicted, and the enum member
says why in the source: recognising *"this request needs free-form generation that NEXUS
does not perform"* is a useful answer — the router can say so plainly instead of
force-fitting the request onto a router that cannot serve it, or dropping it silently.
Deleting them would leave the classifier blind to exactly the requests it most needs to
recognise as out of reach. **The 14-class label set is therefore unchanged**, and so are
the classifier's classes.

**Template families per intent.** Three sources, assembled in `_build_templates`:

1. `_curated()` — the hand-written exemplars already carried by `IntentSpec.examples`,
   one template per exemplar, plus four keyword-grounded shapes for all intents except
   `code_assist`, `deep_reasoning` and `out_of_scope`. Those three are excluded on
   purpose: a one-line keyword utterance is a light, generic request, and putting one
   into a class NEXUS cannot serve blurs the boundary that makes them worth predicting
   at all — the whole value of those two classes is that "I cannot answer this" is a
   narrow, recognisable decision rather than the default.
2. `_INTENT_TEMPLATES` — the hand-written families, 20–36 per intent (the fourteen counts
   are 26, 23, 24, 21, 20, 36, 33, 33, 24, 31, 35, 29, 36 and 34 in taxonomy order,
   405 templates in all), drawn from 33 slot pools (`_TASK_TITLES`, `_PROJECT_NAMES`,
   `_DATES`, `_REPOS`, `_BRANCHES`, `_LANGUAGES`, …; `concept` is an alias for `topic`
   rather than a second pool). The wording rule they were written under: state the *action
   on the entity*, and leave out any word that would move the utterance into a
   neighbouring class. No "priority" or "deadline" inside `task_manage`; no "how am I
   doing" inside `schedule_plan`.
3. `_vocabulary_templates()` — synthesised from the inventory for five intents
   (`risk_query`/`account_admin` from permissions and recommendations; `task_manage`,
   `schedule_plan`, `analytics_insight` from activity events).

**Surface variation.** `_decorate()` adds a prefix from a 10-element pool, a suffix from
a 9-element pool, a trailing question mark (35% of eligible rows), or a single
keystroke typo (10%, touching only interior characters of words ≥ 5 characters). Guards
worth knowing about, because each one exists to stop a specific corruption:

- Already-question templates get **no** frame at all — *"please which tasks are still
  open before I forget"* teaches the model to expect broken syntax.
- No prefix may precede a noun-phrase opener (`_NO_PREFIX_WORDS`).
- `"please"` in the prefix suppresses a `"please"`/`" if you can"` suffix, because
  *"please … please"* is the single most obvious tell of a generated corpus.
- `_decoration_ok()` rejects any frame containing punctuation the normaliser strips —
  a frame can never rewrite a template's meaning.
- `_ACTIVITY_INTENTS` splits the activity vocabulary **per intent** rather than pooling
  it. A shared pool lets a `task_manage` row say *"log a note being saved and move it to
  Monday"* — knowledge vocabulary inside the task class — which is exactly how a template
  corpus quietly destroys the boundary it was written to teach.

**Build mechanics.**

- **Per-intent RNG.** Each intent draws from `random.Random(seed * 1000003 + index_of_intent)`.
  Adding a template to one intent cannot reshuffle another intent's rows, and a dataset
  hash changes only in the segment that actually changed.
- **Exact balance, or failure.** `per_intent` is applied to all fourteen equally. If the
  families cannot fill the quota the builder raises rather than returning a lopsided
  corpus — the message names the count produced and the attempt budget, and tells the
  caller to lower `per_intent`.
- **Slot combinations are drawn once.** `drawn` records `(template_id, slot values)`; a
  repeat is skipped. Without it the corpus fills its quota with the same sentence wearing
  different decorations — precisely the padding the validator would then report as
  near-duplicates.
- **Attempt budget.** `max(target * 400, 10_000)` draws before the builder gives up.
- **Template integrity.** `_check_templates()` runs per intent: no duplicate
  `template_id`, no brace pair the slot parser would read as a placeholder but that is not
  a declared slot, and the declared slots must equal the used slots. `_render()` uses
  regex substitution rather than `str.format` deliberately — the code-assist templates
  contain braces of their own, and a language where `{` means placeholder makes every code
  example a syntax error.

**Volume.** `--per-intent` defaults to **200**, giving **14 × 200 = 2,800 rows** — the
figure `ml/datasets/dataset_manifest.json` records, and the corpus the current splits were
built from. The number moved during the phase and two things about it are worth being
precise about:

- **The CLI default and the library default now agree.** `build_routing_dataset` and
  `build_routing_records` both default to `per_intent=200`
  (`ml/datasets/routing.py:1979`, `:2029`), so a caller that bypasses `ml.train` gets the
  same corpus the pipeline gets. An earlier revision had the library still defaulting to
  90 while the CLI said 150; that drift is fixed.
- **200 is a chosen corpus size, not a ceiling.** The builder has no hard limit and never
  pads: the slot pools hold 2 to 43 values each (`_SLOT_VALUES`, 33 pools), and raising
  `--per-intent` simply asks for more distinct slot combinations from them. What is
  guaranteed is the *shape* of the ceiling — when the narrowest family runs dry, the
  builder raises, naming the count produced and the attempt budget, and tells the caller
  to add template families (see the attempt budget above) rather than to lower the number.

### 2.4 What the current prepare actually produced

From `ml/datasets/dataset_manifest.json` (seed `20260101`, taxonomy `nexo_intents.v1`,
generated `2026-10-03T19:35:13Z`), quoted from the file:

| | routing |
| --- | --- |
| Dataset version | `routing_dataset.v1` |
| Record schema | `routing_intent.v1` |
| Total rows | 2800 |
| Per class | 200 × 14 intents |
| Provenance | `{"synthetic": 2800}` |
| train / validation / test | 1960 / 420 / 420 |

Both reports agree with the manifest and both are clean.
`ml/reports/routing_dataset.md` records `records 2800`, `usable 2800`, `classes 14`,
`contradictions 0`, `invalid_labels 0` and **no findings**; `ml/reports/routing_splits.md`
records `leaked_keys 0`, `unique_keys 2800`, `total_items 2800` over `1960 / 420 / 420`.

Every file the prepare wrote is listed in the manifest with a **sha256** and, for JSONL,
a row count: the three split files, `capability_inventory.json`, `label_map.json` and
`splits.json`. A checkpoint records that dataset digest, and a resume refuses to continue
a run whose data changed underneath it.

---

## 3. Validation — `ml/validation.py`

The gate between building and training. Its premise: **every builder can produce
records; none can produce trustworthy records.** None of the failures it looks for
announce themselves at training time — the run converges and reports a number that is
quietly wrong.

### 3.1 The ERROR/WARNING asymmetry

This is the design, and everything else follows from it.

- **ERROR stops the run.** The data is *false*: the run would measure the wrong thing.
  A duplicate text, a contradictory label, a leaked split, a credential, a record that
  will not parse, a label outside the taxonomy, a schema version this pipeline cannot read.
- **WARNING annotates it and the run continues.** The data is *imperfect*: a real dataset
  of real requests is lopsided and repetitive, and refusing to train on lopsided data
  would refuse to train on reality. Class imbalance and near-duplicates are reported and
  attached to the artifact.

`assert_clean(*reports)` **raises** rather than returning a verdict. A caller that
ignores a returned boolean is the reason a corrupt dataset reaches a training run in the
first place.

### 3.2 The checks, by name

Every finding carries a stable `code` that a test or a pipeline step asserts on.

**Record-level, shared by the two dataset validators** (`_parse_or_flag`):

| Code | Severity | What |
| --- | --- | --- |
| `malformed_record` | ERROR | the row is not a JSON object, or its `from_dict` refused it (parse failures are grouped by message, so a systematic builder bug produces one counted finding rather than ten thousand) |
| `missing_schema_version` | ERROR | no `schema_version` declared — not coerced and not guessed at |
| `unknown_schema_version` | ERROR | a version from a revision this pipeline cannot read |
| `mismatched_schema_version` | WARNING | a readable version, but not this dataset's |
| `missing_field` | ERROR | a field the model would read as empty (`text`/`intent` for routing; `subject`/`source_schema_version` for features) |
| `credential_detected` | ERROR | credential-shaped text in a text field, **aggregated per (field, kind)** |

**Routing** (`validate_routing_dataset`, requires `known_intents` and `max_class_ratio`):

| Code | Severity | What |
| --- | --- | --- |
| `invalid_label` | ERROR | an intent outside the taxonomy — the router can only act on an intent the application knows how to execute |
| `duplicate_text` | ERROR | a text appearing verbatim more than once |
| `contradictory_label` | ERROR | one normalised text carrying more than one intent |
| `near_duplicate_text` | WARNING | groups sharing a bag of words |
| `class_imbalance` | WARNING | largest class ÷ smallest non-empty class above `max_class_ratio` (`MAX_CLASS_RATIO = 5.0` in `train.py`, a tripwire against a generator regressing, not a tuned threshold) |

Counts recorded: `records`, `usable`, `classes`, `known_intents`, `invalid_labels`,
`contradictions`, and a `class:<intent>` key per intent.

**Feature rows** (`validate_feature_dataset`) — no label, so the null contract is what
applies: `duplicate_row` (ERROR) on a subject repeated within one schema version, and
`incomplete_row` (WARNING) for rows with at least one uncomputable column. The schema's
`from_dict` already refuses the failure that matters most — a column marked unavailable
that still carries a value — because that is the one that silently reintroduces the
fabricated zero the Phases 8/9 remediation removed.

**Splits** (`validate_splits`) — see §4.3. Codes: `split_leakage` (ERROR) and
`empty_split` (WARNING).

**Free text** (`validate_no_credentials`) — the gate for the places a dataset is not: the
run manifest, and any free text a human pastes into a report. Each is assembled by hand
or from environment values, which is exactly the code path that has put a key into a log
line somewhere before.

### 3.3 A report never reproduces what it rejected

`find_credential()` returns the **kind** (`kaggle_token`, `assigned_secret`), never the
match. `_excerpt()` runs every sample — credential or not — through
`ml.preprocessing.normalize.redact` and collapses it to at most 120 characters on one
line. Findings quote at most **5** samples, because a dataset with ten thousand leaked
rows still deserves a report a human will read to the end.

This is not defensive decoration. A report is written to disk, printed into a CI log and
attached to a run manifest. A validator that echoed the secret it found would have leaked
it to all three. Redaction is applied unconditionally because there is no reason for any
excerpt to be the one place a secret survives.

### 3.4 Report shape

`ValidationReport` carries `name`, `schema_version`, `findings`, `counts` and a `passed`
flag **derived** from the findings, so a report can never claim success while holding an
error. Report version `nexo_validation.v1`. `to_dict()` and `to_markdown()` both exist;
the Markdown ends in one unambiguous `PASS`/`FAIL` line, and `stage_prepare` writes both
to `ml/reports/<stem>.{json,md}` — which is exactly what `make ml-validate` prints.

The gate is deterministic and stdlib-only: the same dataset produces the same report on a
laptop and in CI, with no dependency that could drift between them. That discipline is
not separable from §4's — a non-deterministic split makes leakage undetectable.

---

## 4. Deterministic splits — `ml/preprocessing/splits.py`

**Config version:** `nexo_splits.v1`. A v2 partition is not comparable to a v1 one even
when both are 70/15/15.

### 4.1 What leakage is here, and why it is worse than usual

A model scored on rows that leaked into its training set has been graded on recall rather
than generalisation. NEXUS has two structural sources and both are baked into how the
datasets are built:

1. **The routing set is expanded from templates**, so *"add a task"* and *"create a new
   task"* recur as dozens of paraphrases sharing one bag of words. Splitting record by
   record puts near-identical text on both sides of the boundary.
2. **Feature sets are derived per subject**, so the same developer's rows land in every
   split unless the subject is held out.

The near-duplicate key is `near_duplicate_key()` — **an order-insensitive bag of words**
(`" ".join(sorted(tokenize(text)))`). Two phrasings built from the same words collide, so
a near-duplicate cannot straddle a boundary even when the surface wording differs. Two
distinct examples sharing a rare bag of words is a false positive; that is the safe
direction, because it costs a row rather than inflating a score.

### 4.2 Why grouping precedes stratification

The two objectives fight, and the order is not a preference. Stratification balances the
label histogram by dealing records into splits; grouping forbids a duplicate family from
being split across them. If stratification ran first, grouping would have to undo its
deals and the outcome would depend on which pass ran last.

Grouping first collapses each family into one indivisible **unit**, and stratification
then balances *units* — the coarsest granularity at which the choice still exists. The
consequence is stated rather than hidden: two paraphrases of one intent form a single
unit and therefore land whole in one split, so validation and test are thinner than the
raw class counts suggest. That is the price of a number nobody can accuse of being
flattered.

### 4.3 The mechanics

- **`SplitConfig`** defaults: `train=0.7`, `validation=0.15`, `test=0.15`, `seed=20260101`,
  `group_by=True`. Fractions are **checked, not normalised** — silently rescaling
  `0.6/0.2/0.2` into `0.7/0.15/0.15` would mean the configuration in the manifest is not
  the configuration that ran.
- **Seeding.** Everything derives from `random.Random(seed)` over an input order the caller
  fixes. No global random state, no dependence on dict or set iteration order, no wall
  clock. Every tie-break resolves toward the earliest name in the canonical order
  `("train", "validation", "test")`.
- **Entry points.** `split_records()` — record by record, stratified; only for rows that
  are already independent (per-subject feature vectors). `assign_leakage_free_splits()` —
  takes a `duplicate_key_fn` that maps a **record** to its family key, and collapses
  families into units first. The pipeline uses the second for the routing corpus
  (`ml/train.py:435`, `_split_records`).
- **Quotas** are assigned by **largest remainder** per label class, so seats always sum
  exactly to the class size. The deal is then shuffled and handed out over the class's
  shuffled members, so two classes interleave and no class is systematically earlier.
- **Small classes** are answered by largest remainder rather than by an exception: a class
  of two under 0.7/0.15/0.15 has floors 1/0/0 and one seat left over, which goes to
  `train`. Spreading a class of two across validation and test would put a row in a split
  whose only content is a near-twin of a training row — a per-class score manufactured
  entirely by leakage.
- **Family label** is the majority label, ties broken by the smallest label. A family that
  mixes intents is a contradiction the builder should have caught; picking one side
  deterministically keeps the stratification accounting honest instead of dropping the
  family.
- **Empty-split repair.** If stratification leaves a split empty, one unit moves into it
  from the split holding the most units. A split stays empty when no donor can spare one,
  and `counts` reports the zero rather than the repair inventing a row.
- **Nothing is dropped.** `_finalise()` proves the partition is total — every unit placed,
  every key assigned exactly once, the assignment count equal to the input count — and
  raises `DataValidationError` otherwise. It is written as a real raise rather than an
  `assert` because `-O` strips asserts, and this is precisely a case where the process is
  already misconfigured.
- **`counts` always carries all three names, including zeros.** An absent key and a count
  of zero are different claims, and only one of them is true.

### 4.4 The audit

`validate_splits()` re-derives the partition and asks whether any near-duplicate group
appears in more than one split. `duplicate_key_fn` is **supplied by the caller rather than
hardcoded** so the audit checks the same key the splitter grouped by — if the two ever
disagree, the audit checks a different partition from the one actually used, which is
worse than not checking at all. `split_leakage` is an **ERROR**.

`stage_prepare` audits **the corpus as its own partition**, and the reason is worth
stating because it still binds: leakage is a within-partition property. Two rows that
happen to share a bag of words but belong to two different training sets are two rows
that no single model ever saw together, so pooling them into one audit reports a collision
no model can commit and no split can fix.

**Current state:** the audit PASSES. `ml/reports/routing_splits.md` —
`leaked_keys 0`, `unique_keys 2800`, `total_items 2800`, 1960/420/420.

`ml/datasets/splits.json` records `split_config_version`, the seed, the three fractions,
`group_by`, and the full `key → split` assignment for the routing corpus, so a downstream
consumer re-derives the partition without holding the original list order.

---

## 5. The small model — `backend/ml/configs/small_model.toml`

Loaded by `ml/training/config.py` into a pydantic `SmallModelConfig`
(`extra="forbid"` — a typo like `max_seq_lenght` is rejected rather than silently
leaving the default in place while the manifest reports a deliberate value). The TOML is
versioned precisely so the reasoning travels with the number, and every row below gives
the reason the file itself carries.

| Field | Value | Why |
| --- | --- | --- |
| `base_model` | `microsoft/deberta-v3-base` | The smallest pretrained encoder that still separates the near-synonymous intent pairs in the taxonomy reliably; a smaller encoder collapses them. |
| `num_labels` | `14` | The size of the closed label set — the routing destination, **not** the twelve `RecommendationType` members. Eleven are router families, two are the classes NEXUS cannot serve, one is abstention. `stage_prepare` asserts this against `len(INTENT_NAMES)` and refuses to write splits on a mismatch: "A head sized for the wrong count does not fail, it just never predicts the missing class." |
| `max_seq_length` | `128` | The longest intent utterance runs to roughly 40 subword tokens. Longer adds only padding, and every extra position costs quadratically in attention. This is also the cap the serving path uses, so training and inference truncate identically. `SmallModelConfig` validates `32 ≤ n ≤ 512`; beyond 512 the encoder's serving context and its training context stop agreeing. |
| `learning_rate` | `0.00002` (2e-5) | The standard encoder fine-tuning rate. A corpus this size starts overfitting the templates above ~5e-5, and the intent set is closed, so there is no pretraining benefit to a larger rate. |
| `weight_decay` | `0.01` | The usual AdamW decoupled decay. It matters more here than usual because most of the 183M parameters are embeddings the task never updates; decaying them keeps the representation from drifting on a small corpus. |
| `num_train_epochs` | `5` | Five epochs over the ~1960 training utterances at batch 16. The TOML states **123 optimiser steps per epoch, 614 in all**; the completed run reports **615**, because 1960 rows ÷ 16 = 122.5 rounds up to 123 steps per epoch and 123 × 5 = 615. The TOML records why five: **four was tried first, on a 150-rows-per-intent corpus, and produced 0.9675 accuracy / 0.9674 macro F1 over 308 held-out rows, with the training loss still falling (2.63 → 0.056).** Raising both the corpus (150 → 200 rows per intent) and the epoch count was the experiment, and it has now run: **0.9738 accuracy / 0.9737 macro F1 over 420 test rows**, with the training loss falling 2.6196 → 0.0264. The TOML states the test in advance — if the ceiling is the *data* rather than the schedule, more steps over more rows should move it; if it does not, the boundary between `knowledge_capture` and `knowledge_lookup` is a property of the taxonomy. The result came out on the data side: those two classes now score **1.0000** and **0.9831**, and the one `knowledge_lookup` error goes to `project_manage` rather than to its neighbour. |
| `per_device_train_batch_size` | `16` | Fits comfortably in 24 GB at 128 tokens, and is small enough that each optimiser step still sees variety across the labels. |
| `per_device_eval_batch_size` | `32` | Evaluation passes, so it can be 2× the training batch without affecting peak memory during the training step. |
| `warmup_ratio` | `0.1` | Short schedule on a small dataset: without warmup the first few hundred steps carry the full learning rate and destabilise the encoder. |
| `weighting_strategy` | `"balanced"` | Inverse-frequency loss weighting. Generated intent sets are rarely uniform, and without it a rare-but-actionable intent is swamped by a common one. Closed set `{none, balanced}` — there is no third option because an unrecognised spelling would otherwise be silently treated as `none`. |
| `seed` | `20260101` | One seed for the split, the initialisation and every sampler, recorded in the manifest so the run is reproducible from the artifact alone. |
| `gradient_checkpointing` | `false` | At 128 tokens the activations for a 183M-parameter encoder are small, and recomputing them costs roughly 30% throughput to save memory that is not needed. |
| `save_every_n_steps` | `100` | Every 100 steps. The classifier trains locally in one process, so this is about an interrupt on a long CPU run not costing the whole run. The TOML puts the arithmetic in: "367 steps means four resumable points; a cadence above ~150 would leave this model with one checkpoint for the whole training, which is a checkpoint that saves nothing when it is interrupted." |
| `eval_every_n_steps` | `100` | Same cadence, "so a checkpoint is never saved without the metric that justifies it." |
| `parameter_count` | `183000000` | Dominated by DeBERTa-v3's 128k-row embedding table. Carried as data, not a comment. |
| `parameter_count_band` | `[100000000, 300000000]` | The brief's size band, enforced by `load_config`: below 100M the router cannot hold the embedding table; above 300M it stops being a router and starts being an inference cost. |

### 5.1 The local trainer

`ml/scripts/train_small_local.py` is a standalone script with its own argparse, run
directly or via `ml.train --train-small`. What it does that the orchestrator does not:

- **Reads the label map, never assumes it.** `num_labels` comes from `label_map.json`,
  cross-checked against `ml.datasets.routing.label_map` (the taxonomy's own index order).
- **Measures the parameter count, then asserts it** against the band. A run that lands
  outside stops rather than producing an artifact that would be rejected at load time.
  (The observed run measured **184,432,910** — inside the band, above the TOML's 183M
  because the classification head adds parameters.)
- **Device selection** is a closed enum (`auto`/`cpu`/`cuda`); `auto` resolves to CUDA only
  when torch can actually see a device, and `--device cuda` against a CPU torch is an
  error rather than a silent downgrade. `--threads` caps the CPU pool via
  `torch.set_num_threads`.
- **Class weighting** from `weighting_strategy`; `_parameter_groups()` separates decayed
  from non-decayed parameters (biases and norms) the way AdamW expects.
- **Encoding** is done once, in int64, with padding to a fixed length — chosen so a
  rounding error cannot change batch shapes between steps, which is what buys a resume
  that does not depend on how some other routine batches.
- **Resume** restores the furthest checkpoint under `ml/artifacts/small-model/checkpoints`
  after checking it belongs to the current data — same `dataset_checksum`, same
  `dataset_version`. Without `--resume`, a stage that finds a resumable checkpoint **says
  so and starts fresh** rather than silently continuing a run whose provenance nobody
  asked for. Nothing is deleted on either path.

**What the last completed run recorded.** `ml/artifacts/small-model/training_state.json`
is the run's own account of itself, and it is deliberately boring: `run_id
small-20261003T193530Z-855c5eb8`, `epochs 5`, `steps 615`, `steps_per_epoch 123`,
`train_rows 1960`, `validation_rows 420`, `device cpu`, `batch_size 16`,
`parameter_count 184432910`, `first_loss 2.6196`, `final_loss 0.0264`,
`duration_seconds 3976.4`, `resumed false` (`resumed_from: null`), `seed 20260101`,
`torch_version 2.14.1+cpu`, `transformers_version 5.18.0`, and a `code_commit` with
`code_dirty: true`. Its `dataset_checksum` is
`50956003057ba44c2c50011b7fb2c0b1cea3d58179e83635ca353fea6b49eeaf`; that digest is over
the train and validation splits **together**, so a change to either refuses the resume.
The two per-split digests it also carries — `train_checksum 0ac04a10…` and
`validation_checksum 4805a12d…` — are the same sha256 values
`ml/datasets/dataset_manifest.json` records for `routing_train.jsonl` and
`routing_validation.jsonl`, which is how a reader confirms the model was fitted on the
splits the current prepare wrote. The config block nested inside it records
`num_train_epochs: 5`, `learning_rate 2e-05`, `max_seq_length 128` and `seed 20260101` —
the configuration that ran is the configuration in the TOML.

Its `validation` block is the trainer's own score on the 420 validation rows:
accuracy **0.9810**, macro F1 **0.9809**, loss 0.0816, against an initial validation
accuracy of 0.0714. The numbers quoted as this model's performance are not these: they
come from `evaluate`, on 420 held-out rows the trainer never saw.

This is the run the TOML's `num_train_epochs = 5` comment predicted — the 2,800-row
corpus, five epochs, 615 optimiser steps, on CPU. `ml/reports/pipeline_summary.json`
records all three stages `PASSED` under a verdict of `PASS`: `prepare` ("2800 routing
rows {'train': 1960, 'validation': 420, 'test': 420}"), `train-small` ("615 steps,
validation accuracy 0.9810 (macro F1 0.9809)") and `evaluate` ("0.9738 accuracy, 0.9737
macro F1 on 420 rows").

**Reproducing a run from its own artifacts.** Everything needed to re-derive a run is in
the files it wrote: the seed (`20260101`) in `training_state.json`, the manifest and in
`ml/configs/small_model.toml`; the exact configuration as data, in the `config` block of
`training_state.json` and in every `checkpoint.json`; the code it came from, as
`code_commit` plus `code_dirty`; the data, as `dataset_checksum` over the two splits
together; the environment, captured by `ml/training/manifest.py` into the timestamped
manifest under `ml/reports/manifests/`; and the scored output, per row, in
`predictions_test.jsonl` beside `metrics.md`. `make ml-all` re-runs the lot; nothing in
the pipeline depends on wall-clock time, global random state, or dict iteration order.

`stage_evaluate` then scores the held-out test split through
`ml.evaluation.metrics.evaluate` — the same code that produces the trainer's own
validation numbers — so the report, the manifest and the trainer cannot disagree by
construction. Metrics: accuracy, macro F1, weighted F1, per-class precision/recall/F1 with
support, a full confusion matrix and (optionally) top-k accuracy, rendered as Markdown
with `_REPORT_PRECISION = 4` decimals. Every stage writes a timestamped manifest to
`ml/reports/manifests/` carrying a sha256 for every file it claims to have produced.

**The last completed evaluation**, from `ml/artifacts/small-model/metrics.md` and
`metrics.json`, quoted: accuracy **0.9738**, macro F1 **0.9737**, weighted F1 **0.9737**,
over **420** held-out rows (30 per class × 14) — `split: test`, `rows: 420`, and support
of exactly 30 for every one of the fourteen labels, so no class is under-measured. Four
classes are perfect (`schedule_plan`, `knowledge_capture`, `account_admin`,
`deep_reasoning`); eleven of the fourteen score at or above 0.95, and the remaining three
are the weakest named below:
`project_manage` at **0.9153** (precision 0.9310, recall 0.9000 — 27 of 30, the misses
going to `risk_query` twice and `career_track` once), `analytics_insight` at **0.9333**
(precision and recall both 0.9333, the misses going to `learning_track` and
`career_track`), and `developer_intel` at **0.9474** (precision 1.0000, recall 0.9000 —
27 of 30, the misses going to `analytics_insight` twice and `project_manage` once), then
`risk_query` at **0.9524** (precision 0.9091, recall 1.0000). Eleven errors in 420 rows,
spread across the adjacency §2.3 describes; nothing is concentrated on one broken pair
any more.

The evaluation that preceded it — 0.9675 accuracy, 0.9674 macro F1 over 308 rows from the
2,100-row, four-epoch corpus — is **superseded**. It is the history that motivated
raising `--per-intent` to 200 and `num_train_epochs` to 5, and it is not a measurement of
the current configuration.

---

## 6. Checkpointing and resume — `ml/training/checkpoint.py`

> **The machine that decides whether a run is worth resuming is not the machine that
> trains it.**

Everything under `ml/` is stdlib-only, and the checkpoint format is the reason that
stays cheap: this module never imports torch and never unpickles anything. It writes
bytes, hashes what it wrote, records the step, and answers "which checkpoint is the
furthest I got, and is it whole?" — a question that has to be answerable in CI and on a
machine with no torch at all. A design that made that answer require importing the
library the resume exists to avoid installing would answer it nowhere. The local trainer
writes into this format through its `save_sidecar` hook; the readers do not care who
wrote it.

**The format.** A directory: payload files plus one `checkpoint.json`.
Format version `nexo_checkpoint.v1`. What the classifier actually writes:

```text
step-100/
├── checkpoint.json          ← written LAST; its presence is the commit point
├── extra.json               ← optional scalar state (step counters, RNG, loss history)
├── model.safetensors        ← logical names; the format does not know what they are
├── optimizer.pt             ← written by the trainer's sidecar hook
├── rng_state.pt
├── config.json
├── tokenizer.json
└── tokenizer_config.json
```

**`CheckpointMetadata` fields.** `format_version`, `run_id`, `model_name`, `global_step`,
`epoch`, `segment_index`, `segments_completed`, `dataset_version`, `dataset_checksum`,
`code_commit`, `config`, `hyperparameters`, `seed`, `created_at`, `elapsed_seconds`,
`files`. The resume-relevant ones are `global_step`, `epoch`, `segment_index` and
`segments_completed` — a trainer that processes a dataset in segments has to know which
segment the run died in as well as how far it got, or it restarts the epoch with a
shuffle it has already consumed. `dataset_version` and `dataset_checksum` are both
present so a resume can refuse to continue onto changed data: resuming onto a different
dataset is worse than not resuming, because the result looks like a continuation and is
not one.

`files` maps a **logical name** to a filename inside the directory, so a consumer asks
for `state.directory / state.metadata.files["model"]` and never hardcodes a
filename this module chose. Logical names are restricted to `[A-Za-z0-9._-]+` with no
separators, traversal, absolute paths or drive letters. A name with no extension gets
`.bin`; a name with one keeps it.

**The crash contract, which is the whole point.** Order of operations in
`save_checkpoint()`: payload files → `extra.json` → the `save_sidecar` hook → **metadata
last**. Every file is written to a temporary name and moved into place with `os.replace`,
so a reader never observes a partial payload. A directory that exists without its
metadata is therefore an *interrupted write* — not a checkpoint, but debris — and both
`is_resumable()` and `latest_checkpoint()` refuse it. Corrupting a run by resuming from a
half-written one is worse than restarting it, because the failure surfaces much later as
a nonsense loss curve.

`_rejection_reason()` enumerates every refusal with its own sentence: not a directory;
no `checkpoint.json`; unreadable; not a JSON object; a foreign format version; no file
map; a declared payload that is **missing** or **empty**; a metadata field that is
mistyped (booleans are rejected explicitly, since `True` is an `int` in Python and a
metadata file written by a buggy producer should fail loudly rather than resume at step
1).

**The four readers.**

| Function | Behaviour |
| --- | --- |
| `is_resumable(dir)` | the predicate. **Quiet by design** — it is the one a caller polls, and a warning on every poll of an obviously-empty directory trains people to ignore warnings. |
| `load_checkpoint(dir)` | the caller named this directory, so a refusal is an **error**. Silently returning nothing would hide a decision that needs making. |
| `list_checkpoints(root)` | every complete checkpoint, oldest step first. Incomplete directories are skipped silently — this is the call a report makes to draw a curve, and a curve does not care about the debris of an interrupted save. |
| `latest_checkpoint(root)` | the furthest complete checkpoint, **reporting every directory it ignored and why**. A truncated save that silently drops out of the scan is indistinguishable from a run that never reached that step, and only one of those is a bug worth chasing. Ties on `global_step` break by directory name, so a root holding two copies of one step always yields the same one. |

`CheckpointError` is distinct from a generic OS error so resuming can tell "this directory
is not a checkpoint" (skip, warn, continue from the previous one) from "this process
cannot write to disk" (stop).

## 7. The `ml-*` Make targets

All run from the repository root and `cd backend` first, because the `ml` package is
imported as `ml` from the backend working directory.

```make
ML_RUN := cd $(BACKEND) && ../$(ML_PY) -m ml.train
ML_PY ?= backend/ml/.venv/Scripts/python.exe   # or bin/python on POSIX, falling back to $(PY)
```

`ML_PY` is **overridable** and is the first thing to reach for when a target fails:

```bash
make ml-train-small ML_PY=backend/ml/.venv/Scripts/python.exe
```

The override exists because `make` is not shipped with Windows — use Git Bash with a make
package, or run the scripts directly.

| Target | Runs | Needs | Notes |
| --- | --- | --- | --- |
| `ml-help` | — | — | Greps the `ml-*` targets with descriptions out of the Makefile itself, so the help cannot drift from the targets. Also prints the `ML_PY` pin instructions. |
| `ml-prepare` | `ml.train --prepare` | stdlib only, no GPU | Harvests the inventory, cross-checks `num_labels` against the taxonomy, builds the routing corpus, refuses to continue unless every validator passes, splits without leakage, writes JSONL splits + `label_map.json` + `capability_inventory.json` + `splits.json` + `dataset_manifest.json`. |
| `ml-datasets` | `ml-prepare` | — | Alias. |
| `ml-validate` | `ml-prepare`, then prints every `ml/reports/*.md` | stdlib only | Depends on `ml-prepare`, so the reports it prints are never stale. Tells you to run `make ml-prepare` first if the directory is empty. |
| `ml-train-small` | `ml.train --train-small` | **torch** (`$(ML_PY)`) | Fine-tunes the routing classifier locally on CPU. Delegates to `ml.scripts.train_small_local` in a subprocess under the ML interpreter. |
| `ml-train-small-resume` | `ml.train --train-small --resume` | **torch** | Resumes from the latest local checkpoint after checking it belongs to the current data (same checksum, same dataset version). |
| `ml-eval` | `ml.train --evaluate` | stdlib + torch + the trained artifact | Scores the held-out test split and writes the evaluation reports and a timestamped manifest. `BLOCKED` if nothing has been trained. |
| `ml-all` | `ml-prepare` → `ml-train-small` → `ml-eval` | torch | The whole pipeline. The classifier trains on CPU in about 66 minutes (2,800 rows, 5 epochs, 615 steps, measured), so `ml-all` is the whole of Phase 10 — there is no half of it left out. |
| `ml-test` | `pytest tests/test_ml_*.py` | `$(PY)` (the backend venv) | Only the ml test modules. |

**`ml-all` is the pipeline.** There is no stage left out of it and no target for a stage
that does not exist: the `.PHONY` list (`Makefile:66`) is `ml-help ml-prepare ml-datasets
ml-validate ml-train-small ml-train-small-resume ml-eval ml-all ml-test`, and every one
of those has a recipe. A target that no longer has a stage behind it is worse than a
missing one, because `make ml-help` would advertise it.

The other useful flags, all on `ml.train`:

| Flag | Default | Meaning |
| --- | --- | --- |
| Stage flags | — | `--prepare`, `--train-small`, `--evaluate`. A set, not a sequence: asking for `--evaluate --prepare` runs both, in pipeline order. |
| `--all` | — | Runs every stage in `STAGES` order; this is also what a bare `python -m ml.train` does. |
| `--seed` | `20260101` | Master seed for the corpus and the split. |
| `--per-intent` | `200` | Routing rows per intent (2,800 total). A chosen size, not a hard ceiling — see §2.3; raising it past what the families can fill raises rather than pads. |
| `--config-dir` | `ml/configs` | Where the TOML lives. |
| `--datasets-dir` / `--artifacts-dir` / `--reports-dir` | `ml/datasets`, `ml/artifacts`, `ml/reports` | Output locations. |
| `--resume` | off | Passed through to `--train-small`; implied when `--resume` is given alone. |
| `--dry-run` | off | Print the stage plan and exit. |
| `--verbose` | off | Print the commands each stage runs. |

### 7.1 What the tests pin

`make ml-test` runs `pytest tests/test_ml_*.py` under the **backend** venv — thirteen
modules, stdlib-only, which is possible because nothing in `ml/` imports torch at module
scope. A collected run of the suite reports **321 passed**. Each module pins one property,
and the per-module counts below are the test functions as written (parametrised cases
collect as more than one), so they are a fair guide to how much of §2–§6 is actually
tested rather than merely described:

| Module | Tests | What it defends |
| --- | ---: | --- |
| `test_ml_capabilities.py` | 20 | the AST harvest off `backend/app` |
| `test_ml_checkpoint.py` | 25 | the torch-free checkpoint format, its readers and every refusal |
| `test_ml_config.py` | 18 | typed configuration, `extra="forbid"`, the shape and capacity validators |
| `test_ml_datasets.py` | 13 | the generated routing corpus: intents, balance, determinism, validation |
| `test_ml_features.py` | 21 | the four `*_features.v1` contracts as training rows |
| `test_ml_manifest.py` | 15 | the record of what a run *was* |
| `test_ml_metrics.py` | 23 | classification metrics, in pure standard library |
| `test_ml_normalize.py` | 24 | normalisation, near-duplicate keys, credential detection |
| `test_ml_schema.py` | 23 | record schemas, versioning, JSONL I/O, checksums |
| `test_ml_secrets.py` | 14 | the security gate: nothing in `ml/` may carry, read or emit a credential |
| `test_ml_splits.py` | 24 | deterministic, leakage-free splitting |
| `test_ml_taxonomy.py` | 14 | the fourteen intents and their destinations |
| `test_ml_validation.py` | 32 | the data-integrity gate between building a dataset and training on it |

There is no stage-test module any more, and nothing needs one: the remote stages that
would have needed a stubbed client are gone with the model, and the three stages that
remain are exercised end to end by running the pipeline rather than by asserting on a
recorded verdict.

---

## 8. Troubleshooting

### The data half (`ml-prepare`, `ml-validate`)

**`small_model.toml declares num_labels=14 but the taxonomy has N intents`.** The TOML and
`ml/datasets/taxonomy.py` disagree. Fix the TOML, or add the intent. This fails loudly on
purpose: a head sized for the wrong count does not fail, it just never predicts the missing
class.

**`{intent}: produced N of M unique examples from T templates in B attempts; add template
families or lower per_intent`.** A template family ran dry before filling its quota.
Either lower `--per-intent` or add templates to that intent. Raising it produces this error,
not padding.

**`intents are not balanced: [...]`.** A post-condition on `build_routing_dataset`. Same
cause as above.

**`dataset validation failed: routing_intent.v1: contradictory_label (...)`.** One
normalised text carries more than one intent. Look at the finding's redacted sample; fix
the generator. Do not delete the row silently — the contradiction is a boundary the model
cannot learn.

**`split_leakage` in `ml/reports/*_splits.md`.** A near-duplicate group straddles a
boundary. This should be impossible with `group_by=True`; if it appears, the splitter and
the audit are being handed different `duplicate_key_fn`s. Both calls in `stage_prepare`
pass `near_duplicate_key`.

**`class_imbalance` warning.** Informational. The corpus is balanced by construction, so
this is a tripwire against a generator regressing. `MAX_CLASS_RATIO = 5.0`.

### The local training half (`ml-train-small`)

**`ModuleNotFoundError: No module named 'torch'`.** You ran with the backend interpreter.
Use `make ml-train-small ML_PY=backend/ml/.venv/Scripts/python.exe`, or call
`backend/ml/.venv/Scripts/python.exe -m ml.scripts.train_small_local` directly. The
`ml/train.py` path shells out to the right interpreter for you; a direct
`python -m ml.train --train-small` on the backend venv will not.

**`--device cuda was requested but torch sees no CUDA device`.** Explicit, not a silent
downgrade. Use `--device cpu` (the default `auto` already resolves correctly).

**A very slow run.** It is CPU training, by design — the Makefile target says "locally on
CPU". The confirmed run over the current corpus took `duration_seconds 3976.4` (≈66 min)
for **615** steps at batch 16, so budget over an hour rather than the roughly forty
minutes an earlier four-epoch, 368-step run needed. `--threads` caps the pool if you want
to leave the machine usable.

**Resume refused.** The checkpoint's `dataset_checksum` or `dataset_version` does not match
the current data. That is the intended behaviour: resuming onto a different dataset is
worse than not resuming, because the result looks like a continuation and is not one. Run
`make ml-prepare` then `make ml-train-small` fresh, or restore the previous split files.
This is what a re-prepare did to the checkpoints of the superseded run: it recorded
`dataset_checksum 97bc73a7…` over the 2,100-row corpus's splits, and after the 2,800-row
prepare `ml/datasets/routing_train.jsonl` hashes to `0ac04a10…`, so those checkpoints
stopped being resumable and the run started fresh (`resumed: false`). The checkpoints now
on disk belong to the confirmed run and carry `dataset_checksum 50956003057ba44c…`, so
`make ml-train-small-resume` will accept them against the current splits — but the run is
complete (`steps 615` of `planned_steps 615`), so there is nothing left to resume.

**`ignoring checkpoint directory …: no checkpoint.json` / `payload 'x' is empty`.** An
interrupted save. `latest_checkpoint` skipped it and warned. This is the format working as
designed — a directory with no metadata is debris, not a checkpoint. Delete it if you like;
nothing on the resume path will touch it.

**Poor macro-F1 on adjacent classes.** The expected failure mode of this corpus. The
confusion matrix in `ml/artifacts/small-model/metrics.md` is where to look: the classes
that bleed into each other are the ones whose template families share vocabulary. Fixing
it means changing templates, not changing the learning rate.

### The evaluation half (`ml-eval`)

**`no trained model at …/artifacts/small-model/final. Run --train-small first; evaluate
scores a checkpoint, it does not train one`.** `BLOCKED`, and correct: nothing is broken,
nothing has been trained yet. Exit status is 0.

**`no test split at ml/datasets/routing_test.jsonl. Run --prepare first`.** Also `BLOCKED`.

**`small_model.toml declares num_labels=14 but the taxonomy has N intents` at prepare
time.** `stage_prepare` refuses to write splits. Adding or renaming an intent changes the
label count, and the TOML has to change with it — see the entry in the data half above.

**`make ml-validate` prints reports for a dataset that no longer exists.** The target
depends on `ml-prepare`, so the reports it prints are always from the prepare it just
ran. The exception would be a stale `.md` file sitting in `ml/reports/` that no stage
writes any more; there is none today — the directory holds `routing_dataset.{json,md}`,
`routing_splits.{json,md}`, `pipeline_summary.json` and `manifests/`, all of them written
by the current pipeline. Delete anything else you find there rather than reading it as
current.

---

## 9. Findings

Recorded here because this document's brief forbids modifying any Python source under
`backend/ml/`. Items are kept with their current state rather than deleted, because
"this was broken and is now fixed" is the more useful record. **None of them is open** —
item 1 was fixed in the source after this document was first written, and items 2, 3 and 8
were closed by the confirmed run described in §5.1. The numbering is retained from the
draft these findings were written in.

1. **~~Cross-intent de-duplication defeated by a shadowed parameter.~~ FIXED.**
   This was the finding that mattered most: `_generate()` used to rebind its `seen`
   parameter to a fresh set, so `build_routing_dataset()`'s shared cross-intent set was
   never mutated and de-duplication was per-intent only — which would have undercut the
   module's central claim that no utterance carries two labels. **The rebinding is
   gone.** `ml/datasets/routing.py:1868` now carries a comment stating the invariant
   explicitly (*"`seen` is the caller's cross-intent set and is deliberately NOT re-bound
   here"*), `_generate` adds to the set it was handed, and `build_routing_dataset`
   passes one set across all fourteen intents. The generator docstring's claim now
   holds as written. Re-verified end-to-end at the shipped seed on the current 2,800-row
   corpus: `ml/reports/routing_dataset.md` records `records 2800 / usable 2800`,
   `contradictions 0` and no findings, and `ml/reports/routing_splits.md` records
   `unique_keys 2800` with `leaked_keys 0`.

2. **~~`num_train_epochs` in the committed TOML is 5; the on-disk small-model artifacts were
   produced with 4.~~ RESOLVED.** The retrain the TOML's comment asked for has completed
   and written over those artifacts. `training_state.json` now records `run_id
   small-20261003T193530Z-855c5eb8`, `epochs: 5`, `steps: 615`, `steps_per_epoch: 123`,
   `train_rows: 1960`, `validation_rows: 420`, `first_loss: 2.6196`, `final_loss: 0.0264`,
   `duration_seconds: 3976.4`, `resumed: false`, and a nested `config` block whose
   `num_train_epochs` is `5`. The configuration that ran is the configuration in
   `ml/configs/small_model.toml`; **the artifacts on disk no longer predate the current
   configuration.**

3. **~~Those artifacts were trained on a different corpus than the current splits.~~
   RESOLVED.** `training_state.json` records `train_checksum 0ac04a10…` and
   `validation_checksum 4805a12d…`, which are exactly the sha256 values
   `ml/datasets/dataset_manifest.json` carries for `routing_train.jsonl` (1,960 rows) and
   `routing_validation.jsonl` (420 rows), and `metrics.md` / `metrics.json` report 420
   scored rows with support of 30 on every one of the fourteen labels — against a test
   split that is 420 rows. **The current model's performance is accuracy 0.9738, macro F1
   0.9737, weighted F1 0.9737 over those 420 held-out rows.** The 0.9675 / 0.9674 figures
   on the 2,100-row, four-epoch, 308-row corpus are **superseded**: they are quoted only as
   the history of why `--per-intent` is 200 and `num_train_epochs` is 5, and they are not
   the model's performance.

8. **~~The checkpoints on disk are not resumable against the current splits.~~ RESOLVED.**
   The confirmed run wrote `step-100` through `step-600` under
   `ml/artifacts/small-model/checkpoints/`, each carrying `dataset_checksum
   50956003057ba44c…` and `dataset_version routing_dataset.v1` for run
   `small-20261003T193530Z-855c5eb8` — so they match the current splits and
   `make ml-train-small-resume` will not refuse them. They are also not useful, because the
   run is complete (`steps 615` of `planned_steps 615`) and there is nothing left to
   resume. The refusal described in §8 is what the *next* re-prepare will produce, and it
   is the resume contract working rather than a defect.

---

## 10. Standing constraints this half inherits

Restated because they are enforced in the code above rather than in this document:

1. **No fake data.** Every number traces to a file. A figure that could not be computed is
   null, never `0` — the same rule `FeatureRow.from_dict` enforces, and the same rule
   `_excerpt`/`redact` apply to reports.
2. **Deterministic before learned.** Nothing in `ml/` is on a request path. Phase 10
   produces artifacts; serving them is Phase 11's job, and the deterministic engines in
   `app/services` remain the fallback that learned code is measured against.
3. **Explainability.** Every hyperparameter carries its reason beside it, in the TOML or in
   the validator that enforces it. A number without a derivation is the defect this half of
   the project exists to avoid.
4. **Never claim what was not executed.** `BLOCKED` is a first-class status. `passed` is
   derived from findings, not supplied. `assert_clean` raises. A manifest hashes every file
   it claims. A checkpoint must be complete before it is resumed. And a class the product
   cannot serve is labelled as one NEXUS cannot serve — the router's job is to say so, not
   to pretend a request was handled.
5. **Credentials are never read, printed, logged or committed.** This document, like the
   code, never opens a credential file; every string that could reach a log or a report
   passes through `redact()` first.

---

## Appendix — file map

| Path | Role |
| --- | --- |
| `backend/ml/train.py` | the orchestrator; the three stages, the CLI, the constants |
| `backend/ml/configs/small_model.toml` | the classifier's hyperparameters, with derivations |
| `backend/ml/training/config.py` | pydantic models + validators; `load_config` |
| `backend/ml/datasets/routing.py` | the routing corpus generator |
| `backend/ml/datasets/features.py` | deterministic per-subject feature vectors and their null contract |
| `backend/ml/datasets/taxonomy.py` | the fourteen intents and their specs |
| `backend/ml/datasets/capabilities.py` | the `ast`-based harvest of the real product surface |
| `backend/ml/datasets/schema.py` | record schemas, versioning, JSONL I/O, checksums |
| `backend/ml/validation.py` | the data-integrity gate |
| `backend/ml/preprocessing/splits.py` | deterministic, leakage-aware splitting |
| `backend/ml/preprocessing/normalize.py` | normalisation, `near_duplicate_key`, `redact`, `find_credential` |
| `backend/ml/training/checkpoint.py` | the torch-free local checkpoint format and readers |
| `backend/ml/training/manifest.py` | run manifests, environment capture, run ids |
| `backend/ml/scripts/train_small_local.py` | the local CPU training loop |
| `backend/ml/evaluation/metrics.py` | accuracy, macro/weighted F1, confusion matrix, top-k |
| `backend/tests/test_ml_*.py` | the thirteen `ml` test modules `make ml-test` collects |
| `Makefile` (lines 139–192) | the `ml-*` targets |
