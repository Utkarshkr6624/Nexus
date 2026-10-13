# Phase 3 Part A - Dataset preflight report

Baseline: `qwen_jarvis_dataset_v2` (11,332 examples, `qwen_jarvis_phase2/dataset/splits`, read-only).
Output: `dataset/v2_clean/` (11,262 examples) -> tool-protocol conversion -> `dataset/v2_final/` (11,288 examples, FROZEN; re-frozen once after the independent audit, see 'Post-audit corrections').
Nothing was regenerated. Every change is a declared rule or a per-record edit in `apply_patches.py`, logged to `changelog.jsonl` (id, operation, reason).
Method note: this was a targeted audit by keyword triage plus full reading of every flagged item; it was **not** an external fact-check of all 11k examples.

## A1 Mathematics
* Prompt-shape clusters: 322 distinct shapes in 750 examples; only three clusters exceeded 12 members (23, 19, 16). Capped at 12 each -> **22 removed** (750 -> 728).
  Smaller clusters (<=12) are different parameterisations of legitimate problem families (compound interest, ratios, percent change) and were kept: they teach the method, and their answers are computed exactly.
* Correctness: `arith_check.py` re-parses every `a op b = c` statement in all assistant text (outside code) with a real expression parser and compares exactly (with rounding/percent handling):
  **1,637 equations checked, 64 flagged; all 64 read by hand: 0 genuine errors** (they are parser artefacts such as `2 x (60+24+40) = 248` losing the prefix, or algebraic identities, not arithmetic).
  Spot derivations done by hand included the chain-rule, cylinder-to-spheres, birthday-problem and remainder-theorem examples. The Phase 2.1 independent audit (240 maths + 351 reasoning answers recomputed, 0 disagreements) stands.

## A2 Reasoning
* 741 distinct prompt shapes in 1,500 examples; two clusters exceeded 12 (23, 18) -> capped at 12 -> **17 removed** (1,500 -> 1,483). Puzzle families (liars, arrangement, river crossing, deadline scheduling) are generated with brute-force-verified unique solutions and were kept.
* Hard/diverse items were preferred when choosing which cluster members to keep.

## A3 Knowledge / tool / safety / capability-boundary categories
* Read in full every item the risk triage flagged in these categories (see A5) plus a random sample of 36 unflagged items (9 per category). Concrete defects found and fixed:
  garbled safety templates (`"...otherwise talk to emergency care for allergic signs, otherwise a pharmacist"`), ungrammatical slot-filled prompts (`"Is my child has a fever..."`), `"Help me help me"`, `"Could you what's..."`,
  lowercase `i` in template answers, an answer/question mismatch pair (bleach vs baking-soda), a landlord-worded answer to a generic debt prompt. No categories were regenerated.

## A4 Categories at minimum
* Not inflated. Final counts (v2_final): general_conversation 1,090 - general_knowledge 1,173 - reasoning 1,483 - programming 1,500 - mathematics 728 - instruction_following 750 - jarvis_assistant 985 -
  nexus_knowledge 944 - tool_aware 1,002 - capability_boundaries 649 - multi_turn_context 492 - safety 492. Six categories sit slightly below their Phase-2 nominal minimum (reasoning -17, mathematics -22,
  jarvis -15, multi_turn -8, safety -8, boundaries -1) because redundant/incoherent items were removed; the total (11,288) and multi-turn (1,413) remain well above the 10,000 / 1,000 floors. This is the stated priority: quality over category inflation.

## A5/A6 Factual-risk audit (health, medical, legal, financial, security, certainty claims, model/tool claims)
* Triage (`triage_risk.py`): 1,387 of 11,332 examples matched a risk pattern; the 982 flagged in prose categories (consequential-advice classes) were **read in full** (answers up to 600 characters shown).
* Findings and actions: no dangerous instruction, dosage or diagnosis found. The safety examples consistently decline diagnosis, refuse to give paediatric doses (defer to label/pharmacist), give emergency guidance, include crisis resources, and treat scams/privacy/legal requests safely.
  Fixed: Iceland March daylight (11-12 h -> 10 to 13+ h), unhedged "honey in tombs", "progressive tax never lowers take-home pay" (softened, benefit cliffs), "turbulence is not dangerous" (seatbelt caveat),
  absolute "toothache will not go away on its own" (3+ records), the bleach/baking-soda answer swap (2 records).
* Remaining limitation: general-knowledge answers are model-written and were only checked where flagged; they have not been externally fact-checked.

## A7 NEXUS factual audit (mandatory)
* Sources of truth used: `qwen_jarvis_phase1/*.md`, `qwen_jarvis_phase2/scripts/nexus_facts.py`, **and the NEXUS source itself, read-only** (config, router, env example), because several details quoted in the data are not in the Phase 1 documents.
* Identifier audit (`nexus_audit.py`): every ML_* setting, path, snake_case identifier and number in the 944 NEXUS answers checked against docs and source: 85 distinct tokens, 0 unverified.
* Read all 322 newly authored NEXUS examples in full plus 14 configuration facts verified in code (ROUTING_TIMEOUT_MS = 30,000; ML_* defaults incl. threshold 0.90 and input limit 2,000; rate limits 600/120 per 60 s; sessions 20/30 days;
  planner 8-20 h and 15-240 min; productivity weights 30/25/20/25; reset token 30 min; audit retention 400 days declared-not-enforced; recognition timeout 15 s; test-file counts 103/52; 14 migrations; torch pin).
* **One genuine factual error found and fixed**: four examples said "twelve of the fourteen intents map to a service"; the router (`SERVICE_TARGETS`) has **eleven** (out_of_scope gets a list of surfaces; code_assist and deep_reasoning have no destination). Four unverifiable details ("running a risk evaluation is an idempotent POST") were removed.
* Current-vs-planned: no example presents Qwen, Ollama, a chat endpoint/UI, a tool registry, or web/file/terminal tools as existing; the 3 non-NEXUS-category examples that mention NEXUS are correct. NEXUS-claim checks: 0 failures on the final set.

## A8 JavaScript timing examples
* Scanned 25 examples touching `Date.now`, `performance.now`, `setTimeout/Interval`, `timeit`, `perf_counter`. Wall-clock-dependent ones were **rewritten and re-executed**: TTL cache (injected clock), sequential vs parallel awaits
  (peak-concurrency counter), busy-loop blocking (fixed-iteration loop), list-vs-set membership (comparison counting instead of `timeit`). Microtask/timer *ordering* examples (deterministic) were kept.
  The quoted outputs are the real outputs of the new code (`patch_js_timing.py`).

## A9 Wording slip
* `qj2-555e27377677` (writing coach): the clumsy last turn was rewritten to "Keeping your phrase "very tired", here is a more dramatic version: ..."; the surrounding turns were checked for consistency.

## Other defects found by the scan (fixed by rule, all logged)
* 143 jarvis answers with an ungrammatical task phrase; 50 lowercase pronouns in templates; 24 pronoun slips ("To help you reduce my ..."); 8 `the the`; 15 ungrammatical user prompts; 13 garbled safety templates;
  15 semantically incoherent slot-filled drafts deleted (resignation/interview/reference about a "vendor contract"); 8 deleted ungrammatical prompts; the repeated "What's my name again?" dialogue reduced from 12 copies to 4.

## Totals
| step | records |
|---|--:|
| v2 baseline | 11,332 |
| after preflight (v2_clean) | 11,262 (70 deleted, 294 edited, 4 rewritten, 1 moved val->train) |
| after tool-protocol conversion + 32 canonical protocol examples + exact-duplicate removal + audit leakage fix (v2_final) | **11,288** (train 10,298 / validation 497 / test 493) |

Validation of v2_final (`validate_clean.py`): 0 per-record content failures, 0 exact duplicates, 0 duplicate prompts, 0 train/validation/test leakage under the broadened definition (identical or near-identical prompts, identical long answers, **any** assistant turn of >= 100 characters shared with train, shared first user prompts), all code blocks valid
(440 Python, 106 JSON, 56 JavaScript, 52 SQL syntax, 23 full C/C++/Java programs compiled). `validate_protocol.py --render`: 0 violations over 11,288 records, 705 tool-bearing records rendered through the real Qwen3 chat template.

## Post-audit corrections (independent auditor, `audit/FINAL_AUDIT.md`)
* **Leakage claim was too narrow (audit finding 4).** The original check compared only the *last* assistant answer, so 20 held-out records whose *earlier or paraphrased-prompt* answers were identical to train answers, and 20 that shared a first user prompt with train, slipped through. The freeze step now moves a held-out record to train when **any** assistant turn >= 100 characters (normalised) appears in train, or its first user prompt does, and also resolves such overlaps between validation and test. 41 records were moved in the final freeze (validation 516 -> 497, test 515 -> 493). `validate_clean.py` now reports and fails on these measures; all are 0.
* **Content defects (audit finding 10)**, fixed by rule and logged: 9 reasoning answers claiming "a search of n = 1 to 2000 finds no counterexample" (an unperformed action) reworded; 42 JARVIS answers whose numbered list started at "2." renumbered; 7 prompts "What is how ..." (plus 4 lowercase 'what is how' variants found by the re-audit) repaired; one odd generated search query; 22 redundant "so I can't access it" clauses removed.
* Remaining known content limitations: time-sensitive statements about the project state ("no Qwen weights have been downloaded, no training has run") are true today and will be false after Phase 5; they should be removed or regenerated in a later dataset version.
