"""Independent validation of the FINAL dataset files (does not trust the generators).
Run:  python validation/validate_final.py
Writes reports/final_validation.json and prints a summary. Exit code 1 if any hard check fails."""
import collections
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "validation"))
import checks  # noqa: E402

SPL = ROOT / "dataset" / "splits"
VAL = ROOT / "dataset" / "validated"
REP = ROOT / "reports"
TARGETS = {"general_conversation": 1000, "general_knowledge": 1000, "reasoning": 1500, "programming": 1500, "mathematics": 750, "instruction_following": 750,
           "jarvis_assistant": 1000, "nexus_knowledge": 750, "tool_aware": 750, "capability_boundaries": 500, "multi_turn_context": 500, "safety": 500}


def load(p):
    return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]


def norm(t):
    return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()


def body(r):
    return [m for m in r["messages"] if m["role"] != "system"]


def utext(r):
    return " ".join(m["content"] for m in body(r) if m["role"] == "user")


def shingles(t, k=3):
    w = norm(t).split()
    return {" ".join(w[i:i + k]) for i in range(max(1, len(w) - k + 1))} if w else set()


res = {"hard_failures": [], "checks": {}}
splits = {s: load(SPL / f"{s}.jsonl") for s in ("train", "validation", "test")}
allrecs = [r for s in splits.values() for r in s]
res["checks"]["total_examples"] = len(allrecs)
res["checks"]["split_counts"] = {s: len(v) for s, v in splits.items()}

# 1. messages-only files consistent with full files
mo_ok = True
for s, recs in splits.items():
    mo = load(SPL / f"{s}_messages_only.jsonl")
    if len(mo) != len(recs) or any(set(m.keys()) != {"messages"} or m["messages"] != r["messages"] for m, r in zip(mo, recs)):
        mo_ok = False
res["checks"]["messages_only_files_consistent"] = mo_ok
if not mo_ok:
    res["hard_failures"].append("messages_only files inconsistent")

# 2. ids unique, schema + all content checks re-run on the final files
ids = [r["id"] for r in allrecs]
res["checks"]["unique_ids"] = len(ids) == len(set(ids))
probs = collections.Counter()
bad_examples = []
for r in allrecs:
    rec = {"messages": r["messages"], "category": r["metadata"]["category"], "subcategory": r["metadata"]["subcategory"]}
    for chk in checks.ALL:
        p = chk(rec)
        for x in p:
            probs[x.split(":")[0]] += 1
            if len(bad_examples) < 10:
                bad_examples.append((r["id"], x))
res["checks"]["content_check_failures"] = dict(probs)
if probs:
    res["hard_failures"].append(f"content checks failed: {dict(probs)}")

# 3. duplicates in the final set
conv = collections.Counter(norm(" ".join(m["role"][0] + m["content"] for m in body(r))) for r in allrecs)
prompts = collections.Counter(norm(utext(r)) for r in allrecs)
res["checks"]["exact_duplicate_conversations"] = sum(v - 1 for v in conv.values() if v > 1)
res["checks"]["duplicate_prompts"] = sum(v - 1 for v in prompts.values() if v > 1)
if res["checks"]["exact_duplicate_conversations"] or res["checks"]["duplicate_prompts"]:
    res["hard_failures"].append("duplicates in final set")

# 4. cross-split leakage
pr_by_split = {s: {norm(utext(r)) for r in recs} for s, recs in splits.items()}
ans_by_split = {s: {norm(r["messages"][-1]["content"]) for r in recs if len(r["messages"][-1]["content"]) >= 60} for s, recs in splits.items()}
leak = {}
for a, b in (("train", "validation"), ("train", "test"), ("validation", "test")):
    leak[f"{a}_vs_{b}_identical_prompts"] = len(pr_by_split[a] & pr_by_split[b])
    leak[f"{a}_vs_{b}_identical_long_answers"] = len(ans_by_split[a] & ans_by_split[b])
# near-duplicate prompts between test/validation and train (shingle Jaccard >= 0.8), via inverted index on shingles
tr_sh = [(shingles(utext(r)), r["id"]) for r in splits["train"]]
inv = collections.defaultdict(list)
for i, (sh, _) in enumerate(tr_sh):
    for s in sh:
        inv[s].append(i)
for name in ("validation", "test"):
    n = 0
    for r in splits[name]:
        sh = shingles(utext(r))
        if len(sh) < 6:
            continue
        cand = collections.Counter()
        for s in sh:
            lst = inv.get(s)
            if lst and len(lst) < 400:
                cand.update(lst)
        for j, c in cand.most_common(5):
            if c / len(sh | tr_sh[j][0]) >= 0.8:
                n += 1
                break
    leak[f"{name}_near_duplicate_prompts_vs_train"] = n
res["checks"]["leakage"] = leak
if any(v for k, v in leak.items() if "identical_prompts" in k):
    res["hard_failures"].append("identical prompts across splits")

# 5. distribution, multi-turn, system prompt coverage
cat = collections.Counter(r["metadata"]["category"] for r in allrecs)
res["checks"]["category_counts"] = {c: cat.get(c, 0) for c in TARGETS}
res["checks"]["category_pct_of_target"] = {c: round(100 * cat.get(c, 0) / t) for c, t in TARGETS.items()}
mt = sum(1 for r in allrecs if checks.turn_counts({"messages": r["messages"]}) >= 2)
res["checks"]["multi_turn_examples"] = mt
res["checks"]["multi_turn_ge_1000"] = mt >= 1000
if mt < 1000:
    res["hard_failures"].append("fewer than 1000 multi-turn examples")
res["checks"]["total_ge_10000"] = len(allrecs) >= 10000
if len(allrecs) < 10000:
    res["hard_failures"].append("fewer than 10000 examples")
res["checks"]["category_minimums_met"] = {c: cat.get(c, 0) >= t for c, t in TARGETS.items()}   # Phase 2.1 gate
for c, t in TARGETS.items():
    if cat.get(c, 0) < t:
        res["hard_failures"].append(f"category below minimum: {c} {cat.get(c, 0)} < {t}")
res["checks"]["with_system_message"] = sum(1 for r in allrecs if r["messages"][0]["role"] == "system")
res["checks"]["largest_category_share_pct"] = round(100 * max(cat.values()) / len(allrecs), 1)
words = [sum(len(m["content"].split()) for m in r["messages"]) for r in allrecs]
res["checks"]["words_per_example"] = {"mean": round(sum(words) / len(words), 1), "max": max(words), "p95": sorted(words)[int(0.95 * len(words))]}
res["checks"]["approx_tokens_total"] = int(sum(words) * 1.35)
res["checks"]["source_types"] = dict(collections.Counter(r["metadata"]["source_type"] for r in allrecs))

# 6. code blocks in assistant messages
py_total = py_bad = js_total = js_bad = json_total = json_bad = sql_total = sql_bad = comp_total = comp_bad = 0
import sqlite3  # noqa: E402
bad_code = []
tmpdir = Path(os.environ.get("P2_SCRATCH", tempfile.gettempdir())) / "p2_final"
tmpdir.mkdir(parents=True, exist_ok=True)
for r in allrecs:
    for m in r["messages"]:
        if m["role"] != "assistant":
            continue
        for lang, code in re.findall(r"```(\w+)\n(.*?)```", m["content"], flags=re.S):
            if lang == "python":
                py_total += 1
                try:
                    compile(code, "<block>", "exec")
                except SyntaxError as e:
                    py_bad += 1
                    bad_code.append((r["id"], "python", str(e)[:60]))
            elif lang == "json":
                json_total += 1
                try:
                    json.loads(code)
                except Exception as e:
                    json_bad += 1
                    bad_code.append((r["id"], "json", str(e)[:60]))
            elif lang in ("javascript",):
                js_total += 1
                f = tmpdir / f"b{js_total}.js"
                f.write_text(code, encoding="utf-8")
                p = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
                if p.returncode:
                    js_bad += 1
                    bad_code.append((r["id"], "javascript", p.stderr[:60]))
            elif lang == "sql":
                sql_total += 1
                text = code.strip()
                text = text if text.endswith(";") else text + ";"
                # every ';'-terminated statement must be syntactically complete for SQLite's tokenizer
                if not sqlite3.complete_statement(text):
                    sql_bad += 1
                    bad_code.append((r["id"], "sql", text[:60]))
            elif lang in ("c", "cpp", "java") and re.search(r"\bmain\s*\(", code):
                comp_total += 1
                d = tmpdir / f"cc{comp_total}"
                d.mkdir(exist_ok=True)
                if lang == "java":
                    cls = re.search(r"public\s+class\s+(\w+)", code)
                    (d / f"{cls.group(1) if cls else 'Main'}.java").write_text(code, encoding="utf-8")
                    cmd = ["javac", f"{cls.group(1) if cls else 'Main'}.java"]
                else:
                    fn = "x.cpp" if lang == "cpp" else "x.c"
                    (d / fn).write_text(code, encoding="utf-8")
                    cmd = ["g++" if lang == "cpp" else "gcc", "-std=c++17" if lang == "cpp" else "-std=c11", "-fsyntax-only", fn]
                p = subprocess.run(cmd, capture_output=True, text=True, cwd=d)
                if p.returncode:
                    comp_bad += 1
                    bad_code.append((r["id"], lang, p.stderr[:60]))
res["checks"]["code_blocks"] = {"python": [py_total, py_bad], "json": [json_total, json_bad], "javascript": [js_total, js_bad], "sql_syntax": [sql_total, sql_bad],
                                "c_cpp_java_full_programs_compiled": [comp_total, comp_bad], "bad_examples": bad_code[:8]}
if py_bad or json_bad or js_bad or sql_bad or comp_bad:
    res["hard_failures"].append("syntactically invalid code blocks in assistant messages")

# 7. grounding of tool-flow answers (answer must reuse content from the tool result it follows)
flow = grounded = 0
ungrounded = []
for r in allrecs:
    msgs = r["messages"]
    for i, m in enumerate(msgs):
        if m["role"] == "user" and m["content"].startswith("[tool result") and i + 1 < len(msgs) and msgs[i + 1]["role"] == "assistant":
            flow += 1
            result = m["content"].split("\n", 1)[1] if "\n" in m["content"] else ""
            toks = {t for t in re.findall(r"[A-Za-z0-9_.#'/-]+", result.lower()) if len(t) >= 3 or t.isdigit()} - {"error", "ok", "the", "and", "exit", "code", "count"}
            ans = msgs[i + 1]["content"].lower().replace(",", "")
            empty_result = bool(re.fullmatch(r"\s*(no results\.?|0 rows|\(no tasks\)|\[\]|none)\s*", result.lower()))
            if empty_result:
                ok_g = bool(re.search(r"\b(no|nothing|empty|zero|0)\b", ans))
            else:
                ok_g = any(t.replace(",", "") in ans for t in toks) or not toks
            if ok_g:
                grounded += 1
            else:
                ungrounded.append(r["id"])
res["checks"]["tool_result_answers"] = {"count": flow, "grounded_in_result": grounded, "ungrounded_ids": ungrounded[:5]}
if ungrounded:
    res["hard_failures"].append("tool-result answers not grounded in the result")

# 8. tool-awareness consistency
none_ctx = proposals_without_tools = tools_ctx = claims_in_none = 0
for r in allrecs:
    sysm = r["messages"][0]["content"] if r["messages"][0]["role"] == "system" else ""
    asst = " ".join(m["content"] for m in r["messages"] if m["role"] == "assistant")
    if re.search(r"(Tools connected in this session: none|Runtime capabilities this session: none|No tools are attached|no tool access)", sysm):
        none_ctx += 1
        if "Proposed call" in asst:
            proposals_without_tools += 1
    elif "Tools available" in sysm or "tools:" in sysm or "Available tools" in sysm:
        tools_ctx += 1
res["checks"]["tool_context"] = {"no_tools_examples": none_ctx, "tools_attached_examples": tools_ctx, "proposals_when_no_tools": proposals_without_tools}
if proposals_without_tools:
    res["hard_failures"].append("tool proposals although no tools attached")

# 9. independent recomputation of a sample of maths answers from the prompt text
ok = bad = skipped = 0
fails = []
for r in allrecs:
    if r["metadata"]["category"] != "mathematics":
        continue
    q = r["messages"][-2]["content"]
    a = r["messages"][-1]["content"]
    sub = r["metadata"]["subcategory"]
    m_ans = re.search(r"Answer:\s*(.+)", a)
    got = m_ans.group(1).strip() if m_ans else ""
    expect = None
    try:
        if sub == "order_of_operations":
            m = re.search(r"(?:What is|Evaluate|compute)\s+(.+?)(?:\?| and show)", q)
            expr = m.group(1).replace("^", "**")
            expect = str(int(eval(expr))) if float(eval(expr)).is_integer() else str(eval(expr))
        elif sub == "linear_equation":
            m = re.search(r"(\d+)x ([+-]) (\d+) = (-?\d+)", q)
            a_, s_, b_, c_ = int(m.group(1)), m.group(2), int(m.group(3)), int(m.group(4))
            b_ = b_ if s_ == "+" else -b_
            expect = "x = " + str((c_ - b_) // a_)
        elif sub == "number_theory" and "gcd(" in q:
            m = re.search(r"gcd\((\d+), (\d+)\)", q)
            x, y = int(m.group(1)), int(m.group(2))
            expect = f"gcd = {math.gcd(x, y)}, lcm = {x * y // math.gcd(x, y)}"
        elif sub == "combinatorics" and "chosen from" in q:
            m = re.search(r"can (\d+) people be chosen from (\d+)", q)
            expect = str(math.comb(int(m.group(2)), int(m.group(1))))
        elif sub == "statistics" and q.startswith("Find the mean and median of:"):
            nums = [int(x) for x in q.split(":", 1)[1].replace("\n", " ").split(",")]
            import statistics as st
            expect = f"mean ≈ {sum(nums) / len(nums):.2f}, median = {st.median(nums)}"
        elif sub == "geometry" and "hypotenuse" in q and "and hypotenuse" not in q and "inscribed" not in q:   # known v1 template only
            m = re.search(r"legs (\d+) and (\d+)", q)
            x, y = int(m.group(1)), int(m.group(2))
            expect = f"hypotenuse {math.sqrt(x * x + y * y):g}, area {x * y / 2:g}"
        elif sub == "algorithm_math" and q.startswith("A recursive") is False and "binary search" in q:
            m = re.search(r"list of ([\d,]+) items", q)
            n = int(m.group(1).replace(",", ""))
            expect = f"about {math.floor(math.log2(n)) + 1}"
        elif sub == "units" and "to miles" in q:
            m = re.search(r"Convert ([\d.]+) km", q)
            km = float(m.group(1))
            expect = f"≈ {km / 1.609344:.2f} miles; {km * 1000:g} m"
        elif sub == "percent" and q.startswith("What is "):
            m = re.search(r"What is (\d+)% of (\d+)\?", q)
            from fractions import Fraction as F_
            v = F_(int(m.group(1)) * int(m.group(2)), 100)
            expect = str(v.numerator) if v.denominator == 1 else f"{v.numerator}/{v.denominator}"
        elif sub == "finance_math" and "compound interest" in q:
            m = re.search(r"\$(\d+) at (\d+)% compound interest per year, how much will I have after (\d+) years", q)
            P_, r_, n_ = int(m.group(1)), int(m.group(2)), int(m.group(3))
            expect = f"about ${P_ * (1 + r_ / 100) ** n_:,.2f}. The interest earned is ${P_ * (1 + r_ / 100) ** n_ - P_:,.2f}."
        elif sub == "matrices" and "determinant" in q:
            m = re.search(r"\[(-?\d+) (-?\d+); (-?\d+) (-?\d+)\]", q)
            a_, b_, c_, d_ = map(int, m.groups())
            expect = f"det = {a_ * d_ - b_ * c_}"
        elif sub == "matrices" and q.startswith("Multiply A"):
            ms = re.findall(r"\[(-?\d+) (-?\d+); (-?\d+) (-?\d+)\]", q)
            A_, B_ = [list(map(int, x)) for x in ms]
            C_ = [A_[0] * B_[0] + A_[1] * B_[2], A_[0] * B_[1] + A_[1] * B_[3], A_[2] * B_[0] + A_[3] * B_[2], A_[2] * B_[1] + A_[3] * B_[3]]
            expect = f"AB = [{C_[0]} {C_[1]}; {C_[2]} {C_[3]}]"
        elif sub == "discrete_math" and "subsets" in q:
            n_ = int(re.search(r"with (\d+) elements", q).group(1))
            expect = f"{2 ** n_} subsets; {math.comb(n_, 2)} of size 2"
        elif sub == "sequences" and "arithmetic sequence" in q:
            m = re.search(r"starts at (\d+) with common difference (\d+).*?the (\d+)th term", q)
            s_, e_, k_ = map(int, m.groups())
            expect = f"term {k_} = {s_ + (k_ - 1) * e_}; sum = {k_ * (2 * s_ + (k_ - 1) * e_) // 2}"
        elif sub == "probability" and "sum is" in q:
            t_ = int(re.search(r"sum is (\d+)", q).group(1))
            n_ = sum(1 for i in range(1, 7) for j in range(1, 7) if i + j == t_)
            from fractions import Fraction as F_
            fr_ = F_(n_, 36)
            expect = f"{fr_.numerator}/{fr_.denominator} (about {n_ / 36:.3f})" if fr_.denominator != 1 else None
        elif sub == "statistics" and q.startswith("What's the mode"):
            nums = [int(x) for x in q.split("of", 1)[1].strip(" ?").split(",")]
            import statistics as st2
            expect = ", ".join(map(str, sorted(st2.multimode(nums))))
    except Exception:
        expect = None
    if expect is None:
        skipped += 1
        continue
    if got == expect:
        ok += 1
    else:
        bad += 1
        fails.append((r["id"], sub, expect, got))
res["checks"]["math_recomputation"] = {"checked": ok + bad, "agree": ok, "disagree": bad, "not_checked": skipped, "disagreements": fails[:6]}
if bad:
    res["hard_failures"].append("maths answers disagree with recomputation")

# 10. independent re-solving of reasoning puzzles from the prompt text
import itertools
rk = rbad = rskip = 0
rfails = []
for r in allrecs:
    if r["metadata"]["category"] != "reasoning":
        continue
    q = r["messages"][-2]["content"]
    a = r["messages"][-1]["content"]
    sub = r["metadata"]["subcategory"]
    if sub == "ordering":
        clues = re.findall(r"(\w+) is (?:taller|older|faster|heavier|earlier) than (\w+)", q)
        people = sorted({p for c in clues for p in c})
        sols = []
        for perm in itertools.permutations(people):
            pos = {p: i for i, p in enumerate(perm)}
            if all(pos[x] < pos[y] for x, y in clues):
                sols.append(perm)
        m = re.search(r"Full order from \w+ to \w+: ([^.]+)\.", a)
        if len(sols) == 1 and m:
            rk += 1
            if [x.strip() for x in m.group(1).split(",")] != list(sols[0]):
                rbad += 1
                rfails.append((r["id"], "ordering"))
        else:
            rskip += 1
    elif sub == "pattern":
        m = re.search(r":\s*([-\d,\s]+?),?\s*\.\.\.", q)
        ans = re.search(r"Answer:\s*(-?\d+)", a)
        if m and ans:
            nums = [int(x) for x in re.findall(r"-?\d+", m.group(1))]
            nxt = None
            if len(nums) >= 3 and len({nums[i + 1] - nums[i] for i in range(len(nums) - 1)}) == 1:
                nxt = nums[-1] + (nums[1] - nums[0])
            elif len(nums) >= 3 and all(nums[i] for i in range(len(nums) - 1)) and len({nums[i + 1] / nums[i] for i in range(len(nums) - 1)}) == 1:
                nxt = int(nums[-1] * nums[1] / nums[0])
            if nxt is None:
                rskip += 1
            else:
                rk += 1
                if int(ans.group(1)) != nxt:
                    rbad += 1
                    rfails.append((r["id"], "pattern"))
        else:
            rskip += 1
    else:
        rskip += 1
res["checks"]["reasoning_recomputation"] = {"checked": rk, "agree": rk - rbad, "disagree": rbad, "not_checked": rskip, "disagreements": rfails[:6]}
if rbad:
    res["hard_failures"].append("reasoning answers disagree with re-solving")

json.dump(res, open(REP / "final_validation.json", "w", encoding="utf-8"), indent=1, ensure_ascii=False)
print(json.dumps({k: v for k, v in res["checks"].items() if k not in ("leakage",)}, indent=1, ensure_ascii=False)[:6000])
print("LEAKAGE:", json.dumps(res["checks"]["leakage"], indent=1))
print("HARD FAILURES:", res["hard_failures"] or "none")
sys.exit(1 if res["hard_failures"] else 0)
