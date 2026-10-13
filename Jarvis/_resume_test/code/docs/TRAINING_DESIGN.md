# Training design - Qwen3-8B QLoRA SFT (Phase 3: B1-B3, B7, B8, B11)

Status: configured and unit/smoke-tested on CPU with a tiny random Qwen3-architecture model. **No Qwen3-8B weights were downloaded, no GPU training or Kaggle run has happened.**
Labels used below: **VERIFIED** (read from a primary source or computed from `_hf_meta/config.json` in this workspace), **ASSUMED** (engineering judgement, to be confirmed in Phase 5), **ESTIMATED** (arithmetic on assumptions; not measured), **NOT TESTED LOCALLY** (needs a CUDA GPU and bitsandbytes; pending Phase 5).

## 1. Model and stack
| Item | Value | Label / source |
|---|---|---|
| Model | `Qwen/Qwen3-8B` - the only generative model; `production` configs with any other `model_id` fail validation | VERIFIED (model card, `config.json`: `Qwen3ForCausalLM`, 36 layers, hidden 4096, 32 Q / 8 KV heads, head_dim 128, intermediate 12288, vocab 151936, bf16 weights) |
| transformers | >= 4.51.0 (older raises `KeyError: 'qwen3'`); local tests ran on 5.19.0, peft 0.21.2, trl 1.15.0, torch 2.14.1 (CPU) | VERIFIED (model card; installed versions). Kaggle's preinstalled versions must be checked in Phase 5 - pipeline code uses `dtype=` with a `torch_dtype=` fallback |
| Context | native 32,768; `max_position_embeddings` 40,960 in config; training length capped at **1024** | VERIFIED (card/config); cap justified by measured token stats (see 3) |
| Chat format | ChatML, `enable_thinking=False`, tools via `<tool_call>` / `<tool_response>` | VERIFIED from `tokenizer_config.json` template (see CONTRACT.md) |
| Single-model rule | no second LLM anywhere in the pipeline; the eval agent compares base vs fine-tuned *Qwen3-8B* | design rule |

## 2. Why a custom loop instead of TRL `SFTTrainer`
- We need **assistant-only loss masks** produced by `pipeline.data` (per-assistant-turn samples whose prompt is rendered exactly as at inference with `enable_thinking=False`). `SFTTrainer` formats text itself and its masking options depend on template markers; it would bypass the Data agent's verified rendering and the Qwen3 think-block quirk (last assistant turn gets an empty `<think>` block, earlier ones do not).
- Deterministic resume (same data order from `(seed, epoch, step)`), atomic checkpoints with integrity hashes, and config/dataset identity checks are all explicit in ~300 lines and unit-tested on CPU.
- **Guards added after the independent audit** (all unit-tested on CPU with injected NaN loss/gradients): non-finite loss with no GradScaler aborts before backward; a non-finite gradient without a GradScaler aborts; with a GradScaler a non-finite step is skipped and counted, and the run aborts after `train.max_consecutive_skipped_steps` (default 20) consecutive skips with a message naming the likely causes; `skipped_steps` is logged and stored in every checkpoint's metrics and loop state (restored on resume); after training the L2 norm of the LoRA B matrices (initialised to exactly 0) is recorded and a run whose adapter did not change is **not** reported as a success. Throughput is MEASURED, not assumed: every periodic log line carries `tok_per_s`, and a `first_steps_throughput` event (tokens/s over the first 10 optimizer steps) is written to `metrics.jsonl` so Phase 5 can replace `assumed_tokens_per_sec`.
- **Loss (OOM guard, changed after the Kaggle memory review).** A naive causal-LM loss materialises fp32 logits for every position: with vocab 151,936 that is micro-batch x seq x 151,936 x 4 B (about 2.5 GB at 2 x 2048, plus its gradient) and was estimated OOM-likely on a 16 GB T4 (docs/MEMORY_PLAN.md: 18.3 GiB naive vs 12.5 GiB label-only, ESTIMATED). `masked_lm_loss` therefore runs the transformer body on all tokens, selects the hidden states at **supervised positions only** (about 45 % of tokens carry loss), and applies `lm_head` + fp32 cross-entropy to them in chunks of 1024 positions, each chunk recomputed in backward via `torch.utils.checkpoint`. The result is token-weighted by `num_items_in_batch` (the accumulation group's label-token count), so gradient accumulation equals one big batch. Tests: equals HF's own shifted CE with `num_items_in_batch` (rel 1e-5), loss and gradients are identical for chunk sizes 100000 and 7, and a batch with no labels gives 0. VERIFIED on the tiny model; GPU memory behaviour is ESTIMATED / NOT TESTED LOCALLY.
- TRL stays installed for Phase 5 options (e.g. a later DPO) but is not on the SFT path. LABEL: ASSUMED trade-off; the cost is that fp16/GPU behaviour of this loop is **NOT TESTED LOCALLY**.

## 3. Parameters and reasons
### Quantisation (QLoRA)
| Param | Value | Reason | Label |
|---|---|---|---|
| `load_in_4bit` | true | an 8B model in bf16 is ~16 GB; NF4 weights ~5 GB so it fits one 16 GB T4/P100 with activations | VERIFIED concept (QLoRA, HF docs); numbers ESTIMATED |
| `bnb_4bit_quant_type` | `nf4` | HF docs: "use NF4 for training 4-bit base models" | VERIFIED |
| `bnb_4bit_use_double_quant` | true | extra ~0.4 bit/param saving at no quality cost | VERIFIED |
| compute dtype | `auto` -> bf16 only if the GPU compute capability is **>= 8** (Ampere or newer: A100/L4/A10/H100), else fp16 + GradScaler. `torch.cuda.is_bf16_supported()` is NOT used: it returns True on a T4 (cc 7.5) through emulation (audit finding 1). Unit-tested with monkeypatched capabilities (7,5)->fp16, (6,0)->fp16, (8,0)->bf16, (9,0)->bf16 | T4/P100 have no native bf16 | VERIFIED hardware facts; choice ASSUMED |
| `kbit_prepare` | `manual` (gradient checkpointing + `enable_input_require_grads`, `use_reentrant=False`) | `prepare_model_for_kbit_training` upcasts non-quantised params (embeddings, `lm_head`, ~1.2 B params each ... 151936x4096 = 622 M params each) to fp32: ~+5 GB (ESTIMATED from config dims). `peft` mode is selectable | ASSUMED / ESTIMATED; NOT TESTED LOCALLY |
| device | single GPU `device_map={"": 0}` | simple, avoids naive model-parallel slowdown; the second T4 is unused (data-parallel via `accelerate` is a Phase-5 option) | ASSUMED |
| bitsandbytes | NF4 supported on Pascal (P100) and newer, so T4 and P100 both work | VERIFIED (HF bitsandbytes docs hardware table) | 
| Local status | bitsandbytes/CUDA path **NOT TESTED LOCALLY / PENDING PHASE 5** (no CUDA here; local GPU is a 4 GB RTX 3050) | | |
| GPU choice | **Prefer the T4** (sm_75, fp16 path, tested design). **P100 (sm_60): support in the default Kaggle PyTorch wheels is UNVERIFIED** (recent CUDA wheels may not ship sm_60 kernels; cannot be checked offline), so treat P100 as a fallback only after a quick `torch.cuda` smoke check in the notebook | UNVERIFIED |

### LoRA
| Param | Value | Reason | Label |
|---|---|---|---|
| target modules | `q_proj,k_proj,v_proj,o_proj,gate_proj,up_proj,down_proj` | all linear layers of every block is the QLoRA recommendation; names exist in the HF Qwen3 implementation - the tiny test model built from the same class gets exactly 7 adapters per layer (`test_lora_attaches_to_expected_modules`) | VERIFIED by instantiation of `Qwen3ForCausalLM` |
| rank `r` | 16 | enough capacity for style/behaviour/tool-protocol learning on ~13 k samples without drifting far from base | ASSUMED |
| `alpha` | 32 (alpha/r = 2) | common 2x scaling; with lr 1e-4 keeps updates moderate | ASSUMED |
| dropout | 0.05 | light regularisation for 2 epochs | ASSUMED |
| bias / rslora | none / off | PEFT defaults (VERIFIED defaults r=8, alpha=8, dropout 0.0, bias none, use_rslora false) | |
| trainable params | **43,646,976** (0.53 % of 8.2 B) | computed from config dims: 36 x 16 x (8192+10240+8192+32768+16384) | VERIFIED arithmetic |

### Optimisation
| Param | Value | Reason | Label |
|---|---|---|---|
| optimizer | `torch.optim.AdamW` on LoRA params only | state for 43.6 M fp32 params ~0.5 GB (ESTIMATED), so paged 8-bit optimisers (bitsandbytes) are unnecessary and would add an untestable dependency | ASSUMED |
| learning rate | 1e-4 | QLoRA paper used 2e-4 for <=13 B; lowered for fp16 stability and because the dataset is small and partly templated (overfitting/forgetting risk) | ASSUMED |
| scheduler / warmup | cosine, warmup 3 % | standard; warmup guards early gradient spikes | ASSUMED |
| weight decay | 0.01 | mild | ASSUMED |
| Adam betas / eps | 0.9, 0.999, 1e-8 | defaults | |
| grad clip | 1.0 | fp16 gradient-spike guard (forum reports of fp16 NaNs on T4); non-finite gradients are skipped under the GradScaler and abort in non-scaler runs | ASSUMED (anecdotal reports) |
| epochs | 2 | small curated dataset; >3 epochs risks memorising templates | ASSUMED |
| per-device batch x accumulation | 4 x 4 = **16** sequences | with the label-only chunked loss and length-bucketed batches at max 1024, the Kaggle review ESTIMATES 12.5 GiB peak at micro-batch 4 (fits 16 GB). **OOM ladder** (effective batch stays 16): `--set train.per_device_batch=2 --set train.grad_accum=8`, then 1 x 16. Changing the micro-batch changes the config hash and data order, so a ladder step means a fresh run (or `--force-resume`) | ESTIMATED |
| max sequence length | **1024** | Data agent loader statistics on the real Qwen3 tokenizer: longest per-turn sample ~800 tokens, 0 % truncation at 1024 (about 12.7 k samples and ~1.86 M tokens per epoch, ~38.7 % of tokens supervised, per `reports/data_stats_train_final.md`; recompute from the current frozen files before Phase 5); 1024 is the cheapest safe length for a T4 | VERIFIED on the tokenizer (not a GPU measurement) |
| train_on | `per_turn` | one sample per assistant turn with the prompt rendered exactly as inference; avoids training on a prompt shape the model never sees (Qwen3 inserts `<think>` only on the last assistant turn) | design (Data agent API) |
| group_by_length | true (deterministic mega-batches of 50 x batch) | less padding with identical order on every run | ASSUMED |
| packing | off (validation rejects `packing: true`) | packing would blend examples and break per-turn masks | design |
| gradient checkpointing | on, `use_reentrant=False` | trades ~30 % speed for large activation savings on 16 GB | ASSUMED |
| precision | `auto`: bf16 only for compute capability >= 8 (native), else fp16 + `GradScaler`; LoRA weights fp32 | T4/P100 cannot do bf16 natively; Qwen3-family fp16 overflow reports exist (embedding model NaNs in fp16; a T4 devlog) so a bf16/fp32 fallback is documented | VERIFIED hardware; stability ASSUMED, **NOT TESTED LOCALLY** |
| seed | 42 | recorded in `reproducibility.json`; data order depends only on `(seed, epoch)` | |

### Run control
| Param | Value | Reason | Label |
|---|---|---|---|
| eval interval | 100 optimizer steps (~12 evaluations per run, ESTIMATED 826 steps/epoch from the dry-run on the candidate dataset) | frequent enough to spot divergence, cheap on 516 validation records | ASSUMED |
| checkpoint interval | 100 steps + every new best + final | Kaggle sessions can be killed (documented 12 h limit; weekly GPU quota ~30 h, not an official number - check the account page) | VERIFIED limits per search results (quota unofficial) |
| retention | last 2 + best | a LoRA checkpoint with optimizer state is small; disk (~20 GB persistent, unofficial) is not the constraint | ASSUMED |
| early stopping | patience 4 evals on validation loss, min_delta 0; best checkpoint always retained | stops waste on a diverging/flat run | ASSUMED |
| time projection | `assumed_tokens_per_sec: 250` (the earlier 1200 was wrong: a T4's 65 TFLOP/s bound is ~1,323 tok/s at 100 % utilisation, so 1200 would need ~91 %; QLoRA with dequantisation is far below that). Realistic ESTIMATE 130-400 tok/s => roughly 1.1-3.3 h per epoch; at 250 tok/s the `--dry-run` projects ~3.7 h for 2 epochs | **ESTIMATE ONLY**; Phase 5 must replace it with the rate measured in the first steps | ESTIMATED |

## 3b. Dataset identity (frozen v2_final)
`dataset/v2_final/` carries `MANIFEST.json` (version `qwen_jarvis_dataset_v2_final`, SHA-256 and record count per split, status FROZEN). Before any data is used, `load_data` checks (a) the manifest version equals `data.dataset_version` and (b) the train and validation files hash to the manifest values; any mismatch stops the run ("the frozen dataset was modified"). Every checkpoint's `identity` stores the dataset version, the train/validation hashes **and the SHA-256 of MANIFEST.json**; resume refuses a different dataset unless `--force-resume`. `--dry-run` on the production config reads the frozen files (train 10,298 records, validation 497; tool-protocol records validated in `strict` mode).

### 3c. Production-only validation and integrity rules (audit findings 2, 3, 12, 16)
- Production configs are rejected if `quant.enabled: false` (an unquantised 8B model does not fit 16 GB), `attn_implementation: flash_attention_2` (unsupported on T4/P100; `sdpa` or `eager` only), or a tokenizer location other than the official `Qwen/Qwen3-8B` id or this workspace's verified `_hf_meta` directory.
- **Manifest**: `MANIFEST.json` is located case-insensitively (a lower-case `manifest.json` in a packaged copy still works on Linux). In production mode (and `--dry-run` on the production config) a missing manifest, a version mismatch or a train/validation hash mismatch is an error before any data is rendered. Smoke mode tolerates a missing manifest.
- **Loader**: production and its `--dry-run` require the verified Data-agent loader (`pipeline.data`); an import failure is recorded (`data_adapter.IMPORT_ERROR`) and stops the run. The simplified fallback renderer exists for smoke mode only.
- **Tokenizer fingerprint**: at start the SHA-256 of the chat template, the vocabulary (token to id) and the special tokens are computed and compared with the verified reference (the local `_hf_meta` files when present, else `configs/tokenizer_fingerprint.json`, generated from `_hf_meta`; the audit confirmed the HF-cache copy of Qwen/Qwen3-8B is byte-identical to `_hf_meta`). A mismatch aborts production; both fingerprints are written to `reproducibility.json` together with the resolved precision, the data loader used and the hardware (including `bf16_native`).
- **Fresh run vs resume**: a run started without `--resume` refuses a run directory that already contains checkpoints. Pruning orders checkpoints by SAVE TIME (`saved_ns` in the metadata), never by step number, so checkpoints written in the current session are never deleted in favour of older, higher-numbered ones restored from a previous session; `--resume latest` also means "most recently saved".
- `--dry-run` on the production config uses the local verified `_hf_meta` tokenizer when it exists (no network); otherwise `AutoTokenizer.from_pretrained("Qwen/Qwen3-8B")` fetches tokenizer files only (never weights), which needs network or a warm cache.
- The `--dry-run` sample counts and step plan are **EXACT** (`EXACT_*` keys): assistant messages are counted over the whole train and validation files (train 10,298 records = 12,777 samples; validation 497 = 576; 798 steps per epoch, 1,596 for 2 epochs). Only the mean token length (a seeded random subset of ~256 records), the token total and the hours (at the 250 tok/s assumption) are ESTIMATES (the sampled mean is ~6% below the full-data 146.2, so the hours are ~6% low). Treat `reports/data_stats_train.json` and the measured tokens/s as authoritative.
- **No sample limits in production**: `data.max_train_examples` / `data.max_eval_examples` must be null in production mode (they would silently train on a prefix of the file). Config validation rejects any other value. The only exception is the TEST-ONLY environment variable `QWEN_TEST_ONLY_ALLOW_EXAMPLE_LIMITS=I_AM_TESTING_NOT_TRAINING`, which exists for unit tests of the production code path; never set it for a real run. It does not open the production lock.

## 4. Safe modes and the production lock (B7)
- `--validate-config`: parse + validate only. `--dry-run`: config, tokenizer, a data sample, batch shapes, step/time plan; **never constructs a model** (test monkeypatches model constructors to raise). `--smoke-test`: tiny random Qwen3-architecture model (2 layers, hidden 64; ~19 M parameters, 16,384 trainable), real tokenizer from `_hf_meta`, ~24-64 records of the frozen v2_final dataset (strict-validated, tool-protocol records included), 6 steps, interruption at step 4, resume, eval; refuses non-smoke configs. Banner: `PIPELINE SMOKE TEST - NOT QWEN PRODUCTION TRAINING`.
- Production needs **all of**: env `QWEN_PHASE5_UNLOCK=I_UNDERSTAND_THIS_STARTS_PRODUCTION_TRAINING`, flag `--confirm-production`, `production_locked: false`, `mode: production`. Otherwise exit code 3 and a message naming what is missing. `load_production_model` re-checks (`unlocked=True` is only passed after the check). `configs/qlora_sft.yaml` ships with `production_locked: true`. Importing `train.py` loads no ML library (tested in a subprocess).

## 5. Checkpoints and resume (B8)
- Directory `runs/<run>/checkpoint-<step>/` = `adapter/` (PEFT safetensors), `trainer_state.pt` (optimizer, scheduler, grad scaler, RNG for python/numpy/torch/cuda, loop state), `checkpoint_meta.json` (step, epoch, metrics, identity = model id + dataset version + dataset hash + config hash, library versions, UTC time, SHA-256 + size of every file). `latest.json` / `best.json` pointers, retention = last K + best.
- Atomic: written to `.tmp-checkpoint-N` then renamed; a failure mid-save leaves no partial directory and the pointers untouched (tested with a simulated disk-full).
- Load: verify meta, schema, every file hash; identity mismatch (model id, dataset version/hash, config hash) is an error unless `--force-resume`; `trainer_state.pt` is read with `torch.load(weights_only=True)` so a tampered checkpoint cannot execute code (tested). Hyper-parameters that only affect logging/saving cadence may change between runs; learning rate, LoRA settings, batch size etc. may not.
- Resume re-enters the same `(epoch, offset)` of the deterministic batch plan. Test: an interrupted + resumed run produces **bit-identical adapter weights and validation loss** to an uninterrupted run (CPU fp32, tiny model).

## 6. Reproducibility record (B11)
`repro.snapshot` writes `reproducibility.json` per run: dataset version + SHA-256 of train/validation files + counts, model id, tokenizer, seed, resolved precision, quantisation, library versions, CPU/GPU details, git commit (if available), full config and its hash. Environment variables are never recorded.

## 7. What is verified where
| Capability | Local CPU (this phase) | Needs Kaggle GPU (Phase 5) |
|---|---|---|
| Config load/validation, overrides, lock | verified (tests) | - |
| Tokenisation, per-turn masks, collate, batching | verified (tests with the real Qwen3 tokenizer) | - |
| Forward/backward/optimizer/scheduler/eval/checkpoint/resume | verified on the tiny model | re-verify on 8B |
| LoRA attach to the Qwen3 module names | verified on the tiny model | confirm on the real 8B |
| 4-bit NF4 load, bitsandbytes, fp16 GradScaler, gradient checkpointing on GPU, OOM headroom, throughput, real VRAM | **NOT TESTED LOCALLY / PENDING PHASE 5** | required |
| Loss quality, win/loss versus base | none claimed | after training |
