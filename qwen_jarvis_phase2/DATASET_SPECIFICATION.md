# Dataset Specification — `qwen_jarvis_dataset_v2` (v1 numbers shown where noted)


> **Phase 2.1 update (v2).** Same schema and rules as below, plus a template-repetition trim and a minimum-per-category gate. IDs now start with `qj2-`. Delivered counts in v2: general_conversation 1090, general_knowledge 1173, reasoning 1500, programming 1500, mathematics 750, instruction_following 750, jarvis_assistant 1000, nexus_knowledge 944, tool_aware 975, capability_boundaries 650, multi_turn_context 500, safety 500; total 11,332; multi-turn 1,465; split 10,296/518/518. The 'Delivered' column in the table below is the v1 figure. See `reports/PHASE_2_1_QUALITY_REPORT.md`.

## 1. Purpose and scope

Training data for a future fine-tune of **Qwen3-8B** into a general-purpose assistant with NEXUS awareness and tool-aware behaviour.
It teaches *behaviour and judgement*, not capability: training does **not** give the model internet, file, terminal, microphone or NEXUS access.
Those come only from a runtime tool layer that does not exist yet. The data is therefore written so the assistant:

1. answers directly when no tool is needed,
2. says plainly when a capability is unavailable (and offers what it *can* do),
3. proposes a tool call — never states a result it has not received,
4. asks a clarifying question when an action is ambiguous or risky,
5. reports tool failures and empty results exactly as returned,
6. describes NEXUS accurately, separating **implemented today** from **planned**.

## 2. Record formats

**Training format** (`*_messages_only.jsonl`) — one JSON object per line:

```json
{"messages": [
  {"role": "system",    "content": "You are the NEXUS assistant. Runtime capabilities this session: none. ..."},
  {"role": "user",      "content": "Read build.log and give me the key points."},
  {"role": "assistant", "content": "I don't have access to your files, so I can't read build.log. If you paste the text, I'll list the key points."}
]}
```

- Roles: `system` (optional, first, at most one), `user`, `assistant`. First non-system turn is `user`; turns alternate; the last turn is `assistant`.
- Multi-turn = two or more `user` turns. Longest example: 6 user turns.

**Full format** (`train.jsonl`, `validation.jsonl`, `test.jsonl`, `dataset/validated/…_all.jsonl`) adds `id` and `metadata`:

| field | meaning |
|---|---|
| `id` | `qj1-` + the first 12 hex characters of the MD5 of the canonical JSON of `messages` (stable across rebuilds) |
| `category` | one of the 12 categories below |
| `subcategory` | generator-level label (e.g. `tool_flow`, `cannot_confirm`, `debug_logic`) |
| `difficulty` | `easy` / `medium` / `hard` (generator-assigned, indicative) |
| `source_type` | `authored` (hand-written), `computed` (solved/checked in code), `verified` (code was executed), `composed` (authored parts combined with slot values/framings) |
| `group` | family id used so related examples stay in the same split |
| `validation_status` | `accepted` |
| `turns` | number of user turns |

## 3. Conventions that matter for later phases

- **Runtime context lives in the system message.** 68% of examples carry one. It states either that no tools are attached
  (`Tools connected in this session: none`, `Runtime capabilities this session: none`, `No tools are attached…`) or lists attached tools
  (`Available tools: web.search, fs.read_file, …`). The assistant's behaviour follows from it. Examples without a system message
  are context-free general conversation.
- **No tool-call wire format is defined here.** A proposed call is written in prose as ``Proposed call: `tool(arg=...)` `` — this is a
  placeholder for Phase 3, not a format to train a parser on. Tool names (`web.search`, `fs.read_file`, `shell.run`, `app.create_task`, …)
  are illustrative and **do not claim NEXUS has these tools**; the system message says whether any are attached.
- **Tool results** appear as `user`-role messages that start with `[tool result from <tool>]` (130 examples). A dedicated `tool` role is a Phase 3 decision.
- **Assistant never claims execution it cannot have performed.** The validator rejects first-person action claims ("I saved…", "I ran…") unless the
  conversation contains an actual `[tool result …]` message.
- **NEXUS facts** come from `scripts/nexus_facts.py`, derived from `qwen_jarvis_phase1/PHASE_1_REPORT.md`. Every fact is tagged
  `C` (implemented), `P` (planned) or `N` (absent / README claim not backed by code). Nothing in the dataset claims Ollama, Whisper, web access,
  file/terminal tools, a chat endpoint, a server-side conversation store, or an integrated/trained Qwen exists today.
- **Qwen3 thinking mode:** examples contain no `<think>` blocks. Choosing thinking vs non-thinking behaviour is a Phase 4 decision.

## 4. Categories (target → delivered)

| # | Category (`category`) | Target | Delivered | Notes |
|--:|---|--:|--:|---|
| 1 | `general_conversation` | 1,000 | 630 | small talk, empathy with specific advice, recommendations, clarification, advice, casual multi-turn |
| 2 | `general_knowledge` | 1,000 | 1,174 | 246 fact sheets × 4 question forms; 190 three-turn follow-up dialogs |
| 3 | `reasoning` | 1,500 | 1,500 | ordering/knights-knaves/assignment puzzles, scheduling, critical path, weighted decisions, patterns, cause/effect, estimation |
| 4 | `programming` | 1,500 | 1,335 | verified Python/6-language algorithms, debugging, SQL, git, regex, code review, concepts |
| 5 | `mathematics` | 750 | 750 | computed with exact arithmetic; worked solutions |
| 6 | `instruction_following` | 750 | 750 | conversions, extraction, list ops, constraint writing, classification |
| 7 | `jarvis_assistant` | 1,000 | 1,119 | prioritisation, weekly plans, routines, meeting prep, drafting, "what do you need from me", identity, style control |
| 8 | `nexus_knowledge` | 750 | 622 | fact Q&A, built-vs-planned, README-vs-code, UI scenarios, dialogs |
| 9 | `tool_aware` | 750 | 975 | no-tool refusals, proposals, tool mismatch, direct answers, clarification, tool-result flows |
| 10 | `capability_boundaries` | 500 | 650 | cannot-confirm, no-access, no-action, no-live-data, no-memory, pretend/pressure, failure reports |
| 11 | `multi_turn_context` | 500 | 476 | running state, list edits, unit follow-ups, changing requirements, corrections, reference resolution |
| 12 | `safety` | 500 | 519 | scams, health limits, declining deceptive/privacy-invading requests with alternatives, crisis support, calibrated uncertainty, AI honesty |
|   | **Total** | **10,000** | **10,500** | no category exceeds 14.3% of the dataset |

Multi-turn examples are spread across categories (not only #11): 1,134 in total.

## 5. Quality rules enforced by code (`validation/checks.py`)

- Structure: roles, alternation, non-empty content, balanced code fences.
- No unresolved template placeholders; no metadata words leaking into assistant text.
- No first-person claims of having performed actions without a tool result in the conversation.
- NEXUS claims: forbidden assertions (e.g. "NEXUS uses Ollama") are rejected unless negated/qualified as planned; unknown API paths are rejected.
- Tool awareness: no proposed tool calls when the system message says no tools are attached.
- Boundaries: every `capability_boundaries` answer must contain an explicit limitation/failure statement.
- Dedup (build): exact conversation, normalised prompt, MinHash near-duplicate prompts (≥0.8 within a category), near-duplicate answers, and a cap of two identical long answers.
- Leakage control (build + audit): group-aware splitting, then any held-out example whose prompt is ≥0.7 similar to a training prompt, or whose long answer also appears in another split, is moved into train.

## 6. Splits

90/5/5 by category, assigned per `group` so a family of related examples never straddles splits. After leakage control the realised split is
**91.0% / 4.5% / 4.5%**. Every category appears in validation and test.
