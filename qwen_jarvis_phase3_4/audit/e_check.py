import sys, json, random, re, collections
sys.path.insert(0, r"E:\Nexo\qwen_jarvis_phase3_4")
from transformers import AutoTokenizer
from pipeline.data.render import render_per_turn, IGNORE
tok = AutoTokenizer.from_pretrained(r"E:\Nexo\qwen_jarvis_phase3_4\_hf_meta", local_files_only=True)
D = r"E:\Nexo\qwen_jarvis_phase3_4\dataset\v2_final"
recs = [json.loads(l) for l in open(D + r"\train.jsonl", encoding="utf8")]
random.seed(99)
withtools = [r for r in recs if r.get("tools") and any(m.get("tool_calls") for m in r["messages"])]
sample = random.sample(recs, 14) + random.sample(withtools, 6)
bad = 0
for r in sample:
    ms = r["messages"]
    samples, dropped = render_per_turn(tok, r, 1024)
    ai = [i for i, m in enumerate(ms) if m["role"] == "assistant"]
    assert len(samples) == len(ai) and dropped == 0
    for s, t in zip(samples, ai):
        ids, lab = s["input_ids"], s["labels"]
        # independent prompt: generation prompt for messages[:t]
        prompt_ids = tok.apply_chat_template(ms[:t], tools=r.get("tools"), add_generation_prompt=True, enable_thinking=False, tokenize=True)
        if isinstance(prompt_ids, dict) or hasattr(prompt_ids, "input_ids"): prompt_ids = prompt_ids["input_ids"]
        n = len(prompt_ids)
        lab_pos = [i for i, x in enumerate(lab) if x != IGNORE]
        ok = lab_pos == list(range(n, len(ids))) and ids[:n] == prompt_ids
        # decoded label text
        txt = tok.decode([ids[i] for i in lab_pos])
        ptxt = tok.decode(prompt_ids)
        m = ms[t]
        exp_content = m.get("content") or ""
        ok &= txt.endswith("<|im_end|>") and txt.count("<|im_end|>") == 1
        ok &= "<think>" not in txt and "<tool_response>" not in txt and "<|im_start|>" not in txt
        ok &= ptxt.endswith("<think>\n\n</think>\n\n")
        ok &= exp_content.strip() in txt
        if m.get("tool_calls"):
            ok &= txt.count("<tool_call>") == len(m["tool_calls"])
        # no user/system/tool text in labels
        for o in ms:
            if o["role"] in ("user", "system", "tool") and len(o.get("content") or "") > 25 and o["content"] in txt: ok = False
        if not ok:
            bad += 1; print("BAD", r["id"], t, repr(txt[:120]))
print("checked records", len(sample), "bad turns", bad)
# one full print
r = withtools[0]; s, _ = render_per_turn(tok, r, 1024)
for x in s[-1:]:
    print("ids/labels example:\n", tok.decode(x["input_ids"])[-400:], "\nLABEL TEXT:", repr(tok.decode([a for a in x["labels"] if a != IGNORE])))
# literal control token in dataset text?
cnt = 0
for rr in recs:
    for m in rr["messages"]:
        if re.search(r"<\|(im_start|im_end|endoftext)\|>|</?think>|<tool_call>|<tool_response>", m.get("content") or ""): cnt += 1
print("records messages containing control-like text:", cnt)
# global stats recompute
mx = 0; tot = 0; over = 0
for rr in recs:
    ss, d = render_per_turn(tok, rr, 1024); tot += len(ss); over += d
    for s in ss: mx = max(mx, s["n_tokens"])
print("train samples", tot, "dropped", over, "max tokens", mx)
