import json, re, random, ast, collections
D = r"E:\Nexo\qwen_jarvis_phase3_4\dataset\v2_final"
S = {n: [json.loads(l) for l in open(f"{D}\\{n}.jsonl", encoding="utf8") if l.strip()] for n in ["train","validation","test"]}
ROLES = {"system","user","assistant","tool"}
bad = 0
for n, rs in S.items():
    for r in rs:
        ms = r["messages"]
        ok = ms and all(m["role"] in ROLES for m in ms) and ms[-1]["role"] == "assistant"
        ok = ok and any(m["role"]=="user" for m in ms)
        for m in ms:
            if m["role"]!="assistant" and not isinstance(m.get("content"),str): ok=False
            if m["role"]=="assistant" and not (m.get("content") or m.get("tool_calls")): ok=False
        if not ok: bad+=1; print("BAD", n, r["id"])
print("schema bad", bad)
# categories
cats = {n: collections.Counter(r["metadata"]["category"] for r in rs) for n, rs in S.items()}
allc = set().union(*[set(c) for c in cats.values()])
for c in sorted(allc): print(c, [cats[n].get(c,0) for n in S])
mt = sum(1 for rs in S.values() for r in rs if sum(m["role"]=="user" for m in r["messages"])>1)
print("multi-turn", mt, "total", sum(len(v) for v in S.values()))
# exact dup convs
seen = collections.Counter(json.dumps(r["messages"], sort_keys=True) for rs in S.values() for r in rs)
print("exact dup convs", sum(1 for v in seen.values() if v>1))
ids = collections.Counter(r["id"] for rs in S.values() for r in rs); print("dup ids", sum(1 for v in ids.values() if v>1))
norm = lambda s: re.sub(r"[^a-z0-9 ]","",s.lower()).split()
def lastuser(r): return [m for m in r["messages"] if m["role"]=="user"][-1]["content"]
def firstuser(r): return [m for m in r["messages"] if m["role"]=="user"][0]["content"]
def sh(t): w=t; return {tuple(w[i:i+3]) for i in range(max(1,len(w)-2))}
tr = S["train"]
trp = collections.defaultdict(list)
for r in tr: trp[" ".join(norm(lastuser(r)))].append(r["id"])
trsh = [(sh(norm(lastuser(r))), r) for r in tr]
inv = collections.defaultdict(set)
for i,(s,_) in enumerate(trsh):
    for g in s: inv[g].add(i)
for n in ["validation","test"]:
    eq=0; near=[]; ans=0
    tra = {" ".join(norm(m["content"])) for r in tr for m in r["messages"] if m["role"]=="assistant" and m.get("content") and len(m["content"])>150}
    for r in S[n]:
        p = " ".join(norm(lastuser(r)))
        if p in trp: eq+=1
        s = sh(norm(lastuser(r)))
        cand = collections.Counter(i for g in s for i in inv[g])
        best=0;bi=None
        for i,c in cand.most_common(30):
            t=trsh[i][0]; j=len(s&t)/len(s|t)
            if j>best: best=j;bi=i
        if best>=0.7 and len(s)>=3: near.append((best,r["id"],trsh[bi][1]["id"]))
        for m in r["messages"]:
            if m["role"]=="assistant" and m.get("content") and len(m["content"])>150 and " ".join(norm(m["content"])) in tra: ans+=1; break
    print(n, "prompt-equal", eq, "near>=0.7", len(near), "long-answer-identical recs", ans)
    for x in sorted(near,reverse=True)[:8]: print("  ", x)
# val vs test
vp = {" ".join(norm(lastuser(r))) for r in S["validation"]}
print("val-test prompt equal", sum(" ".join(norm(lastuser(r))) in vp for r in S["test"]))
# code blocks / json blocks
cb=pyfail=js=jsfail=0
for rs in S.values():
    for r in rs:
        for m in r["messages"]:
            if m["role"]!="assistant" or not m.get("content"): continue
            for lang, code in re.findall(r"```(\w*)\n(.*?)```", m["content"], re.S):
                if lang in ("python","py"):
                    cb+=1
                    try: ast.parse(code)
                    except Exception as e:
                        pyfail+=1
                        if pyfail<=8: print("PYFAIL", r["id"], str(e)[:60], code[:80].replace("\n","|"))
                if lang=="json":
                    js+=1
                    try: json.loads(code)
                    except Exception:
                        jsfail+=1
                        if jsfail<=5: print("JSFAIL", r["id"], code[:80].replace("\n","|"))
print("py blocks",cb,"fail",pyfail,"json blocks",js,"fail",jsfail)
# garbled
pat = re.compile(r"\{\{|\}\}|\{[a-z_]+\}|<\|im_|TODO|lorem|\[INSERT|<think>|\bNone\b$")
g=0
for n,rs in S.items():
    for r in rs:
        for m in r["messages"]:
            if m["role"] in("user","assistant") and m.get("content") and pat.search(m["content"]):
                g+=1
                if g<=10: print("GARBLE?", n, r["id"], pat.search(m["content"]).group(0), m["content"][:80].replace("\n","|"))
print("garble hits", g)
random.seed(777)
allr=[(n,r) for n,rs in S.items() for r in rs]
by=collections.defaultdict(list)
for n,r in allr: by[r["metadata"]["category"]].append(r)
out=[]
for c,l in sorted(by.items()):
    for r in random.sample(l,5): out.append((c,r))
with open(r"E:\Nexo\qwen_jarvis_phase3_4\audit\_sample60.txt","w",encoding="utf8") as f:
    for c,r in out:
        f.write(f"\n=== {c} {r['id']}\n")
        for m in r["messages"]:
            f.write(f"[{m['role']}] {(m.get('content') or '')[:700]} {json.dumps(m.get('tool_calls'))[:300] if m.get('tool_calls') else ''}\n")
