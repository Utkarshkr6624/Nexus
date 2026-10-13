# Qwen3-8B Training Plan (plan only; nothing run, nothing downloaded)

## 1. Isolation

Training lives outside the app: proposed `E:\Nexo\qwen3-agent\` (git-ignored or its own repo) or a sibling directory.
Contains `data/`, `tools/` (simulated schemas), `configs/`, `scripts/`, `checkpoints/`, `eval/`, `logs/`.
Own virtualenv. Never imports from or writes to `backend/` or `frontend/`; never touches `backend/ml/` (DeBERTa) artifacts.
Note: 20 untracked dataset scripts already sit at repo root / `scripts/` (see report F5); relocate them only with the user's approval.

## 2. Method

- Base: Qwen3-8B (open weights, Apache-2.0 per the Qwen3 release; re-verify license text before use).
- Supervised fine-tuning with QLoRA (4-bit NF4, double quant), LoRA r=16-64 on all linear projections, lr ~1e-4-2e-4 cosine,
  1-3 epochs, packed/length-grouped batches, max seq 4096 (raise only if needed).
- Library candidates: transformers + peft + trl + bitsandbytes (or Unsloth). Versions pinned in the training env only.
- Fixed chat template (Qwen3 ChatML with tool-call format). Decide thinking vs non-thinking mode once, and use the same at train and serve time.
- Loss on assistant turns only (including tool-call turns); tool/user/system turns masked.
- Outputs an adapter; optional merge + GGUF quantization later for local serving.
- Preserve general ability: keep LoRA modest, mix in replay, and compare to the base on general benchmarks (retention gate).

## 3. Data strategy (all new files in the isolated workspace; no existing dataset touched)

Format: JSONL, one record = `messages` (system/user/assistant/tool, assistant `tool_calls` with JSON arguments) + `metadata`
{category, difficulty, tools_available, expected_behavior, source, id}. Validate with a strict schema.

| Category | Content | Notes |
|---|---|---|
| General capability | Q&A, explanation, reasoning, coding, math, planning | Prefer a small replay set of open-licensed instruction data (license-checked) over generating new; goal is retention, not teaching |
| Assistant behavior | conversation, clarification, uncertainty, capability boundaries | |
| NEXUS awareness | what NEXUS is, architecture, features, terminology, workflows | Written from verified docs/code only, no invented endpoints; include "not available" cases (e.g. no Ollama) |
| JARVIS behavior | concise vs detailed, task orientation, action planning | Concise-by-default style spec |
| Tool behavior | need-a-tool decision, tool choice, correct args, result interpretation, failure explanation, multi-step with verification | Simulated tool outputs incl. errors, empty results, permission denied, timeouts |
| Safety/boundaries | never claim unperformed action; never invent tool output; no internet claim without web tool; no file-change claim without write result; reasoning vs execution | Heavy negative/contrast pairs (explain-how vs do-it) |

Suggested mix for a first run (~4-8k examples): 35% general replay, 15% behavior/clarification, 15% NEXUS knowledge,
30% tool-use (incl. 10% failure/recovery), 5% refusals/boundaries. Tune after baseline eval.

Quality controls (from the Phase 10 lesson, where template data gave 97% in-distribution but 75% on real phrasing):
diverse paraphrase and register, multiple human-written seeds per intent, near-duplicate removal (MinHash), leakage check
between splits, hold-out *style-different* test set written by hand, deterministic splits (seeded hash of id), manifest with counts/hashes.

Tool schemas: defined once in `tools/manifest.json`; every tool call in data validated against it; tools are simulated in
training (no real execution). Only tools that will actually exist, or explicitly marked planned-and-unavailable for refusal training.

## 4. Training does not grant capabilities

Weights teach format and judgment. Internet, files, terminal, microphone, NEXUS control come only from the runtime tool layer.
Eval must include "tool not in the provided list" cases where the correct behavior is to say it is unavailable.

## 5. Hardware / environment

| Item | Value (verified locally 2026-10-09) |
|---|---|
| OS | Windows 11 Home |
| Python / Node | 3.13.14 / 24.19.0 |
| GPU | RTX 3050 Laptop (reported 4 GB), Intel Iris Xe |
| RAM / disk | 15.6 GB / 161 GB free on E: |

Qwen3-8B memory (estimates, not measured): bf16 weights ~16 GB; 4-bit ~5-6 GB; QLoRA training at 2-4k seq ~10-14 GB incl. activations
with gradient checkpointing. => cannot train locally; fits Kaggle T4 (16 GB) tight, or 2xT4 / P100 (16 GB).
Local: GGUF Q4 (~5 GB) exceeds 4 GB VRAM; needs CPU offload; expect a few tokens/s. Verify at Phase 4 with a real dry-run, not assumption.
Python 3.13 compatibility of bitsandbytes/peft/trl locally is unverified; Kaggle's own environment will be used for the real run.

## 6. Local vs Kaggle

Local (free, no GPU waste): dataset building, validation, dedup, splits, tokenizer/chat-template tests, token-length stats,
config linting, training-script dry run on a tiny model (e.g. Qwen3-0.6B) on CPU/small GPU, eval harness development.
Kaggle: model load test of 8B 4-bit, short smoke run (~20 steps), then the real run. Notes: T4 has no bf16 (use fp16 compute);
sessions are time-limited (~9-12 h) and weekly GPU quota is limited (re-verify exact numbers); save checkpoints every N steps to
`/kaggle/working`, support resume, upload data as a Kaggle Dataset and the adapter as an output/Model; internet may be needed to pull weights (enable in notebook settings).
Expected cost: $0. Expected time: roughly 2-6 GPU-hours for ~5k examples x 2 epochs on a T4 (estimate; measure in dry run).

## 7. Evaluation (gate before any integration)

Baseline Qwen3-8B vs tuned on: general reasoning/coding retention, NEXUS Q&A, tool selection accuracy, argument validity (schema),
answer-vs-action discrimination, multi-tool plans, clarification, failure recovery, hallucinated-execution rate (must be ~0), unknown-tool refusal.
Do not rely on loss. Fail the gate -> stop, iterate on data.

## 8. Stages

2 dataset -> 3 tool manifest/simulation -> 4 pipeline prep + dry run -> 5 training -> 6 evaluation -> 7 integration -> 8 JARVIS UX.
Each gated on the previous; no GPU spend before the Phase 4 dry run passes.
