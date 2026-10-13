"""Assistant-token loss / perplexity evaluator.

Loss is next-token cross-entropy over the *label* tokens only (assistant content, tool-call text and the closing <|im_end|>; everything else is -100),
exactly the tokens the trainer optimises (see pipeline/data/render.py). Perplexity = exp(mean token loss).

HONEST SCOPE: perplexity here is meaningful only as assistant-token loss on in-distribution data (this dataset's validation/test splits), for
comparing checkpoints of the same model and tokenizer. It is not a measure of general ability, and values from a different tokenizer or
dataset are not comparable.

numpy implementation (`token_nll`) is used by the tests; the torch implementation (`evaluate_samples`) is used for real models (torch imported lazily).
"""
import math
from collections import defaultdict

IGNORE = -100
NOTE = ("Assistant-token loss on in-distribution data only; comparable only between checkpoints of the same model/tokenizer on the same split. "
        "Not a general-ability metric.")


def token_nll(logits, labels):
    """numpy: logits [T, V] for positions 0..T-1, labels [T] (IGNORE = masked). Next-token shift is applied here: position t predicts labels[t+1].
    Returns (sum_nll, n_tokens)."""
    import numpy as np
    lg = np.asarray(logits, dtype=np.float64)[:-1]
    tg = np.asarray(labels)[1:]
    mask = tg != IGNORE
    if not mask.any():
        return 0.0, 0
    lg, tg = lg[mask], tg[mask]
    m = lg.max(axis=1, keepdims=True)
    lse = m[:, 0] + np.log(np.exp(lg - m).sum(axis=1))
    nll = lse - lg[np.arange(len(tg)), tg]
    return float(nll.sum()), int(len(tg))


class LossAccumulator:
    def __init__(self):
        self.cat = defaultdict(lambda: [0.0, 0])
        self.samples = []            # (category, nll_sum, n_tokens) for bootstrap intervals

    def add(self, category, nll_sum, n_tokens):
        c = category or "unknown"
        self.cat[c][0] += nll_sum
        self.cat[c][1] += n_tokens
        self.samples.append((c, nll_sum, n_tokens))

    @staticmethod
    def _entry(s, n):
        ml = s / n if n else float("nan")
        return {"tokens": n, "mean_loss": ml, "perplexity": math.exp(min(ml, 50)) if n else float("nan")}

    def summary(self, bootstrap=0, seed=0):
        by = {c: self._entry(s, n) for c, (s, n) in sorted(self.cat.items())}
        tot_s, tot_n = sum(v[0] for v in self.cat.values()), sum(v[1] for v in self.cat.values())
        out = {"by_category": by, "overall": self._entry(tot_s, tot_n), "n_samples": len(self.samples), "note": NOTE}
        if bootstrap and self.samples:
            import random
            rnd = random.Random(seed)
            means = []
            for _ in range(bootstrap):
                pick = [self.samples[rnd.randrange(len(self.samples))] for _ in self.samples]
                n = sum(p[2] for p in pick)
                means.append(sum(p[1] for p in pick) / n if n else float("nan"))
            means.sort()
            out["overall"]["mean_loss_ci95"] = [means[int(0.025 * len(means))], means[int(0.975 * len(means)) - 1]]
        return out


def evaluate_samples(model, samples, *, pad_token_id=151643, batch_size=4, device="cpu"):
    """samples: rendered samples from pipeline.data (dicts with input_ids/attention_mask/labels/category). Returns a LossAccumulator."""
    import torch
    import torch.nn.functional as F
    from pipeline.data.dataset import collate
    model.eval()
    acc = LossAccumulator()
    order = sorted(range(len(samples)), key=lambda i: (len(samples[i]["input_ids"]), i))     # deterministic, minimal padding
    with torch.no_grad():
        for s in range(0, len(order), batch_size):
            idx = order[s:s + batch_size]
            batch = collate([samples[i] for i in idx], pad_token_id)
            ids, att, lab = batch["input_ids"].to(device), batch["attention_mask"].to(device), batch["labels"].to(device)
            logits = model(input_ids=ids, attention_mask=att).logits.float()
            tok = F.cross_entropy(logits[:, :-1].reshape(-1, logits.size(-1)), lab[:, 1:].reshape(-1), ignore_index=IGNORE, reduction="none").view(lab.size(0), -1)
            mask = (lab[:, 1:] != IGNORE).float()
            sums, cnts = (tok * mask).sum(1).tolist(), mask.sum(1).tolist()
            for j, i in enumerate(idx):
                acc.add(samples[i].get("category"), sums[j], int(cnts[j]))
    return acc


def evaluate_split(model, tokenizer, path, *, max_len=2048, batch_size=4, device="cpu", limit=None, bootstrap=0):
    """Loads a split with pipeline.data (per-turn samples, strict validation) and returns the summary dict."""
    from pipeline.data.dataset import load_split
    data = load_split(path, tokenizer, max_len, train_on="per_turn", validate="strict", limit=limit)
    acc = evaluate_samples(model, data, pad_token_id=tokenizer.pad_token_id, batch_size=batch_size, device=device)
    out = acc.summary(bootstrap=bootstrap)
    out["split"] = str(path)
    out["load_report"] = getattr(data, "report", None).__dict__ if getattr(data, "report", None) is not None else None
    return out
