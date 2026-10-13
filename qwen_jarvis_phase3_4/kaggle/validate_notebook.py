"""Static validation of the Kaggle notebook (Phase 4 C10).  Does NOT execute the notebook and does NOT contact Kaggle or Hugging Face.
Writes kaggle/notebook_validation.json.  Exit code 1 if any check FAILS (PENDING checks do not fail unless --strict).
Kaggle execution itself is reported as PENDING PHASE 5 - it is never claimed to have run.

Checks: nbformat validity, empty outputs, banner, every code cell compiles (magics stripped), use-before-define across cells,
imports resolvable against requirements, path consistency with the package file list, entry-point modules and CLI flags vs the real
sources (train.py / eval cli), config keys used by --set, production gating (tags, branch structure, default-False switches, unlock string equals
the one in configs/qlora_sft.yaml, nothing sets the unlock env var), no weight download outside the gate, no secrets, no secret printing.
usage: python kaggle/validate_notebook.py [--strict] [--notebook PATH]"""
import argparse
import ast
import builtins
import json
import re
import sys
from pathlib import Path

import nbformat

HERE = Path(__file__).resolve().parent
WS = HERE.parent
sys.path.insert(0, str(HERE))
import build_package  # noqa: E402  (stdlib-only module; gives the package file list and the secret patterns)

NB_PATH = HERE / "notebook" / "qwen_jarvis_phase5_training.ipynb"
REQ_PATH = HERE / "requirements_kaggle.txt"
REPORT = HERE / "notebook_validation.json"

checks = []
ZIP_OVERRIDE = None     # --zip PATH validates a scratch build instead of kaggle/dist (used while the final package is not to be rebuilt yet)


def rec(name, status, detail=""):
    assert status in ("pass", "fail", "pending")
    checks.append({"name": name, "status": status, "detail": detail})


def strip_magics(src):
    return "\n".join(l for l in src.splitlines() if not l.lstrip().startswith(("%", "!")))


def names_in(node, ctx):
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ctx)}


def defined_names(tree):
    d = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            d.add(n.id)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            d.add(n.name)
            if not isinstance(n, ast.ClassDef):
                a = n.args
                for arg in a.posonlyargs + a.args + a.kwonlyargs + ([a.vararg] if a.vararg else []) + ([a.kwarg] if a.kwarg else []):
                    d.add(arg.arg)
        elif isinstance(n, ast.Lambda):
            for arg in n.args.args + n.args.kwonlyargs:
                d.add(arg.arg)
        elif isinstance(n, ast.Import):
            for al in n.names:
                d.add((al.asname or al.name).split(".")[0])
        elif isinstance(n, ast.ImportFrom):
            for al in n.names:
                d.add(al.asname or al.name)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            d.add(n.name)
    return d


def parent_map(tree):
    return {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}


def call_name(call):
    f = call.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        parts = []
        while isinstance(f, ast.Attribute):
            parts.append(f.attr)
            f = f.value
        if isinstance(f, ast.Name):
            parts.append(f.id)
        return ".".join(reversed(parts))
    return ""


def string_consts(node):
    return [n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def main(strict, nb_path):
    # ------------------------------------------------------------------ structure
    nb = nbformat.read(str(nb_path), as_version=4)
    try:
        nbformat.validate(nb)
        rec("nbformat valid (v4)", "pass", f"nbformat {nb.nbformat}.{nb.nbformat_minor}, {len(nb.cells)} cells")
    except Exception as e:
        rec("nbformat valid (v4)", "fail", str(e)[:200])
        return finish(strict)
    code_cells = [(i, c) for i, c in enumerate(nb.cells) if c.cell_type == "code"]
    dirty = [i for i, c in code_cells if c.get("outputs") or c.get("execution_count") is not None]
    rec("all outputs empty / never executed", "pass" if not dirty else "fail", f"dirty cells: {dirty}")
    first = nb.cells[0]
    ok = first.cell_type == "markdown" and "DO NOT RUN PRODUCTION CELLS" in first.source and "PENDING PHASE 5" in first.source and "**PHASE 3/4" in first.source
    rec("banner (bold: PHASE 3/4 DO NOT RUN PRODUCTION CELLS; PENDING PHASE 5)", "pass" if ok else "fail")
    kmeta = nb.metadata.get("kaggle", {})
    ok = kmeta.get("isInternetEnabled") is False and kmeta.get("isGpuEnabled") is False
    rec("notebook metadata: GPU and internet OFF by default", "pass" if ok else "fail", json.dumps(kmeta)[:160])

    # ------------------------------------------------------------------ compile
    trees, bad = {}, []
    for i, c in code_cells:
        try:
            trees[i] = ast.parse(strip_magics(c.source), filename=f"cell{i}")
        except SyntaxError as e:
            bad.append((i, str(e)))
    rec("every code cell compiles (ast.parse, magics stripped)", "pass" if not bad else "fail", str(bad)[:300])
    if bad:
        return finish(strict)
    for i, c in code_cells:
        compile(trees[i], f"cell{i}", "exec")

    # ------------------------------------------------------------------ use-before-define
    known = set(dir(builtins)) | {"__file__", "__name__"}
    defined_so_far, problems = set(), []
    for i, c in code_cells:
        loads = names_in(trees[i], ast.Load)
        here = defined_names(trees[i])
        for n in sorted(loads - known - defined_so_far - here):
            problems.append((i, n))
        defined_so_far |= here
    rec("no name used before it is defined (cell-order dependency analysis)", "pass" if not problems else "fail", str(problems)[:300])

    # ------------------------------------------------------------------ imports vs requirements
    req_names = set()
    for line in REQ_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            req_names.add(re.split(r"[<>=!~\[ ]", line)[0].lower().replace("-", "_"))
    alias = {"pyyaml": "yaml", "huggingface_hub": "huggingface_hub"}
    importable = {alias.get(r, r) for r in req_names} | set(sys.stdlib_module_names) | {"torch", "kaggle_secrets", "pipeline"}
    imports = set()
    for i, c in code_cells:
        for n in ast.walk(trees[i]):
            if isinstance(n, ast.Import):
                imports |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
                imports.add(n.module.split(".")[0])
    missing = sorted(imports - importable)
    rec("imports resolvable (stdlib / requirements / Kaggle-preinstalled torch)", "pass" if not missing else "fail", f"imports={sorted(imports)} unresolved={missing}")
    pkg_names = {n.lower().replace("-", "_") for n in req_names}
    need = {"transformers", "peft", "bitsandbytes", "accelerate"} - pkg_names
    rec("requirements list the training stack (transformers>=4.51, peft, bitsandbytes, accelerate)", "pass" if not need and any(
        l.startswith("transformers>=4.5") for l in REQ_PATH.read_text(encoding="utf-8").splitlines()) else "fail", f"missing={sorted(need)}")

    # ------------------------------------------------------------------ package layout / paths
    try:
        files, label = build_package.collect_files(allow_placeholder=True)
        pkg = {a for a, _ in files}
        rec("package file list computed", "pass", f"{len(pkg)} files, dataset from {label}")
    except SystemExit as e:
        pkg = set()
        rec("package file list computed", "fail", str(e))
    bad_paths = []
    for i, c in code_cells:
        for s in string_consts(trees[i]):
            if s.startswith("/kaggle") and not s.startswith(("/kaggle/input", "/kaggle/working", "/kaggle/temp")):
                bad_paths.append((i, s))
            if re.match(r"^(configs|kaggle|dataset|tool_protocol)/[\w./-]+$", s) and pkg and s != "kaggle/dist":     # kaggle/dist = local-only build output, not packaged
                if s not in pkg and not any(p.startswith(s.rstrip("/") + "/") for p in pkg):
                    bad_paths.append((i, s))
    rec("paths consistent with the package layout and /kaggle conventions", "pass" if not bad_paths else "fail", str(bad_paths)[:300])

    # ------------------------------------------------------------------ entry points and CLI flags
    mods = {}
    for i, c in code_cells:
        for n in ast.walk(trees[i]):
            if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name) and n.targets[0].id in ("TRAIN_MODULE", "EVAL_MODULE") \
                    and isinstance(n.value, ast.Constant):
                mods[n.targets[0].id] = n.value.value
    flag_report = {}
    for var, label in (("TRAIN_MODULE", "train"), ("EVAL_MODULE", "eval")):
        mod = mods.get(var)
        if not mod:
            rec(f"{var} defined", "fail")
            continue
        path = WS / (mod.replace(".", "/") + ".py")
        if not path.exists():
            rec(f"{label} entry point {mod} exists", "pending", "module file not present yet")
            continue
        src = path.read_text(encoding="utf-8")
        has_main = "__main__" in src or "def main" in src
        flags = set(re.findall(r"add_argument\(\s*[\"'](--[\w-]+)", src)) | set(re.findall(r"add_argument\([^)]*?[\"'](--[\w-]+)[\"']", src))
        rec(f"{label} entry point {mod} exists and is runnable as a module", "pass" if has_main else "fail", f"flags found: {sorted(flags)}")
        used = set()
        for i, c in code_cells:
            if var in names_in(trees[i], ast.Load):
                used |= {s for s in string_consts(trees[i]) if s.startswith("--")}
        flag_report[label] = (flags, used)
        rec(f"{label} CLI flags used by the notebook exist in the source", "pass" if used <= flags else "fail", f"used={sorted(used)} missing={sorted(used - flags)}")
    # --set keys must exist in the config
    cfg_text = (WS / "configs" / "qlora_sft.yaml").read_text(encoding="utf-8")
    set_ok = "production_locked:" in cfg_text and re.search(r"^output:\s*\n(?:\s+.*\n)*?\s+output_dir:", cfg_text, re.M) is not None
    rec("--set overrides refer to real config keys (production_locked, output.output_dir)", "pass" if set_ok else "fail")

    # ------------------------------------------------------------------ production gating
    tags = {i: set(c.metadata.get("tags", [])) for i, c in code_cells}
    prod = [i for i in tags if "production" in tags[i]]
    gate = [i for i in tags if "gate" in tags[i]]
    rec("production cells tagged (train, resume, eval, compare)", "pass" if len(prod) >= 4 else "fail", f"production cells: {prod}")
    gate_ok, gate_detail = bool(gate), ""
    if gate:
        g = trees[gate[0]]
        gtext = nb.cells[gate[0]].source
        consts = {t.targets[0].id: t.value.value for t in ast.walk(g) if isinstance(t, ast.Assign) and isinstance(t.targets[0], ast.Name) and isinstance(t.value, ast.Constant)}
        gate_ok = consts.get("PHASE5_AUTHORIZED") is False and consts.get("CONFIRM_PRODUCTION") is False
        gate_detail = f"PHASE5_AUTHORIZED={consts.get('PHASE5_AUTHORIZED')}, CONFIRM_PRODUCTION={consts.get('CONFIRM_PRODUCTION')}"
        m_env = re.search(r"QWEN_PHASE5_UNLOCK=(\S+)", cfg_text)
        same = consts.get("UNLOCK_ENV") == "QWEN_PHASE5_UNLOCK" and m_env is not None and consts.get("UNLOCK_VALUE") == m_env.group(1)
        rec("gate unlock string equals the one in configs/qlora_sft.yaml", "pass" if same else "fail", f"notebook={consts.get('UNLOCK_VALUE')} config={m_env.group(1) if m_env else None}")
        need_checks = all(k in gtext for k in ("PHASE5_AUTHORIZED", "CONFIRM_PRODUCTION", "UNLOCK_ENV", "ON_KAGGLE", "HAS_CUDA"))
        gate_ok = gate_ok and need_checks and "def production_allowed" in gtext
    rec("gate cell present; switches default to False; checks Kaggle + CUDA + env var", "pass" if gate_ok else "fail", gate_detail)
    sets_env = []
    for i, c in code_cells:
        for n in ast.walk(trees[i]):
            if isinstance(n, ast.Assign):
                for t in n.targets:
                    if isinstance(t, ast.Subscript) and isinstance(t.value, ast.Attribute) and call_name(ast.Call(func=t.value, args=[], keywords=[])) == "os.environ":
                        key = t.slice
                        if (isinstance(key, ast.Name) and key.id == "UNLOCK_ENV") or (isinstance(key, ast.Constant) and key.value == "QWEN_PHASE5_UNLOCK"):
                            sets_env.append(i)
    rec("no cell sets the unlock environment variable", "pass" if not sets_env else "fail", str(sets_env))

    gated_fail = []
    for i in prod:
        t = trees[i]
        first_stmt = t.body[0] if t.body else None
        ok_first = isinstance(first_stmt, ast.Assign) or (isinstance(first_stmt, ast.Assign))
        # find the 'ok, reasons = production_allowed()' statement anywhere at top level before any run call
        top_guard = any(isinstance(s, ast.Assign) and isinstance(s.value, ast.Call) and call_name(s.value) == "production_allowed" for s in t.body)
        pm = parent_map(t)
        risky = [n for n in ast.walk(t) if isinstance(n, ast.Call) and call_name(n) in ("run_stream", "subprocess.run", "subprocess.Popen", "os.system", "load_hub_auth")]
        for call in risky:
            node, safe = call, False
            while node in pm:
                par = pm[node]
                if isinstance(par, ast.If):
                    in_body = any(node is b or node in list(ast.walk(b)) for b in par.body)
                    test_is_not_ok = isinstance(par.test, ast.UnaryOp) and isinstance(par.test.op, ast.Not) and isinstance(par.test.operand, ast.Name) and par.test.operand.id == "ok"
                    test_is_ok = isinstance(par.test, ast.Name) and par.test.id == "ok"
                    if (test_is_ok and in_body) or (test_is_not_ok and not in_body):
                        safe = True
                        break
                node = par
            if not safe:
                gated_fail.append((i, call_name(call)))
        if not top_guard:
            gated_fail.append((i, "no production_allowed() guard"))
    rec("every action in production cells sits behind the production_allowed() branch", "pass" if not gated_fail else "fail", str(gated_fail))

    leaks = []
    for i, c in code_cells:
        if i in prod or i in gate:
            continue
        src = strip_magics(c.source)
        for bad_tok in ("from_pretrained", "snapshot_download", "hf_hub_download", "load_in_4bit", "BitsAndBytesConfig", "--confirm-production", "production_locked=false", "AutoModel"):
            if bad_tok in src:
                leaks.append((i, bad_tok))
        # non-production use of the training module may only use safe flags
        for n in ast.walk(trees[i]):
            if isinstance(n, ast.Call) and call_name(n) == "run_stream":
                s = string_consts(n)
                if any(isinstance(a, ast.List) and "TRAIN_MODULE" in names_in(a, ast.Load) for a in n.args) and not any(x in ("--validate-config", "--dry-run", "--smoke-test") for x in s):
                    leaks.append((i, "train module launched without a safe-mode flag"))
    rec("no weight download / training launch outside gated cells (safe modes only)", "pass" if not leaks else "fail", str(leaks))
    install_cells = [i for i, c in code_cells if "pip" in c.source and "install" in c.source]
    inst_guarded = all(("ON_KAGGLE" in nb.cells[i].source) for i in install_cells)
    rec("pip install only on Kaggle (never into a local/NEXUS environment)", "pass" if inst_guarded and install_cells else "fail", f"install cells: {install_cells}")

    # ------------------------------------------------------------------ secrets
    full = "\n".join(c.source for c in nb.cells)
    hits = [lab for lab, rx in build_package.SECRET_PATTERNS if rx.search(full)]
    rec("no secrets / tokens in the notebook", "pass" if not hits else "fail", str(hits))
    leak_print = []
    for i, c in code_cells:
        for n in ast.walk(trees[i]):
            if isinstance(n, ast.Call) and call_name(n) in ("print", "repr", "str", "logging.info", "json.dumps"):
                for a in n.args:
                    for nm in names_in(a, ast.Load):
                        if re.search(r"(?i)token|secret|password|api_?key", nm):
                            leak_print.append((i, nm))
                    for sub in ast.walk(a):
                        if isinstance(sub, ast.Attribute) and call_name(ast.Call(func=sub, args=[], keywords=[])) == "os.environ" and not isinstance(pm_get(trees[i], sub), ast.Subscript):
                            leak_print.append((i, "os.environ dumped"))
    rec("no cell prints secrets or dumps the environment", "pass" if not leak_print else "fail", str(leak_print))
    rec("child-process output is redacted before display", "pass" if "def redact" in full and "redact(" in full else "fail")

    # ------------------------------------------------------------------ audit-driven checks (dataset manifest case, MODE switch, loss cell, bf16, pip check, bounds, docs)
    cells_by_id = {c.id: c.source for c in nb.cells}
    # (2) the packaged dataset directory must carry MANIFEST.json with EXACTLY this name, and its hashes must match the packaged files
    ds_arcs = sorted(a for a in pkg if a.startswith("dataset/v2_final/"))
    lower_dupes = [a for a in ds_arcs if a.lower() == "dataset/v2_final/manifest.json" and a != "dataset/v2_final/MANIFEST.json"]
    has_exact = "dataset/v2_final/MANIFEST.json" in pkg
    rec("packaged dataset dir contains MANIFEST.json (exact, case-sensitive name; no lower-case variant)", "pass" if has_exact and not lower_dupes else "fail",
        f"entries={ds_arcs} lower-case variants={lower_dupes}")
    if has_exact:
        by_arc = dict(files)
        mf = json.loads(by_arc["dataset/v2_final/MANIFEST.json"].read_text(encoding="utf-8"))
        import hashlib
        mism = [n for n, info in mf["files"].items() if hashlib.sha256(by_arc[f"dataset/v2_final/{n}"].read_bytes()).hexdigest() != info["sha256"]]
        rec("MANIFEST.json hashes match the packaged dataset files", "pass" if not mism else "fail", f"mismatches={mism}")
    zip_path = Path(ZIP_OVERRIDE) if ZIP_OVERRIDE else HERE / "dist" / build_package.ZIP_NAME
    if zip_path.exists():
        import hashlib
        import zipfile
        with zipfile.ZipFile(zip_path) as z:
            names = z.namelist()
            exact = "dataset/v2_final/MANIFEST.json" in names
            variants = [n for n in names if n.lower() == "dataset/v2_final/manifest.json" and n != "dataset/v2_final/MANIFEST.json"]
            mism = []
            if exact:
                mf = json.loads(z.read("dataset/v2_final/MANIFEST.json"))
                mism = [n for n, info in mf["files"].items() if hashlib.sha256(z.read(f"dataset/v2_final/{n}")).hexdigest() != info["sha256"]]
        rec("built zip: dataset/v2_final/MANIFEST.json exact-case and hashes match after extraction", "pass" if exact and not variants and not mism else "fail",
            f"exact={exact} variants={variants} mismatches={mism} (rebuild the package if this is stale)")
    else:
        rec("built zip: dataset/v2_final/MANIFEST.json exact-case and hashes match after extraction", "pending", "zip not built yet")
    c04 = cells_by_id.get("c04-package", "")
    rec("notebook verifies dataset MANIFEST.json (exact name + hashes) after extraction", "pass" if '"MANIFEST.json" in ds_names' in c04 and 'info["sha256"]' in c04 else "fail")
    # (1) precision choice from compute capability, not from is_bf16_supported()
    c02 = cells_by_id.get("c02-env", "")
    ok_cap = "get_device_capability" in c02 and ">= 8" in c02 and "BF16_OK = bool(torch.cuda.is_bf16_supported())" not in c02 and "T4" in c02
    rec("environment cell picks bf16 only for compute capability >= 8 (T4/P100 -> fp16 recommendation)", "pass" if ok_cap else "fail")
    # (7) MODE switch
    gate_src = cells_by_id.get("c09-gate", "")
    c10, c11 = cells_by_id.get("c10-train", ""), cells_by_id.get("c11-resume", "")
    mode_ok = bool(re.search(r'^MODE = "train"', gate_src, re.M)) and 'MODE != "train"' in c10 and 'MODE != "resume"' in c11 and "MODE" in gate_src
    rec('MODE switch: default "train"; train cell needs MODE="train", resume cell needs MODE="resume" (other prints which MODE it needs)', "pass" if mode_ok else "fail")
    rec("train cell refuses to start over existing checkpoints", "pass" if 'rglob("latest.json")' in c10 and "REFUSED" in c10 else "fail")
    # (17) gated loss cell + real subcommand flags
    loss_cells = [i for i in prod if "loss" in string_consts(trees[i])]
    rec("a gated production cell runs `pipeline.eval.cli loss` (base vs fine-tuned)", "pass" if loss_cells and all("--allow-model-load" in nb.cells[i].source for i in loss_cells) else "fail", f"cells={loss_cells}")
    eval_mod = mods.get("EVAL_MODULE")
    if eval_mod and (WS / (eval_mod.replace(".", "/") + ".py")).exists():
        import subprocess
        helps, sub_bad = {}, []
        for i, c in code_cells:
            for n in ast.walk(trees[i]):
                if isinstance(n, ast.List):
                    elts = n.elts
                    names = [e.id if isinstance(e, ast.Name) else (e.value if isinstance(e, ast.Constant) else None) for e in elts]
                    if "EVAL_MODULE" in names:
                        k = names.index("EVAL_MODULE")
                        sub = names[k + 1] if k + 1 < len(names) else None
                        flags = {e.value for e in elts if isinstance(e, ast.Constant) and isinstance(e.value, str) and e.value.startswith("--")}
                        if sub not in helps:
                            helps[sub] = subprocess.run([sys.executable, "-m", eval_mod, sub, "--help"], capture_output=True, text=True, cwd=WS).stdout
                        for f in flags:
                            if f not in helps[sub]:
                                sub_bad.append((i, sub, f))
        rec("eval subcommands and flags used by the notebook exist (checked with the real --help)", "pass" if helps and not sub_bad else "fail", f"subcommands={sorted(k for k in helps if k)} bad={sub_bad}")
    # (12) pip check, version printout, requirement bounds
    c05 = cells_by_id.get("c05-install", "")
    rec("pip check and a library-version printout are in the install cell", "pass" if '"pip", "check"' in c05 and "md.version" in c05 and "UNVERIFIED" in c05 else "fail")
    req_lines = [l.strip() for l in REQ_PATH.read_text(encoding="utf-8").splitlines() if l.strip() and not l.strip().startswith("#")]
    bounds_bad = [l for l in req_lines if "<" in l and not re.search(r",<\d+$", l)]
    unbounded = [l for l in req_lines if "<" not in l]
    rec("requirement upper bounds are next-major guards only; transformers>=4.51 kept", "pass" if not bounds_bad and any(l.startswith("transformers>=4.51") for l in req_lines) else "fail",
        f"non-major bounds={bounds_bad}; unbounded={unbounded}")
    rec("requirements file states that Kaggle versions are UNVERIFIED", "pass" if "UNVERIFIED" in REQ_PATH.read_text(encoding="utf-8") else "fail")
    # (11, 15) documentation statements
    readme = (HERE / "README.md").read_text(encoding="utf-8")
    memplan = (WS / "docs" / "MEMORY_PLAN.md").read_text(encoding="utf-8") if (WS / "docs" / "MEMORY_PLAN.md").exists() else ""
    docs_ok = ("UNVERIFIED" in readme and "workspace-only" in readme and "T4 over P100" in readme and "UNVERIFIED" in memplan and "Prefer a T4 over a P100" in memplan
               and "prints the measured tokens/s" not in memplan and "prints the measured tokens/s" not in readme and "resume-from" not in memplan + readme)
    rec("README / MEMORY_PLAN: versions UNVERIFIED, T4 over P100, smoke-test workspace-only, no unsupported tokens/s claim", "pass" if docs_ok else "fail")

    # ------------------------------------------------------------------ cell order sanity
    order = [i for i in gate]
    rec("gate cell precedes every production cell", "pass" if gate and all(g < p for g in gate for p in prod) else "fail")
    rec("Kaggle execution status", "pending", "PENDING PHASE 5 - the notebook has not been run on Kaggle; only static validation is claimed here.")
    return finish(strict)


def pm_get(tree, node):
    return parent_map(tree).get(node)


def finish(strict):
    failed = [c for c in checks if c["status"] == "fail"]
    pending = [c for c in checks if c["status"] == "pending"]
    summary = {"passed": sum(c["status"] == "pass" for c in checks), "failed": len(failed), "pending": len(pending), "strict": strict,
               "note": "Static validation only. Kaggle execution is PENDING PHASE 5."}
    REPORT.write_text(json.dumps({"summary": summary, "checks": checks}, indent=1), encoding="utf-8")
    for c in checks:
        print(f"[{c['status'].upper():7s}] {c['name']}" + (f"  -- {c['detail']}" if c["status"] != "pass" and c["detail"] else ""))
    print(json.dumps(summary))
    return 1 if failed or (strict and pending) else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true", help="treat PENDING checks (e.g. entry-point modules not written yet) as failures")
    ap.add_argument("--notebook", default=str(NB_PATH))
    ap.add_argument("--zip", default=None, help="validate this package zip instead of kaggle/dist")
    a = ap.parse_args()
    ZIP_OVERRIDE = a.zip
    sys.exit(main(a.strict, Path(a.notebook)))
