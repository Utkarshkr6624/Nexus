"""Phase 2.1 multi-turn context examples (category multi_turn_context): hand-written 3-6 user-turn conversations that depend on earlier turns.
Shapes deliberately differ from the Phase 2 generators (running_arithmetic, list_editing, unit_followups, changing_requirements):
remembered constraints, reference resolution, ellipsis, self-corrections, detours and returns, summaries, conflicting information,
things never provided in the chat, tutoring with learner answers, pasted-text Q&A, debugging with reused code context.
Arithmetic in the dialogues was checked with Python before writing."""
from gen21_common import *
import gen21_mt_a, gen21_mt_b, gen21_mt_c, gen21_mt_d

B = Bank("multi_turn_context", "mt21")
S = [s for s in SYS_GENERAL if s]       # non-empty general system prompts

for mod in (gen21_mt_a, gen21_mt_b, gen21_mt_c, gen21_mt_d):
    mod.add(B.d, S)

recs = B.records()
assert all(sum(1 for m in r["messages"] if m["role"] == "user") >= 3 for r in recs), "every example must have >= 3 user turns"

if __name__ == "__main__":
    save("multiturn21", recs)
