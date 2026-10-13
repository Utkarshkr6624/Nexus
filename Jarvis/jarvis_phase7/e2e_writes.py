"""Phase 7 live check 2: DeBERTa with NEXO off, then real-model approval flows (task, event, files).

Writes evidence/e2e_writes.json. Uses the account from e2e_on_demand.py, the real model and database.
Side effects only touch that test account and the NEXO sandbox workspace.
"""

import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "http://127.0.0.1:8000/api/v1"


def call(method, path, body=None, token=None, timeout=700):
    req = urllib.request.Request(API + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def gpu():
    out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                         capture_output=True, text=True, timeout=10).stdout
    return int(out.split()[0]) if out.strip() else None


def model_procs():
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq llama-server.exe", "/FO", "CSV", "/NH"], capture_output=True, text=True).stdout
    return [ln for ln in out.splitlines() if "llama-server" in ln]


HERE = Path(__file__).parent
OUT = HERE / "evidence" / "e2e_writes.json"
WS = HERE / "workspace"
log: dict = {"steps": []}


def step(name, **data):
    data["name"] = name
    log["steps"].append(data)
    print(json.dumps(data)[:300], flush=True)


s, b = call("POST", "/auth/login", {"email": "nexo.e2e@example.com", "password": "Nexo-e2e-Passw0rd!"})
token = b["access_token"]

s, r = call("POST", "/ml/route", {"text": "add a task to buy milk tomorrow"}, token)
step("deberta_with_nexo_off", http=s, body={k: r.get(k) for k in ("intent", "confidence", "service", "status") if k in r} or r)

t0 = time.time()
call("POST", "/jarvis/model/load", {}, token)
while time.time() - t0 < 900:
    s, st = call("GET", "/jarvis/status", token=token)
    if st["lifecycle"]["state"] in ("ready", "error"):
        break
    time.sleep(3)
step("loaded", state=st["lifecycle"]["state"], seconds=round(time.time() - t0))


def chat(label, text):
    s, r = call("POST", "/jarvis/chat", {"messages": [{"role": "user", "content": text}], "timezone": "Asia/Kolkata"}, token)
    step(label, http=s, outcome=r.get("outcome"), text=(r.get("text") or "")[:200], proposal=r.get("proposal"),
         trace=[(x["tool"], x["status"]) for x in r.get("trace", [])])
    return r


def decide(label, r, approve):
    s, d = call("POST", "/jarvis/confirm", {"proposal_id": r["proposal_id"], "approve": approve}, token)
    step(label, http=s, outcome=d.get("outcome"),
         trace=[(x["tool"], x["status"], (x.get("result") or {}).get("ok")) for x in d.get("trace", [])],
         text=(d.get("text") or "")[:200])
    return s


def tasks():
    s, b = call("GET", "/tasks?limit=100", token=token)
    items = b.get("items", b if isinstance(b, list) else [])
    return [t.get("title") for t in items]


# app_create_task needs exactly one project (its contract has no project argument).
s, b = call("GET", "/projects", token=token)
if not (b.get("items", b if isinstance(b, list) else [])):
    s, b = call("POST", "/projects", {"name": "NEXO e2e project"}, token)
    step("project_created", http=s)

cases = [
    ("task", 'app_create_task {"title": "NEXO e2e check"}', True),
    ("task_declined", 'app_create_task {"title": "NEXO declined task"}', False),
    ("task_update", 'app_update_task {"task": "NEXO e2e check", "action": "complete"}', True),
    ("event", 'app_create_event {"title": "NEXO e2e event", "start": "2026-10-20 10:00", "minutes": 30}', True),
    ("file_write", 'fs_write_file {"path": "e2e/hello.txt", "content": "hello from NEXO"}', True),
    ("file_move", 'fs_move_file {"source": "e2e/hello.txt", "destination": "e2e/moved.txt"}', True),
    ("file_delete", 'fs_delete_file {"path": "e2e/moved.txt"}', True),
]
for label, call_text, approve in cases:
    r = chat(label, f"Please do exactly this: {call_text}")
    if not r.get("proposal_id"):
        continue
    decide(f"{label}_{'approve' if approve else 'decline'}", r, approve)
    if label == "task":
        step("repeat_confirm", http=decide("task_repeat", r, True))
    step(f"{label}_state", tasks=[t for t in tasks() if t and "NEXO" in t],
         files=sorted(p.relative_to(WS).as_posix() for p in WS.rglob("*") if p.is_file()))

before = gpu()
s, un = call("POST", "/jarvis/model/unload", {}, token)
step("unloaded", state=un.get("state"), last_unload=un.get("last_unload"), gpu_before=before, gpu_after=gpu(), llama=len(model_procs()))
s, r = call("POST", "/ml/route", {"text": "add a task to buy milk tomorrow"}, token)
step("deberta_after_unload", http=s, intent=r.get("intent"))
OUT.write_text(json.dumps(log, indent=1), encoding="utf-8")
