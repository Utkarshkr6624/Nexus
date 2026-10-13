"""Phase 7 cleanup: deletes only verified duplicates, verified-reproducible outputs, caches and empty directories.

Every deletion is checked first (sha256 equal to an authoritative copy, or a confirmed cache/empty directory) and
recorded in CLEANUP_MANIFEST.json together with what was preserved and why. Anything not listed here is untouched.
usage: python cleanup_phase7.py [--dry-run]
"""
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

NEXO = Path("E:/Nexo")
J = NEXO / "Jarvis"
HERE = Path(__file__).resolve().parent
DRY = "--dry-run" in sys.argv


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for c in iter(lambda: fh.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


removed, problems = [], []


def remove_duplicate(dup: Path, authority: Path, reason: str) -> None:
    if not dup.is_file() or not authority.is_file():
        problems.append(f"missing: {dup} or {authority}")
        return
    a, b = sha(dup), sha(authority)
    if a != b:
        problems.append(f"NOT identical, kept: {dup}")
        return
    removed.append({"path": str(dup), "bytes": dup.stat().st_size, "sha256": a, "category": "D exact duplicate",
                    "reason": reason, "authoritative_copy": str(authority)})
    if not DRY:
        dup.unlink()


def remove_reproducible(p: Path, authority: Path, reason: str) -> None:
    remove_duplicate(p, authority, reason)
    if removed and removed[-1]["path"] == str(p):
        removed[-1]["category"] = "C recoverable generated output"


def remove_cache_dir(d: Path) -> None:
    files = [f for f in d.rglob("*") if f.is_file()]
    if any(f.suffix not in (".pyc", ".pyo") for f in files):
        problems.append(f"cache dir has non-bytecode files, kept: {d}")
        return
    removed.append({"path": str(d), "bytes": sum(f.stat().st_size for f in files), "files": len(files),
                    "category": "E disposable cache", "reason": "Python bytecode cache; regenerated on import",
                    "authoritative_copy": None})
    if not DRY:
        shutil.rmtree(d)


def remove_empty_dirs(base: Path) -> None:
    for d in sorted((x for x in base.rglob("*") if x.is_dir()), key=lambda x: -len(x.parts)):
        if not any(d.iterdir()):
            removed.append({"path": str(d), "bytes": 0, "category": "E empty directory",
                            "reason": "empty download target directory", "authoritative_copy": None})
            if not DRY:
                d.rmdir()


# D: the resume test's private copy of checkpoint-1596; the original is protected and sha-verified.
copy = J / "_resume_test" / "run" / "checkpoint-1596"
for rel in ("trainer_state.pt", "checkpoint_meta.json", "adapter/adapter_model.safetensors", "adapter/adapter_config.json",
            "adapter/README.md"):
    remove_duplicate(copy / rel, J / "checkpoint-1596" / rel,
                     "byte-identical copy made for the Phase 5 resume load test; its pre/post hashes stay recorded in "
                     "_resume_test/pre_test_sha256.txt and post_test_sha256.txt; re-copy checkpoint-1596 to rerun load_test.py")
# C: this session's local re-run of scorer v2.2; identical to the stored v2 re-score, reproducible with rescore_v2.py.
rerun = HERE / "evidence" / "scorer_v2_local_rerun" / "raw"
stored = J / "jarvis_phase6_final" / "results" / "kaggle_run_v31" / "merged_v2" / "raw"
for p in sorted(rerun.glob("*.json")) if rerun.exists() else []:
    remove_reproducible(p, stored / p.name, "local re-run output identical to the stored scorer-v2.2 re-score; "
                        "reproducible with jarvis_phase6_final/scripts/rescore_v2.py")
# E: bytecode caches in the dataset project (never inside .venv).
for d in sorted((NEXO / "qwen_jarvis_phase3_4").rglob("__pycache__")):
    if ".venv" not in d.parts:
        remove_cache_dir(d)
# E: empty download directories.
for base in (J / "_phase_c_downloads", J / "_finalization_downloads", copy, rerun):
    if base.exists():
        remove_empty_dirs(base)
        if base in (copy, rerun) and not any(base.iterdir()):
            removed.append({"path": str(base), "bytes": 0, "category": "E empty directory",
                            "reason": "emptied by the removals above", "authoritative_copy": None})
            if not DRY:
                base.rmdir()

PRESERVED = [
    {"path": "Jarvis/phase6_run4 (vs phase6_run4_new)", "category": "B", "reason": "Run 4 evidence is protected; the raw files are identical, but both directories are referenced (SOURCE_SHA256 files, reports)"},
    {"path": "qwen_jarvis_phase3_4/kaggle/v3_upload/*.zip, v31_upload/*.zip (= dist_v3, dist_v31)", "category": "F", "reason": "identical, but each upload folder is the input to `kaggle datasets version`"},
    {"path": "Jarvis/_resume_test/code", "category": "A", "reason": "pinned evaluation package used by scripts/rescore_v2.py"},
    {"path": "v3_train_output/.../checkpoint-600, jarvis_v31_remediation_train/.../checkpoint-100", "category": "B", "reason": "unique training artifacts (not duplicates); size alone is no reason"},
    {"path": "Jarvis/jarvis_phase6_final/{v3_eval,v3_eval_upload,v31_eval,v31_eval_upload,upload_staging}", "category": "B", "reason": "staged copies are upload provenance, with their own STAGING_SHA256SUMS"},
    {"path": "Jarvis/jarvis_phase6_final/logs/selftest_runs/group1|group2", "category": "B", "reason": "self-test evidence; identical base_model.json is expected"},
    {"path": "qwen_jarvis_phase3_4/v3_build/*.pre_v3", "category": "F", "reason": "pre-V3 backups of the builder and trainer; uncertain value, kept"},
    {"path": "qwen_jarvis_phase3_4/v3_build/reports_v31/V31_SHA256SUMS.txt", "category": "B", "reason": "stale (pre-install) but historical; superseded by MANIFEST.json, documented in WORKING_STATE.md"},
    {"path": "Jarvis/_v31_drafts_staging, jarvis_v31_extra_drafts.zip", "category": "B", "reason": "draft provenance and the validated staging input"},
    {"path": "all datasets (V2, V3, V3.1, V3.1.1 candidate), 7 original checkpoints, v3_700, v31_139, configs, scripts, reports, run 1-4 evidence, scorers", "category": "A/B", "reason": "protected"},
]

check = subprocess.run(["sha256sum", "-c", "CHECKPOINT_SHA256SUMS.txt"], cwd=J, capture_output=True, text=True)
ok_lines = sum(1 for line in check.stdout.splitlines() if line.endswith(": OK"))
v31 = J / "jarvis_v31_remediation_train/runs/qwen3_8b_jarvis_v3_1_remediation/checkpoint-139/adapter/adapter_model.safetensors"
v37 = J / "v3_train_output/runs/qwen3_8b_jarvis_v3_remediation/checkpoint-700/adapter/adapter_model.safetensors"
manifest = {
    "created_utc": datetime.now(UTC).isoformat(timespec="seconds"),
    "dry_run": DRY,
    "removed": removed,
    "removed_bytes": sum(r["bytes"] for r in removed),
    "problems": problems,
    "preserved": PRESERVED,
    "verification_after_cleanup": {
        "original_checkpoints_sha256sum_c": f"{ok_lines}/35 OK" + ("" if check.returncode == 0 else " (FAILURES)"),
        "checkpoint_v31_139_adapter_sha256": sha(v31),
        "checkpoint_v3_700_adapter_sha256": sha(v37),
        "resume_test_evidence_kept": sorted(p.name for p in (J / "_resume_test").iterdir() if p.is_file()),
    },
}
if not DRY:
    (HERE / "CLEANUP_MANIFEST.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
print(json.dumps({k: v for k, v in manifest.items() if k not in ("removed", "preserved")}, indent=1))
print(f"{len(removed)} entries, {manifest['removed_bytes'] / 1e6:.1f} MB")
sys.exit(1 if problems or check.returncode else 0)
