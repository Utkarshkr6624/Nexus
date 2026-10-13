"""Builds kaggle/notebook/qwen_jarvis_phase5_training.ipynb reproducibly (fixed cell ids, no outputs, no execution counts).
usage: python kaggle/notebook/build_notebook.py        (needs `nbformat`)

SAFETY MODEL
* Phase 3/4 never runs this notebook.  Kaggle execution is PENDING PHASE 5.
* Cells tagged `production` can load Qwen3-8B / train / evaluate.  They are inert unless ALL of these hold:
    1. PHASE5_AUTHORIZED = True      (a human edits the gate cell in Phase 5)
    2. CONFIRM_PRODUCTION = True     (second, independent switch)
    3. environment variable QWEN_PHASE5_UNLOCK == the exact unlock string (same lock as configs/qlora_sft.yaml / train.py)
    4. running on Kaggle with a CUDA GPU
* No cell outside the production cells downloads weights.  Secrets are read only inside the gated branch, only from Kaggle Secrets, and never printed.
"""
import sys
from pathlib import Path

import nbformat as nbf
from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook

OUT = Path(__file__).resolve().parent / "qwen_jarvis_phase5_training.ipynb"

# ---- single place that names the pipeline entry points / flags the notebook uses (validate_notebook.py cross-checks them against the sources) ----
TRAIN_MODULE = "pipeline.train.train"
EVAL_MODULE = "pipeline.eval.cli"
CLI = {
    "train_flags": ["--config", "--set", "--validate-config", "--dry-run", "--confirm-production", "--resume"],
    "eval_flags": [],
}

cells = []


def md(cid, text):
    cells.append(new_markdown_cell(text, id=cid))


def code(cid, text, tags=None):
    meta = {"tags": tags} if tags else {}
    cells.append(new_code_cell(text.strip("\n"), id=cid, metadata=meta))


md("c00-banner", """# Qwen3-8B JARVIS - QLoRA SFT on Kaggle (Phase 5 notebook)

**PHASE 3/4 — DO NOT RUN PRODUCTION CELLS. Kaggle execution is PENDING PHASE 5.**

This notebook was built and statically validated in Phase 3/4. It has **never been executed on Kaggle**; no Qwen model has been downloaded or trained, and no
GPU time has been used. Cells tagged `production` stay inert until the lock in the *Production gate* cell (two switches + the unlock environment variable + Kaggle with a CUDA GPU) is deliberately opened in Phase 5.
Library versions on Kaggle are **UNVERIFIED** until Phase 5: every local test ran on CPU with transformers 5.19.0, peft 0.21.2, trl 1.15.0, torch 2.14.1.

Cell order: environment check -> locate and verify the package -> install dependencies -> show config -> safe dry run -> **production gate** -> train -> resume -> evaluate -> base-vs-fine-tuned comparison -> export adapter -> summary.""")

md("c01-rules", """## Rules for this notebook
* Never paste a token into a cell. If a Hugging Face token is ever needed, add it to **Kaggle Secrets** under the name `HF_TOKEN`; the gated cell reads it and never prints it. (Qwen3-8B is open-weights and normally needs none.)
* Turn **Internet** and **GPU** on only when Phase 5 starts. Before that the notebook is read-only documentation.
* Only LoRA **adapters** are saved to `/kaggle/working` (merged 16 GB models would blow the output limit).
* Results are reported as measured only if a cell below printed them in the run log. Memory/time figures in `docs/MEMORY_PLAN.md` are ESTIMATES.""")

code("c02-env", '''
# 1. Environment check - read-only: no installs, no downloads, no model loading.
import os, sys, platform, shutil, subprocess, pathlib

ON_KAGGLE = os.path.exists("/kaggle/input") or "KAGGLE_KERNEL_RUN_TYPE" in os.environ
print("python", sys.version.split()[0], "|", platform.platform())
print("ON_KAGGLE:", ON_KAGGLE)

def _safe_cmd(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception as exc:                       # missing nvidia-smi etc.
        return f"(unavailable: {type(exc).__name__})"

print("nvidia-smi:", _safe_cmd(["nvidia-smi", "--query-gpu=name,memory.total,compute_cap", "--format=csv,noheader"]) or "(no output)")

HAS_CUDA, BF16_OK, GPU_NAMES = False, False, []
try:
    import torch
    HAS_CUDA = torch.cuda.is_available()
    print("torch", torch.__version__, "| cuda available:", HAS_CUDA)
    if HAS_CUDA:
        GPU_NAMES = [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())]
        caps = [torch.cuda.get_device_capability(i) for i in range(torch.cuda.device_count())]
        BF16_OK = all(major >= 8 for major, _minor in caps)      # native bf16 needs compute capability >= 8 (Ampere+)
        for i, name in enumerate(GPU_NAMES):
            total_gib = torch.cuda.get_device_properties(i).total_memory / 2**30
            print(f"  gpu{i}: {name}  {total_gib:.1f} GiB  capability {caps[i]}")
        print("torch.cuda.is_bf16_supported() says:", bool(torch.cuda.is_bf16_supported()), "(True on a T4 only because bf16 is EMULATED there - do not use it to choose the precision)")
        print("native bf16 (compute capability >= 8):", BF16_OK, "->", "use bf16" if BF16_OK else "use fp16: T4 (sm_75) and P100 (sm_60) have no native bf16")
        print("recommendation: prefer a T4 (sm_75); P100 (sm_60) support in the default Kaggle PyTorch wheels is UNVERIFIED until Phase 5")
except Exception as exc:
    print("torch not importable:", type(exc).__name__)

work_root = "/kaggle/working" if ON_KAGGLE else "."
du = shutil.disk_usage(work_root)
print(f"disk at {work_root}: free {du.free / 2**30:.1f} GiB of {du.total / 2**30:.1f} GiB")
if os.path.exists("/proc/meminfo"):
    mem_kib = int(open("/proc/meminfo").readline().split()[1])
    print(f"host RAM: {mem_kib / 2**20:.1f} GiB")
print("No production training is started by this cell.")
''')

code("c03-helpers", '''
# 2. Small helpers (no side effects). Output of child processes is redacted for token-looking strings before it is shown.
import re, json, subprocess, sys

_TOKEN_RX = re.compile(r"\\b(?:hf|ghp|gho|sk)[_-][A-Za-z0-9_\\-]{16,}\\b")

def redact(line):
    return _TOKEN_RX.sub("***redacted***", line)

def run_stream(cmd, env_extra=None, cwd=None):
    """Run a child process, stream its (redacted) output, return the exit code. Never echoes the environment."""
    env = dict(os.environ)
    env.update(env_extra or {})
    env["PYTHONUNBUFFERED"] = "1"
    env["HF_HUB_DISABLE_TELEMETRY"] = "1"
    printable = " ".join(str(c) for c in cmd)
    print("$", redact(printable))
    proc = subprocess.Popen([str(c) for c in cmd], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env, cwd=cwd)
    for line in proc.stdout:
        print(redact(line.rstrip()))
    return proc.wait()
''')

code("c04-package", '''
# 3. Locate the uploaded package (a Kaggle dataset containing qwen_jarvis_kaggle_package.zip), verify its manifest hashes, and extract it.
import zipfile, hashlib, pathlib, json, os, sys

PKG_NAME = "qwen_jarvis_kaggle_package.zip"
WORK = pathlib.Path("/kaggle/working") if ON_KAGGLE else pathlib.Path("./_local_work")
PKG_DIR = WORK / "package"
RUNS = WORK / "runs"
EXPORT = WORK / "export"

PKG_DATASET_SLUG = "qwen-jarvis-phase5-package"                  # the ONLY dataset this notebook may use (utkarsh6624/qwen-jarvis-phase5-package)
OLD_DATASET_SLUG = "qwen-jarvis-phase-5-training-package"        # an older, stale upload: if it is attached the notebook refuses to continue

def find_package():
    if ON_KAGGLE:
        inp = pathlib.Path("/kaggle/input")
        everything = sorted(list(inp.rglob(PKG_NAME)) + list(inp.rglob("PACKAGE_MANIFEST.json")))
        if any(OLD_DATASET_SLUG in p.parts for p in everything):
            print(f"REFUSED - the stale dataset '{OLD_DATASET_SLUG}' is attached. Remove it (Add data -> remove) and keep only '{PKG_DATASET_SLUG}'.")
            return None, None
        mine = [p for p in everything if PKG_DATASET_SLUG in p.parts]
        manifests = [p for p in mine if p.name == "PACKAGE_MANIFEST.json"]
        zips = [p for p in mine if p.name == PKG_NAME]
        if len(manifests) + len(zips) != 1:
            print(f"REFUSED - expected exactly one package inside the dataset '{PKG_DATASET_SLUG}', found {len(manifests)} manifest(s) and {len(zips)} zip(s).")
            return None, None
        return ("dir", manifests[0].parent) if manifests else ("zip", zips[0])
    for root in (pathlib.Path("kaggle/dist"), pathlib.Path("../dist")):
        if root.exists():
            for p in sorted(root.rglob(PKG_NAME)):
                return "zip", p
            for p in sorted(root.rglob("PACKAGE_MANIFEST.json")):
                return "dir", p.parent
    return None, None

kind, found = find_package()
if kind is None:
    print("Package not found. Add ONLY the Kaggle dataset", PKG_DATASET_SLUG, "(owner utkarsh6624) to this notebook (Add data -> your dataset).")
else:
    print("package source:", kind, found)
    if kind == "zip":
        PKG_DIR.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(found) as z:
            z.extractall(PKG_DIR)
        pkg_root = PKG_DIR
    else:
        pkg_root = found
    manifest = json.loads((pkg_root / "PACKAGE_MANIFEST.json").read_text(encoding="utf-8"))
    bad = [f["path"] for f in manifest["files"] if hashlib.sha256((pkg_root / f["path"]).read_bytes()).hexdigest() != f["sha256"]]
    print(f"manifest files: {len(manifest['files'])}, hash mismatches: {len(bad)}, dataset dir: {manifest['dataset_dir']}")
    assert not bad, f"package integrity check failed: {bad[:5]}"
    # Frozen-dataset integrity: the dataset directory must contain MANIFEST.json (exact, case-sensitive name - Kaggle is Linux) and the
    # train/validation/test files must match the SHA-256 values recorded in it. train.py performs the same check at start-up.
    ds_dir = pkg_root / "dataset" / "v2_final"
    ds_names = sorted(q.name for q in ds_dir.iterdir())
    assert "MANIFEST.json" in ds_names, f"dataset/v2_final has no MANIFEST.json (exact case); found {ds_names}"
    ds_manifest = json.loads((ds_dir / "MANIFEST.json").read_text(encoding="utf-8"))
    ds_bad = [name for name, info in ds_manifest["files"].items() if hashlib.sha256((ds_dir / name).read_bytes()).hexdigest() != info["sha256"]]
    print(f"dataset {ds_manifest.get('version')}: {len(ds_manifest['files'])} files checked against MANIFEST.json, mismatches: {len(ds_bad)}")
    assert not ds_bad, f"dataset files do not match MANIFEST.json: {ds_bad}"
    PKG_DIR = pkg_root
    os.chdir(PKG_DIR)
    sys.path.insert(0, str(PKG_DIR))
    print("working directory:", os.getcwd())
''')

code("c05-install", '''
# 4. Install dependencies. Only on Kaggle (never into a local/NEXUS environment). torch is NOT reinstalled (Kaggle ships a CUDA build).
import subprocess, sys, importlib.metadata as md

INSTALL_DEPS = True
if ON_KAGGLE and INSTALL_DEPS and kind is not None:
    rc = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", "kaggle/requirements_kaggle.txt"]).returncode
    print("pip exit code:", rc)
else:
    print("Skipped dependency install (not on Kaggle, INSTALL_DEPS is False, or the package was not found).")
LOCALLY_TESTED = {"torch": "2.14.1 (CPU)", "transformers": "5.19.0", "peft": "0.21.2", "trl": "1.15.0", "accelerate": "1.15.0", "datasets": "5.1.0",
                  "bitsandbytes": "never run locally (CUDA only)"}
print("Library versions on this machine vs the only combination tested in Phase 3/4 (CPU, local venv). Versions on Kaggle are UNVERIFIED until Phase 5.")
for name in ["torch", "transformers", "peft", "accelerate", "bitsandbytes", "datasets", "tokenizers", "pyyaml", "trl"]:
    try:
        print(f"{name:14s} {md.version(name):14s} | locally tested: {LOCALLY_TESTED.get(name, '-')}")
    except md.PackageNotFoundError:
        print(f"{name:14s} {'not installed':14s} | locally tested: {LOCALLY_TESTED.get(name, '-')}")
if ON_KAGGLE and kind is not None:
    rc_check = subprocess.run([sys.executable, "-m", "pip", "check"]).returncode
    print("pip check exit code:", rc_check, "(0 = no broken requirements; non-zero means fix the conflict before the production gate)")
''')

code("c06-config", '''
# 5. Show the training configuration (read-only). The production config is locked (production_locked: true) until Phase 5.
import yaml

CONFIG = "configs/qlora_sft.yaml"
cfg = yaml.safe_load(open(CONFIG, encoding="utf-8"))
print("banner:", cfg.get("banner"), "| production_locked:", cfg.get("production_locked"))
for section in ("model", "quant", "lora", "data", "train", "output"):
    print(f"[{section}]")
    for key, value in cfg.get(section, {}).items():
        print(f"   {key}: {value}")
''')

code("c07-dryrun", '''
# 6. SAFE dry run - validates config, dataset, tokenizer and training arguments and projects time/memory. No model weights are loaded or trained.
TRAIN_MODULE = "pipeline.train.train"
rc1 = run_stream([sys.executable, "-m", TRAIN_MODULE, "--config", CONFIG, "--validate-config"])
rc2 = run_stream([sys.executable, "-m", TRAIN_MODULE, "--config", CONFIG, "--dry-run"])
# the 20-step GPU probe plan (same overrides the probe launch cell uses): expect EXACT_total_optimizer_steps 20 and limits_active null/null
rc3 = run_stream([sys.executable, "-m", TRAIN_MODULE, "--config", CONFIG, "--dry-run", "--set", "train.max_steps=20", "--set", "output.run_name=probe20"])
print("validate-config exit code:", rc1, "| dry-run exit code:", rc2, "| probe dry-run exit code:", rc3)
assert rc1 == 0 and rc2 == 0 and rc3 == 0, "fix the dry-run failures before opening the production gate"
''')

md("c08-gate-md", """## PRODUCTION GATE

Everything below this line that is tagged `production` can download Qwen3-8B and start training. It is **disabled by default**.
To open it in Phase 5 (and only then): set `PHASE5_AUTHORIZED = True` and `CONFIRM_PRODUCTION = True` in the next cell **and** set the environment variable named in `UNLOCK_ENV`
to the exact `UNLOCK_VALUE`. All four conditions (including a CUDA GPU on Kaggle) are checked by `production_allowed()`.

`RUN_KIND` defaults to `"probe20"`: the launch cell then adds `--set train.max_steps=20 --set output.run_name=probe20`, so opening the gate runs only a 20-step probe on the full dataset. The real two-epoch run needs `RUN_KIND = "full"` set deliberately; it uses its own run directory (`runs/qwen3_8b_jarvis_full_v1`), checks for existing checkpoints only there, and never touches `runs/probe20`.""")

code("c09-gate", '''
# 7. Production gate. DISABLED BY DEFAULT.
UNLOCK_ENV = "QWEN_PHASE5_UNLOCK"
UNLOCK_VALUE = "I_UNDERSTAND_THIS_STARTS_PRODUCTION_TRAINING"
PHASE5_AUTHORIZED = False      # <- a human changes this to True in Phase 5 only
CONFIRM_PRODUCTION = False     # <- second independent switch
MODE = "train"                 # <- "train" = FRESH run (cell 8 acts, cell 9 skips); "resume" = continue after a killed session (cell 9 acts, cell 8 skips).
                               #    Set it explicitly: 'Run all' in a restarted session must not start a fresh run over an existing one.
assert MODE in ("train", "resume"), "MODE must be 'train' or 'resume'"
RUN_KIND = "probe20"           # <- "probe20" (DEFAULT) = 20-step GPU probe in a fresh run dir 'probe20'; "full" = the real 2-epoch run, only after the probe was reviewed.
assert RUN_KIND in ("probe20", "full"), "RUN_KIND must be 'probe20' or 'full'"
PROBE_RUN_NAME, FULL_RUN_NAME = "probe20", "qwen3_8b_jarvis_full_v1"
assert PROBE_RUN_NAME != FULL_RUN_NAME
PROBE_ARGS = ["--set", "train.max_steps=20", "--set", "output.run_name=probe20"]
FULL_ARGS = ["--set", "output.run_name=qwen3_8b_jarvis_full_v1"]      # no max_steps override: the config's 2 epochs = 1596 optimizer steps
RUN_NAME = PROBE_RUN_NAME if RUN_KIND == "probe20" else FULL_RUN_NAME
RUN_DIR = RUNS / RUN_NAME      # the trainer writes checkpoints to <output_dir>/<run_name> = this directory; every cell below looks ONLY here, never across RUNS

def launch_overrides():
    """Extra trainer overrides for the launch/resume cells: the probe caps the run at 20 optimizer steps in runs/probe20; the full run gets its own run name (runs/qwen3_8b_jarvis_full_v1)."""
    return list(PROBE_ARGS) if RUN_KIND == "probe20" else list(FULL_ARGS)
# The third lock is the environment variable: in Phase 5 add  os.environ[UNLOCK_ENV] = UNLOCK_VALUE  here yourself. It is NOT set by this notebook.

def production_allowed():
    reasons = []
    if not PHASE5_AUTHORIZED:
        reasons.append("PHASE5_AUTHORIZED is False")
    if not CONFIRM_PRODUCTION:
        reasons.append("CONFIRM_PRODUCTION is False")
    if os.environ.get(UNLOCK_ENV) != UNLOCK_VALUE:
        reasons.append(f"environment variable {UNLOCK_ENV} is not set to the unlock value")
    if not ON_KAGGLE:
        reasons.append("not running on Kaggle")
    if not HAS_CUDA:
        reasons.append("no CUDA GPU visible")
    if kind is None:
        reasons.append("package not found")
    return (len(reasons) == 0), reasons

def load_hub_auth():
    """Reads HF_TOKEN from Kaggle Secrets if one exists. The value is placed in the environment and is never printed."""
    try:
        from kaggle_secrets import UserSecretsClient
        token = UserSecretsClient().get_secret("HF_TOKEN")
    except Exception:
        return False
    if token:
        os.environ["HF_TOKEN"] = token
        return True
    return False

allowed, why = production_allowed()
print("PRODUCTION ALLOWED:", allowed)
if not allowed:
    print("locked because:", "; ".join(why))
''', tags=["gate"])

code("c10-train", '''
# 8. PRODUCTION: launch Qwen3-8B QLoRA SFT. Inert unless the gate is open.
ok, reasons = production_allowed()
if not ok:
    print("LOCKED - production training did not start:", "; ".join(reasons))
elif MODE != "train":
    print(f"SKIPPED - MODE is '{MODE}'; this cell needs MODE = 'train' (a fresh run).")
elif RUN_DIR.exists() and list(RUN_DIR.rglob("latest.json")):
    print("REFUSED - checkpoints already exist under", RUN_DIR, "- set MODE = 'resume' to continue them, or choose a different run name for a fresh run.")
else:
    print("Hugging Face auth from Kaggle Secrets:", "present" if load_hub_auth() else "not used (public model)")
    print("RUN_KIND:", RUN_KIND, "| run dir:", RUN_DIR, "| overrides:", " ".join(launch_overrides()))
    RUNS.mkdir(parents=True, exist_ok=True)
    rc = run_stream([sys.executable, "-m", TRAIN_MODULE, "--config", CONFIG, "--set", "production_locked=false",
                     "--set", f"output.output_dir={RUNS}", *launch_overrides(), "--confirm-production"])
    print("training exit code:", rc, "(throughput: read `tok_per_s` in the training log lines above, and the first_steps_throughput event after 10 steps)")
''', tags=["production"])

code("c11-resume", '''
# 9. PRODUCTION: resume after a killed session. Mount the previous run's checkpoints as a Kaggle dataset (it appears under /kaggle/input/...).
ok, reasons = production_allowed()
if not ok:
    print("LOCKED - resume did not start:", "; ".join(reasons))
elif MODE != "resume":
    print(f"SKIPPED - MODE is '{MODE}'; this cell needs MODE = 'resume' (continue an existing run after a killed session).")
else:
    RUNS.mkdir(parents=True, exist_ok=True)
    previous = sorted(p for p in pathlib.Path("/kaggle/input").rglob("latest.json") if p.parent.name == RUN_NAME)
    if previous and not list(RUN_DIR.rglob("latest.json")):
        import shutil as _sh
        src_run = previous[0].parent
        _sh.copytree(src_run, RUN_DIR, dirs_exist_ok=True)
        print("restored previous run:", src_run.name)
    rc = run_stream([sys.executable, "-m", TRAIN_MODULE, "--config", CONFIG, "--set", "production_locked=false",
                     "--set", f"output.output_dir={RUNS}", *launch_overrides(), "--confirm-production", "--resume", "latest"])
    print("resume exit code:", rc)
''', tags=["production"])

code("c12-eval", '''
# 10. PRODUCTION: evaluate the fine-tuned adapter (loads the base model, so it is gated). Reports are written under /kaggle/working/eval.
EVAL_MODULE = "pipeline.eval.cli"
ok, reasons = production_allowed()
if not ok:
    print("LOCKED - evaluation did not start:", "; ".join(reasons))
else:
    best = sorted(RUN_DIR.rglob("best.json"))
    adapter_dir = (best[0].parent / json.loads(best[0].read_text())["checkpoint"] / "adapter") if best else None
    if adapter_dir is None or not adapter_dir.exists():
        print("no best checkpoint found - train first")
    else:
        rc = run_stream([sys.executable, "-m", EVAL_MODULE, "run", "--model", "Qwen/Qwen3-8B", "--adapter", str(adapter_dir),
                         "--suite", "all", "--out", str(WORK / "eval" / "finetuned"), "--tag", "finetuned",
                         "--dataset-version", "qwen_jarvis_dataset_v2_final", "--allow-model-load"])
        print("evaluation exit code:", rc)
''', tags=["production"])

code("c13-compare", '''
# 11. PRODUCTION: base Qwen3-8B vs fine-tuned comparison on the same held-out test set (the baseline run needs no adapter).
ok, reasons = production_allowed()
if not ok:
    print("LOCKED - baseline comparison did not start:", "; ".join(reasons))
else:
    rc = run_stream([sys.executable, "-m", EVAL_MODULE, "run", "--model", "Qwen/Qwen3-8B", "--suite", "all",
                     "--out", str(WORK / "eval" / "base"), "--tag", "base",
                     "--dataset-version", "qwen_jarvis_dataset_v2_final", "--allow-model-load"])
    print("baseline evaluation exit code:", rc)
    base_json, ft_json = WORK / "eval" / "base" / "eval_results.json", WORK / "eval" / "finetuned" / "eval_results.json"
    if rc == 0 and base_json.exists() and ft_json.exists():
        rc2 = run_stream([sys.executable, "-m", EVAL_MODULE, "compare", "--a", str(base_json), "--b", str(ft_json),
                          "--label-a", "base", "--label-b", "finetuned", "--out", str(WORK / "eval" / "compare")])
        print("compare exit code:", rc2)
    else:
        print("compare skipped: both evaluations must have completed first. No numbers exist until these cells have really run.")
''', tags=["production"])

code("c13b-loss", '''
# 11b. PRODUCTION: assistant-token loss / perplexity on the held-out test split, base vs fine-tuned (loads the model, so it is gated).
# Meaningful only as in-distribution assistant-token loss; compare checkpoints of the same model on the same split.
ok, reasons = production_allowed()
if not ok:
    print("LOCKED - loss evaluation did not start:", "; ".join(reasons))
else:
    best = sorted(RUN_DIR.rglob("best.json"))
    adapter_dir = (best[0].parent / json.loads(best[0].read_text())["checkpoint"] / "adapter") if best else None
    rc_base = run_stream([sys.executable, "-m", EVAL_MODULE, "loss", "--model", "Qwen/Qwen3-8B", "--split", "dataset/v2_final/test.jsonl",
                          "--out", str(WORK / "eval" / "loss_base.json"), "--allow-model-load"])
    print("base loss exit code:", rc_base)
    if adapter_dir is not None and adapter_dir.exists():
        rc_ft = run_stream([sys.executable, "-m", EVAL_MODULE, "loss", "--model", "Qwen/Qwen3-8B", "--adapter", str(adapter_dir),
                            "--split", "dataset/v2_final/test.jsonl", "--out", str(WORK / "eval" / "loss_finetuned.json"), "--allow-model-load"])
        print("fine-tuned loss exit code:", rc_ft)
    else:
        print("no best checkpoint found - fine-tuned loss skipped. No numbers exist until these cells have really run.")
''', tags=["production"])

code("c14-export", '''
# 12. Export the LoRA adapter only (small). Safe to run: it just copies files and prints sizes; with no checkpoint it does nothing.
import shutil, json, pathlib

best = sorted(RUN_DIR.rglob("best.json")) if RUN_DIR.exists() else []
if not best:
    print("No best checkpoint under", RUN_DIR, "- nothing to export (no training has run).")
else:
    pointer = json.loads(best[0].read_text(encoding="utf-8"))
    src = best[0].parent / pointer["checkpoint"] / "adapter"
    EXPORT.mkdir(parents=True, exist_ok=True)
    dst = EXPORT / "qwen3_8b_jarvis_lora_adapter"
    shutil.copytree(src, dst, dirs_exist_ok=True)
    total_mb = sum(p.stat().st_size for p in dst.rglob("*") if p.is_file()) / 1e6
    print(f"exported adapter from {src} -> {dst} ({total_mb:.0f} MB)")
    print("Optional merge (NOT done here): merging into the bf16 base needs ~17 GB RAM and ~16 GB disk; do it outside /kaggle/working or skip.")
''')

code("c15-summary", '''
# 13. Final summary - reads what exists; claims nothing that was not produced by this session.
import glob
print("== run summary ==")
metas = sorted(glob.glob(str(RUN_DIR / "**" / "checkpoint_meta.json"), recursive=True)) if RUN_DIR.exists() else []
if not metas:
    print("No checkpoints found: no training has run in this session (expected during Phase 3/4: Kaggle execution is PENDING PHASE 5).")
else:
    last = json.loads(pathlib.Path(metas[-1]).read_text(encoding="utf-8"))
    print("latest checkpoint step:", last.get("step"), "| epoch:", last.get("epoch"))
    print("metrics:", last.get("metrics"))
    print("identity:", last.get("identity"))
    print("libraries:", last.get("libraries"))
print("production gate was open:", production_allowed()[0])
''')

nb = new_notebook(cells=cells, metadata={
    "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
    "language_info": {"name": "python", "version": "3.11"},
    "kaggle": {"accelerator": "none", "isInternetEnabled": False, "isGpuEnabled": False,
               "note": "Phase 3/4 build: GPU and internet are OFF. Enable them only when starting Phase 5."},
})
nb.nbformat = 4
nb.nbformat_minor = 5
nbf.validate(nb)
OUT.write_text(nbf.writes(nb) + "\n", encoding="utf-8")
print("wrote", OUT, "-", len(cells), "cells,", sum(1 for c in cells if c.cell_type == "code"), "code")
