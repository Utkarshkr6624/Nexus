# Phase 1 Report — Qwen/JARVIS Foundation (read-only audit)

Date: 2026-10-09. Scope: read-only audit + planning documents. No NEXUS file was modified.
Companion docs: `QWEN_ARCHITECTURE_PLAN.md`, `QWEN_TRAINING_PLAN.md`, `QWEN_INTEGRATION_PLAN.md`.

## 1. README understanding

The README describes NEXUS as a "Personal Intelligence & Decision Platform", built in 14 completed
phases: projects/tasks, planner, knowledge, analytics, risks/recommendations, developer (git)
intelligence, learning/career, then ML (Phase 10 DeBERTa intent classifier), ML integration (11),
"Voice + Ollama" (12), "Command Center + Web Intelligence" (13) and hardening (14).
Stack: React/TS/Vite/Tailwind/TanStack Query/Zustand frontend; FastAPI/SQLAlchemy/Alembic/PostgreSQL/JWT backend.

## 2. Actual architecture (verified in code)

- Backend `backend/app/`: `api/v1/*` (actions, activity, analytics, auth, availability, calendar, career,
  developer, health, intelligence, knowledge, learning, ml, planner, projects, recommendations, risks,
  search, tags, tasks, users, work_sessions), `services/`, `repositories/`, `models/`, `schemas/`, `core/`, `ml/`.
- Frontend `frontend/src/`: pages, features (assistant, command-center, command-palette, quick-actions,
  search, ...), services, stores (Zustand), types. API client uses relative `/api/v1` through the Vite proxy.
- DB: PostgreSQL (`core/config.py` postgres_* settings, `DATABASE_URL`), Alembic migrations (14 files).
- Auth: JWT access/refresh, sessions, RBAC permissions (e.g. `analytics.read` guards `/ml/*`), audit log, in-memory rate limiting.
- Tests: ~103 backend test files, ~52 frontend test files. Not run in Phase 1 (read-only rule).

## 3. Existing AI system (verified)

- ONE model: `microsoft/deberta-v3-base`, 14-class intent classifier (`nexo_intents.v1`), ~0.974 test acc on
  synthetic data; **75% (42/56) on hand-written natural language** per `docs/specifications/phase-11-report.md`.
- Served in-process (`app/ml/classifier.py`, `model_loader.py`, `runtime.py`, `router.py`), torch 2.14.1+cpu,
  transformers 5.18.0 pinned. Endpoints: `GET /ml/status`, `POST /ml/route`.
- `app/ml/actions/` + `api/v1/actions.py`: **propose -> confirm** (`POST /ml/action/propose`, `/ml/action/confirm`).
  Hand-written slot extraction (no LLM), a closed `ActionKind` set (task/project/knowledge/learning/planner/
  developer repo/account writes), per-action `destructive` flag. Confirm is "the only thing in NEXUS that executes a sentence".
- `code_assist` and `deep_reasoning` intents have **no destination**: the API returns `generation_unavailable`.
- Training code: `backend/ml/` (datasets schema/taxonomy, training config/checkpoint/manifest, evaluation, `train.py`,
  `configs/small_model.toml`). Datasets/artifacts are gitignored (`backend/ml/datasets/*.jsonl`, `backend/ml/artifacts/`).
- There is no LLM, no generation, no chat/conversation backend, no tool-calling layer.

## 4. Existing voice system (verified)

Frontend-only, in `frontend/src/features/assistant/`: browser Web Speech API for STT (`speech-recognition.ts`;
audio leaves the device on Chrome/Edge, absent on Firefox) and `speechSynthesis` for TTS (local). A state machine
(`use-voice-assistant.ts`), bounded non-persisted Zustand store (20 turns), page `/assistant`. The only backend
call is `POST /ml/route {text}`; history is never sent. Spoken output is routing statements only, never answers.
No backend voice code, no Whisper, no local STT.

## 5. Existing tools (verified)

No generic tool abstraction (no registry, schemas, filesystem/terminal/browser/web tools).
What exists: the ~25 REST routers/services (the de-facto NEXUS "tools"), the closed `ActionSpec`/`ActionKind`
table with propose/confirm and owner-scoped service calls, global search (`/search`), command palette (frontend),
quick-actions (frontend), developer module that shells out to local `git` (`services/developer/git.py`, with an
optional `DEVELOPER_PATH_ALLOWLIST`). Command Center frontend panels are read-oriented (priority queue, measured
panels, ML insight panel, quick actions).

## 6. Qwen integration architecture

See `QWEN_ARCHITECTURE_PLAN.md` and `QWEN_INTEGRATION_PLAN.md`. Summary: Qwen3-8B behind a new isolated
inference service; a tool registry that wraps existing services and `actions.py` propose/confirm; a conversation
manager; voice stays browser-based at first. DeBERTa is kept (cheap router/guard) until evaluation decides.

## 7. Training strategy

QLoRA SFT of Qwen3-8B on Kaggle, tool-call chat format, general-capability replay mixed in. See `QWEN_TRAINING_PLAN.md`.

## 8. Dataset strategy

Categories A-F from the Phase 1 prompt, expressed as chat+tool-call JSONL with a validated schema, simulated tools,
deterministic split and leakage checks. See `QWEN_TRAINING_PLAN.md` section 3.

## 9. Kaggle strategy

Free T4 (16 GB; sometimes 2x T4) / P100 sessions, 4-bit NF4 QLoRA, checkpoint to `/kaggle/working`, resume across
sessions, Kaggle Dataset for data in, Kaggle Model/Dataset (or HF Hub) for adapter out. Quotas/limits must be
re-verified at Phase 4 (not checked here; no network use in Phase 1).

## 10. Local environment

Verified: Windows 11, Python 3.13.14, Node 24.19.0, RTX 3050 Laptop GPU (driver reports 4 GB adapter RAM;
treat as 4 GB VRAM), Intel Iris Xe, 15.6 GB RAM, 161 GB free on E:. Local machine should: build/validate datasets,
schema checks, tokenizer/chat-template checks, tiny dry runs (CPU or tiny model), and later run inference only
if quantized + partial CPU offload is acceptable (slow). It must NOT train Qwen3-8B (needs ~10-12+ GB VRAM even with QLoRA).

## 11. Future JARVIS capabilities (later phases)

General chat/reasoning/coding; NEXUS Q&A; NEXUS actions via propose/confirm; file read/write via sandboxed tools;
terminal (allowlisted, confirmed); web search/fetch via Command Center; multi-step plans with verification;
clarification; honest failure reporting; voice in/out.

## 12. Risks

1. README claims not backed by code (see Findings F1-F2). Plans must not assume Ollama or web tooling exist.
2. 4 GB VRAM cannot hold an 8B model; local inference will be slow or need CPU offload; may be unusable for voice latency.
3. Qwen3-8B on Kaggle T4: no bf16, QLoRA only, ~12 h session cap, weekly GPU quota; long-context tool traces raise memory.
4. Existing project rule "one model only / NEXUS runs no LLM" is written into docs and tests (`test_offline_local_first.py`,
   `requirements.txt` comments). Qwen integration will legitimately contradict these; they must be updated deliberately in the integration phase.
5. Agent tools (files/terminal) are a large security surface; NEXUS's current model is owner-scoped, confirm-before-write.
6. Training data quality: template-generated data caused a 97% -> 75% generalisation gap in Phase 10; the same failure mode applies.
7. Hallucinated tool results: needs negative examples plus runtime enforcement; training alone does not guarantee it.
8. Pinned `transformers==5.18.0` / torch CPU in the backend: Qwen serving needs separate runtime/env, not an in-place upgrade.
9. Qwen3 "thinking" mode / chat-template handling must be fixed in training and serving identically.

## Findings (reported, NOT fixed)

- F1: README says Ollama integration exists. `grep -i ollama` over `backend/app` = 0 hits; `requirements.txt` states
  ollama is "deliberately absent"; `docs/phase-12-voice.md` says NEXUS runs no language model.
- F2: README says "Web / Internet Intelligence". No outbound HTTP client or web route found in `backend/app`
  (only `urllib.parse` usage). Not verified in frontend beyond grep for fetch/ollama.
- F3: `qwen.txt` says default DB is SQLite; code is configured for PostgreSQL. `qwen.txt` also says "No local PostgreSQL" for this machine.
  How the dev DB actually runs here was not verified (`.env` not read).
- F4: `qwen.txt` maps `code_assist`/`deep_reasoning` -> Qwen3-8B (historical); code returns `generation_unavailable`.
- F5: Pre-existing untracked files (20, dated Oct 8): `generate_dataset.py`, `trim_and_validate.py`, `scripts/write_*.py`, `gen_*.py`,
  `validate.py`, `dedup.py`, `qwen.txt`. They write to `E:/Nexo/qwen3-agent/phase2/dataset/v2_final/`, which does not exist now.
  They suggest a Phase-2-style dataset attempt already happened; its output is not present. Also they live inside the NEXUS
  repo, contradicting the isolation goal. Untouched.
- F6: `MlStatusRead` frontend/backend schema drift, documented in `docs/phase-12-voice.md` 8.2. Untouched.
- F7: Roadmap numbering: `qwen.txt` defines 8 phases (Phase 1 = agent specification); the pasted prompt calls Phase 1
  "understanding, safety audit and training foundation". This report treats them as the same Phase 1.

## Phase 2 prerequisites

1. User decision on F1/F2/F3/F5 (what is real, what to do with the root-level dataset scripts).
2. Approve the isolated workspace location (proposed: `E:\Nexo\qwen3-agent\` kept out of `backend/`, or a sibling repo).
3. Approve tool manifest scope and the confirm/permission policy (see integration plan).
4. Approve the chat/tool-call JSONL schema in the training plan.
5. Confirm Kaggle account/quota availability and the adapter hand-off channel.
6. Decide whether to relax the "one model / no LLM" project rule for Qwen (it is a different rule from "one generative model").

## Files changed

Created only (all new, isolated): `qwen_jarvis_phase1/PHASE_1_REPORT.md`, `QWEN_ARCHITECTURE_PLAN.md`,
`QWEN_TRAINING_PLAN.md`, `QWEN_INTEGRATION_PLAN.md`. No existing file modified.
