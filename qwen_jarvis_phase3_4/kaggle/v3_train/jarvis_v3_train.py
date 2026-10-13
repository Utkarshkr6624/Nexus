# JARVIS V3 remediation training (private Kaggle GPU kernel). Bounded warm-start fine-tuning of Qwen3-8B QLoRA from checkpoint-1300.
# Gates before any model download: V3 package manifest == trusted sha256 and every listed file matches; init adapter == trusted sha256.
# A wall-clock guard stops training before the GPU budget is at risk; checkpoints are saved every 100 steps, so a stopped run is recoverable.
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

PACKAGE_MANIFEST_SHA256 = "0db3ce95e9ebecefcfe3946939d59229590fa1d28ee3d9f524d0ba7e37677465"     # kaggle/dist_v3 build, 2026-10-12
INIT_ADAPTER_SHA256 = "d71f9a974837cdbaf4ade94e779ed15bfeee67bb215b77c14db9333926ef570c"         # checkpoint-1300 adapter_model.safetensors
PINNED = ["transformers==5.16.1", "peft==0.20.0", "accelerate==1.14.0", "bitsandbytes==0.50.2"]   # versions that trained checkpoints 800-1596
MAX_STEPS, LR = 700, 5e-5
GUARD_MIN = 160                     # hard stop for the training subprocess (minutes)
W = Path("/kaggle/working")
PKG, RUNS = W / "package", W / "runs"
T0 = time.time()
status = {"started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "max_steps": MAX_STEPS, "learning_rate": LR, "guard_min": GUARD_MIN}


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def save_status():
    status["elapsed_min"] = round((time.time() - T0) / 60, 1)
    (W / "v3_train_status.json").write_text(json.dumps(status, indent=1, default=str))


try:
    # 1. package: zip or extracted files from utkarsh6624/qwen-jarvis-v3-package
    inp = Path("/kaggle/input")
    zips = [p for p in inp.rglob("qwen_jarvis_v3_package.zip")]
    if zips:
        zipfile.ZipFile(zips[0]).extractall(PKG)
    else:
        mans = [p for p in inp.rglob("PACKAGE_MANIFEST.json") if "qwen-jarvis-v3-package" in p.parts]
        if len(mans) != 1:
            raise SystemExit(f"ABORT: expected one V3 package, found {mans}")
        shutil.copytree(mans[0].parent, PKG)
    man = PKG / "PACKAGE_MANIFEST.json"
    if sha(man) != PACKAGE_MANIFEST_SHA256:
        raise SystemExit("ABORT: package manifest hash mismatch")
    bad = [f["path"] for f in json.loads(man.read_text())["files"] if sha(PKG / f["path"]) != f["sha256"]]
    if bad:
        raise SystemExit(f"ABORT: package files differ from manifest: {bad[:5]}")
    status["package_gate"] = "PASS"
    # 2. init adapter
    cands = [p for p in inp.rglob("checkpoint-1300") if (p / "adapter" / "adapter_model.safetensors").exists()]
    good = [p / "adapter" for p in cands if sha(p / "adapter" / "adapter_model.safetensors") == INIT_ADAPTER_SHA256]
    if not good:
        raise SystemExit(f"ABORT: no checkpoint-1300 adapter with the trusted hash among {cands}")
    status["init_adapter"] = {"path": str(good[0]), "sha256": INIT_ADAPTER_SHA256, "gate": "PASS"}
    log("gates PASS; init adapter", good[0])
    # 3. pinned libraries
    rc = subprocess.run([sys.executable, "-m", "pip", "install", "-q", *PINNED]).returncode
    status["pip_rc"] = rc
    import importlib.metadata as md
    status["libraries"] = {n: md.version(n) for n in ("torch", "transformers", "peft", "accelerate", "bitsandbytes")}
    status["gpus"] = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"], capture_output=True, text=True).stdout.strip()
    log("libraries", status["libraries"], status["gpus"])
    save_status()
    # 4. bounded training
    cmd = [sys.executable, "-m", "pipeline.train.train", "--config", "configs/qlora_sft_v3.yaml", "--set", "production_locked=false",
           "--set", f"output.output_dir={RUNS}", "--set", f"train.max_steps={MAX_STEPS}", "--set", f"train.learning_rate={LR}",
           "--confirm-production", "--init-adapter", str(good[0]), "--init-adapter-sha256", INIT_ADAPTER_SHA256]
    status["command"] = " ".join(cmd)
    env = dict(os.environ, QWEN_PHASE5_UNLOCK="I_UNDERSTAND_THIS_STARTS_PRODUCTION_TRAINING", PYTHONUNBUFFERED="1", HF_HUB_DISABLE_TELEMETRY="1")
    t1 = time.time()
    with open(W / "train_v3.log", "w") as lf:
        proc = subprocess.Popen(cmd, cwd=PKG, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for line in proc.stdout:
            lf.write(line)
            lf.flush()
            if '"step"' in line or "warm start" in line or "Error" in line or "Traceback" in line:
                print(line.rstrip(), flush=True)
            if time.time() - t1 > GUARD_MIN * 60 and proc.poll() is None:
                status["guard_triggered"] = True
                log("GUARD: stopping training at", round((time.time() - t1) / 60, 1), "min")
                proc.terminate()
                break
        try:
            proc.wait(timeout=120)
        except subprocess.TimeoutExpired:
            proc.kill()
    status["train_rc"] = proc.returncode
    status["train_minutes"] = round((time.time() - t1) / 60, 1)
except BaseException as e:  # noqa: BLE001  (always write the status so the session's evidence is kept)
    status["error"] = f"{type(e).__name__}: {e}"
    log("ERROR", status["error"])
finally:
    run = RUNS / "qwen3_8b_jarvis_v3_remediation"
    if run.exists():
        status["checkpoints"] = {}
        for ck in sorted(run.glob("checkpoint-*")):
            meta = json.loads((ck / "checkpoint_meta.json").read_text()) if (ck / "checkpoint_meta.json").exists() else {}
            status["checkpoints"][ck.name] = {"step": meta.get("step"), "metrics": meta.get("metrics"),
                                             "adapter_sha256": sha(ck / "adapter" / "adapter_model.safetensors") if (ck / "adapter" / "adapter_model.safetensors").exists() else None}
        for ptr in ("best.json", "latest.json"):
            if (run / ptr).exists():
                status[ptr] = json.loads((run / ptr).read_text())
        if (run / "metrics.jsonl").exists():
            status["metrics_tail"] = (run / "metrics.jsonl").read_text().splitlines()[-6:]
    shutil.rmtree(PKG, ignore_errors=True)          # the package is an input; keep only run outputs
    save_status()
    log("done", json.dumps({k: status.get(k) for k in ("train_rc", "train_minutes", "guard_triggered", "error", "best.json")}, default=str))
