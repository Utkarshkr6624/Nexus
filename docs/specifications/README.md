# NEXUS — Phase Specifications

This directory holds the **authoritative specifications** for NEXUS Phases 3 through 12,
exactly as they were issued by the project owner.

These are the contracts the code is built to. When the implementation and a specification
disagree, the specification is what the work is measured against — and the disagreement should be
fixed in one direction or the other, never left ambiguous.

| Spec | Phase | Module | Status |
| --- | --- | --- | --- |
| [`phase-3-projects-tasks.md`](./phase-3-projects-tasks.md) | 3 | Projects, Tasks & Work Management | ✅ Complete |
| [`phase-4-planner.md`](./phase-4-planner.md) | 4 | Intelligent Planner, Calendar & Scheduling Engine | ✅ Complete |
| [`phase-5-knowledge.md`](./phase-5-knowledge.md) | 5 | Knowledge Base, Notes, Resources & Knowledge Graph | ✅ Complete |
| [`phase-6-analytics.md`](./phase-6-analytics.md) | 6 | Analytics & Intelligence Data Engine | ✅ Complete — [report](./phase-6-report.md) |
| [`phase-7-risk-recommendations.md`](./phase-7-risk-recommendations.md) | 7 | Risk Detection & Recommendation Engine | ✅ Complete — [report](./phase-7-report.md) |
| [`phase-8-9-developer-learning-career.md`](phase-8-9-developer-learning-career.md) | 8 + 9 | Developer Intelligence + Learning & Career Intelligence | ✅ Complete — [Phase 8 report](./phase-8-developer-report.md) · [Phase 9 report](./phase-9-learning-career-report.md) |
| [`phase-10-architecture.md`](./phase-10-architecture.md) · [`phase-10-training.md`](./phase-10-training.md) · [`phase-10-report.md`](./phase-10-report.md) | 10 | ML Training — routing/intent classifier | ✅ Complete — the classifier was trained and evaluated ([report](./phase-10-report.md)) |
| [`phase-11-ml-integration.md`](./phase-11-ml-integration.md) · [`phase-11-report.md`](./phase-11-report.md) | 11 | ML Integration — the trained classifier, served | ✅ Complete — two endpoints, and the Phase 10 checkpoint answers them ([report](./phase-11-report.md)) |
| [`../phase-12-voice.md`](../phase-12-voice.md) | 12 | Voice — the browser's own Web Speech APIs as the interface to the one classifier | ✅ Complete — documented at [`docs/phase-12-voice.md`](../phase-12-voice.md), the path the Phase 12 brief specified |

> **Where the Phase 12 document lives.** Phase 12 is indexed here because it is a phase,
> but the document itself sits one directory up, at
> [`docs/phase-12-voice.md`](../phase-12-voice.md), because that is the path the Phase 12
> brief specified. It is deliberately *not* duplicated into `docs/specifications/` — two
> copies of one document is one copy that will eventually be wrong. If a
> `phase-12-voice.md` is ever added to this directory, this row is what should point at it
> instead.

### Internal contracts

These are the working contracts each phase was built against in parallel, one per
implementation swarm. They are **superseded by the corresponding report** wherever the two
disagree, and each disagreement is recorded in that report.

| Contract | Phase | Covers |
| --- | --- | --- |
| [`phase-7-contracts.md`](./phase-7-contracts.md) | 7 | Scoring functions, the risk and recommendation schemas, the recommendation rules |
| [`phase-8-developer-contracts.md`](./phase-8-developer-contracts.md) | 8 | The git engine boundary, the four git tables, the eight metrics, the feature vector |
| [`phase-9-learning-career-contracts.md`](./phase-9-learning-career-contracts.md) | 9 | 🔴 The five enums, the six tables, `SkillGap`, the routers, the feature vectors |

A phase is marked complete when its brief is implemented, the full suite is green, and the
mandatory regression check in that brief has been run. Each completed phase has a report
documenting what was built, what was wrong, and what was actually executed.

### Remediation pass

After Phase 9 shipped, an audit ran over Phases 1–9 and found real defects — three of them
data-loss or correctness blockers, plus a coverage gap where Phases 3–5 had no dedicated test
modules at all. Both phase reports carry a clearly-marked remediation section recording what
changed and which of their own claims were wrong:

| Report | Remediation section |
| --- | --- |
| [`phase-8-developer-report.md`](./phase-8-developer-report.md) | [§12 Remediation pass](./phase-8-developer-report.md#12-remediation-pass) — the never-scanned repository leaving the feature vector, and the inverted `maintenance_activity` claim |
| [`phase-9-learning-career-report.md`](./phase-9-learning-career-report.md) | [§13 Remediation pass](./phase-9-learning-career-report.md#13-remediation-pass) — migration `0010`'s three unenforced invariants, and the same inverted claim |

[`../architecture.md` §18](../architecture.md#18-remediation-pass-over-phases-19) is the
single account of the whole pass: the three blockers, the coverage gap, the versioned feature
vector, and every number in the documentation set that moved because of it.

## Why these are stored rather than kept in chat

Several of these phases were issued across separate sessions and were, more than once, sent
against a codebase that had not yet reached the previous phase. Storing them means:

- the **dependency chain is explicit** — Phase 4's scheduling model requires Phase 3's
  `task_id`, `due_date` and `estimated_minutes`; Phase 6's analytics require Phase 4's work
  sessions; Phase 7's risks require Phase 6's metrics;
- the **acceptance criteria survive** a context reset, so a phase can be resumed without
  re-reading a conversation;
- **scope disputes are settled from a document**, not from recollection.

## Cross-phase dependencies

```text
Phase 3  Projects · Tasks · Tags · Events
   │      (task_id, due_date, estimated_minutes, dependencies, subtasks)
   ▼
Phase 4  Planner · Calendar · Work Sessions · Availability
   │      (scheduled start/end, actual vs planned duration, time-of-day)
   ▼
Phase 5  Knowledge Base · Notes · Concepts · Relationships
   │      (links knowledge to projects and tasks)
   ▼
Phase 6  Analytics & Intelligence Data Engine
   │      (aggregates every event stream above into metrics)
   ▼
Phase 7  Risk Detection & Recommendation Engine
          (consumes Phase 6 metrics + Phases 3–5 data)
```

Phases 8 and 9 branch from the trunk rather than following it:

```text
Phase 8  Developer Intelligence ──┐
   │      (local git history: commits, branches, changed lines)
   │      → developer_features.v1
                                   ├──> Phase 10 (ML training)
Phase 9  Learning & Career Int. ──┘
          (goals, skills, gaps, career profile and evidence)
          → learning_features.v1, career_features.v1
```

Both phases produce **features**, not models: nothing is trained, loaded, served or
registered. Each feature row is stamped with a schema version
(`developer_features.v1`, `learning_features.v1`, `career_features.v1`) so a Phase 10
trainer knows what every column meant without having to trust the client that ordered them.

That chain ends at Phase 12 rather than at Phase 10: Phase 10 trains the routing classifier
and writes artifacts, Phase 11 is what loads one inside the API process, and Phase 12 is the
interface in front of it. The first two are one pipeline with two boundaries, and the second
boundary is where a model that was only ever measured becomes one that answers requests.

### Phase 10 — ML training

Phase 10 is the first phase that trains anything, and it is the reason Phases 8 and 9
stamped their feature vectors. It lives in its own package, `backend/ml/`, and does not
touch `backend/app/`: one model, two execution environments, one entry point.

The model — `microsoft/deberta-v3-base`, fine-tuned as a classifier over the 14 Nexo
intents — **routes, it does not answer**: it decides which capability should handle an
utterance. Twelve of the fourteen intents are handled by the deterministic services in
`app/services/`, and the remaining two, `code_assist` and `deep_reasoning`, are marked
`large-model:unavailable` — NEXUS runs no language model, so those two are trained and
predicted anyway, so that the router can recognise a request it cannot serve rather than
being blind to it. `out_of_scope` is a trained class too, so abstention is something the
model can be *right* about. Phase 10 ends at artifacts — **no model is loaded into the
running application**, no route or service reads one, and loading it belongs to Phase 11.

An earlier draft of this phase specified a second trained model as well. It was removed
from the repository outright — not disabled — and nothing in this document or in the code
refers to it as though it existed. The report records what was deleted and why.

The entry point is `python -m ml.train`, run from `backend/`. It has three selectable
stages — `--prepare`, `--train-small` and `--evaluate` — and `--all`, or no flag at all,
runs all three in that order. The Makefile wraps them in nine `ml-*` targets.

| Document | Covers |
| --- | --- |
| [`phase-10-architecture.md`](./phase-10-architecture.md) | The `backend/ml/` package, its two interpreters, the dataset pipeline, the manifests, and the boundary that keeps a training run out of the API process |
| [`phase-10-training.md`](./phase-10-training.md) | The routing classifier (`microsoft/deberta-v3-base` over the 14 Nexo intents), the evaluation, and what was actually executed |
| [`phase-10-report.md`](./phase-10-report.md) | The final run report: corpus, metrics, checkpoint/resume evidence, and the limitations |

Phase 10 is **complete**, and the report says so in its first line. The routing
classifier was trained on CPU and evaluated on 308 held-out rows — **0.9675 accuracy,
0.9674 macro F1**, worst per-intent F1 0.927, from `microsoft/deberta-v3-base` at
184,432,910 parameters. A retrain over a larger corpus is in flight and has no result
yet; the numbers above are the last run that finished. Standing rule 5 below applies to
this phase more than to any other — read
[`phase-10-report.md`](./phase-10-report.md) for the full account.

### Phase 11 — ML integration

Phase 11 is the other half of Phase 10, and it is deliberately the smaller one. Phase 10
produced a checkpoint; Phase 11 puts that checkpoint inside the running application and
stops there. The boundary it drew is the whole of its design:

```text
user text
  → app/api/v1/ml.py        POST /api/v1/ml/route   (auth + analytics.read)
  → app/ml/runtime.py       one model per process, loaded in the FastAPI lifespan
  → app/ml/classifier.py    predict() — validation, raw text, tokenizer, model, softmax
  → app/ml/model_loader.py  checkpoint resolution, label cross-validation, device
  → IntentPrediction        intent + confidence + alternatives + latency
  → app/ml/router.py        threshold verdict, destination, existing service
  → RoutingDecision         status + destination + ServiceTarget + reason
  → response
```

**It routes; it does not answer, and it does not call anything.** The decision names the
service an utterance belongs to and the entry point on it; the caller makes the call,
through the same authenticated, owner-scoped route they would have used had they typed
the request. There is no second model and no generative model: `code_assist` and
`deep_reasoning` are trained classes that reach the destination `large-model:unavailable`
and no service at all, because NEXUS runs no language model and would rather say so than
answer a code question with a confident non-answer.

**Degradation is an answer, not a crash.** `backend/ml/artifacts/` is gitignored, so a
fresh clone has no checkpoint. That is a supported state: the runtime records a
machine-readable reason, the app boots, every other route keeps working, and
`POST /api/v1/ml/route` answers **503 `ml_unavailable`** rather than a fabricated intent.
`ML_FAIL_FAST=true` turns that into a refusal to boot, for a deployment that would rather
fail loudly than run with its headline feature quietly missing.

**Two endpoints, and the count is the design.** `POST /ml/route` already returns the
intent *and* the confidence, so a separate `/ml/predict` would be the same payload under a
second URL — and a second URL is a second thing to authenticate, version, document and
eventually deprecate. `GET /ml/status` answers **200 even when ML is broken**, which is the
health-endpoint precedent verbatim: the endpoint reporting that a dependency is degraded
is itself healthy.

| Document | Covers |
| --- | --- |
| [`phase-11-ml-integration.md`](./phase-11-ml-integration.md) | The serving boundary: `app/ml/`, the two endpoints, the configuration surface, the label contract, and the rules about what may cross it |
| [`phase-11-report.md`](./phase-11-report.md) | What was executed: the routing threshold and the measurements behind it, the generalisation results including the 75.0% that must not be softened, latency, and the limits of all three |

### Phase 12 — Voice

Phase 12 adds an interface, not a model. NEXO still runs exactly one — the Phase 10
classifier, served by Phase 11 — and the voice layer is the thing a person talks to.

```text
microphone → SpeechRecognition (the browser's own, vendor-transcribed)
              │  one final utterance, nothing else
              ▼
            /assistant  ──►  POST /api/v1/ml/route   (auth + analytics.read)
              │                { text } — exactly one field, never the history
              ▼
            Phase 11 → the 14-class classifier → a routing decision
              │
              └──────────►  window.speechSynthesis  (local, the OS voice)
```

**No second model, no LLM, no Whisper, no cloud AI API, no paid service.** Speech-to-text
uses the Web Speech API and text-to-speech uses `window.speechSynthesis`; neither is a model
NEXO trains, serves or pays for, and that is precisely why they were chosen.

**The one-model rule is what forced the privacy limitation.** Chrome and Edge transcribe on
the browser vendor's servers — **your microphone audio leaves your machine** — and Firefox
implements `SpeechRecognition` not at all. The only fully-local alternative is a local
speech-recognition model, and adding one would breach the constraint the whole project is
built on. So the trade is stated in the product: `speech-recognition.ts` exports a canonical
`RECOGNITION_PRIVACY_NOTICE` the UI renders verbatim, the typed field is always available,
and it is labelled as the only way in on a browser with no recogniser.

**One utterance per turn, forever.** The classifier has no dialogue state, so history is
never sent — a test asserts the request body is exactly `{ text }`. It exists only to render
the log and to offer a "you last routed X" chip when a turn comes back `uncertain`.
**NEXO routes, it does not answer**: a spoken request yields a named service and entrypoint
(`TaskService.list`), and `code_assist` / `deep_reasoning` come back
`generation_unavailable`, which is a stated capability gap. Executing the named action is
Phase 13's Command Center and does not exist yet. And Phase 11's 75.0% carries forward
unchanged — **voice is the same model behind a different input method, and improves nothing
about it.**

| Document | Covers |
| --- | --- |
| [`../phase-12-voice.md`](../phase-12-voice.md) | The whole phase: the microphone privacy limitation and why NEXO accepts it, the six-state lifecycle, both browser wrappers, the bounded conversation store, the API and status codes, security, testing, and the limitations that are not bugs |

## Standing rules that apply to every phase

These recur in the specifications and are not restated in each file:

1. **No fake data.** Every number on screen traces to a database row. A metric that cannot be
   computed from insufficient data says so — "Not enough data yet" — rather than rendering `0%`.
2. **Deterministic before learned.** Every phase through 9 is rules and arithmetic, never an LLM
   and never a trained model. The deterministic engine stays as the fallback for cold-start users.
   Phase 10 is where that changes — a model is trained — and it does not displace the rules: the
   deterministic path remains the answer whenever a model is absent, unevaluated, or unsure.
   Phase 11 is where that promise is cashed in. The trained classifier is now served inside the
   running application, and the deterministic service behind an intent is still the thing that is
   actually called: a prediction below `ML_CONFIDENCE_THRESHOLD` names no service at all, and a
   deployment with no checkpoint answers 503 rather than guessing.
   Phase 12 does not cash it in any further, and says so: the voice layer adds an interface
   to the same classifier, sends it one utterance at a time, and names a service rather than
   executing anything. A browser with no speech recogniser is a rendered state, not a broken
   product, because the typed path is always there.
3. **Explainability is a requirement, not a nicety.** Every score states its formula. Every risk
   states why it exists.
4. **Ownership is enforced in the query.** Tenant scoping lives in the repository, never in a
   post-fetch check, and never in the frontend.
5. **Report only what was executed.** A test that was not run is not a passing test.