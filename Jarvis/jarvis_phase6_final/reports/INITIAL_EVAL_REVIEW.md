# Review of the initial 96-response Phase 6 run: BLOCKED

## Availability

None of the four expected files are available in this environment:
- `phase6_raw_results.json`
- `phase6_raw_results.csv`
- `phase6_manifest.json`
- `phase6_initial_scores.csv`

They were expected at `/kaggle/working/jarvis_phase6_evaluation/`. Where I looked:
- **Local search** (depth 6) of `E:\`, `Downloads`, `Documents` and `Desktop`: no `phase6_*` or `*jarvis_phase6*` files.
- **Kaggle kernel outputs:** `kaggle kernels files` returned an empty list for every kernel of account `utkarsh6624`, including the two newest notebooks.
- **The two newest notebooks** were pulled into `artifacts/kaggle_pull/`. Both are Phase 5 training notebooks with no saved outputs and no Phase 6 evaluation code. The run was most likely an interactive session whose `/kaggle/working` was not committed.

So, of the steps requested for this review:
- **Not done:** record counts, duplicate and missing records, error checks, per-probe coverage and the response inspection. All are **BLOCKED**.
- **Not reproduced:** the 12 prompt texts and the heuristic scorer behind them are not available.

**To unblock:** if the session or a commit still exists, copy the four files unchanged into `results/initial_96/`. The review can then be run as a pure Python analysis.

## What the initial run could and could not measure (from its description)

- **Size.** 12 prompts × 8 configurations. One generation per cell, so a single sampling or formatting quirk decides each result. There are no repeats, no confidence intervals, and a difference of 1–3 raw points between configurations is within noise.
- **Objectively verifiable** from the reported prompts:
  - ₹80 change;
  - "does not follow" for the roses syllogism;
  - 12:50 PM;
  - the subtraction-to-addition bug fix;
  - recall of "Orion / Python";
  - the exact P11 JSON string.

  All of these now exist as deterministic items in `prompts/phase6_reasoning.jsonl`, `phase6_coding.jsonl` and `phase6_format.jsonl`. Context recall is covered by the original `multi_turn` suite.
- **Needed human judgement:** introductions and statements about limitations, beyond whether a name was used.
- **Likely scoring errors** in a keyword heuristic:
  - credit for an expected keyword appearing inside a wrong or hedged answer (false positive);
  - fenced JSON accepted as exact JSON, as the original package's `json_object` check does (false positive; verified in code);
  - correct answers in different wording failing (false negative);
  - identity scored against a JARVIS name the training data never taught (see the rubric, note 1).
- **Not measured at all:** tool-call behaviour, multi-step workflows, failure recovery, safety, over-refusal, coding by execution, and determinism.

## Reported findings and their status

These are the reported findings. All are **unverified**, because no responses are available. Each now has a dedicated retest in the Phase 6 suites, and each has dataset evidence recorded in `results/failed_tests_and_known_regressions.json`.

| Reported finding | Status | Dataset evidence (frozen v2_final train split, 10 298 conversations) |
|---|---|---|
| 800 and 1100 introduce themselves as JARVIS; 1200–1596 as Qwen | UNVERIFIED | 0 system prompts mention JARVIS. The 2 identity questions in training are answered "the assistant inside NEXUS". All 88 "Qwen" mentions are roadmap facts. |
| P11: 800, 1100, 1500, 1596 fence the JSON | UNVERIFIED | 24/24 "JSON only" training answers are fenced |
| P11: 1200, 1300, 1400 refuse ("cannot send" / "can only write") | UNVERIFIED | 70 assistant turns contain "I can't send" (49 draft-message templates ending "I can't send it for you"); 311 system prompts say "You can only reply with text"; only 1 exact-output instruction |
| Heuristic points 16 / 15 / 15 / 13 / 14 / 14 / 14 / 14 | Not used | An unknown rubric applied to 12 prompts. Not a ranking. |

The dataset counts make the reported behaviour **plausible**, but they do not prove the cause. A causal claim would need the retest results, ideally with an ablation, which needs retraining and is outside Phase 6.
