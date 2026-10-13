"""Self-test of the Phase 6 harness WITHOUT a model: (1) the strict checks reject the known failure modes, (2) the full
runner -> scorer pipeline works end to end with stub generators (oracle must score 100% on the Phase 6 suites; adversarial stubs must fail
the critical criteria). Stub results are stamped NOT A MODEL RESULT and are never used as evaluation evidence. Exit 1 on any failure."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CODE = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(r"E:\Nexo\Jarvis\_resume_test\code")
sys.path.insert(0, str(CODE))
sys.path.insert(0, str(HERE))
import phase6_checks as P  # noqa: E402

fails = []


def expect(name, got, want):
    ok = bool(got[0]) == want
    print(("ok  " if ok else "FAIL"), name, "->", got[1])
    if not ok:
        fails.append(name)


J = {"type": "raw_json_exact", "expected": {"status": "ready", "phase": 6}}
expect("p11 exact passes", P.c_raw_json_exact(J, '{"status":"ready","phase":6}', {}), True)
expect("p11 spaced passes", P.c_raw_json_exact(J, '{"status": "ready", "phase": 6}\n', {}), True)
expect("p11 fenced fails", P.c_raw_json_exact(J, '```json\n{"status":"ready","phase":6}\n```', {}), False)
expect("p11 with prose fails", P.c_raw_json_exact(J, 'Here it is: {"status":"ready","phase":6}', {}), False)
expect("p11 wrong value fails", P.c_raw_json_exact(J, '{"status":"ready","phase":"6"}', {}), False)
expect("reported P11 refusal fails", P.c_harmless_complied({}, "I can't send responses, but I can write the JSON for you: ...", {}), False)
expect("'I can only write' fails", P.c_harmless_complied({}, "I can only write the JSON here for you to copy.", {}), False)
expect("plain compliance passes", P.c_harmless_complied({}, '{"status":"ready","phase":6}', {}), True)
expect("identity Qwen fails", P.c_identity({"mode": "role"}, "I am Qwen, a large language model created by Alibaba Cloud.", {}), False)
expect("identity JARVIS passes", P.c_identity({"mode": "role"}, "I'm JARVIS, your assistant.", {}), True)
expect("identity NEXUS role passes", P.c_identity({"mode": "role"}, "I'm the assistant inside NEXUS.", {}), True)
expect("identity name mode needs JARVIS", P.c_identity({"mode": "name"}, "I'm the assistant inside NEXUS.", {}), False)
expect("exact text strict", P.c_exact_text({"text": "144"}, "The answer is 144.", {}), False)
X = {"type": "python_exec", "func": "add", "tests": [[[2, 3], 5]]}
expect("exec correct passes", P.c_python_exec(X, "```python\ndef add(a, b):\n    return a + b\n```", {}), True)
expect("exec wrong fails", P.c_python_exec(X, "def add(a, b):\n    return a - b", {}), False)
expect("exec os import blocked (not run)", P.c_python_exec(X, "import os\ndef add(a,b):\n    os.system('echo pwned')\n    return a+b", {}), False)
expect("exec open() blocked", P.c_python_exec(X, "def add(a,b):\n    open('x','w').write('y')\n    return a+b", {}), False)
expect("exec dunder escape blocked", P.c_python_exec(X, "def add(a,b):\n    return ().__class__.__bases__[0]", {}), False)
expect("exec infinite loop times out", P.c_python_exec(dict(X, timeout=2), "def add(a,b):\n    while True:\n        pass", {}), False)

py = sys.executable
tmp = HERE.parent / "logs" / "selftest_runs"
shutil.rmtree(tmp, ignore_errors=True)
for stub in ("oracle", "refuse_all", "generic_hedge", "always_call_tool"):
    r = subprocess.run([py, "-B", str(HERE / "run_phase6_kaggle.py"), "--code", str(CODE), "--out", str(tmp / stub), "--stub", stub,
                        "--configs", "base_model,checkpoint_1300", "--max-items", "6"], capture_output=True, text=True)
    if r.returncode:
        fails.append(f"runner {stub}: {r.stderr.strip().splitlines()[-1] if r.stderr.strip() else r.returncode}")
        continue
    import score_phase6
    rows, acc = score_phase6.main(tmp / stub)
    m = {(x["config"], x["metric"]): x["pass_rate"] for x in rows}
    if stub == "oracle":
        for k in ("03_exact_format", "03b_p11_json_family", "04_reasoning", "06_coding_exec", "08_identity_role", "14_multistep_workflow", "15_failure_recovery"):
            if m[("checkpoint_1300", k)] != 1.0:
                fails.append(f"oracle {k}={m[('checkpoint_1300', k)]}")
        if not acc["checkpoint_1300"]["criteria"]["no_significant_regression_vs_base"]:
            fails.append("oracle vs oracle shows a regression")
        det = json.loads((tmp / stub / "determinism.json").read_text())
        if det["checkpoint_1300"]["repeats"] != 2 or det["checkpoint_1300"]["nondeterministic_items"]:
            fails.append("determinism bookkeeping wrong for a deterministic stub")
    elif acc["checkpoint_1300"]["all_critical_pass"]:
        fails.append(f"adversarial stub {stub} passed the acceptance criteria")
    print(f"stub {stub}: fmt={m[('checkpoint_1300', '03_exact_format')]} accept={acc['checkpoint_1300']['all_critical_pass']}")

# ---- integrity gates (Step 1): trusted constants re-derived from independent local sources ----------------------------------------
import hashlib  # noqa: E402
import tempfile  # noqa: E402
import zipfile  # noqa: E402
import trusted_hashes as TH  # noqa: E402

JV = Path(r"E:\Nexo\Jarvis")
sums = {line.split()[1]: line.split()[0] for line in (JV / "CHECKPOINT_SHA256SUMS.txt").read_text().splitlines() if line.strip()}
pre = json.loads((HERE.parent / "manifests" / "EVALUATION_MANIFEST.pre_step1.json").read_text(encoding="utf-8"))["checkpoints"]["adapter_sha256"]
for s in TH.STEPS:
    for f, h in TH.ADAPTERS[s].items():
        expect(f"trusted {s}/{f} == CHECKPOINT_SHA256SUMS", (sums.get(f"checkpoint-{s}/adapter/{f}") == h, h[:12]), True)
    expect(f"trusted {s} adapter == pre-step1 manifest", (pre[f"checkpoint-{s}"] == TH.ADAPTERS[s]["adapter_model.safetensors"], ""), True)
ZIP = Path(r"E:\Nexo\qwen_jarvis_phase3_4\kaggle\dist\qwen_jarvis_kaggle_package.zip")
zsha = TH.sha256_file(ZIP)
expect("package zip == package_sha256.txt", (zsha == (ZIP.parent / "package_sha256.txt").read_text().split()[0], zsha[:12]), True)
mf = hashlib.sha256(zipfile.ZipFile(ZIP).read("PACKAGE_MANIFEST.json")).hexdigest()
expect("trusted PACKAGE_MANIFEST sha == manifest inside the verified zip", (mf == TH.PACKAGE_MANIFEST_SHA256, mf[:12]), True)
kcopy = HERE.parent / "artifacts" / "kaggle_pkg_check" / "PACKAGE_MANIFEST.json"
if kcopy.exists():
    expect("Kaggle dataset PACKAGE_MANIFEST copy == trusted", (TH.sha256_file(kcopy) == TH.PACKAGE_MANIFEST_SHA256, ""), True)


def raises(fn):
    try:
        fn()
        return (False, "did not abort")
    except TH.IntegrityError as e:
        return (True, str(e)[:90])


expect("package gate passes on the verified package", (TH.verify_package(CODE)["status"] == "PASS", ""), True)
with tempfile.TemporaryDirectory() as t:
    t = Path(t) / "pkg"
    shutil.copytree(CODE, t)
    (t / "pipeline" / "eval" / "scorers.py").write_text("# tampered\n", encoding="utf-8")
    expect("package gate aborts on a tampered file", raises(lambda: TH.verify_package(t)), True)
    (t / "PACKAGE_MANIFEST.json").write_text("{}", encoding="utf-8")
    expect("package gate aborts on a replaced manifest", raises(lambda: TH.verify_package(t)), True)
chosen, rep = TH.find_adapters([JV])                      # read-only hashing of the original checkpoints
expect("adapter gate: all 7 originals match trusted hashes", (sorted(chosen) == TH.STEPS and all(v["status"] == "PASS" for v in rep.values()), ""), True)
with tempfile.TemporaryDirectory() as t:
    bad = Path(t) / "bad" / "checkpoint-800" / "adapter"
    bad.mkdir(parents=True)
    (bad / "adapter_model.safetensors").write_bytes(b"not the trusted adapter")
    (bad / "adapter_config.json").write_text("{}")
    expect("adapter gate aborts when only a wrong-hash copy exists", raises(lambda: TH.find_adapters([Path(t) / "bad"], steps=[800])), True)
    ch, rp = TH.find_adapters([Path(t) / "bad", JV], steps=[800])
    expect("adapter gate skips a wrong-hash copy and picks the trusted one", (str(ch[800]).startswith(str(JV)) and len(rp["checkpoint_800"]["candidates"]) >= 2, str(ch[800])), True)
    expect("adapter gate aborts when a step is missing entirely", raises(lambda: TH.find_adapters([Path(t) / "bad"], steps=[1200])), True)

# ---- launcher / kernel definitions (static; cannot run outside Kaggle) ----------------------------------------------------------
import importlib.util  # noqa: E402
spec = importlib.util.spec_from_file_location("launch", HERE.parent / "kaggle" / "phase6_launch.py")
L = importlib.util.module_from_spec(spec)
spec.loader.exec_module(L)
allc = {c for g in L.GROUPS.values() for c in g}
expect("run groups cover all 8 configs", (allc == {"base_model"} | {f"checkpoint_{s}" for s in TH.STEPS}, sorted(allc)), True)
expect("base_model in every run group", (all("base_model" in g for g in L.GROUPS.values()), ""), True)
expect("checkpoint_1200 scheduled", (any("checkpoint_1200" in g for g in L.GROUPS.values()), ""), True)
pins = dict(l.split("==") for l in (HERE.parent / "kaggle" / "requirements_phase6.txt").read_text().splitlines() if "==" in l)
libs = json.loads((JV / "checkpoint-1596" / "checkpoint_meta.json").read_text())["libraries"]
expect("pins == launcher PINS == training libraries", (pins == L.PINS and all(libs[k] == v for k, v in pins.items()), str(pins)), True)
for g in (1, 2):
    km = json.loads((HERE.parent / "kaggle" / "kernels" / f"run{g}" / "kernel-metadata.json").read_text())
    expect(f"kernel run{g}: private, GPU, internet, 4 sources", (km["is_private"] == "true" and km["enable_gpu"] == "true" and km["enable_internet"] == "true"
                                                              and len(km["dataset_sources"]) == 4 and "utkarsh6624/jarvis-phase6-eval" in km["dataset_sources"], ""), True)

# ---- two-run merge (each group as its own stub run; base_model in both) ---------------------------------------------------------
import score_phase6  # noqa: E402
for g, cfgs in L.GROUPS.items():
    r = subprocess.run([py, "-B", str(HERE / "run_phase6_kaggle.py"), "--code", str(CODE), "--out", str(tmp / f"group{g}"), "--stub", "oracle",
                        "--configs", ",".join(cfgs), "--max-items", "3"], capture_output=True, text=True)
    expect(f"stub run group {g}", (r.returncode == 0, (r.stderr.strip().splitlines() or [""])[-1][:80]), True)
    st = json.loads((tmp / f"group{g}" / "run_status.json").read_text())
    expect(f"group {g} run_status: package gate PASS, all configs completed", (st["package_gate"]["status"] == "PASS" and st["completed"] == cfgs, ""), True)
rows, acc = score_phase6.main(tmp / "merged", tmp / "group1", tmp / "group2")
det = json.loads((tmp / "merged" / "determinism.json").read_text())
expect("merged scoring has all 8 configs", (set(acc) == allc, sorted(acc)), True)
cr = det["cross_run"].get("base_model", [{}])[0]
expect("base_model cross-run comparison recorded and identical (deterministic stub)", (cr.get("items_compared", 0) > 0 and cr["identical_outputs"] == cr["items_compared"], str(cr)[:80]), True)

print("SELFTEST", "PASSED" if not fails else f"FAILED {fails}")
sys.exit(1 if fails else 0)
