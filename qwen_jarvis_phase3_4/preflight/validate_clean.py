"""Validation gate for a dataset directory (default dataset/v2_clean).  usage: python validate_clean.py [dir] [out.json]
Checks: Phase-2 per-record checks (structure, placeholders, fabricated actions, NEXUS claims, tool awareness, boundaries), exact + normalised
prompt duplicates, near-duplicate prompts between held-out splits and train, identical long answers across splits, code-block validity
(python/json/javascript/sql syntax, full C/C++/Java programs), category counts and multi-turn count.  Exit code 1 on any hard failure."""
import collections
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
WS = HERE.parent
sys.path.insert(0, str(WS.parent / "qwen_jarvis_phase2" / "validation"))
import checks  # noqa: E402  (Phase 2 per-record checks; read-only import)

D = Path(sys.argv[1]) if len(sys.argv) > 1 else WS / "dataset" / "v2_clean"
OUTJ = Path(sys.argv[2]) if len(sys.argv) > 2 else HERE / "validation_results.json"
TARGETS = {"general_conversation": 1000, "general_knowledge": 1000, "reasoning": 1500, "programming": 1500, "mathematics": 750, "instruction_following": 750,
           "jarvis_assistant": 1000, "nexus_knowledge": 750, "tool_aware": 750, "capability_boundaries": 500, "multi_turn_context": 500, "safety": 500}


def norm(t):
    return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()


def md5(t):
    return hashlib.md5(t.encode("utf-8")).hexdigest()


def shingles(t, k=3):
    w = norm(t).split()
    return {" ".join(w[i:i + k]) for i in range(max(0, len(w) - k + 1))} if len(w) >= k else ({" ".join(w)} if w else set())


def utext(r):
    return " ".join(m["content"] for m in r["messages"] if m["role"] == "user")


splits = {s: [json.loads(l) for l in open(D / f"{s}.jsonl", encoding="utf-8") if l.strip()] for s in ("train", "validation", "test")}
allr = [r for s in splits.values() for r in s]
res = {"dir": str(D), "total": len(allr), "split_counts": {s: len(v) for s, v in splits.items()}, "hard_failures": []}

# 1. phase-2 per-record checks (they expect category/subcategory at top level)
def legacy_view(msgs):
    """chat+tools records -> the Phase-2 convention so the Phase-2 content checks apply unchanged: tool messages become user '[tool result ...]' messages"""
    out = []
    for m in msgs:
        if m["role"] == "tool":
            body = "[tool result from " + m.get("name", "tool") + "]" + chr(10) + m["content"]
            if out and out[-1]["role"] == "user" and out[-1]["content"].startswith("[tool result"):
                out[-1] = {"role": "user", "content": out[-1]["content"] + chr(10) + body}
            else:
                out.append({"role": "user", "content": body})
        elif m["role"] == "assistant" and m.get("tool_calls"):
            out.append({"role": "assistant", "content": m["content"] or "(calls a tool)"})
        else:
            out.append({"role": m["role"], "content": m["content"]})
    return out


fails = collections.Counter()
examples = []
for r in allr:
    rr = {"messages": legacy_view(r["messages"]), "category": r["metadata"]["category"], "subcategory": r["metadata"]["subcategory"]}
    probs = []
    for c in checks.ALL:
        probs += c(rr)
    if probs:
        fails[probs[0].split(":")[0]] += 1
        examples.append((r["id"], probs[0][:100]))
res["content_check_failures"] = dict(fails)
res["content_check_examples"] = examples[:10]
if fails:
    res["hard_failures"].append("per-record content checks failed")

# 2. duplicates
ids = [r["id"] for r in allr]
res["unique_ids"] = len(set(ids)) == len(ids)
conv = collections.Counter(md5(json.dumps(r["messages"], sort_keys=True)) for r in allr)
res["exact_duplicate_conversations"] = sum(v - 1 for v in conv.values() if v > 1)
pr = collections.Counter(md5(norm(utext(r))) for r in allr)
res["duplicate_normalised_prompts"] = sum(v - 1 for v in pr.values() if v > 1)
if res["exact_duplicate_conversations"] or not res["unique_ids"]:
    res["hard_failures"].append("duplicates")

# 3. leakage
tr_sh = [(shingles(utext(r)), r["id"]) for r in splits["train"]]
inv = collections.defaultdict(list)
for i, (sh, _) in enumerate(tr_sh):
    for s in sh:
        inv[s].append(i)
pr_by = {s: {md5(norm(utext(r))) for r in rs} for s, rs in splits.items()}
ans_by = {s: {md5(norm(r["messages"][-1]["content"])) for r in rs if len(r["messages"][-1]["content"]) >= 60} for s, rs in splits.items()}
leak = {}
for a, b in (("train", "validation"), ("train", "test"), ("validation", "test")):
    leak[f"{a}_vs_{b}_identical_prompts"] = len(pr_by[a] & pr_by[b])
    leak[f"{a}_vs_{b}_identical_long_answers"] = len(ans_by[a] & ans_by[b])
near = {}
for name in ("validation", "test"):
    n = 0
    for r in splits[name]:
        u = shingles(utext(r))
        if len(u) < 6:
            continue
        cand = collections.Counter()
        for s in u:
            lst = inv.get(s)
            if lst and len(lst) < 500:
                cand.update(lst)
        for j, c in cand.most_common(8):
            if c / len(u | tr_sh[j][0]) >= 0.7:
                n += 1
                break
    near[f"{name}_near_duplicate_prompts_vs_train"] = n
leak.update(near)
def _long(r, n=100):
    return {md5(norm(m["content"])) for m in r["messages"] if m["role"] == "assistant" and len(m["content"]) >= n}


def _first(r):
    return md5(norm(next(m["content"] for m in r["messages"] if m["role"] == "user")))


tr_long = set().union(*(_long(r) for r in splits["train"]))
tr_first = {_first(r) for r in splits["train"]}
for name in ("validation", "test"):
    leak[f"{name}_records_sharing_a_long_assistant_turn_with_train"] = sum(1 for r in splits[name] if _long(r) & tr_long)
    leak[f"{name}_records_sharing_first_prompt_with_train"] = sum(1 for r in splits[name] if _first(r) in tr_first)
vl, tl = set().union(*(_long(r) for r in splits["validation"])), set().union(*(_long(r) for r in splits["test"]))
leak["validation_vs_test_shared_long_assistant_turns"] = len(vl & tl)
leak["validation_vs_test_shared_first_prompts"] = len({_first(r) for r in splits["validation"]} & {_first(r) for r in splits["test"]})
res["leakage"] = leak
if any(leak.values()):
    res["hard_failures"].append("train/validation/test leakage")

# 4. categories, multi-turn
cat = collections.Counter(r["metadata"]["category"] for r in allr)
res["category_counts"] = {c: cat.get(c, 0) for c in TARGETS}
res["category_vs_phase2_minimum"] = {c: cat.get(c, 0) - t for c, t in TARGETS.items()}
mt = sum(1 for r in allr if sum(1 for m in r["messages"] if m["role"] == "user") >= 2)
res["multi_turn_examples"] = mt
res["with_tools_attached"] = sum(1 for r in allr if r.get("tools"))
res["with_tool_calls"] = sum(1 for r in allr if any(m.get("tool_calls") for m in r["messages"]))
if len(allr) < 10000:
    res["hard_failures"].append("fewer than 10000 examples")
if mt < 1000:
    res["hard_failures"].append("fewer than 1000 multi-turn examples")
for s in splits:
    missing = [c for c in TARGETS if not any(r["metadata"]["category"] == c for r in splits[s])]
    if missing:
        res["hard_failures"].append(f"categories missing from {s}: {missing}")

# 5. code blocks
tmp = Path(os.environ.get("P2_SCRATCH", tempfile.gettempdir())) / "p34_clean_code"
tmp.mkdir(parents=True, exist_ok=True)
cnt = collections.Counter()
bad = []
for r in allr:
    for m in r["messages"]:
        if m["role"] != "assistant":
            continue
        for lang, code in re.findall(r"```(\w+)\n(.*?)```", m["content"], flags=re.S):
            if lang == "python":
                cnt["python"] += 1
                try:
                    compile(code, "<b>", "exec")
                except SyntaxError as e:
                    cnt["python_bad"] += 1
                    bad.append((r["id"], "python", str(e)[:60]))
            elif lang == "json":
                cnt["json"] += 1
                try:
                    json.loads(code)
                except Exception as e:
                    cnt["json_bad"] += 1
                    bad.append((r["id"], "json", str(e)[:60]))
            elif lang == "javascript":
                cnt["javascript"] += 1
                f = tmp / f"b{cnt['javascript']}.js"
                f.write_text(code, encoding="utf-8")
                p = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
                if p.returncode:
                    cnt["javascript_bad"] += 1
                    bad.append((r["id"], "javascript", p.stderr[:60]))
            elif lang == "sql":
                cnt["sql"] += 1
                t = code.strip()
                t = t if t.endswith(";") else t + ";"
                if not sqlite3.complete_statement(t):
                    cnt["sql_bad"] += 1
                    bad.append((r["id"], "sql", t[:60]))
            elif lang in ("c", "cpp", "java") and re.search(r"\bmain\s*\(", code):
                cnt[lang + "_program"] += 1
                d = tmp / f"p{sum(v for k, v in cnt.items() if k.endswith('_program'))}"
                d.mkdir(exist_ok=True)
                if lang == "java":
                    cls = re.search(r"public\s+class\s+(\w+)", code)
                    fn = (cls.group(1) if cls else "Main") + ".java"
                    (d / fn).write_text(code, encoding="utf-8")
                    cmd = ["javac", fn]
                else:
                    fn = "x.cpp" if lang == "cpp" else "x.c"
                    (d / fn).write_text(code, encoding="utf-8")
                    cmd = ["g++" if lang == "cpp" else "gcc", "-std=c++17" if lang == "cpp" else "-std=c11", "-fsyntax-only", fn]
                p = subprocess.run(cmd, capture_output=True, text=True, cwd=d)
                if p.returncode:
                    cnt[lang + "_program_bad"] += 1
                    bad.append((r["id"], lang, p.stderr[:60]))
res["code_blocks"] = dict(cnt)
res["code_bad_examples"] = bad[:10]
if any(k.endswith("_bad") for k in cnt):
    res["hard_failures"].append("invalid code blocks")

json.dump(res, open(OUTJ, "w", encoding="utf-8"), indent=1)
print(json.dumps({k: v for k, v in res.items() if k not in ("content_check_examples",)}, indent=1)[:3500])
print("HARD FAILURES:", res["hard_failures"] or "none")
sys.exit(1 if res["hard_failures"] else 0)
