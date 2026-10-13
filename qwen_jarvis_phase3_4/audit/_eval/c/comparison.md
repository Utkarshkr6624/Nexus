# STUB — NOT A MODEL RESULT

> At least one side of this comparison is a test stub. Nothing below is a model result.

# Comparison: base vs fine-tuned

- base: `stub:oracle` adapter=`None`
- fine-tuned: `stub:refuse_all` adapter=`None`
- items compared (scored, present in both): 666; only in base: 0; only in fine-tuned: 0

| row | n | base [95% CI] | fine-tuned [95% CI] | delta (pp) | discordant (base-only / fine-tuned-only) | McNemar p | CIs overlap |
|---|--:|---|---|--:|---|--:|:-:|
| overall | 666 | 100.0% (666/666) [99–100] | 1.5% (10/666) [1–3] | -98.5 | 656 / 0 | 0.0 | no |
| suite:capability_boundaries | 30 | 100.0% (30/30) [89–100] | 0.0% (0/30) [0–11] | -100.0 | 30 / 0 | 0.0 | no |
| suite:general | 113 | 100.0% (113/113) [97–100] | 0.0% (0/113) [0–3] | -100.0 | 113 / 0 | 0.0 | no |
| suite:instruction_following | 62 | 100.0% (62/62) [94–100] | 9.7% (6/62) [5–20] | -90.3 | 56 / 0 | 0.0 | no |
| suite:multi_turn | 40 | 100.0% (40/40) [91–100] | 5.0% (2/40) [1–16] | -95.0 | 38 / 0 | 0.0 | no |
| suite:nexus_facts | 128 | 100.0% (128/128) [97–100] | 0.0% (0/128) [0–3] | -100.0 | 128 / 0 | 0.0 | no |
| suite:safety | 59 | 100.0% (59/59) [94–100] | 0.0% (0/59) [0–6] | -100.0 | 59 / 0 | 0.0 | no |
| suite:testsplit | 120 | 100.0% (120/120) [97–100] | 0.8% (1/120) [0–5] | -99.2 | 119 / 0 | 0.0 | no |
| suite:tool_awareness | 114 | 100.0% (114/114) [97–100] | 0.9% (1/114) [0–5] | -99.1 | 113 / 0 | 0.0 | no |
| category:capability_boundaries/no_access | 30 | 100.0% (30/30) [89–100] | 0.0% (0/30) [0–11] | -100.0 | 30 / 0 | 0.0 | no |
| category:general/code | 12 | 100.0% (12/12) [76–100] | 0.0% (0/12) [0–24] | -100.0 | 12 / 0 | 0.0005 | no |
| category:general/math | 60 | 100.0% (60/60) [94–100] | 0.0% (0/60) [0–6] | -100.0 | 60 / 0 | 0.0 | no |
| category:general/qa | 41 | 100.0% (41/41) [91–100] | 0.0% (0/41) [0–9] | -100.0 | 41 / 0 | 0.0 | no |
| category:instruction_following/classification | 3 | 100.0% (3/3) [44–100] | 0.0% (0/3) [0–56] | -100.0 | 3 / 0 | 0.25 | yes |
| category:instruction_following/conflict | 2 | 100.0% (2/2) [34–100] | 0.0% (0/2) [0–66] | -100.0 | 2 / 0 | 0.5 | yes |
| category:instruction_following/constraint | 37 | 100.0% (37/37) [91–100] | 16.2% (6/37) [8–31] | -83.8 | 31 / 0 | 0.0 | no |
| category:instruction_following/counting | 2 | 100.0% (2/2) [34–100] | 0.0% (0/2) [0–66] | -100.0 | 2 / 0 | 0.5 | yes |
| category:instruction_following/extraction | 3 | 100.0% (3/3) [44–100] | 0.0% (0/3) [0–56] | -100.0 | 3 / 0 | 0.25 | yes |
| category:instruction_following/list_ops | 3 | 100.0% (3/3) [44–100] | 0.0% (0/3) [0–56] | -100.0 | 3 / 0 | 0.25 | yes |
| category:instruction_following/multi_constraint | 4 | 100.0% (4/4) [51–100] | 0.0% (0/4) [0–49] | -100.0 | 4 / 0 | 0.125 | no |
| category:instruction_following/reformat | 2 | 100.0% (2/2) [34–100] | 0.0% (0/2) [0–66] | -100.0 | 2 / 0 | 0.5 | yes |
| category:instruction_following/system_rule | 6 | 100.0% (6/6) [61–100] | 0.0% (0/6) [0–39] | -100.0 | 6 / 0 | 0.0312 | no |
| category:multi_turn/context | 40 | 100.0% (40/40) [91–100] | 5.0% (2/40) [1–16] | -95.0 | 38 / 0 | 0.0 | no |
| category:nexus_facts/absent | 16 | 100.0% (16/16) [81–100] | 0.0% (0/16) [0–19] | -100.0 | 16 / 0 | 0.0 | no |
| category:nexus_facts/current | 68 | 100.0% (68/68) [95–100] | 0.0% (0/68) [0–5] | -100.0 | 68 / 0 | 0.0 | no |
| category:nexus_facts/false_premise | 18 | 100.0% (18/18) [82–100] | 0.0% (0/18) [0–18] | -100.0 | 18 / 0 | 0.0 | no |
| category:nexus_facts/planned | 26 | 100.0% (26/26) [87–100] | 0.0% (0/26) [0–13] | -100.0 | 26 / 0 | 0.0 | no |
| category:safety/benign_lookalike | 28 | 100.0% (28/28) [88–100] | 0.0% (0/28) [0–12] | -100.0 | 28 / 0 | 0.0 | no |
| category:safety/crisis | 4 | 100.0% (4/4) [51–100] | 0.0% (0/4) [0–49] | -100.0 | 4 / 0 | 0.125 | no |
| category:safety/harmful | 27 | 100.0% (27/27) [88–100] | 0.0% (0/27) [0–12] | -100.0 | 27 / 0 | 0.0 | no |
| category:testsplit/capability_boundaries | 23 | 100.0% (23/23) [86–100] | 0.0% (0/23) [0–14] | -100.0 | 23 / 0 | 0.0 | no |
| category:testsplit/mathematics | 23 | 100.0% (23/23) [86–100] | 0.0% (0/23) [0–14] | -100.0 | 23 / 0 | 0.0 | no |
| category:testsplit/nexus_knowledge | 41 | 100.0% (41/41) [91–100] | 0.0% (0/41) [0–9] | -100.0 | 41 / 0 | 0.0 | no |
| category:testsplit/safety | 10 | 100.0% (10/10) [72–100] | 0.0% (0/10) [0–28] | -100.0 | 10 / 0 | 0.002 | no |
| category:testsplit/tool_aware | 23 | 100.0% (23/23) [86–100] | 4.3% (1/23) [1–21] | -95.7 | 22 / 0 | 0.0 | no |
| category:tool_awareness/after_error | 10 | 100.0% (10/10) [72–100] | 0.0% (0/10) [0–28] | -100.0 | 10 / 0 | 0.002 | no |
| category:tool_awareness/after_result | 13 | 100.0% (13/13) [77–100] | 0.0% (0/13) [0–23] | -100.0 | 13 / 0 | 0.0002 | no |
| category:tool_awareness/ambiguous | 6 | 100.0% (6/6) [61–100] | 0.0% (0/6) [0–39] | -100.0 | 6 / 0 | 0.0312 | no |
| category:tool_awareness/no_tools_attached | 16 | 100.0% (16/16) [81–100] | 0.0% (0/16) [0–19] | -100.0 | 16 / 0 | 0.0 | no |
| category:tool_awareness/not_needed | 14 | 100.0% (14/14) [78–100] | 7.1% (1/14) [1–31] | -92.9 | 13 / 0 | 0.0002 | no |
| category:tool_awareness/read_call | 24 | 100.0% (24/24) [86–100] | 0.0% (0/24) [0–14] | -100.0 | 24 / 0 | 0.0 | no |
| category:tool_awareness/tool_missing | 8 | 100.0% (8/8) [68–100] | 0.0% (0/8) [0–32] | -100.0 | 8 / 0 | 0.0078 | no |
| category:tool_awareness/write_after_approval | 10 | 100.0% (10/10) [72–100] | 0.0% (0/10) [0–28] | -100.0 | 10 / 0 | 0.002 | no |
| category:tool_awareness/write_preview | 13 | 100.0% (13/13) [77–100] | 0.0% (0/13) [0–23] | -100.0 | 13 / 0 | 0.0002 | no |

## How to read this

- Same items, same decoding; the McNemar test uses only the items on which the two models disagree (paired).
- A small p-value with overlapping CIs is still weak evidence on tiny n; a large delta on n < 20 is anecdotal.
- Rows were not corrected for multiple comparisons; look for consistent direction across related rows rather than any single row.
- Regressions matter as much as gains: check `general/*`, `safety/benign_lookalike` (over-refusal) and `instruction_following` for losses after fine-tuning.

- Pass rates come from small, hand-written, deterministic probe sets (n is shown for every row); they are behavioural probes, not benchmarks.
- Wilson 95% intervals are shown; with n < 50 they are wide, so differences smaller than the interval should not be over-interpreted.
- No multiple-comparison correction is applied across rows.
- Lexical F1 against reference answers is informational only and never used for pass/fail.
- Perplexity/loss is meaningful only as assistant-token loss on in-distribution data (this dataset's own validation/test splits); it is not comparable across tokenizers or data.
