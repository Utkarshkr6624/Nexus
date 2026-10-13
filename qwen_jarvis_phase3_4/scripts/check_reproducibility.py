"""C12: run every deterministic preparation/build step twice and compare outputs.  usage: .venv\\Scripts\\python.exe scripts\\check_reproducibility.py
Writes reports/reproducibility.json.  Steps: dataset rebuild (preflight -> convert -> freeze), tool-protocol conversion, eval suite build, data statistics,
Kaggle package build, tiny-model smoke training (identical final validation loss), checkpoint-resume equivalence is covered by tests/test_train_smoke.py."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

WS = Path(__file__).resolve().parent.parent
PY = sys.executable
ENV = dict(os.environ, PYTHONIOENCODING="utf-8")
out = {}


def sh(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def run(args, cwd=WS):
    r = subprocess.run(args, capture_output=True, text=True, cwd=cwd, env=ENV)
    if r.returncode:
        raise SystemExit(f"FAILED {args}\n{r.stdout[-500:]}\n{r.stderr[-800:]}")
    return r.stdout


def record(name, a, b):
    out[name] = {"identical": a == b, "run1": a if len(str(a)) < 200 else str(a)[:200], "run2": b if len(str(b)) < 200 else str(b)[:200]}
    print(f"{'IDENTICAL' if a == b else 'DIFFERENT':10s} {name}")


# 1. dataset rebuild
def dataset():
    sh_ = lambda: {f: sh(WS / "dataset" / "v2_final" / f) for f in ("train.jsonl", "validation.jsonl", "test.jsonl")}
    run(["bash", "scripts/rebuild_dataset.sh"])
    a = sh_()
    run(["bash", "scripts/rebuild_dataset.sh"])
    return a, sh_()


a, b = dataset()
record("dataset_rebuild_preflight_convert_freeze", a, b)

# 2. eval suites
def suites():
    d = WS / "pipeline" / "eval" / "data"
    run([PY, "-m", "pipeline.eval.build_suites"]) if (WS / "pipeline" / "eval" / "build_suites.py").exists() else None
    return {p.name: sh(p) for p in sorted(d.glob("*.json*"))}


a = suites()
b = suites()
record("eval_suites_rebuild", a, b)

# 3. data statistics (tokenisation + rendering) on the frozen set
def stats():
    d = Path(tempfile.mkdtemp())
    run([PY, "-m", "pipeline.data.cli", "stats", "--split", "validation", "--data-dir", "dataset/v2_final", "--mode", "strict", "--max-len", "1024", "--out-dir", str(d), "--tag", "x"])
    j = json.load(open(d / "data_stats_validation_x.json"))
    shutil.rmtree(d, ignore_errors=True)
    for k in ("generated_at", "timestamp", "created"):
        j.pop(k, None)
    return hashlib.sha256(json.dumps(j, sort_keys=True).encode()).hexdigest()


record("data_stats_validation", stats(), stats())

# 4. Kaggle package
def pkg():
    run([PY, "kaggle/notebook/build_notebook.py"])
    run([PY, "kaggle/build_package.py"])
    return sh(WS / "kaggle" / "dist" / "qwen_jarvis_kaggle_package.zip")


record("kaggle_package_zip", pkg(), pkg())

# 5. tiny-model smoke training: same seed -> same final validation loss
def smoke():
    r = run([PY, "-m", "pipeline.train.train", "--config", "configs/smoke_test.yaml", "--smoke-test"])
    j = json.loads(r[r.index("{"):r.rindex("}") + 1])
    return j["final_val_loss"], j["final_step"]


record("smoke_training_final_val_loss", smoke(), smoke())

ok = all(v["identical"] for v in out.values())
out["_all_identical"] = ok
json.dump(out, open(WS / "reports" / "reproducibility.json", "w"), indent=1)
print("ALL IDENTICAL" if ok else "DIFFERENCES FOUND")
sys.exit(0 if ok else 1)
