"""Rebuilds the whole dataset from scratch:  python scripts/run_all.py
Order matters only for gen_multiturn (reads the knowledge generators' source) and build/validate/manifest (which read generated files).
Set P2_SCRATCH to a temp directory for compiler/test scratch files (defaults to the system temp dir). Stdlib only; needs node, javac, g++, gcc for the verified-code generators."""
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
GENERATORS = [
    "gen_nexus", "gen_nexus2", "gen_nexus3", "gen_math", "gen_reasoning",
    "gen_prog_multi", "gen_prog_py", "gen_prog_debug", "gen_prog_sql_git", "gen_prog_concepts1", "gen_prog_concepts2", "gen_prog_extra",
    "gen_knowledge", "gen_knowledge2", "gen_tools", "gen_tools2", "gen_boundaries3", "gen_jarvis", "gen_jarvis2",
    "gen_conversation", "gen_conversation2", "gen_conversation3", "gen_instruction", "gen_safety", "gen_safety2", "gen_safety3", "gen_context", "gen_multiturn",
    # Phase 2.1 additions
    "gen21_conv_a", "gen21_conv_b", "gen21_conv_c", "gen21_conv_d", "gen21_conv_e", "gen21_conv_f", "gen21_conv_g", "gen21_conv_h",
    "gen21_prog_errors", "gen21_prog_sql", "gen21_prog_py", "gen21_prog_web", "gen21_prog_algo", "gen21_nexus", "gen21_multiturn", "gen21_math", "gen21_reasoning", "gen21_instruction",
]


def run(cmd, cwd):
    r = subprocess.run([sys.executable] + cmd, cwd=cwd, capture_output=True, text=True, env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    tail = (r.stdout.strip().splitlines() or [""])[-1]
    print(f"{' '.join(cmd):45s} rc={r.returncode}  {tail[:90]}")
    if r.returncode:
        print(r.stderr[-800:])
        sys.exit(r.returncode)


gen_dir = ROOT / "dataset" / "generated"
for f in gen_dir.glob("*.jsonl"):
    f.unlink()
for g in GENERATORS:
    run([f"{g}.py"], HERE)
run(["build.py"], HERE)
run([str(ROOT / "validation" / "validate_final.py")], ROOT)
run(["make_manifest.py"], HERE)
