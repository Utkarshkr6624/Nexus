"""Phase 6 (v2 prompt) Stage 2: read-only checkpoint integrity + manifest.

Opens every checkpoint file read-only, rehashes it, and compares against
CHECKPOINT_SHA256SUMS.txt and each checkpoint_meta.json. Writes only into OUT.
"""
import csv, hashlib, json, os, sys
from pathlib import Path

ROOT = Path(r"E:\Nexo\Jarvis")
OUT = ROOT / "jarvis_phase6_final" / "phase6_results"
STEPS = [800, 1100, 1200, 1300, 1400, 1500, 1596]
EXPECT = {"r": 16, "lora_alpha": 32, "lora_dropout": 0.05, "peft_type": "LORA",
          "task_type": "CAUSAL_LM", "base_model_name_or_path": "Qwen/Qwen3-8B"}
MODULES = {"q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"}


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 22), b""):
            h.update(b)
    return h.hexdigest()


def main():
    OUT.mkdir(exist_ok=True)
    sums = {}
    for line in (ROOT / "CHECKPOINT_SHA256SUMS.txt").read_text(encoding="utf-8").splitlines():
        if line.strip():
            h, rel = line.split(None, 1)
            sums[rel.strip().replace("\\", "/")] = h
    best = json.loads((ROOT / "best.json").read_text(encoding="utf-8"))
    report, rows = {"root": str(ROOT), "checkpoints": {}}, []
    for s in STEPS:
        d = ROOT / f"checkpoint-{s}"
        r = {"path": str(d), "checks": {}, "files": {}}
        meta = json.loads((d / "checkpoint_meta.json").read_text(encoding="utf-8"))
        cfg = json.loads((d / "adapter" / "adapter_config.json").read_text(encoding="utf-8"))
        cfg_ok = all(cfg.get(k) == v for k, v in EXPECT.items()) and set(cfg.get("target_modules") or []) == MODULES
        r["checks"]["adapter_config_matches_expected"] = cfg_ok
        r["checks"]["meta_step_matches_dir"] = meta.get("step") == s == meta["metrics"].get("metrics_step")
        r["checks"]["meta_model_id"] = meta["identity"]["model_id"] == "Qwen/Qwen3-8B"
        r["checks"]["meta_dataset_version"] = meta["identity"]["dataset_version"] == "qwen_jarvis_dataset_v2_final"
        all_match = True
        for rel, rec in meta["files"].items():
            p = d / rel
            size, h = p.stat().st_size, sha256(p)
            key = f"checkpoint-{s}/{rel}"
            ok = size == rec["bytes"] and h == rec["sha256"] and sums.get(key, h) == h
            all_match &= ok
            r["files"][rel] = {"bytes": size, "sha256": h, "meta_match": h == rec["sha256"] and size == rec["bytes"],
                               "sums_file_match": sums.get(key) == h if key in sums else None}
            rows.append({"checkpoint": s, "file_path": str(p), "file_size": size, "sha256": h,
                         "adapter_config_status": "PASS" if cfg_ok else "FAIL",
                         "metadata_status": "PASS" if r["checks"]["meta_step_matches_dir"] else "FAIL",
                         "integrity_status": "PASS" if ok else "FAIL",
                         "load_test_status": "NOT_TESTED", "evaluation_status": "NOT_TESTED"})
        r["checks"]["all_files_hash_match"] = all_match
        r["metrics"] = meta["metrics"]
        r["is_best_meta"] = meta.get("is_best")
        r["saved_utc"] = meta.get("saved_utc")
        r["status"] = "PASS" if all(r["checks"].values()) else "FAIL"
        report["checkpoints"][s] = r
        print(s, r["status"], {k: v for k, v in r["checks"].items() if not v})
    report["best_json"] = best
    report["best_json_consistent"] = best["metrics"] == report["checkpoints"][best["step"]]["metrics"]
    report["lowest_val_loss_step"] = min(STEPS, key=lambda s: report["checkpoints"][s]["metrics"]["val_loss"])
    report["missing_checkpoints"] = {
        "900": "absent", "1000": "absent",
        "explanation": "keep_last=2 pruning during training; not present locally or in either Kaggle checkpoint dataset. "
                       "Not required to evaluate the 7 available checkpoints."}
    report["status"] = "PASS" if all(c["status"] == "PASS" for c in report["checkpoints"].values()) else "FAIL"
    (OUT / "phase6_checkpoint_integrity.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    with open(OUT / "phase6_checkpoint_manifest.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)
    print("overall", report["status"], "best.json consistent", report["best_json_consistent"],
          "lowest val_loss", report["lowest_val_loss_step"])
    assert len(rows) == 28, len(rows)  # 7 checkpoints x 4 files


if __name__ == "__main__":
    main()
