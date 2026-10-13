"""Checkpoint system (B8): atomic saves, latest/best pointers, retention, integrity checks and RNG/optimizer/scheduler/scaler state.

Layout of a run directory:
  <run>/checkpoint-<step>/adapter/            PEFT LoRA adapter (adapter_model.safetensors + adapter_config.json)
  <run>/checkpoint-<step>/trainer_state.pt    optimizer, scheduler, grad scaler, RNG, loop state   (loaded with weights_only=True)
  <run>/checkpoint-<step>/checkpoint_meta.json step, epoch, metrics, config hash, dataset version/hash, model id, libs, file hashes
  <run>/latest.json , <run>/best.json         tiny pointer files (written atomically)
A checkpoint directory only ever appears under its final name after every file in it is complete (write to .tmp-*, then rename).
"""
from __future__ import annotations

import json
import os
import random
import re
import shutil
import time
from pathlib import Path

from .repro import lib_versions, sha256_bytes, write_json_atomic

META = "checkpoint_meta.json"
STATE = "trainer_state.pt"
ADAPTER = "adapter"
SCHEMA = 1
_NAME = re.compile(r"^checkpoint-(\d+)$")


class CheckpointError(RuntimeError):
    """Invalid, incomplete or incompatible checkpoint (message always says what to do)."""


def _file_hash(p: Path) -> str:
    return sha256_bytes(p.read_bytes())


def rng_state() -> dict:
    import numpy as np
    import torch

    py = random.getstate()
    npst = np.random.get_state()
    st = {"python": [py[0], list(py[1]), py[2]], "numpy": [npst[0], npst[1].tolist(), int(npst[2]), int(npst[3]), float(npst[4])], "torch_cpu": torch.get_rng_state()}
    if torch.cuda.is_available():
        st["torch_cuda"] = torch.cuda.get_rng_state_all()
    return st


def restore_rng(st: dict) -> None:
    import numpy as np
    import torch

    py = st["python"]
    random.setstate((py[0], tuple(py[1]), py[2]))
    n = st["numpy"]
    np.random.set_state((n[0], np.array(n[1], dtype=np.uint32), n[2], n[3], n[4]))
    torch.set_rng_state(st["torch_cpu"])
    if "torch_cuda" in st and torch.cuda.is_available():
        torch.cuda.set_rng_state_all(st["torch_cuda"])


def _saved_key(p: Path):
    """Chronological order of saving (not step number): meta.saved_ns, falling back to the directory mtime."""
    try:
        return (0, json.loads((p / META).read_text(encoding="utf-8"))["saved_ns"])
    except Exception:
        return (0, p.stat().st_mtime_ns)


def list_by_save_time(run_dir: Path) -> list[Path]:
    return sorted((p for _, p in list_checkpoints(run_dir)), key=_saved_key)


def ensure_fresh_run_dir(run_dir: Path) -> None:
    """A run started WITHOUT --resume must not share a directory with existing checkpoints (restored sessions, earlier runs)."""
    cks = list_checkpoints(run_dir)
    if cks:
        raise CheckpointError(f"run directory {run_dir} already contains {len(cks)} checkpoint(s) (latest: {cks[-1][1].name}). "
                              "Pass --resume to continue that run, or choose a different output.run_name; refusing to mix runs.")


def list_checkpoints(run_dir: Path) -> list[tuple[int, Path]]:
    out = []
    if run_dir.exists():
        for p in run_dir.iterdir():
            m = _NAME.match(p.name)
            if m and p.is_dir():
                out.append((int(m.group(1)), p))
    return sorted(out)


def save_checkpoint(run_dir: Path, *, step: int, model, optimizer, scheduler, scaler, loop_state: dict, identity: dict, metrics: dict, keep_last: int,
                    is_best: bool = False) -> Path:
    """identity = {model_id, dataset_version, dataset_hash, config_hash, ...} (checked again on resume)."""
    import torch

    run_dir.mkdir(parents=True, exist_ok=True)
    final = run_dir / f"checkpoint-{step}"
    tmp = run_dir / f".tmp-checkpoint-{step}"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir()
    try:
        model.save_pretrained(str(tmp / ADAPTER))
        state = {"optimizer": optimizer.state_dict(), "scheduler": scheduler.state_dict() if scheduler is not None else None,
                 "scaler": scaler.state_dict() if scaler is not None else None, "rng": rng_state(), "loop": loop_state}
        torch.save(state, tmp / STATE)
        files = {}
        for p in sorted(tmp.rglob("*")):
            if p.is_file():
                files[p.relative_to(tmp).as_posix()] = {"sha256": _file_hash(p), "bytes": p.stat().st_size}
        meta = {"schema": SCHEMA, "step": step, "epoch": loop_state.get("epoch"), "metrics": metrics, "identity": identity, "libraries": lib_versions(),
                "saved_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "saved_ns": time.time_ns(), "files": files, "is_best": is_best}
        (tmp / META).write_text(json.dumps(meta, indent=1, sort_keys=True, default=str), encoding="utf-8")
        if final.exists():
            shutil.rmtree(final)
        os.replace(tmp, final)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    write_json_atomic(run_dir / "latest.json", {"checkpoint": final.name, "step": step})
    if is_best:
        write_json_atomic(run_dir / "best.json", {"checkpoint": final.name, "step": step, "metrics": metrics})
    prune(run_dir, keep_last)
    return final


def prune(run_dir: Path, keep_last: int) -> list[str]:
    """Keep the `keep_last` most recently SAVED checkpoints plus the one best.json points to. Ordering is by save time, never by step number,
    so a checkpoint written in this session is not deleted in favour of an older, higher-numbered one restored from a previous session."""
    cks = [(0, p) for p in list_by_save_time(run_dir)]
    keep = {p.name for _, p in cks[-keep_last:]}
    best = run_dir / "best.json"
    if best.exists():
        try:
            keep.add(json.loads(best.read_text(encoding="utf-8"))["checkpoint"])
        except Exception:
            pass
    removed = []
    for _, p in cks:
        if p.name not in keep:
            shutil.rmtree(p, ignore_errors=True)
            removed.append(p.name)
    return removed


def resolve_resume(run_dir: Path, arg: str) -> Path:
    """arg: 'latest' | 'best' | a path to a checkpoint directory."""
    if arg in ("latest", "best"):
        ptr = run_dir / f"{arg}.json"
        if ptr.exists():
            try:
                p = run_dir / json.loads(ptr.read_text(encoding="utf-8"))["checkpoint"]
                if p.is_dir():
                    return p
            except Exception:
                pass
        if arg == "latest":
            cks = list_by_save_time(run_dir)
            if cks:
                return cks[-1]
        raise CheckpointError(f"no '{arg}' checkpoint found in {run_dir}; nothing to resume. Start without --resume.")
    p = Path(arg)
    if not p.is_dir():
        raise CheckpointError(f"checkpoint path does not exist or is not a directory: {p}")
    return p


def verify_checkpoint(path: Path) -> dict:
    """Integrity check: meta readable, every listed file present with matching size and sha256. Returns meta."""
    mp = path / META
    if not mp.exists():
        raise CheckpointError(f"{path} is not a complete checkpoint ({META} missing). It was probably interrupted; resume from an earlier one.")
    try:
        meta = json.loads(mp.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise CheckpointError(f"{mp} is corrupt JSON ({e.msg}); do not resume from it.") from e
    for key in ("schema", "step", "identity", "files"):
        if key not in meta:
            raise CheckpointError(f"{mp} is missing required field '{key}'.")
    if meta["schema"] != SCHEMA:
        raise CheckpointError(f"unsupported checkpoint schema {meta['schema']} (this code reads schema {SCHEMA}).")
    for rel, info in meta["files"].items():
        f = path / rel
        if not f.exists():
            raise CheckpointError(f"checkpoint {path.name} is missing file '{rel}'.")
        if f.stat().st_size != info["bytes"] or _file_hash(f) != info["sha256"]:
            raise CheckpointError(f"checkpoint {path.name}: file '{rel}' does not match its recorded hash (truncated or modified).")
    if not (path / ADAPTER / "adapter_config.json").exists():
        raise CheckpointError(f"checkpoint {path.name} has no adapter_config.json.")
    return meta


def check_compatibility(meta: dict, expected: dict, force: bool = False) -> list[str]:
    """Raises CheckpointError for identity mismatches unless force; returns the list of mismatches."""
    got = meta["identity"]
    bad = [f"{k}: checkpoint has {got.get(k)!r}, current run has {v!r}" for k, v in expected.items() if got.get(k) != v]
    if bad and not force:
        raise CheckpointError("checkpoint is not compatible with this run:\n  - " + "\n  - ".join(bad)
                              + "\nFix the config/dataset, or pass --force-resume if the difference is intentional.")
    return bad


def load_checkpoint(path: Path, expected: dict | None = None, force: bool = False) -> dict:
    """Verifies and loads trainer state (weights_only=True: no pickled code is executed). Adapter weights are loaded separately."""
    import torch

    meta = verify_checkpoint(path)
    mismatches = check_compatibility(meta, expected or {}, force)
    try:
        state = torch.load(path / STATE, map_location="cpu", weights_only=True)
    except Exception as e:
        raise CheckpointError(f"could not read {STATE} in {path.name}: {type(e).__name__}: {e}") from e
    return {"meta": meta, "state": state, "mismatches": mismatches, "adapter_dir": path / ADAPTER}


def load_adapter_into(model, adapter_dir: Path) -> None:
    from peft import load_peft_weights, set_peft_model_state_dict

    weights = load_peft_weights(str(adapter_dir), device="cpu")
    res = set_peft_model_state_dict(model, weights)
    unexpected = getattr(res, "unexpected_keys", None)
    if unexpected:
        raise CheckpointError(f"adapter in {adapter_dir} has unexpected keys for this model: {unexpected[:3]}")
