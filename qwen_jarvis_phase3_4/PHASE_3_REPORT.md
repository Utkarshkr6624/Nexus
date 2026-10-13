# Phase 3 report - dataset preflight and training pipeline engineering

Workspace: `E:\Nexo\qwen_jarvis_phase3_4\` (isolated). NEXUS and the 20 older untracked files were not touched (see Phase 4 report for the git evidence).
Labels used throughout: **VERIFIED** (checked by running code / reading real files in this workspace), **ESTIMATED** (arithmetic on assumptions), **NOT TESTED LOCALLY** (needs a GPU / bitsandbytes / Kaggle), **PENDING PHASE 5**.
**No Qwen3-8B weights were downloaded, no GPU training ran, no Kaggle session was used, no fine-tuned checkpoint exists, and no model accuracy is claimed.**

## 1. Dataset preflight (Part A) - details in `preflight/PREFLIGHT_REPORT.md`
| | |
|---|---|
| baseline | `qwen_jarvis_dataset_v2`, 11,332 examples (not regenerated) |
| after targeted corrections (`dataset/v2_clean`) | 11,262 (70 deleted, 294 edited, 4 rewritten, 1 split move) - every change logged in `preflight/changelog.jsonl` |
| frozen (`dataset/v2_final`) | **11,288** = 10,298 train / 497 validation / 493 test; 1,413 multi-turn; 705 records with `tools`; 257 conversations with structured tool calls (re-frozen once after the independent audit's broader leakage finding, see `preflight/PREFLIGHT_REPORT.md`) |

* **Maths**: 22 over-represented near-identical prompts removed (clusters capped at 12); independent exact re-parse of 1,637 `a op b = c` statements found 0 genuine errors (64 parser artefacts read by hand).
* **Reasoning**: 17 over-represented near-identical prompts removed (clusters capped at 12); verified-unique puzzle families kept.
* **Factual-risk audit**: 982 risk-flagged answers (health, medical, legal, financial, security, certainty, tool/model claims) read in full. No dangerous instructions, dosages or diagnoses were found; 9 records with factual or absolute-claim problems fixed (Iceland daylight, tax absolute, honey, turbulence, toothache absolute, bleach/baking-soda swap, ...).
* **NEXUS audit**: all 944 NEXUS answers checked at identifier level (0/85 unverified tokens) against the Phase 1 docs and the NEXUS source (read-only); all 322 newly authored ones read in full; 14 configuration facts confirmed in code. **One genuine error found and fixed**: "twelve of fourteen intents map to a service" -> **eleven** (`backend/app/ml/router.py`); 4 unverifiable details removed. No example presents Qwen, Ollama, a chat endpoint/UI, a tool registry or web/file/terminal tools as existing.
* **JavaScript timing**: 4 wall-clock-dependent examples rewritten to deterministic ones and re-executed (injected clock, concurrency counter, fixed-iteration loop, comparison counting).
* **Wording slip**: the writing-coach turn fixed and the whole conversation rechecked.
* Limits: model-written general-knowledge content was checked where flagged/sampled, **not externally fact-checked**.

## 2. Tool protocol (A10/A11) - `tool_protocol/TOOL_PROTOCOL.md`
`toolproto-1`: Qwen-native `tool_calls` / `role:"tool"` / `tools` representation (verified against the real chat template), 21 illustrative tools with JSON-schema arguments, a compact JSON result envelope, canonical underscore names, and a policy (read-only tools called directly; side-effect tools previewed and approved first; runtime may still refuse; no result -> no claim; unattached tool -> no call). It is explicitly a **future** tool layer: nothing in NEXUS implements it.
* Converter `convert.py` (deterministic, `--strict`): 11,262 -> 0 unconverted; 44 argument-less direct flows annotated by hand (`arg_overrides.json`) plus 3 placeholder-URL flows given a concrete URL; 32 hand-built canonical examples added (declined confirmation, runtime denial, sequential/parallel calls, missing arguments, unattached tool, ...).
* Validator `validate_protocol.py --render`: rules R1-R10; **0 violations over 11,288 records; 705 tool-bearing records rendered through the real chat template with lossless `<tool_call>` JSON round-trip**. `test_protocol.py`: 21 tests, including 13 negative tests proving the validator rejects unknown tools, bad arguments, orphan results, bad envelopes, unconfirmed writes, calls after a refusal, mismatched previews, action claims without a tool result and success claims after an error. The validator enforces rules R1-R10.
* A defect found by the Data agent (5 records listing a tool twice) was fixed in the converter and a rule R1 was added.

## 3. Qwen3-8B model details (B1/B2) - all from files in `_hf_meta/` unless marked
| item | value | status |
|---|---|---|
| model id | `Qwen/Qwen3-8B` (single generative model; configs with another `model_id` fail validation) | VERIFIED (config.json `Qwen3ForCausalLM`) |
| architecture | 36 layers, hidden 4096, 32 attention / 8 KV heads, head_dim 128, intermediate 12288, vocab 151,936, untied embeddings, bf16 weights | VERIFIED (config.json) |
| parameters | 8,190,735,360 | computed from config.json |
| context | `max_position_embeddings` 40,960; training length capped at 1,024 | VERIFIED / design |
| tokenizer | byte-level BPE, 151,669 tokens; `<|im_start|>`=151644, `<|im_end|>`=151645 (EOS), `<|endoftext|>`=151643 (PAD), `<think>`=151667, `</think>`=151668, `<tool_call>`=151657, `</tool_call>`=151658, `<tool_response>`=151665, `</tool_response>`=151666 - all single tokens, no BOS | VERIFIED (loaded and encoded) |
| chat template | ChatML; tools block `<tools>`, calls as `<tool_call>{json}</tool_call>`, `tool` messages inside a user turn as `<tool_response>`; only the **last** assistant turn gets an empty `<think></think>` block; `enable_thinking=False` appends it to the generation prompt | VERIFIED (rendered with transformers 5.19.0) |
| minimum transformers | >= 4.51.0 (config `transformers_version` 4.51.0) | VERIFIED from config; installed locally 5.19.0, peft 0.21.2, trl 1.15.0, torch 2.14.1 CPU |
| QLoRA / bitsandbytes | NF4 + double quant, LoRA on 7 projection types | design; **NOT TESTED LOCALLY** (no CUDA / bitsandbytes here) |
| precision | bf16 where supported, else fp16 + GradScaler (T4/P100 lack native bf16) | design; fp16 stability **NOT TESTED** |

## 4. Tokenizer and chat-template tests (B5) - `tests/test_tokenizer.py` (10 tests, all pass)
Normal text, multi-turn, code, JSON, maths, Unicode (incl. NFC normalisation behaviour), long prompts, special tokens, encode/decode round trips and template rendering against exact expected strings, including a tool-call fixture.

## 5. Data loader (B4) - `pipeline/data/` (44 tests pass: tokenizer 10, schema 12, render/masking 13, loader/batching 9)
* Strict schema validation of the chat+tools format (roles, alternation, tool rules, control-token injection blocked) and a lenient Phase-2 mode.
* **Rendering convention**: one training sample per assistant message, prompt = exact inference prompt (`add_generation_prompt=True, enable_thinking=False`); loss only on assistant content, any `<tool_call>` block and the closing `<|im_end|>`; prompt tokens, think block, system/user/tool text are masked. Tested on 600 real records plus fixtures: the training prefix equals the inference prompt at every generation point.
* Dynamic padding collate (pad id 151643, labels -100), length-grouped and token-budget batching, deterministic ordering, truncation policy and reporting.
* **Statistics on the frozen set (max_len 1024, VERIFIED)**:

| split | records | training samples | tokens | mean / median / p95 / p99 / max per sample | truncated |
|---|--:|--:|--:|---|--:|
| train | 10,298 | 12,777 | 1,867,413 | 146 / 110 / 395 / 554 / 795 | 0% |
| validation | 497 | 576 | 83,292 | 145 / 117 / 346 / 498 / 679 | 0% |
| test | 493 | 582 | 78,911 | 136 / 107 / 321 / 502 / 623 | 0% |

  Everything fits in 1,024 tokens (longest 795); 2,048 would only add padding. Per-category token statistics and histograms are in `reports/data_stats_*.md`.

## 6. QLoRA and SFT configuration (B3) - `configs/qlora_sft.yaml`, rationale per parameter in `docs/TRAINING_DESIGN.md`
| group | value |
|---|---|
| quantisation | NF4, double quantisation, compute dtype auto (bf16 if supported else fp16) |
| LoRA | r=16, alpha=32, dropout 0.05, targets q,k,v,o,gate,up,down; 43,646,976 trainable parameters (0.53%) |
| optimisation | AdamW (LoRA params only), lr 1e-4, cosine schedule, 3% warmup, weight decay 0.01, grad-clip 1.0, 2 epochs, seed 42 |
| batching | max length 1024, micro-batch 4 x accumulation 4 = effective 16, length-grouped, no packing |
| memory | gradient checkpointing; **chunked label-position-only loss** (avoids the 151,936-vocab logits blow-up; matches HF full-logits loss in tests) |
| run control | evaluation and checkpoint every 100 steps; keep last 2 + best; early stopping patience 4 evaluations |
| data identity | manifest version and file hashes checked before any data is read; recorded in every checkpoint |
Why a custom loop rather than TRL `SFTTrainer`: the verified per-turn rendering and loss masks must not be bypassed; resume, atomic checkpoints and identity checks are explicit and unit-tested.

## 7. Training engine and production lock (B6/B7) - `pipeline/train/train.py`
Safe modes: `--validate-config`, `--dry-run` (tokenizer + data + batch shapes + step/time plan, never builds a model), `--smoke-test` (tiny random Qwen3-architecture model, real tokenizer, 65 samples; banner "PIPELINE SMOKE TEST - NOT QWEN PRODUCTION TRAINING"). `--resume` is rejected with the safe modes. Importing `train.py` loads no ML library.
**Production lock**: refuses with exit code 3 unless the environment variable `QWEN_PHASE5_UNLOCK=I_UNDERSTAND_THIS_STARTS_PRODUCTION_TRAINING`, the `--confirm-production` flag and `production_locked: false` are all present (the lock refuses when any one condition is missing: no conditions, flag only, flag + config without the environment variable, wrong environment value, environment variable only - all tested). `model.trust_remote_code=true`, any non-`Qwen/Qwen3-8B` production model, `quant.enabled: false`, `flash_attention_2` and an unofficial tokenizer location are rejected by production validation.

**Hardening added after the independent audit** (all unit-tested): precision `auto` picks bf16 only on compute capability >= 8 (T4/P100 -> fp16 + GradScaler; `is_bf16_supported()` is True on a T4 via emulation); the frozen-dataset manifest is located case-insensitively and a missing/mismatched manifest is a hard error in production and in the production dry run; the fallback renderer is refused outside smoke mode; a NaN loss aborts, and `train.max_consecutive_skipped_steps` (default 20) consecutive non-finite-gradient skips abort the run, with `skipped_steps` recorded in checkpoints; a fresh run in a non-empty run directory is refused; a tokenizer/chat-template fingerprint is compared with `_hf_meta`; periodic log lines carry `tok_per_s`; a run whose LoRA weights did not change is not reported as a success.

## 8. Checkpoint system (B8) - `pipeline/train/checkpoint.py`
`runs/<name>/checkpoint-<step>/` = PEFT adapter (safetensors) + `trainer_state.pt` (optimizer, scheduler, GradScaler, RNG, loop state; loaded with `weights_only`) + `checkpoint_meta.json` (step, epoch, metrics, config hash, dataset manifest hash, model id, library versions, timestamp); atomic write-then-rename, `latest`/`best` pointers, retention policy, integrity checks on load (missing/corrupt/truncated/hash-mismatch/incompatible identity refused unless `--force-resume`). **Verified on CPU**: an interrupted-and-resumed smoke run is bit-identical (adapter weights and validation loss) to an uninterrupted one.

## 9. Evaluation system (B9/B10) - `pipeline/eval/` (README for Phase 5 usage)
Model-agnostic (`generate(messages, tools) -> str`), deterministic scorers with no LLM judge and no execution of model output: ~40 check types over 8 suites (instruction following 62, multi-turn 40, NEXUS facts 128, tool awareness 114, capability boundaries 54 including 24 positive controls where refusing is a failure, safety 59, general 113, held-out test split 168), a strict tool-call parser, assistant-token loss/perplexity per category, Wilson intervals, base-vs-fine-tuned `compare` with paired McNemar tests, and stamped `STUB - NOT A MODEL RESULT` output. Scorer validation: an oracle stub scores 682/682 and the adversarial stubs (refuse-all, always/never call tools, hallucinate NEXUS, empty, echo, a generic hedge string, a content-free 'done') score <= 1.5% overall and <= 10% on every suite, bounds enforced in `selftest`; five loopholes found on the first run and a constant-hedge loophole found by the independent audit were fixed and pinned by tests. These are **probe sets, not benchmarks**; no model has been evaluated.

## 10. Reproducibility (B11) - `scripts/check_reproducibility.py`, `reports/reproducibility.json`
Dataset rebuild (preflight -> conversion -> freeze), eval-suite build, data statistics, the Kaggle package zip and the smoke-training validation loss were each run twice: all identical. `pipeline/train/repro.py` records library versions, hardware, seed, config and dataset hashes with every run.

## 11. Memory planning (B12) - `docs/MEMORY_PLAN.md` (everything ESTIMATED, nothing MEASURED)
Parameters 8,190,735,360; NF4 + double-quant weights ~5.66 GiB resident; LoRA r=16 adapter + AdamW ~0.65 GiB; with the chunked loss, seq 1024 x micro-batch 4 ~ **12.5 GiB** peak (fits a 16 GB T4/P100 with ~1.9 GiB headroom against ~14.4 GiB usable), versus ~18.3 GiB with a naive full-logits loss (would OOM). Throughput 132-397 tokens/s on a T4 (a FLOPs argument with an assumed 10-30% utilisation) -> **2.6-7.8 h for 2 epochs** (1.87 M tokens/epoch). Dual-T4, fp16 vs bf16, OOM ladder and session-kill strategy documented. The local RTX 3050 (4 GB) cannot hold the model; no local training was attempted.

## 12. Kaggle package (B13) - `kaggle/`
`dist/qwen_jarvis_kaggle_package.zip` (64 files, 2.7 MB, byte-reproducible): pipeline, configs, tool schemas, the frozen dataset, requirements, README, notebook. The 17-cell notebook is built by `build_notebook.py`: environment check, safe modes, a **gate** (`PHASE5_AUTHORIZED`/`CONFIRM_PRODUCTION` default False, unlock env var, Kaggle + CUDA check) in front of every production cell (train, resume, eval, base-vs-fine-tuned compare), adapter export, summary. `validate_notebook.py --strict`: 39 passed, 0 failed, 1 pending (Kaggle execution itself). The package was extracted into a clean directory and `--validate-config`, `--dry-run` and the stub evaluation were run from there. **Kaggle execution: PENDING PHASE 5.**

## 13. Files created (inside `qwen_jarvis_phase3_4/` only)
`preflight/` (audit scripts, patches, changelog, reports) - `tool_protocol/` (spec, schemas, converter, validator, tests, examples) - `pipeline/data|train|eval/` - `configs/` - `docs/` (CONTRACT, TRAINING_DESIGN, MEMORY_PLAN) - `kaggle/` - `scripts/` (freeze, rebuild, reproducibility) - `security/` - `tests/` - `reports/` - `dataset/` (`v2_clean`, `v2_final_candidate`, **`v2_final` frozen**) - `_hf_meta/` (tokenizer/config files only, no weights) - `.venv/` (isolated CPU environment).

## 14. Known limitations carried into Phase 5
Everything GPU-bound is untested: 4-bit loading, fp16/bf16 stability, real memory and tokens/s, gradient-checkpointing behaviour, Kaggle quota/disk limits. The NEXUS and tool-use quality of the fine-tuned model is unknown until trained and evaluated. The dataset is model-written (not externally fact-checked), moderately templated in places, and its tool names are illustrative.
