"""Freezes the Phase 3 dataset: qwen_jarvis_dataset_v2_final.
Inputs : dataset/v2_final_candidate/{train,validation,test}.jsonl  (preflight-cleaned + tool-protocol-converted)
         tool_protocol/protocol_examples.jsonl                      (32 canonical protocol examples, added to train only)
Output : dataset/v2_final/{train,validation,test}.jsonl  and  dataset/v2_final/MANIFEST.json  (counts, hashes, lineage)
Steps  : drop exact-duplicate conversations created by the conversion; re-run leakage control (identical/near-identical prompts, identical long answers)
         moving held-out records into train; recompute ids as qj2f-<md5 of messages+tools>; write; hash.  Deterministic.  Stdlib only."""
import collections
import hashlib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WS = HERE.parent
CAND = WS / "dataset" / "v2_final_candidate"
OUT = WS / "dataset" / "v2_final"
OUT.mkdir(parents=True, exist_ok=True)
VERSION = "qwen_jarvis_dataset_v2_final"


def md5(t):
    return hashlib.md5(t.encode("utf-8")).hexdigest()


def norm(t):
    return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()


def shing(t, k=3):
    w = norm(t).split()
    return {" ".join(w[i:i + k]) for i in range(max(0, len(w) - k + 1))} if len(w) >= k else ({" ".join(w)} if w else set())


def utext(r):
    return " ".join(m["content"] for m in r["messages"] if m["role"] == "user")


def load(d, splits=("train", "validation", "test")):
    recs = []
    for s in splits:
        p = d / f"{s}.jsonl"
        for l in open(p, encoding="utf-8"):
            if l.strip():
                r = json.loads(l)
                r["_split"] = s
                recs.append(r)
    return recs


def long_turns(r, n=100):
    """normalised hashes of every assistant turn of at least n characters (a long answer shared across splits is leakage even under a paraphrased prompt)"""
    return {md5(norm(m["content"])) for m in r["messages"] if m["role"] == "assistant" and len(m["content"]) >= n}


def first_prompt(r):
    return md5(norm(next(m["content"] for m in r["messages"] if m["role"] == "user")))


log = collections.Counter()
recs = load(CAND)
for l in open(WS / "tool_protocol" / "protocol_examples.jsonl", encoding="utf-8"):
    r = json.loads(l)
    r["_split"] = "train"
    r["metadata"]["source_id"] = r["id"]
    recs.append(r)
    log["protocol_examples_added"] += 1

# 1. exact duplicate conversations (messages + tools)
seen, kept = set(), []
for r in sorted(recs, key=lambda r: (r["_split"] != "train", r["id"])):         # prefer keeping the held-out copy if a duplicate straddles splits
    key = md5(json.dumps(r["messages"], sort_keys=True))        # identical messages teach nothing new even if the attached-tool list differs
    if key in seen:
        log["exact_duplicates_dropped"] += 1
        continue
    seen.add(key)
    kept.append(r)

# 2. leakage control
moved = 0
for _ in range(8):
    train = [r for r in kept if r["_split"] == "train"]
    inv = collections.defaultdict(list)
    sh = {id(r): shing(utext(r)) for r in kept}
    for r in train:
        for g in sh[id(r)]:
            inv[g].append(id(r))
    sh_by_id = {id(x): sh[id(x)] for x in train}
    train_prompts = {md5(norm(utext(r))) for r in train}
    train_ans = {md5(norm(r["messages"][-1]["content"])) for r in train if len(r["messages"][-1]["content"]) >= 60}
    train_long = set().union(*(long_turns(r) for r in train))
    train_first = {first_prompt(r) for r in train}
    changed = False
    for r in kept:
        if r["_split"] == "train":
            continue
        hit = md5(norm(utext(r))) in train_prompts or bool(long_turns(r) & train_long) or first_prompt(r) in train_first
        u = sh[id(r)]
        if not hit and len(u) >= 6:
            cand = collections.Counter()
            for g in u:
                lst = inv.get(g)
                if lst and len(lst) < 500:
                    cand.update(lst)
            for xid, c in cand.most_common(8):
                if c / len(u | sh_by_id[xid]) >= 0.7:
                    hit = True
                    break
        a = r["messages"][-1]["content"]
        if not hit and len(a) >= 60 and md5(norm(a)) in train_ans:
            hit = True
        if hit:
            r["_split"] = "train"
            moved += 1
            changed = True
    # held-out vs held-out identical prompts / answers: move the later one to train
    seen_p, seen_a, seen_l, seen_f = {}, {}, {}, {}
    for r in sorted((x for x in kept if x["_split"] != "train"), key=lambda x: x["id"]):
        p = md5(norm(utext(r)))
        a = md5(norm(r["messages"][-1]["content"])) if len(r["messages"][-1]["content"]) >= 60 else None
        ls, f = long_turns(r), first_prompt(r)
        if ((p in seen_p and seen_p[p] != r["_split"]) or (a and a in seen_a and seen_a[a] != r["_split"])
                or any(x in seen_l and seen_l[x] != r["_split"] for x in ls) or (f in seen_f and seen_f[f] != r["_split"])):
            r["_split"] = "train"
            moved += 1
            changed = True
        else:
            seen_p.setdefault(p, r["_split"])
            if a:
                seen_a.setdefault(a, r["_split"])
            for x in ls:
                seen_l.setdefault(x, r["_split"])
            seen_f.setdefault(f, r["_split"])
    if not changed:
        break
log["moved_to_train_for_leakage"] = moved

# 3. write
by_split = collections.defaultdict(list)
for r in kept:
    meta = dict(r["metadata"])
    new_id = "qj2f-" + md5(json.dumps({"m": r["messages"], "t": r.get("tools")}, sort_keys=True))[:12]
    meta.setdefault("source_id", r["id"])
    meta["dataset_version"] = VERSION
    rec = {"id": new_id, "messages": r["messages"]}
    if r.get("tools"):
        rec["tools"] = r["tools"]
    rec["metadata"] = meta
    by_split[r["_split"]].append(rec)
files = {}
for s in ("train", "validation", "test"):
    recs_s = sorted(by_split[s], key=lambda x: x["id"])
    p = OUT / f"{s}.jsonl"
    with open(p, "w", encoding="utf-8") as f:
        for rec in recs_s:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    files[f"{s}.jsonl"] = {"records": len(recs_s), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
allr = [rec for s in by_split.values() for rec in s]
cat = collections.Counter(r["metadata"]["category"] for r in allr)
manifest = {
    "version": VERSION, "baseline": "qwen_jarvis_dataset_v2 (11,332 examples)", "stages": ["preflight corrections (preflight/changelog.jsonl)", "tool protocol conversion (tool_protocol/convert.py, toolproto-1)", "32 canonical protocol examples", "freeze (this script)"],
    "records": len(allr), "files": files, "categories": dict(cat),
    "multi_turn": sum(1 for r in allr if sum(1 for m in r["messages"] if m["role"] == "user") >= 2),
    "with_tools": sum(1 for r in allr if r.get("tools")), "with_tool_calls": sum(1 for r in allr if any(m.get("tool_calls") for m in r["messages"])),
    "tool_messages": sum(1 for r in allr for m in r["messages"] if m["role"] == "tool"),
    "freeze_actions": dict(log), "tool_protocol": "toolproto-1",
    "status": "FROZEN FOR TRAINING PIPELINE DEVELOPMENT - do not edit; make a new version instead",
}
json.dump(manifest, open(OUT / "MANIFEST.json", "w", encoding="utf-8"), indent=1)
print(json.dumps({k: manifest[k] for k in ("records", "files", "categories", "multi_turn", "with_tools", "with_tool_calls", "tool_messages", "freeze_actions")}, indent=1))
