# JARVIS Phase 6 Final Report: Qwen3-8B LoRA checkpoint evaluation

**PHASE 6 STATUS: INCOMPLETE — BLOCKERS REMAIN**

Date: 2026-10-11. Workspace: `E:\Nexo\Jarvis\jarvis_phase6_final\`. Model under evaluation: Qwen/Qwen3-8B plus 7 LoRA adapters (steps 800, 1100, 1200, 1300, 1400, 1500, 1596). No other conversational model was used or introduced. Claude Code was the development agent only. No retraining, no Phase 5 resume, no NEXUS integration.

## 1. Executive summary

- **Behavioural evaluation was not executed.** This machine cannot generate with Qwen3-8B: it has a 4 GB RTX 3050, CPU-only torch 2.14.1, no bitsandbytes and no local Qwen3-8B weights. **Zero model generations were run in this task.** Every behavioural metric is **NOT TESTED** for all 8 configurations.
- **The initial 96-response files are not available.** They are not on this machine and not in any Kaggle kernel output. Their reported findings remain **unverified** (`reports/INITIAL_EVAL_REVIEW.md`).
- **Completed and checked:**
  - integrity verification of all 7 adapters: **7/7 PASS**;
  - training-data evidence for the three reported behaviours;
  - an explicit rubric with 16 areas, 23 metrics and acceptance criteria;
  - 54 new deterministic test items: exact-format/P11 family, reasoning/maths, executed coding, identity, multi-step mock-tool workflows;
  - a verified multi-adapter Kaggle runner with adapter-activation proof, plus a scorer and a self-test (all passing);
  - reproduction instructions.
- **Strongest new findings,** both from the frozen training data and both evidence of plausible causes, not proof:
  1. **The model was never trained to call itself JARVIS.**
     - 0 of 5770 system prompts mention JARVIS.
     - The only 2 identity questions are answered "I'm the assistant inside NEXUS".
     - All 88 assistant mentions of "Qwen" are NEXUS-roadmap facts about the Qwen project.
     - Expecting JARVIS self-identity tests an untrained behaviour.
  2. **The training data rewards exactly the two reported P11 failure modes.**
     - **24/24** "JSON only" examples answer inside code fences.
     - **70** assistant turns say "I can't send…"; 49 of these are draft-message templates ending "I can't send it for you".
     - 311 system prompts say "You can only reply with text".
     - Only **1** example is an explicit exact/verbatim-output instruction.
  3. **A scorer defect in the existing evaluation package.** `_extract_json` strips fences, so fenced JSON passes "output only the JSON". This was fixed for Phase 6 with new strict checks; the original file is unchanged.
- **No checkpoint is selected.** There is no behavioural evidence to select one on (§11).

## 2. Actual work completed (all verified)

| Task | Result | Artifact |
|---|---|---|
| Environment / artifact discovery | done; blockers recorded | `PHASE6_PROGRESS.md` |
| Kaggle results search (local + kernel outputs) | not found | `reports/INITIAL_EVAL_REVIEW.md`, `artifacts/kaggle_pull/` |
| Checkpoint integrity (7 × 16 checks) | **all PASS** | `manifests/checkpoint_integrity.json`, `logs/integrity_check.log` |
| Training-data evidence | done | `results/dataset_evidence.json` |
| Rubric | done | `reports/EVALUATION_RUBRIC.md` |
| Phase 6 suites (54 items; every oracle passes its own check) | done | `prompts/phase6_*.jsonl`, `scripts/phase6_suites.py` |
| Strict checks (exact JSON, fences, harmless compliance, identity, executed code) | 19/19 unit assertions pass | `scripts/phase6_checks.py`, `logs/selftest_phase6.log` |
| Multi-adapter runner + scorer, end-to-end with stubs | oracle accepted; refuse-all / generic-hedge / always-call-tool rejected | `scripts/run_phase6_kaggle.py`, `scripts/score_phase6.py` |
| Package scorer self-test (original) | oracle 682/682; adversarial ≤ 1.6 % | `logs/package_eval_selftest.log` |
| Model generations (base + 7 adapters) | **BLOCKED (no GPU / weights)** | — |
| Reload verification | **BLOCKED** | — |

## 3. Methodology

- **Prompts.** The original `pipeline.eval` suites shipped in the training package that produced the checkpoints:
  - instruction_following, multi_turn, nexus_facts, general and testsplit are subsampled to 25 (seed 20261011);
  - tool_awareness (114), safety (59) and capability_boundaries (54) run in full.

  They are supplemented by the 54 Phase 6 items. That gives **406 items and 422 generations per configuration, 3376 in total**.
- **Settings.** The training-format chat template with `enable_thinking=False`, greedy decoding, 384 new tokens and 4-bit NF4. These are identical for every configuration.
- **Base model.** The base runs under `disable_adapter()`. The runner refuses to generate unless three things hold:
  - the bare-base logit fingerprint taken before any adapter is loaded is reproduced under `disable_adapter()`;
  - each adapter gives `active_adapters == [name]` with a distinct fingerprint;
  - the adapter sha256 values are recorded, so they can be matched to `CHECKPOINT_SHA256SUMS.txt`.
- **Determinism.** `phase6_format` runs twice per configuration.
- **Scoring.** Deterministic only, with no LLM judge. Pass/fail per item, Wilson intervals, and paired exact McNemar tests against the base model. Rubric, objective/subjective split and known scorer biases: `reports/EVALUATION_RUBRIC.md`.

## 4. Results and score tables

**NOT TESTED.** `CHECKPOINT_COMPARISON.csv` has 184 rows (8 configurations × 23 metrics). Every row is `status=NOT_TESTED` with its planned n, for example:
- exact format 16
- P11 family 6
- tool selection 91
- tool-call schema 35
- safety 59
- capability honesty 54
- multi-step 8
- failure recovery 14

`scripts/score_phase6.py` fills real values after the Kaggle run. No pass rate in this report comes from a model.

## 5. Base model comparison

Not tested. The planned comparison is the paired per-item McNemar test per metric (`regressions.json`). The initial run's report that the base returned the exact P11 JSON is unverified.

## 6. Checkpoint comparison: metadata only (verified)

| Checkpoint | train_loss | val_loss | val_ppl | is_best (meta) | saved (UTC) | Integrity |
|---|---|---|---|---|---|---|
| 800 | 0.3852 | 0.665891 | 1.94622 | true (segment 1) | 09:29:34 | PASS |
| 1100 | 0.5195 | 0.674362 | 1.96278 | false | 10:19:14 | PASS |
| 1200 | 0.7542 | 0.667345 | 1.94905 | false | 10:35:09 | PASS |
| 1300 | 0.3623 | **0.659943** | 1.93468 | true | 15:02:05 | PASS |
| 1400 | 0.4566 | 0.664875 | 1.94425 | false | 15:19:45 | PASS |
| 1500 | 0.3862 | 0.662790 | 1.94020 | false | 15:37:05 | PASS |
| 1596 | 0.3635 | 0.662652 | 1.93993 | false | 15:55:02 | PASS |

`train_loss` is the loss of the last micro-batch before saving, so it is noisy. Source for all values: `manifests/checkpoint_integrity.json`.

Two checkpoints have `is_best=true` because there are **two run segments**: 800–1200, then a resume from 1200 that produced 1300–1596. The early-stopping state carries across the gap (FINALIZATION_REPORT §4.4). Checkpoint-1200 exists in both Kaggle datasets with byte-identical metadata (the same adapter hash), so it is counted once.

Validation losses span **0.6599–0.6744**: about 2 % relative, on 36 056 tokens. **Lowest validation loss is not used as the selection criterion.**

## 7. Instruction-following regression analysis (P11)

- **Model status:** not retested (no GPU).
- **Retest built:** six P11 wordings, with and without the training-style "text only" system prompt, including the "send" wording, plus 10 other harmless format constraints (`phase6_format`, 16 items, run twice).
- **Evidence:** training data as summarised in §1. Whether the regression is checkpoint-specific, prompt-specific or tied to the system prompt can only be answered from the retest.
- **Ruled out by verification:** adapter-loading mix-ups as a cause in the *new* runner (fingerprint gate). This cannot be ruled out for the initial run.

## 8. Reasoning and coding

- **Not tested on the model.**
- 16 reasoning/maths items with known answers.
- 7 coding items executed against unit tests: empty input, negative input, boundaries, the debug case. Execution goes through an AST allow-list, restricted builtins, `python -I -S`, a temp cwd and a timeout. **This is not an OS sandbox,** so it is meant to run on Kaggle (ephemeral) or with `--no-exec`.
- The self-test proves blocked imports, `open()`, dunder escapes and infinite loops are not credited.

## 9. Tool-call, planning and recovery

- **Not tested on the model.**
- 114 toolproto-1 items plus 8 multi-step mock workflows:
  - search → summarise with sources;
  - search → pick the right file → read → summarise;
  - empty, malformed, error, conflicting and timeout results.
- Tool calls are parsed strictly and validated against `tool_schemas.json`.
- **These are mock tools.** No result can show that real web, filesystem, terminal or NEXUS tools work.

## 10. Safety and honesty

**Not tested on the model.** Planned:
- 59 safety items: 27 harmful, 28 benign look-alikes to measure over-refusal, 4 crisis;
- 54 capability-boundary items, including 24 positive controls;
- fabricated-action guards in every tool and boundary item;
- `harmless_complied` on every format item, separating erroneous refusals from proper safety refusals.

## 11. Checkpoint selection

**No checkpoint is selected, and none can be defended now.** There is no behavioural evidence for any configuration, and the only behavioural reports (the initial 12-prompt run) are unverified. Validation loss favours 1300, but the rubric excludes validation loss as a primary criterion.

- **Best available candidate:** none designated.
- **Fully validated candidate:** none.
- **Production-ready model:** none.

The minimum evidence needed to select is the Kaggle run in `reports/REPRODUCTION.md` §B (8 configurations × 422 generations), then the selection rule in the rubric.

Expect, from the dataset evidence, that **every** checkpoint may fail the strict exact-format criterion and the JARVIS-name identity. If so, the report must say "no checkpoint passes", and the fix is a data change in a future training phase, not a Phase 6 decision.

## 12. Remaining issues and blockers

1. **BLOCKER:** GPU generation for base + 7 adapters (needs Kaggle T4, ~16 GB base download, roughly 8 GPU-hours). This needs your approval and quota.
2. **BLOCKER:** the initial 96-response files must be transferred, or declared lost.
3. **Training-data issues** to raise for a *future* phase (not fixed here):
   - no JARVIS identity target;
   - fenced-only JSON examples;
   - inability templates that may generalise;
   - almost no exact-output instructions.
4. Scorer defect in the original package (fence-tolerant `json_object`). Bypassed with strict checks; still present in the package.
5. `python_exec` isolation is limited (see §8).

## 13. Artifact preservation

- **Originals:**
  - checkpoints are untouched;
  - `CHECKPOINT_SHA256SUMS.txt` passes `sha256sum -c` 35/35;
  - the 55 pre-existing workspace files are byte-identical to the snapshot taken after the resume test.
- **The deliverable contains only evaluation metadata, scripts and prompts.** There is no adapter copy (none selected), no tokenizer files and no base weights.
- **Base model retrieval:** Hugging Face Hub `Qwen/Qwen3-8B`. Local config/tokenizer snapshot: `b968826d9c46dd6066d109eabc6255188de91218`.
- **The evaluated adapters** are referenced immutably by sha256 in `EVALUATION_MANIFEST.json`.
- **No uploads were made.**

## 14. Reload verification

**BLOCKED.** The real-weights reload needs the GPU and base model above. There is earlier partial evidence for checkpoint-1596 only, from `_resume_test/RESUME_VALIDATION_REPORT.md`:
- the adapter fits the Qwen3-8B architecture on a meta-device skeleton;
- the training state loads with `weights_only=True`.

This is labelled **PARTIAL** and is not reload-passed.

## 15. Final readiness decision

**NOT READY.** No configuration has been behaviourally evaluated, and no checkpoint is selected or validated.

**Next permitted step:** stay in Phase 6 and run REPRODUCTION §B on Kaggle; then score, review and select. Phase 7 and the NEXUS integration phases (11–14) must not start until Phase 6 is COMPLETE.

## 16. Deliverables

| Item | Path |
|---|---|
| A. Final report | `E:\Nexo\Jarvis\jarvis_phase6_final\PHASE6_FINAL_REPORT.md` |
| B. Comparison (long form, NOT_TESTED) | `…\CHECKPOINT_COMPARISON.csv` |
| C. Manifest | `…\EVALUATION_MANIFEST.json` |
| D. Progress / resume | `…\PHASE6_PROGRESS.md` |
| E. Scripts and prompts | `…\scripts\`, `…\prompts\` |
| F. Adapter reference | `EVALUATION_MANIFEST.json` → `checkpoints.adapter_sha256` (originals in `E:\Nexo\Jarvis\checkpoint-*`) |
| G. Failed tests / known regressions | `…\results\failed_tests_and_known_regressions.json` |
| Rubric, initial-run review, reproduction | `…\reports\` |
| Integrity manifest | `…\manifests\checkpoint_integrity.json` |
