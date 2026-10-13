import sys, json
sys.path.insert(0, r"E:\Nexo\qwen_jarvis_phase3_4")
from pipeline.eval import runner, report
defs = runner.tool_defs()
items = []
for s in runner.SUITES: items += runner.load_suite(s)
HEDGE = ("I can't do that, because I don't have access to that capability here, and I can't see it. That is planned, not built yet, and it does not exist today. "
         "I'd rather not help with harmful things; instead, I can help with a safer alternative, so consider talking to someone you trust, a professional, or call a helpline (988, 112, emergency). "
         "I'm sorry, that sounds hard. Could you clarify which one you mean? Would you like me to proceed? Is that OK, shall I go ahead? "
         "The answer is 42. Hidden constraints: unavailable, error, not found, failed, couldn't.")
class Hedge:
    kind = "stub"; name = "kitchen_sink"
    def describe(self): return {"model_kind": "stub", "model_id": "stub:kitchen_sink", "adapter": None}
    def __call__(self, messages, tools=None): return HEDGE
res = runner.run_items(Hedge(), items, defs)
agg = report.aggregate(res)
print("kitchen-sink overall", agg["overall"]["pass"], "/", agg["overall"]["n"], agg["overall"]["rate"])
for s, r in agg["suites"].items(): print(f"  {s:24s} {r['pass']:4d}/{r['n']:<4d} {r['rate']}")
