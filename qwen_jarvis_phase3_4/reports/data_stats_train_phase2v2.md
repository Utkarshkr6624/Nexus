# Data statistics: train

- records: 10296, rendered training samples: 12755, max_len: 2048
- per-sample tokens: total 1585405, mean 124.3, median 108, p95 257, p99 337, max 675
- label (loss) tokens: 720031 (45.4% of all tokens)
- per-conversation tokens: total 1323954, mean 128.6, median 111.0, p95 265, p99 341, max 675
- conversations fitting: <=512: 99.94%, <=1024: 100.0%, <=1536: 100.0%, <=2048: 100.0%, <=3072: 100.0%, <=4096: 100.0%
- truncation at max_len=2048: 0.0% of conversations
- samples fitting: <=512: 99.95%, <=1024: 100.0%, <=1536: 100.0%, <=2048: 100.0%, <=3072: 100.0%, <=4096: 100.0%
- multi-turn records: 1369, tool records: 0, with system message: 5754
- load report: `{'path': '..\\qwen_jarvis_phase2\\dataset\\splits\\train.jsonl', 'mode': 'phase2', 'train_on': 'per_turn', 'max_len': 2048, 'records': 10296, 'invalid': 0, 'invalid_examples': [], 'samples': 12755, 'records_with_dropped_turns': 0, 'dropped_turns': 0, 'records_truncated': 0, 'records_skipped': 0, 'truncated_pct': 0.0}`

## Histogram (tokens per sample)

| tokens | count |
|---|--:|
| 1-64 | 1748 |
| 65-128 | 6392 |
| 129-192 | 2858 |
| 193-256 | 1110 |
| 257-384 | 605 |
| 385-512 | 36 |
| 513-768 | 6 |
| 769-1024 | 0 |
| 1025-1536 | 0 |
| 1537-2048 | 0 |
| 2049-3072 | 0 |
| 3073-4096 | 0 |
| >4096 | 0 |

## Per category (tokens per sample)

| category | n | mean | median | p95 | max |
|---|--:|--:|--:|--:|--:|
| capability_boundaries | 627 | 87.9 | 84 | 143 | 200 |
| general_conversation | 1155 | 83.8 | 78 | 158 | 300 |
| general_knowledge | 1473 | 81.9 | 73 | 149 | 187 |
| instruction_following | 728 | 88.3 | 79.0 | 167 | 296 |
| jarvis_assistant | 1000 | 198.3 | 184.5 | 332 | 372 |
| mathematics | 721 | 114.8 | 111 | 166 | 258 |
| multi_turn_context | 1607 | 120.3 | 109 | 245 | 446 |
| nexus_knowledge | 996 | 99.3 | 91.0 | 173 | 236 |
| programming | 1462 | 161.9 | 151.0 | 278 | 486 |
| reasoning | 1449 | 177.0 | 175 | 316 | 675 |
| safety | 457 | 103.0 | 101 | 145 | 196 |
| tool_aware | 1080 | 125.2 | 122.0 | 186 | 241 |
