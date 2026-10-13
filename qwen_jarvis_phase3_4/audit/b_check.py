import json, hashlib, re, random, ast, collections, sys
D = r"E:\Nexo\qwen_jarvis_phase3_4\dataset\v2_final"
man = json.load(open(D + r"\MANIFEST.json"))
S = {}
for n in ["train", "validation", "test"]:
    p = f"{D}\\{n}.jsonl"
    raw = open(p, "rb").read()
    h = hashlib.sha256(raw).hexdigest()
    recs = [json.loads(l) for l in raw.decode("utf8").splitlines() if l.strip()]
    S[n] = recs
    m = man["files"][n + ".jsonl"]
    print(n, len(recs), "count_ok", len(recs) == m["records"], "sha_ok", h == m["sha256"])
print("keys", collections.Counter(tuple(sorted(r.keys())) for rs in S.values() for r in rs).most_common(5))
r0 = S["train"][0]; print(json.dumps(r0)[:600])
