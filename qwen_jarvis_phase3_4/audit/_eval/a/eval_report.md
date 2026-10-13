# STUB — NOT A MODEL RESULT

> These numbers come from a test stub that only exercises the evaluation code. They say nothing about any model.

# Evaluation report

## Run metadata

- **model_kind**: `stub`
- **model_id**: `stub:oracle`
- **dataset_version**: `qwen_jarvis_dataset_v2_final`
- **suite_versions**: `{"instruction_following": "716bafddc2905647", "multi_turn": "d30f4436148cb870", "nexus_facts": "bbe73d330e8b6a56", "tool_awareness": "62f4c4e732b1dec6", "capability_boundaries": "f21648ab3649155d", "safety": "5a6ef0ff68a71c69", "general": "081f7184c31664b5", "testsplit": "3b365e1e3624d46d"}`
- **tool_schemas_sha256**: `0d6a24986f45b4f2964303cdf2d7bea50a39ba8a93851e04a5d85186fc80ec35`
- **seed**: `0`
- **decoding**: `{"do_sample": false, "num_beams": 1, "enable_thinking": false, "note": "stub - no decoding"}`
- **hardware**: `{"platform": "Windows-11-10.0.26300-SP0", "python": "3.13.14", "note": "GPU/VRAM are recorded by the Phase 5 notebook, not here"}`
- **libraries**: `{"torch": "2.14.1+cpu", "transformers": "5.19.0", "peft": "0.21.2", "trl": "1.15.0", "accelerate": "1.15.0", "tokenizers": "0.23.2"}`

- **generated (UTC)**: 2026-10-09T12:58:57Z

## Overall

Scored items: 666 of 723. Pass rate 100.0% (666/666) [99–100] (95% Wilson CI in brackets, percent).

## By suite

| suite | pass rate [95% CI] | n |
|---|---|--:|
| capability_boundaries | 100.0% (30/30) [89–100] | 30 |
| general | 100.0% (113/113) [97–100] | 113 |
| instruction_following | 100.0% (62/62) [94–100] | 62 |
| multi_turn | 100.0% (40/40) [91–100] | 40 |
| nexus_facts | 100.0% (128/128) [97–100] | 128 |
| safety | 100.0% (59/59) [94–100] | 59 |
| testsplit | 100.0% (120/120) [97–100] | 120 |
| tool_awareness | 100.0% (114/114) [97–100] | 114 |

## By category

| category | pass rate [95% CI] | n |
|---|---|--:|
| capability_boundaries/no_access | 100.0% (30/30) [89–100] | 30 |
| general/code | 100.0% (12/12) [76–100] | 12 |
| general/math | 100.0% (60/60) [94–100] | 60 |
| general/qa | 100.0% (41/41) [91–100] | 41 |
| instruction_following/classification | 100.0% (3/3) [44–100] | 3 |
| instruction_following/conflict | 100.0% (2/2) [34–100] | 2 |
| instruction_following/constraint | 100.0% (37/37) [91–100] | 37 |
| instruction_following/counting | 100.0% (2/2) [34–100] | 2 |
| instruction_following/extraction | 100.0% (3/3) [44–100] | 3 |
| instruction_following/list_ops | 100.0% (3/3) [44–100] | 3 |
| instruction_following/multi_constraint | 100.0% (4/4) [51–100] | 4 |
| instruction_following/reformat | 100.0% (2/2) [34–100] | 2 |
| instruction_following/system_rule | 100.0% (6/6) [61–100] | 6 |
| multi_turn/context | 100.0% (40/40) [91–100] | 40 |
| nexus_facts/absent | 100.0% (16/16) [81–100] | 16 |
| nexus_facts/current | 100.0% (68/68) [95–100] | 68 |
| nexus_facts/false_premise | 100.0% (18/18) [82–100] | 18 |
| nexus_facts/planned | 100.0% (26/26) [87–100] | 26 |
| safety/benign_lookalike | 100.0% (28/28) [88–100] | 28 |
| safety/crisis | 100.0% (4/4) [51–100] | 4 |
| safety/harmful | 100.0% (27/27) [88–100] | 27 |
| testsplit/capability_boundaries | 100.0% (23/23) [86–100] | 23 |
| testsplit/mathematics | 100.0% (23/23) [86–100] | 23 |
| testsplit/nexus_knowledge | 100.0% (41/41) [91–100] | 41 |
| testsplit/safety | 100.0% (10/10) [72–100] | 10 |
| testsplit/tool_aware | 100.0% (23/23) [86–100] | 23 |
| tool_awareness/after_error | 100.0% (10/10) [72–100] | 10 |
| tool_awareness/after_result | 100.0% (13/13) [77–100] | 13 |
| tool_awareness/ambiguous | 100.0% (6/6) [61–100] | 6 |
| tool_awareness/no_tools_attached | 100.0% (16/16) [81–100] | 16 |
| tool_awareness/not_needed | 100.0% (14/14) [78–100] | 14 |
| tool_awareness/read_call | 100.0% (24/24) [86–100] | 24 |
| tool_awareness/tool_missing | 100.0% (8/8) [68–100] | 8 |
| tool_awareness/write_after_approval | 100.0% (10/10) [72–100] | 10 |
| tool_awareness/write_preview | 100.0% (13/13) [77–100] | 13 |

## Derived behaviour rates

| metric | rate [95% CI] | n |
|---|---|--:|
| harmful_refusal_rate | 100.0% (27/27) [88–100] | 27 |
| over_refusal_rate | 0.0% (0/28) [0–12] | 28 |
| false_tool_call_rate | 16.2% (13/80) [10–26] | 80 |
| valid_tool_call_rate_when_needed | 100.0% (34/34) [90–100] | 34 |
| any_tool_markup_rate_when_needed | 100.0% (34/34) [90–100] | 34 |

## Informational

- mean lexical F1 vs reference (never pass/fail): 0.9804 over 177 items
- output hygiene counts: {'empty': 0, 'template_token_leak': 0, 'think_block': 0}

## Caveats

- Pass rates come from small, hand-written, deterministic probe sets (n is shown for every row); they are behavioural probes, not benchmarks.
- Wilson 95% intervals are shown; with n < 50 they are wide, so differences smaller than the interval should not be over-interpreted.
- No multiple-comparison correction is applied across rows.
- Lexical F1 against reference answers is informational only and never used for pass/fail.
- Perplexity/loss is meaningful only as assistant-token loss on in-distribution data (this dataset's own validation/test splits); it is not comparable across tokenizers or data.
