"""Prints the Qwen3 chat-template rendering of one converted example per conversion kind (documentation aid).  usage: render_demo.py [dir]"""
import json
import sys
from pathlib import Path

from transformers import AutoTokenizer

HERE = Path(__file__).resolve().parent
d = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE.parent / "dataset" / "v2_final_candidate"
tok = AutoTokenizer.from_pretrained(str(HERE.parent / "_hf_meta"))
want = {"read_direct": None, "confirm_flow": None, "error_flow": None}
for l in open(d / "train.jsonl", encoding="utf-8"):
    r = json.loads(l)
    if not r.get("tools"):
        continue
    roles = [m["role"] for m in r["messages"] if m["role"] != "system"]
    users = roles.count("user")
    if "tool" not in roles:
        continue
    if users == 1 and want["read_direct"] is None:
        want["read_direct"] = r
    elif users == 2 and want["confirm_flow"] is None:
        want["confirm_flow"] = r
    elif users == 2 and want["error_flow"] is None and '"ok":false' in json.dumps(r["messages"], ensure_ascii=False):
        want["error_flow"] = r
for k, r in want.items():
    if r is None:
        continue
    print("=" * 30, k, r["id"])
    txt = tok.apply_chat_template(r["messages"], tools=r["tools"], tokenize=False, enable_thinking=False)
    print(txt[:3800])
