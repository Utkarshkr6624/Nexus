# Data statistics: test

- records: 515, rendered training samples: 612, max_len: 2048
- per-sample tokens: total 82070, mean 134.1, median 106.0, p95 319, p99 473, max 623
- label (loss) tokens: 35309 (43.0% of all tokens)
- per-conversation tokens: total 69529, mean 136.1, median 107, p95 318, p99 473, max 623
- conversations fitting: <=512: 99.02%, <=1024: 100.0%, <=1536: 100.0%, <=2048: 100.0%, <=3072: 100.0%, <=4096: 100.0%
- truncation at max_len=2048: 0.0% of conversations
- samples fitting: <=512: 99.18%, <=1024: 100.0%, <=1536: 100.0%, <=2048: 100.0%, <=3072: 100.0%, <=4096: 100.0%
- multi-turn records: 46, tool records: 20, with system message: 284
- load report: `{'path': 'dataset\\v2_final_candidate\\test.jsonl', 'mode': 'skip', 'train_on': 'per_turn', 'max_len': 2048, 'records': 515, 'invalid': 0, 'invalid_examples': [], 'samples': 612, 'records_with_dropped_turns': 0, 'dropped_turns': 0, 'records_truncated': 0, 'records_skipped': 0, 'truncated_pct': 0.0}`

## Histogram (tokens per sample)

| tokens | count |
|---|--:|
| 1-64 | 87 |
| 65-128 | 298 |
| 129-192 | 114 |
| 193-256 | 62 |
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
| capability_boundaries | 34 | 115.9 | 84.5 | 472 | 549 |
| general_conversation | 55 | 80.2 | 78 | 134 | 159 |
| general_knowledge | 43 | 67.0 | 70 | 97 | 137 |
| instruction_following | 42 | 83.4 | 76.5 | 135 | 174 |
| jarvis_assistant | 53 | 198.1 | 178 | 336 | 353 |
| mathematics | 37 | 112.9 | 113 | 148 | 159 |
| multi_turn_context | 89 | 119.4 | 108 | 261 | 328 |
| nexus_knowledge | 45 | 89.7 | 88 | 145 | 181 |
| programming | 76 | 159.0 | 158.0 | 243 | 339 |
| reasoning | 77 | 172.0 | 188 | 298 | 439 |
| safety | 25 | 104.5 | 99 | 142 | 171 |
| tool_aware | 36 | 279.2 | 238.5 | 610 | 623 |
