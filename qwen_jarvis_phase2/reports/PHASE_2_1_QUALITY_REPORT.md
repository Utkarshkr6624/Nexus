# Phase 2.1 Quality Report - dataset `qwen_jarvis_dataset_v2`

Correction pass over the isolated Qwen/JARVIS dataset. The NEXUS application was not touched; all changes are inside `qwen_jarvis_phase2/`. The Phase 2 dataset is preserved untouched in `baseline_v1/`.

## 1. Dataset size

| | count |
|---|--:|
| Previous count (v1) | 10,500 |
| New candidates written in Phase 2.1 | 3,313 |
| New candidates that survived into v2 | 2,983 |
| New candidates rejected / removed | 330 |
| v1 examples kept unchanged in v2 | 8,239 |
| v1 examples removed in v2 (templated / duplicate / balance) | 2,261 |
| **Final count (v2)** | **11,332** |

New candidates by generator file:

| file | candidates |
|---|--:|
| conv21_a | 68 |
| conv21_b | 52 |
| conv21_c | 86 |
| conv21_d | 77 |
| conv21_e | 75 |
| conv21_f | 47 |
| conv21_g | 87 |
| conv21_h | 41 |
| instruction21 | 497 |
| math21 | 450 |
| multiturn21 | 151 |
| nexus21 | 349 |
| prog21_algo | 135 |
| prog21_errors | 48 |
| prog21_py | 59 |
| prog21_sql | 33 |
| prog21_web | 176 |
| reasoning21 | 882 |

Why new candidates were removed (by category):

| category | candidates | kept | removed | reasons |
|---|--:|--:|--:|---|
| general_conversation | 533 | 529 | 4 | {'same_prompt_normalised': 4} |
| reasoning | 882 | 822 | 60 | {'identical_answer_cap': 16, 'near_duplicate': 31, 'same_prompt_normalised': 12, 'exact_duplicate': 1} |
| programming | 451 | 448 | 3 | {'same_prompt_normalised': 3} |
| mathematics | 444 | 391 | 53 | {'near_duplicate': 12, 'balance_cap': 40, 'same_prompt_normalised': 1} |
| instruction_following | 497 | 320 | 177 | {'balance_cap': 169, 'near_duplicate': 3, 'exact_duplicate': 1, 'same_prompt_normalised': 4} |
| nexus_knowledge | 349 | 322 | 27 | {'same_prompt_normalised': 1, 'near_duplicate': 4, 'identical_answer_cap': 22} |
| multi_turn_context | 151 | 151 | 0 | - |

## 2. Category balance

| category | old (v1) | new (v2) | target (minimum) | pass |
|---|--:|--:|--:|:-:|
| general_conversation | 630 | 1090 | 1000 | PASS |
| general_knowledge | 1174 | 1173 | 1000 | PASS |
| reasoning | 1500 | 1500 | 1500 | PASS |
| programming | 1335 | 1500 | 1500 | PASS |
| mathematics | 750 | 750 | 750 | PASS |
| instruction_following | 750 | 750 | 750 | PASS |
| jarvis_assistant | 1119 | 1000 | 1000 | PASS |
| nexus_knowledge | 622 | 944 | 750 | PASS |
| tool_aware | 975 | 975 | 750 | PASS |
| capability_boundaries | 650 | 650 | 500 | PASS |
| multi_turn_context | 476 | 500 | 500 | PASS |
| safety | 519 | 500 | 500 | PASS |
| **total** | 10500 | 11332 | 10,000 | PASS |

Largest category share: 13.2% (v1: 14.3%).

## 3. Diversity

- Exact duplicates removed in the v2 build: 1008 (v1 build: 1000). These are overlaps between the regenerated generator outputs, not duplicates in the final files.
- Same-prompt (normalised) duplicates removed: 768
- Near-duplicates removed (MinHash/Jaccard >= 0.8 on prompts within a category, or near-identical answers): 1267
- Identical-answer cap (more than 2 copies of one long answer): 479
- **Templated examples removed** by the new repetition trim (skeleton clusters > 12, or the same sub-category and 4-word answer opener > 22): **1944** - {'mathematics': 230, 'programming': 283, 'reasoning': 940, 'multi_turn_context': 127, 'jarvis_assistant': 119, 'general_conversation': 65, 'capability_boundaries': 100, 'tool_aware': 52, 'safety': 19, 'instruction_following': 9}
- Balance-cap drops: {'capability_boundaries': 9, 'instruction_following': 509, 'tool_aware': 216, 'mathematics': 371}
- Duplicates / near-duplicates remaining in the final files (independent audit): exact-duplicate conversations 0, duplicate prompts 0

Diversity metrics (higher is more varied; `prompt skeleton ratio` = distinct masked prompt shapes / examples; `answer skeleton ratio` likewise for answers; `top opener` = share of the single most common 4-word assistant opener):

| category | n v1 -> v2 | prompt skeleton v1 -> v2 | answer skeleton v1 -> v2 | top opener v1 -> v2 |
|---|--:|--:|--:|--:|
| general_conversation | 630 -> 1090 | 0.983 -> 0.986 | 0.819 -> 0.913 | 0.125 -> 0.02 |
| general_knowledge | 1174 -> 1173 | 0.878 -> 0.878 | 0.711 -> 0.712 | 0.006 -> 0.006 |
| reasoning | 1500 -> 1500 | 0.339 -> 0.599 | 0.079 -> 0.389 | 0.153 -> 0.089 |
| programming | 1335 -> 1500 | 0.543 -> 0.765 | 0.801 -> 0.878 | 0.111 -> 0.023 |
| mathematics | 750 -> 750 | 0.141 -> 0.429 | 0.141 -> 0.432 | 0.071 -> 0.029 |
| instruction_following | 750 -> 750 | 0.427 -> 0.671 | 0.332 -> 0.479 | 0.041 -> 0.019 |
| jarvis_assistant | 1119 -> 1000 | 0.944 -> 0.938 | 0.594 -> 0.643 | 0.098 -> 0.097 |
| nexus_knowledge | 622 -> 944 | 0.868 -> 0.899 | 0.73 -> 0.788 | 0.029 -> 0.019 |
| tool_aware | 975 -> 975 | 0.834 -> 0.854 | 0.483 -> 0.487 | 0.066 -> 0.065 |
| capability_boundaries | 650 -> 650 | 0.957 -> 0.963 | 0.694 -> 0.718 | 0.052 -> 0.065 |
| multi_turn_context | 476 -> 500 | 0.464 -> 0.69 | 0.429 -> 0.678 | 0.273 -> 0.07 |
| safety | 519 -> 500 | 0.992 -> 0.992 | 0.763 -> 0.762 | 0.066 -> 0.068 |
| **overall** | 10500 -> 11332 | 0.673 -> 0.798 | 0.527 -> 0.658 | - |

Distinct 4-word assistant openers overall: 3784 -> 5770; distinct 3-word user openers: 2561 -> 3718.

Source mix (v1 -> v2): authored 2809 -> 4029, verified 1055 -> 1044, computed 3760 -> 3393, composed 2876 -> 2866.

## 4. Multi-turn

- Multi-turn examples (>= 2 user turns): v1 1134 -> **v2 1465** (requirement: >= 1,000).
- With substantive assistant content (>= 40 assistant words in total): 1155.
- By number of user turns: {2: 645, 3: 558, 4: 174, 5: 77, 6: 11}.
- `multi_turn_context` category: v1 476 -> v2 500 (minimum 500).

## 5. Code

- Code blocks checked in the final files by the independent audit: {"python": [440, 0], "json": [106, 0], "javascript": [55, 0], "sql_syntax": [52, 0], "c_cpp_java_full_programs_compiled": [23, 0], "bad_examples": []}
- Failing code blocks: 0
- Phase 2.1 additions executed for real: compile/runtime error explanations (Python, JavaScript, C, C++, Java, TypeScript) quote genuine compiler/interpreter output and the corrected program was run and its output asserted; SQL queries ran on SQLite and show the real result table; Python/JS/TS snippets ran and the quoted output is the real output. Candidates whose code failed verification were dropped, not edited to fit.

## 6. Mathematics

- Maths answers recomputed from the prompt text by the independent audit: {"checked": 107, "agree": 107, "disagree": 0, "not_checked": 643}
- Reasoning answers recomputed: {"checked": 46, "agree": 46, "disagree": 0, "not_checked": 1454}
- Disagreements: maths 0, reasoning 0

## 7. NEXUS

- NEXUS knowledge examples in v2: **944** (v1: 622); new candidates written: 349; new kept: 322; rejected by the NEXUS-claim validator: 0.
- Verification: every NEXUS-specific statement was written against `scripts/nexus_facts.py` (derived from `qwen_jarvis_phase1/PHASE_1_REPORT.md`) and passes `validation/checks.py::nexus_claims` (forbidden-claim list, unknown-path list, entity-without-negation/planned-marker rule). All final NEXUS examples pass; none claims Qwen, Ollama, a chat endpoint, web/file/terminal tools or a tool registry exists today.
- Final-file NEXUS claim failures (independent audit): {} (empty = none)

## 8. Leakage

- Split sizes: {'train': 10296, 'test': 518, 'validation': 518}
- Identical prompts across splits: {'train_vs_validation_identical_prompts': 0, 'train_vs_test_identical_prompts': 0, 'validation_vs_test_identical_prompts': 0}
- Full leakage block from the audit: `{"train_vs_validation_identical_prompts": 0, "train_vs_validation_identical_long_answers": 0, "train_vs_test_identical_prompts": 0, "train_vs_test_identical_long_answers": 0, "validation_vs_test_identical_prompts": 0, "validation_vs_test_identical_long_answers": 0, "validation_near_duplicate_prompts_vs_train": 0, "test_near_duplicate_prompts_vs_train": 0}`
- Held-out examples moved into train to remove prompt/answer similarity: {'test': 50, 'validation': 58}

## 9. Git safety

- Tracked-file modifications (`git status --porcelain`, lines not starting with `??`): **0**
- `git diff --stat`: empty (no tracked file differs from HEAD)
- New untracked paths from this work: ['qwen_jarvis_phase1/', 'qwen_jarvis_phase2/']
- Other untracked files (all pre-existing, untouched): 20
- Existing NEXUS files changed: 0. No model downloaded, no training, no Kaggle use, no dependency installed.

## 10. Validation summary

- Independent audit hard failures: []
- Category minimums met: True
