# JARVIS Phase 6 evaluation, run group 3 (V3 remediation checkpoint) (private GPU kernel). Locates the launcher in the attached private dataset
# utkarsh6624/jarvis-phase6-eval and runs it; all integrity gates live in the launcher and runner. Never trains.
import runpy
import sys
from pathlib import Path

found = [p for p in Path("/kaggle/input").rglob("phase6_launch.py") if p.parent.name == "kaggle"]
if len(found) != 1:
    raise SystemExit(f"ABORT: expected exactly one kaggle/phase6_launch.py under /kaggle/input, found {found}")
sys.argv = [str(found[0]), "3"]
runpy.run_path(str(found[0]), run_name="__main__")
