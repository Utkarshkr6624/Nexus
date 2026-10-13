"""Start NEXUS with NEXO chat (internally "Jarvis") for local development: model server (if installed), backend, frontend.

    backend\\.venv\\Scripts\\python.exe scripts\\launch_nexus.py        (or just: start.cmd)

What it does, in order:
  1. Checks prerequisites: backend venv, frontend node_modules, .env, PostgreSQL reachable.
  2. Jarvis model server (Jarvis/jarvis_phase7/serve_jarvis.py, llama.cpp runtime): NOT started here. Its
     command is handed to the backend (JARVIS_MODEL_LAUNCH_ARGV), which starts it when the user presses
     "Turn NEXO on" on the NEXO page and stops it on "Turn NEXO off" or after JARVIS_IDLE_UNLOAD_MINUTES idle.
     Missing runtime files are reported; NEXUS starts anyway.
  3. Backend (backend/run.py) with JARVIS_ENABLED=true, JARVIS_MODEL_URL and (unless already set)
     JARVIS_WORKSPACE_DIR=%USERPROFILE%\\Downloads\\NEXO set for this process only; .env is never edited. Pass --no-jarvis to start NEXUS with Jarvis off.
  4. Frontend (npm run dev).
  5. Confirms /health, /api/v1/health, the frontend and /jarvis/status-equivalent model health, then waits.
Anything already running on a port is reused, never started twice. Ctrl+C stops only what this script started.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
PHASE7 = ROOT / "Jarvis" / "jarvis_phase7"
RUNTIME = PHASE7 / "runtime"
PY = BACKEND / ".venv" / "Scripts" / "python.exe"
ADAPTER = (
    ROOT / "Jarvis" / "jarvis_v31_remediation_train" / "runs" / "qwen3_8b_jarvis_v3_1_remediation" / "checkpoint-139" / "adapter"
)
ADAPTER_SHA = "d754a3c6b77fb80bf24e67527d5b32f04606d8496e9e7c2b9d7cccef48f48fa8"
GGUF = RUNTIME / "models" / "Qwen3-8B-Q4_K_M.gguf"
GGUF_SHA = "d98cdcbd03e17ce47681435b5150e34c1417f50b5c0019dd560e4882c5745785"  # Qwen/Qwen3-8B-GGUF LFS sha256
LORA = RUNTIME / "models" / "jarvis_v31_139_lora.gguf"
LLAMA = RUNTIME / "llama.cpp" / "llama-server.exe"
BACKEND_URL, FRONTEND_URL, MODEL_PORT = "http://127.0.0.1:8000", "http://localhost:5173", 8765
LOGS = PHASE7 / "logs"

started: list[subprocess.Popen] = []


def say(msg: str) -> None:
    print(f"[launch] {msg}", flush=True)


def get(url: str, timeout: float = 3.0) -> tuple[int, dict | str] | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
            try:
                return r.status, json.loads(body)
            except ValueError:
                return r.status, body
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except (urllib.error.URLError, OSError):
        return None


def wait_for(url: str, seconds: float, proc: subprocess.Popen | None = None) -> tuple[int, dict | str] | None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        r = get(url)
        if r and r[0] == 200:
            return r
        if proc is not None and proc.poll() is not None:
            return None
        time.sleep(2)
    return None


def port_open(host: str, port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex((host, port)) == 0


def start(name: str, cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> subprocess.Popen:
    LOGS.mkdir(parents=True, exist_ok=True)
    log = (LOGS / f"{name}.log").open("w", encoding="utf-8")
    say(f"starting {name} (log: {LOGS / (name + '.log')})")
    p = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT,
                         creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    started.append(p)
    return p


def stop_all() -> None:
    for p in reversed(started):
        if p.poll() is None:
            if os.name == "nt":  # npm/vite and llama-server spawn children; stop the whole tree
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
            else:
                p.terminate()
    started.clear()


def fail(msg: str) -> int:
    say(f"ERROR: {msg}")
    stop_all()
    return 1


MODEL_ARGV = [str(PY), str(PHASE7 / "serve_jarvis.py"), "--adapter", str(ADAPTER),
              "--expect-sha256", ADAPTER_SHA, "--port", str(MODEL_PORT),
              "--llama-server", str(LLAMA), "--gguf", str(GGUF), "--gguf-sha256", GGUF_SHA, "--lora-gguf", str(LORA)]


def model_server(skip: bool) -> str:
    """Return the model state line. The model is never loaded here: the backend loads it on demand."""
    if skip:
        return "NEXO chat disabled (--no-jarvis)"
    url = f"http://127.0.0.1:{MODEL_PORT}/health"
    r = get(url)
    if r and r[0] == 200:
        return f"model server already running (not started by NEXUS, so NEXUS will not stop it): {r[1]}"
    missing = [str(p.relative_to(ROOT)) for p in (LLAMA, GGUF, LORA, RUNTIME / "LORA_GGUF.json", ADAPTER) if not p.exists()]
    if missing:
        return ("BLOCKED - NEXO chat model runtime not installed; missing: " + ", ".join(missing)
                + ". Follow Jarvis/jarvis_phase7/runtime/README.md. NEXUS starts; NEXO chat will show 'Model not available'.")
    return "OFF - not loaded. Press 'Turn NEXO on' on the NEXO page to load it; it unloads when turned off or idle."


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-jarvis", action="store_true", help="start NEXUS with NEXO chat turned off")
    ap.add_argument("--no-frontend", action="store_true")
    a = ap.parse_args()

    for need, hint in ((PY, "run `make install` (creates backend/.venv)"),
                       (FRONTEND / "node_modules", "run `npm install` in frontend/"),
                       (ROOT / ".env", "run `python scripts/bootstrap.py` (creates .env)")):
        if not need.exists():
            return fail(f"{need} is missing: {hint}")
    if not port_open("127.0.0.1", int(os.environ.get("POSTGRES_PORT", "5432"))):
        return fail("PostgreSQL is not accepting connections on 127.0.0.1:5432; start the PostgreSQL service first")

    model_state = model_server(a.no_jarvis)
    say(model_state)

    if get(f"{BACKEND_URL}/health"):
        say(f"backend already running at {BACKEND_URL} (reused; its JARVIS_* settings are whatever it was started with)")
    else:
        env = dict(os.environ)
        env["JARVIS_ENABLED"] = "false" if a.no_jarvis else "true"
        env["JARVIS_MODEL_URL"] = "" if a.no_jarvis else f"http://127.0.0.1:{MODEL_PORT}"
        env["JARVIS_MODEL_LAUNCH_ARGV"] = "" if a.no_jarvis else json.dumps(MODEL_ARGV)
        if not env.get("JARVIS_GIT_REPO_DIR"):  # read-only git_inspect of this project
            env["JARVIS_GIT_REPO_DIR"] = str(ROOT)
        if not (env.get("JARVIS_WEB_SEARCH_PROVIDER") or env.get("JARVIS_WEB_SEARCH_URL")):
            env["JARVIS_WEB_SEARCH_PROVIDER"] = "wikipedia"  # free, keyless; set your own SearXNG URL to replace it
        if not env.get("JARVIS_WORKSPACE_DIR"):  # files NEXO saves land in Downloads\NEXO; its tools reach only that folder
            workspace = Path.home() / "Downloads" / "NEXO"
            workspace.mkdir(parents=True, exist_ok=True)
            env["JARVIS_WORKSPACE_DIR"] = str(workspace)
        proc = start("backend", [str(PY), "run.py"], BACKEND, env)
        if not wait_for(f"{BACKEND_URL}/health", 180, proc):
            return fail("backend did not answer /health; see Jarvis/jarvis_phase7/logs/backend.log")
    ready = get(f"{BACKEND_URL}/api/v1/health", timeout=10)
    if not ready or ready[0] != 200:
        return fail(f"backend readiness check failed ({ready}); is the database migrated? (`make migrate`)")
    say(f"backend healthy: {BACKEND_URL}  (API docs: {BACKEND_URL}/docs)")

    if not a.no_frontend:
        if get(FRONTEND_URL):
            say(f"frontend already running at {FRONTEND_URL} (reused)")
        else:
            npm = "npm.cmd" if os.name == "nt" else "npm"
            proc = start("frontend", [npm, "run", "dev"], FRONTEND)
            if not wait_for(FRONTEND_URL, 120, proc):
                return fail("frontend did not start; see Jarvis/jarvis_phase7/logs/frontend.log")
        say(f"frontend reachable: {FRONTEND_URL}")

    say("=" * 60)
    say(f"Open {FRONTEND_URL}/nexo  (sign in or register first)")
    say(f"NEXO chat model: {model_state}")
    say("NEXO chat (model: Qwen3-8B + checkpoint_v31_139) is a development candidate, NOT acceptance-qualified (Phase 6 verdict FAIL).")
    say("Press Ctrl+C to stop the processes this launcher started.")
    try:
        while all(p.poll() is None for p in started):
            time.sleep(2)
        dead = [p.args for p in started if p.poll() is not None]
        say(f"a process exited unexpectedly: {dead}; stopping the rest")
        return 1
    except KeyboardInterrupt:
        say("stopping...")
        return 0
    finally:
        stop_all()


if __name__ == "__main__":
    sys.exit(main())
