"""Computes ESTIMATED memory / time figures for Qwen3-8B QLoRA from the real config.json and writes docs/MEMORY_PLAN.md
and kaggle/memory_estimates.json.  Nothing here is MEASURED: no GPU run has happened (the local GPU is a 4 GB RTX 3050 Laptop).
Stdlib only, plus `tokenizers` (optional; used only to count tokens of the dataset with the real Qwen3 tokenizer.json).
usage: python kaggle/memory_calc.py"""
import json
import statistics
from pathlib import Path

WS = Path(__file__).resolve().parent.parent
HF = WS / "_hf_meta"
cfg = json.load(open(HF / "config.json", encoding="utf-8"))

H = cfg["hidden_size"]; L = cfg["num_hidden_layers"]; I = cfg["intermediate_size"]; V = cfg["vocab_size"]
NH = cfg["num_attention_heads"]; NKV = cfg["num_key_value_heads"]; HD = cfg["head_dim"]
GiB = 2 ** 30

# ---------------------------------------------------------------- parameter count (formulas) ------------------
q = H * (NH * HD); k = H * (NKV * HD); v = H * (NKV * HD); o = (NH * HD) * H
mlp = 3 * H * I
norms_layer = 2 * H + 2 * HD            # input_layernorm, post_attention_layernorm, q_norm, k_norm (Qwen3)
lin_layer = q + k + v + o + mlp         # weights that bitsandbytes quantises
layer = lin_layer + norms_layer
emb = V * H
lm_head = 0 if cfg["tie_word_embeddings"] else V * H
total = L * layer + emb + lm_head + H   # + final norm
lin_total = L * lin_layer
P = {"embed_tokens": emb, "lm_head": lm_head, "per_layer_linear": lin_layer, "per_layer_total": layer, "linear_total": lin_total, "total": total}

# NF4: 4 bit = 0.5 B/param; blocksize 64 -> one absmax per 64 params. double quant: absmax stored 8-bit (1 B / 64 params)
# plus an fp32 constant per 256 absmax values (4 B / (64*256) params).  Without double quant absmax is fp32 (4 B / 64 params).
nf4_dq = lin_total * (0.5 + 1 / 64 + 4 / (64 * 256))
nf4_nodq = lin_total * (0.5 + 4 / 64)
fp16_rest = (emb + lm_head) * 2 + (L * norms_layer + H) * 2     # embeddings and lm_head stay 16-bit (not quantised by HF/bnb)
weights_dq = nf4_dq + fp16_rest
weights_nodq = nf4_nodq + fp16_rest

# ---------------------------------------------------------------- LoRA ---------------------------------------------
def lora_params(r):
    per_layer = r * ((H + NH * HD) + (H + NKV * HD) * 2 + (NH * HD + H) + 2 * (H + I) + (I + H))   # q,k,v,o,gate,up,down
    return per_layer * L


LORA_R = [8, 16, 32, 64]
ADAMW_BYTES = 4 + 4 + 8          # fp32 adapter weight + fp32 grad + AdamW m,v (fp32)
ADAM8_BYTES = 4 + 4 + 2          # paged_adamw_8bit: states ~2 B / param

# ---------------------------------------------------------------- per-step terms -----------------------------------
CUDA_CTX = 0.6 * GiB             # CUDA context + cuBLAS/cuDNN workspaces (0.3-1 GB typical)
MISC = 0.5 * GiB                 # bnb temp buffers, allocator slack, misc tensors
DEQ = lin_layer * 2              # one layer's dequantised fp16 weights (transient in fwd/bwd)
MARGIN = 1.15                    # fragmentation / allocator margin


def activations(tokens):
    ckpt = L * tokens * H * 2                                    # gradient checkpointing: one fp16 hidden state per layer
    work = tokens * ((4 * I + 8 * H) * 2) * 2                    # one layer recomputed: ~(4*I + 8*H) elements/token, x2 for backward
    return ckpt, work


def logits_bytes(tokens, chunked):
    t = min(tokens, 512) if chunked else tokens                  # chunked loss: only 512 positions of logits live at a time
    return t * V * (2 + 4 + 4)                                   # fp16 logits + fp32 upcast + fp32 grad (softmax recomputed)


def peak(seq, mb, r, chunked, adam8=False):
    tokens = seq * mb
    ckpt, work = activations(tokens)
    lora = lora_params(r) * (ADAM8_BYTES if adam8 else ADAMW_BYTES)
    parts = {"weights_nf4_double_quant+fp16_embed_head": weights_dq, "lora_weights_grads_optimizer": lora, "checkpointed_activations": ckpt,
             "one_layer_working_set": work, "dequantised_layer_transient": DEQ, "logits_and_loss": logits_bytes(tokens, chunked),
             "cuda_context": CUDA_CTX, "misc": MISC}
    raw = sum(parts.values())
    return raw * MARGIN / GiB, {k_: v_ / GiB for k_, v_ in parts.items()}


def verdict(gib, usable=14.4):
    return "fits" if gib <= usable - 1.5 else ("TIGHT" if gib <= usable else "OOM-likely")


# ---------------------------------------------------------------- dataset tokens -----------------------------------
def count_tokens():
    try:
        from tokenizers import Tokenizer
    except Exception:
        return None
    tok = Tokenizer.from_file(str(HF / "tokenizer.json"))
    src = WS / "dataset" / "v2_final"
    src_label = "dataset/v2_final"
    if not (src / "train.jsonl").exists():
        src = WS.parent / "qwen_jarvis_phase2" / "dataset" / "splits"
        src_label = "qwen_jarvis_phase2/dataset/splits (v2, before preflight/tool conversion)"
    out = {"source": src_label, "note": "approximate ChatML rendering (no tools block, empty think block on last assistant turn); the Data agent's render is authoritative"}
    for split in ("train", "validation", "test"):
        lens, asst = [], []
        for line in open(src / f"{split}.jsonl", encoding="utf-8"):
            rec = json.loads(line)
            text, a_tokens = "", 0
            msgs = rec["messages"]
            for i, m in enumerate(msgs):
                c = m["content"] or ""
                if m["role"] == "assistant" and i == len(msgs) - 1:
                    c = "<think>\n\n</think>\n\n" + c
                seg = f"<|im_start|>{m['role']}\n{c}<|im_end|>\n"
                n = len(tok.encode(seg).ids)
                text += seg
                if m["role"] == "assistant":
                    a_tokens += n
            lens.append(len(tok.encode(text).ids))
            asst.append(a_tokens)
        lens_sorted = sorted(lens)
        out[split] = {"examples": len(lens), "total_tokens": sum(lens), "assistant_tokens": sum(asst), "mean": round(statistics.mean(lens), 1),
                      "median": statistics.median(lens), "p95": lens_sorted[int(0.95 * len(lens))], "p99": lens_sorted[int(0.99 * len(lens))], "max": max(lens),
                      "over_512": sum(1 for x in lens if x > 512), "over_1024": sum(1 for x in lens if x > 1024), "over_2048": sum(1 for x in lens if x > 2048)}
    return out


tok_stats = count_tokens()
# The Data agent's authoritative report (real render, per-assistant-turn samples) supersedes the approximate count above when it exists.
data_stats = {}
for _split in ("train", "validation", "test"):
    _p = WS / "reports" / f"data_stats_{_split}.json"
    if _p.exists():
        data_stats[_split] = json.load(open(_p, encoding="utf-8"))
if "train" in data_stats:
    train_tokens = data_stats["train"]["per_sample_tokens"]["total"]
    train_samples = data_stats["train"]["samples"]
    train_tokens_source = f"reports/data_stats_train.json (Data agent, source {data_stats['train'].get('source', {}).get('path', '?')}, mode {data_stats['train'].get('source', {}).get('mode', '?')})"
else:
    train_tokens = tok_stats["train"]["total_tokens"] if tok_stats else 1_450_000
    train_samples = tok_stats["train"]["examples"] if tok_stats else 10_000
    train_tokens_source = "approximate ChatML count with the real tokenizer (Data agent report not found)"

# ---------------------------------------------------------------- throughput / time (ROUGH ESTIMATE) ----------------
FLOPS_PER_TOKEN = 6 * total      # fwd (2N) + recompute fwd (2N) + dgrad bwd (2N); no weight-grad for frozen base
T4_FP16_PEAK = 65e12
MFU = (0.10, 0.30)               # assumed model-FLOPs utilisation range for bnb-dequant QLoRA on a T4: a guess, NOT a measurement
tps = [T4_FP16_PEAK * m / FLOPS_PER_TOKEN for m in MFU]

# ---------------------------------------------------------------- write ---------------------------------------------
est = {"label": "ESTIMATED - nothing measured on a GPU", "params": P, "weights_gib": {"nf4_double_quant": weights_dq / GiB, "nf4_no_double_quant": weights_nodq / GiB},
       "lora": {str(r): {"params": lora_params(r), "gib_adamw": lora_params(r) * ADAMW_BYTES / GiB, "gib_adam8bit": lora_params(r) * ADAM8_BYTES / GiB} for r in LORA_R},
       "tokens": tok_stats, "tokens_per_sec_estimate": tps, "assumed_mfu": MFU}
grid = {}
for chunked in (False, True):
    for r in (16,):
        for seq in (1024, 2048, 3072):
            for mb in (1, 2, 4):
                g, _ = peak(seq, mb, r, chunked)
                grid[f"chunked={chunked},r={r},seq={seq},mb={mb}"] = round(g, 2)
est["peak_gib_grid"] = grid
json.dump(est, open(WS / "kaggle" / "memory_estimates.json", "w", encoding="utf-8"), indent=1)

W = []
w = W.append
w("# Memory & time plan - Qwen3-8B QLoRA on Kaggle\n")
w("> **Every number in this document is ESTIMATED.** Nothing has been MEASURED on a GPU. No Qwen3-8B weights were downloaded, no model was loaded and no Kaggle "
  "session was used in Phase 3/4. The local GPU is an **NVIDIA GeForce RTX 3050 Laptop with 4 GB VRAM**: the quantised 8B model alone needs about "
  f"{weights_dq / GiB:.1f} GiB, so Qwen3-8B cannot be trained (or even loaded on the GPU) locally. Kaggle execution is **PENDING PHASE 5**.\n"
  "> Reproduce this file with `python kaggle/memory_calc.py` (formulas below are the code).\n")
w("## 1. Parameter count (from the real `_hf_meta/config.json`)\n")
w(f"Config: hidden {H}, layers {L}, heads {NH} (query) / {NKV} (KV, GQA), head_dim {HD}, intermediate {I}, vocab {V}, tie_word_embeddings={cfg['tie_word_embeddings']}.\n")
w("| item | formula | parameters |\n|---|---|--:|")
w(f"| embed_tokens | vocab x hidden = {V} x {H} | {emb:,} |")
w(f"| lm_head (untied) | vocab x hidden | {lm_head:,} |")
w(f"| q_proj | hidden x (heads x head_dim) = {H} x {NH * HD} | {q:,} |")
w(f"| k_proj | hidden x (kv_heads x head_dim) = {H} x {NKV * HD} | {k:,} |")
w(f"| v_proj | same as k | {v:,} |")
w(f"| o_proj | ({NH * HD}) x hidden | {o:,} |")
w(f"| MLP (gate, up, down) | 3 x hidden x intermediate = 3 x {H} x {I} | {mlp:,} |")
w(f"| norms per layer | 2 x hidden + q_norm/k_norm 2 x head_dim | {norms_layer:,} |")
w(f"| **per layer** | sum | {layer:,} |")
w(f"| **{L} layers** | | {L * layer:,} |")
w(f"| **total** | layers + embed + lm_head + final norm | **{total:,}** (~{total / 1e9:.2f} B) |\n")
w("## 2. Weight memory with 4-bit NF4 QLoRA\n")
w("bitsandbytes quantises the decoder `Linear` layers only (q/k/v/o/gate/up/down); `embed_tokens` and `lm_head` stay in 16-bit.\n")
w(f"- Quantised linear parameters: {lin_total:,}. NF4 = 0.5 byte/param. Quantisation constants: block size 64; with *double quantisation* the per-block absmax is 8-bit (1/64 byte/param) plus an fp32 constant per 256 blocks (4/(64x256) byte/param) -> about {0.5 + 1 / 64 + 4 / (64 * 256):.4f} byte/param.")
w(f"- NF4 weights with double quant: **{nf4_dq / GiB:.2f} GiB** (without double quant, fp32 absmax: {nf4_nodq / GiB:.2f} GiB).")
w(f"- 16-bit embeddings + lm_head + norms: {fp16_rest / GiB:.2f} GiB.")
w(f"- **Total resident base-model weights: {weights_dq / GiB:.2f} GiB** (ESTIMATED; {weights_nodq / GiB:.2f} GiB without double quant).\n")
w("## 3. LoRA adapter, gradients and optimiser (all 7 projection types)\n")
w("Trainable parameters per layer = r x [(H+Q) + 2x(H+KV) + (Q+H) + 2x(H+I) + (I+H)] with Q = heads x head_dim = 4096, KV = 1024 -> 75,776 x r per layer, x36 layers.\n")
w("| r | trainable params | % of base | AdamW (fp32 weight+grad+m+v, 16 B/param) | paged 8-bit AdamW (~10 B/param) | adapter file (fp32) |\n|--:|--:|--:|--:|--:|--:|")
for r in LORA_R:
    n = lora_params(r)
    w(f"| {r} | {n:,} | {100 * n / total:.2f}% | {n * ADAMW_BYTES / GiB:.2f} GiB | {n * ADAM8_BYTES / GiB:.2f} GiB | {n * 4 / 1e6:.0f} MB |")
w("")
w("## 4. Activations, logits and fixed overheads\n")
w("Formulas used (tokens = seq x micro-batch):\n")
w(f"- Checkpointed activations (gradient checkpointing on): `layers x tokens x hidden x 2 B` = {L} x tokens x {H} x 2 = {L * H * 2 / 1024:.0f} KiB per token.")
w(f"- One recomputed layer working set: `tokens x (4 x intermediate + 8 x hidden) x 2 B x 2 (fwd+bwd)` = {((4 * I + 8 * H) * 4) / 1024:.0f} KiB per token. Attention is assumed to use PyTorch SDPA (memory-efficient kernel); the eager O(seq^2) score matrix is *not* included - do not use eager attention. FlashAttention-2 needs Ampere or newer, so it is not available on T4/P100.")
w(f"- Dequantised weights of one layer (transient): {DEQ / GiB:.2f} GiB.")
w(f"- **Logits / loss (largest variable term):** vocab = {V}. Naive loss keeps fp16 logits + fp32 upcast + fp32 gradient = 10 B x tokens x {V} = {10 * V / 1024 / 1024:.2f} MiB per token (2048 tokens -> {2048 * 10 * V / GiB:.1f} GiB). A chunked / label-only loss that materialises at most 512 positions at a time caps this at {512 * 10 * V / GiB:.2f} GiB. The Training agent's `train.py` should compute logits only for assistant-labelled positions or chunk the loss; the table shows both.")
w(f"- CUDA context + workspaces {CUDA_CTX / GiB:.1f} GiB; misc buffers {MISC / GiB:.1f} GiB; allocator/fragmentation margin x{MARGIN}.")
w("- KV cache: not used in training (use_cache=False). It matters only at evaluation/generation time (36 layers x 2 x 8 KV heads x 128 x 2 B = 144 KiB per token, negligible here).\n")
w("## 5. Peak VRAM estimate tables (LoRA r = 16, AdamW, gradient checkpointing, fp16 compute) - ESTIMATED\n")
w("Usable VRAM assumed: **T4 / P100 = 16 GB card, about 14.4 GiB usable**. 'fits' = at least 1.5 GiB headroom, 'TIGHT' = under the limit, 'OOM-likely' = over.\n")
for chunked in (False, True):
    w(f"### {'Chunked / label-only loss (recommended)' if chunked else 'Naive full-logits loss'}\n")
    w("| seq | micro-batch | tokens/step | peak GiB (ESTIMATED) | verdict |\n|--:|--:|--:|--:|---|")
    for seq in (1024, 2048, 3072):
        for mb in (1, 2, 4):
            g, _ = peak(seq, mb, 16, chunked)
            w(f"| {seq} | {mb} | {seq * mb} | {g:.1f} | {verdict(g)} |")
    w("")
g, parts = peak(2048, 1, 16, True)
w("Example breakdown (seq 2048, micro-batch 1, r=16, chunked loss; before the 1.15 margin), ESTIMATED:\n")
w("| term | GiB |\n|---|--:|")
for k_, v_ in parts.items():
    w(f"| {k_} | {v_:.2f} |")
w(f"| **total with margin** | **{g:.1f}** |\n")
w("Effect of LoRA rank at seq 2048 / micro-batch 1 / chunked loss (ESTIMATED): " + ", ".join(f"r={r}: {peak(2048, 1, r, True)[0]:.1f} GiB" for r in LORA_R) + ". "
  "The rank changes optimiser memory by under 3 GiB even at r=64, so rank is chosen for quality, not memory; the activations and logits dominate the variable part.\n")
w("## 6. Dataset size and time per epoch\n")
if tok_stats:
    w(f"Tokens counted with the **real Qwen3 `tokenizer.json`** on `{tok_stats['source']}` (approximate ChatML rendering; the Data agent's `reports/data_stats_*.json` is authoritative when present):\n")
    w("| split | examples | total tokens | assistant tokens | mean | median | p95 | p99 | max | >512 | >1024 | >2048 |\n|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|")
    for s in ("train", "validation", "test"):
        d = tok_stats[s]
        w(f"| {s} | {d['examples']:,} | {d['total_tokens']:,} | {d['assistant_tokens']:,} | {d['mean']} | {d['median']} | {d['p95']} | {d['p99']} | {d['max']} | {d['over_512']} | {d['over_1024']} | {d['over_2048']} |")
    w("")
    w("Because almost no example exceeds 1024 tokens, `max_seq_length = 1024` loses nothing for this dataset and is the memory-cheapest choice; 2048 only adds headroom for later data. "
  "Sort/bucket by length (or pack) to avoid padding waste: padded batch tokens can exceed real tokens by 2x or more when lengths are mixed.\n")
else:
    w("`tokenizers` was not importable when this file was generated; the figure used below (~1.45 M train tokens) is a rough prior.\n")
if "train" in data_stats:
    st = data_stats["train"]
    ps = st["per_sample_tokens"]
    w(f"**Authoritative figures from the Data agent's loader (`{train_tokens_source}`):** {st['records']:,} train records expand to **{st['samples']:,} training samples** "
      f"(multi-turn records are split per assistant turn); {ps['total']:,} tokens are processed per epoch (mean {ps['mean']}, median {ps['median']}, p95 {ps['p95']}, p99 {ps['p99']}, max {ps['max']}); "
      f"only {st['label_tokens']['ratio_of_all_tokens'] * 100:.0f}% of those tokens carry loss. Truncation at max_len {st['max_len']}: {st['truncation_pct_at_max_len']}%. "
      f"Samples that fit in 512 tokens: {st['samples_fit_pct']['512']}%; in 1024: {st['samples_fit_pct']['1024']}%. "
      "Time estimates below use this per-epoch token count (the per-turn expansion is ~19% more tokens than the plain conversation count).\n")
w("### Throughput and time (ROUGH ESTIMATE - the only defensible basis is a FLOPs argument, not a measurement)\n")
w(f"- Compute per trained token with gradient checkpointing and a frozen quantised base: forward 2N + recompute 2N + activation-gradient backward 2N = 6N = {FLOPS_PER_TOKEN / 1e9:.0f} GFLOP (N = {total / 1e9:.2f} B; no weight-gradient pass for frozen weights).")
w(f"- T4 fp16 tensor-core peak is about 65 TFLOP/s. QLoRA with on-the-fly NF4 dequantisation typically uses a small fraction; **assumed utilisation {int(MFU[0] * 100)}%-{int(MFU[1] * 100)}% (a guess)** -> about **{tps[0]:.0f}-{tps[1]:.0f} tokens/s**. A P100 has no fp16 tensor cores and is expected to be slower; 2x T4 with DDP could approach 2x.")
w("- This range is wide on purpose. Phase 5 must record the real tokens/s from the first logged steps and update these estimates.\n")
w("| epochs | tokens processed (train samples, no padding) | hours at low end | hours at high end |\n|--:|--:|--:|--:|")
for e in (1, 2, 3):
    n = train_tokens * e
    w(f"| {e} | {n:,} | {n / tps[0] / 3600:.1f} | {n / tps[1] / 3600:.1f} |")
w("\nAdd about 5-15% for evaluation passes and checkpoint writes, and more if padding is not controlled. Kaggle GPU quota is documented as roughly 30 h/week and a session limit of about 9-12 h (verify current limits at Phase 5): "
  "even the pessimistic end for 2 epochs should be split across sessions with resume.\n")
w("## 7. Batch-size and gradient-accumulation strategy\n")
w(f"- Target **effective batch = 16 sequences** (about 16 x ~125 mean tokens, i.e. roughly two thousand real tokens per optimiser step) - about {train_samples // 16} optimiser steps per epoch on {train_samples:,} training samples, large enough for stable LoRA updates.")
w("- Micro-batch 4 with length-bucketed batches at max_seq 1024 and chunked loss -> accumulation 4; if the OOM ladder fires, micro-batch 2 x accumulation 8, then micro-batch 1 x accumulation 16 (the effective batch never changes, so results stay comparable).")
w("- Scale the learning rate with the effective batch, not the micro-batch; log tokens/step because the number of real tokens varies per step.")
w("- For dual T4 with DDP, effective batch = micro-batch x accumulation x 2 GPUs: halve the accumulation to keep 16.\n")
w("## 8. Dual T4 (2 x 16 GB): `device_map` vs DDP\n")
w("| option | how | pros | cons |\n|---|---|---|---|")
w("| **A. Single GPU (recommended first)** | load the model on cuda:0, leave the second GPU idle | simplest, most robust, no distributed pitfalls; ~6 GiB weights leave ample room | no speed-up |")
w("| B. `device_map=\"auto\"` (model split by layers across 2 GPUs) | one process, naive pipeline | trivial to enable, more headroom for larger seq/micro-batch | GPUs run **sequentially** (no speed-up), cross-GPU activation copies, fragile with gradient checkpointing and bnb in some versions |")
w("| C. DDP (`accelerate launch --num_processes 2`, `device_map={\"\": local_rank}`) | one full 4-bit copy per GPU (~6 GiB each) | up to ~2x throughput | needs a script launch (not a notebook cell), `ddp_find_unused_parameters=False`, `use_reentrant=False` checkpointing, seeds/IO on rank 0 only; more failure modes |\n")
w("**Recommendation:** run option A for the first full run (the dataset is small: ~1.9 M tokens/epoch), and only consider C if measured tokens/s makes 2 epochs infeasible within the quota. Option B gives no speed-up and is not recommended. **Prefer a T4 over a P100:** the P100 is compute capability sm_60 and its support in the current default Kaggle PyTorch (and bitsandbytes) wheels is UNVERIFIED; the T4 (sm_75) is the safer choice.\n")
w("## 9. fp16 vs bf16\n")
w("- **T4 (sm_75) and P100 (sm_60) have no native bf16** (native bf16 needs compute capability >= 8). `torch.cuda.is_bf16_supported()` returns True on a T4 only because bf16 is *emulated* there (slow), so the precision must be chosen from `torch.cuda.get_device_capability()[0] >= 8`, not from that call. Use fp16 compute (`bnb_4bit_compute_dtype=torch.float16`, `fp16=True` with a GradScaler); keep LoRA weights and optimiser states in fp32.")
w("- Qwen3 was trained in bf16; fp16 has a narrower range, so activation overflow (inf/NaN loss) is a real risk. This is **not verified** - Phase 5's first dry steps must watch for NaN/inf loss and the grad-scaler skipping steps. Fallbacks, in order: lower the learning rate; keep sensitive modules (norms, lm_head) in fp32; run on an Ampere-class GPU if one is available (bf16, FlashAttention-2).")
w("- On Ampere or newer (A100/L4/RTX30xx+), prefer bf16.\n")
w("## 10. OOM fallback ladder\n")
w("Apply in this order and record each step in the run log (all estimated; first one that fits wins):\n")
w("1. Confirm chunked / label-only loss and SDPA attention are active; `use_cache=False`; gradient checkpointing on (`use_reentrant=False`).")
w("2. Micro-batch 4 -> 2 -> 1 (accumulation 4 -> 8 -> 16).")
w("3. `max_seq_length` 2048 -> 1024 -> 768 (this dataset has almost nothing above 1024 tokens; truncation is logged by the loader).")
w("4. `paged_adamw_8bit` optimiser (saves about 6 bytes per trainable parameter: 16 -> ~10 B/param).")
w("5. LoRA rank 64 -> 32 -> 16 -> 8, or fewer target modules (attention-only).")
w("6. Set `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`; `torch.cuda.empty_cache()` between eval and train.")
w("7. Last resort: CPU offload of optimiser states (slow) - or switch to a smaller model only by a new explicit decision (not allowed under the current plan: Qwen3-8B is the model).\n")
w("## 11. Disk, RAM and session strategy\n")
w("- **Base weights:** Qwen3-8B bf16 safetensors are about 16.4 GB (8.19 B x 2 B). Let the Hugging Face cache live in the default location (or `/kaggle/temp`), **not** in `/kaggle/working`, so it is not counted as output. Download needs internet enabled for that session only.")
w("- **/kaggle/working is limited (documented ~20 GB; verify) and only its contents are saved as output.** Save **LoRA adapters only**: r=16 adapter = about 175 MB fp32; a resumable checkpoint with optimiser state is about 3x that (~0.5 GB). Keep the last 2-3 checkpoints plus the best (<= ~2.5 GB). Do not write merged 16 GB models to /kaggle/working.")
w("- **Host RAM:** 4-bit loading streams shards (largest shard about 4 GB); peak CPU RAM is expected under ~10 GB (ESTIMATED). Merging the adapter into a full bf16 model on CPU needs about 17 GB RAM and 16 GB disk - optional, do it outside /kaggle/working or skip it.")
w("- **Sessions can be killed or time out.** Checkpoint every N steps (config), write `checkpoint_meta.json`, and at the end of each session upload `/kaggle/working/runs/...` as a Kaggle dataset (or download it) so the next session can mount it under `/kaggle/input/...` and resume with `--resume latest`. Never rely on session state.")
w("- **Secrets:** a Hugging Face token (only if needed) goes in Kaggle Secrets and is read through `kaggle_secrets.UserSecretsClient`; it is never printed, hard-coded, or written to the package. Qwen3-8B is an open-weights model and normally needs no token.\n")
import yaml
_cfg = yaml.safe_load(open(WS / "configs" / "qlora_sft.yaml", encoding="utf-8"))
_seq, _mb, _ga = _cfg["data"]["max_seq_len"], _cfg["train"]["per_device_batch"], _cfg["train"]["grad_accum"]
_aps = _cfg["train"].get("assumed_tokens_per_sec")
_r = _cfg["lora"]["r"]
gn, _ = peak(_seq, _mb, _r, False)
gc, _ = peak(_seq, _mb, _r, True)
w("## 12. Check of the production config (`configs/qlora_sft.yaml`) against these estimates\n")
w(f"The locked production config (read from the file when this document is generated) uses max_seq_len {_seq}, micro-batch {_mb}, accumulation {_ga} (effective batch {_mb * _ga}), r={_r}, `{_cfg['train']['optimizer']}`, gradient checkpointing. "
  f"ESTIMATED peak: **{gc:.1f} GiB with the label-only chunked loss that `train.py` implements ({verdict(gc)})** versus **{gn:.1f} GiB if a naive full-logits loss were used ({verdict(gn)})**. "
  "The chunked loss is therefore required, not optional. OOM fallback via `--set`: "
  f"micro-batch {max(1, _mb // 2)} x accumulation {_ga * 2}: {peak(_seq, max(1, _mb // 2), _r, True)[0]:.1f} GiB; micro-batch 1 x accumulation {_mb * _ga}: {peak(_seq, 1, _r, True)[0]:.1f} GiB (chunked).\n")
_lo, _hi = train_tokens * 2 / tps[1] / 3600, train_tokens * 2 / tps[0] / 3600
w(f"`assumed_tokens_per_sec` in the config is {_aps} (used only for the dry-run time projection). It is a mid-range ESTIMATE, not a measurement: at 100% utilisation of a T4's 65 TFLOP/s the FLOPs argument gives an upper bound of {T4_FP16_PEAK / FLOPS_PER_TOKEN:.0f} tokens/s, "
  f"and QLoRA with on-the-fly dequantisation reaches only a fraction of it. The realistic ESTIMATE is {tps[0]:.0f}-{tps[1]:.0f} tokens/s on a T4, i.e. about {_lo:.1f}-{_hi:.1f} h for the configured 2 epochs over {train_tokens:,} tokens/epoch. "
  "The notebook does not compute throughput itself: it streams the trainer output, and the trainer's periodic log lines carry `tok_per_s` and a `first_steps_throughput` event is written after 10 steps (verified in `pipeline/train/train.py`). The estimate is replaced by a measurement only once Phase 5 has really run.\n")
w("## 13. What is verified vs estimated vs pending\n")
w("| item | status |\n|---|---|\n| Architecture numbers (layers, hidden, heads, vocab, intermediate) | **verified** from the real `config.json` |\n| Parameter count and NF4 sizes | computed (**estimate** of the on-device footprint) |\n| Peak VRAM tables | **ESTIMATED** (no GPU measurement) |\n| Tokens/s and hours | **ROUGH ESTIMATE** (FLOPs argument, assumed utilisation) |\n| Dataset token counts | computed with the real tokenizer on the stored dataset (approximate rendering) |\n| Kaggle quota / disk / RAM limits | per Kaggle documentation as remembered; **verify at Phase 5** |\n| fp16 stability of Qwen3 | **not tested** |\n| Library versions on Kaggle (transformers / peft / bitsandbytes / torch) | **UNVERIFIED until Phase 5** - all local tests ran on CPU with transformers 5.19.0, peft 0.21.2, trl 1.15.0, torch 2.14.1; bitsandbytes never ran |\n| P100 (sm_60) support in current Kaggle wheels | **UNVERIFIED** - prefer T4 |\n| Any Kaggle execution | **PENDING PHASE 5** |")
Path(WS / "docs" / "MEMORY_PLAN.md").write_text("\n".join(W) + "\n", encoding="utf-8")
print("wrote docs/MEMORY_PLAN.md; total params", f"{total:,}", "weights GiB", round(weights_dq / GiB, 2), "train tokens", train_tokens)
