"""Read-only evidence from the frozen training data (dataset/v2_final) for the Phase 6 behaviour questions:
identity (JARVIS / Qwen / NEXUS), fenced JSON on "JSON only" requests, and inability phrases ("I can't send").
Counts are of conversations whose ASSISTANT turns match. Output: results/dataset_evidence.json. Never writes to the dataset."""
import collections
import json
import re
import sys
from pathlib import Path

DS = Path(sys.argv[1] if len(sys.argv) > 1 else r"E:\Nexo\Jarvis\_resume_test\code\dataset\v2_final")
OUT = Path(__file__).resolve().parents[1] / "results" / "dataset_evidence.json"


def turns(r, role):
    return [m.get("content") or "" for m in r["messages"] if m["role"] == role]


def rows(name):
    with open(DS / f"{name}.jsonl", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh]


def hits(rs, pat, role="assistant"):
    rx = re.compile(pat, re.I)
    return [r for r in rs if any(rx.search(t) for t in turns(r, role))]


def sample(rs, pat, n=3):
    rx = re.compile(pat, re.I)
    out = []
    for r in rs[:n]:
        a = next(t for t in turns(r, "assistant") if rx.search(t))
        i = rx.search(a).start()
        out.append({"id": r["id"], "category": r["metadata"]["category"], "subcategory": r["metadata"].get("subcategory"),
                    "user": turns(r, "user")[-1][:160], "assistant_excerpt": a[max(0, i - 120):i + 120]})
    return out


def main():
    man = json.loads((DS / "MANIFEST.json").read_text(encoding="utf-8"))
    tr = rows("train")
    ev = {"dataset": man["version"], "train_records": len(tr)}
    for key, pat in {"assistant_mentions_jarvis": r"\bjarvis\b", "assistant_mentions_qwen": r"\bqwen\b", "assistant_cant_send": r"I can(?:'|’)t send"}.items():
        h = hits(tr, pat)
        ev[key] = {"rows": len(h), "categories": collections.Counter(r["metadata"]["category"] for r in h).most_common(5),
                   "subcategories": collections.Counter(r["metadata"].get("subcategory") for r in h).most_common(5), "examples": sample(h, pat)}
    ev["system_prompts_mentioning_jarvis"] = len(hits(tr, r"\bjarvis\b", "system"))
    ev["system_prompts_only_reply_with_text"] = len(hits(tr, r"only reply with text", "system"))
    idq = hits(tr, r"who are you|your name|introduce yourself|what are you\b", "user")
    ev["identity_questions"] = [{"id": r["id"], "user": turns(r, "user")[0][:120], "assistant": turns(r, "assistant")[0][:200]} for r in idq]
    jq = [r for r in tr if any(re.search(r"\bjson\b", u, re.I) and re.search(r"only|exactly|nothing else|no (?:markdown|code fence|explanation)", u, re.I)
                               for u in turns(r, "user"))]
    ev["json_only_requests"] = {"rows": len(jq), "assistant_answers_with_code_fence": sum(1 for r in jq if "```" in " ".join(turns(r, "assistant"))),
                                "examples": [{"id": r["id"], "user": turns(r, "user")[-1][:120], "assistant": turns(r, "assistant")[-1][:120]} for r in jq[:3]]}
    ev["verbatim_or_exact_output_instructions"] = len(hits(tr, r"reply with exactly|respond with only|output exactly|verbatim", "user"))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(ev, indent=1, ensure_ascii=False), encoding="utf-8")
    for k in ("assistant_mentions_jarvis", "assistant_mentions_qwen", "assistant_cant_send"):
        print(k, ev[k]["rows"], ev[k]["categories"])
    print("identity_questions", len(ev["identity_questions"]), "| json_only", ev["json_only_requests"]["rows"], "fenced", ev["json_only_requests"]["assistant_answers_with_code_fence"],
          "| exact-output instructions", ev["verbatim_or_exact_output_instructions"], "| sys jarvis", ev["system_prompts_mentioning_jarvis"])


if __name__ == "__main__":
    main()
