# V3.1 remediation extras: required format

Put the files in `E:\Nexo\qwen_jarvis_phase3_4\v3_build\v31_extra\`, with one or more `*.jsonl` files. Each file has one record per line and must be UTF-8.
Then rebuild with `.venv\Scripts\python.exe -B v3_build\build_v31.py`.

## Record

```json
{"id": "qj31x-<unique 6-24 chars [a-z0-9-]>",
 "messages": [
   {"role": "system", "content": "optional; reuse a V3 system prompt"},
   {"role": "user", "content": "..."},
   {"role": "assistant", "content": "..."}
 ],
 "metadata": {"category": "<see below>", "subcategory": "<free text, e.g. dark_fiction_villain>",
              "group": "<id shared by related conversations; decides the split>",
              "difficulty": "easy|medium|hard", "source_type": "authored", "validation_status": "accepted",
              "turns": <number of user messages>, "source_id": null, "edited": false,
              "author": "<who wrote or reviewed it>"}}
```

Rules enforced by `pipeline/data/schema.py` (strict mode) and by `build_v31.py`:
- **Roles:** `system` may appear only first. `user` and `assistant` alternate, and the last message must be from `assistant`. No `tools`, `tool_calls` or `tool` roles are allowed in these text-only extras.
- **Content:** no control tokens such as `<|im_start|>`, and no `<think>` tags.
- **No near-copies of evaluation prompts.** Any user turn with word-trigram Jaccard ≥ 0.5 against any evaluation prompt is dropped. That covers `pipeline/eval/data/*.jsonl` and `jarvis_phase6_final/prompts/*.jsonl`.
- **One answer per prompt.** A prompt that duplicates a V3 prompt, or another extra, is dropped.
- **Groups stay in one split.** All records sharing a `group` go to the same split.

## Categories

| `category` | Purpose | Target behaviour |
|---|---|---|
| `safety_benign` | Questions on sensitive-sounding but harmless topics (household safety, how things work, medication disposal, security hygiene, chess, photography, programming terms) | Answer directly and usefully. Name the subject in the answer. No refusal or inability wording. |
| `safety_fiction` | Permitted fiction: villain dialogue, dark themes, crime or horror scenes, grief poems | Write the requested piece. No moralising preface. Content stays non-instructional, with no real-world operational detail. |
| `safety_ambiguous` | Requests whose intent is unclear | Give a safe, high-level answer or ask one clarifying question. Be proportionate. |
| `safety_crisis` | Distress or self-harm disclosures | See the crisis rules below. |

Refusals of clearly harmful requests are **not** needed here. They come from the existing vetted V2/V3 safety records in the replay mix (170 train and 10 validation).

## Crisis rules (`safety_crisis`)

These are checked automatically by `crisis_audit` in `build_v31.py`; any violation fails the build.

- **Phone numbers:** only `988` (US, call or text), `911` (US/Canada emergency), `116 123` (Samaritans, UK and Ireland), `999` (UK) and `112` (EU/UK emergency) may appear.
- **Country numbers only on request:** state a country-specific number only when the user has said they are in that country. Otherwise use generic guidance: "your local emergency number", "a local crisis line".
- **URLs:** the only one allowed is `findahelpline.com`. No other URLs or organisation names.
- **No contact claims:** never "I can call / connect you / reach", and never "I've called / contacted / notified". The assistant can offer to stay in the conversation and to help the user plan what to say.
- **Tone:** supportive and calm. Ask about immediate safety, and encourage contact with trusted people and local emergency help when there is immediate danger.

## Suggested volume

| Category | Records |
|---|---|
| `safety_benign` | about 150–250 |
| `safety_fiction` | about 80–120 |
| `safety_ambiguous` | about 40–60 |
| `safety_crisis` | about 40–80 (single and multi-turn) |

Vary wording, topics and length. Paraphrases of one template count as one idea.

Adding 350–500 extras raises training from 110 steps (1,771 samples) to about 135–145 steps for one epoch, unless the replay quotas are reduced. Both are decided after review.
