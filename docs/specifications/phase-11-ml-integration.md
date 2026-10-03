# Phase 11 — ML integration: serving the trained router

**Status:** documents the code as it exists in `backend/app/ml/`, `backend/app/api/v1/ml.py`
and `backend/app/schemas/ml.py`. NEXUS serves **one** model — a `deberta-v3-base` intent
classifier over the fourteen Nexo intents, trained by Phase 10 — behind **two** endpoints.
**Scope:** the boundary between "a checkpoint exists" and "a request is routed by it". The
training pipeline itself is Phase 10's and is described in
[`phase-10-training.md`](phase-10-training.md) and [`phase-10-report.md`](phase-10-report.md);
nothing here restates it except where serving has to agree with it.

> **What is not here.** There is no generative model, no second model, no cloud inference
> and no fallback path of any kind. Two of the fourteen classes — `code_assist` and
> `deep_reasoning` — are trained and predicted and then explicitly refused; §7 explains why
> refusing them is the correct behaviour rather than a missing feature.

---

## 1. What this phase is for

Phase 10 ended with an artifact: `backend/ml/artifacts/small-model/final/`, 737,756,192
bytes of weights, scoring 0.9738 accuracy / 0.9737 macro F1 on a 420-row held-out split. It
ended there deliberately, and its own report said so as limitation 5 — *"No model is loaded
into the running application."* Until something loads that directory, the number is a fact
about a file rather than a fact about the product: every request NEXUS receives is still
answered by a deterministic engine under `app/services/`, and the classifier is
decoration.

This phase is the other half of that sentence. It puts the checkpoint inside the running
FastAPI process, in front of a caller, behind an authenticated endpoint, and turns the
model's output into a decision about a service that already exists. It changes no
training code, retrains nothing, and adds no model.

The shape of the problem is narrower than "add machine learning to the product", and the
narrowness is the design. What is being built is:

- a **loader** that turns a directory on a filesystem into a live model, and refuses to
  produce one that would misroute traffic;
- a **classifier** that maps raw text to a distribution over fourteen classes and nothing
  else;
- a **router** that turns that distribution into a named existing service or an explicit
  refusal;
- a **runtime** that owns the loaded model for the process and degrades in a way both a
  caller and an operator can read.

What is deliberately *not* being built is slot filling, request execution, conversational
state, or any second model. §12 says why each of those is out of scope and what would be
needed instead.

---

## 2. Architecture

### 2.1 The pipeline

```text
user text
  → app/api/v1/ml.py          POST /api/v1/ml/route   (auth + Permission.ANALYTICS_READ)
  → app/ml/runtime.py         MLRuntime — one model per process, loaded in the FastAPI lifespan, off the event loop
  → app/ml/classifier.py      IntentClassifier.predict() — validation, raw text, tokenizer, model, softmax
  → app/ml/model_loader.py    checkpoint resolution + label cross-validation + device selection
  → IntentPrediction          intent + confidence + alternatives + truncated + latency_ms
  → app/ml/router.py          IntentRouter.route() — threshold verdict, destination, existing service
  → RoutingDecision           status + destination + ServiceTarget + reason
  → response
```

The ordering of that list is an ordering of *cost and specificity*. Validation happens
before anything expensive; the model runs only on text that survives it; the router runs
only on a prediction; and the service is named only if the router's policy says so. Each
stage has exactly one reason to exist and no authority over the next.

### 2.2 Which module owns what

| Module | Owns | Explicitly does not own |
| --- | --- | --- |
| `app/ml/model_loader.py` | Resolving the checkpoint directory, validating it, cross-checking its label order against the taxonomy, choosing a device, loading weights, building `ModelIdentity`. | Thresholds, destinations, services, HTTP. It answers one question — "is there a usable classifier in this process?" — and returns something that can answer "which intent is this?". |
| `app/ml/classifier.py` | Validation of the utterance, tokenisation, the forward pass, softmax, and turning a tensor row into a plain `IntentPrediction` dataclass. | Whether the confidence is *enough*. That is the router's question. A prediction that carries no policy is what makes the classifier testable against a checkpoint and nothing else. |
| `app/ml/router.py` | The threshold verdict, the destination, the `SERVICE_TARGETS` table, and every negative answer — `uncertain`, `out_of_scope`, `generation_unavailable`. | Tensor work, HTTP, and the act of calling anything. It decides; the caller executes. |
| `app/ml/runtime.py` | The process-wide lifecycle: load once, hold, release, and record a machine-readable reason when it cannot. | Anything request-shaped. No per-user state, no cache to invalidate, no second owner. |
| `app/ml/schemas.py` | The three value objects that cross the boundary, deliberately free of `torch`. | — |
| `app/ml/exceptions.py` | The 503/500/422 split, all deriving from `NexusError` so they render through the standard envelope. | — |

The four-way separation is not a preference for tidiness. Each cut prevents a specific
defect:

- Loader and classifier are apart so the classifier can be exercised against a
  hand-built `LoadedModel` with no weights in it, and so the loader can be tested without a
  request, a user or a database.
- Classifier and router are apart because "0.62 is uncertain" is a **deployment policy**,
  and a policy baked into the class that runs the model would be invisible at every call
  site and untestable independently of a 703 MiB load.
- Router and runtime are apart because the router is stateless and cheap to construct, so
  it can read the threshold from the settings this request resolved rather than from a
  module-level singleton that a reconfigured deployment would never pick up.
- Runtime is the only module that knows a checkpoint lives on a filesystem. That
  concentration is the point: a runtime with no owner ends up loading 703 MiB once per
  route that touches it, or once per worker reload, and the failure surfaces as a 500 in
  an unrelated endpoint.

### 2.3 The lazy-torch rule

**`torch` and `transformers` are imported inside functions.** There is no module-scope
import of either anywhere in `app/ml/`. This is load-bearing and it is pinned by a test.

A module-scope `import torch` would turn "this deployment has no classifier" into "this
application will not start" — a strictly worse failure with a strictly worse error message.
The check is cheap and was run for this document: importing `app.ml` leaves `torch`,
`transformers`, `sqlalchemy` and every `app.services.*` module out of `sys.modules`
entirely. `app.ml` pulls in only its own seven modules plus the four stdlib-only Phase 10
modules it reads a contract from (`ml.datasets.schema`, `ml.datasets.taxonomy`,
`ml.validation`, `ml.preprocessing.normalize`).

The same holds one level up: `import app.main` and `import app.api.deps` are both
torch-free. `app/api/deps.py` reaches `app.ml.runtime`, and `runtime.py` imports
`app.ml.classifier` *inside* `_build_classifier()` — the single place in the whole package
where the dependency is allowed to become real.

### 2.4 Why `router.py` resolves `app.services` lazily

Every module under `app.services` imports the ORM session machinery. Importing them at
module scope in the router would make the classifier half of `app.ml` unimportable without
a database, turning a routing table into something that needs the full stack to test. The
import paths in `SERVICE_TARGETS` are therefore **strings**, and `resolve_service()` does
the import on demand.

This buys a second thing that is arguably more valuable. A routing table that is only ever
compared as strings has nothing to be wrong about until it is used, which means a renamed
or moved service fails at resolve time with a named `ModelRuntimeError` rather than
quietly pointing at nothing. `RoutingDecisionRead` reports the service name and the
entrypoint; the resolution itself happens on the caller's side, at the moment the caller
decides to act.

### 2.5 Why the API route never loads a model

`app/api/v1/ml.py` resolves the caller, validates one bounded string, asks the runtime for
a prediction, and hands that prediction to the router. It does not import
`model_loader`, does not touch the filesystem, and cannot construct a `LoadedModel`.

The route's only judgement is which HTTP answer to give, and it makes exactly one: a
runtime that cannot classify gets a **503 `ml_unavailable`**, never a fabricated
prediction. Answering 200 with an invented intent would be worse than refusing, because
the caller's next move is to call a service on the strength of it — NEXUS would be
inventing a user's instruction and then acting on it.

Inference itself runs through `run_in_threadpool`. A 184M-parameter forward pass on CPU is
solid compute, and the only thing worse than serving it slowly is stalling the event loop
while it happens: every other route in the process would queue behind one classification.

---

## 3. The model

| | |
| --- | --- |
| Base model | `microsoft/deberta-v3-base` |
| Architecture | `DebertaV2ForSequenceClassification` |
| Parameters | **184,432,910** |
| Checkpoint | `backend/ml/artifacts/small-model/final/` |
| Members | `model.safetensors` (737,756,192 bytes), `config.json`, `label_map.json`, `tokenizer.json`, `tokenizer_config.json` |
| Tokenizer | `DebertaV2Tokenizer`, fast variant, `do_lower_case: false`, `add_prefix_space: true`, vocab 128,100 |
| Context length | **128** subword tokens |
| Test accuracy / macro F1 | 0.9738 / 0.9737 (420-row held-out split) |
| Validation accuracy / macro F1 | 0.9810 / 0.9809 |
| Trained on | CPU, torch 2.14.1+cpu, transformers 5.18.0, 615 steps, 5 epochs |

The checkpoint was **not** retrained, retuned or re-scored by this phase. Every figure in
that table is Phase 10's, and Phase 10 re-read each one from the artifacts rather than
carrying it forward.

### 3.1 Where it lives, and what its absence means

`backend/ml/artifacts/` is **gitignored** (`.gitignore` line 86, alongside the JSONL
splits and the run manifests). The weights are 703 MiB of binary; committing them would put
a model in version control that no review can read and that no diff can describe.

The operational consequence is worth stating without hedging: **on a fresh clone there is
no checkpoint, and therefore no ML.** This is a supported state, not a broken install. The
runtime records `checkpoint_missing`, the app boots, every deterministic router works, and
the two ML endpoints answer 503 with that reason. A deployment that wants the classifier
runs `make ml-all` (Phase 10's pipeline) to produce the artifact locally, and the default
path resolves relative to the repository so a moved checkout keeps working.

`ml_checkpoint_exists` deliberately does **not** raise when the directory is absent. The
settings object must be constructible on a machine that has never trained anything, because
everything else in the application depends on it.

### 3.2 The label contract

`ml/datasets/taxonomy.py` — `Intent`, `INTENT_SPECS`, `DestinationKind`,
`TAXONOMY_VERSION = "nexo_intents.v1"` — is the source of truth for the label set, and it is
read **at serving time**. That is a deliberate change in the standing arrangement: the `ml`
package under `backend/` remains the stdlib-only training pipeline, but the label set it
defines is no longer a training detail. It is a contract between two halves of the system,
so both halves now read the same definition.

On **every load**, the checkpoint's `id2label` is compared index-by-index against
`ml.datasets.routing.label_map()`. A single reorder or rename of one class raises
`ModelCheckpointError` and refuses the load.

The failure this prevents is specific and silent. A reordered label map does not raise; it
routes `schedule_plan` utterances to the knowledge router with a confident-looking 0.99,
and nothing anywhere reports an error. `label2id` and `config.num_labels` are *not* checked
separately — they are the inverse and the length of the map that was checked, so checking
them again would only add ways for a correct checkpoint to be rejected.

The classifier then resolves every prediction's argmax through the **validated tuple**, not
through the model's own `config.id2label`, so the labels it emits are by construction the
ones that were checked.

---

## 4. Configuration

Seven `ML_*` settings, all validated by `_validate_ml_settings` in
`backend/app/core/config.py`, which **raises** — and because `Settings` is constructed once
per process through `get_settings()`, a refusal happens at boot where an operator sees it.

| Variable | Default | Effect | What breaks if you get it wrong |
| --- | --- | --- | --- |
| `ML_ENABLED` | `true` | Master switch. Off means no model is ever loaded and the ML endpoints answer 503 with reason `disabled`. | `false` looks like a working router that refuses everything. It is not a failure — the status endpoint reports `enabled: false` and the deterministic routers are untouched. |
| `ML_FAIL_FAST` | `false` | Whether a failed load stops the process. | `false` on a host whose production config points at a checkpoint that is not there gives you a feature that quietly 503s rather than an outage you are told about. Turn it on for such a deployment. It never fires for `ML_ENABLED=false`, because refusing to boot over an operator's deliberate off switch makes the switch useless. |
| `ML_MODEL_PATH` | *(empty)* | Directory holding the checkpoint. Empty resolves `<backend>/ml/artifacts/small-model/final`, anchored to the repository rather than to the working directory. A configured *relative* path resolves against the process's working directory, which is where an operator writing one into a unit file means it to point. | A path to a directory that exists but is not a checkpoint yields `checkpoint is missing model.safetensors` rather than "wrong path", because the five required member files are listed explicitly. |
| `ML_DEVICE` | `auto` | `auto` resolves to CUDA when `torch.cuda.is_available()` and CPU otherwise; `cpu` and `cuda` are honoured literally. | An unrecognised value is refused at boot, not coerced to CPU — a silent fallback would turn a configuration typo into a latency regression nobody attributed to the typo. An explicit `cuda` on a machine with no CUDA **raises** rather than downgrading, because an operator who asked for a GPU and got a CPU has a capacity problem nobody will notice until the p99 does. |
| `ML_CONFIDENCE_THRESHOLD` | `0.90` | The confidence below which a prediction becomes `uncertain` instead of a named service. Must lie in `(0, 1]`. | Zero accepts every utterance whatever the model said; above one refuses every request as unconfident. Neither raises at request time — both look like working code. |
| `ML_MAX_INPUT_CHARS` | `2000` | Hard ceiling on submitted text, applied before inference and baked into the request schema. Must be positive and at most 10,000. | Zero or less refuses every classification with an error no caller can satisfy. Above 10,000 is refused because the trained context is 128 subword tokens: past that point extra characters cannot change the prediction, they only cost tokenisation time and make the request body a place to park something the router will never read. |
| `ML_REJECT_CREDENTIALS` | `true` | Screens incoming text with `ml.validation.find_credential` before it reaches the tokenizer. | `false` means a user who pastes a live key into a chat box has that key classified and possibly logged. See §8. |

### 4.1 `ML_MODEL_PATH` is never a request parameter

The path is deployment configuration. `ModelIdentity.checkpoint` **reports** it on the
authenticated diagnostics endpoint and it is written to the startup log; no request may
choose it, and `RouteRequest` sets `extra="forbid"` so `{"text": ..., "model_path": ...}`
is a 422 rather than a cheerful 200 with the client's override silently ignored.

The reason is the obvious one once stated: a caller able to choose the checkpoint would be
choosing which weights answer them. That is not a routing decision, it is a capability
escalation.

### 4.2 Two length bounds, and why they differ

`ML_MAX_INPUT_CHARS` defaults to **2000** and is enforced by `RouteRequest` at the edge.
`IntentClassifier` enforces a separate domain bound of **4000** characters, because
validation lives in the domain so that every caller — the API, the batch path, a script —
is held to the same rule, and because a script calling `predict()` directly has no schema
to stop it. The stricter of the two is the one an HTTP caller meets.

---

## 5. Inference

### 5.1 The exact pipeline

`IntentClassifier.predict(text)` does this, in this order:

1. **Validate.** Type, non-blank, length, and — when `ML_REJECT_CREDENTIALS` is on —
   credential screening. All of it before any tensor exists.
2. **Measure the true token count.** `tokenizer(text, truncation=False)` first, so the
   `truncated` flag is measured *before* truncation rather than guessed at. A second encode
   costs microseconds against a 184M-parameter forward pass.
3. **Encode at the trained width.** `truncation=True`, `max_length=128`, `padding=False`,
   `return_tensors="pt"`.
4. **Drop `token_type_ids`.** DeBERTa-v3 declares `type_vocab_size: 0`; the tokenizer emits
   the key anyway and the model ignores it. The training code popped it, so the serving
   path pops it.
5. **Move to the device, forward under `torch.inference_mode()`.** `eval()` mode was set at
   load time and `requires_grad_(False)` was applied to every parameter.
6. **Softmax**, then `topk` for the winner plus three runner-ups.
7. **Check the shape.** A row whose width is not the validated label count raises
   `InferenceError` — the one thing the load-time label check exists to make impossible.

### 5.2 The raw-text parity rule

**Raw text goes straight to the tokenizer. No normalisation, no lowercasing, no
punctuation stripping, no whitespace collapsing — anywhere in the serving path.**

Phase 10 trained on the raw `text` values of the routing dataset.
`ml/preprocessing/normalize.py` was written for duplicate detection, near-duplicate leakage
auditing and, newly, credential screening. It is never used to rewrite an utterance.

This is not tidiness. Lowercasing text the model was fitted on capitalised, punctuated,
occasionally mistyped requests is a **distribution shift the model has never seen**, and it
would invalidate the 0.9738 figure without changing a single line of the model, the
checkpoint, or the metrics file. That number describes what this model does to text
produced the way Phase 10's corpus produces text. Change the input distribution and the
number describes something else, silently.

The same rule runs at the edge: `POST /api/v1/ml/route` passes `payload.text` through byte
for byte. There is no `strip()`, no `casefold()`, and the schema's `min_length=1` is the
only length rule before the domain's own.

### 5.3 Truncation at 128

The trained context is 128 subword tokens, recorded in both
`ml/configs/small_model.toml` and `training_state.json`, and the loader prefers the value
recorded alongside the weights. Tokenising at any other width is a distribution shift: a
longer window hands the encoder positions it was not trained to use, and the relative
positional buckets DeBERTa-v3 relies on were calibrated at this width.

Truncated text is not silently truncated *from the model's point of view* — the model
cannot tell. It is reported instead: `IntentPrediction.truncated` is true whenever the
untruncated token count exceeded 128, and the field travels through to the API response
and into the structured log line. A prediction made from a prefix of the request is a
weaker claim, and the caller is told rather than left to assume the whole string was read.

### 5.4 Eval mode, inference mode, and one lock

The model is put into `eval()` and has `requires_grad_(False)` applied at load time, once,
and every forward pass runs inside `torch.inference_mode()`.

**Thread safety** is one shared model plus a `threading.Lock` around the forward pass. The
model is 703 MiB of shared mutable state and requests arrive from FastAPI's thread pool;
holding the lock across tokenise+forward bounds the resident set to one in-flight forward
pass instead of letting N threads each allocate their own activation buffers, and it makes
`latency_ms` mean something. The lock is deliberately narrow: nothing is logged, no caller
state is touched and no `IntentPrediction` is built while it is held, because a lock held
across logging is a lock that serialises the whole request queue behind a JSON formatter.
Time spent waiting for it is excluded from `latency_ms` — queueing delay is a capacity
fact, not this classifier's inference cost.

**No per-request state is ever stored on the instance.** Everything `predict` computes lives
in locals for the duration of the call. A test asserts the exact attribute set before and
after concurrent calls, so "we did not add a cache" is a checked property rather than a
convention.

`predict_many` is a loop over `predict` on purpose. A separately batched forward pass
would be faster per item and would be a *second* tokenisation and scoring path, which would
drift the first time one was fixed and the other was not. Correctness of the served answer
is worth more here than the throughput of a path only tests use. The batch is capped at 32
items so one caller cannot occupy the serialised model for seconds.

---

## 6. Confidence

### 6.1 The threshold table

`ML_CONFIDENCE_THRESHOLD` defaults to **0.90**. It was chosen by re-running the checkpoint
over the 420-row held-out split and measuring precision against coverage at each level.

| threshold | requests kept | precision on kept | coverage | errors remaining |
| --- | ---: | ---: | ---: | ---: |
| 0.00 | 420 | 0.9738 | 100% | 11 |
| 0.50 | 419 | 0.9737 | 99.8% | 11 |
| 0.60 | 416 | 0.9784 | 99.0% | 9 |
| 0.70 | 411 | 0.9830 | 97.9% | 7 |
| 0.80 | 407 | 0.9853 | 96.9% | 6 |
| **0.90** | **400** | **0.9900** | **95.2%** | **4** |
| 0.95 | 396 | 0.9924 | 94.3% | 3 |
| 0.98 | 377 | 0.9947 | 89.8% | 2 |
| 0.99 | 247 | 1.0000 | 58.8% | 0 |

0.90 keeps 95.2% of utterances while lifting precision on accepted requests from 0.9738 to
0.9900, rejecting 7 of the 11 errors. The rows above it trade badly for a router whose
failure mode is refusing a legitimate request: 0.95 buys one further error rejected for one
more point of coverage; 0.98 buys one more for five points of coverage; and 0.99 refuses
**more than 40% of requests** to buy nothing at all.

That asymmetry is the whole argument. A wrong confident answer writes to the user's
calendar, task list or knowledge base. A refusal costs one clarifying turn. So the
asymmetry has to run in the direction of refusing, but not so hard that the router refuses
a legitimate request and stops being a router — which is why the trade is evaluated as
*errors removed per point of coverage* and not as accuracy alone.

### 6.2 What confidence is **not**

This is the part that must not be misread, so it is stated on its own.

**A softmax probability is not accuracy.** `IntentPrediction.confidence` is this model's
estimate for *this utterance*. It is not the 0.9738 test accuracy, and 0.97 accuracy does
not make every prediction 0.97 confident. They are different quantities measured on
different things: one is an aggregate over a held-out set, the other is a number attached
to a single input.

**The threshold is not derived from 0.9738.** There is no arithmetic connecting the two.
0.9738 is an accuracy on 420 template-generated rows; 0.90 is a cut point chosen by
tabulating precision against coverage on those same rows. Reading the threshold as "the
point at which the model is 90% accurate" inverts the meaning of both numbers.

**Neither is calibrated.** No temperature scaling, no reliability diagram, no
cross-entropy-vs-confidence curve. "0.9" here means the softmax gave the winning class 0.9
on a fourteen-way distribution, and nothing more. A model whose probabilities were
systematically overconfident would produce exactly these numbers and be wrong exactly this
often.

**Both are measured on synthetic, template-generated text.** The corpus is 2,800 rows
rendered from template families over this repository's own closed vocabularies. Real
utterances are messier. Expect both a lower mean confidence and a worse hit rate than the
table above — which makes 0.90 conservative in the safe direction (more refusals, fewer
wrong writes) and simultaneously an over-estimate of how often NEXUS will answer at all.
Re-derive it against real traffic before trusting it.

And the measurement that matters most for reading all of the above is in §11: on natural
language the model was wrong *confidently* seven times out of fourteen.

### 6.3 What `uncertain` does and does not protect against

`uncertain` is returned when the winning class is below the threshold, **or when the
confidence is NaN** — tested explicitly, because NaN compares false against every bound
and would sail through a bare less-than check and be treated as a confident prediction.

**It does protect against:** any below-threshold request reaching a service. For **all
fourteen** intents, `target` is null when the status is `uncertain`. Not eleven, not
thirteen — all fourteen. That is the property worth having, and it is the reason the
threshold is checked first in the router's branch order rather than after the destination
is known.

**It does not protect against:** a confident mistake. §11 records four Set B errors
predicted at or above 0.90. Those were accepted, named a service and served. Low-confidence
routing to `uncertain` is a real safety property and it is **not** a general accuracy
defence. A model that is confidently wrong is confidently wrong.

**It also costs something.** The `reason` names up to three runner-up intents so the caller
can offer "did you mean…?" rather than a flat refusal, which is the mitigation for the
coverage loss — but it is still one extra turn for the ~4.8% of held-out requests that
land in that band, and rather more for real traffic.

---

## 7. The fourteen intents

### 7.1 The routing table

The class-id order below is the checkpoint's `id2label`, and it is a contract rather than
an accident of alphabetical sorting: routers first, then the two classes NEXUS cannot
serve, then abstention. A head sitting next to the two classes it cannot serve tells you
something at a glance in a confusion matrix.

| id | intent | destination | destination_kind | service reached |
| ---: | --- | --- | --- | --- |
| 0 | `task_manage` | `api/v1/tasks` | router | `TaskService.list` |
| 1 | `project_manage` | `api/v1/projects` | router | `ProjectService.list` |
| 2 | `schedule_plan` | `api/v1/planner` | router | `PlannerService.week` |
| 3 | `knowledge_capture` | `api/v1/knowledge` | router | `KnowledgeService.create_note` |
| 4 | `knowledge_lookup` | `api/v1/knowledge` | router | `KnowledgeService.search` |
| 5 | `analytics_insight` | `api/v1/analytics` | router | `AnalyticsService.overview` |
| 6 | `risk_query` | `api/v1/risks` | router | `RiskDetectionService.evaluate` |
| 7 | `developer_intel` | `api/v1/developer` | router | `DeveloperIntelligenceService.summary` |
| 8 | `learning_track` | `api/v1/learning` | router | `LearningIntelligenceService.summary` |
| 9 | `career_track` | `api/v1/career` | router | `CareerIntelligenceService.summary` |
| 10 | `account_admin` | `api/v1/users` | router | `UserService.get_active_by_id` |
| 11 | `code_assist` | `large-model:unavailable` | large_model | none |
| 12 | `deep_reasoning` | `large-model:unavailable` | large_model | none |
| 13 | `out_of_scope` | `abstain` | fallback | none |

Destination and destination kind come from `ml.datasets.taxonomy.intent_spec()` at call
time. The eleven `SERVICE_TARGETS` entries are the only hand-written part, and they are the
bridge the classifier does not know how to make. **All eleven service targets and their
entrypoint method names were verified to resolve against the real classes in
`app/services/`, and a test asserts it** — so a table pointing at a renamed service fails
the suite rather than the production request.

An `entrypoint` names the concrete first call rather than a category, because most of these
intents can end at several routes and the first question is always "what exists" before it
is "change it". `TaskService` tells a caller which surface they reached; `TaskService.list`
tells them the call to make.

A `DestinationKind.ROUTER` intent with no `SERVICE_TARGETS` entry raises rather than
returning a decision with no service. Silently dropping the target would hand the caller
an `ACCEPTED` decision that cannot be acted on, which reads exactly like success.

### 7.2 The four routing outcomes

`RoutingStatus` is a closed set of four, and every one of them is a **200**. The router
worked and the answer is the answer.

| status | when | `target` | what the caller learns |
| --- | --- | --- | --- |
| `accepted` | At or above threshold, and a router intent | set | The named service and entrypoint. "The classifier chose the surface, the caller makes the call." |
| `generation_unavailable` | `code_assist` or `deep_reasoning`, at or above threshold | **null** | NEXUS recognised the request and declines it, because it runs no generative model. |
| `out_of_scope` | `out_of_scope`, at or above threshold | **null** | NEXUS has no surface for this and will not guess; the reason lists the surfaces it does offer. |
| `uncertain` | Below threshold, or NaN confidence | **null, always** | NEXUS is not confident enough, and names the runner-up intents. |

The negative answers carry `destination` and `destination_kind` **populated even when no
service is named**. "Recognised, and there is nothing to call" is a materially different
answer from a 404, and collapsing it would throw away the only part of the answer the
caller can act on.

The `out_of_scope` suggestion list is derived from the router intents' own taxonomy specs
rather than written out, so a page built from it cannot advertise a capability the router
would refuse to route to. The iteration is over `INTENT_SPECS` rather than the
`ROUTER_INTENTS` frozenset, because frozenset order follows string hashing and this string
reaches an API response where the same request must not produce two different sentences.

### 7.3 Why `code_assist` and `deep_reasoning` answer `large-model:unavailable`

These two classes are trained, predicted and then refused. That is the design, not an
incomplete feature.

NEXUS runs a deterministic platform with one learned router on top of it. There is no
generative model in this repository and there is no code path by which one could be
reached. So when the classifier says "this is a request for free-form generation", the only
honest answers are the bad ones: force-fit it onto a router that cannot serve it (so
"explain this stack trace" becomes a knowledge search over notes that do not contain a
stack trace), or improvise an answer (which is inventing one). The refusal is
`generation_unavailable`, with destination exactly `large-model:unavailable`, `target`
null, and a reason that says NEXUS runs no generative model and will not answer by
improvising one.

Removing the classes from the label set would have made the router blind to exactly the
requests it most needs to recognise as out of reach. They were kept for that reason in
Phase 10 and they are kept for it here.

---

## 8. Failure behaviour

The exception split is between **the deployment cannot classify** and **this request cannot
be classified**. Everything derives from `NexusError`, so a failure inside the classifier
renders through the standard envelope and never leaks a stack trace, a filesystem path or a
tensor shape to a client.

| Condition | Caller sees | Error code | Operator sees |
| --- | --- | --- | --- |
| Checkpoint missing / no `final/` directory | 503 | `ml_unavailable`, `details.reason = "checkpoint_missing"` | WARNING `ml_runtime_unavailable` naming the configured path. Logged at WARNING, not ERROR, because this is the documented state of a fresh clone. |
| `torch` or `transformers` not installed | 503 | `ml_unavailable`, `details.reason = "runtime_missing"` | WARNING naming the missing package and stating that every other route is unaffected. |
| Checkpoint incomplete — one of the five required files missing or unreadable | 503 | `ml_unavailable`, `details.reason = "checkpoint_missing"` | WARNING naming the **file**, never the absolute path: *"checkpoint is missing model.safetensors"*. |
| Checkpoint corrupted — invalid JSON, tokenizer will not decode, weights will not load | 503 | `ml_unavailable`, `details.reason = "checkpoint_missing"` | WARNING naming which member file failed; the underlying exception's traceback goes to the log, not to the caller. |
| **Label-order mismatch** | 503 | `ml_unavailable` | The refusal is the point: `checkpoint class 7 is labelled 'schedule_plan' but the intent taxonomy expects 'risk_query'` — raised in milliseconds, *before* the 703 MiB read. |
| Device unavailable (`ML_DEVICE=cuda`, no CUDA) | 503 | `ml_unavailable`, `details.reason = "runtime_missing"` | WARNING telling the operator to install a CUDA-enabled torch or request `cpu`. Never a silent downgrade. |
| Load failed for any other reason | 503 | `ml_unavailable`, `details.reason = "load_failed"` | ERROR with the traceback. Logged louder than a missing checkpoint because "the checkpoint is missing" and "our wiring is wrong" are different pages of the same runbook. |
| ML switched off | 503 | `ml_unavailable`, `details.reason = "disabled"` | INFO, not a warning. An operator's deliberate off switch is a decision, not a failure. |
| Inference fails on a request it should have been able to answer | 500 | `internal_error` | ERROR `ml.inference_failed` carrying the **exception type** and the character count — never the utterance. |
| Empty, whitespace-only, or over-long text | 422 | `validation_error` | Nothing. `details` carry the bound and the length, never the text. |
| Credential-shaped text | 422 | `validation_error`, `details.reason = "credential_shaped"`, `details.kind = <kind>` | Nothing logged containing the value. See below. |
| Valid text, below threshold | **200** | — | INFO `intent_routed` with intent, status, confidence, threshold, destination, latency. No text. |
| Service target cannot be resolved (renamed service) | 500 | `internal_error` | ERROR. A deployment fault, surfaced through the envelope rather than escaping as a bare `ImportError`. |

### 8.1 Degraded mode

A missing checkpoint, missing torch, or a failed load leaves the runtime **unavailable with
a machine-readable `reason`** rather than crashing. The closed vocabulary is `not_loaded`,
`available`, `disabled`, `checkpoint_missing`, `runtime_missing`, `load_failed`, `stopped`
— a closed set rather than prose, because it is what a health check branches on and what a
caller is told when it gets a 503. "The checkpoint is missing" is an operator-actionable
fact; "something went wrong" is not.

The ML endpoints then answer **503 `ml_unavailable`**. Never 200 with a fabricated result.
`GET /api/v1/ml/status` answers **200** in the same situation and reports `available:
false` with `unavailable_reason` and `model: null` — the endpoint reporting that a
dependency is degraded is itself healthy, and returning 503 would make the one route that
could explain an outage part of it. The flip side is that `/ml/route` does fail closed.

`MLRuntime.load` is idempotent. A second call after a *failed* load is a no-op, so a route
meeting a 503 does not re-read a checkpoint that was just found to be missing on every
single request.

### 8.2 `ML_FAIL_FAST`

Under `ML_FAIL_FAST=true`, every one of the load failures above **raises** instead of
degrading, and the process refuses to start. One method decides between the two modes
(`_handle_failure`), so no branch can accidentally swallow an error the operator asked to
hear about.

The flag exists for the deployment that cannot serve its own contract at all — a host
whose production config points at a checkpoint that is not there. There, a feature that
quietly 503s on every request is worse than a boot failure, because nothing in the
operating picture says ML is supposed to be up. It never fires for `ML_ENABLED=false`,
because refusing to boot over a deliberate off switch would make the switch useless in
exactly the deployments that need it.

### 8.3 Credential screening

Incoming text is scanned with `ml.validation.find_credential` — the same function Phase 10
wrote and tested for untrusted input — which reports the **kind** of credential, never the
value. A refusal is a 422 whose `details` carry `reason: "credential_shaped"` and the kind.
The refusal therefore cannot itself become a second copy of the secret.

It is on by default because the alternative is worse than the false positives it costs: a
user pasting a live key into a chat box has already lost control of that key, and
classifying the request only makes the loss quieter. A user who genuinely means *"change
my password to…"* can turn the screen off with `ML_REJECT_CREDENTIALS=false`.

### 8.4 What is never logged

The submitted utterance never reaches a log, a report or an error message. Predictions are
logged as intent, confidence, threshold, destination, latency, `truncated` and character
count. A failure inside torch is logged as the exception **type**. Exception messages never
carry an absolute path — the checkpoint location is deployment information, and it lives in
the log and the authenticated diagnostics endpoint where it is useful.

---

## 9. Performance

Measured on the machine this phase was built on: **CPU only, 14 torch threads, no GPU
present.**

| | |
| --- | --- |
| `import torch` (cold) | 0.30 s |
| Model load from checkpoint (tokenizer + 703 MiB of weights + `.eval()`) | **4.50 s** |
| First inference after load (cold) | **109.1 ms** |
| Warm inference, median of 30 | **60.2 ms** (range 56.3–64.3 ms) |
| Inference on a ~2,900-character input (truncated to 128 tokens) | 140.6 ms |
| fp32 weight footprint | 704 MB |
| CUDA latency | **not measured — this machine has no CUDA device** |

**CUDA was not measured and no CUDA number is estimated anywhere in this document.** The
`auto` device path resolves to CUDA when `torch.cuda.is_available()` and this build reports
none, so every figure above is a CPU figure. A deployment on a GPU will see a different
warm latency and the same 4.50 s-equivalent load class; this document does not say by how
much.

The 4.50 s load happens **once per process**, in the FastAPI lifespan, in a threadpool —
loading 703 MiB must not block the event loop, because a loop stalled during startup is a
loop that cannot answer the probe waiting on it. It is why `POST /api/v1/ml/route` answers
~60 ms warm rather than ~5 s per request, and why the test suite loads the checkpoint in a
session-scoped fixture rather than per test.

The ~2,900-character row is the point of the `ML_MAX_INPUT_CHARS` ceiling: past 128
subword tokens the extra characters cannot change the prediction, they only cost time. That
input is measured at 140.6 ms against 60.2 ms warm on a short one — the gap is tokenisation
of text that is then thrown away.

---

## 10. Testing

| Suite | Result |
| --- | --- |
| **Phase 11 integration suite** (`tests/test_ml_integration_*.py`, 5 files) | **630 passed, 14 xfailed** |
| — model loading, failure modes, device, lazy import, degraded runtime | `test_ml_integration_loading.py` |
| — all 14 intents, confidence integrity, truncation, validation | `test_ml_integration_classification.py` |
| — routing policy, the no-second-model guard, thresholds | `test_ml_integration_routing.py` |
| — HTTP API, auth, permissions, error envelope, live end-to-end | `test_ml_integration_api.py` |
| — configuration, lifecycle, single-load, concurrency, logging privacy | `test_ml_integration_config.py` |
| **Phase 10 ML regression suite** (`tests/test_ml_*.py` excluding the five above, 13 files) | **321 passed** — unchanged, no regressions |
| **Full backend suite** | **3,221 passed, 14 xfailed, 1 skipped** |
| `ruff check .` | All checks passed |
| `ruff format --check .` | All files already formatted |

The single skip is the pre-existing Windows symlink-privilege skip in
`test_developer_git.py`; it is environmental and predates this phase.

**The 14 xfails are not skipped coverage.** They are the measured generalization failures
of §11, pinned as expected failures so they cannot silently disappear or quietly turn into
passes. Deleting the xfail markers would turn a recorded fact into a green checkmark; that
is the failure mode they exist to prevent.

A note on why the Phase 11 suite carries a pytest marker at all, since the Phase 10 suite
was deliberately 100% offline and torch-free. `pytest.ini` now declares
`ml_model: requires the trained Phase 10 checkpoint on disk and torch installed; skipped
when either is absent`. This is the first suite in the project that cannot be run by
everyone who clones the repository, and the marker follows the same philosophy as the
existing `integration` marker: name the environmental precondition and skip cleanly, with a
reason, rather than hard-failing a clean checkout.

### 10.1 What each file is protecting

**`test_ml_integration_loading.py`** protects the boundary between "there is a checkpoint"
and "there is a usable classifier": every one of the five required member files missing in
turn, unreadable files, invalid JSON, a non-object JSON top level, weights that will not
decode, a tokenizer that will not load, and the label-order mismatch that must refuse the
load. It pins the device contract — `auto` resolving, an explicit `cuda` on a
CPU-only machine **raising** rather than downgrading, an unrecognised device name
refused. And it pins the lazy-import property directly: after `import app.ml`, `import
app.main` and `import app.api.deps`, `sys.modules` must contain no `torch`. That check is
what stops a well-meaning `import torch` at the top of a new module from turning a
supported deployment state into a boot failure.

**`test_ml_integration_classification.py`** protects the inference contract: every one of
the fourteen intents reachable and returning a taxonomy member; the softmax row summing to
one and every confidence inside `[0, 1]`; the three runner-ups being genuinely lower than
the winner and distinct from it; `truncated` true at exactly the right boundary and false
one token short of it; the class bound and the blank-text rejection; and the credential
screen refusing while naming only the kind.

**`test_ml_integration_routing.py`** protects the policy: all four statuses, `target` null
for every negative one and for `uncertain` **across all fourteen intents**, the threshold
verdict at and either side of the boundary, NaN confidence being refused, the generative
classes producing exactly `large-model:unavailable`, and the eleven `SERVICE_TARGETS`
entries resolving against the real classes in `app/services/`. It also carries the
repository's no-second-model guard: every `.py` file in `app/ml/` is swept for twelve
forbidden backend names — `qwen`, `ollama`, `qlora`, `openai`, `anthropic`,
`google.generativeai`, `groq`, `mistral`, `cohere`, `litellm`, `vllm`, `llama` — and the
list of modules to sweep is itself pinned, so a new module added later cannot escape the
sweep by not being listed.

**`test_ml_integration_api.py`** protects the wire contract: 401 without a session, 403
without `analytics.read`, 422 for empty and over-long text and for a credential-shaped
utterance, 503 when the runtime is degraded, the full error envelope shape, `extra="forbid"`
on the request model, and a live end-to-end call through the real stack against the real
checkpoint.

**`test_ml_integration_config.py`** protects configuration and lifecycle: each of the seven
settings' validators, `ml_resolved_model_path` and `ml_checkpoint_exists` deriving without
touching the filesystem at import time, the lifespan loading exactly once and the shutdown
releasing it, the `ASGITransport` fallback in `get_ml_runtime` leaving the singleton
honestly *unloaded*, concurrent `predict` calls leaving the instance's attribute set
unchanged, and the logging assertions that no test anywhere sees a user's utterance in a
log record.

---

## 11. Generalization: what actually happened

This is the most important section in the document. Everything before it describes intent;
this section describes what happened when the model was given language a person would
actually type.

Phase 10's own report warned that *"97.4% was measured on synthetic text"*. Set B below is
the first measurement of what happens when that caveat bites, and it is not flattering.

### 11.1 Two sets, two numbers

**Set A — held-out representative phrasings. 34 utterances, all 14 intents.** Hand-written
for this phase and **not** copied from the training corpus. Result: **34/34 correct
(100%)**.

**Set B — natural-language generalization. 56 utterances.** Written specifically to break
the model: lowercase and ALL-CAPS, trailing or absent punctuation, questions with no
question mark, terse mobile-style phrasing, first person, politeness filler, and
vocabulary deliberately away from the domain nouns the synthetic corpus leans on ("Nexo",
"knowledge base", "pytest", "commits", "repo"). Result: **42/56 correct — 75.0%.**

The 25-point gap between Set A and Set B is not a modelling defect that more training would
fix. It is the synthetic-data caveat showing up on the first test that was designed to look
for it, and it is the number that should travel with every accuracy figure quoted anywhere
else in this repository. **75.0%, not 100%, is what this router does on language that was
not written by the person who wrote the templates.**

### 11.2 Every miss, with the confidence the model assigned

| Utterance | Expected | Predicted | Confidence |
| --- | --- | --- | ---: |
| who else is on the kitchen extension with me | `project_manage` | `account_admin` | 0.9512 |
| what did my day look like on the 14th | `schedule_plan` | `analytics_insight` | 0.6399 |
| write this down for me - the boiler service is due in october | `knowledge_capture` | `schedule_plan` | 0.4673 |
| am i getting slower at this or is it just me | `analytics_insight` | `risk_query` | 0.9558 |
| how much code have i actually shipped this week | `developer_intel` | `analytics_insight` | 0.9924 |
| i havent opened vscode in days lol whats my streak looking like | `developer_intel` | `analytics_insight` | 0.9586 |
| WHEN DID I LAST PUSH ANYTHING | `developer_intel` | `knowledge_lookup` | 0.8396 |
| give me something to practise tonight, ive lost the plot | `learning_track` | `risk_query` | 0.8173 |
| should i keep at the piano or just quit | `learning_track` | `risk_query` | 0.3684 |
| am i actually going to get that senior job or am i deluding myself | `career_track` | `risk_query` | 0.5610 |
| should we move offices or rent the extra floor, talk me through | `deep_reasoning` | `risk_query` | 0.3393 |
| is remote work actually better or is that what everyone says | `deep_reasoning` | `risk_query` | 0.9004 |
| argue me into or out of hiring a second designer | `deep_reasoning` | `risk_query` | 0.8495 |
| if we killed the caching layer tomorrow what would actually break | `deep_reasoning` | `project_manage` | 0.4177 |

### 11.3 Finding one: `risk_query` is a sink

**Seven of the fourteen misses collapse into `risk_query`.** The taxonomy's out-of-scope
class was intended to absorb "nothing here fits", but in practice the model treats
`risk_query` as the sink for anything conversational, uncertain or reflective it cannot
place — *"am i getting slower at this"*, *"should i keep at the piano or just quit"*,
*"am i deluding myself"*, and four of the six reflective or deliberative `deep_reasoning`
utterances.

That is a different failure from what Phase 10's confusion matrix predicted. Phase 10 saw
`risk_query` losing **precision** on a 420-row synthetic split (recall 1.000, F1 0.952); this
is the same shape at four times the intensity on real phrasing. The boundary Phase 10 held
between 0.915 and 0.952 on templates is not held at all on conversational text.

The damage is bounded, and the bound should be stated rather than discovered later:
`risk_query` is a **read** surface (`RiskDetectionService.evaluate`), so a misroute here is
a wrong read rather than a wrong write. That is luck, not design — the routing table happens
to have the sink sitting on a read-only intent. If a future taxonomy reorders or the
confidence model changes, nothing about this particular accident is protected.

### 11.4 Finding two: confidence does not catch these

**Seven of the fourteen misses were predicted at 0.82 or above, and five of those at 0.90
or above — at or past the threshold.** They were accepted, a service was named, and the
caller was told NEXUS knew which surface they meant. The highest-confidence miss in the
table is 0.9924 (*"how much code have i actually shipped this week"* read as
`analytics_insight` rather than `developer_intel`).

This is the single most important limitation of Phase 11 and it belongs next to every
accuracy figure in this repository, not in a footnote.

Low-confidence routing to `uncertain` is a real safety property — it holds for all fourteen
intents, and it is checked. It is **not** a general accuracy defence. A model that is
confidently wrong is confidently wrong, and the threshold cannot know which it is dealing
with. The §6.2 statement that confidence is not calibrated is not an abstraction here; it is
the mechanism by which five of these fourteen got through.

The compounding factor is that the misses are concentrated in exactly the phrasings the
corpus does not produce. Set B deliberately avoided the domain nouns ("Nexo", "knowledge
base", "pytest", "commits", "repo") — and the model's confident errors cluster on the
utterances that use none of them. A caller who writes in the corpus's vocabulary is being
served by a model near its measured accuracy. A caller who does not is not.

---

## 12. Known limitations

1. **The corpus is synthetic, and this is the phase where that stops being theoretical.**
   Phase 10 recorded it as a caveat on an offline artifact. The artifact is now on a
   request path, and Set B measures the consequence: **75.0%** on hand-written natural
   language against **100%** on representative phrasings. The 0.9738 test accuracy says the
   intents are separable in the controlled vocabulary the corpus was rendered from. It does
   not say the model routes real sentences at that rate, and it does not.

2. **`risk_query` is a sink for conversational and reflective phrasing.** Seven of fourteen
   Set B errors land there. The taxonomy has an `out_of_scope` class for material that fits
   nothing, and the model is not using it. The fix is a wording change in the templates for
   `risk_query` and `deep_reasoning` — more rows of the same shape will not move a boundary
   that is being crossed by the absence of vocabulary.

3. **The confidence threshold does not catch confident errors.** Seven of fourteen misses
   were at or above 0.82, four at or above 0.90. See §11.4. This is the limitation that
   would matter most if this router were wired to a mutating surface, which is precisely why
   §12.4 says it is not.

4. **No slot filling — and this is a scope decision, not an oversight.** The router names
   the service and the entrypoint; it does **not** execute the call, and it does not extract
   arguments. Turning *"add a task to draft the migration plan for Friday"* into
   `TaskService.create_task(title="Draft the migration plan", due_date=<Friday>)` requires
   pulling a date out of free text, which is argument extraction. It needs either a second
   model or hand-written per-utterance parsers, and Phase 11 owns neither. Doing it any
   other way — a regex for dates, a guess at the title — would put invented arguments in
   front of a write, which is the one failure mode a router that appears confident makes
   much more expensive. The caller makes the call, through the same authenticated,
   owner-scoped route it would have used had the user typed the request themselves.

5. **The checkpoint is gitignored.** A fresh clone has no model and therefore no ML. This
   is deliberate (703 MiB of binary is not a reviewable diff) and it is the reason `ML_ENABLED`,
   `ML_FAIL_FAST`, degraded mode and the whole of §8 exist. It is also why a CI job cannot
   assert the accuracy figures without running Phase 10 first.

6. **Every performance figure here is a CPU figure.** The machine this phase was built on
   has no CUDA device. `ML_DEVICE=auto` resolved to CPU for every measurement in §9, and
   **no CUDA latency was measured and none is estimated.** The device *path* is tested —
   `auto` resolving, explicit `cuda` raising rather than downgrading — but a GPU deployment's
   numbers are unknown to this document.

7. **There is no second model, and that is a product decision rather than an unfinished
   feature.** One classifier, fourteen classes, two of which are explicitly refused. No LLM
   fallback, no cloud inference, no ensemble. Adding a second model would not make any of
   the fourteen intents more accurate; it would make the `large-model:unavailable` answer a
   lie.

---

## Appendix — file map

| Path | Role |
| --- | --- |
| `backend/app/ml/__init__.py` | the package's architectural summary and its four-module separation |
| `backend/app/ml/model_loader.py` | checkpoint resolution, label cross-validation, device selection, weight loading |
| `backend/app/ml/classifier.py` | `IntentClassifier.predict()` — validation, tokenisation, forward pass, softmax |
| `backend/app/ml/router.py` | `IntentRouter.route()`, `SERVICE_TARGETS`, `resolve_service()`, `routing_taxonomy()` |
| `backend/app/ml/runtime.py` | `MLRuntime`, `MLRuntimeStatus`, the process singleton |
| `backend/app/ml/schemas.py` | `IntentPrediction`, `ServiceTarget`, `RoutingDecision`, `ModelIdentity` |
| `backend/app/ml/exceptions.py` | the 503 / 500 / 422 split |
| `backend/app/api/v1/ml.py` | `POST /ml/route`, `GET /ml/status` |
| `backend/app/schemas/ml.py` | the wire contract |
| `backend/app/core/config.py` | the seven `ML_*` settings and their validators |
| `backend/app/main.py` | `_lifespan` — loading in a threadpool, releasing on shutdown |
| `backend/app/api/deps.py` | `get_ml_runtime`, `MLRuntimeDep` |
| `backend/app/core/exceptions.py` | `ErrorCode.ML_UNAVAILABLE` |
| `backend/ml/datasets/taxonomy.py` | the label contract, read at serving time |
| `backend/tests/test_ml_integration_*.py` | the five Phase 11 test modules |