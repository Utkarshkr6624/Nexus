"""Phase 3 preflight: apply all targeted corrections to dataset v2 and write dataset/v2_clean/{train,validation,test}.jsonl.
Source of truth for records: qwen_jarvis_phase2/dataset/splits/*.jsonl (read-only).  Every change is logged to preflight/changelog.jsonl
(id, op, reason).  Deterministic: same inputs -> byte-identical outputs.  Stdlib only."""
import collections
import hashlib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WS = HERE.parent
SRC = WS.parent / "qwen_jarvis_phase2" / "dataset" / "splits"
OUT = WS / "dataset" / "v2_clean"
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(HERE))


def md5(t):
    return hashlib.md5(t.encode("utf-8")).hexdigest()


def load():
    recs = []
    for s in ("train", "validation", "test"):
        for l in open(SRC / f"{s}.jsonl", encoding="utf-8"):
            r = json.loads(l)
            r["_split"] = s
            recs.append(r)
    return recs


log = []          # change log entries


def note(rid, op, reason, detail=""):
    log.append({"id": rid, "op": op, "reason": reason, "detail": detail})


# ------------------------------------------------------------------ 1. whole-conversation replacements (JS timing, wording slip)
def replace_turns(rec, turns, reason):
    system = [m for m in rec["messages"] if m["role"] == "system"]
    rec["messages"] = system + [{"role": t["role"], "content": t["content"]} for t in turns]
    rec["_edited"] = True
    note(rec["id"], "replace", reason)


WORDING_SLIP_ID = "qj2-555e27377677"
WORDING_SLIP_FIX = ("Keeping your phrase \"very tired\", here is a more dramatic version: \"Very tired but never late, the team delivered the project on time, "
                    "and still they were bitterly disappointed.\"")

# ------------------------------------------------------------------ 2. per-record text edits (find -> replace); every `old` must be found exactly once
EDITS = [
    ("qj2-32dc18fbfd46", "Daylight is limited, around 11 to 12 hours by late March, and weather changes quickly",
     "Daylight in March grows from roughly 10 hours at the start of the month to over 13 by the end, and weather changes quickly",
     "factual: Iceland March daylight hours"),
    ("qj2-a300424f9fdc", "Honey never really spoils. Archaeologists have found edible honey in ancient Egyptian tombs thousands of years old, because its low moisture and acidity make it very hard for microbes to survive.",
     "Honey barely spoils: its low moisture and acidity make it very hard for microbes to survive, and sealed honey found in ancient Egyptian tombs is often reported to have remained edible.",
     "factual: unhedged honey/tomb claim"),
    ("qj2-3bf02c7bc996", "In a progressive system, earning more never takes your total take-home pay lower, since only the income above each threshold is taxed at the higher rate.",
     "In a marginal-rate progressive system, a raise does not by itself lower your take-home pay, since only the income above each threshold is taxed at the higher rate (though loss of income-tested benefits can change the picture).",
     "factual: absolute tax claim softened"),
    ("qj2-c34ad73b0c73", "turbulence is uncomfortable but not dangerous", "turbulence is uncomfortable but rarely dangerous if you keep your seatbelt fastened",
     "medical/safety: absolute claim about turbulence"),
    ("qj2-4ce461abddfd", "tell the landlord early", "tell the person you owe early", "context mismatch: generic debt prompt answered with landlord wording"),
    ("qj2-4ce461abddfd", "point you to tenant or debt-advice services", "point you to debt-advice services", "context mismatch"),
    ("qj2-0b36c9c5a6a2", "Twelve of them point at an existing NEXUS service; code_assist and deep_reasoning have no destination today.",
     "Eleven of them point at an existing NEXUS service, out_of_scope gets a list of the surfaces NEXUS does have, and code_assist and deep_reasoning have no destination today.",
     "NEXUS fact: 11 intents map to a service (backend/app/ml/router.py SERVICE_TARGETS), not 12"),
    ("qj2-8b9768a9980f", "Twelve of them point at an existing NEXUS service; code_assist and deep_reasoning have no destination today.",
     "Eleven of them point at an existing NEXUS service, out_of_scope gets a list of the surfaces NEXUS does have, and code_assist and deep_reasoning have no destination today.",
     "NEXUS fact: 11 intents map to a service, not 12"),
    ("qj2-38b1a9b7ad31", "twelve of the fourteen intents map to an existing NEXUS service and these two have no destination",
     "eleven of the fourteen intents map to an existing NEXUS service (out_of_scope gets a list of the surfaces NEXUS does have) and these two have no destination",
     "NEXUS fact: 11 intents map to a service, not 12"),
    ("qj2-c4cfccdd0471", "twelve map to existing NEXUS areas or services, plus out_of_scope and the two generative ones",
     "eleven map to existing NEXUS areas or services, plus out_of_scope and the two generative ones",
     "NEXUS fact: 11 intents map to a service, not 12"),
    ("qj2-a08b42250d15", "\"No. Dataset, training and evaluation live", "\"Dataset, training and evaluation live", "wording: stray 'No.' answering a how-question"),
    ("qj2-9e64729e45bc",
     "No, and it's dangerous. Drinking bleach is poisonous and can cause severe injury or death. There's no good evidence that baking soda cures cancer, and relying on it could delay effective treatment. If anyone has swallowed something harmful, contact poison control or emergency services.",
     "No. There's no good evidence that baking soda cures cancer, and relying on it could delay effective treatment. Some people promote far more dangerous 'cures', such as drinking bleach, which is poisonous and can cause severe injury or death. If anyone has swallowed something harmful, contact poison control or emergency services.",
     "answer/question mismatch (bleach text on a baking-soda question)"),
    ("qj2-b29b3519c3b2",
     "No, and it's dangerous. Drinking bleach is poisonous and can cause severe injury or death. There's no good evidence that baking soda cures cancer, and relying on it could delay effective treatment. If anyone has swallowed something harmful, contact poison control or emergency services.",
     "No, and it's dangerous. Drinking bleach is poisonous and can cause severe injury or death, and it does not cure any disease. If anyone has swallowed it, contact poison control or emergency services right away.",
     "answer/question mismatch (baking-soda text on a bleach question)"),
]

# ------------------------------------------------------------------ 3. global regex fixes: (roles, pattern, replacement, reason)
G = [
    (("assistant",), r"\bthe the\b", "the", "duplicated word"),
    (("assistant",), r"(To help you [^,\n.]*?)\bmy\b", r"\1your", "pronoun slip in slot template ('help you ... my ...')"),
    (("assistant",), r"\bon early next week\b", "early next week", "grammar in slot template"),
    (("assistant",), r"I'd don't ", "I'd advise you not to ", "grammar in safety template"),
    (("assistant",), r"(?<=[.:;,] )i (?=[a-z])", "I ", "lowercase pronoun in template"),
    (("assistant",), r"\bbecause i'm\b", "because I'm", "lowercase pronoun in template"),
    (("assistant",), r"Based on the audit, no: no\.", "Based on the audit, no.", "doubled yes/no after status prefix"),
    (("assistant",), r"^(Today|Implemented today|In the current codebase|As things stand now)[:,] (?:yes|Yes): ", "Yes: ", "status prefix + yes"),
    (("assistant",), r"^(That is absent|It does not exist today|Not present in the current system)\. No\. ", r"\1. ", "doubled negative after status prefix"),
    (("assistant",), r"spare few minutes, ([^.\n]*?) is quick", r'spare few minutes, "\1" is quick', "ungrammatical task phrase in jarvis template"),
    (("assistant",), r" Running an evaluation is a write, so it is a POST and is idempotent\.", "", "unverified NEXUS detail removed"),
    (("assistant",), r"it will not go away on its own", "it is unlikely to go away on its own", "medical: absolute claim"),
    # safety-template glitches
    (("assistant",), r"otherwise (?:I'd see |talk to )?emergency care for allergic signs, otherwise a pharmacist", "otherwise a pharmacist can usually help with an ordinary bite", "garbled template"),
    (("assistant",), r"for ordinary follow-up, emergency care for allergic signs, otherwise a pharmacist is the right person", "for ordinary follow-up, a pharmacist is the right person", "garbled template"),
    (("assistant",), r"Otherwise, urgent care if any of those appear is the right person to ask\.", "Otherwise, a doctor is the right person to ask.", "garbled template"),
    (("assistant",), r"for ordinary follow-up, see urgent care if any of those appear", "for ordinary follow-up, see a doctor", "garbled template"),
    (("assistant",), r"for ordinary follow-up, urgent medical care is the right person", "for ordinary follow-up, a doctor is the right person", "garbled template"),
    (("assistant",), r"otherwise (?:I'd see |talk to )urgent medical care", "otherwise I'd see a doctor", "garbled template"),
    (("assistant",), r"Otherwise, I'd speak to emergency care if sudden, otherwise their doctor promptly\.", "Otherwise, I'd speak to their doctor promptly.", "garbled template"),
    (("assistant",), r"Otherwise, emergency care if sudden, otherwise their doctor promptly is the right person to ask\.", "Otherwise, their doctor is the right person to ask.", "garbled template"),
    (("assistant",), r"for ordinary follow-up, see emergency care if sudden, otherwise their doctor promptly", "for ordinary follow-up, see their doctor promptly", "garbled template"),
    (("assistant",), r"a doctor, especially if they recur is the right person", "a doctor, especially if they recur, is the right person", "grammar in template"),
    # user-side prompt glitches
    (("user",), r"\b[Hh]elp me help me\b", "Help me", "duplicated words in prompt"),
    (("user",), r"\bPlease please\b", "Please", "duplicated words in prompt"),
    (("user",), r"\bCould you you booked\b", "Can you confirm you booked", "ungrammatical prompt"),
    (("user",), r"\bCould you (what|how|where|who)", r"Could you tell me \1", "ungrammatical prompt"),
    # independent-audit findings (content defects)
    (("assistant",), r"A search of n = 1 to 2000 finds no counterexample, but the reason", "Small cases agree with it, but the reason", "unperformed-action claim ('a search ... finds')"),
    (("assistant",), r"(After that the rough sequence is:\n)((?:\d+\. [^\n]*(?:\n|$))+)", lambda m: m.group(1) + re.sub(r"^(\d+)\.", lambda n: f"{int(n.group(1)) - 1}.", m.group(2), flags=re.M),
     "numbered list started at 2 (step 1 folded into the intro)"),
    (("user",), r"\bWhat is how (bridges carry loads)\?", r"How do \1?", "template slip in prompt"),
    (("user",), r"\bWhat is how (thermostats and home energy use) work\?", r"How do \1 work?", "template slip in prompt"),
    (("user",), r"\bWhat is how ", "How ", "template slip in prompt"),
    (("user",), r"(?i)\bwhat is how (bridges carry loads)\?", r"how do \1?", "template slip in prompt"),
    (("user",), r"(?i)\bwhat is how (vaccines) are scheduled\?", r"how are \1 scheduled?", "template slip in prompt"),
    (("user",), r"(?i)\bwhat is how to (read a nutrition label)\?", r"how do you \1?", "template slip in prompt"),
    (("user",), r"(?i)\bwhat is how (thermostats and home energy use) work\?", r"how do \1 work?", "template slip in prompt"),
    (("assistant",), r'query="price the S&P 500"', 'query="S&P 500 price"', "odd generated query"),
    (("assistant",), r"(No, I don't have access to [^.,\n]*?), so I can't access it\.", r"\1.", "redundant clause"),
]
G = [(roles, re.compile(p, re.M), rep, why) for roles, p, rep, why in G]

# ------------------------------------------------------------------ 4. deletions
UNGRAM = re.compile(r"(?:\bIs my (?:child|teenager|elderly parent|toddler)\b|\babout my (?:child|teenager|elderly parent|toddler) (?:has|seems)\b|\bhad my (?:child|teenager|elderly parent) (?:has|seems)\b|\bhave my (?:child|teenager|elderly parent) (?:has|seems)\b|\bI've had my (?:child|teenager|elderly parent|toddler))", re.I)
DRAFT_BAD = re.compile(r"(?:follow up after an interview|notice of resignation|request a reference|resign|reference from)", re.I)
NAME_AGAIN = "What's my name again?"
NAME_KEEP = 4


def skeleton(text):
    t = re.sub(r"`[^`]*`|\"[^\"]*\"|'[^']*'", " Q ", text)
    t = re.sub(r"\b\d+(?:[.,]\d+)?\b", " N ", t)
    t = re.sub(r"\b[A-Z][a-z]+\b", " W ", t)
    return re.sub(r"[^a-zA-Z]+", " ", t).lower().strip()[:90]


CLUSTER_CAP = 12


def main():
    recs = load()
    by_id = {r["id"]: r for r in recs}
    n0 = len(recs)

    # 1. JS timing patches
    for l in open(HERE / "patches_js_timing.jsonl", encoding="utf-8"):
        p = json.loads(l)
        replace_turns(by_id[p["id"]], p["new_turns"], "js timing dependence removed: " + p["reason"])
    slip = by_id[WORDING_SLIP_ID]
    assert slip["messages"][-1]["role"] == "assistant" and "weaker form" in slip["messages"][-1]["content"]
    slip["messages"][-1]["content"] = WORDING_SLIP_FIX
    slip["_edited"] = True
    note(WORDING_SLIP_ID, "edit", "A9 clumsy sentence in writing-coach example fixed")

    # 2. targeted edits
    for rid, old, new, why in EDITS:
        r = by_id[rid]
        hit = 0
        for m in r["messages"]:
            if old in m["content"]:
                m["content"] = m["content"].replace(old, new)
                hit += 1
        assert hit == 1, (rid, old[:60], hit)
        r["_edited"] = True
        note(rid, "edit", why)

    # 3. global regex fixes
    for r in recs:
        for m in r["messages"]:
            if m["role"] == "system":
                continue
            for roles, rx, rep, why in G:
                if m["role"] in roles:
                    new, k = rx.subn(rep, m["content"])
                    if k:
                        m["content"] = new
                        r["_edited"] = True
                        note(r["id"], "regex", why, rx.pattern[:60])

    # 4. deletions
    delete = {}

    def mark(rid, why):
        delete.setdefault(rid, why)

    for r in recs:
        users = " || ".join(m["content"] for m in r["messages"] if m["role"] == "user")
        if UNGRAM.search(users):
            mark(r["id"], "ungrammatical slot-filled prompt")
        if r["metadata"]["category"] == "jarvis_assistant" and r["metadata"]["subcategory"] == "draft_message":
            u0 = [m["content"] for m in r["messages"] if m["role"] == "user"][0]
            if DRAFT_BAD.search(u0):
                mark(r["id"], "slot-filled draft with semantically incoherent topic (interview/resignation/reference about a project)")
    name_cluster = sorted((r for r in recs if [m["content"] for m in r["messages"] if m["role"] == "user"][-1].strip() == NAME_AGAIN and any("tight budget" in m["content"] for m in r["messages"])),
                          key=lambda r: md5(json.dumps(r["messages"], sort_keys=True)))
    for r in name_cluster[NAME_KEEP:]:
        mark(r["id"], f"template cluster: identical 'name again' dialogue repeated with different names (kept {NAME_KEEP})")
    # skeleton-cluster cap for maths / reasoning
    for cat in ("mathematics", "reasoning"):
        cl = collections.defaultdict(list)
        for r in recs:
            if r["metadata"]["category"] == cat and r["id"] not in delete:
                u0 = [m["content"] for m in r["messages"] if m["role"] == "user"][0]
                cl[skeleton(u0)].append(r)
        for sk, rs in cl.items():
            if len(rs) > CLUSTER_CAP:
                rs.sort(key=lambda r: ({"hard": 0, "medium": 1, "easy": 2}.get(r["metadata"]["difficulty"], 3), md5(json.dumps(r["messages"], sort_keys=True))))
                for r in rs[CLUSTER_CAP:]:
                    mark(r["id"], f"{cat}: prompt-shape cluster capped at {CLUSTER_CAP} (cluster size {len(rs)})")
    for rid, why in delete.items():
        note(rid, "delete", why)

    # 5. write
    kept = [r for r in recs if r["id"] not in delete]
    # dedupe again (edits can create duplicates)
    seen, final = set(), []
    for r in sorted(kept, key=lambda r: r["id"]):
        k = md5(json.dumps(r["messages"], sort_keys=True))
        if k in seen:
            note(r["id"], "delete", "exact duplicate created by an edit")
            continue
        seen.add(k)
        final.append(r)
    # leakage control after edits: a held-out record whose prompt is >= 0.7 similar (3-gram Jaccard) to a train prompt, or whose long answer also
    # appears in another split, moves to train (same rule as the Phase 2 build)
    def shing(t, k=3):
        w = re.sub(r"[^a-z0-9]+", " ", t.lower()).strip().split()
        return {" ".join(w[i:i + k]) for i in range(max(0, len(w) - k + 1))} if len(w) >= k else ({" ".join(w)} if w else set())

    def utext(r):
        return " ".join(m["content"] for m in r["messages"] if m["role"] == "user")

    moved = 0
    for _ in range(5):
        inv = collections.defaultdict(list)
        sh = {id(r): shing(utext(r)) for r in final}
        train_r = [r for r in final if r["_split"] == "train"]
        for r in train_r:
            for g in sh[id(r)]:
                inv[g].append(r)
        train_ans = {md5(re.sub(r"[^a-z0-9]+", " ", r["messages"][-1]["content"].lower()).strip()) for r in train_r if len(r["messages"][-1]["content"]) >= 60}
        changed = False
        for r in final:
            if r["_split"] == "train":
                continue
            u = sh[id(r)]
            hit = False
            if len(u) >= 6:
                cand = collections.Counter()
                for g in u:
                    lst = inv.get(g)
                    if lst and len(lst) < 500:
                        cand.update(id(x) for x in lst)
                idmap = {id(x): x for x in train_r}
                for xid, c in cand.most_common(8):
                    if c / len(u | sh[xid]) >= 0.7:
                        hit = True
                        break
            a = r["messages"][-1]["content"]
            if not hit and len(a) >= 60 and md5(re.sub(r"[^a-z0-9]+", " ", a.lower()).strip()) in train_ans:
                hit = True
            if hit:
                note(r["id"], "split", f"moved {r['_split']} -> train to remove near-duplicate/identical-answer leakage after edits")
                r["_split"] = "train"
                moved += 1
                changed = True
        if not changed:
            break
    print("leakage moves after edits:", moved)
    counts = collections.Counter()
    outs = {s: open(OUT / f"{s}.jsonl", "w", encoding="utf-8") for s in ("train", "validation", "test")}
    for r in final:
        meta = dict(r["metadata"])
        meta["source_id"] = r["id"]
        meta["edited"] = bool(r.get("_edited"))
        new_id = "qj2f-" + md5(json.dumps(r["messages"], sort_keys=True))[:12]
        outs[r["_split"]].write(json.dumps({"id": new_id, "messages": r["messages"], "metadata": meta}, ensure_ascii=False) + "\n")
        counts[r["_split"]] += 1
    for f in outs.values():
        f.close()
    with open(HERE / "changelog.jsonl", "w", encoding="utf-8") as f:
        for e in sorted(log, key=lambda e: (e["op"], e["id"], e["reason"])):
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    summ = collections.Counter((e["op"], e["reason"].split(":")[0][:40]) for e in log)
    print("source", n0, "-> clean", len(final), dict(counts))
    print("deleted", len(delete), "edited records", sum(1 for r in final if r.get("_edited")))
    for k, v in sorted(summ.items()):
        print("  ", k, v)


if __name__ == "__main__":
    main()
