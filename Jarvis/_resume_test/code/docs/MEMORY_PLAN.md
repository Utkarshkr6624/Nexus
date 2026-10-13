# Memory & time plan - Qwen3-8B QLoRA on Kaggle

> **Every number in this document is ESTIMATED.** Nothing has been MEASURED on a GPU. No Qwen3-8B weights were downloaded, no model was loaded and no Kaggle session was used in Phase 3/4. The local GPU is an **NVIDIA GeForce RTX 3050 Laptop with 4 GB VRAM**: the quantised 8B model alone needs about 5.7 GiB, so Qwen3-8B cannot be trained (or even loaded on the GPU) locally. Kaggle execution is **PENDING PHASE 5**.
> Reproduce this file with `python kaggle/memory_calc.py` (formulas below are the code).

## 1. Parameter count (from the real `_hf_meta/config.json`)

Config: hidden 4096, layers 36, heads 32 (query) / 8 (KV, GQA), head_dim 128, intermediate 12288, vocab 151936, tie_word_embeddings=False.

| item | formula | parameters |
|---|---|--:|
| embed_tokens | vocab x hidden = 151936 x 4096 | 622,329,856 |
| lm_head (untied) | vocab x hidden | 622,329,856 |
| q_proj | hidden x (heads x head_dim) = 4096 x 4096 | 16,777,216 |
| k_proj | hidden x (kv_heads x head_dim) = 4096 x 1024 | 4,194,304 |
| v_proj | same as k | 4,194,304 |
| o_proj | (4096) x hidden | 16,777,216 |
| MLP (gate, up, down) | 3 x hidden x intermediate = 3 x 4096 x 12288 | 150,994,944 |
| norms per layer | 2 x hidden + q_norm/k_norm 2 x head_dim | 8,448 |
| **per layer** | sum | 192,946,432 |
| **36 layers** | | 6,946,071,552 |
| **total** | layers + embed + lm_head + final norm | **8,190,735,360** (~8.19 B) |

## 2. Weight memory with 4-bit NF4 QLoRA

bitsandbytes quantises the decoder `Linear` layers only (q/k/v/o/gate/up/down); `embed_tokens` and `lm_head` stay in 16-bit.

- Quantised linear parameters: 6,945,767,424. NF4 = 0.5 byte/param. Quantisation constants: block size 64; with *double quantisation* the per-block absmax is 8-bit (1/64 byte/param) plus an fp32 constant per 256 blocks (4/(64x256) byte/param) -> about 0.5159 byte/param.
- NF4 weights with double quant: **3.34 GiB** (without double quant, fp32 absmax: 3.64 GiB).
- 16-bit embeddings + lm_head + norms: 2.32 GiB.
- **Total resident base-model weights: 5.66 GiB** (ESTIMATED; 5.96 GiB without double quant).

## 3. LoRA adapter, gradients and optimiser (all 7 projection types)

Trainable parameters per layer = r x [(H+Q) + 2x(H+KV) + (Q+H) + 2x(H+I) + (I+H)] with Q = heads x head_dim = 4096, KV = 1024 -> 75,776 x r per layer, x36 layers.

| r | trainable params | % of base | AdamW (fp32 weight+grad+m+v, 16 B/param) | paged 8-bit AdamW (~10 B/param) | adapter file (fp32) |
|--:|--:|--:|--:|--:|--:|
| 8 | 21,823,488 | 0.27% | 0.33 GiB | 0.20 GiB | 87 MB |
| 16 | 43,646,976 | 0.53% | 0.65 GiB | 0.41 GiB | 175 MB |
| 32 | 87,293,952 | 1.07% | 1.30 GiB | 0.81 GiB | 349 MB |
| 64 | 174,587,904 | 2.13% | 2.60 GiB | 1.63 GiB | 698 MB |

## 4. Activations, logits and fixed overheads

Formulas used (tokens = seq x micro-batch):

- Checkpointed activations (gradient checkpointing on): `layers x tokens x hidden x 2 B` = 36 x tokens x 4096 x 2 = 288 KiB per token.
- One recomputed layer working set: `tokens x (4 x intermediate + 8 x hidden) x 2 B x 2 (fwd+bwd)` = 320 KiB per token. Attention is assumed to use PyTorch SDPA (memory-efficient kernel); the eager O(seq^2) score matrix is *not* included - do not use eager attention. FlashAttention-2 needs Ampere or newer, so it is not available on T4/P100.
- Dequantised weights of one layer (transient): 0.36 GiB.
- **Logits / loss (largest variable term):** vocab = 151936. Naive loss keeps fp16 logits + fp32 upcast + fp32 gradient = 10 B x tokens x 151936 = 1.45 MiB per token (2048 tokens -> 2.9 GiB). A chunked / label-only loss that materialises at most 512 positions at a time caps this at 0.72 GiB. The Training agent's `train.py` should compute logits only for assistant-labelled positions or chunk the loss; the table shows both.
- CUDA context + workspaces 0.6 GiB; misc buffers 0.5 GiB; allocator/fragmentation margin x1.15.
- KV cache: not used in training (use_cache=False). It matters only at evaluation/generation time (36 layers x 2 x 8 KV heads x 128 x 2 B = 144 KiB per token, negligible here).

## 5. Peak VRAM estimate tables (LoRA r = 16, AdamW, gradient checkpointing, fp16 compute) - ESTIMATED

Usable VRAM assumed: **T4 / P100 = 16 GB card, about 14.4 GiB usable**. 'fits' = at least 1.5 GiB headroom, 'TIGHT' = under the limit, 'OOM-likely' = over.

### Naive full-logits loss

| seq | micro-batch | tokens/step | peak GiB (ESTIMATED) | verdict |
|--:|--:|--:|--:|---|
| 1024 | 1 | 1024 | 11.3 | fits |
| 1024 | 2 | 2048 | 13.6 | TIGHT |
| 1024 | 4 | 4096 | 18.3 | OOM-likely |
| 2048 | 1 | 2048 | 13.6 | TIGHT |
| 2048 | 2 | 4096 | 18.3 | OOM-likely |
| 2048 | 4 | 8192 | 27.7 | OOM-likely |
| 3072 | 1 | 3072 | 16.0 | OOM-likely |
| 3072 | 2 | 6144 | 23.0 | OOM-likely |
| 3072 | 4 | 12288 | 37.1 | OOM-likely |

### Chunked / label-only loss (recommended)

| seq | micro-batch | tokens/step | peak GiB (ESTIMATED) | verdict |
|--:|--:|--:|--:|---|
| 1024 | 1 | 1024 | 10.4 | fits |
| 1024 | 2 | 2048 | 11.1 | fits |
| 1024 | 4 | 4096 | 12.5 | fits |
| 2048 | 1 | 2048 | 11.1 | fits |
| 2048 | 2 | 4096 | 12.5 | fits |
| 2048 | 4 | 8192 | 15.2 | OOM-likely |
| 3072 | 1 | 3072 | 11.8 | fits |
| 3072 | 2 | 6144 | 13.9 | TIGHT |
| 3072 | 4 | 12288 | 18.0 | OOM-likely |

Example breakdown (seq 2048, micro-batch 1, r=16, chunked loss; before the 1.15 margin), ESTIMATED:

| term | GiB |
|---|--:|
| weights_nf4_double_quant+fp16_embed_head | 5.66 |
| lora_weights_grads_optimizer | 0.65 |
| checkpointed_activations | 0.56 |
| one_layer_working_set | 0.62 |
| dequantised_layer_transient | 0.36 |
| logits_and_loss | 0.72 |
| cuda_context | 0.60 |
| misc | 0.50 |
| **total with margin** | **11.1** |

Effect of LoRA rank at seq 2048 / micro-batch 1 / chunked loss (ESTIMATED): r=8: 10.8 GiB, r=16: 11.1 GiB, r=32: 11.9 GiB, r=64: 13.4 GiB. The rank changes optimiser memory by under 3 GiB even at r=64, so rank is chosen for quality, not memory; the activations and logits dominate the variable part.

## 6. Dataset size and time per epoch

Tokens counted with the **real Qwen3 `tokenizer.json`** on `dataset/v2_final` (approximate ChatML rendering; the Data agent's `reports/data_stats_*.json` is authoritative when present):

| split | examples | total tokens | assistant tokens | mean | median | p95 | p99 | max | >512 | >1024 | >2048 |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| train | 10,298 | 1,323,179 | 808,495 | 128.5 | 110.0 | 267 | 344 | 676 | 6 | 0 | 0 |
| validation | 497 | 65,778 | 40,217 | 132.4 | 115 | 273 | 358 | 440 | 0 | 0 | 0 |
| test | 493 | 62,507 | 38,008 | 126.8 | 108 | 268 | 337 | 440 | 0 | 0 | 0 |

Because almost no example exceeds 1024 tokens, `max_seq_length = 1024` loses nothing for this dataset and is the memory-cheapest choice; 2048 only adds headroom for later data. Sort/bucket by length (or pack) to avoid padding waste: padded batch tokens can exceed real tokens by 2x or more when lengths are mixed.

**Authoritative figures from the Data agent's loader (`reports/data_stats_train.json (Data agent, source dataset\v2_final\train.jsonl, mode strict)`):** 10,298 train records expand to **12,777 training samples** (multi-turn records are split per assistant turn); 1,867,413 tokens are processed per epoch (mean 146.2, median 110, p95 395, p99 554, max 795); only 39% of those tokens carry loss. Truncation at max_len 1024: 0.0%. Samples that fit in 512 tokens: 98.3%; in 1024: 100.0%. Time estimates below use this per-epoch token count (the per-turn expansion is ~19% more tokens than the plain conversation count).

### Throughput and time (ROUGH ESTIMATE - the only defensible basis is a FLOPs argument, not a measurement)

- Compute per trained token with gradient checkpointing and a frozen quantised base: forward 2N + recompute 2N + activation-gradient backward 2N = 6N = 49 GFLOP (N = 8.19 B; no weight-gradient pass for frozen weights).
- T4 fp16 tensor-core peak is about 65 TFLOP/s. QLoRA with on-the-fly NF4 dequantisation typically uses a small fraction; **assumed utilisation 10%-30% (a guess)** -> about **132-397 tokens/s**. A P100 has no fp16 tensor cores and is expected to be slower; 2x T4 with DDP could approach 2x.
- This range is wide on purpose. Phase 5 must record the real tokens/s from the first logged steps and update these estimates.

| epochs | tokens processed (train samples, no padding) | hours at low end | hours at high end |
|--:|--:|--:|--:|
| 1 | 1,867,413 | 3.9 | 1.3 |
| 2 | 3,734,826 | 7.8 | 2.6 |
| 3 | 5,602,239 | 11.8 | 3.9 |

Add about 5-15% for evaluation passes and checkpoint writes, and more if padding is not controlled. Kaggle GPU quota is documented as roughly 30 h/week and a session limit of about 9-12 h (verify current limits at Phase 5): even the pessimistic end for 2 epochs should be split across sessions with resume.

## 7. Batch-size and gradient-accumulation strategy

- Target **effective batch = 16 sequences** (about 16 x ~125 mean tokens, i.e. roughly two thousand real tokens per optimiser step) - about 798 optimiser steps per epoch on 12,777 training samples, large enough for stable LoRA updates.
- Micro-batch 4 with length-bucketed batches at max_seq 1024 and chunked loss -> accumulation 4; if the OOM ladder fires, micro-batch 2 x accumulation 8, then micro-batch 1 x accumulation 16 (the effective batch never changes, so results stay comparable).
- Scale the learning rate with the effective batch, not the micro-batch; log tokens/step because the number of real tokens varies per step.
- For dual T4 with DDP, effective batch = micro-batch x accumulation x 2 GPUs: halve the accumulation to keep 16.

## 8. Dual T4 (2 x 16 GB): `device_map` vs DDP

| option | how | pros | cons |
|---|---|---|---|
| **A. Single GPU (recommended first)** | load the model on cuda:0, leave the second GPU idle | simplest, most robust, no distributed pitfalls; ~6 GiB weights leave ample room | no speed-up |
| B. `device_map="auto"` (model split by layers across 2 GPUs) | one process, naive pipeline | trivial to enable, more headroom for larger seq/micro-batch | GPUs run **sequentially** (no speed-up), cross-GPU activation copies, fragile with gradient checkpointing and bnb in some versions |
| C. DDP (`accelerate launch --num_processes 2`, `device_map={"": local_rank}`) | one full 4-bit copy per GPU (~6 GiB each) | up to ~2x throughput | needs a script launch (not a notebook cell), `ddp_find_unused_parameters=False`, `use_reentrant=False` checkpointing, seeds/IO on rank 0 only; more failure modes |

**Recommendation:** run option A for the first full run (the dataset is small: ~1.9 M tokens/epoch), and only consider C if measured tokens/s makes 2 epochs infeasible within the quota. Option B gives no speed-up and is not recommended. **Prefer a T4 over a P100:** the P100 is compute capability sm_60 and its support in the current default Kaggle PyTorch (and bitsandbytes) wheels is UNVERIFIED; the T4 (sm_75) is the safer choice.

## 9. fp16 vs bf16

- **T4 (sm_75) and P100 (sm_60) have no native bf16** (native bf16 needs compute capability >= 8). `torch.cuda.is_bf16_supported()` returns True on a T4 only because bf16 is *emulated* there (slow), so the precision must be chosen from `torch.cuda.get_device_capability()[0] >= 8`, not from that call. Use fp16 compute (`bnb_4bit_compute_dtype=torch.float16`, `fp16=True` with a GradScaler); keep LoRA weights and optimiser states in fp32.
- Qwen3 was trained in bf16; fp16 has a narrower range, so activation overflow (inf/NaN loss) is a real risk. This is **not verified** - Phase 5's first dry steps must watch for NaN/inf loss and the grad-scaler skipping steps. Fallbacks, in order: lower the learning rate; keep sensitive modules (norms, lm_head) in fp32; run on an Ampere-class GPU if one is available (bf16, FlashAttention-2).
- On Ampere or newer (A100/L4/RTX30xx+), prefer bf16.

## 10. OOM fallback ladder

Apply in this order and record each step in the run log (all estimated; first one that fits wins):

1. Confirm chunked / label-only loss and SDPA attention are active; `use_cache=False`; gradient checkpointing on (`use_reentrant=False`).
2. Micro-batch 4 -> 2 -> 1 (accumulation 4 -> 8 -> 16).
3. `max_seq_length` 2048 -> 1024 -> 768 (this dataset has almost nothing above 1024 tokens; truncation is logged by the loader).
4. `paged_adamw_8bit` optimiser (saves about 6 bytes per trainable parameter: 16 -> ~10 B/param).
5. LoRA rank 64 -> 32 -> 16 -> 8, or fewer target modules (attention-only).
6. Set `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`; `torch.cuda.empty_cache()` between eval and train.
7. Last resort: CPU offload of optimiser states (slow) - or switch to a smaller model only by a new explicit decision (not allowed under the current plan: Qwen3-8B is the model).

## 11. Disk, RAM and session strategy

- **Base weights:** Qwen3-8B bf16 safetensors are about 16.4 GB (8.19 B x 2 B). Let the Hugging Face cache live in the default location (or `/kaggle/temp`), **not** in `/kaggle/working`, so it is not counted as output. Download needs internet enabled for that session only.
- **/kaggle/working is limited (documented ~20 GB; verify) and only its contents are saved as output.** Save **LoRA adapters only**: r=16 adapter = about 175 MB fp32; a resumable checkpoint with optimiser state is about 3x that (~0.5 GB). Keep the last 2-3 checkpoints plus the best (<= ~2.5 GB). Do not write merged 16 GB models to /kaggle/working.
- **Host RAM:** 4-bit loading streams shards (largest shard about 4 GB); peak CPU RAM is expected under ~10 GB (ESTIMATED). Merging the adapter into a full bf16 model on CPU needs about 17 GB RAM and 16 GB disk - optional, do it outside /kaggle/working or skip it.
- **Sessions can be killed or time out.** Checkpoint every N steps (config), write `checkpoint_meta.json`, and at the end of each session upload `/kaggle/working/runs/...` as a Kaggle dataset (or download it) so the next session can mount it under `/kaggle/input/...` and resume with `--resume latest`. Never rely on session state.
- **Secrets:** a Hugging Face token (only if needed) goes in Kaggle Secrets and is read through `kaggle_secrets.UserSecretsClient`; it is never printed, hard-coded, or written to the package. Qwen3-8B is an open-weights model and normally needs no token.

## 12. Check of the production config (`configs/qlora_sft.yaml`) against these estimates

The locked production config (read from the file when this document is generated) uses max_seq_len 1024, micro-batch 4, accumulation 4 (effective batch 16), r=16, `adamw_torch`, gradient checkpointing. ESTIMATED peak: **12.5 GiB with the label-only chunked loss that `train.py` implements (fits)** versus **18.3 GiB if a naive full-logits loss were used (OOM-likely)**. The chunked loss is therefore required, not optional. OOM fallback via `--set`: micro-batch 2 x accumulation 8: 11.1 GiB; micro-batch 1 x accumulation 16: 10.4 GiB (chunked).

`assumed_tokens_per_sec` in the config is 250 (used only for the dry-run time projection). It is a mid-range ESTIMATE, not a measurement: at 100% utilisation of a T4's 65 TFLOP/s the FLOPs argument gives an upper bound of 1323 tokens/s, and QLoRA with on-the-fly dequantisation reaches only a fraction of it. The realistic ESTIMATE is 132-397 tokens/s on a T4, i.e. about 2.6-7.8 h for the configured 2 epochs over 1,867,413 tokens/epoch. The notebook does not compute throughput itself: it streams the trainer output, and the trainer's periodic log lines carry `tok_per_s` and a `first_steps_throughput` event is written after 10 steps (verified in `pipeline/train/train.py`). The estimate is replaced by a measurement only once Phase 5 has really run.

## 13. What is verified vs estimated vs pending

| item | status |
|---|---|
| Architecture numbers (layers, hidden, heads, vocab, intermediate) | **verified** from the real `config.json` |
| Parameter count and NF4 sizes | computed (**estimate** of the on-device footprint) |
| Peak VRAM tables | **ESTIMATED** (no GPU measurement) |
| Tokens/s and hours | **ROUGH ESTIMATE** (FLOPs argument, assumed utilisation) |
| Dataset token counts | computed with the real tokenizer on the stored dataset (approximate rendering) |
| Kaggle quota / disk / RAM limits | per Kaggle documentation as remembered; **verify at Phase 5** |
| fp16 stability of Qwen3 | **not tested** |
| Library versions on Kaggle (transformers / peft / bitsandbytes / torch) | **UNVERIFIED until Phase 5** - all local tests ran on CPU with transformers 5.19.0, peft 0.21.2, trl 1.15.0, torch 2.14.1; bitsandbytes never ran |
| P100 (sm_60) support in current Kaggle wheels | **UNVERIFIED** - prefer T4 |
| Any Kaggle execution | **PENDING PHASE 5** |
