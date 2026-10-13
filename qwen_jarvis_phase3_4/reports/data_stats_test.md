# Data statistics: test

- records: 493, rendered training samples: 582, max_len: 1024
- per-sample tokens: total 78911, mean 135.6, median 106.5, p95 321, p99 502, max 623
- label (loss) tokens: 33843 (42.9% of all tokens)
- per-conversation tokens: total 67062, mean 137.1, median 108, p95 319, p99 515, max 623
- conversations fitting: <=512: 98.98%, <=1024: 100.0%, <=1536: 100.0%, <=2048: 100.0%, <=3072: 100.0%, <=4096: 100.0%
- truncation at max_len=1024: 0.0% of conversations
- samples fitting: <=512: 99.14%, <=1024: 100.0%, <=1536: 100.0%, <=2048: 100.0%, <=3072: 100.0%, <=4096: 100.0%
- multi-turn records: 42, tool records: 20, with system message: 268
- load report: `{'path': 'dataset\\v2_final\\test.jsonl', 'mode': 'strict', 'train_on': 'per_turn', 'max_len': 1024, 'records': 493, 'invalid': 0, 'invalid_examples': [], 'samples': 582, 'records_with_dropped_turns': 0, 'dropped_turns': 0, 'records_truncated': 0, 'records_skipped': 0, 'truncated_pct': 0.0}`

## Histogram (tokens per sample)

| tokens | count |
|---|--:|
| 1-64 | 84 |
| 65-128 | 280 |
| 129-192 | 106 |
| 193-256 | 61 |
| 257-384 | 33 |
| 385-512 | 13 |
| 513-768 | 5 |
| 769-1024 | 0 |
| 1025-1536 | 0 |
| 1537-2048 | 0 |
| 2049-3072 | 0 |
| 3073-4096 | 0 |
| >4096 | 0 |

## Per category (tokens per sample)

| category | n | mean | median | p95 | max |
|---|--:|--:|--:|--:|--:|
| capability_boundaries | 34 | 115.3 | 84.5 | 472 | 549 |
| general_conversation | 55 | 80.2 | 78 | 134 | 159 |
| general_knowledge | 34 | 64.7 | 62.5 | 100 | 137 |
| instruction_following | 35 | 79.3 | 74 | 129 | 145 |
| jarvis_assistant | 53 | 198.1 | 178 | 336 | 353 |
| mathematics | 37 | 112.9 | 113 | 148 | 159 |
| multi_turn_context | 85 | 120.7 | 108 | 261 | 328 |
| nexus_knowledge | 40 | 86.6 | 86.5 | 145 | 158 |
| programming | 71 | 159.0 | 159 | 243 | 339 |
| reasoning | 77 | 172.0 | 188 | 298 | 439 |
| safety | 25 | 104.5 | 99 | 142 | 171 |
| tool_aware | 36 | 279.2 | 238.5 | 610 | 623 |
