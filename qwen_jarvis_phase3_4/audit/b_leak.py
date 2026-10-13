import json, re, collections
D = r"E:\Nexo\qwen_jarvis_phase3_4\dataset\v2_final"
S = {n: [json.loads(l) for l in open(f"{D}\\{n}.jsonl", encoding="utf8")] for n in ["train","validation","test"]}
norm = lambda s: " ".join(re.sub(r"[^a-z0-9 ]","",s.lower()).split())
def lu(r): return [m for m in r["messages"] if m["role"]=="user"][-1]["content"]
def la(r): return r["messages"][-1].get("content") or ""
tr = collections.defaultdict(list)
for r in S["train"]: tr[norm(lu(r))].append(r)
for n in ["validation","test"]:
    k=0; sameans=0; singleturn=0; long_=0
    for r in S[n]:
        p=norm(lu(r))
        if p in tr:
            k+=1
            same=any(norm(la(x))==norm(la(r)) for x in tr[p])
            sameans+=same
            nu=sum(m["role"]=="user" for m in r["messages"])
            if len(p)>40: long_+=1
            if k<=14: print(n, r["metadata"]["category"], nu, "|", lu(r)[:70].replace("\n"," "), "| sameans", same, "ntrain", len(tr[p]))
    print(n,"overlap",k,"same last answer",sameans,"prompts>40ch",long_)
# long identical answers
tra=collections.defaultdict(list)
for r in S["train"]:
    for m in r["messages"]:
        if m["role"]=="assistant" and m.get("content") and len(m["content"])>150: tra[norm(m["content"])].append(r["id"])
for n in ["validation","test"]:
    c=0
    for r in S[n]:
        for m in r["messages"]:
            if m["role"]=="assistant" and m.get("content") and len(m["content"])>150 and norm(m["content"]) in tra:
                c+=1; print(n, r["metadata"]["category"], m["content"][:70].replace("\n"," ")); break
    print(n,"long ident ans",c)
