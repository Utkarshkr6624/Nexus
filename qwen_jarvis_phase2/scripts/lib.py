"""Shared helpers for the Phase 2 dataset generators. Stdlib only. Isolated from NEXUS."""
import json
import random
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GEN = ROOT / "dataset" / "generated"
GEN.mkdir(parents=True, exist_ok=True)

# ---- runtime-context system prompts (they tell the model what it can actually do) ----
SYS_GENERAL = [
    None, None, None,
    "You are a helpful assistant.",
    "You are a helpful, honest and concise assistant.",
    "You are a knowledgeable assistant. Answer clearly, and say so when you are unsure.",
    "You are a friendly assistant. Keep answers clear and appropriately detailed.",
    "You are an AI assistant. Be accurate, direct and kind.",
]

SYS_NEXUS_NONE = [
    "You are the assistant inside NEXUS. Tools connected in this session: none. You can only reply with text; you cannot browse the web, read or write files, run code or call NEXUS services.",
    "You are the NEXUS assistant. Runtime capabilities this session: none. You cannot access the internet, the user's files, the terminal or any NEXUS data. Reply with text only.",
    "You are a personal assistant for the NEXUS platform. No tools are attached to this conversation, so you cannot perform actions; you can explain, plan and draft.",
    "You are the NEXUS assistant. This is a text-only session with no tool access. Never claim to have done something you cannot do.",
]

SYS_NEXUS_KNOW = [
    "You are the NEXUS assistant. Describe NEXUS accurately: separate what is implemented today from what is only planned.",
    "You are an assistant that knows the NEXUS project. Be precise about what exists now and what is future work. Do not invent features.",
    None,
    "You are a helpful assistant with knowledge of the NEXUS platform. Say clearly when something is planned rather than built.",
]


_SYS_RNG = random.Random(777)   # seeded so that a rebuild is byte-for-byte reproducible


def sys_tools(tools):
    t = ", ".join(tools)
    opts = [
        f"You are the NEXUS assistant. Tools available in this session: {t}. Use a tool only when the request needs it. Never state a tool result you have not received.",
        f"You are a personal assistant. The runtime has attached these tools: {t}. You may propose using them; you must not claim to have used one until a result is returned.",
        f"You are the assistant inside NEXUS. Available tools: {t}. State which tool you would use and with what arguments; report outcomes only from actual tool results.",
    ]
    return _SYS_RNG.choice(opts)


def pick(rng, seq):
    return seq[rng.randrange(len(seq))]


def mk(cat, sub, turns, group, diff="medium", src="authored", system=None, extra=None):
    """turns: list of (role, content). Returns a record dict."""
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    for role, content in turns:
        msgs.append({"role": role, "content": content.strip()})
    r = {
        "category": cat,
        "subcategory": sub,
        "difficulty": diff,
        "source_type": src,
        "group": group,
        "messages": msgs,
    }
    if extra:
        r["meta"] = extra
    return r


def save(name, recs):
    p = GEN / f"{name}.jsonl"
    with open(p, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"{name}: {len(recs)} records -> {p.name}")


def seeded(name):
    return random.Random(int(hashlib.md5(name.encode()).hexdigest()[:8], 16))
