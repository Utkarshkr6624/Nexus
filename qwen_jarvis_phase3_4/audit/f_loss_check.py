import sys, torch
sys.path.insert(0, r"E:\Nexo\qwen_jarvis_phase3_4")
from transformers import Qwen3Config, AutoModelForCausalLM
import pipeline.train.train as T
torch.manual_seed(1234)
cfg = Qwen3Config(vocab_size=500, hidden_size=32, intermediate_size=64, num_hidden_layers=2, num_attention_heads=4, num_key_value_heads=2, head_dim=8,
                  max_position_embeddings=128, tie_word_embeddings=False)
m = AutoModelForCausalLM.from_config(cfg).float()
m.train()
B, L = 3, 40
ids = torch.randint(0, 500, (B, L))
att = torch.ones(B, L, dtype=torch.long); att[1, 30:] = 0; att[2, 35:] = 0     # right padding
labels = ids.clone()
labels[:, :10] = -100; labels[att == 0] = -100; labels[0, 20:23] = -100          # prompt mask + pad + holes
items = int((labels[:, 1:] != -100).sum())
batch = {"input_ids": ids, "attention_mask": att, "labels": labels}
T.LOSS_CHUNK = 7
# reference: standard HF loss (mean over supervised tokens) using full logits
m.zero_grad()
ref = m(input_ids=ids, attention_mask=att, labels=labels).loss
ref.backward()
g_ref = {n: p.grad.clone() for n, p in m.named_parameters() if p.grad is not None}
m.zero_grad()
mine = T.masked_lm_loss(m, batch, items)
mine.backward()
g_mine = {n: p.grad.clone() for n, p in m.named_parameters() if p.grad is not None}
print("items", items, "ref", float(ref), "chunked", float(mine), "absdiff", abs(float(ref) - float(mine)))
worst = max(float((g_ref[n] - g_mine[n]).abs().max()) for n in g_ref)
print("max grad abs diff over", len(g_ref), "params:", worst, "| missing grads:", set(g_ref) ^ set(g_mine))
# no-grad (eval) path and a different chunk
m.eval()
with torch.no_grad():
    T.LOSS_CHUNK = 1024
    e = T.masked_lm_loss(m, batch, items)
    r = m(input_ids=ids, attention_mask=att, labels=labels).loss
print("eval path", float(e), float(r))
# accumulation weighting: two micro-batches, sum of (loss_i with common items) == joint loss
m.train()
b1 = {k: v[:1] for k, v in batch.items()}; b2 = {k: v[1:] for k, v in batch.items()}
tot = T.masked_lm_loss(m, b1, items) + T.masked_lm_loss(m, b2, items)
print("accum-sum", float(tot), "joint ref", float(ref))
