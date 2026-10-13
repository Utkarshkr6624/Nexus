"""Loading, batching and collation. torch is imported lazily so the schema/render code can be used without it."""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from pathlib import Path

from .render import IGNORE, PAD_ID, render_samples
from .schema import validate_record


class DataError(RuntimeError):
    pass


def read_jsonl(path):
    p = Path(path)
    if not p.is_file():
        raise DataError(f"dataset file not found: {p}")
    out = []
    with open(p, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise DataError(f"{p}:{n}: invalid JSON ({e.msg})") from e
    if not out:
        raise DataError(f"dataset file is empty: {p}")
    return out


@dataclass
class LoadReport:
    path: str = ""
    mode: str = ""
    train_on: str = ""
    max_len: int = 0
    records: int = 0
    invalid: int = 0
    invalid_examples: list = field(default_factory=list)
    samples: int = 0
    records_with_dropped_turns: int = 0       # per_turn: at least one assistant turn longer than max_len was dropped
    dropped_turns: int = 0
    records_truncated: int = 0                # single modes: trailing exchanges cut
    records_skipped: int = 0                  # nothing fit
    truncated_pct: float = 0.0

    def as_dict(self):
        return self.__dict__.copy()


class SplitData(list):
    """List of rendered samples with a .report attribute."""
    report: LoadReport


def load_split(path, tokenizer, max_len=2048, *, train_on="per_turn", validate="strict", limit=None, seed=0, shuffle_limit=False):
    """Read, validate and render one split. validate: 'strict' | 'phase2' | 'off'. Invalid records raise DataError unless
    validate='off'; use validate='skip' to drop and report them instead."""
    recs = read_jsonl(path)
    if limit:
        if shuffle_limit:
            random.Random(seed).shuffle(recs)
        recs = recs[:limit]
    rep = LoadReport(path=str(path), mode=validate, train_on=train_on, max_len=max_len, records=len(recs))
    out = SplitData()
    for i, rec in enumerate(recs):
        if validate in ("strict", "phase2", "skip"):
            probs = validate_record(rec, "phase2" if validate == "phase2" else "strict")
            if probs:
                rep.invalid += 1
                if len(rep.invalid_examples) < 10:
                    rep.invalid_examples.append((i, rec.get("id"), probs[:3]))
                if validate != "skip":
                    raise DataError(f"{path}: record #{i} ({rec.get('id')}) failed {validate} validation: {probs[:3]}")
                continue
        samples, bad = render_samples(rec, tokenizer, max_len, train_on)
        if train_on == "per_turn":
            rep.dropped_turns += bad
            rep.records_with_dropped_turns += 1 if bad else 0
        else:
            if not samples:
                rep.records_skipped += 1
            elif samples[0]["truncated"]:
                rep.records_truncated += 1
        out.extend(samples)
    rep.samples = len(out)
    cut = rep.records_with_dropped_turns + rep.records_truncated + rep.records_skipped
    rep.truncated_pct = round(100.0 * cut / max(1, rep.records), 3)
    out.report = rep
    return out


# ---------------------------------------------------------------- collation / batching
def collate(batch, pad_token_id=PAD_ID, padding_side="right", pad_to_multiple_of=None):
    """Dynamic padding. Returns dict of torch.LongTensor: input_ids, attention_mask, labels (-100 on padding)."""
    import torch
    if not batch:
        raise DataError("cannot collate an empty batch")
    if padding_side not in ("left", "right"):
        raise ValueError("padding_side must be 'left' or 'right'")
    L = max(len(b["input_ids"]) for b in batch)
    if pad_to_multiple_of:
        L = ((L + pad_to_multiple_of - 1) // pad_to_multiple_of) * pad_to_multiple_of
    ids, att, lab = [], [], []
    for b in batch:
        n = L - len(b["input_ids"])
        pad_i, pad_a, pad_l = [pad_token_id] * n, [0] * n, [IGNORE] * n
        if padding_side == "right":
            ids.append(b["input_ids"] + pad_i)
            att.append(b["attention_mask"] + pad_a)
            lab.append(b["labels"] + pad_l)
        else:
            ids.append(pad_i + b["input_ids"])
            att.append(pad_a + b["attention_mask"])
            lab.append(pad_l + b["labels"])
    return {"input_ids": torch.tensor(ids, dtype=torch.long), "attention_mask": torch.tensor(att, dtype=torch.long),
            "labels": torch.tensor(lab, dtype=torch.long)}


class TorchDataset:
    """Minimal map-style dataset (does not require subclassing torch.utils.data.Dataset to be usable with DataLoader)."""

    def __init__(self, samples):
        self.samples = list(samples)

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, i):
        s = self.samples[i]
        return {"input_ids": s["input_ids"], "attention_mask": s["attention_mask"], "labels": s["labels"]}


def shuffled_indices(n, seed, epoch=0):
    idx = list(range(n))
    random.Random(seed * 1_000_003 + epoch).shuffle(idx)
    return idx


def length_grouped_batches(lengths, batch_size, seed=0, epoch=0, mega_batch_mult=50):
    """Deterministic length-grouped batching (as in HF's group_by_length): shuffle, cut into mega-batches of
    batch_size*mega_batch_mult, sort each by length (desc), cut into batches, then shuffle batch order. Padding waste drops a lot."""
    idx = shuffled_indices(len(lengths), seed, epoch)
    mega = batch_size * mega_batch_mult
    batches = []
    for s in range(0, len(idx), mega):
        chunk = sorted(idx[s:s + mega], key=lambda i: -lengths[i])
        batches += [chunk[j:j + batch_size] for j in range(0, len(chunk), batch_size)]
    random.Random(seed * 7919 + epoch).shuffle(batches)
    return batches


def token_budget_batches(lengths, max_tokens, seed=0, epoch=0, max_batch_size=64):
    """Batches whose padded size (max_len_in_batch * n) stays under max_tokens. Deterministic."""
    idx = sorted(shuffled_indices(len(lengths), seed, epoch), key=lambda i: -lengths[i])
    batches, cur, cur_max = [], [], 0
    for i in idx:
        m = max(cur_max, lengths[i])
        if cur and (m * (len(cur) + 1) > max_tokens or len(cur) >= max_batch_size):
            batches.append(cur)
            cur, cur_max, m = [], 0, lengths[i]
        cur.append(i)
        cur_max = m
    if cur:
        batches.append(cur)
    random.Random(seed * 104729 + epoch).shuffle(batches)
    return batches


def padding_waste(lengths, batches):
    """Fraction of padded positions across batches."""
    real = sum(lengths[i] for b in batches for i in b)
    padded = sum(max(lengths[i] for i in b) * len(b) for b in batches)
    return 0.0 if padded == 0 else 1.0 - real / padded
