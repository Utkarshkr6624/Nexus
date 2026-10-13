import json, re, sys, collections
sys.path.insert(0, r"E:\Nexo\qwen_jarvis_phase3_4\tool_protocol")
import validate_protocol as V
D = r"E:\Nexo\qwen_jarvis_phase3_4\dataset\v2_final"
BY = V.BY_NAME
DECL = re.compile(r"(?i)^\s*(no\b|nope|don't|do not|cancel|stop|wait|never mind|not now|actually no|hold on)")
CLAIM = re.compile(r"(?i)\b(I (ran|have run|executed|deleted|sent|created|wrote|saved|fetched|searched|moved|updated|booked)|has been (deleted|sent|created|saved|moved|updated)|the (output|result) (was|is|shows)|done[.!]|successfully)\b")
decl = prevmis = claims = 0; sidecalls = 0; ex = []
for n in ["train", "validation", "test"]:
    for l in open(f"{D}\\{n}.jsonl", encoding="utf8"):
        r = json.loads(l); ms = r["messages"]
        for i, m in enumerate(ms):
            if m["role"] == "assistant" and m.get("tool_calls"):
                for c in m["tool_calls"]:
                    nm = c["function"]["name"]
                    if BY[nm]["requires_confirmation"]:
                        sidecalls += 1
                        j = i - 1; us = []
                        while j >= 0 and ms[j]["role"] != "assistant": us.append(ms[j]["content"]); j -= 1
                        if us and DECL.search(us[0]): decl += 1; ex.append(("decl", r["id"], us[0][:60]))
                        pv = ms[j]["content"] if j >= 0 else ""
                        if nm not in pv: prevmis += 1; ex.append(("prevmis", r["id"], nm, pv[:80]))
            # claims of actions with no tool message anywhere before it in record
            if m["role"] == "assistant" and m.get("content") and r.get("tools") and CLAIM.search(m["content"]):
                before = [x for x in ms[:i] if x["role"] == "tool"]
                if not before: claims += 1; ex.append(("claim", r["id"], m["content"][:110].replace("\n", " ")))
print("side-effect calls", sidecalls, "after decline-looking user turn", decl, "preview names other tool", prevmis, "action-claim w/o prior tool msg", claims)
for e in ex[:25]: print(e)
