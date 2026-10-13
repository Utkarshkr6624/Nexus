"""Aggregation, Wilson confidence intervals, JSON/markdown reports and the base-vs-fine-tuned `compare` report. Stdlib only."""
import json
import math
from collections import defaultdict
from datetime import datetime, timezone

from .text_utils import wilson

STUB_STAMP = "STUB — NOT A MODEL RESULT"
CAVEATS = [
    "Pass rates come from small, hand-written, deterministic probe sets (n is shown for every row); they are behavioural probes, not benchmarks.",
    "Wilson 95% intervals are shown; with n < 50 they are wide, so differences smaller than the interval should not be over-interpreted.",
    "No multiple-comparison correction is applied across rows.",
    "Lexical F1 against reference answers is informational only and never used for pass/fail.",
    "Perplexity/loss is meaningful only as assistant-token loss on in-distribution data (this dataset's own validation/test splits); it is not comparable across tokenizers or data.",
]


def _rate(k, n):
    lo, hi = wilson(k, n)
    return {"n": n, "pass": k, "rate": round(k / n, 4) if n else None, "ci95": [round(lo, 4), round(hi, 4)]}


def aggregate(items):
    """items: list of per-item result dicts from the runner. Returns {'overall', 'suites', 'categories', 'derived'}."""
    by_suite, by_cat = defaultdict(list), defaultdict(list)
    for it in items:
        if not it["scored"]:
            continue
        by_suite[it["suite"]].append(it)
        by_cat[it["category"]].append(it)
    suites = {s: _rate(sum(i["pass"] for i in v), len(v)) for s, v in sorted(by_suite.items())}
    cats = {c: _rate(sum(i["pass"] for i in v), len(v)) for c, v in sorted(by_cat.items())}
    scored = [i for v in by_suite.values() for i in v]
    f1 = [i["info"]["lexical_f1"] for i in items if "lexical_f1" in i.get("info", {})]
    hyg = defaultdict(int)
    for i in items:
        for k, v in i.get("info", {}).get("hygiene", {}).items():
            hyg[k] += int(bool(v))
    derived = {}

    def sel(pred):
        return [i for i in items if pred(i)]

    harm = sel(lambda i: i["category"] == "safety/harmful")
    if harm:
        derived["harmful_refusal_rate"] = _rate(sum(1 for i in harm if i["flags"]["refused"]), len(harm))
    ben = sel(lambda i: i["category"] == "safety/benign_lookalike")
    if ben:
        derived["over_refusal_rate"] = _rate(sum(1 for i in ben if i["flags"]["refused"]), len(ben))
    no_call_cats = ("tool_awareness/not_needed", "tool_awareness/no_tools_attached", "tool_awareness/tool_missing", "tool_awareness/ambiguous", "tool_awareness/write_preview",
                    "tool_awareness/after_result", "tool_awareness/after_error")
    nc = sel(lambda i: i["category"] in no_call_cats)
    if nc:
        derived["false_tool_call_rate"] = _rate(sum(1 for i in nc if i["flags"]["tool_markup"]), len(nc))
    need = sel(lambda i: i["category"] in ("tool_awareness/read_call", "tool_awareness/write_after_approval"))
    if need:
        derived["valid_tool_call_rate_when_needed"] = _rate(sum(1 for i in need if i["pass"]), len(need))
        derived["any_tool_markup_rate_when_needed"] = _rate(sum(1 for i in need if i["flags"]["tool_markup"]), len(need))
    return {"overall": _rate(sum(i["pass"] for i in scored), len(scored)), "suites": suites, "categories": cats, "derived": derived,
            "informational": {"mean_lexical_f1": round(sum(f1) / len(f1), 4) if f1 else None, "n_lexical_f1": len(f1)},
            "hygiene_counts": dict(hyg), "n_items_total": len(items), "n_items_scored": len(scored)}


def make_report(meta, items, loss=None):
    stub = meta.get("model_kind") == "stub"
    rep = {"generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "meta": meta, "metrics": aggregate(items), "caveats": CAVEATS}
    if stub:
        rep["disclaimer"] = STUB_STAMP
    if loss is not None:
        rep["loss"] = loss
    return rep


def _fmt_rate(r):
    if r is None or r["rate"] is None:
        return "n/a"
    return f"{100 * r['rate']:.1f}% ({r['pass']}/{r['n']}) [{100 * r['ci95'][0]:.0f}–{100 * r['ci95'][1]:.0f}]"


def to_markdown(rep):
    m, mt = rep["meta"], rep["metrics"]
    L = []
    if rep.get("disclaimer"):
        L += [f"# {rep['disclaimer']}", "", "> These numbers come from a test stub that only exercises the evaluation code. They say nothing about any model.", ""]
    L += ["# Evaluation report", "", "## Run metadata", ""]
    for k in ("model_kind", "model_id", "revision", "adapter", "load_in_4bit", "dataset_version", "suite_versions", "tool_schemas_sha256", "seed", "decoding", "hardware", "libraries", "max_items"):
        if k in m and m[k] is not None:
            L.append(f"- **{k}**: `{json.dumps(m[k], ensure_ascii=False) if isinstance(m[k], (dict, list)) else m[k]}`")
    L += ["", f"- **generated (UTC)**: {rep['generated_utc']}", "", "## Overall", "", f"Scored items: {mt['n_items_scored']} of {mt['n_items_total']}. Pass rate {_fmt_rate(mt['overall'])} (95% Wilson CI in brackets, percent).", "",
          "## By suite", "", "| suite | pass rate [95% CI] | n |", "|---|---|--:|"]
    for s, r in mt["suites"].items():
        L.append(f"| {s} | {_fmt_rate(r)} | {r['n']} |")
    L += ["", "## By category", "", "| category | pass rate [95% CI] | n |", "|---|---|--:|"]
    for c, r in mt["categories"].items():
        L.append(f"| {c} | {_fmt_rate(r)} | {r['n']} |")
    if mt["derived"]:
        L += ["", "## Derived behaviour rates", "", "| metric | rate [95% CI] | n |", "|---|---|--:|"]
        for k, r in mt["derived"].items():
            L.append(f"| {k} | {_fmt_rate(r)} | {r['n']} |")
    if "loss" in rep and rep["loss"]:
        ls = rep["loss"]
        L += ["", "## Assistant-token loss / perplexity", "", f"Split: `{ls.get('split')}`. {ls.get('note', '')}", "", "| category | tokens | mean loss | perplexity |", "|---|--:|--:|--:|"]
        for c, v in ls["by_category"].items():
            L.append(f"| {c} | {v['tokens']} | {v['mean_loss']:.4f} | {v['perplexity']:.3f} |")
        o = ls["overall"]
        L.append(f"| **overall** | {o['tokens']} | {o['mean_loss']:.4f} | {o['perplexity']:.3f} |")
    inf = mt["informational"]
    L += ["", "## Informational", "", f"- mean lexical F1 vs reference (never pass/fail): {inf['mean_lexical_f1']} over {inf['n_lexical_f1']} items", f"- output hygiene counts: {mt['hygiene_counts']}",
          "", "## Caveats", ""] + [f"- {c}" for c in rep["caveats"]]
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------------------------------------------- compare
def _binom_two_sided(b, c):
    """Exact two-sided McNemar p-value from the discordant counts (b: A pass/B fail, c: A fail/B pass)."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


def compare(rep_a, rep_b, items_a, items_b, label_a="base", label_b="fine-tuned"):
    ids_a = {i["id"]: i for i in items_a if i["scored"]}
    ids_b = {i["id"]: i for i in items_b if i["scored"]}
    common = sorted(set(ids_a) & set(ids_b))
    out = {"label_a": label_a, "label_b": label_b, "n_common": len(common), "only_in_a": len(set(ids_a) - set(ids_b)), "only_in_b": len(set(ids_b) - set(ids_a)), "rows": {}}
    stub = rep_a["meta"].get("model_kind") == "stub" or rep_b["meta"].get("model_kind") == "stub"
    out["stub"] = stub

    def row(sel):
        ia = [ids_a[i] for i in sel]
        ib = [ids_b[i] for i in sel]
        ka, kb = sum(x["pass"] for x in ia), sum(x["pass"] for x in ib)
        b = sum(1 for x, y in zip(ia, ib) if x["pass"] and not y["pass"])
        c = sum(1 for x, y in zip(ia, ib) if not x["pass"] and y["pass"])
        ra, rb = _rate(ka, len(sel)), _rate(kb, len(sel))
        overlap = not (ra["ci95"][1] < rb["ci95"][0] or rb["ci95"][1] < ra["ci95"][0])
        return {"n": len(sel), "a": ra, "b": rb, "delta": round((kb - ka) / len(sel), 4) if sel else None, "a_only_pass": b, "b_only_pass": c,
                "mcnemar_p": round(_binom_two_sided(b, c), 4), "ci_overlap": overlap}

    out["rows"]["overall"] = row(common)
    for key, fn in (("suite", lambda i: ids_a[i]["suite"]), ("category", lambda i: ids_a[i]["category"])):
        groups = defaultdict(list)
        for i in common:
            groups[fn(i)].append(i)
        for g, sel in sorted(groups.items()):
            out["rows"][f"{key}:{g}"] = row(sel)
    return out


def compare_markdown(cmp, rep_a, rep_b):
    L = []
    if cmp["stub"]:
        L += [f"# {STUB_STAMP}", "", "> At least one side of this comparison is a test stub. Nothing below is a model result.", ""]
    L += [f"# Comparison: {cmp['label_a']} vs {cmp['label_b']}", "",
          f"- {cmp['label_a']}: `{rep_a['meta'].get('model_id')}` adapter=`{rep_a['meta'].get('adapter')}`",
          f"- {cmp['label_b']}: `{rep_b['meta'].get('model_id')}` adapter=`{rep_b['meta'].get('adapter')}`",
          f"- items compared (scored, present in both): {cmp['n_common']}; only in {cmp['label_a']}: {cmp['only_in_a']}; only in {cmp['label_b']}: {cmp['only_in_b']}", "",
          f"| row | n | {cmp['label_a']} [95% CI] | {cmp['label_b']} [95% CI] | delta (pp) | discordant ({cmp['label_a']}-only / {cmp['label_b']}-only) | McNemar p | CIs overlap |",
          "|---|--:|---|---|--:|---|--:|:-:|"]
    for k, r in cmp["rows"].items():
        d = "n/a" if r["delta"] is None else f"{100 * r['delta']:+.1f}"
        L.append(f"| {k} | {r['n']} | {_fmt_rate(r['a'])} | {_fmt_rate(r['b'])} | {d} | {r['a_only_pass']} / {r['b_only_pass']} | {r['mcnemar_p']} | {'yes' if r['ci_overlap'] else 'no'} |")
    L += ["", "## How to read this", "",
          "- Same items, same decoding; the McNemar test uses only the items on which the two models disagree (paired).",
          "- A small p-value with overlapping CIs is still weak evidence on tiny n; a large delta on n < 20 is anecdotal.",
          "- Rows were not corrected for multiple comparisons; look for consistent direction across related rows rather than any single row.",
          "- Regressions matter as much as gains: check `general/*`, `safety/benign_lookalike` (over-refusal) and `instruction_following` for losses after fine-tuning.", ""]
    L += [f"- {c}" for c in CAVEATS]
    return "\n".join(L) + "\n"
