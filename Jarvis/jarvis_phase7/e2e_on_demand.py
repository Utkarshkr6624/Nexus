"""Phase 7 live check: on-demand NEXO load, real chats (text, tools, approval), unload, memory release.

Runs against the backend started by start.cmd with a dedicated test account. Writes
evidence/e2e_on_demand.json. Talks only to 127.0.0.1; uses the real model and database.
"""

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "http://127.0.0.1:8000/api/v1"
HERE = Path(__file__).parent
OUT = HERE / "evidence" / "e2e_on_demand.json"
log: dict = {"steps": []}


def call(method, path, body=None, token=None, timeout=700):
    req = urllib.request.Request(API + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def gpu():
    try:
        return int(subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                                  capture_output=True, text=True, timeout=10).stdout.split()[0])
    except Exception:
        return None


def step(name, **data):
    data["name"] = name
    log["steps"].append(data)
    print(json.dumps(data)[:400], flush=True)


def model_procs():
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq llama-server.exe", "/FO", "CSV", "/NH"], capture_output=True, text=True).stdout
    return [ln for ln in out.splitlines() if "llama-server" in ln]


email, pw = "nexo.e2e@example.com", "Nexo-e2e-Passw0rd!"
s, b = call("POST", "/auth/login", {"email": email, "password": pw})
if s != 200:
    s, b = call("POST", "/auth/register", {"email": email, "password": pw, "username": "nexoe2e"})
    s, b = call("POST", "/auth/login", {"email": email, "password": pw})
token = b.get("access_token") or b.get("tokens", {}).get("access_token")
assert token, (s, b)

s, st = call("GET", "/jarvis/status", token=token)
step("status_before", http=s, lifecycle=st.get("lifecycle"), model=st.get("model"), gpu_mb=gpu(), llama=len(model_procs()),
     tools_available=sum(t["available"] for t in st["tools"]), tools_total=len(st["tools"]))
s, c = call("POST", "/jarvis/chat", {"messages": [{"role": "user", "content": "hello"}]}, token)
step("chat_while_off", http=s, body=c)

t0 = time.time()
s, ld = call("POST", "/jarvis/model/load", {}, token)
step("load_called", http=s, body=ld)
s, ld2 = call("POST", "/jarvis/model/load", {}, token)
step("load_called_again", http=s, state=ld2.get("state"))
while time.time() - t0 < 900:
    s, st = call("GET", "/jarvis/status", token=token)
    if st["lifecycle"]["state"] in ("ready", "error"):
        break
    time.sleep(3)
step("loaded", seconds=round(time.time() - t0), lifecycle=st["lifecycle"], model=st["model"], gpu_mb=gpu(), llama=len(model_procs()))


def chat(label, msgs):
    t = time.time()
    s, r = call("POST", "/jarvis/chat", {"messages": msgs, "timezone": "Asia/Kolkata"}, token)
    step(label, http=s, secs=round(time.time() - t, 1), outcome=r.get("outcome"), text=r.get("text"),
         trace=[{"tool": x["tool"], "status": x["status"], "ok": (x.get("result") or {}).get("ok")} for x in r.get("trace", [])],
         proposal=r.get("proposal"), proposal_id=r.get("proposal_id"), guards=r.get("guards"))
    return r


chat("text", [{"role": "user", "content": "Hi NEXO, who are you? Answer in one sentence."}])
chat("calculate", [{"role": "user", "content": "Use the calculate tool: what is 12 km in miles?"}])
chat("search", [{"role": "user", "content": "Find good movies for me."}])
chat("list_files", [{"role": "user", "content": "List the files in my workspace using workspace_list_files."}])
r = chat("create_task", [{"role": "user", "content": "Create a task titled 'NEXO e2e check'."}])
if r.get("proposal_id"):
    s, d = call("POST", "/jarvis/confirm", {"proposal_id": r["proposal_id"], "approve": False}, token)
    step("decline", http=s, outcome=d.get("outcome"), text=d.get("text"))
    s, d = call("POST", "/jarvis/confirm", {"proposal_id": r["proposal_id"], "approve": True}, token)
    step("approve_after_decline", http=s)

s, st = call("GET", "/jarvis/status", token=token)
before = gpu()
s, un = call("POST", "/jarvis/model/unload", {}, token)
step("unloaded", http=s, body=un, gpu_mb_before=before, gpu_mb_after=gpu(), llama=len(model_procs()))
s, c = call("POST", "/jarvis/chat", {"messages": [{"role": "user", "content": "hello"}]}, token)
step("chat_after_unload", http=s, body=c)
s, h = call("GET", "/health")
step("nexus_health_after_unload", http=s)
OUT.write_text(json.dumps(log, indent=1), encoding="utf-8")
print("wrote", OUT)
