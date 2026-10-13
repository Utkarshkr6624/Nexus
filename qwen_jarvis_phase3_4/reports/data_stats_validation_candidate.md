# Data statistics: validation

- records: 516, rendered training samples: 606, max_len: 2048
- per-sample tokens: total 86000, mean 141.9, median 113.0, p95 338, p99 498, max 679
- label (loss) tokens: 37115 (43.2% of all tokens)
- per-conversation tokens: total 73507, mean 143.8, median 116, p95 346, p99 498, max 679
- conversations fitting: <=512: 99.22%, <=1024: 100.0%, <=1536: 100.0%, <=2048: 100.0%, <=3072: 100.0%, <=4096: 100.0%
- truncation at max_len=2048: 0.0% of conversations
- samples fitting: <=512: 99.34%, <=1024: 100.0%, <=1536: 100.0%, <=2048: 100.0%, <=3072: 100.0%, <=4096: 100.0%
- multi-turn records: 49, tool records: 25, with system message: 287
- load report: `{'path': 'dataset\\v2_final_candidate\\validation.jsonl', 'mode': 'skip', 'train_on': 'per_turn', 'max_len': 2048, 'records': 516, 'invalid': 0, 'invalid_examples': [], 'samples': 606, 'records_with_dropped_turns': 0, 'dropped_turns': 0, 'records_truncated': 0, 'records_skipped': 0, 'truncated_pct': 0.0}`

## Histogram (tokens per sample)

| tokens | count |
|---|--:|
| 1-64 | 77 |
| 65-128 | 283 |
| 129-192 | 119 |
| 193-256 | 67 |
| 257-384 | 39 |
| 385-512 | 17 |
| 513-768 | 4 |
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
| general_conversation | 64 | 99.1 | 84.5 | 208 | 272 |
| general_knowledge | 36 | 67.7 | 66.5 | 95 | 112 |
| instruction_following | 52 | 79.3 | 70.0 | 140 | 255 |
| jarvis_assistant | 56 | 225.6 | 226.0 | 383 | 509 |
| mathematics | 33 | 130.5 | 134 | 161 | 165 |
| multi_turn_context | 81 | 125.5 | 116 | 244 | 391 |
| nexus_knowledge | 38 | 86.4 | 85.0 | 147 | 150 |
| programming | 76 | 156.3 | 153.5 | 250 | 386 |
| reasoning | 75 | 189.0 | 196 | 327 | 439 |
| safety | 26 | 102.7 | 95.5 | 140 | 162 |
| tool_aware | 40 | 288.3 | 318.5 | 644 | 679 |
