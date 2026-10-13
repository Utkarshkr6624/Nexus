"""Bridge between the trainer and the Data agent's loader (`pipeline.data`, see docs/CONTRACT.md).

If `pipeline.data.dataset` exposes `load_split` / `collate`, those are used (single source of truth for rendering and masking).
Otherwise a minimal, clearly-labelled FALLBACK renderer is used so the trainer and its smoke test stay self-contained. The fallback
trains on the final assistant turn only (or all assistant turns when the rendering is prefix-stable) and never executes dataset text.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

IGNORE = -100
SOURCE = "fallback"


IMPORT_ERROR = None


def _try_data_agent():
    global IMPORT_ERROR
    try:
        import importlib

        mod = importlib.import_module("pipeline.data.dataset")
        if hasattr(mod, "load_split") and hasattr(mod, "collate"):
            return mod
        IMPORT_ERROR = "pipeline.data.dataset lacks load_split/collate"
    except Exception as e:     # recorded, never silent: production refuses to run on the fallback (see train.require_primary_loader)
        IMPORT_ERROR = f"{type(e).__name__}: {e}"
    return None


_DATA_AGENT = _try_data_agent()
SOURCE = "pipeline.data" if _DATA_AGENT is not None else "fallback"


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def count_lines(path: Path) -> int:
    with open(path, "rb") as f:
        return sum(1 for line in f if line.strip())


def count_samples(path: Path, train_on: str = "per_turn") -> int:
    """EXACT number of training samples the loader will emit for the whole file, without tokenising: per_turn = one per assistant message,
    all_assistant / last_assistant = one per record. Upper bound only if turns longer than max_seq_len are dropped (reports/data_stats_*.json: 0 at 1024)."""
    n = 0
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"{path}:{i}: invalid JSON ({e.msg})") from e
            n += sum(1 for m in rec["messages"] if m.get("role") == "assistant") if train_on == "per_turn" else 1
    return n


def read_jsonl(path: Path, limit: int | None = None) -> list[dict]:
    out = []
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise ValueError(f"{path}:{i}: invalid JSON ({e.msg})") from e
            if limit and len(out) >= limit:
                break
    return out


# ------------------------------- fallback renderer ----------------------------------------------
def _template(tok, messages, tools, add_generation_prompt):
    return tok.apply_chat_template(messages, tools=tools, tokenize=False, add_generation_prompt=add_generation_prompt, enable_thinking=False)


def _fallback_render(rec: dict, tok, max_len: int, train_on: str) -> dict:
    msgs = rec["messages"]
    tools = rec.get("tools")
    if not msgs or msgs[-1]["role"] != "assistant":
        raise ValueError(f"example {rec.get('id')}: last message must be an assistant message")
    full = _template(tok, msgs, tools, False)
    ids = tok(full, add_special_tokens=False)["input_ids"]
    labels = [IGNORE] * len(ids)
    # supervise the final assistant message: everything after the generation prompt of the previous turns
    prompt = _template(tok, msgs[:-1], tools, True)
    if not full.startswith(prompt):
        raise ValueError(f"example {rec.get('id')}: chat template is not prefix-stable; cannot derive assistant mask")
    p_ids = tok(prompt, add_special_tokens=False)["input_ids"]
    if ids[: len(p_ids)] != p_ids:
        raise ValueError(f"example {rec.get('id')}: tokenisation not prefix-stable at the prompt/answer boundary")
    for i in range(len(p_ids), len(ids)):
        labels[i] = ids[i]
    truncated = len(ids) > max_len
    if truncated:
        ids, labels = ids[:max_len], labels[:max_len]
    return {"input_ids": ids, "labels": labels, "attention_mask": [1] * len(ids), "n_tokens": len(ids), "truncated": truncated,
            "n_supervised": sum(1 for x in labels if x != IGNORE)}


def load_examples(path: Path, tok, max_len: int, train_on: str, limit: int | None = None, validate: str = "strict", random_subset: bool = False) -> list[dict]:
    """Returns a list of dicts with input_ids / labels / attention_mask (+ n_tokens, truncated).
    random_subset: with a limit, take a seeded random subset of records instead of the first `limit` (used only by the dry-run length estimate)."""
    if _DATA_AGENT is not None:
        return _DATA_AGENT.load_split(str(path), tok, max_len, train_on=train_on, validate=validate, limit=limit, seed=0, shuffle_limit=random_subset)
    recs = read_jsonl(path, limit)
    return [_fallback_render(r, tok, max_len, "last_assistant") for r in recs]


def collate(batch: list[dict], pad_id: int):
    """Right-pads to the longest example in the batch. Returns torch tensors."""
    import torch

    if _DATA_AGENT is not None:
        return _DATA_AGENT.collate(batch, pad_id)
    n = max(len(b["input_ids"]) for b in batch)
    ids = torch.full((len(batch), n), pad_id, dtype=torch.long)
    lab = torch.full((len(batch), n), IGNORE, dtype=torch.long)
    att = torch.zeros((len(batch), n), dtype=torch.long)
    for i, b in enumerate(batch):
        k = len(b["input_ids"])
        ids[i, :k] = torch.tensor(b["input_ids"])
        lab[i, :k] = torch.tensor(b["labels"])
        att[i, :k] = 1
    return {"input_ids": ids, "labels": lab, "attention_mask": att}
