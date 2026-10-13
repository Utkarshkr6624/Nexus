"""Reproducibility snapshot (B11). Never records environment variables (no secrets), only versions, hardware and hashes."""
from __future__ import annotations

import hashlib
import json
import platform
import random
import subprocess
import sys
import time
from pathlib import Path

LIBS = ("torch", "transformers", "peft", "trl", "accelerate", "datasets", "bitsandbytes", "tokenizers", "numpy", "yaml")


def lib_versions() -> dict:
    import importlib

    out = {}
    for name in LIBS:
        try:
            m = importlib.import_module(name)
            out[name] = getattr(m, "__version__", "unknown")
        except Exception as e:   # bitsandbytes may fail to import on CPU-only machines
            out[name] = f"not importable ({type(e).__name__})"
    return out


def bf16_native() -> bool:
    """True only for GPUs with native bf16 (compute capability >= 8, Ampere+). `torch.cuda.is_bf16_supported()` also returns True on a
    T4 (cc 7.5) through *emulation*, which is slow and invalidates the fp16 plan, so it must not be used for the precision decision."""
    try:
        import torch

        return bool(torch.cuda.is_available()) and torch.cuda.get_device_capability()[0] >= 8
    except Exception:
        return False


def hardware() -> dict:
    info = {"platform": platform.platform(), "python": sys.version.split()[0], "cpu_count": None, "cuda": False, "gpus": []}
    try:
        import os

        info["cpu_count"] = os.cpu_count()
    except Exception:
        pass
    try:
        import torch

        info["cuda"] = bool(torch.cuda.is_available())
        if info["cuda"]:
            for i in range(torch.cuda.device_count()):
                p = torch.cuda.get_device_properties(i)
                info["gpus"].append({"name": p.name, "total_memory_mb": p.total_memory // (1024 * 1024), "capability": f"{p.major}.{p.minor}"})
            info["bf16_supported_incl_emulation"] = bool(torch.cuda.is_bf16_supported())
            info["bf16_native"] = bf16_native()
    except Exception as e:
        info["gpu_probe_error"] = type(e).__name__
    return info


def git_commit(cwd: Path | None = None) -> str | None:
    try:
        r = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10, cwd=str(cwd) if cwd else None)
        return r.stdout.strip() or None if r.returncode == 0 else None
    except Exception:
        return None


def set_seed(seed: int) -> None:
    random.seed(seed)
    try:
        import numpy as np

        np.random.seed(seed % (2**32))
    except Exception:
        pass
    try:
        import torch

        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
    except Exception:
        pass


def snapshot(cfg, dataset_info: dict, resolved_precision: str, extra: dict | None = None) -> dict:
    snap = {
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "banner": cfg.banner,
        "mode": cfg.mode,
        "model_id": cfg.model.model_id,
        "tokenizer": cfg.model.tokenizer_path,
        "dataset": dataset_info,
        "seed": cfg.train.seed,
        "precision": resolved_precision,
        "quantization": cfg.quant.__dict__ if cfg.quant.enabled else None,
        "libraries": lib_versions(),
        "hardware": hardware(),
        "git_commit": git_commit(Path(cfg.config_dir) if cfg.config_dir else None),
        "config": cfg.to_dict(),
        "config_hash": cfg.snapshot_hash(),
    }
    if extra:
        snap.update(extra)
    return snap


def write_json_atomic(path: Path, obj) -> None:
    import os

    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, sort_keys=True, default=str), encoding="utf-8")
    os.replace(tmp, path)


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def tokenizer_fingerprint(tok) -> dict:
    """Content hashes of what actually matters for training: chat template, vocabulary (token -> id) and special tokens."""
    vocab = tok.get_vocab()
    return {
        "chat_template_sha256": hashlib.sha256((tok.chat_template or "").encode("utf-8")).hexdigest(),
        "vocab_sha256": hashlib.sha256(json.dumps(sorted(vocab.items(), key=lambda kv: kv[1]), ensure_ascii=False).encode("utf-8")).hexdigest(),
        "special_tokens_sha256": hashlib.sha256(json.dumps({"eos": tok.eos_token, "pad": tok.pad_token, "bos": tok.bos_token, "unk": tok.unk_token},
                                                           sort_keys=True).encode("utf-8")).hexdigest(),
        "vocab_size": len(vocab), "eos_token": tok.eos_token, "pad_token": tok.pad_token,
    }
