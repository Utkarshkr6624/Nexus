import json, re, collections
D = r"E:\Nexo\qwen_jarvis_phase3_4\dataset\v2_final"
S = {n: [json.loads(l) for l in open(f"{D}\\{n}.jsonl", encoding="utf8")] for n in ["train","validation","test"]}
miss1=[]; whatis=[]; dblspace=0
for n,rs in S.items():
    for r in rs:
        for m in r["messages"]:
            c=m.get("content") or ""
            if m["role"]=="assistant":
                nums=re.findall(r"(?m)^(\d+)\. ",c)
                if nums and nums[0]!="1": miss1.append((n,r["id"],r["metadata"]["category"],c[:60].replace("\n","|")))
            if m["role"]=="user" and re.search(r"(?i)\bwhat is how\b|\bwhat is why\b|\bwhat is what\b",c): whatis.append((n,r["id"],c[:60]))
print("assistant numbered list not starting at 1:",len(miss1), collections.Counter(x[2] for x in miss1)); print(miss1[:3])
print("'What is how' prompts:",len(whatis), whatis[:2])
