"""Diversity / template diagnostics over a *_all.jsonl file.  Stdlib only.
usage: python validation/diversity.py [path] [out.json]
Metrics per category: distinct assistant 4-word openers, top opener share, distinct user 3-word openers, skeleton ratio
(distinct prompt skeletons / examples, where numbers and capitalised/quoted tokens are masked), and mean pairwise-ish
near-template clustering via skeleton buckets."""
import collections, json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dataset/validated/qwen_jarvis_dataset_v1_all.jsonl"


def skel(t):
    t = re.sub(r"`[^`]*`|\"[^\"]*\"|'[^']*'", " Q ", t)
    t = re.sub(r"\b\d+(?:[.,]\d+)?\b", " N ", t)
    t = re.sub(r"\b[A-Z][a-z]+\b", " W ", t)
    return re.sub(r"[^a-zA-Z]+", " ", t).lower().strip()


def words(t, n):
    return " ".join(re.sub(r"[^a-z0-9' ]+", " ", t.lower()).split()[:n])


def analyse(recs):
    by = collections.defaultdict(list)
    for r in recs:
        by[r["metadata"]["category"]].append(r)
    out = {}
    for c, rs in sorted(by.items()):
        first_u = [next(m["content"] for m in r["messages"] if m["role"] == "user") for r in rs]
        last_a = [r["messages"][-1]["content"] for r in rs]
        ao = collections.Counter(words(a, 4) for a in last_a)
        uo = collections.Counter(words(u, 3) for u in first_u)
        sk = collections.Counter(skel(u)[:140] for u in first_u)
        ask = collections.Counter(re.sub(r"\s+", " ", skel(a))[:90] for a in last_a)
        out[c] = {"n": len(rs),
                  "distinct_asst_openers_4w": len(ao), "top_asst_opener_share": round(ao.most_common(1)[0][1] / len(rs), 3),
                  "distinct_user_openers_3w": len(uo), "top_user_opener_share": round(uo.most_common(1)[0][1] / len(rs), 3),
                  "prompt_skeleton_ratio": round(len(sk) / len(rs), 3), "answer_skeleton_ratio": round(len(ask) / len(rs), 3),
                  "top_asst_openers": ao.most_common(4)}
    allr = recs
    allu = [next(m["content"] for m in r["messages"] if m["role"] == "user") for r in allr]
    alla = [r["messages"][-1]["content"] for r in allr]
    out["_overall"] = {"n": len(allr),
                       "prompt_skeleton_ratio": round(len({skel(u)[:140] for u in allu}) / len(allr), 3),
                       "answer_skeleton_ratio": round(len({re.sub(r"\s+", " ", skel(a))[:90] for a in alla}) / len(allr), 3),
                       "distinct_asst_openers_4w": len({words(a, 4) for a in alla}),
                       "distinct_user_openers_3w": len({words(u, 3) for u in allu})}
    return out


if __name__ == "__main__":
    recs = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    res = analyse(recs)
    if len(sys.argv) > 2:
        json.dump(res, open(sys.argv[2], "w", encoding="utf-8"), indent=1)
    for c, v in res.items():
        print(c, {k: v[k] for k in v if k != "top_asst_openers"})
