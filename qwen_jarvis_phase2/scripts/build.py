"""Assemble -> validate -> dedup -> balance -> split -> write.  Stdlib only.
Reads  dataset/generated/*.jsonl  (never modifies it)
Writes dataset/validated/, dataset/splits/, reports/*.json|jsonl"""
import collections
import hashlib
import json
import random
import re
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "validation"))
import checks  # noqa: E402

GEN = ROOT / "dataset" / "generated"
VAL = ROOT / "dataset" / "validated"
SPL = ROOT / "dataset" / "splits"
REP = ROOT / "reports"
for d in (VAL, SPL, REP):
    d.mkdir(parents=True, exist_ok=True)

VERSION = "qwen_jarvis_dataset_v2"
TARGETS = {"general_conversation": 1000, "general_knowledge": 1000, "reasoning": 1500, "programming": 1500, "mathematics": 750, "instruction_following": 750,
           "jarvis_assistant": 1000, "nexus_knowledge": 750, "tool_aware": 750, "capability_boundaries": 500, "multi_turn_context": 500, "safety": 500}
CAP_FACTOR = 1.3     # a category may exceed its target by at most 30% so none dominates
CAP_OVERRIDE = {"reasoning": 1500, "mathematics": 750, "instruction_following": 750}   # template-heavy categories are held at their target
PRIORITY = {"authored": 0, "verified": 1, "computed": 2, "composed": 3}


def norm(t):
    return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()


def md5(t):
    return hashlib.md5(t.encode("utf-8")).hexdigest()


def body(rec):
    return [m for m in rec["messages"] if m["role"] != "system"]


def user_text(rec):
    return " ".join(m["content"] for m in body(rec) if m["role"] == "user")


def asst_text(rec):
    return " ".join(m["content"] for m in body(rec) if m["role"] == "assistant")


def shingles(text, k=3):
    w = norm(text).split()
    if len(w) < k:
        return {" ".join(w)} if w else set()
    return {" ".join(w[i:i + k]) for i in range(len(w) - k + 1)}


def jacc(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# ------------- MinHash LSH --------------
NPERM, BANDS = 48, 16
_rng = random.Random(1234)
PRIME = (1 << 61) - 1
PERMS = [(_rng.randrange(1, PRIME), _rng.randrange(0, PRIME)) for _ in range(NPERM)]


def minhash(sh):
    if not sh:
        return None
    hs = [zlib.crc32(s.encode("utf-8")) for s in sh]
    return tuple(min((a * h + b) % PRIME for h in hs) for a, b in PERMS)


def bands(sig):
    r = NPERM // BANDS
    return [(i, hash(sig[i * r:(i + 1) * r])) for i in range(BANDS)]


class LSH:
    def __init__(self):
        self.buckets = collections.defaultdict(list)

    def candidates(self, sig):
        out = set()
        for b in bands(sig):
            out.update(self.buckets.get(b, ()))
        return out

    def add(self, idx, sig):
        for b in bands(sig):
            self.buckets[b].append(idx)


# ------------- load --------------
raw = []
for f in sorted(GEN.glob("*.jsonl")):
    for line in open(f, encoding="utf-8"):
        if line.strip():
            r = json.loads(line)
            r["_file"] = f.stem
            raw.append(r)
stats = {"version": VERSION, "generated_total": len(raw), "generated_by_file": dict(collections.Counter(r["_file"] for r in raw))}
print("loaded", len(raw))

# ------------- validate --------------
rejected = []
valid = []
reason_counter = collections.Counter()
for r in raw:
    probs = []
    for chk in checks.ALL:
        probs += chk(r)
    if probs:
        rejected.append({"reason": probs, "category": r["category"], "subcategory": r["subcategory"], "messages": r["messages"]})
        for p in probs:
            reason_counter[p.split(":")[0]] += 1
    else:
        valid.append(r)
stats["validation_rejected"] = len(rejected)
stats["validation_reasons"] = dict(reason_counter)
print("valid after checks", len(valid), "rejected", len(rejected))

# ------------- dedup --------------
valid.sort(key=lambda r: (PRIORITY.get(r["source_type"], 9), r["category"], r["subcategory"]))
seen_exact, seen_prompt = set(), {}
ans_count = collections.Counter()
kept = []
dups = collections.Counter()
removed_log = {}     # md5(messages) -> (reason, category, file)


def mark(r, reason):
    removed_log[md5(json.dumps(r["messages"], sort_keys=True))] = (reason, r["category"], r["_file"])


lsh_u, lsh_a = LSH(), LSH()
sh_u, sh_a = [], []
for r in valid:
    conv = norm(" ".join(m["role"][0] + " " + m["content"] for m in body(r)))
    ek = md5(conv)
    if ek in seen_exact:
        dups["exact_duplicate"] += 1
        mark(r, "exact_duplicate")
        continue
    pk = md5(norm(user_text(r)))
    if pk in seen_prompt:
        dups["same_prompt_normalised"] += 1
        mark(r, "same_prompt_normalised")
        continue
    last_ans = norm(r["messages"][-1]["content"])
    ak = md5(last_ans)
    if len(last_ans) >= 25 and ans_count[ak] >= 2:
        dups["identical_answer_cap"] += 1
        mark(r, "identical_answer_cap")
        continue
    us = shingles(user_text(r))
    as_ = shingles(asst_text(r))
    usig = minhash(us) if len(us) >= 6 else None
    asig = minhash(as_) if len(as_) >= 10 else None
    near = False
    if usig:
        for j in lsh_u.candidates(usig):
            if kept[j]["category"] == r["category"] and jacc(us, sh_u[j]) >= 0.8:
                near = True
                break
    if not near and asig:
        for j in lsh_a.candidates(asig):
            if jacc(as_, sh_a[j]) >= 0.9 and jacc(us, sh_u[j]) >= 0.5:
                near = True
                break
    if near:
        dups["near_duplicate"] += 1
        mark(r, "near_duplicate")
        continue
    idx = len(kept)
    kept.append(r)
    sh_u.append(us)
    sh_a.append(as_)
    seen_exact.add(ek)
    seen_prompt[pk] = idx
    ans_count[ak] += 1
    if usig:
        lsh_u.add(idx, usig)
    if asig:
        lsh_a.add(idx, asig)
stats["duplicates_removed"] = dict(dups)
stats["duplicates_removed_total"] = sum(dups.values())
print("after dedup", len(kept), dict(dups))

# ------------- template-repetition trim (Phase 2.1) --------------
# Cluster each category's examples by a masked "prompt skeleton" (numbers, quoted text and capitalised words masked) plus the
# assistant's 3-word opener.  Inside a cluster larger than TEMPLATE_KEEP only the first TEMPLATE_KEEP survive, preferring hand-written
# and executed examples over composed/computed ones.  Trimming never takes a category below its target minimum.
TEMPLATE_KEEP = 12
SRC_RANK = {"authored": 0, "verified": 1, "computed": 2, "composed": 3}


def skeleton(text):
    t = re.sub(r"`[^`]*`|\"[^\"]*\"|'[^']*'", " Q ", text)
    t = re.sub(r"\b\d+(?:[.,]\d+)?\b", " N ", t)
    t = re.sub(r"\b[A-Z][a-z]+\b", " W ", t)
    return re.sub(r"[^a-zA-Z]+", " ", t).lower().strip()[:90]


def tpl_key(r):
    first_u = next(m["content"] for m in r["messages"] if m["role"] == "user")
    return (r["category"], r["subcategory"], skeleton(first_u), " ".join(norm(r["messages"][-1]["content"]).split()[:3]))


trimmed_template = collections.Counter()
cnt_cat = collections.Counter(r["category"] for r in kept)
clusters = collections.defaultdict(list)
for idx, r in enumerate(kept):
    clusters[tpl_key(r)].append(idx)
drop = set()
for key, idxs in sorted(clusters.items(), key=lambda kv: -len(kv[1])):
    if len(idxs) <= TEMPLATE_KEEP:
        continue
    cat = key[0]
    order = sorted(idxs, key=lambda i: (SRC_RANK.get(kept[i]["source_type"], 9), md5(json.dumps(kept[i]["messages"], sort_keys=True))))
    for i in order[TEMPLATE_KEEP:]:
        if cnt_cat[cat] - 1 >= TARGETS.get(cat, 0):
            drop.add(i)
            cnt_cat[cat] -= 1
            trimmed_template[cat] += 1
# second pass: same subcategory AND same first-4-word assistant opener (and, for tool/boundary families, same system prompt) beyond a
# per-opener allowance are repeated "stock" answers; trim the composed/computed ones first.
OPENER_KEEP = 22
cl2 = collections.defaultdict(list)
for idx, r in enumerate(kept):
    if idx in drop:
        continue
    cl2[(r["category"], r["subcategory"], " ".join(norm(r["messages"][-1]["content"]).split()[:4]))].append(idx)
for key, idxs in sorted(cl2.items(), key=lambda kv: -len(kv[1])):
    if len(idxs) <= OPENER_KEEP:
        continue
    cat = key[0]
    order = sorted(idxs, key=lambda i: (SRC_RANK.get(kept[i]["source_type"], 9), md5(json.dumps(kept[i]["messages"], sort_keys=True))))
    for i in order[OPENER_KEEP:]:
        if cnt_cat[cat] - 1 >= TARGETS.get(cat, 0):
            drop.add(i)
            cnt_cat[cat] -= 1
            trimmed_template[cat] += 1
for i in drop:
    mark(kept[i], "template_trim")
kept = [r for i, r in enumerate(kept) if i not in drop]
stats["template_trimmed"] = dict(trimmed_template)
stats["template_trimmed_total"] = sum(trimmed_template.values())
print("template-trimmed", stats["template_trimmed_total"], dict(trimmed_template))

# ------------- balance (cap each category) --------------
by_cat = collections.defaultdict(list)
for r in kept:
    by_cat[r["category"]].append(r)
final = []
cluster_size = collections.Counter(tpl_key(r) for r in kept)
dropped_balance = collections.Counter()
PRIORITY_SUBS = {"tool_flow", "knowledge_dialog", "plan_revision", "report_failure_honestly", "interpret_tool_result", "resist_fabrication", "resist_pressure"}
for cat, rs in by_cat.items():
    cap = CAP_OVERRIDE.get(cat, int(TARGETS.get(cat, 1000) * CAP_FACTOR))
    if len(rs) > cap:
        prio = [r for r in rs if r["subcategory"] in PRIORITY_SUBS]
        rest = [r for r in rs if r["subcategory"] not in PRIORITY_SUBS]
        room = max(0, cap - len(prio))
        # deterministic, diversity-preserving: round-robin over subcategories
        subs = collections.defaultdict(list)
        # within a sub-category prefer examples from small template clusters; the ones dropped at the cap are the most repeated shapes
        for r in sorted(rest, key=lambda x: (cluster_size[tpl_key(x)], md5(json.dumps(x["messages"], sort_keys=True)))):
            subs[r["subcategory"]].append(r)
        chosen = []
        keys = sorted(subs)
        while len(chosen) < room:
            progressed = False
            for k in keys:
                if subs[k] and len(chosen) < room:
                    chosen.append(subs[k].pop(0))
                    progressed = True
            if not progressed:
                break
        dropped_balance[cat] = len(rs) - len(chosen) - len(prio)
        keep_ids = {id(x) for x in prio + chosen}
        for x in rs:
            if id(x) not in keep_ids:
                mark(x, "balance_cap")
        rs = prio + chosen
    final.extend(rs)
stats["balance_dropped"] = dict(dropped_balance)

# ids, group repair
cnt_group = collections.Counter(r["group"] for r in final)
for r in final:
    r["id"] = "qj2-" + md5(json.dumps(r["messages"], sort_keys=True))[:12]
    if cnt_group[r["group"]] > 25:       # a 'group' that is really a whole generator family -> treat each instance independently
        r["group"] = r["id"]
    r["validation_status"] = "accepted"
final.sort(key=lambda r: r["id"])
print("final", len(final))

# ------------- split (group-aware, stratified by category) --------------
split_of = {}
rng = random.Random(20261009)
for cat in sorted({r["category"] for r in final}):
    rs = [r for r in final if r["category"] == cat]
    groups = collections.defaultdict(list)
    for r in rs:
        groups[r["group"]].append(r)
    gl = sorted(groups)
    rng.shuffle(gl)
    n = len(rs)
    tgt_test, tgt_val = round(n * 0.05), round(n * 0.05)
    c_test = c_val = 0
    for g in gl:
        sz = len(groups[g])
        if c_test < tgt_test:
            s = "test"; c_test += sz
        elif c_val < tgt_val:
            s = "validation"; c_val += sz
        else:
            s = "train"
        for r in groups[g]:
            split_of[r["id"]] = s

# leakage control: any validation/test record that is too similar to a train record (prompt Jaccard >= 0.7, or an identical long
# answer), or whose long answer also appears in the other held-out split, is moved to train. Repeats until stable because
# moving a record into train can create new conflicts.
moved = collections.Counter()
by_id = {r["id"]: r for r in final}
sh_cache = {r["id"]: shingles(user_text(r)) for r in final}
ans_key = {r["id"]: norm(r["messages"][-1]["content"]) for r in final}
ans_long = {r["id"]: len(r["messages"][-1]["content"]) >= 60 for r in final}
changed = True
while changed:
    changed = False
    inv = collections.defaultdict(list)
    train_ids = [i for i, s in split_of.items() if s == "train"]
    train_ans = set()
    for i in train_ids:
        for s in sh_cache[i]:
            inv[s].append(i)
        if ans_long[i]:
            train_ans.add(ans_key[i])
    held_seen = {}
    for rid in sorted(split_of):
        s = split_of[rid]
        if s == "train":
            continue
        u = sh_cache[rid]
        leak = False
        if len(u) >= 6:
            cand = collections.Counter()
            for sh in u:
                lst = inv.get(sh)
                if lst and len(lst) < 500:
                    cand.update(lst)
            for j, c in cand.most_common(8):
                if c / len(u | sh_cache[j]) >= 0.7:
                    leak = True
                    break
        a = ans_key[rid]
        if not leak and ans_long[rid]:
            if a in train_ans:
                leak = True
            elif a in held_seen and held_seen[a] != s:
                leak = True
            else:
                held_seen.setdefault(a, s)
        if leak:
            moved[s] += 1
            split_of[rid] = "train"
            changed = True
stats["leakage_moved_to_train"] = dict(moved)

splits = collections.defaultdict(list)
for r in final:
    splits[split_of[r["id"]]].append(r)

# ------------- write --------------
def clean(r):
    meta = {k: r[k] for k in ("id", "category", "subcategory", "difficulty", "source_type", "group", "validation_status") if k in r}
    meta["turns"] = checks.turn_counts(r)
    if "meta" in r:
        meta["extra"] = r["meta"]
    return {"id": r["id"], "messages": r["messages"], "metadata": meta}


def dump(path, recs, messages_only=False):
    with open(path, "w", encoding="utf-8") as f:
        for r in recs:
            o = {"messages": r["messages"]} if messages_only else clean(r)
            f.write(json.dumps(o, ensure_ascii=False) + "\n")


dump(VAL / f"{VERSION}_all.jsonl", final)
for name in ("train", "validation", "test"):
    recs = sorted(splits[name], key=lambda r: r["id"])
    dump(SPL / f"{name}.jsonl", recs)
    dump(SPL / f"{name}_messages_only.jsonl", recs, messages_only=True)
for r in rejected:
    removed_log[md5(json.dumps(r["messages"], sort_keys=True))] = ("validation_rejected", r["category"], "")
json.dump(removed_log, open(REP / "removed_log.json", "w", encoding="utf-8"))
with open(REP / "rejected.jsonl", "w", encoding="utf-8") as f:
    for r in rejected:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")

cat_counts = collections.Counter(r["category"] for r in final)
split_cat = {s: dict(collections.Counter(r["category"] for r in rs)) for s, rs in splits.items()}
stats.update({
    "final_total": len(final),
    "category_counts": {c: cat_counts.get(c, 0) for c in TARGETS},
    "category_targets": TARGETS,
    "split_counts": {s: len(rs) for s, rs in splits.items()},
    "split_by_category": split_cat,
    "multi_turn_examples": sum(1 for r in final if checks.turn_counts(r) >= 2),
    "multi_turn_by_split": {s: sum(1 for r in rs if checks.turn_counts(r) >= 2) for s, rs in splits.items()},
    "source_types": dict(collections.Counter(r["source_type"] for r in final)),
    "with_system_message": sum(1 for r in final if r["messages"][0]["role"] == "system"),
    "difficulty": dict(collections.Counter(r["difficulty"] for r in final)),
})
json.dump(stats, open(REP / "build_stats.json", "w", encoding="utf-8"), indent=1)
print(json.dumps({k: stats[k] for k in ("final_total", "category_counts", "split_counts", "multi_turn_examples", "leakage_moved_to_train")}, indent=1))
