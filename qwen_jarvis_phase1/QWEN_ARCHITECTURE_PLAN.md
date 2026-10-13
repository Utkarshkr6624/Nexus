# Qwen Architecture Plan (conceptual, nothing implemented)

Principle: Qwen = brain, NEXUS = body, tools = capabilities, voice = interface, Command Center = outside world.
One generative model (Qwen3-8B). DeBERTa is a classifier, not a generative model; keeping it is not a "second LLM".

## Pipeline

```
USER
 -> NEXUS UI / VOICE            [exists: /assistant page, browser STT/TTS; chat UI does not exist]
 -> CONVERSATION / INPUT LAYER  [missing: history, system prompt, context window, per-user session]
 -> QWEN3-8B                    [missing: inference service]
 -> DECISION / TOOL-USE LAYER   [missing: tool-call parser, loop controller, step limit]
       answer directly  |  call tool  |  ask clarification  |  state unavailable
 -> PERMISSION GATE             [partially exists: RBAC + propose/confirm + destructive flag]
 -> NEXUS TOOLS                 [exists as REST services; wrappers missing]
 -> WEB / FILES / COMMAND CENTER / APP ACTIONS  [web/files/terminal missing]
 -> TOOL RESULT (structured, truthy)
 -> QWEN -> next step or final answer -> USER (text, optionally spoken)
```

## Integration points (all read-only findings)

| Point | Current location / responsibility | Why suitable | Qwen connects as | Do NOT change yet | Deps / risks |
|---|---|---|---|---|---|
| Model service | none; `app/ml/` hosts only DeBERTa (`runtime.py` singleton, lazy torch) | `app/ml` already shows the load-once, 503-when-unavailable pattern | New `app/llm/` (or separate process) exposing `generate(messages, tools)` | `app/ml/*`, pinned torch/transformers | 8B on CPU-only backend env unusable; prefer a separate local server process (llama.cpp/vLLM-style OpenAI-compatible endpoint) |
| Backend AI endpoint | `api/v1/ml.py` (`/ml/status`, `/ml/route`) | Existing auth (`analytics.read`), schemas, error envelope | New router e.g. `/assistant/chat` (streaming) | existing `/ml/*` contracts | needs a new permission; streaming via SSE |
| Conversation manager | frontend-only 20-turn store; backend stateless | Backend history is needed since the model, unlike the classifier, uses it | New `conversations` tables + service | existing tables/migrations | new Alembic migration (later phase only); privacy of stored transcripts (current design deliberately never persists) |
| Tool layer | closed `ActionSpec` table in `app/ml/actions/proposals.py`; services/repositories | Already owner-scoped, validated, with destructive flag and confirm gate | Tool registry whose NEXUS tools call the same service methods; writes go through propose/confirm semantics | `actions.py` routes and `ActionKind` | slot-extraction logic is regex-ish; Qwen supplies structured args instead, so adapter must re-validate with the same pydantic payloads |
| Voice pipeline | `features/assistant/*` hook + state machine | Clean state machine; swapping the `/ml/route` call for chat is localised | Hook sends transcript to chat endpoint, speaks the reply | STT/TTS wrappers, privacy notice | TTS of long answers; reply must be speakable; STT privacy limitation unchanged |
| Command Center | `features/command-center/*` + quick-actions | Natural home for status, execution history, confirmations | Panels showing tool traces and pending confirmations | existing panels | UI work only in later phase |
| Frontend chat UI | none (assistant page shows routing decisions) | - | New chat view or evolve `/assistant` | `assistant-page` tests that assert "routes, not answers" copy | those tests/copy encode the no-LLM stance and will have to change deliberately |
| Web / files / terminal tools | none | - | New sandboxed tool modules | developer `git.py` and `DEVELOPER_PATH_ALLOWLIST` as the model for allowlisting | largest security surface |

## Exists vs. later

Exists: auth/RBAC, services, propose/confirm, DeBERTa router, browser voice, command center panels, search.
Later: inference service, chat endpoint + streaming, conversation store, tool registry/executor, permission policy
for files/terminal/web, web tooling, chat UI, execution history UI, evaluation harness, tests.

## Design rules for the runtime (to be enforced in code, not trusted to training)

1. Every assistant claim of action must correspond to a recorded tool call with a success result.
2. Tool results are produced only by the executor; the model cannot write `tool` turns.
3. Writes/destructive actions require explicit user confirmation.
4. Hard step limit and timeout per request; tool arguments validated by schema before execution.
5. Per-user scoping on all NEXUS tools (owner id injected by the runtime, never taken from model output).
6. File/terminal/web tools off by default, enabled per capability, with path allowlists.
