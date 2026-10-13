"""Phase 6 Kaggle launcher (runs inside a PRIVATE GPU kernel; never trains, never writes to /kaggle/input).

Steps, each aborting the run on failure BEFORE any generation:
  1. locate inputs under /kaggle/input (mount layout differs between Kaggle versions, so paths are discovered, not hard-coded);
  2. require a CUDA GPU with >= 14 GB (T4 = 15 GB);
  3. pip-install the pinned training versions (requirements_phase6.txt) and assert them;
  4. run scripts/run_phase6_kaggle.py in a FRESH interpreter (so the pinned libraries are the ones imported); it applies the package
     gate (trusted PACKAGE_MANIFEST sha256 + every package file) and the adapter gate (all 7 adapters vs trusted sha256) before loading the model;
  5. score this run's raw results (scripts/score_phase6.py).
Outputs: /kaggle/working/phase6_run<G>/ (raw/<config>.json, run_status.json, env.json, launcher.log, scoring files).
No tokens are needed (Qwen/Qwen3-8B is public); no environment variables are printed."""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

GROUPS = {   # base_model runs in BOTH sessions: every paired comparison has a same-session base, and the two base runs check cross-session determinism
    1: ["base_model", "checkpoint_800", "checkpoint_1100", "checkpoint_1200"],
    2: ["base_model", "checkpoint_1300", "checkpoint_1400", "checkpoint_1500", "checkpoint_1596"],
}
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from trusted_hashes import V3_PREFIX, V3_STEP  # noqa: E402
if V3_STEP is not None:   # group 3: the V3 remediation checkpoint only; base_model is compared against the bit-identical historical base runs
    GROUPS[3] = [f"checkpoint_{V3_PREFIX}{V3_STEP}"]
PINS = {"transformers": "5.16.1", "peft": "0.20.0", "accelerate": "1.14.0", "bitsandbytes": "0.50.2"}
INPUT = Path("/kaggle/input")


def log(out, msg):
    print(msg, flush=True)
    with open(out / "launcher.log", "a", encoding="utf-8") as fh:
        fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}\n")


def one(paths, what):
    paths = sorted(set(paths))
    if len(paths) != 1:
        raise SystemExit(f"ABORT: expected exactly one {what}, found {[str(p) for p in paths]}")
    return paths[0]


def main(group):
    if group not in GROUPS:
        raise SystemExit(f"ABORT: group must be one of {list(GROUPS)}")
    out = Path(f"/kaggle/working/phase6_run{group}")
    out.mkdir(parents=True, exist_ok=True)
    eval_root = one([p.parents[1] for p in INPUT.rglob("run_phase6_kaggle.py") if p.parent.name == "scripts" and (p.parents[1] / "prompts").is_dir()],
                    "Phase 6 eval bundle (scripts/ + prompts/)")
    pkg_root = one([p.parent for p in INPUT.rglob("PACKAGE_MANIFEST.json") if (p.parent / "pipeline" / "eval" / "runner.py").is_file()], "training package")
    log(out, f"group {group}: configs {GROUPS[group]}")
    log(out, f"eval bundle: {eval_root}\npackage: {pkg_root}\ncheckpoint search root: {INPUT}")

    for prev in INPUT.rglob(f"phase6_run{group}/raw/*.json"):        # resume: a previous output of this group attached as a kernel source
        if not (out / "raw" / prev.name).exists():
            (out / "raw").mkdir(exist_ok=True)
            shutil.copy2(prev, out / "raw" / prev.name)
            log(out, f"resume: copied {prev}")

    import torch
    if not torch.cuda.is_available():
        raise SystemExit("ABORT: no CUDA GPU (enable a T4 accelerator)")
    gpus = [{"name": torch.cuda.get_device_name(i), "gb": round(torch.cuda.get_device_properties(i).total_memory / 2 ** 30, 1)} for i in range(torch.cuda.device_count())]
    log(out, f"gpus: {gpus}; torch {torch.__version__} cuda {torch.version.cuda}")
    if not all("T4" in g["name"] and g["gb"] >= 14 for g in gpus):     # the agreed evaluation hardware; a P100 also has 16 GB, so check the name
        raise SystemExit(f"ABORT: Phase 6 runs on Tesla T4 only; got {gpus}")

    r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", str(eval_root / "kaggle" / "requirements_phase6.txt")], capture_output=True, text=True)
    log(out, f"pip install rc={r.returncode} {r.stderr.strip()[-400:]}")
    if r.returncode:
        raise SystemExit("ABORT: pinned install failed")
    from importlib import metadata
    got = {k: metadata.version(k) for k in PINS}
    log(out, f"library versions: {got}")
    if got != PINS:
        raise SystemExit(f"ABORT: installed versions {got} != pinned {PINS}")
    (out / "env.json").write_text(json.dumps({"group": group, "configs": GROUPS[group], "gpus": gpus, "torch": torch.__version__, "cuda": torch.version.cuda,
                                              "libraries": got, "python": sys.version.split()[0], "eval_root": str(eval_root), "package_root": str(pkg_root)},
                                             indent=1), encoding="utf-8")

    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", HF_HUB_DISABLE_TELEMETRY="1", TOKENIZERS_PARALLELISM="false")
    cmd = [sys.executable, "-B", str(eval_root / "scripts" / "run_phase6_kaggle.py"), "--code", str(pkg_root), "--ckpt-root", str(INPUT),
           "--out", str(out), "--configs", ",".join(GROUPS[group])]
    log(out, "run: " + " ".join(cmd))
    with open(out / "runner.log", "w", encoding="utf-8") as fh:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
        for line in p.stdout:
            print(line, end="", flush=True)
            fh.write(line)
        rc = p.wait()
    log(out, f"runner rc={rc}")
    if rc:
        raise SystemExit(f"runner failed rc={rc} (see runner.log); completed configs are kept in raw/")
    r = subprocess.run([sys.executable, "-B", str(eval_root / "scripts" / "score_phase6.py"), str(out)], capture_output=True, text=True, env=env)
    log(out, f"score rc={r.returncode}\n{r.stdout[-3000:]}{r.stderr[-1000:]}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("PHASE6_GROUP", "0")))
