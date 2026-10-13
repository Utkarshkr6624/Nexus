# STUB — NOT A MODEL RESULT

> These numbers come from a test stub that only exercises the evaluation code. They say nothing about any model.

# Evaluation report

## Run metadata

- **model_kind**: `stub`
- **model_id**: `stub:refuse_all`
- **dataset_version**: `qwen_jarvis_dataset_v2_final`
- **suite_versions**: `{"instruction_following": "716bafddc2905647", "multi_turn": "d30f4436148cb870", "nexus_facts": "bbe73d330e8b6a56", "tool_awareness": "62f4c4e732b1dec6", "capability_boundaries": "f21648ab3649155d", "safety": "5a6ef0ff68a71c69", "general": "081f7184c31664b5", "testsplit": "3b365e1e3624d46d"}`
- **tool_schemas_sha256**: `0d6a24986f45b4f2964303cdf2d7bea50a39ba8a93851e04a5d85186fc80ec35`
- **seed**: `0`
- **decoding**: `{"do_sample": false, "num_beams": 1, "enable_thinking": false, "note": "stub - no decoding"}`
- **hardware**: `{"platform": "Windows-11-10.0.26300-SP0", "python": "3.13.14", "note": "GPU/VRAM are recorded by the Phase 5 notebook, not here"}`
- **libraries**: `{"torch": "2.14.1+cpu", "transformers": "5.19.0", "peft": "0.21.2", "trl": "1.15.0", "accelerate": "1.15.0", "tokenizers": "0.23.2"}`

- **generated (UTC)**: 2026-10-09T12:58:57Z

## Overall

Scored items: 666 of 723. Pass rate 1.5% (10/666) [1–3] (95% Wilson CI in brackets, percent).

## By suite

| suite | pass rate [95% CI] | n |
|---|---|--:|
| capability_boundaries | 0.0% (0/30) [0–11] | 30 |
| general | 0.0% (0/113) [0–3] | 113 |
| instruction_following | 9.7% (6/62) [5–20] | 62 |
| multi_turn | 5.0% (2/40) [1–16] | 40 |
| nexus_facts | 0.0% (0/128) [0–3] | 128 |
| safety | 0.0% (0/59) [0–6] | 59 |
| testsplit | 0.8% (1/120) [0–5] | 120 |
| tool_awareness | 0.9% (1/114) [0–5] | 114 |

## By category

| category | pass rate [95% CI] | n |
|---|---|--:|
| capability_boundaries/no_access | 0.0% (0/30) [0–11] | 30 |
| general/code | 0.0% (0/12) [0–24] | 12 |
| general/math | 0.0% (0/60) [0–6] | 60 |
| general/qa | 0.0% (0/41) [0–9] | 41 |
| instruction_following/classification | 0.0% (0/3) [0–56] | 3 |
| instruction_following/conflict | 0.0% (0/2) [0–66] | 2 |
| instruction_following/constraint | 16.2% (6/37) [8–31] | 37 |
| instruction_following/counting | 0.0% (0/2) [0–66] | 2 |
| instruction_following/extraction | 0.0% (0/3) [0–56] | 3 |
| instruction_following/list_ops | 0.0% (0/3) [0–56] | 3 |
| instruction_following/multi_constraint | 0.0% (0/4) [0–49] | 4 |
| instruction_following/reformat | 0.0% (0/2) [0–66] | 2 |
| instruction_following/system_rule | 0.0% (0/6) [0–39] | 6 |
| multi_turn/context | 5.0% (2/40) [1–16] | 40 |
| nexus_facts/absent | 0.0% (0/16) [0–19] | 16 |
| nexus_facts/current | 0.0% (0/68) [0–5] | 68 |
| nexus_facts/false_premise | 0.0% (0/18) [0–18] | 18 |
| nexus_facts/planned | 0.0% (0/26) [0–13] | 26 |
| safety/benign_lookalike | 0.0% (0/28) [0–12] | 28 |
| safety/crisis | 0.0% (0/4) [0–49] | 4 |
| safety/harmful | 0.0% (0/27) [0–12] | 27 |
| testsplit/capability_boundaries | 0.0% (0/23) [0–14] | 23 |
| testsplit/mathematics | 0.0% (0/23) [0–14] | 23 |
| testsplit/nexus_knowledge | 0.0% (0/41) [0–9] | 41 |
| testsplit/safety | 0.0% (0/10) [0–28] | 10 |
| testsplit/tool_aware | 4.3% (1/23) [1–21] | 23 |
| tool_awareness/after_error | 0.0% (0/10) [0–28] | 10 |
| tool_awareness/after_result | 0.0% (0/13) [0–23] | 13 |
| tool_awareness/ambiguous | 0.0% (0/6) [0–39] | 6 |
| tool_awareness/no_tools_attached | 0.0% (0/16) [0–19] | 16 |
| tool_awareness/not_needed | 7.1% (1/14) [1–31] | 14 |
| tool_awareness/read_call | 0.0% (0/24) [0–14] | 24 |
| tool_awareness/tool_missing | 0.0% (0/8) [0–32] | 8 |
| tool_awareness/write_after_approval | 0.0% (0/10) [0–28] | 10 |
| tool_awareness/write_preview | 0.0% (0/13) [0–23] | 13 |

## Derived behaviour rates

| metric | rate [95% CI] | n |
|---|---|--:|
| harmful_refusal_rate | 100.0% (27/27) [88–100] | 27 |
| over_refusal_rate | 100.0% (28/28) [88–100] | 28 |
| false_tool_call_rate | 0.0% (0/80) [0–5] | 80 |
| valid_tool_call_rate_when_needed | 0.0% (0/34) [0–10] | 34 |
| any_tool_markup_rate_when_needed | 0.0% (0/34) [0–10] | 34 |

## Informational

- mean lexical F1 vs reference (never pass/fail): 0.0864 over 177 items
- output hygiene counts: {'empty': 0, 'template_token_leak': 0, 'think_block': 0}

## Caveats

- Pass rates come from small, hand-written, deterministic probe sets (n is shown for every row); they are behavioural probes, not benchmarks.
- Wilson 95% intervals are shown; with n < 50 they are wide, so differences smaller than the interval should not be over-interpreted.
- No multiple-comparison correction is applied across rows.
- Lexical F1 against reference answers is informational only and never used for pass/fail.
- Perplexity/loss is meaningful only as assistant-token loss on in-distribution data (this dataset's own validation/test splits); it is not comparable across tokenizers or data.
