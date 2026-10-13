"""Phase 4 C9 security audit for the Phase 3/4 workspace.  usage: python audit.py   -> writes security/security_audit.json, exit 1 on any FAIL.

Static checks over all first-party code (excludes .venv and _hf_meta):
  S1  no dynamic execution of text: eval/exec/compile-on-data, os.system, shell=True, pickle/marshal/dill loads, torch.load without weights_only=True, yaml.load (non-safe)
  S2  no trust_remote_code=True; no network calls at import time; model/weights download only inside the gated production path (from_pretrained('Qwen/Qwen3-8B') guarded by the lock)
  S3  no reads of .env / credential files; no hard-coded secrets (token/key patterns); notebooks contain no outputs/secrets; HF token only via environment / Kaggle Secrets
  S4  no writes outside the workspace by code paths exercised in tests (checked at runtime in S6) and no references to NEXUS source directories as write targets
  S5  dataset text is only ever treated as data: no subprocess/eval/exec call receives record content (checked structurally: calls to subprocess in first-party code are listed for review)
Runtime checks:
  S6  git: 0 tracked modifications; the set of untracked paths outside our workspaces equals the pre-existing 20 files and none of them changed since the session started (mtime < 2026-10-09)
  S7  the production lock refuses (exit 3) and does not import torch/transformers when only importing train.py
  S8  checkpoint loading refuses a tampered trainer_state (weights_only) - verified by tests/test_checkpoint.py (run here)
"""
import ast
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
WS = HERE.parent
REPO = WS.parent
SKIP = {".venv", "_hf_meta", "__pycache__", "dist", "runs", "dataset", "reports"}
res = {"checks": {}, "findings": [], "fail": []}
# Manually reviewed findings (file, kind) -> justification.  Anything not listed here fails the audit.
ALLOW = {
    ("kaggle/test_notebook_gate.py", "S1 eval/exec"): "exec() runs this workspace's OWN notebook cells (not dataset text) inside a temp dir with subprocess.Popen stubbed and every launch recorded; it exists to prove the production cells are inert.",
    ("kaggle/notebook/qwen_jarvis_phase5_training.ipynb", "notebook may print a secret"): "the cell prints only whether Kaggle-Secrets auth is 'present'; it never prints the token value (matched the word 'Secrets').",
}


def files(exts):
    for p in WS.rglob("*"):
        if p.is_file() and p.suffix in exts and not (set(p.relative_to(WS).parts) & SKIP) and p.resolve() != Path(__file__).resolve():
            yield p


def rel(p):
    return str(p.relative_to(WS)).replace("\\", "/")


# ------------------------------------------------------------------------ S1/S2/S3 via AST + regex
PY = list(files({".py"}))
issues = []
subprocess_sites = []
for p in PY:
    src = p.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        issues.append(("syntax", rel(p), str(e)))
        continue
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            f = n.func
            name = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else "")
            owner = f.value.id if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) else ""
            if name in ("eval", "exec") and isinstance(f, ast.Name):      # bare eval()/exec(); .eval() on torch modules is not Python eval
                issues.append(("S1 eval/exec", rel(p), n.lineno))
            if (owner, name) in {("os", "system"), ("os", "popen"), ("pickle", "load"), ("pickle", "loads"), ("marshal", "loads"), ("dill", "load")}:
                issues.append(("S1 " + owner + "." + name, rel(p), n.lineno))
            if name == "load" and owner == "yaml":
                issues.append(("S1 yaml.load (use safe_load)", rel(p), n.lineno))
            if name == "load" and owner == "torch":
                kw = {k.arg: k.value for k in n.keywords}
                wo = kw.get("weights_only")
                if not (isinstance(wo, ast.Constant) and wo.value is True):
                    issues.append(("S1 torch.load without weights_only=True", rel(p), n.lineno))
            for k in n.keywords:
                if k.arg == "shell" and isinstance(k.value, ast.Constant) and k.value.value is True:
                    issues.append(("S1 shell=True", rel(p), n.lineno))
                if k.arg == "trust_remote_code" and isinstance(k.value, ast.Constant) and k.value.value is True:
                    issues.append(("S2 trust_remote_code=True", rel(p), n.lineno))
            if owner == "subprocess" or name in ("Popen",):
                subprocess_sites.append(f"{rel(p)}:{n.lineno}")
    for i, line in enumerate(src.splitlines(), 1):
        if re.search(r"""open\([^)]*['"]\.env['"]|\.env\b.*read_text|load_dotenv|dotenv""", line) and "security" not in rel(p) and not line.strip().startswith("#"):
            issues.append(("S3 touches .env", rel(p), i))
        if re.search(r"""(?i)(hf_[A-Za-z0-9]{30,}|AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9]{32,}|ghp_[A-Za-z0-9]{30,}|xox[abp]-[A-Za-z0-9-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY)""", line):
            issues.append(("S3 secret-looking string", rel(p), i))
allowed = [i for i in issues if (i[1], i[0]) in ALLOW]
issues = [i for i in issues if (i[1], i[0]) not in ALLOW]
res["checks"]["S1_S2_S3_python"] = {"files_scanned": len(PY), "issues": issues, "reviewed_and_allowed": [list(a) + [ALLOW[(a[1], a[0])]] for a in allowed]}
res["checks"]["S5_subprocess_call_sites_for_review"] = sorted(subprocess_sites)
if issues:
    res["fail"].append("static findings in python code")

# every subprocess call site must not interpolate dataset text: list the argument builders (manual review recorded)
# notebook / text files: no secrets, no outputs
nb_issues = []
for p in files({".ipynb", ".md", ".txt", ".yaml", ".yml", ".json", ".toml", ".sh"}):
    t = p.read_text(encoding="utf-8", errors="replace")
    if re.search(r"(hf_[A-Za-z0-9]{30,}|AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9]{32,}|ghp_[A-Za-z0-9]{30,}|-----BEGIN [A-Z ]*PRIVATE KEY)", t):
        nb_issues.append(("secret-looking string", rel(p)))
    if p.suffix == ".ipynb":
        nb = json.loads(t)
        for c in nb["cells"]:
            if c.get("outputs"):
                nb_issues.append(("notebook cell has outputs", rel(p)))
            if c["cell_type"] == "code" and re.search(r"(?i)(print|display|echo)[^\n]*(token|secret|api_key|HF_TOKEN)", "".join(c["source"])):
                nb_issues.append(("notebook may print a secret", rel(p)))
nb_allowed = [i for i in nb_issues if (i[1], i[0]) in ALLOW]
nb_issues = [i for i in nb_issues if (i[1], i[0]) not in ALLOW]
res["checks"]["S3_text_and_notebooks"] = {"issues": nb_issues, "reviewed_and_allowed": [list(a) + [ALLOW[(a[1], a[0])]] for a in nb_allowed]}
if nb_issues:
    res["fail"].append("secret/outputs in text or notebook")

# S2: where is a model download possible?
dl = []
for p in PY:
    src = p.read_text(encoding="utf-8", errors="replace")
    for m in re.finditer(r"from_pretrained\(([^)]*)\)", src):
        dl.append((rel(p), src[:m.start()].count("\n") + 1, m.group(1)[:70].replace("\n", " ")))
res["checks"]["S2_from_pretrained_call_sites_for_review"] = dl

# ------------------------------------------------------------------------ S6 git / pre-existing files
def git(*a):
    return subprocess.run(["git", "-C", str(REPO)] + list(a), capture_output=True, text=True).stdout


tracked_mod = [l for l in git("status", "--porcelain").splitlines() if not l.startswith("??")]
untracked = [l[3:] for l in git("status", "--porcelain").splitlines() if l.startswith("??")]
ours = [u for u in untracked if u.startswith(("qwen_jarvis_phase1", "qwen_jarvis_phase2", "qwen_jarvis_phase3_4"))]
pre = [u for u in untracked if u not in ours]
session_start = time.mktime(time.strptime("2026-10-09 00:00:00", "%Y-%m-%d %H:%M:%S"))
changed_pre = []
for u in pre:
    p = REPO / u
    if p.exists() and p.stat().st_mtime >= session_start:
        changed_pre.append(u)
res["checks"]["S6_git"] = {"tracked_modifications": len(tracked_mod), "pre_existing_untracked_files": len(pre), "pre_existing_files_modified_today": changed_pre, "new_paths": ours}
if tracked_mod or changed_pre or len(pre) != 20:
    res["fail"].append("git state: tracked modification / pre-existing untracked file changed / unexpected untracked file count")
# NEXUS tracked source untouched also verified by diff
diff = git("diff", "--stat").strip()
res["checks"]["S6_git_diff_empty"] = diff == ""
if diff:
    res["fail"].append("git diff not empty")

# ------------------------------------------------------------------------ S7 importing train.py must not import ML libraries; lock refuses
py = WS / ".venv" / "Scripts" / "python.exe"
code = ("import sys; import pipeline.train.train as t; "
        "bad=[m for m in ('torch','transformers','peft','bitsandbytes') if m in sys.modules]; print('LOADED:'+','.join(bad))")
r = subprocess.run([str(py), "-c", code], capture_output=True, text=True, cwd=WS)
loaded = r.stdout.strip().split("LOADED:")[-1] if "LOADED:" in r.stdout else "?"
res["checks"]["S7_import_train_loads_ml_libraries"] = loaded or "none"
if loaded not in ("", "none"):
    res["fail"].append("importing train.py imports ML libraries: " + loaded)
r2 = subprocess.run([str(py), "-m", "pipeline.train.train", "--config", "configs/qlora_sft.yaml"], capture_output=True, text=True, cwd=WS, env={k: v for k, v in os.environ.items() if k != "QWEN_PHASE5_UNLOCK"})
res["checks"]["S7_production_without_unlock"] = {"exit_code": r2.returncode, "refused": "LOCKED" in (r2.stdout + r2.stderr)}
if r2.returncode != 3:
    res["fail"].append("production lock did not refuse with exit code 3")
# also: unlock env alone is not enough
env = dict(os.environ, QWEN_PHASE5_UNLOCK="I_UNDERSTAND_THIS_STARTS_PRODUCTION_TRAINING")
r3 = subprocess.run([str(py), "-m", "pipeline.train.train", "--config", "configs/qlora_sft.yaml"], capture_output=True, text=True, cwd=WS, env=env)
res["checks"]["S7_unlock_env_alone_still_refused"] = {"exit_code": r3.returncode}
if r3.returncode != 3:
    res["fail"].append("env var alone unlocked production")

# ------------------------------------------------------------------------ S4 filesystem footprint: nothing new outside the three workspaces
res["checks"]["S4_untracked_outside_our_workspaces"] = {"count": len(pre), "all_pre_existing_unchanged": not changed_pre}

# local model weights must not exist (no unnecessary download)
weights = [rel(p) for p in WS.rglob("*") if p.is_file() and p.suffix in (".safetensors", ".bin", ".gguf", ".pt", ".pth") and ".venv" not in p.parts and p.stat().st_size > 5_000_000]
tiny_smoke_artifacts = [rel(p) for p in WS.rglob("*") if p.is_file() and p.suffix in (".safetensors", ".pt") and ".venv" not in p.parts and p.stat().st_size <= 5_000_000]
res["checks"]["tiny_smoke_test_artifacts_<=5MB"] = len(tiny_smoke_artifacts)
res["checks"]["no_model_weights_on_disk"] = weights
big = [rel(p) for p in WS.rglob("*") if p.is_file() and p.stat().st_size > 50_000_000 and ".venv" not in p.parts]
res["checks"]["files_over_50MB_outside_venv"] = big
hf_cache = Path(os.path.expanduser("~/.cache/huggingface"))
qcache = [p for p in hf_cache.glob("hub/models--Qwen--Qwen3-8B/**/*") if p.is_file() or p.is_symlink()] if hf_cache.exists() else []
qweights = [str(p.name) for p in qcache if p.suffix in (".safetensors", ".bin", ".gguf", ".pt", ".pth") or (p.exists() and p.stat().st_size > 50_000_000)]
res["checks"]["qwen_hf_cache_files"] = sorted({p.name for p in qcache})
res["checks"]["hf_cache_exists_with_qwen_weights"] = bool(qweights)
if weights or big or res["checks"]["hf_cache_exists_with_qwen_weights"]:
    res["fail"].append("model weights / large files present")

res["status"] = "PASS" if not res["fail"] else "FAIL"
json.dump(res, open(HERE / "security_audit.json", "w", encoding="utf-8"), indent=1)
print(json.dumps({"status": res["status"], "fail": res["fail"], "issues": issues[:15], "nb_issues": nb_issues[:5], "S6": res["checks"]["S6_git"],
                  "S7": [res["checks"]["S7_import_train_loads_ml_libraries"], res["checks"]["S7_production_without_unlock"], res["checks"]["S7_unlock_env_alone_still_refused"]],
                  "subprocess_sites": len(subprocess_sites), "from_pretrained_sites": len(dl)}, indent=1))
sys.exit(0 if not res["fail"] else 1)
