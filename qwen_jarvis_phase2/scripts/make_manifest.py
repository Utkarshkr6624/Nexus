"""Writes dataset/dataset_manifest.json and reports/dataset_statistics.json (hashes, counts, composition, diversity proxies)."""
import collections
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPL = ROOT / "dataset" / "splits"
VAL = ROOT / "dataset" / "validated"
REP = ROOT / "reports"
VERSION = "qwen_jarvis_dataset_v2"


def load(p):
    return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]


def sha(p):
    h = hashlib.sha256()
    h.update(open(p, "rb").read())
    return h.hexdigest()


splits = {s: load(SPL / f"{s}.jsonl") for s in ("train", "validation", "test")}
allr = [r for v in splits.values() for r in v]
build = json.load(open(REP / "build_stats.json"))
fv = json.load(open(REP / "final_validation.json"))

cat_split = {c: {s: sum(1 for r in v if r["metadata"]["category"] == c) for s, v in splits.items()} for c in sorted({r["metadata"]["category"] for r in allr})}
cat_src = {c: dict(collections.Counter(r["metadata"]["source_type"] for r in allr if r["metadata"]["category"] == c)) for c in cat_split}
cat_turns = {c: {"single_turn": sum(1 for r in allr if r["metadata"]["category"] == c and r["metadata"]["turns"] == 1), "multi_turn": sum(1 for r in allr if r["metadata"]["category"] == c and r["metadata"]["turns"] >= 2)} for c in cat_split}


def skeleton(r):
    u = next(m["content"] for m in r["messages"] if m["role"] == "user")
    s = re.sub(r"\d+(\.\d+)?", "#", u.lower())
    s = re.sub(r"\b(maya|arjun|lena|omar|sofia|ken|priya|tom|aisha|diego|nora|ravi|chloe|ivan|zara|ben|hana|luis|mira|sam|dana)\b", "NAME", s)
    return re.sub(r"\s+", " ", s)[:70]


div = {}
for c in cat_split:
    rs = [r for r in allr if r["metadata"]["category"] == c]
    sk = {skeleton(r) for r in rs}
    toks = [t for r in rs for m in r["messages"] if m["role"] == "assistant" for t in re.findall(r"[a-z']+", m["content"].lower())]
    div[c] = {"examples": len(rs), "distinct_prompt_skeletons_first_70_chars": len(sk), "skeleton_ratio": round(len(sk) / len(rs), 2), "assistant_type_token_ratio": round(len(set(toks)) / max(1, len(toks)), 3), "assistant_vocab": len(set(toks))}

stats = {
    "version": VERSION, "total": len(allr), "splits": {s: len(v) for s, v in splits.items()}, "split_pct": {s: round(100 * len(v) / len(allr), 2) for s, v in splits.items()},
    "category_by_split": cat_split, "category_by_source_type": cat_src, "category_turns": cat_turns, "diversity": div,
    "multi_turn_total": sum(1 for r in allr if r["metadata"]["turns"] >= 2), "multi_turn_by_split": {s: sum(1 for r in v if r["metadata"]["turns"] >= 2) for s, v in splits.items()},
    "source_types": dict(collections.Counter(r["metadata"]["source_type"] for r in allr)), "difficulty": dict(collections.Counter(r["metadata"]["difficulty"] for r in allr)),
    "with_system_message": sum(1 for r in allr if r["messages"][0]["role"] == "system"), "turn_distribution": dict(sorted(collections.Counter(r["metadata"]["turns"] for r in allr).items())),
    "build": {k: build[k] for k in ("generated_total", "validation_rejected", "duplicates_removed", "duplicates_removed_total", "balance_dropped", "leakage_moved_to_train", "final_total")},
}
json.dump(stats, open(REP / "dataset_statistics.json", "w", encoding="utf-8"), indent=1)

files = {}
for p in sorted(list(SPL.glob("*.jsonl")) + list(VAL.glob("*.jsonl"))):
    files[str(p.relative_to(ROOT)).replace("\\", "/")] = {"sha256": sha(p), "bytes": p.stat().st_size, "lines": sum(1 for _ in open(p, encoding="utf-8"))}
manifest = {
    "dataset_version": VERSION, "format": "JSONL; one record per line", "training_format": {"file_suffix": "_messages_only.jsonl", "record": {"messages": [{"role": "system|user|assistant", "content": "string"}]}},
    "full_format": {"id": "qj2-<hash>", "messages": "as above", "metadata": ["id", "category", "subcategory", "difficulty", "source_type", "group", "validation_status", "turns"]},
    "totals": {"examples": len(allr), "splits": stats["splits"], "multi_turn": stats["multi_turn_total"]}, "files": files,
    "validation": {"hard_failures": fv["hard_failures"], "checks_passed": not fv["hard_failures"]}, "tool_call_wire_format": "not defined in this dataset (Phase 3)",
    "tool_result_convention": "tool results appear as user-role messages beginning '[tool result from <tool>]' because a tool role/format is a Phase 3 decision",
    "isolated_from_nexus": True,
}
json.dump(manifest, open(ROOT / "dataset" / "dataset_manifest.json", "w", encoding="utf-8"), indent=1)
print(json.dumps({k: stats[k] for k in ("total", "splits", "split_pct", "multi_turn_total", "source_types", "turn_distribution")}, indent=1))
for c, d in div.items():
    print(f"{c:24s} n={d['examples']:5d} skeletons={d['distinct_prompt_skeletons_first_70_chars']:5d} ratio={d['skeleton_ratio']} ttr={d['assistant_type_token_ratio']}")
print("cat_split:", json.dumps(cat_split))
print("cat_turns:", json.dumps(cat_turns))
