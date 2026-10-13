# Data statistics: validation

- records: 518, rendered training samples: 609, max_len: 2048
- per-sample tokens: total 78288, mean 128.6, median 111, p95 262, p99 347, max 439
- label (loss) tokens: 37207 (47.5% of all tokens)
- per-conversation tokens: total 67870, mean 131.0, median 113.0, p95 270, p99 356, max 439
- conversations fitting: <=512: 100.0%, <=1024: 100.0%, <=1536: 100.0%, <=2048: 100.0%, <=3072: 100.0%, <=4096: 100.0%
- truncation at max_len=2048: 0.0% of conversations
- samples fitting: <=512: 100.0%, <=1024: 100.0%, <=1536: 100.0%, <=2048: 100.0%, <=3072: 100.0%, <=4096: 100.0%
- multi-turn records: 50, tool records: 0, with system message: 288
- load report: `{'path': '..\\qwen_jarvis_phase2\\dataset\\splits\\validation.jsonl', 'mode': 'phase2', 'train_on': 'per_turn', 'max_len': 2048, 'records': 518, 'invalid': 0, 'invalid_examples': [], 'samples': 609, 'records_with_dropped_turns': 0, 'dropped_turns': 0, 'records_truncated': 0, 'records_skipped': 0, 'truncated_pct': 0.0}`

## Histogram (tokens per sample)

| tokens | count |
|---|--:|
| 1-64 | 78 |
| 65-128 | 297 |
| 129-192 | 132 |
| 193-256 | 68 |
| 257-384 | 30 |
| 385-512 | 4 |
| 513-768 | 0 |
| 769-1024 | 0 |
| 1025-1536 | 0 |
| 1537-2048 | 0 |
| 2049-3072 | 0 |
| 3073-4096 | 0 |
| >4096 | 0 |

## Per category (tokens per sample)

| category | n | mean | median | p95 | max |
|---|--:|--:|--:|--:|--:|
| capability_boundaries | 29 | 84.7 | 84 | 111 | 113 |
| general_conversation | 64 | 98.8 | 84.5 | 208 | 272 |
| general_knowledge | 37 | 67.5 | 66 | 95 | 112 |
| instruction_following | 52 | 79.3 | 70.0 | 140 | 255 |
| jarvis_assistant | 56 | 201.3 | 201.5 | 343 | 357 |
| mathematics | 34 | 129.4 | 133.5 | 161 | 165 |
| multi_turn_context | 81 | 125.5 | 116 | 244 | 391 |
| nexus_knowledge | 38 | 86.5 | 85.0 | 147 | 150 |
| programming | 76 | 156.3 | 153.5 | 250 | 386 |
| reasoning | 75 | 189.0 | 196 | 327 | 439 |
| safety | 26 | 102.5 | 95.5 | 140 | 162 |
| tool_aware | 41 | 123.0 | 118 | 156 | 240 |
