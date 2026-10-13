"""Shared helpers for the preflight audit. Stdlib only. Reads the Phase 2 v2 splits (read-only)."""
import hashlib
import json
import re
import sys
from pathlib import Path

WS = Path(__file__).resolve().parent.parent
P2 = WS.parent / "qwen_jarvis_phase2"
SPLIT_SRC = P2 / "dataset" / "splits"
CLEAN = WS / "dataset" / "v2_clean"
PF = WS / "preflight"
sys.path.insert(0, str(P2 / "validation"))
sys.path.insert(0, str(P2 / "scripts"))

SPLITS = ("train", "validation", "test")


def jl(path):
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def load_src():
    out = []
    for s in SPLITS:
        for r in jl(SPLIT_SRC / f"{s}.jsonl"):
            r["_split"] = s
            out.append(r)
    return out


def md5(t):
    return hashlib.md5(t.encode("utf-8")).hexdigest()


def msgs_hash(messages):
    return md5(json.dumps(messages, sort_keys=True, ensure_ascii=False))


def norm(t):
    return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()


def user_text(r):
    return " ".join(m["content"] for m in r["messages"] if m["role"] == "user")


def asst_text(r):
    return " ".join(m["content"] for m in r["messages"] if m["role"] == "assistant")


def full_text(r):
    return "\n".join(m["content"] for m in r["messages"])


def show(r, limit=700):
    print("=" * 70, r["id"], r["metadata"]["category"], r["metadata"]["subcategory"], r.get("_split", ""))
    for m in r["messages"]:
        print(f"[{m['role'][0]}]", m["content"][:limit].replace("\n", "\n    "))
