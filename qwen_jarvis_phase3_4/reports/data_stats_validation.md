# Data statistics: validation

- records: 497, rendered training samples: 576, max_len: 1024
- per-sample tokens: total 83292, mean 144.6, median 116.5, p95 346, p99 498, max 679
- label (loss) tokens: 36056 (43.3% of all tokens)
- per-conversation tokens: total 71568, mean 145.5, median 117.0, p95 346, p99 509, max 679
- conversations fitting: <=512: 99.19%, <=1024: 100.0%, <=1536: 100.0%, <=2048: 100.0%, <=3072: 100.0%, <=4096: 100.0%
- truncation at max_len=1024: 0.0% of conversations
- samples fitting: <=512: 99.31%, <=1024: 100.0%, <=1536: 100.0%, <=2048: 100.0%, <=3072: 100.0%, <=4096: 100.0%
- multi-turn records: 43, tool records: 25, with system message: 274
- load report: `{'path': 'dataset\\v2_final\\validation.jsonl', 'mode': 'strict', 'train_on': 'per_turn', 'max_len': 1024, 'records': 497, 'invalid': 0, 'invalid_examples': [], 'samples': 576, 'records_with_dropped_turns': 0, 'dropped_turns': 0, 'records_truncated': 0, 'records_skipped': 0, 'truncated_pct': 0.0}`

## Histogram (tokens per sample)

| tokens | count |
|---|--:|
| 1-64 | 69 |
| 65-128 | 264 |
| 129-192 | 117 |
| 193-256 | 66 |
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
| general_knowledge | 25 | 64.4 | 63 | 94 | 95 |
| instruction_following | 41 | 76.2 | 69 | 125 | 255 |
| jarvis_assistant | 56 | 225.6 | 226.0 | 383 | 509 |
| mathematics | 33 | 130.5 | 134 | 161 | 165 |
| multi_turn_context | 75 | 128.8 | 124 | 275 | 391 |
| nexus_knowledge | 38 | 86.4 | 85.0 | 147 | 150 |
| programming | 74 | 155.4 | 148.0 | 250 | 386 |
| reasoning | 75 | 189.0 | 196 | 327 | 439 |
| safety | 26 | 102.7 | 95.5 | 140 | 162 |
| tool_aware | 40 | 288.3 | 318.5 | 644 | 679 |
