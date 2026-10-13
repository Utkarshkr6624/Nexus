# Data statistics: train

- records: 10298, rendered training samples: 12777, max_len: 1024
- per-sample tokens: total 1867413, mean 146.2, median 110, p95 395, p99 554, max 795
- label (loss) tokens: 722417 (38.7% of all tokens)
- per-conversation tokens: total 1489871, mean 146.2, median 111.0, p95 377, p99 556, max 795
- conversations fitting: <=512: 98.3%, <=1024: 100.0%, <=1536: 100.0%, <=2048: 100.0%, <=3072: 100.0%, <=4096: 100.0%
- truncation at max_len=1024: 0.0% of conversations
- samples fitting: <=512: 98.3%, <=1024: 100.0%, <=1536: 100.0%, <=2048: 100.0%, <=3072: 100.0%, <=4096: 100.0%
- multi-turn records: 1328, tool records: 660, with system message: 5770
- load report: `{'path': 'dataset\\v2_final\\train.jsonl', 'mode': 'strict', 'train_on': 'per_turn', 'max_len': 1024, 'records': 10298, 'invalid': 0, 'invalid_examples': [], 'samples': 12777, 'records_with_dropped_turns': 0, 'dropped_turns': 0, 'records_truncated': 0, 'records_skipped': 0, 'truncated_pct': 0.0}`

## Histogram (tokens per sample)

| tokens | count |
|---|--:|
| 1-64 | 1757 |
| 65-128 | 5982 |
| 129-192 | 2425 |
| 193-256 | 1082 |
| 257-384 | 839 |
| 385-512 | 475 |
| 513-768 | 216 |
| 769-1024 | 1 |
| 1025-1536 | 0 |
| 1537-2048 | 0 |
| 2049-3072 | 0 |
| 3073-4096 | 0 |
| >4096 | 0 |

## Per category (tokens per sample)

| category | n | mean | median | p95 | max |
|---|--:|--:|--:|--:|--:|
| capability_boundaries | 648 | 131.4 | 91.5 | 471 | 589 |
| general_conversation | 1155 | 83.8 | 78 | 158 | 300 |
| general_knowledge | 1494 | 81.8 | 73.0 | 149 | 187 |
| instruction_following | 746 | 88.5 | 79.0 | 167 | 296 |
| jarvis_assistant | 986 | 207.9 | 193.0 | 342 | 661 |
| mathematics | 702 | 115.1 | 111.0 | 166 | 258 |
| multi_turn_context | 1593 | 120.4 | 109 | 245 | 446 |
| nexus_knowledge | 1001 | 99.3 | 91 | 173 | 236 |
| programming | 1469 | 162.1 | 151 | 281 | 486 |
| reasoning | 1432 | 177.5 | 176.0 | 317 | 675 |
| safety | 449 | 102.7 | 101 | 144 | 196 |
| tool_aware | 1102 | 346.7 | 390.0 | 607 | 795 |
