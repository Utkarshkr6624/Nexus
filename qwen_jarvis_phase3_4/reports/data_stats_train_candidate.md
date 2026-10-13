# Data statistics: train

- records: 10226, rendered training samples: 12658, max_len: 2048
- per-sample tokens: total 1840116, mean 145.4, median 109.0, p95 392, p99 554, max 795
- label (loss) tokens: 717882 (39.0% of all tokens)
- per-conversation tokens: total 1473896, mean 145.6, median 111.0, p95 374, p99 556, max 795
- conversations fitting: <=512: 98.29%, <=1024: 100.0%, <=1536: 100.0%, <=2048: 100.0%, <=3072: 100.0%, <=4096: 100.0%
- truncation at max_len=2048: 0.0% of conversations
- samples fitting: <=512: 98.29%, <=1024: 100.0%, <=1536: 100.0%, <=2048: 100.0%, <=3072: 100.0%, <=4096: 100.0%
- multi-turn records: 1310, tool records: 629, with system message: 5710
- load report: `{'path': 'dataset\\v2_final_candidate\\train.jsonl', 'mode': 'skip', 'train_on': 'per_turn', 'max_len': 2048, 'records': 10231, 'invalid': 5, 'invalid_examples': [(1624, 'qj2f-22b0384cd095', ["duplicate tool name 'web_search'"]), (2173, 'qj2f-2ec50dc93764', ["duplicate tool name 'fs_read_file'"]), (6561, 'qj2f-97754675c298', ["duplicate tool name 'fs_read_file'"]), (7518, 'qj2f-b22b07e7b863', ["duplicate tool name 'fs_read_file'"]), (8446, 'qj2f-cb7c0777e97e', ["duplicate tool name 'fs_read_file'"])], 'samples': 12658, 'records_with_dropped_turns': 0, 'dropped_turns': 0, 'records_truncated': 0, 'records_skipped': 0, 'truncated_pct': 0.0}`

## Histogram (tokens per sample)

| tokens | count |
|---|--:|
| 1-64 | 1745 |
| 65-128 | 5942 |
| 129-192 | 2419 |
| 193-256 | 1076 |
| 257-384 | 807 |
| 385-512 | 452 |
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
| capability_boundaries | 645 | 131.5 | 92 | 471 | 589 |
| general_conversation | 1155 | 83.8 | 78 | 158 | 300 |
| general_knowledge | 1474 | 81.8 | 73.0 | 149 | 187 |
| instruction_following | 728 | 88.3 | 79.0 | 167 | 296 |
| jarvis_assistant | 986 | 207.9 | 193.0 | 342 | 661 |
| mathematics | 702 | 115.1 | 111.0 | 166 | 258 |
| multi_turn_context | 1583 | 120.6 | 109 | 245 | 446 |
| nexus_knowledge | 996 | 99.2 | 91.0 | 173 | 236 |
| programming | 1462 | 162.1 | 151.0 | 281 | 486 |
| reasoning | 1432 | 177.6 | 176.0 | 317 | 675 |
| safety | 449 | 102.7 | 101 | 144 | 196 |
| tool_aware | 1046 | 345.0 | 391.0 | 608 | 795 |
