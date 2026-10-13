# Dataset Generation Report — `qwen_jarvis_dataset_v1`

Phase 2 of the Qwen/JARVIS roadmap: dataset engineering only. No model was trained, downloaded or configured, no Kaggle GPU was used,
and no NEXUS file, dependency or configuration was changed.

## 1. Phase 2 objective

Produce at least 10,000 validated supervised fine-tuning examples for a future Qwen3-8B JARVIS/NEXUS assistant that stays a
general-purpose assistant while learning NEXUS facts, tool-aware judgement and honest capability boundaries.
**Result: 10,500 accepted examples, 1,134 of them multi-turn.**

## 2. Dataset architecture

```text
28 generators (scripts/gen_*.py)            -> dataset/generated/*.jsonl      14,592 raw records
        |  validation/checks.py (per-example checks)                          -2 rejected
        |  dedup (exact, normalised prompt, MinHash near-dup, answer cap)     -3,396 removed
        |  balance (per-category cap, priority subcategories kept)            -694 trimmed
        v
dataset/validated/qwen_jarvis_dataset_v1_all.jsonl                            10,500
        |  group-aware stratified split + leakage control                     (123 held-out examples moved to train)
        v
dataset/splits/{train,validation,test}[_messages_only].jsonl                  9,556 / 472 / 472
        |  validation/validate_final.py  (independent audit of the files on disk)
        v
dataset/dataset_manifest.json (sha256 per file) · reports/*.json
```

Everything is standard-library Python; `scripts/run_all.py` rebuilds the lot in ~1 minute and two consecutive full rebuilds were byte-identical.

## 3. Dataset schema

See `DATASET_SPECIFICATION.md`. Training records are `{"messages": [...]}` with roles `system`/`user`/`assistant`; full records add
`id` and `metadata` (`category`, `subcategory`, `difficulty`, `source_type`, `group`, `validation_status`, `turns`). The system message carries the
runtime context (tools attached or not), which is what makes tool-awareness learnable.

## 4. Category distribution (final)

| Category | Target | Final | % of target | train / val / test | multi-turn |
|---|--:|--:|--:|---|--:|
| general_conversation | 1,000 | 630 | **63%** | 568 / 31 / 31 | 30 |
| general_knowledge | 1,000 | 1,174 | 117% | 1,097 / 33 / 44 | 190 |
| reasoning | 1,500 | 1,500 | 100% | 1,360 / 71 / 69 | 0 |
| programming | 1,500 | 1,335 | 89% | 1,201 / 68 / 66 | 98 |
| mathematics | 750 | 750 | 100% | 678 / 36 / 36 | 0 |
| instruction_following | 750 | 750 | 100% | 676 / 38 / 36 | 0 |
| jarvis_assistant | 1,000 | 1,119 | 112% | 1,009 / 56 / 54 | 110 |
| nexus_knowledge | 750 | 622 | **83%** | 570 / 26 / 26 | 87 |
| tool_aware | 750 | 975 | 130% | 901 / 37 / 37 | 96 |
| capability_boundaries | 500 | 650 | 130% | 596 / 24 / 30 | 39 |
| multi_turn_context | 500 | 476 | 95% | 436 / 24 / 16 | 476 |
| safety | 500 | 519 | 104% | 464 / 28 / 27 | 8 |
| **Total** | **10,000** | **10,500** | 105% | **9,556 / 472 / 472** | **1,134** |

The largest category (reasoning) is 14.3% of the dataset. Two categories are under target (conversation 63%, NEXUS 83%) and programming is at 89%;
the shortfall was made up by the over-target categories, so the **total** requirement is met, but the per-category targets are not all met (see concerns).

## 5. Total generated examples

14,592 raw records from 28 generator files.

## 6. Total validated examples

10,500 accepted (final). 14,592 − 2 (validation) − 3,396 (duplicates) − 694 (balance trimming) = 10,500.

## 7. Rejected examples

| Stage | Count | Reason |
|---|--:|---|
| Per-example validation | 2 | boundary answer lacked an explicit limitation statement |
| Balance trimming | 694 | tool_aware 267, mathematics 170, reasoning 119, capability_boundaries 109, instruction_following 29 (kept round-robin across subcategories; priority subcategories such as tool-result flows are never trimmed) |

During development the validator also caught and led me to fix, before the final build: ~430 programming/context examples in which the assistant said
"I ran this…"/"I removed…" (a fabricated-execution phrasing that the dataset must not teach — rewritten as "this returns…"), memory addresses in training text,
and several wrong expectations in my own tests (e.g. a weekend date count, a float rounding case, JS array coercion).

## 8. Duplicate removal statistics

| Type | Removed |
|---|--:|
| Exact duplicate conversation (ignoring system prompt) | 1,000 |
| Same normalised prompt | 734 |
| Near-duplicate (MinHash, Jaccard ≥ 0.8 prompt within a category, or ≥ 0.9 answer with ≥ 0.5 prompt similarity) | 1,221 |
| Identical long answer beyond a cap of two | 441 |
| **Total** | **3,396** |

A large share of the exact/same-prompt duplicates came from my own first-draft generators reusing one conversation under different system prompts;
those generators were replaced by larger slot pools rather than just deduplicated.

## 9. Multi-turn statistics

1,134 multi-turn examples (requirement: ≥ 1,000): 377 two-turn, 511 three-turn, 102 four-turn, 98 five-turn, 46 six-turn. Train 1,062 / validation 37 / test 35.
Types: running arithmetic, list editing, unit follow-ups, changing requirements, corrections, authored reference-resolution dialogs, three-step tool flows
(propose → approve → tool result → grounded report), knowledge dialogs (definition → example → misconception), plan revisions, casual dialogs, NEXUS dialogs.

## 10. Train / validation / test counts

Train 9,556 (91.0%) · validation 472 (4.5%) · test 472 (4.5%). Splits are stratified by category and assigned per `group` (whole families together).
Leakage control then moved 123 would-be held-out examples (62 validation, 61 test) into train because they resembled training items, which is why the
held-out share is slightly under 5%. Audit result: **0** identical prompts and **0** identical long answers across any pair of splits; **0** held-out prompts with
Jaccard ≥ 0.8 to a training prompt. Per-category validation/test counts are small for some categories (as low as 16 for multi_turn_context test), so per-category
test scores will be noisy.

## 11. Validation methodology

Three layers:

1. **At generation (correctness by construction).** Maths and reasoning answers are computed with exact arithmetic or brute-force search (puzzle solutions are checked
   for uniqueness). Code answers were executed: 148 Python functions against their tests; 108 programs in Python/JavaScript/TypeScript/Java/C++/C compiled and run
   with output compared to a reference (108/108 after fixes); 38 output-prediction snippets (JS/Python/Java/C++/C), 10 regexes, 28 SQL queries on SQLite fixtures,
   and 12 git command sequences replayed in a throwaway repository. Generators that failed their own checks were corrected, not skipped.
2. **At build (`validation/checks.py`).** Structure, placeholders, metadata leakage, fabricated-action claims, NEXUS-claim and path checks, tool-awareness and boundary checks.
3. **Independent audit of the final files (`validation/validate_final.py`).** Re-reads the files on disk and re-checks schema, ID uniqueness, duplicates, leakage, distribution,
   multi-turn count, code syntax (566 Python blocks compiled, 177 JSON blocks parsed, 31 JavaScript blocks `node --check`ed — 0 failures), tool-result grounding (130/130 answers reuse
   their tool result), and **independent recomputation from the prompt text**: 216 maths answers (0 disagreements) and 282 reasoning answers (ordering and numeric-pattern puzzles
   re-solved by brute force; 0 disagreements). The remaining ~530 maths and ~1,220 reasoning answers rely on the generator's own computation (not re-derived independently).

Final audit status: **no hard failures.**

## 12. NEXUS-specific validation

- Facts originate in `scripts/nexus_facts.py` (+ `gen_nexus2/3.py`), each tagged implemented / planned / absent, written from `PHASE_1_REPORT.md`, the repository README, `docs/phase-12-voice.md`
  and the code audit (settings defaults such as rate limits, session lifetimes, ML settings and timeouts were taken from the README and the voice document).
- Automated: every assistant sentence in every category is checked for forbidden assertions (`NEXUS uses Ollama`, `NEXUS has a chat endpoint`, `NEXUS can browse the web`, …) and mentions of
  Ollama/Whisper/Qwen/LLM/web search/SQLite/chat endpoint in a NEXUS context must carry a negation or planned marker; API paths must be one of the five real ML routes or `/assistant`. 0 violations in the final set.
- The dataset deliberately encodes the Phase 1 discrepancies: README claims Ollama and web intelligence, but the code has neither; NEXUS has no generative model, no tool layer and no chat UI today.
- Limit: the check is lexical. It would not catch a wrong *number* or a subtly wrong paraphrase of a fact; those were reviewed by reading samples, not by an independent source.

## 13. Tool-awareness validation

- 1,528 examples state "no tools attached", 681 list attached tools; **0** propose a tool call when the system message says none are attached.
- Four behaviours are represented: answer directly (also when tools are attached), say it is unavailable, propose a call and wait, ask for clarification. Tool-mismatch examples (tools attached, but none can do the job) say so.
- 130 answers follow an in-conversation tool result (success, error, empty, permission denied, timeout); all 130 reuse the content of the result they follow, and failures are reported as failures.
- No example claims NEXUS currently has file, terminal, web or database tools; tool names are illustrative and gated by the system message.

## 14. Capability-boundary validation

- 650 examples, each required to contain an explicit limitation/failure statement; families: cannot-confirm (40 claims), no-access (30 things), no-action (30 actions), no-live-data (25), no-memory (20),
  pretend/authority requests (16), knowledge-limit/guarantee requests (18), "did you do X?" and false-memory questions, tool-failure reports, multi-turn pressure to fabricate.
- The validator also rejects any assistant sentence of the form "I saved/sent/ran/checked…" without a tool result in the conversation.
- Wording is varied by slot pools and answer templates; see concerns on templating.

## 15. Dataset version

`qwen_jarvis_dataset_v1` (IDs prefixed `qj1-`). Hashes of all output files are in `dataset/dataset_manifest.json`.

## 16. Exact output files

```text
qwen_jarvis_phase2/README.md · DATASET_SPECIFICATION.md · DATASET_GENERATION_REPORT.md
qwen_jarvis_phase2/dataset/dataset_manifest.json
qwen_jarvis_phase2/dataset/validated/qwen_jarvis_dataset_v1_all.jsonl            10,500
qwen_jarvis_phase2/dataset/splits/train.jsonl · train_messages_only.jsonl         9,556
qwen_jarvis_phase2/dataset/splits/validation.jsonl · validation_messages_only.jsonl   472
qwen_jarvis_phase2/dataset/splits/test.jsonl · test_messages_only.jsonl           472
qwen_jarvis_phase2/dataset/generated/*.jsonl                                      28 raw generator files
qwen_jarvis_phase2/reports/{build_stats,dataset_statistics,final_validation,code_verification_multi}.json · rejected.jsonl
qwen_jarvis_phase2/scripts/ (generators, build.py, run_all.py, make_manifest.py, nexus_facts.py, py_bank*.py, lib.py)
qwen_jarvis_phase2/validation/ (checks.py, validate_final.py)
```

## 17. Git safety result

`git status`: the only new path from this phase is `qwen_jarvis_phase2/` (plus `qwen_jarvis_phase1/` from the previous phase). `git diff`: **0 tracked files modified**.
The 20 pre-existing untracked files (`generate_dataset.py`, `trim_and_validate.py`, `qwen.txt`, `scripts/dedup.py`, `scripts/gen_*.py`, `scripts/validate.py`, `scripts/write_*.py`)
are unchanged: their modification times are still 2026-10-08 and nothing in the repository's `scripts/` is newer than this session. The old
`E:/Nexo/qwen3-agent/phase2/dataset/v2_final/` location was not used. Compiler/test scratch files were written to the session scratch directory, outside the repository.

## 18. Remaining concerns

1. **Under-target categories.** general_conversation is at 63% of its target, nexus_knowledge 83%, programming 89%. The 10,000 total is met only because tool_aware, boundaries,
   knowledge and JARVIS exceed their targets. If balance matters more than the total, add ~370 conversation, ~130 NEXUS and ~165 programming examples.
2. **Templated and programmatic content.** 36% of examples are `computed`, 27% `composed` (authored parts combined with slot pools/framings), 27% `authored`, 10% `verified` code. Lexical-diversity proxy
   (distinct first-70-character prompt skeletons / examples): reasoning 0.10, mathematics 0.27, instruction following 0.34, multi-turn context 0.41 — versus 0.9–1.0 for conversation, knowledge,
   safety, boundaries and programming. Reasoning and maths have verified-correct, varied *instances* but few distinct *phrasings*; the model could over-fit their format. Phase 4 should
   not up-weight these categories, and an evaluation set of hand-written (non-template) prompts should be built — the Phase 10 DeBERTa result (97% on template data, 75% on hand-written) is the cautionary example.
   The test split shares these templates with train (splitting is by example family, not by template), so it measures retention of the format more than generalisation.
3. **Authorship and factual accuracy.** All content was written by an AI model (Claude) in this session, with no internet access and no external fact-checking. General-knowledge fact sheets, health/safety
   guidance and the programming concept answers were written from model knowledge and have **not** been independently verified; they should be spot-checked (especially health, legal, finance and safety content and
   dated claims) before large-scale use. Stylistic homogeneity is likely.
4. **Short, single-register answers.** Mean 76 words per example (95th percentile 150, maximum 241); ~1.08M tokens in total. There is no long-form writing, long code, long context or extended reasoning, and there are no
   Qwen `<think>` traces. This set shapes *behaviour*; it is not enough to preserve or improve broad general ability. Phase 4 should mix it with open-licensed general replay data (the training plan suggests ~35%).
5. **Tool formats are provisional.** Tool results are `user`-role messages and proposed calls are prose; Phase 3 must define a real tool role/call format, after which the ~1,000 tool-related examples need to be re-rendered.
6. **Small held-out slices.** 472 examples each for validation and test; some categories have fewer than 30 test examples.
7. **Safety coverage is everyday-risk only.** Refusals and health/crisis responses are included, but adversarial jailbreak coverage is thin and was intentionally kept free of operational detail.
8. **Phase 1 environment facts** (SQLite vs PostgreSQL, the older untracked dataset scripts) remain unresolved by the user; the dataset says PostgreSQL per the code and treats SQLite as a README/notes mismatch.

## 19. Phase 3 prerequisites

- Decide the tool-call and tool-result wire format (roles, JSON schema, Qwen3 template rendering) and the exact tool manifest, then convert the tool-related examples to it.
- Decide thinking vs non-thinking behaviour for training and serving.
- Decide whether to fill the conversation/NEXUS/programming shortfalls before or after Phase 3, and whether to add a hand-written, template-free evaluation set.
- Approve the permission policy (auto / confirm / deny) the simulated tool traces will assume.
- Review a sample of knowledge, health, safety and NEXUS content for factual accuracy.
- Confirm where the Qwen workspace lives long-term and whether the older untracked dataset scripts in the NEXUS repo should be archived.
- Keep Phase 4's compute plan unchanged: dry run on a small model locally; nothing on Kaggle until the dry run passes.
