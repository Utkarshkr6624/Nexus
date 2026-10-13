"""Executes the notebook's cells LOCALLY in a temp directory (this is NOT a Kaggle run) to prove the production gate is inert.
It records every subprocess launch and fails if any training/evaluation module is launched without the safe-mode flags.
Scenarios: (A) defaults; (B) both switches True but no unlock env var and not on Kaggle; (C) switches True AND env var set, still not on Kaggle / no CUDA.
Writes kaggle/notebook_gate_test.json.   usage: python kaggle/test_notebook_gate.py   (run with the workspace venv python)"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import nbformat

HERE = Path(__file__).resolve().parent
NB = HERE / "notebook" / "qwen_jarvis_phase5_training.ipynb"
ZIP = HERE / "dist" / "qwen_jarvis_kaggle_package.zip"
if "--zip" in sys.argv:
    ZIP = Path(sys.argv[sys.argv.index("--zip") + 1])
REPORT = HERE / "notebook_gate_test.json"
UNLOCK_VALUE = "I_UNDERSTAND_THIS_STARTS_PRODUCTION_TRAINING"
SAFE = ("--validate-config", "--dry-run", "--smoke-test")
ORDER = ["c02-env", "c03-helpers", "c04-package", "c05-install", "c06-config", "c07-dryrun", "c09-gate", "c10-train", "c11-resume", "c12-eval", "c13-compare", "c13b-loss", "c14-export", "c15-summary"]


def run_scenario(name, patch_gate):
    nb = nbformat.read(str(NB), as_version=4)
    cells = {c.id: c.source for c in nb.cells if c.cell_type == "code"}
    n_prod = sum(1 for c in nb.cells if c.cell_type == "code" and "production" in c.metadata.get("tags", []))
    launched = []
    real_popen = subprocess.Popen

    class RecordingPopen(real_popen):
        def __init__(self, args, *a, **k):
            launched.append([str(x) for x in args])
            super().__init__(args, *a, **k)

    tmp = Path(tempfile.mkdtemp(prefix="p4_gate_"))
    (tmp / "kaggle" / "dist").mkdir(parents=True)
    shutil.copy(ZIP, tmp / "kaggle" / "dist" / ZIP.name)
    old_cwd, old_env = os.getcwd(), dict(os.environ)
    os.environ.pop("QWEN_PHASE5_UNLOCK", None)
    ns = {"__name__": "__notebook__"}
    out = io.StringIO()
    errors = []
    try:
        os.chdir(tmp)
        subprocess.Popen = RecordingPopen
        with contextlib.redirect_stdout(out):
            for cid in ORDER:
                src = cells[cid]
                if cid == "c09-gate" and patch_gate:
                    src = src.replace("PHASE5_AUTHORIZED = False", "PHASE5_AUTHORIZED = True").replace("CONFIRM_PRODUCTION = False", "CONFIRM_PRODUCTION = True")
                    if patch_gate == "with_env":
                        os.environ["QWEN_PHASE5_UNLOCK"] = UNLOCK_VALUE
                try:
                    exec(compile(src, cid, "exec"), ns)
                except BaseException as e:   # an AssertionError in the dry-run cell is reported, not hidden
                    errors.append((cid, f"{type(e).__name__}: {str(e)[:200]}"))
                print(f"----- end of {cid}")
    finally:
        subprocess.Popen = real_popen
        os.chdir(old_cwd)
        os.environ.clear()
        os.environ.update(old_env)
        shutil.rmtree(tmp, ignore_errors=True)
    text = out.getvalue()
    unsafe = [cmd for cmd in launched if any(m in " ".join(cmd) for m in ("pipeline.train.train", "pipeline.eval")) and not any(f in cmd for f in SAFE)]
    locked_msgs = sum(1 for line in text.splitlines() if line.startswith("LOCKED"))
    return {"scenario": name, "launched_commands": launched, "unsafe_launches": unsafe, "locked_messages_from_production_cells": locked_msgs,
            "production_allowed_printed": [l for l in text.splitlines() if l.startswith("PRODUCTION ALLOWED")], "cell_errors": errors,
            "pass": not unsafe and locked_msgs == n_prod and not any(e[0] in ("c09-gate", "c10-train", "c11-resume", "c12-eval", "c13-compare", "c13b-loss") for e in errors),
            "tail": text.splitlines()[-12:]}


PROBE_NAME, FULL_NAME = "probe20", "qwen3_8b_jarvis_full_v1"


def _tree(root):
    """{relative path: bytes} of every file under root (to prove a scenario left existing artifacts untouched)."""
    return {str(f.relative_to(root)): f.read_bytes() for f in sorted(root.rglob("*")) if f.is_file()} if root.exists() else {}


def run_mode_logic():
    """Scenario D: the gate is SIMULATED open (production_allowed patched) and run_stream is a recorder - nothing is launched.
    Verifies that only the cell matching MODE issues a command, that the train cell refuses over checkpoints of ITS OWN run directory only
    (an existing probe20 run must not block the full run), that the run names/dirs of probe and full never collide, and that existing artifacts are untouched."""
    nb = nbformat.read(str(NB), as_version=4)
    cells = {c.id: c.source for c in nb.cells if c.cell_type == "code"}
    results = {}
    cases = (("train", None, "probe20"), ("resume", None, "probe20"), ("train", "own", "probe20"),
             ("train", None, "full"), ("resume", None, "full"), ("train", "own", "full"),
             ("train", "probe_only", "full"), ("resume", "probe_only", "full"))
    for mode, pre, run_kind in cases:
        tmp = Path(tempfile.mkdtemp(prefix="p4_mode_"))
        ns = {"__name__": "__notebook__", "os": os, "sys": sys, "json": json}
        out = io.StringIO()
        calls = []
        want_name = PROBE_NAME if run_kind == "probe20" else FULL_NAME
        try:
            with contextlib.redirect_stdout(out):
                import pathlib
                ns.update(pathlib=pathlib, RUNS=tmp / "runs", WORK=tmp, TRAIN_MODULE="pipeline.train.train", CONFIG="configs/qlora_sft.yaml",
                          ON_KAGGLE=False, HAS_CUDA=False, kind=None)
                # run the REAL gate cell (MODE / RUN_KIND / RUN_DIR / launch_overrides come from it), then simulate an open gate and record launches
                gate_src = cells["c09-gate"].replace('MODE = "train" ', f'MODE = "{mode}" ', 1).replace('RUN_KIND = "probe20" ', f'RUN_KIND = "{run_kind}" ', 1)
                assert f'MODE = "{mode}"' in gate_src and f'RUN_KIND = "{run_kind}"' in gate_src
                exec(compile(gate_src, "c09-gate", "exec"), ns)
                ns.update(production_allowed=lambda: (True, []), load_hub_auth=lambda: False, run_stream=lambda cmd, **k: (calls.append([str(c) for c in cmd]) or 0))
                if pre:
                    d = tmp / "runs" / (want_name if pre == "own" else PROBE_NAME)
                    (d / "checkpoint-20" / "adapter").mkdir(parents=True)
                    (d / "latest.json").write_text('{"checkpoint": "checkpoint-20", "step": 20}')
                    (d / "best.json").write_text('{"checkpoint": "checkpoint-20", "step": 20}')
                    (d / "checkpoint-20" / "adapter" / "adapter_model.safetensors").write_bytes(b"PROBE-OR-OLD-ADAPTER")
                before = _tree(tmp / "runs")
                exec(compile(cells["c10-train"], "c10-train", "exec"), ns)
                n_after_train = len(calls)
                exec(compile(cells["c11-resume"], "c11-resume", "exec"), ns)
                untouched = all(_tree(tmp / "runs").get(k) == v for k, v in before.items())      # nothing that existed was changed or removed
                run_dir_ok = ns["RUN_NAME"] == want_name and ns["RUN_DIR"] == tmp / "runs" / want_name
                if run_kind == "full":
                    run_dir_ok = run_dir_ok and ns["RUN_DIR"] != tmp / "runs" / PROBE_NAME and PROBE_NAME not in ns["RUN_DIR"].parts
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        text = out.getvalue()
        train_cmds, resume_cmds = calls[:n_after_train], calls[n_after_train:]
        key = f"MODE={mode}, RUN_KIND={run_kind}, existing={pre}"
        name_arg = lambda cmd: next((c.split("=", 1)[1] for c in cmd if c.startswith("output.run_name=")), None)
        steps_arg = lambda cmd: next((c for c in cmd if c.startswith("train.max_steps=")), None)
        no_hparams = lambda cmd: not any(c.startswith(("train.epochs", "train.learning_rate", "train.per_device_batch", "train.grad_accum", "data.max_", "quant.", "lora.", "model.")) for c in cmd)
        right_args = lambda cmd: name_arg(cmd) == want_name and no_hparams(cmd) and (steps_arg(cmd) == "train.max_steps=20" if run_kind == "probe20" else steps_arg(cmd) is None)
        if mode == "train" and pre != "own":
            ok = len(train_cmds) == 1 and "--resume" not in train_cmds[0] and not resume_cmds and "SKIPPED" in text and right_args(train_cmds[0]) \
                and "--confirm-production" in train_cmds[0] and any(x.startswith("output.output_dir=") for x in train_cmds[0]) and "production_locked=false" in train_cmds[0] \
                and "REFUSED" not in text.split("SKIPPED")[0]
        elif mode == "resume":
            ok = not train_cmds and len(resume_cmds) == 1 and "--resume" in resume_cmds[0] and "SKIPPED" in text and right_args(resume_cmds[0])
        else:
            ok = not train_cmds and not resume_cmds and "REFUSED" in text
        ok = ok and untouched and run_dir_ok
        results[key] = {"pass": bool(ok), "train_commands": len(train_cmds), "resume_commands": len(resume_cmds),
                        "messages": [l[:140] for l in text.splitlines() if l.startswith(("SKIPPED", "REFUSED", "restored"))],
                        "train_command": [c if not c.startswith("output.output_dir=") else "output.output_dir=<runs>" for c in train_cmds[0][3:]] if train_cmds else None}
    return results


def run_run_dir_isolation():
    """Scenario F: with BOTH runs/probe20 and the full run directory present, the evaluation, loss and export cells and the summary must use the ACTIVE
    run only (sorted(RUNS.rglob('best.json'))[0] would have picked probe20 first)."""
    nb = nbformat.read(str(NB), as_version=4)
    cells = {c.id: c.source for c in nb.cells if c.cell_type == "code"}
    results = {}
    for run_kind in ("full", "probe20"):
        tmp = Path(tempfile.mkdtemp(prefix="p4_iso_"))
        out, calls = io.StringIO(), []
        want = FULL_NAME if run_kind == "full" else PROBE_NAME
        other = PROBE_NAME if run_kind == "full" else FULL_NAME
        try:
            import pathlib
            for name in (PROBE_NAME, FULL_NAME):
                d = tmp / "runs" / name
                (d / "checkpoint-9" / "adapter").mkdir(parents=True)
                (d / "best.json").write_text(json.dumps({"checkpoint": "checkpoint-9", "step": 9}))
                (d / "checkpoint-9" / "checkpoint_meta.json").write_text(json.dumps({"step": 9, "epoch": 0, "metrics": {}, "identity": name}))
                (d / "checkpoint-9" / "adapter" / "adapter_model.safetensors").write_bytes(name.encode())
            ns = {"__name__": "__notebook__", "os": os, "sys": sys, "json": json, "pathlib": pathlib, "RUNS": tmp / "runs", "WORK": tmp, "EXPORT": tmp / "export",
                  "TRAIN_MODULE": "pipeline.train.train", "CONFIG": "configs/qlora_sft.yaml", "ON_KAGGLE": False, "HAS_CUDA": False, "kind": None}
            with contextlib.redirect_stdout(out):
                exec(compile(cells["c09-gate"].replace('RUN_KIND = "probe20" ', f'RUN_KIND = "{run_kind}" ', 1), "c09-gate", "exec"), ns)
                ns.update(production_allowed=lambda: (True, []), run_stream=lambda cmd, **k: (calls.append([str(c) for c in cmd]) or 0))
                for cid in ("c12-eval", "c13b-loss", "c14-export", "c15-summary"):
                    exec(compile(cells[cid], cid, "exec"), ns)
            adapters = [c[c.index("--adapter") + 1] for c in calls if "--adapter" in c]
            exported = (tmp / "export" / "qwen3_8b_jarvis_lora_adapter" / "adapter_model.safetensors").read_bytes().decode()
            text = out.getvalue()
            ok = bool(adapters) and all(Path(a).parts[-3] == want and other not in Path(a).parts for a in adapters) and exported == want \
                and f"identity: {want}" in text and f"identity: {other}" not in text
            results[f"RUN_KIND={run_kind}"] = {"pass": ok, "adapters_used": [Path(a).parts[-3] for a in adapters], "exported_from": exported}
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return results


def run_dataset_pin_logic():
    """Scenario E: the package finder must use ONLY the dataset qwen-jarvis-phase5-package and refuse when the stale dataset is attached.
    Executes the notebook's real find_package() against fake /kaggle/input trees (mount layouts: /input/<slug> and /input/datasets/<owner>/<slug>)."""
    nb = nbformat.read(str(NB), as_version=4)
    src = {c.id: c.source for c in nb.cells if c.cell_type == "code"}["c04-package"]
    body = src[src.index("PKG_DATASET_SLUG ="):src.index("kind, found = find_package()")]
    new_slug, old_slug = "qwen-jarvis-phase5-package", "qwen-jarvis-phase-5-training-package"
    cases = {"only_new_flat": ([new_slug], "dir", new_slug), "only_new_nested": ([f"datasets/utkarsh6624/{new_slug}"], "dir", new_slug),
             "both_attached": ([old_slug, new_slug], None, None), "only_old": ([old_slug], None, None), "other_dataset": (["some-other-dataset"], None, None),
             "none": ([], None, None), "new_twice": ([new_slug, f"datasets/x/{new_slug}"], None, None)}
    results = {}
    for name, (mounts, want_kind, want_slug) in cases.items():
        tmp = Path(tempfile.mkdtemp(prefix="p4_pin_"))
        try:
            for m in mounts:
                d = tmp / "input" / m
                d.mkdir(parents=True)
                (d / "PACKAGE_MANIFEST.json").write_text("{}")
            import pathlib as _pl
            out = io.StringIO()
            ns = {"pathlib": _pl, "PKG_NAME": "qwen_jarvis_kaggle_package.zip", "ON_KAGGLE": True}
            with contextlib.redirect_stdout(out):
                exec(compile(body.replace('"/kaggle/input"', repr(str(tmp / "input"))), "c04-find", "exec"), ns)
                kind, found = ns["find_package"]()
            ok = kind == want_kind and (want_slug is None or (want_slug in Path(found).parts and old_slug not in Path(found).parts))
            results[name] = {"pass": ok, "kind": kind, "message": out.getvalue().strip()[:140]}
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return results


if __name__ == "__main__":
    if not ZIP.exists():
        sys.exit("build the package first: python kaggle/build_package.py")
    results = [run_scenario("A_defaults", None), run_scenario("B_switches_true_no_env", "switches"), run_scenario("C_switches_true_env_set_not_kaggle", "with_env")]
    mode_results = run_mode_logic()
    pin_results = run_dataset_pin_logic()
    iso_results = run_run_dir_isolation()
    summary = {"all_pass": all(r["pass"] for r in results) and all(v["pass"] for v in mode_results.values()) and all(v["pass"] for v in pin_results.values()) and all(v["pass"] for v in iso_results.values()), "mode_switch_logic": mode_results, "run_dir_isolation": iso_results, "dataset_pin_logic": pin_results, "note": "Local execution of notebook cells with subprocess recording. This is NOT a Kaggle run; Kaggle execution is PENDING PHASE 5.",
               "results": results}
    REPORT.write_text(json.dumps(summary, indent=1), encoding="utf-8")
    for r in results:
        print(("PASS" if r["pass"] else "FAIL"), r["scenario"], "| launches:", len(r["launched_commands"]), "| unsafe:", len(r["unsafe_launches"]), "| LOCKED msgs:", r["locked_messages_from_production_cells"], "| cell errors:", r["cell_errors"])
    for k, v in mode_results.items():
        print(("PASS" if v["pass"] else "FAIL"), "MODE logic:", k, "| train cmds:", v["train_commands"], "| resume cmds:", v["resume_commands"], "|", v["messages"])
    for k, v in iso_results.items():
        print(("PASS" if v["pass"] else "FAIL"), "run-dir isolation:", k, "| adapters used:", v["adapters_used"], "| exported from:", v["exported_from"])
    for k, v in pin_results.items():
        print(("PASS" if v["pass"] else "FAIL"), "dataset pin:", k, "| kind:", v["kind"], "|", v["message"])
    sys.exit(0 if summary["all_pass"] else 1)
