"""Discriminating repetition test: same prompts, same server session, LoRA scale 1 (shipped) vs 0 (base Q4_K_M only).

Per-request ``lora`` overrides only this request's adapter scale; the server config and the artifacts are untouched.
"""
import json, re, sys, time, urllib.request, uuid

API, LLAMA = "http://127.0.0.1:8000/api/v1", "http://127.0.0.1:8766"
OUT = sys.argv[1]


def req(url, m="GET", b=None, t=None, timeout=300):
    r = urllib.request.Request(url, data=None if b is None else json.dumps(b).encode(), method=m,
                               headers={"Content-Type": "application/json", **({"Authorization": "Bearer " + t} if t else {})})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as x:
            return x.status, json.loads(x.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def adjacent(t):
    w = re.findall(r"[a-z']+", t.lower())
    return [x for i, x in enumerate(w) if i and x == w[i - 1] and len(x) > 1]


tag, pw = uuid.uuid4().hex[:6], "Nexo-Final-2026!x"
em = f"lora.{tag}@example.com"
req(API + "/auth/register", "POST", {"username": "lora" + tag, "email": em, "password": pw})
tok = req(API + "/auth/login", "POST", {"email": em, "password": pw})[1]["access_token"]
out = {"label": "INTEGRATION (real model, direct llama-server)", "started": time.strftime("%Y-%m-%dT%H:%M:%S")}
try:
    req(API + "/jarvis/model/load", "POST", {}, tok)
    for _ in range(300):
        if req(API + "/jarvis/status", t=tok)[1]["lifecycle"]["state"] == "ready":
            break
        time.sleep(2)
    out["lora_adapters"] = req(LLAMA + "/lora-adapters")[1]
    SYS = ("You are the assistant inside NEXUS. State which tool you would use and with what arguments; "
           "report outcomes only from actual tool results. Your name is NEXO.")
    prompts = {
        "fact": "At what temperature does water boil at sea level?",
        "plan": "Give me a short 3-step plan to prepare for my exams next week.",
        "chat": "Hi! Who are you and what can you help me with?",
        "explain": "Explain in a few sentences how a hash map works.",
        "capital": "What is the capital of France? Answer in one sentence.",
        "long": "Describe the water cycle in about 150 words.",
    }
    runs = {}
    for scale in (1.0, 0.0):
        for name, p in prompts.items():
            msgs = [{"role": "system", "content": SYS}, {"role": "user", "content": p}]
            prompt = req(LLAMA + "/apply-template", "POST", {"messages": msgs, "chat_template_kwargs": {"enable_thinking": False}})[1]["prompt"]
            s = time.monotonic()
            c = req(LLAMA + "/completion", "POST", {"prompt": prompt, "n_predict": 256, "temperature": 0, "top_k": 1, "cache_prompt": False,
                                                     "stop": ["<|im_end|>"], "dry_multiplier": 0.8, "lora": [{"id": 0, "scale": scale}]})[1]
            runs[f"scale{scale}|{name}"] = {"text": c["content"], "secs": round(time.monotonic() - s, 1), "adjacent_repeats": adjacent(c["content"]),
                                            "tokens": c.get("tokens_predicted"), "stop_type": c.get("stop_type")}
            print(f"scale{scale}|{name}", runs[f"scale{scale}|{name}"]["adjacent_repeats"], c["content"][:120].replace("\n", " "), flush=True)
    out["runs"] = runs
    out["totals"] = {s: sum(len(v["adjacent_repeats"]) for k, v in runs.items() if k.startswith(s)) for s in ("scale1.0", "scale0.0")}
    print(out["totals"])
finally:
    out["unload"] = req(API + "/jarvis/model/unload", "POST", {}, tok)[0]
    out["delete_user"] = req(API + "/users/me", "DELETE", {"password": pw, "confirm": True}, tok)[0]
    json.dump(out, open(OUT, "w", encoding="utf-8"), indent=2)
