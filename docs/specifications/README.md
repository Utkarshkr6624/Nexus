# NEXUS — Phase Specifications

This directory holds the **authoritative specifications** for NEXUS Phases 3 through 10, exactly
as they were issued by the project owner.

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

## Standing rules that apply to every phase

These recur in the specifications and are not restated in each file:

1. **No fake data.** Every number on screen traces to a database row. A metric that cannot be
   computed from insufficient data says so — "Not enough data yet" — rather than rendering `0%`.
2. **Deterministic before learned.** Every phase through 9 is rules and arithmetic, never an LLM
   and never a trained model. The deterministic engine stays as the fallback for cold-start users.
   Phase 10 is where that changes — a model is trained — and it does not displace the rules: the
   deterministic path remains the answer whenever a model is absent, unevaluated, or unsure.
3. **Explainability is a requirement, not a nicety.** Every score states its formula. Every risk
   states why it exists.
4. **Ownership is enforced in the query.** Tenant scoping lives in the repository, never in a
   post-fetch check, and never in the frontend.
5. **Report only what was executed.** A test that was not run is not a passing test.