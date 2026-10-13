"""Shared helpers for the qwen_jarvis_dataset_v3 generators (stdlib only, deterministic)."""
import hashlib
import json
import random
from pathlib import Path

WS = Path(__file__).resolve().parent.parent
V2 = WS / "dataset" / "v2_final"
VERSION = "qwen_jarvis_dataset_v3"

GENERAL_SYS = ["You are a helpful assistant.", "You are an AI assistant. Be accurate, direct and kind.", "You are a helpful, honest and concise assistant.",
               "You are a knowledgeable assistant. Answer clearly, and say so when you are unsure.",
               "You are a friendly assistant. Keep answers clear and appropriately detailed."]
TEXT_ONLY_SYS = ["You are a personal assistant for the NEXUS platform. No tools are attached to this conversation, so you cannot perform actions; you can explain, plan and draft.",
                 "You are the NEXUS assistant. This is a text-only session with no tool access. Never claim to have done something you cannot do.",
                 "You are the assistant inside NEXUS. Tools connected in this session: none. You can only reply with text; you cannot browse the web, read or write files, run code or call NEXUS services."]
TOOL_SYS = ["You are the NEXUS assistant. Use a tool only when the request needs it. Never state a tool result you have not received.",
            "You are a personal assistant. You may propose using the attached tools; you must not claim to have used one until a result is returned.",
            "You are the assistant inside NEXUS. State which tool you would use and with what arguments; report outcomes only from actual tool results."]


def load_v2():
    out = {}
    for sp in ("train", "validation", "test"):
        out[sp] = [json.loads(l) for l in open(V2 / f"{sp}.jsonl", encoding="utf-8")]
    return out


def tool_defs():
    """Exact tool definitions as they appear in V2 records (one canonical form per tool, verified unique)."""
    defs = {}
    for sp in load_v2().values():
        for r in sp:
            for t in r.get("tools", []):
                defs[t["function"]["name"]] = t
    return defs


def env(ok, data=None, code=None, message=None):
    """toolproto-1 result envelope: compact JSON, ensure_ascii=False."""
    body = {"ok": True, "data": data} if ok else {"ok": False, "error": {"code": code, "message": message}}
    return json.dumps(body, separators=(",", ":"), ensure_ascii=False)


class Maker:
    def __init__(self, family, seed):
        self.family, self.rng, self.out = family, random.Random(seed), []

    def sys(self, pool, p_none=0.3):
        return None if self.rng.random() < p_none else self.rng.choice(pool)

    def add(self, messages, category, sub, *, system=None, tools=None, source_type="computed", group=None, difficulty="medium"):
        msgs = ([{"role": "system", "content": system}] if system else []) + messages
        key = json.dumps(msgs, ensure_ascii=False, sort_keys=True)
        h = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
        rec = {"id": f"qj3n-{h}", "messages": msgs,
               "metadata": {"id": f"qj3n-{h}", "category": category, "subcategory": sub, "difficulty": difficulty, "source_type": source_type,
                            "group": group or f"v3_{self.family}_{h}", "validation_status": "accepted",
                            "turns": sum(m["role"] == "user" for m in messages), "source_id": None, "edited": False, "dataset_version": VERSION,
                            "v3_family": self.family}}
        if tools:
            rec["tools"] = tools
        self.out.append(rec)
        return rec
