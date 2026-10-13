"""Token statistics for a split. Reports both per-training-sample and per-conversation views."""
from __future__ import annotations

import collections
import statistics

from .render import IGNORE, render_single
from .schema import has_tools, user_turns

FIT_LIMITS = (512, 1024, 1536, 2048, 3072, 4096)
BINS = (64, 128, 192, 256, 384, 512, 768, 1024, 1536, 2048, 3072, 4096, 10 ** 9)


def _pct(vals, q):
    if not vals:
        return 0
    s = sorted(vals)
    return s[min(len(s) - 1, int(q * len(s)))]


def _summary(vals):
    if not vals:
        return {"n": 0}
    return {"n": len(vals), "total": sum(vals), "mean": round(sum(vals) / len(vals), 1), "median": statistics.median(vals), "p95": _pct(vals, 0.95),
            "p99": _pct(vals, 0.99), "max": max(vals), "min": min(vals)}


def histogram(vals):
    counts = collections.Counter()
    for v in vals:
        for b in BINS:
            if v <= b:
                counts[b] += 1
                break
    out, prev = [], 0
    for b in BINS:
        label = f"{prev + 1}-{b}" if b < 10 ** 9 else f">{prev}"
        out.append({"bin": label, "count": counts.get(b, 0)})
        prev = b
    return out


def compute_stats(records, samples, tokenizer, report=None, max_len=2048, with_conversation_lengths=True):
    """records: raw records; samples: rendered samples (from load_split); report: LoadReport."""
    s_len = [s["n_tokens"] for s in samples]
    s_lab = [s["n_label_tokens"] for s in samples]
    out = {"records": len(records), "samples": len(samples), "max_len": max_len}
    out["per_sample_tokens"] = _summary(s_len)
    out["label_tokens"] = {"total": sum(s_lab), "ratio_of_all_tokens": round(sum(s_lab) / max(1, sum(s_len)), 4)}
    out["samples_fit_pct"] = {str(k): round(100 * sum(1 for x in s_len if x <= k) / max(1, len(s_len)), 2) for k in FIT_LIMITS}
    out["histogram_per_sample"] = histogram(s_len)
    out["multi_turn_records"] = sum(1 for r in records if user_turns(r) >= 2)
    out["tool_records"] = sum(1 for r in records if has_tools(r))
    out["records_with_system_message"] = sum(1 for r in records if r["messages"][0]["role"] == "system")
    if report is not None:
        out["load_report"] = report.as_dict()
    by_cat = collections.defaultdict(list)
    for s in samples:
        by_cat[s.get("category") or "unknown"].append(s["n_tokens"])
    out["per_category_tokens"] = {c: _summary(v) for c, v in sorted(by_cat.items())}
    if with_conversation_lengths:
        # whole-conversation length (no truncation) drives the max_len decision
        conv = []
        for r in records:
            s = render_single(tokenizer, r, max_len=10 ** 9, train_on="all_assistant")
            if s is not None:
                conv.append(s["n_tokens"])
        out["per_conversation_tokens"] = _summary(conv)
        out["conversations_fit_pct"] = {str(k): round(100 * sum(1 for x in conv if x <= k) / max(1, len(conv)), 2) for k in FIT_LIMITS}
        out["histogram_per_conversation"] = histogram(conv)
        out["truncation_pct_at_max_len"] = round(100 * sum(1 for x in conv if x > max_len) / max(1, len(conv)), 3)
    return out


def to_markdown(name, st):
    L = [f"# Data statistics: {name}\n"]
    L.append(f"- records: {st['records']}, rendered training samples: {st['samples']}, max_len: {st['max_len']}")
    t = st["per_sample_tokens"]
    L.append(f"- per-sample tokens: total {t.get('total')}, mean {t.get('mean')}, median {t.get('median')}, p95 {t.get('p95')}, p99 {t.get('p99')}, max {t.get('max')}")
    L.append(f"- label (loss) tokens: {st['label_tokens']['total']} ({100 * st['label_tokens']['ratio_of_all_tokens']:.1f}% of all tokens)")
    if "per_conversation_tokens" in st:
        c = st["per_conversation_tokens"]
        L.append(f"- per-conversation tokens: total {c.get('total')}, mean {c.get('mean')}, median {c.get('median')}, p95 {c.get('p95')}, p99 {c.get('p99')}, max {c.get('max')}")
        L.append(f"- conversations fitting: " + ", ".join(f"<={k}: {v}%" for k, v in st["conversations_fit_pct"].items()))
        L.append(f"- truncation at max_len={st['max_len']}: {st['truncation_pct_at_max_len']}% of conversations")
    L.append(f"- samples fitting: " + ", ".join(f"<={k}: {v}%" for k, v in st["samples_fit_pct"].items()))
    L.append(f"- multi-turn records: {st['multi_turn_records']}, tool records: {st['tool_records']}, with system message: {st['records_with_system_message']}")
    if "load_report" in st:
        L.append(f"- load report: `{st['load_report']}`")
    L.append("\n## Histogram (tokens per sample)\n\n| tokens | count |\n|---|--:|")
    for h in st["histogram_per_sample"]:
        L.append(f"| {h['bin']} | {h['count']} |")
    L.append("\n## Per category (tokens per sample)\n\n| category | n | mean | median | p95 | max |\n|---|--:|--:|--:|--:|--:|")
    for c, v in st["per_category_tokens"].items():
        L.append(f"| {c} | {v['n']} | {v['mean']} | {v['median']} | {v['p95']} | {v['max']} |")
    return "\n".join(L) + "\n"
