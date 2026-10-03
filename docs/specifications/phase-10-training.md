# Phase 10 — ML Training: the operational half

**Status:** documents the pipeline as it exists in `backend/ml/`. Updated for the seven-stage
orchestrator: `--probe-remote` and `--eval-qwen` have joined the three local stages, and
`--train-qwen` now has a real push path rather than a documented intention.
**Scope:** dataset construction, validation, splitting, both model configurations,
checkpointing and resume, the Kaggle workflow, the stage tests, and the `ml-*` Make targets.

This document deliberately covers the **operational** half of Phase 10 — the machinery
that produces and trains on data. It does not restate the product intent (why a router
exists, why two classes escalate) or the architecture (how a trained checkpoint reaches
a request); those are in [`../architecture.md`](../architecture.md). Where this document
disagrees with the code, the code is what runs — and the disagreement is written down
here rather than left for someone to discover.

> **AGENTS.md:** no `AGENTS.md` exists at the repository root or under `backend/` at the
> time of writing, so there is no project-supplied agent guidance to reconcile against.

---

## 1. The shape of the pipeline, and the seam inside it

Phase 10 is two packages wearing one name. Everything under `backend/ml/` is
**stdlib-only** — no `torch`, no `transformers`, no `pydantic` outside the config
module — except the places that genuinely need them. That split is not stylistic; it is
what keeps the backend test suite runnable without a multi-hundred-megabyte download.

| Interpreter | Lives at | What runs there |
| --- | --- | --- |
| Backend venv | `backend/.venv/Scripts/python.exe` | the orchestrator (`ml.train`), all of `ml.datasets`, `ml.validation`, `ml.preprocessing`, `ml.training.checkpoint`, `ml.training.remote`, `ml.kaggle.notebook`, the TOML loader |
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

There are **seven** selectable stages — `prepare`, `train-small`, `evaluate`,
`train-qwen`, `probe-remote`, `eval-qwen`, `qwen-status` — each with a flag of the same
name, described in §8.5. The first three are local; the last four reach Kaggle through
`ml/training/remote.py` and are the reason this half of the pipeline has an honest
notion of "blocked".

`ml/scripts/train_small_local.py` reinforces the boundary in the other direction: every
`torch` import is inside a function, because pytest collects the whole `ml` package
under the backend interpreter and a module-scope `import torch` would break collection
on an interpreter that cannot have torch.

---

## 2. Dataset construction

### 2.1 Why every row is synthetic

There is no corpus of real user utterances to sample from, and this is not a
shortage that more effort would fix. NEXUS is a single-user, self-hosted personal
system: there is exactly one user, so there is no population from which to sample
phrasing. Scraping a chatbot benchmark would produce a label space that does not match
the 185 routes that have to serve it. Every row in both corpora is therefore generated
deterministically from a template plus a real capability vocabulary, and every row
carries `provenance=Provenance.SYNTHETIC` because that is exactly what it is.

Three consequences are load-bearing and are enforced in code, not asserted in prose:

1. **The manifest states it.** `stage_prepare` writes an explicit `provenance` histogram
   per corpus (`_provenance_breakdown`, `ml/train.py:900`). The day a hand-written row
   enters a corpus it shows up there as a second key. The current run reads
   `{"synthetic": 2100}` for routing and `{"synthetic": 400}` for qwen.
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

The inventory reaches the generators as a `CapabilityInventory` argument. Two builders
use it differently:

- **`routing.py`** filters its closed vocabularies against it. Every field of
  `_Vocabulary` is therefore a subset of what the application actually declares, and an
  **empty field is a signal, not a fallback to invention** — a renamed enum stops
  generation rather than teaching a stale label.
- **`qwen_sft.py`** grounds every generated response. `_Grounding.check()` refuses to
  return a row that names an undeclared route or claims NEXUS already performed an
  action. Because it can be called without a live inventory, the module carries a
  **frozen snapshot** (`_SNAPSHOT_ROUTES` and friends) — a verbatim copy of
  `build_capability_inventory(Path("app"))` at the commit it was written against, 185
  routes sorted by `(path, method)`, re-stamped with the live
  `CAPABILITY_INVENTORY_VERSION` so a mismatched harvest is a refusal rather than a
  silent mixture. The training pipeline passes the live inventory; the snapshot is the
  no-`app`-in-front-of-you fallback.

### 2.3 The routing corpus — `ml/datasets/routing.py`

**Version:** `routing_dataset.v1` (versions the *generator*, distinct from the
`routing_intent.v1` record schema it produces).

**What a row is.** One utterance, one intent label, a `template_id`, a `source`, and
`provenance=Provenance.SYNTHETIC`.

**Where the fourteen labels come from.** `ml/datasets/taxonomy.py`'s `INTENT_NAMES` —
the routing destination a request can land on: eleven router families, the two
large-model classes (`code_assist`, `deep_reasoning`) and the abstention class
(`out_of_scope`). These are **not** the twelve `RecommendationType` members; those name
the actions a person takes and are the *vocabulary the templates are written from*.
`label_map()` indexes intents in taxonomy order, not alphabetical, because the taxonomy
order is the order the classes were chosen in — routers first, then the two model
classes, then abstention — and a class index that preserves that is readable in a
confusion matrix, where a head sitting next to its escalation classes tells you something
at a glance.

**Template families per intent.** Three sources, assembled in `_build_templates`:

1. `_curated()` — the hand-written exemplars already carried by `IntentSpec.examples`,
   one template per exemplar, plus four keyword-grounded shapes for all intents except
   `code_assist`, `deep_reasoning` and `out_of_scope`. Those three are excluded on
   purpose: a one-line keyword utterance is a light, generic request, and putting one
   into a heavy class blurs the boundary that justifies routing to the 8B model.
2. `_INTENT_TEMPLATES` — the hand-written families, 20–36 per intent (the fourteen counts
   are 26, 23, 24, 21, 20, 36, 33, 33, 24, 31, 35, 29, 36 and 34 in taxonomy order),
   drawn from 33 slot pools (`_TASK_TITLES`, `_PROJECT_NAMES`, `_DATES`, `_REPOS`,
   `_BRANCHES`, `_LANGUAGES`, …; `concept` is an alias for `topic` rather than a second
   pool). The wording rule they were written under: state the *action on the
   entity*, and leave out any word that would move the utterance into a neighbouring
   class. No "priority" or "deadline" inside `task_manage`; no "how am I doing" inside
   `schedule_plan`.
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

**Volume.** `--per-intent` defaults to **150**, giving **14 × 150 = 2100 rows** — the
figure `ml/datasets/dataset_manifest.json` records. Two things about that number are
worth being precise about, because the number moved during the phase and the code
does not agree with itself about where it lives:

- **The CLI default is 150; the library default is still 90.** `build_routing_dataset`
  and `build_routing_records` both default to `per_intent=90`
  (`ml/datasets/routing.py:1976` and `:2028`), so a caller that bypasses `ml.train`
  gets the older figure and 1,260 rows.
- **150 is a chosen corpus size, not a ceiling.** The CLI help still describes it as
  *"the largest value the narrowest template family can fill with distinct slot
  combinations"*, which the current slot pools no longer support: the pools hold up
  to 43 values each (`_SLOT_VALUES`, 33 pools), and
  `build_routing_dataset(seed=20260101, per_intent=200)` returns **2,800 rows** that
  `validate_routing_dataset` accepts with `records 2800 / usable 2800` and **zero
  findings**. Verified against the current tree, not inferred.

The phase's own account of how it got here — 200 per intent was tried first, the
`analytics_insight` family could not fill its quota, and the pools were widened until
it could — is not recoverable from the repository, so it is recorded here as reported
rather than asserted. What the code does guarantee is the *shape* of the ceiling: the
builder raises rather than returning a lopsided corpus (see the attempt budget above).

### 2.4 The Qwen SFT corpus — `ml/datasets/qwen_sft.py`

**Version:** `qwen_dataset.v1`; record schema `qwen_sft.v1`.

**What it is for.** Not general knowledge. Qwen3-8B already knows what Python is; a
corpus of generic Q&A about that would spend a fine-tune teaching the model something it
has and risk washing out what it does not have. Every row targets a behaviour a base
model gets *wrong about NEXUS specifically*: interpreting a request the way this product
means it, planning in Nexo's own surfaces and verbs, reasoning about which of 185 real
routes answers a question, emitting structured actions without inventing any, noticing
ambiguity and asking instead of guessing, and escalating the right requests while
handing the rest back to the router.

**The system preamble.** `NEXO_SYSTEM_PROMPT` is one constant used unchanged by every
row, so base and fine-tuned models are compared on the same prompt and the measured
difference is the fine-tune rather than a different preamble. Its routing table is
rendered from `INTENT_SPECS` at import time, not retyped, so the preamble cannot drift
from the label set the runtime routes on. It states the two rules that bind every
response:

- **Null, never zero** — the contract of `developer_features.v1` and its three siblings.
- **You never act without confirmation** — every `RecommendationType` names an action a
  **person** takes. "NEXUS proposes; the person decides."

**Ten behavioural categories** (`_Category`), each with its own generator function and
its own derived RNG stream, in a closed enum because `metadata['category']` is a training
filter:

| Category | What it teaches |
| --- | --- |
| `intent_interpretation` | bare nouns, anaphora with no antecedent, comparatives with no baseline, look-back vs look-forward, "can you" as a capability question, overloaded domain words, misfiled objects |
| `multi_step_planning` | ordered plans where every step names a real route, reads before writes, explicit abort conditions, a plan that must survive having nothing to plan with |
| `tool_reasoning` | discriminating between real routes (185 exist, domains overlap), naming the runner-ups rejected and the property that decided it, permission reasoning |
| `structured_action` | proposals with per-field provenance, explicitly-null fields, the gating permission and the resulting activity event |
| `ambiguity_handling` | asking the shortest disambiguating question instead of guessing between two writes |
| `escalation` | which requests belong to the router and which belong to the large model |
| `nexo_workflow` | multi-surface Nexo journeys |
| `coding_assist` | Nexo-flavoured engineering help |
| `analysis` | reading deterministic analytics correctly |
| `planning` | capacity and scheduling reasoning |

**Grounding is enforced, not asserted.** `_Grounding.check()` runs on every candidate
response before it becomes a row and raises `DatasetError` for two failures, both of
which are invisible to a human skimming a 400-row file and fatal to a fine-tune:

- an undeclared route (`_ROUTE_MENTION` matches any `METHOD /path` in the text and checks
  it against the inventory, after stripping trailing punctuation);
- a claim that NEXUS already performed the action (`_AUTO_EXECUTE_CLAIM`, matched at
  sentence-initial position only, so *"I have completed 14 tasks this month"* — a true
  statement about the person's history — is not mistaken for one).

**Volume.** `--per-category` defaults to **40**, chosen in the code as the smallest
number at which every generator still contributes most of its distinct behaviours.
"Forty is already the point where extra volume costs quality." A category that cannot
reach 40 distinct rows **reports its real count** rather than padding — the honest number
is more useful than a padded one. That gives 10 × 40 = **400 rows**.

**Build mechanics.** Same per-category isolation as routing: `random.Random(seed *
1000003 + category_index)`. `_collect()` drops a candidate whose `near_duplicate_key`
collides and draws the next, bounded by `target * 6 + 48` attempts or generator
exhaustion, whichever comes first. The `seen` set *is* shared across categories here,
and `routing.py`'s equivalent shares it across intents as well — the shadowing that
used to defeat that is gone (§11.1), so cross-category and cross-intent duplicate
instructions are both genuinely impossible.

### 2.5 What the current prepare actually produced

From `ml/datasets/dataset_manifest.json` (seed `20260101`, taxonomy
`nexo_intents.v1`):

| | routing | qwen |
| --- | --- | --- |
| Dataset version | `routing_dataset.v1` | `qwen_dataset.v1` |
| Record schema | `routing_intent.v1` | `qwen_sft.v1` |
| Total rows | 2100 | 400 |
| Per class | 150 × 14 intents | 40 × 10 categories |
| Provenance | `{"synthetic": 2100}` | `{"synthetic": 400}` |
| train / validation / test | 1470 / 322 / 308 | 280 / 60 / 60 |

Every file the prepare wrote is listed in the manifest with a **sha256** and, for JSONL,
a row count: the six split files, `capability_inventory.json`, `label_map.json` and
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

**Record-level, shared by all three dataset validators** (`_parse_or_flag`):

| Code | Severity | What |
| --- | --- | --- |
| `malformed_record` | ERROR | the row is not a JSON object, or its `from_dict` refused it (parse failures are grouped by message, so a systematic builder bug produces one counted finding rather than ten thousand) |
| `missing_schema_version` | ERROR | no `schema_version` declared — not coerced and not guessed at |
| `unknown_schema_version` | ERROR | a version from a revision this pipeline cannot read |
| `mismatched_schema_version` | WARNING | a readable version, but not this dataset's |
| `missing_field` | ERROR | a field the model would read as empty (`text`/`intent`, `instruction`/`response`/`system`, `subject`/`source_schema_version`) |
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

**Qwen** (`validate_qwen_dataset`):

| Code | Severity | What |
| --- | --- | --- |
| `duplicate_example` | ERROR | an `(instruction, response)` pair repeated |
| `contradictory_response` | ERROR | one normalised instruction paired with more than one response |
| `near_duplicate_text` | WARNING | on the instruction |

The contradiction looks different here — not one text with two labels, but one
instruction with two different answers. A QLoRA run fitted on that pair reproduces
whichever target it saw last, and the comparison against the base model still looks like
a result. Counts: `records`, `usable`, `distinct_instructions`, `contradictions`,
`templates`, and a `provenance:<name>` key per provenance value.

**Feature rows** (`validate_feature_dataset`) — no label, so the null contract is what
applies: `duplicate_row` (ERROR) on a subject repeated within one schema version, and
`incomplete_row` (WARNING) for rows with at least one uncomputable column. The schema's
`from_dict` already refuses the failure that matters most — a column marked unavailable
that still carries a value — because that is the one that silently reintroduces the
fabricated zero the Phases 8/9 remediation removed.

**Splits** (`validate_splits`) — see §4.3. Codes: `split_leakage` (ERROR) and
`empty_split` (WARNING).

**Free text** (`validate_no_credentials`) — the gate for the places a dataset is not: the
run manifest, the evaluation transcript, the notebook that assembles the prompt. Each is
assembled by hand or from environment values, which is exactly the code path that has put
a key into a log line somewhere before.

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
  families into units first. The pipeline uses the second for both corpora
  (`ml/train.py:798`, `_split_records`).
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

`stage_prepare` audits **each corpus separately**, and the reason is worth stating: leakage
is a within-partition property. A routing utterance and a qwen instruction that happen to
share a bag of words are two rows of two different training sets; pooling them into one
audit reports a collision no model can commit and no split can fix.

**Current state:** both audits PASS. `ml/reports/routing_splits.md` —
`leaked_keys 0`, `unique_keys 2100`, `total_items 2100`, 1470/322/308.
`ml/reports/qwen_splits.md` — `leaked_keys 0`, `unique_keys 400`, `total_items 400`,
280/60/60.

`ml/datasets/splits.json` records `split_config_version`, the seed, the three fractions,
`group_by`, and the full `key → split` assignment per corpus, so a downstream consumer
re-derives the partition without holding the original list order.

---

## 5. The small model — `backend/ml/configs/small_model.toml`

Loaded by `ml/training/config.py` into a pydantic `SmallModelConfig`
(`extra="forbid"` — a typo like `max_seq_lenght` is rejected rather than silently
leaving the default in place while the manifest reports a deliberate value). The TOMLs are
versioned precisely so the reasoning travels with the number.

| Field | Value | Why |
| --- | --- | --- |
| `base_model` | `microsoft/deberta-v3-base` | The smallest pretrained encoder that still separates the near-synonymous intents reliably. A smaller encoder collapses `reschedule_task` vs `update_estimate`. |
| `num_labels` | `14` | The size of the closed label set — the routing destination, **not** the twelve `RecommendationType` members. `stage_prepare` asserts this against `len(INTENT_NAMES)` and refuses to write splits on a mismatch: "A head sized for the wrong count does not fail, it just never predicts the missing class." |
| `max_seq_length` | `128` | The longest intent utterance runs to roughly 40 subword tokens. Longer adds only padding, and every extra position costs quadratically in attention. This is also the serving cap, so training and inference truncate identically. Validated to `32 ≤ n ≤ 512`. |
| `learning_rate` | `0.00002` (2e-5) | The standard encoder fine-tuning rate. A corpus this small starts overfitting the templates above ~5e-5, and the intent set is closed, so there is no pretraining benefit to a larger rate. |
| `weight_decay` | `0.01` | The usual AdamW decoupled decay. It matters more here than usual because most of the 183M parameters are embeddings the task never updates; decaying them keeps the representation from drifting on a small corpus. |
| `num_train_epochs` | `4` | Four epochs over ~1470 training utterances. The TOML records that **three was tried first and stopped while training loss was still falling (2.64 → 1.70 and still descending)**, which is an under-trained encoder rather than a converged one; five is where validation macro-F1 started to fall in a sweep. |
| `per_device_train_batch_size` | `16` | Fits comfortably in 24 GB at 128 tokens, and is small enough that each optimiser step still sees variety across the labels. |
| `per_device_eval_batch_size` | `32` | Evaluation passes, so it can be 2× the training batch without affecting peak memory during the training step. |
| `warmup_ratio` | `0.1` | Short schedule on a small dataset: without warmup the first few hundred steps carry the full learning rate and destabilise the encoder. |
| `weighting_strategy` | `"balanced"` | Inverse-frequency loss weighting. Generated intent sets are rarely uniform, and without it a rare-but-actionable intent is swamped by a common one. Closed set `{none, balanced}` — there is no third option because an unrecognised spelling would otherwise be silently treated as `none`. |
| `seed` | `20260101` | One seed for the split, the initialisation and every sampler, recorded in the manifest so the run is reproducible from the artifact alone. |
| `gradient_checkpointing` | `false` | At 128 tokens the activations for a 183M-parameter encoder are small, and recomputing them costs roughly 30% throughput to save memory that is not needed. |
| `save_every_n_steps` | `100` | Every 100 steps so an interrupted Kaggle session resumes rather than restarts, while keeping the artifact directory small. |
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

`stage_evaluate` then scores the held-out test split through
`ml.evaluation.metrics.evaluate` — the same code that produces the trainer's own
validation numbers — so the report, the manifest and the trainer cannot disagree by
construction. Metrics: accuracy, macro F1, weighted F1, per-class precision/recall/F1 with
support, a full confusion matrix and (optionally) top-k accuracy, rendered as Markdown
with `_REPORT_PRECISION = 4` decimals. Every stage writes a timestamped manifest to
`ml/reports/manifests/` carrying a sha256 for every file it claims to have produced.

---

## 6. Qwen QLoRA — `backend/ml/configs/qwen_qlora.toml`

Loaded into `QwenConfig`, also `extra="forbid"`. The TOML opens with the memory
arithmetic that constrains every value:

```text
8B parameters at 4 bits        ~= 4 GB resident, the dominant fixed cost
LoRA adapters in bf16 + AdamW  ~= 0.5 GB
activations at 2048 tokens     = the term that decides whether we OOM
```

Activations do not shrink with quantisation, so the accelerator is spent by keeping them
small: one sequence at a time, recomputed in the backward pass, accumulated until the
optimiser sees a batch of 16.

### 6.1 Quantisation

| Field | Value | Why |
| --- | --- | --- |
| `base_model` | `Qwen/Qwen3-8B` | Kept frozen and 4-bit; only the adapters train. |
| `method` | `"qlora"` | **Closed.** Full fine-tuning of 8B needs far more than the 24 GB available and the brief rules it out. `_check_quantisation` rejects anything else at load rather than letting a run start and die in the allocator. |
| `load_in_4bit` | `true` | **Closed.** Also rejected if false — unquantised 8B fine-tuning does not fit. |
| `bnb_quant_type` | `"nf4"` | NF4 (normal float) beats plain FP4 at this model size and is what the bitsandbytes kernels are tuned for. `fp4` is accepted only for a kernel without NF4 support. |
| `bnb_compute_dtype` | `"bfloat16"` | Matmuls and adapter optimiser states in bf16 rather than fp32: same exponent range as fp32, so no range loss, and half the memory traffic. fp16 would risk overflow in the attention logits. |

The notebook realises this as a `BitsAndBytesConfig` with `load_in_4bit=True`,
`bnb_4bit_use_double_quant=True` and a compute dtype of bf16 when the GPU supports it,
falling back to fp16 (and setting the `bf16`/`fp16` `SFTConfig` flag accordingly).

### 6.2 The LoRA adapter

| Field | Value | Why |
| --- | --- | --- |
| `lora_r` | `16` | Nexo contributes a few hundred supervised examples; a much larger adapter has enough capacity to memorise them outright — a training loss with no generalisation behind it. Below ~8 it cannot represent Nexo-specific vocabulary against an 8B base. Validated to `4 ≤ r ≤ 128`. |
| `lora_alpha` | `32` | `2×` the rank, the conventional setting; keeps the effective update magnitude in the range the base model's activations were trained under. |
| `lora_dropout` | `0.05` | At a few hundred examples this is the main defence against memorising individual training prompts. |
| `lora_target_modules` | all seven: `q_proj`, `k_proj`, `v_proj`, `o_proj`, `gate_proj`, `up_proj`, `down_proj` | Attention **and** MLP. Nexo's behaviour changes are not localised to attention — deciding that a task should be broken down involves the MLP too — so restricting LoRA to `q_proj`/`v_proj` would leave the adapter half-applied. |

The adapter is also `bias='none'`, `task_type='CAUSAL_LM'`, and the notebook calls
`MODEL.enable_input_require_grads()` before enabling gradient checkpointing — without it
the checkpointed blocks see no trainable input at all, because the base is frozen.

### 6.3 Optimisation

| Field | Value | Why |
| --- | --- | --- |
| `max_seq_length` | `2048` | Covers a Nexo reasoning answer with its system preamble and evidence attached. Longer exceeds what one sequence can hold on the device; shorter truncates mid-argument. Validated to `256 ≤ n ≤ 8192`. |
| `learning_rate` | `0.0002` (2e-4) | 10× the encoder rate because only ~0.1% of parameters are trainable. The adapter is small, so it can take a larger step than the full model can. |
| `num_train_epochs` | `2` | Two epochs over a few hundred examples. Past that the adapter reads back its training prompts rather than generalising. |
| `per_device_train_batch_size` | `1` | **The memory term.** Activations for 2048 tokens are what the remaining VRAM goes on. |
| `gradient_accumulation_steps` | `16` | Accumulate 16 micro-batches to reach the effective batch of 16 the optimiser needs. Gradient statistics are indistinguishable from a real batch of 16; peak activation memory is not. |
| — effective batch | **16** | `QwenConfig.effective_batch_size`, enforced to be ≥ `MIN_EFFECTIVE_BATCH_SIZE = 8`. The **product** is checked, not the micro-batch, because the micro-batch is already bounded by memory alone and the product is what an edited TOML breaks quietly. A config that silently dropped to an effective batch of 2 trains, and trains badly, without explaining itself. |
| `warmup_ratio` | `0.03` | The QLoRA literature converges faster than the encoder because so few parameters move, so a long warmup wastes steps the adapter needs. |
| `lr_scheduler_type` | `"cosine"` | Decay to near zero over the run, so the final adapter settles instead of oscillating around the optimum at a constant step size. |
| `weight_decay` | `0.0` | None. The adapter is ~0.1% of the model and already heavily regularised by dropout; decay on top of that shrinks the Nexo-specific signal it is learning. |
| `gradient_checkpointing` | `true` | Recompute activations in the backward pass instead of storing them. Combined with micro-batch 1, this is what keeps a 2048-token sequence on one card. |
| `max_grad_norm` | `0.3` | Tighter than the usual 1.0. A few hundred examples produce spiky gradients from long structured answers, and an unclipped step can undo an epoch of progress in one update. |
| `seed` | `20260101` | Shared with the classifier and with the dataset split. |

### 6.4 Cadence and segmentation

| Field | Value | Why |
| --- | --- | --- |
| `segment_steps` | `250` | Write a resumable segment every 250 steps. Kaggle sessions are killed on a timer, and a segment boundary is where a run can resume without losing more than a few minutes of compute. |
| `save_every_n_steps` | `100` | Checkpoint at **twice** the segment cadence. The adapter is small, but the optimiser state is not, and keeping both is what makes a resume correct rather than merely plausible. |
| `eval_every_n_steps` | `100` | The eval pass on a few hundred held-out examples is seconds, so there is no reason to go longer and lose the early signal on whether the adapter is learning at all. |

**Segmentation semantics.** A QLoRA run is not one notebook. `--train-qwen` renders and
pushes **one segment** at a time. `max_steps` is the number of optimiser steps *that
segment adds*, not the total. The local `qwen_run.json` records `segment_index` and
`max_steps` alongside `run_id`, `dataset_slug`, `notebook_sha256`, `base_model`, `seed`
and the local accelerator probe — so a later segment, or a reader, knows exactly which
piece of which run this is.

---

## 7. Checkpointing and resume

There are **two** checkpoint formats, deliberately, because there are two machines.

### 7.1 `ml/training/checkpoint.py` — the local, torch-free format

> **The machine that resumes a run is not the machine that trains it.**

The GPU notebook serialises tensors and copies the results out; this module never imports
torch and never unpickles anything. It writes bytes, hashes what it wrote, records the
step, and answers "which checkpoint is the furthest I got, and is it whole?" — a
question that has to be answerable on a laptop, in CI, and on a machine with no torch at
all, because that machine is the one that decides whether a run is worth resuming. A
design that made that answer require importing the library the resume exists to avoid
would answer it nowhere.

**The format.** A directory: payload files plus one `checkpoint.json`.
Format version `nexo_checkpoint.v1`.

```text
step-100/
├── checkpoint.json          ← written LAST; its presence is the commit point
├── extra.json               ← optional scalar state (step counters, RNG, loss history)
├── adapter.safetensors      ← or model.safetensors, tokenizer.json, ...
└── ...
```

**`CheckpointMetadata` fields.** `format_version`, `run_id`, `model_name`, `global_step`,
`epoch`, `segment_index`, `segments_completed`, `dataset_version`, `dataset_checksum`,
`code_commit`, `config`, `hyperparameters`, `seed`, `created_at`, `elapsed_seconds`,
`files`. The resume-relevant ones are `global_step`, `epoch`, `segment_index` and
`segments_completed` — the trainer processes a dataset in segments, so a resume has to
know which segment the run died in as well as how far it got, or it restarts the epoch
with a shuffle it has already consumed. `dataset_version` and `dataset_checksum` are both
present so a resume can refuse to continue onto changed data: resuming onto a different
dataset is worse than not resuming, because the result looks like a continuation and is
not one.

`files` maps a **logical name** to a filename inside the directory, so a consumer asks
for `state.directory / state.metadata.files["adapter.safetensors"]` and never hardcodes a
filename this module chose. Logical names are restricted to `[A-Za-z0-9._-]+` with no
separators, traversal, absolute paths or drive letters. A name with no extension gets
`.bin`; a name with one keeps it.

**The crash contract, which is the whole point.** Order of operations in
`save_checkpoint()`: payload files → `extra.json` → the `save_sidecar` hook → **metadata
last**. Every file is written to a temporary name and moved into place with `os.replace`,
so a reader never observes a partial payload. A directory that exists without its
metadata is therefore an *interrupted write* — not a checkpoint, but debris — and both
`is_resumable()` and `latest_checkpoint()` refuse it. Corrupting a run by resuming from a
half-written one is worse than restarting it, because the failure surfaces hours later as
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

### 7.2 The notebook's checkpoint and resume — `ml/kaggle/notebook.py`

> **Resume is real, not nominal.** A resumed segment restores the adapter in trainable
> mode, the optimizer and scheduler state, and the RNG state of `torch`, `numpy` and
> `random`, then skips exactly the micro-batches a continuous run would already have
> consumed. Without the RNG restore a resumed run differs from an uninterrupted one in
> dropout **and in nothing else you can easily see**; the number still looks plausible,
> which is what makes it dangerous.

A remote segment directory:

```text
step-<n>/
├── adapter_config.json       ┐ from PeftModel.save_pretrained — the 4-bit base weights
├── adapter_model.safetensors ┘ are NEVER written to disk by this notebook
├── optimizer.pt
├── scheduler.pt
├── rng_state.pt
├── trainer_state.json        ← global_step is the resume point
└── checkpoint.json           ← run_id, segment_index, segment_max_steps, global_step,
                                base_model, dataset_slug, dataset_sha256, seed,
                                library_versions, micro_batches_per_epoch,
                                resume_micro_batches, files[], resume_hint
```

**`NexoCheckpointCallback`** fires `on_step_end` every `save_every_n_steps` steps
(measured as `resumed_step + state.global_step`, so segment 1's step 100 is step 350 of
the whole run) and always on `on_train_end` — so the final step of a segment is written
whether or not it lands on a cadence boundary. It sets `control.should_save = False`,
because the HuggingFace `save_strategy` is set to `'no'`: **this callback is the only
thing that writes a checkpoint**, so there is exactly one format and one cadence.

The trainer/callback circular dependency is broken by constructing the callback first,
building the trainer with it, then assigning `CHECKPOINT_CALLBACK.trainer = TRAINER` —
the callback needs the optimizer and scheduler the trainer owns, and the trainer needs the
callback to exist before it starts.

**`_CELL_QWEN_RESUME`** is the restore path:

1. `RESUMED_STEP` from `trainer_state.json`; both `resumed_step` and `last_step` on the
   callback are set to it.
2. `optimizer.pt`, `scheduler.pt`, `rng_state.pt` loaded with `weights_only=False` —
   with the reason stated in the notebook source: a `LambdaLR` carries its lambda objects
   and is not on the allowlist, and these files were written by this notebook moments ago,
   not downloaded.
3. `TRAINER.optimizer.load_state_dict()` and `TRAINER.lr_scheduler.load_state_dict()` —
   without these, a resumed run's learning-rate schedule restarts from the beginning while
   its weights are already 350 steps in, and the mismatch is invisible in the loss curve.
4. `_restore_rng_state()` — torch, numpy, python, and `cuda_rng_state_all` when CUDA is
   present.
5. `RESUME_MICRO_BATCHES = (RESUMED_STEP * GRADIENT_ACCUMULATION_STEPS) % MICRO_BATCHES_PER_EPOCH`,
   then **floored to an optimizer-step boundary** (`-= RESUME_MICRO_BATCHES %
   GRADIENT_ACCUMULATION_STEPS`), assigned to `TRAINER.args.skip_first_batches`, and
   printed. The floor is there so the gradient accumulator is never split across a resume.

Together these make a resumed run **bit-comparable** to an uninterrupted one — which is
the only reason to checkpoint at all, because otherwise a two-segment run and a one-segment
run are simply different runs with the same name.

**Tokenisation is completion-only.** `_CELL_QWEN_TOKENIZE` applies the chat template to
`{system, instruction}` with `add_generation_prompt=True`, tokenises the prompt and the
response separately, concatenates, truncates to `max_seq_length`, and sets
`labels = [-100] * prompt_length + target_ids`. A record whose response is truncated away
entirely returns `None` and is dropped — keeping it would train the model on nothing but
masked prompt tokens. `PreTokenizedSFTTrainer` overrides `_prepare_dataset` to a no-op
because **the prompt/completion boundary is only known to the code that applied the chat
template**; letting SFTTrainer re-tokenise from text would discard the mask and the model
would be trained to reproduce the instruction and the system preamble as readily as the
answer.

The collator is `DataCollatorForSeq2Seq(tokenizer, padding=True, label_pad_token_id=-100)`.

---

## 8. The Kaggle workflow — `ml/training/remote.py`

### 8.1 The design constraint

The backend `requirements.txt` is pinned and carries no `torch`, no `transformers` and no
`kaggle`. This module is the seam between the local pipeline and the remote one, and it
shells out to the `kaggle` CLI with `subprocess` **and nothing else**: no `kaggle` Python
package, no `requests`, no third-party import of any kind. Same reason as everything else
in `ml/` — the contributor's clone and CI must stay runnable without a multi-gigabyte
download standing between them and a test run.

**Credentials are never read here.** Nothing opens `~/.kaggle/access_token` or any other
credential file, and nothing logs, returns or embeds a credential *value*. Authentication
is the CLI's job; this module's job is to notice that it is absent. Username discovery
runs `kaggle kernels list -m --format json` and reads the `author` field the API already
returned — a successful private listing is evidence of authentication in a way that the
presence of a config file is not.

Every string that goes into an exception passes through `redact()` first, because the
Kaggle CLI is perfectly capable of echoing a token back in a traceback. `RemoteError`
scrubs its `command` and `stderr` **on construction** rather than at the call sites that
happen to remember, because the one place that has to be right cannot be left to every
future caller's diligence.

### 8.2 The GPU-probe contract

> **A GPU in the metadata is not a GPU.**

Verified against Kaggle CLI 2.2.4. A kernel pushed with `enable_gpu: true` and
`gpu_type_option: "T4x2"` comes back from the API with `machine_shape: "NvidiaTeslaT4"`
recorded in its metadata — and the executed environment had **no** `/dev/nvidia*` device
node and **no** `nvidia-smi` binary on `PATH`. The metadata is a *request* echoed back,
not an observation. Anything reporting "GPU enabled" on the strength of kernel metadata
is reporting the request.

**The contract.** `render_gpu_probe_notebook()` emits a one-cell notebook — no installs,
no downloads, a few seconds — that writes `nexo_gpu_probe.json` into its own output
directory:

```json
{ "nvidia_smi": bool,
  "device_nodes": ["/dev/nvidia0", ...],   // paths that really exist
  "torch_cuda_available": bool | null,
  "recorded_at": "YYYY-MM-DDTHH:MM:SSZ" }
```

It also resolves `huggingface.co` and `pypi.org`, because "no accelerator" and "no
network" are independent blockers and a report that only checks one leaves the operator
with half the diagnosis.

**`verify_gpu_available(force=True)`** reads that file and treats the presence of a device
node **or** a working `nvidia-smi` as the only admissible evidence. It never returns
`True` without a probe behind it. It returns `(False, "not yet probed …")` when the file
is absent. With `force=True` a probe older than `NEXO_GPU_PROBE_MAX_AGE_SECONDS` (default
86400) is refused — accelerator availability is a property of the machine rather than of
the account, so yesterday's T4 says nothing about today's; with `force=False` the newest
probe is reported whatever its age, for the case where a stale answer beats no answer. A
missing or unparseable `recorded_at` makes the probe **unusable rather than infinitely
old**, because without one the freshness window cannot be applied honestly.

Location: `NEXO_REMOTE_ARTIFACT_DIR`, defaulting to `artifacts/remote` relative to the
working directory. `stage_probe_remote` sets it per-call so the answer depends on
`--artifacts-dir` and not on whatever the shell happened to export.

`KaggleEnvironment.gpu_quota_hours` is deliberately a **quota**, not a capability claim —
an account property, honestly obtainable. `None` means "the CLI did not report a figure we
could parse", never `0`, which would assert the quota is exhausted. `kaggle quota` on CLI
2.2.4 prints a **fixed-width table**, not JSON; reading the first number yields the *used*
column and would report an exhausted account, so `_parse_quota()` locates the columns from
the header and reads the `GPU` row's `remaining` positionally. A format it does not
recognise yields `None`, not a guess.

### 8.3 The client — `KaggleClient`

Every call is a subprocess with `capture_output=True`, so stdout and stderr stay separate;
several calls parse stdout as JSON and merging the streams would let a warning line
corrupt the parse. Nothing is cached between calls except the constructor's environment
overrides, because a cached status is a status that was true at some point other than now.

| Method | Command | Behaviour |
| --- | --- | --- |
| `probe()` | `--version`, `quota`, `kernels list -m --format json` | Every unknown degrades to `None`/`False`. A CLI that is not installed is not an error — an unauthenticated local run is a legitimate state. |
| `create_or_version_dataset(...)` | `datasets create -p … -t … -d …` then `datasets version …` | First push creates; every push after must be a **version**, because a Kaggle dataset cannot be silently overwritten and the identity of revision N is what a manifest has to cite. Hence the two-step attempt rather than a version-first strategy that fails on a first push. |
| `push_kernel(workdir)` | `kernels push -p …` | Requires `kernel-metadata.json`; returns ref, version, URL. |
| `kernel_status(ref)` | `kernels status` | `state` normalised to a closed set, `raw` keeps the scrubbed CLI text because "why did my state become UNKNOWN" is unanswerable without it. |
| `wait_for_kernel(ref, …)` | `kernels status` in a loop | Default `poll_seconds=30`, `max_seconds=43200` (12 h). **Returns whatever terminal state was observed, including `ERROR` and `CANCELLED`** — the caller asked what happened and an exception would throw away the CLI's own wording. A caller treating this as success without checking `status.state` is the bug. Reaching the ceiling **raises**, because a queue that never starts must not be reported as a training run. |
| `kernel_output(ref, dest)` | `kernels output ref -p dest` | 1800 s timeout — the client's general 300 s default is not enough for a checkpoint. |
| `kernel_logs(ref, dest)` | `kernels output … --log` | Same timeout. |
| `delete_kernel(ref)` | `kernels delete` | **Not** wrapped in a retry: a delete that appears to fail because the network blipped has already succeeded, and retrying is how a slug gets reused. |
| `list_kernels()` | `kernels list -m --format json` | Phase 10 kernels only (slug carries `KAGGLE_REF_PREFIX`). Never raises on a failed listing — a client that cannot see its own kernels has nothing useful to say about them. CLI 2.2.4 returns no version field, so `version` is `0` rather than invented. |

**State normalisation.** `KERNEL_STATES = {PENDING, RUNNING, COMPLETE, ERROR, CANCELLED,
UNKNOWN}`; `TERMINAL_STATES = {COMPLETE, ERROR, CANCELLED}`. Nineteen aliases
(`QUEUED`→`PENDING`, `SUCCEEDED`→`COMPLETE`, `ABORTED`→`CANCELLED`, …) matched
case-insensitively, then substring heuristics, then `UNKNOWN`. Seventeen aliases
(`QUEUED`→`PENDING`, `SUCCEEDED`→`COMPLETE`, `ABORTED`→`CANCELLED`, …) matched
case-insensitively, then substring heuristics, then `UNKNOWN`. A raw string outside the
set becomes `UNKNOWN` rather than being passed through, so a caller switching on `status.state`
sees a closed set.

### 8.4 `kernel-metadata.json`

Two entries are load-bearing and were found the hard way on CLI 2.2.4:

- **`kernel_type` must be present and equal to `"notebook"`.** Omitting it does not fall
  back to a default: `kernels push` aborts with *"A valid kernel type must be specified in
  the metadata"* **before it uploads anything**.
- **`id` must be the full `<username>/<slug>` ref, and `title` must slugify to that same
  slug.** When they disagree Kaggle warns and then resolves identity from the *title* — so
  a mismatched `id` does not fail loudly, it silently pushes to a different kernel than
  the caller named. `render_kernel_metadata()` raises rather than pushing.

The rest: `code_file`, `language="python"`, `enable_gpu`, `gpu_type_option`, and
`enable_internet=True` (the notebook must download an 8B base model from HuggingFace).

`slugify()` reproduces Kaggle's own normalisation — fold to ASCII, lowercase, collapse
non-alphanumeric runs to a single hyphen, truncate at 50 chars on a hyphen boundary —
because Kaggle resolves identity from the title and the slug and the title have to agree.
Every slug this module creates begins `nexo-phase10`, so a Phase 10 run is greppable in
an account that also hosts unrelated experiments.

### 8.5 The stage flow in `ml/train.py`

There are **seven** selectable stages (`STAGES`, `ml/train.py:1947`): `prepare`,
`train-small`, `evaluate`, `train-qwen`, `probe-remote`, `eval-qwen`, `qwen-status`.
`--probe-remote` and `--eval-qwen` are the two that were added after this section was
first written; the rest of the flow is unchanged.

**`--probe-remote` (`stage_probe_remote`).** Probe the CLI → render the one-cell probe →
push → wait (`PROBE_POLL_SECONDS=20`, `PROBE_TIMEOUT_SECONDS=3600`) → download the output
into `ml/artifacts/remote`, where the kernel's own `nexo_gpu_probe.json` lands →
print machine, CPUs, torch + CUDA, `nvidia-smi` path, device nodes and DNS results →
write **`ml/artifacts/qwen/gpu_probe_run.json`** carrying both what was **requested**
(`enable_gpu: true`, `gpu_type_option: "T4x2"`) and what was **observed**, plus
`gpu_available` and the verdict sentence. Re-running pushes a new *version* of
`nexo-phase10-gpu-probe` rather than accumulating `nexo-phase10-probe-7` in the
account. `BLOCKED` when the CLI is unusable or the account cannot be identified — the
stage never pushes in that case, because a ref cannot be composed without a username.
A kernel that ends in any state other than `COMPLETE` is **FAILED**, and no
`gpu_probe_run.json` is written: a probe that did not run has nothing to testify to.
This is the only stage that is **expected** to report `BLOCKED` on a well-configured
account — a BLOCKED verdict here means the probe ran and found nothing, which is the
most useful thing it can do.

**`--train-qwen` (`stage_train_qwen`).** Two candidates, both probed before anything is
uploaded: the local torch interpreter, and — via a probe a remote kernel wrote about the
machine it ran on — the Kaggle session. Local first because a shortcut that works costs
nothing; remote second because it is the only one that can hold an 8B model in 4 bits.

- The memory floor is computed from the model name (`Qwen/Qwen3-8B` → `8e9` params ×
  `Q4_BYTES_PER_PARAM = 0.5` + `VRAM_HEADROOM_BYTES = 2 GiB`) and stated **in numbers**:
  the recorded blocker reads *"needs about 5.7 GiB (3.7 GiB of frozen 4-bit weights plus
  2.0 GiB of activations and optimiser state)"*.
- The remote candidate is admissible **only** on the strength of a probe file —
  `_remote_gpu_verdict` delegates to `verify_gpu_available`, which returns `False` with
  *"not yet probed"* when `ml/artifacts/remote/nexo_gpu_probe.json` is absent. There is no
  path that reports a remote accelerator from kernel metadata or from quota.
- If **neither** fits, the stage records **`BLOCKED`** — not failed, not faked — with the
  blocker sentence, the local probe, the remote probe and the remote verdict, and
  **still writes the notebook and the adapter input bundle**, so the run is one
  `kaggle kernels push` away on hardware that can hold it. A fabricated success here would
  be worse than no run at all: the artifact would be trusted.
- If one fits, the **push path is real** and runs in this order:
  1. `_publish_qwen_dataset` publishes `ml/artifacts/qwen/segment-000/input` as a Kaggle
     dataset (create on the first push, **version** on every one after, because a Kaggle
     dataset cannot be silently overwritten) and returns the `<username>/<slug>` ref. An
     input directory holding no `*.jsonl` raises `PipelineError` and the stage ends
     **BLOCKED** — publishing a corpus of nothing would produce a kernel that trains on
     nothing. Note the ordering: this runs **before** anything copies the splits into
     `segment-000/input`, so on a fresh tree it depends on an earlier run having
     materialised that directory. See §11.10.
  2. `_materialise_qwen_bundle` (inside `_push_qwen_kernel`) copies the splits in and
     renders the notebook **against the slug the publish step actually created** — not
     the placeholder `nexo-phase10/qwen_dataset.v1` a blocked run writes — so the pushed
     notebook mounts a revision that exists.
  3. The notebook is copied into a per-run push directory under `ml/artifacts/remote`,
     alongside a `kernel-metadata.json` whose `dataset_sources` names the published
     dataset.
  4. `push_kernel` then **`wait_for_kernel` in the same call**. Pushing and waiting is
     deliberately one call: a caller that pushed and returned would leave the operator
     polling by hand with no record of whether the run finished, failed or was cancelled.
     Waiting also means the stage's verdict is the kernel's **terminal state** rather
     than "accepted for execution", which is not a claim anyone should publish. Polling is
     `QWEN_POLL_SECONDS=60`, ceiling `QWEN_TIMEOUT_SECONDS=43200`.
  5. The kernel output is downloaded into `ml/artifacts/qwen/segment-000/output`, and
     `qwen_run.json` is rewritten with `kernel_ref`, `kernel_version`, `kernel_url`,
     `kernel_state` and `finished_at`. A CLI failure before the wait marks the record
     `FAILED`; a non-`COMPLETE` terminal state marks the **stage** `FAILED` even though
     the download is still attempted — a failed segment's partial checkpoints are the
     input to the next one.
- Publishing before pushing is what makes the run reproducible from the URL alone: the
  notebook mounts a slug, not a file, and the slug's revision is what a later segment or an
  evaluation run has to cite. It also keeps the corpus out of the kernel's own push
  directory, because Kaggle zips that whole directory and a corpus would travel twice.

**`--eval-qwen` (`stage_eval_qwen`).** The base-versus-adapter comparison, and the only
stage that refuses on principle rather than on capability. It runs three gates in order:

1. **No adapter ⇒ BLOCKED.** `_adapter_dir_name` searches `ml/artifacts/qwen/**` for an
   `adapter_config.json` and takes the shallowest match. If there is none, the stage says
   so and stops: *"a base-versus-base comparison would be reported as a fine-tuning
   result and is not one"*. There is deliberately no fallback to evaluating the base model
   alone.
2. **No verified accelerator ⇒ BLOCKED**, with the adapter directory still named as an
   artifact. The adapter is a remote artifact, so it is evaluated on the same kind of
   session that produced it; the probe requirement is the same `verify_gpu_available`
   contract `--train-qwen` uses.
3. Otherwise it renders `render_eval_notebook` into `ml/artifacts/qwen/eval-000/` and
   pushes it as its own kernel, waits with the same `QWEN_POLL_SECONDS` /
   `QWEN_TIMEOUT_SECONDS` cadence, downloads into `eval-000/output` on `COMPLETE`, and
   reports `PASSED` only when the terminal state is `COMPLETE`. **Read §11.11 before
   trusting this path against the real service:** the pushed kernel is given an empty
   `dataset_sources`, while the notebook locates both the held-out split and the adapter
   inside an attached dataset. The code path is exercised by the tests; it has never run
   on Kaggle.

**`--qwen-status` (`stage_qwen_status`).** Reports username, `authenticated`, CLI version
and GPU quota, then the accelerator verdict from the probe file; then, if `qwen_run.json`
carries a `kernel_ref`, polls that kernel's state and reports it; otherwise says plainly
that the rendered notebook is the handoff. Also lists the Phase 10 kernels the CLI can see
and counts local `checkpoints/step-*` directories. `BLOCKED` when the CLI is unusable,
since an unauthenticated machine is a legitimate state and not a failure.

**Stage statuses** are `PASSED` / `FAILED` / `BLOCKED`. `BLOCKED` exists so that "this
cannot run here, and here is exactly why" is not forced to masquerade as either success
or failure.

### 8.6 What actually happened

The two stages that leave a record of a remote exchange have been run against the real
account, and both records are on disk. Quoted from the files, not from console output.

**`--probe-remote` → `PASSED`, and the answer was no.**
`ml/artifacts/qwen/gpu_probe_run.json` records kernel
`utkarsh6624/nexo-phase10-gpu-probe` **version 2**, state `COMPLETE`, with:

```text
requested   {"enable_gpu": true, "gpu_type_option": "T4x2"}
observed    platform Linux-6.18.48+-x86_64-with-glibc2.39, 4 CPUs
            torch 2.11.0+cpu, torch_cuda_available false
            nvidia_smi false (path null), device_nodes []
            kaggle_run_type "Batch"
            internet: huggingface.co and pypi.org both gaierror (name resolution failed)
verdict     probe is current: no /dev/nvidia* device and no working nvidia-smi
gpu_available  false
```

Two things in that record are worth more than the verdict. `kaggle_run_type: "Batch"`
is the mechanism behind the §8.2 conclusion: a GPU request was honoured with a CPU-only
batch image. And the DNS results failed too, which is the independent blocker the
probe was written to surface — even a verified T4 would not have been enough, because
the notebook has to download an 8B base model from HuggingFace.

**`--train-qwen` → `BLOCKED`, with the artifacts written anyway.**
`ml/artifacts/qwen/qwen_run.json` records `status: "BLOCKED"` with:

```text
Qwen/Qwen3-8B under 4-bit QLoRA needs about 5.7 GiB (3.7 GiB of frozen 4-bit weights
plus 2.0 GiB of activations and optimiser state). Local: torch 2.14.1+cpu sees no CUDA
device; need 5.7 GiB. Remote: probe is current: no /dev/nvidia* device and no working
nvidia-smi. No training was attempted and no adapter exists.
```

The record also carries `required_vram_bytes: 6147483648`, the full `remote_probe`
block and `remote_verdict`, plus `dataset_slug: nexo-phase10/qwen_dataset.v1` — the
unprefixed placeholder a blocked run writes, since nothing was published. The notebook
and bundle were written regardless (`notebook_sha256` recorded, `segment-000/input/`
populated with `qwen_train.jsonl`, `qwen_validation.jsonl` and `label_map.json`).

**`--eval-qwen` stops at its first gate.** No `adapter_config.json` exists anywhere
under `ml/artifacts/qwen/` (verified by directory listing), so the stage would report
`BLOCKED` before the accelerator check is even reached. There is no Qwen adapter on
disk, none is claimed to, and **no QLoRA kernel has been pushed** — `qwen_run.json`
carries no `kernel_ref`.

### 8.7 The eval notebook

`render_eval_notebook()` scores base and fine-tuned Qwen on the same held-out split under
the same prompts, the same greedy decoding and the same seed, so the difference in the
report is attributable to the adapter and to nothing else. The rubric is deterministic:
it checks that the answer names the subject, takes an action a person can take, offers no
claim of having already executed anything, stays inside a sane length, and leaks no
credential. Defaults: `eval_max_rows=200`, `eval_max_new_tokens=512`, `eval_batch_size=8`.

> **Now wired to a stage.** This used to be the pipeline's one unwired renderer.
> `ml/train.py` imports `render_eval_notebook` (in the import block at
> `ml/train.py:91`) and `stage_eval_qwen`
> renders it, pushes it as a kernel and downloads the report — see §8.5. Two things
> about that wiring are honest limits rather than oversights: the stage **BLOCKS**
> without an adapter (§11.6 is no longer the reason, but the consequence is real — no
> fine-tune has run on any account this repository can reach, so there is no adapter
> and no evaluation has been executed), and there is still **no `ml-eval-qwen` Makefile
> target** (§11.7). `render_small_training_notebook` remains genuinely unused: the small
> model is trained locally on CPU instead, and no stage references it.

Notebook mechanics worth noting, because each fails silently otherwise:

- **Cell ids are mandatory** (`NBFORMAT_MINOR = 5`, the first version where cell ids exist
  and are validated). Every cell carries a stable readable id, checked against
  `[A-Za-z0-9][A-Za-z0-9_-]{0,63}`.
- **Config typos are refused.** `_merge_config` rejects a key that is *almost* a known key
  at a similarity threshold of 0.85 — where transposition typos (`lenght`, `epocs`,
  `ratioo`) end and unrelated names begin — because a typo that falls back to a default
  trains a run nobody asked for. A genuinely unrelated key like `label_smoothing` is
  carried into the notebook rather than dropped, so nothing the caller supplied is lost.
- **`RUN_MANIFEST_FIELDS`** mirrors `ml.training.manifest`; the notebook writer refuses to
  emit a payload whose keys differ from that tuple, because a notebook that could not
  write a manifest the local pipeline could parse would defeat the point.
- **Identifiers baked into source are validated.** `run_id`, `output_dir_name` and
  `adapter_dir_name` must match `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`; `resume_from` and
  dataset paths additionally refuse `..` and backslashes. A value containing a quote or a
  newline would escape the string literal it is interpolated into; a traversal would escape
  `/kaggle/input`. Both are configuration errors that must not reach a GPU.
- **trl API renames are handled by inspection.** `SFTConfig` is introspected with
  `fields()` and both spellings (`max_seq_length`/`max_length`,
  `evaluation_strategy`/`eval_strategy`) are passed; whatever the installed trl does not
  declare is dropped and **printed as `ignored kwargs`** — so a re-pin fails as a
  printout of what was ignored rather than as a run that quietly trained on the wrong
  sequence length.

### 8.8 What the stage tests assert — `backend/tests/test_ml_stages.py`

The remote stages are the only part of Phase 10 whose outcome depends on a machine
nobody in the repository can inspect, so they get a test module of their own, and its
design rule is stated in the module docstring: **every test asserts on the recorded
verdict — `qwen_run.json`, `gpu_probe_run.json`, the rendered notebook — never on
console output.** A test that only checked stdout would still pass if the file on disk
said something else, and the file on disk is what a later reader trusts.

No test here touches the network. `KaggleClient` is replaced by a `StubClient` that
records calls and refuses to answer anything the stages were not written to make
(an unstubbed call raises, rather than being papered over by a permissive mock), and
the local torch probe is monkeypatched to report a CPU-only interpreter.

What is pinned, grouped by the property it defends:

| Property | Representative tests |
| --- | --- |
| **The probe notebook is cheap, deterministic and asks the right four questions** | `test_the_probe_notebook_is_a_valid_notebook` (validated against `nbformat` when that optional dependency is present), `..._installs_nothing`, `..._is_deterministic`, `..._writes_the_file_the_verifier_reads` (the filename is a contract between two modules, so a rename must not pass silently) |
| **`probe-remote` separates the request from the machine** | `test_probe_remote_distinguishes_a_requested_gpu_from_a_present_one` writes a CPU probe and asserts `gpu_available is False`; `..._accepts_a_kernel_that_reports_a_device` writes a device probe and asserts `True` |
| **A probe that did not run records nothing** | `test_probe_remote_fails_rather_than_claims_success_when_the_kernel_errors` asserts `FAILED` **and** that no `gpu_probe_run.json` exists |
| **BLOCKED is a first-class outcome, distinct from FAILED** | `test_probe_remote_is_blocked_not_failed_when_the_cli_is_missing`, `..._never_pushes_when_the_account_is_unknown` (also asserts nothing was pushed) |
| **`train-qwen` blocks with numbers and still writes its artifacts** | `test_train_qwen_is_blocked_and_says_how_much_vram_it_needs`, `test_train_qwen_records_the_blocked_verdict_rather_than_only_printing_it`, `test_a_blocked_run_still_writes_the_notebook_and_the_bundle` |
| **The push path is real, in order** | `test_train_qwen_pushes_when_a_probe_verifies_a_remote_accelerator` asserts `create_or_version_dataset`, `push_kernel` **and** `wait_for_kernel` were all called; `..._refuses_to_push_an_empty_bundle` asserts `push_kernel` was **not** called and the detail names `--prepare`; `..._fails_when_the_kernel_errors` asserts the record carries `kernel_state: "ERROR"` |
| **`eval-qwen` refuses the comparison that must never be published** | `test_eval_qwen_refuses_to_compare_the_base_model_against_itself` asserts `BLOCKED` and the literal `"base-versus-base"` in the detail; `..._is_blocked_when_an_adapter_exists_but_no_accelerator_does`; `..._pushes_the_paired_comparison_when_it_can` |
| **Every stage is reachable, and flags select a set** | `test_every_stage_is_reachable_from_the_command_line` (parametrised over all seven flags), `test_stage_flags_select_a_set_not_a_sequence`, `test_no_flag_runs_every_stage` |
| **The remote artifact directory is scoped, not global** | `test_the_remote_artifact_directory_is_scoped_and_restored` (nested scopes restore correctly), `test_an_exported_remote_directory_survives_the_scope`, `test_the_verdict_is_not_available_without_a_probe_on_disk` |

The module is picked up by `make ml-test`, whose glob is `tests/test_ml_*.py`. Run it
with `cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_ml_stages.py`.

---

## 9. The `ml-*` Make targets

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
| `ml-prepare` | `ml.train --prepare` | stdlib only, no GPU | Harvests the inventory, cross-checks `num_labels` against the taxonomy, builds both corpora, refuses to continue unless every validator passes, splits without leakage, writes JSONL splits + `label_map.json` + `capability_inventory.json` + `splits.json` + `dataset_manifest.json`. |
| `ml-datasets` | `ml-prepare` | — | Alias. |
| `ml-validate` | `ml-prepare`, then prints every `ml/reports/*.md` | stdlib only | Depends on `ml-prepare`, so the reports it prints are never stale. Tells you to run `make ml-prepare` first if the directory is empty. |
| `ml-train-small` | `ml.train --train-small` | **torch** (`$(ML_PY)`) | Fine-tunes the routing classifier locally on CPU. Delegates to `ml.scripts.train_small_local` in a subprocess under the ML interpreter. |
| `ml-train-small-resume` | `ml.train --train-small --resume` | **torch** | Resumes from the latest local checkpoint after checking it belongs to the current data (same checksum, same dataset version). |
| `ml-eval` | `ml.train --evaluate` | stdlib + the trained artifact | Scores the held-out test split and writes the evaluation reports and a timestamped manifest. |
| `ml-train-qwen` | `ml.train --train-qwen` | Kaggle CLI + a verified accelerator | Publishes the LoRA corpus as a Kaggle dataset, renders the notebook against that slug, pushes one QLoRA segment as a kernel, waits for the terminal state and downloads the output. `BLOCKED` (artifacts still written) when no accelerator is verified. |
| `ml-probe-remote` | `ml.train --probe-remote` | Kaggle CLI | Pushes a one-cell kernel that records whether Kaggle actually gave us a GPU, into `ml/artifacts/remote/`, and records the requested-vs-observed verdict in `ml/artifacts/qwen/gpu_probe_run.json`. |
| `ml-qwen-status` | `ml.train --qwen-status` | Kaggle CLI (optional) | Reports the account, the quota, the accelerator verdict, and the state of a pushed kernel. |
| `ml-all` | `ml-prepare` → `ml-train-small` → `ml-eval` | torch | The whole **local** pipeline. |
| `ml-test` | `pytest tests/test_ml_*.py` | `$(PY)` (the backend venv) | Only the ml test modules, which now includes `tests/test_ml_stages.py`. |

**There is no `ml-eval-qwen` target.** `--eval-qwen` is a real stage with a real flag and
a real renderer behind it (§8.5), but the Makefile has no target for it — the `.PHONY`
list stops at `ml-qwen-status` (`Makefile:68`, `:196`). Run it as
`cd backend && ../.venv/Scripts/python.exe -m ml.train --eval-qwen`. Recorded as §11.7
rather than quietly described as "make ml-eval-qwen".

**`ml-train-qwen` is deliberately not part of `ml-all`.** Qwen3-8B under QLoRA does not
fit the 4 GB of VRAM on this machine, and the probe records no GPU on the remote side
(§8.6), so folding it in would mean an "all" that cannot actually run.

The other useful flags, all on `ml.train`:

| Flag | Default | Meaning |
| --- | --- | --- |
| Stage flags | — | `--prepare`, `--train-small`, `--evaluate`, `--train-qwen`, `--probe-remote`, `--eval-qwen`, `--qwen-status`. A set, not a sequence: asking for `--evaluate --prepare` runs both, in pipeline order. |
| `--seed` | `20260101` | Master seed for both corpora, both splits and both models. |
| `--per-intent` | `150` | Routing rows per intent (2,100 total). A chosen size, not a hard ceiling — see §2.3; raising it past what the families can fill raises rather than pads. |
| `--per-category` | `40` | Qwen rows per category. |
| `--config-dir` | `ml/configs` | Where the two TOMLs live. |
| `--datasets-dir` / `--artifacts-dir` / `--reports-dir` | `ml/datasets`, `ml/artifacts`, `ml/reports` | Output locations. |
| `--resume` | off | Passed through to `--train-small`; implied when `--resume` is given alone. |
| `--dry-run` | off | Print the stage plan and exit. |
| `--verbose` | off | Print the commands each stage runs. |

With no stage flag, `--all` runs **every** stage in `STAGES` order — `prepare`,
`train-small`, `evaluate`, `train-qwen`, `probe-remote`, `eval-qwen`, `qwen-status`.
`train-small` precedes `evaluate` because the report is about a model that exists. Note
that a bare `python -m ml.train` therefore reaches the Kaggle stages too, and that they
are ordered `train-qwen` **before** `probe-remote`: a run that starts with no probe on
disk blocks its `--train-qwen` and then writes one, so the second pass is the one that
would push. Run the stages individually rather than relying on the no-flag default on a
machine with no accelerator.

---

## 10. Troubleshooting

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

**`response names GET /some/path, which the capability inventory (…) does not declare`.**
`_Grounding.check()` refused a Qwen row: the template references a route the product does
not serve. Either the route was renamed upstream or the template is stale.

**`response claims NEXUS performed an action: 'I have created …'.** Same gate, second
check. The template needs rewording into a proposal.

### The local training half (`ml-train-small`)

**`ModuleNotFoundError: No module named 'torch'`.** You ran with the backend interpreter.
Use `make ml-train-small ML_PY=backend/ml/.venv/Scripts/python.exe`, or call
`backend/ml/.venv/Scripts/python.exe -m ml.scripts.train_small_local` directly. The
`ml/train.py` path shells out to the right interpreter for you; a direct
`python -m ml.train --train-small` on the backend venv will not.

**`--device cuda was requested but torch sees no CUDA device`.** Explicit, not a silent
downgrade. Use `--device cpu` (the default `auto` already resolves correctly).

**A very slow run.** It is CPU training, by design — the Makefile target says "locally on
CPU". The observed 3-epoch run took ~1034 s for 168 steps at batch 16. `--threads` caps
the pool if you want to leave the machine usable.

**Resume refused.** The checkpoint's `dataset_checksum` or `dataset_version` does not match
the current data. That is the intended behaviour: resuming onto a different dataset is
worse than not resuming, because the result looks like a continuation and is not one. Run
`make ml-prepare` then `make ml-train-small` fresh, or restore the previous split files.

**`ignoring checkpoint directory …: no checkpoint.json` / `payload 'x' is empty`.** An
interrupted save. `latest_checkpoint` skipped it and warned. This is the format working as
designed — a directory with no metadata is debris, not a checkpoint. Delete it if you like;
nothing on the resume path will touch it.

**Poor macro-F1 on adjacent classes.** The expected failure mode of this corpus. The
confusion matrix in `ml/artifacts/small-model/metrics.md` is where to look: the classes
that bleed into each other are the ones whose template families share vocabulary. Fixing
it means changing templates, not changing the learning rate.

### The remote half (`ml-probe-remote`, `ml-train-qwen`, `ml-eval-qwen`, `ml-qwen-status`)

**`kaggle CLI unusable: …`.** Not on `PATH`. Install it, or pass a path. An unauthenticated
machine is a legitimate state — the stage reports `BLOCKED`, not `FAILED`.

**`the kaggle CLI cannot identify the account … Authenticate with 'kaggle auth login'`.**
The private kernel listing returned nothing, so no ref can be composed.

**`not yet probed (<path> absent or unreadable)`.** `verify_gpu_available` has no evidence
yet. Run `make ml-probe-remote` first. This is the single most useful command in the
phase: it converts "Kaggle should give us a GPU" from an assumption into a file on disk.

**`stale probe: Nh old, limit is 24.0h`.** Override with
`NEXO_GPU_PROBE_MAX_AGE_SECONDS`, or pass `force=False` where a stale answer beats none.

**`no /dev/nvidia* device and no working nvidia-smi`.** The probe ran and the answer is
no — the most useful outcome it can produce. Note that kernel metadata will still say
`machine_shape: NvidiaTeslaT4`; that records the *request*. In the recorded run the
probe also reported `kaggle_run_type: "Batch"` and DNS failures for both
`huggingface.co` and `pypi.org`, which is the same finding stated twice: the GPU
request was met with a CPU-only batch image on a session with no working DNS. Even a
verified accelerator would not have been sufficient to train.

**`probe kernel ended <STATE>` / stage `FAILED` with no `gpu_probe_run.json`.** The
kernel was pushed but did not reach `COMPLETE`. A failed probe writes no record, by
design: there is nothing to testify. Read the kernel's own log
(`ml/artifacts/remote/nexo-phase10-gpu-probe.log`) and re-run.

**`title 'X' slugifies to 'y' but ref names 'z' …`.** `render_kernel_metadata` refusing to
push to a kernel you did not name. Make the title and the slug agree.

**`A valid kernel type must be specified in the metadata`.** `kernel_type` is missing.
`render_kernel_metadata` always sets it; this indicates a hand-edited metadata file.

**`kernel did not reach a terminal state within Ns; last observed …`.** The wait ceiling.
A segment of an 8B model at 2048 tokens legitimately runs for hours; a run that hits the
ceiling is reported as **still running**, never as finished. Check
`make ml-qwen-status` and the kernel URL.

**Stage reports `BLOCKED`.** Working as designed. Read the `blocker` sentence in
`ml/artifacts/qwen/qwen_run.json` — it states the required VRAM in GiB, what the local
torch saw, and what the remote probe observed. The notebook and its input bundle are
already written; the run is one `kaggle kernels push` away on hardware that can hold it.

**`…holds no JSONL split, so there is nothing to train on. Run --prepare before
--train-qwen`.** The publish step found an empty input bundle, so it refused to publish
rather than push a kernel that would train on nothing. Run `make ml-prepare` and repeat.

**`no LoRA adapter under …, so there is nothing to compare the base model against …
a base-versus-base comparison would be reported as a fine-tuning result and is not
one`.** `--eval-qwen`, first gate. Not a bug and not a fallback you should route
around: without a fine-tune there is no fine-tuning result. Push one segment on a
verified accelerator, download the adapter, and rerun.

**`adapter <name> is present but no accelerator is available to run the paired
evaluation: …`.** `--eval-qwen`, second gate. The adapter exists but no probe verifies a
GPU. Run `make ml-probe-remote` first; if it comes back negative, evaluate on whichever
session actually produced the adapter.

**Continuing a Qwen run.** Push the next segment with `resume_from` pointing at a
`step-<n>` directory (absolute, or mounted under `/kaggle/input`). `segment_index` must
increase. The callback measures steps as `resumed_step + global_step`, so segment 1's
step 100 is step 350 of the whole run.

---

## 11. Findings

Recorded here because this document's brief forbids modifying any Python source under
`backend/ml/`. Items 1 and 6 are **no longer findings** — they were fixed in the source
after this document was written, and are kept with their current state rather than
deleted, because "this was broken and is now fixed" is the more useful record.

1. **~~Cross-intent de-duplication defeated by a shadowed parameter.~~ FIXED.**
   This was the finding that mattered most: `_generate()` used to rebind its `seen`
   parameter to a fresh set, so `build_routing_dataset()`'s shared cross-intent set was
   never mutated and de-duplication was per-intent only — which would have undercut the
   module's central claim that no utterance carries two labels. **The rebinding is
   gone.** `ml/datasets/routing.py:1867` now carries a comment stating the invariant
   explicitly (*"`seen` is the caller's cross-intent set and is deliberately NOT re-bound
   here"*), `_generate` adds to the set it was handed, and `build_routing_dataset`
   passes one set across all fourteen intents. The generator docstring's claim now
   holds as written. Re-verified end-to-end at the shipped seed: the routing validation
   report records `records 2100 / usable 2100`, `contradictions 0` and no findings, and
   `ml/reports/routing_splits.md` records `unique_keys 2100`; a 2,800-row build at
   `per_intent=200` also validates clean (§2.3).

2. **`num_train_epochs` in the committed TOML is 4; the on-disk small-model artifacts were
   produced with 3.** `ml/artifacts/small-model/training_state.json` records
   `epochs: 3`, `steps: 168`, `steps_per_epoch: 56`, `first_loss: 2.6409`,
   `final_loss: 1.7017`, `duration_seconds: 1034.1`, and a nested `config` block whose
   `num_train_epochs` is `3`. `ml/configs/small_model.toml` now says `4` — the TOML's own
   comment explains the move ("Three was tried first and stopped while the training loss
   was still falling"). The artifacts on disk therefore predate the current configuration.

3. **Those artifacts were trained on a different corpus than the current splits.**
   `training_state.json` records `train_rows: 882` and `validation_rows: 196`, while
   `ml/datasets/dataset_manifest.json` records 1470 / 322 for the routing splits. The
   metrics in `ml/artifacts/small-model/metrics.md` (accuracy 0.6209, macro F1 0.5648,
   with `schedule_plan` at F1 0.000 and `learning_track` at F1 0.133) describe the earlier,
   smaller prepare. **Do not quote them as the current model's performance.** They are
   reported here only because they exist on disk.

4. **`SmallModelConfig.num_train_epochs` defaults to 3** in
   `ml/training/config.py:114`, while the TOML says 4. The default only applies if the key
   is omitted, which `load_config` treats as an error (`extra="forbid"` does not apply to
   missing keys, and `num_labels` has no default) — so this is cosmetic drift between the
   pydantic default and the TOML, not a live discrepancy.

5. **Small-model training is not resumable mid-epoch in the local trainer.** The checkpoint
   cadence (`save_every_n_steps: 100`) exceeds the observed total step count (168 for 3
   epochs at 1470 rows; 368 for the configured 4 epochs), so at most one intermediate
   checkpoint is written per run, and `on_train_end`-equivalent final-state handling means
   an interruption between step 100 and the end loses more than half the run. The Qwen
   segment cadence is designed around this and is not affected. Worth revisiting if the
   small model's schedule grows.

6. **~~Two of the four notebook renderers are unreachable from the pipeline.~~ MOSTLY
   FIXED.** `ml/train.py` now imports **three** of the four renderers —
   `render_gpu_probe_notebook`, `render_qwen_training_notebook` and
   `render_eval_notebook` (`ml/train.py:91`) — and `stage_eval_qwen` renders and pushes
   the evaluation as its own kernel (§8.5). The Qwen comparison, which used to have no
   stage at all, has one. **What is still open:** `render_small_training_notebook`
   remains unreferenced, which is correct rather than unfinished (the small model is
   trained locally on CPU, so a Kaggle notebook for it would be dead code); and the
   evaluation stage has never actually run, because no adapter exists (§8.6). The code
   path is tested (`tests/test_ml_stages.py::test_eval_qwen_pushes_the_paired_comparison_when_it_can`
   exercises it against a stubbed client and a synthetic adapter) but has never been
   executed against Kaggle.

7. **`--eval-qwen` has no Makefile target.** `Makefile:68` lists the `.PHONY` targets
   and stops at `ml-qwen-status`; there is no `ml-eval-qwen` recipe. The stage is
   reachable from the command line and from `selected_stages`, and it writes a manifest
   (`model_name: "qwen-qlora-eval"`), so the gap is only the convenience target and the
   `.PHONY` entry. Not recorded as a design choice: all six other stages have targets.

8. **Two stale descriptions of the routing corpus size.** Both are documentation-level
   rather than behavioural, and both are verified against the current tree:
   - `build_routing_dataset(per_intent=90)` and `build_routing_records(per_intent=90)`
     still default to **90** (`ml/datasets/routing.py:1976`, `:2028`) while the CLI
     default is **150**. Anything calling the builders directly — a notebook, a
     one-off script — silently gets 1,260 rows instead of 2,100.
   - The `--per-intent` help string still calls 150 *"the largest value the narrowest
     template family can fill"*. It is not: 200 per intent builds 2,800 rows that
     validate with zero findings (§2.3). The builder's fail-rather-than-pad behaviour is
     real and unchanged; only the stated ceiling is wrong.

9. **`stage_probe_remote` writes `gpu_probe_run.json` under `artifacts/qwen/`, not under
   `artifacts/remote/`.** The stage downloads the kernel output into
   `ml/artifacts/remote/` — that is where `nexo_gpu_probe.json`, the file the verifier
   reads, lands — and then records the requested-vs-observed verdict at
   `ml/artifacts/qwen/gpu_probe_run.json` (`ml/train.py:775`). Nothing reads the record
   back: `--train-qwen` and `--eval-qwen` re-derive their verdict from the probe file,
   so the record is a human-facing audit trail rather than state. The split is
   defensible, but the directory name is not obvious, and a reader looking for the
   verdict next to the kernel that produced it will not find it.

10. **`--train-qwen` publishes the bundle directory before it is populated.**
    `stage_train_qwen` calls `_publish_qwen_dataset(...)` on
    `ml/artifacts/qwen/segment-000/input` (`ml/train.py:1693`) and only afterwards calls
    `_push_qwen_kernel`, which is what invokes `_materialise_qwen_bundle` and copies
    `qwen_train.jsonl` / `qwen_validation.jsonl` / `label_map.json` into that directory.
    On a fresh tree — `--prepare` run, no prior `--train-qwen` — the publish step
    therefore sees an empty directory and ends `BLOCKED` with *"holds no JSONL split, so
    there is nothing to train on. Run --prepare before --train-qwen"*, which is the wrong
    instruction: `--prepare` has already been run. It only works today because the blocked
    runs on this machine populated `segment-000/input` first. Moving the publish call
    inside `_push_qwen_kernel`, after the bundle is materialised, is the fix.

11. **`stage_eval_qwen` pushes the evaluation kernel with no dataset attached.** The
    eval notebook resolves the held-out split from `dataset_slug` under
    `/kaggle/input/<slug>` and the adapter by searching the mounted inputs for
    `adapter_config.json` (`_CELL_DATASET`, `_CELL_RESOLVE_INPUTS` in
    `ml/kaggle/notebook.py`) — but `render_kernel_metadata(..., dataset_sources=())` is
    called with no sources (`ml/train.py:1826`), and the slug passed to the renderer is
    the unprefixed placeholder `nexo-phase10/qwen_dataset.v1` rather than a published
    `<username>/<slug>`. A real push would reach the notebook's own
    "dataset not attached" failure, which the renderer is explicit about how to diagnose.
    The stage is covered by
    `tests/test_ml_stages.py::test_eval_qwen_pushes_the_paired_comparison_when_it_can`,
    but that test stubs the client and never renders a mount, so the gap is invisible to
    it. Fixing it means publishing the adapter as a dataset and mounting it, in the same
    spirit as `_publish_qwen_dataset`.

---

## 12. Standing constraints this half inherits

Restated because they are enforced in the code above rather than in this document:

1. **No fake data.** Every number traces to a file. A figure that could not be computed is
   null, never `0` — the same rule the Qwen system preamble trains on, the same rule
   `FeatureRow.from_dict` enforces, and the same rule `_excerpt`/`redact` apply to reports.
2. **Deterministic before learned.** Nothing in `ml/` is on a request path. Phase 10
   produces artifacts; serving them is Phase 11's job, and the deterministic engines in
   `app/services` remain the fallback that learned code is measured against.
3. **Explainability.** Every hyperparameter carries its reason beside it, in the TOML or in
   the validator that enforces it. A number without a derivation is the defect this half of
   the project exists to avoid.
4. **Never claim what was not executed.** `BLOCKED` is a first-class status. `passed` is
   derived from findings, not supplied. `assert_clean` raises. A manifest hashes every file
   it claims. A probe file must exist before a GPU is reported. An adapter must exist
   before a fine-tuned model is compared against anything.
5. **Credentials are never read, printed, logged or committed.** This document, like the
   code, never opens `~/.kaggle/access_token`; every string that could reach a log or a
   report passes through `redact()` first.

---

## Appendix — file map

| Path | Role |
| --- | --- |
| `backend/ml/train.py` | the orchestrator; all seven stages, the CLI, the constants |
| `backend/ml/configs/small_model.toml` | the classifier's hyperparameters, with derivations |
| `backend/ml/configs/qwen_qlora.toml` | the QLoRA hyperparameters, with derivations |
| `backend/ml/training/config.py` | pydantic models + validators; `load_config` |
| `backend/ml/datasets/routing.py` | the routing corpus generator |
| `backend/ml/datasets/qwen_sft.py` | the QLoRA SFT corpus generator, the system preamble, the grounding gate |
| `backend/ml/datasets/features.py` | deterministic per-subject feature vectors and their null contract |
| `backend/ml/datasets/taxonomy.py` | the fourteen intents and their specs |
| `backend/ml/datasets/capabilities.py` | the `ast`-based harvest of the real product surface |
| `backend/ml/datasets/schema.py` | record schemas, versioning, JSONL I/O, checksums |
| `backend/ml/validation.py` | the data-integrity gate |
| `backend/ml/preprocessing/splits.py` | deterministic, leakage-aware splitting |
| `backend/ml/preprocessing/normalize.py` | normalisation, `near_duplicate_key`, `redact`, `find_credential` |
| `backend/ml/training/checkpoint.py` | the torch-free local checkpoint format and readers |
| `backend/ml/training/remote.py` | the credential-blind `kaggle` CLI driver and the GPU-probe contract |
| `backend/ml/training/manifest.py` | run manifests, environment capture, run ids |
| `backend/ml/kaggle/notebook.py` | renders the four notebooks as source text |
| `backend/ml/scripts/train_small_local.py` | the local CPU training loop |
| `backend/ml/evaluation/metrics.py` | accuracy, macro/weighted F1, confusion matrix, top-k |
| `backend/ml/evaluation/qwen_eval.py` | the deterministic generation rubric and the base-vs-fine-tune comparison |
| `backend/tests/test_ml_stages.py` | the seven stages' honesty contract: stubbed client, recorded verdicts |
| `Makefile` (lines 140–202) | the `ml-*` targets |
