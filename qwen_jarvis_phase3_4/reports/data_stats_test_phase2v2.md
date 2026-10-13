# Data statistics: test

- records: 518, rendered training samples: 614, max_len: 2048
- per-sample tokens: total 75286, mean 122.6, median 105.0, p95 254, p99 329, max 439
- label (loss) tokens: 35440 (47.1% of all tokens)
- per-conversation tokens: total 65109, mean 125.7, median 107.0, p95 264, p99 331, max 439
- conversations fitting: <=512: 100.0%, <=1024: 100.0%, <=1536: 100.0%, <=2048: 100.0%, <=3072: 100.0%, <=4096: 100.0%
- truncation at max_len=2048: 0.0% of conversations
- samples fitting: <=512: 100.0%, <=1024: 100.0%, <=1536: 100.0%, <=2048: 100.0%, <=3072: 100.0%, <=4096: 100.0%
- multi-turn records: 46, tool records: 0, with system message: 286
- load report: `{'path': '..\\qwen_jarvis_phase2\\dataset\\splits\\test.jsonl', 'mode': 'phase2', 'train_on': 'per_turn', 'max_len': 2048, 'records': 518, 'invalid': 0, 'invalid_examples': [], 'samples': 614, 'records_with_dropped_turns': 0, 'dropped_turns': 0, 'records_truncated': 0, 'records_skipped': 0, 'truncated_pct': 0.0}`

## Histogram (tokens per sample)

| tokens | count |
|---|--:|
| 1-64 | 87 |
| 65-128 | 311 |
| 129-192 | 125 |
| 193-256 | 62 |
| 257-384 | 28 |
| 385-512 | 1 |
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
| capability_boundaries | 33 | 83.2 | 83 | 127 | 143 |
| general_conversation | 55 | 80.2 | 78 | 134 | 159 |
| general_knowledge | 43 | 67.0 | 70 | 97 | 137 |
| instruction_following | 42 | 83.4 | 76.5 | 135 | 174 |
| jarvis_assistant | 54 | 193.0 | 176.0 | 333 | 351 |
| mathematics | 39 | 112.2 | 112 | 148 | 159 |
| multi_turn_context | 89 | 119.4 | 108 | 261 | 328 |
| nexus_knowledge | 45 | 89.7 | 88 | 145 | 181 |
| programming | 76 | 159.0 | 158.0 | 243 | 339 |
| reasoning | 77 | 172.0 | 188 | 298 | 439 |
| safety | 25 | 104.5 | 99 | 142 | 171 |
| tool_aware | 36 | 120.5 | 121.5 | 146 | 148 |
