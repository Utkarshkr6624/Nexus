import json, re, random
D = r"E:\Nexo\qwen_jarvis_phase3_4\dataset\v2_final"
recs=[]
for n in ["train","validation","test"]:
    for l in open(f"{D}\\{n}.jsonl", encoding="utf8"): recs.append(json.loads(l))
terms = re.compile(r"(?i)\b(ollama|qwen|whisper|tool registry|chat endpoint|/chat|llm|web search|terminal|file access)\b")
neg = re.compile(r"(?i)\b(no|not|n't|never|without|none|isn't|doesn't|does not|cannot|can't|planned|plan|future|would|will|could|roadmap|proposed|if|once|absent|deliberately)\b")
hits=[]
for r in recs:
    if r["metadata"]["category"] not in ("nexus_knowledge",) and not any("NEXUS" in (m.get("content") or "") for m in r["messages"] if m["role"]=="system"): pass
    for m in r["messages"]:
        if m["role"]!="assistant" or not m.get("content"): continue
        for s in re.split(r"(?<=[.!?])\s+", m["content"]):
            if terms.search(s) and not neg.search(s):
                hits.append((r["id"], r["metadata"]["category"], s[:220]))
print("sentences mentioning terms w/o negation/future cue:", len(hits))
for h in hits[:60]: print(h)
nk=[r for r in recs if r["metadata"]["category"]=="nexus_knowledge"]
random.seed(4242)
with open(r"E:\Nexo\qwen_jarvis_phase3_4\audit\_nexus40.txt","w",encoding="utf8") as f:
    for r in random.sample(nk,40):
        f.write("\n=== "+r["id"]+"\n")
        for m in r["messages"]:
            if m["role"]!="system": f.write(f"[{m['role']}] {(m.get('content') or '')[:500]}\n")
