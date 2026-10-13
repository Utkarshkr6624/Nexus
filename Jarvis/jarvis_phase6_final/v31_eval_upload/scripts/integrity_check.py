"""Phase 6 checkpoint integrity (read-only). For each of the 7 adapters: config fields, safetensors opens, tensor keys/shapes vs the Qwen3-8B
architecture (model skeleton on the `meta` device + the ORIGINAL attach_lora, so no weights are needed), finiteness, SHA-256 vs checkpoint_meta.json,
and the training metadata. Writes manifests/checkpoint_integrity.json. Never writes to the checkpoint directories."""
import hashlib
import json
import math
import sys
from pathlib import Path

JARVIS = Path(r"E:\Nexo\Jarvis")
CODE = JARVIS / "_resume_test" / "code"               # extracted Kaggle package (training code that produced the checkpoints)
HF_META = Path(r"E:\Nexo\qwen_jarvis_phase3_4\_hf_meta")  # Qwen3-8B config + tokenizer only (read)
OUT = Path(__file__).resolve().parents[1] / "manifests" / "checkpoint_integrity.json"
STEPS = [800, 1100, 1200, 1300, 1400, 1500, 1596]
EXPECT = {"peft_type": "LORA", "r": 16, "lora_alpha": 32, "lora_dropout": 0.05, "task_type": "CAUSAL_LM", "base_model_name_or_path": "Qwen/Qwen3-8B", "bias": "none",
          "target_modules": {"q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"}}
sys.path.insert(0, str(CODE))


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 24), b""):
            h.update(b)
    return h.hexdigest()


def main():
    import torch
    from peft import get_peft_model_state_dict
    from safetensors import safe_open
    from transformers import AutoConfig, AutoModelForCausalLM
    from pipeline.train import config as C, train as T

    cfg = C.load_config(CODE / "configs" / "qlora_sft.yaml")
    with torch.device("meta"):
        skel = T.attach_lora(AutoModelForCausalLM.from_config(AutoConfig.from_pretrained(str(HF_META))), cfg)
    expected_shapes = {k: tuple(v.shape) for k, v in get_peft_model_state_dict(skel).items()}

    res = {"generated_by": "scripts/integrity_check.py", "architecture_reference": "Qwen3-8B config (_hf_meta/config.json) on meta device + original attach_lora",
           "expected_adapter_tensors": len(expected_shapes), "checkpoints": {}}
    for s in STEPS:
        d = JARVIS / f"checkpoint-{s}"
        meta = json.loads((d / "checkpoint_meta.json").read_text(encoding="utf-8"))
        r = {"path": str(d), "checks": {}}
        acfg = json.loads((d / "adapter" / "adapter_config.json").read_text(encoding="utf-8"))
        for k, v in EXPECT.items():
            got = set(acfg.get(k) or []) if k == "target_modules" else acfg.get(k)
            r["checks"][f"config.{k}"] = "PASS" if got == v else f"FAIL (got {got!r})"
        r["checks"]["config.inference_mode"] = f"INFO {acfg.get('inference_mode')}"
        st = d / "adapter" / "adapter_model.safetensors"
        with safe_open(str(st), framework="pt") as f:
            keys = list(f.keys())
            shapes = {k: tuple(f.get_slice(k).get_shape()) for k in keys}
            nonfinite = sum(int(not torch.isfinite(f.get_tensor(k).float()).all()) for k in keys)
            bnorm = math.sqrt(sum(float(f.get_tensor(k).float().pow(2).sum()) for k in keys if "lora_B" in k))
        r["checks"]["safetensors.opens"] = "PASS"
        r["checks"]["tensors.keys_match_architecture"] = "PASS" if set(shapes) == set(expected_shapes) else \
            f"FAIL (missing {sorted(set(expected_shapes) - set(shapes))[:2]}, unexpected {sorted(set(shapes) - set(expected_shapes))[:2]})"
        r["checks"]["tensors.shapes_match_architecture"] = "PASS" if shapes == expected_shapes else "FAIL"
        r["checks"]["tensors.all_finite"] = "PASS" if nonfinite == 0 else f"FAIL ({nonfinite} tensors)"
        r["checks"]["tensors.lora_B_norm_vs_meta"] = "PASS" if abs(bnorm - meta["metrics"]["lora_B_norm"]) <= 1e-5 * meta["metrics"]["lora_B_norm"] else \
            f"FAIL ({bnorm} vs {meta['metrics']['lora_B_norm']})"
        files = {}
        for rel, info in meta["files"].items():
            p = d / rel
            h, n = sha(p), p.stat().st_size
            files[rel] = {"bytes": n, "sha256": h, "meta_sha256": info["sha256"], "status": "PASS" if (h, n) == (info["sha256"], info["bytes"]) else "FAIL"}
        files["checkpoint_meta.json"] = {"bytes": (d / "checkpoint_meta.json").stat().st_size, "sha256": sha(d / "checkpoint_meta.json"),
                                         "status": "UNVERIFIED (self-describing; no external hash)"}
        r["files"] = files
        r["n_tensors"] = len(keys)
        r["lora_B_norm"] = bnorm
        r["metadata"] = {"step": meta["step"], "epoch": meta.get("epoch"), "train_loss": meta["metrics"].get("train_loss"), "val_loss": meta["metrics"].get("val_loss"),
                         "val_ppl": meta["metrics"].get("val_ppl"), "val_tokens": meta["metrics"].get("val_tokens"), "metrics_step": meta["metrics"].get("metrics_step"),
                         "is_best": meta.get("is_best"), "saved_utc": meta.get("saved_utc"), **meta["identity"], "libraries": meta.get("libraries")}
        bad = [k for k, v in {**r["checks"], **{f"sha256.{k}": v["status"] for k, v in files.items()}}.items() if str(v).startswith("FAIL")]
        r["overall"] = "PASS" if not bad else f"FAIL {bad}"
        res["checkpoints"][f"checkpoint-{s}"] = r
        print(f"checkpoint-{s}: {r['overall']} tensors={len(keys)} val_loss={r['metadata']['val_loss']} is_best={r['metadata']['is_best']} saved={r['metadata']['saved_utc']}")

    # two `is_best: true` checkpoints: explained by the two run segments (800-1200 saved 09:29-10:35Z, 1300-1596 saved 15:02-15:55Z), see FINALIZATION_REPORT.md §4.4
    res["is_best_true"] = [k for k, v in res["checkpoints"].items() if v["metadata"]["is_best"]]
    res["checkpoint_1200_duplicates"] = {
        "local": str(JARVIS / "checkpoint-1200"),
        "primary_dataset_meta_copy": str(JARVIS / "_finalization_downloads" / "primary" / "checkpoint-1200" / "checkpoint_meta.json"),
        "meta_byte_identical": sha(JARVIS / "_finalization_downloads" / "primary" / "checkpoint-1200" / "checkpoint_meta.json") == sha(JARVIS / "checkpoint-1200" / "checkpoint_meta.json"),
        "note": "the primary dataset's checkpoint-1200 meta lists the same adapter/trainer_state hashes; its adapter file itself was not downloaded. One copy is used for behaviour tests."}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    print("is_best_true:", res["is_best_true"], "| 1200 meta duplicate identical:", res["checkpoint_1200_duplicates"]["meta_byte_identical"])


if __name__ == "__main__":
    main()
